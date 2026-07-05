#!/usr/bin/env python3
"""Analyze the full-stack no-search L1 reliability run.

Stitches per-frame ``pv_present`` verdicts (``long_all.csv`` from
``fullstack_noscan_run.py``) into a per-rep install-date for each target, then
measures rep-to-rep reproducibility the same way the 2026-06-24 two-arm test did
(mode-interval hit / year hit / undated flip, bucketed rock_solid>=0.8 /
wobbly 0.4-0.7 / chaotic<0.4) so the numbers line up directly against Arm A
(frozen 8-frame sequence, L1-only) and Arm B (adaptive search, L3 frozen).

Two install signals per rep:
  - first_present_date (FPD): first vintage scored pv_present -- Arm A parity.
  - first_sustained:          first pv_present date from which >=50% of later
                              non-abstain frames are present (rejects isolated
                              single-frame false-positives a dense grid surfaces).

Also emits a per-frame flip table: for each (chip, capture_date), the fraction
of reps disagreeing with the modal verdict -- localizes residual L1 noise to
specific ambiguous vintages (the lever for per-vintage majority voting).
"""
from __future__ import annotations

import argparse
import ast
import csv
import json
from collections import Counter, defaultdict
from pathlib import Path


def _tier(h: float) -> str:
    return "rock_solid" if h >= 0.8 else ("wobbly" if h >= 0.4 else "chaotic")


def _mode_hit(values: list[str]) -> tuple[str, float]:
    vals = [v for v in values if v != ""]
    if not vals:
        return "", 0.0
    modal, n = Counter(vals).most_common(1)[0]
    return modal, n / len(values)  # denom = all reps (missing counts against)


def derive_install(profile: list[tuple[str, str]]) -> tuple[str, str, bool]:
    """profile = sorted [(capture_date, pv_present '1'/'0'/'')]. Returns
    (first_present_date, first_sustained_date, undated)."""
    fpd = ""
    for d, pv in profile:
        if pv == "1":
            fpd = d
            break
    # first sustained
    fsd = ""
    n = len(profile)
    for i, (d, pv) in enumerate(profile):
        if pv != "1":
            continue
        later = [p for _, p in profile[i:] if p in ("0", "1")]
        if later and sum(1 for p in later if p == "1") / len(later) >= 0.5:
            fsd = d
            break
    return fpd, fsd, (fpd == "")


