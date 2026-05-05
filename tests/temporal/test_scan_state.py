"""Unit tests for scan_state IO and scan_decision Task-A skeleton."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.temporal.scan_config import AdaptiveScanConfig
from scripts.temporal.scan_decision import (
    ExecuteRoundAction,
    TerminateAction,
    VintageEntry,
    decide_next_action,
    plan_initial_round,
    select_evenly_spaced_picks,
)
from scripts.temporal.scan_state import (
    Pick,
    Round,
    RoundResult,
    ScanState,
    create_scan_state,
    load_scan_state,
    save_scan_state,
    state_path_for,
)


@pytest.fixture
def tmp_state_dir(tmp_path: Path) -> Path:
    return tmp_path / "scan_states"


@pytest.fixture
def sample_anchor() -> dict[str, str]:
    return {
        "anchor_id": "johannesburg_G0922_a000005",
        "region_key": "johannesburg",
        "grid_id": "G0922",
    }


def test_create_and_save_scan_state_round_trip(tmp_state_dir: Path, sample_anchor: dict[str, str]) -> None:
    state = create_scan_state(sample_anchor)
    path = state_path_for(state.anchor_id, tmp_state_dir)
    save_scan_state(state, path)
    assert path.exists()
    loaded = load_scan_state(path)
    assert loaded is not None
    assert loaded.anchor_id == state.anchor_id
    assert loaded.status == "scanning"
    assert loaded.rounds == []
    assert loaded.spec_version == state.spec_version


def test_round_with_results_serializes(tmp_state_dir: Path, sample_anchor: dict[str, str]) -> None:
    state = create_scan_state(sample_anchor)
    rnd = Round(
        round_id=1,
        round_type="initial",
        window_start_date="2018-06-01",
        window_end_date="2024-09-01",
        picks=[Pick(chip_index=1, capture_date="2018-06-01", version=101, requested_zoom=19)],
        results=[
            RoundResult(
                chip_index=1,
                capture_date="2018-06-01",
                version=101,
                pv_present=False,
                confidence=0.92,
                quality_flag="usable",
                decision_source="dry_run_stub",
                evidence="no panels visible",
                chip_path="",
                actual_zoom=19,
            )
        ],
        completed=True,
    )
    state.rounds.append(rnd)
    state.status = "done_appears"
    path = state_path_for(state.anchor_id, tmp_state_dir)
    save_scan_state(state, path)

    loaded = load_scan_state(path)
    assert loaded is not None
    assert len(loaded.rounds) == 1
    assert loaded.rounds[0].results[0].pv_present is False
    assert loaded.is_terminal


def test_load_rejects_spec_mismatch(tmp_state_dir: Path, sample_anchor: dict[str, str]) -> None:
    state = create_scan_state(sample_anchor)
    path = state_path_for(state.anchor_id, tmp_state_dir)
    save_scan_state(state, path)
    raw = json.loads(path.read_text())
    raw["spec_version"] = "phase99_unknown"
    path.write_text(json.dumps(raw))
    with pytest.raises(ValueError, match="spec_version mismatch"):
        load_scan_state(path)


def test_invalid_status_rejected() -> None:
    with pytest.raises(ValueError, match="status must be one of"):
        ScanState(
            anchor_id="x",
            region_key="y",
            grid_id="z",
            status="not_a_real_status",
        )


def test_invalid_round_type_rejected() -> None:
    with pytest.raises(ValueError, match="round_type must be one of"):
        Round(
            round_id=1,
            round_type="not_a_real_type",
            window_start_date=None,
            window_end_date=None,
        )


def test_select_evenly_spaced_picks_anchors_endpoints() -> None:
    vintages = [VintageEntry(capture_date=f"2020-{m:02d}-01", version=v) for v, m in enumerate(range(1, 13), start=200)]
    picks = select_evenly_spaced_picks(vintages, target_count=5, requested_zoom=19)
    assert len(picks) == 5
    assert picks[0].capture_date == "2020-01-01"
    assert picks[-1].capture_date == "2020-12-01"
    assert [p.chip_index for p in picks] == [1, 2, 3, 4, 5]


def test_select_evenly_spaced_picks_returns_all_if_few() -> None:
    vintages = [VintageEntry(capture_date=f"2020-{m:02d}-01", version=200 + m) for m in (1, 6)]
    picks = select_evenly_spaced_picks(vintages, target_count=5, requested_zoom=19)
    assert len(picks) == 2
    assert [p.capture_date for p in picks] == ["2020-01-01", "2020-06-01"]


def test_plan_initial_round_uses_floor_year() -> None:
    config = AdaptiveScanConfig(round_1_floor_year=2018, picks_per_round=5)
    vintages = [VintageEntry(capture_date=f"{y}-06-01", version=y) for y in range(2010, 2025)]
    rnd = plan_initial_round(vintages, config)
    assert rnd.round_id == 1
    assert rnd.round_type == "initial"
    assert rnd.window_start_date == "2018-06-01"
    assert rnd.window_end_date == "2024-06-01"
    assert "degraded" not in rnd.notes


def test_plan_initial_round_degrades_when_no_floor_match() -> None:
    config = AdaptiveScanConfig(round_1_floor_year=2018, picks_per_round=5)
    vintages = [VintageEntry(capture_date=f"{y}-06-01", version=y) for y in range(2010, 2017)]
    rnd = plan_initial_round(vintages, config)
    assert "degraded" in rnd.notes
    assert rnd.window_end_date == "2016-06-01"


def test_decide_next_action_first_call_plans_initial(sample_anchor: dict[str, str]) -> None:
    config = AdaptiveScanConfig()
    state = create_scan_state(sample_anchor)
    vintages = [VintageEntry(capture_date=f"{y}-06-01", version=y) for y in range(2018, 2025)]
    action = decide_next_action(state, vintages, config)
    assert isinstance(action, ExecuteRoundAction)
    assert action.round.round_type == "initial"
    assert action.round.round_id == 1


def test_decide_next_action_terminates_after_round_in_task_a(sample_anchor: dict[str, str]) -> None:
    config = AdaptiveScanConfig()
    state = create_scan_state(sample_anchor)
    state.rounds.append(
        Round(round_id=1, round_type="initial", window_start_date=None, window_end_date=None, completed=True)
    )
    vintages = [VintageEntry(capture_date=f"{y}-06-01", version=y) for y in range(2018, 2025)]
    action = decide_next_action(state, vintages, config)
    assert isinstance(action, TerminateAction)
    assert action.status == "done_appears"


def test_initial_round_uses_primary_zoom_from_ladder() -> None:
    config = AdaptiveScanConfig(download_zoom_ladder=(20, 19))
    vintages = [VintageEntry(capture_date=f"{y}-06-01", version=y) for y in range(2018, 2025)]
    state = ScanState(anchor_id="a", region_key="r", grid_id="g")
    action = decide_next_action(state, vintages, config)
    assert isinstance(action, ExecuteRoundAction)
    assert all(p.requested_zoom == 20 for p in action.round.picks)


def test_orchestrator_failure_persists_terminal_state(
    tmp_state_dir: Path, sample_anchor: dict[str, str]
) -> None:
    from scripts.temporal.run_adaptive_scan import _record_orchestrator_failure

    state = _record_orchestrator_failure(
        sample_anchor, tmp_state_dir, RuntimeError("simulated GEHI timeout")
    )
    assert state.status == "done_ambiguous_orchestrator_error"
    assert state.is_terminal
    assert "RuntimeError" in state.notes
    loaded = load_scan_state(state_path_for(state.anchor_id, tmp_state_dir))
    assert loaded is not None
    assert loaded.status == "done_ambiguous_orchestrator_error"


def test_orchestrator_failure_preserves_existing_rounds(
    tmp_state_dir: Path, sample_anchor: dict[str, str]
) -> None:
    from scripts.temporal.run_adaptive_scan import _record_orchestrator_failure

    state = create_scan_state(sample_anchor)
    state.rounds.append(
        Round(
            round_id=1,
            round_type="initial",
            window_start_date="2018-06-01",
            window_end_date="2024-06-01",
            completed=True,
        )
    )
    save_scan_state(state, state_path_for(state.anchor_id, tmp_state_dir))

    failed = _record_orchestrator_failure(
        sample_anchor, tmp_state_dir, ValueError("bad bbox")
    )
    assert failed.status == "done_ambiguous_orchestrator_error"
    assert len(failed.rounds) == 1
    assert failed.rounds[0].round_id == 1
