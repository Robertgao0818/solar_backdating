"""Tests for fractional posterior-mass aggregation (aggregate.py, ISSUE-03 UNIT C).

Deterministic, no RNG, no banked-data dependency (hand-built posteriors + a
directly-constructed ``TurnbullFit``).
"""
from __future__ import annotations

from collections import Counter
from datetime import date, timedelta

from solar_backdating.estimators.seam import EpochCell, InstallDatePosterior
from solar_backdating.estimators.survival import TurnbullFit, year_day_fractions
from solar_backdating.eval.aggregate import (
    BEYOND,
    cohort_year_histogram,
    cohort_year_histogram_from_fit,
    midpoint_vs_posterior_report,
    midpoint_year,
    point_year,
    posterior_year_mass,
    survival_curve_from_histogram,
    survival_tvd,
)
from solar_backdating.eval.metrics import tvd


# --------------------------------------------------------------------------- #
# Inline posterior factory (no conftest, matches suite style)
# --------------------------------------------------------------------------- #
def _cell(idx, start, end, beyond=False):
    return EpochCell(index=idx, start_date=start, end_date=end, is_beyond_window=beyond)


def _mk_post(cells, masses, *, map_date="", map_start=None, map_end=None, map_index=0):
    beyond = cells[-1].is_beyond_window
    return InstallDatePosterior(
        estimator="test",
        epochs=tuple(cells),
        posterior=tuple(masses),
        map_index=map_index,
        map_interval_start=map_start,
        map_interval_end=map_end,
        p_undated=(masses[-1] if beyond else 0.0),
        credible_low_date=None,
        credible_high_date=None,
        credible_mass=0.9,
        map_date=map_date,
    )


def _undated_post():
    """All mass on a beyond-window cell (undated)."""
    return _mk_post(
        [_cell(0, date(2018, 1, 1), None, beyond=True)],
        [1.0],
        map_date="",
    )


# --------------------------------------------------------------------------- #
# 1. posterior_year_mass sums to 1; single-cell one-year -> all mass that year
# --------------------------------------------------------------------------- #
def test_posterior_year_mass_single_year_sums_to_one():
    post = _mk_post(
        [_cell(0, date(2018, 1, 1), date(2019, 1, 1))],
        [1.0],
        map_date="2018-06-01",
    )
    m = posterior_year_mass(post)
    assert abs(sum(m.values()) - 1.0) < 1e-12
    assert abs(m[2018] - 1.0) < 1e-12
    assert BEYOND not in m


# --------------------------------------------------------------------------- #
# 2. Posterior spanning multiple years -> mass split by day-fraction
# --------------------------------------------------------------------------- #
def test_posterior_year_mass_multi_year_matches_day_fractions():
    start, end = date(2017, 1, 1), date(2020, 1, 1)
    post = _mk_post([_cell(0, start, end)], [1.0], map_date="2018-06-01")
    m = posterior_year_mass(post)
    fr = year_day_fractions(start, end)
    assert set(m) == set(fr)
    for y in fr:
        assert abs(m[y] - fr[y]) < 1e-12
    assert abs(sum(m.values()) - 1.0) < 1e-12


def test_posterior_year_mass_two_years_boundary_split():
    # A cell straddling a mid-year boundary spreads mass by day fraction.
    start, end = date(2018, 7, 1), date(2019, 7, 1)
    post = _mk_post([_cell(0, start, end)], [1.0], map_date="2018-09-01")
    m = posterior_year_mass(post)
    fr = year_day_fractions(start, end)
    assert set(m) == {2018, 2019}
    assert abs(m[2018] - fr[2018]) < 1e-12
    assert abs(m[2019] - fr[2019]) < 1e-12


def test_posterior_year_mass_open_left_scored_by_end_year():
    # Open-left cell (start None) is scored entirely by its end_date year,
    # mirroring changepoint._cell_log_prior.
    post = _mk_post(
        [_cell(0, None, date(2017, 5, 1)), _cell(1, date(2017, 5, 1), date(2018, 5, 1))],
        [0.7, 0.3],
        map_date="2017-05-01",
    )
    m = posterior_year_mass(post)
    assert abs(m[2017] - (0.7 + year_day_fractions(date(2017, 5, 1), date(2018, 5, 1))[2017] * 0.3)) < 1e-12
    assert abs(sum(m.values()) - 1.0) < 1e-12


