#!/usr/bin/env python3
"""V5 structured recovery for V4 rejected short-gap windows.

This runner reviews only the V4 ``rejected_shortgap`` population.  It restores
additive frame-label and censoring/interval sidecars without changing the R0
manifest and without reintroducing the V3 six-factor gate.
"""

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
from scripts.temporal import run_r4_shortgap_window_review as v4
from scripts.validation.gemini_solar_image_review import RateLimiter
from scripts.validation.issue29_coj_external_robustness import client_config

RUN_ID = "r4_shortgap_window_recovery_v5"
SOURCE_RUN_ID = "r4_shortgap_window_review_v4"
MODEL_ALIAS = "gemini-3.5-flash-low"
EXPECTED_POPULATION = 1196
DEFAULT_WORKERS = 30
DEFAULT_QPS = 6.0
MAX_OUTPUT_TOKENS = 8192
MAX_ATTEMPTS = 6
RECOVERY_RULE_VERSION = "r4_shortgap_recovery_rules_v1@2026-07-24"
STRIP_SLOT_ORDER = (
    "preceding_1", "preceding_2", "earlier", "later",
    "following_1", "following_2",
)
FRAME_IDS = tuple(f"F{index:02d}" for index in range(1, 7))
SEQUENCE_STATUSES = (
    "monotonic_install",
    "already_present_before_F01",
    "not_present_through_F06",
    "present_to_absent_or_nonmonotonic",
    "unreviewable",
)
FIRST_PV_FRAMES = (
    "before_F01", *FRAME_IDS, "after_F06", "none", "unclear",
)

PROMPT = """Recover the visible rooftop-PV sequence for one V4-rejected window.

Images are in this exact order:
1. Tight EARLIER and LATER pair.
2. Context EARLIER and LATER pair.
3. Chronological strip F01 through F06.

The strip is authoritative for frame order. A grey crossed slot is blank and
contains no source frame. Judge the same target roof in every nonblank slot.
Do not classify solar water heaters, skylights, vents, glare, shadows, or a
neighbouring roof as photovoltaic modules.

Choose exactly one sequence:
- monotonic_install: PV is absent, then first appears at one readable nonblank
  F01..F06, and remains present in all later readable nonblank frames.
- already_present_before_F01: PV is present in every readable nonblank frame,
  so the installation predates the visible strip.
- not_present_through_F06: PV is absent in every readable nonblank frame.
- present_to_absent_or_nonmonotonic: readable frames contradict one monotonic
  absent-to-present installation sequence.
- unreviewable: target correspondence or visibility is insufficient.

For monotonic_install, first_pv_frame must be the exact first nonblank F01..F06
where PV is present. For already_present_before_F01 use before_F01. For
not_present_through_F06 use after_F06 or none. For nonmonotonic or unreviewable
use unclear. Never choose a blank slot as first_pv_frame. Return JSON only."""

RESPONSE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["sequence_status", "first_pv_frame", "confidence", "reason"],
    "properties": {
        "sequence_status": {"type": "string", "enum": list(SEQUENCE_STATUSES)},
        "first_pv_frame": {"type": "string", "enum": list(FIRST_PV_FRAMES)},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "reason": {"type": "string", "minLength": 1},
    },
}

_LOCK = threading.Lock()


def _canonical_sha(value: Any) -> str:
    return v3.sha256_bytes(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    )


