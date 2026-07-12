#!/usr/bin/env python3
"""Minimal pilot: maj-unanimous Gemini labels vs single-rep labels for DINO head.

Uses the banked 3-rep reliability run (no new API):
  ~/zasolar_data/geid_temporal/llm_reliability_20260622/run/{long_rep1..3,review_png_manifest}

Arm A — train on maj-unanimous (3/3 agree present|absent)
Arm B — train on single-rep (rep1) labels on the SAME frame universe

Both evaluated on held-out anchors against maj-unanimous (primary) and rep1
(secondary). Backbone: DINOv2-S/14 frozen (cheap local floor; relative arm
comparison does not need L-SAT).

Pilot caveats (printed in report):
  - Reliability PNGs may carry review markers (same pixels for both arms).
  - No independent human gold — maj is a denoised Gemini proxy, not truth.
  - Universe restricted to frames with 3 usable present/absent votes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.temporal.geid_temporal_common import read_csv_rows, write_csv_rows
from scripts.temporal.train_dinov3_head import (
    CALIB_SALT,
    CLASS_INDEX,
    CLASS_ORDER,
    _split_heldout_halves,
    calibrate_band,
    run_evaluate,
    save_head_bundle,
    train_head,
)

REL_DEFAULT = Path.home() / "zasolar_data/geid_temporal/llm_reliability_20260622/run"
OUT_DEFAULT = Path.home() / "zasolar_data/geid_temporal/pilot_maj_unanimous_distill_20260710"

BACKBONE = "vit_small_patch14_dinov2.lvd142m"
INPUT_SIZE = 252  # 14 * 18
CENTER_POOL_K = 3
SEED = 20260710
TRAIN_FRAC = 0.70


def _norm_pv(v: object) -> str:
    s = str(v).strip().lower()
    if s in ("1", "true", "yes"):
        return "1"
    if s in ("0", "false", "no"):
        return "0"
    return ""


def _sha_bucket(s: str, salt: str) -> float:
    h = hashlib.sha256((salt + s).encode("utf-8")).hexdigest()
    return int(h[:12], 16) / float(16**12)


def build_frame_table(rel_dir: Path) -> list[dict[str, str]]:
    longs: list[dict[tuple[str, str], dict[str, str]]] = []
    for i in range(1, 4):
        m: dict[tuple[str, str], dict[str, str]] = {}
        for r in read_csv_rows(rel_dir / f"long_rep{i}.csv"):
            key = (str(r["anchor_id"]), str(r["capture_date"]))
            m[key] = {
                "pv": _norm_pv(r.get("pv_present", "")),
                "qf": str(r.get("quality_flag", "")),
                "chip_id": str(r.get("chip_id", "")),
                "target_label": str(r.get("target_label", "")),
                "actual_zoom": str(r.get("actual_zoom", "")),
                "grid_id": str(r.get("grid_id", "")),
            }
        longs.append(m)

    keys = sorted(set(longs[0]) & set(longs[1]) & set(longs[2]))
    png_by_key: dict[tuple[str, str], str] = {}
    zoom_by_key: dict[tuple[str, str], str] = {}
    for r in read_csv_rows(rel_dir / "review_png_manifest.csv"):
        key = (str(r["anchor_id"]), str(r["capture_date"]))
        png_by_key[key] = str(r.get("review_png_path", ""))
        zoom_by_key[key] = str(r.get("actual_zoom", ""))

    rows: list[dict[str, str]] = []
    for key in keys:
        pvs = [longs[i][key]["pv"] for i in range(3)]
        if any(p not in ("0", "1") for p in pvs):
            continue
        unan = len(set(pvs)) == 1
        c = Counter(pvs)
        top, n = c.most_common(1)[0]
        maj_ok = n >= 2 and (len(c) == 1 or n > c.most_common()[1][1])
        png = png_by_key.get(key, "")
        if not png or not Path(png).exists():
            continue
        meta = longs[0][key]
        rows.append(
            {
                "anchor_id": key[0],
                "capture_date": key[1],
                "version": "rel",  # dummy join version for FEATURE_STRING_COLS
                "chip_id": meta["chip_id"],
                "target_label": meta["target_label"],
                "grid_id": meta["grid_id"],
                "actual_zoom": zoom_by_key.get(key) or meta["actual_zoom"] or "",
                "png_path": png,
                "rep1_pv": pvs[0],
                "rep2_pv": pvs[1],
                "rep3_pv": pvs[2],
                "label_maj_unan": (
                    ("present" if pvs[0] == "1" else "absent") if unan else ""
                ),
                "label_maj": (
                    ("present" if top == "1" else "absent") if maj_ok else ""
                ),
                "label_rep1": "present" if pvs[0] == "1" else "absent",
                "is_unanimous": "1" if unan else "0",
                "sub_domain": "reliability_panel",
                "terminal_status": "reliability_panel",
            }
        )
    return rows


def assign_splits(rows: list[dict[str, str]], train_frac: float = TRAIN_FRAC) -> None:
    anchors = sorted({r["anchor_id"] for r in rows})
    train_set = {a for a in anchors if _sha_bucket(a, "maj_pilot_split_v1:") < train_frac}
    heldout = [a for a in anchors if a not in train_set]
    calib, report = _split_heldout_halves(heldout)
    # Override salt-split labels into split column expected by train_head:
    # train | heldout — calib/report are sub-halves of heldout via CALIB_SALT inside train_dinov3_head
    for r in rows:
        r["split"] = "train" if r["anchor_id"] in train_set else "heldout"
        r["heldout_half"] = (
            "calib"
            if r["anchor_id"] in calib
            else ("report" if r["anchor_id"] in report else "")
        )


def extract_features(
    rows: list[dict[str, str]],
    *,
    out_npz: Path,
    device: str,
    batch_size: int,
) -> dict[str, Any]:
    from scripts.temporal.dinov3_scorer import Dinov3PresenceScorer

    scorer = Dinov3PresenceScorer(
        backbone_model_id=BACKBONE,
        input_size=INPUT_SIZE,
        center_pool_k=CENTER_POOL_K,
        upscale_policy="bilinear",
        device=device,
    )
    paths = [r["png_path"] for r in rows]
    print(f"[extract] embedding {len(paths)} chips on {device} …", flush=True)
    feats = scorer.embed_chips(paths, batch_size=batch_size)
    assert feats.shape[0] == len(rows), (feats.shape, len(rows))
    npz: dict[str, Any] = {
        "features": feats.astype(np.float32),
        "anchor_id": np.array([r["anchor_id"] for r in rows]),
        "capture_date": np.array([r["capture_date"] for r in rows]),
        "version": np.array([r["version"] for r in rows]),
        "split": np.array([r["split"] for r in rows]),
        "sub_domain": np.array([r["sub_domain"] for r in rows]),
        "actual_zoom": np.array([r["actual_zoom"] for r in rows]),
        "terminal_status": np.array([r["terminal_status"] for r in rows]),
        "png_path": np.array([r["png_path"] for r in rows]),
        # labels filled per-arm when training
        "label_3class": np.array([""] * len(rows)),
        "label_maj_unan": np.array([r["label_maj_unan"] for r in rows]),
        "label_rep1": np.array([r["label_rep1"] for r in rows]),
        "is_unanimous": np.array([r["is_unanimous"] for r in rows]),
    }
    out_npz.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(out_npz, **npz)
    meta = {
        "n": len(rows),
        "embed_dim": int(feats.shape[1]),
        "backbone": BACKBONE,
        "input_size": INPUT_SIZE,
        "center_pool_k": CENTER_POOL_K,
        "n_train_rows": int(sum(1 for r in rows if r["split"] == "train")),
        "n_heldout_rows": int(sum(1 for r in rows if r["split"] == "heldout")),
        "n_train_anchors": len({r["anchor_id"] for r in rows if r["split"] == "train"}),
        "n_heldout_anchors": len({r["anchor_id"] for r in rows if r["split"] == "heldout"}),
        "label_maj_unan_counts": dict(Counter(r["label_maj_unan"] for r in rows if r["label_maj_unan"])),
        "label_rep1_counts": dict(Counter(r["label_rep1"] for r in rows)),
        "n_unanimous": int(sum(1 for r in rows if r["is_unanimous"] == "1")),
    }
    (out_npz.parent / "features_meta.json").write_text(
        json.dumps(meta, indent=2), encoding="utf-8"
    )
    print(f"[extract] wrote {out_npz} dim={feats.shape[1]} meta={meta}", flush=True)
    return npz


def _arm_npz(base: dict[str, Any], label_key: str, *, unan_only: bool) -> dict[str, Any]:
    """Copy npz with label_3class set; optionally keep only unanimous frames."""
    mask = np.ones(len(base["anchor_id"]), dtype=bool)
    if unan_only:
        mask &= base["is_unanimous"].astype(str) == "1"
    labels = base[label_key].astype(str)
    mask &= np.isin(labels, np.array(["present", "absent"]))
    out = {k: (v[mask] if isinstance(v, np.ndarray) and len(v) == len(mask) else v) for k, v in base.items()}
    out["label_3class"] = labels[mask]
    return out


def train_and_eval_arm(
    base_npz: dict[str, Any],
    *,
    arm_name: str,
    label_key: str,
    unan_only: bool,
    out_dir: Path,
    coverage_floor: float = 0.85,
) -> dict[str, Any]:
    arm_dir = out_dir / arm_name
    arm_dir.mkdir(parents=True, exist_ok=True)
    npz = _arm_npz(base_npz, label_key, unan_only=unan_only)
    n = len(npz["label_3class"])
    print(
        f"[{arm_name}] rows={n} labels={dict(Counter(npz['label_3class'].tolist()))} "
        f"train_anchors={len(set(npz['anchor_id'][npz['split']=='train']))}",
        flush=True,
    )
    # persist arm features for evaluate CLI compatibility
    feat_path = arm_dir / "features.npz"
    np.savez_compressed(feat_path, **{k: v for k, v in npz.items() if k in {
        "features", "anchor_id", "capture_date", "version", "label_3class", "split",
        "sub_domain", "actual_zoom", "terminal_status", "png_path",
    }})

    head_path = arm_dir / "head.pt"
    log = train_head(
        npz,
        out_path=head_path,
        seed=SEED,
        config={
            "backbone_model_id": BACKBONE,
            "input_size": INPUT_SIZE,
            "center_pool_k": CENTER_POOL_K,
            "upscale_policy": "bilinear",
            "patch_size": 14,
            "chip_render_variant": "reliability_review_png_pilot",
            "arm": arm_name,
            "label_key": label_key,
        },
        provenance={"pilot": "maj_unanimous_distill", "seed": SEED},
    )
    print(f"[{arm_name}] train done val_macro_f1={log.get('best_val_macro_f1')}", flush=True)

    cal, frontier = calibrate_band(npz, head_path=head_path, coverage_floor=coverage_floor)
    # calibrate_band expects loaded head internally via path — check signature
    print(f"[{arm_name}] calib={cal}", flush=True)

    metrics = run_evaluate(feat_path, head_path, arm_dir / "eval_vs_train_teacher")
    return {
        "arm": arm_name,
        "label_key": label_key,
        "n_rows": n,
        "train_log": log,
        "calibration": cal,
        "eval_vs_own_teacher": metrics.get("overall", metrics),
        "head_path": str(head_path),
        "features_path": str(feat_path),
    }


def evaluate_cross(
    head_path: Path,
    eval_npz: dict[str, Any],
    *,
    out_dir: Path,
    tag: str,
) -> dict[str, Any]:
    """Evaluate a trained head against an alternate label column (via label_3class)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    feat_path = out_dir / "features.npz"
    keep = {
        "features", "anchor_id", "capture_date", "version", "label_3class", "split",
        "sub_domain", "actual_zoom", "terminal_status", "png_path",
    }
    np.savez_compressed(feat_path, **{k: eval_npz[k] for k in keep})
    return run_evaluate(feat_path, head_path, out_dir)


