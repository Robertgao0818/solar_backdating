#!/usr/bin/env python3
"""Select the per-layer work lists for the end-to-end from-scratch reproducibility test.

This is the deterministic (no-API) front half of the end-to-end rerun. It takes the
frozen 642-installation / 244-anchor §3 stratified sample (via ``reference.csv``) and the
PRODUCTION routing artefacts, and emits the three candidate-pool CSVs that each rerun
replica feeds to the real production layer scripts:

  l0_anchors.csv          244 group anchors (chip_groups_as_anchors rows) -> run_adaptive_scan (L0)
  l1_anchors.csv          per_target_anchors rows for sample sfids in no-recent groups -> run_adaptive_scan (L1)
  census_cohort_sample.csv census2023_cohort rows mapping to the sample -> run_census2023_scan (L_census)

Faithfulness seam (documented here so the doc can cite it verbatim):
  We REUSE production's *routing membership* (which anchors are no-recent / in the 2023
  census cohort) as the fixed candidate pool, and RE-RUN every stochastic step within each
  layer (GEHI/Wayback fetch + render + Gemini scoring + infer + merge). The only fixed
  inputs are the 244-anchor sample itself and the static polygon inventory / chip_targets /
  cohort-frame mappings. Cases where a rerun's L0 status would change an installation's
  layer routing are not silently absorbed: ``llm_endtoend_analyze.py`` measures them as a
  separate routing-flip diagnostic.

No Gemini / GEHI calls. Run once; all replicas share the output.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import pandas as pd

HOME = Path(os.path.expanduser("~"))
SCAN = HOME / "zasolar_data/geid_temporal/jhb_full382_fpcut_scan_2026-06-02"
CHIPGROUPS = HOME / "zasolar_data/geid_temporal/jhb_full382_unified_A_merge01_c0925_fpcut_2026-06-01_chipgroups"

DEFAULT_REFERENCE = HOME / "zasolar_data/geid_temporal/llm_endtoend_20260623/reference.csv"
DEFAULT_OUTDIR = HOME / "zasolar_data/geid_temporal/llm_endtoend_20260623/work_lists"

CHIP_GROUPS_AS_ANCHORS = CHIPGROUPS / "chip_groups_as_anchors.csv"
PER_TARGET_ANCHORS = SCAN / "norecent_pertarget/per_target_anchors.csv"
CENSUS_COHORT = SCAN / "census2023/census2023_cohort.csv"
MAIN_INTERVALS = SCAN / "install_intervals.csv"

NO_RECENT_STATUS = "done_ambiguous_no_recent_anchor"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--reference", type=Path, default=DEFAULT_REFERENCE)
    ap.add_argument("--outdir", type=Path, default=DEFAULT_OUTDIR)
    a = ap.parse_args()

    ref = pd.read_csv(a.reference, dtype=str)
    sample_groups = set(ref["group_anchor_id"])
    sample_sfids = set(int(x) for x in ref["source_feature_id"])
    print(f"reference: {len(ref)} installations, {len(sample_groups)} group anchors, "
          f"{len(sample_sfids)} source_feature_ids")

    a.outdir.mkdir(parents=True, exist_ok=True)

    # ---- L0: group anchors (chip_groups_as_anchors rows) ----
    cga = pd.read_csv(CHIP_GROUPS_AS_ANCHORS, dtype=str)
    l0 = cga[cga["anchor_id"].isin(sample_groups)].copy()
    missing_groups = sample_groups - set(l0["anchor_id"])
    if missing_groups:
        print(f"  WARN: {len(missing_groups)} sample group anchors not found in "
              f"chip_groups_as_anchors (e.g. {sorted(missing_groups)[:3]})", file=sys.stderr)
    l0_path = a.outdir / "l0_anchors.csv"
    l0.to_csv(l0_path, index=False)
    print(f"L0  group anchors: {len(l0):>5}  -> {l0_path}")

    # ---- L1: per-target anchors for sample sfids in no-recent groups ----
    # production per_target_anchors already == members of no-recent L0 groups (minus a
    # production-side drop filter). Filtering by sfid keeps the production routing decision
    # intact while restricting to the sample.
    pta = pd.read_csv(PER_TARGET_ANCHORS, dtype=str)
    pta["_sf"] = pta["source_feature_id"].astype(int)
    l1 = pta[pta["_sf"].isin(sample_sfids)].drop(columns="_sf").copy()
    l1_path = a.outdir / "l1_anchors.csv"
    l1.to_csv(l1_path, index=False)
    n_groups_l1 = l1["chip_id"].nunique()
    print(f"L1  per-target anchors: {len(l1):>5}  (in {n_groups_l1} no-recent groups)  -> {l1_path}")

    # ---- L_census: cohort rows mapping to the sample ----
    coh = pd.read_csv(CENSUS_COHORT, dtype=str)
    # 'c' rows: anchor_id is the chip-group c-id -> keep if in sample groups
    # 't' rows: anchor_id is a per-target t-id -> keep if its sfid is in sample
    tid_to_sf = {r["anchor_id"]: int(r["source_feature_id"]) for _, r in pta.iterrows()}
    keep = []
    for _, r in coh.iterrows():
        aid = r["anchor_id"]
        if r["id_kind"] == "c":
            keep.append(aid in sample_groups)
        elif r["id_kind"] == "t":
            keep.append(tid_to_sf.get(aid, -1) in sample_sfids)
        else:
            keep.append(False)
    coh_s = coh[pd.Series(keep, index=coh.index)].copy()
    coh_path = a.outdir / "census_cohort_sample.csv"
    coh_s.to_csv(coh_path, index=False)
    print(f"L_c cohort rows: {len(coh_s):>5}  "
          f"(c={int((coh_s['id_kind']=='c').sum())}, t={int((coh_s['id_kind']=='t').sum())})  -> {coh_path}")

    # ---- provenance: how the sample's installations were dated IN PRODUCTION ----
    print("\nproduction layer coverage of the sample (prod_date_provider):")
    print(ref["prod_date_provider"].fillna("(undated)").value_counts().to_string())

    # cross-check: production no-recent groups among the sample (for the routing-flip baseline)
    miv = pd.read_csv(MAIN_INTERVALS, dtype=str)
    prod_norecent = set(miv.loc[miv["status"] == NO_RECENT_STATUS, "anchor_id"]) & sample_groups
    print(f"\nsample group anchors that were no-recent in production L0: {len(prod_norecent)}")
    print(f"wrote work lists to {a.outdir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
