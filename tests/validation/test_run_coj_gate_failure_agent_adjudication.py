from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.validation import run_coj_gate_failure_agent_adjudication as agent


def provenance(blank=("F06",)):
    return [
        {"frame_id": frame, "source_present": frame not in blank}
        for frame in agent.FRAME_IDS
    ]


def response(states=None, **updates):
    states = states or ["absent", "absent", "present", "present", "present", "blank"]
    value = {
        "target_match": "same_target",
        "coj_2019_state": "absent",
        "coj_2023_state": "present",
        "r0_frames": [
            {"frame_id": frame, "state": state, "evidence": "visible"}
            for frame, state in zip(agent.FRAME_IDS, states)
        ],
        "confidence": 0.9,
        "reason": "same roof and visible frames",
    }
    value.update(updates)
    return value


def rep(number, states=None, **updates):
    return {**response(states, **updates), "_rep": number}


def test_response_requires_exact_frame_coverage_and_blank_match():
    got = agent.validate_response(response(), provenance())
    assert len(got["r0_frames"]) == 6
    bad = response()
    bad["r0_frames"][-1]["state"] = "absent"
    with pytest.raises(ValueError, match="blank"):
        agent.validate_response(bad, provenance())
    duplicate = response()
    duplicate["r0_frames"][-1]["frame_id"] = "F05"
    with pytest.raises(ValueError, match="duplicate"):
        agent.validate_response(duplicate, provenance())


def test_unclear_target_cannot_emit_definite_states():
    with pytest.raises(ValueError, match="unclear target"):
        agent.validate_response(
            response(target_match="unclear"), provenance()
        )


def test_third_rep_and_fieldwise_majority():
    primary = [rep(1), rep(2)]
    assert not agent.needs_third_rep(primary)
    split = [rep(1), rep(2, states=["absent", "present", "present", "present", "present", "blank"])]
    assert agent.needs_third_rep(split)
    result = agent.fieldwise_consensus([
        *split,
        rep(3, states=["absent", "present", "present", "present", "present", "blank"]),
    ])
    states = {item["frame_id"]: item["state"] for item in result["r0_frames"]}
    assert states["F02"] == "present"
    assert result["field_agreeing_reps"]["F02"] == [2, 3]


@pytest.mark.parametrize(
    ("states", "status", "first"),
    [
        (["absent", "absent", "present", "present", "present", "blank"],
         "monotonic_install", "F03"),
        (["present", "present", "present", "present", "present", "blank"],
         "already_present_before_F01", "before_F01"),
        (["absent", "absent", "absent", "absent", "absent", "blank"],
         "not_present_through_F06", "none"),
        (["absent", "present", "present", "absent", "present", "blank"],
         "present_to_absent_or_nonmonotonic", "unclear"),
        (["unclear", "present", "present", "present", "present", "blank"],
         "unreviewable", "unclear"),
    ],
)
def test_deterministic_decoder(states, status, first):
    frames = [
        {"frame_id": frame, "state": state}
        for frame, state in zip(agent.FRAME_IDS, states)
    ]
    source = {frame: frame != "F06" for frame in agent.FRAME_IDS}
    got = agent.decode_sequence(frames, source)
    assert got == {"sequence_status": status, "first_pv_frame": first}


def test_agent_transport_profile():
    assert agent.MODEL_ALIAS == "gemini-3.6-flash-high"
    assert agent.EXPECTED_EXACT_MODEL_VERSION == "gemini-3.6-flash"
    assert agent.MAX_OUTPUT_TOKENS == 16384


def test_version_fallback_is_rejected_before_lock():
    lock = {}
    with pytest.raises(ValueError, match="version rejected"):
        agent.admit_version(lock, "gemini-default")
    assert "exact_model_version" not in lock
    assert agent.admit_version(lock, "gemini-3.6-flash") == "gemini-3.6-flash"
