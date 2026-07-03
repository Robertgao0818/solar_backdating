"""Turnbull survival core for the ISSUE-03 empirical-Bayes cohort prior (UNIT A).

Represents each anchor's install observation as an interval-censored datum over
calendar time and fits the nonparametric maximum-likelihood estimate (NPMLE) of
the per-calendar-year install distribution by a deterministic Turnbull
self-consistency EM. The fit is then exported as a ``CohortPrior`` the frozen
changepoint decoder (``estimators/changepoint.py``) consumes without any change
to its seam signature.

Why year atoms (not a monthly/weekly grid): the decoder scores a cell by
``sum_y exp(year_log_mass[y]) * frac(cell, y)`` at CALENDAR-YEAR granularity
(``changepoint._cell_log_prior`` / ``_year_day_fractions``). The Turnbull fit
here is exactly the MLE of the year masses ``pi_y`` under that same generative
model, so year atoms are the correct support — sub-year endpoint precision is
retained through the fractional boundary-year weights (``year_day_fractions``),
never through a finer time grid.

BEYOND atom: a single right-censoring sentinel absorbs anchors that are still
absent at the end of their observed cadence. Every JHB anchor is
detection-positive on the 2024 ortho, so in production ``beyond_mass`` is
empirically ~0; the atom exists for generality and for the synthetic tests.
``to_cohort_prior`` never exports ``log(beyond_mass ~ 0)`` — it sets
``beyond_log_mass`` by an explicit ``beyond_policy`` (default = terminal
support-year density), because ``log(~0)`` would over-penalize the undated cell.

Determinism contract (mirrors ``emissions.fit_emissions_em``): float64-scale
Python floats, fixed uniform EM init, no RNG, intervals canonically sorted
before the EM so the sums are byte-identical regardless of input order. The fit
records ``converged`` / ``n_iterations`` / ``final_loglik``.
"""
from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from solar_backdating.estimators.changepoint import CohortPrior, _year_day_fractions

__all__ = [
    "CensoringInterval",
    "TurnbullFit",
    "year_day_fractions",
    "fit_turnbull",
    "to_cohort_prior",
    "cohort_prior_to_json",
    "cohort_prior_from_json",
    "survival_curve",
    "CohortPrior",
]

_EPS = 1e-12
_BEYOND = "beyond"  # right-censoring sentinel atom key (str; never a real year)


@dataclass(frozen=True)
class CensoringInterval:
    """One anchor's interval-censored install observation over calendar time.

    ``(lower, upper]`` — start EXCLUSIVE, end INCLUSIVE, matching ``EpochCell``'s
    ``(start_date, end_date]`` convention. ``kind`` selects the censoring shape:

    - ``'interval'``: ``lower`` and ``upper`` both finite (dated bracket).
    - ``'left'``: ``lower is None`` — install predates the observed history
      (left-censored); only ``upper`` (earliest usable observation) is known.
    - ``'right'``: ``upper is None`` — still absent at the end of the cadence
      (right-censored); only ``lower`` (last usable absent) is known.

    The trailing fields are provenance/stratification carried through for the
    downstream cohort builder and gate reports (UNITS B/C/D); the Turnbull fit
    itself reads only ``lower``/``upper``/``kind``.
    """

    lower: date | None
    upper: date | None
    kind: str
    anchor_id: str = ""
    grid_id: str = ""
    status: str = ""
    recovered: bool = False
    n_usable: int = 0
    cadence_gap_days: float | None = None


@dataclass(frozen=True)
class TurnbullFit:
    """Result of a Turnbull self-consistency EM fit over calendar-year atoms.

    ``year_mass`` is a DENSE, ascending tuple over ``[min_year, max_year]`` (gap
    years carry mass 0.0); together with ``beyond_mass`` it sums to 1.0. The fit
    metadata (``n_iterations`` / ``converged`` / ``final_loglik``) makes the run
    self-describing and reproducible.
    """

    year_mass: tuple[tuple[int, float], ...]
    beyond_mass: float
    n_observations: int
    n_iterations: int
    converged: bool
    final_loglik: float


def year_day_fractions(start: date, end: date) -> dict[int, float]:
    """Public delegate to ``changepoint._year_day_fractions`` (single source of
    truth for the decoder's own cell fractioning). ``total_days <= 0`` returns
    ``{end.year: 1.0}`` (inherited)."""
    return _year_day_fractions(start, end)


