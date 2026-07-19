"""Tests for the Phase-0 emission extension (DESIGN-phase0-emission-extension,
owner-signed 2026-07-19). Covers the §6.3 test-point checklist (points 1-8) plus
the §2.2 boundary accessors and the §2.4 boundary-cell evidence-completeness
invariant.
"""
from __future__ import annotations

import hashlib
import math
from datetime import date

import pytest

from solar_backdating.estimators import ClampContext, EstimatorConfig, get_estimator
from solar_backdating.estimators.emissions import (
    EmissionModel,
    FrameEmission,
    fit_emissions_em,
    frame_loglik,
    gate_frame_emission,
    pool_epoch_frame_emissions,
)
from solar_backdating.estimators.losses import (
    _tvd,
    frame_calibration_loss,
    interval_marginal_nll,
    pairwise_tvd,
)
from solar_backdating.estimators.seam import (
    EpochCell,
    VintageObservation,
    is_already_present,
    is_beyond_window,
)
from solar_backdating.estimators.training_targets import TeacherBracket, build_k_i
from solar_backdating.eval.metrics import tvd as metrics_tvd
from solar_backdating.localization.observation import TargetLocalizationObservation

CP = get_estimator("changepoint")


def _o(y, m, d, pv, qf="usable", conf=None, row=None):
    return VintageObservation(date(y, m, d), pv, confidence=conf, quality_flag=qf, source_row=row)


def _fe(y, m, d, q, e0, e1, row=None, stratum="default"):
    return FrameEmission(date(y, m, d), q=q, e0=e0, e1=e1, stratum=stratum, source_row=row)


def _tlo(**kw):
    base = dict(
        anchor_id="a1",
        capture_date=date(2020, 1, 1),
        building_found=True,
        roof_plane_matched=True,
        target_localized=True,
        transform_type="identity",
        transform_params={},
        registration_confidence=None,
        shift_uncertainty_m=None,
        projected_target_polygon=None,
        failure_reason="",
        cascade_stage="identity",
        abstain=False,
    )
    base.update(kw)
    return TargetLocalizationObservation(**base)


# --------------------------------------------------------------------------- #
# Test point 1 — frame_loglik limit values + monotonicity
# --------------------------------------------------------------------------- #
def test_frame_loglik_q_zero_is_exactly_zero_both_states():
    # Asymmetric e0/e1 to prove the q=0 constant is state-INDEPENDENT.
    f = _fe(2020, 1, 1, q=0.0, e0=0.1, e1=0.97)
    assert frame_loglik(f, True) == 0.0
    assert frame_loglik(f, False) == 0.0


def test_frame_loglik_q_one_is_log_e_exactly():
    f = _fe(2020, 1, 1, q=1.0, e0=0.3, e1=0.8)
    assert frame_loglik(f, True) == math.log(0.8)
    assert frame_loglik(f, False) == math.log(0.3)


def test_frame_loglik_monotone_in_q_for_fixed_e():
    for e in (0.9, 0.1):  # e != 0.5, both sides of 0.5
        vals = [frame_loglik(_fe(2020, 1, 1, q=q / 10.0, e0=e, e1=e), True) for q in range(11)]
        diffs = [b - a for a, b in zip(vals, vals[1:])]
        # strictly monotone (decreasing for e<1): all deltas same, nonzero sign
        assert all(d < 0 for d in diffs)


# --------------------------------------------------------------------------- #
# Test point 2 — pool_epoch_frame_emissions duplicate-injection invariance
# --------------------------------------------------------------------------- #
def test_pool_duplicate_injection_invariance():
    base = [_fe(2018, 1, 1, q=0.9, e0=0.8, e1=0.7, row=0)]
    pooled_base = pool_epoch_frame_emissions(base, 30)[0]

    # inject a near-duplicate within the gap with q STRICTLY below the max
    inj = base + [_fe(2018, 1, 10, q=0.5, e0=0.2, e1=0.3, row=1)]
    pooled_inj = pool_epoch_frame_emissions(inj, 30)[0]

    # the evidence-bearing triple is invariant (the pava/changepoint
    # duplicate-injection tradition, applied to the pooled evidence unit);
    # n_members is pure bookkeeping and legitimately reflects the added member.
    assert pooled_inj.q == pooled_base.q == 0.9
    assert pooled_inj.e0 == pooled_base.e0 == 0.8
    assert pooled_inj.e1 == pooled_base.e1 == 0.7
    assert pooled_base.n_members == 1 and pooled_inj.n_members == 2


