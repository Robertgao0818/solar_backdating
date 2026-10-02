"""Offline tests for year-class labels used in the satellite availability brief."""

from scripts.temporal.satellite_year_availability import classify_year


def test_joburg_2023_is_priority_sparse():
    assert classify_year(n_cells_with=32, n_h3=74, n_dates=32) == "sparse_1_2"


def test_citywide_one_look_is_not_priority():
    assert classify_year(n_cells_with=327, n_h3=367, n_dates=353) == "one_look"


def test_dense_requires_both_revisit_and_spatial():
    assert classify_year(n_cells_with=367, n_h3=367, n_dates=4421) == "dense"


def test_singleton_is_noise_not_a_city_year():
    assert classify_year(n_cells_with=1, n_h3=147, n_dates=1) == "noise"


def test_absent_and_usable():
    assert classify_year(n_cells_with=0, n_h3=74, n_dates=0) == "absent"
    assert classify_year(n_cells_with=74, n_h3=74, n_dates=196) == "usable"
