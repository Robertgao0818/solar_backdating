"""Monotone single-changepoint posterior decoder (registered as ``"changepoint"``).

ISSUE-02. Exact O(T) enumeration of a latent absent->present single changepoint
tau over collapsed epochs {0..T-1} plus a right-censored beyond-window cell
{T} — no HMM / forward-backward machinery (the monotone single-changepoint
structure makes exact enumeration sufficient; BOCPD and general HMMs are
explicitly rejected, see the replan's section 4).

Epoch collapsing reuses ``estimators/epochs.py::collapse_epochs`` at
``config.decoder_epoch_gap_days`` (default 30, NOT PAVA's 16 — justified by
the panel's empirical gap distribution: median inter-vintage gap ~31 d, 42.5%
of gaps <=30 d, so a ~30 d threshold catches the near-duplicate cluster
without merging genuinely distinct vintages; PAVA keeps its own 16 d default
untouched, both are independent ``EstimatorConfig`` fields).

Emissions: a 3-symbol confusion matrix ``P(symbol | state, stratum)`` fitted
cohort-wide by EM (``estimators/emissions.py``), stratified by ``quality_flag``
ONLY — the banked panel has no zoom/imagery-era column (D17/ISSUE-18
territory; see ``emissions.py`` docstring). ``config.emissions is None`` falls
back to PAVA's symmetric noise (``P(opposite|state) = config.flip_rate``) with
abstain epochs dropped — this is the "symmetric noise + flat prior" reference
point that makes the decoder's MAP reduce to (a strict generalization of)
PAVA's single-changepoint rule on that config, per D1(ii)/D2's "sustained is
its MAP under symmetric noise + flat prior" framing.

Censoring-exact decode (orchestrator design correction 2, 2026-07-03):
``_epoch_loglik`` scores present/absent epochs with the SCORED-CONDITIONAL
emission probability, ``P(sym|state,scored) = P(sym|state) / (P(absent|state)
+ P(present|state))`` — i.e. the fitted 3-symbol row renormalized over its
``{absent, present}`` columns only, dropping the abstain column entirely from
the decode-time likelihood. This is required by ISSUE-04 doctrine: abstain is
resolution-caused CENSORING, not state-dependent evidence, so by construction
it must be state-independent (a constant that cancels in the softmax) rather
than something an emission matrix should learn a per-state probability for.
Modeling abstain as state-dependent would require cross-domain calibration
data this panel does not have -- proof: the fitted ``ambiguous``/``unusable``
strata's ``present_state`` row has EXACTLY ZERO raw EM training counts (every
ambiguous/unusable-stratum epoch's E-step mass landed under absent_state
across the whole 380-sequence cohort), so that row is pure add-k-smoothing
uniform filler, not a learned probability -- treating it as informative (the
pre-correction-2 code did) silently reconstructed near-full "absent" evidence
strength out of an artifact with zero support. The EmissionModel itself keeps
its full 3-symbol shape and fitted abstain rates (``fit_emissions_em`` is
UNCHANGED) for provenance / future D17 zoom-stratified abstain-rate work --
only this module's decode-time read of it changed.

Prior: flat by default; ``config.cohort_prior`` is the ISSUE-03 empirical-Bayes
hook (``CohortPrior``, below) — deliberately minimal in this issue (a Turnbull
hazard artifact plugs in without changing the seam signature).

Epoch-collapse invariance property (D2 AC5), REVISED by correction 2: unlike
the original ISSUE-02 landing, this decoder now DROPS abstain epochs from both
the likelihood AND the tau-cell lattice, uniformly, regardless of whether an
emission model is configured -- exactly like PAVA drops all-abstain epochs,
extended to ``epochs.epoch_symbol()``'s USABLE-only definition: an epoch with
scored verdicts that are all ``ambiguous``/``unusable``-flagged is also
abstain (no usable evidence), not just an epoch with zero scored verdicts at
all. Non-usable epochs therefore no longer define interval boundaries either
-- ``map_interval_start`` is the last usable-scored absent epoch's
``end_date``, matching production's own usable-anchored bound semantics
(``scan_decision.py::usable_observations`` / ``infer_install_dates.py::
_usable``). The prior "resolution-conditioning" framing (abstain-heavy epochs
under a fitted asymmetric matrix CAN shift the MAP) is SUPERSEDED: it produced
a data artifact (see the Censoring-exact paragraph above), not a real
resolution-conditioning signal, and correction 2 removes that behavior.
NEUTRAL-tie epochs (a tie among usable-scored members, at least one present)
are UNCHANGED: still kept in the cell lattice (mirroring PAVA, which also
keeps tied epochs), still contribute zero log-likelihood at every tau -- this
preserves the duplicate-injection invariance property (injecting a frame that
only ever creates or resolves a tie must not silently delete a cell boundary).

Cutoff/clamp scope (ISSUE-26): ``ceiling_date`` (preferred per-grid flight
date) or ``census_end_date`` (fallback) excludes later reference frames before
epoch collapse, so they cannot affect the likelihood or cell lattice.
The cutoff changes the posterior by construction. The legacy
``clamped_earliest_present`` / ``clamp_inverted`` / ``marker_missed_pv``
defenses remain in place after decoding and only change ``map_date``/``notes``.
"""
from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from solar_backdating.estimators.emissions import (
    SYMBOL_INDEX,
    frame_loglik,
    pool_epoch_frame_emissions,
)
from solar_backdating.estimators.epochs import Epoch, collapse_epochs, epoch_symbol
from solar_backdating.estimators.seam import (
    ClampContext,
    EpochCell,
    EstimatorConfig,
    InstallDatePosterior,
    VintageObservation,
    register,
)

