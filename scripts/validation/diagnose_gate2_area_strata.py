#!/usr/bin/env python3
"""Area-stratified diagnosis of ISSUE-06 gate-2 decoded disagreements.

Question: does the student's interval-level fidelity miss concentrate on
small installations ("attention can't see small PV")? Three discriminating
signals, computed per footprint-area bucket (buckets match the registration
pilot in replan_v2/DATA-learned-matching-bounded-pilot-2026-07-09):

  1. decoded student-vs-teacher agreement (invisibility -> monotonic drop
     as area shrinks);
  2. teacher rep-vs-rep self-consistency on the same buckets (if the
     teacher drops the same way, the gradient is intrinsic ambiguity, and
     the student-specific effect is the GAP ceiling - student);
  3. end-bound direction on INTERVAL<->INTERVAL misses (invisibility ->
     student misses faint early frames -> LATE first-present, delta > 0;
     an EARLY bias means false-presents, the opposite signature).

Inputs are the unit_rows.jsonl files written by diagnose_fidelity_gate2.py
plus reference.csv (source_area_m2 per target). Read-only; no re-scoring.

Usage:
  python scripts/validation/diagnose_gate2_area_strata.py \
    --reference ~/zasolar_data/geid_temporal/llm_endtoend_storebacked_20260704/reference.csv \
    --diag-root ~/zasolar_data/geid_temporal/fidelity_gate_20260710/gate2_diag \
    --out ~/zasolar_data/geid_temporal/fidelity_gate_20260710/gate2_diag/area_strata
"""
from __future__ import annotations

import argparse
import csv
import json
import math
from collections import defaultdict
from itertools import combinations
from pathlib import Path

BUCKETS = [
    ("a_xs<15", 0.0, 15.0),
    ("b_sm15-40", 15.0, 40.0),
    ("c_md40-100", 40.0, 100.0),
    ("d_lg>=100", 100.0, math.inf),
]
BACKBONES = ("dinov3_lsat", "dinov2_floor")


def bucket_of(area: float) -> str:
    for name, lo, hi in BUCKETS:
        if lo <= area < hi:
            return name
    raise ValueError(f"unbucketable area {area}")


