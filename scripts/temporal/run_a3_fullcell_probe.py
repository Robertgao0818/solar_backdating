#!/usr/bin/env python3
"""A3 full-cell availability probe + cold-cluster selector (handoff §7 step 3).

Two bounded questions the 2026-08-18 pilot left open:

1. Does `availability --complete` still report complete dates when the union
   window is a WHOLE dense grid cell (~400 anchors / ~1.1 km), not a 12-anchor
   cluster? Probe-only — no imagery is downloaded for the full-cell arms.
2. Which non-Top-52 cell should anchor the cold-cache wall-clock run? This
   script picks it deterministically (anchor count closest to the cold
   population median, >= cluster-n members, Top-52 excluded), probes its
   cluster union for a complete date (early-exit over up to 3 candidates),
   and exports a pilot-driver-compatible groups CSV so the cold download arms
   run through the UNMODIFIED `run_a3_region_batch_pilot.py`.

Stages (resumable via .done sentinels; only `availability` touches GEHI):
  plan         read citywide anchors, select probe cells, write plan.json
  availability availability --complete on each union (2 full-cell + 1-3 cold)
  export       cold groups CSV + chosen capture date + probe_summary.json

Live-call budget: 2 full-cell probes + 1..3 cold probes, all paced through the
shared GehiRateLimiter. No downloads happen here.
"""

from __future__ import annotations

# ruff: noqa: E402

import argparse
import json
import os
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.temporal.gehi_availability import fetch_availability_for_anchor
from scripts.temporal.gehi_common import (
    DEFAULT_GEHI_EXE,
    GehiRateLimiter,
    make_throttled_runner,
    run_gehi,
)
from scripts.temporal.gehi_region_batch import (
    DEFAULT_MOSAIC_PAD_DEG,
    region_anchor,
    select_centroid_cluster,
    union_bbox,
)
from scripts.temporal.geid_temporal_common import read_csv_rows, write_csv_rows

DEFAULT_ANCHORS = (
    Path.home()
    / "zasolar_data/geid_temporal/cape_town_citywide_backdating_v1_20260812"
    / "anchors_v1/anchors_all.csv"
)
DEFAULT_TOP52_GROUPS = (
    Path.home()
    / "zasolar_data/geid_temporal/cape_town_top52_backdating_v1_20260724"
    / "groups_v1/chip_groups_as_anchors.csv"
)
DEFAULT_OUTPUT = (
    Path.home() / "zasolar_data/geid_temporal/a3_fullcell_probe_20260819"
)
STAGES = ("plan", "availability", "export")
# A z19 tile spans 360/2**19 degrees of longitude.
Z19_TILE_DEG = 360.0 / 2**19
MAX_COLD_CANDIDATES = 3


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _stage_done(root: Path, stage: str) -> Path:
    return root / "stages" / f"{stage}.done"


def _mark_done(root: Path, stage: str, payload: Mapping[str, object]) -> None:
    _write_json(_stage_done(root, stage), {"stage": stage, "finished_at": utc_now(), **payload})


def _skip(root: Path, stage: str, force: bool) -> bool:
    return (not force) and _stage_done(root, stage).exists()


def _want(args: argparse.Namespace, stage: str) -> bool:
    return args.only is None or stage in args.only


def _load_plan(root: Path) -> dict[str, object]:
    path = root / "plan" / "plan.json"
    if not path.is_file():
        raise SystemExit(f"plan.json missing: {path} (run the plan stage first)")
    return json.loads(path.read_text(encoding="utf-8"))


def group_cells(
    anchors: Sequence[Mapping[str, object]],
    *,
    exclude_grids: set[str],
    min_members: int,
) -> dict[str, list[dict[str, object]]]:
    """Group anchor rows by source_grid, dropping excluded/sparse cells.

    Every returned row carries a `grid_id` copy of `source_grid` so the
    export is directly consumable by run_a3_region_batch_pilot.py.
    """
    cells: dict[str, list[dict[str, object]]] = {}
    for row in anchors:
        grid_id = str(row.get("source_grid", "")).strip()
        if not grid_id or grid_id in exclude_grids:
            continue
        item = dict(row)
        item["grid_id"] = grid_id
        cells.setdefault(grid_id, []).append(item)
    return {g: rows for g, rows in cells.items() if len(rows) >= min_members}


def select_fullcell_targets(
    cells: Mapping[str, Sequence[Mapping[str, object]]],
    *,
    reference_count: int = 400,
) -> list[str]:
    """Pick the densest cell and the cell closest to `reference_count`.

    These bracket the full-cell question: the stress case (densest) and the
    nominal "~400 anchors / ~1.1 km" case from the handoff. Stable tie-breaks
    on grid_id keep the selection replayable.
    """
    if not cells:
        raise ValueError("no cells to select from")
    densest = max(sorted(cells), key=lambda g: len(cells[g]))
    closest = min(
        sorted(cells),
        key=lambda g: (abs(len(cells[g]) - reference_count), g != densest),
    )
    picks = [densest]
    if closest != densest:
        picks.append(closest)
    return picks


