#!/usr/bin/env python3
"""Run a fresh, ledger-accounted Lite canary without mutating the run lock."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.temporal.quota_control import QuotaCircuitBreaker, QuotaPolicy
from scripts.temporal.run_adaptive_scan import _default_gemini_env
from scripts.validation.gemini_solar_image_review import (
    GeminiClientConfig,
    _call_gemini,
    env_value,
    extract_json_object,
    load_env_file,
)

PROMPT = """Inspect the marked rooftop image for a single CT runtime canary.
Return exactly one JSON object with keys pv_present (boolean or null),
confidence (number), quality_flag (usable|ambiguous|unusable), evidence (string),
and notes (string). Do not include prose or markdown."""


def config_from_env(path: Path, *, expected_model_version: str, quota_controller) -> GeminiClientConfig:
    env = load_env_file(path)
    return GeminiClientConfig(
        base_url=env_value(env, "GOOGLE_GEMINI_BASE_URL") or "",
        api_key=env_value(env, "GEMINI_API_KEY") or "",
        model="gemini-3.1-flash-lite",
        api_format=env_value(env, "GEMINI_API_FORMAT", "native"),
        native_path=env_value(env, "GEMINI_NATIVE_PATH", "/v1beta"),
        max_tokens_per_chip=int(env_value(env, "GEMINI_MAX_TOKENS_PER_CHIP", "600")),
        expected_model_version=expected_model_version,
        quota_controller=quota_controller,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-lock", type=Path, required=True)
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--env-file", type=Path, default=None)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()
    lock = json.loads(args.run_lock.read_text(encoding="utf-8"))
    if lock.get("status") not in {"awaiting_fresh_lite_canary", "canary_passed"}:
        raise SystemExit(f"run lock is not canary-ready: status={lock.get('status')!r}")
    model_info = lock["models"]["primary"]
    alias = model_info["requested_alias"]
    expected = model_info["expected_model_version"]
    if alias != "gemini-3.1-flash-lite":
        raise SystemExit(f"not a Lite lock: {alias}")
    if not args.image.is_file():
        raise SystemExit(f"canary image not found: {args.image}")
    quota_info = lock["quota"]
    policy = QuotaPolicy(
        window_id=quota_info["window_id"],
        gross_safe_budget=int(quota_info["gross_safe_budget"]),
        work_budget=int(quota_info["work_budget"]),
        warning_budget=int(quota_info["warning_budget"]),
        canary_reserve=int(quota_info["canary_reserve"]),
        retry_reserve=int(quota_info["retry_reserve"]),
        reset_after=quota_info["reset_after"],
    )
    ledger = Path(quota_info["ledger"])
    pause = Path(quota_info["pause_marker"])
    controller = QuotaCircuitBreaker(ledger, policy=policy, pause_path=pause, model_tier="primary")
    env_file = args.env_file or _default_gemini_env()
    config = config_from_env(env_file, expected_model_version=expected, quota_controller=controller)
    started = datetime.now(timezone.utc)
    context = {
        "anchor_id": "__canary__",
        "run_id": Path(lock["run_root"]).name,
        "wave_id": "canary",
        "round_id": 0,
        "round_type": "canary",
        "chunk_index": 0,
        "n_picks": 1,
        "attempt_kind": "canary",
        "logical_call_id": f"{Path(lock['run_root']).name}:canary:{started.strftime('%Y%m%dT%H%M%S%fZ')}",
        "requested_alias": alias,
        "model_tier": "primary",
    }
    text, raw = _call_gemini(
        image_paths=[args.image],
        prompt=PROMPT,
        config=config,
        max_tokens=4096,
        response_mime_type="application/json",
        attempt_context=context,
    )
    payload = extract_json_object(text)
    if not isinstance(payload, dict):
        raise RuntimeError("canary response is not a JSON object")
    returned = str(raw.get("modelVersion") or "").strip()
    if returned != expected:
        raise RuntimeError(f"model identity mismatch: expected={expected!r} returned={returned!r}")
    result = {
        "schema_version": "ct_lite_canary_v2",
        "started_utc": started.isoformat(),
        "finished_utc": datetime.now(timezone.utc).isoformat(),
        "run_lock": str(args.run_lock),
        "image": str(args.image),
        "requested_alias": alias,
        "expected_model_version": expected,
        "returned_model_version": returned,
        "response_schema_pass": True,
        "response_keys": sorted(payload),
        "quota": controller.summary(),
    }
    output = args.output or (Path(lock["run_root"]) / "canary" / f"lite_{started.strftime('%Y%m%dT%H%M%SZ')}.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    temp = output.with_suffix(output.suffix + ".tmp")
    temp.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temp, output)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
