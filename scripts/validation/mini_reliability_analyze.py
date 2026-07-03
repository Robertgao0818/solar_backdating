#!/usr/bin/env python3
"""Mini per-anchor reliability analysis for the 12-anchor / 10-rep two-arm test.

Two arms, SAME 12 anchors, SAME 10 reps:
  - Arm A (frozen): identical review PNGs re-scored 10x. Isolates L1 = LLM single-call
    randomness. Unit = per (group_anchor, target_label); install signal = first_present_date.
  - Arm B (end-to-end, L3 frozen via shared prefetched chip stack): the adaptive search
    is re-run from scratch 10x on a frozen full-vintage stack. Isolates L1 + L2 (LLM +
    adaptive search). Unit = per source_feature_id (installation); install signal = the
    canonical agree_key install interval (UNDATED / AP_BOUND<=|end / INTERVAL|start|end).

For EACH arm, per unit across the 10 reps, we compute:
  - mode-interval hit rate: fraction of reps landing in the unit's most-frequent interval,
    bucketed: >=0.8 rock-solid / 0.4-0.7 wobbly / <0.4 chaotic.
  - install-year-bucket consistency: fraction of reps in the modal install year.
  - undated flip rate: fraction of reps where the unit is undated/absorbing.
  - interval width distribution (arm B only; arm A is a point date -> width 0/NA).
All split by status_stratum, arm A vs arm B side by side, plus the A->B degradation.

Inputs:
  arm A: --arma-reps score_rep1.csv ... score_rep10.csv  + --sample-anchors (group->stratum)
  arm B: --armb-reps rep1/delivery.csv ...                + --reference (sfid->stratum, prod key)
"""
from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path


# ----------------------------- shared helpers -----------------------------
def _s(v) -> str:
    s = str(v if v is not None else "").strip()
    return "" if s in ("nan", "None") else s


def _parse_date(s: str):
    s = _s(s)[:10]
    if not s:
        return None
    try:
        y, m, d = s.split("-")
        return date(int(y), int(m), int(d))
    except Exception:
        return None


def _months(d0: date, d1: date) -> int:
    return (d1.year - d0.year) * 12 + (d1.month - d0.month)


def _tier(hit_rate: float) -> str:
    if hit_rate >= 0.8:
        return "rock_solid"
    if hit_rate >= 0.4:
        return "wobbly"
    return "chaotic"


def _mode_hit(values: list[str]) -> tuple[str, float, int]:
    """Return (modal_value, hit_rate over non-empty reps, n_reps_considered)."""
    vals = [v for v in values if v != ""]
    if not vals:
        return ("", 0.0, 0)
    c = Counter(vals)
    modal, modal_n = c.most_common(1)[0]
    return (modal, modal_n / len(values), len(values))  # denom = ALL reps (missing counts against)


# ----------------------------- arm A loader -----------------------------
def load_arm_a(rep_paths: list[Path], sample_anchors: Path):
    """unit = (group_anchor, target_label). Returns dict[unit] -> per-rep signals."""
    strat = {r["anchor_id"]: r["status_stratum"]
             for r in csv.DictReader(open(sample_anchors))}
    # per rep: key=(chip_id, anchor_id, target_label) -> row
    reps = []
    for p in rep_paths:
        reps.append({(r["chip_id"], r["anchor_id"], r["target_label"]): r
                     for r in csv.DictReader(open(p))})
    keys = set()
    for r in reps:
        keys |= set(r)
    units = {}
    for k in sorted(keys):
        group = k[0]
        st = strat.get(group, "unknown")
        intervals, years, undated = [], [], []
        for rep in reps:
            row = rep.get(k)
            if row is None:
                intervals.append(""); years.append(""); undated.append(None); continue
            fpd = _s(row.get("first_present_date"))
            pat = _s(row.get("sequence_pattern"))
            qf = _s(row.get("quality_flag"))
            # undated: no first-present date (never appears) OR sequence failed
            is_undated = (fpd == "") or pat.strip("?-") == "" or "fail" in qf.lower()
            undated.append(1 if is_undated else 0)
            # interval signal for arm A = first_present_date (point)
            intervals.append("UNDATED" if is_undated else f"FPD|{fpd}")
            years.append("" if is_undated or len(fpd) < 4 else fpd[:4])
        units[k] = {"group": group, "stratum": st, "label": k[2],
                    "intervals": intervals, "years": years, "undated": undated,
                    "widths": [0] * len(reps)}  # point date
    return units, len(reps)


# ----------------------------- arm B loader -----------------------------
def _agree_key_raw(r: dict) -> str:
    if _s(r.get("undated_reason")):
        return "UNDATED"
    if not _s(r.get("date_provider")):
        return "UNDATED"
    bound = _s(r.get("date_is_bound"))
    try:
        bound_i = int(float(bound)) if bound else 0
    except ValueError:
        bound_i = 0
    if bound_i == 1:
        return f"AP_BOUND<=|{_s(r.get('install_interval_end'))}"
    return f"INTERVAL|{_s(r.get('install_interval_start'))}|{_s(r.get('install_interval_end'))}"


