#!/usr/bin/env python3
"""Build a stratified anchor sample for the LLM test-retest reliability experiment.

We measure how consistently the Gemini sequence scorer (the ONE stochastic step in
an otherwise-deterministic backdating pipeline) returns the same install-date and
the same temporal presence pattern when shown the SAME chips repeatedly.

This script only does local data wrangling (NO API):
  - reads the full-run scan_states/*.json to stratify anchors by outcome `status`
    (clean `done_appears` vs the ambiguous/hard strata where LLM stochasticity bites),
  - stratified-samples anchor_ids (oversampling the hard strata so their reliability
    is tightly estimated; the overall figure is re-weighted to the inventory later),
  - joins to the canonical chip geometry (chip_groups_as_anchors.csv / chip_targets.csv),
  - emits the gehi_download candidates + the scorer chip-targets subset, on a single
    fixed global date window (so all reps see identical inputs).

Outputs (under --out-dir):
  sample_anchors.csv        anchor_id, grid_id, status_stratum
  candidates.csv            anchor_id, region_key, grid_id, capture_date   (for gehi_download)
  anchors_sample.csv        subset of chip_groups_as_anchors.csv           (for gehi_download)
  chip_targets_sample.csv   subset of chip_targets.csv                     (for the scorer)
  sample_manifest.json      provenance: strata counts, dates, inventory weights
"""
from __future__ import annotations

import argparse
import csv
import glob
import json
import random
from collections import Counter, defaultdict
from pathlib import Path

CHIPGROUPS = Path(
    "/home/gaosh/zasolar_data/geid_temporal/"
    "jhb_full382_unified_A_merge01_c0925_fpcut_2026-06-01_chipgroups"
)
SCAN_STATES = Path(
    "/home/gaosh/zasolar_data/geid_temporal/"
    "jhb_full382_fpcut_scan_2026-06-02/scan_states"
)
REGION_KEY = "johannesburg"

# Fixed global date window: one set of target vintages applied to every anchor, with
# gehi --allow-nearest snapping to the closest available capture. Identical across reps.
DEFAULT_DATES = [
    "2018-06-30", "2019-06-30", "2020-06-30", "2021-06-30",
    "2022-06-30", "2023-06-30", "2024-06-30", "2025-02-28",
]

# Target sample per stratum (oversample the ambiguous/hard strata). Capped at availability.
DEFAULT_QUOTAS = {
    "done_appears": 60,
    "done_ambiguous_nonmonotonic": 60,
    "done_ambiguous_no_recent_anchor": 60,
    "done_installed_during_census": 25,
    "done_already_present_before_geid_history": 15,
    "done_ambiguous_gemini_failed": 24,
}


def load_status_by_anchor() -> dict[str, str]:
    out: dict[str, str] = {}
    for fp in glob.glob(str(SCAN_STATES / "*.json")):
        try:
            d = json.load(open(fp))
        except Exception:
            continue
        aid = d.get("anchor_id")
        if aid:
            out[aid] = d.get("status", "unknown")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out-dir", type=Path, required=True)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--dates", default=",".join(DEFAULT_DATES))
    args = ap.parse_args()

    rng = random.Random(args.seed)
    dates = [d.strip() for d in args.dates.split(",") if d.strip()]
    args.out_dir.mkdir(parents=True, exist_ok=True)

    status_by_anchor = load_status_by_anchor()
    pop_counts = Counter(status_by_anchor.values())
    total_pop = sum(pop_counts.values())

    by_status: dict[str, list[str]] = defaultdict(list)
    for aid, st in status_by_anchor.items():
        by_status[st].append(aid)

    # stratified sample (deterministic given seed)
    sampled: dict[str, str] = {}  # anchor_id -> stratum
    for stratum, quota in DEFAULT_QUOTAS.items():
        pool = sorted(by_status.get(stratum, []))
        rng.shuffle(pool)
        for aid in pool[:quota]:
            sampled[aid] = stratum

    sampled_ids = set(sampled)
    if not sampled_ids:
        raise SystemExit("No anchors sampled — check scan_states path.")

    # join geometry: anchors_sample (chip_groups_as_anchors) + chip_targets subset
    anchors_src = CHIPGROUPS / "chip_groups_as_anchors.csv"
    targets_src = CHIPGROUPS / "chip_targets.csv"

    with open(anchors_src, newline="") as fh:
        ar = csv.DictReader(fh)
        anchor_fields = ar.fieldnames or []
        anchor_rows = [r for r in ar if r.get("anchor_id") in sampled_ids]
    found_anchor_ids = {r["anchor_id"] for r in anchor_rows}
    missing = sampled_ids - found_anchor_ids
    if missing:
        # drop anchors lacking geometry rather than fail
        for aid in missing:
            sampled.pop(aid, None)
        sampled_ids = set(sampled)

    # NOTE: scan_state/chip_groups anchor_id == the GROUP id, which in chip_targets.csv
    # is the `chip_id` column (chip_targets.anchor_id is the per-target id). Filter on chip_id.
    with open(targets_src, newline="") as fh:
        tr = csv.DictReader(fh)
        target_fields = tr.fieldnames or []
        target_rows = [r for r in tr if r.get("chip_id") in sampled_ids]

    # write anchors_sample.csv
    with open(args.out_dir / "anchors_sample.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=anchor_fields)
        w.writeheader()
        w.writerows(anchor_rows)

    # write chip_targets_sample.csv
    with open(args.out_dir / "chip_targets_sample.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=target_fields)
        w.writeheader()
        w.writerows(target_rows)

    # write candidates.csv (anchor x date)
    grid_by_anchor = {r["anchor_id"]: r.get("grid_id", "") for r in anchor_rows}
    with open(args.out_dir / "candidates.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["anchor_id", "region_key", "grid_id", "capture_date"])
        w.writeheader()
        for aid in sorted(sampled_ids):
            for d in dates:
                w.writerow({"anchor_id": aid, "region_key": REGION_KEY,
                            "grid_id": grid_by_anchor.get(aid, ""), "capture_date": d})

    # write sample_anchors.csv (anchor -> stratum)
    with open(args.out_dir / "sample_anchors.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["anchor_id", "grid_id", "status_stratum"])
        w.writeheader()
        for aid in sorted(sampled_ids):
            w.writerow({"anchor_id": aid, "grid_id": grid_by_anchor.get(aid, ""),
                        "status_stratum": sampled[aid]})

    strata_counts = Counter(sampled.values())
    manifest = {
        "seed": args.seed,
        "dates": dates,
        "n_anchors_sampled": len(sampled_ids),
        "n_targets": len(target_rows),
        "strata_counts": dict(strata_counts),
        "population_status_counts": dict(pop_counts),
        "population_total": total_pop,
        # inventory weight per stratum (for re-weighting the overall reliability figure)
        "inventory_weight": {st: pop_counts.get(st, 0) / total_pop for st in strata_counts},
        "chipgroups_dir": str(CHIPGROUPS),
        "scan_states_dir": str(SCAN_STATES),
    }
    json.dump(manifest, open(args.out_dir / "sample_manifest.json", "w"), indent=2)
    print(json.dumps(manifest, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
