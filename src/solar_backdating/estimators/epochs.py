"""Epoch collapsing (D2 preprocessing) — used by PAVA and the ISSUE-02 decoder.

Baselines never collapse: that is how ``fpd``/``sustained`` stay bit-exact with
``derive_install``, which treats every distinct date independently. ``panel_io``
(PART 2) already deduplicates exact-duplicate ``capture_date`` within a
(unit, rep) by last-wins, so estimators never see literal duplicate dates from
the panel; the collapser still handles them for the property test.

``Epoch.stratum`` and ``epoch_symbol()`` are the single source of truth for the
(symbol, stratum) mapping shared by the ISSUE-02 changepoint decoder
(``estimators/changepoint.py``) and its EM emission fit
(``estimators/emissions.py``) — both import them from here rather than
re-deriving the majority-vote logic. PAVA (``estimators/pava.py``) does NOT use
``epoch_symbol()`` or ``stratum``: it reduces every epoch to its own
present/absent/neutral indicator inline off the RAW ``n_present``/``n_absent``
fields below (`n_present > n_absent` etc.), so adding fields here is additive
and does not change PAVA's outputs (proved by the banked-panel regression
gate, unchanged after this edit).

Usable-only evidence gate (orchestrator design correction, 2026-07-03):
``epoch_symbol()`` computes its PRESENT/ABSENT/NEUTRAL/ABSTAIN majority vote
from ``n_present_usable``/``n_absent_usable`` — the subset of an epoch's
verdicts flagged ``quality_flag == "usable"`` — NOT the raw ``n_present``/
``n_absent`` counts (which stay untouched for PAVA, above). This makes the
decoder's evidence gate match production's own bound-selection filter exactly:
``scripts/temporal/scan_decision.py::usable_observations`` and
``scripts/temporal/infer_install_dates.py::_usable`` both do
``r.quality_flag == "usable" and r.pv_present is not None`` before any
date/bound is derived from a verdict. An ``ambiguous``/``unusable``-flagged
scored verdict is therefore treated as censoring-with-cause (ISSUE-04
doctrine: the observation exists and is recorded, but its content carries no
resolution signal) rather than as dispositive PRESENT/ABSENT evidence — an
epoch with zero usable-scored members emits ``"abstain"`` even if it has
non-usable scored members, and is then scored purely through the emission
model's per-stratum abstain probability (or dropped, under the symmetric-noise
fallback — see ``changepoint.py``). This also fixed the EM fit's starting
conditions: the banked panel is ~99% ``usable`` (44326/44790 rows on the
extended panel, 2026-07-03), so before this correction EM had almost no
non-usable-flagged epochs to calibrate a distinct abstain/noise profile for
the ``ambiguous``/``unusable`` strata — the few that existed were being
absorbed into ordinary PRESENT/ABSENT counts instead.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from solar_backdating.estimators.seam import VintageObservation

# Epoch.stratum tie-break priority when multiple quality_flag values tie for the
# majority among an epoch's members (D2 decoder math: "ties broken by priority
# usable > ambiguous > unusable"). Unknown/unexpected quality_flag strings sort
# after all three known values (deterministic, but does not privilege them).
_STRATUM_PRIORITY = {"usable": 0, "ambiguous": 1, "unusable": 2}


def _majority_stratum(members: Sequence[VintageObservation]) -> str:
    counts = Counter(o.quality_flag for o in members)
    top = max(counts.values())
    candidates = [qf for qf, n in counts.items() if n == top]
    return min(candidates, key=lambda qf: (_STRATUM_PRIORITY.get(qf, 99), qf))


@dataclass(frozen=True)
class Epoch:
    """A contiguous group of near-duplicate vintages (gap-collapsed)."""

    start_date: date  # min member capture_date
    end_date: date  # max member capture_date
    n_present: int  # count of '1' members, ALL quality flags -- PAVA's raw evidence unit
    n_absent: int  # count of '0' members, ALL quality flags -- PAVA's raw evidence unit
    n_abstain: int  # count of '' members, ALL quality flags
    mean_confidence: float | None  # mean of scored members' confidence; None if none scored
    stratum: str = "usable"  # majority quality_flag among ALL members (D2); PAVA ignores this
    n_present_usable: int = 0  # subset of n_present with quality_flag=='usable' -- epoch_symbol()
    n_absent_usable: int = 0  # subset of n_absent with quality_flag=='usable' -- epoch_symbol()


def collapse_epochs(
    observations: Sequence[VintageObservation], gap_days: int
) -> list[Epoch]:
    """Greedy gap-collapse observations into deterministic epochs.

    Sort by ``(capture_date, source_row or 0)``, then open a new epoch whenever
    the next observation is more than ``gap_days`` after the current group's
    running last date. ``gap_days=0`` -> each distinct date is its own epoch;
    exact-duplicate dates still merge (gap 0 <= 0).
    """
    if not observations:
        return []

    ordered = sorted(observations, key=lambda o: (o.capture_date, o.source_row or 0))

    groups: list[list[VintageObservation]] = []
    current: list[VintageObservation] = [ordered[0]]
    last_date = ordered[0].capture_date
    for obs in ordered[1:]:
        if (obs.capture_date - last_date).days <= gap_days:
            current.append(obs)
        else:
            groups.append(current)
            current = [obs]
        last_date = obs.capture_date
    groups.append(current)

    epochs: list[Epoch] = []
    for group in groups:
        n_present = sum(1 for o in group if o.pv_present == "1")
        n_absent = sum(1 for o in group if o.pv_present == "0")
        n_abstain = sum(1 for o in group if o.pv_present not in ("0", "1"))
        n_present_usable = sum(
            1 for o in group if o.pv_present == "1" and o.quality_flag == "usable"
        )
        n_absent_usable = sum(
            1 for o in group if o.pv_present == "0" and o.quality_flag == "usable"
        )
        confs = [o.confidence for o in group if o.confidence is not None]
        mean_conf = sum(confs) / len(confs) if confs else None
        epochs.append(
            Epoch(
                start_date=group[0].capture_date,
                end_date=group[-1].capture_date,
                n_present=n_present,
                n_absent=n_absent,
                n_abstain=n_abstain,
                mean_confidence=mean_conf,
                stratum=_majority_stratum(group),
                n_present_usable=n_present_usable,
                n_absent_usable=n_absent_usable,
            )
        )
    return epochs


def epoch_symbol(epoch: Epoch) -> str:
    """Majority verdict symbol for one epoch — shared by the decoder and its EM fit.

    Votes only off ``n_present_usable``/``n_absent_usable`` — the
    ``quality_flag == "usable"`` subset of the epoch's scored members (see the
    module docstring's "Usable-only evidence gate" note). An
    ``ambiguous``/``unusable``-flagged scored verdict never moves the vote by
    itself; it only ever downgrades an epoch toward ``"abstain"`` when no
    usable-scored member is present, exactly mirroring production's own
    ``usable_observations``/``_usable`` bound filters.

    - ``"present"`` / ``"absent"``: strict majority among USABLE-scored
      members.
    - ``"neutral"``: a tie with at least one usable-scored member
      (``n_present_usable == n_absent_usable > 0``) — contributes 0
      log-likelihood to every latent state and is excluded from EM counts
      (preserves PAVA's duplicate-injection invariance property; PAVA computes
      an analogous three-way split inline, off the RAW, non-quality-filtered
      counts — PAVA never calls this function).
    - ``"abstain"``: no usable-scored members at all (``n_present_usable ==
      n_absent_usable == 0``) — whether because the epoch truly has no scored
      members, or because every scored member is ``ambiguous``/``unusable``-
      flagged (censoring-with-cause, not evidence). PAVA drops all-raw-abstain
      epochs entirely; the ISSUE-02 decoder keeps and scores usable-abstain
      epochs via the emission model when one is configured (``config.emissions
      is not None``), and drops them (matching PAVA) under the symmetric-noise
      fallback (``config.emissions is None`` — see
      ``changepoint.py::estimate_changepoint``, which filters on this same
      function rather than on raw counts).
    """
    if epoch.n_present_usable == 0 and epoch.n_absent_usable == 0:
        return "abstain"
    if epoch.n_present_usable > epoch.n_absent_usable:
        return "present"
    if epoch.n_absent_usable > epoch.n_present_usable:
        return "absent"
    return "neutral"
