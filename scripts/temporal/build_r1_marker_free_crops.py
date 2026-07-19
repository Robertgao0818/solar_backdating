#!/usr/bin/env python3
"""R1 marker-free student crops from the frozen Run3-native manifest (PRD §6.2).

Parent: ``docs/dinov3_scorer/PRD-run3-native-local-line-2026-07-19.md`` §6 R1 --
"从 96m TIFF 生成与 A24/A48 同视野的无十字线 crop; 精确 polygon ROI pooling +
一圈 roof/context 特征; 不让 DINO 学到 marker, 也不用整幅 96m 稀释小目标". This
script builds ONLY the crop bytes + the versioned crop geometry (ROI + context
ring, pixel and metre). It does **not** embed anything -- feature extraction is
R3 (its cache contract is documented in ``R3_FEATURE_CACHE_CONTRACT`` below but
not implemented here).

Frame list is the frozen R0 manifest (single source of truth; never a disk scan):
``~/zasolar_data/geid_temporal/run3_native_line_2026-07/r0_manifest_v1/manifest.parquet``
(311,195 rows, sha256 ffd0c174...f8cf). Source rasters are the per-target 96 m
z19 chips under ``basemap_rebuild_2026-07-13/chips/<anchor>/z19/*.tif``. Crops
land under ``run3_native_line_2026-07/r1_crops_v1/`` (data never enters the repo).

===========================================================================
Crop geometry ``r1_cropgeo_v1@2026-07-19`` (the only version this script emits)
===========================================================================

The crop reproduces the field of view that Gemini scored (the routed
``fullscan_target96_review{24,48}_v2`` render, ``gehi_common.ensure_single_
target_review_png``) **minus the crosshair marker**, so the student sees exactly
what the teacher saw. The render is a *pixel-space centred crop* of the source
chip -- it does NOT reproject -- and this script re-derives the identical crop
window so the marker-free bytes match the teacher's ``draw_marker=False`` path.

Symbols (all per frame, read from the manifest row):

* ``W, H``            source raster pixel size (``raster_width_px/height_px``)
* ``S = min(W, H)``   short side, the render's crop basis
* ``chip_m = 96``     nominal source chip extent (``GEHI_DOWNLOAD_CHIP_SIZE_M``)
* ``fov_m``           routed review extent = 24 (A24) / 48 (A48) = ``min_crop_size_m``
* ``out_min = 256``   output short-side floor (``min_output_px``)
* ``A, E, C, F``      world-file params (``tfw_{a,e,c,f}``; rotation ``B=D=0``).
                      Pixel->world: ``X = C + col*A``, ``Y = F + row*E`` (``E<0``).
                      A/E are in the raster CRS units: **metres (inflated) for
                      EPSG:3857 Wayback**, **degrees for EPSG:4326 TM**.

Crop window (identical formula to ``ensure_single_target_review_png`` with the
fullscan geometry, where ``2*radius*crop_context_multiplier = 2*r*0.01`` is
always << ``fov_m`` so ``crop_size_m`` collapses to ``fov_m``):

    crop_size_m = min(chip_m, max(fov_m, 2*r*0.01)) = fov_m         # 24 or 48
    crop_px     = clamp(round(S * fov_m / chip_m), 1, min(W, H))
    target_x    = W/2 ,  target_y = H/2        # per-target chip: offset ≡ 0
    left = clamp(round(target_x - crop_px/2), 0, W - crop_px)
    top  = clamp(round(target_y - crop_px/2), 0, H - crop_px)
    crop = source[top:top+crop_px, left:left+crop_px]              # square in px
    scale   = out_min / crop_px   if crop_px < out_min   else 1.0   # BICUBIC up
    out_px  = round(crop_px * scale)                                # ≈ 256

Because ``crop_px`` is a fraction of the *short* side while EPSG:4326 pixels are
square-in-degrees (hence non-square-in-ground: gsd_x != gsd_y), the crop's
GROUND field of view is nominally ``fov_m`` but slightly anisotropic on the TM
path (e.g. ~22 m x ~24.5 m for a nominal 24 m A24 TM frame). This is a faithful
property of the teacher render, not a bug; both ground dimensions are recorded
per crop (``fov_ground_w_m`` / ``fov_ground_h_m``).

------------------------------------------------------------------- ROI polygon
The *nominal* target ROI (pre-localization; R2's ``TargetLocalizationObservation.
projected_target_polygon`` supersedes it once the localization cascade has run --
see the seam note in ``DATA-r1-crops-2026-07-19.md``). Defined in TRUE ground
metres via UTM 35S (EPSG:32735, the manifest's metric CRS), then mapped through
each frame's own CRS so both providers are handled correctly:

    roi_edge_m     = min(fov_m, sqrt(source_area_m2))     # area-equivalent square,
                                                          # clamped to the FoV
    context_edge_m = min(fov_m, roi_edge_m * 2.0)         # one roof/context ring

    centroid (lon,lat) --T_ll_utm--> (Ec, Nc)   [EPSG:32735, true metres]
    ROI corners      = (Ec +/- roi_edge_m/2,     Nc +/- roi_edge_m/2)
    context corners  = (Ec +/- context_edge_m/2, Nc +/- context_edge_m/2)
    each UTM corner --T_utm_frame--> (X,Y) --inverse TFW--> (col,row)
                    --crop--> (col-left, row-top) --*scale--> OUTPUT pixel

The context RING is the annulus ``context_square`` minus ``roi_square``. Both polygons
are stored twice -- ``*_px`` in OUTPUT-crop pixels and ``*_lonlat`` in degrees
(the "像素/米两套坐标" contract; UTM corners are recoverable from lon/lat).

Output key ``(chip_sha, crop_geometry)``: a given ``chip_sha`` (source raster
SHA-256) belongs to exactly one anchor/arm (verified: 0 shas span arm/extent),
so the pair is unambiguous. 311,126 unique crops for 311,195 manifest rows (69
byte-identical rasters shared across two capture_dates collapse to one crop).

Layout under ``r1_crops_v1/`` (data only; repo gets code/tests/docs):

    crops/<sha2>/<chip_sha>.r1cg1.png      marker-free crop (atomic: .tmp->rename)
    crop_geometry_index/<sha2>.parquet     per-crop geometry (pure fn of manifest)
    qa/                                     sampled QA sheet (html + tiles)
    _R1_CROPS_STATUS.json                   provenance + input-manifest sha lock

Resume: a crop whose final PNG exists and is non-empty is skipped; the geometry
index is a pure deterministic function of manifest rows and is always rewritten.

===========================================================================
Crop geometry ``r1_cropgeo_v2@2026-07-19`` (owner-approved amendment, additive)
===========================================================================
Same crop RASTER as v1 -- FoV, centred window, BICUBIC upsample to 256px, no
marker -- byte-identical PNGs for the same ``chip_sha``. v2 changes ONLY the
nominal ROI/context definition, from an area-equivalent SQUARE to an
area-preserving RECTANGLE at the R0.1 footprint sidecar's bbox aspect ratio
(closes the seam this script's own docstring flagged in
``DATA-r1-crops-2026-07-19.md`` §4.1, per the recommendation in
``DATA-r0-footprint-sidecar-2026-07-19.md`` §4):

    r = aspect_ratio (sidecar, bbox long/short, >= 1)
    long_pre  = sqrt(area * r)   # sidecar's areamatched_long_m -- not re-derived
    short_pre = sqrt(area / r)   # sidecar's areamatched_short_m
    # long_pre * short_pre == area, exactly -- area-preserving BEFORE any clamp
    long axis := x  if source_width_m >= source_height_m  else y
        # bbox-only signal; the sidecar carries no rotation, so no oriented ROI
        # is attempted (rotated/fill<1 targets are R2's projected_target_polygon
        # job -- an explicit non-goal here, per sidecar doc §4).
    (edge_x_pre, edge_y_pre) := (long_pre, short_pre) or (short_pre, long_pre) per the above
    edge_x = min(fov_m, edge_x_pre) ;  edge_y = min(fov_m, edge_y_pre)   # PER-AXIS FoV clamp
    context_edge_x = min(fov_m, edge_x * 2.0) ; context_edge_y = min(fov_m, edge_y * 2.0)

If either axis clamps, the rendered ROI area is less than ``source_area_m2``;
this is recorded per-crop (``roi_clamp_loss_frac``), never silently absorbed.
If an anchor has no sidecar row (or its area-matched columns are NaN), v2
FALLS BACK to the v1 square and records ``roi_source="fallback_square_v2"``
(expected count: 0 -- the sidecar/manifest reconciliation is 0 missing/0 extra,
re-verified per run in this script, not assumed).

**PNG-reuse contract (red line)**: v2 NEVER renders. ``crop_geometry_index`` is
version-keyed by SUBDIRECTORY (``crop_geometry_index/`` for v1 -- unchanged --
``crop_geometry_index_v2/`` for v2, both under the SAME ``out_dir``), but
``crops/<sha2>/<chip_sha>.r1cg1.png`` is the identical tree and filename tag for
both versions: the crop pixel grid does not depend on ``crop_geometry`` at all,
only the ROI/context annotation does. v2 rows in the geometry index point at
whatever bytes the v1 render pass produces (before, during, or after is fine --
this script's v2 path only ever reads existing PNGs, for its QA sheet, or
writes no PNG at all, for the pure geometry index). This is how
``crop_geometry`` re-keys the R3 feature cache (``chip_sha + crop_geometry +
backbone_hash + pooling_version``) WITHOUT re-keying the crop-pixel store.
Provenance lands in ``_R1_CROPS_STATUS_v2.json`` -- a distinct filename from
v1's ``_R1_CROPS_STATUS.json`` since both versions share ``out_dir``.

===========================================================================
R3_FEATURE_CACHE_CONTRACT (PRD §6.4 -- DEFINED here, IMPLEMENTED in R3)
===========================================================================
The R3 DINO embed step caches one pooled ROI feature per crop under the key
``(chip_sha, crop_geometry, backbone_hash, pooling_version)``:

* ``chip_sha``        source raster SHA-256 (this manifest's ``src_tiff_sha256``).
* ``crop_geometry``   ``r1_cropgeo_v1@2026-07-19`` or ``r1_cropgeo_v2@2026-07-19``
                      (this script's version; the ROI + context ring the pooling
                      reads are frozen by it -- v2 changes the ROI/context shape
                      only, never the crop pixels).
* ``backbone_hash``   DINO weights id (DINOv2-S baseline / DINOv3-L-SAT challenger).
* ``pooling_version`` how ROI + context-ring features are pooled (masked mean over
                      ``roi_*_px`` / ring, etc.) -- a named, versioned scheme.

fp16 pooled ROI feature vectors, written to sharded Parquet/Arrow (shard by
``chip_sha[:2]``, matching this script), each shard finalized with an atomic
completion marker and skipped on resume. No PNG/NPZ flood (the old cache was
cleared; R3 starts on this contract). This script writes NEITHER features NOR
that cache -- it only guarantees the ``(chip_sha, crop_geometry)`` half of the
key is stable and that ``roi_*_px`` / ``context_*_px`` are recorded for pooling.

Run from the shared venv (``source scripts/activate_env.sh``).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from chip_geometry import (  # noqa: E402
    GEHI_DOWNLOAD_CHIP_SIZE_M,
    FULLSCAN_GEOMETRY_VERSION_BY_ARM,
    resolve_chip_geometry,
)

# --------------------------------------------------------------------------- #
# Frozen version identifiers (flow into every output row + the status lock).
# --------------------------------------------------------------------------- #
CROP_GEOMETRY_VERSION = "r1_cropgeo_v1@2026-07-19"
CROP_GEOMETRY_TAG = "r1cg1"  # short filename tag for CROP_GEOMETRY_VERSION
CONTEXT_MULTIPLIER = 2.0  # context square edge = min(fov, roi_edge * this)
METRIC_CRS = "EPSG:32735"  # UTM 35S, the manifest's metric CRS (true metres)

DEFAULT_MANIFEST = (
    Path.home()
    / "zasolar_data/geid_temporal/run3_native_line_2026-07"
    / "r0_manifest_v1/manifest.parquet"
)
EXPECTED_MANIFEST_SHA = (
    "ffd0c174d6dc62098ce96ff3a1883b4d3a0006efd7ced99d6543c7a2f053f8cf"
)
EXPECTED_MANIFEST_ROWS = 311195
DEFAULT_OUT_DIR = (
    Path.home()
    / "zasolar_data/geid_temporal/run3_native_line_2026-07/r1_crops_v1"
)

# --------------------------------------------------------------------------- #
# Crop geometry v2 (r1_cropgeo_v2@2026-07-19) -- ROI/context definition only;
# see the module-docstring section above. The crop RASTER (out_dir, PNG tree,
# CROP_GEOMETRY_TAG) is shared verbatim with v1 -- only the geometry_index
# subdirectory and the status filename are version-specific (both v1 and v2
# read/write the SAME out_dir).
# --------------------------------------------------------------------------- #
CROP_GEOMETRY_VERSION_V2 = "r1_cropgeo_v2@2026-07-19"

DEFAULT_SIDECAR = (
    Path.home()
    / "zasolar_data/geid_temporal/run3_native_line_2026-07"
    / "r0_manifest_v1/footprint_sidecar_v1.parquet"
)
EXPECTED_SIDECAR_SHA = (
    "a1f1bd9cd81f21e31b4574073023fc8044303db8214f5ff07c3175f375b89623"
)
EXPECTED_SIDECAR_ROWS = 41393

# crop_geometry_index dirname, keyed by crop_geometry version. v1's name is the
# frozen historical name (never renamed -- v1 outputs stay valid as-is).
CROP_GEOMETRY_INDEX_DIRNAME = {
    CROP_GEOMETRY_VERSION: "crop_geometry_index",
    CROP_GEOMETRY_VERSION_V2: "crop_geometry_index_v2",
}
# Status/provenance filename, keyed by crop_geometry version. MUST differ
# between versions since v1 and v2 runs share out_dir -- a shared filename
# would silently clobber whichever version's run happened last.
STATUS_FILENAME = {
    CROP_GEOMETRY_VERSION: "_R1_CROPS_STATUS.json",
    CROP_GEOMETRY_VERSION_V2: "_R1_CROPS_STATUS_v2.json",
}
# --qa-only writes here instead -- it never touches the production status file
# above. Without this split, a `--qa-only` run (e.g. a read-only comparison
# check against the real corpus) silently clobbers the production render tally
# (rendered/skipped go to 0) with no way to recover it short of a stray log
# file happening to still exist. Discovered the hard way during cropgeo_v2
# development (see DATA-cropgeo-v2-2026-07-19.md §2 incident note).
QA_STATUS_FILENAME = {
    CROP_GEOMETRY_VERSION: "_R1_QA_STATUS.json",
    CROP_GEOMETRY_VERSION_V2: "_R1_QA_STATUS_v2.json",
}

# Area strata for stratified QA sampling (must match the R0 lock's four strata).
AREA_BINS = [(0, 15), (15, 40), (40, 100), (100, float("inf"))]

# Aspect-ratio strata for the cropgeo_v2 QA sample (DATA-r0-footprint-sidecar
# -2026-07-19.md §3): [1,1.5) / [1.5,2) / [2,4) / [4,+inf), crossed with chip_arm.
ASPECT_BINS = [(1.0, 1.5), (1.5, 2.0), (2.0, 4.0), (4.0, float("inf"))]


def area_bin_label(area_m2: float | None) -> str:
    if area_m2 is None or (isinstance(area_m2, float) and math.isnan(area_m2)):
        return "unknown"
    for lo, hi in AREA_BINS:
        if lo <= area_m2 < hi:
            return f"[{lo},{hi if hi != float('inf') else 'inf'})"
    return "unknown"


def aspect_bin_label(aspect: float | None) -> str:
    if aspect is None or (isinstance(aspect, float) and math.isnan(aspect)):
        return "unknown"
    for lo, hi in ASPECT_BINS:
        if lo <= aspect < hi:
            return f"[{lo},{hi if hi != float('inf') else 'inf'})"
    return "unknown"


# --------------------------------------------------------------------------- #
# Pure geometry (imported by the unit tests).
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class CropWindow:
    """The pixel-space crop window + upsample, re-derived to match the teacher
    render (``ensure_single_target_review_png``) byte-for-byte sans marker."""

    crop_px: int
    left: int
    top: int
    scale: float
    out_px: int
    crop_size_m: float
    target_x: float
    target_y: float


def arm_render_params(chip_arm: str) -> tuple[float, int]:
    """Return ``(fov_m, min_output_px)`` for an arm from the frozen chip-geometry
    registry (never hard-coded here -- the routed review extent is authoritative
    in ``chip_geometry.py``). ``fov_m`` is that arm's ``min_crop_size_m``."""
    gv = FULLSCAN_GEOMETRY_VERSION_BY_ARM[chip_arm.upper()]
    g = resolve_chip_geometry(gv)
    return float(g.min_crop_size_m), int(g.min_output_px)


