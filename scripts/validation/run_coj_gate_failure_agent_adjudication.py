#!/usr/bin/env python3
"""High-thinking blind adjudication of the CoJ gate-failure recovery pilot."""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.temporal import run_r4_shortgap_adjudication as v3
from scripts.temporal import run_r4_shortgap_window_recovery as v5
from scripts.validation.gemini_solar_image_review import RateLimiter
from scripts.validation.issue29_coj_external_robustness import client_config
from scripts.validation import run_coj_gate_failure_recovery_pilot as pilot

DATA_ROOT = Path("/home/gao/zasolar_data/geid_temporal")
SOURCE_ROOT = DATA_ROOT / pilot.RUN_ID
RUN_ID = "coj_gate_failure_agent_adjudication_v2"
MODEL_ALIAS = "gemini-3.6-flash-high"
EXPECTED_EXACT_MODEL_VERSION = "gemini-3.6-flash"
EXPECTED_PANEL = 200
PANEL_SEED = "coj-gate-failure-agent-panel-v1@2026-07-24"
RULE_VERSION = "coj_agent_frame_consensus_decoder_v2@2026-07-24"
MAX_OUTPUT_TOKENS = 16384
MAX_ATTEMPTS = 20
DEFAULT_WORKERS = 30
DEFAULT_QPS = 6.0
FRAME_STATES = ("present", "absent", "unclear", "blank")
TARGET_MATCH = ("same_target", "unclear")
COJ_STATES = ("present", "absent", "unclear")
FRAME_IDS = v5.FRAME_IDS
_LOCK = threading.Lock()

PROMPT = """Blindly adjudicate one rooftop target from three images:
1. confirmed-present 2024/2025 semantic reference montage;
2. target-specific CoJ aerial montage, 2019 then 2023;
3. chronological R0 strip F01 through F06.

Do basic visible-image classification before considering temporal order.
First decide whether the same target roof is shown across sources. Then classify
CoJ-2019, CoJ-2023, and every R0 slot independently. A crossed grey R0 slot is
blank. Use unclear for blur, occlusion, target mismatch, or insufficient visible
evidence. Do not infer a frame from neighbouring dates or copy the confirmed
present reference state into older images.

PV requires a visible regular rectangular photovoltaic-module grid on the
target roof. Do not count solar water heaters, skylights, vents, HVAC, glare,
shadows, dark roof paint, or a neighbouring roof. Allow source, scale, colour,
resolution, parallax, and registration changes.

Return exactly one observation for each F01..F06. If target_match=unclear, both
CoJ states and every nonblank R0 state must be unclear. Return JSON only."""

RESPONSE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "target_match", "coj_2019_state", "coj_2023_state",
        "r0_frames", "confidence", "reason",
    ],
    "properties": {
        "target_match": {"type": "string", "enum": list(TARGET_MATCH)},
        "coj_2019_state": {"type": "string", "enum": list(COJ_STATES)},
        "coj_2023_state": {"type": "string", "enum": list(COJ_STATES)},
        "r0_frames": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["frame_id", "state", "evidence"],
                "properties": {
                    "frame_id": {"type": "string", "enum": list(FRAME_IDS)},
                    "state": {"type": "string", "enum": list(FRAME_STATES)},
                    "evidence": {"type": "string"},
                },
            },
        },
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "reason": {"type": "string", "minLength": 1},
    },
}


def canonical_sha(value: Any) -> str:
    return v3.sha256_bytes(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    )


def stable_hash(*parts: Any) -> str:
    return v3.sha256_bytes("\x1f".join(map(str, parts)).encode())