def _year_b(r: dict) -> str:
    for c in ("install_date", "install_interval_end", "earliest_present_date"):
        v = _s(r.get(c))
        if len(v) >= 4 and v[:4].isdigit():
            return v[:4]
    return ""


def _width_b(r: dict):
    s, e = _parse_date(r.get("install_interval_start")), _parse_date(r.get("install_interval_end"))
    if s and e:
        return _months(s, e)
    return None


def load_arm_b(rep_paths: list[Path], reference: Path):
    """unit = source_feature_id. Returns dict[unit] -> per-rep signals (+ prod key)."""
    refrows = list(csv.DictReader(open(reference)))
    strat = {int(r["source_feature_id"]): r["status_stratum"] for r in refrows}
    prod_key = {int(r["source_feature_id"]): r["prod_agree_key"] for r in refrows}
    sfids = sorted(strat)
    reps = []
    for p in rep_paths:
        d = {}
        for r in csv.DictReader(open(p)):
            sf = _s(r.get("source_feature_id"))
            if sf == "":
                continue
            d[int(float(sf))] = r
        reps.append(d)
    units = {}
    for sf in sfids:
        st = strat[sf]
        intervals, years, undated, widths = [], [], [], []
        for rep in reps:
            row = rep.get(sf)
            if row is None:
                intervals.append(""); years.append(""); undated.append(None); widths.append(None); continue
            ak = _agree_key_raw(row)
            intervals.append(ak)
            undated.append(1 if ak == "UNDATED" else 0)
            years.append("" if ak == "UNDATED" else _year_b(row))
            widths.append(_width_b(row))
        units[sf] = {"group": sf, "stratum": st, "label": str(sf),
                     "intervals": intervals, "years": years, "undated": undated,
                     "widths": widths, "prod_key": prod_key[sf]}
    return units, len(reps)


