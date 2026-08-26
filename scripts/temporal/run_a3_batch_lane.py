#!/usr/bin/env python3
"""A3 production batch lane: candidates -> cluster dumps -> per-anchor chips + QA.

This is the D5 (2026-08-19) production shape of the citywide download path:
per-anchor candidates are grouped into (grid_id, capture_date, provider, zoom)
cluster tasks; each task is ONE GEHI `dump` call, losslessly stitched, cropped
back to per-anchor chips (JPEG q=95, production-identical layout), and folded
into gehi_download-FIELDS manifest rows that the unchanged production
`quality_gate` consumes. Members of a failed cluster fall back to the
per-anchor sequential path; every failure stays a failure class in QA, never
an absence observation.

Stages (resumable; per-task sentinels under run/done/):
  plan      group candidates into cluster tasks (+ canary grid selection)
  run       per task: dump -> stitch -> crop; fallback sequential on failure
  manifest  synthesize per-anchor manifest.csv + batch provenance sidecar
  qa        production quality_gate over the full candidate denominator
  report    call/wall-clock/QA aggregate -> report/summary.json

FAIL-CLOSED GATE (D5): planning more than CANARY_GRID_CAP grids requires the
environment variable ENABLE_REGION_BATCH=true. The 10-grid canary runs
without it; citywide scale-out requires the owner's explicit post-canary
release. `--exact-date` is always on (dump_region_tiles); multi-date hole
filling would poison install-date inference.
"""

from __future__ import annotations

# ruff: noqa: E402

import argparse
import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Mapping, Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.temporal.gehi_common import (
    DEFAULT_GEHI_EXE,
    GehiRateLimiter,
    GehiRunResult,
    make_throttled_runner,
    run_gehi,
)
from scripts.temporal.gehi_download import (
    FIELDS as GEHI_DOWNLOAD_FIELDS,
    DownloadResult,
    build_chip_provenance,
    download_chip_with_zoom_ladder,
)
from scripts.temporal.gehi_region_batch import (
    DEFAULT_MOSAIC_PAD_DEG,
    DUMP_MANIFEST_SIDECAR_FIELDS,
    crop_chip_from_mosaic,
    dump_region_tiles,
    existing_chip_path,
    plan_cluster_tasks,
    region_anchor,
    select_canary_grids,
    stitch_dump_tiles,
    synthesize_dump_manifest,
    union_bbox,
)
from scripts.temporal.geid_temporal_common import read_csv_rows, write_csv_rows
from scripts.temporal.run_ct05_chip_pipeline import quality_gate

DEFAULT_ANCHORS = (
    Path.home()
    / "zasolar_data/geid_temporal/cape_town_citywide_backdating_v1_20260812"
    / "anchors_v1/anchors_all.csv"
)
STAGES = ("plan", "run", "manifest", "qa", "report")
CANARY_GRID_CAP = 10  # D5: >10 grids requires ENABLE_REGION_BATCH=true


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _load_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


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
    return _load_json(path)


def load_anchor_index(path: Path) -> dict[str, dict[str, object]]:
    """anchor_id -> row, with grid_id normalized from source_grid."""
    index: dict[str, dict[str, object]] = {}
    for row in read_csv_rows(path):
        item = dict(row)
        if not str(item.get("grid_id", "")).strip():
            item["grid_id"] = str(item.get("source_grid", "")).strip()
        index[str(item["anchor_id"])] = item
    return index


def _candidate_grid(row: Mapping[str, object], anchor_index: Mapping[str, Mapping[str, object]]) -> str:
    anchor = anchor_index.get(str(row.get("anchor_id", "")).strip())
    if anchor is None:
        raise ValueError(f"candidate anchor {row.get('anchor_id')!r} not in anchor index")
    return str(anchor.get("grid_id", "")).strip()


