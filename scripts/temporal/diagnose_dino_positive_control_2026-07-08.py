#!/usr/bin/env python3
"""Positive-control diagnostic: is the DINO-coarse instrument broken, or does
the PSR<12 population's imagery genuinely lack lockable structure?

Handoff: /tmp/handoff_gehi_dino_instrument_check_2026-07-08.md (session 2,
2026-07-08) -- a reviewer flagged an internal contradiction in the rescue
pilot's NO-GO (``pilot_dino_rescue_2026-07-08.py`` / pilot_summary.txt: 1.2%
rescue rate, argmax observed pinned near shift=0 on a flat-looking 0.6-0.76
similarity floor). A pinned-at-zero argmax on a flat score range is the
fingerprint of a systematic zero-shift bias in the *estimator*, not
necessarily a content problem -- so this script runs a positive-control test
before treating the pilot's NO-GO as a settled statement about the corpus.

Positive control: ``per_chipdate_offsets.csv`` rows with ``ref_kind==
"S3_vexcel"``, ``psr>=12`` (phase-correlation ALREADY trusts this lock) and
``best_offset_m>=5.0`` (a real, non-trivial offset -- not a case where "stays
near zero" would be correct anyway). For these rows (dx_m, dy_m) IS the
ground truth the coarse stage should recover, in the exact ref=Vexcel /
mov=GEHI-vintage convention both ``chip_displacement.estimate_shift`` and
``dino_coarse_match.coarse_match`` share (see ``register()`` in
audit_gehi_displacement.py: S3_vexcel rows always register ref=vexcel_crop,
mov=gehi_vintage_chip).

Two matcher variants, same ``coarse_match`` (unmodified, imported from
``dino_coarse_match``):
  raw       -- exactly what the pilot ran: L2-normalized patch tokens, no
               centering.
  centered  -- fix candidate #1 from the handoff: mean-center each image's own
               token grid (``dino_coarse_match.center_tokens``) before
               matching -- the suspected shared common-mode/DC component
               removed.

Two clean outcomes (handoff's own framing, written to the summary's verdict):
  - raw fails to recover the known offset AND centered recovers it cleanly
    -> the instrument was broken (uncentered-similarity bug); upgrade the fix
       into the pilot rather than trusting the NO-GO as-is.
  - both raw and centered fail to recover -> H1 confirmed: the PSR<12
    population's imagery genuinely lacks lockable structure at this
    backbone/scale, independent of GT quality. The DINO-coarse NO-GO upgrades
    from "no effect measured" to "confirmed ineffective".

Read-only against the 2026-07-06 audit outputs. No new Vexcel API calls, no
new tile/chip downloads. Does not touch chip_displacement.py or
chip_geometry.py.
"""

from __future__ import annotations

import argparse
import csv
import time
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
    center_tokens,
    coarse_match,
    gray_to_tensor,
    load_backbone,
    patch_token_grid,
)

GT_ROOT = Path("/home/gao/zasolar_data/geid_temporal")
AUDIT_DIR = GT_ROOT / "gehi_displacement_audit_2026-07-06"
OFFSETS_CSV = AUDIT_DIR / "per_chipdate_offsets.csv"
VEXCEL_CROPS = AUDIT_DIR / "vexcel_crops"
DEFAULT_OUTPUT_DIR = GT_ROOT / "diagnose_dino_positive_control_2026-07-08"

GSD_M = 0.15  # match the audit's / pilot's registration grid
DEFAULT_PSR_FLOOR = 12.0
DEFAULT_OFFSET_FLOOR_M = 5.0
MAX_COARSE_SEARCH_M = 30.0  # same window the pilot used

DEFAULT_BACKBONE = "vit_small_patch14_dinov2.lvd142m"
DEFAULT_INPUT_SIZE = 518
DEFAULT_WEIGHTS_CACHE = Path("/home/gao/zasolar_data/models/dinov2_floor/hf_cache")

