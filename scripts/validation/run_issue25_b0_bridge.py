#!/usr/bin/env python3
"""Replay banked96 frame sets with the frozen ISSUE-25 model (B0 bridge)."""

from __future__ import annotations

import argparse
import copy
import csv
import dataclasses
import json
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from scripts.temporal.gehi_common import ensure_review_png
from scripts.temporal.presence_scorer import get_scorer
from scripts.temporal.run_adaptive_scan import (
    _default_gemini_env,
    _load_gemini_config,
    _routing_salt,
    _score_batch_picks_chunked,
)
from scripts.temporal.scan_config import AdaptiveScanConfig
from scripts.temporal.scan_state import RoundResult, load_scan_state, save_scan_state
from scripts.temporal.scoring_provenance import jsonl_writer, with_scoring_provenance
from scripts.validation.gemini_solar_image_review import BatchPick, RateLimiter


def _group_ids(path: Path) -> list[str]:
    with path.open(newline="", encoding="utf-8") as fh:
        return [row["anchor_id"] for row in csv.DictReader(fh)]


def replay_group(
    group_id: str,
    *,
    source_states_dir: Path,
    output_dir: Path,
    base_scorer,
    gemini_config,
    limiter: RateLimiter,
    provenance_writer,
    scan_config: AdaptiveScanConfig,
) -> str:
    output_path = output_dir / "scan_states" / f"{group_id}.json"
    if output_path.exists():
        return "skipped_existing"
    source_path = source_states_dir / f"{group_id}.json"
    state = load_scan_state(source_path)
    if state is None:
        raise FileNotFoundError(source_path)
    replay = copy.deepcopy(state)
    replay.notes = (replay.notes + " | " if replay.notes else "") + (
        "ISSUE-25 B0 fixed-frame replay; banked96 bytes and adaptive picks frozen"
    )
    scorer = with_scoring_provenance(
        base_scorer,
        provenance_writer,
        context={"anchor_id": group_id, "issue25_arm": "B0"},
    )

    for rnd in replay.rounds:
        score_picks: list[BatchPick] = []
        batch_to_original: dict[int, int] = {}
        source_by_chip = {result.chip_index: result for result in rnd.results}
        for result in rnd.results:
            if not result.chip_path or not Path(result.chip_path).exists():
                continue
            review_path = ensure_review_png(Path(result.chip_path))
            dense = len(score_picks) + 1
            score_picks.append(
                BatchPick(
                    chip_index=dense,
                    chip_path=review_path,
                    capture_date=result.capture_date,
                    version=str(result.version),
                    actual_zoom=result.actual_zoom,
                )
            )
            batch_to_original[dense] = result.chip_index

        audit_path = output_dir / "audit" / group_id / f"round_{rnd.round_id}.jsonl"
        audit_path.parent.mkdir(parents=True, exist_ok=True)
        audit_lock = threading.Lock()
        with audit_path.open("w", encoding="utf-8") as audit_fh:

            def audit_writer(payload: dict) -> None:
                with audit_lock:
                    audit_fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
                    audit_fh.flush()

            obs_by_chip = _score_batch_picks_chunked(
                score_picks,
                batch_to_original,
                config=scan_config,
                gemini_config=gemini_config,
                audit_writer=audit_writer,
                census_mid_date_iso=state.census_date,
                scorer=scorer,
                limiter=limiter,
                routing_salt=_routing_salt(
                    "target", gemini_config.model, group_id, rnd.round_id
                ),
            )

        new_results: list[RoundResult] = []
        for pick in rnd.picks:
            source = source_by_chip.get(pick.chip_index)
            obs = obs_by_chip.get(pick.chip_index)
            if source is None or obs is None:
                new_results.append(
                    RoundResult(
                        chip_index=pick.chip_index,
                        capture_date=pick.capture_date,
                        version=pick.version,
                        pv_present=None,
                        confidence=None,
                        quality_flag="unusable",
                        decision_source="gemini_failed",
                        notes="B0 replay missing source frame or model observation",
                        chip_path=source.chip_path if source is not None else "",
                        actual_zoom=source.actual_zoom if source is not None else None,
                    )
                )
                continue
            new_results.append(
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
                    chip_path=source.chip_path,
                    actual_zoom=source.actual_zoom,
                )
            )
        rnd.results = new_results
        rnd.notes = (rnd.notes + " | " if rnd.notes else "") + "B0 fixed-frame replay"

    output_path.parent.mkdir(parents=True, exist_ok=True)
    save_scan_state(replay, output_path)
    return "scored"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--groups-csv", type=Path, required=True)
    parser.add_argument("--source-states-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--model", default="gemini-3.1-flash-lite")
    parser.add_argument("--workers", type=int, default=40)
    parser.add_argument("--qps", type=float, default=20.0)
    args = parser.parse_args()

    groups = _group_ids(args.groups_csv)
    gemini_config = dataclasses.replace(
        _load_gemini_config(_default_gemini_env()), model=args.model
    )
    limiter = RateLimiter(args.qps)
    provenance_writer = jsonl_writer(args.output_dir / "scoring_provenance.jsonl")
    base_scorer = get_scorer("gemini")
    scan_config = AdaptiveScanConfig(gemini_max_dates_per_call=5)
    counts: dict[str, int] = {}

    def run(group_id: str) -> tuple[str, str]:
        status = replay_group(
            group_id,
            source_states_dir=args.source_states_dir,
            output_dir=args.output_dir,
            base_scorer=base_scorer,
            gemini_config=gemini_config,
            limiter=limiter,
            provenance_writer=provenance_writer,
            scan_config=scan_config,
        )
        print(f"[B0] {group_id}: {status}", flush=True)
        return group_id, status

    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = [pool.submit(run, group_id) for group_id in groups]
        for future in as_completed(futures):
            _group_id, status = future.result()
            counts[status] = counts.get(status, 0) + 1
    print(f"B0 complete groups={len(groups)} counts={dict(sorted(counts.items()))}")


if __name__ == "__main__":
    main()
