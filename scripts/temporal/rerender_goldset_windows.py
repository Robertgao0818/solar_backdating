#!/usr/bin/env python3
"""WP-C: LLM-free window-chip re-render for the ISSUE-10 gold set.

The adaptive-scan chips were deleted in disk cleanup, so the jump-window frames a
human needs to adjudicate must be re-rendered from retained scan metadata. This
tool reads a sample-assignments CSV (WP-B output) and, per sampled anchor:

1. resolves the jump-window frames (``latest_absent`` / ``earliest_present`` +/-
   flanks) from the retained ``scan_states/<anchor_id>.json`` (frame selection
   reuses ``run_census2023_scan._anchor_frames``);
2. joins the anchor bbox from the chipgroups CSV (scan_states carry NO
   coordinates) and re-renders each frame with the shared GEHistoricalImagery
   zoom-ladder path (``download_chip_with_zoom_ladder``);
3. NORMALIZES each recovered scan chip into the flat per-anchor dir under the
   builder's documented convention ``scan_<capture_date>_v<version>.png``. The
   zoom-ladder helper leaves chips nested under a ``z<zoom>/`` subdir named
   ``<anchor>_<YYYYMMDD>_v<ver>.tif``, but WP-A resolves scan frames with a
   NON-recursive glob for ``scan_<date>_v<ver>.(png|tif)`` in the flat anchor
   dir — so without this step the strip embeds ZERO jump-window frames. The
   normalized artifact is a browser-embeddable PNG produced via the shared
   phase-0 ``ensure_review_png`` conversion (same RGB + anchor marker the QA
   page shows). Idempotent: an already-materialized flat chip is reused.
4. copies the CoJ municipal true-date chips (already on disk) into the same
   per-anchor dir.

Idempotent (skip-if-exists at every step) and LLM-free. Unrecoverable frames
(``all_zooms_failed`` from the zoom ladder, or a missing CoJ source chip) are
DROPPED AND REPORTED in ``rerender/frame_report.csv`` and never crash the batch;
WP-A renders a captioned ``PLACEHOLDER_PIXEL`` slot for each drop.

Network seam: the GEHI subprocess is injected as ``runner`` (defaults to the live
``run_gehi``); unit tests pass a fake runner so no ``.NET`` binary or download is
needed. This mirrors the ``test_gehi_zoom_ladder`` precedent.
"""

# Imports follow a sys.path bootstrap (below) so the subrepo can be run as a
# script; E402 is expected for the scripts.* imports, matching the sibling
# temporal modules' convention.
# ruff: noqa: E402

from __future__ import annotations

import argparse
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.temporal.gehi_common import (
    DEFAULT_GEHI_EXE,
    DEFAULT_PROVIDER,
    GehiRunResult,
    ensure_review_png,
    run_gehi,
)
from scripts.temporal.gehi_download import DownloadResult, download_chip_with_zoom_ladder
from scripts.temporal.geid_temporal_common import read_csv_rows, write_csv_rows
from scripts.temporal.goldset_schema import (
    goldset_root,
    read_sample_assignments,
    rerender_chips_dir,
    rerender_report_path,
)
from scripts.temporal.run_census2023_scan import _anchor_frames
from scripts.temporal.scan_state import ScanState, load_scan_state, state_path_for

#: CoJ municipal true-date vintages available as on-disk chips (READ-ONLY cohort).
DEFAULT_COJ_YEARS: tuple[int, ...] = (2015, 2019, 2023)

#: Default number of usable scan slots to include on each side of the bracket.
DEFAULT_FLANK = 2

#: Per-frame outcome report — one row per resolved window frame (scan + CoJ).
FRAME_REPORT_FIELDS: tuple[str, ...] = (
    "anchor_id",
    "source",                 # scan_tm | coj_2015 | coj_2019 | coj_2023
    "role",                   # latest_absent | earliest_present | flank_* | reference
    "capture_date",
    "version",
    "requested_zoom_ladder",
    "achieved_zoom",
    "status",                 # ok | skipped_existing | all_zooms_failed | copied | source_missing | ...
    "chip_path",
    "error",
)

#: Statuses that mean the frame is on disk and usable by the strip builder.
RECOVERED_STATUSES: frozenset[str] = frozenset({"ok", "skipped_existing", "copied"})


