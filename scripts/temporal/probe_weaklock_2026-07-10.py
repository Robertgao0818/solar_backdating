#!/usr/bin/env python3
"""ISSUE-24 Step-6: weak-lock characterization probe (SuperPoint+LightGlue).

Pre-registration (fixed before any probe-row matcher run or Gemini call):
``docs/replan_v2/DATA-weaklock-probe-2026-07-10.md``. Read that memo first --
it fixes the population, the calibration re-sweep, the probe metrics, the
judge protocol, the competence gate, and the fail-closed rules this script
implements.

Framing: characterization probe, NOT a GO/KILL verdict, NOT a production
commitment (see the memo). No kill bar is applied anywhere in this script.

Reuses (via ``importlib`` -- the source has a dash in its filename, and is
frozen: read-only, never modified) ``pilot_learned_match_2026-07-09.py``'s
population loader, matcher loader/self-check/``match_translation``, and
ref/mov reprojection helpers. Overlay rendering + the Gemini judge-call
wrapper are new code in this file, following the same pattern validated by
``readjudicate_learned_match_gt_2026-07-09.py`` (also frozen; read for
pattern, not imported -- its 3-image ``{unshifted,A,B}`` scheme is reused
verbatim for the competence gate here, but doesn't fit this probe's 2-image
``{A,B}`` unshifted-vs-aligned real-item comparison, which needed a new
prompt variant).

Nothing but this script and the memo lives in the repo. All real outputs go
under ``~/zasolar_data/geid_temporal/weaklock_probe_2026-07-10/``.
"""
from __future__ import annotations

import argparse
import csv
import importlib.util
import io
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
from PIL import Image
from scipy.ndimage import shift as nd_shift

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

PILOT_SCRIPT = REPO_ROOT / "scripts" / "temporal" / "pilot_learned_match_2026-07-09.py"
_spec = importlib.util.spec_from_file_location("pilot_learned_match", PILOT_SCRIPT)
pilot = importlib.util.module_from_spec(_spec)
sys.modules["pilot_learned_match"] = pilot
_spec.loader.exec_module(pilot)

from scripts.temporal import chip_displacement as cd  # noqa: E402
from scripts.temporal.audit_gehi_displacement import CHIP_TARGETS, load_targets  # noqa: E402
from scripts.validation.gemini_solar_image_review import (  # noqa: E402
    RateLimiter,
    env_value,
    extract_json_object,
    load_env_file,
    post_native_generate_content,
)

GSD_M = 0.15
GT_ROOT = Path("~/zasolar_data/geid_temporal").expanduser()
PILOT_137_CSV = GT_ROOT / "pilot_learned_match_2026-07-09" / "full137" / "pilot_results.csv"
READJUD_43_CSV = GT_ROOT / "gt_readjudication_2026-07-09" / "readjudication_43_results.csv"
OUT_DIR = GT_ROOT / "weaklock_probe_2026-07-10"
IMG_DIR = OUT_DIR / "overlays"

ENV_FILE = REPO_ROOT / ".env.gemini.local"
DEFAULT_MODEL = "gemini-3-flash-agent"
DEFAULT_NATIVE_PATH = "/v1beta"
DEFAULT_MAX_TOKENS = 4000
DEFAULT_TIMEOUT = 300
DEFAULT_QPS = 1.0
MAX_ATTEMPTS = 3  # 1 initial + 2 retries, per the pre-registration's fail-closed rule

JPEG_SIDE = 560
JPEG_QUALITY = 85

PROBE_N = 150
PROBE_SEED = 0
PSR_CEILING = 12.0
JUDGE_OFFSET_FLOOR_M = 2.0
JUDGE_N = 40
JUDGE_SEED = 0
FAKE_OFFSET_M = 13.0  # same decoy magnitude as the re-adjudication (inside its error_m p90=14.573m)

TRUSTED_REF_P50_M = 0.96  # ISSUE-23 audit memo Sec3, S3(Vexcel) trusted table @ psr>=12
TRUSTED_REF_P90_M = 3.81

CALIBRATION_PRECISION_BAR = 0.90
CALIBRATION_COVERAGE_BAR = 0.50

