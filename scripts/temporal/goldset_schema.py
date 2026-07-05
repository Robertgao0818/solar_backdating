#!/usr/bin/env python3
"""Shared schema + path + I/O scaffold for the ISSUE-10 gold set.

This module is the ONE file the three ISSUE-10 work packages share. It owns
every field name, vocabulary constant, on-disk path, and low-level (de)serializer
so the WPs only import — they never edit it and never re-declare a field name.

Three artifacts flow through here:

1. **Sample assignment** (CSV) — the stratified sampler (WP-B) writes one row per
   sampled anchor; the strip builder (WP-A) and re-render tool (WP-C) both read it.
2. **Verdict manifest** (JSONL) — the offline HTML (WP-A) exports one record per
   adjudicated anchor; the loader (WP-A) round-trips it back to `VerdictRecord`s;
   ISSUE-11 joins it to pipeline records by `(anchor_id, capture_date, version)`.
3. **Frame identity** — the exact rendered frames a verdict is about, embedded in
   each verdict record so ISSUE-11 can attribute a judgment to specific pixels.

Versioning discipline mirrors the rest of the repo (`STORE_RECORD_VERSION`,
`SPEC_VERSION`): bump `SCHEMA_VERSION` rather than silently changing meaning;
extend vocabulary tuples additively.
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

SCHEMA_VERSION = 1

# ---------------------------------------------------------------------------
# Vocabulary (additive tuples — append, never repurpose)

#: Human adjudication outcome for the jump critical point. Distinct semantic
#: axis from scorer `decision_source`; never overloaded onto that registry.
VERDICTS: tuple[str, ...] = ("CONFIRM", "SHIFT", "UNDATABLE")

#: Annotator slots for the 20% double-annotation overlap.
ANNOTATORS: tuple[str, ...] = ("A", "B")

#: Provenance of each frame drawn on the strip. `scan_tm` = the anchor's own
#: adaptive-scan Vexcel/TM chip (latest-absent / earliest-present / flank);
#: `coj_YYYY` = municipal true-date aerial; `wayback` = in-window Wayback capture.
FRAME_SOURCES: tuple[str, ...] = ("scan_tm", "coj_2015", "coj_2019", "coj_2023", "wayback")

#: Role a frame plays in the jump-window layout.
FRAME_ROLES: tuple[str, ...] = (
    "latest_absent",
    "earliest_present",
    "flank_before",
    "flank_after",
    "reference",  # CoJ / Wayback overlay, not part of the pipeline bracket
)

# ---------------------------------------------------------------------------
# CSV field order — the sampler → builder/re-render handoff contract

SAMPLE_ASSIGNMENT_FIELDS: tuple[str, ...] = (
    "anchor_id",
    "grid_id",
    "stratum",              # cohort stratum when available, else "status_x_conf" fallback
    "terminal_status",
    "confidence",
    "any_contradiction",    # "True"/"False"/"" (empty = flag unavailable)
    "pipeline_interval_start",
    "pipeline_interval_end",
    "latest_absent_date",
    "earliest_present_date",
    "scan_state_path",
    "annotator_id",         # A or B
    "is_overlap",           # True for the double-annotated 20%
    "is_dispute_forced",    # True = owns >=1 of the 15 dated-vs-undated disputes
    "dispute_target_ids",   # ";"-joined short target ids this c-anchor covers ("" if none)
    "sampler_seed",
    "sample_batch_id",
)


@dataclass
class FrameIdentity:
    """One rendered frame's pixel-identity + role, carried inside a verdict."""

    source: str          # one of FRAME_SOURCES
    role: str            # one of FRAME_ROLES
    capture_date: str    # ISO date the imagery was captured ("" for undated municipal)
    version: int | None  # scan-slot version (None for CoJ/Wayback with no scan version)
    chip_path: str       # re-rendered PNG/TIF path used in the strip
    chip_sha256: str = ""  # sha256 of embedded bytes; "" if unrecoverable/placeholder