@dataclass
class FrameSpec:
    """One jump-window scan frame to re-render, resolved from scan metadata."""

    source: str          # "scan_tm"
    role: str            # FRAME_ROLES value
    capture_date: str
    version: int | None
    actual_zoom: int | None


# ---------------------------------------------------------------------------
# Frame resolution — reuse the canonical bracket selector, add flanks

def _usable_observations(state: ScanState) -> list:
    """Usable, decided observations with a (now-dead but retained) chip_path.

    Mirrors ``_anchor_frames``' own filter so flank selection draws from exactly
    the same slot pool as the two bounds. ``chip_path`` is a retained string even
    though the file was deleted, so this filter still admits every scanned slot.
    """
    return [
        r
        for rnd in state.rounds
        for r in rnd.results
        if r.quality_flag == "usable" and r.pv_present is not None and (r.chip_path or "")
    ]


def _spec(result, role: str) -> FrameSpec:
    return FrameSpec(
        source="scan_tm",
        role=role,
        capture_date=str(result.capture_date),
        version=result.version,
        actual_zoom=result.actual_zoom,
    )


def resolve_window_frames(state: ScanState, *, flank: int = DEFAULT_FLANK) -> list[FrameSpec]:
    """Resolve the jump-window scan frames for one anchor.

    Bounds come from ``_anchor_frames`` (single source of truth for
    latest-absent / earliest-present). Flanks are the ``flank`` usable scan slots
    immediately before ``latest_absent`` (role ``flank_before``) and immediately
    after ``earliest_present`` (role ``flank_after``), by ``capture_date``. Each
    (capture_date, version) slot is emitted at most once; the bracket bounds win
    over a flank role for the same slot.
    """
    latest_absent, earliest_present = _anchor_frames(state)
    usable = sorted(_usable_observations(state), key=lambda r: str(r.capture_date))

    frames: list[FrameSpec] = []
    seen: set[tuple[str, object]] = set()

    def _emit(result, role: str) -> None:
        key = (str(result.capture_date), result.version)
        if key in seen:
            return
        seen.add(key)
        frames.append(_spec(result, role))

    if latest_absent is not None:
        _emit(latest_absent, "latest_absent")
    if earliest_present is not None:
        _emit(earliest_present, "earliest_present")

    if latest_absent is not None and flank > 0:
        before = [
            r
            for r in usable
            if str(r.capture_date) < str(latest_absent.capture_date)
            and (str(r.capture_date), r.version) not in seen
        ]
        for r in before[-flank:]:  # nearest-before = the tail of the ascending list
            _emit(r, "flank_before")

    if earliest_present is not None and flank > 0:
        after = [
            r
            for r in usable
            if str(r.capture_date) > str(earliest_present.capture_date)
            and (str(r.capture_date), r.version) not in seen
        ]
        for r in after[:flank]:  # nearest-after = the head of the ascending list
            _emit(r, "flank_after")

    return frames


def frame_zoom_ladder(
    actual_zoom: int | None, *, override: Sequence[int] | None = None
) -> tuple[int, ...]:
    """Zoom ladder for one frame.

    An explicit ``override`` (CLI ``--zoom-ladder``) wins. Otherwise the frame's
    own ``actual_zoom`` (the rung the pipeline actually rendered at, so its
    vintage is known present there) is the primary rung, with one coarser rung as
    a resilience fallback. Falls back to ``(19, 18)`` when no zoom was recorded.
    """
    if override:
        return tuple(int(z) for z in override)
    if actual_zoom is None:
        return (19, 18)
    z = int(actual_zoom)
    return (z, z - 1) if z > 1 else (z,)


# ---------------------------------------------------------------------------
# Bbox join — chipgroups CSV is the ONLY coordinate source

def load_bbox_index(chipgroups_csv: Path) -> dict[str, dict[str, str]]:
    """Index chipgroups rows by ``anchor_id`` for the bbox join.

    scan_states carry no coordinates, so the bbox for GEHI's ``--lower-left`` /
    ``--upper-right`` must come from the chipgroups ``chip_{lon,lat}_{min,max}``
    columns. Never invent a bbox — an anchor absent here is reported, not guessed.
    """
    return {row["anchor_id"]: row for row in read_csv_rows(chipgroups_csv)}


# ---------------------------------------------------------------------------
# Per-frame re-render