PROMPT_TEMPLATE_3IMG = """You are comparing two candidate image alignments to determine which one correctly registers two aerial/satellite photos of the same rooftop location, taken at different times.

You will see exactly 3 images, always in this order:
1. UNSHIFTED -- the two photos overlaid with no alignment correction applied (baseline, ambiguous).
2. ALIGNMENT A -- the two photos overlaid after applying candidate alignment A.
3. ALIGNMENT B -- the two photos overlaid after applying candidate alignment B.

In every overlay image, one photo is rendered in the RED channel and the other in the GREEN channel. Where a real-world edge (building outline, roofline, wall, driveway edge) is correctly aligned between the two photos, red and green cancel out into a neutral grey/olive edge. Where a real-world edge is misaligned, you see a doubled "ghost" edge with a visible red fringe on one side and a green fringe on the other side (like a cheap 3D-glasses effect).

Task: decide which of Alignment A or Alignment B makes man-made structure edges (building outlines, roof ridges, walls) register more cleanly -- more grey/olive edges, less red/green ghosting -- compared to each other and to the ambiguous UNSHIFTED baseline. If BOTH A and B still show clear red/green ghosting on structure edges (neither one visibly resolves the misalignment), answer "neither".

Focus on man-made structure edges, not on soft/organic content like tree canopies or shadows, which can look fringed even when the underlying photos are well aligned.

Return ONLY valid JSON, no markdown fences, no extra text, exactly this schema:
{"alignment_verdict": "A" | "B" | "neither", "confidence": 0.0-1.0, "reasoning": "<one brief sentence>"}

Keep "reasoning" to one short sentence."""

PROMPT_TEMPLATE_2IMG = """You are assessing a candidate image-alignment correction between two aerial/satellite photos of the same rooftop location, taken at different times.

You will see exactly 2 images, labeled ALIGNMENT A and ALIGNMENT B. Each shows the same two photos overlaid. One of the two is the photos with NO correction applied; the other is the photos after a translation correction has been applied. You are not told which is which.

In every overlay image, one photo is rendered in the RED channel and the other in the GREEN channel. Where a real-world edge (building outline, roofline, wall, driveway edge) is correctly aligned between the two photos, red and green cancel out into a neutral grey/olive edge. Where a real-world edge is misaligned, you see a doubled "ghost" edge with a visible red fringe on one side and a green fringe on the other side (like a cheap 3D-glasses effect).

Task: decide which of Alignment A or Alignment B makes man-made structure edges (building outlines, roof ridges, walls) register more cleanly -- more grey/olive edges, less red/green ghosting. If BOTH A and B show essentially the same amount of ghosting on structure edges (neither is a clear improvement over the other), answer "neither".

Focus on man-made structure edges, not on soft/organic content like tree canopies or shadows, which can look fringed even when the underlying photos are well aligned.

Return ONLY valid JSON, no markdown fences, no extra text, exactly this schema:
{"alignment_verdict": "A" | "B" | "neither", "confidence": 0.0-1.0, "reasoning": "<one brief sentence>"}

Keep "reasoning" to one short sentence."""


# --------------------------------------------------------------------------- #
# Overlay rendering (same recipe as readjudicate_learned_match_gt_2026-07-09)  #
# --------------------------------------------------------------------------- #

def _norm01(a: np.ndarray) -> np.ndarray:
    lo, hi = np.percentile(a, 1), np.percentile(a, 99)
    return np.clip((a.astype(np.float32) - lo) / max(hi - lo, 1e-6), 0, 1)


def _to_jpeg_bytes(img: Image.Image) -> bytes:
    if img.size[0] != JPEG_SIDE:
        img = img.resize((JPEG_SIDE, JPEG_SIDE), Image.LANCZOS)
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=JPEG_QUALITY, optimize=True)
    return buf.getvalue()


def render_overlay(ref: np.ndarray, mov_aligned: np.ndarray) -> Image.Image:
    """Red=ref, Green=mov_aligned. Aligned edges cancel to grey/olive; misaligned
    edges show red/green ghosting. Blue channel is the mean, for a viewable image."""
    r = _norm01(ref)
    g = _norm01(mov_aligned)
    rgb = np.zeros((*r.shape, 3), dtype=np.uint8)
    rgb[..., 0] = (r * 255).astype(np.uint8)
    rgb[..., 1] = (g * 255).astype(np.uint8)
    rgb[..., 2] = ((r * 0.5 + g * 0.5) * 255).astype(np.uint8)
    return Image.fromarray(rgb)


def align_mov(mov_gray: np.ndarray, dx_m: float, dy_m: float) -> np.ndarray:
    """Shift mov by (-dy_px, -dx_px) to pull its content onto ref's frame --
    mov's content sits (dx,dy) right/below ref's content in the shared
    convention, so subtracting the offset re-registers it (scipy shift order
    is (row, col) = (dy, dx))."""
    if dx_m == 0.0 and dy_m == 0.0:
        return mov_gray
    dx_px = dx_m / GSD_M
    dy_px = dy_m / GSD_M
    aligned = nd_shift(mov_gray, shift=(-dy_px, -dx_px), order=1, mode="constant", cval=np.nan)
    return np.nan_to_num(aligned, nan=float(np.nanmean(mov_gray)))


