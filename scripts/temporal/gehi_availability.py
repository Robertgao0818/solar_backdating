#!/usr/bin/env python3
"""Run GEHistoricalImagery availability for anchor chip bboxes."""

from __future__ import annotations

import argparse
import json
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
) -> list[dict[str, object]]:
    """Return GEHI availability rows for one anchor chip bbox.

    Unlike `gehi_info`, this probes the full chip bbox. With `complete=True`, a
    returned date means GEHI reports complete coverage for the requested region
    at the requested zoom, making it suitable as a download gate.
    """
    anchor_id = str(anchor["anchor_id"])
    lower_left, upper_right = anchor_bbox_args(anchor)
    cmd_args: list[object] = [
        "availability",
        "--lower-left",
        lower_left,
        "--upper-right",
        upper_right,
        "--zoom",
        zoom,
        "--min-date",
        iso_to_gehi_date(min_date),
        "--max-date",
        iso_to_gehi_date(max_date),
        "--parallel",
        parallel,
        "--provider",
        provider,
    ]
    if complete:
        cmd_args.append("--complete")
    if no_cache:
        cmd_args.append("--no-cache")
    result = runner(cmd_args, executable=gehi_exe, timeout=timeout)
    # GEHI v0.5.1 availability prints useful rows, then tries to enter an
    # interactive chooser and exits non-zero under subprocess pipes.
    assert_gehi_success(result, allow_availability_chooser_exit=True)
    dates = parse_availability_output(result.stdout)
    if raw_log_callback is not None:
        raw_log_callback(anchor_id, result, dates)
    return [
        {
            "anchor_id": anchor_id,
            "region_key": anchor.get("region_key", ""),
            "grid_id": anchor.get("grid_id", ""),
            "provider": provider,
            "zoom": zoom,
            "complete_coverage": int(complete),
            "capture_date": capture_date,
            "availability_stdout_sha256": result.stdout_sha256,
            "gehi_command": result.command,
        }
        for capture_date in dates
    ]


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
                )
            )

    if not rows:
        raise SystemExit("No GEHI availability dates parsed.")
    write_csv_rows(args.output, rows, FIELDS)
    print(f"Wrote {len(rows)} GEHI availability rows -> {args.output}")
    print(f"Wrote raw GEHI availability log -> {args.raw_log}")


if __name__ == "__main__":
    main()
