"""Cohort-scale join / schema / gates for the CoJ true-date audit (ISSUE-09 WP-D).

This is the scientific core of the cohort audit: it turns the long list of
*planned fetch units* (one per anchor × layer-year, produced by the cohort
builder) plus their fetch/score outcomes into

  * the **long** bit table (`cohort_audit_bits.csv`, source of truth — one row
    per planned unit, so the denominator is every unit that was ever planned),
  * the **wide** per-anchor table (`cohort_audit_anchors.csv`, the ISSUE-10
    sampler / event-study facing pivot),
  * the four cohort gates (present-side known-sign agreement, negative-control
    false-present, within-audit monotonicity noise floor, clamp findings),
  * the headline per-stratum contradiction-rate table, and
  * coverage accounting.

Like the pilot's ``coj_audit_join`` this module is pure: no network, no GPU, no
filesystem I/O — every function operates on plain dicts / lists so it is
directly unit-testable (``tests/audit/test_coj_cohort_join.py``). The
orchestrator (WP-E) and the report renderer (``coj_cohort_report``) are the
only modules that touch CSVs.

**Frozen primitives are imported, never re-derived.** The margin rule
(0.30/0.95 + classifier demotion) and the conservative year-only contradiction
bounds live *only* in the pilot's ``coj_audit_join``; this module imports
``classify_bit`` / ``contradicts_present`` / ``contradicts_absent`` /
``PRESENT_FLOOR`` / ``ABSENT_CEILING`` / ``EXCLUDED_STATUSES`` / ``_parse_date``
from there so cohort scale and pilot stay provably identical.

**Per-unit known-sign expectation (design AMENDMENT).** Unlike the pilot's
stratum-blanket ``expected_sign``, the cohort expectation is *per unit*:

  * a dated unit ``(anchor, year Y)`` expects **present** iff its
    ``install_interval_end`` parses and is *strictly before* Jan-1 of year Y
    (the pipeline already claims the install predates the layer);
  * a negative-control unit expects **absent**;
  * every other unit carries **no** expectation (blank).

There is deliberately no per-unit *absent* expectation for dated anchors: a
falsification unit's "absent" is the pipeline's own claim, not ground truth
(this is the ISSUE-08 s3 reclassification), so it never enters a known-sign
gate — it can only raise a contradiction.
"""
from __future__ import annotations

import math
from datetime import date
from typing import Any, Iterable, Mapping

# Read-only reuse of the pilot's frozen primitives (the ONE place the margin
# rule + conservative contradiction bounds live).
from scripts.audit.coj_audit_join import (  # noqa: F401  (PRESENT_FLOOR/ABSENT_CEILING re-exported)
    ABSENT_CEILING,
    EXCLUDED_STATUSES,
    PRESENT_FLOOR,
    _parse_date,
    classify_bit,
    contradicts_absent,
    contradicts_present,
)

# ---------------------------------------------------------------------------
# Cohort constants (partition + layer plan). These string values are the
# integration contract shared with the cohort builder (WP-A) and the schema
# doc; keep them in sync with docs/replan_v2/ISSUE-09-cohort-schema.md.
# ---------------------------------------------------------------------------

LAYER_YEARS: tuple[int, int, int] = (2015, 2019, 2023)

NEGATIVE_CONTROL_STRATUM = "c_negative_control"

COHORT_STRATA: tuple[str, ...] = (
    "c_cal_present_pre2019",
    "c_cal_present_2019_2022",
    "c_probe_2023",
    "c_findings_s3like",
    NEGATIVE_CONTROL_STRATUM,
)

# bit values
BIT_PRESENT = "present"
BIT_ABSENT = "absent"
BIT_LOW_MARGIN = "low_margin"
BIT_NO_COVERAGE = "no_coverage"
BIT_FETCH_FAILED = "fetch_failed"
_HIGH_MARGIN_BITS = frozenset({BIT_PRESENT, BIT_ABSENT})
# a bit that came from actually scoring a chip (the detector ran): present /
# absent / low_margin. no_coverage / fetch_failed are coverage states, NOT a
# scored presence measurement.
_SCORED_BITS = frozenset({BIT_PRESENT, BIT_ABSENT, BIT_LOW_MARGIN})
# a planned unit is "covered" (not a coverage gap) iff its computed presence
# bit is one of these — i.e. anything except fetch_failed (schema doc: a
# no_coverage/empty response is a real terminal answer, fetch_failed is a gap).
_COVERED_BITS = frozenset({BIT_PRESENT, BIT_ABSENT, BIT_LOW_MARGIN, BIT_NO_COVERAGE})