def _interval_year_weights(
    interval: CensoringInterval, years: Sequence[int]
) -> tuple[dict[int, float], float]:
    """Compatibility weights ``a_iy`` of one interval over the year grid.

    Returns ``(year_weights, beyond_weight)``. ``year_weights`` omits years with
    zero weight. A year is compatible with the install being in that year to the
    degree the interval overlaps it:

    - ``interval (L,R]``: ``year_day_fractions(L, R)`` per touched year; BEYOND 0.
    - ``left (-inf,R]``: 1.0 for years ``< year(R)``; ``year(R)`` gets the
      fraction of that year up to ``R``; 0 above; BEYOND 0.
    - ``right (L,+inf)``: 1.0 for years ``> year(L)``; ``year(L)`` gets the
      fraction from ``L`` to year-end; 0 below; BEYOND 1.0.
    """
    if interval.kind == "interval":
        fr = _year_day_fractions(interval.lower, interval.upper)
        yw = {y: fr[y] for y in years if y in fr and fr[y] > 0.0}
        return yw, 0.0
    if interval.kind == "left":
        r = interval.upper
        yw: dict[int, float] = {}
        for y in years:
            if y < r.year:
                yw[y] = 1.0
            elif y == r.year:
                frac = _year_day_fractions(date(r.year, 1, 1), r).get(r.year, 1.0)
                if frac > 0.0:
                    yw[y] = frac
        return yw, 0.0
    if interval.kind == "right":
        lo = interval.lower
        yw = {}
        for y in years:
            if y > lo.year:
                yw[y] = 1.0
            elif y == lo.year:
                frac = _year_day_fractions(lo, date(lo.year + 1, 1, 1)).get(lo.year, 1.0)
                if frac > 0.0:
                    yw[y] = frac
        return yw, 1.0
    raise ValueError(f"unknown CensoringInterval.kind {interval.kind!r}")


def _sort_key(iv: CensoringInterval) -> tuple:
    """Total order for canonical iteration (open bounds sent to +/- sentinels)."""
    lo = iv.lower.toordinal() if iv.lower is not None else -(10**9)
    hi = iv.upper.toordinal() if iv.upper is not None else 10**9
    return (lo, hi, iv.kind, iv.anchor_id, iv.grid_id, iv.status)


def _finite_years(iv: CensoringInterval) -> list[int]:
    if iv.kind == "interval":
        return [iv.lower.year, iv.upper.year]
    if iv.kind == "left":
        return [iv.upper.year]
    if iv.kind == "right":
        return [iv.lower.year]
    raise ValueError(f"unknown CensoringInterval.kind {iv.kind!r}")


def fit_turnbull(
    intervals: Sequence[CensoringInterval], *, max_iters: int = 500, tol: float = 1e-10
) -> TurnbullFit:
    """Deterministic Turnbull NPMLE (self-consistency EM) over year atoms.

    Support = every calendar year in ``[min_finite_year, max_finite_year]``
    (dense) plus a BEYOND atom iff any right-censored interval is present.
    Uniform init over atoms carrying membership; E-step
    ``w_iy = pi_y a_iy / sum_k pi_k a_ik``; M-step ``pi_y = mean_i w_iy``; stop
    at ``max|dpi| < tol`` or ``max_iters``. No RNG; intervals canonically sorted
    so the fit is byte-identical across input permutations.
    """
    ordered = sorted(intervals, key=_sort_key)
    n = len(ordered)
    if n == 0:
        return TurnbullFit(
            year_mass=(),
            beyond_mass=0.0,
            n_observations=0,
            n_iterations=0,
            converged=True,
            final_loglik=0.0,
        )

    fin: list[int] = []
    for iv in ordered:
        fin.extend(_finite_years(iv))
    year_lo, year_hi = min(fin), max(fin)
    years = list(range(year_lo, year_hi + 1))
    has_beyond = any(iv.kind == "right" for iv in ordered)
    n_atoms = len(years) + (1 if has_beyond else 0)

    # Weight matrix W[i][j], atoms = years (ascending) then optional BEYOND last.
    weights: list[list[float]] = []
    for iv in ordered:
        yw, bw = _interval_year_weights(iv, years)
        row = [yw.get(y, 0.0) for y in years]
        if has_beyond:
            row.append(bw)
        weights.append(row)

    # Uniform init over atoms that any interval is compatible with.
    has_member = [any(weights[i][j] > 0.0 for i in range(n)) for j in range(n_atoms)]
    n_member = sum(has_member)
    pi = [1.0 / n_member if has_member[j] else 0.0 for j in range(n_atoms)]

    converged = False
    n_iter = 0
    delta = float("inf")
    for it in range(max_iters):
        n_iter = it + 1
        new = [0.0] * n_atoms
        for i in range(n):
            row = weights[i]
            denom = 0.0
            for j in range(n_atoms):
                if pi[j] > 0.0 and row[j] > 0.0:
                    denom += pi[j] * row[j]
            if denom <= 0.0:
                continue
            inv = 1.0 / denom
            for j in range(n_atoms):
                if pi[j] > 0.0 and row[j] > 0.0:
                    new[j] += pi[j] * row[j] * inv
        new = [v / n for v in new]
        delta = max(abs(new[j] - pi[j]) for j in range(n_atoms))
        pi = new
        if delta < tol:
            converged = True
            break

    final_loglik = 0.0
    for i in range(n):
        row = weights[i]
        acc = 0.0
        for j in range(n_atoms):
            if pi[j] > 0.0 and row[j] > 0.0:
                acc += pi[j] * row[j]
        final_loglik += math.log(max(acc, _EPS))

    year_mass = tuple((years[j], pi[j]) for j in range(len(years)))
    beyond_mass = pi[-1] if has_beyond else 0.0
    return TurnbullFit(
        year_mass=year_mass,
        beyond_mass=beyond_mass,
        n_observations=n,
        n_iterations=n_iter,
        converged=converged,
        final_loglik=final_loglik,
    )


