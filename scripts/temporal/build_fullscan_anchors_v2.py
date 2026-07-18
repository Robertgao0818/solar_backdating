#!/usr/bin/env python3
"""Build the clean, versioned full-population anchors-v2 table (ISSUE-27).

The builder accepts the three frozen RUN-2 inputs, but crosses the legacy
chip-group -> per-target schema boundary only through ``anchor_derivation``.
Every output column is explicitly owned here; unknown input columns fail.
``source_grids`` is recomputed from the per-target 96 m bbox using either the
authoritative JNB task-grid polygons or the documented centroid-lattice
fallback. The v1 run directory is immutable and cannot be selected as output.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
import shutil
import sys
from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from pyproj import Transformer

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.temporal.anchor_derivation import derive_per_target_anchor_row
from scripts.temporal.chip_geometry import FULLSCAN_GEOMETRY_VERSION_BY_ARM
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
DEFAULT_V1_DIR = (
    Path.home()
    / "zasolar_data/geid_temporal/fullscan_gemini_backdating_2026-07"
)
DEFAULT_V1_ANCHORS = DEFAULT_V1_DIR / "anchors_all.csv"
DEFAULT_OUTPUT_DIR = DEFAULT_V1_DIR / "anchors_v2"
DEFAULT_CAPTURE_CSV = (
    PROJECT_ROOT.parent
    / "ZAsolar/data/analysis/vexcel_jhb_per_grid_capture_dates_2026-06-04.csv"
)
DEFAULT_AUTHORITATIVE_GRID = PROJECT_ROOT.parent / "ZAsolar/data/jhb_task_grid_unified.gpkg"
DEFAULT_METRIC_CRS = "EPSG:32735"
DEFAULT_EXPECTED_COUNT = 41_393
DEFAULT_EXPECTED_ARMS = {"A24": 36_322, "A48": 5_071}
EXPECTED_CHANGED_GRID_SETS_FALLBACK = 5_414
EXPECTED_CHANGED_CENSUS_DATES = 223
EXPECTED_CENSUS_DELTA_HISTOGRAM = {-58: 1, 4: 3, 58: 146, 62: 73}

BBOX_INPUT_FIELDS = (
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
)
TARGET_INPUT_FIELDS = (
    "anchor_id",
    "chip_id",
    "region_key",
    "grid_id",
    "source_inventory_path",
    "source_feature_id",
    "source_fid",
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
)
GROUP_INPUT_FIELDS = (
    "anchor_id",
    "chip_id",
    "region_key",
    "grid_id",
    "source_annotation_path",
    "source_feature_id",
    "source_fid",
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
)
CAPTURE_INPUT_FIELDS = (
    "grid_id",
    "centroid_lon",
    "centroid_lat",
    "first_capture_date",
    "estimate_date",
    "last_capture_date",
    "flight_bucket",
    "coverage_status",
)

# Safe per-target carries. Each field's meaning is target-local and does not
# change when the surrounding chip moves from the legacy group centre.
SAFE_TARGET_CARRY_FIELDS = {
    "grid_id": "inventory assignment of the target centroid",
    "source_feature_id": "stable target inventory crosswalk",
    "source_fid": "stable source feature identifier",
    "source_area_m2": "target footprint area",
    "source_width_m": "target footprint width",
    "source_height_m": "target footprint height",
    "confidence": "target detection confidence",
    "score": "target detection score",
    "sam_score": "target segmentation score",
    "n_merged": "target feature merge count",
}

OUTPUT_FIELDS = (
    "anchor_id",
    "chip_id",
    "legacy_group_anchor_id",
    "region_key",
    "grid_id",
    "source_feature_id",
    "source_fid",
    "source_grid",
    "centroid_lon",
    "centroid_lat",
    "source_area_m2",
    "source_width_m",
    "source_height_m",
    "confidence",
    "score",
    "sam_score",
    "n_merged",
    "target_index",
    "target_label",
    "target_offset_x_m",
    "target_offset_y_m",
    "search_radius_m",
    "chip_half_m",
    "chip_size_m",
    "chip_lon_min",
    "chip_lat_min",
    "chip_lon_max",
    "chip_lat_max",
    "source_grids",
    "chip_arm",
    "review_extent_m",
    "chip_arm_rule",
    "chip_arm_amendment",
    "geometry_version",
    "legacy_source_inventory_path",
)

GridResolver = Callable[[Mapping[str, object]], tuple[set[str], tuple[str, ...]]]


@dataclass(frozen=True)
class GridSource:
    resolver: GridResolver
    valid_grid_ids: frozenset[str]
    metadata: dict[str, object]


def _read_csv_strict(
    path: Path, expected_fields: Iterable[str], *, label: str
) -> list[dict[str, str]]:
    expected = tuple(expected_fields)
    with path.open("r", newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        actual = tuple(reader.fieldnames or ())
        missing = sorted(set(expected) - set(actual))
        unknown = sorted(set(actual) - set(expected))
        if missing or unknown:
            raise ValueError(
                f"{label} schema mismatch at {path}: "
                f"missing={missing} unknown={unknown}"
            )
        return [dict(row) for row in reader]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _require_unique(rows: list[Mapping[str, object]], field: str, *, label: str) -> None:
    values = [str(row.get(field) or "") for row in rows]
    if any(not value for value in values):
        raise ValueError(f"{label} contains blank {field}")
    duplicates = [value for value, n in Counter(values).items() if n > 1]
    if duplicates:
        raise ValueError(f"duplicate {field} in {label}: {duplicates[:3]}")


def _metric_bbox(
    row: Mapping[str, object], to_metric: Transformer
) -> tuple[float, float, float, float]:
    try:
        xmin, ymin = to_metric.transform(
            float(row["chip_lon_min"]), float(row["chip_lat_min"])
        )
        xmax, ymax = to_metric.transform(
            float(row["chip_lon_max"]), float(row["chip_lat_max"])
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(
            f"invalid bbox for {row.get('anchor_id', '<unknown>')}"
        ) from exc
    return float(xmin), float(ymin), float(xmax), float(ymax)


def build_lattice_grid_source(
    capture_rows: list[Mapping[str, object]],
    *,
    metric_crs: str = DEFAULT_METRIC_CRS,
) -> GridSource:
    """Build the documented provisional 1 km lattice from 382 centroids."""
    _require_unique(capture_rows, "grid_id", label="capture-date CSV")
    to_metric = Transformer.from_crs("EPSG:4326", metric_crs, always_xy=True)
    cells: dict[tuple[int, int], str] = {}
    residuals: dict[str, float] = {}
    collisions: set[tuple[int, int]] = set()
    for row in capture_rows:
        grid_id = str(row["grid_id"])
        x, y = to_metric.transform(
            float(row["centroid_lon"]), float(row["centroid_lat"])
        )
        col = math.floor(x / 1000.0)
        lattice_row = math.floor(y / 1000.0)
        key = (col, lattice_row)
        if key in cells:
            collisions.add(key)
        cells[key] = grid_id
        residuals[grid_id] = math.hypot(
            x - (col * 1000.0 + 500.0),
            y - (lattice_row * 1000.0 + 500.0),
        )
    for key in collisions:
        cells.pop(key, None)

    def resolve(anchor: Mapping[str, object]) -> tuple[set[str], tuple[str, ...]]:
        xmin, ymin, xmax, ymax = _metric_bbox(anchor, to_metric)
        grid_ids: set[str] = set()
        unresolved: list[str] = []
        for col in range(math.floor(xmin / 1000.0), math.floor(xmax / 1000.0) + 1):
            for lattice_row in range(
                math.floor(ymin / 1000.0), math.floor(ymax / 1000.0) + 1
            ):
                key = (col, lattice_row)
                grid_id = cells.get(key)
                if grid_id is None:
                    unresolved.append(f"{col},{lattice_row}")
                else:
                    grid_ids.add(grid_id)
        return grid_ids, tuple(sorted(unresolved))

    approximate = sorted(grid_id for grid_id, value in residuals.items() if value > 1.0)
    severe = sorted(grid_id for grid_id, value in residuals.items() if value > 200.0)
    return GridSource(
        resolver=resolve,
        valid_grid_ids=frozenset(str(row["grid_id"]) for row in capture_rows),
        metadata={
            "method": "centroid_lattice_fallback_v2",
            "provisional": True,
            "metric_crs": metric_crs,
            "cell_size_m": 1000.0,
            "n_grid_ids": len(capture_rows),
            "n_resolvable_cells": len(cells),
            "collision_cells": [f"{c},{r}" for c, r in sorted(collisions)],
            "approximate_grid_ids_residual_gt_1m": approximate,
            "severe_approximate_grid_ids_residual_gt_200m": severe,
            "max_centroid_residual_m": max(residuals.values(), default=0.0),
        },
    )


def build_polygon_grid_source(
    path: Path,
    *,
    metric_crs: str = DEFAULT_METRIC_CRS,
) -> GridSource:
    """Load the authoritative 382-cell JNB polygon grid."""
    import geopandas as gpd
    from shapely.geometry import Point, box

    grids = gpd.read_file(path)
    candidate_columns = [
        column
        for column in ("gridcell_id", "grid_id", "id")
        if column in grids.columns
    ]
    if len(candidate_columns) != 1:
        raise ValueError(
            f"authoritative grid must expose exactly one id column from "
            f"gridcell_id/grid_id/id; found={candidate_columns}"
        )
    id_column = candidate_columns[0]
    ids = grids[id_column].astype(str)
    active = grids.loc[ids.str.fullmatch(r"JNB\d{4}")].copy()
    active["_grid_id"] = active[id_column].astype(str)
    if len(active) != 382 or active["_grid_id"].nunique() != 382:
        raise ValueError(
            f"authoritative grid must contain exactly 382 unique JNB cells; "
            f"rows={len(active)} unique={active['_grid_id'].nunique()}"
        )
    if active.crs is None:
        raise ValueError(f"authoritative grid has no CRS: {path}")
    active = active.to_crs(metric_crs).reset_index(drop=True)
    spatial_index = active.sindex
    to_metric = Transformer.from_crs("EPSG:4326", metric_crs, always_xy=True)

    def resolve(anchor: Mapping[str, object]) -> tuple[set[str], tuple[str, ...]]:
        xmin, ymin, xmax, ymax = _metric_bbox(anchor, to_metric)
        bbox = box(xmin, ymin, xmax, ymax)
        candidates = active.iloc[list(spatial_index.query(bbox, predicate="intersects"))]
        touched = {
            str(grid_id)
            for grid_id, geometry in candidates[["_grid_id", "geometry"]].itertuples(
                index=False, name=None
            )
            if geometry.intersection(bbox).area > 1e-6
        }
        cx, cy = to_metric.transform(
            float(anchor["centroid_lon"]), float(anchor["centroid_lat"])
        )
        point = Point(cx, cy)
        covering = {
            str(grid_id)
            for grid_id, geometry in candidates[["_grid_id", "geometry"]].itertuples(
                index=False, name=None
            )
            if geometry.covers(point)
        }
        if len(covering) != 1:
            raise ValueError(
                f"expected one centroid grid for {anchor.get('anchor_id')}, "
                f"found={sorted(covering)}"
            )
        touched.update(covering)
        return touched, ()

    return GridSource(
        resolver=resolve,
        valid_grid_ids=frozenset(active["_grid_id"].astype(str)),
        metadata={
            "method": "authoritative_grid_polygons_v1",
            "provisional": False,
            "metric_crs": metric_crs,
            "id_column": id_column,
            "n_grid_ids": len(active),
        },
    )


def _source_grid_set(row: Mapping[str, object]) -> set[str]:
    raw = str(row.get("source_grids") or "").strip()
    if not raw:
        raw = str(row.get("grid_id") or "").strip()
    return {value.strip() for value in raw.split(";") if value.strip()}


def build_rows(
    bbox_rows: list[dict[str, str]],
    target_rows: list[dict[str, str]],
    group_rows: list[dict[str, str]],
    *,
    grid_source: GridSource,
    chip_size_m: float = 96.0,
    metric_crs: str = DEFAULT_METRIC_CRS,
) -> tuple[list[dict[str, object]], dict[str, object]]:
    """Build allowlisted v2 rows and return join/grid diagnostics."""
    _require_unique(bbox_rows, "anchor_id", label="per-target bbox CSV")
    _require_unique(target_rows, "anchor_id", label="chip_targets CSV")
    _require_unique(group_rows, "anchor_id", label="group anchors CSV")
    bbox_by_anchor = {row["anchor_id"]: row for row in bbox_rows}
    groups_by_anchor = {row["anchor_id"]: row for row in group_rows}

    out: list[dict[str, object]] = []
    unmatched_bbox = set(bbox_by_anchor)
    unresolved_anchor_ids: list[str] = []
    unresolved_cells: Counter[str] = Counter()
    max_frozen_bbox_error_m = 0.0
    to_metric = Transformer.from_crs("EPSG:4326", metric_crs, always_xy=True)

    for source in target_rows:
        anchor_id = source["anchor_id"]
        frozen_bbox = bbox_by_anchor.get(anchor_id)
        if frozen_bbox is None:
            raise ValueError(f"target {anchor_id} missing frozen per-target bbox")
        unmatched_bbox.discard(anchor_id)
        legacy_group_id = source["chip_id"]
        group = groups_by_anchor.get(legacy_group_id)
        if group is None:
            raise ValueError(
                f"target {anchor_id} references missing legacy group {legacy_group_id}"
            )
        if frozen_bbox["chip_id"] != legacy_group_id:
            raise ValueError(
                f"bbox/target legacy chip mismatch for {anchor_id}: "
                f"bbox={frozen_bbox['chip_id']} target={legacy_group_id}"
            )

        derived = derive_per_target_anchor_row(
            source, chip_size_m=chip_size_m, metric_crs=metric_crs
        )
        derived_bbox = _metric_bbox(derived, to_metric)
        frozen_metric_bbox = _metric_bbox(frozen_bbox, to_metric)
        bbox_error = max(
            abs(left - right)
            for left, right in zip(derived_bbox, frozen_metric_bbox, strict=True)
        )
        max_frozen_bbox_error_m = max(max_frozen_bbox_error_m, bbox_error)
        if bbox_error >= 0.01:
            raise ValueError(
                f"derived bbox disagrees with frozen bbox for {anchor_id}: "
                f"max_error={bbox_error:.6f}m"
            )

        source_grids, unresolved = grid_source.resolver(derived)
        if unresolved:
            unresolved_anchor_ids.append(anchor_id)
            unresolved_cells.update(unresolved)
        centroid_grid = str(source["grid_id"])
        if centroid_grid not in source_grids:
            raise ValueError(
                f"recomputed source_grids omits centroid grid for {anchor_id}: "
                f"centroid={centroid_grid} touched={sorted(source_grids)}"
            )
        if not source_grids:
            raise ValueError(f"recomputed source_grids is empty for {anchor_id}")
        unknown_grids = source_grids - grid_source.valid_grid_ids
        if unknown_grids:
            raise ValueError(
                f"recomputed unknown grids for {anchor_id}: {sorted(unknown_grids)}"
            )

        try:
            area = float(source["source_area_m2"])
            width = float(source["source_width_m"])
            height = float(source["source_height_m"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"bad target footprint fields for {anchor_id}") from exc
        if not (area > 0 and width > 0 and height > 0):
            raise ValueError(f"non-positive target footprint for {anchor_id}")
        arm = route_chip_arm(area)

        row: dict[str, object] = {
            field: derived[field]
            for field in (
                "anchor_id",
                "chip_id",
                "legacy_group_anchor_id",
                "region_key",
                "source_grid",
                "centroid_lon",
                "centroid_lat",
                "target_index",
                "target_label",
                "target_offset_x_m",
                "target_offset_y_m",
                "search_radius_m",
                "chip_half_m",
                "chip_size_m",
                "chip_lon_min",
                "chip_lat_min",
                "chip_lon_max",
                "chip_lat_max",
            )
        }
        row.update({field: source[field] for field in SAFE_TARGET_CARRY_FIELDS})
        row.update(
            {
                "source_grids": ";".join(sorted(source_grids)),
                "chip_arm": arm,
                "review_extent_m": arm.removeprefix("A"),
                "chip_arm_rule": (
                    f"source_area_m2>={ROUTE_AREA_CUT_M2:g}->A48 else A24"
                ),
                "chip_arm_amendment": ROUTED_AMENDMENT_ID,
                "geometry_version": FULLSCAN_GEOMETRY_VERSION_BY_ARM[arm],
                "legacy_source_inventory_path": source["source_inventory_path"],
            }
        )
        if set(row) != set(OUTPUT_FIELDS):
            raise AssertionError(
                f"internal output allowlist drift: missing={sorted(set(OUTPUT_FIELDS)-set(row))} "
                f"extra={sorted(set(row)-set(OUTPUT_FIELDS))}"
            )
        out.append({field: row[field] for field in OUTPUT_FIELDS})

    if unmatched_bbox:
        raise ValueError(
            f"{len(unmatched_bbox)} frozen bbox rows unmatched in targets; "
            f"first={sorted(unmatched_bbox)[:3]}"
        )
    out.sort(key=lambda row: str(row["anchor_id"]))
    return out, {
        "max_frozen_bbox_coordinate_error_m": max_frozen_bbox_error_m,
        "unresolved_anchor_count": len(unresolved_anchor_ids),
        "unresolved_anchor_ids_sample": unresolved_anchor_ids[:20],
        "unresolved_lattice_cells": dict(sorted(unresolved_cells.items())),
    }


def validate_invariants(
    rows: list[Mapping[str, object]],
    *,
    expected_count: int,
    expected_arms: Mapping[str, int] | None,
    metric_crs: str = DEFAULT_METRIC_CRS,
) -> dict[str, object]:
    if len(rows) != expected_count:
        raise ValueError(f"expected {expected_count} anchors, built {len(rows)}")
    ids = [str(row["anchor_id"]) for row in rows]
    if len(set(ids)) != len(ids):
        raise ValueError("anchors-v2 contains duplicate anchor_id")

    to_metric = Transformer.from_crs("EPSG:4326", metric_crs, always_xy=True)
    max_side_error = 0.0
    max_square_error = 0.0
    max_center_error = 0.0
    max_offset_norm = 0.0
    empty_grids = 0
    centroid_grid_misses = 0
    for row in rows:
        xmin, ymin, xmax, ymax = _metric_bbox(row, to_metric)
        width = xmax - xmin
        height = ymax - ymin
        side_error = max(abs(width - 96.0), abs(height - 96.0))
        square_error = abs(width - height)
        max_side_error = max(max_side_error, side_error)
        max_square_error = max(max_square_error, square_error)
        cx, cy = to_metric.transform(
            float(row["centroid_lon"]), float(row["centroid_lat"])
        )
        center_error = math.hypot(cx - (xmin + xmax) / 2.0, cy - (ymin + ymax) / 2.0)
        max_center_error = max(max_center_error, center_error)
        offset_norm = math.hypot(
            float(row["target_offset_x_m"]), float(row["target_offset_y_m"])
        )
        max_offset_norm = max(max_offset_norm, offset_norm)
        grids = _source_grid_set(row)
        if not grids:
            empty_grids += 1
        if str(row["grid_id"]) not in grids:
            centroid_grid_misses += 1

    if max_side_error > 0.01 or max_square_error > 0.01:
        raise ValueError(
            f"bbox side invariant failed: side_error={max_side_error:.6f}m "
            f"square_error={max_square_error:.6f}m"
        )
    if max_center_error >= 0.01:
        raise ValueError(
            f"bbox center invariant failed: max_error={max_center_error:.6f}m"
        )
    if max_offset_norm >= 0.01:
        raise ValueError(
            f"target offset invariant failed: max_norm={max_offset_norm:.6f}m"
        )
    if empty_grids or centroid_grid_misses:
        raise ValueError(
            f"source_grids invariant failed: empty={empty_grids} "
            f"centroid_grid_misses={centroid_grid_misses}"
        )

    arm_counts = dict(sorted(Counter(str(row["chip_arm"]) for row in rows).items()))
    if expected_arms is not None and arm_counts != dict(expected_arms):
        raise ValueError(
            f"arm split differs from expected: actual={arm_counts} expected={dict(expected_arms)}"
        )
    return {
        "row_count": len(rows),
        "unique_anchor_ids": len(set(ids)),
        "max_bbox_side_error_m": max_side_error,
        "max_bbox_square_error_m": max_square_error,
        "max_bbox_center_error_m": max_center_error,
        "max_target_offset_norm_m": max_offset_norm,
        "empty_source_grids": empty_grids,
        "centroid_grid_misses": centroid_grid_misses,
        "arm_counts": arm_counts,
        "passed": True,
    }


def _capture_dates(capture_rows: Iterable[Mapping[str, object]]) -> dict[str, date]:
    out: dict[str, date] = {}
    for row in capture_rows:
        text = str(row.get("last_capture_date") or "")[:10]
        if text:
            out[str(row["grid_id"])] = date.fromisoformat(text)
    return out


def reconcile_against_v1(
    rows: list[Mapping[str, object]],
    v1_rows: list[Mapping[str, object]],
    *,
    capture_dates: Mapping[str, date],
    expect_fallback_counts: bool,
    enforce_expectations: bool = True,
) -> dict[str, object]:
    _require_unique(v1_rows, "anchor_id", label="v1 anchors")
    v1_by_id = {str(row["anchor_id"]): row for row in v1_rows}
    if set(v1_by_id) != {str(row["anchor_id"]) for row in rows}:
        raise ValueError("v1/v2 anchor_id populations differ")

    changed_grid_sets = 0
    changed_census_dates = 0
    delta_histogram: Counter[int] = Counter()
    unresolved_census = 0
    for row in rows:
        old = v1_by_id[str(row["anchor_id"])]
        new_grids = _source_grid_set(row)
        old_grids = _source_grid_set(old)
        changed_grid_sets += new_grids != old_grids
        new_dates = [capture_dates[g] for g in new_grids if g in capture_dates]
        old_dates = [capture_dates[g] for g in old_grids if g in capture_dates]
        if not new_dates or not old_dates:
            unresolved_census += 1
            continue
        delta = (max(new_dates) - max(old_dates)).days
        if delta:
            changed_census_dates += 1
            delta_histogram[delta] += 1

    actual_histogram = dict(sorted(delta_histogram.items()))
    if enforce_expectations:
        if expect_fallback_counts and changed_grid_sets != EXPECTED_CHANGED_GRID_SETS_FALLBACK:
            raise ValueError(
                f"fallback changed-grid reconciliation mismatch: actual={changed_grid_sets} "
                f"expected={EXPECTED_CHANGED_GRID_SETS_FALLBACK}"
            )
        if changed_census_dates != EXPECTED_CHANGED_CENSUS_DATES:
            raise ValueError(
                f"census-date reconciliation mismatch: actual={changed_census_dates} "
                f"expected={EXPECTED_CHANGED_CENSUS_DATES}"
            )
        if actual_histogram != EXPECTED_CENSUS_DELTA_HISTOGRAM:
            raise ValueError(
                f"census-date delta histogram mismatch: actual={actual_histogram} "
                f"expected={EXPECTED_CENSUS_DELTA_HISTOGRAM}"
            )
    return {
        "changed_source_grid_sets": changed_grid_sets,
        "changed_census_dates": changed_census_dates,
        "census_delta_days_histogram": {
            str(key): value for key, value in actual_histogram.items()
        },
        "unresolved_census_comparisons": unresolved_census,
        "expected": {
            "fallback_changed_source_grid_sets": EXPECTED_CHANGED_GRID_SETS_FALLBACK,
            "changed_census_dates": EXPECTED_CHANGED_CENSUS_DATES,
            "census_delta_days_histogram": {
                str(key): value
                for key, value in EXPECTED_CENSUS_DELTA_HISTOGRAM.items()
            },
        },
        "passed": True,
    }


def _write_csv(path: Path, rows: list[Mapping[str, object]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(OUTPUT_FIELDS), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _safe_output_paths(output_dir: Path, v1_dir: Path) -> tuple[Path, Path]:
    output = output_dir.expanduser().resolve()
    immutable_v1 = v1_dir.expanduser().resolve()
    if output == immutable_v1:
        raise ValueError(f"refusing to overwrite immutable v1 run directory: {output}")
    if output.exists():
        raise ValueError(f"output directory already exists; refusing overwrite: {output}")
    temp = output.with_name(f".{output.name}.building")
    if temp.exists():
        raise ValueError(f"stale temporary build directory exists: {temp}")
    return output, temp


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bbox-csv", type=Path, default=DEFAULT_BBOX_CSV)
    parser.add_argument("--targets-csv", type=Path, default=DEFAULT_TARGETS_CSV)
    parser.add_argument("--groups-csv", type=Path, default=DEFAULT_GROUPS_CSV)
    parser.add_argument("--v1-anchors-csv", type=Path, default=DEFAULT_V1_ANCHORS)
    parser.add_argument("--vexcel-capture-csv", type=Path, default=DEFAULT_CAPTURE_CSV)
    parser.add_argument("--grid-gpkg", type=Path, default=DEFAULT_AUTHORITATIVE_GRID)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--expected-count", type=int, default=DEFAULT_EXPECTED_COUNT)
    parser.add_argument("--metric-crs", default=DEFAULT_METRIC_CRS)
    args = parser.parse_args()

    output_dir, temp_dir = _safe_output_paths(args.output_dir, DEFAULT_V1_DIR)
    bbox_rows = _read_csv_strict(
        args.bbox_csv, BBOX_INPUT_FIELDS, label="per-target bbox CSV"
    )
    target_rows = _read_csv_strict(
        args.targets_csv, TARGET_INPUT_FIELDS, label="chip_targets CSV"
    )
    group_rows = _read_csv_strict(
        args.groups_csv, GROUP_INPUT_FIELDS, label="group anchors CSV"
    )
    capture_rows = _read_csv_strict(
        args.vexcel_capture_csv, CAPTURE_INPUT_FIELDS, label="Vexcel capture CSV"
    )
    with args.v1_anchors_csv.open("r", newline="", encoding="utf-8") as fh:
        v1_rows = [dict(row) for row in csv.DictReader(fh)]

    if args.grid_gpkg.exists():
        grid_source = build_polygon_grid_source(
            args.grid_gpkg, metric_crs=args.metric_crs
        )
        grid_input_path: Path | None = args.grid_gpkg
    else:
        grid_source = build_lattice_grid_source(
            capture_rows, metric_crs=args.metric_crs
        )
        grid_input_path = None

    rows, join_grid_stats = build_rows(
        bbox_rows,
        target_rows,
        group_rows,
        grid_source=grid_source,
        metric_crs=args.metric_crs,
    )
    expected_arms = DEFAULT_EXPECTED_ARMS if args.expected_count == DEFAULT_EXPECTED_COUNT else None
    invariants = validate_invariants(
        rows,
        expected_count=args.expected_count,
        expected_arms=expected_arms,
        metric_crs=args.metric_crs,
    )
    reconciliation = reconcile_against_v1(
        rows,
        v1_rows,
        capture_dates=_capture_dates(capture_rows),
        expect_fallback_counts=bool(grid_source.metadata["provisional"]),
    )

    temp_dir.mkdir(parents=True)
    try:
        all_path = temp_dir / "anchors_all.csv"
        _write_csv(all_path, rows)
        arm_counts = split_anchors_by_chip_arm(all_path, temp_dir)
        if arm_counts != invariants["arm_counts"]:
            raise AssertionError(
                f"split output counts differ from invariant counts: "
                f"split={arm_counts} invariant={invariants['arm_counts']}"
            )
        input_specs: dict[str, dict[str, object]] = {
            "bbox_csv": {"path": str(args.bbox_csv), "sha256": _sha256(args.bbox_csv)},
            "targets_csv": {
                "path": str(args.targets_csv),
                "sha256": _sha256(args.targets_csv),
            },
            "groups_csv": {
                "path": str(args.groups_csv),
                "sha256": _sha256(args.groups_csv),
            },
            "v1_anchors_csv": {
                "path": str(args.v1_anchors_csv),
                "sha256": _sha256(args.v1_anchors_csv),
            },
            "vexcel_capture_csv": {
                "path": str(args.vexcel_capture_csv),
                "sha256": _sha256(args.vexcel_capture_csv),
            },
        }
        if grid_input_path is not None:
            input_specs["grid_gpkg"] = {
                "path": str(grid_input_path),
                "sha256": _sha256(grid_input_path),
            }
        summary: dict[str, Any] = {
            "schema_version": "anchors_v2_issue27",
            "geometry_versions": dict(FULLSCAN_GEOMETRY_VERSION_BY_ARM),
            "inputs": input_specs,
            "grid_geometry": grid_source.metadata,
            "join_and_grid_diagnostics": join_grid_stats,
            "invariants": invariants,
            "reconciliation_vs_v1": reconciliation,
            "route": {
                "cut_m2": ROUTE_AREA_CUT_M2,
                "amendment": ROUTED_AMENDMENT_ID,
            },
            "outputs": {},
        }
        for name in ("anchors_all.csv", "anchors_A24.csv", "anchors_A48.csv"):
            path = temp_dir / name
            summary["outputs"][name] = {
                "path": str(output_dir / name),
                "sha256": _sha256(path),
            }
        summary_path = temp_dir / "anchors_summary.json"
        summary_path.write_text(
            json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        output_dir.parent.mkdir(parents=True, exist_ok=True)
        temp_dir.rename(output_dir)
    except Exception:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise

    print(
        f"anchors={len(rows)} arms={invariants['arm_counts']} "
        f"grid_method={grid_source.metadata['method']} -> {output_dir}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
