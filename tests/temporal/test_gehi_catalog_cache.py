"""Tests for scripts/temporal/gehi_catalog_cache.py (ISSUE-13).

All offline: sqlite3 against tmp_path, fake `live_fn` callables. Never invokes
the real GEHI binary.
"""

from __future__ import annotations

import subprocess
import threading
import time

import pytest

from scripts.temporal.gehi_catalog_cache import (
    CacheStats,
    CatalogCache,
    CatalogCacheKey,
    get_or_fetch,
)


def _avail_key(**overrides) -> CatalogCacheKey:
    base = dict(
        kind="availability",
        provider="TM",
        zoom=19,
        lower_left="-26.21,27.99",
        upper_right="-26.19,28.01",
        min_date="2009/01/01",
        max_date="2025/12/31",
        complete=True,
    )
    base.update(overrides)
    return CatalogCacheKey(**base)


def _info_key(**overrides) -> CatalogCacheKey:
    base = dict(kind="info", provider="TM", zoom=19, lower_left="-26.2041,28.0473")
    base.update(overrides)
    return CatalogCacheKey(**base)


def _cache(tmp_path, **kwargs) -> CatalogCache:
    return CatalogCache(tmp_path / "cache.sqlite3", **kwargs)


# ---------------------------------------------------------------------------
# CatalogCache: put/get, exact-key isolation


def test_put_then_get_any_roundtrips_payload(tmp_path):
    cache = _cache(tmp_path)
    key = _avail_key()
    cache.put(key, payload=["2019/01/01", "2020/06/15"], stdout_sha256="abc", gehi_command="cmd",
              anchor_id="a1", region_key="jhb", grid_id="G1")
    entry = cache.get_any(key)
    assert entry is not None
    assert entry.payload == ["2019/01/01", "2020/06/15"]
    assert entry.stdout_sha256 == "abc"
    assert entry.anchor_id == "a1"


def test_get_any_miss_returns_none(tmp_path):
    cache = _cache(tmp_path)
    assert cache.get_any(_avail_key()) is None


def test_key_isolation_by_date_range(tmp_path):
    """A narrower date-range query must NOT be served from a wider-range entry."""
    cache = _cache(tmp_path)
    wide = _avail_key(min_date="2009/01/01", max_date="2025/12/31")
    narrow = _avail_key(min_date="2020/01/01", max_date="2021/01/01")
    cache.put(wide, payload=["2019/01/01"], stdout_sha256="x", gehi_command="c")
    assert cache.get_any(narrow) is None
    assert cache.get_any(wide) is not None


def test_key_isolation_by_complete_flag(tmp_path):
    cache = _cache(tmp_path)
    complete_key = _avail_key(complete=True)
    partial_key = _avail_key(complete=False)
    cache.put(complete_key, payload=["2019/01/01"], stdout_sha256="x", gehi_command="c")
    assert cache.get_any(partial_key) is None
    assert cache.get_any(complete_key) is not None


def test_key_isolation_by_kind(tmp_path):
    """availability and info never collide even with overlapping string fields."""
    cache = _cache(tmp_path)
    avail = CatalogCacheKey(kind="availability", provider="TM", zoom=19, lower_left="same")
    info = CatalogCacheKey(kind="info", provider="TM", zoom=19, lower_left="same")
    cache.put(avail, payload=["avail-payload"], stdout_sha256="x", gehi_command="c")
    assert cache.get_any(info) is None
    assert cache.get_any(avail).payload == ["avail-payload"]


def test_key_isolation_by_zoom_and_provider(tmp_path):
    cache = _cache(tmp_path)
    z19 = _avail_key(zoom=19)
    z20 = _avail_key(zoom=20)
    wayback = _avail_key(provider="Wayback")
    cache.put(z19, payload=["z19"], stdout_sha256="x", gehi_command="c")
    assert cache.get_any(z20) is None
    assert cache.get_any(wayback) is None
    assert cache.get_any(z19).payload == ["z19"]


def test_put_upserts_existing_key(tmp_path):
    cache = _cache(tmp_path)
    key = _avail_key()
    cache.put(key, payload=["old"], stdout_sha256="x", gehi_command="c")
    cache.put(key, payload=["new"], stdout_sha256="y", gehi_command="c2")
    entry = cache.get_any(key)
    assert entry.payload == ["new"]
    assert entry.stdout_sha256 == "y"


# ---------------------------------------------------------------------------
# TTL staleness


def test_get_fresh_within_ttl(tmp_path):
    cache = _cache(tmp_path)
    key = _avail_key()
    cache.put(key, payload=["d"], stdout_sha256="x", gehi_command="c")
    assert cache.get_fresh(key, max_age_days=30.0) is not None


def test_get_fresh_beyond_ttl_returns_none(tmp_path):
    import datetime as dt

    cache = _cache(tmp_path)
    key = _avail_key()
    old_ts = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=40)).isoformat()
    cache.put(key, payload=["d"], stdout_sha256="x", gehi_command="c", fetched_at=old_ts)
    assert cache.get_fresh(key, max_age_days=30.0) is None
    # but get_any still finds it (needed for stale degrade)
    assert cache.get_any(key) is not None


# ---------------------------------------------------------------------------
# get_or_fetch: fresh-hit short-circuit, miss->live, force-refresh, stale degrade


def test_get_or_fetch_fresh_hit_skips_live_call(tmp_path):
    cache = _cache(tmp_path)
    key = _avail_key()
    cache.put(key, payload=["2019/01/01"], stdout_sha256="x", gehi_command="c")

    def _boom():
        raise AssertionError("live_fn must not be called on a fresh hit")

    result = get_or_fetch(cache, key, live_fn=_boom, anchor_id="a1")
    assert result == ["2019/01/01"]
    assert cache.stats.hits == 1
    assert cache.stats.live_calls == 0