# "Recovered" tolerance, in units of one patch cell's diagonal (~2.4-2.6m at
# input_size=518 for a 96m chip -- varies slightly per anchor's footprint).
# 1.5 cells is "off by at most about one cell", the coarsest the argmax's own
# quantization allows -- tighter than this is not a fair bar for an integer-
# patch-shift search.
RECOVERED_TOL_CELLS = 1.5
# "Pinned near zero" despite a >=5m known offset -- the pilot's own symptom.
PINNED_TOL_CELLS = 1


# --------------------------------------------------------------------------- #
# Population + join tables                                                    #
# --------------------------------------------------------------------------- #

def load_positive_control(psr_floor: float, offset_floor_m: float) -> list[dict]:
    with OFFSETS_CSV.open() as f:
        return [
            r
            for r in csv.DictReader(f)
            if r["ref_kind"] == "S3_vexcel"
            and float(r["psr"]) >= psr_floor
            and float(r["best_offset_m"]) >= offset_floor_m
        ]


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


# --------------------------------------------------------------------------- #
# Per-anchor cache (ref-side work done once per chip_id)                      #
# --------------------------------------------------------------------------- #

@dataclass
class RefCache:
    metric_crs: str
    transform: object
    shape: tuple
    ref_gray: np.ndarray
    ref_tok_raw: object
    ref_tok_centered: object
    side: int
    cell_m_y: float
    cell_m_x: float


def build_ref_cache(
    chip_id: str, target: dict, encoder, mean, std, input_size: int, device: str
) -> RefCache | None:
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
    return RefCache(
        metric_crs=metric_crs,
        transform=transform,
        shape=shape,
        ref_gray=ref_gray,
        ref_tok_raw=ref_tok,
        ref_tok_centered=center_tokens(ref_tok),
        side=side,
        cell_m_y=extent_m_y / side,
        cell_m_x=extent_m_x / side,
    )