# fetch_outcome buckets
_SCORABLE_OUTCOMES = frozenset({"ok", "skipped_existing"})
_NO_COVERAGE_OUTCOME = "empty"
# a planned unit is "covered" (not a fetch gap) iff its fetch reached one of
# these terminal outcomes. Used only as a fallback when a bit row carries no
# computed `bit` (coverage keys on the presence bit when it is present).
COVERED_FETCH_OUTCOMES = frozenset({"ok", "skipped_existing", "empty"})

# negative-control gate thresholds (design §3): PASS iff <= this many controls
# show a high-margin present bit AND the gate is evaluable (enough controls
# actually produced a scored presence bit — a calibration gate that never ran
# on any control must not certify the absent-side thresholds vacuously).
NC_FALSE_PRESENT_THRESHOLD = 2
NC_SCORED_THRESHOLD = 0.95

# known-sign / coverage acceptance thresholds
KNOWN_SIGN_THRESHOLD = 0.95
COVERAGE_THRESHOLD = 0.95

# schema column order (mirrored in ISSUE-09-cohort-schema.md — keep in sync).
LONG_COLUMNS: tuple[str, ...] = (
    "anchor_id", "stratum", "year", "unit_purpose", "status", "confidence",
    "install_interval_start", "install_interval_end", "chip_path", "fetch_outcome",
    "detector_S", "classifier_pv_prob", "bit", "expected", "agrees",
    "contradicts_interval", "routed_to_queue",
)

WIDE_COLUMNS: tuple[str, ...] = (
    "anchor_id", "stratum", "status", "confidence",
    "install_interval_start", "install_interval_end",
    "latest_absent_date", "earliest_present_date", "grid_id", "is_negative_control",
    "bit_2015", "detector_S_2015", "classifier_pv_prob_2015", "contradicts_2015",
    "bit_2019", "detector_S_2019", "classifier_pv_prob_2019", "contradicts_2019",
    "bit_2023", "detector_S_2023", "classifier_pv_prob_2023", "contradicts_2023",
    "any_contradiction", "n_contradictions", "n_high_margin_bits", "first_present_year",
)


# ---------------------------------------------------------------------------
# tolerant coercion helpers (functions accept either pure dicts from
# build_cohort_bit — bools/floats — or string rows read back from a CSV)
# ---------------------------------------------------------------------------


def _truthy(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value).strip().lower() in ("true", "1", "yes")


def _parse_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text or text.lower() in ("none", "nan", ""):
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _is_negative_control(row: Mapping) -> bool:
    if _truthy(row.get("is_negative_control")):
        return True
    if row.get("stratum") == NEGATIVE_CONTROL_STRATUM:
        return True
    if row.get("unit_purpose") == "negative_control":
        return True
    anchor_id = row.get("anchor_id")
    return isinstance(anchor_id, str) and anchor_id.startswith("nc_")


# ---------------------------------------------------------------------------
# Per-unit known-sign expectation (design AMENDMENT)
# ---------------------------------------------------------------------------


def expected_bit(row: dict, year: int) -> str | None:
    """Per-unit known-sign expectation: ``'present'`` / ``'absent'`` / ``None``.

    * negative-control unit  -> ``'absent'`` (the audit instrument should read
      absent there; a high-margin present here is a false-present).
    * dated unit whose ``install_interval_end`` parses and is strictly before
      Jan-1 of ``year`` -> ``'present'`` (pipeline already claims the install
      predates the layer year).
    * everything else -> ``None`` (no expectation; e.g. a falsification unit's
      pipeline-claimed absence is NOT a ground-truth expectation).
    """
    if _is_negative_control(row):
        return "absent"
    end = _parse_date(row.get("install_interval_end"))
    if end is not None and end < date(int(year), 1, 1):
        return "present"
    return None