def test_pool_equal_q_injection_does_not_displace_representative():
    base = [_fe(2018, 1, 1, q=0.9, e0=0.8, e1=0.7, row=0)]
    # a later near-dup at the SAME q must not steal the representative slot.
    inj = base + [_fe(2018, 1, 10, q=0.9, e0=0.1, e1=0.1, row=1)]
    pooled = pool_epoch_frame_emissions(inj, 30)[0]
    assert pooled.q == 0.9 and pooled.e0 == 0.8 and pooled.e1 == 0.7


def test_pool_max_q_rule_not_soft_or():
    # 5 identical low-q frames must NOT inflate q toward 1 (soft-OR would).
    frames = [_fe(2018, 1, i + 1, q=0.3, e0=0.6, e1=0.6, row=i) for i in range(5)]
    pooled = pool_epoch_frame_emissions(frames, 30)[0]
    assert pooled.q == 0.3


# --------------------------------------------------------------------------- #
# Test point 3 — TLO hard gate combinations (§3.2)
# --------------------------------------------------------------------------- #
def test_gate_target_not_localized_zeros_q_regardless_of_model_q():
    tlo = _tlo(building_found=False, roof_plane_matched=False,
               target_localized=False, failure_reason="building_not_found")
    frame, pending = gate_frame_emission(date(2020, 1, 1), 0.999, 0.9, 0.9, tlo)
    assert frame.q == 0.0
    assert pending is False
    # deep defense: e0/e1 filled to the no-information 0.5 (§3.2)
    assert frame.e0 == 0.5 and frame.e1 == 0.5


def test_gate_localized_but_abstain_still_zeros_q():
    # target_localized=True AND abstain=True is a schema-legal combination
    # (e.g. low_confidence / dark_zone); reading only target_localized would
    # MISS this and wrongly grant full weight.
    tlo = _tlo(abstain=True, failure_reason="low_confidence")
    assert tlo.target_localized is True and tlo.abstain is True
    frame, pending = gate_frame_emission(date(2020, 1, 1), 0.8, 0.7, 0.6, tlo)
    assert frame.q == 0.0
    assert frame.e0 == 0.5 and frame.e1 == 0.5


def test_gate_clean_localized_grants_full_weight():
    tlo = _tlo()  # localized, not abstaining
    frame, pending = gate_frame_emission(date(2020, 1, 1), 0.83, 0.7, 0.6, tlo)
    assert frame.q == 0.83
    assert frame.e0 == 0.7 and frame.e1 == 0.6
    assert pending is False


def test_gate_tlo_none_is_noop_with_localization_pending():
    frame, pending = gate_frame_emission(date(2020, 1, 1), 0.77, 0.7, 0.6, None)
    assert frame.q == 0.77  # NO-OP: not zeroed
    assert frame.e0 == 0.7 and frame.e1 == 0.6  # e0/e1 preserved, not filler
    assert pending is True  # provenance down-passed


def test_gate_tlo_none_wrong_single_expression_regresses():
    """Regression guard: the forbidden single-expression form
    ``q if (tlo and tlo.target_localized) else 0.0`` collapses tlo=None into the
    zeroing branch, contradicting the label layer's 'preserve legacy' policy.
    Prove the correct gate and the wrong one disagree on tlo=None."""
    q_model = 0.77
    tlo = None
    correct_frame, _ = gate_frame_emission(date(2020, 1, 1), q_model, 0.7, 0.6, tlo)
    wrong = q_model if (tlo and tlo.target_localized) else 0.0
    assert correct_frame.q == q_model
    assert wrong == 0.0
    assert correct_frame.q != wrong


