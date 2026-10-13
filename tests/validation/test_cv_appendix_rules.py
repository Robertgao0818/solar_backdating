"""Unit tests for the appendix figure rules. No network and no full tables."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from scripts.validation.cv_appendix_constants import (
    CAPACITY_KWP_PER_M2,
    CITYWIDE_ROWS,
    CLASS_COUNTS,
    SSEG_COMMISSIONED_ROWS,
    SSEG_USECOLS,
)
from scripts.validation.cv_appendix_rules import (
    interval_installed,
    interval_midpoint,
    is_commissioned_status,
    is_offgrid_status,
    is_residential_cell,
    is_residential_zoning,
    installed_mask,
    lagged_pearson,
    polygon_interval,
    model_capacity_kwp,
    parcel_model_interval,
    polygon_installed,
    primary_zone,
    quantile_bin_means,
    spread_interval,
    supply_layer_covers_city,
)
from scripts.validation.fetch_coct_parcels import page_ranges


def _row(**kwargs) -> pd.Series:
    base = {
        "date_status": None,
        "left_censored": 0,
        "census_bound": 0,
        "install_interval_start": None,
        "install_interval_end": None,
        "latest_absent_date": None,
        "earliest_present_date": None,
    }
    base.update(kwargs)
    return pd.Series(base)


def test_acceptance_totals_add_up():
    assert sum(CLASS_COUNTS.values()) == CITYWIDE_ROWS
    assert SSEG_COMMISSIONED_ROWS == 5458


def test_sseg_loader_does_not_read_rounded_coordinates():
    assert "latitude" not in SSEG_USECOLS
    assert "longitude" not in SSEG_USECOLS
    assert "wkt" in SSEG_USECOLS


def test_commissioned_prefixes_do_not_match_other_statuses():
    assert is_commissioned_status("1.0 Approved grid-tied installation commissioned")
    assert is_commissioned_status("4.0 Approved grid-tied installation commissioned (legalised)")
    assert not is_commissioned_status("2.0 Approved off-grid installation")
    assert not is_commissioned_status("0.4 Permission to install grid-tied installation")
    assert not is_commissioned_status("10.0 something")
    assert is_offgrid_status("2.0 Approved off-grid installation")
    assert not is_offgrid_status("1.0 Approved grid-tied installation commissioned")


def test_midpoint_matches_the_published_dated_examples():
    assert interval_midpoint(pd.Timestamp("2022-01-30"), pd.Timestamp("2022-04-17")) == pd.Timestamp(
        "2022-03-09"
    )
    assert interval_midpoint(pd.Timestamp("2022-04-17"), pd.Timestamp("2022-04-30")) == pd.Timestamp(
        "2022-04-23"
    )


def test_stock_rules_by_class():
    snap = pd.Timestamp("2024-06-30")
    left = _row(
        date_status="done_already_present_before_geid_history",
        left_censored=1,
        earliest_present_date="2019-01-15",
    )
    dated = _row(
        date_status="done_appears",
        install_interval_start="2022-01-30",
        install_interval_end="2022-04-17",
    )
    late = _row(
        date_status="done_appears",
        install_interval_start="2024-06-01",
        install_interval_end="2024-12-01",
    )
    bound = _row(
        date_status="done_installed_during_census",
        census_bound=1,
        latest_absent_date="2023-10-19",
        earliest_present_date="2025-01-31",
    )
    undated = _row()
    for rule in ("lower", "midpoint", "upper"):
        assert polygon_installed(left, snap, rule)
        assert not polygon_installed(undated, snap, rule)
    assert polygon_installed(dated, snap, "midpoint")
    assert polygon_installed(dated, snap, "lower")
    # Interval opens before the snapshot and closes after it.
    assert not polygon_installed(late, snap, "midpoint")
    assert not polygon_installed(late, snap, "lower")
    assert polygon_installed(late, snap, "upper")
    frame = pd.DataFrame([dated, late, left, bound, undated])
    classified = frame.apply(
        lambda row: pd.Series(polygon_interval(row), index=["kind", "start", "end"]),
        axis=1,
    )
    mask = installed_mask(classified.kind, classified.start, classified.end, snap, "midpoint")
    scalar = [
        interval_installed(k, s, e, snap, "midpoint")
        for k, s, e in classified.itertuples(index=False)
    ]
    assert mask.tolist() == scalar
    # Census end is 2025-01-31, after the snapshot, so the lower bound is out.
    assert not polygon_installed(bound, snap, "lower")
    assert polygon_installed(bound, snap, "upper")
    assert polygon_installed(bound, snap, "midpoint")
    # Same census-bounded polygon is not yet installed at the start of 2020.
    assert not polygon_installed(bound, pd.Timestamp("2020-01-31"), "midpoint")
    assert not polygon_installed(bound, pd.Timestamp("2020-01-31"), "upper")


def test_capacity_uses_the_frozen_density():
    assert CAPACITY_KWP_PER_M2 == 0.19
    assert model_capacity_kwp(10) == 1.9


def test_residential_zone_drops_road_slivers():
    zoning = (
        "Single Residential 1 : Conventional Housing,"
        "Transport 2 : Public Road and Public Parking,"
        "Transport 2 : Public Road and Public Parking,Utility"
    )
    assert primary_zone(zoning).startswith("Single Residential")
    assert is_residential_zoning(zoning)
    assert not is_residential_zoning("General Industrial 1")
    assert is_residential_zoning("General Residential 2, General Business 1")
    assert is_residential_cell(0.5)
    assert not is_residential_cell(0.49)
    assert not is_residential_cell(float("nan"))


def test_spread_and_cross_correlation():
    spread = spread_interval(pd.Timestamp("2022-01-31"), pd.Timestamp("2022-02-01"))
    assert abs(sum(spread.values()) - 1) < 1e-9
    assert set(spread) == {2022 * 12 + 1, 2022 * 12 + 2}
    rows = lagged_pearson(
        {1: 1.0, 2: 2.0, 3: 3.0, 4: 4.0},
        {1: 1.0, 2: 2.0, 3: 3.0, 4: 4.0, 5: 5.0},
        max_lag=1,
    )
    assert rows[0]["pearson"] == 1.0
    assert rows[1]["n_months"] == 4
    assert rows[1]["pearson"] == 1.0


def test_quantile_bins_keep_a_zero_bin():
    bins = quantile_bin_means(np.array([0, 0, 1, 2, 3, 4]), np.array([0, 1, 1, 1, 1, 1]), n_bins=2)
    assert bins.loc[bins.kind == "zero", "n"].item() == 2
    assert bins.loc[bins.kind == "quantile", "n"].sum() == 4


def test_parcel_interval_left_censor_has_no_l():
    kind, start, end = parcel_model_interval(
        ["left_censored", "dated"],
        [None, pd.Timestamp("2022-01-30")],
        [pd.Timestamp("2019-01-15"), pd.Timestamp("2022-04-17")],
    )
    assert kind == "left_censored"
    assert start is None
    assert end == pd.Timestamp("2019-01-15")


def test_page_ranges_cover_the_server_count():
    ranges = page_ranges(835018, 2000)
    assert ranges[0] == (1, 2000)
    assert ranges[-1] == (834001, 835018)
    assert len(ranges) == 418
    assert sum(hi - lo + 1 for lo, hi in ranges) == 835018


def test_figure_scripts_do_not_multiply_by_the_rejected_density():
    root = Path(__file__).resolve().parents[2] / "scripts" / "validation"
    banned = ("* 0.040", "* 0.04", "*0.040", "*0.04")
    for name in (
        "cv_appendix_rules.py",
        "cv_appendix_constants.py",
        "cv_appendix_d6.py",
        "build_cv_appendix_figures.py",
        "build_cv_appendix_v2.py",
        "build_cv_appendix_v3.py",
    ):
        text = (root / name).read_text()
        for token in banned:
            assert token not in text, name


def test_supply_layer_covers_city_rejects_a_layer_that_misses_the_metro():
    # One named city location outside both polygons is enough.
    assert supply_layer_covers_city(1, 0.01) is False
    # A high share of commissioned points outside both polygons is enough.
    assert supply_layer_covers_city(0, 0.86) is False
    assert supply_layer_covers_city(0, 0.20) is False
    assert supply_layer_covers_city(0, float("nan")) is False
    assert supply_layer_covers_city(0, 0.05) is True


def test_interval_installed_rejects_unknown_rule():
    try:
        interval_installed("dated", pd.Timestamp("2020-01-01"), pd.Timestamp("2020-02-01"), pd.Timestamp("2020-06-01"), "nope")
    except ValueError:
        return
    raise AssertionError("expected ValueError")


def test_v2_revision_refuses_frozen_directories():
    from scripts.validation import build_cv_appendix_v2 as builder

    frozen_staging = builder.WORK_ROOT / "staging_v2r1"
    frozen_release = builder.WORK_ROOT / "release_v2r1"
    for tag in (None, "", "v2r1", "v3r1", "v1r2", "../v2r2"):
        try:
            builder.bind_revision(tag)
        except SystemExit:
            continue
        raise AssertionError(f"revision {tag!r} was accepted")
    builder.bind_revision("v2r2")
    assert builder.STAGING.name == "staging_v2r2"
    assert builder.RELEASE.name == "release_v2r2"
    assert builder.REVISION == "v2r2"
    builder.STAGING = frozen_staging
    builder.RELEASE = frozen_release
    builder.REVISION = None


def test_v2_collaborator_text_states_counts_as_prose():
    from scripts.validation import build_cv_appendix_v2 as builder

    builder.REVISION = "v2r2"
    d2 = {
        "cell": "89ad3617603ffff",
        "cell_choice": "owner_override",
        "n_residential_parcels": 143,
        "n_new_parcels": 44,
        "d3_model_parcels_midpoint": 45,
        "d3_sseg_parcels": 7,
        "top_share": 0.12,
        "top_zone": "SR1",
        "eskom_area_fraction": 0.0,
        "vexcel_collection": "za-wc-rondebosch-2024",
        "vexcel_first_capture": "2024-04-12T11:23:26",
        "vexcel_gsd_cm": 6.2,
        "n_already": 1,
        "model_by_year": {"2020": 2, "2021": 4, "2022": 7, "2023": 10, "2024": 21},
        "sseg_by_year": {"2020": 1, "2021": 0, "2022": 1, "2023": 1, "2024": 4},
    }
    picks = pd.DataFrame(
        [
            {
                "slot": "TP residential",
                "grid_id": "L1896",
                "area_m2": 22.266194,
                "iou": 0.913325,
                "max_iou_other": 0.913325,
            }
        ]
    )
    readme, captions = builder._release_text(d2, picks, 2)
    builder._assert_collaborator_text(readme, captions)
    assert "2020: 2, 2021: 4, 2022: 7, 2023: 10, 2024: 21" in readme
    assert "2020: 1, 2021: 0, 2022: 1, 2023: 1, 2024: 4" in readme
    assert "owner's pick" in readme
    assert "89ad3601c7bffff" in readme
    assert "most parcels" not in readme
    assert "revision v2r2" in readme
    assert "6.2 cm" in captions
    assert "most parcels" not in captions
    builder.REVISION = None
