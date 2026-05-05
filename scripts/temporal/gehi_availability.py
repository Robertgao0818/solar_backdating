#!/usr/bin/env python3
"""Run GEHistoricalImagery availability for anchor chip bboxes."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.temporal.gehi_common import (
    DEFAULT_GEHI_EXE,
    DEFAULT_PROBE_ZOOM,
    DEFAULT_PROVIDER,
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
        for anchor in anchors:
            anchor_id = str(anchor["anchor_id"])
            lower_left, upper_right = anchor_bbox_args(anchor)
            cmd_args: list[object] = [
                "availability",
                "--lower-left",
                lower_left,
                "--upper-right",
                upper_right,
                "--zoom",
                args.zoom,
                "--min-date",
                iso_to_gehi_date(args.min_date),
                "--max-date",
                iso_to_gehi_date(args.max_date),
                "--parallel",
                args.parallel,
                "--provider",
                args.provider,
            ]
            if not args.allow_partial:
                cmd_args.append("--complete")
            if args.no_cache:
                cmd_args.append("--no-cache")
            result = run_gehi(cmd_args, executable=args.gehi_exe, timeout=args.timeout)
            # GEHI v0.5.1 availability prints useful rows, then tries to enter
            # an interactive chooser and exits non-zero under subprocess pipes.
            assert_gehi_success(result, allow_availability_chooser_exit=True)
            dates = parse_availability_output(result.stdout)
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
            for capture_date in dates:
                rows.append(
                    {
                        "anchor_id": anchor_id,
                        "region_key": anchor.get("region_key", ""),
                        "grid_id": anchor.get("grid_id", ""),
                        "provider": args.provider,
                        "zoom": args.zoom,
                        "complete_coverage": int(not args.allow_partial),
                        "capture_date": capture_date,
                        "availability_stdout_sha256": result.stdout_sha256,
                        "gehi_command": result.command,
                    }
                )

    if not rows:
        raise SystemExit("No GEHI availability dates parsed.")
    write_csv_rows(args.output, rows, FIELDS)
    print(f"Wrote {len(rows)} GEHI availability rows -> {args.output}")
    print(f"Wrote raw GEHI availability log -> {args.raw_log}")


if __name__ == "__main__":
    main()