# --------------------------------------------------------------------------- #
# Test point 4 — build_k_i four-branch dispatch + empty-overlap raise
# --------------------------------------------------------------------------- #
def _cells():
    return [
        EpochCell(0, None, date(2016, 1, 1)),
        EpochCell(1, date(2016, 1, 1), date(2017, 1, 1)),
        EpochCell(2, date(2017, 1, 1), date(2018, 1, 1)),
        EpochCell(3, date(2018, 1, 1), None, is_beyond_window=True),
    ]


def test_build_k_i_left_censored():
    assert build_k_i(TeacherBracket("left_censored", None, None), _cells()) == frozenset({0})


def test_build_k_i_right_censored():
    assert build_k_i(TeacherBracket("right_censored", None, None), _cells()) == frozenset({3})


def test_build_k_i_interval_exact_single_cell():
    br = TeacherBracket("interval", date(2016, 1, 1), date(2017, 1, 1))
    assert build_k_i(br, _cells()) == frozenset({1})


def test_build_k_i_interval_coarse_covers_multiple_student_cells():
    br = TeacherBracket("interval", date(2016, 6, 1), date(2018, 1, 1))
    assert build_k_i(br, _cells()) == frozenset({1, 2})


def test_build_k_i_interval_fine_contained_in_one_cell():
    br = TeacherBracket("interval", date(2016, 3, 1), date(2016, 9, 1))
    assert build_k_i(br, _cells()) == frozenset({1})


def test_build_k_i_census_bound_matches_cutoff_cell():
    # census cutoff date == the cell whose end_date equals it (cell 1 ends at
    # 2017-01-01); the evidence_cutoff synthetic epoch guarantees this cell.
    br = TeacherBracket("census_bound", None, date(2017, 1, 1))
    assert build_k_i(br, _cells()) == frozenset({1})


def test_build_k_i_empty_overlap_raises():
    # zero-width (degenerate) teacher interval overlaps no cell -> pipeline bug.
    br = TeacherBracket("interval", date(2017, 1, 1), date(2017, 1, 1))
    with pytest.raises(ValueError):
        build_k_i(br, _cells())


def test_build_k_i_census_bound_no_match_raises():
    br = TeacherBracket("census_bound", None, date(2099, 1, 1))
    with pytest.raises(ValueError):
        build_k_i(br, _cells())


def test_build_k_i_unknown_kind_raises():
    with pytest.raises(ValueError):
        build_k_i(TeacherBracket("bogus", None, None), _cells())


# --------------------------------------------------------------------------- #
# Test point 5 — interval_marginal_nll algebra
# --------------------------------------------------------------------------- #
def test_interval_marginal_nll_singleton_is_plain_nll():
    posterior = (0.1, 0.6, 0.3)
    assert interval_marginal_nll(posterior, frozenset({1})) == pytest.approx(-math.log(0.6))


def test_interval_marginal_nll_invariant_to_mass_redistribution_within_k():
    k_i = frozenset({0, 1})
    a = interval_marginal_nll((0.1, 0.6, 0.3), k_i)  # mass 0.7
    b = interval_marginal_nll((0.4, 0.3, 0.3), k_i)  # mass 0.7, different split
    assert a == pytest.approx(b)
    assert a == pytest.approx(-math.log(0.7))


def test_frame_calibration_loss_abstain_only_penalizes_q():
    # target q≈0: loss grows as q rises; e0/e1 have no effect.
    lo = frame_calibration_loss(_fe(2020, 1, 1, q=0.01, e0=0.9, e1=0.1), "abstain")
    hi = frame_calibration_loss(_fe(2020, 1, 1, q=0.9, e0=0.1, e1=0.9), "abstain")
    assert hi > lo
    assert lo == pytest.approx(-math.log(0.99))


def test_frame_calibration_loss_present_uses_e1_and_q():
    f = _fe(2020, 1, 1, q=0.8, e0=0.3, e1=0.9)
    assert frame_calibration_loss(f, "present") == pytest.approx(-math.log(0.9) - math.log(0.8))
    assert frame_calibration_loss(f, "absent") == pytest.approx(-math.log(0.3) - math.log(0.8))


