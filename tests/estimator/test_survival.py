"""Tests for the Turnbull survival core (survival.py, ISSUE-03 UNIT A).

Deterministic, no RNG, no banked-data dependency (pure synthetic cohorts).
"""
from __future__ import annotations

import math
from datetime import date

from solar_backdating.estimators import ClampContext, EstimatorConfig, get_estimator
from solar_backdating.estimators.changepoint import _year_day_fractions
from solar_backdating.estimators.survival import (
    _BEYOND,
    CensoringInterval,
    _interval_year_weights,
    cohort_prior_from_json,
    cohort_prior_to_json,
    fit_turnbull,
    survival_curve,
    to_cohort_prior,
    year_day_fractions,
)

CP = get_estimator("changepoint")


def _iv(lower, upper, kind, **kw):
    return CensoringInterval(lower=lower, upper=upper, kind=kind, **kw)


def _interval(y0, m0, d0, y1, m1, d1, **kw):
    return _iv(date(y0, m0, d0), date(y1, m1, d1), "interval", **kw)


def _left(y, m, d, **kw):
    return _iv(None, date(y, m, d), "left", **kw)


def _right(y, m, d, **kw):
    return _iv(date(y, m, d), None, "right", **kw)


# --------------------------------------------------------------------------- #
# 1. year_day_fractions drift guard
# --------------------------------------------------------------------------- #
def test_year_day_fractions_matches_changepoint():
    start, end = date(2017, 3, 1), date(2019, 9, 1)
    assert year_day_fractions(start, end) == _year_day_fractions(start, end)
    # degenerate span inherits the {end.year: 1.0} rule
    assert year_day_fractions(date(2020, 1, 1), date(2020, 1, 1)) == {2020: 1.0}


# --------------------------------------------------------------------------- #
# 2. single tight one-year interval
# --------------------------------------------------------------------------- #
def test_single_tight_interval_concentrates():
    fit = fit_turnbull([_interval(2018, 6, 1, 2018, 9, 1)])
    assert fit.converged
    mass = dict(fit.year_mass)
    assert math.isclose(mass[2018], 1.0, abs_tol=1e-9)
    assert math.isclose(fit.beyond_mass, 0.0, abs_tol=1e-12)
    assert fit.n_observations == 1


# --------------------------------------------------------------------------- #
# 3. two disjoint tight interval groups -> two-mode proportions
# --------------------------------------------------------------------------- #
def test_two_disjoint_groups_proportions():
    intervals = [
        _interval(2016, 3, 1, 2016, 6, 1),
        _interval(2016, 4, 1, 2016, 7, 1),
        _interval(2016, 5, 1, 2016, 8, 1),
        _interval(2020, 3, 1, 2020, 6, 1),
    ]
    fit = fit_turnbull(intervals)
    mass = dict(fit.year_mass)
    assert math.isclose(mass[2016], 0.75, abs_tol=1e-6)
    assert math.isclose(mass[2020], 0.25, abs_tol=1e-6)
    # the gap years exist (dense grid) but carry zero mass
    assert math.isclose(mass.get(2018, 0.0), 0.0, abs_tol=1e-9)
    assert math.isclose(sum(mass.values()) + fit.beyond_mass, 1.0, abs_tol=1e-9)


# --------------------------------------------------------------------------- #
# 4. left-censored weights spread over years <= year(R) only
# --------------------------------------------------------------------------- #
def test_left_censored_weights():
    years = [2016, 2017, 2018, 2019, 2020]
    yw, bw = _interval_year_weights(_left(2019, 7, 1), years)
    assert yw[2016] == 1.0 and yw[2017] == 1.0 and yw[2018] == 1.0
    frac = _year_day_fractions(date(2019, 1, 1), date(2019, 7, 1))[2019]
    assert math.isclose(yw[2019], frac, abs_tol=1e-12)
    assert 2020 not in yw or yw[2020] == 0.0
    assert bw == 0.0


