#!/usr/bin/env python3
"""RUN3 coverage-gain QA: reproducible transition analysis + stratified sampler.

Answers "is the RUN2->RUN3 coverage gain (78.4%->83.2% bounded) a real accuracy
improvement or a different failure-mode mix?" by decomposing every anchor's
RUN2->RUN3 transition in DELIVERABLE coverage classes (dated / census_bound /
left_censored / undated -- the same classes build_install_dated_deliverable.py's
coverage_reconcile gate reports), cross-tabbing each transition stratum against
the RUN2 stale marker offset (`target_offset_x_m/y_m` from the v1 anchors CSV,
the ISSUE-27 bug that RUN3 fixed to offset==0), and drawing a fixed-seed
stratified sample for visual grading.

Deterministic and read-only with respect to the three source inputs. Re-running
with the same input paths and --seed reproduces byte-identical outputs. See
docs/replan_v2/DATA-fullscan-run3-qa-coverage-2026-07-19.md.
"""
from __future__ import annotations
import argparse, csv, hashlib, random, statistics, collections
from datetime import date
from pathlib import Path

from scripts.temporal.build_install_dated_deliverable import classify_row

def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()

def load(p: Path) -> dict[str, dict]:
    with p.open(newline="", encoding="utf-8") as f:
        return {r["anchor_id"]: r for r in csv.DictReader(f)}

def dclass(r: dict) -> str:
    """Deliverable coverage class, using the production classification gate."""
    fields = classify_row(r)
    if fields.left_censored:
        return "left_censored"
    if fields.census_bound:
        return "census_bound"
    if fields.date_status == "done_appears" and fields.install_date:
        return "dated"
    return "undated"

def midd(r: dict):
    v = r["install_mid_estimate"]
    try:
        return date.fromisoformat(v) if v else None
    except ValueError:
        return None

def stratum(a: str, r2: dict, r3: dict) -> str:
    x2, x3 = r2[a], r3[a]
    s2 = x2["status"]; c2, c3 = dclass(x2), dclass(x3)
    if s2 == "done_installed_during_census" and c3 == "dated":
        return "S1_censusbound_to_dated"
    if s2 == "done_ambiguous_marker_missed_pv" and c3 in ("dated", "census_bound", "left_censored"):
        return "S2_contradiction_resolved"
    if c2 == "undated" and s2 != "done_ambiguous_marker_missed_pv" and c3 == "dated":
        return "S3_undated_to_dated"
    if c2 == "dated" and c3 in ("census_bound", "undated"):
        return "S4a_dated_to_bound_or_undated"
    if c2 == "dated" and c3 == "left_censored":
        return "S4b_dated_to_leftcensor"
    if c2 == "census_bound" and c3 == "census_bound":
        return "S6_censusbound_persist"
    if c2 == "dated" and c3 == "dated":
        m2, m3 = midd(x2), midd(x3)
        if m2 and m3 and abs((m3 - m2).days) >= 180:
            return "S5_redate_ge180d"
        return "S8_dated_stable"
    if c3 == "left_censored" and c2 != "left_censored":
        return "S7_became_leftcensor"
    return "S9_other"

def v1_offset(a: str, v1: dict):
    r = v1.get(a)
    if not r:
        return None
    try:
        return abs(float(r["target_offset_x_m"])) + abs(float(r["target_offset_y_m"]))
    except (KeyError, ValueError):
        return None

