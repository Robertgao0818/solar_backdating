#!/usr/bin/env python3
"""Anchor-pair A″ pilot — same-sensor present template (pre-reg 2026-07-10).

Implements ``docs/dinov3_scorer/DATA-anchor-pair-a2-same-sensor-prereg-2026-07-10.md``:

* H3 same-sensor ranking smoke (fail-closed before head train)
* Arm P: GEHI cand × latest teacher-``present`` GEHI (bet; self-pair banned)
* Arm N/E: nearest / earliest teacher-absent ablations
* Arm B: parameter-matched single-frame control
* Patch-token grids (pool last); rate-normalized multi-seed R3

Zero API, zero new GT, frozen backbone, **no Vexcel**. Local GPU.
"""

from __future__ import annotations

import argparse
import gc
import json
import os
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.temporal.train_dinov3_head import (  # noqa: E402
    DEFAULT_COVERAGE_FLOOR,
    _split_heldout_halves,
)
from scripts.validation.check_student_path_gate import (  # noqa: E402
    check_anchor_pair_a2_r1,
)
from scripts.validation.pilot_anchor_pair_2026_07_10 import (  # noqa: E402
    report_half_mask,
    transition_band_dates,
)
from scripts.validation.pilot_anchor_pair_v2_2026_07_10 import (  # noqa: E402
    GRID_KEEP,
    HEAD_WIDTH_V,
    N_BOOT,
    SEEDS,
    _build_head,
    bootstrap_reduction_ci,
    calibrate_probs,
    count_params,
    decisions_from_probs,
    eval_arm_on_report,
    extract_patch_cache,
    global_pool,
    load_grids,
    load_head,
    nearest_absent_before,
    pack_pair,
    pack_single,
    predict_probs,
    rate_metrics,
    train_patch_head,
    width_for_param_match,
)

FEATURES_DEFAULT = Path.home() / (
    "zasolar_data/slice5/out/nomarker_bilinear518_k6/features.npz"
)
# Prefer reusing A′ GEHI grids when present (same backbone / preprocess).
A1_CAND_GRIDS = Path.home() / (
    "zasolar_data/geid_temporal/pilot_anchor_pair_v2_20260710/patch_tokens/cand_grids.npy"
)
OUT_DEFAULT = Path.home() / "zasolar_data/geid_temporal/pilot_anchor_pair_a2_20260710"
SMOKE_RNG_SEED = 20260710
HEAD_WIDTH_P = HEAD_WIDTH_V  # first successful config lock (same as A′)


# --------------------------------------------------------------------------- #
# Metadata / eligibility
# --------------------------------------------------------------------------- #
def build_row_table(npz: Mapping[str, np.ndarray]) -> list[dict[str, Any]]:
    n = len(npz["anchor_id"])
    has_version = "version" in npz
    rows = []
    for i in range(n):
        rows.append(
            {
                "i": i,
                "anchor_id": str(npz["anchor_id"][i]),
                "capture_date": str(npz["capture_date"][i]),
                "version": str(npz["version"][i]) if has_version else "",
                "label": str(npz["label_3class"][i]),
                "split": str(npz["split"][i]),
                "png_path": str(npz["png_path"][i]),
                "terminal_status": str(npz["terminal_status"][i]),
            }
        )
    return rows


def pair_eligibility(
    rows: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Any], set[str]]:
    by_a: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for r in rows:
        by_a[r["anchor_id"]].append(r)

    pair_ok: set[str] = set()
    for a, rs in by_a.items():
        labs = {r["label"] for r in rs}
        if "absent" in labs and "present" in labs:
            pair_ok.add(a)

    n_self_ban = 0
    n_p_rows = 0
    for r in rows:
        if r["anchor_id"] not in pair_ok:
            continue
        ref = latest_present_key(by_a[r["anchor_id"]])
        if ref is None:
            continue
        if frame_key(r) == ref:
            n_self_ban += 1
            continue
        n_p_rows += 1

    meta = {
        "n_anchors": len(by_a),
        "n_pair_eligible": len(pair_ok),
        "pair_share": len(pair_ok) / max(1, len(by_a)),
        "n_rows_pair": sum(1 for r in rows if r["anchor_id"] in pair_ok),
        "n_arm_p_scorable_rows": n_p_rows,
        "n_arm_p_self_pair_excluded": n_self_ban,
    }
    return meta, pair_ok


