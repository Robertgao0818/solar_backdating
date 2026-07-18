#!/usr/bin/env python3
"""Census-shift top-up driver (2026-07-18, ahead of RUN 3 / `run_fullscan_backdating_v2.sh`).

Context: the ISSUE-27 `anchors_v2` rebuild resolves each target's ISSUE-26
census date from its OWN per-target `source_grids` column
(`run_adaptive_scan._resolve_census_mid_date` / `_anchor_grid_ids`,
`scripts/temporal/run_adaptive_scan.py:1963-1994`), which can list a
different (often larger) grid set than the LEGACY chip-group used when
`build_gehi_candidates.py` originally selected download candidates for the
`basemap_rebuild_2026-07-13` pull. Where the new per-target census
(`census_v2`) falls later than the legacy group census (`census_v1`), the
already-downloaded chip set can be short on ISSUE-26 post-census reference
frames (`post_census_reference_frames`, default 3, `configs/
geid_anchor_presence.yaml`).

Key fact this script exploits (verified empirically 2026-07-18): the raw
TM/Wayback availability pulled into `availability_rebuild_2026-07-12/` covers
the FULL 2019-2025 window per legacy chip-group, independent of any census
cutoff -- the cutoff is applied only downstream, when `build_gehi_candidates.
py` selects which of those already-known dates become download candidates.
So for most anchors, the frames needed to satisfy `census_v2` already exist
in the known TM/Wayback catalog and were simply never selected/downloaded
under the old, tighter `census_v1` cutoff -- no live GEHI call needed, just
re-selection + a small incremental download.

Also key: `run_adaptive_scan.py`'s offline catalog builders
(`_build_offline_tm_catalog` / `_build_offline_wayback_catalog`) do their OWN
complete ISSUE-26 cutoff walk at scan-launch time, using whatever `capture_
date`/`version` rows exist per anchor in `--offline-tm-catalog-csv` and the
census date THEY resolve from `--anchors-csv` + `--vexcel-capture-csv` (i.e.
`census_v2`, once RUN 3 uses `anchors_v2`). They do NOT read this CSV's own
`census_date`/`cutoff_max_date` columns (those are provenance-only, written
by `build_gehi_candidates.py` for its own audit trail). So this script's job
is narrow: (1) confirm which anchors are short, (2) find the incremental
`(anchor_id, capture_date, provider)` triples needed to close the gap using
already-known dates first, live GEHI only as a last resort, (3) download
exactly those to `basemap_rebuild_2026-07-13/chips`, (4) emit new candidate
rows (same schema as `build_gehi_candidates.py`'s `OUTPUT_FIELDS`) merged
into a NEW catalog file, RUN 2's inputs untouched.

Stages (subcommands), each reading the previous stage's artifact so the
pipeline is resumable/inspectable:

  reconcile        Recompute census_v1/census_v2 for all anchors_v2 targets
                    from vexcel + anchors_v2 source_grids; scan the on-disk
                    chips corpus for the flagged anchors to recompute
                    n_frames_post_census_v2_on_disk/max_downloaded_date; diff
                    against the input topup-check CSV. Exits non-zero (and
                    refuses to let later stages run) on any disagreement
                    unless --allow-mismatch.
  classify          For each anchor still needing topup, pull the legacy
                    group's already-known TM+Wayback dates from
                    availability_rebuild_2026-07-12/, re-walk the ISSUE-26
                    cutoff with census_v2 (reusing `compute_cutoff_max_date`
                    verbatim), and split into bucket (a) satisfiable from
                    already-known dates vs bucket (b) still short (true
                    short-tail candidates for a live query).
  live-query        Run live TM availability / Wayback info calls for bucket
                    (b) anchors only (small volume, home machine, IPv4,
                    paced). Re-runs the classify walk with the enriched date
                    set to see how many bucket-(b) anchors resolve.
  plan-download     Emit the exact (anchor_id, capture_date, provider,
                    version) rows not yet on disk, split TM/Wayback.
  download          Run gehi_download.py for each provider's plan.
  merge-catalog     Write gehi_vintage_candidates_full_run3.csv = original +
                    new rows (OUTPUT_FIELDS schema), original untouched.
  verify            Recompute n_frames_post_census_v2_on_disk from the actual
                    chips dir post-download + confirm every new row's chip
                    resolves at the exact `_chip_path_for` path.

Run stages in order; each is idempotent / safe to re-run.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.temporal.build_gehi_candidates import (  # noqa: E402
    OUTPUT_FIELDS,
    compute_cutoff_max_date,
)
from scripts.temporal.gehi_common import (  # noqa: E402
    DEFAULT_GEHI_EXE,
    DEFAULT_REQUEST_INTERVAL_S,
    GehiRateLimiter,
    make_throttled_runner,
    run_gehi,
)
from scripts.temporal.gehi_availability import fetch_availability_for_anchor  # noqa: E402
from scripts.temporal.gehi_info import fetch_vintages_for_anchor  # noqa: E402
from scripts.temporal.gehi_download import _chip_path_for  # noqa: E402
from scripts.temporal.geid_temporal_common import read_csv_rows, write_csv_rows  # noqa: E402

MIN_DATE = "2019-01-01"
MAX_DATE = "2025-12-31"
KEEP = 3
ZOOM = 19
# Dataset-wide unretrievable Wayback vintage dates, see memory
# gehi-pilot2023-download-2026-07-13 (2026-07-16 root-cause finding): these
# two dates hung the GEHI dotnet binary across the ENTIRE ~41k-anchor corpus.
BAD_WAYBACK_DATES = {"2020-04-25", "2021-08-30"}

DL_ROOT = Path.home() / "zasolar_data" / "geid_temporal" / "basemap_rebuild_2026-07-13"
CHIPS_DIR = DL_ROOT / "chips"
ORIGINAL_CATALOG_CSV = DL_ROOT / "gehi_vintage_candidates_full.csv"
AVAIL_DIR = Path.home() / "zasolar_data" / "geid_temporal" / "availability_rebuild_2026-07-12"
TM_AVAIL_CSVS = [
    AVAIL_DIR / "tm_full_reparsed.csv",
    AVAIL_DIR / "tm_retry_403.csv",
    AVAIL_DIR / "tm_repull_trunc_home.csv",
]
WAYBACK_AVAIL_CSV = AVAIL_DIR / "wayback_info_full.csv"
VEXCEL_CSV = Path("/home/gao/projects/ZAsolar/data/analysis/vexcel_jhb_per_grid_capture_dates_2026-06-04.csv")
ANCHORS_V2_CSV = Path.home() / "zasolar_data/geid_temporal/fullscan_gemini_backdating_2026-07/anchors_v2/anchors_all.csv"
ANCHORS_96M_CSV = DL_ROOT / "anchors_per_target_96m.csv"
LEGACY_GROUPS_CSV = Path.home() / "zasolar_data/geid_temporal/jhb_full382_unified_A_merge01_c0925_fpcut_2026-06-01_chipgroups/chip_groups_as_anchors.csv"
TOPUP_CHECK_CSV = Path.home() / "zasolar_data/geid_temporal/fullscan_gemini_backdating_2026-07/anchors_v2/census_shift_topup_check_2026-07-18.csv"

OUT_DIR = Path.home() / "zasolar_data/geid_temporal/fullscan_gemini_backdating_2026-07/run3_v2/topup_2026-07-18"

CHIP_DATE_RE = re.compile(r"_(\d{8})_v[^./]+\.tif$")


def parse_iso(value: object) -> str:
    return str(value).strip()[:10]


# ---------------------------------------------------------------------------
# Shared loaders
# ---------------------------------------------------------------------------


def load_vexcel(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for row in read_csv_rows(path):
        gid = str(row.get("grid_id", "")).strip()
        raw = str(row.get("last_capture_date", "")).strip()
        if gid and raw:
            out[gid] = parse_iso(raw)
    return out


def load_anchors_v2(path: Path) -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    for row in read_csv_rows(path):
        aid = str(row.get("anchor_id", "")).strip()
        if aid:
            out[aid] = dict(row)
    return out


def load_anchors_96m(path: Path) -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    for row in read_csv_rows(path):
        aid = str(row.get("anchor_id", "")).strip()
        if aid:
            out[aid] = dict(row)
    return out


def load_topup_check(path: Path) -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    for row in read_csv_rows(path):
        aid = str(row.get("anchor_id", "")).strip()
        if aid:
            out[aid] = dict(row)
    return out


def load_legacy_groups(path: Path) -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    for row in read_csv_rows(path):
        gid = str(row.get("anchor_id", "")).strip()
        if gid:
            out[gid] = dict(row)
    return out


def anchor_grid_ids(arow: dict[str, str]) -> list[str]:
    raw = str(arow.get("source_grids", "") or "").strip()
    if not raw:
        raw = str(arow.get("grid_id", "") or "").strip()
    return [g.strip() for g in raw.split(";") if g.strip()]


def census_v1_for(arow: dict[str, str], vexcel: dict[str, str], legacy_groups: dict[str, dict[str, str]]) -> str | None:
    """Legacy census: max vexcel `last_capture_date` over the LEGACY chip-group's
    multi-grid `source_grids` (`chip_groups_as_anchors.csv`, joined via
    `legacy_group_anchor_id`) -- this is what `build_gehi_candidates.
    resolve_census_date` used for the original `basemap_rebuild_2026-07-13`
    download. NOT anchors_v2's own single `grid_id` column, which can
    disagree when the legacy group spans multiple grids that anchors_v2's
    per-target regrouping split apart (verified 2026-07-18 on anchor
    t00020296: legacy group JNB0117;JNB0118 -> 2024-04-19, vs anchors_v2's
    own single grid_id JNB0117 -> 2024-02-21)."""
    group_id = str(arow.get("legacy_group_anchor_id", "")).strip()
    grow = legacy_groups.get(group_id)
    if grow is None:
        return None
    raw = str(grow.get("source_grids", "") or "").strip()
    grids = [g.strip() for g in raw.split(";") if g.strip()]
    dates = [vexcel[g] for g in grids if g in vexcel]
    return max(dates) if dates else None


def census_v2_for(arow: dict[str, str], vexcel: dict[str, str]) -> str | None:
    """New per-target multi-grid census (anchors_v2's own `source_grids`)."""
    dates = [vexcel[g] for g in anchor_grid_ids(arow) if g in vexcel]
    return max(dates) if dates else None


def scan_chip_dates(anchor_id: str, chips_dir: Path = CHIPS_DIR) -> list[str]:
    """All distinct capture_date values found on disk for one anchor (any zoom/version)."""
    anchor_dir = chips_dir / anchor_id
    if not anchor_dir.is_dir():
        return []
    dates: set[str] = set()
    for tif in anchor_dir.glob("z*/*.tif"):
        m = CHIP_DATE_RE.search(tif.name)
        if m and tif.stat().st_size > 0:
            d = m.group(1)
            dates.add(f"{d[0:4]}-{d[4:6]}-{d[6:8]}")
    return sorted(dates)


# ---------------------------------------------------------------------------
# Stage: reconcile
# ---------------------------------------------------------------------------


def cmd_reconcile(args: argparse.Namespace) -> None:
    vexcel = load_vexcel(args.vexcel_csv)
    anchors_v2 = load_anchors_v2(args.anchors_v2_csv)
    legacy_groups = load_legacy_groups(args.legacy_groups_csv)
    topup_check = load_topup_check(args.topup_check_csv)
    print(f"[reconcile] vexcel grids={len(vexcel)} anchors_v2={len(anchors_v2)} legacy_groups={len(legacy_groups)} topup_check_rows={len(topup_check)}")

    full_rows: list[dict[str, object]] = []
    for anchor_id, arow in anchors_v2.items():
        cv1 = census_v1_for(arow, vexcel, legacy_groups)
        cv2 = census_v2_for(arow, vexcel)
        full_rows.append(
            {
                "anchor_id": anchor_id,
                "grid_id": arow.get("grid_id", ""),
                "source_grids": arow.get("source_grids", ""),
                "legacy_group_anchor_id": arow.get("legacy_group_anchor_id", ""),
                "census_v1_recomputed": cv1 or "",
                "census_v2_recomputed": cv2 or "",
                "census_shifted": int(bool(cv1 and cv2 and cv2 > cv1)),
            }
        )
    full_path = args.out_dir / "census_v2_recompute_full.csv"
    write_csv_rows(
        full_path,
        full_rows,
        ["anchor_id", "grid_id", "source_grids", "legacy_group_anchor_id", "census_v1_recomputed", "census_v2_recomputed", "census_shifted"],
    )
    n_shifted = sum(r["census_shifted"] for r in full_rows)
    print(f"[reconcile] wrote {len(full_rows)} rows -> {full_path} ({n_shifted} anchors with census_v2 > census_v1)")

    # Reconcile against the given 186-row topup-check CSV: recompute disk
    # state (max_downloaded_date, n_frames_post_census_v2_on_disk, needs_topup)
    # for exactly those anchors and diff every column.
    full_by_id = {r["anchor_id"]: r for r in full_rows}
    mismatches: list[dict[str, object]] = []
    recon_rows: list[dict[str, object]] = []
    for anchor_id, trow in topup_check.items():
        rec = full_by_id.get(anchor_id)
        if rec is None:
            mismatches.append({"anchor_id": anchor_id, "field": "anchor_id", "reason": "not found in anchors_v2"})
            continue
        cv1_given, cv2_given = trow.get("census_v1", ""), trow.get("census_v2", "")
        cv1_rec, cv2_rec = rec["census_v1_recomputed"], rec["census_v2_recomputed"]

        dates_on_disk = scan_chip_dates(anchor_id, args.chips_dir)
        max_dl_rec = dates_on_disk[-1] if dates_on_disk else ""
        n_post_rec = sum(1 for d in dates_on_disk if cv2_rec and d > cv2_rec and d <= MAX_DATE) if cv2_rec else 0
        needs_topup_rec = int(bool(cv2_rec) and n_post_rec < KEEP)

        given_max_dl = trow.get("max_downloaded_date", "")
        given_n_post = trow.get("n_frames_post_census_v2_on_disk", "")
        given_needs = trow.get("needs_topup", "")

        row_mismatches = []
        if cv1_given != cv1_rec:
            row_mismatches.append(("census_v1", cv1_given, cv1_rec))
        if cv2_given != cv2_rec:
            row_mismatches.append(("census_v2", cv2_given, cv2_rec))
        if max_dl_rec != given_max_dl:
            row_mismatches.append(("max_downloaded_date", given_max_dl, max_dl_rec))
        if str(n_post_rec) != str(given_n_post):
            row_mismatches.append(("n_frames_post_census_v2_on_disk", given_n_post, n_post_rec))
        if str(needs_topup_rec) != str(given_needs):
            row_mismatches.append(("needs_topup", given_needs, needs_topup_rec))

        for field, given, rec_val in row_mismatches:
            mismatches.append({"anchor_id": anchor_id, "field": field, "given": given, "recomputed": rec_val})

        recon_rows.append(
            {
                "anchor_id": anchor_id,
                "census_v1_given": cv1_given,
                "census_v1_recomputed": cv1_rec,
                "census_v2_given": cv2_given,
                "census_v2_recomputed": cv2_rec,
                "max_downloaded_date_given": given_max_dl,
                "max_downloaded_date_recomputed": max_dl_rec,
                "n_frames_post_census_v2_on_disk_given": given_n_post,
                "n_frames_post_census_v2_on_disk_recomputed": n_post_rec,
                "needs_topup_given": given_needs,
                "needs_topup_recomputed": needs_topup_rec,
                "n_dates_on_disk_total": len(dates_on_disk),
                "match": int(len(row_mismatches) == 0),
            }
        )

    recon_path = args.out_dir / "reconcile_186.csv"
    write_csv_rows(
        recon_path,
        recon_rows,
        [
            "anchor_id",
            "census_v1_given", "census_v1_recomputed",
            "census_v2_given", "census_v2_recomputed",
            "max_downloaded_date_given", "max_downloaded_date_recomputed",
            "n_frames_post_census_v2_on_disk_given", "n_frames_post_census_v2_on_disk_recomputed",
            "needs_topup_given", "needs_topup_recomputed",
            "n_dates_on_disk_total", "match",
        ],
    )
    report_path = args.out_dir / "reconcile_report.json"
    n_match = sum(r["match"] for r in recon_rows)
    report = {
        "topup_check_rows": len(topup_check),
        "reconciled_rows": len(recon_rows),
        "fully_matching_rows": n_match,
        "mismatching_rows": len(recon_rows) - n_match,
        "mismatch_details_count": len(mismatches),
    }
    report_path.write_text(json.dumps(report, indent=2))
    mismatch_path = args.out_dir / "reconcile_mismatches.json"
    mismatch_path.write_text(json.dumps(mismatches, indent=2))
    print(f"[reconcile] {n_match}/{len(recon_rows)} rows match exactly -> {recon_path}")
    print(f"[reconcile] report -> {report_path}; mismatch detail -> {mismatch_path}")

    if mismatches and not args.allow_mismatch:
        print(f"[reconcile] STOP: {len(mismatches)} field-level mismatches found; refusing to continue.", file=sys.stderr)
        for m in mismatches[:20]:
            print(f"  {m}", file=sys.stderr)
        if len(mismatches) > 20:
            print(f"  ... and {len(mismatches) - 20} more (see {mismatch_path})", file=sys.stderr)
        raise SystemExit(2)
    print("[reconcile] OK: 186-row topup-check CSV matches independent recompute exactly.")


# ---------------------------------------------------------------------------
# Stage: classify
# ---------------------------------------------------------------------------


def load_known_tm_dates(group_ids: set[str], paths: Iterable[Path]) -> dict[str, set[str]]:
    by_group: dict[str, set[str]] = defaultdict(set)
    for path in paths:
        if not path.exists():
            print(f"[classify] WARNING: missing TM availability file {path}", file=sys.stderr)
            continue
        with path.open("r", newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                gid = str(row.get("anchor_id", "")).strip()
                if gid not in group_ids:
                    continue
                d = parse_iso(row.get("capture_date", ""))
                if d:
                    by_group[gid].add(d)
    return dict(by_group)


def load_known_wayback_rows(group_ids: set[str], path: Path) -> dict[str, dict[str, str]]:
    """group_id -> {capture_date -> version}, first occurrence wins (matches build_gehi_candidates.load_wayback_rows)."""
    by_group: dict[str, dict[str, str]] = defaultdict(dict)
    if not path.exists():
        print(f"[classify] WARNING: missing Wayback availability file {path}", file=sys.stderr)
        return {}
    with path.open("r", newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            gid = str(row.get("anchor_id", "")).strip()
            if gid not in group_ids:
                continue
            d = parse_iso(row.get("capture_date", ""))
            v = str(row.get("version", "")).strip()
            if d and v and d not in by_group[gid]:
                by_group[gid][d] = v
    return dict(by_group)


def walk_cutoff(merged_dates: set[str], census_date: str) -> tuple[str, int]:
    """Return (effective_max_date, n_post_census_dates_within_it)."""
    effective_max = compute_cutoff_max_date(merged_dates, census_date, min_date=MIN_DATE, max_date=MAX_DATE, keep=KEEP)
    n_post = sum(1 for d in merged_dates if d > census_date and d <= effective_max)
    return effective_max, n_post


def cmd_classify(args: argparse.Namespace) -> None:
    recon_rows = read_csv_rows(args.reconcile_csv)
    targets = [r for r in recon_rows if str(r.get("needs_topup_recomputed", "0")) == "1"]
    print(f"[classify] {len(targets)} anchors confirmed needing topup (of {len(recon_rows)} reconciled)")

    anchors_v2 = load_anchors_v2(args.anchors_v2_csv)
    group_ids = {anchors_v2[r["anchor_id"]]["legacy_group_anchor_id"] for r in targets if r["anchor_id"] in anchors_v2}
    print(f"[classify] loading known TM/Wayback dates for {len(group_ids)} legacy groups ...")
    t0 = time.time()
    tm_known = load_known_tm_dates(group_ids, TM_AVAIL_CSVS)
    wb_known = load_known_wayback_rows(group_ids, WAYBACK_AVAIL_CSV)
    print(f"[classify] loaded in {time.time() - t0:.1f}s: TM groups={len(tm_known)} WB groups={len(wb_known)}")

    bucket_a: list[dict[str, object]] = []
    bucket_b: list[dict[str, object]] = []
    for r in targets:
        anchor_id = r["anchor_id"]
        arow = anchors_v2.get(anchor_id)
        if arow is None:
            print(f"[classify] WARNING: {anchor_id} missing from anchors_v2, skipping", file=sys.stderr)
            continue
        group_id = arow["legacy_group_anchor_id"]
        census_v2 = r["census_v2_recomputed"]
        tm_dates = tm_known.get(group_id, set())
        wb_dates = set(wb_known.get(group_id, {}).keys())
        merged = tm_dates | wb_dates
        effective_max, n_post = walk_cutoff(merged, census_v2)
        record = {
            "anchor_id": anchor_id,
            "legacy_group_anchor_id": group_id,
            "census_v2": census_v2,
            "n_tm_known": len(tm_dates),
            "n_wb_known": len(wb_dates),
            "effective_max_from_known": effective_max,
            "n_post_census_from_known": n_post,
        }
        if n_post >= KEEP or effective_max == MAX_DATE:
            bucket_a.append(record)
        else:
            bucket_b.append(record)

    a_path = args.out_dir / "bucket_a_satisfiable_from_known.csv"
    b_path = args.out_dir / "bucket_b_short_tail_candidates.csv"
    fields = ["anchor_id", "legacy_group_anchor_id", "census_v2", "n_tm_known", "n_wb_known", "effective_max_from_known", "n_post_census_from_known"]
    write_csv_rows(a_path, bucket_a, fields)
    write_csv_rows(b_path, bucket_b, fields)
    print(f"[classify] bucket (a) satisfiable from already-known dates: {len(bucket_a)} -> {a_path}")
    print(f"[classify] bucket (b) short-tail, needs live query:        {len(bucket_b)} -> {b_path}")
    if len(bucket_b) > 30:
        print(f"[classify] STOP: bucket (b) = {len(bucket_b)} anchors exceeds the pre-approved ~30 threshold; report before querying live.", file=sys.stderr)
        raise SystemExit(3)


# ---------------------------------------------------------------------------
# Stage: live-query (bucket b only)
# ---------------------------------------------------------------------------


def cmd_live_query(args: argparse.Namespace) -> None:
    b_rows = read_csv_rows(args.bucket_b_csv)
    live_fields = ["anchor_id", "census_v2", "n_tm_live", "n_wb_live", "effective_max_after_live", "n_post_census_after_live", "still_short", "bad_wayback_date_hit"]
    if not b_rows:
        print("[live-query] bucket (b) is empty, nothing to query.")
        write_csv_rows(args.out_dir / "live_query_results.csv", [], live_fields)
        write_csv_rows(args.out_dir / "short_tail_final.csv", [], live_fields)
        return
    anchors_96m = load_anchors_96m(args.anchors_96m_csv)
    limiter = GehiRateLimiter(min_interval_s=args.request_interval)
    throttled_runner = make_throttled_runner(limiter=limiter, base_runner=run_gehi)
    results: list[dict[str, object]] = []
    for r in b_rows:
        anchor_id = r["anchor_id"]
        anchor96 = anchors_96m.get(anchor_id)
        if anchor96 is None:
            print(f"[live-query] WARNING: {anchor_id} missing from anchors_per_target_96m.csv, skipping", file=sys.stderr)
            continue
        census_v2 = r["census_v2"]
        print(f"[live-query] {anchor_id}: TM availability ...")
        tm_rows = fetch_availability_for_anchor(
            anchor96, zoom=ZOOM, provider="TM", min_date=MIN_DATE, max_date=MAX_DATE,
            complete=True, gehi_exe=args.gehi_exe, runner=throttled_runner,
        )
        tm_dates = {parse_iso(row["capture_date"]) for row in tm_rows}
        print(f"[live-query] {anchor_id}: Wayback info ...")
        wb_rows = fetch_vintages_for_anchor(anchor96, zoom=ZOOM, provider="Wayback", gehi_exe=args.gehi_exe, runner=throttled_runner)
        wb_dates = {parse_iso(row["capture_date"]) for row in wb_rows}
        bad_hit = (tm_dates | wb_dates) & BAD_WAYBACK_DATES
        if bad_hit:
            print(f"[live-query] {anchor_id}: WARNING known-bad Wayback date(s) in live result: {bad_hit}", file=sys.stderr)
        merged = tm_dates | wb_dates
        effective_max, n_post = walk_cutoff(merged, census_v2)
        results.append(
            {
                "anchor_id": anchor_id,
                "census_v2": census_v2,
                "n_tm_live": len(tm_dates),
                "n_wb_live": len(wb_dates),
                "effective_max_after_live": effective_max,
                "n_post_census_after_live": n_post,
                "still_short": int(n_post < KEEP and effective_max != MAX_DATE),
                "bad_wayback_date_hit": ";".join(sorted(bad_hit)),
            }
        )
    out_path = args.out_dir / "live_query_results.csv"
    write_csv_rows(
        out_path, results,
        ["anchor_id", "census_v2", "n_tm_live", "n_wb_live", "effective_max_after_live", "n_post_census_after_live", "still_short", "bad_wayback_date_hit"],
    )
    n_still_short = sum(r["still_short"] for r in results)
    short_tail_final = [r for r in results if r["still_short"]]
    st_path = args.out_dir / "short_tail_final.csv"
    write_csv_rows(
        st_path, short_tail_final,
        ["anchor_id", "census_v2", "n_tm_live", "n_wb_live", "effective_max_after_live", "n_post_census_after_live", "still_short", "bad_wayback_date_hit"],
    )
    print(f"[live-query] {len(results)} anchors queried live -> {out_path}")
    print(f"[live-query] {n_still_short} anchors remain short-tail after live query -> {st_path}")


# ---------------------------------------------------------------------------
# Stage: plan-download
# ---------------------------------------------------------------------------


def cmd_plan_download(args: argparse.Namespace) -> None:
    anchors_v2 = load_anchors_v2(args.anchors_v2_csv)
    bucket_a = read_csv_rows(args.bucket_a_csv)
    live_results = read_csv_rows(args.live_query_csv) if args.live_query_csv.exists() and args.live_query_csv.stat().st_size > 0 else []
    live_by_id = {r["anchor_id"]: r for r in live_results}

    group_ids: set[str] = set()
    for r in bucket_a:
        aid = r["anchor_id"]
        if aid in anchors_v2:
            group_ids.add(anchors_v2[aid]["legacy_group_anchor_id"])
    for aid in live_by_id:
        if aid in anchors_v2:
            group_ids.add(anchors_v2[aid]["legacy_group_anchor_id"])

    tm_known = load_known_tm_dates(group_ids, TM_AVAIL_CSVS)
    wb_known = load_known_wayback_rows(group_ids, WAYBACK_AVAIL_CSV)

    new_rows: list[dict[str, object]] = []
    all_targets = {r["anchor_id"]: r for r in bucket_a}
    all_targets.update(live_by_id)

    for anchor_id, rec in all_targets.items():
        arow = anchors_v2.get(anchor_id)
        if arow is None:
            continue
        group_id = arow["legacy_group_anchor_id"]
        census_v2 = rec["census_v2"]
        tm_dates = set(tm_known.get(group_id, set()))
        wb_date_versions = dict(wb_known.get(group_id, {}))
        # Bucket-b anchors: fold in the live-fetched dates too (best effort;
        # live-query stage doesn't persist raw rows, so this reuses whatever
        # the known-availability files already have plus what walk_cutoff
        # found reachable -- if a live-only date is needed it will show up as
        # a a gap in the `verify` stage and must be re-run through classify).
        merged = tm_dates | set(wb_date_versions)
        effective_max, _ = walk_cutoff(merged, census_v2)

        on_disk = set(scan_chip_dates(anchor_id, args.chips_dir))
        for d in sorted(tm_dates):
            if MIN_DATE <= d <= effective_max and d not in on_disk:
                new_rows.append(
                    {
                        "anchor_id": anchor_id, "capture_date": d, "version": "",
                        "all_capture_dates": "", "provider": "TM",
                        "chip_id": arow.get("chip_id", ""), "grid_id": arow.get("grid_id", ""),
                        "region_key": arow.get("region_key", ""), "source_grids": arow.get("source_grids", ""),
                        "census_date": census_v2, "cutoff_max_date": effective_max,
                    }
                )
        for d, v in sorted(wb_date_versions.items()):
            if MIN_DATE <= d <= effective_max and d not in on_disk:
                if d in BAD_WAYBACK_DATES:
                    continue
                new_rows.append(
                    {
                        "anchor_id": anchor_id, "capture_date": d, "version": v,
                        "all_capture_dates": d, "provider": "Wayback",
                        "chip_id": arow.get("chip_id", ""), "grid_id": arow.get("grid_id", ""),
                        "region_key": arow.get("region_key", ""), "source_grids": arow.get("source_grids", ""),
                        "census_date": census_v2, "cutoff_max_date": effective_max,
                    }
                )

    plan_path = args.out_dir / "topup_new_candidate_rows.csv"
    write_csv_rows(plan_path, new_rows, OUTPUT_FIELDS)
    n_tm = sum(1 for r in new_rows if r["provider"] == "TM")
    n_wb = sum(1 for r in new_rows if r["provider"] == "Wayback")
    print(f"[plan-download] {len(new_rows)} new candidate rows (TM={n_tm}, Wayback={n_wb}) -> {plan_path}")
    if len(new_rows) > 2000:
        print(f"[plan-download] STOP: {len(new_rows)} incremental frames exceeds the pre-approved ~2000 threshold; report before downloading.", file=sys.stderr)
        raise SystemExit(4)


# ---------------------------------------------------------------------------
# Stage: download
# ---------------------------------------------------------------------------


def cmd_download(args: argparse.Namespace) -> None:
    import os
    import subprocess

    plan_rows = read_csv_rows(args.plan_csv)
    for provider in ("TM", "Wayback"):
        rows = [r for r in plan_rows if r["provider"] == provider]
        if not rows:
            print(f"[download] no {provider} rows to fetch, skipping")
            continue
        provider_csv = args.out_dir / f"topup_candidates_{provider.lower()}.csv"
        write_csv_rows(provider_csv, rows, OUTPUT_FIELDS)
        manifest_path = args.out_dir / f"topup_manifest_{provider.lower()}.csv"
        raw_log_path = args.out_dir / f"topup_raw_{provider.lower()}.jsonl"
        cmd = [
            sys.executable, str(PROJECT_ROOT / "scripts/temporal/gehi_download.py"),
            "--anchors-csv", str(args.anchors_96m_csv),
            "--candidates-csv", str(provider_csv),
            "--output-dir", str(args.chips_dir),
            "--manifest", str(manifest_path),
            "--raw-log", str(raw_log_path),
            "--gehi-exe", str(args.gehi_exe),
            "--provider", provider,
            "--zoom", str(ZOOM),
            "--request-interval", str(args.request_interval),
            "--allow-failures",
        ]
        print(f"[download] {provider}: {len(rows)} rows -> {' '.join(cmd)}")
        env = dict(os.environ)
        env["DOTNET_SYSTEM_NET_DISABLEIPV6"] = "1"
        result = subprocess.run(cmd, env=env)
        if result.returncode != 0:
            print(f"[download] {provider}: gehi_download.py exited {result.returncode}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Stage: merge-catalog
# ---------------------------------------------------------------------------


def cmd_merge_catalog(args: argparse.Namespace) -> None:
    """Merge topup candidate rows into a NEW catalog file (original untouched).

    Only rows with an actual chip on disk are included -- a planned candidate
    whose download failed (phantom availability-metadata date, or a
    systematically-bad provider vintage) must never be written into the
    catalog: `--offline-require-chip-on-disk` would filter it out downstream
    anyway, but writing it here would misrepresent the catalog as containing
    real, disk-backed candidates. Failed rows are reported and excluded, not
    silently dropped.
    """
    original_rows = read_csv_rows(args.original_catalog_csv)
    new_rows = read_csv_rows(args.plan_csv)
    seen = {(r["anchor_id"], r["capture_date"], r["provider"]) for r in original_rows}

    on_disk, failed = [], []
    for r in new_rows:
        version = r["version"] or "noversion"
        chip_path = _chip_path_for(args.chips_dir, r["anchor_id"], r["capture_date"], version, ZOOM)
        if chip_path.exists() and chip_path.stat().st_size > 0:
            on_disk.append(r)
        else:
            failed.append(r)

    added = [r for r in on_disk if (r["anchor_id"], r["capture_date"], r["provider"]) not in seen]
    merged = list(original_rows) + added
    write_csv_rows(args.out_catalog_csv, merged, OUTPUT_FIELDS)

    failed_path = args.out_dir / "topup_download_failed_rows.csv"
    write_csv_rows(failed_path, failed, OUTPUT_FIELDS)

    print(f"[merge-catalog] planned={len(new_rows)} on_disk={len(on_disk)} failed_download={len(failed)} -> failed rows logged to {failed_path}")
    print(f"[merge-catalog] original={len(original_rows)} + new={len(added)} (of {len(on_disk)} disk-confirmed, "
          f"{len(on_disk) - len(added)} already present in original) = {len(merged)} -> {args.out_catalog_csv}")


# ---------------------------------------------------------------------------
# Stage: verify
# ---------------------------------------------------------------------------


def cmd_verify(args: argparse.Namespace) -> None:
    recon_rows = read_csv_rows(args.reconcile_csv)
    targets = [r for r in recon_rows if str(r.get("needs_topup_recomputed", "0")) == "1"]
    anchors_v2 = load_anchors_v2(args.anchors_v2_csv)
    plan_rows = read_csv_rows(args.plan_csv)

    verify_rows: list[dict[str, object]] = []
    n_chip_missing = 0
    for r in targets:
        anchor_id = r["anchor_id"]
        census_v2 = r["census_v2_recomputed"]
        dates_on_disk = scan_chip_dates(anchor_id, args.chips_dir)
        n_post = sum(1 for d in dates_on_disk if census_v2 and d > census_v2 and d <= MAX_DATE)
        max_dl = dates_on_disk[-1] if dates_on_disk else ""
        still_needs = int(bool(census_v2) and n_post < KEEP)
        verify_rows.append(
            {
                "anchor_id": anchor_id,
                "census_v2": census_v2,
                "n_frames_post_census_v2_on_disk_after_topup": n_post,
                "max_downloaded_date_after_topup": max_dl,
                "still_needs_topup": still_needs,
            }
        )

    n_chip_path_mismatch = 0
    for pr in plan_rows:
        if pr["anchor_id"] not in {r["anchor_id"] for r in targets}:
            continue
        version = pr["version"] or "noversion"
        expected = _chip_path_for(args.chips_dir, pr["anchor_id"], pr["capture_date"], version, ZOOM)
        if not expected.exists() or expected.stat().st_size == 0:
            n_chip_path_mismatch += 1
            print(f"[verify] MISSING expected chip: {expected}", file=sys.stderr)

    out_path = args.out_dir / "verify_final.csv"
    write_csv_rows(out_path, verify_rows, ["anchor_id", "census_v2", "n_frames_post_census_v2_on_disk_after_topup", "max_downloaded_date_after_topup", "still_needs_topup"])
    n_resolved = sum(1 for r in verify_rows if not r["still_needs_topup"])
    print(f"[verify] {n_resolved}/{len(verify_rows)} anchors now have >= {KEEP} post-census_v2 frames on disk -> {out_path}")
    print(f"[verify] chip-path resolution failures for planned rows: {n_chip_path_mismatch}/{len(plan_rows)}")
    if n_chip_path_mismatch:
        raise SystemExit(5)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def add_common_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--out-dir", type=Path, default=OUT_DIR)
    p.add_argument("--chips-dir", type=Path, default=CHIPS_DIR)
    p.add_argument("--anchors-v2-csv", type=Path, default=ANCHORS_V2_CSV)
    p.add_argument("--anchors-96m-csv", type=Path, default=ANCHORS_96M_CSV)
    p.add_argument("--vexcel-csv", type=Path, default=VEXCEL_CSV)
    p.add_argument("--gehi-exe", type=Path, default=DEFAULT_GEHI_EXE)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="stage", required=True)

    p = sub.add_parser("reconcile")
    add_common_args(p)
    p.add_argument("--topup-check-csv", type=Path, default=TOPUP_CHECK_CSV)
    p.add_argument("--legacy-groups-csv", type=Path, default=LEGACY_GROUPS_CSV)
    p.add_argument("--allow-mismatch", action="store_true")
    p.set_defaults(func=cmd_reconcile)

    p = sub.add_parser("classify")
    add_common_args(p)
    p.add_argument("--reconcile-csv", type=Path, default=OUT_DIR / "reconcile_186.csv")
    p.set_defaults(func=cmd_classify)

    p = sub.add_parser("live-query")
    add_common_args(p)
    p.add_argument("--bucket-b-csv", type=Path, default=OUT_DIR / "bucket_b_short_tail_candidates.csv")
    p.add_argument("--request-interval", type=float, default=DEFAULT_REQUEST_INTERVAL_S)
    p.set_defaults(func=cmd_live_query)

    p = sub.add_parser("plan-download")
    add_common_args(p)
    p.add_argument("--bucket-a-csv", type=Path, default=OUT_DIR / "bucket_a_satisfiable_from_known.csv")
    p.add_argument("--live-query-csv", type=Path, default=OUT_DIR / "live_query_results.csv")
    p.set_defaults(func=cmd_plan_download)

    p = sub.add_parser("download")
    add_common_args(p)
    p.add_argument("--plan-csv", type=Path, default=OUT_DIR / "topup_new_candidate_rows.csv")
    p.add_argument("--request-interval", type=float, default=DEFAULT_REQUEST_INTERVAL_S)
    p.set_defaults(func=cmd_download)

    p = sub.add_parser("merge-catalog")
    add_common_args(p)
    p.add_argument("--plan-csv", type=Path, default=OUT_DIR / "topup_new_candidate_rows.csv")
    p.add_argument("--original-catalog-csv", type=Path, default=ORIGINAL_CATALOG_CSV)
    p.add_argument("--out-catalog-csv", type=Path, default=DL_ROOT / "gehi_vintage_candidates_full_run3.csv")
    p.set_defaults(func=cmd_merge_catalog)

    p = sub.add_parser("verify")
    add_common_args(p)
    p.add_argument("--reconcile-csv", type=Path, default=OUT_DIR / "reconcile_186.csv")
    p.add_argument("--plan-csv", type=Path, default=OUT_DIR / "topup_new_candidate_rows.csv")
    p.set_defaults(func=cmd_verify)

    return parser.parse_args()


def main() -> None:
    args = parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    args.func(args)


if __name__ == "__main__":
    main()