def render_slots(tag: str, ref_gray: np.ndarray, mov_gray: np.ndarray, slots: dict[str, tuple[float, float]]) -> dict[str, Path]:
    """Writes one overlay JPEG per named slot (e.g. {"unshifted": (0,0), "A": ..., "B": ...})."""
    IMG_DIR.mkdir(parents=True, exist_ok=True)
    paths = {}
    for label, (dx_m, dy_m) in slots.items():
        aligned = align_mov(mov_gray, dx_m, dy_m)
        img = render_overlay(ref_gray, aligned)
        p = IMG_DIR / f"{tag}_{label}.jpg"
        p.write_bytes(_to_jpeg_bytes(img))
        paths[label] = p
    return paths


# --------------------------------------------------------------------------- #
# Self-check (mandatory before any real row -- reuses the frozen pilot's own) #
# --------------------------------------------------------------------------- #

def run_matcher_self_check(models: dict, device: str) -> bool:
    args_ns = SimpleNamespace(self_check_shift=(7, -4), ransac_thresh_px=pilot.DEFAULT_RANSAC_THRESH_PX)
    out_dir = OUT_DIR / "self_check"
    return pilot.run_self_check("superpoint_lightglue", models, device, args_ns, out_dir)


# --------------------------------------------------------------------------- #
# Calibration re-sweep (pure recompute over already-computed 137+43 rows)      #
# --------------------------------------------------------------------------- #

def load_calibration_data() -> list[dict]:
    with PILOT_137_CSV.open() as f:
        pilot_rows = list(csv.DictReader(f))
    with READJUD_43_CSV.open() as f:
        readjud_rows = list(csv.DictReader(f))
    readjud_by_key = {(r["chip_id"], r["capture_date"]): r for r in readjud_rows}

    out = []
    for r in pilot_rows:
        key = (r["chip_id"], r["capture_date"])
        if r["recovered"] == "True":
            corrected_recovered = True
        elif key in readjud_by_key:
            corrected_recovered = readjud_by_key[key]["unblinded_verdict"] == "est"
        else:
            # Should not happen: every recovered==False row of the 137 was in
            # the 43-row adjudication population. Fail closed if it did.
            corrected_recovered = False
        out.append({**r, "corrected_recovered": corrected_recovered})
    return out


def calibration_sweep(rows: list[dict]) -> tuple[list[dict], int | None]:
    n = len(rows)
    thresholds = sorted({int(r["n_inliers"]) for r in rows})
    sweep = []
    chosen: int | None = None
    for t in thresholds:
        kept = [r for r in rows if int(r["n_inliers"]) >= t]
        if not kept:
            continue
        prec = sum(1 for r in kept if r["corrected_recovered"]) / len(kept)
        cov = len(kept) / n
        qualifies = prec >= CALIBRATION_PRECISION_BAR and cov >= CALIBRATION_COVERAGE_BAR
        sweep.append({"t": t, "precision": round(prec, 4), "coverage": round(cov, 4), "n": len(kept), "qualifies": qualifies})
        if qualifies and chosen is None:
            chosen = t  # lowest threshold clearing both bars -> maximizes coverage among qualifiers
    return sweep, chosen


# --------------------------------------------------------------------------- #
# 150-row weak-lock population pass (reuses pilot's population/matcher fns)   #
# --------------------------------------------------------------------------- #

