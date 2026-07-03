"""Pure join/gate logic for the CoJ true-date audit pilot (ISSUE-08).

No network, no filesystem I/O, no GPU — every function here operates on
plain dicts / lists of dicts so it is directly unit-testable
(``tests/audit/test_coj_audit_join.py``). The orchestrator
(``coj_audit_pilot.py``) is the only module in this package that touches
pandas/CSVs; it maps rows read from ``install_intervals.csv`` /
``chip_groups_as_anchors.csv`` / ``census2023_cohort.csv`` onto the plain
dicts consumed here.

Strata (see ``docs/replan_v2/ISSUE-08-coj-audit-pilot.md`` + the pilot doc for
the full rationale):

  s1_known_present_pre2019   -- expect PRESENT on both the 2019 and 2023 layer
  s2_known_present_2019_2023 -- expect PRESENT on 2023 only (2019 sign unknown)
  s3_known_absent_2023       -- expect ABSENT on both layers (install starts
                                 after the 2023 flight, so it is a fortiori
                                 absent in the earlier 2019 flight too)
  s4_ambiguous                -- no expected sign; exercises the contradiction
                                 flag only, sampled from the census2023 cohort

``done_ambiguous_clamp_inverted`` and ``done_ambiguous_marker_missed_pv`` are
excluded everywhere (the pipeline already knows these interval estimates are
internally inconsistent; using them as "known sign" would be circular).

Margin rule (the ONE place the 0.30 / 0.95 thresholds live):

  S >= 0.95 AND (classifier unavailable OR classifier pv_prob >= 0.5) -> present
  S <= 0.30                                                            -> absent
  otherwise (0.30 < S < 0.95, OR classifier disagrees with a would-be
  present bit)                                                         -> low_margin

Only ``present`` / ``absent`` (high-margin) bits participate in gates or can
raise a contradiction flag; ``low_margin`` bits are routed to the human queue
and never contradict anything.

Contradiction logic uses conservative, year-only vintage bounds (both CoJ
layers only carry a capture *year*, not a day):

  present@Y contradicts the interval iff install_interval_start > Dec 31 of Y
    (pipeline claims still-absent past year Y; the year-Y layer shows PV)
  absent@Y contradicts the interval iff install_interval_end < Jan 1 of Y
    (pipeline claims already-present before year Y; the year-Y layer shows
    no PV)
"""
from __future__ import annotations

import random
from datetime import date
from typing import Any

# ---------------------------------------------------------------------------
# Strata
# ---------------------------------------------------------------------------

EXCLUDED_STATUSES: frozenset[str] = frozenset(
    {"done_ambiguous_clamp_inverted", "done_ambiguous_marker_missed_pv"}
)

_S1 = "s1_known_present_pre2019"
_S2 = "s2_known_present_2019_2023"
_S3 = "s3_known_absent_2023"
_S4 = "s4_ambiguous"

STRATA = (_S1, _S2, _S3, _S4)

_DATE_2019_01_01 = date(2019, 1, 1)
_DATE_2023_01_01 = date(2023, 1, 1)
_DATE_2023_12_31 = date(2023, 12, 31)

_S1_STATUSES = frozenset({"done_appears", "done_already_present_before_geid_history"})


def _parse_date(value: Any) -> date | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() == "nan":
        return None
    return date.fromisoformat(text[:10])


def classify_stratum(row: dict) -> str | None:
    """Classify one ``install_intervals.csv`` row into s1/s2/s3, or ``None``.

    ``None`` covers both excluded statuses and rows that simply don't match
    any known-sign stratum (e.g. the other ``done_ambiguous_*`` buckets, or
    dates that fall outside the s1/s2/s3 windows). Stratum 4 (ambiguous
    straddlers) is NOT derived here — it is sampled directly from the
    census2023 cohort file by the caller (those rows have no ``status``).
    """
    status = row.get("status")
    if status in EXCLUDED_STATUSES:
        return None

    start = _parse_date(row.get("install_interval_start"))
    end = _parse_date(row.get("install_interval_end"))
    if start is None or end is None:
        return None

    if status in _S1_STATUSES and end < _DATE_2019_01_01:
        return _S1

    if status == "done_appears":
        if _DATE_2019_01_01 <= end < _DATE_2023_01_01:
            return _S2
        if start > _DATE_2023_12_31:
            return _S3

    return None


