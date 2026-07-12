#!/usr/bin/env python3
"""2023-census narrowing scan: one Gemini sequence call per cohort anchor.

For every anchor in ``build_census2023_cohort.py``'s output, assemble an ordered
frame sequence and ask Gemini, in a single call, which frames show PV:

    [ latest-absent TM frame ]  (cached, known absent)
    [ 2023 Esri Wayback frame(s), ascending ]   (downloaded here, the query)
    [ earliest-present TM frame ]  (cached, known present — appearance reference)

The absent/present anchor frames come straight from the original scan's cached
chips (``scan_state.json`` -> ``chip_path``); the 2023 frames are downloaded with
``download_chip_with_zoom_ladder(provider="Wayback", --exact-date)``. Every frame is
rendered with the SAME renderer the adaptive scan used (``ensure_review_png``,
centre ``+``), so a cohort 'c' (chip-group) and 't' (per-target) anchor are handled
identically — each chip is centred on its own anchor.

The result is applied with a strict **narrow-or-keep** rule (never widen, never drop):

    A = cohort latest_absent_date,  P = cohort install_interval_end (present-clamped)
    for the 2023 frames strictly inside (A, P):
        new_A = max(A, latest 2023 date read ABSENT)
        new_P = min(P, earliest 2023 date read PRESENT)
    apply only if the 2023 readings are monotonic AND (new_A, new_P) is a strict
    subset of (A, P); otherwise KEEP the clamped interval unchanged.

Output ``census_install_intervals.csv`` is merge-ready (carries the
install_interval_* / install_mid_estimate / earliest_present_date / confidence /
status fields ``merge_three_layers.date_record`` reads) plus census diagnostics.
Only rows with ``census_decision == narrowed`` should override the base date in the
L_census merge layer; kept rows fall through to gehi_main / gehi_pertarget.

Resume key = ``anchor_id`` (rows already in --output are skipped with --resume).
Model defaults to ``gemini-3-flash`` (the cheap present/absent tier); the env
default ``gemini-3-flash-agent`` is capable-tier and exhausts under load.
"""

# ruff: noqa: E402

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Mapping

if TYPE_CHECKING:  # pragma: no cover - typing only
    from scripts.temporal.verdict_store import VerdictStore

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.temporal.gehi_common import ensure_review_png
from scripts.temporal.gehi_download import build_chip_provenance, download_chip_with_zoom_ladder
from scripts.temporal.presence_scorer import PresenceScorer, get_scorer, validate_emission
from scripts.temporal.scan_config import AdaptiveScanConfig, load_config
from scripts.temporal.scan_state import load_scan_state
from scripts.temporal.score_target_sequence import RateLimiter
from scripts.validation.gemini_solar_image_review import (
    API_FORMATS,
    GeminiClientConfig,
    SequenceDatePick,
    env_value,
    load_env_file,
)

DEFAULT_MODEL = "gemini-3-flash"
# Wayback census-narrowing zoom ladder is single-sourced from the shared config
# (adaptive_scan.census2023_zoom_ladder); see resolve_census_zoom_ladder.
DEFAULT_CONFIG_YAML = PROJECT_ROOT / "configs" / "geid_anchor_presence.yaml"

CENSUS_FIELDS = [
    "anchor_id",
    "id_kind",
    "region_key",
    "grid_id",
    "status",                    # done_appears (merge-compatible) for narrowed/kept-dated rows
    # original (pre-census, present-clamped) bracket
    "orig_latest_absent_date",
    "orig_install_interval_end",
    # anchor exemplar frames actually shown to Gemini
    "absent_anchor_date",
    "present_anchor_date",
    # the 2023 census query + Gemini readings
    "wb_2023_dates",
    "wb_2023_readings",          # e.g. 2023-01-23:absent;2023-11-24:present
    "sequence_pattern",
    "consistency_flag",
    "gemini_quality_flag",
    "gemini_first_present_date",
    "gemini_confidence",
    # narrowing decision
    "census_decision",           # narrowed | kept_no_change | kept_nonmonotonic | kept_no_usable_2023 | kept_gemini_failed | kept_no_anchor_frame
    "narrowed",                  # 1 only when census actually tightened the bracket
    # merge-ready date fields (mirror install_intervals schema; merge.date_record reads these)
    "latest_absent_date",
    "earliest_present_date",
    "install_interval_start",
    "install_interval_end",
    "install_mid_estimate",
    "confidence",
    "date_provider",             # gehi_census2023
    # bookkeeping
    "n_2023_used",
    "n_2023_missing",
    # achieved-zoom histogram over this anchor's downloaded 2023 Wayback frames
    # (ISSUE-18 / D17), e.g. "18:1;19:2"; "unknown" buckets a null served zoom.
    "achieved_zoom_counts",
    "audit_path",
    "notes",
]


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------