# --------------------------------------------------------------------------- #
# 5. right-censored weights deposit on BEYOND + years after L
# --------------------------------------------------------------------------- #
def test_right_censored_weights():
    years = [2016, 2017, 2018, 2019, 2020]
    yw, bw = _interval_year_weights(_right(2018, 6, 1), years)
    frac = _year_day_fractions(date(2018, 6, 1), date(2019, 1, 1))[2018]
    assert math.isclose(yw[2018], frac, abs_tol=1e-12)
    assert yw[2019] == 1.0 and yw[2020] == 1.0
    assert 2016 not in yw and 2017 not in yw
    assert bw == 1.0


# --------------------------------------------------------------------------- #
# 6. mixed cohort: normalization + monotone log-lik
# --------------------------------------------------------------------------- #
def test_mixed_cohort_normalizes_and_loglik_monotone():
    intervals = [
        _interval(2017, 2, 1, 2017, 8, 1),
        _left(2018, 6, 1),
        _right(2019, 3, 1),
        _interval(2020, 1, 1, 2020, 6, 1),
    ]
    fit = fit_turnbull(intervals)
    total = sum(p for _, p in fit.year_mass) + fit.beyond_mass
    assert math.isclose(total, 1.0, abs_tol=1e-9)
    assert fit.beyond_mass > 0.0  # a right-censored obst feeds BEYOND
    # log-lik non-decreasing across iterations (checked via a per-iter trace)
    trace = _loglik_trace(intervals, n_steps=15)
    for a, b in zip(trace, trace[1:]):
        assert b >= a - 1e-9


# --------------------------------------------------------------------------- #
# 7. determinism: input order does not change the fit
# --------------------------------------------------------------------------- #
def test_determinism_input_order():
    intervals = [
        _interval(2016, 3, 1, 2016, 9, 1, anchor_id="a"),
        _left(2019, 6, 1, anchor_id="b"),
        _right(2018, 2, 1, anchor_id="c"),
        _interval(2020, 1, 1, 2020, 7, 1, anchor_id="d"),
    ]
    fit_a = fit_turnbull(intervals)
    fit_b = fit_turnbull(list(reversed(intervals)))
    fit_c = fit_turnbull([intervals[2], intervals[0], intervals[3], intervals[1]])
    assert fit_a.year_mass == fit_b.year_mass == fit_c.year_mass
    assert fit_a.beyond_mass == fit_b.beyond_mass == fit_c.beyond_mass
    assert fit_a.final_loglik == fit_b.final_loglik == fit_c.final_loglik


# --------------------------------------------------------------------------- #
# 8. CohortPrior JSON round-trip
# --------------------------------------------------------------------------- #
def test_cohort_prior_json_roundtrip():
    fit = fit_turnbull(
        [_interval(2017, 1, 1, 2017, 6, 1), _interval(2019, 1, 1, 2019, 6, 1)]
    )
    cp = to_cohort_prior(fit)
    d = cohort_prior_to_json(cp)
    cp2 = cohort_prior_from_json(d)
    assert cp2.year_log_mass == cp.year_log_mass
    assert cp2.beyond_log_mass == cp.beyond_log_mass
    assert isinstance(d["year_log_mass"], list)
    assert isinstance(d["year_log_mass"][0][0], int)


# --------------------------------------------------------------------------- #
# 9. exported prior moves the changepoint MAP earlier (decoder integration)
# --------------------------------------------------------------------------- #
def test_exported_prior_moves_changepoint_map():
    obs = [
        _obs(2016, "0"),
        _obs(2017, "0"),
        _obs(2018, "1"),
        _obs(2019, "1"),
    ]
    base = CP(obs, ClampContext(), EstimatorConfig())
    assert base.map_date == "2018-01-01"

    fit = fit_turnbull([_interval(2016, 3, 1, 2016, 6, 1)])  # concentrated on 2016
    cp = to_cohort_prior(fit)
    shifted = CP(obs, ClampContext(), EstimatorConfig(cohort_prior=cp))
    assert shifted.map_index < base.map_index
    assert shifted.map_date < base.map_date


