"""Regression pins for the ISSUE-19 versioned chip-geometry registry.

See docs/replan_v2/ISSUE-19-chip-geometry-policy.md and the decision memo
docs/replan_v2/ISSUE-19-geometry-decision-2026-07-04.md. Frozen v1 params MUST
equal today's banked ``ensure_single_target_review_png`` defaults and v2 MUST
equal the ISSUE-04 tight-crop arm (0.5 / 12 / 256) — exact equality so a silent
geometry drift on either side fails loudly here.
"""

from __future__ import annotations

import dataclasses
import inspect

import pytest

from scripts.temporal.chip_geometry import (
    LEGACY_GEOMETRY,
    LEGACY_GEOMETRY_VERSION,
    RERENDER_GEOMETRY,
    RERENDER_GEOMETRY_VERSION,
    ChipGeometry,
    available_geometry_versions,
    contain_crop_to_footprint,
    resolve_chip_geometry,
)


def test_v1_banked_params_match_gehi_common_defaults() -> None:
    """v1 must reproduce the banked render byte-for-byte, so its param triple is
    pinned to the live ``ensure_single_target_review_png`` defaults."""
    from scripts.temporal.gehi_common import ensure_single_target_review_png

    sig = inspect.signature(ensure_single_target_review_png)
    geom = resolve_chip_geometry("chip_geom_v1_banked96")
    assert geom.crop_context_multiplier == sig.parameters["crop_context_multiplier"].default == 3.0
    assert geom.min_crop_size_m == sig.parameters["min_crop_size_m"].default == 24.0
    assert geom.min_output_px == sig.parameters["min_output_px"].default == 128


def test_v2_tight_params_match_issue04_arm() -> None:
    geom = resolve_chip_geometry("chip_geom_v2_tight12")
    assert geom.crop_context_multiplier == 0.5
    assert geom.min_crop_size_m == 12.0
    assert geom.min_output_px == 256


def test_module_defaults_point_at_right_versions() -> None:
    assert LEGACY_GEOMETRY_VERSION == "chip_geom_v1_banked96"
    assert RERENDER_GEOMETRY_VERSION == "chip_geom_v2_tight12"
    assert LEGACY_GEOMETRY is resolve_chip_geometry(LEGACY_GEOMETRY_VERSION)
    assert RERENDER_GEOMETRY is resolve_chip_geometry(RERENDER_GEOMETRY_VERSION)
    assert LEGACY_GEOMETRY.geometry_version == LEGACY_GEOMETRY_VERSION
    assert RERENDER_GEOMETRY.geometry_version == RERENDER_GEOMETRY_VERSION
    assert available_geometry_versions() == (
        "chip_geom_v1_banked96",
        "chip_geom_v2_tight12",
    )


def test_resolve_returns_the_registered_instance() -> None:
    geom = resolve_chip_geometry("chip_geom_v2_tight12")
    assert isinstance(geom, ChipGeometry)
    assert geom is RERENDER_GEOMETRY


def test_resolve_unknown_version_fails_loudly() -> None:
    with pytest.raises(KeyError, match="unknown chip geometry_version"):
        resolve_chip_geometry("chip_geom_v99_missing")


def test_chip_geometry_is_immutable() -> None:
    geom = resolve_chip_geometry("chip_geom_v1_banked96")
    with pytest.raises(dataclasses.FrozenInstanceError):
        geom.min_output_px = 999  # type: ignore[misc]


def test_download_extent_is_informational_and_stable_across_versions() -> None:
    # The GEHI download / builder extent is 96 m and unchanged across geometry
    # versions; only the scoring-crop render differs (decision memo).
    assert LEGACY_GEOMETRY.download_chip_size_m == RERENDER_GEOMETRY.download_chip_size_m == 96.0


def test_geometry_version_is_not_a_verdict_key_component() -> None:
    """Hard constraint (D18): geometry re-keys ONLY through ``chip_sha256`` (the
    rendered PNG's content hash), never as a direct key field. ``build_verdict_key``
    must therefore expose no geometry parameter."""
    from scripts.temporal.verdict_store import build_verdict_key

    params = set(inspect.signature(build_verdict_key).parameters)
    assert not any("geom" in name for name in params)


def test_contain_crop_to_footprint_floors_at_footprint_and_caps_at_chip() -> None:
    # crop narrower than the footprint's larger side -> enlarged to contain it
    assert contain_crop_to_footprint(
        12.0, source_width_m=30.0, source_height_m=10.0, chip_size_m=96.0
    ) == 30.0
    # crop already contains the footprint -> left unchanged
    assert contain_crop_to_footprint(
        40.0, source_width_m=30.0, source_height_m=10.0, chip_size_m=96.0
    ) == 40.0
    # footprint exceeds the downloaded chip -> capped at the chip extent
    assert contain_crop_to_footprint(
        12.0, source_width_m=200.0, source_height_m=10.0, chip_size_m=96.0
    ) == 96.0
