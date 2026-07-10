"""Tests for the student-path arithmetic verdict checker."""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.validation.check_student_path_gate import (
    check_anchor_pair_r1,
    check_frame_bar,
)

R1_GO = {
    "transition_fp_reduction_pct": 35.0,
    "transition_fn_reduction_pct": 31.0,
    "overall_decided_agreement_a": 0.86,
    "overall_decided_agreement_b": 0.84,
    "unusable_recall_a": 0.30,
    "unusable_recall_b": 0.31,
}


def test_r1_go_when_all_criteria_met():
    ok, lines = check_anchor_pair_r1(R1_GO)
    assert ok
    assert any("GO" in l for l in lines)


def test_r1_kill_on_one_sided_fp_only_win():
    metrics = dict(R1_GO, transition_fn_reduction_pct=10.0)
    ok, lines = check_anchor_pair_r1(metrics)
    assert not ok
    assert any("KILL" in l for l in lines)


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
    assert any("FAIL-CLOSED" in l and "unusable_recall_b" in l for l in lines)


def test_frame_bar_requires_both_bands():
    assert check_frame_bar(
        {"transition_decided_agreement": 0.91, "overall_decided_agreement": 0.92})[0]
    assert not check_frame_bar(
        {"transition_decided_agreement": 0.89, "overall_decided_agreement": 0.95})[0]
    assert not check_frame_bar(
        {"transition_decided_agreement": 0.95, "overall_decided_agreement": 0.89})[0]


def test_frame_bar_fails_closed_on_missing_keys():
    ok, lines = check_frame_bar({"overall_decided_agreement": 0.95})
    assert not ok
    assert any("FAIL-CLOSED" in l for l in lines)
