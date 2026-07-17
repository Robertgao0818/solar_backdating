#!/usr/bin/env python3
"""Download exact-date GEHistoricalImagery GeoTIFF chips for anchor/date rows.

Supports a zoom ladder (try each zoom in order, fall back on failure or empty
output) so the orchestrator can prefer higher-GSD captures (z=20) and gracefully
fall back to z=19 when only that level has the requested vintage. Idempotent:
if a non-empty file already exists at any ladder zoom for the anchor/date/version,
the download is skipped.

GEHI calls are paced and 403/429-backed-off by default via the shared
`GehiRateLimiter` (see gehi_common; tune with --request-interval /
--max-attempts), and the tile cache is pinned to the shared directory so
repeated/overlapping requests hit disk instead of the network.
"""

from __future__ import annotations

# Imports follow a sys.path bootstrap (below) so the subrepo can be run as a
# script; E402 is expected for the scripts.* imports, matching the sibling
# temporal modules' convention.
# ruff: noqa: E402

import argparse
import csv
import hashlib
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.temporal.gehi_common import (
    DEFAULT_GEHI_EXE,
    DEFAULT_MAX_ATTEMPTS,
    DEFAULT_PROBE_ZOOM,
    DEFAULT_PROVIDER,
    DEFAULT_REQUEST_INTERVAL_S,
    GehiRateLimiter,
    GehiRunResult,
    anchor_bbox_args,
    iso_to_gehi_date,
    make_throttled_runner,
    run_gehi,
)
from scripts.temporal.geid_temporal_common import read_csv_rows, safe_task_token, write_csv_rows

DEFAULT_ANCHORS = PROJECT_ROOT / "data" / "geid_temporal" / "anchors.csv"
DEFAULT_CANDIDATES = PROJECT_ROOT / "data" / "geid_temporal" / "gehi_vintage_candidates.csv"
DEFAULT_OUTPUT_DIR = Path.home() / "zasolar_data" / "geid_temporal" / "gehi_chips"
DEFAULT_MANIFEST = PROJECT_ROOT / "data" / "geid_temporal" / "gehi_image_artifacts.csv"
DEFAULT_RAW_LOG = PROJECT_ROOT / "data" / "geid_temporal" / "gehi_download_raw.jsonl"

FIELDS = [
    "artifact_id",
    "anchor_id",
    "region_key",
    "grid_id",
    "provider",
    "zoom",
    "actual_zoom",
    "requested_zoom_ladder",
    "capture_date",
    "version",
    "path",
    "sha256",
    "status",
    "exact_date",
    "download_stdout_sha256",
    "gehi_command",
    # Raster-measured provenance (ISSUE-18 / D17). Additive: measured from the
    # chip on disk via build_chip_provenance for both ok and skipped_existing.
    "raster_width_px",
    "raster_height_px",
    "raster_crs",
    "extent_minx",
    "extent_miny",
    "extent_maxx",
    "extent_maxy",
    "gsd_x_m",
    "gsd_y_m",
    "raster_error",
]

# Canonical per-chip provenance keys (ISSUE-18 / D17). Pinned so both ISSUE-18
# work items and the provenance docs use exactly these names; shaped to join the
# ISSUE-06 scoring sidecar on (anchor_id, capture_date, version) + chip_sha256.
CHIP_PROVENANCE_FIELDS = (
    "anchor_id",
    "capture_date",
    "version",
    "provider",
    "requested_zoom_ladder",
    "achieved_zoom",
    "status",
    "chip_path",
    "chip_sha256",
    "raster_width_px",
    "raster_height_px",
    "raster_crs",
    "extent_minx",
    "extent_miny",
    "extent_maxx",
    "extent_maxy",
    "gsd_x_m",
    "gsd_y_m",
    "raster_error",
)


@dataclass
class DownloadResult:
    anchor_id: str
    capture_date: str
    version: str
    requested_zoom_ladder: tuple[int, ...]
    actual_zoom: int | None
    path: Path | None
    sha256: str
    status: str  # "ok" | "skipped_existing" | "all_zooms_failed"
    error: str | None
    gehi_command: str
    download_stdout_sha256: str


