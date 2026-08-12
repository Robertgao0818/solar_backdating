"""Tests for V5 structured short-gap recovery and additive sidecars."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "temporal"))

import run_r4_shortgap_window_recovery as v5  # noqa: E402


def rep(status: str, frame: str, number: int, confidence: float = 0.9):
    return {
        "sequence_status": status,
        "first_pv_frame": frame,
        "confidence": confidence,
        "reason": f"{status}/{frame}",
        "_rep": number,
    }


@pytest.mark.parametrize(
    ("status", "frame"),
    [
        ("monotonic_install", "F03"),
        ("already_present_before_F01", "before_F01"),
        ("not_present_through_F06", "after_F06"),
        ("not_present_through_F06", "none"),
        ("present_to_absent_or_nonmonotonic", "unclear"),
        ("unreviewable", "unclear"),
    ],
)
def test_schema_accepts_valid_status_frame_pairs(status, frame):
    got = v5.validate_response({
        "sequence_status": status, "first_pv_frame": frame,
        "confidence": 0.8, "reason": "visible sequence",
    })
    assert (got["sequence_status"], got["first_pv_frame"]) == (status, frame)


@pytest.mark.parametrize(
    ("status", "frame"),
    [
        ("monotonic_install", "before_F01"),
        ("already_present_before_F01", "F01"),
        ("not_present_through_F06", "unclear"),
        ("present_to_absent_or_nonmonotonic", "F04"),
        ("unreviewable", "none"),
    ],
)
def test_schema_rejects_status_frame_contradictions(status, frame):
    with pytest.raises(ValueError, match="contradictory"):
        v5.validate_response({
            "sequence_status": status, "first_pv_frame": frame,
            "confidence": 0.8, "reason": "contradiction",
        })


def test_packet_validation_rejects_first_pv_in_blank_slot():
    packet = {
        "slot_provenance": [
            {"frame_id": frame, "source_present": frame != "F02"}
            for frame in v5.FRAME_IDS
        ]
    }
    with pytest.raises(ValueError, match="blank"):
        v5.validate_packet_response({
            "sequence_status": "monotonic_install",
            "first_pv_frame": "F02", "confidence": 0.8, "reason": "bad",
        }, packet)


def test_two_and_three_rep_consensus_on_exact_tuple():
    primary = [
        rep("monotonic_install", "F03", 1),
        rep("monotonic_install", "F03", 2),
    ]
    assert not v5.needs_third_rep(primary)
    assert v5.consensus(primary)["first_pv_frame"] == "F03"
    split = [
        rep("monotonic_install", "F03", 1),
        rep("monotonic_install", "F04", 2),
    ]
    assert v5.needs_third_rep(split)
    got = v5.consensus([*split, rep("monotonic_install", "F04", 3)])
    assert got["agreeing_reps"] == [2, 3]
    assert got["first_pv_frame"] == "F04"


def test_three_way_disagreement_fails_closed():
    got = v5.consensus([
        rep("monotonic_install", "F03", 1),
        rep("already_present_before_F01", "before_F01", 2),
        rep("not_present_through_F06", "none", 3),
    ])
    assert got["sequence_status"] == "unreviewable"
    assert got["first_pv_frame"] == "unclear"
    assert got["agreeing_reps"] == []


def _packet_and_candidate(anchor_id="a", blank=frozenset()):
    dates = ["2020-01-01", "2020-02-01", "2020-03-01",
             "2020-04-01", "2020-05-01", "2020-06-01"]
    packet = {"anchor_id": anchor_id, "source_objects": [], "render_objects": []}
    candidate = {"anchor_id": anchor_id}
    for frame, slot, date in zip(v5.FRAME_IDS, v5.STRIP_SLOT_ORDER, dates):
        if frame in blank:
            candidate[f"{slot}_capture_date"] = None
            candidate[f"{slot}_src_tiff_sha256"] = None
            continue
        sha = frame.lower() * 8
        candidate[f"{slot}_capture_date"] = date
        candidate[f"{slot}_src_tiff_sha256"] = sha
        packet["source_objects"].append(
            {"slot": slot, "path": f"/src/{frame}.tif", "sha256": sha}
        )
        packet["render_objects"].append({
            "slot": slot, "path": f"/render/{frame}.png",
            "sha256": f"render-{frame}", "kind": "tight_marker_free",
        })
    return packet, candidate


def test_slot_to_date_mapping_preserves_blank_slots():
    packet, candidate = _packet_and_candidate(blank={"F02", "F06"})
    got = v5._slot_provenance(packet, candidate)
    assert [row["frame_id"] for row in got] == list(v5.FRAME_IDS)
    assert not got[1]["source_present"] and got[1]["capture_date"] is None
    assert got[2]["source_slot"] == "earlier"
    assert got[2]["capture_date"] == "2020-03-01"


def _decision(anchor_id, status, frame):
    return {
        "anchor_id": anchor_id, "sequence_status": status,
        "first_pv_frame": frame, "agreeing_reps": [1, 2], "rep_count": 2,
        "confidence": 0.9, "reason": "structured", "model_alias": v5.MODEL_ALIAS,
        "exact_model_version": "gemini-default", "prompt_sha256": "p",
        "response_schema_sha256": "s", "packet_sha256": f"packet-{anchor_id}",
        "source_v4_packet_sha256": f"v4-{anchor_id}",
        "temporal_strip_sha256": f"strip-{anchor_id}",
        "recovery_rule_version": v5.RECOVERY_RULE_VERSION,
        "recovery_rule_sha256": "r",
    }


def test_boundaries_censoring_and_no_labels_for_manual(monkeypatch):
    monkeypatch.setattr(v5, "EXPECTED_POPULATION", 5)
    packet_rows, candidate_rows = [], []
    cases = [
        ("m", "monotonic_install", "F04"),
        ("l", "already_present_before_F01", "before_F01"),
        ("r", "not_present_through_F06", "after_F06"),
        ("n", "present_to_absent_or_nonmonotonic", "unclear"),
        ("u", "unreviewable", "unclear"),
    ]
    for anchor, _, _ in cases:
        packet, candidate = _packet_and_candidate(anchor, blank={"F02"})
        packet["slot_provenance"] = v5._slot_provenance(packet, candidate)
        packet_rows.append(packet)
        candidate["earlier_capture_date"] = "2020-03-01"
        candidate["later_capture_date"] = "2020-04-01"
        candidate_rows.append(candidate)
    out = v5.build_recovery_sidecars(
        packets=pd.DataFrame(packet_rows),
        candidates=pd.DataFrame(candidate_rows),
        decisions=pd.DataFrame([_decision(*case) for case in cases]),
    )
    labels = out["recovered_frame_labels"]
    monotonic = labels[labels.anchor_id.eq("m")]
    assert monotonic.set_index("frame_id").loc["F03", "pv_label"] == "absent"
    assert monotonic.set_index("frame_id").loc["F04", "pv_label"] == "present"
    interval = out["recovered_install_intervals"].iloc[0]
    assert interval.last_absent_frame == "F03"
    assert interval.first_present_frame == "F04"
    assert interval.interval_days == 31
    assert not bool(interval.boundary_moved)
    assert out["left_censored"].iloc[0].boundary_frame == "F01"
    assert out["right_censored"].iloc[0].boundary_frame == "F06"
    assert set(labels.anchor_id) == {"m", "l", "r"}
    assert out["manual_review_queue"].anchor_id.tolist() == ["n"]


def test_monotonic_f01_is_frame_supervision_without_fake_interval(monkeypatch):
    monkeypatch.setattr(v5, "EXPECTED_POPULATION", 1)
    packet, candidate = _packet_and_candidate()
    packet["slot_provenance"] = v5._slot_provenance(packet, candidate)
    candidate["earlier_capture_date"] = "2020-03-01"
    candidate["later_capture_date"] = "2020-04-01"
    out = v5.build_recovery_sidecars(
        packets=pd.DataFrame([packet]), candidates=pd.DataFrame([candidate]),
        decisions=pd.DataFrame([_decision("a", "monotonic_install", "F01")]),
    )
    assert len(out["recovered_frame_labels"]) == 6
    assert out["recovered_install_intervals"].empty


def test_sidecar_parquet_roundtrip_preserves_provenance_lists(tmp_path):
    frame = pd.DataFrame([{
        column: (
            [1, 2] if column == "agreeing_reps"
            else 2 if column == "rep_count"
            else 0.9 if column == "confidence"
            else "value"
        )
        for column in v5.LABEL_COLUMNS
    }])
    path = tmp_path / "labels.parquet"
    frame.to_parquet(path, index=False)
    got = pd.read_parquet(path)
    assert list(got.iloc[0].agreeing_reps) == [1, 2]
    assert got.iloc[0].packet_sha256 == "value"


def test_resume_identity_and_version_drift(tmp_path):
    packet, candidate = _packet_and_candidate()
    packet["slot_provenance"] = v5._slot_provenance(packet, candidate)
    packet.update({
        "packet_sha256": "packet", "prompt_sha256": "prompt",
        "response_schema_sha256": "schema", "recovery_rule_sha256": "rule",
    })
    rep_path = tmp_path / "production/reps/a/rep1.json"
    rep_path.parent.mkdir(parents=True)
    payload = {
        **rep("unreviewable", "unclear", 1), "_run_id": v5.RUN_ID,
        "_packet_sha256": "different", "_model_alias": v5.MODEL_ALIAS,
        "_prompt_sha256": "prompt", "_response_schema_sha256": "schema",
        "_recovery_rule_sha256": "rule", "_exact_model_version": "v1",
    }
    rep_path.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="resume identity"):
        v5.run_rep(
            packet=packet, rep=1, scope="production", out_root=tmp_path,
            lock={"exact_model_version": "v1"}, caller=lambda **_: None,
        )
    with pytest.raises(v5.v3.ModelVersionDrift):
        v5._admit_version({"exact_model_version": "v1"}, "v2")


def test_run_identity_rejects_prompt_or_prepared_input_drift(tmp_path):
    for name, content in [
        ("packet_index.parquet", b"packet"),
        ("candidate_manifest.parquet", b"candidate"),
        ("source_v4_rejected.parquet", b"source"),
    ]:
        (tmp_path / name).write_bytes(content)
    lock = {
        "run_id": v5.RUN_ID,
        **v5._config_identity(),
        "packet_index_sha256": v5.v3.sha256_file(tmp_path / "packet_index.parquet"),
        "candidate_manifest_sha256": v5.v3.sha256_file(
            tmp_path / "candidate_manifest.parquet"
        ),
        "source_v4_rejected_sha256": v5.v3.sha256_file(
            tmp_path / "source_v4_rejected.parquet"
        ),
    }
    v5.assert_run_identity(tmp_path, lock)
    with pytest.raises(ValueError, match="prompt_sha256"):
        v5.assert_run_identity(tmp_path, {**lock, "prompt_sha256": "drift"})
    (tmp_path / "packet_index.parquet").write_bytes(b"changed")
    with pytest.raises(ValueError, match="packet_index_sha256"):
        v5.assert_run_identity(tmp_path, lock)


def test_locked_transport_profile_and_prompt_scope():
    assert v5.MAX_OUTPUT_TOKENS == 8192
    assert v5.DEFAULT_WORKERS == 30
    assert v5.DEFAULT_QPS == 6
    assert "six-factor" not in v5.PROMPT
    assert "Never choose a blank slot" in v5.PROMPT
