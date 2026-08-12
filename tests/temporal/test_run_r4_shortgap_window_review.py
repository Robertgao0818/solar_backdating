"""Tests for the simplified V4 under-45-day window review."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "temporal"))

import run_r4_shortgap_window_review as v4  # noqa: E402


def rep(verdict: str, number: int, confidence: float = 0.9):
    return {
        "verdict": verdict, "confidence": confidence, "reason": verdict,
        "_rep": number,
    }


def test_prompt_asks_only_the_window_question():
    assert "confirmed_shortgap only when all are true" in v4.PROMPT
    assert "absent in EARLIER" in v4.PROMPT
    assert "present in LATER" in v4.PROMPT
    assert "six-factor" not in v4.PROMPT


@pytest.mark.parametrize("verdict", v4.VERDICTS)
def test_response_schema_accepts_only_three_verdicts(verdict):
    got = v4.validate_response({
        "verdict": verdict, "confidence": 0.8, "reason": "visible endpoints",
    })
    assert got["verdict"] == verdict
    with pytest.raises(ValueError):
        v4.validate_response({**got, "extra": True})


def test_two_rep_agreement_and_third_rep_majority():
    primary = [rep("confirmed_shortgap", 1), rep("confirmed_shortgap", 2)]
    assert not v4.needs_third_rep(primary)
    assert v4.consensus(primary)["verdict"] == "confirmed_shortgap"

    primary = [rep("confirmed_shortgap", 1), rep("rejected_shortgap", 2)]
    assert v4.needs_third_rep(primary)
    result = v4.consensus([*primary, rep("rejected_shortgap", 3)])
    assert result["verdict"] == "rejected_shortgap"
    assert result["agreeing_reps"] == [2, 3]


def test_three_way_disagreement_is_unreviewable():
    result = v4.consensus([
        rep("confirmed_shortgap", 1),
        rep("rejected_shortgap", 2),
        rep("unreviewable", 3),
    ])
    assert result["verdict"] == "unreviewable"
    assert result["agreeing_reps"] == []


def test_transport_schema_attempt_budget_and_low_thinking_token_cap():
    assert v4.MAX_ATTEMPTS == 6
    assert v4.MAX_OUTPUT_TOKENS == 8192
