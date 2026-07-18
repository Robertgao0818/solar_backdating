#!/usr/bin/env python3
"""Adaptive PV-presence scan orchestrator (Phase-0 skeleton).

For each anchor in the input CSV, drives an adaptive round-by-round scan over
historical GEHI vintages, scoring each picked vintage with Gemini, and writes a
per-anchor `scan_state.json` checkpoint after every round. Task A ships only
the dry-run skeleton: GEHI/Gemini calls are stubbed by `--dry-run` so the loop
can be exercised without API cost. Tasks C/D wire in the real providers.

Quick start (dry-run, jhb_vexcel10_smoke):

    python scripts/temporal/run_adaptive_scan.py \
        --anchors-csv ~/zasolar_data/geid_temporal/jhb_vexcel10_smoke/anchors.csv \
        --scan-states-dir ~/zasolar_data/geid_temporal/jhb_vexcel10_smoke/scan_states \
        --dry-run --limit-anchors 3
"""

from __future__ import annotations

# Imports follow a sys.path bootstrap (below) so the subrepo can be run as a
# script; E402 is expected for the scripts.* imports, matching the sibling
# temporal modules' convention.
# ruff: noqa: E402

import argparse
import csv
import dataclasses
import hashlib
import json
import sys
import threading
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Iterable, Mapping, Sequence

if TYPE_CHECKING:  # pragma: no cover - typing only
    from scripts.temporal.verdict_store import VerdictStore

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.temporal.chip_geometry import resolve_chip_geometry
from scripts.temporal.gehi_catalog_cache import (
    DB_FILENAME,
    CatalogCache,
    DEFAULT_CATALOG_MAX_AGE_DAYS,
    add_catalog_cache_cli_args,
)
from scripts.temporal.presence_scorer import (
    PresenceScorer,
    get_scorer,
)
from scripts.temporal.presence_scorer import (
    Pick as ScorerPick,
)
from scripts.temporal.scan_config import AdaptiveScanConfig, load_config
from scripts.temporal.scan_decision import (
    Action,
    ExecuteRoundAction,
    TerminateAction,
    VintageEntry,
    decide_next_action,
)
from scripts.temporal.scan_state import (
    LEGACY_V1_GEOMETRY_VERSION,
    Pick,
    Round,
    RoundResult,
    ScanState,
    create_scan_state,
    load_scan_state,
    save_scan_state,
    state_path_for,
)

DEFAULT_ANCHORS_CSV = Path.home() / "zasolar_data/geid_temporal/jhb_vexcel10_smoke/anchors.csv"
DEFAULT_SCAN_STATES_DIR = Path.home() / "zasolar_data/geid_temporal/jhb_vexcel10_smoke/scan_states"
DEFAULT_CHIPS_DIR = Path.home() / "zasolar_data/geid_temporal/gehi_chips"
DEFAULT_AUDIT_DIR = Path.home() / "zasolar_data/geid_temporal/jhb_vexcel10_smoke/gemini_audit"
DEFAULT_CONFIG_YAML = PROJECT_ROOT / "configs" / "geid_anchor_presence.yaml"


def _default_gemini_env() -> Path:
    """Reuse the gemini reviewer's resolver: subrepo first, then ZASOLAR_ROOT."""
    from scripts.validation.gemini_solar_image_review import _resolve_default_env_file

    return _resolve_default_env_file()


def _routing_salt(mode: str, model: str, anchor_id: str, round_id: object) -> str | None:
    """Per-anchor routing nonce so concurrent native calls fan out across the
    gateway account pool instead of all hashing onto one account.

    Mirrors the FP-cut reviewer's ``--routing-salt-mode``:
    ``none`` disables it, ``auto`` salts pro models only (flash defaults
    unchanged), ``target`` salts every call. The nonce is deterministic per
    (model, anchor, round) so a retried call sticks to the same account while
    distinct anchors spread across the pool. The text is labelled as
    load-balancing-only so the model ignores it for the visual decision.
    """
    if mode == "none":
        return None
    if mode == "auto" and "pro" not in (model or "").lower():
        return None
    return f"{model}:{anchor_id}:r{round_id}"

DRY_RUN_PROFILE_LABELS = (
    "appears_2015",
    "appears_2018",
    "appears_2020",
    "appears_2023",
    "all_present",
    "all_absent",
)


@dataclass(frozen=True)
class DryRunProfile:
    label: str
    install_date: date | None  # None when never present (case C) or always present (case B)


@dataclass
class VintageCatalog:
    vintages: list[VintageEntry]
    available_dates_by_zoom: dict[int, set[str]]
    catalog_max_date: str | None = None
    available_dates_by_provider_zoom: dict[str, dict[int, set[str]]] = field(
        default_factory=dict
    )


def merge_provider_catalogs(
    tm: VintageCatalog, wayback: VintageCatalog
) -> VintageCatalog:
    """Merge production imagery sources into one chronological adaptive catalog.

    A capture date is scored once. GE Time Machine is the primary production
    source and wins exact-date collisions; Wayback contributes dates absent from
    TM. Provider-specific availability maps remain attached for download checks.
    """
    by_date = {v.capture_date: dataclasses.replace(v, provider="Wayback") for v in wayback.vintages}
    by_date.update(
        {v.capture_date: dataclasses.replace(v, provider="TM") for v in tm.vintages}
    )
    max_dates = [d for d in (tm.catalog_max_date, wayback.catalog_max_date) if d]
    return VintageCatalog(
        vintages=[by_date[d] for d in sorted(by_date)],
        available_dates_by_zoom={},
        catalog_max_date=max(max_dates) if max_dates else None,
        available_dates_by_provider_zoom={
            "TM": tm.available_dates_by_zoom,
            "Wayback": wayback.available_dates_by_zoom,
        },
    )


def _resolve_catalog_max_date(
    capture_dates: Iterable[str],
    config: AdaptiveScanConfig,
    *,
    census_date: str | None,
) -> str:
    """Return the per-anchor hard catalog bound.

    The configured ``catalog_max_date`` remains an absolute safety cap. This is
    the first metadata-derived candidate; the real coverage-gated catalog may
    advance through later candidates until N bbox-complete reference dates are
    available.
    """
    return _catalog_cutoff_candidates(
        capture_dates, config, census_date=census_date
    )[0]