def frame_key(r: Mapping[str, Any]) -> tuple[str, str]:
    return (str(r["capture_date"]), str(r.get("version", "")))


def sort_frame_key(k: tuple[str, str]) -> tuple[str, str]:
    return k  # ISO dates + version string sort lexicographically OK here


def latest_present_key(rows_a: Sequence[Mapping[str, Any]]) -> tuple[str, str] | None:
    presents = [frame_key(r) for r in rows_a if r["label"] == "present"]
    if not presents:
        return None
    return max(presents, key=sort_frame_key)


def second_latest_present_key(
    rows_a: Sequence[Mapping[str, Any]],
) -> tuple[str, str] | None:
    presents = sorted(
        {frame_key(r) for r in rows_a if r["label"] == "present"},
        key=sort_frame_key,
    )
    if len(presents) < 2:
        return None
    return presents[-2]


def earliest_absent_key(rows_a: Sequence[Mapping[str, Any]]) -> tuple[str, str] | None:
    absents = [frame_key(r) for r in rows_a if r["label"] == "absent"]
    if not absents:
        return None
    return min(absents, key=sort_frame_key)


def index_by_frame_key(
    rows_a: Sequence[Mapping[str, Any]],
) -> dict[tuple[str, str], int]:
    """Map (date, version) → row index; last write wins if duplicates."""
    out: dict[tuple[str, str], int] = {}
    for r in rows_a:
        out[frame_key(r)] = int(r["i"])
    return out


def absent_date_list(rows_a: Sequence[Mapping[str, Any]]) -> list[str]:
    return sorted({r["capture_date"] for r in rows_a if r["label"] == "absent"})


# --------------------------------------------------------------------------- #
# Same-sensor smoke (H3)
# --------------------------------------------------------------------------- #
def run_same_sensor_smoke(
    *,
    rows: Sequence[Mapping[str, Any]],
    pair_ok: set[str],
    cand_grids: np.ndarray,
    out_path: Path,
    rng_seed: int = SMOKE_RNG_SEED,
) -> dict[str, Any]:
    """H3: median s_pos > s_abs AND median s_pos > s_neg (same-sensor)."""
    by_a: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for r in rows:
        if r["anchor_id"] in pair_ok:
            by_a[r["anchor_id"]].append(r)

    anchors = sorted(by_a)
    held = [a for a in anchors if any(r["split"] == "heldout" for r in by_a[a])]
    _, report = _split_heldout_halves(held) if held else (set(), set())
    smoke_anchors = sorted(report) if report else anchors

    # present_ref index per anchor (for s_neg)
    ref_i: dict[str, int] = {}
    for a in smoke_anchors:
        pk = latest_present_key(by_a[a])
        if pk is None:
            continue
        idx_map = index_by_frame_key(by_a[a])
        if pk in idx_map:
            ref_i[a] = idx_map[pk]

    s_pos: list[float] = []
    s_abs: list[float] = []
    s_neg: list[float] = []
    n_skipped_one_present = 0
    n_skipped_missing = 0
    rng = np.random.default_rng(rng_seed)
    other_ids = [a for a in smoke_anchors if a in ref_i]

    for a in smoke_anchors:
        if a not in ref_i:
            n_skipped_missing += 1
            continue
        rs = by_a[a]
        idx_map = index_by_frame_key(rs)
        pref = latest_present_key(rs)
        sp = second_latest_present_key(rs)
        ea = earliest_absent_key(rs)
        if pref is None or ea is None:
            n_skipped_missing += 1
            continue
        if sp is None:
            n_skipped_one_present += 1
            continue
        i_ref = idx_map.get(pref)
        i_pos = idx_map.get(sp)
        i_abs = idx_map.get(ea)
        if i_ref is None or i_pos is None or i_abs is None:
            n_skipped_missing += 1
            continue

        pref_vec = global_pool(cand_grids[i_ref])
        s_pos.append(float(np.dot(global_pool(cand_grids[i_pos]), pref_vec)))
        s_abs.append(float(np.dot(global_pool(cand_grids[i_abs]), pref_vec)))

        others = [o for o in other_ids if o != a]
        if others:
            o = others[int(rng.integers(0, len(others)))]
            s_neg.append(float(np.dot(global_pool(cand_grids[ref_i[o]]), pref_vec)))

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
        "n_skipped_one_present": n_skipped_one_present,
        "n_skipped_missing": n_skipped_missing,
        "median_cos_nonref_present_ref": m_pos,
        "median_cos_early_absent_ref": m_abs,
        "median_cos_other_present_ref": m_neg,
        "mean_cos_nonref_present_ref": float(np.mean(s_pos)) if s_pos else None,
        "mean_cos_early_absent_ref": float(np.mean(s_abs)) if s_abs else None,
        "mean_cos_other_present_ref": float(np.mean(s_neg)) if s_neg else None,
        "rule": "median s_pos > s_abs AND median s_pos > s_neg",
        "rng_seed": rng_seed,
    }
    out_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2), flush=True)
    return result


