#!/usr/bin/env python3
"""RUN 2 re-scan mini-pilot -- aggregate metrics across arms for the memo.

Reads pilot_A0_A3.csv (+ optionally pilot_A4.csv) and prints/writes:
  - per-arm flip rate on contradiction frames (absent->present)
  - per-arm new_status distribution + status_changed rate
  - per-arm bracket_changed rate + bracket_shift_days distribution
  - per-arm post-census reference-frame agreement rate
  - per-arm total elapsed_sec / n_chunks (cost/latency proxy)
  - a per-anchor disagreement matrix (which arms flipped which anchors)
"""

from __future__ import annotations

import argparse
import csv
import statistics
from collections import Counter, defaultdict
from pathlib import Path


def _load(paths: list[Path]) -> list[dict]:
    rows: list[dict] = []
    for p in paths:
        if not p.exists():
            continue
        with p.open(newline="", encoding="utf-8") as fh:
            rows.extend(csv.DictReader(fh))
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results-csv", type=Path, nargs="+", required=True)
    parser.add_argument("--out-md", type=Path, required=True)
    args = parser.parse_args()

    rows = _load(args.results_csv)
    by_arm: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_arm[r["arm"]].append(r)

    lines: list[str] = []
    lines.append("| arm | model | crop | prompt | n | flip_rate | status_changed_rate | bracket_changed_rate | "
                  "median_bracket_shift_days(|changed only|) | post_census_agreement (mean) | errors | "
                  "total_elapsed_sec | mean_elapsed_sec | total_chunks |")
    lines.append("|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")

    for arm in sorted(by_arm):
        arm_rows = by_arm[arm]
        n = len(arm_rows)
        errors = sum(1 for r in arm_rows if r["error"])
        ok_rows = [r for r in arm_rows if not r["error"]]
        flips = sum(1 for r in ok_rows if r["flipped_absent_to_present"] in ("True", "true"))
        status_changed = sum(1 for r in ok_rows if r["status_changed"] in ("True", "true"))
        bracket_changed = [r for r in ok_rows if r["bracket_changed"] in ("True", "true")]
        shifts = []
        for r in bracket_changed:
            try:
                shifts.append(abs(int(r["bracket_shift_days"])))
            except (ValueError, TypeError):
                pass
        agree_vals = []
        for r in ok_rows:
            v = r["post_census_agreement_rate"]
            if v not in ("", None):
                try:
                    agree_vals.append(float(v))
                except ValueError:
                    pass
        elapsed_vals = [float(r["elapsed_sec"]) for r in arm_rows if r["elapsed_sec"]]
        chunk_vals = [int(r["n_chunks"]) for r in arm_rows if r["n_chunks"]]
        model = arm_rows[0]["model"] if arm_rows else ""
        crop = arm_rows[0]["crop"] if arm_rows else ""
        prompt = arm_rows[0]["prompt"] if arm_rows else ""

        lines.append(
            "| {arm} | {model} | {crop} | {prompt} | {n} | {flip_rate:.1%} | {status_rate:.1%} | "
            "{bracket_rate:.1%} | {median_shift} | {agree:.3f} | {errors} | {total_elapsed:.0f} | "
            "{mean_elapsed:.1f} | {total_chunks} |".format(
                arm=arm, model=model, crop=crop, prompt=prompt, n=n,
                flip_rate=(flips / len(ok_rows)) if ok_rows else 0.0,
                status_rate=(status_changed / len(ok_rows)) if ok_rows else 0.0,
                bracket_rate=(len(bracket_changed) / len(ok_rows)) if ok_rows else 0.0,
                median_shift=(f"{statistics.median(shifts):.0f}" if shifts else "n/a"),
                agree=(statistics.mean(agree_vals) if agree_vals else float("nan")),
                errors=errors,
                total_elapsed=sum(elapsed_vals),
                mean_elapsed=(statistics.mean(elapsed_vals) if elapsed_vals else 0.0),
                total_chunks=sum(chunk_vals),
            )
        )

    lines.append("")
    lines.append("## New-status distribution per arm")
    lines.append("")
    for arm in sorted(by_arm):
        counts = Counter(r["new_status"] for r in by_arm[arm] if not r["error"])
        lines.append(f"**{arm}**: " + ", ".join(f"{k}={v}" for k, v in counts.most_common()))
    lines.append("")

    lines.append("## Per-fingerprint flip rate (F1_critical vs F3_late_transition)")
    lines.append("")
    lines.append("| arm | fingerprint | n | flip_rate |")
    lines.append("|---|---|---:|---:|")
    for arm in sorted(by_arm):
        for fp in ("F1_critical", "F3_late_transition"):
            sub = [r for r in by_arm[arm] if r["fingerprint"] == fp and not r["error"]]
            if not sub:
                continue
            flips = sum(1 for r in sub if r["flipped_absent_to_present"] in ("True", "true"))
            lines.append(f"| {arm} | {fp} | {len(sub)} | {flips/len(sub):.1%} |")

    args.out_md.parent.mkdir(parents=True, exist_ok=True)
    args.out_md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    print(f"\nWrote {args.out_md}")


if __name__ == "__main__":
    main()
