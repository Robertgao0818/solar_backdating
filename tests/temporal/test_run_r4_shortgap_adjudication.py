"""Contract tests for the preregistered R4 short-gap adjudication."""

from __future__ import annotations

import itertools
import sys
from pathlib import Path

import pandas as pd
import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "temporal"))

import run_r4_shortgap_adjudication as sg  # noqa: E402
import run_r4_training as r4  # noqa: E402


def response(
    target="yes", eu="yes", es="absent", lu="yes", ls="present",
    transition="yes", confidence=0.9, **extra,
):
    return {
        "same_physical_target": target,
        "earlier": {"usable": eu, "pv_state": es},
        "later": {"usable": lu, "pv_state": ls},
        "transition_supported": transition,
        "failure_modes": [],
        "confidence": confidence,
        "reason": "visible evidence",
        **extra,
    }


def test_frozen_panel_id_hash():
    assert len(sg.PANEL_IDS) == 40
    assert sg.canonical_panel_id_sha(reversed(sg.PANEL_IDS)) == sg.PANEL_ID_SHA256


def test_enhanced_prompt_is_short_imperative_and_does_not_leak_teacher_labels():
    prompt = sg.ENHANCED_PROMPT
    assert sg.PROMPT == prompt
    assert sg.PROMPT != sg.LEGACY_PROMPT
    assert len(prompt.split()) < 90
    assert 'set pv_state="unclear"' in prompt
    assert 'Use transition_supported="yes"' in prompt
    assert "Return schema-valid JSON only." in prompt
    assert "done_appears" not in prompt
    assert "teacher confidence" not in prompt.lower()


@pytest.mark.parametrize(
    "value,match",
    [
        (response(target="no"), "contradicts"),
        (response(eu="no"), "non-usable"),
        (response(transition="yes", es="present", ls="present"), "outside clean"),
        (response(unexpected=1), "fields"),
        (response(confidence=1.1), "outside"),
    ],
)
def test_schema_contradictions_rejected(value, match):
    with pytest.raises(ValueError, match=match):
        sg.validate_response(value)


def test_complete_legal_factor_product_classifies_once():
    counts = {}
    legal = 0
    for target, eu, es, lu, ls, transition in itertools.product(
        sg.TARGET, sg.USABLE, sg.PV_STATE, sg.USABLE, sg.PV_STATE, sg.TRANSITION
    ):
        item = response(target, eu, es, lu, ls, transition)
        try:
            sg.validate_response(item)
        except ValueError:
            continue
        legal += 1
        cls = sg.derive_class(item)
        counts[cls] = counts.get(cls, 0) + 1
    assert legal > 0
    assert set(counts) == {
        "TARGET_MISMATCH_OR_SOURCE_SHIFT", "AMBIGUOUS",
        "ONE_OR_BOTH_UNUSABLE", "CLEAN_INSTALL_TRANSITION",
        "ALREADY_PRESENT_BOTH", "ABSENT_BOTH",
    }
    assert sum(counts.values()) == legal


def test_third_rep_triggers_on_retry_uncertainty_low_confidence_and_disagreement():
    clean = response()
    assert not sg.needs_third_rep([clean, clean])
    assert sg.needs_third_rep([clean, response(confidence=.79)])
    assert sg.needs_third_rep([clean, response(transition="uncertain")])
    retried = dict(clean, _schema_retry=True)
    assert sg.needs_third_rep([clean, retried])


def test_confidence_gate_is_per_agreeing_rep_not_average():
    reps = [response(confidence=.99), response(confidence=.79), response(confidence=.99, ls="absent", transition="no")]
    got = sg.consensus(reps)
    assert got.final_class == "AMBIGUOUS"
    assert got.earlier_override == got.later_override == "uninformative"


def test_unusable_endpoint_table_can_retain_high_confidence_usable_endpoint():
    item = response(eu="no", es="unclear", ls="present", transition="no")
    got = sg.consensus([item, item])
    assert got.final_class == "ONE_OR_BOTH_UNUSABLE"
    assert got.earlier_override == "uninformative"
    assert got.later_override == "present"
    low = dict(item, confidence=.7)
    got = sg.consensus([low, low])
    assert got.final_class == "ONE_OR_BOTH_UNUSABLE"
    assert got.later_override == "uninformative"


