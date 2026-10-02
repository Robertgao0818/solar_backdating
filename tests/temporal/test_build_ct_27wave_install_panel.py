"""Cape Town scored frames use the same installed-label rules as Johannesburg."""

from scripts.temporal.build_ct_27wave_install_panel import observations_from_state
from scripts.temporal.build_jhb_run3_install_panel import (
    classify_panel_date,
    decoder_installed,
)
from scripts.temporal.scan_state import Round, RoundResult, ScanState


def _result(capture_date, present, *, quality="usable", confidence=0.9, reference_only=False):
    return RoundResult(
        chip_index=1,
        capture_date=capture_date,
        version=1,
        pv_present=present,
        confidence=confidence,
        quality_flag=quality,
        decision_source="test",
        reference_only=reference_only,
    )


def _state(results):
    return ScanState(
        anchor_id="ct_test",
        region_key="cape_town",
        grid_id="CPT0001",
        status="done_ambiguous_nonmonotonic",
        rounds=[
            Round(
                round_id=1,
                round_type="initial",
                window_start_date=None,
                window_end_date=None,
                results=results,
            )
        ],
        census_date="2025-01-31",
    )


def test_dip_repaired_frame_is_an_observed_present_and_reference_is_not() -> None:
    state = _state(
        [
            _result("2018-06-15", True, confidence=0.95),
            _result("2019-06-15", False, confidence=0.1),
            _result("2021-06-15", True, confidence=0.98),
            _result("2025-10-10", False, reference_only=True),
        ]
    )
    rows = {row["capture_date"]: row for row in observations_from_state(state)}
    assert decoder_installed(rows["2019-06-15"]) == "1"
    assert rows["2019-06-15"]["dip_flipped"] == "1"
    assert rows["2019-06-15"]["pv_present"] == "0"
    assert decoder_installed(rows["2025-10-10"]) is None
    assert rows["2025-10-10"]["post_census"] == "1"
    assert rows["2025-10-10"]["in_evidence"] == "0"


def test_unscored_catalog_date_inside_the_bracket_stays_blank() -> None:
    assert classify_panel_date(
        "2020-06-01",
        observed=None,
        has_bracket=True,
        left_censored=False,
        interval_start="2020-01-01",
        interval_end="2021-01-01",
    ) == ("", "unknown_in_interval")
