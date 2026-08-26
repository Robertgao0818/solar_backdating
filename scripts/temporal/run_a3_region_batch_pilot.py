#!/usr/bin/env python3
"""Bounded A3 region-batch pilot: ≤2 grids, one shared TM vintage, crop vs sequential.

Does not touch the citywide download lane. Writes everything under
~/zasolar_data/geid_temporal/a3_region_batch_pilot_YYYYMMDD/.

Stages (resumable via .done sentinels):
  plan          select centroid clusters and write plan.json
  availability  GEHI availability --complete on each union bbox
  batch         one GEHI download per (grid, date), then local crops
  sequential    same chips via the production per-anchor path (timing arm)
  compare       batch crops vs same-run sequential + existing Top-52 chips
  report        summary.json + GO/NO-GO against the prereg bars
  batch_dump    dump arm: GEHI dump tiles -> lossless stitch -> crops (q=95)
  compare_dump  dump crops vs same-run sequential + QA
  report_dump   dump arm summary.json + GO/NO-GO against the same prereg bars
  manifest_dump fold dump crops into gehi_download-FIELDS per-anchor manifest
                rows + provenance sidecar, then self-check through the
                production quality_gate (offline; closes the handoff §8
                'manifest synthesis' open question)

The dump arm was added after the 2026-08-18 run showed `download` JPEG-
compresses the whole mosaic (0.5.1 cannot disable it), which alone broke the
MAE<=5@90% bar; a hand-run dump correction on CPT2713 passed it. See
HANDOFF-a3-region-batch-2026-08-19.md §7 steps 1-2.
"""

from __future__ import annotations

# ruff: noqa: E402

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

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
from scripts.temporal.gehi_download import (
    FIELDS as GEHI_DOWNLOAD_FIELDS,
    download_chip_with_zoom_ladder,
)
from scripts.temporal.gehi_region_batch import (
    DEFAULT_MOSAIC_PAD_DEG,
    DUMP_MANIFEST_SIDECAR_FIELDS,
    cluster_waste,
    compare_chips,
    crop_chip_from_mosaic,
    dump_region_tiles,
    existing_chip_path,
    region_anchor,
    select_centroid_cluster,
    stitch_dump_tiles,
    synthesize_dump_manifest,
    union_bbox,
)
from scripts.temporal.geid_temporal_common import read_csv_rows, write_csv_rows
from scripts.temporal.run_ct05_chip_pipeline import inspect_raster, quality_gate

DEFAULT_GROUPS = (
    Path.home()
    / "zasolar_data/geid_temporal/cape_town_top52_backdating_v1_20260724"
    / "groups_v1/chip_groups_as_anchors.csv"
)
DEFAULT_EXISTING_CHIPS = (
    Path.home()
    / "zasolar_data/geid_temporal/cape_town_top52_backdating_v1_20260724"
    / "ct05_download_v1/chips"
)
DEFAULT_OUTPUT = (
    Path.home() / "zasolar_data/geid_temporal/a3_region_batch_pilot_20260818"
)
DEFAULT_GRIDS = ("CPT2713", "CPT3584")
DEFAULT_DATE = "2019-05-30"
DEFAULT_VERSION = "noversion"
DEFAULT_CLUSTER_N = 12
STAGES = (
    "plan",
    "availability",
    "batch",
    "sequential",
    "compare",
    "report",
    "batch_dump",
    "compare_dump",
    "report_dump",
    "manifest_dump",
)


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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--groups-csv", type=Path, default=DEFAULT_GROUPS)
    parser.add_argument("--existing-chips", type=Path, default=DEFAULT_EXISTING_CHIPS)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--grids", default=",".join(DEFAULT_GRIDS))
    parser.add_argument("--capture-date", default=DEFAULT_DATE)
    parser.add_argument("--version", default=DEFAULT_VERSION)
    parser.add_argument("--cluster-n", type=int, default=DEFAULT_CLUSTER_N)
    parser.add_argument("--zoom", type=int, default=19)
    parser.add_argument("--provider", default="TM")
    parser.add_argument("--gehi-exe", type=Path, default=DEFAULT_GEHI_EXE)
    parser.add_argument("--request-interval", type=float, default=2.0)
    parser.add_argument("--parallel", type=int, default=4)
    parser.add_argument("--timeout", type=float, default=600.0)
    parser.add_argument(
        "--only",
        nargs="+",
        choices=STAGES,
        help="Run only these stages (still require earlier .done sentinels).",
    )
    parser.add_argument("--force", action="store_true", help="Redo stages even if .done exists.")
    return parser.parse_args()


def _want(args: argparse.Namespace, stage: str) -> bool:
    return args.only is None or stage in args.only


def _skip(root: Path, stage: str, force: bool) -> bool:
    return (not force) and _stage_done(root, stage).exists()


