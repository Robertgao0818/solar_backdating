"""Tests for `scripts.audit.coj_arcgis_fetch` (ISSUE-08 + ISSUE-09 cohort).

All network is injected (`http_get` / `sleep_fn` / `rng` / `fetch_one`
callables) — no real requests are made. Covers: URL construction,
WGS84->3857 bbox reprojection, retry/backoff on fake failures, the
<5000-byte empty heuristic, text/html WAF-challenge detection, and (ISSUE-09,
WP-B) the 2015 layer + the across-units `fetch_units_concurrent` driver.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from pathlib import Path

import pytest

from scripts.audit.coj_arcgis_fetch import (
    COJ_LAYERS,
    FetchOutcome,
    FetchUnit,
    backoff_seconds,
    build_export_url,
    fetch_and_save_chip,
    fetch_chip,
    fetch_units_concurrent,
    pixel_size_for_bbox,
    wgs84_bbox_to_3857,
)


# ---------------------------------------------------------------------------
# Reprojection + URL construction
# ---------------------------------------------------------------------------


def test_wgs84_bbox_to_3857_known_point():
    xmin, ymin, xmax, ymax = wgs84_bbox_to_3857(28.0436039521, -26.2882437334, 28.0445731622, -26.2873700506)
    assert xmin == pytest.approx(3121799.71, abs=1.0)
    assert ymin == pytest.approx(-3034825.19, abs=1.0)
    assert xmax == pytest.approx(3121907.60, abs=1.0)
    assert ymax == pytest.approx(-3034716.71, abs=1.0)
    assert xmax > xmin
    assert ymax > ymin


def test_pixel_size_for_bbox_uses_native_gsd():
    # 100m x 200m box at 0.15 m/px -> ~667 x ~1333, before any cap
    w, h = pixel_size_for_bbox((0.0, 0.0, 100.0, 200.0), native_gsd=0.15, min_px=1, max_px=100000)
    assert w == pytest.approx(667, abs=1)
    assert h == pytest.approx(1333, abs=1)


def test_pixel_size_for_bbox_respects_min_and_max_caps():
    w, h = pixel_size_for_bbox((0.0, 0.0, 1.0, 1.0), native_gsd=0.15, min_px=64, max_px=1024)
    assert w == 64 and h == 64
    w2, h2 = pixel_size_for_bbox((0.0, 0.0, 1000.0, 1000.0), native_gsd=0.15, min_px=64, max_px=1024)
    assert w2 == 1024 and h2 == 1024


def test_build_export_url_contains_required_params():
    base = COJ_LAYERS[2023]
    url = build_export_url(base, (1.0, 2.0, 3.0, 4.0), (400, 400))
    assert url.startswith(base + "/exportImage?")
    assert "bbox=1.0,2.0,3.0,4.0" in url
    assert "bboxSR=3857" in url
    assert "imageSR=3857" in url
    assert "size=400,400" in url
    assert "format=tiff" in url
    assert "f=image" in url
    assert "interpolation=RSP_BilinearInterpolation" in url


def test_coj_layers_registered_for_all_years():
    # ISSUE-09 (WP-B) adds 2015; 2019/2023 URLs are unchanged (pilot depends on them).
    assert set(COJ_LAYERS) == {2015, 2019, 2023}
    assert COJ_LAYERS[2015].endswith("/2015/ImageServer")
    assert COJ_LAYERS[2019].endswith("/2019/ImageServer")
    assert COJ_LAYERS[2023].endswith("/2023/ImageServer")


def test_coj_layers_2015_matches_2019_2023_url_pattern():
    # The 2015 URL must follow the identical AerialPhotography/<year>/ImageServer pattern.
    assert 2015 in COJ_LAYERS
    assert (
        COJ_LAYERS[2015]
        == "https://ags.joburg.org.za/server/rest/services/AerialPhotography/2015/ImageServer"
    )
    # same prefix + suffix shape as the two verified pilot layers
    prefix = "https://ags.joburg.org.za/server/rest/services/AerialPhotography/"
    for year in (2015, 2019, 2023):
        assert COJ_LAYERS[year] == f"{prefix}{year}/ImageServer"


# ---------------------------------------------------------------------------
# backoff
# ---------------------------------------------------------------------------


class _FixedRng:
    def uniform(self, a, b):
        return 0.0  # no jitter, deterministic


def test_backoff_seconds_doubles_each_attempt_without_jitter():
    rng = _FixedRng()
    assert backoff_seconds(1, base=1.0, jitter_frac=0.3, rng=rng) == pytest.approx(1.0)
    assert backoff_seconds(2, base=1.0, jitter_frac=0.3, rng=rng) == pytest.approx(2.0)
    assert backoff_seconds(3, base=1.0, jitter_frac=0.3, rng=rng) == pytest.approx(4.0)


def test_backoff_seconds_jitter_bounded():
    class _MaxRng:
        def uniform(self, a, b):
            return b  # max jitter

    rng = _MaxRng()
    val = backoff_seconds(1, base=1.0, jitter_frac=0.3, rng=rng)
    assert val == pytest.approx(1.3)


# ---------------------------------------------------------------------------
# fetch_chip retry / classification behaviour
# ---------------------------------------------------------------------------


@dataclass
class _FakeResponse:
    status_code: int
    content: bytes
    headers: dict


def _sleeper():
    calls = []

    def _sleep(seconds):
        calls.append(seconds)

    return _sleep, calls


def test_fetch_chip_success_first_try():
    def http_get(url):
        return _FakeResponse(200, b"x" * 20000, {"Content-Type": "image/tiff"})

    body, outcome = fetch_chip("http://example/export", http_get=http_get, sleep_fn=lambda s: None)
    assert body == b"x" * 20000
    assert outcome.outcome == "ok"
    assert outcome.attempts == 1
    assert outcome.http_status == 200


def test_fetch_chip_retries_on_5xx_then_succeeds():
    calls = {"n": 0}

    def http_get(url):
        calls["n"] += 1
        if calls["n"] < 3:
            return _FakeResponse(503, b"", {"Content-Type": "text/plain"})
        return _FakeResponse(200, b"x" * 20000, {"Content-Type": "image/tiff"})

    sleep_fn, sleeps = _sleeper()
    body, outcome = fetch_chip("http://example/export", http_get=http_get, sleep_fn=sleep_fn, max_attempts=3)
    assert body is not None
    assert outcome.outcome == "ok"
    assert outcome.attempts == 3
    assert len(sleeps) == 2  # slept between attempts 1->2 and 2->3


def test_fetch_chip_gives_up_after_max_retries_on_persistent_5xx():
    def http_get(url):
        return _FakeResponse(500, b"", {"Content-Type": "text/plain"})

    sleep_fn, sleeps = _sleeper()
    body, outcome = fetch_chip("http://example/export", http_get=http_get, sleep_fn=sleep_fn, max_attempts=3)
    assert body is None
    assert outcome.outcome == "http_error"
    assert outcome.attempts == 3
    assert outcome.http_status == 500
    assert len(sleeps) == 2


def test_fetch_chip_detects_waf_html_challenge_as_failure():
    def http_get(url):
        return _FakeResponse(200, b"<html>captcha challenge</html>" * 200, {"Content-Type": "text/html; charset=utf-8"})

    sleep_fn, sleeps = _sleeper()
    body, outcome = fetch_chip("http://example/export", http_get=http_get, sleep_fn=sleep_fn, max_attempts=2)
    assert body is None
    assert outcome.outcome == "waf_challenge"
    assert outcome.attempts == 2


def test_fetch_chip_empty_body_is_not_a_hard_failure_no_retry():
    def http_get(url):
        return _FakeResponse(200, b"\x00" * 10, {"Content-Type": "image/tiff"})

    sleep_fn, sleeps = _sleeper()
    body, outcome = fetch_chip("http://example/export", http_get=http_get, sleep_fn=sleep_fn, max_attempts=3)
    assert body is None
    assert outcome.outcome == "empty"
    assert outcome.attempts == 1
    assert len(sleeps) == 0  # no coverage is not retried


def test_fetch_chip_exception_is_retried_then_reported():
    def http_get(url):
        raise ConnectionError("boom")

    sleep_fn, sleeps = _sleeper()
    body, outcome = fetch_chip("http://example/export", http_get=http_get, sleep_fn=sleep_fn, max_attempts=3)
    assert body is None
    assert outcome.outcome == "exception"
    assert outcome.attempts == 3
    assert len(sleeps) == 2


def _make_fake_arcgis_tiff_bytes(width: int = 64, height: int = 64) -> bytes:
    """A smooth (highly JPEG-compressible), georeferenced 3-band uint8 TIFF,
    standing in for what ArcGIS exportImage's format=tiff actually returns."""
    import numpy as np
    import rasterio
    from rasterio.transform import from_bounds

    gradient = np.linspace(0, 255, width, dtype="uint8")
    data = np.tile(gradient, (3, height, 1))
    profile = {
        "driver": "GTiff", "height": height, "width": width,
        "count": 3, "dtype": "uint8", "crs": "EPSG:3857",
        "transform": from_bounds(0, 0, width * 0.15, height * 0.15, width, height),
    }
    with rasterio.MemoryFile() as mem:
        with mem.open(**profile) as dst:
            dst.write(data)
        return bytes(mem.read())


