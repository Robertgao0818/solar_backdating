import copy
import json
from pathlib import Path

import pytest

from scripts.temporal.validate_ct11_acceptance_prereg import validate_prereg


CONFIG = json.loads(
    (Path(__file__).parents[2] / "configs" / "ct11_corrected_acceptance_v1.json").read_text()
)
SUMMARY = {
    "status": "frozen_before_corrected_review",
    "exact_claimed_boundary_failures": 0,
    "post_cutoff_frames": 0,
    "assignments_sha256": CONFIG["package"]["assignment_sha256"],
    "native_package_sha256": CONFIG["package"]["pass1_package_sha256"],
    "repeat_package_sha256": CONFIG["package"]["repeat_package_sha256"],
}


def test_unconfirmed_prereg_lints_but_fails_closed_for_review():
    validate_prereg(CONFIG, SUMMARY, require_confirmed=False)
    with pytest.raises(ValueError, match="owner has not confirmed"):
        validate_prereg(CONFIG, SUMMARY, require_confirmed=True)


def test_confirmed_prereg_passes():
    config = copy.deepcopy(CONFIG)
    config["owner_confirmation"] = {
        "confirmed": True,
        "confirmed_at_utc": "2026-08-03T00:00:00Z",
        "confirmation_evidence": "owner message",
    }
    validate_prereg(config, SUMMARY)


def test_package_hash_drift_fails():
    summary = {**SUMMARY, "native_package_sha256": "0" * 64}
    with pytest.raises(ValueError, match="package identity mismatch"):
        validate_prereg(CONFIG, summary, require_confirmed=False)