@dataclass
class VerdictRecord:
    """One human adjudication of a single anchor's jump critical point.

    ISSUE-11 join keys: `anchor_id` (+ `stratum` for per-stratum Wilson CI),
    `annotator_id` + `is_overlap` for inter-annotator agreement, and the
    `corrected_*` bracket vs `pipeline_*` bracket for hit/miss + shift distance.
    """

    anchor_id: str
    grid_id: str
    stratum: str
    terminal_status: str
    confidence: str
    any_contradiction: str            # "True"/"False"/"" — mirrors assignment
    verdict: str                      # one of VERDICTS
    pipeline_interval_start: str
    pipeline_interval_end: str
    corrected_interval_start: str = ""  # SHIFT only, else ""
    corrected_interval_end: str = ""    # SHIFT only, else ""
    annotator_id: str = ""
    is_overlap: bool = False
    is_dispute_forced: bool = False
    #: Original short target ids (e.g. ``t00034036``) this c-anchor's jump verdict
    #: adjudicates. One c-anchor may own several of the 15 disputes (many-to-one
    #: collapse); ISSUE-11 explodes this list to attribute all 15 across the forced
    #: c-anchors. Empty for non-dispute anchors.
    dispute_target_ids: list[str] = field(default_factory=list)
    adjudication_seconds: float = 0.0  # submitted_ts - opened_ts, computed client-side
    opened_ts_utc: str = ""
    submitted_ts_utc: str = ""
    strip_content_hash: str = ""       # hash over `frames` identities (provenance lock)
    frames: list[FrameIdentity] = field(default_factory=list)
    sampler_seed: int | None = None
    sample_batch_id: str = ""
    notes: str = ""

    def __post_init__(self) -> None:
        if self.verdict not in VERDICTS:
            raise ValueError(f"verdict must be one of {VERDICTS}, got {self.verdict!r}")
        if self.annotator_id and self.annotator_id not in ANNOTATORS:
            raise ValueError(f"annotator_id must be one of {ANNOTATORS}, got {self.annotator_id!r}")


# ---------------------------------------------------------------------------
# Paths — deterministic layout under a single dated gold-set root

def now_utc_iso() -> str:
    """UTC timestamp, `YYYY-MM-DDTHH:MM:SSZ` — matches repo-wide convention."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def goldset_root(tag: str, *, base: Path | None = None) -> Path:
    """Root dir for a gold-set batch, e.g. tag=``dryrun_20260705``."""
    base = base or (Path.home() / "zasolar_data" / "geid_temporal")
    return base / f"goldset_{tag}"


def sample_assignment_path(root: Path) -> Path:
    return root / "sample_assignments.csv"


def rerender_chips_dir(root: Path) -> Path:
    """Re-rendered window frames (WP-C output), keyed by anchor/date/version."""
    return root / "rerender" / "chips"


def rerender_report_path(root: Path) -> Path:
    """Per-frame re-render outcome (WP-C): recovered vs dropped-and-reported."""
    return root / "rerender" / "frame_report.csv"


def strips_dir(root: Path) -> Path:
    """Self-contained strip HTML pages (WP-A output)."""
    return root / "strips"


def verdict_manifest_path(root: Path, annotator_id: str, sample_batch_id: str) -> Path:
    """Verdict JSONL imported back from a UI export, namespaced by annotator."""
    return root / "verdicts" / f"verdicts_{sample_batch_id}_{annotator_id}.jsonl"


# ---------------------------------------------------------------------------
# Content hashing

def frame_content_hash(frames: Sequence[FrameIdentity]) -> str:
    """Order-independent sha256 over frame identities — the strip provenance lock.

    Locks a verdict to the exact set of frames shown. Uses identity fields only
    (source/role/date/version/chip_sha256), not chip_path, so relocating files
    does not change the hash while re-rendering different pixels does.
    """
    payload = sorted(
        (f.source, f.role, f.capture_date, f.version, f.chip_sha256) for f in frames
    )
    blob = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Dispute crosswalk — short target id -> owning chip-group (c-anchor)
#
# The 15 dated-vs-undated disputes live in
# `fullstack_noscan_20260630/analysis/validity_per_unit.csv` under a column
# literally named `anchor` (NOT `anchor_id`), as bare short TARGET ids
# (`t00036959`, `t00034036`, ...). Every other gold-set input is keyed on the
# chip-group `c...` namespace (`..._c0000001`). A short target id therefore
# resolves to an adjudicable anchor only through the chipgroups CSV:
#
#   short `t00034036`  ->  full `..._t00034036`  (appears in some row's
#   `target_anchor_ids` ";"-list)  ->  that row's `anchor_id` == owning c-anchor.
#
# Multiple disputes can share one c-anchor (verified: c0001985/c0009311/c0009952/
# c0010157 each own >=2 of the 15). We collapse many-to-one so each owning
# c-anchor is force-included exactly once, carrying every short target id it owns.

#: column name of the short target id inside validity_per_unit.csv.
DISPUTE_ANCHOR_COLUMN = "anchor"
#: category value selecting the dated-vs-undated disputes.
DISPUTE_CATEGORY = "dated_vs_undated_mismatch"
#: trailing target token inside a full chip-group target id (e.g. ``..._t00034036``).
_TARGET_TOKEN_RE = re.compile(r"(t\d+)$")


def encode_target_ids(target_ids: Sequence[str]) -> str:
    """`;`-join short target ids for the CSV assignment column (mirrors chipgroups)."""
    return ";".join(target_ids)


def decode_target_ids(value: str) -> list[str]:
    """Parse the `;`-joined CSV column back to a list (empty -> [])."""
    value = (value or "").strip()
    return [t for t in (x.strip() for x in value.split(";")) if t]


def read_dispute_target_ids(validity_csv: Path) -> list[str]:
    """Read the short target ids of the 15 dated-vs-undated disputes.

    Reads the `anchor` column (NOT `anchor_id`) of rows whose `category` ==
    `dated_vs_undated_mismatch`. Returns bare short ids like ``t00036959``.
    """
    out: list[str] = []
    with Path(validity_csv).open("r", newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            if row.get("category") == DISPUTE_CATEGORY:
                tid = (row.get(DISPUTE_ANCHOR_COLUMN) or "").strip()
                if tid:
                    out.append(tid)
    return out


def _target_token(full_or_short: str) -> str:
    """Extract the bare `tNNNN` token from a short or full-prefixed target id."""
    m = _TARGET_TOKEN_RE.search(full_or_short.strip())
    return m.group(1) if m else ""


def resolve_disputes_to_anchors(
    dispute_target_ids: Sequence[str], chipgroups_csv: Path
) -> tuple[dict[str, list[str]], list[str]]:
    """Collapse short dispute target ids to their owning chip-group (c-anchor).

    Builds a token->c-anchor index from the chipgroups `target_anchor_ids` column
    (prefix-robust: matches on the trailing `tNNNN` token), then maps each dispute.

    Returns ``(anchor_to_targets, unresolved)`` where ``anchor_to_targets`` maps a
    c-anchor id -> the sorted list of short target ids it owns (deduped many-to-one),
    and ``unresolved`` lists any dispute target id with no owning chip-group (these
    are dropped-and-reported by WP-B, never crash the draw).
    """
    token_to_anchor: dict[str, str] = {}
    with Path(chipgroups_csv).open("r", newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            c_anchor = (row.get("anchor_id") or "").strip()
            for full_t in (row.get("target_anchor_ids") or "").split(";"):
                tok = _target_token(full_t)
                if tok and c_anchor:
                    token_to_anchor.setdefault(tok, c_anchor)

    anchor_to_targets: dict[str, list[str]] = {}
    unresolved: list[str] = []
    for short_t in dispute_target_ids:
        tok = _target_token(short_t)
        c_anchor = token_to_anchor.get(tok)
        if c_anchor is None:
            unresolved.append(short_t)
            continue
        anchor_to_targets.setdefault(c_anchor, []).append(short_t)
    for c_anchor in anchor_to_targets:
        anchor_to_targets[c_anchor] = sorted(set(anchor_to_targets[c_anchor]))
    return anchor_to_targets, unresolved


# ---------------------------------------------------------------------------
# Sample-assignment CSV I/O (WP-B writes, WP-A + WP-C read)

def write_sample_assignments(rows: Iterable[dict[str, Any]], path: Path) -> None:
    """Write assignment rows in the locked `SAMPLE_ASSIGNMENT_FIELDS` order."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(SAMPLE_ASSIGNMENT_FIELDS))
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in SAMPLE_ASSIGNMENT_FIELDS})


