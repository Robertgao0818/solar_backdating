"""Baseline point-mass estimators: ``fpd`` and ``sustained``.

Both IGNORE ``config.epoch_gap_days`` (no collapsing) and emit degenerate
point-mass posteriors that reproduce
``scripts.validation.fullstack_noscan_analyze.derive_install`` bit-exact:

- ``fpd.map_date == derive_install(profile)[0]``  (first-present-date)
- ``sustained.map_date == derive_install(profile)[1]``  (first-sustained-date)

``map_date == ""`` iff undated (no present / no sustained onset). The clamp
context is accepted for interface conformance; the point-mass baselines apply
no clamp (that is the decoder's job in ISSUE-02).
"""
from __future__ import annotations

from collections.abc import Sequence

from solar_backdating.estimators.seam import (
    ClampContext,
    EpochCell,
    EstimatorConfig,
    InstallDatePosterior,
    VintageObservation,
    register,
)


def _ordered(observations: Sequence[VintageObservation]) -> list[VintageObservation]:
    return sorted(observations, key=lambda o: (o.capture_date, o.source_row or 0))


def _first_present_index(ordered: list[VintageObservation]) -> int | None:
    for i, o in enumerate(ordered):
        if o.pv_present == "1":
            return i
    return None


def _first_sustained_index(ordered: list[VintageObservation]) -> int | None:
    """Exact port of derive_install's sustained rule (>=50% present suffix)."""
    for i, o in enumerate(ordered):
        if o.pv_present != "1":
            continue
        later = [x.pv_present for x in ordered[i:] if x.pv_present in ("0", "1")]
        if later and sum(1 for p in later if p == "1") / len(later) >= 0.5:
            return i
    return None


def _build_epochs(ordered: list[VintageObservation], last_absent_start) -> tuple:
    cells = [
        EpochCell(
            index=i,
            start_date=o.capture_date,
            end_date=o.capture_date,
            is_beyond_window=False,
        )
        for i, o in enumerate(ordered)
    ]
    cells.append(
        EpochCell(
            index=len(ordered),
            start_date=last_absent_start,
            end_date=None,
            is_beyond_window=True,
        )
    )
    return tuple(cells)


def _point_posterior(
    ordered: list[VintageObservation], idx: int | None, estimator: str
) -> InstallDatePosterior:
    """Assemble a degenerate point-mass posterior at profile index ``idx``.

    ``idx is None`` -> undated (mass on the trailing beyond-window cell).
    """
    n = len(ordered)
    # last absent date (most recent '0') as the undated lower bound.
    last_absent = None
    for o in ordered:
        if o.pv_present == "0":
            last_absent = o.capture_date
    epochs = _build_epochs(ordered, last_absent)

    if idx is None:
        posterior = tuple(1.0 if i == n else 0.0 for i in range(n + 1))
        return InstallDatePosterior(
            estimator=estimator,
            epochs=epochs,
            posterior=posterior,
            map_index=n,
            map_interval_start=last_absent,
            map_interval_end=None,
            p_undated=1.0,
            credible_low_date=None,
            credible_high_date=None,
            credible_mass=1.0,
            map_date="",
            notes="",
        )

    posterior = tuple(1.0 if i == idx else 0.0 for i in range(n + 1))
    d = ordered[idx].capture_date
    prior_absent = (
        ordered[idx - 1].capture_date
        if idx > 0 and ordered[idx - 1].pv_present == "0"
        else None
    )
    return InstallDatePosterior(
        estimator=estimator,
        epochs=epochs,
        posterior=posterior,
        map_index=idx,
        map_interval_start=prior_absent,
        map_interval_end=d,
        p_undated=0.0,
        credible_low_date=d,
        credible_high_date=d,
        credible_mass=1.0,
        map_date=d.isoformat(),
        notes="",
    )


@register("fpd")
def estimate_fpd(
    observations: Sequence[VintageObservation],
    clamp: ClampContext = ClampContext(),
    config: EstimatorConfig = EstimatorConfig(),
) -> InstallDatePosterior:
    """First-present-date baseline (Arm A parity)."""
    ordered = _ordered(observations)
    return _point_posterior(ordered, _first_present_index(ordered), "fpd")


@register("sustained")
def estimate_sustained(
    observations: Sequence[VintageObservation],
    clamp: ClampContext = ClampContext(),
    config: EstimatorConfig = EstimatorConfig(),
) -> InstallDatePosterior:
    """First-sustained-date baseline (rejects isolated single-frame blips)."""
    ordered = _ordered(observations)
    return _point_posterior(ordered, _first_sustained_index(ordered), "sustained")
