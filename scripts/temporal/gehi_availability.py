#!/usr/bin/env python3
"""Run GEHistoricalImagery availability for anchor chip bboxes."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Callable, Mapping

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.temporal.gehi_catalog_cache import (
    DB_FILENAME,
    CatalogCache,
    CatalogCacheKey,
    DEFAULT_CATALOG_MAX_AGE_DAYS,
    add_catalog_cache_cli_args,
    get_or_fetch,
)
from scripts.temporal.gehi_common import (
    DEFAULT_GEHI_EXE,
    DEFAULT_PROBE_ZOOM,
    DEFAULT_PROVIDER,
    GehiRunResult,
    anchor_bbox_args,
    assert_gehi_success,
    iso_to_gehi_date,
    parse_availability_output,
    run_gehi,
)
from scripts.temporal.geid_temporal_common import read_csv_rows, write_csv_rows

DEFAULT_ANCHORS = PROJECT_ROOT / "data" / "geid_temporal" / "anchors.csv"
DEFAULT_OUTPUT = PROJECT_ROOT / "data" / "geid_temporal" / "gehi_availability.csv"
DEFAULT_RAW_LOG = PROJECT_ROOT / "data" / "geid_temporal" / "gehi_availability_raw.jsonl"

FIELDS = [
    "anchor_id",
    "region_key",
    "grid_id",
    "provider",
    "zoom",
    "complete_coverage",
    "capture_date",
    "availability_stdout_sha256",
    "gehi_command",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--anchors-csv", type=Path, default=DEFAULT_ANCHORS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--raw-log", type=Path, default=DEFAULT_RAW_LOG)
    parser.add_argument("--gehi-exe", type=Path, default=DEFAULT_GEHI_EXE)
    parser.add_argument("--zoom", type=int, default=DEFAULT_PROBE_ZOOM)
    parser.add_argument("--provider", default=DEFAULT_PROVIDER)
    parser.add_argument("--min-date", default="2009-01-01", help="Oldest capture date, YYYY-MM-DD.")
    parser.add_argument("--max-date", default="2025-12-31", help="Youngest capture date, YYYY-MM-DD.")
    parser.add_argument("--parallel", type=int, default=4)
    parser.add_argument("--limit-anchors", type=int)
    parser.add_argument("--allow-partial", action="store_true", help="Do not pass GEHI --complete.")
    parser.add_argument("--no-cache", action="store_true")
    parser.add_argument("--timeout", type=float, default=300.0)
    add_catalog_cache_cli_args(parser)
    return parser.parse_args()


def fetch_availability_for_anchor(
    anchor: Mapping[str, object],
    *,
    zoom: int = DEFAULT_PROBE_ZOOM,
    provider: str = DEFAULT_PROVIDER,
    min_date: str = "2009-01-01",
    max_date: str = "2025-12-31",
    parallel: int = 4,
    complete: bool = True,
    gehi_exe: Path = DEFAULT_GEHI_EXE,
    no_cache: bool = False,
    timeout: float = 300.0,
    runner: Callable[..., GehiRunResult] = run_gehi,
    raw_log_callback: Callable[[str, GehiRunResult, list[str]], None] | None = None,
    catalog_cache: CatalogCache | None = None,
    force_refresh: bool = False,
    max_age_days: float = DEFAULT_CATALOG_MAX_AGE_DAYS,
) -> list[dict[str, object]]:
    """Return GEHI availability rows for one anchor chip bbox.

    Unlike `gehi_info`, this probes the full chip bbox. With `complete=True`, a
    returned date means GEHI reports complete coverage for the requested region
    at the requested zoom, making it suitable as a download gate.

    `catalog_cache=None` (the default) is byte-identical to the pre-ISSUE-13
    behavior: always issue the live GEHI subprocess call, and on
    `TimeoutExpired`/any other `Exception` warn to stderr and degrade to `[]`
    (a bare `assert_gehi_success` failure — GEHI exiting non-zero for a reason
    other than the availability chooser — still propagates as `RuntimeError`,
    unchanged).

    With a `catalog_cache`, a fresh cache hit skips the live call entirely.
    On a miss (or `force_refresh=True`) the live call still runs; if it then
    fails (timeout, `RuntimeError`, or any other exception) and a last-good
    cache entry exists, that stale entry is served instead (with a stderr
    warning) rather than degrading to `[]`/raising. Only when there is no
    cache entry to fall back on does failure behavior match the `None` path:
    `TimeoutExpired`/generic `Exception` -> warn + `[]`; `RuntimeError` (GEHI
    non-zero exit) -> re-raise.
    """
    anchor_id = str(anchor["anchor_id"])
    lower_left, upper_right = anchor_bbox_args(anchor)
    gehi_min_date = iso_to_gehi_date(min_date)
    gehi_max_date = iso_to_gehi_date(max_date)
    cmd_args: list[object] = [
        "availability",
        "--lower-left",
        lower_left,
        "--upper-right",
        upper_right,
        "--zoom",
        zoom,
        "--min-date",
        gehi_min_date,
        "--max-date",
        gehi_max_date,
        "--parallel",
        parallel,
        "--provider",
        provider,
    ]
    if complete:
        cmd_args.append("--complete")
    if no_cache:
        cmd_args.append("--no-cache")

    def _build_rows(dates: list[str], stdout_sha256: str, gehi_command: str) -> list[dict[str, object]]:
        return [
            {
                "anchor_id": anchor_id,
                "region_key": anchor.get("region_key", ""),
                "grid_id": anchor.get("grid_id", ""),
                "provider": provider,
                "zoom": zoom,
                "complete_coverage": int(complete),
                "capture_date": capture_date,
                "availability_stdout_sha256": stdout_sha256,
                "gehi_command": gehi_command,
            }
            for capture_date in dates
        ]

    if catalog_cache is None:
        try:
            result = runner(cmd_args, executable=gehi_exe, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            # A hung anchor must not kill the whole run: degrade to no
            # availability rows and let the caller continue with the next anchor.
            print(
                f"[gehi_availability] anchor {anchor_id} availability timed out "
                f"after {timeout}s: {exc}",
                file=sys.stderr,
            )
            return []
        except Exception as exc:  # noqa: BLE001 - resilience: never abort the batch
            print(
                f"[gehi_availability] anchor {anchor_id} availability failed: "
                f"{type(exc).__name__}: {exc}",
                file=sys.stderr,
            )
            return []
        # GEHI v0.5.1 availability prints useful rows, then tries to enter an
        # interactive chooser and exits non-zero under subprocess pipes.
        assert_gehi_success(result, allow_availability_chooser_exit=True)
        dates = parse_availability_output(result.stdout)
        if raw_log_callback is not None:
            raw_log_callback(anchor_id, result, dates)
        return _build_rows(dates, result.stdout_sha256, result.command)

    cache_key = CatalogCacheKey(
        kind="availability",
        provider=provider,
        zoom=int(zoom),
        lower_left=lower_left,
        upper_right=upper_right,
        min_date=gehi_min_date,
        max_date=gehi_max_date,
        complete=bool(complete),
    )

    def _live() -> tuple[list[str], str, str]:
        result = runner(cmd_args, executable=gehi_exe, timeout=timeout)
        # GEHI v0.5.1 availability prints useful rows, then tries to enter an
        # interactive chooser and exits non-zero under subprocess pipes.
        assert_gehi_success(result, allow_availability_chooser_exit=True)
        dates = parse_availability_output(result.stdout)
        if raw_log_callback is not None:
            raw_log_callback(anchor_id, result, dates)
        return dates, result.stdout_sha256, result.command

    try:
        dates = get_or_fetch(
            catalog_cache,
            cache_key,
            force_refresh=force_refresh,
            max_age_days=max_age_days,
            anchor_id=anchor_id,
            region_key=str(anchor.get("region_key", "")),
            grid_id=str(anchor.get("grid_id", "")),
            live_fn=_live,
            label="gehi_availability",
        )
    except subprocess.TimeoutExpired as exc:
        print(
            f"[gehi_availability] anchor {anchor_id} availability timed out "
            f"after {timeout}s: {exc}",
            file=sys.stderr,
        )
        return []
    except RuntimeError:
        raise
    except Exception as exc:  # noqa: BLE001 - resilience: never abort the batch
        print(
            f"[gehi_availability] anchor {anchor_id} availability failed: "
            f"{type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return []
    # Cache entries carry only stdout_sha256/gehi_command metadata for the
    # *original* fetch, which is still a factually correct description of the
    # underlying GEHI call, so serving it unchanged to a different querying
    # anchor sharing the same bbox/zoom/date-range key is fine.
    cached_entry = catalog_cache.get_any(cache_key)
    stdout_sha256 = cached_entry.stdout_sha256 if cached_entry is not None else ""
    gehi_command = cached_entry.gehi_command if cached_entry is not None else ""
    return _build_rows(dates, stdout_sha256, gehi_command)


def main() -> None:
    args = parse_args()
    if not args.anchors_csv.exists():
        raise SystemExit(f"Anchor CSV not found: {args.anchors_csv}")
    anchors = read_csv_rows(args.anchors_csv)
    if args.limit_anchors:
        anchors = anchors[: args.limit_anchors]
    if not anchors:
        raise SystemExit("No anchors found.")

    catalog_cache: CatalogCache | None = None
    if not args.no_catalog_cache:
        db_path = (args.catalog_cache_dir / DB_FILENAME) if args.catalog_cache_dir else None
        catalog_cache = CatalogCache(db_path)

    args.raw_log.parent.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, object]] = []
    with args.raw_log.open("w", encoding="utf-8") as log_fh:
        def _log(anchor_id: str, result: GehiRunResult, dates: list[str]) -> None:
            log_fh.write(
                json.dumps(
                    {
                        "anchor_id": anchor_id,
                        "returncode": result.returncode,
                        "command": result.command,
                        "stdout_sha256": result.stdout_sha256,
                        "stderr_sha256": result.stderr_sha256,
                        "stdout": result.stdout,
                        "stderr": result.stderr,
                        "dates": dates,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )

        for anchor in anchors:
            rows.extend(
                fetch_availability_for_anchor(
                    anchor,
                    zoom=args.zoom,
                    provider=args.provider,
                    min_date=args.min_date,
                    max_date=args.max_date,
                    parallel=args.parallel,
                    complete=not args.allow_partial,
                    gehi_exe=args.gehi_exe,
                    no_cache=args.no_cache,
                    timeout=args.timeout,
                    raw_log_callback=_log,
                    catalog_cache=catalog_cache,
                    force_refresh=args.force_catalog_refresh,
                    max_age_days=args.catalog_max_age_days,
                )
            )

    if not rows:
        raise SystemExit("No GEHI availability dates parsed.")
    write_csv_rows(args.output, rows, FIELDS)
    print(f"Wrote {len(rows)} GEHI availability rows -> {args.output}")
    print(f"Wrote raw GEHI availability log -> {args.raw_log}")
    if catalog_cache is not None:
        print(catalog_cache.stats.summary())


if __name__ == "__main__":
    main()
