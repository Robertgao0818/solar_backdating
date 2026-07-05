#!/usr/bin/env python3
"""ISSUE-10 WP-B — stratified sampler for the jump-critical-point gold set.

Draws a seeded, reproducible sample of anchors for human adjudication,
stratified by terminal status x confidence x audit-contradiction flag
(docs/replan_v2/ISSUE-10-design-2026-07-05.md "Decisions"). Also force-includes
the (currently 6) c-anchors that own one or more of the 15 dated-vs-undated
disputes, resolved via the `goldset_schema` crosswalk, outside the stratified
quota.

Inputs:
* `--intervals-csv`   install_intervals.csv (anchor_id, status, confidence,
  install_interval_start/end, latest_absent_date, earliest_present_date,
  scan_state_path, grid_id). Required -- the base population.
* `--cohort-audit-csv` cohort_audit_anchors.csv (anchor_id, any_contradiction).
  Optional; when absent (or an anchor is not in it), the contradiction axis
  collapses to "" and that anchor's stratum key drops to `status_x_confidence`
  (graceful degradation, never a crash).
* `--disputes-csv` + `--chipgroups-csv`  the 15 dated-vs-undated disputes and
  the chip-group crosswalk needed to resolve them to owning c-anchors. Both
  are required together to run dispute force-include; if only one is given,
  dispute force-include is skipped with a warning (never a crash).

Outputs (under `goldset_schema.goldset_root(tag)`, unless `--dry-run`):
* `sample_assignments.csv` -- one row per (anchor, annotator) via
  `goldset_schema.write_sample_assignments`.
* `dispute_resolution_report.csv` -- any dispute target id that could not be
  resolved to an owning c-anchor (drop-and-report, never fatal), plus the rare
  case where a resolved c-anchor itself is missing from the intervals CSV.
"""

from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path
from typing import NamedTuple

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.temporal import goldset_schema
from scripts.temporal.geid_temporal_common import read_csv_rows, write_csv_rows

DISPUTE_REPORT_FIELDS = ("target_id", "reason")


class AnchorRecord(NamedTuple):
    """One eligible-for-sampling anchor, joined intervals + cohort contradiction."""

    anchor_id: str
    grid_id: str
    terminal_status: str
    confidence: str
    any_contradiction: str  # "True"/"False"/"" (join miss)
    pipeline_interval_start: str
    pipeline_interval_end: str
    latest_absent_date: str
    earliest_present_date: str
    scan_state_path: str
    stratum: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--intervals-csv", type=Path, required=True)
    parser.add_argument("--cohort-audit-csv", type=Path, default=None, help="Optional; degrades gracefully when absent.")
    parser.add_argument("--disputes-csv", type=Path, default=None, help="validity_per_unit.csv; needs --chipgroups-csv too.")
    parser.add_argument("--chipgroups-csv", type=Path, default=None, help="chip_groups_as_anchors.csv; dispute crosswalk source.")
    parser.add_argument("--n", type=int, required=True, help="Stratified quota size (excludes forced dispute anchors). Range [10, 500].")
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--overlap-frac", type=float, default=0.20, help="Fraction of the quota double-annotated (A and B). Default 0.20.")
    parser.add_argument("--tag", required=True, help="Gold-set batch tag; output root = goldset_schema.goldset_root(tag).")
    parser.add_argument("--data-root", type=Path, default=None, help="Override the base dir under which goldset_root(tag) resolves (tests).")
    parser.add_argument("--dry-run", action="store_true", help="Print the stratum table + dispute collapse; write nothing.")
    return parser.parse_args()


# ---------------------------------------------------------------------------
# Join: intervals x cohort contradiction -> AnchorRecord with stratum key


def _make_stratum(status: str, confidence: str, any_contradiction: str) -> str:
    if any_contradiction == "":
        return f"{status}_x_{confidence}"
    return f"{status}_x_{confidence}_x_{any_contradiction}"