def test_fetch_and_save_chip_recompresses_to_jpeg_geotiff(tmp_path):
    raw = _make_fake_arcgis_tiff_bytes()

    def http_get(url):
        return _FakeResponse(200, raw, {"Content-Type": "image/tiff"})

    out_path = tmp_path / "chip.tif"
    outcome = fetch_and_save_chip(
        "a1", 2023, (0.0, 0.0, 0.01, 0.01), out_path,
        http_get=http_get, sleep_fn=lambda s: None,
    )

    assert outcome.outcome == "ok"
    assert out_path.exists()
    assert out_path.stat().st_size < len(raw)
    assert outcome.n_bytes == out_path.stat().st_size

    import rasterio

    with rasterio.open(out_path) as src:
        assert "jpeg" in str(src.profile.get("compress", "")).lower()
        assert src.crs is not None
        assert src.count == 3


def test_fetch_and_save_chip_skips_existing_non_empty_file(tmp_path):
    out_path = tmp_path / "chip.tif"
    out_path.write_bytes(b"already-here")

    def http_get(url):
        raise AssertionError("must not fetch when a non-empty chip already exists")

    outcome = fetch_and_save_chip(
        "a1", 2023, (0.0, 0.0, 0.01, 0.01), out_path,
        http_get=http_get, sleep_fn=lambda s: None,
    )
    assert outcome.outcome == "skipped_existing"


