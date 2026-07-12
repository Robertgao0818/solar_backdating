"""Tests for the student-path arithmetic verdict checker."""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.validation.check_student_path_gate import (  # noqa: E402
    check_anchor_pair_a2_r1,
    check_anchor_pair_r1,
    check_anchor_pair_v2_r1,
    check_frame_bar,
    check_sequence_head_r1,
)

R1_GO = {
    "transition_fp_reduction_pct": 35.0,
    "transition_fn_reduction_pct": 31.0,
    "overall_decided_agreement_a": 0.86,
    "overall_decided_agreement_b": 0.84,
    "unusable_recall_a": 0.30,
    "unusable_recall_b": 0.31,
}

SEQUENCE_R1_GO = {
    "interval_agreement_delta": 0.12,
    "interval_agreement_delta_ci_low": 0.04,
    "transition_fp_reduction_pct": 35.0,
    "transition_fp_reduction_ci_low_pct": 8.0,
    "transition_fn_reduction_pct": 31.0,
    "transition_fn_reduction_ci_low_pct": 5.0,
    "overall_decided_agreement_a": 0.92,
    "overall_decided_agreement_b": 0.90,
    "present_to_unusable_rate_a": 0.08,
    "present_to_unusable_rate_b": 0.07,
    "temporal_params": 720_000,
    "control_params": 700_000,
    "n_seeds": 3,
}


def test_sequence_r1_go_requires_effect_ci_guard_and_matched_capacity():
    ok, lines = check_sequence_head_r1(SEQUENCE_R1_GO)
    assert ok
    assert any("GO" in line for line in lines)


def test_sequence_r1_kills_on_nonpositive_effect_ci():
    metrics = dict(SEQUENCE_R1_GO, interval_agreement_delta_ci_low=0.0)
    assert not check_sequence_head_r1(metrics)[0]


def test_sequence_r1_blocks_abstention_escape_capacity_mismatch_and_too_few_seeds():
    assert not check_sequence_head_r1(dict(SEQUENCE_R1_GO, present_to_unusable_rate_a=0.10))[0]
    assert not check_sequence_head_r1(dict(SEQUENCE_R1_GO, temporal_params=900_000))[0]
    assert not check_sequence_head_r1(dict(SEQUENCE_R1_GO, n_seeds=2))[0]


def test_sequence_r1_fails_closed_on_missing_key():
    metrics = dict(SEQUENCE_R1_GO)
    del metrics["transition_fn_reduction_ci_low_pct"]
    ok, lines = check_sequence_head_r1(metrics)
    assert not ok
    assert any(
        "FAIL-CLOSED" in line and "transition_fn_reduction_ci_low_pct" in line for line in lines
    )


def test_r1_go_when_all_criteria_met():
    ok, lines = check_anchor_pair_r1(R1_GO)
    assert ok
    assert any("GO" in line for line in lines)


def test_r1_kill_on_one_sided_fp_only_win():
    metrics = dict(R1_GO, transition_fn_reduction_pct=10.0)
    ok, lines = check_anchor_pair_r1(metrics)
    assert not ok
    assert any("KILL" in line for line in lines)


def test_r1_kill_on_overall_regression():
    metrics = dict(R1_GO, overall_decided_agreement_a=0.83)
    ok, _ = check_anchor_pair_r1(metrics)
    assert not ok


def test_r1_unusable_recall_tolerance_is_2pp():
    assert check_anchor_pair_r1(dict(R1_GO, unusable_recall_a=0.29))[0]
    assert not check_anchor_pair_r1(dict(R1_GO, unusable_recall_a=0.28))[0]


def test_r1_fails_closed_on_missing_keys():
    metrics = dict(R1_GO)
    del metrics["unusable_recall_b"]
    ok, lines = check_anchor_pair_r1(metrics)
    assert not ok
    assert any("FAIL-CLOSED" in line and "unusable_recall_b" in line for line in lines)


def test_frame_bar_requires_both_bands():
    assert check_frame_bar(
        {"transition_decided_agreement": 0.91, "overall_decided_agreement": 0.92}
    )[0]
    assert not check_frame_bar(
        {"transition_decided_agreement": 0.89, "overall_decided_agreement": 0.95}
    )[0]
    assert not check_frame_bar(
        {"transition_decided_agreement": 0.95, "overall_decided_agreement": 0.89}
    )[0]


