#!/usr/bin/env python3
"""RUN 2 re-scan mini-pilot -- select the ~30 highest-disagreement anchors
for the A4 combo arm (enlarged + parallax prompt + gemini-3-flash).

Disagreement score per anchor = number of arms (of A0..A3) whose
contradiction-frame verdict differs from A0's contradiction-frame verdict,
plus 1 if new_status differs across arms. Ties broken by whether any arm
flipped absent->present (prioritize anchors where mitigation actually did
something) then by anchor_id for determinism.
"""

from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from pathlib import Path

N_A4 = 30


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results-csv", type=Path, required=True, help="pilot_A0_A3.csv")
    parser.add_argument("--manifest", type=Path, required=True, help="original sample manifest (for full row data)")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    by_anchor: dict[str, dict[str, dict]] = defaultdict(dict)
    with args.results_csv.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            by_anchor[row["anchor_id"]][row["arm"]] = row

    scored: list[tuple[int, bool, str]] = []
    for anchor_id, arms in by_anchor.items():
        if "A0" not in arms:
            continue
        baseline_verdict = arms["A0"]["contradiction_new_verdict"]
        baseline_status = arms["A0"]["new_status"]
        disagree = 0
        any_flip = False
        for arm_name in ("A1", "A2", "A3"):
            r = arms.get(arm_name)
            if r is None:
                continue
            if r["contradiction_new_verdict"] != baseline_verdict:
                disagree += 1
            if r["new_status"] != baseline_status:
                disagree += 1
            if r["flipped_absent_to_present"] in ("True", "true", True):
                any_flip = True
        scored.append((disagree, any_flip, anchor_id))

    scored.sort(key=lambda t: (-t[0], not t[1], t[2]))
    top = scored[:N_A4]
    top_ids = {anchor_id for _, _, anchor_id in top}

    with args.manifest.open(newline="", encoding="utf-8") as fh:
        manifest_rows = list(csv.DictReader(fh))
    out_rows = [r for r in manifest_rows if r["anchor_id"] in top_ids]

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(manifest_rows[0].keys()))
        writer.writeheader()
        writer.writerows(out_rows)

    print(f"Selected {len(out_rows)} anchors for A4 (top disagreement). Disagreement scores (top 10):")
    for disagree, any_flip, anchor_id in top[:10]:
        print(f"  {anchor_id}: disagree_score={disagree} any_flip={any_flip}")
    print(f"Wrote manifest to {args.out}")


if __name__ == "__main__":
    main()
