"""Unit tests for ``scripts.temporal.chip_displacement`` (GEHI cross-vintage
displacement audit primitives).

The module is pure/array-level: given two co-gridded gray arrays it recovers the
sub-pixel translation of the moving array relative to the reference, scores how
trustworthy that estimate is (so low-texture / low-zoom failures are flagged, not
misread as large real shifts), converts pixels to true ground metres, and answers
the contamination question "does this offset push the installation footprint out
of a crop of size ``crop_size_m`` centred on the nominal anchor?".

See handoff ``/tmp/handoff_gehi_displacement_audit_2026-07-06.md`` and PRD D18
(chip geometry). No disk / GEHI access here — I/O paths are exercised by the
audit script, not this unit suite.
"""

from __future__ import annotations

import numpy as np
import pytest
from scipy import ndimage

from scripts.temporal import chip_displacement as cd


def _textured(shape=(220, 200), seed=0):
    """A broadband-textured gray image phase-correlation can lock onto.

    Sum of smoothed random blobs at a few scales — non-periodic, plenty of edges,
    similar to a real rooftop chip's gradient content.
    """
    rng = np.random.default_rng(seed)
    img = np.zeros(shape, dtype=np.float64)
    for sigma in (2.0, 5.0, 11.0):
        img += ndimage.gaussian_filter(rng.standard_normal(shape), sigma)
    img -= img.min()
    img /= img.max() + 1e-9
    return img


# --------------------------------------------------------------------------- #
# estimate_shift: synthetic known-shift recovery                              #
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("dy0,dx0", [(0.0, 0.0), (3.0, -5.0), (-7.0, 2.0), (1.5, 4.5)])
def test_estimate_shift_recovers_known_translation(dy0, dx0):
    ref = _textured(seed=1)
    # mov content = ref moved by (dy0, dx0) toward higher indices (down / right).
    mov = ndimage.shift(ref, (dy0, dx0), order=1, mode="constant", cval=0.0)
    res = cd.estimate_shift(ref, mov, upsample=10)
    # Displacement of mov RELATIVE TO ref must equal the applied offset.
    assert res.dy_px == pytest.approx(dy0, abs=0.4), res
    assert res.dx_px == pytest.approx(dx0, abs=0.4), res
    assert res.ok
    assert res.alignment_score > 0.75


def test_estimate_shift_zero_for_identical():
    ref = _textured(seed=2)
    res = cd.estimate_shift(ref, ref.copy(), upsample=10)
    assert res.offset_px == pytest.approx(0.0, abs=0.2)
    assert res.alignment_score > 0.95


def test_estimate_shift_flags_low_texture():
    # A near-flat chip (blank roof / cloud) has no signal to register — must be
    # flagged not-ok via the texture floor, never returned as a confident shift.
    flat = np.full((200, 200), 0.5) + 1e-4 * np.random.default_rng(3).standard_normal((200, 200))
    res = cd.estimate_shift(flat, flat.copy())
    assert not res.ok
    assert "texture" in res.reason


def test_estimate_shift_flags_uncorrelated_pair():
    # Structure vs white noise: no shared content, so the phase-correlation peak
    # sits at the statistical noise floor (PSR well below a real lock). Must be
    # flagged not-ok so the audit drops it rather than logging a spurious shift.
    a = _textured(seed=4)
    b = np.random.default_rng(123).standard_normal(a.shape)
    res = cd.estimate_shift(a, b)
    assert not res.ok
    # And far below a genuine lock of the same image.
    good = cd.estimate_shift(a, ndimage.shift(a, (2.0, -3.0), order=1))
    assert res.psr < good.psr


def test_estimate_shift_is_robust_to_gain_offset():
    # Cross-vintage brightness/contrast differences must not masquerade as shift;
    # gradient pre-whitening should make the estimate invariant to affine gain.
    ref = _textured(seed=5)
    mov = ndimage.shift(ref, (2.0, -3.0), order=1)
    mov = 0.4 * mov + 0.25  # different exposure
    res = cd.estimate_shift(ref, mov, upsample=10)
    assert res.dy_px == pytest.approx(2.0, abs=0.5)
    assert res.dx_px == pytest.approx(-3.0, abs=0.5)
    assert res.ok


# --------------------------------------------------------------------------- #
# to_gray                                                                     #
# --------------------------------------------------------------------------- #

def test_to_gray_accepts_band_first_and_last():
    # Realistic (non-3/4) spatial dims so band-first vs band-last is unambiguous.
    hwc = np.stack([np.ones((8, 6)), 2 * np.ones((8, 6)), 3 * np.ones((8, 6))], axis=-1)
    chw = np.moveaxis(hwc, -1, 0)  # rasterio read order (C, H, W)
    g1 = cd.to_gray(hwc)
    g2 = cd.to_gray(chw)
    assert g1.shape == (8, 6)
    np.testing.assert_allclose(g1, g2)


def test_to_gray_passthrough_2d():
    g = cd.to_gray(np.ones((5, 5)))
    assert g.shape == (5, 5)


# --------------------------------------------------------------------------- #
# shift_px_to_m                                                               #
# --------------------------------------------------------------------------- #

def test_shift_px_to_m_offset_is_euclidean():
    dx_m, dy_m, offset_m = cd.shift_px_to_m(dy_px=3.0, dx_px=4.0, gsd_y_m=1.0, gsd_x_m=1.0)
    assert dx_m == pytest.approx(4.0)
    assert dy_m == pytest.approx(3.0)
    assert offset_m == pytest.approx(5.0)


