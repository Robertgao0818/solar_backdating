#!/usr/bin/env python3
"""Arithmetic verdicts for the student revival paths — no discretion.

Implements the locked bars from
docs/dinov3_scorer/DATA-student-revival-paths-2026-07-10.md:

  anchor_pair_r1   — v1 audit record (DATA-anchor-pair-pilot-prereg): raw-count
                     dual >=30% FP/FN (Arm A vs B), overall no regression,
                     unusable recall within 2pp. Do not reuse for v2.
  anchor_pair_v2_r1 — pairing-v2 (DATA-anchor-pair-v2-prereg): Arm V (Vexcel
                     present) vs param-matched B; rate-normalized dual >=30%
                     with CI low >0; overall no regression; present->unusable
                     guard; unusable-recall guard; param match +/-10%; n_seeds
                     >=3. GO licenses gate-2 re-run only.
  anchor_pair_a2_r1 — pairing-A″ (DATA-anchor-pair-a2-same-sensor-prereg):
                     Arm P (latest GEHI present) vs param-matched B; same
                     rate/CI/guard structure as v2 with _p key suffix.
  frame_bar        — advisory investment stop-loss: decided-agreement >= 0.90
                     on BOTH the transition band and overall.
  sequence_head_r1 — path-B pilot: pooled multi-seed interval lift with a
                     positive paired-cluster CI, dual transition FP/FN rate
                     reductions, no overall regression or abstention escape,
                     and parameter matching within 10%.

Input is a flat JSON metrics file; missing keys fail the rule that needs
them (fail-closed). Exit code 0 iff every requested rule passes.

Metrics keys:
  anchor_pair_r1: transition_fp_reduction_pct, transition_fn_reduction_pct,
                  overall_decided_agreement_a, overall_decided_agreement_b,
                  unusable_recall_a, unusable_recall_b
  anchor_pair_v2_r1:
                  transition_fp_rate_reduction_pct,
                  transition_fp_rate_reduction_ci_low_pct,
                  transition_fn_rate_reduction_pct,
                  transition_fn_rate_reduction_ci_low_pct,
                  overall_decided_agreement_v, overall_decided_agreement_b,
                  present_to_unusable_rate_v, present_to_unusable_rate_b,
                  unusable_recall_v, unusable_recall_b,
                  arm_v_params, arm_b_params, n_seeds
  anchor_pair_a2_r1:
                  transition_fp_rate_reduction_pct,
                  transition_fp_rate_reduction_ci_low_pct,
                  transition_fn_rate_reduction_pct,
                  transition_fn_rate_reduction_ci_low_pct,
                  overall_decided_agreement_p, overall_decided_agreement_b,
                  present_to_unusable_rate_p, present_to_unusable_rate_b,
                  unusable_recall_p, unusable_recall_b,
                  arm_p_params, arm_b_params, n_seeds
  frame_bar:      transition_decided_agreement, overall_decided_agreement
  sequence_head_r1: interval_agreement_delta,
                    interval_agreement_delta_ci_low,
                    transition_fp_reduction_pct,
                    transition_fp_reduction_ci_low_pct,
                    transition_fn_reduction_pct,
                    transition_fn_reduction_ci_low_pct,
                    overall_decided_agreement_a,
                    overall_decided_agreement_b,
                    present_to_unusable_rate_a,
                    present_to_unusable_rate_b,
                    temporal_params, control_params, n_seeds

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
PRESENT_TO_UNUSABLE_TOLERANCE = 0.02
PARAMETER_MATCH_TOLERANCE = 0.10
MIN_SEQUENCE_SEEDS = 3
MIN_PAIR_V2_SEEDS = 3
MIN_PAIR_A2_SEEDS = 3

# Path C0 (ISSUE-09, DATA-c0-reverse-template-prereg-2026-07-12) — training-free.
C0_SMOKE_AUC_MIN = 0.75
C0_SMOKE_MIN_ANCHORS = 60
C0_REPLICATE_EQUIV_MIN_DELTA_PP = -5.0
C0_ADJUDICATION_SHARE_THRESHOLD = 1.0 / 3.0
# Amendment 2026-07-16 (prereg + ISSUE-09-c0-final-review-2026-07-16): rule 2
# is a Bayesian posterior gate, not a point estimate — P(p > 1/3 | k, n) with a
# Jeffreys Beta(1/2, 1/2) prior must clear 0.95. At the old n=40 a point
# estimate of 14/40 could pass on noise; the posterior gate at n>=150 passes
# from an observed win share of ~0.40 (k=60/150 -> 0.957).
C0_ADJUDICATION_MIN_N = 150
C0_ADJUDICATION_POSTERIOR_MIN = 0.95
C0_ADJUDICATION_PRIOR_ALPHA = 0.5
C0_ADJUDICATION_PRIOR_BETA = 0.5
C0_ADJUDICATION_SHARE_CONSISTENCY_TOL = 0.005
C0_SAFETY_POLARITY_MAX_RATE = 0.005


def _beta_posterior_above(threshold: float, k: float, n: float) -> float:
    """P(p > threshold | k successes of n), Beta-Binomial with Jeffreys prior."""
    from scipy.stats import beta  # lazy: only the c0_r1 rule needs scipy

    return float(
        1.0
        - beta.cdf(
            threshold,
            C0_ADJUDICATION_PRIOR_ALPHA + k,
            C0_ADJUDICATION_PRIOR_BETA + (n - k),
        )
    )


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
        (
            fp >= FP_FN_REDUCTION_MIN_PCT,
            f"transition FP reduction {fp:.1f}% >= {FP_FN_REDUCTION_MIN_PCT:.0f}%",
        ),
        (
            fn >= FP_FN_REDUCTION_MIN_PCT,
            f"transition FN reduction {fn:.1f}% >= {FP_FN_REDUCTION_MIN_PCT:.0f}%",
        ),
        (agree_a >= agree_b, f"overall decided-agreement A {agree_a:.4f} >= B {agree_b:.4f}"),
        (
            rec_a >= rec_b - UNUSABLE_RECALL_TOLERANCE,
            f"unusable recall A {rec_a:.4f} >= B {rec_b:.4f} - {UNUSABLE_RECALL_TOLERANCE}",
        ),
    ]
    ok = all(passed for passed, _ in checks)
    lines = [("PASS  " if passed else "FAIL  ") + desc for passed, desc in checks]
    lines.append(
        "verdict: GO (licenses gate-2 re-run only)"
        if ok
        else "verdict: KILL (one-sided or no win — oracle rules out partial credit)"
    )
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
    lines.append(
        "verdict: investment bar cleared"
        if ok
        else "verdict: STOP — below ~0.90 frames, gate-2 re-runs are foreseeable-FAIL"
    )
    return ok, lines


def check_anchor_pair_v2_r1(metrics: dict) -> tuple[bool, list[str]]:
    """Arm V (Vexcel present) vs param-matched B — rate-normalized multi-seed."""
    missing: list[str] = []
    fp = _get(metrics, "transition_fp_rate_reduction_pct", missing)
    fp_ci = _get(metrics, "transition_fp_rate_reduction_ci_low_pct", missing)
    fn = _get(metrics, "transition_fn_rate_reduction_pct", missing)
    fn_ci = _get(metrics, "transition_fn_rate_reduction_ci_low_pct", missing)
    agree_v = _get(metrics, "overall_decided_agreement_v", missing)
    agree_b = _get(metrics, "overall_decided_agreement_b", missing)
    ptu_v = _get(metrics, "present_to_unusable_rate_v", missing)
    ptu_b = _get(metrics, "present_to_unusable_rate_b", missing)
    rec_v = _get(metrics, "unusable_recall_v", missing)
    rec_b = _get(metrics, "unusable_recall_b", missing)
    params_v = _get(metrics, "arm_v_params", missing)
    params_b = _get(metrics, "arm_b_params", missing)
    n_seeds = _get(metrics, "n_seeds", missing)
    if missing:
        return False, [f"FAIL-CLOSED missing keys: {', '.join(missing)}"]

    param_ratio = params_v / params_b if params_b > 0 else float("inf")
    checks = [
        (
            fp >= FP_FN_REDUCTION_MIN_PCT,
            f"TB FP-rate reduction {fp:.1f}% >= {FP_FN_REDUCTION_MIN_PCT:.0f}%",
        ),
        (fp_ci > 0.0, f"TB FP-rate reduction 95% CI low {fp_ci:.1f}% > 0%"),
        (
            fn >= FP_FN_REDUCTION_MIN_PCT,
            f"TB FN-rate reduction {fn:.1f}% >= {FP_FN_REDUCTION_MIN_PCT:.0f}%",
        ),
        (fn_ci > 0.0, f"TB FN-rate reduction 95% CI low {fn_ci:.1f}% > 0%"),
        (
            agree_v >= agree_b,
            f"overall decided-agreement V {agree_v:.4f} >= B {agree_b:.4f}",
        ),
        (
            ptu_v <= ptu_b + PRESENT_TO_UNUSABLE_TOLERANCE,
            f"present->unusable V {ptu_v:.4f} <= B {ptu_b:.4f} + {PRESENT_TO_UNUSABLE_TOLERANCE}",
        ),
        (
            rec_v >= rec_b - UNUSABLE_RECALL_TOLERANCE,
            f"unusable recall V {rec_v:.4f} >= B {rec_b:.4f} - {UNUSABLE_RECALL_TOLERANCE}",
        ),
        (
            abs(param_ratio - 1.0) <= PARAMETER_MATCH_TOLERANCE,
            f"parameter ratio V/B {param_ratio:.4f} within +/-{PARAMETER_MATCH_TOLERANCE:.0%}",
        ),
        (n_seeds >= MIN_PAIR_V2_SEEDS, f"pooled seeds {n_seeds:.0f} >= {MIN_PAIR_V2_SEEDS}"),
    ]
    ok = all(passed for passed, _ in checks)
    lines = [("PASS  " if passed else "FAIL  ") + desc for passed, desc in checks]
    lines.append(
        "verdict: GO (licenses gate-2 re-run only)"
        if ok
        else "verdict: KILL (pairing-v2 R2 bar not cleared)"
    )
    return ok, lines


def check_anchor_pair_a2_r1(metrics: dict) -> tuple[bool, list[str]]:
    """Arm P (latest same-sensor present) vs param-matched B — rate multi-seed."""
    missing: list[str] = []
    fp = _get(metrics, "transition_fp_rate_reduction_pct", missing)
    fp_ci = _get(metrics, "transition_fp_rate_reduction_ci_low_pct", missing)
    fn = _get(metrics, "transition_fn_rate_reduction_pct", missing)
    fn_ci = _get(metrics, "transition_fn_rate_reduction_ci_low_pct", missing)
    agree_p = _get(metrics, "overall_decided_agreement_p", missing)
    agree_b = _get(metrics, "overall_decided_agreement_b", missing)
    ptu_p = _get(metrics, "present_to_unusable_rate_p", missing)
    ptu_b = _get(metrics, "present_to_unusable_rate_b", missing)
    rec_p = _get(metrics, "unusable_recall_p", missing)
    rec_b = _get(metrics, "unusable_recall_b", missing)
    params_p = _get(metrics, "arm_p_params", missing)
    params_b = _get(metrics, "arm_b_params", missing)
    n_seeds = _get(metrics, "n_seeds", missing)
    if missing:
        return False, [f"FAIL-CLOSED missing keys: {', '.join(missing)}"]

    param_ratio = params_p / params_b if params_b > 0 else float("inf")
    checks = [
        (
            fp >= FP_FN_REDUCTION_MIN_PCT,
            f"TB FP-rate reduction {fp:.1f}% >= {FP_FN_REDUCTION_MIN_PCT:.0f}%",
        ),
        (fp_ci > 0.0, f"TB FP-rate reduction 95% CI low {fp_ci:.1f}% > 0%"),
        (
            fn >= FP_FN_REDUCTION_MIN_PCT,
            f"TB FN-rate reduction {fn:.1f}% >= {FP_FN_REDUCTION_MIN_PCT:.0f}%",
        ),
        (fn_ci > 0.0, f"TB FN-rate reduction 95% CI low {fn_ci:.1f}% > 0%"),
        (
            agree_p >= agree_b,
            f"overall decided-agreement P {agree_p:.4f} >= B {agree_b:.4f}",
        ),
        (
            ptu_p <= ptu_b + PRESENT_TO_UNUSABLE_TOLERANCE,
            f"present->unusable P {ptu_p:.4f} <= B {ptu_b:.4f} + {PRESENT_TO_UNUSABLE_TOLERANCE}",
        ),
        (
            rec_p >= rec_b - UNUSABLE_RECALL_TOLERANCE,
            f"unusable recall P {rec_p:.4f} >= B {rec_b:.4f} - {UNUSABLE_RECALL_TOLERANCE}",
        ),
        (
            abs(param_ratio - 1.0) <= PARAMETER_MATCH_TOLERANCE,
            f"parameter ratio P/B {param_ratio:.4f} within +/-{PARAMETER_MATCH_TOLERANCE:.0%}",
        ),
        (n_seeds >= MIN_PAIR_A2_SEEDS, f"pooled seeds {n_seeds:.0f} >= {MIN_PAIR_A2_SEEDS}"),
    ]
    ok = all(passed for passed, _ in checks)
    lines = [("PASS  " if passed else "FAIL  ") + desc for passed, desc in checks]
    lines.append(
        "verdict: GO (licenses gate-2 re-run only)"
        if ok
        else "verdict: KILL (pairing-A2 R3 bar not cleared)"
    )
    return ok, lines


def check_sequence_head_r1(metrics: dict) -> tuple[bool, list[str]]:
    missing: list[str] = []
    interval_delta = _get(metrics, "interval_agreement_delta", missing)
    interval_ci_low = _get(metrics, "interval_agreement_delta_ci_low", missing)
    fp_reduction = _get(metrics, "transition_fp_reduction_pct", missing)
    fp_ci_low = _get(metrics, "transition_fp_reduction_ci_low_pct", missing)
    fn_reduction = _get(metrics, "transition_fn_reduction_pct", missing)
    fn_ci_low = _get(metrics, "transition_fn_reduction_ci_low_pct", missing)
    agree_a = _get(metrics, "overall_decided_agreement_a", missing)
    agree_b = _get(metrics, "overall_decided_agreement_b", missing)
    ptu_a = _get(metrics, "present_to_unusable_rate_a", missing)
    ptu_b = _get(metrics, "present_to_unusable_rate_b", missing)
    temporal_params = _get(metrics, "temporal_params", missing)
    control_params = _get(metrics, "control_params", missing)
    n_seeds = _get(metrics, "n_seeds", missing)
    if missing:
        return False, [f"FAIL-CLOSED missing keys: {', '.join(missing)}"]

    parameter_ratio = temporal_params / control_params if control_params > 0 else float("inf")
    checks = [
        (interval_delta > 0.0, f"interval agreement delta {interval_delta:.4f} > 0"),
        (interval_ci_low > 0.0, f"interval agreement delta 95% CI low {interval_ci_low:.4f} > 0"),
        (
            fp_reduction >= FP_FN_REDUCTION_MIN_PCT,
            f"transition FP-rate reduction {fp_reduction:.1f}% >= {FP_FN_REDUCTION_MIN_PCT:.0f}%",
        ),
        (fp_ci_low > 0.0, f"transition FP-rate reduction 95% CI low {fp_ci_low:.1f}% > 0%"),
        (
            fn_reduction >= FP_FN_REDUCTION_MIN_PCT,
            f"transition FN-rate reduction {fn_reduction:.1f}% >= {FP_FN_REDUCTION_MIN_PCT:.0f}%",
        ),
        (fn_ci_low > 0.0, f"transition FN-rate reduction 95% CI low {fn_ci_low:.1f}% > 0%"),
        (agree_a >= agree_b, f"overall decided-agreement A {agree_a:.4f} >= B {agree_b:.4f}"),
        (
            ptu_a <= ptu_b + PRESENT_TO_UNUSABLE_TOLERANCE,
            f"present->unusable A {ptu_a:.4f} <= B {ptu_b:.4f} + {PRESENT_TO_UNUSABLE_TOLERANCE}",
        ),
        (
            abs(parameter_ratio - 1.0) <= PARAMETER_MATCH_TOLERANCE,
            f"parameter ratio A/B {parameter_ratio:.4f} within +/-{PARAMETER_MATCH_TOLERANCE:.0%}",
        ),
        (n_seeds >= MIN_SEQUENCE_SEEDS, f"pooled seeds {n_seeds:.0f} >= {MIN_SEQUENCE_SEEDS}"),
    ]
    ok = all(passed for passed, _ in checks)
    lines = [("PASS  " if passed else "FAIL  ") + desc for passed, desc in checks]
    lines.append(
        "verdict: GO (licenses gate-2 re-run only)"
        if ok
        else "verdict: KILL (path-B prereg bar not cleared)"
    )
    return ok, lines


def check_c0_s0(metrics: dict) -> tuple[bool, list[str]]:
    """Path C0 Stage-0 smoke: usable step signal in frozen features at all?"""
    missing: list[str] = []
    auc = _get(metrics, "c0_smoke_auc", missing)
    n_anchors = _get(metrics, "c0_smoke_n_anchors", missing)
    if missing:
        return False, [f"FAIL-CLOSED missing keys: {', '.join(missing)}"]

    checks = [
        (auc >= C0_SMOKE_AUC_MIN, f"smoke AUC {auc:.4f} >= {C0_SMOKE_AUC_MIN}"),
        (
            n_anchors >= C0_SMOKE_MIN_ANCHORS,
            f"transition anchors {n_anchors:.0f} >= {C0_SMOKE_MIN_ANCHORS}",
        ),
    ]
    ok = all(passed for passed, _ in checks)
    lines = [("PASS  " if passed else "FAIL  ") + desc for passed, desc in checks]
    lines.append(
        "verdict: GO (C0 main paired eval may proceed)"
        if ok
        else "verdict: KILL (C0-S0 — no usable frozen-feature step signal)"
    )
    return ok, lines


def check_c0_r1(metrics: dict) -> tuple[bool, list[str]]:
    """Path C0 main paired-eval bars (vs FRESH Gemini round, post-rebuild).

    Rule 2 amended 2026-07-16: the adjudication bar is a posterior gate
    `P(p_C0 > 1/3 | k, n) >= 0.95` (Jeffreys prior) on the integer win count
    `c0_adjudication_correct_n`, with the minimum sample raised to 150. The
    reported share is cross-checked against k/n and fails closed on mismatch.
    """
    missing: list[str] = []
    delta = _get(metrics, "c0_replicate_equiv_delta_pp", missing)
    delta_ci_low = _get(metrics, "c0_replicate_equiv_delta_ci_low_pp", missing)
    adj_share = _get(metrics, "c0_adjudication_correct_share", missing)
    adj_k = _get(metrics, "c0_adjudication_correct_n", missing)
    adj_n = _get(metrics, "c0_adjudication_n", missing)
    safety_rate = _get(metrics, "c0_safety_polarity_rate", missing)
    safety_inspected = _get(metrics, "c0_safety_all_inspected", missing)
    if missing:
        return False, [f"FAIL-CLOSED missing keys: {', '.join(missing)}"]
    if not (adj_n > 0 and 0 <= adj_k <= adj_n):
        return False, [
            f"FAIL-CLOSED invalid adjudication counts: k={adj_k}, n={adj_n}"
        ]
    if abs(adj_share - adj_k / adj_n) > C0_ADJUDICATION_SHARE_CONSISTENCY_TOL:
        return False, [
            f"FAIL-CLOSED adjudication share {adj_share:.4f} inconsistent with "
            f"k/n = {adj_k:.0f}/{adj_n:.0f} = {adj_k / adj_n:.4f}"
        ]

    posterior = _beta_posterior_above(C0_ADJUDICATION_SHARE_THRESHOLD, adj_k, adj_n)
    checks = [
        (
            delta_ci_low >= C0_REPLICATE_EQUIV_MIN_DELTA_PP,
            f"replicate-equivalence delta {delta:.2f}pp, CI low {delta_ci_low:.2f}pp"
            f" >= {C0_REPLICATE_EQUIV_MIN_DELTA_PP}pp",
        ),
        (
            posterior >= C0_ADJUDICATION_POSTERIOR_MIN,
            f"adjudication posterior P(p > 1/3 | {adj_k:.0f}/{adj_n:.0f}) = "
            f"{posterior:.4f} >= {C0_ADJUDICATION_POSTERIOR_MIN} "
            f"(share {adj_share:.3f}, Jeffreys prior)",
        ),
        (
            adj_n >= C0_ADJUDICATION_MIN_N,
            f"adjudicated disagreements {adj_n:.0f} >= {C0_ADJUDICATION_MIN_N}",
        ),
        (
            safety_rate <= C0_SAFETY_POLARITY_MAX_RATE,
            f"post-anchor present->absent polarity rate {safety_rate:.4f} <= "
            f"{C0_SAFETY_POLARITY_MAX_RATE}",
        ),
        (
            safety_inspected >= 1.0,
            "every polarity violation individually inspected",
        ),
    ]
    ok = all(passed for passed, _ in checks)
    lines = [("PASS  " if passed else "FAIL  ") + desc for passed, desc in checks]
    lines.append(
        "verdict: GO (C0 replicate-equivalent to fresh Gemini)"
        if ok
        else "verdict: KILL (C0-R1 — paired-eval bar not cleared)"
    )
    return ok, lines


RULES = {
    "anchor_pair_r1": check_anchor_pair_r1,
    "anchor_pair_v2_r1": check_anchor_pair_v2_r1,
    "anchor_pair_a2_r1": check_anchor_pair_a2_r1,
    "frame_bar": check_frame_bar,
    "sequence_head_r1": check_sequence_head_r1,
    "c0_s0": check_c0_s0,
    "c0_r1": check_c0_r1,
}


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
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
