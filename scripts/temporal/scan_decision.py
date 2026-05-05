"""Round-decision pure functions for the adaptive PV-presence scan.

Task A skeleton: only `plan_initial_round` returns realistic picks. The other
decision branches are TODO stubs that terminate the scan after Round 1 with a
placeholder `done_appears` status. Task B replaces the stubs with the full
case A-E logic locked in spec.

The full design is documented in docs/gehi_temporal_replacement_plan.md and
docs/geid_temporal_anchor_presence_architecture.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal

from scripts.temporal.scan_config import AdaptiveScanConfig
from scripts.temporal.scan_state import Pick, Round, ScanState, next_round_id


@dataclass(frozen=True)
class VintageEntry:
    capture_date: str
    version: int


@dataclass(frozen=True)
class ExecuteRoundAction:
    kind: Literal["execute_round"]
    round: Round


@dataclass(frozen=True)
class TerminateAction:
    kind: Literal["terminate"]
    status: str
    notes: str = ""


Action = ExecuteRoundAction | TerminateAction


def parse_iso(value: str) -> date:
    return datetime.strptime(value[:10], "%Y-%m-%d").date()


def select_evenly_spaced_picks(
    vintages: list[VintageEntry],
    *,
    target_count: int,
    requested_zoom: int,
) -> list[Pick]:
    """Return up to `target_count` picks anchored at two ends with middle evenly spaced.

    `vintages` must be sorted ascending by capture_date. Picks preserve original
    chronological order with chip_index 1..N reset for the round.
    """
    if not vintages:
        return []
    if len(vintages) <= target_count:
        chosen = list(vintages)
    else:
        n = len(vintages)
        k = target_count - 2
        indices = [0]
        if k > 0:
            for i in range(1, k + 1):
                idx = round(i * (n - 1) / (k + 1))
                indices.append(idx)
        indices.append(n - 1)
        seen: set[int] = set()
        unique_indices: list[int] = []
        for idx in indices:
            if idx not in seen:
                seen.add(idx)
                unique_indices.append(idx)
        unique_indices.sort()
        chosen = [vintages[i] for i in unique_indices]
    return [
        Pick(chip_index=i + 1, capture_date=v.capture_date, version=v.version, requested_zoom=requested_zoom)
        for i, v in enumerate(chosen)
    ]


def plan_initial_round(
    vintages: list[VintageEntry],
    config: AdaptiveScanConfig,
) -> Round:
    """Round 1 picks = anchor [floor_year nearest, latest] + middle evenly spaced.

    If no vintage has year >= floor_year, fall back to the earliest available and
    let the orchestrator note the degradation. The returned Round carries the
    picks but no results.
    """
    if not vintages:
        raise ValueError("Cannot plan initial round: vintage list is empty")
    sorted_vintages = sorted(vintages, key=lambda v: v.capture_date)
    floor_year = config.round_1_floor_year
    in_window = [v for v in sorted_vintages if parse_iso(v.capture_date).year >= floor_year]
    notes = ""
    if in_window:
        window = in_window
    else:
        window = sorted_vintages
        notes = f"round1_floor_degraded=true (no vintage >= {floor_year})"
    picks = select_evenly_spaced_picks(
        window, target_count=config.picks_per_round, requested_zoom=config.download_zoom_ladder[0]
    )
    return Round(
        round_id=1,
        round_type="initial",
        window_start_date=picks[0].capture_date if picks else None,
        window_end_date=picks[-1].capture_date if picks else None,
        picks=picks,
        notes=notes,
    )


def decide_next_action(
    state: ScanState,
    vintages: list[VintageEntry],
    config: AdaptiveScanConfig,
) -> Action:
    """Return the next action for the orchestrator to execute.

    Task A behavior: only handles "no rounds yet → execute initial round". Any
    state with at least one round terminates with `done_appears`. Task B
    replaces this with full case A/B/C/D/E logic.
    """
    if state.is_terminal:
        return TerminateAction(kind="terminate", status=state.status)
    if not state.rounds:
        rnd = plan_initial_round(vintages, config)
        rnd.round_id = next_round_id(state)
        return ExecuteRoundAction(kind="execute_round", round=rnd)
    return TerminateAction(
        kind="terminate",
        status="done_appears",
        notes="task_a_stub: terminating after round 1 unconditionally",
    )
