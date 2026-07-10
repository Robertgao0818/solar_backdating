"""Tests for the monotone single-changepoint posterior decoder (changepoint.py)."""
from __future__ import annotations

import os
from datetime import date
from pathlib import Path

import pytest

from solar_backdating.estimators import (
    ClampContext,
    EstimatorConfig,
    get_estimator,
)
from solar_backdating.estimators.changepoint import CohortPrior, _cell_log_prior, _epoch_loglik
from solar_backdating.estimators.emissions import EmissionModel, fit_emissions_em
from solar_backdating.estimators.seam import VintageObservation

CP = get_estimator("changepoint")
FPD = get_estimator("fpd")
SUSTAINED = get_estimator("sustained")
PAVA = get_estimator("pava")


def _obs(y, m, d, pv, conf=None, row=None, quality_flag="usable"):
    return VintageObservation(
        capture_date=date(y, m, d),
        pv_present=pv,
        confidence=conf,
        source_row=row,
        quality_flag=quality_flag,
    )


def _yearly(pvs, start=2016):
    """One frame per year -> each is its own epoch under any of {16,30,45}."""
    return [_obs(start + i, 1, 1, pv, row=i) for i, pv in enumerate(pvs)]


# --------------------------------------------------------------------------- #
# Known changepoint recovery / degenerate cases
# --------------------------------------------------------------------------- #
def test_known_changepoint_recovered():
    obs = _yearly(["0", "0", "0", "1", "1", "1"])
    post = CP(obs, ClampContext(), EstimatorConfig())
    assert post.estimator == "changepoint"
    assert post.map_interval_start == date(2018, 1, 1)
    assert post.map_interval_end == date(2019, 1, 1)
    assert post.map_date == "2019-01-01"
    assert post.p_undated < 0.5


def test_all_absent_high_p_undated():
    obs = _yearly(["0", "0", "0", "0"])
    post = CP(obs, ClampContext(), EstimatorConfig())
    assert post.map_date == ""
    assert post.p_undated > 0.5
    assert post.map_index == len(post.epochs) - 1
    assert post.p_undated == max(post.posterior)


def test_all_present_open_left():
    obs = _yearly(["1", "1", "1"])
    post = CP(obs, ClampContext(), EstimatorConfig())
    assert post.map_index == 0
    assert post.map_interval_start is None
    assert post.map_date == "2016-01-01"
    assert post.p_undated < 0.5


def test_empty_observations_undated():
    post = CP([], ClampContext(), EstimatorConfig())
    assert post.map_date == ""
    assert post.p_undated == 1.0
    assert abs(sum(post.posterior) - 1.0) < 1e-9


# --------------------------------------------------------------------------- #
# Epoch-collapse invariance (AC5)
# --------------------------------------------------------------------------- #
def test_duplicate_frame_invariance():
    obs = _yearly(["0", "0", "1", "1"])
    base = CP(obs, ClampContext(), EstimatorConfig())
    dup = list(obs)
    dup.insert(3, _obs(2018, 1, 1, "1", row=99))  # exact dup
    dup.insert(4, _obs(2018, 1, 10, "1", row=100))  # near-dup within decoder gap (30d default)
    other = CP(dup, ClampContext(), EstimatorConfig())
    assert other.map_date == base.map_date
    assert other.map_index == base.map_index
    assert other.posterior == base.posterior


def test_duplicate_frame_invariance_nonmonotone_blip():
    obs = _yearly(["0", "0", "1", "0", "0"])
    base = CP(obs, ClampContext(), EstimatorConfig())
    assert base.map_date == ""  # isolated blip out-voted -> undated
    dup = list(obs)
    dup.append(_obs(2018, 1, 5, "1", row=100))
    dup.append(_obs(2018, 1, 10, "1", row=101))
    dup.append(_obs(2018, 1, 15, "1", row=102))
    other = CP(dup, ClampContext(), EstimatorConfig())
    assert other.map_date == base.map_date == ""
    assert other.posterior == base.posterior


