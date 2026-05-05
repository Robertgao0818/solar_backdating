#!/usr/bin/env python3
"""Download exact-date GEHistoricalImagery GeoTIFF chips for anchor/date rows.

Supports a zoom ladder (try each zoom in order, fall back on failure or empty
output) so the orchestrator can prefer higher-GSD captures (z=20) and gracefully
fall back to z=19 when only that level has the requested vintage. Idempotent:
if a non-empty file already exists at any ladder zoom for the anchor/date/version,
the download is skipped.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Sequence

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
]


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
    parser.add_argument("--allow-nearest", action="store_true", help="Do not pass GEHI --exact-date.")
    parser.add_argument(
        "--allow-failures",
        action="store_true",
        help="Exit 0 even if some candidates failed at every ladder zoom. Default: exit 1 on any all_zooms_failed.",
    )
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
    target_sr: str = "",
    allow_nearest: bool = False,
    runner: Callable[..., GehiRunResult] = run_gehi,
    raw_log_callback: Callable[[Mapping[str, object]], None] | None = None,
    vintage_check: Callable[[int, str], bool] | None = None,
) -> DownloadResult:
    """Download a chip at the first zoom in `zoom_ladder` that succeeds.

    Idempotent: scans the ladder for an existing non-empty chip first and
    returns it (preferring the highest-quality / earliest-in-ladder match)
    without re-running GEHI. On miss, attempts each zoom in ladder order;
    falls back to next zoom on non-zero return code, empty output, runner
    exception, or `vintage_check(zoom, capture_date) is False`. Any non-empty
    file left by a failed attempt is removed so a later run re-attempts cleanly.

    `vintage_check` is an optional provenance gate: when supplied, the ladder
    skips any zoom whose vintage catalog does not contain `capture_date`. The
    intended source is a per-anchor, per-zoom GEHI info catalog cached by the
    caller (see `make_vintage_check` in run_adaptive_scan).
    """
    if not zoom_ladder:
        raise ValueError("zoom_ladder must be non-empty")
    anchor_id = str(anchor["anchor_id"])
    version_str = str(version).strip()
    ladder = tuple(int(z) for z in zoom_ladder)

    if not overwrite:
        for zoom in ladder:
            candidate_path = _chip_path_for(output_root, anchor_id, capture_date, version_str, zoom)
            if candidate_path.exists() and candidate_path.stat().st_size > 0:
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

    last_error: str | None = None
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
                target_sr=args.target_sr,
                allow_nearest=args.allow_nearest,
                raw_log_callback=_log,
            )
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
                    "zoom": outcome.actual_zoom if outcome.actual_zoom is not None else "",
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
