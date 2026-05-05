"""Phase-0 install_intervals.csv inference tests.

Each test builds a synthetic ScanState with a specific status + observations,
runs `infer_one`, and asserts the resulting Phase0InstallInterval row.
"""

from __future__ import annotations

import csv
from datetime import date
from pathlib import Path

import pytest

from scripts.temporal.infer_install_dates import (
    OUTPUT_FIELDS,
    Phase0InstallInterval,
    infer_one,
    write_intervals,
)
from scripts.temporal.scan_state import (
    Pick,
    Round,
    RoundResult,
    ScanState,
    save_scan_state,
    state_path_for,
)


CENSUS_MID = date(2024, 6, 30)


def _result(capture_date: str, *, present: bool | None, quality: str = "usable", source: str = "gemini_batch") -> RoundResult:
    return RoundResult(
        chip_index=1,
        capture_date=capture_date,
        version=0,
        pv_present=present,
        confidence=0.9 if present is not None else None,
        quality_flag=quality,
        decision_source=source,
        actual_zoom=20,
    )


def _state_with(status: str, results: list[RoundResult], *, anchor_id: str = "a", notes: str = "") -> ScanState:
    state = ScanState(anchor_id=anchor_id, region_key="r", grid_id="g")
    state.status = status
    state.notes = notes
    state.rounds = [
        Round(
            round_id=1,
            round_type="initial",
            window_start_date=results[0].capture_date if results else None,
            window_end_date=results[-1].capture_date if results else None,
            picks=[
                Pick(chip_index=i + 1, capture_date=r.capture_date, version=0, requested_zoom=20)
                for i, r in enumerate(results)
            ],
            results=results,
            completed=True,
        )
    ]
    return state


def test_done_appears_high_confidence_short_gap() -> None:
    state = _state_with(
        "done_appears",
        [
            _result("2020-04-15", present=False),
            _result("2020-08-15", present=True),
        ],
    )
    interval = infer_one(state, census_mid_date=CENSUS_MID, scan_state_path=Path("/x.json"))
    assert interval.status == "done_appears"
    assert interval.latest_absent_date == "2020-04-15"
    assert interval.earliest_present_date == "2020-08-15"
    assert interval.install_interval_start == "2020-04-15"
    assert interval.install_interval_end == "2020-08-15"
    assert interval.install_mid_estimate == "2020-06-15"
    assert interval.confidence == "high"


def test_done_appears_medium_confidence_gap_under_two_years() -> None:
    state = _state_with(
        "done_appears",
        [
            _result("2019-01-01", present=False),
            _result("2020-12-01", present=True),
        ],
    )
    interval = infer_one(state, census_mid_date=CENSUS_MID, scan_state_path=Path("/x.json"))
    assert interval.confidence == "medium"


def test_done_appears_low_confidence_long_gap() -> None:
    state = _state_with(
        "done_appears",
        [
            _result("2014-01-01", present=False),
            _result("2024-01-01", present=True),
        ],
    )
    interval = infer_one(state, census_mid_date=CENSUS_MID, scan_state_path=Path("/x.json"))
    assert interval.confidence == "low"


def test_done_appears_picks_latest_absent_and_earliest_present() -> None:
    state = _state_with(
        "done_appears",
        [
            _result("2018-06-15", present=False),
            _result("2020-06-15", present=False),
            _result("2022-06-15", present=False),
            _result("2023-06-15", present=True),
            _result("2024-06-15", present=True),
        ],
    )
    interval = infer_one(state, census_mid_date=CENSUS_MID, scan_state_path=Path("/x.json"))
    assert interval.latest_absent_date == "2022-06-15"
    assert interval.earliest_present_date == "2023-06-15"
    # 365-day gap is in the medium band (high requires <= 6 months ≈ 183 days)
    assert interval.confidence == "medium"


def test_done_installed_during_census_uses_census_upper_bound() -> None:
    state = _state_with(
        "done_installed_during_census",
        [
            _result("2018-06-15", present=False),
            _result("2024-03-01", present=False),
        ],
    )
    interval = infer_one(state, census_mid_date=CENSUS_MID, scan_state_path=Path("/x.json"))
    assert interval.status == "done_installed_during_census"
    assert interval.latest_absent_date == "2024-03-01"
    assert interval.earliest_present_date == ""
    assert interval.install_interval_start == "2024-03-01"
    assert interval.install_interval_end == "2024-06-30"
    assert interval.install_mid_estimate == "2024-06-30"
    assert interval.confidence == "high"