def read_contradiction_flags(path: Path | None) -> dict[str, str]:
    """anchor_id -> any_contradiction ("True"/"False"). Empty dict when the file
    is absent or lacks the column -- callers treat a missing key as "" (degrade)."""
    if path is None or not Path(path).exists():
        return {}
    rows = read_csv_rows(path)
    if not rows or "any_contradiction" not in rows[0]:
        print(f"[WARN] {path} missing 'any_contradiction' column; contradiction axis degrades to ''", file=sys.stderr)
        return {}
    out: dict[str, str] = {}
    for row in rows:
        aid = (row.get("anchor_id") or "").strip()
        if aid:
            out[aid] = (row.get("any_contradiction") or "").strip()
    return out


def build_anchor_records(intervals_csv: Path, contradiction_by_anchor: dict[str, str]) -> list[AnchorRecord]:
    records: list[AnchorRecord] = []
    for row in read_csv_rows(intervals_csv):
        anchor_id = (row.get("anchor_id") or "").strip()
        if not anchor_id:
            continue
        status = (row.get("status") or "").strip()
        confidence = (row.get("confidence") or "").strip()
        any_contradiction = contradiction_by_anchor.get(anchor_id, "")
        records.append(
            AnchorRecord(
                anchor_id=anchor_id,
                grid_id=(row.get("grid_id") or "").strip(),
                terminal_status=status,
                confidence=confidence,
                any_contradiction=any_contradiction,
                pipeline_interval_start=(row.get("install_interval_start") or "").strip(),
                pipeline_interval_end=(row.get("install_interval_end") or "").strip(),
                latest_absent_date=(row.get("latest_absent_date") or "").strip(),
                earliest_present_date=(row.get("earliest_present_date") or "").strip(),
                scan_state_path=(row.get("scan_state_path") or "").strip(),
                stratum=_make_stratum(status, confidence, any_contradiction),
            )
        )
    return sorted(records, key=lambda r: r.anchor_id)


# ---------------------------------------------------------------------------
# Dispute resolution (crosswalk lives in goldset_schema; this just wires it up
# to the intervals population and reports anything unresolvable)


def resolve_disputes(
    disputes_csv: Path | None,
    chipgroups_csv: Path | None,
    records_by_id: dict[str, AnchorRecord],
) -> tuple[dict[str, list[str]], list[dict[str, str]]]:
    """Returns (anchor_to_targets restricted to anchors present in intervals,
    report_rows for anything drop-and-reported)."""
    if disputes_csv is None and chipgroups_csv is None:
        return {}, []
    if disputes_csv is None or chipgroups_csv is None:
        print("[WARN] --disputes-csv and --chipgroups-csv are both required to resolve disputes; skipping force-include", file=sys.stderr)
        return {}, []

    target_ids = goldset_schema.read_dispute_target_ids(disputes_csv)
    anchor_to_targets, unresolved = goldset_schema.resolve_disputes_to_anchors(target_ids, chipgroups_csv)

    report_rows = [{"target_id": t, "reason": "no_owning_chipgroup"} for t in unresolved]
    resolved: dict[str, list[str]] = {}
    for anchor_id, targets in anchor_to_targets.items():
        if anchor_id in records_by_id:
            resolved[anchor_id] = targets
        else:
            for t in targets:
                report_rows.append({"target_id": t, "reason": "owning_anchor_not_in_intervals"})
    return resolved, report_rows


# ---------------------------------------------------------------------------
# Proportional-with-largest-remainder allocation + seeded draw


