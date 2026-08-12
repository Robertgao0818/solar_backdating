#!/usr/bin/env python3
"""Fail closed unless the corrected CT-11 package and owner prereg match."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


EXPECTED_THRESHOLDS = {
    "exact_bracket_wilson_95_lower_min": (0.0, 1.0),
    "inventory_weighted_exact_bracket_min": (0.0, 1.0),
    "undatable_rate_max": (0.0, 1.0),
    "repeat_state_agreement_min": (0.0, 1.0),
    "repeat_state_wilson_95_lower_min": (0.0, 1.0),
    "exact_boundary_failures_max": (0, 0),
    "post_cutoff_frames_max": (0, 0),
}


def validate_prereg(config: dict, package_summary: dict, *, require_confirmed: bool = True) -> None:
    if config.get("schema_version") != 1:
        raise ValueError("unsupported acceptance prereg schema")
    thresholds = config.get("thresholds") or {}
    if set(thresholds) != set(EXPECTED_THRESHOLDS):
        raise ValueError("acceptance threshold keys are incomplete or unexpected")
    for key, (low, high) in EXPECTED_THRESHOLDS.items():
        value = thresholds[key]
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not low <= value <= high:
            raise ValueError(f"invalid threshold {key}={value!r}")
    expected_package = config.get("package") or {}
    actual = {
        "assignment_sha256": package_summary.get("assignments_sha256"),
        "pass1_package_sha256": package_summary.get("native_package_sha256"),
        "repeat_package_sha256": package_summary.get("repeat_package_sha256"),
    }
    if expected_package != actual:
        raise ValueError(f"preregistered package identity mismatch: expected={expected_package} actual={actual}")
    if package_summary.get("status") != "frozen_before_corrected_review":
        raise ValueError("corrected package was not frozen before review")
    if package_summary.get("exact_claimed_boundary_failures") != 0:
        raise ValueError("corrected package has claimed-boundary failures")
    if package_summary.get("post_cutoff_frames") != 0:
        raise ValueError("corrected package contains post-cutoff frames")
    confirmation = config.get("owner_confirmation") or {}
    if require_confirmed and not (
        confirmation.get("confirmed") is True
        and confirmation.get("confirmed_at_utc")
        and confirmation.get("confirmation_evidence")
    ):
        raise ValueError("owner has not confirmed the corrected CT-11 acceptance prereg")
    policy = config.get("policy") or {}
    if policy.get("thresholds_may_not_be_weakened_after_corrected_verdicts_are_viewed") is not True:
        raise ValueError("no-retroactive-weakening policy is not locked")
    if policy.get("failed_remediation_requires_new_holdout") is not True:
        raise ValueError("new-holdout-on-remediation policy is not locked")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--package-summary", type=Path, required=True)
    parser.add_argument("--allow-unconfirmed", action="store_true")
    args = parser.parse_args()
    config = json.loads(args.config.read_text())
    summary = json.loads(args.package_summary.read_text())
    validate_prereg(config, summary, require_confirmed=not args.allow_unconfirmed)
    print(json.dumps({"status": "PASS", "owner_confirmed": config["owner_confirmation"]["confirmed"]}))


if __name__ == "__main__":
    main()
