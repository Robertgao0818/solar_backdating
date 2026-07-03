"""Tests for `scripts.audit.coj_cohort_build` (ISSUE-09, WP-A).

Pure seams only — cohort-stratum assignment (incl. the design's boundary
cases), the per-anchor layer plan + unit_purpose precedence rule (design §2 +
the per-unit expectation amendment), the seeded stratified falsification
subsample, and the negative-control geometry helpers (lattice / STRtree
buffer-exclude / seeded draw / UTM->WGS84 bbox). No network, no GPU, no gpkg
I/O: geometry is exercised with tiny synthetic shapely shapes. `build_cohort`
(the thin fiona+csv I/O wrapper) is intentionally untested here — it is
exercised end-to-end by the WP-E smoke run.
"""
from __future__ import annotations

from collections import Counter

import pytest
from pyproj import Transformer
from shapely.geometry import Point, box
from shapely.strtree import STRtree

from scripts.audit.coj_cohort_build import (
    COHORT_STRATA,
    LAYER_YEARS,
    PlannedUnit,
    assign_cohort_stratum,
    exclude_near,
    lattice_centers,
    nc_bbox_4326,
    plan_units_for_anchor,
    sample_negative_controls,
    select_falsification_subsample,
)


# ---------------------------------------------------------------------------
# constants
# ---------------------------------------------------------------------------


def test_layer_years_and_strata_constants():
    assert LAYER_YEARS == (2015, 2019, 2023)
    assert COHORT_STRATA == (
        "c_cal_present_pre2019",
        "c_cal_present_2019_2022",
        "c_probe_2023",
        "c_findings_s3like",
        "c_negative_control",
    )


def test_planned_unit_is_frozen():
    u = PlannedUnit(anchor_id="a1", year=2023, stratum="c_probe_2023", unit_purpose="headline_2023")
    assert (u.anchor_id, u.year, u.stratum, u.unit_purpose) == (
        "a1",
        2023,
        "c_probe_2023",
        "headline_2023",
    )
    with pytest.raises(Exception):
        u.year = 2019  # type: ignore[misc]  # frozen dataclass


# ---------------------------------------------------------------------------
# assign_cohort_stratum
# ---------------------------------------------------------------------------


def _row(status="done_appears", start="2021-01-01", end="2021-06-01", anchor_id="a1"):
    return {
        "anchor_id": anchor_id,
        "status": status,
        "install_interval_start": start,
        "install_interval_end": end,
    }


def test_stratum_pre2019_end_before_2019():
    assert assign_cohort_stratum(_row(start="2016-01-01", end="2018-06-01")) == "c_cal_present_pre2019"


def test_stratum_pre2019_none_start_already_present():
    # done_already_present_before_geid_history rows carry an empty start; the
    # stratum rule keys on `end` only, so they still land in pre2019.
    row = _row(status="done_already_present_before_geid_history", start="", end="2018-03-30")
    assert assign_cohort_stratum(row) == "c_cal_present_pre2019"


def test_stratum_2019_2022():
    assert assign_cohort_stratum(_row(start="2020-06-01", end="2021-06-01")) == "c_cal_present_2019_2022"


def test_stratum_boundary_end_eq_2019_01_01_is_2019_2022_not_pre2019():
    # end == 2019-01-01 is NOT strictly < 2019-01-01 -> not pre2019.
    assert assign_cohort_stratum(_row(start="2018-06-01", end="2019-01-01")) == "c_cal_present_2019_2022"


def test_stratum_probe2023():
    assert assign_cohort_stratum(_row(start="2022-06-01", end="2024-06-01")) == "c_probe_2023"


def test_stratum_boundary_end_eq_2023_01_01_is_probe_not_2019_2022():
    # end == 2023-01-01 is NOT strictly < 2023-01-01 -> falls through to probe.
    assert assign_cohort_stratum(_row(start="2022-06-01", end="2023-01-01")) == "c_probe_2023"


def test_stratum_probe2023_none_start():
    row = _row(status="done_already_present_before_geid_history", start="", end="2024-02-21")
    assert assign_cohort_stratum(row) == "c_probe_2023"