def test_fetch_outcome_is_jsonl_serializable():
    def http_get(url):
        return _FakeResponse(200, b"x" * 20000, {"Content-Type": "image/tiff"})

    _, outcome = fetch_chip("http://example/export", http_get=http_get, sleep_fn=lambda s: None)
    d = outcome.to_dict()
    assert d["outcome"] == "ok"
    assert isinstance(d["latency_s"], float)
    import json

    json.dumps(d)  # must not raise


# ---------------------------------------------------------------------------
# ISSUE-09 (WP-B) — FetchUnit + across-units concurrent driver
#
# Politeness invariant under test: concurrency is ACROSS units only. Each
# `fetch_one` call stays fully sequential internally (the pilot per-request
# sleep/backoff lives inside fetch_and_save_chip, NOT here), and the driver
# adds NO retry of its own — a unit's outcome is terminal. The driver's job is
# purely: submit each unit once to a thread pool, funnel every result through a
# single lock (so the shared fetch_stats.jsonl writer can't interleave), and
# roll up a {outcome: count} summary.
# ---------------------------------------------------------------------------


def _make_units(n: int, *, year: int = 2023) -> list[FetchUnit]:
    return [
        FetchUnit(
            anchor_id=f"a{i:04d}",
            year=year,
            bbox_4326=(0.0, 0.0, 1.0, 1.0),
            out_path=Path(f"/tmp/coj_fake/{year}/a{i:04d}.tif"),
        )
        for i in range(n)
    ]