def _parse_iso(value: str) -> date | None:
    value = (value or "").strip()
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _midpoint(start: date, end: date) -> date:
    return start + timedelta(days=(end - start).days // 2)


def _confidence_for_appears(gap_days: int) -> str:
    if gap_days <= 183:
        return "high"
    if gap_days <= 730:
        return "medium"
    return "low"


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8") as fh:
        return [dict(r) for r in csv.DictReader(fh)]


def _scan_state_path(anchor_id: str, id_kind: str, *, main_dir: Path, norecent_dir: Path) -> Path:
    base = norecent_dir if id_kind == "t" else main_dir
    return base / f"{anchor_id}.json"


def _anchor_frames(state) -> tuple[object | None, object | None]:
    """Return (latest_absent_result, earliest_present_result) from cached usable obs."""
    usable = [
        r
        for rnd in state.rounds
        for r in rnd.results
        if r.quality_flag == "usable" and r.pv_present is not None and (r.chip_path or "")
    ]
    absents = [r for r in usable if r.pv_present is False]
    presents = [r for r in usable if r.pv_present is True]
    latest_absent = max(absents, key=lambda r: r.capture_date) if absents else None
    earliest_present = min(presents, key=lambda r: r.capture_date) if presents else None
    return latest_absent, earliest_present


def _routing_salt(mode: str, model: str, anchor_id: str) -> str | None:
    if mode == "none":
        return None
    if mode == "auto" and "pro" not in (model or "").lower():
        return None
    return f"{model}:{anchor_id}:census2023"


def _format_zoom_counts(zooms: list[int | None]) -> str:
    """Render a per-anchor achieved-zoom histogram as ``"18:1;19:2"`` (sorted,
    CSV-safe). A null served zoom buckets as ``unknown``."""
    counts: dict[str, int] = {}
    for z in zooms:
        key = str(z) if z is not None else "unknown"
        counts[key] = counts.get(key, 0) + 1
    return ";".join(f"{k}:{counts[k]}" for k in sorted(counts))


def _parse_zoom_counts(value: object) -> dict[str, int]:
    """Inverse of ``_format_zoom_counts`` for the end-of-run aggregate summary."""
    out: dict[str, int] = {}
    for part in str(value or "").split(";"):
        part = part.strip()
        if not part or ":" not in part:
            continue
        key, raw = part.rsplit(":", 1)
        try:
            out[key] = out.get(key, 0) + int(raw)
        except ValueError:
            continue
    return out


# ---------------------------------------------------------------------------
# per-anchor work
# ---------------------------------------------------------------------------

class CensusJob:
    """Mutable per-anchor scratch built from a cohort row + cached scan_state."""

    def __init__(self, row: dict[str, str]) -> None:
        self.row = row
        self.anchor_id = row["anchor_id"].strip()
        self.id_kind = row.get("id_kind", "").strip()
        self.grid_id = row.get("grid_id", "").strip()
        self.region_key = row.get("region_key", "").strip()
        self.A = _parse_iso(row.get("latest_absent_date", ""))
        self.P = _parse_iso(row.get("install_interval_end", ""))
        self.wb_dates = [d for d in (_parse_iso(x) for x in row.get("wb_2023_dates", "").split(";")) if d]

    def anchor_dict(self) -> dict[str, str]:
        r = self.row
        return {
            "anchor_id": self.anchor_id,
            "region_key": self.region_key,
            "grid_id": self.grid_id,
            "centroid_lon": r.get("centroid_lon", ""),
            "centroid_lat": r.get("centroid_lat", ""),
            "chip_lon_min": r.get("chip_lon_min", ""),
            "chip_lat_min": r.get("chip_lat_min", ""),
            "chip_lon_max": r.get("chip_lon_max", ""),
            "chip_lat_max": r.get("chip_lat_max", ""),
        }


def _base_out(job: CensusJob) -> dict[str, object]:
    """Output row pre-filled with the KEEP (no-change) outcome."""
    A, P = job.A, job.P
    mid = _midpoint(A, P).isoformat() if (A and P and A <= P) else ""
    conf = _confidence_for_appears((P - A).days) if (A and P and A <= P) else "low"
    return {
        "anchor_id": job.anchor_id,
        "id_kind": job.id_kind,
        "region_key": job.region_key,
        "grid_id": job.grid_id,
        "status": "done_appears",
        "orig_latest_absent_date": job.row.get("latest_absent_date", ""),
        "orig_install_interval_end": job.row.get("install_interval_end", ""),
        "absent_anchor_date": "",
        "present_anchor_date": "",
        "wb_2023_dates": job.row.get("wb_2023_dates", ""),
        "wb_2023_readings": "",
        "sequence_pattern": "",
        "consistency_flag": "",
        "gemini_quality_flag": "",
        "gemini_first_present_date": "",
        "gemini_confidence": "",
        "census_decision": "kept_no_change",
        "narrowed": "0",
        "latest_absent_date": job.row.get("latest_absent_date", ""),
        "earliest_present_date": job.row.get("install_interval_end", ""),
        "install_interval_start": job.row.get("latest_absent_date", ""),
        "install_interval_end": job.row.get("install_interval_end", ""),
        "install_mid_estimate": mid,
        "confidence": conf,
        "date_provider": "gehi_census2023",
        "n_2023_used": 0,
        "n_2023_missing": 0,
        "achieved_zoom_counts": "",
        "audit_path": "",
        "notes": "",
    }


def run_one_anchor(
    job: CensusJob,
    *,
    main_dir: Path,
    norecent_dir: Path,
    census_chips_dir: Path,
    audit_dir: Path | None,
    config: GeminiClientConfig,
    zoom_ladder: tuple[int, ...],
    max_tokens: int | None,
    routing_salt_mode: str,
    limiter: RateLimiter,
    scorer: PresenceScorer,
    overwrite_chips: bool = False,
    min_cache_zoom: int | None = None,
    provenance_writer: Callable[[Mapping[str, object]], None] | None = None,
    scoring_provenance_writer: Callable[[Mapping[str, object]], None] | None = None,
    verdict_store: VerdictStore | None = None,
) -> dict[str, object]:
    out = _base_out(job)
    if job.A is None or job.P is None or job.A > job.P:
        out["census_decision"] = "kept_no_anchor_frame"
        out["notes"] = "invalid cohort bracket"
        return out

    state_path = _scan_state_path(job.anchor_id, job.id_kind, main_dir=main_dir, norecent_dir=norecent_dir)
    if not state_path.exists():
        out["census_decision"] = "kept_no_anchor_frame"
        out["notes"] = f"scan_state not found: {state_path}"
        return out
    state = load_scan_state(state_path)
    absent_res, present_res = _anchor_frames(state)
    if absent_res is None or present_res is None:
        out["census_decision"] = "kept_no_anchor_frame"
        out["notes"] = "missing cached absent/present anchor frame"
        return out

    # ISSUE-07 verdict store: consult the content-addressed store before the
    # per-anchor sequence call. Wrapped BEFORE the provenance sidecar so cache
    # hits still emit sidecar rows (provenance outermost).
    if verdict_store is not None:
        from scripts.temporal.verdict_store import with_verdict_store

        scorer = with_verdict_store(scorer, verdict_store)
    # ISSUE-06 scoring-provenance sidecar: wrap the injected scorer so the single
    # per-anchor sequence call emits one provenance row per scored frame, stamped
    # with this anchor. Wrapper delegates failure_decision_sources verbatim, so
    # the kept_gemini_failed gate below is unaffected.
    if scoring_provenance_writer is not None:
        from scripts.temporal.scoring_provenance import with_scoring_provenance

        scorer = with_scoring_provenance(
            scorer, scoring_provenance_writer, context={"anchor_id": job.anchor_id}
        )

    # download the 2023 Wayback frames strictly inside the bracket
    anchor = job.anchor_dict()
    wb_frames: list[tuple[date, Path, int | None]] = []  # (date, png_path, zoom)
    achieved_zooms: list[int | None] = []
    n_missing = 0
    escape_kwargs: dict[str, object] = {}
    if overwrite_chips:
        escape_kwargs["overwrite"] = True
    if min_cache_zoom is not None:
        escape_kwargs["min_cache_zoom"] = min_cache_zoom
    for d in sorted(job.wb_dates):
        if not (job.A < d < job.P):
            continue
        outcome = download_chip_with_zoom_ladder(
            anchor,
            capture_date=d.isoformat(),
            version="wb",
            zoom_ladder=zoom_ladder,
            output_root=census_chips_dir,
            provider="Wayback",
            allow_nearest=False,
            **escape_kwargs,
        )
        if provenance_writer is not None:
            provenance_writer(build_chip_provenance(outcome, anchor, "Wayback"))
        if outcome.status not in ("ok", "skipped_existing") or outcome.path is None:
            n_missing += 1
            continue
        achieved_zooms.append(outcome.actual_zoom)
        try:
            png = ensure_review_png(Path(outcome.path))
        except Exception as exc:  # noqa: BLE001
            n_missing += 1
            out["notes"] = f"{out['notes']} | render_fail {d}: {type(exc).__name__}".strip(" |")
            continue
        wb_frames.append((d, png, outcome.actual_zoom))

    out["n_2023_missing"] = n_missing
    out["achieved_zoom_counts"] = _format_zoom_counts(achieved_zooms)
    if not wb_frames:
        out["census_decision"] = "kept_no_usable_2023"
        out["notes"] = (f"{out['notes']} | no usable 2023 frame downloaded").strip(" |")
        return out

    # render the cached anchor exemplars
    try:
        absent_png = ensure_review_png(Path(absent_res.chip_path))
        present_png = ensure_review_png(Path(present_res.chip_path))
    except Exception as exc:  # noqa: BLE001
        out["census_decision"] = "kept_no_anchor_frame"
        out["notes"] = f"anchor render fail: {type(exc).__name__}: {exc}"
        return out

    out["absent_anchor_date"] = absent_res.capture_date[:10]
    out["present_anchor_date"] = present_res.capture_date[:10]
    out["n_2023_used"] = len(wb_frames)

    # ordered picks: absent anchor, 2023 frames ascending, present anchor
    picks: list[SequenceDatePick] = []
    picks.append(SequenceDatePick(date_index=1, chip_path=absent_png,
                                  capture_date=absent_res.capture_date[:10], version="tm",
                                  actual_zoom=absent_res.actual_zoom))
    for i, (d, png, zoom) in enumerate(wb_frames, start=2):
        picks.append(SequenceDatePick(date_index=i, chip_path=png, capture_date=d.isoformat(),
                                      version="wb", actual_zoom=zoom))
    picks.append(SequenceDatePick(date_index=len(picks) + 1, chip_path=present_png,
                                  capture_date=present_res.capture_date[:10], version="tm",
                                  actual_zoom=present_res.actual_zoom))

    audit_writer = None
    audit_path = None
    if audit_dir is not None:
        audit_path = audit_dir / f"{job.anchor_id}.jsonl"
        audit_path.parent.mkdir(parents=True, exist_ok=True)
        fh = audit_path.open("a", encoding="utf-8")

        def audit_writer(payload: dict[str, Any]) -> None:  # noqa: ANN001
            rec = dict(payload)
            rec["anchor_id"] = job.anchor_id
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

        out["audit_path"] = str(audit_path)

    salt = _routing_salt(routing_salt_mode, config.model, job.anchor_id)
    limiter.wait()
    try:
        result = scorer.sequence(
            picks, config=config, audit_writer=audit_writer, max_tokens=max_tokens,
            routing_salt=salt,
        )
    finally:
        if audit_dir is not None:
            fh.close()

    validate_emission(result.quality_flag, result.decision_source)
    out["sequence_pattern"] = result.sequence_pattern
    out["consistency_flag"] = result.consistency_flag
    out["gemini_quality_flag"] = result.quality_flag
    out["gemini_first_present_date"] = result.first_present_date or ""
    out["gemini_confidence"] = "" if result.confidence is None else f"{result.confidence:.4f}"

    # map 2023 dates -> reading
    obs_by_date = {o.capture_date[:10]: o for o in result.observations}
    readings: list[str] = []
    present_2023: list[date] = []
    absent_2023: list[date] = []
    n_unknown = 0
    for d, _, _ in wb_frames:
        o = obs_by_date.get(d.isoformat())
        if o is None or o.pv_present is None:
            readings.append(f"{d.isoformat()}:unknown")
            n_unknown += 1
            continue
        if o.pv_present:
            readings.append(f"{d.isoformat()}:present")
            present_2023.append(d)
        else:
            readings.append(f"{d.isoformat()}:absent")
            absent_2023.append(d)
    out["wb_2023_readings"] = ";".join(readings)

    if result.decision_source in scorer.failure_decision_sources:
        out["census_decision"] = "kept_gemini_failed"
        return out

    # Gemini's own whole-sequence verdict (includes the anchor frames).
    if result.consistency_flag == "non_monotonic_requires_review":
        out["census_decision"] = "kept_nonmonotonic"
        return out

    # Exemplar-agreement gate: the census read must reproduce the KNOWN anchor
    # labels (absent anchor -> absent, present anchor -> present). When it does not
    # (cross-source / cross-batch inconsistency, or a washed-out phantom present
    # frame), the 2023 readings for this roof are not trustworthy -> keep + flag.
    absent_obs = obs_by_date.get(absent_res.capture_date[:10])
    present_obs = obs_by_date.get(present_res.capture_date[:10])
    absent_anchor_ok = absent_obs is not None and absent_obs.pv_present is False
    present_anchor_ok = present_obs is not None and present_obs.pv_present is True
    if not (absent_anchor_ok and present_anchor_ok):
        out["census_decision"] = "kept_exemplar_disagreement"
        out["notes"] = (
            f"{out['notes']} | exemplar disagreement: absent_anchor_read="
            f"{'?' if absent_obs is None else absent_obs.pv_present}, "
            f"present_anchor_read={'?' if present_obs is None else present_obs.pv_present}"
        ).strip(" |")
        return out

    if not present_2023 and not absent_2023:
        out["census_decision"] = "kept_no_usable_2023"
        return out

    # monotonicity over the 2023 readings: every absent must precede every present
    if present_2023 and absent_2023 and max(absent_2023) >= min(present_2023):
        out["census_decision"] = "kept_nonmonotonic"
        return out

    A, P = job.A, job.P
    new_A = max([A] + [d for d in absent_2023 if A < d < P])
    new_P = min([P] + [d for d in present_2023 if A < d < P])
    if new_A < new_P and (new_A > A or new_P < P):
        mid = _midpoint(new_A, new_P)
        out["census_decision"] = "narrowed"
        out["narrowed"] = "1"
        out["latest_absent_date"] = new_A.isoformat()
        out["earliest_present_date"] = new_P.isoformat()
        out["install_interval_start"] = new_A.isoformat()
        out["install_interval_end"] = new_P.isoformat()
        out["install_mid_estimate"] = mid.isoformat()
        out["confidence"] = _confidence_for_appears((new_P - new_A).days)
    else:
        out["census_decision"] = "kept_no_change"
    return out


# ---------------------------------------------------------------------------
# driver
# ---------------------------------------------------------------------------

def _atomic_write(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=CENSUS_FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in CENSUS_FIELDS})
    tmp.replace(path)


