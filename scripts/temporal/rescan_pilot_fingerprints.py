#!/usr/bin/env python3
"""RUN 2 re-scan pilot — rebuild F1-decision-critical / F3-late-transition
fingerprint populations from scan states (read-only), matching the
definitions in docs/replan_v2/DATA-fullscan-run2-parallax-audit-2026-07-17.md
section 1.

"confident absent/present" = pv_present in {True, False} (not null),
confidence >= 0.8, quality_flag in {usable, ambiguous}. Observations are
flattened across all rounds (later round overwrites same capture_date),
sorted by capture_date.

F1-decision-critical: a confident-present observation followed
(chronologically) by a confident-absent observation, BOTH strictly before
census_date (i.e. inside the evidence scan_decision.py actually uses).
Population per the audit: 1,777 (done_appears 670 + already_present 956 +
nonmonotonic 151).

F3 late-transition: status == done_appears, pre-census confident-absent run
spanning >= 365 days, AND last_absent_date -> census_date gap <= 365 days.
Population per the audit: 948.

Output: two CSVs (f1_critical.csv, f3_late_transition.csv) with anchor_id,
status, chip_arm, scan_state_path, plus the specific frame dates that
constitute the contradiction/transition (so the pilot script knows exactly
which frames to re-send).
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

BASE = Path.home() / "zasolar_data/geid_temporal/fullscan_gemini_backdating_2026-07"
SCAN_STATE_DIRS = {"A24": BASE / "a24" / "scan_states", "A48": BASE / "a48" / "scan_states"}

CONFIDENT_QUALITY = {"usable", "ambiguous"}
CONFIDENCE_FLOOR = 0.8


def _confident_observations(state: dict) -> list[dict]:
    by_date: dict[str, dict] = {}
    for rnd in state.get("rounds", []):
        for res in rnd.get("results", []):
            if res.get("pv_present") is None:
                continue
            conf = res.get("confidence")
            if conf is None or float(conf) < CONFIDENCE_FLOOR:
                continue
            if res.get("quality_flag") not in CONFIDENT_QUALITY:
                continue
            by_date[res["capture_date"]] = res
    return [by_date[d] for d in sorted(by_date)]


def _f1_critical(state: dict, census_date: str | None) -> dict | None:
    if not census_date:
        return None
    obs = _confident_observations(state)
    pre = [o for o in obs if o["capture_date"][:10] < census_date[:10]]
    for i in range(len(pre) - 1):
        if pre[i]["pv_present"] is True and pre[i + 1]["pv_present"] is False:
            return {
                "present_date": pre[i]["capture_date"],
                "absent_date": pre[i + 1]["capture_date"],
            }
    return None


def _f3_late_transition(state: dict, census_date: str | None) -> dict | None:
    if state.get("status") != "done_appears" or not census_date:
        return None
    obs = _confident_observations(state)
    pre_absent = [o for o in obs if o["capture_date"][:10] < census_date[:10] and o["pv_present"] is False]
    if not pre_absent:
        return None
    dates = sorted(o["capture_date"] for o in pre_absent)
    from datetime import date as _date

    def _d(s: str) -> _date:
        return _date.fromisoformat(s[:10])

    span_days = (_d(dates[-1]) - _d(dates[0])).days
    if span_days < 365:
        return None
    gap_days = (_d(census_date) - _d(dates[-1])).days
    if gap_days > 365:
        return None
    return {
        "first_absent_date": dates[0],
        "last_absent_date": dates[-1],
        "span_days": span_days,
        "gap_to_census_days": gap_days,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)

    f1_rows: list[dict] = []
    f3_rows: list[dict] = []
    n_scanned = 0

    for chip_arm, d in SCAN_STATE_DIRS.items():
        paths = sorted(d.glob("*.json"))
        for p in paths:
            n_scanned += 1
            try:
                state = json.loads(p.read_text())
            except Exception:
                continue
            census_date = state.get("census_date")
            f1 = _f1_critical(state, census_date)
            if f1 is not None:
                f1_rows.append(
                    {
                        "anchor_id": state["anchor_id"],
                        "status": state.get("status", ""),
                        "chip_arm": chip_arm,
                        "grid_id": state.get("grid_id", ""),
                        "census_date": census_date,
                        "scan_state_path": str(p),
                        **f1,
                    }
                )
            f3 = _f3_late_transition(state, census_date)
            if f3 is not None:
                f3_rows.append(
                    {
                        "anchor_id": state["anchor_id"],
                        "status": state.get("status", ""),
                        "chip_arm": chip_arm,
                        "grid_id": state.get("grid_id", ""),
                        "census_date": census_date,
                        "scan_state_path": str(p),
                        **f3,
                    }
                )
            if n_scanned % 5000 == 0:
                print(f"  scanned {n_scanned}, F1={len(f1_rows)}, F3={len(f3_rows)}")

    print(f"Scanned {n_scanned} scan states. F1-critical={len(f1_rows)} (audit expects 1777), "
          f"F3-late-transition={len(f3_rows)} (audit expects 948)")

    for name, rows in (("f1_critical.csv", f1_rows), ("f3_late_transition.csv", f3_rows)):
        path = args.out_dir / name
        if rows:
            with path.open("w", newline="", encoding="utf-8") as fh:
                writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
                writer.writeheader()
                writer.writerows(rows)
            print(f"Wrote {len(rows)} rows to {path}")


if __name__ == "__main__":
    main()