_EPS_FLOOR = 1e-12
_LOG_FLOOR = -700.0  # exp(-700) ~ 1e-304; safely above double underflow, effectively "excluded"
# "Epoch is abstain" threshold for the continuous frame branch (design §4.1):
# a PooledEpochEmission with q below this near-zero floor carries no usable
# evidence and is dropped from the tau lattice, exactly mirroring the discrete
# branch's ``epoch_symbol(e) != "abstain"`` filter. Same 1e-3 magnitude as
# emissions.py's add-k smoothing constant, by design.
_FRAME_ABSTAIN_EPS = 1e-3


@dataclass(frozen=True)
class CohortPrior:
    """ISSUE-03 empirical-Bayes hook: a per-calendar-year log-mass prior over
    the install year, plus a scalar log-mass for the beyond-window (never
    installed within the observed cadence) cell.

    ``year_log_mass``: ``((year, log_mass), ...)``. Years absent from this
    tuple contribute zero mass. A cell's log-prior is
    ``log( sum_y exp(year_log_mass[y]) * frac(cell, y) )`` where ``frac(cell,
    y)`` is the fraction of the cell's ``(start_date, end_date]`` day-span
    falling in calendar year ``y`` (the open-left cell, ``start_date is
    None``, has no span to fraction over and is scored entirely by
    ``end_date``'s year — the earliest-present bound is the only date the
    open-left cell actually carries). ``beyond_log_mass`` scores the
    right-censored beyond-window cell directly (no year fractioning).

    ``config.cohort_prior is None`` -> flat prior (identical to
    ``log_prior(cell) == 0`` for every cell), matching every other estimator
    in this repo before ISSUE-03 lands.
    """

    year_log_mass: tuple[tuple[int, float], ...]
    beyond_log_mass: float


def _year_day_fractions(start, end) -> dict[int, float]:
    """Fraction of the ``total_days = (end-start).days`` in ``(start, end]``
    that fall in each calendar year, computed by year-boundary walk (O(years),
    not O(days))."""
    total_days = (end - start).days
    if total_days <= 0:
        return {end.year: 1.0}
    from datetime import date as _date

    counts: dict[int, int] = {}
    year = start.year
    cur = start
    while cur < end:
        year_end = _date(year + 1, 1, 1)
        seg_end = year_end if year_end < end else end
        seg_days = (seg_end - cur).days
        if seg_days > 0:
            counts[year] = counts.get(year, 0) + seg_days
        cur = seg_end
        year += 1
    return {y: c / total_days for y, c in counts.items()}