def build_panel(source: pd.DataFrame) -> pd.DataFrame:
    overlap = source.loc[source["v5_sequence_status"].notna()].copy()
    exact = (
        overlap["v5_sequence_status"].eq(overlap["sequence_status"])
        & overlap["v5_first_pv_frame"].eq(overlap["first_pv_frame"])
    )
    agreement = overlap.loc[exact].copy()
    disagreement = overlap.loc[~exact].copy()
    if len(agreement) != 43 or len(disagreement) != 56:
        raise ValueError("unexpected V5/pilot overlap partition")
    agreement["panel_component"] = "V5_PILOT_EXACT_AGREEMENT_CENSUS"
    disagreement["panel_component"] = "V5_PILOT_DISAGREEMENT_CENSUS"

    nonoverlap = source.loc[source["v5_sequence_status"].isna()].copy()
    intervals = pd.read_parquet(SOURCE_ROOT / "r0_install_intervals.parquet")
    nonoverlap = nonoverlap.merge(
        intervals[["anchor_id", "boundary_changed"]], on="anchor_id", how="left"
    )
    nonoverlap["panel_hash"] = [
        stable_hash(PANEL_SEED, anchor_id)
        for anchor_id in nonoverlap["anchor_id"].astype(str)
    ]
    selections = []

    def take(rows: pd.DataFrame, count: int, component: str) -> None:
        if len(rows) < count:
            raise ValueError(f"{component}: only {len(rows)} candidates for {count}")
        chosen = rows.sort_values("panel_hash").head(count).copy()
        chosen["panel_component"] = component
        selections.append(chosen)

    take(
        nonoverlap.loc[
            nonoverlap["sequence_status"].eq("monotonic_install")
            & nonoverlap["boundary_changed"].eq(True)
        ],
        50, "NONOVERLAP_MONOTONIC_CHANGED",
    )
    take(
        nonoverlap.loc[
            nonoverlap["sequence_status"].eq("monotonic_install")
            & nonoverlap["boundary_changed"].eq(False)
        ],
        20, "NONOVERLAP_MONOTONIC_UNCHANGED",
    )
    censored = nonoverlap.loc[
        nonoverlap["sequence_status"].isin(
            ["already_present_before_F01", "not_present_through_F06"]
        )
    ]
    take(censored, 15, "NONOVERLAP_CENSORED")
    difficult = nonoverlap.loc[
        nonoverlap["sequence_status"].isin(
            ["present_to_absent_or_nonmonotonic", "unreviewable"]
        )
    ]
    take(difficult, 16, "NONOVERLAP_DIFFICULT")
    panel = pd.concat([agreement, disagreement, *selections], ignore_index=True)
    if len(panel) != EXPECTED_PANEL or panel["anchor_id"].nunique() != EXPECTED_PANEL:
        raise ValueError("agent panel must contain 200 unique anchors")
    panel["panel_seed"] = PANEL_SEED
    panel["panel_order_hash"] = [
        stable_hash(PANEL_SEED, "order", anchor_id)
        for anchor_id in panel["anchor_id"].astype(str)
    ]
    return panel.sort_values("panel_order_hash").reset_index(drop=True)