def test_abstain_excluded_with_default_emissions():
    obs = _yearly(["0", "0", "1", "1"])
    base = CP(obs, ClampContext(), EstimatorConfig())
    laced = list(obs)
    laced.insert(2, _obs(2017, 6, 1, "", row=50))  # abstain between epochs
    other = CP(laced, ClampContext(), EstimatorConfig())
    assert other.map_date == base.map_date

    all_abstain = _yearly(["", "", ""])
    post = CP(all_abstain, ClampContext(), EstimatorConfig())
    assert post.p_undated == 1.0
    assert post.map_date == ""


def test_abstain_insertion_never_shifts_map_even_under_asymmetric_abstain_column():
    """SUPERSEDED test (orchestrator design correction 2, 2026-07-03): the
    original version of this test asserted that a fitted matrix with abstain
    strongly informative for present_state (rare under absent_state, common
    under present_state) KEEPS an inserted abstain epoch and CAN shift the
    decision -- the old "resolution-conditioning" framing. That framing is now
    retired: ISSUE-04 established abstain as resolution-caused CENSORING, so
    it must be state-INDEPENDENT by construction, not something an emission
    matrix gets to have an opinion about per-state. This version proves the
    new invariant with a matrix whose ABSTAIN column is even more wildly
    asymmetric between states (0.50 vs 0.98) than the original (0.01 vs 0.60)
    -- specifically chosen so its {absent, present} conditional ratio (9:1 /
    1:9) exactly matches the default symmetric-noise config's own eps=0.1
    ratio, isolating the abstain column as the ONLY thing that differs. Under
    correction 2 the abstain epoch is dropped before scoring (not merely
    down-weighted), so inserting it changes NOTHING -- posteriors are
    byte-identical with/without it, under BOTH configs, and the two configs
    agree with each other since their scored-symbol odds are equal."""
    obs = _yearly(["0", "0", "1", "0", "0"])  # isolated blip -> undated under default
    laced = list(obs) + [_obs(2018, 6, 1, "", row=99)]

    default_base = CP(obs, ClampContext(), EstimatorConfig())
    default_laced = CP(laced, ClampContext(), EstimatorConfig())
    assert default_laced == default_base
    assert default_base.map_date == ""  # isolated blip still outvoted -> undated

    # {absent,present} conditional ratio == 9:1 / 1:9, matching eps=0.1 exactly;
    # abstain column (0.50 vs 0.98) is wildly asymmetric but must have zero effect.
    asymmetric_abstain = EmissionModel(
        strata=("usable", "default"),
        matrices=(
            ((0.45, 0.05, 0.50), (0.002, 0.018, 0.98)),
            ((0.45, 0.05, 0.50), (0.002, 0.018, 0.98)),
        ),
    )
    fitted_base = CP(obs, ClampContext(), EstimatorConfig(emissions=asymmetric_abstain))
    fitted_laced = CP(laced, ClampContext(), EstimatorConfig(emissions=asymmetric_abstain))
    assert fitted_laced == fitted_base  # abstain insertion: zero effect
    assert fitted_base.map_date == default_base.map_date == ""  # matching odds -> same MAP
    assert fitted_base.posterior == default_base.posterior  # same ratio -> same posterior


def test_epoch_loglik_censoring_exact_renormalization():
    """Direct unit check of correction 2A's formula: P(sym|state,scored) =
    P(sym|state) / (P(absent|state)+P(present|state)). Two matrices with an
    IDENTICAL 9:1 {absent,present} ratio but wildly different abstain mass
    (0.10 vs 0.90) must produce byte-identical scored log-likelihoods for
    every (symbol, state) combination -- the abstain column's magnitude must
    never leak into the scored-conditional probability."""
    low_abstain = EmissionModel(
        strata=("s",), matrices=(((0.81, 0.09, 0.10), (0.09, 0.81, 0.10)),)
    )
    high_abstain = EmissionModel(
        strata=("s",), matrices=(((0.09, 0.01, 0.90), (0.01, 0.09, 0.90)),)
    )
    for symbol in ("absent", "present"):
        for state_is_present in (False, True):
            a = _epoch_loglik(symbol, state_is_present, "s", EstimatorConfig(emissions=low_abstain))
            b = _epoch_loglik(
                symbol, state_is_present, "s", EstimatorConfig(emissions=high_abstain)
            )
            assert a == pytest.approx(b, abs=1e-9)


