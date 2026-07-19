"""PV-masked phase-correlation translation estimate (PRD §5.2 cascade tier 1).

Mirrors ``scripts.temporal.chip_displacement.estimate_shift``'s algorithm
(same PSR/texture/max-offset thresholds -- calibrated on real GEHI
same-anchor cross-vintage pairs, i.e. exactly this "同域 GEHI" use case, per
that module's docstring) but with the PV polygon + buffer masked out
*before* the Sobel/Hann pre-whitening, so a moved/changed panel can never
drive the correlation lock (§5.2's anti-self-proof rule). Reuses
``chip_displacement``'s pure helper functions read-only (``to_gray``,
``_sobel_mag``, ``_hann2d``, ``_psr``) rather than duplicating them or
patching that shared, frozen module.

The masked region is filled with the *unmasked* region's own mean intensity
(not zero) before the gradient step -- a flat fill contributes ~no Sobel
energy, so it neither introduces a spurious hard edge at the mask boundary
nor lets the (identically-positioned, since both frames are on the same
target-centred grid) masked rectangle correlate with itself.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from scripts.temporal.chip_displacement import (
    DEFAULT_MAX_OFFSET_PX,
    DEFAULT_MIN_PSR,
    DEFAULT_MIN_TEXTURE_STD,
    _hann2d,
    _psr,
    _sobel_mag,
    to_gray,
)

#: PSR at which ``registration_confidence`` saturates to 1.0 -- same
#: squashing convention as ``chip_displacement.ShiftResult.alignment_score``.
PSR_FULL = 12.0


@dataclass(frozen=True)
class RawShift:
    """Masked phase-correlation estimate, pre-schema (internal cascade
    bookkeeping -- not a ``TargetLocalizationObservation`` field type).

    ``texture_std_ref``/``texture_std_mov`` are reported separately
    (deliberate deviation from ``chip_displacement.estimate_shift``, which
    only ever gates on the ref side): a blank/corrupt ``mov`` -- i.e. the
    observation being scored -- is exactly the decontamination signal this
    layer exists to catch (PRD §5.2), whereas a blank/corrupt *reference*
    frame says nothing about the observation and must not gate it.
    """

    dy_px: float
    dx_px: float
    offset_px: float
    psr: float
    texture_std_ref: float
    texture_std_mov: float
    ok: bool
    reason: str


def estimate_shift_masked(
    ref: np.ndarray,
    mov: np.ndarray,
    mask: np.ndarray,
    *,
    upsample: int = 10,
    min_texture_std: float = DEFAULT_MIN_TEXTURE_STD,
    min_psr: float = DEFAULT_MIN_PSR,
    max_offset_px: float = DEFAULT_MAX_OFFSET_PX,
) -> RawShift:
    """PV-masked counterpart of ``chip_displacement.estimate_shift``.

    ``ref``/``mov`` must already be co-gridded (same shape/extent -- the
    caller reprojects via ``chip_displacement.reproject_to_grid`` onto a
    common metric grid, exactly as the unmasked estimator requires).
    ``mask`` is ``True`` = excluded (PV + buffer) pixel, same shape.
    """
    from skimage.registration import phase_cross_correlation

    g_ref = to_gray(ref)
    g_mov = to_gray(mov)
    h = min(g_ref.shape[0], g_mov.shape[0], mask.shape[0])
    w = min(g_ref.shape[1], g_mov.shape[1], mask.shape[1])
    g_ref = g_ref[:h, :w]
    g_mov = g_mov[:h, :w]
    m = mask[:h, :w]
    unmasked = ~m

    fill_ref = float(g_ref[unmasked].mean()) if unmasked.any() else float(g_ref.mean())
    fill_mov = float(g_mov[unmasked].mean()) if unmasked.any() else float(g_mov.mean())
    g_ref = np.where(m, fill_ref, g_ref)
    g_mov = np.where(m, fill_mov, g_mov)

    grad_ref = _sobel_mag(g_ref)
    grad_mov = _sobel_mag(g_mov)
    texture_std_ref = float(grad_ref[unmasked].std()) if unmasked.any() else float(grad_ref.std())
    texture_std_mov = float(grad_mov[unmasked].std()) if unmasked.any() else float(grad_mov.std())
    if texture_std_ref < min_texture_std or texture_std_mov < min_texture_std:
        if texture_std_mov < min_texture_std and texture_std_ref < min_texture_std:
            reason = "low-texture-both"
        elif texture_std_mov < min_texture_std:
            reason = "low-texture-mov"
        else:
            reason = "low-texture-ref"
        return RawShift(0.0, 0.0, 0.0, 0.0, texture_std_ref, texture_std_mov, False, reason)

    win = _hann2d((h, w))
    a = grad_ref * win
    b = grad_mov * win

    shift, _err, _phase = phase_cross_correlation(a, b, upsample_factor=upsample, normalization="phase")
    dy_px = -float(shift[0])
    dx_px = -float(shift[1])
    offset_px = float(np.hypot(dy_px, dx_px))

    F = np.fft.fft2(a)
    G = np.fft.fft2(b)
    R = F * np.conj(G)
    R /= np.abs(R) + 1e-8
    corr = np.fft.fftshift(np.real(np.fft.ifft2(R)))
    peak_yx = np.unravel_index(int(np.argmax(corr)), corr.shape)
    psr = _psr(corr, peak_yx)

    if offset_px > max_offset_px:
        return RawShift(dy_px, dx_px, offset_px, psr, texture_std_ref, texture_std_mov, False, "max-offset")
    ok = psr >= min_psr
    reason = "" if ok else "low-psr"
    return RawShift(dy_px, dx_px, offset_px, psr, texture_std_ref, texture_std_mov, ok, reason)


# --------------------------------------------------------------------------- #
# Periodicity diagnostic (team-lead follow-up, 2026-07-19): a cheap,          #
# single-frame proxy for "how likely is a repeating-structure phase-          #
# correlation alias lock on this content", independent of any ref/mov pair.  #
# Not part of the cascade's decision path -- diagnostic only.                 #
# --------------------------------------------------------------------------- #

DEFAULT_PERIODICITY_EXCLUDE_PX = 3


@dataclass(frozen=True)
class PeriodicityResult:
    """``score`` is the best non-trivial-lag autocorrelation value normalized
    by the zero-lag value (in ``[0, ~1]``; near 1 means the (Sobel+Hann)
    content is nearly self-identical at some nonzero lag -- a repeating
    structure such as a row-house block or a regular panel array).
    ``alias_psr`` is that same secondary peak's peak-to-sidelobe ratio, using
    the identical ``_psr``/normalization convention ``estimate_shift_masked``
    uses for a real ref/mov lock -- i.e. "if this frame were paired against
    an independently-noised copy of itself, would the alias lag alone clear
    the ``DEFAULT_MIN_PSR`` confidence floor". ``lag_dy_px``/``lag_dx_px``
    locate that secondary peak.
    """

    score: float
    alias_psr: float
    lag_dy_px: float
    lag_dx_px: float


def periodicity_score(
    gray: np.ndarray,
    mask: np.ndarray,
    *,
    exclude_radius_px: int = DEFAULT_PERIODICITY_EXCLUDE_PX,
) -> PeriodicityResult:
    """Single-frame self-autocorrelation periodicity diagnostic.

    Same masking/Sobel/Hann preprocessing as ``estimate_shift_masked`` (so
    the score reflects exactly the content the real registration stages
    operate on), autocorrelated against itself (phase-normalized, same
    convention as the cross-correlation in ``estimate_shift_masked``) rather
    than cross-correlated against a second frame. The zero-lag peak is
    always the global max for an autocorrelation; ``exclude_radius_px``
    excludes that trivial peak so the reported secondary peak reflects
    genuine repeating structure, not the estimator's own self-match.
    """
    g = to_gray(gray)
    h = min(g.shape[0], mask.shape[0])
    w = min(g.shape[1], mask.shape[1])
    g = g[:h, :w]
    m = mask[:h, :w]
    unmasked = ~m

    fill = float(g[unmasked].mean()) if unmasked.any() else float(g.mean())
    g = np.where(m, fill, g)
    grad = _sobel_mag(g)
    win = _hann2d((h, w))
    a = grad * win

    # Standard (Wiener-Khinchin) autocorrelation -- unlike
    # ``estimate_shift_masked``'s cross-correlation, deliberately NOT
    # phase-normalized: for a self-correlation the power spectrum
    # ``F * conj(F)`` is already real and non-negative (zero phase
    # everywhere), so dividing by its own magnitude would degenerate to an
    # all-ones spectrum -- a delta function at lag zero regardless of
    # content. The un-normalized, energy-weighted autocorrelation is what
    # actually encodes periodicity strength.
    F = np.fft.fft2(a)
    S = F * np.conj(F)
    ac = np.fft.fftshift(np.real(np.fft.ifft2(S)))

    cy, cx = h // 2, w // 2
    zero_lag = float(ac[cy, cx])

    excl = np.zeros_like(ac, dtype=bool)
    y0, y1 = max(0, cy - exclude_radius_px), cy + exclude_radius_px + 1
    x0, x1 = max(0, cx - exclude_radius_px), cx + exclude_radius_px + 1
    excl[y0:y1, x0:x1] = True
    candidate = np.where(excl, -np.inf, ac)
    peak_idx = np.unravel_index(int(np.argmax(candidate)), ac.shape)

    secondary = float(ac[peak_idx])
    score = float(secondary / zero_lag) if zero_lag > 0 else 0.0
    alias_psr = _psr(ac, peak_idx)
    return PeriodicityResult(
        score=score,
        alias_psr=alias_psr,
        lag_dy_px=float(peak_idx[0] - cy),
        lag_dx_px=float(peak_idx[1] - cx),
    )
