from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.validation import build_delivery_scope_v1 as scope


def test_scope_is_unique_and_exhaustive():
    manifest, sidecars, summary = scope.build_scope()
    assert len(manifest) == 2146
    assert manifest["anchor_id"].is_unique
    assert manifest["delivery_category"].notna().all()
    assert sum(summary["delivery_category_counts"].values()) == 2146
    assert len(sidecars["clean_install_intervals"]) == 1397
    assert len(sidecars["coj_gate_failure_calibration_200"]) == 200


def test_clean_scope_is_disjoint_from_censored_and_manual():
    manifest, sidecars, _ = scope.build_scope()
    clean = set(sidecars["clean_install_intervals"]["anchor_id"])
    for name in ("left_censored", "right_censored", "manual_review_queue"):
        assert clean.isdisjoint(set(sidecars[name]["anchor_id"]))
    assert set(manifest.loc[
        manifest["delivery_category"].str.startswith("primary_"), "anchor_id"
    ]) == clean


def test_coj_calibration_is_additive_only():
    manifest, sidecars, _ = scope.build_scope()
    calibration = sidecars["coj_gate_failure_calibration_200"]
    assert len(calibration) == 200
    assert set(calibration["delivery_category"]) == {
        "coj_high_thinking_calibration_only"
    }
    # Only the 104 high-panel anchors that belong to the inherited V4/V5
    # population can be annotated on this 2,146-row manifest; the remaining
    # non-overlap calibration anchors live only in the separate sidecar.
    assert manifest["coj_high_panel"].sum() == 104