def read_sample_assignments(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


# ---------------------------------------------------------------------------
# Verdict manifest JSONL I/O (WP-A: UI export -> loadable records round-trip)

def _record_to_json(rec: VerdictRecord) -> dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "ts_utc": now_utc_iso(), **asdict(rec)}


def _record_from_json(obj: dict[str, Any]) -> VerdictRecord:
    ver = int(obj.get("schema_version", SCHEMA_VERSION))
    if ver != SCHEMA_VERSION:
        raise ValueError(
            f"goldset verdict schema_version mismatch: file={ver} expected={SCHEMA_VERSION}"
        )
    frames = [FrameIdentity(**fr) for fr in obj.get("frames", [])]
    known = {f for f in VerdictRecord.__dataclass_fields__}  # type: ignore[attr-defined]
    kwargs = {k: v for k, v in obj.items() if k in known and k != "frames"}
    return VerdictRecord(frames=frames, **kwargs)


def write_verdict_manifest(records: Iterable[VerdictRecord], path: Path) -> None:
    """Append-only JSONL, one verdict per line (mirrors verdict_store shape)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(_record_to_json(rec), ensure_ascii=False) + "\n")


def read_verdict_manifest(path: Path) -> list[VerdictRecord]:
    """Load a verdict JSONL back to `VerdictRecord`s (the round-trip target)."""
    out: list[VerdictRecord] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            out.append(_record_from_json(json.loads(line)))
    return out