def _patch_calibrate_band():
    """calibrate_band in train_dinov3_head takes (npz, head_path=...) — verify."""
    import inspect
    from scripts.temporal import train_dinov3_head as t

    sig = inspect.signature(t.calibrate_band)
    return list(sig.parameters)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--rel-dir", type=Path, default=REL_DEFAULT)
    ap.add_argument("--out-dir", type=Path, default=OUT_DEFAULT)
    ap.add_argument("--device", choices=("cuda", "cpu"), default="cuda")
    ap.add_argument("--batch-size", type=int, default=32)
    ap.add_argument("--skip-extract", action="store_true")
    ap.add_argument("--coverage-floor", type=float, default=0.85)
    args = ap.parse_args()

    out_dir = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    print("[1] build frame table", flush=True)
    rows = build_frame_table(args.rel_dir)
    # Primary universe: unanimous frames only (clean teacher target)
    unan_rows = [r for r in rows if r["is_unanimous"] == "1"]
    assign_splits(unan_rows)
    if unan_rows:
        write_csv_rows(
            out_dir / "frame_table_unanimous.csv",
            unan_rows,
            list(unan_rows[0].keys()),
        )
    if rows:
        write_csv_rows(
            out_dir / "frame_table_all_majable.csv",
            rows,
            list(rows[0].keys()),
        )
    stats = {
        "n_frames_3rep_pa": len(rows),
        "n_unanimous": len(unan_rows),
        "n_anchors_unan": len({r["anchor_id"] for r in unan_rows}),
        "maj_unan_labels": dict(Counter(r["label_maj_unan"] for r in unan_rows)),
        "rep1_on_unan": dict(Counter(r["label_rep1"] for r in unan_rows)),
        "rep1_vs_maj_agree_on_unan": sum(
            1 for r in unan_rows if r["label_rep1"] == r["label_maj_unan"]
        )
        / max(1, len(unan_rows)),
    }
    print(json.dumps(stats, indent=2), flush=True)
    (out_dir / "label_stats.json").write_text(json.dumps(stats, indent=2), encoding="utf-8")

    feat_path = out_dir / "features_shared.npz"
    if args.skip_extract and feat_path.exists():
        print(f"[2] load features {feat_path}", flush=True)
        raw = np.load(feat_path, allow_pickle=False)
        base = {k: raw[k] for k in raw.files}
    else:
        print("[2] extract features", flush=True)
        base = extract_features(
            unan_rows, out_npz=feat_path, device=args.device, batch_size=args.batch_size
        )

    # --- Arm A: maj-unanimous teacher ---
    print("[3] arm A train (maj_unanimous)", flush=True)
    npz_a = _arm_npz(base, "label_maj_unan", unan_only=True)
    arm_a = out_dir / "arm_A_maj_unan"
    arm_a.mkdir(parents=True, exist_ok=True)
    feat_a = arm_a / "features.npz"
    np.savez_compressed(
        feat_a,
        **{
            k: npz_a[k]
            for k in (
                "features",
                "anchor_id",
                "capture_date",
                "version",
                "label_3class",
                "split",
                "sub_domain",
                "actual_zoom",
                "terminal_status",
                "png_path",
            )
        },
    )
    head_a = arm_a / "head.pt"
    log_a = train_head(
        npz_a,
        out_path=head_a,
        seed=SEED,
        config={
            "backbone_model_id": BACKBONE,
            "input_size": INPUT_SIZE,
            "center_pool_k": CENTER_POOL_K,
            "upscale_policy": "bilinear",
            "patch_size": 14,
            "chip_render_variant": "reliability_review_png_pilot",
            "arm": "A_maj_unan",
        },
        provenance={"pilot": "maj_unanimous_distill", "arm": "A"},
    )
    # calibrate_band API: (npz, *, head_path or load inside run_calibrate)
    from scripts.temporal.train_dinov3_head import run_calibrate

    cal_a = run_calibrate(feat_a, head_a, coverage_floor=args.coverage_floor)
    print(f"[3] arm A calib {cal_a.get('calibration', cal_a)}", flush=True)
    ev_a_own = run_evaluate(feat_a, head_a, arm_a / "eval_vs_maj")

    # --- Arm B: rep1 teacher on same universe ---
    print("[4] arm B train (rep1)", flush=True)
    npz_b = _arm_npz(base, "label_rep1", unan_only=True)
    # On unanimous frames, rep1 == maj by construction! That would make arms identical.
    # For a meaningful noisy-teacher arm we need frames where we still eval vs maj
    # but train on rep1 including NON-unanimous frames.
    # Rebuild: train universe = all frames with 3 PA votes; labels rep1; eval vs maj on unan heldout.
    print("[4b] rebuild arm B on ALL 3-rep PA frames (noisy train universe)", flush=True)
    all_rows = [r for r in rows if r["label_rep1"] in ("present", "absent")]
    # Keep split assignment consistent with unan split for shared heldout anchors where possible
    anchor_split = {r["anchor_id"]: r["split"] for r in unan_rows}
    for r in all_rows:
        if r["anchor_id"] in anchor_split:
            r["split"] = anchor_split[r["anchor_id"]]
        else:
            r["split"] = "train" if _sha_bucket(r["anchor_id"], "maj_pilot_split_v1:") < TRAIN_FRAC else "heldout"
    # extract features for all_rows if needed
    feat_all_path = out_dir / "features_all_pa.npz"
    if args.skip_extract and feat_all_path.exists():
        raw_all = np.load(feat_all_path, allow_pickle=False)
        base_all = {k: raw_all[k] for k in raw_all.files}
    else:
        # merge: for unan rows reuse embeddings by png path index
        print("[4b] extract features for full PA universe", flush=True)
        base_all = extract_features(
            all_rows, out_npz=feat_all_path, device=args.device, batch_size=args.batch_size
        )
        # extract_features overwrote label fields from all_rows — ensure rep1/maj present
        # rebuild label arrays from all_rows order
        base_all["label_rep1"] = np.array([r["label_rep1"] for r in all_rows])
        base_all["label_maj_unan"] = np.array([r["label_maj_unan"] for r in all_rows])
        base_all["is_unanimous"] = np.array([r["is_unanimous"] for r in all_rows])
        base_all["split"] = np.array([r["split"] for r in all_rows])
        np.savez_compressed(feat_all_path, **base_all)

    npz_b = _arm_npz(base_all, "label_rep1", unan_only=False)
    arm_b = out_dir / "arm_B_rep1"
    arm_b.mkdir(parents=True, exist_ok=True)
    feat_b = arm_b / "features.npz"
    np.savez_compressed(
        feat_b,
        **{
            k: npz_b[k]
            for k in (
                "features",
                "anchor_id",
                "capture_date",
                "version",
                "label_3class",
                "split",
                "sub_domain",
                "actual_zoom",
                "terminal_status",
                "png_path",
            )
        },
    )
    head_b = arm_b / "head.pt"
    log_b = train_head(
        npz_b,
        out_path=head_b,
        seed=SEED,
        config={
            "backbone_model_id": BACKBONE,
            "input_size": INPUT_SIZE,
            "center_pool_k": CENTER_POOL_K,
            "upscale_policy": "bilinear",
            "patch_size": 14,
            "chip_render_variant": "reliability_review_png_pilot",
            "arm": "B_rep1",
        },
        provenance={"pilot": "maj_unanimous_distill", "arm": "B"},
    )
    cal_b = run_calibrate(feat_b, head_b, coverage_floor=args.coverage_floor)
    print(f"[4] arm B calib {cal_b.get('calibration', cal_b)}", flush=True)
    ev_b_own = run_evaluate(feat_b, head_b, arm_b / "eval_vs_rep1")

    # Cross-eval both heads on unanimous held-out vs maj labels (primary)
    print("[5] cross-eval both heads vs maj-unanimous", flush=True)
    cross_a = evaluate_cross(head_a, npz_a, out_dir=arm_a / "cross_vs_maj", tag="A")
    # For B, evaluate on unan-only maj labels (heldout in npz_a universe) using B's head
    # Build eval npz: unan frames with maj labels, features from base (unan)
    cross_b = evaluate_cross(head_b, npz_a, out_dir=arm_b / "cross_vs_maj", tag="B")

    def _overall(m: Any) -> dict[str, Any]:
        if not isinstance(m, dict):
            return {}
        return m.get("overall", m)

    summary = {
        "stats": stats,
        "arm_A_maj_train": {
            "n": int(len(npz_a["label_3class"])),
            "val_macro_f1": log_a.get("best_val_macro_f1"),
            "calibration": cal_a.get("calibration", cal_a),
            "eval_vs_maj": _overall(ev_a_own),
            "cross_vs_maj": _overall(cross_a),
        },
        "arm_B_rep1_train": {
            "n": int(len(npz_b["label_3class"])),
            "val_macro_f1": log_b.get("best_val_macro_f1"),
            "calibration": cal_b.get("calibration", cal_b),
            "eval_vs_rep1": _overall(ev_b_own),
            "cross_vs_maj": _overall(cross_b),
        },
        "primary_comparison": {
            "metric": "held-out report-half decided-agreement vs maj-unanimous labels",
            "arm_A": _overall(cross_a),
            "arm_B": _overall(cross_b),
        },
        "caveats": [
            "maj-unanimous is denoised Gemini, not human gold",
            "review PNGs may include markers (shared by both arms)",
            "DINOv2-S floor backbone — relative arm comparison only",
            "Arm B trains on full 3-rep PA universe (includes 2-1 frames); Arm A on unanimous only",
        ],
    }
    (out_dir / "SUMMARY.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")

    # Markdown report
    oa = _overall(cross_a)
    ob = _overall(cross_b)
    lines = [
        "# Pilot: maj-unanimous vs single-rep teacher for DINO head",
        "",
        f"- Universe (unanimous PA frames): **{stats['n_unanimous']}** frames / **{stats['n_anchors_unan']}** anchors",
        f"- Full 3-rep PA frames: **{stats['n_frames_3rep_pa']}**",
        f"- Backbone: `{BACKBONE}` input={INPUT_SIZE} k={CENTER_POOL_K}",
        f"- Seed: {SEED}",
        "",
        "## Label stats",
        f"- maj-unanimous counts: `{stats['maj_unan_labels']}`",
        f"- rep1 vs maj agree on unanimous universe: **{stats['rep1_vs_maj_agree_on_unan']:.4f}** (should be ~1.0)",
        "",
        "## Primary: held-out decided-agreement **vs maj-unanimous**",
        "",
        "| Arm | Train teacher | n_train_universe | coverage | decided-agreement |",
        "|-----|---------------|------------------|----------|-------------------|",
        f"| **A** | maj-unanimous | {len(npz_a['label_3class'])} | {_fmt(oa.get('coverage'))} | **{_fmt(oa.get('agreement') or oa.get('decided_agreement'))}** |",
        f"| **B** | rep1 (all PA) | {len(npz_b['label_3class'])} | {_fmt(ob.get('coverage'))} | **{_fmt(ob.get('agreement') or ob.get('decided_agreement'))}** |",
        "",
        "## Own-teacher eval (sanity)",
        f"- A vs maj (own): `{_overall(ev_a_own)}`",
        f"- B vs rep1 (own): `{_overall(ev_b_own)}`",
        "",
        "## Caveats",
        *[f"- {c}" for c in summary["caveats"]],
        "",
        f"Artifacts: `{out_dir}`",
        "",
    ]
    (out_dir / "SUMMARY.md").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines), flush=True)
    print(f"[done] {out_dir / 'SUMMARY.md'}", flush=True)


def _fmt(x: Any) -> str:
    if x is None:
        return "n/a"
    try:
        return f"{float(x):.4f}"
    except (TypeError, ValueError):
        return str(x)


if __name__ == "__main__":
    main()