def stage_plan(args: argparse.Namespace) -> dict[str, object]:
    groups = read_csv_rows(args.groups_csv)
    grids = [g.strip() for g in args.grids.split(",") if g.strip()]
    if not 1 <= len(grids) <= 2:
        raise SystemExit(f"A3 pilot allows 1 or 2 grids, got {grids!r}")
    clusters: list[dict[str, object]] = []
    missing_existing = 0
    for grid_id in grids:
        members = [row for row in groups if str(row.get("grid_id", "")) == grid_id]
        if not members:
            raise SystemExit(f"no anchors for grid {grid_id} in {args.groups_csv}")
        cluster = select_centroid_cluster(members, n=args.cluster_n)
        for row in cluster:
            chip = existing_chip_path(
                args.existing_chips,
                str(row["anchor_id"]),
                args.capture_date,
                version=args.version,
                zoom=args.zoom,
            )
            row["existing_chip"] = str(chip)
            if not chip.is_file():
                missing_existing += 1
        bbox = union_bbox(cluster)
        waste = cluster_waste(cluster)
        region = region_anchor(
            grid_id=grid_id, capture_date=args.capture_date, bbox=bbox
        )
        clusters.append(
            {
                "grid_id": grid_id,
                "n_grid_anchors": len(members),
                "n_cluster": len(cluster),
                "capture_date": args.capture_date,
                "version": args.version,
                "provider": args.provider,
                "zoom": args.zoom,
                "region_anchor": region,
                "union": waste,
                "anchor_ids": [str(r["anchor_id"]) for r in cluster],
                "anchors": cluster,
            }
        )
        write_csv_rows(
            args.output_dir / "plan" / f"{grid_id}_cluster.csv",
            cluster,
            list(cluster[0].keys()),
        )
    plan = {
        "schema_version": "a3_region_batch_pilot_v1",
        "created_at": utc_now(),
        "grids": grids,
        "capture_date": args.capture_date,
        "version": args.version,
        "provider": args.provider,
        "zoom": args.zoom,
        "cluster_n": args.cluster_n,
        "request_interval_s": args.request_interval,
        "groups_csv": str(args.groups_csv),
        "existing_chips": str(args.existing_chips),
        "missing_existing_chips": missing_existing,
        "clusters": clusters,
        "notes": (
            "Top-52 2019-05-30 tiles are expected cache-hot. This isolates "
            "process+interval tax, not cold-cache network bytes."
        ),
    }
    _write_json(args.output_dir / "plan" / "plan.json", plan)
    return {"n_clusters": len(clusters), "missing_existing_chips": missing_existing}


def _load_plan(root: Path) -> dict[str, object]:
    path = root / "plan" / "plan.json"
    if not path.is_file():
        raise SystemExit(f"plan.json missing: {path} (run the plan stage first)")
    return json.loads(path.read_text(encoding="utf-8"))


def stage_availability(args: argparse.Namespace, plan: dict[str, object]) -> dict[str, object]:
    limiter = GehiRateLimiter(min_interval_s=args.request_interval)
    runner = make_throttled_runner(base_runner=run_gehi, limiter=limiter, max_attempts=3)
    rows: list[dict[str, object]] = []
    for cluster in plan["clusters"]:
        region = cluster["region_anchor"]
        t0 = time.monotonic()
        avail_rows = fetch_availability_for_anchor(
            region,
            zoom=int(plan["zoom"]),
            provider=str(plan["provider"]),
            min_date="2019-01-01",
            max_date="2025-12-31",
            parallel=args.parallel,
            complete=True,
            gehi_exe=args.gehi_exe,
            timeout=args.timeout,
            runner=runner,
        )
        elapsed = time.monotonic() - t0
        dates = sorted({str(r["capture_date"]) for r in avail_rows})
        target = str(plan["capture_date"])
        rec = {
            "grid_id": cluster["grid_id"],
            "region_anchor_id": region["anchor_id"],
            "target_date": target,
            "target_complete_on_union": int(target in dates),
            "n_complete_dates": len(dates),
            "elapsed_s": round(elapsed, 3),
            "dates": dates,
        }
        rows.append(rec)
        _write_json(args.output_dir / "availability" / f"{cluster['grid_id']}.json", rec)
    _write_json(args.output_dir / "availability" / "summary.json", rows)
    return {
        "n_unions": len(rows),
        "n_target_complete": sum(int(r["target_complete_on_union"]) for r in rows),
    }