# ---------------------------------------------------------------------------
# build_cohort_bit — one long (§4a) bit row per planned unit
# ---------------------------------------------------------------------------


def build_cohort_bit(row: dict) -> dict:
    """Turn one long planned-unit input row into the full §4a bit row.

    ``bit`` extends the pilot's 3-value set (present/absent/low_margin) with two
    coverage states so the long-table denominator is *every planned unit*:

      * ``no_coverage``  -- the ArcGIS ``exportImage`` returned an empty body
        (``fetch_outcome == 'empty'``): imagery does not cover the bbox.
      * ``fetch_failed`` -- the fetch never yielded a scorable chip
        (``waf_challenge`` / ``http_error`` / ``exception`` / ``not_fetched`` /
        blank, or ``ok`` with no readable/scored chip).

    Only ``present`` / ``absent`` / ``low_margin`` come from the pilot's
    ``classify_bit``; the two coverage states are decided from ``fetch_outcome``
    and never contradict anything or route to the queue.
    """
    year = int(row["year"])
    fetch_outcome = (row.get("fetch_outcome") or "")
    fetch_outcome = str(fetch_outcome).strip()
    detector_s = _parse_float(row.get("detector_S"))
    classifier_prob = _parse_float(row.get("classifier_pv_prob"))

    if fetch_outcome == _NO_COVERAGE_OUTCOME:
        bit = BIT_NO_COVERAGE
    elif fetch_outcome in _SCORABLE_OUTCOMES and detector_s is not None:
        bit = classify_bit(detector_s, classifier_prob)
    else:
        # waf_challenge / http_error / exception / not_fetched / blank, or a
        # fetched-ok chip that never produced a score.
        bit = BIT_FETCH_FAILED

    expected = expected_bit(row, year)  # 'present' | 'absent' | None

    routed_to_queue = bit == BIT_LOW_MARGIN

    agrees: bool | str
    if bit in _HIGH_MARGIN_BITS and expected is not None:
        agrees = bit == expected
    else:
        agrees = ""

    contradicts = False
    if bit == BIT_PRESENT:
        contradicts = contradicts_present(row.get("install_interval_start"), year)
    elif bit == BIT_ABSENT:
        contradicts = contradicts_absent(row.get("install_interval_end"), year)

    return {
        "anchor_id": row.get("anchor_id"),
        "stratum": row.get("stratum"),
        "year": year,
        "unit_purpose": row.get("unit_purpose", ""),
        "status": row.get("status", ""),
        "confidence": row.get("confidence", ""),
        "install_interval_start": row.get("install_interval_start", ""),
        "install_interval_end": row.get("install_interval_end", ""),
        "chip_path": row.get("chip_path", ""),
        "fetch_outcome": fetch_outcome,
        "detector_S": row.get("detector_S", ""),
        "classifier_pv_prob": row.get("classifier_pv_prob", ""),
        "bit": bit,
        "expected": expected if expected is not None else "",
        "agrees": agrees,
        "contradicts_interval": bool(contradicts),
        "routed_to_queue": routed_to_queue,
    }


# ---------------------------------------------------------------------------
# pivot_anchors — long bits -> wide (§4b) per-anchor table
# ---------------------------------------------------------------------------


def _iter_anchor_meta(anchor_meta: Iterable[dict] | Mapping[str, dict]) -> list[dict]:
    if isinstance(anchor_meta, Mapping):
        return [{**v, "anchor_id": k} for k, v in anchor_meta.items()]
    return list(anchor_meta)


