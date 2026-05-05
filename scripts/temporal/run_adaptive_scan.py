#!/usr/bin/env python3
"""Adaptive PV-presence scan orchestrator (Phase-0 skeleton).

For each anchor in the input CSV, drives an adaptive round-by-round scan over
historical GEHI vintages, scoring each picked vintage with Gemini, and writes a
per-anchor `scan_state.json` checkpoint after every round. Task A ships only
the dry-run skeleton: GEHI/Gemini calls are stubbed by `--dry-run` so the loop
can be exercised without API cost. Tasks C/D wire in the real providers.

Quick start (dry-run, jhb_vexcel10_smoke):

    python scripts/temporal/run_adaptive_scan.py \
        --anchors-csv ~/zasolar_data/geid_temporal/jhb_vexcel10_smoke/anchors.csv \
        --scan-states-dir ~/zasolar_data/geid_temporal/jhb_vexcel10_smoke/scan_states \
        --dry-run --limit-anchors 3
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Iterable

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.temporal.scan_config import AdaptiveScanConfig, load_config
from scripts.temporal.scan_decision import (
    Action,
    ExecuteRoundAction,
    TerminateAction,
    VintageEntry,
    decide_next_action,
    parse_iso,
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

DEFAULT_ANCHORS_CSV = Path.home() / "zasolar_data/geid_temporal/jhb_vexcel10_smoke/anchors.csv"
DEFAULT_SCAN_STATES_DIR = Path.home() / "zasolar_data/geid_temporal/jhb_vexcel10_smoke/scan_states"
DEFAULT_CONFIG_YAML = PROJECT_ROOT / "configs" / "geid_anchor_presence.yaml"

DRY_RUN_PROFILE_LABELS = (
    "appears_2015",
    "appears_2018",
    "appears_2020",
    "appears_2023",
    "all_present",
    "all_absent",
)


@dataclass(frozen=True)
class DryRunProfile:
    label: str
    install_date: date | None  # None when never present (case C) or always present (case B)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--anchors-csv", type=Path, default=DEFAULT_ANCHORS_CSV)
    parser.add_argument("--scan-states-dir", type=Path, default=DEFAULT_SCAN_STATES_DIR)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_YAML)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Skip GEHI/Gemini calls; mock vintage list and Gemini results from anchor_id hash.",
    )
    parser.add_argument("--limit-anchors", type=int, help="Process only the first N anchors")
    parser.add_argument(
        "--force-restart",
        action="store_true",
        help="Delete and recreate every scan_state. Default behavior is resume from existing state.",
    )
    return parser.parse_args()


def read_anchors(path: Path, limit: int | None) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        rows = [dict(r) for r in reader]
    if limit is not None:
        rows = rows[:limit]
    return rows


def anchor_hash(anchor_id: str) -> int:
    digest = hashlib.sha256(anchor_id.encode("utf-8")).hexdigest()
    return int(digest[:8], 16)


def dry_run_profile_for(anchor_id: str) -> DryRunProfile:
    label = DRY_RUN_PROFILE_LABELS[anchor_hash(anchor_id) % len(DRY_RUN_PROFILE_LABELS)]
    if label.startswith("appears_"):
        year = int(label.split("_")[1])
        return DryRunProfile(label=label, install_date=date(year, 6, 15))
    return DryRunProfile(label=label, install_date=None)


def dry_run_vintages(anchor_id: str) -> list[VintageEntry]:
    """Synthetic z=19 vintage list spanning 2009-04 .. 2025-03 with some gaps."""
    seed = anchor_hash(anchor_id)
    out: list[VintageEntry] = []
    cursor = date(2009, 4, 1)
    end = date(2025, 3, 31)
    version = 100 + (seed % 50)
    while cursor <= end:
        if (cursor.month + seed) % 7 != 0:
            out.append(VintageEntry(capture_date=cursor.isoformat(), version=version))
            version += 1
        step_months = 4 + (cursor.month + seed) % 5
        new_year = cursor.year + (cursor.month - 1 + step_months) // 12
        new_month = (cursor.month - 1 + step_months) % 12 + 1
        cursor = date(new_year, new_month, 1)
    return out


def dry_run_gemini_result(
    pick: Pick,
    profile: DryRunProfile,
) -> RoundResult:
    pv_present: bool | None
    quality = "usable"
    notes = f"dry_run profile={profile.label}"
    pick_date = parse_iso(pick.capture_date)
    if profile.label == "all_present":
        pv_present = True
    elif profile.label == "all_absent":
        pv_present = False
    else:
        assert profile.install_date is not None
        pv_present = pick_date >= profile.install_date
    return RoundResult(
        chip_index=pick.chip_index,
        capture_date=pick.capture_date,
        version=pick.version,
        pv_present=pv_present,
        confidence=0.95,
        quality_flag=quality,
        decision_source="dry_run_stub",
        evidence=f"stub evidence for {profile.label}",
        notes=notes,
        chip_path="",
        actual_zoom=pick.requested_zoom,
    )


def execute_round_dry_run(
    rnd: Round,
    profile: DryRunProfile,
) -> Round:
    rnd.results = [dry_run_gemini_result(pick, profile) for pick in rnd.picks]
    rnd.completed = True
    rnd.failed = False
    return rnd


def execute_round_real(rnd: Round, anchor: dict[str, str], config: AdaptiveScanConfig) -> Round:
    raise NotImplementedError(
        "Real round execution requires Tasks C+D (gehi_download zoom ladder + "
        "gemini batch mode). Run with --dry-run for now."
    )


def run_one_anchor(
    anchor: dict[str, str],
    config: AdaptiveScanConfig,
    scan_states_dir: Path,
    *,
    dry_run: bool,
    force_restart: bool,
) -> ScanState:
    anchor_id = anchor["anchor_id"]
    state_path = state_path_for(anchor_id, scan_states_dir)

    state: ScanState | None = None
    if force_restart and state_path.exists():
        state_path.unlink()
    else:
        state = load_scan_state(state_path)

    if state is None:
        state = create_scan_state(anchor)
        save_scan_state(state, state_path)

    if state.is_terminal:
        return state

    profile = dry_run_profile_for(anchor_id) if dry_run else None
    vintages = dry_run_vintages(anchor_id) if dry_run else _fetch_real_vintages(anchor, config)

    max_iter = 32
    for _ in range(max_iter):
        action: Action = decide_next_action(state, vintages, config)
        if isinstance(action, TerminateAction):
            state.status = action.status
            if action.notes:
                state.notes = (state.notes + " | " if state.notes else "") + action.notes
            state.next_action = None
            save_scan_state(state, state_path)
            return state
        assert isinstance(action, ExecuteRoundAction)
        rnd = action.round
        if dry_run:
            assert profile is not None
            rnd = execute_round_dry_run(rnd, profile)
        else:
            rnd = execute_round_real(rnd, anchor, config)
        state.rounds.append(rnd)
        state.next_action = "decide_next_action"
        save_scan_state(state, state_path)
    raise RuntimeError(f"Scan loop exceeded {max_iter} rounds for {anchor_id}")


def _fetch_real_vintages(anchor: dict[str, str], config: AdaptiveScanConfig) -> list[VintageEntry]:
    raise NotImplementedError(
        "Real vintage fetch via gehi_info is not wired in Task A. Use --dry-run."
    )


def summarize(states: Iterable[ScanState]) -> None:
    by_status: dict[str, int] = defaultdict(int)
    total_rounds = 0
    total_observations = 0
    for s in states:
        by_status[s.status] += 1
        total_rounds += len(s.rounds)
        total_observations += sum(len(r.results) for r in s.rounds)
    print(f"\nProcessed {sum(by_status.values())} anchors, {total_rounds} rounds, {total_observations} observations.")
    for status in sorted(by_status):
        print(f"  {status}: {by_status[status]}")


def main() -> None:
    args = parse_args()
    config = load_config(args.config)
    if not args.anchors_csv.exists():
        raise SystemExit(f"Anchors CSV not found: {args.anchors_csv}")
    args.scan_states_dir.mkdir(parents=True, exist_ok=True)
    anchors = read_anchors(args.anchors_csv, limit=args.limit_anchors)
    if not anchors:
        raise SystemExit("Anchors CSV produced 0 rows.")

    states: list[ScanState] = []
    for anchor in anchors:
        anchor_id = anchor["anchor_id"]
        marker = "[DRY]" if args.dry_run else "[RUN]"
        try:
            state = run_one_anchor(
                anchor,
                config,
                args.scan_states_dir,
                dry_run=args.dry_run,
                force_restart=args.force_restart,
            )
        except Exception as exc:
            state = _record_orchestrator_failure(
                anchor, args.scan_states_dir, exc
            )
            print(f"{marker} {anchor_id}: ERROR status={state.status} reason={exc!r}")
        else:
            print(f"{marker} {anchor_id}: status={state.status} rounds={len(state.rounds)}")
        states.append(state)
    summarize(states)


def _record_orchestrator_failure(
    anchor: dict[str, str], scan_states_dir: Path, exc: BaseException
) -> ScanState:
    """Persist a terminal scan_state when run_one_anchor raises, so the batch can continue.

    Reuses any pre-existing rounds (so partial progress is preserved) and tags
    the state as `done_ambiguous_orchestrator_error` with the exception summary
    in `notes`. Never re-raises — the batch loop owns continuation.
    """
    state_path = state_path_for(anchor["anchor_id"], scan_states_dir)
    state = load_scan_state(state_path)
    if state is None:
        state = create_scan_state(anchor)
    state.status = "done_ambiguous_orchestrator_error"
    state.next_action = None
    error_note = f"orchestrator_error: {type(exc).__name__}: {exc}"
    state.notes = (state.notes + " | " + error_note) if state.notes else error_note
    save_scan_state(state, state_path)
    return state


if __name__ == "__main__":
    main()
