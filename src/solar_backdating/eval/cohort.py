"""UNIT B — cohort builder: anchors -> ``CensoringInterval``s for the Turnbull fit.

Maps every anchor (a production ``scan_state.json`` or a banked ``long_all.csv``
panel row) onto one interval-censored install observation over calendar time.
The mapping reproduces ``scripts/temporal/infer_install_dates.infer_one``'s bound
rules AND its status reclassification for the non-ambiguous statuses (so the
per-status marginals match production, AC1), then applies a single
maximal-usable-absent-prefix rule to recover EVERY ``done_ambiguous_*`` status
as a censored observation instead of dropping it (the ~23% of rows ISSUE-03
exists to recover).

Bound semantics (mirrors ``infer_one``):

- Only USABLE observations define bounds: ``quality_flag == "usable"`` and
  ``pv_present in ("0", "1")`` (an abstain/blank verdict is not a usable bound).
- ``done_appears``: ``(latest usable absent, earliest usable present]``, the
  present side clamped to the grid's Vexcel flight date. If the clamp inverts
  (``latest_absent > ceiling``) the observation is reclassified
  ``done_ambiguous_clamp_inverted`` and recovered by the prefix rule.
- ``done_installed_during_census``: ``(latest usable absent, ceiling-or-census]``.
  If the latest absent is at/after that upper bound it is reclassified
  ``done_ambiguous_marker_missed_pv`` and recovered.
- ``done_already_present_before_geid_history``: left-censored, upper = earliest
  usable observation clamped to the ceiling.
- ``done_ambiguous_*`` (recovery, ``recovered=True``, status preserved): upper =
  ceiling-or-census; lower = the latest usable absent strictly before the upper
  bound within the MAXIMAL ABSENT PREFIX (the leading run of absents before the
  first usable present). Post-present and post-flight absents fall out
  naturally, so no negative-width interval is ever emitted.
- ``scanning`` / unknown -> ``None`` (dropped, counted separately).

Read-only over its inputs; writes nothing. Determinism: usable observations are
sorted by ``(capture_date, source_row)`` before any extremum/prefix scan, so the
bounds are byte-identical regardless of input order.
"""
from __future__ import annotations

import csv
import json
import statistics
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from datetime import date
from pathlib import Path
from typing import Literal

from solar_backdating.estimators import VintageObservation
from solar_backdating.estimators.survival import CensoringInterval
from solar_backdating.eval import scan_state_io

__all__ = [
    "GLOBAL_CENSUS_MID",
    "load_grid_ceilings",
    "interval_from_observations",
    "intervals_from_scan_states",
    "intervals_from_panel",
    "interval_counts",
    "stratify_intervals",
]

# Global census-imagery upper bound when a grid-specific Vexcel flight date is
# unavailable (mirrors infer_install_dates' --census-mid-date default).
GLOBAL_CENSUS_MID = date(2024, 6, 30)


