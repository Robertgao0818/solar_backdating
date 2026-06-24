#!/usr/bin/env python3
"""Build the Wayback re-scan anchor subset: the unresolved 2023-gap cases.

The TM full-inventory scan leaves a tail of anchors the Google Earth Time
Machine vintages could not resolve. ESRI Wayback fills exactly those (it has
real 2023 captures where TM has a 2023 hole over JHB), so this script selects
the still-unresolved anchors and writes a single anchors CSV for a
``run_adaptive_scan.py --provider Wayback`` pass.

Two granularities are unioned:

* NORECENT (per-target ``t`` ids): the ``no_recent_anchor`` chip-groups were
  re-expanded per-target and rescanned by the ``bd_norecent`` run into its own
  scan-states dir. We take every target whose *latest* status is still
  unresolved (``done_ambiguous_*`` or ``done_already_present_before_geid_history``).
  Geometry comes from ``per_target_anchors.csv``.
* MAIN (chip-group ``c`` ids): the other unresolved categories
  (nonmonotonic / marker_missed / gemini_failed / already_present) were never
  reprocessed, so we take them from the main install_intervals at chip-group
  granularity. ``no_recent_anchor`` is deliberately EXCLUDED here because it is
  fully superseded by the per-target NORECENT rows. Geometry comes from
  ``chip_groups_as_anchors.csv``.

Output columns are the minimal set ``run_adaptive_scan`` + the GEHI wrappers
need (anchor_id, region_key, grid_id, centroid + chip bbox), plus
``wb_source``/``wb_prior_status`` for provenance. Counts per source/status are
printed; nothing is silently dropped.
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

OUTPUT_FIELDS = [
    "anchor_id",
    "region_key",
    "grid_id",
    "centroid_lon",
    "centroid_lat",
    "chip_lon_min",
    "chip_lat_min",
    "chip_lon_max",
    "chip_lat_max",
    "wb_source",
    "wb_prior_status",
]

# Geometry columns copied verbatim from the source anchor CSVs.
GEOM_FIELDS = [
    "region_key",
    "grid_id",
    "centroid_lon",
    "centroid_lat",
    "chip_lon_min",
    "chip_lat_min",
    "chip_lon_max",
    "chip_lat_max",
]


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8") as fh:
        return [dict(row) for row in csv.DictReader(fh)]


def _is_unresolved(status: str) -> bool:
    return status.startswith("done_ambiguous") or status == "done_already_present_before_geid_history"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--main-install-intervals", type=Path, required=True,
                   help="install_intervals.csv from the main (chip-group) TM run.")
    p.add_argument("--main-anchors", type=Path, required=True,
                   help="chip_groups_as_anchors.csv (main anchor geometry).")
    p.add_argument("--norecent-install-intervals", type=Path, required=True,
                   help="install_intervals.csv inferred from the norecent per-target scan-states.")
    p.add_argument("--norecent-anchors", type=Path, required=True,
                   help="per_target_anchors.csv (norecent anchor geometry).")
    p.add_argument("--output", type=Path, required=True)
    return p.parse_args()


def _select(intervals: list[dict[str, str]], anchors_by_id: dict[str, dict[str, str]],
            *, source_tag: str, exclude_no_recent: bool) -> tuple[list[dict[str, str]], dict[str, int], int]:
    """Return (rows, status_counts, n_missing_geometry) for unresolved anchors."""
    status_counts: dict[str, int] = {}
    rows: list[dict[str, str]] = []
    missing = 0
    for rec in intervals:
        status = str(rec.get("status", "")).strip()
        if not _is_unresolved(status):
            continue
        if exclude_no_recent and status == "done_ambiguous_no_recent_anchor":
            continue
        anchor_id = str(rec.get("anchor_id", "")).strip()
        geom = anchors_by_id.get(anchor_id)
        if geom is None:
            missing += 1
            continue
        status_counts[status] = status_counts.get(status, 0) + 1
        out = {"anchor_id": anchor_id, "wb_source": source_tag, "wb_prior_status": status}
        for f in GEOM_FIELDS:
            out[f] = geom.get(f, "")
        rows.append(out)
    return rows, status_counts, missing


def main() -> None:
    args = parse_args()
    for pth in (args.main_install_intervals, args.main_anchors,
                args.norecent_install_intervals, args.norecent_anchors):
        if not pth.exists():
            raise SystemExit(f"Required input not found: {pth}")

    main_anchors = {r["anchor_id"]: r for r in _read_csv(args.main_anchors)}
    norecent_anchors = {r["anchor_id"]: r for r in _read_csv(args.norecent_anchors)}

    nr_rows, nr_counts, nr_missing = _select(
        _read_csv(args.norecent_install_intervals), norecent_anchors,
        source_tag="norecent_pertarget", exclude_no_recent=False,
    )
    main_rows, main_counts, main_missing = _select(
        _read_csv(args.main_install_intervals), main_anchors,
        source_tag="main_chipgroup", exclude_no_recent=True,
    )

    seen: set[str] = set()
    out_rows: list[dict[str, str]] = []
    for row in nr_rows + main_rows:  # norecent wins on the (disjoint) id space
        if row["anchor_id"] in seen:
            continue
        seen.add(row["anchor_id"])
        out_rows.append(row)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=OUTPUT_FIELDS)
        w.writeheader()
        w.writerows(out_rows)

    print(f"[wayback-subset] NORECENT per-target unresolved: {len(nr_rows)}")
    for s, n in sorted(nr_counts.items()):
        print(f"    {s}: {n}")
    if nr_missing:
        print(f"    WARNING: {nr_missing} norecent unresolved anchor_ids had no geometry row (skipped)")
    print(f"[wayback-subset] MAIN chip-group unresolved (excl. no_recent): {len(main_rows)}")
    for s, n in sorted(main_counts.items()):
        print(f"    {s}: {n}")
    if main_missing:
        print(f"    WARNING: {main_missing} main unresolved anchor_ids had no geometry row (skipped)")
    print(f"[wayback-subset] TOTAL unique anchors written: {len(out_rows)} -> {args.output}")


if __name__ == "__main__":
    main()
