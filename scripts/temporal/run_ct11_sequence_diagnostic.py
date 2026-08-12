#!/usr/bin/env python3
"""Run a non-mutating corrected CT-11 sequence-scoring diagnostic.

This pilot uses the existing single-target sequence scorer over the frozen
corrected native frame set.  It never writes production scan states and is not
an acceptance test; the failed CT-11 holdout is used only to diagnose whether
sequence-aware scoring is a viable remediation architecture.
"""

from __future__ import annotations

import argparse
import csv
import dataclasses
import hashlib
import json
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scripts.temporal.quota_control import QuotaCircuitBreaker, QuotaPolicy
from scripts.temporal.run_adaptive_scan import _default_gemini_env, _load_gemini_config
from scripts.validation.gemini_solar_image_review import (
    RateLimiter,
    SequenceDatePick,
    score_single_target_sequence,
)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def classify_sequence(result) -> tuple[str, str, str]:
    """Map ordered tri-state sequence evidence to the blind-review classes."""
    observations = sorted(result.observations, key=lambda row: row.date_index)
    if result.quality_flag == "unusable" or not observations:
        return "UNDATABLE", "", ""
    values = [row.pv_present for row in observations]
    if all(value is not False for value in values) and values[0] is True:
        return "ALREADY_PRESENT", "", observations[0].capture_date
    if all(value is not True for value in values) and values[-1] is False:
        return "ALL_ABSENT", observations[-1].capture_date, ""
    true_indices = [index for index, value in enumerate(values) if value is True]
    false_indices = [index for index, value in enumerate(values) if value is False]
    if true_indices and false_indices:
        first_true = min(true_indices)
        prior_false = [index for index in false_indices if index < first_true]
        later_false = [index for index in false_indices if index > first_true]
        if prior_false and not later_false:
            last_false = max(prior_false)
            return (
                "TRANSITION",
                observations[last_false].capture_date,
                observations[first_true].capture_date,
            )
    return "UNDATABLE", "", ""


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_hash_manifest(output_root: Path) -> None:
    paths = [output_root / "run_config.json", output_root / "summary.json", output_root / "quota_ledger.jsonl"]
    paths.extend(sorted((output_root / "results").glob("*.json")))
    paths.extend(sorted((output_root / "audit").glob("*.jsonl")))
    lines = [f"{sha256_file(path)}  {path.relative_to(output_root)}" for path in paths if path.exists()]
    (output_root / "diagnostic_outputs.sha256").write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package-root", required=True, type=Path)
    parser.add_argument("--review-root", required=True, type=Path)
    parser.add_argument("--selection", required=True, type=Path)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--model", default="gemini-3-flash")
    parser.add_argument("--expected-model", default="gemini-3-flash")
    parser.add_argument("--limit", type=int, default=40)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--qps", type=float, default=6.0)
    parser.add_argument("--window-id", required=True)
    parser.add_argument("--reset-after")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.limit < 1 or args.limit > 40:
        raise SystemExit("--limit must be in 1..40")
    if args.qps > 8:
        raise SystemExit("diagnostic start-rate must not exceed QPS=8")
    args.output_root.mkdir(parents=True, exist_ok=True)
    results_dir = args.output_root / "results"
    audit_dir = args.output_root / "audit"
    results_dir.mkdir(exist_ok=True)
    audit_dir.mkdir(exist_ok=True)

    selection_doc = json.loads(args.selection.read_text(encoding="utf-8"))
    selected = selection_doc["anchors"][: args.limit]
    private_manifest = json.loads(
        (args.package_root / "private_blind_manifest.json").read_text(encoding="utf-8")
    )
    manifest_by_anchor = {row["anchor_id"]: row for row in private_manifest}
    blind_rows = {
        int(row["blind_index"]): row
        for row in read_csv(args.review_root / "blind_pass1_independent.csv")
    }
    frame_rows = read_csv(args.package_root / "rerender" / "frame_report.csv")
    frame_paths = {
        (row["anchor_id"], row["capture_date"]): Path(row["chip_path"])
        for row in frame_rows
        if row["source"] == "scan_tm" and row["status"] in {"ok", "skipped_existing"}
    }

    reset_after = args.reset_after or (
        datetime.now(timezone.utc) + timedelta(hours=6)
    ).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    run_config = {
        "schema_version": 1,
        "purpose": "corrected CT-11 sequence-path diagnosis; not acceptance",
        "selection_sha256": sha256_file(args.selection),
        "package_summary_sha256": sha256_file(args.package_root / "corrected_package_summary.json"),
        "pass1_sha256": sha256_file(args.review_root / "blind_pass1_independent.csv"),
        "model": args.model,
        "expected_model": args.expected_model,
        "limit": args.limit,
        "qps": args.qps,
        "max_in_flight": 0,
        "workers": args.workers,
        "window_id": args.window_id,
        "reset_after": reset_after,
    }
    config_path = args.output_root / "run_config.json"
    if config_path.exists():
        if json.loads(config_path.read_text(encoding="utf-8")) != run_config:
            raise SystemExit("existing run_config.json differs from requested diagnostic")
    else:
        config_path.write_text(json.dumps(run_config, indent=2) + "\n", encoding="utf-8")

    policy = QuotaPolicy(
        window_id=args.window_id,
        gross_safe_budget=max(20, args.limit * 3),
        work_budget=max(10, args.limit * 2),
        warning_budget=max(5, args.limit),
        canary_reserve=0,
        retry_reserve=max(5, args.limit),
        reset_after=reset_after,
    )
    breaker = QuotaCircuitBreaker(
        args.output_root / "quota_ledger.jsonl",
        policy=policy,
        pause_path=args.output_root / "PAUSED_QUOTA.json",
        model_tier="recovery_sequence_diagnostic",
    )
    env_file = args.env_file or _default_gemini_env()
    base_config = _load_gemini_config(env_file, expected_model_version=args.expected_model)
    gemini_config = dataclasses.replace(
        base_config,
        model=args.model,
        expected_model_version=args.expected_model,
        quota_controller=breaker,
    )
    limiter = RateLimiter(args.qps, max_in_flight=0)
    print_lock = threading.Lock()

    def run_one(item: dict) -> dict:
        pilot_index = int(item["pilot_index"])
        result_path = results_dir / f"{pilot_index:03d}.json"
        if result_path.exists():
            return json.loads(result_path.read_text(encoding="utf-8"))
        anchor_id = item["anchor_id"]
        manifest = manifest_by_anchor[anchor_id]
        blind = blind_rows[int(manifest["blind_index"])]
        dates = list(manifest["frame_dates"])
        picks = [
            SequenceDatePick(
                date_index=index,
                chip_path=frame_paths[(anchor_id, date)],
                capture_date=date,
                version="corrected_ct11_diagnostic",
            )
            for index, date in enumerate(dates, start=1)
        ]
        if not all(pick.chip_path.is_file() for pick in picks):
            raise FileNotFoundError(f"missing corrected frame for {anchor_id}")
        audit_path = audit_dir / f"{pilot_index:03d}.jsonl"

        def audit_writer(payload: dict) -> None:
            with audit_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(payload, ensure_ascii=False) + "\n")

        sequence = score_single_target_sequence(
            picks,
            config=gemini_config,
            audit_writer=audit_writer,
            limiter=limiter,
            routing_salt=f"ct11-corrected-sequence:{anchor_id}",
        )
        predicted_class, latest_absent, earliest_present = classify_sequence(sequence)
        result = {
            "pilot_index": pilot_index,
            "anchor_id": anchor_id,
            "blind_index": int(manifest["blind_index"]),
            "model": args.model,
            "expected_model": args.expected_model,
            "frame_dates": dates,
            "predicted_class": predicted_class,
            "predicted_latest_absent": latest_absent,
            "predicted_earliest_present": earliest_present,
            "corrected_class": blind["independent_class"],
            "corrected_latest_absent": blind["corrected_latest_absent"],
            "corrected_earliest_present": blind["corrected_earliest_present"],
            "corrected_class_match": predicted_class == blind["independent_class"],
            "corrected_interval_match": (
                predicted_class == blind["independent_class"]
                and latest_absent == blind["corrected_latest_absent"]
                and earliest_present == blind["corrected_earliest_present"]
            ),
            "sequence": dataclasses.asdict(sequence),
        }
        result_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        with print_lock:
            print(
                f"[{pilot_index:03d}] corrected={blind['independent_class']} "
                f"sequence={predicted_class} match={result['corrected_class_match']}",
                flush=True,
            )
        return result

    results: list[dict] = []
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
        "purpose": "diagnostic_only_not_acceptance",
        "n": len(results),
        "model": args.model,
        "qps": args.qps,
        "max_in_flight": 0,
        "corrected_class_matches": class_matches,
        "corrected_class_agreement": class_matches / len(results),
        "corrected_interval_matches": interval_matches,
        "corrected_interval_agreement": interval_matches / len(results),
        "quota": breaker.summary(),
    }
    (args.output_root / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    write_hash_manifest(args.output_root)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
