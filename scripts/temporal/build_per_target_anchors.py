#!/usr/bin/env python3
"""Build per-target, target-centered 96m GEHI download anchors.

ISSUE-25 decision: the GEHI basemap rebuild download unit is a per-target,
target-centered 96m box, not the shared group-level box in
``chip_targets.csv``. That file's ``chip_lon_min/lat_min/lon_max/lat_max``
columns are the *group's* packed 96m chip (shared by all targets in the same
``chip_id``), so multiple targets in one group carry an identical box even
though their centroids differ. This script re-derives one 96m box per target,
centered on that target's own ``centroid_lon/centroid_lat``, using the same
pyproj Transformer + EPSG:32735 metric-CRS approach as
``build_inventory_chip_groups.py`` (kept consistent on purpose; do not
reinvent the projection here).

Downstream consumer: ``scripts/temporal/gehi_download.py`` via
``gehi_common.anchor_bbox_args``, which only reads ``anchor_id`` and the four
``chip_*`` corner columns from each anchor row.
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

import pandas as pd
from pyproj import Transformer

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

DEFAULT_TARGETS_CSV = (
    Path.home()
    / "zasolar_data/geid_temporal"
    / "jhb_full382_unified_A_merge01_c0925_fpcut_2026-06-01_chipgroups"
    / "chip_targets.csv"
)
DEFAULT_OUTPUT = (
    Path.home()
    / "zasolar_data/geid_temporal/basemap_rebuild_2026-07-13"
    / "anchors_per_target_96m.csv"
)
DEFAULT_METRIC_CRS = "EPSG:32735"
DEFAULT_WGS84 = "EPSG:4326"

OUTPUT_FIELDS = [
    "anchor_id",
    "chip_id",
    "region_key",
    "grid_id",
    "centroid_lon",
    "centroid_lat",
    "chip_lon_min",
    "chip_lat_min",
    "chip_lon_max",
    "chip_lat_max",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--targets-csv", type=Path, default=DEFAULT_TARGETS_CSV)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--metric-crs", default=DEFAULT_METRIC_CRS)
    parser.add_argument(
        "--chip-size-m",
        type=float,
        default=96.0,
        help="Fixed square anchor side length in metres.",
    )
    return parser.parse_args()


def build_anchors(
    df: pd.DataFrame,
    *,
    metric_crs: str,
    chip_size_m: float,
) -> pd.DataFrame:
    half = chip_size_m / 2.0
    to_metric = Transformer.from_crs(DEFAULT_WGS84, metric_crs, always_xy=True)
    to_wgs84 = Transformer.from_crs(metric_crs, DEFAULT_WGS84, always_xy=True)

    xs, ys = to_metric.transform(df["centroid_lon"].to_numpy(), df["centroid_lat"].to_numpy())

    lon_min, lat_min = to_wgs84.transform(xs - half, ys - half)
    lon_max, lat_max = to_wgs84.transform(xs + half, ys + half)

    out = pd.DataFrame(
        {
            "anchor_id": df["anchor_id"],
            "chip_id": df["chip_id"],
            "region_key": df["region_key"],
            "grid_id": df["grid_id"],
            "centroid_lon": df["centroid_lon"],
            "centroid_lat": df["centroid_lat"],
            "chip_lon_min": lon_min,
            "chip_lat_min": lat_min,
            "chip_lon_max": lon_max,
            "chip_lat_max": lat_max,
        }
    )
    return out[OUTPUT_FIELDS]


def self_check(df_src: pd.DataFrame, out: pd.DataFrame, *, metric_crs: str, chip_size_m: float) -> None:
    to_metric = Transformer.from_crs(DEFAULT_WGS84, metric_crs, always_xy=True)

    n = len(out)
    print(f"row_count = {n}")

    sample = out.sample(min(5, n), random_state=0)
    print("sample box edge lengths (should be ~{:.1f}m):".format(chip_size_m))
    for _, row in sample.iterrows():
        x_min, y_min = to_metric.transform(row["chip_lon_min"], row["chip_lat_min"])
        x_max, y_max = to_metric.transform(row["chip_lon_max"], row["chip_lat_max"])
        width_m = x_max - x_min
        height_m = y_max - y_min
        cx, cy = to_metric.transform(row["centroid_lon"], row["centroid_lat"])
        center_x = (x_min + x_max) / 2.0
        center_y = (y_min + y_max) / 2.0
        center_err = ((center_x - cx) ** 2 + (center_y - cy) ** 2) ** 0.5
        print(
            f"  anchor_id={row['anchor_id']} width_m={width_m:.3f} "
            f"height_m={height_m:.3f} center_offset_m={center_err:.4f}"
        )

    merged = out.merge(
        df_src[["anchor_id", "chip_lon_min", "chip_lat_min", "chip_lon_max", "chip_lat_max"]],
        on="anchor_id",
        suffixes=("_new", "_group"),
    )
    gx_min, gy_min = to_metric.transform(
        merged["chip_lon_min_group"].to_numpy(), merged["chip_lat_min_group"].to_numpy()
    )
    gx_max, gy_max = to_metric.transform(
        merged["chip_lon_max_group"].to_numpy(), merged["chip_lat_max_group"].to_numpy()
    )
    nx_min, ny_min = to_metric.transform(
        merged["chip_lon_min_new"].to_numpy(), merged["chip_lat_min_new"].to_numpy()
    )
    nx_max, ny_max = to_metric.transform(
        merged["chip_lon_max_new"].to_numpy(), merged["chip_lat_max_new"].to_numpy()
    )
    group_cx = (gx_min + gx_max) / 2.0
    group_cy = (gy_min + gy_max) / 2.0
    new_cx = (nx_min + nx_max) / 2.0
    new_cy = (ny_min + ny_max) / 2.0
    offsets = ((new_cx - group_cx) ** 2 + (new_cy - group_cy) ** 2) ** 0.5
    offsets = pd.Series(offsets)
    print("offset from original group-box center (metres):")
    print(
        "  min={:.3f} p50={:.3f} p90={:.3f} max={:.3f} mean={:.3f}".format(
            offsets.min(), offsets.median(), offsets.quantile(0.9), offsets.max(), offsets.mean()
        )
    )
    print(f"  n(offset==0) = {(offsets == 0).sum()} of {len(offsets)}")


def main() -> None:
    args = parse_args()
    if not args.targets_csv.exists():
        raise SystemExit(f"targets CSV not found: {args.targets_csv}")

    df_src = pd.read_csv(args.targets_csv)
    required = {"anchor_id", "chip_id", "region_key", "grid_id", "centroid_lon", "centroid_lat"}
    missing = required - set(df_src.columns)
    if missing:
        raise SystemExit(f"targets CSV missing required columns: {sorted(missing)}")

    out = build_anchors(df_src, metric_crs=args.metric_crs, chip_size_m=args.chip_size_m)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(args.output, index=False, quoting=csv.QUOTE_MINIMAL)
    print(f"wrote {len(out)} rows to {args.output}")

    self_check(df_src, out, metric_crs=args.metric_crs, chip_size_m=args.chip_size_m)


if __name__ == "__main__":
    main()
