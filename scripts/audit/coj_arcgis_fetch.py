"""CoJ ArcGIS ImageServer fetcher for the true-date audit pilot (ISSUE-08).

Talks to the City of Johannesburg's public aerial-photography ImageServers
(2019 + 2023 flights, 0.15 m native, verified live in the ISSUE-08 scouting
pass). All network access is injected via an ``http_get`` callable so this
module — and its retry/backoff/classification logic — is fully unit-testable
offline (``tests/audit/test_coj_arcgis_fetch.py``).

Politeness / reliability posture (see the pilot doc for measured numbers):
sequential requests, small extra sleep between requests, retry x3 with
exponential backoff (1s/2s/4s) +/- 30% jitter on transient failures. A
response body under 5000 bytes is treated as "no coverage at this bbox" (a
soft, non-retried outcome) rather than a hard failure; an HTTP 200 with an
``text/html`` content-type is the WAF challenge-page signature and is treated
as a hard failure (subject to retry).
"""
from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol

from pyproj import Transformer

# ---------------------------------------------------------------------------
# Layer registry (live-verified in the ISSUE-08 scouting pass)
# ---------------------------------------------------------------------------

COJ_LAYERS: dict[int, str] = {
    2019: "https://ags.joburg.org.za/server/rest/services/AerialPhotography/2019/ImageServer",
    2023: "https://ags.joburg.org.za/server/rest/services/AerialPhotography/2023/ImageServer",
}

NATIVE_GSD_M = 0.15
EMPTY_BODY_BYTES_THRESHOLD = 5000
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_BACKOFF_BASE_S = 1.0
DEFAULT_BACKOFF_JITTER_FRAC = 0.3
DEFAULT_POLITENESS_SLEEP_S = 0.4

_WGS84_TO_3857 = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)


# ---------------------------------------------------------------------------
# Reprojection + URL construction
# ---------------------------------------------------------------------------


def wgs84_bbox_to_3857(
    lon_min: float, lat_min: float, lon_max: float, lat_max: float
) -> tuple[float, float, float, float]:
    """Reproject a WGS84 (lon/lat) bbox to Web Mercator (EPSG:3857) meters."""
    xmin, ymin = _WGS84_TO_3857.transform(lon_min, lat_min)
    xmax, ymax = _WGS84_TO_3857.transform(lon_max, lat_max)
    return xmin, ymin, xmax, ymax


def pixel_size_for_bbox(
    bbox_3857: tuple[float, float, float, float],
    *,
    native_gsd: float = NATIVE_GSD_M,
    min_px: int = 64,
    max_px: int = 1024,
) -> tuple[int, int]:
    """Choose an export pixel size approximating native GSD, capped to [min_px, max_px].

    NOTE: EPSG:3857 units are Web Mercator meters, not true ground meters —
    they overstate ground distance by sec(latitude) (~1.11x at Johannesburg's
    -26.2 deg). This yields a modestly finer-than-native export, never
    coarser; documented as a caveat in the pilot doc rather than corrected,
    since it does not affect detector/classifier scoring quality.
    """
    xmin, ymin, xmax, ymax = bbox_3857
    w = max(min_px, min(max_px, round((xmax - xmin) / native_gsd)))
    h = max(min_px, min(max_px, round((ymax - ymin) / native_gsd)))
    return int(w), int(h)


def build_export_url(
    base_url: str,
    bbox_3857: tuple[float, float, float, float],
    size_px: tuple[int, int],
    *,
    image_format: str = "tiff",
    interpolation: str = "RSP_BilinearInterpolation",
) -> str:
    xmin, ymin, xmax, ymax = bbox_3857
    w, h = size_px
    params = {
        "bbox": f"{xmin},{ymin},{xmax},{ymax}",
        "bboxSR": "3857",
        "imageSR": "3857",
        "size": f"{w},{h}",
        "format": image_format,
        "interpolation": interpolation,
        "f": "image",
    }
    query = "&".join(f"{k}={v}" for k, v in params.items())
    return f"{base_url}/exportImage?{query}"


# ---------------------------------------------------------------------------
# Backoff
# ---------------------------------------------------------------------------


class _RandLike(Protocol):
    def uniform(self, a: float, b: float) -> float: ...


def backoff_seconds(
    attempt: int,
    *,
    base: float = DEFAULT_BACKOFF_BASE_S,
    jitter_frac: float = DEFAULT_BACKOFF_JITTER_FRAC,
    rng: _RandLike | None = None,
) -> float:
    """Exponential backoff (base * 2**(attempt-1)) +/- jitter_frac, jitter in [0, +frac]."""
    rng = rng or random
    delay = base * (2 ** (attempt - 1))
    jitter = rng.uniform(0.0, jitter_frac)
    return delay * (1.0 + jitter)


# ---------------------------------------------------------------------------
# Fetch outcome
# ---------------------------------------------------------------------------


@dataclass
class FetchOutcome:
    """One reliability-stats record. JSONL-serializable via ``to_dict``."""

    layer: str = ""
    url: str = ""
    http_status: int | None = None
    content_type: str = ""
    n_bytes: int = 0
    latency_s: float = 0.0
    attempts: int = 0
    outcome: str = ""  # "ok" | "empty" | "waf_challenge" | "http_error" | "exception"
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "layer": self.layer,
            "url": self.url,
            "http_status": self.http_status,
            "content_type": self.content_type,
            "n_bytes": self.n_bytes,
            "latency_s": round(self.latency_s, 4),
            "attempts": self.attempts,
            "outcome": self.outcome,
            "error": self.error,
        }