def _report_row(
    anchor_id: str,
    *,
    source: str,
    role: str,
    capture_date: str,
    version: object,
    requested_zoom_ladder: Sequence[int] | str,
    achieved_zoom: object,
    status: str,
    chip_path: object,
    error: object,
) -> dict[str, object]:
    if isinstance(requested_zoom_ladder, str):
        ladder_str = requested_zoom_ladder
    else:
        ladder_str = ",".join(str(z) for z in requested_zoom_ladder)
    return {
        "anchor_id": anchor_id,
        "source": source,
        "role": role,
        "capture_date": capture_date,
        "version": "" if version is None else version,
        "requested_zoom_ladder": ladder_str,
        "achieved_zoom": "" if achieved_zoom is None else achieved_zoom,
        "status": status,
        "chip_path": "" if chip_path is None else str(chip_path),
        "error": "" if error is None else str(error),
    }


def _flat_scan_chip_name(capture_date: str, version: object, suffix: str) -> str:
    """Builder-facing flat filename ``scan_<date>_v<version><suffix>``.

    Mirrors WP-A's ``resolve_scan_chip`` glob ``scan_{capture_date}_v{version}.*``
    (ISO capture_date + integer scan version). The ``_v<version>`` part is dropped
    when no version is recorded; WP-A's loose fallback still matches on the date.
    """
    vpart = "" if version is None or str(version).strip() == "" else f"_v{version}"
    return f"scan_{capture_date}{vpart}{suffix}"


def _materialize_flat_scan_chip(
    source_chip: Path,
    flat_dir: Path,
    capture_date: str,
    version: object,
) -> Path | None:
    """Copy/convert a recovered scan chip into the flat builder convention.

    ``download_chip_with_zoom_ladder`` leaves the chosen-zoom chip nested under a
    ``z<zoom>/`` subdir; WP-A globs the flat anchor dir non-recursively for
    ``scan_<date>_v<ver>.(png|tif)``. This bridges the two by materializing a
    browser-embeddable PNG (reusing the shared phase-0 ``ensure_review_png``
    conversion — same RGB + anchor marker as the QA page) at the flat path.

    Idempotent: a non-empty flat PNG (or the TIFF fallback) already present is
    returned without re-converting, so a 2nd LLM-free run does no extra work.
    Returns the flat chip path, or ``None`` if nothing could be materialized.
    """
    png_target = flat_dir / _flat_scan_chip_name(capture_date, version, ".png")
    if png_target.exists() and png_target.stat().st_size > 0:
        return png_target
    tif_target = flat_dir / _flat_scan_chip_name(capture_date, version, ".tif")
    if tif_target.exists() and tif_target.stat().st_size > 0:
        return tif_target
    if not source_chip.exists() or source_chip.stat().st_size == 0:
        return None
    flat_dir.mkdir(parents=True, exist_ok=True)
    try:
        review_png = ensure_review_png(source_chip)  # sibling PNG next to nested TIFF
        shutil.copy2(review_png, png_target)
        return png_target
    except Exception:  # noqa: BLE001 - never break the batch on an unreadable raster
        try:
            shutil.copy2(source_chip, tif_target)  # builder converts .tif on demand
            return tif_target
        except OSError:
            return None


def _render_scan_frame(
    anchor_id: str,
    bbox_row: Mapping[str, object],
    frame: FrameSpec,
    *,
    chips_root: Path,
    provider: str,
    gehi_exe: Path,
    zoom_override: Sequence[int] | None,
    timeout: float,
    runner: Callable[..., GehiRunResult],
) -> dict[str, object]:
    ladder = frame_zoom_ladder(frame.actual_zoom, override=zoom_override)
    outcome: DownloadResult = download_chip_with_zoom_ladder(
        bbox_row,
        capture_date=frame.capture_date,
        version="" if frame.version is None else frame.version,
        zoom_ladder=ladder,
        output_root=chips_root,
        provider=provider,
        gehi_exe=gehi_exe,
        timeout=timeout,
        runner=runner,
    )
    chip_path: object = outcome.path
    if outcome.status in RECOVERED_STATUSES and outcome.path is not None:
        flat = _materialize_flat_scan_chip(
            Path(outcome.path),
            chips_root / anchor_id,
            frame.capture_date,
            frame.version,
        )
        if flat is not None:
            chip_path = flat
    return _report_row(
        anchor_id,
        source=frame.source,
        role=frame.role,
        capture_date=frame.capture_date,
        version=frame.version,
        requested_zoom_ladder=ladder,
        achieved_zoom=outcome.actual_zoom,
        status=outcome.status,
        chip_path=chip_path,
        error=outcome.error,
    )