def test_frame_bar_fails_closed_on_missing_keys():
    ok, lines = check_frame_bar({"overall_decided_agreement": 0.95})
    assert not ok
    assert any("FAIL-CLOSED" in line for line in lines)


V2_R1_GO = {
    "transition_fp_rate_reduction_pct": 35.0,
    "transition_fp_rate_reduction_ci_low_pct": 8.0,
    "transition_fn_rate_reduction_pct": 31.0,
    "transition_fn_rate_reduction_ci_low_pct": 5.0,
    "overall_decided_agreement_v": 0.86,
    "overall_decided_agreement_b": 0.84,
    "present_to_unusable_rate_v": 0.08,
    "present_to_unusable_rate_b": 0.07,
    "unusable_recall_v": 0.30,
    "unusable_recall_b": 0.31,
    "arm_v_params": 720_000,
    "arm_b_params": 700_000,
    "n_seeds": 3,
}


def test_v2_r1_go_when_all_criteria_met():
    ok, lines = check_anchor_pair_v2_r1(V2_R1_GO)
    assert ok
    assert any("GO" in line for line in lines)


def test_v2_r1_kill_when_ci_low_nonpositive():
    metrics = dict(V2_R1_GO, transition_fp_rate_reduction_ci_low_pct=0.0)
    ok, lines = check_anchor_pair_v2_r1(metrics)
    assert not ok
    assert any("KILL" in line for line in lines)


def test_v2_r1_kill_on_present_to_unusable_escape():
    metrics = dict(V2_R1_GO, present_to_unusable_rate_v=0.12)
    ok, _ = check_anchor_pair_v2_r1(metrics)
    assert not ok


def test_v2_r1_requires_three_seeds():
    metrics = dict(V2_R1_GO, n_seeds=2)
    ok, _ = check_anchor_pair_v2_r1(metrics)
    assert not ok


def test_v2_r1_fails_closed_on_missing_keys():
    metrics = dict(V2_R1_GO)
    del metrics["arm_v_params"]
    ok, lines = check_anchor_pair_v2_r1(metrics)
    assert not ok
    assert any("FAIL-CLOSED" in line and "arm_v_params" in line for line in lines)


A2_R1_GO = {
    "transition_fp_rate_reduction_pct": 35.0,
    "transition_fp_rate_reduction_ci_low_pct": 8.0,
    "transition_fn_rate_reduction_pct": 31.0,
    "transition_fn_rate_reduction_ci_low_pct": 5.0,
    "overall_decided_agreement_p": 0.86,
    "overall_decided_agreement_b": 0.84,
    "present_to_unusable_rate_p": 0.08,
    "present_to_unusable_rate_b": 0.07,
    "unusable_recall_p": 0.30,
    "unusable_recall_b": 0.31,
    "arm_p_params": 720_000,
    "arm_b_params": 700_000,
    "n_seeds": 3,
}


def test_a2_r1_go_when_all_criteria_met():
    ok, lines = check_anchor_pair_a2_r1(A2_R1_GO)
    assert ok
    assert any("GO" in line for line in lines)


def test_a2_r1_kill_when_ci_low_nonpositive():
    metrics = dict(A2_R1_GO, transition_fp_rate_reduction_ci_low_pct=0.0)
    ok, lines = check_anchor_pair_a2_r1(metrics)
    assert not ok
    assert any("KILL" in line for line in lines)


def test_a2_r1_kill_on_present_to_unusable_escape():
    metrics = dict(A2_R1_GO, present_to_unusable_rate_p=0.12)
    ok, _ = check_anchor_pair_a2_r1(metrics)
    assert not ok


def test_a2_r1_requires_three_seeds():
    metrics = dict(A2_R1_GO, n_seeds=2)
    ok, _ = check_anchor_pair_a2_r1(metrics)
    assert not ok


def test_a2_r1_fails_closed_on_missing_keys():
    metrics = dict(A2_R1_GO)
    del metrics["arm_p_params"]
    ok, lines = check_anchor_pair_a2_r1(metrics)
    assert not ok
    assert any("FAIL-CLOSED" in line and "arm_p_params" in line for line in lines)
