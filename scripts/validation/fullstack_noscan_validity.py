#!/usr/bin/env python3
"""Validity check for the full-stack no-search run: self-consistency != accuracy.

The reproducibility numbers (sustained mode-hit 0.91) only say the 10 reps AGREE;
they do not say the agreed answer is RIGHT. This joins each unit's modal install
year (sustained estimator) to the production install year and measures agreement,
to rule out "10 reps consistently wrong".

IMPORTANT: production (jhb_full382_fpcut, via the adaptive search) is itself ONE
noisy draw, not hand ground truth. A disagreement is ambiguous -- it can mean the
full-stack scan FIXED a production search error, or that the full-stack scan is
wrong. Read agreement rate + the disagreement direction, not as a pass/fail.
"""
from __future__ import annotations

import argparse
import ast
import csv
import json
from collections import Counter
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--per-unit", type=Path, required=True, help="per_unit_fullstack.csv")
    ap.add_argument("--reference", type=Path, required=True, help="mini_reliability reference.csv")
    ap.add_argument("--out-dir", type=Path, required=True)
    a = ap.parse_args()
    a.out_dir.mkdir(parents=True, exist_ok=True)

    # production answer per target_anchor_id
    prod = {}
    for r in csv.DictReader(open(a.reference)):
        prod[r["target_anchor_id"]] = {
            "year": (r.get("prod_install_year") or "").strip()[:4],
            "agree_key": (r.get("prod_agree_key") or "").strip(),
            "status": (r.get("prod_date_status") or "").strip(),
            "stratum": (r.get("status_stratum") or "").strip(),
        }

    rows = []
    for r in csv.DictReader(open(a.per_unit)):
        unit = tuple(ast.literal_eval(r["unit"]))
        anchor = unit[1]
        p = prod.get(anchor)
        if p is None:
            continue
        fs_year = (r.get("modal_sustained_year") or "").strip()
        fs_undated = fs_year == ""
        prod_undated = (p["year"] == "") or ("UNDATED" in p["agree_key"])
        if fs_undated and prod_undated:
            cat, ydiff = "both_undated", ""
        elif fs_undated != prod_undated:
            cat, ydiff = "dated_vs_undated_mismatch", ""
        else:
            ydiff = abs(int(fs_year) - int(p["year"]))
            cat = "year_exact" if ydiff == 0 else ("year_within1" if ydiff <= 1 else "year_off")
        rows.append({
            "anchor": anchor[-9:], "stratum": p["stratum"],
            "fs_year": fs_year or "UNDATED", "prod_year": p["year"] or "UNDATED",
            "sustained_mode_hit": r["sustained_year_mode_hit"],
            "category": cat, "year_diff": ydiff,
        })

    cats = Counter(r["category"] for r in rows)
    both_dated = [r for r in rows if r["category"] in ("year_exact", "year_within1", "year_off")]
    n = len(rows)
    summary = {
        "note": "production = one noisy adaptive-search draw, NOT hand GT; agreement = reproduce-production, disagreement is ambiguous",
        "n_units": n,
        "year_exact": cats.get("year_exact", 0),
        "year_within_1yr": cats.get("year_exact", 0) + cats.get("year_within1", 0),
        "year_off_ge2": cats.get("year_off", 0),
        "both_undated": cats.get("both_undated", 0),
        "dated_vs_undated_mismatch": cats.get("dated_vs_undated_mismatch", 0),
        "pct_exact_year_of_dated": round(cats.get("year_exact", 0) / len(both_dated), 3) if both_dated else None,
        "pct_within1_of_dated": round((cats.get("year_exact", 0) + cats.get("year_within1", 0)) / len(both_dated), 3) if both_dated else None,
        "pct_concordant_decision": round((cats.get("year_exact", 0) + cats.get("year_within1", 0)
                                          + cats.get("year_off", 0) + cats.get("both_undated", 0)) / n, 3) if n else None,
    }
    (a.out_dir / "validity_summary.json").write_text(json.dumps(summary, indent=2))
    with open(a.out_dir / "validity_per_unit.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows)

    print(json.dumps(summary, indent=2))
    print("\n--- disagreements (year_off >=2 OR dated/undated mismatch) ---")
    print(f"{'anchor':>10} {'stratum':>40} {'fs':>8} {'prod':>8} {'selfhit':>8}")
    for r in sorted(rows, key=lambda x: (x["category"] != "dated_vs_undated_mismatch", x["category"])):
        if r["category"] in ("year_off", "dated_vs_undated_mismatch"):
            print(f"{r['anchor']:>10} {r['stratum']:>40} {r['fs_year']:>8} {r['prod_year']:>8} {r['sustained_mode_hit']:>8}")
    print(f"\nwrote {a.out_dir}/validity_summary.json + validity_per_unit.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