def test_stratum_s3like_start_after_2023():
    assert assign_cohort_stratum(_row(start="2024-03-01", end="2024-06-01")) == "c_findings_s3like"


def test_stratum_boundary_start_eq_2023_12_31_is_not_s3like():
    # start == 2023-12-31 is NOT strictly > 2023-12-31 -> not s3like; with
    # end >= 2023-01-01 it is a probe.
    assert assign_cohort_stratum(_row(start="2023-12-31", end="2024-06-01")) == "c_probe_2023"


@pytest.mark.parametrize(
    "status",
    ["done_ambiguous_clamp_inverted", "done_ambiguous_marker_missed_pv"],
)
def test_stratum_excluded_statuses_return_none(status):
    assert assign_cohort_stratum(_row(status=status, start="2016-01-01", end="2018-06-01")) is None


def test_stratum_non_dated_statuses_return_none():
    for status in ["done_ambiguous_no_recent_anchor", "done_ambiguous_nonmonotonic"]:
        assert assign_cohort_stratum(_row(status=status, start="2016-01-01", end="2018-06-01")) is None


def test_stratum_unparseable_end_returns_none():
    assert assign_cohort_stratum(_row(start="2016-01-01", end="")) is None
    assert assign_cohort_stratum(_row(start="2016-01-01", end="nan")) is None


# ---------------------------------------------------------------------------
# plan_units_for_anchor
# ---------------------------------------------------------------------------


def _plan_set(row, **kw):
    return {(u.year, u.unit_purpose) for u in plan_units_for_anchor(row, **kw)}


def test_plan_pre2019_deep_all_calibration():
    row = _row(start="2013-01-01", end="2014-06-01")
    assert _plan_set(row, in_fals_2019=False) == {
        (2015, "calibration_present"),
        (2019, "calibration_present"),
        (2023, "calibration_present"),
    }


def test_plan_pre2019_2015_is_falsification_when_start_after_2015():
    # end 2018 (pre2019) but start 2016 -> at 2015 the pipeline claims absent,
    # so the 2015 unit falsifies rather than calibrates.
    row = _row(start="2016-01-01", end="2018-06-01")
    assert _plan_set(row, in_fals_2019=False) == {
        (2015, "falsification"),
        (2019, "calibration_present"),
        (2023, "calibration_present"),
    }


def test_plan_pre2019_boundary_end_eq_2015_01_01_2015_not_calibration():
    # end == 2015-01-01 is NOT strictly < Jan-1-2015 -> the 2015 unit is a
    # bisector, carrying no present-expectation.
    row = _row(start="2013-01-01", end="2015-01-01")
    got = _plan_set(row, in_fals_2019=False)
    assert (2015, "bisector") in got
    assert (2015, "calibration_present") not in got


def test_plan_pre2019_none_start_2015_is_bisector():
    row = _row(status="done_already_present_before_geid_history", start="", end="2018-03-30")
    assert _plan_set(row, in_fals_2019=False) == {
        (2015, "bisector"),
        (2019, "calibration_present"),
        (2023, "calibration_present"),
    }


def test_plan_2019_2022_plain_only_2023():
    row = _row(start="2020-06-01", end="2021-06-01")
    assert _plan_set(row, in_fals_2019=False) == {(2023, "calibration_present")}


def test_plan_2019_2022_bisector_gets_2019():
    # start < 2019-01-01 AND end > 2019-12-31 -> genuine 2019 bisector.
    row = _row(start="2018-06-01", end="2020-06-01")
    assert _plan_set(row, in_fals_2019=False) == {
        (2019, "bisector"),
        (2023, "calibration_present"),
    }


def test_plan_2019_2022_in_fals_gets_2019_falsification():
    row = _row(start="2020-06-01", end="2021-06-01")
    assert _plan_set(row, in_fals_2019=True) == {
        (2019, "falsification"),
        (2023, "calibration_present"),
    }


def test_plan_probe2023_plain_only_headline():
    row = _row(start="2022-06-01", end="2024-06-01")
    assert _plan_set(row, in_fals_2019=False) == {(2023, "headline_2023")}


def test_plan_probe2023_in_fals_gets_2019_falsification():
    row = _row(start="2022-06-01", end="2024-06-01")
    assert _plan_set(row, in_fals_2019=True) == {
        (2019, "falsification"),
        (2023, "headline_2023"),
    }


