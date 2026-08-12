#!/usr/bin/env python3
"""V4 direct review of proposed under-45-day PV installation windows.

This runner intentionally answers one question only: is the proposed window
supported by readable same-roof imagery with PV absent earlier and present
later?  It does not run the V3 six-factor robustness/model-selection gate.
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
from scripts.validation.gemini_solar_image_review import RateLimiter
from scripts.validation.issue29_coj_external_robustness import client_config

RUN_ID = "r4_shortgap_window_review_v4"
SOURCE_RUN_ID = "r4_shortgap_adjudication_v3"
MODEL_ALIAS = "gemini-3.5-flash-low"
EXPECTED_POPULATION = 2146
DEFAULT_WORKERS = 30
DEFAULT_QPS = 6.0
MAX_OUTPUT_TOKENS = 8192
MAX_ATTEMPTS = 6
VERDICTS = ("confirmed_shortgap", "rejected_shortgap", "unreviewable")

PROMPT = """Review one proposed under-45-day rooftop PV installation window.

Images are in this order:
1. Tight EARLIER and LATER pair.
2. Context EARLIER and LATER pair.
3. Chronological strip F01 to F06.

Return confirmed_shortgap only when all are true:
- the EARLIER and LATER views show the same physical roof;
- both endpoints are readable;
- rooftop photovoltaic modules are absent in EARLIER;
- rooftop photovoltaic modules are present in LATER.

Return rejected_shortgap when the same roof and both endpoints are readable but
the endpoint pattern is anything else, including present/present,
absent/absent, or present/absent.

