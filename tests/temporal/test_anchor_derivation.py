from __future__ import annotations

import csv
from pathlib import Path

import pytest
from pyproj import Transformer

from scripts.temporal.anchor_derivation import (
    derive_per_target_anchor_row,
    derive_per_target_anchor_rows,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
FROZEN_DIR = PROJECT_ROOT / "docs/replan_v2/issue25_manifest_20260710"


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as fh:
        return [dict(row) for row in csv.DictReader(fh)]


def test_derivation_resets_group_frame_fields_and_keeps_crosswalk() -> None:
    row = derive_per_target_anchor_row(
        {
            "anchor_id": "target-1",
            "chip_id": "legacy-group-9",
            "grid_id": "JNB0001",
            "region_key": "johannesburg",
            "centroid_lon": 28.05,
            "centroid_lat": -26.20,
            "target_index": 4,
            "target_label": "T04",
            "target_offset_x_m": 21.0,
            "target_offset_y_m": -8.0,
            "search_radius_m": 7.5,
        }
    )

    assert row["anchor_id"] == "target-1"
    assert row["chip_id"] == "target-1"
    assert row["legacy_group_anchor_id"] == "legacy-group-9"
    assert row["target_index"] == 1
    assert row["target_label"] == "T01"
    assert row["target_offset_x_m"] == 0.0
    assert row["target_offset_y_m"] == 0.0
    assert row["search_radius_m"] == 7.5


def test_derivation_bbox_is_96m_and_centroid_centered() -> None:
    row = derive_per_target_anchor_row(
        {
            "anchor_id": "target-1",
            "grid_id": "JNB0001",
            "centroid_lon": 28.05,
            "centroid_lat": -26.20,
        }
    )
    to_metric = Transformer.from_crs("EPSG:4326", "EPSG:32735", always_xy=True)
    min_x, min_y = to_metric.transform(row["chip_lon_min"], row["chip_lat_min"])
    max_x, max_y = to_metric.transform(row["chip_lon_max"], row["chip_lat_max"])
    center_x, center_y = to_metric.transform(row["centroid_lon"], row["centroid_lat"])

    assert max_x - min_x == pytest.approx(96.0, abs=0.01)
    assert max_y - min_y == pytest.approx(96.0, abs=0.01)
    assert (min_x + max_x) / 2.0 == pytest.approx(center_x, abs=0.01)
    assert (min_y + max_y) / 2.0 == pytest.approx(center_y, abs=0.01)


def test_shared_derivation_matches_frozen_issue25_manifest() -> None:
    source = _read_csv(FROZEN_DIR / "sample_manifest.csv")
    expected = _read_csv(FROZEN_DIR / "target_anchors_96m.csv")

    actual = derive_per_target_anchor_rows(source)
    assert [row["anchor_id"] for row in actual] == [row["anchor_id"] for row in expected]

    bbox_fields = {"chip_lon_min", "chip_lat_min", "chip_lon_max", "chip_lat_max"}
    for actual_row, expected_row in zip(actual, expected, strict=True):
        for field, expected_value in expected_row.items():
            if field in bbox_fields:
                # PROJ patch releases can differ by a few final decimal digits;
                # pin the geometry rather than an implementation-specific string.
                assert float(actual_row[field]) == pytest.approx(
                    float(expected_value), abs=1e-12
                )
            else:
                assert str(actual_row[field]) == expected_value
