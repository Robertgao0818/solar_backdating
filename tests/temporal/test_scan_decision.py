"""Case A-E coverage for `decide_next_action`.

Each test sets up a `ScanState` with a specific round/results history and asserts
the next action. No GEHI / Gemini calls — pure decision logic.
"""

from __future__ import annotations

from typing import Iterable

import pytest

from scripts.temporal.scan_config import AdaptiveScanConfig
from scripts.temporal.scan_decision import (
    ExecuteRoundAction,
    TerminateAction,
    VintageEntry,
    decide_next_action,
    find_transitions,
    is_nonmonotonic,
)
from scripts.temporal.scan_state import (
    Pick,
    Round,
    RoundResult,
    ScanState,
)


def _vintages_yearly(start: int, end: int, *, month: int = 6, day: int = 15) -> list[VintageEntry]:
    return [
        VintageEntry(capture_date=f"{y}-{month:02d}-{day:02d}", version=y)
        for y in range(start, end + 1)
    ]


def _vintages_monthly(start_year: int, start_month: int, end_year: int, end_month: int, *, day: int = 1) -> list[VintageEntry]:
    out: list[VintageEntry] = []
    y, m = start_year, start_month
    version_seed = 1000
    while (y, m) <= (end_year, end_month):
        out.append(VintageEntry(capture_date=f"{y}-{m:02d}-{day:02d}", version=version_seed))
        version_seed += 1
        m += 1
        if m > 12:
            m = 1
            y += 1
    return out


def _result(capture_date: str, *, present: bool | None, version: int = 0, quality: str = "usable", source: str = "gemini_batch") -> RoundResult:
    return RoundResult(
        chip_index=1,
        capture_date=capture_date,
        version=version,
        pv_present=present,
        confidence=0.9,
        quality_flag=quality,
        decision_source=source,
        actual_zoom=20,
    )


def _round(round_id: int, round_type: str, *, results: Iterable[RoundResult], window_start: str | None, window_end: str | None) -> Round:
    rs = list(results)
    return Round(
        round_id=round_id,
        round_type=round_type,
        window_start_date=window_start,
        window_end_date=window_end,
        picks=[Pick(chip_index=i + 1, capture_date=r.capture_date, version=r.version, requested_zoom=20) for i, r in enumerate(rs)],
        results=rs,
        completed=True,
    )


@pytest.fixture
def state_factory():
    def _make(rounds: list[Round]) -> ScanState:
        s = ScanState(anchor_id="a", region_key="r", grid_id="g")
        s.rounds = list(rounds)
        return s
    return _make


@pytest.fixture
def config() -> AdaptiveScanConfig:
    return AdaptiveScanConfig()


def test_helper_find_transitions_single_pair() -> None:
    obs = [
        _result("2018-06-15", present=False),
        _result("2020-06-15", present=False),
        _result("2022-06-15", present=True),
        _result("2024-06-15", present=True),
    ]
    transitions = find_transitions(obs)
    assert len(transitions) == 1
    assert transitions[0][0].capture_date == "2020-06-15"
    assert transitions[0][1].capture_date == "2022-06-15"


def test_helper_is_nonmonotonic_detects_present_then_absent() -> None:
    obs = [
        _result("2018-06-15", present=False),
        _result("2020-06-15", present=True),
        _result("2022-06-15", present=False),
        _result("2024-06-15", present=True),
    ]
    assert is_nonmonotonic(obs)


def test_helper_is_nonmonotonic_clean_sequence() -> None:
    obs = [
        _result("2018-06-15", present=False),
        _result("2020-06-15", present=True),
        _result("2024-06-15", present=True),
    ]
    assert not is_nonmonotonic(obs)


