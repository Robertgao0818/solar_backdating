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
DEFAULT_CHIPS_DIR = Path.home() / "zasolar_data/geid_temporal/gehi_chips"
DEFAULT_AUDIT_DIR = Path.home() / "zasolar_data/geid_temporal/jhb_vexcel10_smoke/gemini_audit"
DEFAULT_CONFIG_YAML = PROJECT_ROOT / "configs" / "geid_anchor_presence.yaml"
DEFAULT_GEMINI_ENV = PROJECT_ROOT / ".env.gemini.local"

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
    parser.add_argument("--chips-dir", type=Path, default=DEFAULT_CHIPS_DIR, help="Where GEHI chips are written")
    parser.add_argument("--audit-dir", type=Path, default=DEFAULT_AUDIT_DIR, help="Per-anchor per-round Gemini audit JSONL")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_YAML)
    parser.add_argument("--gemini-env-file", type=Path, default=DEFAULT_GEMINI_ENV)
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


def execute_round_real(
    rnd: Round,
    anchor: dict[str, str],
    config: AdaptiveScanConfig,
    *,
    chips_dir: Path,
    audit_dir: Path,
    gemini_config,  # GeminiClientConfig - imported lazily
) -> Round:
    """Download chips for each pick (zoom ladder), batch-score with Gemini, return Round with results."""
    import json as _json

    from scripts.temporal.gehi_download import download_chip_with_zoom_ladder
    from scripts.validation.gemini_solar_image_review import (
        BatchPick,
        score_batch_with_fallback,
    )

    download_outcomes: list[tuple[Pick, object]] = []
    for pick in rnd.picks:
        outcome = download_chip_with_zoom_ladder(
            anchor,
            capture_date=pick.capture_date,
            version=pick.version,
            zoom_ladder=config.download_zoom_ladder,
            output_root=chips_dir,
        )
        download_outcomes.append((pick, outcome))

    score_picks: list[BatchPick] = []
    download_by_index: dict[int, object] = {}
    for pick, outcome in download_outcomes:
        download_by_index[pick.chip_index] = outcome
        if outcome.status in ("ok", "skipped_existing") and outcome.path:
            score_picks.append(
                BatchPick(
                    chip_index=pick.chip_index,
                    chip_path=outcome.path,
                    capture_date=pick.capture_date,
                    version=str(pick.version),
                    actual_zoom=outcome.actual_zoom,
                )
            )

    audit_path = audit_dir / anchor["anchor_id"] / f"round_{rnd.round_id}.jsonl"
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    observations = []
    if score_picks:
        with audit_path.open("w", encoding="utf-8") as audit_fh:
            def _audit(payload: dict) -> None:
                audit_fh.write(_json.dumps(payload, ensure_ascii=False) + "\n")

            observations = score_batch_with_fallback(
                score_picks, config=gemini_config, audit_writer=_audit
            )

    obs_by_index = {o.chip_index: o for o in observations}
    rnd_results: list[RoundResult] = []
    for pick in rnd.picks:
        outcome = download_by_index[pick.chip_index]
        if outcome.status not in ("ok", "skipped_existing") or outcome.path is None:
            rnd_results.append(
                RoundResult(
                    chip_index=pick.chip_index,
                    capture_date=pick.capture_date,
                    version=pick.version,
                    pv_present=None,
                    confidence=None,
                    quality_flag="unusable",
                    decision_source="gemini_failed",
                    evidence="",
                    notes=f"download_failed: status={outcome.status} error={outcome.error or ''}"[:300],
                    chip_path="",
                    actual_zoom=outcome.actual_zoom,
                )
            )
            continue
        obs = obs_by_index.get(pick.chip_index)
        if obs is None:
            rnd_results.append(
                RoundResult(
                    chip_index=pick.chip_index,
                    capture_date=pick.capture_date,
                    version=pick.version,
                    pv_present=None,
                    confidence=None,
                    quality_flag="unusable",
                    decision_source="gemini_failed",
                    evidence="",
                    notes="missing observation in batch results",
                    chip_path=str(outcome.path),
                    actual_zoom=outcome.actual_zoom,
                )
            )
            continue
        rnd_results.append(
            RoundResult(
                chip_index=pick.chip_index,
                capture_date=pick.capture_date,
                version=pick.version,
                pv_present=obs.pv_present,
                confidence=obs.confidence,
                quality_flag=obs.quality_flag,
                decision_source=obs.decision_source,
                evidence=obs.evidence,
                notes=obs.notes,
                chip_path=str(outcome.path),
                actual_zoom=outcome.actual_zoom,
            )
        )

    rnd.results = rnd_results
    rnd.completed = True
    rnd.failed = False
    return rnd