def load_grid_ceilings(csv_path: Path | None) -> dict[str, date]:
    """``grid_id -> last_capture_date`` present-side detection ceiling.

    Mirrors ``infer_install_dates.load_vexcel_capture_dates``: reads the
    ``grid_id`` and ``last_capture_date`` columns and parses the first 10 chars
    of the timestamp as an ISO date. Returns ``{}`` when ``csv_path`` is ``None``
    or the file is absent (the census-mid fallback then applies everywhere).
    """
    if csv_path is None:
        return {}
    path = Path(csv_path)
    if not path.exists():
        return {}
    out: dict[str, date] = {}
    with path.open("r", newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            gid = str(row.get("grid_id", "")).strip()
            raw = str(row.get("last_capture_date", "")).strip()
            if not gid or not raw:
                continue
            try:
                out[gid] = date.fromisoformat(raw[:10])
            except ValueError:
                continue
    return out


def _usable_sorted(observations: Sequence[VintageObservation]) -> list[tuple[date, str]]:
    """``(capture_date, pv_present)`` for usable observations, ascending by
    ``(date, source_row)`` (deterministic, matches ``infer_one._usable``)."""
    rows = [
        (o.capture_date, o.pv_present, o.source_row or 0)
        for o in observations
        if o.quality_flag == "usable" and o.pv_present in ("0", "1")
    ]
    rows.sort(key=lambda t: (t[0], t[2]))
    return [(d, p) for d, p, _ in rows]


def _median_cadence(usable: Sequence[tuple[date, str]]) -> float | None:
    """Median inter-usable-vintage gap in days (``None`` when < 2 usable)."""
    if len(usable) < 2:
        return None
    dates = sorted(d for d, _ in usable)
    diffs = [(dates[i + 1] - dates[i]).days for i in range(len(dates) - 1)]
    return float(statistics.median(diffs))


def _last_confident_absent(usable: Sequence[tuple[date, str]], upper: date) -> date | None:
    """Latest usable absent strictly before ``upper`` within the maximal absent
    prefix — the leading run of absents that precedes the first usable present.

    ``usable`` must be ascending. Iterate the prefix, keeping the latest absent
    with ``date < upper``; stop at the first present. Because the returned date
    is always ``< upper``, an interval built from it can never invert.
    """
    best: date | None = None
    for d, p in usable:
        if p == "1":  # first usable present ends the prefix
            break
        if d < upper:
            best = d
    return best


def interval_from_observations(
    observations: Sequence[VintageObservation],
    status: str,
    *,
    ceiling: date | None,
    census_mid: date = GLOBAL_CENSUS_MID,
    anchor_id: str = "",
    grid_id: str = "",
) -> CensoringInterval | None:
    """Map one anchor's usable observations + terminal status to an interval.

    Returns ``None`` only for ``scanning`` / unknown statuses. Every terminal
    status yields a mapped ``CensoringInterval`` (interval or left-censored).
    """
    usable = _usable_sorted(observations)
    n_usable = len(usable)
    cadence = _median_cadence(usable)
    absents = [d for d, p in usable if p == "0"]
    presents = [d for d, p in usable if p == "1"]

    def make(lower: date | None, upper: date | None, kind: str, *, st: str,
             recovered: bool) -> CensoringInterval:
        return CensoringInterval(
            lower=lower,
            upper=upper,
            kind=kind,
            anchor_id=anchor_id,
            grid_id=grid_id,
            status=st,
            recovered=recovered,
            n_usable=n_usable,
            cadence_gap_days=cadence,
        )

    def recover(st: str) -> CensoringInterval:
        upper = ceiling if ceiling is not None else census_mid
        lower = _last_confident_absent(usable, upper)
        if lower is None:
            return make(None, upper, "left", st=st, recovered=True)
        return make(lower, upper, "interval", st=st, recovered=True)

    if status == "done_appears":
        if absents and presents:
            lower = max(absents)
            upper = min(presents)
            if ceiling is not None and upper > ceiling:
                upper = ceiling
            if lower > upper:  # clamp inverted -> recover as ambiguous
                return recover("done_ambiguous_clamp_inverted")
            return make(lower, upper, "interval", st="done_appears", recovered=False)
        if presents:  # no usable absent -> left-censored
            upper = min(presents)
            if ceiling is not None and upper > ceiling:
                upper = ceiling
            return make(None, upper, "left", st="done_appears", recovered=False)
        # Degenerate done_appears without any usable present: undated left bound.
        upper = ceiling if ceiling is not None else census_mid
        return make(None, upper, "left", st="done_appears", recovered=False)

    if status == "done_installed_during_census":
        upper = ceiling if ceiling is not None else census_mid
        if absents:
            lower = max(absents)
            if lower >= upper:  # contradicts census-GT prior -> recover
                return recover("done_ambiguous_marker_missed_pv")
            return make(lower, upper, "interval",
                        st="done_installed_during_census", recovered=False)
        return make(None, upper, "left",
                    st="done_installed_during_census", recovered=False)

    if status == "done_already_present_before_geid_history":
        if usable:
            upper = min(d for d, _ in usable)
            if ceiling is not None and upper > ceiling:
                upper = ceiling
            return make(None, upper, "left", st=status, recovered=False)
        upper = ceiling if ceiling is not None else census_mid
        return make(None, upper, "left", st=status, recovered=False)

    if status.startswith("done_ambiguous_"):
        return recover(status)

    # scanning / unknown -> dropped (counted by the caller as n_dropped)
    return None


def intervals_from_scan_states(
    paths: Iterable[Path],
    *,
    ceilings: dict[str, date],
    census_mid: date = GLOBAL_CENSUS_MID,
) -> list[CensoringInterval]:
    """PRIMARY builder: production ``scan_state.json`` files -> intervals.

    Per file: ``json.load`` the top-level ``{status, grid_id, anchor_id}`` and
    read observations via ``scan_state_io.load_scan_observations`` (the single
    flatten authority). READ-ONLY — never rewrites a file. An unloadable file
    raises (never silently dropped). ``scanning`` / unknown statuses map to
    ``None`` and are omitted from the returned list.
    """
    out: list[CensoringInterval] = []
    for path in paths:
        p = Path(path)
        top = json.loads(p.read_text())
        status = top.get("status", "")
        grid_id = top.get("grid_id", "") or ""
        anchor_id = top.get("anchor_id", "") or ""
        observations = scan_state_io.load_scan_observations(p)
        ceiling = ceilings.get(grid_id)
        iv = interval_from_observations(
            observations,
            status,
            ceiling=ceiling,
            census_mid=census_mid,
            anchor_id=anchor_id,
            grid_id=grid_id,
        )
        if iv is not None:
            out.append(iv)
    return out


def intervals_from_panel(
    panel: dict,
    chip_of: dict,
    strata: dict[str, str],
    *,
    ceilings: dict[str, date],
    census_mid: date = GLOBAL_CENSUS_MID,
    rep: str | None = None,
) -> list[CensoringInterval]:
    """SECONDARY builder: banked Panel A/B (``load_panel`` output) -> intervals.

    ``status := strata[chip_of[unit]]`` (the ``status_stratum`` column IS the
    terminal status). One interval per unit from ``rep`` (default: the first
    sorted rep present for that unit). ``grid_id`` is not carried in the panel,
    so no per-grid clamp is applied — the census-mid fallback governs the upper
    bound (``ceilings`` is accepted for signature symmetry).
    """
    out: list[CensoringInterval] = []
    for unit in sorted(panel):
        reps = panel[unit]
        if not reps:
            continue
        use_rep = rep if (rep is not None and rep in reps) else sorted(reps)[0]
        observations = reps[use_rep]
        status = strata.get(chip_of.get(unit, ""), "")
        anchor_id = unit[1] if isinstance(unit, tuple) and len(unit) > 1 else str(unit)
        iv = interval_from_observations(
            observations,
            status,
            ceiling=None,
            census_mid=census_mid,
            anchor_id=anchor_id,
            grid_id="",
        )
        if iv is not None:
            out.append(iv)
    return out


def interval_counts(intervals: Sequence[CensoringInterval]) -> dict:
    """AC1 marginals: totals by status, by kind, and the recovered-ambiguous
    count. The caller reports ``n_dropped`` (``n_input - n``) separately."""
    by_status: Counter[str] = Counter()
    by_kind: Counter[str] = Counter()
    n_recovered = 0
    for iv in intervals:
        by_status[iv.status] += 1
        by_kind[iv.kind] += 1
        if iv.recovered:
            n_recovered += 1
    return {
        "n": len(intervals),
        "by_status": dict(by_status),
        "by_kind": {
            "interval": by_kind.get("interval", 0),
            "left": by_kind.get("left", 0),
            "right": by_kind.get("right", 0),
        },
        "n_recovered": n_recovered,
    }


def _cadence_bucket(gap: float | None) -> str:
    if gap is None:
        return "unknown"
    if gap <= 90:
        return "<=90"
    if gap <= 365:
        return "90-365"
    return ">365"


def stratify_intervals(
    intervals: Sequence[CensoringInterval],
    key: Literal["status", "region", "cadence"],
) -> dict[str, list[CensoringInterval]]:
    """Pure grouping (no curves — keeps cohort building orthogonal to
    aggregation). ``region := grid_id[:3]`` (e.g. ``JNB``) else ``UNK``;
    ``cadence`` buckets the median gap into ``<=90 / 90-365 / >365 / unknown``.
    """
    groups: dict[str, list[CensoringInterval]] = defaultdict(list)
    for iv in intervals:
        if key == "status":
            k = iv.status
        elif key == "region":
            k = iv.grid_id[:3] if iv.grid_id else "UNK"
        elif key == "cadence":
            k = _cadence_bucket(iv.cadence_gap_days)
        else:
            raise ValueError(f"unknown stratify key {key!r}")
        groups[k].append(iv)
    return dict(groups)
