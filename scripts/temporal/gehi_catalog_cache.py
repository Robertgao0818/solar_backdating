#!/usr/bin/env python3
"""Persistent catalog cache for GEHistoricalImagery `availability` / `info` calls.

ISSUE-13 (docs/replan_v2/ISSUE-13-gehi-availability-cache.md, PRD D14). The
production bottleneck is live GEHI catalog calls timing out at 300s each;
scan-prep and chip re-download hit the *same* catalog query (same anchor bbox
or location, same zoom, same date range) over and over across resumed runs,
diagnostic re-checks, and repeated adaptive-scan zoom-ladder lookups. This
module gives `gehi_availability.py` and `gehi_info.py` a persistent
sqlite3-backed cache to consult instead of re-issuing the live subprocess call.

Design (see docs/gehi_catalog_cache.md for the full write-up):

- `CatalogCacheKey` — the cache correctness key. Two GEHI catalog calls are
  the "same query" iff (kind, provider, zoom, lower_left, upper_right,
  min_date, max_date, complete) match exactly. `anchor_id`/`region_key`/
  `grid_id` are bucketing metadata recorded for observability, never part of
  the key: two different anchors that happen to issue byte-identical GEHI
  commands legitimately share one cache entry.
- `CatalogCache` — thread-safe sqlite3 (WAL) key/value store: `get_any`,
  `get_fresh`, `put`. Safe to share one instance across a ThreadPoolExecutor
  of anchor workers (each connection call is guarded by an internal lock;
  WAL mode lets readers and the writer overlap without blocking each other).
- `CacheStats` — thread-safe accounting shared across a whole run: hits,
  misses, live_calls, stale_served, refresh_failures. `summary()` renders a
  one-line log message.
- `get_or_fetch` — the shared serve-or-refresh orchestration used by both
  `gehi_availability.fetch_availability_for_anchor` and
  `gehi_info.fetch_vintages_for_anchor`. Fresh hit short-circuits the live
  call entirely; on miss/stale/force-refresh it calls `live_fn()`, caches a
  success, and degrades to the last-good cached entry (if any) on failure
  instead of just propagating the error — the whole point is that a timing
  out GEHI process doesn't have to mean "no data", when we already know the
  answer from an earlier run.

`payload` is deliberately opaque to this module (JSON round-tripped as-is):
`gehi_availability.py` stores a plain `list[str]` of capture dates;
`gehi_info.py` stores the richer deduped `list[dict]` info rows (version,
path, capture_date_min/max, ...). Both call sites re-stamp
anchor_id/region_key/grid_id onto the served payload themselves — the cache
does not know each payload's field shape.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

CACHE_DIR_ENV = "SOLAR_GEHI_CATALOG_CACHE_DIR"
DEFAULT_CACHE_DIR = Path.home() / "zasolar_data" / "geid_temporal" / "catalog_cache"
DEFAULT_CATALOG_MAX_AGE_DAYS = 30.0
DB_FILENAME = "gehi_catalog_cache.sqlite3"


def default_cache_dir() -> Path:
    """Resolve the cache directory: env override, else the canonical default."""
    override = os.environ.get(CACHE_DIR_ENV)
    return Path(override).expanduser() if override else DEFAULT_CACHE_DIR


def default_db_path() -> Path:
    return default_cache_dir() / DB_FILENAME


@dataclass(frozen=True)
class CatalogCacheKey:
    """Cache correctness key for one GEHI catalog query.

    `kind` distinguishes `availability` (bbox-based) from `info`
    (location-based) queries. `info` queries have no bbox/date-range/complete
    concept, so those fields are left at their defaults ("" / False) and
    `lower_left` carries the `lat,lon` location string instead of a bbox
    corner. This keeps one table/key-shape for both kinds without conflating
    an info query for one location with an availability query for the same
    string used as a bbox corner (the `kind` field partitions them).
    """

    kind: str
    provider: str
    zoom: int
    lower_left: str
    upper_right: str = ""
    min_date: str = ""
    max_date: str = ""
    complete: bool = False

    def as_row(self) -> tuple:
        return (
            self.kind,
            self.provider,
            int(self.zoom),
            self.lower_left,
            self.upper_right,
            self.min_date,
            self.max_date,
            bool(self.complete),
        )


@dataclass(frozen=True)
class CatalogCacheEntry:
    key: CatalogCacheKey
    payload: Any
    fetched_at: str  # ISO-8601 UTC timestamp string
    stdout_sha256: str
    gehi_command: str
    anchor_id: str
    region_key: str
    grid_id: str

    @property
    def age_days(self) -> float:
        fetched = datetime.fromisoformat(self.fetched_at)
        if fetched.tzinfo is None:
            fetched = fetched.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        return (now - fetched).total_seconds() / 86400.0


@dataclass
class CacheStats:
    """Thread-safe hit/miss/live-call accounting for one cache session."""

    hits: int = 0
    misses: int = 0
    live_calls: int = 0
    stale_served: int = 0
    refresh_failures: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    def record_hit(self) -> None:
        with self._lock:
            self.hits += 1

    def record_miss(self) -> None:
        with self._lock:
            self.misses += 1

    def record_live_call(self) -> None:
        with self._lock:
            self.live_calls += 1

    def record_stale_served(self) -> None:
        with self._lock:
            self.stale_served += 1

    def record_refresh_failure(self) -> None:
        with self._lock:
            self.refresh_failures += 1

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            return {
                "hits": self.hits,
                "misses": self.misses,
                "live_calls": self.live_calls,
                "stale_served": self.stale_served,
                "refresh_failures": self.refresh_failures,
            }

    def summary(self) -> str:
        s = self.snapshot()
        return (
            "catalog_cache: "
            f"hits={s['hits']} misses={s['misses']} live_calls={s['live_calls']} "
            f"stale_served={s['stale_served']} refresh_failures={s['refresh_failures']}"
        )


class CatalogCache:
    """Thread-safe sqlite3 (WAL) cache of GEHI catalog query results.

    One instance is meant to be shared across an entire run (including across
    a ThreadPoolExecutor of anchor workers): every public method takes an
    internal lock around the sqlite3 connection, and the connection itself
    runs in WAL journal mode so concurrent readers never block the writer for
    long. sqlite3 connections are not safe to share across threads without
    care; `check_same_thread=False` plus our own lock is the standard pattern
    for a single shared connection used by multiple threads that never issue
    overlapping statements on it concurrently (the lock enforces that).
    """

    def __init__(self, db_path: Path | str | None = None, *, stats: CacheStats | None = None):
        self.db_path = Path(db_path) if db_path is not None else default_db_path()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.stats = stats if stats is not None else CacheStats()
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("PRAGMA synchronous=NORMAL;")
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS catalog_cache (
                    kind TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    zoom INTEGER NOT NULL,
                    lower_left TEXT NOT NULL,
                    upper_right TEXT NOT NULL,
                    min_date TEXT NOT NULL,
                    max_date TEXT NOT NULL,
                    complete INTEGER NOT NULL,
                    payload_json TEXT NOT NULL,
                    fetched_at TEXT NOT NULL,
                    stdout_sha256 TEXT NOT NULL,
                    gehi_command TEXT NOT NULL,
                    anchor_id TEXT NOT NULL,
                    region_key TEXT NOT NULL,
                    grid_id TEXT NOT NULL,
                    PRIMARY KEY (
                        kind, provider, zoom, lower_left, upper_right,
                        min_date, max_date, complete
                    )
                )
                """
            )
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def __enter__(self) -> "CatalogCache":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def put(
        self,
        key: CatalogCacheKey,
        *,
        payload: Any,
        stdout_sha256: str,
        gehi_command: str,
        anchor_id: str = "",
        region_key: str = "",
        grid_id: str = "",
        fetched_at: str | None = None,
    ) -> None:
        fetched_at = fetched_at or datetime.now(timezone.utc).isoformat()
        row = key.as_row()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO catalog_cache (
                    kind, provider, zoom, lower_left, upper_right, min_date, max_date, complete,
                    payload_json, fetched_at, stdout_sha256, gehi_command, anchor_id, region_key, grid_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(kind, provider, zoom, lower_left, upper_right, min_date, max_date, complete)
                DO UPDATE SET
                    payload_json=excluded.payload_json,
                    fetched_at=excluded.fetched_at,
                    stdout_sha256=excluded.stdout_sha256,
                    gehi_command=excluded.gehi_command,
                    anchor_id=excluded.anchor_id,
                    region_key=excluded.region_key,
                    grid_id=excluded.grid_id
                """,
                (*row, json.dumps(payload), fetched_at, stdout_sha256, gehi_command, anchor_id, region_key, grid_id),
            )
            self._conn.commit()

    def get_any(self, key: CatalogCacheKey) -> CatalogCacheEntry | None:
        """Return the cached entry for `key` regardless of freshness."""
        row = key.as_row()
        with self._lock:
            cur = self._conn.execute(
                """
                SELECT payload_json, fetched_at, stdout_sha256, gehi_command, anchor_id, region_key, grid_id
                FROM catalog_cache
                WHERE kind=? AND provider=? AND zoom=? AND lower_left=? AND upper_right=?
                  AND min_date=? AND max_date=? AND complete=?
                """,
                row,
            )
            record = cur.fetchone()
        if record is None:
            return None
        payload_json, fetched_at, stdout_sha256, gehi_command, anchor_id, region_key, grid_id = record
        return CatalogCacheEntry(
            key=key,
            payload=json.loads(payload_json),
            fetched_at=fetched_at,
            stdout_sha256=stdout_sha256,
            gehi_command=gehi_command,
            anchor_id=anchor_id,
            region_key=region_key,
            grid_id=grid_id,
        )

    def get_fresh(
        self, key: CatalogCacheKey, *, max_age_days: float = DEFAULT_CATALOG_MAX_AGE_DAYS
    ) -> CatalogCacheEntry | None:
        """Return the cached entry for `key` only if within `max_age_days`."""
        entry = self.get_any(key)
        if entry is None:
            return None
        if entry.age_days > max_age_days:
            return None
        return entry


def get_or_fetch(
    cache: CatalogCache,
    key: CatalogCacheKey,
    *,
    force_refresh: bool = False,
    max_age_days: float = DEFAULT_CATALOG_MAX_AGE_DAYS,
    anchor_id: str = "",
    region_key: str = "",
    grid_id: str = "",
    live_fn: Callable[[], tuple[Any, str, str]],
    label: str = "gehi_catalog_cache",
) -> Any:
    """Serve `key` from `cache`, calling `live_fn()` on miss/stale/force.

    `live_fn` performs the actual GEHI subprocess call (+ success assertion +
    output parsing) and returns `(payload, stdout_sha256, gehi_command)` on
    success, or raises (subprocess.TimeoutExpired / RuntimeError / any other
    Exception) on failure. `payload` must be JSON-serializable.

    - Fresh hit (age <= max_age_days) and not force_refresh: no live call at
      all; returns the cached payload (`stats.hits`).
    - Miss or force_refresh: calls `live_fn()`.
      - Success: caches the result and returns the fresh payload
        (`stats.misses` + `stats.live_calls`).
      - Failure with an existing (possibly stale) cache entry: warns to
        stderr and returns the stale payload instead of raising
        (`stats.stale_served`, plus the same `misses`/`live_calls`).
      - Failure with no cache entry at all: re-raises the original exception
        (`stats.refresh_failures`) — callers that want the pre-cache
        warn-and-return-empty behavior on `TimeoutExpired`/generic
        `Exception` (but a hard raise on `RuntimeError`) implement that by
        catching around this call, exactly as they did around the raw live
        call before caching existed.
    """
    if not force_refresh:
        entry = cache.get_fresh(key, max_age_days=max_age_days)
        if entry is not None:
            cache.stats.record_hit()
            return entry.payload
    cache.stats.record_miss()
    cache.stats.record_live_call()
    try:
        payload, stdout_sha256, gehi_command = live_fn()
    except Exception as exc:  # noqa: BLE001 - deliberately broad: degrade-to-stale covers all failure modes
        stale = cache.get_any(key)
        if stale is not None:
            cache.stats.record_stale_served()
            print(
                f"[{label}] anchor {anchor_id or '?'}: live catalog fetch failed "
                f"({type(exc).__name__}: {exc}); serving stale cache entry from "
                f"{stale.fetched_at} (age {stale.age_days:.1f}d)",
                file=sys.stderr,
            )
            return stale.payload
        cache.stats.record_refresh_failure()
        raise
    cache.put(
        key,
        payload=payload,
        stdout_sha256=stdout_sha256,
        gehi_command=gehi_command,
        anchor_id=anchor_id,
        region_key=region_key,
        grid_id=grid_id,
    )
    return payload


def add_catalog_cache_cli_args(parser: Any) -> None:
    """Add the shared `--catalog-*` flags to an argparse parser.

    Shared by `gehi_availability.py`, `gehi_info.py`, and
    `run_adaptive_scan.py` so the flag names/help text/defaults never drift
    between the three CLIs. Default = cache ON everywhere it's wired in.
    """
    parser.add_argument(
        "--no-catalog-cache",
        action="store_true",
        help="Disable the persistent GEHI catalog cache (ISSUE-13); always issue live "
        "availability/info calls. Unrelated to GEHI's own --no-cache raw-tile flag.",
    )
    parser.add_argument(
        "--force-catalog-refresh",
        action="store_true",
        help="Bypass fresh cache hits and re-issue every catalog call live, "
        "still updating the cache with the fresh result. Ignored with --no-catalog-cache.",
    )
    parser.add_argument(
        "--catalog-max-age-days",
        type=float,
        default=DEFAULT_CATALOG_MAX_AGE_DAYS,
        help=f"Cache entries older than this are treated as stale (default {DEFAULT_CATALOG_MAX_AGE_DAYS}d): "
        "a live refresh is attempted, and the stale entry is only served if that refresh fails.",
    )
    parser.add_argument(
        "--catalog-cache-dir",
        type=Path,
        default=None,
        help=f"Directory holding the catalog cache sqlite3 file. Default: ${CACHE_DIR_ENV} "
        f"if set, else {DEFAULT_CACHE_DIR}.",
    )
