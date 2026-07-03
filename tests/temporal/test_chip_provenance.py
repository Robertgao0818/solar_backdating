"""ISSUE-18 part A — chip provenance record + orchestrator escape-hatch plumbing.

Covers:
- ``build_chip_provenance`` measures raster geometry/GSD/extent/CRS/hash from a
  real tiny GeoTIFF (incl. the cos-lat deg->m GSD conversion), tolerates a
  non-raster file (``raster_error`` set, no raise), and produces provenance for
  ``status="skipped_existing"`` cache hits (the whole point: cache hits are where
  a pinned low zoom hides).
- both production orchestrators forward ``--overwrite-chips`` / ``--min-cache-zoom``
  down to ``download_chip_with_zoom_ladder`` (and, when the flags are off, add no
  extra kwargs — byte-identical to today's call).
- ``run_adaptive_scan.summarize`` prints an achieved-zoom histogram.

Everything runs offline (injected fake runners / monkeypatched download).
"""

from __future__ import annotations

import math
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from scripts.temporal import gehi_download as gd
from scripts.temporal import run_adaptive_scan as ras
from scripts.temporal import run_census2023_scan as rc
from scripts.temporal.gehi_download import DownloadResult, build_chip_provenance
from scripts.temporal.scan_config import AdaptiveScanConfig
from scripts.temporal.scan_state import Pick, Round, RoundResult, ScanState

# Known chip geometry: 8x8 px, EPSG:4326, 0.0001 deg/pixel near Johannesburg.
_WEST = 28.0
_NORTH = -25.9992
_PIXEL_DEG = 0.0001
_SIZE = 8
_CENTER_LAT = (_NORTH + (_NORTH - _SIZE * _PIXEL_DEG)) / 2.0  # -25.9996


def _write_tiny_geotiff(path: Path) -> None:
    transform = from_origin(_WEST, _NORTH, _PIXEL_DEG, _PIXEL_DEG)
    data = np.zeros((_SIZE, _SIZE), dtype="uint8")
    with rasterio.open(
        path, "w", driver="GTiff", height=_SIZE, width=_SIZE, count=1,
        dtype="uint8", crs="EPSG:4326", transform=transform,
    ) as ds:
        ds.write(data, 1)


def _result(path: Path | None, *, status: str, actual_zoom: int | None, sha256: str = "abc") -> DownloadResult:
    return DownloadResult(
        anchor_id="a1",
        capture_date="2024-06-15",
        version="12345",
        requested_zoom_ladder=(20, 19),
        actual_zoom=actual_zoom,
        path=path,
        sha256=sha256,
        status=status,
        error=None,
        gehi_command="",
        download_stdout_sha256="",
    )


# ---------------------------------------------------------------------------
# build_chip_provenance
# ---------------------------------------------------------------------------


def test_build_chip_provenance_measures_real_geotiff(tmp_path: Path) -> None:
    tif = tmp_path / "chip.tif"
    _write_tiny_geotiff(tif)
    prov = build_chip_provenance(_result(tif, status="ok", actual_zoom=20), {"anchor_id": "a1"}, "Wayback")

    assert prov["anchor_id"] == "a1"
    assert prov["capture_date"] == "2024-06-15"
    assert prov["version"] == "12345"
    assert prov["provider"] == "Wayback"
    assert prov["requested_zoom_ladder"] == [20, 19]
    assert prov["achieved_zoom"] == 20
    assert prov["status"] == "ok"
    assert prov["chip_path"] == str(tif)
    assert prov["chip_sha256"] == "abc"

    assert prov["raster_width_px"] == 8
    assert prov["raster_height_px"] == 8
    assert prov["raster_crs"] == "EPSG:4326"
    assert prov["raster_error"] is None

    assert prov["extent_minx"] == pytest.approx(_WEST)
    assert prov["extent_maxx"] == pytest.approx(_WEST + _SIZE * _PIXEL_DEG)
    assert prov["extent_maxy"] == pytest.approx(_NORTH)
    assert prov["extent_miny"] == pytest.approx(_NORTH - _SIZE * _PIXEL_DEG)


def test_build_chip_provenance_cos_lat_gsd(tmp_path: Path) -> None:
    tif = tmp_path / "chip.tif"
    _write_tiny_geotiff(tif)
    prov = build_chip_provenance(_result(tif, status="ok", actual_zoom=20), {}, "TM")

    expected_x = _PIXEL_DEG * 111320.0 * math.cos(math.radians(_CENTER_LAT))
    expected_y = _PIXEL_DEG * 111320.0
    assert prov["gsd_x_m"] == pytest.approx(expected_x, rel=1e-3)
    assert prov["gsd_y_m"] == pytest.approx(expected_y, rel=1e-3)
    # x is shrunk by cos(lat) relative to y at this latitude.
    assert prov["gsd_x_m"] < prov["gsd_y_m"]