def validate_response(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the native response and all status/frame contradictions."""
    if not isinstance(value, Mapping):
        raise ValueError("response must be an object")
    expected = {"sequence_status", "first_pv_frame", "confidence", "reason"}
    if set(value) != expected:
        raise ValueError("response fields differ from V5 schema")
    status = str(value["sequence_status"])
    frame = str(value["first_pv_frame"])
    if status not in SEQUENCE_STATUSES or frame not in FIRST_PV_FRAMES:
        raise ValueError("out-of-vocabulary recovery response")
    allowed = {
        "monotonic_install": set(FRAME_IDS),
        "already_present_before_F01": {"before_F01"},
        "not_present_through_F06": {"after_F06", "none"},
        "present_to_absent_or_nonmonotonic": {"unclear"},
        "unreviewable": {"unclear"},
    }
    if frame not in allowed[status]:
        raise ValueError(f"contradictory sequence_status/first_pv_frame: {status}/{frame}")
    confidence = value["confidence"]
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        raise ValueError("confidence must be numeric")
    if not 0 <= float(confidence) <= 1:
        raise ValueError("confidence outside [0,1]")
    reason = str(value["reason"]).strip()
    if not reason:
        raise ValueError("reason cannot be blank")
    return {
        "sequence_status": status,
        "first_pv_frame": frame,
        "confidence": float(confidence),
        "reason": reason,
    }


def _valid_frame_ids(packet: Mapping[str, Any]) -> set[str]:
    return {
        str(item["frame_id"])
        for item in list(packet["slot_provenance"])
        if bool(item["source_present"])
    }


def validate_packet_response(
    value: Mapping[str, Any], packet: Mapping[str, Any],
) -> dict[str, Any]:
    parsed = validate_response(value)
    if (
        parsed["sequence_status"] == "monotonic_install"
        and parsed["first_pv_frame"] not in _valid_frame_ids(packet)
    ):
        raise ValueError(
            f"first_pv_frame {parsed['first_pv_frame']} refers to a blank strip slot"
        )
    return parsed


def needs_third_rep(reps: Sequence[Mapping[str, Any]]) -> bool:
    if len(reps) != 2:
        raise ValueError("exactly two primary reps required")
    keys = ("sequence_status", "first_pv_frame")
    return any(reps[0][key] != reps[1][key] for key in keys)


def consensus(reps: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Require two votes for the exact (status, first-frame) tuple."""
    if len(reps) not in {2, 3}:
        raise ValueError("V5 consensus requires two or three reps")
    groups: dict[tuple[str, str], list[Mapping[str, Any]]] = {}
    for rep in reps:
        key = (str(rep["sequence_status"]), str(rep["first_pv_frame"]))
        groups.setdefault(key, []).append(rep)
    winner = max(groups, key=lambda key: len(groups[key]))
    agreeing = groups[winner]
    if len(agreeing) < 2:
        return {
            "sequence_status": "unreviewable",
            "first_pv_frame": "unclear",
            "agreeing_reps": [],
            "confidence": None,
            "reason": "three-way status/first-frame disagreement",
            "rep_count": len(reps),
            "consensus_source": "NO_TWO_VOTE_CONSENSUS",
        }
    return {
        "sequence_status": winner[0],
        "first_pv_frame": winner[1],
        "agreeing_reps": [int(rep["_rep"]) for rep in agreeing],
        "confidence": sum(float(rep["confidence"]) for rep in agreeing) / len(agreeing),
        "reason": " | ".join(dict.fromkeys(str(rep["reason"]) for rep in agreeing)),
        "rep_count": len(reps),
        "consensus_source": "GEMINI_V5_TWO_VOTE_CONSENSUS",
    }


def _slot_provenance(
    packet: Mapping[str, Any], candidate: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Reconstruct F01..F06 from frozen packet objects, retaining blank slots."""
    sources = {str(item["slot"]): dict(item) for item in list(packet["source_objects"])}
    renders = {
        str(item["slot"]): dict(item)
        for item in list(packet["render_objects"])
        if item.get("kind") == "tight_marker_free"
    }
    if set(sources) - set(STRIP_SLOT_ORDER) or set(renders) - set(STRIP_SLOT_ORDER):
        raise ValueError(f"{candidate['anchor_id']}: unknown strip provenance slot")
    rows = []
    previous_date: str | None = None
    for frame_id, slot in zip(FRAME_IDS, STRIP_SLOT_ORDER):
        raw_date = candidate.get(f"{slot}_capture_date")
        date = None if raw_date is None or pd.isna(raw_date) else str(raw_date)[:10]
        source = sources.get(slot)
        render = renders.get(slot)
        present = source is not None
        if present != (date is not None) or present != (render is not None):
            raise ValueError(f"{candidate['anchor_id']}/{slot}: packet/candidate blank mismatch")
        expected_sha = candidate.get(f"{slot}_src_tiff_sha256")
        if present and str(source["sha256"]) != str(expected_sha):
            raise ValueError(f"{candidate['anchor_id']}/{slot}: source SHA mismatch")
        if date is not None and previous_date is not None and date < previous_date:
            raise ValueError(f"{candidate['anchor_id']}: nonchronological strip provenance")
        if date is not None:
            previous_date = date
        rows.append({
            "frame_id": frame_id,
            "source_slot": slot,
            "source_present": present,
            "capture_date": date,
            "src_tiff_path": None if source is None else str(source["path"]),
            "src_tiff_sha256": None if source is None else str(source["sha256"]),
            "render_path": None if render is None else str(render["path"]),
            "render_sha256": None if render is None else str(render["sha256"]),
        })
    return rows


def _prepare_identity(source_root: Path) -> dict[str, Any]:
    paths = {
        "source_artifacts_manifest": source_root / "artifacts.sha256",
        "source_window_review": source_root / "window_review.parquet",
        "source_packet_index": source_root / "packet_index.parquet",
        "source_candidate_manifest": source_root / "candidate_manifest.parquet",
        "source_run_lock": source_root / "RUN_LOCK.json",
    }
    if any(not path.is_file() for path in paths.values()):
        missing = [name for name, path in paths.items() if not path.is_file()]
        raise FileNotFoundError(f"missing V4 inputs: {missing}")
    return {f"{name}_sha256": v3.sha256_file(path) for name, path in paths.items()}


def _config_identity() -> dict[str, Any]:
    return {
        "prompt_sha256": v3.sha256_bytes(PROMPT.encode()),
        "response_schema_sha256": _canonical_sha(RESPONSE_SCHEMA),
        "recovery_rule_version": RECOVERY_RULE_VERSION,
        "recovery_rule_sha256": v3.sha256_bytes(RECOVERY_RULE_VERSION.encode()),
        "model_alias": MODEL_ALIAS,
        "max_output_tokens": MAX_OUTPUT_TOKENS,
    }


def assert_run_identity(out_root: Path, lock: Mapping[str, Any]) -> None:
    """Fail before calls if code-level or prepared-input identity has drifted."""
    expected = {"run_id": RUN_ID, **_config_identity()}
    mismatches = [
        key for key, value in expected.items() if lock.get(key) != value
    ]
    local_files = {
        "packet_index_sha256": out_root / "packet_index.parquet",
        "candidate_manifest_sha256": out_root / "candidate_manifest.parquet",
        "source_v4_rejected_sha256": out_root / "source_v4_rejected.parquet",
    }
    for key, path in local_files.items():
        if not path.is_file() or lock.get(key) != v3.sha256_file(path):
            mismatches.append(key)
    if mismatches:
        raise ValueError(f"V5 run identity drift: {sorted(set(mismatches))}")


def prepare_v5(*, source_root: Path, out_root: Path) -> dict[str, Any]:
    identity = _prepare_identity(source_root)
    existing_lock = out_root / "RUN_LOCK.json"
    if existing_lock.exists():
        lock = json.loads(existing_lock.read_text())
        if any(
            lock.get(key) != value for key, value in identity.items()
        ):
            raise ValueError("existing V5 output has a different resume identity")
        assert_run_identity(out_root, lock)
        return {"status": "ALREADY_PREPARED", "packets": lock["population"]}

    review = pd.read_parquet(source_root / "window_review.parquet")
    rejected = review.loc[review["verdict"].eq("rejected_shortgap")].copy()
    if len(rejected) != EXPECTED_POPULATION or rejected["anchor_id"].nunique() != EXPECTED_POPULATION:
        raise ValueError(f"V4 rejected population is {len(rejected)}, expected 1196")
    rejected_ids = set(rejected["anchor_id"].astype(str))
    source_packets = pd.read_parquet(source_root / "packet_index.parquet")
    source_candidates = pd.read_parquet(source_root / "candidate_manifest.parquet")
    packets = source_packets.loc[source_packets["anchor_id"].astype(str).isin(rejected_ids)].copy()
    candidates = source_candidates.loc[
        source_candidates["anchor_id"].astype(str).isin(rejected_ids)
    ].copy()
    if len(packets) != EXPECTED_POPULATION or len(candidates) != EXPECTED_POPULATION:
        raise ValueError("V5 packet/candidate subsets do not match rejected population")

    config_identity = _config_identity()
    prompt_sha = config_identity["prompt_sha256"]
    schema_sha = config_identity["response_schema_sha256"]
    rules_sha = config_identity["recovery_rule_sha256"]
    candidate_by_id = candidates.set_index("anchor_id", drop=False)
    slot_rows = []
    for packet in packets.to_dict("records"):
        candidate = candidate_by_id.loc[packet["anchor_id"]].to_dict()
        slot_rows.append(_slot_provenance(packet, candidate))
    packets["source_v4_packet_sha256"] = packets["packet_sha256"]
    packets["slot_provenance"] = slot_rows
    packets["prompt"] = PROMPT
    packets["prompt_sha256"] = prompt_sha
    packets["response_schema_sha256"] = schema_sha
    packets["recovery_rule_sha256"] = rules_sha
    packets["packet_sha256"] = [
        v3.sha256_bytes(
            f"{RUN_ID}\0{source_sha}\0{prompt_sha}\0{schema_sha}\0{rules_sha}".encode()
        )
        for source_sha in packets["source_v4_packet_sha256"].astype(str)
    ]
    packets = packets.sort_values("anchor_id").reset_index(drop=True)
    candidates = candidates.sort_values("anchor_id").reset_index(drop=True)
    rejected = rejected.sort_values("anchor_id").reset_index(drop=True)
    v3.atomic_parquet(out_root / "packet_index.parquet", packets)
    v3.atomic_parquet(out_root / "candidate_manifest.parquet", candidates)
    v3.atomic_parquet(out_root / "source_v4_rejected.parquet", rejected)
    smoke_ids = packets["anchor_id"].astype(str).head(10).tolist()
    lock = {
        "run_id": RUN_ID,
        "source_run_id": SOURCE_RUN_ID,
        "source_root": str(source_root),
        **identity,
        "population": EXPECTED_POPULATION,
        "packet_index_sha256": v3.sha256_file(out_root / "packet_index.parquet"),
        "candidate_manifest_sha256": v3.sha256_file(out_root / "candidate_manifest.parquet"),
        "source_v4_rejected_sha256": v3.sha256_file(out_root / "source_v4_rejected.parquet"),
        **config_identity,
        "transport": {"workers": DEFAULT_WORKERS, "qps": DEFAULT_QPS},
        "smoke_anchor_ids": smoke_ids,
        "strip_slot_order": dict(zip(FRAME_IDS, STRIP_SLOT_ORDER)),
        "consensus_contract": (
            "two primary reps; third iff sequence_status or first_pv_frame differs; "
            "two votes required for exact tuple"
        ),
    }
    v3.atomic_json(existing_lock, lock)
    v3.artifact_manifest(out_root)
    return {"status": "PREPARED", "packets": len(packets), "smoke_anchors": 10}


def _admit_version(lock: dict[str, Any], returned: Any) -> str:
    version = str(returned or "").strip()
    if not version:
        raise v3.ModelVersionDrift("MODEL_VERSION_DRIFT: missing exact version")
    with _LOCK:
        expected = lock.get("exact_model_version")
        if expected is None:
            lock["exact_model_version"] = version
        elif expected != version:
            raise v3.ModelVersionDrift(
                f"MODEL_VERSION_DRIFT: expected {expected!r}, got {version!r}"
            )
    return version


def _compact_raw(raw: Mapping[str, Any]) -> dict[str, Any]:
    candidates = raw.get("candidates") or []
    return {
        "modelVersion": raw.get("modelVersion"),
        "responseId": raw.get("responseId"),
        "usageMetadata": raw.get("usageMetadata"),
        "finishReasons": [item.get("finishReason") for item in candidates],
        "transport_retries": raw.get("_transport_retries", 0),
    }


def _validate_packet_files(packet: Mapping[str, Any]) -> None:
    for name in ("boundary_tight", "boundary_context", "temporal_strip"):
        path = Path(str(packet[f"{name}_path"]))
        if not path.is_file() or v3.sha256_file(path) != packet[f"{name}_sha256"]:
            raise ValueError(f"{packet['anchor_id']}: {name} integrity failure")


def run_rep(
    *,
    packet: Mapping[str, Any],
    rep: int,
    scope: str,
    out_root: Path,
    lock: dict[str, Any],
    caller: Callable[..., tuple[str, Mapping[str, Any]]],
) -> dict[str, Any]:
    anchor_id = str(packet["anchor_id"])
    rep_path = out_root / scope / "reps" / anchor_id / f"rep{rep}.json"
    identity = {
        "_run_id": RUN_ID,
        "_packet_sha256": str(packet["packet_sha256"]),
        "_model_alias": MODEL_ALIAS,
        "_prompt_sha256": str(packet["prompt_sha256"]),
        "_response_schema_sha256": str(packet["response_schema_sha256"]),
        "_recovery_rule_sha256": str(packet["recovery_rule_sha256"]),
    }
    if rep_path.exists():
        payload = json.loads(rep_path.read_text())
        if any(payload.get(key) != value for key, value in identity.items()):
            raise ValueError(f"{anchor_id}: resume identity mismatch")
        _admit_version(lock, payload.get("_exact_model_version"))
        return payload
    _validate_packet_files(packet)
    attempts_path = out_root / scope / "attempts.jsonl"
    prior = []
    if attempts_path.exists():
        for line in attempts_path.read_text().splitlines():
            record = json.loads(line)
            if str(record.get("anchor_id")) == anchor_id and int(record.get("rep", -1)) == rep:
                prior.append(int(record.get("attempt", 0)))
    first_attempt = max(prior, default=0) + 1
    last_error = ""
    images = [
        Path(packet["boundary_tight_path"]),
        Path(packet["boundary_context_path"]),
        Path(packet["temporal_strip_path"]),
    ]
    for attempt in range(first_attempt, MAX_ATTEMPTS + 1):
        salt = v3.sha256_bytes(
            f"{RUN_ID}\0{scope}\0{anchor_id}\0{rep}\0{attempt}\0"
            f"{packet['packet_sha256']}".encode()
        )
        started = time.time()
        raw: Mapping[str, Any] = {}
        try:
            text, raw = caller(
                image_paths=images,
                prompt=PROMPT,
                model=MODEL_ALIAS,
                max_tokens=MAX_OUTPUT_TOKENS,
                response_mime_type="application/json",
                response_schema=RESPONSE_SCHEMA,
                routing_salt=salt,
            )
            exact = _admit_version(lock, raw.get("modelVersion"))
            parsed = validate_packet_response(json.loads(text), packet)
            parsed.update({
                **identity,
                "_rep": rep,
                "_attempt": attempt,
                "_routing_salt": salt,
                "_exact_model_version": exact,
            })
            v3.atomic_json(rep_path, parsed)
            v3.append_jsonl(attempts_path, {
                "anchor_id": anchor_id,
                "rep": rep,
                "attempt": attempt,
                "status": "VALID",
                "routing_salt": salt,
                "packet_sha256": packet["packet_sha256"],
                "prompt_sha256": packet["prompt_sha256"],
                "response_schema_sha256": packet["response_schema_sha256"],
                "recovery_rule_sha256": packet["recovery_rule_sha256"],
                "model_alias": MODEL_ALIAS,
                "exact_model_version": exact,
                "max_output_tokens": MAX_OUTPUT_TOKENS,
                "elapsed_seconds": time.time() - started,
                "response": parsed,
                "raw": _compact_raw(raw),
            })
            v3.atomic_json(out_root / "RUN_LOCK.json", lock)
            return parsed
        except v3.ModelVersionDrift:
            raise
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            v3.append_jsonl(attempts_path, {
                "anchor_id": anchor_id,
                "rep": rep,
                "attempt": attempt,
                "status": "RETRYABLE_FAILURE",
                "routing_salt": salt,
                "packet_sha256": packet["packet_sha256"],
                "model_alias": MODEL_ALIAS,
                "max_output_tokens": MAX_OUTPUT_TOKENS,
                "elapsed_seconds": time.time() - started,
                "error": last_error,
                "raw": _compact_raw(raw),
            })
    raise RuntimeError(
        f"{anchor_id} rep {rep} failed after {MAX_ATTEMPTS} attempts: {last_error}"
    )


def run_packets(
    *,
    packets: Sequence[Mapping[str, Any]],
    out_root: Path,
    scope: str,
    workers: int,
    qps: float,
    allow_third: bool,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    lock_path = out_root / "RUN_LOCK.json"
    lock = json.loads(lock_path.read_text())
    assert_run_identity(out_root, lock)
    caller = v3.native_caller(client_config(MODEL_ALIAS), RateLimiter(qps))
    rep_rows: list[dict[str, Any]] = []
    decision_rows: list[dict[str, Any]] = []

    def one(packet: Mapping[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        reps = [
            run_rep(
                packet=packet, rep=rep, scope=scope, out_root=out_root,
                lock=lock, caller=caller,
            )
            for rep in (1, 2)
        ]
        if allow_third and needs_third_rep(reps):
            reps.append(run_rep(
                packet=packet, rep=3, scope=scope, out_root=out_root,
                lock=lock, caller=caller,
            ))
        result = consensus(reps)
        return reps, {
            "anchor_id": packet["anchor_id"],
            **result,
            "decision_source": "GEMINI_V5_STRUCTURED_RECOVERY",
            "packet_sha256": packet["packet_sha256"],
            "source_v4_packet_sha256": packet["source_v4_packet_sha256"],
            "temporal_strip_sha256": packet["temporal_strip_sha256"],
            "model_alias": MODEL_ALIAS,
            "exact_model_version": reps[0]["_exact_model_version"],
            "prompt_sha256": packet["prompt_sha256"],
            "response_schema_sha256": packet["response_schema_sha256"],
            "recovery_rule_version": RECOVERY_RULE_VERSION,
            "recovery_rule_sha256": packet["recovery_rule_sha256"],
        }

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = [pool.submit(one, packet) for packet in packets]
        for index, future in enumerate(as_completed(futures), 1):
            reps, decision = future.result()
            rep_rows.extend(reps)
            decision_rows.append(decision)
            if index % 100 == 0 or index == len(futures):
                print(f"[{scope}] completed {index}/{len(futures)}", flush=True)
    v3.atomic_json(lock_path, lock)
    reps_frame = pd.DataFrame(rep_rows).sort_values(["_packet_sha256", "_rep"])
    decisions_frame = pd.DataFrame(decision_rows).sort_values("anchor_id")
    return reps_frame, decisions_frame


def run_smoke(*, out_root: Path, workers: int, qps: float) -> dict[str, Any]:
    lock = json.loads((out_root / "RUN_LOCK.json").read_text())
    packets = pd.read_parquet(out_root / "packet_index.parquet")
    smoke_ids = set(lock["smoke_anchor_ids"])
    smoke = [
        row for row in packets.to_dict("records")
        if str(row["anchor_id"]) in smoke_ids
    ]
    if len(smoke) != 10:
        raise ValueError("smoke did not resolve 10 locked rejected anchors")
    reps, decisions = run_packets(
        packets=smoke, out_root=out_root, scope="smoke",
        workers=workers, qps=qps, allow_third=False,
    )
    if len(reps) != 20:
        raise RuntimeError("smoke did not complete exactly 20 primary reps")
    v3.atomic_parquet(out_root / "smoke" / "rep_verdicts.parquet", reps)
    v3.atomic_parquet(out_root / "smoke" / "consensus.parquet", decisions)
    status = {
        "status": "SMOKE_PASS",
        "anchors": 10,
        "reps": 20,
        "model_alias": MODEL_ALIAS,
        "exact_model_version": json.loads(
            (out_root / "RUN_LOCK.json").read_text()
        )["exact_model_version"],
    }
    v3.atomic_json(out_root / "smoke" / "status.json", status)
    v3.artifact_manifest(out_root)
    return status


LABEL_COLUMNS = [
    "anchor_id", "frame_id", "source_slot", "capture_date", "src_tiff_path",
    "src_tiff_sha256", "render_path", "render_sha256", "pv_label",
    "label_source", "sequence_status", "first_pv_frame", "agreeing_reps",
    "rep_count", "confidence", "model_alias", "exact_model_version",
    "prompt_sha256", "response_schema_sha256", "packet_sha256",
    "source_v4_packet_sha256", "temporal_strip_sha256",
    "recovery_rule_version", "recovery_rule_sha256",
]
INTERVAL_COLUMNS = [
    "anchor_id", "last_absent_frame", "last_absent_slot",
    "last_absent_capture_date", "last_absent_src_tiff_sha256",
    "first_present_frame", "first_present_slot", "first_present_capture_date",
    "first_present_src_tiff_sha256", "interval_days", "boundary_moved",
    "original_earlier_capture_date", "original_later_capture_date",
    "agreeing_reps", "rep_count", "confidence", "model_alias",
    "exact_model_version", "prompt_sha256", "response_schema_sha256",
    "packet_sha256", "source_v4_packet_sha256", "temporal_strip_sha256",
    "recovery_rule_version", "recovery_rule_sha256",
]
CENSOR_COLUMNS = [
    "anchor_id", "censor_type", "boundary_frame", "boundary_slot",
    "boundary_capture_date", "boundary_src_tiff_sha256", "visible_frame_count",
    "sequence_status", "first_pv_frame", "agreeing_reps", "rep_count",
    "confidence", "model_alias", "exact_model_version", "prompt_sha256",
    "response_schema_sha256", "packet_sha256", "source_v4_packet_sha256",
    "temporal_strip_sha256", "recovery_rule_version", "recovery_rule_sha256",
]
MANUAL_COLUMNS = [
    "anchor_id", "queue_reason", "sequence_status", "first_pv_frame",
    "agreeing_reps", "rep_count", "confidence", "reason", "model_alias",
    "exact_model_version", "prompt_sha256", "response_schema_sha256",
    "packet_sha256", "source_v4_packet_sha256", "temporal_strip_sha256",
    "recovery_rule_version", "recovery_rule_sha256",
]


def _common_provenance(decision: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: decision[key]
        for key in (
            "agreeing_reps", "rep_count", "confidence", "model_alias",
            "exact_model_version", "prompt_sha256", "response_schema_sha256",
            "packet_sha256", "source_v4_packet_sha256", "temporal_strip_sha256",
            "recovery_rule_version", "recovery_rule_sha256",
        )
    }


def build_recovery_sidecars(
    *,
    packets: pd.DataFrame,
    candidates: pd.DataFrame,
    decisions: pd.DataFrame,
) -> dict[str, pd.DataFrame]:
    """Apply the locked deterministic recovery rules to consensus only."""
    if len(decisions) != EXPECTED_POPULATION or decisions["anchor_id"].nunique() != EXPECTED_POPULATION:
        raise ValueError("recovery decisions do not cover 1196 anchors exactly once")
    packet_by_id = packets.set_index("anchor_id", drop=False)
    candidate_by_id = candidates.set_index("anchor_id", drop=False)
    label_rows: list[dict[str, Any]] = []
    interval_rows: list[dict[str, Any]] = []
    left_rows: list[dict[str, Any]] = []
    right_rows: list[dict[str, Any]] = []
    manual_rows: list[dict[str, Any]] = []

    for decision in decisions.to_dict("records"):
        anchor_id = decision["anchor_id"]
        slots = [
            dict(item)
            for item in list(packet_by_id.loc[anchor_id, "slot_provenance"])
            if bool(item["source_present"])
        ]
        provenance = _common_provenance(decision)
        status = decision["sequence_status"]
        frame = decision["first_pv_frame"]
        if status == "monotonic_install":
            first_index = FRAME_IDS.index(frame)
            for item in slots:
                label = "present" if FRAME_IDS.index(item["frame_id"]) >= first_index else "absent"
                label_rows.append({
                    "anchor_id": anchor_id, **item, "pv_label": label,
                    "label_source": "GEMINI_V5_MONOTONIC_RECOVERY",
                    "sequence_status": status, "first_pv_frame": frame,
                    **provenance,
                })
            before = [
                item for item in slots
                if FRAME_IDS.index(item["frame_id"]) < first_index
            ]
            present = next(item for item in slots if item["frame_id"] == frame)
            if before:
                absent = before[-1]
                candidate = candidate_by_id.loc[anchor_id]
                interval_rows.append({
                    "anchor_id": anchor_id,
                    "last_absent_frame": absent["frame_id"],
                    "last_absent_slot": absent["source_slot"],
                    "last_absent_capture_date": absent["capture_date"],
                    "last_absent_src_tiff_sha256": absent["src_tiff_sha256"],
                    "first_present_frame": present["frame_id"],
                    "first_present_slot": present["source_slot"],
                    "first_present_capture_date": present["capture_date"],
                    "first_present_src_tiff_sha256": present["src_tiff_sha256"],
                    "interval_days": (
                        pd.Timestamp(present["capture_date"])
                        - pd.Timestamp(absent["capture_date"])
                    ).days,
                    "boundary_moved": not (
                        absent["capture_date"] == str(candidate["earlier_capture_date"])[:10]
                        and present["capture_date"] == str(candidate["later_capture_date"])[:10]
                    ),
                    "original_earlier_capture_date": str(candidate["earlier_capture_date"])[:10],
                    "original_later_capture_date": str(candidate["later_capture_date"])[:10],
                    **provenance,
                })
        elif status == "already_present_before_F01":
            for item in slots:
                label_rows.append({
                    "anchor_id": anchor_id, **item, "pv_label": "present",
                    "label_source": "GEMINI_V5_LEFT_CENSORED_RECOVERY",
                    "sequence_status": status, "first_pv_frame": frame,
                    **provenance,
                })
            boundary = slots[0]
            left_rows.append({
                "anchor_id": anchor_id, "censor_type": "left",
                "boundary_frame": boundary["frame_id"],
                "boundary_slot": boundary["source_slot"],
                "boundary_capture_date": boundary["capture_date"],
                "boundary_src_tiff_sha256": boundary["src_tiff_sha256"],
                "visible_frame_count": len(slots),
                "sequence_status": status, "first_pv_frame": frame,
                **provenance,
            })
        elif status == "not_present_through_F06":
            for item in slots:
                label_rows.append({
                    "anchor_id": anchor_id, **item, "pv_label": "absent",
                    "label_source": "GEMINI_V5_RIGHT_CENSORED_RECOVERY",
                    "sequence_status": status, "first_pv_frame": frame,
                    **provenance,
                })
            boundary = slots[-1]
            right_rows.append({
                "anchor_id": anchor_id, "censor_type": "right",
                "boundary_frame": boundary["frame_id"],
                "boundary_slot": boundary["source_slot"],
                "boundary_capture_date": boundary["capture_date"],
                "boundary_src_tiff_sha256": boundary["src_tiff_sha256"],
                "visible_frame_count": len(slots),
                "sequence_status": status, "first_pv_frame": frame,
                **provenance,
            })
        elif status == "present_to_absent_or_nonmonotonic":
            manual_rows.append({
                "anchor_id": anchor_id,
                "queue_reason": "NONMONOTONIC_SEQUENCE_EXCLUDED_FROM_INTERVAL_LOSS",
                "sequence_status": status, "first_pv_frame": frame,
                "reason": decision["reason"], **provenance,
            })
        elif status != "unreviewable":
            raise ValueError(f"unknown recovery status {status!r}")

    def frame(rows: list[dict[str, Any]], columns: list[str]) -> pd.DataFrame:
        return pd.DataFrame(rows, columns=columns).sort_values(
            [column for column in ("anchor_id", "frame_id") if column in columns]
        ).reset_index(drop=True)

    return {
        "recovered_frame_labels": frame(label_rows, LABEL_COLUMNS),
        "recovered_install_intervals": frame(interval_rows, INTERVAL_COLUMNS),
        "left_censored": frame(left_rows, CENSOR_COLUMNS),
        "right_censored": frame(right_rows, CENSOR_COLUMNS),
        "manual_review_queue": frame(manual_rows, MANUAL_COLUMNS),
    }


def run_production(*, out_root: Path, workers: int, qps: float) -> dict[str, Any]:
    smoke_status = json.loads((out_root / "smoke" / "status.json").read_text())
    if smoke_status.get("status") != "SMOKE_PASS":
        raise RuntimeError("production requires a passed authenticated smoke")
    packets = pd.read_parquet(out_root / "packet_index.parquet")
    candidates = pd.read_parquet(out_root / "candidate_manifest.parquet")
    reps, decisions = run_packets(
        packets=packets.to_dict("records"), out_root=out_root, scope="production",
        workers=workers, qps=qps, allow_third=True,
    )
    if set(decisions["exact_model_version"]) != {smoke_status["exact_model_version"]}:
        raise v3.ModelVersionDrift("production exact version differs from smoke")
    source = pd.read_parquet(out_root / "source_v4_rejected.parquet")
    source_columns = source[[
        "anchor_id", "split", "gap_days", "strict_under_45_days",
        "earlier_capture_date", "later_capture_date", "verdict",
    ]].rename(columns={"verdict": "source_v4_verdict"})
    decisions = source_columns.merge(decisions, on="anchor_id", validate="one_to_one")
    v3.atomic_parquet(out_root / "rep_verdicts.parquet", reps)
    v3.atomic_parquet(out_root / "recovery_verdicts.parquet", decisions)
    sidecars = build_recovery_sidecars(
        packets=packets, candidates=candidates, decisions=decisions,
    )
    for name, frame in sidecars.items():
        v3.atomic_parquet(out_root / f"{name}.parquet", frame)

    source_v4 = pd.read_parquet(
        Path(json.loads((out_root / "RUN_LOCK.json").read_text())["source_root"])
        / "window_review.parquet"
    )
    strict_v4 = source_v4.loc[source_v4["strict_under_45_days"]]
    status_counts = decisions["sequence_status"].value_counts().to_dict()
    consensus_source_counts = decisions["consensus_source"].value_counts().to_dict()
    intervals = sidecars["recovered_install_intervals"]
    interval_anchor_ids = set(intervals["anchor_id"].astype(str))
    monotonic = decisions.loc[decisions["sequence_status"].eq("monotonic_install")]
    frame_only_monotonic = int(
        (~monotonic["anchor_id"].astype(str).isin(interval_anchor_ids)).sum()
    )
    attempt_records = [
        json.loads(line)
        for line in (out_root / "production" / "attempts.jsonl").read_text().splitlines()
    ]
    summary = {
        "status": "COMPLETE",
        "run_id": RUN_ID,
        "source_run_id": SOURCE_RUN_ID,
        "model_alias": MODEL_ALIAS,
        "exact_model_version": smoke_status["exact_model_version"],
        "population": EXPECTED_POPULATION,
        "rep_rows": len(reps),
        "third_rep_anchors": int(reps["_rep"].eq(3).sum()),
        "primary_agreement_rate": 1 - int(reps["_rep"].eq(3).sum()) / EXPECTED_POPULATION,
        "sequence_status_counts": status_counts,
        "consensus_source_counts": consensus_source_counts,
        "production_attempt_rows": len(attempt_records),
        "production_retryable_failure_rows": sum(
            record["status"] != "VALID" for record in attempt_records
        ),
        "v4_strict_under_45_confirmed": int(
            strict_v4["verdict"].eq("confirmed_shortgap").sum()
        ),
        "v5_recovered_install_intervals": len(intervals),
        "v5_recovered_moved_boundary_intervals": int(
            intervals["boundary_moved"].sum()
        ) if len(intervals) else 0,
        "v5_recovered_original_boundary_intervals": int(
            (~intervals["boundary_moved"]).sum()
        ) if len(intervals) else 0,
        "v5_frame_supervision_only_without_interval_non_censored": frame_only_monotonic,
        "v5_recovered_frame_label_rows": len(sidecars["recovered_frame_labels"]),
        "v5_recovered_frame_label_anchors": int(
            sidecars["recovered_frame_labels"]["anchor_id"].nunique()
        ),
        "v5_left_censored": len(sidecars["left_censored"]),
        "v5_right_censored": len(sidecars["right_censored"]),
        "v5_nonmonotonic_manual_review": len(sidecars["manual_review_queue"]),
        "v5_unreviewable": status_counts.get("unreviewable", 0),
        "v4_outside_strict_under_45_inherited_rows": int(
            (~source_v4["strict_under_45_days"]).sum()
        ),
        "v4_outside_strict_under_45_verdict_counts": source_v4.loc[
            ~source_v4["strict_under_45_days"], "verdict"
        ].value_counts().to_dict(),
        "sidecar_contract": "additive_only; R0 manifest not modified",
        "finished_utc": datetime.now(timezone.utc).isoformat(),
    }
    v3.atomic_json(out_root / "summary.json", summary)
    lock = json.loads((out_root / "RUN_LOCK.json").read_text())
    lock["production_transport"] = {"workers": workers, "qps": qps}
    lock["finished_utc"] = summary["finished_utc"]
    v3.atomic_json(out_root / "RUN_LOCK.json", lock)
    v3.artifact_manifest(out_root)
    return summary


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    default = Path.home() / "zasolar_data/geid_temporal" / RUN_ID
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    prepare = sub.add_parser("prepare")
    prepare.add_argument(
        "--source-root", type=Path,
        default=Path.home() / "zasolar_data/geid_temporal" / SOURCE_RUN_ID,
    )
    prepare.add_argument("--out-root", type=Path, default=default)
    for name in ("smoke", "production"):
        command = sub.add_parser(name)
        command.add_argument("--out-root", type=Path, default=default)
        command.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
        command.add_argument("--qps", type=float, default=DEFAULT_QPS)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.command == "prepare":
        result = prepare_v5(source_root=args.source_root, out_root=args.out_root)
    elif args.command == "smoke":
        result = run_smoke(out_root=args.out_root, workers=args.workers, qps=args.qps)
    else:
        result = run_production(out_root=args.out_root, workers=args.workers, qps=args.qps)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
