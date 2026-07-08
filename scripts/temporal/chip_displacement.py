#!/usr/bin/env python3
"""GEHI cross-vintage displacement primitives (translation estimation +
contamination geometry) for the displacement/contamination audit.

Motivation (handoff ``/tmp/handoff_gehi_displacement_audit_2026-07-06.md``;
architecture ``docs/geid_temporal_anchor_presence_architecture.md`` §4). Every
GEHI vintage chip for one anchor shares the *same nominal* EPSG:4326 tile-snapped
bbox — the geotransform is identical across dates, so any real cross-vintage
misregistration lives entirely in the pixel content and is invisible to the
georeferencing. This module measures that content-level translation, scores how
much to trust each estimate (so a low-texture / low-zoom chip is *flagged*, never
misread as a large real shift — handoff 坑 §zoom), converts it to true ground
metres, and answers the decision question behind PRD D18: does the offset push
the installation footprint out of a scoring crop of size ``crop_size_m`` centred
on the nominal anchor? The banked-96 m chip tolerates several metres; the ISSUE-04
``chip_geom_v2_tight12`` re-render (12 m) does not.

Design split: everything here is pure/array-level and unit-tested
(``tests/temporal/test_chip_displacement.py``). Disk reads + reprojection onto a
common metric grid live in the ``read_*`` / ``reproject_*`` helpers at the bottom
(rasterio/pyproj imported lazily) and are exercised by
``scripts/temporal/audit_gehi_displacement.py``, not the unit suite.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy import ndimage

from scripts.temporal.chip_geometry import (
    contain_crop_to_footprint,
    resolve_chip_geometry,
)

# --------------------------------------------------------------------------- #
# Reliability thresholds (defaults; the audit CLI can override).              #
# --------------------------------------------------------------------------- #
# Gradient-magnitude std (on 0..1 intensity) below which a chip carries no
# registrable structure (blown-out / blank / missing) — a safety net.
DEFAULT_MIN_TEXTURE_STD = 0.006
# Phase-correlation peak-to-sidelobe ratio below which the lock is untrustworthy.
# Calibrated on real GEHI pairs at the 650x650 audit grid (2026-07-06): the noise
# floor (real chip vs white-noise / shuffled-self) sits at PSR~7, genuine
# same-anchor locks at PSR 8.5-26, a perfect self-roll at ~4900. 8.0 clears the
# floor while admitting the weakest real locks. The floor scales with surface
# size, so the audit also reports contamination at PSR cuts {8,10,12} to show the
# conclusion is stable across the thin floor-to-lock margin.
DEFAULT_MIN_PSR = 8.0
# PSR mapped to alignment_score == 1.0 (a comfortably-locked estimate).
_PSR_FULL = 12.0
# Implausible-offset guard: a lock this far off (px) is a registration failure,
# not real misregistration — the audit converts this to metres via GSD.
DEFAULT_MAX_OFFSET_PX = 180.0


@dataclass(frozen=True)
class ShiftResult:
    """Estimated translation of ``mov`` content relative to ``ref``.

    ``dy_px`` / ``dx_px`` are how far the moving chip's content sits below / right
    of the reference (numpy row/col order), i.e. the *displacement*, which is the
    negation of skimage's registration shift. ``psr`` is the phase-correlation
    peak-to-sidelobe ratio (the content-robust trust metric); ``alignment_score``
    in [0, 1] is ``psr`` squashed for a bounded report. ``ok`` folds the texture
    floor, the PSR floor and the max-offset guard; ``reason`` explains a False.
    """

    dy_px: float
    dx_px: float
    offset_px: float
    psr: float
    alignment_score: float
    texture_std: float
    ok: bool
    reason: str = ""


# --------------------------------------------------------------------------- #
# Array preprocessing                                                         #
# --------------------------------------------------------------------------- #

def to_gray(arr: np.ndarray) -> np.ndarray:
    """Collapse an RGB(A) chip to a 2-D float gray image on a 0..1 scale.

    Accepts ``(H, W)``, band-last ``(H, W, C)`` or band-first ``(C, H, W)``.
    Intensity is divided by 255 when the input looks like 8-bit — a *fixed* scale,
    never per-image min-max (that would amplify a blank chip's noise to full range
    and defeat the texture floor).
    """
    a = np.asarray(arr, dtype=np.float64)
    if a.ndim == 3:
        band_first = a.shape[0] in (3, 4)
        band_last = a.shape[-1] in (3, 4)
        if band_last and not band_first:
            a = a[..., :3]
        else:  # band-first, or ambiguous -> prefer band-first (rasterio read order)
            a = np.moveaxis(a[:3], 0, -1)
        a = 0.299 * a[..., 0] + 0.587 * a[..., 1] + 0.114 * a[..., 2]
    if a.max() > 1.5:
        a = a / 255.0
    return a


def _sobel_mag(gray: np.ndarray) -> np.ndarray:
    """Sobel gradient magnitude — the whitened representation registration and
    NCC both run on, making them invariant to cross-vintage exposure/gain."""
    gy = ndimage.sobel(gray, axis=0, mode="reflect")
    gx = ndimage.sobel(gray, axis=1, mode="reflect")
    return np.hypot(gx, gy)


def _hann2d(shape: tuple[int, int]) -> np.ndarray:
    wy = np.hanning(shape[0])
    wx = np.hanning(shape[1])
    return np.outer(wy, wx)


# --------------------------------------------------------------------------- #
# Core estimator                                                              #
# --------------------------------------------------------------------------- #

def _psr(corr: np.ndarray, peak_yx: tuple[int, int], exclude: int = 5) -> float:
    """Peak-to-sidelobe ratio: how far the peak stands above the background of the
    correlation surface (peak excluded). Content-robust — a sharp dominant peak
    scores high regardless of absolute scene similarity."""
    mask = np.ones_like(corr, dtype=bool)
    y0, x0 = peak_yx
    mask[max(0, y0 - exclude):y0 + exclude + 1, max(0, x0 - exclude):x0 + exclude + 1] = False
    bg = corr[mask]
    return float((corr[y0, x0] - bg.mean()) / (bg.std() + 1e-12))


def estimate_shift(
    ref: np.ndarray,
    mov: np.ndarray,
    *,
    upsample: int = 10,
    min_texture_std: float = DEFAULT_MIN_TEXTURE_STD,
    min_psr: float = DEFAULT_MIN_PSR,
    max_offset_px: float = DEFAULT_MAX_OFFSET_PX,
) -> ShiftResult:
    """Estimate the sub-pixel translation of ``mov`` relative to ``ref``.

    Both inputs must already be co-gridded (same CRS/extent/shape — the caller
    reprojects onto a common metric grid). RGB is collapsed to gray; a Hann window
    then Sobel-gradient pre-whiten both, making the estimate invariant to
    cross-vintage exposure/gain. The sub-pixel displacement comes from skimage's
    upsampled ``phase_cross_correlation`` (negated to a displacement); trust comes
    from the PSR of the phase-correlation surface — real GEHI locks sit well above
    uncorrelated pairs (calibrated 2026-07-06), which an absolute NCC cannot
    separate.
    """
    from skimage.registration import phase_cross_correlation

    g_ref = to_gray(ref)
    g_mov = to_gray(mov)
    # Co-grid guard: crop both to the common shape if they differ by a row/col.
    h = min(g_ref.shape[0], g_mov.shape[0])
    w = min(g_ref.shape[1], g_mov.shape[1])
    g_ref = g_ref[:h, :w]
    g_mov = g_mov[:h, :w]

    grad_ref = _sobel_mag(g_ref)
    grad_mov = _sobel_mag(g_mov)
    texture_std = float(grad_ref.std())
    if texture_std < min_texture_std:
        return ShiftResult(0.0, 0.0, 0.0, 0.0, 0.0, texture_std, False, "low-texture")

    win = _hann2d((h, w))
    a = grad_ref * win
    b = grad_mov * win

    # Sub-pixel displacement (skimage): shift registers mov ONTO ref -> negate.
    shift, _err, _phase = phase_cross_correlation(a, b, upsample_factor=upsample, normalization="phase")
    dy_px = -float(shift[0])
    dx_px = -float(shift[1])
    offset_px = float(np.hypot(dy_px, dx_px))

    # PSR from the (integer-grid) phase-correlation surface.
    F = np.fft.fft2(a)
    G = np.fft.fft2(b)
    R = F * np.conj(G)
    R /= np.abs(R) + 1e-8
    corr = np.fft.fftshift(np.real(np.fft.ifft2(R)))
    peak_yx = np.unravel_index(int(np.argmax(corr)), corr.shape)
    psr = _psr(corr, peak_yx)
    alignment_score = float(min(1.0, max(0.0, psr / _PSR_FULL)))

    if offset_px > max_offset_px:
        return ShiftResult(dy_px, dx_px, offset_px, psr, alignment_score, texture_std, False, "max-offset")
    ok = psr >= min_psr
    reason = "" if ok else "low-psr"
    return ShiftResult(dy_px, dx_px, offset_px, psr, alignment_score, texture_std, ok, reason)


# --------------------------------------------------------------------------- #
# Pixel -> metre                                                              #
# --------------------------------------------------------------------------- #

def shift_px_to_m(
    dy_px: float, dx_px: float, *, gsd_y_m: float, gsd_x_m: float
) -> tuple[float, float, float]:
    """Convert a pixel displacement to true ground metres on a metric grid.

    Returns ``(dx_m, dy_m, offset_m)`` where ``offset_m`` is the Euclidean
    magnitude — the ``best_offset_m`` the architecture doc (§4.4) asked for.
    """
    dx_m = float(dx_px) * float(gsd_x_m)
    dy_m = float(dy_px) * float(gsd_y_m)
    offset_m = float(np.hypot(dx_m, dy_m))
    return dx_m, dy_m, offset_m


# --------------------------------------------------------------------------- #
# Contamination geometry                                                      #
# --------------------------------------------------------------------------- #

def panel_exits_crop(
    offset_m: float,
    *,
    footprint_m: float,
    crop_size_m: float,
    mode: str = "partial",
) -> bool:
    """Does a displacement of ``offset_m`` push the footprint out of the crop?

    The crop of size ``crop_size_m`` is centred on the *nominal* anchor; the panel
    (extent ``footprint_m``, half-extent ``footprint_m/2``) actually sits at
    nominal + offset. ``mode="partial"`` → any part of the footprint leaves the
    crop (offset > (crop − footprint)/2). ``mode="full"`` → the whole footprint
    leaves (offset > (crop + footprint)/2). A footprint wider than the crop is
    already partially clipped at zero offset.
    """
    half_crop = 0.5 * float(crop_size_m)
    half_fp = 0.5 * float(footprint_m)
    if mode == "partial":
        return float(offset_m) > (half_crop - half_fp)
    if mode == "full":
        return float(offset_m) > (half_crop + half_fp)
    raise ValueError(f"mode must be 'partial' or 'full', got {mode!r}")


def footprint_retained_fraction(
    dx_m: float,
    dy_m: float,
    *,
    footprint_w_m: float,
    footprint_h_m: float,
    crop_size_m: float,
) -> float:
    """Fraction of the installation footprint that stays inside the scoring crop.

    The square crop (side ``crop_size_m``) is centred on the nominal anchor; the
    panel (``footprint_w_m`` x ``footprint_h_m``) sits at the measured offset
    ``(dx_m, dy_m)``. Returns the retained-area fraction in [0, 1] — a directional,
    decision-useful contamination measure that (unlike a binary partial/full flag)
    does not read "any offset clips" when the crop is floored to a large footprint.
    """
    half_crop = 0.5 * float(crop_size_m)

    def _overlap(center: float, half: float) -> float:
        lo = max(-half_crop, center - half)
        hi = min(half_crop, center + half)
        return max(0.0, hi - lo)

    ox = _overlap(float(dx_m), 0.5 * float(footprint_w_m))
    oy = _overlap(float(dy_m), 0.5 * float(footprint_h_m))
    area = float(footprint_w_m) * float(footprint_h_m)
    return (ox * oy) / area if area > 0 else 0.0


def crop_size_for_geometry(
    geometry_version: str,
    *,
    footprint_w_m: float,
    footprint_h_m: float,
    search_radius_m: float,
    chip_size_m: float,
) -> float:
    """The realised scoring-crop extent for a named chip geometry, per target.

    Faithful to ``chip_geometry`` (``ensure_single_target_review_png``):
    ``crop = min(chip_size_m, max(min_crop_size_m, 2*radius*context_mult))`` then
    floored to the footprint by the ISSUE-19 ``contain_crop_to_footprint`` guard.
    """
    geom = resolve_chip_geometry(geometry_version)
    crop = min(
        float(chip_size_m),
        max(geom.min_crop_size_m, 2.0 * float(search_radius_m) * geom.crop_context_multiplier),
    )
    return contain_crop_to_footprint(
        crop,
        source_width_m=footprint_w_m,
        source_height_m=footprint_h_m,
        chip_size_m=chip_size_m,
    )


# --------------------------------------------------------------------------- #
# I/O + common-grid reprojection (rasterio/pyproj lazy; not in the unit suite) #
# --------------------------------------------------------------------------- #

def reproject_to_grid(path, *, dst_crs, dst_transform, dst_shape):
    """Read a chip and warp it onto an explicit metric target grid.

    Because the nominal geotransforms are identical across vintages, warping onto a
    shared grid does NOT remove the content misregistration — it only puts every
    chip on the same pixels so ``estimate_shift`` can recover the residual shift.
    Returns an ``(H, W)`` gray float array on the target grid.
    """
    import rasterio
    from rasterio.warp import Resampling, reproject

    with rasterio.open(path) as ds:
        src = ds.read(indexes=list(range(1, min(ds.count, 3) + 1)))  # up to RGB
        src_crs = ds.crs
        src_transform = ds.transform
    dst = np.zeros((src.shape[0], dst_shape[0], dst_shape[1]), dtype=np.float32)
    for b in range(src.shape[0]):
        reproject(
            source=src[b],
            destination=dst[b],
            src_transform=src_transform,
            src_crs=src_crs,
            dst_transform=dst_transform,
            dst_crs=dst_crs,
            resampling=Resampling.bilinear,
        )
    return to_gray(np.moveaxis(dst, 0, -1))


def utm_grid_for_bounds(lon_min, lat_min, lon_max, lat_max, *, metric_crs, gsd_m):
    """Build a north-up metric target grid (transform + shape) covering a lon/lat
    bbox at ``gsd_m`` resolution in ``metric_crs``. Returns ``(transform, (H, W),
    (gsd_m, gsd_m))``. Used to put GEHI (4326) and CoJ (3857) chips on one grid."""
    from pyproj import Transformer
    from rasterio.transform import from_origin

    tf = Transformer.from_crs("EPSG:4326", metric_crs, always_xy=True)
    xs, ys = tf.transform([lon_min, lon_max, lon_min, lon_max], [lat_min, lat_min, lat_max, lat_max])
    minx, maxx = min(xs), max(xs)
    miny, maxy = min(ys), max(ys)
    w = int(np.ceil((maxx - minx) / gsd_m))
    h = int(np.ceil((maxy - miny) / gsd_m))
    transform = from_origin(minx, maxy, gsd_m, gsd_m)
    return transform, (h, w), (gsd_m, gsd_m)