def test_get_or_fetch_miss_calls_live_and_caches(tmp_path):
    cache = _cache(tmp_path)
    key = _avail_key()
    calls = []

    def _live():
        calls.append(1)
        return ["2020/01/01"], "sha", "cmd"

    result = get_or_fetch(cache, key, live_fn=_live, anchor_id="a1")
    assert result == ["2020/01/01"]
    assert len(calls) == 1
    assert cache.stats.misses == 1
    assert cache.stats.live_calls == 1
    # second call is now a fresh hit
    result2 = get_or_fetch(cache, key, live_fn=_live, anchor_id="a1")
    assert result2 == ["2020/01/01"]
    assert len(calls) == 1
    assert cache.stats.hits == 1


def test_get_or_fetch_force_refresh_bypasses_fresh_entry(tmp_path):
    cache = _cache(tmp_path)
    key = _avail_key()
    cache.put(key, payload=["old"], stdout_sha256="x", gehi_command="c")
    calls = []

    def _live():
        calls.append(1)
        return ["new"], "sha2", "cmd2"

    result = get_or_fetch(cache, key, live_fn=_live, force_refresh=True, anchor_id="a1")
    assert result == ["new"]
    assert len(calls) == 1
    assert cache.get_any(key).payload == ["new"]


def test_get_or_fetch_stale_served_on_live_failure(tmp_path):
    cache = _cache(tmp_path)
    key = _avail_key()
    cache.put(key, payload=["stale-data"], stdout_sha256="x", gehi_command="c",
              fetched_at="2000-01-01T00:00:00+00:00")  # ancient -> stale

    def _live_timeout():
        raise subprocess.TimeoutExpired(cmd="gehi", timeout=300.0)

    result = get_or_fetch(cache, key, live_fn=_live_timeout, max_age_days=1.0, anchor_id="a1")
    assert result == ["stale-data"]
    assert cache.stats.stale_served == 1
    assert cache.stats.refresh_failures == 0


def test_get_or_fetch_stale_served_on_runtime_error(tmp_path):
    """The RuntimeError path (GEHI non-zero exit via assert_gehi_success) must also degrade."""
    cache = _cache(tmp_path)
    key = _avail_key()
    cache.put(key, payload=["stale-data"], stdout_sha256="x", gehi_command="c",
              fetched_at="2000-01-01T00:00:00+00:00")

    def _live_runtime_error():
        raise RuntimeError("GEHistoricalImagery failed with return code 1: boom")

    result = get_or_fetch(cache, key, live_fn=_live_runtime_error, max_age_days=1.0, anchor_id="a1")
    assert result == ["stale-data"]
    assert cache.stats.stale_served == 1


def test_get_or_fetch_no_stale_entry_reraises(tmp_path):
    cache = _cache(tmp_path)
    key = _avail_key()

    def _live_runtime_error():
        raise RuntimeError("GEHistoricalImagery failed with return code 1: boom")

    with pytest.raises(RuntimeError):
        get_or_fetch(cache, key, live_fn=_live_runtime_error, anchor_id="a1")
    assert cache.stats.refresh_failures == 1
    assert cache.stats.stale_served == 0


def test_get_or_fetch_no_stale_entry_timeout_reraises(tmp_path):
    cache = _cache(tmp_path)
    key = _avail_key()

    def _live_timeout():
        raise subprocess.TimeoutExpired(cmd="gehi", timeout=300.0)

    with pytest.raises(subprocess.TimeoutExpired):
        get_or_fetch(cache, key, live_fn=_live_timeout, anchor_id="a1")
    assert cache.stats.refresh_failures == 1


# ---------------------------------------------------------------------------
# CacheStats accounting + summary


def test_cache_stats_summary_reports_all_counters():
    stats = CacheStats(hits=3, misses=2, live_calls=2, stale_served=1, refresh_failures=1)
    text = stats.summary()
    assert "hits=3" in text
    assert "misses=2" in text
    assert "live_calls=2" in text
    assert "stale_served=1" in text
    assert "refresh_failures=1" in text


def test_cache_stats_snapshot_is_consistent_dict():
    stats = CacheStats()
    stats.record_hit()
    stats.record_miss()
    snap = stats.snapshot()
    assert snap == {"hits": 1, "misses": 1, "live_calls": 0, "stale_served": 0, "refresh_failures": 0}


# ---------------------------------------------------------------------------
# Concurrency smoke: 2 threads hammering put/get on the same cache


def test_concurrent_put_get_smoke(tmp_path):
    cache = _cache(tmp_path)
    errors: list[Exception] = []

    def worker(n: int) -> None:
        try:
            for i in range(25):
                key = _avail_key(zoom=19 + (n % 2))
                cache.put(key, payload=[f"{n}-{i}"], stdout_sha256=f"s{n}{i}", gehi_command="c")
                entry = cache.get_any(key)
                assert entry is not None
        except Exception as exc:  # noqa: BLE001 - captured for the assertion below
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    assert not errors, errors
    assert all(not t.is_alive() for t in threads)


def test_catalog_cache_dir_env_default(monkeypatch, tmp_path):
    from scripts.temporal import gehi_catalog_cache as mod

    monkeypatch.setenv(mod.CACHE_DIR_ENV, str(tmp_path / "custom_cache"))
    assert mod.default_cache_dir() == tmp_path / "custom_cache"
    monkeypatch.delenv(mod.CACHE_DIR_ENV)
    assert mod.default_cache_dir() == mod.DEFAULT_CACHE_DIR
