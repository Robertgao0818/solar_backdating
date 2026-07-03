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
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol

from pyproj import Transformer

# ---------------------------------------------------------------------------
# Layer registry (live-verified in the ISSUE-08 scouting pass; 2015 added for
# the ISSUE-09 cohort — same AerialPhotography/<year>/ImageServer pattern,
# probed live but not yet exercised against exportImage, see cohort design §9)
# ---------------------------------------------------------------------------

COJ_LAYERS: dict[int, str] = {
    2015: "https://ags.joburg.org.za/server/rest/services/AerialPhotography/2015/ImageServer",
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


# ---------------------------------------------------------------------------
# Across-units concurrent fetch driver (ISSUE-09 cohort scale, WP-B)
# ---------------------------------------------------------------------------
#
# The pilot fetched ~244 units strictly sequentially. Cohort scale (~16k units)
# needs 4-6 workers to bring the wall-clock from ~69 h down to ~14 h. The one
# thing that must NOT change is the per-request politeness posture: concurrency
# is ACROSS units only. Each ``fetch_one`` call is left fully sequential
# internally — its sleep / retry x3 backoff logic lives inside
# ``fetch_and_save_chip`` (§8 of the pilot doc), and this driver adds NO retry
# of its own. A unit's ``FetchOutcome`` is terminal: the driver classifies,
# funnels it through a single lock so the shared ``fetch_stats.jsonl`` writer
# can't interleave, tallies it, and moves on.


@dataclass(frozen=True)
class FetchUnit:
    """One planned (anchor, layer-year) fetch unit for the cohort driver.

    ``bbox_4326`` is the WGS84 (lon_min, lat_min, lon_max, lat_max) anchor box;
    ``out_path`` is where the production ``fetch_one`` wrapper writes the chip.
    Frozen so it can be a stable key / safely shared across worker threads.
    """

    anchor_id: str
    year: int
    bbox_4326: tuple[float, float, float, float]
    out_path: Path


def fetch_units_concurrent(
    units: list[FetchUnit],
    *,
    fetch_one: Callable[[FetchUnit], FetchOutcome],
    on_result: Callable[[FetchUnit, FetchOutcome], None],
    pool_size: int = 5,
    max_units: int | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> dict[str, int]:
    """Fetch ``units`` concurrently across a thread pool, one call per unit.

    Driver = ``concurrent.futures.ThreadPoolExecutor(pool_size)`` (the work is
    network I/O-bound, so threads not processes). ``fetch_one`` is injected
    (production wraps ``fetch_and_save_chip`` with the real ``requests``
    transport) and keeps ALL per-request politeness/backoff internal — this
    driver never retries. Every result (including a ``fetch_one`` that raises,
    which becomes a terminal ``outcome="exception"`` for that unit rather than
    crashing the pool) is passed to ``on_result`` under a single
    ``threading.Lock`` and tallied into the returned ``{outcome: count}``
    summary under that same lock, so callers can serialize their append+flush
    of ``fetch_stats.jsonl`` without further locking.

    ``max_units`` caps how many units are submitted (smoke runs). ``should_stop``
    is polled before each submission for cooperative interruption
    (KeyboardInterrupt) — already-submitted units still run to completion; no
    further units are handed to the pool once it returns True.
    """
    to_submit = units if max_units is None else units[:max_units]

    summary: dict[str, int] = {}
    lock = threading.Lock()
    # Bounded submission window: without it, every unit lands in the executor
    # queue instantly and ``should_stop`` (polled at submission time) can never
    # halt a run in progress — at cohort scale (~16k units) that would leave
    # Ctrl-C with nothing to stop and ``Executor.__exit__`` draining the whole
    # queue for hours. Keeping at most 2×pool_size units in flight makes
    # cooperative stop take effect within ~2×pool_size units.
    window = threading.BoundedSemaphore(pool_size * 2)

    def _worker(unit: FetchUnit) -> None:
        try:
            try:
                outcome = fetch_one(unit)
            except Exception as exc:  # noqa: BLE001 - fetch_one is injected; a raise is a terminal outcome, not a pool crash
                outcome = FetchOutcome(
                    layer=str(unit.year), url="", attempts=0,
                    outcome="exception", error=str(exc),
                )
            with lock:
                summary[outcome.outcome] = summary.get(outcome.outcome, 0) + 1
                on_result(unit, outcome)
        finally:
            window.release()

    with ThreadPoolExecutor(max_workers=pool_size) as executor:
        futures: list[Future] = []
        for unit in to_submit:
            if should_stop is not None and should_stop():
                break
            window.acquire()
            try:
                futures.append(executor.submit(_worker, unit))
            except BaseException:
                window.release()
                raise
        # Surface any driver-level failure (a bug in on_result / the lock path);
        # fetch_one exceptions are already handled inside _worker. Not retrieving
        # future exceptions would swallow them silently.
        for fut in futures:
            fut.result()

    return summary
