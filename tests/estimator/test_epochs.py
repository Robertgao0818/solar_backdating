"""Tests for epoch collapsing (epochs.py)."""
from __future__ import annotations

from datetime import date

import pytest

from solar_backdating.estimators.epochs import Epoch, collapse_epochs
from solar_backdating.estimators.seam import VintageObservation


def _obs(y, m, d, pv, conf=None, row=None):
    return VintageObservation(
        capture_date=date(y, m, d),
        pv_present=pv,
        confidence=conf,
        source_row=row,
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