def stage_batch(args: argparse.Namespace, plan: dict[str, object]) -> dict[str, object]:
    limiter = GehiRateLimiter(min_interval_s=args.request_interval)
    mosaic_root = args.output_dir / "batch" / "mosaics"
    crop_root = args.output_dir / "batch" / "chips"
    raw_log = args.output_dir / "batch" / "raw.jsonl"
    raw_log.parent.mkdir(parents=True, exist_ok=True)
    summaries: list[dict[str, object]] = []
    with raw_log.open("w", encoding="utf-8") as log_fh:
        def _log(payload: Mapping[str, object]) -> None:
            log_fh.write(json.dumps(payload, ensure_ascii=False) + "\n")

        for cluster in plan["clusters"]:
            region = dict(cluster["region_anchor"])
            padded = union_bbox([region]).padded(DEFAULT_MOSAIC_PAD_DEG)
            region.update(padded.as_anchor_fields())
            t0 = time.monotonic()
            outcome = download_chip_with_zoom_ladder(
                region,
                capture_date=str(plan["capture_date"]),
                version=str(plan["version"]),
                zoom_ladder=(int(plan["zoom"]),),
                output_root=mosaic_root,
                provider=str(plan["provider"]),
                gehi_exe=args.gehi_exe,
                parallel=args.parallel,
                timeout=args.timeout,
                limiter=limiter,
                max_attempts=3,
                raw_log_callback=_log,
                recompress=False,
                overwrite=args.force,
            )
            download_s = time.monotonic() - t0
            crops: list[dict[str, object]] = []
            crop_errors = 0
            t1 = time.monotonic()
            if outcome.status in {"ok", "skipped_existing"} and outcome.path is not None:
                for anchor in cluster["anchors"]:
                    out_path = existing_chip_path(
                        crop_root,
                        str(anchor["anchor_id"]),
                        str(plan["capture_date"]),
                        version=str(plan["version"]),
                        zoom=int(plan["zoom"]),
                    )
                    try:
                        crop = crop_chip_from_mosaic(
                            outcome.path, anchor, out_path, recompress=True
                        )
                        crop["status"] = "ok"
                    except Exception as exc:  # noqa: BLE001 - record and continue
                        crop = {
                            "anchor_id": str(anchor["anchor_id"]),
                            "path": str(out_path),
                            "status": "crop_failed",
                            "error": f"{type(exc).__name__}: {exc}",
                        }
                        crop_errors += 1
                    crops.append(crop)
            crop_s = time.monotonic() - t1
            rec = {
                "grid_id": cluster["grid_id"],
                "download_status": outcome.status,
                "download_error": outcome.error,
                "mosaic_path": str(outcome.path) if outcome.path else "",
                "gehi_command": outcome.gehi_command,
                "download_s": round(download_s, 3),
                "crop_s": round(crop_s, 3),
                "n_crops_ok": sum(1 for c in crops if c.get("status") == "ok"),
                "n_crop_errors": crop_errors,
                "invocations": 0 if outcome.status == "skipped_existing" else 1,
                "crops": crops,
            }
            summaries.append(rec)
            _write_json(args.output_dir / "batch" / f"{cluster['grid_id']}.json", rec)
    _write_json(args.output_dir / "batch" / "summary.json", summaries)
    return {
        "n_mosaics_ok": sum(
            1 for r in summaries if r["download_status"] in {"ok", "skipped_existing"}
        ),
        "n_crops_ok": sum(int(r["n_crops_ok"]) for r in summaries),
        "n_crop_errors": sum(int(r["n_crop_errors"]) for r in summaries),
        "download_s": sum(float(r["download_s"]) for r in summaries),
        "invocations": sum(int(r["invocations"]) for r in summaries),
    }


def stage_sequential(args: argparse.Namespace, plan: dict[str, object]) -> dict[str, object]:
    limiter = GehiRateLimiter(min_interval_s=args.request_interval)
    chip_root = args.output_dir / "sequential" / "chips"
    raw_log = args.output_dir / "sequential" / "raw.jsonl"
    raw_log.parent.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, object]] = []
    t_all = time.monotonic()
    invocations = 0
    with raw_log.open("w", encoding="utf-8") as log_fh:
        def _log(payload: Mapping[str, object]) -> None:
            log_fh.write(json.dumps(payload, ensure_ascii=False) + "\n")

        for cluster in plan["clusters"]:
            for anchor in cluster["anchors"]:
                t0 = time.monotonic()
                outcome = download_chip_with_zoom_ladder(
                    anchor,
                    capture_date=str(plan["capture_date"]),
                    version=str(plan["version"]),
                    zoom_ladder=(int(plan["zoom"]),),
                    output_root=chip_root,
                    provider=str(plan["provider"]),
                    gehi_exe=args.gehi_exe,
                    parallel=args.parallel,
                    timeout=args.timeout,
                    limiter=limiter,
                    max_attempts=3,
                    raw_log_callback=_log,
                    recompress=True,
                )
                if outcome.status != "skipped_existing":
                    invocations += 1
                rows.append(
                    {
                        "grid_id": cluster["grid_id"],
                        "anchor_id": str(anchor["anchor_id"]),
                        "status": outcome.status,
                        "error": outcome.error,
                        "path": str(outcome.path) if outcome.path else "",
                        "elapsed_s": round(time.monotonic() - t0, 3),
                    }
                )
    elapsed = time.monotonic() - t_all
    payload = {
        "n_ok": sum(1 for r in rows if r["status"] in {"ok", "skipped_existing"}),
        "n_failed": sum(1 for r in rows if r["status"] not in {"ok", "skipped_existing"}),
        "invocations": invocations,
        "elapsed_s": round(elapsed, 3),
        "rows": rows,
    }
    _write_json(args.output_dir / "sequential" / "summary.json", payload)
    return {
        "n_ok": payload["n_ok"],
        "n_failed": payload["n_failed"],
        "invocations": invocations,
        "elapsed_s": payload["elapsed_s"],
    }


