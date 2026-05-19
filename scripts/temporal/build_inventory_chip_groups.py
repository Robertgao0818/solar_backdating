#!/usr/bin/env python3
"""Build fixed-size multi-target chip groups from an upstream PV inventory.

The upstream ZAsolar inventory remains the authoritative set of current PV
prediction footprints. This script creates a temporal-pipeline bridge: nearby
inventory polygons are packed into fixed-size GEHI/Gemini chip groups so one
historical image stack can cover several adjacent PV detections.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import geopandas as gpd
import numpy as np
import pandas as pd
from pyproj import Transformer
from shapely.geometry import Point, box
from shapely.strtree import STRtree

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.temporal.geid_temporal_common import write_csv_rows

ZASOLAR_ROOT = Path("/home/gaosh/projects/ZAsolar")
DEFAULT_INVENTORY = (
    ZASOLAR_ROOT
    / "results/analysis/full382_merge01_2026-05-15/"
    / "jhb_full382_unified_A_merge01_c0925.gpkg"
)
DEFAULT_OUTPUT_DIR = (
    Path.home() / "zasolar_data/geid_temporal/jhb_full382_unified_A_merge01_c0925_chipgroups"
)
DEFAULT_METRIC_CRS = "EPSG:32735"
DEFAULT_WGS84 = "EPSG:4326"

GROUP_ANCHOR_FIELDS = [
    "anchor_id",
    "chip_id",
    "region_key",
    "grid_id",
    "source_annotation_path",
    "source_feature_id",
    "quality_tier",
    "anchor_policy",
    "centroid_lon",
    "centroid_lat",
    "source_area_m2",
    "source_width_m",
    "source_height_m",
    "chip_half_m",
    "search_radius_m",
    "chip_lon_min",
    "chip_lat_min",
    "chip_lon_max",
    "chip_lat_max",
    "alignment_note",
    "inventory_tag",
    "source_inventory_path",
    "n_targets",
    "target_anchor_ids",
    "source_grids",
    "chip_size_m",
    "group_width_m",
    "group_height_m",
    "max_target_offset_m",
]

TARGET_FIELDS = [
    "anchor_id",
    "chip_id",
    "region_key",
    "grid_id",
    "source_inventory_path",
    "source_feature_id",
    "source_grid",
    "target_index",
    "target_label",
    "centroid_lon",
    "centroid_lat",
    "source_area_m2",
    "source_width_m",
    "source_height_m",
    "confidence",
    "score",
    "sam_score",
    "n_merged",
    "target_offset_x_m",
    "target_offset_y_m",
    "search_radius_m",
    "chip_size_m",
    "chip_lon_min",
    "chip_lat_min",
    "chip_lon_max",
    "chip_lat_max",
]


@dataclass(frozen=True)
class Target:
    source_idx: int
    anchor_id: str
    region_key: str
    grid_id: str
    source_grid: str
    geom: Any
    centroid: Point
    source_bounds: tuple[float, float, float, float]
    pack_bounds: tuple[float, float, float, float]
    area_m2: float
    width_m: float
    height_m: float
    confidence: Any
    score: Any
    sam_score: Any
    n_merged: Any


@dataclass(frozen=True)
class ChipGroup:
    chip_id: str
    member_indices: tuple[int, ...]
    center_x: float
    center_y: float
    chip_bounds: tuple[float, float, float, float]
    pack_bounds: tuple[float, float, float, float]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", type=Path, default=DEFAULT_INVENTORY)
    parser.add_argument("--layer", help="Optional input GPKG layer name.")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--region-key", default="johannesburg")
    parser.add_argument(
        "--inventory-tag",
        default="jhb_full382_unified_A_merge01_c0925",
        help="Stable prefix for generated target/chip IDs.",
    )
    parser.add_argument("--metric-crs", default=DEFAULT_METRIC_CRS)
    parser.add_argument("--min-confidence", type=float, default=None)
    parser.add_argument(
        "--chip-size-m",
        type=float,
        default=96.0,
        help="Fixed square chip side length in metres.",
    )
    parser.add_argument(
        "--pack-margin-m",
        type=float,
        default=6.0,
        help="Margin around each source polygon bbox when deciding if targets fit in one chip.",
    )
    parser.add_argument(
        "--search-radius-m",
        type=float,
        default=10.0,
        help="Downstream visual search tolerance around each target marker.",
    )
    parser.add_argument(
        "--max-targets-per-chip",
        type=int,
        default=4,
        help="Soft upper bound on target count per Gemini chip for automatic review.",
    )
    parser.add_argument(
        "--hard-max-targets-per-chip",
        type=int,
        default=6,
        help="Reject larger target groups because Gemini matrix attention becomes unreliable.",
    )
    parser.add_argument(
        "--no-gpkg",
        action="store_true",
        help="Skip writing QA GeoPackages for chip boxes and target points.",
    )
    return parser.parse_args()


def _safe_token(value: str) -> str:
    token = re.sub(r"[^A-Za-z0-9]+", "_", value.strip()).strip("_").lower()
    return token or "inventory"


def _format_float(value: float, ndigits: int = 4) -> str:
    if value is None or not math.isfinite(float(value)):
        return ""
    return f"{float(value):.{ndigits}f}"


def _format_any(value: Any) -> Any:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except TypeError:
        pass
    return value


def _union_bounds(bounds: Iterable[tuple[float, float, float, float]]) -> tuple[float, float, float, float]:
    mins_x: list[float] = []
    mins_y: list[float] = []
    maxs_x: list[float] = []
    maxs_y: list[float] = []
    for minx, miny, maxx, maxy in bounds:
        mins_x.append(minx)
        mins_y.append(miny)
        maxs_x.append(maxx)
        maxs_y.append(maxy)
    return min(mins_x), min(mins_y), max(maxs_x), max(maxs_y)


def _expand_bounds(
    bounds: tuple[float, float, float, float],
    margin: float,
) -> tuple[float, float, float, float]:
    minx, miny, maxx, maxy = bounds
    return minx - margin, miny - margin, maxx + margin, maxy + margin


def _bounds_width(bounds: tuple[float, float, float, float]) -> float:
    return float(bounds[2] - bounds[0])


def _bounds_height(bounds: tuple[float, float, float, float]) -> float:
    return float(bounds[3] - bounds[1])


def _bbox_to_wgs84(
    transformer: Transformer,
    bounds: tuple[float, float, float, float],
) -> tuple[float, float, float, float]:
    minx, miny, maxx, maxy = bounds
    points = [
        transformer.transform(minx, miny),
        transformer.transform(minx, maxy),
        transformer.transform(maxx, miny),
        transformer.transform(maxx, maxy),
    ]
    lons = [p[0] for p in points]
    lats = [p[1] for p in points]
    return min(lons), min(lats), max(lons), max(lats)


def _point_to_wgs84(transformer: Transformer, point: Point) -> tuple[float, float]:
    return transformer.transform(float(point.x), float(point.y))


def _load_inventory(
    path: Path,
    *,
    layer: str | None,
    metric_crs: str,
    min_confidence: float | None,
) -> gpd.GeoDataFrame:
    if not path.exists():
        raise SystemExit(f"Inventory not found: {path}")
    kwargs: dict[str, Any] = {}
    if layer:
        kwargs["layer"] = layer
    gdf = gpd.read_file(path, **kwargs)
    if gdf.empty:
        raise SystemExit(f"Inventory has no features: {path}")
    if gdf.crs is None:
        raise SystemExit(f"Inventory CRS is missing: {path}")
    gdf = gdf.to_crs(metric_crs)
    gdf["__source_feature_id"] = np.arange(len(gdf), dtype=int)
    if min_confidence is not None:
        score_col = "confidence" if "confidence" in gdf.columns else "score"
        if score_col not in gdf.columns:
            raise SystemExit("--min-confidence requested but no confidence/score column exists")
        gdf = gdf[gdf[score_col] >= min_confidence].copy()
    gdf = gdf.reset_index(drop=True)
    if gdf.empty:
        raise SystemExit("No features remain after filtering.")
    return gdf


def make_targets(
    gdf: gpd.GeoDataFrame,
    *,
    region_key: str,
    inventory_tag: str,
    pack_margin_m: float,
) -> list[Target]:
    token = _safe_token(inventory_tag)
    targets: list[Target] = []
    for idx, row in gdf.iterrows():
        geom = row.geometry
        if geom is None or geom.is_empty:
            continue
        if not geom.is_valid:
            geom = geom.buffer(0)
        if geom is None or geom.is_empty:
            continue
        centroid = geom.centroid
        if centroid is None or centroid.is_empty:
            continue
        bounds = tuple(float(v) for v in geom.bounds)
        width = _bounds_width(bounds)
        height = _bounds_height(bounds)
        source_idx = int(row.get("__source_feature_id", idx))
        source_grid = str(row.get("source_grid", "") or row.get("grid_id", "") or "").strip()
        grid_id = source_grid or "unknown"
        targets.append(
            Target(
                source_idx=source_idx,
                anchor_id=f"{token}_t{source_idx + 1:08d}",
                region_key=region_key,
                grid_id=grid_id,
                source_grid=source_grid,
                geom=geom,
                centroid=centroid,
                source_bounds=bounds,
                pack_bounds=_expand_bounds(bounds, pack_margin_m),
                area_m2=float(geom.area),
                width_m=width,
                height_m=height,
                confidence=row.get("confidence", ""),
                score=row.get("score", ""),
                sam_score=row.get("sam_score", ""),
                n_merged=row.get("n_merged", ""),
            )
        )
    if not targets:
        raise SystemExit("No valid target geometries found.")
    return targets


def build_chip_groups(
    targets: Sequence[Target],
    *,
    chip_size_m: float,
    max_targets_per_chip: int,
    inventory_tag: str,
) -> list[ChipGroup]:
    if chip_size_m <= 0:
        raise ValueError("chip_size_m must be positive")
    if max_targets_per_chip <= 0:
        raise ValueError("max_targets_per_chip must be positive")

    points = [target.centroid for target in targets]
    tree = STRtree(points)
    half = chip_size_m / 2.0
    assigned = np.zeros(len(targets), dtype=bool)
    seed_order = sorted(
        range(len(targets)),
        key=lambda i: (
            math.floor(float(targets[i].centroid.y) / chip_size_m),
            math.floor(float(targets[i].centroid.x) / chip_size_m),
            targets[i].source_grid,
            targets[i].source_idx,
        ),
    )

    groups: list[ChipGroup] = []
    token = _safe_token(inventory_tag)
    for seed_idx in seed_order:
        if assigned[seed_idx]:
            continue
        seed = targets[seed_idx]
        members = [seed_idx]
        group_bounds = seed.pack_bounds
        query_bounds = (
            float(seed.centroid.x) - half,
            float(seed.centroid.y) - half,
            float(seed.centroid.x) + half,
            float(seed.centroid.y) + half,
        )
        candidate_indices = [int(i) for i in tree.query(box(*query_bounds))]
        candidate_indices = [
            i
            for i in candidate_indices
            if i != seed_idx and not assigned[i]
        ]
        candidate_indices.sort(
            key=lambda i: (
                (float(targets[i].centroid.x) - float(seed.centroid.x)) ** 2
                + (float(targets[i].centroid.y) - float(seed.centroid.y)) ** 2,
                targets[i].source_grid,
                targets[i].source_idx,
            )
        )

        for idx in candidate_indices:
            if len(members) >= max_targets_per_chip:
                break
            new_bounds = _union_bounds([group_bounds, targets[idx].pack_bounds])
            if _bounds_width(new_bounds) <= chip_size_m and _bounds_height(new_bounds) <= chip_size_m:
                members.append(idx)
                group_bounds = new_bounds

        for idx in members:
            assigned[idx] = True

        center_x = (group_bounds[0] + group_bounds[2]) / 2.0
        center_y = (group_bounds[1] + group_bounds[3]) / 2.0
        chip_bounds = (
            center_x - half,
            center_y - half,
            center_x + half,
            center_y + half,
        )
        groups.append(
            ChipGroup(
                chip_id=f"{token}_c{len(groups) + 1:07d}",
                member_indices=tuple(sorted(members, key=lambda i: targets[i].source_idx)),
                center_x=center_x,
                center_y=center_y,
                chip_bounds=chip_bounds,
                pack_bounds=group_bounds,
            )
        )

    return groups


def build_manifest_rows(
    targets: Sequence[Target],
    groups: Sequence[ChipGroup],
    *,
    inventory_path: Path,
    inventory_tag: str,
    chip_size_m: float,
    search_radius_m: float,
    metric_crs: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    to_wgs84 = Transformer.from_crs(metric_crs, DEFAULT_WGS84, always_xy=True)
    group_rows: list[dict[str, Any]] = []
    target_rows: list[dict[str, Any]] = []

    for group in groups:
        members = [targets[i] for i in group.member_indices]
        chip_lon_min, chip_lat_min, chip_lon_max, chip_lat_max = _bbox_to_wgs84(
            to_wgs84, group.chip_bounds
        )
        center_lon, center_lat = to_wgs84.transform(group.center_x, group.center_y)
        source_grids = sorted({m.source_grid for m in members if m.source_grid})
        target_anchor_ids = [m.anchor_id for m in members]
        source_area = sum(m.area_m2 for m in members)
        group_width = _bounds_width(group.pack_bounds)
        group_height = _bounds_height(group.pack_bounds)
        max_offset = max(
            math.hypot(float(m.centroid.x) - group.center_x, float(m.centroid.y) - group.center_y)
            for m in members
        )
        representative_grid = source_grids[0] if source_grids else members[0].grid_id
        group_rows.append(
            {
                "anchor_id": group.chip_id,
                "chip_id": group.chip_id,
                "region_key": members[0].region_key,
                "grid_id": representative_grid,
                "source_annotation_path": str(inventory_path),
                "source_feature_id": ";".join(str(m.source_idx) for m in members[:20]),
                "quality_tier": "",
                "anchor_policy": "inventory_proximity_fixed_chip_group",
                "centroid_lon": f"{center_lon:.10f}",
                "centroid_lat": f"{center_lat:.10f}",
                "source_area_m2": _format_float(source_area),
                "source_width_m": _format_float(group_width),
                "source_height_m": _format_float(group_height),
                "chip_half_m": _format_float(chip_size_m / 2.0, 2),
                "search_radius_m": _format_float(search_radius_m, 2),
                "chip_lon_min": f"{chip_lon_min:.10f}",
                "chip_lat_min": f"{chip_lat_min:.10f}",
                "chip_lon_max": f"{chip_lon_max:.10f}",
                "chip_lat_max": f"{chip_lat_max:.10f}",
                "alignment_note": (
                    "multi-target inventory chip group; per-target offsets and labels are in "
                    "chip_targets.csv; source masks remain current-inventory anchors only"
                ),
                "inventory_tag": inventory_tag,
                "source_inventory_path": str(inventory_path),
                "n_targets": len(members),
                "target_anchor_ids": ";".join(target_anchor_ids),
                "source_grids": ";".join(source_grids),
                "chip_size_m": _format_float(chip_size_m, 2),
                "group_width_m": _format_float(group_width),
                "group_height_m": _format_float(group_height),
                "max_target_offset_m": _format_float(max_offset),
            }
        )

        for target_index, target in enumerate(members, start=1):
            lon, lat = _point_to_wgs84(to_wgs84, target.centroid)
            target_rows.append(
                {
                    "anchor_id": target.anchor_id,
                    "chip_id": group.chip_id,
                    "region_key": target.region_key,
                    "grid_id": target.grid_id,
                    "source_inventory_path": str(inventory_path),
                    "source_feature_id": target.source_idx,
                    "source_grid": target.source_grid,
                    "target_index": target_index,
                    "target_label": f"T{target_index:02d}",
                    "centroid_lon": f"{lon:.10f}",
                    "centroid_lat": f"{lat:.10f}",
                    "source_area_m2": _format_float(target.area_m2),
                    "source_width_m": _format_float(target.width_m),
                    "source_height_m": _format_float(target.height_m),
                    "confidence": _format_any(target.confidence),
                    "score": _format_any(target.score),
                    "sam_score": _format_any(target.sam_score),
                    "n_merged": _format_any(target.n_merged),
                    "target_offset_x_m": _format_float(float(target.centroid.x) - group.center_x),
                    "target_offset_y_m": _format_float(float(target.centroid.y) - group.center_y),
                    "search_radius_m": _format_float(search_radius_m, 2),
                    "chip_size_m": _format_float(chip_size_m, 2),
                    "chip_lon_min": f"{chip_lon_min:.10f}",
                    "chip_lat_min": f"{chip_lat_min:.10f}",
                    "chip_lon_max": f"{chip_lon_max:.10f}",
                    "chip_lat_max": f"{chip_lat_max:.10f}",
                }
            )

    return group_rows, target_rows


def build_summary(
    *,
    inventory_path: Path,
    targets: Sequence[Target],
    groups: Sequence[ChipGroup],
    chip_size_m: float,
    max_targets_per_chip: int,
    pack_margin_m: float,
    search_radius_m: float,
) -> dict[str, Any]:
    counts = [len(g.member_indices) for g in groups]
    counts_series = pd.Series(counts, dtype="int64")
    grid_count = len({t.source_grid for t in targets if t.source_grid})
    overflow_targets = [
        t.anchor_id
        for t in targets
        if _bounds_width(t.pack_bounds) > chip_size_m or _bounds_height(t.pack_bounds) > chip_size_m
    ]
    return {
        "source_inventory_path": str(inventory_path),
        "n_targets": len(targets),
        "n_chip_groups": len(groups),
        "n_source_grids": grid_count,
        "chip_size_m": chip_size_m,
        "pack_margin_m": pack_margin_m,
        "search_radius_m": search_radius_m,
        "max_targets_per_chip": max_targets_per_chip,
        "mean_targets_per_chip": float(counts_series.mean()),
        "median_targets_per_chip": float(counts_series.median()),
        "max_targets_observed": int(counts_series.max()),
        "singleton_chip_groups": int((counts_series == 1).sum()),
        "gemini_request_reduction_factor_vs_per_target": len(targets) / max(len(groups), 1),
        "target_count_histogram": {
            str(int(k)): int(v) for k, v in counts_series.value_counts().sort_index().items()
        },
        "overflow_target_count": len(overflow_targets),
        "overflow_target_examples": overflow_targets[:20],
        "policy": (
            "Fixed-size chip groups are temporal scan units. Each group may contain multiple "
            "current-inventory PV targets; use chip_targets.csv to map group-level imagery "
            "back to source detections."
        ),
    }


def write_outputs(
    *,
    output_dir: Path,
    group_rows: Sequence[Mapping[str, Any]],
    target_rows: Sequence[Mapping[str, Any]],
    summary: Mapping[str, Any],
    groups: Sequence[ChipGroup],
    targets: Sequence[Target],
    metric_crs: str,
    write_gpkg: bool,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    write_csv_rows(output_dir / "chip_groups_as_anchors.csv", group_rows, GROUP_ANCHOR_FIELDS)
    write_csv_rows(output_dir / "chip_targets.csv", target_rows, TARGET_FIELDS)
    (output_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

    readme = f"""# JHB Full382 Temporal Chip Groups

