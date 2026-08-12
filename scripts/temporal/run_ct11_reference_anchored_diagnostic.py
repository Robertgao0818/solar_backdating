#!/usr/bin/env python3
"""Diagnose reference-anchored scoring on the failed corrected CT-11 holdout.

This is development-only evidence.  It does not mutate production states and
cannot serve as CT-11 acceptance.  The diagnostic adds the already-frozen
post-census appearance reference to each corrected pre-cutoff strip, while the
reference row remains explicitly ``reference_only`` and is excluded from the
scientific classification.
"""

from __future__ import annotations

import argparse
import csv
import dataclasses
import hashlib
import json
import random
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from scripts.temporal.gehi_common import ensure_review_png
from scripts.temporal.quota_control import QuotaCircuitBreaker, QuotaPolicy
from scripts.temporal.run_adaptive_scan import _default_gemini_env, _load_gemini_config
from scripts.temporal.run_ct11_recovery_pilot import classify_observations
from scripts.validation.gemini_solar_image_review import (
    BatchPick,
    RateLimiter,
    score_batch_with_fallback,
)


CLASSES = ("ALREADY_PRESENT", "ALL_ABSENT", "TRANSITION", "UNDATABLE")


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def resolve_reference_result(state: dict[str, Any]) -> dict[str, Any] | None:
    """Return the newest explicit reference-only result, if one exists."""
    candidates = [
        result
        for round_doc in state.get("rounds", [])
        for result in round_doc.get("results", [])
        if result.get("reference_only") is True
        and result.get("capture_date")
        and result.get("chip_path")
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda row: str(row["capture_date"])[:10])


def choose_balanced_reference_eligible(
    joined_rows: list[dict[str, str]],
    states: dict[str, tuple[Path, dict[str, Any]]],
    *,
    n_per_class: int,
    seed: int,
) -> list[dict[str, Any]]:
    """Freeze an outcome-balanced development sample with an explicit reference."""
    rng = random.Random(seed)
    chosen: list[dict[str, Any]] = []
    for label in CLASSES:
        eligible = sorted(
            (
                row
                for row in joined_rows
                if row["independent_class"] == label
                and row["anchor_id"] in states
                and resolve_reference_result(states[row["anchor_id"]][1]) is not None
            ),
            key=lambda row: row["anchor_id"],
        )
        if len(eligible) < n_per_class:
            raise ValueError(f"{label} has only {len(eligible)} reference-eligible rows")
        chosen.extend(rng.sample(eligible, n_per_class))
    rng.shuffle(chosen)
    return [
        {
            "pilot_index": index,
            "anchor_id": row["anchor_id"],
            "blind_index": int(row["blind_index"]),
            "corrected_class": row["independent_class"],
        }
        for index, row in enumerate(chosen, start=1)
    ]


