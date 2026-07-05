#!/usr/bin/env python3
"""ISSUE-04: D8-rule estimator comparison on the EXTENDED reliability panel.

Concatenates the banked full-stack frame-verdict table
(``fullstack_noscan_20260630/long_all.csv``) with the new ``done_appears``
extension run, recomputes per-unit reliability for BOTH baseline estimators
(FPD = first-present-date, sustained = first-sustained-date), and reports every
metric in the four D8 variants:

  - unweighted        mean over units (the diagnostic view; what the banked
                      summary.json reported)
  - inventory-weighted stratum means weighted by the population inventory
                      weight from the sampler manifest (the HEADLINE view;
                      done_appears carries ~0.689)
  - all-units         UNDATED counts as a mode value / against the hit
  - dated-only        mode-hit over dated reps only, reported alongside
                      (D8 rule 3), with the dated-rep fraction

Per D8 rule 5 the weighted headline is only meaningful once the dominant
stratum has n>=8 support — which is exactly what the ISSUE-04 extension adds.
Estimator logic is imported from ``fullstack_noscan_analyze`` (no drift).
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.validation.fullstack_noscan_analyze import (  # noqa: E402
    _mode_hit,
    _tier,
    derive_install,
)


def load_long_many(paths: list[Path]):
    data: dict[tuple, dict[str, dict[str, str]]] = defaultdict(lambda: defaultdict(dict))
    for path in paths:
        with path.open(newline="") as fh:
            for r in csv.DictReader(fh):
                unit = (r["chip_id"], r["anchor_id"], r["target_label"])
                data[unit][r["rep"]][r["capture_date"]] = r["pv_present"]
    return data


def unit_metrics(reps: dict[str, dict[str, str]]) -> dict:
    """Per-unit reliability for both estimators, all-units + dated-only."""
    fpd_list, fsd_list = [], []
    for _rep, datemap in sorted(reps.items()):
        profile = sorted(datemap.items())
        fpd, fsd, und = derive_install(profile)
        fpd_list.append("UNDATED" if und else f"FPD|{fpd}")
        fsd_list.append("UNDATED" if fsd == "" else f"FSD|{fsd}")

    def block(lst: list[str], tag: str) -> dict:
        years = ["" if v == "UNDATED" else v.split("|")[1][:4] for v in lst]
        _, hit_all = _mode_hit(lst)
        _, yr_all = _mode_hit(years)
        dated = [v for v in lst if v != "UNDATED"]
        dated_years = [v.split("|")[1][:4] for v in dated]
        if dated:
            m_d, hit_d = _mode_hit(dated)
            _, yr_d = _mode_hit(dated_years)
        else:
            m_d, hit_d, yr_d = "", 0.0, 0.0
        modal, _ = _mode_hit(lst)
        return {
            f"{tag}_mode_hit": round(hit_all, 3),
            f"{tag}_year_mode_hit": round(yr_all, 3),
            f"{tag}_mode_hit_dated_only": round(hit_d, 3),
            f"{tag}_year_mode_hit_dated_only": round(yr_d, 3),
            f"{tag}_dated_fraction": round(len(dated) / len(lst), 3) if lst else 0.0,
            f"{tag}_modal": modal,
            f"{tag}_modal_dated": m_d,
        }

    out = {"n_reps": len(reps)}
    out.update(block(fpd_list, "fpd"))
    out.update(block(fsd_list, "sustained"))
    out["fpd_undated_flip_rate"] = round(
        sum(1 for v in fpd_list if v == "UNDATED") / len(fpd_list), 3
    ) if fpd_list else 0.0
    out["sustained_undated_flip_rate"] = round(
        sum(1 for v in fsd_list if v == "UNDATED") / len(fsd_list), 3
    ) if fsd_list else 0.0
    out["tier_fpd"] = _tier(out["fpd_mode_hit"])
    return out


METRICS = [
    "fpd_mode_hit", "fpd_year_mode_hit",
    "fpd_mode_hit_dated_only", "fpd_year_mode_hit_dated_only", "fpd_dated_fraction",
    "sustained_mode_hit", "sustained_year_mode_hit",
    "sustained_mode_hit_dated_only", "sustained_year_mode_hit_dated_only",
    "sustained_dated_fraction",
    "fpd_undated_flip_rate", "sustained_undated_flip_rate",
]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--long", type=Path, nargs="+", required=True,
                    help="one or more long_all.csv to concatenate")
    ap.add_argument("--sample-anchors", type=Path, required=True,
                    help="anchor_id,grid_id,status_stratum map covering ALL chips")
    ap.add_argument("--weights-manifest", type=Path, required=True,
                    help="sample_manifest.json holding inventory_weight per stratum")
    ap.add_argument("--out-dir", type=Path, required=True)
    a = ap.parse_args()
    a.out_dir.mkdir(parents=True, exist_ok=True)

    strat = {r["anchor_id"]: r["status_stratum"]
             for r in csv.DictReader(open(a.sample_anchors))}
    weights = json.loads(a.weights_manifest.read_text())["inventory_weight"]

    data = load_long_many(a.long)
    per_unit = []
    for unit, reps in sorted(data.items()):
        chip = unit[0]
        row = {"unit": str(unit), "chip_id": chip,
               "stratum": strat.get(chip, "unknown")}
        row.update(unit_metrics(reps))
        per_unit.append(row)

    strata = sorted({r["stratum"] for r in per_unit})
    unknown = [r for r in per_unit if r["stratum"] == "unknown"]
    if unknown:
        raise SystemExit(f"units with unknown stratum (fix --sample-anchors): "
                         f"{[r['unit'] for r in unknown][:5]}")

    def stratum_means(rows):
        n = len(rows) or 1
        return {m: sum(r[m] for r in rows) / n for m in METRICS}

    by_stratum = {}
    for st in strata:
        rows = [r for r in per_unit if r["stratum"] == st]
        by_stratum[st] = {"n": len(rows), **{m: round(v, 3) for m, v in stratum_means(rows).items()}}

    # unweighted overall = mean over units; weighted = inventory-weighted stratum means
    overall_unweighted = {m: round(v, 3) for m, v in stratum_means(per_unit).items()}
    wsum = sum(weights.get(st, 0.0) for st in strata)
    overall_weighted = {}
    for m in METRICS:
        overall_weighted[m] = round(
            sum(weights.get(st, 0.0) * by_stratum[st][m] for st in strata) / wsum, 3
        )

    summary = {
        "experiment": "ISSUE-04 extended-panel D8 estimator comparison "
                      "(full-stack no-search, window=8, K=10)",
        "n_units": len(per_unit),
        "n_units_by_stratum": {st: by_stratum[st]["n"] for st in strata},
        "inventory_weights": {st: weights.get(st, 0.0) for st in strata},
        "weight_coverage": round(wsum, 4),
        "overall_unweighted": overall_unweighted,
        "overall_inventory_weighted": overall_weighted,
        "by_stratum": by_stratum,
        "d8_notes": [
            "weighted headline valid: dominant stratum support "
            f"n={by_stratum.get('done_appears', {}).get('n', 0)} (>=8 required)",
            "dated-only variants reported alongside all-units per D8 rule 3",
            "DIAGNOSTIC-ONLY caliber: mode-hit / year-mode-hit are hard-MAP "
            "point-date metrics, retired as the production install-year caliber "
            "(PRD-AMENDMENT-P1, ISSUE-22); production caliber = fractional/survival "
            "year mass",
        ],
    }
    (a.out_dir / "d8_summary.json").write_text(json.dumps(summary, indent=2))
    with open(a.out_dir / "per_unit_extended.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(per_unit[0].keys()))
        w.writeheader(); w.writerows(per_unit)

    L = ["# ISSUE-04 extended panel — D8 estimator comparison", "",
         "_Diagnostic-only caliber: hard-MAP point-date mode-hit metrics, retired as "
         "the production install-year caliber per PRD-AMENDMENT-P1 (ISSUE-22); "
         "production caliber = fractional/survival year mass._", "",
         f"- units: {len(per_unit)}  (done_appears n={by_stratum.get('done_appears', {}).get('n', 0)})",
         f"- weights from: {a.weights_manifest}", "",
         "## Overall (date-level mode-hit / year-level / undated-flip)", "",
         "| estimator | variant | date-hit | year-hit | dated-only date-hit | dated-only year-hit | undated-flip |",
         "|---|---|--:|--:|--:|--:|--:|"]
    for tag, label in (("fpd", "FPD"), ("sustained", "sustained")):
        for vname, blk in (("unweighted", overall_unweighted),
                           ("inventory-weighted", overall_weighted)):
            L.append(
                f"| {label} | {vname} | {blk[f'{tag}_mode_hit']} | {blk[f'{tag}_year_mode_hit']} "
                f"| {blk[f'{tag}_mode_hit_dated_only']} | {blk[f'{tag}_year_mode_hit_dated_only']} "
                f"| {blk[f'{tag}_undated_flip_rate']} |")
    L += ["", "## By stratum (unweighted means; weight in header)", "",
          "| stratum | w | n | FPD date-hit | FPD yr | FPD flip | sus date-hit | sus yr | sus yr dated-only | sus flip |",
          "|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|"]
    for st in strata:
        s = by_stratum[st]
        L.append(f"| {st} | {weights.get(st, 0.0):.4f} | {s['n']} | {s['fpd_mode_hit']:.3f} "
                 f"| {s['fpd_year_mode_hit']:.3f} | {s['fpd_undated_flip_rate']:.3f} "
                 f"| {s['sustained_mode_hit']:.3f} "
                 f"| {s['sustained_year_mode_hit']:.3f} | {s['sustained_year_mode_hit_dated_only']:.3f} "
                 f"| {s['sustained_undated_flip_rate']:.3f} |")
    (a.out_dir / "d8_table.md").write_text("\n".join(L) + "\n")

    print(json.dumps({k: summary[k] for k in
                      ("n_units", "n_units_by_stratum", "overall_unweighted",
                       "overall_inventory_weighted")}, indent=2))
    print(f"\nwrote {a.out_dir}/d8_summary.json + d8_table.md + per_unit_extended.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
