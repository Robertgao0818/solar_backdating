"""Behavior tests for the pre-registered path-B sequence-head pilot."""

from __future__ import annotations

import csv
import numpy as np
import torch

from scripts.validation.pilot_sequence_head_2026_07_10 import (
    DecodedPrediction,
    TeacherIntervalTarget,
    arm_point_metrics,
    build_model_pair,
    build_sequences,
    collate_sequences,
    compare_arms,
    decode_teacher_targets,
    decode_logits,
    prediction_frame_labels,
    rep_agreement_by_status,
)


def fixture_targets(raw):
    """Build explicit decoded targets for synthetic fixtures only."""
    anchors = np.asarray(raw["anchor_id"]).astype(str)
    dates = np.asarray(raw["capture_date"]).astype(str)
    labels = np.asarray(raw["label_3class"]).astype(str)
    out = {}
    for anchor in sorted(set(anchors)):
        rows = sorted(
            [(date, label) for a, date, label in zip(anchors, dates, labels) if a == anchor]
        )
        present = [index for index, (_date, label) in enumerate(rows) if label == "present"]
        if not present:
            out[anchor] = TeacherIntervalTarget("UNDATED", None, None, "UNDATED")
            continue
        upper = present[0]
        absent_before = [
            index for index, (_date, label) in enumerate(rows[:upper]) if label == "absent"
        ]
        if absent_before:
            lower_date = rows[absent_before[-1]][0]
            upper_date = rows[upper][0]
            out[anchor] = TeacherIntervalTarget(
                "INTERVAL",
                lower_date,
                upper_date,
                f"INTERVAL|{lower_date}|{upper_date}",
            )
        else:
            upper_date = rows[upper][0]
            out[anchor] = TeacherIntervalTarget(
                "AP_BOUND", None, upper_date, f"AP_BOUND<=|{upper_date}"
            )
    return out


def test_build_sequences_sorts_frames_and_builds_decoded_interval_targets():
    raw = {
        "features": np.arange(20, dtype=np.float32).reshape(5, 4),
        "anchor_id": np.array(["interval", "interval", "interval", "bound", "undated"]),
        "capture_date": np.array(
            ["2021-01-01", "2019-01-01", "2020-01-01", "2018-01-01", "2018-01-01"]
        ),
        "version": np.array(["3", "1", "2", "1", "1"]),
        "label_3class": np.array(["present", "absent", "absent", "present", "absent"]),
        "split": np.array(["train", "train", "train", "heldout", "heldout"]),
        "terminal_status": np.array(
            [
                "done_appears",
                "done_appears",
                "done_appears",
                "done_already_present_before_geid_history",
                "done_appears",
            ]
        ),
    }

    samples = {sample.anchor_id: sample for sample in build_sequences(raw, fixture_targets(raw))}

    interval = samples["interval"]
    assert interval.dates == ("2019-01-01", "2020-01-01", "2021-01-01")
    assert interval.target_kind == "INTERVAL"
    assert (interval.lower_index, interval.upper_index) == (1, 2)
    assert interval.transition_mask.tolist() == [True, True, True]

    bound = samples["bound"]
    assert bound.target_kind == "AP_BOUND"
    assert bound.lower_index is None
    assert bound.upper_index == 0

    undated = samples["undated"]
    assert undated.target_kind == "UNDATED"
    assert undated.lower_index is None
    assert undated.upper_index is None


