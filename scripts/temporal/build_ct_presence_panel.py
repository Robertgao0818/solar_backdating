#!/usr/bin/env python3
"""Pivot the Cape Town visit sequences into an anchor-by-date panel.

One row is one installation. One column is one evidence capture date. The
cell is ``0``, ``1``, or ``u``. A blank cell means that installation was not
imaged on that date. Reference frames are not columns.

``ct_citywide_presence_panel_repaired_20260923.csv`` uses the dip-repaired
code, the same alphabet the install-date counts were computed from.
``ct_citywide_presence_panel_raw_20260923.csv`` uses the model verdict. The
date columns are identical.
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path

from scripts.temporal.build_ct_install_dated_deliverable import sha256
from scripts.temporal.build_ct_presence_sequences import (
    EXPECTED_ROWS,
    EXPECTED_TOP52,
    EXPECTED_WAVE,
)

CITY_ROOT = (
    Path.home() / "zasolar_data/geid_temporal/cape_town_citywide_backdating_v1_20260812"
)
DEFAULT_SEQUENCE_DIR = CITY_ROOT / "sequences_20260923"
SEQUENCE_CSV = "ct_citywide_install_dated_20260902_with_sequences.csv"
PANEL_REPAIRED = "ct_citywide_presence_panel_repaired_20260923.csv"
PANEL_RAW = "ct_citywide_presence_panel_raw_20260923.csv"
DATE_COVERAGE = "ct_citywide_presence_panel_date_coverage_20260923.csv"
PANEL_GATES = "panel_gates.json"
ID_FIELDS = ("source_anchor_id", "sequence_coverage")
CODE_ALPHABET = frozenset({"0", "1", "u"})


def split_dates(value: str) -> list[str]:
    value = (value or "").strip()
    return value.split(";") if value else []


def split_codes(value: str) -> list[str]:
    value = (value or "").strip()
    return value.split(" ") if value else []


def aligned_pairs(dates_field: str, codes_field: str, anchor_id: str) -> list[tuple[str, str]]:
    dates = split_dates(dates_field)
    codes = split_codes(codes_field)
    if len(dates) != len(codes):
        raise ValueError(
            f"{anchor_id}: {len(dates)} dates vs {len(codes)} codes"
        )
    if len(dates) != len(set(dates)):
        raise ValueError(f"{anchor_id}: duplicate evidence date")
    bad = [code for code in codes if code not in CODE_ALPHABET]
    if bad:
        raise ValueError(f"{anchor_id}: codes outside 0/1/u: {bad[:5]}")
    return list(zip(dates, codes, strict=True))


def panel_header(dates: list[str]) -> list[str]:
    return [*ID_FIELDS, *dates]


def panel_values(
    anchor_id: str,
    coverage: str,
    dates: list[str],
    pairs: list[tuple[str, str]],
) -> list[str]:
    by_date = dict(pairs)
    return [anchor_id, coverage, *[by_date.get(day, "") for day in dates]]


def _load_sequences(path: Path) -> tuple[list[str], list[dict[str, str]], list[str]]:
    rows: list[dict[str, str]] = []
    discovered: set[str] = set()
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        fieldnames = list(reader.fieldnames or [])
        for row in reader:
            raw_pairs = aligned_pairs(row["obs_dates"], row["obs_code_raw"], row["source_anchor_id"])
            repaired_pairs = aligned_pairs(
                row["obs_dates"], row["obs_code_repaired"], row["source_anchor_id"]
            )
            if [day for day, _code in raw_pairs] != [day for day, _code in repaired_pairs]:
                raise ValueError(f"{row['source_anchor_id']}: raw and repaired dates differ")
            row["_raw"] = raw_pairs
            row["_repaired"] = repaired_pairs
            discovered.update(day for day, _code in repaired_pairs)
            rows.append(row)
    return fieldnames, rows, sorted(discovered)


def _coverage_rows(dates: list[str], rows: list[dict[str, str]]) -> list[dict[str, int | str | float]]:
    observed = {day: 0 for day in dates}
    raw_counts = {day: Counter() for day in dates}
    repaired_counts = {day: Counter() for day in dates}
    for row in rows:
        for day, code in row["_raw"]:
            raw_counts[day][code] += 1
        for day, code in row["_repaired"]:
            observed[day] += 1
            repaired_counts[day][code] += 1
    out = []
    for day in dates:
        n = observed[day]
        out.append(
            {
                "capture_date": day,
                "n_roofs_imaged": n,
                "share_of_27wave": n / EXPECTED_WAVE,
                "n_0_repaired": repaired_counts[day]["0"],
                "n_1_repaired": repaired_counts[day]["1"],
                "n_u": repaired_counts[day]["u"],
                "n_0_raw": raw_counts[day]["0"],
                "n_1_raw": raw_counts[day]["1"],
            }
        )
    return out


def _write_panel(path: Path, header: list[str], rows: list[dict[str, str]], key: str) -> None:
    dates = header[len(ID_FIELDS) :]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        for row in rows:
            writer.writerow(
                panel_values(row["source_anchor_id"], row["sequence_coverage"], dates, row[key])
            )


def _verify_panel(path: Path, rows: list[dict[str, str]], code_field: str) -> str | None:
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        written = list(reader)
    if len(written) != len(rows):
        return f"{path.name}: {len(written)} rows vs {len(rows)} source rows"
    for source, panel in zip(rows, written, strict=True):
        if panel["source_anchor_id"] != source["source_anchor_id"]:
            return f"{path.name}: row order drifted at {source['source_anchor_id']}"
        expected = aligned_pairs(source["obs_dates"], source[code_field], source["source_anchor_id"])
        got = [(day, panel[day]) for day in panel if day not in ID_FIELDS and panel[day]]
        if got != expected:
            return (
                f"{path.name}: {source['source_anchor_id']} panel {got[:6]} "
                f"!= source {expected[:6]}"
            )
    return None


def _refresh_checksums(directory: Path) -> None:
    names = sorted(
        path.name
        for path in directory.iterdir()
        if path.is_file() and path.name != "SHA256SUMS.txt"
    )
    lines = [f"{sha256(directory / name)}  {name}" for name in names]
    (directory / "SHA256SUMS.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def build(args: argparse.Namespace) -> dict[str, object]:
    source = args.sequence_dir / SEQUENCE_CSV
    if not source.is_file():
        raise SystemExit(f"missing sequence table {source}")
    _fieldnames, rows, dates = _load_sequences(source)
    if len(rows) != EXPECTED_ROWS:
        raise SystemExit(f"expected {EXPECTED_ROWS} rows, found {len(rows)}")
    coverage_counts = Counter(row["sequence_coverage"] for row in rows)
    if coverage_counts["complete"] != EXPECTED_WAVE or coverage_counts["missing_scan_state"] != EXPECTED_TOP52:
        raise SystemExit(f"coverage counts {dict(coverage_counts)}")
    filled = sum(len(row["_repaired"]) for row in rows)
    differ_cells = 0
    differ_anchors = 0
    for row in rows:
        mismatches = sum(
            raw != repaired
            for (_day, raw), (_day2, repaired) in zip(row["_raw"], row["_repaired"], strict=True)
        )
        differ_cells += mismatches
        differ_anchors += int(mismatches > 0)
        if row["sequence_coverage"] == "missing_scan_state" and row["_repaired"]:
            raise SystemExit(f"{row['source_anchor_id']}: missing row has a sequence")

    header = panel_header(dates)
    repaired_path = args.sequence_dir / PANEL_REPAIRED
    raw_path = args.sequence_dir / PANEL_RAW
    _write_panel(repaired_path, header, rows, "_repaired")
    _write_panel(raw_path, header, rows, "_raw")
    repaired_error = _verify_panel(repaired_path, rows, "obs_code_repaired")
    raw_error = _verify_panel(raw_path, rows, "obs_code_raw")
    if repaired_error or raw_error:
        raise SystemExit(repaired_error or raw_error)

    coverage = _coverage_rows(dates, rows)
    coverage_path = args.sequence_dir / DATE_COVERAGE
    with coverage_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(coverage[0]))
        writer.writeheader()
        for record in coverage:
            record["share_of_27wave"] = f"{record['share_of_27wave']:.6f}"
            writer.writerow(record)

    gates = {
        "n_rows": len(rows),
        "n_complete": EXPECTED_WAVE,
        "n_missing_scan_state": EXPECTED_TOP52,
        "n_date_columns": len(dates),
        "first_date": dates[0],
        "last_date": dates[-1],
        "n_filled_cells": filled,
        "n_blank_cells": EXPECTED_ROWS * len(dates) - filled,
        "n_cells_raw_ne_repaired": differ_cells,
        "n_anchors_raw_ne_repaired": differ_anchors,
        "panel_repaired": PANEL_REPAIRED,
        "panel_raw": PANEL_RAW,
        "blank_means": "not imaged on this evidence date",
        "repaired_cell_means": "0 absent, 1 present, u unusable, after isolated-dip repair",
    }
    (args.sequence_dir / PANEL_GATES).write_text(
        json.dumps(gates, indent=2) + "\n", encoding="utf-8"
    )
    _refresh_checksums(args.sequence_dir)
    return gates


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sequence-dir", type=Path, default=DEFAULT_SEQUENCE_DIR)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    gates = build(parse_args(argv))
    print(
        f"OK dates={gates['n_date_columns']} filled={gates['n_filled_cells']} "
        f"raw_ne_repaired_cells={gates['n_cells_raw_ne_repaired']}"
    )


if __name__ == "__main__":
    main()