def stage_plan(args: argparse.Namespace) -> dict[str, object]:
    anchor_index = load_anchor_index(args.anchors_csv)
    candidates = read_csv_rows(args.candidates_csv)
    grid_counts: dict[str, int] = {}
    for row in candidates:
        grid = _candidate_grid(row, anchor_index)
        grid_counts[grid] = grid_counts.get(grid, 0) + 1
    if args.canary_grids:
        grids = select_canary_grids(grid_counts, n=args.canary_grids)
    elif args.grids:
        grids = [g.strip() for g in args.grids.split(",") if g.strip()]
        unknown = set(grids) - set(grid_counts)
        if unknown:
            raise SystemExit(f"--grids not present in candidates: {sorted(unknown)}")
    else:
        grids = sorted(grid_counts)
    if len(grids) > CANARY_GRID_CAP and os.environ.get("ENABLE_REGION_BATCH") != "true":
        raise SystemExit(
            f"FAIL-CLOSED (D5): plan covers {len(grids)} grids > canary cap "
            f"{CANARY_GRID_CAP}. Citywide scale-out needs the owner's explicit "
            "post-canary release, expressed as ENABLE_REGION_BATCH=true in the "
            "environment. Run with --canary-grids 10 or --grids for a bounded batch."
        )
    grid_set = set(grids)
    scoped = [r for r in candidates if _candidate_grid(r, anchor_index) in grid_set]
    tasks = plan_cluster_tasks(
        scoped,
        anchor_index,
        provider=args.provider or None,
        zoom=args.zoom or None,
    )
    if args.limit_tasks:
        tasks = tasks[: args.limit_tasks]
    # Per-member version map for the manifest stage (TM version is deliberately
    # empty; collapse never happens by version — AGENTS.md rule 7).
    versions: dict[str, dict[str, str]] = {}
    for row in scoped:
        key = str(row["anchor_id"])
        versions.setdefault(key, {})[str(row["capture_date"])[:10]] = str(
            row.get("version", "")
        )
    for task in tasks:
        task["member_versions"] = {
            aid: versions.get(aid, {}).get(task["capture_date"], "")
            for aid in task["anchor_ids"]
        }
    _write_json(args.output_dir / "plan" / "tasks.json", tasks)
    write_csv_rows(
        args.output_dir / "plan" / "tasks.csv",
        [
            {
                "task_id": t["task_id"],
                "grid_id": t["grid_id"],
                "capture_date": t["capture_date"],
                "provider": t["provider"],
                "zoom": t["zoom"],
                "n_members": t["n_members"],
                "union_width_m": round(t["union"]["width_m"], 1),
                "union_height_m": round(t["union"]["height_m"], 1),
                "padded_tiles_z19": t["padded_tile_estimate_z19"]["n_tiles"],
            }
            for t in tasks
        ],
        [
            "task_id", "grid_id", "capture_date", "provider", "zoom",
            "n_members", "union_width_m", "union_height_m", "padded_tiles_z19",
        ],
    )
    plan = {
        "schema_version": "a3_batch_lane_plan_v1",
        "created_at": utc_now(),
        "candidates_csv": str(args.candidates_csv),
        "anchors_csv": str(args.anchors_csv),
        "provider_filter": args.provider,
        "zoom_filter": args.zoom,
        "grids": grids,
        "canary_grids_arg": args.canary_grids,
        "enable_region_batch_env": os.environ.get("ENABLE_REGION_BATCH", ""),
        "n_candidates_scoped": len(scoped),
        "n_tasks": len(tasks),
        "n_members_total": sum(int(t["n_members"]) for t in tasks),
        "sequential_baseline_calls": len(scoped),
        "notes": (
            "One dump call per task vs one call per candidate row in the "
            "sequential path. Tasks are (grid, capture_date, provider, zoom); "
            "members keep their own version."
        ),
    }
    _write_json(args.output_dir / "plan" / "plan.json", plan)
    return {
        "n_grids": len(grids),
        "n_tasks": len(tasks),
        "n_candidates_scoped": len(scoped),
        "grids": grids,
    }