def run_probe_population(models: dict, device: str, targets: dict, n_pop: int) -> tuple[list[dict], dict]:
    population = pilot.load_weak_lock_population(PSR_CEILING)
    rng = np.random.default_rng(PROBE_SEED)
    idx = rng.permutation(len(population))[:PROBE_N]  # always the pre-registered seed=0/150 draw
    sample = [population[i] for i in idx][:n_pop]  # n_pop < PROBE_N only for --smoke debug runs

    rows_out: list[dict] = []
    counts = {"n_sampled": len(sample), "missing_anchor": 0, "missing_vexcel": 0, "missing_mov": 0, "missing_reproject": 0}
    ref_cache: dict[str, Any] = {}
    t0 = time.perf_counter()
    for k, row in enumerate(sample, 1):
        cid = row["chip_id"]
        if cid not in ref_cache:
            tgt = targets.get(cid)
            anchor_dir = pilot.find_anchor_dir(cid)
            if tgt is None or anchor_dir is None:
                ref_cache[cid] = None
                counts["missing_anchor"] += 1
            else:
                rc = pilot.build_ref_cache(cid, tgt)
                ref_cache[cid] = rc
                if rc is None:
                    counts["missing_vexcel"] += 1
        ref = ref_cache[cid]
        if ref is None:
            continue

        anchor_dir = pilot.find_anchor_dir(cid)
        mov_path = pilot.find_mov_path(anchor_dir, row["capture_date"], row["version"]) if anchor_dir else None
        if mov_path is None:
            counts["missing_mov"] += 1
            continue

        try:
            mov_gray = cd.reproject_to_grid(mov_path, dst_crs=ref.metric_crs, dst_transform=ref.transform, dst_shape=ref.shape)
        except Exception:
            counts["missing_reproject"] += 1
            continue

        t_row0 = time.perf_counter()
        m = pilot.match_translation("superpoint_lightglue", models, ref.ref_gray, mov_gray, device, pilot.DEFAULT_RANSAC_THRESH_PX)
        row_time_s = time.perf_counter() - t_row0

        est_dx_m = m["est_dx_px"] * GSD_M
        est_dy_m = m["est_dy_px"] * GSD_M
        est_offset_m = float(np.hypot(est_dx_m, est_dy_m))

        extent_y_m = ref.shape[0] * GSD_M
        extent_x_m = ref.shape[1] * GSD_M
        cell_diag_m = float(np.hypot(extent_y_m / pilot.DINO_BASELINE_GRID_SIDE, extent_x_m / pilot.DINO_BASELINE_GRID_SIDE))
        cross_val_tol_m = pilot.RECOVERED_TOL_CELLS * cell_diag_m

        phase_dx_m = float(row["dx_m"]) if row.get("dx_m") not in (None, "") else None
        phase_dy_m = float(row["dy_m"]) if row.get("dy_m") not in (None, "") else None
        cross_error_m = None
        cross_validated = False
        if phase_dx_m is not None and phase_dy_m is not None:
            cross_error_m = float(np.hypot(est_dx_m - phase_dx_m, est_dy_m - phase_dy_m))
            cross_validated = cross_error_m <= cross_val_tol_m

        rows_out.append(
            {
                "chip_id": cid,
                "grid_id": row.get("grid_id", ""),
                "capture_date": row["capture_date"],
                "version": row.get("version", "0"),
                "area_bucket": row.get("area_bucket", ""),
                "psr": row.get("psr", ""),
                "phasecorr_dx_m": phase_dx_m if phase_dx_m is not None else "",
                "phasecorr_dy_m": phase_dy_m if phase_dy_m is not None else "",
                "phasecorr_offset_m": row.get("best_offset_m", ""),
                "est_dx_m": round(est_dx_m, 3),
                "est_dy_m": round(est_dy_m, 3),
                "est_offset_m": round(est_offset_m, 3),
                "n_matches": m["n_matches"],
                "n_inliers": m["n_inliers"],
                "inlier_ratio": round(m["inlier_ratio"], 4),
                "cross_val_tol_m": round(cross_val_tol_m, 3),
                "cross_error_m": round(cross_error_m, 3) if cross_error_m is not None else "",
                "cross_validated": cross_validated,
                "row_time_s": round(row_time_s, 3),
            }
        )
        if k % 25 == 0:
            print(f"  {k}/{len(sample)} rows, {time.perf_counter() - t0:.0f}s elapsed", flush=True)

    counts["n_processed"] = len(rows_out)
    counts["n_missing_total"] = counts["missing_anchor"] + counts["missing_vexcel"] + counts["missing_mov"] + counts["missing_reproject"]
    counts["elapsed_s"] = time.perf_counter() - t0
    return rows_out, counts


def build_ref_mov_for_row(chip_id: str, capture_date: str, version: str, targets: dict) -> tuple[np.ndarray, np.ndarray] | None:
    tgt = targets.get(chip_id)
    anchor_dir = pilot.find_anchor_dir(chip_id)
    if tgt is None or anchor_dir is None:
        return None
    rc = pilot.build_ref_cache(chip_id, tgt)
    if rc is None:
        return None
    mov_path = pilot.find_mov_path(anchor_dir, capture_date, version)
    if mov_path is None:
        return None
    try:
        mov_gray = cd.reproject_to_grid(mov_path, dst_crs=rc.metric_crs, dst_transform=rc.transform, dst_shape=rc.shape)
    except Exception:
        return None
    return rc.ref_gray, mov_gray


# --------------------------------------------------------------------------- #
# Gemini call wrapper (same pattern as readjudicate_learned_match_gt_2026-07-09)#
# --------------------------------------------------------------------------- #

@dataclass
class GatewayConfig:
    base_url: str
    api_key: str
    model: str
    native_path: str
    max_tokens: int
    timeout: int


def load_gateway_config() -> GatewayConfig:
    env = load_env_file(ENV_FILE)
    base_url = env_value(env, "GOOGLE_GEMINI_BASE_URL")
    api_key = env_value(env, "GEMINI_API_KEY")
    model = env_value(env, "GEMINI_MODEL", DEFAULT_MODEL)
    native_path = env_value(env, "GEMINI_NATIVE_PATH", DEFAULT_NATIVE_PATH)
    max_tokens = int(env_value(env, "GEMINI_MAX_TOKENS_PER_CHIP", str(DEFAULT_MAX_TOKENS)))
    timeout = int(env_value(env, "GEMINI_TIMEOUT", str(DEFAULT_TIMEOUT)))
    if not base_url or not api_key:
        raise SystemExit("Missing GOOGLE_GEMINI_BASE_URL or GEMINI_API_KEY in .env.gemini.local")
    return GatewayConfig(base_url, api_key, model, native_path, max_tokens, timeout)


