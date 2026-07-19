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

Alias note (DESIGN-phase0-emission-extension, decision D-E1, 2026-07-19): the
product-layer three-state label ``present``/``absent``/``uninformative``
(``+corrupt``) is the SAME object as this module's code-layer symbol triple
``present``/``absent``/``abstain``. ``"abstain"`` == "uninformative(+corrupt)";
``SYMBOLS`` is deliberately NOT renamed (renaming would churn ``SYMBOL_INDEX``,
``changepoint.py``, ``fit_emissions_em`` and break every DECISION-A artifact /
test that already keys on ``"abstain"``). The frame-level student extension
below (``FrameEmission`` / ``frame_loglik``) is the continuous generalization
of the same uninformative-marginalization invariant ``_epoch_loglik`` already
enforces on the discrete symbol: ``q -> {0, 1}`` reproduces the two existing
branches exactly (constant-marginalized abstain at ``q=0``; hard scored-symbol
decode at ``q=1``). ``SYMBOLS`` / ``EmissionModel`` / ``fit_emissions_em`` are
untouched — the student path is a permanent fork (decision D-E2), not an EM
successor.
"""
from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import TYPE_CHECKING

from solar_backdating.estimators.epochs import collapse_epochs, epoch_symbol

if TYPE_CHECKING:
    from solar_backdating.estimators.seam import VintageObservation
    from solar_backdating.localization.observation import TargetLocalizationObservation

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


# --------------------------------------------------------------------------- #
# Frame-level (student) emission extension — DESIGN-phase0-emission-extension
# (owner-signed 2026-07-19). The three symbols below are the CONTINUOUS
# generalization of the discrete symbol/EM path above; everything from here
# down is inert unless a caller sets ``EstimatorConfig.frame_emissions``. See
# the module docstring's alias note for why ``SYMBOLS`` is not renamed.
# --------------------------------------------------------------------------- #

# Deep-defense filler for e0/e1 when the §3.2 TLO gate zeroes q (see
# ``gate_frame_emission``): 0.5 is the no-information prior value, so even a
# code path that forgets to check q before reading e0/e1 gets an uninformative
# read, never a spurious present/absent lean.
_GATED_EMISSION_FILLER = 0.5


@dataclass(frozen=True)
class FrameEmission:
    """One student frame's decode-time consumption unit (design §1.2).

    ``q`` : P(usable & localized | x_t) — ALREADY hard-ANDed with the
        localization observation upstream (``gate_frame_emission`` below is the
        §3.2 gate that produces a gated ``q``); this dataclass does NOT re-apply
        that gate.
    ``e0``: P(observe "absent"  | state=absent_state,  usable & localized).
    ``e1``: P(observe "present" | state=present_state, usable & localized).
        Only two numbers are needed (present/absent is binary within each
        state; ``1-e0`` / ``1-e1`` are the other halves), mirroring the
        discrete ``Matrix``'s two rows ``row_absent_state=(e0, 1-e0)`` /
        ``row_present_state=(1-e1, e1)``.
    ``stratum``: reserved key (A24/A48 × area band × era, design §4.3); today
        every frame may carry ``"default"`` — not consumed by the minimal
        decode.
    """

    capture_date: date
    q: float
    e0: float
    e1: float
    stratum: str = DEFAULT_STRATUM
    source_row: int | None = None


def frame_loglik(frame: FrameEmission, state_is_present: bool) -> float:
    """``log( q * e(state) + (1 - q) )`` — design §1.2.

    ``e(state)`` is ``e1`` when ``state_is_present`` else ``e0``. This is the
    exact Bernoulli marginalization of a latent ``usable & localized`` indicator
    ``u_t`` independent of PV state (design §3.1): ``u_t=1`` emits a usable
    symbol with prob ``e(state)``; ``u_t=0`` emits nothing (contributes constant
    ``1``). Limits:

    - ``q = 0``  -> ``log(1) = 0.0`` exactly, for BOTH states (state-independent
      constant that cancels in the softmax — strictly identical to the discrete
      abstain-drop's zero contribution; design §2.4).
    - ``q = 1``  -> ``log(e(state))`` (degenerates to hard scored-symbol decode).
    - ``0<q<1``  -> log of the convex combination, monotone in ``q`` for fixed
      ``e != 0.5``.

    ``state_is_present`` only affects the ``e(state)`` term, so at ``q -> 0``
    both state branches collapse to the same ``log(1) = 0`` constant — the
    design §2.4 boundary-cell evidence-completeness invariant.
    """
    e = frame.e1 if state_is_present else frame.e0
    value = frame.q * e + (1.0 - frame.q)
    return math.log(max(value, _EPS_FLOOR))


def gate_frame_emission(
    capture_date: date,
    q_model: float,
    e0: float,
    e1: float,
    tlo: "TargetLocalizationObservation | None",
    *,
    stratum: str = DEFAULT_STRATUM,
    source_row: int | None = None,
) -> tuple[FrameEmission, bool]:
    """Build a FrameEmission with the §3.2 TLO hard-gate applied to ``q``.

    Returns ``(frame, localization_pending)``. This is the SINGLE canonical
    normative three-branch gate of design §3.2 — deliberately NOT collapsed into
    a single ``q if (tlo and tlo.target_localized) else 0.0`` expression, which
    would fold the ``tlo is None`` bridge state into the zeroing branch (exactly
    the seam the schema-author cross-check flagged). Both ``target_localized``
    AND ``abstain`` are read: the schema permits the legal combination
    ``target_localized=True ∧ abstain=True`` (e.g. ``low_confidence`` /
    ``dark_zone``), which the label layer downgrades to uninformative, so the
    likelihood layer must zero ``q`` there too.

    - ``tlo is None`` (bridge state, the actual status of every 311k observation
      today, pre-R2): NO-OP. ``q`` passes through unchanged and
      ``localization_pending=True`` is returned as provenance — never an
      implicit "not localized" zeroing.
    - ``tlo.target_localized and not tlo.abstain``: full weight, ``q = q_model``.
    - else: ``q = 0.0``, and ``e0 = e1 = 0.5`` (deep defense — q=0 already makes
      them mathematically inert, the filler only stops a q-forgetting code path
      from reading a leaning value; design §3.2).
    """
    if tlo is None:
        # Bridge state: localization layer has not run this observation. Do NOT
        # zero q — that would penalize every legacy observation with no
        # evidence it failed localization (contra effective_label's own
        # "preserve legacy, localization_pending=True" policy).
        q_effective = q_model
        localization_pending = True
        e0_effective, e1_effective = e0, e1
    elif tlo.target_localized and not tlo.abstain:
        q_effective = q_model
        localization_pending = False
        e0_effective, e1_effective = e0, e1
    else:
        q_effective = 0.0
        localization_pending = False
        e0_effective = e1_effective = _GATED_EMISSION_FILLER
    frame = FrameEmission(
        capture_date=capture_date,
        q=q_effective,
        e0=e0_effective,
        e1=e1_effective,
        stratum=stratum,
        source_row=source_row,
    )
    return frame, localization_pending


@dataclass(frozen=True)
class PooledEpochEmission:
    """Continuous epoch aggregate — the frame-level analogue of a collapsed
    ``Epoch`` (design §1.4). ``q`` / ``(e0, e1)`` follow the indicator-of-max-q
    "representative frame" rule so injecting near-duplicate frames can only hold
    ``q`` flat, never inflate it (preserves ``pava.py``'s "one epoch = one
    evidence unit" invariant)."""

    start_date: date
    end_date: date
    q: float  # = max(member.q)
    e0: float  # taken from the argmax-q member ("representative frame")
    e1: float
    n_members: int


def pool_epoch_frame_emissions(
    frames: Sequence[FrameEmission], gap_days: int
) -> list[PooledEpochEmission]:
    """Gap-collapse frames into epochs, one ``PooledEpochEmission`` each.

    Grouping is the same greedy gap walk as ``epochs.collapse_epochs`` (sort by
    ``(capture_date, source_row or 0)``, split when the next frame is more than
    ``gap_days`` after the running last date). Per epoch (design §1.4, the
    recommended "representative frame" rule, NOT soft-OR / weighted average):

    - ``q_epoch = max(member.q)`` — indicator-style, so an epoch's usability
      confidence never rises just because more correlated near-duplicate frames
      were injected into it.
    - ``(e0, e1)_epoch`` are taken from the argmax-q member (ties broken by
      ``(capture_date, source_row or 0)``, so the earliest member wins — an
      injected later near-duplicate at the same q cannot displace it).

    Does NOT drop low-q epochs: that ``q < eps`` "epoch is abstain" filter is
    the decoder's job (``changepoint.py``'s frame branch), mirroring how
    ``collapse_epochs`` keeps all epochs and ``estimate_changepoint`` filters on
    ``epoch_symbol(e) != "abstain"``.
    """
    if not frames:
        return []

    ordered = sorted(frames, key=lambda f: (f.capture_date, f.source_row or 0))

    groups: list[list[FrameEmission]] = []
    current: list[FrameEmission] = [ordered[0]]
    last_date = ordered[0].capture_date
    for frame in ordered[1:]:
        if (frame.capture_date - last_date).days <= gap_days:
            current.append(frame)
        else:
            groups.append(current)
            current = [frame]
        last_date = frame.capture_date
    groups.append(current)

    pooled: list[PooledEpochEmission] = []
    for group in groups:
        representative = min(
            group, key=lambda f: (-f.q, f.capture_date, f.source_row or 0)
        )
        pooled.append(
            PooledEpochEmission(
                start_date=group[0].capture_date,
                end_date=group[-1].capture_date,
                q=representative.q,
                e0=representative.e0,
                e1=representative.e1,
                n_members=len(group),
            )
        )
    return pooled