def _qa_row(path: Path, anchor: Mapping[str, object]) -> dict[str, object]:
    if not path.is_file():
        return {"decode_status": "fail", "bbox_status": "fail", "failure_class": "missing_file"}
    return inspect_raster(path, anchor)


def stage_compare(args: argparse.Namespace, plan: dict[str, object]) -> dict[str, object]:
    return _compare_arm(args, plan, args.output_dir / "batch" / "chips", args.output_dir / "compare")


def _compare_arm(
    args: argparse.Namespace,
    plan: dict[str, object],
    batch_root: Path,
    out_dir: Path,
) -> dict[str, object]:
    seq_root = args.output_dir / "sequential" / "chips"
    rows: list[dict[str, object]] = []
    for cluster in plan["clusters"]:
        for anchor in cluster["anchors"]:
            aid = str(anchor["anchor_id"])
            batch_path = existing_chip_path(
                batch_root, aid, str(plan["capture_date"]),
                version=str(plan["version"]), zoom=int(plan["zoom"]),
            )
            seq_path = existing_chip_path(
                seq_root, aid, str(plan["capture_date"]),
                version=str(plan["version"]), zoom=int(plan["zoom"]),
            )
            exist_path = existing_chip_path(
                args.existing_chips, aid, str(plan["capture_date"]),
                version=str(plan["version"]), zoom=int(plan["zoom"]),
            )
            qa_batch = _qa_row(batch_path, anchor)
            qa_seq = _qa_row(seq_path, anchor)
            vs_seq = (
                compare_chips(seq_path, batch_path)
                if seq_path.is_file() and batch_path.is_file()
                else {"comparable": False, "error": "missing sequential or batch chip"}
            )
            vs_exist = (
                compare_chips(exist_path, batch_path)
                if exist_path.is_file() and batch_path.is_file()
                else {"comparable": False, "error": "missing existing or batch chip"}
            )
            rows.append(
                {
                    "grid_id": cluster["grid_id"],
                    "anchor_id": aid,
                    "batch_path": str(batch_path),
                    "sequential_path": str(seq_path),
                    "existing_path": str(exist_path),
                    "batch_qa_decode": qa_batch.get("decode_status"),
                    "batch_qa_bbox": qa_batch.get("bbox_status"),
                    "batch_qa_pixel": qa_batch.get("pixel_quality_status"),
                    "batch_near_black": qa_batch.get("near_black_fraction", ""),
                    "seq_qa_bbox": qa_seq.get("bbox_status"),
                    "vs_seq_comparable": vs_seq.get("comparable"),
                    "vs_seq_mae": vs_seq.get("mae", ""),
                    "vs_seq_p99_abs": vs_seq.get("p99_abs", ""),
                    "vs_seq_exact": vs_seq.get("exact_match_frac", ""),
                    "vs_seq_within_2dn": vs_seq.get("within_2dn_frac", ""),
                    "vs_seq_within_8dn": vs_seq.get("within_8dn_frac", ""),
                    "vs_seq_reprojected": vs_seq.get("reprojected", ""),
                    "vs_exist_mae": vs_exist.get("mae", ""),
                    "vs_exist_within_8dn": vs_exist.get("within_8dn_frac", ""),
                    "vs_seq_error": vs_seq.get("error", ""),
                }
            )
    fields = list(rows[0].keys()) if rows else ["anchor_id"]
    write_csv_rows(out_dir / "chip_compare.csv", rows, fields)
    maes = [float(r["vs_seq_mae"]) for r in rows if r["vs_seq_mae"] != ""]
    payload = {
        "n_rows": len(rows),
        "n_batch_bbox_pass": sum(1 for r in rows if r["batch_qa_bbox"] == "pass"),
        "n_batch_pixel_pass": sum(1 for r in rows if r["batch_qa_pixel"] == "pass"),
        "median_mae_vs_seq": float(np_median(maes)) if maes else None,
        "n_mae_le_5": sum(1 for m in maes if m <= 5.0),
        "n_mae_le_15": sum(1 for m in maes if m <= 15.0),
        "n_comparable": len(maes),
    }
    _write_json(out_dir / "summary.json", payload)
    return payload