def wilson(p: float, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (float("nan"), float("nan"))
    denom = 1 + z * z / n
    centre = p + z * z / (2 * n)
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return ((centre - margin) / denom, (centre + margin) / denom)


def end_year(key: str) -> int:
    return int(key.split("|")[-1][:4])


def load_area(reference_csv: Path) -> dict[str, float]:
    with open(reference_csv) as f:
        return {
            row["target_anchor_id"]: float(row["source_area_m2"])
            for row in csv.DictReader(f)
        }


def decoded_rows(diag_root: Path, backbone: str, area: dict[str, float]) -> list[dict]:
    rows = []
    with open(diag_root / backbone / "unit_rows.jsonl") as f:
        for line in f:
            r = json.loads(line)
            if not r["decoded"]:
                continue
            r["area"] = area[r["target_anchor_id"]]
            r["bucket"] = bucket_of(r["area"])
            rows.append(r)
    return rows


def per_bucket_student(rows: list[dict]) -> dict[str, dict]:
    out = {}
    for name, _, _ in BUCKETS:
        b = [r for r in rows if r["bucket"] == name]
        if not b:
            continue
        n = len(b)
        agree = sum(r["agree"] for r in b) / n
        ci = wilson(agree, n)
        wsum = sum(r["w"] for r in b)
        clean = [r for r in b if not r["had_unusable"]]
        misses_ii = [
            r for r in b
            if not r["agree"] and r["t_class"] == "INTERVAL" and r["s_class"] == "INTERVAL"
        ]
        deltas = [end_year(r["student_key"]) - end_year(r["teacher_key"]) for r in misses_ii]
        out[name] = {
            "n_rows": n,
            "n_targets": len({r["target_anchor_id"] for r in b}),
            "agree": round(agree, 4),
            "agree_ci95": [round(ci[0], 4), round(ci[1], 4)],
            "agree_inv_weighted": round(sum(r["agree"] * r["w"] for r in b) / wsum, 4),
            "unusable_share": round(sum(r["had_unusable"] for r in b) / n, 4),
            "clean_agree": round(sum(r["agree"] for r in clean) / len(clean), 4) if clean else None,
            "n_interval_interval_miss": len(deltas),
            "delta_end_early_lt0": round(sum(d < 0 for d in deltas) / len(deltas), 4) if deltas else None,
            "delta_end_same_eq0": round(sum(d == 0 for d in deltas) / len(deltas), 4) if deltas else None,
            "delta_end_late_gt0": round(sum(d > 0 for d in deltas) / len(deltas), 4) if deltas else None,
        }
    return out


def per_bucket_teacher_ceiling(rows: list[dict]) -> dict[str, dict]:
    """Pairwise teacher rep-vs-rep agree_key agreement per bucket (unweighted).

    Teacher keys are identical across backbone files (same banked reps), so
    any one backbone's rows suffice.
    """
    per_target: dict[str, dict[int, str]] = defaultdict(dict)
    bucket: dict[str, str] = {}
    for r in rows:
        per_target[r["target_anchor_id"]][r["rep"]] = r["teacher_key"]
        bucket[r["target_anchor_id"]] = r["bucket"]
    out = {}
    for name, _, _ in BUCKETS:
        match = tot = 0
        for tgt, reps in per_target.items():
            if bucket[tgt] != name:
                continue
            for r1, r2 in combinations(sorted(reps), 2):
                tot += 1
                match += reps[r1] == reps[r2]
        if tot:
            out[name] = {"n_pairs": tot, "ceiling": round(match / tot, 4)}
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--reference", type=Path, required=True)
    ap.add_argument("--diag-root", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    area = load_area(args.reference)
    result: dict = {"buckets": [b[0] for b in BUCKETS], "backbones": {}}

    ceiling = None
    for backbone in BACKBONES:
        rows = decoded_rows(args.diag_root, backbone, area)
        if ceiling is None:
            ceiling = per_bucket_teacher_ceiling(rows)
        result["backbones"][backbone] = per_bucket_student(rows)
    result["teacher_ceiling"] = ceiling
    for backbone in BACKBONES:
        for name, stats in result["backbones"][backbone].items():
            if name in ceiling:
                stats["gap_vs_ceiling"] = round(ceiling[name]["ceiling"] - stats["agree"], 4)

    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "area_strata.json").write_text(json.dumps(result, indent=2))

    lines = ["# Gate-2 decoded agreement by footprint-area bucket", ""]
    lines.append("| bucket | n_tgt | ceiling | LSAT agree | LSAT gap | floor agree | floor gap | unus% | early/same/late (LSAT) |")
    lines.append("|---|--:|--:|--:|--:|--:|--:|--:|---|")
    for name, _, _ in BUCKETS:
        ls = result["backbones"]["dinov3_lsat"].get(name)
        fl = result["backbones"]["dinov2_floor"].get(name)
        ce = ceiling.get(name, {}).get("ceiling")
        if not ls:
            continue
        esl = "/".join(
            "-" if ls[k] is None else f"{ls[k]:.0%}"
            for k in ("delta_end_early_lt0", "delta_end_same_eq0", "delta_end_late_gt0")
        )
        lines.append(
            f"| {name} | {ls['n_targets']} | {ce:.3f} | {ls['agree']:.3f} | {ls['gap_vs_ceiling']:.3f} "
            f"| {fl['agree']:.3f} | {fl['gap_vs_ceiling']:.3f} | {ls['unusable_share']:.0%} | {esl} |"
        )
    md = "\n".join(lines) + "\n"
    (args.out / "area_strata.md").write_text(md)
    print(md)
    print(f"artifacts -> {args.out}")


if __name__ == "__main__":
    main()