def test_case_a_round1_transition_triggers_bisection(state_factory, config) -> None:
    vintages = _vintages_yearly(2018, 2024) + _vintages_monthly(2020, 7, 2021, 5)
    r1 = _round(
        1,
        "initial",
        results=[
            _result("2018-06-15", present=False),
            _result("2020-06-15", present=False),
            _result("2022-06-15", present=True),
            _result("2024-06-15", present=True),
        ],
        window_start="2018-06-15",
        window_end="2024-06-15",
    )
    state = state_factory([r1])
    action = decide_next_action(state, vintages, config)
    assert isinstance(action, ExecuteRoundAction)
    assert action.round.round_type == "bisection"
    pick_dates = [p.capture_date for p in action.round.picks]
    for d in pick_dates:
        assert "2020-06-15" < d < "2022-06-15"


def test_case_a_no_monthly_vintages_terminates_appears(state_factory, config) -> None:
    vintages = [
        VintageEntry(capture_date=d, version=v)
        for v, d in enumerate(("2018-06-15", "2020-06-15", "2022-06-15", "2024-06-15"), start=1)
    ]
    r1 = _round(
        1,
        "initial",
        results=[
            _result("2018-06-15", present=False),
            _result("2020-06-15", present=False),
            _result("2022-06-15", present=True),
            _result("2024-06-15", present=True),
        ],
        window_start="2018-06-15",
        window_end="2024-06-15",
    )
    state = state_factory([r1])
    action = decide_next_action(state, vintages, config)
    assert isinstance(action, TerminateAction)
    assert action.status == "done_appears"


def test_case_a_after_bisection_terminates(state_factory, config) -> None:
    vintages = _vintages_yearly(2018, 2024) + _vintages_monthly(2020, 7, 2021, 5)
    r1 = _round(
        1,
        "initial",
        results=[
            _result("2018-06-15", present=False),
            _result("2020-06-15", present=False),
            _result("2022-06-15", present=True),
            _result("2024-06-15", present=True),
        ],
        window_start="2018-06-15",
        window_end="2024-06-15",
    )
    r2 = _round(
        2,
        "bisection",
        results=[
            _result("2020-12-01", present=False),
            _result("2021-04-01", present=True),
        ],
        window_start="2020-06-15",
        window_end="2022-06-15",
    )
    state = state_factory([r1, r2])
    action = decide_next_action(state, vintages, config)
    assert isinstance(action, TerminateAction)
    assert action.status == "done_appears"


def test_case_b_all_present_walks_back(state_factory, config) -> None:
    vintages = _vintages_yearly(2009, 2024)
    r1 = _round(
        1,
        "initial",
        results=[
            _result("2018-06-15", present=True),
            _result("2020-06-15", present=True),
            _result("2022-06-15", present=True),
            _result("2024-06-15", present=True),
        ],
        window_start="2018-06-15",
        window_end="2024-06-15",
    )
    state = state_factory([r1])
    action = decide_next_action(state, vintages, config)
    assert isinstance(action, ExecuteRoundAction)
    assert action.round.round_type == "walk_back"
    assert action.round.window_end_date == "2018-06-15"
    pick_dates = [p.capture_date for p in action.round.picks]
    for d in pick_dates:
        assert d < "2018-06-15"


def test_case_b_walk_back_finds_transition_triggers_bisection(state_factory, config) -> None:
    vintages = _vintages_yearly(2009, 2024) + _vintages_monthly(2014, 7, 2015, 5)
    r1 = _round(
        1,
        "initial",
        results=[_result(f"{y}-06-15", present=True) for y in (2018, 2020, 2022, 2024)],
        window_start="2018-06-15",
        window_end="2024-06-15",
    )
    r2 = _round(
        2,
        "walk_back",
        results=[
            _result("2013-06-15", present=False),
            _result("2014-06-15", present=False),
            _result("2015-06-15", present=True),
            _result("2016-06-15", present=True),
            _result("2017-06-15", present=True),
        ],
        window_start="2013-06-15",
        window_end="2018-06-15",
    )
    state = state_factory([r1, r2])
    action = decide_next_action(state, vintages, config)
    assert isinstance(action, ExecuteRoundAction)
    assert action.round.round_type == "bisection"