# ----------------------------- per-arm aggregation -----------------------------
def analyze_arm(units: dict, nrep: int) -> dict:
    per_unit = []
    for k, u in units.items():
        modal_iv, iv_hit, _ = _mode_hit(u["intervals"])
        modal_yr, yr_hit, _ = _mode_hit([y for y in u["years"]])
        und = [x for x in u["undated"] if x is not None]
        undated_flip = (sum(und) / len(und)) if und else None
        wids = [w for w in u["widths"] if w is not None]
        per_unit.append({
            "unit": str(k), "stratum": u["stratum"],
            "modal_interval": modal_iv, "interval_mode_hit": round(iv_hit, 3),
            "tier": _tier(iv_hit),
            "modal_year": modal_yr, "year_mode_hit": round(yr_hit, 3) if u["years"] else None,
            "undated_flip_rate": round(undated_flip, 3) if undated_flip is not None else None,
            "median_width_months": (sorted(wids)[len(wids) // 2] if wids else None),
            "intervals_reps": "|".join(u["intervals"]),
            "prod_key": u.get("prod_key", ""),
        })

    # overall + per-stratum tier counts
    def tier_counts(rows):
        c = Counter(r["tier"] for r in rows)
        return {"rock_solid": c.get("rock_solid", 0), "wobbly": c.get("wobbly", 0),
                "chaotic": c.get("chaotic", 0), "n": len(rows)}

    by_stratum = {}
    for st in sorted({r["stratum"] for r in per_unit}):
        rows = [r for r in per_unit if r["stratum"] == st]
        yhits = [r["year_mode_hit"] for r in rows if r["year_mode_hit"] is not None]
        uflips = [r["undated_flip_rate"] for r in rows if r["undated_flip_rate"] is not None]
        ivhits = [r["interval_mode_hit"] for r in rows]
        by_stratum[st] = {
            **tier_counts(rows),
            "mean_interval_mode_hit": round(sum(ivhits) / len(ivhits), 3) if ivhits else None,
            "mean_year_mode_hit": round(sum(yhits) / len(yhits), 3) if yhits else None,
            "mean_undated_flip": round(sum(uflips) / len(uflips), 3) if uflips else None,
        }
    overall = tier_counts(per_unit)
    ivh = [r["interval_mode_hit"] for r in per_unit]
    yh = [r["year_mode_hit"] for r in per_unit if r["year_mode_hit"] is not None]
    uf = [r["undated_flip_rate"] for r in per_unit if r["undated_flip_rate"] is not None]
    overall["mean_interval_mode_hit"] = round(sum(ivh) / len(ivh), 3) if ivh else None
    overall["mean_year_mode_hit"] = round(sum(yh) / len(yh), 3) if yh else None
    overall["mean_undated_flip"] = round(sum(uf) / len(uf), 3) if uf else None
    return {"n_reps": nrep, "overall": overall, "by_stratum": by_stratum, "per_unit": per_unit}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--arma-reps", type=Path, nargs="+", required=True)
    ap.add_argument("--sample-anchors", type=Path, required=True)
    ap.add_argument("--armb-reps", type=Path, nargs="+", required=True)
    ap.add_argument("--reference", type=Path, required=True)
    ap.add_argument("--out-dir", type=Path, required=True)
    a = ap.parse_args()
    a.out_dir.mkdir(parents=True, exist_ok=True)

    a_units, a_nrep = load_arm_a(a.arma_reps, a.sample_anchors)
    b_units, b_nrep = load_arm_b(a.armb_reps, a.reference)
    A = analyze_arm(a_units, a_nrep)
    B = analyze_arm(b_units, b_nrep)

    def degr(metric):
        av, bv = A["overall"].get(metric), B["overall"].get(metric)
        if av is None or bv is None:
            return None
        return round(bv - av, 3)

    report = {
        "experiment": "12-anchor x 10-rep two-arm install-interval reliability",
        "arm_A": "frozen review PNGs re-scored (L1 = LLM single-call randomness)",
        "arm_B": "end-to-end, L3 imagery frozen via shared prefetched vintage stack (L1 + L2 adaptive search)",
        "tier_def": ">=0.8 rock_solid / 0.4-0.7 wobbly / <0.4 chaotic (mode-interval hit over 10 reps)",
        "arm_A_n_reps": a_nrep, "arm_B_n_reps": b_nrep,
        "arm_A_overall": A["overall"], "arm_B_overall": B["overall"],
        "A_to_B_degradation": {
            "mean_interval_mode_hit": degr("mean_interval_mode_hit"),
            "mean_year_mode_hit": degr("mean_year_mode_hit"),
            "mean_undated_flip": degr("mean_undated_flip"),
            "note": "B-A; negative interval/year hit = adaptive search adds instability; positive undated_flip = more undated churn",
        },
        "arm_A_by_stratum": A["by_stratum"],
        "arm_B_by_stratum": B["by_stratum"],
    }
    (a.out_dir / "mini_reliability_summary.json").write_text(json.dumps(report, indent=2))

    # per-unit CSVs
    for arm, res in (("A", A), ("B", B)):
        with open(a.out_dir / f"per_unit_arm_{arm}.csv", "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(res["per_unit"][0].keys()))
            w.writeheader(); w.writerows(res["per_unit"])

    # markdown
    L = ["# 12-anchor x 10-rep install-interval reliability (A vs B)", ""]
    L += [f"- Arm A (L1 only, frozen PNG): {a_nrep} reps, {A['overall']['n']} units (group_anchor x target)",
          f"- Arm B (L1+L2, L3 frozen): {b_nrep} reps, {B['overall']['n']} units (installations)",
          f"- Tier: {report['tier_def']}", ""]
    L += ["## Overall tier distribution (mode-interval hit)", "",
          "| arm | n | rock-solid (>=8/10) | wobbly (4-7) | chaotic (<4) | mean iv-hit | mean yr-hit | mean undated-flip |",
          "|---|--:|--:|--:|--:|--:|--:|--:|"]
    for arm, res in (("A", A["overall"]), ("B", B["overall"])):
        L.append(f"| {arm} | {res['n']} | {res['rock_solid']} | {res['wobbly']} | {res['chaotic']} "
                 f"| {res['mean_interval_mode_hit']} | {res['mean_year_mode_hit']} | {res['mean_undated_flip']} |")
    d = report["A_to_B_degradation"]
    L += ["", "## A -> B degradation (adaptive-search amplifier)", "",
          f"- mean interval-mode-hit: {d['mean_interval_mode_hit']} (B-A)",
          f"- mean year-mode-hit: {d['mean_year_mode_hit']} (B-A)",
          f"- mean undated-flip: {d['mean_undated_flip']} (B-A)", ""]
    for arm, res in (("A", A), ("B", B)):
        L += [f"## Arm {arm} by status_stratum", "",
              "| stratum | n | rock-solid | wobbly | chaotic | mean iv-hit | mean yr-hit | mean undated-flip |",
              "|---|--:|--:|--:|--:|--:|--:|--:|"]
        for st, s in sorted(res["by_stratum"].items(), key=lambda kv: -kv[1]["n"]):
            L.append(f"| {st} | {s['n']} | {s['rock_solid']} | {s['wobbly']} | {s['chaotic']} "
                     f"| {s['mean_interval_mode_hit']} | {s['mean_year_mode_hit']} | {s['mean_undated_flip']} |")
        L.append("")
    (a.out_dir / "mini_reliability_table.md").write_text("\n".join(L) + "\n")

    print(json.dumps({"arm_A": A["overall"], "arm_B": B["overall"],
                      "A_to_B_degradation": report["A_to_B_degradation"]}, indent=2))
    print(f"\nwrote {a.out_dir}/mini_reliability_summary.json + table.md + per_unit_arm_{{A,B}}.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