def pivot_anchors(bit_rows: Iterable[dict], anchor_meta: Iterable[dict] | Mapping[str, dict]) -> list[dict]:
    """Pivot the long bit rows into one wide row per anchor (the ISSUE-10 table).

    ``anchor_meta`` is the authoritative anchor set (rows of
    ``cohort_anchors.csv`` — or a mapping ``anchor_id -> meta``); the output has
    exactly one row per meta anchor, in meta order. A layer year with no planned
    unit for the anchor gets ``bit_Y == 'not_planned'`` and blank per-year
    fields. Rollups (``any_contradiction`` / ``n_contradictions`` /
    ``n_high_margin_bits`` / ``first_present_year``) are computed over the
    anchor's *planned* bits only.
    """
    by_anchor: dict[str, dict[int, dict]] = {}
    for b in bit_rows:
        by_anchor.setdefault(str(b.get("anchor_id")), {})[int(b["year"])] = b

    out: list[dict] = []
    for meta in _iter_anchor_meta(anchor_meta):
        anchor_id = str(meta.get("anchor_id"))
        year_bits = by_anchor.get(anchor_id, {})

        wide: dict = {
            "anchor_id": anchor_id,
            "stratum": meta.get("stratum", ""),
            "status": meta.get("status", ""),
            "confidence": meta.get("confidence", ""),
            "install_interval_start": meta.get("install_interval_start", ""),
            "install_interval_end": meta.get("install_interval_end", ""),
            "latest_absent_date": meta.get("latest_absent_date", ""),
            "earliest_present_date": meta.get("earliest_present_date", ""),
            "grid_id": meta.get("grid_id", ""),
            "is_negative_control": _is_negative_control(meta),
        }

        for year in LAYER_YEARS:
            b = year_bits.get(year)
            if b is None:
                wide[f"bit_{year}"] = "not_planned"
                wide[f"detector_S_{year}"] = ""
                wide[f"classifier_pv_prob_{year}"] = ""
                wide[f"contradicts_{year}"] = ""
            else:
                wide[f"bit_{year}"] = b.get("bit", "")
                wide[f"detector_S_{year}"] = b.get("detector_S", "")
                wide[f"classifier_pv_prob_{year}"] = b.get("classifier_pv_prob", "")
                wide[f"contradicts_{year}"] = _truthy(b.get("contradicts_interval"))

        planned = list(year_bits.values())
        n_contra = sum(1 for b in planned if _truthy(b.get("contradicts_interval")))
        n_high = sum(1 for b in planned if b.get("bit") in _HIGH_MARGIN_BITS)
        present_years = sorted(
            int(b["year"]) for b in planned if b.get("bit") == BIT_PRESENT
        )
        wide["any_contradiction"] = n_contra > 0
        wide["n_contradictions"] = n_contra
        wide["n_high_margin_bits"] = n_high
        wide["first_present_year"] = present_years[0] if present_years else ""

        out.append(wide)
    return out


# ---------------------------------------------------------------------------
# compute_cohort_gates
# ---------------------------------------------------------------------------