def to_cohort_prior(
    fit: TurnbullFit,
    *,
    smooth_lambda: float = 0.05,
    beyond_policy: str = "terminal_year",
    temperature: float = 1.0,
) -> CohortPrior:
    """Export a Turnbull fit as the decoder's ``CohortPrior``.

    Support = every year in the fit's ``[min_year, max_year]`` (dense, so gap
    years and the phantom terminal year are covered). Each support year gets a
    Cromwell-floored mass ``p_y = (1-lambda) m_y + lambda/K`` (``K`` = support
    size), then ``year_log_mass[y] = temperature * log(p_y)`` — the floor
    guarantees no ``-inf`` inside the window. ``beyond_log_mass`` is set by an
    explicit ``beyond_policy`` (NEVER by the empirical ``beyond_mass``, which is
    ~0 in production and would tank the undated cell):

    - ``"terminal_year"`` (default): ``temperature * log(p_{max year})``.
    - ``"cohort_mean"``: ``temperature * log(mean_y p_y)``.
    - ``"terminal_minus_log2"``: ``terminal_year - log 2``.

    A global additive constant cancels in the decoder softmax, so the absolute
    normalization of the exported log-masses is immaterial.
    """
    if not fit.year_mass:
        return CohortPrior(year_log_mass=(), beyond_log_mass=0.0)

    m = dict(fit.year_mass)
    ymin = fit.year_mass[0][0]
    ymax = fit.year_mass[-1][0]
    support = list(range(ymin, ymax + 1))
    k = len(support)
    p = {y: (1.0 - smooth_lambda) * m.get(y, 0.0) + smooth_lambda / k for y in support}

    year_log_mass = tuple((y, temperature * math.log(p[y])) for y in support)

    terminal = temperature * math.log(p[ymax])
    if beyond_policy == "terminal_year":
        beyond_log_mass = terminal
    elif beyond_policy == "cohort_mean":
        beyond_log_mass = temperature * math.log(sum(p[y] for y in support) / k)
    elif beyond_policy == "terminal_minus_log2":
        beyond_log_mass = terminal - math.log(2.0)
    else:
        raise ValueError(f"unknown beyond_policy {beyond_policy!r}")

    return CohortPrior(year_log_mass=year_log_mass, beyond_log_mass=beyond_log_mass)


def cohort_prior_to_json(cp: CohortPrior) -> dict:
    """Plain-JSON view: ``{"year_log_mass": [[year, log_mass], ...],
    "beyond_log_mass": float}``."""
    return {
        "year_log_mass": [[int(y), float(lm)] for y, lm in cp.year_log_mass],
        "beyond_log_mass": float(cp.beyond_log_mass),
    }


def cohort_prior_from_json(d: dict) -> CohortPrior:
    """Inverse of ``cohort_prior_to_json`` (tuple round-trip)."""
    return CohortPrior(
        year_log_mass=tuple((int(y), float(lm)) for y, lm in d["year_log_mass"]),
        beyond_log_mass=float(d["beyond_log_mass"]),
    )


def survival_curve(fit: TurnbullFit) -> tuple[tuple[int, float], ...]:
    """``S(y) = P(T > y)`` step over the fitted year support, with the BEYOND
    mass folded into the tail residual. Non-increasing over ``[0, 1]``; empty
    fit -> empty curve."""
    if not fit.year_mass:
        return ()
    running = sum(p for _, p in fit.year_mass) + fit.beyond_mass
    out: list[tuple[int, float]] = []
    for year, mass in fit.year_mass:
        running -= mass
        out.append((year, running))
    return tuple(out)
