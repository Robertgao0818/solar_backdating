"""Wide anchor-by-date panel pivoted from the visit sequence strings."""

from __future__ import annotations

import pytest

from scripts.temporal.build_ct_presence_panel import (
    aligned_pairs,
    panel_header,
    panel_values,
    split_codes,
    split_dates,
)


def test_empty_sequence_fields_split_to_nothing() -> None:
    assert split_dates("") == []
    assert split_codes("") == []
    assert split_dates("2019-01-15;2020-01-30") == ["2019-01-15", "2020-01-30"]
    assert split_codes("0 u 1") == ["0", "u", "1"]


def test_panel_puts_each_date_in_its_own_column_and_leaves_other_dates_blank() -> None:
    dates = ["2019-01-15", "2020-01-30", "2020-09-30"]
    values = panel_values(
        "anchor-a",
        "complete",
        dates,
        aligned_pairs("2019-01-15;2020-09-30", "1 u", "anchor-a"),
    )
    assert panel_header(dates) == [
        "source_anchor_id",
        "sequence_coverage",
        "2019-01-15",
        "2020-01-30",
        "2020-09-30",
    ]
    assert values == ["anchor-a", "complete", "1", "", "u"]


def test_missing_scan_state_is_a_blank_row() -> None:
    dates = ["2019-01-15", "2020-01-30"]
    assert panel_values("anchor-b", "missing_scan_state", dates, []) == [
        "anchor-b",
        "missing_scan_state",
        "",
        "",
    ]


def test_duplicate_date_or_unknown_code_is_rejected() -> None:
    with pytest.raises(ValueError, match="duplicate"):
        aligned_pairs("2019-01-15;2019-01-15", "0 1", "anchor-a")
    with pytest.raises(ValueError, match="outside"):
        aligned_pairs("2019-01-15", "2", "anchor-a")
