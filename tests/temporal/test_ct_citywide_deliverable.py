from __future__ import annotations

import sqlite3
from pathlib import Path

import fiona
from shapely.geometry import box

from scripts.temporal.build_ct_citywide_install_dated_deliverable import (
    CSV_FIELDS_CITYWIDE,
    INTERVAL_CSV_FIELDS,
    attach_citywide_fields,
    interval_from_top52_row,
)
from scripts.temporal.build_ct_install_dated_deliverable import (
    GPKG_LAST_CHANGE,
    INTERVAL_REQUIRED_FIELDS,
    METRIC_CRS,
    classify,
    freeze_gpkg_last_change,
    write_gpkg,
)


def test_interval_csv_fields_match_required_contract() -> None:
    assert set(INTERVAL_CSV_FIELDS) == INTERVAL_REQUIRED_FIELDS


def test_top52_dated_row_round_trips_through_classify() -> None:
    row = {
        "source_anchor_id": "ct_t00000001",
        "source_grid": "CPT0001",
        "scan_status": "done_appears",
        "latest_absent_date": "2023-01-01",
        "earliest_present_date": "2024-01-01",
        "install_interval_start": "2023-01-01",
        "install_interval_end": "2024-01-01",
        "install_date": "2023-07-02",
        "n_observations": "8",
        "n_absent": "4",
        "n_present": "4",
        "n_unusable": "0",
        "n_rounds": "1",
        "scan_state_path": "state.json",
        "install_confidence": "high",
        "scan_notes": "ok",
    }
    interval = interval_from_top52_row(row)
    classified = classify(interval)
    assert classified.date_status == "done_appears"
    assert classified.date_is_bound == "0"
    assert classified.install_date == "2023-07-02"
    assert classified.undated_reason == ""
    assert classified.left_censored == 0
    assert classified.census_bound == 0


def test_top52_undated_row_stays_undated() -> None:
    row = {
        "source_anchor_id": "ct_t00000002",
        "source_grid": "CPT0002",
        "scan_status": "done_ambiguous_nonmonotonic",
        "latest_absent_date": "",
        "earliest_present_date": "",
        "install_interval_start": "",
        "install_interval_end": "",
        "install_date": "",
        "n_observations": "5",
        "n_absent": "2",
        "n_present": "3",
        "n_unusable": "0",
        "n_rounds": "1",
        "scan_state_path": "state.json",
        "install_confidence": "",
        "scan_notes": "",
    }
    classified = classify(interval_from_top52_row(row))
    assert classified.install_date == ""
    assert classified.undated_reason == "nonmonotonic"


def test_citywide_extra_fields_written_to_gpkg(tmp_path: Path) -> None:
    rows = [
        {
            "source_feature_id": 1,
            "source_grid": "CPT0001",
            "source_grids": "CPT0001",
            "centroid_lon": 18.4,
            "centroid_lat": -33.9,
            "area_m2": 12.0,
            "confidence": 0.9,
            "date_provider": "gehi_merged",
            "date_status": "done_appears",
            "date_is_bound": "0",
            "install_date": "2023-07-02",
            "install_interval_start": "2023-01-01",
            "install_interval_end": "2024-01-01",
            "latest_absent_date": "2023-01-01",
            "earliest_present_date": "2024-01-01",
            "install_confidence": "high",
            "source_anchor_id": "ct_t00000001",
            "undated_reason": "",
            "scan_status": "done_appears",
            "n_observations": 4,
            "n_absent": 2,
            "n_present": 2,
            "n_unusable": 0,
            "n_rounds": 1,
            "scan_state_path": "state.json",
            "scan_notes": "",
            "left_censored": 0,
            "census_bound": 0,
            "chip_arm": "A24",
            "review_extent_m": 24.0,
            "geometry_version": "fullscan_target96_review24_v2",
            "cohort_version": "ct_citywide_v1_20260812",
            "census_cutoff": "2025-01-31",
            "h3_r7": "87ad368e9ffffff",
            "source_kind": "primary_lite",
            "wave": "wave_01",
            "cohort": "citywide",
            "_geometry": box(260_000, 6_240_000, 260_004, 6_240_004).__geo_interface__,
        }
    ]
    attach_citywide_fields(
        rows,
        {
            "ct_t00000001": {
                "h3_r7": "87ad368e9ffffff",
                "source_kind": "primary_lite",
                "wave": "wave_01",
                "cohort": "citywide",
            }
        },
    )
    gpkg = tmp_path / "out.gpkg"
    write_gpkg(rows, gpkg, fields=CSV_FIELDS_CITYWIDE)
    freeze_gpkg_last_change(gpkg)
    with fiona.open(gpkg, layer="ct_solar_install_dated") as src:
        assert "32734" in str(src.crs)
        assert "h3_r7" in src.schema["properties"]
        feature = next(iter(src))
        assert feature["properties"]["h3_r7"] == "87ad368e9ffffff"
        assert feature["properties"]["source_kind"] == "primary_lite"
    with sqlite3.connect(gpkg) as connection:
        stamp = connection.execute("SELECT last_change FROM gpkg_contents").fetchone()[0]
    assert stamp == GPKG_LAST_CHANGE
    assert METRIC_CRS == "EPSG:32734"
