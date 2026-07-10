from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from scripts.validation.issue25_stage_c import (
    choose_stage2_winner,
    pairwise_agreement,
    prepare_stage1_anchors,
    prepare_stage2_anchors,
    validate_authenticated_preflight,
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

    count = prepare_stage2_anchors(manifest, targets, output, expected_count=1)

    assert count == 1
    with output.open() as fh:
        assert next(csv.DictReader(fh))["anchor_id"] == "t2"


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
    assert winner["large_safety_floor"] == pytest.approx(0.7982)