def compute_cohort_gates(bit_rows: Iterable[dict], *, nc_anchor_ids: set[str]) -> dict:
    """Compute the four cohort gates over built §4a bit rows.

    Returns ``{gate_a, gate_nc, gate_b, clamp_findings}``:

      * ``gate_a`` -- present-side known-sign agreement per (stratum × year)
        over expectation-bearing high-margin non-control units, target ≥95%.
      * ``gate_nc`` -- negative-control false-present count (# controls with ≥1
        high-margin present bit); PASS iff the gate is *evaluable* (≥
        ``NC_SCORED_THRESHOLD`` of the declared controls produced a scored
        present/absent/low_margin bit) **and** the false-present count ≤ 2. A
        gate that never scored a control (all fetch_failed/no_coverage, or no
        controls declared) is not evaluable and cannot pass vacuously. Plus
        scored fraction, rate + Wilson CI.
      * ``gate_b`` -- within-audit monotonicity noise floor: anchors with a
        high-margin present@earlier ∧ absent@later pair over {2015,2019,2023}
        (count + ids; a noise floor, not a hard pass/fail).
      * ``clamp_findings`` -- count + ids of anchors with a high-margin
        present@2023 bit that contradicts a clamped interval, excluding
        ``EXCLUDED_STATUSES`` (surfaced as a findings tally, not a gate).
    """
    rows = list(bit_rows)

    # --- gate (a): present-side known-sign agreement, per (stratum, year) ---
    cell_acc: dict[tuple[str, int], dict[str, int]] = {}
    for b in rows:
        if str(b.get("anchor_id")) in nc_anchor_ids:
            continue  # controls belong to gate_nc, not gate_a
        expected = b.get("expected")
        if expected in (None, ""):
            continue  # no expectation -> not a known-sign unit
        if b.get("bit") not in _HIGH_MARGIN_BITS:
            continue  # low-margin / coverage states are inert
        key = (str(b.get("stratum")), int(b["year"]))
        acc = cell_acc.setdefault(key, {"n": 0, "agree": 0})
        acc["n"] += 1
        if _truthy(b.get("agrees")):
            acc["agree"] += 1

    cells: list[dict] = []
    for (stratum, year), acc in sorted(cell_acc.items()):
        n = acc["n"]
        rate = acc["agree"] / n if n else None
        cells.append({
            "stratum": stratum,
            "year": year,
            "n": n,
            "agree": acc["agree"],
            "rate": rate,
            "passes": (rate is not None) and (rate >= KNOWN_SIGN_THRESHOLD),
        })
    gate_a = {"cells": cells, "passes": all(c["passes"] for c in cells)}

    # --- gate (nc): negative-control false-present ---
    #
    # A control's absent-side bit only counts if the instrument actually ran on
    # it (a present/absent/low_margin bit). A control whose 2019/2023 fetches all
    # degraded to fetch_failed/no_coverage was never scored, so it can neither be
    # a false-present nor evidence that the thresholds are calibrated. The gate
    # is therefore only *evaluable* when a sufficient fraction of the declared
    # controls produced a scored bit; without that floor the gate would report
    # PASS on an instrument that never ran (k_fp trivially 0).
    nc_false_present: set[str] = set()
    nc_scored: set[str] = set()
    for b in rows:
        anchor_id = str(b.get("anchor_id"))
        if anchor_id not in nc_anchor_ids:
            continue
        bit = b.get("bit")
        if bit in _SCORED_BITS:
            nc_scored.add(anchor_id)
        if bit == BIT_PRESENT:
            nc_false_present.add(anchor_id)
    n_controls = len(nc_anchor_ids)
    n_scored = len(nc_scored)
    k_fp = len(nc_false_present)
    scored_rate = (n_scored / n_controls) if n_controls else None
    evaluable = (scored_rate is not None) and (scored_rate >= NC_SCORED_THRESHOLD)
    gate_nc = {
        "n_controls": n_controls,
        "n_scored_controls": n_scored,
        "scored_rate": scored_rate,
        "scored_threshold": NC_SCORED_THRESHOLD,
        "evaluable": evaluable,
        "n_false_present": k_fp,
        "false_present_anchor_ids": sorted(nc_false_present),
        "rate": (k_fp / n_controls) if n_controls else None,
        "wilson_ci": wilson_ci(k_fp, n_controls),
        "threshold": NC_FALSE_PRESENT_THRESHOLD,
        "passes": evaluable and (k_fp <= NC_FALSE_PRESENT_THRESHOLD),
    }

    # --- gate (b): within-audit monotonicity noise floor (3-year) ---
    high_by_anchor: dict[str, dict[int, str]] = {}
    for b in rows:
        if b.get("bit") not in _HIGH_MARGIN_BITS:
            continue
        high_by_anchor.setdefault(str(b.get("anchor_id")), {})[int(b["year"])] = b["bit"]

    mono_violations: list[str] = []
    for anchor_id, by_year in high_by_anchor.items():
        violated = False
        for i, y_early in enumerate(LAYER_YEARS):
            for y_late in LAYER_YEARS[i + 1:]:
                if by_year.get(y_early) == BIT_PRESENT and by_year.get(y_late) == BIT_ABSENT:
                    violated = True
        if violated:
            mono_violations.append(anchor_id)
    gate_b = {
        "violations": len(mono_violations),
        "violation_anchor_ids": sorted(mono_violations),
    }

    # --- clamp findings: high-margin present@2023 contradiction (excl. excluded statuses) ---
    clamp_ids = sorted({
        str(b.get("anchor_id"))
        for b in rows
        if int(b["year"]) == 2023
        and b.get("bit") == BIT_PRESENT
        and _truthy(b.get("contradicts_interval"))
        and b.get("status") not in EXCLUDED_STATUSES
    })
    clamp_findings = {"count": len(clamp_ids), "anchor_ids": clamp_ids}

    return {
        "gate_a": gate_a,
        "gate_nc": gate_nc,
        "gate_b": gate_b,
        "clamp_findings": clamp_findings,
    }


