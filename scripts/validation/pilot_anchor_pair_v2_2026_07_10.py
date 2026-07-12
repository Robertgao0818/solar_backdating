#!/usr/bin/env python3
"""Anchor-pair v2 pilot — Vexcel present template (pre-reg 2026-07-10).

Implements ``docs/dinov3_scorer/DATA-anchor-pair-v2-prereg-2026-07-10.md``:

* H3 domain-gap smoke (fail-closed before head train)
* Arm V: GEHI cand × Vexcel census present (bet)
* Arm N/E: nearest / earliest teacher-absent ablations
* Arm B: parameter-matched single-frame control
* Patch-token grids (pool last); rate-normalized multi-seed R2

Zero API, zero new GT, frozen backbone, local GPU.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.temporal.train_dinov3_head import (  # noqa: E402
    CALIB_SALT,
    CLASS_INDEX,
    CLASS_ORDER,
    DEFAULT_COVERAGE_FLOOR,
    DEFAULT_GRID_STEP,
    DEFAULT_LR,
    DEFAULT_MAX_EPOCHS,
    DEFAULT_PATIENCE,
    DEFAULT_WEIGHT_DECAY,
    VAL_SALT,
    _assert_no_heldout_leak,
    _class_weights,
    _macro_f1,
    _split_heldout_halves,
    _threshold_grid,
    _val_anchors,
)
from scripts.validation.check_student_path_gate import (  # noqa: E402
    check_anchor_pair_v2_r1,
)
from scripts.validation.pilot_anchor_pair_2026_07_10 import (  # noqa: E402
    area_bucket,
    load_area_map,
    report_half_mask,
    transition_band_dates,
)

FEATURES_DEFAULT = Path.home() / (
    "zasolar_data/slice5/out/nomarker_bilinear518_k6/features.npz"
)
VEXCEL_CROPS_DEFAULT = Path.home() / (
    "zasolar_data/geid_temporal/gehi_displacement_audit_2026-07-06/vexcel_crops"
)
CHIPGROUPS_DIR = Path.home() / (
    "zasolar_data/geid_temporal/"
    "jhb_full382_unified_A_merge01_c0925_fpcut_2026-06-01_chipgroups"
)
OUT_DEFAULT = Path.home() / "zasolar_data/geid_temporal/pilot_anchor_pair_v2_20260710"

BACKBONE = "vit_small_patch14_dinov2.lvd142m"
INPUT_SIZE = 518
CENTER_POOL_K = 6  # scorer default for floor; patch grids ignore pooling
SEEDS = (0, 1, 2)
BATCH_EXTRACT = 8
BATCH_TRAIN = 32
MAX_EPOCHS = DEFAULT_MAX_EPOCHS
PATIENCE = DEFAULT_PATIENCE
LR = DEFAULT_LR
WD = DEFAULT_WEIGHT_DECAY
HEAD_WIDTH_V = 128  # first successful config lock
N_BOOT = 2000
# Full 37×37×384×3×N float32 exceeds RAM; keep centre spatial window (still
# patch-token structure / pool-last — not global-pool-then-diff).
GRID_KEEP = 15


# --------------------------------------------------------------------------- #
# Metadata / eligibility
# --------------------------------------------------------------------------- #
def resolve_vexcel_paths(
    anchors: Sequence[str],
    crops_dir: Path,
    chipgroups_dir: Path = CHIPGROUPS_DIR,
) -> dict[str, str]:
    """Map anchor_id → banked Vexcel crop path (direct or via chip-group)."""
    crop_by_stem = {p.stem: str(p) for p in crops_dir.glob("*.tif")}
    t_to_c: dict[str, str] = {}
    cg = chipgroups_dir / "chip_groups_as_anchors.csv"
    if cg.exists():
        with cg.open(newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                gid = str(row.get("anchor_id", ""))
                for t in str(row.get("target_anchor_ids", "")).split(";"):
                    t = t.strip()
                    if t:
                        t_to_c[t] = gid
    out: dict[str, str] = {}
    for a in anchors:
        if a in crop_by_stem:
            out[a] = crop_by_stem[a]
            continue
        g = t_to_c.get(a)
        if g and g in crop_by_stem:
            out[a] = crop_by_stem[g]
    return out


def vexcel_tifs_to_pngs(
    vexcel_paths: Mapping[str, str],
    png_dir: Path,
) -> dict[str, str]:
    """Convert GeoTIFFs to RGB PNGs (PIL libtiff XMP bug on some Vexcel tifs).

    Idempotent: skips existing PNGs. Returns anchor_id → png path.
    """
    import rasterio
    from PIL import Image

    png_dir = Path(png_dir)
    png_dir.mkdir(parents=True, exist_ok=True)
    out: dict[str, str] = {}
    n_new = 0
    for a, tif in vexcel_paths.items():
        dest = png_dir / f"{a}.png"
        if not dest.exists():
            with rasterio.open(tif) as src:
                arr = src.read()
            if arr.shape[0] >= 3:
                rgb = np.transpose(arr[:3], (1, 2, 0))
            else:
                rgb = np.stack([arr[0]] * 3, axis=-1)
            if rgb.dtype != np.uint8:
                if float(rgb.max()) <= 1.5:
                    rgb = (np.clip(rgb, 0, 1) * 255).astype(np.uint8)
                else:
                    rgb = np.clip(rgb, 0, 255).astype(np.uint8)
            Image.fromarray(rgb).save(dest)
            n_new += 1
        out[a] = str(dest)
    print(f"[vexcel-png] {len(out)} paths ({n_new} newly converted) → {png_dir}", flush=True)
    return out


def build_row_table(npz: Mapping[str, np.ndarray]) -> list[dict[str, Any]]:
    n = len(npz["anchor_id"])
    rows = []
    for i in range(n):
        rows.append(
            {
                "i": i,
                "anchor_id": str(npz["anchor_id"][i]),
                "capture_date": str(npz["capture_date"][i]),
                "label": str(npz["label_3class"][i]),
                "split": str(npz["split"][i]),
                "png_path": str(npz["png_path"][i]),
                "terminal_status": str(npz["terminal_status"][i]),
            }
        )
    return rows


def eligibility_masks(
    rows: Sequence[Mapping[str, Any]],
    vexcel_paths: Mapping[str, str],
) -> tuple[dict[str, Any], set[str], set[str]]:
    by_a: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for r in rows:
        by_a[r["anchor_id"]].append(r)

    pair_ok: set[str] = set()
    for a, rs in by_a.items():
        labs = {r["label"] for r in rs}
        if "absent" in labs and "present" in labs:
            pair_ok.add(a)
    vex_ok = {a for a in pair_ok if a in vexcel_paths}

    meta = {
        "n_anchors": len(by_a),
        "n_pair_eligible": len(pair_ok),
        "n_vexcel_eligible": len(vex_ok),
        "pair_share": len(pair_ok) / max(1, len(by_a)),
        "vexcel_share_of_pair": len(vex_ok) / max(1, len(pair_ok)),
        "n_rows_pair": sum(1 for r in rows if r["anchor_id"] in pair_ok),
        "n_rows_vexcel": sum(1 for r in rows if r["anchor_id"] in vex_ok),
    }
    return meta, pair_ok, vex_ok


def ref_dates_for_anchor(
    rows_a: Sequence[Mapping[str, Any]],
) -> dict[str, str | None]:
    """earliest absent, nearest-absent helper keys, late present."""
    absents = sorted(r["capture_date"] for r in rows_a if r["label"] == "absent")
    presents = sorted(r["capture_date"] for r in rows_a if r["label"] == "present")
    return {
        "earliest_absent": absents[0] if absents else None,
        "latest_absent": absents[-1] if absents else None,
        "late_present": presents[-1] if presents else None,
        "first_present": presents[0] if presents else None,
    }


def nearest_absent_before(cand_date: str, absent_dates: Sequence[str]) -> str | None:
    before = [d for d in absent_dates if d < cand_date]
    if before:
        return max(before)
    # fallback: nearest overall absent (not self)
    others = [d for d in absent_dates if d != cand_date]
    if not others:
        return None
    return min(others, key=lambda d: abs(_date_ord(d) - _date_ord(cand_date)))


def _date_ord(d: str) -> int:
    digits = "".join(c for c in d if c.isdigit())
    return int(digits[:8]) if len(digits) >= 8 else 0


# --------------------------------------------------------------------------- #
# Patch cache
# --------------------------------------------------------------------------- #
def extract_patch_cache(
    paths: Sequence[str],
    out_path: Path,
    *,
    device: str,
    batch_size: int,
    meta_path: Path | None = None,
) -> dict[str, Any]:
    from scripts.temporal.dinov3_scorer import Dinov2PresenceScorer

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    scorer = Dinov2PresenceScorer(
        backbone_model_id=BACKBONE,
        input_size=INPUT_SIZE,
        center_pool_k=CENTER_POOL_K,
        upscale_policy="bilinear",
        device=device,
    )
    print(f"[extract] {len(paths)} chips → {out_path}", flush=True)
    t0 = time.perf_counter()
    grids = scorer.embed_patch_grids(paths, batch_size=batch_size)
    elapsed = time.perf_counter() - t0
    np.save(out_path, grids)
    meta = {
        "n": int(grids.shape[0]),
        "shape": list(grids.shape),
        "dtype": str(grids.dtype),
        "backbone": BACKBONE,
        "input_size": INPUT_SIZE,
        "seconds": elapsed,
        "chips_per_s": (len(paths) / elapsed) if elapsed > 0 else None,
    }
    if meta_path:
        meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(f"[extract] done shape={grids.shape} {elapsed:.1f}s", flush=True)
    return meta


def load_grids(path: Path) -> np.ndarray:
    return np.load(path, mmap_mode="r")


# --------------------------------------------------------------------------- #
# Domain-gap smoke (H3)
# --------------------------------------------------------------------------- #
def global_pool(grid: np.ndarray) -> np.ndarray:
    """Mean-pool spatial dims → [C] float32 L2-normalized."""
    v = np.asarray(grid, dtype=np.float32).mean(axis=(0, 1))
    n = float(np.linalg.norm(v) + 1e-8)
    return v / n


def run_domain_gap_smoke(
    *,
    rows: Sequence[Mapping[str, Any]],
    vex_ok: set[str],
    vexcel_paths: Mapping[str, str],
    cand_grids: np.ndarray,
    vex_index: Mapping[str, int],
    vex_grids: np.ndarray,
    out_path: Path,
    rng_seed: int = 0,
) -> dict[str, Any]:
    """H3: median cos(late_present,V) > cos(early_absent,V) and > cos(other_V, V)."""
    by_a: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for r in rows:
        if r["anchor_id"] in vex_ok:
            by_a[r["anchor_id"]].append(r)

    # prefer report half for smoke evaluation universe
    anchors = sorted(by_a)
    held = [a for a in anchors if any(r["split"] == "heldout" for r in by_a[a])]
    _, report = _split_heldout_halves(held) if held else (set(), set())
    smoke_anchors = sorted(report) if report else anchors

    s_pos: list[float] = []
    s_abs: list[float] = []
    s_neg: list[float] = []
    rng = np.random.default_rng(rng_seed)
    other_ids = [a for a in smoke_anchors if a in vex_index]

    for a in smoke_anchors:
        if a not in vex_index:
            continue
        refs = ref_dates_for_anchor(by_a[a])
        if not refs["late_present"] or not refs["earliest_absent"]:
            continue
        # row indices
        late_i = next(
            (
                r["i"]
                for r in by_a[a]
                if r["capture_date"] == refs["late_present"] and r["label"] == "present"
            ),
            None,
        )
        early_i = next(
            (
                r["i"]
                for r in by_a[a]
                if r["capture_date"] == refs["earliest_absent"] and r["label"] == "absent"
            ),
            None,
        )
        if late_i is None or early_i is None:
            continue
        pv = global_pool(vex_grids[vex_index[a]])
        s_pos.append(float(np.dot(global_pool(cand_grids[late_i]), pv)))
        s_abs.append(float(np.dot(global_pool(cand_grids[early_i]), pv)))
        # random other vexcel
        others = [o for o in other_ids if o != a]
        if others:
            o = others[int(rng.integers(0, len(others)))]
            s_neg.append(float(np.dot(global_pool(vex_grids[vex_index[o]]), pv)))

    def med(xs: list[float]) -> float | None:
        return float(np.median(xs)) if xs else None

    m_pos, m_abs, m_neg = med(s_pos), med(s_abs), med(s_neg)
    go = (
        m_pos is not None
        and m_abs is not None
        and m_neg is not None
        and m_pos > m_abs
        and m_pos > m_neg
    )
    result = {
        "verdict": "SMOKE_GO" if go else "SMOKE_KILL",
        "n_targets": len(s_pos),
        "median_cos_late_present_vexcel": m_pos,
        "median_cos_early_absent_vexcel": m_abs,
        "median_cos_other_vexcel_vexcel": m_neg,
        "mean_cos_late_present_vexcel": float(np.mean(s_pos)) if s_pos else None,
        "mean_cos_early_absent_vexcel": float(np.mean(s_abs)) if s_abs else None,
        "mean_cos_other_vexcel_vexcel": float(np.mean(s_neg)) if s_neg else None,
        "rule": "median s_pos > s_abs AND median s_pos > s_neg",
    }
    out_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2), flush=True)
    return result


# --------------------------------------------------------------------------- #
# Head + train + metrics
# --------------------------------------------------------------------------- #
def _build_head(in_ch: int, width: int, n_classes: int = 3):
    import torch
    from torch import nn

    class PatchHead(nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.stem = nn.Sequential(
                nn.Conv2d(in_ch, width, kernel_size=1),
                nn.ReLU(inplace=True),
                nn.Conv2d(width, width, kernel_size=3, padding=1),
                nn.ReLU(inplace=True),
                nn.Conv2d(width, width, kernel_size=3, padding=1),
                nn.ReLU(inplace=True),
            )
            self.pool = nn.AdaptiveAvgPool2d(1)
            self.fc = nn.Linear(width, n_classes)

        def forward(self, x: Any) -> Any:
            h = self.stem(x)
            h = self.pool(h).flatten(1)
            return self.fc(h)

    return PatchHead()


def count_params(model: Any) -> int:
    return int(sum(p.numel() for p in model.parameters()))


def width_for_param_match(target_params: int, in_ch: int, tol: float = 0.10) -> int:
    """Pick conv width so single-frame head matches target_params within tol."""
    best_w, best_err = 128, float("inf")
    for w in range(64, 513, 8):
        m = _build_head(in_ch, w)
        n = count_params(m)
        err = abs(n - target_params) / max(1, target_params)
        if err < best_err:
            best_err, best_w = err, w
        if err <= tol:
            return w
    return best_w


def _center_crop_hwc(grid: np.ndarray, keep: int = GRID_KEEP) -> np.ndarray:
    """HWC float16/32 → centre keep×keep HWC float32."""
    g = np.asarray(grid)
    h, w, c = g.shape
    k = min(keep, h, w)
    y0 = (h - k) // 2
    x0 = (w - k) // 2
    return np.asarray(g[y0 : y0 + k, x0 : x0 + k, :], dtype=np.float32)


def _to_nchw(grid: np.ndarray) -> np.ndarray:
    """HWC → CHW float32, centre-cropped."""
    g = _center_crop_hwc(grid)
    return np.transpose(g, (2, 0, 1))


def pack_pair(cand: np.ndarray, ref: np.ndarray) -> np.ndarray:
    c = _to_nchw(cand)
    r = _to_nchw(ref)
    return np.concatenate([c, r, c - r], axis=0)


def pack_single(cand: np.ndarray) -> np.ndarray:
    return _to_nchw(cand)


def train_patch_head(
    x: np.ndarray,
    y: np.ndarray,
    anchors: np.ndarray,
    split: np.ndarray,
    *,
    pair: bool,
    width: int,
    seed: int,
    out_path: Path,
    embed_dim: int,
    device: str = "cpu",
) -> dict[str, Any]:
    import torch
    from torch import nn

    in_ch = embed_dim * 3 if pair else embed_dim
    is_train = split == "train"
    _assert_no_heldout_leak(split, is_train)
    train_anchors = sorted(set(anchors[is_train]))
    val_set = set(_val_anchors(train_anchors))
    in_val = np.array([a in val_set for a in anchors])
    is_val = is_train & in_val
    is_fit = is_train & ~in_val
    if not is_fit.any():
        raise SystemExit("empty fit set")

    y_idx = np.array([CLASS_INDEX[str(l)] for l in y], dtype=np.int64)
    weights, counts, warnings = _class_weights(list(y_idx[is_fit]))

    if device == "cuda" and not torch.cuda.is_available():
        device = "cpu"
    dev = torch.device(device)

    torch.manual_seed(int(seed))
    if device == "cuda":
        torch.cuda.manual_seed_all(int(seed))
    model = _build_head(in_ch, width).to(dev)
    n_params = count_params(model)
    if n_params > 5_000_000:
        raise SystemExit(f"head params {n_params} > 5M")

    opt = torch.optim.AdamW(model.parameters(), lr=LR, weight_decay=WD)
    loss_fn = nn.CrossEntropyLoss(
        weight=torch.tensor(weights, dtype=torch.float32, device=dev)
    )
    gen = torch.Generator().manual_seed(int(seed))

    # Keep features in numpy (float16); cast per-batch to float32 — avoids 2× RAM.
    fit_idx = np.where(is_fit)[0]
    val_idx = np.where(is_val)[0]
    y_fit_t = torch.tensor(y_idx[is_fit], dtype=torch.long)
    y_val_np = y_idx[is_val]

    def _batch_x(indices: np.ndarray) -> Any:
        return torch.tensor(
            np.asarray(x[indices], dtype=np.float32), device=dev
        )

    best_f1, best_state, no_imp = -1.0, None, 0
    epochs_run = 0
    n_fit = len(fit_idx)
    for epoch in range(MAX_EPOCHS):
        model.train()
        perm = torch.randperm(n_fit, generator=gen).numpy()
        for s in range(0, n_fit, BATCH_TRAIN):
            bi = fit_idx[perm[s : s + BATCH_TRAIN]]
            yi = y_fit_t[perm[s : s + BATCH_TRAIN]].to(dev)
            opt.zero_grad()
            loss = loss_fn(model(_batch_x(bi)), yi)
            loss.backward()
            opt.step()
        model.eval()
        with torch.no_grad():
            if len(val_idx) > 0:
                preds = []
                for s in range(0, len(val_idx), BATCH_TRAIN):
                    preds.append(
                        model(_batch_x(val_idx[s : s + BATCH_TRAIN]))
                        .argmax(1)
                        .cpu()
                        .numpy()
                    )
                pred = np.concatenate(preds) if preds else np.array([], dtype=np.int64)
                f1 = _macro_f1(y_val_np, pred)
            else:
                preds = []
                for s in range(0, n_fit, BATCH_TRAIN):
                    preds.append(
                        model(_batch_x(fit_idx[s : s + BATCH_TRAIN]))
                        .argmax(1)
                        .cpu()
                        .numpy()
                    )
                pred = np.concatenate(preds)
                f1 = _macro_f1(y_idx[is_fit], pred)
        epochs_run = epoch + 1
        if f1 > best_f1 + 1e-9:
            best_f1 = f1
            best_state = {
                k: v.detach().cpu().clone() for k, v in model.state_dict().items()
            }
            no_imp = 0
        else:
            no_imp += 1
            if no_imp >= PATIENCE:
                break
    if best_state is None:
        best_state = {
            k: v.detach().cpu().clone() for k, v in model.state_dict().items()
        }

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    torch.save(
        {
            "schema_version": 1,
            "head_arch": "patch_conv",
            "pair": pair,
            "width": width,
            "in_ch": in_ch,
            "embed_dim": embed_dim,
            "state_dict": best_state,
            "class_order": list(CLASS_ORDER),
            "device_trained": device,
        },
        out_path,
    )
    log = {
        "seed": seed,
        "pair": pair,
        "width": width,
        "n_params": n_params,
        "epochs_run": epochs_run,
        "best_val_macro_f1": best_f1,
        "n_fit": int(is_fit.sum()),
        "n_val": int(is_val.sum()),
        "class_counts": {CLASS_ORDER[c]: counts[c] for c in range(3)},
        "warnings": warnings,
        "device": device,
    }
    out_path.with_suffix(".train_log.json").write_text(
        json.dumps(log, indent=2), encoding="utf-8"
    )
    return log


def load_head(path: Path) -> Any:
    import torch

    obj = torch.load(path, map_location="cpu", weights_only=False)
    model = _build_head(int(obj["in_ch"]), int(obj["width"]))
    model.load_state_dict(obj["state_dict"])
    model.eval()
    return model


def predict_probs(
    model: Any,
    x: np.ndarray,
    batch: int = 64,
    device: str = "cpu",
) -> np.ndarray:
    import torch

    if device == "cuda" and not torch.cuda.is_available():
        device = "cpu"
    dev = torch.device(device)
    model = model.to(dev)
    model.eval()
    outs = []
    with torch.no_grad():
        for s in range(0, len(x), batch):
            t = torch.tensor(
                np.asarray(x[s : s + batch], dtype=np.float32), device=dev
            )
            logits = model(t).cpu().numpy()
            logits = logits - logits.max(axis=1, keepdims=True)
            e = np.exp(logits)
            outs.append(e / e.sum(axis=1, keepdims=True))
    return np.concatenate(outs, axis=0) if outs else np.zeros((0, 3), dtype=np.float32)


def calibrate_probs(
    probs: np.ndarray,
    labels: np.ndarray,
    anchors: np.ndarray,
    split: np.ndarray,
    coverage_floor: float = DEFAULT_COVERAGE_FLOOR,
) -> dict[str, Any]:
    labels = np.asarray(labels).astype(str)
    anchors = np.asarray(anchors).astype(str)
    split = np.asarray(split).astype(str)
    held = set(anchors[split == "heldout"])
    calib, _ = _split_heldout_halves(held)
    mask = (split == "heldout") & np.array([a in calib for a in anchors])
    mask &= np.isin(labels, np.array(["present", "absent"]))
    if not mask.any():
        raise SystemExit("no calib rows")
    pp = probs[mask, 0]
    argm = probs[mask].argmax(1)
    teacher = labels[mask]
    total = int(mask.sum())
    not_u = argm != 2
    frontier = []
    for lo in _threshold_grid(DEFAULT_GRID_STEP):
        for hi in _threshold_grid(DEFAULT_GRID_STEP):
            if hi < lo:
                continue
            dec = not_u & ((pp > hi) | (pp < lo))
            n = int(dec.sum())
            cov = n / total
            agr = float((np.where(pp[dec] > hi, "present", "absent") == teacher[dec]).mean()) if n else 0.0
            frontier.append({"lo": lo, "hi": hi, "coverage": cov, "agreement": agr})
    feas = [f for f in frontier if f["coverage"] >= coverage_floor] or frontier
    if not any(f["coverage"] >= coverage_floor for f in frontier):
        mx = max(f["coverage"] for f in frontier)
        feas = [f for f in frontier if f["coverage"] == mx]
    best = min(feas, key=lambda f: (-f["agreement"], -f["coverage"], f["hi"] - f["lo"], f["lo"]))
    return {
        "lo": best["lo"],
        "hi": best["hi"],
        "decided_agreement": best["agreement"],
        "abstain_rate": 1.0 - best["coverage"],
        "n_calib": total,
    }


def decisions_from_probs(probs: np.ndarray, lo: float, hi: float) -> list[str]:
    out = []
    for i in range(len(probs)):
        if int(probs[i].argmax()) == 2:
            out.append("unusable")
        elif float(probs[i, 0]) > hi:
            out.append("present")
        elif float(probs[i, 0]) < lo:
            out.append("absent")
        else:
            out.append("ambiguous")
    return out


def rate_metrics(
    teacher: Sequence[str], student: Sequence[str]
) -> dict[str, Any]:
    t = list(teacher)
    s = list(student)
    n = len(t)
    # TB FP/FN rates over decided PA
    abs_dec = [(tt, ss) for tt, ss in zip(t, s) if tt == "absent" and ss in ("present", "absent")]
    pre_dec = [(tt, ss) for tt, ss in zip(t, s) if tt == "present" and ss in ("present", "absent")]
    fp = sum(1 for tt, ss in abs_dec if ss == "present")
    fn = sum(1 for tt, ss in pre_dec if ss == "absent")
    fp_rate = fp / len(abs_dec) if abs_dec else None
    fn_rate = fn / len(pre_dec) if pre_dec else None
    n_tp = sum(1 for tt in t if tt == "present")
    ptu = sum(1 for tt, ss in zip(t, s) if tt == "present" and ss == "unusable")
    ptu_rate = ptu / n_tp if n_tp else None
    pa_dec = [(tt, ss) for tt, ss in zip(t, s) if ss in ("present", "absent")]
    pa_agree = sum(1 for tt, ss in pa_dec if tt == ss)
    n_tu = sum(1 for tt in t if tt == "unusable")
    ur = sum(1 for tt, ss in zip(t, s) if tt == "unusable" and ss == "unusable")
    return {
        "n": n,
        "fp": fp,
        "fn": fn,
        "n_absent_decided": len(abs_dec),
        "n_present_decided": len(pre_dec),
        "fp_rate": fp_rate,
        "fn_rate": fn_rate,
        "present_to_unusable_rate": ptu_rate,
        "decided_agreement": (pa_agree / len(pa_dec)) if pa_dec else None,
        "coverage": (len(pa_dec) / n) if n else 0.0,
        "unusable_recall": (ur / n_tu) if n_tu else None,
        "n_teacher_unusable": n_tu,
    }


def bootstrap_reduction_ci(
    rate_v: float | None,
    rate_b: float | None,
    n_v_den: int,
    n_b_den: int,
    *,
    n_boot: int = N_BOOT,
    seed: int = 0,
) -> tuple[float | None, float | None]:
    """Relative reduction (b-v)/b with percentile CI via independent binomial draws."""
    if rate_v is None or rate_b is None or rate_b <= 0 or n_v_den <= 0 or n_b_den <= 0:
        return None, None
    point = (rate_b - rate_v) / rate_b
    rng = np.random.default_rng(seed)
    draws = []
    for _ in range(n_boot):
        rv = rng.binomial(n_v_den, min(max(rate_v, 0.0), 1.0)) / n_v_den
        rb = rng.binomial(n_b_den, min(max(rate_b, 0.0), 1.0)) / n_b_den
        if rb <= 0:
            continue
        draws.append((rb - rv) / rb)
    if not draws:
        return point, None
    lo = float(np.percentile(draws, 2.5))
    return point, lo


# --------------------------------------------------------------------------- #
# Dataset builders for arms
# --------------------------------------------------------------------------- #
def build_arm_index(
    rows: Sequence[Mapping[str, Any]],
    *,
    arm: str,
    pair_ok: set[str],
    vex_ok: set[str],
    cand_grids: np.ndarray,
    vex_index: Mapping[str, int],
    vex_grids: np.ndarray,
) -> dict[str, Any]:
    """Index rows for an arm; materialize x with centre crop (RAM-safe)."""
    by_a: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for r in rows:
        by_a[r["anchor_id"]].append(r)

    abs_dates: dict[str, list[str]] = {}
    date_to_i: dict[str, dict[str, int]] = {}
    for a, rs in by_a.items():
        abs_dates[a] = sorted({r["capture_date"] for r in rs if r["label"] == "absent"})
        date_to_i[a] = {}
        for r in rs:
            date_to_i[a].setdefault(r["capture_date"], r["i"])

    labels: list[str] = []
    anchors: list[str] = []
    splits: list[str] = []
    is_tb: list[bool] = []
    packs: list[np.ndarray] = []
    embed_dim = int(cand_grids.shape[-1])

    universe = vex_ok if arm in ("V", "B") else pair_ok

    for r in rows:
        a = r["anchor_id"]
        if a not in universe:
            continue
        cand_i = r["i"]
        cand_date = r["capture_date"]

        if arm == "V":
            if a not in vex_index:
                continue
            x = pack_pair(cand_grids[cand_i], vex_grids[vex_index[a]])
        elif arm == "B":
            x = pack_single(cand_grids[cand_i])
        elif arm in ("N", "E"):
            if arm == "E":
                ref_d = abs_dates[a][0] if abs_dates[a] else None
            else:
                ref_d = nearest_absent_before(cand_date, abs_dates[a])
            if ref_d is None or ref_d == cand_date:
                continue
            ref_i = date_to_i[a].get(ref_d)
            if ref_i is None:
                continue
            x = pack_pair(cand_grids[cand_i], cand_grids[ref_i])
        else:
            raise ValueError(arm)

        dates = [z["capture_date"] for z in by_a[a]]
        labs = [z["label"] for z in by_a[a]]
        packs.append(x)
        labels.append(r["label"])
        anchors.append(a)
        splits.append(r["split"])
        is_tb.append(cand_date in transition_band_dates(dates, labs))

    if not packs:
        c = embed_dim if arm == "B" else embed_dim * 3
        return {
            "x": np.zeros((0, c, GRID_KEEP, GRID_KEEP), np.float32),
            "labels": np.array([], dtype=object),
            "anchors": np.array([], dtype=object),
            "split": np.array([], dtype=object),
            "is_tb": np.array([], dtype=bool),
            "embed_dim": embed_dim,
        }
    print(f"  [{arm}] materializing {len(packs)} samples …", flush=True)
    return {
        "x": np.stack(packs, axis=0).astype(np.float16, copy=False),
        "labels": np.array(labels, dtype=object),
        "anchors": np.array(anchors, dtype=object),
        "split": np.array(splits, dtype=object),
        "is_tb": np.array(is_tb, dtype=bool),
        "embed_dim": embed_dim,
    }


def eval_arm_on_report(
    data: Mapping[str, Any],
    probs: np.ndarray,
    lo: float,
    hi: float,
) -> dict[str, Any]:
    anchors = data["anchors"].astype(str)
    split = data["split"].astype(str)
    labels = data["labels"].astype(str)
    is_tb = data["is_tb"]
    report = report_half_mask(anchors, split)
    dec = decisions_from_probs(probs, lo, hi)
    # overall
    ov_t = [labels[i] for i in range(len(labels)) if report[i]]
    ov_s = [dec[i] for i in range(len(labels)) if report[i]]
    tb_t = [labels[i] for i in range(len(labels)) if report[i] and is_tb[i]]
    tb_s = [dec[i] for i in range(len(labels)) if report[i] and is_tb[i]]
    return {
        "overall": rate_metrics(ov_t, ov_s),
        "transition_band": rate_metrics(tb_t, tb_s),
        "n_report": int(report.sum()),
        "n_report_tb": int((report & is_tb).sum()),
    }


# --------------------------------------------------------------------------- #
# Main pipeline
# --------------------------------------------------------------------------- #
def run_pilot(args: argparse.Namespace) -> dict[str, Any]:
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    patch_dir = out / "patch_tokens"
    patch_dir.mkdir(exist_ok=True)

    print("[load] features meta", flush=True)
    raw = np.load(args.features, allow_pickle=False)
    npz = {k: raw[k] for k in raw.files}
    rows = build_row_table(npz)
    anchors_unique = sorted({r["anchor_id"] for r in rows})

    vexcel_tifs = resolve_vexcel_paths(anchors_unique, Path(args.vexcel_crops))
    vexcel_paths = vexcel_tifs_to_pngs(vexcel_tifs, out / "vexcel_png")
    elig_meta, pair_ok, vex_ok = eligibility_masks(rows, vexcel_paths)
    print("[eligibility]", json.dumps(elig_meta, indent=2), flush=True)
    (out / "eligibility.json").write_text(
        json.dumps({"meta": elig_meta, "n_vexcel_paths": len(vexcel_paths)}, indent=2),
        encoding="utf-8",
    )

    cand_path = patch_dir / "cand_grids.npy"
    cand_meta_path = patch_dir / "cand_grids.meta.json"
    vex_path = patch_dir / "vexcel_grids.npy"
    vex_meta_path = patch_dir / "vexcel_grids.meta.json"
    vex_id_path = patch_dir / "vexcel_anchor_ids.json"

    if args.skip_extract and cand_path.exists() and vex_path.exists():
        print("[extract] skip — loading cache", flush=True)
    else:
        cand_paths = [r["png_path"] for r in rows]
        extract_patch_cache(
            cand_paths,
            cand_path,
            device=args.device,
            batch_size=args.batch_size,
            meta_path=cand_meta_path,
        )
        # stable order for vexcel
        vex_ids = sorted(vex_ok)
        vex_pngs = [vexcel_paths[a] for a in vex_ids]
        extract_patch_cache(
            vex_pngs,
            vex_path,
            device=args.device,
            batch_size=args.batch_size,
            meta_path=vex_meta_path,
        )
        vex_id_path.write_text(json.dumps(vex_ids), encoding="utf-8")

    cand_grids = load_grids(cand_path)
    vex_grids = load_grids(vex_path)
    vex_ids = json.loads(vex_id_path.read_text(encoding="utf-8"))
    vex_index = {a: i for i, a in enumerate(vex_ids)}
    embed_dim = int(cand_grids.shape[-1])
    print(f"[cache] cand={cand_grids.shape} vex={vex_grids.shape} dim={embed_dim}", flush=True)

    # ---- H3 smoke ----
    print("[H3] domain-gap smoke", flush=True)
    smoke = run_domain_gap_smoke(
        rows=rows,
        vex_ok=vex_ok,
        vexcel_paths=vexcel_paths,
        cand_grids=cand_grids,
        vex_index=vex_index,
        vex_grids=vex_grids,
        out_path=out / "smoke_domain_gap.json",
    )
    if smoke["verdict"] != "SMOKE_GO":
        result = {
            "status": "STOPPED_SMOKE_KILL",
            "smoke": smoke,
            "eligibility": elig_meta,
            "r2_verdict": None,
        }
        (out / "pilot_result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        (out / "summary.md").write_text(
            "# Pairing-v2 STOPPED — domain-gap smoke KILL\n\n"
            f"See smoke_domain_gap.json. Do not interpret as pairing failure.\n\n"
            f"```json\n{json.dumps(smoke, indent=2)}\n```\n",
            encoding="utf-8",
        )
        print("[stop] SMOKE_KILL — no head training", flush=True)
        return result

    if args.smoke_only:
        print("[done] smoke-only", flush=True)
        return {"status": "SMOKE_ONLY_GO", "smoke": smoke}

    # param match width for B
    probe = _build_head(embed_dim * 3, HEAD_WIDTH_V)
    n_v = count_params(probe)
    width_b = width_for_param_match(n_v, embed_dim)
    n_b = count_params(_build_head(embed_dim, width_b))
    print(f"[params] V width={HEAD_WIDTH_V} n={n_v}; B width={width_b} n={n_b} "
          f"ratio={n_v/max(1,n_b):.3f}", flush=True)

    seed_evals: dict[str, list[dict[str, Any]]] = {"V": [], "B": [], "N": [], "E": []}
    seed_logs: dict[str, list[dict[str, Any]]] = {"V": [], "B": [], "N": [], "E": []}

    # One arm at a time (centre GRID_KEEP tensors ~several GB each).
    arm_specs = (
        ("V", True, HEAD_WIDTH_V),
        ("B", False, width_b),
        ("N", True, HEAD_WIDTH_V),
        ("E", True, HEAD_WIDTH_V),
    )
    for arm, pair, width in arm_specs:
        print(f"[data] build arm {arm} (centre {GRID_KEEP}×{GRID_KEEP})", flush=True)
        data = build_arm_index(
            rows,
            arm=arm,
            pair_ok=pair_ok,
            vex_ok=vex_ok,
            cand_grids=cand_grids,
            vex_index=vex_index,
            vex_grids=vex_grids,
        )
        print(
            f"  [{arm}] n={len(data['labels'])} "
            f"x={data['x'].nbytes/1e9:.2f}GB shape={data['x'].shape}",
            flush=True,
        )
        for seed in SEEDS:
            print(f"==== arm {arm} seed {seed} ====", flush=True)
            arm_dir = out / f"arm_{arm}" / f"seed{seed}"
            arm_dir.mkdir(parents=True, exist_ok=True)
            head_pt = arm_dir / "head.pt"
            log = train_patch_head(
                data["x"],
                data["labels"],
                data["anchors"].astype(str),
                data["split"].astype(str),
                pair=pair,
                width=width,
                seed=seed,
                out_path=head_pt,
                embed_dim=embed_dim,
            )
            model = load_head(head_pt)
            probs = predict_probs(model, data["x"])
            cal = calibrate_probs(
                probs,
                data["labels"],
                data["anchors"].astype(str),
                data["split"].astype(str),
                coverage_floor=args.coverage_floor,
            )
            (arm_dir / "calibration.json").write_text(
                json.dumps(cal, indent=2), encoding="utf-8"
            )
            ev = eval_arm_on_report(data, probs, float(cal["lo"]), float(cal["hi"]))
            seed_evals[arm].append(ev)
            seed_logs[arm].append({**log, "calibration": cal})
            print(
                f"  [{arm} s{seed}] f1={log['best_val_macro_f1']:.3f} "
                f"TB fp_rate={ev['transition_band']['fp_rate']} "
                f"fn_rate={ev['transition_band']['fn_rate']} "
                f"agree={ev['overall']['decided_agreement']}",
                flush=True,
            )
            del model, probs
        del data
        import gc

        gc.collect()

    def pool_rate(arm: str, key: str, stratum: str = "transition_band") -> float | None:
        vals = [e[stratum][key] for e in seed_evals[arm] if e[stratum][key] is not None]
        return float(np.mean(vals)) if vals else None

    def pool_den(arm: str, key: str) -> int:
        # mean denominator across seeds (for bootstrap)
        vals = [e["transition_band"][key] for e in seed_evals[arm]]
        return int(round(float(np.mean(vals)))) if vals else 0

    fp_v = pool_rate("V", "fp_rate")
    fp_b = pool_rate("B", "fp_rate")
    fn_v = pool_rate("V", "fn_rate")
    fn_b = pool_rate("B", "fn_rate")
    fp_red, fp_ci = bootstrap_reduction_ci(
        fp_v, fp_b, pool_den("V", "n_absent_decided"), pool_den("B", "n_absent_decided")
    )
    fn_red, fn_ci = bootstrap_reduction_ci(
        fn_v, fn_b, pool_den("V", "n_present_decided"), pool_den("B", "n_present_decided")
    )

    metrics_flat = {
        "transition_fp_rate_reduction_pct": (fp_red * 100.0) if fp_red is not None else -1.0,
        "transition_fp_rate_reduction_ci_low_pct": (fp_ci * 100.0) if fp_ci is not None else -1.0,
        "transition_fn_rate_reduction_pct": (fn_red * 100.0) if fn_red is not None else -1.0,
        "transition_fn_rate_reduction_ci_low_pct": (fn_ci * 100.0) if fn_ci is not None else -1.0,
        "overall_decided_agreement_v": pool_rate("V", "decided_agreement", "overall") or 0.0,
        "overall_decided_agreement_b": pool_rate("B", "decided_agreement", "overall") or 0.0,
        "present_to_unusable_rate_v": pool_rate("V", "present_to_unusable_rate") or 0.0,
        "present_to_unusable_rate_b": pool_rate("B", "present_to_unusable_rate") or 0.0,
        "unusable_recall_v": pool_rate("V", "unusable_recall", "overall") or 0.0,
        "unusable_recall_b": pool_rate("B", "unusable_recall", "overall") or 0.0,
        "arm_v_params": float(n_v),
        "arm_b_params": float(n_b),
        "n_seeds": float(len(SEEDS)),
    }
    ok, lines = check_anchor_pair_v2_r1(metrics_flat)
    verdict = {
        "verdict": "GO" if ok else "KILL",
        "checker_lines": lines,
        "metrics_flat": metrics_flat,
        "pooled": {
            "fp_rate_v": fp_v,
            "fp_rate_b": fp_b,
            "fn_rate_v": fn_v,
            "fn_rate_b": fn_b,
            "fp_reduction": fp_red,
            "fp_reduction_ci_low": fp_ci,
            "fn_reduction": fn_red,
            "fn_reduction_ci_low": fn_ci,
        },
    }
    print("[R2]", verdict["verdict"], flush=True)
    for ln in lines:
        print(" ", ln, flush=True)

    # H2 ride-along
    h2 = {
        "fp_rate_V": fp_v,
        "fp_rate_N": pool_rate("N", "fp_rate"),
        "fp_rate_E": pool_rate("E", "fp_rate"),
        "fn_rate_V": fn_v,
        "fn_rate_N": pool_rate("N", "fn_rate"),
        "fn_rate_E": pool_rate("E", "fn_rate"),
    }

    result = {
        "status": "COMPLETE",
        "prereg": "DATA-anchor-pair-v2-prereg-2026-07-10",
        "smoke": smoke,
        "eligibility": elig_meta,
        "params": {"arm_v": n_v, "arm_b": n_b, "width_v": HEAD_WIDTH_V, "width_b": width_b},
        "seed_evals": seed_evals,
        "seed_logs": {
            a: [{k: v for k, v in lg.items() if k != "warnings"} for lg in logs]
            for a, logs in seed_logs.items()
        },
        "h2_ride_along": h2,
        "r2_verdict": verdict,
    }
    (out / "pilot_result.json").write_text(
        json.dumps(result, indent=2, default=str), encoding="utf-8"
    )
    (out / "metrics_for_checker.json").write_text(
        json.dumps(metrics_flat, indent=2), encoding="utf-8"
    )
    (out / "summary.md").write_text(_summary_md(result), encoding="utf-8")
    print(f"[done] {out / 'pilot_result.json'}", flush=True)
    return result


def _summary_md(result: Mapping[str, Any]) -> str:
    v = result.get("r2_verdict") or {}
    smoke = result.get("smoke") or {}
    p = v.get("pooled") or {}
    lines = [
        "# Pairing-v2 pilot summary",
        "",
        f"**Domain-gap smoke:** {smoke.get('verdict')}",
        f"**R2 verdict (Arm V vs B):** {v.get('verdict', 'n/a')}",
        "",
        "## Pooled rates (3 seeds, report-half TB)",
        f"- FP rate V/B: {p.get('fp_rate_v')} / {p.get('fp_rate_b')} "
        f"(red={p.get('fp_reduction')}, CI_lo={p.get('fp_reduction_ci_low')})",
        f"- FN rate V/B: {p.get('fn_rate_v')} / {p.get('fn_rate_b')} "
        f"(red={p.get('fn_reduction')}, CI_lo={p.get('fn_reduction_ci_low')})",
        "",
        "## H2 polarity ride-along (FP rates)",
        f"- V={result.get('h2_ride_along', {}).get('fp_rate_V')} "
        f"N={result.get('h2_ride_along', {}).get('fp_rate_N')} "
        f"E={result.get('h2_ride_along', {}).get('fp_rate_E')}",
        "",
        "## Checker",
        "```",
        *list(v.get("checker_lines") or []),
        "```",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--features", type=Path, default=FEATURES_DEFAULT)
    ap.add_argument("--vexcel-crops", type=Path, default=VEXCEL_CROPS_DEFAULT)
    ap.add_argument("--out-dir", type=Path, default=OUT_DEFAULT)
    ap.add_argument("--device", default="cuda", choices=("cuda", "cpu"))
    ap.add_argument("--batch-size", type=int, default=BATCH_EXTRACT)
    ap.add_argument("--coverage-floor", type=float, default=DEFAULT_COVERAGE_FLOOR)
    ap.add_argument("--skip-extract", action="store_true")
    ap.add_argument("--smoke-only", action="store_true", help="stop after H3 smoke")
    args = ap.parse_args()
    run_pilot(args)


if __name__ == "__main__":
    main()
