"""Tests for the EmissionModel + EM fit (emissions.py)."""
from __future__ import annotations

from datetime import date

from solar_backdating.estimators.emissions import (
    SYMBOL_INDEX,
    SYMBOLS,
    EmissionModel,
    fit_emissions_em,
)
from solar_backdating.estimators.seam import VintageObservation


def _obs(y, m, d, pv, row=None, quality_flag="usable"):
    return VintageObservation(
        capture_date=date(y, m, d), pv_present=pv, source_row=row, quality_flag=quality_flag
    )


def _yearly(pvs, start=2013, quality_flag="usable"):
    return [
        _obs(start + i, 1, 1, pv, row=i, quality_flag=quality_flag) for i, pv in enumerate(pvs)
    ]


def test_lookup_exact_and_default_fallback():
    model = EmissionModel(
        strata=("usable", "default"),
        matrices=(
            ((0.9, 0.05, 0.05), (0.05, 0.9, 0.05)),
            ((0.8, 0.1, 0.1), (0.1, 0.8, 0.1)),
        ),
    )
    assert model.lookup("usable") == ((0.9, 0.05, 0.05), (0.05, 0.9, 0.05))
    # unknown stratum -> falls back to "default"
    assert model.lookup("unusable") == ((0.8, 0.1, 0.1), (0.1, 0.8, 0.1))


def test_lookup_no_default_raises_keyerror():
    model = EmissionModel(strata=("usable",), matrices=(((0.9, 0.05, 0.05), (0.05, 0.9, 0.05)),))
    import pytest

    with pytest.raises(KeyError):
        model.lookup("unusable")


def test_to_json_from_json_round_trip():
    model = EmissionModel(
        strata=("usable", "default"),
        matrices=(
            ((0.9, 0.05, 0.05), (0.05, 0.9, 0.05)),
            ((0.8, 0.1, 0.1), (0.1, 0.8, 0.1)),
        ),
        counts=(
            ((90.0, 5.0, 5.0), (5.0, 90.0, 5.0)),
            ((80.0, 10.0, 10.0), (10.0, 80.0, 10.0)),
        ),
    )
    d = model.to_json()
    assert d["symbols"] == list(SYMBOLS)
    restored = EmissionModel.from_json(d)
    assert restored == model


def test_fit_deterministic_repeat():
    seqs = [_yearly(["0", "0", "1", "1", "1"]), _yearly(["0", "1", "1"])]
    a = fit_emissions_em(seqs, gap_days=30)
    b = fit_emissions_em(seqs, gap_days=30)
    assert a == b


def test_fit_smoothing_keeps_rows_nonzero():
    # A degenerate cohort (all-present, no absent evidence at all) would zero out
    # half the counts without add-k smoothing.
    seqs = [_yearly(["1", "1", "1"])]
    model = fit_emissions_em(seqs, gap_days=30)
    for mat in model.matrices:
        for row in mat:
            assert all(p > 0.0 for p in row)
            assert abs(sum(row) - 1.0) < 1e-9


def test_fit_recovers_planted_flip_rate():
    """Cohort deterministically constructed (no RNG) from a known changepoint
    tau=3 over T=10 epochs: 10 clean sequences (zero errors) anchor the true
    tau confidently, plus 7 sequences each with exactly one INTERIOR flip (kept
    away from tau's boundary so no alternative single-changepoint tau can
    "explain away" the error with zero cost) -> planted flip rate = 7/170 ~=
    0.0412 in both directions (P(present|absent_state) and P(absent|present_state)).
    """
    t = 10
    tau_true = 3
    sequences = []
    for _ in range(10):
        pvs = ["1" if i >= tau_true else "0" for i in range(t)]
        sequences.append(_yearly(pvs))
    flip_positions = [0, 1, 5, 6, 7, 8, 9]  # avoid 2,3,4 (adjacent to the boundary)
    for pos in flip_positions:
        true_present = pos >= tau_true
        pvs = ["1" if i >= tau_true else "0" for i in range(t)]
        pvs[pos] = "0" if true_present else "1"
        sequences.append(_yearly(pvs))

    planted_flip_rate = len(flip_positions) / (len(sequences) * t)
    model = fit_emissions_em(sequences, gap_days=30)

    mat = model.lookup("usable")
    absent_state_row, present_state_row = mat
    recovered_flip_absent_state = absent_state_row[1]  # P(present | absent_state)
    recovered_flip_present_state = present_state_row[0]  # P(absent | present_state)

    assert abs(recovered_flip_absent_state - planted_flip_rate) < 0.03
    assert abs(recovered_flip_present_state - planted_flip_rate) < 0.03
    # repeated call -> byte-identical (determinism)
    assert fit_emissions_em(sequences, gap_days=30) == model


def test_fit_ignores_non_usable_scored_verdicts_via_abstain_symbol():
    """Orchestrator design correction (2026-07-03): a sequence where every
    verdict is 'ambiguous'-flagged carries real '0'/'1' pv_present values, but
    epochs.epoch_symbol()'s usable-only vote maps every one of its epochs to
    the ABSTAIN symbol (no usable-scored evidence at all). The EM fit must
    therefore land almost all of the 'ambiguous' stratum's expected mass in
    the abstain column, not the present/absent columns -- non-usable scored
    verdicts are censoring-with-cause (ISSUE-04 doctrine), never dispositive
    evidence, even during EM fitting."""
    seqs_ambiguous = [_yearly(["0", "1", "1"], quality_flag="ambiguous")]
    model = fit_emissions_em(seqs_ambiguous, gap_days=30)
    mat = model.lookup("ambiguous")
    for row in mat:
        assert row[SYMBOL_INDEX["abstain"]] > 0.99
        assert row[SYMBOL_INDEX["absent"]] < 0.01
        assert row[SYMBOL_INDEX["present"]] < 0.01
        assert abs(sum(row) - 1.0) < 1e-9


def test_fit_stratifies_by_quality_flag_plus_pooled_default():
    seqs_usable = [_yearly(["0", "0", "1", "1"], quality_flag="usable")]
    seqs_unusable = [_yearly(["0", "0", "1", "1"], quality_flag="unusable")]
    model = fit_emissions_em(seqs_usable + seqs_unusable, gap_days=30)
    assert "usable" in model.strata
    assert "unusable" in model.strata
    assert "default" in model.strata
    # pooled "default" counts = sum of the two real strata's counts (both
    # strata AND the pooled default are updated from every epoch).
    idx_usable = model.strata.index("usable")
    idx_unusable = model.strata.index("unusable")
    idx_default = model.strata.index("default")
    for state in range(2):
        for sym in range(3):
            pooled = model.counts[idx_default][state][sym]
            parts = model.counts[idx_usable][state][sym] + model.counts[idx_unusable][state][sym]
            assert abs(pooled - parts) < 1e-6
