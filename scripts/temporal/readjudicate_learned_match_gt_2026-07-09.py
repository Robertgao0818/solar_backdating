#!/usr/bin/env python3
"""ISSUE-24 GT re-adjudication: blinded Gemini-judged overlay comparison for
the 43 `recovered==False` disagreement rows of the learned-matching pilot.

Pre-registration (fixed before any Gemini call on real data):
``docs/replan_v2/DATA-learned-matching-gt-readjudication-2026-07-09.md``.
Read that memo before touching this script -- it fixes the population, the
judge, the blinding scheme, the fail-closed rules, the competence gate, the
human-consistency gate, and the recompute rule this script implements.

Overlay rendering recipe (validated pre-verdict by a synthetic sign check,
see the memo): ``np.roll(ref, shift=(dy_px, dx_px))`` to build a synthetic
``mov``, corrected by ``scipy.ndimage.shift(mov, shift=(-dy_px, -dx_px))``,
recovers ``ref`` with MAE 0.0000. ``--self-check`` re-runs that round-trip
here. Ref/mov reprojection reuses (via importlib, no copy-paste)
``build_ref_cache`` / ``find_anchor_dir`` / ``find_mov_path`` from the frozen
``pilot_learned_match_2026-07-09.py`` plus
``chip_displacement.reproject_to_grid`` -- the exact recipe the pilot used,
so the images shown to the judge are pixel-identical to what the matcher
actually saw.

Nothing but this script and the memo lives in the repo. All real outputs
(overlays, per-row CSVs, summary) go under
``~/zasolar_data/geid_temporal/gt_readjudication_2026-07-09/``.
"""
from __future__ import annotations

import argparse
import base64
import csv
import importlib.util
import io
import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
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
RESULTS_CSV = Path(
    "~/zasolar_data/geid_temporal/pilot_learned_match_2026-07-09/full137/pilot_results.csv"
).expanduser()
OUT_DIR = Path("~/zasolar_data/geid_temporal/gt_readjudication_2026-07-09").expanduser()
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

FAKE_OFFSET_M = 13.0  # inside the pilot's own observed error_m p90 range (14.573m)

# The 10 rows the human peeked at (disclosed, pre-protocol) -- gate 6 checks
# Gemini's majority verdict on exactly these against the human's aggregate read.
PEEKED_ROWS = {
    ("c0014433", "2015-07-30"),
    ("c0011139", "2022-10-30"),
    ("c0005840", "2018-12-30"),
    ("c0006653", "2018-12-30"),
    ("c0005937", "2022-10-30"),
    ("c0004809", "2021-10-30"),
    ("c0005924", "2022-04-30"),
    ("c0002980", "2018-12-30"),
    ("c0005409", "2016-11-30"),
    ("c0004084", "2021-10-30"),
}

