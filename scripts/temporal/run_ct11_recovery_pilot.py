#!/usr/bin/env python3
"""Re-score a frozen CT-11 diagnostic subset with the recovery Gemini tier.

This is a diagnostic pilot, not a mutation of production scan states or the
frozen CT-11 verdicts.  It uses strict global start-rate pacing and a
fail-closed quota ledger.  Selection is balanced over the frozen Codex blind
classes and is persisted before any recovery response is read.
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
from pathlib import Path

from scripts.temporal.quota_control import QuotaCircuitBreaker, QuotaPolicy
from scripts.temporal.run_adaptive_scan import _default_gemini_env, _load_gemini_config
from scripts.validation.gemini_solar_image_review import (
    BatchPick,
    RateLimiter,
    score_batch_with_fallback,
)


CLASSES = ("ALREADY_PRESENT", "ALL_ABSENT", "TRANSITION", "UNDATABLE")


def classify_observations(observations, dates: list[str]) -> tuple[str, str, str]:
    usable = [
        (date, obs.pv_present)
        for date, obs in zip(dates, observations)
        if obs.quality_flag == "usable" and obs.pv_present is not None
    ]
    if not usable:
        return "UNDATABLE", "", ""
    labels = [label for _, label in usable]
    if all(labels):
        return "ALREADY_PRESENT", "", usable[0][0]
    if not any(labels):
        return "ALL_ABSENT", usable[-1][0], ""
    first_present = next(index for index, label in enumerate(labels) if label)
    if any(not label for label in labels[first_present + 1 :]):
        return "UNDATABLE", "", ""
    latest_absent = usable[first_present - 1][0]
    earliest_present = usable[first_present][0]
    return "TRANSITION", latest_absent, earliest_present


def choose_balanced(rows: list[dict], *, n_per_class: int, seed: int) -> list[dict]:
    rng = random.Random(seed)
    selected: list[dict] = []
    for blind_class in CLASSES:
        eligible = sorted(
            (row for row in rows if row["blind_class"] == blind_class),
            key=lambda row: row["anchor_id"],
        )
        if len(eligible) < n_per_class:
            raise ValueError(f"{blind_class} has only {len(eligible)} rows")
        selected.extend(rng.sample(eligible, n_per_class))
    rng.shuffle(selected)
    return selected


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="") as handle:
        return list(csv.DictReader(handle))


def write_hash_manifest(output_root: Path) -> Path:
    paths = [output_root / "selection.json", output_root / "summary.json", output_root / "quota_ledger.jsonl"]
    paths.extend(sorted((output_root / "results").glob("*.json")))
    paths.extend(sorted((output_root / "audit").glob("*.jsonl")))
    lines = []
    for path in paths:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        lines.append(f"{digest}  {path.relative_to(output_root)}")
    manifest = output_root / "pilot_outputs.sha256"
    manifest.write_text("\n".join(lines) + "\n")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qa-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--env-file", type=Path, default=None)
    parser.add_argument("--model", default="gemini-3-flash")
    parser.add_argument("--expected-model", default="gemini-3-flash")
    parser.add_argument("--n-per-class", type=int, default=10)
    parser.add_argument("--seed", type=int, default=20260803)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--qps", type=float, default=6.0)
    parser.add_argument("--window-id", required=True)
    parser.add_argument("--reset-after", required=True)
    args = parser.parse_args()
    args.output_root.mkdir(parents=True, exist_ok=True)
    results_dir = args.output_root / "results"
    audit_dir = args.output_root / "audit"
    results_dir.mkdir(exist_ok=True)
    audit_dir.mkdir(exist_ok=True)

    qa_rows = read_csv(args.qa_root / "ct11_enriched_verdicts_20260803.csv")
    by_anchor = {row["anchor_id"]: row for row in qa_rows}
    blind_raw = {
        int(row["blind_index"]): row
        for row in read_csv(args.qa_root / "blind_pass1_independent.csv")
    }
    manifest_rows = json.loads((args.qa_root / "blind_manifest_pass1.json").read_text())
    manifest_by_anchor = {row["anchor_id"]: row for row in manifest_rows}
    frame_rows = read_csv(args.qa_root / "rerender" / "frame_report.csv")
    frame_paths = {
        (row["anchor_id"], row["capture_date"]): Path(row["chip_path"])
        for row in frame_rows
        if row["source"] == "scan_tm" and row["status"] in {"ok", "skipped_existing"}
    }

    selection_path = args.output_root / "selection.json"
    if selection_path.exists():
        selection = json.loads(selection_path.read_text())
    else:
        chosen = choose_balanced(qa_rows, n_per_class=args.n_per_class, seed=args.seed)
        selection = {
            "schema_version": 1,
            "purpose": "outcome-balanced CT-11 recovery-model diagnosis",
            "seed": args.seed,
            "n_per_class": args.n_per_class,
            "n_anchors": len(chosen),
            "anchors": [
                {
                    "pilot_index": index,
                    "anchor_id": row["anchor_id"],
                    "blind_index": int(row["blind_index"]),
                    "blind_class": row["blind_class"],
                }
                for index, row in enumerate(chosen, start=1)
            ],
        }
        selection_path.write_text(json.dumps(selection, indent=2) + "\n")
    expected_n = len(CLASSES) * args.n_per_class
    if selection.get("n_anchors") != expected_n or len(selection.get("anchors", [])) != expected_n:
        raise SystemExit("existing selection does not match requested balanced pilot size")

    env_file = args.env_file or _default_gemini_env()
    base_config = _load_gemini_config(env_file, expected_model_version=args.expected_model)
    policy = QuotaPolicy(
        window_id=args.window_id,
        gross_safe_budget=max(80, expected_n * 2),
        work_budget=max(60, expected_n + 10),
        warning_budget=max(50, expected_n),
        canary_reserve=0,
        retry_reserve=20,
        reset_after=args.reset_after,
    )
    breaker = QuotaCircuitBreaker(
        args.output_root / "quota_ledger.jsonl",
        policy=policy,
        pause_path=args.output_root / "PAUSED_QUOTA.json",
        model_tier="recovery",
    )
    config = dataclasses.replace(
        base_config,
        model=args.model,
        expected_model_version=args.expected_model,
        quota_controller=breaker,
    )
    limiter = RateLimiter(args.qps, max_in_flight=0)
    print_lock = threading.Lock()

    def run_one(item: dict) -> dict:
        anchor_id = item["anchor_id"]
        output_path = results_dir / f"{int(item['pilot_index']):03d}.json"
        if output_path.exists():
            return json.loads(output_path.read_text())
        manifest = manifest_by_anchor[anchor_id]
        dates = list(manifest["frame_dates"])
        paths = [frame_paths[(anchor_id, date)] for date in dates]
        if not all(path.is_file() for path in paths):
            raise FileNotFoundError(f"pilot frames missing for {anchor_id}")
        picks = [
            BatchPick(chip_index=index, chip_path=path, capture_date=date, version="diagnostic")
            for index, (path, date) in enumerate(zip(paths, dates), start=1)
        ]
        audit_path = audit_dir / f"{int(item['pilot_index']):03d}.jsonl"

        def audit_writer(payload: dict) -> None:
            with audit_path.open("a") as handle:
                handle.write(json.dumps(payload, ensure_ascii=False) + "\n")

        observations = score_batch_with_fallback(
            picks,
            config=config,
            audit_writer=audit_writer,
            limiter=limiter,
            attempt_context={
                "anchor_id": anchor_id,
                "run_id": "ct11_recovery_pilot_20260803",
                "wave_id": "diagnostic",
                "round_id": 1,
                "round_type": "ct11_strip_rescore",
                "chunk_index": 1,
                "n_picks": len(picks),
                "requested_alias": args.model,
                "model_tier": "recovery",
                "logical_call_id": f"ct11-recovery:{anchor_id}",
            },
        )
        predicted_class, latest_absent, earliest_present = classify_observations(observations, dates)
        qa = by_anchor[anchor_id]
        raw = blind_raw[int(qa["blind_index"])]
        result = {
            **item,
            "model": args.model,
            "expected_model": args.expected_model,
            "frame_dates": dates,
            "predicted_class": predicted_class,
            "predicted_latest_absent": latest_absent,
            "predicted_earliest_present": earliest_present,
            "codex_class_match": predicted_class == qa["blind_class"],
            "codex_interval_match": (
                predicted_class == qa["blind_class"]
                and latest_absent == raw["corrected_latest_absent"]
                and earliest_present == raw["corrected_earliest_present"]
            ),
            "observations": [dataclasses.asdict(obs) for obs in observations],
        }
        output_path.write_text(json.dumps(result, indent=2) + "\n")
        with print_lock:
            print(
                f"[{int(item['pilot_index']):03d}/{expected_n}] "
                f"blind={qa['blind_class']} recovery={predicted_class} "
                f"match={result['codex_class_match']}",
                flush=True,
            )
        return result

    results = []
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = [executor.submit(run_one, item) for item in selection["anchors"]]
        for future in as_completed(futures):
            results.append(future.result())
    results.sort(key=lambda row: int(row["pilot_index"]))
    class_matches = sum(bool(row["codex_class_match"]) for row in results)
    interval_matches = sum(bool(row["codex_interval_match"]) for row in results)
    summary = {
        "schema_version": 1,
        "status": "complete",
        "n": len(results),
        "model": args.model,
        "expected_model": args.expected_model,
        "qps": args.qps,
        "max_in_flight": 0,
        "workers": args.workers,
        "codex_class_agreement": class_matches / len(results),
        "codex_class_matches": class_matches,
        "codex_interval_agreement": interval_matches / len(results),
        "codex_interval_matches": interval_matches,
        "quota": breaker.summary(),
    }
    (args.output_root / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    write_hash_manifest(args.output_root)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
