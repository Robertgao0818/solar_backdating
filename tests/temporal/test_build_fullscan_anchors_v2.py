from __future__ import annotations

import csv
from datetime import date
from pathlib import Path

import pytest
from pyproj import Transformer

from scripts.temporal.anchor_derivation import derive_per_target_anchor_row
from scripts.temporal.build_fullscan_anchors_v2 import (
    BBOX_INPUT_FIELDS,
    OUTPUT_FIELDS,
    TARGET_INPUT_FIELDS,
    GridSource,
    _read_csv_strict,
    build_lattice_grid_source,
    build_rows,
    reconcile_against_v1,
    validate_invariants,
)


def _target(**overrides: str) -> dict[str, str]:
    row = {field: "" for field in TARGET_INPUT_FIELDS}
    row.update(
        {
            "anchor_id": "target-1",
            "chip_id": "group-1",
            "region_key": "johannesburg",
            "grid_id": "JNB0001",
            "source_inventory_path": "/inventory.gpkg",
            "source_feature_id": "10",
            "source_fid": "11",
            "source_grid": "JNB0001",
            "target_index": "3",
            "target_label": "T03",
            "centroid_lon": "28.05",
            "centroid_lat": "-26.20",
            "source_area_m2": "12.5",
            "source_width_m": "5.0",
            "source_height_m": "4.0",
            "confidence": "0.9",
            "score": "0.8",
            "sam_score": "0.7",
            "n_merged": "2",
            "target_offset_x_m": "20",
            "target_offset_y_m": "-5",
            "search_radius_m": "10",
            "chip_size_m": "96",
            "chip_lon_min": "0",
            "chip_lat_min": "0",
            "chip_lon_max": "0",
            "chip_lat_max": "0",
        }
    )
    row.update(overrides)
    return row


def _bbox_for(target: dict[str, str]) -> dict[str, str]:
    derived = derive_per_target_anchor_row(target)
    return {
        "anchor_id": target["anchor_id"],
        "chip_id": target["chip_id"],
        "region_key": target["region_key"],
        "grid_id": target["grid_id"],
        "centroid_lon": target["centroid_lon"],
        "centroid_lat": target["centroid_lat"],
        "chip_lon_min": str(derived["chip_lon_min"]),
        "chip_lat_min": str(derived["chip_lat_min"]),
        "chip_lon_max": str(derived["chip_lon_max"]),
        "chip_lat_max": str(derived["chip_lat_max"]),
    }


def _group() -> dict[str, str]:
    return {"anchor_id": "group-1", "chip_id": "group-1"}


def _single_grid_source() -> GridSource:
    return GridSource(
        resolver=lambda _row: ({"JNB0001"}, ()),
        valid_grid_ids=frozenset({"JNB0001"}),
        metadata={"method": "synthetic", "provisional": False},
    )


def test_strict_csv_schema_rejects_unknown_column(tmp_path: Path) -> None:
    path = tmp_path / "bbox.csv"
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=[*BBOX_INPUT_FIELDS, "unexpected"])
        writer.writeheader()
        writer.writerow({field: "x" for field in writer.fieldnames})

    with pytest.raises(ValueError, match=r"unknown=\['unexpected'\]"):
        _read_csv_strict(path, BBOX_INPUT_FIELDS, label="bbox")


def test_build_rows_rederives_group_frame_fields_and_uses_allowlist() -> None:
    target = _target()
    rows, diagnostics = build_rows(
        [_bbox_for(target)],
        [target],
        [_group()],
        grid_source=_single_grid_source(),
    )

    assert diagnostics["max_frozen_bbox_coordinate_error_m"] < 0.01
    assert len(rows) == 1
    row = rows[0]
    assert tuple(row) == OUTPUT_FIELDS
    assert row["anchor_id"] == "target-1"
    assert row["chip_id"] == "target-1"
    assert row["legacy_group_anchor_id"] == "group-1"
    assert row["target_index"] == 1
    assert row["target_label"] == "T01"
    assert row["target_offset_x_m"] == 0.0
    assert row["target_offset_y_m"] == 0.0
    assert row["source_grids"] == "JNB0001"
    assert row["chip_arm"] == "A24"
    assert row["geometry_version"] == "fullscan_target96_review24_v2"
    assert row["legacy_source_inventory_path"] == "/inventory.gpkg"


def test_build_rows_rejects_centroid_grid_omission() -> None:
    target = _target()
    bad_source = GridSource(
        resolver=lambda _row: ({"JNB0002"}, ()),
        valid_grid_ids=frozenset({"JNB0001", "JNB0002"}),
        metadata={"method": "synthetic", "provisional": False},
    )

    with pytest.raises(ValueError, match="omits centroid grid"):
        build_rows(
            [_bbox_for(target)],
            [target],
            [_group()],
            grid_source=bad_source,
        )


def test_lattice_grid_recompute_crosses_cell_boundary() -> None:
    to_wgs84 = Transformer.from_crs("EPSG:32735", "EPSG:4326", always_xy=True)
    lon1, lat1 = to_wgs84.transform(500_500.0, 7_100_500.0)
    lon2, lat2 = to_wgs84.transform(501_500.0, 7_100_500.0)
    capture_rows = [
        {"grid_id": "JNB0001", "centroid_lon": lon1, "centroid_lat": lat1},
        {"grid_id": "JNB0002", "centroid_lon": lon2, "centroid_lat": lat2},
    ]
    source = build_lattice_grid_source(capture_rows)
    boundary_lon, boundary_lat = to_wgs84.transform(501_000.0, 7_100_500.0)
    anchor = derive_per_target_anchor_row(
        {
            "anchor_id": "boundary",
            "grid_id": "JNB0002",
            "centroid_lon": boundary_lon,
            "centroid_lat": boundary_lat,
        }
    )

    grids, unresolved = source.resolver(anchor)

    assert grids == {"JNB0001", "JNB0002"}
    assert unresolved == ()


def test_invariants_fail_on_non_96m_bbox() -> None:
    target = _target()
    rows, _ = build_rows(
        [_bbox_for(target)], [target], [_group()], grid_source=_single_grid_source()
    )
    rows[0]["chip_lon_max"] = rows[0]["chip_lon_min"]

    with pytest.raises(ValueError, match="bbox side invariant"):
        validate_invariants(rows, expected_count=1, expected_arms={"A24": 1})


def test_reconciliation_reports_grid_and_census_delta_without_population_gate() -> None:
    v2 = [{"anchor_id": "a", "grid_id": "JNB0002", "source_grids": "JNB0002"}]
    v1 = [{"anchor_id": "a", "grid_id": "JNB0001", "source_grids": "JNB0001"}]

    summary = reconcile_against_v1(
        v2,
        v1,
        capture_dates={
            "JNB0001": date(2024, 2, 17),
            "JNB0002": date(2024, 4, 19),
        },
        expect_fallback_counts=False,
        enforce_expectations=False,
    )

    assert summary["changed_source_grid_sets"] == 1
    assert summary["changed_census_dates"] == 1
    assert summary["census_delta_days_histogram"] == {"62": 1}
