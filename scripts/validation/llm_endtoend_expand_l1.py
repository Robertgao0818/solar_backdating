#!/usr/bin/env python3
"""Expand the per-target (L1) work list for ONE replica, from that replica's own L0 output.

Production routes a chip group to the per-target no-recent re-scan (L1) iff its L0 status is
``done_ambiguous_no_recent_anchor``. To keep L1 routing fully from-scratch (not pinned to
production's no-recent set), this rebuilds the L1 anchor list from THIS replica's L0
``install_intervals.csv``: every sample source_feature_id whose chip group is no-recent in
this replica becomes a per-target anchor, with geometry taken from ``chip_targets.csv``
(the same source production's ``per_target_anchors.csv`` was built from).

Output schema matches ``per_target_anchors.csv`` so it drops straight into
``run_adaptive_scan.py --anchors-csv`` and ``aggregate_recovery.py``.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path

import pandas as pd

HOME = Path(os.path.expanduser("~"))
CHIPGROUPS = HOME / "zasolar_data/geid_temporal/jhb_full382_unified_A_merge01_c0925_fpcut_2026-06-01_chipgroups"
CHIP_TARGETS = CHIPGROUPS / "chip_targets.csv"
PER_TARGET_ANCHORS = HOME / "zasolar_data/geid_temporal/jhb_full382_fpcut_scan_2026-06-02/norecent_pertarget/per_target_anchors.csv"

NO_RECENT_STATUS = "done_ambiguous_no_recent_anchor"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--reference", type=Path, required=True, help="reference.csv (642 sample installations).")
    ap.add_argument("--l0-intervals", type=Path, required=True, help="this replica's L0 install_intervals.csv.")
    ap.add_argument("--output", type=Path, required=True, help="per-target anchors CSV for this replica's L1.")
    a = ap.parse_args()

    ref = pd.read_csv(a.reference, dtype=str)
    sample_sfids = set(int(x) for x in ref["source_feature_id"])

    iv = pd.read_csv(a.l0_intervals, dtype=str)
    norecent_groups = set(iv.loc[iv["status"] == NO_RECENT_STATUS, "anchor_id"])

    ct = pd.read_csv(CHIP_TARGETS, dtype=str)
    schema = list(pd.read_csv(PER_TARGET_ANCHORS, dtype=str, nrows=0).columns)

    ct["_sf"] = ct["source_feature_id"].astype(int)
    sel = ct[ct["_sf"].isin(sample_sfids) & ct["chip_id"].isin(norecent_groups)].copy()
    out = sel[schema].copy()

    a.output.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(a.output, index=False)
    print(f"L1 expand: replica no-recent groups={len(norecent_groups)}, "
          f"sample per-target anchors={len(out)} -> {a.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