DEFAULT_USER_AGENT = (
    "ZAsolar-CoJ-audit-pilot/1.0 (+solar rooftop census research; "
    "contact via ZAsolar repo issue tracker)"
)


def fetch_chip(
    url: str,
    *,
    http_get: Callable[[str], Any],
    sleep_fn: Callable[[float], None] = time.sleep,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    backoff_base: float = DEFAULT_BACKOFF_BASE_S,
    backoff_jitter_frac: float = DEFAULT_BACKOFF_JITTER_FRAC,
    rng: _RandLike | None = None,
    layer: str = "",
    monotonic_fn: Callable[[], float] = time.monotonic,
) -> tuple[bytes | None, FetchOutcome]:
    """Fetch one chip with retry/backoff. Returns (body_or_None, FetchOutcome).

    ``http_get(url)`` must return an object with ``.status_code`` (int),
    ``.headers`` (mapping, case-insensitivity not assumed — pass a dict with
    a "Content-Type" key), and ``.content`` (bytes); or raise an exception to
    simulate a network failure.

    Classification (checked in this order once a response is obtained):
      1. non-200 status -> "http_error" (retried)
      2. "text/html" in Content-Type -> "waf_challenge" (retried; ArcGIS never
         legitimately returns html for exportImage)
      3. body shorter than EMPTY_BODY_BYTES_THRESHOLD -> "empty" (returned
         immediately, NOT retried — this is a soft "no coverage" signal)
      4. otherwise -> "ok"

    A raised exception from ``http_get`` -> "exception" (retried).
    """
    attempt = 0
    last_outcome = FetchOutcome(layer=layer, url=url)
    while attempt < max_attempts:
        attempt += 1
        t0 = monotonic_fn()
        try:
            resp = http_get(url)
        except Exception as exc:  # noqa: BLE001 - deliberately broad; network fn is injected
            latency = monotonic_fn() - t0
            last_outcome = FetchOutcome(
                layer=layer, url=url, latency_s=latency, attempts=attempt,
                outcome="exception", error=str(exc),
            )
            if attempt >= max_attempts:
                return None, last_outcome
            sleep_fn(backoff_seconds(attempt, base=backoff_base, jitter_frac=backoff_jitter_frac, rng=rng))
            continue

        latency = monotonic_fn() - t0
        status = getattr(resp, "status_code", None)
        headers = getattr(resp, "headers", {}) or {}
        content_type = str(headers.get("Content-Type", ""))
        body = getattr(resp, "content", b"") or b""

        if status != 200:
            last_outcome = FetchOutcome(
                layer=layer, url=url, http_status=status, content_type=content_type,
                n_bytes=len(body), latency_s=latency, attempts=attempt, outcome="http_error",
            )
            if attempt >= max_attempts:
                return None, last_outcome
            sleep_fn(backoff_seconds(attempt, base=backoff_base, jitter_frac=backoff_jitter_frac, rng=rng))
            continue

        if "text/html" in content_type.lower():
            last_outcome = FetchOutcome(
                layer=layer, url=url, http_status=status, content_type=content_type,
                n_bytes=len(body), latency_s=latency, attempts=attempt, outcome="waf_challenge",
            )
            if attempt >= max_attempts:
                return None, last_outcome
            sleep_fn(backoff_seconds(attempt, base=backoff_base, jitter_frac=backoff_jitter_frac, rng=rng))
            continue

        if len(body) < EMPTY_BODY_BYTES_THRESHOLD:
            return None, FetchOutcome(
                layer=layer, url=url, http_status=status, content_type=content_type,
                n_bytes=len(body), latency_s=latency, attempts=attempt, outcome="empty",
            )

        return body, FetchOutcome(
            layer=layer, url=url, http_status=status, content_type=content_type,
            n_bytes=len(body), latency_s=latency, attempts=attempt, outcome="ok",
        )

    return None, last_outcome  # pragma: no cover - loop always returns internally


def fetch_and_save_chip(
    anchor_id: str,
    year: int,
    bbox_4326: tuple[float, float, float, float],
    out_path: Path,
    *,
    http_get: Callable[[str], Any],
    sleep_fn: Callable[[float], None] = time.sleep,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    min_px: int = 64,
    max_px: int = 1024,
    rng: _RandLike | None = None,
) -> FetchOutcome:
    """Fetch one anchor's chip for ``year`` and write it to ``out_path`` if non-empty.

    Skips the network call entirely (resumability) if ``out_path`` already
    exists and is non-empty. Idempotent: safe to re-run the pilot orchestrator
    after an interrupted fetch pass.
    """
    if out_path.exists() and out_path.stat().st_size > 0:
        return FetchOutcome(
            layer=str(year), url="", n_bytes=out_path.stat().st_size,
            attempts=0, outcome="skipped_existing",
        )

    base_url = COJ_LAYERS[year]
    lon_min, lat_min, lon_max, lat_max = bbox_4326
    bbox_3857 = wgs84_bbox_to_3857(lon_min, lat_min, lon_max, lat_max)
    size_px = pixel_size_for_bbox(bbox_3857, min_px=min_px, max_px=max_px)
    url = build_export_url(base_url, bbox_3857, size_px)

    body, outcome = fetch_chip(
        url, http_get=http_get, sleep_fn=sleep_fn, max_attempts=max_attempts,
        rng=rng, layer=str(year),
    )
    if body is not None:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(body)
    return outcome
