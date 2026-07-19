"""``TargetLocalizationObservation`` schema tests (PRD §5.3/§3.3,
``docs/dinov3_scorer/PRD-run3-native-local-line-2026-07-19.md``).

No real data dependency -- pure dataclass/serialization/gate-function tests.
Covers: constructor invariants (illegal field combinations raise
``ValueError``), ``to_json_record``/``from_json_record`` round-trip, and the
``effective_label`` truth table -- with particular weight on the two red
lines the team-lead brief called out: (1) a not-found building can never
produce ``absent``, and (2) an out-of-bounds/conflicting transform must
abstain, never localize.
"""
from __future__ import annotations

from datetime import date

import pytest

from solar_backdating.localization.observation import (
    DEFAULT_MAX_TRANSLATION_M,
    TargetLocalizationObservation,
    effective_label,
    transform_within_bounds,
    verdict_token,
)

ANCHOR = "jhb_test_anchor_0001"
DATE = date(2020, 6, 15)


def _make(**overrides) -> TargetLocalizationObservation:
    """Baseline valid identity-stage observation; override fields per-test."""
    kwargs = dict(
        anchor_id=ANCHOR,
        capture_date=DATE,
        building_found=True,
        roof_plane_matched=True,
        target_localized=True,
        transform_type="identity",
        transform_params={},
        registration_confidence=1.0,
        shift_uncertainty_m=0.0,
        projected_target_polygon=None,
        failure_reason="",
        cascade_stage="identity",
        abstain=False,
    )
    kwargs.update(overrides)
    return TargetLocalizationObservation(**kwargs)


# --------------------------------------------------------------------------- #
# Constructor invariants                                                      #
# --------------------------------------------------------------------------- #


def test_baseline_identity_observation_is_valid() -> None:
    tlo = _make()
    assert tlo.target_localized
    assert not tlo.abstain
    assert tlo.transform_params == {}


def test_target_localized_requires_building_found() -> None:
    with pytest.raises(ValueError, match="building_found"):
        _make(building_found=False, roof_plane_matched=False, target_localized=True)


def test_target_localized_requires_roof_plane_matched() -> None:
    with pytest.raises(ValueError, match="roof_plane_matched"):
        _make(roof_plane_matched=False, target_localized=True)


def test_roof_plane_matched_requires_building_found() -> None:
    with pytest.raises(ValueError, match="building_found"):
        _make(building_found=False, roof_plane_matched=True, target_localized=False)


def test_building_not_found_is_a_legal_uninformative_observation() -> None:
    """The core red line, expressed as a *legal* construction: building not
    found is NOT itself an error -- it just cannot claim target_localized."""
    tlo = _make(
        building_found=False,
        roof_plane_matched=False,
        target_localized=False,
        cascade_stage="phase_correlation",
        transform_type="identity",
        failure_reason="building_not_found",
    )
    assert not tlo.target_localized
    assert tlo.failure_reason == "building_not_found"


def test_unknown_transform_type_rejected() -> None:
    with pytest.raises(ValueError, match="transform_type"):
        _make(transform_type="affine")


def test_unknown_cascade_stage_rejected() -> None:
    with pytest.raises(ValueError, match="cascade_stage"):
        _make(cascade_stage="ransac_final")


def test_unknown_failure_reason_rejected() -> None:
    with pytest.raises(ValueError, match="failure_reason"):
        _make(failure_reason="camera_exploded")


def test_identity_stage_requires_identity_transform() -> None:
    with pytest.raises(ValueError, match="cascade_stage='identity'"):
        _make(
            cascade_stage="identity",
            transform_type="translation",
            transform_params={"dx_m": 1.0, "dy_m": 1.0},
        )


@pytest.mark.parametrize(
    "reason", ["transform_conflict", "transform_out_of_bounds", "roi_expansion_clipped"]
)
def test_forced_abstain_reason_requires_abstain_true(reason: str) -> None:
    with pytest.raises(ValueError, match="abstain=True"):
        _make(
            cascade_stage="phase_correlation",
            building_found=True,
            roof_plane_matched=True,
            target_localized=False,
            transform_type="translation",
            transform_params={"dx_m": 0.5, "dy_m": 0.5},
            failure_reason=reason,
            abstain=False,
        )


@pytest.mark.parametrize(
    "reason", ["transform_conflict", "transform_out_of_bounds", "roi_expansion_clipped"]
)
def test_forced_abstain_reason_requires_target_not_localized(reason: str) -> None:
    with pytest.raises(ValueError, match="target_localized=False"):
        _make(
            cascade_stage="phase_correlation",
            target_localized=True,
            transform_type="identity",
            failure_reason=reason,
            abstain=True,
        )


def test_roi_expansion_clipped_requires_abstain() -> None:
    with pytest.raises(ValueError, match="roi_expansion_clipped=True requires abstain=True"):
        _make(
            cascade_stage="phase_correlation",
            transform_type="identity",
            roi_expansion_m=3.0,
            roi_expansion_clipped=True,
            abstain=False,
        )


