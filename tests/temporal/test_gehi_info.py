"""Tests for scripts/temporal/gehi_info.py resilience + CSV field contract."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.temporal import gehi_info
from scripts.temporal.gehi_catalog_cache import CatalogCache
from scripts.temporal.gehi_common import GehiRunResult


def _anchor() -> dict[str, object]:
    return {
        "anchor_id": "a000001",
        "region_key": "jhb",
        "grid_id": "G0816",
        "centroid_lat": -26.2041,
        "centroid_lon": 28.0473,
    }


def test_fetch_vintages_swallows_timeout(capsys):
    """A runner raising TimeoutExpired must not propagate; degrade to []."""

    def _timeout_runner(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="GEHistoricalImagery info", timeout=300.0)

    result = gehi_info.fetch_vintages_for_anchor(
        _anchor(),
        runner=_timeout_runner,
        timeout=300.0,
    )

    assert result == []
    captured = capsys.readouterr()
    assert "a000001" in captured.err
    assert "timed out" in captured.err.lower()


def test_fetch_vintages_swallows_broad_exception(capsys):
    """Any runner exception (mirroring download resilience) degrades to []."""

    def _boom_runner(*args, **kwargs):
        raise RuntimeError("subprocess pipe died")

    result = gehi_info.fetch_vintages_for_anchor(
        _anchor(),
        runner=_boom_runner,
    )

    assert result == []
    captured = capsys.readouterr()
    assert "a000001" in captured.err


def test_fields_contains_dedupe_metadata():
    """Contract guard: dedupe metadata must survive into the CSV FIELDS."""
    assert "all_versions" in gehi_info.FIELDS
    assert "n_versions_at_date" in gehi_info.FIELDS


_INFO_STDOUT = "Level = 19, Path = 021\ndate = 2019/06/15, version = 296\n"


def _fake_info_result() -> GehiRunResult:
    return GehiRunResult(args=("gehi", "info"), returncode=0, stdout=_INFO_STDOUT, stderr="")


def test_catalog_cache_none_hits_runner_every_call():
    calls = []

    def _runner(*args, **kwargs):
        calls.append(1)
        return _fake_info_result()

    rows1 = gehi_info.fetch_vintages_for_anchor(_anchor(), runner=_runner)
    rows2 = gehi_info.fetch_vintages_for_anchor(_anchor(), runner=_runner)
    assert len(calls) == 2
    assert rows1 and rows2
    assert rows1[0]["capture_date"] == "2019-06-15"
    assert rows1[0]["anchor_id"] == "a000001"


def test_catalog_cache_fresh_hit_skips_second_live_call(tmp_path):
    cache = CatalogCache(tmp_path / "cache.sqlite3")
    calls = []

    def _runner(*args, **kwargs):
        calls.append(1)
        return _fake_info_result()

    rows1 = gehi_info.fetch_vintages_for_anchor(_anchor(), runner=_runner, catalog_cache=cache)
    rows2 = gehi_info.fetch_vintages_for_anchor(_anchor(), runner=_runner, catalog_cache=cache)
    assert len(calls) == 1
    assert rows1 == rows2
    assert cache.stats.hits == 1
    assert cache.stats.live_calls == 1


def test_catalog_cache_restamps_anchor_metadata_on_hit(tmp_path):
    """A cache hit reflects the CURRENT querying anchor, not the one that populated it."""
    cache = CatalogCache(tmp_path / "cache.sqlite3")

    def _runner(*args, **kwargs):
        return _fake_info_result()

    anchor_a = _anchor()
    anchor_b = dict(_anchor())
    anchor_b["anchor_id"] = "a999999"
    anchor_b["grid_id"] = "G9999"

    gehi_info.fetch_vintages_for_anchor(anchor_a, runner=_runner, catalog_cache=cache)
    rows_b = gehi_info.fetch_vintages_for_anchor(anchor_b, runner=_runner, catalog_cache=cache)
    assert rows_b[0]["anchor_id"] == "a999999"
    assert rows_b[0]["grid_id"] == "G9999"
    assert cache.stats.hits == 1


def test_catalog_cache_serves_stale_on_live_failure(tmp_path):
    cache = CatalogCache(tmp_path / "cache.sqlite3")
    calls = {"n": 0}

    def _runner(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return _fake_info_result()
        raise subprocess.TimeoutExpired(cmd="gehi", timeout=1.0)

    rows1 = gehi_info.fetch_vintages_for_anchor(_anchor(), runner=_runner, catalog_cache=cache, max_age_days=0.0)
    rows2 = gehi_info.fetch_vintages_for_anchor(_anchor(), runner=_runner, catalog_cache=cache, max_age_days=0.0)
    assert rows1 and rows2
    assert rows1[0]["capture_date"] == rows2[0]["capture_date"]
    assert cache.stats.stale_served == 1


def test_catalog_cache_timeout_with_no_stale_entry_degrades_to_empty(tmp_path):
    cache = CatalogCache(tmp_path / "cache.sqlite3")

    def _boom(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="gehi", timeout=1.0)

    rows = gehi_info.fetch_vintages_for_anchor(_anchor(), runner=_boom, catalog_cache=cache)
    assert rows == []
    assert cache.stats.refresh_failures == 1