def compute_crop_window(
    width_px: int,
    height_px: int,
    fov_m: float,
    *,
    chip_size_m: float = GEHI_DOWNLOAD_CHIP_SIZE_M,
    min_output_px: int = 256,
) -> CropWindow:
    """Re-derive the teacher's centred crop window. Target offset is 0 for these
    per-target chips (the manifest carries no offset column; centre ≡ centroid,
    verified <0.02 m), so ``target_{x,y}`` are the image centre. Formula is
    identical to ``ensure_single_target_review_png`` (see module docstring)."""
    W, H = int(width_px), int(height_px)
    short = min(W, H)
    crop_size_m = min(float(chip_size_m), max(float(fov_m), 0.0))
    crop_px = max(1, int(round(short * crop_size_m / float(chip_size_m))))
    crop_px = min(crop_px, W, H)
    target_x = W / 2.0
    target_y = H / 2.0
    left = int(round(target_x - crop_px / 2.0))
    left = max(0, min(left, W - crop_px))
    top = int(round(target_y - crop_px / 2.0))
    top = max(0, min(top, H - crop_px))
    if crop_px < min_output_px:
        scale = float(min_output_px) / float(crop_px)
        out_px = max(1, int(round(crop_px * scale)))
    else:
        scale = 1.0
        out_px = crop_px
    return CropWindow(
        crop_px=crop_px,
        left=left,
        top=top,
        scale=scale,
        out_px=out_px,
        crop_size_m=crop_size_m,
        target_x=target_x,
        target_y=target_y,
    )


