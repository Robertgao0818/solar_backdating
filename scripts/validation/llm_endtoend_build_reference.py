#!/usr/bin/env python3
"""Build the comparison ground-truth for the end-to-end backdating rerun robustness test.

This is the *reference* half of the experiment documented in
`ZAsolar/docs/validation/2026-06-21-validation-methodology.md`
("End-to-end reproducibility - the from-scratch rerun"). It does NO scoring and
NO API calls: it joins the §3 reliability stratified sample to the production
delivery so that, for every sampled installation, we know

  - which production layer actually dated it (date_provider), and
  - the install interval production actually shipped (the thing a rerun must
    reproduce, per the user's "之前实际生产得到的安装区间" definition).

ID lineage (verified against llm_reliability_sample.py):
  group anchor id  == scan_state.anchor_id == chip_groups_as_anchors.anchor_id
                   == chip_targets.csv `chip_id`  (NOT chip_targets.anchor_id)
  target anchor id == chip_targets.anchor_id  (`t...`)
  installation id  == source_feature_id (int) -> joins to the delivery CSV

Output: reference.csv, one row per sampled installation (642), plus a printed
coverage table (provider x stratum, inventory-weighted) so the rerun scope/cost
can be decided before any API spend.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

HOME = Path.home()
SCAN = HOME / "zasolar_data/geid_temporal/jhb_full382_fpcut_scan_2026-06-02"
SAMPLE = HOME / "zasolar_data/geid_temporal/llm_reliability_20260622/sample"
DELIVERY = SCAN / "jhb_full382_fpcut_install_dated_2026-06-05_with_census.csv"


def _install_year(row) -> str:
    """Production cohort year: prefer install_date mid; fall back to interval_end.

    Columns carry the ``prod_`` prefix at the point this runs (post-rename)."""
    for col in ("prod_install_date", "prod_install_interval_end", "prod_earliest_present_date"):
        v = row.get(col)
        if isinstance(v, str) and len(v) >= 4 and v[:4].isdigit():
            return v[:4]
    return ""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sample-dir", type=Path, default=SAMPLE)
    ap.add_argument("--scan-dir", type=Path, default=SCAN)
    ap.add_argument("--delivery-csv", type=Path, default=DELIVERY)
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()

    out = args.out or (HOME / "zasolar_data/geid_temporal/llm_endtoend_20260623/reference.csv")
    out.parent.mkdir(parents=True, exist_ok=True)

    manifest = json.loads((args.sample_dir / "sample_manifest.json").read_text())
    inv_w = manifest["inventory_weight"]

    samp = pd.read_csv(args.sample_dir / "sample_anchors.csv")  # group anchor -> stratum
    samp = samp.rename(columns={"anchor_id": "group_anchor_id", "grid_id": "grid_id_grp"})

    ct = pd.read_csv(args.sample_dir / "chip_targets_sample.csv")
    ct = ct.rename(columns={"chip_id": "group_anchor_id", "anchor_id": "target_anchor_id"})
    keep = ["group_anchor_id", "target_anchor_id", "source_feature_id", "grid_id",
            "centroid_lon", "centroid_lat", "source_area_m2"]
    ct = ct[[c for c in keep if c in ct.columns]]

    ref = ct.merge(samp[["group_anchor_id", "status_stratum"]], on="group_anchor_id", how="left")
    assert ref["status_stratum"].notna().all(), "stratum join failed (group_anchor_id mismatch)"

    fin = pd.read_csv(args.delivery_csv)
    fin_cols = ["source_feature_id", "date_provider", "date_status", "date_is_bound",
                "install_date", "install_interval_start", "install_interval_end",
                "earliest_present_date", "install_confidence", "undated_reason"]
    fin = fin[fin_cols].rename(columns=lambda c: c if c == "source_feature_id" else f"prod_{c}")
    ref = ref.merge(fin, on="source_feature_id", how="left")

    miss = ref["prod_date_provider"].isna() & ref["prod_undated_reason"].isna()
    n_missing = int(miss.sum())
    if n_missing:
        print(f"[WARN] {n_missing} sampled installations not found in delivery (will be flagged)")

    ref["prod_install_year"] = ref.apply(_install_year, axis=1)
    ref["inv_weight"] = ref["status_stratum"].map(inv_w)

    # canonical agreement key for "same install interval" (the experiment's unit)
    def agree_key(r):
        if isinstance(r.get("prod_undated_reason"), str) and r["prod_undated_reason"]:
            return "UNDATED"
        if r.get("prod_date_is_bound") == 1:
            return f"AP_BOUND<=|{r.get('prod_install_interval_end','')}"
        s, e = r.get("prod_install_interval_start", ""), r.get("prod_install_interval_end", "")
        return f"INTERVAL|{s}|{e}"
    ref["prod_agree_key"] = ref.apply(agree_key, axis=1)

    ref.to_csv(out, index=False)

    # ---- coverage report (decide rerun scope/cost from this) ----
    print(f"\nreference written: {out}  ({len(ref)} installations, "
          f"{ref.group_anchor_id.nunique()} groups)\n")
    print("=== production layer (date_provider) x stratum, installation counts ===")
    prov = ref["prod_date_provider"].fillna("UNDATED")
    print(pd.crosstab(ref["status_stratum"], prov, margins=True).to_string())
    print("\n=== installation counts + inventory weight by stratum ===")
    g = ref.groupby("status_stratum").agg(n_install=("source_feature_id", "count"),
                                          inv_weight=("inv_weight", "first"))
    print(g.to_string())
    print("\n=== which rerun LAYER each provider needs ===")
    layer_map = {"gehi_main": "L0 (run_adaptive_scan group + infer)",
                 "gehi_already_present_bound": "L0 (AP-bound, from infer)",
                 "gehi_pertarget": "L1 (run_adaptive_scan per-target + infer)",
                 "gehi_census2023": "L_census (run_census2023_scan)",
                 "UNDATED": "none (reproduce 'undated' verdict)"}
    pc = prov.value_counts()
    for p, n in pc.items():
        print(f"  {p:32s} {n:4d}  -> {layer_map.get(p, '??')}")
    print("\n=== production install-year cohort (sample, for aggregate TVD check) ===")
    yr = ref.loc[ref.prod_install_year != "", "prod_install_year"].value_counts().sort_index()
    print(yr.to_string())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
