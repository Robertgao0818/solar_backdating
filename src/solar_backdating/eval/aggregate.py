"""Fractional posterior-mass aggregation for the ISSUE-03 gates (UNIT C).

Turns a cohort of ``InstallDatePosterior`` (or a Turnbull fit) into calendar-year
mass histograms and survival curves, and quantifies reproducibility with a
total-variation distance between reps.

Three cohort-year channels are kept deliberately separate because they answer
different questions and reproduce different published baselines:

- ``point`` (AC5 point channel): one hard vote per anchor at ``map_date``'s year
  — the ISSUE-02 point-date channel that produced the recorded rep-to-rep year
  TVD band ``[0.081 / 0.059 / 0.079]``. A one-day MAP flip across a year
  boundary moves a whole unit between bins, which is exactly the instability the
  mass channel is meant to absorb.
- ``midpoint`` (AC3 comparator): one hard vote at the interval midpoint's year —
  the ``infer_install_dates.install_mid_estimate`` analog.
- ``posterior`` (AC5 mass channel): each anchor's posterior mass spread
  FRACTIONALLY across years by ``survival.year_day_fractions`` — the exact same
  boundary-year fractioning the decoder itself uses in
  ``changepoint._cell_log_prior`` (single source of truth). A near-symmetric
  two-year posterior contributes ~0.5/0.5 regardless of which side the MAP
  landed on, so the cohort histogram — and its survival curve — is stable where
  the point channel is not.

``BEYOND`` (the right-censored / undated sentinel) is carried as a string key
alongside the integer years, so it never collides with a calendar year and
survives ``metrics.tvd``'s set-union iteration unchanged.

Determinism: pure arithmetic over sorted keys, no RNG. ``survival_tvd`` is a
thin delegate to ``metrics.tvd`` (one repo-wide TVD definition; the sup-norm
``|dS|`` companion metric is reported by UNIT D, not here).
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from datetime import timedelta
from typing import Literal

from solar_backdating.estimators import InstallDatePosterior
from solar_backdating.estimators.survival import TurnbullFit, year_day_fractions
from solar_backdating.eval.metrics import tvd

__all__ = [
    "BEYOND",
    "posterior_year_mass",
    "point_year",
    "midpoint_year",
    "cohort_year_histogram",
    "cohort_year_histogram_from_fit",
    "survival_curve_from_histogram",
    "survival_tvd",
    "midpoint_vs_posterior_report",
]

BEYOND = "beyond"  # right-censored / undated sentinel key (str; never a real year)

Mode = Literal["point", "midpoint", "posterior"]


def posterior_year_mass(post: InstallDatePosterior) -> dict[int | str, float]:
    """One anchor's fractional posterior mass over calendar years (+ ``BEYOND``).

    For each ``(cell, weight)`` in ``zip(post.epochs, post.posterior)``:

    - beyond-window cell -> all its weight goes to ``BEYOND``;
    - open-left cell (``start_date is None``) -> all its weight goes to
      ``end_date.year`` (the earliest-present bound is the only date it carries);
    - otherwise -> the weight is split across years by
      ``year_day_fractions(start_date, end_date)``.

    This mirrors ``changepoint._cell_log_prior``'s own cell fractioning exactly,
    so the returned masses sum to ``sum(post.posterior)`` (== 1.0 for a valid
    posterior).
    """
    out: dict[int | str, float] = {}
    for cell, weight in zip(post.epochs, post.posterior):
        if cell.is_beyond_window:
            out[BEYOND] = out.get(BEYOND, 0.0) + weight
        elif cell.start_date is None:
            y = cell.end_date.year
            out[y] = out.get(y, 0.0) + weight
        else:
            for y, frac in year_day_fractions(cell.start_date, cell.end_date).items():
                out[y] = out.get(y, 0.0) + weight * frac
    return out


def point_year(post: InstallDatePosterior) -> int | str:
    """AC5 point channel: ``int(map_date[:4])``, or ``BEYOND`` when undated.

    Reproduces ``panel_io.install_year`` / the ISSUE-02 point-date channel: one
    hard vote at the MAP year."""
    if post.map_date == "":
        return BEYOND
    return int(post.map_date[:4])


def midpoint_year(post: InstallDatePosterior) -> int | str:
    """AC3 comparator: the calendar year of the interval midpoint.

    ``install_mid_estimate`` analog — midpoint of ``(map_interval_start,
    map_interval_end]`` (``start + (end - start).days // 2``). ``BEYOND`` when
    undated (``map_date == ""`` or no earliest-present bound); when open-left
    (``map_interval_start is None``) the earliest-present ``end`` year is used,
    since there is no lower bound to average against.
    """
    if post.map_date == "" or post.map_interval_end is None:
        return BEYOND
    end = post.map_interval_end
    start = post.map_interval_start
    if start is None:
        return end.year
    mid = start + timedelta(days=(end - start).days // 2)
    return mid.year


def cohort_year_histogram(
    items: Iterable[tuple[InstallDatePosterior, float]], *, mode: Mode
) -> Counter:
    """Weighted cohort-year histogram (a ``Counter`` with float values).

    ``items`` is ``(posterior, weight)`` pairs (weight = inventory/inverse
    weight). ``mode`` selects the channel: ``"point"`` and ``"midpoint"`` cast
    one hard vote of ``weight`` at the anchor's point/midpoint year;
    ``"posterior"`` spreads ``weight`` fractionally by ``posterior_year_mass``.
    Every channel conserves total cohort mass (``sum == sum of weights``).
    """
    hist: Counter = Counter()
    for post, weight in items:
        if mode == "point":
            hist[point_year(post)] += weight
        elif mode == "midpoint":
            hist[midpoint_year(post)] += weight
        elif mode == "posterior":
            for key, mass in posterior_year_mass(post).items():
                hist[key] += weight * mass
        else:  # pragma: no cover - guarded by Literal
            raise ValueError(f"unknown mode {mode!r}")
    return hist


def cohort_year_histogram_from_fit(fit: TurnbullFit) -> Counter:
    """Cohort-year PMF straight from a Turnbull fit (float ``Counter``).

    Each fitted year contributes its mass; ``beyond_mass`` (if any) goes to
    ``BEYOND``. Equals the summed ``posterior_year_mass`` of a cohort whose
    decoder posteriors coincide with the Turnbull atoms."""
    hist: Counter = Counter()
    for year, mass in fit.year_mass:
        hist[year] += mass
    if fit.beyond_mass > 0.0:
        hist[BEYOND] += fit.beyond_mass
    return hist


def survival_curve_from_histogram(hist: Counter) -> tuple[tuple[int, float], ...]:
    """``S(y) = P(T > y)`` step over the histogram's finite years.

    The histogram is normalized to a PMF, then the survival value at each
    ascending year is the residual mass strictly above it; ``BEYOND`` mass is
    never subtracted, so it stays folded into the tail residual (an install
    beyond every observed year exceeds all finite ``y``). Non-increasing, in
    ``[0, 1]``; empty / zero-mass histogram -> empty curve.
    """
    total = sum(hist.values())
    if total <= 0.0:
        return ()
    years = sorted(k for k in hist if not isinstance(k, str))
    running = 1.0
    out: list[tuple[int, float]] = []
    for y in years:
        running -= hist[y] / total
        out.append((y, max(running, 0.0)))
    return tuple(out)


def survival_tvd(hist_a: Counter, hist_b: Counter) -> float:
    """Total-variation distance between two cohort-year PMFs.

    Documented thin delegate to ``metrics.tvd`` (one repo-wide TVD definition)
    so the survival channel and the point channel are scored on the same ruler.
    Symmetric; ``0`` on identical histograms. UNIT D reports the companion
    sup-norm ``|dS|`` separately."""
    return tvd(hist_a, hist_b)


def midpoint_vs_posterior_report(
    items: Iterable[tuple[InstallDatePosterior, float]],
) -> dict:
    """AC3: contrast the point / midpoint / posterior cohort-year channels.

    Returns the three histograms, the per-year ``midpoint - posterior`` delta
    (mass-conserving, so it sums to ~0), the ``l1_shift`` (sum of absolute
    deltas), and dated / undated anchor counts. ``items`` is materialized once
    so the three channels see the identical cohort.
    """
    materialized = list(items)
    point_hist = cohort_year_histogram(materialized, mode="point")
    midpoint_hist = cohort_year_histogram(materialized, mode="midpoint")
    posterior_hist = cohort_year_histogram(materialized, mode="posterior")

    keys = set(midpoint_hist) | set(posterior_hist)
    per_year_delta = {k: midpoint_hist[k] - posterior_hist[k] for k in keys}
    l1_shift = sum(abs(v) for v in per_year_delta.values())

    n_undated = sum(1 for post, _ in materialized if point_year(post) == BEYOND)
    n_dated = len(materialized) - n_undated

    return {
        "point_hist": point_hist,
        "midpoint_hist": midpoint_hist,
        "posterior_hist": posterior_hist,
        "per_year_delta": per_year_delta,
        "l1_shift": l1_shift,
        "n_dated": n_dated,
        "n_undated": n_undated,
    }