# ---------------------------------------------------------------------------
# contradiction_rate_by_stratum — the headline table (+ Wilson CI)
# ---------------------------------------------------------------------------

RATE_FIELDNAMES: tuple[str, ...] = (
    "level", "stratum", "year", "flag",
    "n_denominator", "n_numerator", "rate", "ci_low", "ci_high",
)


def _in_fals_2019(b: Mapping) -> bool:
    """Whether a bit row belongs to the 2019 falsification subsample.

    Robust to either integration path: an explicit ``in_fals_2019`` truthy
    field, or the pinned unit_purpose enum (``falsification`` at year 2019).
    """
    if _truthy(b.get("in_fals_2019")):
        return True
    return int(b.get("year", 0)) == 2019 and b.get("unit_purpose") == "falsification"


def _rate_row(level, stratum, year, flag, num, denom) -> dict:
    lo, hi = wilson_ci(num, denom)
    return {
        "level": level,
        "stratum": stratum,
        "year": year,
        "flag": flag,
        "n_denominator": denom,
        "n_numerator": num,
        "rate": (num / denom) if denom else None,
        "ci_low": lo,
        "ci_high": hi,
    }


def contradiction_rate_by_stratum(bit_rows: Iterable[dict]) -> list[dict]:
    """Per-stratum contradiction-rate table — the ISSUE-09 headline output.

    Emits, in a single flat list (each row carries a ``level`` discriminator):

      * ``level=='stratum'``      -- anchor-level rate: denominator = anchors
        with ≥1 high-margin bit; numerator = anchors with ≥1 contradiction.
      * ``level=='stratum_year'`` -- bit-level rate per (stratum × year):
        denominator = high-margin bits; numerator = contradiction bits.
      * ``level=='flag'``         -- the ``in_fals_2019`` sub-breakdown
        (anchor-level over the 2019 falsification subsample).

    Every row carries a Wilson score interval (``ci_low`` / ``ci_high``).
    """
    rows = list(bit_rows)

    # group per stratum
    strata = sorted({str(b.get("stratum")) for b in rows})
    out: list[dict] = []

    for stratum in strata:
        srows = [b for b in rows if str(b.get("stratum")) == stratum]

        # anchor-level
        anchors_high: set[str] = set()
        anchors_contra: set[str] = set()
        for b in srows:
            aid = str(b.get("anchor_id"))
            if b.get("bit") in _HIGH_MARGIN_BITS:
                anchors_high.add(aid)
            if _truthy(b.get("contradicts_interval")):
                anchors_contra.add(aid)
        denom = len(anchors_high)
        num = len(anchors_high & anchors_contra)
        out.append(_rate_row("stratum", stratum, "", "", num, denom))

        # bit-level per year
        for year in LAYER_YEARS:
            yrows = [b for b in srows if int(b.get("year", 0)) == year]
            high = [b for b in yrows if b.get("bit") in _HIGH_MARGIN_BITS]
            if not high:
                continue
            hi_contra = sum(1 for b in high if _truthy(b.get("contradicts_interval")))
            out.append(_rate_row("stratum_year", stratum, year, "", hi_contra, len(high)))

    # in_fals_2019 flag sub-row (anchor-level, across all strata)
    fals_rows = [b for b in rows if _in_fals_2019(b)]
    fals_high: set[str] = set()
    fals_contra: set[str] = set()
    for b in fals_rows:
        aid = str(b.get("anchor_id"))
        if b.get("bit") in _HIGH_MARGIN_BITS:
            fals_high.add(aid)
        if _truthy(b.get("contradicts_interval")):
            fals_contra.add(aid)
    out.append(_rate_row("flag", "", "", "in_fals_2019",
                         len(fals_high & fals_contra), len(fals_high)))

    return out


# ---------------------------------------------------------------------------
# coverage_report — covered/failed accounting for the ≥95% AC
# ---------------------------------------------------------------------------