def test_done_appears_inverted_interval_flagged() -> None:
    """Defensive: corrupt scan_state where a present observation predates an absent."""
    state = _state_with(
        "done_appears",
        [
            _result("2022-06-15", present=True),
            _result("2024-01-15", present=False),
        ],
    )
    interval = infer_one(state, census_mid_date=CENSUS_MID, scan_state_path=Path("/x.json"))
    assert interval.confidence == "low"
    assert "inverted_interval" in interval.notes
    assert interval.install_interval_start == ""
    assert interval.install_interval_end == ""
    assert interval.install_mid_estimate == ""
    assert interval.latest_absent_date == "2024-01-15"
    assert interval.earliest_present_date == "2022-06-15"


def test_done_installed_during_census_inverted_interval_flagged() -> None:
    """Defensive: latest_absent_date past census_mid_date downgrades to low + flags notes."""
    state = _state_with(
        "done_installed_during_census",
        [_result("2024-11-01", present=False)],
    )
    interval = infer_one(state, census_mid_date=CENSUS_MID, scan_state_path=Path("/x.json"))
    assert interval.confidence == "low"
    assert "inverted_interval" in interval.notes


def test_done_installed_during_census_medium_when_old_absent() -> None:
    state = _state_with(
        "done_installed_during_census",
        [_result("2018-06-15", present=False)],
    )
    interval = infer_one(state, census_mid_date=CENSUS_MID, scan_state_path=Path("/x.json"))
    assert interval.confidence == "medium"


def test_done_already_present_open_lower_bound() -> None:
    state = _state_with(
        "done_already_present_before_geid_history",
        [
            _result("2009-04-15", present=True),
            _result("2014-06-15", present=True),
            _result("2018-06-15", present=True),
        ],
    )
    interval = infer_one(state, census_mid_date=CENSUS_MID, scan_state_path=Path("/x.json"))
    assert interval.status == "done_already_present_before_geid_history"
    assert interval.latest_absent_date == ""
    assert interval.earliest_present_date == "2009-04-15"
    assert interval.install_interval_start == ""
    assert interval.install_interval_end == "2009-04-15"
    assert interval.install_mid_estimate == ""
    assert interval.confidence == "low"


def test_done_ambiguous_nonmonotonic_no_interval() -> None:
    state = _state_with(
        "done_ambiguous_nonmonotonic",
        [
            _result("2018-06-15", present=False),
            _result("2020-06-15", present=True),
            _result("2022-06-15", present=False),
        ],
        notes="present->absent step detected",
    )
    interval = infer_one(state, census_mid_date=CENSUS_MID, scan_state_path=Path("/x.json"))
    assert interval.install_interval_start == ""
    assert interval.install_interval_end == ""
    assert interval.confidence == "low"
    assert "present->absent" in interval.notes


def test_done_ambiguous_gemini_failed_no_interval() -> None:
    state = _state_with(
        "done_ambiguous_gemini_failed",
        [_result("2018-06-15", present=None, quality="unusable", source="gemini_failed")],
        notes="failure_pct=66 > threshold=50",
    )
    interval = infer_one(state, census_mid_date=CENSUS_MID, scan_state_path=Path("/x.json"))
    assert interval.install_interval_start == ""
    assert interval.install_interval_end == ""
    assert interval.confidence == "low"
    assert interval.n_unusable >= 1
    assert "failure_pct" in interval.notes


def test_scanning_in_progress_marked_in_notes() -> None:
    state = _state_with(
        "scanning",
        [_result("2020-06-15", present=False)],
    )
    interval = infer_one(state, census_mid_date=CENSUS_MID, scan_state_path=Path("/x.json"))
    assert interval.status == "scanning"
    assert "scan_in_progress" in interval.notes
    assert interval.confidence == "low"


def test_observation_counters_correct() -> None:
    state = _state_with(
        "done_appears",
        [
            _result("2018-06-15", present=False),
            _result("2020-06-15", present=False),
            _result("2022-06-15", present=True),
            _result("2023-06-15", present=None, quality="unusable", source="gemini_failed"),
            _result("2024-06-15", present=True),
        ],
    )
    interval = infer_one(state, census_mid_date=CENSUS_MID, scan_state_path=Path("/x.json"))
    assert interval.n_observations == 5
    assert interval.n_absent == 2
    assert interval.n_present == 2
    assert interval.n_unusable == 1
    assert interval.n_rounds == 1


def test_inconsistent_done_appears_without_transition_falls_to_low() -> None:
    """Defensive: status says done_appears but observations don't include both absent and present."""
    state = _state_with(
        "done_appears",
        [_result("2020-06-15", present=True)],
    )
    interval = infer_one(state, census_mid_date=CENSUS_MID, scan_state_path=Path("/x.json"))
    assert interval.confidence == "low"
    assert "inconsistent" in interval.notes