def _copy_coj_chip(
    anchor_id: str,
    year: int,
    *,
    chips_root: Path,
    coj_chips_dir: Path | None,
) -> dict[str, object]:
    """Idempotently copy one CoJ municipal chip into the per-anchor re-render dir.

    Drop-and-report: a CoJ vintage that has no chip for this anchor (the cohort
    is only partially covered — e.g. the 2019 dir holds ~2,688 of ~12,491) yields
    a ``source_missing`` row, never a crash.
    """
    source = f"coj_{year}"
    dst = chips_root / anchor_id / f"coj_{year}.tif"
    if dst.exists() and dst.stat().st_size > 0:
        return _report_row(
            anchor_id, source=source, role="reference", capture_date="", version=None,
            requested_zoom_ladder="", achieved_zoom=None, status="skipped_existing",
            chip_path=dst, error=None,
        )
    src = None if coj_chips_dir is None else coj_chips_dir / str(year) / f"{anchor_id}.tif"
    if src is None or not src.exists() or src.stat().st_size == 0:
        return _report_row(
            anchor_id, source=source, role="reference", capture_date="", version=None,
            requested_zoom_ladder="", achieved_zoom=None, status="source_missing",
            chip_path=None, error=f"no CoJ {year} chip at {src}",
        )
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    return _report_row(
        anchor_id, source=source, role="reference", capture_date="", version=None,
        requested_zoom_ladder="", achieved_zoom=None, status="copied",
        chip_path=dst, error=None,
    )


def rerender_anchor(
    anchor_id: str,
    state: ScanState | None,
    bbox_row: Mapping[str, object] | None,
    *,
    chips_root: Path,
    provider: str = DEFAULT_PROVIDER,
    gehi_exe: Path = DEFAULT_GEHI_EXE,
    zoom_override: Sequence[int] | None = None,
    flank: int = DEFAULT_FLANK,
    timeout: float = 600.0,
    coj_chips_dir: Path | None = None,
    coj_years: Sequence[int] = DEFAULT_COJ_YEARS,
    runner: Callable[..., GehiRunResult] = run_gehi,
) -> list[dict[str, object]]:
    """Re-render every window frame for one anchor; return its frame-report rows.

    Missing scan_state or missing bbox are drop-and-report (anchor-level rows),
    not exceptions — per the design, disputes now resolve to c-anchors that own
    real scan_states + bbox, so these branches only guard genuinely broken input.
    """
    if state is None:
        return [_report_row(
            anchor_id, source="scan_tm", role="", capture_date="", version=None,
            requested_zoom_ladder="", achieved_zoom=None, status="scan_state_missing",
            chip_path=None, error="no scan_state for anchor",
        )]
    if bbox_row is None:
        return [_report_row(
            anchor_id, source="scan_tm", role="", capture_date="", version=None,
            requested_zoom_ladder="", achieved_zoom=None, status="bbox_missing",
            chip_path=None, error="anchor absent from chipgroups CSV (no bbox)",
        )]

    rows: list[dict[str, object]] = []
    for frame in resolve_window_frames(state, flank=flank):
        rows.append(_render_scan_frame(
            anchor_id, bbox_row, frame,
            chips_root=chips_root, provider=provider, gehi_exe=gehi_exe,
            zoom_override=zoom_override, timeout=timeout, runner=runner,
        ))
    for year in coj_years:
        rows.append(_copy_coj_chip(anchor_id, int(year), chips_root=chips_root, coj_chips_dir=coj_chips_dir))
    return rows