def test_ambiguous_run_near_ceiling_dates_correctly_not_clamp_inverted():
    """Regression test for the real production anchor (sf=34237, JNB0324,
    traced 2026-07-03) that motivated correction 2: a run of ambiguous-only
    absent epochs sits between the last usable-scored absent and a lone
    usable-scored present near the imagery ceiling. Pre-correction-2, the
    ambiguous run was either scored as hard "absent" evidence (correction 1's
    starting point) or as soft-but-still-absent-leaning "abstain" evidence
    (the zero-training-count emission artifact correction 2 targets) -- both
    pushed the MAP changepoint all the way up to the lone present epoch,
    past the imagery ceiling, triggering clamp_inverted -> undated. Under
    correction 2 the ambiguous run is dropped entirely from both the
    likelihood and the cell lattice: the interval anchors on the last
    USABLE-scored absent epoch, phantom-caps at the ceiling, and the anchor
    dates cleanly -- matching production's own usable-anchored bound."""
    obs = [
        _obs(2018, 3, 30, "0", row=0, quality_flag="usable"),
        _obs(2019, 5, 30, "0", row=1, quality_flag="usable"),
        _obs(2020, 5, 31, "0", row=2, quality_flag="usable"),
        _obs(2021, 7, 30, "0", row=3, quality_flag="usable"),
        _obs(2021, 10, 30, "0", row=4, quality_flag="ambiguous"),
        _obs(2022, 3, 30, "0", row=5, quality_flag="ambiguous"),
        _obs(2023, 1, 30, "0", row=6, quality_flag="ambiguous"),
        _obs(2024, 2, 29, "0", row=7, quality_flag="ambiguous"),
        _obs(2025, 2, 28, "", row=8, quality_flag="unusable"),
        _obs(2025, 5, 30, "1", row=9, quality_flag="usable"),
    ]
    ceiling = date(2024, 2, 21)
    clamp = ClampContext(ceiling_date=ceiling)
    config = EstimatorConfig(decoder_epoch_gap_days=45)

    post = CP(obs, clamp, config)
    assert post.map_date == "2024-02-21"  # phantom-capped, DATED
    assert post.map_interval_start == date(2021, 7, 30)  # anchored on last usable absent
    assert post.map_interval_end == ceiling
    assert "clamped_earliest_present" not in post.notes
    assert "clamp_inverted" not in post.notes  # the bug this test guards against
    assert post.p_undated < 0.5

    # Same result under a fitted matrix with the real degenerate zero-count
    # 'ambiguous' stratum row (uniform present_state fallback) -- proves the
    # fix holds under the exact emission-model shape that caused the bug, not
    # just the symmetric-noise fallback.
    import dataclasses

    fitted = EmissionModel(
        strata=("ambiguous", "usable", "default"),
        matrices=(
            ((0.0938, 0.0, 0.9062), (1 / 3, 1 / 3, 1 / 3)),
            ((0.9775, 0.0128, 0.0098), (0.0735, 0.9163, 0.0102)),
            ((0.9566, 0.0125, 0.0310), (0.0734, 0.9147, 0.0120)),
        ),
    )
    post_fitted = CP(obs, clamp, dataclasses.replace(config, emissions=fitted))
    assert post_fitted.map_date == "2024-02-21"
    assert post_fitted.map_interval_start == date(2021, 7, 30)
    assert "clamp_inverted" not in post_fitted.notes


def test_ambiguous_run_does_not_define_interval_boundary():
    """Direct check that non-usable epochs never define a cell boundary: the
    number of tau-cells (T+1) and every cell's start/end date must be
    identical whether or not the trailing ambiguous-only run is present --
    only the usable-scored epochs (2016 absent, 2018 present) define cells."""
    obs = [
        _obs(2016, 1, 1, "0", row=0, quality_flag="usable"),
        _obs(2018, 1, 1, "1", row=1, quality_flag="usable"),
    ]
    with_ambiguous_run = list(obs) + [
        _obs(2019, 6, 1, "0", row=90, quality_flag="ambiguous"),
        _obs(2020, 6, 1, "0", row=91, quality_flag="unusable"),
    ]
    config = EstimatorConfig(decoder_epoch_gap_days=30)
    base = CP(obs, ClampContext(), config)
    laced = CP(with_ambiguous_run, ClampContext(), config)
    assert laced.epochs == base.epochs  # identical cell lattice
    assert laced.posterior == base.posterior
    assert laced.map_date == base.map_date


