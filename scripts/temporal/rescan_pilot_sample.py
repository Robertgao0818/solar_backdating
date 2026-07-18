#!/usr/bin/env python3
"""RUN 2 re-scan mini-pilot -- pre-registered sample (Step 1).

Fixed-seed sample of ~100 F1-decision-critical + ~50 F3-late-transition
anchors from the fingerprint CSVs built by rescan_pilot_fingerprints.py.
Writes one manifest CSV consumed by rescan_pilot_run.py.
"""

from __future__ import annotations

import argparse
import csv
import random
from pathlib import Path

SEED = 20260717
N_F1 = 100
N_F3 = 50


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fingerprints-dir", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    rng = random.Random(SEED)

    with (args.fingerprints_dir / "f1_critical.csv").open(newline="", encoding="utf-8") as fh:
        f1_rows = list(csv.DictReader(fh))
    with (args.fingerprints_dir / "f3_late_transition.csv").open(newline="", encoding="utf-8") as fh:
        f3_rows = list(csv.DictReader(fh))

    f1_sample = rng.sample(f1_rows, min(N_F1, len(f1_rows)))
    f3_sample = rng.sample(f3_rows, min(N_F3, len(f3_rows)))

    for r in f1_sample:
        r["fingerprint"] = "F1_critical"
    for r in f3_sample:
        r["fingerprint"] = "F3_late_transition"

    out_rows = f1_sample + f3_sample
    fieldnames = ["fingerprint", "anchor_id", "status", "chip_arm", "grid_id", "census_date",
                  "scan_state_path", "present_date", "absent_date", "first_absent_date",
                  "last_absent_date", "span_days", "gap_to_census_days"]
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for r in out_rows:
            row = {k: r.get(k, "") for k in fieldnames}
            writer.writerow(row)

    print(f"seed={SEED}: sampled {len(f1_sample)} F1_critical + {len(f3_sample)} F3_late_transition "
          f"= {len(out_rows)} anchors -> {args.out}")


if __name__ == "__main__":
    main()
