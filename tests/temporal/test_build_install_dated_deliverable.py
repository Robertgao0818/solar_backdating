from __future__ import annotations

import csv
from pathlib import Path

import fiona
import geopandas as gpd
import pytest
from shapely.geometry import box

from scripts.temporal.build_install_dated_deliverable import (
    CSV_FIELDS,
    build_rows,
    classify_row,
    load_geometry_rows,
    run_gates,
    write_csv,
    write_gpkg,
)

METRIC_CRS = "EPSG:32735"


def _geometry_gpkg(tmp_path: Path, n: int = 5) -> Path:
    """n polygons; positional order 0..n-1 IS source_feature_id by design."""
    base_x = 600_000
    base_y = 7_100_000
    gdf = gpd.GeoDataFrame(
        {
            "source_grid": [f"JNB{i:04d}" for i in range(n)],
            "confidence": [0.9] * n,
            "score": [0.9] * n,
            "area_m2": [10.0 + i for i in range(n)],
            "orig_area_m2": [10.0 + i for i in range(n)],
            "sam_score": [0.95] * n,
            "n_merged": [1] * n,
            "source_tile": ["tileA"] * n,
        },
        geometry=[box(base_x + i * 20, base_y, base_x + i * 20 + 4, base_y + 4) for i in range(n)],
        crs=METRIC_CRS,
    )
    path = tmp_path / "geometry.gpkg"
    gdf.to_file(path, layer="solar_predictions", driver="GPKG")
    return path


def _anchors_csv(tmp_path: Path, n: int = 5) -> Path:
    path = tmp_path / "anchors_all.csv"
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["anchor_id", "source_feature_id", "chip_arm"])
        writer.writeheader()
        for i in range(n):
            writer.writerow({"anchor_id": f"t{i:08d}", "source_feature_id": i, "chip_arm": "A24"})
    return path


def _intervals_csv(tmp_path: Path, rows: list[dict]) -> Path:
    path = tmp_path / "install_intervals_all.csv"
    fields = [
        "lane", "anchor_id", "status", "latest_absent_date", "earliest_present_date",
        "install_interval_start", "install_interval_end", "install_mid_estimate", "confidence",
    ]
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            full = {f: "" for f in fields}
            full.update(row)
            writer.writerow(full)
    return path


def _vexcel_csv(tmp_path: Path, n: int = 5) -> Path:
    path = tmp_path / "vexcel.csv"
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["grid_id", "last_capture_date"])
        writer.writeheader()
        for i in range(n):
            writer.writerow({"grid_id": f"JNB{i:04d}", "last_capture_date": "2024-04-19"})
    return path


# --- classify_row: pure mapping logic -------------------------------------

def test_classify_done_appears_valid_bracket():
    row = {
        "status": "done_appears",
        "install_interval_start": "2021-01-01",
        "install_interval_end": "2021-07-01",
        "install_mid_estimate": "2021-04-02",
        "earliest_present_date": "2021-07-01",
        "confidence": "high",
    }
    out = classify_row(row)
    assert out.date_provider == "gehi_merged"
    assert out.date_is_bound == "0"
    assert out.install_date == "2021-04-02"
    assert out.undated_reason == ""
    assert out.interval_days == "181"


def test_classify_done_appears_inverted_interval_is_undated_not_dated():
    """infer_install_dates.py can leave status=done_appears with blank fields
    when its own inversion check fires -- must not emit a 'dated' row with no
    date (would violate the deliverable's interval invariant)."""
    row = {
        "status": "done_appears",
        "install_interval_start": "",
        "install_interval_end": "",
        "install_mid_estimate": "",
        "earliest_present_date": "2023-01-01",
        "confidence": "low",
    }
    out = classify_row(row)
    assert out.date_provider == ""
    assert out.install_date == ""
    assert out.undated_reason == "inverted_interval_mislabeled_appears"


def test_classify_done_installed_during_census():
    row = {
        "status": "done_installed_during_census",
        "install_interval_start": "2023-06-01",
        "install_interval_end": "2024-04-19",
        "confidence": "high",
    }
    out = classify_row(row)
    assert out.date_provider == "gehi_merged_census"
    assert out.date_is_bound == "1"
    assert out.install_date == "2024-04-19"
    assert out.earliest_present_date == "2024-04-19"
    assert out.census_bound == 1
    assert out.left_censored == 0


