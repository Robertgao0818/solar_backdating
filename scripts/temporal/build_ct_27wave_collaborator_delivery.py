#!/usr/bin/env python3
"""Collaborator delivery for the Cape Town 27-wave visit sequences.

Top-52 rows are omitted. The wide 101-date panel is not the delivery. Two
analysis files replace it:

* a long visit file, one row per evidence image;
* a roof-level robustness file with monotonicity and k=1,2,3 confirmation.

The frozen 2026-09-02 citywide package is not modified.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict
from pathlib import Path

from scripts.temporal.build_ct_install_dated_deliverable import sha256
from scripts.temporal.build_ct_presence_sequences import EXPECTED_WAVE
from scripts.temporal.ct_visit_robustness import side_fields

CITY_ROOT = (
    Path.home() / "zasolar_data/geid_temporal/cape_town_citywide_backdating_v1_20260812"
)
DEFAULT_INSTALL_CSV = (
    CITY_ROOT / "deliverable_2026-09-02/ct_citywide_install_dated_20260902.csv"
)
DEFAULT_VISITS_CSV = (
    CITY_ROOT / "sequences_20260923/ct_citywide_presence_observations_20260923.csv"
)
DEFAULT_OUTPUT_DIR = CITY_ROOT / "deliverable_27wave_20260923"
WAVE = "citywide_27wave"
INSTALL_NAME = "ct_27wave_install_dated_20260902.csv"
VISITS_NAME = "ct_27wave_visits_20260923.csv"
ROBUST_NAME = "ct_27wave_robustness_20260923.csv"
VISIT_FIELDS = (
    "source_anchor_id",
    "obs_index",
    "capture_date",
    "present_raw",
    "present_repaired",
    "unusable",
    "dip_repaired",
)
KEY_FIELDS = (
    "source_anchor_id",
    "source_grid",
    "scan_status",
    "published_latest_absent",
    "published_earliest_present",
    "n_evidence",
    "n_unusable",
)


def _present(code: str) -> str:
    if code == "u":
        return ""
    if code in {"0", "1"}:
        return code
    raise ValueError(f"unexpected code {code!r}")


def _day(value: str) -> str:
    value = (value or "").strip()
    return value[:10] if value else ""


def _load_visits(path: Path) -> dict[str, list[tuple[int, str, str, str]]]:
    grouped: dict[str, list[tuple[int, str, str, str]]] = defaultdict(list)
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if row["cohort"] != WAVE or row["frame_role"] != "evidence":
                continue
            grouped[row["source_anchor_id"]].append(
                (
                    int(row["obs_index"]),
                    row["capture_date"],
                    row["code_raw"],
                    row["code_repaired"],
                )
            )
    for anchor_id, visits in grouped.items():
        visits.sort(key=lambda item: item[0])
        indexes = [item[0] for item in visits]
        if indexes != list(range(len(visits))):
            raise ValueError(f"{anchor_id}: evidence obs_index is not 0..n-1")
        dates = [item[1] for item in visits]
        if dates != sorted(dates):
            raise ValueError(f"{anchor_id}: evidence dates are not chronological")
    return grouped


def _robust_fields(anchor: dict[str, str], visits: list[tuple[int, str, str, str]]) -> dict[str, str | int]:
    raw_pairs = [(day, raw) for _index, day, raw, _repaired in visits]
    repaired_pairs = [(day, repaired) for _index, day, _raw, repaired in visits]
    fields: dict[str, str | int] = {
        "source_anchor_id": anchor["source_anchor_id"],
        "source_grid": anchor["source_grid"],
        "scan_status": anchor["scan_status"],
        "published_latest_absent": _day(anchor["latest_absent_date"]),
        "published_earliest_present": _day(anchor["earliest_present_date"]),
        "n_evidence": len(visits),
        "n_unusable": sum(raw == "u" for _index, _day, raw, _repaired in visits),
    }
    fields.update(side_fields("raw", raw_pairs))
    fields.update(side_fields("repaired", repaired_pairs))
    return fields


def _check_against_published(fields: dict[str, str | int], anchor: dict[str, str]) -> str | None:
    anchor_id = anchor["source_anchor_id"]
    if int(fields["n_evidence"]) != int(anchor["n_observations"] or 0):
        return f"{anchor_id}: n_evidence {fields['n_evidence']} != n_observations"
    if int(fields["n_unusable"]) != int(anchor["n_unusable"] or 0):
        return f"{anchor_id}: n_unusable mismatch"
    if int(fields["repaired_n_absent"]) != int(anchor["n_absent"] or 0):
        return f"{anchor_id}: repaired absent mismatch"
    if int(fields["repaired_n_present"]) != int(anchor["n_present"] or 0):
        return f"{anchor_id}: repaired present mismatch"
    status = anchor["scan_status"]
    notes = anchor.get("scan_notes", "")
    earliest = _day(anchor["earliest_present_date"])
    latest = _day(anchor["latest_absent_date"])
    if (
        status in {"done_appears", "done_already_present_before_geid_history"}
        and int(fields["repaired_n_present"]) > 0
        and earliest
        and "present_clamped" not in notes
        and fields["repaired_k1_confirm_date"] != earliest
    ):
        return f"{anchor_id}: k1 confirm {fields['repaired_k1_confirm_date']} != {earliest}"
    if (
        status == "done_appears"
        and int(fields["repaired_monotonic"]) == 1
        and int(fields["repaired_n_absent"]) > 0
        and fields["repaired_k1_last_absent"] != latest
    ):
        return f"{anchor_id}: monotonic last absent {fields['repaired_k1_last_absent']} != {latest}"
    return None


def _census_latest_ok(fields: dict[str, str | int], anchor: dict[str, str], visits: list[tuple[int, str, str, str]]) -> str | None:
    if anchor["scan_status"] != "done_installed_during_census":
        return None
    if int(fields["repaired_n_present"]) != 0 or int(fields["repaired_n_absent"]) == 0:
        return None
    absent_dates = [day for _index, day, _raw, repaired in visits if repaired == "0"]
    latest = _day(anchor["latest_absent_date"])
    if not absent_dates or max(absent_dates) != latest:
        return f"{anchor['source_anchor_id']}: census latest absent {latest} != {max(absent_dates, default='')}"
    return None


def _summarize(rows: list[dict[str, str | int]]) -> dict[str, int]:
    def count(predicate) -> int:
        return sum(1 for row in rows if predicate(row))

    both_k = lambda row, k: row[f"repaired_k{k}_reached"] == 1 and row[f"raw_k{k}_reached"] == 1
    return {
        "n_anchors": len(rows),
        "raw_monotonic": count(lambda row: row["raw_monotonic"] == 1),
        "repaired_monotonic": count(lambda row: row["repaired_monotonic"] == 1),
        "monotonic_only_after_repair": count(
            lambda row: row["raw_monotonic"] == 0 and row["repaired_monotonic"] == 1
        ),
        "raw_k2_reached": count(lambda row: row["raw_k2_reached"] == 1),
        "raw_k3_reached": count(lambda row: row["raw_k3_reached"] == 1),
        "repaired_k2_reached": count(lambda row: row["repaired_k2_reached"] == 1),
        "repaired_k3_reached": count(lambda row: row["repaired_k3_reached"] == 1),
        "repaired_k2_moves_confirm_date": count(
            lambda row: both_k(row, 1)
            and row["repaired_k2_reached"] == 1
            and row["repaired_k2_confirm_date"] != row["repaired_k1_confirm_date"]
        ),
        "repaired_k3_moves_confirm_date": count(
            lambda row: row["repaired_k1_reached"] == 1
            and row["repaired_k3_reached"] == 1
            and row["repaired_k3_confirm_date"] != row["repaired_k1_confirm_date"]
        ),
    }


def _readme(stats: dict[str, int], n_visits: int) -> str:
    n = stats["n_anchors"]
    return f"""# Cape Town 27-wave visit sequences for robustness checks

