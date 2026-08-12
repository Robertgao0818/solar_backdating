#!/usr/bin/env python3
"""Build the Cape Town V1.4 target-anchor package from the frozen CT roster.

This builder deliberately consumes only the hash-locked CT scope manifest. It
does not read the Johannesburg anchor/group packages or infer a grid namespace.
The output is the per-target CSV contract used by CT availability and scan
stages, split into the A24/A48 review arms.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path

import pandas as pd

DEFAULT_MANIFEST = (
    Path.home()
    / "zasolar_data/geid_temporal/cape_town_top52_backdating_v1_20260724"
    / "manifests/ct_top52_manifest_v1.csv"
)
DEFAULT_OUTPUT = DEFAULT_MANIFEST.parent.parent / "anchors_v1"
EXPECTED_FIELDS = {
    "anchor_id",
    "source_feature_id",
    "source_grid",
    "centroid_grid",
    "source_grids",
    "source_grid_matches_centroid_grid",
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

OUTPUT_FIELDS = [
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
]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate(
    df: pd.DataFrame,
    *,
    expected_count: int | None = 21453,
    expected_a24: int | None = 19729,
    expected_a48: int | None = 1724,
) -> None:
    unknown = set(df.columns) - EXPECTED_FIELDS
    missing = EXPECTED_FIELDS - set(df.columns)
    if unknown or missing:
        raise ValueError(f"manifest schema mismatch: missing={sorted(missing)} unknown={sorted(unknown)}")
    if expected_count is not None and len(df) != expected_count:
        raise ValueError(f"unexpected CT cohort row count: {len(df)} != {expected_count}")
    if df.anchor_id.isna().any() or not df.anchor_id.is_unique:
        raise ValueError("anchor_id must be non-null and unique")
    if df.source_feature_id.isna().any() or not df.source_feature_id.is_unique:
        raise ValueError("source_feature_id must be non-null and unique")
    if not df.region_key.eq("cape_town").all():
        raise ValueError("CT manifest contains a non-cape_town region")
    if not df.metric_crs.eq("EPSG:32734").all():
        raise ValueError("CT manifest contains a non-EPSG:32734 row")
    if not df.source_grid.str.fullmatch(r"CPT\d{4}").all():
        raise ValueError("source_grid contains a non-CPT namespace")
    if not df.centroid_grid.str.fullmatch(r"CPT\d{4}").all():
        raise ValueError("centroid_grid contains a non-CPT namespace")
    if not df.chip_arm.isin(["A24", "A48"]).all():
        raise ValueError("chip_arm must be A24 or A48")
    if not df.geometry_version.isin(
        ["fullscan_target96_review24_v2", "fullscan_target96_review48_v2"]
    ).all():
        raise ValueError("unknown geometry_version")
    expected_gv = df.chip_arm.map(
        {"A24": "fullscan_target96_review24_v2", "A48": "fullscan_target96_review48_v2"}
    )
    if not df.geometry_version.eq(expected_gv).all():
        raise ValueError("geometry_version does not match chip_arm")
    expected_extent = df.chip_arm.map({"A24": 24.0, "A48": 48.0})
    if not df.review_extent_m.astype(float).eq(expected_extent).all():
        raise ValueError("review_extent_m does not match chip_arm")
    if not df.source_box_width_m.astype(float).eq(96.0).all() or not df.source_box_height_m.astype(float).eq(96.0).all():
        raise ValueError("CT source boxes must be 96 m square")
    if df.center_offset_m.astype(float).abs().max() > 0.01:
        raise ValueError("non-zero target/bbox center offset")
    a24 = int((df.chip_arm == "A24").sum())
    a48 = int((df.chip_arm == "A48").sum())
    if expected_a24 is not None and a24 != expected_a24:
        raise ValueError(f"unexpected A24 count: {a24} != {expected_a24}")
    if expected_a48 is not None and a48 != expected_a48:
        raise ValueError(f"unexpected A48 count: {a48} != {expected_a48}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--expected-count",
        type=int,
        default=21453,
        help="Expected row count (Top-52 default 21453; citywide 111801). "
        "Pass 0 to skip the count gate.",
    )
    parser.add_argument(
        "--expected-a24",
        type=int,
        default=19729,
        help="Expected A24 arm count (0 = skip).",
    )
    parser.add_argument(
        "--expected-a48",
        type=int,
        default=1724,
        help="Expected A48 arm count (0 = skip).",
    )
    args = parser.parse_args()
    if not args.manifest.exists():
        raise SystemExit(f"manifest not found: {args.manifest}")
    if args.output_dir.exists():
        raise SystemExit(f"refusing to overwrite existing output: {args.output_dir}")

    df = pd.read_csv(args.manifest)
    validate(
        df,
        expected_count=None if args.expected_count == 0 else args.expected_count,
        expected_a24=None if args.expected_a24 == 0 else args.expected_a24,
        expected_a48=None if args.expected_a48 == 0 else args.expected_a48,
    )
    args.output_dir.mkdir(parents=True)
    out = df[OUTPUT_FIELDS].copy()
    out.to_csv(args.output_dir / "anchors_all.csv", index=False, lineterminator="\n", quoting=csv.QUOTE_MINIMAL)
    out[out.chip_arm == "A24"].to_csv(args.output_dir / "anchors_A24.csv", index=False, lineterminator="\n")
    out[out.chip_arm == "A48"].to_csv(args.output_dir / "anchors_A48.csv", index=False, lineterminator="\n")
    summary = {
        "schema_version": "ct_anchors_v1",
        "manifest": str(args.manifest),
        "manifest_sha256": sha256(args.manifest),
        "row_count": len(out),
        "unique_anchor_ids": int(out.anchor_id.nunique()),
        "arm_counts": {str(k): int(v) for k, v in out.chip_arm.value_counts().sort_index().items()},
        "region_keys": sorted(out.region_key.unique().tolist()),
        "metric_crs": sorted(out.metric_crs.unique().tolist()),
        "geometry_versions": sorted(out.geometry_version.unique().tolist()),
        "review_required_count": int(out.review_required.astype(bool).sum()),
        "source_grid_centroid_grid_mismatch_count": int((~df.source_grid_matches_centroid_grid.astype(bool)).sum()),
    }
    (args.output_dir / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    paths = sorted(args.output_dir.iterdir())
    with (args.output_dir / "artifacts.sha256").open("w") as handle:
        for path in paths:
            if path.name == "artifacts.sha256":
                continue
            handle.write(f"{sha256(path)}  {path}\n")
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