# --------------------------------------------------------------------------- #
# Arm datasets
# --------------------------------------------------------------------------- #
def build_arm_index(
    rows: Sequence[Mapping[str, Any]],
    *,
    arm: str,
    pair_ok: set[str],
    cand_grids: np.ndarray,
) -> dict[str, Any]:
    """Materialize centre-cropped tensors for one arm (RAM-safe float16)."""
    by_a: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for r in rows:
        by_a[r["anchor_id"]].append(r)

    abs_dates: dict[str, list[str]] = {}
    date_to_i: dict[str, dict[str, int]] = {}
    frame_to_i: dict[str, dict[tuple[str, str], int]] = {}
    present_ref: dict[str, tuple[str, str] | None] = {}
    for a, rs in by_a.items():
        abs_dates[a] = absent_date_list(rs)
        date_to_i[a] = {}
        for r in rs:
            date_to_i[a].setdefault(r["capture_date"], int(r["i"]))
        frame_to_i[a] = index_by_frame_key(rs)
        present_ref[a] = latest_present_key(rs)

    labels: list[str] = []
    anchors: list[str] = []
    splits: list[str] = []
    is_tb: list[bool] = []
    packs: list[np.ndarray] = []
    embed_dim = int(cand_grids.shape[-1])

    for r in rows:
        a = r["anchor_id"]
        if a not in pair_ok:
            continue
        cand_i = int(r["i"])
        cand_date = r["capture_date"]
        cand_fk = frame_key(r)

        if arm == "P":
            pref = present_ref[a]
            if pref is None or cand_fk == pref:
                continue  # self-pair ban
            ref_i = frame_to_i[a].get(pref)
            if ref_i is None:
                continue
            x = pack_pair(cand_grids[cand_i], cand_grids[ref_i])
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


# --------------------------------------------------------------------------- #
# Ensure GEHI patch cache
# --------------------------------------------------------------------------- #
def ensure_cand_grids(
    *,
    rows: Sequence[Mapping[str, Any]],
    patch_dir: Path,
    reuse_path: Path | None,
    device: str,
    batch_size: int,
    skip_extract: bool,
) -> Path:
    cand_path = patch_dir / "cand_grids.npy"
    cand_meta = patch_dir / "cand_grids.meta.json"
    n_rows = len(rows)

    if cand_path.exists() and skip_extract:
        print(f"[extract] skip — using {cand_path}", flush=True)
        return cand_path

    if cand_path.exists():
        try:
            g = load_grids(cand_path)
            if g.shape[0] == n_rows:
                print(f"[extract] existing cache ok n={n_rows}", flush=True)
                return cand_path
        except Exception as exc:  # noqa: BLE001
            print(f"[extract] existing cache unreadable ({exc}); rebuild", flush=True)

    # Symlink / copy reuse from A′ when row count matches
    if reuse_path and Path(reuse_path).exists():
        try:
            g = load_grids(Path(reuse_path))
            if g.shape[0] == n_rows:
                cand_path.parent.mkdir(parents=True, exist_ok=True)
                if cand_path.exists() or cand_path.is_symlink():
                    cand_path.unlink()
                os.symlink(Path(reuse_path).resolve(), cand_path)
                prov = {
                    "source": str(Path(reuse_path).resolve()),
                    "n": n_rows,
                    "shape": list(g.shape),
                    "note": "reused A′ GEHI cand_grids (same distill order)",
                }
                cand_meta.write_text(json.dumps(prov, indent=2), encoding="utf-8")
                print(f"[extract] symlinked A′ cand grids → {cand_path}", flush=True)
                return cand_path
            print(
                f"[extract] reuse shape[0]={g.shape[0]} != n_rows={n_rows}; re-extract",
                flush=True,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"[extract] reuse failed ({exc}); re-extract", flush=True)

    cand_paths = [r["png_path"] for r in rows]
    extract_patch_cache(
        cand_paths,
        cand_path,
        device=device,
        batch_size=batch_size,
        meta_path=cand_meta,
    )
    return cand_path