def test_exact_version_lock_stops_on_missing_or_drift():
    lock = {}
    assert sg.admit_exact_version(lock, "A", "alias", "models/x@001") == "models/x@001"
    with pytest.raises(sg.ModelVersionDrift, match="MODEL_VERSION_DRIFT"):
        sg.admit_exact_version(lock, "A", "alias", "models/x@002")
    with pytest.raises(sg.ModelVersionDrift, match="missing"):
        sg.admit_exact_version({}, "B", "alias", None)


def test_auto_filters_are_exact_and_content_unusable_is_endpoint_only():
    early = {"src_tiff_sha256": "x", "render_sha256": "a", "valid_target_pixels": 2}
    late = {"src_tiff_sha256": "x", "render_sha256": "b", "valid_target_pixels": 2}
    assert sg.auto_rule(early, late)[0] == "AUTO_IDENTICAL_SOURCE_CONFLICT"
    late["src_tiff_sha256"] = "y"
    late["render_sha256"] = "a"
    assert sg.auto_rule(early, late)[0] == "AUTO_IDENTICAL_RENDER_CONFLICT"
    late["render_sha256"] = "b"
    early["valid_target_pixels"] = 0
    assert sg.auto_rule(early, late) == ("AUTO_CONTENT_UNUSABLE", "uninformative", "")


def _manifest():
    return pd.DataFrame(
        {
            "anchor_id": ["a"] * 4 + ["test"] * 2,
            "capture_date": [
                "2020-01-01", "2021-01-01", "2021-01-31", "2022-01-01",
                "2020-01-01", "2020-01-10",
            ],
            "chip_index": range(6),
            "split": ["train"] * 4 + ["test"] * 2,
            "scan_status": ["done_appears"] * 6,
            "label_v1": ["absent", "absent", "present", "present", "absent", "present"],
            "src_tiff_sha256": [f"s{i}" for i in range(6)],
            "chip_arm": ["A24"] * 6,
            "source_area_m2": [20.] * 6,
            "area_bin": ["15-40"] * 6,
        }
    )


def test_population_derived_only_from_r0_train_cal_and_short_gap():
    got = sg.derive_candidates(_manifest(), expected_count=1)
    assert got.anchor_id.tolist() == ["a"]
    assert got.gap_days.tolist() == [30]
    assert got.interval_loss_eligible.tolist() == [False]


def test_population_uses_decoder_epoch_chain_not_endpoint_distance():
    frame = _manifest().iloc[:4].copy()
    frame["capture_date"] = ["2021-01-01", "2021-01-31", "2021-02-20", "2022-01-01"]
    frame["label_v1"] = ["absent", "uninformative", "present", "present"]
    got = sg.derive_candidates(frame, expected_count=1)
    assert got.gap_days.item() == 50


def test_sidecar_roundtrip_applies_only_boundary_and_disables_interval(tmp_path):
    manifest = _manifest().iloc[:4].copy()
    sidecar = pd.DataFrame(
        {
            "anchor_id": ["a", "a"],
            "capture_date": ["2021-01-01", "2021-01-31"],
            "endpoint": ["earlier", "later"],
            "override_label": ["present", None],
            "decision_source": ["GEMINI_CONSENSUS"] * 2,
            "final_class": ["ALREADY_PRESENT_BOTH"] * 2,
            "interval_loss_eligible": [False, False],
        }
    )
    path = tmp_path / "overrides.parquet"
    sidecar.to_parquet(path, index=False)
    got, affected = r4.apply_shortgap_sidecar(manifest, path)
    assert affected == {"a"}
    assert got.loc[got.capture_date.eq("2021-01-01"), "label_v1"].item() == "present"
    assert got.loc[got.capture_date.eq("2021-01-31"), "label_v1"].item() == "present"
    assert not got["interval_loss_eligible"].any()


