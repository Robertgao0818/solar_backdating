#!/usr/bin/env python3
"""R4 short-gap boundary adjudication contract and runner.

Implements the policy frozen in
``docs/dinov3_scorer/RUN-r4-shortgap-gemini-adjudication-prereg-2026-07-23.md``.
The module deliberately separates deterministic policy from image rendering and
HTTP transport: builders may inject the locked renderer, while every scientific
decision is made by the pure, exhaustively testable functions below.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

RUN_ID = "r4_shortgap_adjudication_v3"
GAP_DAYS = 45
EXPECTED_POPULATION = 2146
PANEL_SALT = "r4_shortgap_model_panel_v3@2026-07-24"
PANEL_ID_SHA256 = "80bcccd8e175acfdd8be23de4677f3e0d3d10416f3ea441b8d4f3ff5bd702ea1"
CANDIDATE_ALIASES = ("gemini-3.1-flash-lite", "gemini-3.5-flash-extra-low")
MAX_OUTPUT_TOKENS = 8192
CONFIDENCE_FLOOR = 0.80
DEFAULT_WORKERS = 30
DEFAULT_QPS = 6.0
TRANSPORT_REVISION = 4

_FILE_LOCK = threading.Lock()
_MODEL_LOCK = threading.Lock()

TARGET = ("yes", "no", "uncertain")
USABLE = ("yes", "no", "uncertain")
PV_STATE = ("present", "absent", "unclear")
TRANSITION = ("yes", "no", "uncertain")
FAILURE_MODES = {
    "registration_shift", "source_change", "cloud_shadow", "blur", "occlusion",
    "construction", "wrong_roof", "other",
}
GATE_FIELDS = (
    "same_physical_target", "earlier.usable", "earlier.pv_state",
    "later.usable", "later.pv_state", "transition_supported",
)
DEFINITE_CLASSES = {
    "CLEAN_INSTALL_TRANSITION", "ALREADY_PRESENT_BOTH", "ABSENT_BOTH",
}
PANEL_IDS = tuple("""
jhb_full382_unified_a_merge01_c0925_fpcut_2026_06_01_t00001760
jhb_full382_unified_a_merge01_c0925_fpcut_2026_06_01_t00001893
jhb_full382_unified_a_merge01_c0925_fpcut_2026_06_01_t00002141
jhb_full382_unified_a_merge01_c0925_fpcut_2026_06_01_t00004986
jhb_full382_unified_a_merge01_c0925_fpcut_2026_06_01_t00008447
jhb_full382_unified_a_merge01_c0925_fpcut_2026_06_01_t00009606
jhb_full382_unified_a_merge01_c0925_fpcut_2026_06_01_t00012322
jhb_full382_unified_a_merge01_c0925_fpcut_2026_06_01_t00012540
jhb_full382_unified_a_merge01_c0925_fpcut_2026_06_01_t00013761
jhb_full382_unified_a_merge01_c0925_fpcut_2026_06_01_t00014140
jhb_full382_unified_a_merge01_c0925_fpcut_2026_06_01_t00016822
jhb_full382_unified_a_merge01_c0925_fpcut_2026_06_01_t00016869
jhb_full382_unified_a_merge01_c0925_fpcut_2026_06_01_t00017574
jhb_full382_unified_a_merge01_c0925_fpcut_2026_06_01_t00018629
jhb_full382_unified_a_merge01_c0925_fpcut_2026_06_01_t00022066
jhb_full382_unified_a_merge01_c0925_fpcut_2026_06_01_t00022133
jhb_full382_unified_a_merge01_c0925_fpcut_2026_06_01_t00022777
jhb_full382_unified_a_merge01_c0925_fpcut_2026_06_01_t00023713
jhb_full382_unified_a_merge01_c0925_fpcut_2026_06_01_t00024779
jhb_full382_unified_a_merge01_c0925_fpcut_2026_06_01_t00027511
jhb_full382_unified_a_merge01_c0925_fpcut_2026_06_01_t00028153
jhb_full382_unified_a_merge01_c0925_fpcut_2026_06_01_t00028253
jhb_full382_unified_a_merge01_c0925_fpcut_2026_06_01_t00029552
jhb_full382_unified_a_merge01_c0925_fpcut_2026_06_01_t00030368
jhb_full382_unified_a_merge01_c0925_fpcut_2026_06_01_t00032426
jhb_full382_unified_a_merge01_c0925_fpcut_2026_06_01_t00033275
jhb_full382_unified_a_merge01_c0925_fpcut_2026_06_01_t00033549
jhb_full382_unified_a_merge01_c0925_fpcut_2026_06_01_t00034698
jhb_full382_unified_a_merge01_c0925_fpcut_2026_06_01_t00035051
jhb_full382_unified_a_merge01_c0925_fpcut_2026_06_01_t00035684
jhb_full382_unified_a_merge01_c0925_fpcut_2026_06_01_t00035946
jhb_full382_unified_a_merge01_c0925_fpcut_2026_06_01_t00036170
jhb_full382_unified_a_merge01_c0925_fpcut_2026_06_01_t00037113
jhb_full382_unified_a_merge01_c0925_fpcut_2026_06_01_t00037214
jhb_full382_unified_a_merge01_c0925_fpcut_2026_06_01_t00037235
jhb_full382_unified_a_merge01_c0925_fpcut_2026_06_01_t00037516
jhb_full382_unified_a_merge01_c0925_fpcut_2026_06_01_t00037743
jhb_full382_unified_a_merge01_c0925_fpcut_2026_06_01_t00038603
jhb_full382_unified_a_merge01_c0925_fpcut_2026_06_01_t00039693
jhb_full382_unified_a_merge01_c0925_fpcut_2026_06_01_t00039728
""".strip().splitlines())

RESPONSE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "same_physical_target", "earlier", "later", "transition_supported",
        "failure_modes", "confidence", "reason",
    ],
    "properties": {
        "same_physical_target": {"type": "string", "enum": list(TARGET)},
        "earlier": {
            "type": "object", "additionalProperties": False,
            "required": ["usable", "pv_state"],
            "properties": {
                "usable": {"type": "string", "enum": list(USABLE)},
                "pv_state": {"type": "string", "enum": list(PV_STATE)},
            },
        },
        "later": {
            "type": "object", "additionalProperties": False,
            "required": ["usable", "pv_state"],
            "properties": {
                "usable": {"type": "string", "enum": list(USABLE)},
                "pv_state": {"type": "string", "enum": list(PV_STATE)},
            },
        },
        "transition_supported": {"type": "string", "enum": list(TRANSITION)},
        "failure_modes": {
            "type": "array", "uniqueItems": True,
            "items": {"type": "string", "enum": sorted(FAILURE_MODES)},
        },
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "reason": {"type": "string", "minLength": 1},
    },
}

# Native Gemini responseSchema is a strict subset of JSON Schema and rejects
# ``uniqueItems``. Duplicate failure modes remain forbidden by
# ``validate_response`` after parsing, so this transport projection does not
# relax the scientific contract.
TRANSPORT_RESPONSE_SCHEMA = json.loads(json.dumps(RESPONSE_SCHEMA))
TRANSPORT_RESPONSE_SCHEMA["properties"]["failure_modes"].pop("uniqueItems")

LEGACY_PROMPT = """Review one rooftop target using the three images in this exact order:
1) boundary tight pair labelled EARLIER and LATER;
2) 96 m boundary context pair with the target ROI outlined;
3) chronological six-slot temporal strip (blank slots mean no source frame).
Frame IDs are neutral. First decide whether every view shows the same physical
roof despite sensor, GSD, colour, parallax, or registration changes. Then return
only the response-schema JSON. Judge visible facts; do not infer an installation
from the ordering alone."""

PROMPT = """Review one target.

Use this order:
1. Tight: EARLIER, LATER.
2. Context: EARLIER, LATER.
3. Strip: F01 to F06.

First confirm the same roof.
Allow sensor or registration shifts.
Judge endpoints separately.
Ignore order as transition evidence.

Use usable="yes" only if readable.
If usable is not "yes", set pv_state="unclear".
Use transition_supported="yes" only for the same target, two usable endpoints,
EARLIER absent, and LATER present.
Otherwise use "no" or "uncertain".