def test_case_b_walk_back_to_tail(state_factory, config) -> None:
    """When next walk-back candidate count < tail_round_threshold, promote to tail."""
    vintages = _vintages_yearly(2011, 2024)
    r1 = _round(
        1,
        "initial",
        results=[_result(f"{y}-06-15", present=True) for y in (2018, 2020, 2022, 2024)],
        window_start="2018-06-15",
        window_end="2024-06-15",
    )
    r2 = _round(
        2,
        "walk_back",
        results=[_result(f"{y}-06-15", present=True) for y in (2013, 2014, 2015, 2016, 2017)],
        window_start="2013-06-15",
        window_end="2018-06-15",
    )
    state = state_factory([r1, r2])
    action = decide_next_action(state, vintages, config)
    assert isinstance(action, ExecuteRoundAction)
    assert action.round.round_type == "tail"
    pick_dates = [p.capture_date for p in action.round.picks]
    assert pick_dates == ["2011-06-15", "2012-06-15"]


def test_tail_all_present_terminates_already_present(state_factory, config) -> None:
    vintages = _vintages_yearly(2010, 2024)
    r1 = _round(
        1,
        "initial",
        results=[_result(f"{y}-06-15", present=True) for y in (2018, 2020, 2022, 2024)],
        window_start="2018-06-15",
        window_end="2024-06-15",
    )
    r2 = _round(
        2,
        "walk_back",
        results=[_result(f"{y}-06-15", present=True) for y in (2013, 2014, 2015, 2016, 2017)],
        window_start="2013-06-15",
        window_end="2018-06-15",
    )
    r3 = _round(
        3,
        "tail",
        results=[_result(f"{y}-06-15", present=True) for y in (2010, 2011, 2012)],
        window_start="2010-06-15",
        window_end="2013-06-15",
    )
    state = state_factory([r1, r2, r3])
    action = decide_next_action(state, vintages, config)
    assert isinstance(action, TerminateAction)
    assert action.status == "done_already_present_before_geid_history"


def test_case_c_all_absent_latest_usable_installed_during_census(state_factory, config) -> None:
    vintages = _vintages_yearly(2018, 2024)
    r1 = _round(
        1,
        "initial",
        results=[
            _result("2018-06-15", present=False),
            _result("2020-06-15", present=False),
            _result("2022-06-15", present=False),
            _result("2024-06-15", present=False),
        ],
        window_start="2018-06-15",
        window_end="2024-06-15",
    )
    state = state_factory([r1])
    action = decide_next_action(state, vintages, config)
    assert isinstance(action, TerminateAction)
    assert action.status == "done_installed_during_census"


def test_case_c_blocked_when_latest_unusable_triggers_recovery(state_factory, config) -> None:
    vintages = _vintages_yearly(2018, 2024)
    r1 = _round(
        1,
        "initial",
        results=[
            _result("2018-06-15", present=False),
            _result("2020-06-15", present=False),
            _result("2022-06-15", present=False),
            _result("2024-06-15", present=None, quality="unusable"),
        ],
        window_start="2018-06-15",
        window_end="2024-06-15",
    )
    state = state_factory([r1])
    action = decide_next_action(state, vintages, config)
    assert isinstance(action, ExecuteRoundAction)
    assert action.round.round_type == "anchor_recovery"


def test_recovery_exhausted_terminates_no_recent_anchor(state_factory) -> None:
    config = AdaptiveScanConfig(max_anchor_recovery_rounds=2)
    vintages = _vintages_yearly(2018, 2024)
    r1 = _round(
        1,
        "initial",
        results=[
            _result(f"{y}-06-15", present=False)
            for y in (2018, 2020, 2022)
        ] + [_result("2024-06-15", present=None, quality="unusable")],
        window_start="2018-06-15",
        window_end="2024-06-15",
    )
    rec1 = _round(
        2, "anchor_recovery",
        results=[_result("2023-06-15", present=None, quality="unusable")],
        window_start="2023-06-15", window_end="2023-06-15",
    )
    rec2 = _round(
        3, "anchor_recovery",
        results=[_result("2021-06-15", present=None, quality="unusable")],
        window_start="2021-06-15", window_end="2021-06-15",
    )
    state = state_factory([r1, rec1, rec2])
    action = decide_next_action(state, vintages, config)
    assert isinstance(action, TerminateAction)
    assert action.status == "done_ambiguous_no_recent_anchor"