def write_hash_manifest(output_root: Path) -> Path:
    paths = [
        output_root / "selection.json",
        output_root / "run_config.json",
        output_root / "summary.json",
        output_root / "quota_ledger.jsonl",
    ]
    paths.extend(sorted((output_root / "results").glob("*.json")))
    paths.extend(sorted((output_root / "audit").glob("*.jsonl")))
    manifest = output_root / "diagnostic_outputs.sha256"
    manifest.write_text(
        "".join(
            f"{sha256_file(path)}  {path.relative_to(output_root)}\n"
            for path in paths
            if path.exists()
        ),
        encoding="utf-8",
    )
    return manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package-root", required=True, type=Path)
    parser.add_argument("--review-root", required=True, type=Path)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--model", default="gemini-3.1-flash-lite")
    parser.add_argument("--expected-model", default="gemini-3.1-flash-lite")
    parser.add_argument("--n-per-class", type=int, default=10)
    parser.add_argument("--selection-seed", type=int, default=2026080401)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--qps", type=float, default=6.0)
    parser.add_argument("--window-id", required=True)
    parser.add_argument("--reset-after")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if abs(args.qps - 6.0) > 1e-12:
        raise SystemExit("this diagnostic is locked to startup QPS=6")
    if args.n_per_class < 1:
        raise SystemExit("--n-per-class must be positive")

    args.output_root.mkdir(parents=True, exist_ok=True)
    results_dir = args.output_root / "results"
    audit_dir = args.output_root / "audit"
    results_dir.mkdir(exist_ok=True)
    audit_dir.mkdir(exist_ok=True)

    joined_path = args.review_root / "evaluation" / "ct11_corrected_joined.csv"
    state_selection_path = args.package_root / "authoritative_state_selection.csv"
    private_manifest_path = args.package_root / "private_blind_manifest.json"
    frame_report_path = args.package_root / "rerender" / "frame_report.csv"
    joined_rows = read_csv(joined_path)
    state_selection = read_csv(state_selection_path)
    states: dict[str, tuple[Path, dict[str, Any]]] = {}
    for row in state_selection:
        path = Path(row["selected_state_path"])
        if sha256_file(path) != row["selected_state_sha256"]:
            raise ValueError(f"state hash mismatch: {path}")
        states[row["anchor_id"]] = (path, json.loads(path.read_text(encoding="utf-8")))

    selection_path = args.output_root / "selection.json"
    expected_n = len(CLASSES) * args.n_per_class
    if selection_path.exists():
        selection = json.loads(selection_path.read_text(encoding="utf-8"))
    else:
        anchors = choose_balanced_reference_eligible(
            joined_rows,
            states,
            n_per_class=args.n_per_class,
            seed=args.selection_seed,
        )
        selection = {
            "schema_version": 1,
            "purpose": "failed-holdout development diagnostic; never acceptance",
            "selection_seed": args.selection_seed,
            "n_per_corrected_class": args.n_per_class,
            "n_anchors": len(anchors),
            "anchors": anchors,
        }
        selection_path.write_text(json.dumps(selection, indent=2) + "\n", encoding="utf-8")
    if selection.get("n_anchors") != expected_n or len(selection.get("anchors", [])) != expected_n:
        raise SystemExit("existing selection does not match the requested frozen design")
    selected = selection["anchors"]
    if args.limit is not None:
        if args.limit < 1 or args.limit > len(selected):
            raise SystemExit("--limit is outside the frozen selection")
        selected = selected[: args.limit]

    private_manifest = {
        row["anchor_id"]: row
        for row in json.loads(private_manifest_path.read_text(encoding="utf-8"))
    }
    corrected_by_blind = {
        int(row["blind_index"]): row
        for row in read_csv(args.review_root / "blind_pass1_independent.csv")
    }
    frame_paths = {
        (row["anchor_id"], row["capture_date"]): Path(row["chip_path"])
        for row in read_csv(frame_report_path)
        if row["source"] == "scan_tm" and row["status"] in {"ok", "skipped_existing"}
    }

    reset_after = args.reset_after or (
        datetime.now(timezone.utc) + timedelta(hours=6)
    ).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    run_config = {
        "schema_version": 1,
        "purpose": "reference-anchored corrected CT-11 development diagnostic; not acceptance",
        "selection_sha256": sha256_file(selection_path),
        "joined_sha256": sha256_file(joined_path),
        "state_selection_sha256": sha256_file(state_selection_path),
        "private_manifest_sha256": sha256_file(private_manifest_path),
        "frame_report_sha256": sha256_file(frame_report_path),
        "model": args.model,
        "expected_model": args.expected_model,
        "limit": len(selected),
        "qps": 6.0,
        "max_in_flight": 0,
        "workers": args.workers,
        "window_id": args.window_id,
        "reset_after": reset_after,
        "evidence_cutoff": "2025-01-31",
        "reference_policy": "newest_explicit_reference_only_excluded_from_classification",
    }
    config_path = args.output_root / "run_config.json"
    if config_path.exists():
        if json.loads(config_path.read_text(encoding="utf-8")) != run_config:
            raise SystemExit("existing run_config.json differs from this invocation")
    else:
        config_path.write_text(json.dumps(run_config, indent=2) + "\n", encoding="utf-8")

    policy = QuotaPolicy(
        window_id=args.window_id,
        gross_safe_budget=max(20, len(selected) * 3),
        work_budget=max(10, len(selected) * 2),
        warning_budget=max(5, len(selected)),
        canary_reserve=0,
        retry_reserve=max(5, len(selected)),
        reset_after=reset_after,
    )
    breaker = QuotaCircuitBreaker(
        args.output_root / "quota_ledger.jsonl",
        policy=policy,
        pause_path=args.output_root / "PAUSED_QUOTA.json",
        model_tier="ct11_reference_anchored_diagnostic",
    )
    base_config = _load_gemini_config(
        args.env_file or _default_gemini_env(),
        expected_model_version=args.expected_model,
    )
    gemini_config = dataclasses.replace(
        base_config,
        model=args.model,
        expected_model_version=args.expected_model,
        quota_controller=breaker,
    )
    limiter = RateLimiter(6.0, max_in_flight=0)
    print_lock = threading.Lock()

    def run_one(item: dict[str, Any]) -> dict[str, Any]:
        pilot_index = int(item["pilot_index"])
        result_path = results_dir / f"{pilot_index:03d}.json"
        if result_path.exists():
            return json.loads(result_path.read_text(encoding="utf-8"))
        anchor_id = item["anchor_id"]
        manifest = private_manifest[anchor_id]
        dates = list(manifest["frame_dates"])
        evidence_paths = [frame_paths[(anchor_id, date)] for date in dates]
        reference = resolve_reference_result(states[anchor_id][1])
        if reference is None:
            raise ValueError(f"frozen selection lost reference eligibility: {anchor_id}")
        reference_path = ensure_review_png(Path(reference["chip_path"]))
        paths = [*evidence_paths, reference_path]
        if not all(path.is_file() for path in paths):
            raise FileNotFoundError(f"missing diagnostic input for {anchor_id}")
        picks = [
            BatchPick(
                chip_index=index,
                chip_path=path,
                capture_date=(dates[index - 1] if index <= len(dates) else str(reference["capture_date"])[:10]),
                version="ct11_reference_anchored_diagnostic",
                reference_only=index > len(dates),
            )
            for index, path in enumerate(paths, start=1)
        ]
        audit_path = audit_dir / f"{pilot_index:03d}.jsonl"

        def audit_writer(payload: dict[str, Any]) -> None:
            with audit_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(payload, ensure_ascii=False) + "\n")

        observations = score_batch_with_fallback(
            picks,
            config=gemini_config,
            audit_writer=audit_writer,
            census_mid_date_iso="2025-01-31",
            limiter=limiter,
            routing_salt=f"ct11-reference-anchored:{anchor_id}",
            attempt_context={
                "anchor_id": anchor_id,
                "run_id": args.window_id,
                "wave_id": "failed_holdout_diagnostic",
                "round_id": 1,
                "round_type": "reference_anchored_strip",
                "chunk_index": 1,
                "n_picks": len(picks),
                "requested_alias": args.model,
                "model_tier": "ct11_reference_anchored_diagnostic",
                "logical_call_id": f"ct11-reference-anchored:{anchor_id}",
            },
        )
        evidence_observations = observations[: len(dates)]
        predicted_class, latest_absent, earliest_present = classify_observations(
            evidence_observations, dates
        )
        corrected = corrected_by_blind[int(manifest["blind_index"])]
        result = {
            **item,
            "model": args.model,
            "expected_model": args.expected_model,
            "frame_dates": dates,
            "reference_date": str(reference["capture_date"])[:10],
            "reference_chip_sha256": sha256_file(Path(reference["chip_path"])),
            "predicted_class": predicted_class,
            "predicted_latest_absent": latest_absent,
            "predicted_earliest_present": earliest_present,
            "corrected_class": corrected["independent_class"],
            "corrected_latest_absent": corrected["corrected_latest_absent"],
            "corrected_earliest_present": corrected["corrected_earliest_present"],
            "corrected_class_match": predicted_class == corrected["independent_class"],
            "corrected_interval_match": (
                predicted_class == corrected["independent_class"]
                and latest_absent == corrected["corrected_latest_absent"]
                and earliest_present == corrected["corrected_earliest_present"]
            ),
            "observations": [dataclasses.asdict(row) for row in observations],
        }
        result_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        with print_lock:
            print(
                f"[{pilot_index:03d}] corrected={corrected['independent_class']} "
                f"reference_anchored={predicted_class} match={result['corrected_class_match']}",
                flush=True,
            )
        return result

    results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = [executor.submit(run_one, item) for item in selected]
        for future in as_completed(futures):
            results.append(future.result())
    results.sort(key=lambda row: int(row["pilot_index"]))
    class_matches = sum(bool(row["corrected_class_match"]) for row in results)
    interval_matches = sum(bool(row["corrected_interval_match"]) for row in results)
    summary = {
        "schema_version": 1,
        "status": "complete",
        "purpose": "failed_holdout_diagnostic_only_not_acceptance",
        "n": len(results),
        "model": args.model,
        "qps": 6.0,
        "max_in_flight": 0,
        "corrected_class_matches": class_matches,
        "corrected_class_agreement": class_matches / len(results),
        "corrected_interval_matches": interval_matches,
        "corrected_interval_agreement": interval_matches / len(results),
        "by_corrected_class": {
            label: {
                "n": sum(row["corrected_class"] == label for row in results),
                "class_matches": sum(
                    row["corrected_class"] == label and row["corrected_class_match"]
                    for row in results
                ),
            }
            for label in CLASSES
        },
        "quota": breaker.summary(),
    }
    (args.output_root / "summary.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8"
    )
    write_hash_manifest(args.output_root)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