Return schema-valid JSON only.
"""
ENHANCED_PROMPT = PROMPT


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with _FILE_LOCK:
        tmp = path.with_suffix(path.suffix + f".{threading.get_ident()}.tmp")
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        os.replace(tmp, path)


def atomic_parquet(path: Path, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    frame.to_parquet(tmp, index=False)
    os.replace(tmp, path)


def append_jsonl(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with _FILE_LOCK:
        with path.open("a") as stream:
            stream.write(json.dumps(dict(payload), sort_keys=True) + "\n")
            stream.flush()
            os.fsync(stream.fileno())


def _exact_keys(value: Mapping[str, Any], expected: set[str], where: str) -> None:
    if set(value) != expected:
        raise ValueError(f"{where} fields {sorted(value)} != {sorted(expected)}")


def validate_response(value: Mapping[str, Any]) -> dict[str, Any]:
    """Strict validation, including all preregistered semantic contradictions."""
    if not isinstance(value, Mapping):
        raise ValueError("response must be an object")
    _exact_keys(
        value,
        {"same_physical_target", "earlier", "later", "transition_supported",
         "failure_modes", "confidence", "reason"},
        "response",
    )
    target, transition = value["same_physical_target"], value["transition_supported"]
    if target not in TARGET or transition not in TRANSITION:
        raise ValueError("out-of-vocabulary target/transition")
    endpoints = {}
    for name in ("earlier", "later"):
        endpoint = value[name]
        if not isinstance(endpoint, Mapping):
            raise ValueError(f"{name} must be an object")
        _exact_keys(endpoint, {"usable", "pv_state"}, name)
        if endpoint["usable"] not in USABLE or endpoint["pv_state"] not in PV_STATE:
            raise ValueError(f"out-of-vocabulary {name}")
        if endpoint["usable"] != "yes" and endpoint["pv_state"] != "unclear":
            raise ValueError(f"{name}: non-usable endpoint has definite PV state")
        endpoints[name] = dict(endpoint)
    modes = value["failure_modes"]
    if (
        not isinstance(modes, list) or len(modes) != len(set(modes))
        or any(mode not in FAILURE_MODES for mode in modes)
    ):
        raise ValueError("invalid failure_modes")
    confidence = value["confidence"]
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        raise ValueError("confidence must be numeric")
    if not 0 <= float(confidence) <= 1:
        raise ValueError("confidence outside [0,1]")
    if not isinstance(value["reason"], str) or not value["reason"].strip():
        raise ValueError("reason must be non-empty")
    clean_tuple = (
        target == "yes"
        and endpoints["earlier"] == {"usable": "yes", "pv_state": "absent"}
        and endpoints["later"] == {"usable": "yes", "pv_state": "present"}
    )
    if target == "no" and transition == "yes":
        raise ValueError("target=no contradicts transition=yes")
    if transition == "yes" and not clean_tuple:
        raise ValueError("transition=yes outside clean absent->present tuple")
    return {
        "same_physical_target": target, "earlier": endpoints["earlier"],
        "later": endpoints["later"], "transition_supported": transition,
        "failure_modes": list(modes), "confidence": float(confidence),
        "reason": value["reason"].strip(),
    }


def derive_class(value: Mapping[str, Any]) -> str:
    v = validate_response(value)
    if v["same_physical_target"] == "no":
        return "TARGET_MISMATCH_OR_SOURCE_SHIFT"
    if v["same_physical_target"] == "uncertain":
        return "AMBIGUOUS"
    e, l = v["earlier"], v["later"]
    if e["usable"] == "no" or l["usable"] == "no":
        return "ONE_OR_BOTH_UNUSABLE"
    if e["usable"] == "uncertain" or l["usable"] == "uncertain":
        return "AMBIGUOUS"
    states = (e["pv_state"], l["pv_state"])
    transition = v["transition_supported"]
    if states == ("absent", "present") and transition == "yes":
        return "CLEAN_INSTALL_TRANSITION"
    if states == ("present", "present") and transition == "no":
        return "ALREADY_PRESENT_BOTH"
    if states == ("absent", "absent") and transition == "no":
        return "ABSENT_BOTH"
    return "AMBIGUOUS"


def gate_tuple(value: Mapping[str, Any]) -> tuple[str, ...]:
    v = validate_response(value)
    return (
        v["same_physical_target"], v["earlier"]["usable"], v["earlier"]["pv_state"],
        v["later"]["usable"], v["later"]["pv_state"], v["transition_supported"],
    )


def needs_third_rep(primary: Sequence[Mapping[str, Any]]) -> bool:
    if len(primary) != 2:
        raise ValueError("exactly two primary reps required")
    valid = [
        validate_response({k: v for k, v in rep.items() if not k.startswith("_")})
        for rep in primary
    ]
    return bool(
        gate_tuple(valid[0]) != gate_tuple(valid[1])
        or derive_class(valid[0]) != derive_class(valid[1])
        or any(rep["confidence"] < CONFIDENCE_FLOOR for rep in valid)
        or any("uncertain" in gate_tuple(rep) or "unclear" in gate_tuple(rep) for rep in valid)
        or any(bool(rep.get("_schema_retry", False)) for rep in primary)
    )


@dataclass(frozen=True)
class Consensus:
    final_class: str
    gate_factors: tuple[str, ...] | None
    agreeing_reps: tuple[int, ...]
    high_confidence_reps: tuple[int, ...]
    earlier_override: str
    later_override: str


def _endpoint_override(usable: str, state: str, enough_confidence: bool) -> str:
    if usable != "yes" or state == "unclear" or not enough_confidence:
        return "uninformative"
    return state


def consensus(reps: Sequence[Mapping[str, Any]]) -> Consensus:
    if len(reps) not in (2, 3):
        raise ValueError("consensus requires two or three reps")
    valid = [
        validate_response({k: v for k, v in rep.items() if not k.startswith("_")})
        for rep in reps
    ]
    groups: dict[tuple[str, ...], list[int]] = {}
    for idx, rep in enumerate(valid, 1):
        groups.setdefault(gate_tuple(rep), []).append(idx)
    winners = [(key, idxs) for key, idxs in groups.items() if len(idxs) >= 2]
    if len(winners) != 1:
        return Consensus("AMBIGUOUS", None, (), (), "uninformative", "uninformative")
    factors, agreeing = winners[0]
    high = tuple(i for i in agreeing if valid[i - 1]["confidence"] >= CONFIDENCE_FLOOR)
    cls = derive_class(valid[agreeing[0] - 1])
    enough = len(high) >= 2
    if cls in DEFINITE_CLASSES and not enough:
        cls = "AMBIGUOUS"
    if cls == "CLEAN_INSTALL_TRANSITION":
        labels = ("absent", "present")
    elif cls == "ALREADY_PRESENT_BOTH":
        labels = ("present", "present")
    elif cls == "ABSENT_BOTH":
        labels = ("absent", "absent")
    elif cls == "ONE_OR_BOTH_UNUSABLE":
        labels = (
            _endpoint_override(factors[1], factors[2], enough),
            _endpoint_override(factors[3], factors[4], enough),
        )
    else:
        labels = ("uninformative", "uninformative")
    return Consensus(cls, factors, tuple(agreeing), high, labels[0], labels[1])


def auto_rule(
    earlier: Mapping[str, Any], later: Mapping[str, Any]
) -> tuple[str, str, str] | None:
    """Return (decision source, earlier override, later override)."""
    if earlier["src_tiff_sha256"] == later["src_tiff_sha256"]:
        return ("AUTO_IDENTICAL_SOURCE_CONFLICT", "uninformative", "uninformative")
    if earlier.get("render_sha256") == later.get("render_sha256") and earlier.get("render_sha256"):
        return ("AUTO_IDENTICAL_RENDER_CONFLICT", "uninformative", "uninformative")
    unusable = [
        bool(row.get("zero_valid_target_support"))
        or int(row.get("valid_target_pixels", 1)) == 0
        for row in (earlier, later)
    ]
    if any(unusable):
        return (
            "AUTO_CONTENT_UNUSABLE",
            "uninformative" if unusable[0] else "",
            "uninformative" if unusable[1] else "",
        )
    return None


def _parse_date(value: Any) -> date:
    return date.fromisoformat(str(value)[:10])


def _same_decoder_epoch(all_dates: Sequence[date], lower: date, upper: date) -> bool:
    current = 0
    epoch_by_date: dict[date, int] = {}
    previous: date | None = None
    for value in sorted(all_dates):
        if previous is not None and (value - previous).days > GAP_DAYS:
            current += 1
        epoch_by_date[value] = current
        previous = value
    return epoch_by_date[lower] == epoch_by_date[upper]


def derive_candidates(manifest: pd.DataFrame, *, expected_count: int = EXPECTED_POPULATION) -> pd.DataFrame:
    """Derive the population from R0 rows; never inspect an output directory."""
    required = {
        "anchor_id", "capture_date", "chip_index", "split", "scan_status",
        "label_v1", "src_tiff_sha256",
    }
    if missing := sorted(required - set(manifest.columns)):
        raise ValueError(f"manifest missing {missing}")
    source = manifest.loc[
        manifest["split"].isin(["train", "calibration"])
        & manifest["scan_status"].eq("done_appears")
    ].copy()
    candidates: list[dict[str, Any]] = []
    for anchor_id, rows in source.groupby("anchor_id", sort=True):
        rows = rows.assign(_date=rows["capture_date"].map(_parse_date)).sort_values(
            ["_date", "chip_index"]
        )
        absent = rows.loc[rows["label_v1"].eq("absent")]
        present = rows.loc[rows["label_v1"].eq("present")]
        if absent.empty or present.empty:
            continue
        upper = present["_date"].min()
        lower_rows = absent.loc[absent["_date"].lt(upper)]
        if lower_rows.empty:
            continue
        lower = lower_rows["_date"].max()
        # Empty K_i is a decoder-epoch property, not merely endpoint distance:
        # intervening frames can bridge two endpoints through <=45-day steps.
        if not _same_decoder_epoch(rows["_date"].tolist(), lower, upper):
            continue
        earlier = lower_rows.loc[lower_rows["_date"].eq(lower)].iloc[-1]
        later = present.loc[present["_date"].eq(upper)].iloc[0]
        before = rows.loc[rows["_date"].lt(lower)].tail(2)
        after = rows.loc[rows["_date"].gt(upper)].head(2)
        item: dict[str, Any] = {
            "anchor_id": str(anchor_id), "split": str(earlier["split"]),
            "gap_days": (upper - lower).days,
            "earlier_capture_date": lower.isoformat(), "later_capture_date": upper.isoformat(),
            "earlier_chip_index": int(earlier["chip_index"]),
            "later_chip_index": int(later["chip_index"]),
            "earlier_src_tiff_sha256": str(earlier["src_tiff_sha256"]),
            "later_src_tiff_sha256": str(later["src_tiff_sha256"]),
            "interval_loss_eligible": False,
            "crop_geometry": "r1_cropgeo_v1@2026-07-19",
            "backbone_hash": "vit_small_patch14_dinov2.lvd142m@sha256:cb91f5a740744d75",
            "pooling_version": "r3_pool_v1",
        }
        for col in ("chip_arm", "source_area_m2", "area_bin"):
            if col in rows:
                item[col] = earlier[col]
        for pos in range(2):
            row = before.iloc[pos] if pos < len(before) else None
            item[f"preceding_{pos + 1}_capture_date"] = None if row is None else row["_date"].isoformat()
            item[f"preceding_{pos + 1}_src_tiff_sha256"] = None if row is None else str(row["src_tiff_sha256"])
            item[f"preceding_{pos + 1}_chip_index"] = None if row is None else int(row["chip_index"])
            row = after.iloc[pos] if pos < len(after) else None
            item[f"following_{pos + 1}_capture_date"] = None if row is None else row["_date"].isoformat()
            item[f"following_{pos + 1}_src_tiff_sha256"] = None if row is None else str(row["src_tiff_sha256"])
            item[f"following_{pos + 1}_chip_index"] = None if row is None else int(row["chip_index"])
        for col in (
            "src_tiff_path", "version", "provider", "geometry_version",
            "chip_png_path", "chip_png_sha256", "prompt_config_hash", "model_id",
            "quality_flag", "confidence", "teacher_confidence",
        ):
            if col in rows:
                item[f"earlier_{col}"] = earlier[col]
                item[f"later_{col}"] = later[col]
        candidates.append(item)
    out = pd.DataFrame(candidates).sort_values("anchor_id").reset_index(drop=True)
    if len(out) != expected_count:
        raise ValueError(f"short-gap population {len(out)} != frozen {expected_count}")
    return out


def canonical_panel_id_sha(ids: Iterable[str]) -> str:
    payload = "".join(f"{anchor_id}\n" for anchor_id in sorted(ids)).encode()
    return sha256_bytes(payload)


def build_panel_manifest(candidates: pd.DataFrame) -> pd.DataFrame:
    panel = candidates.loc[candidates["anchor_id"].astype(str).isin(PANEL_IDS)].copy()
    if len(panel) != 40:
        raise ValueError(f"frozen panel resolved to {len(panel)}/40 candidate rows")
    panel["area_panel_bin"] = panel["source_area_m2"].map(
        lambda value: "<15" if float(value) < 15 else "15-40" if float(value) < 40 else ">=40"
    )
    panel["version_relation"] = [
        "same" if str(earlier) == str(later) else "cross"
        for earlier, later in zip(panel["earlier_version"], panel["later_version"])
    ]
    panel["selection_hash"] = panel["anchor_id"].astype(str).map(
        lambda anchor_id: sha256_bytes(PANEL_SALT.encode() + b"\0" + anchor_id.encode())
    )
    panel = panel.sort_values("anchor_id").reset_index(drop=True)
    verify_panel(panel)
    return panel


def verify_panel(panel: pd.DataFrame) -> None:
    ids = panel["anchor_id"].astype(str).tolist()
    if len(ids) != 40 or set(ids) != set(PANEL_IDS):
        raise ValueError("panel IDs differ from frozen Appendix A")
    if canonical_panel_id_sha(ids) != PANEL_ID_SHA256:
        raise ValueError("canonical panel ID SHA mismatch")
    expected = {
        "gap_days": {7: 10, 30: 10, 31: 10, 40: 10},
        "chip_arm": {"A24": 32, "A48": 8},
        "split": {"train": 30, "calibration": 10},
    }
    for col, counts in expected.items():
        if panel[col].value_counts().to_dict() != counts:
            raise ValueError(f"panel {col} quotas differ")
    if "version_relation" in panel and panel["version_relation"].value_counts().to_dict() != {"same": 20, "cross": 20}:
        raise ValueError("panel version-relation quotas differ")
    if "area_panel_bin" in panel and panel["area_panel_bin"].value_counts().to_dict() != {"<15": 16, "15-40": 16, ">=40": 8}:
        raise ValueError("panel area quotas differ")
    if "area_panel_bin" in panel:
        for gap, rows in panel.groupby("gap_days"):
            if rows["area_panel_bin"].value_counts().to_dict() != {"<15": 4, "15-40": 4, ">=40": 2}:
                raise ValueError(f"panel gap={gap} area quotas differ")
            expected_split = (
                {"<15": (3, 1), "15-40": (3, 1), ">=40": (1, 1)}
                if gap in (7, 31)
                else {"<15": (3, 1), "15-40": (3, 1), ">=40": (2, 0)}
            )
            for area, (n_train, n_cal) in expected_split.items():
                counts = rows.loc[rows["area_panel_bin"].eq(area), "split"].value_counts()
                if int(counts.get("train", 0)) != n_train or int(counts.get("calibration", 0)) != n_cal:
                    raise ValueError(f"panel gap={gap} area={area} split quotas differ")
    gap30 = panel.loc[panel["gap_days"].eq(30)]
    if "version_relation" in panel and not gap30["version_relation"].eq("same").all():
        raise ValueError("gap-30 panel rows must be same-version")


class ModelVersionDrift(RuntimeError):
    pass


def admit_exact_version(lock: dict[str, Any], arm: str, alias: str, returned: Any) -> str:
    """Establish once, then enforce the non-empty exact version for an arm."""
    version = str(returned or "").strip()
    if not version:
        raise ModelVersionDrift("MODEL_VERSION_DRIFT: missing returned exact version")
    with _MODEL_LOCK:
        arms = lock.setdefault("model_arms", {})
        current = arms.get(arm)
        if current is None:
            arms[arm] = {"requested_alias": alias, "exact_model_version": version}
        elif current != {"requested_alias": alias, "exact_model_version": version}:
            raise ModelVersionDrift(
                f"MODEL_VERSION_DRIFT: {arm} expected {current}, got {(alias, version)}"
            )
    return version


def select_model(
    arm_matches: Mapping[str, int], n_clear: int, arm_aliases: Mapping[str, str]
) -> dict[str, Any]:
    if n_clear < 30:
        raise ValueError("N_clear below frozen minimum 30")
    competent = {arm: hits / n_clear >= 0.90 for arm, hits in arm_matches.items()}
    eligible = [arm for arm, ok in competent.items() if ok]
    if not eligible:
        raise ValueError("neither model arm passed the competence gate")
    best = max(arm_matches[arm] for arm in eligible)
    tied = [arm for arm in eligible if arm_matches[arm] == best]
    winner = next(
        (arm for arm in tied if arm_aliases[arm] == "gemini-3.1-flash-lite"),
        sorted(tied)[0],
    )
    return {
        "n_clear": n_clear, "matches": dict(arm_matches), "competent": competent,
        "winning_arm": winner, "winning_alias": arm_aliases[winner],
    }


def build_human_reference(annotations: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Build the clear denominator; senior adjudication never enlarges it."""
    required = {
        "anchor_id", "annotator_id", "annotator_role", "human_visually_clear",
        *GATE_FIELDS,
    }
    if missing := sorted(required - set(annotations.columns)):
        raise ValueError(f"human annotations missing {missing}")
    primary = annotations.loc[annotations["annotator_role"].eq("primary")]
    clear_rows, disputed = [], []
    for anchor_id in PANEL_IDS:
        rows = primary.loc[primary["anchor_id"].astype(str).eq(anchor_id)]
        if len(rows) != 2 or rows["annotator_id"].nunique() != 2:
            raise ValueError(f"{anchor_id}: exactly two named primary annotations required")
        tuples = []
        for _, row in rows.iterrows():
            factors = tuple(str(row[field]) for field in GATE_FIELDS)
            validate_response({
                "same_physical_target": factors[0],
                "earlier": {"usable": factors[1], "pv_state": factors[2]},
                "later": {"usable": factors[3], "pv_state": factors[4]},
                "transition_supported": factors[5], "failure_modes": [],
                "confidence": 1.0, "reason": "human annotation",
            })
            if row["human_visually_clear"] == "yes":
                if factors[0] == "yes" and (
                    (factors[1] == "yes" and factors[2] == "unclear")
                    or (factors[3] == "yes" and factors[4] == "unclear")
                ):
                    raise ValueError(f"{anchor_id}: visually clear usable endpoint must have definite state")
            tuples.append(factors)
        is_clear = bool(rows["human_visually_clear"].eq("yes").all() and tuples[0] == tuples[1])
        if is_clear:
            clear_rows.append({"anchor_id": anchor_id, **dict(zip(GATE_FIELDS, tuples[0]))})
        else:
            disputed.append({"anchor_id": anchor_id, "reason": "not_both_clear_or_factor_disagreement"})
    reference = pd.DataFrame(clear_rows)
    disputes = pd.DataFrame(disputed)
    if len(reference) < 30:
        raise ValueError(f"N_clear={len(reference)} below frozen minimum 30")
    if len(disputes):
        senior = annotations.loc[
            annotations["annotator_role"].eq("senior")
            & annotations["anchor_id"].astype(str).isin(disputes["anchor_id"])
        ]
        if set(senior["anchor_id"].astype(str)) != set(disputes["anchor_id"].astype(str)):
            raise ValueError("every non-clear row requires named senior adjudication")
        if senior["annotator_id"].astype(str).str.strip().eq("").any():
            raise ValueError("senior annotator_id cannot be blank")
    return reference, disputes


