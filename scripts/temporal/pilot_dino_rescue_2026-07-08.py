#!/usr/bin/env python3
"""Pilot: does a DINO-coarse + phase-correlation-refine two-stage registration
rescue the GEHI/Vexcel PSR<12 population cheaply enough to productionize?

Handoff: /tmp/handoff_gehi_dino_rescue_pilot_2026-07-08.md (design agreed
there, don't redesign). Background: docs/replan_v2/DATA-gehi-displacement-
audit-2026-07-06.md.

Population: ``per_chipdate_offsets.csv`` rows with ``ref_kind=="S3_vexcel"``
and ``psr<12`` (2,785 rows / 679 anchors) — i.e. registrations that already
cleared the PSR>=8 floor gate (the only rows the audit wrote at all) but miss
the stricter production PSR>=12 gate.

Method (frozen by the handoff, not re-derived here):
  1. Coarse: dense DINO patch-token match (argmax cosine-similarity over an
     integer patch-shift search) between the GEHI vintage chip and the
     anchor's cached Vexcel crop, both reprojected onto the SAME per-anchor
     UTM grid ``chip_displacement.utm_grid_for_bounds`` already used by the
     audit (gsd=0.15m) — reused verbatim.
  2. Refine: pre-shift the moving chip's reprojected array by the coarse
     offset (``scipy.ndimage.shift``), then call the existing, validated
     ``chip_displacement.estimate_shift`` UNMODIFIED on (ref, mov_shifted).
     Its own PSR is the production gate; the total offset = coarse + residual.

Accuracy proxy: predicted_S3 = per-anchor GEHI-latest-vs-Vexcel bias
(``gehi_vs_vexcel_bias.csv``) + the matching S1 (GEHI-vintage-vs-GEHI-latest)
row for the same (chip_id, capture_date) — the exact vector identity the
audit memo's Finding 4 validated (n=6,624 triples, baseline residual
p50=0.45m / p90=4.82m).

Read-only against the 2026-07-06 audit outputs. No new Vexcel API calls, no
new tile/chip downloads. Does not touch chip_displacement.py or
chip_geometry.py.
"""

from __future__ import annotations

import argparse
import csv
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from scripts.temporal import chip_displacement as cd
from scripts.temporal.audit_gehi_displacement import (
    CHIP_TARGETS,
    DISTILL_CHIPS,
    PANEL_REPAIR_CHIPS,
    enumerate_stack,
    load_targets,
)
from scripts.temporal.dino_coarse_match import (
    coarse_match,
    gray_to_tensor,
    load_backbone,
    patch_token_grid,
)

GT_ROOT = Path("/home/gaosh/zasolar_data/geid_temporal")
AUDIT_DIR = GT_ROOT / "gehi_displacement_audit_2026-07-06"
OFFSETS_CSV = AUDIT_DIR / "per_chipdate_offsets.csv"
BIAS_CSV = AUDIT_DIR / "gehi_vs_vexcel_bias.csv"
VEXCEL_CROPS = AUDIT_DIR / "vexcel_crops"
DEFAULT_OUTPUT_DIR = GT_ROOT / "pilot_dino_rescue_2026-07-08"

GSD_M = 0.15  # match the audit's registration grid, so PSR is comparable
PRODUCTION_PSR_GATE = 12.0
MAX_COARSE_SEARCH_M = 30.0  # comfortably above estimate_shift's 27m guard

DEFAULT_BACKBONE = "vit_small_patch14_dinov2.lvd142m"  # dinov2_floor, ViT-S/14
DEFAULT_INPUT_SIZE = 518
DEFAULT_WEIGHTS_CACHE = Path("/home/gaosh/zasolar_data/models/dinov2_floor/hf_cache")

# Production-scale extrapolation constants (from the handoff conversation).
PROD_N_ANCHORS = 15_800
PROD_N_CHIPDATES = 275_000  # midpoint of the 250-300k estimate


# --------------------------------------------------------------------------- #
# Population + join tables                                                    #
# --------------------------------------------------------------------------- #

def load_population(psr_cut: float) -> list[dict]:
    with OFFSETS_CSV.open() as f:
        return [r for r in csv.DictReader(f) if r["ref_kind"] == "S3_vexcel" and float(r["psr"]) < psr_cut]