def allocate_quota(records: list[AnchorRecord], n: int) -> dict[str, int]:
    """stratum -> draw count. Proportional to stratum size, largest-remainder
    top-up so counts sum to exactly `n` (capped at total eligible population)."""
    by_stratum: dict[str, list[AnchorRecord]] = {}
    for r in records:
        by_stratum.setdefault(r.stratum, []).append(r)
    total = len(records)
    if total == 0 or n <= 0:
        return {}
    n = min(n, total)

    raw = {s: n * len(rows) / total for s, rows in by_stratum.items()}
    counts = {s: int(q) for s, q in raw.items()}
    for s in counts:
        counts[s] = min(counts[s], len(by_stratum[s]))
    remaining = n - sum(counts.values())

    # Largest fractional remainder first; deterministic tie-break by stratum name.
    order = sorted(by_stratum.keys(), key=lambda s: (-(raw[s] - counts[s]), s))
    while remaining > 0:
        progressed = False
        for s in order:
            if remaining <= 0:
                break
            if counts[s] < len(by_stratum[s]):
                counts[s] += 1
                remaining -= 1
                progressed = True
        if not progressed:
            break  # every stratum exhausted -- can't reach n, stop (n capped above anyway)
    return counts


def draw_quota(records: list[AnchorRecord], quota_counts: dict[str, int], seed: int) -> list[AnchorRecord]:
    """Seeded per-stratum draw. A dedicated `random.Random` per stratum (keyed
    on `seed:stratum`) makes the result independent of dict/insertion order."""
    by_stratum: dict[str, list[AnchorRecord]] = {}
    for r in records:
        by_stratum.setdefault(r.stratum, []).append(r)
    selected: list[AnchorRecord] = []
    for stratum in sorted(quota_counts):
        pool = sorted(by_stratum.get(stratum, []), key=lambda r: r.anchor_id)
        k = min(quota_counts[stratum], len(pool))
        rng = random.Random(f"{seed}:{stratum}")
        selected.extend(rng.sample(pool, k))
    return sorted(selected, key=lambda r: r.anchor_id)


# ---------------------------------------------------------------------------
# Annotator assignment (20% double-annotation overlap + A/B round-robin)


def _assignment_row(
    rec: AnchorRecord,
    annotator: str,
    *,
    is_overlap: bool,
    is_dispute_forced: bool,
    dispute_target_ids: str,
    seed: int,
    batch_id: str,
) -> dict[str, object]:
    return {
        "anchor_id": rec.anchor_id,
        "grid_id": rec.grid_id,
        "stratum": rec.stratum,
        "terminal_status": rec.terminal_status,
        "confidence": rec.confidence,
        "any_contradiction": rec.any_contradiction,
        "pipeline_interval_start": rec.pipeline_interval_start,
        "pipeline_interval_end": rec.pipeline_interval_end,
        "latest_absent_date": rec.latest_absent_date,
        "earliest_present_date": rec.earliest_present_date,
        "scan_state_path": rec.scan_state_path,
        "annotator_id": annotator,
        "is_overlap": is_overlap,
        "is_dispute_forced": is_dispute_forced,
        "dispute_target_ids": dispute_target_ids,
        "sampler_seed": seed,
        "sample_batch_id": batch_id,
    }


def assign_quota_annotators(
    quota: list[AnchorRecord], *, seed: int, overlap_frac: float, batch_id: str
) -> list[dict[str, object]]:
    quota_sorted = sorted(quota, key=lambda r: r.anchor_id)
    overlap_count = round(len(quota_sorted) * overlap_frac)
    rng = random.Random(f"{seed}:overlap")
    overlap_ids = {r.anchor_id for r in rng.sample(quota_sorted, min(overlap_count, len(quota_sorted)))}

    rows: list[dict[str, object]] = []
    single_i = 0
    for rec in quota_sorted:
        if rec.anchor_id in overlap_ids:
            for annotator in goldset_schema.ANNOTATORS:
                rows.append(
                    _assignment_row(
                        rec, annotator, is_overlap=True, is_dispute_forced=False,
                        dispute_target_ids="", seed=seed, batch_id=batch_id,
                    )
                )
        else:
            annotator = goldset_schema.ANNOTATORS[single_i % len(goldset_schema.ANNOTATORS)]
            single_i += 1
            rows.append(
                _assignment_row(
                    rec, annotator, is_overlap=False, is_dispute_forced=False,
                    dispute_target_ids="", seed=seed, batch_id=batch_id,
                )
            )
    return rows


