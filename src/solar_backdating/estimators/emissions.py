"""EmissionModel + deterministic EM fit (ISSUE-02 changepoint decoder).

3-symbol confusion matrix ``P(symbol | state, stratum)`` — ``symbol`` in
``SYMBOLS`` (absent / present / abstain), ``state`` in ``STATES`` (absent_state
/ present_state). Stratified by ``quality_flag`` ONLY: the banked panel
(``long_all.csv``) has no zoom or imagery-era column, so the PRD D2 "stratified
by quality flag, zoom, and imagery era" spec can only be executed on the
``quality_flag`` axis today. Zoom/imagery-era stratification is explicitly
D17/ISSUE-18 territory (achieved-zoom provenance does not exist in the banked
panel yet) — this module's stratification is a documented subset of the target
spec, not the final form.

This module has NO dependency on ``seam.py`` at runtime (only under
``TYPE_CHECKING``, for type hints) so that ``seam.py`` can hold an
``EstimatorConfig.emissions: EmissionModel | None`` field without a runtime
import cycle (seam -> emissions -> ... -> seam). See ``seam.py``'s
``TYPE_CHECKING`` block for the other half of this contract.

Fitting is fully unsupervised: the latent variable is the changepoint index
``tau`` per (unit, rep) sequence (uniform prior during fitting, matching the
flat-prior default used everywhere else in this issue), never an install date.
Fitting ``fit_emissions_em`` on the same panel the harness later evaluates
against is therefore by design (PRD D2: "estimated cohort-wide by EM"), not
label leakage — no ground-truth install date ever enters the E/M steps.
"""
from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from solar_backdating.estimators.epochs import collapse_epochs, epoch_symbol

if TYPE_CHECKING:
    from solar_backdating.estimators.seam import VintageObservation

SYMBOLS = ("absent", "present", "abstain")
STATES = ("absent_state", "present_state")
SYMBOL_INDEX = {s: i for i, s in enumerate(SYMBOLS)}
DEFAULT_STRATUM = "default"

_EPS_FLOOR = 1e-12
_ZERO_ROW = (0.0, 0.0, 0.0)

Matrix = tuple[tuple[float, float, float], tuple[float, float, float]]