Return unreviewable when the roof match or either endpoint cannot be judged.
Do not treat solar water heaters, skylights, vents, shadows, or neighbouring
roofs as PV. Ignore chronological order as evidence. Return JSON only."""

RESPONSE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["verdict", "confidence", "reason"],
    "properties": {
        "verdict": {"type": "string", "enum": list(VERDICTS)},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "reason": {"type": "string", "minLength": 1},
    },
}

_LOCK = threading.Lock()


def validate_response(value: Mapping[str, Any]) -> dict[str, Any]:
    if set(value) != {"verdict", "confidence", "reason"}:
        raise ValueError("response fields differ from V4 schema")
    verdict = str(value["verdict"])
    if verdict not in VERDICTS:
        raise ValueError(f"unknown verdict {verdict!r}")
    confidence = value["confidence"]
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        raise ValueError("confidence must be numeric")
    if not 0 <= float(confidence) <= 1:
        raise ValueError("confidence outside [0,1]")
    reason = str(value["reason"]).strip()
    if not reason:
        raise ValueError("reason cannot be blank")
    return {"verdict": verdict, "confidence": float(confidence), "reason": reason}


def consensus(reps: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if len(reps) not in {2, 3}:
        raise ValueError("V4 consensus requires two or three reps")
    counts = {verdict: 0 for verdict in VERDICTS}
    for rep in reps:
        counts[str(rep["verdict"])] += 1
    winner, votes = max(counts.items(), key=lambda item: item[1])
    if votes < 2:
        winner = "unreviewable"
        agreeing = []
    else:
        agreeing = [rep for rep in reps if rep["verdict"] == winner]
    return {
        "verdict": winner,
        "agreeing_reps": [int(rep["_rep"]) for rep in agreeing],
        "confidence": (
            sum(float(rep["confidence"]) for rep in agreeing) / len(agreeing)
            if agreeing else None
        ),
        "reason": (
            " | ".join(dict.fromkeys(str(rep["reason"]) for rep in agreeing))
            if agreeing else "three-way disagreement"
        ),
        "rep_count": len(reps),
    }


def needs_third_rep(reps: Sequence[Mapping[str, Any]]) -> bool:
    if len(reps) != 2:
        raise ValueError("exactly two primary reps required")
    return reps[0]["verdict"] != reps[1]["verdict"]


def _compact_raw(raw: Mapping[str, Any]) -> dict[str, Any]:
    candidates = raw.get("candidates") or []
    return {
        "modelVersion": raw.get("modelVersion"),
        "responseId": raw.get("responseId"),
        "usageMetadata": raw.get("usageMetadata"),
        "finishReasons": [item.get("finishReason") for item in candidates],
        "transport_retries": raw.get("_transport_retries", 0),
    }


def prepare_v4(
    *, source_root: Path, out_root: Path,
) -> dict[str, Any]:
    source_index_path = source_root / "packet_index.parquet"
    source_auto_path = source_root / "auto_verdicts.parquet"
    source_candidates_path = source_root / "candidate_manifest.parquet"
    packets = pd.read_parquet(source_index_path)
    if len(packets) != EXPECTED_POPULATION:
        raise ValueError(f"source packet population is {len(packets)}, expected 2146")
    prompt_sha = v3.sha256_bytes(PROMPT.encode())
    packets = packets.copy()
    packets["source_packet_sha256"] = packets["packet_sha256"]
    packets["prompt"] = PROMPT
    packets["prompt_sha256"] = prompt_sha
    packets["packet_sha256"] = [
        v3.sha256_bytes(
            f"{RUN_ID}\0{source_sha}\0{prompt_sha}".encode()
        )
        for source_sha in packets["source_packet_sha256"].astype(str)
    ]
    v3.atomic_parquet(out_root / "packet_index.parquet", packets)
    candidates = pd.read_parquet(source_candidates_path)
    v3.atomic_parquet(out_root / "candidate_manifest.parquet", candidates)
    source_auto = pd.read_parquet(source_auto_path)
    auto = source_auto.loc[source_auto["decision_source"].notna(), ["anchor_id", "decision_source"]].copy()
    auto["verdict"] = "unreviewable"
    auto["confidence"] = None
    auto["reason"] = "deterministic identical-source/render conflict"
    auto["rep_count"] = 0
    v3.atomic_parquet(out_root / "auto_verdicts.parquet", auto)
    lock = {
        "run_id": RUN_ID,
        "source_run_id": SOURCE_RUN_ID,
        "source_packet_index_path": str(source_index_path),
        "source_packet_index_sha256": v3.sha256_file(source_index_path),
        "packet_index_sha256": v3.sha256_file(out_root / "packet_index.parquet"),
        "candidate_manifest_sha256": v3.sha256_file(out_root / "candidate_manifest.parquet"),
        "prompt_sha256": prompt_sha,
        "response_schema_sha256": v3.sha256_bytes(
            json.dumps(RESPONSE_SCHEMA, sort_keys=True, separators=(",", ":")).encode()
        ),
        "model_alias": MODEL_ALIAS,
        "transport": {"workers": DEFAULT_WORKERS, "qps": DEFAULT_QPS},
        "review_contract": {
            "confirmed_shortgap": "same readable roof; earlier absent; later present",
            "rejected_shortgap": "same readable roof; any other endpoint pattern",
            "unreviewable": "roof match or either endpoint cannot be judged",
        },
    }
    v3.atomic_json(out_root / "RUN_LOCK.json", lock)
    v3.artifact_manifest(out_root)
    return {"status": "PREPARED", "packets": len(packets), "auto_verdicts": len(auto)}


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


def run_rep(
    *,
    packet: Mapping[str, Any], rep: int, scope: str, out_root: Path,
    lock: dict[str, Any], caller: Callable[..., tuple[str, Mapping[str, Any]]],
) -> dict[str, Any]:
    anchor_id = str(packet["anchor_id"])
    rep_path = out_root / scope / "reps" / anchor_id / f"rep{rep}.json"
    if rep_path.exists():
        payload = json.loads(rep_path.read_text())
        if (
            payload.get("_packet_sha256") != packet["packet_sha256"]
            or payload.get("_model_alias") != MODEL_ALIAS
        ):
            raise ValueError(f"{anchor_id}: resume identity mismatch")
        _admit_version(lock, payload.get("_exact_model_version"))
        return payload
    images = [
        Path(packet["boundary_tight_path"]),
        Path(packet["boundary_context_path"]),
        Path(packet["temporal_strip_path"]),
    ]
    last_error = ""
    attempts_path = out_root / scope / "attempts.jsonl"
    prior_attempts = []
    if attempts_path.exists():
        for line in attempts_path.read_text().splitlines():
            record = json.loads(line)
            if (
                str(record.get("anchor_id")) == anchor_id
                and int(record.get("rep", -1)) == rep
            ):
                prior_attempts.append(int(record.get("attempt", 0)))
    first_attempt = max(prior_attempts, default=0) + 1
    for attempt in range(first_attempt, MAX_ATTEMPTS + 1):
        salt = v3.sha256_bytes(
            f"{RUN_ID}\0{scope}\0{anchor_id}\0{rep}\0{attempt}".encode()
        )
        started = time.time()
        raw: Mapping[str, Any] = {}
        try:
            text, raw = caller(
                image_paths=images, prompt=PROMPT, model=MODEL_ALIAS,
                max_tokens=MAX_OUTPUT_TOKENS,
                response_mime_type="application/json",
                response_schema=RESPONSE_SCHEMA, routing_salt=salt,
            )
            exact = _admit_version(lock, raw.get("modelVersion"))
            parsed = validate_response(json.loads(text))
            parsed.update({
                "_rep": rep, "_attempt": attempt,
                "_packet_sha256": packet["packet_sha256"],
                "_model_alias": MODEL_ALIAS,
                "_exact_model_version": exact,
            })
            v3.atomic_json(rep_path, parsed)
            v3.append_jsonl(out_root / scope / "attempts.jsonl", {
                "anchor_id": anchor_id, "rep": rep, "attempt": attempt,
                "status": "VALID", "routing_salt": salt,
                "max_output_tokens": MAX_OUTPUT_TOKENS,
                "elapsed_seconds": time.time() - started,
                "response": parsed, "raw": _compact_raw(raw),
            })
            v3.atomic_json(out_root / "RUN_LOCK.json", lock)
            return parsed
        except v3.ModelVersionDrift:
            raise
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            v3.append_jsonl(out_root / scope / "attempts.jsonl", {
                "anchor_id": anchor_id, "rep": rep, "attempt": attempt,
                "status": "RETRYABLE_FAILURE", "routing_salt": salt,
                "max_output_tokens": MAX_OUTPUT_TOKENS,
                "elapsed_seconds": time.time() - started, "error": last_error,
                "raw": _compact_raw(raw),
            })
    raise RuntimeError(
        f"{anchor_id} rep {rep} failed after {MAX_ATTEMPTS} attempts: {last_error}"
    )


def run_packets(
    *,
    packets: Sequence[Mapping[str, Any]], out_root: Path, scope: str,
    workers: int, qps: float, allow_third: bool,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    lock_path = out_root / "RUN_LOCK.json"
    lock = json.loads(lock_path.read_text())
    if "max_output_tokens_initial" not in lock:
        lock["max_output_tokens_initial"] = 2048
    lock["max_output_tokens_current"] = MAX_OUTPUT_TOKENS
    lock["max_output_tokens_amendment"] = (
        "8192 after one low-thinking logical rep exhausted 2048 tokens "
        "with about 1963 thinking tokens and a truncated reason string"
    )
    config = client_config(MODEL_ALIAS)
    caller = v3.native_caller(config, RateLimiter(qps))
    rep_rows: list[dict[str, Any]] = []
    consensus_rows: list[dict[str, Any]] = []

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
        return reps, {"anchor_id": packet["anchor_id"], **result, "decision_source": "GEMINI_V4"}

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = [pool.submit(one, packet) for packet in packets]
        for index, future in enumerate(as_completed(futures), 1):
            reps, result = future.result()
            rep_rows.extend(reps)
            consensus_rows.append(result)
            if index % 100 == 0 or index == len(futures):
                print(f"[{scope}] completed {index}/{len(futures)}", flush=True)
    v3.atomic_json(lock_path, lock)
    reps_frame = pd.DataFrame(rep_rows).sort_values(["_packet_sha256", "_rep"])
    decisions_frame = pd.DataFrame(consensus_rows).sort_values("anchor_id")
    return reps_frame, decisions_frame


def run_smoke(*, out_root: Path, workers: int, qps: float) -> dict[str, Any]:
    packets = pd.read_parquet(out_root / "packet_index.parquet").sort_values("anchor_id")
    smoke_ids = set(v3.PANEL_IDS[:10])
    smoke = [row for row in packets.to_dict("records") if str(row["anchor_id"]) in smoke_ids]
    if len(smoke) != 10:
        raise ValueError("smoke did not resolve 10 frozen anchors")
    reps, decisions = run_packets(
        packets=smoke, out_root=out_root, scope="smoke",
        workers=workers, qps=qps, allow_third=False,
    )
    if len(reps) != 20:
        raise RuntimeError("smoke did not complete exactly 20 primary reps")
    v3.atomic_parquet(out_root / "smoke" / "rep_verdicts.parquet", reps)
    v3.atomic_parquet(out_root / "smoke" / "consensus.parquet", decisions)
    status = {
        "status": "SMOKE_PASS", "anchors": 10, "reps": 20,
        "exact_model_version": json.loads((out_root / "RUN_LOCK.json").read_text())["exact_model_version"],
    }
    v3.atomic_json(out_root / "smoke" / "status.json", status)
    v3.artifact_manifest(out_root)
    return status


def run_production(*, out_root: Path, workers: int, qps: float) -> dict[str, Any]:
    smoke_status = json.loads((out_root / "smoke" / "status.json").read_text())
    if smoke_status.get("status") != "SMOKE_PASS":
        raise RuntimeError("production requires a passed authenticated smoke")
    packets = pd.read_parquet(out_root / "packet_index.parquet")
    auto = pd.read_parquet(out_root / "auto_verdicts.parquet")
    auto_ids = set(auto["anchor_id"].astype(str))
    callable_packets = [
        row for row in packets.to_dict("records")
        if str(row["anchor_id"]) not in auto_ids
    ]
    if len(callable_packets) != 2143:
        raise ValueError(f"callable population is {len(callable_packets)}, expected 2143")
    reps, decisions = run_packets(
        packets=callable_packets, out_root=out_root, scope="production",
        workers=workers, qps=qps, allow_third=True,
    )
    auto = auto.reindex(columns=decisions.columns).dropna(axis=1, how="all")
    combined = pd.concat([decisions, auto], ignore_index=True, sort=False)
    if len(combined) != EXPECTED_POPULATION or combined["anchor_id"].nunique() != EXPECTED_POPULATION:
        raise ValueError("V4 verdicts do not cover 2146 anchors exactly once")
    candidates = pd.read_parquet(out_root / "candidate_manifest.parquet")
    review_columns = [
        "anchor_id", "split", "gap_days", "earlier_capture_date",
        "later_capture_date", "chip_arm", "source_area_m2", "area_bin",
        "earlier_version", "later_version",
    ]
    combined = candidates[review_columns].merge(
        combined, on="anchor_id", validate="one_to_one",
    ).sort_values("anchor_id").reset_index(drop=True)
    combined["strict_under_45_days"] = combined["gap_days"].lt(45)
    v3.atomic_parquet(out_root / "rep_verdicts.parquet", reps)
    v3.atomic_parquet(out_root / "window_review.parquet", combined)
    combined.to_csv(out_root / "window_review.csv", index=False)
    verdict_counts = combined["verdict"].value_counts().to_dict()
    by_gap = {
        str(int(gap)): rows["verdict"].value_counts().to_dict()
        for gap, rows in combined.groupby("gap_days")
    }
    strict = combined.loc[combined["strict_under_45_days"]]
    v3.atomic_json(out_root / "summary.json", {
        "status": "COMPLETE",
        "model_alias": MODEL_ALIAS,
        "exact_model_version": json.loads((out_root / "RUN_LOCK.json").read_text())["exact_model_version"],
        "population": EXPECTED_POPULATION,
        "gemini_reviewed": len(callable_packets),
        "auto_unreviewable": len(auto),
        "rep_rows": len(reps),
        "third_rep_anchors": int(reps["_rep"].eq(3).sum()),
        "primary_agreement_rate": 1 - int(reps["_rep"].eq(3).sum()) / len(callable_packets),
        "verdict_counts": verdict_counts,
        "verdict_fractions": {
            verdict: count / EXPECTED_POPULATION
            for verdict, count in verdict_counts.items()
        },
        "verdict_counts_by_gap_days": by_gap,
        "strict_under_45_population": len(strict),
        "strict_under_45_verdict_counts": strict["verdict"].value_counts().to_dict(),
        "outside_strict_under_45_population": int((~combined["strict_under_45_days"]).sum()),
        "finished_utc": datetime.now(timezone.utc).isoformat(),
    })
    v3.artifact_manifest(out_root)
    return json.loads((out_root / "summary.json").read_text())


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
        result = prepare_v4(source_root=args.source_root, out_root=args.out_root)
    elif args.command == "smoke":
        result = run_smoke(out_root=args.out_root, workers=args.workers, qps=args.qps)
    else:
        result = run_production(out_root=args.out_root, workers=args.workers, qps=args.qps)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
