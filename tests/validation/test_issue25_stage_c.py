from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from scripts.validation.issue25_stage_c import (
    ROUTED_WINNER_ID,
    build_stage2_winner_decision,
    choose_stage2_winner,
    compute_routed_counterfactual,
    pairwise_agreement,
    prepare_b0_groups,
    prepare_stage1_anchors,
    prepare_stage2_anchors,
    route_chip_arm,
    split_anchors_by_chip_arm,
    validate_authenticated_preflight,
    validate_scan_matrix,
    write_stage2_winner_confirmation,
)


def _write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def test_prepare_stage1_anchors_joins_frozen_footprint_bbox(tmp_path: Path) -> None:
    manifest = tmp_path / "target_anchors.csv"
    targets = tmp_path / "chip_targets.csv"
    output = tmp_path / "stage1.csv"
    _write_csv(
        manifest,
        [
            {"anchor_id": "t1", "sample_stage": "stage1_core", "chip_size_m": 96},
            {"anchor_id": "t2", "sample_stage": "stage2_precision", "chip_size_m": 96},
        ],
    )
    _write_csv(
        targets,
        [
            {"anchor_id": "t1", "source_width_m": 5.5, "source_height_m": 8.25},
            {"anchor_id": "t2", "source_width_m": 4.0, "source_height_m": 6.0},
        ],
    )

    count = prepare_stage1_anchors(manifest, targets, output, expected_count=1)

    assert count == 1
    with output.open() as fh:
        rows = list(csv.DictReader(fh))
    assert rows == [
        {
            "anchor_id": "t1",
            "sample_stage": "stage1_core",
            "chip_size_m": "96",
            "source_width_m": "5.5",
            "source_height_m": "8.25",
        }
    ]


def test_prepare_stage1_anchors_fails_when_bbox_join_is_missing(tmp_path: Path) -> None:
    manifest = tmp_path / "target_anchors.csv"
    targets = tmp_path / "chip_targets.csv"
    _write_csv(
        manifest,
        [{"anchor_id": "t1", "sample_stage": "stage1_core", "chip_size_m": 96}],
    )
    _write_csv(
        targets,
        [{"anchor_id": "other", "source_width_m": 5.5, "source_height_m": 8.25}],
    )

    with pytest.raises(ValueError, match="missing footprint bbox"):
        prepare_stage1_anchors(manifest, targets, tmp_path / "out.csv", expected_count=1)


def test_prepare_stage2_anchors_selects_precision_extension(tmp_path: Path) -> None:
    manifest = tmp_path / "target_anchors.csv"
    targets = tmp_path / "chip_targets.csv"
    output = tmp_path / "stage2.csv"
    _write_csv(
        manifest,
        [
            {
                "anchor_id": "t1",
                "sample_stage": "stage1_core",
                "chip_size_m": 96,
                "source_area_m2": 12.0,
            },
            {
                "anchor_id": "t2",
                "sample_stage": "stage2_precision",
                "chip_size_m": 96,
                "source_area_m2": 12.0,
            },
            {
                "anchor_id": "t3",
                "sample_stage": "stage2_precision",
                "chip_size_m": 96,
                "source_area_m2": 55.0,
            },
        ],
    )
    _write_csv(
        targets,
        [
            {"anchor_id": "t1", "source_width_m": 5.5, "source_height_m": 8.25},
            {"anchor_id": "t2", "source_width_m": 4.0, "source_height_m": 6.0},
            {"anchor_id": "t3", "source_width_m": 10.0, "source_height_m": 12.0},
        ],
    )

    count = prepare_stage2_anchors(manifest, targets, output, expected_count=2)

    assert count == 2
    with output.open() as fh:
        rows = list(csv.DictReader(fh))
    assert [row["anchor_id"] for row in rows] == ["t2", "t3"]
    assert rows[0]["chip_arm"] == "A24"
    assert rows[0]["review_extent_m"] == "24"
    assert rows[1]["chip_arm"] == "A48"
    assert rows[1]["review_extent_m"] == "48"


def test_route_chip_arm_uses_single_forty_cut() -> None:
    assert route_chip_arm(39.999) == "A24"
    assert route_chip_arm(40.0) == "A48"
    assert route_chip_arm(100.0) == "A48"


def test_split_anchors_by_chip_arm(tmp_path: Path) -> None:
    anchors = tmp_path / "stage2.csv"
    _write_csv(
        anchors,
        [
            {"anchor_id": "t1", "chip_arm": "A24"},
            {"anchor_id": "t2", "chip_arm": "A48"},
            {"anchor_id": "t3", "chip_arm": "A24"},
        ],
    )
    counts = split_anchors_by_chip_arm(anchors, tmp_path / "splits")
    assert counts == {"A24": 2, "A48": 1}
    with (tmp_path / "splits" / "anchors_A24.csv").open() as fh:
        assert [r["anchor_id"] for r in csv.DictReader(fh)] == ["t1", "t3"]


