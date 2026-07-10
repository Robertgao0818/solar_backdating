#!/usr/bin/env python3
"""Path-B sequence-level distillation pilot (pre-registered 2026-07-10).

The locked design lives in
``docs/dinov3_scorer/DATA-sequence-head-pilot-prereg-2026-07-10.md``.
This harness consumes cached frozen frame embeddings, trains a temporal head
and a parameter-matched independent-frame control for seeds 0/1/2, then emits
paired anchor-cluster bootstrap metrics and machine-checkable verdict inputs.
"""

from __future__ import annotations

import argparse
import csv
import json
import itertools
import math
import os
import random
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

os.environ.setdefault("CUBLAS_WORKSPACE_CONFIG", ":4096:8")

import torch
from torch import nn
from torch.nn import functional as F

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROJECT_SRC = PROJECT_ROOT / "src"
for path in (PROJECT_SRC, PROJECT_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from scripts.temporal.train_dinov3_head import _val_anchors  # noqa: E402

KIND_ORDER = ("INTERVAL", "AP_BOUND", "UNDATED")
KIND_INDEX = {name: index for index, name in enumerate(KIND_ORDER)}
MODEL_DIM = 192
TEMPORAL_LAYERS = 2
TEMPORAL_HEADS = 4
TEMPORAL_FF_DIM = 384
MODEL_DROPOUT = 0.1
FEATURES_DEFAULT = Path.home() / "zasolar_data/slice5/out/nomarker_bilinear518_k6/features.npz"
LABEL_MANIFEST_DEFAULT = (
    Path.home() / "zasolar_data/geid_temporal/dinov3_distill_20260705/label_manifest.csv"
)
REP_ROWS_DEFAULT = Path.home() / (
    "zasolar_data/geid_temporal/fidelity_gate_20260710/gate2_diag/dinov2_floor/unit_rows.jsonl"
)
OUT_DEFAULT = Path.home() / "zasolar_data/geid_temporal/pilot_sequence_head_20260710_corrected"
SEEDS = (0, 1, 2)
LEARNING_RATE = 1e-3
WEIGHT_DECAY = 1e-4
BATCH_SIZE = 32
MAX_EPOCHS = 100
PATIENCE = 12
BOOTSTRAP_DRAWS = 10_000
BOOTSTRAP_SEED = 20_260_710


@dataclass(frozen=True)
class SequenceSample:
    anchor_id: str
    split: str
    terminal_status: str
    features: np.ndarray
    dates: tuple[str, ...]
    versions: tuple[str, ...]
    labels: tuple[str, ...]
    teacher_key: str
    target_kind: str
    lower_index: int | None
    upper_index: int | None
    transition_mask: np.ndarray


@dataclass(frozen=True)
class DecodedPrediction:
    kind: str
    lower_index: int | None
    upper_index: int | None


@dataclass(frozen=True)
class TeacherIntervalTarget:
    kind: str
    lower_date: str | None
    upper_date: str | None
    teacher_key: str


def build_sequences(
    raw: Mapping[str, np.ndarray],
    decoded_targets: Mapping[str, TeacherIntervalTarget],
) -> list[SequenceSample]:
    """Build date-deduplicated sequences aligned to explicit decoded targets."""
    required = {
        "features",
        "anchor_id",
        "capture_date",
        "version",
        "label_3class",
        "split",
        "terminal_status",
    }
    missing = sorted(required - set(raw))
    if missing:
        raise ValueError(f"feature cache missing required arrays: {', '.join(missing)}")

    features = np.asarray(raw["features"], dtype=np.float32)
    anchors = np.asarray(raw["anchor_id"]).astype(str)
    dates = np.asarray(raw["capture_date"]).astype(str)
    versions = np.asarray(raw["version"]).astype(str)
    labels = np.asarray(raw["label_3class"]).astype(str)
    splits = np.asarray(raw["split"]).astype(str)
    statuses = np.asarray(raw["terminal_status"]).astype(str)
    n = len(anchors)
    if features.ndim != 2 or any(
        len(array) != n for array in (dates, versions, labels, splits, statuses)
    ):
        raise ValueError("feature-cache arrays are not row-aligned")

    row_ids: dict[str, list[int]] = {}
    for index, anchor in enumerate(anchors):
        row_ids.setdefault(str(anchor), []).append(index)

    samples: list[SequenceSample] = []
    for anchor in sorted(row_ids):
        sorted_indices = sorted(
            row_ids[anchor],
            key=lambda index: (str(dates[index]), str(versions[index]), index),
        )
        indices: list[int] = []
        seen_dates: set[str] = set()
        for index in sorted_indices:
            capture_date = str(dates[index])
            if capture_date in seen_dates:
                continue
            seen_dates.add(capture_date)
            indices.append(index)
        anchor_splits = {str(splits[index]) for index in indices}
        anchor_statuses = {str(statuses[index]) for index in indices}
        if len(anchor_splits) != 1 or len(anchor_statuses) != 1:
            raise ValueError(f"anchor {anchor} has inconsistent split/status rows")

        ordered_labels = tuple(str(labels[index]) for index in indices)
        ordered_dates = tuple(str(dates[index]) for index in indices)
        if anchor not in decoded_targets:
            raise ValueError(f"missing decoded teacher target for anchor {anchor}")
        target = decoded_targets[anchor]
        if target.kind not in KIND_INDEX:
            raise ValueError(f"anchor {anchor} has unsupported target kind {target.kind!r}")
        lower_index: int | None = None
        upper_index: int | None = None
        if target.lower_date is not None:
            if target.lower_date not in ordered_dates:
                raise ValueError(
                    f"decoded lower date {target.lower_date} missing from anchor {anchor} sequence"
                )
            lower_index = ordered_dates.index(target.lower_date)
        if target.upper_date is not None:
            if target.upper_date not in ordered_dates:
                raise ValueError(
                    f"decoded upper date {target.upper_date} missing from anchor {anchor} sequence"
                )
            upper_index = ordered_dates.index(target.upper_date)
        if target.kind == "INTERVAL" and (
            lower_index is None or upper_index is None or lower_index >= upper_index
        ):
            raise ValueError(f"anchor {anchor} has invalid decoded interval indices")
        if target.kind == "AP_BOUND" and upper_index is None:
            raise ValueError(f"anchor {anchor} AP_BOUND target lacks an upper date")

        transition_mask = np.zeros(len(indices), dtype=bool)
        if target.kind == "INTERVAL":
            assert lower_index is not None and upper_index is not None
            lo = max(0, lower_index - 1)
            hi = min(len(indices), upper_index + 2)
            transition_mask[lo:hi] = True

        samples.append(
            SequenceSample(
                anchor_id=anchor,
                split=next(iter(anchor_splits)),
                terminal_status=next(iter(anchor_statuses)),
                features=features[indices].copy(),
                dates=ordered_dates,
                versions=tuple(str(versions[index]) for index in indices),
                labels=ordered_labels,
                teacher_key=target.teacher_key,
                target_kind=target.kind,
                lower_index=lower_index,
                upper_index=upper_index,
                transition_mask=transition_mask,
            )
        )
    return samples


def decode_teacher_targets(
    raw: Mapping[str, np.ndarray],
    manifest_path: Path = LABEL_MANIFEST_DEFAULT,
) -> tuple[dict[str, TeacherIntervalTarget], dict[str, Any]]:
    """Decode cached-frame observations with the adopted Phase-0 teacher."""
    anchors = np.asarray(raw["anchor_id"]).astype(str)
    dates = np.asarray(raw["capture_date"]).astype(str)
    versions = np.asarray(raw["version"]).astype(str)
    feature_keys = set(zip(anchors, dates, versions))
    manifest_rows: dict[tuple[str, str, str], dict[str, str]] = {}
    with Path(manifest_path).open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            key = (
                str(row.get("anchor_id", "")),
                str(row.get("capture_date", "")),
                str(row.get("version", "")),
            )
            if key in feature_keys:
                if key in manifest_rows and manifest_rows[key] != row:
                    raise ValueError(f"conflicting label-manifest rows for {key}")
                manifest_rows[key] = dict(row)
    missing = feature_keys - set(manifest_rows)
    if missing:
        raise ValueError(
            f"label manifest missing {len(missing)} cached feature rows; "
            f"sample={sorted(missing)[:3]}"
        )

    from scripts.validation.estimator_endtoend_decode import (  # noqa: PLC0415
        ADOPTED_DECODER_EPOCH_GAP_DAYS,
        ADOPTED_ESTIMATOR,
        DEFAULT_COHORT_PRIOR_JSON,
        DEFAULT_EMISSIONS_JSON,
        _load_emissions,
    )
    from scripts.validation.fidelity_gate import (  # noqa: PLC0415
        build_decoder,
        posterior_to_agree_key,
    )
    from solar_backdating.estimators import (  # noqa: PLC0415
        ClampContext,
        VintageObservation,
    )
    from solar_backdating.estimators.survival import (  # noqa: PLC0415
        cohort_prior_from_json,
    )

    emissions = _load_emissions(DEFAULT_EMISSIONS_JSON)
    cohort_prior = cohort_prior_from_json(
        json.loads(Path(DEFAULT_COHORT_PRIOR_JSON).read_text(encoding="utf-8"))
    )
    estimator, config = build_decoder(
        estimator=ADOPTED_ESTIMATOR,
        emissions=emissions,
        cohort_prior=cohort_prior,
        decoder_epoch_gap_days=ADOPTED_DECODER_EPOCH_GAP_DAYS,
    )

    by_anchor: dict[str, list[tuple[str, str, int]]] = defaultdict(list)
    for row_index, (anchor, capture_date, version) in enumerate(zip(anchors, dates, versions)):
        by_anchor[str(anchor)].append((str(capture_date), str(version), row_index))

    targets: dict[str, TeacherIntervalTarget] = {}
    for anchor in sorted(by_anchor):
        observations: list[VintageObservation] = []
        seen_dates: set[str] = set()
        for capture_date, version, row_index in sorted(by_anchor[anchor]):
            if capture_date in seen_dates:
                continue
            seen_dates.add(capture_date)
            row = manifest_rows[(anchor, capture_date, version)]
            raw_pv = str(row.get("pv_present", "")).strip().lower()
            if raw_pv in {"1", "true"}:
                pv_present = "1"
            elif raw_pv in {"0", "false"}:
                pv_present = "0"
            else:
                pv_present = ""
            raw_confidence = str(row.get("confidence", "")).strip()
            observations.append(
                VintageObservation(
                    capture_date=date.fromisoformat(capture_date),
                    pv_present=pv_present,
                    confidence=float(raw_confidence) if raw_confidence else None,
                    quality_flag=str(row.get("quality_flag") or "usable"),
                    source_row=row_index,
                )
            )
        # No Vexcel clamp here: clamp-only dates are not frame indices. Gate-2
        # applies its external clamp after the student interval is produced.
        teacher_key = posterior_to_agree_key(estimator(observations, ClampContext(), config))
        if teacher_key == "UNDATED":
            target = TeacherIntervalTarget("UNDATED", None, None, teacher_key)
        elif teacher_key.startswith("AP_BOUND<=|"):
            target = TeacherIntervalTarget(
                "AP_BOUND", None, teacher_key.split("|", 1)[1], teacher_key
            )
        elif teacher_key.startswith("INTERVAL|"):
            _kind, lower_date, upper_date = teacher_key.split("|")
            target = TeacherIntervalTarget("INTERVAL", lower_date, upper_date, teacher_key)
        else:
            raise ValueError(f"unsupported decoded teacher key {teacher_key!r}")
        targets[anchor] = target

    kind_counts: dict[str, int] = defaultdict(int)
    for target in targets.values():
        kind_counts[target.kind] += 1
    return targets, {
        "source": str(manifest_path),
        "joined_feature_rows": len(manifest_rows),
        "decoder": ADOPTED_ESTIMATOR,
        "decoder_epoch_gap_days": ADOPTED_DECODER_EPOCH_GAP_DAYS,
        "emissions": str(DEFAULT_EMISSIONS_JSON),
        "cohort_prior": str(DEFAULT_COHORT_PRIOR_JSON),
        "clamp": None,
        "clamp_reason": (
            "boundary-index target must use cached frame dates; gate applies external clamp later"
        ),
        "target_kind_counts": dict(sorted(kind_counts.items())),
    }


def rep_agreement_by_status(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Pool independent decoded Gemini-key pairs into clipped status weights."""
    by_unit: dict[tuple[str, str], dict[int, str]] = defaultdict(dict)
    for row in rows:
        if not bool(row.get("decoded")):
            continue
        sf = str(row.get("sf", ""))
        status = str(row.get("stratum", ""))
        rep = int(row.get("rep", 0))
        key = str(row.get("teacher_key", ""))
        if not sf or not status or rep <= 0 or not key:
            continue
        by_unit[(sf, status)][rep] = key

    counts: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    global_equal = 0
    global_pairs = 0
    for (_sf, status), rep_keys in by_unit.items():
        for key_a, key_b in itertools.combinations(rep_keys.values(), 2):
            equal = int(key_a == key_b)
            counts[status][0] += equal
            counts[status][1] += 1
            global_equal += equal
            global_pairs += 1
    if global_pairs == 0:
        raise ValueError("rep-agreement artifact contains no decoded rep pairs")

    by_status: dict[str, dict[str, float | int]] = {}
    for status in sorted(counts):
        n_equal, n_pairs = counts[status]
        agreement = n_equal / n_pairs
        by_status[status] = {
            "n_equal": n_equal,
            "n_pairs": n_pairs,
            "agreement": agreement,
            "weight": min(1.0, max(0.25, agreement)),
        }
    global_agreement = global_equal / global_pairs
    return {
        "by_status": by_status,
        "global": {
            "n_equal": global_equal,
            "n_pairs": global_pairs,
            "agreement": global_agreement,
            "weight": min(1.0, max(0.25, global_agreement)),
        },
    }


def _time_encoding(ordinal_days: torch.Tensor, dim: int) -> torch.Tensor:
    if dim % 2:
        raise ValueError("time-encoding dimension must be even")
    years = ordinal_days / 365.25
    frequency = torch.exp(
        torch.arange(0, dim, 2, device=ordinal_days.device, dtype=ordinal_days.dtype)
        * (-math.log(10_000.0) / dim)
    )
    angles = years.unsqueeze(-1) * frequency
    out = torch.zeros(
        *ordinal_days.shape, dim, device=ordinal_days.device, dtype=ordinal_days.dtype
    )
    out[..., 0::2] = torch.sin(angles)
    out[..., 1::2] = torch.cos(angles)
    return out


class _IntervalHeadBase(nn.Module):
    def __init__(self, state_dim: int) -> None:
        super().__init__()
        self.kind_head = nn.Linear(state_dim, len(KIND_ORDER))
        self.lower_head = nn.Linear(state_dim, 1)
        self.upper_head = nn.Linear(state_dim, 1)

    def _outputs(self, states: torch.Tensor, mask: torch.Tensor) -> dict[str, torch.Tensor]:
        float_mask = mask.unsqueeze(-1).to(states.dtype)
        pooled = (states * float_mask).sum(dim=1) / float_mask.sum(dim=1).clamp_min(1.0)
        invalid = ~mask
        lower = self.lower_head(states).squeeze(-1).masked_fill(invalid, -1e9)
        upper = self.upper_head(states).squeeze(-1).masked_fill(invalid, -1e9)
        return {
            "kind_logits": self.kind_head(pooled),
            "lower_logits": lower,
            "upper_logits": upper,
        }


class TemporalIntervalHead(_IntervalHeadBase):
    """Small temporal transformer over frozen per-frame embeddings."""

    def __init__(self, embed_dim: int) -> None:
        super().__init__(MODEL_DIM)
        self.input_projection = nn.Linear(embed_dim, MODEL_DIM)
        layer = nn.TransformerEncoderLayer(
            d_model=MODEL_DIM,
            nhead=TEMPORAL_HEADS,
            dim_feedforward=TEMPORAL_FF_DIM,
            dropout=MODEL_DROPOUT,
            activation="gelu",
            batch_first=True,
            norm_first=True,
        )
        self.encoder = nn.TransformerEncoder(
            layer, num_layers=TEMPORAL_LAYERS, enable_nested_tensor=False
        )

    def forward(
        self,
        features: torch.Tensor,
        ordinal_days: torch.Tensor,
        mask: torch.Tensor,
    ) -> dict[str, torch.Tensor]:
        states = self.input_projection(features) + _time_encoding(ordinal_days, MODEL_DIM)
        states = self.encoder(states, src_key_padding_mask=~mask)
        return self._outputs(states, mask)


class IndependentFrameIntervalHead(_IntervalHeadBase):
    """Parameter-matched control whose frame scores have no cross-frame interaction."""

    def __init__(self, embed_dim: int, hidden_width: int) -> None:
        super().__init__(MODEL_DIM)
        self.frame_mlp = nn.Sequential(
            nn.Linear(embed_dim, hidden_width),
            nn.GELU(),
            nn.Linear(hidden_width, MODEL_DIM),
            nn.GELU(),
        )

    def forward(
        self,
        features: torch.Tensor,
        ordinal_days: torch.Tensor,
        mask: torch.Tensor,
    ) -> dict[str, torch.Tensor]:
        states = self.frame_mlp(features) + _time_encoding(ordinal_days, MODEL_DIM)
        return self._outputs(states, mask)


def trainable_parameter_count(model: nn.Module) -> int:
    return sum(parameter.numel() for parameter in model.parameters() if parameter.requires_grad)


def _control_parameter_count(embed_dim: int, hidden_width: int) -> int:
    frame_mlp = embed_dim * hidden_width + hidden_width
    frame_mlp += hidden_width * MODEL_DIM + MODEL_DIM
    output_heads = MODEL_DIM * 5 + 5
    return frame_mlp + output_heads


def build_model_pair(embed_dim: int) -> tuple[nn.Module, nn.Module, dict[str, Any]]:
    """Construct locked arms and choose the exact closest control width."""
    temporal = TemporalIntervalHead(embed_dim)
    temporal_params = trainable_parameter_count(temporal)
    widths = range(64, 2049)
    hidden_width = min(
        widths,
        key=lambda width: (
            abs(_control_parameter_count(embed_dim, width) - temporal_params),
            width,
        ),
    )
    control = IndependentFrameIntervalHead(embed_dim, hidden_width)
    control_params = trainable_parameter_count(control)
    ratio = temporal_params / control_params
    if not 0.90 <= ratio <= 1.10:
        raise ValueError(
            f"parameter match failed: temporal={temporal_params}, control={control_params}, "
            f"ratio={ratio:.4f}"
        )
    return (
        temporal,
        control,
        {
            "temporal_params": temporal_params,
            "control_params": control_params,
            "parameter_ratio": ratio,
            "control_hidden_width": hidden_width,
        },
    )


def decode_logits(
    *,
    kind_logits: np.ndarray,
    lower_logits: np.ndarray,
    upper_logits: np.ndarray,
    length: int,
) -> DecodedPrediction:
    """Decode one model output under the shared valid-interval constraint."""
    if length <= 0:
        raise ValueError("cannot decode an empty sequence")
    kind = KIND_ORDER[int(np.asarray(kind_logits).argmax())]
    lower = np.asarray(lower_logits, dtype=float)[:length]
    upper = np.asarray(upper_logits, dtype=float)[:length]
    if kind == "UNDATED":
        return DecodedPrediction(kind="UNDATED", lower_index=None, upper_index=None)
    if kind == "AP_BOUND" or length == 1:
        return DecodedPrediction(
            kind="AP_BOUND",
            lower_index=None,
            upper_index=int(upper.argmax()),
        )

    best: tuple[float, int, int] | None = None
    for lower_index in range(length - 1):
        for upper_index in range(lower_index + 1, length):
            candidate = (
                float(lower[lower_index] + upper[upper_index]),
                -lower_index,
                -upper_index,
            )
            if best is None or candidate > best:
                best = candidate
    assert best is not None
    return DecodedPrediction(
        kind="INTERVAL",
        lower_index=-best[1],
        upper_index=-best[2],
    )


def prediction_frame_labels(
    prediction: DecodedPrediction,
    length: int,
) -> tuple[str, ...]:
    """Map a decoded sequence target back to monitored frame decisions."""
    if prediction.kind == "UNDATED":
        return tuple("unusable" for _ in range(length))
    assert prediction.upper_index is not None
    if prediction.kind == "AP_BOUND":
        return tuple(
            "present" if index >= prediction.upper_index else "unusable" for index in range(length)
        )
    assert prediction.lower_index is not None
    return tuple(
        "absent"
        if index <= prediction.lower_index
        else "present"
        if index >= prediction.upper_index
        else "unusable"
        for index in range(length)
    )


def _prediction_matches(sample: SequenceSample, prediction: DecodedPrediction) -> bool:
    return (
        prediction.kind == sample.target_kind
        and prediction.lower_index == sample.lower_index
        and prediction.upper_index == sample.upper_index
    )


_CONTRIBUTION_FIELDS = (
    "n_predictions",
    "interval_correct",
    "transition_fp",
    "transition_absent",
    "transition_fn",
    "transition_present",
    "transition_present_to_unusable",
    "overall_decided",
    "overall_agree",
    "overall_rows",
    "transition_decided",
    "transition_agree",
    "transition_rows",
)


def prediction_contributions(
    samples: Sequence[SequenceSample],
    predictions_by_seed: Mapping[int, Mapping[str, DecodedPrediction]],
) -> list[dict[str, Any]]:
    """Return additive per-anchor/seed contributions for metrics and bootstrap."""
    contributions: list[dict[str, Any]] = []
    for seed in sorted(predictions_by_seed):
        predictions = predictions_by_seed[seed]
        for sample in samples:
            if sample.anchor_id not in predictions:
                raise ValueError(f"missing prediction for seed={seed} anchor={sample.anchor_id}")
            prediction = predictions[sample.anchor_id]
            student_labels = prediction_frame_labels(prediction, len(sample.labels))
            row = {field: 0 for field in _CONTRIBUTION_FIELDS}
            row.update({"anchor_id": sample.anchor_id, "seed": int(seed)})
            row["n_predictions"] = 1
            row["interval_correct"] = int(_prediction_matches(sample, prediction))
            for index, (teacher, student) in enumerate(zip(sample.labels, student_labels)):
                in_transition = bool(sample.transition_mask[index])
                row["overall_rows"] += 1
                if student in ("present", "absent"):
                    row["overall_decided"] += 1
                    row["overall_agree"] += int(student == teacher)
                if not in_transition:
                    continue
                row["transition_rows"] += 1
                if student in ("present", "absent"):
                    row["transition_decided"] += 1
                    row["transition_agree"] += int(student == teacher)
                if teacher == "absent":
                    row["transition_absent"] += 1
                    row["transition_fp"] += int(student == "present")
                elif teacher == "present":
                    row["transition_present"] += 1
                    row["transition_fn"] += int(student == "absent")
                    row["transition_present_to_unusable"] += int(student == "unusable")
            contributions.append(row)
    return contributions


def _safe_rate(numerator: float, denominator: float) -> float | None:
    return numerator / denominator if denominator > 0 else None


def metrics_from_contributions(contributions: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    totals = {
        field: sum(float(row[field]) for row in contributions) for field in _CONTRIBUTION_FIELDS
    }
    return {
        "n_anchors": len({str(row["anchor_id"]) for row in contributions}),
        "n_seeds": len({int(row["seed"]) for row in contributions}),
        "n_predictions": int(totals["n_predictions"]),
        "interval_agreement": _safe_rate(totals["interval_correct"], totals["n_predictions"]),
        "transition_fp_rate": _safe_rate(totals["transition_fp"], totals["transition_absent"]),
        "transition_fn_rate": _safe_rate(totals["transition_fn"], totals["transition_present"]),
        "present_to_unusable_rate": _safe_rate(
            totals["transition_present_to_unusable"], totals["transition_present"]
        ),
        "overall_decided_agreement": _safe_rate(totals["overall_agree"], totals["overall_decided"]),
        "transition_decided_agreement": _safe_rate(
            totals["transition_agree"], totals["transition_decided"]
        ),
        "overall_coverage": _safe_rate(totals["overall_decided"], totals["overall_rows"]),
        "transition_coverage": _safe_rate(totals["transition_decided"], totals["transition_rows"]),
        "counts": {key: int(value) for key, value in totals.items()},
    }


def arm_point_metrics(
    samples: Sequence[SequenceSample],
    predictions_by_seed: Mapping[int, Mapping[str, DecodedPrediction]],
) -> dict[str, Any]:
    metrics = metrics_from_contributions(prediction_contributions(samples, predictions_by_seed))
    metrics["confusion"] = confusion_tables(samples, predictions_by_seed)
    return metrics


def confusion_tables(
    samples: Sequence[SequenceSample],
    predictions_by_seed: Mapping[int, Mapping[str, DecodedPrediction]],
) -> dict[str, dict[str, int]]:
    overall: Counter[str] = Counter()
    transition: Counter[str] = Counter()
    for seed in sorted(predictions_by_seed):
        for sample in samples:
            prediction = predictions_by_seed[seed][sample.anchor_id]
            student_labels = prediction_frame_labels(prediction, len(sample.labels))
            for index, (teacher, student) in enumerate(zip(sample.labels, student_labels)):
                key = f"{teacher}|{student}"
                overall[key] += 1
                if bool(sample.transition_mask[index]):
                    transition[key] += 1
    return {
        "overall": dict(sorted(overall.items())),
        "transition": dict(sorted(transition.items())),
    }


def _relative_reduction_pct(arm_a_rate: float, arm_b_rate: float) -> float:
    if arm_b_rate <= 0.0:
        return 0.0 if arm_a_rate <= 0.0 else -100.0
    return 100.0 * (arm_b_rate - arm_a_rate) / arm_b_rate


def _metrics_from_total_array(total: np.ndarray) -> dict[str, float]:
    values = dict(zip(_CONTRIBUTION_FIELDS, total.tolist()))

    def rate(numerator: str, denominator: str) -> float:
        denom = float(values[denominator])
        return float(values[numerator]) / denom if denom > 0 else 0.0

    return {
        "interval_agreement": rate("interval_correct", "n_predictions"),
        "transition_fp_rate": rate("transition_fp", "transition_absent"),
        "transition_fn_rate": rate("transition_fn", "transition_present"),
        "present_to_unusable_rate": rate("transition_present_to_unusable", "transition_present"),
        "overall_decided_agreement": rate("overall_agree", "overall_decided"),
        "transition_decided_agreement": rate("transition_agree", "transition_decided"),
    }


def compare_arms(
    samples: Sequence[SequenceSample],
    predictions_a: Mapping[int, Mapping[str, DecodedPrediction]],
    predictions_b: Mapping[int, Mapping[str, DecodedPrediction]],
    *,
    bootstrap_draws: int = 10_000,
    bootstrap_seed: int = 20_260_710,
) -> dict[str, Any]:
    """Compare arms with a paired bootstrap over anchor clusters."""
    contributions_a = prediction_contributions(samples, predictions_a)
    contributions_b = prediction_contributions(samples, predictions_b)
    keyed_a = {(str(row["anchor_id"]), int(row["seed"])): row for row in contributions_a}
    keyed_b = {(str(row["anchor_id"]), int(row["seed"])): row for row in contributions_b}
    if set(keyed_a) != set(keyed_b):
        raise ValueError("arm predictions do not cover identical anchor/seed pairs")

    anchors = sorted({anchor for anchor, _seed in keyed_a})
    cluster_a = np.zeros((len(anchors), len(_CONTRIBUTION_FIELDS)), dtype=np.float64)
    cluster_b = np.zeros_like(cluster_a)
    anchor_index = {anchor: index for index, anchor in enumerate(anchors)}
    field_index = {field: index for index, field in enumerate(_CONTRIBUTION_FIELDS)}
    for key, row_a in keyed_a.items():
        row_b = keyed_b[key]
        index = anchor_index[key[0]]
        for field, column in field_index.items():
            cluster_a[index, column] += float(row_a[field])
            cluster_b[index, column] += float(row_b[field])

    arm_a = metrics_from_contributions(contributions_a)
    arm_b = metrics_from_contributions(contributions_b)
    arm_a["confusion"] = confusion_tables(samples, predictions_a)
    arm_b["confusion"] = confusion_tables(samples, predictions_b)
    point = {
        "interval_agreement_delta": arm_a["interval_agreement"] - arm_b["interval_agreement"],
        "transition_fp_reduction_pct": _relative_reduction_pct(
            arm_a["transition_fp_rate"], arm_b["transition_fp_rate"]
        ),
        "transition_fn_reduction_pct": _relative_reduction_pct(
            arm_a["transition_fn_rate"], arm_b["transition_fn_rate"]
        ),
    }

    tracked = {
        "interval_agreement_delta": [],
        "transition_fp_reduction_pct": [],
        "transition_fn_reduction_pct": [],
        "arm_a_interval_agreement": [],
        "arm_b_interval_agreement": [],
        "arm_a_transition_fp_rate": [],
        "arm_b_transition_fp_rate": [],
        "arm_a_transition_fn_rate": [],
        "arm_b_transition_fn_rate": [],
        "arm_a_present_to_unusable_rate": [],
        "arm_b_present_to_unusable_rate": [],
        "arm_a_overall_decided_agreement": [],
        "arm_b_overall_decided_agreement": [],
        "arm_a_transition_decided_agreement": [],
        "arm_b_transition_decided_agreement": [],
    }
    rng = np.random.default_rng(bootstrap_seed)
    for _ in range(bootstrap_draws):
        selected = rng.integers(0, len(anchors), size=len(anchors))
        metrics_a = _metrics_from_total_array(cluster_a[selected].sum(axis=0))
        metrics_b = _metrics_from_total_array(cluster_b[selected].sum(axis=0))
        tracked["interval_agreement_delta"].append(
            metrics_a["interval_agreement"] - metrics_b["interval_agreement"]
        )
        tracked["transition_fp_reduction_pct"].append(
            _relative_reduction_pct(
                metrics_a["transition_fp_rate"], metrics_b["transition_fp_rate"]
            )
        )
        tracked["transition_fn_reduction_pct"].append(
            _relative_reduction_pct(
                metrics_a["transition_fn_rate"], metrics_b["transition_fn_rate"]
            )
        )
        for arm_name, metrics in (("arm_a", metrics_a), ("arm_b", metrics_b)):
            tracked[f"{arm_name}_interval_agreement"].append(metrics["interval_agreement"])
            tracked[f"{arm_name}_transition_fp_rate"].append(metrics["transition_fp_rate"])
            tracked[f"{arm_name}_transition_fn_rate"].append(metrics["transition_fn_rate"])
            tracked[f"{arm_name}_present_to_unusable_rate"].append(
                metrics["present_to_unusable_rate"]
            )
            tracked[f"{arm_name}_overall_decided_agreement"].append(
                metrics["overall_decided_agreement"]
            )
            tracked[f"{arm_name}_transition_decided_agreement"].append(
                metrics["transition_decided_agreement"]
            )

    ci95 = {
        name: [float(value) for value in np.percentile(values, [2.5, 97.5])]
        for name, values in tracked.items()
    }
    return {
        "arm_a": arm_a,
        "arm_b": arm_b,
        "point": point,
        "ci95": ci95,
        "bootstrap": {
            "method": "paired anchor-cluster percentile bootstrap",
            "draws": bootstrap_draws,
            "seed": bootstrap_seed,
            "n_anchor_clusters": len(anchors),
        },
    }


def _date_ordinal(value: str) -> int:
    text = str(value)
    if len(text) == 4:
        text = f"{text}-01-01"
    return date.fromisoformat(text).toordinal()


def collate_sequences(
    samples: Sequence[SequenceSample],
    sample_weights: Mapping[str, float],
) -> dict[str, Any]:
    """Pad samples into model tensors without exposing teacher frame labels."""
    if not samples:
        raise ValueError("cannot collate an empty sequence batch")
    batch_size = len(samples)
    max_length = max(len(sample.dates) for sample in samples)
    embed_dim = int(samples[0].features.shape[1])
    features = torch.zeros(batch_size, max_length, embed_dim, dtype=torch.float32)
    ordinal_days = torch.zeros(batch_size, max_length, dtype=torch.float32)
    mask = torch.zeros(batch_size, max_length, dtype=torch.bool)
    kind_target = torch.empty(batch_size, dtype=torch.long)
    lower_target = torch.full((batch_size,), -100, dtype=torch.long)
    upper_target = torch.full((batch_size,), -100, dtype=torch.long)
    weights = torch.empty(batch_size, dtype=torch.float32)
    for row, sample in enumerate(samples):
        length = len(sample.dates)
        if sample.features.shape != (length, embed_dim):
            raise ValueError(f"anchor {sample.anchor_id} has inconsistent feature shape")
        features[row, :length] = torch.from_numpy(sample.features)
        ordinal_days[row, :length] = torch.tensor(
            [_date_ordinal(value) for value in sample.dates], dtype=torch.float32
        )
        mask[row, :length] = True
        kind_target[row] = KIND_INDEX[sample.target_kind]
        if sample.lower_index is not None:
            lower_target[row] = sample.lower_index
        if sample.upper_index is not None:
            upper_target[row] = sample.upper_index
        if sample.anchor_id not in sample_weights:
            raise ValueError(f"missing loss weight for anchor {sample.anchor_id}")
        weights[row] = float(sample_weights[sample.anchor_id])
    return {
        "anchor_id": [sample.anchor_id for sample in samples],
        "lengths": [len(sample.dates) for sample in samples],
        "features": features,
        "ordinal_days": ordinal_days,
        "mask": mask,
        "kind_target": kind_target,
        "lower_target": lower_target,
        "upper_target": upper_target,
        "sample_weight": weights,
    }


def normalized_sample_weights(
    samples: Sequence[SequenceSample],
    rep_agreement: Mapping[str, Any],
) -> dict[str, float]:
    """Map status reliability to anchors and normalize train weights to mean one."""
    by_status = rep_agreement["by_status"]
    fallback = float(rep_agreement["global"]["weight"])
    raw = {
        sample.anchor_id: float(by_status.get(sample.terminal_status, {}).get("weight", fallback))
        for sample in samples
    }
    train_values = [raw[sample.anchor_id] for sample in samples if sample.split == "train"]
    if not train_values:
        raise ValueError("no train samples available for weight normalization")
    scale = float(np.mean(train_values))
    if scale <= 0:
        raise ValueError("non-positive mean training reliability weight")
    return {anchor: value / scale for anchor, value in raw.items()}


def sequence_loss(output: Mapping[str, torch.Tensor], batch: Mapping[str, Any]) -> torch.Tensor:
    """Weighted mean of available kind/lower/upper sequence-level CE terms."""
    kind = F.cross_entropy(output["kind_logits"], batch["kind_target"], reduction="none")
    total = kind
    terms = torch.ones_like(kind)
    lower_target = batch["lower_target"]
    upper_target = batch["upper_target"]
    lower_valid = lower_target != -100
    upper_valid = upper_target != -100
    if bool(lower_valid.any()):
        total = total + F.cross_entropy(
            output["lower_logits"], lower_target, ignore_index=-100, reduction="none"
        )
        terms = terms + lower_valid.to(terms.dtype)
    if bool(upper_valid.any()):
        total = total + F.cross_entropy(
            output["upper_logits"], upper_target, ignore_index=-100, reduction="none"
        )
        terms = terms + upper_valid.to(terms.dtype)
    per_sample = total / terms
    weights = batch["sample_weight"]
    return (per_sample * weights).sum() / weights.sum().clamp_min(1e-12)


def _to_device(batch: Mapping[str, Any], device: torch.device) -> dict[str, Any]:
    return {
        key: value.to(device) if isinstance(value, torch.Tensor) else value
        for key, value in batch.items()
    }


def _batched(samples: Sequence[SequenceSample], batch_size: int) -> list[list[SequenceSample]]:
    return [
        list(samples[start : start + batch_size]) for start in range(0, len(samples), batch_size)
    ]


def predict_model(
    model: nn.Module,
    samples: Sequence[SequenceSample],
    sample_weights: Mapping[str, float],
    *,
    device: torch.device,
    batch_size: int = BATCH_SIZE,
) -> dict[str, DecodedPrediction]:
    model.eval()
    predictions: dict[str, DecodedPrediction] = {}
    with torch.no_grad():
        for sample_batch in _batched(samples, batch_size):
            batch = _to_device(collate_sequences(sample_batch, sample_weights), device)
            output = model(batch["features"], batch["ordinal_days"], batch["mask"])
            kind = output["kind_logits"].detach().cpu().numpy()
            lower = output["lower_logits"].detach().cpu().numpy()
            upper = output["upper_logits"].detach().cpu().numpy()
            for row, sample in enumerate(sample_batch):
                predictions[sample.anchor_id] = decode_logits(
                    kind_logits=kind[row],
                    lower_logits=lower[row],
                    upper_logits=upper[row],
                    length=len(sample.dates),
                )
    return predictions


def _evaluate_validation(
    model: nn.Module,
    samples: Sequence[SequenceSample],
    sample_weights: Mapping[str, float],
    *,
    device: torch.device,
) -> tuple[float, float]:
    model.eval()
    weighted_loss = 0.0
    weight_sum = 0.0
    with torch.no_grad():
        for sample_batch in _batched(samples, BATCH_SIZE):
            batch = _to_device(collate_sequences(sample_batch, sample_weights), device)
            output = model(batch["features"], batch["ordinal_days"], batch["mask"])
            batch_weight = float(batch["sample_weight"].sum().item())
            weighted_loss += float(sequence_loss(output, batch).item()) * batch_weight
            weight_sum += batch_weight
    predictions = predict_model(model, samples, sample_weights, device=device)
    correct = 0.0
    total = 0.0
    for sample in samples:
        weight = float(sample_weights[sample.anchor_id])
        total += weight
        correct += weight * int(_prediction_matches(sample, predictions[sample.anchor_id]))
    return correct / total, weighted_loss / weight_sum


def _set_seed(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)
    torch.use_deterministic_algorithms(True, warn_only=False)


def train_model(
    model: nn.Module,
    train_samples: Sequence[SequenceSample],
    sample_weights: Mapping[str, float],
    *,
    seed: int,
    device: torch.device,
    out_path: Path,
    model_name: str,
    max_epochs: int = MAX_EPOCHS,
    patience: int = PATIENCE,
) -> dict[str, Any]:
    """Train one locked arm and persist the best validation checkpoint."""
    _set_seed(seed)
    train_anchors = sorted(sample.anchor_id for sample in train_samples)
    val_ids = set(_val_anchors(train_anchors))
    fit_samples = [sample for sample in train_samples if sample.anchor_id not in val_ids]
    val_samples = [sample for sample in train_samples if sample.anchor_id in val_ids]
    if not fit_samples or not val_samples:
        raise ValueError("deterministic validation carve-out produced an empty split")

    model.to(device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=LEARNING_RATE, weight_decay=WEIGHT_DECAY)
    rng = np.random.default_rng(seed)
    best_exact = -1.0
    best_loss = float("inf")
    best_state: dict[str, torch.Tensor] | None = None
    epochs_without_improvement = 0
    history: list[dict[str, float | int]] = []
    for epoch in range(1, max_epochs + 1):
        model.train()
        order = rng.permutation(len(fit_samples))
        epoch_loss = 0.0
        epoch_weight = 0.0
        for start in range(0, len(order), BATCH_SIZE):
            sample_batch = [fit_samples[int(index)] for index in order[start : start + BATCH_SIZE]]
            batch = _to_device(collate_sequences(sample_batch, sample_weights), device)
            optimizer.zero_grad(set_to_none=True)
            output = model(batch["features"], batch["ordinal_days"], batch["mask"])
            loss = sequence_loss(output, batch)
            loss.backward()
            optimizer.step()
            batch_weight = float(batch["sample_weight"].sum().item())
            epoch_loss += float(loss.item()) * batch_weight
            epoch_weight += batch_weight

        val_exact, val_loss = _evaluate_validation(
            model, val_samples, sample_weights, device=device
        )
        history.append(
            {
                "epoch": epoch,
                "train_loss": epoch_loss / epoch_weight,
                "val_interval_exact": val_exact,
                "val_loss": val_loss,
            }
        )
        improved = val_exact > best_exact + 1e-12 or (
            abs(val_exact - best_exact) <= 1e-12 and val_loss < best_loss - 1e-12
        )
        if improved:
            best_exact = val_exact
            best_loss = val_loss
            best_state = {
                key: value.detach().cpu().clone() for key, value in model.state_dict().items()
            }
            epochs_without_improvement = 0
        else:
            epochs_without_improvement += 1
            if epochs_without_improvement >= patience:
                break
    if best_state is None:
        raise RuntimeError("training completed without a validation checkpoint")
    model.load_state_dict(best_state)
    model.to(device)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    torch.save(
        {
            "state_dict": best_state,
            "model_name": model_name,
            "seed": seed,
            "embed_dim": int(train_samples[0].features.shape[1]),
            "config": {
                "learning_rate": LEARNING_RATE,
                "weight_decay": WEIGHT_DECAY,
                "batch_size": BATCH_SIZE,
                "max_epochs": max_epochs,
                "patience": patience,
            },
        },
        out_path,
    )
    log = {
        "model_name": model_name,
        "seed": seed,
        "n_fit_anchors": len(fit_samples),
        "n_val_anchors": len(val_samples),
        "epochs_run": len(history),
        "best_val_interval_exact": best_exact,
        "best_val_loss": best_loss,
        "history": history,
    }
    out_path.with_suffix(".train.json").write_text(json.dumps(log, indent=2), encoding="utf-8")
    return log


def _load_rep_rows(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def _prediction_json(
    sample_by_anchor: Mapping[str, SequenceSample],
    predictions: Mapping[str, DecodedPrediction],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for anchor in sorted(predictions):
        sample = sample_by_anchor[anchor]
        prediction = predictions[anchor]
        rows.append(
            {
                "anchor_id": anchor,
                "teacher_key": sample.teacher_key,
                "teacher_kind": sample.target_kind,
                "teacher_lower_index": sample.lower_index,
                "teacher_upper_index": sample.upper_index,
                "predicted_kind": prediction.kind,
                "predicted_lower_index": prediction.lower_index,
                "predicted_upper_index": prediction.upper_index,
                "exact": _prediction_matches(sample, prediction),
            }
        )
    return rows


def _confusion_markdown(
    title: str,
    confusion_a: Mapping[str, int],
    confusion_b: Mapping[str, int],
) -> list[str]:
    lines = [f"### {title}", "", "| teacher→prediction | Arm A | Arm B |", "|---|---:|---:|"]
    for key in sorted(set(confusion_a) | set(confusion_b)):
        lines.append(f"| `{key}` | {confusion_a.get(key, 0)} | {confusion_b.get(key, 0)} |")
    lines.append("")
    return lines


def _summary_markdown(result: Mapping[str, Any]) -> str:
    comparison = result["comparison"]
    a = comparison["arm_a"]
    b = comparison["arm_b"]
    p = comparison["point"]
    ci = comparison["ci95"]
    verdict = result["verdict"]
    return "\n".join(
        [
            "# Path-B sequence-head pilot",
            "",
            f"- verdict: **{verdict['overall']}**",
            f"- population: train={result['population']['train_anchors']} heldout={result['population']['heldout_anchors']}",
            f"- parameters: temporal={result['model']['temporal_params']} control={result['model']['control_params']} ratio={result['model']['parameter_ratio']:.4f}",
            f"- interval agreement: A={a['interval_agreement']:.4f}, B={b['interval_agreement']:.4f}, delta={p['interval_agreement_delta']:.4f} (95% CI {ci['interval_agreement_delta'][0]:.4f}..{ci['interval_agreement_delta'][1]:.4f})",
            f"- transition FP rate: A={a['transition_fp_rate']:.4f}, B={b['transition_fp_rate']:.4f}, reduction={p['transition_fp_reduction_pct']:.1f}% (95% CI {ci['transition_fp_reduction_pct'][0]:.1f}..{ci['transition_fp_reduction_pct'][1]:.1f})",
            f"- transition FN rate: A={a['transition_fn_rate']:.4f}, B={b['transition_fn_rate']:.4f}, reduction={p['transition_fn_reduction_pct']:.1f}% (95% CI {ci['transition_fn_reduction_pct'][0]:.1f}..{ci['transition_fn_reduction_pct'][1]:.1f})",
            f"- present->unusable: A={a['present_to_unusable_rate']:.4f} (95% CI {ci['arm_a_present_to_unusable_rate'][0]:.4f}..{ci['arm_a_present_to_unusable_rate'][1]:.4f}), B={b['present_to_unusable_rate']:.4f} (95% CI {ci['arm_b_present_to_unusable_rate'][0]:.4f}..{ci['arm_b_present_to_unusable_rate'][1]:.4f})",
            f"- overall decided agreement: A={a['overall_decided_agreement']:.4f} (95% CI {ci['arm_a_overall_decided_agreement'][0]:.4f}..{ci['arm_a_overall_decided_agreement'][1]:.4f}), B={b['overall_decided_agreement']:.4f} (95% CI {ci['arm_b_overall_decided_agreement'][0]:.4f}..{ci['arm_b_overall_decided_agreement'][1]:.4f})",
            f"- transition decided agreement: A={a['transition_decided_agreement']:.4f} (95% CI {ci['arm_a_transition_decided_agreement'][0]:.4f}..{ci['arm_a_transition_decided_agreement'][1]:.4f}), B={b['transition_decided_agreement']:.4f} (95% CI {ci['arm_b_transition_decided_agreement'][0]:.4f}..{ci['arm_b_transition_decided_agreement'][1]:.4f})",
            f"- coverage overall/transition: A={a['overall_coverage']:.4f}/{a['transition_coverage']:.4f}, B={b['overall_coverage']:.4f}/{b['transition_coverage']:.4f}",
            "",
            "## Confusion tables",
            "",
            *_confusion_markdown(
                "Overall",
                a["confusion"]["overall"],
                b["confusion"]["overall"],
            ),
            *_confusion_markdown(
                "Transition band",
                a["confusion"]["transition"],
                b["confusion"]["transition"],
            ),
            "",
            "## Machine verdict",
            "",
            *[f"- {line}" for line in verdict["sequence_head_r1_lines"]],
            *[f"- {line}" for line in verdict["frame_bar_lines"]],
            "",
        ]
    )


def run_pilot(
    *,
    features_path: Path = FEATURES_DEFAULT,
    label_manifest_path: Path = LABEL_MANIFEST_DEFAULT,
    rep_rows_path: Path = REP_ROWS_DEFAULT,
    out_dir: Path = OUT_DEFAULT,
    device_name: str = "cuda",
) -> dict[str, Any]:
    """Execute the locked path-B pilot and write all artifacts."""
    if device_name.startswith("cuda") and not torch.cuda.is_available():
        raise RuntimeError("CUDA requested for the locked pilot but is not available")
    device = torch.device(device_name)
    out_dir.mkdir(parents=True, exist_ok=True)
    loaded = np.load(features_path, allow_pickle=False)
    raw = {key: loaded[key] for key in loaded.files}
    decoded_targets, target_provenance = decode_teacher_targets(raw, label_manifest_path)
    samples = build_sequences(raw, decoded_targets)
    if not samples or samples[0].features.shape[1] != 384:
        raise ValueError("locked pilot requires the pooled 384-d feature cache")
    train_samples = [sample for sample in samples if sample.split == "train"]
    heldout_samples = [sample for sample in samples if sample.split == "heldout"]
    if not train_samples or not heldout_samples:
        raise ValueError("feature cache must contain both train and heldout anchors")

    rep_agreement = rep_agreement_by_status(_load_rep_rows(rep_rows_path))
    sample_weights = normalized_sample_weights(samples, rep_agreement)
    sample_by_anchor = {sample.anchor_id: sample for sample in samples}
    predictions_a: dict[int, dict[str, DecodedPrediction]] = {}
    predictions_b: dict[int, dict[str, DecodedPrediction]] = {}
    training_logs: dict[str, Any] = {"arm_a": {}, "arm_b": {}}
    model_meta: dict[str, Any] | None = None

    for seed in SEEDS:
        _set_seed(seed)
        temporal, control, current_meta = build_model_pair(384)
        if model_meta is None:
            model_meta = current_meta
        elif current_meta != model_meta:
            raise RuntimeError("parameter metadata changed across seeds")
        arm_a_path = out_dir / "arm_A_temporal" / f"seed_{seed}" / "head.pt"
        arm_b_path = out_dir / "arm_B_frame_control" / f"seed_{seed}" / "head.pt"
        training_logs["arm_a"][str(seed)] = train_model(
            temporal,
            train_samples,
            sample_weights,
            seed=seed,
            device=device,
            out_path=arm_a_path,
            model_name="temporal_transformer",
        )
        training_logs["arm_b"][str(seed)] = train_model(
            control,
            train_samples,
            sample_weights,
            seed=seed,
            device=device,
            out_path=arm_b_path,
            model_name="independent_frame_control",
        )
        predictions_a[seed] = predict_model(
            temporal, heldout_samples, sample_weights, device=device
        )
        predictions_b[seed] = predict_model(control, heldout_samples, sample_weights, device=device)
        (arm_a_path.parent / "heldout_predictions.json").write_text(
            json.dumps(_prediction_json(sample_by_anchor, predictions_a[seed]), indent=2),
            encoding="utf-8",
        )
        (arm_b_path.parent / "heldout_predictions.json").write_text(
            json.dumps(_prediction_json(sample_by_anchor, predictions_b[seed]), indent=2),
            encoding="utf-8",
        )

    assert model_meta is not None
    comparison = compare_arms(
        heldout_samples,
        predictions_a,
        predictions_b,
        bootstrap_draws=BOOTSTRAP_DRAWS,
        bootstrap_seed=BOOTSTRAP_SEED,
    )
    flat_metrics = {
        "interval_agreement_delta": comparison["point"]["interval_agreement_delta"],
        "interval_agreement_delta_ci_low": comparison["ci95"]["interval_agreement_delta"][0],
        "transition_fp_reduction_pct": comparison["point"]["transition_fp_reduction_pct"],
        "transition_fp_reduction_ci_low_pct": comparison["ci95"]["transition_fp_reduction_pct"][0],
        "transition_fn_reduction_pct": comparison["point"]["transition_fn_reduction_pct"],
        "transition_fn_reduction_ci_low_pct": comparison["ci95"]["transition_fn_reduction_pct"][0],
        "overall_decided_agreement_a": comparison["arm_a"]["overall_decided_agreement"],
        "overall_decided_agreement_b": comparison["arm_b"]["overall_decided_agreement"],
        "present_to_unusable_rate_a": comparison["arm_a"]["present_to_unusable_rate"],
        "present_to_unusable_rate_b": comparison["arm_b"]["present_to_unusable_rate"],
        "temporal_params": model_meta["temporal_params"],
        "control_params": model_meta["control_params"],
        "n_seeds": len(SEEDS),
        "transition_decided_agreement": comparison["arm_a"]["transition_decided_agreement"],
        "overall_decided_agreement": comparison["arm_a"]["overall_decided_agreement"],
    }
    from scripts.validation.check_student_path_gate import (  # noqa: PLC0415
        check_frame_bar,
        check_sequence_head_r1,
    )

    sequence_ok, sequence_lines = check_sequence_head_r1(flat_metrics)
    frame_ok, frame_lines = check_frame_bar(flat_metrics)
    verdict = {
        "overall": "GO" if sequence_ok and frame_ok else "KILL",
        "sequence_head_r1_pass": sequence_ok,
        "frame_bar_pass": frame_ok,
        "sequence_head_r1_lines": sequence_lines,
        "frame_bar_lines": frame_lines,
        "gate2_rerun_licensed": bool(sequence_ok and frame_ok),
    }
    target_counts: dict[str, int] = defaultdict(int)
    for sample in samples:
        target_counts[sample.target_kind] += 1
    result = {
        "prereg": "docs/dinov3_scorer/DATA-sequence-head-pilot-prereg-2026-07-10.md",
        "features_path": str(features_path),
        "label_manifest_path": str(label_manifest_path),
        "rep_rows_path": str(rep_rows_path),
        "teacher_target_provenance": target_provenance,
        "device": str(device),
        "population": {
            "anchors": len(samples),
            "train_anchors": len(train_samples),
            "heldout_anchors": len(heldout_samples),
            "target_kind_counts": dict(sorted(target_counts.items())),
        },
        "rep_agreement": rep_agreement,
        "model": model_meta,
        "training": training_logs,
        "comparison": comparison,
        "flat_metrics": flat_metrics,
        "verdict": verdict,
    }
    (out_dir / "pilot_metrics.json").write_text(
        json.dumps(flat_metrics, indent=2), encoding="utf-8"
    )
    (out_dir / "pilot_result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    (out_dir / "summary.md").write_text(_summary_markdown(result), encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--features", type=Path, default=FEATURES_DEFAULT)
    parser.add_argument("--label-manifest", type=Path, default=LABEL_MANIFEST_DEFAULT)
    parser.add_argument("--rep-rows", type=Path, default=REP_ROWS_DEFAULT)
    parser.add_argument("--out", type=Path, default=OUT_DEFAULT)
    parser.add_argument("--device", default="cuda")
    args = parser.parse_args()
    result = run_pilot(
        features_path=args.features,
        label_manifest_path=args.label_manifest,
        rep_rows_path=args.rep_rows,
        out_dir=args.out,
        device_name=args.device,
    )
    print(json.dumps(result["verdict"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