def np_median(values: list[float]) -> float:
    import numpy as np

    return float(np.median(np.asarray(values, dtype=np.float64)))


def _evaluate_verdict(
    *,
    n_chips: int,
    n_grids: int,
    n_union_complete: int,
    batch_inv: int,
    seq_inv: int,
    batch_s: float,
    seq_s: float,
    cmp_: Mapping[str, object],
) -> dict[str, object]:
    """Preregistered GO/NO-GO bar math, shared by the download and dump arms."""
    n_bbox = int(cmp_["n_batch_bbox_pass"])
    n_pixel = int(cmp_["n_batch_pixel_pass"])
    n_comp = int(cmp_["n_comparable"])
    med_mae = cmp_.get("median_mae_vs_seq")
    n_mae5 = int(cmp_.get("n_mae_le_5") or 0)
    n_mae15 = int(cmp_.get("n_mae_le_15") or 0)

    bars = {
        "union_complete_all_grids": n_union_complete == n_grids,
        "qa_bbox_pass_rate_ge_95": (n_bbox / n_chips) >= 0.95 if n_chips else False,
        "qa_pixel_pass_rate_ge_95": (n_pixel / n_chips) >= 0.95 if n_chips else False,
        "median_mae_le_5": med_mae is not None and med_mae <= 5.0,
        "mae_le_5_rate_ge_90": (n_mae5 / n_comp) >= 0.90 if n_comp else False,
        "invocations_batch_lt_sequential": batch_inv < seq_inv and batch_inv > 0,
        "wallclock_ratio_ge_2": (seq_s / batch_s) >= 2.0 if batch_s > 0 else False,
    }
    kill = {
        "qa_bbox_pass_rate_lt_80": (n_bbox / n_chips) < 0.80 if n_chips else True,
        "median_mae_gt_15": med_mae is not None and med_mae > 15.0,
        "mae_le_15_rate_lt_80": (n_mae15 / n_comp) < 0.80 if n_comp else True,
    }
    if any(kill.values()):
        verdict = "NO-GO"
    elif all(bars.values()):
        verdict = "GO"
    else:
        verdict = "MIXED"
    return {
        "verdict": verdict,
        "go_bars": bars,
        "kill_bars": kill,
        "n_batch_bbox_pass": n_bbox,
        "n_batch_pixel_pass": n_pixel,
        "median_mae_vs_seq": med_mae,
    }


def stage_report(args: argparse.Namespace, plan: dict[str, object]) -> dict[str, object]:
    avail = json.loads((args.output_dir / "availability" / "summary.json").read_text())
    batch = json.loads((args.output_dir / "batch" / "summary.json").read_text())
    seq = json.loads((args.output_dir / "sequential" / "summary.json").read_text())
    cmp_ = json.loads((args.output_dir / "compare" / "summary.json").read_text())
    n_chips = sum(int(c["n_cluster"]) for c in plan["clusters"])
    batch_inv = sum(int(r["invocations"]) for r in batch)
    seq_inv = int(seq["invocations"])
    batch_s = sum(float(r["download_s"]) + float(r["crop_s"]) for r in batch)
    seq_s = float(seq["elapsed_s"])
    n_union_complete = sum(int(r["target_complete_on_union"]) for r in avail)
    evaluated = _evaluate_verdict(
        n_chips=n_chips,
        n_grids=len(plan["clusters"]),
        n_union_complete=n_union_complete,
        batch_inv=batch_inv,
        seq_inv=seq_inv,
        batch_s=batch_s,
        seq_s=seq_s,
        cmp_=cmp_,
    )
    verdict = str(evaluated["verdict"])
    bars = evaluated["go_bars"]
    kill = evaluated["kill_bars"]
    n_bbox = int(evaluated["n_batch_bbox_pass"])
    n_pixel = int(evaluated["n_batch_pixel_pass"])
    med_mae = evaluated["median_mae_vs_seq"]
    report = {
        "schema_version": "a3_region_batch_pilot_report_v1",
        "finished_at": utc_now(),
        "verdict": verdict,
        "n_chips": n_chips,
        "n_grids": len(plan["clusters"]),
        "n_union_complete": n_union_complete,
        "batch_invocations": batch_inv,
        "sequential_invocations": seq_inv,
        "invocation_ratio": (seq_inv / batch_inv) if batch_inv else None,
        "batch_wall_s": round(batch_s, 3),
        "sequential_wall_s": round(seq_s, 3),
        "wallclock_ratio": (seq_s / batch_s) if batch_s else None,
        "n_batch_bbox_pass": n_bbox,
        "n_batch_pixel_pass": n_pixel,
        "median_mae_vs_seq": med_mae,
        "go_bars": bars,
        "kill_bars": kill,
        "notes": [
            "Cache-hot Top-52 tiles: wall-clock measures process+interval, not cold bytes.",
            "GO requires all go_bars true; any kill_bar true forces NO-GO; else MIXED.",
            "ENABLE_REGION_BATCH stays false until owner signs a production path.",
        ],
    }
    _write_json(args.output_dir / "report" / "summary.json", report)
    print(json.dumps(report, indent=2, sort_keys=True))
    return {"verdict": verdict, **{k: report[k] for k in ("batch_invocations", "sequential_invocations")}}


