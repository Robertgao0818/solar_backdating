"""Unit tests for the D6 commission comparison. No full tables."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from shapely.geometry import box

from scripts.validation.cv_appendix_constants import D6_MONTH_DAYS
from scripts.validation.cv_appendix_d6 import (
    CASE_AGREE,
    CASE_CONTRADICTION,
    CASE_LAG,
    InvalidParcelInterval,
    assign_exterior_metres,
    classify_case,
    classify_dated_case,
    commission_inside_interval,
    delta_months,
    display_edges,
    earliest_commission,
    enters_case_count,
    enters_delta_histogram,
    histogram_with_tails,
    reference_histogram,
    summarize_months,
    summarize_registration_lag,
)
from scripts.validation.cv_appendix_rules import parcel_model_interval


def test_only_dated_parcels_enter_the_histogram():
    assert enters_delta_histogram("dated")
    for kind in ("left_censored", "census_bound", "mixed", "undated", "no_model"):
        assert not enters_delta_histogram(kind)
    doc = enters_delta_histogram.__doc__ or ""
    assert "all polygons are dated" not in doc.lower()
    assert "ignored" in doc.lower()


def test_case_count_is_separate_from_the_histogram():
    for kind in ("dated", "census_bound", "mixed", "left_censored"):
        assert enters_case_count(kind)
    for kind in ("undated", "no_model"):
        assert not enters_case_count(kind)
    assert enters_delta_histogram("dated")
    assert not enters_delta_histogram("mixed")
    commission = pd.Timestamp("2020-01-01")
    assert classify_case("dated", commission, pd.Timestamp("2020-06-01"), commission, 90) == classify_dated_case(
        commission, pd.Timestamp("2020-06-01"), commission, 90
    )
    with pytest.raises(ValueError):
        classify_case("undated", commission, pd.Timestamp("2020-06-01"), commission, 90)
    with pytest.raises(ValueError):
        classify_case("no_model", None, None, commission, 90)


def test_census_bound_uses_the_dated_rule_on_the_census_day():
    kind, start, end = parcel_model_interval(
        ["census_bound"],
        [pd.Timestamp("2024-04-01")],
        [pd.Timestamp("2025-01-31")],
    )
    assert kind == "census_bound"
    assert start == pd.Timestamp("2024-04-01")
    assert end == pd.Timestamp("2025-01-31")
    commission = pd.Timestamp("2024-07-02")
    # U is the census day, after C, so this is not registration lag.
    assert classify_case(kind, start, end, commission, 90) == CASE_AGREE
    late = commission + pd.Timedelta(days=91)
    assert classify_case(kind, late, end, commission, 90) == CASE_CONTRADICTION
    on_limit = commission + pd.Timedelta(days=90)
    assert classify_case(kind, on_limit, end, commission, 90) == CASE_AGREE
    # The same rule still returns lag when U is on or before C.
    assert classify_case(kind, pd.Timestamp("2020-01-01"), commission, commission, 90) == CASE_LAG


def test_mixed_case_uses_the_earliest_bounds():
    kind, start, end = parcel_model_interval(
        ["dated", "census_bound", "undated"],
        [pd.Timestamp("2022-06-01"), pd.Timestamp("2022-01-15"), None],
        [pd.Timestamp("2022-08-01"), pd.Timestamp("2025-01-31"), None],
    )
    assert kind == "mixed"
    assert start == pd.Timestamp("2022-01-15")
    assert end == pd.Timestamp("2022-08-01")
    commission = pd.Timestamp("2022-03-01")
    assert classify_case(kind, start, end, commission, 90) == CASE_AGREE
    # Earliest U on or before C is registration lag, even if the census day is later.
    lag_kind, lag_start, lag_end = parcel_model_interval(
        ["dated", "census_bound"],
        [pd.Timestamp("2021-01-01"), pd.Timestamp("2021-06-01")],
        [pd.Timestamp("2021-04-01"), pd.Timestamp("2025-01-31")],
    )
    assert lag_end == pd.Timestamp("2021-04-01")
    assert classify_case(lag_kind, lag_start, lag_end, pd.Timestamp("2021-05-01"), 90) == CASE_LAG
    contradiction_start = commission + pd.Timedelta(days=91)
    assert (
        classify_case(kind, contradiction_start, pd.Timestamp("2022-12-01"), commission, 90)
        == CASE_CONTRADICTION
    )


def test_left_censored_is_never_a_contradiction():
    commission = pd.Timestamp("2020-06-01")
    present = pd.Timestamp("2020-01-15")
    assert classify_case("left_censored", None, present, commission, 90) == CASE_LAG
    assert classify_case("left_censored", None, present, commission, 0) == CASE_LAG
    assert classify_case("left_censored", None, present, commission, 180) == CASE_LAG
    assert classify_case("left_censored", None, commission, commission, 90) == CASE_LAG
    assert classify_case("left_censored", pd.NaT, commission + pd.Timedelta(days=1), commission, 0) == CASE_AGREE
    with pytest.raises(ValueError):
        classify_case("left_censored", pd.Timestamp("2021-01-01"), present, commission, 90)
    with pytest.raises(ValueError):
        classify_case("left_censored", None, None, commission, 90)


def test_cases_follow_the_frozen_boundaries():
    left = pd.Timestamp("2020-01-01")
    assert classify_dated_case(left, pd.Timestamp("2020-02-01"), pd.Timestamp("2020-03-01"), 90) == CASE_LAG
    assert classify_dated_case(left, pd.Timestamp("2020-04-01"), pd.Timestamp("2020-02-01"), 90) == CASE_AGREE
    assert classify_dated_case(pd.Timestamp("2020-06-01"), pd.Timestamp("2020-07-01"), left, 90) == CASE_CONTRADICTION
    # U == C is registration lag, not agreement.
    assert classify_dated_case(left, pd.Timestamp("2020-02-01"), pd.Timestamp("2020-02-01"), 90) == CASE_LAG
    # L exactly at C + tau is still agreement. One day later is a contradiction.
    commission = pd.Timestamp("2020-01-01")
    assert classify_dated_case(commission + pd.Timedelta(days=90), pd.Timestamp("2020-06-01"), commission, 90) == CASE_AGREE
    assert (
        classify_dated_case(commission + pd.Timedelta(days=91), pd.Timestamp("2020-06-01"), commission, 90)
        == CASE_CONTRADICTION
    )


def test_inverted_interval_is_not_repaired():
    with pytest.raises(InvalidParcelInterval):
        classify_dated_case(pd.Timestamp("2020-06-01"), pd.Timestamp("2020-01-01"), pd.Timestamp("2020-03-01"), 90)


def test_delta_is_midpoint_minus_commission_in_defined_months():
    midpoint = pd.Timestamp("2020-02-01")
    commission = pd.Timestamp("2020-01-01")
    assert delta_months(midpoint, commission) == pytest.approx(31 / D6_MONTH_DAYS)
    assert delta_months(commission, midpoint) < 0
    assert commission_inside_interval(
        pd.Timestamp("2020-01-01"), pd.Timestamp("2020-03-01"), pd.Timestamp("2020-03-01")
    )
    assert not commission_inside_interval(
        pd.Timestamp("2020-01-01"), pd.Timestamp("2020-03-01"), pd.Timestamp("2020-03-02")
    )


def test_summary_quantiles_use_the_linear_method():
    summary = summarize_months(np.array([1.0, 2.0, 3.0, 4.0]))
    assert summary["n"] == 4
    assert summary["median"] == pytest.approx(2.5)
    assert summary["q1"] == pytest.approx(1.75)
    assert summary["q3"] == pytest.approx(3.25)
    assert summary["iqr"] == pytest.approx(1.5)
    wide = summarize_months(np.array([0.0, 6.0, 12.0, 13.0]))
    assert wide["n_abs_le_6"] == 2
    assert wide["n_abs_le_12"] == 3


def test_registration_lag_uses_only_rows_with_u_on_or_before_c():
    lag = summarize_registration_lag(
        [pd.Timestamp("2020-04-10"), pd.Timestamp("2020-01-15")],
        [pd.Timestamp("2020-04-01"), pd.Timestamp("2020-02-01")],
    )
    assert lag["n"] == 1
    assert lag["median_days"] == pytest.approx(9.0)
    assert lag["median_months"] == pytest.approx(9.0 / D6_MONTH_DAYS)
    empty = summarize_registration_lag([pd.Timestamp("2020-01-01")], [pd.Timestamp("2020-02-01")])
    assert empty["n"] == 0
    assert empty["median_days"] is None


def test_earliest_commission_is_the_minimum_calendar_day():
    assert earliest_commission([pd.Timestamp("2021-05-02"), pd.Timestamp("2020-12-31 18:00")]) == pd.Timestamp(
        "2020-12-31"
    )


def test_reference_histogram_is_the_exact_uniform_mixture():
    edges = display_edges(-2, 2, 1)
    one_day = pd.Timestamp("2020-01-15")
    counts, below, above = reference_histogram([one_day], [one_day], edges)
    assert below == 0
    assert above == 0
    assert counts.sum() == pytest.approx(1.0)
    # Δ = 0 falls in the bin [0, 1).
    assert counts[list(edges[:-1]).index(0.0)] == pytest.approx(1.0)
    observed, obs_below, obs_above = histogram_with_tails(np.array([0.0, -2.5, 2.5]), edges)
    assert obs_below == 1
    assert obs_above == 1
    assert int(observed.sum()) + obs_below + obs_above == 3


def test_exterior_buffer_does_not_pick_among_ties():
    geoms = [box(0, 0, 10, 10), box(30, 0, 40, 10)]
    ids = np.array([11, 22])
    xy = np.array(
        [
            [20.5, 5.0],  # 9.5 m from parcel 22, 10.5 m from parcel 11
            [20.0, 5.0],  # 10 m from both
            [100.0, 100.0],
        ]
    )
    out_ids, status = assign_exterior_metres(xy, geoms, ids, 10.0)
    assert status.tolist() == ["buffer_unique", "buffer_ambiguous", "unmatched"]
    assert out_ids[0] == 22
    assert np.isnan(out_ids[1])
    assert np.isnan(out_ids[2])
    empty_ids, empty_status = assign_exterior_metres(np.zeros((0, 2)), geoms, ids, 10.0)
    assert len(empty_ids) == 0
    assert len(empty_status) == 0