def test_teacher_target_comes_from_phase0_decoder_not_label_3class(tmp_path):
    raw = {
        "features": np.zeros((3, 4), dtype=np.float32),
        "anchor_id": np.array(["a", "a", "a"]),
        "capture_date": np.array(["2019-01-01", "2020-01-01", "2021-01-01"]),
        "version": np.array(["1", "2", "3"]),
        # Deliberately uninformative: decoded target must use raw manifest fields.
        "label_3class": np.array(["unusable", "unusable", "unusable"]),
        "split": np.array(["train", "train", "train"]),
        "terminal_status": np.array(["done_appears"] * 3),
    }
    manifest = tmp_path / "labels.csv"
    with manifest.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "anchor_id",
                "capture_date",
                "version",
                "pv_present",
                "confidence",
                "quality_flag",
            ],
        )
        writer.writeheader()
        for capture_date, version, pv_present in (
            ("2019-01-01", "1", "0"),
            ("2020-01-01", "2", "0"),
            ("2021-01-01", "3", "1"),
        ):
            writer.writerow(
                {
                    "anchor_id": "a",
                    "capture_date": capture_date,
                    "version": version,
                    "pv_present": pv_present,
                    "confidence": "0.99",
                    "quality_flag": "usable",
                }
            )

    targets, provenance = decode_teacher_targets(raw, manifest)
    samples = build_sequences(raw, targets)

    assert provenance["decoder"] == "changepoint"
    assert targets["a"].teacher_key.startswith("INTERVAL|")
    assert samples[0].target_kind == "INTERVAL"
    assert samples[0].labels == ("unusable", "unusable", "unusable")


def test_rep_agreement_weights_pool_independent_decoded_pairs_by_status():
    rows = [
        {"sf": 1, "stratum": "stable", "rep": 1, "decoded": True, "teacher_key": "A"},
        {"sf": 1, "stratum": "stable", "rep": 2, "decoded": True, "teacher_key": "A"},
        {"sf": 1, "stratum": "stable", "rep": 3, "decoded": True, "teacher_key": "B"},
        {"sf": 2, "stratum": "stable", "rep": 1, "decoded": True, "teacher_key": "C"},
        {"sf": 2, "stratum": "stable", "rep": 2, "decoded": True, "teacher_key": "C"},
        {"sf": 3, "stratum": "thin", "rep": 1, "decoded": True, "teacher_key": "D"},
        {"sf": 3, "stratum": "thin", "rep": 2, "decoded": True, "teacher_key": "E"},
        {"sf": 3, "stratum": "thin", "rep": 3, "decoded": False, "teacher_key": "E"},
    ]

    result = rep_agreement_by_status(rows)

    assert result["by_status"]["stable"]["agreement"] == 0.5  # 2 equal / 4 pairs
    assert result["by_status"]["stable"]["weight"] == 0.5
    assert result["by_status"]["thin"]["agreement"] == 0.0
    assert result["by_status"]["thin"]["weight"] == 0.25  # prereg clip floor
    assert result["global"]["agreement"] == 0.4


def test_model_pair_has_same_interval_interface_and_matched_capacity():
    temporal, control, meta = build_model_pair(embed_dim=384)
    features = torch.randn(2, 3, 384)
    ordinal_days = torch.tensor([[17532.0, 17897.0, 18262.0], [17532.0, 0.0, 0.0]])
    mask = torch.tensor([[True, True, True], [True, False, False]])

    for model in (temporal, control):
        output = model(features, ordinal_days, mask)
        assert output["kind_logits"].shape == (2, 3)
        assert output["lower_logits"].shape == (2, 3)
        assert output["upper_logits"].shape == (2, 3)
        assert output["lower_logits"][1, 1].item() < -1e8

    assert 0.90 <= meta["parameter_ratio"] <= 1.10
    assert meta["temporal_params"] < 10_000_000


def test_decode_logits_enforces_valid_interval_and_maps_frames():
    prediction = decode_logits(
        kind_logits=np.array([5.0, 0.0, 0.0]),
        lower_logits=np.array([4.0, 0.0, 5.0, 0.0]),
        upper_logits=np.array([0.0, 5.0, 1.0, 0.0]),
        length=4,
    )

    assert prediction.kind == "INTERVAL"
    assert (prediction.lower_index, prediction.upper_index) == (0, 1)
    assert prediction_frame_labels(prediction, 4) == ("absent", "present", "present", "present")


