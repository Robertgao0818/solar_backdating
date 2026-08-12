#!/usr/bin/env python3
"""Build CT target-level chip-group contracts from the frozen anchor package.

Cape Town production is deliberately target-level: every frozen inventory
anchor is its own 96 m source chip.  This adapter emits the historical
``chip_groups_as_anchors.csv`` and ``chip_targets.csv`` contracts without
repacking targets or changing their IDs, boxes, grids, or review arms.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path

import pandas as pd

DEFAULT_ANCHORS = (
    Path.home()
    / "zasolar_data/geid_temporal/cape_town_top52_backdating_v1_20260724"
    / "anchors_v1/anchors_all.csv"
)
DEFAULT_OUTPUT = DEFAULT_ANCHORS.parent.parent / "groups_v1"
EXPECTED_COUNT = 21_453
EXPECTED_INPUT_FIELDS = {
    "anchor_id",
    "source_feature_id",
    "source_grid",
    "centroid_grid",
    "source_grids",
    "region_key",
    "centroid_lon",
    "centroid_lat",
    "metric_crs",
    "area_m2",
    "confidence",
    "source_width_m",
    "source_height_m",
    "chip_lon_min",
    "chip_lat_min",
    "chip_lon_max",
    "chip_lat_max",
    "source_box_width_m",
    "source_box_height_m",
    "center_offset_m",
    "chip_arm",
    "review_extent_m",
    "geometry_exception",
    "review_required",
    "geometry_version",
    "input_inventory_sha256",
    "roster_sha256",
    "cohort_version",
}

GROUP_FIELDS = [
    "anchor_id",
    "chip_id",
    "region_key",
    "grid_id",
    "source_grid",
    "centroid_grid",
    "source_grids",
    "source_feature_id",
    "centroid_lon",
    "centroid_lat",
    "metric_crs",
    "source_area_m2",
    "source_width_m",
    "source_height_m",
    "chip_lon_min",
    "chip_lat_min",
    "chip_lon_max",
    "chip_lat_max",
    "chip_size_m",
    "chip_half_m",
    "search_radius_m",
    "n_targets",
    "target_anchor_ids",
    "target_offset_x_m",
    "target_offset_y_m",
    "target_label",
    "chip_arm",
    "review_extent_m",
    "geometry_exception",
    "review_required",
    "geometry_version",
    "input_inventory_sha256",
    "roster_sha256",
    "cohort_version",
    "anchor_policy",
]

TARGET_FIELDS = [
    "anchor_id",
    "chip_id",
    "region_key",
    "grid_id",
    "source_grid",
    "centroid_grid",
    "source_grids",
    "source_feature_id",
    "target_index",
    "target_label",
    "centroid_lon",
    "centroid_lat",
    "metric_crs",
    "source_area_m2",
    "source_width_m",
    "source_height_m",
    "confidence",
    "target_offset_x_m",
    "target_offset_y_m",
    "search_radius_m",
    "chip_size_m",
    "chip_lon_min",
    "chip_lat_min",
    "chip_lon_max",
    "chip_lat_max",
    "chip_arm",
    "review_extent_m",
    "geometry_exception",
    "review_required",
    "geometry_version",
    "input_inventory_sha256",
    "roster_sha256",
    "cohort_version",
]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_anchors(df: pd.DataFrame, expected_count: int = EXPECTED_COUNT) -> None:
    missing = EXPECTED_INPUT_FIELDS - set(df.columns)
    unknown = set(df.columns) - EXPECTED_INPUT_FIELDS
    if missing or unknown:
        raise ValueError(
            f"anchor schema mismatch: missing={sorted(missing)} unknown={sorted(unknown)}"
        )
    if len(df) != expected_count:
        raise ValueError(f"unexpected CT anchor count: {len(df)} != {expected_count}")
    if not df.anchor_id.is_unique or not df.source_feature_id.is_unique:
        raise ValueError("anchor_id and source_feature_id must both be unique")
    if not df.region_key.eq("cape_town").all():
        raise ValueError("non-Cape Town row in CT anchors")
    if not df.metric_crs.eq("EPSG:32734").all():
        raise ValueError("CT anchors must use EPSG:32734")
    if not df.source_grid.str.fullmatch(r"CPT\d{4}").all():
        raise ValueError("source_grid contains a non-CPT ID")
    if not df.centroid_grid.str.fullmatch(r"CPT\d{4}").all():
        raise ValueError("centroid_grid contains a non-CPT ID")
    if not df.source_box_width_m.astype(float).eq(96.0).all():
        raise ValueError("source_box_width_m must be 96")
    if not df.source_box_height_m.astype(float).eq(96.0).all():
        raise ValueError("source_box_height_m must be 96")
    if df.center_offset_m.astype(float).abs().max() > 0.01:
        raise ValueError("CT target-level groups require zero center offset")


def build_rows(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    base = pd.DataFrame(
        {
            "anchor_id": df.anchor_id,
            "chip_id": df.anchor_id,
            "region_key": df.region_key,
            "grid_id": df.source_grid,
            "source_grid": df.source_grid,
            "centroid_grid": df.centroid_grid,
            "source_grids": df.source_grids,
            "source_feature_id": df.source_feature_id.astype(int),
            "centroid_lon": df.centroid_lon,
            "centroid_lat": df.centroid_lat,
            "metric_crs": df.metric_crs,
            "source_area_m2": df.area_m2,
            "source_width_m": df.source_width_m,
            "source_height_m": df.source_height_m,
            "chip_lon_min": df.chip_lon_min,
            "chip_lat_min": df.chip_lat_min,
            "chip_lon_max": df.chip_lon_max,
            "chip_lat_max": df.chip_lat_max,
            "chip_size_m": 96.0,
            "search_radius_m": 10.0,
            "target_offset_x_m": 0.0,
            "target_offset_y_m": 0.0,
            "target_label": "T01",
            "chip_arm": df.chip_arm,
            "review_extent_m": df.review_extent_m,
            "geometry_exception": df.geometry_exception.fillna(""),
            "review_required": df.review_required,
            "geometry_version": df.geometry_version,
            "input_inventory_sha256": df.input_inventory_sha256,
            "roster_sha256": df.roster_sha256,
            "cohort_version": df.cohort_version,
        }
    )
    groups = base.assign(
        chip_half_m=48.0,
        n_targets=1,
        target_anchor_ids=base.anchor_id,
        anchor_policy="ct_frozen_target_centered_96m_v1",
    )[GROUP_FIELDS]
    targets = base.assign(target_index=1, confidence=df.confidence)[TARGET_FIELDS]
    return groups, targets


def write_csv(df: pd.DataFrame, path: Path) -> None:
    df.to_csv(path, index=False, lineterminator="\n", quoting=csv.QUOTE_MINIMAL)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--anchors-csv", type=Path, default=DEFAULT_ANCHORS)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--expected-count", type=int, default=EXPECTED_COUNT)
    args = parser.parse_args()

    if not args.anchors_csv.exists():
        raise SystemExit(f"anchors not found: {args.anchors_csv}")
    if args.output_dir.exists():
        raise SystemExit(f"refusing to overwrite existing output: {args.output_dir}")

    anchors = pd.read_csv(args.anchors_csv)
    validate_anchors(anchors, args.expected_count)
    groups, targets = build_rows(anchors)
    if not groups.anchor_id.equals(targets.anchor_id):
        raise ValueError("group/target anchor order changed")
    if not groups.anchor_id.eq(groups.chip_id).all():
        raise ValueError("CT target-level contract requires anchor_id == chip_id")
    if groups[["target_offset_x_m", "target_offset_y_m"]].abs().to_numpy().max() != 0:
        raise ValueError("CT target-level offsets must be exactly zero")

    args.output_dir.mkdir(parents=True)
    group_path = args.output_dir / "chip_groups_as_anchors.csv"
    target_path = args.output_dir / "chip_targets.csv"
    write_csv(groups, group_path)
    write_csv(targets, target_path)
    summary = {
        "schema_version": "ct_target_groups_v1",
        "source_anchors": str(args.anchors_csv),
        "source_anchors_sha256": sha256(args.anchors_csv),
        "row_count": len(groups),
        "group_count": len(groups),
        "target_count": len(targets),
        "targets_per_group": 1,
        "chip_size_m": 96.0,
        "metric_crs": ["EPSG:32734"],
        "region_keys": ["cape_town"],
        "max_target_offset_m": 0.0,
        "grouping_policy": "one frozen target per target-centered source chip",
    }
    summary_path = args.output_dir / "summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    with (args.output_dir / "artifacts.sha256").open("w") as handle:
        for path in (group_path, target_path, summary_path):
            handle.write(f"{sha256(path)}  {path}\n")
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