MANUAL_FACTOR_CODES = {
    "AP": ("yes", "yes", "absent", "yes", "present", "yes"),
    "PP": ("yes", "yes", "present", "yes", "present", "no"),
    "AA": ("yes", "yes", "absent", "yes", "absent", "no"),
    "U_P": ("yes", "uncertain", "unclear", "yes", "present", "uncertain"),
    "UU": ("yes", "yes", "unclear", "yes", "unclear", "uncertain"),
    "TU": ("uncertain", "yes", "unclear", "yes", "unclear", "no"),
}


def materialize_manual_annotations(
    decisions: pd.DataFrame, *, reviewed_utc: str
) -> pd.DataFrame:
    """Expand two isolated visual passes and required senior-style reviews."""
    required = {
        "anchor_id", "pass1_code", "pass2_code", "clear1", "clear2",
        "senior_code", "review_note",
    }
    if missing := sorted(required - set(decisions.columns)):
        raise ValueError(f"manual review decisions missing {missing}")
    if set(decisions["anchor_id"].astype(str)) != set(PANEL_IDS) or len(decisions) != 40:
        raise ValueError("manual review decisions must contain the frozen 40 anchors exactly once")

    rows: list[dict[str, Any]] = []

    def add(
        anchor_id: str, code: str, clear: str, annotator_id: str,
        role: str, note: str,
    ) -> None:
        if code not in MANUAL_FACTOR_CODES:
            raise ValueError(f"{anchor_id}: unknown manual factor code {code!r}")
        if clear not in {"yes", "no"}:
            raise ValueError(f"{anchor_id}: invalid human_visually_clear={clear!r}")
        factors = MANUAL_FACTOR_CODES[code]
        payload = {
            "anchor_id": anchor_id,
            "annotator_id": annotator_id,
            "annotator_role": role,
            "human_visually_clear": clear,
            **dict(zip(GATE_FIELDS, factors)),
            "reason_code": code,
            "reason": note,
            "rubric_version": "r4_shortgap_six_factor_v1",
            "reviewed_utc": reviewed_utc,
            "reviewer_provenance": (
                "same Codex visual reviewer; two order-isolated passes, "
                "not two independent human people"
            ),
        }
        validate_response({
            "same_physical_target": factors[0],
            "earlier": {"usable": factors[1], "pv_state": factors[2]},
            "later": {"usable": factors[3], "pv_state": factors[4]},
            "transition_supported": factors[5],
            "failure_modes": [], "confidence": 1.0, "reason": note,
        })
        rows.append(payload)

    for row in decisions.sort_values("anchor_id").itertuples(index=False):
        anchor_id = str(row.anchor_id)
        note = str(row.review_note)
        add(anchor_id, str(row.pass1_code), str(row.clear1), "codex_visual_pass_1", "primary", note)
        add(anchor_id, str(row.pass2_code), str(row.clear2), "codex_visual_pass_2", "primary", note)
        senior_code = "" if pd.isna(row.senior_code) else str(row.senior_code).strip()
        if senior_code:
            add(
                anchor_id, senior_code, "no", "codex_visual_adjudication",
                "senior", note,
            )
    return pd.DataFrame(rows)