def smoke_test(cfg: GatewayConfig) -> bool:
    try:
        resp = post_native_generate_content(
            base_url=cfg.base_url,
            native_path=cfg.native_path,
            api_key=cfg.api_key,
            model=cfg.model,
            prompt="Reply with exactly this JSON and nothing else: {\"ok\": true}",
            image_paths=[],
            max_tokens=200,
            timeout=cfg.timeout,
        )
        chunks = []
        for cand in resp.get("candidates") or []:
            for part in (cand.get("content") or {}).get("parts") or []:
                if part.get("text"):
                    chunks.append(part["text"])
        text = "\n".join(chunks)
        print(f"SMOKE TEST response: {text[:200]!r}", flush=True)
        return bool(text.strip())
    except Exception as exc:  # noqa: BLE001
        print(f"SMOKE TEST FAILED: {type(exc).__name__}: {exc}", flush=True)
        return False


def judge_item(cfg: GatewayConfig, image_paths: list[Path], prompt: str, limiter: RateLimiter) -> dict[str, Any]:
    last_err = None
    for attempt in range(MAX_ATTEMPTS):
        limiter.wait()
        try:
            resp = post_native_generate_content(
                base_url=cfg.base_url,
                native_path=cfg.native_path,
                api_key=cfg.api_key,
                model=cfg.model,
                prompt=prompt,
                image_paths=image_paths,
                max_tokens=cfg.max_tokens,
                timeout=cfg.timeout,
                response_mime_type="application/json",
            )
        except Exception as exc:  # noqa: BLE001
            last_err = f"{type(exc).__name__}: {exc}"
            continue

        chunks = []
        for cand in resp.get("candidates") or []:
            for part in (cand.get("content") or {}).get("parts") or []:
                if part.get("text"):
                    chunks.append(part["text"])
        text = "\n".join(chunks)
        try:
            obj = extract_json_object(text)
        except Exception as exc:  # noqa: BLE001
            last_err = f"parse_error: {exc}: {text[:200]!r}"
            continue

        if not isinstance(obj, dict):
            last_err = f"not_object: {text[:200]!r}"
            continue
        verdict = obj.get("alignment_verdict")
        if verdict not in ("A", "B", "neither"):
            last_err = f"bad_verdict: {obj!r}"
            continue

        confidence = obj.get("confidence")
        try:
            confidence = float(confidence) if confidence is not None else None
        except (TypeError, ValueError):
            confidence = None

        return {
            "raw_verdict": verdict,
            "confidence": confidence,
            "reasoning": str(obj.get("reasoning", ""))[:500],
            "retries": attempt,
            "error": None,
        }

    return {"raw_verdict": "abstain", "confidence": None, "reasoning": "", "retries": MAX_ATTEMPTS - 1, "error": last_err}


# --------------------------------------------------------------------------- #
# Judge competence gate (10 synthetic controls, 3-image layout)                #
# --------------------------------------------------------------------------- #

