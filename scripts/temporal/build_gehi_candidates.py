#!/usr/bin/env python3
"""Merge TM + Wayback group-level availability into target-level GEHI candidates.

Pipeline position: upstream of `gehi_download.py`. Consumes the group-level
outputs of `availability_rebuild_2026-07-12/` (TM `availability` CSVs +
Wayback `info` CSV) and the chip-group -> target fan-out map, and produces a
per-*target*-anchor candidates CSV in the schema `gehi_download.py` already
reads (`anchor_id,capture_date[,version][,all_capture_dates]`; extra columns
are ignored by its `csv.DictReader`-based loader, see
`read_candidate_rows`/`expand_candidate_dates` in that file).

Why a separate merge step instead of teaching `gehi_download.py` to read
group-level rows directly: the group -> target fan-out and the per-anchor
census cutoff are both target-shaped decisions (a target's census date can
differ from a sibling target's in the same chip group only in the trivial
sense that they share it -- but `gehi_download.py` has no concept of "cutoff
date" at all, see below), so they belong in a candidate-generation step, not
in the downloader.

Census cutoff (ISSUE-26 semantics, commit cb25ee3 / run_adaptive_scan.py
`_catalog_cutoff_candidates` + `_resolve_catalog_max_date`): each anchor's
window is `[--min-date, effective_max]`, where `effective_max` is the
per-anchor census date advanced forward to include the earliest
`--post-census-frames` (default 3) reference captures after census, hard
capped at `--max-date`. This mirrors `_catalog_cutoff_candidates(...)[0]`
exactly: if census >= hard cap, the cap wins outright; otherwise take the
Nth-soonest post-census date in the union of TM + Wayback dates for that
anchor (or the last available one if fewer than N exist, or the census date
itself if none exist). `gehi_download.py` has no per-anchor upper bound of
its own -- it downloads whatever candidate rows it is given -- so this script
is the only place the cutoff is enforced. Per-anchor census date = max
`last_capture_date` (Vexcel, `load_vexcel_capture_dates` in
`infer_install_dates.py`) over the chip group's `source_grids`, matching the
multi-grid handling `_resolve_census_mid_date` added in cb25ee3.

TM/Wayback same-date collision decision: KEEP both, no merge/drop. Two
independent reasons:
  1. `gehi_download.py` selects provider globally via a single `--provider`
     CLI flag per invocation (see `args.provider` at the bottom of that
     file) -- it never reads a per-row `provider` column. So a downstream
     run against this file's output MUST already be split by the `provider`
     column into two separate `gehi_download.py` invocations (`--provider
     TM` and `--provider Wayback`, each pointed at a provider-filtered slice
     of this CSV); TM and Wayback rows never compete within one invocation.
  2. Even so, `artifact_path()` in `gehi_download.py` keys the on-disk chip
     filename only by `(anchor_id, capture_date, version)`. TM rows always
     carry an empty version (-> `"noversion"` in the filename), Wayback rows
     always carry Wayback's own numeric version label, so a same-date TM/
     Wayback pair can never collide on disk even if accidentally run
     together. TM and Wayback are different imagery sources; a same-date
     coincidence is corroboration, not redundancy.
Within a single provider, TM rows are deduped by `(anchor_id, capture_date)`
after merging all `--tm-csv` inputs (the 403-retry and truncation-repull
files can overlap on a group that hit both failure modes); Wayback rows are
deduped the same way defensively, though the source `info` CSV is already
one row per `(anchor_id, capture_date)`.

Known holdout: group
`jhb_full382_unified_a_merge01_c0925_fpcut_2026_06_01_c0012722` has zero TM
rows in `tm_retry_403.csv` (permanent TM-side holdout, not a bug) but has
Wayback coverage. This script does not special-case it -- a group with zero
TM dates simply contributes zero TM candidate rows and whatever Wayback rows
it has; nothing about the merge assumes a minimum row count per provider.
"""

from __future__ import annotations

# Imports follow a sys.path bootstrap (below) so the subrepo can be run as a
# script; E402 is expected for the scripts.* imports, matching the sibling
# temporal modules' convention.
# ruff: noqa: E402

import argparse
import csv
import statistics
import sys
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.temporal.geid_temporal_common import read_csv_rows, write_csv_rows

DEFAULT_MIN_DATE = "2019-01-01"
DEFAULT_MAX_DATE = "2025-12-31"
DEFAULT_POST_CENSUS_FRAMES = 3

OUTPUT_FIELDS = [
    "anchor_id",
    "capture_date",
    "version",
    "all_capture_dates",
    "provider",
    "chip_id",
    "grid_id",
    "region_key",
    "source_grids",
    "census_date",
    "cutoff_max_date",
]


@dataclass
class GroupInfo:
    group_id: str
    region_key: str
    grid_id: str
    source_grids: list[str]
    target_anchor_ids: list[str]