def world_to_src_px(
    x: float, y: float, tfw: tuple[float, float, float, float]
) -> tuple[float, float]:
    """World (frame CRS) -> fractional source pixel (col, row). ``tfw=(A,E,C,F)``,
    rotation B=D=0: ``col=(x-C)/A``, ``row=(y-F)/E``."""
    a, e, c, f = tfw
    return (x - c) / a, (y - f) / e


def src_px_to_out_px(
    col: float, row: float, win: CropWindow
) -> tuple[float, float]:
    """Source pixel -> output-crop pixel (crop then upsample)."""
    return (col - win.left) * win.scale, (row - win.top) * win.scale


class _Transformers:
    """Lazily-built, cached pyproj transformers keyed by frame CRS."""

    def __init__(self) -> None:
        from pyproj import Transformer

        self._ll_to_utm = Transformer.from_crs(
            "EPSG:4326", METRIC_CRS, always_xy=True
        )
        self._utm_to_frame: dict[str, Any] = {}
        self._Transformer = Transformer

    def ll_to_utm(self, lon: float, lat: float) -> tuple[float, float]:
        e, n = self._ll_to_utm.transform(lon, lat)
        return float(e), float(n)

    def utm_to_frame(self, e: float, n: float, frame_crs: str) -> tuple[float, float]:
        tf = self._utm_to_frame.get(frame_crs)
        if tf is None:
            tf = self._Transformer.from_crs(METRIC_CRS, frame_crs, always_xy=True)
            self._utm_to_frame[frame_crs] = tf
        x, y = tf.transform(e, n)
        return float(x), float(y)


def _rect_corners(
    cx: float, cy: float, edge_x: float, edge_y: float
) -> list[tuple[float, float]]:
    """Axis-aligned rectangle corners in a north-up frame, CCW from lower-left.
    A square is the special case ``edge_x == edge_y`` (v1's ROI/context and
    v2's context ring / non-elongated ROIs)."""
    hx, hy = edge_x / 2.0, edge_y / 2.0
    return [(cx - hx, cy - hy), (cx + hx, cy - hy), (cx + hx, cy + hy), (cx - hx, cy + hy)]


def _square_corners(cx: float, cy: float, edge: float) -> list[tuple[float, float]]:
    return _rect_corners(cx, cy, edge, edge)


@dataclass
class GeometryRecord:
    anchor_id: str
    capture_date: str
    chip_sha: str
    crop_geometry: str
    raster_crs: str
    provider: str
    chip_arm: str
    area_bin: str
    source_area_m2: float
    review_extent_m: float
    # crop window
    src_width_px: int
    src_height_px: int
    crop_px: int
    left: int
    top: int
    scale: float
    out_px: int
    crop_size_m: float
    fov_ground_w_m: float
    fov_ground_h_m: float
    center_reproj_err_px: float  # source-pixel (native GSD) -- the <1px invariant
    center_reproj_err_out_px: float  # same in upscaled output pixels (diagnostic)
    # ROI + context (pixel + lon/lat). In general a RECTANGLE (edge_x != edge_y);
    # a SQUARE is the special case edge_x == edge_y (v1 always; v2's fallback).
    roi_edge_m: float  # legacy/informational: max(roi_edge_x_m, roi_edge_y_m) -- v1's edge exactly
    context_edge_m: float  # legacy/informational: max(context_edge_x_m, context_edge_y_m)
    roi_edge_x_m: float
    roi_edge_y_m: float
    context_edge_x_m: float
    context_edge_y_m: float
    roi_area_m2: float  # actual rendered ROI area (post FoV clamp) = roi_edge_x_m * roi_edge_y_m
    roi_clamp_loss_frac: float  # max(0, 1 - roi_area_m2/source_area_m2); 0 unless FoV-clamped
    roi_long_axis: str  # "x" | "y" | "square"
    roi_source: str  # "square_v1" | "sidecar_v2" | "fallback_square_v2"
    roi_px: str  # json list of [ox,oy]
    context_px: str
    roi_lonlat: str
    context_lonlat: str
    roi_in_bounds: bool
    png_relpath: str