def test_arm_metrics_use_fixed_teacher_denominators_and_exact_interval_keys():
    raw = {
        "features": np.zeros((3, 4), dtype=np.float32),
        "anchor_id": np.array(["a", "a", "a"]),
        "capture_date": np.array(["2019-01-01", "2020-01-01", "2021-01-01"]),
        "version": np.array(["1", "2", "3"]),
        "label_3class": np.array(["absent", "absent", "present"]),
        "split": np.array(["heldout", "heldout", "heldout"]),
        "terminal_status": np.array(["done_appears"] * 3),
    }
    sample = build_sequences(raw, fixture_targets(raw))[0]

    metrics = arm_point_metrics(
        [sample],
        {0: {"a": DecodedPrediction("INTERVAL", lower_index=0, upper_index=1)}},
    )

    assert metrics["interval_agreement"] == 0.0
    assert metrics["transition_fp_rate"] == 0.5  # one FP / two teacher-absent rows
    assert metrics["transition_fn_rate"] == 0.0
    assert metrics["present_to_unusable_rate"] == 0.0


def test_compare_arms_bootstraps_paired_anchor_clusters_with_all_seeds():
    raw = {
        "features": np.zeros((6, 4), dtype=np.float32),
        "anchor_id": np.array(["a", "a", "a", "b", "b", "b"]),
        "capture_date": np.array(["2019", "2020", "2021"] * 2),
        "version": np.array(["1", "2", "3"] * 2),
        "label_3class": np.array(["absent", "absent", "present"] * 2),
        "split": np.array(["heldout"] * 6),
        "terminal_status": np.array(["done_appears"] * 6),
    }
    samples = build_sequences(raw, fixture_targets(raw))
    correct = DecodedPrediction("INTERVAL", lower_index=1, upper_index=2)
    early = DecodedPrediction("INTERVAL", lower_index=0, upper_index=1)
    predictions_a = {seed: {"a": correct, "b": correct} for seed in (0, 1, 2)}
    predictions_b = {seed: {"a": early, "b": early} for seed in (0, 1, 2)}

    result = compare_arms(
        samples,
        predictions_a,
        predictions_b,
        bootstrap_draws=200,
        bootstrap_seed=7,
    )

    assert result["arm_a"]["n_seeds"] == 3
    assert result["point"]["interval_agreement_delta"] == 1.0
    assert result["ci95"]["interval_agreement_delta"] == [1.0, 1.0]
    assert "arm_a_present_to_unusable_rate" in result["ci95"]
    assert "arm_a_overall_decided_agreement" in result["ci95"]
    assert "arm_a_transition_decided_agreement" in result["ci95"]
    assert result["arm_a"]["confusion"]["overall"]["absent|absent"] == 12


def test_collate_sequences_pads_and_keeps_sequence_targets_separate_from_inputs():
    raw = {
        "features": np.zeros((4, 4), dtype=np.float32),
        "anchor_id": np.array(["a", "a", "a", "b"]),
        "capture_date": np.array(["2019", "2020", "2021", "2018"]),
        "version": np.array(["1", "2", "3", "1"]),
        "label_3class": np.array(["absent", "absent", "present", "present"]),
        "split": np.array(["train"] * 4),
        "terminal_status": np.array(
            ["done_appears"] * 3 + ["done_already_present_before_geid_history"]
        ),
    }
    samples = build_sequences(raw, fixture_targets(raw))

    batch = collate_sequences(samples, {"a": 1.25, "b": 0.75})

    assert batch["features"].shape == (2, 3, 4)
    assert batch["mask"].tolist() == [[True, True, True], [True, False, False]]
    assert batch["kind_target"].tolist() == [0, 1]
    assert batch["lower_target"].tolist() == [1, -100]
    assert batch["upper_target"].tolist() == [2, 0]
    assert batch["sample_weight"].tolist() == [1.25, 0.75]
    assert "labels" not in batch  # leakage guard: labels never enter model tensors
