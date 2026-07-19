"""Unit tests for the R2 localization cascade (PRD §5,
``docs/dinov3_scorer/PRD-run3-native-local-line-2026-07-19.md``).

Pure synthetic-array / stub-registration-context tests -- no real GEHI
imagery, no GPU, no manifest dependency. Covers the four things the
team-lead brief called out explicitly: phase-correlation boundedness (an
out-of-bounds lock must abstain, never localize), PV-mask exclusion (a
displaced feature confined to the masked region must not drive the
estimate), weak-lock low-confidence abstention (a dark-zone match must
abstain, never force a fit), and cascade stage priority (phase-correlation's
confident lock is final; only its own "ambiguous, nothing committed" marker
escalates to weak-lock). Also covers TLO field completeness on real cascade
output.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest

from scripts.temporal.replay_localization_cascade import (
    CONFLICT_DISAGREEMENT_M,
    PERIODICITY_ESCALATION_ALIAS_PSR,
    RegistrationContext,
    StageInput,
    _corrupt_signal,
    _identity_fallback,
    _periodicity_escalation_reason,
    _phase_correlation_core,
    _weak_lock_core,
    identity_stage,
    phase_correlation_stage,
    run_full_cascade,
    run_full_cascade_traced,
    weak_lock_stage,
)
from solar_backdating.localization.observation import DEFAULT_MAX_TRANSLATION_M
from solar_backdating.localization.phase_corr import (
    DEFAULT_MIN_PSR,
    PeriodicityResult,
    RawShift,
    estimate_shift_masked,
    periodicity_score,
)
from solar_backdating.localization.pv_mask import build_centered_mask, pv_mask_half_extent_m
from solar_backdating.localization.reference import ReferenceCandidate
from solar_backdating.localization.weak_lock import RawMatch

ANCHOR = "jhb_test_cascade_0001"
DATE = date(2020, 6, 15)
BBOX = (28.0900, -26.1188, 28.0911, -26.1180)
GSD_M = 0.3


def _make_input(**overrides) -> StageInput:
    kwargs = dict(
        anchor_id=ANCHOR,
        capture_date=DATE,
        anchor_bbox_lonlat=BBOX,
        chip_path=Path("/nonexistent/chip.tif"),
        reference_chip_path=None,
        prior_observation=None,
        chip_arm="A24",
        source_area_m2=20.0,
        label_v1="absent",
        quality_flag="usable",
        confidence=0.95,
        reference_pool=(),
    )
    kwargs.update(overrides)
    return StageInput(**kwargs)


def _make_ctx(ref_gray: np.ndarray, mov_gray: np.ndarray, mask: np.ndarray) -> RegistrationContext:
    ref_cand = ReferenceCandidate(
        anchor_id=ANCHOR,
        capture_date=date(2025, 3, 30),
        chip_arm="A24",
        src_tiff_path=Path("/nonexistent/ref.tif"),
        label_v1="present",
        quality_flag="usable",
        confidence=1.0,
    )
    return RegistrationContext(
        ref_cand=ref_cand, ref_gray=ref_gray, mov_gray=mov_gray, mask=mask, gsd_x=GSD_M, gsd_y=GSD_M
    )


# --------------------------------------------------------------------------- #
# identity_stage / fallback helpers                                          #
# --------------------------------------------------------------------------- #


def test_identity_stage_always_localizes_and_never_abstains() -> None:
    tlo = identity_stage(_make_input())
    assert tlo.target_localized
    assert not tlo.abstain
    assert tlo.transform_type == "identity"
    assert tlo.cascade_stage == "identity"


def test_identity_fallback_used_when_no_reference_available() -> None:
    """No reference in the pool (or no chip_path) -- phase_correlation must
    fall back to trusted identity geometry, not downgrade the label."""
    inp = _make_input(chip_path=None)
    tlo = phase_correlation_stage(inp)
    assert tlo.cascade_stage == "phase_correlation"
    assert tlo.target_localized
    assert tlo.transform_type == "identity"
    assert tlo.failure_reason == ""
    assert not tlo.abstain


def test_corrupt_signal_helper_is_schema_legal_and_downgrades_via_gate() -> None:
    from solar_backdating.localization.observation import effective_label

    tlo = _corrupt_signal(_make_input(), "phase_correlation")
    assert not tlo.target_localized
    assert tlo.failure_reason == "roof_plane_not_matched"
    assert not tlo.abstain
    gate = effective_label("absent", "usable", tlo)
    assert gate["label"] == "uninformative"
    assert gate["gated_reason"] == "roof_plane_not_matched"


# --------------------------------------------------------------------------- #
# Phase-correlation boundedness (red line: out-of-bounds -> abstain, never   #
# localize) -- driven end-to-end through _phase_correlation_core by mocking  #
# the registration context and the masked estimator's output.               #
# --------------------------------------------------------------------------- #


def test_phase_correlation_in_bounds_lock_localizes() -> None:
    ctx = _make_ctx(np.zeros((64, 64)), np.zeros((64, 64)), np.zeros((64, 64), dtype=bool))
    # 3m offset at 0.3 gsd -> within DEFAULT_MAX_TRANSLATION_M=5.0
    small_px = 3.0 / GSD_M
    raw = RawShift(dy_px=small_px, dx_px=0.0, offset_px=small_px, psr=20.0, texture_std_ref=0.1, texture_std_mov=0.1, ok=True, reason="")
    with patch(
        "scripts.temporal.replay_localization_cascade._prepare_registration_context", return_value=ctx
    ), patch(
        "scripts.temporal.replay_localization_cascade.estimate_shift_masked", return_value=raw
    ):
        tlo, raw_out, ctx_out = _phase_correlation_core(_make_input())
    assert tlo.target_localized
    assert not tlo.abstain
    assert tlo.transform_type == "translation"
    assert tlo.failure_reason == ""
    assert raw_out is raw
    assert ctx_out is ctx


def test_phase_correlation_out_of_bounds_lock_must_abstain_never_localize() -> None:
    ctx = _make_ctx(np.zeros((64, 64)), np.zeros((64, 64)), np.zeros((64, 64), dtype=bool))
    big_px = (DEFAULT_MAX_TRANSLATION_M + 3.0) / GSD_M
    raw = RawShift(dy_px=big_px, dx_px=0.0, offset_px=big_px, psr=20.0, texture_std_ref=0.1, texture_std_mov=0.1, ok=True, reason="")
    with patch(
        "scripts.temporal.replay_localization_cascade._prepare_registration_context", return_value=ctx
    ), patch(
        "scripts.temporal.replay_localization_cascade.estimate_shift_masked", return_value=raw
    ):
        tlo, _raw_out, _ctx_out = _phase_correlation_core(_make_input())
    assert not tlo.target_localized
    assert tlo.abstain
    assert tlo.failure_reason == "transform_out_of_bounds"


def test_phase_correlation_weak_lock_escalation_marker_commits_nothing() -> None:
    """A low-PSR ("low-psr") result must not localize, must not abstain
    outright, and must carry no committed transform -- it is the escalation
    signal ``run_full_cascade`` reads to hand off to weak_lock."""
    ctx = _make_ctx(np.zeros((64, 64)), np.zeros((64, 64)), np.zeros((64, 64), dtype=bool))
    raw = RawShift(dy_px=1.0, dx_px=1.0, offset_px=1.4, psr=2.0, texture_std_ref=0.1, texture_std_mov=0.1, ok=False, reason="low-psr")
    with patch(
        "scripts.temporal.replay_localization_cascade._prepare_registration_context", return_value=ctx
    ), patch(
        "scripts.temporal.replay_localization_cascade.estimate_shift_masked", return_value=raw
    ):
        tlo, raw_out, _ctx_out = _phase_correlation_core(_make_input())
    assert not tlo.target_localized
    assert not tlo.abstain
    assert tlo.failure_reason == "low_confidence"
    assert tlo.transform_type == "identity"
    assert raw_out.reason == "low-psr"


def test_phase_correlation_out_of_bounds_never_localizes_property() -> None:
    """Direct property check on the schema helper itself, independent of the
    cascade plumbing above."""
    from solar_backdating.localization.observation import transform_within_bounds

    params = {"dx_m": DEFAULT_MAX_TRANSLATION_M + 0.01, "dy_m": 0.0}
    assert not transform_within_bounds("translation", params)


# --------------------------------------------------------------------------- #
# PV-mask exclusion: a feature displaced only inside the masked region must  #
# not drive the phase-correlation estimate.                                  #
# --------------------------------------------------------------------------- #


def test_pv_mask_excludes_in_mask_only_displacement() -> None:
    h, w = 200, 200
    flat = np.full((h, w), 0.5)
    ref = flat.copy()
    mov = flat.copy()

    half = pv_mask_half_extent_m(20.0)
    mask, _ = build_centered_mask((h, w), 0.3, half)

    cy, cx = h // 2, w // 2
    ref[cy - 5 : cy + 5, cx - 5 : cx + 5] = 1.0
    mov[cy - 5 + 8 : cy + 5 + 8, cx - 5 + 8 : cx + 5 + 8] = 1.0  # shifted 8px, inside mask only

    masked = estimate_shift_masked(ref, mov, mask)
    # No exploitable structure survives outside the mask on this synthetic
    # flat background -> fails safe (does not fabricate the in-mask shift).
    assert not masked.ok
    assert masked.reason in ("low-texture-ref", "low-texture-mov", "low-texture-both")
    assert masked.dy_px == 0.0 and masked.dx_px == 0.0

    unmasked = estimate_shift_masked(ref, mov, np.zeros((h, w), dtype=bool))
    assert unmasked.ok
    assert unmasked.dy_px == pytest.approx(8.0) and unmasked.dx_px == pytest.approx(8.0)


def test_pv_mask_half_extent_scales_with_area_and_buffer() -> None:
    small = pv_mask_half_extent_m(4.0)
    large = pv_mask_half_extent_m(100.0)
    assert large > small


def test_build_centered_mask_caps_at_max_fraction() -> None:
    mask, half_px = build_centered_mask((40, 40), 0.3, half_extent_m=1000.0, max_mask_fraction=0.5)
    assert half_px == pytest.approx(0.5 * 40 / 2.0)
    assert mask.sum() < 40 * 40  # never masks the whole grid


# --------------------------------------------------------------------------- #
# Weak-lock: low-confidence / dark-zone must abstain, never force a fit.     #
# --------------------------------------------------------------------------- #


def test_weak_lock_no_matches_is_dark_zone_abstain() -> None:
    ctx = _make_ctx(np.zeros((64, 64)), np.zeros((64, 64)), np.zeros((64, 64), dtype=bool))
    match = RawMatch(dx_px=0.0, dy_px=0.0, n_matches=0, n_inliers=0, inlier_ratio=0.0, residual_std_px=None)
    with patch(
        "scripts.temporal.replay_localization_cascade.match_translation_masked", return_value=match
    ):
        tlo, match_out = _weak_lock_core(_make_input(), ctx, prior_raw=None, device="cpu")
    assert not tlo.target_localized
    assert tlo.abstain
    assert tlo.failure_reason == "dark_zone"
    assert match_out is match


def test_weak_lock_below_inlier_floor_is_dark_zone_abstain() -> None:
    ctx = _make_ctx(np.zeros((64, 64)), np.zeros((64, 64)), np.zeros((64, 64), dtype=bool))
    match = RawMatch(dx_px=5.0, dy_px=5.0, n_matches=50, n_inliers=3, inlier_ratio=0.06, residual_std_px=1.0)
    with patch(
        "scripts.temporal.replay_localization_cascade.match_translation_masked", return_value=match
    ):
        tlo, _ = _weak_lock_core(_make_input(), ctx, prior_raw=None, device="cpu")
    assert not tlo.target_localized
    assert tlo.abstain
    assert tlo.failure_reason == "dark_zone"


def test_weak_lock_no_context_falls_back_to_identity() -> None:
    tlo, match_out = _weak_lock_core(_make_input(), ctx=None, prior_raw=None, device="cpu")
    assert tlo.target_localized
    assert tlo.transform_type == "identity"
    assert tlo.cascade_stage == "weak_lock"
    assert match_out is None


def test_weak_lock_confident_in_bounds_lock_localizes() -> None:
    ctx = _make_ctx(np.zeros((64, 64)), np.zeros((64, 64)), np.zeros((64, 64), dtype=bool))
    dx_px = 2.0 / GSD_M
    match = RawMatch(dx_px=dx_px, dy_px=0.0, n_matches=100, n_inliers=40, inlier_ratio=0.4, residual_std_px=0.5)
    with patch(
        "scripts.temporal.replay_localization_cascade.match_translation_masked", return_value=match
    ):
        tlo, _ = _weak_lock_core(_make_input(), ctx, prior_raw=None, device="cpu")
    assert tlo.target_localized
    assert not tlo.abstain
    assert tlo.transform_type == "translation"


def test_weak_lock_out_of_bounds_confident_lock_must_abstain() -> None:
    ctx = _make_ctx(np.zeros((64, 64)), np.zeros((64, 64)), np.zeros((64, 64), dtype=bool))
    dx_px = (DEFAULT_MAX_TRANSLATION_M + 4.0) / GSD_M
    match = RawMatch(dx_px=dx_px, dy_px=0.0, n_matches=100, n_inliers=40, inlier_ratio=0.4, residual_std_px=0.5)
    with patch(
        "scripts.temporal.replay_localization_cascade.match_translation_masked", return_value=match
    ):
        tlo, _ = _weak_lock_core(_make_input(), ctx, prior_raw=None, device="cpu")
    assert not tlo.target_localized
    assert tlo.abstain
    assert tlo.failure_reason == "transform_out_of_bounds"


def test_weak_lock_conflicting_with_phase_correlation_must_abstain() -> None:
    ctx = _make_ctx(np.zeros((64, 64)), np.zeros((64, 64)), np.zeros((64, 64), dtype=bool))
    # weak_lock confidently finds +2m east; phase_correlation's own weak
    # estimate pointed strongly the opposite way -- disagreement far exceeds
    # CONFLICT_DISAGREEMENT_M.
    dx_px = 2.0 / GSD_M
    match = RawMatch(dx_px=dx_px, dy_px=0.0, n_matches=100, n_inliers=40, inlier_ratio=0.4, residual_std_px=0.5)
    conflicting_prior_dx_px = -(CONFLICT_DISAGREEMENT_M + 5.0) / GSD_M
    prior_raw = RawShift(
        dy_px=0.0, dx_px=conflicting_prior_dx_px, offset_px=abs(conflicting_prior_dx_px),
        psr=3.0, texture_std_ref=0.1, texture_std_mov=0.1, ok=False, reason="low-psr",
    )
    with patch(
        "scripts.temporal.replay_localization_cascade.match_translation_masked", return_value=match
    ):
        tlo, _ = _weak_lock_core(_make_input(), ctx, prior_raw=prior_raw, device="cpu")
    assert not tlo.target_localized
    assert tlo.abstain
    assert tlo.failure_reason == "transform_conflict"


# --------------------------------------------------------------------------- #
# Cascade priority: run_full_cascade only escalates on phase_correlation's   #
# "low_confidence" marker; everything else (confident lock, forced abstain,  #
# identity fallback) is final and weak_lock is never invoked.                #
# --------------------------------------------------------------------------- #


def test_cascade_confident_phase_correlation_lock_skips_weak_lock() -> None:
    ctx = _make_ctx(np.zeros((64, 64)), np.zeros((64, 64)), np.zeros((64, 64), dtype=bool))
    raw = RawShift(dy_px=1.0, dx_px=0.0, offset_px=1.0, psr=20.0, texture_std_ref=0.1, texture_std_mov=0.1, ok=True, reason="")
    with patch(
        "scripts.temporal.replay_localization_cascade._prepare_registration_context", return_value=ctx
    ), patch(
        "scripts.temporal.replay_localization_cascade.estimate_shift_masked", return_value=raw
    ), patch(
        "scripts.temporal.replay_localization_cascade.match_translation_masked"
    ) as mock_weak_lock:
        tlo = run_full_cascade(_make_input(), device="cpu")
    mock_weak_lock.assert_not_called()
    assert tlo.cascade_stage == "phase_correlation"
    assert tlo.target_localized


def test_cascade_forced_abstain_phase_correlation_skips_weak_lock() -> None:
    ctx = _make_ctx(np.zeros((64, 64)), np.zeros((64, 64)), np.zeros((64, 64), dtype=bool))
    big_px = (DEFAULT_MAX_TRANSLATION_M + 3.0) / GSD_M
    raw = RawShift(dy_px=big_px, dx_px=0.0, offset_px=big_px, psr=20.0, texture_std_ref=0.1, texture_std_mov=0.1, ok=True, reason="")
    with patch(
        "scripts.temporal.replay_localization_cascade._prepare_registration_context", return_value=ctx
    ), patch(
        "scripts.temporal.replay_localization_cascade.estimate_shift_masked", return_value=raw
    ), patch(
        "scripts.temporal.replay_localization_cascade.match_translation_masked"
    ) as mock_weak_lock:
        tlo = run_full_cascade(_make_input(), device="cpu")
    mock_weak_lock.assert_not_called()
    assert tlo.cascade_stage == "phase_correlation"
    assert tlo.abstain
    assert tlo.failure_reason == "transform_out_of_bounds"


def test_cascade_low_confidence_escalates_to_weak_lock() -> None:
    ctx = _make_ctx(np.zeros((64, 64)), np.zeros((64, 64)), np.zeros((64, 64), dtype=bool))
    raw = RawShift(dy_px=1.0, dx_px=1.0, offset_px=1.4, psr=2.0, texture_std_ref=0.1, texture_std_mov=0.1, ok=False, reason="low-psr")
    match = RawMatch(dx_px=2.0 / GSD_M, dy_px=0.0, n_matches=100, n_inliers=40, inlier_ratio=0.4, residual_std_px=0.5)
    with patch(
        "scripts.temporal.replay_localization_cascade._prepare_registration_context", return_value=ctx
    ), patch(
        "scripts.temporal.replay_localization_cascade.estimate_shift_masked", return_value=raw
    ), patch(
        "scripts.temporal.replay_localization_cascade.match_translation_masked", return_value=match
    ) as mock_weak_lock:
        tlo = run_full_cascade(_make_input(), device="cpu")
    mock_weak_lock.assert_called_once()
    assert tlo.cascade_stage == "weak_lock"
    assert tlo.target_localized


def test_cascade_identity_fallback_skips_weak_lock() -> None:
    with patch(
        "scripts.temporal.replay_localization_cascade.match_translation_masked"
    ) as mock_weak_lock:
        tlo = run_full_cascade(_make_input(chip_path=None), device="cpu")
    mock_weak_lock.assert_not_called()
    assert tlo.cascade_stage == "phase_correlation"
    assert tlo.target_localized
    assert tlo.transform_type == "identity"


# --------------------------------------------------------------------------- #
# Leverage 1 (team-lead follow-up, 2026-07-19): periodicity-aware dual-side  #
# routing to weak_lock -- abstain side (a confident-but-out-of-bounds lock   #
# on high-periodicity content) and pass side (the item_07 lesson: a         #
# confident IN-bounds lock on high-periodicity content is also re-checked). #
# --------------------------------------------------------------------------- #

_HIGH_PERIODICITY = PeriodicityResult(
    score=0.9, alias_psr=PERIODICITY_ESCALATION_ALIAS_PSR + 1.0, lag_dy_px=0.0, lag_dx_px=20.0
)
_LOW_PERIODICITY = PeriodicityResult(
    score=0.1, alias_psr=PERIODICITY_ESCALATION_ALIAS_PSR - 1.0, lag_dy_px=3.0, lag_dx_px=-2.0
)


def _confident_in_bounds_raw() -> RawShift:
    small_px = 1.0 / GSD_M
    return RawShift(
        dy_px=small_px, dx_px=0.0, offset_px=small_px, psr=20.0,
        texture_std_ref=0.1, texture_std_mov=0.1, ok=True, reason="",
    )


def _confident_out_of_bounds_raw() -> RawShift:
    big_px = (DEFAULT_MAX_TRANSLATION_M + 3.0) / GSD_M
    return RawShift(
        dy_px=big_px, dx_px=0.0, offset_px=big_px, psr=20.0,
        texture_std_ref=0.1, texture_std_mov=0.1, ok=True, reason="",
    )


def test_periodicity_escalation_reason_none_for_low_periodicity_confident_lock() -> None:
    ctx = _make_ctx(np.zeros((64, 64)), np.zeros((64, 64)), np.zeros((64, 64), dtype=bool))
    with patch(
        "scripts.temporal.replay_localization_cascade._prepare_registration_context", return_value=ctx
    ), patch(
        "scripts.temporal.replay_localization_cascade.estimate_shift_masked",
        return_value=_confident_in_bounds_raw(),
    ), patch(
        "scripts.temporal.replay_localization_cascade.periodicity_score", return_value=_LOW_PERIODICITY
    ):
        tlo, _raw, ctx_out = _phase_correlation_core(_make_input())
    assert _periodicity_escalation_reason(tlo, ctx_out) is None


def test_periodicity_pass_side_escalates_confident_lock_to_weak_lock() -> None:
    """The item_07 lesson: a confident, in-bounds phase-correlation lock on
    high-periodicity content must NOT bypass weak_lock re-verification."""
    ctx = _make_ctx(np.zeros((64, 64)), np.zeros((64, 64)), np.zeros((64, 64), dtype=bool))
    match = RawMatch(dx_px=1.0 / GSD_M, dy_px=0.0, n_matches=100, n_inliers=40, inlier_ratio=0.4, residual_std_px=0.5)
    with patch(
        "scripts.temporal.replay_localization_cascade._prepare_registration_context", return_value=ctx
    ), patch(
        "scripts.temporal.replay_localization_cascade.estimate_shift_masked",
        return_value=_confident_in_bounds_raw(),
    ), patch(
        "scripts.temporal.replay_localization_cascade.periodicity_score", return_value=_HIGH_PERIODICITY
    ), patch(
        "scripts.temporal.replay_localization_cascade.match_translation_masked", return_value=match
    ) as mock_weak_lock:
        tlo, reason, _record = run_full_cascade_traced(_make_input(), device="cpu")
    mock_weak_lock.assert_called_once()
    assert reason == "periodicity_pass_side"
    assert tlo.cascade_stage == "weak_lock"
    # weak_lock's own estimate (1.4m from phase_correlation's) confirms, no conflict.
    assert tlo.target_localized and not tlo.abstain


def test_periodicity_abstain_side_escalates_out_of_bounds_to_weak_lock() -> None:
    """A confident-but-out-of-bounds phase-correlation lock on high-
    periodicity content is re-checked rather than accepted as final."""
    ctx = _make_ctx(np.zeros((64, 64)), np.zeros((64, 64)), np.zeros((64, 64), dtype=bool))
    match = RawMatch(dx_px=0.0, dy_px=0.0, n_matches=0, n_inliers=0, inlier_ratio=0.0, residual_std_px=None)
    with patch(
        "scripts.temporal.replay_localization_cascade._prepare_registration_context", return_value=ctx
    ), patch(
        "scripts.temporal.replay_localization_cascade.estimate_shift_masked",
        return_value=_confident_out_of_bounds_raw(),
    ), patch(
        "scripts.temporal.replay_localization_cascade.periodicity_score", return_value=_HIGH_PERIODICITY
    ), patch(
        "scripts.temporal.replay_localization_cascade.match_translation_masked", return_value=match
    ) as mock_weak_lock:
        tlo, reason, _record = run_full_cascade_traced(_make_input(), device="cpu")
    mock_weak_lock.assert_called_once()
    assert reason == "periodicity_abstain_side"
    assert tlo.cascade_stage == "weak_lock"
    assert tlo.abstain and tlo.failure_reason == "dark_zone"


def test_periodicity_abstain_side_rescue_is_blocked_keeps_original_out_of_bounds_verdict() -> None:
    """Team-lead ruling (2026-07-19, DATA-r2 §13): a weak_lock review that
    WOULD rescue an out-of-bounds abstain into a confident lock must be
    discarded -- the final verdict falls back to phase_correlation's own
    original transform_out_of_bounds abstain, not weak_lock's confident
    finding. The review is still recorded (rescue_blocked=True)."""
    ctx = _make_ctx(np.zeros((64, 64)), np.zeros((64, 64)), np.zeros((64, 64), dtype=bool))
    pc_raw = _confident_out_of_bounds_raw()
    # weak_lock confidently finds a small, in-bounds, NON-conflicting offset
    # -- exactly the "would-be rescue" scenario the ruling forbids.
    match = RawMatch(dx_px=1.0 / GSD_M, dy_px=0.0, n_matches=100, n_inliers=40, inlier_ratio=0.4, residual_std_px=0.5)
    with patch(
        "scripts.temporal.replay_localization_cascade._prepare_registration_context", return_value=ctx
    ), patch(
        "scripts.temporal.replay_localization_cascade.estimate_shift_masked", return_value=pc_raw
    ), patch(
        "scripts.temporal.replay_localization_cascade.periodicity_score", return_value=_HIGH_PERIODICITY
    ), patch(
        "scripts.temporal.replay_localization_cascade.match_translation_masked", return_value=match
    ) as mock_weak_lock:
        tlo, reason, record = run_full_cascade_traced(_make_input(), device="cpu")
    mock_weak_lock.assert_called_once()
    assert reason == "periodicity_abstain_side"
    # Final verdict is phase_correlation's ORIGINAL out-of-bounds abstain,
    # not weak_lock's would-be confident rescue.
    assert tlo.cascade_stage == "phase_correlation"
    assert not tlo.target_localized
    assert tlo.abstain and tlo.failure_reason == "transform_out_of_bounds"
    # The blocked review is still recorded, with weak_lock's actual (would-
    # be-rescuing) finding preserved for the conflict-gate redesign data.
    assert record is not None
    assert record.rescue_blocked is True
    assert record.escalation_reason == "periodicity_abstain_side"
    assert record.weak_lock_target_localized is True
    assert record.phase_correlation_target_localized is False


@pytest.mark.parametrize(
    "n_matches,n_inliers,inlier_ratio,dx_px,dy_px",
    [
        (100, 40, 0.4, 1.0 / GSD_M, 0.0),  # would-be confident, agrees with phase_correlation
        (100, 40, 0.4, -1.0 / GSD_M, 0.0),  # would-be confident, small disagreement (no conflict)
        (200, 80, 0.4, 4.9 / GSD_M, 0.0),  # would-be confident, near the legal bound
        (50, 20, 0.4, 0.0, 4.9 / GSD_M),  # would-be confident, other axis
    ],
)
def test_periodicity_abstain_side_never_produces_target_localized_true(
    n_matches: int, n_inliers: int, inlier_ratio: float, dx_px: float, dy_px: float
) -> None:
    """Red-line-level invariant (team-lead ruling, DATA-r2 §13): across a
    spread of weak_lock outcomes that would otherwise confidently localize,
    a periodicity_abstain_side escalation must NEVER produce
    target_localized=True in the final result."""
    ctx = _make_ctx(np.zeros((64, 64)), np.zeros((64, 64)), np.zeros((64, 64), dtype=bool))
    match = RawMatch(
        dx_px=dx_px, dy_px=dy_px, n_matches=n_matches, n_inliers=n_inliers, inlier_ratio=inlier_ratio,
        residual_std_px=0.5,
    )
    with patch(
        "scripts.temporal.replay_localization_cascade._prepare_registration_context", return_value=ctx
    ), patch(
        "scripts.temporal.replay_localization_cascade.estimate_shift_masked",
        return_value=_confident_out_of_bounds_raw(),
    ), patch(
        "scripts.temporal.replay_localization_cascade.periodicity_score", return_value=_HIGH_PERIODICITY
    ), patch(
        "scripts.temporal.replay_localization_cascade.match_translation_masked", return_value=match
    ):
        tlo, reason, _record = run_full_cascade_traced(_make_input(), device="cpu")
    assert reason == "periodicity_abstain_side"
    assert tlo.target_localized is False


def test_periodicity_abstain_side_review_conflict_uses_existing_conflict_semantics() -> None:
    """When the periodicity-triggered weak_lock re-check disagrees sharply
    with phase-correlation's own (out-of-bounds, periodicity-flagged)
    committed offset, the outcome is transform_conflict via the SAME
    conflict logic used elsewhere -- no special-cased "trust the re-check"
    override. (Note: this can only be demonstrated from the abstain side --
    two independently in-bounds offsets are each capped at
    DEFAULT_MAX_TRANSLATION_M=5m, so their disagreement can never exceed
    CONFLICT_DISAGREEMENT_M=10m; the pass-side escalation path structurally
    cannot reach transform_conflict for that reason, confirmed by
    ``test_periodicity_pass_side_escalates_confident_lock_to_weak_lock``
    landing on a confident re-confirmed lock instead.)"""
    ctx = _make_ctx(np.zeros((64, 64)), np.zeros((64, 64)), np.zeros((64, 64), dtype=bool))
    # weak_lock confidently finds an in-bounds (|offset|=5m, the legal max)
    # offset on the opposite side from phase_correlation's own out-of-bounds
    # committed offset (dy_m=8.0) -- disagreement 13m, clears
    # CONFLICT_DISAGREEMENT_M=10m while weak_lock's own offset stays legal.
    match = RawMatch(dx_px=0.0, dy_px=-5.0 / GSD_M, n_matches=100, n_inliers=40, inlier_ratio=0.4, residual_std_px=0.5)
    with patch(
        "scripts.temporal.replay_localization_cascade._prepare_registration_context", return_value=ctx
    ), patch(
        "scripts.temporal.replay_localization_cascade.estimate_shift_masked",
        return_value=_confident_out_of_bounds_raw(),
    ), patch(
        "scripts.temporal.replay_localization_cascade.periodicity_score", return_value=_HIGH_PERIODICITY
    ), patch(
        "scripts.temporal.replay_localization_cascade.match_translation_masked", return_value=match
    ) as mock_weak_lock:
        tlo, reason, _record = run_full_cascade_traced(_make_input(), device="cpu")
    mock_weak_lock.assert_called_once()
    assert reason == "periodicity_abstain_side"
    assert tlo.abstain and not tlo.target_localized
    assert tlo.failure_reason == "transform_conflict"


def test_periodicity_routing_does_not_apply_to_low_confidence_marker() -> None:
    """The pre-existing low_confidence escalation path is untouched -- it
    fires via its own condition, independent of periodicity_score (which is
    never even called for that branch)."""
    ctx = _make_ctx(np.zeros((64, 64)), np.zeros((64, 64)), np.zeros((64, 64), dtype=bool))
    raw = RawShift(dy_px=1.0, dx_px=1.0, offset_px=1.4, psr=2.0, texture_std_ref=0.1, texture_std_mov=0.1, ok=False, reason="low-psr")
    match = RawMatch(dx_px=2.0 / GSD_M, dy_px=0.0, n_matches=100, n_inliers=40, inlier_ratio=0.4, residual_std_px=0.5)
    with patch(
        "scripts.temporal.replay_localization_cascade._prepare_registration_context", return_value=ctx
    ), patch(
        "scripts.temporal.replay_localization_cascade.estimate_shift_masked", return_value=raw
    ), patch(
        "scripts.temporal.replay_localization_cascade.periodicity_score"
    ) as mock_periodicity, patch(
        "scripts.temporal.replay_localization_cascade.match_translation_masked", return_value=match
    ) as mock_weak_lock:
        tlo, reason, _record = run_full_cascade_traced(_make_input(), device="cpu")
    mock_weak_lock.assert_called_once()
    mock_periodicity.assert_not_called()
    assert reason == "low_confidence"


def test_periodicity_dark_zone_semantics_unchanged_by_routing() -> None:
    """Red line: weak_lock's dark-zone gate (inlier-count/ratio floors) is
    identical regardless of WHY the escalation happened -- periodicity
    routing does not loosen it into an unconditional warp."""
    ctx = _make_ctx(np.zeros((64, 64)), np.zeros((64, 64)), np.zeros((64, 64), dtype=bool))
    weak_match = RawMatch(dx_px=5.0, dy_px=5.0, n_matches=50, n_inliers=3, inlier_ratio=0.06, residual_std_px=1.0)
    with patch(
        "scripts.temporal.replay_localization_cascade._prepare_registration_context", return_value=ctx
    ), patch(
        "scripts.temporal.replay_localization_cascade.estimate_shift_masked",
        return_value=_confident_in_bounds_raw(),
    ), patch(
        "scripts.temporal.replay_localization_cascade.periodicity_score", return_value=_HIGH_PERIODICITY
    ), patch(
        "scripts.temporal.replay_localization_cascade.match_translation_masked", return_value=weak_match
    ):
        tlo, reason, _record = run_full_cascade_traced(_make_input(), device="cpu")
    assert reason == "periodicity_pass_side"
    assert tlo.abstain and not tlo.target_localized
    assert tlo.failure_reason == "dark_zone"


# --------------------------------------------------------------------------- #
# TLO field completeness on real cascade output (schema round-trip through   #
# every reachable branch's constructor call -- if any branch built an        #
# illegal TLO, __post_init__ would already have raised during the calls      #
# above; this asserts the JSON record carries every documented field).       #
# --------------------------------------------------------------------------- #


def test_tlo_json_record_field_completeness() -> None:
    tlo = identity_stage(_make_input())
    record = tlo.to_json_record()
    expected_fields = {
        "schema_version", "anchor_id", "capture_date", "building_found",
        "roof_plane_matched", "target_localized", "transform_type", "transform_params",
        "registration_confidence", "shift_uncertainty_m", "projected_target_polygon",
        "failure_reason", "cascade_stage", "abstain", "roi_expansion_m", "roi_expansion_clipped",
    }
    assert expected_fields <= record.keys()


def test_identity_ring_matches_bbox_corners() -> None:
    """Legacy-join fallback path (`_make_input`'s default `centroid_lon=
    centroid_lat=fov_m=None`) -- whole chip-bbox ring, kept for the
    pre-R0 identity-only debug CLI path."""
    tlo = identity_stage(_make_input())
    lon_min, lat_min, lon_max, lat_max = BBOX
    assert tlo.projected_target_polygon[0] == (lon_min, lat_min)
    assert tlo.projected_target_polygon[2] == (lon_max, lat_max)
    assert tlo.projected_target_polygon[0] == tlo.projected_target_polygon[-1]  # closed ring


# --------------------------------------------------------------------------- #
# Polygon-base fix (r3-ceiling cross-check gap, ruled by team-lead            #
# 2026-07-19): manifest-driven observations must get the R1-aligned nominal  #
# ROI square (`geo.target_roi_ring_lonlat`), not the whole chip bbox.        #
# --------------------------------------------------------------------------- #

CENTROID_LON = 28.1044619567
CENTROID_LAT = -26.2089399634


def _ring_bbox_edges_m(ring: tuple[tuple[float, float], ...]) -> tuple[float, float]:
    """Exact (east-west, north-south) edge lengths in true metres, via the
    same UTM 35S reprojection the production path (geo.py) uses -- not an
    approximation, so tests can assert a tight tolerance."""
    from pyproj import Transformer

    tf = Transformer.from_crs("EPSG:4326", "EPSG:32735", always_xy=True)
    xs, ys = [], []
    for lon, lat in ring:
        x, y = tf.transform(lon, lat)
        xs.append(x)
        ys.append(y)
    return max(xs) - min(xs), max(ys) - min(ys)


def test_identity_ring_r1_aligned_edge_length_bounded_by_fov() -> None:
    """Polygon bbox edge length must never exceed `fov_m` -- the exact
    failure mode of the fixed gap (raw chip bbox was 7.2-38.8x too large)."""
    inp = _make_input(
        centroid_lon=CENTROID_LON, centroid_lat=CENTROID_LAT, fov_m=24.0, source_area_m2=5.0717
    )
    tlo = identity_stage(inp)
    ew_m, ns_m = _ring_bbox_edges_m(tlo.projected_target_polygon)
    assert ew_m <= 24.0 + 1e-6
    assert ns_m <= 24.0 + 1e-6
    # and it should be close to sqrt(area), not the fov ceiling, for a small target
    import math

    assert ew_m == pytest.approx(math.sqrt(5.0717), abs=1e-6)


def test_identity_ring_r1_aligned_edge_length_clamped_to_fov_for_large_area() -> None:
    """`roi_edge_m = min(fov_m, sqrt(area))` -- a target larger than the FoV
    clamps to `fov_m`, never exceeds it."""
    inp = _make_input(
        centroid_lon=CENTROID_LON, centroid_lat=CENTROID_LAT, fov_m=24.0, source_area_m2=10_000.0
    )
    tlo = identity_stage(inp)
    ew_m, ns_m = _ring_bbox_edges_m(tlo.projected_target_polygon)
    assert ew_m <= 24.0 + 1e-6
    assert ns_m <= 24.0 + 1e-6


def test_identity_ring_r1_aligned_center_matches_centroid() -> None:
    """Polygon centre must coincide with the target centroid, within a tight
    tolerance (the whole-chip-bbox ring this replaces was NOT centred on the
    centroid -- it was the raw nominal chip footprint)."""
    inp = _make_input(
        centroid_lon=CENTROID_LON, centroid_lat=CENTROID_LAT, fov_m=48.0, source_area_m2=42.3
    )
    tlo = identity_stage(inp)
    lons = [p[0] for p in tlo.projected_target_polygon]
    lats = [p[1] for p in tlo.projected_target_polygon]
    center_lon = (min(lons) + max(lons)) / 2
    center_lat = (min(lats) + max(lats)) / 2
    assert center_lon == pytest.approx(CENTROID_LON, abs=1e-9)
    assert center_lat == pytest.approx(CENTROID_LAT, abs=1e-9)


def test_identity_ring_falls_back_to_bbox_when_centroid_or_fov_missing() -> None:
    """Legacy-join observations (no centroid/fov columns) keep the whole
    chip-bbox ring rather than raising or silently guessing."""
    inp = _make_input(centroid_lon=None, centroid_lat=None, fov_m=None)
    tlo = identity_stage(inp)
    lon_min, lat_min, lon_max, lat_max = BBOX
    assert tlo.projected_target_polygon[0] == (lon_min, lat_min)


# --------------------------------------------------------------------------- #
# DIAGNOSTIC (team-lead follow-up, 2026-07-19): synthetic periodic-texture   #
# reproduction of the "spurious alias lock" hypothesis raised from the R2    #
# blind-review overlays (repeating row-house/panel-array grids). These are   #
# NOT regression gates on cascade correctness -- they characterize a known   #
# limitation of Sobel+Hann+phase-correlation on repeating structure, so the  #
# phenomenon is reproducible rather than resting on a one-off visual read.   #
# See DATA-r2-localization-replay-2026-07-19.md §5.4 for the real-frame      #
# quantitative follow-up this motivated.                                    #
# --------------------------------------------------------------------------- #

PERIOD_PX = 20


def _make_periodic_structure(h: int, w: int, period: int, duty: float = 0.6) -> np.ndarray:
    """Identical repeated 'house' edge structure -- the same silhouette every
    period (no per-period randomness): this is the structural/edge component
    (rooflines, driveway edges) that Sobel gradient isolates, and that a real
    row-house block or regular panel array would present near-identically at
    every period even though fine surface texture differs frame to frame."""
    yy, xx = np.mgrid[0:h, 0:w]
    return ((xx % period) < (duty * period)).astype(float) * ((yy % period) < (duty * period)).astype(float)


def _with_independent_noise(structure: np.ndarray, seed: int, noise: float = 0.08) -> np.ndarray:
    """Independent per-frame noise realization -- simulates cross-vintage
    capture differences (lighting/fine texture) that do NOT travel with a
    roll shift, unlike naively rolling a single noised array (which would
    make ref/mov identical-content-shifted and never alias)."""
    rng = np.random.default_rng(seed)
    return np.clip(0.3 + 0.6 * structure + rng.normal(0, noise, size=structure.shape), 0, 1)


@pytest.mark.parametrize("true_dx_px", [15, 25, 43])  # > PERIOD_PX/2 -- aliasing regime
def test_diagnostic_periodic_texture_produces_psr_confident_alias_lock(true_dx_px: int) -> None:
    """(a) Existence proof: a periodic structure + a true shift larger than
    half the period reproducibly makes ``estimate_shift_masked`` lock onto
    the WRONG period (``ok=True``, PSR clearing ``DEFAULT_MIN_PSR``) while
    the estimated offset is off from the true shift by ~a multiple of the
    period -- i.e. a confident, schema-legal, but wrong lock."""
    h, w = 160, 160
    structure = _make_periodic_structure(h, w, PERIOD_PX)
    shifted = np.roll(structure, shift=(0, true_dx_px), axis=(0, 1))
    ref = _with_independent_noise(structure, seed=1)
    mov = _with_independent_noise(shifted, seed=2)
    mask = np.zeros((h, w), dtype=bool)

    result = estimate_shift_masked(ref, mov, mask)

    assert result.ok
    assert result.psr >= DEFAULT_MIN_PSR
    error_px = abs(result.dx_px - true_dx_px)
    assert error_px > PERIOD_PX / 2, "expected a genuine alias, not a near-correct estimate"
    nearest_multiple_error = min(abs(error_px - k * PERIOD_PX) for k in range(0, 4))
    assert nearest_multiple_error < 1.0, "aliased error should land within ~1px of a period multiple"


@pytest.mark.parametrize("true_dx_px", [3, -3, 5])  # < PERIOD_PX/2 -- non-aliasing regime
def test_diagnostic_periodic_texture_small_shift_still_resolves_correctly(true_dx_px: int) -> None:
    """Contrast case: the same periodic structure with a true shift WELL
    inside half a period is resolved correctly -- the aliasing failure mode
    is specific to shifts exceeding half the period, not periodic content
    in general."""
    h, w = 160, 160
    structure = _make_periodic_structure(h, w, PERIOD_PX)
    shifted = np.roll(structure, shift=(0, true_dx_px), axis=(0, 1))
    ref = _with_independent_noise(structure, seed=1)
    mov = _with_independent_noise(shifted, seed=2)
    mask = np.zeros((h, w), dtype=bool)

    result = estimate_shift_masked(ref, mov, mask)

    assert result.ok
    assert abs(result.dx_px - true_dx_px) < 0.5


def test_diagnostic_periodicity_score_flags_periodic_content_via_alias_psr() -> None:
    """(b) ``periodicity_score``'s discriminative signal is ``alias_psr``
    (a genuine PSR at the best non-trivial lag), not the raw ``score`` ratio
    (which is dominated by Hann-window taper decay for any content, periodic
    or not -- confirmed empirically before wiring this into the real-frame
    diagnostic in DATA-r2 §5.4). A periodic structure's ``alias_psr`` should
    itself clear ``DEFAULT_MIN_PSR`` (i.e. it would pass the cascade's own
    confidence gate if paired against an independently-noised copy of
    itself) and its alias lag should land at ~the true period; non-periodic
    (organic/noise-like) content should not. Not asserting
    ``alias_psr >= DEFAULT_MIN_PSR`` here -- a single-frame self-correlation's
    ``alias_psr`` scale is not a strict 1:1 stand-in for a real ref/mov
    cross-correlation's PSR (test (a) above already demonstrates a real
    cross-correlation aliasing at PSR 12-15, well above the floor); this
    test's claim is the comparative/locational one, which is what the
    real-frame diagnostic (DATA-r2 §5.4) actually consumes."""
    from scipy.ndimage import gaussian_filter

    h, w = 160, 160
    structure = _make_periodic_structure(h, w, PERIOD_PX)
    mask = np.zeros((h, w), dtype=bool)

    periodic_frame = _with_independent_noise(structure, seed=3)
    rng = np.random.default_rng(4)
    organic_frame = gaussian_filter(np.clip(rng.normal(0.5, 0.15, size=(h, w)), 0, 1), sigma=2.0)

    periodic_result = periodicity_score(periodic_frame, mask)
    organic_result = periodicity_score(organic_frame, mask)

    assert periodic_result.alias_psr > organic_result.alias_psr
    assert abs(abs(periodic_result.lag_dx_px) - PERIOD_PX) <= 2.0
