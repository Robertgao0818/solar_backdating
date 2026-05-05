#!/usr/bin/env python3
"""Probe GEHistoricalImagery Time Machine vintages at anchor centroids."""

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
    "info_stdout_sha256",
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
        for anchor in anchors:
            anchor_id = str(anchor["anchor_id"])
            cmd_args: list[object] = [
                "info",
                "--location",
                anchor_location_arg(anchor),
                "--zoom",
                args.zoom,
                "--provider",
                args.provider,
            ]
            if args.no_cache:
                cmd_args.append("--no-cache")
            result = run_gehi(cmd_args, executable=args.gehi_exe, timeout=args.timeout)
            assert_gehi_success(result)
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
            for item in parse_info_output(result.stdout):
                item.update(
                    {
                        "anchor_id": anchor_id,
                        "region_key": anchor.get("region_key", ""),
                        "grid_id": anchor.get("grid_id", ""),
                        "provider": args.provider,
                        "info_stdout_sha256": result.stdout_sha256,
                        "gehi_command": result.command,
                    }
                )
                rows.append(item)

    if not rows:
        raise SystemExit("No GEHI vintage rows parsed.")
    rows = dedupe_info_rows_by_version(rows)
    write_csv_rows(args.output, rows, FIELDS)
    print(f"Wrote {len(rows)} version-deduped GEHI vintage candidates -> {args.output}")
    print(f"Wrote raw GEHI info log -> {args.raw_log}")


if __name__ == "__main__":
    main()