def _cell_log_prior(cell: EpochCell, prior: CohortPrior | None) -> float:
    if prior is None:
        return 0.0
    if cell.is_beyond_window:
        return prior.beyond_log_mass
    year_mass = dict(prior.year_log_mass)
    if cell.start_date is None:
        fracs = {cell.end_date.year: 1.0}
    else:
        fracs = _year_day_fractions(cell.start_date, cell.end_date)
    total = 0.0
    for y, frac in fracs.items():
        if y in year_mass:
            total += math.exp(year_mass[y]) * frac
    if total <= 0.0:
        return _LOG_FLOOR
    return math.log(total)


def _epoch_loglik(
    symbol: str, state_is_present: bool, stratum: str, config: EstimatorConfig
) -> float:
    # symbol is never "abstain" here: estimate_changepoint drops abstain-symbol
    # epochs before this is called (uniformly, both with and without a fitted
    # emission model -- see the "Epoch-collapse invariance property" docstring
    # paragraph above), since correction 2 makes abstain state-independent by
    # construction, not something to score per-state at all.
    if symbol == "neutral":
        return 0.0
    if config.emissions is not None:
        matrix = config.emissions.lookup(stratum)
        row = matrix[1] if state_is_present else matrix[0]
        # Censoring-exact (correction 2A): condition on "scored" by
        # renormalizing over the {absent, present} columns only, so the
        # fitted abstain probability -- including the degenerate
        # zero-training-count uniform rows sparse strata get from add-k
        # smoothing -- can never leak into the scored-symbol likelihood.
        denom = max(
            row[SYMBOL_INDEX["absent"]] + row[SYMBOL_INDEX["present"]], _EPS_FLOOR
        )
        p = max(row[SYMBOL_INDEX[symbol]] / denom, _EPS_FLOOR)
        return math.log(p)
    # Symmetric-noise fallback (PAVA-equivalent): already a 2-outcome,
    # scored-only model by construction (no abstain probability exists here).
    eps = min(max(config.flip_rate, _EPS_FLOOR), 1.0 - _EPS_FLOOR)
    log_hi = math.log(1.0 - eps)
    log_lo = math.log(eps)
    if symbol == "present":
        return log_hi if state_is_present else log_lo
    return log_lo if state_is_present else log_hi  # symbol == "absent"


def _build_cells(epochs: list[Epoch]) -> tuple[EpochCell, ...]:
    """Build the tau-indexed EpochCell tuple (length T+1). Identical shape/logic
    to ``pava._build_cells`` — deliberately duplicated (not imported) so this
    module never touches ``pava.py``."""
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


def _undated_result(epochs_cells: tuple[EpochCell, ...], last_absent, notes: str):
    n_cells = len(epochs_cells)
    posterior = tuple(1.0 if i == n_cells - 1 else 0.0 for i in range(n_cells))
    return InstallDatePosterior(
        estimator="changepoint",
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


def _decode_frame_emissions(
    clamp: ClampContext, config: EstimatorConfig
) -> InstallDatePosterior:
    """Continuous (student) frame branch — design §4.1. Structurally parallel to
    the discrete decode below but scores ``frame_loglik`` over pooled frames
    instead of ``_epoch_loglik`` over collapsed symbols. Scope is deliberately
    minimal (the census synthetic-epoch injection and marker_missed_pv /
    clamp_inverted report-layer reclassifications are NOT reproduced here — the
    frame branch is training/calibration-facing and no gate exercises those on
    it today); it applies only clamp #1 (phantom-future cap), like PAVA. The TLO
    hard-gate is already baked into each frame's ``q`` upstream
    (``gate_frame_emission``), so it is not re-applied here."""
    pooled_all = pool_epoch_frame_emissions(
        config.frame_emissions, config.decoder_epoch_gap_days
    )
    # Drop q<eps epochs (the continuous "epoch is abstain" filter, §4.1) — the
    # frame analogue of the discrete branch's abstain-symbol drop.
    epochs = [p for p in pooled_all if p.q >= _FRAME_ABSTAIN_EPS]
    t = len(epochs)

    if t == 0:
        beyond = (EpochCell(index=0, start_date=None, end_date=None, is_beyond_window=True),)
        reason = "no_observations" if not pooled_all else "no_scored_evidence"
        return _undated_result(beyond, None, reason)

    cells = _build_cells(epochs)  # duck-typed on .start_date/.end_date; PooledEpochEmission fits
    last_absent = epochs[t - 1].end_date

    scores: list[float] = []
    for tau in range(t + 1):
        s = 0.0
        for i in range(t):
            s += frame_loglik(epochs[i], i >= tau)
        s += _cell_log_prior(cells[tau], config.cohort_prior)
        scores.append(s)

    m = max(scores)
    weights = [math.exp(s - m) for s in scores]
    total = sum(weights)
    posterior = tuple(w / total for w in weights)

    map_index = 0
    best = posterior[0]
    for i in range(1, t + 1):
        if posterior[i] > best:
            best = posterior[i]
            map_index = i

    p_undated = posterior[t]

    notes: list[str] = []
    if map_index == t:
        map_interval_start = last_absent
        map_interval_end = None
        map_date = ""
    else:
        map_interval_start = cells[map_index].start_date  # None if tau*=0 (open-left)
        map_interval_end = cells[map_index].end_date
        map_date = map_interval_end.isoformat()
        if clamp.ceiling_date is not None and map_interval_end > clamp.ceiling_date:
            raw = map_interval_end.isoformat()
            map_interval_end = clamp.ceiling_date
            map_date = clamp.ceiling_date.isoformat()
            notes.append(f"clamped_earliest_present {raw}->{clamp.ceiling_date.isoformat()}")

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
        estimator="changepoint",
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
        notes=" | ".join(notes),
    )