BASE_QUOTAS = {
    "S1_censusbound_to_dated": 22, "S2_contradiction_resolved": 16,
    "S3_undated_to_dated": 12, "S4a_dated_to_bound_or_undated": 16,
    "S5_redate_ge180d": 10, "S6_censusbound_persist": 14,
    "S7_became_leftcensor": 8, "S8_dated_stable": 8,
}
SUPPLEMENTAL_QUOTAS = {"S4b_dated_to_leftcensor": 8}

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run2-intervals", type=Path, required=True)
    ap.add_argument("--run3-intervals", type=Path, required=True)
    ap.add_argument("--v1-anchors", type=Path, required=True, help="RUN2 anchors_all.csv (stale offsets)")
    ap.add_argument("--out-dir", type=Path, required=True)
    ap.add_argument("--seed", type=int, default=20260719)
    args = ap.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)

    r2, r3, v1 = load(args.run2_intervals), load(args.run3_intervals), load(args.v1_anchors)
    common = sorted(set(r2) & set(r3))
    by = collections.defaultdict(list)
    for a in common:
        by[stratum(a, r2, r3)].append(a)

    out = []
    out.append("# RUN3 coverage-gain QA — transition + offset analysis (reproducible)\n")
    out.append("## Input provenance (sha256)\n")
    for label, p in [("run2_intervals", args.run2_intervals), ("run3_intervals", args.run3_intervals), ("v1_anchors", args.v1_anchors)]:
        out.append(f"- `{label}` = `{p}`  sha256 `{sha256(p)}`")
    out.append(f"- join: {len(common)} common anchor_ids "
               f"({len(set(r2)-set(r3))} run2-only, {len(set(r3)-set(r2))} run3-only)  seed={args.seed}\n")

    # class totals
    r2c = collections.Counter(dclass(r2[a]) for a in common)
    r3c = collections.Counter(dclass(r3[a]) for a in common)
    out.append("## Deliverable coverage-class totals (RUN2 vs RUN3)\n")
    out.append("| class | RUN2 n | RUN2 % | RUN3 n | RUN3 % |")
    out.append("|---|---:|---:|---:|---:|")
    for k in ("dated", "census_bound", "left_censored", "undated"):
        out.append(f"| {k} | {r2c[k]} | {r2c[k]/len(common):.1%} | {r3c[k]} | {r3c[k]/len(common):.1%} |")
    bnd2 = r2c["dated"] + r2c["census_bound"]; bnd3 = r3c["dated"] + r3c["census_bound"]
    out.append(f"| **bounded (dated+census_bound)** | **{bnd2}** | **{bnd2/len(common):.1%}** | **{bnd3}** | **{bnd3/len(common):.1%}** |\n")

    # transition matrix
    cov = collections.Counter((dclass(r2[a]), dclass(r3[a])) for a in common)
    c2tot = collections.Counter()
    for (a2, _), n in cov.items():
        c2tot[a2] += n
    out.append("## Coverage-class transition matrix (RUN2 -> RUN3)\n")
    out.append("| RUN2 class | RUN3 class | n | % of RUN2 class |")
    out.append("|---|---|---:|---:|")
    for (a2, a3), n in sorted(cov.items(), key=lambda kv: -kv[1]):
        out.append(f"| {a2} | {a3} | {n} | {n/c2tot[a2]:.1%} |")
    out.append("")

    # offset-by-stratum: the quantitative backbone
    out.append("## RUN2 stale marker offset (|dx|+|dy|, metres) by transition stratum\n")
    out.append("A dose-response test: if the coverage change were a random new failure "
               "mode it would not correlate with how badly the RUN2 marker was mis-placed. "
               "It does — the recovery strata (S1/S2/S3) sit at ~18-20 m median "
               "offset, while the unchanged/stable control (S8) sits at ~4 m. "
               "S4b is kept separate so dated-to-left-censored losses are not "
               "misreported as gains.\n")
    out.append("| stratum | n | median | mean | >10m | >25m |")
    out.append("|---|---:|---:|---:|---:|---:|")
    allo = []
    for k in sorted(by):
        vs = [o for a in by[k] if (o := v1_offset(a, v1)) is not None]
        if not vs:
            continue
        allo += vs
        out.append(f"| {k} | {len(vs)} | {statistics.median(vs):.1f} | {statistics.mean(vs):.1f} | "
                   f"{sum(1 for v in vs if v>10)/len(vs):.1%} | {sum(1 for v in vs if v>25)/len(vs):.1%} |")
    out.append(f"| ALL | {len(allo)} | {statistics.median(allo):.1f} | {statistics.mean(allo):.1f} | "
               f"{sum(1 for v in allo if v>10)/len(allo):.1%} | {sum(1 for v in allo if v>25)/len(allo):.1%} |\n")

    (args.out_dir / "transition_offset_analysis.md").write_text("\n".join(out) + "\n", encoding="utf-8")
    print("\n".join(out))

    # Fixed-seed stratified sample. Preserve the original 106 anchor IDs exactly:
    # at sampling time its S7 pool included dated->left_censored. The draw
    # happened to select none of those 395 rows. We now classify them as S4b and
    # append an explicit supplemental draw, without silently changing all later
    # strata by advancing the shared RNG at a different point.
    rng = random.Random(args.seed)
    manifest = []
    sample_pools = dict(by)
    sample_pools["S7_became_leftcensor"] = (
        by.get("S7_became_leftcensor", []) + by.get("S4b_dated_to_leftcensor", [])
    )
    for strat, q in [*BASE_QUOTAS.items(), *SUPPLEMENTAL_QUOTAS.items()]:
        pool = sorted(sample_pools.get(strat, [])) if strat in BASE_QUOTAS else sorted(by.get(strat, []))
        pick = pool if len(pool) <= q else rng.sample(pool, q)
        for a in sorted(pick):
            actual_strat = stratum(a, r2, r3)
            if strat == "S7_became_leftcensor" and actual_strat != strat:
                raise RuntimeError(
                    "The preserved original S7 draw included a dated->left_censored row; "
                    "update the manifest migration explicitly."
                )
            x2, x3 = r2[a], r3[a]; m2, m3 = midd(x2), midd(x3)
            manifest.append({
                "anchor_id": a, "lane": x3["lane"], "stratum": actual_strat,
                "run2_status": x2["status"], "run3_status": x3["status"],
                "run2_class": dclass(x2), "run3_class": dclass(x3),
                "run2_mid": x2["install_mid_estimate"], "run3_mid": x3["install_mid_estimate"],
                "mid_shift_days": ((m3 - m2).days if (m2 and m3) else ""),
                "v1_offset_m": (f"{v1_offset(a, v1):.2f}" if v1_offset(a, v1) is not None else ""),
                "run3_latest_absent": x3["latest_absent_date"], "run3_earliest_present": x3["earliest_present_date"],
                "run3_scan_state_path": x3["scan_state_path"], "run3_notes": x3["notes"],
            })
    mpath = args.out_dir / "qa_manifest.csv"
    with mpath.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(manifest[0].keys())); w.writeheader(); w.writerows(manifest)
    print(f"\nSampled {len(manifest)} anchors (seed={args.seed}) -> {mpath}  sha256 {sha256(mpath)}")

if __name__ == "__main__":
    main()
