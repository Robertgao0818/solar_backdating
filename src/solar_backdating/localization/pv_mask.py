"""PV-polygon + buffer pixel mask for the localization cascade's registration
stages (PRD §5.2: "配准特征屏蔽 PV polygon + buffer ... 防止配准利用待判定目标").

The R0 manifest (``docs/dinov3_scorer/PRD-run3-native-local-line-2026-07-19.md``
§3.2) does not carry actual PV polygon vertices -- only the chip's nominal
bbox and ``source_area_m2``. ``fullscan_target96_review{24,48}_v2`` chips are
target-centred by construction (``scripts/temporal/chip_geometry.py``'s
``crop_context_multiplier=0.01`` + ``contain_crop_to_footprint`` -- the render
crop is floored to the footprint and centred on the anchor), so a centred
square proxy sized off ``source_area_m2`` is a defensible stand-in for the
real polygon until R0 threads the finer geometry through (flagged as an open
interface gap in the R2 delivery memo, not silently assumed away).
"""
from __future__ import annotations

import math

import numpy as np

#: Extra margin beyond the area-derived footprint box, to also cover PV-
#: adjacent structure (panel frame shadow, mounting hardware) that could
#: still leak target-specific signal into the registration surface.
DEFAULT_BUFFER_M = 3.0

#: A real installation footprint is rarely a perfect square; inflate the
#: sqrt(area) square side by this factor so the mask comfortably contains an
#: elongated rectangular array rather than clipping its corners.
DEFAULT_AREA_SHAPE_FACTOR = 1.3

#: Never mask more than this fraction of the shorter grid side -- masking
#: (near-)everything would leave no stable structure for registration to
#: lock onto, which is a different failure (roof_plane_not_matched) than an
#: oversized PV mask silently swallowing the whole chip.
DEFAULT_MAX_MASK_FRACTION = 0.6


def pv_mask_half_extent_m(
    source_area_m2: float,
    *,
    buffer_m: float = DEFAULT_BUFFER_M,
    shape_factor: float = DEFAULT_AREA_SHAPE_FACTOR,
) -> float:
    """Half-side (metres) of the centred square PV-mask proxy."""
    area = max(float(source_area_m2), 0.0)
    side = math.sqrt(area) * shape_factor
    return side / 2.0 + buffer_m


def build_centered_mask(
    shape: tuple[int, int],
    gsd_m: float,
    half_extent_m: float,
    *,
    max_mask_fraction: float = DEFAULT_MAX_MASK_FRACTION,
) -> tuple[np.ndarray, float]:
    """Boolean ``(H, W)`` array, ``True`` = excluded (PV + buffer) pixel.

    The mask is centred on the array (matches the target-centred chip
    convention -- see module docstring) and capped at
    ``max_mask_fraction`` of the shorter side so registration always retains
    some unmasked border. Returns ``(mask, applied_half_extent_px)`` -- the
    latter for provenance/debugging (whether the cap actually bound).
    """
    h, w = shape
    if gsd_m <= 0:
        raise ValueError(f"gsd_m must be positive, got {gsd_m!r}")
    half_extent_px = half_extent_m / gsd_m
    cap_px = max_mask_fraction * min(h, w) / 2.0
    half_extent_px = min(half_extent_px, cap_px)

    cy, cx = (h - 1) / 2.0, (w - 1) / 2.0
    yy, xx = np.ogrid[:h, :w]
    mask = (np.abs(yy - cy) <= half_extent_px) & (np.abs(xx - cx) <= half_extent_px)
    return mask, half_extent_px