def parse_zoom_ladder(value: str) -> tuple[int, ...]:
    parts = [s.strip() for s in str(value).split(",") if s.strip()]
    if not parts:
        raise ValueError(f"empty zoom ladder: {value!r}")
    return tuple(int(p) for p in parts)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--anchors-csv", type=Path, default=DEFAULT_ANCHORS)
    parser.add_argument("--candidates-csv", type=Path, default=DEFAULT_CANDIDATES)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--raw-log", type=Path, default=DEFAULT_RAW_LOG)
    parser.add_argument("--gehi-exe", type=Path, default=DEFAULT_GEHI_EXE)
    parser.add_argument(
        "--zoom",
        type=str,
        default=str(DEFAULT_PROBE_ZOOM),
        help="Zoom ladder as comma-separated levels, tried in order. Example: '20,19' tries z=20 first, falls back to z=19.",
    )
    parser.add_argument("--provider", default=DEFAULT_PROVIDER)
    parser.add_argument("--parallel", type=int, default=4)
    parser.add_argument("--target-sr", default="", help="Optional EPSG:#### or WKT path passed to GEHI.")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--no-cache", action="store_true")
    parser.add_argument("--timeout", type=float, default=600.0)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument(
        "--min-cache-zoom",
        type=int,
        default=None,
        help="Cache-refresh escape hatch: refuse cached chips below this zoom so the "
        "ladder re-fetches and upgrades them. Governs cache acceptance only; live "
        "attempts are unaffected. Default: off (accept any cached zoom).",
    )
    parser.add_argument("--allow-nearest", action="store_true", help="Do not pass GEHI --exact-date.")
    parser.add_argument(
        "--request-interval",
        type=float,
        default=DEFAULT_REQUEST_INTERVAL_S,
        help="Anti-ban pacing: minimum seconds between GEHI invocations (+ jitter). 0 disables pacing "
        "(403/429 backoff stays active).",
    )
    parser.add_argument(
        "--max-attempts",
        type=int,
        default=DEFAULT_MAX_ATTEMPTS,
        help="Total attempts per zoom when GEHI stderr carries a 403/429 block signal; each block sleeps "
        "the soft backoff (60s), escalating to a hard backoff (30min) after 5 consecutive blocks.",
    )
    parser.add_argument(
        "--no-recompress",
        action="store_true",
        help="Keep GEHI's uncompressed GeoTIFF output as-is. Default: recompress each fresh "
        "chip in place as tiled JPEG-in-GeoTIFF (quality 95, ~8-15x smaller, CRS/transform "
        "preserved; failures keep the original and are recorded in the raw log).",
    )
    parser.add_argument(
        "--allow-failures",
        action="store_true",
        help="Exit 0 even if some candidates failed at every ladder zoom. Default: exit 1 on any all_zooms_failed.",
    )
    return parser.parse_args()


# Local JPEG-in-GeoTIFF recompression (same precedent as
# scripts/audit/coj_arcgis_fetch.py:_write_jpeg_compressed_geotiff): GEHI's
# uncompressed GeoTIFF output is ~8-15x larger on disk than the same pixels
# JPEG-compressed, and the source Google tiles are JPEG to begin with.
JPEG_COMPRESSION_QUALITY = 95