Delivery date: 2026-09-23
Cohort: the 27-wave remainder of the Cape Town census, **{n:,} installations**.
The 21,453 Top-52 installations are not in this package.

These are model readings of historical satellite images, not verified physical
installation dates. The census cutoff is 2025-01-31. Polygons and the Top-52
interval table remain in the 2026-09-02 citywide research preview.

## Why this is not a 101-column sheet

Each roof is photographed on its own subset of days. A column for every date
in the city is mostly blank, and a blank cell means "no image", not "no
panels". The files below keep only real images and precompute the two checks
collaborators asked for.

## Files

- `{INSTALL_NAME}` — the 2026-09-02 install-date table restricted to these
  {n:,} rows (intervals, counts, coordinates). Same values as the citywide
  file. Excel can open it.
- `{ROBUST_NAME}` — one row per installation. Monotonicity, reversal counts,
  and confirmation under 1, 2, and 3 successive presents. Excel can open it.
- `{VISITS_NAME}` — one row per evidence image ({n_visits:,} rows). Use Stata,
  R, or Python. Excel's row limit is about 1,048,576, so Excel will not open
  this file completely.
- `gates.json` — checks against the published interval counts.

## Visit file

| column | meaning |
|---|---|
| `source_anchor_id` | installation id; join key to the other two files |
| `obs_index` | 0, 1, 2, … in time order within the installation |
| `capture_date` | image date |
| `present_raw` | model reading: 0 absent, 1 present, blank if the image was unusable |
| `present_repaired` | same scale after isolated-dip repair (an interior 0 flanked by confident 1s is flipped to 1). The published `n_absent` / `n_present` match this column |
| `unusable` | 1 if that image was unusable |
| `dip_repaired` | 1 if repair changed this image's reading |

