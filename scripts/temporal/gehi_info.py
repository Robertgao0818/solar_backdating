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
) -> list[dict[str, object]]:
    """Return version-deduped GEHI vintage rows for a single anchor.

    Library API for orchestrators that do not want to drive the full CSV-based
    CLI. `runner` is overridable for tests; in production use the default
    `run_gehi` subprocess wrapper.
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
    rows: list[dict[str, object]] = []
    for item in parse_info_output(result.stdout):
        item.update(
            {
                "anchor_id": anchor_id,
                "region_key": anchor.get("region_key", ""),
                "grid_id": anchor.get("grid_id", ""),
                "provider": provider,
                "info_stdout_sha256": result.stdout_sha256,
                "gehi_command": result.command,
            }
        )
        rows.append(item)
    return dedupe_info_rows_by_version(rows)


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
                )
            )

    if not rows:
        raise SystemExit("No GEHI vintage rows parsed.")
    write_csv_rows(args.output, rows, FIELDS)
    print(f"Wrote {len(rows)} version-deduped GEHI vintage candidates -> {args.output}")
    print(f"Wrote raw GEHI info log -> {args.raw_log}")


if __name__ == "__main__":
    main()

