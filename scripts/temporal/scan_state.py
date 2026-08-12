"""Scan state persistence for the adaptive PV-presence scan orchestrator.

A `ScanState` is the per-anchor checkpoint for `run_adaptive_scan.py`. It records
the rounds the orchestrator has executed, the picks it scored in each round, and
the terminal status (or `scanning` while in flight), plus the resolved census
date and per-anchor catalog cutoff that governed planning. The orchestrator
writes the state atomically after every round so the loop is resumable.

The schema is intentionally narrow: dataclasses serialize to JSON via
`asdict()`. Bumping `SPEC_VERSION` is the only forward-compatibility lever; old
state files with a mismatched version are refused, not silently migrated.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from scripts.temporal import presence_scorer

SPEC_VERSION = "phase0_v3_reference_only"
LEGACY_SPEC_VERSIONS = frozenset({"phase0_v2"})
LEGACY_V1_GEOMETRY_VERSION = "legacy-v1"

ROUND_TYPES = {"initial", "walk_back", "bisection", "tail", "anchor_recovery"}

TERMINAL_STATUSES = {
    "done_appears",
    "done_installed_during_census",
    "done_already_present_before_geid_history",
    "done_ambiguous_nonmonotonic",
    "done_ambiguous_gemini_failed",
    "done_ambiguous_no_recent_anchor",
    "done_ambiguous_orchestrator_error",
    # Post-hoc status assigned in inference layer when scan_state.status =
    # done_installed_during_census but latest_absent_date >= census_imagery_mid_date,
    # contradicting the GT prior that the anchor is PV-positive at census time.
    # Means: marker likely fell on a roof aisle / shadow / wrong segment and
    # missed the PV — needs human review.
    "done_ambiguous_marker_missed_pv",
}
ALL_STATUSES = {"scanning", *TERMINAL_STATUSES}

QUALITY_FLAGS = {"usable", "ambiguous", "unusable"}
DECISION_SOURCES = {
    "gemini_batch",
    "gemini_per_image",
    "gemini_failed",
    "dry_run_stub",
    "manual",
}


@dataclass
class Pick:
    chip_index: int
    capture_date: str
    # int for live TM/Wayback catalogs; the literal "noversion" for offline-TM
    # catalog picks so chip paths line up with the pre-downloaded basemap.
    version: int | str
    requested_zoom: int
    provider: str = "TM"
    reference_only: bool = False


@dataclass
class RoundResult:
    chip_index: int
    capture_date: str
    version: int | str
    pv_present: bool | None
    confidence: float | None
    quality_flag: str
    decision_source: str
    evidence: str = ""
    notes: str = ""
    chip_path: str = ""
    actual_zoom: int | None = None
    provider: str = "TM"
    reference_only: bool = False


@dataclass
class Round:
    round_id: int
    round_type: str
    window_start_date: str | None
    window_end_date: str | None
    picks: list[Pick] = field(default_factory=list)
    results: list[RoundResult] = field(default_factory=list)
    completed: bool = False
    failed: bool = False
    notes: str = ""

    def __post_init__(self) -> None:
        if self.round_type not in ROUND_TYPES:
            raise ValueError(f"round_type must be one of {ROUND_TYPES}, got {self.round_type!r}")


@dataclass
class ScanState:
    anchor_id: str
    region_key: str
    grid_id: str
    status: str = "scanning"
    rounds: list[Round] = field(default_factory=list)
    next_action: str | None = None
    started_at: str = ""
    updated_at: str = ""
    spec_version: str = SPEC_VERSION
    notes: str = ""
    census_date: str | None = None
    catalog_max_date: str | None = None
    post_census_reference_frames: int | None = None
    geometry_version: str | None = None

    def __post_init__(self) -> None:
        if self.status not in ALL_STATUSES:
            raise ValueError(f"status must be one of {ALL_STATUSES}, got {self.status!r}")

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES

    def usable_observations(self) -> list[RoundResult]:
        return [
            r
            for rnd in self.rounds
            for r in rnd.results
            if r.quality_flag == "usable" and r.pv_present is not None
        ]


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def create_scan_state(anchor: dict[str, Any]) -> ScanState:
    ts = now_iso()
    # Frozen CT v1 anchor manifests use ``source_grid`` as the provenance
    # column; older adaptive-scan manifests called the same value ``grid_id``.
    # Prefer the legacy key when present for byte-compatible existing callers,
    # then adapt the CT schema at the state boundary.
    grid_id = anchor.get("grid_id") or anchor.get("source_grid") or anchor.get("centroid_grid")
    if not grid_id:
        raise KeyError("anchor has no grid_id/source_grid/centroid_grid")
    return ScanState(
        anchor_id=str(anchor["anchor_id"]),
        region_key=str(anchor["region_key"]),
        grid_id=str(grid_id),
        started_at=ts,
        updated_at=ts,
        geometry_version=str(
            anchor.get("geometry_version") or LEGACY_V1_GEOMETRY_VERSION
        ),
    )


def state_path_for(anchor_id: str, scan_states_dir: Path) -> Path:
    return scan_states_dir / f"{anchor_id}.json"


def load_scan_state(path: Path, *, allow_legacy: bool = True) -> ScanState | None:
    if not path.exists():
        return None
    raw = json.loads(path.read_text(encoding="utf-8"))
    spec = raw.get("spec_version")
    if spec != SPEC_VERSION and not (allow_legacy and spec in LEGACY_SPEC_VERSIONS):
        raise ValueError(
            f"scan_state spec_version mismatch at {path}: file={spec!r} expected={SPEC_VERSION!r}"
        )
    rounds = [
        Round(
            round_id=int(r["round_id"]),
            round_type=str(r["round_type"]),
            window_start_date=r.get("window_start_date"),
            window_end_date=r.get("window_end_date"),
            picks=[Pick(**p) for p in r.get("picks", [])],
            results=[RoundResult(**res) for res in r.get("results", [])],
            completed=bool(r.get("completed", False)),
            failed=bool(r.get("failed", False)),
            notes=str(r.get("notes", "")),
        )
        for r in raw.get("rounds", [])
    ]
    return ScanState(
        anchor_id=str(raw["anchor_id"]),
        region_key=str(raw["region_key"]),
        grid_id=str(raw["grid_id"]),
        status=str(raw.get("status", "scanning")),
        rounds=rounds,
        next_action=raw.get("next_action"),
        started_at=str(raw.get("started_at", "")),
        updated_at=str(raw.get("updated_at", "")),
        spec_version=str(raw.get("spec_version", SPEC_VERSION)),
        notes=str(raw.get("notes", "")),
        census_date=raw.get("census_date"),
        catalog_max_date=raw.get("catalog_max_date"),
        post_census_reference_frames=(
            int(raw["post_census_reference_frames"])
            if raw.get("post_census_reference_frames") is not None
            else None
        ),
        geometry_version=(
            str(raw["geometry_version"])
            if raw.get("geometry_version") is not None
            else None
        ),
    )


def _validate_state_vocab(state: ScanState) -> None:
    """Reject unregistered quality_flag / decision_source values at write time.

    The single choke-point where adaptive-scan RoundResults are persisted. Values
    are checked against the additive registries in `presence_scorer`; register
    new scorer vocabulary via `presence_scorer.register_quality_flag` /
    `register_decision_source` before saving states that carry it.
    """
    for rnd in state.rounds:
        for res in rnd.results:
            presence_scorer.validate_quality_flag(res.quality_flag)
            presence_scorer.validate_decision_source(res.decision_source)


def save_scan_state(state: ScanState, path: Path) -> None:
    _validate_state_vocab(state)
    path.parent.mkdir(parents=True, exist_ok=True)
    state.updated_at = now_iso()
    payload = asdict(state)
    # Provider metadata is additive for merged-source scans. Omit the historical
    # TM default so ordinary scan-state bytes and downstream fixtures remain
    # unchanged; non-TM picks/results retain the explicit provider on disk.
    for rnd in payload.get("rounds", []):
        for pick in rnd.get("picks", []):
            if pick.get("provider") == "TM":
                pick.pop("provider", None)
            if pick.get("reference_only") is False:
                pick.pop("reference_only", None)
        for result in rnd.get("results", []):
            if result.get("provider") == "TM":
                result.pop("provider", None)
            if result.get("reference_only") is False:
                result.pop("reference_only", None)
    fd, tmp_path = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, ensure_ascii=False, sort_keys=False)
        os.replace(tmp_path, path)
    except Exception:
        Path(tmp_path).unlink(missing_ok=True)
        raise


def append_round(state: ScanState, rnd: Round) -> None:
    state.rounds.append(rnd)


def find_round(state: ScanState, round_id: int) -> Round | None:
    for rnd in state.rounds:
        if rnd.round_id == round_id:
            return rnd
    return None


def next_round_id(state: ScanState) -> int:
    if not state.rounds:
        return 1
    return max(r.round_id for r in state.rounds) + 1