def test_build_chip_provenance_non_raster_sets_error(tmp_path: Path) -> None:
    bad = tmp_path / "bad.tif"
    bad.write_bytes(b"this is not a GeoTIFF")
    prov = build_chip_provenance(_result(bad, status="ok", actual_zoom=19), {}, "TM")

    assert prov["raster_error"] is not None
    assert isinstance(prov["raster_error"], str)
    for key in (
        "raster_width_px", "raster_height_px", "raster_crs",
        "extent_minx", "extent_miny", "extent_maxx", "extent_maxy",
        "gsd_x_m", "gsd_y_m",
    ):
        assert prov[key] is None
    # non-raster fields still populated
    assert prov["status"] == "ok"
    assert prov["achieved_zoom"] == 19


def test_build_chip_provenance_for_skipped_existing(tmp_path: Path) -> None:
    """Cache hits (status='skipped_existing') get full raster provenance too."""
    tif = tmp_path / "chip.tif"
    _write_tiny_geotiff(tif)
    prov = build_chip_provenance(_result(tif, status="skipped_existing", actual_zoom=19), {}, "TM")

    assert prov["status"] == "skipped_existing"
    assert prov["achieved_zoom"] == 19
    assert prov["raster_width_px"] == 8
    assert prov["raster_error"] is None
    assert prov["gsd_x_m"] is not None


def test_build_chip_provenance_no_path(tmp_path: Path) -> None:
    prov = build_chip_provenance(
        _result(None, status="all_zooms_failed", actual_zoom=None, sha256=""),
        {}, "TM",
    )
    assert prov["chip_path"] is None
    assert prov["chip_sha256"] is None
    assert prov["raster_error"] is not None
    assert prov["raster_width_px"] is None


# ---------------------------------------------------------------------------
# Orchestrator flag plumbing: --overwrite-chips / --min-cache-zoom
# ---------------------------------------------------------------------------


class _TrapScorer:
    """A structurally-valid scorer whose scoring path must never be reached in
    these plumbing tests (all downloads fail, so no scoring happens)."""

    name = "trap"
    failure_decision_sources = frozenset({"trap_failed"})
    quality_flags = frozenset({"usable", "unusable"})
    decision_sources = frozenset({"trap_failed"})

    def batch(self, *_a: Any, **_k: Any):  # pragma: no cover - not reached
        raise AssertionError("scorer must not be called when all downloads fail")

    def score(self, *_a: Any, **_k: Any):  # pragma: no cover
        raise NotImplementedError

    def sequence(self, *_a: Any, **_k: Any):  # pragma: no cover
        raise AssertionError("scorer.sequence must not be called")


def _capturing_failed_download(captured: dict[str, Any]):
    def fake_download(anchor, **kwargs):
        captured.clear()
        captured.update(kwargs)
        return DownloadResult(
            anchor_id=str(anchor["anchor_id"]),
            capture_date=str(kwargs["capture_date"]),
            version=str(kwargs["version"]),
            requested_zoom_ladder=tuple(kwargs["zoom_ladder"]),
            actual_zoom=None,
            path=None,
            sha256="",
            status="all_zooms_failed",
            error="stub",
            gehi_command="",
            download_stdout_sha256="",
        )

    return fake_download


def test_adaptive_forwards_escape_hatch_kwargs(tmp_path: Path, monkeypatch) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(gd, "download_chip_with_zoom_ladder", _capturing_failed_download(captured))

    rnd = Round(
        round_id=1, round_type="initial", window_start_date=None, window_end_date=None,
        picks=[Pick(chip_index=1, capture_date="2020-01-01", version=100, requested_zoom=20)],
    )
    ras.execute_round_real(
        rnd,
        {"anchor_id": "A1", "region_key": "johannesburg"},
        AdaptiveScanConfig(),
        chips_dir=tmp_path / "chips",
        audit_dir=tmp_path / "audit",
        gemini_config=object(),
        scorer=_TrapScorer(),
        overwrite_chips=True,
        min_cache_zoom=20,
    )
    assert captured.get("overwrite") is True
    assert captured.get("min_cache_zoom") == 20


def test_adaptive_no_flags_adds_no_extra_kwargs(tmp_path: Path, monkeypatch) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(gd, "download_chip_with_zoom_ladder", _capturing_failed_download(captured))

    rnd = Round(
        round_id=1, round_type="initial", window_start_date=None, window_end_date=None,
        picks=[Pick(chip_index=1, capture_date="2020-01-01", version=100, requested_zoom=20)],
    )
    ras.execute_round_real(
        rnd,
        {"anchor_id": "A1", "region_key": "johannesburg"},
        AdaptiveScanConfig(),
        chips_dir=tmp_path / "chips",
        audit_dir=tmp_path / "audit",
        gemini_config=object(),
        scorer=_TrapScorer(),
    )
    assert "overwrite" not in captured
    assert "min_cache_zoom" not in captured


