from __future__ import annotations

import csv
import sys
from pathlib import Path

import numpy as np
import rasterio
from rasterio.transform import from_bounds

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "temporal"))

import run_ct05_chip_pipeline as chips  # noqa: E402


def candidate(
    anchor_id: str,
    date: str,
    *,
    provider: str = "TM",
    version: str = "",
    zoom: int = 19,
) -> dict[str, object]:
    return {
        "anchor_id": anchor_id,
        "capture_date": date,
        "version": version,
        "provider": provider,
        "requested_zoom": zoom,
        "reference_only": 0,
    }


def anchor(anchor_id: str) -> dict[str, object]:
    return {
        "anchor_id": anchor_id,
        "chip_lon_min": 18.399,
        "chip_lat_min": -34.001,
        "chip_lon_max": 18.401,
        "chip_lat_max": -33.999,
    }


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def write_tif(path: Path, *, flat: bool = False, clipped: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(4)
    data = (
        np.full((3, 64, 64), 255, dtype=np.uint8)
        if flat
        else rng.integers(20, 230, size=(3, 64, 64), dtype=np.uint8)
    )
    bounds = (18.399, -34.001, 18.401, -33.999)
    if clipped:
        bounds = (18.3995, -34.0005, 18.4005, -33.9995)
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=64,
        height=64,
        count=3,
        dtype="uint8",
        crs="EPSG:4326",
        transform=from_bounds(*bounds, 64, 64),
    ) as dst:
        dst.write(data)


def test_download_plan_is_complete_disjoint_and_splits_zoom(tmp_path: Path) -> None:
    rows = [
        candidate("a1", "2020-01-01"),
        candidate("a2", "2020-02-01", provider="Wayback", version="777"),
        candidate("a3", "2020-03-01", zoom=18),
    ]
    result = chips.plan_downloads(rows, chips.DEFAULT_ROUTES, tmp_path)
    assert result["candidate_count"] == 3
    assert sum(row["candidate_count"] for row in result["shards"]) == 3
    populated = [row for row in result["shards"] if row["candidate_count"]]
    assert {row["download_ladder"] for row in populated} == {"19,18", "18"}


def test_download_plan_rejects_duplicate_anchor_date(tmp_path: Path) -> None:
    rows = [
        candidate("a1", "2020-01-01"),
        candidate("a1", "2020-01-01", provider="Wayback", version="777"),
    ]
    try:
        chips.plan_downloads(rows, chips.DEFAULT_ROUTES, tmp_path)
    except ValueError as exc:
        assert "duplicate" in str(exc)
    else:
        raise AssertionError("duplicate candidate was accepted")


def test_download_plan_applies_run3_2019_floor(tmp_path: Path) -> None:
    rows = [
        candidate("old", "2018-12-31"),
        candidate("floor", "2019-01-01"),
        candidate("new", "2025-02-01"),
    ]
    result = chips.plan_downloads(rows, chips.DEFAULT_ROUTES, tmp_path)
    assert result["input_candidate_count"] == 3
    assert result["candidate_count"] == 2
    assert result["min_capture_date"] == "2019-01-01"
    planned = [
        row
        for shard in result["shards"]
        for row in read_csv(Path(shard["path"]))
    ]
    assert {row["anchor_id"] for row in planned} == {"floor", "new"}


def manifest_row(row: dict[str, object], path: Path) -> dict[str, object]:
    return {
        **row,
        "actual_zoom": row["requested_zoom"],
        "status": "ok",
        "path": str(path),
        "sha256": chips.sha256_file(path),
    }


def test_chip_qa_passes_hash_decode_bbox_and_pixels(tmp_path: Path) -> None:
    row = candidate("a1", "2020-01-01")
    tif = tmp_path / "good.tif"
    write_tif(tif)
    manifest = tmp_path / "manifest.csv"
    chips.write_csv_rows(
        manifest,
        [manifest_row(row, tif)],
        list(manifest_row(row, tif).keys()),
    )
    summary = chips.quality_gate(
        [row], [anchor("a1")], [manifest], tmp_path / "qa"
    )
    assert summary["release_eligible_count"] == 1
    qa = read_csv(tmp_path / "qa" / "chip_qa.csv")[0]
    assert qa["hash_status"] == "pass"
    assert qa["decode_status"] == "pass"
    assert qa["bbox_status"] == "pass"
    assert qa["pixel_quality_status"] == "pass"


def test_chip_qa_distinguishes_pixel_and_bbox_failures(tmp_path: Path) -> None:
    rows = [
        candidate("a1", "2020-01-01"),
        candidate("a2", "2020-02-01"),
    ]
    flat = tmp_path / "flat.tif"
    clipped = tmp_path / "clipped.tif"
    write_tif(flat, flat=True)
    write_tif(clipped, clipped=True)
    mrows = [manifest_row(rows[0], flat), manifest_row(rows[1], clipped)]
    manifest = tmp_path / "manifest.csv"
    chips.write_csv_rows(manifest, mrows, list(mrows[0].keys()))
    summary = chips.quality_gate(
        rows, [anchor("a1"), anchor("a2")], [manifest], tmp_path / "qa"
    )
    assert summary["failure_count"] == 2
    qa = read_csv(tmp_path / "qa" / "chip_qa.csv")
    assert qa[0]["pixel_quality_status"] == "fail"
    assert "pixel_uninformative" in qa[0]["failure_class"]
    assert qa[1]["bbox_status"] == "fail"
    assert "bbox_incomplete" in qa[1]["failure_class"]


def test_chip_qa_missing_manifest_remains_operational_failure(tmp_path: Path) -> None:
    manifest = tmp_path / "empty.csv"
    chips.write_csv_rows(manifest, [], ["anchor_id", "capture_date", "version", "provider"])
    summary = chips.quality_gate(
        [candidate("a1", "2020-01-01")],
        [anchor("a1")],
        [manifest],
        tmp_path / "qa",
    )
    assert summary["failure_classes"] == {"missing_manifest": 1}
    qa = read_csv(tmp_path / "qa" / "chip_qa.csv")[0]
    assert qa["release_eligible"] == "0"
