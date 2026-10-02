"""Rules for monotonicity and k consecutive presents."""

from scripts.temporal.ct_visit_robustness import (
    confirming_run,
    is_monotonic,
    max_consecutive_present,
    side_fields,
    usable_series,
)


def test_unusable_frames_are_skipped_and_do_not_break_a_run() -> None:
    pairs = [("2019-01-01", "0"), ("2019-06-01", "u"), ("2020-01-01", "1"), ("2021-01-01", "1")]
    series = usable_series(pairs)
    assert series == [("2019-01-01", "0"), ("2020-01-01", "1"), ("2021-01-01", "1")]
    assert is_monotonic(series)
    found = confirming_run(series, 2)
    assert found is not None
    assert found["last_absent"] == "2019-01-01"
    assert found["confirm_date"] == "2021-01-01"
    assert found["run_span_days"] == 366
    assert found["absent_gap_days"] == 731


def test_isolated_present_does_not_confirm_k2() -> None:
    series = [("2019-01-01", "0"), ("2020-01-01", "1"), ("2021-01-01", "0"), ("2022-01-01", "1"), ("2023-01-01", "1")]
    assert not is_monotonic(series)
    assert max_consecutive_present(series) == 2
    k1 = confirming_run(series, 1)
    k2 = confirming_run(series, 2)
    assert k1 is not None and k1["confirm_date"] == "2020-01-01"
    assert k1["last_absent"] == "2019-01-01"
    assert k2 is not None and k2["confirm_date"] == "2023-01-01"
    assert k2["last_absent"] == "2021-01-01"
    assert confirming_run(series, 3) is None


def test_leading_presents_have_no_preceding_absent() -> None:
    series = [("2019-01-15", "1"), ("2020-09-30", "1"), ("2023-10-19", "0")]
    assert not is_monotonic(series)
    k2 = confirming_run(series, 2)
    assert k2 is not None
    assert k2["last_absent"] == ""
    assert k2["confirm_date"] == "2020-09-30"
    assert k2["absent_gap_days"] == ""


def test_side_fields_use_a_stable_k_schema() -> None:
    fields = side_fields("raw", [("2019-01-01", "0"), ("2020-01-01", "u"), ("2021-01-01", "1")])
    assert fields["raw_n_absent"] == 1
    assert fields["raw_n_present"] == 1
    assert fields["raw_monotonic"] == 1
    assert fields["raw_usable_code"] == "0 1"
    assert fields["raw_k1_reached"] == 1
    assert fields["raw_k1_confirm_date"] == "2021-01-01"
    assert fields["raw_k1_run_span_days"] == 0
    assert fields["raw_k2_reached"] == 0
    assert fields["raw_k2_confirm_date"] == ""
