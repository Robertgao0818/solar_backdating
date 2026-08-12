#!/usr/bin/env python3
"""Cape Town CT-05 multi-route GEHI catalog probe and reconciliation.

The live probe is deliberately separate from scoring.  Multiple network
routes may populate immutable raw logs and catalog observations; ``merge``
then produces one frozen, offline candidate CSV plus an explicit outcome row
for every frozen anchor.  A failed/blocked query is never interpreted as an
empty catalog.

Typical flow::

    python scripts/temporal/run_ct05_catalog_probe.py plan \
      --anchors-csv .../groups_v1/chip_groups_as_anchors.csv \
      --out-dir .../ct05_catalog_v1/plan

    # Run one command per route/shard.  IPv4 pinning is owned by the launcher:
    # DOTNET_SYSTEM_NET_DISABLEIPV6=1 python ... probe ...
    python scripts/temporal/run_ct05_catalog_probe.py probe \
      --anchors-csv .../plan/shards/home_v4.csv --route-id home_v4 \
      --out-dir .../ct05_catalog_v1/routes/home_v4

    python scripts/temporal/run_ct05_catalog_probe.py merge \
      --anchors-csv .../groups_v1/chip_groups_as_anchors.csv \
      --route-dir .../ct05_catalog_v1/routes/home_v4 \
      --route-dir .../ct05_catalog_v1/routes/home_v6 \
      --out-dir .../ct05_catalog_v1/merged

TM is probed with bbox-complete ``availability`` at z19 and z18.  Wayback is
probed with ``info`` at z19 and z18 because GEHI's Wayback catalog surface is
the layer/version source used by exact-date downloads.  Wayback completeness
is therefore finalized by the download/chip QA stage, never assumed from the
centroid info query.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Iterable, Mapping, Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.temporal.gehi_common import (  # noqa: E402
    DEFAULT_GEHI_EXE,
    GehiRateLimiter,
    GehiRunResult,
    anchor_bbox_args,
    anchor_location_arg,
    assert_gehi_success,
    is_blocked_result,
    iso_to_gehi_date,
    parse_availability_output,
    parse_info_output,
    run_gehi,
)
from scripts.temporal.geid_temporal_common import read_csv_rows, write_csv_rows  # noqa: E402

DEFAULT_ROUTES = ("home_v4", "home_v6", "koko_v4", "koko_v6")
DEFAULT_ZOOMS = (19, 18)
DEFAULT_MIN_DATE = "2009-01-01"
DEFAULT_MAX_DATE = "2025-12-31"
DEFAULT_CENSUS_DATE = "2025-01-31"
DEFAULT_POST_CENSUS_FRAMES = 3
SUCCESS_STATUSES = frozenset({"ok_nonempty", "ok_empty"})

OUTCOME_FIELDS = (
    "anchor_id",
    "route_id",
    "provider",
    "zoom",
    "query_kind",
    "status",
    "n_rows",
    "attempts",
    "started_utc",
    "finished_utc",
    "stdout_sha256",
    "stderr_sha256",
    "gehi_command",
    "error",
)

CATALOG_FIELDS = (
    "anchor_id",
    "capture_date",
    "version",
    "provider",
    "zoom",
    "query_kind",
    "route_id",
    "stdout_sha256",
    "gehi_command",
)

FINAL_OUTCOME_FIELDS = (
    "anchor_id",
    "catalog_status",
    "release_eligible",
    "tm_date_count",
    "wayback_date_count",
    "merged_date_count",
    "successful_query_count",
    "required_query_count",
    "unresolved_queries",
    "routes_observed",
)

CANDIDATE_FIELDS = (
    "anchor_id",
    "capture_date",
    "version",
    "provider",
    "requested_zoom",
    "query_kind",
    "route_id",
    "bbox_complete_at_catalog",
    "reference_only",
    "census_date",
    "cutoff_max_date",
    "catalog_stdout_sha256",
    "gehi_command",
)


@dataclass(frozen=True)
class QuerySpec:
    provider: str
    zoom: int
    query_kind: str

    @property
    def key_suffix(self) -> tuple[str, int, str]:
        return self.provider, self.zoom, self.query_kind


REQUIRED_SPECS = tuple(
    [QuerySpec("TM", zoom, "availability_complete") for zoom in DEFAULT_ZOOMS]
    + [QuerySpec("Wayback", zoom, "info") for zoom in DEFAULT_ZOOMS]
)


def utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def stable_route(anchor_id: str, routes: Sequence[str]) -> str:
    if not routes:
        raise ValueError("at least one route is required")
    digest = hashlib.sha256(anchor_id.encode("utf-8")).digest()
    slot = int.from_bytes(digest[:8], "big") % len(routes)
    return routes[slot]


def validate_anchors(rows: Sequence[Mapping[str, object]]) -> None:
    if not rows:
        raise ValueError("anchor CSV is empty")
    ids = [str(row.get("anchor_id", "")).strip() for row in rows]
    if any(not value for value in ids):
        raise ValueError("anchor CSV contains a blank anchor_id")
    if len(ids) != len(set(ids)):
        raise ValueError("anchor_id must be unique")
    bad_regions = {
        str(row.get("region_key", "")).strip()
        for row in rows
        if str(row.get("region_key", "")).strip() != "cape_town"
    }
    if bad_regions:
        raise ValueError(f"non-Cape Town anchors found: {sorted(bad_regions)}")
    bbox_fields = ("chip_lon_min", "chip_lat_min", "chip_lon_max", "chip_lat_max")
    missing = [
        str(row["anchor_id"])
        for row in rows
        if any(str(row.get(field, "")).strip() == "" for field in bbox_fields)
    ]
    if missing:
        raise ValueError(f"{len(missing)} anchors lack chip bbox fields; first={missing[0]}")


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def plan_routes(
    anchors: Sequence[Mapping[str, object]],
    routes: Sequence[str],
    out_dir: Path,
) -> dict[str, object]:
    validate_anchors(anchors)
    shards: dict[str, list[Mapping[str, object]]] = {route: [] for route in routes}
    for anchor in anchors:
        route = stable_route(str(anchor["anchor_id"]), routes)
        shards[route].append(anchor)

    shard_dir = out_dir / "shards"
    shard_dir.mkdir(parents=True, exist_ok=True)
    fieldnames = list(anchors[0].keys())
    shard_meta = []
    for route, rows in shards.items():
        path = shard_dir / f"{route}.csv"
        write_csv_rows(path, rows, fieldnames)
        shard_meta.append(
            {
                "route_id": route,
                "path": str(path),
                "anchor_count": len(rows),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        )
    manifest = {
        "schema_version": "ct05_route_plan_v1",
        "created_utc": utc_now(),
        "anchor_count": len(anchors),
        "routes": list(routes),
        "required_queries_per_anchor": [asdict(spec) for spec in REQUIRED_SPECS],
        "shards": shard_meta,
    }
    _write_json(out_dir / "route_plan.json", manifest)
    return manifest


def plan_remaining_lanes(
    anchors: Sequence[Mapping[str, object]],
    outcome_paths: Sequence[Path],
    *,
    route_id: str,
    lanes: int,
    out_dir: Path,
) -> dict[str, object]:
    """Split anchors lacking four successful queries into stable resume lanes."""
    validate_anchors(anchors)
    if lanes < 1:
        raise ValueError("lanes must be positive")
    successful: set[tuple[str, str, int, str]] = set()
    for path in outcome_paths:
        for row in read_jsonl(path):
            if str(row.get("status")) not in SUCCESS_STATUSES:
                continue
            successful.add(
                (
                    str(row.get("anchor_id", "")),
                    str(row.get("provider", "")),
                    int(row.get("zoom", 0)),
                    str(row.get("query_kind", "")),
                )
            )
    completed_ids = {
        str(anchor["anchor_id"])
        for anchor in anchors
        if all(
            (str(anchor["anchor_id"]), *spec.key_suffix) in successful
            for spec in REQUIRED_SPECS
        )
    }
    remaining = [
        anchor for anchor in anchors if str(anchor["anchor_id"]) not in completed_ids
    ]
    lane_rows: list[list[Mapping[str, object]]] = [[] for _ in range(lanes)]
    for anchor in remaining:
        digest = hashlib.sha256(str(anchor["anchor_id"]).encode("utf-8")).digest()
        lane_rows[int.from_bytes(digest[8:16], "big") % lanes].append(anchor)

    out_dir.mkdir(parents=True, exist_ok=True)
    fieldnames = list(anchors[0].keys())
    records = []
    for index, rows in enumerate(lane_rows, 1):
        lane_id = f"{route_id}_lane{index:02d}"
        path = out_dir / f"{lane_id}.csv"
        write_csv_rows(path, rows, fieldnames)
        records.append(
            {
                "lane_id": lane_id,
                "anchor_count": len(rows),
                "path": str(path),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        )
    manifest = {
        "schema_version": "ct05_remaining_lane_plan_v1",
        "created_utc": utc_now(),
        "route_id": route_id,
        "source_anchor_count": len(anchors),
        "fully_completed_anchor_count": len(completed_ids),
        "remaining_anchor_count": len(remaining),
        "successful_query_key_count": len(successful),
        "lanes": lanes,
        "lane_shards": records,
        "outcome_paths": [str(path) for path in outcome_paths],
    }
    _write_json(out_dir / f"{route_id}_remaining_plan.json", manifest)
    return manifest


class JsonlAppender:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = path.open("a", encoding="utf-8")

    def write(self, payload: Mapping[str, object]) -> None:
        self._fh.write(json.dumps(dict(payload), ensure_ascii=False, sort_keys=True) + "\n")
        self._fh.flush()

    def close(self) -> None:
        self._fh.close()

    def __enter__(self) -> "JsonlAppender":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


def read_jsonl(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    rows: list[dict[str, object]] = []
    with path.open(encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSONL at {path}:{lineno}: {exc}") from exc
            if not isinstance(item, dict):
                raise ValueError(f"non-object JSONL row at {path}:{lineno}")
            rows.append(item)
    return rows


def completed_query_keys(outcome_path: Path) -> set[tuple[str, str, int, str]]:
    completed: set[tuple[str, str, int, str]] = set()
    for row in read_jsonl(outcome_path):
        if str(row.get("status")) not in SUCCESS_STATUSES:
            continue
        completed.add(
            (
                str(row.get("anchor_id", "")),
                str(row.get("provider", "")),
                int(row.get("zoom", 0)),
                str(row.get("query_kind", "")),
            )
        )
    return completed


def build_query_args(
    anchor: Mapping[str, object],
    spec: QuerySpec,
    *,
    min_date: str,
    max_date: str,
    parallel: int,
    no_cache: bool,
) -> list[object]:
    if spec.query_kind == "availability_complete":
        lower_left, upper_right = anchor_bbox_args(anchor)
        args: list[object] = [
            "availability",
            "--lower-left",
            lower_left,
            "--upper-right",
            upper_right,
            "--zoom",
            spec.zoom,
            "--min-date",
            iso_to_gehi_date(min_date),
            "--max-date",
            iso_to_gehi_date(max_date),
            "--parallel",
            parallel,
            "--provider",
            spec.provider,
            "--complete",
        ]
    elif spec.query_kind == "info":
        args = [
            "info",
            "--location",
            anchor_location_arg(anchor),
            "--zoom",
            spec.zoom,
            "--provider",
            spec.provider,
        ]
    else:
        raise ValueError(f"unsupported query kind: {spec.query_kind}")
    if no_cache:
        args.append("--no-cache")
    return args


def parse_query_rows(
    anchor_id: str,
    spec: QuerySpec,
    result: GehiRunResult,
    route_id: str,
    *,
    min_date: str,
    max_date: str,
) -> list[dict[str, object]]:
    if spec.query_kind == "availability_complete":
        assert_gehi_success(result, allow_availability_chooser_exit=True)
        dates = parse_availability_output(result.stdout)
        version_by_date = {date[:10]: "" for date in dates}
    else:
        assert_gehi_success(result)
        # Deduplicate by capture date, not GEHI internal version.  Multiple
        # date labels can share one version and remain useful observations.
        version_by_date: dict[str, str] = {}
        for raw in parse_info_output(result.stdout):
            date = str(raw.get("capture_date", ""))[:10]
            if not date or date in version_by_date:
                continue
            version_by_date[date] = str(raw.get("version", "")).strip()
    rows = []
    for capture_date, version in sorted(version_by_date.items()):
        if not (min_date <= capture_date <= max_date):
            continue
        rows.append(
            {
                "anchor_id": anchor_id,
                "capture_date": capture_date,
                "version": version,
                "provider": spec.provider,
                "zoom": spec.zoom,
                "query_kind": spec.query_kind,
                "route_id": route_id,
                "stdout_sha256": result.stdout_sha256,
                "gehi_command": result.command,
            }
        )
    return rows


def execute_query(
    anchor: Mapping[str, object],
    spec: QuerySpec,
    *,
    route_id: str,
    raw_log: JsonlAppender,
    limiter: GehiRateLimiter,
    gehi_exe: Path,
    min_date: str,
    max_date: str,
    parallel: int,
    no_cache: bool,
    timeout: float,
    max_attempts: int,
    runner: Callable[..., GehiRunResult] = run_gehi,
) -> tuple[dict[str, object], list[dict[str, object]]]:
    anchor_id = str(anchor["anchor_id"])
    started = utc_now()
    args = build_query_args(
        anchor,
        spec,
        min_date=min_date,
        max_date=max_date,
        parallel=parallel,
        no_cache=no_cache,
    )
    final_result: GehiRunResult | None = None
    error = ""
    attempts = 0
    for attempt in range(1, max(1, max_attempts) + 1):
        attempts = attempt
        limiter.wait()
        attempt_started = utc_now()
        try:
            result = runner(args, executable=gehi_exe, timeout=timeout)
            final_result = result
            raw_log.write(
                {
                    "anchor_id": anchor_id,
                    "route_id": route_id,
                    "provider": spec.provider,
                    "zoom": spec.zoom,
                    "query_kind": spec.query_kind,
                    "attempt": attempt,
                    "started_utc": attempt_started,
                    "returncode": result.returncode,
                    "command": result.command,
                    "stdout_sha256": result.stdout_sha256,
                    "stderr_sha256": result.stderr_sha256,
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                }
            )
            if is_blocked_result(result):
                limiter.record_block()
                error = f"blocked response on attempt {attempt}"
                continue
            limiter.record_success()
            break
        except subprocess.TimeoutExpired as exc:
            error = f"TimeoutExpired: {exc}"
        except Exception as exc:  # noqa: BLE001 - outcome must survive every route failure
            error = f"{type(exc).__name__}: {exc}"
        raw_log.write(
            {
                "anchor_id": anchor_id,
                "route_id": route_id,
                "provider": spec.provider,
                "zoom": spec.zoom,
                "query_kind": spec.query_kind,
                "attempt": attempt,
                "started_utc": attempt_started,
                "exception": error,
            }
        )

    catalog_rows: list[dict[str, object]] = []
    status = "error"
    if final_result is not None and is_blocked_result(final_result):
        status = "blocked"
    elif final_result is not None:
        try:
            catalog_rows = parse_query_rows(
                anchor_id,
                spec,
                final_result,
                route_id,
                min_date=min_date,
                max_date=max_date,
            )
            status = "ok_nonempty" if catalog_rows else "ok_empty"
            error = ""
        except Exception as exc:  # noqa: BLE001 - parse/nonzero is an explicit outcome
            status = "error"
            error = f"{type(exc).__name__}: {exc}"

    outcome = {
        "anchor_id": anchor_id,
        "route_id": route_id,
        "provider": spec.provider,
        "zoom": spec.zoom,
        "query_kind": spec.query_kind,
        "status": status,
        "n_rows": len(catalog_rows),
        "attempts": attempts,
        "started_utc": started,
        "finished_utc": utc_now(),
        "stdout_sha256": final_result.stdout_sha256 if final_result else "",
        "stderr_sha256": final_result.stderr_sha256 if final_result else "",
        "gehi_command": final_result.command if final_result else "",
        "error": error,
    }
    return outcome, catalog_rows


def run_probe(
    anchors: Sequence[Mapping[str, object]],
    *,
    route_id: str,
    out_dir: Path,
    gehi_exe: Path = DEFAULT_GEHI_EXE,
    min_date: str = DEFAULT_MIN_DATE,
    max_date: str = DEFAULT_MAX_DATE,
    parallel: int = 4,
    no_cache: bool = False,
    timeout: float = 300.0,
    request_interval: float = 1.0,
    max_attempts: int = 3,
    runner: Callable[..., GehiRunResult] = run_gehi,
    limiter: GehiRateLimiter | None = None,
) -> dict[str, object]:
    validate_anchors(anchors)
    out_dir.mkdir(parents=True, exist_ok=True)
    outcome_path = out_dir / "query_outcomes.jsonl"
    catalog_path = out_dir / "catalog_rows.jsonl"
    raw_path = out_dir / "raw_gehi.jsonl"
    completed = completed_query_keys(outcome_path)
    limiter = limiter or GehiRateLimiter(min_interval_s=request_interval)
    attempted = succeeded = failed = skipped = 0

    with JsonlAppender(outcome_path) as outcome_log, JsonlAppender(
        catalog_path
    ) as catalog_log, JsonlAppender(raw_path) as raw_log:
        for anchor in anchors:
            anchor_id = str(anchor["anchor_id"])
            for spec in REQUIRED_SPECS:
                key = (anchor_id, *spec.key_suffix)
                if key in completed:
                    skipped += 1
                    continue
                outcome, rows = execute_query(
                    anchor,
                    spec,
                    route_id=route_id,
                    raw_log=raw_log,
                    limiter=limiter,
                    gehi_exe=gehi_exe,
                    min_date=min_date,
                    max_date=max_date,
                    parallel=parallel,
                    no_cache=no_cache,
                    timeout=timeout,
                    max_attempts=max_attempts,
                    runner=runner,
                )
                attempted += 1
                if str(outcome["status"]) in SUCCESS_STATUSES:
                    succeeded += 1
                else:
                    failed += 1
                outcome_log.write(outcome)
                for row in rows:
                    catalog_log.write(row)

    summary = {
        "schema_version": "ct05_route_probe_summary_v1",
        "route_id": route_id,
        "anchor_count": len(anchors),
        "required_query_count": len(anchors) * len(REQUIRED_SPECS),
        "attempted_this_run": attempted,
        "successful_this_run": succeeded,
        "failed_this_run": failed,
        "skipped_completed": skipped,
        "finished_utc": utc_now(),
        "address_family_env": {
            "DOTNET_SYSTEM_NET_DISABLEIPV6": os.environ.get(
                "DOTNET_SYSTEM_NET_DISABLEIPV6", ""
            )
        },
    }
    _write_json(out_dir / "summary.json", summary)
    return summary


def _latest_successful_outcomes(
    route_dirs: Sequence[Path],
) -> tuple[
    dict[tuple[str, str, int, str], dict[str, object]],
    list[dict[str, object]],
]:
    successes: dict[tuple[str, str, int, str], dict[str, object]] = {}
    all_rows: list[dict[str, object]] = []
    for route_dir in route_dirs:
        for row in read_jsonl(route_dir / "query_outcomes.jsonl"):
            all_rows.append(row)
            if str(row.get("status")) not in SUCCESS_STATUSES:
                continue
            key = (
                str(row.get("anchor_id", "")),
                str(row.get("provider", "")),
                int(row.get("zoom", 0)),
                str(row.get("query_kind", "")),
            )
            successes[key] = row
    return successes, all_rows


def _dedupe_catalog_rows(
    route_dirs: Sequence[Path],
    successful: Mapping[tuple[str, str, int, str], Mapping[str, object]],
) -> list[dict[str, object]]:
    # Route logs are append-only and may contain a failed-route retry followed
    # by success.  Only rows belonging to a query with a successful outcome
    # participate.  First row wins per provider/zoom/date; the final global
    # candidate selection below dedupes by (anchor_id, capture_date).
    kept: dict[tuple[str, str, int, str, str], dict[str, object]] = {}
    for route_dir in route_dirs:
        for row in read_jsonl(route_dir / "catalog_rows.jsonl"):
            query_key = (
                str(row.get("anchor_id", "")),
                str(row.get("provider", "")),
                int(row.get("zoom", 0)),
                str(row.get("query_kind", "")),
            )
            if query_key not in successful:
                continue
            key = (*query_key[:3], query_key[3], str(row.get("capture_date", "")))
            kept.setdefault(key, row)
    return list(kept.values())


def compute_cutoff(
    dates: Iterable[str],
    census_date: str,
    *,
    max_date: str,
    post_census_frames: int,
) -> str:
    if census_date >= max_date:
        return max_date
    newer = sorted({date for date in dates if census_date < date <= max_date})
    if post_census_frames <= 0 or not newer:
        return census_date
    return newer[min(post_census_frames, len(newer)) - 1]


def _candidate_priority(row: Mapping[str, object]) -> tuple[int, int, str, str]:
    # z19 primary, z18 fallback; TM wins a same-day provider collision because
    # its catalog row is bbox-complete.  Stable textual suffixes make the
    # choice reproducible if a future route repeats the same query.
    return (
        0 if int(row.get("zoom", 0)) == 19 else 1,
        0 if str(row.get("provider", "")) == "TM" else 1,
        str(row.get("route_id", "")),
        str(row.get("version", "")),
    )


def merge_routes(
    anchors: Sequence[Mapping[str, object]],
    route_dirs: Sequence[Path],
    out_dir: Path,
    *,
    min_date: str = DEFAULT_MIN_DATE,
    max_date: str = DEFAULT_MAX_DATE,
    census_date: str = DEFAULT_CENSUS_DATE,
    post_census_frames: int = DEFAULT_POST_CENSUS_FRAMES,
) -> dict[str, object]:
    validate_anchors(anchors)
    if not route_dirs:
        raise ValueError("at least one --route-dir is required")
    successful, all_outcomes = _latest_successful_outcomes(route_dirs)
    catalog_rows = _dedupe_catalog_rows(route_dirs, successful)
    by_anchor: dict[str, list[dict[str, object]]] = {}
    for row in catalog_rows:
        by_anchor.setdefault(str(row["anchor_id"]), []).append(row)

    final_outcomes: list[dict[str, object]] = []
    candidates: list[dict[str, object]] = []
    unresolved_anchor_ids: list[str] = []
    for anchor in anchors:
        anchor_id = str(anchor["anchor_id"])
        unresolved = [
            f"{spec.provider}:z{spec.zoom}:{spec.query_kind}"
            for spec in REQUIRED_SPECS
            if (anchor_id, *spec.key_suffix) not in successful
        ]
        rows = by_anchor.get(anchor_id, [])
        tm_dates = {
            str(row["capture_date"]) for row in rows if str(row["provider"]) == "TM"
        }
        wb_dates = {
            str(row["capture_date"])
            for row in rows
            if str(row["provider"]) == "Wayback"
        }
        merged_dates = tm_dates | wb_dates
        if unresolved:
            status = "operational_failure"
            release_eligible = 0
            unresolved_anchor_ids.append(anchor_id)
        elif not merged_dates:
            status = "no_history"
            release_eligible = 1
        elif not tm_dates and wb_dates:
            status = "wayback_only"
            release_eligible = 1
        elif tm_dates and not wb_dates:
            status = "tm_only"
            release_eligible = 1
        else:
            status = "tm_and_wayback"
            release_eligible = 1
        routes_observed = sorted(
            {
                str(row.get("route_id", ""))
                for row in all_outcomes
                if str(row.get("anchor_id", "")) == anchor_id
            }
        )
        final_outcomes.append(
            {
                "anchor_id": anchor_id,
                "catalog_status": status,
                "release_eligible": release_eligible,
                "tm_date_count": len(tm_dates),
                "wayback_date_count": len(wb_dates),
                "merged_date_count": len(merged_dates),
                "successful_query_count": len(REQUIRED_SPECS) - len(unresolved),
                "required_query_count": len(REQUIRED_SPECS),
                "unresolved_queries": ";".join(unresolved),
                "routes_observed": ";".join(routes_observed),
            }
        )
        if unresolved:
            continue
        cutoff = compute_cutoff(
            merged_dates,
            census_date,
            max_date=max_date,
            post_census_frames=post_census_frames,
        )
        rows_by_date: dict[str, list[dict[str, object]]] = {}
        for row in rows:
            date = str(row["capture_date"])[:10]
            if min_date <= date <= cutoff:
                rows_by_date.setdefault(date, []).append(row)
        for date, same_date_rows in sorted(rows_by_date.items()):
            chosen = min(same_date_rows, key=_candidate_priority)
            candidates.append(
                {
                    "anchor_id": anchor_id,
                    "capture_date": date,
                    "version": str(chosen.get("version", "")),
                    "provider": str(chosen["provider"]),
                    "requested_zoom": int(chosen["zoom"]),
                    "query_kind": str(chosen["query_kind"]),
                    "route_id": str(chosen["route_id"]),
                    "bbox_complete_at_catalog": int(
                        str(chosen["query_kind"]) == "availability_complete"
                    ),
                    "reference_only": int(date > census_date),
                    "census_date": census_date,
                    "cutoff_max_date": cutoff,
                    "catalog_stdout_sha256": str(chosen.get("stdout_sha256", "")),
                    "gehi_command": str(chosen.get("gehi_command", "")),
                }
            )

    out_dir.mkdir(parents=True, exist_ok=True)
    outcomes_csv = out_dir / "anchor_catalog_outcomes.csv"
    candidates_csv = out_dir / "gehi_vintage_candidates_ct05.csv"
    write_csv_rows(outcomes_csv, final_outcomes, FINAL_OUTCOME_FIELDS)
    write_csv_rows(candidates_csv, candidates, CANDIDATE_FIELDS)

    retry_rows = [
        anchor for anchor in anchors if str(anchor["anchor_id"]) in set(unresolved_anchor_ids)
    ]
    retry_csv = out_dir / "retry_anchors.csv"
    write_csv_rows(retry_csv, retry_rows, list(anchors[0].keys()))

    key_count = len(
        {(str(row["anchor_id"]), str(row["capture_date"])) for row in candidates}
    )
    if key_count != len(candidates):
        raise AssertionError("candidate dedupe invariant violated: duplicate (anchor_id, capture_date)")
    summary = {
        "schema_version": "ct05_catalog_merge_summary_v1",
        "created_utc": utc_now(),
        "anchor_count": len(anchors),
        "candidate_count": len(candidates),
        "candidate_key_count": key_count,
        "release_eligible_anchors": len(anchors) - len(unresolved_anchor_ids),
        "operational_failure_anchors": len(unresolved_anchor_ids),
        "wayback_only_anchors": sum(
            row["catalog_status"] == "wayback_only" for row in final_outcomes
        ),
        "no_history_anchors": sum(
            row["catalog_status"] == "no_history" for row in final_outcomes
        ),
        "census_date": census_date,
        "post_census_reference_frames": post_census_frames,
        "route_dirs": [str(path) for path in route_dirs],
        "outputs": {
            "anchor_catalog_outcomes": str(outcomes_csv),
            "candidates": str(candidates_csv),
            "retry_anchors": str(retry_csv),
        },
    }
    _write_json(out_dir / "summary.json", summary)
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    plan = sub.add_parser("plan")
    plan.add_argument("--anchors-csv", type=Path, required=True)
    plan.add_argument("--out-dir", type=Path, required=True)
    plan.add_argument("--routes", default=",".join(DEFAULT_ROUTES))

    remaining = sub.add_parser("plan-remaining")
    remaining.add_argument("--anchors-csv", type=Path, required=True)
    remaining.add_argument("--outcome", type=Path, action="append", required=True)
    remaining.add_argument("--route-id", required=True)
    remaining.add_argument("--lanes", type=int, default=2)
    remaining.add_argument("--out-dir", type=Path, required=True)

    probe = sub.add_parser("probe")
    probe.add_argument("--anchors-csv", type=Path, required=True)
    probe.add_argument("--route-id", required=True)
    probe.add_argument("--out-dir", type=Path, required=True)
    probe.add_argument("--gehi-exe", type=Path, default=DEFAULT_GEHI_EXE)
    probe.add_argument("--min-date", default=DEFAULT_MIN_DATE)
    probe.add_argument("--max-date", default=DEFAULT_MAX_DATE)
    probe.add_argument("--parallel", type=int, default=4)
    probe.add_argument("--no-cache", action="store_true")
    probe.add_argument("--timeout", type=float, default=300.0)
    probe.add_argument("--request-interval", type=float, default=1.0)
    probe.add_argument("--max-attempts", type=int, default=3)
    probe.add_argument("--limit-anchors", type=int)

    merge = sub.add_parser("merge")
    merge.add_argument("--anchors-csv", type=Path, required=True)
    merge.add_argument("--route-dir", type=Path, action="append", default=[])
    merge.add_argument(
        "--route-root",
        type=Path,
        action="append",
        default=[],
        help="Discover immediate child directories containing both catalog JSONL files.",
    )
    merge.add_argument("--out-dir", type=Path, required=True)
    merge.add_argument("--min-date", default=DEFAULT_MIN_DATE)
    merge.add_argument("--max-date", default=DEFAULT_MAX_DATE)
    merge.add_argument("--census-date", default=DEFAULT_CENSUS_DATE)
    merge.add_argument(
        "--post-census-frames", type=int, default=DEFAULT_POST_CENSUS_FRAMES
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    anchors = read_csv_rows(args.anchors_csv)
    if args.command == "plan":
        routes = tuple(value.strip() for value in args.routes.split(",") if value.strip())
        result = plan_routes(anchors, routes, args.out_dir)
    elif args.command == "plan-remaining":
        result = plan_remaining_lanes(
            anchors,
            args.outcome,
            route_id=args.route_id,
            lanes=args.lanes,
            out_dir=args.out_dir,
        )
    elif args.command == "probe":
        if args.limit_anchors is not None:
            anchors = anchors[: args.limit_anchors]
        result = run_probe(
            anchors,
            route_id=args.route_id,
            out_dir=args.out_dir,
            gehi_exe=args.gehi_exe,
            min_date=args.min_date,
            max_date=args.max_date,
            parallel=args.parallel,
            no_cache=args.no_cache,
            timeout=args.timeout,
            request_interval=args.request_interval,
            max_attempts=args.max_attempts,
        )
    else:
        route_dirs = list(args.route_dir)
        for root in args.route_root:
            route_dirs.extend(
                child
                for child in sorted(root.iterdir())
                if child.is_dir()
                and (child / "query_outcomes.jsonl").is_file()
                and (child / "catalog_rows.jsonl").is_file()
            )
        if not route_dirs:
            raise SystemExit("merge requires at least one --route-dir or --route-root")
        result = merge_routes(
            anchors,
            route_dirs,
            args.out_dir,
            min_date=args.min_date,
            max_date=args.max_date,
            census_date=args.census_date,
            post_census_frames=args.post_census_frames,
        )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
