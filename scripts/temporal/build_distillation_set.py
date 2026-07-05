#!/usr/bin/env python3
"""Build the dinov3-scorer distillation training set (ISSUE-02).

Harvest per-vintage presence labels from the retained ``scan_state.json`` corpus
(**no new LLM calls** — the verdicts already exist), assign an anchor-disjoint
train / held-out split, stratify a small chip subset, and re-render that subset
through the *unmodified* idempotent GEHI download + scoring-crop stages at the
ISSUE-19 ``chip_geom_v2_tight12`` geometry.

See ``docs/dinov3_scorer/ISSUE-02-distillation-training-set.md`` (parent PRD
``docs/dinov3_sat_scorer_backbone_prd.md`` §D6). The harvest spans **four
corpora**: three scan-state corpora — chip-group (``c``-ids) + per-target
(``t``-ids) carry the base anchors, wayback re-scans a subset (extra rounds on
already-counted anchors) — **and census2023**, which is NOT a CSV overlay: it is
``census2023/census_audit/*.jsonl`` (one JSON line per anchor, each carrying a
``parsed`` dict with sequence-level ``confidence`` / ``quality_flag`` and a list
of ``observations`` bearing per-date ``capture_date`` / ``pv_present``). Each
census anchor's ``region`` / ``grid_id`` / terminal ``status`` are joined from
``census_install_intervals.csv``. census rows are LABEL-ONLY (``version=""`` — the
census chips were not persisted and used ``wb``/``tm`` provider tags, so they are
NOT re-renderable through the current path) and contribute genuinely new
``c``-namespace anchors absent from the scan corpus.

The D12 reference counts (``23,147 unique / 250,502 rounds``) were computed on
**inconsistent subsets** (unique anchors counted chip-group+per-target+wayback;
total rounds counted chip-group+per-target RoundResults ONLY — wayback and
census excluded) and are kept only as labelled ``EXPECTED_*`` references. The
authoritative ``unique_anchors`` / ``total_rounds`` are **re-derived on disk at
harvest time** (post-dedup, across all four corpora) and written to the README —
see the reconcile section there; D12 should be renumbered against them.
``done_ambiguous_*`` anchors are marker-misregistration: their whole observation
series is untrustworthy, so they are RETAINED in the manifest as
``label_3class=unusable`` (never dropped, so ISSUE-04 can measure co-teacher
disagreement on them) but excluded from the present/absent supervision pool.

Label rule (``label_3class``). present / absent **only** from a ``usable`` round
with high confidence (``>= HIGH_CONF_DEFAULT``, in ``[HIGH_CONF, 1.0]``) on a
non-``done_ambiguous_*`` anchor; everything else -> ``unusable``. ``HIGH_CONF``
is a NEW recorded render parameter: there was no pre-existing presence-confidence
threshold in the scan stack, and ~99.5% of usable verdicts sit at >= 0.90, which
is the natural cut.

Chip geometry is an explicit, versioned render parameter resolved from
``chip_geometry.py`` (default ``chip_geom_v2_tight12``) and recorded in the
render provenance — never silently inherited, never a verdict-store key.

Plugin boundary: this module imports only sibling ``scripts.temporal.*`` seams
and (optionally, for CBD resolution) reads a main-repo data gpkg by path. It does
NOT import ``scripts.training.*`` or any other main-repo code. All real artifacts
land under ``~/zasolar_data/`` (gitignored); only a tiny fixture is committed.
"""

from __future__ import annotations

# sys.path bootstrap so the subrepo runs as a script (matches sibling modules).
# ruff: noqa: E402
import argparse
import hashlib
import json
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Mapping, Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.temporal.chip_geometry import (
    LEGACY_GEOMETRY_VERSION,
    RERENDER_GEOMETRY_VERSION,
    contain_crop_to_footprint,
    resolve_chip_geometry,
)
from scripts.temporal.gehi_common import ReviewTargetMarker, ensure_single_target_review_png
from scripts.temporal.gehi_download import DownloadResult, download_chip_with_zoom_ladder
from scripts.temporal.geid_temporal_common import read_csv_rows, write_csv_rows
from scripts.temporal.scan_state import ScanState, load_scan_state, state_path_for  # noqa: F401

# --------------------------------------------------------------------------- #
# Confirmed on-disk corpus roots. Three scan-state corpora — chip-group +
# per-target carry the base anchors; wayback re-scans a subset (extra rounds on
# already-counted anchors). census2023 is the FOURTH corpus and IS harvested
# (from ``census_audit/*.jsonl`` joined to ``census_install_intervals.csv`` — it
# is NOT a CSV overlay); see ``harvest_census2023`` and the module docstring.
# --------------------------------------------------------------------------- #
_GEID = Path.home() / "zasolar_data" / "geid_temporal"
CORPUS_DIRS: dict[str, Path] = {
    "chip_group": _GEID / "jhb_full382_fpcut_scan_2026-06-02" / "scan_states",
    "per_target": _GEID / "jhb_full382_fpcut_scan_2026-06-02" / "norecent_pertarget" / "scan_states",
    "wayback": _GEID / "jhb_full382_fpcut_wayback_2026-06-04" / "scan_states",
}

# census2023 (fourth corpus) — per-anchor JSON-Lines label files + interval join.
_CENSUS_ROOT = _GEID / "jhb_full382_fpcut_scan_2026-06-02" / "census2023"
CENSUS_AUDIT_DIR = _CENSUS_ROOT / "census_audit"
CENSUS_INTERVALS_CSV = _CENSUS_ROOT / "census_install_intervals.csv"

CHIP_TARGETS_CSV = (
    _GEID
    / "jhb_full382_unified_A_merge01_c0925_fpcut_2026-06-01_chipgroups"
    / "chip_targets.csv"
)
DEFAULT_OUTPUT_ROOT = _GEID / "dinov3_distill_2026-07-04"

# D12 reference sizing (pre-census, INCONSISTENT subsets — see module docstring):
# EXPECTED_UNIQUE_ANCHORS counted chip-group+per-target+wayback anchors;
# EXPECTED_ROUNDS counted chip-group+per-target RoundResults only (no wayback,
# no census). Kept only as a labelled reference; the authoritative totals are
# re-derived on disk at harvest time and written to the README. Recorded, not
# enforced: any deviation is explained in the README, never crashed on.
EXPECTED_UNIQUE_ANCHORS = 23_147  # D12 reference (pre-census, inconsistent)
EXPECTED_ROUNDS = 250_502  # D12 reference (pre-census, inconsistent)

# NEW recorded label parameter (no pre-existing threshold in the presence stack).
HIGH_CONF_DEFAULT = 0.90

# AC lists 13 columns "at least"; ``corpus`` and ``label_render_geometry`` are
# appended (15 total). ``label_render_geometry`` = the geometry the harvested
# label was produced under (``chip_geom_v1_banked96`` for ALL rows) — the
# Distillation-label caveat's provenance column, DISTINCT from the re-render
# ``render_geometry`` recorded in chip provenance.
MANIFEST_COLUMNS: tuple[str, ...] = (
    "anchor_id",
    "region",
    "grid_id",
    "capture_date",
    "version",
    "actual_zoom",
    "pv_present",
    "confidence",
    "quality_flag",
    "terminal_status",
    "label_3class",
    "split",
    "sub_domain",
    "corpus",
    "label_render_geometry",
)

