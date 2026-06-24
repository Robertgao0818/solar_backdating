#!/usr/bin/env python3
"""No-API re-cuts of the LLM test-retest run (k=3 identical-input reps).

Reuses the already-saved per-target agreement table
(`results/analysis/llm_consistency/reliability/reliability_targets.csv`, written by
`llm_reliability_analyze.py`) plus the raw `score_rep{1,2,3}.csv` for the sequence
confidence / consistency flags. Computes — WITHOUT any new API call — the four
re-cuts the §C refinement needs:

  1. Majority-of-3 recovery: 3-agree vs 2-1 split (majority recoverable) vs
     1-1-1 three-way split (no recoverable answer).
  2. Date-disagreement decomposition in window steps (one imagery step vs >=2),
     and presence-flip (date <-> none) vs date-only shift.
  3. Abstain ("?") behaviour: how many cells the model leaves uncertain and how
     much of the rep-to-rep flipping rides on abstain tokens (not 0<->1 flips).
  4. Where the noise concentrates: disagreement rate vs sequence confidence and
     vs the monotonic/non-monotonic consistency flag.

Also asserts the structural fact that, on a year-spaced window, install-date
agreement == install-year agreement (the 8 window dates fall in 8 distinct years),
so coarsening to year cannot buy individual stability.

Outputs: <out-dir>/reliability_recut.json and reliability_recut.md
"""
from __future__ import annotations

import argparse
import csv
import json
import statistics as stats
from collections import Counter, defaultdict
from pathlib import Path

# The frozen 8-date window (also in reliability_summary.json -> dates_window).
WINDOW = [
    "2018-06-30", "2019-06-30", "2020-06-30", "2021-06-30",
    "2022-06-30", "2023-06-30", "2024-06-30", "2025-02-28",
]
IDX = {d: i for i, d in enumerate(WINDOW)}
NONE = "NONE"  # canonical token for "no first-present date" (empty string)