# --------------------------------------------------------------------------- #
# Main
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
    elig_meta, pair_ok = pair_eligibility(rows)
    print("[eligibility]", json.dumps(elig_meta, indent=2), flush=True)
    (out / "eligibility.json").write_text(
        json.dumps({"meta": elig_meta}, indent=2), encoding="utf-8"
    )

    cand_path = ensure_cand_grids(
        rows=rows,
        patch_dir=patch_dir,
        reuse_path=Path(args.reuse_cand_grids) if args.reuse_cand_grids else None,
        device=args.device,
        batch_size=args.batch_size,
        skip_extract=args.skip_extract,
    )
    cand_grids = load_grids(cand_path)
    embed_dim = int(cand_grids.shape[-1])
    print(f"[cache] cand={cand_grids.shape} dim={embed_dim}", flush=True)
    if cand_grids.shape[0] != len(rows):
        raise SystemExit(
            f"cand_grids n={cand_grids.shape[0]} != feature rows {len(rows)}"
        )

    print("[H3] same-sensor ranking smoke", flush=True)
    smoke = run_same_sensor_smoke(
        rows=rows,
        pair_ok=pair_ok,
        cand_grids=cand_grids,
        out_path=out / "smoke_same_sensor.json",
        rng_seed=SMOKE_RNG_SEED,
    )
    if smoke["verdict"] != "SMOKE_GO":
        result = {
            "status": "STOPPED_SMOKE_KILL",
            "prereg": "DATA-anchor-pair-a2-same-sensor-prereg-2026-07-10",
            "smoke": smoke,
            "eligibility": elig_meta,
            "r3_verdict": None,
        }
        (out / "pilot_result.json").write_text(
            json.dumps(result, indent=2), encoding="utf-8"
        )
        (out / "summary.md").write_text(
            "# Pairing-A″ STOPPED — same-sensor smoke KILL\n\n"
            "See smoke_same_sensor.json. Do not interpret as pairing failure.\n\n"
            f"```json\n{json.dumps(smoke, indent=2)}\n```\n",
            encoding="utf-8",
        )
        print("[stop] SMOKE_KILL — no head training", flush=True)
        return result

    if args.smoke_only:
        result = {
            "status": "SMOKE_ONLY_GO",
            "prereg": "DATA-anchor-pair-a2-same-sensor-prereg-2026-07-10",
            "smoke": smoke,
            "eligibility": elig_meta,
        }
        (out / "pilot_result.json").write_text(
            json.dumps(result, indent=2), encoding="utf-8"
        )
        (out / "summary.md").write_text(
            "# Pairing-A″ smoke GO (smoke-only run)\n\n"
            f"```json\n{json.dumps(smoke, indent=2)}\n```\n",
            encoding="utf-8",
        )
        print("[done] smoke-only GO", flush=True)
        return result

    probe = _build_head(embed_dim * 3, HEAD_WIDTH_P)
    n_p = count_params(probe)
    width_b = width_for_param_match(n_p, embed_dim)
    n_b = count_params(_build_head(embed_dim, width_b))
    print(
        f"[params] P width={HEAD_WIDTH_P} n={n_p}; B width={width_b} n={n_b} "
        f"ratio={n_p / max(1, n_b):.3f}",
        flush=True,
    )

    seed_evals: dict[str, list[dict[str, Any]]] = {
        "P": [],
        "B": [],
        "N": [],
        "E": [],
    }
    seed_logs: dict[str, list[dict[str, Any]]] = {
        "P": [],
        "B": [],
        "N": [],
        "E": [],
    }

    arm_specs = (
        ("P", True, HEAD_WIDTH_P),
        ("B", False, width_b),
        ("N", True, HEAD_WIDTH_P),
        ("E", True, HEAD_WIDTH_P),
    )
    for arm, pair, width in arm_specs:
        print(f"[data] build arm {arm} (centre {GRID_KEEP}×{GRID_KEEP})", flush=True)
        data = build_arm_index(
            rows, arm=arm, pair_ok=pair_ok, cand_grids=cand_grids
        )
        print(
            f"  [{arm}] n={len(data['labels'])} "
            f"x={data['x'].nbytes / 1e9:.2f}GB shape={data['x'].shape}",
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
                device=args.device,
            )
            model = load_head(head_pt)
            probs = predict_probs(model, data["x"], device=args.device)
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
        gc.collect()

    def pool_rate(arm: str, key: str, stratum: str = "transition_band") -> float | None:
        vals = [
            e[stratum][key] for e in seed_evals[arm] if e[stratum][key] is not None
        ]
        return float(np.mean(vals)) if vals else None

    def pool_den(arm: str, key: str) -> int:
        vals = [e["transition_band"][key] for e in seed_evals[arm]]
        return int(round(float(np.mean(vals)))) if vals else 0

    fp_p = pool_rate("P", "fp_rate")
    fp_b = pool_rate("B", "fp_rate")
    fn_p = pool_rate("P", "fn_rate")
    fn_b = pool_rate("B", "fn_rate")
    fp_red, fp_ci = bootstrap_reduction_ci(
        fp_p,
        fp_b,
        pool_den("P", "n_absent_decided"),
        pool_den("B", "n_absent_decided"),
        n_boot=N_BOOT,
        seed=0,
    )
    fn_red, fn_ci = bootstrap_reduction_ci(
        fn_p,
        fn_b,
        pool_den("P", "n_present_decided"),
        pool_den("B", "n_present_decided"),
        n_boot=N_BOOT,
        seed=0,
    )

    metrics_flat = {
        "transition_fp_rate_reduction_pct": (fp_red * 100.0) if fp_red is not None else -1.0,
        "transition_fp_rate_reduction_ci_low_pct": (fp_ci * 100.0) if fp_ci is not None else -1.0,
        "transition_fn_rate_reduction_pct": (fn_red * 100.0) if fn_red is not None else -1.0,
        "transition_fn_rate_reduction_ci_low_pct": (fn_ci * 100.0) if fn_ci is not None else -1.0,
        "overall_decided_agreement_p": pool_rate("P", "decided_agreement", "overall") or 0.0,
        "overall_decided_agreement_b": pool_rate("B", "decided_agreement", "overall") or 0.0,
        "present_to_unusable_rate_p": pool_rate("P", "present_to_unusable_rate") or 0.0,
        "present_to_unusable_rate_b": pool_rate("B", "present_to_unusable_rate") or 0.0,
        "unusable_recall_p": pool_rate("P", "unusable_recall", "overall") or 0.0,
        "unusable_recall_b": pool_rate("B", "unusable_recall", "overall") or 0.0,
        "arm_p_params": float(n_p),
        "arm_b_params": float(n_b),
        "n_seeds": float(len(SEEDS)),
    }
    ok, lines = check_anchor_pair_a2_r1(metrics_flat)
    verdict = {
        "verdict": "GO" if ok else "KILL",
        "checker_lines": lines,
        "metrics_flat": metrics_flat,
        "pooled": {
            "fp_rate_p": fp_p,
            "fp_rate_b": fp_b,
            "fn_rate_p": fn_p,
            "fn_rate_b": fn_b,
            "fp_reduction": fp_red,
            "fp_reduction_ci_low": fp_ci,
            "fn_reduction": fn_red,
            "fn_reduction_ci_low": fn_ci,
        },
    }
    print("[R3]", verdict["verdict"], flush=True)
    for ln in lines:
        print(" ", ln, flush=True)

    h2 = {
        "fp_rate_P": fp_p,
        "fp_rate_N": pool_rate("N", "fp_rate"),
        "fp_rate_E": pool_rate("E", "fp_rate"),
        "fn_rate_P": fn_p,
        "fn_rate_N": pool_rate("N", "fn_rate"),
        "fn_rate_E": pool_rate("E", "fn_rate"),
    }

    result = {
        "status": "COMPLETE",
        "prereg": "DATA-anchor-pair-a2-same-sensor-prereg-2026-07-10",
        "smoke": smoke,
        "eligibility": elig_meta,
        "params": {
            "arm_p": n_p,
            "arm_b": n_b,
            "width_p": HEAD_WIDTH_P,
            "width_b": width_b,
        },
        "seed_evals": seed_evals,
        "seed_logs": {
            a: [{k: v for k, v in lg.items() if k != "warnings"} for lg in logs]
            for a, logs in seed_logs.items()
        },
        "h2_ride_along": h2,
        "r3_verdict": verdict,
    }
    (out / "pilot_result.json").write_text(
        json.dumps(result, indent=2, default=str), encoding="utf-8"
    )
    (out / "pilot_metrics.json").write_text(
        json.dumps(metrics_flat, indent=2), encoding="utf-8"
    )
    (out / "summary.md").write_text(_summary_md(result), encoding="utf-8")
    print(f"[done] {out / 'pilot_result.json'}", flush=True)
    return result


