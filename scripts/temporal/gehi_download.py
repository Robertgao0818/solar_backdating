#!/usr/bin/env python3
"""Download exact-date GEHistoricalImagery GeoTIFF chips for anchor/date rows."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from pathlib import Path
from typing import Mapping

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
    "capture_date",
    "version",
    "path",
    "sha256",
    "status",
    "exact_date",
    "download_stdout_sha256",
    "gehi_command",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--anchors-csv", type=Path, default=DEFAULT_ANCHORS)
    parser.add_argument("--candidates-csv", type=Path, default=DEFAULT_CANDIDATES)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--raw-log", type=Path, default=DEFAULT_RAW_LOG)
    parser.add_argument("--gehi-exe", type=Path, default=DEFAULT_GEHI_EXE)
    parser.add_argument("--zoom", type=int, default=DEFAULT_PROBE_ZOOM)
    parser.add_argument("--provider", default=DEFAULT_PROVIDER)
    parser.add_argument("--parallel", type=int, default=4)
    parser.add_argument("--target-sr", default="", help="Optional EPSG:#### or WKT path passed to GEHI.")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--no-cache", action="store_true")
    parser.add_argument("--timeout", type=float, default=600.0)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--allow-nearest", action="store_true", help="Do not pass GEHI --exact-date.")
    return parser.parse_args()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def load_anchor_index(path: Path) -> dict[str, Mapping[str, str]]:
    return {row["anchor_id"]: row for row in read_csv_rows(path)}


def read_candidate_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8") as fh:
        return [dict(row) for row in csv.DictReader(fh) if row.get("anchor_id") and row.get("capture_date")]


def expand_candidate_dates(row: Mapping[str, str]) -> list[dict[str, str]]:
    """Return concrete download rows from a candidate row.

    `gehi_info.py` dedupes catalog rows by `(anchor_id, version)` but preserves
    all GEHI date labels. Downloading is still date-specific, especially for
    smoke regression rows such as 2015-08-30.
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


def main() -> None:
    args = parse_args()
    if not args.anchors_csv.exists():
        raise SystemExit(f"Anchor CSV not found: {args.anchors_csv}")
    if not args.candidates_csv.exists():
        raise SystemExit(f"Candidates CSV not found: {args.candidates_csv}")

    anchors = load_anchor_index(args.anchors_csv)
    candidates = [item for row in read_candidate_rows(args.candidates_csv) for item in expand_candidate_dates(row)]
    if args.limit:
        candidates = candidates[: args.limit]
    if not candidates:
        raise SystemExit("No candidate rows found.")

    args.raw_log.parent.mkdir(parents=True, exist_ok=True)
    manifest_rows: list[dict[str, object]] = []
    with args.raw_log.open("w", encoding="utf-8") as log_fh:
        for row in candidates:
            anchor_id = row["anchor_id"]
            if anchor_id not in anchors:
                raise SystemExit(f"Candidate anchor {anchor_id!r} not found in anchors CSV.")
            anchor = anchors[anchor_id]
            out_path = artifact_path(args.output_dir, row, zoom=args.zoom)
            status = "skipped_existing"
            result = None
            if args.overwrite or not out_path.exists():
                out_path.parent.mkdir(parents=True, exist_ok=True)
                lower_left, upper_right = anchor_bbox_args(anchor)
                cmd_args: list[object] = [
                    "download",
                    "--lower-left",
                    lower_left,
                    "--upper-right",
                    upper_right,
                    "--zoom",
                    args.zoom,
                    "--date",
                    iso_to_gehi_date(row["capture_date"]),
                    "--output",
                    out_path,
                    "--parallel",
                    args.parallel,
                    "--provider",
                    args.provider,
                ]
                if not args.allow_nearest:
                    cmd_args.append("--exact-date")
                if args.target_sr:
                    cmd_args.extend(["--target-sr", args.target_sr])
                if args.no_cache:
                    cmd_args.append("--no-cache")
                result = run_gehi(cmd_args, executable=args.gehi_exe, timeout=args.timeout)
                assert_gehi_success(result)
                status = "ok" if out_path.exists() else "missing_output"
                log_fh.write(
                    json.dumps(
                        {
                            "anchor_id": anchor_id,
                            "capture_date": row["capture_date"],
                            "version": row.get("version", ""),
                            "returncode": result.returncode,
                            "command": result.command,
                            "stdout_sha256": result.stdout_sha256,
                            "stderr_sha256": result.stderr_sha256,
                            "stdout": result.stdout,
                            "stderr": result.stderr,
                            "path": str(out_path),
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
            file_hash = sha256_file(out_path) if out_path.exists() else ""
            artifact_id = hashlib.sha1(f"{anchor_id}|{args.zoom}|{row['capture_date']}|{row.get('version', '')}".encode("utf-8")).hexdigest()[:16]
            manifest_rows.append(
                {
                    "artifact_id": artifact_id,
                    "anchor_id": anchor_id,
                    "region_key": anchor.get("region_key", row.get("region_key", "")),
                    "grid_id": anchor.get("grid_id", row.get("grid_id", "")),
                    "provider": args.provider,
                    "zoom": args.zoom,
                    "capture_date": row["capture_date"],
                    "version": row.get("version", ""),
                    "path": str(out_path),
                    "sha256": file_hash,
                    "status": status,
                    "exact_date": int(not args.allow_nearest),
                    "download_stdout_sha256": result.stdout_sha256 if result else "",
                    "gehi_command": result.command if result else "",
                }
            )

    write_csv_rows(args.manifest, manifest_rows, FIELDS)
    print(f"Wrote {len(manifest_rows)} GEHI image artifact rows -> {args.manifest}")
    print(f"Wrote raw GEHI download log -> {args.raw_log}")


if __name__ == "__main__":
    main()