def resolve_roi_spec(
    area_m2: float,
    fov_m: float,
    *,
    crop_geometry: str,
    sidecar_row: "pd.Series | None" = None,
) -> dict[str, Any]:
    """Resolve the nominal (pre-localization) ROI edges for one frame.

    Returns ``edge_x_pre``/``edge_y_pre`` (metres, BEFORE the per-axis FoV clamp
    -- their product always equals ``area_m2`` exactly, the area-preserving
    invariant both v1 and v2 share) and ``edge_x``/``edge_y`` (AFTER the clamp --
    what actually gets rendered), plus ``long_axis``/``roi_source`` provenance.

    * v1 (``CROP_GEOMETRY_VERSION``): axis-aligned SQUARE, edge = sqrt(area).
    * v2 (``CROP_GEOMETRY_VERSION_V2``): axis-aligned, AREA-PRESERVING RECTANGLE
      at the sidecar's bbox aspect ratio (``areamatched_long_m``/``short_m``,
      already ``sqrt(A*r)``/``sqrt(A/r)`` -- not re-derived here). The long edge
      goes on whichever raw axis (x or y) the sidecar bbox says is longer
      (``source_width_m`` vs ``source_height_m``); no rotation is attempted
      (DATA-r0-footprint-sidecar-2026-07-19.md §4 non-goal). If the sidecar row
      is missing or its area-matched columns are NaN, falls back to the v1
      square and records ``roi_source="fallback_square_v2"``.
    """
    edge_sq_pre = math.sqrt(area_m2) if area_m2 > 0 else 0.0

    if crop_geometry == CROP_GEOMETRY_VERSION:
        edge_x_pre = edge_y_pre = edge_sq_pre
        long_axis = "square"
        roi_source = "square_v1"
    elif crop_geometry == CROP_GEOMETRY_VERSION_V2:
        has_sidecar = sidecar_row is not None and not (
            pd.isna(sidecar_row.get("areamatched_long_m"))
            or pd.isna(sidecar_row.get("areamatched_short_m"))
        )
        if has_sidecar:
            long_m = float(sidecar_row["areamatched_long_m"])
            short_m = float(sidecar_row["areamatched_short_m"])
            w = float(sidecar_row["source_width_m"])
            h = float(sidecar_row["source_height_m"])
            if w >= h:
                edge_x_pre, edge_y_pre, long_axis = long_m, short_m, "x"
            else:
                edge_x_pre, edge_y_pre, long_axis = short_m, long_m, "y"
            roi_source = "sidecar_v2"
        else:
            edge_x_pre = edge_y_pre = edge_sq_pre
            long_axis = "square"
            roi_source = "fallback_square_v2"
    else:
        raise ValueError(
            f"unknown crop_geometry {crop_geometry!r}; expected "
            f"{CROP_GEOMETRY_VERSION!r} or {CROP_GEOMETRY_VERSION_V2!r}"
        )

    edge_x = min(fov_m, edge_x_pre)
    edge_y = min(fov_m, edge_y_pre)
    return {
        "edge_x_pre": edge_x_pre,
        "edge_y_pre": edge_y_pre,
        "edge_x": edge_x,
        "edge_y": edge_y,
        "long_axis": long_axis,
        "roi_source": roi_source,
    }


def compute_geometry_record(
    row: pd.Series,
    tfx: _Transformers,
    *,
    crop_geometry: str = CROP_GEOMETRY_VERSION,
    sidecar_row: "pd.Series | None" = None,
) -> GeometryRecord:
    """Full per-frame geometry record (pure fn of a manifest row + transformers).

    Also computes the crop-centre reprojection error: reproject the target
    centroid through frame CRS -> source px -> output px and compare to the
    geometric output centre (``out_px/2``). This is the <1 px invariant the
    tests assert for both CRS paths.

    ``crop_geometry``/``sidecar_row`` select the ROI shape only (see
    ``resolve_roi_spec``); the crop window/render below is IDENTICAL regardless
    of ``crop_geometry`` -- this is what makes v2 crops byte-identical to v1's.
    """
    arm = str(row["chip_arm"])
    fov_m, min_out = arm_render_params(arm)
    win = compute_crop_window(
        int(row["raster_width_px"]),
        int(row["raster_height_px"]),
        fov_m,
        min_output_px=min_out,
    )
    tfw = (float(row["tfw_a"]), float(row["tfw_e"]), float(row["tfw_c"]), float(row["tfw_f"]))
    frame_crs = str(row["raster_crs"])
    lon, lat = float(row["centroid_lon"]), float(row["centroid_lat"])

    # Crop-centre reprojection error: reproject the target centroid through the
    # frame CRS to a source pixel and compare to the crop window's centre. Source
    # pixels are the native reprojection resolution (~0.3 m GSD); the output crop
    # is that window upsampled (interpolation only), so the error is measured at
    # source scale -- the honest invariant. The output-pixel value is reported too.
    ec, nc = tfx.ll_to_utm(lon, lat)
    xc, yc = tfx.utm_to_frame(ec, nc, frame_crs)
    col_c, row_c = world_to_src_px(xc, yc, tfw)
    win_center_col = win.left + win.crop_px / 2.0
    win_center_row = win.top + win.crop_px / 2.0
    center_err_src = math.hypot(col_c - win_center_col, row_c - win_center_row)
    center_err = center_err_src  # source-pixel error (the <1px invariant)
    ox_c, oy_c = src_px_to_out_px(col_c, row_c, win)
    center_err_out = math.hypot(ox_c - win.out_px / 2.0, oy_c - win.out_px / 2.0)

    # ground FoV (true metres) from the source-pixel crop edges via UTM
    def out_to_utm(ox: float, oy: float) -> tuple[float, float]:
        col = win.left + ox / win.scale
        rw = win.top + oy / win.scale
        a, e, c, f = tfw
        x = c + col * a
        y = f + rw * e
        return _frame_to_utm(x, y, frame_crs, tfx)

    e_l, n_l = out_to_utm(0.0, win.out_px / 2.0)
    e_r, n_r = out_to_utm(win.out_px, win.out_px / 2.0)
    e_t, n_t = out_to_utm(win.out_px / 2.0, 0.0)
    e_b, n_b = out_to_utm(win.out_px / 2.0, win.out_px)
    fov_w = math.hypot(e_r - e_l, n_r - n_l)
    fov_h = math.hypot(e_b - e_t, n_b - n_t)

    # ROI + context rectangles in UTM -> output px + lon/lat. v1: always a
    # square (edge_x == edge_y). v2: sidecar-aspect rectangle, per-axis clamped.
    area = float(row["source_area_m2"])
    roi_spec = resolve_roi_spec(
        area, fov_m, crop_geometry=crop_geometry, sidecar_row=sidecar_row
    )
    edge_x, edge_y = roi_spec["edge_x"], roi_spec["edge_y"]
    context_edge_x = min(fov_m, edge_x * CONTEXT_MULTIPLIER)
    context_edge_y = min(fov_m, edge_y * CONTEXT_MULTIPLIER)
    roi_area = edge_x * edge_y
    roi_clamp_loss_frac = max(0.0, 1.0 - roi_area / area) if area > 0 else 0.0

    def corners_px_and_lonlat(edge_x_m: float, edge_y_m: float):
        px: list[list[float]] = []
        ll: list[list[float]] = []
        utm_to_ll = _get_utm_to_ll(tfx)
        for (ce, cn) in _rect_corners(ec, nc, edge_x_m, edge_y_m):
            fx, fy = tfx.utm_to_frame(ce, cn, frame_crs)
            col, rw = world_to_src_px(fx, fy, tfw)
            ox, oy = src_px_to_out_px(col, rw, win)
            px.append([round(ox, 3), round(oy, 3)])
            clon, clat = utm_to_ll.transform(ce, cn)
            ll.append([round(float(clon), 9), round(float(clat), 9)])
        return px, ll

    roi_px, roi_ll = corners_px_and_lonlat(edge_x, edge_y)
    context_px, context_ll = corners_px_and_lonlat(context_edge_x, context_edge_y)
    roi_in_bounds = all(
        -0.5 <= p[0] <= win.out_px + 0.5 and -0.5 <= p[1] <= win.out_px + 0.5
        for p in roi_px
    )

    chip_sha = str(row["src_tiff_sha256"])
    png_rel = f"crops/{chip_sha[:2]}/{chip_sha}.{CROP_GEOMETRY_TAG}.png"
    return GeometryRecord(
        anchor_id=str(row["anchor_id"]),
        capture_date=str(row["capture_date"]),
        chip_sha=chip_sha,
        crop_geometry=crop_geometry,
        raster_crs=frame_crs,
        provider=str(row["provider"]),
        chip_arm=arm,
        area_bin=area_bin_label(area),
        source_area_m2=area,
        review_extent_m=float(fov_m),
        src_width_px=int(row["raster_width_px"]),
        src_height_px=int(row["raster_height_px"]),
        crop_px=win.crop_px,
        left=win.left,
        top=win.top,
        scale=round(win.scale, 8),
        out_px=win.out_px,
        crop_size_m=win.crop_size_m,
        fov_ground_w_m=round(fov_w, 4),
        fov_ground_h_m=round(fov_h, 4),
        center_reproj_err_px=round(center_err, 5),
        center_reproj_err_out_px=round(center_err_out, 5),
        roi_edge_m=round(max(edge_x, edge_y), 4),
        context_edge_m=round(max(context_edge_x, context_edge_y), 4),
        roi_edge_x_m=round(edge_x, 4),
        roi_edge_y_m=round(edge_y, 4),
        context_edge_x_m=round(context_edge_x, 4),
        context_edge_y_m=round(context_edge_y, 4),
        roi_area_m2=round(roi_area, 6),
        roi_clamp_loss_frac=round(roi_clamp_loss_frac, 6),
        roi_long_axis=roi_spec["long_axis"],
        roi_source=roi_spec["roi_source"],
        roi_px=json.dumps(roi_px),
        context_px=json.dumps(context_px),
        roi_lonlat=json.dumps(roi_ll),
        context_lonlat=json.dumps(context_ll),
        roi_in_bounds=bool(roi_in_bounds),
        png_relpath=png_rel,
    )


