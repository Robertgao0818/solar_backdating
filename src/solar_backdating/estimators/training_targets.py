"""RUN 3 teacher brackets -> student change-cell target set ``K_i``
(DESIGN-phase0-emission-extension §4.1).

A teacher decode for one anchor is normalized into a ``TeacherBracket`` (one of
four boundary shapes), and ``build_k_i`` maps it to the set of the STUDENT's own
tau cells the RUN 3 label permits — by DATE-INTERVAL OVERLAP, never by epoch
index identity. The two grids can differ (the student drops uninformative frames
and may collapse gaps differently), so index identity would silently mismatch;
overlap is the only correct join. Pure, no numpy/torch — same style as the rest
of ``estimators/``.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from solar_backdating.estimators.seam import EpochCell


@dataclass(frozen=True)
class TeacherBracket:
    """One anchor's teacher-side decode, normalized to a boundary shape.

    ``kind`` in ``{"interval", "left_censored", "right_censored",
    "census_bound"}``; ``(lower, upper]`` are the interval bounds with the SAME
    ``(start_date, end_date]`` half-open semantics as ``EpochCell`` (``None`` =
    open on that side).
    """

    kind: str
    lower: date | None
    upper: date | None


_KINDS = ("interval", "left_censored", "right_censored", "census_bound")


def build_k_i(bracket: TeacherBracket, cells: Sequence[EpochCell]) -> frozenset[int]:
    """Change-cell index set the teacher bracket allows, over the STUDENT cells.

    Dispatch (design §4.1):

    - ``"left_censored"``  -> ``{0}`` (the open-left cell).
    - ``"right_censored"`` -> ``{len(cells) - 1}`` (the beyond-window cell).
    - ``"census_bound"``   -> the cell(s) whose ``end_date`` equals
      ``bracket.upper`` (the census / evidence-cutoff date). The
      ``evidence_cutoff`` synthetic-epoch mechanism guarantees exactly one such
      cell exists; an empty match is a pipeline bug and raises.
    - ``"interval"``       -> every student cell whose ``(start, end]`` has a
      non-empty date overlap with ``(bracket.lower, bracket.upper]``. NOT
      required to be exactly one: a coarse teacher interval can straddle several
      finer student cells, or vice versa.

    A non-empty ``K_i`` is required for the overlap kinds (``census_bound``,
    ``interval``): an empty overlap is a data/pipeline bug and raises ValueError
    rather than being silently swallowed as an empty set.
    """
    if bracket.kind not in _KINDS:
        raise ValueError(f"unknown TeacherBracket.kind={bracket.kind!r}; expected one of {_KINDS}")

    if bracket.kind == "left_censored":
        return frozenset({0})
    if bracket.kind == "right_censored":
        return frozenset({len(cells) - 1})
    if bracket.kind == "census_bound":
        result = frozenset(i for i, c in enumerate(cells) if c.end_date == bracket.upper)
        if not result:
            raise ValueError(
                f"census_bound bracket (upper={bracket.upper!r}) matched no student cell "
                "end_date; the evidence_cutoff synthetic epoch should guarantee one"
            )
        return result

    # interval: half-open overlap with None -> ±infinity via date.min/date.max.
    lo = bracket.lower or date.min
    hi = bracket.upper or date.max
    result = frozenset(
        i
        for i, c in enumerate(cells)
        if max(c.start_date or date.min, lo) < min(c.end_date or date.max, hi)
    )
    if not result:
        raise ValueError(
            f"interval bracket ({bracket.lower!r}, {bracket.upper!r}] overlaps no student "
            "cell; empty K_i is a pipeline bug, not a silent empty set"
        )
    return result
