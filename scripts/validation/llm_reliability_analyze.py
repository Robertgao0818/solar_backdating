#!/usr/bin/env python3
"""Analyse the LLM test-retest reliability run: agreement across k=3 identical-input reps.

Reads score_rep{1,2,3}.csv (one Gemini sequence call per target per rep, on byte-identical
chips) + sample_anchors.csv (stratum) + sample_manifest.json (inventory weights), and reports:

  - P(install-date identical across all 3 reps)   per stratum + inventory-weighted overall
  - P(presence-pattern identical across all 3)     per stratum + weighted
  - mean per-date-cell agreement (all-3-agree rate over the 8 presence calls)
  - install-date dispersion among disagreers (months between earliest/latest of the 3)
  - abstain / quality_flag behaviour

Outputs: <out-dir>/reliability_summary.json and reliability_table.md
"""
from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from datetime import date
from pathlib import Path


def _key(r: dict) -> tuple:
    return (r.get("chip_id", ""), r.get("anchor_id", ""), r.get("target_label", ""))


def _parse_date(s: str):
    s = (s or "").strip()
    if not s:
        return None
    try:
        y, m, d = s[:10].split("-")
        return date(int(y), int(m), int(d))
    except Exception:
        return None


def _months_between(ds: list) -> float | None:
    pds = [_parse_date(d) for d in ds]
    pds = [p for p in pds if p is not None]
    if len(pds) < 2:
        return None
    lo, hi = min(pds), max(pds)
    return (hi.year - lo.year) * 12 + (hi.month - lo.month)


