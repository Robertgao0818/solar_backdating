"""Round-decision pure functions for the adaptive PV-presence scan.

Drives `run_adaptive_scan.py`'s state machine. Given a `ScanState` and the
GEHI vintage list for an anchor, returns either an `ExecuteRoundAction` (with
the next Round to run) or a `TerminateAction` (with the final status).

Case mapping (locked in Phase-0 spec, see docs/gehi_temporal_replacement_plan.md
and the grilling thread):

  A  transition (absent->present) found  -> bisection round, then `done_appears`
  B  all observations present            -> walk-back round (or tail, then
                                            `done_already_present_before_geid_history`)
  C  all observations absent + latest_avail vintage usable absent
                                         -> `done_installed_during_census`
  D  multi-transition (present->absent)  -> `done_ambiguous_nonmonotonic`
  E  >50% gemini_failed across rounds    -> `done_ambiguous_gemini_failed`
  R  latest unusable / no usable obs     -> anchor_recovery round (capped),
                                            then `done_ambiguous_no_recent_anchor`

Bisection uses the open interval `(A.date, P.date)`; merged endpoints come from
prior round results.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Iterable, Literal

from scripts.temporal.scan_config import AdaptiveScanConfig
from scripts.temporal.scan_state import (
    Pick,
    Round,
    RoundResult,
    ScanState,
    next_round_id,
)


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


def collect_all_results(rounds: Iterable[Round]) -> list[RoundResult]:
    return [r for rnd in rounds for r in rnd.results]


def usable_observations(results: Iterable[RoundResult]) -> list[RoundResult]:
    return [r for r in results if r.quality_flag == "usable" and r.pv_present is not None]


def failure_pct(results: list[RoundResult]) -> float:
    if not results:
        return 0.0
    failed = sum(1 for r in results if r.decision_source == "gemini_failed")
    return 100.0 * failed / len(results)


def is_nonmonotonic(usable: list[RoundResult]) -> bool:
    """True if a present observation is followed (chronologically) by an absent one.

    Order by `capture_date` ascending; any present->absent step is a multi-transition
    signal because real PV installations are monotonically present once installed.
    """
    sorted_obs = sorted(usable, key=lambda o: o.capture_date)
    for i in range(len(sorted_obs) - 1):
        if sorted_obs[i].pv_present and not sorted_obs[i + 1].pv_present:
            return True
    return False


def find_transitions(usable: list[RoundResult]) -> list[tuple[RoundResult, RoundResult]]:
    """Return absent->present pairs in chronological order. At most one expected if monotonic."""
    sorted_obs = sorted(usable, key=lambda o: o.capture_date)
    transitions: list[tuple[RoundResult, RoundResult]] = []
    for i in range(len(sorted_obs) - 1):
        if not sorted_obs[i].pv_present and sorted_obs[i + 1].pv_present:
            transitions.append((sorted_obs[i], sorted_obs[i + 1]))
    return transitions


def latest_vintage_date(vintages: list[VintageEntry]) -> str:
    return max(v.capture_date for v in vintages)


def _scored_dates(results: Iterable[RoundResult]) -> set[str]:
    return {r.capture_date for r in results}


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


def plan_walk_back_round(
    vintages: list[VintageEntry],
    prev_round: Round,
    all_results: list[RoundResult],
    config: AdaptiveScanConfig,
    round_id: int,
) -> Round | None:
    """Walk-back round: 5 picks evenly from unscored vintages older than `prev_round.window_start_date`.

    Window: `[window_start_year - walk_back_years, prev_round.window_start_date)` open at top
    (the shared boundary is already scored and is merged into the timeline, not
    re-scored). Returns None if no candidates available — caller should handle.
    """
    if prev_round.window_start_date is None:
        return None
    boundary = prev_round.window_start_date
    boundary_year = parse_iso(boundary).year
    next_start_year = boundary_year - config.walk_back_years
    scored = _scored_dates(all_results)
    candidates = [
        v
        for v in sorted(vintages, key=lambda v: v.capture_date)
        if v.capture_date < boundary
        and v.capture_date not in scored
        and parse_iso(v.capture_date).year >= next_start_year
    ]
    if not candidates:
        return None
    picks = select_evenly_spaced_picks(
        candidates, target_count=config.picks_per_round, requested_zoom=config.download_zoom_ladder[0]
    )
    return Round(
        round_id=round_id,
        round_type="walk_back",
        window_start_date=picks[0].capture_date if picks else None,
        window_end_date=boundary,
        picks=picks,
        notes=f"walk_back: shared boundary {boundary} merged from prior round, not re-scored",
    )


def plan_tail_round(
    vintages: list[VintageEntry],
    prev_round: Round,
    all_results: list[RoundResult],
    config: AdaptiveScanConfig,
    round_id: int,
) -> Round | None:
    """Tail round: score every remaining unscored vintage older than the current boundary.

    Triggered when next walk-back's candidate count < `tail_round_threshold`. After
    a tail round, if everything remains present, terminate
    `done_already_present_before_geid_history`.
    """
    if prev_round.window_start_date is None:
        return None
    boundary = prev_round.window_start_date
    scored = _scored_dates(all_results)
    candidates = sorted(
        [v for v in vintages if v.capture_date < boundary and v.capture_date not in scored],
        key=lambda v: v.capture_date,
    )
    if not candidates:
        return None
    picks = [
        Pick(
            chip_index=i + 1,
            capture_date=v.capture_date,
            version=v.version,
            requested_zoom=config.download_zoom_ladder[0],
        )
        for i, v in enumerate(candidates)
    ]
    return Round(
        round_id=round_id,
        round_type="tail",
        window_start_date=picks[0].capture_date,
        window_end_date=boundary,
        picks=picks,
        notes=f"tail: scoring all {len(picks)} remaining unscored vintages older than {boundary}",
    )


def plan_bisection_round(
    vintages: list[VintageEntry],
    transition_a: RoundResult,
    transition_p: RoundResult,
    all_results: list[RoundResult],
    config: AdaptiveScanConfig,
    round_id: int,
) -> Round | None:
    """Bisection round: every monthly vintage in the OPEN interval (A.date, P.date).

    Endpoints are not re-scored; merged via prior round results during decision.
    Returns None if no monthly vintage exists between A and P (caller terminates
    with `done_appears` and the prior interval [A.date, P.date]).
    """
    scored = _scored_dates(all_results)
    candidates = sorted(
        [
            v
            for v in vintages
            if transition_a.capture_date < v.capture_date < transition_p.capture_date
            and v.capture_date not in scored
        ],
        key=lambda v: v.capture_date,
    )
    if not candidates:
        return None
    picks = [
        Pick(
            chip_index=i + 1,
            capture_date=v.capture_date,
            version=v.version,
            requested_zoom=config.download_zoom_ladder[0],
        )
        for i, v in enumerate(candidates)
    ]
    return Round(
        round_id=round_id,
        round_type="bisection",
        window_start_date=transition_a.capture_date,
        window_end_date=transition_p.capture_date,
        picks=picks,
        notes=f"bisection: {len(picks)} monthly vintages in ({transition_a.capture_date}, {transition_p.capture_date})",
    )


def plan_anchor_recovery_round(
    vintages: list[VintageEntry],
    all_results: list[RoundResult],
    config: AdaptiveScanConfig,
    round_id: int,
) -> Round | None:
    """Single-pick recovery round: try the next-most-recent unscored GEHI vintage.

    Triggered when Round 1's latest pick was unusable (case C precondition fails)
    or the entire scan has zero usable observations. Capped at
    `max_anchor_recovery_rounds`. Returns None if no unscored vintage remains.
    """
    scored = _scored_dates(all_results)
    unscored_recent = sorted(
        [v for v in vintages if v.capture_date not in scored],
        key=lambda v: v.capture_date,
        reverse=True,
    )
    if not unscored_recent:
        return None
    pick_v = unscored_recent[0]
    pick = Pick(
        chip_index=1,
        capture_date=pick_v.capture_date,
        version=pick_v.version,
        requested_zoom=config.download_zoom_ladder[0],
    )
    return Round(
        round_id=round_id,
        round_type="anchor_recovery",
        window_start_date=pick.capture_date,
        window_end_date=pick.capture_date,
        picks=[pick],
        notes="anchor_recovery: prior latest_avail unusable; trying next-most-recent vintage",
    )


def _count_round_type(state: ScanState, round_type: str) -> int:
    return sum(1 for r in state.rounds if r.round_type == round_type)


def _bisection_already_done(state: ScanState) -> bool:
    return _count_round_type(state, "bisection") > 0


def decide_next_action(
    state: ScanState,
    vintages: list[VintageEntry],
    config: AdaptiveScanConfig,
) -> Action:
    """Return the next Action for the orchestrator: execute another round or terminate."""
    if state.is_terminal:
        return TerminateAction(kind="terminate", status=state.status)

    if not state.rounds:
        return ExecuteRoundAction(kind="execute_round", round=plan_initial_round(vintages, config))

    all_results = collect_all_results(state.rounds)

    if all_results and failure_pct(all_results) > config.case_e_failure_pct:
        return TerminateAction(
            kind="terminate",
            status="done_ambiguous_gemini_failed",
            notes=f"failure_pct={failure_pct(all_results):.1f} > threshold={config.case_e_failure_pct:.1f}",
        )

    usable = usable_observations(all_results)

    if not usable:
        recovery_count = _count_round_type(state, "anchor_recovery")
        if recovery_count >= config.max_anchor_recovery_rounds:
            return TerminateAction(
                kind="terminate",
                status="done_ambiguous_no_recent_anchor",
                notes=f"no usable observations after {recovery_count} anchor_recovery rounds",
            )
        rnd = plan_anchor_recovery_round(vintages, all_results, config, next_round_id(state))
        if rnd is None:
            return TerminateAction(
                kind="terminate",
                status="done_ambiguous_no_recent_anchor",
                notes="no unscored vintages remain for anchor_recovery",
            )
        return ExecuteRoundAction(kind="execute_round", round=rnd)

    if is_nonmonotonic(usable):
        return TerminateAction(
            kind="terminate",
            status="done_ambiguous_nonmonotonic",
            notes="present->absent step detected in chronological observations",
        )

    transitions = find_transitions(usable)

    if transitions:
        if _bisection_already_done(state):
            return TerminateAction(kind="terminate", status="done_appears")
        a, p = transitions[0]
        rnd = plan_bisection_round(vintages, a, p, all_results, config, next_round_id(state))
        if rnd is None:
            return TerminateAction(
                kind="terminate",
                status="done_appears",
                notes=f"no monthly vintages in ({a.capture_date}, {p.capture_date}); interval = [{a.capture_date}, {p.capture_date}]",
            )
        return ExecuteRoundAction(kind="execute_round", round=rnd)

    n_present = sum(1 for o in usable if o.pv_present)
    n_absent = sum(1 for o in usable if not o.pv_present)

    if n_absent > 0 and n_present == 0:
        return _decide_all_absent(state, vintages, all_results, config)

    if n_present > 0 and n_absent == 0:
        return _decide_all_present(state, vintages, all_results, config)

    raise RuntimeError(
        f"Unreachable: state has {len(usable)} usable obs but none are decidable (anchor_id={state.anchor_id})"
    )


def _decide_all_absent(
    state: ScanState,
    vintages: list[VintageEntry],
    all_results: list[RoundResult],
    config: AdaptiveScanConfig,
) -> Action:
    latest_avail = latest_vintage_date(vintages)
    latest_obs = next(
        (
            r
            for r in all_results
            if r.capture_date == latest_avail and r.quality_flag == "usable" and r.pv_present is False
        ),
        None,
    )
    if latest_obs is not None:
        return TerminateAction(
            kind="terminate",
            status="done_installed_during_census",
            notes=f"all observations absent; latest_avail={latest_avail} usable absent",
        )

    recovery_count = _count_round_type(state, "anchor_recovery")
    if recovery_count >= config.max_anchor_recovery_rounds:
        return TerminateAction(
            kind="terminate",
            status="done_ambiguous_no_recent_anchor",
            notes=f"all absent but latest_avail not usable after {recovery_count} anchor_recovery rounds",
        )
    rnd = plan_anchor_recovery_round(vintages, all_results, config, next_round_id(state))
    if rnd is None:
        return TerminateAction(
            kind="terminate",
            status="done_ambiguous_no_recent_anchor",
            notes="all absent; no unscored vintages remain for anchor_recovery",
        )
    return ExecuteRoundAction(kind="execute_round", round=rnd)


def _decide_all_present(
    state: ScanState,
    vintages: list[VintageEntry],
    all_results: list[RoundResult],
    config: AdaptiveScanConfig,
) -> Action:
    last_round = state.rounds[-1]

    if last_round.round_type == "tail":
        return TerminateAction(
            kind="terminate",
            status="done_already_present_before_geid_history",
            notes=f"all present including tail round covering {len(last_round.picks)} earliest vintages",
        )

    if last_round.window_start_date is None:
        return TerminateAction(
            kind="terminate",
            status="done_already_present_before_geid_history",
            notes="last round has no window_start_date; cannot walk back",
        )

    boundary = last_round.window_start_date
    scored = _scored_dates(all_results)
    older_unscored = [v for v in vintages if v.capture_date < boundary and v.capture_date not in scored]

    if not older_unscored:
        return TerminateAction(
            kind="terminate",
            status="done_already_present_before_geid_history",
            notes=f"all present; no unscored vintages older than {boundary}",
        )

    walk_back_year_floor = parse_iso(boundary).year - config.walk_back_years
    in_window = [v for v in older_unscored if parse_iso(v.capture_date).year >= walk_back_year_floor]

    if len(in_window) < config.tail_round_threshold:
        rnd = plan_tail_round(vintages, last_round, all_results, config, next_round_id(state))
        if rnd is None:
            return TerminateAction(
                kind="terminate",
                status="done_already_present_before_geid_history",
                notes=f"all present; tail round empty",
            )
        return ExecuteRoundAction(kind="execute_round", round=rnd)

    rnd = plan_walk_back_round(vintages, last_round, all_results, config, next_round_id(state))
    if rnd is None or not rnd.picks:
        rnd = plan_tail_round(vintages, last_round, all_results, config, next_round_id(state))
        if rnd is None:
            return TerminateAction(
                kind="terminate",
                status="done_already_present_before_geid_history",
                notes="all present; both walk-back and tail produced no picks",
            )
    return ExecuteRoundAction(kind="execute_round", round=rnd)
