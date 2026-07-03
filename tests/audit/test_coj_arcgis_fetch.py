"""Tests for `scripts.audit.coj_arcgis_fetch` (ISSUE-08).

All network is injected (`http_get` / `sleep_fn` / `rng` callables) — no real
requests are made. Covers: URL construction, WGS84->3857 bbox reprojection,
retry/backoff on fake failures, the <5000-byte empty heuristic, and
text/html WAF-challenge detection.
"""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from scripts.audit.coj_arcgis_fetch import (
    COJ_LAYERS,
    FetchOutcome,
    backoff_seconds,
    build_export_url,
    fetch_chip,
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


def test_coj_layers_registered_for_both_years():
    assert set(COJ_LAYERS) == {2019, 2023}
    assert COJ_LAYERS[2019].endswith("/2019/ImageServer")
    assert COJ_LAYERS[2023].endswith("/2023/ImageServer")


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


def test_fetch_outcome_is_jsonl_serializable():
    def http_get(url):
        return _FakeResponse(200, b"x" * 20000, {"Content-Type": "image/tiff"})

    _, outcome = fetch_chip("http://example/export", http_get=http_get, sleep_fn=lambda s: None)
    d = outcome.to_dict()
    assert d["outcome"] == "ok"
    assert isinstance(d["latency_s"], float)
    import json

    json.dumps(d)  # must not raise