def _delayed_ok(unit: FetchUnit) -> FetchOutcome:
    # Deterministic-per-index tiny delay: exercises worker interleaving without
    # touching the network or the clock in any nondeterministic way.
    idx = int(unit.anchor_id[1:])
    time.sleep((idx % 5) * 0.001)
    return FetchOutcome(layer=str(unit.year), outcome="ok", attempts=1)


def test_fetch_unit_is_frozen_dataclass():
    u = FetchUnit(
        anchor_id="a1", year=2015, bbox_4326=(1.0, 2.0, 3.0, 4.0), out_path=Path("/tmp/x.tif")
    )
    assert u.anchor_id == "a1"
    assert u.year == 2015
    assert u.bbox_4326 == (1.0, 2.0, 3.0, 4.0)
    assert u.out_path == Path("/tmp/x.tif")
    with pytest.raises(Exception):  # FrozenInstanceError — must be immutable
        u.anchor_id = "b"  # type: ignore[misc]


def test_fetch_units_concurrent_processes_each_unit_exactly_once():
    units = _make_units(50)
    recorded: list[tuple[str, str]] = []

    # on_result deliberately has NO lock of its own — the driver must serialize
    # it under a single lock, so this plain append is safe if the contract holds.
    def on_result(unit: FetchUnit, outcome: FetchOutcome) -> None:
        recorded.append((unit.anchor_id, outcome.outcome))

    summary = fetch_units_concurrent(
        units, fetch_one=_delayed_ok, on_result=on_result, pool_size=4
    )

    assert len(recorded) == 50
    # every unit seen exactly once, none lost or duplicated under pool_size>1
    assert sorted(a for a, _ in recorded) == sorted(u.anchor_id for u in units)
    assert summary == {"ok": 50}
    assert sum(summary.values()) == len(recorded)


def test_fetch_units_concurrent_exception_becomes_terminal_outcome():
    units = _make_units(50)
    recorded: dict[str, str] = {}

    def fetch_one(unit: FetchUnit) -> FetchOutcome:
        idx = int(unit.anchor_id[1:])
        if idx % 2 == 0:
            raise ConnectionError(f"boom {idx}")
        return FetchOutcome(layer=str(unit.year), outcome="ok", attempts=1)

    def on_result(unit: FetchUnit, outcome: FetchOutcome) -> None:
        recorded[unit.anchor_id] = outcome.outcome

    summary = fetch_units_concurrent(
        units, fetch_one=fetch_one, on_result=on_result, pool_size=4
    )

    # a raising fetch_one -> outcome 'exception' for THAT unit; pool not crashed,
    # every other unit still processed.
    assert len(recorded) == 50
    assert summary.get("exception") == 25
    assert summary.get("ok") == 25
    assert recorded["a0000"] == "exception"
    assert recorded["a0001"] == "ok"


def test_fetch_units_concurrent_does_not_retry_units():
    # Even a failing (non-ok) outcome is terminal: the driver must call fetch_one
    # exactly once per unit and never re-submit.
    units = _make_units(20)
    call_counts: dict[str, int] = {}
    lock = threading.Lock()

    def fetch_one(unit: FetchUnit) -> FetchOutcome:
        with lock:
            call_counts[unit.anchor_id] = call_counts.get(unit.anchor_id, 0) + 1
        return FetchOutcome(layer=str(unit.year), outcome="http_error", attempts=3)

    def on_result(unit: FetchUnit, outcome: FetchOutcome) -> None:
        pass

    summary = fetch_units_concurrent(
        units, fetch_one=fetch_one, on_result=on_result, pool_size=4
    )

    assert len(call_counts) == 20
    assert all(v == 1 for v in call_counts.values())
    assert summary == {"http_error": 20}


