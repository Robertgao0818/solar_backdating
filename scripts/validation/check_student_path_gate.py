#!/usr/bin/env python3
"""Arithmetic verdicts for the student revival paths — no discretion.

Implements the two locked bars from
docs/dinov3_scorer/DATA-student-revival-paths-2026-07-10.md:

  anchor_pair_r1  — verbatim R1 from DATA-anchor-pair-pilot-prereg-2026-07-10:
                    GO iff transition-band FP down >=30% AND FN down >=30%
                    (Arm A vs matched-capacity Arm B), AND overall
                    decided-agreement(A) >= (B), AND unusable recall(A) >=
                    recall(B) - 2pp. One-sided wins are KILL, not partial
                    credit (dual-fail oracle: FP-only fixes cannot clear the
                    ceiling).
  frame_bar       — advisory investment stop-loss: decided-agreement >= 0.90
                    on BOTH the transition band and overall. Gates further
                    investment, not the gate-2 verdict itself.

Input is a flat JSON metrics file; missing keys fail the rule that needs
them (fail-closed). Exit code 0 iff every requested rule passes.

Metrics keys:
  anchor_pair_r1: transition_fp_reduction_pct, transition_fn_reduction_pct,
                  overall_decided_agreement_a, overall_decided_agreement_b,
                  unusable_recall_a, unusable_recall_b
  frame_bar:      transition_decided_agreement, overall_decided_agreement

Usage:
  python scripts/validation/check_student_path_gate.py \
    --metrics pilot_metrics.json --rule all
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

FRAME_BAR = 0.90
FP_FN_REDUCTION_MIN_PCT = 30.0
UNUSABLE_RECALL_TOLERANCE = 0.02


def _get(metrics: dict, key: str, missing: list[str]) -> float | None:
    if key not in metrics:
        missing.append(key)
        return None
    return float(metrics[key])


def check_anchor_pair_r1(metrics: dict) -> tuple[bool, list[str]]:
    lines: list[str] = []
    missing: list[str] = []
    fp = _get(metrics, "transition_fp_reduction_pct", missing)
    fn = _get(metrics, "transition_fn_reduction_pct", missing)
    agree_a = _get(metrics, "overall_decided_agreement_a", missing)
    agree_b = _get(metrics, "overall_decided_agreement_b", missing)
    rec_a = _get(metrics, "unusable_recall_a", missing)
    rec_b = _get(metrics, "unusable_recall_b", missing)
    if missing:
        return False, [f"FAIL-CLOSED missing keys: {', '.join(missing)}"]

    checks = [
        (fp >= FP_FN_REDUCTION_MIN_PCT,
         f"transition FP reduction {fp:.1f}% >= {FP_FN_REDUCTION_MIN_PCT:.0f}%"),
        (fn >= FP_FN_REDUCTION_MIN_PCT,
         f"transition FN reduction {fn:.1f}% >= {FP_FN_REDUCTION_MIN_PCT:.0f}%"),
        (agree_a >= agree_b,
         f"overall decided-agreement A {agree_a:.4f} >= B {agree_b:.4f}"),
        (rec_a >= rec_b - UNUSABLE_RECALL_TOLERANCE,
         f"unusable recall A {rec_a:.4f} >= B {rec_b:.4f} - {UNUSABLE_RECALL_TOLERANCE}"),
    ]
    ok = all(passed for passed, _ in checks)
    lines = [("PASS  " if passed else "FAIL  ") + desc for passed, desc in checks]
    lines.append("verdict: GO (licenses gate-2 re-run only)" if ok
                 else "verdict: KILL (one-sided or no win — oracle rules out partial credit)")
    return ok, lines


def check_frame_bar(metrics: dict) -> tuple[bool, list[str]]:
    missing: list[str] = []
    trans = _get(metrics, "transition_decided_agreement", missing)
    overall = _get(metrics, "overall_decided_agreement", missing)
    if missing:
        return False, [f"FAIL-CLOSED missing keys: {', '.join(missing)}"]
    checks = [
        (trans >= FRAME_BAR, f"transition-band decided-agreement {trans:.4f} >= {FRAME_BAR}"),
        (overall >= FRAME_BAR, f"overall decided-agreement {overall:.4f} >= {FRAME_BAR}"),
    ]
    ok = all(passed for passed, _ in checks)
    lines = [("PASS  " if passed else "FAIL  ") + desc for passed, desc in checks]
    lines.append("verdict: investment bar cleared" if ok
                 else "verdict: STOP — below ~0.90 frames, gate-2 re-runs are foreseeable-FAIL")
    return ok, lines


RULES = {
    "anchor_pair_r1": check_anchor_pair_r1,
    "frame_bar": check_frame_bar,
}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--metrics", type=Path, required=True, help="flat JSON metrics file")
    ap.add_argument("--rule", choices=[*RULES, "all"], default="all")
    args = ap.parse_args()

    metrics = json.loads(args.metrics.read_text())
    names = list(RULES) if args.rule == "all" else [args.rule]

    all_ok = True
    for name in names:
        ok, lines = RULES[name](metrics)
        all_ok &= ok
        print(f"[{name}]")
        for line in lines:
            print(f"  {line}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
