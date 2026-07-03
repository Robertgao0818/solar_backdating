#!/usr/bin/env python3
"""Probe GEHistoricalImagery Time Machine vintages at anchor centroids."""

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
    anchor_location_arg,
    assert_gehi_success,
    dedupe_info_rows_by_version,
    parse_info_output,
    run_gehi,
)
from scripts.temporal.geid_temporal_common import read_csv_rows, write_csv_rows

DEFAULT_ANCHORS = PROJECT_ROOT / "data" / "geid_temporal" / "anchors.csv"
DEFAULT_OUTPUT = PROJECT_ROOT / "data" / "geid_temporal" / "gehi_vintage_candidates.csv"
DEFAULT_RAW_LOG = PROJECT_ROOT / "data" / "geid_temporal" / "gehi_info_raw.jsonl"

FIELDS = [
    "anchor_id",
    "region_key",
    "grid_id",
    "provider",
    "zoom",
    "path",
    "capture_date",
    "version",
    "capture_date_min",
    "capture_date_max",
    "all_capture_dates",
    "n_date_labels",
    "version_dedupe_key",
    "all_versions",
    "n_versions_at_date",
    "info_stdout_sha256",
    "gehi_command",
]


def fetch_vintages_for_anchor(
    anchor: Mapping[str, object],
    *,
    zoom: int = DEFAULT_PROBE_ZOOM,
    provider: str = DEFAULT_PROVIDER,
    gehi_exe: Path = DEFAULT_GEHI_EXE,
    no_cache: bool = False,
    timeout: float = 300.0,
    runner: Callable[..., GehiRunResult] = run_gehi,
    raw_log_callback: Callable[[str, GehiRunResult], None] | None = None,
    catalog_cache: CatalogCache | None = None,
    force_refresh: bool = False,
    max_age_days: float = DEFAULT_CATALOG_MAX_AGE_DAYS,
) -> list[dict[str, object]]:
    """Return version-deduped GEHI vintage rows for a single anchor.

    Library API for orchestrators that do not want to drive the full CSV-based
    CLI. `runner` is overridable for tests; in production use the default
    `run_gehi` subprocess wrapper.

    `catalog_cache=None` (the default) is byte-identical to the pre-ISSUE-13
    behavior. With a `catalog_cache`, the raw (un-stamped) `parse_info_output`
    rows are the cached payload — cache entries are anchor-agnostic (keyed on
    provider/zoom/location only), so `anchor_id`/`region_key`/`grid_id`/
    `gehi_command`/`info_stdout_sha256` are (re-)stamped onto the payload and
    deduped fresh on every call, whether served from a hit, a live fetch, or a
    stale degrade. See `gehi_availability.fetch_availability_for_anchor` for
    the shared failure-degradation contract (fresh hit skips the live call;
    miss/force_refresh calls live; on live failure, degrade to a stale entry
    if one exists, else match the `None`-path behavior).
    """
    anchor_id = str(anchor["anchor_id"])
    cmd_args: list[object] = [
        "info",
        "--location",
        anchor_location_arg(anchor),
        "--zoom",
        zoom,
        "--provider",
        provider,
    ]
    if no_cache:
        cmd_args.append("--no-cache")

    def _stamp_and_dedupe(
        raw_rows: list[dict[str, object]], stdout_sha256: str, gehi_command: str
    ) -> list[dict[str, object]]:
        rows: list[dict[str, object]] = []
        for item in raw_rows:
            stamped = dict(item)
            stamped.update(
                {
                    "anchor_id": anchor_id,
                    "region_key": anchor.get("region_key", ""),
                    "grid_id": anchor.get("grid_id", ""),
                    "provider": provider,
                    "info_stdout_sha256": stdout_sha256,
                    "gehi_command": gehi_command,
                }
            )
            rows.append(stamped)
        return dedupe_info_rows_by_version(rows)

    if catalog_cache is None:
        try:
            result = runner(cmd_args, executable=gehi_exe, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            print(
                f"[gehi_info] info timed out for anchor {anchor_id} after {timeout}s; "
                f"skipping ({type(exc).__name__})",
                file=sys.stderr,
            )
            return []
        except Exception as exc:  # noqa: BLE001 - mirror download resilience, keep loop alive
            print(
                f"[gehi_info] info failed for anchor {anchor_id}: "
                f"{type(exc).__name__}: {exc}; skipping",
                file=sys.stderr,
            )
            return []
        assert_gehi_success(result)
        if raw_log_callback is not None:
            raw_log_callback(anchor_id, result)
        raw_rows = parse_info_output(result.stdout)
        return _stamp_and_dedupe(raw_rows, result.stdout_sha256, result.command)

    cache_key = CatalogCacheKey(
        kind="info",
        provider=provider,
        zoom=int(zoom),
        lower_left=anchor_location_arg(anchor),
    )

    def _live() -> tuple[list[dict[str, object]], str, str]:
        result = runner(cmd_args, executable=gehi_exe, timeout=timeout)
        assert_gehi_success(result)
        if raw_log_callback is not None:
            raw_log_callback(anchor_id, result)
        raw_rows = parse_info_output(result.stdout)
        return raw_rows, result.stdout_sha256, result.command

    try:
        raw_rows = get_or_fetch(
            catalog_cache,
            cache_key,
            force_refresh=force_refresh,
            max_age_days=max_age_days,
            anchor_id=anchor_id,
            region_key=str(anchor.get("region_key", "")),
            grid_id=str(anchor.get("grid_id", "")),
            live_fn=_live,
            label="gehi_info",
        )
    except subprocess.TimeoutExpired as exc:
        print(
            f"[gehi_info] info timed out for anchor {anchor_id} after {timeout}s; "
            f"skipping ({type(exc).__name__})",
            file=sys.stderr,
        )
        return []
    except RuntimeError:
        raise
    except Exception as exc:  # noqa: BLE001 - mirror download resilience, keep loop alive
        print(
            f"[gehi_info] info failed for anchor {anchor_id}: "
            f"{type(exc).__name__}: {exc}; skipping",
            file=sys.stderr,
        )
        return []
    cached_entry = catalog_cache.get_any(cache_key)
    stdout_sha256 = cached_entry.stdout_sha256 if cached_entry is not None else ""
    gehi_command = cached_entry.gehi_command if cached_entry is not None else ""
    return _stamp_and_dedupe(raw_rows, stdout_sha256, gehi_command)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--anchors-csv", type=Path, default=DEFAULT_ANCHORS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--raw-log", type=Path, default=DEFAULT_RAW_LOG)
    parser.add_argument("--gehi-exe", type=Path, default=DEFAULT_GEHI_EXE)
    parser.add_argument("--zoom", type=int, default=DEFAULT_PROBE_ZOOM)
    parser.add_argument("--provider", default=DEFAULT_PROVIDER)
    parser.add_argument("--limit-anchors", type=int)
    parser.add_argument("--no-cache", action="store_true")
    parser.add_argument("--timeout", type=float, default=300.0)
    add_catalog_cache_cli_args(parser)
    return parser.parse_args()


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
        def _log(anchor_id: str, result: GehiRunResult) -> None:
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
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )

        for anchor in anchors:
            rows.extend(
                fetch_vintages_for_anchor(
                    anchor,
                    zoom=args.zoom,
                    provider=args.provider,
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
        raise SystemExit("No GEHI vintage rows parsed.")
    write_csv_rows(args.output, rows, FIELDS)
    print(f"Wrote {len(rows)} version-deduped GEHI vintage candidates -> {args.output}")
    print(f"Wrote raw GEHI info log -> {args.raw_log}")
    if catalog_cache is not None:
        print(catalog_cache.stats.summary())


if __name__ == "__main__":
    main()