def test_classify_done_already_present():
    row = {
        "status": "done_already_present_before_geid_history",
        "install_interval_end": "2019-02-01",
        "earliest_present_date": "2019-02-01",
    }
    out = classify_row(row)
    assert out.date_provider == "gehi_merged_leftcensor"
    assert out.install_interval_start == ""
    assert out.install_date == "2019-02-01"
    assert out.left_censored == 1
    assert out.date_is_bound == "1"


def test_classify_ambiguous_status_is_undated_with_reason():
    row = {"status": "done_ambiguous_marker_missed_pv"}
    out = classify_row(row)
    assert out.date_provider == ""
    assert out.undated_reason == "marker_missed_pv"


# --- end-to-end build + gates ----------------------------------------------

def test_build_rows_and_gates_pass_on_clean_population(tmp_path: Path):
    n = 5
    geometry_rows = load_geometry_rows(_geometry_gpkg(tmp_path, n))
    anchors_by_sfid = {
        int(r["source_feature_id"]): r
        for r in csv.DictReader(_anchors_csv(tmp_path, n).open())
    }
    interval_specs = [
        {"lane": "a24", "anchor_id": "t00000000", "status": "done_appears",
         "install_interval_start": "2021-01-01", "install_interval_end": "2021-07-01",
         "install_mid_estimate": "2021-04-02", "earliest_present_date": "2021-07-01",
         "confidence": "high"},
        {"lane": "a24", "anchor_id": "t00000001", "status": "done_installed_during_census",
         "install_interval_start": "2023-06-01", "install_interval_end": "2024-04-19",
         "confidence": "high"},
        {"lane": "a24", "anchor_id": "t00000002", "status": "done_already_present_before_geid_history",
         "install_interval_end": "2019-02-01", "earliest_present_date": "2019-02-01"},
        {"lane": "a48", "anchor_id": "t00000003", "status": "done_ambiguous_no_recent_anchor"},
        {"lane": "a24", "anchor_id": "t00000004", "status": "done_appears",
         "install_interval_start": "", "install_interval_end": "", "install_mid_estimate": "",
         "earliest_present_date": "2023-01-01", "confidence": "low"},
    ]
    intervals_by_anchor = {
        r["anchor_id"]: r
        for r in csv.DictReader(_intervals_csv(tmp_path, interval_specs).open())
    }

    rows = build_rows(geometry_rows, anchors_by_sfid, intervals_by_anchor)
    assert len(rows) == n
    assert sorted(r["source_feature_id"] for r in rows) == list(range(n))

    csv_path = tmp_path / "out.csv"
    gpkg_path = tmp_path / "out.gpkg"
    write_csv(rows, csv_path)
    write_gpkg(rows, gpkg_path)

    vexcel = {f"JNB{i:04d}": __import__("datetime").date(2024, 4, 19) for i in range(n)}
    gates = run_gates(rows, expected_count=n, vexcel_flight_dates=vexcel, gpkg_path=gpkg_path)

    assert gates["row_count"]["pass"]
    assert gates["sfid_bijective"]["pass"]
    assert gates["interval_invariant"]["pass"], gates["interval_invariant"]
    assert gates["flight_date_ceiling"]["pass"], gates["flight_date_ceiling"]
    assert gates["csv_gpkg_centroid_agreement"]["pass"], gates["csv_gpkg_centroid_agreement"]

    coverage = gates["coverage_reconcile"]
    assert coverage["n_dated_bracket"] == 1  # t0
    assert coverage["n_census_bound"] == 1  # t1
    assert coverage["n_left_censored"] == 1  # t2
    assert coverage["n_undated"] == 2  # t3 (ambiguous) + t4 (inverted_interval bug)
    assert coverage["pass"]

    # CSV round-trips with the exact frozen column set.
    with csv_path.open(newline="", encoding="utf-8") as fh:
        header = next(csv.reader(fh))
    assert header == CSV_FIELDS

    with fiona.open(gpkg_path) as src:
        assert src.name == "solar_install_dated"
        assert len(src) == n
        crs = src.crs
    assert "32735" in str(crs)


def test_build_rows_raises_on_missing_anchor_join(tmp_path: Path):
    n = 2
    geometry_rows = load_geometry_rows(_geometry_gpkg(tmp_path, n))
    with pytest.raises(ValueError, match="missing from anchors_all.csv"):
        build_rows(geometry_rows, {}, {})