# --------------------------------------------------------------------------- #
# 3. Fully-undated posterior -> all mass on BEYOND
# --------------------------------------------------------------------------- #
def test_posterior_year_mass_all_beyond():
    m = posterior_year_mass(_undated_post())
    assert abs(m[BEYOND] - 1.0) < 1e-12
    assert set(m) == {BEYOND}


# --------------------------------------------------------------------------- #
# 4. point_year / midpoint_year
# --------------------------------------------------------------------------- #
def test_point_year_and_midpoint_year():
    post = _mk_post(
        [_cell(0, date(2018, 1, 1), date(2019, 1, 1))],
        [1.0],
        map_date="2018-06-01",
        map_start=date(2018, 1, 1),
        map_end=date(2019, 1, 1),
    )
    assert point_year(post) == 2018
    # midpoint of (2018-01-01, 2019-01-01] = 2018-01-01 + 182d -> 2018
    mid = date(2018, 1, 1) + timedelta(days=(date(2019, 1, 1) - date(2018, 1, 1)).days // 2)
    assert midpoint_year(post) == mid.year

    undated = _undated_post()
    assert point_year(undated) == BEYOND
    assert midpoint_year(undated) == BEYOND


def test_midpoint_year_crosses_boundary():
    # Interval spanning a year boundary whose midpoint lands in the later year.
    post = _mk_post(
        [_cell(0, date(2018, 10, 1), date(2019, 6, 1))],
        [1.0],
        map_date="2018-12-01",
        map_start=date(2018, 10, 1),
        map_end=date(2019, 6, 1),
    )
    mid = date(2018, 10, 1) + timedelta(days=(date(2019, 6, 1) - date(2018, 10, 1)).days // 2)
    assert midpoint_year(post) == mid.year == 2019
    # point channel disagrees (map_date is in 2018) -> demonstrates channel split
    assert point_year(post) == 2018


# --------------------------------------------------------------------------- #
# 5. cohort_year_histogram(mode="point") reproduces a hand-built Counter
# --------------------------------------------------------------------------- #
def test_cohort_year_histogram_point_mode():
    p18 = _mk_post([_cell(0, date(2018, 1, 1), date(2019, 1, 1))], [1.0], map_date="2018-06-01")
    p19 = _mk_post([_cell(0, date(2019, 1, 1), date(2020, 1, 1))], [1.0], map_date="2019-06-01")
    und = _undated_post()
    hist = cohort_year_histogram([(p18, 1.0), (p19, 2.0), (und, 1.0)], mode="point")
    assert hist == Counter({2018: 1.0, 2019: 2.0, BEYOND: 1.0})


# --------------------------------------------------------------------------- #
# 6. AC5 mechanism proof: mass channel is stable where MAP flips
# --------------------------------------------------------------------------- #
def test_ac5_mass_channel_beats_point_channel():
    cells = [
        _cell(0, date(2018, 1, 1), date(2019, 1, 1)),
        _cell(1, date(2019, 1, 1), date(2020, 1, 1)),
    ]
    # Two "reps" of the same anchor: nearly identical mass, MAP flips year.
    rep_a = _mk_post(cells, [0.5, 0.5], map_date="2018-06-01", map_index=0)
    rep_b = _mk_post(cells, [0.5, 0.5], map_date="2019-06-01", map_index=1)

    point_a = cohort_year_histogram([(rep_a, 1.0)], mode="point")
    point_b = cohort_year_histogram([(rep_b, 1.0)], mode="point")
    post_a = cohort_year_histogram([(rep_a, 1.0)], mode="posterior")
    post_b = cohort_year_histogram([(rep_b, 1.0)], mode="posterior")

    tvd_point = survival_tvd(point_a, point_b)
    tvd_post = survival_tvd(post_a, post_b)
    assert tvd_point > 0.5  # point date fully flips
    assert tvd_post < 1e-12  # mass identical
    assert tvd_post < tvd_point


# --------------------------------------------------------------------------- #
# 7. survival_curve_from_histogram monotone non-increasing, in [0, 1]
# --------------------------------------------------------------------------- #
def test_survival_curve_from_histogram_monotone():
    hist = Counter({2016: 1.0, 2017: 2.0, 2018: 1.0, BEYOND: 0.5})
    curve = survival_curve_from_histogram(hist)
    years = [y for y, _ in curve]
    vals = [s for _, s in curve]
    assert years == sorted(years)
    assert all(0.0 <= s <= 1.0 for s in vals)
    for a, b in zip(vals, vals[1:]):
        assert b <= a + 1e-12
    # Beyond mass stays as the tail residual after the last finite year.
    total = sum(hist.values())
    assert abs(vals[-1] - hist[BEYOND] / total) < 1e-12


def test_survival_curve_empty_histogram():
    assert survival_curve_from_histogram(Counter()) == ()


# --------------------------------------------------------------------------- #
# 8. survival_tvd symmetric, zero on identical, == metrics.tvd
# --------------------------------------------------------------------------- #
def test_survival_tvd_symmetric_and_zero_on_identical():
    a = Counter({2018: 3.0, 2019: 1.0})
    b = Counter({2018: 1.0, 2019: 3.0})
    assert abs(survival_tvd(a, a)) < 1e-12
    assert abs(survival_tvd(a, b) - survival_tvd(b, a)) < 1e-12
    assert abs(survival_tvd(a, b) - tvd(a, b)) < 1e-12


# --------------------------------------------------------------------------- #
# 9. cohort_year_histogram_from_fit matches summed posterior_year_mass
# --------------------------------------------------------------------------- #
def test_cohort_histogram_from_fit_matches_posterior_sum():
    fit = TurnbullFit(
        year_mass=((2018, 0.6), (2019, 0.4)),
        beyond_mass=0.0,
        n_observations=2,
        n_iterations=1,
        converged=True,
        final_loglik=0.0,
    )
    from_fit = cohort_year_histogram_from_fit(fit)
    p18 = _mk_post([_cell(0, date(2018, 1, 1), date(2019, 1, 1))], [1.0], map_date="2018-06-01")
    p19 = _mk_post([_cell(0, date(2019, 1, 1), date(2020, 1, 1))], [1.0], map_date="2019-06-01")
    summed = cohort_year_histogram([(p18, 0.6), (p19, 0.4)], mode="posterior")
    assert set(from_fit) == set(summed)
    for k in from_fit:
        assert abs(from_fit[k] - summed[k]) < 1e-12


def test_cohort_histogram_from_fit_includes_beyond():
    fit = TurnbullFit(
        year_mass=((2018, 0.7),),
        beyond_mass=0.3,
        n_observations=1,
        n_iterations=1,
        converged=True,
        final_loglik=0.0,
    )
    hist = cohort_year_histogram_from_fit(fit)
    assert abs(hist[2018] - 0.7) < 1e-12
    assert abs(hist[BEYOND] - 0.3) < 1e-12


# --------------------------------------------------------------------------- #
# 10. midpoint_vs_posterior_report: per_year_delta mass-conserving, counts
# --------------------------------------------------------------------------- #
def test_midpoint_vs_posterior_report():
    dated = _mk_post(
        [_cell(0, date(2018, 1, 1), date(2019, 1, 1)), _cell(1, date(2019, 1, 1), date(2020, 1, 1))],
        [0.5, 0.5],
        map_date="2018-06-01",
        map_start=date(2018, 1, 1),
        map_end=date(2019, 1, 1),
    )
    und = _undated_post()
    rep = midpoint_vs_posterior_report([(dated, 1.0), (und, 1.0)])

    # Mass-conserving: midpoint and posterior channels have equal total mass.
    assert abs(sum(rep["per_year_delta"].values())) < 1e-9
    # The two channels genuinely differ (midpoint concentrates, posterior spreads).
    assert rep["l1_shift"] > 1e-9
    assert rep["n_dated"] == 1
    assert rep["n_undated"] == 1
    # All three channels present and each conserves total cohort mass (= 2.0).
    for key in ("point_hist", "midpoint_hist", "posterior_hist"):
        assert abs(sum(rep[key].values()) - 2.0) < 1e-9


def test_midpoint_and_posterior_differ_on_constructed_example():
    # Constructed case where midpoint-year counting and mass counting diverge.
    post = _mk_post(
        [_cell(0, date(2017, 1, 1), date(2020, 1, 1))],
        [1.0],
        map_date="2018-06-01",
        map_start=date(2017, 1, 1),
        map_end=date(2020, 1, 1),
    )
    mid_hist = cohort_year_histogram([(post, 1.0)], mode="midpoint")
    mass_hist = cohort_year_histogram([(post, 1.0)], mode="posterior")
    # Midpoint dumps the whole unit into one year; mass spreads over three.
    assert len(mid_hist) == 1
    assert len(mass_hist) == 3
    assert survival_tvd(mid_hist, mass_hist) > 0.0