# ---------------------------------------------------------------------------
# dump arm (added 2026-08-18, HANDOFF-a3-region-batch-2026-08-19 §7 step 1-2):
# GEHI `dump` native tiles -> lossless stitch -> per-anchor crops (JPEG q=95).
# ---------------------------------------------------------------------------


def stage_batch_dump(args: argparse.Namespace, plan: dict[str, object]) -> dict[str, object]:
    limiter = GehiRateLimiter(min_interval_s=args.request_interval)
    tiles_root = args.output_dir / "batch_dump" / "tiles"
    mosaic_root = args.output_dir / "batch_dump" / "mosaics"
    crop_root = args.output_dir / "batch_dump" / "chips"
    raw_log = args.output_dir / "batch_dump" / "raw.jsonl"
    raw_log.parent.mkdir(parents=True, exist_ok=True)
    summaries: list[dict[str, object]] = []
    with raw_log.open("w", encoding="utf-8") as log_fh:
        def _log(payload: Mapping[str, object]) -> None:
            log_fh.write(json.dumps(payload, ensure_ascii=False) + "\n")

        for cluster in plan["clusters"]:
            grid_id = str(cluster["grid_id"])
            region = dict(cluster["region_anchor"])
            padded = union_bbox([region]).padded(DEFAULT_MOSAIC_PAD_DEG)
            region.update(padded.as_anchor_fields())
            tile_dir = tiles_root / grid_id
            t0 = time.monotonic()
            outcome = dump_region_tiles(
                region,
                capture_date=str(plan["capture_date"]),
                zoom=int(plan["zoom"]),
                output_dir=tile_dir,
                provider=str(plan["provider"]),
                gehi_exe=args.gehi_exe,
                parallel=args.parallel,
                timeout=args.timeout,
                limiter=limiter,
                max_attempts=3,
                raw_log_callback=_log,
                overwrite=args.force,
            )
            dump_s = time.monotonic() - t0
            mosaic_info: dict[str, object] | None = None
            stitch_s = 0.0
            crops: list[dict[str, object]] = []
            crop_errors = 0
            crop_s = 0.0
            mosaic_path = (
                mosaic_root / f"{grid_id}_mosaic_z{plan['zoom']}_uncompressed.tif"
            )
            if outcome.status in {"ok", "reused_existing"}:
                t1 = time.monotonic()
                try:
                    mosaic_info = stitch_dump_tiles(
                        tile_dir, mosaic_path, zoom=int(plan["zoom"])
                    )
                except Exception as exc:  # noqa: BLE001 - record and skip crops
                    mosaic_info = None
                    outcome.error = (
                        f"stitch failed: {type(exc).__name__}: {exc}"
                    )
                    outcome.status = "failed"
                stitch_s = time.monotonic() - t1
            if mosaic_info is not None:
                t2 = time.monotonic()
                for anchor in cluster["anchors"]:
                    out_path = existing_chip_path(
                        crop_root,
                        str(anchor["anchor_id"]),
                        str(plan["capture_date"]),
                        version=str(plan["version"]),
                        zoom=int(plan["zoom"]),
                    )
                    try:
                        crop = crop_chip_from_mosaic(
                            mosaic_path, anchor, out_path, recompress=True
                        )
                        crop["status"] = "ok"
                    except Exception as exc:  # noqa: BLE001 - record and continue
                        crop = {
                            "anchor_id": str(anchor["anchor_id"]),
                            "path": str(out_path),
                            "status": "crop_failed",
                            "error": f"{type(exc).__name__}: {exc}",
                        }
                        crop_errors += 1
                    crops.append(crop)
                crop_s = time.monotonic() - t2
            rec = {
                "grid_id": grid_id,
                "dump_status": outcome.status,
                "dump_error": outcome.error,
                "tile_dir": str(tile_dir),
                "n_tiles": outcome.n_tiles,
                "mosaic_path": str(mosaic_path) if mosaic_info else "",
                "mosaic": mosaic_info,
                "gehi_command": outcome.gehi_command,
                "dump_s": round(dump_s, 3),
                "stitch_s": round(stitch_s, 3),
                "crop_s": round(crop_s, 3),
                "n_crops_ok": sum(1 for c in crops if c.get("status") == "ok"),
                "n_crop_errors": crop_errors,
                "invocations": 0 if outcome.status == "reused_existing" else (
                    1 if outcome.status == "ok" else 0
                ),
                "crops": crops,
            }
            summaries.append(rec)
            _write_json(args.output_dir / "batch_dump" / f"{grid_id}.json", rec)
    _write_json(args.output_dir / "batch_dump" / "summary.json", summaries)
    return {
        "n_dumps_ok": sum(
            1 for r in summaries if r["dump_status"] in {"ok", "reused_existing"}
        ),
        "n_crops_ok": sum(int(r["n_crops_ok"]) for r in summaries),
        "n_crop_errors": sum(int(r["n_crop_errors"]) for r in summaries),
        "dump_s": sum(float(r["dump_s"]) for r in summaries),
        "invocations": sum(int(r["invocations"]) for r in summaries),
    }