def run_competence_gate(cfg: GatewayConfig, limiter: RateLimiter, targets: dict) -> tuple[list[dict], int, bool]:
    with PILOT_137_CSV.open() as f:
        pilot_rows = list(csv.DictReader(f))
    trues = [r for r in pilot_rows if r["recovered"] == "True"]

    rng_select = np.random.default_rng(0)
    rng_direction = np.random.default_rng(0)
    rng_assign = np.random.default_rng(0)

    idx = rng_select.choice(len(trues), size=10, replace=False)
    rows = [trues[i] for i in idx]

    items = []
    for row in rows:
        chip_id, capture_date = row["chip_id"], row["capture_date"]
        ref_mov = build_ref_mov_for_row(chip_id, capture_date, "0", targets)
        if ref_mov is None:
            print(f"SKIP synthetic {chip_id}/{capture_date}: could not build ref/mov", flush=True)
            continue
        ref_gray, mov_gray = ref_mov
        correct_dx, correct_dy = float(row["known_dx_m"]), float(row["known_dy_m"])
        theta = rng_direction.uniform(0, 2 * np.pi)
        fake_dx = correct_dx + FAKE_OFFSET_M * np.cos(theta)
        fake_dy = correct_dy + FAKE_OFFSET_M * np.sin(theta)
        correct_is_a = bool(rng_assign.integers(0, 2))
        slots = {
            "unshifted": (0.0, 0.0),
            "A": (correct_dx, correct_dy) if correct_is_a else (fake_dx, fake_dy),
            "B": (fake_dx, fake_dy) if correct_is_a else (correct_dx, correct_dy),
        }
        tag = f"synth_{chip_id}_{capture_date}"
        paths = render_slots(tag, ref_gray, mov_gray, slots)
        items.append(
            {
                "chip_id": chip_id, "capture_date": capture_date,
                "correct_dx_m": correct_dx, "correct_dy_m": correct_dy,
                "fake_dx_m": fake_dx, "fake_dy_m": fake_dy, "fake_direction_deg": np.degrees(theta),
                "correct_slot": "A" if correct_is_a else "B",
                "paths": paths,
            }
        )
    print(f"rendered {len(items)}/10 synthetic competence-gate controls", flush=True)

    results = []
    n_correct = 0
    for item in items:
        image_paths = [item["paths"]["unshifted"], item["paths"]["A"], item["paths"]["B"]]
        r = judge_item(cfg, image_paths, PROMPT_TEMPLATE_3IMG, limiter)
        unblinded = (
            "correct" if r["raw_verdict"] == item["correct_slot"]
            else ("fake" if r["raw_verdict"] in ("A", "B") else r["raw_verdict"])
        )
        is_correct = unblinded == "correct"
        n_correct += int(is_correct)
        results.append({**item, **r, "unblinded_verdict": unblinded, "is_correct": is_correct})
        print(f"  {item['chip_id']}/{item['capture_date']}: raw={r['raw_verdict']} unblinded={unblinded} correct={is_correct}", flush=True)

    return results, n_correct, n_correct >= 8


# --------------------------------------------------------------------------- #
# Judge arm: up-to-40 real probe items, 2-image {A,B} = {unshifted, sp_lg}     #
# --------------------------------------------------------------------------- #

def run_judge_arm(cfg: GatewayConfig, limiter: RateLimiter, targets: dict, selected_rows: list[dict]) -> list[dict]:
    rng_assign = np.random.default_rng(JUDGE_SEED)
    results = []
    for row in selected_rows:
        chip_id, capture_date, version = row["chip_id"], row["capture_date"], row.get("version", "0")
        ref_mov = build_ref_mov_for_row(chip_id, capture_date, version, targets)
        if ref_mov is None:
            print(f"SKIP judge item {chip_id}/{capture_date}: could not build ref/mov", flush=True)
            continue
        ref_gray, mov_gray = ref_mov
        est_dx, est_dy = row["est_dx_m"], row["est_dy_m"]
        splg_is_a = bool(rng_assign.integers(0, 2))
        slots = {
            "A": (est_dx, est_dy) if splg_is_a else (0.0, 0.0),
            "B": (0.0, 0.0) if splg_is_a else (est_dx, est_dy),
        }
        tag = f"judge_{chip_id}_{capture_date}"
        paths = render_slots(tag, ref_gray, mov_gray, slots)
        image_paths = [paths["A"], paths["B"]]
        r = judge_item(cfg, image_paths, PROMPT_TEMPLATE_2IMG, limiter)
        splg_slot = "A" if splg_is_a else "B"
        if r["raw_verdict"] == "abstain":
            unblinded = "abstain"
        elif r["raw_verdict"] == "neither":
            unblinded = "neither"
        elif r["raw_verdict"] == splg_slot:
            unblinded = "sp_lg"
        else:
            unblinded = "unshifted"
        sp_lg_better = unblinded == "sp_lg"
        results.append(
            {
                "chip_id": chip_id, "capture_date": capture_date, "area_bucket": row.get("area_bucket", ""),
                "est_dx_m": est_dx, "est_dy_m": est_dy, "est_offset_m": row["est_offset_m"],
                "n_inliers": row["n_inliers"], "splg_slot": splg_slot,
                **r, "unblinded_verdict": unblinded, "sp_lg_better": sp_lg_better,
            }
        )
        print(f"  {chip_id}/{capture_date}: raw={r['raw_verdict']} unblinded={unblinded} sp_lg_better={sp_lg_better}", flush=True)
    return results


# --------------------------------------------------------------------------- #
# CSV / summary writers                                                       #
# --------------------------------------------------------------------------- #

def _write_csv(path: Path, rows: list[dict], fieldnames: list[str] | None = None) -> None:
    if not rows:
        path.write_text("")
        return
    fn = fieldnames or list(rows[0].keys())
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fn, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def _q(vals: list[float], p: float):
    return round(float(np.percentile(vals, p)), 3) if vals else None