def test_roi_expansion_clipped_with_abstain_is_valid() -> None:
    tlo = _make(
        cascade_stage="phase_correlation",
        building_found=True,
        roof_plane_matched=True,
        target_localized=False,
        transform_type="identity",
        roi_expansion_m=3.0,
        roi_expansion_clipped=True,
        failure_reason="roi_expansion_clipped",
        abstain=True,
    )
    assert tlo.abstain and tlo.roi_expansion_clipped


def test_abstain_with_target_localized_requires_failure_reason() -> None:
    with pytest.raises(ValueError, match="failure_reason"):
        _make(target_localized=True, abstain=True, failure_reason="")


def test_abstain_with_target_localized_and_failure_reason_is_valid() -> None:
    # Edge case explicitly allowed by the team-lead spec: abstain=True can
    # co-occur with target_localized=True IF a failure_reason is given.
    tlo = _make(target_localized=True, abstain=True, failure_reason="low_confidence")
    assert tlo.abstain and tlo.target_localized


@pytest.mark.parametrize(
    "transform_type,params",
    [
        ("translation", {"dx_m": DEFAULT_MAX_TRANSLATION_M + 1.0, "dy_m": 0.0}),
        ("similarity", {"dx_m": 0.0, "dy_m": 0.0, "scale": 1.5}),
        ("similarity", {"dx_m": 0.0, "dy_m": 0.0, "rotation_deg": 45.0}),
    ],
)
def test_out_of_bounds_transform_must_abstain(transform_type: str, params: dict) -> None:
    assert not transform_within_bounds(transform_type, params)
    with pytest.raises(ValueError, match="exceed the configured"):
        _make(
            cascade_stage="weak_lock",
            transform_type=transform_type,
            transform_params=params,
            target_localized=True,  # illegally trying to localize on an OOB transform
            abstain=False,
        )


def test_out_of_bounds_transform_with_abstain_and_reason_is_valid() -> None:
    tlo = _make(
        cascade_stage="weak_lock",
        building_found=True,
        roof_plane_matched=True,
        target_localized=False,
        transform_type="translation",
        transform_params={"dx_m": DEFAULT_MAX_TRANSLATION_M + 2.0, "dy_m": 0.0},
        failure_reason="transform_out_of_bounds",
        abstain=True,
    )
    assert tlo.abstain and not tlo.target_localized


def test_out_of_bounds_transform_abstain_needs_matching_failure_reason() -> None:
    with pytest.raises(ValueError, match="failure_reason to one of"):
        _make(
            cascade_stage="weak_lock",
            building_found=True,
            roof_plane_matched=True,
            target_localized=False,
            transform_type="translation",
            transform_params={"dx_m": DEFAULT_MAX_TRANSLATION_M + 2.0, "dy_m": 0.0},
            failure_reason="low_confidence",  # not a FORCED_ABSTAIN reason
            abstain=True,
        )


def test_within_bounds_translation_can_localize() -> None:
    tlo = _make(
        cascade_stage="phase_correlation",
        transform_type="translation",
        transform_params={"dx_m": 1.0, "dy_m": 1.0},
        target_localized=True,
        abstain=False,
    )
    assert tlo.target_localized


def test_registration_confidence_out_of_range_rejected() -> None:
    with pytest.raises(ValueError, match="registration_confidence"):
        _make(registration_confidence=1.5)


def test_negative_shift_uncertainty_rejected() -> None:
    with pytest.raises(ValueError, match="shift_uncertainty_m"):
        _make(shift_uncertainty_m=-0.1)


def test_negative_roi_expansion_rejected() -> None:
    with pytest.raises(ValueError, match="roi_expansion_m"):
        _make(roi_expansion_m=-1.0)


def test_transform_params_is_immutable_mapping() -> None:
    tlo = _make(
        cascade_stage="phase_correlation",
        transform_type="translation",
        transform_params={"dx_m": 1.0, "dy_m": 1.0},
        target_localized=True,
    )
    with pytest.raises(TypeError):
        tlo.transform_params["dx_m"] = 99.0  # type: ignore[index]


# --------------------------------------------------------------------------- #
# Serialization round-trip                                                    #
# --------------------------------------------------------------------------- #


def test_json_round_trip_identity() -> None:
    tlo = _make(projected_target_polygon=((28.09, -26.11), (28.10, -26.11), (28.10, -26.12)))
    record = tlo.to_json_record()
    restored = TargetLocalizationObservation.from_json_record(record)
    assert restored == tlo
    assert record["schema_version"] == "1.0.0"
    assert record["capture_date"] == "2020-06-15"


def test_json_round_trip_wkt_polygon() -> None:
    tlo = _make(projected_target_polygon="POLYGON((28.09 -26.11, 28.10 -26.11, 28.10 -26.12))")
    record = tlo.to_json_record()
    assert isinstance(record["projected_target_polygon"], str)
    restored = TargetLocalizationObservation.from_json_record(record)
    assert restored == tlo


def test_json_round_trip_abstain_observation() -> None:
    tlo = _make(
        cascade_stage="weak_lock",
        building_found=True,
        roof_plane_matched=True,
        target_localized=False,
        transform_type="translation",
        transform_params={"dx_m": DEFAULT_MAX_TRANSLATION_M + 2.0, "dy_m": 0.0},
        failure_reason="transform_out_of_bounds",
        abstain=True,
    )
    restored = TargetLocalizationObservation.from_json_record(tlo.to_json_record())
    assert restored == tlo