# --------------------------------------------------------------------------- #
# Main diagnostic loop                                                        #
# --------------------------------------------------------------------------- #

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=None, help="cap #population rows (smoke run)")
    ap.add_argument("--psr-floor", type=float, default=DEFAULT_PSR_FLOOR)
    ap.add_argument("--offset-floor-m", type=float, default=DEFAULT_OFFSET_FLOOR_M)
    ap.add_argument("--backbone-model-id", type=str, default=DEFAULT_BACKBONE)
    ap.add_argument("--input-size", type=int, default=DEFAULT_INPUT_SIZE)
    ap.add_argument("--weights-cache-dir", type=Path, default=DEFAULT_WEIGHTS_CACHE)
    ap.add_argument("--max-search-m", type=float, default=MAX_COARSE_SEARCH_M)
    ap.add_argument("--device", type=str, default=None)
    ap.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    ap.add_argument("--seed", type=int, default=0, help="row-order shuffle seed, for --limit subsampling")
    args = ap.parse_args()

    import torch

    device = args.device or ("cuda" if torch.cuda.is_available() else "cpu")
    args.output_dir.mkdir(parents=True, exist_ok=True)

    print(f"loading positive control (psr>={args.psr_floor}, offset>={args.offset_floor_m}m)...", flush=True)
    population = load_positive_control(args.psr_floor, args.offset_floor_m)
    if args.limit:
        rng = np.random.default_rng(args.seed)
        idx = rng.permutation(len(population))[: args.limit]
        population = [population[i] for i in idx]
    print(f"population: {len(population)} rows", flush=True)

    targets = load_targets(CHIP_TARGETS)

    print(f"loading backbone {args.backbone_model_id} on {device}...", flush=True)
    encoder, mean, std = load_backbone(args.backbone_model_id, args.weights_cache_dir, device)

    ref_cache: dict[str, RefCache | None] = {}
    n_missing_anchor = n_missing_vexcel = n_missing_mov = 0
    rows_out: list[dict] = []

    t_start = time.perf_counter()
    for k, row in enumerate(population, 1):
        cid = row["chip_id"]
        if cid not in ref_cache:
            tgt = targets.get(cid)
            anchor_dir = find_anchor_dir(cid)
            if tgt is None or anchor_dir is None:
                ref_cache[cid] = None
                n_missing_anchor += 1
            else:
                rc = build_ref_cache(cid, tgt, encoder, mean, std, args.input_size, device)
                ref_cache[cid] = rc
                if rc is None:
                    n_missing_vexcel += 1
        ref = ref_cache[cid]
        if ref is None:
            continue

        anchor_dir = find_anchor_dir(cid)
        mov_path = find_mov_path(anchor_dir, row["capture_date"], row["version"]) if anchor_dir else None
        if mov_path is None:
            n_missing_mov += 1
            continue

        try:
            mov_gray = cd.reproject_to_grid(
                mov_path, dst_crs=ref.metric_crs, dst_transform=ref.transform, dst_shape=ref.shape
            )
        except Exception:
            continue
        tensor = gray_to_tensor(mov_gray, args.input_size, mean, std, device)
        mov_tok, side2 = patch_token_grid(encoder, tensor)
        mov_tok_centered = center_tokens(mov_tok)

        max_shift_y = min(side2 // 3, max(1, round(args.max_search_m / ref.cell_m_y)))
        max_shift_x = min(side2 // 3, max(1, round(args.max_search_m / ref.cell_m_x)))
        max_shift_cells = max(max_shift_y, max_shift_x)

        known_dx_m = float(row["dx_m"])
        known_dy_m = float(row["dy_m"])
        known_offset_m = float(row["best_offset_m"])
        cell_diag_m = float(np.hypot(ref.cell_m_y, ref.cell_m_x))
        recovered_tol_m = RECOVERED_TOL_CELLS * cell_diag_m
        pinned_tol_cells = PINNED_TOL_CELLS

        def _variant(ref_tok, mov_tok_):
            sy, sx, sim = coarse_match(ref_tok, mov_tok_, ref.side, max_shift_cells)
            dy_m = sy * ref.cell_m_y
            dx_m = sx * ref.cell_m_x
            error_m = float(np.hypot(dx_m - known_dx_m, dy_m - known_dy_m))
            pinned = abs(sy) <= pinned_tol_cells and abs(sx) <= pinned_tol_cells
            recovered = error_m <= recovered_tol_m
            return {
                "sy": sy, "sx": sx, "dy_m": round(dy_m, 3), "dx_m": round(dx_m, 3),
                "sim": round(sim, 4), "error_m": round(error_m, 3),
                "pinned_near_zero": pinned, "recovered": recovered,
            }

        raw = _variant(ref.ref_tok_raw, mov_tok)
        centered = _variant(ref.ref_tok_centered, mov_tok_centered)

        rows_out.append(
            {
                "chip_id": cid,
                "grid_id": row["grid_id"],
                "capture_date": row["capture_date"],
                "area_bucket": row["area_bucket"],
                "known_dx_m": round(known_dx_m, 3),
                "known_dy_m": round(known_dy_m, 3),
                "known_offset_m": round(known_offset_m, 3),
                "cell_diag_m": round(cell_diag_m, 3),
                "raw_dy_m": raw["dy_m"], "raw_dx_m": raw["dx_m"], "raw_sim": raw["sim"],
                "raw_error_m": raw["error_m"], "raw_pinned_near_zero": raw["pinned_near_zero"],
                "raw_recovered": raw["recovered"],
                "centered_dy_m": centered["dy_m"], "centered_dx_m": centered["dx_m"],
                "centered_sim": centered["sim"], "centered_error_m": centered["error_m"],
                "centered_pinned_near_zero": centered["pinned_near_zero"],
                "centered_recovered": centered["recovered"],
            }
        )
        if k % 50 == 0:
            elapsed = time.perf_counter() - t_start
            print(f"  {k}/{len(population)} rows, {elapsed:.0f}s elapsed", flush=True)

    total_elapsed = time.perf_counter() - t_start
    print(
        f"DONE: {len(rows_out)} rows processed, "
        f"missing_anchor={n_missing_anchor} missing_vexcel={n_missing_vexcel} missing_mov={n_missing_mov}, "
        f"{total_elapsed:.0f}s total",
        flush=True,
    )

    _write_csv(args.output_dir / "diagnostic_results.csv", rows_out)
    _write_summary(args.output_dir / "diagnostic_summary.txt", rows_out, n_missing_anchor, n_missing_vexcel, n_missing_mov, len(population), args)
    print(f"outputs -> {args.output_dir}", flush=True)


def _write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        path.write_text("")
        return
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


def _rate(rows: list[dict], key: str) -> tuple[int, int, float]:
    n = len(rows)
    r = sum(1 for x in rows if x[key])
    return r, n, (r / n if n else float("nan"))


def _q(vals: list[float], p: float):
    return round(float(np.percentile(vals, p)), 3) if vals else None


def _write_summary(
    path: Path, rows: list[dict], n_missing_anchor: int, n_missing_vexcel: int, n_missing_mov: int,
    n_population: int, args,
) -> None:
    lines: list[str] = []
    lines.append("DINO-coarse positive-control diagnostic (2026-07-08, session 2)")
    lines.append(
        f"population: {n_population} rows (ref_kind=S3_vexcel, psr>={args.psr_floor}, "
        f"best_offset_m>={args.offset_floor_m})"
    )
    lines.append(
        f"processed: {len(rows)}  missing_anchor={n_missing_anchor} "
        f"missing_vexcel={n_missing_vexcel} missing_mov={n_missing_mov}"
    )
    lines.append(f"recovered tolerance: {RECOVERED_TOL_CELLS} patch cells; pinned tolerance: {PINNED_TOL_CELLS} cell")
    lines.append("")

    for variant in ("raw", "centered"):
        r, n, rate = _rate(rows, f"{variant}_recovered")
        pr, pn, prate = _rate(rows, f"{variant}_pinned_near_zero")
        errs = [x[f"{variant}_error_m"] for x in rows]
        sims = [x[f"{variant}_sim"] for x in rows]
        lines.append(f"[{variant.upper()}]")
        lines.append(f"  recovered rate (error <= tol): {rate:.1%} ({r}/{n})")
        lines.append(f"  pinned-near-zero rate (despite known offset >={args.offset_floor_m}m): {prate:.1%} ({pr}/{pn})")
        lines.append(f"  error_m: p50={_q(errs,50)} p90={_q(errs,90)} max={round(max(errs),3) if errs else None}")
        lines.append(f"  sim range: min={round(min(sims),4) if sims else None} max={round(max(sims),4) if sims else None}")
        lines.append("")

    lines.append("By area bucket (recovered rate, raw vs centered):")
    for ab in sorted({x["area_bucket"] for x in rows}):
        sub = [x for x in rows if x["area_bucket"] == ab]
        rr, _, rraw = _rate(sub, "raw_recovered")
        rc, _, rcen = _rate(sub, "centered_recovered")
        lines.append(f"  {ab:14s} raw={rraw:6.1%} (n={len(sub)})  centered={rcen:6.1%}")
    lines.append("")

    _, _, raw_rate = _rate(rows, "raw_recovered")
    _, _, cen_rate = _rate(rows, "centered_recovered")
    _, _, raw_pinned_rate = _rate(rows, "raw_pinned_near_zero")
    _, _, cen_pinned_rate = _rate(rows, "centered_pinned_near_zero")
    centering_delta = cen_rate - raw_rate
    pinned_delta = cen_pinned_rate - raw_pinned_rate
    ab_rates = {}
    for ab in sorted({x["area_bucket"] for x in rows}):
        sub = [x for x in rows if x["area_bucket"] == ab]
        _, _, ab_rates[ab] = _rate(sub, "raw_recovered")
    size_gradient = (
        len(ab_rates) >= 2
        and list(ab_rates.values()) == sorted(ab_rates.values())
        and max(ab_rates.values()) - min(ab_rates.values()) >= 0.15
    )

    lines.append("Verdict:")
    if raw_rate >= 0.5:
        lines.append(
            f"  raw={raw_rate:.1%} centered={cen_rate:.1%} -> the raw matcher ALREADY recovers known "
            f"offsets at a useful rate on this positive control -- the pilot's population-wide 1.2% "
            f"rescue rate is not explained by a broken coarse instrument; re-examine why the PSR<12 "
            f"rows themselves differ from this positive-control set (texture/scene-change, not the "
            f"matcher)."
        )
    elif centering_delta >= 0.15 and cen_rate >= 0.5:
        lines.append(
            f"  raw={raw_rate:.1%} centered={cen_rate:.1%} (delta={centering_delta:+.1%}) -> INSTRUMENT "
            f"WAS BROKEN: uncentered cosine similarity (fix candidate #1) explains the pilot's NO-GO. "
            f"The centered matcher recovers already-known offsets on this positive control -- upgrade "
            f"the pilot to use center_tokens() before re-running the PSR<12 population; do not treat "
            f"the original NO-GO as a settled statement about the corpus."
        )
    else:
        lines.append(
            f"  raw={raw_rate:.1%} centered={cen_rate:.1%} (delta={centering_delta:+.1%}) -> PARTIAL "
            f"SIGNAL, NOT A CLEAN VERDICT EITHER WAY. Both matchers recover well above chance on known, "
            f"non-trivial offsets (rules out 'zero signal') but neither clears 50% and neither is close "
            f"to a clean lock. Centering (fix candidate #1) is NOT the explanation: it only moved "
            f"recovery by {centering_delta:+.1%} and moved the pinned-near-zero rate by only "
            f"{pinned_delta:+.1%} (raw={raw_pinned_rate:.1%}, centered={cen_pinned_rate:.1%} -- still "
            f"the majority of rows). Overlap-normalization (fix candidate #2) was already ruled out "
            f"analytically + by a unit-test regression check (tests/temporal/test_dino_coarse_match.py: "
            f"coarse_match shows no zero-bias on uncorrelated tokens; if anything bias runs toward the "
            f"search-window edges under noise, not toward zero). So NEITHER of the two cheap suspected "
            f"'instrument bugs' explains the pinned-at-zero symptom."
        )
        if size_gradient:
            lines.append(
                f"  Recovery rises monotonically with footprint size ({ab_rates}) -- consistent with a "
                f"genuine backbone/scale limitation (final-layer ViT tokens are the most semantically "
                f"pooled, hence least spatially discriminative, and a ~2.4-2.6m patch cell covers most "
                f"of a small installation in 1-2 cells) rather than a blanket 'this corpus has no "
                f"lockable structure' claim."
            )
        lines.append(
            "  This one-hour experiment did not reach fix candidates #3 (intermediate-layer ViT tokens) "
            "or #4 (finer input-size / smaller patch cells) from the handoff's priority list -- those "
            "remain untested and would be needed to fully settle whether a better-tuned instrument could "
            "raise the pilot's rescue rate. Net: the NO-GO business decision still stands (1.2% is "
            "nowhere near the 20-30% floor even under the most generous reading), but the causal claim "
            "should NOT be strengthened to 'confirmed ineffective content' -- it stays 'this specific "
            "raw/centered final-layer coarse matcher underperforms; cause only partially isolated'."
        )

    text = "\n".join(lines)
    path.write_text(text + "\n")
    print(text, flush=True)


if __name__ == "__main__":
    main()