def test_plan_probe2023_bisector_gets_2019_bisector():
    # deep-past start with end past 2023 -> the 2019 bit bisects.
    row = _row(start="2018-06-01", end="2024-06-01")
    assert _plan_set(row, in_fals_2019=False) == {
        (2019, "bisector"),
        (2023, "headline_2023"),
    }


def test_plan_s3like_both_years_findings():
    row = _row(start="2024-03-01", end="2024-06-01")
    assert _plan_set(row, in_fals_2019=False) == {
        (2019, "findings_s3like"),
        (2023, "findings_s3like"),
    }


def test_plan_none_stratum_returns_empty():
    row = _row(status="done_ambiguous_no_recent_anchor", start="2020-01-01", end="2021-01-01")
    assert plan_units_for_anchor(row, in_fals_2019=False) == []
    excluded = _row(status="done_ambiguous_clamp_inverted", start="2016-01-01", end="2018-01-01")
    assert plan_units_for_anchor(excluded, in_fals_2019=False) == []


def test_plan_every_dated_anchor_gets_a_2023_unit():
    for start, end in [
        ("2013-01-01", "2014-06-01"),
        ("2020-06-01", "2021-06-01"),
        ("2022-06-01", "2024-06-01"),
        ("2024-03-01", "2024-06-01"),
    ]:
        row = _row(start=start, end=end)
        years = {u.year for u in plan_units_for_anchor(row, in_fals_2019=False)}
        assert 2023 in years


# ---------------------------------------------------------------------------
# select_falsification_subsample
# ---------------------------------------------------------------------------


def _fals_rows():
    rows = []
    for yr, n in [(2020, 772), (2021, 1471), (2022, 6890), (2023, 1212)]:
        for i in range(n):
            rows.append(_row(start=f"{yr}-06-01", end=f"{yr}-09-01", anchor_id=f"y{yr}_{i:05d}"))
    return rows


def _year_counts(selected):
    return Counter(a.split("_")[0][1:] for a in selected)


def test_fals_subsample_hamilton_allocation_matches_design():
    sel = select_falsification_subsample(
        _fals_rows(), seed=20260704, n_target=1200, per_stratum_floor=150
    )
    assert len(sel) == 1200
    assert _year_counts(sel) == {"2020": 188, "2021": 231, "2022": 565, "2023": 216}


def test_fals_subsample_deterministic_for_fixed_seed():
    rows = _fals_rows()
    a = select_falsification_subsample(rows, seed=20260704, n_target=1200)
    b = select_falsification_subsample(rows, seed=20260704, n_target=1200)
    assert a == b


def test_fals_subsample_different_seed_differs():
    rows = _fals_rows()
    a = select_falsification_subsample(rows, seed=1, n_target=1200)
    b = select_falsification_subsample(rows, seed=2, n_target=1200)
    assert a != b


def test_fals_subsample_excludes_s3like_and_non_dated():
    rows = _fals_rows()
    rows.append(_row(start="2024-06-01", end="2024-09-01", anchor_id="s3_0"))  # s3like
    rows.append(
        _row(
            status="done_ambiguous_no_recent_anchor",
            start="2022-06-01",
            end="2022-09-01",
            anchor_id="amb_0",
        )
    )
    sel = select_falsification_subsample(rows, seed=1, n_target=1200)
    assert "s3_0" not in sel
    assert "amb_0" not in sel


def test_fals_subsample_honors_floor_and_small_pool_cap():
    rows = [
        _row(start="2020-06-01", end="2020-09-01", anchor_id=f"y2020_{i:03d}") for i in range(50)
    ] + [
        _row(start="2022-06-01", end="2022-09-01", anchor_id=f"y2022_{i:04d}") for i in range(5000)
    ]
    sel = select_falsification_subsample(rows, seed=1, n_target=1200, per_stratum_floor=150)
    counts = Counter(a.split("_")[0] for a in sel)
    assert counts["y2020"] == 50  # whole (sub-floor) pool taken
    assert counts["y2022"] == 1150  # floor + all of the remainder
    assert len(sel) == 1200


