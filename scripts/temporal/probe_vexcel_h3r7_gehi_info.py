#!/usr/bin/env python3
"""GEHI `info` reconnaissance at Vexcel-covered H3-7 centroids.

One centroid query per unique H3-7 cell × {TM, Wayback} at z19. This is a
coverage catalog, not bbox-complete availability and not a chip download.

Stages (resumable):
  plan       unique H3-7 centroids from the Vexcel municipality CSV
  probe      live/cached GEHI info; per-query JSON sentinels
  summarize  city × provider CSVs + independent SRC/RUN docs
  status     progress counts
  preflight  one Cape Town cell, both providers; refuse empty TM

Empty catalogs are real answers (`ok_empty`) and are not retried.
Timeouts / 403 blocks / errors are not empty and are retried on resume.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

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
    GehiRateLimiter,
    GehiRunResult,
    anchor_location_arg,
    assert_gehi_success,
    dedupe_info_rows_by_date,
    is_blocked_result,
    make_throttled_runner,
    parse_info_output,
    run_gehi,
)
from scripts.temporal.geid_temporal_common import read_csv_rows, write_csv_rows

DEFAULT_VEXCEL_CSV = (
    PROJECT_ROOT / "docs/briefs/_muni_catalog/vexcel_muni_h3_r7.csv"
)
DEFAULT_OUTPUT = (
    Path.home() / "zasolar_data/geid_temporal/satellite_vexcel_h3r7_gehi_info_2026-09-13"
)
DOCS_CATALOG = PROJECT_ROOT / "docs/briefs/_muni_catalog"
SRC_PATH = PROJECT_ROOT / "docs/briefs/SRC-satellite-vexcel-h3r7-gehi-info-2026-09-13.md"
RUN_PATH = PROJECT_ROOT / "docs/replan_v2/RUN-satellite-vexcel-h3r7-gehi-info-2026-09-13.md"

PROVIDERS = ("TM", "Wayback")
PROVIDER_LABEL = {
    "TM": "Google Earth (GEHI TM)",
    "Wayback": "Esri Wayback",
}
GSD_Z19_M = 0.25
SUCCESS_STATUSES = frozenset({"ok_nonempty", "ok_empty"})
RETRY_STATUSES = frozenset({"timeout", "blocked", "error"})

MUNI_ORDER = [
    "cape_town",
    "johannesburg",
    "ethekwini",
    "ekurhuleni",
    "tshwane",
    "nelson_mandela_bay",
    "buffalo_city",
    "mangaung",
    "msunduzi",
]
MUNI_NAMES = {
    "cape_town": "City of Cape Town",
    "johannesburg": "City of Johannesburg",
    "ethekwini": "eThekwini",
    "ekurhuleni": "City of Ekurhuleni",
    "tshwane": "City of Tshwane",
    "nelson_mandela_bay": "Nelson Mandela Bay",
    "buffalo_city": "Buffalo City",
    "mangaung": "Mangaung",
    "msunduzi": "Msunduzi",
}

CELL_PROVIDER_FIELDS = [
    "h3_index",
    "municipality",
    "municipalities",
    "municipality_name",
    "collections",
    "lat",
    "lng",
    "provider",
    "provider_label",
    "zoom",
    "gsd_m_nominal",
    "query_kind",
    "status",
    "n_dates",
    "n_years",
    "min_date",
    "max_date",
    "years",
    "cache_source",
    "started_utc",
    "finished_utc",
    "error",
]
CELL_FIELDS = [
    "h3_index",
    "municipalities",
    "collections",
    "lat",
    "lng",
    "tm_status",
    "tm_n_dates",
    "tm_n_years",
    "tm_min_date",
    "tm_max_date",
    "tm_years",
    "wayback_status",
    "wayback_n_dates",
    "wayback_n_years",
    "wayback_min_date",
    "wayback_max_date",
    "wayback_years",
    "union_n_dates",
    "union_n_years",
    "union_min_date",
    "union_max_date",
    "union_years",
]
CITY_PROVIDER_FIELDS = [
    "municipality",
    "municipality_name",
    "provider",
    "provider_label",
    "zoom",
    "gsd_m_nominal",
    "n_h3_vexcel",
    "n_ok",
    "n_nonempty",
    "n_empty",
    "n_fail",
    "median_dates",
    "median_dates_nonempty",
    "p10_dates",
    "p90_dates",
    "median_n_years",
    "min_date",
    "max_date",
    "years_union",
    "n_years_union",
]
YEAR_HIST_FIELDS = [
    "municipality",
    "municipality_name",
    "provider",
    "year",
    "n_cells_with_year",
    "n_date_rows",
]
LAYER_FIELDS = [
    "family",
    "municipality",
    "municipality_name",
    "layer_id",
    "provider",
    "provider_label",
    "zoom",
    "gsd_m_nominal",
    "n_h3_r7",
    "n_nonempty",
    "median_dates_per_cell",
    "min_date",
    "max_date",
    "years_union",
    "usable_as_time_axis",
    "note",
]


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def muni_rank(name: str) -> int:
    try:
        return MUNI_ORDER.index(name)
    except ValueError:
        return len(MUNI_ORDER)


def unique_h3_cells(rows: Sequence[Mapping[str, object]]) -> list[dict[str, Any]]:
    """Collapse Vexcel coverage rows to one centroid per H3-7 index.

    A cell that spills across municipalities (Johannesburg / Ekurhuleni) is
    probed once and attributed to every membership at summarize time.
    """
    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        h3_index = str(row["h3_index"]).strip()
        if not h3_index:
            raise ValueError("vexcel H3 CSV row missing h3_index")
        muni = str(row["municipality"]).strip()
        collection = str(row.get("collection", "")).strip()
        lat = float(row["lat"])
        lng = float(row["lng"])
        if h3_index not in grouped:
            grouped[h3_index] = {
                "h3_index": h3_index,
                "lat": lat,
                "lng": lng,
                "municipalities": set(),
                "collections": set(),
            }
        cell = grouped[h3_index]
        if abs(cell["lat"] - lat) > 1e-8 or abs(cell["lng"] - lng) > 1e-8:
            raise ValueError(
                f"h3 {h3_index} has conflicting centroids "
                f"({cell['lat']},{cell['lng']}) vs ({lat},{lng})"
            )
        cell["municipalities"].add(muni)
        if collection:
            cell["collections"].add(collection)

    out: list[dict[str, Any]] = []
    for h3_index, cell in grouped.items():
        munis = sorted(cell["municipalities"], key=muni_rank)
        collections = sorted(cell["collections"])
        primary = munis[0]
        out.append(
            {
                "h3_index": h3_index,
                "lat": cell["lat"],
                "lng": cell["lng"],
                "municipality": primary,
                "municipalities": munis,
                "municipalities_joined": "|".join(munis),
                "collections_joined": "|".join(collections),
                "municipality_name": MUNI_NAMES.get(primary, primary),
            }
        )
    out.sort(key=lambda c: (muni_rank(c["municipality"]), c["h3_index"]))
    return out


def cell_as_anchor(cell: Mapping[str, Any]) -> dict[str, object]:
    return {
        "anchor_id": cell["h3_index"],
        "region_key": cell["municipality"],
        "grid_id": cell["h3_index"],
        "centroid_lat": cell["lat"],
        "centroid_lon": cell["lng"],
    }


def query_filename(h3_index: str, provider: str) -> str:
    return f"{h3_index}__{provider}.json"


def probe_path(root: Path, h3_index: str, provider: str) -> Path:
    return root / "probes" / query_filename(h3_index, provider)


def is_success(status: str) -> bool:
    return status in SUCCESS_STATUSES


def _percentile(values: Sequence[float], p: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    idx = (len(ordered) - 1) * p
    lo = int(idx)
    hi = min(lo + 1, len(ordered) - 1)
    frac = idx - lo
    return float(ordered[lo] * (1.0 - frac) + ordered[hi] * frac)


def _median(values: Sequence[float]) -> float | None:
    if not values:
        return None
    return float(statistics.median(values))


def _round_or_blank(value: float | None, ndigits: int = 1) -> str | float:
    if value is None:
        return ""
    return round(value, ndigits)


def dates_from_info_rows(rows: Sequence[Mapping[str, object]]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for row in rows:
        date = str(row.get("capture_date", "")).strip()
        if date and date not in seen:
            seen.add(date)
            out.append(date)
    return sorted(out)


def years_from_dates(dates: Sequence[str]) -> list[int]:
    years = sorted({int(d[:4]) for d in dates if len(d) >= 4})
    return years


def outcome_from_dates(
    *,
    cell: Mapping[str, Any],
    provider: str,
    zoom: int,
    dates: Sequence[str],
    status: str,
    cache_source: str,
    started_utc: str,
    finished_utc: str,
    error: str = "",
    gehi_command: str = "",
) -> dict[str, Any]:
    years = years_from_dates(dates)
    return {
        "h3_index": cell["h3_index"],
        "municipality": cell["municipality"],
        "municipalities": cell["municipalities_joined"],
        "municipality_name": cell["municipality_name"],
        "collections": cell["collections_joined"],
        "lat": cell["lat"],
        "lng": cell["lng"],
        "provider": provider,
        "provider_label": PROVIDER_LABEL[provider],
        "zoom": zoom,
        "gsd_m_nominal": GSD_Z19_M,
        "query_kind": "info",
        "status": status,
        "n_dates": len(dates),
        "n_years": len(years),
        "min_date": dates[0] if dates else "",
        "max_date": dates[-1] if dates else "",
        "years": ",".join(str(y) for y in years),
        "dates": list(dates),
        "cache_source": cache_source,
        "started_utc": started_utc,
        "finished_utc": finished_utc,
        "error": error,
        "gehi_command": gehi_command,
    }


def probe_one(
    cell: Mapping[str, Any],
    provider: str,
    *,
    zoom: int = DEFAULT_PROBE_ZOOM,
    gehi_exe: Path = DEFAULT_GEHI_EXE,
    timeout: float = 300.0,
    runner: Callable[..., GehiRunResult] | None = None,
    catalog_cache: CatalogCache | None = None,
    force_refresh: bool = False,
    max_age_days: float = DEFAULT_CATALOG_MAX_AGE_DAYS,
    no_cache: bool = False,
) -> dict[str, Any]:
    """Run one GEHI info query. Distinguishes empty catalogs from failures."""
    anchor = cell_as_anchor(cell)
    location = anchor_location_arg(anchor)
    cmd_args: list[object] = [
        "info",
        "--location",
        location,
        "--zoom",
        zoom,
        "--provider",
        provider,
    ]
    if no_cache:
        cmd_args.append("--no-cache")

    live_meta: dict[str, Any] = {
        "returncode": None,
        "blocked": False,
        "command": "",
        "stdout_sha256": "",
    }
    started = utc_now()
    base_runner = runner if runner is not None else run_gehi

    def _live() -> tuple[list[dict[str, object]], str, str]:
        result = base_runner(cmd_args, executable=gehi_exe, timeout=timeout)
        live_meta["returncode"] = result.returncode
        live_meta["blocked"] = is_blocked_result(result)
        live_meta["command"] = result.command
        live_meta["stdout_sha256"] = result.stdout_sha256
        if live_meta["blocked"]:
            raise RuntimeError(
                f"GEHI blocked ({result.returncode}): {(result.stderr or result.stdout)[:300]}"
            )
        assert_gehi_success(result)
        return parse_info_output(result.stdout), result.stdout_sha256, result.command

    cache_source = "live"
    stats_before = catalog_cache.stats.snapshot() if catalog_cache is not None else None
    try:
        if catalog_cache is None:
            raw_rows, _sha, command = _live()
        else:
            cache_key = CatalogCacheKey(
                kind="info",
                provider=provider,
                zoom=int(zoom),
                lower_left=location,
            )
            raw_rows = get_or_fetch(
                catalog_cache,
                cache_key,
                force_refresh=force_refresh,
                max_age_days=max_age_days,
                anchor_id=str(anchor["anchor_id"]),
                region_key=str(anchor["region_key"]),
                grid_id=str(anchor["grid_id"]),
                live_fn=_live,
                label="vexcel_h3r7_gehi_info",
            )
            stats_after = catalog_cache.stats.snapshot()
            assert stats_before is not None
            if stats_after["hits"] > stats_before["hits"]:
                cache_source = "hit"
            elif stats_after["stale_served"] > stats_before["stale_served"]:
                cache_source = "stale"
            else:
                cache_source = "live"
            cached = catalog_cache.get_any(cache_key)
            command = cached.gehi_command if cached is not None else live_meta["command"]
    except subprocess.TimeoutExpired as exc:
        return outcome_from_dates(
            cell=cell,
            provider=provider,
            zoom=zoom,
            dates=[],
            status="timeout",
            cache_source="none",
            started_utc=started,
            finished_utc=utc_now(),
            error=f"TimeoutExpired:{exc.timeout}",
        )
    except Exception as exc:  # noqa: BLE001 - keep the batch alive
        status = "blocked" if live_meta["blocked"] else "error"
        return outcome_from_dates(
            cell=cell,
            provider=provider,
            zoom=zoom,
            dates=[],
            status=status,
            cache_source="none",
            started_utc=started,
            finished_utc=utc_now(),
            error=f"{type(exc).__name__}: {exc}"[:500],
            gehi_command=str(live_meta["command"]),
        )

    stamped: list[dict[str, object]] = []
    for item in raw_rows:
        row = dict(item)
        row.update(
            {
                "anchor_id": anchor["anchor_id"],
                "region_key": anchor["region_key"],
                "grid_id": anchor["grid_id"],
                "provider": provider,
            }
        )
        stamped.append(row)
    dates = dates_from_info_rows(dedupe_info_rows_by_date(stamped))
    return outcome_from_dates(
        cell=cell,
        provider=provider,
        zoom=zoom,
        dates=dates,
        status="ok_nonempty" if dates else "ok_empty",
        cache_source=cache_source,
        started_utc=started,
        finished_utc=utc_now(),
        gehi_command=command,
    )


def plan_queries(cells: Sequence[Mapping[str, Any]]) -> list[tuple[dict[str, Any], str]]:
    return [(cell, provider) for cell in cells for provider in PROVIDERS]


def load_cells_from_plan(root: Path) -> list[dict[str, Any]]:
    path = root / "plan" / "h3_centroids.csv"
    if not path.is_file():
        raise SystemExit(f"plan missing: {path} (run plan first)")
    rows = read_csv_rows(path)
    cells = []
    for row in rows:
        cells.append(
            {
                "h3_index": row["h3_index"],
                "lat": float(row["lat"]),
                "lng": float(row["lng"]),
                "municipality": row["municipality"],
                "municipalities": str(row["municipalities"]).split("|") if row["municipalities"] else [],
                "municipalities_joined": row["municipalities"],
                "collections_joined": row["collections"],
                "municipality_name": row["municipality_name"],
            }
        )
    return cells


def cmd_plan(args: argparse.Namespace) -> None:
    root: Path = args.output
    rows = read_csv_rows(args.vexcel_csv)
    cells = unique_h3_cells(rows)
    queries = plan_queries(cells)
    plan_dir = root / "plan"
    plan_dir.mkdir(parents=True, exist_ok=True)
    centroid_rows = [
        {
            "h3_index": c["h3_index"],
            "lat": f"{c['lat']:.10f}",
            "lng": f"{c['lng']:.10f}",
            "municipality": c["municipality"],
            "municipalities": c["municipalities_joined"],
            "collections": c["collections_joined"],
            "municipality_name": c["municipality_name"],
        }
        for c in cells
    ]
    write_csv_rows(
        plan_dir / "h3_centroids.csv",
        centroid_rows,
        [
            "h3_index",
            "lat",
            "lng",
            "municipality",
            "municipalities",
            "collections",
            "municipality_name",
        ],
    )
    by_muni: dict[str, int] = defaultdict(int)
    for cell in cells:
        for muni in cell["municipalities"]:
            by_muni[muni] += 1
    payload = {
        "created": utc_now(),
        "vexcel_csv": str(args.vexcel_csv),
        "n_source_rows": len(rows),
        "n_unique_h3": len(cells),
        "n_queries": len(queries),
        "providers": list(PROVIDERS),
        "zoom": args.zoom,
        "query_kind": "info",
        "gsd_m_nominal": GSD_Z19_M,
        "n_h3_by_municipality": dict(sorted(by_muni.items(), key=lambda kv: muni_rank(kv[0]))),
        "note": (
            "Centroid GEHI info, not bbox-complete availability. "
            "A date at the centroid is not a promise the whole ~4.9 km2 cell is complete."
        ),
    }
    _write_json(plan_dir / "plan.json", payload)
    (root / "probes").mkdir(parents=True, exist_ok=True)
    print(
        f"plan: {len(cells)} unique H3-7 cells, {len(queries)} queries "
        f"(TM+Wayback z{args.zoom}) -> {plan_dir}"
    )


def _append_jsonl(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, ensure_ascii=False) + "\n")


def _progress_payload(
    *,
    n_queries: int,
    n_done: int,
    n_success: int,
    n_empty: int,
    n_fail: int,
    last_key: str,
) -> dict[str, Any]:
    return {
        "updated": utc_now(),
        "n_queries": n_queries,
        "n_done_success": n_success,
        "n_empty": n_empty,
        "n_fail_open": n_fail,
        "n_remaining": max(0, n_queries - n_success),
        "last_key": last_key,
        "frac_success": round(n_success / n_queries, 4) if n_queries else 0.0,
    }


def cmd_status(args: argparse.Namespace) -> None:
    root: Path = args.output
    plan_path = root / "plan" / "plan.json"
    if not plan_path.is_file():
        print("status: no plan yet")
        return
    plan = _read_json(plan_path)
    n_queries = int(plan["n_queries"])
    n_ok = n_empty = n_fail = 0
    for path in (root / "probes").glob("*.json"):
        status = str(_read_json(path).get("status", ""))
        if status == "ok_nonempty":
            n_ok += 1
        elif status == "ok_empty":
            n_empty += 1
        else:
            n_fail += 1
    n_success = n_ok + n_empty
    print(
        f"status: success={n_success}/{n_queries} "
        f"(nonempty={n_ok} empty={n_empty} retryable={n_fail} "
        f"remaining={n_queries - n_success})"
    )
    complete = root / "stages" / "summarize.done"
    if complete.is_file():
        print(f"summarize.done {complete}")


def cmd_probe(args: argparse.Namespace) -> None:
    root: Path = args.output
    cells = load_cells_from_plan(root)
    if args.limit_cells:
        cells = cells[: args.limit_cells]
    queries = plan_queries(cells)
    catalog_cache: CatalogCache | None = None
    if not args.no_catalog_cache:
        db_path = (args.catalog_cache_dir / DB_FILENAME) if args.catalog_cache_dir else None
        catalog_cache = CatalogCache(db_path)
    limiter = GehiRateLimiter(min_interval_s=args.request_interval)
    runner = make_throttled_runner(limiter=limiter, base_runner=run_gehi)
    n_success = n_empty = n_fail = n_skipped = 0
    started_all = utc_now()
    jsonl = root / "logs" / "query_outcomes.jsonl"
    for i, (cell, provider) in enumerate(queries, start=1):
        path = probe_path(root, cell["h3_index"], provider)
        if path.is_file() and not args.force_retry_all:
            existing = _read_json(path)
            if is_success(str(existing.get("status", ""))):
                n_skipped += 1
                if existing["status"] == "ok_empty":
                    n_empty += 1
                n_success += 1
                continue
        outcome = probe_one(
            cell,
            provider,
            zoom=args.zoom,
            gehi_exe=args.gehi_exe,
            timeout=args.timeout,
            runner=runner,
            catalog_cache=catalog_cache,
            force_refresh=args.force_catalog_refresh,
            max_age_days=args.catalog_max_age_days,
            no_cache=args.no_cache,
        )
        _write_json(path, outcome)
        _append_jsonl(
            jsonl,
            {
                "h3_index": outcome["h3_index"],
                "provider": outcome["provider"],
                "status": outcome["status"],
                "n_dates": outcome["n_dates"],
                "cache_source": outcome["cache_source"],
                "finished_utc": outcome["finished_utc"],
                "error": outcome["error"],
            },
        )
        if is_success(outcome["status"]):
            n_success += 1
            if outcome["status"] == "ok_empty":
                n_empty += 1
        else:
            n_fail += 1
        if i % 10 == 0 or i == len(queries):
            _write_json(
                root / "progress.json",
                _progress_payload(
                    n_queries=len(queries),
                    n_done=i,
                    n_success=n_success,
                    n_empty=n_empty,
                    n_fail=n_fail,
                    last_key=f"{cell['h3_index']}|{provider}|{outcome['status']}",
                ),
            )
            print(
                f"[{utc_now()}] {i}/{len(queries)} "
                f"success={n_success} empty={n_empty} fail={n_fail} skip={n_skipped} "
                f"last={cell['h3_index']}|{provider}|{outcome['status']}|n={outcome['n_dates']}",
                flush=True,
            )
    _write_json(
        root / "stages" / "probe.done",
        {
            "stage": "probe",
            "finished_at": utc_now(),
            "started_at": started_all,
            "n_queries": len(queries),
            "n_success": n_success,
            "n_empty": n_empty,
            "n_fail": n_fail,
            "n_skipped": n_skipped,
            "cache": catalog_cache.stats.summary() if catalog_cache is not None else "disabled",
        },
    )
    if catalog_cache is not None:
        print(catalog_cache.stats.summary())
    print(
        f"probe done success={n_success}/{len(queries)} empty={n_empty} fail={n_fail} skip={n_skipped}"
    )
    if n_success < len(queries):
        raise SystemExit(
            f"probe incomplete: {len(queries) - n_success} queries still retryable; rerun probe"
        )


def _load_outcomes(root: Path, cells: Sequence[Mapping[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for cell in cells:
        for provider in PROVIDERS:
            path = probe_path(root, cell["h3_index"], provider)
            if path.is_file():
                out[(cell["h3_index"], provider)] = _read_json(path)
    return out


def _usable_label(median_dates: float | None, n_nonempty: int, n_cells: int) -> str:
    if not n_cells or n_nonempty == 0:
        return "no"
    frac = n_nonempty / n_cells
    if median_dates is None:
        return "no"
    if frac >= 0.9 and median_dates >= 20:
        return "yes_dense"
    if frac >= 0.7 and median_dates >= 5:
        return "yes_thin_complement"
    if frac >= 0.5:
        return "sparse"
    return "thin"


def summarize_records(
    cells: Sequence[Mapping[str, Any]],
    outcomes: Mapping[tuple[str, str], Mapping[str, Any]],
) -> dict[str, Any]:
    """Build the independent catalog tables. Membership is exploded per municipality."""
    cell_provider_rows: list[dict[str, Any]] = []
    cell_rows: list[dict[str, Any]] = []
    city_counts: dict[tuple[str, str], dict[str, Any]] = {}
    year_cells: dict[tuple[str, str, int], set[str]] = defaultdict(set)
    year_dates: dict[tuple[str, str, int], int] = defaultdict(int)

    def bucket(muni: str, provider: str) -> dict[str, Any]:
        key = (muni, provider)
        if key not in city_counts:
            city_counts[key] = {
                "n_h3_vexcel": 0,
                "n_ok": 0,
                "n_nonempty": 0,
                "n_empty": 0,
                "n_fail": 0,
                "n_dates_all": [],
                "n_dates_nonempty": [],
                "n_years_all": [],
                "dates": [],
            }
        return city_counts[key]

    for cell in cells:
        tm = outcomes.get((cell["h3_index"], "TM"), {})
        wb = outcomes.get((cell["h3_index"], "Wayback"), {})
        tm_dates = list(tm.get("dates") or [])
        wb_dates = list(wb.get("dates") or [])
        union = sorted(set(tm_dates) | set(wb_dates))
        union_years = years_from_dates(union)
        cell_rows.append(
            {
                "h3_index": cell["h3_index"],
                "municipalities": cell["municipalities_joined"],
                "collections": cell["collections_joined"],
                "lat": cell["lat"],
                "lng": cell["lng"],
                "tm_status": tm.get("status", "missing"),
                "tm_n_dates": tm.get("n_dates", ""),
                "tm_n_years": tm.get("n_years", ""),
                "tm_min_date": tm.get("min_date", ""),
                "tm_max_date": tm.get("max_date", ""),
                "tm_years": tm.get("years", ""),
                "wayback_status": wb.get("status", "missing"),
                "wayback_n_dates": wb.get("n_dates", ""),
                "wayback_n_years": wb.get("n_years", ""),
                "wayback_min_date": wb.get("min_date", ""),
                "wayback_max_date": wb.get("max_date", ""),
                "wayback_years": wb.get("years", ""),
                "union_n_dates": len(union) if tm and wb and is_success(str(tm.get("status", ""))) and is_success(str(wb.get("status", ""))) else "",
                "union_n_years": len(union_years) if union else "",
                "union_min_date": union[0] if union else "",
                "union_max_date": union[-1] if union else "",
                "union_years": ",".join(str(y) for y in union_years),
            }
        )
        for provider in PROVIDERS:
            outcome = outcomes.get((cell["h3_index"], provider))
            if outcome is None:
                continue
            for muni in cell["municipalities"]:
                row = {
                    "h3_index": cell["h3_index"],
                    "municipality": muni,
                    "municipalities": cell["municipalities_joined"],
                    "municipality_name": MUNI_NAMES.get(muni, muni),
                    "collections": cell["collections_joined"],
                    "lat": cell["lat"],
                    "lng": cell["lng"],
                    "provider": provider,
                    "provider_label": PROVIDER_LABEL[provider],
                    "zoom": outcome.get("zoom", DEFAULT_PROBE_ZOOM),
                    "gsd_m_nominal": GSD_Z19_M,
                    "query_kind": "info",
                    "status": outcome.get("status", ""),
                    "n_dates": outcome.get("n_dates", 0),
                    "n_years": outcome.get("n_years", 0),
                    "min_date": outcome.get("min_date", ""),
                    "max_date": outcome.get("max_date", ""),
                    "years": outcome.get("years", ""),
                    "cache_source": outcome.get("cache_source", ""),
                    "started_utc": outcome.get("started_utc", ""),
                    "finished_utc": outcome.get("finished_utc", ""),
                    "error": outcome.get("error", ""),
                }
                cell_provider_rows.append(row)
                b = bucket(muni, provider)
                b["n_h3_vexcel"] += 1
                status = str(outcome.get("status", ""))
                if status == "ok_nonempty":
                    b["n_ok"] += 1
                    b["n_nonempty"] += 1
                    n_dates = int(outcome.get("n_dates") or 0)
                    b["n_dates_all"].append(n_dates)
                    b["n_dates_nonempty"].append(n_dates)
                    b["n_years_all"].append(int(outcome.get("n_years") or 0))
                    for date in outcome.get("dates") or []:
                        b["dates"].append(date)
                        year = int(str(date)[:4])
                        year_cells[(muni, provider, year)].add(cell["h3_index"])
                        year_dates[(muni, provider, year)] += 1
                elif status == "ok_empty":
                    b["n_ok"] += 1
                    b["n_empty"] += 1
                    b["n_dates_all"].append(0)
                    b["n_years_all"].append(0)
                else:
                    b["n_fail"] += 1

    city_rows: list[dict[str, Any]] = []
    layer_rows: list[dict[str, Any]] = []
    for muni in MUNI_ORDER:
        for provider in PROVIDERS:
            b = city_counts.get((muni, provider))
            if b is None:
                continue
            dates_sorted = sorted(b["dates"])
            years_union = years_from_dates(dates_sorted)
            median_all = _median(b["n_dates_all"])
            usable = _usable_label(median_all, b["n_nonempty"], b["n_h3_vexcel"])
            city_row = {
                "municipality": muni,
                "municipality_name": MUNI_NAMES.get(muni, muni),
                "provider": provider,
                "provider_label": PROVIDER_LABEL[provider],
                "zoom": DEFAULT_PROBE_ZOOM,
                "gsd_m_nominal": GSD_Z19_M,
                "n_h3_vexcel": b["n_h3_vexcel"],
                "n_ok": b["n_ok"],
                "n_nonempty": b["n_nonempty"],
                "n_empty": b["n_empty"],
                "n_fail": b["n_fail"],
                "median_dates": _round_or_blank(median_all),
                "median_dates_nonempty": _round_or_blank(_median(b["n_dates_nonempty"])),
                "p10_dates": _round_or_blank(_percentile(b["n_dates_all"], 0.10)),
                "p90_dates": _round_or_blank(_percentile(b["n_dates_all"], 0.90)),
                "median_n_years": _round_or_blank(_median(b["n_years_all"])),
                "min_date": dates_sorted[0] if dates_sorted else "",
                "max_date": dates_sorted[-1] if dates_sorted else "",
                "years_union": ",".join(str(y) for y in years_union),
                "n_years_union": len(years_union),
            }
            city_rows.append(city_row)
            layer_rows.append(
                {
                    "family": "satellite",
                    "municipality": muni,
                    "municipality_name": MUNI_NAMES.get(muni, muni),
                    "layer_id": f"{muni}_{provider}_z19_h3centroid",
                    "provider": provider,
                    "provider_label": PROVIDER_LABEL[provider],
                    "zoom": DEFAULT_PROBE_ZOOM,
                    "gsd_m_nominal": GSD_Z19_M,
                    "n_h3_r7": b["n_h3_vexcel"],
                    "n_nonempty": b["n_nonempty"],
                    "median_dates_per_cell": city_row["median_dates"],
                    "min_date": city_row["min_date"],
                    "max_date": city_row["max_date"],
                    "years_union": city_row["years_union"],
                    "usable_as_time_axis": usable,
                    "note": (
                        "GEHI info at H3-7 centroid, Vexcel-covered cells only. "
                        "Not bbox-complete. Independent of the 2026-09-06 CT/JHB reuse catalog."
                    ),
                }
            )

    year_rows: list[dict[str, Any]] = []
    for (muni, provider, year), h3s in sorted(
        year_cells.items(), key=lambda kv: (muni_rank(kv[0][0]), kv[0][1], kv[0][2])
    ):
        year_rows.append(
            {
                "municipality": muni,
                "municipality_name": MUNI_NAMES.get(muni, muni),
                "provider": provider,
                "year": year,
                "n_cells_with_year": len(h3s),
                "n_date_rows": year_dates[(muni, provider, year)],
            }
        )

    n_unique = len(cells)
    n_success_pairs = sum(
        1
        for cell in cells
        for provider in PROVIDERS
        if is_success(str(outcomes.get((cell["h3_index"], provider), {}).get("status", "")))
    )
    return {
        "cell_provider_rows": cell_provider_rows,
        "cell_rows": cell_rows,
        "city_rows": city_rows,
        "year_rows": year_rows,
        "layer_rows": layer_rows,
        "n_unique_h3": n_unique,
        "n_success_pairs": n_success_pairs,
        "n_expected_pairs": n_unique * len(PROVIDERS),
    }


def render_src(summary: Mapping[str, Any], city_rows: Sequence[Mapping[str, Any]]) -> str:
    created = summary.get("created", utc_now())
    lines = [
        "# SRC — GEHI satellite coverage at Vexcel H3-7 centroids",
        "",
        f"Date: {created[:10]} · Method: **live `gehi_info` at H3-7 centroids**, "
        "TM + Wayback, z19. Sample: every unique Vexcel-covered H3-7 cell "
        "in `vexcel_muni_h3_r7.csv` (union of 12 urban ortho collections). "
        "Not the 2026-09-06 Cape Town / Johannesburg reuse catalog.",
        "",
        "Companion CSVs:",
        "[`_muni_catalog/satellite_vexcel_h3r7_cell.csv`](_muni_catalog/satellite_vexcel_h3r7_cell.csv)",
        "· [`_muni_catalog/satellite_vexcel_h3r7_cell_provider.csv`](_muni_catalog/satellite_vexcel_h3r7_cell_provider.csv)",
        "· [`_muni_catalog/satellite_vexcel_h3r7_city_provider.csv`](_muni_catalog/satellite_vexcel_h3r7_city_provider.csv)",
        "· [`_muni_catalog/satellite_vexcel_h3r7_year_histogram.csv`](_muni_catalog/satellite_vexcel_h3r7_year_histogram.csv)",
        "· [`_muni_catalog/satellite_vexcel_h3r7_layers.csv`](_muni_catalog/satellite_vexcel_h3r7_layers.csv).",
        "Full copy: `~/zasolar_data/geid_temporal/satellite_vexcel_h3r7_gehi_info_2026-09-13/`.",
        "",
        "**GSD** (ground sample distance，地面采样距离) is the repo convention at",
        "South-African latitudes: z19 ≈ 0.25 m. **Time node** = one distinct",
        "`capture_date` after `(cell, capture_date)` dedupe. Wayback dates are",
        "Esri *captured* dates, not layer release dates.",
        "",
        "---",
        "",
        "## 1. What this is, and what it is not",
        "",
        "Each H3-7 cell (~4.9 km² at Cape Town) is represented by **one point**:",
        "the cell centroid already stored on the Vexcel coverage CSV. `gehi_info`",
        "lists every labeled vintage at that point. That is the cheap way to ask",
        "\"does this city even have a satellite time axis, and how many years?\"",
        "",
        "It is **not**:",
        "",
        "- bbox-complete `availability` over the whole hex (a date at the centroid",
        "  can miss the rest of the cell, and a date covering most roofs can miss",
        "  the centroid);",
        "- z20 / z18;",
        "- a per-roof catalog (Cape Town's 111,801-anchor citywide file remains",
        "  the production stack for that city);",
        "- a chip download.",
        "",
        "Cape Town numbers here will **not** match the 2026-09-06 reuse catalog",
        "(median 107 bbox-complete chip-dates / anchor). Centroid `info` and",
        "chip-box `availability --complete` are different queries. Use this file",
        "to compare cities; use the reuse catalog to date Cape Town roofs.",
        "",
        f"- Unique H3-7 cells probed: **{summary.get('n_unique_h3', '')}**",
        f"- Queries (TM + Wayback): **{summary.get('n_expected_pairs', '')}**",
        f"- Successful (including empty): **{summary.get('n_success_pairs', '')}**",
        "",
        "---",
        "",
        "## 2. City × provider (z19 info at centroid)",
        "",
        "`median_dates` includes empty cells as zero. `median_dates_nonempty`",
        "drops them. Coverage denominator is Vexcel-covered H3-7, not the",
        "cadastral metro.",
        "",
        "| city | provider | H3-7 | nonempty | empty | fail | median dates | median nonempty | years | span |",
        "|---|---|---:|---:|---:|---:|---:|---:|---|---|",
    ]
    for row in city_rows:
        years = str(row.get("years_union") or "")
        year_short = years if len(years) < 40 else f"{row.get('n_years_union')} years"
        span = ""
        if row.get("min_date") and row.get("max_date"):
            span = f"{row['min_date']} … {row['max_date']}"
        lines.append(
            f"| {row['municipality_name']} | {row['provider']} | {row['n_h3_vexcel']} | "
            f"{row['n_nonempty']} | {row['n_empty']} | {row['n_fail']} | "
            f"{row['median_dates']} | {row['median_dates_nonempty']} | {year_short} | {span} |"
        )
    lines += [
        "",
        "**What this says.** These medians are the number of distinct satellite",
        "capture dates sitting on a Vexcel-covered hex centroid. That is the",
        "time-axis density we would actually be able to line up against a",
        "municipal / Vexcel aerial year.",
        "",
        "**Why it matters.** A city with TM median ~100 dates can carry a dense",
        "satellite panel; a city with TM median ~10 and a 2019-only window cannot.",
        "Empty cells are part of the coverage story, not missing data.",
        "",
        "**If ignored.** One might (a) treat this as bbox-complete chip coverage",
        "and over-claim completeness inside a 4.9 km² hex, or (b) compare these",
        "medians to Cape Town's 107 chip-dates as if they were the same query.",
        "",
        "---",
        "",
        "## 3. How to read a time node",
        "",
        "- TM: Google Earth Time Machine labeled date at z19.",
        "- Wayback: Esri World Imagery Wayback *captured* date at z19.",
        "- Union (cell CSV): distinct dates across both providers. Same calendar",
        "  day from both sensors still counts once.",
        "- Years: distinct calendar years with ≥1 date. A year with one date and",
        "  a year with forty dates both count as one year-node for a year panel;",
        "  `n_dates` is what you need for day-interval work.",
        "",
        "Failed queries (`timeout` / `blocked` / `error`) are **not** empty",
        "catalogs. They stay retryable and do not enter the median.",
        "",
    ]
    return "\n".join(lines) + "\n"


def render_run(summary: Mapping[str, Any], city_rows: Sequence[Mapping[str, Any]]) -> str:
    created = summary.get("created", utc_now())
    lines = [
        "# RUN — Vexcel H3-7 GEHI info reconnaissance (TM + Wayback z19)",
        "",
        "| 字段 | 值 |",
        "|---|---|",
        "| 文档 ID | `RUN-satellite-vexcel-h3r7-gehi-info-2026-09-13` |",
        f"| 日期 | {created[:10]} |",
        "| Status | **done** (centroid `info` only; no chips) |",
        "| 样本 | all unique Vexcel-covered H3-7 cells |",
        f"| 格子 | {summary.get('n_unique_h3', '')} unique H3-7 |",
        f"| 查询 | {summary.get('n_expected_pairs', '')} = cells × {{TM, Wayback}} × z19 |",
        f"| 成功 | {summary.get('n_success_pairs', '')} (empty catalogs included) |",
        "| 真源 | `~/zasolar_data/geid_temporal/satellite_vexcel_h3r7_gehi_info_2026-09-13/` |",
        "| SRC | [`../briefs/SRC-satellite-vexcel-h3r7-gehi-info-2026-09-13.md`](../briefs/SRC-satellite-vexcel-h3r7-gehi-info-2026-09-13.md) |",
        "",
        "Centroid `gehi_info`, not bbox-complete availability. Independent of the",
        "2026-09-06 CT/JHB reuse catalog.",
        "",
        "## City medians",
        "",
        "| city | TM median dates | Wayback median dates | TM years | Wayback years |",
        "|---|---:|---:|---|---|",
    ]
    by_key = {(r["municipality"], r["provider"]): r for r in city_rows}
    for muni in MUNI_ORDER:
        tm = by_key.get((muni, "TM"))
        wb = by_key.get((muni, "Wayback"))
        if tm is None and wb is None:
            continue
        name = (tm or wb)["municipality_name"]
        lines.append(
            f"| {name} | {tm['median_dates'] if tm else ''} | "
            f"{wb['median_dates'] if wb else ''} | "
            f"{tm['n_years_union'] if tm else ''} | "
            f"{wb['n_years_union'] if wb else ''} |"
        )
    lines.append("")
    return "\n".join(lines) + "\n"


def cmd_summarize(args: argparse.Namespace) -> None:
    root: Path = args.output
    cells = load_cells_from_plan(root)
    outcomes = _load_outcomes(root, cells)
    tables = summarize_records(cells, outcomes)
    derived = root / "derived"
    derived.mkdir(parents=True, exist_ok=True)
    write_csv_rows(derived / "cell_provider.csv", tables["cell_provider_rows"], CELL_PROVIDER_FIELDS)
    write_csv_rows(derived / "cell.csv", tables["cell_rows"], CELL_FIELDS)
    write_csv_rows(derived / "city_provider.csv", tables["city_rows"], CITY_PROVIDER_FIELDS)
    write_csv_rows(derived / "year_histogram.csv", tables["year_rows"], YEAR_HIST_FIELDS)
    write_csv_rows(derived / "layers.csv", tables["layer_rows"], LAYER_FIELDS)
    summary = {
        "created": utc_now(),
        "method": "gehi_info centroid; TM+Wayback; z19; Vexcel H3-7 cells only",
        "n_unique_h3": tables["n_unique_h3"],
        "n_expected_pairs": tables["n_expected_pairs"],
        "n_success_pairs": tables["n_success_pairs"],
        "n_missing_or_failed": tables["n_expected_pairs"] - tables["n_success_pairs"],
        "output": str(root),
    }
    _write_json(derived / "SUMMARY.json", summary)
    src_text = render_src(summary, tables["city_rows"])
    run_text = render_run(summary, tables["city_rows"])
    (root / "SRC-satellite-vexcel-h3r7-gehi-info-2026-09-13.md").write_text(src_text, encoding="utf-8")
    (root / "RUN-satellite-vexcel-h3r7-gehi-info-2026-09-13.md").write_text(run_text, encoding="utf-8")
    if args.publish:
        if tables["n_success_pairs"] != tables["n_expected_pairs"]:
            raise SystemExit(
                "refusing to publish: "
                f"{tables['n_success_pairs']}/{tables['n_expected_pairs']} queries successful"
            )
        mapping = {
            "satellite_vexcel_h3r7_cell_provider.csv": derived / "cell_provider.csv",
            "satellite_vexcel_h3r7_cell.csv": derived / "cell.csv",
            "satellite_vexcel_h3r7_city_provider.csv": derived / "city_provider.csv",
            "satellite_vexcel_h3r7_year_histogram.csv": derived / "year_histogram.csv",
            "satellite_vexcel_h3r7_layers.csv": derived / "layers.csv",
        }
        DOCS_CATALOG.mkdir(parents=True, exist_ok=True)
        for name, src in mapping.items():
            dest = DOCS_CATALOG / name
            dest.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
        SRC_PATH.write_text(src_text, encoding="utf-8")
        RUN_PATH.write_text(run_text, encoding="utf-8")
        print(f"published CSVs -> {DOCS_CATALOG}")
        print(f"published SRC -> {SRC_PATH}")
        print(f"published RUN -> {RUN_PATH}")
    _write_json(
        root / "stages" / "summarize.done",
        {
            "stage": "summarize",
            "finished_at": utc_now(),
            "n_success_pairs": tables["n_success_pairs"],
            "n_expected_pairs": tables["n_expected_pairs"],
            "published": bool(args.publish),
        },
    )
    print(
        f"summarize: {tables['n_success_pairs']}/{tables['n_expected_pairs']} "
        f"successful pairs, {tables['n_unique_h3']} cells -> {derived}"
    )


def cmd_preflight(args: argparse.Namespace) -> None:
    """One Cape Town cell, both providers. TM must be nonempty."""
    root: Path = args.output
    cells = load_cells_from_plan(root)
    ct = [c for c in cells if "cape_town" in c["municipalities"]]
    if not ct:
        raise SystemExit("preflight: no Cape Town Vexcel cell in plan")
    cell = ct[0]
    catalog_cache: CatalogCache | None = None
    if not args.no_catalog_cache:
        db_path = (args.catalog_cache_dir / DB_FILENAME) if args.catalog_cache_dir else None
        catalog_cache = CatalogCache(db_path)
    limiter = GehiRateLimiter(min_interval_s=args.request_interval)
    runner = make_throttled_runner(limiter=limiter, base_runner=run_gehi)
    results = {}
    for provider in PROVIDERS:
        outcome = probe_one(
            cell,
            provider,
            zoom=args.zoom,
            gehi_exe=args.gehi_exe,
            timeout=args.timeout,
            runner=runner,
            catalog_cache=catalog_cache,
            force_refresh=args.force_catalog_refresh,
            max_age_days=args.catalog_max_age_days,
            no_cache=args.no_cache,
        )
        _write_json(probe_path(root, cell["h3_index"], provider), outcome)
        results[provider] = outcome
        print(
            f"preflight {cell['h3_index']} {provider}: "
            f"status={outcome['status']} n_dates={outcome['n_dates']} "
            f"span={outcome['min_date']}..{outcome['max_date']}",
            flush=True,
        )
    tm = results["TM"]
    if tm["status"] != "ok_nonempty":
        raise SystemExit(
            f"preflight failed: Cape Town TM was {tm['status']} ({tm.get('error','')}). "
            "Refusing to start the full probe."
        )
    _write_json(
        root / "stages" / "preflight.done",
        {
            "stage": "preflight",
            "finished_at": utc_now(),
            "h3_index": cell["h3_index"],
            "tm_n_dates": tm["n_dates"],
            "wayback_n_dates": results["Wayback"]["n_dates"],
            "wayback_status": results["Wayback"]["status"],
        },
    )
    print("preflight ok")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=("plan", "preflight", "probe", "summarize", "status"),
    )
    parser.add_argument("--vexcel-csv", type=Path, default=DEFAULT_VEXCEL_CSV)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--gehi-exe", type=Path, default=DEFAULT_GEHI_EXE)
    parser.add_argument("--zoom", type=int, default=DEFAULT_PROBE_ZOOM)
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument("--request-interval", type=float, default=1.0)
    parser.add_argument("--limit-cells", type=int)
    parser.add_argument("--no-cache", action="store_true", help="GEHI native --no-cache")
    parser.add_argument("--force-retry-all", action="store_true")
    parser.add_argument("--publish", action="store_true", help="copy CSVs + SRC into docs/")
    add_catalog_cache_cli_args(parser)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    args = parse_args(argv)
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "stages").mkdir(parents=True, exist_ok=True)
    (args.output / "logs").mkdir(parents=True, exist_ok=True)
    if args.command == "plan":
        cmd_plan(args)
    elif args.command == "preflight":
        cmd_preflight(args)
    elif args.command == "probe":
        cmd_probe(args)
    elif args.command == "summarize":
        cmd_summarize(args)
    elif args.command == "status":
        cmd_status(args)


if __name__ == "__main__":
    main()