def select_cold_candidates(
    cells: Mapping[str, Sequence[Mapping[str, object]]],
    *,
    n: int = MAX_COLD_CANDIDATES,
) -> list[str]:
    """Rank cells by |count - median count| (representative cold cells)."""
    if not cells:
        raise ValueError("no cells to select from")
    median = statistics.median(len(rows) for rows in cells.values())
    ranked = sorted(
        cells,
        key=lambda g: (abs(len(cells[g]) - median), g),
    )
    return ranked[: max(1, n)]


def _tile_estimate(width_deg: float, height_deg: float) -> dict[str, int]:
    """Whole-tile cover count for a z19 dump of the (padded) union."""
    import math

    n_x = max(1, math.ceil(width_deg / Z19_TILE_DEG))
    n_y = max(1, math.ceil(height_deg / Z19_TILE_DEG))
    return {"n_tiles_x": n_x, "n_tiles_y": n_y, "n_tiles": n_x * n_y}


def _region_record(grid_id: str, members: Sequence[Mapping[str, object]]) -> dict[str, object]:
    bbox = union_bbox(members)
    padded = bbox.padded(DEFAULT_MOSAIC_PAD_DEG)
    region = region_anchor(grid_id=grid_id, capture_date="probe", bbox=bbox)
    return {
        "grid_id": grid_id,
        "n_members": len(members),
        "region_anchor": region,
        "union": {
            "lon_min": bbox.lon_min,
            "lat_min": bbox.lat_min,
            "lon_max": bbox.lon_max,
            "lat_max": bbox.lat_max,
            "width_m": bbox.width_m(),
            "height_m": bbox.height_m(),
        },
        "padded_tile_estimate_z19": _tile_estimate(padded.width_deg, padded.height_deg),
    }


def stage_plan(args: argparse.Namespace) -> dict[str, object]:
    anchors = read_csv_rows(args.anchors_csv)
    top52 = {
        str(row.get("grid_id", "")).strip()
        for row in read_csv_rows(args.top52_groups_csv)
    }
    cells = group_cells(anchors, exclude_grids=top52, min_members=args.cluster_n)
    if not cells:
        raise SystemExit("no non-Top-52 cells with enough anchors")
    full_picks = select_fullcell_targets(cells)
    cold_picks = select_cold_candidates(cells)
    full_cells = [_region_record(g, cells[g]) for g in full_picks]
    cold_candidates = []
    for grid_id in cold_picks:
        members = cells[grid_id]
        cluster = select_centroid_cluster(members, n=args.cluster_n)
        rec = _region_record(grid_id, cluster)
        rec["n_cell_members"] = len(members)
        rec["anchor_ids"] = [str(r["anchor_id"]) for r in cluster]
        cold_candidates.append(rec)
    plan = {
        "schema_version": "a3_fullcell_probe_v1",
        "created_at": utc_now(),
        "anchors_csv": str(args.anchors_csv),
        "top52_groups_csv": str(args.top52_groups_csv),
        "n_top52_grids_excluded": len(top52),
        "n_eligible_cells": len(cells),
        "provider": args.provider,
        "zoom": args.zoom,
        "cluster_n": args.cluster_n,
        "min_date": args.min_date,
        "max_date": args.max_date,
        "full_cells": full_cells,
        "cold_candidates": cold_candidates,
        "notes": (
            "Full-cell arms are probe-only (no imagery download). The cold "
            "candidate list is probed with early exit; the first union with a "
            "complete date wins, so the export can feed the unmodified A3 "
            "pilot driver."
        ),
    }
    _write_json(args.output_dir / "plan" / "plan.json", plan)
    return {
        "n_eligible_cells": len(cells),
        "full_cells": [r["grid_id"] for r in full_cells],
        "cold_candidates": [r["grid_id"] for r in cold_candidates],
    }


def _probe(
    region: Mapping[str, object],
    args: argparse.Namespace,
    runner,
) -> dict[str, object]:
    t0 = time.monotonic()
    rows = fetch_availability_for_anchor(
        region,
        zoom=args.zoom,
        provider=args.provider,
        min_date=args.min_date,
        max_date=args.max_date,
        parallel=args.parallel,
        complete=True,
        gehi_exe=args.gehi_exe,
        timeout=args.timeout,
        runner=runner,
    )
    elapsed = time.monotonic() - t0
    dates = sorted({str(r["capture_date"]) for r in rows})
    return {
        "grid_id": region["grid_id"],
        "region_anchor_id": region["anchor_id"],
        "n_complete_dates": len(dates),
        "dates": dates,
        "latest_complete_date": dates[-1] if dates else "",
        "elapsed_s": round(elapsed, 3),
    }