def load_s1_index() -> dict[tuple[str, str], dict]:
    idx: dict[tuple[str, str], dict] = {}
    with OFFSETS_CSV.open() as f:
        for r in csv.DictReader(f):
            if r["ref_kind"] == "S1_gehi_latest":
                idx[(r["chip_id"], r["capture_date"])] = r
    return idx


def load_bias_index() -> dict[str, dict]:
    idx: dict[str, dict] = {}
    with BIAS_CSV.open() as f:
        for r in csv.DictReader(f):
            idx.setdefault(r["chip_id"], r)
    return idx


def find_anchor_dir(chip_id: str) -> Path | None:
    d = DISTILL_CHIPS / chip_id
    if d.is_dir():
        return d
    d = PANEL_REPAIR_CHIPS / chip_id
    if d.is_dir():
        return d
    return None


def find_mov_path(anchor_dir: Path, capture_date: str, version: str) -> Path | None:
    stack = enumerate_stack(anchor_dir)
    cands = [p for p in stack if p["date"] == capture_date]
    if not cands:
        return None
    if len(cands) > 1:
        exact = [p for p in cands if p["version"] == int(version)]
        if exact:
            cands = exact
    return cands[0]["path"]


def year_group(year: int) -> str:
    if year <= 2014:
        return "<=2014"
    if year <= 2017:
        return "2015-2017"
    if year <= 2020:
        return "2018-2020"
    return "2021+"


# --------------------------------------------------------------------------- #
# Per-anchor cache (ref-side work done once per chip_id, not per chip-date)   #
# --------------------------------------------------------------------------- #

@dataclass
class AnchorCache:
    metric_crs: str
    transform: object
    shape: tuple
    ref_gray: np.ndarray
    ref_tok: object
    side: int
    cell_m_y: float
    cell_m_x: float
    bbox4326: tuple
    setup_sec: float


def build_anchor_cache(
    chip_id: str, target: dict, encoder, mean, std, input_size: int, device: str
) -> AnchorCache | None:
    from core.grid_utils import get_metric_crs

    vx_path = VEXCEL_CROPS / f"{chip_id}.tif"
    if not vx_path.exists():
        return None
    grid_id = target.get("grid_id") or "unknown"
    try:
        metric_crs = get_metric_crs(grid_id, region="johannesburg")
    except Exception:
        metric_crs = "EPSG:32735"
    bbox = (
        float(target["chip_lon_min"]),
        float(target["chip_lat_min"]),
        float(target["chip_lon_max"]),
        float(target["chip_lat_max"]),
    )
    t0 = time.perf_counter()
    transform, shape, _ = cd.utm_grid_for_bounds(*bbox, metric_crs=metric_crs, gsd_m=GSD_M)
    if shape[0] < 24 or shape[1] < 24:
        return None
    try:
        ref_gray = cd.reproject_to_grid(vx_path, dst_crs=metric_crs, dst_transform=transform, dst_shape=shape)
    except Exception:
        return None
    tensor = gray_to_tensor(ref_gray, input_size, mean, std, device)
    ref_tok, side = patch_token_grid(encoder, tensor)
    extent_m_y = shape[0] * GSD_M
    extent_m_x = shape[1] * GSD_M
    setup_sec = time.perf_counter() - t0
    return AnchorCache(
        metric_crs=metric_crs,
        transform=transform,
        shape=shape,
        ref_gray=ref_gray,
        ref_tok=ref_tok,
        side=side,
        cell_m_y=extent_m_y / side,
        cell_m_x=extent_m_x / side,
        bbox4326=bbox,
        setup_sec=setup_sec,
    )