def build_forced_rows(
    records_by_id: dict[str, AnchorRecord],
    anchor_to_targets: dict[str, list[str]],
    *,
    seed: int,
    batch_id: str,
) -> list[dict[str, object]]:
    """Each owning c-anchor force-included exactly once, both A and B -- the
    many-to-one dispute collapse is already done by `resolve_disputes` upstream
    (one dict entry per owning c-anchor), so this never emits a duplicate row."""
    rows: list[dict[str, object]] = []
    for anchor_id in sorted(anchor_to_targets):
        rec = records_by_id[anchor_id]
        target_ids_str = goldset_schema.encode_target_ids(anchor_to_targets[anchor_id])
        for annotator in goldset_schema.ANNOTATORS:
            rows.append(
                _assignment_row(
                    rec, annotator, is_overlap=True, is_dispute_forced=True,
                    dispute_target_ids=target_ids_str, seed=seed, batch_id=batch_id,
                )
            )
    return rows


# ---------------------------------------------------------------------------
# Reporting


def print_stratum_table(quota_counts: dict[str, int], *, n: int) -> None:
    print(f"Stratum allocation (quota n={n}):")
    for stratum in sorted(quota_counts):
        print(f"  {stratum}: {quota_counts[stratum]}")
    print(f"  TOTAL: {sum(quota_counts.values())}")


def print_dispute_collapse(anchor_to_targets: dict[str, list[str]]) -> None:
    print(f"Dispute -> c-anchor collapse ({len(anchor_to_targets)} forced c-anchors):")
    for anchor_id in sorted(anchor_to_targets):
        targets = anchor_to_targets[anchor_id]
        print(f"  {anchor_id}: {goldset_schema.encode_target_ids(targets)} ({len(targets)} target(s))")


def main() -> None:
    args = parse_args()
    if not (10 <= args.n <= 500):
        raise SystemExit(f"--n must be in [10, 500], got {args.n}")

    contradiction_by_anchor = read_contradiction_flags(args.cohort_audit_csv)
    records = build_anchor_records(args.intervals_csv, contradiction_by_anchor)
    if not records:
        raise SystemExit(f"No anchors found in {args.intervals_csv}")
    records_by_id = {r.anchor_id: r for r in records}

    anchor_to_targets, report_rows = resolve_disputes(args.disputes_csv, args.chipgroups_csv, records_by_id)

    eligible = [r for r in records if r.anchor_id not in anchor_to_targets]
    quota_counts = allocate_quota(eligible, args.n)
    quota = draw_quota(eligible, quota_counts, args.seed)

    if args.dry_run:
        print_stratum_table(quota_counts, n=args.n)
        print_dispute_collapse(anchor_to_targets)
        if report_rows:
            print(f"Unresolved/dropped disputes ({len(report_rows)}):")
            for row in report_rows:
                print(f"  {row['target_id']}: {row['reason']}")
        return

    quota_rows = assign_quota_annotators(quota, seed=args.seed, overlap_frac=args.overlap_frac, batch_id=args.tag)
    forced_rows = build_forced_rows(records_by_id, anchor_to_targets, seed=args.seed, batch_id=args.tag)
    all_rows = quota_rows + forced_rows

    root = goldset_schema.goldset_root(args.tag, base=args.data_root)
    assignments_path = goldset_schema.sample_assignment_path(root)
    goldset_schema.write_sample_assignments(all_rows, assignments_path)
    print(f"Wrote {len(all_rows)} assignment rows ({len(quota)} quota anchors + {len(anchor_to_targets)} forced) -> {assignments_path}")

    report_path = root / "dispute_resolution_report.csv"
    if report_rows:
        write_csv_rows(report_path, report_rows, DISPUTE_REPORT_FIELDS)
        print(f"Wrote {len(report_rows)} unresolved/dropped dispute row(s) -> {report_path}")
    else:
        print("No unresolved disputes.")


if __name__ == "__main__":
    main()