def test_fals_subsample_all_selected_are_absent_2019_claimants():
    rows = _fals_rows()
    sel = select_falsification_subsample(rows, seed=7, n_target=300)
    # every selected anchor's start year is in {2020,2021,2022,2023}
    assert all(a.split("_")[0][1:] in {"2020", "2021", "2022", "2023"} for a in sel)


# ---------------------------------------------------------------------------
# negative-control geometry (pure shapely)
# ---------------------------------------------------------------------------


def test_lattice_centers_keeps_only_interior_points():
    poly = box(0.0, 0.0, 90.0, 90.0)
    centers = set(lattice_centers(poly, spacing_m=30.0))
    # candidate grid {0,30,60,90}^2; only strictly-interior points survive.
    assert centers == {(30.0, 30.0), (30.0, 60.0), (60.0, 30.0), (60.0, 60.0)}


def test_lattice_centers_deterministic_order():
    poly = box(0.0, 0.0, 90.0, 90.0)
    assert lattice_centers(poly, spacing_m=30.0) == lattice_centers(poly, spacing_m=30.0)


def test_exclude_near_drops_close_keeps_far_with_polys():
    centers = [(30.0, 30.0), (60.0, 60.0)]
    detections = [box(28.0, 28.0, 32.0, 32.0)]  # sits on top of (30,30)
    survivors = exclude_near(centers, detections, buffer_m=5.0)
    assert survivors == [(60.0, 60.0)]


def test_exclude_near_accepts_prebuilt_strtree():
    centers = [(30.0, 30.0), (60.0, 60.0)]
    tree = STRtree([box(28.0, 28.0, 32.0, 32.0)])
    survivors = exclude_near(centers, tree, buffer_m=5.0)
    assert survivors == [(60.0, 60.0)]


def test_exclude_near_buffer_boundary_is_inclusive():
    centers = [(0.0, 0.0)]
    det = [box(10.0, -1.0, 11.0, 1.0)]  # nearest point (10,0), distance 10
    assert exclude_near(centers, det, buffer_m=10.0) == []  # distance == buffer -> excluded
    assert exclude_near(centers, det, buffer_m=9.9) == [(0.0, 0.0)]  # just outside -> kept


def test_exclude_near_empty_detections_keeps_all():
    centers = [(1.0, 1.0), (2.0, 2.0)]
    assert exclude_near(centers, [], buffer_m=5.0) == centers


def test_sample_negative_controls_deterministic_subset():
    surviving = [(float(i), float(i)) for i in range(20)]
    a = sample_negative_controls(surviving, seed=20260704, n=5)
    b = sample_negative_controls(surviving, seed=20260704, n=5)
    assert a == b
    assert len(a) == 5
    assert set(a).issubset(set(surviving))


def test_sample_negative_controls_n_ge_pool_returns_all():
    surviving = [(1.0, 1.0), (2.0, 2.0)]
    out = sample_negative_controls(surviving, seed=1, n=10)
    assert set(out) == set(surviving)


def test_sample_negative_controls_different_seed_differs():
    surviving = [(float(i), float(i)) for i in range(100)]
    a = sample_negative_controls(surviving, seed=1, n=10)
    b = sample_negative_controls(surviving, seed=2, n=10)
    assert set(a) != set(b)


def test_nc_bbox_4326_corners_ordered_and_round_trip():
    # a plausible JHB point in UTM 35S (EPSG:32735)
    center = (600000.0, 7100000.0)
    lon_min, lat_min, lon_max, lat_max = nc_bbox_4326(center, chip_half_m=48.0, utm_epsg=32735)
    assert lon_max > lon_min  # xmax > xmin
    assert lat_max > lat_min
    # round-trip the SW corner back to UTM ~ (center - 48)
    back = Transformer.from_crs("EPSG:4326", "EPSG:32735", always_xy=True)
    x, y = back.transform(lon_min, lat_min)
    assert x == pytest.approx(center[0] - 48.0, abs=1.0)
    assert y == pytest.approx(center[1] - 48.0, abs=1.0)
    # box is ~96 m wide -> a small fraction of a degree
    assert 0.0 < (lon_max - lon_min) < 0.01