_UTM_TO_LL_CACHE: dict[int, Any] = {}


def _get_utm_to_ll(tfx: _Transformers):
    key = id(tfx)
    tf = _UTM_TO_LL_CACHE.get(key)
    if tf is None:
        from pyproj import Transformer

        tf = Transformer.from_crs(METRIC_CRS, "EPSG:4326", always_xy=True)
        _UTM_TO_LL_CACHE[key] = tf
    return tf


_FRAME_TO_UTM_CACHE: dict[tuple[int, str], Any] = {}


def _frame_to_utm(x: float, y: float, frame_crs: str, tfx: _Transformers):
    key = (id(tfx), frame_crs)
    tf = _FRAME_TO_UTM_CACHE.get(key)
    if tf is None:
        from pyproj import Transformer

        tf = Transformer.from_crs(frame_crs, METRIC_CRS, always_xy=True)
        _FRAME_TO_UTM_CACHE[key] = tf
    e, n = tf.transform(x, y)
    return float(e), float(n)


# --------------------------------------------------------------------------- #
# Rendering (mirrors ensure_single_target_review_png, draw_marker=False).
# --------------------------------------------------------------------------- #
def render_marker_free_crop(tiff_path: str, win: CropWindow):
    """Return a PIL.Image of the marker-free crop for ``win``. Identical crop +
    BICUBIC upsample to the teacher's ``draw_marker=False`` path."""
    from PIL import Image

    with Image.open(tiff_path) as img:
        if img.mode not in ("RGB", "RGBA"):
            img = img.convert("RGB")
        else:
            img = img.copy()
        crop = img.crop((win.left, win.top, win.left + win.crop_px, win.top + win.crop_px))
        if win.scale != 1.0:
            new_size = (
                max(1, int(round(crop.size[0] * win.scale))),
                max(1, int(round(crop.size[1] * win.scale))),
            )
            resampling = getattr(Image, "Resampling", Image).BICUBIC
            crop = crop.resize(new_size, resampling)
    return crop


