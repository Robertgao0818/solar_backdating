"""PAVA / isotonic single-changepoint floor (registered as ``"pava"``).

The mandatory falsification floor (PRD D2/D3, US-6). Pure, deterministic, no
RNG, no EM. It collapses observations into epochs (D2), then enumerates the
T+1 monotone single-step absent->present hypotheses tau in {0..T} — exactly the
isotonic (PAVA) fit restricted to one 0->1 step — scores each under a fixed
symmetric emission noise epsilon, and returns the pseudo-posterior over
changepoints as an ``InstallDatePosterior``.

Clamp scope (floor, minimal): PAVA applies ONLY clamp #1, the phantom-future
cap — if ``clamp.ceiling_date`` is set and the dated earliest-present bound
exceeds it, the bound and ``map_date`` are clamped down and the raw value is
preserved in ``notes``. The fuller ``clamp_inverted`` / ``marker_missed_pv``
reclassifications are ISSUE-02 decoder scope, deliberately out of the floor.
"""
from __future__ import annotations

import math
from collections.abc import Sequence

from solar_backdating.estimators.epochs import Epoch, collapse_epochs
from solar_backdating.estimators.seam import (
    ClampContext,
    EpochCell,
    EstimatorConfig,
    InstallDatePosterior,
    VintageObservation,
    register,
)

_EPS_FLOOR = 1e-12


def _undated_result(epochs_cells: tuple[EpochCell, ...], last_absent, notes: str):
    n_cells = len(epochs_cells)
    posterior = tuple(1.0 if i == n_cells - 1 else 0.0 for i in range(n_cells))
    return InstallDatePosterior(
        estimator="pava",
        epochs=epochs_cells,
        posterior=posterior,
        map_index=n_cells - 1,
        map_interval_start=last_absent,
        map_interval_end=None,
        p_undated=1.0,
        credible_low_date=None,
        credible_high_date=None,
        credible_mass=1.0,
        map_date="",
        notes=notes,
    )


def _build_cells(epochs: list[Epoch]) -> tuple[EpochCell, ...]:
    """Build the tau-indexed EpochCell tuple (length T+1)."""
    t = len(epochs)
    cells: list[EpochCell] = []
    for tau in range(t + 1):
        if tau == 0:
            start, end, beyond = None, epochs[0].start_date, False
        elif tau < t:
            start, end, beyond = epochs[tau - 1].end_date, epochs[tau].start_date, False
        else:  # tau == t
            start, end, beyond = epochs[t - 1].end_date, None, True
        cells.append(
            EpochCell(index=tau, start_date=start, end_date=end, is_beyond_window=beyond)
        )
    return tuple(cells)


@register("pava")
def estimate_pava(
    observations: Sequence[VintageObservation],
    clamp: ClampContext = ClampContext(),
    config: EstimatorConfig = EstimatorConfig(),
) -> InstallDatePosterior:
    all_epochs = collapse_epochs(observations, config.epoch_gap_days)
    # Drop all-abstain epochs: they carry no presence/absence evidence and would
    # otherwise insert phantom changepoint brackets on abstain-only dates (§3.5:
    # "inserting '' frames does not shift map_index"). Abstain members that share
    # an epoch with scored frames are retained via that epoch's n_abstain.
    epochs = [e for e in all_epochs if e.n_present > 0 or e.n_absent > 0]
    t = len(epochs)

    # No scored evidence anywhere (empty input or all-abstain) -> cannot date.
    if t == 0:
        beyond = (EpochCell(index=0, start_date=None, end_date=None, is_beyond_window=True),)
        reason = "no_observations" if not all_epochs else "no_scored_evidence"
        return _undated_result(beyond, None, reason)

    cells = _build_cells(epochs)
    last_absent = epochs[t - 1].end_date  # lower bound if undated

    eps = min(max(config.flip_rate, _EPS_FLOOR), 1.0 - _EPS_FLOOR)
    log_hi = math.log(1.0 - eps)
    log_lo = math.log(eps)

    # Per-epoch INDICATOR emission (D2 / AC5): each collapsed epoch contributes at
    # most ONE unit of present/absent evidence, independent of member COUNT. A
    # count-weighted likelihood (n_present*log_hi + n_absent*log_lo) double-counts
    # correlated near-duplicate frames — the exact effect D2's epoch collapse must
    # avoid — so injecting a duplicate / near-duplicate frame into an epoch could
    # sharpen or flip the MAP. Reducing every epoch to its majority verdict makes
    # the decode invariant under such injections: a mixed epoch takes its majority
    # verdict; an evenly-split epoch is neutral (contributes 0 to every tau, so it
    # cancels in the softmax and shifts no changepoint).
    loglik_present: list[float] = []
    loglik_absent: list[float] = []
    for e in epochs:
        if e.n_present > e.n_absent:
            loglik_present.append(log_hi)
            loglik_absent.append(log_lo)
        elif e.n_absent > e.n_present:
            loglik_present.append(log_lo)
            loglik_absent.append(log_hi)
        else:  # evenly-split scored members -> uninformative epoch
            loglik_present.append(0.0)
            loglik_absent.append(0.0)

    # Score(tau) = sum absent[i<tau] + sum present[i>=tau] + prior.
    scores: list[float] = []
    for tau in range(t + 1):
        s = sum(loglik_absent[:tau]) + sum(loglik_present[tau:])
        s += config.prior_weight * 0.0  # flat log-prior (placeholder for future prior)
        scores.append(s)

    m = max(scores)
    weights = [math.exp(s - m) for s in scores]
    total = sum(weights)
    posterior = tuple(w / total for w in weights)

    # MAP: argmax, ties broken by SMALLEST tau.
    map_index = 0
    best = posterior[0]
    for i in range(1, t + 1):
        if posterior[i] > best:
            best = posterior[i]
            map_index = i

    p_undated = posterior[t]

    # MAP interval / date from tau*.
    notes = ""
    if map_index == t:
        map_interval_start = last_absent
        map_interval_end = None
        map_date = ""
    else:
        map_interval_start = cells[map_index].start_date  # None if tau*=0
        map_interval_end = cells[map_index].end_date
        map_date = map_interval_end.isoformat()
        if clamp.ceiling_date is not None and map_interval_end > clamp.ceiling_date:
            raw = map_interval_end.isoformat()
            map_interval_end = clamp.ceiling_date
            map_date = clamp.ceiling_date.isoformat()
            notes = f"clamped_earliest_present {raw}->{clamp.ceiling_date.isoformat()}"

    # HPD: accumulate cells by descending posterior (ties by index asc).
    order = sorted(range(t + 1), key=lambda i: (-posterior[i], i))
    included: list[int] = []
    cum = 0.0
    for i in order:
        included.append(i)
        cum += posterior[i]
        if cum >= config.credible_mass:
            break
    credible_mass = cum

    non_beyond = [i for i in included if not cells[i].is_beyond_window]
    beyond_included = any(cells[i].is_beyond_window for i in included)
    if not non_beyond or any(cells[i].start_date is None for i in non_beyond):
        credible_low = None
    else:
        credible_low = min(cells[i].start_date for i in non_beyond)
    if beyond_included or not non_beyond:
        credible_high = None
    else:
        credible_high = max(cells[i].end_date for i in non_beyond)

    return InstallDatePosterior(
        estimator="pava",
        epochs=cells,
        posterior=posterior,
        map_index=map_index,
        map_interval_start=map_interval_start,
        map_interval_end=map_interval_end,
        p_undated=p_undated,
        credible_low_date=credible_low,
        credible_high_date=credible_high,
        credible_mass=credible_mass,
        map_date=map_date,
        notes=notes,
    )
