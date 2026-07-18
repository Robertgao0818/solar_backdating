#!/usr/bin/env python3
"""RUN 2 re-scan mini-pilot -- extra draw for the A5 stale-offset addendum
(2026-07-18 mid-flight critical update).

anchors_all.csv's target_offset_x_m/y_m is stale (computed against the
legacy group-chip center; the rebuilt per-target 96m chips are centered on
each target's own centroid instead), so a large |offset| means the
production review crop was centered meters away from where the target
actually is. This population (|offset| > 12m -- beyond half the 24m A24
crop -- AND status == done_installed_during_census, the single largest and
least-audited bucket per the parallax audit) is exactly where a placement
fix should matter most, and it is NOT reachable by the F1/F3 fingerprint
sample (F1/F3 both require at least one confident-present observation to
exist; done_installed_during_census by construction has none -- see the
parallax audit memo section 0).

Output manifest has the same columns as rescan_pilot_sample.py's so it can
be fed straight into rescan_pilot_run.py; contradiction-frame detection
reuses the F3 code path (last_absent_date = the anchor's own latest
confident-absent observation -- the frame right before the census cutoff,
the one most likely to gain a present read once the crop is recentered).
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path

SEED = 20260718
N_SAMPLE = 30
OFFSET_THRESHOLD_M = 12.0

BASE = Path.home() / "zasolar_data/geid_temporal/fullscan_gemini_backdating_2026-07"
ANCHORS_ALL_CSV = BASE / "anchors_all.csv"
SCAN_STATE_DIRS = {"A24": BASE / "a24" / "scan_states", "A48": BASE / "a48" / "scan_states"}

CONFIDENT_QUALITY = {"usable", "ambiguous"}
CONFIDENCE_FLOOR = 0.8


def _latest_confident_absent(state: dict, census_date: str | None) -> str | None:
    by_date: dict[str, dict] = {}
    for rnd in state.get("rounds", []):
        for res in rnd.get("results", []):
            if res.get("pv_present") is not True and res.get("pv_present") is not False:
                continue
            conf = res.get("confidence")
            if conf is None or float(conf) < CONFIDENCE_FLOOR:
                continue
            if res.get("quality_flag") not in CONFIDENT_QUALITY:
                continue
            if res.get("pv_present") is not False:
                continue
            if census_date and res["capture_date"][:10] >= census_date[:10]:
                continue
            by_date[res["capture_date"]] = res
    if not by_date:
        return None
    return max(by_date)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--status", default="done_installed_during_census")
    parser.add_argument("--offset-threshold-m", type=float, default=OFFSET_THRESHOLD_M)
    parser.add_argument("--n", type=int, default=N_SAMPLE)
    args = parser.parse_args()

    import random

    rng = random.Random(SEED)

    candidates: list[dict] = []
    with ANCHORS_ALL_CSV.open("r", newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            try:
                ox = float(row["target_offset_x_m"])
                oy = float(row["target_offset_y_m"])
            except (KeyError, ValueError):
                continue
            offset_m = math.hypot(ox, oy)
            if offset_m <= args.offset_threshold_m:
                continue
            chip_arm = row.get("chip_arm", "A24")
            state_dir = SCAN_STATE_DIRS.get(chip_arm.upper())
            if state_dir is None:
                continue
            state_path = state_dir / f"{row['anchor_id']}.json"
            if not state_path.exists():
                continue
            try:
                state = json.loads(state_path.read_text())
            except Exception:
                continue
            if state.get("status") != args.status:
                continue
            census_date = state.get("census_date")
            last_absent = _latest_confident_absent(state, census_date)
            if last_absent is None:
                continue
            candidates.append(
                {
                    "fingerprint": "offset_large_census_bucket",
                    "anchor_id": row["anchor_id"],
                    "status": state.get("status", ""),
                    "chip_arm": chip_arm,
                    "grid_id": state.get("grid_id", ""),
                    "census_date": census_date,
                    "scan_state_path": str(state_path),
                    "present_date": "",
                    "absent_date": "",
                    "first_absent_date": "",
                    "last_absent_date": last_absent,
                    "span_days": "",
                    "gap_to_census_days": "",
                    "offset_m": f"{offset_m:.2f}",
                }
            )

    print(f"Found {len(candidates)} candidates with |offset|>{args.offset_threshold_m}m, "
          f"status={args.status}, >=1 confident pre-census absent frame")
    sample = rng.sample(candidates, min(args.n, len(candidates)))

    fieldnames = ["fingerprint", "anchor_id", "status", "chip_arm", "grid_id", "census_date",
                  "scan_state_path", "present_date", "absent_date", "first_absent_date",
                  "last_absent_date", "span_days", "gap_to_census_days", "offset_m"]
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(sample)

    print(f"seed={SEED}: sampled {len(sample)} anchors -> {args.out}")


if __name__ == "__main__":
    main()
