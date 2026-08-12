#!/usr/bin/env python3
"""WP-C: LLM-free window-chip re-render for the ISSUE-10 gold set.

The adaptive-scan chips were deleted in disk cleanup, so the jump-window frames a
human needs to adjudicate must be re-rendered from retained scan metadata. This
tool reads a sample-assignments CSV (WP-B output) and, per sampled anchor:

1. resolves the jump-window frames from the assignment's frozen claimed
   ``latest_absent_date`` / ``earliest_present_date`` when present, then adds
   flanks from ``scan_states/<anchor_id>.json``. Legacy assignments without
   claimed bounds fall back to ``run_census2023_scan._anchor_frames``;
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
import bisect
import re
import shutil
import sys
from collections import Counter, defaultdict
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
    _target_token,
    decode_target_ids,
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

#: Default number of full-stack artifact slots to flank the modal-FPD frame with,
#: measured in INDEX positions over the chip's date-sorted frozen-artifact list (NOT a
#: date range). Index-bounding inherently caps the noisy-target blow-up (a chaotic
#: target's long vote-run collapses to <=2*flank+1 frames). ISSUE-11 prep default = 2.
DEFAULT_FULLSTACK_FLANK = 2

#: Per-frame outcome report — one row per resolved window frame (scan + CoJ).
FRAME_REPORT_FIELDS: tuple[str, ...] = (
    "anchor_id",
    "source",                 # scan_tm | fullstack | coj_2015 | coj_2019 | coj_2023
    "role",                   # latest_absent | earliest_present | flank_* | reference
    "capture_date",
    "version",
    "requested_zoom_ladder",
    "achieved_zoom",
    "status",                 # ok | skipped_existing | all_zooms_failed | copied | source_missing | ...
    "chip_path",
    "error",
)

#: Statuses that mean the frame is on disk and usable by the strip builder. The three
#: ``fullstack_*``/``gehi_fallback_ok`` recovered statuses are the ISSUE-11 dispute
#: extension (WI-3): a frozen full-stack tif copied into an ``fsarm_`` chip, an
#: idempotent skip, or a dead frozen path re-fetched via the GEHI fallback.
RECOVERED_STATUSES: frozenset[str] = frozenset(
    {
        "ok",
        "skipped_existing",
        "copied",
        "fullstack_copied",
        "fullstack_skipped_existing",
        "gehi_fallback_ok",
    }
)

#: Caption sidecar (``rerender/fullstack_frames.csv``) written by WP-C alongside the
#: frame report — one row per on-disk full-stack (``fsarm_``) chip. WP-A reads it to
#: build the per-target FPD captions on the distinct full-stack row. ``off_roof_marker``
#: is dormant plumbing (no automated signal in the specified inputs — see WI-3 report).
FULLSTACK_FRAMES_SIDECAR_FIELDS: tuple[str, ...] = (
    "anchor_id",
    "capture_date",
    "version",
    "chip_filename",
    "owning_target_ids",   # ";"-joined short target ids that claimed this frame
    "claimed_fpds",        # ";"-joined modal FPD(s), aligned to owning_target_ids
    "is_modal_fpd_frame",  # "True"/"False" — the ◀ claimed-FPD marker frame
    "status",
    "actual_zoom",         # frozen artifact's zoom (WP-A caption z<zoom>); beyond design's 8 cols
    "off_roof_marker",     # "" unless a geometry signal marks the anchor (dormant plumbing)
)


@dataclass
class FrameSpec:
    """One jump-window scan frame to re-render, resolved from scan metadata."""

    source: str          # "scan_tm"
    role: str            # FRAME_ROLES value
    capture_date: str
    version: int | None
    actual_zoom: int | None
    chip_path: str = ""   # retained production chip; reuse before any GEHI fallback
    note: str = ""       # analysis-side annotation, never shown


@dataclass
class FullstackFrame:
    """One frozen full-stack artifact selected for a dispute anchor's recovered window.

    ``source_path`` is the frozen ``.tif`` path from ``artifacts_fullstack.csv`` (alive
    on disk => copied into an ``fsarm_`` chip; dead => GEHI fallback). The caption fields
    ride into ``fullstack_frames.csv`` for WP-A's per-target ``FPD=<date>`` captions.
    """

    capture_date: str
    version: int | None
    source_path: str
    actual_zoom: int | None
    owning_target_ids: tuple[str, ...] = ()   # short ids whose modal-neighborhood took it
    claimed_fpds: tuple[str, ...] = ()        # aligned to owning_target_ids
    is_modal_fpd_frame: bool = False


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
        if r.quality_flag == "usable"
        and r.pv_present is not None
        and not getattr(r, "reference_only", False)
        and (r.chip_path or "")
    ]


def _spec(result, role: str) -> FrameSpec:
    return FrameSpec(
        source="scan_tm",
        role=role,
        capture_date=str(result.capture_date),
        version=result.version,
        actual_zoom=result.actual_zoom,
        chip_path=str(result.chip_path or ""),
    )


def resolve_window_frames(
    state: ScanState,
    *,
    flank: int = DEFAULT_FLANK,
    claimed_latest_absent: str | None = None,
    claimed_earliest_present: str | None = None,
) -> list[FrameSpec]:
    """Resolve the jump-window scan frames for one anchor.

    Bounds come from the frozen sample assignment when either claimed-bound
    argument is supplied. This is required for dip-repaired intervals: taking
    the raw last absent from scan state can select a later contradictory frame
    and silently omit the actual deliverable boundary. Legacy callers that
    provide neither argument retain the historical ``_anchor_frames`` behavior.
    Flanks are the ``flank`` usable scan slots
    immediately before ``latest_absent`` (role ``flank_before``) and immediately
    after ``earliest_present`` (role ``flank_after``), by ``capture_date``. Each
    (capture_date, version) slot is emitted at most once; the bracket bounds win
    over a flank role for the same slot.
    """
    usable = sorted(_usable_observations(state), key=lambda r: str(r.capture_date))

    def _claimed(date: str | None, role: str):
        value = (date or "").strip()[:10]
        if not value:
            return None
        matches = [result for result in usable if str(result.capture_date)[:10] == value]
        if not matches:
            raise ValueError(
                f"{state.anchor_id}: claimed {role}={value} is absent from usable scan results"
            )
        return matches[0]

    if claimed_latest_absent is not None or claimed_earliest_present is not None:
        latest_absent = _claimed(claimed_latest_absent, "latest_absent")
        earliest_present = _claimed(claimed_earliest_present, "earliest_present")
    else:
        latest_absent, earliest_present = _anchor_frames(state)

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
# Full-stack recovered-window resolution (dispute anchors only)
#
# The production scan gave up on the 15 dated-vs-undated disputes (degenerate
# scan_state -> empty production window). The full-stack arm DID score them; its
# frozen-TIFF inventory lives in ``fullstack_noscan_*/artifacts_fullstack.csv``
# (``chip_id`` = owning c-anchor, one row per on-disk frozen ``.tif``) and its
# per-target modal first-present-date (FPD) lives in
# ``analysis/per_unit_fullstack.csv`` (``unit`` = stringified ``(chip_id, target_id,
# label)`` tuple, ``fpd_reps`` = the 10 reps' claimed FPDs or UNDATED).
#
# Per owned dispute target we take the MODAL FPD date(s) and select the artifacts
# within +-flank INDEX positions of that date in the chip's date-sorted artifact list
# (NOT a date range — index-bounding caps a noisy target's blow-up). A tie unions both
# modal dates' neighborhoods; an all-UNDATED target yields no frames + one
# ``fullstack_no_window`` report row. Frames union across the anchor's targets, deduped
# by ``(capture_date, version)``, are COPIED (LLM-free) from the frozen tif into a
# distinct ``fsarm_`` chip — GEHI is only a fallback for a dead frozen path. See
# ``docs/replan_v2/ISSUE-11-prep-design-2026-07-05.md`` §WI-3.


def load_fullstack_artifacts(artifacts_csv: Path) -> dict[str, list[dict[str, object]]]:
    """Group ``artifacts_fullstack.csv`` rows by ``chip_id`` (the owning c-anchor).

    Each retained row carries ``capture_date`` / ``version`` (int|None) / ``path``
    (frozen tif) / ``actual_zoom`` (int|None) / ``status``; the per-chip list is sorted
    by ``(capture_date, version)`` so index-flank neighborhoods are deterministic. An
    anchor absent here has no full-stack coverage (banner-only, WI-3 AC-3.4).
    """
    out: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in read_csv_rows(artifacts_csv):
        chip_id = (row.get("chip_id") or "").strip()
        date = (row.get("capture_date") or "").strip()[:10]
        if not chip_id or not date:
            continue
        ver_raw = (row.get("version") or "").strip()
        zoom_raw = (row.get("actual_zoom") or "").strip()
        out[chip_id].append(
            {
                "capture_date": date,
                "version": int(ver_raw) if ver_raw.lstrip("-").isdigit() else None,
                "path": (row.get("path") or "").strip(),
                "actual_zoom": int(zoom_raw) if zoom_raw.lstrip("-").isdigit() else None,
                "status": (row.get("status") or "").strip(),
            }
        )
    return {
        chip_id: sorted(
            rows,
            key=lambda r: (str(r["capture_date"]), r["version"] if r["version"] is not None else -1),
        )
        for chip_id, rows in out.items()
    }


#: Parses the leading ``('<chip_id>', '<target_id>', ...`` of a stringified unit tuple.
_UNIT_TUPLE_RE = re.compile(r"""^\(\s*['"](?P<chip>[^'"]+)['"]\s*,\s*['"](?P<target>[^'"]+)['"]""")


def parse_unit_tuple(unit: str) -> tuple[str, str] | None:
    """``"('chip', 'target', 'T01')"`` -> ``(chip_id, target_id)`` (None if unparseable)."""
    m = _UNIT_TUPLE_RE.match((unit or "").strip())
    return (m.group("chip"), m.group("target")) if m else None


def parse_fpd_reps(raw: str) -> list[str]:
    """Extract the per-rep FPD dates from ``FPD|<date>|FPD|<date>…`` / ``UNDATED`` tokens.

    ``UNDATED`` reps contribute no date; a rep with a real first-present date contributes
    it (truncated to ISO ``YYYY-MM-DD``). Order preserved; empty input -> ``[]``.
    """
    toks = [t.strip() for t in (raw or "").split("|")]
    dates: list[str] = []
    i = 0
    while i < len(toks):
        if toks[i] == "FPD" and i + 1 < len(toks):
            d = toks[i + 1][:10]
            if d and d != "UNDATED":
                dates.append(d)
            i += 2
        else:
            i += 1
    return dates


def modal_fpd_dates(raw: str) -> list[str]:
    """Modal FPD date(s) over the reps — a list (>1 on a tie), ``[]`` when all UNDATED."""
    dates = parse_fpd_reps(raw)
    if not dates:
        return []
    counts = Counter(dates)
    top = max(counts.values())
    return sorted(d for d, n in counts.items() if n == top)


def load_fullstack_units(units_csv: Path) -> dict[tuple[str, str], list[str]]:
    """Index ``per_unit_fullstack.csv`` by ``(chip_id, target_token)`` -> modal FPD list.

    ``[]`` value = the target's reps are all UNDATED (no modal window). A target absent
    from this map has no per-unit row at all (also treated as no window upstream).
    """
    out: dict[tuple[str, str], list[str]] = {}
    for row in read_csv_rows(units_csv):
        parsed = parse_unit_tuple(row.get("unit", ""))
        if parsed is None:
            continue
        chip_id, target_id = parsed
        tok = _target_token(target_id)
        if not tok:
            continue
        out[(chip_id, tok)] = modal_fpd_dates(row.get("fpd_reps", ""))
    return out


def _index_flank_window(
    artifacts: Sequence[Mapping[str, object]], dates_sorted: Sequence[str], modal_date: str, flank: int
) -> set[int]:
    """Artifact indices within +-``flank`` INDEX positions of ``modal_date``.

    Exact matches anchor the window on their own index span; a modal date absent from
    the artifact list falls back to its ``bisect`` insertion point so the neighbourhood
    still gives temporal context (no frame is then marked modal).
    """
    hits = [i for i, a in enumerate(artifacts) if a["capture_date"] == modal_date]
    if hits:
        lo = max(0, min(hits) - flank)
        hi = min(len(artifacts), max(hits) + flank + 1)
    else:
        p = bisect.bisect_left(list(dates_sorted), modal_date)
        lo = max(0, p - flank)
        hi = min(len(artifacts), p + flank)
    return set(range(lo, hi))


def resolve_fullstack_frames(
    chip_id: str,
    dispute_target_ids: Sequence[str],
    artifacts: Sequence[Mapping[str, object]],
    units_index: Mapping[tuple[str, str], list[str]],
    *,
    flank: int = DEFAULT_FULLSTACK_FLANK,
) -> tuple[list[FullstackFrame], list[str]]:
    """Select one dispute c-anchor's recovered full-stack window frames.

    Returns ``(frames, no_window_target_tokens)``. ``frames`` are the union across the
    anchor's owned targets of each target's modal-FPD +-flank INDEX neighbourhood, deduped
    by ``(capture_date, version)`` and carrying every owning target + claimed FPD for the
    caption. ``no_window_target_tokens`` are the owned targets whose reps are all UNDATED
    (AC-3.6) — one ``fullstack_no_window`` report row each.
    """
    dates_sorted = [str(a["capture_date"]) for a in artifacts]

    # owned target tokens, order-preserving dedup.
    tokens: list[str] = []
    seen_tok: set[str] = set()
    for raw in dispute_target_ids:
        tok = _target_token(raw)
        if tok and tok not in seen_tok:
            seen_tok.add(tok)
            tokens.append(tok)

    acc: dict[tuple[str, object], dict[str, object]] = {}
    no_window: list[str] = []
    for tok in tokens:
        modal = units_index.get((chip_id, tok))
        if not modal:  # None (no per-unit row) or [] (all UNDATED) -> no datable window
            no_window.append(tok)
            continue
        idxs: set[int] = set()
        for md in modal:
            idxs |= _index_flank_window(artifacts, dates_sorted, md, flank)
        claimed_label = "/".join(modal)
        for i in sorted(idxs):
            a = artifacts[i]
            key = (str(a["capture_date"]), a["version"])
            entry = acc.setdefault(
                key, {"artifact": a, "owning": [], "claimed": {}, "modal": False}
            )
            if tok not in entry["owning"]:
                entry["owning"].append(tok)
            entry["claimed"][tok] = claimed_label
            if str(a["capture_date"]) in modal:
                entry["modal"] = True

    frames: list[FullstackFrame] = []
    for key in sorted(acc, key=lambda k: (k[0], k[1] if k[1] is not None else -1)):
        e = acc[key]
        a = e["artifact"]
        owning = sorted(e["owning"])
        frames.append(
            FullstackFrame(
                capture_date=str(a["capture_date"]),
                version=a["version"],
                source_path=str(a["path"]),
                actual_zoom=a["actual_zoom"],
                owning_target_ids=tuple(owning),
                claimed_fpds=tuple(e["claimed"][t] for t in owning),
                is_modal_fpd_frame=bool(e["modal"]),
            )
        )
    return frames, no_window


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


#: Maps a source to its builder-facing flat filename prefix. ``scan_`` is the production
#: adaptive-scan frame; the dispute full-stack frame is materialized separately with the
#: ``fsarm_`` prefix (a DISTINCT namespace WP-A globs for its own second row, and which
#: WP-A's ``resolve_scan_chip`` explicitly excludes so it is never mis-read as a scan).
_SOURCE_FLAT_PREFIX: dict[str, str] = {"scan_tm": "scan"}

#: Flat filename prefix for a dispute anchor's full-stack recovered-window chip.
FULLSTACK_FLAT_PREFIX = "fsarm"


def _flat_scan_chip_name(capture_date: str, version: object, suffix: str, *, prefix: str = "scan") -> str:
    """Builder-facing flat filename ``<prefix>_<date>_v<version><suffix>``.

    Mirrors WP-A's ``resolve_scan_chip`` glob ``scan_{capture_date}_v{version}.*``
    (ISO capture_date + integer scan version) for ``prefix="scan"``; the parallel
    ``fsarm_`` prefix is what WP-A globs to discover full-stack recovered-window frames.
    The ``_v<version>`` part is dropped when no version is recorded; WP-A's loose
    fallback still matches on the date.
    """
    vpart = "" if version is None or str(version).strip() == "" else f"_v{version}"
    return f"{prefix}_{capture_date}{vpart}{suffix}"


def _materialize_flat_scan_chip(
    source_chip: Path,
    flat_dir: Path,
    capture_date: str,
    version: object,
    *,
    prefix: str = "scan",
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
    png_target = flat_dir / _flat_scan_chip_name(capture_date, version, ".png", prefix=prefix)
    if png_target.exists() and png_target.stat().st_size > 0:
        return png_target
    tif_target = flat_dir / _flat_scan_chip_name(capture_date, version, ".tif", prefix=prefix)
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

    # CT production retains its frozen GEHI chips.  Reusing those exact bytes is
    # both the strongest provenance path and materially faster than downloading
    # the same vintage again.  Older ISSUE-10 runs may still carry dead paths,
    # so only fall through to the existing zoom-ladder fetch when the retained
    # path is absent or empty.
    retained = Path(frame.chip_path) if frame.chip_path else None
    if retained is not None and retained.is_file() and retained.stat().st_size > 0:
        flat = _materialize_flat_scan_chip(
            retained,
            chips_root / anchor_id,
            frame.capture_date,
            frame.version,
            prefix=_SOURCE_FLAT_PREFIX.get(frame.source, "scan"),
        )
        if flat is not None:
            return _report_row(
                anchor_id,
                source=frame.source,
                role=frame.role,
                capture_date=frame.capture_date,
                version=frame.version,
                requested_zoom_ladder=ladder,
                achieved_zoom=frame.actual_zoom,
                status="skipped_existing",
                chip_path=flat,
                error=frame.note or None,
            )

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
    prefix = _SOURCE_FLAT_PREFIX.get(frame.source, "scan")
    if outcome.status in RECOVERED_STATUSES and outcome.path is not None:
        flat = _materialize_flat_scan_chip(
            Path(outcome.path),
            chips_root / anchor_id,
            frame.capture_date,
            frame.version,
            prefix=prefix,
        )
        if flat is not None:
            chip_path = flat
    # full-stack rep-support (frame.note) rides in the report `error` column when the
    # frame itself is fine — analysis-side provenance, never surfaced on the strip.
    error = outcome.error if outcome.error else (frame.note or None)
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
        error=error,
    )


# ---------------------------------------------------------------------------
# Full-stack frame materialization (frozen-tif copy; GEHI only as a dead-path fallback)

def _existing_fsarm_chip(flat_dir: Path, capture_date: str, version: object) -> Path | None:
    """Return an already-materialized ``fsarm_`` chip (png preferred, then tif), or None."""
    png = flat_dir / _flat_scan_chip_name(capture_date, version, ".png", prefix=FULLSTACK_FLAT_PREFIX)
    if png.exists() and png.stat().st_size > 0:
        return png
    tif = flat_dir / _flat_scan_chip_name(capture_date, version, ".tif", prefix=FULLSTACK_FLAT_PREFIX)
    if tif.exists() and tif.stat().st_size > 0:
        return tif
    return None


def _gehi_fallback_fullstack(
    frame: FullstackFrame,
    bbox_row: Mapping[str, object] | None,
    *,
    chips_root: Path,
    flat_dir: Path,
    provider: str,
    gehi_exe: Path,
    zoom_override: Sequence[int] | None,
    timeout: float,
    runner: Callable[..., GehiRunResult],
) -> tuple[str, object, object]:
    """Re-fetch a dead frozen full-stack frame via the GEHI zoom ladder.

    Returns ``(status, chip_path, achieved_zoom)``. No bbox / ``all_zooms_failed`` ->
    ``fullstack_source_missing`` (AC-3.5); a live fetch materialized into an ``fsarm_``
    chip -> ``gehi_fallback_ok``; a fetch that lands but cannot be materialized ->
    ``gehi_fallback_failed``.
    """
    if bbox_row is None:
        return "fullstack_source_missing", None, None
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
    if outcome.status in ("ok", "skipped_existing") and outcome.path is not None:
        flat = _materialize_flat_scan_chip(
            Path(outcome.path), flat_dir, frame.capture_date, frame.version,
            prefix=FULLSTACK_FLAT_PREFIX,
        )
        if flat is not None:
            return "gehi_fallback_ok", flat, outcome.actual_zoom
        return "gehi_fallback_failed", None, outcome.actual_zoom
    return "fullstack_source_missing", None, outcome.actual_zoom


def _render_fullstack_frames(
    anchor_id: str,
    frames: Sequence[FullstackFrame],
    no_window_targets: Sequence[str],
    bbox_row: Mapping[str, object] | None,
    *,
    chips_root: Path,
    provider: str = DEFAULT_PROVIDER,
    gehi_exe: Path = DEFAULT_GEHI_EXE,
    zoom_override: Sequence[int] | None = None,
    timeout: float = 600.0,
    runner: Callable[..., GehiRunResult] = run_gehi,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    """Materialize a dispute anchor's full-stack frames; return (report_rows, sidecar_rows).

    A frozen tif alive on disk is COPIED (LLM-free, no GEHI) into an ``fsarm_`` chip
    (``fullstack_copied`` / idempotent ``fullstack_skipped_existing``); a dead frozen path
    falls back to the injected GEHI runner. Every on-disk chip also emits a
    ``fullstack_frames.csv`` sidecar row carrying its owning targets + claimed FPD(s) for
    WP-A's captions. Each all-UNDATED owned target adds one ``fullstack_no_window`` row.
    """
    report_rows: list[dict[str, object]] = []
    sidecar_rows: list[dict[str, object]] = []
    flat_dir = chips_root / anchor_id
    for frame in frames:
        existing = _existing_fsarm_chip(flat_dir, frame.capture_date, frame.version)
        achieved: object = None
        if existing is not None:
            status, chip_path = "fullstack_skipped_existing", existing
        else:
            src = Path(frame.source_path) if frame.source_path else None
            if src is not None and src.exists() and src.stat().st_size > 0:
                flat = _materialize_flat_scan_chip(
                    src, flat_dir, frame.capture_date, frame.version, prefix=FULLSTACK_FLAT_PREFIX,
                )
                if flat is not None:
                    status, chip_path = "fullstack_copied", flat
                else:
                    status, chip_path = "fullstack_source_missing", None
            else:
                status, chip_path, achieved = _gehi_fallback_fullstack(
                    frame, bbox_row, chips_root=chips_root, flat_dir=flat_dir,
                    provider=provider, gehi_exe=gehi_exe, zoom_override=zoom_override,
                    timeout=timeout, runner=runner,
                )
        report_rows.append(_report_row(
            anchor_id, source="fullstack", role="fullstack_window",
            capture_date=frame.capture_date, version=frame.version,
            requested_zoom_ladder="", achieved_zoom=achieved,
            status=status, chip_path=chip_path,
            error=None if status in RECOVERED_STATUSES else "full-stack frame not recovered",
        ))
        if status in RECOVERED_STATUSES and chip_path is not None:
            sidecar_rows.append({
                "anchor_id": anchor_id,
                "capture_date": frame.capture_date,
                "version": "" if frame.version is None else frame.version,
                "chip_filename": Path(str(chip_path)).name,
                "owning_target_ids": ";".join(frame.owning_target_ids),
                "claimed_fpds": ";".join(frame.claimed_fpds),
                "is_modal_fpd_frame": "True" if frame.is_modal_fpd_frame else "False",
                "status": status,
                "actual_zoom": "" if frame.actual_zoom is None else frame.actual_zoom,
                "off_roof_marker": "",
            })
    for tok in no_window_targets:
        report_rows.append(_report_row(
            anchor_id, source="fullstack", role="fullstack_window",
            capture_date="", version=None, requested_zoom_ladder="", achieved_zoom=None,
            status="fullstack_no_window", chip_path=None,
            error=f"target {tok}: no modal FPD window (reps all UNDATED)",
        ))
    return report_rows, sidecar_rows


def _fullstack_sidecar_path(root: Path) -> Path:
    """Caption sidecar path — sibling of ``frame_report.csv`` under ``rerender/``."""
    return rerender_report_path(root).parent / "fullstack_frames.csv"


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
    claimed_latest_absent: str | None = None,
    claimed_earliest_present: str | None = None,
    timeout: float = 600.0,
    coj_chips_dir: Path | None = None,
    coj_years: Sequence[int] = DEFAULT_COJ_YEARS,
    runner: Callable[..., GehiRunResult] = run_gehi,
) -> list[dict[str, object]]:
    """Re-render the production scan + CoJ reference frames for one anchor.

    Missing scan_state or missing bbox are drop-and-report (anchor-level rows),
    not exceptions — per the design, disputes now resolve to c-anchors that own
    real scan_states + bbox, so these branches only guard genuinely broken input.

    Dispute anchors' full-stack recovered-window frames are handled separately by
    ``_render_fullstack_frames`` in ``rerender_from_assignments`` (a distinct
    ``fsarm_`` namespace + caption sidecar), NOT here — this keeps the production
    scan/CoJ contract unchanged.
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
    for frame in resolve_window_frames(
        state,
        flank=flank,
        claimed_latest_absent=claimed_latest_absent,
        claimed_earliest_present=claimed_earliest_present,
    ):
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
    fullstack_artifacts_csv: Path | None = None,
    fullstack_units_csv: Path | None = None,
    fullstack_flank: int = DEFAULT_FULLSTACK_FLANK,
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

    When ``fullstack_artifacts_csv`` is given, dispute anchors (``is_dispute_forced``)
    additionally get their full-stack recovered-window frames resolved from the frozen
    ``artifacts_fullstack.csv`` inventory (per-target modal FPD +-flank INDEX window,
    ``dispute_target_ids`` -> targets), copied into distinct ``fsarm_`` chips, and a
    ``fullstack_frames.csv`` caption sidecar written — this surfaces the present-side
    evidence the production scan gave up on. Omitting the flag is a byte-identical no-op
    (AC-3.8): no sidecar file, no ``fullstack`` rows. A dispute anchor absent from the
    inventory simply gets none (banner-only downstream).
    """
    chips_root = rerender_chips_dir(root)
    bbox_index = load_bbox_index(chipgroups_csv)

    fullstack_on = fullstack_artifacts_csv is not None
    artifacts_by_chip = load_fullstack_artifacts(fullstack_artifacts_csv) if fullstack_on else {}
    units_index = load_fullstack_units(fullstack_units_csv) if fullstack_units_csv else {}

    assignments = read_sample_assignments(assignments_csv)
    seen_anchors: set[str] = set()
    ordered_anchors: list[str] = []
    dispute_info: dict[str, list[str]] = {}  # anchor_id -> dispute_target_ids (forced only)
    assignment_by_anchor: dict[str, dict[str, str]] = {}
    for row in assignments:
        aid = (row.get("anchor_id") or "").strip()
        if aid and aid not in seen_anchors:
            seen_anchors.add(aid)
            ordered_anchors.append(aid)
            assignment_by_anchor[aid] = row
            if str(row.get("is_dispute_forced", "")).strip().lower() in ("true", "1", "yes"):
                dispute_info[aid] = decode_target_ids(row.get("dispute_target_ids", ""))

    report_rows: list[dict[str, object]] = []
    sidecar_rows: list[dict[str, object]] = []
    for anchor_id in ordered_anchors:
        state_path = state_path_for(anchor_id, scan_states_dir)
        state = load_scan_state(state_path) if state_path.exists() else None
        bbox_row = bbox_index.get(anchor_id)
        assignment = assignment_by_anchor[anchor_id]
        claimed_latest_absent = (assignment.get("latest_absent_date") or "").strip()
        claimed_earliest_present = (assignment.get("earliest_present_date") or "").strip()
        has_claimed_columns = bool(
            claimed_latest_absent
            or claimed_earliest_present
            or (assignment.get("pipeline_interval_start") or "").strip()
            or (assignment.get("pipeline_interval_end") or "").strip()
        )
        report_rows.extend(rerender_anchor(
            anchor_id, state, bbox_row,
            chips_root=chips_root, provider=provider, gehi_exe=gehi_exe,
            zoom_override=zoom_override, flank=flank, timeout=timeout,
            claimed_latest_absent=claimed_latest_absent if has_claimed_columns else None,
            claimed_earliest_present=claimed_earliest_present if has_claimed_columns else None,
            coj_chips_dir=coj_chips_dir, coj_years=coj_years, runner=runner,
        ))
        if fullstack_on and anchor_id in dispute_info and anchor_id in artifacts_by_chip:
            fs_frames, no_window = resolve_fullstack_frames(
                anchor_id, dispute_info[anchor_id], artifacts_by_chip[anchor_id],
                units_index, flank=fullstack_flank,
            )
            fs_report, fs_sidecar = _render_fullstack_frames(
                anchor_id, fs_frames, no_window, bbox_row,
                chips_root=chips_root, provider=provider, gehi_exe=gehi_exe,
                zoom_override=zoom_override, timeout=timeout, runner=runner,
            )
            report_rows.extend(fs_report)
            sidecar_rows.extend(fs_sidecar)

    write_csv_rows(rerender_report_path(root), report_rows, FRAME_REPORT_FIELDS)
    if fullstack_on:
        write_csv_rows(_fullstack_sidecar_path(root), sidecar_rows, FULLSTACK_FRAMES_SIDECAR_FIELDS)
    return report_rows


# ---------------------------------------------------------------------------
# CLI

def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--assignments", type=Path, required=True, help="sample_assignments.csv (WP-B output)")
    p.add_argument("--scan-states-dir", type=Path, required=True, help="dir of retained scan_states/<anchor_id>.json")
    p.add_argument("--chipgroups-csv", type=Path, required=True, help="chip_groups_as_anchors.csv (bbox source)")
    p.add_argument("--coj-chips-dir", type=Path, default=None, help="CoJ chips root with {2015,2019,2023}/<anchor>.tif")
    p.add_argument("--fullstack-artifacts-csv", type=Path, default=None,
                   help="fullstack_noscan_*/artifacts_fullstack.csv — frozen full-stack inventory "
                        "(dispute anchors' recovered ``fsarm_`` window frames). Omit -> byte-identical no-op.")
    p.add_argument("--fullstack-units-csv", type=Path, default=None,
                   help="fullstack_noscan_*/analysis/per_unit_fullstack.csv — per-target modal FPD source")
    p.add_argument("--fullstack-flank", type=int, default=DEFAULT_FULLSTACK_FLANK,
                   help="full-stack artifact slots (INDEX positions) to flank each modal-FPD frame")
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
        fullstack_artifacts_csv=args.fullstack_artifacts_csv,
        fullstack_units_csv=args.fullstack_units_csv,
        fullstack_flank=args.fullstack_flank,
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