def _census_cohort_row() -> dict[str, str]:
    return {
        "anchor_id": "census_anchor_plumb",
        "id_kind": "c",
        "grid_id": "JNB0202",
        "region_key": "johannesburg",
        "latest_absent_date": "2020-06-01",
        "install_interval_end": "2023-12-01",
        "wb_2023_dates": "2023-03-15",
        "centroid_lon": "28.0",
        "centroid_lat": "-26.0",
        "chip_lon_min": "27.99",
        "chip_lat_min": "-26.01",
        "chip_lon_max": "28.01",
        "chip_lat_max": "-25.99",
    }


def _census_state() -> ScanState:
    results = [
        RoundResult(
            chip_index=1, capture_date="2019-05-01", version=1, pv_present=False,
            confidence=0.9, quality_flag="usable", decision_source="gemini_batch",
            chip_path="/tmp/absent.tif", actual_zoom=20,
        ),
        RoundResult(
            chip_index=2, capture_date="2024-06-01", version=1, pv_present=True,
            confidence=0.9, quality_flag="usable", decision_source="gemini_batch",
            chip_path="/tmp/present.tif", actual_zoom=20,
        ),
    ]
    rnd = Round(
        round_id=1, round_type="initial", window_start_date=None, window_end_date=None,
        results=results, completed=True,
    )
    return ScanState(
        anchor_id="census_anchor_plumb", region_key="johannesburg", grid_id="JNB0202",
        status="done_appears", rounds=[rnd],
    )


def _run_census_plumb(tmp_path: Path, monkeypatch, captured: dict[str, Any], **kw) -> dict[str, object]:
    monkeypatch.setattr(rc, "load_scan_state", lambda _p: _census_state())
    monkeypatch.setattr(rc.Path, "exists", lambda self: True)

    def fake_download(anchor, **kwargs):
        captured.clear()
        captured.update(kwargs)
        return SimpleNamespace(status="all_zooms_failed", path=None, actual_zoom=None)

    monkeypatch.setattr(rc, "download_chip_with_zoom_ladder", fake_download)
    from scripts.validation.gemini_solar_image_review import GeminiClientConfig

    job = rc.CensusJob(_census_cohort_row())
    return rc.run_one_anchor(
        job,
        main_dir=tmp_path / "main",
        norecent_dir=tmp_path / "norecent",
        census_chips_dir=tmp_path / "chips",
        audit_dir=None,
        config=GeminiClientConfig(base_url="https://stub", api_key="stub"),
        zoom_ladder=(19, 18),
        max_tokens=8192,
        routing_salt_mode="none",
        limiter=rc.RateLimiter(0.0),
        scorer=object(),
        **kw,
    )


def test_census_forwards_escape_hatch_kwargs(tmp_path: Path, monkeypatch) -> None:
    captured: dict[str, Any] = {}
    out = _run_census_plumb(tmp_path, monkeypatch, captured, overwrite_chips=True, min_cache_zoom=19)
    # download reached but returned missing -> kept_no_usable_2023 (no scorer call)
    assert out["census_decision"] == "kept_no_usable_2023"
    assert captured.get("overwrite") is True
    assert captured.get("min_cache_zoom") == 19


def test_census_no_flags_adds_no_extra_kwargs(tmp_path: Path, monkeypatch) -> None:
    captured: dict[str, Any] = {}
    _run_census_plumb(tmp_path, monkeypatch, captured)
    assert "overwrite" not in captured
    assert "min_cache_zoom" not in captured


# ---------------------------------------------------------------------------
# summarize achieved-zoom histogram
# ---------------------------------------------------------------------------


def _state_with_zooms(zooms: list[int | None]) -> ScanState:
    results = [
        RoundResult(
            chip_index=i, capture_date="2020-01-01", version=1, pv_present=True,
            confidence=0.9, quality_flag="usable", decision_source="gemini_batch",
            actual_zoom=z,
        )
        for i, z in enumerate(zooms)
    ]
    rnd = Round(
        round_id=1, round_type="initial", window_start_date=None, window_end_date=None,
        results=results, completed=True,
    )
    return ScanState(
        anchor_id="a", region_key="johannesburg", grid_id="G1", status="scanning", rounds=[rnd],
    )


def test_summarize_prints_achieved_zoom_histogram(capsys) -> None:
    states = [_state_with_zooms([20, 20, 19, None])]
    ras.summarize(states)
    out = capsys.readouterr().out
    assert "z20: 2 (50.0%)" in out
    assert "z19: 1 (25.0%)" in out
    assert "unknown: 1 (25.0%)" in out