def _unit_covered_and_reason(bit_row: dict | None) -> tuple[bool, str]:
    """Decide whether a planned unit is *covered* and, if not, a gap reason.

    Coverage keys on the **computed presence bit**, not on ``fetch_outcome``: a
    unit is covered iff its ``bit`` is a real terminal answer
    (present/absent/low_margin/no_coverage) and a gap iff its ``bit`` is
    ``fetch_failed`` — this honours the schema doc, where an ``ok`` fetch that
    yielded no scorable chip is still a coverage gap (``bit == fetch_failed``).
    When a bit row carries no computed ``bit`` (a simplified caller / a planned
    unit with no bit row at all), it falls back to the ``fetch_outcome``.
    """
    if bit_row is None:
        return False, "not_fetched"
    bit = str(bit_row.get("bit") or "").strip()
    fetch_outcome = str(bit_row.get("fetch_outcome") or "").strip() or "not_fetched"
    if bit:
        if bit in _COVERED_BITS:
            return True, ""
        # bit == fetch_failed (the only non-covered bit): surface WHY it failed.
        # An ok/skipped fetch that produced no score is an unscorable gap, not a
        # transport failure — label it so ``fetch_failures.csv`` is honest.
        if fetch_outcome in COVERED_FETCH_OUTCOMES:
            return False, f"{fetch_outcome}_unscorable"
        return False, fetch_outcome
    # no computed bit -> fall back to the raw fetch outcome.
    if fetch_outcome in COVERED_FETCH_OUTCOMES:
        return True, ""
    return False, fetch_outcome


def coverage_report(planned_units: Iterable[dict], bit_rows: Iterable[dict]) -> dict:
    """Coverage accounting over the planned units.

    A planned unit is *covered* iff its computed presence ``bit`` is a real
    terminal answer (``present`` / ``absent`` / ``low_margin`` / ``no_coverage``);
    a ``fetch_failed`` bit — including an ``ok`` fetch that never yielded a
    scorable chip — a retried transport failure, ``not_fetched``, or a planned
    unit with no bit row at all is a gap. (When a bit row carries no computed
    ``bit`` the decision falls back to its ``fetch_outcome``.) An anchor is
    covered iff **all** its planned units are covered.
    ``coverage = covered_dated_anchors / dated_anchors`` (negative controls are
    excluded from the dated denominator). Every gap is enumerated in
    ``failed_units`` (feeds ``fetch_failures.csv``).
    """
    bit_row_by_unit: dict[tuple[str, int], dict] = {}
    for b in bit_rows:
        bit_row_by_unit[(str(b.get("anchor_id")), int(b["year"]))] = b

    dated_anchor_ok: dict[str, bool] = {}
    failed_units: list[dict] = []
    for u in planned_units:
        anchor_id = str(u.get("anchor_id"))
        year = int(u["year"])
        bit_row = bit_row_by_unit.get((anchor_id, year))
        is_covered, reason = _unit_covered_and_reason(bit_row)
        if not is_covered:
            failed_units.append({"anchor_id": anchor_id, "year": year, "outcome": reason})

        if _is_negative_control(u):
            continue  # controls do not enter the dated coverage denominator
        prev = dated_anchor_ok.get(anchor_id, True)
        dated_anchor_ok[anchor_id] = prev and is_covered

    dated_anchors = len(dated_anchor_ok)
    covered_anchors = sum(1 for ok in dated_anchor_ok.values() if ok)
    coverage = (covered_anchors / dated_anchors) if dated_anchors else None
    failed_units.sort(key=lambda f: (f["anchor_id"], f["year"]))

    return {
        "dated_anchors": dated_anchors,
        "covered_anchors": covered_anchors,
        "coverage": coverage,
        "passes_95pct": (coverage is not None) and (coverage >= COVERAGE_THRESHOLD),
        "n_failed_units": len(failed_units),
        "failed_units": failed_units,
    }


# ---------------------------------------------------------------------------
# wilson_ci — standard Wilson score interval
# ---------------------------------------------------------------------------


def wilson_ci(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval for ``k`` successes out of ``n`` trials.

    Standard (no continuity correction), clamped to [0, 1]. ``n == 0`` returns
    the degenerate ``(0.0, 0.0)``.
    """
    if n <= 0:
        return (0.0, 0.0)
    p = k / n
    z2 = z * z
    denom = 1.0 + z2 / n
    center = (p + z2 / (2 * n)) / denom
    margin = (z / denom) * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n))
    return (max(0.0, center - margin), min(1.0, center + margin))