# --------------------------------------------------------------------------- #
# Main pilot loop                                                             #
# --------------------------------------------------------------------------- #

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=None, help="cap #population rows (smoke run)")
    ap.add_argument("--psr-cut", type=float, default=12.0, help="population filter: psr < this")
    ap.add_argument("--production-gate", type=float, default=PRODUCTION_PSR_GATE)
    ap.add_argument("--backbone-model-id", type=str, default=DEFAULT_BACKBONE)
    ap.add_argument("--input-size", type=int, default=DEFAULT_INPUT_SIZE)
    ap.add_argument("--weights-cache-dir", type=Path, default=DEFAULT_WEIGHTS_CACHE)
    ap.add_argument("--max-search-m", type=float, default=MAX_COARSE_SEARCH_M)
    ap.add_argument("--device", type=str, default=None)
    ap.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = ap.parse_args()

    import torch

    device = args.device or ("cuda" if torch.cuda.is_available() else "cpu")
    args.output_dir.mkdir(parents=True, exist_ok=True)

    print(f"loading population (psr<{args.psr_cut})...", flush=True)
    population = load_population(args.psr_cut)
    if args.limit:
        population = population[: args.limit]
    print(f"population: {len(population)} rows", flush=True)

    s1_index = load_s1_index()
    bias_index = load_bias_index()
    targets = load_targets(CHIP_TARGETS)

    print(f"loading backbone {args.backbone_model_id} on {device}...", flush=True)
    encoder, mean, std = load_backbone(args.backbone_model_id, args.weights_cache_dir, device)

    anchor_cache: dict[str, AnchorCache | None] = {}
    anchor_setup_secs: list[float] = []

    rows_out: list[dict] = []
    dino_secs: list[float] = []
    refine_secs: list[float] = []
    n_missing_anchor = n_missing_vexcel = n_missing_mov = 0

    t_start = time.perf_counter()
    for k, row in enumerate(population, 1):
        cid = row["chip_id"]
        if cid not in anchor_cache:
            tgt = targets.get(cid)
            anchor_dir = find_anchor_dir(cid)
            if tgt is None or anchor_dir is None:
                anchor_cache[cid] = None
                n_missing_anchor += 1
            else:
                ac = build_anchor_cache(cid, tgt, encoder, mean, std, args.input_size, device)
                anchor_cache[cid] = ac
                if ac is None:
                    n_missing_vexcel += 1
                else:
                    anchor_setup_secs.append(ac.setup_sec)
        anchor = anchor_cache[cid]
        if anchor is None:
            continue

        anchor_dir = find_anchor_dir(cid)
        mov_path = find_mov_path(anchor_dir, row["capture_date"], row["version"]) if anchor_dir else None
        if mov_path is None:
            n_missing_mov += 1
            continue

        t0 = time.perf_counter()
        try:
            mov_gray = cd.reproject_to_grid(
                mov_path, dst_crs=anchor.metric_crs, dst_transform=anchor.transform, dst_shape=anchor.shape
            )
        except Exception:
            continue
        tensor = gray_to_tensor(mov_gray, args.input_size, mean, std, device)
        mov_tok, side2 = patch_token_grid(encoder, tensor)
        max_shift_y = min(side2 // 3, max(1, round(args.max_search_m / anchor.cell_m_y)))
        max_shift_x = min(side2 // 3, max(1, round(args.max_search_m / anchor.cell_m_x)))
        max_shift_cells = max(max_shift_y, max_shift_x)
        sy, sx, sim = coarse_match(anchor.ref_tok, mov_tok, anchor.side, max_shift_cells)
        dino_sec = time.perf_counter() - t0
        dino_secs.append(dino_sec)

        coarse_dy_m = sy * anchor.cell_m_y
        coarse_dx_m = sx * anchor.cell_m_x
        coarse_dy_px = coarse_dy_m / GSD_M
        coarse_dx_px = coarse_dx_m / GSD_M

        t1 = time.perf_counter()
        from scipy import ndimage

        mov_shifted = ndimage.shift(mov_gray, shift=(-coarse_dy_px, -coarse_dx_px), order=1, mode="reflect")
        res = cd.estimate_shift(anchor.ref_gray, mov_shifted, min_psr=0.0)
        refine_sec = time.perf_counter() - t1
        refine_secs.append(refine_sec)

        rescued = bool(res.ok and res.psr >= args.production_gate)
        total_dx_m = total_dy_m = total_offset_m = None
        pred_dx_m = pred_dy_m = resid_vs_pred_m = None
        if res.ok:
            total_dy_px = coarse_dy_px + res.dy_px
            total_dx_px = coarse_dx_px + res.dx_px
            total_dx_m, total_dy_m, total_offset_m = cd.shift_px_to_m(
                total_dy_px, total_dx_px, gsd_y_m=GSD_M, gsd_x_m=GSD_M
            )
            s1 = s1_index.get((cid, row["capture_date"]))
            bias = bias_index.get(cid)
            if s1 is not None and bias is not None:
                pred_dx_m = float(bias["bias_dx_m"]) + float(s1["dx_m"])
                pred_dy_m = float(bias["bias_dy_m"]) + float(s1["dy_m"])
                resid_vs_pred_m = float(np.hypot(total_dx_m - pred_dx_m, total_dy_m - pred_dy_m))

        year = int(row["year"])
        rows_out.append(
            {
                "chip_id": cid,
                "grid_id": row["grid_id"],
                "capture_date": row["capture_date"],
                "year": year,
                "year_bucket": year_group(year),
                "area_bucket": row["area_bucket"],
                "orig_psr": float(row["psr"]),
                "orig_dx_m": float(row["dx_m"]),
                "orig_dy_m": float(row["dy_m"]),
                "coarse_dy_m": round(coarse_dy_m, 3),
                "coarse_dx_m": round(coarse_dx_m, 3),
                "coarse_sim": round(sim, 4),
                "refined_ok": res.ok,
                "refined_reason": res.reason,
                "refined_psr": round(res.psr, 2),
                "rescued": rescued,
                "total_dx_m": round(total_dx_m, 3) if total_dx_m is not None else None,
                "total_dy_m": round(total_dy_m, 3) if total_dy_m is not None else None,
                "total_offset_m": round(total_offset_m, 3) if total_offset_m is not None else None,
                "pred_dx_m": round(pred_dx_m, 3) if pred_dx_m is not None else None,
                "pred_dy_m": round(pred_dy_m, 3) if pred_dy_m is not None else None,
                "resid_vs_pred_m": round(resid_vs_pred_m, 3) if resid_vs_pred_m is not None else None,
                "dino_coarse_sec": round(dino_sec, 4),
                "refine_sec": round(refine_sec, 4),
            }
        )
        if k % 200 == 0:
            elapsed = time.perf_counter() - t_start
            print(f"  {k}/{len(population)} rows, {elapsed:.0f}s elapsed", flush=True)

    total_elapsed = time.perf_counter() - t_start
    print(
        f"DONE: {len(rows_out)} rows processed, "
        f"missing_anchor={n_missing_anchor} missing_vexcel={n_missing_vexcel} missing_mov={n_missing_mov}, "
        f"{total_elapsed:.0f}s total",
        flush=True,
    )

    _write_csv(args.output_dir / "pilot_results.csv", rows_out)
    _write_summary(
        args.output_dir / "pilot_summary.txt",
        rows_out,
        anchor_setup_secs,
        dino_secs,
        refine_secs,
        n_missing_anchor,
        n_missing_vexcel,
        n_missing_mov,
        len(population),
        args,
    )
    print(f"outputs -> {args.output_dir}", flush=True)


def _write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        path.write_text("")
        return
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


def _rate(rows: list[dict]) -> tuple[int, int, float]:
    n = len(rows)
    r = sum(1 for x in rows if x["rescued"])
    return r, n, (r / n if n else float("nan"))


def _q(vals: list[float], p: float):
    return round(float(np.percentile(vals, p)), 3) if vals else None


def _write_summary(
    path: Path,
    rows: list[dict],
    anchor_setup_secs: list[float],
    dino_secs: list[float],
    refine_secs: list[float],
    n_missing_anchor: int,
    n_missing_vexcel: int,
    n_missing_mov: int,
    n_population: int,
    args,
) -> None:
    lines: list[str] = []
    lines.append("DINO-coarse + phase-correlation-refine rescue pilot (2026-07-08)")
    lines.append(f"population: {n_population} rows (psr<{args.psr_cut}, ref_kind=S3_vexcel)")
    lines.append(
        f"processed: {len(rows)}  missing_anchor={n_missing_anchor} "
        f"missing_vexcel={n_missing_vexcel} missing_mov={n_missing_mov}"
    )
    lines.append(f"production PSR gate: {args.production_gate}")
    lines.append("")

    r, n, rate = _rate(rows)
    lines.append(f"OVERALL rescue rate: {rate:.1%} ({r}/{n})")
    lines.append("")

    lines.append("By year bucket:")
    for yb in ("<=2014", "2015-2017", "2018-2020", "2021+"):
        sub = [x for x in rows if x["year_bucket"] == yb]
        r, n, rate = _rate(sub)
        lines.append(f"  {yb:12s} {rate:6.1%}  (n={n})" if n else f"  {yb:12s}    n/a  (n=0)")
    lines.append("")

    lines.append("By area bucket (all years):")
    for ab in sorted({x["area_bucket"] for x in rows}):
        sub = [x for x in rows if x["area_bucket"] == ab]
        r, n, rate = _rate(sub)
        lines.append(f"  {ab:14s} {rate:6.1%}  (n={n})")
    lines.append("")

    lines.append("By area bucket, 2021+ only:")
    rows_2021 = [x for x in rows if x["year_bucket"] == "2021+"]
    for ab in sorted({x["area_bucket"] for x in rows_2021}):
        sub = [x for x in rows_2021 if x["area_bucket"] == ab]
        r, n, rate = _rate(sub)
        lines.append(f"  {ab:14s} {rate:6.1%}  (n={n})")
    lines.append("")

    resid = [x["resid_vs_pred_m"] for x in rows if x["resid_vs_pred_m"] is not None]
    resid_rescued = [x["resid_vs_pred_m"] for x in rows if x["resid_vs_pred_m"] is not None and x["rescued"]]
    lines.append(f"Accuracy vs S1+bias prediction (organic baseline: p50=0.45m / p90=4.82m):")
    lines.append(f"  all rows w/ prediction available (n={len(resid)}): p50={_q(resid,50)}m p90={_q(resid,90)}m")
    lines.append(
        f"  rescued rows only (n={len(resid_rescued)}): p50={_q(resid_rescued,50)}m p90={_q(resid_rescued,90)}m"
    )
    lines.append("")

    setup_mean = float(np.mean(anchor_setup_secs)) if anchor_setup_secs else float("nan")
    dino_mean = float(np.mean(dino_secs)) if dino_secs else float("nan")
    refine_mean = float(np.mean(refine_secs)) if refine_secs else float("nan")
    per_chipdate = dino_mean + refine_mean
    lines.append("Wall-clock cost:")
    lines.append(f"  per-anchor one-time setup (ref reproject + ref DINO fwd): mean={setup_mean:.3f}s (n={len(anchor_setup_secs)})")
    lines.append(f"  per-chip-date DINO coarse match: mean={dino_mean:.3f}s (n={len(dino_secs)})")
    lines.append(f"  per-chip-date phase-correlation refine: mean={refine_mean:.3f}s (n={len(refine_secs)})")
    lines.append(f"  per-chip-date total (coarse+refine): mean={per_chipdate:.3f}s")
    prod_sec = PROD_N_ANCHORS * setup_mean + PROD_N_CHIPDATES * per_chipdate
    lines.append(
        f"  production extrapolation ({PROD_N_ANCHORS} anchors x one-time setup + "
        f"{PROD_N_CHIPDATES} chip-dates x per-chip-date): {prod_sec/3600:.1f}h"
    )
    lines.append("")

    overall_r, overall_n, overall_rate = _rate(rows)
    lines.append("Go/no-go:")
    if overall_rate >= 0.5:
        lines.append(f"  rescue={overall_rate:.1%} >= 50% -> GO: worth building into chip_geom_v3_tight_scaled's recentering leg")
    elif overall_rate < 0.25:
        lines.append(f"  rescue={overall_rate:.1%} < 25% -> NO-GO: drop DINO approach, scope widening-only + flag recenter-unavailable")
    else:
        lines.append(f"  rescue={overall_rate:.1%} is MIXED (25-50%) -> scope recentering to strata where it demonstrably works (see breakdowns above)")

    text = "\n".join(lines)
    path.write_text(text + "\n")
    print(text, flush=True)


if __name__ == "__main__":
    main()
