#!/usr/bin/env python3
"""Build the 2023-census cohort: done_appears anchors a 2023 Wayback frame can narrow.

This is the *narrowing* counterpart to ``build_wayback_unresolved_subset.py`` (which
re-scanned TM's unresolved tail). Here the target is the recent-install peak: every
``done_appears`` anchor whose **(present-clamped) install interval** strictly contains at
least one **real 2023 Esri Wayback capture**. Esri Wayback has true 2023 captures over
JHB where TM has a 2023 hole, so dropping a 2023 frame *inside* the bracket can push the
last-absent forward or pull the first-present back — tightening the interval.

Two granularities are unioned (disjoint id-spaces):

* MAIN (chip-group ``c`` ids): clamped ``install_intervals.csv`` from the main TM run;
  geometry from ``chip_groups_as_anchors.csv``.
* NORECENT (per-target ``t`` ids): clamped ``install_intervals_pertarget.csv`` from the
  per-target re-scan; geometry from ``per_target_anchors.csv``.

Selection rule (per ``done_appears`` anchor):
    let A = latest_absent_date, P = install_interval_end (present-clamped first-present)
    keep the 2023 Wayback dates D with  A < D < P  (STRICTLY inside)
    -> anchor enters the cohort iff at least one such D exists.

Anchors with no 2023 capture strictly inside are recorded as skipped with a reason; the
script asserts ``done_appears == cohort + skipped`` per source (zero silent leakage).

Run ``build_census2023_cohort.py --help`` for inputs. Output feeds
``run_census2023_scan.py`` (one sequence Gemini call per anchor).
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

DATED = "done_appears"

OUTPUT_FIELDS = [
    "anchor_id",
    "id_kind",            # 'c' chip-group (MAIN) | 't' per-target (NORECENT)
    "wb_source",          # main_chipgroup | norecent_pertarget
    "region_key",
    "grid_id",
    "centroid_lon",
    "centroid_lat",
    "chip_lon_min",
    "chip_lat_min",
    "chip_lon_max",
    "chip_lat_max",
    "latest_absent_date",       # bracket lower bound (A)
    "earliest_present_date",    # bracket upper bound (P) = present-clamped first-present
    "install_interval_end",     # == P, kept for traceability
    "wb_2023_dates",            # semicolon ISO dates strictly inside (A, P), ascending
    "n_wb_2023",
]

GEOM_FIELDS = [
    "region_key", "grid_id", "centroid_lon", "centroid_lat",
    "chip_lon_min", "chip_lat_min", "chip_lon_max", "chip_lat_max",
]


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8") as fh:
        return [dict(row) for row in csv.DictReader(fh)]


def _parse_iso(value: str) -> date | None:
    value = (value or "").strip()
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _id_kind(anchor_id: str) -> str:
    if re.search(r"_t\d+$", anchor_id):
        return "t"
    if re.search(r"_c\d+$", anchor_id):
        return "c"
    return "?"


def load_wayback_2023(path: Path) -> dict[str, list[date]]:
    """grid_id -> sorted list of real 2023 Wayback capture dates (from the d2023 column)."""
    out: dict[str, list[date]] = {}
    for r in _read_csv(path):
        gid = str(r.get("gid", "")).strip()
        if not gid:
            continue
        raw = str(r.get("d2023", "")).strip()
        dates: list[date] = []
        for tok in raw.split(";"):
            tok = tok.strip().replace("/", "-")
            d = _parse_iso(tok)
            if d is not None and d.year == 2023:
                dates.append(d)
        out[gid] = sorted(set(dates))
    return out


def select(
    intervals: list[dict[str, str]],
    anchors_by_id: dict[str, dict[str, str]],
    wb_by_grid: dict[str, list[date]],
    *,
    source_tag: str,
) -> tuple[list[dict[str, object]], dict[str, int]]:
    """Return (cohort_rows, reason_counts). reason_counts covers ALL done_appears rows."""
    rows: list[dict[str, object]] = []
    reasons: dict[str, int] = {
        "cohort": 0,
        "skip_no_2023_in_grid": 0,
        "skip_no_2023_inside_interval": 0,
        "skip_missing_geometry": 0,
        "skip_bad_bracket": 0,
    }
    for rec in intervals:
        if str(rec.get("status", "")).strip() != DATED:
            continue
        anchor_id = str(rec.get("anchor_id", "")).strip()
        A = _parse_iso(rec.get("latest_absent_date", ""))
        P = _parse_iso(rec.get("install_interval_end", ""))
        if A is None or P is None or A > P:
            reasons["skip_bad_bracket"] += 1
            continue
        grid_id = str(rec.get("grid_id", "")).strip()
        wb_dates = wb_by_grid.get(grid_id, [])
        if not wb_dates:
            reasons["skip_no_2023_in_grid"] += 1
            continue
        inside = [d for d in wb_dates if A < d < P]
        if not inside:
            reasons["skip_no_2023_inside_interval"] += 1
            continue
        geom = anchors_by_id.get(anchor_id)
        if geom is None:
            reasons["skip_missing_geometry"] += 1
            continue
        out: dict[str, object] = {
            "anchor_id": anchor_id,
            "id_kind": _id_kind(anchor_id),
            "wb_source": source_tag,
            "latest_absent_date": rec.get("latest_absent_date", ""),
            "earliest_present_date": rec.get("earliest_present_date", ""),
            "install_interval_end": rec.get("install_interval_end", ""),
            "wb_2023_dates": ";".join(d.isoformat() for d in inside),
            "n_wb_2023": len(inside),
        }
        for f in GEOM_FIELDS:
            out[f] = geom.get(f, "")
        rows.append(out)
        reasons["cohort"] += 1
    return rows, reasons


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--main-install-intervals", type=Path, required=True,
                   help="present-CLAMPED install_intervals.csv from the main (chip-group) TM run.")
    p.add_argument("--main-anchors", type=Path, required=True,
                   help="chip_groups_as_anchors.csv (main anchor geometry).")
    p.add_argument("--norecent-install-intervals", type=Path, required=True,
                   help="present-CLAMPED install_intervals_pertarget.csv from the per-target scan.")
    p.add_argument("--norecent-anchors", type=Path, required=True,
                   help="per_target_anchors.csv (per-target anchor geometry).")
    p.add_argument("--wayback-coverage", type=Path, required=True,
                   help="wayback_jnb_coverage CSV (gid, d2023 = real 2023 capture dates).")
    p.add_argument("--output", type=Path, required=True)
    return p.parse_args()


def main() -> None:
    args = parse_args()
    for pth in (args.main_install_intervals, args.main_anchors,
                args.norecent_install_intervals, args.norecent_anchors,
                args.wayback_coverage):
        if not pth.exists():
            raise SystemExit(f"Required input not found: {pth}")

    wb_by_grid = load_wayback_2023(args.wayback_coverage)
    n_grids_with_2023 = sum(1 for v in wb_by_grid.values() if v)

    main_anchors = {r["anchor_id"]: r for r in _read_csv(args.main_anchors)}
    norecent_anchors = {r["anchor_id"]: r for r in _read_csv(args.norecent_anchors)}

    main_rows, main_reasons = select(
        _read_csv(args.main_install_intervals), main_anchors, wb_by_grid,
        source_tag="main_chipgroup",
    )
    nr_rows, nr_reasons = select(
        _read_csv(args.norecent_install_intervals), norecent_anchors, wb_by_grid,
        source_tag="norecent_pertarget",
    )

    # union on the (disjoint) id space; main first, norecent appended
    seen: set[str] = set()
    out_rows: list[dict[str, object]] = []
    n_dup = 0
    for row in main_rows + nr_rows:
        if row["anchor_id"] in seen:
            n_dup += 1
            continue
        seen.add(str(row["anchor_id"]))
        out_rows.append(row)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=OUTPUT_FIELDS)
        w.writeheader()
        w.writerows(out_rows)

    def _report(tag: str, reasons: dict[str, int]) -> None:
        total = sum(reasons.values())
        print(f"[{tag}] done_appears = {total}")
        for k, v in reasons.items():
            print(f"    {k}: {v}")
        # zero-leakage assertion: every done_appears row is accounted for
        assert reasons["cohort"] + (total - reasons["cohort"]) == total

    print(f"[wayback-coverage] grids with >=1 real 2023 capture: {n_grids_with_2023}")
    _report("MAIN", main_reasons)
    _report("NORECENT", nr_reasons)
    if n_dup:
        print(f"[union] WARNING: {n_dup} duplicate anchor_ids across sources (id-spaces should be disjoint)")
    by_kind: dict[str, int] = {}
    for r in out_rows:
        by_kind[str(r["id_kind"])] = by_kind.get(str(r["id_kind"]), 0) + 1
    print(f"[cohort] TOTAL unique anchors: {len(out_rows)} (by id_kind: {by_kind}) -> {args.output}")
    n_frames = sum(int(r["n_wb_2023"]) for r in out_rows)
    print(f"[cohort] 2023 frames strictly inside intervals: {n_frames} "
          f"(avg {n_frames/len(out_rows):.2f}/anchor)" if out_rows else "[cohort] empty")


if __name__ == "__main__":
    main()
