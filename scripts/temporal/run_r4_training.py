#!/usr/bin/env python3
"""Run3-native R4 frozen-feature head training and calibration.

Default attempt is ``r4_v2`` (H1 quality-head supervision repair). The
v1 contract remains
``docs/dinov3_scorer/RUN-r4-training-calibration-prereg-2026-07-20.md``;
the v2 amendment is
``docs/dinov3_scorer/RUN-r4-v2-h1-quality-supervision-prereg-2026-08-13.md``.
The module cannot read the R0 test split: R4 owns only ``train`` and the
deterministic ``cal_es/cal_fit/cal_select`` subdivision.

The module keeps policy in small pure functions so input locks, the exact head,
continuous Phase-0 posterior, calibration roles, and threshold rules can be
tested without loading the 311k-row corpus or a GPU.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform
import random
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"
for _path in (str(PROJECT_ROOT), str(SRC_ROOT)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from solar_backdating.estimators.survival import (  # noqa: E402
    CensoringInterval,
    cohort_prior_to_json,
    fit_turnbull,
    to_cohort_prior,
)

ATTEMPT_ID = "r4_v2"
CAL_ROLE_SALT = b"r4_v1_cal_roles@2026-07-20"
QUALITY_SUPERVISION_RULE = "r4_v2_h1_teacher_quality_d12iii"
AMBIGUOUS_STATUSES = frozenset({
    "done_ambiguous_nonmonotonic",
    "done_ambiguous_no_recent_anchor",
})
EXPECTED_H1_ALLOWED = {
    "eligible": 245_862, "q0": 9_547, "q1": 236_315,
    "excluded_a1": 4_292, "excluded_ambiguous_status": 20_712,
}
EXPECTED_H1_TRAIN = {"eligible": 205_604, "q0": 8_375, "q1": 197_229}
SEEDS = (2026072001, 2026072002, 2026072003)
FEATURE_DIM = 1152
HIDDEN_DIM = 256
EXPECTED_PARAMS = 298_243
GAP_DAYS = 45
Q_EPS = 1e-3
EXPECTED_CAL_COUNTS = {"cal_es": 2512, "cal_fit": 1799, "cal_select": 1898}
EXPECTED_STATUS_COUNTS = {
    "done_appears": 18_340,
    "done_installed_during_census": 17_395,
    "done_already_present_before_geid_history": 2_119,
    "done_ambiguous_nonmonotonic": 3_315,
    "done_ambiguous_no_recent_anchor": 224,
}
EXPECTED_INPUTS = {
    "manifest.parquet": "ffd0c174d6dc62098ce96ff3a1883b4d3a0006efd7ced99d6543c7a2f053f8cf",
    "splits.parquet": "5a8ba02c95589a9f145d838b406129d17cce086964344356fe80bd924c418ee5",
    "MANIFEST_LOCK.json": "825d44fcbd294b183ad9b536ae224b3b9a356ff382d493a70c6f0b1c65b9a67d",
    "_R3_FEATURE_CACHE_LOCK.json": "86297de7ed35f75cd4cbd961f97b430130fbc69000979a9f0b8dade043bdcf03",
}
EXPECTED_CACHE_KEY = {
    "crop_geometry": "r1_cropgeo_v1@2026-07-19",
    "backbone_hash": "vit_small_patch14_dinov2.lvd142m@sha256:cb91f5a740744d75",
    "pooling_version": "r3_pool_v1",
}
RESOLVED_CONFIG = {
    "attempt_id": ATTEMPT_ID,
    "feature": {
        "dimension": FEATURE_DIM,
        "order": ["feat_roi", "feat_context", "feat_full"],
        "normalizer": "train_population_mean_std_eps_1e-6",
        **EXPECTED_CACHE_KEY,
    },
    "head": {
        "architecture": "layernorm1152_linear256_gelu_dropout_two_head",
        "hidden_dim": HIDDEN_DIM,
        "dropout": 0.10,
        "trainable_parameters": EXPECTED_PARAMS,
    },
    "decoder": {
        "rule": "phase0_continuous_r4_v1",
        "gap_days": GAP_DAYS,
        "q_epsilon": Q_EPS,
        "census_cutoff": "manifest_census_date_with_synthetic_present_epoch",
    },
    "optimizer": {
        "name": "AdamW", "lr": 3e-4, "betas": [0.9, 0.999], "eps": 1e-8,
        "weight_decay": 1e-4, "batch_anchors": 128, "gradient_clip": 1.0,
        "max_epochs": 80, "min_epochs": 15, "patience": 10,
    },
    "calibration": {
        "rule": "hierarchical_platt_r4_v1", "max_iters": 500,
        "gradient_tolerance": 1e-9, "residual_l2": 1.0,
    },
    "threshold": {
        "rule": "r4_v1_lowest_grid_qualifier", "grid_min": 0.50,
        "grid_max": 0.99, "grid_step": 0.01, "overall_wilson_min": 0.7542,
        "under40_wilson_min": 0.7337, "coverage_min": 0.80,
    },
    "seeds": list(SEEDS),
    "quality_supervision": {
        "rule": QUALITY_SUPERVISION_RULE,
        "q_positive": "quality_flag==usable AND label_v1_r0 in {present,absent}",
        "exclude": ["a1_empty_k_patch", "d12iii_ambiguous_anchor"],
    },
}


def config_relpath() -> str:
    return f"config/{ATTEMPT_ID}.yaml"


R4_MANIFEST_COLUMNS = [
    "anchor_id", "capture_date", "chip_index", "src_tiff_sha256", "label_v1",
    "quality_flag", "chip_arm", "area_bin", "source_area_m2", "census_date",
    "scan_status", "split", "legacy_group_anchor_id",
]
SHORTGAP_SIDECAR_COLUMNS = {
    "anchor_id", "capture_date", "endpoint", "override_label",
    "decision_source", "final_class", "interval_loss_eligible",
}
FRAME_PRED_COLUMNS = {
    "seed", "anchor_id", "capture_date", "split_role", "chip_sha",
    "effective_label", "localization_pending", "raw_q_logit", "raw_state_logit",
    "raw_q", "raw_e0", "raw_e1", "cal_q", "cal_e0", "cal_e1",
    "chip_arm", "area_bin", "source_area_m2", "era_bin", "quality_flag",
    "quality_target", "quality_supervision_eligible", "quality_target_reason",
    "checkpoint_sha", "calibrator_hash", "config_hash",
}
ANCHOR_PRED_COLUMNS = {
    "seed", "anchor_id", "split_role", "cell_bounds", "posterior", "map_index",
    "boundary_state", "max_posterior", "accepted", "teacher_bracket", "k_i",
    "map_in_k", "interval_eligible", "source_area_m2", "sustained_diagnostic",
}


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    os.replace(tmp, path)


def atomic_parquet(path: Path, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    frame.to_parquet(tmp, index=False)
    os.replace(tmp, path)


def assert_artifact_schema(frame: pd.DataFrame, required: set[str], *, name: str) -> None:
    if missing := sorted(required - set(frame.columns)):
        raise ValueError(f"{name} missing required columns: {missing}")


def save_checkpoint(model: Any, path: Path, metadata: Mapping[str, Any]) -> str:
    from safetensors.torch import save_file

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tensors = {k: v.detach().cpu().contiguous() for k, v in model.state_dict().items()}
    save_file(tensors, str(tmp), metadata={"attempt_id": ATTEMPT_ID})
    os.replace(tmp, path)
    checkpoint_sha = sha256_file(path)
    atomic_json(path.parent / "selected.json", {**dict(metadata), "checkpoint_sha256": checkpoint_sha})
    return checkpoint_sha


def load_checkpoint(model: Any, path: Path) -> None:
    from safetensors.torch import load_file

    model.load_state_dict(load_file(str(path)), strict=True)


def calibration_role(anchor_id: str) -> tuple[int, str]:
    digest = hashlib.sha256(CAL_ROLE_SALT + b"\0" + anchor_id.encode()).digest()
    bucket = int.from_bytes(digest[:8], "big") % 10
    role = "cal_es" if bucket <= 3 else "cal_fit" if bucket <= 6 else "cal_select"
    return bucket, role


def build_calibration_roles(splits: pd.DataFrame, *, enforce_counts: bool = True) -> pd.DataFrame:
    cal = splits.loc[splits["split"].eq("calibration"), ["anchor_id", "split"]].copy()
    if cal["anchor_id"].duplicated().any():
        raise ValueError("duplicate calibration anchor_id")
    pairs = [calibration_role(str(a)) for a in cal["anchor_id"]]
    cal["hash_bucket"] = [p[0] for p in pairs]
    cal["r4_role"] = [p[1] for p in pairs]
    cal = cal.rename(columns={"split": "r0_split"}).sort_values("anchor_id").reset_index(drop=True)
    if enforce_counts:
        got = cal["r4_role"].value_counts().to_dict()
        if got != EXPECTED_CAL_COUNTS:
            raise ValueError(f"calibration role counts differ: {got} != {EXPECTED_CAL_COUNTS}")
    return cal


def assert_r4_split_access(frame: pd.DataFrame, *, context: str) -> None:
    bad = sorted(set(frame["split"].dropna().astype(str)) - {"train", "calibration"})
    if bad:
        raise PermissionError(f"R4 test-access guard: {context} contains forbidden split(s) {bad}")


def audit_inputs(manifest: pd.DataFrame, splits: pd.DataFrame) -> dict[str, int]:
    required_m = set(R4_MANIFEST_COLUMNS)
    if missing := sorted(required_m - set(manifest.columns)):
        raise ValueError(f"manifest missing columns: {missing}")
    if missing := sorted({"anchor_id", "split", "leakage_component_id"} - set(splits.columns)):
        raise ValueError(f"splits missing columns: {missing}")

    merged = manifest[["anchor_id", "split"]].merge(
        splits, on="anchor_id", how="left", suffixes=("_manifest", "_lock"), validate="many_to_one"
    )
    counts = {
        "anchors_multiple_splits": int(manifest.groupby("anchor_id")["split"].nunique().gt(1).sum()),
        "chip_sha_cross_split": int(manifest.groupby("src_tiff_sha256")["split"].nunique().gt(1).sum()),
        "legacy_group_cross_split": int(manifest.groupby("legacy_group_anchor_id")["split"].nunique().gt(1).sum()),
        "leakage_component_cross_split": int(splits.groupby("leakage_component_id")["split"].nunique().gt(1).sum()),
        "manifest_split_mismatch": int(
            merged["split_lock"].isna().sum()
            + (
                merged["split_lock"].notna()
                & merged["split_lock"].ne(merged["split_manifest"])
            ).sum()
        ),
        "duplicate_anchor_capture_date": int(manifest.duplicated(["anchor_id", "capture_date"]).sum()),
    }
    if any(counts.values()):
        raise ValueError(f"R4 leakage/completeness gate failed: {counts}")
    status_counts = manifest.drop_duplicates("anchor_id")["scan_status"].value_counts().to_dict()
    if status_counts != EXPECTED_STATUS_COUNTS:
        raise ValueError(f"scan-status anchor counts differ: {status_counts}")
    return counts


def effective_label(label: str, tlo: Any | None = None) -> tuple[str, bool]:
    if label not in {"present", "absent", "uninformative"}:
        raise ValueError(f"unknown label_v1 {label!r}")
    if tlo is None:
        return label, True
    if bool(tlo.target_localized) and not bool(tlo.abstain):
        return label, False
    return "uninformative", False


def apply_shortgap_sidecar(
    manifest: pd.DataFrame, sidecar_path: Path | None
) -> tuple[pd.DataFrame, set[str]]:
    """Apply the immutable R4 short-gap sidecar without mutating R0.

    A null override means that the unaffected endpoint retains its R0 label.
    Every sidecar anchor is nevertheless frame-loss-only, as required by the
    amended preregistration.
    """
    out = manifest.copy()
    if sidecar_path is None:
        out["label_v1_r0"] = out["label_v1"]
        out["a1_override"] = False
        out["interval_loss_eligible"] = True
        return out, set()
    if not sidecar_path.is_file():
        raise FileNotFoundError(sidecar_path)
    sidecar = pd.read_parquet(sidecar_path)
    if missing := sorted(SHORTGAP_SIDECAR_COLUMNS - set(sidecar.columns)):
        raise ValueError(f"short-gap sidecar missing columns: {missing}")
    if sidecar.duplicated(["anchor_id", "capture_date"]).any():
        raise ValueError("short-gap sidecar has duplicate frame keys")
    if not sidecar["interval_loss_eligible"].eq(False).all():
        raise ValueError("short-gap sidecar attempted to enable interval loss")
    labels = set(sidecar["override_label"].dropna().astype(str))
    if not labels <= {"present", "absent", "uninformative"}:
        raise ValueError(f"short-gap sidecar has invalid override labels: {sorted(labels)}")
    if set(sidecar["endpoint"].astype(str)) - {"earlier", "later"}:
        raise ValueError("short-gap sidecar may override boundary endpoints only")
    counts = sidecar.groupby("anchor_id").size()
    if not counts.eq(2).all():
        raise ValueError("short-gap sidecar requires exactly two boundary rows per anchor")
    keyed = out.merge(
        sidecar[["anchor_id", "capture_date", "override_label"]],
        on=["anchor_id", "capture_date"], how="left", validate="one_to_one",
        indicator=True,
    )
    matched = int(keyed["_merge"].eq("both").sum())
    if matched != len(sidecar):
        raise ValueError(f"short-gap sidecar matched {matched}/{len(sidecar)} R0 rows")
    keyed["label_v1_r0"] = keyed["label_v1"]
    keyed["a1_override"] = keyed["_merge"].eq("both")
    keyed["label_v1"] = keyed["override_label"].fillna(keyed["label_v1"])
    affected = set(sidecar["anchor_id"].astype(str))
    keyed["interval_loss_eligible"] = ~keyed["anchor_id"].astype(str).isin(affected)
    keyed = keyed.drop(columns=["override_label", "_merge"])
    return keyed, affected


def attach_quality_supervision(frame: pd.DataFrame) -> pd.DataFrame:
    """H1 quality-head targets from teacher quality_flag (D12.iii analogue).

    A1 empty-K patches and ``done_ambiguous_*`` series are excluded from
    quality BCE. State / interval labels are not rewritten here.
    """
    out = frame.copy()
    if "label_v1_r0" not in out.columns:
        out["label_v1_r0"] = out["label_v1"]
    if "a1_override" not in out.columns:
        out["a1_override"] = False
    status = (
        out["scan_status"].astype(str)
        if "scan_status" in out.columns
        else pd.Series("", index=out.index)
    )
    flag = (
        out["quality_flag"].astype(str)
        if "quality_flag" in out.columns
        else pd.Series("usable", index=out.index)
    )
    label_r0 = out["label_v1_r0"].astype(str)
    a1 = out["a1_override"].astype(bool)
    excluded_status = status.isin(AMBIGUOUS_STATUSES)
    eligible = (~a1) & (~excluded_status)
    q_pos = flag.eq("usable") & label_r0.isin(["present", "absent"])
    out["quality_supervision_eligible"] = eligible.to_numpy()
    out["quality_target"] = np.where(eligible, q_pos.astype(np.float64), np.nan)
    out["quality_target_reason"] = np.select(
        [a1, excluded_status, q_pos],
        ["a1_empty_k_patch", "d12iii_ambiguous_anchor", "usable_verdict"],
        default="quality_negative",
    )
    return out


def quality_supervision_counts(frame: pd.DataFrame) -> dict[str, int]:
    eligible = frame["quality_supervision_eligible"].astype(bool)
    return {
        "eligible": int(eligible.sum()),
        "q0": int((eligible & frame["quality_target"].eq(0)).sum()),
        "q1": int((eligible & frame["quality_target"].eq(1)).sum()),
        "excluded_a1": int(frame["a1_override"].astype(bool).sum()),
        "excluded_ambiguous_status": int(
            (~frame["a1_override"].astype(bool) & frame["scan_status"].isin(AMBIGUOUS_STATUSES)).sum()
        ),
    }


def _quality_values(
    quality_targets: Sequence[Any] | None,
    quality_eligible: Sequence[bool] | None,
) -> list[float]:
    if quality_targets is None:
        return []
    eligible = (
        list(quality_eligible)
        if quality_eligible is not None
        else [True] * len(quality_targets)
    )
    values = []
    for target, keep in zip(quality_targets, eligible):
        if keep and target == target:
            values.append(float(target))
    return values


def _parse_date(value: Any) -> date:
    return date.fromisoformat(str(value)[:10])


@dataclass(frozen=True)
class TeacherBracket:
    kind: str
    lower: date | None
    upper: date | None


@dataclass
class AnchorSequence:
    anchor_id: str
    split_role: str
    features: np.ndarray
    labels: list[str]
    capture_dates: list[date]
    ceiling_date: date | None
    bracket: TeacherBracket | None
    source_area_m2: float
    frame_rows: pd.DataFrame


def derive_teacher_bracket(rows: pd.DataFrame) -> TeacherBracket | None:
    if rows["anchor_id"].nunique() != 1 or rows["scan_status"].nunique() != 1:
        raise ValueError("derive_teacher_bracket requires one anchor/status")
    status = str(rows["scan_status"].iloc[0])
    ordered = rows.assign(_date=rows["capture_date"].map(_parse_date)).sort_values(["_date", "chip_index"])
    absent = ordered.loc[ordered["effective_label"].eq("absent"), "_date"].tolist()
    present = ordered.loc[ordered["effective_label"].eq("present"), "_date"].tolist()
    if status == "done_appears":
        if not absent or not present:
            raise ValueError("done_appears missing absent/present bound")
        upper = min(present)
        before = [d for d in absent if d < upper]
        if not before:
            raise ValueError("done_appears missing absent strictly before present")
        return TeacherBracket("interval", max(before), upper)
    if status == "done_installed_during_census":
        census = {_parse_date(v) for v in ordered["census_date"]}
        if len(census) != 1:
            raise ValueError("non-constant census_date")
        upper = census.pop()
        before = [d for d in absent if d < upper]
        if not before:
            raise ValueError("census-bound anchor missing absent strictly before census")
        return TeacherBracket("census_bound", max(before), upper)
    if status == "done_already_present_before_geid_history":
        if not present:
            raise ValueError("left-censored anchor missing present frame")
        return TeacherBracket("left_censored", None, min(present))
    if status in {"done_ambiguous_nonmonotonic", "done_ambiguous_no_recent_anchor"}:
        return None
    raise ValueError(f"unknown scan_status {status!r}")


def bracket_to_turnbull(anchor_id: str, bracket: TeacherBracket) -> CensoringInterval:
    if bracket.kind == "interval":
        return CensoringInterval(anchor_id=anchor_id, lower=bracket.lower, upper=bracket.upper, kind="interval")
    if bracket.kind == "census_bound":
        if bracket.lower is None:
            raise ValueError("census-bound prior interval missing lower bound")
        return CensoringInterval(anchor_id=anchor_id, lower=bracket.lower, upper=bracket.upper, kind="interval")
    if bracket.kind == "left_censored":
        return CensoringInterval(anchor_id=anchor_id, lower=None, upper=bracket.upper, kind="left")
    raise ValueError(bracket.kind)


def audit_interval_representability(manifest: pd.DataFrame) -> dict[str, Any]:
    """Check that every interval-loss target has a cell under frozen gap=45."""
    bad = []
    eligible = 0
    by_status: dict[str, int] = {}
    for anchor_id, rows in manifest.groupby("anchor_id", sort=True):
        if "interval_loss_eligible" in rows and not bool(rows["interval_loss_eligible"].iloc[0]):
            continue
        bracket = derive_teacher_bracket(rows)
        if bracket is None:
            continue
        eligible += 1
        ordered = rows.sort_values(["capture_date", "chip_index"])
        dates = [_parse_date(v) for v in ordered["capture_date"]]
        bounds = _cell_bounds(_epoch_groups(dates), dates)
        try:
            interval_cell_indices(bounds, bracket)
        except ValueError:
            status = str(rows["scan_status"].iloc[0])
            by_status[status] = by_status.get(status, 0) + 1
            if len(bad) < 100:
                bad.append({
                    "anchor_id": str(anchor_id), "scan_status": status,
                    "teacher_bracket": {
                        "kind": bracket.kind,
                        "lower": bracket.lower.isoformat() if bracket.lower else None,
                        "upper": bracket.upper.isoformat() if bracket.upper else None,
                    },
                    "cell_bounds": [
                        [lo.isoformat() if lo else None, hi.isoformat() if hi else None]
                        for lo, hi in bounds
                    ],
                })
    return {
        "rule": "r4_v1_gap45_empty_k_fatal", "gap_days": GAP_DAYS,
        "eligible_anchors": eligible, "empty_k_anchors": sum(by_status.values()),
        "empty_k_by_status": by_status, "examples_capped_100": bad,
        "pass": not by_status,
    }


def feature_normalizer(features: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    if features.ndim != 2 or features.shape[1] != FEATURE_DIM:
        raise ValueError(f"expected [N,{FEATURE_DIM}] features, got {features.shape}")
    mean = features.astype(np.float64).mean(axis=0)
    std = features.astype(np.float64).std(axis=0, ddof=0)
    std[std < 1e-6] = 1.0
    return mean.astype(np.float32), std.astype(np.float32)


def normalizer_frame(mean: np.ndarray, std: np.ndarray) -> pd.DataFrame:
    branches = np.repeat(["roi", "context", "full"], 384)
    return pd.DataFrame({
        "feature_index": np.arange(FEATURE_DIM),
        "branch": branches,
        "branch_index": np.tile(np.arange(384), 3),
        "mean": mean,
        "std": std,
    })


def make_head(
    train_labels: Sequence[str],
    quality_targets: Sequence[Any] | None = None,
    quality_eligible: Sequence[bool] | None = None,
):
    import torch
    from torch import nn

    class R4Head(nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.norm = nn.LayerNorm(FEATURE_DIM, eps=1e-5)
            self.shared = nn.Linear(FEATURE_DIM, HIDDEN_DIM)
            self.dropout = nn.Dropout(0.10)
            self.quality = nn.Linear(HIDDEN_DIM, 1)
            self.state = nn.Linear(HIDDEN_DIM, 2)

        def forward(self, x):
            h = self.dropout(torch.nn.functional.gelu(self.shared(self.norm(x)), approximate="none"))
            return self.quality(h).squeeze(-1), self.state(h)

    labels = list(train_labels)
    q_values = _quality_values(quality_targets, quality_eligible)
    if q_values:
        n_info = sum(value == 1.0 for value in q_values)
        n_uninfo = sum(value == 0.0 for value in q_values)
    else:
        n_info = sum(x in {"present", "absent"} for x in labels)
        n_uninfo = sum(x == "uninformative" for x in labels)
    n_absent = sum(x == "absent" for x in labels)
    n_present = sum(x == "present" for x in labels)
    if min(n_info, n_uninfo, n_absent, n_present) <= 0:
        raise ValueError("all four train label counts must be positive")
    model = R4Head()
    for module in model.modules():
        if isinstance(module, nn.Linear):
            nn.init.xavier_uniform_(module.weight, gain=1.0)
            nn.init.zeros_(module.bias)
    nn.init.ones_(model.norm.weight)
    nn.init.zeros_(model.norm.bias)
    with torch.no_grad():
        model.quality.bias.fill_(math.log(n_info / n_uninfo))
        priors = torch.tensor([n_absent, n_present], dtype=model.state.bias.dtype)
        model.state.bias.copy_(torch.log(priors / priors.sum()))
    n_params = sum(p.numel() for p in model.parameters() if p.requires_grad)
    if n_params != EXPECTED_PARAMS:
        raise AssertionError(f"R4 head params {n_params} != {EXPECTED_PARAMS}")
    return model, {
        "informative": n_info, "uninformative": n_uninfo,
        "absent": n_absent, "present": n_present,
        "quality_bias": math.log(n_info / n_uninfo),
        "state_bias": [math.log(n_absent / (n_absent + n_present)), math.log(n_present / (n_absent + n_present))],
    }


def class_weights(
    labels: Sequence[str],
    quality_targets: Sequence[Any] | None = None,
    quality_eligible: Sequence[bool] | None = None,
) -> dict[str, float]:
    labels = list(labels)
    q_values = _quality_values(quality_targets, quality_eligible)
    counts = {
        "quality_0": (
            sum(value == 0.0 for value in q_values)
            if q_values else sum(x == "uninformative" for x in labels)
        ),
        "quality_1": (
            sum(value == 1.0 for value in q_values)
            if q_values else sum(x in {"present", "absent"} for x in labels)
        ),
        "absent": sum(x == "absent" for x in labels),
        "present": sum(x == "present" for x in labels),
    }
    if min(counts.values()) <= 0:
        raise ValueError("cannot derive class weights with an empty class")
    raw = {k: min(10.0, 1.0 / math.sqrt(v)) for k, v in counts.items()}
    q_mean = (raw["quality_0"] + raw["quality_1"]) / 2
    s_mean = (raw["absent"] + raw["present"]) / 2
    return {
        "quality_0": raw["quality_0"] / q_mean,
        "quality_1": raw["quality_1"] / q_mean,
        "absent": raw["absent"] / s_mean,
        "present": raw["present"] / s_mean,
    }


def _epoch_groups(capture_dates: Sequence[date]) -> list[list[int]]:
    order = sorted(range(len(capture_dates)), key=lambda i: (capture_dates[i], i))
    if not order:
        return []
    groups, current = [], [order[0]]
    last = capture_dates[order[0]]
    for idx in order[1:]:
        d = capture_dates[idx]
        if (d - last).days <= GAP_DAYS:
            current.append(idx)
        else:
            groups.append(current)
            current = [idx]
        last = d
    groups.append(current)
    return groups


def _cell_bounds(groups: Sequence[Sequence[int]], dates: Sequence[date]) -> list[tuple[date | None, date | None]]:
    bounds: list[tuple[date | None, date | None]] = []
    for tau in range(len(groups) + 1):
        if tau == 0:
            bounds.append((None, min(dates[i] for i in groups[0])))
        elif tau == len(groups):
            bounds.append((max(dates[i] for i in groups[-1]), None))
        else:
            bounds.append((max(dates[i] for i in groups[tau - 1]), min(dates[i] for i in groups[tau])))
    return bounds


def interval_cell_indices(
    cell_bounds: Sequence[tuple[date | None, date | None]], bracket: TeacherBracket
) -> list[int]:
    if bracket.kind == "left_censored":
        return [0]
    out = []
    for i, (lo, hi) in enumerate(cell_bounds):
        if hi is None:
            continue
        left = date.min if lo is None else lo
        bleft = date.min if bracket.lower is None else bracket.lower
        if max(left, bleft) < min(hi, bracket.upper):
            out.append(i)
        elif hi == bracket.upper and left < hi:
            out.append(i)
    if not out:
        raise ValueError(f"empty K_i for {bracket}")
    return sorted(set(out))


def _torch_cell_log_prior(
    lo: date | None, hi: date | None, cohort_prior: Any | None
) -> float:
    if cohort_prior is None:
        return 0.0
    if hasattr(cohort_prior, "year_log_mass"):
        year_mass = dict(cohort_prior.year_log_mass)
        if hi is None:
            return float(cohort_prior.beyond_log_mass)
    else:
        # Backward-compatible fixture form; formal R4 uses CohortPrior.
        year_mass = {int(k): float(v) for k, v in cohort_prior.items()}
        if hi is None:
            return float(year_mass[max(year_mass)])
    if lo is None:
        fractions = {hi.year: 1.0}
    else:
        total_days = (hi - lo).days
        if total_days <= 0:
            fractions = {hi.year: 1.0}
        else:
            fractions: dict[int, float] = {}
            cursor = lo
            year = lo.year
            while cursor < hi:
                year_end = date(year + 1, 1, 1)
                segment_end = min(year_end, hi)
                days = (segment_end - cursor).days
                if days > 0:
                    fractions[year] = fractions.get(year, 0.0) + days / total_days
                cursor = segment_end
                year += 1
    mass = sum(math.exp(year_mass[y]) * frac for y, frac in fractions.items() if y in year_mass)
    return math.log(mass) if mass > 0 else -700.0


def torch_phase0_posterior(
    q,
    state_logits,
    capture_dates: Sequence[date],
    cohort_prior: Any | None = None,
    ceiling_date: date | None = None,
):
    """Differentiable numerical translation of the R4 continuous decoder."""
    import torch

    active = [i for i, d in enumerate(capture_dates) if ceiling_date is None or d < ceiling_date]
    active_dates = [capture_dates[i] for i in active]
    groups_all = [[active[j] for j in group] for group in _epoch_groups(active_dates)]
    reps = []
    for group in groups_all:
        local = q[torch.tensor(group, device=q.device)]
        reps.append(group[int(torch.argmax(local).item())])
    kept = [(group, rep) for group, rep in zip(groups_all, reps) if float(q[rep].detach()) >= Q_EPS]
    reps = [rep for _group, rep in kept]
    if not reps:
        return torch.ones(1, dtype=q.dtype, device=q.device), [(None, None)]
    groups = [group for group, _rep in kept]
    epoch_ranges = [
        (min(capture_dates[i] for i in group), max(capture_dates[i] for i in group))
        for group in groups
    ]
    state_prob = torch.softmax(state_logits, dim=-1)
    emissions = [(q[rep], state_prob[rep, 0], state_prob[rep, 1]) for rep in reps]
    if ceiling_date is not None:
        epoch_ranges.append((ceiling_date, ceiling_date))
        one = torch.ones((), dtype=q.dtype, device=q.device)
        zero = torch.zeros((), dtype=q.dtype, device=q.device)
        emissions.append((one, zero, one))
    bounds: list[tuple[date | None, date | None]] = []
    for tau in range(len(epoch_ranges) + 1):
        if tau == 0:
            bounds.append((None, epoch_ranges[0][0]))
        elif tau == len(epoch_ranges):
            bounds.append((epoch_ranges[-1][1], None))
        else:
            bounds.append((epoch_ranges[tau - 1][1], epoch_ranges[tau][0]))
    scores = []
    for tau in range(len(emissions) + 1):
        terms = []
        for pos, (q_value, e0, e1) in enumerate(emissions):
            e = e1 if pos >= tau else e0
            terms.append(torch.log(torch.clamp(q_value * e + (1.0 - q_value), min=1e-12)))
        score = torch.stack(terms).sum()
        if cohort_prior is not None:
            score = score + _torch_cell_log_prior(*bounds[tau], cohort_prior)
        scores.append(score)
    return torch.softmax(torch.stack(scores), dim=0), bounds


def _quality_supervision_from_anchor(anchor: AnchorSequence) -> tuple[list[Any] | None, list[bool] | None]:
    rows = anchor.frame_rows
    if "quality_target" in rows.columns and "quality_supervision_eligible" in rows.columns:
        return (
            rows["quality_target"].tolist(),
            rows["quality_supervision_eligible"].astype(bool).tolist(),
        )
    return None, None


def _flatten_quality_supervision(
    sequences: Sequence[AnchorSequence],
) -> tuple[list[float], list[bool]]:
    targets: list[float] = []
    eligible: list[bool] = []
    for anchor in sequences:
        q_targets, q_eligible = _quality_supervision_from_anchor(anchor)
        if q_targets is None or q_eligible is None:
            targets.extend(float(label != "uninformative") for label in anchor.labels)
            eligible.extend(True for _ in anchor.labels)
        else:
            targets.extend(q_targets)
            eligible.extend(q_eligible)
    return targets, eligible


def anchor_loss(
    q_logits,
    state_logits,
    labels: Sequence[str],
    dates: Sequence[date],
    bracket: TeacherBracket | None,
    weights: Mapping[str, float],
    cohort_prior: Any | None = None,
    ceiling_date: date | None = None,
    quality_targets: Sequence[Any] | None = None,
    quality_eligible: Sequence[bool] | None = None,
):
    import torch
    import torch.nn.functional as F

    if quality_targets is None:
        q_target_list = [float(x != "uninformative") for x in labels]
        q_mask = [True] * len(labels)
    else:
        q_target_list = [0.0 if target != target else float(target) for target in quality_targets]
        q_mask = (
            list(quality_eligible)
            if quality_eligible is not None
            else [target == target for target in quality_targets]
        )
    eligible_idx = [i for i, keep in enumerate(q_mask) if keep]
    if eligible_idx:
        idx = torch.tensor(eligible_idx, device=q_logits.device)
        q_target = torch.tensor(
            [q_target_list[i] for i in eligible_idx],
            dtype=q_logits.dtype, device=q_logits.device,
        )
        q_weight = torch.tensor(
            [weights["quality_1"] if q_target_list[i] else weights["quality_0"] for i in eligible_idx],
            dtype=q_logits.dtype, device=q_logits.device,
        )
        q_loss = (F.binary_cross_entropy_with_logits(q_logits[idx], q_target, reduction="none") * q_weight).mean()
    else:
        q_loss = q_logits.sum() * 0.0
    informative = [i for i, x in enumerate(labels) if x != "uninformative"]
    if informative:
        idx = torch.tensor(informative, device=q_logits.device)
        targets = torch.tensor([0 if labels[i] == "absent" else 1 for i in informative], device=q_logits.device)
        sw = torch.tensor([weights["absent"], weights["present"]], dtype=q_logits.dtype, device=q_logits.device)
        state_loss = F.cross_entropy(state_logits[idx], targets, weight=sw)
    else:
        state_loss = q_logits.sum() * 0.0
    interval_loss = None
    posterior = None
    bounds = None
    if bracket is not None:
        posterior, bounds = torch_phase0_posterior(
            torch.sigmoid(q_logits), state_logits, dates, cohort_prior, ceiling_date
        )
        k_i = interval_cell_indices(bounds, bracket)
        interval_loss = -torch.log(torch.clamp(posterior[k_i].sum(), min=1e-12))
    return interval_loss, q_loss + state_loss, q_loss, state_loss, posterior, bounds


def wilson_lower(successes: int, total: int, z: float = 1.6448536269514722) -> float:
    if total <= 0:
        return 0.0
    p = successes / total
    den = 1 + z * z / total
    centre = p + z * z / (2 * total)
    margin = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total))
    return (centre - margin) / den


def select_threshold(anchors: pd.DataFrame) -> tuple[float | None, list[dict[str, Any]]]:
    required = {"max_posterior", "map_in_k", "interval_eligible", "source_area_m2"}
    if missing := sorted(required - set(anchors.columns)):
        raise ValueError(f"threshold frame missing {missing}")
    rows = []
    n_all = len(anchors)
    for threshold in np.round(np.arange(0.50, 1.00, 0.01), 2):
        accepted = anchors["max_posterior"].ge(threshold)
        elig = accepted & anchors["interval_eligible"]
        small = elig & anchors["source_area_m2"].lt(40)
        n_elig, n_small = int(elig.sum()), int(small.sum())
        ok_elig = int(anchors.loc[elig, "map_in_k"].sum())
        ok_small = int(anchors.loc[small, "map_in_k"].sum())
        row = {
            "threshold": float(threshold), "coverage": float(accepted.mean()) if n_all else 0.0,
            "n_interval": n_elig, "hits_interval": ok_elig,
            "wilson_overall": wilson_lower(ok_elig, n_elig),
            "n_under40": n_small, "hits_under40": ok_small,
            "wilson_under40": wilson_lower(ok_small, n_small),
        }
        row["qualifies"] = bool(
            row["wilson_overall"] >= 0.7542
            and row["wilson_under40"] >= 0.7337
            and row["coverage"] >= 0.80
        )
        rows.append(row)
    selected = next((r["threshold"] for r in rows if r["qualifies"]), None)
    return selected, rows


def _softplus_np(x: float) -> float:
    return float(np.logaddexp(0.0, x))


def fit_hierarchical_platt(
    raw_logits: Sequence[float],
    targets: Sequence[int],
    geometry: Sequence[str],
    era: Sequence[str],
    quality_bin: Sequence[str],
) -> dict[str, Any]:
    """Fit the frozen full-batch float64 hierarchical Platt calibrator."""
    from scipy.optimize import minimize

    logits = np.asarray(raw_logits, dtype=np.float64)
    y = np.asarray(targets, dtype=np.float64)
    if logits.ndim != 1 or len(logits) != len(y) or len(logits) == 0:
        raise ValueError("invalid Platt inputs")
    if set(np.unique(y)) != {0.0, 1.0}:
        raise ValueError("Platt fit requires both binary classes")
    categories = {
        "geometry": sorted(set(map(str, geometry))),
        "era": sorted(set(map(str, era))),
        "quality": sorted(set(map(str, quality_bin))),
    }
    offsets: dict[str, tuple[int, dict[str, int]]] = {}
    cursor = 2
    for name, values in categories.items():
        mapping = {v: i for i, v in enumerate(values)}
        offsets[name] = (cursor, mapping)
        cursor += len(values)
    encoded = {
        "geometry": np.array([offsets["geometry"][1][str(v)] for v in geometry]),
        "era": np.array([offsets["era"][1][str(v)] for v in era]),
        "quality": np.array([offsets["quality"][1][str(v)] for v in quality_bin]),
    }

    def objective(params: np.ndarray) -> tuple[float, np.ndarray]:
        slope = _softplus_np(float(params[0]))
        sigmoid_raw = 1.0 / (1.0 + math.exp(-float(params[0])))
        z = slope * logits + params[1]
        for name in ("geometry", "era", "quality"):
            start, mapping = offsets[name]
            z = z + params[start + encoded[name]]
        nll = np.logaddexp(0.0, z) - y * z
        residual = params[2:]
        loss = float(nll.mean() + 0.5 * np.dot(residual, residual))
        p = 1.0 / (1.0 + np.exp(-np.clip(z, -50, 50)))
        dz = (p - y) / len(y)
        grad = np.zeros_like(params)
        grad[0] = np.dot(dz, logits) * sigmoid_raw
        grad[1] = dz.sum()
        for name in ("geometry", "era", "quality"):
            start, mapping = offsets[name]
            np.add.at(grad, start + encoded[name], dz)
        grad[2:] += residual
        return loss, grad

    result = minimize(
        objective, np.zeros(cursor, dtype=np.float64), jac=True, method="L-BFGS-B",
        options={"maxiter": 500, "gtol": 1e-9, "ftol": 0.0, "maxls": 50},
    )
    if not result.success and result.jac is not None and np.max(np.abs(result.jac)) > 1e-7:
        raise RuntimeError(f"Platt calibration failed: {result.message}")
    params = result.x
    payload = {
        "schema_version": 1, "method": "hierarchical_platt_r4_v1",
        "a_raw": float(params[0]), "slope": _softplus_np(float(params[0])),
        "intercept": float(params[1]), "l2_residual": 1.0,
        "categories": categories, "residuals": {},
        "optimizer": {"name": "LBFGS", "max_iters": 500, "gtol": 1e-9,
                      "success": bool(result.success), "message": str(result.message)},
    }
    for name in ("geometry", "era", "quality"):
        start, mapping = offsets[name]
        payload["residuals"][name] = {v: float(params[start + i]) for v, i in mapping.items()}
    return payload


def apply_hierarchical_platt(
    calibrator: Mapping[str, Any], raw_logits: Sequence[float], geometry: Sequence[str],
    era: Sequence[str], quality_bin: Sequence[str],
) -> np.ndarray:
    z = calibrator["slope"] * np.asarray(raw_logits, dtype=np.float64) + calibrator["intercept"]
    for i, (g, e, q) in enumerate(zip(geometry, era, quality_bin)):
        z[i] += calibrator["residuals"]["geometry"].get(str(g), 0.0)
        z[i] += calibrator["residuals"]["era"].get(str(e), 0.0)
        z[i] += calibrator["residuals"]["quality"].get(str(q), 0.0)
    return 1.0 / (1.0 + np.exp(-np.clip(z, -50, 50)))


def era_bin(capture_date: Any) -> str:
    year = _parse_date(capture_date).year
    if year <= 2020:
        return "2019-2020"
    if year <= 2022:
        return "2021-2022"
    return "2023-2025"


def raw_quality_bin(q: float) -> str:
    return "low" if q < 0.5 else "medium" if q < 0.8 else "high"


def _git_state() -> tuple[str, bool]:
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=PROJECT_ROOT, text=True).strip()
    dirty = bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=PROJECT_ROOT, text=True).strip())
    return commit, dirty


def _environment_snapshot() -> dict[str, Any]:
    import importlib.metadata
    import torch

    packages = {}
    for name in ("torch", "pyarrow", "pandas", "numpy", "safetensors"):
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = None
    freeze = subprocess.check_output(
        [sys.executable, "-m", "pip", "freeze"], text=True
    )
    gpu = None
    if torch.cuda.is_available():
        gpu = {
            "name": torch.cuda.get_device_name(0),
            "count": torch.cuda.device_count(),
            "cuda_runtime": torch.version.cuda,
            "cudnn": torch.backends.cudnn.version(),
        }
    return {
        "python": sys.version,
        "executable": sys.executable,
        "os": platform.platform(),
        "cpu": platform.processor(),
        "packages": packages,
        "gpu": gpu,
        "pip_freeze_sha256": hashlib.sha256(freeze.encode()).hexdigest(),
    }


def prepare_contract(r0_root: Path, cache_root: Path, out_root: Path, *, allow_fixture: bool = False) -> dict[str, Any]:
    manifest_path = r0_root / "manifest.parquet"
    splits_path = r0_root / "splits.parquet"
    lock_path = r0_root / "MANIFEST_LOCK.json"
    cache_lock_path = cache_root / "_R3_FEATURE_CACHE_LOCK.json"
    paths = [manifest_path, splits_path, lock_path, cache_lock_path]
    if not all(p.exists() for p in paths):
        raise FileNotFoundError([str(p) for p in paths if not p.exists()])
    if not allow_fixture:
        for path in paths:
            expected = EXPECTED_INPUTS[path.name]
            got = sha256_file(path)
            if got != expected:
                raise ValueError(f"SHA mismatch {path}: {got} != {expected}")
    manifest = pd.read_parquet(manifest_path)
    splits = pd.read_parquet(splits_path)
    audit = audit_inputs(manifest, splits) if not allow_fixture else {}
    roles = build_calibration_roles(splits, enforce_counts=not allow_fixture)
    atomic_parquet(out_root / "locks/calibration_roles.parquet", roles)
    config_path = out_root / config_relpath()
    atomic_json(config_path, RESOLVED_CONFIG)
    commit, dirty = _git_state()
    run_lock = {
        "attempt_id": ATTEMPT_ID, "status": "PREPARED", "repository_commit": commit,
        "git_dirty": dirty,
        "inputs": {
            str(manifest_path): {
                "sha256": sha256_file(manifest_path), "rows": len(manifest),
                "anchors": int(manifest["anchor_id"].nunique()),
            },
            str(splits_path): {"sha256": sha256_file(splits_path), "rows": len(splits)},
            str(lock_path): {"sha256": sha256_file(lock_path)},
            str(cache_lock_path): {
                "sha256": sha256_file(cache_lock_path),
                "transitively_locks_r3_shards": True,
            },
        },
        "calibration_roles": {"rows": len(roles), "sha256": sha256_file(out_root / "locks/calibration_roles.parquet")},
        "calibration_role_counts": roles["r4_role"].value_counts().sort_index().to_dict(),
        "leakage_counts": audit, "seeds": list(SEEDS), "test_access_guard": True,
        "resolved_config": {
            "path": str(config_path.resolve()), "sha256": sha256_file(config_path),
            "value": RESOLVED_CONFIG,
        },
        "rule_versions": {
            "label": "r4_v1_three_state_tlo_bridge",
            "quality_supervision": QUALITY_SUPERVISION_RULE,
            "bracket": "r4_v1_manifest_teacher_bracket",
            "decoder": "phase0_continuous_r4_v1",
            "calibration": "hierarchical_platt_r4_v1",
            "threshold": "r4_v1_lowest_grid_qualifier",
        },
        "environment": _environment_snapshot(),
    }
    atomic_json(out_root / "locks/RUN_LOCK.json", run_lock)
    return run_lock


def materialize_train_locks(
    r0_root: Path, cache_root: Path, out_root: Path, *, allow_fixture: bool = False,
    shortgap_sidecar: Path | None = None,
) -> dict[str, Any]:
    """Materialize train-only normalizer/prior after the pre-run input gate."""
    run_lock_path = out_root / "locks/RUN_LOCK.json"
    if not run_lock_path.exists():
        raise FileNotFoundError("run prepare before materialize-locks")
    run_lock = json.loads(run_lock_path.read_text())
    manifest = pd.read_parquet(r0_root / "manifest.parquet", columns=R4_MANIFEST_COLUMNS)
    allowed = manifest.loc[manifest["split"].isin(["train", "calibration"])].copy()
    assert_r4_split_access(allowed, context="materialize manifest")
    allowed, frame_only_anchors = apply_shortgap_sidecar(allowed, shortgap_sidecar)
    if shortgap_sidecar is not None and not allow_fixture and len(frame_only_anchors) != 2146:
        raise ValueError(
            f"short-gap sidecar anchor count {len(frame_only_anchors)} != frozen 2146"
        )
    allowed = attach_quality_supervision(allowed)
    allowed["effective_label"] = allowed["label_v1"].map(lambda x: effective_label(str(x))[0])
    if not allow_fixture:
        allowed_counts = quality_supervision_counts(allowed)
        if allowed_counts != EXPECTED_H1_ALLOWED:
            raise ValueError(f"H1 allowed quality counts {allowed_counts} != {EXPECTED_H1_ALLOWED}")

    interval_audit = audit_interval_representability(allowed)
    interval_audit_path = out_root / "locks/interval_cell_audit.json"
    atomic_json(interval_audit_path, interval_audit)
    if not interval_audit["pass"]:
        raise ValueError(
            "R4 frozen interval-cell contract is unrepresentable for "
            f"{interval_audit['empty_k_anchors']} anchors; see {interval_audit_path}"
        )

    train = allowed.loc[allowed["split"].eq("train")].copy()
    train_sha_counts = train["src_tiff_sha256"].astype(str).value_counts().to_dict()
    train_shas = set(train_sha_counts)
    shas_by_prefix: dict[str, list[str]] = {}
    for chip_sha in train_shas:
        shas_by_prefix.setdefault(chip_sha[:2], []).append(chip_sha)
    seen_train: set[str] = set()
    total = np.zeros(FEATURE_DIM, dtype=np.float64)
    total_sq = np.zeros(FEATURE_DIM, dtype=np.float64)
    n = 0
    feature_cols = ["chip_sha", "crop_geometry", "backbone_hash", "pooling_version",
                    "feat_roi", "feat_context", "feat_full"]
    for shard in sorted((cache_root / "features").glob("*.parquet")):
        shard_prefix = shard.stem
        wanted = sorted(shas_by_prefix.get(shard_prefix, []))
        if not wanted:
            continue
        frame = pd.read_parquet(shard, columns=feature_cols, filters=[("chip_sha", "in", wanted)])
        if frame["chip_sha"].duplicated().any():
            raise ValueError(f"duplicate feature key in {shard}")
        for key, expected in EXPECTED_CACHE_KEY.items():
            if set(frame[key].astype(str)) != {expected}:
                raise ValueError(f"unexpected {key} in {shard}")
        vectors = []
        for row in frame.itertuples(index=False):
            parts = [np.asarray(row.feat_roi, dtype=np.float32),
                     np.asarray(row.feat_context, dtype=np.float32),
                     np.asarray(row.feat_full, dtype=np.float32)]
            if any(part.shape != (384,) for part in parts):
                raise ValueError(f"non-384 feature for {row.chip_sha}")
            vector = np.concatenate(parts)
            if not np.isfinite(vector).all():
                raise ValueError(f"non-finite feature for {row.chip_sha}")
            count = int(train_sha_counts[str(row.chip_sha)])
            vectors.append((vector, count))
            seen_train.add(str(row.chip_sha))
        if vectors:
            batch = np.stack([v for v, _count in vectors]).astype(np.float64)
            counts = np.asarray([count for _v, count in vectors], dtype=np.float64)
            total += (batch * counts[:, None]).sum(axis=0)
            total_sq += (np.square(batch) * counts[:, None]).sum(axis=0)
            n += int(counts.sum())
    missing = train_shas - seen_train
    unexpected = seen_train - train_shas
    if missing or unexpected:
        raise ValueError(f"feature join mismatch: missing={len(missing)} unexpected={len(unexpected)}")
    if n != len(train):
        raise ValueError(f"normalizer observation count {n} != train rows {len(train)}")
    mean64 = total / n
    var64 = np.maximum(total_sq / n - np.square(mean64), 0.0)
    std64 = np.sqrt(var64)
    std64[std64 < 1e-6] = 1.0
    normalizer_path = out_root / "locks/feature_normalizer.parquet"
    atomic_parquet(normalizer_path, normalizer_frame(mean64.astype(np.float32), std64.astype(np.float32)))

    intervals = []
    bracket_counts: dict[str, int] = {}
    for anchor_id, rows in train.groupby("anchor_id", sort=True):
        if not bool(rows["interval_loss_eligible"].iloc[0]):
            continue
        bracket = derive_teacher_bracket(rows)
        if bracket is None:
            continue
        intervals.append(bracket_to_turnbull(str(anchor_id), bracket))
        bracket_counts[bracket.kind] = bracket_counts.get(bracket.kind, 0) + 1
    fit = fit_turnbull(intervals, max_iters=500, tol=1e-10)
    prior = to_cohort_prior(
        fit, smooth_lambda=0.05, beyond_policy="terminal_year", temperature=1.0
    )
    prior_payload = {
        "schema_version": 1, "rule": "r4_v1_train_only_turnbull",
        "fit": {
            "year_mass": [[y, mass] for y, mass in fit.year_mass],
            "beyond_mass": fit.beyond_mass, "n_observations": fit.n_observations,
            "n_iterations": fit.n_iterations, "converged": fit.converged,
            "final_loglik": fit.final_loglik,
        },
        "cohort_prior": cohort_prior_to_json(prior), "bracket_counts": bracket_counts,
    }
    prior_path = out_root / "locks/train_cohort_prior.json"
    atomic_json(prior_path, prior_payload)
    train_q_targets = train["quality_target"].tolist()
    train_q_eligible = train["quality_supervision_eligible"].astype(bool).tolist()
    train_counts = quality_supervision_counts(train)
    if not allow_fixture and {
        "eligible": train_counts["eligible"],
        "q0": train_counts["q0"],
        "q1": train_counts["q1"],
    } != EXPECTED_H1_TRAIN:
        raise ValueError(f"H1 train quality counts {train_counts} != {EXPECTED_H1_TRAIN}")
    label_weights = class_weights(
        train["effective_label"].tolist(), train_q_targets, train_q_eligible,
    )
    run_lock.update({
        "status": "LOCKS_MATERIALIZED",
        "feature_normalizer": {"rows": FEATURE_DIM, "n_train_observations": n,
                               "n_unique_train_chips": len(train_shas),
                               "sha256": sha256_file(normalizer_path)},
        "train_cohort_prior": {"n_intervals": len(intervals), "sha256": sha256_file(prior_path),
                               "bracket_counts": bracket_counts},
        "class_weights": label_weights,
        "quality_supervision": {
            "rule": QUALITY_SUPERVISION_RULE,
            "allowed": quality_supervision_counts(allowed),
            "train": train_counts,
        },
        "shortgap_sidecar": None if shortgap_sidecar is None else {
            "path": str(shortgap_sidecar),
            "sha256": sha256_file(shortgap_sidecar),
            "frame_loss_only_anchors": len(frame_only_anchors),
        },
    })
    atomic_json(run_lock_path, run_lock)
    return run_lock


def load_r4_sequences(
    r0_root: Path, cache_root: Path, out_root: Path
) -> tuple[list[AnchorSequence], dict[int, float], dict[str, float]]:
    """Load only train/calibration observations and join the locked R3 cache."""
    lock = json.loads((out_root / "locks/RUN_LOCK.json").read_text())
    if lock.get("status") != "LOCKS_MATERIALIZED":
        raise ValueError("materialize-locks must complete before loading sequences")
    manifest = pd.read_parquet(r0_root / "manifest.parquet", columns=R4_MANIFEST_COLUMNS)
    allowed = manifest.loc[manifest["split"].isin(["train", "calibration"])].copy()
    assert_r4_split_access(allowed, context="training dataloader")
    sidecar_meta = lock.get("shortgap_sidecar")
    sidecar_path = None if not sidecar_meta else Path(sidecar_meta["path"])
    if sidecar_path is not None and sha256_file(sidecar_path) != sidecar_meta["sha256"]:
        raise ValueError("short-gap sidecar SHA differs from R4 run lock")
    allowed, frame_only_anchors = apply_shortgap_sidecar(allowed, sidecar_path)
    allowed = attach_quality_supervision(allowed)
    roles = pd.read_parquet(out_root / "locks/calibration_roles.parquet")
    role_map = roles.set_index("anchor_id")["r4_role"].to_dict()
    allowed["split_role"] = np.where(
        allowed["split"].eq("train"), "train", allowed["anchor_id"].map(role_map)
    )
    if allowed["split_role"].isna().any():
        raise ValueError("calibration anchor missing deterministic R4 role")
    allowed["effective_label"] = allowed["label_v1"].astype(str)
    allowed["_row_order"] = np.arange(len(allowed))

    normalizer = pd.read_parquet(out_root / "locks/feature_normalizer.parquet").sort_values("feature_index")
    mean = normalizer["mean"].to_numpy(np.float32)
    std = normalizer["std"].to_numpy(np.float32)
    wanted_shas = set(allowed["src_tiff_sha256"].astype(str))
    shas_by_prefix: dict[str, list[str]] = {}
    for chip_sha in wanted_shas:
        shas_by_prefix.setdefault(chip_sha[:2], []).append(chip_sha)
    feature_map: dict[str, np.ndarray] = {}
    columns = ["chip_sha", "crop_geometry", "backbone_hash", "pooling_version",
               "feat_roi", "feat_context", "feat_full"]
    for shard in sorted((cache_root / "features").glob("*.parquet")):
        wanted = shas_by_prefix.get(shard.stem, [])
        if not wanted:
            continue
        frame = pd.read_parquet(shard, columns=columns, filters=[("chip_sha", "in", wanted)])
        for key, expected in EXPECTED_CACHE_KEY.items():
            if set(frame[key].astype(str)) != {expected}:
                raise ValueError(f"unexpected {key} in {shard}")
        for row in frame.itertuples(index=False):
            raw = np.concatenate([
                np.asarray(row.feat_roi, np.float32),
                np.asarray(row.feat_context, np.float32),
                np.asarray(row.feat_full, np.float32),
            ])
            if raw.shape != (FEATURE_DIM,):
                raise ValueError(f"bad feature shape for {row.chip_sha}: {raw.shape}")
            if not np.isfinite(raw).all():
                raise ValueError(f"non-finite feature for {row.chip_sha}")
            chip_sha = str(row.chip_sha)
            if chip_sha in feature_map:
                raise ValueError(f"duplicate R3 feature key {chip_sha}")
            feature_map[chip_sha] = ((raw - mean) / std).astype(np.float32)
    missing = wanted_shas - set(feature_map)
    if missing:
        raise ValueError(f"{len(missing)} allowed observations lack R3 features")

    from solar_backdating.estimators.survival import cohort_prior_from_json

    prior_payload = json.loads((out_root / "locks/train_cohort_prior.json").read_text())
    cohort_prior = cohort_prior_from_json(prior_payload["cohort_prior"])
    sequences = []
    for anchor_id, rows in allowed.groupby("anchor_id", sort=True):
        rows = rows.sort_values(["capture_date", "chip_index", "_row_order"]).copy()
        bracket = None if str(anchor_id) in frame_only_anchors else derive_teacher_bracket(rows)
        features = np.stack([feature_map[str(v)] for v in rows["src_tiff_sha256"]])
        sequences.append(AnchorSequence(
            anchor_id=str(anchor_id), split_role=str(rows["split_role"].iloc[0]),
            features=features, labels=rows["effective_label"].astype(str).tolist(),
            capture_dates=[_parse_date(v) for v in rows["capture_date"]],
            ceiling_date=_parse_date(rows["census_date"].iloc[0]), bracket=bracket,
            source_area_m2=float(rows["source_area_m2"].iloc[0]), frame_rows=rows,
        ))
    return sequences, cohort_prior, dict(lock["class_weights"])


def _seed_everything(seed: int, *, device: str) -> None:
    import torch

    random.seed(seed)
    np.random.seed(seed & 0xFFFFFFFF)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)
    torch.use_deterministic_algorithms(True)
    if device.startswith("cuda"):
        torch.backends.cudnn.benchmark = False
        torch.backends.cudnn.deterministic = True


def verify_manifest_decoder_equivalence(
    sequences: Sequence[AnchorSequence],
    cohort_prior: Any,
    out_root: Path,
    *,
    sample_size: int = 200,
) -> dict[str, Any]:
    """Compare the detached torch decoder with reference Phase-0 pre-training."""
    import torch
    from solar_backdating.estimators.changepoint import estimate_changepoint
    from solar_backdating.estimators.emissions import FrameEmission
    from solar_backdating.estimators.seam import ClampContext, EstimatorConfig

    train = [anchor for anchor in sequences if anchor.split_role == "train"]
    if len(train) < sample_size:
        raise ValueError(f"decoder equivalence needs {sample_size} train anchors, got {len(train)}")
    chosen = sorted(
        train,
        key=lambda anchor: (hashlib.sha256(anchor.anchor_id.encode()).hexdigest(), anchor.anchor_id),
    )[:sample_size]
    _seed_everything(SEEDS[0], device="cpu")
    train_labels = [label for anchor in train for label in anchor.labels]
    model, _priors = make_head(train_labels)
    model.eval()
    max_abs_difference = 0.0
    failures = []
    with torch.no_grad():
        for anchor in chosen:
            q_logits, state_logits = model(torch.from_numpy(anchor.features))
            q = torch.sigmoid(q_logits).to(torch.float64)
            state_prob = torch.softmax(state_logits, dim=-1).to(torch.float64)
            got, bounds = torch_phase0_posterior(
                q,
                torch.log(state_prob.clamp_min(1e-12)),
                anchor.capture_dates,
                cohort_prior,
                anchor.ceiling_date,
            )
            frames = [
                FrameEmission(
                    capture_date=capture_date,
                    q=float(q[i]),
                    e0=float(state_prob[i, 0]),
                    e1=float(state_prob[i, 1]),
                    source_row=i,
                )
                for i, capture_date in enumerate(anchor.capture_dates)
            ]
            ref = estimate_changepoint(
                [],
                ClampContext(ceiling_date=anchor.ceiling_date),
                EstimatorConfig(
                    decoder_epoch_gap_days=GAP_DAYS,
                    frame_emissions=frames,
                    cohort_prior=cohort_prior,
                ),
            )
            got_np = got.detach().cpu().numpy()
            ref_np = np.asarray(ref.posterior, dtype=np.float64)
            difference = (
                float(np.max(np.abs(got_np - ref_np)))
                if got_np.shape == ref_np.shape
                else float("inf")
            )
            max_abs_difference = max(max_abs_difference, difference)
            got_map = int(np.argmax(got_np))
            got_boundary = (
                "already_present" if got_map == 0 else
                "beyond_window" if got_map == len(got_np) - 1 else
                "interval"
            )
            ref_boundary = (
                "already_present" if ref.map_index == 0 else
                "beyond_window" if ref.epochs[ref.map_index].is_beyond_window else
                "interval"
            )
            ref_bounds = [(cell.start_date, cell.end_date) for cell in ref.epochs]
            if (
                difference > 1e-6
                or got_map != ref.map_index
                or got_boundary != ref_boundary
                or bounds != ref_bounds
            ):
                failures.append({
                    "anchor_id": anchor.anchor_id,
                    "max_abs_difference": difference,
                    "torch_map_index": got_map,
                    "reference_map_index": ref.map_index,
                    "torch_boundary_state": got_boundary,
                    "reference_boundary_state": ref_boundary,
                    "bounds_equal": bounds == ref_bounds,
                })
    payload = {
        "rule": "r4_v1_manifest_decoder_equivalence",
        "selection": "ascending_sha256_utf8_anchor_id",
        "seed": SEEDS[0],
        "sample_size": sample_size,
        "max_abs_posterior_difference": max_abs_difference,
        "tolerance": 1e-6,
        "failure_count": len(failures),
        "failures": failures[:20],
        "pass": not failures,
    }
    path = out_root / "locks/decoder_equivalence_200.json"
    atomic_json(path, payload)
    if failures:
        raise ValueError(f"manifest decoder equivalence failed; see {path}")
    return payload


def _batch_loss(model: Any, anchors: Sequence[AnchorSequence], weights: Mapping[str, float], cohort_prior: Any, device: str):
    import torch

    lengths = [len(a.labels) for a in anchors]
    x = torch.from_numpy(np.concatenate([a.features for a in anchors])).to(device)
    q_logits, state_logits = model(x)
    interval_losses, frame_losses = [], []
    quality_losses, state_losses = [], []
    offset = 0
    for anchor, length in zip(anchors, lengths):
        frame_slice = slice(offset, offset + length)
        q_targets, q_eligible = _quality_supervision_from_anchor(anchor)
        il, fl, quality_loss, state_loss, _posterior, _bounds = anchor_loss(
            q_logits[frame_slice], state_logits[frame_slice], anchor.labels, anchor.capture_dates,
            anchor.bracket, weights, cohort_prior, anchor.ceiling_date,
            quality_targets=q_targets, quality_eligible=q_eligible,
        )
        if il is not None:
            interval_losses.append(il)
        frame_losses.append(fl)
        quality_losses.append(quality_loss)
        state_losses.append(state_loss)
        offset += length
    frame_mean = torch.stack(frame_losses).mean()
    quality_mean = torch.stack(quality_losses).mean()
    state_mean = torch.stack(state_losses).mean()
    interval_mean = torch.stack(interval_losses).mean() if interval_losses else frame_mean * 0.0
    return interval_mean + 0.25 * frame_mean, interval_mean, frame_mean, quality_mean, state_mean


def evaluate_anchor_losses(model: Any, anchors: Sequence[AnchorSequence], weights: Mapping[str, float], cohort_prior: Mapping[int, float], device: str, batch_size: int = 128) -> tuple[float, float]:
    import torch

    model.eval()
    interval_sum = frame_sum = 0.0
    interval_n = frame_n = 0
    with torch.no_grad():
        for start in range(0, len(anchors), batch_size):
            batch = anchors[start:start + batch_size]
            _total, interval, frame, _quality, _state = _batch_loss(
                model, batch, weights, cohort_prior, device
            )
            n_i = sum(a.bracket is not None for a in batch)
            interval_sum += float(interval) * n_i
            interval_n += n_i
            frame_sum += float(frame) * len(batch)
            frame_n += len(batch)
    return interval_sum / max(interval_n, 1), frame_sum / max(frame_n, 1)


def train_one_seed(
    sequences: Sequence[AnchorSequence], cohort_prior: Mapping[int, float],
    weights: Mapping[str, float], seed: int, out_root: Path, *, device: str,
    max_epochs: int = 80, min_epochs: int = 15, patience: int = 10,
) -> dict[str, Any]:
    """Frozen R4 optimizer/checkpoint loop; test sequences are impossible here."""
    import torch

    if seed not in SEEDS:
        raise ValueError(f"seed must be one of {SEEDS}")
    if any(a.split_role == "test" for a in sequences):
        raise PermissionError("R4 training received test anchor")
    train = [a for a in sequences if a.split_role == "train"]
    cal_es = [a for a in sequences if a.split_role == "cal_es"]
    if not train or not cal_es:
        raise ValueError("train/cal_es sequences required")
    _seed_everything(seed, device=device)
    q_targets, q_eligible = _flatten_quality_supervision(train)
    model, prior_counts = make_head(
        [label for a in train for label in a.labels], q_targets, q_eligible,
    )
    model.to(device)
    optimizer = torch.optim.AdamW(
        model.parameters(), lr=3e-4, betas=(0.9, 0.999), eps=1e-8, weight_decay=1e-4
    )
    epoch0_interval, epoch0_frame = evaluate_anchor_losses(
        model, cal_es, weights, cohort_prior, device
    )
    best_key = (float("inf"), float("inf"), 10**9)
    significant_best = float("inf")
    best_path = None
    stale = 0
    logs = []
    rng = np.random.default_rng(seed)
    for epoch in range(1, max_epochs + 1):
        epoch_started = time.monotonic()
        order = rng.permutation(len(train))
        model.train()
        totals = []
        interval_totals = []
        frame_totals = []
        quality_totals = []
        state_totals = []
        batch_anchor_counts = []
        grad_norms = []
        for start in range(0, len(order), 128):
            batch = [train[int(i)] for i in order[start:start + 128]]
            optimizer.zero_grad(set_to_none=True)
            total, interval, frame, quality, state = _batch_loss(
                model, batch, weights, cohort_prior, device
            )
            if not all(torch.isfinite(value) for value in (total, interval, frame, quality, state)):
                raise FloatingPointError(f"non-finite training loss seed={seed} epoch={epoch}")
            total.backward()
            grad_norm = torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            totals.append(float(total.detach()))
            interval_totals.append(float(interval.detach()))
            frame_totals.append(float(frame.detach()))
            quality_totals.append(float(quality.detach()))
            state_totals.append(float(state.detach()))
            batch_anchor_counts.append(len(batch))
            grad_norms.append(float(grad_norm))
        cal_interval, cal_frame = evaluate_anchor_losses(
            model, cal_es, weights, cohort_prior, device
        )
        if not math.isfinite(cal_interval) or not math.isfinite(cal_frame):
            raise FloatingPointError(f"non-finite cal_es loss seed={seed} epoch={epoch}")
        checkpoint = out_root / f"seeds/{seed}/checkpoints/epoch_{epoch:03d}.safetensors"
        checkpoint_sha = save_checkpoint(
            model, checkpoint, {"seed": seed, "epoch": epoch,
                                "selection_tuple": [cal_interval, cal_frame, epoch]},
        )
        logs.append({
            "seed": seed, "epoch": epoch,
            "train_total": float(np.average(totals, weights=batch_anchor_counts)),
            "train_interval": float(np.average(interval_totals, weights=batch_anchor_counts)),
            "train_frame": float(np.average(frame_totals, weights=batch_anchor_counts)),
            "train_quality": float(np.average(quality_totals, weights=batch_anchor_counts)),
            "train_state": float(np.average(state_totals, weights=batch_anchor_counts)),
            "cal_es_interval_nll": cal_interval, "cal_es_frame_loss": cal_frame,
            "gradient_norm_mean": float(np.mean(grad_norms)), "learning_rate": 3e-4,
            "checkpoint_sha256": checkpoint_sha,
            "wall_time_seconds": time.monotonic() - epoch_started,
        })
        key = (cal_interval, cal_frame, epoch)
        selection_better = (
            cal_interval < best_key[0] - 1e-6
            or (abs(cal_interval - best_key[0]) <= 1e-6 and key[1:] < best_key[1:])
        )
        if selection_better:
            best_key, best_path = key, checkpoint
        if cal_interval < significant_best - 1e-4:
            significant_best = cal_interval
            stale = 0
        else:
            stale += 1
        if epoch >= min_epochs and stale >= patience:
            break
    if best_path is None:
        raise RuntimeError("no eligible checkpoint")
    load_checkpoint(model, best_path)
    log_path = out_root / f"seeds/{seed}/train.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = log_path.with_suffix(".jsonl.tmp")
    tmp.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in logs))
    os.replace(tmp, log_path)
    selected = {
        "seed": seed, "epoch": best_key[2], "cal_es_interval_nll": best_key[0],
        "cal_es_frame_loss": best_key[1], "checkpoint_path": str(best_path),
        "checkpoint_sha256": sha256_file(best_path), "epoch0_interval_nll": epoch0_interval,
        "epoch0_frame_loss": epoch0_frame,
        "interval_improvement_fraction": (epoch0_interval - best_key[0]) / epoch0_interval,
        "prior_counts": prior_counts,
    }
    atomic_json(out_root / f"seeds/{seed}/checkpoints/selected.json", selected)
    return selected


def _binary_metrics(
    y: np.ndarray, raw_p: np.ndarray, cal_p: np.ndarray, train_prevalence: float
) -> dict[str, Any]:
    from sklearn.metrics import roc_auc_score

    eps = 1e-12
    def nll(p):
        p = np.clip(p, eps, 1 - eps)
        return float(np.mean(-(y * np.log(p) + (1 - y) * np.log(1 - p))))
    order = np.argsort(cal_p, kind="stable")
    bins = np.array_split(order, 15)
    ece = sum(
        len(idx) / len(y) * abs(float(cal_p[idx].mean()) - float(y[idx].mean()))
        for idx in bins if len(idx)
    )
    reliability = [
        {
            "bin": i,
            "count": int(len(idx)),
            "mean_probability": float(cal_p[idx].mean()) if len(idx) else None,
            "positive_rate": float(y[idx].mean()) if len(idx) else None,
        }
        for i, idx in enumerate(bins)
    ]
    auroc = float(roc_auc_score(y, cal_p)) if len(np.unique(y)) == 2 else None
    return {
        "rows": int(len(y)), "positives": int(y.sum()), "negatives": int(len(y) - y.sum()),
        "raw_nll": nll(raw_p), "calibrated_nll": nll(cal_p),
        "raw_brier": float(np.mean(np.square(raw_p - y))),
        "calibrated_brier": float(np.mean(np.square(cal_p - y))),
        "constant_train_prevalence_brier": float(np.mean(np.square(train_prevalence - y))),
        "ece_15_equal_count": float(ece),
        "auroc": auroc, "reliability_bins": reliability,
    }


def calibration_slice_report(
    frames: pd.DataFrame,
    *,
    target: str,
    train_prevalence: float,
) -> dict[str, Any]:
    if target == "quality":
        if {"quality_target", "quality_supervision_eligible"} <= set(frames.columns):
            rows = frames[frames["quality_supervision_eligible"].astype(bool)].copy()
            rows["_target"] = rows["quality_target"].astype(float)
        else:
            rows = frames.copy()
            rows["_target"] = rows["effective_label"].ne("uninformative").astype(float)
        raw_col, cal_col = "raw_q", "cal_q"
    elif target == "state":
        rows = frames[frames["effective_label"].ne("uninformative")].copy()
        rows["_target"] = rows["effective_label"].eq("present").astype(float)
        raw_col, cal_col = "raw_e1", "cal_e1"
    else:
        raise ValueError(target)
    rows["capture_year"] = rows["capture_date"].astype(str).str[:4]
    rows["under40"] = np.where(rows["source_area_m2"].lt(40), "under40", "40plus")

    def one(frame: pd.DataFrame) -> dict[str, Any]:
        y = frame["_target"].to_numpy(np.float64)
        metrics = _binary_metrics(
            y, frame[raw_col].to_numpy(np.float64), frame[cal_col].to_numpy(np.float64),
            train_prevalence,
        )
        metrics["underpowered"] = bool(
            len(frame) < 200 or metrics["positives"] < 20 or metrics["negatives"] < 20
        )
        return metrics

    dimensions = {
        "chip_arm": "chip_arm",
        "area_bin": "area_bin",
        "era_bin": "era_bin",
        "capture_year": "capture_year",
        "raw_q_quality_bin": "raw_quality_bin",
        "teacher_quality_flag": "quality_flag",
        "under40_headline": "under40",
    }
    slices = {}
    for name, column in dimensions.items():
        slices[name] = {
            str(value): one(group)
            for value, group in rows.groupby(column, sort=True, dropna=False)
        }
    return {"overall": one(rows), "slices": slices}


def bootstrap_anchor_rates(
    anchors: pd.DataFrame,
    threshold: float | None,
    *,
    n_resamples: int = 2000,
    seed: int = 2026072099,
) -> dict[str, Any]:
    if threshold is None or anchors.empty:
        return {"seed": seed, "n_resamples": n_resamples, "available": False}
    ordered = anchors.sort_values("anchor_id").reset_index(drop=True)
    rng = np.random.default_rng(seed)
    values = {"coverage": [], "accepted_map_in_k": [], "under40_accepted_map_in_k": []}
    for _ in range(n_resamples):
        sample = ordered.iloc[rng.integers(0, len(ordered), len(ordered))]
        accepted = sample["max_posterior"].ge(threshold)
        eligible = accepted & sample["interval_eligible"]
        small = eligible & sample["source_area_m2"].lt(40)
        values["coverage"].append(float(accepted.mean()))
        values["accepted_map_in_k"].append(
            float(sample.loc[eligible, "map_in_k"].mean()) if eligible.any() else float("nan")
        )
        values["under40_accepted_map_in_k"].append(
            float(sample.loc[small, "map_in_k"].mean()) if small.any() else float("nan")
        )
    intervals = {}
    for name, samples in values.items():
        finite = np.asarray(samples, dtype=np.float64)
        finite = finite[np.isfinite(finite)]
        intervals[name] = {
            "point": (
                float(ordered["max_posterior"].ge(threshold).mean())
                if name == "coverage" else
                float(ordered.loc[
                    ordered["max_posterior"].ge(threshold)
                    & ordered["interval_eligible"]
                    & (ordered["source_area_m2"].lt(40) if name.startswith("under40") else True),
                    "map_in_k",
                ].mean())
            ),
            "p2_5": float(np.percentile(finite, 2.5)),
            "p97_5": float(np.percentile(finite, 97.5)),
            "finite_resamples": int(len(finite)),
        }
    return {"seed": seed, "n_resamples": n_resamples, "available": True, "intervals": intervals}


def calibrate_one_seed(
    sequences: Sequence[AnchorSequence], cohort_prior: Mapping[int, float],
    seed: int, selected: Mapping[str, Any], out_root: Path, *, device: str,
) -> dict[str, Any]:
    import torch

    train_labels = [label for a in sequences if a.split_role == "train" for label in a.labels]
    model, priors = make_head(train_labels)
    load_checkpoint(model, Path(str(selected["checkpoint_path"])))
    model.to(device).eval()
    frame_records = []
    with torch.no_grad():
        for anchor in sequences:
            if anchor.split_role not in {"cal_es", "cal_fit", "cal_select"}:
                continue
            qlog, slog = model(torch.from_numpy(anchor.features).to(device))
            qlog_np = qlog.cpu().numpy().astype(np.float64)
            slog_np = slog.cpu().numpy().astype(np.float64)
            raw_q = 1 / (1 + np.exp(-np.clip(qlog_np, -50, 50)))
            raw_state = np.exp(slog_np - slog_np.max(axis=1, keepdims=True))
            raw_state /= raw_state.sum(axis=1, keepdims=True)
            q_targets, q_eligible = _quality_supervision_from_anchor(anchor)
            for i, row in enumerate(anchor.frame_rows.itertuples(index=False)):
                geometry = f"{row.chip_arm}|{row.area_bin}"
                eligible = bool(q_eligible[i]) if q_eligible is not None else True
                target = (
                    float(q_targets[i])
                    if q_targets is not None and q_targets[i] == q_targets[i]
                    else (1.0 if anchor.labels[i] != "uninformative" else 0.0)
                )
                reason = (
                    str(getattr(row, "quality_target_reason", "usable_verdict"))
                    if eligible else str(getattr(row, "quality_target_reason", "a1_empty_k_patch"))
                )
                frame_records.append({
                    "seed": seed, "anchor_id": anchor.anchor_id,
                    "capture_date": str(row.capture_date)[:10], "split_role": anchor.split_role,
                    "chip_sha": str(row.src_tiff_sha256), "effective_label": anchor.labels[i],
                    "localization_pending": True, "raw_q_logit": qlog_np[i],
                    "raw_state_logit": slog_np[i, 1] - slog_np[i, 0], "raw_q": raw_q[i],
                    "raw_e0": raw_state[i, 0], "raw_e1": raw_state[i, 1],
                    "chip_arm": str(row.chip_arm), "area_bin": str(row.area_bin),
                    "source_area_m2": float(row.source_area_m2),
                    "era_bin": era_bin(row.capture_date), "quality_flag": str(row.quality_flag),
                    "quality_target": target if eligible else float("nan"),
                    "quality_supervision_eligible": eligible,
                    "quality_target_reason": reason,
                    "geometry_cell": geometry, "raw_quality_bin": raw_quality_bin(raw_q[i]),
                })
    frames = pd.DataFrame(frame_records)
    fit = frames[frames["split_role"].eq("cal_fit")]
    fit_q = fit[fit["quality_supervision_eligible"].astype(bool)]
    if fit_q.empty:
        raise ValueError("cal_fit has no quality-supervision-eligible rows")
    q_cal = fit_hierarchical_platt(
        fit_q["raw_q_logit"], fit_q["quality_target"].astype(int),
        fit_q["geometry_cell"], fit_q["era_bin"], fit_q["raw_quality_bin"],
    )
    fit_state = fit[fit["effective_label"].ne("uninformative")]
    state_cal = fit_hierarchical_platt(
        fit_state["raw_state_logit"], fit_state["effective_label"].eq("present").astype(int),
        fit_state["geometry_cell"], fit_state["era_bin"], fit_state["raw_quality_bin"],
    )
    seed_root = out_root / f"seeds/{seed}"
    atomic_json(seed_root / "calibrators/quality.json", q_cal)
    atomic_json(seed_root / "calibrators/state.json", state_cal)
    cal_hash = hashlib.sha256(
        (sha256_file(seed_root / "calibrators/quality.json") +
         sha256_file(seed_root / "calibrators/state.json")).encode()
    ).hexdigest()
    frames["cal_q"] = apply_hierarchical_platt(
        q_cal, frames["raw_q_logit"], frames["geometry_cell"], frames["era_bin"], frames["raw_quality_bin"]
    )
    frames["cal_e1"] = apply_hierarchical_platt(
        state_cal, frames["raw_state_logit"], frames["geometry_cell"], frames["era_bin"], frames["raw_quality_bin"]
    )
    frames["cal_e0"] = 1.0 - frames["cal_e1"]
    frames["checkpoint_sha"] = str(selected["checkpoint_sha256"])
    frames["calibrator_hash"] = cal_hash
    frames["config_hash"] = sha256_file(out_root / config_relpath())

    anchors = []
    seq_map = {a.anchor_id: a for a in sequences}
    for (role, anchor_id), group in frames.groupby(["split_role", "anchor_id"], sort=True):
        from solar_backdating.estimators.baselines import estimate_sustained
        from solar_backdating.estimators.seam import VintageObservation

        anchor = seq_map[str(anchor_id)]
        q = torch.tensor(group["cal_q"].to_numpy(), dtype=torch.float64)
        state = torch.log(torch.tensor(group[["cal_e0", "cal_e1"]].to_numpy(), dtype=torch.float64).clamp_min(1e-12))
        posterior, bounds = torch_phase0_posterior(
            q, state, anchor.capture_dates, cohort_prior, anchor.ceiling_date
        )
        post = posterior.numpy()
        map_index = int(np.argmax(post))
        k_i = interval_cell_indices(bounds, anchor.bracket) if anchor.bracket is not None else []
        teacher_mass = float(post[k_i].sum()) if k_i else None
        sustained_rows = [
            VintageObservation(
                capture_date=_parse_date(row.capture_date),
                pv_present=(
                    "" if float(row.cal_q) < Q_EPS else
                    "1" if float(row.cal_e1) >= float(row.cal_e0) else "0"
                ),
                confidence=float(row.cal_q),
                quality_flag="usable",
                source_row=i,
            )
            for i, row in enumerate(group.itertuples(index=False))
            if anchor.ceiling_date is None or _parse_date(row.capture_date) < anchor.ceiling_date
        ]
        sustained = estimate_sustained(sustained_rows)
        anchors.append({
            "seed": seed, "anchor_id": anchor_id, "split_role": role,
            "cell_bounds": json.dumps([[x.isoformat() if x else None, y.isoformat() if y else None] for x, y in bounds]),
            "posterior": post.tolist(), "map_index": map_index,
            "boundary_state": "already_present" if map_index == 0 else "beyond_window" if map_index == len(post)-1 else "interval",
            "max_posterior": float(post.max()), "accepted": False,
            "teacher_bracket": json.dumps(anchor.bracket.__dict__, default=str) if anchor.bracket else None,
            "k_i": k_i, "map_in_k": map_index in k_i,
            "interval_eligible": anchor.bracket is not None,
            "source_area_m2": anchor.source_area_m2,
            "teacher_mass": teacher_mass,
            "sustained_diagnostic": json.dumps({
                "map_date": sustained.map_date,
                "map_interval_start": (
                    sustained.map_interval_start.isoformat()
                    if sustained.map_interval_start else None
                ),
                "map_interval_end": (
                    sustained.map_interval_end.isoformat()
                    if sustained.map_interval_end else None
                ),
            }, sort_keys=True),
        })
    anchor_df = pd.DataFrame(anchors)
    select = anchor_df[anchor_df["split_role"].eq("cal_select")].copy()
    threshold, grid = select_threshold(select)
    if threshold is not None:
        anchor_df["accepted"] = anchor_df["max_posterior"].ge(threshold)
    atomic_json(seed_root / "thresholds.json", {"selected_threshold": threshold, "grid": grid})
    for role in ("cal_es", "cal_fit", "cal_select"):
        frame_out = frames[frames["split_role"].eq(role)].drop(columns=["geometry_cell", "raw_quality_bin"])
        anchor_out = anchor_df[anchor_df["split_role"].eq(role)]
        assert_artifact_schema(frame_out, FRAME_PRED_COLUMNS, name=f"{role} frames")
        assert_artifact_schema(anchor_out, ANCHOR_PRED_COLUMNS, name=f"{role} anchors")
        atomic_parquet(seed_root / f"predictions_{role}.parquet", frame_out)
        atomic_parquet(seed_root / f"anchors_{role}.parquet", anchor_out)

    sel_frames = frames[frames["split_role"].eq("cal_select")]
    q_prevalence = priors["informative"] / (priors["informative"] + priors["uninformative"])
    state_prevalence = priors["present"] / (priors["present"] + priors["absent"])
    q_report = calibration_slice_report(
        sel_frames, target="quality", train_prevalence=q_prevalence
    )
    state_report = calibration_slice_report(
        sel_frames, target="state", train_prevalence=state_prevalence
    )
    q_metrics = q_report["overall"]
    s_metrics = state_report["overall"]
    calibration_failed = any([
        not np.isfinite(frames[["raw_q", "raw_e0", "raw_e1", "cal_q", "cal_e0", "cal_e1"]].to_numpy()).all(),
        q_metrics["calibrated_nll"] > q_metrics["raw_nll"] + 0.005,
        s_metrics["calibrated_nll"] > s_metrics["raw_nll"] + 0.005,
        q_metrics["ece_15_equal_count"] > 0.05,
        s_metrics["ece_15_equal_count"] > 0.05,
        q_metrics["calibrated_brier"] >= q_metrics["constant_train_prevalence_brier"],
        s_metrics["calibrated_brier"] >= s_metrics["constant_train_prevalence_brier"],
        threshold is None,
    ])
    eligible_select = select[select["interval_eligible"]]
    map_in_k_rate = float(eligible_select["map_in_k"].mean()) if len(eligible_select) else 0.0
    interval_nll = float(
        np.mean([-math.log(max(float(v), 1e-12)) for v in eligible_select["teacher_mass"]])
    ) if len(eligible_select) else float("inf")
    bootstrap = bootstrap_anchor_rates(select, threshold)
    metrics = {"seed": seed, "quality": q_report, "state": state_report,
               "selected_threshold": threshold, "calibration_failed": calibration_failed,
               "cal_select_map_in_k_rate": map_in_k_rate,
               "cal_select_interval_nll": interval_nll,
               "coverage_risk_bootstrap": bootstrap}
    atomic_json(seed_root / "metrics.json", metrics)
    return metrics


def finalize_r4_artifacts(out_root: Path, seed_results: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    frame_parts = []
    for seed in SEEDS:
        for role in ("cal_es", "cal_fit", "cal_select"):
            frame_parts.append(pd.read_parquet(out_root / f"seeds/{seed}/predictions_{role}.parquet"))
    combined = pd.concat(frame_parts, ignore_index=True)
    atomic_parquet(out_root / "predictions/calibration_all_seeds.parquet", combined)
    ranked = sorted(
        seed_results,
        key=lambda item: (
            -float(item["calibration"]["cal_select_map_in_k_rate"]),
            float(item["calibration"]["cal_select_interval_nll"]),
            int(item["training"]["seed"]),
        ),
    )
    median_seed = int(ranked[1]["training"]["seed"])
    summary = {
        "median_ranked_seed": median_seed,
        "ranking": [int(item["training"]["seed"]) for item in ranked],
        "seeds": list(seed_results),
    }
    atomic_json(out_root / "metrics/seed_summary.json", summary)
    return {"median_ranked_seed": median_seed}


def _required_r4_artifacts(out_root: Path) -> list[Path]:
    paths = [
        out_root / config_relpath(),
        out_root / "locks/RUN_LOCK.json",
        out_root / "locks/calibration_roles.parquet",
        out_root / "locks/feature_normalizer.parquet",
        out_root / "locks/train_cohort_prior.json",
        out_root / "locks/decoder_equivalence_200.json",
        out_root / "predictions/calibration_all_seeds.parquet",
        out_root / "metrics/R4_HEALTH.json",
        out_root / "metrics/seed_summary.json",
    ]
    for seed in SEEDS:
        root = out_root / f"seeds/{seed}"
        paths.extend([
            root / "train.jsonl",
            root / "checkpoints/selected.json",
            root / "calibrators/quality.json",
            root / "calibrators/state.json",
            root / "thresholds.json",
            root / "metrics.json",
        ])
        for role in ("cal_es", "cal_fit", "cal_select"):
            paths.append(root / f"predictions_{role}.parquet")
            paths.append(root / f"anchors_{role}.parquet")
    return paths


def evaluate_r4_health(
    out_root: Path, seed_results: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    lock = json.loads((out_root / "locks/RUN_LOCK.json").read_text())
    by_seed = {int(item["training"]["seed"]): item for item in seed_results}
    missing_paths = [str(path.relative_to(out_root)) for path in _required_r4_artifacts(out_root) if not path.is_file()]
    checks = {
        "clean_repository_lock": not bool(lock.get("git_dirty")),
        "leakage_counts_zero": not any(lock.get("leakage_counts", {}).values()),
        "test_access_guard": bool(lock.get("test_access_guard")),
        "decoder_equivalence": bool(lock.get("manifest_decoder_equivalence", {}).get("pass")),
        "all_three_seeds_present": set(by_seed) == set(SEEDS),
        "all_seed_values_finite": all(
            math.isfinite(float(item["training"][key]))
            for item in seed_results
            for key in ("cal_es_interval_nll", "cal_es_frame_loss", "interval_improvement_fraction")
        ),
        "all_seed_training_improvement_ge_1pct": all(
            float(item["training"]["interval_improvement_fraction"]) >= 0.01
            for item in seed_results
        ),
        "at_least_two_calibration_healthy": sum(
            not bool(item["calibration"]["calibration_failed"]) for item in seed_results
        ) >= 2,
        "median_seed_threshold_valid": False,
        "required_artifacts_present": not missing_paths,
    }
    summary_path = out_root / "metrics/seed_summary.json"
    if summary_path.is_file():
        median_seed = int(json.loads(summary_path.read_text())["median_ranked_seed"])
        checks["median_seed_threshold_valid"] = (
            median_seed in by_seed
            and by_seed[median_seed]["calibration"].get("selected_threshold") is not None
        )
    if not checks["all_three_seeds_present"] or not checks["all_seed_values_finite"] or not checks["all_seed_training_improvement_ge_1pct"]:
        verdict = "TRAINING_FAILED"
    elif not checks["at_least_two_calibration_healthy"] or not checks["median_seed_threshold_valid"]:
        verdict = "CALIBRATION_FAILED"
    elif not all(checks.values()):
        verdict = "TRAINING_FAILED"
    else:
        verdict = "READY_FOR_R5"
    return {
        "attempt_id": ATTEMPT_ID,
        "verdict": verdict,
        "checks": checks,
        "missing_required_artifacts": missing_paths,
        "seeds": list(seed_results),
    }


def finalize_run_lock(out_root: Path) -> dict[str, Any]:
    path = out_root / "locks/RUN_LOCK.json"
    lock = json.loads(path.read_text())
    outputs = {}
    for artifact in sorted(out_root.rglob("*")):
        if not artifact.is_file() or artifact == path or artifact.name == "artifacts.sha256":
            continue
        outputs[str(artifact.relative_to(out_root))] = {"sha256": sha256_file(artifact)}
    lock.update({
        "status": "R4_COMPLETE",
        "output_hashes": outputs,
        "determinism": {
            "torch_use_deterministic_algorithms": True,
            "cudnn_benchmark": False,
            "cudnn_deterministic": True,
        },
        "hash_policy": "RUN_LOCK hashes all non-self primary outputs; artifacts.sha256 hashes RUN_LOCK",
    })
    atomic_json(path, lock)
    return lock


def write_artifact_manifest(out_root: Path) -> int:
    artifact_paths = sorted(
        p for p in out_root.rglob("*")
        if p.is_file() and not p.name.endswith(".tmp") and p.name != "artifacts.sha256"
    )
    lines = [f"{sha256_file(path)}  {path.relative_to(out_root)}" for path in artifact_paths]
    target = out_root / "artifacts.sha256"
    tmp = target.with_suffix(".sha256.tmp")
    tmp.write_text("\n".join(lines) + "\n")
    os.replace(tmp, target)
    return len(artifact_paths)


def smoke(out_root: Path) -> dict[str, Any]:
    """Tiny train+calibration-only synthetic smoke; never touches R0 files."""
    import torch

    random.seed(SEEDS[0]); np.random.seed(SEEDS[0] & 0xFFFFFFFF); torch.manual_seed(SEEDS[0])
    labels = ["absent", "present", "uninformative"] * 8
    model, priors = make_head(labels)
    x = torch.randn(len(labels), FEATURE_DIM)
    q_logits, state_logits = model(x)
    bracket = TeacherBracket("interval", date(2020, 1, 1), date(2021, 1, 1))
    dates = [date(2019 + i // 6, 1 + (i % 6) * 2, 1) for i in range(len(labels))]
    il, fl, _ql, _sl, posterior, bounds = anchor_loss(
        q_logits, state_logits, labels, dates, bracket, class_weights(labels)
    )
    loss = il + 0.25 * fl
    loss.backward()
    optimizer = torch.optim.AdamW(model.parameters(), lr=3e-4, weight_decay=1e-4)
    optimizer.step()
    checkpoint = out_root / f"seeds/{SEEDS[0]}/checkpoints/epoch_001.safetensors"
    checkpoint_sha = save_checkpoint(
        model, checkpoint, {"seed": SEEDS[0], "epoch": 1, "selection_tuple": [1.0, 1.0, 1]}
    )
    restored, _ = make_head(labels)
    load_checkpoint(restored, checkpoint)
    for key, value in model.state_dict().items():
        if not torch.equal(value.cpu(), restored.state_dict()[key].cpu()):
            raise AssertionError(f"checkpoint round-trip differs at {key}")
    raw = np.linspace(-2, 2, 24)
    targets = np.array([0] * 12 + [1] * 12)
    geometry = ["A24|15-40"] * 12 + ["A48|40-100"] * 12
    era = ["2019-2020", "2021-2022", "2023-2025"] * 8
    qbin = ["low"] * 8 + ["medium"] * 8 + ["high"] * 8
    calibrator = fit_hierarchical_platt(raw, targets, geometry, era, qbin)
    calibrated = apply_hierarchical_platt(calibrator, raw, geometry, era, qbin)
    anchors = pd.DataFrame({
        "max_posterior": [0.99] * 400, "map_in_k": [True] * 400,
        "interval_eligible": [True] * 400,
        "source_area_m2": [20.0] * 200 + [60.0] * 200,
    })
    threshold, grid = select_threshold(anchors)
    if threshold is None:
        raise AssertionError("smoke threshold stage did not select a qualifying threshold")
    for role in ("cal_es", "cal_fit", "cal_select"):
        frame_stub = pd.DataFrame([{c: None for c in FRAME_PRED_COLUMNS}])
        frame_stub.loc[0, ["seed", "anchor_id", "capture_date", "split_role"]] = [
            SEEDS[0], f"smoke-{role}", "2020-01-01", role,
        ]
        anchor_stub = pd.DataFrame([{c: None for c in ANCHOR_PRED_COLUMNS}])
        anchor_stub.loc[0, ["seed", "anchor_id", "split_role"]] = [
            SEEDS[0], f"smoke-{role}", role,
        ]
        assert_artifact_schema(frame_stub, FRAME_PRED_COLUMNS, name=f"{role} frame predictions")
        assert_artifact_schema(anchor_stub, ANCHOR_PRED_COLUMNS, name=f"{role} anchor predictions")
        frame_path = out_root / f"seeds/{SEEDS[0]}/predictions_{role}.parquet"
        anchor_path = out_root / f"seeds/{SEEDS[0]}/anchors_{role}.parquet"
        atomic_parquet(frame_path, frame_stub)
        atomic_parquet(anchor_path, anchor_stub)
        assert_artifact_schema(pd.read_parquet(frame_path), FRAME_PRED_COLUMNS, name=f"{role} frame round-trip")
        assert_artifact_schema(pd.read_parquet(anchor_path), ANCHOR_PRED_COLUMNS, name=f"{role} anchor round-trip")
    payload = {
        "status": "SMOKE_PASS", "loss": float(loss.detach()), "posterior_sum": float(posterior.detach().sum()),
        "n_bounds": len(bounds), "head_params": EXPECTED_PARAMS, "threshold": threshold,
        "prior_counts": priors, "test_rows_read": 0,
        "calibration_finite": bool(np.isfinite(calibrated).all()),
        "checkpoint_sha256": checkpoint_sha,
    }
    atomic_json(out_root / "metrics/R4_SMOKE.json", payload)
    atomic_json(out_root / "seeds/2026072001/calibrators/quality.json", calibrator)
    atomic_json(out_root / "seeds/2026072001/calibrators/state.json", calibrator)
    atomic_json(out_root / "seeds/2026072001/thresholds.json", {"selected_threshold": threshold, "grid": grid})
    return payload


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    root = Path.home() / "zasolar_data/geid_temporal/run3_native_line_2026-07"
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)
    prep = sub.add_parser("prepare", help="verify frozen inputs and materialize pre-run locks")
    prep.add_argument("--r0-root", type=Path, default=root / "r0_manifest_v1")
    prep.add_argument("--cache-root", type=Path, default=root / "r3_feature_cache_v1")
    prep.add_argument("--out-root", type=Path, default=root / ATTEMPT_ID)
    mat = sub.add_parser("materialize-locks", help="write train-only normalizer and cohort prior")
    mat.add_argument("--r0-root", type=Path, default=root / "r0_manifest_v1")
    mat.add_argument("--cache-root", type=Path, default=root / "r3_feature_cache_v1")
    mat.add_argument("--out-root", type=Path, default=root / ATTEMPT_ID)
    mat.add_argument(
        "--shortgap-sidecar", type=Path,
        help="SHA-lock and apply r4_shortgap_adjudication_v1/frame_label_overrides.parquet",
    )
    train = sub.add_parser("train", help="run the three frozen R4 training seeds")
    train.add_argument("--r0-root", type=Path, default=root / "r0_manifest_v1")
    train.add_argument("--cache-root", type=Path, default=root / "r3_feature_cache_v1")
    train.add_argument("--out-root", type=Path, default=root / ATTEMPT_ID)
    train.add_argument("--device", default="cuda")
    sm = sub.add_parser("smoke", help="run a synthetic train/calibration-only contract smoke")
    sm.add_argument("--out-root", type=Path, required=True)
    return p.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.command == "prepare":
        result = prepare_contract(args.r0_root, args.cache_root, args.out_root)
    elif args.command == "materialize-locks":
        result = materialize_train_locks(
            args.r0_root, args.cache_root, args.out_root,
            shortgap_sidecar=args.shortgap_sidecar,
        )
    elif args.command == "train":
        commit, dirty = _git_state()
        run_lock = json.loads((args.out_root / "locks/RUN_LOCK.json").read_text())
        if dirty or run_lock.get("git_dirty") or run_lock.get("repository_commit") != commit:
            raise RuntimeError(
                "formal R4 training requires a freshly prepared clean worktree at the recorded commit"
            )
        sequences, cohort_prior, weights = load_r4_sequences(
            args.r0_root, args.cache_root, args.out_root
        )
        equivalence = verify_manifest_decoder_equivalence(
            sequences, cohort_prior, args.out_root
        )
        run_lock["manifest_decoder_equivalence"] = {
            **equivalence,
            "sha256": sha256_file(args.out_root / "locks/decoder_equivalence_200.json"),
        }
        atomic_json(args.out_root / "locks/RUN_LOCK.json", run_lock)
        seed_results = []
        for seed in SEEDS:
            selected = train_one_seed(
                sequences, cohort_prior, weights, seed, args.out_root, device=args.device
            )
            calibration = calibrate_one_seed(
                sequences, cohort_prior, seed, selected, args.out_root, device=args.device
            )
            seed_results.append({"training": selected, "calibration": calibration})
        finalized = finalize_r4_artifacts(args.out_root, seed_results)
        result = evaluate_r4_health(args.out_root, seed_results)
        result["finalized"] = finalized
        atomic_json(args.out_root / "metrics/R4_HEALTH.json", result)
        result = evaluate_r4_health(args.out_root, seed_results)
        result["finalized"] = finalized
        atomic_json(args.out_root / "metrics/R4_HEALTH.json", result)
        finalize_run_lock(args.out_root)
        write_artifact_manifest(args.out_root)
    else:
        result = smoke(args.out_root)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