@dataclass(frozen=True)
class EmissionModel:
    """``P(symbol | state, stratum)`` — plain nested tuples, no numpy.

    ``matrices[i]`` is the 2x3 matrix for ``strata[i]``: row 0 = absent_state,
    row 1 = present_state; each row is ``(P(absent), P(present), P(abstain))``
    summing to 1. ``counts[i]`` mirrors the shape with the raw expected counts
    that produced ``matrices[i]`` (post-smoothing accumulator), kept for
    provenance/audit — NOT used at decode time.
    """

    strata: tuple[str, ...]
    matrices: tuple[Matrix, ...]
    counts: tuple[Matrix, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        """Guard against a duplicate stratum key. ``strata`` doubles as a dict
        key set everywhere this model is built/consumed (``lookup`` uses
        ``.index()``, ``fit_emissions_em`` builds ``{s: ... for s in strata}``
        dict comprehensions) — a duplicate silently aliases two accumulator
        cells into one (see the ``DEFAULT_STRATUM`` collision this guard was
        added for) with no exception. Fail loudly instead."""
        if len(set(self.strata)) != len(self.strata):
            seen: set[str] = set()
            dupes = sorted({s for s in self.strata if s in seen or seen.add(s)})  # type: ignore[func-returns-value]
            raise ValueError(f"EmissionModel.strata has duplicate entries: {dupes!r}")

    def lookup(self, stratum: str) -> Matrix:
        """Matrix for ``stratum``; falls back to the pooled ``"default"`` stratum."""
        if stratum in self.strata:
            return self.matrices[self.strata.index(stratum)]
        if DEFAULT_STRATUM in self.strata:
            return self.matrices[self.strata.index(DEFAULT_STRATUM)]
        raise KeyError(
            f"stratum {stratum!r} not in emission model and no {DEFAULT_STRATUM!r} fallback"
        )

    def to_json(self) -> dict:
        return {
            "symbols": list(SYMBOLS),
            "states": list(STATES),
            "strata": list(self.strata),
            "matrices": [[list(row) for row in mat] for mat in self.matrices],
            "counts": [[list(row) for row in mat] for mat in self.counts],
        }

    @staticmethod
    def from_json(d: dict) -> "EmissionModel":
        strata = tuple(d["strata"])
        matrices = tuple(tuple(tuple(row) for row in mat) for mat in d["matrices"])
        raw_counts = d.get("counts")
        if raw_counts:
            counts = tuple(tuple(tuple(row) for row in mat) for mat in raw_counts)
        else:
            counts = tuple((_ZERO_ROW, _ZERO_ROW) for _ in strata)
        return EmissionModel(strata=strata, matrices=matrices, counts=counts)


def _collapse_to_symbol_stratum(
    obs: Sequence["VintageObservation"], gap_days: int
) -> list[tuple[str, str]]:
    """One (symbol, stratum) pair per collapsed epoch — ALL epochs kept (incl.
    all-abstain ones), matching the decoder's has-emission-model branch. This is
    the single source of truth shared by ``changepoint.py``'s likelihood and
    this module's E/M steps."""
    return [(epoch_symbol(e), e.stratum) for e in collapse_epochs(obs, gap_days)]


def _init_matrices(strata: tuple[str, ...]) -> dict[str, Matrix]:
    """Deterministic init: P(correct)=0.9, P(flip)=0.05, P(abstain)=0.05, both
    states, every stratum (including ``default``)."""
    row_absent_state = (0.9, 0.05, 0.05)  # P(absent),P(present),P(abstain) | absent_state
    row_present_state = (0.05, 0.9, 0.05)  # P(absent),P(present),P(abstain) | present_state
    return {s: (row_absent_state, row_present_state) for s in strata}


def _seq_loglik_by_tau(seq: list[tuple[str, str]], matrices: dict[str, Matrix]) -> list[float]:
    """Score(tau) for tau in 0..T over a fixed (symbol, stratum) sequence.
    NEUTRAL epochs contribute 0 (excluded), matching the decoder's likelihood."""
    t = len(seq)
    scores: list[float] = []
    for tau in range(t + 1):
        s = 0.0
        for i, (symbol, stratum) in enumerate(seq):
            if symbol == "neutral":
                continue
            mat = matrices.get(stratum, matrices[DEFAULT_STRATUM])
            row = mat[1] if i >= tau else mat[0]
            p = max(row[SYMBOL_INDEX[symbol]], _EPS_FLOOR)
            s += math.log(p)
        scores.append(s)
    return scores


def _softmax(scores: list[float]) -> list[float]:
    m = max(scores)
    w = [math.exp(s - m) for s in scores]
    tot = sum(w)
    return [x / tot for x in w]


def fit_emissions_em(
    sequences: list[list["VintageObservation"]],
    *,
    gap_days: int,
    max_iters: int = 200,
    tol: float = 1e-9,
) -> EmissionModel:
    """Deterministic EM fit of the 3-symbol confusion matrix, stratified by
    ``quality_flag`` (plus a pooled ``"default"`` stratum over all counts).

    Latent variable = the single-changepoint index ``tau`` per sequence
    (uniform prior during fitting). E-step: exact posterior over ``tau`` under
    the CURRENT matrices (same O(T) enumeration as the decoder, T from
    ``collapse_epochs(seq, gap_days)``). M-step: expected symbol x state
    counts per stratum (NEUTRAL epochs excluded), add-k smoothing (k=1e-3),
    renormalize rows. No RNG; fixed init (see ``_init_matrices``); convergence
    = max absolute parameter delta < ``tol`` or ``max_iters`` reached — both
    fixed rules, so the fit is byte-identical across repeated runs on the same
    input regardless of dict/set iteration order (strata are sorted before
    use).
    """
    k = 1e-3
    seqs: list[list[tuple[str, str]]] = [
        _collapse_to_symbol_stratum(obs, gap_days) for obs in sequences
    ]
    seqs = [s for s in seqs if s]  # drop sequences with zero epochs (no evidence at all)

    strata_seen = sorted({stratum for s in seqs for (_symbol, stratum) in s})
    if DEFAULT_STRATUM in strata_seen:
        # DEFAULT_STRATUM ("default") is reserved for the pooled fallback row
        # this function appends below. If a real quality_flag value literally
        # equals "default", appending the pooled row would alias it onto the
        # real stratum's own accumulator cell (same dict key), silently
        # doubling that stratum's expected counts every EM epoch with no
        # error. Currently dormant: scan_state.py's QUALITY_FLAGS enum is
        # {"usable", "ambiguous", "unusable"} and never emits "default", but
        # any other observation source could. Fail loudly instead of
        # corrupting the fit.
        raise ValueError(
            f"quality_flag value {DEFAULT_STRATUM!r} collides with the reserved "
            "pooled-fallback stratum name; rename the input stratum or the "
            "DEFAULT_STRATUM sentinel before fitting"
        )
    strata: tuple[str, ...] = tuple(strata_seen) + (DEFAULT_STRATUM,)

    matrices = _init_matrices(strata)

    for _iteration in range(max_iters):
        # counts[stratum][state_idx] = [count_absent, count_present, count_abstain]
        counts: dict[str, list[list[float]]] = {
            s: [[0.0, 0.0, 0.0], [0.0, 0.0, 0.0]] for s in strata
        }
        for seq in seqs:
            t = len(seq)
            scores = _seq_loglik_by_tau(seq, matrices)
            post = _softmax(scores)  # posterior over tau, length t+1
            for i, (symbol, stratum) in enumerate(seq):
                if symbol == "neutral":
                    continue
                sidx = SYMBOL_INDEX[symbol]
                mass_present = sum(post[tau] for tau in range(t + 1) if i >= tau)
                mass_absent = 1.0 - mass_present
                counts[stratum][0][sidx] += mass_absent
                counts[stratum][1][sidx] += mass_present
                counts[DEFAULT_STRATUM][0][sidx] += mass_absent
                counts[DEFAULT_STRATUM][1][sidx] += mass_present

        new_matrices: dict[str, Matrix] = {}
        for s in strata:
            rows = []
            for state_idx in range(2):
                raw = counts[s][state_idx]
                total = sum(raw) + 3 * k
                rows.append(tuple((c + k) / total for c in raw))
            new_matrices[s] = (rows[0], rows[1])

        delta = max(
            abs(new_matrices[s][si][j] - matrices[s][si][j])
            for s in strata
            for si in range(2)
            for j in range(3)
        )
        matrices = new_matrices
        if delta < tol:
            break

    return EmissionModel(
        strata=strata,
        matrices=tuple(matrices[s] for s in strata),
        counts=tuple(tuple(tuple(row) for row in counts[s]) for s in strata),
    )
