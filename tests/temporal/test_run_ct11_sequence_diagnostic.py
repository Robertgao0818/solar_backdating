from types import SimpleNamespace

from scripts.temporal.run_ct11_sequence_diagnostic import classify_sequence


def _result(values, quality="usable"):
    observations = [
        SimpleNamespace(date_index=index, capture_date=f"2024-0{index}-01", pv_present=value)
        for index, value in enumerate(values, start=1)
    ]
    return SimpleNamespace(observations=observations, quality_flag=quality)


def test_classify_sequence_preserves_censoring_and_transition_semantics():
    assert classify_sequence(_result([True, True])) == (
        "ALREADY_PRESENT",
        "",
        "2024-01-01",
    )
    assert classify_sequence(_result([False, False])) == (
        "ALL_ABSENT",
        "2024-02-01",
        "",
    )
    assert classify_sequence(_result([False, None, True])) == (
        "TRANSITION",
        "2024-01-01",
        "2024-03-01",
    )


def test_classify_sequence_does_not_turn_unknown_or_nonmonotonic_into_absence():
    assert classify_sequence(_result([None, True])) == ("UNDATABLE", "", "")
    assert classify_sequence(_result([True, False])) == ("UNDATABLE", "", "")
    assert classify_sequence(_result([False, False], quality="unusable")) == (
        "UNDATABLE",
        "",
        "",
    )
