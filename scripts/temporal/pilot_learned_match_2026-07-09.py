#!/usr/bin/env python3
"""Bounded pilot: SuperPoint+LightGlue keypoint matching + RANSAC translation
fit for weak-lock GEHI<->Vexcel registration (ISSUE-24).

Pre-registration: ``docs/replan_v2/DATA-learned-matching-bounded-pilot-2026-07-09.md``
(committed before this script produced any result). Parent plan:
``docs/replan_v2/ISSUE-24-learned-feature-matching.md``. Precedent discipline
(same population, same benchmark number to beat): the DINO-coarse dense-patch-
token line, closed KILL at 39.4% recovered
(``docs/replan_v2/DATA-dino-coarse-bounded-kill-2026-07-08.md``).

Mechanism: unlike DINO-coarse's dense-token argmax over integer patch shifts
(a semantic-similarity heatmap, never designed for sub-pixel geometric
correspondence), SuperPoint+LightGlue emits explicit sparse keypoint
correspondences that a robust estimator turns into a translation fit *with an
inlier-count confidence signal* -- a PSR-analog for free.

Population loader, ``find_anchor_dir``/``find_mov_path``, and the ref-side
reprojection recipe are copied (not imported -- the source has a dash in its
filename) from ``diagnose_dino_positive_control_2026-07-08.py`` (read-only
reference, not modified). Shared helpers that ARE importable
(``audit_gehi_displacement``'s ``CHIP_TARGETS``/``DISTILL_CHIPS``/
``PANEL_REPAIR_CHIPS``/``enumerate_stack``/``load_targets``, and
``chip_displacement``) are imported, never modified, per ISSUE-24's hard
constraints.

GT convention (identical to the DINO diagnostic): for ``ref_kind==
"S3_vexcel"`` rows, ``ref=vexcel_crop``, ``mov=gehi_vintage_chip`` (see
``register()`` in ``audit_gehi_displacement.py``). ``(dx_m, dy_m)`` in
``per_chipdate_offsets.csv`` is how far the GEHI vintage chip's content sits
right/below the Vexcel crop's content, in the shared north-up UTM grid
(column = east-positive, row = south-positive). SuperPoint keypoints are
``(x, y)`` = ``(col, row)`` (confirmed against ``lightglue``'s
``SuperPoint.forward``, which converts internal ``(h, w)`` indices via
``torch.flip``), so a matched pair's displacement ``mov_kp - ref_kp`` is
already in the SAME ``(dx_px, dy_px)`` convention as
``chip_displacement.ShiftResult`` -- no extra negation, unlike
``skimage.phase_cross_correlation`` (which needed one). Verified empirically
by ``--self-check`` (see module docstring in the pre-registration memo and
the synthetic np.roll check below) before any real row was processed.

All real data outputs go under ``~/zasolar_data/geid_temporal/`` -- nothing
but this script + the memo lives in the repo.
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

GT_ROOT = Path("/home/gaosh/zasolar_data/geid_temporal")
AUDIT_DIR = GT_ROOT / "gehi_displacement_audit_2026-07-06"
OFFSETS_CSV = AUDIT_DIR / "per_chipdate_offsets.csv"
VEXCEL_CROPS = AUDIT_DIR / "vexcel_crops"
DEFAULT_OUTPUT_DIR = GT_ROOT / "pilot_learned_match_2026-07-09"

GSD_M = 0.15  # match the audit's / DINO diagnostic's registration grid
DEFAULT_PSR_FLOOR = 12.0
DEFAULT_OFFSET_FLOOR_M = 5.0

# Primary "recovered" tolerance -- computed EXACTLY as the DINO baseline did
# (pre-registration memo): 1.5 * cell_diag_m, cell_diag_m = hypot(extent_y_m/37,
# extent_x_m/37), extent = per-row chip UTM extent at GSD 0.15m. The "/37" is
# NOT derived from this matcher (SuperPoint has no patch-token grid) -- it is
# fixed to the DINO baseline's 37x37/518px tier so the recovered rate is
# row-by-row comparable to the 39.4% DINO ceiling this pilot benchmarks against.
RECOVERED_TOL_CELLS = 1.5
DINO_BASELINE_GRID_SIDE = 37
SECONDARY_TOL_M = (2.59, 1.0)  # report-only, never judged against the kill bar

KILL_BAR = 0.70

DEFAULT_MAX_KEYPOINTS = 2048
DEFAULT_RANSAC_THRESH_PX = 5.0  # ~0.75m at GSD 0.15m


# --------------------------------------------------------------------------- #
# Population + join tables (population loader / find_anchor_dir / find_mov_path
# copied from diagnose_dino_positive_control_2026-07-08.py -- not importable,
# dash in filename; that script is READ-ONLY reference, never modified).       #
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


def load_weak_lock_population(psr_ceiling: float) -> list[dict]:
    """PSR<ceiling S3_vexcel rows -- no trusted GT (phase-correlation itself
    distrusts these locks); used only for the bounded Step-6 probe, never
    judged against the kill bar."""
    with OFFSETS_CSV.open() as f:
        return [
            r
            for r in csv.DictReader(f)
            if r["ref_kind"] == "S3_vexcel" and float(r["psr"]) < psr_ceiling
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
# Per-anchor ref cache (ref-side reprojection done once per chip_id)          #
# --------------------------------------------------------------------------- #

@dataclass
class RefCache:
    metric_crs: str
    transform: object
    shape: tuple
    ref_gray: np.ndarray


def build_ref_cache(chip_id: str, target: dict) -> RefCache | None:
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
    return RefCache(metric_crs=metric_crs, transform=transform, shape=shape, ref_gray=ref_gray)


# --------------------------------------------------------------------------- #
# SuperPoint + LightGlue matcher                                              #
# --------------------------------------------------------------------------- #

MATCHER_CHOICES = ("superpoint_lightglue", "loftr")


def load_matcher(matcher_name: str, device: str, max_keypoints: int) -> dict:
    """Arm 1 (default): SuperPoint (sparse detector) + LightGlue (attention
    matcher). Arm 2 (gated behind arm-1 failing the kill bar, per ISSUE-24
    §1 / the pre-registration memo): kornia-native LoFTR -- detector-free,
    dense coarse-to-fine transformer matching, already installed
    (kornia==0.8.2 ships ``kornia.feature.LoFTR``, unlike SuperPoint)."""
    if matcher_name == "superpoint_lightglue":
        from lightglue import LightGlue, SuperPoint

        extractor = SuperPoint(max_num_keypoints=max_keypoints).eval().to(device)
        lg = LightGlue(features="superpoint").eval().to(device)
        return {"extractor": extractor, "matcher": lg}
    if matcher_name == "loftr":
        import kornia.feature as KF

        loftr = KF.LoFTR(pretrained="outdoor").eval().to(device)
        return {"matcher": loftr}
    raise ValueError(f"unknown matcher {matcher_name!r}, expected one of {MATCHER_CHOICES}")


def gray_to_tensor(gray: np.ndarray, device: str):
    import torch

    arr = np.clip(np.asarray(gray, dtype=np.float32), 0.0, 1.0)
    return torch.from_numpy(arr[None]).float().to(device)  # (1, H, W) grayscale in [0,1]


def get_matched_keypoints(matcher_name: str, models: dict, ref_gray: np.ndarray, mov_gray: np.ndarray, device: str):
    """Returns ``(kpts0, kpts1)`` -- same-length arrays of matched pixel
    coordinates, row ``i`` of ``kpts0`` corresponding to row ``i`` of
    ``kpts1`` (the same physical point as seen in ref / mov). Both arms
    return ``(x, y)`` = ``(col, row)`` coordinates (SuperPoint: confirmed
    against ``lightglue``'s source, see module docstring; LoFTR: kornia's
    ``keypoints0``/``keypoints1`` are pixel ``(x, y)`` by convention,
    confirmed by the synthetic np.roll check used for both arms before any
    real pair)."""
    import torch

    if matcher_name == "superpoint_lightglue":
        from lightglue.utils import rbd

        t_ref = gray_to_tensor(ref_gray, device)
        t_mov = gray_to_tensor(mov_gray, device)
        with torch.no_grad():
            feats0 = models["extractor"].extract(t_ref)
            feats1 = models["extractor"].extract(t_mov)
            matches01 = models["matcher"]({"image0": feats0, "image1": feats1})
        feats0, feats1, matches01 = (rbd(x) for x in (feats0, feats1, matches01))
        kpts0 = feats0["keypoints"].detach().cpu().numpy()
        kpts1 = feats1["keypoints"].detach().cpu().numpy()
        idx = matches01["matches"].detach().cpu().numpy()
        if len(idx) == 0:
            return np.zeros((0, 2)), np.zeros((0, 2))
        return kpts0[idx[:, 0]], kpts1[idx[:, 1]]
    if matcher_name == "loftr":
        t_ref = gray_to_tensor(ref_gray, device)[None]  # (1, 1, H, W)
        t_mov = gray_to_tensor(mov_gray, device)[None]
        with torch.no_grad():
            out = models["matcher"]({"image0": t_ref, "image1": t_mov})
        kpts0 = out["keypoints0"].detach().cpu().numpy()
        kpts1 = out["keypoints1"].detach().cpu().numpy()
        return kpts0, kpts1
    raise ValueError(f"unknown matcher {matcher_name!r}, expected one of {MATCHER_CHOICES}")


def ransac_translation(disp: np.ndarray, *, thresh_px: float, max_hypotheses: int = 3000, seed: int = 0) -> tuple[float, float, np.ndarray]:
    """Exhaustive (up to ``max_hypotheses``) 1-point RANSAC over per-match
    displacement vectors.

    A pure-translation model is fully determined by ONE correspondence, so
    every match's own displacement vector is a hypothesis; the hypothesis
    with the largest support within ``thresh_px`` wins and the final estimate
    is the mean of its inlier set. Exhaustive (every match tried as the
    hypothesis) rather than randomly sampled when ``n <= max_hypotheses``:
    with SuperPoint capped at a few hundred-to-~2k matches per pair this is
    at worst O(n^2) on a tiny array (trivially cheap) and it removes RANSAC's
    own sampling randomness as a confound on top of the matcher itself -- a
    deterministic instantiation of the "direct 1-point RANSAC on displacement
    vectors" option in ISSUE-24. LoFTR's dense correspondences can exceed
    ``max_hypotheses``; above that a fixed-seed random subsample of
    hypotheses is used instead (still deterministic given ``seed``), to keep
    runtime bounded without changing the estimator.
    """
    n = len(disp)
    if n == 0:
        return 0.0, 0.0, np.zeros(0, dtype=bool)
    if n == 1:
        return float(disp[0, 0]), float(disp[0, 1]), np.ones(1, dtype=bool)
    if n > max_hypotheses:
        rng = np.random.default_rng(seed)
        hyp_idx = rng.choice(n, size=max_hypotheses, replace=False)
    else:
        hyp_idx = np.arange(n)
    best_inliers = np.zeros(n, dtype=bool)
    best_count = -1
    for i in hyp_idx:
        d = np.linalg.norm(disp - disp[i], axis=1)
        inliers = d <= thresh_px
        cnt = int(inliers.sum())
        if cnt > best_count:
            best_count = cnt
            best_inliers = inliers
    dx, dy = disp[best_inliers].mean(axis=0)
    return float(dx), float(dy), best_inliers


def match_translation(matcher_name: str, models: dict, ref_gray: np.ndarray, mov_gray: np.ndarray, device: str, ransac_thresh_px: float) -> dict:
    kpts0, kpts1 = get_matched_keypoints(matcher_name, models, ref_gray, mov_gray, device)
    n_matches = int(len(kpts0))
    if n_matches == 0:
        return {"est_dx_px": 0.0, "est_dy_px": 0.0, "n_matches": 0, "n_inliers": 0, "inlier_ratio": 0.0}
    disp = kpts1 - kpts0  # (dx_px, dy_px) per match, mov - ref -- see get_matched_keypoints docstring
    dx_px, dy_px, inlier_mask = ransac_translation(disp, thresh_px=ransac_thresh_px)
    n_inliers = int(inlier_mask.sum())
    return {
        "est_dx_px": dx_px,
        "est_dy_px": dy_px,
        "n_matches": n_matches,
        "n_inliers": n_inliers,
        "inlier_ratio": n_inliers / n_matches,
    }


# --------------------------------------------------------------------------- #
# Self-check (mandatory before any real pair)                                 #
# --------------------------------------------------------------------------- #

def run_self_check(matcher_name: str, models: dict, device: str, args, output_dir: Path) -> bool:
    """Synthetic sign check: mov = np.roll(ref, known integer pixel shift).

    ``np.roll(arr, shift=(dy, dx), axis=(0, 1))`` moves content to HIGHER row
    index by ``dy`` and HIGHER column index by ``dx`` -- i.e. the rolled
    image's content sits ``dy`` rows below / ``dx`` cols right of the
    original's, which is exactly the ``ShiftResult`` (mov below/right of ref)
    convention this pilot targets. If ``match_translation`` returns
    ``(est_dx_px, est_dy_px)`` matching ``(dx, dy)`` in sign and magnitude,
    the estimator's convention is confirmed correct end-to-end (keypoint
    convention, match indexing, RANSAC) -- fix the estimator, not the
    comparison, if this fails.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    targets = load_targets(CHIP_TARGETS)
    ref_gray = None
    chosen_cid = None
    for cid, tgt in targets.items():
        if not (DISTILL_CHIPS / cid).is_dir() and not (PANEL_REPAIR_CHIPS / cid).is_dir():
            continue
        rc = build_ref_cache(cid, tgt)
        if rc is not None:
            ref_gray = rc.ref_gray
            chosen_cid = cid
            break
    if ref_gray is None:
        print("SELF-CHECK FAIL: no usable ref image found on disk", flush=True)
        (output_dir / "self_check.txt").write_text("FAIL: no usable ref image found\n")
        return False

    known_dy_px, known_dx_px = args.self_check_shift
    mov_gray = np.roll(ref_gray, shift=(known_dy_px, known_dx_px), axis=(0, 1))

    m = match_translation(matcher_name, models, ref_gray, mov_gray, device, args.ransac_thresh_px)
    est_dx_px, est_dy_px = m["est_dx_px"], m["est_dy_px"]
    err_px = float(np.hypot(est_dx_px - known_dx_px, est_dy_px - known_dy_px))
    tol_px = 2.0
    passed = err_px <= tol_px and m["n_matches"] > 0

    lines = [
        f"Self-check (2026-07-09, matcher={matcher_name}): mov = np.roll(ref, shift=(dy,dx), axis=(0,1))",
        f"ref chip_id: {chosen_cid}",
        f"known shift (dy_px, dx_px): ({known_dy_px}, {known_dx_px})",
        f"estimated (dy_px, dx_px): ({est_dy_px:.3f}, {est_dx_px:.3f})",
        f"n_matches={m['n_matches']} n_inliers={m['n_inliers']} inlier_ratio={m['inlier_ratio']:.3f}",
        f"error_px={err_px:.3f} tol_px={tol_px}",
        f"VERDICT: {'PASS' if passed else 'FAIL'}",
    ]
    text = "\n".join(lines)
    print(text, flush=True)
    (output_dir / "self_check.txt").write_text(text + "\n")
    return passed