def stage_availability(args: argparse.Namespace, plan: dict[str, object]) -> dict[str, object]:
    limiter = GehiRateLimiter(min_interval_s=args.request_interval)
    runner = make_throttled_runner(base_runner=run_gehi, limiter=limiter, max_attempts=3)
    full_results = []
    for rec in plan["full_cells"]:
        result = _probe(rec["region_anchor"], args, runner)
        full_results.append(result)
        _write_json(args.output_dir / "availability" / f"full_{rec['grid_id']}.json", result)
    cold_results = []
    chosen: dict[str, object] | None = None
    for rec in plan["cold_candidates"]:
        result = _probe(rec["region_anchor"], args, runner)
        result["n_cell_members"] = rec["n_members"]
        cold_results.append(result)
        _write_json(args.output_dir / "availability" / f"cold_{rec['grid_id']}.json", result)
        if result["n_complete_dates"] > 0:
            chosen = {"grid_id": rec["grid_id"], **result}
            break  # early exit: first union with a complete date wins
    payload = {
        "full_cells": full_results,
        "cold_probed": cold_results,
        "cold_chosen": chosen,
    }
    _write_json(args.output_dir / "availability" / "summary.json", payload)
    return {
        "n_full_probed": len(full_results),
        "n_cold_probed": len(cold_results),
        "cold_chosen": chosen["grid_id"] if chosen else "",
    }


def stage_export(args: argparse.Namespace, plan: dict[str, object]) -> dict[str, object]:
    summary = json.loads(
        (args.output_dir / "availability" / "summary.json").read_text(encoding="utf-8")
    )
    chosen = summary.get("cold_chosen")
    if not chosen:
        raise SystemExit(
            "no cold candidate union reported a complete date; probe more "
            "candidates (--max-cold-candidates) before running download arms"
        )
    grid_id = str(chosen["grid_id"])
    capture_date = str(chosen["latest_complete_date"])
    anchors = read_csv_rows(args.anchors_csv)
    top52 = {
        str(row.get("grid_id", "")).strip()
        for row in read_csv_rows(args.top52_groups_csv)
    }
    cells = group_cells(anchors, exclude_grids=top52, min_members=args.cluster_n)
    members = cells[grid_id]
    groups_path = args.output_dir / "export" / f"{grid_id}_groups.csv"
    write_csv_rows(groups_path, members, list(members[0].keys()))
    pilot_cmd = [
        "python", "scripts/temporal/run_a3_region_batch_pilot.py",
        "--groups-csv", str(groups_path),
        "--output-dir", str(args.output_dir / "coldcell_pilot"),
        "--grids", grid_id,
        "--capture-date", capture_date,
        "--request-interval", str(args.request_interval),
        "--only", "plan", "availability", "sequential", "batch_dump",
        "compare_dump", "report_dump", "manifest_dump",
    ]
    payload = {
        "cold_grid_id": grid_id,
        "capture_date": capture_date,
        "n_cell_members": len(members),
        "groups_csv": str(groups_path),
        "pilot_command": " ".join(pilot_cmd),
        "full_cells": summary["full_cells"],
    }
    _write_json(args.output_dir / "export" / "probe_summary.json", payload)
    return payload


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--anchors-csv", type=Path, default=DEFAULT_ANCHORS)
    parser.add_argument("--top52-groups-csv", type=Path, default=DEFAULT_TOP52_GROUPS)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--provider", default="TM")
    parser.add_argument("--zoom", type=int, default=19)
    parser.add_argument("--cluster-n", type=int, default=12)
    parser.add_argument("--min-date", default="2019-01-01")
    parser.add_argument("--max-date", default="2025-12-31")
    parser.add_argument("--request-interval", type=float, default=2.0)
    parser.add_argument("--parallel", type=int, default=4)
    parser.add_argument("--timeout", type=float, default=600.0)
    parser.add_argument("--gehi-exe", type=Path, default=DEFAULT_GEHI_EXE)
    parser.add_argument(
        "--only",
        nargs="+",
        choices=STAGES,
        help="Run only these stages (still require earlier .done sentinels).",
    )
    parser.add_argument("--force", action="store_true", help="Redo stages even if .done exists.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    plan: dict[str, object] | None = None
    for stage, fn in (
        ("plan", lambda: stage_plan(args)),
        ("availability", lambda: stage_availability(args, plan or _load_plan(args.output_dir))),
        ("export", lambda: stage_export(args, plan or _load_plan(args.output_dir))),
    ):
        if not _want(args, stage):
            continue
        if _skip(args.output_dir, stage, args.force):
            print(f"[A3-probe] skip {stage} (done)", flush=True)
            if stage == "plan":
                plan = _load_plan(args.output_dir)
            continue
        print(f"[A3-probe] start {stage}", flush=True)
        t0 = time.monotonic()
        payload = fn()
        if stage == "plan":
            plan = _load_plan(args.output_dir)
        _mark_done(args.output_dir, stage, {"elapsed_s": round(time.monotonic() - t0, 3), **payload})
        print(f"[A3-probe] done {stage} {payload}", flush=True)


if __name__ == "__main__":
    main()