Generated from `{summary['source_inventory_path']}`.

- Targets: {summary['n_targets']}
- Fixed chip groups: {summary['n_chip_groups']}
- Chip size: {summary['chip_size_m']} m
- Request reduction vs per-target scan: {summary['gemini_request_reduction_factor_vs_per_target']:.2f}x

`chip_groups_as_anchors.csv` is compatible with existing group-level GEHI scan
entrypoints because `anchor_id == chip_id` and each row has a chip bbox.
`chip_targets.csv` maps each original inventory polygon to its group and marker
offset for multi-target Gemini review.
"""
    (output_dir / "README.md").write_text(readme, encoding="utf-8")

    if not write_gpkg:
        return

    group_records = []
    for row, group in zip(group_rows, groups, strict=True):
        rec = dict(row)
        rec["geometry"] = box(*group.chip_bounds)
        group_records.append(rec)
    group_gdf = gpd.GeoDataFrame(group_records, geometry="geometry", crs=metric_crs)
    group_gdf.to_file(output_dir / "chip_groups.gpkg", driver="GPKG", layer="chip_groups")

    target_records = []
    target_lookup = {target.anchor_id: target for target in targets}
    for row in target_rows:
        target = target_lookup[str(row["anchor_id"])]
        rec = dict(row)
        rec["geometry"] = target.centroid
        target_records.append(rec)
    target_gdf = gpd.GeoDataFrame(target_records, geometry="geometry", crs=metric_crs)
    target_gdf.to_file(output_dir / "chip_targets.gpkg", driver="GPKG", layer="chip_targets")


def main() -> None:
    args = parse_args()
    if args.chip_size_m <= 0:
        raise SystemExit("--chip-size-m must be positive")
    if args.pack_margin_m < 0:
        raise SystemExit("--pack-margin-m must be >= 0")
    if args.max_targets_per_chip <= 0:
        raise SystemExit("--max-targets-per-chip must be positive")
    if args.hard_max_targets_per_chip <= 0:
        raise SystemExit("--hard-max-targets-per-chip must be positive")
    if args.max_targets_per_chip > args.hard_max_targets_per_chip:
        raise SystemExit("--max-targets-per-chip must be <= --hard-max-targets-per-chip")

    inventory = args.inventory.resolve()
    gdf = _load_inventory(
        inventory,
        layer=args.layer,
        metric_crs=args.metric_crs,
        min_confidence=args.min_confidence,
    )
    targets = make_targets(
        gdf,
        region_key=args.region_key,
        inventory_tag=args.inventory_tag,
        pack_margin_m=args.pack_margin_m,
    )
    groups = build_chip_groups(
        targets,
        chip_size_m=args.chip_size_m,
        max_targets_per_chip=args.max_targets_per_chip,
        inventory_tag=args.inventory_tag,
    )
    group_rows, target_rows = build_manifest_rows(
        targets,
        groups,
        inventory_path=inventory,
        inventory_tag=args.inventory_tag,
        chip_size_m=args.chip_size_m,
        search_radius_m=args.search_radius_m,
        metric_crs=args.metric_crs,
    )
    summary = build_summary(
        inventory_path=inventory,
        targets=targets,
        groups=groups,
        chip_size_m=args.chip_size_m,
        max_targets_per_chip=args.max_targets_per_chip,
        pack_margin_m=args.pack_margin_m,
        search_radius_m=args.search_radius_m,
    )
    write_outputs(
        output_dir=args.output_dir,
        group_rows=group_rows,
        target_rows=target_rows,
        summary=summary,
        groups=groups,
        targets=targets,
        metric_crs=args.metric_crs,
        write_gpkg=not args.no_gpkg,
    )

    print(
        "Wrote "
        f"{summary['n_targets']} targets into {summary['n_chip_groups']} chip groups "
        f"({summary['gemini_request_reduction_factor_vs_per_target']:.2f}x fewer group-level scans) "
        f"-> {args.output_dir}"
    )
    print(
        "Primary handoff: "
        f"{args.output_dir / 'chip_groups_as_anchors.csv'} + {args.output_dir / 'chip_targets.csv'}"
    )


if __name__ == "__main__":
    main()