def _summary_md(result: Mapping[str, Any]) -> str:
    v = result.get("r3_verdict") or {}
    smoke = result.get("smoke") or {}
    p = v.get("pooled") or {}
    lines = [
        "# Pairing-A″ (same-sensor present) pilot summary",
        "",
        f"**Same-sensor smoke:** {smoke.get('verdict')}",
        f"**R3 verdict (Arm P vs B):** {v.get('verdict', 'n/a')}",
        "",
        "## Smoke medians",
        f"- s_pos (non-ref present ↔ ref): {smoke.get('median_cos_nonref_present_ref')}",
        f"- s_abs (early absent ↔ ref): {smoke.get('median_cos_early_absent_ref')}",
        f"- s_neg (other present_ref ↔ ref): {smoke.get('median_cos_other_present_ref')}",
        "",
        "## Pooled rates (3 seeds, report-half TB)",
        f"- FP rate P/B: {p.get('fp_rate_p')} / {p.get('fp_rate_b')} "
        f"(red={p.get('fp_reduction')}, CI_lo={p.get('fp_reduction_ci_low')})",
        f"- FN rate P/B: {p.get('fn_rate_p')} / {p.get('fn_rate_b')} "
        f"(red={p.get('fn_reduction')}, CI_lo={p.get('fn_reduction_ci_low')})",
        "",
        "## H2 polarity ride-along (FP rates)",
        f"- P={result.get('h2_ride_along', {}).get('fp_rate_P')} "
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
    ap.add_argument("--out-dir", type=Path, default=OUT_DEFAULT)
    ap.add_argument(
        "--reuse-cand-grids",
        type=Path,
        default=A1_CAND_GRIDS,
        help="A′ GEHI cand_grids.npy to symlink when row counts match",
    )
    ap.add_argument("--device", default="cuda", choices=("cuda", "cpu"))
    ap.add_argument("--batch-size", type=int, default=8)
    ap.add_argument("--coverage-floor", type=float, default=DEFAULT_COVERAGE_FLOOR)
    ap.add_argument("--skip-extract", action="store_true")
    ap.add_argument("--smoke-only", action="store_true", help="stop after H3 smoke")
    args = ap.parse_args()
    t0 = time.perf_counter()
    run_pilot(args)
    print(f"[wall] {time.perf_counter() - t0:.1f}s", flush=True)


if __name__ == "__main__":
    main()