def _recompress_jpeg_in_geotiff(path: Path) -> str | None:
    """Recompress a GeoTIFF in place as tiled JPEG-in-GeoTIFF (quality 95).

    Preserves CRS/transform (rasterio round-trip). Writes to a sibling temp
    file and `os.replace`s it so a crash mid-write never corrupts the chip.
    Returns None on success, an error string on failure (caller keeps the
    original file — recompression is a disk optimization, never a reason to
    fail a download). Band counts other than 1/3 (JPEG can't encode them) are
    skipped with a reason string.
    """
    import os

    import rasterio

    tmp_path = path.with_name(path.name + ".recompress.tmp")
    try:
        with rasterio.open(path) as src:
            profile = src.profile.copy()
            if profile.get("compress", "").upper() == "JPEG":
                return None  # already recompressed (e.g. resumed run)
            if profile.get("count", 1) not in (1, 3):
                return f"unsupported band count {profile.get('count')} for JPEG"
            if profile.get("dtype") != "uint8":
                return f"unsupported dtype {profile.get('dtype')} for JPEG"
            data = src.read()
        profile.update(
            driver="GTiff", compress="JPEG", jpeg_quality=JPEG_COMPRESSION_QUALITY,
            tiled=True, blockxsize=512, blockysize=512,
        )
        if profile.get("count", 1) == 3:
            profile["photometric"] = "YCBCR"
        with rasterio.open(tmp_path, "w", **profile) as dst:
            dst.write(data)
        os.replace(tmp_path, path)
        return None
    except Exception as exc:  # noqa: BLE001 - never fail a download over recompression
        tmp_path.unlink(missing_ok=True)
        return f"{type(exc).__name__}: {exc}"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def build_chip_provenance(
    outcome: "DownloadResult",
    anchor: Mapping[str, object],
    provider: str,
) -> dict[str, object]:
    """Build the canonical per-chip provenance record for a download outcome.

    Returns exactly the keys in ``CHIP_PROVENANCE_FIELDS``. The record is shaped
    to join the ISSUE-06 presence-scoring sidecar on the tuple
    ``(anchor_id, capture_date, version)`` plus ``chip_sha256`` — keep these
    field names stable so that join holds.

    Raster geometry (pixel dims, CRS, extent, GSD) is measured from the chip on
    disk with rasterio and is produced for **both** ``status="ok"`` and
    ``status="skipped_existing"``: cache hits are measured too, because the
    skip-existing cache is exactly where a low-zoom chip hides after a ladder
    upgrade — provenance is worthless if it stops at the cache boundary. On any
    raster read failure (missing/corrupt/unreadable file) every raster field is
    ``None`` and ``raster_error`` carries the message; this function never
    raises.

    Geographic (degree) CRSs are converted to metres/pixel for GSD using the
    chip-centre latitude: ``gsd_x_m = xres_deg * 111320 * cos(lat)`` and
    ``gsd_y_m = yres_deg * 111320``. Projected CRSs are already metric, so their
    pixel resolution is used directly.

    Caveat: ``achieved_zoom`` is the ladder rung GEHI *reported* for the chip as
    a whole. GEHI can silently substitute coarser tiles (nearest-neighbour
    upsampled) for individual 256 px tiles inside a nominally-successful
    download; that in-chip substitution is invisible at this level and is
    instead flagged by the sentinel effective-resolution estimator (a sibling
    ISSUE-18 deliverable). ``anchor`` is accepted for caller symmetry / future
    provenance enrichment; the measured latitude comes from the raster itself.
    """
    path = outcome.path
    chip_sha256: str | None
    if outcome.sha256:
        chip_sha256 = outcome.sha256
    elif path is not None and path.exists():
        chip_sha256 = sha256_file(path)
    else:
        chip_sha256 = None

    record: dict[str, object] = {
        "anchor_id": outcome.anchor_id,
        "capture_date": outcome.capture_date,
        "version": outcome.version,
        "provider": provider,
        "requested_zoom_ladder": [int(z) for z in outcome.requested_zoom_ladder],
        "achieved_zoom": outcome.actual_zoom,
        "status": outcome.status,
        "chip_path": str(path) if path is not None else None,
        "chip_sha256": chip_sha256,
        "raster_width_px": None,
        "raster_height_px": None,
        "raster_crs": None,
        "extent_minx": None,
        "extent_miny": None,
        "extent_maxx": None,
        "extent_maxy": None,
        "gsd_x_m": None,
        "gsd_y_m": None,
        "raster_error": None,
    }
    if path is None:
        record["raster_error"] = "no chip path (download produced no file)"
        return record
    try:
        import rasterio

        with rasterio.open(path) as ds:
            bounds = ds.bounds
            xres, yres = ds.res
            crs = ds.crs
            record["raster_width_px"] = int(ds.width)
            record["raster_height_px"] = int(ds.height)
            record["raster_crs"] = crs.to_string() if crs is not None else None
            record["extent_minx"] = float(bounds.left)
            record["extent_miny"] = float(bounds.bottom)
            record["extent_maxx"] = float(bounds.right)
            record["extent_maxy"] = float(bounds.top)
            if crs is not None and crs.is_geographic:
                center_lat = (float(bounds.bottom) + float(bounds.top)) / 2.0
                record["gsd_x_m"] = float(xres) * 111320.0 * math.cos(math.radians(center_lat))
                record["gsd_y_m"] = float(yres) * 111320.0
            else:
                record["gsd_x_m"] = float(xres)
                record["gsd_y_m"] = float(yres)
    except Exception as exc:  # noqa: BLE001 - provenance must never break the pipeline
        record["raster_error"] = f"{type(exc).__name__}: {exc}"
        for key in (
            "raster_width_px", "raster_height_px", "raster_crs",
            "extent_minx", "extent_miny", "extent_maxx", "extent_maxy",
            "gsd_x_m", "gsd_y_m",
        ):
            record[key] = None
    return record


