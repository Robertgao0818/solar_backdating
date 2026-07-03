"""Tests for the PAVA single-changepoint isotonic floor (pava.py)."""
from __future__ import annotations

from datetime import date

from solar_backdating.estimators import (
    ClampContext,
    EstimatorConfig,
    get_estimator,
)
from solar_backdating.estimators.seam import VintageObservation

PAVA = get_estimator("pava")


def _obs(y, m, d, pv, conf=None, row=None):
    return VintageObservation(
        capture_date=date(y, m, d),
        pv_present=pv,
        confidence=conf,
        source_row=row,
    )


def _yearly(pvs):
    """One frame per year starting 2016, so default gap makes each its own epoch."""
    return [_obs(2016 + i, 1, 1, pv, row=i) for i, pv in enumerate(pvs)]


def test_known_changepoint_recovered():
    obs = _yearly(["0", "0", "0", "1", "1", "1"])
    post = PAVA(obs, ClampContext(), EstimatorConfig())
    assert post.estimator == "pava"
    # changepoint between 3rd absent (2018) and 4th frame first present (2019)
    assert post.map_interval_start == date(2018, 1, 1)
    assert post.map_interval_end == date(2019, 1, 1)
    assert post.map_date == "2019-01-01"
    assert post.p_undated < 0.5


def test_all_absent_high_p_undated():
    obs = _yearly(["0", "0", "0", "0"])
    post = PAVA(obs, ClampContext(), EstimatorConfig())
    assert post.map_date == ""
    assert post.p_undated > 0.5
    # beyond-window cell is the max
    assert post.map_index == len(post.epochs) - 1
    assert post.p_undated == max(post.posterior)


def test_all_present_open_left():
    obs = _yearly(["1", "1", "1"])
    post = PAVA(obs, ClampContext(), EstimatorConfig())
    assert post.map_index == 0
    assert post.map_interval_start is None
    assert post.map_date == "2016-01-01"
    assert post.p_undated < 0.5


def test_duplicate_frame_invariance():
    # AC5: injecting an exact-duplicate present frame and a near-duplicate within
    # gap_days does not shift the changepoint decision. With the per-epoch
    # INDICATOR emission (each collapsed epoch = one unit of evidence, verdict by
    # majority) the whole posterior — not just the MAP — is invariant, because
    # the injected frames collapse into the blip epoch without changing its
    # (already present) verdict.
    obs = _yearly(["0", "0", "1", "1"])
    base = PAVA(obs, ClampContext(), EstimatorConfig())
    dup = list(obs)
    dup.insert(3, _obs(2018, 1, 1, "1", row=99))  # exact dup of index 2 date
    dup.insert(4, _obs(2018, 1, 10, "1", row=100))  # near-dup within 16 days
    other = PAVA(dup, ClampContext(), EstimatorConfig())
    assert other.map_date == base.map_date
    assert other.map_index == base.map_index
    assert other.map_interval_start == base.map_interval_start
    assert other.map_interval_end == base.map_interval_end
    assert other.posterior == base.posterior  # full-posterior invariance, not just MAP


def test_duplicate_frame_invariance_nonmonotone_blip():
    # Adversarial (review finding): a non-monotone blip 0,0,1,0,0 decodes UNDATED
    # (the isolated present frame is out-voted by the single-changepoint model).
    # Under a COUNT-weighted likelihood, stacking near-duplicate present frames
    # onto the blip date double-counts the correlated frames and flips the decode
    # to DATED. The indicator emission must keep it UNDATED and unchanged.
    obs = _yearly(["0", "0", "1", "0", "0"])
    base = PAVA(obs, ClampContext(), EstimatorConfig())
    assert base.map_date == ""  # base is undated
    assert base.p_undated == max(base.posterior)
    dup = list(obs)
    # three present frames within gap_days of the 2018 blip -> same epoch
    dup.append(_obs(2018, 1, 5, "1", row=100))
    dup.append(_obs(2018, 1, 10, "1", row=101))
    dup.append(_obs(2018, 1, 15, "1", row=102))
    other = PAVA(dup, ClampContext(), EstimatorConfig())
    assert other.map_date == base.map_date == ""
    assert other.map_index == base.map_index
    assert other.posterior == base.posterior


def test_abstain_excluded():
    obs = _yearly(["0", "0", "1", "1"])
    base = PAVA(obs, ClampContext(), EstimatorConfig())
    laced = list(obs)
    laced.insert(2, _obs(2017, 6, 1, "", row=50))  # abstain between epochs
    other = PAVA(laced, ClampContext(), EstimatorConfig())
    assert other.map_date == base.map_date

    all_abstain = _yearly(["", "", ""])
    post = PAVA(all_abstain, ClampContext(), EstimatorConfig())
    assert post.p_undated == 1.0
    assert post.map_date == ""


def test_determinism_shuffled():
    obs = _yearly(["0", "1", "0", "1", "1"])
    a = PAVA(obs, ClampContext(), EstimatorConfig())
    b = PAVA(obs, ClampContext(), EstimatorConfig())
    assert a == b
    # shuffled input with source_row preserved -> identical (estimator re-sorts)
    shuffled = [obs[3], obs[0], obs[4], obs[1], obs[2]]
    c = PAVA(shuffled, ClampContext(), EstimatorConfig())
    assert c == a


def test_clamp_earliest_present():
    obs = _yearly(["0", "0", "1", "1"])  # first present = 2018-01-01
    ceiling = date(2017, 6, 1)
    post = PAVA(obs, ClampContext(ceiling_date=ceiling), EstimatorConfig())
    assert post.map_date == "2017-06-01"
    assert post.map_interval_end == ceiling
    assert "clamped_earliest_present" in post.notes
    assert "2018-01-01" in post.notes


def test_posterior_sums_to_one_and_hpd_covers():
    obs = _yearly(["0", "0", "0", "1", "1"])
    post = PAVA(obs, ClampContext(), EstimatorConfig(credible_mass=0.9))
    assert abs(sum(post.posterior) - 1.0) < 1e-9
    assert post.credible_mass >= 0.9 - 1e-12


def test_empty_observations_undated():
    post = PAVA([], ClampContext(), EstimatorConfig())
    assert post.map_date == ""
    assert post.p_undated == 1.0
    assert abs(sum(post.posterior) - 1.0) < 1e-9
