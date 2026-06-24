#!/usr/bin/env python3
"""Splice ONE replica's rerun layer outputs into copies of the production layer CSVs, then
run the REAL production merge + flatten so the replica gets a true production-grade delivery.

Why splice instead of a minimal merge: ``merge_three_layers.py`` is the production
post-processing (precedence L_census > L0 > L1 > already-present-bound, per-grid Vexcel
present-clamp, midpoint recompute). To reproduce it 1:1 we run that exact script, only
swapping the sample's layer rows for the replica's rerun values. The merge processes the
full 41,393-polygon inventory; we read back only the 642 sampled installations.

Splice rule per layer (start from the production FULL csv, overwrite the sample's rows):
  L0  install_intervals.csv               replace rows whose anchor_id is in the replica L0 run
  L1  recovered_polygons_pertarget.csv    drop sample sfids, add the replica L1 recovered rows
  Lc  census_install_intervals.csv        replace rows whose anchor_id is in the replica census run
  per_target_anchors.csv                  copied unchanged (merge uses it only for t-id->sfid)

The merge script is copied with its ``SCAN`` constant repointed at the spliced dir; all
static inputs (chip_targets, source gpkg, Vexcel capture dates) stay at the originals.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pandas as pd

HOME = Path(os.path.expanduser("~"))
PROD_SCAN = HOME / "zasolar_data/geid_temporal/jhb_full382_fpcut_scan_2026-06-02"
SUBREPO = Path(__file__).resolve().parents[2]

PROD_MAIN_IV = PROD_SCAN / "install_intervals.csv"
PROD_RECOVERED = PROD_SCAN / "norecent_pertarget/recovered_polygons_pertarget.csv"
PROD_PER_TARGET = PROD_SCAN / "norecent_pertarget/per_target_anchors.csv"
PROD_CENSUS = PROD_SCAN / "census2023/census_install_intervals.csv"
PROD_MERGE = PROD_SCAN / "merge_three_layers.py"
FLATTEN = PROD_SCAN / "flatten_gpkg_to_csv.py"

PROD_SCAN_LINE = f'SCAN = f"{{HOME}}/zasolar_data/geid_temporal/jhb_full382_fpcut_scan_2026-06-02"'


def _replace_rows(prod_path: Path, rep_path: Path, key: str, rep_key_subset_msg: str) -> pd.DataFrame:
    prod = pd.read_csv(prod_path, dtype=str)
    rep = pd.read_csv(rep_path, dtype=str) if rep_path.exists() and rep_path.stat().st_size else prod.iloc[0:0]
    if len(rep) and set(rep.columns) != set(prod.columns):
        sys.exit(f"column mismatch splicing {rep_path.name}: "
                 f"prod-only={set(prod.columns)-set(rep.columns)} rep-only={set(rep.columns)-set(prod.columns)}")
    rep = rep[list(prod.columns)] if len(rep) else rep
    rep_keys = set(rep[key]) if len(rep) else set()
    kept = prod[~prod[key].isin(rep_keys)]
    out = pd.concat([kept, rep], ignore_index=True)
    print(f"  splice {prod_path.name}: prod={len(prod)} - replaced={len(prod)-len(kept)} + rep={len(rep)} = {len(out)} ({rep_key_subset_msg})")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--reference", type=Path, required=True)
    ap.add_argument("--rep-dir", type=Path, required=True, help="replica root (contains L0/, L1/, L_census/).")
    a = ap.parse_args()

    ref = pd.read_csv(a.reference, dtype=str)
    sample_sfids = set(ref["source_feature_id"].astype(str))

    rep = a.rep_dir
    rep_l0_iv = rep / "L0/install_intervals.csv"
    rep_l1_recovered = rep / "L1/norecent_pertarget/recovered_polygons_pertarget.csv"
    rep_census = rep / "L_census/census_install_intervals.csv"

    merge_scan = rep / "merge_scan"
    (merge_scan / "norecent_pertarget").mkdir(parents=True, exist_ok=True)
    (merge_scan / "census2023").mkdir(parents=True, exist_ok=True)

    print("splicing replica layer outputs into production copies:")
    # L0
    _replace_rows(PROD_MAIN_IV, rep_l0_iv, "anchor_id",
                  "244 sample group anchors").to_csv(merge_scan / "install_intervals.csv", index=False)
    # L1 recovered: drop sample sfids, add rep rows (rep sfids subset of sample by construction)
    prod_rec = pd.read_csv(PROD_RECOVERED, dtype=str)
    rep_rec = (pd.read_csv(rep_l1_recovered, dtype=str)
               if rep_l1_recovered.exists() and rep_l1_recovered.stat().st_size else prod_rec.iloc[0:0])
    if len(rep_rec):
        rep_rec = rep_rec[list(prod_rec.columns)]
    kept_rec = prod_rec[~prod_rec["source_feature_id"].astype(str).isin(sample_sfids)]
    out_rec = pd.concat([kept_rec, rep_rec], ignore_index=True)
    out_rec.to_csv(merge_scan / "norecent_pertarget/recovered_polygons_pertarget.csv", index=False)
    print(f"  splice recovered_polygons_pertarget.csv: prod={len(prod_rec)} - sample_dropped="
          f"{len(prod_rec)-len(kept_rec)} + rep={len(rep_rec)} = {len(out_rec)}")
    # per_target_anchors: unchanged (t-id->sfid map for census/wayback dispatch)
    shutil.copy2(PROD_PER_TARGET, merge_scan / "norecent_pertarget/per_target_anchors.csv")
    # L_census
    census_out = merge_scan / "census2023/census_install_intervals.csv"
    _replace_rows(PROD_CENSUS, rep_census, "anchor_id",
                  "sample cohort anchors").to_csv(census_out, index=False)

    # ---- copy merge script with SCAN repointed ----
    merge_src = PROD_MERGE.read_text()
    if PROD_SCAN_LINE not in merge_src:
        sys.exit("could not find the SCAN constant line to repoint in merge_three_layers.py")
    merge_dst = merge_scan / "merge_three_layers.py"
    merge_dst.write_text(merge_src.replace(PROD_SCAN_LINE, f'SCAN = "{merge_scan}"'))

    out_gpkg = rep / "delivery.gpkg"
    out_csv = rep / "delivery.csv"

    print("\nrunning REAL merge (repointed SCAN, --with-census):")
    r = subprocess.run([sys.executable, str(merge_dst), "--with-census",
                        "--census-csv", str(census_out), "--out-gpkg", str(out_gpkg)],
                       cwd=str(merge_scan))
    if r.returncode != 0:
        sys.exit(f"merge failed (rc={r.returncode})")

    print("\nflatten gpkg -> delivery csv:")
    r = subprocess.run([sys.executable, str(FLATTEN), "--gpkg", str(out_gpkg), "--out-csv", str(out_csv)])
    if r.returncode != 0:
        sys.exit(f"flatten failed (rc={r.returncode})")

    print(f"\nreplica delivery: {out_csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