def load_anchor_index(path: Path) -> dict[str, Mapping[str, str]]:
    return {row["anchor_id"]: row for row in read_csv_rows(path)}


def read_candidate_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8") as fh:
        return [dict(row) for row in csv.DictReader(fh) if row.get("anchor_id") and row.get("capture_date")]


def expand_candidate_dates(row: Mapping[str, str]) -> list[dict[str, str]]:
    """Return concrete download rows from a candidate row.

    `gehi_info.py` dedupes catalog rows by `(anchor_id, capture_date)`,
    preserving distinct capture dates within a shared version. Downloading is
    still date-specific, especially for smoke regression rows such as
    2015-08-30.
    """
    all_dates = [d for d in str(row.get("all_capture_dates", "")).split(";") if d]
    if not all_dates:
        all_dates = [str(row["capture_date"])[:10]]
    out = []
    for capture_date in all_dates:
        item = dict(row)
        item["capture_date"] = capture_date
        out.append(item)
    return out


def artifact_path(root: Path, row: Mapping[str, str], *, zoom: int) -> Path:
    anchor_id = safe_task_token(row["anchor_id"])
    capture = str(row["capture_date"])[:10]
    version = str(row.get("version", "")).strip() or "noversion"
    return root / anchor_id / f"z{zoom}" / f"{anchor_id}_{capture.replace('-', '')}_v{version}.tif"


def _chip_path_for(output_root: Path, anchor_id: str, capture_date: str, version: str, zoom: int) -> Path:
    return artifact_path(
        output_root,
        {"anchor_id": anchor_id, "capture_date": capture_date, "version": version},
        zoom=zoom,
    )


def _quarantine_partial(out_path: Path) -> None:
    """Remove a leftover output file from a failed GEHI attempt so idempotent
    re-runs do not later mistake it for a successful download. Called after any
    non-success path in `download_chip_with_zoom_ladder`.
    """
    try:
        if out_path.exists():
            out_path.unlink()
    except OSError:
        pass


