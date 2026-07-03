"""solar_backdating.estimators — install-date estimator seam + registry.

Re-exports the frozen public surface (ISSUE-01 §7) and imports the baseline and
PAVA modules for their registration side-effects, so importing this package is
sufficient to make ``get_estimator("fpd"|"sustained"|"pava")`` resolve.
"""
from __future__ import annotations

from solar_backdating.estimators.seam import (
    ClampContext,
    EpochCell,
    EstimatorConfig,
    InstallDatePosterior,
    VintageObservation,
    available_estimators,
    get_estimator,
    register,
)

# Side-effect registrations (import AFTER seam so the registry exists).
from solar_backdating.estimators import baselines as _baselines  # noqa: E402,F401
from solar_backdating.estimators import pava as _pava  # noqa: E402,F401
from solar_backdating.estimators import changepoint as _changepoint  # noqa: E402,F401

__all__ = [
    "VintageObservation",
    "ClampContext",
    "EstimatorConfig",
    "EpochCell",
    "InstallDatePosterior",
    "register",
    "get_estimator",
    "available_estimators",
]