# --------------------------------------------------------------------------- #
# effective_label gate truth table                                            #
# --------------------------------------------------------------------------- #


def test_effective_label_pending_when_tlo_is_none_preserves_legacy_absent() -> None:
    result = effective_label("absent", "usable", None)
    assert result == {"label": "absent", "localization_pending": True, "gated_reason": ""}


def test_effective_label_pending_when_tlo_is_none_preserves_legacy_present() -> None:
    result = effective_label("present", "usable", None)
    assert result["label"] == "present"
    assert result["localization_pending"] is True


def test_effective_label_absent_passes_when_localized() -> None:
    tlo = _make()  # target_localized=True, abstain=False
    result = effective_label("absent", "usable", tlo)
    assert result == {"label": "absent", "localization_pending": False, "gated_reason": ""}


def test_effective_label_absent_downgraded_when_building_not_found() -> None:
    tlo = _make(
        building_found=False,
        roof_plane_matched=False,
        target_localized=False,
        cascade_stage="phase_correlation",
        failure_reason="building_not_found",
    )
    result = effective_label("absent", "usable", tlo)
    assert result["label"] == "uninformative"
    assert result["localization_pending"] is False
    assert result["gated_reason"] == "building_not_found"


def test_effective_label_absent_downgraded_when_roof_plane_not_matched() -> None:
    tlo = _make(
        roof_plane_matched=False,
        target_localized=False,
        cascade_stage="phase_correlation",
        failure_reason="roof_plane_not_matched",
    )
    result = effective_label("absent", "usable", tlo)
    assert result["label"] == "uninformative"
    assert result["gated_reason"] == "roof_plane_not_matched"


def test_effective_label_absent_downgraded_when_abstain() -> None:
    tlo = _make(
        cascade_stage="weak_lock",
        target_localized=False,
        transform_type="translation",
        transform_params={"dx_m": DEFAULT_MAX_TRANSLATION_M + 2.0, "dy_m": 0.0},
        failure_reason="transform_out_of_bounds",
        abstain=True,
    )
    result = effective_label("absent", "usable", tlo)
    assert result["label"] == "uninformative"
    assert result["gated_reason"] == "transform_out_of_bounds"


def test_effective_label_present_passes_through_even_when_not_localized() -> None:
    """§3.3's gate is specifically about absent -- present is not blocked by
    a failed localization (it is not the contamination channel this layer
    targets)."""
    tlo = _make(
        building_found=False,
        roof_plane_matched=False,
        target_localized=False,
        cascade_stage="phase_correlation",
        failure_reason="building_not_found",
    )
    result = effective_label("present", "usable", tlo)
    assert result == {"label": "present", "localization_pending": False, "gated_reason": ""}


def test_effective_label_quality_flag_unusable_wins_regardless_of_localization() -> None:
    tlo = _make()  # fully localized
    result = effective_label("absent", "unusable", tlo)
    assert result["label"] == "uninformative"
    assert result["localization_pending"] is False
    assert result["gated_reason"] == ""  # quality-side, not a localization gate downgrade


@pytest.mark.parametrize("verdict", ["uninformative", "corrupt", "ambiguous", "garbled_token"])
def test_effective_label_non_present_absent_verdicts_map_to_uninformative(verdict: str) -> None:
    tlo = _make()
    result = effective_label(verdict, "usable", tlo)
    assert result["label"] == "uninformative"


# --------------------------------------------------------------------------- #
# verdict_token -- canonical RUN 3 pv_present bool -> token mapping           #
# --------------------------------------------------------------------------- #


def test_verdict_token_true_is_present() -> None:
    assert verdict_token(True) == "present"


def test_verdict_token_false_is_absent() -> None:
    assert verdict_token(False) == "absent"


def test_verdict_token_none_is_uninformative() -> None:
    assert verdict_token(None) == "uninformative"


def test_verdict_token_ignores_quality_flag() -> None:
    # quality_flag is accepted for call-site convenience but not interpreted
    # here -- that's effective_label's/_legacy_three_state's job.
    assert verdict_token(True, quality_flag="unusable") == "present"
    assert verdict_token(None, quality_flag="usable") == "uninformative"


@pytest.mark.parametrize(
    "pv_present,expected_base",
    [(True, "present"), (False, "absent"), (None, "uninformative")],
)
def test_verdict_token_feeds_effective_label_consistently(
    pv_present: bool | None, expected_base: str
) -> None:
    """verdict_token's output must be exactly the token effective_label expects
    -- round-tripping it through effective_label (localized, usable-quality
    TLO, so no gate/quality downgrade fires) should reproduce the same base
    label with no gating."""
    token = verdict_token(pv_present)
    assert token == expected_base
    tlo = _make()  # target_localized=True, abstain=False
    result = effective_label(token, "usable", tlo)
    assert result["label"] == expected_base
    assert result["gated_reason"] == ""