def _fallback_ladder(zoom: int) -> tuple[int, ...]:
    return (19, 18) if zoom == 19 else (18,)


def run_one_task(
    task: Mapping[str, object],
    anchor_index: Mapping[str, Mapping[str, object]],
    args: argparse.Namespace,
    *,
    dump_runner: Callable[..., GehiRunResult] | None = None,
    seq_runner: Callable[..., GehiRunResult] | None = None,
    limiter: GehiRateLimiter | None = None,
    raw_log_callback: Callable[[Mapping[str, object]], None] | None = None,
) -> dict[str, object]:
    """Execute one cluster task: dump -> stitch -> crop, fallback sequential.

    Resumability/idempotence is per task: dump_region_tiles reuses a non-empty
    tile folder, and an existing crop file is left untouched (crop is
    deterministic). Fallback downloads use the production per-anchor path and
    are skipped for members that already have an ok crop.
    """
    task_id = str(task["task_id"])
    zoom = int(task["zoom"])
    date = str(task["capture_date"])
    versions: Mapping[str, str] = task.get("member_versions", {})  # type: ignore[assignment]
    members = [anchor_index[aid] for aid in task["anchor_ids"]]
    region = region_anchor(grid_id=str(task["grid_id"]), capture_date=date, bbox=union_bbox(members))
    padded = union_bbox([region]).padded(DEFAULT_MOSAIC_PAD_DEG)
    region.update(padded.as_anchor_fields())

    tile_dir = args.output_dir / "run" / "tiles" / task_id
    mosaic_path = args.output_dir / "run" / "mosaics" / f"{task_id}_mosaic_z{zoom}_uncompressed.tif"
    chip_root = args.output_dir / "chips"

    t0 = time.monotonic()
    outcome = dump_region_tiles(
        region,
        capture_date=date,
        zoom=zoom,
        output_dir=tile_dir,
        provider=str(task["provider"]),
        gehi_exe=args.gehi_exe,
        parallel=args.parallel,
        timeout=args.timeout,
        limiter=limiter,
        max_attempts=args.max_attempts,
        raw_log_callback=raw_log_callback,
        runner=dump_runner,
        overwrite=False,
    )
    dump_s = time.monotonic() - t0
    dump_invocations = 0 if outcome.status == "reused_existing" else (
        1 if outcome.status == "ok" else 0
    )

    mosaic_info: dict[str, object] | None = None
    stitch_s = 0.0
    stitch_error = ""
    if outcome.status in {"ok", "reused_existing"}:
        t1 = time.monotonic()
        try:
            # allow_holes: GEHI dump silently skips tiles the vintage does not
            # cover (canary 2026-08-19: 8/285 holes in non-member rectangle
            # areas). Hole-intersecting crops are rejected below and only
            # those members fall back — not the whole cluster.
            mosaic_info = stitch_dump_tiles(
                tile_dir, mosaic_path, zoom=zoom, allow_holes=True
            )
        except Exception as exc:  # noqa: BLE001 - inconsistency -> fallback below
            stitch_error = f"{type(exc).__name__}: {exc}"
        stitch_s = time.monotonic() - t1

    crops: list[dict[str, object]] = []
    crop_s = 0.0
    if mosaic_info is not None:
        t2 = time.monotonic()
        for member in members:
            aid = str(member["anchor_id"])
            version = str(versions.get(aid, ""))
            out_path = existing_chip_path(
                chip_root, aid, date, version=version or "noversion", zoom=zoom
            )
            try:
                crop = crop_chip_from_mosaic(
                    mosaic_path,
                    member,
                    out_path,
                    recompress=not args.no_recompress,
                    hole_cells=mosaic_info.get("holes") or None,
                    hole_tile_px=mosaic_info.get("hole_tile_px"),
                )
                crop["status"] = "ok"
            except Exception as exc:  # noqa: BLE001 - record; fallback below
                crop = {
                    "anchor_id": aid,
                    "path": str(out_path),
                    "status": "crop_failed",
                    "error": f"{type(exc).__name__}: {exc}",
                }
            crops.append(crop)
        crop_s = time.monotonic() - t2

    crop_ok_ids = {str(c["anchor_id"]) for c in crops if c.get("status") == "ok"}
    pending = [m for m in members if str(m["anchor_id"]) not in crop_ok_ids]
    fallbacks: list[dict[str, object]] = []
    fallback_s = 0.0
    fallback_invocations = 0
    if pending and not args.no_fallback:
        t3 = time.monotonic()
        for member in pending:
            aid = str(member["anchor_id"])
            version = str(versions.get(aid, ""))
            outcome_fb = download_chip_with_zoom_ladder(
                member,
                capture_date=date,
                version=version,
                zoom_ladder=_fallback_ladder(zoom),
                output_root=chip_root,
                provider=str(task["provider"]),
                gehi_exe=args.gehi_exe,
                parallel=args.parallel,
                timeout=args.timeout,
                limiter=limiter,
                max_attempts=args.max_attempts,
                raw_log_callback=raw_log_callback,
                runner=seq_runner,
                recompress=not args.no_recompress,
            )
            if outcome_fb.status != "skipped_existing":
                fallback_invocations += 1
            fallbacks.append(
                {
                    "anchor_id": aid,
                    "status": outcome_fb.status,
                    "error": outcome_fb.error,
                    "path": str(outcome_fb.path) if outcome_fb.path else "",
                    "actual_zoom": outcome_fb.actual_zoom,
                    "sha256": outcome_fb.sha256,
                    "gehi_command": outcome_fb.gehi_command,
                    "download_stdout_sha256": outcome_fb.download_stdout_sha256,
                }
            )
        fallback_s = time.monotonic() - t3

    rec = {
        "task_id": task_id,
        "grid_id": task["grid_id"],
        "capture_date": date,
        "provider": task["provider"],
        "zoom": zoom,
        "n_members": len(members),
        "dump_status": outcome.status,
        "dump_error": outcome.error,
        "n_tiles": outcome.n_tiles,
        "tile_dir": str(tile_dir),
        "mosaic_path": str(mosaic_path) if mosaic_info else "",
        "mosaic": mosaic_info,
        "stitch_error": stitch_error,
        "gehi_command": outcome.gehi_command,
        "dump_stdout_sha256": outcome.stdout_sha256,
        "dump_s": round(dump_s, 3),
        "stitch_s": round(stitch_s, 3),
        "crop_s": round(crop_s, 3),
        "fallback_s": round(fallback_s, 3),
        "n_crops_ok": len(crop_ok_ids),
        "n_crop_failed": len(crops) - len(crop_ok_ids),
        "n_fallback": len(fallbacks),
        "n_fallback_ok": sum(
            1 for f in fallbacks if str(f["status"]) in {"ok", "skipped_existing"}
        ),
        "invocations_dump": dump_invocations,
        "invocations_fallback": fallback_invocations,
        "crops": crops,
        "fallbacks": fallbacks,
    }
    return rec