def stage_compare_dump(args: argparse.Namespace, plan: dict[str, object]) -> dict[str, object]:
    return _compare_arm(
        args, plan, args.output_dir / "batch_dump" / "chips", args.output_dir / "compare_dump"
    )


def stage_report_dump(args: argparse.Namespace, plan: dict[str, object]) -> dict[str, object]:
    avail = json.loads((args.output_dir / "availability" / "summary.json").read_text())
    batch = json.loads((args.output_dir / "batch_dump" / "summary.json").read_text())
    seq = json.loads((args.output_dir / "sequential" / "summary.json").read_text())
    cmp_ = json.loads((args.output_dir / "compare_dump" / "summary.json").read_text())
    n_chips = sum(int(c["n_cluster"]) for c in plan["clusters"])
    batch_inv = sum(int(r["invocations"]) for r in batch)
    seq_inv = int(seq["invocations"])
    batch_s = sum(
        float(r["dump_s"]) + float(r["stitch_s"]) + float(r["crop_s"]) for r in batch
    )
    seq_s = float(seq["elapsed_s"])
    n_union_complete = sum(int(r["target_complete_on_union"]) for r in avail)
    evaluated = _evaluate_verdict(
        n_chips=n_chips,
        n_grids=len(plan["clusters"]),
        n_union_complete=n_union_complete,
        batch_inv=batch_inv,
        seq_inv=seq_inv,
        batch_s=batch_s,
        seq_s=seq_s,
        cmp_=cmp_,
    )
    report = {
        "schema_version": "a3_region_batch_pilot_report_v1",
        "arm": "dump_lossless_stitch",
        "finished_at": utc_now(),
        **evaluated,
        "n_chips": n_chips,
        "n_grids": len(plan["clusters"]),
        "n_union_complete": n_union_complete,
        "batch_invocations": batch_inv,
        "sequential_invocations": seq_inv,
        "invocation_ratio": (seq_inv / batch_inv) if batch_inv else None,
        "batch_wall_s": round(batch_s, 3),
        "sequential_wall_s": round(seq_s, 3),
        "wallclock_ratio": (seq_s / batch_s) if batch_s else None,
        "per_grid": [
            {
                "grid_id": r["grid_id"],
                "dump_status": r["dump_status"],
                "n_tiles": r["n_tiles"],
                "invocations": r["invocations"],
                "dump_s": r["dump_s"],
                "stitch_s": r["stitch_s"],
                "crop_s": r["crop_s"],
                "n_crops_ok": r["n_crops_ok"],
                "n_crop_errors": r["n_crop_errors"],
            }
            for r in batch
        ],
        "notes": [
            "Dump arm: GEHI dump native tiles + lossless stitch + per-crop JPEG q=95; "
            "the download arm's whole-mosaic JPEG pass is gone.",
            "Same preregistered bars as the 2026-08-18 download arm; GO requires all "
            "go_bars true, any kill_bar true forces NO-GO, else MIXED.",
            "Wall-clock remains cache-hot (Top-52 2019-05-30 tiles in the shared "
            "cache): it measures process+interval tax, not cold bytes.",
            "ENABLE_REGION_BATCH stays false until owner signs a production path.",
        ],
    }
    _write_json(args.output_dir / "report" / "dump_summary.json", report)
    print(json.dumps(report, indent=2, sort_keys=True))
    return {"verdict": report["verdict"], "batch_invocations": batch_inv}


