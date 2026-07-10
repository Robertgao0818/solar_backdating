from __future__ import annotations

import csv
from pathlib import Path

import pytest

from scripts.validation.issue25_stage_c import prepare_stage1_anchors


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