def stage_run(
    args: argparse.Namespace,
    plan: dict[str, object],
    *,
    dump_runner: Callable[..., GehiRunResult] | None = None,
    seq_runner: Callable[..., GehiRunResult] | None = None,
) -> dict[str, object]:
    anchor_index = load_anchor_index(args.anchors_csv)
    tasks = _load_json(args.output_dir / "plan" / "tasks.json")
    if dump_runner is None:
        limiter: GehiRateLimiter | None = GehiRateLimiter(min_interval_s=args.request_interval)
    else:
        limiter = None  # injected runners (tests) must not pay real pacing
    raw_log = args.output_dir / "run" / "raw.jsonl"
    raw_log.parent.mkdir(parents=True, exist_ok=True)
    done_dir = args.output_dir / "run" / "done"
    n_new = 0
    with raw_log.open("a", encoding="utf-8") as log_fh:
        def _log(payload: Mapping[str, object]) -> None:
            log_fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
            log_fh.flush()

        for task in tasks:
            task_id = str(task["task_id"])
            sentinel = done_dir / f"{task_id}.json"
            if sentinel.is_file() and not args.force:
                continue
            rec = run_one_task(
                task,
                anchor_index,
                args,
                dump_runner=dump_runner,
                seq_runner=seq_runner,
                limiter=limiter,
                raw_log_callback=_log,
            )
            _write_json(sentinel, rec)
            n_new += 1
            print(
                f"[A3-lane] {task_id}: dump={rec['dump_status']} "
                f"crops={rec['n_crops_ok']}/{rec['n_members']} "
                f"fallback={rec['n_fallback']} ({rec['dump_s']}s)",
                flush=True,
            )
    recs = [_load_json(p) for p in sorted(done_dir.glob("*.json"))]
    summary = {
        "n_tasks": len(recs),
        "n_run_this_stage": n_new,
        "n_dump_ok": sum(1 for r in recs if r["dump_status"] in {"ok", "reused_existing"}),
        "n_dump_failed": sum(1 for r in recs if r["dump_status"] == "failed"),
        "n_stitch_failed": sum(1 for r in recs if r["stitch_error"]),
        "n_crops_ok": sum(int(r["n_crops_ok"]) for r in recs),
        "n_crop_failed": sum(int(r["n_crop_failed"]) for r in recs),
        "n_fallback": sum(int(r["n_fallback"]) for r in recs),
        "n_fallback_ok": sum(int(r["n_fallback_ok"]) for r in recs),
        "n_tasks_with_holes": sum(
            1 for r in recs if (r.get("mosaic") or {}).get("n_holes")
        ),
        "n_holes_total": sum(
            int((r.get("mosaic") or {}).get("n_holes") or 0) for r in recs
        ),
        "invocations_dump": sum(int(r["invocations_dump"]) for r in recs),
        "invocations_fallback": sum(int(r["invocations_fallback"]) for r in recs),
        "dump_s": round(sum(float(r["dump_s"]) for r in recs), 3),
        "stitch_s": round(sum(float(r["stitch_s"]) for r in recs), 3),
        "crop_s": round(sum(float(r["crop_s"]) for r in recs), 3),
        "fallback_s": round(sum(float(r["fallback_s"]) for r in recs), 3),
    }
    _write_json(args.output_dir / "run" / "summary.json", summary)
    return summary


