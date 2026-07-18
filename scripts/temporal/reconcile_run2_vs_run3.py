#!/usr/bin/env python3
"""RUN 3 vs RUN 2 full-population reconciliation.

Reuses the rescan_pilot_analyze.py metric definitions (flip rate on
contradiction frames, status_changed rate, bracket_changed rate +
bracket_shift_days distribution) but applies them to the full 41,393-anchor
population by joining RUN 2's and RUN 3's install_intervals_all.csv on
anchor_id, instead of the 30-anchor A0-A5 pilot sample. See RUN-fullscan-
gemini-backdating-run3-2026-07-18.md §7 item 1.

"Contradiction frame" here = RUN 2's done_ambiguous_marker_missed_pv cohort
(infer_install_dates.py's census-GT-contradiction reclassification: the
scan's own latest_absent_date fell on/after the grid's Vexcel flight date,
i.e. the run 2 chip geometry still read absent after ground truth says
present) -- the full-population analogue of the pilot's F1_critical
fingerprint. "Flip" = that anchor resolving to a real bounded status
(done_appears or done_installed_during_census with a non-blank interval) in
RUN 3.
"""
from __future__ import annotations

import argparse
import csv
import statistics
from collections import Counter
from datetime import date
from pathlib import Path

BOUNDED_STATUSES = {"done_appears", "done_installed_during_census"}
CONTRADICTION_STATUS = "done_ambiguous_marker_missed_pv"


def _load(path: Path) -> dict[str, dict]:
    with path.open(newline="", encoding="utf-8") as fh:
        return {r["anchor_id"]: r for r in csv.DictReader(fh)}


def _is_dated(row: dict) -> bool:
    return row["status"] in BOUNDED_STATUSES and bool(row["install_mid_estimate"])


def _coverage_class(row: dict) -> str:
    if _is_dated(row):
        return "dated"
    if row["status"] == "done_already_present_before_geid_history":
        return "left_censored"
    return "undated"


