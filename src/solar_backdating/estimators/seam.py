"""Estimator seam: canonical dataclasses + estimator registry.

The seam's presence vocabulary is the raw panel string triple ``'1'`` (present)
/ ``'0'`` (absent) / ``''`` (abstain/unusable), deliberately identical to
``scripts.validation.fullstack_noscan_analyze.derive_install`` so the
``fpd``/``sustained`` baselines are bit-exact by construction. Panel IO
(PART 2) normalizes every on-disk schema into this triple before it reaches an
estimator.

Every registered estimator implements the uniform signature::

    estimate(observations: Sequence[VintageObservation],
             clamp: ClampContext = ClampContext(),
             config: EstimatorConfig = EstimatorConfig()) -> InstallDatePosterior
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # Type-only: avoids a runtime import cycle. changepoint.py and emissions.py
    # both import FROM seam.py at runtime; seam.py must not import them back
    # (ISSUE-02). Safe because ``from __future__ import annotations`` (above)
    # makes every annotation in this module a lazy string, never evaluated
    # unless something calls ``typing.get_type_hints`` on it.
    from solar_backdating.estimators.changepoint import CohortPrior
    from solar_backdating.estimators.emissions import EmissionModel, FrameEmission


@dataclass(frozen=True)
class VintageObservation:
    """One scored vintage frame for a single (unit, rep)."""

    capture_date: date  # parsed python date (first 10 chars of ISO)
    pv_present: str  # canonical '1' | '0' | ''
    confidence: float | None = None  # scorer confidence in [0,1]; None if abstain/unknown
    quality_flag: str = "usable"  # 'usable' | 'unusable' | 'ambiguous'
    source_row: int | None = None  # stable secondary sort key (CSV line); optional


@dataclass(frozen=True)
class ClampContext:
    """Census evidence cutoff plus report-layer upper-bound defenses."""

    ceiling_date: date | None = None  # per-grid flight date; preferred evidence cutoff
    census_end_date: date | None = None  # regional fallback evidence cutoff


@dataclass(frozen=True)
class EstimatorConfig:
    """Tunables. Baselines ignore all of these except by construction."""

    epoch_gap_days: int = 16  # PAVA epoch-collapsing threshold; baselines IGNORE this
    flip_rate: float = 0.1  # PAVA symmetric emission noise epsilon; changepoint fallback too
    credible_mass: float = 0.90  # HPD nominal coverage
    prior_weight: float = 0.0  # PAVA: log-prior added per tau; 0.0 = flat prior

    # --- ISSUE-02 (changepoint decoder) additions; PAVA/fpd/sustained ignore these ---
    decoder_epoch_gap_days: int = 30  # changepoint epoch-collapsing threshold (PAVA keeps 16)
    emissions: "EmissionModel | None" = None  # fitted 3-symbol confusion matrix; None = sym. noise
    cohort_prior: "CohortPrior | None" = None  # ISSUE-03 empirical-Bayes hook; None = flat prior

    # --- Frame-level (student) emission extension additions (DESIGN-phase0-
    # emission-extension, 2026-07-19); every other estimator ignores this ---
    # None = the discrete/EM path above is untouched (byte-for-byte). When set,
    # ``estimate_changepoint`` decodes the continuous frame branch instead and
    # MUST NOT also carry ``emissions`` (mutually exclusive; raises ValueError).
    frame_emissions: "Sequence[FrameEmission] | None" = None


@dataclass(frozen=True)
class EpochCell:
    """A changepoint / install-bracket cell of an ``InstallDatePosterior``."""

    index: int  # 0..T-1 observed install-bracket cells; T = beyond-window
    start_date: date | None  # lower/earliest-present date of the cell; None if open-left
    end_date: date | None  # upper/latest-absent date; None if beyond-window (open-right)
    is_beyond_window: bool = False


@dataclass(frozen=True)
class InstallDatePosterior:
    """Estimator output. ``map_date`` is the mode-hit contract (PART 2)."""

    estimator: str  # registry name that produced this
    epochs: tuple[EpochCell, ...]  # length T+1; last cell is beyond-window
    posterior: tuple[float, ...]  # length == len(epochs); sums to 1.0 (+/-1e-9)
    map_index: int  # argmax cell index (deterministic tie-break)
    map_interval_start: date | None  # latest-absent bound; None = open-left (all-present)
    map_interval_end: date | None  # earliest-present bound; None = beyond-window/undated
    p_undated: float  # posterior mass on the beyond-window cell
    credible_low_date: date | None  # HPD lower date bound
    credible_high_date: date | None  # HPD upper date bound; None if HPD includes beyond-window
    credible_mass: float  # realized coverage of the HPD set (>= config.credible_mass)
    map_date: str  # 'YYYY-MM-DD' point token, or '' when undated
    notes: str = ""  # free text (clamp raw values, degenerate flags)


EstimatorFn = Callable[
    [Sequence[VintageObservation], ClampContext, EstimatorConfig],
    InstallDatePosterior,
]

_REGISTRY: dict[str, EstimatorFn] = {}


def register(name: str) -> Callable[[EstimatorFn], EstimatorFn]:
    """Decorator registering an estimator under ``name``. Duplicate name -> ValueError."""

    def _wrap(fn: EstimatorFn) -> EstimatorFn:
        if name in _REGISTRY:
            raise ValueError(f"estimator {name!r} is already registered")
        _REGISTRY[name] = fn
        return fn

    return _wrap


def get_estimator(name: str) -> EstimatorFn:
    """Return the registered estimator. Miss -> KeyError listing available names."""
    try:
        return _REGISTRY[name]
    except KeyError:
        raise KeyError(
            f"unknown estimator {name!r}; available: {sorted(_REGISTRY)}"
        ) from None


def available_estimators() -> list[str]:
    """Sorted registered names."""
    return sorted(_REGISTRY)


# --------------------------------------------------------------------------- #
# Explicit boundary-state accessors (DESIGN-phase0-emission-extension §2.2).
# The two boundary semantics already live in the tau grid (tau=0 open-left cell,
# beyond-window cell); these functions promote the previously-implicit read of
# them (``map_interval_start is None`` / ``is_beyond_window`` reinvented inline
# in changepoint.py, infer_install_dates.py, ext2015's ``classify_row``, etc.)
# into one testable public contract. No dataclass fields change — the
# InstallDatePosterior JSON round-trip / equality contract is untouched.
# --------------------------------------------------------------------------- #
def is_already_present(result: InstallDatePosterior) -> bool:
    """True iff the MAP cell is the open-left (already-present / left-censored)
    boundary: ``map_index == 0`` and ``epochs[0].start_date is None``."""
    return result.map_index == 0 and result.epochs[0].start_date is None


def is_beyond_window(result: InstallDatePosterior) -> bool:
    """True iff the MAP cell is the beyond-window (right-censored / undated)
    boundary — the MAP-hit version of ``p_undated``'s cell."""
    return result.epochs[result.map_index].is_beyond_window