def write_summary(
    *, out_path: Path,
    n_corrected_recovered: int, sweep: list[dict], chosen_t: int | None,
    counts: dict, rows: list[dict], near_zero: list[dict], eligible: list[dict],
    competence_pass: bool | None, n_correct: int | None,
    judge_results: list[dict], judge_skipped_reason: str | None,
) -> None:
    lines = []
    lines.append("ISSUE-24 Step-6 weak-lock characterization probe -- results (2026-07-10)")
    lines.append("Framing: characterization probe, NOT a GO/KILL verdict, NOT a production commitment.")
    lines.append("")

    lines.append("[Calibration re-sweep, 137-row positive control, corrected labels]")
    lines.append(f"  corrected recovered: {n_corrected_recovered}/137")
    if chosen_t is not None:
        row = next(s for s in sweep if s["t"] == chosen_t)
        lines.append(f"  CONFIDENT-LOCK CUT: n_inliers>={chosen_t}  precision={row['precision']:.1%}  coverage={row['coverage']:.1%}")
    else:
        lines.append("  no n_inliers threshold clears >=90% precision at >=50% coverage -> n_inliers reported descriptively only")
    lines.append("")

    n_sampled = counts["n_sampled"]
    n_processed = counts["n_processed"]
    n_locked = sum(1 for r in rows if r["n_matches"] > 0)
    lines.append("[Metric a: attempt/lock rate]")
    lines.append(f"  sampled: {n_sampled}")
    lines.append(f"  attempted (ref+mov built): {n_processed}/{n_sampled} ({n_processed/n_sampled:.1%})" if n_sampled else "  attempted: n/a")
    lines.append(f"  locked (n_matches>0): {n_locked}/{n_sampled} ({n_locked/n_sampled:.1%})" if n_sampled else "  locked: n/a")
    lines.append(
        f"  missing: {counts['n_missing_total']}/{n_sampled}"
        f" (anchor={counts['missing_anchor']} vexcel={counts['missing_vexcel']}"
        f" mov={counts['missing_mov']} reproject={counts['missing_reproject']})"
    )
    lines.append("")

    n_cross = sum(1 for r in rows if r["cross_validated"])
    lines.append("[Metric b: cross-validation vs phase-correlation's own (untrusted) point estimate]")
    lines.append(f"  cross-validated (error <= ~5.6m tol): {n_cross}/{n_processed} ({n_cross/n_processed:.1%})" if n_processed else "  n/a")
    lines.append("")

    offs = [r["est_offset_m"] for r in rows]
    lines.append("[Metric c: offset-magnitude distribution]")
    lines.append(f"  this probe (SP+LG est_offset_m): p50={_q(offs,50)} p90={_q(offs,90)} max={round(max(offs),3) if offs else None}")
    lines.append(f"  trusted S3(Vexcel) reference (psr>=12 gate, ISSUE-23 audit Sec3): p50={TRUSTED_REF_P50_M} p90={TRUSTED_REF_P90_M}")
    lines.append("")

    lines.append(f"[Near-zero-correction class] est_offset_m < {JUDGE_OFFSET_FLOOR_M}m: {len(near_zero)}/{n_processed} rows -- cross-validation (b) is the only check for these" if n_processed else "")
    lines.append(f"[Judge-eligible] est_offset_m >= {JUDGE_OFFSET_FLOOR_M}m: {len(eligible)}/{n_processed} rows" if n_processed else "")
    lines.append("")

    lines.append("[Metric d: blinded Gemini judge]")
    if competence_pass is None:
        lines.append("  SKIPPED: gateway smoke test failed")
    elif not competence_pass:
        lines.append(f"  competence gate: {n_correct}/10 -> <8/10, judge arm SKIPPED (metrics a-c reported only)")
    elif judge_skipped_reason:
        lines.append(f"  SKIPPED: {judge_skipped_reason}")
    else:
        n_judged = len(judge_results)
        n_better = sum(1 for r in judge_results if r["sp_lg_better"])
        lines.append(f"  competence gate: {n_correct}/10 -> PASS")
        lines.append(f"  SP+LG-aligned judged better: {n_better}/{n_judged} ({n_better/n_judged:.1%})" if n_judged else "  n/a")
        from collections import Counter
        cnt = Counter(r["unblinded_verdict"] for r in judge_results)
        lines.append(f"  category counts: {dict(cnt)}")
    lines.append("")

    text = "\n".join(lines)
    out_path.write_text(text + "\n")
    print(text, flush=True)