def rerender_from_assignments(
    assignments_csv: Path,
    *,
    scan_states_dir: Path,
    chipgroups_csv: Path,
    root: Path,
    coj_chips_dir: Path | None = None,
    coj_years: Sequence[int] = DEFAULT_COJ_YEARS,
    provider: str = DEFAULT_PROVIDER,
    gehi_exe: Path = DEFAULT_GEHI_EXE,
    zoom_override: Sequence[int] | None = None,
    flank: int = DEFAULT_FLANK,
    timeout: float = 600.0,
    runner: Callable[..., GehiRunResult] = run_gehi,
) -> list[dict[str, object]]:
    """Drive the whole batch: unique anchors -> frame rows -> frame_report.csv.

    Assignments carry two rows (A and B) per anchor; each anchor is re-rendered
    exactly once (dedup by first appearance). Returns the frame-report rows and
    writes them to ``rerender/frame_report.csv``.
    """
    chips_root = rerender_chips_dir(root)
    bbox_index = load_bbox_index(chipgroups_csv)

    assignments = read_sample_assignments(assignments_csv)
    seen_anchors: set[str] = set()
    ordered_anchors: list[str] = []
    for row in assignments:
        aid = (row.get("anchor_id") or "").strip()
        if aid and aid not in seen_anchors:
            seen_anchors.add(aid)
            ordered_anchors.append(aid)

    report_rows: list[dict[str, object]] = []
    for anchor_id in ordered_anchors:
        state_path = state_path_for(anchor_id, scan_states_dir)
        state = load_scan_state(state_path) if state_path.exists() else None
        bbox_row = bbox_index.get(anchor_id)
        report_rows.extend(rerender_anchor(
            anchor_id, state, bbox_row,
            chips_root=chips_root, provider=provider, gehi_exe=gehi_exe,
            zoom_override=zoom_override, flank=flank, timeout=timeout,
            coj_chips_dir=coj_chips_dir, coj_years=coj_years, runner=runner,
        ))

    write_csv_rows(rerender_report_path(root), report_rows, FRAME_REPORT_FIELDS)
    return report_rows


# ---------------------------------------------------------------------------
# CLI

def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--assignments", type=Path, required=True, help="sample_assignments.csv (WP-B output)")
    p.add_argument("--scan-states-dir", type=Path, required=True, help="dir of retained scan_states/<anchor_id>.json")
    p.add_argument("--chipgroups-csv", type=Path, required=True, help="chip_groups_as_anchors.csv (bbox source)")
    p.add_argument("--coj-chips-dir", type=Path, default=None, help="CoJ chips root with {2015,2019,2023}/<anchor>.tif")
    p.add_argument("--tag", type=str, required=True, help="gold-set tag, e.g. dryrun_20260705")
    p.add_argument("--root", type=Path, default=None, help="override output root (default: goldset_root(tag))")
    p.add_argument("--provider", default=DEFAULT_PROVIDER, help="GEHI provider for scan frames (TM | Wayback)")
    p.add_argument("--gehi-exe", type=Path, default=DEFAULT_GEHI_EXE)
    p.add_argument("--zoom-ladder", type=str, default="", help="override zoom ladder for all frames, e.g. '20,19'")
    p.add_argument("--flank", type=int, default=DEFAULT_FLANK, help="usable slots to include each side of the bracket")
    p.add_argument("--timeout", type=float, default=600.0)
    # `--geometry-version` is accepted for runbook symmetry; WP-C renders the full
    # banked-96 chip (the context the pipeline decided on), so it takes no other value.
    p.add_argument("--geometry-version", default="chip_geom_v1_banked96")
    return p.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    args = parse_args(argv)
    root = args.root or goldset_root(args.tag)
    zoom_override = None
    if args.zoom_ladder.strip():
        zoom_override = tuple(int(z) for z in args.zoom_ladder.split(",") if z.strip())

    rows = rerender_from_assignments(
        args.assignments,
        scan_states_dir=args.scan_states_dir,
        chipgroups_csv=args.chipgroups_csv,
        root=root,
        coj_chips_dir=args.coj_chips_dir,
        provider=args.provider,
        gehi_exe=args.gehi_exe,
        zoom_override=zoom_override,
        flank=args.flank,
        timeout=args.timeout,
    )
    recovered = sum(1 for r in rows if r["status"] in RECOVERED_STATUSES)
    dropped = len(rows) - recovered
    print(f"Re-rendered {len(rows)} frames ({recovered} recovered, {dropped} dropped/reported)")
    print(f"Frame report -> {rerender_report_path(root)}")
    print(f"Chips -> {rerender_chips_dir(root)}")


if __name__ == "__main__":
    main()