@register("changepoint")
def estimate_changepoint(
    observations: Sequence[VintageObservation],
    clamp: ClampContext = ClampContext(),
    config: EstimatorConfig = EstimatorConfig(),
) -> InstallDatePosterior:
    # Entry mutual-exclusion (design §6.2): the discrete EM/symbol emissions and
    # the continuous frame emissions are two distinct decode contracts; carrying
    # both is a caller bug, not a merge. Fail loudly (mirrors
    # EmissionModel.__post_init__'s "explicit error over silent double-write").
    if config.emissions is not None and config.frame_emissions is not None:
        raise ValueError(
            "EstimatorConfig carries both emissions and frame_emissions; the "
            "discrete EM path and the continuous frame path are mutually "
            "exclusive — set exactly one"
        )
    # Continuous frame branch (design §4.1). Inert unless frame_emissions is set,
    # so the discrete decode below is byte-for-byte unchanged for every existing
    # caller (none set frame_emissions).
    if config.frame_emissions is not None:
        return _decode_frame_emissions(clamp, config)

    evidence_cutoff = (
        clamp.ceiling_date
        if clamp.ceiling_date is not None
        else clamp.census_end_date
    )
    if evidence_cutoff is None:
        all_epochs = collapse_epochs(observations, config.decoder_epoch_gap_days)
    else:
        # The census mosaic is the known-PV observation for this downstream
        # backdating task. Keep historical GEHI evidence strictly before it,
        # then append census presence as its own epoch so a near-date absent
        # GEHI frame cannot collapse with and neutralize the stronger census
        # evidence. Frames after the cutoff remain available to callers for
        # review/provenance but never enter the likelihood or cell lattice.
        historical = [o for o in observations if o.capture_date < evidence_cutoff]
        all_epochs = collapse_epochs(historical, config.decoder_epoch_gap_days)
        has_historical_evidence = any(
            o.quality_flag == "usable" and o.pv_present in ("0", "1")
            for o in historical
        )
        if has_historical_evidence:
            all_epochs.append(
                Epoch(
                    start_date=evidence_cutoff,
                    end_date=evidence_cutoff,
                    n_present=1,
                    n_absent=0,
                    n_abstain=0,
                    mean_confidence=1.0,
                    stratum="usable",
                    n_present_usable=1,
                    n_absent_usable=0,
                )
            )

    # Usable-anchored cell lattice (correction 2B): drop abstain-symbol epochs
    # UNCONDITIONALLY -- both with and without a fitted emission model. Abstain
    # is censoring-with-cause (state-independent, see _epoch_loglik above), so
    # it carries no likelihood signal either way, and it must not define a
    # tau-cell boundary: exactly like PAVA drops all-raw-abstain epochs,
    # extended to epoch_symbol()'s usable-only vote (an epoch whose only
    # scored members are ambiguous/unusable-flagged is also abstain -- zero
    # usable evidence -- even though it has raw scored members). This makes
    # map_interval_start the last usable-scored absent epoch's end_date,
    # matching production's own usable-anchored bound semantics.
    epochs = [e for e in all_epochs if epoch_symbol(e) != "abstain"]
    t = len(epochs)

    if t == 0:
        beyond = (EpochCell(index=0, start_date=None, end_date=None, is_beyond_window=True),)
        # Mirror pava.py: distinguish truly-empty input from input that had
        # real epochs which were all filtered out as carrying no usable
        # evidence (e.g. every epoch is abstain or ambiguous/unusable-only).
        reason = "no_observations" if not all_epochs else "no_scored_evidence"
        return _undated_result(beyond, None, reason)

    cells = _build_cells(epochs)
    last_absent = epochs[t - 1].end_date  # lower bound if undated

    symbols = [epoch_symbol(e) for e in epochs]
    strata = [e.stratum for e in epochs]

    scores: list[float] = []
    for tau in range(t + 1):
        s = 0.0
        for i in range(t):
            s += _epoch_loglik(symbols[i], i >= tau, strata[i], config)
        s += _cell_log_prior(cells[tau], config.cohort_prior)
        scores.append(s)

    m = max(scores)
    weights = [math.exp(s - m) for s in scores]
    total = sum(weights)
    posterior = tuple(w / total for w in weights)

    # MAP: argmax, ties broken by SMALLEST tau (same convention as pava).
    map_index = 0
    best = posterior[0]
    for i in range(1, t + 1):
        if posterior[i] > best:
            best = posterior[i]
            map_index = i

    p_undated = posterior[t]

    notes: list[str] = []
    if map_index == t:
        map_interval_start = last_absent
        map_interval_end = None
        map_date = ""
    else:
        map_interval_start = cells[map_index].start_date  # None if tau*=0 (open-left)
        map_interval_end = cells[map_index].end_date
        map_date = map_interval_end.isoformat()
        # Clamp #1 (phantom-future cap) — identical to PAVA.
        if clamp.ceiling_date is not None and map_interval_end > clamp.ceiling_date:
            raw = map_interval_end.isoformat()
            map_interval_end = clamp.ceiling_date
            map_date = clamp.ceiling_date.isoformat()
            notes.append(f"clamped_earliest_present {raw}->{clamp.ceiling_date.isoformat()}")
        # clamp_inverted (decoder-only): even the latest-absent bound post-dates
        # the ceiling -> the whole dated bracket is physically impossible.
        # Report-layer reclassification only: posterior is left untouched.
        if (
            clamp.ceiling_date is not None
            and map_interval_start is not None
            and map_interval_start > clamp.ceiling_date
        ):
            notes.append(
                f"clamp_inverted {map_interval_start.isoformat()}>{clamp.ceiling_date.isoformat()}"
            )
            map_date = ""

    # marker_missed_pv (decoder-only, status-note only): the decoder believes
    # the sequence is still absent at the end of the observed window (posterior
    # mass concentrated on the beyond-window cell) yet the last USABLE absent
    # observation is already at/after the census flight date -> the scan never
    # even got close to the census marker; mirrors infer_install_dates.py's
    # done_installed_during_census -> done_ambiguous_marker_missed_pv upgrade.
    if clamp.census_end_date is not None and map_index == t:
        usable_absent_dates = [
            o.capture_date
            for o in observations
            if o.quality_flag == "usable" and o.pv_present == "0"
        ]
        if usable_absent_dates:
            last_usable_absent = max(usable_absent_dates)
            if last_usable_absent >= clamp.census_end_date:
                notes.append(
                    f"marker_missed_pv {last_usable_absent.isoformat()}"
                    f">={clamp.census_end_date.isoformat()}"
                )

    # HPD: accumulate cells by descending posterior (ties by index asc) — same
    # accumulation rule as PAVA.
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
        estimator="changepoint",
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
        notes=" | ".join(notes),
    )
