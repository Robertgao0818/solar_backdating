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
R3_FEATURE_CACHE_CONTRACT (PRD §6.4 -- DEFINED here, IMPLEMENTED in R3)
===========================================================================
The R3 DINO embed step caches one pooled ROI feature per crop under the key
``(chip_sha, crop_geometry, backbone_hash, pooling_version)``:

* ``chip_sha``        source raster SHA-256 (this manifest's ``src_tiff_sha256``).
* ``crop_geometry``   ``r1_cropgeo_v1@2026-07-19`` (this script's version; the ROI
                      + context ring the pooling reads are frozen by it).
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

# Area strata for stratified QA sampling (must match the R0 lock's four strata).
AREA_BINS = [(0, 15), (15, 40), (40, 100), (100, float("inf"))]


def area_bin_label(area_m2: float | None) -> str:
    if area_m2 is None or (isinstance(area_m2, float) and math.isnan(area_m2)):
        return "unknown"
    for lo, hi in AREA_BINS:
        if lo <= area_m2 < hi:
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


def _square_corners(cx: float, cy: float, edge: float) -> list[tuple[float, float]]:
    h = edge / 2.0
    # CCW from lower-left in a north-up frame.
    return [(cx - h, cy - h), (cx + h, cy - h), (cx + h, cy + h), (cx - h, cy + h)]


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
    # ROI + context (pixel + lon/lat)
    roi_edge_m: float
    context_edge_m: float
    roi_px: str  # json list of [ox,oy]
    context_px: str
    roi_lonlat: str
    context_lonlat: str
    roi_in_bounds: bool
    png_relpath: str


def compute_geometry_record(row: pd.Series, tfx: _Transformers) -> GeometryRecord:
    """Full per-frame geometry record (pure fn of a manifest row + transformers).

    Also computes the crop-centre reprojection error: reproject the target
    centroid through frame CRS -> source px -> output px and compare to the
    geometric output centre (``out_px/2``). This is the <1 px invariant the
    tests assert for both CRS paths.
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

    # ROI + context squares in UTM -> output px + lon/lat
    area = float(row["source_area_m2"])
    roi_edge = min(fov_m, math.sqrt(area) if area > 0 else 0.0)
    context_edge = min(fov_m, roi_edge * CONTEXT_MULTIPLIER)

    def corners_px_and_lonlat(edge: float):
        px: list[list[float]] = []
        ll: list[list[float]] = []
        utm_to_ll = _get_utm_to_ll(tfx)
        for (ce, cn) in _square_corners(ec, nc, edge):
            fx, fy = tfx.utm_to_frame(ce, cn, frame_crs)
            col, rw = world_to_src_px(fx, fy, tfw)
            ox, oy = src_px_to_out_px(col, rw, win)
            px.append([round(ox, 3), round(oy, 3)])
            clon, clat = utm_to_ll.transform(ce, cn)
            ll.append([round(float(clon), 9), round(float(clat), 9)])
        return px, ll

    roi_px, roi_ll = corners_px_and_lonlat(roi_edge)
    context_px, context_ll = corners_px_and_lonlat(context_edge)
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
        crop_geometry=CROP_GEOMETRY_VERSION,
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
        roi_edge_m=round(roi_edge, 4),
        context_edge_m=round(context_edge, 4),
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

    html = (
        "<!doctype html><meta charset='utf-8'><title>R1 marker-free crops QA</title>"
        "<style>body{font-family:sans-serif;background:#111;color:#eee;margin:16px}"
        ".row{display:flex;flex-wrap:wrap;gap:10px}figure{margin:0;font-size:11px;"
        "text-align:center}img{border:1px solid #444;image-rendering:pixelated}"
        "figcaption{color:#bbb}h2{border-bottom:1px solid #333;margin-top:24px}"
        "small{color:#888;font-weight:normal}</style>"
        f"<h1>R1 marker-free crops -- {CROP_GEOMETRY_VERSION}</h1>"
        "<p>Red = nominal ROI square (sqrt(area), clamped to FoV). "
        "Cyan = context ring outer square. Overlay drawn on a copy; the "
        "stored crop PNG carries no overlay and no crosshair.</p>"
        + "".join(rows_html)
    )
    out_html = qa_dir / "qa_sheet.html"
    out_html.write_text(html)
    return out_html


# --------------------------------------------------------------------------- #
# Build driver.
# --------------------------------------------------------------------------- #
def run(args: argparse.Namespace) -> int:
    manifest_path = Path(args.manifest)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    df = load_manifest(manifest_path, check_sha=not args.no_sha_check)
    df["area_bin"] = df["source_area_m2"].map(area_bin_label)
    print(f"[R1] manifest {len(df)} rows loaded", flush=True)

    if args.sample_per_stratum > 0:
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
    for _, row in df.iterrows():
        records.append(compute_geometry_record(row, tfx))

    # Write sharded geometry index (atomic per shard).
    idx_dir = out_dir / "crop_geometry_index"
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
        print(f"[R1] wrote geometry index: {len(by_shard)} shards", flush=True)

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

    # Phase 2: render crops (resume: skip existing non-empty PNG).
    sha_to_tiff = dict(zip(df["src_tiff_sha256"], df["src_tiff_path"]))
    rendered = skipped = errors = 0
    err_samples: list[str] = []
    if not args.qa_only:
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

    # QA sheet.
    qa_path = None
    if args.qa or args.qa_only or args.sample_per_stratum > 0:
        qa_path = build_qa_sheet(records, out_dir)
        print(f"[R1] QA sheet: {qa_path}", flush=True)

    # Status lock / provenance.
    status = {
        "crop_geometry_version": CROP_GEOMETRY_VERSION,
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
    }
    (out_dir / "_R1_CROPS_STATUS.json").write_text(json.dumps(status, indent=2))
    print(f"[R1] wrote {out_dir/'_R1_CROPS_STATUS.json'}", flush=True)
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    p.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    p.add_argument(
        "--sample-per-stratum",
        type=int,
        default=0,
        help=">0: render only N frames per (arm x area_bin x CRS) stratum (QA pilot)",
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
        help="skip the manifest sha/row-count lock (synthetic fixtures only)",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    return run(parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