# ---------------------------------------------------------------------------
# Expected sign
# ---------------------------------------------------------------------------


def expected_sign(stratum: str, year: int) -> bool | None:
    """Expected PV presence bool for ``stratum`` at ``year``, or ``None`` (no expectation)."""
    if stratum == _S1:
        return True
    if stratum == _S2:
        return True if year == 2023 else None
    if stratum == _S3:
        return False
    return None  # s4_ambiguous and anything unrecognized


# ---------------------------------------------------------------------------
# Margin rule
# ---------------------------------------------------------------------------

PRESENT_FLOOR = 0.95
ABSENT_CEILING = 0.30
CLASSIFIER_CORROBORATION_FLOOR = 0.5


def classify_bit(score: float, classifier_pv_prob: float | None = None) -> str:
    """Apply the margin rule to one detector score (+ optional classifier prob)."""
    if score <= ABSENT_CEILING:
        return "absent"
    if score >= PRESENT_FLOOR:
        if classifier_pv_prob is not None and classifier_pv_prob < CLASSIFIER_CORROBORATION_FLOOR:
            return "low_margin"  # detector/classifier disagreement on a would-be present bit
        return "present"
    return "low_margin"


# ---------------------------------------------------------------------------
# Contradiction logic (conservative, year-only vintage bounds)
# ---------------------------------------------------------------------------


def contradicts_present(install_interval_start: Any, year: int) -> bool:
    start = _parse_date(install_interval_start)
    if start is None:
        return False
    return start > date(year, 12, 31)


def contradicts_absent(install_interval_end: Any, year: int) -> bool:
    end = _parse_date(install_interval_end)
    if end is None:
        return False
    return end < date(year, 1, 1)


# ---------------------------------------------------------------------------
# Per (anchor, year) audit bit
# ---------------------------------------------------------------------------


def build_audit_bit(row: dict) -> dict:
    """Build one audit_bits row from a scored (anchor, year) input row.

    Expects ``row`` to carry: anchor_id, stratum, year, score
    (``classifier_pv_prob`` optional), install_interval_start,
    install_interval_end. Returns a dict with the added fields: bit,
    expected, agrees, contradiction, routed_to_queue.
    """
    stratum = row["stratum"]
    year = int(row["year"])
    score = row["score"]
    classifier_pv_prob = row.get("classifier_pv_prob")

    bit = classify_bit(score, classifier_pv_prob)
    expected = expected_sign(stratum, year)
    routed_to_queue = bit == "low_margin"

    agrees: bool | None
    if bit == "low_margin" or expected is None:
        agrees = None
    else:
        agrees = (bit == "present") == expected

    contradiction = False
    if not routed_to_queue:
        if bit == "present":
            contradiction = contradicts_present(row.get("install_interval_start"), year)
        elif bit == "absent":
            contradiction = contradicts_absent(row.get("install_interval_end"), year)

    out = dict(row)
    out.update(
        {
            "bit": bit,
            "expected": expected,
            "agrees": agrees,
            "contradiction": contradiction,
            "routed_to_queue": routed_to_queue,
        }
    )
    return out


# ---------------------------------------------------------------------------
# Self-gates
# ---------------------------------------------------------------------------

KNOWN_SIGN_THRESHOLD = 0.95