# --------------------------------------------------------------------------- #
# Usable-only evidence gate (orchestrator design correction, 2026-07-03):
# ambiguous/unusable-flagged scored verdicts are censoring-with-cause, not
# dispositive PRESENT/ABSENT evidence -- mirrors production's
# usable_observations/_usable filters (scan_decision.py / infer_install_dates.py).
# --------------------------------------------------------------------------- #
def test_ambiguous_verdicts_do_not_shift_map():
    """Two ambiguous-flagged '1' verdicts are gap-collapsed into the FIRST
    absent epoch (2016). Under the pre-correction raw-count vote this would
    flip that epoch's majority to 'present' (2 ambiguous-present vs 1
    usable-absent) and could drag the changepoint earlier. Under the usable-
    only vote the epoch stays 'absent' (its only usable member), so the MAP
    is identical to the clean base case."""
    obs = _yearly(["0", "0", "0", "1", "1", "1"])
    base = CP(obs, ClampContext(), EstimatorConfig())
    assert base.map_date == "2019-01-01"

    laced = list(obs) + [
        _obs(2016, 1, 10, "1", row=90, quality_flag="ambiguous"),
        _obs(2016, 1, 20, "1", row=91, quality_flag="ambiguous"),
    ]
    other = CP(laced, ClampContext(), EstimatorConfig())
    assert other.map_date == base.map_date == "2019-01-01"
    assert other.map_index == base.map_index
    assert other.posterior == base.posterior


def test_all_ambiguous_sequence_undated_with_reason():
    """A sequence with real scored verdicts but NO usable-flagged member at all:
    every epoch collapses to the abstain symbol (no usable evidence anywhere),
    the symmetric-noise fallback drops them all (epoch_symbol() != 'abstain'
    filter), and the decoder correctly reports 'no_scored_evidence' rather
    than 'no_observations' (the raw epochs did exist, just carried no usable
    evidence) -- same distinction pava.py makes for its own raw-abstain case."""
    obs = [
        _obs(2016, 1, 1, "0", row=0, quality_flag="ambiguous"),
        _obs(2018, 1, 1, "1", row=1, quality_flag="ambiguous"),
        _obs(2020, 1, 1, "1", row=2, quality_flag="unusable"),
    ]
    post = CP(obs, ClampContext(), EstimatorConfig())
    assert post.map_date == ""
    assert post.p_undated == 1.0
    assert post.notes == "no_scored_evidence"


def test_usable_only_majority_overrides_ambiguous_epoch_under_fitted_emissions():
    """Same mixed-epoch scenario as
    test_epoch_symbol_mixed_epoch_usable_vote_overrides_ambiguous_majority, but
    exercised end-to-end through the decoder with a fitted emission model
    (has_emissions=True path, which keeps every epoch including abstain ones):
    the epoch's stratum is the majority quality_flag among ALL members
    ('ambiguous', 2-to-1), but its SYMBOL is driven purely by the single usable
    '0' verdict -> 'absent', not the ambiguous-majority 'present'."""
    from solar_backdating.estimators.epochs import collapse_epochs, epoch_symbol

    obs = [
        _obs(2016, 1, 1, "1", row=0, quality_flag="ambiguous"),
        _obs(2016, 1, 3, "1", row=1, quality_flag="ambiguous"),
        _obs(2016, 1, 5, "0", row=2, quality_flag="usable"),
    ]
    epochs = collapse_epochs(obs, gap_days=30)
    assert len(epochs) == 1
    assert epochs[0].stratum == "ambiguous"  # majority quality_flag among ALL members
    assert epoch_symbol(epochs[0]) == "absent"  # usable-only vote