def _mid_date(row: dict) -> date | None:
    v = row["install_mid_estimate"]
    if not v:
        return None
    try:
        return date.fromisoformat(v)
    except ValueError:
        return None


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run2-intervals-csv", type=Path, required=True)
    ap.add_argument("--run3-intervals-csv", type=Path, required=True)
    ap.add_argument("--out-md", type=Path, required=True)
    args = ap.parse_args()

    run2 = _load(args.run2_intervals_csv)
    run3 = _load(args.run3_intervals_csv)

    common = sorted(set(run2) & set(run3))
    only_run2 = sorted(set(run2) - set(run3))
    only_run3 = sorted(set(run3) - set(run2))

    n = len(common)
    status_changed = 0
    coverage_from: Counter[tuple[str, str]] = Counter()
    status_pairs: Counter[tuple[str, str]] = Counter()

    dated_both = 0
    bracket_changed = []
    bracket_shift_days = []

    contradiction_ids = [aid for aid in common if run2[aid]["status"] == CONTRADICTION_STATUS]
    contradiction_flips = 0

    for aid in common:
        r2, r3 = run2[aid], run3[aid]
        s2, s3 = r2["status"], r3["status"]
        status_pairs[(s2, s3)] += 1
        if s2 != s3:
            status_changed += 1
        coverage_from[(_coverage_class(r2), _coverage_class(r3))] += 1

        if s2 == CONTRADICTION_STATUS and _is_dated(r3):
            contradiction_flips += 1

        if _is_dated(r2) and _is_dated(r3):
            dated_both += 1
            m2, m3 = _mid_date(r2), _mid_date(r3)
            if m2 and m3:
                shift = (m3 - m2).days
                if shift != 0:
                    bracket_changed.append(aid)
                    bracket_shift_days.append(shift)

    lines: list[str] = []
    lines.append("# DATA -- RUN 3 vs RUN 2 full-population reconciliation")
    lines.append("")
    lines.append(
        f"Joined on `anchor_id`: {n} common anchors "
        f"({len(only_run2)} RUN2-only, {len(only_run3)} RUN3-only)."
    )
    lines.append("")
    lines.append("## Headline metrics (rescan_pilot_analyze.py definitions, full population)")
    lines.append("")
    lines.append(
        "| n | status_changed_rate | contradiction_flip_rate (n={cn}) | "
        "bracket_changed_rate (of {db} dated-in-both) | "
        "median |bracket_shift_days| (changed only) |".format(
            cn=len(contradiction_ids), db=dated_both
        )
    )
    lines.append("|---:|---:|---:|---:|---:|")
    lines.append(
        "| {n} | {sc:.1%} | {cf:.1%} | {bc:.1%} | {ms} |".format(
            n=n,
            sc=status_changed / n if n else 0.0,
            cf=(contradiction_flips / len(contradiction_ids)) if contradiction_ids else float("nan"),
            bc=(len(bracket_changed) / dated_both) if dated_both else 0.0,
            ms=(f"{statistics.median(abs(x) for x in bracket_shift_days):.0f}" if bracket_shift_days else "n/a"),
        )
    )
    lines.append("")
    lines.append(
        f"`contradiction_flip_rate`: of {len(contradiction_ids)} RUN2 anchors flagged "
        f"`{CONTRADICTION_STATUS}` (census-GT contradiction -- scan still read absent "
        "on/after the grid's Vexcel flight date), the share that resolved to a real "
        "bounded status (`done_appears`/`done_installed_during_census` with a "
        "non-blank interval) in RUN3."
    )
    lines.append("")

    lines.append("## Coverage-class transition matrix (RUN2 -> RUN3)")
    lines.append("")
    lines.append("| RUN2 class | RUN3 class | n | % of RUN2 class |")
    lines.append("|---|---|---:|---:|")
    run2_class_totals: Counter[str] = Counter()
    for (c2, _c3), cnt in coverage_from.items():
        run2_class_totals[c2] += cnt
    for (c2, c3), cnt in sorted(coverage_from.items(), key=lambda kv: (-kv[1])):
        lines.append(f"| {c2} | {c3} | {cnt} | {cnt / run2_class_totals[c2]:.1%} |")
    lines.append("")

    lines.append("## Status distribution, RUN2 vs RUN3 (common anchors only)")
    lines.append("")
    lines.append("| status | RUN2 n | RUN3 n |")
    lines.append("|---|---:|---:|")
    run2_status: Counter[str] = Counter(run2[aid]["status"] for aid in common)
    run3_status: Counter[str] = Counter(run3[aid]["status"] for aid in common)
    for status in sorted(set(run2_status) | set(run3_status), key=lambda s: -run2_status.get(s, 0)):
        lines.append(f"| {status} | {run2_status.get(status, 0)} | {run3_status.get(status, 0)} |")
    lines.append("")

    lines.append("## Top status transitions (status_changed anchors only)")
    lines.append("")
    lines.append("| RUN2 status | RUN3 status | n |")
    lines.append("|---|---|---:|")
    changed_pairs = {k: v for k, v in status_pairs.items() if k[0] != k[1]}
    for (s2, s3), cnt in sorted(changed_pairs.items(), key=lambda kv: -kv[1])[:20]:
        lines.append(f"| {s2} | {s3} | {cnt} |")
    lines.append("")

    if only_run2:
        lines.append(f"## RUN2-only anchors ({len(only_run2)})")
        lines.append("")
        lines.append(", ".join(only_run2[:50]) + (" ..." if len(only_run2) > 50 else ""))
        lines.append("")
    if only_run3:
        lines.append(f"## RUN3-only anchors ({len(only_run3)})")
        lines.append("")
        lines.append(", ".join(only_run3[:50]) + (" ..." if len(only_run3) > 50 else ""))
        lines.append("")

    args.out_md.parent.mkdir(parents=True, exist_ok=True)
    args.out_md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    print(f"\nWrote {args.out_md}")


if __name__ == "__main__":
    main()