def test_shift_px_to_m_anisotropic_gsd():
    dx_m, dy_m, offset_m = cd.shift_px_to_m(dy_px=2.0, dx_px=2.0, gsd_y_m=0.5, gsd_x_m=0.25)
    assert dy_m == pytest.approx(1.0)
    assert dx_m == pytest.approx(0.5)
    assert offset_m == pytest.approx(np.hypot(1.0, 0.5))


# --------------------------------------------------------------------------- #
# panel_exits_crop: contamination boundaries                                  #
# --------------------------------------------------------------------------- #

def test_panel_exits_crop_partial_boundary():
    # crop 12 m, footprint 4 m -> half-slack = (12-4)/2 = 4 m.
    assert not cd.panel_exits_crop(3.9, footprint_m=4.0, crop_size_m=12.0, mode="partial")
    assert cd.panel_exits_crop(4.1, footprint_m=4.0, crop_size_m=12.0, mode="partial")


def test_panel_exits_crop_full_boundary():
    # full loss when offset > (crop + footprint)/2 = (12+4)/2 = 8 m.
    assert not cd.panel_exits_crop(7.9, footprint_m=4.0, crop_size_m=12.0, mode="full")
    assert cd.panel_exits_crop(8.1, footprint_m=4.0, crop_size_m=12.0, mode="full")


def test_panel_exits_crop_footprint_larger_than_crop():
    # A footprint wider than the crop is already clipped at zero offset (partial).
    assert cd.panel_exits_crop(0.0, footprint_m=20.0, crop_size_m=12.0, mode="partial")


def test_banked96_far_more_tolerant_than_tight12():
    # The whole point of the audit: the same real offset that is harmless under
    # the banked 96 m chip can eject a small panel from the 12 m tight crop.
    off, fp = 6.0, 4.0
    assert not cd.panel_exits_crop(off, footprint_m=fp, crop_size_m=60.0, mode="partial")
    assert cd.panel_exits_crop(off, footprint_m=fp, crop_size_m=12.0, mode="partial")


# --------------------------------------------------------------------------- #
# footprint_retained_fraction                                                 #
# --------------------------------------------------------------------------- #

def test_retained_fraction_centered_is_full():
    assert cd.footprint_retained_fraction(0.0, 0.0, footprint_w_m=4.0, footprint_h_m=4.0, crop_size_m=12.0) == pytest.approx(1.0)


def test_retained_fraction_edge_touch_still_full():
    # offset = (crop-fp)/2 = 4 -> panel edge exactly at crop edge -> still all in.
    assert cd.footprint_retained_fraction(4.0, 0.0, footprint_w_m=4.0, footprint_h_m=4.0, crop_size_m=12.0) == pytest.approx(1.0)


def test_retained_fraction_partial_clip():
    # offset 5, fp 4, crop 12 -> panel spans [3,7], crop [-6,6] -> keep [3,6]=3/4.
    assert cd.footprint_retained_fraction(5.0, 0.0, footprint_w_m=4.0, footprint_h_m=4.0, crop_size_m=12.0) == pytest.approx(0.75)


def test_retained_fraction_full_loss():
    # offset 8 = (crop+fp)/2 -> panel fully outside -> 0.
    assert cd.footprint_retained_fraction(8.0, 0.0, footprint_w_m=4.0, footprint_h_m=4.0, crop_size_m=12.0) == pytest.approx(0.0)


def test_retained_fraction_is_directional_for_long_panel():
    # A long thin panel (12 x 3) with the crop floored to 12 m: the LONG axis has
    # no slack (any offset clips) but the SHORT axis has plenty. The directional
    # metric must reflect that, unlike a binary "crop==footprint -> any offset
    # exits" flag that would fire on both.
    clip = cd.footprint_retained_fraction(2.0, 0.0, footprint_w_m=12.0, footprint_h_m=3.0, crop_size_m=12.0)
    slack = cd.footprint_retained_fraction(0.0, 2.0, footprint_w_m=12.0, footprint_h_m=3.0, crop_size_m=12.0)
    assert clip == pytest.approx(10.0 / 12.0)  # long axis: keep [3,6]∪... -> 10 of 12 m
    assert slack == pytest.approx(1.0)          # short axis: panel [-1.5+2,1.5+2] still inside


# --------------------------------------------------------------------------- #
# crop_size_for_geometry: faithful to chip_geometry registry                  #
# --------------------------------------------------------------------------- #

def test_crop_size_tight12_small_install():
    # 0.5 mult, 12 m floor, radius 10 -> max(12, 2*10*0.5=10)=12, capped at 96,
    # then floored to the footprint (3.4 x 7.8 -> 7.8 < 12 so stays 12).
    cs = cd.crop_size_for_geometry(
        "chip_geom_v2_tight12", footprint_w_m=3.36, footprint_h_m=7.81,
        search_radius_m=10.0, chip_size_m=96.0,
    )
    assert cs == pytest.approx(12.0)


def test_crop_size_banked96_uses_context_multiplier():
    # 3.0 mult, 24 m floor, radius 10 -> max(24, 2*10*3=60)=60, capped at 96.
    cs = cd.crop_size_for_geometry(
        "chip_geom_v1_banked96", footprint_w_m=3.36, footprint_h_m=7.81,
        search_radius_m=10.0, chip_size_m=96.0,
    )
    assert cs == pytest.approx(60.0)


def test_crop_size_floored_to_large_footprint():
    # A large install floors the tight crop up to its own footprint (contain guard).
    cs = cd.crop_size_for_geometry(
        "chip_geom_v2_tight12", footprint_w_m=30.0, footprint_h_m=18.0,
        search_radius_m=10.0, chip_size_m=96.0,
    )
    assert cs == pytest.approx(30.0)