def panel_tuple_matches(
    reference: pd.DataFrame, consensus_frame: pd.DataFrame
) -> tuple[int, pd.DataFrame]:
    """Score exact six-factor matches on the frozen clear denominator."""
    model = consensus_frame.set_index(consensus_frame["anchor_id"].astype(str))
    rows = []
    for _, row in reference.iterrows():
        anchor_id = str(row["anchor_id"])
        if anchor_id not in model.index:
            raise ValueError(f"panel consensus missing clear anchor {anchor_id}")
        expected = tuple(str(row[field]) for field in GATE_FIELDS)
        raw_factors = model.loc[anchor_id, "gate_factors"]
        actual = (
            tuple(str(value) for value in raw_factors)
            if raw_factors is not None
            else ()
        )
        rows.append({
            "anchor_id": anchor_id, "expected_factors": expected,
            "model_factors": actual, "complete_tuple_match": actual == expected,
        })
    details = pd.DataFrame(rows)
    return int(details["complete_tuple_match"].sum()), details


def write_frame_overrides(
    candidates: pd.DataFrame, verdicts: pd.DataFrame, path: Path
) -> pd.DataFrame:
    """Materialize exactly two boundary rows per anchor, including retain-as-null."""
    merged = candidates.merge(verdicts, on="anchor_id", validate="one_to_one")
    rows = []
    for row in merged.itertuples(index=False):
        for endpoint in ("earlier", "later"):
            override = getattr(row, f"{endpoint}_override")
            rows.append({
                "anchor_id": row.anchor_id,
                "capture_date": getattr(row, f"{endpoint}_capture_date"),
                "endpoint": endpoint,
                "override_label": override or None,
                "decision_source": row.decision_source,
                "final_class": row.final_class,
                "interval_loss_eligible": False,
            })
    out = pd.DataFrame(rows).sort_values(["anchor_id", "capture_date"]).reset_index(drop=True)
    if out.duplicated(["anchor_id", "capture_date"]).any() or len(out) != 2 * len(candidates):
        raise ValueError("override sidecar is not exactly two unique boundary rows per anchor")
    atomic_parquet(path, out)
    return out


def validate_packet_record(record: Mapping[str, Any]) -> None:
    """Validate pre-request provenance and the fixed three-image order."""
    if list(record.get("image_order", [])) != [
        "boundary_tight", "boundary_context", "temporal_strip",
    ]:
        raise ValueError("review packet image order differs from frozen order")
    if any(token in str(record.get("prompt", "")).lower() for token in ("done_appears", "teacher confidence")):
        raise ValueError("prompt leaks frozen teacher metadata")
    for key in ("boundary_tight", "boundary_context", "temporal_strip"):
        path = Path(record[f"{key}_path"])
        if not path.is_file() or sha256_file(path) != record[f"{key}_sha256"]:
            raise ValueError(f"packet artifact integrity failed: {key}")