def _fallback_manifest_row(
    fb: Mapping[str, object],
    anchor: Mapping[str, object],
    *,
    capture_date: str,
    version: str,
    provider: str,
    requested_zoom: int,
) -> dict[str, object]:
    """gehi_download-FIELDS row for a sequential-fallback chip."""
    ok = str(fb["status"]) in {"ok", "skipped_existing"}
    actual_zoom = fb.get("actual_zoom")
    outcome = DownloadResult(
        anchor_id=str(fb["anchor_id"]),
        capture_date=capture_date,
        version=version,
        requested_zoom_ladder=_fallback_ladder(requested_zoom),
        actual_zoom=int(actual_zoom) if isinstance(actual_zoom, int) else None,
        path=Path(str(fb["path"])) if ok and fb.get("path") else None,
        sha256=str(fb.get("sha256", "")) if ok else "",
        status=str(fb["status"]) if ok else f"fallback_failed: {fb.get('error') or ''}",
        error=None if ok else str(fb.get("error") or ""),
        gehi_command=str(fb.get("gehi_command", "")),
        download_stdout_sha256=str(fb.get("download_stdout_sha256", "")),
    )
    provenance = build_chip_provenance(outcome, anchor, provider)
    artifact_id = hashlib.sha1(
        f"{outcome.anchor_id}|{outcome.actual_zoom or ''}|{capture_date}|{version}".encode("utf-8")
    ).hexdigest()[:16]
    row = {
        "artifact_id": artifact_id,
        "anchor_id": outcome.anchor_id,
        "region_key": anchor.get("region_key", ""),
        "grid_id": anchor.get("grid_id", ""),
        "provider": provider,
        "zoom": requested_zoom,
        "actual_zoom": outcome.actual_zoom if outcome.actual_zoom is not None else "",
        "requested_zoom_ladder": ",".join(str(z) for z in outcome.requested_zoom_ladder),
        "capture_date": capture_date,
        "version": version,
        "path": str(outcome.path) if outcome.path else "",
        "sha256": outcome.sha256,
        "status": outcome.status,
        "exact_date": 1,
        "download_stdout_sha256": outcome.download_stdout_sha256,
        "gehi_command": outcome.gehi_command,
        "raster_width_px": provenance["raster_width_px"] if ok else "",
        "raster_height_px": provenance["raster_height_px"] if ok else "",
        "raster_crs": provenance["raster_crs"] if ok else "",
        "extent_minx": provenance["extent_minx"] if ok else "",
        "extent_miny": provenance["extent_miny"] if ok else "",
        "extent_maxx": provenance["extent_maxx"] if ok else "",
        "extent_maxy": provenance["extent_maxy"] if ok else "",
        "gsd_x_m": provenance["gsd_x_m"] if ok else "",
        "gsd_y_m": provenance["gsd_y_m"] if ok else "",
        "raster_error": provenance["raster_error"] if ok else str(fb.get("error") or ""),
    }
    if list(row.keys()) != GEHI_DOWNLOAD_FIELDS:
        raise ValueError("fallback manifest row drifted from gehi_download.FIELDS")
    return row


