from __future__ import annotations

from datetime import date
from pathlib import Path

import fiona
import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import box

from scripts.temporal.build_ct_chip_groups_v1 import (
    EXPECTED_INPUT_FIELDS,
    build_rows as build_group_rows,
    validate_anchors,
)
from scripts.temporal.build_ct_install_dated_deliverable import (
    CENSUS_CUTOFF,
    METRIC_CRS,
    build_rows as build_deliverable_rows,
    classify,
    load_geometry,
    run_gates,
    write_gpkg,
)


def _anchor_frame(n: int = 3) -> pd.DataFrame:
    rows = []
    for idx in range(n):
        row = {field: "" for field in EXPECTED_INPUT_FIELDS}
        row.update(
            {
                "anchor_id": f"ct_t{idx:08d}",
                "source_feature_id": idx,
                "source_grid": f"CPT{idx:04d}",
                "centroid_grid": f"CPT{idx:04d}",
                "source_grids": f"CPT{idx:04d}",
                "region_key": "cape_town",
                "centroid_lon": 18.4 + idx / 1000,
                "centroid_lat": -33.9,
                "metric_crs": METRIC_CRS,
                "area_m2": 10.0,
                "confidence": 0.9,
                "source_width_m": 4.0,
                "source_height_m": 3.0,
                "chip_lon_min": 18.39,
                "chip_lat_min": -33.91,
                "chip_lon_max": 18.41,
                "chip_lat_max": -33.89,
                "source_box_width_m": 96.0,
                "source_box_height_m": 96.0,
                "center_offset_m": 0.0,
                "chip_arm": "A24",
                "review_extent_m": 24.0,
                "geometry_exception": "",
                "review_required": False,
                "geometry_version": "fullscan_target96_review24_v2",
                "input_inventory_sha256": "a" * 64,
                "roster_sha256": "b" * 64,
                "cohort_version": "ct_top52_tie_band_v1_20260724",
            }
        )
        rows.append(row)
    return pd.DataFrame(rows)


def _inventory(tmp_path: Path, n: int = 3) -> Path:
    gdf = gpd.GeoDataFrame(
        {
            "source_feature_id": list(range(n)),
            "source_grid": [f"CPT{i:04d}" for i in range(n)],
            "area_m2": [16.0] * n,
            "confidence": [0.95] * n,
        },
        geometry=[box(260_000 + i * 20, 6_240_000, 260_004 + i * 20, 6_240_004) for i in range(n)],
        crs=METRIC_CRS,
    )
    path = tmp_path / "ct.gpkg"
    gdf.to_file(path, layer="ct_solar_inventory", driver="GPKG")
    return path


def _interval(anchor_id: str, grid_id: str, status: str, **updates: str) -> dict[str, str]:
    row = {
        "anchor_id": anchor_id,
        "region_key": "cape_town",
        "grid_id": grid_id,
        "status": status,
        "latest_absent_date": "",
        "earliest_present_date": "",
        "install_interval_start": "",
        "install_interval_end": "",
        "install_mid_estimate": "",
        "n_observations": "3",
        "n_absent": "1",
        "n_present": "2",
        "n_unusable": "0",
        "n_rounds": "1",
        "scan_state_path": "/tmp/state.json",
        "confidence": "high",
        "notes": "",
    }
    row.update(updates)
    return row


def test_ct_groups_are_one_to_one_target_centered() -> None:
    anchors = _anchor_frame()
    validate_anchors(anchors, expected_count=3)
    groups, targets = build_group_rows(anchors)
    assert groups.anchor_id.tolist() == targets.anchor_id.tolist()
    assert groups.anchor_id.tolist() == groups.chip_id.tolist()
    assert groups.n_targets.tolist() == [1, 1, 1]
    assert groups.target_anchor_ids.tolist() == groups.anchor_id.tolist()
    assert set(groups.metric_crs) == {METRIC_CRS}
    assert set(groups.chip_size_m) == {96.0}
    assert set(groups.target_offset_x_m) == {0.0}
    assert set(groups.target_offset_y_m) == {0.0}


def test_ct_group_allowlist_rejects_stale_column() -> None:
    anchors = _anchor_frame()
    anchors["legacy_jhb_offset"] = 1
    with pytest.raises(ValueError, match="unknown=.*legacy_jhb_offset"):
        validate_anchors(anchors, expected_count=3)


def test_ct_deliverable_join_crs_and_gates(tmp_path: Path) -> None:
    anchors_df = _anchor_frame()
    anchors = [{key: str(value) for key, value in row.items()} for row in anchors_df.to_dict("records")]
    intervals = [
        _interval(
            "ct_t00000000",
            "CPT0000",
            "done_appears",
            latest_absent_date="2023-01-01",
            earliest_present_date="2024-01-01",
            install_interval_start="2023-01-01",
            install_interval_end="2024-01-01",
            install_mid_estimate="2023-07-02",
        ),
        _interval(
            "ct_t00000001",
            "CPT0001",
            "done_installed_during_census",
            latest_absent_date="2024-01-01",
            install_interval_start="2024-01-01",
            install_interval_end=CENSUS_CUTOFF.isoformat(),
        ),
        _interval(
            "ct_t00000002",
            "CPT0002",
            "done_ambiguous_nonmonotonic",
        ),
    ]
    geometry = load_geometry(_inventory(tmp_path), {0, 1, 2})
    rows = build_deliverable_rows(
        anchors,
        {row["anchor_id"]: row for row in intervals},
        geometry,
    )
    gates = run_gates(rows, expected_count=3)
    assert all(result["pass"] for result in gates.values()), gates
    assert gates["coverage_reconcile"]["total"] == 3
    assert gates["census_ceiling"]["cutoff"] == "2025-01-31"

    gpkg = tmp_path / "out.gpkg"
    write_gpkg(rows, gpkg)
    with fiona.open(gpkg, layer="ct_solar_install_dated") as src:
        assert len(src) == 3
        assert "32734" in str(src.crs)


def test_ct_deliverable_rejects_post_census_output() -> None:
    row = {
        "source_anchor_id": "a",
        "source_feature_id": 1,
        "source_grid": "CPT0001",
        "install_interval_start": "2024-01-01",
        "install_date": "2025-02-01",
        "install_interval_end": "2025-02-01",
        "date_is_bound": "0",
        "census_bound": 0,
        "left_censored": 0,
        "undated_reason": "",
    }
    gates = run_gates([row], expected_count=1)
    assert not gates["census_ceiling"]["pass"]


def test_ct_classification_keeps_ambiguous_rows_undated() -> None:
    out = classify({"status": "done_ambiguous_marker_missed_pv"})
    assert out.install_date == ""
    assert out.undated_reason == "marker_missed_pv"
    assert CENSUS_CUTOFF == date(2025, 1, 31)
