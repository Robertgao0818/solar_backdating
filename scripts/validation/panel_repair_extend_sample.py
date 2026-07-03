#!/usr/bin/env python3
"""ISSUE-04 panel repair: deterministically EXTEND the done_appears stratum.

The banked reliability panel (mini_reliability_20260624, 12 anchors / 28 units)
allocated only n=2 units to ``done_appears`` — the stratum carrying ~69% of the
inventory weight. Per PRD D4 (replan v2), enlarge that stratum to ~8-10 units
before any cohort-wide estimator decision (D8 rule 5: no cohort decision from a
stratum with n=2 support).

Extension is a *continuation of the original draw*, not a fresh sample: the
mini panel's 2 done_appears anchors are exactly the first 2 elements of
``random.Random(42).shuffle(sorted(pool))`` (verified at runtime and recorded
in the manifest). We walk the SAME shuffled order from position 2, accumulating
anchors until the new-unit (target) count reaches ``--target-new-units``.
This keeps the extended stratum a valid simple random sample of the
done_appears population.

Outputs (under --out-dir):
  sample_anchors_new.csv       anchor_id, grid_id, status_stratum   (new anchors only)
  anchors_sample_new.csv       chip_groups_as_anchors.csv subset    (for prefetch/freeze)
  chip_targets_sample_new.csv  chip_targets.csv subset              (for prep/scorer)
  sample_anchors_extended.csv  banked 12 anchors + new rows         (for analyze)
  extend_manifest.json         provenance: seed check, picks, counts
"""
from __future__ import annotations

import argparse
import csv
import glob
import json
import random
from pathlib import Path

CHIPGROUPS = Path(
    "/home/gaosh/zasolar_data/geid_temporal/"
    "jhb_full382_unified_A_merge01_c0925_fpcut_2026-06-01_chipgroups"
)
SCAN_STATES = Path(
    "/home/gaosh/zasolar_data/geid_temporal/"
    "jhb_full382_fpcut_scan_2026-06-02/scan_states"
)
BANKED_SAMPLE = Path(
    "/home/gaosh/zasolar_data/geid_temporal/mini_reliability_20260624/sample"
)
STRATUM = "done_appears"


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
    ap.add_argument("--seed", type=int, default=42, help="must match the banked draw")
    ap.add_argument("--target-new-units", type=int, default=8,
                    help="accumulate anchors until this many NEW units (targets)")
    args = ap.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)

    status_by_anchor = load_status_by_anchor()
    pool = sorted(a for a, s in status_by_anchor.items() if s == STRATUM)
    rng = random.Random(args.seed)
    rng.shuffle(pool)

    # the banked panel's done_appears anchors MUST be the head of this order
    banked = {r["anchor_id"]: r["status_stratum"]
              for r in csv.DictReader(open(BANKED_SAMPLE / "sample_anchors.csv"))}
    banked_appears = sorted(a for a, s in banked.items() if s == STRATUM)
    head_ok = set(pool[: len(banked_appears)]) == set(banked_appears)
    if not head_ok:
        raise SystemExit(
            "Seed-42 shuffle head does not reproduce the banked done_appears picks "
            f"({[p[-9:] for p in pool[:2]]} vs {[p[-9:] for p in banked_appears]}); "
            "refusing to extend — sampling frame or seed changed."
        )

    # targets per chip-group (chip_targets.anchor_id is per-target; group id = chip_id)
    with open(CHIPGROUPS / "chip_targets.csv", newline="") as fh:
        tr = csv.DictReader(fh)
        target_fields = tr.fieldnames or []
        targets_by_chip: dict[str, list[dict]] = {}
        for r in tr:
            targets_by_chip.setdefault(r["chip_id"], []).append(r)

    picked: list[str] = []
    n_new_units = 0
    for aid in pool[len(banked_appears):]:
        tgts = targets_by_chip.get(aid, [])
        if not tgts:
            continue  # no geometry -> skip, keep walking the shuffled order
        picked.append(aid)
        n_new_units += len(tgts)
        if n_new_units >= args.target_new_units:
            break

    picked_set = set(picked)
    with open(CHIPGROUPS / "chip_groups_as_anchors.csv", newline="") as fh:
        ar = csv.DictReader(fh)
        anchor_fields = ar.fieldnames or []
        anchor_rows = [r for r in ar if r.get("anchor_id") in picked_set]
    missing = picked_set - {r["anchor_id"] for r in anchor_rows}
    if missing:
        raise SystemExit(f"anchors missing from chip_groups_as_anchors.csv: {missing}")

    target_rows = [r for aid in picked for r in targets_by_chip[aid]]
    grid_by_anchor = {r["anchor_id"]: r.get("grid_id", "") for r in anchor_rows}

    with open(args.out_dir / "anchors_sample_new.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=anchor_fields)
        w.writeheader(); w.writerows(anchor_rows)
    with open(args.out_dir / "chip_targets_sample_new.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=target_fields)
        w.writeheader(); w.writerows(target_rows)
    with open(args.out_dir / "sample_anchors_new.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["anchor_id", "grid_id", "status_stratum"])
        w.writeheader()
        for aid in picked:
            w.writerow({"anchor_id": aid, "grid_id": grid_by_anchor.get(aid, ""),
                        "status_stratum": STRATUM})
    # merged stratum map for fullstack_noscan_analyze on the extended panel
    with open(args.out_dir / "sample_anchors_extended.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["anchor_id", "grid_id", "status_stratum"])
        w.writeheader()
        for r in csv.DictReader(open(BANKED_SAMPLE / "sample_anchors.csv")):
            w.writerow(r)
        for aid in picked:
            w.writerow({"anchor_id": aid, "grid_id": grid_by_anchor.get(aid, ""),
                        "status_stratum": STRATUM})

    manifest = {
        "issue": "ISSUE-04 reliability panel repair (PRD D4)",
        "stratum": STRATUM,
        "seed": args.seed,
        "banked_head_reproduced": head_ok,
        "banked_done_appears_anchors": banked_appears,
        "population_done_appears": len(pool),
        "extension_start_position": len(banked_appears),
        "picked_anchors": picked,
        "n_new_anchors": len(picked),
        "n_new_units": n_new_units,
        "target_new_units": args.target_new_units,
        "chipgroups_dir": str(CHIPGROUPS),
        "scan_states_dir": str(SCAN_STATES),
        "banked_sample_dir": str(BANKED_SAMPLE),
    }
    json.dump(manifest, open(args.out_dir / "extend_manifest.json", "w"), indent=2)
    print(json.dumps(manifest, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
