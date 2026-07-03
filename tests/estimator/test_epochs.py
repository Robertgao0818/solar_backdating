"""Tests for epoch collapsing (epochs.py)."""
from __future__ import annotations

from datetime import date

import pytest

from solar_backdating.estimators.epochs import Epoch, collapse_epochs, epoch_symbol
from solar_backdating.estimators.seam import VintageObservation


def _obs(y, m, d, pv, conf=None, row=None, quality_flag="usable"):
    return VintageObservation(
        capture_date=date(y, m, d),
        pv_present=pv,
        confidence=conf,
        source_row=row,
        quality_flag=quality_flag,
    )


def test_gap_zero_one_epoch_per_distinct_date():
    obs = [
        _obs(2019, 1, 1, "0", row=0),
        _obs(2019, 6, 1, "0", row=1),
        _obs(2020, 1, 1, "1", row=2),
    ]
    epochs = collapse_epochs(obs, gap_days=0)
    assert len(epochs) == 3
    assert all(isinstance(e, Epoch) for e in epochs)
    assert [e.n_present for e in epochs] == [0, 0, 1]


def test_gap_zero_exact_duplicate_merges():
    obs = [
        _obs(2019, 1, 1, "0", row=0),
        _obs(2019, 1, 1, "1", row=1),  # exact-duplicate date
    ]
    epochs = collapse_epochs(obs, gap_days=0)
    assert len(epochs) == 1
    assert epochs[0].n_present == 1
    assert epochs[0].n_absent == 1


def test_near_duplicate_within_gap_merges():
    obs = [
        _obs(2019, 1, 1, "0", row=0),
        _obs(2019, 1, 10, "0", row=1),  # 9 days -> within gap 16
        _obs(2019, 3, 1, "1", row=2),  # far -> new epoch
    ]
    epochs = collapse_epochs(obs, gap_days=16)
    assert len(epochs) == 2
    assert epochs[0].n_absent == 2
    assert epochs[0].start_date == date(2019, 1, 1)
    assert epochs[0].end_date == date(2019, 1, 10)
    assert epochs[1].n_present == 1


def test_verdict_aggregation_mixed():
    obs = [
        _obs(2019, 1, 1, "1", conf=0.9, row=0),
        _obs(2019, 1, 5, "0", conf=0.8, row=1),
        _obs(2019, 1, 8, "", row=2),  # abstain, no confidence
    ]
    epochs = collapse_epochs(obs, gap_days=16)
    assert len(epochs) == 1
    e = epochs[0]
    assert e.n_present == 1
    assert e.n_absent == 1
    assert e.n_abstain == 1
    assert e.mean_confidence == pytest.approx(0.85)


def test_empty_returns_empty():
    assert collapse_epochs([], gap_days=16) == []


# --------------------------------------------------------------------------- #
# Usable-only evidence gate (orchestrator design correction, 2026-07-03):
# n_present_usable/n_absent_usable + epoch_symbol()'s usable-only majority vote.
# --------------------------------------------------------------------------- #
def test_usable_counts_equal_raw_counts_when_all_members_usable():
    obs = [
        _obs(2019, 1, 1, "1", row=0, quality_flag="usable"),
        _obs(2019, 1, 5, "0", row=1, quality_flag="usable"),
    ]
    epochs = collapse_epochs(obs, gap_days=16)
    e = epochs[0]
    assert e.n_present == e.n_present_usable == 1
    assert e.n_absent == e.n_absent_usable == 1


def test_ambiguous_scored_members_excluded_from_usable_counts():
    """Raw n_present/n_absent count every scored member (PAVA's unit); the new
    usable-only counts drop the ambiguous/unusable-flagged ones."""
    obs = [
        _obs(2019, 1, 1, "1", row=0, quality_flag="ambiguous"),
        _obs(2019, 1, 3, "1", row=1, quality_flag="unusable"),
        _obs(2019, 1, 5, "0", row=2, quality_flag="usable"),
    ]
    epochs = collapse_epochs(obs, gap_days=16)
    e = epochs[0]
    assert (e.n_present, e.n_absent) == (2, 1)  # raw: PAVA's view, untouched
    assert (e.n_present_usable, e.n_absent_usable) == (0, 1)  # usable-only: decoder's view


def test_epoch_symbol_usable_majority_present_and_absent():
    e_present = Epoch(
        date(2019, 1, 1), date(2019, 1, 1), n_present=1, n_absent=0, n_abstain=0,
        mean_confidence=None, n_present_usable=1, n_absent_usable=0,
    )
    e_absent = Epoch(
        date(2019, 1, 1), date(2019, 1, 1), n_present=0, n_absent=1, n_abstain=0,
        mean_confidence=None, n_present_usable=0, n_absent_usable=1,
    )
    assert epoch_symbol(e_present) == "present"
    assert epoch_symbol(e_absent) == "absent"


def test_epoch_symbol_usable_tie_is_neutral():
    e = Epoch(
        date(2019, 1, 1), date(2019, 1, 1), n_present=1, n_absent=1, n_abstain=0,
        mean_confidence=None, n_present_usable=1, n_absent_usable=1,
    )
    assert epoch_symbol(e) == "neutral"


def test_epoch_symbol_zero_scored_members_is_abstain():
    e = Epoch(
        date(2019, 1, 1), date(2019, 1, 1), n_present=0, n_absent=0, n_abstain=3,
        mean_confidence=None, n_present_usable=0, n_absent_usable=0,
    )
    assert epoch_symbol(e) == "abstain"


def test_epoch_symbol_ambiguous_only_epoch_is_abstain_not_the_raw_majority():
    """Two ambiguous '1' verdicts and no usable members at all: the RAW majority
    is 'present' (2-0), but with zero usable-scored evidence the usable-only
    vote is abstain -- ambiguous verdicts are censoring-with-cause, not
    dispositive evidence, mirroring production's usable_observations/_usable
    filters (scan_decision.py / infer_install_dates.py)."""
    obs = [
        _obs(2019, 1, 1, "1", row=0, quality_flag="ambiguous"),
        _obs(2019, 1, 3, "1", row=1, quality_flag="ambiguous"),
    ]
    epochs = collapse_epochs(obs, gap_days=16)
    e = epochs[0]
    assert e.n_present == 2 and e.n_absent == 0  # raw majority would be "present"
    assert epoch_symbol(e) == "abstain"  # usable-only vote: no usable evidence at all


def test_epoch_symbol_mixed_epoch_usable_vote_overrides_ambiguous_majority():
    """Two ambiguous '1' verdicts outnumber one usable '0' verdict 2-to-1 in the
    RAW count (old, pre-correction majority = 'present'); the usable-only vote
    is driven purely by the single usable '0' -> 'absent'. This is the concrete
    scenario the correction targets: ambiguous-flagged noise must not be able
    to outvote a usable observation."""
    obs = [
        _obs(2019, 1, 1, "1", row=0, quality_flag="ambiguous"),
        _obs(2019, 1, 3, "1", row=1, quality_flag="ambiguous"),
        _obs(2019, 1, 5, "0", row=2, quality_flag="usable"),
    ]
    epochs = collapse_epochs(obs, gap_days=16)
    e = epochs[0]
    assert e.n_present > e.n_absent  # raw majority: 'present' (2 vs 1)
    assert epoch_symbol(e) == "absent"  # usable-only vote: 'absent' (0 vs 1)