# --------------------------------------------------------------------------- #
# Test point 6 — banked-panel byte-level regression (discrete path unchanged)
# --------------------------------------------------------------------------- #
# Golden digest computed from the DISCRETE changepoint path and verified
# byte-identical before vs after the emission-extension edits (git-stash diff,
# 2026-07-19). The banked fullstack/panel-B corpora are absent in this
# environment (owner space cleanup), so a deterministic synthetic panel stands
# in for the banked-panel regression gate convention (frozen expected output,
# assert reproduction). The frame branch is INERT here (frame_emissions unset).
_DISCRETE_GOLDEN = "447ef33a8da70ba00153052d7605aad199fbbe74752c5d4bb290ae6fd9c2b54b"


def _discrete_panel():
    seqs = [
        [_o(2016 + i, 1, 1, pv, row=i) for i, pv in enumerate(["0", "0", "0", "1", "1"])],
        [_o(2016 + i, 1, 1, "0", row=i) for i in range(4)],
        [_o(2016 + i, 1, 1, "1", row=i) for i in range(3)],
        [_o(2016 + i, 1, 1, pv, row=i) for i, pv in enumerate(["0", "0", "1", "0", "0"])],
        [_o(2016, 1, 1, "0", row=0), _o(2017, 1, 1, "", row=1),
         _o(2018, 1, 1, "1", row=2), _o(2019, 1, 1, "1", row=3)],
        [_o(2018, 3, 30, "0", "usable", row=0), _o(2019, 5, 30, "0", "usable", row=1),
         _o(2020, 1, 1, "1", "ambiguous", row=2), _o(2020, 6, 1, "1", "ambiguous", row=3),
         _o(2021, 1, 1, "1", "usable", row=4)],
        [],
        [_o(2016, 1, 1, "0", row=0), _o(2018, 1, 1, "1", row=1),
         _o(2018, 1, 10, "1", row=2), _o(2018, 1, 20, "1", row=3)],
        [_o(2016 + i, 1, 1, "1", "ambiguous", row=i) for i in range(3)],
    ]
    return seqs


def test_discrete_path_byte_level_regression():
    seqs = _discrete_panel()
    fitted = fit_emissions_em([s for s in seqs if s], gap_days=30)
    clamps = [
        ClampContext(),
        ClampContext(ceiling_date=date(2020, 6, 30)),
        ClampContext(census_end_date=date(2019, 6, 30)),
        ClampContext(ceiling_date=date(2019, 1, 1), census_end_date=date(2019, 6, 30)),
    ]
    parts = []
    for si, seq in enumerate(seqs):
        for ci, clamp in enumerate(clamps):
            for cfg in (
                EstimatorConfig(),
                EstimatorConfig(emissions=fitted),
                EstimatorConfig(decoder_epoch_gap_days=45, emissions=fitted),
            ):
                parts.append(f"{si}|{ci}|{repr(CP(seq, clamp, cfg))}")
    digest = hashlib.sha256("\n".join(parts).encode()).hexdigest()
    assert digest == _DISCRETE_GOLDEN


def test_frame_emissions_none_is_inert():
    obs = [_o(2016 + i, 1, 1, pv, row=i) for i, pv in enumerate(["0", "0", "1", "1"])]
    default = CP(obs, ClampContext(), EstimatorConfig())
    explicit_none = CP(obs, ClampContext(), EstimatorConfig(frame_emissions=None))
    assert default == explicit_none


# --------------------------------------------------------------------------- #
# Test point 7 — emissions / frame_emissions mutual-exclusion raise
# --------------------------------------------------------------------------- #
def test_emissions_and_frame_emissions_mutually_exclusive():
    em = EmissionModel(strata=("default",), matrices=(((0.9, 0.05, 0.05), (0.05, 0.9, 0.05)),))
    frames = [_fe(2018, 1, 1, q=0.9, e0=0.9, e1=0.9)]
    with pytest.raises(ValueError):
        CP([], ClampContext(), EstimatorConfig(emissions=em, frame_emissions=frames))


# --------------------------------------------------------------------------- #
# Test point 8 — TVD statistic order-invariance + cross-check vs metrics.tvd
# --------------------------------------------------------------------------- #
def test_pairwise_tvd_order_invariance_as_multiset():
    reps = [
        {2018: 3, 2019: 1},
        {2018: 1, 2019: 3},
        {2018: 2, 2019: 2},
    ]
    base = sorted(pairwise_tvd(reps))
    permuted = sorted(pairwise_tvd([reps[2], reps[0], reps[1]]))
    assert base == pytest.approx(permuted)


