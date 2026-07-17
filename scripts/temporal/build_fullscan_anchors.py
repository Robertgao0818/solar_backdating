#!/usr/bin/env python3
"""Build the full-population routed anchors for the fullscan Gemini backdating run.

Joins three frozen inputs into one scan-ready anchors table, then splits it by
the ISSUE-25 routed chip arm (`routed_A24_A48_cut40`):

- ``anchors_per_target_96m.csv`` — the per-target, target-centered 96 m boxes
  actually downloaded by the basemap rebuild. These override the group-level
  ``chip_*`` corners in ``chip_targets.csv``, which are shared across every
  target in a chip group and do NOT match the on-disk chips.
- ``chip_targets.csv`` — per-target metadata (``source_area_m2`` for routing,
  ``source_width_m``/``source_height_m`` for the review marker,
  ``target_label``, centroid).
- ``chip_groups_as_anchors.csv`` — group-level ``source_grids`` so
  run_adaptive_scan's ISSUE-26 per-grid Vexcel census-date resolution sees
  every grid a multi-grid chip group touches (anchor ``grid_id`` alone would
  under-resolve those).

Routing/provenance columns reuse ``issue25_stage_c.route_chip_arm`` and the
frozen amendment id so the full run is byte-consistent with the Stage-2
policy. Splitting reuses ``issue25_stage_c.split_anchors_by_chip_arm``
(writes ``anchors_A24.csv`` / ``anchors_A48.csv``).

Outputs (under --output-dir): anchors_all.csv, anchors_A24.csv,
anchors_A48.csv, anchors_summary.json.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.validation.issue25_stage_c import (
    ROUTE_AREA_CUT_M2,
    ROUTED_AMENDMENT_ID,
    route_chip_arm,
    split_anchors_by_chip_arm,
)

DEFAULT_BBOX_CSV = (
    Path.home()
    / "zasolar_data/geid_temporal/basemap_rebuild_2026-07-13/anchors_per_target_96m.csv"
)
DEFAULT_TARGETS_CSV = (
    Path.home()
    / "zasolar_data/geid_temporal"
    / "jhb_full382_unified_A_merge01_c0925_fpcut_2026-06-01_chipgroups"
    / "chip_targets.csv"
)
DEFAULT_GROUPS_CSV = DEFAULT_TARGETS_CSV.parent / "chip_groups_as_anchors.csv"
DEFAULT_OUTPUT_DIR = (
    Path.home() / "zasolar_data/geid_temporal/fullscan_gemini_backdating_2026-07"
)

BBOX_FIELDS = ("chip_lon_min", "chip_lat_min", "chip_lon_max", "chip_lat_max")


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8") as fh:
        return [dict(row) for row in csv.DictReader(fh)]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_rows(
    bbox_rows: list[dict[str, str]],
    target_rows: list[dict[str, str]],
    group_rows: list[dict[str, str]],
) -> list[dict[str, str]]:
    bbox_by_anchor = {row["anchor_id"]: row for row in bbox_rows}
    grids_by_chip = {row["chip_id"]: row.get("source_grids", "") for row in group_rows}

    if len(bbox_by_anchor) != len(bbox_rows):
        raise ValueError("duplicate anchor_id in per-target bbox CSV")

    out: list[dict[str, str]] = []
    missing_bbox: list[str] = []
    for row in target_rows:
        anchor_id = row["anchor_id"]
        bbox = bbox_by_anchor.pop(anchor_id, None)
        if bbox is None:
            missing_bbox.append(anchor_id)
            continue

        merged = dict(row)
        for field in BBOX_FIELDS:
            merged[field] = bbox[field]

        source_grids = grids_by_chip.get(row["chip_id"], "").strip()
        if not source_grids:
            source_grids = row.get("grid_id", "").strip()
        if not source_grids:
            raise ValueError(f"no source_grids/grid_id for {anchor_id}")
        merged["source_grids"] = source_grids

        try:
            area = float(row["source_area_m2"])
            width = float(row["source_width_m"])
            height = float(row["source_height_m"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"bad footprint fields for {anchor_id}") from exc
        if not (area > 0 and width > 0 and height > 0):
            raise ValueError(f"non-positive footprint for {anchor_id}")

        arm = route_chip_arm(area)
        merged["chip_arm"] = arm
        merged["review_extent_m"] = arm.removeprefix("A")
        merged["chip_arm_rule"] = f"source_area_m2>={ROUTE_AREA_CUT_M2:g}->A48 else A24"
        merged["chip_arm_amendment"] = ROUTED_AMENDMENT_ID
        out.append(merged)

    if missing_bbox:
        raise ValueError(
            f"{len(missing_bbox)} chip_targets rows missing per-target bbox, "
            f"first: {missing_bbox[:3]}"
        )
    if bbox_by_anchor:
        raise ValueError(
            f"{len(bbox_by_anchor)} bbox rows unmatched in chip_targets, "
            f"first: {sorted(bbox_by_anchor)[:3]}"
        )
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bbox-csv", type=Path, default=DEFAULT_BBOX_CSV)
    parser.add_argument("--targets-csv", type=Path, default=DEFAULT_TARGETS_CSV)
    parser.add_argument("--groups-csv", type=Path, default=DEFAULT_GROUPS_CSV)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--expected-count", type=int, default=41393)
    args = parser.parse_args()

    rows = build_rows(
        _read_csv(args.bbox_csv),
        _read_csv(args.targets_csv),
        _read_csv(args.groups_csv),
    )
    if len(rows) != args.expected_count:
        raise SystemExit(f"expected {args.expected_count} anchors, built {len(rows)}")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    all_path = args.output_dir / "anchors_all.csv"
    fieldnames = list(rows[0])
    with all_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    counts = split_anchors_by_chip_arm(all_path, args.output_dir)

    summary = {
        "inputs": {
            "bbox_csv": {"path": str(args.bbox_csv), "sha256": _sha256(args.bbox_csv)},
            "targets_csv": {
                "path": str(args.targets_csv),
                "sha256": _sha256(args.targets_csv),
            },
            "groups_csv": {
                "path": str(args.groups_csv),
                "sha256": _sha256(args.groups_csv),
            },
        },
        "n_anchors": len(rows),
        "arm_counts": counts,
        "route": {
            "cut_m2": ROUTE_AREA_CUT_M2,
            "amendment": ROUTED_AMENDMENT_ID,
        },
        "outputs": {
            "anchors_all": {"path": str(all_path), "sha256": _sha256(all_path)},
            **{
                f"anchors_{arm}": {
                    "path": str(args.output_dir / f"anchors_{arm}.csv"),
                    "sha256": _sha256(args.output_dir / f"anchors_{arm}.csv"),
                }
                for arm in counts
            },
        },
    }
    summary_path = args.output_dir / "anchors_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(f"anchors={len(rows)} arms={counts} -> {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
