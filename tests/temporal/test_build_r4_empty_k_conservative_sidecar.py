from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.temporal import build_r4_empty_k_conservative_sidecar as builder  # noqa: E402


def _anchor(anchor_id: str, split: str, dates: list[str]) -> list[dict]:
    labels = ["absent", "present", "present"]
    return [
        {
            "anchor_id": anchor_id,
            "capture_date": capture_date,
            "chip_index": i,
            "label_v1": labels[i],
            "scan_status": "done_appears",
            "split": split,
        }
        for i, capture_date in enumerate(dates)
    ]


def test_unconfirmed_amendment_fails_closed():
    with pytest.raises(PermissionError, match="owner confirmation"):
        builder.validate_amendment({
            "amendment_id": "r4_empty_k_conservative_abstain_v1",
            "policy": {
                "interval_loss_eligible": False,
                "earlier_boundary_override": "uninformative",
                "later_boundary_override": "uninformative",
                "non_boundary_frames_unchanged": True,
                "r0_manifest_unchanged": True,
                "v3_v4_v5_labels_consumed": False,
                "test_split_consumed": False,
            },
            "owner_confirmation": {"confirmed": False},
        })


def test_derives_only_allowed_empty_k_boundary_rows():
    rows = []
    rows += _anchor("short-train", "train", ["2021-09-30", "2021-10-30", "2022-06-01"])
    rows += _anchor("long-cal", "calibration", ["2019-01-01", "2020-01-01", "2021-01-01"])
    rows += _anchor("short-test", "test", ["2021-09-30", "2021-10-30", "2022-06-01"])
    sidecar, summary = builder.derive_sidecar(pd.DataFrame(rows), enforce_counts=False)
    assert summary["anchors"] == 1
    assert summary["boundary_rows"] == 2
    assert set(sidecar["anchor_id"]) == {"short-train"}
    assert sidecar["override_label"].eq("uninformative").all()
    assert sidecar["interval_loss_eligible"].eq(False).all()
    assert set(sidecar["endpoint"]) == {"earlier", "later"}
    assert set(sidecar["original_label"]) == {"absent", "present"}
