"""E2 merge finishing step: 2019+ slice + coverage report + exit gate.

Replaces the pandas heredoc in run_ct_citywide_catalog_e2.sh cmd_merge,
which segfaulted (system python3 pandas, 2026-08-18) on the 13.8M-row
merged candidates CSV. This implementation streams with the csv module:
bounded memory, no parser edge cases.

Usage:
  python e2_merge_coverage.py --run-root <citywide run root>
Writes into <run_root>/ct05_catalog_v1/merged/:
  gehi_vintage_candidates_ct05_run3_2019plus.csv
  catalog_coverage_report.json
  (updates summary.json with 2019+ pointers)
Exits non-zero when the E2 exit gate fails.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path

MIN_DATE_2019PLUS = "2019-01-01"
EXPECTED_ANCHORS = 111801
EXTRAPOLATED_2019PLUS = 6140258


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-root", type=Path, required=True)
    args = parser.parse_args()
    merged = args.run_root / "ct05_catalog_v1" / "merged"

    outcomes_path = merged / "anchor_catalog_outcomes.csv"
    cands_path = merged / "gehi_vintage_candidates_ct05.csv"
    c19_path = merged / "gehi_vintage_candidates_ct05_run3_2019plus.csv"

    status_counts: Counter[str] = Counter()
    anchor_ids: set[str] = set()
    outcome_count = 0
    release_eligible = 0
    with outcomes_path.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            outcome_count += 1
            anchor_ids.add(row["anchor_id"])
            status_counts[row["catalog_status"]] += 1
            release_eligible += int(row["release_eligible"])

    c19_count = 0
    c19_anchors: set[str] = set()
    with cands_path.open(newline="", encoding="utf-8") as src, c19_path.open(
        "w", newline="", encoding="utf-8"
    ) as dst:
        reader = csv.DictReader(src)
        writer = csv.DictWriter(dst, fieldnames=reader.fieldnames)
        writer.writeheader()
        for row in reader:
            if str(row["capture_date"])[:10] >= MIN_DATE_2019PLUS:
                writer.writerow(row)
                c19_count += 1
                c19_anchors.add(row["anchor_id"])

    coverage = {
        "schema_version": "ct_citywide_catalog_coverage_v1",
        "anchor_count": outcome_count,
        "expected_anchor_count": EXPECTED_ANCHORS,
        "every_anchor_has_outcome": outcome_count == EXPECTED_ANCHORS
        and len(anchor_ids) == EXPECTED_ANCHORS,
        "catalog_status_counts": dict(sorted(status_counts.items())),
        "release_eligible_anchors": release_eligible,
        "operational_failure_anchors": status_counts.get("operational_failure", 0),
        "no_history_anchors": status_counts.get("no_history", 0),
        "candidate_count_2019plus": c19_count,
        "unique_anchors_with_2019plus": len(c19_anchors),
        "extrapolated_2019plus_from_top52": EXTRAPOLATED_2019PLUS,
        "2019plus_vs_extrapolation_ratio": c19_count / EXTRAPOLATED_2019PLUS,
        "mean_2019plus_dates_per_anchor_with_history": c19_count
        / max(len(c19_anchors), 1),
    }
    (merged / "catalog_coverage_report.json").write_text(
        json.dumps(coverage, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    summary = json.loads((merged / "summary.json").read_text(encoding="utf-8"))
    summary["candidate_count_2019plus"] = c19_count
    summary["2019plus_csv"] = str(c19_path)
    summary["coverage_report"] = str(merged / "catalog_coverage_report.json")
    (merged / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    print(json.dumps(coverage, indent=2, sort_keys=True))
    if not coverage["every_anchor_has_outcome"]:
        raise SystemExit("E2 exit gate failed: incomplete outcomes")
    if coverage["operational_failure_anchors"] > 0:
        print("WARNING: operational failures remain; inspect retry_anchors.csv", flush=True)


if __name__ == "__main__":
    main()