# --------------------------------------------------------------------------- #
# 10. Cromwell floor: gap years finite; out-of-span years absent
# --------------------------------------------------------------------------- #
def test_cromwell_floor_gap_years_finite():
    fit = fit_turnbull(
        [_interval(2016, 1, 1, 2016, 6, 1), _interval(2020, 1, 1, 2020, 6, 1)]
    )
    cp = to_cohort_prior(fit, smooth_lambda=0.05)
    ylm = dict(cp.year_log_mass)
    assert set(ylm) == {2016, 2017, 2018, 2019, 2020}
    for y in (2017, 2018, 2019):  # gap years inside the span
        assert math.isfinite(ylm[y])
        assert ylm[y] > -700.0
    assert 2015 not in ylm and 2021 not in ylm  # outside span


# --------------------------------------------------------------------------- #
# 11. beyond_policy ordering
# --------------------------------------------------------------------------- #
def test_beyond_policy_ordering():
    fit = fit_turnbull(
        [_interval(2017, 1, 1, 2017, 6, 1), _interval(2019, 1, 1, 2019, 6, 1)]
    )
    cp_term = to_cohort_prior(fit, beyond_policy="terminal_year")
    cp_ml2 = to_cohort_prior(fit, beyond_policy="terminal_minus_log2")
    cp_mean = to_cohort_prior(fit, beyond_policy="cohort_mean")
    assert cp_ml2.beyond_log_mass < cp_term.beyond_log_mass
    assert math.isclose(
        cp_ml2.beyond_log_mass, cp_term.beyond_log_mass - math.log(2.0), abs_tol=1e-12
    )
    assert math.isfinite(cp_mean.beyond_log_mass)


# --------------------------------------------------------------------------- #
# 12. survival_curve well-formed; degenerate cohorts defined
# --------------------------------------------------------------------------- #
def test_survival_curve_and_degenerate_cohorts():
    fit = fit_turnbull(
        [_interval(2017, 1, 1, 2017, 6, 1), _right(2019, 3, 1)]
    )
    curve = survival_curve(fit)
    vals = [s for _, s in curve]
    for a, b in zip(vals, vals[1:]):
        assert b <= a + 1e-12  # non-increasing
    for _, s in curve:
        assert -1e-12 <= s <= 1.0 + 1e-12
        assert not math.isnan(s)

    # empty cohort -> defined, converged, no NaN
    empty = fit_turnbull([])
    assert empty.converged
    assert empty.year_mass == ()
    assert empty.beyond_mass == 0.0
    assert survival_curve(empty) == ()

    # all-left-censored cohort -> defined, converged
    all_left = fit_turnbull([_left(2018, 6, 1), _left(2020, 6, 1)])
    assert all_left.converged
    total = sum(p for _, p in all_left.year_mass) + all_left.beyond_mass
    assert math.isclose(total, 1.0, abs_tol=1e-9)
    for _, p in all_left.year_mass:
        assert not math.isnan(p)


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _obs(year, pv):
    from solar_backdating.estimators.seam import VintageObservation

    return VintageObservation(capture_date=date(year, 1, 1), pv_present=pv)


def _loglik_trace(intervals, *, n_steps):
    """Re-run fit_turnbull with an increasing iteration cap; the final_loglik of
    a monotone EM must be non-decreasing in the cap (self-consistency EM never
    decreases the observed-data log-likelihood)."""
    out = []
    for k in range(1, n_steps + 1):
        out.append(fit_turnbull(intervals, max_iters=k).final_loglik)
    return out


def test_beyond_atom_key_is_string():
    assert isinstance(_BEYOND, str)
