"""solar_backdating.localization -- semantic target-localization + roof
geometry-registration layer (PRD §5, ``docs/dinov3_scorer/PRD-run3-native-local-line-2026-07-19.md``).

Training-label decontamination + uninformative gating for the RUN 3-native
local scoring line. NOT a placement-error rescue for the blind bucket -- see
``observation.py``'s module docstring for why that distinction matters and
what NO-GO verdict it is not allowed to quietly reopen.
"""
from __future__ import annotations

from solar_backdating.localization.observation import (
    CASCADE_STAGES,
    DEFAULT_MAX_ROTATION_DEG,
    DEFAULT_MAX_TRANSLATION_M,
    DEFAULT_SCALE_BOUNDS,
    EFFECTIVE_LABELS,
    FAILURE_REASONS,
    FORCED_ABSTAIN_REASONS,
    SCHEMA_VERSION,
    TRANSFORM_TYPES,
    TargetLocalizationObservation,
    effective_label,
    transform_within_bounds,
    verdict_token,
)

__all__ = [
    "TargetLocalizationObservation",
    "effective_label",
    "verdict_token",
    "transform_within_bounds",
    "SCHEMA_VERSION",
    "TRANSFORM_TYPES",
    "FAILURE_REASONS",
    "FORCED_ABSTAIN_REASONS",
    "CASCADE_STAGES",
    "EFFECTIVE_LABELS",
    "DEFAULT_MAX_TRANSLATION_M",
    "DEFAULT_SCALE_BOUNDS",
    "DEFAULT_MAX_ROTATION_DEG",
]
