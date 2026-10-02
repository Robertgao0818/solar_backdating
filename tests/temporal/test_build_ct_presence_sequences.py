"""Visit-sequence encoding for the Cape Town citywide install-date table."""

from __future__ import annotations

from scripts.temporal.build_ct_presence_sequences import (
    CENSUS_CUTOFF,
    EncodedSequence,
    encode_scan_state,
    reconcile_sequence,
)
from scripts.temporal.scan_state import Round, RoundResult, ScanState


def _result(
    capture_date: str,
    present: bool | None,
    *,
    quality: str = "usable",
    confidence: float | None = 0.9,
    reference_only: bool = False,
) -> RoundResult:
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


def _state(results: list[RoundResult], status: str = "done_ambiguous_nonmonotonic") -> ScanState:
    rnd = Round(
        round_id=1,
        round_type="initial",
        window_start_date=None,
        window_end_date=None,
        results=results,
    )
    return ScanState(
        anchor_id="ct_test",
        region_key="cape_town",
        grid_id="CPT0001",
        status=status,
        rounds=[rnd],
        census_date=CENSUS_CUTOFF,
    )


def test_out_of_order_evidence_is_chronological_and_dips_flip() -> None:
    state = _state(
        [
            _result("2021-06-15", True, confidence=0.98),
            _result("2019-06-15", False, confidence=0.1),
            _result("2018-06-15", True, confidence=0.95),
            _result("2020-01-01", False, reference_only=True),
            _result("2025-10-10", False, reference_only=True),
        ]
    )
    encoded = encode_scan_state(state)
    assert encoded.obs_dates == ("2018-06-15", "2019-06-15", "2021-06-15")
    assert encoded.obs_code_raw == ("1", "0", "1")
    assert encoded.obs_code_repaired == ("1", "1", "1")
    assert encoded.dip_repaired_dates == ("2019-06-15",)
    assert encoded.ref_dates == ("2020-01-01", "2025-10-10")
    assert encoded.ref_code_raw == ("0", "0")
    assert encoded.file_order_was_chronological is False
    assert encoded.evidence[1].file_order == 1
    assert encoded.evidence[1].dip_repaired is True


def test_leading_absent_and_unusable_stay_put() -> None:
    state = _state(
        [
            _result("2017-06-15", False),
            _result("2018-06-15", False),
            _result("2019-06-15", False, quality="ambiguous", confidence=0.4),
            _result("2020-06-15", True, confidence=0.95),
        ]
    )
    encoded = encode_scan_state(state)
    assert encoded.obs_code_raw == ("0", "0", "u", "1")
    assert encoded.obs_code_repaired == encoded.obs_code_raw
    assert encoded.dip_repaired_dates == ()
    assert encoded.file_order_was_chronological is True


def _encoded_pair() -> EncodedSequence:
    return EncodedSequence(
        obs_dates=("2019-01-01", "2020-01-01"),
        obs_code_raw=("0", "1"),
        obs_code_repaired=("0", "1"),
        dip_repaired_dates=(),
        ref_dates=(),
        ref_code_raw=(),
        evidence=(),
        reference=(),
        file_order_was_chronological=True,
    )


def test_reconcile_matches_repaired_counts_and_bracket_days() -> None:
    report = reconcile_sequence(
        scan_status="done_appears",
        n_observations=2,
        n_absent=1,
        n_present=1,
        n_unusable=0,
        latest_absent_date="2019-01-01",
        earliest_present_date="2020-01-01",
        scan_notes="",
        encoded=_encoded_pair(),
    )
    assert report.issues == ()
    assert report.earliest_clamped is False


def test_reconcile_accepts_documented_flight_date_clamp() -> None:
    report = reconcile_sequence(
        scan_status="done_appears",
        n_observations=2,
        n_absent=1,
        n_present=1,
        n_unusable=0,
        latest_absent_date="2019-01-01",
        earliest_present_date="2024-03-15",
        scan_notes="present_clamped: raw earliest_present 2020-01-01 > vexcel flight date",
        encoded=_encoded_pair(),
    )
    assert report.issues == ()
    assert report.earliest_clamped is True


def test_reconcile_rejects_an_earliest_present_that_is_another_visit() -> None:
    report = reconcile_sequence(
        scan_status="done_appears",
        n_observations=2,
        n_absent=1,
        n_present=1,
        n_unusable=0,
        latest_absent_date="2019-01-01",
        earliest_present_date="2019-01-01",
        scan_notes="",
        encoded=_encoded_pair(),
    )
    assert "earliest_present" in report.issues


def test_census_bound_does_not_require_the_upper_bound_inside_the_sequence() -> None:
    report = reconcile_sequence(
        scan_status="done_installed_during_census",
        n_observations=2,
        n_absent=2,
        n_present=0,
        n_unusable=0,
        latest_absent_date="2020-01-01",
        earliest_present_date=CENSUS_CUTOFF,
        scan_notes="",
        encoded=EncodedSequence(
            obs_dates=("2019-01-01", "2020-01-01"),
            obs_code_raw=("0", "0"),
            obs_code_repaired=("0", "0"),
            dip_repaired_dates=(),
            ref_dates=(),
            ref_code_raw=(),
            evidence=(),
            reference=(),
            file_order_was_chronological=True,
        ),
    )
    assert report.issues == ()
    assert report.census_earliest_outside_sequence is True