def compute_gates(audit_bits: list[dict]) -> dict:
    """Compute the three self-gates over a list of ``build_audit_bit`` rows.

    Returns:
      known_sign_agreement: {stratum: {year: {n_high_margin, n_low_margin,
        n_agree, agreement_rate, passes_95pct}}} — gate (a).
      within_audit_monotonicity: {violations, violation_anchor_ids} — gate
        (b), the noise floor: present@2019 & absent@2023 (both high-margin)
        for the same anchor.
      clamp_monotonicity: {violations, violation_anchor_ids} — gate (c):
        high-margin present@2023 bits that contradict a clamped interval
        (install_interval_start > 2023-12-31). This is definitionally the
        year==2023, bit=="present" slice of the general contradiction flag;
        reported separately because it is the specific self-gate ISSUE-08
        asks for.
    """
    known_sign: dict[str, dict[int, dict]] = {}
    for b in audit_bits:
        stratum = b["stratum"]
        year = int(b["year"])
        if b.get("expected") is None:
            continue
        bucket = known_sign.setdefault(stratum, {}).setdefault(
            year, {"n_high_margin": 0, "n_low_margin": 0, "n_agree": 0}
        )
        if b["bit"] == "low_margin":
            bucket["n_low_margin"] += 1
        else:
            bucket["n_high_margin"] += 1
            if b.get("agrees"):
                bucket["n_agree"] += 1

    for stratum, by_year in known_sign.items():
        for year, bucket in by_year.items():
            n = bucket["n_high_margin"]
            rate = (bucket["n_agree"] / n) if n > 0 else None
            bucket["agreement_rate"] = rate
            bucket["passes_95pct"] = (rate is not None) and (rate >= KNOWN_SIGN_THRESHOLD)

    # Gate (b): within-audit monotonicity noise floor.
    by_anchor: dict[str, dict[int, str]] = {}
    for b in audit_bits:
        if b["bit"] not in ("present", "absent"):
            continue
        by_anchor.setdefault(b["anchor_id"], {})[int(b["year"])] = b["bit"]

    mono_violations = sorted(
        anchor_id
        for anchor_id, by_year in by_anchor.items()
        if by_year.get(2019) == "present" and by_year.get(2023) == "absent"
    )

    # Gate (c): clamp-monotonicity violations = high-margin present@2023
    # bits flagged as contradicting the interval. Bits from EXCLUDED_STATUSES
    # anchors never count: those intervals are pre-known internally
    # inconsistent, so a "violation" there is not a new audit disagreement.
    clamp_violations = sorted(
        b["anchor_id"]
        for b in audit_bits
        if int(b["year"]) == 2023
        and b["bit"] == "present"
        and b.get("contradiction")
        and b.get("status") not in EXCLUDED_STATUSES
    )

    return {
        "known_sign_agreement": known_sign,
        "within_audit_monotonicity": {
            "violations": len(mono_violations),
            "violation_anchor_ids": mono_violations,
        },
        "clamp_monotonicity": {
            "violations": len(clamp_violations),
            "violation_anchor_ids": clamp_violations,
        },
    }


# ---------------------------------------------------------------------------
# Seeded sampling
# ---------------------------------------------------------------------------


def sample_pilot(
    intervals_rows: list[dict],
    ambiguous_rows: list[dict],
    *,
    seed: int,
    n_s1: int = 30,
    n_s2: int = 20,
    n_s4: int = 15,
) -> list[dict]:
    """Derive strata from ``intervals_rows`` and draw the pilot sample.

    s1/s2/s4 are seeded random samples (deterministic for a fixed seed,
    order-independent of input row order via sort-then-shuffle); s3 takes
    EVERY matching row regardless of ``n_s1``/``n_s2``/``n_s4`` (there are
    only ~57 in the full cohort per the ISSUE-08 brief). Returns a flat list
    of dicts, each the original row plus a ``stratum`` key.
    """
    buckets: dict[str, list[dict]] = {s: [] for s in (_S1, _S2, _S3)}
    for row in intervals_rows:
        stratum = classify_stratum(row)
        if stratum is not None:
            buckets[stratum].append(row)

    rng = random.Random(seed)

    def _draw(rows: list[dict], n: int) -> list[dict]:
        ordered = sorted(rows, key=lambda r: r["anchor_id"])
        if n >= len(ordered):
            return ordered
        return rng.sample(ordered, n)

    sample: list[dict] = []
    for stratum, n in ((_S1, n_s1), (_S2, n_s2)):
        for row in _draw(buckets[stratum], n):
            out = dict(row)
            out["stratum"] = stratum
            sample.append(out)

    for row in buckets[_S3]:  # ALL of stratum 3, no sampling
        out = dict(row)
        out["stratum"] = _S3
        sample.append(out)

    eligible_ambiguous = [
        r for r in ambiguous_rows if r.get("status") not in EXCLUDED_STATUSES
    ]
    for row in _draw(eligible_ambiguous, n_s4):
        out = dict(row)
        out["stratum"] = _S4
        sample.append(out)

    return sample