def _catalog_cutoff_candidates(
    capture_dates: Iterable[str],
    config: AdaptiveScanConfig,
    *,
    census_date: str | None,
) -> list[str]:
    """Candidate bounds from the earliest plausible cutoff to the hard cap.

    Coverage-gated catalogs try these in order until N bbox-complete
    post-census reference dates are present. Non-gated catalogs use the first
    candidate directly.
    """
    hard_max = config.catalog_max_date[:10]
    if census_date is None:
        return [hard_max]
    census = census_date[:10]
    if census >= hard_max:
        return [hard_max]
    newer = sorted(
        {
            str(value)[:10]
            for value in capture_dates
            if str(value)[:10] > census and str(value)[:10] <= hard_max
        }
    )
    keep = max(0, int(config.post_census_reference_frames))
    if keep == 0 or not newer:
        return [census]
    return newer[min(keep, len(newer)) - 1 :]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--anchors-csv", type=Path, default=DEFAULT_ANCHORS_CSV)
    parser.add_argument("--scan-states-dir", type=Path, default=DEFAULT_SCAN_STATES_DIR)
    parser.add_argument("--chips-dir", type=Path, default=DEFAULT_CHIPS_DIR, help="Where GEHI chips are written")
    parser.add_argument("--audit-dir", type=Path, default=DEFAULT_AUDIT_DIR, help="Per-anchor per-round Gemini audit JSONL")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_YAML)
    parser.add_argument(
        "--gemini-env-file",
        type=Path,
        default=None,
        help="Override .env.gemini.local lookup. Default: subrepo .env.gemini.local, falling back to $ZASOLAR_ROOT/.env.gemini.local.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Skip GEHI/Gemini calls; mock vintage list and score results from anchor_id hash "
        "(routes through the registered 'dry_run' scorer).",
    )
    parser.add_argument(
        "--scorer",
        type=str,
        default="gemini",
        help="Registered PresenceScorer name used for the real (non-dry-run) scan path. "
        "Default 'gemini'; 'dinov3_frozen' selects the self-hosted frozen DINOv3-L-SAT "
        "backbone (ISSUE-03). Ignored when --dry-run is set (that forces the per-anchor "
        "'dry_run' stub scorer). See presence_scorer.available_scorers().",
    )
    parser.add_argument("--limit-anchors", type=int, help="Process only the first N anchors")
    parser.add_argument(
        "--anchor-workers",
        type=int,
        default=1,
        help="Number of anchors to scan concurrently. Anchors are independent "
        "(separate scan_state/chips/audit paths keyed by anchor_id), so this "
        "overlaps one anchor's GEHI downloads with another's Gemini scoring — the "
        "adaptive per-anchor loop stays sequential. Each anchor still consumes both "
        "the Gemini backend (<=N concurrent batch calls) and GEHI (downloads + "
        "catalog fetches); keep <= min(Gemini slots, GEHI tolerance). GEHI is "
        "usually the binding constraint, so ramp from 3-4. Default 1 = serial.",
    )
    parser.add_argument(
        "--force-restart",
        action="store_true",
        help="Delete and recreate every scan_state. Default behavior is resume from existing state.",
    )
    parser.add_argument(
        "--overwrite-chips",
        action="store_true",
        help="Re-download every chip, bypassing the skip-existing cache (ISSUE-18 escape hatch). "
        "Default: off (reuse cached chips).",
    )
    parser.add_argument(
        "--min-cache-zoom",
        type=int,
        default=None,
        help="Refuse cached chips below this zoom so the ladder re-fetches and upgrades them "
        "(ISSUE-18 escape hatch; governs cache acceptance only). Default: off.",
    )
    parser.add_argument(
        "--verdict-store",
        type=Path,
        default=None,
        help="Path of the content-addressed verdict store JSONL (ISSUE-07). "
        "Default: <scan-states-dir parent>/verdict_store.jsonl. Every scoring "
        "call consults it first; identical re-runs replay verdicts and issue "
        "zero scorer calls for already-seen chips. Single-writer per store file.",
    )
    parser.add_argument(
        "--no-verdict-store",
        action="store_true",
        help="Disable the ISSUE-07 verdict store entirely (every chip is re-scored).",
    )
    parser.add_argument(
        "--qps",
        type=float,
        default=0.0,
        help="Global Gemini requests/sec across ALL anchor workers (shared "
        "RateLimiter). 0 = no throttle, worker count alone caps concurrency. "
        "Match to the gateway account pool; the FP-cut full run used qps 8 at 30 workers.",
    )
    parser.add_argument(
        "--round1-model",
        type=str,
        default="gemini-3-flash",
        help="Cheap-tier model for routine present/absent rounds (see --cheap-round-types). "
        "Default gemini-3-flash. Empty string = reuse round2 model (single tier).",
    )
    parser.add_argument(
        "--round2-model",
        type=str,
        default=None,
        help="Capable-tier model for the round_types NOT in --cheap-round-types "
        "(by default just anchor_recovery). "
        "Default: GEMINI_MODEL from the gemini env file (gemini-3-flash-agent).",
    )
    parser.add_argument(
        "--cheap-round-types",
        type=str,
        default="initial,bisection,walk_back,tail",
        help="Comma-separated round_types routed to --round1-model (cheap tier). "
        "Everything NOT listed uses --round2-model (capable tier). bisection/walk_back/tail "
        "ask the SAME present/absent question as the initial round, so the cheap tier handles "
        "them (~86%% of round>=2 calls); only anchor_recovery (marginal cases) escalates. "
        "Pass 'initial' to restore the old round_id==1-only escalation.",
    )
    parser.add_argument(
        "--routing-salt-mode",
        choices=("auto", "none", "target"),
        default="auto",
        help="Append a per-anchor routing nonce to native calls so concurrency fans "
        "out across the gateway account pool. 'target' salts every call; 'auto' salts "
        "pro models only; 'none' disables. Use 'target' for high-concurrency flash runs.",
    )
    parser.add_argument(
        "--census-mid-date-override",
        type=str,
        default=None,
        help="Override the per-anchor census date. ISO YYYY-MM-DD. "
        "Used as the GT-prior anchor for Gemini calibration prompts and the ISSUE-26 cutoff base. "
        "Wins over --vexcel-capture-csv and the regions.yaml lookup.",
    )
    parser.add_argument(
        "--vexcel-capture-csv",
        type=Path,
        default=None,
        help="Per-grid Vexcel capture-date CSV (columns grid_id,...,last_capture_date,...). When "
        "given, each anchor's census date resolves to its grid's real flight date (max over "
        "source_grids for multi-grid chip groups) instead of the region-wide "
        "census_imagery_mid_date, so the ISSUE-26 per-anchor catalog cutoff brackets the true "
        "capture. Grids missing from the CSV fall back to the regions.yaml lookup. "
        "JHB table: data/analysis/vexcel_jhb_per_grid_capture_dates_2026-06-04.csv (ZAsolar repo).",
    )
    parser.add_argument(
        "--provider",
        choices=("TM", "Wayback", "Merged"),
        default="TM",
        help="GEHI imagery provider. TM=Google Earth Time Machine (default); "
        "Wayback=ESRI World Imagery; Merged unions both catalogs into one "
        "adaptive sequence with TM precedence on exact-date collisions. Wayback's "
        "catalog/download key is the real "
        "captured date. Selecting Wayback forces require_complete_coverage_* "
        "off because Wayback's availability lists layer-release dates, not "
        "captured dates, so the completeness intersection would be empty.",
    )
    parser.add_argument(
        "--merged-tm-chips-dir",
        type=Path,
        default=None,
        help="TM cache root used when --provider Merged.",
    )
    parser.add_argument(
        "--merged-wayback-chips-dir",
        type=Path,
        default=None,
        help="Wayback cache root used when --provider Merged.",
    )
    parser.add_argument(
        "--review-extent-m",
        type=float,
        default=None,
        help="Render a target-centred, marked review crop with this fixed metre "
        "extent from the downloaded source raster. Default: score the legacy "
        "full-chip review PNG. Used by ISSUE-25 A24/A48/A96.",
    )
    parser.add_argument(
        "--tm-catalog-interval",
        type=float,
        default=0.0,
        help="Minimum seconds between live TM (Google) catalog metadata calls "
        "(availability/info), shared across all anchor workers, with 403/429 "
        "backoff-and-retry. Wayback catalog calls and chip downloads are "
        "unaffected. Default 0 = unthrottled (legacy behavior). Full-population "
        "runs MUST set this (~1.0 = the validated ~60 calls/min-per-address-"
        "family safe pace; the 2026-07-12 TM 403 ban fired at ~104/min "
        "sustained ~1h). Live calls only happen on catalog-cache misses, so "
        "this is a no-op for cache-warm reruns.",
    )
    parser.add_argument(
        "--offline-tm-catalog-csv",
        type=Path,
        default=None,
        help="Per-anchor TM capture-date CSV (build_gehi_candidates.py output; "
        "anchor_id/capture_date/provider columns, TM rows used). When given, "
        "the TM vintage catalog is built offline from these dates with zero "
        "live TM metadata calls: bbox-completeness is delegated to the "
        "download layer (the basemap rebuild already pulled these dates "
        "through the zoom ladder), and offline picks carry version="
        "'noversion' so they resolve the pre-downloaded _vnoversion chips. "
        "Anchors absent from the CSV fall back to the live TM catalog path. "
        "Wayback catalog calls are unaffected.",
    )
    parser.add_argument(
        "--offline-wayback",
        action="store_true",
        help="Build the Wayback vintage catalog offline from the same "
        "--offline-tm-catalog-csv (its Wayback rows: anchor_id/capture_date/"
        "version/provider columns), with zero live Wayback info calls (z19/z18) "
        "and no z20 lazy-query. Requires --offline-tm-catalog-csv (SystemExit if "
        "given alone). Wayback rows carry a real numeric version (the layer "
        "capture id), unlike offline TM's 'noversion' sentinel, so picks resolve "
        "the real pre-downloaded _v<version>.tif chips. z20 is marked "
        "unavailable rather than lazily fetched live (production Wayback z20 "
        "coverage is ~97% absent) -- this is a deliberate behavior difference "
        "from the live path, which does lazy-fetch z20 and occasionally hits. "
        "Anchors absent from the CSV's Wayback rows fall back to the live "
        "Wayback catalog path.",
    )
    parser.add_argument(
        "--no-live-gehi",
        action="store_true",
        help="Zero-live-GEHI mode: guarantee this process NEVER spawns a GEHI "
        "subprocess, so the only network traffic is the Gemini gateway. Added "
        "after the 2026-07-16 a24_v6 khmdb-ban stall, whose root cause was a "
        "live GEHI download fired by a chip missing from the pre-downloaded "
        "basemap despite both offline catalog flags being set. Requires both "
        "--offline-tm-catalog-csv and --offline-wayback (SystemExit if either "
        "is missing -- simplest way to guarantee every catalog-build path is "
        "offline). Under this flag: (1) a pick whose chip is absent from the "
        "pre-downloaded basemap on disk is recorded status=all_zooms_failed "
        "via the existing download_failed notes path, never live-downloaded; "
        "(2) an anchor absent from the offline TM/Wayback catalog CSV surfaces "
        "as a done_ambiguous_orchestrator_error scan_state (grep-able, "
        "re-runnable via --force-restart once the anchor is added to the CSV) "
        "instead of falling back to a live catalog fetch; (3) any other code "
        "path that would still reach a live GEHI call raises loudly instead "
        "of silently degrading.",
    )
    parser.add_argument(
        "--offline-require-chip-on-disk",
        action="store_true",
        help="Filter BOTH offline catalogs (TM and Wayback) down to entries whose "
        "chip file actually exists under the merged chips dirs (any ladder zoom "
        "counts, same rule the download cache-scan uses) BEFORE the adaptive "
        "picker or the ISSUE-26 cutoff walk ever see them. Closes the 2026-07-17 "
        "forensics finding: a handful of 'phantom' dates are listed in the "
        "offline CSV (the original live catalog call once reported them) but "
        "were never actually captured to disk during the basemap download, so "
        "even --no-live-gehi alone still burns a wasted download_failed pick "
        "every time the adaptive scan selects one. Requires --no-live-gehi plus "
        "both --offline-tm-catalog-csv and --offline-wayback (SystemExit "
        "otherwise -- this is a stricter sub-mode of the same offline-only "
        "guarantee). Logs a startup summary: per-provider dropped-entry counts, "
        "top dropped dates, and any anchor that loses >50%% of its candidates "
        "to the filter (a signal for a targeted TM/Wayback backfill before "
        "rerunning, not something this flag fixes by itself).",
    )
    add_catalog_cache_cli_args(parser)
    return parser.parse_args()


class GeometryVersionMismatchError(ValueError):
    """A non-terminal scan state would mix evidence from two geometries."""


def _anchor_geometry_version(anchor: Mapping[str, str]) -> str:
    return str(anchor.get("geometry_version") or LEGACY_V1_GEOMETRY_VERSION)


def validate_fixed_extent_anchor_geometry(
    anchor: Mapping[str, str], extent_m: float
):
    """Resolve and cross-check one v2 fixed-extent anchor geometry."""
    try:
        geometry_version = str(anchor["geometry_version"])
    except (KeyError, TypeError) as exc:
        raise ValueError(
            "fixed-extent rendering requires geometry_version for "
            f"{anchor.get('anchor_id', '<unknown>')}"
        ) from exc
    if not geometry_version:
        raise ValueError(
            "fixed-extent rendering requires non-empty geometry_version for "
            f"{anchor.get('anchor_id', '<unknown>')}"
        )
    geometry = resolve_chip_geometry(geometry_version)
    if abs(float(extent_m) - geometry.min_crop_size_m) > 1e-9:
        raise ValueError(
            f"review extent {extent_m:g}m does not match geometry_version "
            f"{geometry_version!r} ({geometry.min_crop_size_m:g}m) for "
            f"{anchor.get('anchor_id', '<unknown>')}"
        )
    return geometry