Rows exist only for images that were scored. A date with no row was not
imaged. Do not fill those dates in as 0.

Post-census reference images are not included. They were not used as
install-date evidence.

## Robustness file

`u` images are dropped from the usable sequence. They are not absences, and
they do not break a run of presents. **Consecutive means successive usable
images in time order, not successive calendar days.** Two presents a year
apart are consecutive observations. `run_span_days` is how many calendar days
separate the first and the k-th present of the confirming run. `absent_gap_days`
is the width of the bracket from the last usable absence before that run to
the confirmation date.

`raw_monotonic` / `repaired_monotonic` is 1 when no usable present is followed
later by a usable absence. An all-absent roof is monotonic and still has no
installation. Read the flag together with `k*_reached`.

For k = 1, 2, 3 and for both `raw_` and `repaired_`:

| suffix | meaning |
|---|---|
| `k_reached` | 1 if some run of k usable presents exists |
| `k_last_absent` | latest usable absence before that run; blank if the series starts with presents |
| `k_confirm_date` | date of the k-th present in the earliest such run |
| `k_run_span_days` | calendar days from the first to the k-th present in the run; 0 when k = 1 |
| `k_absent_gap_days` | calendar days from `k_last_absent` to `k_confirm_date`; blank if there is no preceding absence |

`usable_code` is the usable sequence with unusable frames removed, for example
`0 0 1 1`. The published interval uses the repaired sequence with k = 1: the
last absence and the first present. Requiring k = 2 or k = 3 moves the
confirmation date later whenever the first present is not already part of a
longer run.

On the repaired sequence, {stats["repaired_monotonic"]:,} of {n:,} roofs are
monotonic and {stats["monotonic_only_after_repair"]:,} become monotonic only
after dip repair. A run of 2 repaired presents exists for
{stats["repaired_k2_reached"]:,} roofs, and a run of 3 for
{stats["repaired_k3_reached"]:,}. The k = 2 confirm date is the second image
in that run, so it is a later day than the k = 1 date whenever the two images
are not the same day. The useful comparison is whether a second present exists
and how many calendar days separate it from the first (`k2_run_span_days`).

## Counts in this build

| check | roofs |
|---|---:|
| raw monotonic | {stats["raw_monotonic"]:,} |
| repaired monotonic | {stats["repaired_monotonic"]:,} |
| monotonic only after repair | {stats["monotonic_only_after_repair"]:,} |
| raw k = 2 reached | {stats["raw_k2_reached"]:,} |
| raw k = 3 reached | {stats["raw_k3_reached"]:,} |
| repaired k = 2 reached | {stats["repaired_k2_reached"]:,} |
| repaired k = 3 reached | {stats["repaired_k3_reached"]:,} |