def stage_manifest(args: argparse.Namespace, plan: dict[str, object]) -> dict[str, object]:
    anchor_index = load_anchor_index(args.anchors_csv)
    tasks = {str(t["task_id"]): t for t in _load_json(args.output_dir / "plan" / "tasks.json")}
    done_dir = args.output_dir / "run" / "done"
    recs = [_load_json(p) for p in sorted(done_dir.glob("*.json"))]
    if not recs:
        raise SystemExit("no run records found (run stage first)")
    manifest_rows: list[dict[str, object]] = []
    sidecar_rows: list[dict[str, object]] = []
    for rec in recs:
        task = tasks[str(rec["task_id"])]
        versions: Mapping[str, str] = task.get("member_versions", {})  # type: ignore[assignment]
        members = [anchor_index[aid] for aid in task["anchor_ids"]]
        fallback_ids = {str(f["anchor_id"]) for f in rec.get("fallbacks", [])}
        dump_members = [m for m in members if str(m["anchor_id"]) not in fallback_ids]
        crops_by_anchor = {str(c["anchor_id"]): c for c in rec.get("crops", [])}
        # synthesize_dump_manifest takes one version per call; group by it
        # (wave candidates are single-version in practice).
        by_version: dict[str, list[Mapping[str, object]]] = {}
        for m in dump_members:
            by_version.setdefault(str(versions.get(str(m["anchor_id"]), "")), []).append(m)
        for version, group in sorted(by_version.items()):
            rows, side = synthesize_dump_manifest(
                group,
                crops_by_anchor,
                capture_date=str(rec["capture_date"]),
                version=version,
                provider=str(rec["provider"]),
                zoom=int(rec["zoom"]),
                region_id=str(rec["task_id"]),
                tile_dir=str(rec.get("tile_dir", "")),
                n_tiles=rec.get("n_tiles", ""),
                mosaic_path=str(rec.get("mosaic_path", "")),
                dump_gehi_command=str(rec.get("gehi_command", "")),
                dump_stdout_sha256=str(rec.get("dump_stdout_sha256", "")),
            )
            manifest_rows.extend(rows)
            sidecar_rows.extend(side)
        for fb in rec.get("fallbacks", []):
            aid = str(fb["anchor_id"])
            manifest_rows.append(
                _fallback_manifest_row(
                    fb,
                    anchor_index[aid],
                    capture_date=str(rec["capture_date"]),
                    version=str(versions.get(aid, "")),
                    provider=str(rec["provider"]),
                    requested_zoom=int(rec["zoom"]),
                )
            )
            sidecar_rows.append(
                {
                    "anchor_id": aid,
                    "capture_date": str(rec["capture_date"]),
                    "version": str(versions.get(aid, "")),
                    "provider": str(rec["provider"]),
                    "region_id": str(rec["task_id"]),
                    "grid_id": str(rec["grid_id"]),
                    "tile_dir": "",
                    "n_tiles": "",
                    "mosaic_path": "",
                    "mosaic_sha256": "",
                    "crop_path": str(fb.get("path", "")),
                    "crop_sha256": str(fb.get("sha256", "")),
                    "crop_status": f"sequential_fallback:{fb['status']}",
                    "dump_gehi_command": str(fb.get("gehi_command", "")),
                    "dump_stdout_sha256": str(fb.get("download_stdout_sha256", "")),
                }
            )
    manifest_path = args.output_dir / "manifest" / "manifest.csv"
    write_csv_rows(manifest_path, manifest_rows, GEHI_DOWNLOAD_FIELDS)
    write_csv_rows(
        args.output_dir / "manifest" / "manifest_sidecar.csv",
        sidecar_rows,
        list(DUMP_MANIFEST_SIDECAR_FIELDS),
    )
    payload = {
        "n_manifest_rows": len(manifest_rows),
        "n_ok": sum(1 for r in manifest_rows if r["status"] in {"ok", "skipped_existing"}),
        "n_failed": sum(1 for r in manifest_rows if r["status"] not in {"ok", "skipped_existing"}),
        "manifest": str(manifest_path),
    }
    _write_json(args.output_dir / "manifest" / "summary.json", payload)
    return payload