def test_fetch_units_concurrent_max_units_caps_submissions():
    units = _make_units(50)
    seen: list[str] = []

    def on_result(unit: FetchUnit, outcome: FetchOutcome) -> None:
        seen.append(unit.anchor_id)

    summary = fetch_units_concurrent(
        units, fetch_one=_delayed_ok, on_result=on_result, pool_size=4, max_units=10
    )

    assert len(seen) == 10
    assert summary == {"ok": 10}
    # only the first 10 units (submission order) are ever handed to fetch_one
    assert sorted(seen) == [f"a{i:04d}" for i in range(10)]


class _StopAfter:
    """should_stop() that returns False for the first ``n`` checks, then True."""

    def __init__(self, n: int) -> None:
        self.n = n
        self.calls = 0

    def __call__(self) -> bool:
        self.calls += 1
        return self.calls > self.n


def test_fetch_units_concurrent_should_stop_halts_further_submission():
    units = _make_units(50)
    seen: list[str] = []

    def on_result(unit: FetchUnit, outcome: FetchOutcome) -> None:
        seen.append(unit.anchor_id)

    stop = _StopAfter(10)  # checks 1..10 -> False (submit), check 11 -> True (break)
    summary = fetch_units_concurrent(
        units, fetch_one=_delayed_ok, on_result=on_result, pool_size=4, should_stop=stop
    )

    # cooperative stop: the 10 already-submitted units finish; nothing past the stop.
    assert len(seen) == 10
    assert summary == {"ok": 10}
    assert stop.calls == 11


def test_fetch_units_concurrent_should_stop_true_from_start_submits_nothing():
    units = _make_units(10)
    seen: list[str] = []

    def on_result(unit: FetchUnit, outcome: FetchOutcome) -> None:
        seen.append(unit.anchor_id)

    summary = fetch_units_concurrent(
        units, fetch_one=_delayed_ok, on_result=on_result, pool_size=4,
        should_stop=lambda: True,
    )

    assert seen == []
    assert summary == {}


def test_fetch_units_concurrent_empty_input_returns_empty_summary():
    calls = {"n": 0}

    def on_result(unit: FetchUnit, outcome: FetchOutcome) -> None:
        calls["n"] += 1

    summary = fetch_units_concurrent(
        [], fetch_one=_delayed_ok, on_result=on_result, pool_size=4
    )
    assert summary == {}
    assert calls["n"] == 0


def test_fetch_units_concurrent_stop_mid_run_takes_effect_within_window():
    # Regression: submission must be windowed (bounded in-flight), not
    # queue-everything-upfront — otherwise a stop signal raised while units are
    # actually fetching can never halt the run (at cohort scale, ~16k queued
    # units would drain for hours after Ctrl-C). A slow fetch_one + a stop
    # event flipped by the 3rd completion must leave most units unprocessed.
    pool_size = 4
    units = _make_units(200)
    stop = threading.Event()
    seen: list[str] = []

    def fetch_one(unit: FetchUnit) -> FetchOutcome:
        time.sleep(0.01)
        return FetchOutcome(layer=str(unit.year), outcome="ok", attempts=1)

    def on_result(unit: FetchUnit, outcome: FetchOutcome) -> None:
        seen.append(unit.anchor_id)
        if len(seen) >= 3:
            stop.set()

    summary = fetch_units_concurrent(
        units, fetch_one=fetch_one, on_result=on_result,
        pool_size=pool_size, should_stop=stop.is_set,
    )

    # after the stop flips, at most the bounded in-flight window (2*pool_size)
    # plus a submission-race margin may still complete — nowhere near all 200.
    assert 3 <= len(seen) <= 3 + 3 * pool_size
    assert sum(summary.values()) == len(seen)