def validate_response(
    value: Mapping[str, Any], slot_provenance: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    expected = {
        "target_match", "coj_2019_state", "coj_2023_state",
        "r0_frames", "confidence", "reason",
    }
    if not isinstance(value, Mapping) or set(value) != expected:
        raise ValueError("response fields differ from agent schema")
    target = str(value["target_match"])
    coj19, coj23 = str(value["coj_2019_state"]), str(value["coj_2023_state"])
    if target not in TARGET_MATCH or coj19 not in COJ_STATES or coj23 not in COJ_STATES:
        raise ValueError("out-of-vocabulary target/CoJ state")
    raw_frames = value["r0_frames"]
    if not isinstance(raw_frames, list) or len(raw_frames) != 6:
        raise ValueError("response must contain exactly six R0 frames")
    frames: dict[str, dict[str, str]] = {}
    source_present = {
        str(item["frame_id"]): bool(item["source_present"])
        for item in slot_provenance
    }
    for item in raw_frames:
        if not isinstance(item, Mapping) or set(item) != {"frame_id", "state", "evidence"}:
            raise ValueError("invalid R0 frame object")
        frame_id, state = str(item["frame_id"]), str(item["state"])
        if frame_id not in FRAME_IDS or frame_id in frames or state not in FRAME_STATES:
            raise ValueError("invalid/duplicate R0 frame")
        if source_present[frame_id] != (state != "blank"):
            raise ValueError(f"{frame_id}: blank/source contradiction")
        frames[frame_id] = {
            "frame_id": frame_id, "state": state,
            "evidence": str(item["evidence"]).strip(),
        }
    if set(frames) != set(FRAME_IDS):
        raise ValueError("R0 frame coverage mismatch")
    confidence = value["confidence"]
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        raise ValueError("confidence must be numeric")
    if not 0 <= float(confidence) <= 1 or not str(value["reason"]).strip():
        raise ValueError("invalid confidence/reason")
    if target == "unclear":
        if coj19 != "unclear" or coj23 != "unclear":
            raise ValueError("unclear target has definite CoJ state")
        if any(
            frames[frame_id]["state"] not in {"unclear", "blank"}
            for frame_id in FRAME_IDS
        ):
            raise ValueError("unclear target has definite R0 state")
    return {
        "target_match": target, "coj_2019_state": coj19,
        "coj_2023_state": coj23,
        "r0_frames": [frames[frame_id] for frame_id in FRAME_IDS],
        "confidence": float(confidence), "reason": str(value["reason"]).strip(),
    }


def decisive_tuple(rep: Mapping[str, Any]) -> tuple[str, ...]:
    states = {item["frame_id"]: item["state"] for item in rep["r0_frames"]}
    return (
        str(rep["target_match"]), str(rep["coj_2019_state"]),
        str(rep["coj_2023_state"]), *(states[frame_id] for frame_id in FRAME_IDS),
    )


def needs_third_rep(reps: Sequence[Mapping[str, Any]]) -> bool:
    if len(reps) != 2:
        raise ValueError("two primary reps required")
    return decisive_tuple(reps[0]) != decisive_tuple(reps[1])


def _majority(values: Sequence[str], unclear: str) -> tuple[str, list[int]]:
    counts: dict[str, list[int]] = {}
    for index, value in enumerate(values, 1):
        counts.setdefault(value, []).append(index)
    winner = max(counts, key=lambda key: len(counts[key]))
    return (
        (winner, counts[winner]) if len(counts[winner]) >= 2
        else (unclear, [])
    )


def fieldwise_consensus(reps: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if len(reps) not in {2, 3}:
        raise ValueError("two or three reps required")
    target, target_votes = _majority(
        [str(rep["target_match"]) for rep in reps], "unclear"
    )
    coj19, coj19_votes = _majority(
        [str(rep["coj_2019_state"]) for rep in reps], "unclear"
    )
    coj23, coj23_votes = _majority(
        [str(rep["coj_2023_state"]) for rep in reps], "unclear"
    )
    frame_rows = []
    frame_votes = {}
    rep_frames = [
        {item["frame_id"]: item for item in rep["r0_frames"]} for rep in reps
    ]
    for frame_id in FRAME_IDS:
        state, votes = _majority(
            [frames[frame_id]["state"] for frames in rep_frames], "unclear"
        )
        frame_rows.append({"frame_id": frame_id, "state": state})
        frame_votes[frame_id] = votes
    if target == "unclear":
        coj19 = coj23 = "unclear"
        for frame in frame_rows:
            if frame["state"] != "blank":
                frame["state"] = "unclear"
    return {
        "target_match": target, "coj_2019_state": coj19,
        "coj_2023_state": coj23, "r0_frames": frame_rows,
        "field_agreeing_reps": {
            "target_match": target_votes, "coj_2019_state": coj19_votes,
            "coj_2023_state": coj23_votes, **frame_votes,
        },
        "confidence": sum(float(rep["confidence"]) for rep in reps) / len(reps),
        "reason": " | ".join(dict.fromkeys(str(rep["reason"]) for rep in reps)),
        "rep_count": len(reps),
    }


def decode_sequence(
    frames: Sequence[Mapping[str, Any]], source_present: Mapping[str, bool],
) -> dict[str, str]:
    states = [
        (frame_id, str(next(item["state"] for item in frames if item["frame_id"] == frame_id)))
        for frame_id in FRAME_IDS if source_present[frame_id]
    ]
    definite = [(frame_id, state) for frame_id, state in states if state in {"absent", "present"}]
    if not definite:
        return {"sequence_status": "unreviewable", "first_pv_frame": "unclear"}
    seen_present = False
    for _, state in definite:
        if state == "present":
            seen_present = True
        elif seen_present:
            return {
                "sequence_status": "present_to_absent_or_nonmonotonic",
                "first_pv_frame": "unclear",
            }
    has_absent = any(state == "absent" for _, state in definite)
    present_frames = [frame_id for frame_id, state in definite if state == "present"]
    has_unclear = any(state == "unclear" for _, state in states)
    if has_absent and present_frames:
        return {
            "sequence_status": "monotonic_install",
            "first_pv_frame": present_frames[0],
        }
    if present_frames and not has_absent and not has_unclear:
        return {
            "sequence_status": "already_present_before_F01",
            "first_pv_frame": "before_F01",
        }
    if has_absent and not present_frames and not has_unclear:
        return {
            "sequence_status": "not_present_through_F06",
            "first_pv_frame": "none",
        }
    return {"sequence_status": "unreviewable", "first_pv_frame": "unclear"}


def prepare(out_root: Path) -> dict[str, Any]:
    lock_path = out_root / "RUN_LOCK.json"
    if lock_path.exists():
        lock = json.loads(lock_path.read_text())
        if lock.get("run_id") != RUN_ID:
            raise ValueError("existing output has different identity")
        return {"status": "ALREADY_PREPARED", "panel": lock["panel_size"]}
    source = pd.read_parquet(SOURCE_ROOT / "recovery_verdicts.parquet")
    packets = pd.read_parquet(SOURCE_ROOT / "packet_index.parquet")
    panel = build_panel(source)
    selected_packets = panel[["anchor_id", "panel_order_hash"]].merge(
        packets, on="anchor_id", validate="one_to_one"
    )
    prompt_sha = v3.sha256_bytes(PROMPT.encode())
    schema_sha = canonical_sha(RESPONSE_SCHEMA)
    rule_sha = v3.sha256_bytes(RULE_VERSION.encode())
    selected_packets["source_pilot_packet_sha256"] = selected_packets["packet_sha256"]
    selected_packets["prompt_sha256"] = prompt_sha
    selected_packets["response_schema_sha256"] = schema_sha
    selected_packets["rule_sha256"] = rule_sha
    selected_packets["packet_sha256"] = [
        v3.sha256_bytes(
            f"{RUN_ID}\0{source_sha}\0{prompt_sha}\0{schema_sha}\0{rule_sha}\0{order}".encode()
        )
        for source_sha, order in zip(
            selected_packets["source_pilot_packet_sha256"],
            selected_packets["panel_order_hash"],
        )
    ]
    v3.atomic_parquet(out_root / "panel_manifest.parquet", panel)
    v3.atomic_parquet(out_root / "packet_index.parquet", selected_packets)
    lock = {
        "run_id": RUN_ID, "source_run_id": pilot.RUN_ID,
        "panel_size": len(panel), "panel_seed": PANEL_SEED,
        "panel_component_counts": panel["panel_component"].value_counts().to_dict(),
        "model_alias": MODEL_ALIAS,
        "expected_exact_model_version": EXPECTED_EXACT_MODEL_VERSION,
        "max_output_tokens": MAX_OUTPUT_TOKENS,
        "max_attempts_per_rep": MAX_ATTEMPTS,
        "prompt_sha256": prompt_sha, "response_schema_sha256": schema_sha,
        "rule_version": RULE_VERSION, "rule_sha256": rule_sha,
        "panel_manifest_sha256": v3.sha256_file(out_root / "panel_manifest.parquet"),
        "packet_index_sha256": v3.sha256_file(out_root / "packet_index.parquet"),
        "source_summary_sha256": v3.sha256_file(SOURCE_ROOT / "summary.json"),
        "source_recovery_verdicts_sha256": v3.sha256_file(
            SOURCE_ROOT / "recovery_verdicts.parquet"
        ),
        "source_packet_index_sha256": v3.sha256_file(SOURCE_ROOT / "packet_index.parquet"),
        "transport": {"workers": DEFAULT_WORKERS, "qps": DEFAULT_QPS},
        "smoke_anchor_ids": panel["anchor_id"].astype(str).head(10).tolist(),
    }
    v3.atomic_json(lock_path, lock)
    v3.artifact_manifest(out_root)
    return {"status": "PREPARED", "panel": len(panel), "components": lock["panel_component_counts"]}


def admit_version(lock: dict[str, Any], returned: Any) -> str:
    version = str(returned or "").strip()
    if not version:
        raise v3.ModelVersionDrift("missing exact version")
    if version != EXPECTED_EXACT_MODEL_VERSION:
        raise ValueError(
            f"version rejected: expected {EXPECTED_EXACT_MODEL_VERSION!r}, "
            f"got {version!r}"
        )
    with _LOCK:
        expected = lock.get("exact_model_version")
        if expected is None:
            lock["exact_model_version"] = version
        elif expected != version:
            raise v3.ModelVersionDrift(f"expected {expected!r}, got {version!r}")
    return version


def run_rep(
    packet: Mapping[str, Any], rep: int, scope: str, out_root: Path,
    lock: dict[str, Any], caller: Callable[..., tuple[str, Mapping[str, Any]]],
) -> dict[str, Any]:
    anchor_id = str(packet["anchor_id"])
    path = out_root / scope / "reps" / anchor_id / f"rep{rep}.json"
    identity = {
        "_run_id": RUN_ID, "_packet_sha256": packet["packet_sha256"],
        "_model_alias": MODEL_ALIAS, "_prompt_sha256": packet["prompt_sha256"],
        "_response_schema_sha256": packet["response_schema_sha256"],
        "_rule_sha256": packet["rule_sha256"],
    }
    if path.exists():
        payload = json.loads(path.read_text())
        if any(payload.get(key) != value for key, value in identity.items()):
            raise ValueError(f"{anchor_id}: resume identity mismatch")
        admit_version(lock, payload.get("_exact_model_version"))
        return payload
    images = [
        Path(packet["reference_montage_path"]), Path(packet["coj_montage_path"]),
        Path(packet["r0_temporal_strip_path"]),
    ]
    attempts_path = out_root / scope / "attempts.jsonl"
    prior = []
    if attempts_path.exists():
        for line in attempts_path.read_text().splitlines():
            record = json.loads(line)
            if record.get("anchor_id") == anchor_id and int(record.get("rep", -1)) == rep:
                prior.append(int(record["attempt"]))
    last_error = ""
    for attempt in range(max(prior, default=0) + 1, MAX_ATTEMPTS + 1):
        salt = stable_hash(RUN_ID, scope, anchor_id, rep, attempt, packet["packet_sha256"])
        started = time.time()
        raw: Mapping[str, Any] = {}
        try:
            text, raw = caller(
                image_paths=images, prompt=PROMPT, model=MODEL_ALIAS,
                max_tokens=MAX_OUTPUT_TOKENS,
                response_mime_type="application/json",
                response_schema=RESPONSE_SCHEMA, routing_salt=salt,
            )
            exact = admit_version(lock, raw.get("modelVersion"))
            parsed = validate_response(
                json.loads(text), list(packet["r0_slot_provenance"])
            )
            parsed.update({
                **identity, "_rep": rep, "_attempt": attempt,
                "_routing_salt": salt, "_exact_model_version": exact,
            })
            v3.atomic_json(path, parsed)
            v3.append_jsonl(attempts_path, {
                "anchor_id": anchor_id, "rep": rep, "attempt": attempt,
                "status": "VALID", "routing_salt": salt,
                "packet_sha256": packet["packet_sha256"],
                "model_alias": MODEL_ALIAS, "exact_model_version": exact,
                "max_output_tokens": MAX_OUTPUT_TOKENS,
                "elapsed_seconds": time.time() - started,
                "response": parsed,
                "raw": {
                    "modelVersion": raw.get("modelVersion"),
                    "responseId": raw.get("responseId"),
                    "usageMetadata": raw.get("usageMetadata"),
                    "finishReasons": [
                        item.get("finishReason") for item in raw.get("candidates", [])
                    ],
                    "transport_retries": raw.get("_transport_retries", 0),
                },
            })
            v3.atomic_json(out_root / "RUN_LOCK.json", lock)
            return parsed
        except v3.ModelVersionDrift:
            raise
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            v3.append_jsonl(attempts_path, {
                "anchor_id": anchor_id, "rep": rep, "attempt": attempt,
                "status": "RETRYABLE_FAILURE", "routing_salt": salt,
                "packet_sha256": packet["packet_sha256"],
                "max_output_tokens": MAX_OUTPUT_TOKENS,
                "elapsed_seconds": time.time() - started, "error": last_error,
                "returned_model_version": raw.get("modelVersion"),
            })
    raise RuntimeError(f"{anchor_id}/rep{rep}: {last_error}")


def run_packets(
    packets: Sequence[Mapping[str, Any]], out_root: Path, scope: str,
    workers: int, qps: float, allow_third: bool,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    lock = json.loads((out_root / "RUN_LOCK.json").read_text())
    caller = v3.native_caller(client_config(MODEL_ALIAS), RateLimiter(qps))
    rep_rows, consensus_rows = [], []

    def one(packet: Mapping[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        try:
            reps = [
                run_rep(packet, rep, scope, out_root, lock, caller)
                for rep in (1, 2)
            ]
        except RuntimeError as exc:
            # A schema-invalid response that exhausts all attempts has no
            # admissible vote. Fail closed at anchor level; never synthesize a
            # frame label from the surviving one-rep partial result.
            return [], {
                "anchor_id": packet["anchor_id"],
                "target_match": "unclear",
                "coj_2019_state": "unclear",
                "coj_2023_state": "unclear",
                "r0_frames": [
                    {
                        "frame_id": frame_id,
                        "state": (
                            "blank"
                            if not next(
                                item["source_present"]
                                for item in packet["r0_slot_provenance"]
                                if item["frame_id"] == frame_id
                            )
                            else "unclear"
                        ),
                    }
                    for frame_id in FRAME_IDS
                ],
                "field_agreeing_reps": {},
                "rep_count": 0,
                "confidence": 0.0,
                "reason": f"no admissible two-rep consensus: {exc}",
                "sequence_status": "unreviewable",
                "first_pv_frame": "unclear",
                "packet_sha256": packet["packet_sha256"],
                "source_pilot_packet_sha256": packet["source_pilot_packet_sha256"],
                "r0_temporal_strip_sha256": packet["r0_temporal_strip_sha256"],
                "model_alias": MODEL_ALIAS,
                "exact_model_version": lock.get(
                    "exact_model_version", EXPECTED_EXACT_MODEL_VERSION
                ),
                "prompt_sha256": packet["prompt_sha256"],
                "response_schema_sha256": packet["response_schema_sha256"],
                "rule_version": RULE_VERSION,
                "rule_sha256": packet["rule_sha256"],
                "adjudication_error": str(exc),
            }
        if allow_third and needs_third_rep(reps):
            reps.append(run_rep(packet, 3, scope, out_root, lock, caller))
        result = fieldwise_consensus(reps)
        source_present = {
            item["frame_id"]: bool(item["source_present"])
            for item in list(packet["r0_slot_provenance"])
        }
        decoded = (
            decode_sequence(result["r0_frames"], source_present)
            if result["target_match"] == "same_target"
            else {"sequence_status": "unreviewable", "first_pv_frame": "unclear"}
        )
        return reps, {
            "anchor_id": packet["anchor_id"], **result, **decoded,
            "packet_sha256": packet["packet_sha256"],
            "source_pilot_packet_sha256": packet["source_pilot_packet_sha256"],
            "r0_temporal_strip_sha256": packet["r0_temporal_strip_sha256"],
            "model_alias": MODEL_ALIAS,
            "exact_model_version": reps[0]["_exact_model_version"],
            "prompt_sha256": packet["prompt_sha256"],
            "response_schema_sha256": packet["response_schema_sha256"],
            "rule_version": RULE_VERSION, "rule_sha256": packet["rule_sha256"],
        }

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = [pool.submit(one, packet) for packet in packets]
        for index, future in enumerate(as_completed(futures), 1):
            reps, result = future.result()
            rep_rows.extend(reps)
            consensus_rows.append(result)
            if index % 25 == 0 or index == len(futures):
                print(f"[{scope}] completed {index}/{len(futures)}", flush=True)
    v3.atomic_json(out_root / "RUN_LOCK.json", lock)
    return (
        pd.DataFrame(rep_rows).sort_values(["_packet_sha256", "_rep"]),
        pd.DataFrame(consensus_rows).sort_values("anchor_id"),
    )


def run_smoke(out_root: Path, workers: int, qps: float) -> dict[str, Any]:
    lock = json.loads((out_root / "RUN_LOCK.json").read_text())
    packets = pd.read_parquet(out_root / "packet_index.parquet")
    ids = set(lock["smoke_anchor_ids"])
    selected = [row for row in packets.to_dict("records") if row["anchor_id"] in ids]
    reps, decisions = run_packets(selected, out_root, "smoke", workers, qps, False)
    if len(reps) != 20:
        raise RuntimeError("smoke requires 20 primary reps")
    v3.atomic_parquet(out_root / "smoke" / "rep_verdicts.parquet", reps)
    v3.atomic_parquet(out_root / "smoke" / "decisions.parquet", decisions)
    status = {
        "status": "SMOKE_PASS", "anchors": 10, "reps": 20,
        "model_alias": MODEL_ALIAS,
        "exact_model_version": json.loads(
            (out_root / "RUN_LOCK.json").read_text()
        )["exact_model_version"],
    }
    v3.atomic_json(out_root / "smoke" / "status.json", status)
    v3.artifact_manifest(out_root)
    return status


def materialize(
    panel: pd.DataFrame, packets: pd.DataFrame, decisions: pd.DataFrame,
) -> dict[str, pd.DataFrame]:
    # Keep pilot-side fields additive when joining the fresh agent decisions.
    # The panel manifest intentionally contains the low-pilot decision columns,
    # so a bare merge would create pandas _x/_y suffixes and make provenance
    # ambiguous.
    decision_fields = {
        "target_match", "coj_2019_state", "coj_2023_state",
        "sequence_status", "first_pv_frame", "confidence", "reason",
        "rep_count", "packet_sha256", "r0_temporal_strip_sha256",
        "model_alias", "exact_model_version", "prompt_sha256",
        "response_schema_sha256", "rule_version", "rule_sha256",
    }
    panel = panel.rename(columns={
        field: f"low_pilot_{field}"
        for field in decision_fields
        if field in panel.columns
    })
    merged = panel.merge(decisions, on="anchor_id", validate="one_to_one")
    packet_by_id = packets.set_index("anchor_id")
    frames, intervals, coj_rows = [], [], []
    for row in merged.to_dict("records"):
        packet = packet_by_id.loc[row["anchor_id"]]
        slots = {
            item["frame_id"]: dict(item)
            for item in list(packet["r0_slot_provenance"])
        }
        states = {item["frame_id"]: item["state"] for item in row["r0_frames"]}
        common = {
            key: row[key] for key in (
                "field_agreeing_reps", "rep_count", "confidence", "model_alias",
                "exact_model_version", "prompt_sha256", "response_schema_sha256",
                "packet_sha256", "source_pilot_packet_sha256",
                "r0_temporal_strip_sha256", "rule_version", "rule_sha256",
            )
        }
        for frame_id in FRAME_IDS:
            item = slots[frame_id]
            state = states[frame_id]
            if item["source_present"] and state in {"present", "absent"}:
                frames.append({
                    "anchor_id": row["anchor_id"], **item,
                    "pv_label": state, "sequence_status": row["sequence_status"],
                    "first_pv_frame": row["first_pv_frame"], **common,
                })
        for year in (2019, 2023):
            state = row[f"coj_{year}_state"]
            if state in {"present", "absent"}:
                coj_rows.append({
                    "anchor_id": row["anchor_id"], "coj_epoch": year,
                    "acquisition_interval_start": f"{year}-01-01",
                    "acquisition_interval_end": f"{year}-12-31",
                    "pv_label": state, **common,
                })
        if row["sequence_status"] == "monotonic_install":
            first_index = FRAME_IDS.index(row["first_pv_frame"])
            prior_absent = [
                slots[frame_id] for frame_id in FRAME_IDS[:first_index]
                if slots[frame_id]["source_present"] and states[frame_id] == "absent"
            ]
            present = slots[row["first_pv_frame"]]
            if prior_absent:
                absent = prior_absent[-1]
                intervals.append({
                    "anchor_id": row["anchor_id"],
                    "last_absent_frame": absent["frame_id"],
                    "last_absent_capture_date": absent["capture_date"],
                    "last_absent_src_tiff_sha256": absent["src_tiff_sha256"],
                    "first_present_frame": present["frame_id"],
                    "first_present_capture_date": present["capture_date"],
                    "first_present_src_tiff_sha256": present["src_tiff_sha256"],
                    "interval_days": (
                        pd.Timestamp(present["capture_date"])
                        - pd.Timestamp(absent["capture_date"])
                    ).days,
                    **common,
                })
    return {
        "agent_adjudication": merged,
        "agent_r0_frame_labels": pd.DataFrame(frames),
        "agent_r0_install_intervals": pd.DataFrame(intervals),
        "agent_coj_frame_labels": pd.DataFrame(coj_rows),
    }


def run_production(out_root: Path, workers: int, qps: float) -> dict[str, Any]:
    smoke = json.loads((out_root / "smoke" / "status.json").read_text())
    if smoke.get("status") != "SMOKE_PASS":
        raise RuntimeError("production requires passed smoke")
    packets = pd.read_parquet(out_root / "packet_index.parquet")
    panel = pd.read_parquet(out_root / "panel_manifest.parquet")
    reps, decisions = run_packets(
        packets.to_dict("records"), out_root, "production", workers, qps, True
    )
    if set(decisions["exact_model_version"]) != {smoke["exact_model_version"]}:
        raise v3.ModelVersionDrift("production exact version differs from smoke")
    outputs = materialize(panel, packets, decisions)
    v3.atomic_parquet(out_root / "rep_verdicts.parquet", reps)
    for name, frame in outputs.items():
        v3.atomic_parquet(out_root / f"{name}.parquet", frame)
    result = outputs["agent_adjudication"]
    overlap = result.loc[result["v5_sequence_status"].notna()]
    attempts = [
        json.loads(line)
        for line in (out_root / "production" / "attempts.jsonl").read_text().splitlines()
    ]
    summary = {
        "status": "COMPLETE", "panel_size": len(panel),
        "panel_component_counts": panel["panel_component"].value_counts().to_dict(),
        "model_alias": MODEL_ALIAS, "exact_model_version": smoke["exact_model_version"],
        "rep_rows": len(reps), "third_rep_anchors": int(reps["_rep"].eq(3).sum()),
        "primary_full_tuple_agreement_rate": (
            1 - int(reps["_rep"].eq(3).sum()) / len(panel)
        ),
        "target_match_counts": result["target_match"].value_counts().to_dict(),
        "sequence_status_counts": result["sequence_status"].value_counts().to_dict(),
        "coj_2019_state_counts": result["coj_2019_state"].value_counts().to_dict(),
        "coj_2023_state_counts": result["coj_2023_state"].value_counts().to_dict(),
        "frame_label_rows": len(outputs["agent_r0_frame_labels"]),
        "install_interval_rows": len(outputs["agent_r0_install_intervals"]),
        "coj_label_rows": len(outputs["agent_coj_frame_labels"]),
        "v5_overlap": len(overlap),
        "agent_v5_status_agreement": int(
            overlap["sequence_status"].eq(overlap["v5_sequence_status"]).sum()
        ),
        "agent_v5_exact_status_frame_agreement": int(
            (
                overlap["sequence_status"].eq(overlap["v5_sequence_status"])
                & overlap["first_pv_frame"].eq(overlap["v5_first_pv_frame"])
            ).sum()
        ),
        "agent_low_pilot_status_agreement": int(
            result["sequence_status"].eq(
                result["low_pilot_sequence_status"]
            ).sum()
        ),
        "agent_low_pilot_exact_status_frame_agreement": int(
            (
                result["sequence_status"].eq(
                    result["low_pilot_sequence_status"]
                )
                & result["first_pv_frame"].eq(
                    result["low_pilot_first_pv_frame"]
                )
            ).sum()
        ),
        "attempt_rows": len(attempts),
        "retryable_failure_rows": sum(row["status"] != "VALID" for row in attempts),
        "finished_utc": datetime.now(timezone.utc).isoformat(),
    }
    v3.atomic_json(out_root / "summary.json", summary)
    lock = json.loads((out_root / "RUN_LOCK.json").read_text())
    lock["finished_utc"] = summary["finished_utc"]
    v3.atomic_json(out_root / "RUN_LOCK.json", lock)
    v3.artifact_manifest(out_root)
    return summary


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    default = DATA_ROOT / RUN_ID
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    prepare_parser = sub.add_parser("prepare")
    prepare_parser.add_argument("--out-root", type=Path, default=default)
    for name in ("smoke", "production"):
        command = sub.add_parser(name)
        command.add_argument("--out-root", type=Path, default=default)
        command.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
        command.add_argument("--qps", type=float, default=DEFAULT_QPS)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.command == "prepare":
        result = prepare(args.out_root)
    elif args.command == "smoke":
        result = run_smoke(args.out_root, args.workers, args.qps)
    else:
        result = run_production(args.out_root, args.workers, args.qps)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
