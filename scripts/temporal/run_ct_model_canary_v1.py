#!/usr/bin/env python3
"""Run one small Gemini canary per locked CT model alias.

The canary records the gateway-returned ``modelVersion`` and refuses model
identity drift on later invocations. It is intentionally separate from the
production scan and scores one existing local chip only.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

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


def parse_canary_json(value: str) -> dict:
    text = value.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text).strip()
    candidates = [text]
    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if match:
        candidates.append(match.group(0))
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    raise ValueError("canary response did not contain a JSON object")


def config_from_env(path: Path, model: str) -> GeminiClientConfig:
    env = load_env_file(path)
    api_format = env_value(env, "GEMINI_API_FORMAT", "native")
    return GeminiClientConfig(
        base_url=env_value(env, "GOOGLE_GEMINI_BASE_URL") or "",
        api_key=env_value(env, "GEMINI_API_KEY") or "",
        model=model,
        api_format=api_format,
        native_path=env_value(env, "GEMINI_NATIVE_PATH", "/v1beta"),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lock", type=Path, required=True)
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--env-file", type=Path, default=None)
    args = parser.parse_args()
    lock = json.loads(args.lock.read_text())
    if not args.image.exists():
        raise SystemExit(f"canary image not found: {args.image}")
    env_file = args.env_file or _default_gemini_env()
    results = {}
    for tier, model_info in lock["scorer"]["models"].items():
        alias = model_info["requested_alias"]
        config = config_from_env(env_file, alias)
        if not config.base_url or not config.api_key:
            raise SystemExit(f"Gemini credentials missing in {env_file}")
        text, raw = _call_gemini(
            image_paths=[args.image],
            prompt=PROMPT,
            config=config,
            max_tokens=4096,
            response_mime_type="application/json",
        )
        returned = str(raw.get("modelVersion") or "").strip()
        if not returned:
            raise RuntimeError(f"{tier}/{alias}: gateway response has no modelVersion")
        try:
            payload = extract_json_object(text)
            if not isinstance(payload, dict):
                raise ValueError("not an object")
        except ValueError as exc:
            raise RuntimeError(
                f"{tier}/{alias}: canary response is not JSON; "
                f"raw_keys={sorted(raw)} text_prefix={text[:240]!r}"
            ) from exc
        prior = model_info.get("returned_model_identity")
        if prior and prior != returned:
            raise RuntimeError(f"model identity drift for {tier}: {prior} -> {returned}")
        model_info["returned_model_identity"] = returned
        results[tier] = {
            "requested_alias": alias,
            "returned_model_identity": returned,
            "response_keys": sorted(payload),
        }
    lock["status"] = "canary_passed"
    lock["last_canary_utc"] = datetime.now(timezone.utc).isoformat()
    lock["canary_image"] = str(args.image)
    lock["canary_results"] = results
    args.lock.write_text(json.dumps(lock, indent=2, sort_keys=True) + "\n")
    print(json.dumps(results, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