def load_long(path: Path):
    # unit = (chip_id, anchor_id, target_label) ; per rep -> {date: pv}
    data: dict[tuple, dict[str, dict[str, str]]] = defaultdict(lambda: defaultdict(dict))
    chip_of: dict[tuple, str] = {}
    with path.open(newline="") as fh:
        for r in csv.DictReader(fh):
            unit = (r["chip_id"], r["anchor_id"], r["target_label"])
            chip_of[unit] = r["chip_id"]
            data[unit][r["rep"]][r["capture_date"]] = r["pv_present"]
    return data, chip_of


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--long", type=Path, required=True)
    ap.add_argument("--sample-anchors", type=Path, required=True)
    ap.add_argument("--arma-per-unit", type=Path, default=None)
    ap.add_argument("--armb-overall", type=Path, default=None, help="mini_reliability_summary.json")
    ap.add_argument("--out-dir", type=Path, required=True)
    a = ap.parse_args()
    a.out_dir.mkdir(parents=True, exist_ok=True)

    strat = {r["anchor_id"]: r["status_stratum"] for r in csv.DictReader(open(a.sample_anchors))}
    data, chip_of = load_long(a.long)

    # Arm A per-unit iv-hit for side-by-side (unit tuple string -> iv hit)
    arma = {}
    if a.arma_per_unit and a.arma_per_unit.exists():
        for r in csv.DictReader(open(a.arma_per_unit)):
            try:
                key = tuple(ast.literal_eval(r["unit"]))
            except Exception:
                continue
            arma[key] = float(r["interval_mode_hit"])

    per_unit = []
    frame_verdicts: dict[tuple, list[str]] = defaultdict(list)  # (chip,date) -> pv across reps
    for unit, reps in sorted(data.items()):
        chip = chip_of[unit]
        st = strat.get(chip, "unknown")
        fpd_list, year_list, undated_list, fsd_list, syear_list, sundated_list = [], [], [], [], [], []
        for rep, datemap in sorted(reps.items()):
            profile = sorted(datemap.items())
            for d, pv in profile:
                frame_verdicts[(chip, d)].append(pv)
            fpd, fsd, und = derive_install(profile)
            fpd_list.append("UNDATED" if und else f"FPD|{fpd}")
            fsd_list.append("UNDATED" if fsd == "" else f"FSD|{fsd}")
            year_list.append("" if und or len(fpd) < 4 else fpd[:4])
            syear_list.append("" if fsd == "" else fsd[:4])  # year off the SUSTAINED date
            undated_list.append(1 if und else 0)
            sundated_list.append(1 if fsd == "" else 0)
        _, iv_hit = _mode_hit(fpd_list)
        modal_fsd, fsd_hit = _mode_hit(fsd_list)
        modal_yr, yr_hit = _mode_hit(year_list)
        modal_syr, syr_hit = _mode_hit(syear_list)
        fpd_flip = sum(undated_list) / len(undated_list) if undated_list else 0.0
        sustained_flip = sum(sundated_list) / len(sundated_list) if sundated_list else 0.0
        per_unit.append({
            "unit": str(unit), "chip_id": chip, "stratum": st,
            "n_reps": len(reps),
            "interval_mode_hit": round(iv_hit, 3),
            "sustained_mode_hit": round(fsd_hit, 3),
            "tier": _tier(iv_hit),
            "modal_year": modal_yr, "year_mode_hit": round(yr_hit, 3),
            "modal_sustained_year": modal_syr, "sustained_year_mode_hit": round(syr_hit, 3),
            "fpd_undated_flip_rate": round(fpd_flip, 3),
            "sustained_undated_flip_rate": round(sustained_flip, 3),
            "arma_interval_hit": arma.get(unit, ""),
            "fpd_reps": "|".join(fpd_list),
        })

    # per-frame flip
    frame_rows = []
    for (chip, d), verds in sorted(frame_verdicts.items()):
        non_blank = [v for v in verds if v in ("0", "1")]
        if not non_blank:
            continue
        modal, mn = Counter(non_blank).most_common(1)[0]
        flip = 1 - mn / len(non_blank)
        frame_rows.append({
            "chip_id": chip, "capture_date": d, "n_reps": len(verds),
            "n_present": sum(1 for v in verds if v == "1"),
            "n_absent": sum(1 for v in verds if v == "0"),
            "n_abstain": sum(1 for v in verds if v not in ("0", "1")),
            "modal": modal, "flip_rate": round(flip, 3),
        })

    def tier_counts(rows):
        c = Counter(r["tier"] for r in rows)
        return {"n": len(rows), "rock_solid": c.get("rock_solid", 0),
                "wobbly": c.get("wobbly", 0), "chaotic": c.get("chaotic", 0)}

    def means(rows):
        n = len(rows) or 1
        return {
            "mean_interval_mode_hit": round(sum(r["interval_mode_hit"] for r in rows) / n, 3),
            "mean_sustained_mode_hit": round(sum(r["sustained_mode_hit"] for r in rows) / n, 3),
            "mean_year_mode_hit": round(sum(r["year_mode_hit"] for r in rows) / n, 3),
            "mean_sustained_year_mode_hit": round(sum(r["sustained_year_mode_hit"] for r in rows) / n, 3),
            "mean_fpd_undated_flip": round(sum(r["fpd_undated_flip_rate"] for r in rows) / n, 3),
            "mean_sustained_undated_flip": round(sum(r["sustained_undated_flip_rate"] for r in rows) / n, 3),
        }

    overall = {**tier_counts(per_unit), **means(per_unit)}
    by_stratum = {}
    for st in sorted({r["stratum"] for r in per_unit}):
        rows = [r for r in per_unit if r["stratum"] == st]
        by_stratum[st] = {**tier_counts(rows), **means(rows)}

    # Arm A/B reference (published overall from the 2026-06-24 table)
    ref = {"arm_A": {"mean_iv": 0.875, "mean_yr": 0.768, "rock": "20/28"},
           "arm_B": {"mean_iv": 0.771, "mean_yr": 0.411, "rock": "14/28"}}
    if a.armb_overall and a.armb_overall.exists():
        j = json.loads(a.armb_overall.read_text())
        ref["arm_A"] = j.get("arm_A_overall", ref["arm_A"])
        ref["arm_B"] = j.get("arm_B_overall", ref["arm_B"])

    arma_units = [r for r in per_unit if r["arma_interval_hit"] != ""]
    paired = None
    if arma_units:
        n = len(arma_units)
        fs = sum(r["interval_mode_hit"] for r in arma_units) / n
        aa = sum(float(r["arma_interval_hit"]) for r in arma_units) / n
        paired = {"n": n, "fullstack_mean_iv": round(fs, 3), "arma_mean_iv": round(aa, 3),
                  "delta": round(fs - aa, 3)}

    flip_hi = sorted(frame_rows, key=lambda r: -r["flip_rate"])[:15]
    summary = {
        "experiment": "full-stack no-search L1 reliability (window=8 sequence, K reps)",
        # DIAGNOSTIC-ONLY caliber: the mode-hit / year-mode-hit numbers below are
        # hard-MAP point-date metrics, RETIRED as the production install-year
        # caliber (PRD-AMENDMENT-P1, ISSUE-22). Production caliber = fractional /
        # survival year mass; these are reproducibility diagnostics only.
        "caliber_note": "diagnostic_only_hard_MAP_retired_PRD-AMENDMENT-P1_ISSUE-22",
        "n_units": len(per_unit),
        "overall": overall,
        "by_stratum": by_stratum,
        "reference_2026_06_24": ref,
        "paired_vs_armA": paired,
        "per_frame": {
            "n_frames": len(frame_rows),
            "frames_with_any_flip": sum(1 for r in frame_rows if r["flip_rate"] > 0),
            "mean_flip_rate": round(sum(r["flip_rate"] for r in frame_rows) / (len(frame_rows) or 1), 4),
        },
    }
    (a.out_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    with open(a.out_dir / "per_unit_fullstack.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(per_unit[0].keys())); w.writeheader(); w.writerows(per_unit)
    with open(a.out_dir / "per_frame_flip.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(frame_rows[0].keys())); w.writeheader(); w.writerows(frame_rows)

    # markdown
    L = ["# Full-stack no-search L1 reliability (window=8, K reps)", "",
         "_Diagnostic-only caliber: hard-MAP point-date mode-hit metrics, retired as "
         "the production install-year caliber per PRD-AMENDMENT-P1 (ISSUE-22); "
         "production caliber = fractional/survival year mass._", "",
         f"- units (chip x target): {len(per_unit)}   frames scored: {len(frame_rows)}", "",
         "## Overall vs 2026-06-24 two-arm baseline", "",
         "| run | n | rock-solid | wobbly | chaotic | mean iv-hit | mean yr-hit | mean undated-flip |",
         "|---|--:|--:|--:|--:|--:|--:|--:|",
         f"| **full-stack · FPD** | {overall['n']} | {overall['rock_solid']} | {overall['wobbly']} | "
         f"{overall['chaotic']} | {overall['mean_interval_mode_hit']} | {overall['mean_year_mode_hit']} | "
         f"{overall['mean_fpd_undated_flip']} |",
         f"| **full-stack · sustained** | {overall['n']} | - | - | - "
         f"| {overall['mean_sustained_mode_hit']} | {overall['mean_sustained_year_mode_hit']} | "
         f"{overall['mean_sustained_undated_flip']} |",
         f"| Arm A (frozen 8-frame, L1) | 28 | 20 | 8 | 0 | {ref['arm_A'].get('mean_iv', 0.875)} | "
         f"{ref['arm_A'].get('mean_yr', 0.768)} | - |",
         f"| Arm B (adaptive, L3 frozen) | 28 | 14 | 14 | 0 | {ref['arm_B'].get('mean_iv', 0.771)} | "
         f"{ref['arm_B'].get('mean_yr', 0.411)} | - |", ""]
    if paired:
        L += [f"- paired vs Arm A on {paired['n']} shared units: full-stack iv-hit "
              f"{paired['fullstack_mean_iv']} vs Arm A {paired['arma_mean_iv']} "
              f"(delta {paired['delta']:+})", ""]
    L += [f"- sustained estimator: date-hit {overall['mean_sustained_mode_hit']} (vs FPD "
          f"{overall['mean_interval_mode_hit']}), year-hit {overall['mean_sustained_year_mode_hit']} "
          f"(vs FPD {overall['mean_year_mode_hit']})", "",
          "## By status stratum (full-stack)", "",
          "| stratum | n | rock | wob | chaos | mean iv | mean yr | FPD flip | sus flip |",
          "|---|--:|--:|--:|--:|--:|--:|--:|--:|"]
    for st, s in sorted(by_stratum.items(), key=lambda kv: -kv[1]["n"]):
        L.append(f"| {st} | {s['n']} | {s['rock_solid']} | {s['wobbly']} | {s['chaotic']} "
                 f"| {s['mean_interval_mode_hit']} | {s['mean_year_mode_hit']} "
                 f"| {s['mean_fpd_undated_flip']} | {s['mean_sustained_undated_flip']} |")
    L += ["", "## Top per-frame flips (residual L1 noise, vote targets)", "",
          "| chip | date | present | absent | abstain | flip_rate |", "|---|---|--:|--:|--:|--:|"]
    for r in flip_hi:
        L.append(f"| {r['chip_id'][-9:]} | {r['capture_date']} | {r['n_present']} | {r['n_absent']} "
                 f"| {r['n_abstain']} | {r['flip_rate']} |")
    (a.out_dir / "table.md").write_text("\n".join(L) + "\n")

    print(json.dumps(summary, indent=2))
    print(f"\nwrote {a.out_dir}/summary.json + table.md + per_unit_fullstack.csv + per_frame_flip.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