@dataclass
class Stats:
    n_groups: int = 0
    n_groups_missing_census: int = 0
    group_date_counts: list[int] = field(default_factory=list)
    group_rows_before_cutoff: int = 0
    group_rows_after_cutoff: int = 0
    target_rows_total: int = 0
    target_rows_pilot: int = 0


def parse_iso(value: str) -> str:
    return str(value).strip()[:10]


def load_vexcel_census(path: Path) -> dict[str, str]:
    """grid_id -> ISO last_capture_date, per `infer_install_dates.load_vexcel_capture_dates`."""
    out: dict[str, str] = {}
    for row in read_csv_rows(path):
        gid = str(row.get("grid_id", "")).strip()
        raw = str(row.get("last_capture_date", "")).strip()
        if not gid or not raw:
            continue
        out[gid] = parse_iso(raw)
    return out


def load_groups(path: Path) -> list[GroupInfo]:
    groups = []
    for row in read_csv_rows(path):
        group_id = str(row.get("anchor_id", "")).strip()
        if not group_id:
            continue
        source_grids = [g.strip() for g in str(row.get("source_grids", "")).split(";") if g.strip()]
        target_anchor_ids = [
            t.strip() for t in str(row.get("target_anchor_ids", "")).split(";") if t.strip()
        ]
        groups.append(
            GroupInfo(
                group_id=group_id,
                region_key=str(row.get("region_key", "")).strip(),
                grid_id=str(row.get("grid_id", "")).strip(),
                source_grids=source_grids,
                target_anchor_ids=target_anchor_ids,
            )
        )
    return groups


def load_tm_dates(paths: list[Path]) -> dict[str, set[str]]:
    """group_id -> set of ISO capture dates, deduped across all --tm-csv inputs."""
    by_group: dict[str, set[str]] = {}
    for path in paths:
        for row in read_csv_rows(path):
            group_id = str(row.get("anchor_id", "")).strip()
            capture_date = parse_iso(row.get("capture_date", ""))
            if not group_id or not capture_date:
                continue
            by_group.setdefault(group_id, set()).add(capture_date)
    return by_group


def load_wayback_rows(path: Path) -> dict[str, dict[str, dict[str, str]]]:
    """group_id -> {capture_date -> row}, first occurrence wins on duplicate (group, date)."""
    by_group: dict[str, dict[str, dict[str, str]]] = {}
    for row in read_csv_rows(path):
        group_id = str(row.get("anchor_id", "")).strip()
        capture_date = parse_iso(row.get("capture_date", ""))
        if not group_id or not capture_date:
            continue
        slot = by_group.setdefault(group_id, {})
        if capture_date not in slot:
            slot[capture_date] = row
    return by_group


def resolve_census_date(group: GroupInfo, vexcel: dict[str, str]) -> str | None:
    dates = [vexcel[g] for g in group.source_grids if g in vexcel]
    if not dates:
        return None
    return max(dates)


def compute_cutoff_max_date(
    merged_dates: set[str],
    census_date: str,
    *,
    min_date: str,
    max_date: str,
    keep: int,
) -> str:
    """Replicates `_catalog_cutoff_candidates(...)[0]` in run_adaptive_scan.py.

    census >= hard cap -> the cap wins. Otherwise: the Nth-soonest
    post-census date (or the last available one if fewer than N exist, or
    census itself if none exist), never exceeding the hard cap.
    """
    hard_max = max_date[:10]
    census = census_date[:10]
    if census >= hard_max:
        return hard_max
    newer = sorted({d for d in merged_dates if d > census and d <= hard_max})
    keep = max(0, keep)
    if keep == 0 or not newer:
        return census
    return newer[min(keep, len(newer)) - 1]


def scrub_all_capture_dates(raw: str, fallback_date: str, *, min_date: str, max_date: str) -> str:
    dates = [parse_iso(d) for d in str(raw).split(";") if d.strip()]
    kept = [d for d in dates if min_date <= d <= max_date]
    if not kept:
        kept = [fallback_date]
    return ";".join(sorted(set(kept)))


def in_window(date: str, *, min_date: str, max_date: str) -> bool:
    return min_date <= date <= max_date