def test_pairwise_tvd_matches_metrics_reference():
    from collections import Counter
    from itertools import combinations

    reps = [
        Counter({2018: 3, 2019: 1, 2020: 0}),
        Counter({2018: 1, 2019: 2, 2020: 1}),
        Counter({2019: 1, 2020: 3}),
    ]
    got = pairwise_tvd(reps)
    ref = [metrics_tvd(a, b) for a, b in combinations(reps, 2)]
    assert got == pytest.approx(ref)
    # single-pair helper also agrees with the reference formula
    assert _tvd(reps[0], reps[1]) == pytest.approx(metrics_tvd(reps[0], reps[1]))


# --------------------------------------------------------------------------- #
# §2.2 boundary accessors
# --------------------------------------------------------------------------- #
def test_is_already_present_on_open_left_map():
    obs = [_o(2016 + i, 1, 1, "1", row=i) for i in range(3)]  # all present -> open-left MAP
    post = CP(obs, ClampContext(), EstimatorConfig())
    assert post.map_index == 0 and post.map_interval_start is None
    assert is_already_present(post) is True
    assert is_beyond_window(post) is False


def test_is_beyond_window_on_all_absent():
    obs = [_o(2016 + i, 1, 1, "0", row=i) for i in range(4)]  # all absent -> beyond-window MAP
    post = CP(obs, ClampContext(), EstimatorConfig())
    assert is_beyond_window(post) is True
    assert is_already_present(post) is False


def test_accessors_false_on_interior_changepoint():
    obs = [_o(2016 + i, 1, 1, pv, row=i) for i, pv in enumerate(["0", "0", "1", "1"])]
    post = CP(obs, ClampContext(), EstimatorConfig())
    assert is_already_present(post) is False
    assert is_beyond_window(post) is False


# --------------------------------------------------------------------------- #
# §2.4 boundary-cell evidence-completeness invariant + frame-branch sanity
# --------------------------------------------------------------------------- #
def test_uninformative_early_frames_do_not_bias_boundary():
    # §2.4: at q=0 an early frame contributes the SAME log(1)=0 to both states,
    # even with a wildly present-leaning e -- so it cannot systematically bias
    # the tau=0 (already-present) vs tau>0 (absent->present) hypotheses.
    f = _fe(2016, 1, 1, q=0.0, e0=0.05, e1=0.95)
    assert frame_loglik(f, True) == 0.0
    assert frame_loglik(f, False) == 0.0
    # the state-branch gap shrinks monotonically to 0 as q -> 0 (the exact
    # invariant is the q=0 limit above; nonzero q legitimately differs by O(q)).
    gaps = [
        abs(frame_loglik(_fe(2016, 1, 1, q=q, e0=0.05, e1=0.95), True)
            - frame_loglik(_fe(2016, 1, 1, q=q, e0=0.05, e1=0.95), False))
        for q in (0.5, 0.1, 0.01)
    ]
    assert gaps[0] > gaps[1] > gaps[2] > 0


def test_frame_branch_recovers_changepoint():
    frames = [
        _fe(2016, 1, 1, q=0.95, e0=0.95, e1=0.05, row=0),
        _fe(2017, 1, 1, q=0.95, e0=0.95, e1=0.05, row=1),
        _fe(2018, 1, 1, q=0.95, e0=0.05, e1=0.95, row=2),
    ]
    post = CP([], ClampContext(), EstimatorConfig(frame_emissions=frames))
    assert post.map_date == "2018-01-01"
    assert post.map_index == 2


def test_frame_branch_low_q_epochs_dropped():
    # every frame uninformative (q below the abstain floor) -> undated.
    frames = [_fe(2016 + i, 1, 1, q=1e-6, e0=0.9, e1=0.9, row=i) for i in range(3)]
    post = CP([], ClampContext(), EstimatorConfig(frame_emissions=frames))
    assert post.map_date == ""
    assert post.p_undated == 1.0