def _default_env_file() -> Path:
    local = PROJECT_ROOT / ".env.gemini.local"
    if local.exists():
        return local
    zasolar_root = Path(os.environ.get("ZASOLAR_ROOT", "/home/gao/projects/ZAsolar"))
    main_local = zasolar_root / ".env.gemini.local"
    return main_local if main_local.exists() else local


def _load_config(args: argparse.Namespace) -> GeminiClientConfig:
    env = load_env_file(args.env_file)
    base_url = args.base_url or env_value(env, "GOOGLE_GEMINI_BASE_URL")
    api_key = args.api_key or env_value(env, "GEMINI_API_KEY")
    model = args.model or DEFAULT_MODEL
    api_format = args.api_format or env_value(env, "GEMINI_API_FORMAT", "native")
    native_path = args.native_path or env_value(env, "GEMINI_NATIVE_PATH", "/v1beta")
    timeout = args.timeout or int(env_value(env, "GEMINI_TIMEOUT", "120"))
    if not base_url:
        raise SystemExit(f"Missing GOOGLE_GEMINI_BASE_URL (set in {args.env_file} or pass --base-url)")
    if not api_key:
        raise SystemExit(f"Missing GEMINI_API_KEY (set in {args.env_file} or pass --api-key)")
    if api_format not in API_FORMATS:
        raise SystemExit(f"Unsupported API format {api_format!r}; choose {sorted(API_FORMATS)}")
    return GeminiClientConfig(
        base_url=base_url, api_key=api_key, model=model,
        api_format=api_format, native_path=native_path, timeout=timeout,
    )