def download_chip_with_zoom_ladder(
    anchor: Mapping[str, object],
    *,
    capture_date: str,
    version: str | int,
    zoom_ladder: Sequence[int],
    output_root: Path,
    provider: str = DEFAULT_PROVIDER,
    gehi_exe: Path = DEFAULT_GEHI_EXE,
    parallel: int = 4,
    no_cache: bool = False,
    timeout: float = 600.0,
    overwrite: bool = False,
    min_cache_zoom: int | None = None,
    target_sr: str = "",
    allow_nearest: bool = False,
    runner: Callable[..., GehiRunResult] | None = None,
    limiter: GehiRateLimiter | None = None,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    raw_log_callback: Callable[[Mapping[str, object]], None] | None = None,
    vintage_check: Callable[[int, str], bool] | None = None,
    recompress: bool = False,
    no_live_gehi: bool = False,
) -> DownloadResult:
    """Download a chip at the first zoom in `zoom_ladder` that succeeds.

    Idempotent: scans the ladder for an existing non-empty chip first and
    returns it (preferring the highest-quality / earliest-in-ladder match)
    without re-running GEHI, but only if the optional `vintage_check` still
    admits that zoom/date. On miss, attempts each zoom in ladder order; falls
    back to next zoom on non-zero return code, empty output, runner exception,
    or `vintage_check(zoom, capture_date) is False`. Any non-empty file left
    by a failed attempt is removed so a later run re-attempts cleanly.

    `vintage_check` is an optional provenance gate: when supplied, the ladder
    skips any zoom whose vintage catalog does not contain `capture_date`. The
    intended source is a per-anchor, per-zoom GEHI info catalog cached by the
    caller (see `make_vintage_check` in run_adaptive_scan).

    `min_cache_zoom` is the cache-refresh escape hatch (ISSUE-18 / D17). It
    governs *cache acceptance only*: during the skip-existing scan a cached
    chip whose zoom is below `min_cache_zoom` is refused (a raw_log record with
    `skip_reason="cache_below_min_zoom"` is emitted), so the ladder falls
    through to a live download and can upgrade the pinned chip. It does NOT
    tighten live attempts — a fresh lower-zoom download is still legal when the
    higher rung has no vintage. `overwrite=True` bypasses the cache scan
    entirely and so ignores `min_cache_zoom`.

    Anti-ban throttling: when `runner` is not supplied, GEHI calls go through
    `make_throttled_runner` — paced by `limiter` (the process-wide shared
    limiter when None) and retried up to `max_attempts` per zoom on 403/429
    block signals with soft/hard backoff. An explicitly injected `runner` is
    used as-is unless `limiter` is also passed, in which case it is wrapped in
    the same throttle.

    `recompress=True` rewrites each FRESH download in place as tiled
    JPEG-in-GeoTIFF (quality 95) before hashing; cached (`skipped_existing`)
    chips are never touched. Library default is False so existing callers
    (e.g. run_adaptive_scan) keep byte-identical behavior; the CLI entrypoint
    enables it unless --no-recompress is passed.

    `no_live_gehi=True` (ISSUE zero-live-GEHI mode, added after the 2026-07-16
    a24_v6 khmdb-ban stall) forbids this call from EVER spawning a GEHI
    subprocess: once the idempotent cache scan above finds no usable existing
    chip across the whole ladder, the live-download loop is skipped entirely
    (``runner`` is never invoked) and the pick is recorded as
    ``status="all_zooms_failed"`` with an ``error`` naming the block, exactly
    the same failure shape a real all-zooms-blocked download would produce.
    Callers (``execute_round_real``) fold this into the existing
    ``download_failed: ...`` notes path unchanged -- no new state shape.
    Library default is False so existing callers/tests are unaffected.
    """
    if not zoom_ladder:
        raise ValueError("zoom_ladder must be non-empty")
    if runner is None:
        # Late-bind the module-global run_gehi (tests stub it via
        # monkeypatch.setattr) instead of freezing it as a def-time default.
        def _base_runner(cmd_args, *, executable=DEFAULT_GEHI_EXE, timeout=300.0):
            return run_gehi(cmd_args, executable=executable, timeout=timeout)

        runner = make_throttled_runner(base_runner=_base_runner, limiter=limiter, max_attempts=max_attempts)
    elif limiter is not None:
        runner = make_throttled_runner(base_runner=runner, limiter=limiter, max_attempts=max_attempts)
    anchor_id = str(anchor["anchor_id"])
    version_str = str(version).strip()
    ladder = tuple(int(z) for z in zoom_ladder)
    last_error: str | None = None

    if not overwrite:
        for zoom in ladder:
            candidate_path = _chip_path_for(output_root, anchor_id, capture_date, version_str, zoom)
            if candidate_path.exists() and candidate_path.stat().st_size > 0:
                if min_cache_zoom is not None and zoom < min_cache_zoom:
                    last_error = (
                        f"cache_below_min_zoom at z={zoom}: cached zoom below "
                        f"min_cache_zoom={min_cache_zoom}"
                    )
                    if raw_log_callback is not None:
                        raw_log_callback(
                            {
                                "anchor_id": anchor_id,
                                "capture_date": capture_date,
                                "version": version_str,
                                "zoom_attempt": zoom,
                                "path": str(candidate_path),
                                "skip_reason": "cache_below_min_zoom",
                                "cached_zoom": zoom,
                                "min_cache_zoom": min_cache_zoom,
                            }
                        )
                    continue
                if vintage_check is not None:
                    try:
                        vintage_present = bool(vintage_check(zoom, capture_date))
                    except Exception as exc:  # noqa: BLE001
                        last_error = f"vintage_check raised for cached z={zoom}: {type(exc).__name__}: {exc}"
                        if raw_log_callback is not None:
                            raw_log_callback(
                                {
                                    "anchor_id": anchor_id,
                                    "capture_date": capture_date,
                                    "version": version_str,
                                    "zoom_attempt": zoom,
                                    "path": str(candidate_path),
                                    "skip_reason": last_error,
                                }
                            )
                        continue
                    if not vintage_present:
                        last_error = f"cached_vintage_check_failed at z={zoom}: capture_date {capture_date} not in catalog"
                        if raw_log_callback is not None:
                            raw_log_callback(
                                {
                                    "anchor_id": anchor_id,
                                    "capture_date": capture_date,
                                    "version": version_str,
                                    "zoom_attempt": zoom,
                                    "path": str(candidate_path),
                                    "skip_reason": last_error,
                                }
                            )
                        continue
                return DownloadResult(
                    anchor_id=anchor_id,
                    capture_date=capture_date,
                    version=version_str,
                    requested_zoom_ladder=ladder,
                    actual_zoom=zoom,
                    path=candidate_path,
                    sha256=sha256_file(candidate_path),
                    status="skipped_existing",
                    error=None,
                    gehi_command="",
                    download_stdout_sha256="",
                )

    if no_live_gehi:
        # Zero-live-GEHI mode: the cache scan above found no usable chip at any
        # ladder zoom, and the flag forbids the live-download loop below from
        # ever running (that loop is the only thing in this function that
        # calls `runner`, i.e. spawns a GEHI subprocess). Fail the pick the
        # same way a real all-zooms-blocked download would, WITHOUT touching
        # `runner` -- never a silent no-op, never a live call.
        last_error = (
            f"no_live_gehi: no cached chip on disk for any zoom in {ladder} "
            f"(anchor={anchor_id}, capture_date={capture_date}, version={version_str}); "
            "live GEHI download blocked by --no-live-gehi"
        )
        if raw_log_callback is not None:
            raw_log_callback(
                {
                    "anchor_id": anchor_id,
                    "capture_date": capture_date,
                    "version": version_str,
                    "skip_reason": "no_live_gehi_chip_missing",
                    "requested_zoom_ladder": list(ladder),
                    "error": last_error,
                }
            )
        return DownloadResult(
            anchor_id=anchor_id,
            capture_date=capture_date,
            version=version_str,
            requested_zoom_ladder=ladder,
            actual_zoom=None,
            path=None,
            sha256="",
            status="all_zooms_failed",
            error=last_error,
            gehi_command="",
            download_stdout_sha256="",
        )

    lower_left, upper_right = anchor_bbox_args(anchor)
    for zoom in ladder:
        if vintage_check is not None:
            try:
                vintage_present = bool(vintage_check(zoom, capture_date))
            except Exception as exc:  # noqa: BLE001
                last_error = f"vintage_check raised at z={zoom}: {type(exc).__name__}: {exc}"
                if raw_log_callback is not None:
                    raw_log_callback(
                        {
                            "anchor_id": anchor_id,
                            "capture_date": capture_date,
                            "version": version_str,
                            "zoom_attempt": zoom,
                            "skip_reason": last_error,
                        }
                    )
                continue
            if not vintage_present:
                last_error = f"vintage_check_failed at z={zoom}: capture_date {capture_date} not in catalog"
                if raw_log_callback is not None:
                    raw_log_callback(
                        {
                            "anchor_id": anchor_id,
                            "capture_date": capture_date,
                            "version": version_str,
                            "zoom_attempt": zoom,
                            "skip_reason": last_error,
                        }
                    )
                continue
        out_path = _chip_path_for(output_root, anchor_id, capture_date, version_str, zoom)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        cmd_args: list[object] = [
            "download",
            "--lower-left",
            lower_left,
            "--upper-right",
            upper_right,
            "--zoom",
            zoom,
            "--date",
            iso_to_gehi_date(capture_date),
            "--output",
            out_path,
            "--parallel",
            parallel,
            "--provider",
            provider,
        ]
        if not allow_nearest:
            cmd_args.append("--exact-date")
        if target_sr:
            cmd_args.extend(["--target-sr", target_sr])
        if no_cache:
            cmd_args.append("--no-cache")
        try:
            result = runner(cmd_args, executable=gehi_exe, timeout=timeout)
        except Exception as exc:
            last_error = f"runner exception at z={zoom}: {type(exc).__name__}: {exc}"
            _quarantine_partial(out_path)
            if raw_log_callback is not None:
                raw_log_callback(
                    {
                        "anchor_id": anchor_id,
                        "capture_date": capture_date,
                        "version": version_str,
                        "zoom_attempt": zoom,
                        "error": last_error,
                    }
                )
            continue
        if raw_log_callback is not None:
            raw_log_callback(
                {
                    "anchor_id": anchor_id,
                    "capture_date": capture_date,
                    "version": version_str,
                    "zoom_attempt": zoom,
                    "returncode": result.returncode,
                    "command": result.command,
                    "stdout_sha256": result.stdout_sha256,
                    "stderr_sha256": result.stderr_sha256,
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                    "path": str(out_path),
                }
            )
        if result.returncode != 0:
            last_error = f"GEHI returncode={result.returncode} at z={zoom}: {result.stderr[:300] or result.stdout[:300]}"
            _quarantine_partial(out_path)
            continue
        if not out_path.exists() or out_path.stat().st_size == 0:
            last_error = f"GEHI succeeded but output file empty/missing at z={zoom}"
            _quarantine_partial(out_path)
            continue
        if recompress:
            recompress_error = _recompress_jpeg_in_geotiff(out_path)
            if recompress_error is not None and raw_log_callback is not None:
                # Chip is kept uncompressed -- a disk-size regression, not a
                # download failure.
                raw_log_callback(
                    {
                        "anchor_id": anchor_id,
                        "capture_date": capture_date,
                        "version": version_str,
                        "zoom_attempt": zoom,
                        "path": str(out_path),
                        "recompress_error": recompress_error,
                    }
                )
        return DownloadResult(
            anchor_id=anchor_id,
            capture_date=capture_date,
            version=version_str,
            requested_zoom_ladder=ladder,
            actual_zoom=zoom,
            path=out_path,
            sha256=sha256_file(out_path),
            status="ok",
            error=None,
            gehi_command=result.command,
            download_stdout_sha256=result.stdout_sha256,
        )

    return DownloadResult(
        anchor_id=anchor_id,
        capture_date=capture_date,
        version=version_str,
        requested_zoom_ladder=ladder,
        actual_zoom=None,
        path=None,
        sha256="",
        status="all_zooms_failed",
        error=last_error,
        gehi_command="",
        download_stdout_sha256="",
    )


