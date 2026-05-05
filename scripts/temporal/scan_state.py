"""Scan state persistence for the adaptive PV-presence scan orchestrator.

A `ScanState` is the per-anchor checkpoint for `run_adaptive_scan.py`. It records
the rounds the orchestrator has executed, the picks it scored in each round, and
the terminal status (or `scanning` while in flight). The orchestrator writes the
state atomically after every round so the loop is resumable.

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

SPEC_VERSION = "phase0_v2"

ROUND_TYPES = {"initial", "walk_back", "bisection", "tail", "anchor_recovery"}

TERMINAL_STATUSES = {
    "done_appears",
    "done_installed_during_census",
    "done_already_present_before_geid_history",
    "done_ambiguous_nonmonotonic",
    "done_ambiguous_gemini_failed",
    "done_ambiguous_no_recent_anchor",
    "done_ambiguous_orchestrator_error",
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
    version: int
    requested_zoom: int


@dataclass
class RoundResult:
    chip_index: int
    capture_date: str
    version: int
    pv_present: bool | None
    confidence: float | None
    quality_flag: str
    decision_source: str
    evidence: str = ""
    notes: str = ""
    chip_path: str = ""
    actual_zoom: int | None = None


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
    return ScanState(
        anchor_id=str(anchor["anchor_id"]),
        region_key=str(anchor["region_key"]),
        grid_id=str(anchor["grid_id"]),
        started_at=ts,
        updated_at=ts,
    )


def state_path_for(anchor_id: str, scan_states_dir: Path) -> Path:
    return scan_states_dir / f"{anchor_id}.json"


def load_scan_state(path: Path) -> ScanState | None:
    if not path.exists():
        return None
    raw = json.loads(path.read_text(encoding="utf-8"))
    spec = raw.get("spec_version")
    if spec != SPEC_VERSION:
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
    )


def save_scan_state(state: ScanState, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    state.updated_at = now_iso()
    payload = asdict(state)
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
