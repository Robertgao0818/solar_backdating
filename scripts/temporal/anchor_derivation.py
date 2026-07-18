"""Canonical per-target anchor-row derivation for ISSUE-25 and later builds.

A per-target 96 m chip is centred on the target centroid, not on its legacy
multi-target chip group. This module is the sole code path that crosses that
geometry boundary: identifiers, offsets, labels, and bbox fields are all
re-derived together so stale group-frame columns cannot survive unnoticed.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from pyproj import Transformer

DEFAULT_METRIC_CRS = "EPSG:32735"
DEFAULT_WGS84_CRS = "EPSG:4326"
OFFSET_TOLERANCE_M = 0.01


def _derive_per_target_anchor_row(
    source_row: Mapping[str, object],
    *,
    chip_size_m: float,
    offset_tolerance_m: float,
    to_metric: Transformer,
    to_wgs84: Transformer,
) -> dict[str, object]:
    row = dict(source_row)
    target_id = str(row.get("anchor_id") or row.get("target_anchor_id") or "")
    if not target_id:
        raise ValueError("target row missing anchor_id/target_anchor_id")

    try:
        lon = float(row["centroid_lon"])
        lat = float(row["centroid_lat"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"target row {target_id!r} has invalid centroid") from exc

    half_m = float(chip_size_m) / 2.0
    center_x, center_y = to_metric.transform(lon, lat)
    lon_min, lat_min = to_wgs84.transform(center_x - half_m, center_y - half_m)
    lon_max, lat_max = to_wgs84.transform(center_x + half_m, center_y + half_m)

    bbox_min_x, bbox_min_y = to_metric.transform(lon_min, lat_min)
    bbox_max_x, bbox_max_y = to_metric.transform(lon_max, lat_max)
    bbox_center_x = (bbox_min_x + bbox_max_x) / 2.0
    bbox_center_y = (bbox_min_y + bbox_max_y) / 2.0
    offset_x_m = center_x - bbox_center_x
    offset_y_m = center_y - bbox_center_y
    offset_norm_m = (offset_x_m**2 + offset_y_m**2) ** 0.5
    if offset_norm_m >= offset_tolerance_m:
        raise ValueError(
            f"per-target bbox is not centroid-centred for {target_id}: "
            f"offset={offset_norm_m:.6f}m tolerance={offset_tolerance_m:.6f}m"
        )

    legacy_chip_id = str(row.get("chip_id") or row.get("group_anchor_id") or "")
    row.update(
        {
            "anchor_id": target_id,
            "chip_id": target_id,
            "legacy_group_anchor_id": legacy_chip_id,
            "region_key": str(row.get("region_key") or "johannesburg"),
            "source_grid": str(row.get("source_grid") or row.get("grid_id") or ""),
            "target_index": 1,
            "target_label": "T01",
            "centroid_lon": lon,
            "centroid_lat": lat,
            # Persist exact zero after proving the recomputed geometric offset is
            # below tolerance. This preserves the frozen ISSUE-25 CSV bytes.
            "target_offset_x_m": 0.0,
            "target_offset_y_m": 0.0,
            "search_radius_m": float(row.get("search_radius_m") or 10.0),
            "chip_half_m": half_m,
            "chip_size_m": float(chip_size_m),
            "chip_lon_min": lon_min,
            "chip_lat_min": lat_min,
            "chip_lon_max": lon_max,
            "chip_lat_max": lat_max,
        }
    )
    return row


def derive_per_target_anchor_row(
    source_row: Mapping[str, object],
    *,
    chip_size_m: float = 96.0,
    metric_crs: str = DEFAULT_METRIC_CRS,
    offset_tolerance_m: float = OFFSET_TOLERANCE_M,
) -> dict[str, object]:
    """Return one canonical target-centred anchor row.

    Input metadata is retained for compatibility with the frozen ISSUE-25
    manifest builder, but every geometry-sensitive field is overwritten. New
    production builders must still select their output through an explicit
    allowlist rather than serializing this mapping wholesale.
    """
    if chip_size_m < 96.0:
        raise ValueError("per-target source rasters must be at least 96 m")
    if offset_tolerance_m <= 0:
        raise ValueError("offset_tolerance_m must be positive")
    to_metric = Transformer.from_crs(
        DEFAULT_WGS84_CRS, metric_crs, always_xy=True
    )
    to_wgs84 = Transformer.from_crs(
        metric_crs, DEFAULT_WGS84_CRS, always_xy=True
    )
    return _derive_per_target_anchor_row(
        source_row,
        chip_size_m=chip_size_m,
        offset_tolerance_m=offset_tolerance_m,
        to_metric=to_metric,
        to_wgs84=to_wgs84,
    )


def derive_per_target_anchor_rows(
    rows: Iterable[Mapping[str, object]],
    *,
    chip_size_m: float = 96.0,
    metric_crs: str = DEFAULT_METRIC_CRS,
    offset_tolerance_m: float = OFFSET_TOLERANCE_M,
) -> list[dict[str, object]]:
    """Derive and anchor-id sort a collection of canonical target rows."""
    if chip_size_m < 96.0:
        raise ValueError("per-target source rasters must be at least 96 m")
    if offset_tolerance_m <= 0:
        raise ValueError("offset_tolerance_m must be positive")
    to_metric = Transformer.from_crs(
        DEFAULT_WGS84_CRS, metric_crs, always_xy=True
    )
    to_wgs84 = Transformer.from_crs(
        metric_crs, DEFAULT_WGS84_CRS, always_xy=True
    )
    out = [
        _derive_per_target_anchor_row(
            row,
            chip_size_m=chip_size_m,
            offset_tolerance_m=offset_tolerance_m,
            to_metric=to_metric,
            to_wgs84=to_wgs84,
        )
        for row in rows
    ]
    return sorted(out, key=lambda row: str(row["anchor_id"]))