def resolve_census_zoom_ladder(
    cli_zoom_ladder: str | None, config: AdaptiveScanConfig
) -> tuple[int, ...]:
    """Resolve the Wayback census-narrowing zoom ladder.

    Single source of truth: ``config.census2023_zoom_ladder`` (from the shared
    ``adaptive_scan:`` YAML section) governs, unless an explicit ``--zoom-ladder``
    CLI value is given, which wins. A blank/whitespace CLI value falls back to the
    config value. Diverges from ``download_zoom_ladder`` because Wayback has almost
    no z20 (see the YAML comment on ``census2023_zoom_ladder``).
    """
    if cli_zoom_ladder is not None and str(cli_zoom_ladder).strip():
        return tuple(int(z) for z in str(cli_zoom_ladder).split(",") if z.strip())
    return tuple(config.census2023_zoom_ladder)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--cohort-csv", type=Path, required=True, help="build_census2023_cohort.py output.")
    p.add_argument("--main-scan-states-dir", type=Path, required=True)
    p.add_argument("--norecent-scan-states-dir", type=Path, required=True)
    p.add_argument("--census-chips-dir", type=Path, required=True, help="where 2023 Wayback chips are downloaded.")
    p.add_argument("--audit-dir", type=Path, default=None)
    p.add_argument("--no-audit", action="store_true")
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--env-file", type=Path, default=_default_env_file())
    p.add_argument("--base-url")
    p.add_argument("--api-key")
    p.add_argument("--model", default=DEFAULT_MODEL, help=f"Default {DEFAULT_MODEL} (cheap present/absent tier).")
    p.add_argument("--api-format", choices=sorted(API_FORMATS))
    p.add_argument("--native-path")
    p.add_argument("--timeout", type=int)
    p.add_argument("--max-tokens", type=int, default=8192,
                   help="maxOutputTokens. gemini-3-flash spends thinking tokens against this "
                   "budget, so it must be generous (1024 truncates the JSON mid-output). Default 8192.")
    p.add_argument("--workers", type=int, default=1)
    p.add_argument("--qps", type=float, default=0.0)
    p.add_argument("--routing-salt-mode", choices=("none", "auto", "target"), default="none")
    p.add_argument("--config", type=Path, default=DEFAULT_CONFIG_YAML,
                   help="Shared scan config; supplies census2023_zoom_ladder when --zoom-ladder omitted.")
    p.add_argument("--zoom-ladder", default=None,
                   help="Comma-separated Wayback zoom ladder. Overrides "
                   "adaptive_scan.census2023_zoom_ladder from --config; default (19,18) comes from that config.")
    p.add_argument("--limit", type=int, default=None, help="Process only the first N cohort rows (pilot).")
    p.add_argument("--resume", action="store_true", help="Skip anchors already present in --output.")
    p.add_argument("--flush-every", type=int, default=50, help="Atomic-write the output every N completed anchors.")
    p.add_argument("--scorer", default="gemini",
                   help="Registered PresenceScorer to route scoring through (default gemini; "
                   "'dinov3_frozen' selects the self-hosted frozen DINOv3-L-SAT backbone, ISSUE-03).")
    p.add_argument("--overwrite-chips", action="store_true",
                   help="Re-download every 2023 Wayback chip, bypassing the skip-existing cache "
                   "(ISSUE-18 escape hatch). Default: off.")
    p.add_argument("--verdict-store", type=Path, default=None,
                   help="Content-addressed verdict store JSONL (ISSUE-07). Default: "
                   "<output parent>/verdict_store.jsonl. Identical re-runs replay cached "
                   "sequence verdicts and issue zero scorer calls for already-seen windows.")
    p.add_argument("--no-verdict-store", action="store_true",
                   help="Disable the ISSUE-07 verdict store (every window is re-scored).")
    p.add_argument("--min-cache-zoom", type=int, default=None,
                   help="Refuse cached chips below this zoom so the ladder re-fetches and upgrades "
                   "them (ISSUE-18 escape hatch; cache acceptance only). Default: off.")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    if not args.cohort_csv.exists():
        raise SystemExit(f"cohort CSV not found: {args.cohort_csv}")
    scan_cfg = load_config(args.config)
    zoom_ladder = resolve_census_zoom_ladder(args.zoom_ladder, scan_cfg)
    config = _load_config(args)
    scorer = get_scorer(args.scorer)

    cohort = _read_csv(args.cohort_csv)
    if args.limit is not None:
        cohort = cohort[: args.limit]

    done_rows: list[dict[str, object]] = []
    done_ids: set[str] = set()
    if args.resume and args.output.exists():
        for r in _read_csv(args.output):
            done_rows.append(r)
            done_ids.add(str(r.get("anchor_id", "")))
        print(f"[resume] {len(done_ids)} anchors already in {args.output}")

    jobs = [CensusJob(r) for r in cohort if r.get("anchor_id", "").strip() not in done_ids]
    print(f"[census] {len(jobs)} anchors to scan (cohort {len(cohort)}, skipped {len(cohort) - len(jobs)})")

    audit_dir = None if args.no_audit else (args.audit_dir or (args.output.parent / "census_audit"))
    limiter = RateLimiter(args.qps)
    results: list[dict[str, object]] = list(done_rows)
    lock = threading.Lock()
    n_done = 0

    # Per-chip provenance sidecar (ISSUE-18 / D17): one JSONL record per Wayback
    # download outcome, written next to --output. Thread-safe: the census runs a
    # thread pool, so a lock guards the append.
    provenance_path = args.output.parent / "chip_provenance.jsonl"
    provenance_path.parent.mkdir(parents=True, exist_ok=True)
    provenance_lock = threading.Lock()

    def provenance_writer(record: Mapping[str, object]) -> None:
        line = json.dumps(record, ensure_ascii=False)
        with provenance_lock, provenance_path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    # Scoring-provenance sidecar (ISSUE-06 / D5): one JSONL row per scored frame,
    # written next to --output. Thread-safe append (its own lock).
    from scripts.temporal.scoring_provenance import jsonl_writer as _scoring_jsonl_writer

    scoring_provenance_writer = _scoring_jsonl_writer(args.output.parent / "scoring_provenance.jsonl")

    # ISSUE-07: one VerdictStore shared across census workers (thread-safe;
    # single-writer across processes). Wrapped inside run_one_anchor, under the
    # provenance sidecar, so cache hits still emit sidecar rows.
    verdict_store = None
    if not args.no_verdict_store:
        from scripts.temporal.verdict_store import VerdictStore

        store_path = args.verdict_store or (args.output.parent / "verdict_store.jsonl")
        verdict_store = VerdictStore(store_path)
        print(f"[census] verdict_store={store_path} records={len(verdict_store)}")

    def work(job: CensusJob) -> dict[str, object]:
        return run_one_anchor(
            job, main_dir=args.main_scan_states_dir, norecent_dir=args.norecent_scan_states_dir,
            census_chips_dir=args.census_chips_dir, audit_dir=audit_dir, config=config,
            zoom_ladder=zoom_ladder, max_tokens=args.max_tokens,
            routing_salt_mode=args.routing_salt_mode, limiter=limiter, scorer=scorer,
            overwrite_chips=args.overwrite_chips, min_cache_zoom=args.min_cache_zoom,
            provenance_writer=provenance_writer,
            scoring_provenance_writer=scoring_provenance_writer,
            verdict_store=verdict_store,
        )

    def record(res: dict[str, object]) -> None:
        nonlocal n_done
        with lock:
            results.append(res)
            n_done += 1
            if n_done % args.flush_every == 0:
                _atomic_write(args.output, results)
                print(f"[census] {n_done}/{len(jobs)} done (flushed)")

    if args.workers <= 1:
        for job in jobs:
            record(work(job))
    else:
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futs = {pool.submit(work, job): job for job in jobs}
            for fut in as_completed(futs):
                try:
                    record(fut.result())
                except Exception as exc:  # noqa: BLE001 - never drop an anchor silently
                    job = futs[fut]
                    err = _base_out(job)
                    err["census_decision"] = "kept_gemini_failed"
                    err["notes"] = f"unhandled: {type(exc).__name__}: {exc}"
                    record(err)

    _atomic_write(args.output, results)

    # summary
    by_dec: dict[str, int] = {}
    for r in results:
        by_dec[str(r.get("census_decision", ""))] = by_dec.get(str(r.get("census_decision", "")), 0) + 1
    n_narrowed = by_dec.get("narrowed", 0)
    print(f"\n[census] wrote {len(results)} rows -> {args.output}")
    for k, v in sorted(by_dec.items(), key=lambda kv: -kv[1]):
        print(f"  {k}: {v}")
    print(f"[census] narrowed {n_narrowed} anchors")
    if verdict_store is not None:
        stats = verdict_store.stats_snapshot()
        print(
            "[census] verdict_store: "
            + " ".join(f"{k}={stats[k]}" for k in sorted(stats))
            + f" records={len(verdict_store)}"
        )
        verdict_store.close()

    # Aggregated achieved-zoom distribution over every downloaded 2023 Wayback
    # frame (ISSUE-18 / D17); re-summed from each row's achieved_zoom_counts so
    # resumed rows are included. Wayback is empirically ~97% z19 — surfacing it
    # here means it no longer hides in raw scan-state JSON.
    zoom_totals: dict[str, int] = {}
    for r in results:
        for key, count in _parse_zoom_counts(r.get("achieved_zoom_counts", "")).items():
            zoom_totals[key] = zoom_totals.get(key, 0) + count
    total_frames = sum(zoom_totals.values())
    print("[census] achieved-zoom distribution (2023 Wayback frames):")
    if total_frames == 0:
        print("  (no frames downloaded)")
    else:
        for key in sorted(zoom_totals):
            n = zoom_totals[key]
            label = f"z{key}" if key != "unknown" else "unknown"
            print(f"  {label}: {n} ({100.0 * n / total_frames:.1f}%)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
