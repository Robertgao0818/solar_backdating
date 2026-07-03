#!/usr/bin/env python3
"""Tests for gehi_availability resilience."""

from __future__ import annotations

import subprocess

from scripts.temporal.gehi_availability import fetch_availability_for_anchor
from scripts.temporal.gehi_catalog_cache import CatalogCache
from scripts.temporal.gehi_common import GehiRunResult


def _anchor():
    return {
        "anchor_id": "a000001",
        "region_key": "jhb",
        "grid_id": "G0816",
        "centroid_lat": -26.2,
        "centroid_lon": 28.0,
        "chip_lat_min": -26.21,
        "chip_lat_max": -26.19,
        "chip_lon_min": 27.99,
        "chip_lon_max": 28.01,
    }


def test_availability_timeout_does_not_propagate(monkeypatch):
    def _boom(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="gehi", timeout=1.0)

    rows = fetch_availability_for_anchor(_anchor(), runner=_boom)
    # Degraded gracefully: no exception, empty availability for the hung anchor.
    assert rows == []


def test_availability_broad_exception_does_not_propagate(monkeypatch):
    def _boom(*args, **kwargs):
        raise RuntimeError("gehi exploded")

    rows = fetch_availability_for_anchor(_anchor(), runner=_boom)
    assert rows == []


def _fake_result(stdout: str = "[0] 2019/06/15\n") -> GehiRunResult:
    return GehiRunResult(args=("gehi", "availability"), returncode=0, stdout=stdout, stderr="")


def test_catalog_cache_none_is_byte_identical_default(tmp_path):
    """Sanity: catalog_cache=None (the implicit default) still hits the runner every call."""
    calls = []

    def _runner(*args, **kwargs):
        calls.append(1)
        return _fake_result()

    rows1 = fetch_availability_for_anchor(_anchor(), runner=_runner)
    rows2 = fetch_availability_for_anchor(_anchor(), runner=_runner)
    assert len(calls) == 2
    assert rows1 and rows2
    assert rows1[0]["capture_date"] == "2019-06-15"


def test_catalog_cache_fresh_hit_skips_second_live_call(tmp_path):
    cache = CatalogCache(tmp_path / "cache.sqlite3")
    calls = []

    def _runner(*args, **kwargs):
        calls.append(1)
        return _fake_result()

    rows1 = fetch_availability_for_anchor(_anchor(), runner=_runner, catalog_cache=cache)
    rows2 = fetch_availability_for_anchor(_anchor(), runner=_runner, catalog_cache=cache)
    assert len(calls) == 1
    assert rows1 == rows2
    assert cache.stats.hits == 1
    assert cache.stats.live_calls == 1


def test_catalog_cache_force_refresh_reissues_live_call(tmp_path):
    cache = CatalogCache(tmp_path / "cache.sqlite3")
    calls = []

    def _runner(*args, **kwargs):
        calls.append(1)
        return _fake_result()

    fetch_availability_for_anchor(_anchor(), runner=_runner, catalog_cache=cache)
    fetch_availability_for_anchor(_anchor(), runner=_runner, catalog_cache=cache, force_refresh=True)
    assert len(calls) == 2
    assert cache.stats.live_calls == 2


def test_catalog_cache_timeout_with_no_stale_entry_degrades_to_empty(tmp_path):
    cache = CatalogCache(tmp_path / "cache.sqlite3")

    def _boom(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="gehi", timeout=1.0)

    rows = fetch_availability_for_anchor(_anchor(), runner=_boom, catalog_cache=cache)
    assert rows == []
    assert cache.stats.refresh_failures == 1


def test_catalog_cache_serves_stale_on_live_failure(tmp_path):
    cache = CatalogCache(tmp_path / "cache.sqlite3")
    calls = {"n": 0}

    def _runner(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return _fake_result("[0] 2019/06/15\n")
        raise subprocess.TimeoutExpired(cmd="gehi", timeout=1.0)

    rows1 = fetch_availability_for_anchor(_anchor(), runner=_runner, catalog_cache=cache, max_age_days=0.0)
    rows2 = fetch_availability_for_anchor(_anchor(), runner=_runner, catalog_cache=cache, max_age_days=0.0)
    assert rows1 and rows2
    assert rows1[0]["capture_date"] == rows2[0]["capture_date"] == "2019-06-15"
    assert cache.stats.stale_served == 1


def test_catalog_cache_runtime_error_with_no_stale_entry_reraises(tmp_path):
    """assert_gehi_success's RuntimeError path must still propagate with no fallback."""
    cache = CatalogCache(tmp_path / "cache.sqlite3")

    def _runner(*args, **kwargs):
        return GehiRunResult(args=("gehi",), returncode=1, stdout="", stderr="boom")

    import pytest

    with pytest.raises(RuntimeError):
        fetch_availability_for_anchor(_anchor(), runner=_runner, catalog_cache=cache)