def _load_gemini_config(env_file: Path):
    """Load GeminiClientConfig from .env.gemini.local. Lazy import to avoid hard dep in dry-run."""
    from scripts.validation.gemini_solar_image_review import (
        API_FORMATS,
        GeminiClientConfig,
        env_value,
        load_env_file,
    )

    env = load_env_file(env_file)
    base_url = env_value(env, "GOOGLE_GEMINI_BASE_URL")
    api_key = env_value(env, "GEMINI_API_KEY")
    model = env_value(env, "GEMINI_MODEL", "gemini-3-flash-preview")
    api_format = env_value(env, "GEMINI_API_FORMAT", "native")
    native_path = env_value(env, "GEMINI_NATIVE_PATH", "/v1beta")
    if not base_url:
        raise SystemExit(
            f"Missing GOOGLE_GEMINI_BASE_URL (set in {env_file} or env). Use --dry-run if you want to skip Gemini."
        )
    if not api_key:
        raise SystemExit(
            f"Missing GEMINI_API_KEY (set in {env_file} or env). Use --dry-run if you want to skip Gemini."
        )
    if api_format not in API_FORMATS:
        raise SystemExit(f"Unsupported GEMINI_API_FORMAT={api_format!r}; choose {sorted(API_FORMATS)}")
    return GeminiClientConfig(
        base_url=base_url,
        api_key=api_key,
        model=model,
        api_format=api_format,
        native_path=native_path,
    )


def run_one_anchor(
    anchor: dict[str, str],
    config: AdaptiveScanConfig,
    scan_states_dir: Path,
    *,
    dry_run: bool,
    force_restart: bool,
    chips_dir: Path | None = None,
    audit_dir: Path | None = None,
    gemini_config=None,
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
            assert chips_dir is not None and audit_dir is not None and gemini_config is not None
            rnd = execute_round_real(
                rnd, anchor, config,
                chips_dir=chips_dir, audit_dir=audit_dir, gemini_config=gemini_config,
            )
        state.rounds.append(rnd)
        state.next_action = "decide_next_action"
        save_scan_state(state, state_path)
    raise RuntimeError(f"Scan loop exceeded {max_iter} rounds for {anchor_id}")


def _fetch_real_vintages(anchor: dict[str, str], config: AdaptiveScanConfig) -> list[VintageEntry]:
    """Fetch the deduped GEHI vintage catalog for an anchor at config.info_zoom."""
    from scripts.temporal.gehi_info import fetch_vintages_for_anchor

    rows = fetch_vintages_for_anchor(anchor, zoom=config.info_zoom)
    out: list[VintageEntry] = []
    for r in rows:
        capture_date = str(r.get("capture_date", "")).strip()
        version_raw = r.get("version")
        if not capture_date or version_raw in (None, ""):
            continue
        try:
            version = int(version_raw)
        except (TypeError, ValueError):
            continue
        out.append(VintageEntry(capture_date=capture_date, version=version))
    return out


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

    gemini_config = None
    if not args.dry_run:
        gemini_config = _load_gemini_config(args.gemini_env_file)
        args.chips_dir.mkdir(parents=True, exist_ok=True)
        args.audit_dir.mkdir(parents=True, exist_ok=True)

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
                chips_dir=args.chips_dir,
                audit_dir=args.audit_dir,
                gemini_config=gemini_config,
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
