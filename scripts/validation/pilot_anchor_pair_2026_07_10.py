#!/usr/bin/env python3
"""Anchor-pair student pilot (pre-reg 2026-07-10).

Implements the locked design in
``docs/dinov3_scorer/DATA-anchor-pair-pilot-prereg-2026-07-10.md``:

* Population: slice-4/5 distill corpus + ISSUE-02 anchor-disjoint split
  (cached embeddings ``nomarker_bilinear518_k6``).
* Eligible targets: ≥1 teacher ``absent`` frame; anchor = earliest such frame.
* Arm A (bet): MLP on ``[emb_cand, emb_anchor, emb_cand − emb_anchor]``.
* Arm B (capacity control): identical MLP on ``emb_cand`` alone.
* Slice-5 linear head reported as context.
* Primary stratum: teacher transition band ``[last-absent, first-present] ±1``.
* Verdict R1 arithmetic GO/KILL on report half (Arm A vs Arm B).

Zero API, zero new GT, frozen backbone, local GPU only.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import sys
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
    DEFAULT_BATCH_SIZE_TRAIN,
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
    _year_bucket,
)

# --------------------------------------------------------------------------- #
# Locked pilot constants (pre-registration — do not sweep)
# --------------------------------------------------------------------------- #
FEATURES_DEFAULT = Path.home() / (
    "zasolar_data/slice5/out/nomarker_bilinear518_k6/features.npz"
)
LINEAR_HEAD_PT = Path.home() / (
    "zasolar_data/models/dinov2_floor/head_v1_20260705/head_v1.pt"
)
LINEAR_HEAD_JSON = LINEAR_HEAD_PT.with_suffix(".json")
OUT_DEFAULT = Path.home() / "zasolar_data/geid_temporal/pilot_anchor_pair_20260710"
CHIPGROUPS_DIR = Path.home() / (
    "zasolar_data/geid_temporal/"
    "jhb_full382_unified_A_merge01_c0925_fpcut_2026-06-01_chipgroups"
)

SEED = 0  # locked
HIDDEN = (512, 256)  # ≤3 hidden layers; param count ≪ 5M
MLP_LR = DEFAULT_LR
MLP_WD = DEFAULT_WEIGHT_DECAY
MLP_BATCH = DEFAULT_BATCH_SIZE_TRAIN
MLP_MAX_EPOCHS = DEFAULT_MAX_EPOCHS
MLP_PATIENCE = DEFAULT_PATIENCE

AREA_BUCKETS = (
    ("a_xs<15", 0.0, 15.0),
    ("b_sm15-40", 15.0, 40.0),
    ("c_md40-100", 40.0, 100.0),
    ("d_lg>=100", 100.0, math.inf),
)


# --------------------------------------------------------------------------- #
# Pure helpers (unit-tested)
# --------------------------------------------------------------------------- #
def area_bucket(area: float | None) -> str:
    if area is None or not math.isfinite(area):
        return "unknown"
    for name, lo, hi in AREA_BUCKETS:
        if lo <= area < hi:
            return name
    return "unknown"


def load_area_map(
    chipgroups_dir: Path = CHIPGROUPS_DIR,
) -> dict[str, float]:
    """Join ``source_area_m2`` from chip_targets + chip_groups_as_anchors."""
    out: dict[str, float] = {}
    for name in ("chip_targets.csv", "chip_groups_as_anchors.csv"):
        path = chipgroups_dir / name
        if not path.exists():
            continue
        with path.open(newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                aid = str(row.get("anchor_id", ""))
                raw = row.get("source_area_m2", "")
                if not aid or raw in ("", None):
                    continue
                try:
                    out[aid] = float(raw)
                except ValueError:
                    continue
    return out


def earliest_absent_key(dates: Sequence[str], labels: Sequence[str]) -> str | None:
    """Earliest capture_date among ``label == absent`` frames (lexicographic ISO)."""
    cands = [d for d, lab in zip(dates, labels) if lab == "absent"]
    return min(cands) if cands else None


def transition_band_dates(
    dates: Sequence[str], labels: Sequence[str]
) -> set[str]:
    """Frames in teacher ``[last-absent, first-present]`` window ±1 by index.

    * ``first_present`` = earliest date with teacher ``present``.
    * ``last_absent`` = latest date with teacher ``absent`` strictly before
      ``first_present``.
    * Expand closed index interval by ±1 along the sorted unique date sequence.
    * Empty if either bound is missing (no transition under teacher labels).
    """
    by_date: dict[str, set[str]] = defaultdict(set)
    for d, lab in zip(dates, labels):
        by_date[str(d)].add(str(lab))
    ordered = sorted(by_date)
    if not ordered:
        return set()

    present_dates = [d for d in ordered if "present" in by_date[d]]
    if not present_dates:
        return set()
    first_present = present_dates[0]
    absent_before = [
        d for d in ordered if d < first_present and "absent" in by_date[d]
    ]
    if not absent_before:
        return set()
    last_absent = absent_before[-1]

    i0 = ordered.index(last_absent)
    i1 = ordered.index(first_present)
    lo = max(0, min(i0, i1) - 1)
    hi = min(len(ordered) - 1, max(i0, i1) + 1)
    return set(ordered[lo : hi + 1])


def build_eligibility(
    anchors: np.ndarray,
    dates: np.ndarray,
    labels: np.ndarray,
) -> tuple[np.ndarray, dict[str, str], dict[str, Any]]:
    """Return (eligible_mask, anchor_id→anchor_date, eligibility_meta)."""
    anchors = np.asarray(anchors).astype(str)
    dates = np.asarray(dates).astype(str)
    labels = np.asarray(labels).astype(str)

    by_a: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for a, d, lab in zip(anchors, dates, labels):
        by_a[a].append((d, lab))

    anchor_date: dict[str, str] = {}
    eligible_ids: set[str] = set()
    ineligible_terminal: Counter[str] = Counter()
    for a, rows in by_a.items():
        ad = earliest_absent_key([r[0] for r in rows], [r[1] for r in rows])
        if ad is None:
            # terminal status not available here; counted later if needed
            continue
        eligible_ids.add(a)
        anchor_date[a] = ad

    mask = np.array([a in eligible_ids for a in anchors], dtype=bool)
    meta = {
        "n_anchors_total": len(by_a),
        "n_anchors_eligible": len(eligible_ids),
        "n_anchors_ineligible": len(by_a) - len(eligible_ids),
        "ineligible_share": (len(by_a) - len(eligible_ids)) / max(1, len(by_a)),
        "n_rows_eligible": int(mask.sum()),
        "n_rows_total": int(len(mask)),
    }
    return mask, anchor_date, meta


def build_transition_mask(
    anchors: np.ndarray,
    dates: np.ndarray,
    labels: np.ndarray,
    eligible_mask: np.ndarray,
) -> tuple[np.ndarray, dict[str, Any]]:
    """Boolean mask: eligible row is inside its target's transition band."""
    anchors = np.asarray(anchors).astype(str)
    dates = np.asarray(dates).astype(str)
    labels = np.asarray(labels).astype(str)

    by_a: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for i, ok in enumerate(eligible_mask):
        if not ok:
            continue
        by_a[anchors[i]].append((dates[i], labels[i]))

    band_dates: dict[str, set[str]] = {}
    n_with_band = 0
    for a, rows in by_a.items():
        bd = transition_band_dates([r[0] for r in rows], [r[1] for r in rows])
        band_dates[a] = bd
        if bd:
            n_with_band += 1

    mask = np.zeros(len(anchors), dtype=bool)
    for i, ok in enumerate(eligible_mask):
        if not ok:
            continue
        if dates[i] in band_dates.get(anchors[i], set()):
            mask[i] = True

    meta = {
        "n_eligible_anchors": len(by_a),
        "n_anchors_with_transition_band": n_with_band,
        "n_transition_rows": int(mask.sum()),
    }
    return mask, meta