# CBD 25-grid benchmark set (legacy G-namespace; ``geid_domain_gap`` memory):
# CBD GEID is an aerial mosaic, non-CBD is true satellite (SSIM 0.21). Scan-state
# grid_id is JNB-namespace with no G->JNB crosswalk, so ``sub_domain`` is resolved
# by JNB-grid membership (built once by spatial join, see resolve_cbd_jnb_grids).
CBD_G_GRIDS: frozenset[str] = frozenset(
    f"G{n:04d}"
    for block in (772, 814, 853, 888, 922)
    for n in range(block, block + 5)
)
DEFAULT_CBD_GPKG = PROJECT_ROOT.parent / "ZAsolar" / "data" / "jhb_task_grid_unified.gpkg"

SUB_DOMAIN_CBD = "cbd_aerial_mosaic"
SUB_DOMAIN_NON_CBD = "non_cbd_satellite"
SUB_DOMAIN_UNKNOWN = "sub_domain_unknown"


# --------------------------------------------------------------------------- #
# label rule
# --------------------------------------------------------------------------- #
def label_3class(
    *,
    pv_present: bool | None,
    confidence: float | None,
    quality_flag: str,
    terminal_status: str,
    high_conf: float = HIGH_CONF_DEFAULT,
) -> str:
    """Map one RoundResult to ``present`` / ``absent`` / ``unusable``.

    Anchor-level veto first: any round on a ``done_ambiguous_*`` anchor is
    ``unusable`` (marker-misregistration taints the whole series). Then a
    round yields present/absent ONLY when it is ``usable``, carries a verdict,
    and its confidence is high (``high_conf <= confidence <= 1.0`` — the stray
    ``1.5`` and the thin low-confidence tail both fall through). Otherwise
    ``unusable``.
    """
    if terminal_status.startswith("done_ambiguous_"):
        return "unusable"
    if quality_flag != "usable":
        return "unusable"
    if pv_present is None:
        return "unusable"
    if confidence is None or not (high_conf <= confidence <= 1.0):
        return "unusable"
    return "present" if pv_present else "absent"


# --------------------------------------------------------------------------- #
# sub_domain
# --------------------------------------------------------------------------- #
def resolve_sub_domain(grid_id: str, cbd_grids: frozenset[str] | None) -> str:
    """CBD (aerial mosaic) vs non-CBD (satellite) by JNB-grid membership.

    ``cbd_grids`` is the resolved JNB-namespace CBD set (see
    ``resolve_cbd_jnb_grids``). ``None`` means the CBD footprint could not be
    resolved (gpkg unavailable) -> ``sub_domain_unknown`` (recorded, not fatal;
    ``sub_domain`` is a stratification aid, not a label).
    """
    if cbd_grids is None:
        return SUB_DOMAIN_UNKNOWN
    return SUB_DOMAIN_CBD if grid_id in cbd_grids else SUB_DOMAIN_NON_CBD


