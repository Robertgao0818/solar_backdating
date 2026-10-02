"""Offline tests for H3-7 spatial coverage classes."""

from scripts.temporal.build_h3_spatial_availability import coverage_class, tm_cell_class


def test_urban_centre_without_vexcel_is_gap():
    assert (
        coverage_class(in_vexcel=False, in_urban_centre=True, in_urban_domain=True)
        == "urban_centre_gap"
    )


def test_vexcel_wins_even_outside_urban_centre():
    assert (
        coverage_class(in_vexcel=True, in_urban_centre=False, in_urban_domain=False)
        == "vexcel"
    )


def test_tm_cell_thresholds():
    assert tm_cell_class(None) == "unprobed"
    assert tm_cell_class(0) == "empty"
    assert tm_cell_class(32) == "thin"
    assert tm_cell_class(49) == "thin"
    assert tm_cell_class(50) == "usable"
    assert tm_cell_class(90) == "dense"
    assert tm_cell_class(113) == "dense"