def test_write_intervals_round_trip(tmp_path: Path) -> None:
    state = _state_with(
        "done_appears",
        [_result("2020-04-15", present=False), _result("2020-08-15", present=True)],
    )
    interval = infer_one(state, census_mid_date=CENSUS_MID, scan_state_path=Path("/x.json"))
    out_path = tmp_path / "install_intervals.csv"
    write_intervals([interval], out_path)
    with out_path.open("r", newline="") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == 1
    row = rows[0]
    assert set(row.keys()) == set(OUTPUT_FIELDS)
    assert row["install_interval_start"] == "2020-04-15"
    assert row["install_mid_estimate"] == "2020-06-15"
    assert row["confidence"] == "high"


def test_cli_default_output_derives_from_scan_states_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Without --output, CSV must land beside the scan_states dir, not in the JHB smoke path."""
    states_dir = tmp_path / "custom_run" / "scan_states"
    states_dir.mkdir(parents=True)
    s1 = _state_with(
        "done_appears",
        [_result("2020-04-15", present=False), _result("2020-08-15", present=True)],
        anchor_id="a000001",
    )
    save_scan_state(s1, state_path_for(s1.anchor_id, states_dir))

    monkeypatch.setattr(
        "sys.argv",
        [
            "infer_install_dates.py",
            "--scan-states-dir", str(states_dir),
            "--census-mid-date", "2024-06-30",
        ],
    )
    from scripts.temporal.infer_install_dates import main as infer_main
    infer_main()

    expected_output = tmp_path / "custom_run" / "install_intervals.csv"
    assert expected_output.exists()
    with expected_output.open("r", newline="") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == 1
    assert rows[0]["anchor_id"] == "a000001"


def test_cli_aborts_on_load_failure_by_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A corrupt scan_state.json must abort the run instead of producing a partial CSV."""
    states_dir = tmp_path / "scan_states"
    states_dir.mkdir()
    good = _state_with(
        "done_appears",
        [_result("2020-04-15", present=False), _result("2020-08-15", present=True)],
        anchor_id="a000001",
    )
    save_scan_state(good, state_path_for(good.anchor_id, states_dir))
    (states_dir / "a000002.json").write_text("{not valid json", encoding="utf-8")

    output_path = tmp_path / "install_intervals.csv"
    monkeypatch.setattr(
        "sys.argv",
        [
            "infer_install_dates.py",
            "--scan-states-dir", str(states_dir),
            "--output", str(output_path),
            "--census-mid-date", "2024-06-30",
        ],
    )
    from scripts.temporal.infer_install_dates import main as infer_main
    with pytest.raises(SystemExit) as exc_info:
        infer_main()
    assert "failed to load" in str(exc_info.value)
    assert not output_path.exists()


def test_cli_allow_load_failures_continues(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """--allow-load-failures lets the run finish with a partial CSV."""
    states_dir = tmp_path / "scan_states"
    states_dir.mkdir()
    good = _state_with(
        "done_appears",
        [_result("2020-04-15", present=False), _result("2020-08-15", present=True)],
        anchor_id="a000001",
    )
    save_scan_state(good, state_path_for(good.anchor_id, states_dir))
    (states_dir / "a000002.json").write_text("{not valid json", encoding="utf-8")

    output_path = tmp_path / "install_intervals.csv"
    monkeypatch.setattr(
        "sys.argv",
        [
            "infer_install_dates.py",
            "--scan-states-dir", str(states_dir),
            "--output", str(output_path),
            "--census-mid-date", "2024-06-30",
            "--allow-load-failures",
        ],
    )
    from scripts.temporal.infer_install_dates import main as infer_main
    infer_main()
    assert output_path.exists()
    with output_path.open("r", newline="") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == 1


def test_cli_walks_scan_states_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """End-to-end: write 2 scan_state.json files, run main(), verify csv output."""
    states_dir = tmp_path / "scan_states"
    states_dir.mkdir()

    state1 = _state_with("done_appears", [_result("2020-04-15", present=False), _result("2020-08-15", present=True)], anchor_id="a000001")
    state2 = _state_with("done_installed_during_census", [_result("2024-03-01", present=False)], anchor_id="a000002")
    save_scan_state(state1, state_path_for(state1.anchor_id, states_dir))
    save_scan_state(state2, state_path_for(state2.anchor_id, states_dir))

    output_path = tmp_path / "install_intervals.csv"
    args = [
        "infer_install_dates.py",
        "--scan-states-dir", str(states_dir),
        "--output", str(output_path),
        "--census-mid-date", "2024-06-30",
    ]
    monkeypatch.setattr("sys.argv", args)

    from scripts.temporal.infer_install_dates import main as infer_main
    infer_main()

    assert output_path.exists()
    with output_path.open("r", newline="") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == 2
    by_anchor = {r["anchor_id"]: r for r in rows}
    assert by_anchor["a000001"]["status"] == "done_appears"
    assert by_anchor["a000002"]["status"] == "done_installed_during_census"
    assert by_anchor["a000002"]["install_interval_end"] == "2024-06-30"