def _montage(paths: Sequence[Path | None], labels: Sequence[str], *, cell_size: tuple[int, int]) -> Any:
    from PIL import Image, ImageDraw

    width, height = cell_size
    canvas = Image.new("RGB", (width * len(paths), height + 28), "white")
    draw = ImageDraw.Draw(canvas)
    for idx, (path, label) in enumerate(zip(paths, labels)):
        if path is None:
            image = Image.new("RGB", cell_size, (225, 225, 225))
            blank = ImageDraw.Draw(image)
            blank.line((0, 0, width, height), fill=(150, 150, 150), width=3)
            blank.line((width, 0, 0, height), fill=(150, 150, 150), width=3)
        else:
            with Image.open(path) as opened:
                opened.load()
                image = opened.convert("RGB")
            image.thumbnail(cell_size)
            background = Image.new("RGB", cell_size, "white")
            background.paste(image, ((width - image.width) // 2, (height - image.height) // 2))
            image = background
        canvas.paste(image, (idx * width, 28))
        draw.text((idx * width + 6, 7), label, fill="black")
    return canvas


def compose_review_packet(
    *,
    anchor_id: str,
    tight_paths: Sequence[Path],
    context_paths: Sequence[Path],
    strip_paths: Sequence[Path | None],
    out_root: Path,
    provenance: Mapping[str, Any],
) -> dict[str, Any]:
    """Compose the exact three rendered review images and persist provenance."""
    if len(tight_paths) != 2 or len(context_paths) != 2 or len(strip_paths) != 6:
        raise ValueError("packet requires 2 tight, 2 context and exactly 6 strip slots")
    for path in [*tight_paths, *context_paths, *(p for p in strip_paths if p is not None)]:
        if not path.is_file():
            raise FileNotFoundError(path)
    packet_dir = out_root / "review_packets" / sha256_bytes(anchor_id.encode())[:2] / anchor_id
    packet_dir.mkdir(parents=True, exist_ok=True)
    images = {
        "boundary_tight": _montage(tight_paths, ["EARLIER", "LATER"], cell_size=(256, 256)),
        "boundary_context": _montage(context_paths, ["EARLIER", "LATER"], cell_size=(384, 384)),
        "temporal_strip": _montage(
            strip_paths, ["F01", "F02", "F03", "F04", "F05", "F06"],
            cell_size=(192, 192),
        ),
    }
    record: dict[str, Any] = {
        "anchor_id": anchor_id, "prompt": PROMPT,
        "image_order": ["boundary_tight", "boundary_context", "temporal_strip"],
        **dict(provenance),
    }
    for name, image in images.items():
        path = packet_dir / f"{name}.png"
        image.save(path, format="PNG", optimize=False)
        record[f"{name}_path"] = str(path)
        record[f"{name}_sha256"] = sha256_file(path)
        record[f"{name}_size"] = list(image.size)
    rendered_prompt = packet_dir / "prompt.txt"
    rendered_prompt.write_text(PROMPT + "\n")
    record["prompt_sha256"] = sha256_file(rendered_prompt)
    record["packet_sha256"] = sha256_bytes(
        b"\0".join(
            [
                PROMPT.encode(),
                *(record[f"{name}_sha256"].encode() for name in record["image_order"]),
            ]
        )
    )
    atomic_json(packet_dir / "packet.json", record)
    return record


def _geometry_json(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        str(key): (None if pd.isna(value) else value.item() if hasattr(value, "item") else value)
        for key, value in row.items()
    }


def _render_context_and_validity(
    source_path: Path,
    geometry: Mapping[str, Any],
    context_path: Path,
    mask_path: Path,
) -> dict[str, int | bool]:
    """Render the full source view with ROI outline and measure valid ROI support."""
    import numpy as np
    import rasterio
    from PIL import Image, ImageDraw

    # R0 TIFFs may carry their transform in the locked manifest TFW fields
    # rather than embedded GeoTIFF tags. R1 geometry already freezes the exact
    # source-window -> output-pixel mapping, so invert that mapping directly.
    roi_output_px = json.loads(str(geometry["roi_px"]))
    roi_source_px = [
        (
            float(geometry["left"]) + float(x) / float(geometry["scale"]),
            float(geometry["top"]) + float(y) / float(geometry["scale"]),
        )
        for x, y in roi_output_px
    ]
    with rasterio.open(source_path) as dataset:
        if dataset.count < 3 or dataset.width <= 0 or dataset.height <= 0:
            raise ValueError("source raster lacks three non-empty channels")
        target_image = Image.new("L", (dataset.width, dataset.height), 0)
        ImageDraw.Draw(target_image).polygon(roi_source_px, fill=255)
        target = np.asarray(target_image) > 0
        masks = dataset.read_masks(list(range(1, min(3, dataset.count) + 1)))
        valid = masks.min(axis=0) > 0
        target_pixels = int(target.sum())
        valid_pixels = int((target & valid).sum())
        bands = dataset.read(list(range(1, 4)))
        image_array = np.moveaxis(bands, 0, 2)
        if image_array.dtype != np.uint8:
            finite = image_array[np.isfinite(image_array)]
            hi = float(np.percentile(finite, 99)) if finite.size else 1.0
            image_array = np.clip(image_array * (255.0 / max(hi, 1.0)), 0, 255).astype(np.uint8)
        image = Image.fromarray(image_array, "RGB")
        draw = ImageDraw.Draw(image)
        draw.line(
            [*roi_source_px, roi_source_px[0]],
            fill=(255, 220, 0), width=max(2, image.width // 180),
        )
        image.thumbnail((384, 384))
        context_path.parent.mkdir(parents=True, exist_ok=True)
        image.save(context_path, format="PNG", optimize=False)
        mask = Image.fromarray((target & valid).astype(np.uint8) * 255, "L")
        mask_path.parent.mkdir(parents=True, exist_ok=True)
        mask.save(mask_path, format="PNG", optimize=False)
    return {
        "target_pixels": target_pixels,
        "valid_target_pixels": valid_pixels,
        "zero_valid_target_support": target_pixels == 0 or valid_pixels == 0,
    }


def materialize_review_packets(
    *,
    manifest: pd.DataFrame,
    candidates: pd.DataFrame,
    r1_root: Path,
    out_root: Path,
    workers: int = DEFAULT_WORKERS,
) -> tuple[list[dict[str, Any]], pd.DataFrame]:
    """Build every locked three-image packet before any model request."""
    geometry_root = r1_root / "crop_geometry_index"
    shard_prefixes: set[str] = set()
    for column in candidates.columns:
        if column.endswith("_src_tiff_sha256"):
            shard_prefixes.update(
                str(value)[:2] for value in candidates[column].dropna() if str(value)
            )
    geometry_parts = [
        pd.read_parquet(geometry_root / f"{prefix}.parquet")
        for prefix in sorted(shard_prefixes)
    ]
    geometries = pd.concat(geometry_parts, ignore_index=True)
    geometries = geometries.drop_duplicates("chip_sha", keep="last").set_index("chip_sha")
    rows = manifest.set_index(["anchor_id", "capture_date", "chip_index"], drop=False)

    def manifest_row(candidate: Mapping[str, Any], slot: str) -> Mapping[str, Any] | None:
        capture = candidate.get(f"{slot}_capture_date")
        chip_index = candidate.get(f"{slot}_chip_index")
        if capture is None or pd.isna(capture) or chip_index is None or pd.isna(chip_index):
            return None
        key = (str(candidate["anchor_id"]), str(capture)[:10], int(chip_index))
        value = rows.loc[key]
        if isinstance(value, pd.DataFrame):
            if len(value) != 1:
                raise ValueError(f"{key}: non-unique manifest frame")
            value = value.iloc[0]
        return value.to_dict()

    def one(candidate: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        anchor_id = str(candidate["anchor_id"])
        slots = ("preceding_1", "preceding_2", "earlier", "later", "following_1", "following_2")
        frame_rows = {slot: manifest_row(candidate, slot) for slot in slots}
        packet_dir = out_root / "review_packets" / sha256_bytes(anchor_id.encode())[:2] / anchor_id
        object_dir = packet_dir / "objects"
        geometry_dir = packet_dir / "geometry"
        source_objects, geometry_objects, render_objects = [], [], []
        crop_paths: dict[str, Path | None] = {}
        endpoint_stats: dict[str, dict[str, Any]] = {}
        context_paths: list[Path] = []
        for slot, row in frame_rows.items():
            if row is None:
                crop_paths[slot] = None
                continue
            source_path = Path(str(row["src_tiff_path"]))
            source_sha = str(row["src_tiff_sha256"])
            if source_sha not in geometries.index:
                raise ValueError(f"{anchor_id}/{slot}: missing frozen R1 geometry")
            geometry = _geometry_json(geometries.loc[source_sha].to_dict())
            geometry_path = geometry_dir / f"{slot}.json"
            atomic_json(geometry_path, geometry)
            crop_path = r1_root / str(geometry["png_relpath"])
            crop_paths[slot] = crop_path
            source_objects.append({"slot": slot, "path": str(source_path), "sha256": source_sha})
            geometry_objects.append({
                "slot": slot, "path": str(geometry_path), "sha256": sha256_file(geometry_path),
                "version": geometry["crop_geometry"],
            })
            render_objects.append({
                "slot": slot, "path": str(crop_path), "sha256": sha256_file(crop_path),
                "kind": "tight_marker_free",
            })
            if slot in {"earlier", "later"}:
                context_path = object_dir / f"{slot}_context.png"
                mask_path = object_dir / f"{slot}_validity_mask.png"
                stats = _render_context_and_validity(source_path, geometry, context_path, mask_path)
                endpoint_stats[slot] = {
                    "src_tiff_sha256": source_sha,
                    "render_sha256": sha256_file(crop_path),
                    "validity_mask_path": str(mask_path),
                    "validity_mask_sha256": sha256_file(mask_path),
                    "renderer_version": "r4_context_v1@2026-07-24",
                    **stats,
                }
                context_paths.append(context_path)
                render_objects.extend([
                    {"slot": slot, "path": str(context_path), "sha256": sha256_file(context_path), "kind": "context96"},
                    {"slot": slot, "path": str(mask_path), "sha256": sha256_file(mask_path), "kind": "validity_mask"},
                ])
        provenance = {
            "routing_salt": sha256_bytes(f"{PANEL_SALT}\0{anchor_id}".encode()),
            "source_objects": source_objects,
            "geometry_objects": geometry_objects,
            "render_objects": render_objects,
            "earlier_source_path": next(x["path"] for x in source_objects if x["slot"] == "earlier"),
            "earlier_source_sha256": endpoint_stats["earlier"]["src_tiff_sha256"],
            "later_source_path": next(x["path"] for x in source_objects if x["slot"] == "later"),
            "later_source_sha256": endpoint_stats["later"]["src_tiff_sha256"],
            "geometry_path": next(x["path"] for x in geometry_objects if x["slot"] == "earlier"),
            "geometry_sha256": next(x["sha256"] for x in geometry_objects if x["slot"] == "earlier"),
            "endpoint_validity": endpoint_stats,
        }
        packet = compose_review_packet(
            anchor_id=anchor_id,
            tight_paths=[crop_paths["earlier"], crop_paths["later"]],
            context_paths=context_paths,
            strip_paths=[crop_paths[slot] for slot in slots],
            out_root=out_root,
            provenance=provenance,
        )
        auto = auto_rule(endpoint_stats["earlier"], endpoint_stats["later"])
        auto_row = {
            "anchor_id": anchor_id,
            "decision_source": None if auto is None else auto[0],
            "earlier_override": None if auto is None else auto[1],
            "later_override": None if auto is None else auto[2],
            "final_class": None if auto is None else auto[0],
        }
        return packet, auto_row

    packets: list[dict[str, Any]] = []
    autos: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {
            pool.submit(one, row): str(row["anchor_id"])
            for row in candidates.to_dict("records")
        }
        for index, future in enumerate(as_completed(futures), 1):
            try:
                packet, auto = future.result()
                packets.append(packet)
                autos.append(auto)
            except Exception as exc:
                failures.append({
                    "anchor_id": futures[future], "error_type": type(exc).__name__,
                    "error": str(exc), "stage": "packet_materialization",
                })
            if index % 200 == 0:
                print(f"[packets] {index}/{len(futures)} failures={len(failures)}", flush=True)
    if failures:
        failure_path = out_root / "build_failures.jsonl"
        if failure_path.exists():
            failure_path.unlink()
        for failure in failures:
            append_jsonl(failure_path, failure)
        raise RuntimeError(f"packet materialization failed for {len(failures)} anchors")
    packets.sort(key=lambda item: item["anchor_id"])
    preflight_review_packets(packets, out_root)
    packet_index = pd.DataFrame(packets)
    atomic_parquet(out_root / "packet_index.parquet", packet_index)
    auto_frame = pd.DataFrame(autos).sort_values("anchor_id").reset_index(drop=True)
    atomic_parquet(out_root / "auto_verdicts.parquet", auto_frame)
    return packets, auto_frame


def preflight_review_packets(records: Sequence[Mapping[str, Any]], out_root: Path) -> None:
    """Validate every source, geometry and completed render before any request."""
    failures = []
    for record in records:
        anchor_id = str(record.get("anchor_id", "UNKNOWN"))
        try:
            for prefix in ("earlier_source", "later_source", "geometry"):
                path = Path(record[f"{prefix}_path"])
                if not path.is_file():
                    raise FileNotFoundError(path)
                expected = str(record[f"{prefix}_sha256"])
                if sha256_file(path) != expected:
                    raise ValueError(f"{prefix} SHA mismatch")
            for group in ("source_objects", "geometry_objects", "render_objects"):
                for item in record.get(group, []):
                    path = Path(item["path"])
                    if not path.is_file() or sha256_file(path) != item["sha256"]:
                        raise ValueError(f"{group} integrity failed: {item.get('slot')}")
            validate_packet_record(record)
            # Decode, rather than merely stat, all three final review images.
            from PIL import Image
            for key in ("boundary_tight", "boundary_context", "temporal_strip"):
                with Image.open(record[f"{key}_path"]) as image:
                    image.load()
                    if image.mode not in {"RGB", "RGBA"} or min(image.size) <= 0:
                        raise ValueError(f"{key} unexpected image mode/size {image.mode}/{image.size}")
                    expected_size = record.get(f"{key}_size")
                    if expected_size is not None and tuple(expected_size) != image.size:
                        raise ValueError(f"{key} image size {image.size} != {tuple(expected_size)}")
        except Exception as exc:
            failures.append({
                "anchor_id": anchor_id, "error_type": type(exc).__name__,
                "error": str(exc), "stage": "input_integrity_and_render_preflight",
            })
    if failures:
        failure_path = out_root / "build_failures.jsonl"
        if failure_path.exists():
            failure_path.unlink()
        for failure in failures:
            append_jsonl(failure_path, failure)
        raise RuntimeError(f"packet build failed for {len(failures)} objects; no verdict sidecar written")
    failure_path = out_root / "build_failures.jsonl"
    if failure_path.exists():
        failure_path.unlink()


def _quarantine_version_drift(out_root: Path, details: Mapping[str, Any]) -> None:
    atomic_json(out_root / "QUARANTINED_MODEL_VERSION_DRIFT.json", details)
    for name in ("consensus.parquet", "frame_label_overrides.parquet"):
        path = out_root / name
        if path.exists():
            quarantine = out_root / "quarantine" / name
            quarantine.parent.mkdir(parents=True, exist_ok=True)
            os.replace(path, quarantine)


def call_model_rep(
    *,
    anchor_id: str,
    rep: int,
    packet: Mapping[str, Any],
    alias: str,
    arm: str,
    lock: dict[str, Any],
    out_root: Path,
    caller: Callable[..., tuple[str, Mapping[str, Any]]],
    max_attempts: int = 3,
) -> dict[str, Any]:
    """Run one independent logical rep with schema retries and exact-version lock.

    ``caller`` has the same keyword surface as the existing native Gemini helper
    and is injected to keep transport tests authenticated only when explicitly
    requested. The verdict store is intentionally not consulted.
    """
    validate_packet_record(packet)
    image_paths = [
        Path(packet["boundary_tight_path"]), Path(packet["boundary_context_path"]),
        Path(packet["temporal_strip_path"]),
    ]
    last_error = ""
    for attempt in range(1, max_attempts + 1):
        salt = sha256_bytes(
            f"{RUN_ID}\0transport{TRANSPORT_REVISION}\0{anchor_id}\0{rep}\0{attempt}".encode()
        )
        started = time.time()
        raw: Mapping[str, Any] = {}
        try:
            text, raw = caller(
                image_paths=image_paths, prompt=packet["prompt"], model=alias,
                max_tokens=MAX_OUTPUT_TOKENS, response_mime_type="application/json",
                response_schema=TRANSPORT_RESPONSE_SCHEMA, routing_salt=salt,
            )
            returned = raw.get("modelVersion")
            try:
                exact = admit_exact_version(lock, arm, alias, returned)
            except ModelVersionDrift as exc:
                details = {
                    "anchor_id": anchor_id, "rep": rep, "attempt": attempt,
                    "arm": arm, "requested_alias": alias, "returned_version": returned,
                    "error": str(exc),
                }
                append_jsonl(out_root / "attempts.jsonl", {**details, "status": "MODEL_VERSION_DRIFT"})
                _quarantine_version_drift(out_root, details)
                raise
            parsed = validate_response(json.loads(text))
            parsed["_schema_retry"] = attempt > 1
            parsed["_rep"] = rep
            parsed["_exact_model_version"] = exact
            append_jsonl(out_root / "attempts.jsonl", {
                "anchor_id": anchor_id, "rep": rep, "attempt": attempt,
                "routing_salt": salt, "requested_alias": alias,
                "exact_model_version": exact, "status": "VALID",
                "elapsed_seconds": time.time() - started, "raw_response": raw,
            })
            atomic_json(out_root / "RUN_LOCK.json", lock)
            return parsed
        except ModelVersionDrift:
            raise
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            append_jsonl(out_root / "attempts.jsonl", {
                "anchor_id": anchor_id, "rep": rep, "attempt": attempt,
                "routing_salt": salt, "requested_alias": alias,
                "returned_model_version": raw.get("modelVersion"),
                "status": "RETRYABLE_SCHEMA_OR_TRANSPORT_FAILURE",
                "error": last_error, "elapsed_seconds": time.time() - started,
            })
    raise RuntimeError(f"{anchor_id} rep {rep} failed closed after {max_attempts} attempts: {last_error}")


def native_caller(config: Any, limiter: Any | None = None) -> Callable[..., tuple[str, Mapping[str, Any]]]:
    """Adapt the repository's existing native generateContent transport."""
    if getattr(config, "api_format", None) != "native":
        raise ValueError("R4 short-gap adjudication requires api_format=native")
    from scripts.validation.gemini_solar_image_review import _call_gemini

    def call(**kwargs: Any) -> tuple[str, Mapping[str, Any]]:
        alias = kwargs.pop("model")
        if alias != config.model:
            raise ValueError(f"caller config model {config.model!r} != requested alias {alias!r}")
        return _call_gemini(config=config, limiter=limiter, **kwargs)

    return call


def run_anchor_reps(
    *,
    anchor_id: str,
    packet: Mapping[str, Any],
    alias: str,
    arm: str,
    lock: dict[str, Any],
    out_root: Path,
    caller: Callable[..., tuple[str, Mapping[str, Any]]],
) -> tuple[list[dict[str, Any]], Consensus]:
    """Run/resume two primary reps and the mandatory conditional third rep."""
    rep_dir = out_root / "reps" / arm / anchor_id
    rep_dir.mkdir(parents=True, exist_ok=True)

    def one(rep: int) -> dict[str, Any]:
        path = rep_dir / f"rep{rep}.json"
        if path.exists():
            payload = json.loads(path.read_text())
            if (
                payload.get("_packet_sha256") != packet["packet_sha256"]
                or payload.get("_requested_alias") != alias
            ):
                raise ValueError(f"{anchor_id} rep {rep} resume identity mismatch")
            expected = lock.get("model_arms", {}).get(arm, {}).get("exact_model_version")
            if expected and payload.get("_exact_model_version") != expected:
                raise ModelVersionDrift(f"MODEL_VERSION_DRIFT in resumed rep {rep}")
            return payload
        payload = call_model_rep(
            anchor_id=anchor_id, rep=rep, packet=packet, alias=alias, arm=arm,
            lock=lock, out_root=out_root, caller=caller,
        )
        payload["_packet_sha256"] = packet["packet_sha256"]
        payload["_requested_alias"] = alias
        atomic_json(path, payload)
        return payload

    reps = [one(1), one(2)]
    if needs_third_rep(reps):
        reps.append(one(3))
    return reps, consensus(reps)


def run_arm(
    *,
    packets: Sequence[Mapping[str, Any]],
    alias: str,
    arm: str,
    lock: dict[str, Any],
    out_root: Path,
    workers: int,
    qps: float,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Run one blinded arm with one shared global QPS limiter."""
    from scripts.validation.gemini_solar_image_review import RateLimiter
    from scripts.validation.issue29_coj_external_robustness import client_config

    caller = native_caller(client_config(alias), RateLimiter(qps))
    rep_rows, consensus_rows = [], []

    def one(packet: Mapping[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        reps, result = run_anchor_reps(
            anchor_id=str(packet["anchor_id"]), packet=packet, alias=alias, arm=arm,
            lock=lock, out_root=out_root, caller=caller,
        )
        rep_records = [
            {"anchor_id": packet["anchor_id"], "arm": arm, **rep}
            for rep in reps
        ]
        consensus_record = {
            "anchor_id": packet["anchor_id"], "arm": arm,
            "final_class": result.final_class,
            "gate_factors": result.gate_factors,
            "agreeing_reps": result.agreeing_reps,
            "high_confidence_reps": result.high_confidence_reps,
            "earlier_override": result.earlier_override,
            "later_override": result.later_override,
            "decision_source": f"GEMINI_{arm}_CONSENSUS",
        }
        return rep_records, consensus_record

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {pool.submit(one, packet): packet["anchor_id"] for packet in packets}
        for index, future in enumerate(as_completed(futures), 1):
            reps, result = future.result()
            rep_rows.extend(reps)
            consensus_rows.append(result)
            if index % 10 == 0 or index == len(futures):
                print(f"[{arm}] completed {index}/{len(futures)}", flush=True)
    reps_frame = pd.DataFrame(rep_rows).sort_values(["anchor_id", "_rep"])
    consensus_frame = pd.DataFrame(consensus_rows).sort_values("anchor_id")
    return reps_frame, consensus_frame


def run_panel(
    *,
    packet_index: pd.DataFrame,
    out_root: Path,
    workers: int,
    qps: float,
    smoke_anchors: int = 10,
) -> dict[str, Any]:
    """Authenticated smoke followed by blinded panel inference; no model selection."""
    panel_packets = [
        row for row in packet_index.to_dict("records")
        if str(row["anchor_id"]) in set(PANEL_IDS)
    ]
    if len(panel_packets) != 40:
        raise ValueError(f"packet index resolved {len(panel_packets)}/40 panel rows")
    preflight_review_packets(panel_packets, out_root)
    rng = random.Random(2026072401)
    aliases = list(CANDIDATE_ALIASES)
    rng.shuffle(aliases)
    arm_map = {"A": aliases[0], "B": aliases[1]}
    map_path = out_root / "panel" / "ARM_MAP.sealed.json"
    if map_path.exists():
        existing = json.loads(map_path.read_text())
        if existing["arm_map"] != arm_map:
            raise ValueError("sealed panel arm map differs")
    else:
        atomic_json(map_path, {"arm_map": arm_map, "sealed_before_calls": True})
        os.chmod(map_path, 0o600)
    lock_path = out_root / "RUN_LOCK.json"
    lock = json.loads(lock_path.read_text()) if lock_path.exists() else {}
    lock.update({
        "run_id": RUN_ID,
        "transport": {"workers": workers, "global_qps_per_arm": qps},
        "transport_amendment": (
            "owner-authorized 2026-07-24 rerun: enhanced prompt, native "
            "responseSchema plus local contradiction validation, workers=30,qps=6"
        ),
        "transport_revision": TRANSPORT_REVISION,
        "transport_schema_projection": "Gemini subset: uniqueItems enforced locally",
        "panel_packet_count": 40,
        "panel_packet_index_sha256": sha256_file(out_root / "packet_index.parquet"),
    })
    atomic_json(lock_path, lock)
    for arm, alias in arm_map.items():
        smoke_packets = panel_packets[:smoke_anchors]
        smoke_root = out_root / "panel" / "transport_smoke"
        smoke_reps, _ = run_arm(
            packets=smoke_packets, alias=alias, arm=f"{arm}_SMOKE", lock=lock,
            out_root=smoke_root, workers=workers, qps=qps,
        )
        if len(smoke_reps) < smoke_anchors * 2:
            raise RuntimeError(f"{arm}: authenticated smoke did not complete two primary reps")
        smoke_versions = set(smoke_reps["_exact_model_version"].astype(str))
        if len(smoke_versions) != 1:
            raise ModelVersionDrift(f"{arm}: smoke mixed exact versions")
        # Panel must use the exact version established by the corresponding smoke.
        lock.setdefault("model_arms", {})[arm] = {
            "requested_alias": alias, "exact_model_version": next(iter(smoke_versions)),
        }
        atomic_json(lock_path, lock)
        shuffled = list(panel_packets)
        random.Random(sha256_bytes(f"{PANEL_SALT}\0{arm}".encode())).shuffle(shuffled)
        reps, decisions = run_arm(
            packets=shuffled, alias=alias, arm=arm, lock=lock, out_root=out_root / "panel",
            workers=workers, qps=qps,
        )
        atomic_parquet(out_root / "panel" / f"rep_verdicts_arm_{arm}.parquet", reps)
        atomic_parquet(out_root / "panel" / f"consensus_arm_{arm}.parquet", decisions)
        atomic_json(lock_path, lock)
    summary = {
        "status": "PANEL_MODEL_OUTPUTS_LOCKED_AWAITING_HUMAN_REFERENCE",
        "workers": workers, "qps": qps, "panel_anchors": 40,
        "arm_a_panel_anchors_complete": 40,
        "arm_b_panel_anchors_complete": 40,
        "model_selection_released": False,
        "production_released": False,
        "sidecar_published": False,
        "finished_utc": datetime.now(timezone.utc).isoformat(),
    }
    atomic_json(out_root / "panel" / "model_outputs_status.json", summary)
    artifact_manifest(out_root)
    return summary


def build_and_lock_human_reference(
    *, decisions_path: Path, out_root: Path
) -> dict[str, Any]:
    reviewed_utc = datetime.now(timezone.utc).isoformat()
    decisions = pd.read_csv(decisions_path, keep_default_na=False)
    annotations = materialize_manual_annotations(decisions, reviewed_utc=reviewed_utc)
    annotations_path = out_root / "panel" / "human_annotations.jsonl"
    annotations_path.write_text(
        "".join(
            json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n"
            for row in annotations.to_dict("records")
        )
    )
    reference, disputes = build_human_reference(annotations)
    reference_path = out_root / "panel" / "human_reference.parquet"
    disputes_path = out_root / "panel" / "human_disputes.parquet"
    atomic_parquet(reference_path, reference)
    atomic_parquet(disputes_path, disputes)

    lock_path = out_root / "RUN_LOCK.json"
    lock = json.loads(lock_path.read_text())
    lock["human_reference"] = {
        "annotations_path": str(annotations_path),
        "annotations_sha256": sha256_file(annotations_path),
        "reference_path": str(reference_path),
        "reference_sha256": sha256_file(reference_path),
        "n_clear": len(reference),
        "n_disputed": len(disputes),
        "reviewer_provenance": (
            "same Codex visual reviewer; two order-isolated passes, "
            "not two independent human people"
        ),
    }
    atomic_json(lock_path, lock)
    summary = {
        "status": "HUMAN_REFERENCE_LOCKED_AWAITING_MODEL_SELECTION",
        "n_clear": len(reference), "n_disputed": len(disputes),
        "model_selection_released": False, "production_released": False,
        "sidecar_published": False, "finished_utc": reviewed_utc,
    }
    atomic_json(out_root / "panel" / "human_reference_status.json", summary)
    artifact_manifest(out_root)
    return summary


def score_and_select_panel_model(*, out_root: Path) -> dict[str, Any]:
    lock_path = out_root / "RUN_LOCK.json"
    lock = json.loads(lock_path.read_text())
    human = lock.get("human_reference") or {}
    reference_path = Path(str(human.get("reference_path", "")))
    annotations_path = Path(str(human.get("annotations_path", "")))
    if (
        not reference_path.is_file()
        or sha256_file(reference_path) != human.get("reference_sha256")
        or not annotations_path.is_file()
        or sha256_file(annotations_path) != human.get("annotations_sha256")
    ):
        raise ValueError("human reference lock is missing or changed")
    reference = pd.read_parquet(reference_path)
    n_clear = len(reference)
    if n_clear < 30:
        raise ValueError(f"N_clear={n_clear} below frozen minimum 30")

    arm_matches: dict[str, int] = {}
    detail_frames = []
    for arm in ("A", "B"):
        frame = pd.read_parquet(out_root / "panel" / f"consensus_arm_{arm}.parquet")
        matches, details = panel_tuple_matches(reference, frame)
        arm_matches[arm] = matches
        details.insert(1, "arm", arm)
        detail_frames.append(details)
    scoring = pd.concat(detail_frames, ignore_index=True)
    atomic_parquet(out_root / "panel" / "model_selection_scoring.parquet", scoring)

    sealed = json.loads((out_root / "panel" / "ARM_MAP.sealed.json").read_text())
    arm_aliases = dict(sealed["arm_map"])
    try:
        selected = select_model(arm_matches, n_clear, arm_aliases)
    except ValueError as exc:
        failed = {
            "status": "MODEL_SELECTION_FAILED_CLOSED",
            "failure_code": "NEITHER_ARM_COMPETENT",
            "failure_detail": str(exc),
            "n_clear": n_clear,
            "matches": arm_matches,
            "accuracy": {arm: arm_matches[arm] / n_clear for arm in ("A", "B")},
            "competent": {arm: arm_matches[arm] / n_clear >= 0.90 for arm in ("A", "B")},
            "arm_aliases_unsealed_after_human_reference_lock": arm_aliases,
            "human_reference_sha256": human["reference_sha256"],
            "reviewer_provenance": human["reviewer_provenance"],
            "model_selection_released": False,
            "production_released": False,
            "sidecar_published": False,
            "finished_utc": datetime.now(timezone.utc).isoformat(),
        }
        selection_path = out_root / "panel" / "model_selection.json"
        atomic_json(selection_path, failed)
        lock["model_selection"] = {
            **failed, "path": str(selection_path), "sha256": sha256_file(selection_path),
        }
        atomic_json(lock_path, lock)
        atomic_json(out_root / "panel" / "model_outputs_status.json", failed)
        artifact_manifest(out_root)
        return failed
    winner = str(selected["winning_arm"])
    selected.update({
        "accuracy": {arm: arm_matches[arm] / n_clear for arm in ("A", "B")},
        "winning_exact_model_version": lock["model_arms"][winner]["exact_model_version"],
        "human_reference_sha256": human["reference_sha256"],
        "selected_utc": datetime.now(timezone.utc).isoformat(),
        "reviewer_provenance": human["reviewer_provenance"],
    })
    selection_path = out_root / "panel" / "model_selection.json"
    atomic_json(selection_path, selected)
    lock["model_selection"] = {
        **selected, "path": str(selection_path), "sha256": sha256_file(selection_path),
    }
    lock.setdefault("model_arms", {})["PRODUCTION"] = {
        "requested_alias": selected["winning_alias"],
        "exact_model_version": selected["winning_exact_model_version"],
    }
    atomic_json(lock_path, lock)
    status = {
        "status": "MODEL_SELECTED_PRODUCTION_RELEASED",
        "n_clear": n_clear, "matches": arm_matches,
        "winning_arm": winner, "model_selection_released": True,
        "production_released": True, "sidecar_published": False,
        "finished_utc": datetime.now(timezone.utc).isoformat(),
    }
    atomic_json(out_root / "panel" / "model_outputs_status.json", status)
    artifact_manifest(out_root)
    return status


def run_production(
    *, packet_index: pd.DataFrame, out_root: Path, workers: int, qps: float,
) -> dict[str, Any]:
    lock_path = out_root / "RUN_LOCK.json"
    lock = json.loads(lock_path.read_text())
    selection = lock.get("model_selection") or {}
    if selection.get("status") == "MODEL_SELECTION_FAILED_CLOSED":
        raise RuntimeError("production not released: neither panel arm passed competence gate")
    selection_path = Path(str(selection.get("path", "")))
    if (
        not selection_path.is_file()
        or sha256_file(selection_path) != selection.get("sha256")
    ):
        raise ValueError("model selection is missing or changed")
    winner = str(selection["winning_arm"])
    alias = str(selection["winning_alias"])
    exact = str(selection["winning_exact_model_version"])
    production_lock = lock.get("model_arms", {}).get("PRODUCTION", {})
    if (
        production_lock.get("requested_alias") != alias
        or production_lock.get("exact_model_version") != exact
    ):
        raise ModelVersionDrift("MODEL_VERSION_DRIFT: production lock differs from panel winner")

    auto = pd.read_parquet(out_root / "auto_verdicts.parquet")
    auto_rows = auto.loc[auto["decision_source"].notna()].copy()
    auto_ids = set(auto_rows["anchor_id"].astype(str))
    panel_ids = set(PANEL_IDS)
    production_packets = [
        row for row in packet_index.to_dict("records")
        if str(row["anchor_id"]) not in auto_ids | panel_ids
    ]
    if len(production_packets) != EXPECTED_POPULATION - len(auto_ids) - len(panel_ids):
        raise ValueError(
            f"production packet count {len(production_packets)} differs from frozen remainder"
        )
    preflight_review_packets(production_packets, out_root)
    reps, decisions = run_arm(
        packets=production_packets, alias=alias, arm="PRODUCTION", lock=lock,
        out_root=out_root, workers=workers, qps=qps,
    )
    atomic_parquet(out_root / "rep_verdicts.parquet", reps)

    panel_winner = pd.read_parquet(
        out_root / "panel" / f"consensus_arm_{winner}.parquet"
    ).copy()
    panel_winner["decision_source"] = "GEMINI_PANEL_WINNER_CONSENSUS"
    decisions = decisions.copy()
    decisions["decision_source"] = "GEMINI_PRODUCTION_CONSENSUS"
    combined = pd.concat([panel_winner, decisions, auto_rows], ignore_index=True, sort=False)
    if len(combined) != EXPECTED_POPULATION or combined["anchor_id"].astype(str).nunique() != EXPECTED_POPULATION:
        raise ValueError("combined consensus does not cover the frozen population exactly once")
    combined = combined.sort_values("anchor_id").reset_index(drop=True)
    atomic_parquet(out_root / "consensus.parquet", combined)
    candidates = pd.read_parquet(out_root / "candidate_manifest.parquet")
    overrides = write_frame_overrides(
        candidates, combined, out_root / "frame_label_overrides.parquet"
    )
    summary = {
        "status": "PRODUCTION_COMPLETE_SIDECAR_PUBLISHED",
        "workers": workers, "qps": qps,
        "production_anchors_called": len(production_packets),
        "panel_anchors_reused": len(panel_ids),
        "auto_anchors": len(auto_ids),
        "consensus_anchors": len(combined),
        "rep_rows": len(reps),
        "third_rep_anchors": int(reps["_rep"].eq(3).sum()),
        "class_counts": combined["final_class"].value_counts(dropna=False).to_dict(),
        "override_rows": len(overrides),
        "model_selection_released": True, "production_released": True,
        "sidecar_published": True,
        "finished_utc": datetime.now(timezone.utc).isoformat(),
    }
    atomic_json(out_root / "summary.json", summary)
    atomic_json(out_root / "panel" / "model_outputs_status.json", summary)
    atomic_json(lock_path, lock)
    artifact_manifest(out_root)
    return summary


def artifact_manifest(root: Path) -> Path:
    rows = []
    for path in sorted(p for p in root.rglob("*") if p.is_file() and p.name != "artifacts.sha256"):
        rows.append(f"{sha256_file(path)}  {path.relative_to(root).as_posix()}")
    target = root / "artifacts.sha256"
    target.write_text("\n".join(rows) + "\n")
    return target


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    default = Path.home() / "zasolar_data/geid_temporal" / RUN_ID
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    derive = sub.add_parser("derive-candidates")
    derive.add_argument("--manifest", type=Path, required=True)
    derive.add_argument("--out-root", type=Path, default=default)
    derive.add_argument("--expected-count", type=int, default=EXPECTED_POPULATION)
    panel = sub.add_parser("verify-panel")
    panel.add_argument("--panel-manifest", type=Path, required=True)
    build_panel = sub.add_parser("build-panel")
    build_panel.add_argument("--candidate-manifest", type=Path, required=True)
    build_panel.add_argument("--out-root", type=Path, default=default)
    packets = sub.add_parser("build-packets")
    packets.add_argument("--manifest", type=Path, required=True)
    packets.add_argument("--candidate-manifest", type=Path, required=True)
    packets.add_argument("--r1-root", type=Path, required=True)
    packets.add_argument("--out-root", type=Path, default=default)
    packets.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    run = sub.add_parser("run-panel")
    run.add_argument("--packet-index", type=Path, required=True)
    run.add_argument("--out-root", type=Path, default=default)
    run.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    run.add_argument("--qps", type=float, default=DEFAULT_QPS)
    human = sub.add_parser("build-human-reference")
    human.add_argument("--decisions", type=Path, required=True)
    human.add_argument("--out-root", type=Path, default=default)
    select = sub.add_parser("select-panel-model")
    select.add_argument("--out-root", type=Path, default=default)
    production = sub.add_parser("run-production")
    production.add_argument("--packet-index", type=Path, required=True)
    production.add_argument("--out-root", type=Path, default=default)
    production.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    production.add_argument("--qps", type=float, default=DEFAULT_QPS)
    lock = sub.add_parser("hash-artifacts")
    lock.add_argument("--out-root", type=Path, default=default)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.command == "derive-candidates":
        frame = derive_candidates(pd.read_parquet(args.manifest), expected_count=args.expected_count)
        atomic_parquet(args.out_root / "candidate_manifest.parquet", frame)
        print(json.dumps({"anchors": len(frame), "output": str(args.out_root)}))
    elif args.command == "verify-panel":
        verify_panel(pd.read_parquet(args.panel_manifest))
        print(json.dumps({"status": "PASS", "panel_rows": 40}))
    elif args.command == "build-panel":
        frame = build_panel_manifest(pd.read_parquet(args.candidate_manifest))
        path = args.out_root / "panel" / "panel_manifest.parquet"
        atomic_parquet(path, frame)
        print(json.dumps({"status": "PASS", "panel_rows": len(frame), "sha256": sha256_file(path)}))
    elif args.command == "build-packets":
        packets, autos = materialize_review_packets(
            manifest=pd.read_parquet(args.manifest),
            candidates=pd.read_parquet(args.candidate_manifest),
            r1_root=args.r1_root, out_root=args.out_root, workers=args.workers,
        )
        print(json.dumps({
            "status": "PASS", "packets": len(packets),
            "auto_verdicts": int(autos["decision_source"].notna().sum()),
        }))
    elif args.command == "run-panel":
        print(json.dumps(run_panel(
            packet_index=pd.read_parquet(args.packet_index), out_root=args.out_root,
            workers=args.workers, qps=args.qps,
        )))
    elif args.command == "build-human-reference":
        print(json.dumps(build_and_lock_human_reference(
            decisions_path=args.decisions, out_root=args.out_root,
        )))
    elif args.command == "select-panel-model":
        print(json.dumps(score_and_select_panel_model(out_root=args.out_root)))
    elif args.command == "run-production":
        print(json.dumps(run_production(
            packet_index=pd.read_parquet(args.packet_index), out_root=args.out_root,
            workers=args.workers, qps=args.qps,
        )))
    else:
        print(artifact_manifest(args.out_root))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
