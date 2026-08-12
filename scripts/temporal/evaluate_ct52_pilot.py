#!/usr/bin/env python3
"""Evaluate the preregistered CT-06/07 control-vs-optimized pilot gate."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path


def _audit_summary(audit_dir: Path) -> dict[str, object]:
    stage_counts: Counter[str] = Counter()
    records = 0
    http_attempts = 0
    identity_errors = 0
    for path in audit_dir.rglob("*.jsonl"):
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            records += 1
            stage = str(row.get("stage", ""))
            stage_counts[stage] += 1
            http_attempts += 1 + int(row.get("transport_retries", 0) or 0)
            if (
                row.get("requested_alias") != "gemini-3.1-flash-lite"
                or row.get("returned_model_version") != "gemini-3.1-flash-lite"
            ):
                identity_errors += 1
    return {
        "audit_records": records,
        "stage_counts": dict(sorted(stage_counts.items())),
        "logical_batch_calls": stage_counts.get("batch_attempt_1", 0),
        "http_attempts": http_attempts,
        "identity_errors": identity_errors,
    }


def _state_summary(state_dir: Path) -> dict[str, object]:
    status: Counter[str] = Counter()
    initial_picks: Counter[str] = Counter()
    reference_picks = 0
    operational_errors = 0
    for path in state_dir.rglob("*.json"):
        raw = json.loads(path.read_text(encoding="utf-8"))
        status[str(raw.get("status", ""))] += 1
        if str(raw.get("status", "")).startswith("done_ambiguous_orchestrator_error"):
            operational_errors += 1
        initial = next((rnd for rnd in raw.get("rounds", []) if rnd.get("round_type") == "initial"), None)
        if initial is not None:
            picks = initial.get("picks", [])
            initial_picks[str(len(picks))] += 1
            reference_picks += sum(bool(pick.get("reference_only")) for pick in picks)
    return {
        "state_count": sum(status.values()),
        "status_counts": dict(sorted(status.items())),
        "initial_pick_counts": dict(sorted(initial_picks.items())),
        "reference_picks": reference_picks,
        "operational_errors": operational_errors,
    }


def evaluate(
    control_audit_dir: Path,
    optimized_audit_dir: Path,
    control_state_dir: Path,
    optimized_state_dir: Path,
    *,
    min_call_reduction: float = 0.25,
) -> dict[str, object]:
    control = {"audit": _audit_summary(control_audit_dir), "states": _state_summary(control_state_dir)}
    optimized = {"audit": _audit_summary(optimized_audit_dir), "states": _state_summary(optimized_state_dir)}
    control_calls = int(control["audit"]["logical_batch_calls"])
    optimized_calls = int(optimized["audit"]["logical_batch_calls"])
    reduction = (control_calls - optimized_calls) / control_calls if control_calls else 0.0
    operational_pass = all(
        int(arm["audit"]["identity_errors"]) == 0
        and int(arm["states"]["operational_errors"]) == 0
        for arm in (control, optimized)
    )
    optimized_pass = operational_pass and reduction >= min_call_reduction
    result = {
        "schema_version": "ct52_pilot_gate_v1",
        "thresholds": {"min_logical_call_reduction": min_call_reduction},
        "control": control,
        "optimized": optimized,
        "comparison": {
            "control_logical_calls": control_calls,
            "optimized_logical_calls": optimized_calls,
            "logical_call_reduction": reduction,
            "operational_pass": operational_pass,
            "optimized_pass": optimized_pass,
            "decision": "optimized_6slot" if optimized_pass else "fallback_control_5slot",
        },
    }
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--control-audit-dir", type=Path, required=True)
    parser.add_argument("--optimized-audit-dir", type=Path, required=True)
    parser.add_argument("--control-state-dir", type=Path, required=True)
    parser.add_argument("--optimized-state-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--min-logical-call-reduction", type=float, default=0.25)
    args = parser.parse_args()
    result = evaluate(
        args.control_audit_dir,
        args.optimized_audit_dir,
        args.control_state_dir,
        args.optimized_state_dir,
        min_call_reduction=args.min_logical_call_reduction,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True))
    raise SystemExit(0 if result["comparison"]["optimized_pass"] else 1)


if __name__ == "__main__":
    main()
