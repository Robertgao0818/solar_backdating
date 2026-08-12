#!/usr/bin/env python3
"""Build the additive delivery scope for the V4/V5 install-date products."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.temporal import run_r4_shortgap_adjudication as common

DATA_ROOT = Path("/home/gao/zasolar_data/geid_temporal")
V4_ROOT = DATA_ROOT / "r4_shortgap_window_review_v4"
V5_ROOT = DATA_ROOT / "r4_shortgap_window_recovery_v5"
COJ_ROOT = DATA_ROOT / "coj_gate_failure_agent_adjudication_v2"
RUN_ID = "delivery_scope_v1"
OUT_ROOT = DATA_ROOT / RUN_ID
SCOPE_VERSION = "delivery_scope_v1@2026-07-24"


def _strict_v4(v4: pd.DataFrame) -> pd.DataFrame:
    return v4.loc[
        (v4["verdict"] == "confirmed_shortgap")
        & v4["strict_under_45_days"].fillna(False)
    ].copy()


def build_scope() -> tuple[pd.DataFrame, dict[str, pd.DataFrame], dict]:
    v4 = pd.read_parquet(V4_ROOT / "window_review.parquet")
    v5 = pd.read_parquet(V5_ROOT / "recovery_verdicts.parquet")
    v5_intervals = pd.read_parquet(V5_ROOT / "recovered_install_intervals.parquet")
    v5_frames = pd.read_parquet(V5_ROOT / "recovered_frame_labels.parquet")
    left = pd.read_parquet(V5_ROOT / "left_censored.parquet")
    right = pd.read_parquet(V5_ROOT / "right_censored.parquet")
    manual = pd.read_parquet(V5_ROOT / "manual_review_queue.parquet")
    high = pd.read_parquet(COJ_ROOT / "agent_adjudication.parquet")

    # Start from the full inherited V4 population and assign one category.
    scope = v4[
        ["anchor_id", "gap_days", "strict_under_45_days", "verdict",
         "earlier_capture_date", "later_capture_date"]
    ].copy()
    scope["delivery_category"] = "v4_unclassified"
    scope.loc[
        (scope["verdict"] == "confirmed_shortgap")
        & scope["strict_under_45_days"].fillna(False),
        "delivery_category",
    ] = "primary_v4_confirmed_strict"
    scope.loc[
        (scope["verdict"] == "confirmed_shortgap")
        & ~scope["strict_under_45_days"].fillna(False),
        "delivery_category",
    ] = "v4_confirmed_non_strict"
    scope.loc[
        scope["verdict"] == "unreviewable", "delivery_category"
    ] = "v4_unreviewable"

    v5_map = v5.set_index("anchor_id")
    for column in (
        "sequence_status", "first_pv_frame", "confidence", "rep_count",
        "agreeing_reps", "model_alias", "exact_model_version",
    ):
        scope[f"v5_{column}"] = scope["anchor_id"].map(v5_map[column])
    interval_ids = set(v5_intervals["anchor_id"])
    scope.loc[
        scope["anchor_id"].isin(interval_ids)
        & v5_intervals.set_index("anchor_id")["boundary_moved"]
        .reindex(scope["anchor_id"]).astype("boolean").fillna(False).to_numpy(),
        "delivery_category",
    ] = "primary_v5_recovered_interval_moved"
    scope.loc[
        scope["anchor_id"].isin(interval_ids)
        & ~v5_intervals.set_index("anchor_id")["boundary_moved"]
        .reindex(scope["anchor_id"]).astype("boolean").fillna(False).to_numpy(),
        "delivery_category",
    ] = "primary_v5_recovered_interval_unchanged"
    for frame, category in (
        (left, "sidecar_v5_left_censored"),
        (right, "sidecar_v5_right_censored"),
        (manual, "sidecar_v5_manual_review"),
    ):
        scope.loc[scope["anchor_id"].isin(frame["anchor_id"]),
                  "delivery_category"] = category
    scope.loc[
        (scope["v5_sequence_status"] == "unreviewable")
        & ~scope["anchor_id"].isin(interval_ids)
        & ~scope["anchor_id"].isin(set(left.anchor_id))
        & ~scope["anchor_id"].isin(set(right.anchor_id))
        & ~scope["anchor_id"].isin(set(manual.anchor_id)),
        "delivery_category",
    ] = "sidecar_v5_unreviewable"

    high_map = high.set_index("anchor_id")
    scope["coj_high_panel"] = scope["anchor_id"].isin(high_map.index)
    for column in ("sequence_status", "first_pv_frame", "rep_count",
                   "exact_model_version"):
        source = column if column in high_map else f"agent_{column}"
        if source in high_map:
            scope[f"coj_high_{column}"] = scope["anchor_id"].map(high_map[source])

    if scope["delivery_category"].eq("v4_unclassified").any():
        raise AssertionError("scope contains an unclassified inherited row")
    if scope["anchor_id"].duplicated().any():
        raise AssertionError("scope anchor IDs are not unique")

    interval = v4.loc[
        (v4["verdict"] == "confirmed_shortgap")
        & v4["strict_under_45_days"].fillna(False),
        ["anchor_id", "earlier_capture_date", "later_capture_date",
         "gap_days", "confidence", "agreeing_reps", "rep_count",
         "decision_source"],
    ].copy()
    interval["model_alias"] = "gemini-3.5-flash-low"
    interval["exact_model_version"] = "gemini-default"
    interval["delivery_category"] = "primary_v4_confirmed_strict"
    v5_interval = v5_intervals.copy()
    v5_interval["delivery_category"] = (
        "primary_v5_recovered_interval_moved"
    )
    v5_interval.loc[~v5_interval["boundary_moved"],
                    "delivery_category"] = (
        "primary_v5_recovered_interval_unchanged"
    )
    clean = pd.concat([interval, v5_interval], ignore_index=True, sort=False)

    high_calibration = high.copy()
    high_calibration["delivery_category"] = "coj_high_thinking_calibration_only"
    sidecars = {
        "frame_supervision_sidecar": v5_frames,
        "left_censored": left,
        "right_censored": right,
        "manual_review_queue": manual,
        "coj_gate_failure_calibration_200": high_calibration,
        "clean_install_intervals": clean,
    }
    summary = {
        "run_id": RUN_ID,
        "scope_version": SCOPE_VERSION,
        "source_runs": {
            "v4": common.sha256_file(V4_ROOT / "window_review.parquet"),
            "v5": common.sha256_file(V5_ROOT / "recovery_verdicts.parquet"),
            "coj_high": common.sha256_file(COJ_ROOT / "agent_adjudication.parquet"),
        },
        "inherited_rows": len(scope),
        "delivery_category_counts": scope["delivery_category"]
        .value_counts().sort_index().to_dict(),
        "clean_interval_rows": len(clean),
        "v5_frame_label_rows": len(v5_frames),
        "coj_calibration_rows": len(high_calibration),
        "policy": {
            "coj_gate_failures_are_calibration_only": True,
            "r0_manifest_modified": False,
            "strict_v4_threshold": "<45 days",
        },
    }
    return scope, sidecars, summary


def main() -> int:
    scope, sidecars, summary = build_scope()
    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    common.atomic_parquet(OUT_ROOT / "delivery_scope.parquet", scope)
    for name, frame in sidecars.items():
        common.atomic_parquet(OUT_ROOT / f"{name}.parquet", frame)
    lock = {
        "run_id": RUN_ID,
        "scope_version": SCOPE_VERSION,
        "source_runs": summary["source_runs"],
        "inherited_rows": len(scope),
        "delivery_category_counts": summary["delivery_category_counts"],
        "policy": summary["policy"],
    }
    common.atomic_json(OUT_ROOT / "summary.json", summary)
    common.atomic_json(OUT_ROOT / "RUN_LOCK.json", lock)
    common.artifact_manifest(OUT_ROOT)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