PROMPT_TEMPLATE = """You are comparing two candidate image alignments to determine which one correctly registers two aerial/satellite photos of the same rooftop location, taken at different times.

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


# --------------------------------------------------------------------------- #
# Overlay rendering (recipe validated by the pre-verdict synthetic sign check) #
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
    dx_px = dx_m / GSD_M
    dy_px = dy_m / GSD_M
    aligned = nd_shift(mov_gray, shift=(-dy_px, -dx_px), order=1, mode="constant", cval=np.nan)
    return np.nan_to_num(aligned, nan=float(np.nanmean(mov_gray)))


def render_item_images(tag: str, ref_gray: np.ndarray, mov_gray: np.ndarray, slot_a: tuple[float, float], slot_b: tuple[float, float]) -> dict[str, Path]:
    """Writes unshifted/A/B JPEGs for one item, returns {label: path}."""
    IMG_DIR.mkdir(parents=True, exist_ok=True)
    paths = {}
    unshifted = render_overlay(ref_gray, mov_gray)
    p = IMG_DIR / f"{tag}_unshifted.jpg"
    p.write_bytes(_to_jpeg_bytes(unshifted))
    paths["unshifted"] = p

    for label, (dx_m, dy_m) in (("A", slot_a), ("B", slot_b)):
        aligned = align_mov(mov_gray, dx_m, dy_m)
        img = render_overlay(ref_gray, aligned)
        p = IMG_DIR / f"{tag}_{label}.jpg"
        p.write_bytes(_to_jpeg_bytes(img))
        paths[label] = p
    return paths


# --------------------------------------------------------------------------- #
# Self-check                                                                   #
# --------------------------------------------------------------------------- #

def run_self_check() -> bool:
    """Synthetic round-trip: mov = np.roll(ref, (dy,dx)); align_mov must recover
    ref (MAE over the region unaffected by roll wraparound)."""
    targets = load_targets(CHIP_TARGETS)
    ref_gray = None
    chosen = None
    for cid, tgt in targets.items():
        anchor_dir = pilot.find_anchor_dir(cid)
        if anchor_dir is None:
            continue
        rc = pilot.build_ref_cache(cid, tgt)
        if rc is not None:
            ref_gray = rc.ref_gray
            chosen = cid
            break
    if ref_gray is None:
        print("SELF-CHECK FAIL: no usable ref image found on disk")
        return False

    dy_px, dx_px = 7, -4
    mov = np.roll(ref_gray, shift=(dy_px, dx_px), axis=(0, 1))
    dx_m, dy_m = dx_px * GSD_M, dy_px * GSD_M
    aligned = align_mov(mov, dx_m, dy_m)

    # Crop away the wraparound border introduced by np.roll before comparing.
    pad = max(abs(dy_px), abs(dx_px)) + 2
    a = aligned[pad:-pad, pad:-pad]
    b = ref_gray[pad:-pad, pad:-pad]
    mae = float(np.mean(np.abs(a - b)))
    passed = mae < 1e-3
    print(f"SELF-CHECK (ref={chosen}): shift=(dy={dy_px},dx={dx_px}) MAE={mae:.6f} tol=1e-3 -> {'PASS' if passed else 'FAIL'}")
    return passed


# --------------------------------------------------------------------------- #
# Gemini call wrapper                                                          #
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
        print(f"SMOKE TEST response: {text[:200]!r}")
        return bool(text.strip())
    except Exception as exc:  # noqa: BLE001
        print(f"SMOKE TEST FAILED: {type(exc).__name__}: {exc}")
        return False


def judge_item(cfg: GatewayConfig, image_paths: list[Path], limiter: RateLimiter) -> dict[str, Any]:
    """One item, up to MAX_ATTEMPTS. Returns dict with raw_verdict/confidence/
    reasoning/retries; raw_verdict='abstain' if all attempts fail (fail-closed)."""
    last_err = None
    for attempt in range(MAX_ATTEMPTS):
        limiter.wait()
        try:
            resp = post_native_generate_content(
                base_url=cfg.base_url,
                native_path=cfg.native_path,
                api_key=cfg.api_key,
                model=cfg.model,
                prompt=PROMPT_TEMPLATE,
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

    return {
        "raw_verdict": "abstain",
        "confidence": None,
        "reasoning": "",
        "retries": MAX_ATTEMPTS - 1,
        "error": last_err,
    }


# --------------------------------------------------------------------------- #
# Data loading + item construction                                            #
# --------------------------------------------------------------------------- #

def load_pilot_rows() -> tuple[list[dict], list[dict]]:
    with RESULTS_CSV.open() as f:
        rows = list(csv.DictReader(f))
    fails = [r for r in rows if r["recovered"] == "False"]
    trues = [r for r in rows if r["recovered"] == "True"]
    return fails, trues


def build_ref_mov(chip_id: str, capture_date: str, targets: dict) -> tuple[np.ndarray, np.ndarray] | None:
    tgt = targets.get(chip_id)
    anchor_dir = pilot.find_anchor_dir(chip_id)
    if tgt is None or anchor_dir is None:
        return None
    rc = pilot.build_ref_cache(chip_id, tgt)
    if rc is None:
        return None
    mov_path = pilot.find_mov_path(anchor_dir, capture_date, "0")
    if mov_path is None:
        return None
    try:
        mov_gray = cd.reproject_to_grid(mov_path, dst_crs=rc.metric_crs, dst_transform=rc.transform, dst_shape=rc.shape)
    except Exception:
        return None
    return rc.ref_gray, mov_gray


# --------------------------------------------------------------------------- #
# Main                                                                         #
# --------------------------------------------------------------------------- #

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--self-check", action="store_true")
    ap.add_argument("--qps", type=float, default=DEFAULT_QPS)
    ap.add_argument("--skip-smoke-test", action="store_true", help="debug only")
    args = ap.parse_args()

    if args.self_check:
        ok = run_self_check()
        raise SystemExit(0 if ok else 1)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    cfg = load_gateway_config()

    if not args.skip_smoke_test:
        print("running gateway smoke test...")
        if not smoke_test(cfg):
            print("ABORT: gateway smoke test failed. Not proceeding.")
            raise SystemExit(1)

    fails, trues = load_pilot_rows()
    print(f"loaded {len(fails)} disagreement rows, {len(trues)} agreement rows")
    targets = load_targets(CHIP_TARGETS)
    limiter = RateLimiter(args.qps)

    # --- deterministic seed=0 streams, kept separate and documented --- #
    rng_select = np.random.default_rng(0)     # which 10 of the 94 agreement rows
    rng_direction = np.random.default_rng(0)  # fake-offset direction per synthetic item
    rng_assign_synth = np.random.default_rng(0)  # A/B assignment, synthetic items
    rng_assign_real = np.random.default_rng(0)   # A/B assignment, real 43 items

    # --- build + render the 10 synthetic competence-gate controls --- #
    synth_idx = rng_select.choice(len(trues), size=10, replace=False)
    synth_rows = [trues[i] for i in synth_idx]
    synth_items = []
    for row in synth_rows:
        chip_id, capture_date = row["chip_id"], row["capture_date"]
        ref_mov = build_ref_mov(chip_id, capture_date, targets)
        if ref_mov is None:
            print(f"SKIP synthetic {chip_id}/{capture_date}: could not build ref/mov")
            continue
        ref_gray, mov_gray = ref_mov
        correct_dx, correct_dy = float(row["known_dx_m"]), float(row["known_dy_m"])
        theta = rng_direction.uniform(0, 2 * np.pi)
        fake_dx = correct_dx + FAKE_OFFSET_M * np.cos(theta)
        fake_dy = correct_dy + FAKE_OFFSET_M * np.sin(theta)
        correct_is_a = bool(rng_assign_synth.integers(0, 2))
        slot_a = (correct_dx, correct_dy) if correct_is_a else (fake_dx, fake_dy)
        slot_b = (fake_dx, fake_dy) if correct_is_a else (correct_dx, correct_dy)
        tag = f"synth_{chip_id}_{capture_date}"
        paths = render_item_images(tag, ref_gray, mov_gray, slot_a, slot_b)
        synth_items.append({
            "chip_id": chip_id, "capture_date": capture_date,
            "correct_dx_m": correct_dx, "correct_dy_m": correct_dy,
            "fake_dx_m": fake_dx, "fake_dy_m": fake_dy, "fake_direction_deg": np.degrees(theta),
            "correct_slot": "A" if correct_is_a else "B",
            "paths": paths,
        })
    print(f"rendered {len(synth_items)}/10 synthetic controls")

    # --- competence gate --- #
    print("running competence gate (10 synthetic items)...")
    synth_results = []
    n_correct = 0
    for item in synth_items:
        image_paths = [item["paths"]["unshifted"], item["paths"]["A"], item["paths"]["B"]]
        r = judge_item(cfg, image_paths, limiter)
        unblinded = (
            "correct" if r["raw_verdict"] == item["correct_slot"]
            else ("fake" if r["raw_verdict"] in ("A", "B") else r["raw_verdict"])
        )
        is_correct = unblinded == "correct"
        n_correct += int(is_correct)
        synth_results.append({**item, **r, "unblinded_verdict": unblinded, "is_correct": is_correct})
        print(f"  {item['chip_id']}/{item['capture_date']}: raw={r['raw_verdict']} unblinded={unblinded} correct={is_correct}")

    competence_pass = n_correct >= 8
    print(f"COMPETENCE GATE: {n_correct}/10 -> {'PASS' if competence_pass else 'ABORT'}")

    _write_synth_csv(OUT_DIR / "competence_gate_results.csv", synth_results)

    if not competence_pass:
        _write_abort_summary(n_correct)
        print("ABORTING: competence gate failed. Not running the 43 real items.")
        raise SystemExit(1)

    # --- build + render the 43 real disagreement items --- #
    real_items = []
    for row in fails:
        chip_id, capture_date = row["chip_id"], row["capture_date"]
        ref_mov = build_ref_mov(chip_id, capture_date, targets)
        if ref_mov is None:
            print(f"SKIP real {chip_id}/{capture_date}: could not build ref/mov")
            continue
        ref_gray, mov_gray = ref_mov
        known_dx, known_dy = float(row["known_dx_m"]), float(row["known_dy_m"])
        est_dx, est_dy = float(row["est_dx_m"]), float(row["est_dy_m"])
        est_is_a = bool(rng_assign_real.integers(0, 2))
        slot_a = (est_dx, est_dy) if est_is_a else (known_dx, known_dy)
        slot_b = (known_dx, known_dy) if est_is_a else (est_dx, est_dy)
        tag = f"real_{chip_id}_{capture_date}"
        paths = render_item_images(tag, ref_gray, mov_gray, slot_a, slot_b)
        real_items.append({
            "chip_id": chip_id, "capture_date": capture_date, "area_bucket": row.get("area_bucket", ""),
            "known_dx_m": known_dx, "known_dy_m": known_dy,
            "est_dx_m": est_dx, "est_dy_m": est_dy, "error_m": row.get("error_m", ""),
            "est_slot": "A" if est_is_a else "B",
            "is_peeked_row": (chip_id.split("_")[-1], capture_date) in PEEKED_ROWS,
            "paths": paths,
        })
    print(f"rendered {len(real_items)}/{len(fails)} real disagreement items")

    print("running 43-row real adjudication...")
    real_results = []
    for item in real_items:
        image_paths = [item["paths"]["unshifted"], item["paths"]["A"], item["paths"]["B"]]
        r = judge_item(cfg, image_paths, limiter)
        if r["raw_verdict"] == "abstain":
            unblinded = "abstain"
        elif r["raw_verdict"] == "neither":
            unblinded = "neither"
        elif r["raw_verdict"] == item["est_slot"]:
            unblinded = "est"
        else:
            unblinded = "known"
        recovered = unblinded == "est"
        real_results.append({**item, **r, "unblinded_verdict": unblinded, "recovered": recovered})
        print(f"  {item['chip_id']}/{item['capture_date']}: raw={r['raw_verdict']} unblinded={unblinded} recovered={recovered}")

    _write_real_csv(OUT_DIR / "readjudication_43_results.csv", real_results)

    # --- human-consistency gate --- #
    peeked_results = [r for r in real_results if r["is_peeked_row"]]
    n_peeked_est = sum(1 for r in peeked_results if r["unblinded_verdict"] == "est")
    n_peeked = len(peeked_results)
    human_gate_pass = n_peeked_est > n_peeked / 2 if n_peeked else False
    print(f"HUMAN-CONSISTENCY GATE: {n_peeked_est}/{n_peeked} peeked rows judged est-correct -> {'PASS' if human_gate_pass else 'CONTRADICTED'}")

    # --- category counts + recompute --- #
    n_est = sum(1 for r in real_results if r["unblinded_verdict"] == "est")
    n_known = sum(1 for r in real_results if r["unblinded_verdict"] == "known")
    n_neither = sum(1 for r in real_results if r["unblinded_verdict"] == "neither")
    n_abstain = sum(1 for r in real_results if r["unblinded_verdict"] == "abstain")

    corrected_recovered = len(trues) + n_est
    go_primary = corrected_recovered >= 96

    denom_secondary = len(real_results) - n_neither
    secondary_recovered = len(trues) + n_est
    secondary_rate = secondary_recovered / (len(trues) + denom_secondary) if (len(trues) + denom_secondary) else float("nan")

    _write_summary(
        n_correct=n_correct, competence_pass=competence_pass,
        n_peeked=n_peeked, n_peeked_est=n_peeked_est, human_gate_pass=human_gate_pass,
        n_est=n_est, n_known=n_known, n_neither=n_neither, n_abstain=n_abstain,
        n_real=len(real_results), n_trues=len(trues),
        corrected_recovered=corrected_recovered, go_primary=go_primary,
        secondary_rate=secondary_rate, denom_secondary=denom_secondary,
        real_results=real_results, human_gate_applied=human_gate_pass,
    )

    if not human_gate_pass:
        print("STOPPING before recompute application: human-consistency gate contradicted. See summary.")
        raise SystemExit(2)

    print(f"RECOMPUTE: {corrected_recovered}/137 -> {'GO' if go_primary else 'KILL stands'}")


def _write_synth_csv(path: Path, results: list[dict]) -> None:
    fieldnames = [
        "chip_id", "capture_date", "correct_dx_m", "correct_dy_m", "fake_dx_m", "fake_dy_m",
        "fake_direction_deg", "correct_slot", "raw_verdict", "unblinded_verdict", "is_correct",
        "confidence", "reasoning", "retries", "error",
    ]
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        w.writerows(results)


def _write_real_csv(path: Path, results: list[dict]) -> None:
    fieldnames = [
        "chip_id", "capture_date", "area_bucket", "known_dx_m", "known_dy_m", "est_dx_m", "est_dy_m",
        "error_m", "est_slot", "is_peeked_row", "raw_verdict", "unblinded_verdict", "recovered",
        "confidence", "reasoning", "retries", "error",
    ]
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        w.writerows(results)


def _write_abort_summary(n_correct: int) -> None:
    lines = [
        "ISSUE-24 GT re-adjudication -- ABORTED at competence gate (2026-07-09)",
        f"competence gate score: {n_correct}/10 (need >=8/10 to proceed)",
        "43 real disagreement rows were NOT run.",
        "Recommendation: fall back to full human adjudication (out of scope for this script).",
    ]
    (OUT_DIR / "summary.txt").write_text("\n".join(lines) + "\n")


def _write_summary(
    *, n_correct, competence_pass, n_peeked, n_peeked_est, human_gate_pass,
    n_est, n_known, n_neither, n_abstain, n_real, n_trues,
    corrected_recovered, go_primary, secondary_rate, denom_secondary,
    real_results, human_gate_applied,
) -> None:
    lines = []
    lines.append("ISSUE-24 GT re-adjudication -- results (2026-07-09)")
    lines.append("")
    lines.append(f"[Competence gate] {n_correct}/10 -> {'PASS' if competence_pass else 'ABORT'}")
    lines.append(f"[Human-consistency gate] {n_peeked_est}/{n_peeked} peeked rows judged est-correct -> {'PASS' if human_gate_pass else 'CONTRADICTED'}")
    lines.append("")
    lines.append(f"[43-row category counts] est={n_est} known={n_known} neither={n_neither} abstain={n_abstain} (n={n_real})")
    lines.append("")
    lines.append(f"[PRIMARY recompute] corrected recovered = {n_trues} (agreement) + {n_est} (est-correct) = {corrected_recovered}/137")
    lines.append(f"  GO bar: >=96/137 (70%)  ->  {'GO' if go_primary else 'KILL stands'}")
    if not human_gate_applied:
        lines.append("  NOTE: human-consistency gate CONTRADICTED -- recompute NOT applied as a ruling; escalated instead.")
    lines.append("")
    lines.append(f"[SECONDARY, report-only] dropping 'neither' from denominator: {n_trues + n_est}/{n_trues + denom_secondary} = {secondary_rate:.1%}")
    lines.append("")
    confs = [r["confidence"] for r in real_results if r["confidence"] is not None]
    if confs:
        lines.append(f"[confidence distribution, 43 rows] p25={np.percentile(confs,25):.3f} p50={np.percentile(confs,50):.3f} p75={np.percentile(confs,75):.3f}")
    lines.append("")
    (OUT_DIR / "summary.txt").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