def main() -> None:
    args = parse_args()
    if not args.anchors_csv.exists():
        raise SystemExit(f"Anchor CSV not found: {args.anchors_csv}")
    if not args.candidates_csv.exists():
        raise SystemExit(f"Candidates CSV not found: {args.candidates_csv}")

    zoom_ladder = parse_zoom_ladder(args.zoom)
    anchors = load_anchor_index(args.anchors_csv)
    candidates = [item for row in read_candidate_rows(args.candidates_csv) for item in expand_candidate_dates(row)]
    if args.limit:
        candidates = candidates[: args.limit]
    if not candidates:
        raise SystemExit("No candidate rows found.")

    args.raw_log.parent.mkdir(parents=True, exist_ok=True)
    limiter = GehiRateLimiter(min_interval_s=args.request_interval)
    manifest_rows: list[dict[str, object]] = []
    with args.raw_log.open("w", encoding="utf-8") as log_fh:
        def _log(payload: Mapping[str, object]) -> None:
            log_fh.write(json.dumps(payload, ensure_ascii=False) + "\n")

        for row in candidates:
            anchor_id = row["anchor_id"]
            if anchor_id not in anchors:
                raise SystemExit(f"Candidate anchor {anchor_id!r} not found in anchors CSV.")
            anchor = anchors[anchor_id]
            outcome = download_chip_with_zoom_ladder(
                anchor,
                capture_date=row["capture_date"],
                version=row.get("version", ""),
                zoom_ladder=zoom_ladder,
                output_root=args.output_dir,
                provider=args.provider,
                gehi_exe=args.gehi_exe,
                parallel=args.parallel,
                no_cache=args.no_cache,
                timeout=args.timeout,
                overwrite=args.overwrite,
                min_cache_zoom=args.min_cache_zoom,
                target_sr=args.target_sr,
                allow_nearest=args.allow_nearest,
                limiter=limiter,
                max_attempts=args.max_attempts,
                raw_log_callback=_log,
                recompress=not args.no_recompress,
            )
            provenance = build_chip_provenance(outcome, anchor, args.provider)
            artifact_id = hashlib.sha1(
                f"{anchor_id}|{outcome.actual_zoom or ''}|{row['capture_date']}|{row.get('version', '')}".encode("utf-8")
            ).hexdigest()[:16]
            manifest_rows.append(
                {
                    "artifact_id": artifact_id,
                    "anchor_id": anchor_id,
                    "region_key": anchor.get("region_key", row.get("region_key", "")),
                    "grid_id": anchor.get("grid_id", row.get("grid_id", "")),
                    "provider": args.provider,
                    "zoom": outcome.requested_zoom_ladder[0] if outcome.requested_zoom_ladder else "",
                    "actual_zoom": outcome.actual_zoom if outcome.actual_zoom is not None else "",
                    "requested_zoom_ladder": ",".join(str(z) for z in outcome.requested_zoom_ladder),
                    "capture_date": row["capture_date"],
                    "version": row.get("version", ""),
                    "path": str(outcome.path) if outcome.path else "",
                    "sha256": outcome.sha256,
                    "status": outcome.status if outcome.status != "all_zooms_failed" else f"all_zooms_failed: {outcome.error or ''}",
                    "exact_date": int(not args.allow_nearest),
                    "download_stdout_sha256": outcome.download_stdout_sha256,
                    "gehi_command": outcome.gehi_command,
                    "raster_width_px": provenance["raster_width_px"],
                    "raster_height_px": provenance["raster_height_px"],
                    "raster_crs": provenance["raster_crs"],
                    "extent_minx": provenance["extent_minx"],
                    "extent_miny": provenance["extent_miny"],
                    "extent_maxx": provenance["extent_maxx"],
                    "extent_maxy": provenance["extent_maxy"],
                    "gsd_x_m": provenance["gsd_x_m"],
                    "gsd_y_m": provenance["gsd_y_m"],
                    "raster_error": provenance["raster_error"],
                }
            )

    write_csv_rows(args.manifest, manifest_rows, FIELDS)
    print(f"Wrote {len(manifest_rows)} GEHI image artifact rows -> {args.manifest}")
    print(f"Wrote raw GEHI download log -> {args.raw_log}")

    failed_rows = [r for r in manifest_rows if str(r.get("status", "")).startswith("all_zooms_failed")]
    if failed_rows and not args.allow_failures:
        sample = failed_rows[0]
        raise SystemExit(
            f"FAIL: {len(failed_rows)}/{len(manifest_rows)} candidates failed at every ladder zoom. "
            f"Sample: anchor={sample.get('anchor_id')} date={sample.get('capture_date')} status={sample.get('status')}. "
            f"Pass --allow-failures to exit 0 anyway."
        )


if __name__ == "__main__":
    main()