def _atomic_save_png(img, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    img.save(tmp, format="PNG")
    os.replace(tmp, dest)


# --------------------------------------------------------------------------- #
# Manifest loading + reconciliation.
# --------------------------------------------------------------------------- #
def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_manifest(manifest_path: Path, check_sha: bool) -> pd.DataFrame:
    if check_sha:
        got = sha256_file(manifest_path)
        if got != EXPECTED_MANIFEST_SHA:
            raise SystemExit(
                f"[R1][FATAL] manifest sha mismatch: actual={got} "
                f"expected={EXPECTED_MANIFEST_SHA} -- STOP (do not fabricate)"
            )
    df = pd.read_parquet(manifest_path)
    if check_sha and len(df) != EXPECTED_MANIFEST_ROWS:
        raise SystemExit(
            f"[R1][FATAL] manifest rows: actual={len(df)} "
            f"expected={EXPECTED_MANIFEST_ROWS} -- STOP"
        )
    return df


def load_sidecar(sidecar_path: Path, check_sha: bool) -> pd.DataFrame:
    """Load the R0.1 footprint sidecar (only read for ``--crop-geometry v2``)."""
    if check_sha:
        got = sha256_file(sidecar_path)
        if got != EXPECTED_SIDECAR_SHA:
            raise SystemExit(
                f"[R1][FATAL] sidecar sha mismatch: actual={got} "
                f"expected={EXPECTED_SIDECAR_SHA} -- STOP (do not fabricate)"
            )
    df = pd.read_parquet(sidecar_path)
    if check_sha and len(df) != EXPECTED_SIDECAR_ROWS:
        raise SystemExit(
            f"[R1][FATAL] sidecar rows: actual={len(df)} "
            f"expected={EXPECTED_SIDECAR_ROWS} -- STOP"
        )
    return df


def stratified_sample(
    df: pd.DataFrame, per_stratum: int, seed: int
) -> pd.DataFrame:
    """>=per_stratum frames per (chip_arm x area_bin x CRS) cell, per the QA
    plan (A24|[0,15), A24|[15,40), A48|[40,100), A48|[100,inf) x {3857,4326})."""
    df = df.copy()
    df["_crs"] = df["raster_crs"]
    parts = []
    for _, g in df.groupby(["chip_arm", "area_bin", "_crs"]):
        parts.append(g.sample(min(per_stratum, len(g)), random_state=seed))
    return pd.concat(parts).drop(columns="_crs")


def stratified_sample_by_aspect(
    df: pd.DataFrame, per_stratum: int, seed: int
) -> pd.DataFrame:
    """>=per_stratum frames per (aspect_bin x chip_arm) cell -- the cropgeo_v2
    QA plan (DATA-r0-footprint-sidecar-2026-07-19.md §3 aspect bins x arm).
    Requires ``df`` to already carry a sidecar-joined ``aspect_ratio`` column
    (rows with no sidecar match bin to "unknown" and are excluded)."""
    df = df.copy()
    df["_aspect_bin"] = df["aspect_ratio"].map(aspect_bin_label)
    parts = []
    for (ab, _arm), g in df.groupby(["_aspect_bin", "chip_arm"]):
        if ab == "unknown":
            continue
        parts.append(g.sample(min(per_stratum, len(g)), random_state=seed))
    return pd.concat(parts).drop(columns="_aspect_bin")


# --------------------------------------------------------------------------- #
# QA sheet.
# --------------------------------------------------------------------------- #
def build_qa_sheet(
    records: list[GeometryRecord],
    out_dir: Path,
    tile_px: int = 220,
) -> Path:
    """Render a static HTML QA sheet with each sampled crop, the ROI polygon and
    context ring overlaid (as an overlay-only PNG so the crop itself stays clean),
    grouped by stratum x CRS. Human eyeball check: no crosshair, correct FoV,
    correct ROI placement, both CRS present."""
    from PIL import Image, ImageDraw

    qa_dir = out_dir / "qa"
    tiles_dir = qa_dir / "tiles"
    tiles_dir.mkdir(parents=True, exist_ok=True)

    rows_html: list[str] = []
    by_group: dict[tuple[str, str, str], list[GeometryRecord]] = {}
    for r in records:
        by_group.setdefault((r.chip_arm, r.area_bin, r.raster_crs), []).append(r)

    for (arm, ab, crs), recs in sorted(by_group.items()):
        rows_html.append(
            f"<h2>{arm} | {ab} | {crs} "
            f"<small>({len(recs)} sampled, showing up to 8)</small></h2>"
            "<div class='row'>"
        )
        for r in recs[:8]:
            png = out_dir / r.png_relpath
            if not png.exists():
                continue
            with Image.open(png) as im:
                im = im.convert("RGB")
                base = im.resize((tile_px, tile_px))
                ov = base.copy()
                draw = ImageDraw.Draw(ov)
                sx = tile_px / r.out_px
                roi = [(p[0] * sx, p[1] * sx) for p in json.loads(r.roi_px)]
                ctx = [(p[0] * sx, p[1] * sx) for p in json.loads(r.context_px)]
                draw.polygon(ctx, outline=(0, 200, 255))
                draw.polygon(roi, outline=(255, 60, 60))
            tile_name = f"{r.chip_sha[:16]}.png"
            ov.save(tiles_dir / tile_name)
            rows_html.append(
                "<figure>"
                f"<img src='tiles/{tile_name}' width='{tile_px}' height='{tile_px}'>"
                f"<figcaption>{r.anchor_id[-12:]} {r.capture_date}<br>"
                f"area={r.source_area_m2:.1f}m² roi={r.roi_edge_m:.1f}m<br>"
                f"fov={r.fov_ground_w_m:.1f}x{r.fov_ground_h_m:.1f}m "
                f"err={r.center_reproj_err_px:.2f}px</figcaption>"
                "</figure>"
            )
        rows_html.append("</div>")

    geom_used = records[0].crop_geometry if records else CROP_GEOMETRY_VERSION
    roi_desc = (
        "Red = nominal ROI square (sqrt(area), clamped to FoV)."
        if geom_used == CROP_GEOMETRY_VERSION
        else "Red = nominal ROI area-preserving rectangle (sidecar aspect, clamped to FoV)."
    )
    html = (
        "<!doctype html><meta charset='utf-8'><title>R1 marker-free crops QA</title>"
        "<style>body{font-family:sans-serif;background:#111;color:#eee;margin:16px}"
        ".row{display:flex;flex-wrap:wrap;gap:10px}figure{margin:0;font-size:11px;"
        "text-align:center}img{border:1px solid #444;image-rendering:pixelated}"
        "figcaption{color:#bbb}h2{border-bottom:1px solid #333;margin-top:24px}"
        "small{color:#888;font-weight:normal}</style>"
        f"<h1>R1 marker-free crops -- {geom_used}</h1>"
        f"<p>{roi_desc} "
        "Cyan = context ring outer boundary. Overlay drawn on a copy; the "
        "stored crop PNG carries no overlay and no crosshair.</p>"
        + "".join(rows_html)
    )
    out_html = qa_dir / "qa_sheet.html"
    out_html.write_text(html)
    return out_html


def concentric_rect_iou(ex1: float, ey1: float, ex2: float, ey2: float) -> float:
    """IoU of two axis-aligned rectangles sharing the same centre. v1's square
    and v2's rectangle are both centred on the same target centroid, so the
    overlap is simply the per-axis minimum -- no offset geometry needed."""
    ox = max(0.0, min(ex1, ex2))
    oy = max(0.0, min(ey1, ey2))
    inter = ox * oy
    union = ex1 * ey1 + ex2 * ey2 - inter
    return inter / union if union > 0 else 0.0


def build_cropgeo_v2_qa_sheet(
    pairs: list[tuple[GeometryRecord, GeometryRecord, float]],
    out_dir: Path,
    tile_px: int = 220,
) -> tuple[Path, dict]:
    """QA sheet for r1_cropgeo_v2: red = v2 rectangle ROI, yellow = v1 square ROI
    (comparison overlay), cyan = v2 context ring. ``pairs`` = ``(v2_record,
    v1_record, iou)`` for the same underlying frame, grouped by (aspect_bin,
    chip_arm). Reads existing PNGs from the SHARED v1 crop tree -- this
    function never renders; a missing PNG is skipped, not fabricated. Returns
    the HTML path and a stats dict (also written to
    ``qa/cropgeo_v2_qa_stats.json``: IoU + clamp-loss distributions per cell).
    """
    from PIL import Image, ImageDraw

    qa_dir = out_dir / "qa"
    tiles_dir = qa_dir / "tiles_cropgeo_v2"
    tiles_dir.mkdir(parents=True, exist_ok=True)

    def aspect_of(v2: GeometryRecord) -> float:
        # Reconstruct the rendered aspect ratio from the recorded rectangle
        # edges -- works whether the ROI came from the sidecar or the fallback.
        lo = min(v2.roi_edge_x_m, v2.roi_edge_y_m)
        hi = max(v2.roi_edge_x_m, v2.roi_edge_y_m)
        return hi / lo if lo > 0 else float("nan")

    by_group: dict[tuple[str, str], list[tuple[GeometryRecord, GeometryRecord, float]]] = {}
    for v2, v1, iou in pairs:
        ab = aspect_bin_label(aspect_of(v2))
        by_group.setdefault((ab, v2.chip_arm), []).append((v2, v1, iou))

    rows_html: list[str] = []
    stats: dict[str, Any] = {}
    all_iou: list[float] = []
    all_loss: list[float] = []
    all_oob = 0
    for (ab, arm), items in sorted(by_group.items()):
        ious = [it[2] for it in items]
        losses = [it[0].roi_clamp_loss_frac for it in items]
        oob = sum(1 for it in items if not it[0].roi_in_bounds)
        all_iou.extend(ious)
        all_loss.extend(losses)
        all_oob += oob
        stats[f"{ab}|{arm}"] = {
            "n": len(items),
            "iou_mean": float(np.mean(ious)),
            "iou_p10": float(np.percentile(ious, 10)),
            "iou_p50": float(np.percentile(ious, 50)),
            "iou_p90": float(np.percentile(ious, 90)),
            "clamp_loss_mean": float(np.mean(losses)),
            "clamp_loss_max": float(np.max(losses)),
            "roi_out_of_bounds": oob,
        }
        rows_html.append(
            f"<h2>aspect {ab} | {arm} <small>({len(items)} sampled, "
            f"IoU mean={np.mean(ious):.3f}, showing up to 8)</small></h2>"
            "<div class='row'>"
        )
        for v2, v1, iou in items[:8]:
            png = out_dir / v2.png_relpath
            if not png.exists():
                continue
            with Image.open(png) as im:
                im = im.convert("RGB")
                base = im.resize((tile_px, tile_px))
                ov = base.copy()
                draw = ImageDraw.Draw(ov)
                sx = tile_px / v2.out_px
                roi2 = [(p[0] * sx, p[1] * sx) for p in json.loads(v2.roi_px)]
                roi1 = [(p[0] * sx, p[1] * sx) for p in json.loads(v1.roi_px)]
                ctx2 = [(p[0] * sx, p[1] * sx) for p in json.loads(v2.context_px)]
                draw.polygon(ctx2, outline=(0, 200, 255))
                draw.polygon(roi1, outline=(230, 210, 30))
                draw.polygon(roi2, outline=(255, 60, 60))
            tile_name = f"{v2.chip_sha[:16]}.png"
            ov.save(tiles_dir / tile_name)
            rows_html.append(
                "<figure>"
                f"<img src='tiles_cropgeo_v2/{tile_name}' width='{tile_px}' height='{tile_px}'>"
                f"<figcaption>{v2.anchor_id[-12:]}<br>"
                f"iou={iou:.3f} loss={v2.roi_clamp_loss_frac:.3f} axis={v2.roi_long_axis}<br>"
                f"v2 {v2.roi_edge_x_m:.1f}x{v2.roi_edge_y_m:.1f}m "
                f"v1 {v1.roi_edge_m:.1f}m sq</figcaption>"
                "</figure>"
            )
        rows_html.append("</div>")

    stats["overall"] = {
        "n": len(all_iou),
        "iou_mean": float(np.mean(all_iou)) if all_iou else float("nan"),
        "iou_p50": float(np.percentile(all_iou, 50)) if all_iou else float("nan"),
        "clamp_loss_frac_gt_0": float(np.mean([x > 0 for x in all_loss])) if all_loss else 0.0,
        "roi_out_of_bounds": all_oob,
    }

    html = (
        "<!doctype html><meta charset='utf-8'><title>cropgeo_v2 QA</title>"
        "<style>body{font-family:sans-serif;background:#111;color:#eee;margin:16px}"
        ".row{display:flex;flex-wrap:wrap;gap:10px}figure{margin:0;font-size:11px;"
        "text-align:center}img{border:1px solid #444;image-rendering:pixelated}"
        "figcaption{color:#bbb}h2{border-bottom:1px solid #333;margin-top:24px}"
        "small{color:#888;font-weight:normal}</style>"
        f"<h1>r1_cropgeo_v2 QA -- {CROP_GEOMETRY_VERSION_V2}</h1>"
        "<p>Red = v2 area-preserving rectangle ROI. Yellow = v1 sqrt(area) "
        "square ROI (comparison). Cyan = v2 context ring. The crop PNG is the "
        "SHARED v1 crop -- never re-rendered for v2.</p>"
        + "".join(rows_html)
    )
    out_html = qa_dir / "qa_sheet_cropgeo_v2.html"
    out_html.write_text(html)
    stats_path = qa_dir / "cropgeo_v2_qa_stats.json"
    stats_path.write_text(json.dumps(stats, indent=2, sort_keys=True))
    return out_html, stats


# --------------------------------------------------------------------------- #
# Build driver.
# --------------------------------------------------------------------------- #
def run(args: argparse.Namespace) -> int:
    manifest_path = Path(args.manifest)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    crop_geometry = CROP_GEOMETRY_VERSION_V2 if args.crop_geometry == "v2" else CROP_GEOMETRY_VERSION

    df = load_manifest(manifest_path, check_sha=not args.no_sha_check)
    df["area_bin"] = df["source_area_m2"].map(area_bin_label)
    print(f"[R1] manifest {len(df)} rows loaded (crop_geometry={crop_geometry})", flush=True)

    sidecar_missing_count = 0
    if crop_geometry == CROP_GEOMETRY_VERSION_V2:
        sidecar_path = Path(args.sidecar)
        sidecar = load_sidecar(sidecar_path, check_sha=not args.no_sha_check)
        print(f"[R1] sidecar {len(sidecar)} rows loaded", flush=True)
        join_cols = [
            "anchor_id", "source_width_m", "source_height_m",
            "areamatched_long_m", "areamatched_short_m", "aspect_ratio",
        ]
        df = df.merge(sidecar[join_cols], on="anchor_id", how="left", validate="many_to_one")
        sidecar_missing_count = int(df["areamatched_long_m"].isna().sum())
        if sidecar_missing_count:
            print(
                f"[R1][WARN] {sidecar_missing_count} manifest rows have no sidecar "
                "match -- falling back to the v1 square ROI for those "
                "(recorded as roi_source=fallback_square_v2)",
                flush=True,
            )
        else:
            print("[R1] sidecar join: 0 missing (all anchors matched)", flush=True)

    if args.qa_mode == "aspect_arm":
        if crop_geometry != CROP_GEOMETRY_VERSION_V2:
            raise SystemExit("[R1][FATAL] --qa-mode aspect_arm requires --crop-geometry v2")
        if args.sample_per_stratum > 0:
            df = stratified_sample_by_aspect(df, args.sample_per_stratum, args.seed)
            print(
                f"[R1] aspect-stratified sample: {len(df)} frames "
                f"({args.sample_per_stratum}/stratum x aspect_bin x chip_arm)",
                flush=True,
            )
    elif args.sample_per_stratum > 0:
        df = stratified_sample(df, args.sample_per_stratum, args.seed)
        print(
            f"[R1] stratified sample: {len(df)} frames "
            f"({args.sample_per_stratum}/stratum x {df['chip_arm'].nunique()} arms)",
            flush=True,
        )
    elif args.limit:
        df = df.head(args.limit)
        print(f"[R1] limited to first {len(df)} frames", flush=True)

    tfx = _Transformers()

    # Phase 1: geometry records (pure; always recomputed, idempotent).
    print("[R1] computing geometry records ...", flush=True)
    records: list[GeometryRecord] = []
    v1_comparisons: list[GeometryRecord] = []  # only populated in aspect_arm QA mode
    for _, row in df.iterrows():
        sidecar_row = row if crop_geometry == CROP_GEOMETRY_VERSION_V2 else None
        records.append(
            compute_geometry_record(row, tfx, crop_geometry=crop_geometry, sidecar_row=sidecar_row)
        )
        if args.qa_mode == "aspect_arm":
            v1_comparisons.append(
                compute_geometry_record(row, tfx, crop_geometry=CROP_GEOMETRY_VERSION, sidecar_row=None)
            )

    # Write sharded geometry index (atomic per shard) -- version-specific subdir;
    # v1's dirname is unchanged/frozen, v2 writes alongside it under the SAME out_dir.
    idx_dir = out_dir / CROP_GEOMETRY_INDEX_DIRNAME[crop_geometry]
    idx_dir.mkdir(parents=True, exist_ok=True)
    by_shard: dict[str, list[dict]] = {}
    for r in records:
        by_shard.setdefault(r.chip_sha[:2], []).append(asdict(r))
    if not args.qa_only:
        for shard, rows in by_shard.items():
            # merge with any existing shard rows (dedup by chip_sha)
            shard_path = idx_dir / f"{shard}.parquet"
            new_df = pd.DataFrame(rows)
            if shard_path.exists():
                old = pd.read_parquet(shard_path)
                merged = pd.concat([old, new_df]).drop_duplicates(
                    subset=["chip_sha"], keep="last"
                )
            else:
                merged = new_df
            tmp = shard_path.with_suffix(".parquet.tmp")
            merged.to_parquet(tmp, index=False)
            os.replace(tmp, shard_path)
        print(f"[R1] wrote geometry index ({idx_dir.name}): {len(by_shard)} shards", flush=True)

    # Reconciliation of derived geometry.
    center_errs = np.array([r.center_reproj_err_px for r in records])
    roi_oob = sum(1 for r in records if not r.roi_in_bounds)
    n_3857 = sum(1 for r in records if r.raster_crs == "EPSG:3857")
    n_4326 = sum(1 for r in records if r.raster_crs == "EPSG:4326")
    print(
        f"[R1] center reproj err px: max={center_errs.max():.4f} "
        f"p99={np.percentile(center_errs, 99):.4f} "
        f"mean={center_errs.mean():.4f}  | roi_out_of_bounds={roi_oob} "
        f"| crs 3857={n_3857} 4326={n_4326}",
        flush=True,
    )
    if center_errs.max() >= 1.0:
        print(
            f"[R1][WARN] max center reproj err {center_errs.max():.4f}px >= 1px "
            "-- investigate before full run",
            flush=True,
        )

    roi_source_counts: dict[str, int] = {}
    clamp_loss_stats: dict[str, float] = {}
    if crop_geometry == CROP_GEOMETRY_VERSION_V2:
        for r in records:
            roi_source_counts[r.roi_source] = roi_source_counts.get(r.roi_source, 0) + 1
        losses = np.array([r.roi_clamp_loss_frac for r in records])
        clamp_loss_stats = {
            "max": float(losses.max()) if len(losses) else 0.0,
            "p99": float(np.percentile(losses, 99)) if len(losses) else 0.0,
            "mean": float(losses.mean()) if len(losses) else 0.0,
            "frac_gt_0": float((losses > 0).mean()) if len(losses) else 0.0,
        }
        print(
            f"[R1] v2 roi_source={roi_source_counts} clamp_loss_frac: "
            f"max={clamp_loss_stats['max']:.4f} mean={clamp_loss_stats['mean']:.4f} "
            f"frac>0={clamp_loss_stats['frac_gt_0']*100:.2f}%",
            flush=True,
        )

    # Phase 2: render crops (resume: skip existing non-empty PNG).
    # v2 NEVER renders -- crop pixels are byte-identical to v1 and are read from
    # the shared out_dir crops/ tree (module docstring "v2 refs v1 pixels" contract).
    do_render = (crop_geometry == CROP_GEOMETRY_VERSION) and not args.qa_only
    rendered = skipped = errors = 0
    err_samples: list[str] = []
    if do_render:
        sha_to_tiff = dict(zip(df["src_tiff_sha256"], df["src_tiff_path"]))
        n = len(records)
        for i, r in enumerate(records):
            dest = out_dir / r.png_relpath
            if dest.exists() and dest.stat().st_size > 0:
                skipped += 1
                continue
            tiff = str(sha_to_tiff[r.chip_sha])
            fov_m, min_out = arm_render_params(r.chip_arm)
            win = compute_crop_window(
                r.src_width_px, r.src_height_px, fov_m, min_output_px=min_out
            )
            try:
                img = render_marker_free_crop(tiff, win)
                _atomic_save_png(img, dest)
                rendered += 1
            except Exception as exc:  # noqa: BLE001
                errors += 1
                if len(err_samples) < 10:
                    err_samples.append(f"{r.chip_sha[:12]}: {exc}")
            if (i + 1) % 500 == 0:
                print(
                    f"[R1]   render {i+1}/{n} rendered={rendered} "
                    f"skipped={skipped} errors={errors}",
                    flush=True,
                )
        print(
            f"[R1] render done: rendered={rendered} skipped={skipped} "
            f"errors={errors}",
            flush=True,
        )
        if err_samples:
            print("[R1] error samples:", flush=True)
            for e in err_samples:
                print("   ", e, flush=True)
    elif crop_geometry == CROP_GEOMETRY_VERSION_V2:
        n_have_png = sum(1 for r in records if (out_dir / r.png_relpath).exists())
        print(
            f"[R1] v2: no render (reuses shared v1 pixels); "
            f"{n_have_png}/{len(records)} referenced PNGs already exist on disk",
            flush=True,
        )

    # QA sheet.
    qa_path = None
    qa_stats = None
    if args.qa_mode == "aspect_arm" and (args.qa or args.qa_only or args.sample_per_stratum > 0):
        pairs_with_iou = [
            (
                v2, v1,
                concentric_rect_iou(v2.roi_edge_x_m, v2.roi_edge_y_m, v1.roi_edge_m, v1.roi_edge_m),
            )
            for v2, v1 in zip(records, v1_comparisons)
        ]
        qa_path, qa_stats = build_cropgeo_v2_qa_sheet(pairs_with_iou, out_dir)
        print(f"[R1] cropgeo_v2 QA sheet: {qa_path}", flush=True)
    elif args.qa or args.qa_only or args.sample_per_stratum > 0:
        qa_path = build_qa_sheet(records, out_dir)
        print(f"[R1] QA sheet: {qa_path}", flush=True)

    # Status lock / provenance (version-specific filename -- v1/v2 share out_dir).
    status = {
        "crop_geometry_version": crop_geometry,
        "context_multiplier": CONTEXT_MULTIPLIER,
        "metric_crs": METRIC_CRS,
        "manifest": str(manifest_path),
        "manifest_sha256_expected": EXPECTED_MANIFEST_SHA,
        "n_records": len(records),
        "rendered": rendered,
        "skipped": skipped,
        "errors": errors,
        "center_reproj_err_px": {
            "max": float(center_errs.max()),
            "p99": float(np.percentile(center_errs, 99)),
            "mean": float(center_errs.mean()),
        },
        "roi_out_of_bounds": roi_oob,
        "crs_counts": {"EPSG:3857": n_3857, "EPSG:4326": n_4326},
        "qa_sheet": str(qa_path) if qa_path else None,
        "sample_per_stratum": args.sample_per_stratum,
        "qa_mode": args.qa_mode,
    }
    if crop_geometry == CROP_GEOMETRY_VERSION_V2:
        status.update(
            {
                "sidecar": str(args.sidecar),
                "sidecar_sha256_expected": EXPECTED_SIDECAR_SHA,
                "sidecar_missing_rows": sidecar_missing_count,
                "roi_source_counts": roi_source_counts,
                "roi_clamp_loss_frac": clamp_loss_stats,
                "qa_stats": qa_stats,
                "png_source_contract": (
                    "r1_cropgeo_v2 crop pixels are byte-identical to "
                    f"{CROP_GEOMETRY_VERSION} for the same chip_sha (raster FoV, "
                    "centred window, 256px BICUBIC upsample, no marker are all "
                    "unchanged -- only the ROI/context definition differs). This "
                    "run never renders; PNGs are read from the shared out_dir "
                    "crops/ tree written by the v1 generator run."
                ),
            }
        )
    # --qa-only writes to the separate QA status file (QA_STATUS_FILENAME),
    # NEVER to the production status file (STATUS_FILENAME) -- qa-only means
    # "skip rendering + index write", not "safe to run against real data
    # without touching provenance". See the QA_STATUS_FILENAME comment above.
    status_filename = (
        QA_STATUS_FILENAME[crop_geometry] if args.qa_only else STATUS_FILENAME[crop_geometry]
    )
    status_path = out_dir / status_filename
    status_path.write_text(json.dumps(status, indent=2))
    print(f"[R1] wrote {status_path}", flush=True)
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    p.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    p.add_argument(
        "--crop-geometry",
        choices=["v1", "v2"],
        default="v1",
        help="ROI/context geometry: v1 = sqrt(area) square (default, unchanged); "
             "v2 = r1_cropgeo_v2 sidecar-aspect area-preserving rectangle. The "
             "crop RASTER (FoV/window/upsample/no-marker) is identical either "
             "way -- v2 never re-renders, it only writes a differently-shaped "
             "ROI/context annotation referencing the same PNGs.",
    )
    p.add_argument(
        "--sidecar",
        default=str(DEFAULT_SIDECAR),
        help="R0.1 footprint sidecar parquet (only read for --crop-geometry v2)",
    )
    p.add_argument(
        "--qa-mode",
        choices=["area_crs", "aspect_arm"],
        default="area_crs",
        help="QA/sampling stratification: area_crs = v1's area_bin x CRS "
             "(default); aspect_arm = v2's aspect_bin x chip_arm, drawing a "
             "v1-vs-v2 IoU comparison overlay (requires --crop-geometry v2).",
    )
    p.add_argument(
        "--sample-per-stratum",
        type=int,
        default=0,
        help=">0: only N frames per stratum -- (arm x area_bin x CRS) under "
             "--qa-mode area_crs, or (aspect_bin x chip_arm) under aspect_arm "
             "(QA pilot)",
    )
    p.add_argument("--limit", type=int, default=0, help="cap to first N frames (debug)")
    p.add_argument("--seed", type=int, default=20260719)
    p.add_argument("--qa", action="store_true", help="also build the QA sheet")
    p.add_argument(
        "--qa-only",
        action="store_true",
        help="skip rendering + index write; build the QA sheet from existing crops",
    )
    p.add_argument(
        "--no-sha-check",
        action="store_true",
        help="skip the manifest/sidecar sha+row-count lock (synthetic fixtures only)",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    return run(parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