def resolve_cbd_jnb_grids(
    gpkg_path: Path = DEFAULT_CBD_GPKG,
    cbd_g_grids: frozenset[str] = CBD_G_GRIDS,
) -> frozenset[str] | None:
    """Spatially map the 25 legacy-G CBD cells to JNB grid ids (built once).

    Both grid schemes coexist in ``jhb_task_grid_unified.gpkg``; a JNB cell is
    CBD iff its centroid falls inside the dissolved CBD-25 footprint. Returns the
    JNB grid-id set, or ``None`` if the gpkg / geopandas are unavailable (callers
    then record ``sub_domain_unknown``). Best-effort and side-effect-free.
    """
    if not gpkg_path.exists():
        return None
    try:
        import geopandas as gpd  # heavy, optional
    except Exception:
        return None
    try:
        grid = gpd.read_file(gpkg_path)
        id_col = next(
            (c for c in ("gridcell_id", "grid_id", "id") if c in grid.columns), None
        )
        if id_col is None:
            return None
        cbd = grid[grid[id_col].isin(cbd_g_grids)]
        if cbd.empty:
            return None
        cbd_poly = cbd.union_all() if hasattr(cbd, "union_all") else cbd.unary_union
        jnb = grid[grid[id_col].astype(str).str.startswith("JNB")].copy()
        jnb["_c"] = jnb.geometry.centroid
        hits = jnb[jnb["_c"].within(cbd_poly)]
        return frozenset(str(v) for v in hits[id_col].tolist())
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# harvest
# --------------------------------------------------------------------------- #
@dataclass
class HarvestStats:
    unique_anchors: int = 0
    total_rounds: int = 0
    ambiguous_anchors: int = 0
    # rounds collapsed by the (anchor, capture_date, version) dedup (D).
    duplicate_rounds_collapsed: int = 0
    # census2023 (fourth corpus) contribution + its skip counts.
    census_rows: int = 0
    census_anchors: int = 0
    census_malformed_dropped: int = 0
    census_missing_interval_dropped: int = 0
    status_counts: Counter = field(default_factory=Counter)
    label_counts: Counter = field(default_factory=Counter)
    sub_domain_counts: Counter = field(default_factory=Counter)
    corpus_anchor_counts: Counter = field(default_factory=Counter)

    def to_dict(self) -> dict[str, object]:
        """JSON-serialisable snapshot (Counters -> plain dicts)."""
        return {
            "unique_anchors": self.unique_anchors,
            "total_rounds": self.total_rounds,
            "ambiguous_anchors": self.ambiguous_anchors,
            "duplicate_rounds_collapsed": self.duplicate_rounds_collapsed,
            "census_rows": self.census_rows,
            "census_anchors": self.census_anchors,
            "census_malformed_dropped": self.census_malformed_dropped,
            "census_missing_interval_dropped": self.census_missing_interval_dropped,
            "status_counts": dict(self.status_counts),
            "label_counts": dict(self.label_counts),
            "sub_domain_counts": dict(self.sub_domain_counts),
            "corpus_anchor_counts": dict(self.corpus_anchor_counts),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> "HarvestStats":
        """Rebuild from :meth:`to_dict` (round-trips the render-time README)."""
        s = cls()
        s.unique_anchors = int(data.get("unique_anchors", 0) or 0)
        s.total_rounds = int(data.get("total_rounds", 0) or 0)
        s.ambiguous_anchors = int(data.get("ambiguous_anchors", 0) or 0)
        s.duplicate_rounds_collapsed = int(data.get("duplicate_rounds_collapsed", 0) or 0)
        s.census_rows = int(data.get("census_rows", 0) or 0)
        s.census_anchors = int(data.get("census_anchors", 0) or 0)
        s.census_malformed_dropped = int(data.get("census_malformed_dropped", 0) or 0)
        s.census_missing_interval_dropped = int(
            data.get("census_missing_interval_dropped", 0) or 0
        )
        s.status_counts = Counter(data.get("status_counts", {}) or {})
        s.label_counts = Counter(data.get("label_counts", {}) or {})
        s.sub_domain_counts = Counter(data.get("sub_domain_counts", {}) or {})
        s.corpus_anchor_counts = Counter(data.get("corpus_anchor_counts", {}) or {})
        return s


@dataclass
class CensusHarvestStats:
    """Per-corpus contribution of the census2023 harvest (rows + skip counts)."""

    rows_emitted: int = 0
    anchors_seen: int = 0
    malformed_dropped: int = 0
    missing_interval_dropped: int = 0


def _anchor_rows(
    state: ScanState,
    *,
    corpus: str,
    cbd_grids: frozenset[str] | None,
    high_conf: float,
) -> list[dict[str, object]]:
    sub_domain = resolve_sub_domain(state.grid_id, cbd_grids)
    rows: list[dict[str, object]] = []
    for rnd in state.rounds:
        for res in rnd.results:
            rows.append(
                {
                    "anchor_id": state.anchor_id,
                    "region": state.region_key,
                    "grid_id": state.grid_id,
                    "capture_date": res.capture_date,
                    "version": res.version,
                    "actual_zoom": "" if res.actual_zoom is None else res.actual_zoom,
                    "pv_present": _bool_str(res.pv_present),
                    "confidence": "" if res.confidence is None else res.confidence,
                    "quality_flag": res.quality_flag,
                    "terminal_status": state.status,
                    "label_3class": label_3class(
                        pv_present=res.pv_present,
                        confidence=res.confidence,
                        quality_flag=res.quality_flag,
                        terminal_status=state.status,
                        high_conf=high_conf,
                    ),
                    "split": "",
                    "sub_domain": sub_domain,
                    "corpus": corpus,
                    # all harvested labels were produced on v1 (banked96)
                    # geometry — the Distillation-label caveat provenance column.
                    "label_render_geometry": LEGACY_GEOMETRY_VERSION,
                }
            )
    return rows


def _bool_str(value: bool | None) -> str:
    if value is True:
        return "1"
    if value is False:
        return "0"
    return ""


def harvest_corpus(
    corpus_dirs: Mapping[str, Path],
    *,
    cbd_grids: frozenset[str] | None,
    high_conf: float = HIGH_CONF_DEFAULT,
    limit: int | None = None,
) -> tuple[list[dict[str, object]], HarvestStats]:
    """Walk the corpus dirs, load each scan state, emit one row per RoundResult.

    Anchors are deduped across corpora by ``anchor_id`` (the first corpus that
    carries an anchor wins its base rows; later corpora — wayback — append their
    extra rounds for the same anchor). ``limit`` caps the number of *state files*
    read (for a fast read-only sanity run over the real corpus).
    """
    rows: list[dict[str, object]] = []
    stats = HarvestStats()
    seen: set[str] = set()
    read = 0
    for corpus, base in corpus_dirs.items():
        if not base.exists():
            raise FileNotFoundError(
                f"scan corpus '{corpus}' root is missing: {base} — a renamed / "
                f"moved corpus dir must fail loudly, never be silently dropped "
                f"from the harvest (check CORPUS_DIRS paths / date bumps)"
            )
        for path in sorted(base.glob("*.json")):
            if limit is not None and read >= limit:
                break
            read += 1
            state = load_scan_state(path)
            if state is None:
                continue
            anchor_rows = _anchor_rows(
                state, corpus=corpus, cbd_grids=cbd_grids, high_conf=high_conf
            )
            rows.extend(anchor_rows)
            stats.total_rounds += len(anchor_rows)
            if state.anchor_id not in seen:
                seen.add(state.anchor_id)
                stats.unique_anchors += 1
                stats.status_counts[state.status] += 1
                stats.corpus_anchor_counts[corpus] += 1
                if state.status.startswith("done_ambiguous_"):
                    stats.ambiguous_anchors += 1
                if anchor_rows:
                    stats.sub_domain_counts[anchor_rows[0]["sub_domain"]] += 1
            for r in anchor_rows:
                stats.label_counts[r["label_3class"]] += 1
        if limit is not None and read >= limit:
            break
    return rows, stats


# --------------------------------------------------------------------------- #
# census2023 harvest (fourth corpus: JSON-Lines label files + interval join)
# --------------------------------------------------------------------------- #
def _load_census_intervals(intervals_csv: Path) -> dict[str, dict[str, str]]:
    """Return ``{anchor_id: {region_key, grid_id, status}}`` from the interval CSV.

    Required corpus input for the census join (region / grid / terminal status
    live only here, never in the audit line). A missing file fails loudly with
    the expected path named.
    """
    intervals_csv = Path(intervals_csv)
    if not intervals_csv.exists():
        raise FileNotFoundError(
            f"census2023 install-intervals CSV is a required corpus input and is "
            f"missing: {intervals_csv}"
        )
    out: dict[str, dict[str, str]] = {}
    for r in read_csv_rows(intervals_csv):
        anchor_id = str(r.get("anchor_id", "")).strip()
        if not anchor_id:
            continue
        out[anchor_id] = {
            "region_key": str(r.get("region_key", "")),
            "grid_id": str(r.get("grid_id", "")),
            "status": str(r.get("status", "")),
        }
    return out


def _read_first_json_line(path: Path) -> dict[str, object] | None:
    """Read the single JSON line of a ``census_audit/*.jsonl`` file, or ``None``.

    Returns ``None`` on an empty file or unparseable/non-dict line (the caller
    counts it as a ``malformed`` drop).
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    stripped = text.strip()
    if not stripped:
        return None
    first = stripped.split("\n", 1)[0].strip()
    try:
        obj = json.loads(first)
    except (json.JSONDecodeError, ValueError):
        return None
    return obj if isinstance(obj, dict) else None


def harvest_census2023(
    audit_dir: Path,
    intervals_csv: Path,
    *,
    cbd_grids: frozenset[str] | None,
    high_conf: float = HIGH_CONF_DEFAULT,
    limit: int | None = None,
) -> tuple[list[dict[str, object]], CensusHarvestStats]:
    """Harvest one manifest row per census observation (LABEL-ONLY, version="").

    Each ``census_audit/<anchor>.jsonl`` file holds ONE json line with a
    ``parsed`` dict (sequence-level ``confidence`` / ``quality_flag`` +
    ``observations``); per-anchor ``region`` / ``grid_id`` / terminal ``status``
    are joined from ``intervals_csv``. For each observation we emit a row with an
    EMPTY ``version`` / ``actual_zoom`` (census chips were not persisted and used
    ``wb``/``tm`` provider tags, so these rows are label-only, never re-rendered).
    ``label_3class`` reuses the shared rule unchanged. A line with ``ok`` false or
    a non-dict ``parsed`` is skipped + counted (``malformed``); an audit anchor
    absent from the interval CSV is skipped + counted (``missing_interval``).
    ``limit`` caps the number of audit files read (sanity runs).
    """
    audit_dir = Path(audit_dir)
    intervals = _load_census_intervals(intervals_csv)
    rows: list[dict[str, object]] = []
    cstats = CensusHarvestStats()
    seen_anchors: set[str] = set()
    read = 0
    for path in sorted(audit_dir.glob("*.jsonl")):
        if limit is not None and read >= limit:
            break
        read += 1
        line = _read_first_json_line(path)
        if line is None or line.get("ok") is False or not isinstance(line.get("parsed"), dict):
            cstats.malformed_dropped += 1
            continue
        parsed = line["parsed"]
        anchor_id = str(line.get("anchor_id") or path.stem)
        meta = intervals.get(anchor_id)
        if meta is None:
            cstats.missing_interval_dropped += 1
            continue
        region = meta["region_key"]
        grid_id = meta["grid_id"]
        status = meta["status"]
        quality_flag = str(parsed.get("quality_flag", ""))
        conf_val = parsed.get("confidence")
        conf_float = float(conf_val) if isinstance(conf_val, (int, float)) else None
        sub_domain = resolve_sub_domain(grid_id, cbd_grids)
        observations = parsed.get("observations") or []
        for obs in observations:
            if not isinstance(obs, dict):
                continue
            capture_date = obs.get("capture_date")
            if not capture_date:
                continue  # guard; every real obs carries a capture_date
            pv = obs.get("pv_present")
            pv_bool = pv if isinstance(pv, bool) else None
            rows.append(
                {
                    "anchor_id": anchor_id,
                    "region": region,
                    "grid_id": grid_id,
                    "capture_date": str(capture_date),
                    "version": "",  # census = label-only, not re-renderable
                    "actual_zoom": "",
                    "pv_present": _bool_str(pv_bool),
                    "confidence": "" if conf_float is None else conf_float,
                    "quality_flag": quality_flag,
                    "terminal_status": status,
                    "label_3class": label_3class(
                        pv_present=pv_bool,
                        confidence=conf_float,
                        quality_flag=quality_flag,
                        terminal_status=status,
                        high_conf=high_conf,
                    ),
                    "split": "",
                    "sub_domain": sub_domain,
                    "corpus": "census2023",
                    "label_render_geometry": LEGACY_GEOMETRY_VERSION,
                }
            )
            seen_anchors.add(anchor_id)
    cstats.rows_emitted = len(rows)
    cstats.anchors_seen = len(seen_anchors)
    return rows, cstats


# --------------------------------------------------------------------------- #
# round-level dedup + full-harvest reconcile
# --------------------------------------------------------------------------- #
def _confidence_value(row: Mapping[str, object]) -> float:
    try:
        return float(row["confidence"])  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return -1.0


def dedup_rows(
    rows: Sequence[Mapping[str, object]],
) -> tuple[list[dict[str, object]], int]:
    """Collapse duplicate ``(anchor_id, capture_date, version)`` rows to one.

    On collision keep ONE deterministically: prefer a present/absent label over
    ``unusable``, then higher ``confidence``, then earliest original position
    (stable). Output order follows each key's first appearance. Census rows carry
    ``version=""`` and one observation per (anchor, date), so they never collide
    with the integer-versioned scan rounds — the collapse targets wayback/scan
    exact-vintage re-scans. Returns ``(deduped_rows, collapsed_count)``.
    """

    def _label_rank(label: object) -> int:
        return 0 if label in ("present", "absent") else 1

    chosen: dict[tuple[str, str, str], tuple[tuple[int, float, int], dict[str, object]]] = {}
    first_order: dict[tuple[str, str, str], int] = {}
    collapsed = 0
    for idx, row in enumerate(rows):
        key = (
            str(row["anchor_id"]),
            str(row["capture_date"]),
            str(row["version"]),
        )
        rank = (_label_rank(row["label_3class"]), -_confidence_value(row), idx)
        if key not in chosen:
            chosen[key] = (rank, dict(row))
            first_order[key] = idx
        else:
            collapsed += 1
            if rank < chosen[key][0]:
                chosen[key] = (rank, dict(row))
    ordered_keys = sorted(first_order, key=lambda k: first_order[k])
    return [chosen[k][1] for k in ordered_keys], collapsed


def compute_stats(rows: Sequence[Mapping[str, object]]) -> HarvestStats:
    """Re-derive the full harvest stats from the final (deduped) row list.

    Per-anchor fields (status / sub_domain / owning corpus) are taken from the
    FIRST row seen for each anchor, preserving the scan-before-census ordering of
    the combined harvest (so a scan anchor's terminal status wins over its census
    echo). Per-row fields (labels, total rounds) count every surviving row.
    """
    stats = HarvestStats()
    stats.total_rounds = len(rows)
    seen: set[str] = set()
    for r in rows:
        stats.label_counts[str(r["label_3class"])] += 1
        anchor_id = str(r["anchor_id"])
        if anchor_id in seen:
            continue
        seen.add(anchor_id)
        status = str(r["terminal_status"])
        stats.status_counts[status] += 1
        stats.corpus_anchor_counts[str(r.get("corpus", ""))] += 1
        stats.sub_domain_counts[str(r["sub_domain"])] += 1
        if status.startswith("done_ambiguous_"):
            stats.ambiguous_anchors += 1
    stats.unique_anchors = len(seen)
    return stats


def harvest_all(
    corpus_dirs: Mapping[str, Path],
    *,
    cbd_grids: frozenset[str] | None,
    high_conf: float = HIGH_CONF_DEFAULT,
    limit: int | None = None,
    census_audit_dir: Path | None = CENSUS_AUDIT_DIR,
    census_intervals_csv: Path = CENSUS_INTERVALS_CSV,
    include_census: bool = True,
) -> tuple[list[dict[str, object]], HarvestStats]:
    """Harvest all four corpora, dedup rounds, and re-derive reconciled stats.

    Scan corpora are harvested first (chip-group -> per-target -> wayback), then
    census2023 (label-only). Rows are combined, deduped on
    ``(anchor_id, capture_date, version)``, and the authoritative
    ``unique_anchors`` / ``total_rounds`` (post-dedup, all four corpora) are
    re-derived — never asserted against the D12 reference. With census ON, a
    MISSING audit dir fails loudly (census2023 is a required corpus). ``limit``
    caps state/audit files per corpus (sanity runs).
    """
    scan_rows, _ = harvest_corpus(
        corpus_dirs, cbd_grids=cbd_grids, high_conf=high_conf, limit=limit
    )
    census_rows: list[dict[str, object]] = []
    cstats = CensusHarvestStats()
    if include_census:
        if census_audit_dir is None or not Path(census_audit_dir).exists():
            raise FileNotFoundError(
                f"census2023 is a required corpus but its audit dir is missing: "
                f"{census_audit_dir} (pass --no-census2023 to opt out for a "
                f"scan-only sanity run)"
            )
        census_rows, cstats = harvest_census2023(
            census_audit_dir,
            census_intervals_csv,
            cbd_grids=cbd_grids,
            high_conf=high_conf,
            limit=limit,
        )

    all_rows = list(scan_rows) + census_rows
    deduped, collapsed = dedup_rows(all_rows)
    stats = compute_stats(deduped)
    stats.duplicate_rounds_collapsed = collapsed
    stats.census_rows = cstats.rows_emitted
    stats.census_anchors = cstats.anchors_seen
    stats.census_malformed_dropped = cstats.malformed_dropped
    stats.census_missing_interval_dropped = cstats.missing_interval_dropped
    return deduped, stats


# --------------------------------------------------------------------------- #
# anchor-disjoint split
# --------------------------------------------------------------------------- #
def assign_splits(
    anchor_ids: Iterable[str],
    *,
    heldout_frac: float = 0.20,
    salt: str = "dinov3_distill_v1",
) -> dict[str, str]:
    """Deterministically assign each anchor to ``train`` or ``heldout``.

    Anchor-level and hash-based, so the partition is disjoint by construction and
    reproducible from ``salt`` alone (order-independent). Held-out feeds both the
    calibration and gate steps downstream.
    """
    if not 0.0 < heldout_frac < 1.0:
        raise ValueError(f"heldout_frac must be in (0,1), got {heldout_frac}")
    threshold = int(round(heldout_frac * 1_000_000))
    out: dict[str, str] = {}
    for anchor_id in anchor_ids:
        digest = hashlib.sha256(f"{salt}:{anchor_id}".encode("utf-8")).hexdigest()
        bucket = int(digest[:8], 16) % 1_000_000
        out[anchor_id] = "heldout" if bucket < threshold else "train"
    return out


# --------------------------------------------------------------------------- #
# stratified chip subset
# --------------------------------------------------------------------------- #
def select_chip_subset(
    rows: Sequence[Mapping[str, object]],
    *,
    target_n: int = 800,
    salt: str = "dinov3_distill_subset_v1",
    cbd_oversample: float = 3.0,
) -> list[str]:
    """Pick ~``target_n`` anchor_ids stratified by (sub_domain, dominant label).

    Only RENDERABLE anchors are eligible: a row must (a) carry a present/absent
    (usable) label AND (b) have a non-empty ``version`` (scan corpora). census2023
    rows carry ``version=""`` (label-only, chips were never persisted) and are
    therefore EXCLUDED from the re-render subset — they stay in the label pool
    only. CBD anchors are rare (~4.8%) and oversampled by ``cbd_oversample`` so a
    usable per-domain cell survives. Deterministic given ``salt``.
    """
    # dominant label + sub_domain per eligible (renderable) anchor
    per_anchor: dict[str, dict[str, object]] = {}
    for r in rows:
        lbl = r["label_3class"]
        if lbl not in ("present", "absent"):
            continue
        if str(r.get("version", "")) == "":
            continue  # census / label-only rounds are not re-renderable
        a = str(r["anchor_id"])
        entry = per_anchor.setdefault(
            a, {"sub_domain": r["sub_domain"], "present": 0, "absent": 0}
        )
        entry[str(lbl)] = int(entry[str(lbl)]) + 1  # type: ignore[arg-type]

    strata: dict[tuple[str, str], list[str]] = {}
    for a, e in per_anchor.items():
        label = "present" if int(e["present"]) >= int(e["absent"]) else "absent"  # type: ignore[call-overload]
        key = (str(e["sub_domain"]), label)
        strata.setdefault(key, []).append(a)

    for key in strata:
        strata[key].sort(key=lambda x: hashlib.sha256(f"{salt}:{x}".encode()).hexdigest())

    # per-stratum quota with CBD oversampling
    weights = {
        key: (cbd_oversample if key[0] == SUB_DOMAIN_CBD else 1.0) * len(members)
        for key, members in strata.items()
    }
    total_w = sum(weights.values()) or 1.0
    chosen: list[str] = []
    for key, members in strata.items():
        quota = min(len(members), int(round(target_n * weights[key] / total_w)))
        chosen.extend(members[:quota])
    return sorted(set(chosen))


def summarize_subset_strata(
    rows: Sequence[Mapping[str, object]],
    selected_ids: Iterable[str],
) -> dict[str, dict[str, int]]:
    """Per-stratum (``sub_domain`` x dominant-label) eligible / selected counts.

    Mirrors :func:`select_chip_subset`'s eligibility (renderable = present/absent
    on a non-empty ``version``) and dominant-label rule so the README can document
    the JOINT strata counts and how many anchors each stratum contributed to the
    subset — criterion 10's per-stratum breakdown, not just the marginals.
    """
    selected = set(str(a) for a in selected_ids)
    per_anchor: dict[str, dict[str, object]] = {}
    for r in rows:
        lbl = r["label_3class"]
        if lbl not in ("present", "absent"):
            continue
        if str(r.get("version", "")) == "":
            continue
        a = str(r["anchor_id"])
        entry = per_anchor.setdefault(
            a, {"sub_domain": r["sub_domain"], "present": 0, "absent": 0}
        )
        entry[str(lbl)] = int(entry[str(lbl)]) + 1  # type: ignore[arg-type]

    out: dict[str, dict[str, int]] = {}
    for a, e in per_anchor.items():
        label = "present" if int(e["present"]) >= int(e["absent"]) else "absent"  # type: ignore[call-overload]
        key = f"{e['sub_domain']}|{label}"
        cell = out.setdefault(key, {"eligible": 0, "selected": 0})
        cell["eligible"] += 1
        if a in selected:
            cell["selected"] += 1
    return out


# --------------------------------------------------------------------------- #
# chip_targets.csv join (HARD dependency of the re-render)
# --------------------------------------------------------------------------- #
def load_chip_targets_lookup(
    path: Path = CHIP_TARGETS_CSV,
) -> tuple[dict[str, dict[str, str]], dict[str, dict[str, str]]]:
    """Return ``(by_target_id, by_chip_id)`` lookups from ``chip_targets.csv``.

    Hard dependency (ISSUE-02 / D12.ii): a missing file fails loudly with the
    expected path named; an empty file is rejected rather than yielding a silent
    zero-row set. ``by_target_id`` is keyed on the ``anchor_id`` column (the
    per-target ``t``-ids); ``by_chip_id`` is keyed on the ``chip_id`` column (the
    group ``c``-ids), keeping the first row seen for the shared group bbox.
    """
    if not path.exists():
        raise FileNotFoundError(
            f"chip_targets.csv is a hard dependency of the chip re-render and is "
            f"missing: {path}"
        )
    rows = read_csv_rows(path)
    if not rows:
        raise ValueError(f"chip_targets.csv is empty (no data rows): {path}")
    by_target_id: dict[str, dict[str, str]] = {}
    by_chip_id: dict[str, dict[str, str]] = {}
    for r in rows:
        tid = str(r.get("anchor_id", "")).strip()
        cid = str(r.get("chip_id", "")).strip()
        if tid:
            by_target_id[tid] = r
        if cid and cid not in by_chip_id:
            by_chip_id[cid] = r
    return by_target_id, by_chip_id


def _download_anchor(row: Mapping[str, str]) -> dict[str, object]:
    """A chip_targets row IS the GEHI download anchor dict (group-chip keyed)."""
    return {
        "anchor_id": str(row["chip_id"]),  # download keyed on the group chip id
        "region_key": row.get("region_key", ""),
        "grid_id": row.get("grid_id", ""),
        "chip_lon_min": row["chip_lon_min"],
        "chip_lon_max": row["chip_lon_max"],
        "chip_lat_min": row["chip_lat_min"],
        "chip_lat_max": row["chip_lat_max"],
    }


def _marker_from_row(row: Mapping[str, str]) -> ReviewTargetMarker:
    return ReviewTargetMarker(
        target_id=str(row.get("anchor_id", "")),
        target_label=str(row.get("target_label", "")),
        offset_x_m=float(row.get("target_offset_x_m", 0.0) or 0.0),
        offset_y_m=float(row.get("target_offset_y_m", 0.0) or 0.0),
        search_radius_m=float(row.get("search_radius_m", 0.0) or 0.0) or None,
    )


def _anchor_kind(anchor_id: str) -> str:
    """``c`` (chip-group), ``t`` (per-target), or ``unknown`` from the id suffix."""
    import re

    m = re.search(r"_([ct])\d+$", anchor_id)
    return m.group(1) if m else "unknown"


# --------------------------------------------------------------------------- #
# chip re-render (GEHI stage UNMODIFIED — injected seams for offline tests)
# --------------------------------------------------------------------------- #
@dataclass
class RenderStats:
    rendered: int = 0
    dropped_unrecoverable: int = 0
    dropped_unresolved: int = 0
    drop_log: list[dict[str, object]] = field(default_factory=list)
    provenance_rows: list[dict[str, object]] = field(default_factory=list)


def render_subset(
    subset_rows: Sequence[Mapping[str, object]],
    *,
    chip_targets_path: Path = CHIP_TARGETS_CSV,
    geometry_version: str = RERENDER_GEOMETRY_VERSION,
    output_root: Path,
    contain_footprint: bool = True,
    draw_marker: bool = True,
    downloader: Callable[..., DownloadResult] = download_chip_with_zoom_ladder,
    renderer: Callable[..., Path] = ensure_single_target_review_png,
) -> RenderStats:
    """Re-download + scoring-crop each subset round via the unmodified GEHI stage.

    ``geometry_version`` is resolved from the registry (fails loudly on an unknown
    name) and recorded in every provenance row — never silently inherited, never a
    verdict-store key. Rounds whose historical chip is unrecoverable
    (``all_zooms_failed``) are dropped and counted; rounds whose ``anchor_id`` is
    absent from ``chip_targets.csv`` are counted as a separate unresolvable-join
    drop. The download is idempotent (skip-existing), so re-runs do not re-fetch.
    ``downloader`` / ``renderer`` default to the real GEHI seams and are injected
    with stubs in unit tests (no network, no GPU).

    ``draw_marker`` (default ``True``, the slice-2 LLM-review render) is threaded
    to the renderer: ``False`` produces the PRD-D4 marker-free STUDENT chip at a
    distinct ``.nomarker.png`` path (the marked and marker-free variants coexist
    beside the shared tif). Every provenance row additionally records ``marker``
    (``"drawn"`` / ``"none"``) and ``label_arm`` (``"supervision"`` for
    present/absent rows, ``"unusable"`` for quality-driven unusable rows) so the
    student render manifest is self-describing; these extra keys are inert for the
    slice-2 ``chip_render_provenance.csv`` writer, which declares neither column.
    """
    geom = resolve_chip_geometry(geometry_version)  # loud KeyError on unknown
    by_target_id, by_chip_id = load_chip_targets_lookup(chip_targets_path)
    output_root = Path(output_root)
    stats = RenderStats()

    for row in subset_rows:
        anchor_id = str(row["anchor_id"])
        kind = _anchor_kind(anchor_id)
        if kind == "t":
            ct_row = by_target_id.get(anchor_id)
        elif kind == "c":
            ct_row = by_chip_id.get(anchor_id)
        else:
            ct_row = by_target_id.get(anchor_id) or by_chip_id.get(anchor_id)
        if ct_row is None:
            stats.dropped_unresolved += 1
            stats.drop_log.append(
                {"anchor_id": anchor_id, "reason": "unresolvable_join"}
            )
            continue

        capture_date = str(row["capture_date"])
        version = row["version"]
        zoom = row.get("actual_zoom")
        zoom_ladder = (int(zoom),) if zoom not in (None, "") else (20, 19)

        dl = downloader(
            _download_anchor(ct_row),
            capture_date=capture_date,
            version=version,
            zoom_ladder=zoom_ladder,
            output_root=output_root,
            provider="TM",
            allow_nearest=False,
        )
        if dl.status == "all_zooms_failed" or dl.path is None:
            stats.dropped_unrecoverable += 1
            stats.drop_log.append(
                {
                    "anchor_id": anchor_id,
                    "chip_id": ct_row.get("chip_id", ""),
                    "capture_date": capture_date,
                    "version": version,
                    "reason": "all_zooms_failed",
                    "error": dl.error,
                }
            )
            continue

        chip_size_m = float(ct_row.get("chip_size_m", geom.download_chip_size_m) or geom.download_chip_size_m)
        marker = _marker_from_row(ct_row)
        crop_mult = geom.crop_context_multiplier
        if contain_footprint:
            # floor the tight crop to the installation footprint (ISSUE-19 guard)
            radius = marker.search_radius_m or (chip_size_m * 0.05)
            base_crop = min(chip_size_m, max(geom.min_crop_size_m, 2.0 * radius * crop_mult))
            contained = contain_crop_to_footprint(
                base_crop,
                source_width_m=float(ct_row.get("source_width_m", 0.0) or 0.0),
                source_height_m=float(ct_row.get("source_height_m", 0.0) or 0.0),
                chip_size_m=chip_size_m,
            )
            if radius > 0:
                crop_mult = contained / (2.0 * radius)

        png = renderer(
            dl.path,
            marker,
            chip_size_m=chip_size_m,
            crop_context_multiplier=crop_mult,
            min_crop_size_m=geom.min_crop_size_m,
            min_output_px=geom.min_output_px,
            draw_marker=draw_marker,
        )
        label = str(row.get("label_3class", ""))
        label_arm = "supervision" if label in ("present", "absent") else "unusable"
        stats.rendered += 1
        stats.provenance_rows.append(
            {
                "anchor_id": anchor_id,
                "chip_id": ct_row.get("chip_id", ""),
                "capture_date": capture_date,
                "version": version,
                "actual_zoom": dl.actual_zoom,
                "download_status": dl.status,
                "chip_sha256": dl.sha256,
                "tif_path": str(dl.path),
                "png_path": str(png),
                "geometry_version": geometry_version,  # RECORDED PROVENANCE only
                # slice-4 student-render provenance (inert for the slice-2 writer,
                # which declares neither column): marker overlay + supervision arm.
                "marker": "drawn" if draw_marker else "none",
                "label_arm": label_arm,
            }
        )
    return stats


# --------------------------------------------------------------------------- #
# student-chip row selection (slice-4: marker-free + unusable-class supervision)
# --------------------------------------------------------------------------- #
def select_student_rows(
    manifest_rows: Sequence[Mapping[str, object]],
    subset_ids: Iterable[str],
) -> list[dict[str, object]]:
    """Select the manifest rows to re-render as marker-free STUDENT chips (D4).

    A row is kept iff it is a subset anchor, is re-renderable (non-empty
    ``version`` — census/label-only rows carry ``version=""`` and are excluded),
    AND is one of:

    - a present/absent supervision round, or
    - a **quality-driven** ``unusable`` round (haze/cloud on a healthy anchor).

    The ``done_ambiguous_*`` exclusion COMPOSES with the D12.iii rule: a
    marker-misregistration-tainted series must never become ``unusable``-class
    supervision (its whole observation series is untrustworthy), whereas
    quality-driven unusable rows on non-ambiguous anchors are exactly what the
    ``unusable`` class must learn. This is a superset of ``render-chips``'s
    present/absent-only selection — the slice-2 render never covered the
    unusable class.
    """
    subset = {str(a) for a in subset_ids}
    out: list[dict[str, object]] = []
    for r in manifest_rows:
        if str(r.get("anchor_id", "")) not in subset:
            continue
        if str(r.get("version", "")) == "":
            continue  # census / label-only rounds are not re-renderable
        label = str(r.get("label_3class", ""))
        terminal = str(r.get("terminal_status", ""))
        if label in ("present", "absent"):
            out.append(dict(r))
        elif label == "unusable" and not terminal.startswith("done_ambiguous_"):
            out.append(dict(r))
    return out


# --------------------------------------------------------------------------- #
# README / provenance writers
# --------------------------------------------------------------------------- #
def write_manifest(rows: Sequence[Mapping[str, object]], path: Path) -> None:
    write_csv_rows(path, rows, MANIFEST_COLUMNS)


def build_readme(
    stats: HarvestStats,
    *,
    high_conf: float,
    subset_anchor_ids: Sequence[str],
    render_stats: RenderStats | None,
    cbd_resolved: bool,
    geometry_version: str,
    strata_counts: Mapping[str, Mapping[str, int]] | None = None,
) -> str:
    lines = [
        "# dinov3 distillation training set (ISSUE-02)",
        "",
        "Harvested from the retained 4-corpus label store — no new LLM calls. The "
        "corpora are three scan-state corpora (chip-group + per-target + wayback) "
        "and census2023 (`census_audit/*.jsonl`, label-only).",
        "",
        "## Corpus sizing (re-derived on disk — authoritative)",
        f"- unique anchors harvested: {stats.unique_anchors} "
        f"(D12 reference: {EXPECTED_UNIQUE_ANCHORS})",
        f"- total RoundResults post-dedup: {stats.total_rounds} "
        f"(D12 reference: {EXPECTED_ROUNDS})",
        f"- duplicate rounds collapsed on (anchor,capture_date,version): "
        f"{stats.duplicate_rounds_collapsed}",
        f"- census2023 rows / anchors: {stats.census_rows} / {stats.census_anchors} "
        f"(label-only, version=\"\", NOT in the re-render subset)",
        f"- census2023 dropped — malformed/ok=false: "
        f"{stats.census_malformed_dropped}; missing interval join: "
        f"{stats.census_missing_interval_dropped}",
        f"- done_ambiguous_* anchors (excluded from present/absent, kept as "
        f"unusable): {stats.ambiguous_anchors}",
        "- per-corpus unique-anchor ownership (first corpus wins on overlap): "
        + ", ".join(f"{k}={v}" for k, v in stats.corpus_anchor_counts.most_common()),
        "",
        "### D12 reconcile — the reference counts were inconsistent subsets",
        "D12's \"23,147 unique / 250,502 rounds\" were computed on INCONSISTENT "
        "subsets: 23,147 = chip_group+per_target+wayback anchors, while 250,502 = "
        "chip_group+per_target RoundResults ONLY (wayback and census excluded). "
        "Harvesting census2023 per this issue's corpus definition changes BOTH "
        "totals (census adds new c-namespace anchors and label rows), so the "
        "re-derived totals above are authoritative and D12 should be renumbered "
        "against them.",
    ]
    if stats.unique_anchors:
        dev_a = abs(stats.unique_anchors - EXPECTED_UNIQUE_ANCHORS) / EXPECTED_UNIQUE_ANCHORS
        if dev_a > 0.02:
            lines.append(
                f"- NOTE: unique-anchor count deviates {dev_a:.1%} from the D12 "
                f"reference {EXPECTED_UNIQUE_ANCHORS} (expected: census2023 adds new "
                f"anchors; also check corpus roots / --limit) — recorded, not fatal."
            )
    if stats.total_rounds:
        dev_r = abs(stats.total_rounds - EXPECTED_ROUNDS) / EXPECTED_ROUNDS
        if dev_r > 0.02:
            lines.append(
                f"- NOTE: total-rounds count deviates {dev_r:.1%} from the D12 "
                f"reference {EXPECTED_ROUNDS} (expected: reference excluded "
                f"wayback+census rounds; also check --limit) — recorded, not fatal."
            )
    lines += [
        "",
        "## Label rule",
        f"- HIGH_CONF = {high_conf} (NEW recorded parameter; no prior threshold "
        f"existed in the presence stack)",
        "- present/absent only from usable + confidence in [HIGH_CONF, 1.0] on a "
        "non-done_ambiguous_* anchor; else unusable.",
        "",
        "## Label counts",
        *(f"- {k}: {v}" for k, v in sorted(stats.label_counts.items())),
        "",
        "## Terminal-status counts (unique anchors)",
        *(f"- {k}: {v}" for k, v in stats.status_counts.most_common()),
        "",
        "## sub_domain (unique anchors)",
        f"- CBD footprint resolved from gpkg: {cbd_resolved}",
        *(f"- {k}: {v}" for k, v in sorted(stats.sub_domain_counts.items())),
        "",
        "## Stratified chip subset",
        f"- selected anchors: {len(subset_anchor_ids)}",
        f"- re-render geometry (render_geometry): {geometry_version}",
        "- census2023 rows are LABEL-POOL-ONLY: their chips were never persisted "
        "and used wb/tm provider tags (not integer GEID versions), so they carry "
        "version=\"\" and are EXCLUDED from the re-render subset. Re-rendering them "
        "is a bounded follow-up, not part of this issue.",
    ]
    if strata_counts:
        lines += [
            "",
            "### Per-stratum counts (sub_domain x dominant-label: selected / eligible)",
            *(
                f"- {key}: {cell.get('selected', 0)} / {cell.get('eligible', 0)}"
                for key, cell in sorted(strata_counts.items())
            ),
        ]
    if render_stats is not None:
        lines += [
            "",
            "### Chip re-render drop counts",
            f"- chips rendered: {render_stats.rendered}",
            f"- dropped (unrecoverable / all_zooms_failed): "
            f"{render_stats.dropped_unrecoverable}",
            f"- dropped (unresolvable chip_targets join): "
            f"{render_stats.dropped_unresolved}",
        ]
    else:
        lines.append(
            "- (chip re-render not run yet; drop counts land here after "
            "`render-chips`)"
        )
    lines += [
        "",
        "## Distillation-label caveat (teacher/student geometry mismatch)",
        f"- `label_render_geometry` = {LEGACY_GEOMETRY_VERSION} for ALL rows: the "
        f"harvested teacher labels were produced on v1 (banked96) geometry, while "
        f"the student chips re-render at {geometry_version} "
        f"(`render_geometry`). The manifest keeps both as distinct columns so the "
        "mismatch is explicit and downstream-stratifiable.",
        "- Disposition (per ISSUE-02): option (2) exclude/down-weight COMPOSES "
        "WITH — does not duplicate — the 27.6% `done_ambiguous_*` exclusion "
        "(the worst v1-artifact stratum, `done_ambiguous_gemini_failed`, is "
        "already removed by that rule); option (1) accepts + records the residual "
        "small/hard label noise via `label_render_geometry`; option (3) direct "
        "measurement (re-score a v2 calibration subset with the teacher) is "
        "DEFERRED to ISSUE-04 co-teacher dual-scoring — it needs LLM calls, which "
        "this issue forbids.",
        "",
        "All artifacts under ~/zasolar_data/ (gitignored).",
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _cmd_harvest(args: argparse.Namespace) -> None:
    cbd_grids = resolve_cbd_jnb_grids(args.cbd_gpkg)
    corpus_dirs = dict(CORPUS_DIRS)
    # harvest_all (NOT harvest_corpus): merges census2023, dedups rounds on
    # (anchor_id, capture_date, version), and re-derives reconciled 4-corpus
    # stats — criteria 2 (post-dedup, all four corpora) + 7 (census label-only
    # anchors). --no-census2023 opts out for a scan-only sanity run.
    rows, stats = harvest_all(
        corpus_dirs,
        cbd_grids=cbd_grids,
        high_conf=args.high_conf,
        limit=args.limit,
        include_census=not args.no_census2023,
    )
    assignment = assign_splits(
        sorted({str(r["anchor_id"]) for r in rows}),
        heldout_frac=args.heldout_frac,
        salt=args.split_salt,
    )
    train_anchors = {a for a, s in assignment.items() if s == "train"}
    heldout_anchors = {a for a, s in assignment.items() if s == "heldout"}
    assert train_anchors.isdisjoint(heldout_anchors), "train/heldout split leaked"
    for r in rows:
        r["split"] = assignment[str(r["anchor_id"])]

    subset = select_chip_subset(rows, target_n=args.subset_n, salt=args.subset_salt)
    strata_counts = summarize_subset_strata(rows, subset)

    out = Path(args.output_root)
    out.mkdir(parents=True, exist_ok=True)
    write_manifest(rows, out / "label_manifest.csv")
    (out / "chip_subset_anchors.json").write_text(
        json.dumps(sorted(subset), indent=2), encoding="utf-8"
    )
    # Persist reconciled stats + render-time context so `render-chips` can
    # regenerate the README with drop counts without re-harvesting (the
    # non-manifest reconcile fields — census/dedup counts — are not recoverable
    # from the manifest alone).
    (out / "harvest_meta.json").write_text(
        json.dumps(
            {
                "stats": stats.to_dict(),
                "high_conf": args.high_conf,
                "cbd_resolved": cbd_grids is not None,
                "geometry_version": args.chip_geometry,
                "subset_anchor_ids": sorted(subset),
                "strata_counts": strata_counts,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (out / "README.md").write_text(
        build_readme(
            stats,
            high_conf=args.high_conf,
            subset_anchor_ids=subset,
            render_stats=None,
            cbd_resolved=cbd_grids is not None,
            geometry_version=args.chip_geometry,
            strata_counts=strata_counts,
        ),
        encoding="utf-8",
    )
    print(
        f"harvested {stats.unique_anchors} anchors / {stats.total_rounds} rounds; "
        f"census_rows={stats.census_rows}/anchors={stats.census_anchors}; "
        f"deduped={stats.duplicate_rounds_collapsed}; subset={len(subset)}; "
        f"wrote {out}/label_manifest.csv"
    )


def _cmd_render_chips(args: argparse.Namespace) -> None:
    out = Path(args.output_root)
    subset_ids = json.loads((out / "chip_subset_anchors.json").read_text())
    manifest = read_csv_rows(out / "label_manifest.csv")
    subset_set = set(subset_ids)
    # one round per (anchor, capture_date, version) for the selected anchors,
    # restricted to usable present/absent rows (the supervision rounds)
    subset_rows = [
        r
        for r in manifest
        if r["anchor_id"] in subset_set and r["label_3class"] in ("present", "absent")
    ]
    render_stats = render_subset(
        subset_rows,
        chip_targets_path=args.chip_targets,
        geometry_version=args.chip_geometry,
        output_root=out / "chips",
    )
    write_csv_rows(
        out / "chip_render_provenance.csv",
        render_stats.provenance_rows,
        (
            "anchor_id", "chip_id", "capture_date", "version", "actual_zoom",
            "download_status", "chip_sha256", "tif_path", "png_path",
            "geometry_version",
        ),
    )
    # drops.json carries the summary counts (not just the raw drop_log) so the
    # unrecoverable/unresolved drop totals are documented on disk, not only stdout.
    (out / "chip_render_drops.json").write_text(
        json.dumps(
            {
                "rendered": render_stats.rendered,
                "dropped_unrecoverable": render_stats.dropped_unrecoverable,
                "dropped_unresolved": render_stats.dropped_unresolved,
                "drop_log": render_stats.drop_log,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    # Regenerate the README so the promised drop counts + per-stratum breakdown
    # replace the harvest-time "chip re-render not run yet" placeholder
    # (criterion 10). Reconciled harvest stats come from harvest_meta.json when
    # present; else fall back to re-deriving marginals from the manifest.
    meta_path = out / "harvest_meta.json"
    if meta_path.exists():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        stats = HarvestStats.from_dict(meta.get("stats", {}))
        high_conf = float(meta.get("high_conf", HIGH_CONF_DEFAULT))
        cbd_resolved = bool(meta.get("cbd_resolved", False))
        strata_counts = meta.get("strata_counts") or summarize_subset_strata(
            manifest, subset_ids
        )
    else:
        stats = compute_stats(manifest)
        high_conf = HIGH_CONF_DEFAULT
        cbd_resolved = any(
            r.get("sub_domain") == SUB_DOMAIN_CBD for r in manifest
        )
        strata_counts = summarize_subset_strata(manifest, subset_ids)
    (out / "README.md").write_text(
        build_readme(
            stats,
            high_conf=high_conf,
            subset_anchor_ids=subset_ids,
            render_stats=render_stats,
            cbd_resolved=cbd_resolved,
            geometry_version=args.chip_geometry,
            strata_counts=strata_counts,
        ),
        encoding="utf-8",
    )
    print(
        f"rendered {render_stats.rendered}; "
        f"dropped_unrecoverable={render_stats.dropped_unrecoverable}; "
        f"dropped_unresolved={render_stats.dropped_unresolved}"
    )


# slice-4 student-render provenance schema (marker + label_arm appended to the
# slice-2 chip-provenance columns). Distinct from chip_render_provenance.csv:
# these chips are marker-free (PRD D4) and cover the unusable class too.
STUDENT_PROVENANCE_COLUMNS: tuple[str, ...] = (
    "anchor_id", "chip_id", "capture_date", "version", "actual_zoom",
    "download_status", "chip_sha256", "tif_path", "png_path",
    "geometry_version", "marker", "label_arm",
)


def _cmd_render_student_chips(args: argparse.Namespace) -> None:
    """Re-render the subset as marker-free STUDENT chips (PRD D4, slice-4).

    Reads the frozen slice-2 manifest + subset list, selects present/absent
    supervision rows AND quality-driven unusable rows (``select_student_rows``),
    and re-renders them marker-free (``draw_marker=False``) through the same
    idempotent GEHI download + scoring-crop machinery. Downloads land in the
    SHARED ``chips/`` dir so present/absent tifs already on disk are reused
    (``skipped_existing``); the marker-free PNGs are ``.nomarker.png`` siblings,
    so nothing overwrites the slice-2 marked render. Writes ONLY the
    ``student_chip_render_*`` outputs — the slice-2
    ``chip_render_provenance.csv`` / ``chip_render_drops.json`` / ``README.md``
    stay frozen.
    """
    out = Path(args.output_root)
    subset_ids = json.loads((out / "chip_subset_anchors.json").read_text())
    manifest = read_csv_rows(out / "label_manifest.csv")
    subset_rows = select_student_rows(manifest, subset_ids)
    render_stats = render_subset(
        subset_rows,
        chip_targets_path=args.chip_targets,
        geometry_version=args.chip_geometry,
        # SHARED chips/ dir: present/absent tifs re-render as skip-existing, the
        # marker-free PNGs coexist as .nomarker.png siblings of the same tif.
        output_root=out / "chips",
        draw_marker=False,
    )
    write_csv_rows(
        out / "student_chip_render_provenance.csv",
        render_stats.provenance_rows,
        STUDENT_PROVENANCE_COLUMNS,
    )
    (out / "student_chip_render_drops.json").write_text(
        json.dumps(
            {
                "rendered": render_stats.rendered,
                "dropped_unrecoverable": render_stats.dropped_unrecoverable,
                "dropped_unresolved": render_stats.dropped_unresolved,
                "drop_log": render_stats.drop_log,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(
        f"student chips (marker-free) rendered {render_stats.rendered}; "
        f"dropped_unrecoverable={render_stats.dropped_unrecoverable}; "
        f"dropped_unresolved={render_stats.dropped_unresolved}"
    )


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Build the dinov3-scorer distillation set (ISSUE-02).")
    sub = p.add_subparsers(dest="command", required=True)

    h = sub.add_parser("harvest", help="harvest label manifest + split + subset")
    h.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    h.add_argument("--high-conf", type=float, default=HIGH_CONF_DEFAULT)
    h.add_argument("--heldout-frac", type=float, default=0.20)
    h.add_argument("--split-salt", default="dinov3_distill_v1")
    h.add_argument("--subset-salt", default="dinov3_distill_subset_v1")
    h.add_argument("--subset-n", type=int, default=800)
    h.add_argument("--limit", type=int, default=None, help="cap state files read (sanity runs)")
    h.add_argument(
        "--no-census2023",
        action="store_true",
        help="skip the census2023 corpus (scan-only sanity run; opts out of the "
        "required-corpus loud failure on a missing census audit dir)",
    )
    h.add_argument("--cbd-gpkg", type=Path, default=DEFAULT_CBD_GPKG)
    h.add_argument("--chip-geometry", default=RERENDER_GEOMETRY_VERSION)
    h.set_defaults(func=_cmd_harvest)

    r = sub.add_parser("render-chips", help="re-render the stratified subset via GEHI")
    r.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    r.add_argument("--chip-targets", type=Path, default=CHIP_TARGETS_CSV)
    r.add_argument("--chip-geometry", default=RERENDER_GEOMETRY_VERSION)
    r.set_defaults(func=_cmd_render_chips)

    s = sub.add_parser(
        "render-student-chips",
        help="re-render the subset MARKER-FREE (PRD D4) incl. unusable-class rounds",
    )
    s.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    s.add_argument("--chip-targets", type=Path, default=CHIP_TARGETS_CSV)
    s.add_argument("--chip-geometry", default=RERENDER_GEOMETRY_VERSION)
    s.set_defaults(func=_cmd_render_student_chips)

    return p.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    args = parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