def test_prepare_b0_groups_freezes_unique_legacy_group_subset(tmp_path: Path) -> None:
    sample = tmp_path / "sample.csv"
    groups = tmp_path / "groups.csv"
    output = tmp_path / "b0.csv"
    _write_csv(
        sample,
        [
            {"anchor_id": "t1", "chip_id": "g1", "b0_bridge": "True"},
            {"anchor_id": "t2", "chip_id": "g1", "b0_bridge": "True"},
            {"anchor_id": "t3", "chip_id": "g2", "b0_bridge": "False"},
        ],
    )
    _write_csv(
        groups,
        [
            {"anchor_id": "g1", "chip_size_m": 96},
            {"anchor_id": "g2", "chip_size_m": 96},
        ],
    )

    count = prepare_b0_groups(sample, groups, output, expected_targets=2)

    assert count == 1
    with output.open() as fh:
        assert [row["anchor_id"] for row in csv.DictReader(fh)] == ["g1"]


def test_validate_authenticated_preflight_requires_schema_success_and_model(tmp_path: Path) -> None:
    audit = tmp_path / "audit" / "t1"
    audit.mkdir(parents=True)
    (audit / "round_1.jsonl").write_text(
        json.dumps(
            {
                "stage": "batch_attempt_1",
                "n_picks": 2,
                "n_valid": 2,
                "missing_indices": [],
                "error": None,
                "raw_response": '{"chip_index": 1}',
            }
        )
        + "\n"
    )
    (tmp_path / "scoring_provenance.jsonl").write_text(
        json.dumps({"model_id": "gemini-3.1-flash-lite"}) + "\n"
    )

    summary = validate_authenticated_preflight(tmp_path, "gemini-3.1-flash-lite")

    assert summary == {"audit_calls": 1, "scored_observations": 1}


def test_validate_authenticated_preflight_rejects_empty_response(tmp_path: Path) -> None:
    audit = tmp_path / "audit" / "t1"
    audit.mkdir(parents=True)
    (audit / "round_1.jsonl").write_text(
        json.dumps(
            {
                "stage": "batch_attempt_1",
                "n_picks": 1,
                "n_valid": 0,
                "missing_indices": [1],
                "error": "empty response",
                "raw_response": "",
            }
        )
        + "\n"
    )
    (tmp_path / "scoring_provenance.jsonl").write_text("")

    with pytest.raises(ValueError, match="authenticated preflight audit failed"):
        validate_authenticated_preflight(tmp_path, "gemini-3.1-flash-lite")


def test_pairwise_agreement_uses_all_ten_pairs_per_five_rep_target() -> None:
    stats = pairwise_agreement(
        {
            "t1": ["A", "A", "A", "A", "A"],
            "t2": ["A", "A", "B", "B", "B"],
        }
    )

    # t1: 10/10; t2: C(2,2)+C(3,2)=4/10.
    assert stats == {"agreement": 0.7, "matching_pairs": 14, "n_pairs": 20, "n_targets": 2}


def test_choose_stage2_winner_prefers_large_safe_small_bucket_leader() -> None:
    winner = choose_stage2_winner(
        {
            "A24": {"small": {"agreement": 0.76}, "large": {"agreement": 0.80}},
            "A48": {"small": {"agreement": 0.78}, "large": {"agreement": 0.79}},
            "A96": {"small": {"agreement": 0.74}, "large": {"agreement": 0.82}},
        }
    )

    assert winner["arm"] == "A24"
    assert winner["mode"] == "single_arm"
    assert winner["large_safety_floor"] == pytest.approx(0.7982)