def test_case_d_nonmonotonic_terminates(state_factory, config) -> None:
    vintages = _vintages_yearly(2018, 2024)
    r1 = _round(
        1,
        "initial",
        results=[
            _result("2018-06-15", present=False),
            _result("2020-06-15", present=True),
            _result("2022-06-15", present=False),
            _result("2024-06-15", present=True),
        ],
        window_start="2018-06-15",
        window_end="2024-06-15",
    )
    state = state_factory([r1])
    action = decide_next_action(state, vintages, config)
    assert isinstance(action, TerminateAction)
    assert action.status == "done_ambiguous_nonmonotonic"


def test_case_e_failure_pct_terminates(state_factory) -> None:
    config = AdaptiveScanConfig(case_e_failure_pct=50.0)
    vintages = _vintages_yearly(2018, 2024)
    r1 = _round(
        1,
        "initial",
        results=[
            _result("2018-06-15", present=None, quality="unusable", source="gemini_failed"),
            _result("2020-06-15", present=None, quality="unusable", source="gemini_failed"),
            _result("2022-06-15", present=None, quality="unusable", source="gemini_failed"),
            _result("2024-06-15", present=False, quality="usable"),
        ],
        window_start="2018-06-15",
        window_end="2024-06-15",
    )
    state = state_factory([r1])
    action = decide_next_action(state, vintages, config)
    assert isinstance(action, TerminateAction)
    assert action.status == "done_ambiguous_gemini_failed"


def test_no_usable_obs_triggers_anchor_recovery(state_factory, config) -> None:
    vintages = _vintages_yearly(2018, 2024)
    r1 = _round(
        1,
        "initial",
        results=[
            _result(f"{y}-06-15", present=None, quality="unusable") for y in (2018, 2020, 2022, 2024)
        ],
        window_start="2018-06-15",
        window_end="2024-06-15",
    )
    state = state_factory([r1])
    action = decide_next_action(state, vintages, config)
    assert isinstance(action, ExecuteRoundAction)
    assert action.round.round_type == "anchor_recovery"


def test_terminal_state_returns_terminate(state_factory, config) -> None:
    vintages = _vintages_yearly(2018, 2024)
    state = state_factory([])
    state.status = "done_appears"
    action = decide_next_action(state, vintages, config)
    assert isinstance(action, TerminateAction)
    assert action.status == "done_appears"


def test_recovery_round_picks_next_unscored_vintage_descending(state_factory, config) -> None:
    vintages = _vintages_yearly(2018, 2024)
    r1 = _round(
        1,
        "initial",
        results=[
            _result("2018-06-15", present=False),
            _result("2020-06-15", present=False),
            _result("2022-06-15", present=False),
            _result("2024-06-15", present=None, quality="unusable"),
        ],
        window_start="2018-06-15",
        window_end="2024-06-15",
    )
    state = state_factory([r1])
    action = decide_next_action(state, vintages, config)
    assert isinstance(action, ExecuteRoundAction)
    assert action.round.round_type == "anchor_recovery"
    assert action.round.picks[0].capture_date == "2023-06-15"


def test_walk_back_excludes_shared_boundary_from_picks(state_factory, config) -> None:
    vintages = _vintages_yearly(2009, 2024)
    r1 = _round(
        1,
        "initial",
        results=[_result(f"{y}-06-15", present=True) for y in (2018, 2020, 2022, 2024)],
        window_start="2018-06-15",
        window_end="2024-06-15",
    )
    state = state_factory([r1])
    action = decide_next_action(state, vintages, config)
    assert isinstance(action, ExecuteRoundAction)
    assert action.round.round_type == "walk_back"
    pick_dates = [p.capture_date for p in action.round.picks]
    assert "2018-06-15" not in pick_dates