def _pattern_cell_agree(pats: list) -> tuple[int, int]:
    """Return (cells_all_agree, total_cells) across the 3 patterns of one target."""
    grids = [p.split("-") for p in pats if p]
    if len(grids) < 3:
        return (0, 0)
    n = min(len(g) for g in grids)
    agree = sum(1 for i in range(n) if grids[0][i] == grids[1][i] == grids[2][i])
    return (agree, n)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run-dir", type=Path, required=True)
    ap.add_argument("--sample-dir", type=Path, required=True)
    ap.add_argument("--out-dir", type=Path, required=True)
    args = ap.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)

    reps = []
    for i in (1, 2, 3):
        rows = list(csv.DictReader(open(args.run_dir / f"score_rep{i}.csv")))
        reps.append({_key(r): r for r in rows})

    stratum_by_group = {
        r["anchor_id"]: r["status_stratum"]
        for r in csv.DictReader(open(args.sample_dir / "sample_anchors.csv"))
    }
    manifest = json.load(open(args.sample_dir / "sample_manifest.json"))
    inv_w = manifest.get("inventory_weight", {})

    keys = sorted(set(reps[0]) & set(reps[1]) & set(reps[2]))

    per_stratum = defaultdict(lambda: {
        "n": 0, "date_ident": 0, "patt_ident": 0,
        "cell_agree": 0, "cell_total": 0,
        "any_abstain": 0, "disagree_months": [],
    })
    rows_out = []
    for k in keys:
        triplet = [reps[i][k] for i in range(3)]
        group = k[0]  # chip_id == group anchor id
        st = stratum_by_group.get(group, "unknown")
        s = per_stratum[st]
        s["n"] += 1

        fpds = [t.get("first_present_date", "") for t in triplet]
        pats = [t.get("sequence_pattern", "") for t in triplet]
        qflags = [t.get("quality_flag", "") for t in triplet]

        date_ident = len(set(fpds)) == 1
        patt_ident = len(set(pats)) == 1
        if date_ident:
            s["date_ident"] += 1
        if patt_ident:
            s["patt_ident"] += 1
        if any(q and q != "usable" for q in qflags):
            s["any_abstain"] += 1
        ca, ct = _pattern_cell_agree(pats)
        s["cell_agree"] += ca
        s["cell_total"] += ct
        if not date_ident:
            mb = _months_between(fpds)
            if mb is not None:
                s["disagree_months"].append(mb)
        rows_out.append({
            "group": group, "target_label": k[2], "stratum": st,
            "date_identical": int(date_ident), "pattern_identical": int(patt_ident),
            "fpd_reps": "|".join(fpds), "pattern_reps": "|".join(pats),
        })

    # aggregate
    def rate(a, b):
        return round(a / b, 4) if b else None

    strata_report = {}
    overall = {"n": 0, "date_ident": 0, "patt_ident": 0, "cell_agree": 0, "cell_total": 0}
    w_date = w_patt = w_sum = 0.0
    for st, s in per_stratum.items():
        dm = s["disagree_months"]
        strata_report[st] = {
            "n_targets": s["n"],
            "p_date_identical": rate(s["date_ident"], s["n"]),
            "p_pattern_identical": rate(s["patt_ident"], s["n"]),
            "mean_cell_agreement": rate(s["cell_agree"], s["cell_total"]),
            "n_abstain_any": s["any_abstain"],
            "date_disagree_months_median": (sorted(dm)[len(dm) // 2] if dm else None),
            "date_disagree_months_max": (max(dm) if dm else None),
            "inventory_weight": round(inv_w.get(st, 0.0), 4),
        }
        for k2 in ("n", "date_ident", "patt_ident", "cell_agree", "cell_total"):
            overall[k2] += s[k2] if k2 != "n" else s["n"]
        w = inv_w.get(st, 0.0)
        if s["n"]:
            w_date += w * (s["date_ident"] / s["n"])
            w_patt += w * (s["patt_ident"] / s["n"])
            w_sum += w

    report = {
        "experiment": "LLM sequence-scorer test-retest (k=3 identical-input reps)",
        "model": "gemini-3-flash-agent (local antigrav gateway)",
        "dates_window": manifest.get("dates"),
        "n_targets_evaluated": overall["n"],
        "sample_unweighted": {
            "p_date_identical": rate(overall["date_ident"], overall["n"]),
            "p_pattern_identical": rate(overall["patt_ident"], overall["n"]),
            "mean_cell_agreement": rate(overall["cell_agree"], overall["cell_total"]),
        },
        "inventory_weighted": {
            "p_date_identical": round(w_date / w_sum, 4) if w_sum else None,
            "p_pattern_identical": round(w_patt / w_sum, 4) if w_sum else None,
            "note": "re-weighted by each stratum's share of the 15,859-anchor inventory",
        },
        "by_stratum": strata_report,
    }
    json.dump(report, open(args.out_dir / "reliability_summary.json", "w"), indent=2)
    with open(args.out_dir / "reliability_targets.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows_out[0].keys()))
        w.writeheader(); w.writerows(rows_out)

    # markdown table
    lines = ["| Stratum | n | P(date ident) | P(pattern ident) | cell-agree | disagree med/max mo | inv wt |",
             "|---|--:|--:|--:|--:|--:|--:|"]
    for st, r in sorted(strata_report.items(), key=lambda kv: -kv[1]["inventory_weight"]):
        lines.append(f"| {st} | {r['n_targets']} | {r['p_date_identical']} | {r['p_pattern_identical']} "
                     f"| {r['mean_cell_agreement']} | {r['date_disagree_months_median']}/{r['date_disagree_months_max']} "
                     f"| {r['inventory_weight']} |")
    o = report["inventory_weighted"]; u = report["sample_unweighted"]
    lines.append(f"| **inventory-weighted** |  | **{o['p_date_identical']}** | **{o['p_pattern_identical']}** |  |  |  |")
    lines.append(f"| _sample-unweighted_ | {overall['n']} | {u['p_date_identical']} | {u['p_pattern_identical']} | {u['mean_cell_agreement']} |  |  |")
    Path(args.out_dir / "reliability_table.md").write_text("\n".join(lines) + "\n")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