def _norm(d: str) -> str:
    d = (d or "").strip()
    return d if d else NONE


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--targets-csv", type=Path, required=True,
                    help="reliability_targets.csv from llm_reliability_analyze.py")
    ap.add_argument("--run-dir", type=Path, default=None,
                    help="run dir holding score_rep{1,2,3}.csv (for confidence join)")
    ap.add_argument("--out-dir", type=Path, required=True)
    args = ap.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)

    rows = list(csv.DictReader(open(args.targets_csv)))

    # ---- optional join: sequence confidence + consistency flag from rep1 ----
    conf_by_key: dict[tuple, float] = {}
    cflag_by_key: dict[tuple, str] = {}
    if args.run_dir:
        rep1 = args.run_dir / "score_rep1.csv"
        if rep1.exists():
            for r in csv.DictReader(open(rep1)):
                key = (r.get("chip_id", ""), r.get("target_label", ""))
                try:
                    conf_by_key[key] = float(r.get("confidence", "") or "nan")
                except ValueError:
                    pass
                cflag_by_key[key] = r.get("consistency_flag", "")

    # ---------------- accumulators ----------------
    n = 0
    agree3 = 0            # all 3 reps identical first-present-date
    split21 = 0          # 2 agree, 1 differs -> majority recoverable
    split111 = 0         # all 3 differ -> no majority
    year_eq_date = 0     # year-agreement matches date-agreement (sanity)

    # disagreement geometry (date-only, both endpoints are real dates)
    date_only_disagree = 0
    presence_flip = 0    # at least one rep = NONE and at least one = a real date
    step_spread = Counter()  # max-min window-index gap among disagreers (real dates)
    one_step = 0
    multi_step = 0

    # abstain / pattern-cell behaviour
    total_cells = 0
    cells_with_any_abstain = 0     # cells where >=1 rep emitted "?"
    cells_flipped = 0              # cells not identical across the 3 reps
    cells_flipped_pure01 = 0       # flipped with NO "?" involved (true 0<->1)
    cells_flipped_with_abstain = 0 # flipped where >=1 rep was "?"
    abstain_token_count = 0        # total "?" tokens across reps x cells
    n_with_any_abstain = 0         # targets where any rep abstained on any cell

    # concentration vs confidence / consistency
    conf_bucket = defaultdict(lambda: {"n": 0, "disagree": 0})
    cflag_bucket = defaultdict(lambda: {"n": 0, "disagree": 0})
    conf_disagree, conf_agree = [], []

    majority_dates = []  # denoised (majority-of-3) first-present-date per target

    for row in rows:
        n += 1
        fpds = [_norm(x) for x in row["fpd_reps"].split("|")]
        pats = [p for p in row["pattern_reps"].split("|")]
        key = (row["group"], row["target_label"])

        # ---- majority-of-3 on the date ----
        cnt = Counter(fpds)
        top_val, top_n = cnt.most_common(1)[0]
        distinct = len(cnt)
        date_ident = (distinct == 1)
        if distinct == 1:
            agree3 += 1
            majority_dates.append(top_val)
        elif distinct == 2:
            split21 += 1
            majority_dates.append(top_val)  # the value that appears twice
        else:
            split111 += 1
            majority_dates.append(None)     # no recoverable answer

        # ---- year == date check (structural, but verify empirically) ----
        years = [(NONE if v == NONE else v[:4]) for v in fpds]
        year_ident = (len(set(years)) == 1)
        if year_ident == date_ident:
            year_eq_date += 1

        # ---- disagreement geometry ----
        if not date_ident:
            reals = [v for v in fpds if v != NONE]
            has_none = any(v == NONE for v in fpds)
            if has_none and reals:
                presence_flip += 1
            if len(reals) == 3 or (len(reals) >= 2 and not has_none):
                # all endpoints real -> measure window-step spread
                idxs = [IDX[v] for v in reals if v in IDX]
                if len(idxs) >= 2:
                    spread = max(idxs) - min(idxs)
                    step_spread[spread] += 1
                    date_only_disagree += 1
                    if spread == 1:
                        one_step += 1
                    else:
                        multi_step += 1

        # ---- pattern-cell / abstain behaviour ----
        grids = [p.split("-") for p in pats if p]
        if len(grids) == 3:
            m = min(len(g) for g in grids)
            tgt_has_abstain = False
            for i in range(m):
                col = [grids[0][i], grids[1][i], grids[2][i]]
                total_cells += 1
                has_abstain = any(c == "?" for c in col)
                if has_abstain:
                    cells_with_any_abstain += 1
                    tgt_has_abstain = True
                abstain_token_count += sum(1 for c in col if c == "?")
                if len(set(col)) != 1:  # flipped
                    cells_flipped += 1
                    if has_abstain:
                        cells_flipped_with_abstain += 1
                    else:
                        cells_flipped_pure01 += 1
            if tgt_has_abstain:
                n_with_any_abstain += 1

        # ---- concentration vs confidence / consistency ----
        c = conf_by_key.get(key)
        if c is not None and c == c:  # not NaN
            (conf_disagree if not date_ident else conf_agree).append(c)
            b = "conf>=0.9" if c >= 0.9 else ("0.8<=conf<0.9" if c >= 0.8 else "conf<0.8")
            conf_bucket[b]["n"] += 1
            conf_bucket[b]["disagree"] += int(not date_ident)
        cf = cflag_by_key.get(key)
        if cf:
            cflag_bucket[cf]["n"] += 1
            cflag_bucket[cf]["disagree"] += int(not date_ident)

    majority_well_defined = agree3 + split21

    def rate(a, b):
        return round(a / b, 4) if b else None

    out = {
        "experiment": "no-API re-cut of LLM test-retest (k=3 identical-input reps)",
        "source": str(args.targets_csv),
        "n_targets": n,
        "window_is_year_spaced": sorted({d[:4] for d in WINDOW}) == [d[:4] for d in WINDOW][:0] or
                                 len({d[:4] for d in WINDOW}) == len(WINDOW),
        "year_equals_date_agreement_for_all_targets": (year_eq_date == n),
        "majority_of_3": {
            "all_3_agree": agree3,
            "split_2_1_majority_recoverable": split21,
            "split_1_1_1_no_majority": split111,
            "p_all_3_agree": rate(agree3, n),
            "p_majority_well_defined": rate(majority_well_defined, n),
            "p_no_recoverable_answer": rate(split111, n),
            "note": ("A well-defined majority (>=2 of 3) exists for "
                     f"{rate(majority_well_defined, n)} of targets; only "
                     f"{rate(split111, n)} are irrecoverable 3-way splits. "
                     "Rigorously estimating the RELIABILITY of a majority-of-k "
                     "vote needs k>=5 (two independent majority draws) -- see Tier B."),
        },
        "date_disagreement_geometry": {
            "n_disagree": n - agree3,
            "presence_flip_date_vs_none": presence_flip,
            "date_only_disagree": date_only_disagree,
            "one_imagery_step": one_step,
            "ge_two_steps": multi_step,
            "p_one_step_of_date_only": rate(one_step, date_only_disagree),
            "step_spread_histogram": dict(sorted(step_spread.items())),
            "note": ("Window steps are ~12 months apart (last step 2024-06->2025-02 ~8 mo). "
                     "A 1-step flip == the median 12-month disagreement in the headline."),
        },
        "abstain_behaviour": {
            "total_cells": total_cells,
            "cells_flipped": cells_flipped,
            "p_cells_flipped": rate(cells_flipped, total_cells),
            "cells_flipped_pure_0_1": cells_flipped_pure01,
            "cells_flipped_involving_abstain": cells_flipped_with_abstain,
            "p_flips_riding_on_abstain": rate(cells_flipped_with_abstain, cells_flipped),
            "cells_with_any_abstain": cells_with_any_abstain,
            "abstain_token_count": abstain_token_count,
            "targets_with_any_abstain": n_with_any_abstain,
            "p_targets_with_any_abstain": rate(n_with_any_abstain, n),
        },
        "concentration": {
            "mean_seq_confidence_agree": round(stats.mean(conf_agree), 4) if conf_agree else None,
            "mean_seq_confidence_disagree": round(stats.mean(conf_disagree), 4) if conf_disagree else None,
            "disagree_rate_by_confidence_bucket": {
                b: {"n": v["n"], "disagree_rate": rate(v["disagree"], v["n"])}
                for b, v in sorted(conf_bucket.items())
            },
            "disagree_rate_by_consistency_flag": {
                b: {"n": v["n"], "disagree_rate": rate(v["disagree"], v["n"])}
                for b, v in sorted(cflag_bucket.items())
            },
        },
    }
    json.dump(out, open(args.out_dir / "reliability_recut.json", "w"), indent=2)

    # ---- markdown ----
    mo = out["majority_of_3"]; dg = out["date_disagreement_geometry"]; ab = out["abstain_behaviour"]
    md = [
        "# LLM reliability — no-API re-cuts (k=3 reps)",
        "",
        f"n = {n} installations; source `{args.targets_csv.name}`.",
        "",
        "## Majority-of-3 recovery",
        "",
        "| outcome | count | share |",
        "|---|--:|--:|",
        f"| all 3 reps agree | {mo['all_3_agree']} | {mo['p_all_3_agree']} |",
        f"| 2-1 split (majority recoverable) | {mo['split_2_1_majority_recoverable']} | "
        f"{rate(split21, n)} |",
        f"| 1-1-1 three-way split (no majority) | {mo['split_1_1_1_no_majority']} | "
        f"{mo['p_no_recoverable_answer']} |",
        f"| **majority well-defined (>=2/3)** | **{majority_well_defined}** | "
        f"**{mo['p_majority_well_defined']}** |",
        "",
        "## Date-disagreement geometry",
        "",
        f"- disagreeing targets: {dg['n_disagree']}",
        f"- presence flip (date <-> none): {dg['presence_flip_date_vs_none']}",
        f"- date-only shift, one imagery step (~12 mo): {dg['one_imagery_step']} "
        f"({dg['p_one_step_of_date_only']} of date-only)",
        f"- date-only shift, >=2 steps: {dg['ge_two_steps']}",
        f"- step-spread histogram (window-index gap): {dg['step_spread_histogram']}",
        "",
        "## Abstain behaviour",
        "",
        f"- cells flipped across reps: {ab['cells_flipped']}/{ab['total_cells']} "
        f"({ab['p_cells_flipped']})",
        f"- of those flips, share riding on an abstain '?': {ab['p_flips_riding_on_abstain']}",
        f"- targets with any abstain: {ab['targets_with_any_abstain']} "
        f"({ab['p_targets_with_any_abstain']})",
        "",
        "## Concentration",
        "",
        f"- mean seq-confidence, agree vs disagree: "
        f"{out['concentration']['mean_seq_confidence_agree']} vs "
        f"{out['concentration']['mean_seq_confidence_disagree']}",
        "",
        "| confidence bucket | n | disagree rate |",
        "|---|--:|--:|",
    ]
    for b, v in out["concentration"]["disagree_rate_by_confidence_bucket"].items():
        md.append(f"| {b} | {v['n']} | {v['disagree_rate']} |")
    md += ["", "| consistency flag | n | disagree rate |", "|---|--:|--:|"]
    for b, v in out["concentration"]["disagree_rate_by_consistency_flag"].items():
        md.append(f"| {b} | {v['n']} | {v['disagree_rate']} |")
    md += [
        "",
        "## Structural note",
        "",
        f"- year-spaced window (8 dates, 8 distinct years): "
        f"{out['window_is_year_spaced']}",
        f"- date-agreement == year-agreement for ALL {n} targets: "
        f"{out['year_equals_date_agreement_for_all_targets']} "
        "(so coarsening to year cannot raise individual reliability here).",
        "",
    ]
    Path(args.out_dir / "reliability_recut.md").write_text("\n".join(md) + "\n")
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