def pair_features(
    features: np.ndarray,
    anchors: np.ndarray,
    dates: np.ndarray,
    anchor_date: Mapping[str, str],
    row_mask: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    """Build ``[cand, anchor, cand−anchor]`` for masked rows.

    Returns ``(pair_feats[M, 3D], keep_mask_over_full_rows, meta)``.
    Rows whose anchor embedding is missing are dropped (should be rare).
    """
    features = np.asarray(features, dtype=np.float32)
    anchors = np.asarray(anchors).astype(str)
    dates = np.asarray(dates).astype(str)
    d = features.shape[1]

    # index of earliest-absent anchor frame per target
    anchor_idx: dict[str, int] = {}
    for i in range(len(anchors)):
        a = anchors[i]
        ad = anchor_date.get(a)
        if ad is None:
            continue
        if dates[i] == ad and a not in anchor_idx:
            anchor_idx[a] = i
        elif dates[i] == ad:
            # prefer first occurrence if duplicates
            pass

    keep = np.zeros(len(anchors), dtype=bool)
    pairs: list[np.ndarray] = []
    missing_anchor = 0
    for i, ok in enumerate(row_mask):
        if not ok:
            continue
        a = anchors[i]
        j = anchor_idx.get(a)
        if j is None:
            missing_anchor += 1
            continue
        cand = features[i]
        anc = features[j]
        pairs.append(np.concatenate([cand, anc, cand - anc], axis=0))
        keep[i] = True

    if pairs:
        out = np.stack(pairs, axis=0).astype(np.float32)
    else:
        out = np.zeros((0, 3 * d), dtype=np.float32)
    meta = {
        "embed_dim": int(d),
        "pair_dim": int(3 * d),
        "n_pairs": int(out.shape[0]),
        "n_missing_anchor_emb": missing_anchor,
        "n_anchor_frames_indexed": len(anchor_idx),
    }
    return out, keep, meta


# --------------------------------------------------------------------------- #
# MLP train / forward / calibrate / decide
# --------------------------------------------------------------------------- #
def mlp_param_count(in_dim: int, hidden: Sequence[int] = HIDDEN, n_classes: int = 3) -> int:
    total = 0
    d = in_dim
    for h in hidden:
        total += d * h + h
        d = h
    total += d * n_classes + n_classes
    return total


def train_mlp_head(
    features: np.ndarray,
    labels: np.ndarray,
    anchors: np.ndarray,
    split: np.ndarray,
    *,
    out_path: Path,
    seed: int = SEED,
    hidden: Sequence[int] = HIDDEN,
    lr: float = MLP_LR,
    weight_decay: float = MLP_WD,
    batch_size: int = MLP_BATCH,
    max_epochs: int = MLP_MAX_EPOCHS,
    patience: int = MLP_PATIENCE,
    config: Mapping[str, Any] | None = None,
    provenance: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Fit a small ReLU MLP (≤3 hidden) on train split; early-stop on val macro-F1."""
    import torch
    from torch import nn

    features = np.asarray(features, dtype=np.float32)
    labels = np.asarray(labels).astype(str)
    anchors = np.asarray(anchors).astype(str)
    split = np.asarray(split).astype(str)

    is_train = split == "train"
    if not is_train.any():
        raise SystemExit("no train rows for MLP fit")
    _assert_no_heldout_leak(split, is_train)

    train_anchor_set = sorted(set(anchors[is_train]))
    val_set = set(_val_anchors(train_anchor_set))
    in_val = np.array([a in val_set for a in anchors])
    is_val = is_train & in_val
    is_fit = is_train & ~in_val
    if not is_fit.any():
        raise SystemExit("fit set empty after val carve-out")

    unknown = sorted({lbl for lbl in labels if lbl not in CLASS_INDEX})
    if unknown:
        raise SystemExit(f"unknown labels {unknown}")
    y_all = np.array([CLASS_INDEX[lbl] for lbl in labels], dtype=np.int64)

    fit_x, fit_y = features[is_fit], y_all[is_fit]
    val_x, val_y = features[is_val], y_all[is_val]
    in_dim = int(features.shape[1])
    weights, counts, warnings = _class_weights(list(fit_y))

    class MLP(nn.Module):
        def __init__(self) -> None:
            super().__init__()
            layers: list[nn.Module] = []
            d = in_dim
            for h in hidden:
                layers.append(nn.Linear(d, int(h)))
                layers.append(nn.ReLU())
                d = int(h)
            layers.append(nn.Linear(d, len(CLASS_ORDER)))
            self.net = nn.Sequential(*layers)

        def forward(self, x: Any) -> Any:
            return self.net(x)

    torch.manual_seed(int(seed))
    model = MLP()
    n_params = sum(p.numel() for p in model.parameters())
    if n_params > 5_000_000:
        raise SystemExit(f"MLP has {n_params} params > 5M budget")

    optimizer = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=weight_decay)
    loss_fn = nn.CrossEntropyLoss(weight=torch.tensor(weights, dtype=torch.float32))
    x_fit_t = torch.tensor(fit_x, dtype=torch.float32)
    y_fit_t = torch.tensor(fit_y, dtype=torch.long)
    x_val_t = torch.tensor(val_x, dtype=torch.float32)
    gen = torch.Generator().manual_seed(int(seed))

    best_f1 = -1.0
    best_state: dict[str, Any] | None = None
    no_improve = 0
    val_curve: list[float] = []
    epochs_run = 0
    for epoch in range(max_epochs):
        model.train()
        perm = torch.randperm(x_fit_t.shape[0], generator=gen)
        for start in range(0, x_fit_t.shape[0], batch_size):
            idx = perm[start : start + batch_size]
            optimizer.zero_grad()
            loss = loss_fn(model(x_fit_t[idx]), y_fit_t[idx])
            loss.backward()
            optimizer.step()
        model.eval()
        with torch.no_grad():
            if x_val_t.shape[0] > 0:
                vpred = model(x_val_t).argmax(dim=1).cpu().numpy()
                f1 = _macro_f1(val_y, vpred)
            else:
                tpred = model(x_fit_t).argmax(dim=1).cpu().numpy()
                f1 = _macro_f1(fit_y, tpred)
        val_curve.append(f1)
        epochs_run = epoch + 1
        if f1 > best_f1 + 1e-9:
            best_f1 = f1
            best_state = {k: v.detach().clone() for k, v in model.state_dict().items()}
            no_improve = 0
        else:
            no_improve += 1
            if no_improve >= patience:
                break
    if best_state is None:
        best_state = {k: v.detach().clone() for k, v in model.state_dict().items()}

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    bundle = {
        "schema_version": 1,
        "head_arch": "mlp",
        "hidden": list(hidden),
        "in_dim": in_dim,
        "state_dict": best_state,
        "class_order": list(CLASS_ORDER),
    }
    torch.save(bundle, out_path)

    sidecar = {
        "schema_version": 1,
        "head_arch": "mlp",
        "hidden": list(hidden),
        "in_dim": in_dim,
        "n_params": n_params,
        "class_order": list(CLASS_ORDER),
        "calibration": None,
        "config": dict(config or {}),
        "provenance": dict(provenance or {}),
        "seed": int(seed),
    }
    out_path.with_suffix(".json").write_text(json.dumps(sidecar, indent=2), encoding="utf-8")

    train_log = {
        "seed": int(seed),
        "head_arch": "mlp",
        "hidden": list(hidden),
        "n_params": n_params,
        "in_dim": in_dim,
        "hyperparams": {
            "lr": lr,
            "weight_decay": weight_decay,
            "batch_size": batch_size,
            "max_epochs": max_epochs,
            "patience": patience,
        },
        "class_counts": {CLASS_ORDER[c]: counts[c] for c in range(len(CLASS_ORDER))},
        "class_weights": {CLASS_ORDER[c]: weights[c] for c in range(len(CLASS_ORDER))},
        "warnings": warnings,
        "n_train_anchors": len(train_anchor_set),
        "n_val_anchors": len(val_set),
        "n_fit_rows": int(is_fit.sum()),
        "n_val_rows": int(is_val.sum()),
        "epochs_run": epochs_run,
        "best_val_macro_f1": best_f1,
        "val_macro_f1_curve": val_curve,
    }
    out_path.with_name(out_path.stem + ".train_log.json").write_text(
        json.dumps(train_log, indent=2), encoding="utf-8"
    )
    return train_log


def _load_mlp(path: Path) -> tuple[Any, list[int], int]:
    import torch
    from torch import nn

    obj = torch.load(Path(path), map_location="cpu", weights_only=False)
    hidden = list(obj["hidden"])
    in_dim = int(obj["in_dim"])

    class MLP(nn.Module):
        def __init__(self) -> None:
            super().__init__()
            layers: list[nn.Module] = []
            d = in_dim
            for h in hidden:
                layers.append(nn.Linear(d, int(h)))
                layers.append(nn.ReLU())
                d = int(h)
            layers.append(nn.Linear(d, len(CLASS_ORDER)))
            self.net = nn.Sequential(*layers)

        def forward(self, x: Any) -> Any:
            return self.net(x)

    model = MLP()
    model.load_state_dict(obj["state_dict"])
    model.eval()
    return model, hidden, in_dim


def mlp_probs(features: np.ndarray, head_path: Path) -> np.ndarray:
    import torch

    model, _, _ = _load_mlp(head_path)
    x = torch.tensor(np.asarray(features, dtype=np.float32))
    with torch.no_grad():
        logits = model(x).cpu().numpy()
    logits = logits - logits.max(axis=1, keepdims=True)
    exp = np.exp(logits)
    return exp / exp.sum(axis=1, keepdims=True)


def linear_probs(features: np.ndarray, head_pt: Path) -> np.ndarray:
    import torch

    obj = torch.load(Path(head_pt), map_location="cpu", weights_only=False)
    state = obj["state_dict"] if isinstance(obj, dict) and "state_dict" in obj else obj
    w = np.asarray(state["weight"].detach().cpu().numpy(), dtype=np.float32)
    b = np.asarray(state["bias"].detach().cpu().numpy(), dtype=np.float32)
    logits = np.asarray(features, dtype=np.float32) @ w.T + b
    logits = logits - logits.max(axis=1, keepdims=True)
    exp = np.exp(logits)
    return exp / exp.sum(axis=1, keepdims=True)


def decisions_from_probs(probs: np.ndarray, lo: float, hi: float) -> list[str]:
    argm = probs.argmax(axis=1)
    pp = probs[:, 0]
    out: list[str] = []
    for i in range(probs.shape[0]):
        if int(argm[i]) == 2:
            out.append("unusable")
        elif float(pp[i]) > hi:
            out.append("present")
        elif float(pp[i]) < lo:
            out.append("absent")
        else:
            out.append("ambiguous")
    return out


def calibrate_from_probs(
    probs: np.ndarray,
    labels: np.ndarray,
    anchors: np.ndarray,
    split: np.ndarray,
    *,
    coverage_floor: float = DEFAULT_COVERAGE_FLOOR,
    grid_step: float = DEFAULT_GRID_STEP,
) -> dict[str, Any]:
    """Same band sweep as train_dinov3_head.calibrate_band, probs precomputed."""
    labels = np.asarray(labels).astype(str)
    anchors = np.asarray(anchors).astype(str)
    split = np.asarray(split).astype(str)

    train_a = set(anchors[split == "train"])
    held_a = set(anchors[split == "heldout"])
    if train_a & held_a:
        raise SystemExit("calib leak: train∩heldout nonempty")

    calib_set, _ = _split_heldout_halves(held_a)
    held_mask = split == "heldout"
    in_calib = np.array([a in calib_set for a in anchors])
    is_pa = np.isin(labels, np.array(["present", "absent"]))
    row_mask = held_mask & in_calib & is_pa
    if not row_mask.any():
        raise SystemExit("no calib-half present/absent rows")

    pp = probs[row_mask, 0]
    argm = probs[row_mask].argmax(axis=1)
    teacher = labels[row_mask]
    total = int(row_mask.sum())
    not_unusable = argm != 2

    grid = _threshold_grid(grid_step)
    frontier: list[dict[str, float]] = []
    for lo in grid:
        for hi in grid:
            if hi < lo:
                continue
            decided = not_unusable & ((pp > hi) | (pp < lo))
            n_dec = int(decided.sum())
            coverage = n_dec / total
            if n_dec > 0:
                pred = np.where(pp[decided] > hi, "present", "absent")
                agreement = float((pred == teacher[decided]).mean())
            else:
                agreement = 0.0
            frontier.append(
                {
                    "lo": round(lo, 10),
                    "hi": round(hi, 10),
                    "coverage": coverage,
                    "agreement": agreement,
                }
            )

    feasible = [f for f in frontier if f["coverage"] >= coverage_floor]
    fallback = False
    if not feasible:
        fallback = True
        max_cov = max(f["coverage"] for f in frontier)
        feasible = [f for f in frontier if f["coverage"] == max_cov]
    best = min(
        feasible,
        key=lambda f: (-f["agreement"], -f["coverage"], (f["hi"] - f["lo"]), f["lo"]),
    )
    rule = (
        f"max decided-agreement s.t. coverage>={coverage_floor:g}"
        + (" (fallback: argmax-coverage)" if fallback else "")
        + "; tie-break: max agreement, max coverage, min width, min lo"
    )
    return {
        "lo": best["lo"],
        "hi": best["hi"],
        "rule": rule,
        "calib_anchors": len(set(anchors[row_mask])),
        "decided_agreement": best["agreement"],
        "abstain_rate": 1.0 - best["coverage"],
        "n_calib_pa_rows": total,
    }


# --------------------------------------------------------------------------- #
# Metrics
# --------------------------------------------------------------------------- #
def stratum_metrics(
    teacher: Sequence[str],
    student: Sequence[str],
) -> dict[str, Any]:
    """Decided-agreement, FP/FN counts, unusable recall on a row set."""
    n = len(teacher)
    assert n == len(student)
    pa_decided = 0
    pa_agree = 0
    fp = 0  # teacher absent, student present
    fn = 0  # teacher present, student absent
    n_teacher_unusable = 0
    n_unusable_hit = 0
    conf: Counter[tuple[str, str]] = Counter()
    for t, s in zip(teacher, student):
        conf[(t, s)] += 1
        if t == "unusable":
            n_teacher_unusable += 1
            if s == "unusable":
                n_unusable_hit += 1
        if s in ("present", "absent"):
            pa_decided += 1
            if s == t:
                pa_agree += 1
            if t == "absent" and s == "present":
                fp += 1
            if t == "present" and s == "absent":
                fn += 1
    return {
        "n": n,
        "pa_decided": pa_decided,
        "pa_agree": pa_agree,
        "decided_agreement": (pa_agree / pa_decided) if pa_decided else None,
        "coverage": (pa_decided / n) if n else 0.0,
        "fp": fp,
        "fn": fn,
        "n_teacher_unusable": n_teacher_unusable,
        "unusable_recall": (
            n_unusable_hit / n_teacher_unusable if n_teacher_unusable else None
        ),
        "confusion": {f"{t}|{s}": c for (t, s), c in sorted(conf.items())},
    }


def report_half_mask(anchors: np.ndarray, split: np.ndarray) -> np.ndarray:
    anchors = np.asarray(anchors).astype(str)
    split = np.asarray(split).astype(str)
    held = set(anchors[split == "heldout"])
    _, report = _split_heldout_halves(held)
    return np.array(
        [(split[i] == "heldout" and anchors[i] in report) for i in range(len(anchors))],
        dtype=bool,
    )


def evaluate_arm(
    *,
    name: str,
    probs: np.ndarray,
    lo: float,
    hi: float,
    labels: np.ndarray,
    anchors: np.ndarray,
    dates: np.ndarray,
    split: np.ndarray,
    eligible_mask: np.ndarray,
    transition_mask: np.ndarray,
    area_by_anchor: Mapping[str, float],
) -> dict[str, Any]:
    decisions = decisions_from_probs(probs, lo, hi)
    report = report_half_mask(anchors, split)
    # pilot claims only on eligible targets
    base = report & eligible_mask

    def take(mask: np.ndarray) -> dict[str, Any]:
        idx = np.where(mask)[0]
        t = [str(labels[i]) for i in idx]
        s = [decisions[i] for i in idx]
        return stratum_metrics(t, s)

    overall = take(base)
    transition = take(base & transition_mask)

    by_area: dict[str, Any] = {}
    buckets = np.array(
        [area_bucket(area_by_anchor.get(str(a))) for a in anchors], dtype=object
    )
    for bname, _, _ in AREA_BUCKETS:
        by_area[bname] = take(base & (buckets == bname))
    by_area["unknown"] = take(base & (buckets == "unknown"))

    # also overall including ineligible (context)
    overall_all_report = take(report)

    return {
        "arm": name,
        "band": {"lo": lo, "hi": hi},
        "overall_eligible_report": overall,
        "overall_all_report": overall_all_report,
        "transition_band": transition,
        "by_area_bucket": by_area,
        "n_report_eligible": int(base.sum()),
        "n_report_transition": int((base & transition_mask).sum()),
    }


def r1_verdict(arm_a: Mapping[str, Any], arm_b: Mapping[str, Any]) -> dict[str, Any]:
    """Locked R1 arithmetic — no discretion."""
    ta = arm_a["transition_band"]
    tb = arm_b["transition_band"]
    oa = arm_a["overall_eligible_report"]
    ob = arm_b["overall_eligible_report"]

    fp_a, fp_b = int(ta["fp"]), int(tb["fp"])
    fn_a, fn_b = int(ta["fn"]), int(tb["fn"])

    fp_drop = (fp_b - fp_a) / fp_b if fp_b > 0 else (1.0 if fp_a == 0 else 0.0)
    fn_drop = (fn_b - fn_a) / fn_b if fn_b > 0 else (1.0 if fn_a == 0 else 0.0)

    agree_a = oa["decided_agreement"]
    agree_b = ob["decided_agreement"]
    ur_a = oa["unusable_recall"]
    ur_b = ob["unusable_recall"]

    # None-safe: missing agreement fails the ≥ clause
    cond_fp = fp_drop >= 0.30
    cond_fn = fn_drop >= 0.30
    cond_agree = (
        agree_a is not None and agree_b is not None and agree_a >= agree_b
    )
    cond_unusable = (
        ur_a is not None and ur_b is not None and ur_a >= (ur_b - 0.02)
    )
    # if either side has zero teacher-unusable, treat unusable recall as tied pass
    if oa["n_teacher_unusable"] == 0 and ob["n_teacher_unusable"] == 0:
        cond_unusable = True

    go = bool(cond_fp and cond_fn and cond_agree and cond_unusable)
    return {
        "verdict": "GO" if go else "KILL",
        "conditions": {
            "transition_fp_drop_ge_30pct": {
                "pass": cond_fp,
                "fp_a": fp_a,
                "fp_b": fp_b,
                "drop": fp_drop,
            },
            "transition_fn_drop_ge_30pct": {
                "pass": cond_fn,
                "fn_a": fn_a,
                "fn_b": fn_b,
                "drop": fn_drop,
            },
            "overall_decided_agreement_A_ge_B": {
                "pass": cond_agree,
                "agree_a": agree_a,
                "agree_b": agree_b,
            },
            "unusable_recall_A_ge_B_minus_2pp": {
                "pass": cond_unusable,
                "ur_a": ur_a,
                "ur_b": ur_b,
            },
        },
    }


# --------------------------------------------------------------------------- #
# Pipeline
# --------------------------------------------------------------------------- #
def _subset_npz(npz: Mapping[str, np.ndarray], mask: np.ndarray) -> dict[str, np.ndarray]:
    out: dict[str, np.ndarray] = {}
    n = len(mask)
    for k, v in npz.items():
        if isinstance(v, np.ndarray) and len(v) == n:
            out[k] = v[mask]
        else:
            out[k] = v
    return out


def run_pilot(
    *,
    features_path: Path,
    linear_head_pt: Path,
    linear_head_json: Path,
    out_dir: Path,
    coverage_floor: float = DEFAULT_COVERAGE_FLOOR,
) -> dict[str, Any]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"[load] {features_path}", flush=True)
    raw = np.load(features_path, allow_pickle=False)
    npz = {k: raw[k] for k in raw.files}
    features = np.asarray(npz["features"], dtype=np.float32)
    anchors = np.asarray(npz["anchor_id"]).astype(str)
    dates = np.asarray(npz["capture_date"]).astype(str)
    labels = np.asarray(npz["label_3class"]).astype(str)
    split = np.asarray(npz["split"]).astype(str)

    # terminal status for ineligible share report
    terms = np.asarray(npz["terminal_status"]).astype(str)
    anchor_term: dict[str, str] = {}
    for a, t in zip(anchors, terms):
        anchor_term.setdefault(str(a), str(t))

    print("[eligibility]", flush=True)
    eligible_mask, anchor_date, elig_meta = build_eligibility(anchors, dates, labels)
    inelig_ids = sorted(
        {a for a in set(anchors) if a not in anchor_date}
    )
    elig_meta["ineligible_by_terminal"] = dict(
        Counter(anchor_term.get(a, "unknown") for a in inelig_ids)
    )
    elig_meta["ineligible_anchor_ids"] = inelig_ids
    print(json.dumps(elig_meta, indent=2), flush=True)

    print("[transition-band]", flush=True)
    transition_mask, tb_meta = build_transition_mask(
        anchors, dates, labels, eligible_mask
    )
    print(json.dumps(tb_meta, indent=2), flush=True)

    area_by_anchor = load_area_map()
    n_area = sum(1 for a in set(anchors) if a in area_by_anchor)
    print(f"[area] matched {n_area}/{len(set(anchors))} anchors", flush=True)

    # ---- pair features (eligible only) ----
    print("[pair-features]", flush=True)
    pair_feats, pair_keep, pair_meta = pair_features(
        features, anchors, dates, anchor_date, eligible_mask
    )
    print(json.dumps(pair_meta, indent=2), flush=True)
    # align masks to rows that actually got pairs
    eligible_mask = eligible_mask & pair_keep
    transition_mask = transition_mask & pair_keep

    # row index map for pair_feats (dense over pair_keep)
    pair_row_index = np.full(len(anchors), -1, dtype=np.int64)
    pair_row_index[pair_keep] = np.arange(pair_keep.sum())

    # persist meta
    (out_dir / "eligibility.json").write_text(
        json.dumps({"eligibility": elig_meta, "transition": tb_meta, "pair": pair_meta}, indent=2),
        encoding="utf-8",
    )

    # ========== STEP 0: linear head baseline (context) ==========
    print("[step0] slice-5 linear head on report-eligible", flush=True)
    lin_side = json.loads(Path(linear_head_json).read_text(encoding="utf-8"))
    lin_lo = float(lin_side["calibration"]["lo"])
    lin_hi = float(lin_side["calibration"]["hi"])
    lin_probs = linear_probs(features, linear_head_pt)
    linear_eval = evaluate_arm(
        name="slice5_linear",
        probs=lin_probs,
        lo=lin_lo,
        hi=lin_hi,
        labels=labels,
        anchors=anchors,
        dates=dates,
        split=split,
        eligible_mask=eligible_mask,
        transition_mask=transition_mask,
        area_by_anchor=area_by_anchor,
    )
    print(
        f"  linear overall agree={linear_eval['overall_eligible_report']['decided_agreement']} "
        f"tb FP={linear_eval['transition_band']['fp']} FN={linear_eval['transition_band']['fn']}",
        flush=True,
    )

    # ========== Arm B: MLP on emb_cand alone ==========
    print("[armB] train MLP on emb_cand (capacity control)", flush=True)
    arm_b_dir = out_dir / "arm_B_cand_mlp"
    arm_b_dir.mkdir(parents=True, exist_ok=True)
    # train on eligible rows only, features = emb_cand
    elig_idx = np.where(eligible_mask)[0]
    b_feats = features[elig_idx]
    b_labels = labels[elig_idx]
    b_anchors = anchors[elig_idx]
    b_split = split[elig_idx]
    b_head = arm_b_dir / "head.pt"
    log_b = train_mlp_head(
        b_feats,
        b_labels,
        b_anchors,
        b_split,
        out_path=b_head,
        seed=SEED,
        config={
            "arm": "B",
            "input": "emb_cand",
            "hidden": list(HIDDEN),
            "backbone": "vit_small_patch14_dinov2.lvd142m",
            "chip_render_variant": "nomarker_bilinear518_k6",
        },
        provenance={
            "pilot": "anchor_pair_2026_07_10",
            "features_npz": str(features_path),
            "seed": SEED,
        },
    )
    print(
        f"  armB val_macro_f1={log_b['best_val_macro_f1']:.4f} "
        f"epochs={log_b['epochs_run']} params={log_b['n_params']}",
        flush=True,
    )
    b_probs_elig = mlp_probs(b_feats, b_head)
    cal_b = calibrate_from_probs(
        b_probs_elig, b_labels, b_anchors, b_split, coverage_floor=coverage_floor
    )
    # write calib into sidecar
    side_b = json.loads(b_head.with_suffix(".json").read_text(encoding="utf-8"))
    side_b["calibration"] = cal_b
    b_head.with_suffix(".json").write_text(json.dumps(side_b, indent=2), encoding="utf-8")
    print(f"  armB calib lo={cal_b['lo']} hi={cal_b['hi']} agree={cal_b['decided_agreement']}", flush=True)

    # full-length probs for evaluate (only eligible rows matter; zeros elsewhere)
    b_probs_full = np.zeros((len(anchors), 3), dtype=np.float32)
    b_probs_full[elig_idx] = b_probs_elig
    arm_b_eval = evaluate_arm(
        name="arm_B_cand_mlp",
        probs=b_probs_full,
        lo=float(cal_b["lo"]),
        hi=float(cal_b["hi"]),
        labels=labels,
        anchors=anchors,
        dates=dates,
        split=split,
        eligible_mask=eligible_mask,
        transition_mask=transition_mask,
        area_by_anchor=area_by_anchor,
    )
    print(
        f"  armB overall agree={arm_b_eval['overall_eligible_report']['decided_agreement']} "
        f"tb FP={arm_b_eval['transition_band']['fp']} FN={arm_b_eval['transition_band']['fn']}",
        flush=True,
    )

    # ========== Arm A: pair MLP ==========
    print("[armA] train pair MLP [cand, anchor, cand-anchor]", flush=True)
    arm_a_dir = out_dir / "arm_A_pair_mlp"
    arm_a_dir.mkdir(parents=True, exist_ok=True)
    # pair_feats is dense over pair_keep == eligible after align
    assert pair_feats.shape[0] == int(eligible_mask.sum())
    a_labels = labels[elig_idx]
    a_anchors = anchors[elig_idx]
    a_split = split[elig_idx]
    a_head = arm_a_dir / "head.pt"
    log_a = train_mlp_head(
        pair_feats,
        a_labels,
        a_anchors,
        a_split,
        out_path=a_head,
        seed=SEED,
        config={
            "arm": "A",
            "input": "[emb_cand, emb_anchor, emb_cand-emb_anchor]",
            "hidden": list(HIDDEN),
            "backbone": "vit_small_patch14_dinov2.lvd142m",
            "chip_render_variant": "nomarker_bilinear518_k6",
        },
        provenance={
            "pilot": "anchor_pair_2026_07_10",
            "features_npz": str(features_path),
            "seed": SEED,
        },
    )
    print(
        f"  armA val_macro_f1={log_a['best_val_macro_f1']:.4f} "
        f"epochs={log_a['epochs_run']} params={log_a['n_params']}",
        flush=True,
    )
    a_probs_elig = mlp_probs(pair_feats, a_head)
    cal_a = calibrate_from_probs(
        a_probs_elig, a_labels, a_anchors, a_split, coverage_floor=coverage_floor
    )
    side_a = json.loads(a_head.with_suffix(".json").read_text(encoding="utf-8"))
    side_a["calibration"] = cal_a
    a_head.with_suffix(".json").write_text(json.dumps(side_a, indent=2), encoding="utf-8")
    print(f"  armA calib lo={cal_a['lo']} hi={cal_a['hi']} agree={cal_a['decided_agreement']}", flush=True)

    a_probs_full = np.zeros((len(anchors), 3), dtype=np.float32)
    a_probs_full[elig_idx] = a_probs_elig
    arm_a_eval = evaluate_arm(
        name="arm_A_pair_mlp",
        probs=a_probs_full,
        lo=float(cal_a["lo"]),
        hi=float(cal_a["hi"]),
        labels=labels,
        anchors=anchors,
        dates=dates,
        split=split,
        eligible_mask=eligible_mask,
        transition_mask=transition_mask,
        area_by_anchor=area_by_anchor,
    )
    print(
        f"  armA overall agree={arm_a_eval['overall_eligible_report']['decided_agreement']} "
        f"tb FP={arm_a_eval['transition_band']['fp']} FN={arm_a_eval['transition_band']['fn']}",
        flush=True,
    )

    verdict = r1_verdict(arm_a_eval, arm_b_eval)
    print(f"[R1] verdict={verdict['verdict']}", flush=True)
    print(json.dumps(verdict, indent=2), flush=True)

    result = {
        "prereg": "DATA-anchor-pair-pilot-prereg-2026-07-10",
        "seed": SEED,
        "hidden": list(HIDDEN),
        "features_npz": str(features_path),
        "linear_head": str(linear_head_pt),
        "eligibility": elig_meta,
        "transition": tb_meta,
        "pair": pair_meta,
        "mlp_param_budget": {
            "arm_A": log_a["n_params"],
            "arm_B": log_b["n_params"],
            "cap": 5_000_000,
        },
        "step0_linear": linear_eval,
        "arm_B": {
            "train_log": {
                k: log_b[k]
                for k in (
                    "seed",
                    "n_params",
                    "in_dim",
                    "epochs_run",
                    "best_val_macro_f1",
                    "n_fit_rows",
                    "n_val_rows",
                    "class_counts",
                )
            },
            "calibration": cal_b,
            "eval": arm_b_eval,
        },
        "arm_A": {
            "train_log": {
                k: log_a[k]
                for k in (
                    "seed",
                    "n_params",
                    "in_dim",
                    "epochs_run",
                    "best_val_macro_f1",
                    "n_fit_rows",
                    "n_val_rows",
                    "class_counts",
                )
            },
            "calibration": cal_a,
            "eval": arm_a_eval,
        },
        "r1_verdict": verdict,
    }
    (out_dir / "pilot_result.json").write_text(
        json.dumps(result, indent=2, default=str), encoding="utf-8"
    )
    (out_dir / "summary.md").write_text(_render_summary(result), encoding="utf-8")
    print(f"[done] wrote {out_dir / 'pilot_result.json'}", flush=True)
    return result


def _fmt(x: object) -> str:
    if x is None:
        return "n/a"
    if isinstance(x, float):
        return f"{x:.4f}"
    return str(x)


def _render_summary(result: Mapping[str, Any]) -> str:
    v = result["r1_verdict"]
    lin = result["step0_linear"]
    a = result["arm_A"]["eval"]
    b = result["arm_B"]["eval"]
    elig = result["eligibility"]
    lines = [
        "# Anchor-pair pilot summary (2026-07-10)",
        "",
        f"**R1 verdict: {v['verdict']}**",
        "",
        "## Eligibility",
        f"- anchors eligible: {elig['n_anchors_eligible']}/{elig['n_anchors_total']} "
        f"(ineligible share {elig['ineligible_share']:.3f})",
        f"- ineligible by terminal: {elig.get('ineligible_by_terminal', {})}",
        f"- transition rows (all splits): {result['transition']['n_transition_rows']}",
        "",
        "## Step 0 + arms (eligible report-half)",
        "",
        "| arm | overall decided-agr | coverage | unusable recall | TB n | TB agree | TB FP | TB FN |",
        "|---|--:|--:|--:|--:|--:|--:|--:|",
    ]
    for name, ev in (
        ("slice5_linear", lin),
        ("arm_B_cand_mlp", b),
        ("arm_A_pair_mlp", a),
    ):
        o = ev["overall_eligible_report"]
        t = ev["transition_band"]
        lines.append(
            f"| {name} | {_fmt(o['decided_agreement'])} | {_fmt(o['coverage'])} | "
            f"{_fmt(o['unusable_recall'])} | {t['n']} | {_fmt(t['decided_agreement'])} | "
            f"{t['fp']} | {t['fn']} |"
        )
    lines += [
        "",
        "## R1 conditions (A vs B)",
        "",
    ]
    for k, c in v["conditions"].items():
        lines.append(f"- `{k}`: **{'PASS' if c['pass'] else 'FAIL'}** — {c}")
    lines += [
        "",
        "## Area-bucket ride-along (eligible report-half, overall)",
        "",
        "| bucket | A agree | B agree | linear agree | A n |",
        "|---|--:|--:|--:|--:|",
    ]
    for bname, _, _ in AREA_BUCKETS:
        aa = a["by_area_bucket"][bname]
        bb = b["by_area_bucket"][bname]
        ll = lin["by_area_bucket"][bname]
        lines.append(
            f"| {bname} | {_fmt(aa['decided_agreement'])} | {_fmt(bb['decided_agreement'])} | "
            f"{_fmt(ll['decided_agreement'])} | {aa['n']} |"
        )
    lines += [
        "",
        f"Artifacts: see pilot_result.json next to this file.",
        f"Seed={result['seed']}; hidden={result['hidden']}; "
        f"params A={result['mlp_param_budget']['arm_A']} B={result['mlp_param_budget']['arm_B']}.",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--features", type=Path, default=FEATURES_DEFAULT)
    ap.add_argument("--linear-head-pt", type=Path, default=LINEAR_HEAD_PT)
    ap.add_argument("--linear-head-json", type=Path, default=LINEAR_HEAD_JSON)
    ap.add_argument("--out-dir", type=Path, default=OUT_DEFAULT)
    ap.add_argument("--coverage-floor", type=float, default=DEFAULT_COVERAGE_FLOOR)
    args = ap.parse_args()
    run_pilot(
        features_path=args.features,
        linear_head_pt=args.linear_head_pt,
        linear_head_json=args.linear_head_json,
        out_dir=args.out_dir,
        coverage_floor=args.coverage_floor,
    )


if __name__ == "__main__":
    main()