def _arm_metrics_for_route() -> dict:
    """Minimal per-bucket metrics matching the Stage-1 arithmetic pattern."""
    return {
        "A24": {
            "small": {
                "agreement": 0.8272277227722772,
                "matching_pairs": 1671,
                "n_pairs": 2020,
                "n_targets": 202,
            },
            "large": {
                "agreement": 0.8919191919191919,
                "matching_pairs": 883,
                "n_pairs": 990,
                "n_targets": 99,
            },
            "per_area_bucket": {
                "a_xs_lt15": {
                    "agreement": 0.842156862745098,
                    "matching_pairs": 859,
                    "n_pairs": 1020,
                    "n_targets": 102,
                },
                "b_sm_15_40": {
                    "agreement": 0.812,
                    "matching_pairs": 812,
                    "n_pairs": 1000,
                    "n_targets": 100,
                },
                "c_md_40_100": {
                    "agreement": 0.9090909090909091,
                    "matching_pairs": 900,
                    "n_pairs": 990,
                    "n_targets": 99,
                },
                "d_lg_ge100": {
                    "agreement": 0.8919191919191919,
                    "matching_pairs": 883,
                    "n_pairs": 990,
                    "n_targets": 99,
                },
            },
        },
        "A48": {
            "small": {
                "agreement": 0.80990099009901,
                "matching_pairs": 1636,
                "n_pairs": 2020,
                "n_targets": 202,
            },
            "large": {
                "agreement": 0.9646464646464646,
                "matching_pairs": 955,
                "n_pairs": 990,
                "n_targets": 99,
            },
            "per_area_bucket": {
                "a_xs_lt15": {
                    "agreement": 0.8049019607843138,
                    "matching_pairs": 821,
                    "n_pairs": 1020,
                    "n_targets": 102,
                },
                "b_sm_15_40": {
                    "agreement": 0.815,
                    "matching_pairs": 815,
                    "n_pairs": 1000,
                    "n_targets": 100,
                },
                "c_md_40_100": {
                    "agreement": 0.9303030303030303,
                    "matching_pairs": 921,
                    "n_pairs": 990,
                    "n_targets": 99,
                },
                "d_lg_ge100": {
                    "agreement": 0.9646464646464646,
                    "matching_pairs": 955,
                    "n_pairs": 990,
                    "n_targets": 99,
                },
            },
        },
        "A96": {
            "small": {"agreement": 0.79, "matching_pairs": 1, "n_pairs": 1, "n_targets": 1},
            "large": {"agreement": 0.93, "matching_pairs": 1, "n_pairs": 1, "n_targets": 1},
            "per_area_bucket": {},
        },
    }


def test_compute_routed_counterfactual_reuses_stage1_pairs() -> None:
    routed = compute_routed_counterfactual(_arm_metrics_for_route())

    assert routed["arm"] == ROUTED_WINNER_ID
    assert routed["mode"] == "routed"
    assert routed["overall"]["matching_pairs"] == 3547
    assert routed["overall"]["n_pairs"] == 4000
    assert routed["overall"]["agreement"] == pytest.approx(0.88675)
    assert routed["small"]["agreement"] == pytest.approx(0.8272277227722772)
    assert routed["large"]["agreement"] == pytest.approx(0.9646464646464646)
    assert routed["per_area_bucket"]["b_sm_15_40"]["arm"] == "A24"
    assert routed["per_area_bucket"]["d_lg_ge100"]["arm"] == "A48"
    assert routed["per_stratum_gates"]["small"]["pass"] is True
    assert routed["per_stratum_gates"]["large"]["pass"] is True


def test_build_stage2_winner_decision_prefers_routed_over_single_arm() -> None:
    decision = build_stage2_winner_decision(_arm_metrics_for_route())

    assert decision["arm"] == ROUTED_WINNER_ID
    assert decision["mode"] == "routed"
    assert decision["single_arm_prereg_winner"]["arm"] == "A24"
    assert decision["overall_agreement"] == pytest.approx(0.88675)


def test_write_stage2_winner_confirmation(tmp_path: Path) -> None:
    analysis = tmp_path / "stage1_analysis.json"
    decision = tmp_path / "decision.json"
    sentinel = tmp_path / ".confirmed"
    payload = build_stage2_winner_decision(_arm_metrics_for_route())
    analysis.write_text(json.dumps({"stage2_winner": payload}), encoding="utf-8")

    confirmed = write_stage2_winner_confirmation(analysis, decision, sentinel)

    assert confirmed["arm"] == ROUTED_WINNER_ID
    assert sentinel.read_text(encoding="utf-8").strip().endswith("Z")
    assert json.loads(decision.read_text(encoding="utf-8"))["mode"] == "routed"


def test_validate_scan_matrix_requires_exact_states_and_frozen_model(tmp_path: Path) -> None:
    anchors = tmp_path / "anchors.csv"
    _write_csv(anchors, [{"anchor_id": "t1"}])
    matrix = tmp_path / "matrix"
    for rep in (1, 2):
        states = matrix / f"rep{rep}" / "scan_states"
        states.mkdir(parents=True)
        (states / "t1.json").write_text(json.dumps({"status": "done_appears"}))
        (matrix / f"rep{rep}" / "scoring_provenance.jsonl").write_text(
            json.dumps({"model_id": "gemini-3.1-flash-lite"}) + "\n"
        )

    summary = validate_scan_matrix(
        matrix,
        anchors,
        reps=2,
        model="gemini-3.1-flash-lite",
    )

    assert summary == {"anchors": 1, "reps": 2, "states": 2}