# --------------------------------------------------------------------------- #
# Main pilot loop                                                             #
# --------------------------------------------------------------------------- #

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--matcher", choices=list(MATCHER_CHOICES), default="superpoint_lightglue")
    ap.add_argument("--population", choices=["control", "weak_lock"], default="control")
    ap.add_argument("--limit", type=int, default=None, help="cap #population rows (smoke run)")
    ap.add_argument("--seed", type=int, default=0, help="row-order shuffle seed, for --limit subsampling")
    ap.add_argument("--psr-floor", type=float, default=DEFAULT_PSR_FLOOR)
    ap.add_argument("--offset-floor-m", type=float, default=DEFAULT_OFFSET_FLOOR_M)
    ap.add_argument("--psr-ceiling", type=float, default=DEFAULT_PSR_FLOOR, help="--population weak_lock: psr < this")
    ap.add_argument("--max-keypoints", type=int, default=DEFAULT_MAX_KEYPOINTS)
    ap.add_argument("--ransac-thresh-px", type=float, default=DEFAULT_RANSAC_THRESH_PX)
    ap.add_argument("--confident-inlier-threshold", type=int, default=None,
                     help="--population weak_lock only: n_inliers threshold for the confident-lock rate")
    ap.add_argument("--device", type=str, default=None)
    ap.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    ap.add_argument("--self-check", action="store_true", help="run the synthetic sign self-check and exit")
    ap.add_argument("--self-check-shift", type=int, nargs=2, default=(7, -4), metavar=("DY_PX", "DX_PX"))
    args = ap.parse_args()

    import torch

    device = args.device or ("cuda" if torch.cuda.is_available() else "cpu")
    args.output_dir.mkdir(parents=True, exist_ok=True)

    print(f"loading matcher={args.matcher} on {device}...", flush=True)
    models = load_matcher(args.matcher, device, args.max_keypoints)

    if args.self_check:
        ok = run_self_check(args.matcher, models, device, args, args.output_dir)
        raise SystemExit(0 if ok else 1)

    has_gt = args.population == "control"
    if has_gt:
        print(f"loading positive control (psr>={args.psr_floor}, offset>={args.offset_floor_m}m)...", flush=True)
        population = load_positive_control(args.psr_floor, args.offset_floor_m)
    else:
        print(f"loading weak-lock population (psr<{args.psr_ceiling})...", flush=True)
        population = load_weak_lock_population(args.psr_ceiling)

    if args.limit:
        rng = np.random.default_rng(args.seed)
        idx = rng.permutation(len(population))[: args.limit]
        population = [population[i] for i in idx]
    print(f"population: {len(population)} rows (population={args.population})", flush=True)

    targets = load_targets(CHIP_TARGETS)

    ref_cache: dict[str, RefCache | None] = {}
    n_missing_anchor = n_missing_vexcel = n_missing_mov = 0
    rows_out: list[dict] = []

    if device == "cuda":
        torch.cuda.reset_peak_memory_stats()
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
                rc = build_ref_cache(cid, tgt)
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

        t_row0 = time.perf_counter()
        m = match_translation(args.matcher, models, ref.ref_gray, mov_gray, device, args.ransac_thresh_px)
        row_time_s = time.perf_counter() - t_row0

        est_dx_m = m["est_dx_px"] * GSD_M
        est_dy_m = m["est_dy_px"] * GSD_M
        est_offset_m = float(np.hypot(est_dx_m, est_dy_m))

        extent_y_m = ref.shape[0] * GSD_M
        extent_x_m = ref.shape[1] * GSD_M
        cell_diag_m = float(np.hypot(extent_y_m / DINO_BASELINE_GRID_SIDE, extent_x_m / DINO_BASELINE_GRID_SIDE))
        recovered_tol_m = RECOVERED_TOL_CELLS * cell_diag_m

        out = {
            "chip_id": cid,
            "matcher": args.matcher,
            "grid_id": row["grid_id"],
            "capture_date": row["capture_date"],
            "area_bucket": row.get("area_bucket", ""),
            "psr": row.get("psr", ""),
            "phasecorr_dx_m": row.get("dx_m", ""),
            "phasecorr_dy_m": row.get("dy_m", ""),
            "phasecorr_offset_m": row.get("best_offset_m", ""),
            "est_dx_m": round(est_dx_m, 3),
            "est_dy_m": round(est_dy_m, 3),
            "est_offset_m": round(est_offset_m, 3),
            "n_matches": m["n_matches"],
            "n_inliers": m["n_inliers"],
            "inlier_ratio": round(m["inlier_ratio"], 4),
            "row_time_s": round(row_time_s, 3),
        }
        if has_gt:
            known_dx_m = float(row["dx_m"])
            known_dy_m = float(row["dy_m"])
            error_m = float(np.hypot(est_dx_m - known_dx_m, est_dy_m - known_dy_m))
            out.update(
                {
                    "known_dx_m": round(known_dx_m, 3),
                    "known_dy_m": round(known_dy_m, 3),
                    "cell_diag_m": round(cell_diag_m, 3),
                    "recovered_tol_m": round(recovered_tol_m, 3),
                    "error_m": round(error_m, 3),
                    "recovered": error_m <= recovered_tol_m,
                    "recovered_2_59m": error_m <= SECONDARY_TOL_M[0],
                    "recovered_1_0m": error_m <= SECONDARY_TOL_M[1],
                }
            )
        else:
            out["confident_lock"] = (
                args.confident_inlier_threshold is not None and m["n_inliers"] >= args.confident_inlier_threshold
            )
        rows_out.append(out)
        if k % 25 == 0:
            elapsed = time.perf_counter() - t_start
            print(f"  {k}/{len(population)} rows, {elapsed:.0f}s elapsed", flush=True)

    total_elapsed = time.perf_counter() - t_start
    peak_mem_mb = (torch.cuda.max_memory_allocated() / 1e6) if device == "cuda" else None
    print(
        f"DONE: {len(rows_out)} rows processed, "
        f"missing_anchor={n_missing_anchor} missing_vexcel={n_missing_vexcel} missing_mov={n_missing_mov}, "
        f"{total_elapsed:.0f}s total, peak_gpu_mb={peak_mem_mb}",
        flush=True,
    )

    _write_csv(args.output_dir / "pilot_results.csv", rows_out)
    if has_gt:
        _write_summary_control(
            args.output_dir / "pilot_summary.txt", rows_out, n_missing_anchor, n_missing_vexcel, n_missing_mov,
            len(population), total_elapsed, peak_mem_mb, args,
        )
    else:
        _write_summary_weak_lock(
            args.output_dir / "pilot_summary.txt", rows_out, n_missing_anchor, n_missing_vexcel, n_missing_mov,
            len(population), total_elapsed, peak_mem_mb, args,
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


def _rate(rows: list[dict], key: str) -> tuple[int, int, float]:
    n = len(rows)
    r = sum(1 for x in rows if x[key])
    return r, n, (r / n if n else float("nan"))


def _q(vals: list[float], p: float):
    return round(float(np.percentile(vals, p)), 3) if vals else None


def _write_summary_control(
    path: Path, rows: list[dict], n_missing_anchor: int, n_missing_vexcel: int, n_missing_mov: int,
    n_population: int, total_elapsed: float, peak_mem_mb: float | None, args,
) -> None:
    matcher_label = rows[0]["matcher"] if rows else args.matcher
    lines: list[str] = []
    lines.append(f"Learned-matching positive-control pilot (2026-07-09) -- matcher={matcher_label}")
    lines.append(
        f"population: {n_population} rows (ref_kind=S3_vexcel, psr>={args.psr_floor}, "
        f"best_offset_m>={args.offset_floor_m})"
    )
    lines.append(
        f"processed: {len(rows)}  missing_anchor={n_missing_anchor} "
        f"missing_vexcel={n_missing_vexcel} missing_mov={n_missing_mov}"
    )
    lines.append(f"total_elapsed_s={total_elapsed:.1f}  s/row={total_elapsed / max(1, len(rows)):.3f}  peak_gpu_mb={peak_mem_mb}")
    lines.append(f"ransac_thresh_px={args.ransac_thresh_px}  max_keypoints={args.max_keypoints}")
    lines.append("")

    r, n, rate = _rate(rows, "recovered")
    lines.append(f"[PRIMARY: error_m <= 1.5*cell_diag_m (~5.6m, DINO-baseline-comparable)]")
    lines.append(f"  recovered rate: {rate:.1%} ({r}/{n})")
    lines.append(f"  DINO-coarse best (session 2+3, same population/tolerance): 39.4% (54/137)")
    lines.append(f"  KILL BAR: {KILL_BAR:.0%}  ->  VERDICT: {'GO' if rate >= KILL_BAR else 'KILL'}")
    lines.append("")

    for tag, key in (("<=2.59m", "recovered_2_59m"), ("<=1.0m", "recovered_1_0m")):
        r2, n2, rate2 = _rate(rows, key)
        lines.append(f"[SECONDARY, report-only: error_m {tag}]  {rate2:.1%} ({r2}/{n2})")
    lines.append("")

    errs = [x["error_m"] for x in rows]
    lines.append(f"error_m: p50={_q(errs,50)} p90={_q(errs,90)} max={round(max(errs),3) if errs else None}")
    lines.append("")

    lines.append("By area bucket (recovered rate):")
    for ab in sorted({x["area_bucket"] for x in rows}):
        sub = [x for x in rows if x["area_bucket"] == ab]
        _, _, rr = _rate(sub, "recovered")
        lines.append(f"  {ab:14s} recovered={rr:6.1%} (n={len(sub)})")
    lines.append("")

    lines.append("Inlier-count / inlier-ratio distribution, split recovered vs not-recovered:")
    for label, sub in (("recovered", [x for x in rows if x["recovered"]]), ("not_recovered", [x for x in rows if not x["recovered"]])):
        n_inliers = [x["n_inliers"] for x in sub]
        n_matches = [x["n_matches"] for x in sub]
        inlier_ratios = [x["inlier_ratio"] for x in sub]
        lines.append(
            f"  {label:14s} n={len(sub):3d}  n_inliers p25/p50/p75={_q(n_inliers,25)}/{_q(n_inliers,50)}/{_q(n_inliers,75)}"
            f"  n_matches p50={_q(n_matches,50)}  inlier_ratio p50={_q(inlier_ratios,50)}"
        )
    lines.append("")

    # Threshold sweep: what n_inliers cut achieves >=95% precision among recovered rows?
    # (precision = P(recovered | n_inliers >= t)); informs the weak-lock probe's
    # --confident-inlier-threshold, per ISSUE-24 acceptance criteria.
    lines.append("n_inliers threshold sweep (precision = P(recovered | n_inliers>=t), coverage = frac rows kept):")
    if rows:
        cand_thresholds = sorted({x["n_inliers"] for x in rows})
        for t in cand_thresholds:
            kept = [x for x in rows if x["n_inliers"] >= t]
            if not kept:
                continue
            prec = sum(1 for x in kept if x["recovered"]) / len(kept)
            cov = len(kept) / len(rows)
            marker = "  <-- >=95% precision" if prec >= 0.95 else ""
            lines.append(f"  t={t:4d}  precision={prec:.1%}  coverage={cov:.1%}  n={len(kept)}{marker}")
    lines.append("")

    text = "\n".join(lines)
    path.write_text(text + "\n")
    print(text, flush=True)


def _write_summary_weak_lock(
    path: Path, rows: list[dict], n_missing_anchor: int, n_missing_vexcel: int, n_missing_mov: int,
    n_population: int, total_elapsed: float, peak_mem_mb: float | None, args,
) -> None:
    matcher_label = rows[0]["matcher"] if rows else args.matcher
    lines: list[str] = []
    lines.append(f"Learned-matching weak-lock probe (2026-07-09) -- matcher={matcher_label} -- bounded, no trusted GT, NOT a production commitment")
    lines.append(f"population: psr<{args.psr_ceiling} S3_vexcel (2,785-row weak-lock population; this run: subsample n={n_population})")
    lines.append(
        f"processed: {len(rows)}  missing_anchor={n_missing_anchor} "
        f"missing_vexcel={n_missing_vexcel} missing_mov={n_missing_mov}"
    )
    lines.append(f"total_elapsed_s={total_elapsed:.1f}  s/row={total_elapsed / max(1, len(rows)):.3f}  peak_gpu_mb={peak_mem_mb}")
    lines.append(f"confident_inlier_threshold={args.confident_inlier_threshold} (calibrated on the positive-control run)")
    lines.append("")

    if args.confident_inlier_threshold is not None:
        r, n, rate = _rate(rows, "confident_lock")
        lines.append(f"confident-lock rate (n_inliers >= {args.confident_inlier_threshold}): {rate:.1%} ({r}/{n})")
    else:
        lines.append("confident-lock rate: N/A (--confident-inlier-threshold not set)")
    lines.append("")

    offs = [x["est_offset_m"] for x in rows]
    phase_offs = [float(x["phasecorr_offset_m"]) for x in rows if x["phasecorr_offset_m"] not in ("", None)]
    lines.append(f"est_offset_m (this pilot): p50={_q(offs,50)} p90={_q(offs,90)} max={round(max(offs),3) if offs else None}")
    lines.append(f"phasecorr_offset_m (untrusted point estimate already in the CSV, for reference only): p50={_q(phase_offs,50)} p90={_q(phase_offs,90)}")
    lines.append("")

    n_inliers = [x["n_inliers"] for x in rows]
    inlier_ratios = [x["inlier_ratio"] for x in rows]
    lines.append(f"n_inliers: p25/p50/p75={_q(n_inliers,25)}/{_q(n_inliers,50)}/{_q(n_inliers,75)}  inlier_ratio p50={_q(inlier_ratios,50)}")

    text = "\n".join(lines)
    path.write_text(text + "\n")
    print(text, flush=True)


if __name__ == "__main__":
    main()
