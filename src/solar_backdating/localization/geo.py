"""Small geo helpers: the nominal target-footprint ring, and applying a
metric-grid translation to a lon/lat ring.

Fixes a confirmed gap (r3-ceiling cross-check, ruled by team-lead
2026-07-19): ``replay_localization_cascade.py``'s ``_identity_ring`` used to
build ``projected_target_polygon`` off the manifest's raw chip bbox
(``chip_lon_min/lat_min/lon_max/lat_max``, the whole ~96-106 m source chip)
-- 7.2-38.8x too large per axis vs R1's ``roi_px`` on a 14-obs spot check,
0/14 falling inside R1's 256x256 crop canvas. ``target_roi_ring_lonlat``
below is the fix: the canonical, shared nominal-ROI definition, aligned with
``scripts/temporal/build_r1_marker_free_crops.py``'s ``r1_cropgeo_v1``
ROI-square formula (``compute_geometry_record``'s ``roi_edge`` /
``_square_corners``) -- same ``roi_edge_m = min(fov_m, sqrt(source_area_m2))``,
same centroid-centred square, same ``METRIC_CRS = EPSG:32735``. This module
is the canonical home per the team-lead ruling; R1 is expected to switch to
referencing this function in a future pass instead of its own inline
computation -- not done here (R1 files are out of this teammate's scope).
"""
from __future__ import annotations


#: Matches ``build_r1_marker_free_crops.py``'s ``METRIC_CRS`` exactly (UTM
#: 35S, true metres, the manifest's own metric CRS).
R1_ALIGNED_METRIC_CRS = "EPSG:32735"


def _square_ring_utm(cx: float, cy: float, edge_m: float) -> list[tuple[float, float]]:
    """Closed 5-point ring (matches ``_identity_ring``'s prior convention:
    CCW from lower-left, first vertex repeated to close), edge ``edge_m``
    centred on ``(cx, cy)`` in a metric CRS."""
    h = edge_m / 2.0
    return [
        (cx - h, cy - h),
        (cx + h, cy - h),
        (cx + h, cy + h),
        (cx - h, cy + h),
        (cx - h, cy - h),
    ]


def target_roi_ring_lonlat(
    centroid_lon: float,
    centroid_lat: float,
    *,
    fov_m: float,
    source_area_m2: float,
    metric_crs: str = R1_ALIGNED_METRIC_CRS,
) -> tuple[tuple[float, float], ...]:
    """The nominal target-footprint square ring (``r1_cropgeo_v1``-aligned
    definition -- see module docstring), as a closed ``(lon, lat)`` ring.

    ``roi_edge_m = min(fov_m, sqrt(source_area_m2))`` (area-equivalent square,
    clamped to the field of view), centred on the target centroid. This is
    the R1/R2-shared **nominal** ROI -- a pre-localization proxy, not the
    true footprint polygon (R1's own known limitation: the manifest carries
    ``source_area_m2`` but not width/height, so an elongated array becomes a
    square; see ``DATA-r1-crops-2026-07-19.md`` §4 item 1). ``fov_m`` is the
    routed review extent for the observation's arm (manifest's
    ``review_extent_m`` column: 24 for A24, 48 for A48).
    """
    import math

    from pyproj import Transformer

    area = max(float(source_area_m2), 0.0)
    roi_edge_m = min(float(fov_m), math.sqrt(area)) if area > 0 else 0.0

    to_metric = Transformer.from_crs("EPSG:4326", metric_crs, always_xy=True)
    to_lonlat = Transformer.from_crs(metric_crs, "EPSG:4326", always_xy=True)

    cx, cy = to_metric.transform(float(centroid_lon), float(centroid_lat))
    ring_utm = _square_ring_utm(cx, cy, roi_edge_m)
    return tuple((float(lon), float(lat)) for lon, lat in (to_lonlat.transform(x, y) for x, y in ring_utm))


def shift_ring_lonlat(
    ring: tuple[tuple[float, float], ...],
    dx_m: float,
    dy_m: float,
    *,
    metric_crs: str,
) -> tuple[tuple[float, float], ...]:
    """Shift each ``(lon, lat)`` vertex of ``ring`` by ``(dx_m, dy_m)``.

    ``dx_m`` is east-positive; ``dy_m`` is south-positive (so northing moves
    by ``-dy_m``) -- matching ``chip_displacement.shift_px_to_m``'s
    convention on the north-up metric grid the registration stages compute
    on.
    """
    from pyproj import Transformer

    to_metric = Transformer.from_crs("EPSG:4326", metric_crs, always_xy=True)
    to_lonlat = Transformer.from_crs(metric_crs, "EPSG:4326", always_xy=True)

    out = []
    for lon, lat in ring:
        x, y = to_metric.transform(lon, lat)
        x2 = x + float(dx_m)
        y2 = y - float(dy_m)
        lon2, lat2 = to_lonlat.transform(x2, y2)
        out.append((float(lon2), float(lat2)))
    return tuple(out)