def build_rows(
    groups: list[GroupInfo],
    vexcel: dict[str, str],
    tm_dates_by_group: dict[str, set[str]],
    wayback_by_group: dict[str, dict[str, dict[str, str]]],
    *,
    min_date: str,
    max_date: str,
    post_census_frames: int,
    pilot_year: int | None,
    stats: Stats,
) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for group in groups:
        stats.n_groups += 1
        census_date = resolve_census_date(group, vexcel)
        if census_date is None:
            stats.n_groups_missing_census += 1
            continue

        tm_dates = tm_dates_by_group.get(group.group_id, set())
        wayback_rows = wayback_by_group.get(group.group_id, {})
        merged_dates = tm_dates | set(wayback_rows)
        stats.group_date_counts.append(len(merged_dates))
        stats.group_rows_before_cutoff += len(tm_dates) + len(wayback_rows)

        effective_max = compute_cutoff_max_date(
            merged_dates,
            census_date,
            min_date=min_date,
            max_date=max_date,
            keep=post_census_frames,
        )

        tm_kept = sorted(d for d in tm_dates if in_window(d, min_date=min_date, max_date=effective_max))
        wayback_kept = [
            row
            for date, row in wayback_rows.items()
            if in_window(date, min_date=min_date, max_date=effective_max)
        ]

        if pilot_year is not None:
            tm_kept = [d for d in tm_kept if d[:4] == str(pilot_year)]
            wayback_kept = [row for row in wayback_kept if parse_iso(row["capture_date"])[:4] == str(pilot_year)]

        stats.group_rows_after_cutoff += len(tm_kept) + len(wayback_kept)

        source_grids_str = ";".join(group.source_grids)
        for target_id in group.target_anchor_ids:
            for capture_date in tm_kept:
                out.append(
                    {
                        "anchor_id": target_id,
                        "capture_date": capture_date,
                        "version": "",
                        "all_capture_dates": "",
                        "provider": "TM",
                        "chip_id": group.group_id,
                        "grid_id": group.grid_id,
                        "region_key": group.region_key,
                        "source_grids": source_grids_str,
                        "census_date": census_date,
                        "cutoff_max_date": effective_max,
                    }
                )
                stats.target_rows_total += 1
                if pilot_year is not None:
                    stats.target_rows_pilot += 1
            for row in wayback_kept:
                capture_date = parse_iso(row["capture_date"])
                scrub_min = min_date
                scrub_max = effective_max
                if pilot_year is not None:
                    scrub_min = max(scrub_min, f"{pilot_year}-01-01")
                    scrub_max = min(scrub_max, f"{pilot_year}-12-31")
                all_capture_dates = scrub_all_capture_dates(
                    row.get("all_capture_dates", ""),
                    capture_date,
                    min_date=scrub_min,
                    max_date=scrub_max,
                )
                out.append(
                    {
                        "anchor_id": target_id,
                        "capture_date": capture_date,
                        "version": str(row.get("version", "")).strip(),
                        "all_capture_dates": all_capture_dates,
                        "provider": "Wayback",
                        "chip_id": group.group_id,
                        "grid_id": group.grid_id,
                        "region_key": group.region_key,
                        "source_grids": source_grids_str,
                        "census_date": census_date,
                        "cutoff_max_date": effective_max,
                    }
                )
                stats.target_rows_total += 1
                if pilot_year is not None:
                    stats.target_rows_pilot += 1
    return out


def print_stats(stats: Stats) -> None:
    counts = stats.group_date_counts
    p50 = statistics.median(counts) if counts else 0
    p90 = (
        statistics.quantiles(counts, n=10)[8]
        if len(counts) >= 10
        else (max(counts) if counts else 0)
    )
    print(f"groups processed:            {stats.n_groups}")
    print(f"groups missing census date:  {stats.n_groups_missing_census}")
    print(f"per-group merged date count: p50={p50:.1f} p90={p90:.1f}")
    print(f"group-level rows before cutoff: {stats.group_rows_before_cutoff}")
    print(f"group-level rows after cutoff:  {stats.group_rows_after_cutoff}")
    print(f"group-level rows dropped by cutoff: {stats.group_rows_before_cutoff - stats.group_rows_after_cutoff}")
    print(f"target-level rows (fan-out total): {stats.target_rows_total}")
    print(f"target-level rows (pilot-year filtered): {stats.target_rows_pilot}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tm-csv", type=Path, nargs="+", required=True, help="One or more TM availability CSVs (merged, deduped by (anchor_id, capture_date)).")
    parser.add_argument("--wayback-csv", type=Path, required=True, help="Wayback info CSV (group-level).")
    parser.add_argument("--groups-csv", type=Path, required=True, help="chip_groups_as_anchors.csv (group -> target fan-out map).")
    parser.add_argument("--vexcel-csv", type=Path, required=True, help="Per-grid Vexcel capture dates CSV.")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--min-date", default=DEFAULT_MIN_DATE)
    parser.add_argument("--max-date", default=DEFAULT_MAX_DATE)
    parser.add_argument("--post-census-frames", type=int, default=DEFAULT_POST_CENSUS_FRAMES)
    parser.add_argument("--pilot-year", type=int, default=None, help="Keep only rows whose capture_date falls in this year (applied after cutoff).")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    vexcel = load_vexcel_census(args.vexcel_csv)
    groups = load_groups(args.groups_csv)
    tm_dates_by_group = load_tm_dates(args.tm_csv)
    wayback_by_group = load_wayback_rows(args.wayback_csv)

    stats = Stats()
    rows = build_rows(
        groups,
        vexcel,
        tm_dates_by_group,
        wayback_by_group,
        min_date=args.min_date,
        max_date=args.max_date,
        post_census_frames=args.post_census_frames,
        pilot_year=args.pilot_year,
        stats=stats,
    )

    write_csv_rows(args.output, rows, OUTPUT_FIELDS)
    print_stats(stats)
    print(f"wrote {len(rows)} rows -> {args.output}")


if __name__ == "__main__":
    main()
