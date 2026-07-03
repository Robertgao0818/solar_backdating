"""Epoch collapsing (D2 preprocessing) — used ONLY by PAVA.

Baselines never collapse: that is how ``fpd``/``sustained`` stay bit-exact with
``derive_install``, which treats every distinct date independently. ``panel_io``
(PART 2) already deduplicates exact-duplicate ``capture_date`` within a
(unit, rep) by last-wins, so estimators never see literal duplicate dates from
the panel; the collapser still handles them for the property test.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from solar_backdating.estimators.seam import VintageObservation


@dataclass(frozen=True)
class Epoch:
    """A contiguous group of near-duplicate vintages (gap-collapsed)."""

    start_date: date  # min member capture_date
    end_date: date  # max member capture_date
    n_present: int  # count of '1' members
    n_absent: int  # count of '0' members
    n_abstain: int  # count of '' members
    mean_confidence: float | None  # mean of scored members' confidence; None if none scored


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
            )
        )
    return epochs