def stage_qa(args: argparse.Namespace, plan: dict[str, object]) -> dict[str, object]:
    anchor_index = load_anchor_index(args.anchors_csv)
    grid_set = set(str(g) for g in plan["grids"])
    candidates = [
        r
        for r in read_csv_rows(args.candidates_csv)
        if _candidate_grid(r, anchor_index) in grid_set
        and (not args.provider or str(r.get("provider", "")) == args.provider)
        and (not args.zoom or int(str(r.get("requested_zoom", 0))) == args.zoom)
    ]
    anchors = list(anchor_index.values())
    manifest_path = args.output_dir / "manifest" / "manifest.csv"
    summary = quality_gate(candidates, anchors, [manifest_path], args.output_dir / "qa")
    _write_json(args.output_dir / "qa" / "lane_summary.json", summary)
    return summary


def stage_report(args: argparse.Namespace, plan: dict[str, object]) -> dict[str, object]:
    run_summary = _load_json(args.output_dir / "run" / "summary.json")
    qa_summary = _load_json(args.output_dir / "qa" / "lane_summary.json")
    n_candidates = int(plan["sequential_baseline_calls"])
    invocations = int(run_summary["invocations_dump"]) + int(run_summary["invocations_fallback"])
    batch_wall = (
        float(run_summary["dump_s"])
        + float(run_summary["stitch_s"])
        + float(run_summary["crop_s"])
        + float(run_summary["fallback_s"])
    )
    report = {
        "schema_version": "a3_batch_lane_report_v1",
        "finished_at": utc_now(),
        "grids": plan["grids"],
        "n_candidates": n_candidates,
        "n_tasks": run_summary["n_tasks"],
        "invocations_total": invocations,
        "invocations_dump": run_summary["invocations_dump"],
        "invocations_fallback": run_summary["invocations_fallback"],
        "sequential_baseline_calls": n_candidates,
        "call_reduction": round(n_candidates / invocations, 2) if invocations else None,
        "batch_wall_s": round(batch_wall, 3),
        "sequential_est_wall_s": round(n_candidates * args.request_interval, 1),
        "dump_ok_rate": (
            round(run_summary["n_dump_ok"] / run_summary["n_tasks"], 4)
            if run_summary["n_tasks"] else None
        ),
        "crop_ok": run_summary["n_crops_ok"],
        "crop_failed": run_summary["n_crop_failed"],
        "fallback": run_summary["n_fallback"],
        "fallback_ok": run_summary["n_fallback_ok"],
        "tasks_with_holes": run_summary.get("n_tasks_with_holes", 0),
        "holes_total": run_summary.get("n_holes_total", 0),
        "qa_release_eligible": qa_summary["release_eligible_count"],
        "qa_candidate_count": qa_summary["candidate_count"],
        "qa_release_rate": (
            round(qa_summary["release_eligible_count"] / qa_summary["candidate_count"], 4)
            if qa_summary["candidate_count"] else None
        ),
        "qa_failure_classes": qa_summary["failure_classes"],
        "enable_region_batch_env": os.environ.get("ENABLE_REGION_BATCH", ""),
        "notes": [
            "D5 canary shape: dump arm replaces the per-anchor path; sequential "
            "only fires as per-cluster fallback.",
            "sequential_est_wall_s = candidates x request-interval; the "
            "measured cold-tax per call is small next to the interval (RUN "
            "a3-fullcell-coldcell-probe-2026-08-19 §3).",
        ],
    }
    _write_json(args.output_dir / "report" / "summary.json", report)
    print(json.dumps(report, indent=2, sort_keys=True))
    return report


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidates-csv", type=Path, required=True)
    parser.add_argument("--anchors-csv", type=Path, default=DEFAULT_ANCHORS)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--provider", default="TM", help="'' disables the filter")
    parser.add_argument("--zoom", type=int, default=19, help="0 disables the filter")
    parser.add_argument("--canary-grids", type=int, default=0,
                        help="stratified N-grid selection from the candidate population")
    parser.add_argument("--grids", default="", help="explicit comma-separated grid ids")
    parser.add_argument("--limit-tasks", type=int, default=0, help="debug: first N tasks only")
    parser.add_argument("--request-interval", type=float, default=2.0)
    parser.add_argument("--parallel", type=int, default=4)
    parser.add_argument("--timeout", type=float, default=600.0)
    parser.add_argument("--max-attempts", type=int, default=3)
    parser.add_argument("--gehi-exe", type=Path, default=DEFAULT_GEHI_EXE)
    parser.add_argument("--no-fallback", action="store_true",
                        help="do not sequential-download members of failed clusters")
    parser.add_argument("--no-recompress", action="store_true",
                        help="skip the q=95 JPEG recompress on crops (test hook)")
    parser.add_argument("--only", nargs="+", choices=STAGES)
    parser.add_argument("--force", action="store_true")
    return parser.parse_args(argv)


def main() -> None:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    plan: dict[str, object] | None = None
    for stage, fn in (
        ("plan", lambda: stage_plan(args)),
        ("run", lambda: stage_run(args, plan or _load_plan(args.output_dir))),
        ("manifest", lambda: stage_manifest(args, plan or _load_plan(args.output_dir))),
        ("qa", lambda: stage_qa(args, plan or _load_plan(args.output_dir))),
        ("report", lambda: stage_report(args, plan or _load_plan(args.output_dir))),
    ):
        if not _want(args, stage):
            continue
        if stage != "plan":
            plan = plan or _load_plan(args.output_dir)
        if _skip(args.output_dir, stage, args.force):
            print(f"[A3-lane] skip {stage} (done)", flush=True)
            if stage == "plan":
                plan = _load_plan(args.output_dir)
            continue
        print(f"[A3-lane] start {stage}", flush=True)
        t0 = time.monotonic()
        payload = fn()
        if stage == "plan":
            plan = _load_plan(args.output_dir)
        _mark_done(args.output_dir, stage, {"elapsed_s": round(time.monotonic() - t0, 3), **payload})
        print(f"[A3-lane] done {stage}", flush=True)


if __name__ == "__main__":
    main()
