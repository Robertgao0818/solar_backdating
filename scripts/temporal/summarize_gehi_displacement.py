#!/usr/bin/env python3
"""Summarise the GEHI displacement audit into the numbers the round-1 report cites.

Reads ``per_chipdate_offsets.csv`` + ``gehi_vs_vexcel_bias.csv`` from an audit dir
and prints, evidence-graded:
  1. row / drop accounting;
  2. per-signal offset quantiles (overall + by zoom + by area bucket);
  3. contamination (retention) at PSR cuts {8,10,12} for the decision signal;
  4. the constant GEHI-vs-Vexcel bias distribution (the term S1 alone misses);
  5. decomposition sanity  median(S3) ≈ median(S1) + median(bias);
  6. independent-reference agreement  S3(Vexcel) vs S2(CoJ) on shared (chip,date).
"""

from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from pathlib import Path

import numpy as np


def _read(path: Path) -> list[dict]:
    if not path.exists() or path.stat().st_size == 0:
        return []
    with path.open() as f:
        return list(csv.DictReader(f))


def q(vals, p):
    return round(float(np.percentile(vals, p)), 3) if vals else None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--audit-dir", type=Path, default=Path.home() / "zasolar_data/geid_temporal/gehi_displacement_audit_2026-07-06")
    args = ap.parse_args()

    rows = _read(args.audit_dir / "per_chipdate_offsets.csv")
    bias = _read(args.audit_dir / "gehi_vs_vexcel_bias.csv")
    for r in rows:
        for k in ("best_offset_m", "dx_m", "dy_m", "psr", "alignment_score", "retain_tight12", "retain_banked96", "source_area_m2"):
            r[k] = float(r[k])
        r["year"] = int(r["year"])
    for b in bias:
        b["bias_offset_m"] = float(b["bias_offset_m"])

    by_kind = defaultdict(list)
    for r in rows:
        by_kind[r["ref_kind"].split("_")[0] if r["ref_kind"].startswith("S2") else r["ref_kind"]].append(r)

    print(f"\n{'='*70}\nGEHI DISPLACEMENT AUDIT — round-1 summary\n{'='*70}")
    print(f"total kept rows: {len(rows)}   bias rows (anchors w/ Vexcel present-day lock): {len(bias)}")
    drops = args.audit_dir / "drops.txt"
    if drops.exists():
        print("\n-- drop accounting --")
        print(drops.read_text().strip())

    print("\n-- (2) offset quantiles (m), best_offset_m, by signal --")
    print(f"{'signal':16} {'n':>6} {'p50':>7} {'p75':>7} {'p90':>7} {'p95':>7} {'p99':>7} {'max':>7}")
    for kind in sorted(by_kind):
        offs = [r["best_offset_m"] for r in by_kind[kind]]
        print(f"{kind:16} {len(offs):6d} {q(offs,50):7} {q(offs,75):7} {q(offs,90):7} {q(offs,95):7} {q(offs,99):7} {round(max(offs),2):7}")

    print("\n-- (2b) S3_vexcel offset p50/p90/p95 by zoom & area bucket --")
    s3 = [r for r in rows if r["ref_kind"] == "S3_vexcel"]
    for key in ("zoom", "area_bucket"):
        groups = defaultdict(list)
        for r in s3:
            groups[r[key]].append(r["best_offset_m"])
        for g, offs in sorted(groups.items()):
            print(f"   {key}={g:14} n={len(offs):5d} p50={q(offs,50):6} p90={q(offs,90):6} p95={q(offs,95):6} max={round(max(offs),2)}")

    print("\n-- (3) tight12 contamination for S3_vexcel (decision signal) at PSR cuts --")
    print(f"{'psr_cut':>7} {'n':>6} {'retain_mean':>12} {'clip>25%':>9} {'lose>50%':>9} {'full_loss':>10}")
    for cut in (8.0, 10.0, 12.0):
        rs = [r for r in s3 if r["psr"] >= cut]
        if not rs:
            continue
        t = [r["retain_tight12"] for r in rs]
        print(f"{cut:7} {len(rs):6d} {round(np.mean(t),4):12} "
              f"{round(np.mean([x<0.75 for x in t]),4):9} {round(np.mean([x<0.5 for x in t]),4):9} "
              f"{round(np.mean([x<=0.0 for x in t]),4):10}")
    print("   banked96 lose>50% (any signal, psr>=8):",
          round(np.mean([r["retain_banked96"] < 0.5 for r in rows if r["psr"] >= 8]), 4))

    print("\n-- (4) constant GEHI-vs-Vexcel bias |offset| (m), per anchor --")
    if bias:
        bo = [b["bias_offset_m"] for b in bias]
        print(f"   n={len(bo)} p50={q(bo,50)} p90={q(bo,90)} p95={q(bo,95)} max={round(max(bo),2)}")
        for thr in (1.0, 2.0, 3.0, 5.0):
            print(f"   anchors with bias > {thr:.0f} m: {sum(x>thr for x in bo)} ({100*np.mean([x>thr for x in bo]):.1f}%)")

    print("\n-- (5) decomposition sanity: median(S3) ?= median(S1) + median(bias) --")
    s1 = [r["best_offset_m"] for r in rows if r["ref_kind"] == "S1_gehi_latest"]
    s3o = [r["best_offset_m"] for r in s3]
    bo = [b["bias_offset_m"] for b in bias] if bias else [0]
    print(f"   median S1={q(s1,50)}  median bias={q(bo,50)}  median S3={q(s3o,50)}  (offsets are magnitudes, so ⊕ is loose)")

    print("\n-- (6) independent-reference agreement: S3(Vexcel) vs S2(CoJ), shared (chip,date) --")
    idx = defaultdict(dict)
    for r in rows:
        if r["ref_kind"] == "S3_vexcel":
            idx[(r["chip_id"], r["capture_date"])]["s3"] = r
        elif r["ref_kind"].startswith("S2_coj"):
            idx[(r["chip_id"], r["capture_date"])]["s2"] = r
    pairs = [(v["s2"], v["s3"]) for v in idx.values() if "s2" in v and "s3" in v]
    if pairs:
        d = [float(np.hypot(a["dx_m"] - b["dx_m"], a["dy_m"] - b["dy_m"])) for a, b in pairs]
        print(f"   shared (chip,date): {len(pairs)}   |S2 offset - S3 offset| vector diff (m): p50={q(d,50)} p90={q(d,90)}")
    else:
        print("   (no shared chip,date between S2 and S3)")
    print()


if __name__ == "__main__":
    main()