# --------------------------------------------------------------------------- #
# Determinism
# --------------------------------------------------------------------------- #
def test_determinism_shuffled():
    obs = _yearly(["0", "1", "0", "1", "1"])
    a = CP(obs, ClampContext(), EstimatorConfig())
    b = CP(obs, ClampContext(), EstimatorConfig())
    assert a == b
    shuffled = [obs[3], obs[0], obs[4], obs[1], obs[2]]
    c = CP(shuffled, ClampContext(), EstimatorConfig())
    assert c == a


# --------------------------------------------------------------------------- #
# Sustained-generalization property (D1(ii)/D2: "sustained is its MAP under
# symmetric noise + flat prior")
# --------------------------------------------------------------------------- #
def test_sustained_generalization_clean_monotone():
    obs = _yearly(["0", "0", "0", "1", "1", "1"])
    dec = CP(obs, ClampContext(), EstimatorConfig())
    sus = SUSTAINED(obs, ClampContext(), EstimatorConfig())
    assert dec.map_date == sus.map_date


def test_sustained_generalization_interior_single_blip():
    obs = _yearly(["0", "0", "1", "0", "1", "1"])
    dec = CP(obs, ClampContext(), EstimatorConfig())
    sus = SUSTAINED(obs, ClampContext(), EstimatorConfig())
    assert dec.map_date == sus.map_date


def test_sustained_generalization_trailing_noise_archetype():
    """t00005236-40-style archetype: early onset (2018), then a BURST of
    correlated near-duplicate absent frames within a single short window in
    2021 (an imaging-artifact cluster), followed by one more present frame.
    Sustained's unbounded suffix ratio (raw frame counts, no epoch collapsing)
    is dragged below 0.5 at the true onset by the burst and locks onto the
    final frame far in the future. The decoder's epoch collapse reduces the
    whole burst to ONE unit of evidence (default decoder_epoch_gap_days=30
    merges the 5 correlated frames, spaced <=16 days apart, into a single
    epoch) so the single-changepoint total-likelihood comparison still
    recognizes 2018 as the best-fitting changepoint."""
    seq = [
        (date(2013, 1, 1), "0"),
        (date(2014, 1, 1), "0"),
        (date(2015, 1, 1), "0"),
        (date(2016, 1, 1), "0"),
        (date(2017, 1, 1), "0"),
        (date(2018, 1, 1), "1"),  # true early onset
        (date(2019, 1, 1), "1"),
        (date(2020, 1, 1), "1"),
        (date(2021, 1, 1), "0"),
        (date(2021, 1, 10), "0"),
        (date(2021, 1, 20), "0"),
        (date(2021, 2, 5), "0"),
        (date(2021, 2, 15), "0"),  # end of the correlated noisy-absent burst
        (date(2021, 3, 1), "1"),  # resumes present -> fools sustained's suffix ratio
    ]
    obs = [_obs(d.year, d.month, d.day, pv, row=i) for i, (d, pv) in enumerate(seq)]
    fpd = FPD(obs, ClampContext(), EstimatorConfig())
    sus = SUSTAINED(obs, ClampContext(), EstimatorConfig())
    dec = CP(obs, ClampContext(), EstimatorConfig())

    assert fpd.map_date == "2018-01-01"
    assert sus.map_date == "2021-03-01"  # sustained over-dates by 3+ years
    assert dec.map_date == "2018-01-01"  # decoder recovers the early onset
    assert dec.map_date != sus.map_date


# --------------------------------------------------------------------------- #
# CohortPrior (ISSUE-03 hook)
# --------------------------------------------------------------------------- #
def test_cohort_prior_none_is_flat():
    obs = _yearly(["0", "0", "1", "1"])
    post = CP(obs, ClampContext(), EstimatorConfig())
    for cell in post.epochs:
        assert _cell_log_prior(cell, None) == 0.0