def stage_manifest_dump(args: argparse.Namespace, plan: dict[str, object]) -> dict[str, object]:
    """Fold batch_dump crops into per-anchor manifest rows + QA self-check.

    Offline stage: reads the existing batch_dump outputs, writes
    batch_dump/manifest.csv (exactly gehi_download.FIELDS) plus an additive
    provenance sidecar, then runs the production quality_gate on the
    synthesized manifest to prove the per-anchor contract end-to-end.
    """
    dump_dir = args.output_dir / "batch_dump"
    summaries = json.loads((dump_dir / "summary.json").read_text(encoding="utf-8"))
    stdout_by_region: dict[str, str] = {}
    raw_log = dump_dir / "raw.jsonl"
    if raw_log.is_file():
        for line in raw_log.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            payload = json.loads(line)
            if payload.get("region_id"):
                stdout_by_region[str(payload["region_id"])] = str(
                    payload.get("stdout_sha256", "")
                )
    clusters = {str(c["grid_id"]): c for c in plan["clusters"]}
    manifest_rows: list[dict[str, object]] = []
    sidecar_rows: list[dict[str, object]] = []
    for rec in summaries:
        grid_id = str(rec["grid_id"])
        cluster = clusters[grid_id]
        region_id = str(cluster["region_anchor"]["anchor_id"])
        crops_by_anchor = {str(c["anchor_id"]): c for c in rec.get("crops", [])}
        rows, sidecar = synthesize_dump_manifest(
            cluster["anchors"],
            crops_by_anchor,
            capture_date=str(plan["capture_date"]),
            version=str(plan["version"]),
            provider=str(plan["provider"]),
            zoom=int(plan["zoom"]),
            region_id=region_id,
            tile_dir=str(rec.get("tile_dir", "")),
            n_tiles=rec.get("n_tiles", ""),
            mosaic_path=str(rec.get("mosaic_path", "")),
            dump_gehi_command=str(rec.get("gehi_command", "")),
            dump_stdout_sha256=stdout_by_region.get(region_id, ""),
        )
        manifest_rows.extend(rows)
        sidecar_rows.extend(sidecar)
    manifest_path = dump_dir / "manifest.csv"
    write_csv_rows(manifest_path, manifest_rows, GEHI_DOWNLOAD_FIELDS)
    write_csv_rows(
        dump_dir / "manifest_sidecar.csv",
        sidecar_rows,
        list(DUMP_MANIFEST_SIDECAR_FIELDS),
    )
    # QA self-check: the synthesized manifest must pass the production gate
    # unchanged. Candidates are rebuilt from the plan (production would use
    # the ct05 candidate CSV; the keyed fields are identical).
    candidates = [
        {
            "anchor_id": str(a["anchor_id"]),
            "capture_date": str(plan["capture_date"]),
            "version": str(plan["version"]),
            "provider": str(plan["provider"]),
            "requested_zoom": str(plan["zoom"]),
        }
        for c in plan["clusters"]
        for a in c["anchors"]
    ]
    anchors = [a for c in plan["clusters"] for a in c["anchors"]]
    qa = quality_gate(candidates, anchors, [manifest_path], dump_dir / "qa")
    payload = {
        "n_manifest_rows": len(manifest_rows),
        "n_ok": sum(1 for r in manifest_rows if r["status"] == "ok"),
        "n_crop_failed": sum(1 for r in manifest_rows if r["status"] != "ok"),
        "qa_release_eligible": qa["release_eligible_count"],
        "qa_failure_classes": qa["failure_classes"],
        "manifest": str(manifest_path),
        "sidecar": str(dump_dir / "manifest_sidecar.csv"),
    }
    _write_json(args.output_dir / "report" / "manifest_summary.json", payload)
    return payload


def main() -> None:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    plan: dict[str, object] | None = None
    for stage, fn in (
        ("plan", lambda: stage_plan(args)),
        ("availability", lambda: stage_availability(args, plan or _load_plan(args.output_dir))),
        ("batch", lambda: stage_batch(args, plan or _load_plan(args.output_dir))),
        ("sequential", lambda: stage_sequential(args, plan or _load_plan(args.output_dir))),
        ("compare", lambda: stage_compare(args, plan or _load_plan(args.output_dir))),
        ("report", lambda: stage_report(args, plan or _load_plan(args.output_dir))),
        ("batch_dump", lambda: stage_batch_dump(args, plan or _load_plan(args.output_dir))),
        ("compare_dump", lambda: stage_compare_dump(args, plan or _load_plan(args.output_dir))),
        ("report_dump", lambda: stage_report_dump(args, plan or _load_plan(args.output_dir))),
        ("manifest_dump", lambda: stage_manifest_dump(args, plan or _load_plan(args.output_dir))),
    ):
        if not _want(args, stage):
            continue
        if stage != "plan":
            plan = plan or _load_plan(args.output_dir)
        if _skip(args.output_dir, stage, args.force):
            print(f"[A3] skip {stage} (done)", flush=True)
            if stage == "plan":
                plan = _load_plan(args.output_dir)
            continue
        print(f"[A3] start {stage}", flush=True)
        t0 = time.monotonic()
        payload = fn()
        if stage == "plan":
            plan = _load_plan(args.output_dir)
        _mark_done(args.output_dir, stage, {"elapsed_s": round(time.monotonic() - t0, 3), **payload})
        print(f"[A3] done {stage} {payload}", flush=True)


if __name__ == "__main__":
    main()