# --------------------------------------------------------------------------- #
# Main                                                                         #
# --------------------------------------------------------------------------- #

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--self-check", action="store_true", help="run only the mandatory synthetic sign self-check and exit")
    ap.add_argument("--skip-smoke-test", action="store_true", help="debug only")
    ap.add_argument("--qps", type=float, default=DEFAULT_QPS)
    ap.add_argument("--device", type=str, default=None)
    ap.add_argument(
        "--smoke", action="store_true",
        help="debug dry run: 5 population rows / 2 judge items, separate _smoke output files; "
             "NOT the pre-registered protocol, not used in any reported number",
    )
    args = ap.parse_args()

    import torch

    device = args.device or ("cuda" if torch.cuda.is_available() else "cpu")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    suffix = "_smoke" if args.smoke else ""
    n_pop = 5 if args.smoke else PROBE_N
    n_judge = 2 if args.smoke else JUDGE_N

    print(f"loading SuperPoint+LightGlue on {device}...", flush=True)
    models = pilot.load_matcher("superpoint_lightglue", device, pilot.DEFAULT_MAX_KEYPOINTS)

    print("running mandatory self-check...", flush=True)
    ok = run_matcher_self_check(models, device)
    if not ok:
        print("ABORT: self-check FAILED. Not proceeding to real rows.", flush=True)
        raise SystemExit(1)

    if args.self_check:
        raise SystemExit(0)

    print("running calibration re-sweep (137+43 corrected labels, no new matcher calls)...", flush=True)
    calib_rows = load_calibration_data()
    sweep, chosen_t = calibration_sweep(calib_rows)
    _write_csv(OUT_DIR / f"calibration_sweep{suffix}.csv", sweep, fieldnames=["t", "precision", "coverage", "n", "qualifies"])
    n_corrected_recovered = sum(1 for r in calib_rows if r["corrected_recovered"])
    print(f"corrected recovery on 137-row control: {n_corrected_recovered}/137", flush=True)
    if chosen_t is not None:
        print(f"CALIBRATION: n_inliers>={chosen_t} clears >=90% precision at >=50% coverage", flush=True)
    else:
        print("CALIBRATION: no threshold clears the bar -> n_inliers reported descriptively only", flush=True)

    targets = load_targets(CHIP_TARGETS)

    print(f"running {n_pop}-row weak-lock probe population (seed={PROBE_SEED})...", flush=True)
    rows, counts = run_probe_population(models, device, targets, n_pop)
    print(
        f"DONE: {counts['n_processed']} processed / {counts['n_sampled']} sampled, "
        f"missing_total={counts['n_missing_total']}, {counts['elapsed_s']:.0f}s",
        flush=True,
    )
    _write_csv(OUT_DIR / f"probe_150_results{suffix}.csv", rows)

    near_zero = [r for r in rows if r["est_offset_m"] < JUDGE_OFFSET_FLOOR_M]
    eligible = [r for r in rows if r["est_offset_m"] >= JUDGE_OFFSET_FLOOR_M]

    print("running gateway smoke test...", flush=True)
    cfg = load_gateway_config()
    gateway_ok = args.skip_smoke_test or smoke_test(cfg)

    competence_results: list[dict] = []
    n_correct = None
    competence_pass = None
    judge_results: list[dict] = []
    judge_skipped_reason = None

    if not gateway_ok:
        print("ABORT judge arm: gateway smoke test failed. Metrics a-c still reported.", flush=True)
    else:
        limiter = RateLimiter(args.qps)
        print("running judge competence gate (10 synthetic items)...", flush=True)
        competence_results, n_correct, competence_pass = run_competence_gate(cfg, limiter, targets)
        _write_csv(OUT_DIR / f"competence_gate_results{suffix}.csv", competence_results)
        print(f"COMPETENCE GATE: {n_correct}/10 -> {'PASS' if competence_pass else 'SKIP judge arm'}", flush=True)

        if competence_pass:
            rng_judge_select = np.random.default_rng(JUDGE_SEED)
            n_take = min(n_judge, len(eligible))
            idx = rng_judge_select.choice(len(eligible), size=n_take, replace=False) if eligible else np.array([], dtype=int)
            selected = [eligible[i] for i in idx]
            print(f"running judge arm on {len(selected)} items (eligible={len(eligible)})...", flush=True)
            judge_results = run_judge_arm(cfg, limiter, targets, selected)
            _write_csv(OUT_DIR / f"judge_results{suffix}.csv", judge_results)
        else:
            judge_skipped_reason = f"competence gate {n_correct}/10 < 8/10"

    write_summary(
        out_path=OUT_DIR / f"summary{suffix}.txt",
        n_corrected_recovered=n_corrected_recovered, sweep=sweep, chosen_t=chosen_t,
        counts=counts, rows=rows, near_zero=near_zero, eligible=eligible,
        competence_pass=competence_pass, n_correct=n_correct,
        judge_results=judge_results, judge_skipped_reason=judge_skipped_reason,
    )

    if not args.smoke:
        (OUT_DIR / "RUN_DONE").write_text("ok\n")
    print(f"outputs -> {OUT_DIR}", flush=True)
    print("PROBE_RUN_COMPLETE", flush=True)


if __name__ == "__main__":
    main()