def make_fixed_extent_review_renderer(
    extent_m: float,
) -> Callable[[Path, Mapping[str, str]], Path]:
    """Build the ISSUE-27 registry-backed renderer for one frozen routed arm."""
    if extent_m <= 0:
        raise ValueError("review extent must be positive")

    def render(image_path: Path, anchor: Mapping[str, str]) -> Path:
        from scripts.temporal.gehi_common import (
            ReviewTargetMarker,
            ensure_single_target_review_png,
        )

        geometry = validate_fixed_extent_anchor_geometry(anchor, extent_m)
        try:
            source_extent_m = float(anchor["chip_size_m"])
            offset_x_m = float(anchor["target_offset_x_m"])
            offset_y_m = float(anchor["target_offset_y_m"])
            bbox_width_m = float(anchor["source_width_m"])
            bbox_height_m = float(anchor["source_height_m"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(
                "fixed-extent rendering requires chip_size_m, "
                "target_offset_x_m, target_offset_y_m, source_width_m, and "
                f"source_height_m for {anchor.get('anchor_id', '<unknown>')}"
            ) from exc
        if geometry.min_crop_size_m > source_extent_m:
            raise ValueError(
                f"review extent {geometry.min_crop_size_m:g}m exceeds source extent "
                f"{source_extent_m:g}m for {anchor.get('anchor_id', '<unknown>')}"
            )
        marker = ReviewTargetMarker(
            target_id=str(anchor.get("anchor_id", "target")),
            target_label=str(anchor.get("target_label") or "T01"),
            offset_x_m=offset_x_m,
            offset_y_m=offset_y_m,
            search_radius_m=float(anchor.get("search_radius_m") or 10.0),
        )
        if bbox_width_m <= 0 or bbox_height_m <= 0:
            raise ValueError(
                f"invalid teacher bbox {bbox_width_m:g}x{bbox_height_m:g}m for "
                f"{anchor.get('anchor_id', '<unknown>')}"
            )
        return ensure_single_target_review_png(
            image_path,
            marker,
            chip_size_m=source_extent_m,
            crop_context_multiplier=geometry.crop_context_multiplier,
            min_crop_size_m=geometry.min_crop_size_m,
            min_output_px=geometry.min_output_px,
            draw_marker=True,
            bbox_width_m=bbox_width_m,
            bbox_height_m=bbox_height_m,
        )

    return render


def read_anchors(path: Path, limit: int | None) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        rows = [dict(r) for r in reader]
    if limit is not None:
        rows = rows[:limit]
    return rows


def anchor_hash(anchor_id: str) -> int:
    digest = hashlib.sha256(anchor_id.encode("utf-8")).hexdigest()
    return int(digest[:8], 16)


def dry_run_profile_for(anchor_id: str) -> DryRunProfile:
    label = DRY_RUN_PROFILE_LABELS[anchor_hash(anchor_id) % len(DRY_RUN_PROFILE_LABELS)]
    if label.startswith("appears_"):
        year = int(label.split("_")[1])
        return DryRunProfile(label=label, install_date=date(year, 6, 15))
    return DryRunProfile(label=label, install_date=None)


def dry_run_vintages(anchor_id: str) -> list[VintageEntry]:
    """Synthetic z=19 vintage list spanning 2009-04 .. 2025-03 with some gaps."""
    seed = anchor_hash(anchor_id)
    out: list[VintageEntry] = []
    cursor = date(2009, 4, 1)
    end = date(2025, 3, 31)
    version = 100 + (seed % 50)
    while cursor <= end:
        if (cursor.month + seed) % 7 != 0:
            out.append(VintageEntry(capture_date=cursor.isoformat(), version=version))
            version += 1
        step_months = 4 + (cursor.month + seed) % 5
        new_year = cursor.year + (cursor.month - 1 + step_months) // 12
        new_month = (cursor.month - 1 + step_months) % 12 + 1
        cursor = date(new_year, new_month, 1)
    return out


def _scorer_failure_source(scorer: PresenceScorer) -> str:
    """The single decision_source the orchestrator stamps on synthesized failures.

    Download failures and missing-observation rows are orchestrator-level events,
    not scorer verdicts, but they must still count toward the >50%-failed
    ambiguity rule (Case E). They therefore carry a decision_source drawn from the
    active scorer's *declared* failure set. The Gemini scorer declares
    ``{"gemini_failed"}`` so the persisted string — and every existing
    scan_state.json / regression fixture — stays byte-identical. A non-Gemini
    scorer's own failure sentinel flows through the same path. Falls back to the
    historical literal when a scorer declares no failure sources (e.g. dry-run,
    which never reaches this path).
    """
    sources = sorted(scorer.failure_decision_sources)
    return sources[0] if sources else "gemini_failed"


def execute_round_dry_run(
    rnd: Round,
    scorer: PresenceScorer,
) -> Round:
    """Score a round via the injected dry-run scorer (no GEHI/Gemini calls).

    Builds a post-download seam ``Pick`` per scan_state ``Pick`` (dry-run has no
    real chip, so ``chip_path=""`` and ``actual_zoom=pick.requested_zoom``), scores
    them through the seam, and maps each ``PresenceObservation`` back onto the
    persisted ``RoundResult`` shape byte-identically to the old
    ``dry_run_gemini_result``.
    """
    scorer_picks = [
        ScorerPick(
            chip_path="",
            capture_date=pick.capture_date,
            version=pick.version,
            actual_zoom=pick.requested_zoom,
            index=pick.chip_index,
        )
        for pick in rnd.picks
    ]
    observations = scorer.score(scorer_picks, config=None)
    obs_by_index = {obs.index: obs for obs in observations}
    results: list[RoundResult] = []
    for pick in rnd.picks:
        obs = obs_by_index[pick.chip_index]
        results.append(
            RoundResult(
                chip_index=pick.chip_index,
                capture_date=pick.capture_date,
                version=pick.version,
                pv_present=obs.pv_present,
                confidence=obs.pv_score,
                quality_flag=obs.quality_flag,
                decision_source=obs.decision_source,
                evidence=obs.evidence,
                notes=obs.notes,
                chip_path="",
                actual_zoom=pick.requested_zoom,
            )
        )
    rnd.results = results
    rnd.completed = True
    rnd.failed = False
    return rnd


def make_vintage_check(
    anchor: dict[str, str],
    *,
    available_dates_by_zoom: dict[int, set[str]],
    config: AdaptiveScanConfig,
    catalog_max_date: str | None = None,
    catalog_cache: CatalogCache | None = None,
    catalog_force_refresh: bool = False,
    catalog_max_age_days: float = DEFAULT_CATALOG_MAX_AGE_DAYS,
    tm_catalog_runner: Callable[..., object] | None = None,
    no_live_gehi: bool = False,
) -> Callable[[int, str], bool]:
    """Build a vintage_check Callable for `download_chip_with_zoom_ladder`.

    Discovery reuses bbox-level availability catalogs already fetched by
    `_fetch_real_vintage_catalog`. Other ladder zooms lazy-fetch their own
    bbox availability on first lookup. Returns False when the requested
    capture_date is not complete for the requested zoom/chip bbox.

    `catalog_cache=None` (the default) makes zero behavior change: every
    lazy zoom lookup issues a live GEHI call exactly as before ISSUE-13.

    `no_live_gehi=True` closes the one remaining lazy-fetch surface this
    function owns: normally, a zoom absent from the pre-built
    `available_dates_by_zoom` (e.g. a ladder rung the offline catalog builder
    didn't cover) triggers a live `fetch_availability_for_anchor` /
    `fetch_vintages_for_anchor` call on first lookup. With the flag set, that
    branch raises loudly instead of ever reaching those live-call surfaces --
    both `fetch_vintages_for_anchor`/`fetch_availability_for_anchor` default to
    the raw (unthrottled) `run_gehi` and swallow arbitrary exceptions raised by
    an injected `runner` in their no-cache path, so gating here (before the
    call) rather than via a raising `runner=` is the only reliable way to make
    this loud rather than a silent `[]` degrade. `download_chip_with_zoom_ladder`
    already treats any `vintage_check` exception as "skip this zoom" (logged),
    so the anchor still resolves to its normal `all_zooms_failed` / notes path
    -- no new state shape.
    """
    effective_max_date = (catalog_max_date or config.catalog_max_date)[:10]
    catalogs: dict[int, set[str]] = {
        int(zoom): {date for date in dates if date[:10] <= effective_max_date}
        for zoom, dates in available_dates_by_zoom.items()
    }

    # TM (Google khmdb) metadata calls are the 403-ban surface; Wayback is not.
    runner_kwargs: dict[str, object] = (
        {"runner": tm_catalog_runner}
        if tm_catalog_runner is not None and config.provider == "TM"
        else {}
    )

    def check(zoom: int, capture_date: str) -> bool:
        if zoom not in catalogs:
            if no_live_gehi:
                raise RuntimeError(
                    f"no_live_gehi: refusing live {config.provider} availability/info "
                    f"fetch for anchor {anchor.get('anchor_id', '<unknown>')} at z={zoom} "
                    "(zoom absent from the pre-built offline catalog)"
                )
            if config.require_complete_coverage_for_download:
                from scripts.temporal.gehi_availability import fetch_availability_for_anchor

                rows = fetch_availability_for_anchor(
                    anchor,
                    zoom=zoom,
                    provider=config.provider,
                    min_date=config.catalog_min_date,
                    max_date=effective_max_date,
                    parallel=config.availability_parallel,
                    complete=True,
                    catalog_cache=catalog_cache,
                    force_refresh=catalog_force_refresh,
                    max_age_days=catalog_max_age_days,
                    **runner_kwargs,
                )
            else:
                from scripts.temporal.gehi_info import fetch_vintages_for_anchor

                rows = fetch_vintages_for_anchor(
                    anchor,
                    zoom=zoom,
                    provider=config.provider,
                    catalog_cache=catalog_cache,
                    force_refresh=catalog_force_refresh,
                    max_age_days=catalog_max_age_days,
                    **runner_kwargs,
                )
            catalogs[zoom] = {
                str(r.get("capture_date", ""))[:10]
                for r in rows
                if r.get("capture_date")
                and str(r.get("capture_date", ""))[:10] <= effective_max_date
            }
        return capture_date[:10] in catalogs[zoom]

    return check


def _build_batch_picks_with_remap(
    download_outcomes,
    review_asset_resolver,
):
    """Renumber successful downloads to dense batch indices 1..N for Gemini.

    The Gemini batch prompt asks the model to emit `chip_index` values in
    `1..N` matching the input order, where N is the number of images in the
    batch — not the original Pick.chip_index. If we passed the sparse
    original indices (e.g. when one pick failed to download), Gemini's
    `chip_index=2` in a 4-image batch would not refer to the original
    Pick(chip_index=2). Renumber here, then map observations back to
    original chip_index in `execute_round_real`.

    `review_asset_resolver(tif_path) -> review_path` converts each successful
    download path to the format Gemini accepts (PNG/JPEG); typically
    `gehi_common.ensure_review_png`.

    Returns `(batch_picks, batch_to_original)` where `batch_to_original`
    maps the dense batch index back to the original `pick.chip_index`.
    """
    from scripts.validation.gemini_solar_image_review import BatchPick

    batch_picks: list[BatchPick] = []
    batch_to_original: dict[int, int] = {}
    for pick, outcome in download_outcomes:
        if outcome.status not in ("ok", "skipped_existing") or outcome.path is None:
            continue
        review_path = review_asset_resolver(outcome.path)
        batch_idx = len(batch_picks) + 1
        batch_picks.append(
            BatchPick(
                chip_index=batch_idx,
                chip_path=review_path,
                capture_date=pick.capture_date,
                version=str(pick.version),
                actual_zoom=outcome.actual_zoom,
            )
        )
        batch_to_original[batch_idx] = pick.chip_index
    return batch_picks, batch_to_original


def _score_batch_picks_chunked(
    score_picks,
    batch_to_original: dict[int, int],
    *,
    config: AdaptiveScanConfig,
    gemini_config,
    audit_writer,
    census_mid_date_iso: str | None,
    scorer: PresenceScorer | None = None,
    limiter=None,
    routing_salt: str | None = None,
):
    """Score date picks in bounded scorer calls and return original-index observations.

    The concrete scorer arrives via the injected ``scorer`` (its ``.batch`` raw
    callable has the exact ``score_batch_with_fallback`` signature, so native
    ``GeminiObservation`` results — and downstream CSV/scan_state bytes — are
    unchanged). Defaults to the registered ``gemini`` scorer when not supplied so
    legacy direct callers keep working.

    ``limiter`` pacing (2026-07-17 gemini-retry-patch addendum): the Gemini
    scorer's ``score_batch_with_fallback`` now re-acquires ``limiter`` before
    EVERY HTTP attempt, including retries (see
    ``_post_with_transport_retry`` in gemini_solar_image_review.py) — retry
    bursts under transport 429/5xx now respect the shared qps gate too, not
    just first attempts. Because of that, the OLD coarse per-chunk
    ``limiter.wait()`` that used to run right before calling the scorer is
    dropped for the Gemini scorer specifically: keeping both would pace the
    first attempt of every chunk TWICE (the outer wait() plus the transport
    layer's own first-attempt wait()), each call advancing the shared pacer's
    "next allowed" cursor, which roughly halves effective qps. Non-Gemini
    scorers (e.g. ``dinov3_frozen``: local-inference batch, no HTTP retry
    ladder, accepts and ignores a ``limiter=`` kwarg) keep the original coarse
    per-chunk wait() — they never re-acquire it themselves, so dropping it
    there would silently un-throttle them.
    """
    from scripts.validation.gemini_solar_image_review import BatchPick

    if scorer is None:
        scorer = get_scorer("gemini")
    if not score_picks:
        return {}
    max_dates = max(1, int(config.gemini_max_dates_per_call))
    obs_by_original: dict[int, object] = {}
    total_chunks = (len(score_picks) + max_dates - 1) // max_dates

    for chunk_idx, start in enumerate(range(0, len(score_picks), max_dates), start=1):
        chunk = score_picks[start : start + max_dates]
        local_to_original: dict[int, int] = {}
        local_picks = []
        for local_idx, pick in enumerate(chunk, start=1):
            local_to_original[local_idx] = batch_to_original[pick.chip_index]
            local_picks.append(
                BatchPick(
                    chip_index=local_idx,
                    chip_path=pick.chip_path,
                    capture_date=pick.capture_date,
                    version=pick.version,
                    actual_zoom=pick.actual_zoom,
                )
            )

        def _chunk_audit(payload: dict) -> None:
            payload = dict(payload)
            payload["batch_chunk_index"] = chunk_idx
            payload["batch_chunk_count"] = total_chunks
            audit_writer(payload)

        if limiter is not None and scorer.name != "gemini":
            limiter.wait()
        salt_kwargs = {} if routing_salt is None else {"routing_salt": routing_salt}
        limiter_kwargs = {} if limiter is None else {"limiter": limiter}
        observations = scorer.batch(
            local_picks,
            config=gemini_config,
            audit_writer=_chunk_audit,
            census_mid_date_iso=census_mid_date_iso,
            **salt_kwargs,
            **limiter_kwargs,
        )
        for obs in observations:
            original = local_to_original.get(obs.chip_index)
            if original is not None:
                obs_by_original[original] = obs
    return obs_by_original


def execute_round_real(
    rnd: Round,
    anchor: dict[str, str],
    config: AdaptiveScanConfig,
    *,
    chips_dir: Path,
    audit_dir: Path,
    gemini_config,  # GeminiClientConfig - imported lazily
    scorer: PresenceScorer | None = None,
    vintage_check=None,
    census_mid_date_iso: str | None = None,
    limiter=None,
    routing_salt_mode: str = "none",
    overwrite_chips: bool = False,
    min_cache_zoom: int | None = None,
    provenance_writer: Callable[[Mapping[str, object]], None] | None = None,
    review_renderer: Callable[[Path, Mapping[str, str]], Path] | None = None,
    provider_chips_dirs: Mapping[str, Path] | None = None,
    no_live_gehi: bool = False,
) -> Round:
    """Download chips for each pick (zoom ladder), batch-score via the injected
    scorer, return Round with results.

    ``scorer`` is the injected :class:`PresenceScorer` (defaults to the registered
    ``gemini`` scorer for legacy direct callers). Its declared
    ``failure_decision_sources`` supplies the decision_source stamped on
    orchestrator-level failure rows (download failures / missing observations), so
    the >50%-failed ambiguity rule stays scorer-parameterized while Gemini keeps
    emitting the byte-identical ``"gemini_failed"`` sentinel.

    ``overwrite_chips`` / ``min_cache_zoom`` are the ISSUE-18 cache-refresh escape
    hatch, forwarded to every download call only when set (a default run passes
    neither, so the download call is byte-identical to before). ``provenance_writer``,
    when supplied, receives one canonical chip-provenance record per download
    outcome (built via ``build_chip_provenance``); it must be safe to call
    concurrently across anchor workers.

    ``no_live_gehi=True`` (zero-live-GEHI mode) is forwarded to every download
    call: a pick whose chip is missing from the pre-downloaded basemap on disk
    is recorded ``status="all_zooms_failed"`` without ever spawning a GEHI
    subprocess, folding into the existing ``download_failed: ...`` notes path
    below unchanged.
    """
    import json as _json

    from scripts.temporal.gehi_common import ensure_review_png
    from scripts.temporal.gehi_download import build_chip_provenance, download_chip_with_zoom_ladder

    if scorer is None:
        scorer = get_scorer("gemini")
    failure_source = _scorer_failure_source(scorer)

    escape_kwargs: dict[str, object] = {}
    if overwrite_chips:
        escape_kwargs["overwrite"] = True
    if min_cache_zoom is not None:
        escape_kwargs["min_cache_zoom"] = min_cache_zoom
    if no_live_gehi:
        escape_kwargs["no_live_gehi"] = True

    download_outcomes: list[tuple[Pick, object]] = []
    for pick in rnd.picks:
        pick_provider = pick.provider or config.provider
        output_root = (
            provider_chips_dirs.get(pick_provider, chips_dir)
            if provider_chips_dirs is not None
            else chips_dir
        )
        pick_vintage_check = (
            vintage_check.get(pick_provider)
            if isinstance(vintage_check, Mapping)
            else vintage_check
        )
        outcome = download_chip_with_zoom_ladder(
            anchor,
            capture_date=pick.capture_date,
            version=pick.version,
            zoom_ladder=config.download_zoom_ladder,
            output_root=output_root,
            provider=pick_provider,
            vintage_check=pick_vintage_check,
            **escape_kwargs,
        )
        download_outcomes.append((pick, outcome))
        if provenance_writer is not None:
            provenance_writer(build_chip_provenance(outcome, anchor, pick_provider))

    download_by_index: dict[int, object] = {pick.chip_index: outcome for pick, outcome in download_outcomes}
    if review_renderer is None:
        review_asset_resolver = ensure_review_png
    else:
        def review_asset_resolver(path: Path) -> Path:
            return review_renderer(path, anchor)
    score_picks, batch_to_original = _build_batch_picks_with_remap(
        download_outcomes, review_asset_resolver
    )

    routing_salt = None
    if routing_salt_mode != "none":
        routing_salt = _routing_salt(
            routing_salt_mode,
            getattr(gemini_config, "model", ""),
            anchor["anchor_id"],
            rnd.round_id,
        )

    audit_path = audit_dir / anchor["anchor_id"] / f"round_{rnd.round_id}.jsonl"
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    obs_by_original: dict[int, object] = {}
    if score_picks:
        with audit_path.open("w", encoding="utf-8") as audit_fh:
            def _audit(payload: dict) -> None:
                audit_fh.write(_json.dumps(payload, ensure_ascii=False) + "\n")

            obs_by_original = _score_batch_picks_chunked(
                score_picks,
                batch_to_original,
                config=config,
                gemini_config=gemini_config,
                audit_writer=_audit,
                census_mid_date_iso=census_mid_date_iso,
                scorer=scorer,
                limiter=limiter,
                routing_salt=routing_salt,
            )

    rnd_results: list[RoundResult] = []
    for pick in rnd.picks:
        outcome = download_by_index[pick.chip_index]
        if outcome.status not in ("ok", "skipped_existing") or outcome.path is None:
            rnd_results.append(
                RoundResult(
                    chip_index=pick.chip_index,
                    capture_date=pick.capture_date,
                    version=pick.version,
                    pv_present=None,
                    confidence=None,
                    quality_flag="unusable",
                    decision_source=failure_source,
                    evidence="",
                    notes=f"download_failed: status={outcome.status} error={outcome.error or ''}"[:300],
                    chip_path="",
                    actual_zoom=outcome.actual_zoom,
                    provider=pick.provider,
                )
            )
            continue
        obs = obs_by_original.get(pick.chip_index)
        if obs is None:
            rnd_results.append(
                RoundResult(
                    chip_index=pick.chip_index,
                    capture_date=pick.capture_date,
                    version=pick.version,
                    pv_present=None,
                    confidence=None,
                    quality_flag="unusable",
                    decision_source=failure_source,
                    evidence="",
                    notes="missing observation in batch results",
                    chip_path=str(outcome.path),
                    actual_zoom=outcome.actual_zoom,
                    provider=pick.provider,
                )
            )
            continue
        rnd_results.append(
            RoundResult(
                chip_index=pick.chip_index,
                capture_date=pick.capture_date,
                version=pick.version,
                pv_present=obs.pv_present,
                confidence=obs.confidence,
                quality_flag=obs.quality_flag,
                decision_source=obs.decision_source,
                evidence=obs.evidence,
                notes=obs.notes,
                chip_path=str(outcome.path),
                actual_zoom=outcome.actual_zoom,
                provider=pick.provider,
            )
        )

    rnd.results = rnd_results
    rnd.completed = True
    rnd.failed = False
    return rnd


def _load_gemini_config(env_file: Path):
    """Load GeminiClientConfig from .env.gemini.local. Lazy import to avoid hard dep in dry-run."""
    from scripts.validation.gemini_solar_image_review import (
        API_FORMATS,
        GeminiClientConfig,
        env_value,
        load_env_file,
    )

    env = load_env_file(env_file)
    base_url = env_value(env, "GOOGLE_GEMINI_BASE_URL")
    api_key = env_value(env, "GEMINI_API_KEY")
    model = env_value(env, "GEMINI_MODEL", "gemini-3-flash-preview")
    api_format = env_value(env, "GEMINI_API_FORMAT", "native")
    native_path = env_value(env, "GEMINI_NATIVE_PATH", "/v1beta")
    if not base_url:
        raise SystemExit(
            f"Missing GOOGLE_GEMINI_BASE_URL (set in {env_file} or env). Use --dry-run if you want to skip Gemini."
        )
    if not api_key:
        raise SystemExit(
            f"Missing GEMINI_API_KEY (set in {env_file} or env). Use --dry-run if you want to skip Gemini."
        )
    if api_format not in API_FORMATS:
        raise SystemExit(f"Unsupported GEMINI_API_FORMAT={api_format!r}; choose {sorted(API_FORMATS)}")
    return GeminiClientConfig(
        base_url=base_url,
        api_key=api_key,
        model=model,
        api_format=api_format,
        native_path=native_path,
    )


def run_one_anchor(
    anchor: dict[str, str],
    config: AdaptiveScanConfig,
    scan_states_dir: Path,
    *,
    dry_run: bool,
    force_restart: bool,
    chips_dir: Path | None = None,
    audit_dir: Path | None = None,
    gemini_config=None,
    gemini_config_round1=None,
    cheap_round_types: frozenset[str] | None = None,
    limiter=None,
    routing_salt_mode: str = "none",
    census_mid_date_iso: str | None = None,
    scorer: PresenceScorer | None = None,
    overwrite_chips: bool = False,
    min_cache_zoom: int | None = None,
    provenance_writer: Callable[[Mapping[str, object]], None] | None = None,
    scoring_provenance_writer: Callable[[Mapping[str, object]], None] | None = None,
    verdict_store: VerdictStore | None = None,
    catalog_cache: CatalogCache | None = None,
    catalog_force_refresh: bool = False,
    catalog_max_age_days: float = DEFAULT_CATALOG_MAX_AGE_DAYS,
    review_renderer: Callable[[Path, Mapping[str, str]], Path] | None = None,
    provider_chips_dirs: Mapping[str, Path] | None = None,
    tm_catalog_runner: Callable[..., object] | None = None,
    offline_tm_dates: Sequence[str] | None = None,
    offline_wayback_entries: Sequence[tuple[str, str]] | None = None,
    no_live_gehi: bool = False,
) -> ScanState:
    anchor_id = anchor["anchor_id"]
    state_path = state_path_for(anchor_id, scan_states_dir)

    state: ScanState | None = None
    if force_restart and state_path.exists():
        state_path.unlink()
    else:
        state = load_scan_state(state_path)

    if state is not None and not state.is_terminal:
        current_geometry = _anchor_geometry_version(anchor)
        state_geometry = str(
            state.geometry_version or LEGACY_V1_GEOMETRY_VERSION
        )
        if state_geometry != current_geometry:
            raise GeometryVersionMismatchError(
                f"geometry_version mismatch for {anchor_id}: "
                f"state={state_geometry!r} anchors={current_geometry!r}"
            )
        if state.geometry_version is None:
            state.geometry_version = state_geometry

    if state is None:
        state = create_scan_state(anchor)
        save_scan_state(state, state_path)

    if state.is_terminal:
        return state

    # Resolve the scorer this anchor scores through. Dry-run builds a per-anchor
    # dry-run stub from the anchor's deterministic profile (mirroring how the old
    # code derived a DryRunProfile per anchor); the real path uses the injected
    # scorer (default = registered gemini). The scorer's declared
    # failure_decision_sources parameterizes Case E via decide_next_action below.
    if dry_run:
        profile = dry_run_profile_for(anchor_id)
        scorer = get_scorer(
            "dry_run", label=profile.label, install_date=profile.install_date
        )
    elif scorer is None:
        scorer = get_scorer("gemini")
    # ISSUE-07 verdict store: wrap the resolved scorer so every scoring call
    # consults the content-addressed store first (re-runs pay only never-seen
    # chips). Must wrap BEFORE the provenance sidecar so cache hits still emit
    # sidecar rows (provenance outermost — see verdict_store module docstring).
    # Dry-run picks carry no chip files, so the store passes them through and
    # dry-run output stays byte-identical.
    if verdict_store is not None:
        from scripts.temporal.verdict_store import with_verdict_store

        scorer = with_verdict_store(scorer, verdict_store)
    # ISSUE-06 scoring-provenance sidecar: wrap the resolved scorer (dry-run OR
    # real) so every scored chip emits one provenance row stamped with this
    # anchor. The wrapper delegates the scorer's declared vocabulary + failure
    # sources verbatim, so decide_next_action's Case-E rule below is unaffected.
    if scoring_provenance_writer is not None:
        from scripts.temporal.scoring_provenance import with_scoring_provenance

        scoring_context = {"anchor_id": anchor_id}
        if anchor.get("geometry_version"):
            scoring_context["geometry_version"] = str(anchor["geometry_version"])
        scorer = with_scoring_provenance(
            scorer, scoring_provenance_writer, context=scoring_context
        )
    # ISSUE-13 catalog-cache kwargs are only forwarded when a cache is actually
    # active, so a default (`catalog_cache=None`) call is byte-identical to the
    # pre-ISSUE-13 call shape — this matters because test seams monkeypatch
    # `_fetch_real_vintage_catalog`/`make_vintage_check` with fixed-arity stubs
    # (see tests/temporal/test_run_adaptive_scan_seam.py `_install_gehi_stubs`).
    catalog_kwargs: dict[str, object] = {}
    if catalog_cache is not None:
        catalog_kwargs = {
            "catalog_cache": catalog_cache,
            "catalog_force_refresh": catalog_force_refresh,
            "catalog_max_age_days": catalog_max_age_days,
        }
    # Same guarded-kwarg pattern as the cache block: only forwarded when set,
    # so fixed-arity test stubs of the two callees keep working.
    if tm_catalog_runner is not None:
        catalog_kwargs["tm_catalog_runner"] = tm_catalog_runner
    # Same pattern again: forwarded to BOTH `_fetch_real_vintage_catalog` (via
    # fetch_kwargs below) and `make_vintage_check` (via check_kwargs below),
    # since both are built from this shared dict -- one flag, one place.
    if no_live_gehi:
        catalog_kwargs["no_live_gehi"] = True
    real_catalog: VintageCatalog | None = None
    if dry_run:
        vintages = dry_run_vintages(anchor_id)
        catalog_max_date = _resolve_catalog_max_date(
            (v.capture_date for v in vintages),
            config,
            census_date=census_mid_date_iso,
        )
    else:
        fetch_kwargs = dict(catalog_kwargs)
        if census_mid_date_iso is not None:
            fetch_kwargs["census_date"] = census_mid_date_iso
        # Guarded like the cache kwargs: only forwarded when an offline TM
        # catalog is active, so fixed-arity test stubs keep working.
        if offline_tm_dates is not None:
            fetch_kwargs["offline_tm_dates"] = offline_tm_dates
        # Same guarded-kwarg pattern: only forwarded when an offline Wayback
        # catalog is active, so fixed-arity test stubs keep working.
        if offline_wayback_entries is not None:
            fetch_kwargs["offline_wayback_entries"] = offline_wayback_entries
        real_catalog = _fetch_real_vintage_catalog(anchor, config, **fetch_kwargs)
        vintages = real_catalog.vintages
        catalog_max_date = (
            real_catalog.catalog_max_date
            or getattr(config, "catalog_max_date", None)
            or max(v.capture_date for v in vintages)
        )

    state.census_date = census_mid_date_iso
    state.catalog_max_date = catalog_max_date
    state.post_census_reference_frames = getattr(
        config, "post_census_reference_frames", None
    )
    save_scan_state(state, state_path)

    vintage_check = None
    if not dry_run:
        assert real_catalog is not None
        provider_maps = real_catalog.available_dates_by_provider_zoom
        if provider_maps:
            checks = {}
            for provider, dates_by_zoom in provider_maps.items():
                provider_config = dataclasses.replace(config, provider=provider)
                if provider == "Wayback":
                    provider_config = dataclasses.replace(
                        provider_config,
                        require_complete_coverage_for_catalog=False,
                        require_complete_coverage_for_download=False,
                    )
                check_kwargs = dict(catalog_kwargs)
                if census_mid_date_iso is not None:
                    check_kwargs["catalog_max_date"] = catalog_max_date
                checks[provider] = make_vintage_check(
                    anchor,
                    available_dates_by_zoom=dates_by_zoom,
                    config=provider_config,
                    **check_kwargs,
                )
            vintage_check = checks
        else:
            check_kwargs = dict(catalog_kwargs)
            if census_mid_date_iso is not None:
                check_kwargs["catalog_max_date"] = catalog_max_date
            vintage_check = make_vintage_check(
                anchor,
                available_dates_by_zoom=real_catalog.available_dates_by_zoom,
                config=config,
                **check_kwargs,
            )

    assert scorer is not None
    max_iter = 32
    for _ in range(max_iter):
        decision_kwargs: dict[str, object] = {
            "failure_decision_sources": scorer.failure_decision_sources,
        }
        if census_mid_date_iso is not None:
            decision_kwargs["census_date"] = census_mid_date_iso
            decision_kwargs["catalog_max_date"] = catalog_max_date
        action: Action = decide_next_action(
            state,
            vintages,
            config,
            **decision_kwargs,
        )
        if isinstance(action, TerminateAction):
            state.status = action.status
            if action.notes:
                state.notes = (state.notes + " | " if state.notes else "") + action.notes
            state.next_action = None
            save_scan_state(state, state_path)
            return state
        assert isinstance(action, ExecuteRoundAction)
        rnd = action.round
        if dry_run:
            rnd = execute_round_dry_run(rnd, scorer)
        else:
            assert chips_dir is not None and audit_dir is not None and gemini_config is not None
            # Model tiering by round_type: routine present/absent rounds (initial
            # coarse scan, bisection, walk_back/tail) use the cheap tier; only the
            # round_types NOT in cheap_round_types (by default anchor_recovery)
            # escalate to the capable tier. bisection asks the SAME present/absent
            # question as the initial round, so the cheap tier handles it — that is
            # ~86% of round>=2 volume, which is why round_id-based routing wasted the
            # scarce capable-tier quota. Falls back to the old round_id==1 rule when
            # cheap_round_types is unset (preserves legacy callers/tests).
            round_config = gemini_config
            if gemini_config_round1 is not None:
                if cheap_round_types is None:
                    use_cheap = rnd.round_id == 1
                else:
                    use_cheap = rnd.round_type in cheap_round_types
                if use_cheap:
                    round_config = gemini_config_round1
            # Forward the ISSUE-18 escape hatch / provenance sink only when active,
            # so a default run's call is byte-identical to before (keeps existing
            # execute_round_real stubs / call sites intact).
            issue18_kwargs: dict[str, object] = {}
            if overwrite_chips:
                issue18_kwargs["overwrite_chips"] = True
            if min_cache_zoom is not None:
                issue18_kwargs["min_cache_zoom"] = min_cache_zoom
            if provenance_writer is not None:
                issue18_kwargs["provenance_writer"] = provenance_writer
            if review_renderer is not None:
                issue18_kwargs["review_renderer"] = review_renderer
            if provider_chips_dirs is not None:
                issue18_kwargs["provider_chips_dirs"] = provider_chips_dirs
            if no_live_gehi:
                issue18_kwargs["no_live_gehi"] = True
            rnd = execute_round_real(
                rnd, anchor, config,
                chips_dir=chips_dir, audit_dir=audit_dir, gemini_config=round_config,
                scorer=scorer,
                vintage_check=vintage_check,
                census_mid_date_iso=census_mid_date_iso,
                limiter=limiter,
                routing_salt_mode=routing_salt_mode,
                **issue18_kwargs,
            )
        state.rounds.append(rnd)
        # Informational checkpoint metadata only: resume never reads next_action,
        # it re-derives the decision deterministically via decide_next_action().
        state.next_action = "decide_next_action"
        save_scan_state(state, state_path)
    raise RuntimeError(f"Scan loop exceeded {max_iter} rounds for {anchor_id}")


def _fetch_real_vintage_catalog(
    anchor: dict[str, str],
    config: AdaptiveScanConfig,
    *,
    census_date: str | None = None,
    catalog_cache: CatalogCache | None = None,
    catalog_force_refresh: bool = False,
    catalog_max_age_days: float = DEFAULT_CATALOG_MAX_AGE_DAYS,
    tm_catalog_runner: Callable[..., object] | None = None,
    offline_tm_dates: Sequence[str] | None = None,
    offline_wayback_entries: Sequence[tuple[str, str]] | None = None,
    no_live_gehi: bool = False,
) -> VintageCatalog:
    """Fetch bbox-complete GEHI vintages for an anchor.

    The catalog is a union over `config.discovery_zoom_ladder` in priority
    order. z19 normally gives the practical install-date catalog; z18 adds the
    wider historical picture when z19 is sparse. With
    `require_complete_coverage_for_catalog=True`, a date must be complete for
    the full chip bbox at that zoom before it can drive the adaptive scan.

    `catalog_cache=None` (the default) makes zero behavior change: every zoom
    in the ladder issues a live GEHI call exactly as before ISSUE-13.

    `no_live_gehi=True` forbids reaching the live `fetch_vintages_for_anchor`
    path below. The only way this function is normally called at all under
    zero-live-GEHI mode is an anchor absent from the offline catalog CSV for
    this provider (`offline_tm_dates`/`offline_wayback_entries` is `None` even
    though the offline flag is active) -- everything else is intercepted
    upstream by the two `offline_*` early-returns above. Rather than silently
    degrading (both `gehi_info`/`gehi_availability`'s no-cache path swallow
    arbitrary `runner` exceptions and return `[]`), this raises loudly so the
    anchor surfaces as a `done_ambiguous_orchestrator_error` scan_state (the
    existing `run_one_anchor` exception handling in `main()`'s `handle()`) with
    the reason in `notes` -- grep-able, and re-runnable once the anchor is
    added to the offline CSV (or via `--force-restart`).
    """
    if config.provider == "Merged":
        tm_config = dataclasses.replace(config, provider="TM")
        wayback_config = dataclasses.replace(
            config,
            provider="Wayback",
            require_complete_coverage_for_catalog=False,
            require_complete_coverage_for_download=False,
        )
        tm = _fetch_real_vintage_catalog(
            anchor,
            tm_config,
            census_date=census_date,
            catalog_cache=catalog_cache,
            catalog_force_refresh=catalog_force_refresh,
            catalog_max_age_days=catalog_max_age_days,
            tm_catalog_runner=tm_catalog_runner,
            offline_tm_dates=offline_tm_dates,
            no_live_gehi=no_live_gehi,
        )
        wayback = _fetch_real_vintage_catalog(
            anchor,
            wayback_config,
            census_date=census_date,
            catalog_cache=catalog_cache,
            catalog_force_refresh=catalog_force_refresh,
            catalog_max_age_days=catalog_max_age_days,
            tm_catalog_runner=tm_catalog_runner,
            offline_wayback_entries=offline_wayback_entries,
            no_live_gehi=no_live_gehi,
        )
        return merge_provider_catalogs(tm, wayback)

    if config.provider == "TM" and offline_tm_dates is not None:
        return _build_offline_tm_catalog(
            offline_tm_dates, config, census_date=census_date
        )

    if config.provider == "Wayback" and offline_wayback_entries is not None:
        return _build_offline_wayback_catalog(
            offline_wayback_entries, config, census_date=census_date
        )

    if no_live_gehi:
        raise RuntimeError(
            f"no_live_gehi: refusing live {config.provider} catalog fetch for anchor "
            f"{anchor.get('anchor_id', '<unknown>')} (missing from the offline "
            f"{config.provider} catalog CSV, or no offline catalog supplied for "
            "this provider)"
        )

    from scripts.temporal.gehi_info import fetch_vintages_for_anchor

    # TM (Google khmdb) metadata calls are the 403-ban surface; Wayback is not.
    runner_kwargs: dict[str, object] = (
        {"runner": tm_catalog_runner}
        if tm_catalog_runner is not None and config.provider == "TM"
        else {}
    )
    info_by_zoom: dict[int, dict[str, object]] = {}
    for zoom in config.discovery_zoom_ladder:
        info_rows = fetch_vintages_for_anchor(
            anchor,
            zoom=zoom,
            provider=config.provider,
            catalog_cache=catalog_cache,
            force_refresh=catalog_force_refresh,
            max_age_days=catalog_max_age_days,
            **runner_kwargs,
        )
        info_by_date: dict[str, object] = {}
        for row in info_rows:
            capture_date = str(row.get("capture_date", "")).strip()[:10]
            if capture_date and capture_date not in info_by_date:
                info_by_date[capture_date] = row
        info_by_zoom[int(zoom)] = info_by_date

    cutoff_candidates = _catalog_cutoff_candidates(
        (
            capture_date
            for rows in info_by_zoom.values()
            for capture_date in rows
        ),
        config,
        census_date=census_date,
    )

    effective_max_date = cutoff_candidates[0]
    available_dates_by_zoom: dict[int, set[str]] = {}
    if config.require_complete_coverage_for_catalog:
        from scripts.temporal.gehi_availability import fetch_availability_for_anchor

        keep = max(0, int(config.post_census_reference_frames))
        for candidate_max_date in cutoff_candidates:
            candidate_dates_by_zoom: dict[int, set[str]] = {}
            for zoom in config.discovery_zoom_ladder:
                availability_rows = fetch_availability_for_anchor(
                    anchor,
                    zoom=zoom,
                    provider=config.provider,
                    min_date=config.catalog_min_date,
                    max_date=candidate_max_date,
                    parallel=config.availability_parallel,
                    complete=True,
                    catalog_cache=catalog_cache,
                    force_refresh=catalog_force_refresh,
                    max_age_days=catalog_max_age_days,
                    **runner_kwargs,
                )
                candidate_dates_by_zoom[int(zoom)] = {
                    str(row.get("capture_date", ""))[:10]
                    for row in availability_rows
                    if row.get("capture_date")
                }
            effective_max_date = candidate_max_date
            available_dates_by_zoom = candidate_dates_by_zoom
            reference_dates = {
                capture_date
                for dates in candidate_dates_by_zoom.values()
                for capture_date in dates
                if census_date is not None
                and capture_date > census_date[:10]
            }
            if census_date is None or len(reference_dates) >= keep:
                break
    else:
        available_dates_by_zoom = {
            zoom: {
                capture_date
                for capture_date in rows
                if capture_date <= effective_max_date
            }
            for zoom, rows in info_by_zoom.items()
        }

    by_date: dict[str, VintageEntry] = {}
    for zoom in config.discovery_zoom_ladder:
        info_by_date = info_by_zoom[int(zoom)]
        allowed_dates = available_dates_by_zoom[int(zoom)]

        for capture_date in sorted(set(info_by_date) & allowed_dates):
            if capture_date in by_date:
                continue
            row = info_by_date[capture_date]
            version_raw = row.get("version") if isinstance(row, dict) else None
            if version_raw in (None, ""):
                continue
            try:
                version = int(version_raw)
            except (TypeError, ValueError):
                continue
            by_date[capture_date] = VintageEntry(
                capture_date=capture_date,
                version=version,
                provider=config.provider,
            )

    return VintageCatalog(
        vintages=[by_date[d] for d in sorted(by_date)],
        available_dates_by_zoom=available_dates_by_zoom,
        catalog_max_date=effective_max_date,
    )


# Offline-TM catalog entries carry this sentinel version. It deliberately
# matches the `or "noversion"` fallback in gehi_download's chip naming, so
# offline picks resolve the pre-downloaded basemap chips (…_vnoversion.tif)
# instead of re-downloading under a live numeric version.
OFFLINE_TM_VERSION = "noversion"


def _build_offline_tm_catalog(
    offline_tm_dates: Sequence[str],
    config: AdaptiveScanConfig,
    *,
    census_date: str | None,
) -> VintageCatalog:
    """Build the TM vintage catalog from pre-fetched dates, zero live TM calls.

    Source is the download-phase candidates CSV (one capture_date list per
    anchor). Completeness is assumed delegated to the download layer: the
    basemap rebuild already downloaded these dates through the zoom ladder, and
    any residual gap surfaces as a per-pick download failure, not a catalog
    error. Every ladder zoom (discovery + download) is marked available so
    `make_vintage_check` never lazy-fetches live TM availability either.

    Mirrors the live coverage-gated cutoff walk: candidates advance until
    `post_census_reference_frames` post-census dates fall inside the bound
    (ISSUE-26 semantics), except "complete" is a given here.
    """
    dates = sorted(
        {
            str(d)[:10]
            for d in offline_tm_dates
            if str(d)[:10] >= config.catalog_min_date[:10]
        }
    )
    cutoff_candidates = _catalog_cutoff_candidates(
        dates, config, census_date=census_date
    )
    keep = max(0, int(config.post_census_reference_frames))
    effective_max_date = cutoff_candidates[0]
    allowed: set[str] = set()
    for candidate_max_date in cutoff_candidates:
        effective_max_date = candidate_max_date
        allowed = {d for d in dates if d <= candidate_max_date}
        reference_dates = {
            d for d in allowed if census_date is not None and d > census_date[:10]
        }
        if census_date is None or len(reference_dates) >= keep:
            break
    ladder_zooms = {int(z) for z in config.discovery_zoom_ladder} | {
        int(z) for z in config.download_zoom_ladder
    }
    return VintageCatalog(
        vintages=[
            VintageEntry(
                capture_date=d, version=OFFLINE_TM_VERSION, provider="TM"
            )
            for d in sorted(allowed)
        ],
        available_dates_by_zoom={zoom: set(allowed) for zoom in ladder_zooms},
        catalog_max_date=effective_max_date,
    )


def _build_offline_wayback_catalog(
    offline_wayback_entries: Sequence[tuple[str, str]],
    config: AdaptiveScanConfig,
    *,
    census_date: str | None,
) -> VintageCatalog:
    """Build the Wayback vintage catalog from pre-fetched (capture_date, version)
    pairs, zero live Wayback info calls.

    Precisely mirrors the live Wayback path's semantics
    (`_fetch_real_vintage_catalog`'s final ``else:`` branch, which is what
    Wayback always takes because `main()` forces
    `require_complete_coverage_for_catalog=False` for `--provider Wayback`),
    with ONE deliberate exception called out below.

    - **Cutoff**: `effective_max_date` is `_catalog_cutoff_candidates(...)[0]`
      taken directly -- unlike `_build_offline_tm_catalog`, there is NO
      candidate-advance loop here. The live non-coverage-gated branch never
      advances past the first cutoff candidate (that advance loop only exists
      in the `require_complete_coverage_for_catalog=True` branch, which
      Wayback never enters); copying the TM offline builder's loop here would
      silently invent post-census-reference-frame behavior Wayback's live
      path does not have.
    - **z20 (or any download-only zoom)**: marked UNAVAILABLE (empty set)
      rather than left to lazy-fetch. This is the one intentional divergence
      from live: the live path leaves non-discovery download zooms out of
      `available_dates_by_zoom` entirely, so `make_vintage_check` lazy-fetches
      them live on first lookup (occasionally finding a hit). Production
      Wayback z20 coverage is ~97% absent (see `census2023_zoom_ladder`'s
      docstring in scan_config.py), so this offline path trades away those
      rare live hits for zero live Wayback calls. Zoom keys are
      `config.discovery_zoom_ladder | config.download_zoom_ladder`; any zoom
      NOT in the discovery ladder gets the empty set (not hardcoded 19/18/20).
    """
    version_by_date: dict[str, int] = {}
    for capture_date, version_raw in offline_wayback_entries:
        d = str(capture_date)[:10]
        if not d or d < config.catalog_min_date[:10]:
            continue
        try:
            version_by_date[d] = int(version_raw)
        except (TypeError, ValueError):
            continue

    dates = sorted(version_by_date)
    effective_max_date = _catalog_cutoff_candidates(
        dates, config, census_date=census_date
    )[0]
    allowed = [d for d in dates if d <= effective_max_date]

    discovery_zooms = {int(z) for z in config.discovery_zoom_ladder}
    ladder_zooms = discovery_zooms | {int(z) for z in config.download_zoom_ladder}
    available_dates_by_zoom = {
        zoom: (set(allowed) if zoom in discovery_zooms else set())
        for zoom in ladder_zooms
    }

    return VintageCatalog(
        vintages=[
            VintageEntry(
                capture_date=d, version=version_by_date[d], provider="Wayback"
            )
            for d in allowed
        ],
        available_dates_by_zoom=available_dates_by_zoom,
        catalog_max_date=effective_max_date,
    )


def load_offline_provider_catalogs(
    path: Path,
) -> tuple[dict[str, list[str]], dict[str, list[tuple[str, str]]]]:
    """Single pass over the download-phase candidates CSV producing both the
    offline-TM input (per-anchor capture-date list) and the offline-Wayback
    input (per-anchor (capture_date, raw version string) pairs).

    The CSV (`build_gehi_candidates.py` output; `anchor_id`/`capture_date`/
    `version`/`provider` columns) is ~1.2M rows / ~43MB, so both offline
    catalogs are built from one read rather than two. Wayback ``version`` is
    kept as the raw CSV string here; `_build_offline_wayback_catalog` does the
    `int()` validation and skip, matching where the live info-parsing loop
    (`_fetch_real_vintage_catalog`'s `by_date` loop) does its own
    `int(version_raw)` validation. Date strings are interned via a shared pool
    — the corpus has only a few hundred distinct dates.
    """
    tm_dates_by_anchor: dict[str, list[str]] = {}
    wayback_by_anchor: dict[str, list[tuple[str, str]]] = {}
    date_pool: dict[str, str] = {}
    with path.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            provider = str(row.get("provider", "")).strip()
            if provider not in ("TM", "Wayback"):
                continue
            anchor_id = str(row.get("anchor_id", "")).strip()
            capture_date = str(row.get("capture_date", "")).strip()[:10]
            if not anchor_id or not capture_date:
                continue
            capture_date = date_pool.setdefault(capture_date, capture_date)
            if provider == "TM":
                tm_dates_by_anchor.setdefault(anchor_id, []).append(capture_date)
            else:
                version_raw = str(row.get("version", "")).strip()
                wayback_by_anchor.setdefault(anchor_id, []).append(
                    (capture_date, version_raw)
                )
    return tm_dates_by_anchor, wayback_by_anchor


def load_offline_tm_catalog(path: Path) -> dict[str, list[str]]:
    """Load per-anchor TM capture dates from the download-phase candidates CSV.

    Expects `build_gehi_candidates.py` output (`anchor_id`, `capture_date`,
    `provider` columns); only TM rows are used. Date strings are interned via a
    shared pool — the corpus is ~1.2M rows over a few hundred distinct dates.

    Thin wrapper over `load_offline_provider_catalogs` kept for signature/
    behavior back-compat with existing callers and tests; prefer the combined
    loader when both offline TM and offline Wayback catalogs are needed from
    the same CSV, to avoid reading it twice.
    """
    tm_dates_by_anchor, _ = load_offline_provider_catalogs(path)
    return tm_dates_by_anchor


def load_offline_wayback_catalog(path: Path) -> dict[str, list[tuple[str, str]]]:
    """Load per-anchor Wayback (capture_date, raw version string) pairs from the
    download-phase candidates CSV. Thin wrapper over
    `load_offline_provider_catalogs`; see `load_offline_tm_catalog` for why the
    combined loader is preferred when both maps are needed.
    """
    _, wayback_by_anchor = load_offline_provider_catalogs(path)
    return wayback_by_anchor


@dataclass(frozen=True)
class OfflineDiskFilterStats:
    """Summary of `filter_offline_catalogs_to_disk`'s effect, for the startup log."""

    tm_before: int
    tm_after: int
    wayback_before: int
    wayback_after: int
    tm_dropped_dates: Counter[str]
    wayback_dropped_dates: Counter[str]
    # (anchor_id, candidates_before, candidates_after, frac_dropped), sorted by
    # frac_dropped descending. Combined TM+Wayback per anchor (matches the
    # 2026-07-17 forensics severity buckets, which bucket on combined loss).
    anchors_over_50pct_loss: list[tuple[str, int, int, float]]


def filter_offline_catalogs_to_disk(
    tm_dates_by_anchor: Mapping[str, Sequence[str]],
    wayback_by_anchor: Mapping[str, Sequence[tuple[str, str]]],
    *,
    tm_chips_dir: Path,
    wayback_chips_dir: Path,
    zoom_ladder: Sequence[int],
) -> tuple[dict[str, list[str]], dict[str, list[tuple[str, str]]], OfflineDiskFilterStats]:
    """Filter both offline catalogs down to entries with a chip file on disk.

    Root cause this closes (2026-07-17 forensics addendum): the offline catalog
    CSV documents dates the ORIGINAL live catalog metadata call once reported,
    not dates the basemap download actually captured to disk -- for a small set
    of "phantom" dates (ESRI/Google metadata claims coverage the real tile store
    lacks), the adaptive scan still picks them, `download_chip_with_zoom_ladder`
    finds no cached chip, and even under `--no-live-gehi` that pick becomes a
    wasted `download_failed` row instead of never being offered at all. This
    filter removes exactly those entries BEFORE the picker ever sees them, so a
    clean rerun has zero download_failed rows from this cause.

    Same filename-resolution rule the cache-scan in
    `download_chip_with_zoom_ladder` uses: a chip counts as present if it exists
    (non-empty) at ANY zoom in `zoom_ladder` -- pass `config.download_zoom_ladder`
    (the exact ladder that function iterates), not the discovery ladder. TM
    entries resolve via the `OFFLINE_TM_VERSION` ("noversion") sentinel
    filename; Wayback entries via their real numeric version.

    Every anchor_id key from the input dicts is preserved in the output, even
    when every one of its entries gets filtered out (an empty list, not a
    missing key) -- this matters downstream: `_fetch_real_vintage_catalog`
    treats `offline_tm_dates=None`/`offline_wayback_entries=None` (anchor
    entirely ABSENT from the source dict) as "not in the offline catalog at
    all" (a `--no-live-gehi` hard error), which must stay reserved for anchors
    genuinely missing from the CSV -- not conflated with "was in the CSV but
    every candidate turned out un-downloadable", a legitimate (if unlucky)
    empty-catalog-for-this-provider outcome that should NOT raise.

    ISSUE-26 cutoff-walk ordering: this filter MUST run before
    `_build_offline_tm_catalog`/`_build_offline_wayback_catalog`'s
    `_catalog_cutoff_candidates` walk (it does -- callers filter the raw
    per-anchor dicts here, then hand the FILTERED dicts down as
    `offline_tm_dates`/`offline_wayback_entries`, so the walk only ever sees
    obtainable dates). This is a deliberate, load-bearing ordering choice, not
    an implementation detail: walking first and filtering after would let the
    walk "spend" a post-census reference slot on a date that turns out
    undownloadable, silently under-delivering `post_census_reference_frames`
    without ever advancing to a later candidate to compensate (the existing
    unfiltered code has exactly this latent bug). Filtering first means the
    walk always advances until it finds N genuinely obtainable post-census
    dates (or exhausts the filtered list and degrades to the last one --
    already-tested behavior, see `test_offline_census_cutoff_short_tail_uses_last_available`).
    So yes, this changes which cutoff candidate gets chosen versus the
    unfiltered path whenever a would-be candidate is undownloadable -- and that
    change is the fix, not a side effect to guard against.
    """
    from scripts.temporal.gehi_download import _chip_path_for

    def _has_chip(chips_dir: Path, anchor_id: str, date: str, version: str) -> bool:
        for zoom in zoom_ladder:
            path = _chip_path_for(chips_dir, anchor_id, date, version, zoom)
            try:
                if path.exists() and path.stat().st_size > 0:
                    return True
            except OSError:
                continue
        return False

    tm_dropped_dates: Counter[str] = Counter()
    filtered_tm: dict[str, list[str]] = {}
    for anchor_id, dates in tm_dates_by_anchor.items():
        kept: list[str] = []
        for d in dates:
            if _has_chip(tm_chips_dir, anchor_id, d, OFFLINE_TM_VERSION):
                kept.append(d)
            else:
                tm_dropped_dates[d] += 1
        filtered_tm[anchor_id] = kept

    wayback_dropped_dates: Counter[str] = Counter()
    filtered_wayback: dict[str, list[tuple[str, str]]] = {}
    for anchor_id, entries in wayback_by_anchor.items():
        kept_wb: list[tuple[str, str]] = []
        for d, v in entries:
            if _has_chip(wayback_chips_dir, anchor_id, d, v):
                kept_wb.append((d, v))
            else:
                wayback_dropped_dates[d] += 1
        filtered_wayback[anchor_id] = kept_wb

    anchors_over_50pct_loss: list[tuple[str, int, int, float]] = []
    for anchor_id in set(tm_dates_by_anchor) | set(wayback_by_anchor):
        before_n = len(tm_dates_by_anchor.get(anchor_id, ())) + len(wayback_by_anchor.get(anchor_id, ()))
        after_n = len(filtered_tm.get(anchor_id, ())) + len(filtered_wayback.get(anchor_id, ()))
        if before_n > 0:
            frac_dropped = 1.0 - (after_n / before_n)
            if frac_dropped > 0.5:
                anchors_over_50pct_loss.append((anchor_id, before_n, after_n, frac_dropped))
    anchors_over_50pct_loss.sort(key=lambda t: -t[3])

    stats = OfflineDiskFilterStats(
        tm_before=sum(len(v) for v in tm_dates_by_anchor.values()),
        tm_after=sum(len(v) for v in filtered_tm.values()),
        wayback_before=sum(len(v) for v in wayback_by_anchor.values()),
        wayback_after=sum(len(v) for v in filtered_wayback.values()),
        tm_dropped_dates=tm_dropped_dates,
        wayback_dropped_dates=wayback_dropped_dates,
        anchors_over_50pct_loss=anchors_over_50pct_loss,
    )
    return filtered_tm, filtered_wayback, stats


def format_offline_disk_filter_summary(
    stats: OfflineDiskFilterStats, *, top_n: int = 10, max_anchor_samples: int = 20
) -> list[str]:
    """Human-readable `[CFG]`-prefixed-by-caller lines documenting exactly what
    `--offline-require-chip-on-disk` excluded, for the run log."""
    lines: list[str] = []
    tm_dropped = stats.tm_before - stats.tm_after
    wb_dropped = stats.wayback_before - stats.wayback_after
    lines.append(
        f"offline_require_chip_on_disk: TM {tm_dropped}/{stats.tm_before} entries dropped "
        f"({100.0 * tm_dropped / max(1, stats.tm_before):.2f}%), "
        f"Wayback {wb_dropped}/{stats.wayback_before} dropped "
        f"({100.0 * wb_dropped / max(1, stats.wayback_before):.2f}%)"
    )
    if stats.tm_dropped_dates:
        top = ", ".join(f"{d}x{n}" for d, n in stats.tm_dropped_dates.most_common(top_n))
        lines.append(f"  top dropped TM dates: {top}")
    if stats.wayback_dropped_dates:
        top = ", ".join(f"{d}x{n}" for d, n in stats.wayback_dropped_dates.most_common(top_n))
        lines.append(f"  top dropped Wayback dates: {top}")
    n_loss = len(stats.anchors_over_50pct_loss)
    lines.append(f"  anchors losing >50% of their offline candidates to this filter: {n_loss}")
    for anchor_id, before_n, after_n, frac in stats.anchors_over_50pct_loss[:max_anchor_samples]:
        lines.append(
            f"    {anchor_id}: {before_n} -> {after_n} candidates ({100.0 * frac:.0f}% dropped)"
        )
    if n_loss > max_anchor_samples:
        lines.append(f"    ... and {n_loss - max_anchor_samples} more (see forensics memo for the full list)")
    return lines


def _fetch_real_vintages(anchor: dict[str, str], config: AdaptiveScanConfig) -> list[VintageEntry]:
    """Backward-compatible helper for tests/scripts that only need vintages."""
    return _fetch_real_vintage_catalog(anchor, config).vintages


def summarize(states: Iterable[ScanState]) -> None:
    by_status: dict[str, int] = defaultdict(int)
    zoom_counts: dict[str, int] = defaultdict(int)
    total_rounds = 0
    total_observations = 0
    for s in states:
        by_status[s.status] += 1
        total_rounds += len(s.rounds)
        for rnd in s.rounds:
            total_observations += len(rnd.results)
            for r in rnd.results:
                key = str(r.actual_zoom) if r.actual_zoom is not None else "unknown"
                zoom_counts[key] += 1
    print(f"\nProcessed {sum(by_status.values())} anchors, {total_rounds} rounds, {total_observations} observations.")
    for status in sorted(by_status):
        print(f"  {status}: {by_status[status]}")

    # Achieved-zoom distribution over every scored chip (ISSUE-18 / D17): the
    # ladder rung GEHI actually served, which rung-level tooling otherwise buries
    # in raw scan-state JSON. None (download failed / never scored) -> "unknown".
    total_zoom = sum(zoom_counts.values())
    print("Achieved-zoom distribution:")
    if total_zoom == 0:
        print("  (no scored chips)")
    else:
        for key in sorted(zoom_counts):
            n = zoom_counts[key]
            label = f"z{key}" if key != "unknown" else "unknown"
            print(f"  {label}: {n} ({100.0 * n / total_zoom:.1f}%)")


def _anchor_grid_ids(anchor: Mapping[str, str]) -> list[str]:
    """Every grid an anchor touches: ``source_grids`` (';'-joined) or ``grid_id``."""
    raw = str(anchor.get("source_grids", "") or "").strip()
    if not raw:
        raw = str(anchor.get("grid_id", "") or "").strip()
    return [g.strip() for g in raw.split(";") if g.strip()]


def _resolve_census_mid_date(
    anchor: dict[str, str],
    override: str | None,
    *,
    grid_capture_dates: Mapping[str, date] | None = None,
) -> str | None:
    """Look up the per-anchor census date (ISSUE-26 cutoff / GT-prior anchor).

    Precedence: explicit ``--census-mid-date-override`` > per-grid Vexcel
    ``last_capture_date`` (``--vexcel-capture-csv``) > region-level
    ``census_imagery_mid_date`` from regions.yaml. The per-grid date is the
    real flight date of the imagery the detection ran on, so the ISSUE-26
    "census + N reference frames" cutoff brackets the true capture, not the
    region-wide mid-year fallback. Multi-grid chip groups take the max over
    their grids — the date by which the whole group is certainly imaged.

    For Phase-0 we hardcode the mapping region_key -> default census layer.
    Real workflow will pass --layer-id explicitly, but this smoke run only has
    one census layer per region (vexcel_2024 for JHB).
    """
    if override:
        return override
    if grid_capture_dates:
        hits = [
            grid_capture_dates[g]
            for g in _anchor_grid_ids(anchor)
            if g in grid_capture_dates
        ]
        if hits:
            return max(hits).isoformat()
    try:
        from core.region_registry import get_imagery_layer  # type: ignore[import-not-found]
    except Exception:  # noqa: BLE001
        return None
    region_key = anchor.get("region_key", "").strip()
    if not region_key:
        return None
    layer_for_region = {
        "johannesburg": "vexcel_2024",
        "cape_town": "aerial_2025",
    }
    layer_id = layer_for_region.get(region_key)
    if not layer_id:
        return None
    try:
        layer = get_imagery_layer(region_key, layer_id)
    except Exception:  # noqa: BLE001
        return None
    return layer.census_imagery_mid_date


def _exit_code_for_states(states: Iterable[ScanState]) -> int:
    """Return a process exit code: nonzero if any anchor failed.

    Continue-on-error keeps the batch running, but an anchor whose status starts
    with ``done_ambiguous_orchestrator_error`` is a genuine failure that must
    surface as a nonzero exit so callers / CI do not treat a partial run as
    success.
    """
    failed = [
        s.anchor_id
        for s in states
        if s.status.startswith("done_ambiguous_orchestrator_error")
    ]
    return 1 if failed else 0


def main() -> None:
    args = parse_args()
    review_renderer = (
        make_fixed_extent_review_renderer(args.review_extent_m)
        if args.review_extent_m is not None
        else None
    )
    config = load_config(args.config)
    config_overrides: dict[str, object] = {"provider": args.provider}
    if args.provider == "Wayback":
        # Wayback `availability` returns layer-release dates, not captured dates,
        # so intersecting it with the captured-date info catalog yields the empty
        # set. Rely on download's own --exact-date matching (which rejects
        # partial-coverage dates) instead of the pre-gate.
        config_overrides["require_complete_coverage_for_catalog"] = False
        config_overrides["require_complete_coverage_for_download"] = False
    config = dataclasses.replace(config, **config_overrides)
    provider_chips_dirs = None
    if args.provider == "Merged":
        if args.merged_tm_chips_dir is None or args.merged_wayback_chips_dir is None:
            raise SystemExit(
                "--provider Merged requires --merged-tm-chips-dir and "
                "--merged-wayback-chips-dir"
            )
        provider_chips_dirs = {
            "TM": args.merged_tm_chips_dir,
            "Wayback": args.merged_wayback_chips_dir,
        }
    if not args.anchors_csv.exists():
        raise SystemExit(f"Anchors CSV not found: {args.anchors_csv}")
    args.scan_states_dir.mkdir(parents=True, exist_ok=True)
    anchors = read_anchors(args.anchors_csv, limit=args.limit_anchors)
    if not anchors:
        raise SystemExit("Anchors CSV produced 0 rows.")
    if args.review_extent_m is not None:
        for anchor in anchors:
            validate_fixed_extent_anchor_geometry(anchor, args.review_extent_m)

    gemini_config = None
    gemini_config_round1 = None
    cheap_round_types: frozenset[str] | None = None
    limiter = None
    # The real-path scorer is selected via the registry (default 'gemini'), never
    # a hardwired import. Dry-run builds its per-anchor 'dry_run' stub inside
    # run_one_anchor, so it stays None here.
    scorer: PresenceScorer | None = None
    if not args.dry_run:
        scorer = get_scorer(args.scorer)
        env_file = args.gemini_env_file or _default_gemini_env()
        gemini_config = _load_gemini_config(env_file)
        if args.round2_model:
            gemini_config = dataclasses.replace(gemini_config, model=args.round2_model)
        if args.round1_model:
            gemini_config_round1 = dataclasses.replace(gemini_config, model=args.round1_model)
        cheap_round_types = frozenset(
            t.strip() for t in args.cheap_round_types.split(",") if t.strip()
        )
        from scripts.validation.gemini_solar_image_review import RateLimiter

        limiter = RateLimiter(args.qps)
        args.chips_dir.mkdir(parents=True, exist_ok=True)
        if provider_chips_dirs is not None:
            for provider_dir in provider_chips_dirs.values():
                provider_dir.mkdir(parents=True, exist_ok=True)
        args.audit_dir.mkdir(parents=True, exist_ok=True)
        round1_model = gemini_config_round1.model if gemini_config_round1 is not None else gemini_config.model
        print(
            f"[CFG] cheap_model={round1_model} capable_model={gemini_config.model} "
            f"cheap_round_types={','.join(sorted(cheap_round_types))} "
            f"qps={args.qps} anchor_workers={args.anchor_workers} "
            f"routing_salt_mode={args.routing_salt_mode}"
        )

    marker = "[DRY]" if args.dry_run else "[RUN]"
    # Resolve census mid-date per anchor up front (main thread): cheap, and warms
    # the region_registry cache so concurrent anchor workers don't race its first load.
    grid_capture_dates: Mapping[str, date] | None = None
    if args.vexcel_capture_csv is not None:
        if not args.vexcel_capture_csv.exists():
            raise SystemExit(f"--vexcel-capture-csv not found: {args.vexcel_capture_csv}")
        from scripts.temporal.infer_install_dates import load_vexcel_capture_dates

        grid_capture_dates = load_vexcel_capture_dates(args.vexcel_capture_csv)
        if not grid_capture_dates:
            raise SystemExit(
                f"--vexcel-capture-csv yielded 0 usable rows: {args.vexcel_capture_csv}"
            )
    census_by_anchor = {
        anchor["anchor_id"]: _resolve_census_mid_date(
            anchor, args.census_mid_date_override, grid_capture_dates=grid_capture_dates
        )
        for anchor in anchors
    }
    print_lock = threading.Lock()

    # Per-chip provenance sidecar (ISSUE-18 / D17): one JSONL record per download
    # outcome, written next to the scan-states dir. Only the real path downloads
    # chips, so dry-run writes nothing. Thread-safe: a lock guards the append so
    # concurrent anchor workers never interleave a line.
    provenance_writer: Callable[[Mapping[str, object]], None] | None = None
    if not args.dry_run:
        provenance_path = args.scan_states_dir.parent / "chip_provenance.jsonl"
        provenance_path.parent.mkdir(parents=True, exist_ok=True)
        provenance_lock = threading.Lock()

        def provenance_writer(record: Mapping[str, object]) -> None:
            line = json.dumps(record, ensure_ascii=False)
            with provenance_lock, provenance_path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")

    # Scoring-provenance sidecar (ISSUE-06 / D5): one JSONL row per scored chip,
    # written next to the scan-states dir. Enabled for BOTH dry-run and real runs
    # (a dry-run still scores chips through the seam), so its scorer identity is
    # captured regardless of transport. Thread-safe append (its own lock).
    from scripts.temporal.scoring_provenance import jsonl_writer as _scoring_jsonl_writer

    scoring_provenance_writer = _scoring_jsonl_writer(
        args.scan_states_dir.parent / "scoring_provenance.jsonl"
    )

    # ISSUE-07: one VerdictStore shared across every anchor worker (thread-safe;
    # single-writer across processes, enforced via a lock file). Wrapped around
    # the scorer inside run_one_anchor, INSIDE the provenance wrapper, so cache
    # hits still emit scoring-provenance rows. Dry-run picks have no chip files
    # and pass through unhashed, so enabling the store never changes dry-run
    # output. `--no-verdict-store` keeps `verdict_store=None` end to end.
    verdict_store = None
    if not args.no_verdict_store:
        from scripts.temporal.verdict_store import VerdictStore

        store_path = args.verdict_store or (args.scan_states_dir.parent / "verdict_store.jsonl")
        verdict_store = VerdictStore(store_path)
        print(f"[CFG] verdict_store={store_path} records={len(verdict_store)}")

    # ISSUE-13: one CatalogCache shared across every anchor worker (thread-safe:
    # sqlite3 WAL + an internal lock). Lazily constructed only for the real path
    # (dry-run never touches GEHI) and only when the cache isn't disabled;
    # `--no-catalog-cache` keeps `catalog_cache=None` end to end, which is a
    # zero-behavior-change no-op in run_one_anchor / make_vintage_check /
    # _fetch_real_vintage_catalog.
    catalog_cache: CatalogCache | None = None
    if not args.dry_run and not args.no_catalog_cache:
        db_path = (args.catalog_cache_dir / DB_FILENAME) if args.catalog_cache_dir else None
        catalog_cache = CatalogCache(db_path)

    if args.offline_wayback and args.offline_tm_catalog_csv is None:
        raise SystemExit("--offline-wayback requires --offline-tm-catalog-csv (same CSV, Wayback rows)")

    if args.no_live_gehi and not (args.offline_tm_catalog_csv is not None and args.offline_wayback):
        raise SystemExit(
            "--no-live-gehi requires both --offline-tm-catalog-csv and --offline-wayback "
            "(every catalog-build path must be offline for the no-live-GEHI guarantee to hold)"
        )

    if args.offline_require_chip_on_disk and not (
        args.no_live_gehi and args.offline_tm_catalog_csv is not None and args.offline_wayback
    ):
        raise SystemExit(
            "--offline-require-chip-on-disk requires --no-live-gehi plus both "
            "--offline-tm-catalog-csv and --offline-wayback (it is a stricter sub-mode "
            "of the same offline-only guarantee)"
        )

    # Offline TM/Wayback catalogs (see --offline-tm-catalog-csv / --offline-wayback
    # help): loaded once in the main thread from a single CSV pass, then handed
    # to workers as per-anchor lists. Anchors missing from the CSV fall back to
    # the corresponding live catalog path (logged below).
    offline_tm_catalog: dict[str, list[str]] | None = None
    offline_wayback_catalog: dict[str, list[tuple[str, str]]] | None = None
    if args.offline_tm_catalog_csv is not None:
        if not args.offline_tm_catalog_csv.exists():
            raise SystemExit(
                f"--offline-tm-catalog-csv not found: {args.offline_tm_catalog_csv}"
            )
        offline_tm_catalog, offline_wayback_catalog = load_offline_provider_catalogs(
            args.offline_tm_catalog_csv
        )
        covered = sum(1 for a in anchors if a["anchor_id"] in offline_tm_catalog)
        print(
            f"[CFG] offline_tm_catalog={args.offline_tm_catalog_csv} "
            f"anchors_covered={covered}/{len(anchors)}"
        )
        if covered == 0:
            raise SystemExit(
                "--offline-tm-catalog-csv matched 0 anchors - wrong CSV for this anchors file?"
            )
        if args.offline_wayback:
            wb_covered = sum(1 for a in anchors if a["anchor_id"] in offline_wayback_catalog)
            print(
                f"[CFG] offline_wayback_catalog={args.offline_tm_catalog_csv} "
                f"anchors_covered={wb_covered}/{len(anchors)}"
            )
            if wb_covered == 0:
                raise SystemExit(
                    "--offline-wayback matched 0 anchors in the offline TM catalog CSV's Wayback rows"
                )
        else:
            offline_wayback_catalog = None

    if args.offline_require_chip_on_disk:
        assert offline_tm_catalog is not None and offline_wayback_catalog is not None  # enforced above
        tm_chips_dir_for_filter = (
            provider_chips_dirs["TM"] if provider_chips_dirs is not None else args.chips_dir
        )
        wayback_chips_dir_for_filter = (
            provider_chips_dirs["Wayback"] if provider_chips_dirs is not None else args.chips_dir
        )
        offline_tm_catalog, offline_wayback_catalog, disk_filter_stats = filter_offline_catalogs_to_disk(
            offline_tm_catalog,
            offline_wayback_catalog,
            tm_chips_dir=tm_chips_dir_for_filter,
            wayback_chips_dir=wayback_chips_dir_for_filter,
            zoom_ladder=config.download_zoom_ladder,
        )
        for line in format_offline_disk_filter_summary(disk_filter_stats):
            print(f"[CFG] {line}")

    if args.no_live_gehi:
        print(
            "[CFG] no_live_gehi=True -- this run will NEVER spawn a GEHI subprocess; "
            "missing-chip picks fail as all_zooms_failed, anchors absent from the "
            "offline catalog CSV record done_ambiguous_orchestrator_error"
        )

    tm_catalog_runner: Callable[..., object] | None = None
    if args.tm_catalog_interval > 0:
        from scripts.temporal.gehi_common import GehiRateLimiter, make_throttled_runner

        # One process-wide limiter across every anchor worker: the TM 403 ban
        # keys on the sustained per-address-family request rate, not per-thread.
        tm_catalog_runner = make_throttled_runner(
            limiter=GehiRateLimiter(min_interval_s=args.tm_catalog_interval)
        )

    def handle(anchor: dict[str, str]) -> ScanState:
        anchor_id = anchor["anchor_id"]
        offline_tm_dates: list[str] | None = None
        if offline_tm_catalog is not None:
            offline_tm_dates = offline_tm_catalog.get(anchor_id)
            if offline_tm_dates is None:
                with print_lock:
                    print(
                        f"{marker} {anchor_id}: not in offline TM catalog; "
                        + (
                            "no_live_gehi is set -- will raise and record "
                            "done_ambiguous_orchestrator_error instead of a live fetch"
                            if args.no_live_gehi
                            else "falling back to live TM catalog fetch"
                        )
                    )
        offline_wayback_entries: list[tuple[str, str]] | None = None
        if offline_wayback_catalog is not None:
            offline_wayback_entries = offline_wayback_catalog.get(anchor_id)
            if offline_wayback_entries is None:
                with print_lock:
                    print(
                        f"{marker} {anchor_id}: not in offline Wayback catalog; "
                        + (
                            "no_live_gehi is set -- will raise and record "
                            "done_ambiguous_orchestrator_error instead of a live fetch"
                            if args.no_live_gehi
                            else "falling back to live Wayback catalog fetch"
                        )
                    )
        try:
            state = run_one_anchor(
                anchor,
                config,
                args.scan_states_dir,
                dry_run=args.dry_run,
                force_restart=args.force_restart,
                chips_dir=args.chips_dir,
                audit_dir=args.audit_dir,
                gemini_config=gemini_config,
                gemini_config_round1=gemini_config_round1,
                cheap_round_types=cheap_round_types,
                limiter=limiter,
                routing_salt_mode=args.routing_salt_mode,
                census_mid_date_iso=census_by_anchor[anchor_id],
                scorer=scorer,
                overwrite_chips=args.overwrite_chips,
                min_cache_zoom=args.min_cache_zoom,
                provenance_writer=provenance_writer,
                scoring_provenance_writer=scoring_provenance_writer,
                verdict_store=verdict_store,
                catalog_cache=catalog_cache,
                catalog_force_refresh=args.force_catalog_refresh,
                catalog_max_age_days=args.catalog_max_age_days,
                review_renderer=review_renderer,
                provider_chips_dirs=provider_chips_dirs,
                tm_catalog_runner=tm_catalog_runner,
                offline_tm_dates=offline_tm_dates,
                offline_wayback_entries=offline_wayback_entries,
                no_live_gehi=args.no_live_gehi,
            )
        except GeometryVersionMismatchError:
            # Never convert a geometry-epoch mismatch into a terminal state: that
            # would make the next resume silently skip mixed-geometry evidence.
            raise
        except Exception as exc:  # noqa: BLE001 - continue-on-error: record + keep batch running
            state = _record_orchestrator_failure(anchor, args.scan_states_dir, exc)
            with print_lock:
                print(f"{marker} {anchor_id}: ERROR status={state.status} reason={exc!r}")
        else:
            with print_lock:
                print(f"{marker} {anchor_id}: status={state.status} rounds={len(state.rounds)}")
        return state

    anchor_workers = max(1, args.anchor_workers)
    states: list[ScanState] = []
    if anchor_workers == 1:
        for anchor in anchors:
            states.append(handle(anchor))
    else:
        with ThreadPoolExecutor(max_workers=anchor_workers) as pool:
            futures = [pool.submit(handle, anchor) for anchor in anchors]
            for future in as_completed(futures):
                states.append(future.result())
    summarize(states)
    if catalog_cache is not None:
        print(f"[CFG] {catalog_cache.stats.summary()}")
    if verdict_store is not None:
        stats = verdict_store.stats_snapshot()
        print(
            "[CFG] verdict_store: "
            + " ".join(f"{k}={stats[k]}" for k in sorted(stats))
            + f" records={len(verdict_store)}"
        )
        verdict_store.close()

    # Continue-on-error means failed anchors were recorded and skipped, but a
    # partial batch must not exit 0. Surface failures and exit nonzero.
    exit_code = _exit_code_for_states(states)
    if exit_code != 0:
        failed_ids = sorted(
            s.anchor_id
            for s in states
            if s.status.startswith("done_ambiguous_orchestrator_error")
        )
        print(
            f"ERROR: {len(failed_ids)} anchor(s) failed: {', '.join(failed_ids)}",
            file=sys.stderr,
        )
        raise SystemExit(exit_code)


def _record_orchestrator_failure(
    anchor: dict[str, str], scan_states_dir: Path, exc: BaseException
) -> ScanState:
    """Persist a terminal scan_state when run_one_anchor raises, so the batch can continue.

    Reuses any pre-existing rounds (so partial progress is preserved) and tags
    the state as `done_ambiguous_orchestrator_error` with the exception summary
    in `notes`. Never re-raises — the batch loop owns continuation.
    """
    state_path = state_path_for(anchor["anchor_id"], scan_states_dir)
    state = load_scan_state(state_path)
    if state is None:
        state = create_scan_state(anchor)
    state.status = "done_ambiguous_orchestrator_error"
    state.next_action = None
    error_note = f"orchestrator_error: {type(exc).__name__}: {exc}"
    state.notes = (state.notes + " | " + error_note) if state.notes else error_note
    save_scan_state(state, state_path)
    return state


if __name__ == "__main__":
    main()