def test_concentrated_cohort_prior_moves_map():
    obs = _yearly(["0", "0", "1", "1"])
    base = CP(obs, ClampContext(), EstimatorConfig())
    assert base.map_date == "2018-01-01"  # flat-prior baseline

    prior = CohortPrior(year_log_mass=((2016, 50.0),), beyond_log_mass=-50.0)
    shifted = CP(obs, ClampContext(), EstimatorConfig(cohort_prior=prior))
    assert shifted.map_index < base.map_index  # pulled strictly earlier
    assert shifted.map_date < base.map_date


def test_cohort_prior_beyond_window_mass():
    obs = _yearly(["0", "0", "0", "0"])  # already undated by likelihood alone
    prior = CohortPrior(year_log_mass=((2016, 50.0),), beyond_log_mass=-50.0)
    post = CP(obs, ClampContext(), EstimatorConfig(cohort_prior=prior))
    # A heavily-penalized beyond-window cell competing against a heavily-boosted
    # dated cell -> posterior mass should shift toward the dated cell even
    # though the raw (flat-prior) likelihood favored "undated".
    assert post.p_undated < CP(obs, ClampContext(), EstimatorConfig()).p_undated


# --------------------------------------------------------------------------- #
# Clamp scope (phantom cap parity + clamp_inverted + marker_missed_pv)
# --------------------------------------------------------------------------- #
def test_post_census_reference_frame_does_not_change_decoder_posterior():
    census = date(2024, 2, 21)
    evidence = [
        _obs(2022, 6, 1, "0"),
        _obs(2024, 2, 21, "1"),
    ]
    contaminated = [*evidence, _obs(2024, 8, 1, "0")]

    base = CP(evidence, ClampContext(ceiling_date=census), EstimatorConfig())
    other = CP(
        contaminated,
        ClampContext(ceiling_date=census),
        EstimatorConfig(),
    )

    assert other == base


def test_census_presence_does_not_create_date_without_historical_evidence():
    census = date(2024, 2, 21)
    post = CP(
        [_obs(2024, 8, 1, "0")],
        ClampContext(ceiling_date=census),
        EstimatorConfig(),
    )

    assert post.map_date == ""
    assert post.p_undated == 1.0


def test_census_presence_replaces_phantom_future_cap():
    obs = _yearly(["0", "0", "1", "1"])  # first present = 2018-01-01
    ceiling = date(2017, 6, 1)
    cp_post = CP(obs, ClampContext(ceiling_date=ceiling), EstimatorConfig())
    pava_post = PAVA(obs, ClampContext(ceiling_date=ceiling), EstimatorConfig())
    assert cp_post.map_date == pava_post.map_date == "2017-06-01"
    assert cp_post.map_interval_end == pava_post.map_interval_end == ceiling
    assert "clamped_earliest_present" not in cp_post.notes
    assert "clamped_earliest_present" in pava_post.notes


def test_census_presence_prevents_clamp_inverted_with_earlier_history():
    obs = [
        _obs(2014, 1, 1, "0"),
        _obs(2016, 1, 1, "0"),
        _obs(2017, 1, 1, "1"),
    ]
    ceiling = date(2015, 6, 1)
    post = CP(obs, ClampContext(ceiling_date=ceiling), EstimatorConfig())
    assert post.map_date == "2015-06-01"
    assert "clamp_inverted" not in post.notes
    assert post.map_index != len(post.epochs) - 1


def test_post_census_absent_frames_are_reference_only():
    obs = _yearly(["0", "0", "0", "0"], start=2016)  # last absent = 2019-01-01
    census_end = date(2017, 6, 1)  # <= last absent
    post = CP(obs, ClampContext(census_end_date=census_end), EstimatorConfig())
    assert "marker_missed_pv" not in post.notes
    assert post.map_date == "2017-06-01"
    assert post.map_index != len(post.epochs) - 1


def test_marker_missed_pv_not_triggered_when_before_census_end():
    obs = _yearly(["0", "0", "0", "0"], start=2016)  # last absent = 2019-01-01
    census_end = date(2025, 1, 1)  # after all observations -> scan just hasn't caught up
    post = CP(obs, ClampContext(census_end_date=census_end), EstimatorConfig())
    assert "marker_missed_pv" not in post.notes