Stata, after importing the visit file with blank present codes as missing:

```stata
bysort source_anchor_id (obs_index): gen reversal = present_raw[_n-1] == 1 & present_raw == 0
bysort source_anchor_id: egen any_reversal = max(reversal)
```

`any_reversal == 0` is the raw monotonicity flag, because missing (unusable)
rows do not count as 0 or 1.
"""


def _write_checksums(directory: Path) -> None:
    names = sorted(
        path.name for path in directory.iterdir() if path.is_file() and path.name != "SHA256SUMS.txt"
    )
    lines = [f"{sha256(directory / name)}  {name}" for name in names]
    (directory / "SHA256SUMS.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def build(args: argparse.Namespace) -> dict[str, int]:
    if args.output_dir.exists():
        raise SystemExit(f"refusing to overwrite {args.output_dir}")
    visits = _load_visits(args.visits_csv)
    args.output_dir.mkdir(parents=True)
    robust_rows: list[dict[str, str | int]] = []
    n_visits_written = 0
    failures: list[str] = []
    with args.install_csv.open(newline="", encoding="utf-8") as source, (
        args.output_dir / INSTALL_NAME
    ).open("w", newline="", encoding="utf-8") as install_out, (
        args.output_dir / VISITS_NAME
    ).open("w", newline="", encoding="utf-8") as visits_out:
        reader = csv.DictReader(source)
        fieldnames = list(reader.fieldnames or [])
        install_writer = csv.DictWriter(install_out, fieldnames=fieldnames, extrasaction="ignore")
        install_writer.writeheader()
        visit_writer = csv.DictWriter(visits_out, fieldnames=list(VISIT_FIELDS))
        visit_writer.writeheader()
        for anchor in reader:
            if anchor["cohort"] != WAVE:
                continue
            anchor_id = anchor["source_anchor_id"]
            anchor_visits = visits.get(anchor_id, [])
            install_writer.writerow(anchor)
            for index, day, raw, repaired in anchor_visits:
                visit_writer.writerow(
                    {
                        "source_anchor_id": anchor_id,
                        "obs_index": index,
                        "capture_date": day,
                        "present_raw": _present(raw),
                        "present_repaired": _present(repaired),
                        "unusable": int(raw == "u"),
                        "dip_repaired": int(raw != repaired),
                    }
                )
                n_visits_written += 1
            fields = _robust_fields(anchor, anchor_visits)
            problem = _check_against_published(fields, anchor) or _census_latest_ok(
                fields, anchor, anchor_visits
            )
            if problem:
                if len(failures) < 20:
                    failures.append(problem)
            robust_rows.append(fields)
    if len(robust_rows) != EXPECTED_WAVE:
        failures.append(f"anchor count {len(robust_rows)} != {EXPECTED_WAVE}")
    if failures:
        raise SystemExit("gates failed:\n" + "\n".join(failures))
    robust_header = list(robust_rows[0].keys()) if robust_rows else list(KEY_FIELDS)
    with (args.output_dir / ROBUST_NAME).open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=robust_header, quoting=csv.QUOTE_NONNUMERIC)
        writer.writeheader()
        writer.writerows(robust_rows)
    stats = _summarize(robust_rows)
    stats["n_visits"] = n_visits_written
    (args.output_dir / "gates.json").write_text(
        json.dumps(stats, indent=2) + "\n", encoding="utf-8"
    )
    (args.output_dir / "README.md").write_text(_readme(stats, n_visits_written), encoding="utf-8")
    _write_checksums(args.output_dir)
    print(
        f"OK anchors={stats['n_anchors']} visits={n_visits_written} "
        f"raw_monotonic={stats['raw_monotonic']} repaired_monotonic={stats['repaired_monotonic']} "
        f"repaired_k2={stats['repaired_k2_reached']}",
        file=sys.stderr,
    )
    return stats


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--install-csv", type=Path, default=DEFAULT_INSTALL_CSV)
    parser.add_argument("--visits-csv", type=Path, default=DEFAULT_VISITS_CSV)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    build(parse_args(argv))


if __name__ == "__main__":
    main()