def test_model_selection_gate_and_frozen_tie_break():
    aliases = {"A": "gemini-3.5-flash-extra-low", "B": "gemini-3.1-flash-lite"}
    assert sg.select_model({"A": 27, "B": 28}, 30, aliases)["winning_arm"] == "B"
    assert sg.select_model({"A": 27, "B": 27}, 30, aliases)["winning_arm"] == "B"
    with pytest.raises(ValueError, match="neither"):
        sg.select_model({"A": 26, "B": 26}, 30, aliases)
    with pytest.raises(ValueError, match="N_clear"):
        sg.select_model({"A": 30, "B": 30}, 29, aliases)


def test_manual_review_expansion_and_exact_tuple_scoring():
    rows = []
    for anchor_id in sg.PANEL_IDS:
        rows.append({
            "anchor_id": anchor_id, "pass1_code": "AP", "pass2_code": "AP",
            "clear1": "yes", "clear2": "yes", "senior_code": "",
            "review_note": "clear transition",
        })
    rows[0].update({
        "pass1_code": "U_P", "pass2_code": "PP",
        "clear1": "no", "clear2": "no", "senior_code": "U_P",
    })
    annotations = sg.materialize_manual_annotations(
        pd.DataFrame(rows), reviewed_utc="2026-07-24T00:00:00+00:00",
    )
    assert len(annotations) == 81
    reference, disputes = sg.build_human_reference(annotations)
    assert len(reference) == 39
    assert disputes.anchor_id.tolist() == [sg.PANEL_IDS[0]]

    consensus = pd.DataFrame({
        "anchor_id": reference.anchor_id,
        "gate_factors": [
            tuple(row[field] for field in sg.GATE_FIELDS)
            for _, row in reference.iterrows()
        ],
    })
    matches, details = sg.panel_tuple_matches(reference, consensus)
    assert matches == 39
    assert details.complete_tuple_match.all()
    consensus.loc[consensus.index[0], "gate_factors"] = None
    matches, details = sg.panel_tuple_matches(reference, consensus)
    assert matches == 38
    assert not details.iloc[0].complete_tuple_match


def test_packet_order_hash_preflight_and_model_rep(tmp_path):
    paths = []
    for idx in range(6):
        path = tmp_path / f"source{idx}.png"
        Image.new("RGB", (32, 32), (idx, idx, idx)).save(path)
        paths.append(path)
    geometry = tmp_path / "geometry.json"
    geometry.write_text("{}")
    packet = sg.compose_review_packet(
        anchor_id="a", tight_paths=paths[:2], context_paths=paths[2:4],
        strip_paths=[None, paths[0], paths[1], paths[2], paths[3], None],
        out_root=tmp_path,
        provenance={
            "earlier_source_path": str(paths[0]),
            "earlier_source_sha256": sg.sha256_file(paths[0]),
            "later_source_path": str(paths[1]),
            "later_source_sha256": sg.sha256_file(paths[1]),
            "geometry_path": str(geometry),
            "geometry_sha256": sg.sha256_file(geometry),
        },
    )
    sg.preflight_review_packets([packet], tmp_path)

    calls = []
    def caller(**kwargs):
        calls.append(kwargs)
        return __import__("json").dumps(response()), {"modelVersion": "exact-v1"}

    lock = {}
    got = sg.call_model_rep(
        anchor_id="a", rep=1, packet=packet, alias="alias", arm="A",
        lock=lock, out_root=tmp_path, caller=caller,
    )
    assert got["_exact_model_version"] == "exact-v1"
    assert calls[0]["max_tokens"] == 8192
    assert calls[0]["response_schema"] == sg.TRANSPORT_RESPONSE_SCHEMA
    assert "uniqueItems" in sg.RESPONSE_SCHEMA["properties"]["failure_modes"]
    assert "uniqueItems" not in calls[0]["response_schema"]["properties"]["failure_modes"]
    assert (tmp_path / "attempts.jsonl").exists()


def test_preflight_failure_writes_failure_not_label(tmp_path):
    record = {
        "anchor_id": "a",
        "earlier_source_path": str(tmp_path / "missing.tif"),
        "earlier_source_sha256": "x",
    }
    with pytest.raises(RuntimeError, match="no verdict"):
        sg.preflight_review_packets([record], tmp_path)
    text = (tmp_path / "build_failures.jsonl").read_text()
    assert "input_integrity_and_render_preflight" in text
    assert "uninformative" not in text