# --------------------------------------------------------------------------- #
# Regression gate on the extended (Panel B) panel (G1-G6), mirrors
# test_regression_panel.py's skipif-guarded style.
# --------------------------------------------------------------------------- #
PANEL_B_DIR = Path(
    os.environ.get(
        "PANEL_REPAIR_DIR",
        Path.home() / "zasolar_data/geid_temporal/panel_repair_20260703",
    )
)
PANEL_B_LONG = PANEL_B_DIR / "analysis_extended/long_all_extended.csv"
PANEL_B_SAMPLE_ANCHORS = PANEL_B_DIR / "sample/sample_anchors_extended.csv"
SAMPLE_MANIFEST = (
    Path.home() / "zasolar_data/geid_temporal/mini_reliability_20260624/sample/sample_manifest.json"
)

pytestmark_panel_b = pytest.mark.skipif(
    not PANEL_B_LONG.exists() or not PANEL_B_SAMPLE_ANCHORS.exists(),
    reason="extended (Panel B) panel not present",
)


@pytestmark_panel_b
def test_gates_g1_through_g6_on_extended_panel():
    """G1-G6 (ISSUE-02 spec 'Gates' section), same-run comparisons on the
    extended 38-unit panel with EM-fitted emissions. Chosen lever (pre-declared
    in the gate iteration policy): decoder_epoch_gap_days=45 -- the default 30
    narrowly fails G1 (-0.006) and G3 (ties pava, needs strict >); 45 (still
    inside the {16,30,45} lever set) further suppresses the correlated-frame
    double-counting that specifically hurts this decoder on this population,
    and clears all 6 gates. See the ISSUE-02 final report for the full table
    and the honest failing-at-30 baseline."""
    import dataclasses

    from solar_backdating.eval.panel_io import load_inventory_weights, load_panel, load_strata
    from scripts.validation.estimator_harness import run_estimator

    panel, chip_of = load_panel(PANEL_B_LONG)
    strata = load_strata(PANEL_B_SAMPLE_ANCHORS)
    weights = load_inventory_weights(SAMPLE_MANIFEST if SAMPLE_MANIFEST.exists() else None)

    base_config = EstimatorConfig(decoder_epoch_gap_days=45)
    all_sequences = [obs for reps in panel.values() for obs in reps.values()]
    emissions = fit_emissions_em(all_sequences, gap_days=base_config.decoder_epoch_gap_days)
    config = dataclasses.replace(base_config, emissions=emissions)

    _, fpd_h = run_estimator("fpd", panel, chip_of, strata, weights, config)
    _, sus_h = run_estimator("sustained", panel, chip_of, strata, weights, config)
    _, pava_h = run_estimator("pava", panel, chip_of, strata, weights, config)
    _, cp_h = run_estimator("changepoint", panel, chip_of, strata, weights, config)

    cp_unw = cp_h["overall_unweighted"]
    sus_unw = sus_h["overall_unweighted"]
    pava_unw = pava_h["overall_unweighted"]
    cp_inv = cp_h["overall_inventory_weighted"]
    sus_inv = sus_h["overall_inventory_weighted"]

    # G1
    assert cp_unw["map_mode_hit_all"] >= sus_unw["map_mode_hit_all"]
    # G2
    assert cp_unw["undated_flip"] <= sus_unw["undated_flip"]
    # G3 (strict -- beats the floor, doesn't just tie it)
    assert cp_unw["map_mode_hit_all"] > pava_unw["map_mode_hit_all"]
    # G4 (done_appears dominant stratum year-stability >= fpd's floor)
    fpd_da = fpd_h["by_stratum"]["done_appears"]["year_mode_hit"]
    cp_da = cp_h["by_stratum"]["done_appears"]["year_mode_hit"]
    assert cp_da >= fpd_da
    # G5 (inventory-weighted)
    assert cp_inv["map_mode_hit_all"] >= sus_inv["map_mode_hit_all"]
    assert cp_inv["year_mode_hit"] >= sus_inv["year_mode_hit"]
    # G6 (HPD calibration within [0.85, 0.95])
    hpd = cp_h["hpd_calibration"]["overall_contains_rate"]
    assert hpd is not None
    assert 0.85 <= hpd <= 0.95
