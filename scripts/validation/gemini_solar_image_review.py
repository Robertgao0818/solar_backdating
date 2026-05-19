#!/usr/bin/env python3
"""Run Sub2API/Gemini vision checks on rooftop-solar image chips.

This is an AI-assisted QA helper. Its outputs are model observations, not
human-reviewed prediction footprints.
"""

from __future__ import annotations

import argparse
import base64
import csv
import json
import mimetypes
import os
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import requests


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _resolve_default_env_file() -> Path:
    """Find .env.gemini.local. Prefer subrepo-local; fall back to ZAsolar main repo."""
    local = PROJECT_ROOT / ".env.gemini.local"
    if local.exists():
        return local
    zasolar_root = os.environ.get("ZASOLAR_ROOT", "/home/gaosh/projects/ZAsolar")
    main_local = Path(zasolar_root) / ".env.gemini.local"
    if main_local.exists():
        return main_local
    return local  # nonexistent default; load_env_file returns {} and CLI/env vars take over


DEFAULT_ENV_FILE = _resolve_default_env_file()
DEFAULT_TIMEOUT_SEC = 120
API_FORMATS = {"openai", "native"}

DEFAULT_PROMPT = """You are reviewing high-resolution aerial or satellite image chips for rooftop solar PV.

Return only valid JSON with this schema:
{
  "pv_present": true | false | null,
  "confidence": 0.0-1.0,
  "quality_flag": "usable" | "ambiguous" | "unusable",
  "evidence": "short visual evidence",
  "notes": "short caveats if any"
}

Use pv_present=null only when the chip is too blurry, occluded, badly cropped,
or otherwise not interpretable. Do not count skylights, roof vents, HVAC units,
or shadows as PV panels.
"""


BATCH_PROMPT_TEMPLATE = """You are reviewing N={count} high-resolution image chips for rooftop solar PV.
All chips show the SAME rooftop location at different capture dates — the
building outline, roof material, and roof structure are the same across chips;
only the panel installation status changes over time. Score each chip independently
on whether PV is visible at the marker, but USE this same-roof consistency to
distinguish real PV (which has a fixed installation footprint at the marker
once installed) from skylights / vents / other roof features (which appear
identically across all dates regardless of PV status). Chips are presented in
input order; index them 1..N matching that order.

ANCHOR MARKER: every chip has a small yellow + cross drawn at the chip center.
That marker pinpoints the specific rooftop / roof segment we are scoring.
Decide pv_present based on whether PV modules are visible AT the marker (or on
the roof segment that contains the marker) — not based on PV anywhere in the
scene. A neighbouring building's PV array, or an off-center roof element that
happens to look dark/rectangular, must be reported as pv_present=false.

Return ONLY JSONL — one JSON object per line, exactly N lines, in input order.
No prose, no markdown fences, no array wrapper. Schema per line:

{{"chip_index": <int>, "pv_present": true|false|null, "confidence": <0.0-1.0>,
 "quality_flag": "usable"|"ambiguous"|"unusable",
 "evidence": "<specific visual description, e.g. '6 dark rectangular modules on south slope at marker'>",
 "notes": "<short caveats if any>"}}

Rules:
- Use pv_present=null when the marker is occluded, the chip is too blurry, or
  the marker falls off the visible roof (e.g., tall-building viewing-angle
  clipping where the anchor roof isn't in the dominant scene element).
- Do NOT count skylights, roof vents, HVAC units, water heaters, painted
  dark-blue/black flat roof sections, construction-site materials, or
  shadows as PV. PV must show a regular grid pattern of rectangular module
  borders — not a single uniform dark surface.
- The evidence field must describe what you actually see at the marker in
  that specific chip — not generic claims.
- Output exactly {count} lines. One JSON object per line. No surrounding text.
"""


MATRIX_PROMPT_TEMPLATE = """You are reviewing D={date_count} dated image chips for rooftop solar PV.
Each image shows the SAME chip group at a different capture date. The images
are presented in date_index order 1..D. Each image is annotated with target
labels: {target_labels}. Score every target label in every date image.

Return ONLY JSONL — one JSON object per line, exactly D*T={cell_count} lines.
No prose, no markdown fences, no array wrapper. Schema per line:

{{"cell_index": <int>, "date_index": <int>, "capture_date": "YYYY-MM-DD",
 "target_id": "<stable target id>", "target_label": "T01",
 "pv_present": true|false|null, "confidence": <0.0-1.0>,
 "quality_flag": "usable"|"ambiguous"|"unusable",
 "evidence": "<specific visual description at this target/date>",
 "notes": "<short caveats if any>"}}

Rules:
- Evaluate each target label separately. Do not transfer a PV decision from T01
  to T02 just because they are nearby.
- Use pv_present=null when that target label is occluded, too blurry, outside
  the visible roof, or cannot be interpreted because of viewing-angle/parallax.
- Do not count PV elsewhere in the chip unless it is on the requested target.
- Preserve the target_id/target_label mapping exactly as listed below.
- Output rows in date-major order: date_index 1 all targets, then date_index 2,
  continuing through date_index D.

Target mapping:
{target_mapping}
"""


DEFAULT_MAX_MATRIX_DATES = 5
DEFAULT_MAX_MATRIX_TARGETS = 4
HARD_MAX_MATRIX_TARGETS = 6
HARD_MAX_MATRIX_CELLS = 24


def load_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}

    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if (
            len(value) >= 2
            and value[0] == value[-1]
            and value[0] in {"'", '"'}
        ):
            value = value[1:-1]
        values[key] = value
    return values


def env_value(env_file_values: dict[str, str], key: str, default: str = "") -> str:
    return os.environ.get(key) or env_file_values.get(key) or default


def normalize_root_url(base_url: str) -> str:
    return base_url.rstrip("/")


def normalize_openai_url(base_url: str) -> str:
    base = normalize_root_url(base_url)
    if base.endswith("/v1"):
        return base
    return f"{base}/v1"


def auth_headers(api_key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }


def image_data_url(path: Path) -> str:
    mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def image_inline_data(path: Path) -> dict[str, str]:
    mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return {"mime_type": mime, "data": encoded}


def read_prompt(args: argparse.Namespace) -> str:
    if args.prompt_file:
        return args.prompt_file.read_text(encoding="utf-8")
    if args.prompt:
        return args.prompt
    return DEFAULT_PROMPT


def extract_json_object(text: str) -> Any:
    stripped = text.strip()
    fenced = re.match(r"^```(?:json)?\s*(.*?)\s*```$", stripped, re.S)
    if fenced:
        stripped = fenced.group(1).strip()

    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass

    start = stripped.find("{")
    end = stripped.rfind("}")
    if start != -1 and end != -1 and end > start:
        return json.loads(stripped[start : end + 1])
    raise ValueError(f"could not find a complete JSON object in response: {stripped[:200]!r}")


def build_message_content(prompt: str, image_paths: list[Path]) -> list[dict[str, Any]]:
    content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
    for path in image_paths:
        if not path.exists():
            raise FileNotFoundError(path)
        content.append({"type": "image_url", "image_url": {"url": image_data_url(path)}})
    return content


def build_native_parts(prompt: str, image_paths: list[Path]) -> list[dict[str, Any]]:
    parts: list[dict[str, Any]] = [{"text": prompt}]
    for path in image_paths:
        if not path.exists():
            raise FileNotFoundError(path)
        parts.append({"inline_data": image_inline_data(path)})
    return parts


def post_chat_completion(
    *,
    base_url: str,
    api_key: str,
    model: str,
    prompt: str,
    image_paths: list[Path],
    max_tokens: int,
    timeout: int,
) -> dict[str, Any]:
    endpoint = f"{normalize_openai_url(base_url)}/chat/completions"
    payload = {
        "model": model,
        "temperature": 0,
        "max_tokens": max_tokens,
        "messages": [
            {
                "role": "user",
                "content": build_message_content(prompt, image_paths),
            }
        ],
    }

    response = requests.post(
        endpoint,
        headers=auth_headers(api_key),
        data=json.dumps(payload),
        timeout=timeout,
    )
    try:
        response.raise_for_status()
    except requests.HTTPError as exc:
        body = response.text[:2000]
        raise RuntimeError(
            f"{response.status_code} {response.reason} from {endpoint}: {body}"
        ) from exc
    return response.json()


def post_native_generate_content(
    *,
    base_url: str,
    native_path: str,
    api_key: str,
    model: str,
    prompt: str,
    image_paths: list[Path],
    max_tokens: int,
    timeout: int,
) -> dict[str, Any]:
    root = normalize_root_url(base_url)
    path = "/" + native_path.strip("/")
    endpoint = f"{root}{path}/models/{model}:generateContent"
    payload = {
        "contents": [
            {
                "role": "user",
                "parts": build_native_parts(prompt, image_paths),
            }
        ],
        "generationConfig": {
            "temperature": 0,
            "maxOutputTokens": max_tokens,
        },
    }

    response = requests.post(
        endpoint,
        headers=auth_headers(api_key),
        data=json.dumps(payload),
        timeout=timeout,
    )
    try:
        response.raise_for_status()
    except requests.HTTPError as exc:
        body = response.text[:2000]
        raise RuntimeError(
            f"{response.status_code} {response.reason} from {endpoint}: {body}"
        ) from exc
    return response.json()


def response_text(response_json: dict[str, Any]) -> str:
    choices = response_json.get("choices") or []
    if not choices:
        return ""
    message = choices[0].get("message") or {}
    content = message.get("content", "")
    if isinstance(content, str):
        return content
    return json.dumps(content, ensure_ascii=False)


def native_response_text(response_json: dict[str, Any]) -> str:
    chunks: list[str] = []
    for candidate in response_json.get("candidates") or []:
        content = candidate.get("content") or {}
        for part in content.get("parts") or []:
            text = part.get("text")
            if text:
                chunks.append(text)
    return "\n".join(chunks)


@dataclass
class BatchPick:
    """A single chip to score in a batch call. Audit fields are not sent to Gemini."""

    chip_index: int
    chip_path: Path
    capture_date: str = ""
    version: str | int = ""
    actual_zoom: int | None = None


@dataclass(frozen=True)
class MatrixTarget:
    target_id: str
    target_label: str


@dataclass(frozen=True)
class MatrixDatePick:
    date_index: int
    chip_path: Path
    capture_date: str
    version: str | int = ""
    actual_zoom: int | None = None


@dataclass
class GeminiObservation:
    chip_index: int
    pv_present: bool | None
    confidence: float | None
    quality_flag: str
    evidence: str
    notes: str
    decision_source: str  # "gemini_batch" | "gemini_per_image" | "gemini_failed"
    raw_response: str = ""
    error: str | None = None


@dataclass
class GeminiMatrixObservation:
    cell_index: int
    date_index: int
    capture_date: str
    target_id: str
    target_label: str
    pv_present: bool | None
    confidence: float | None
    quality_flag: str
    evidence: str
    notes: str
    decision_source: str  # "gemini_matrix" | "gemini_failed"
    raw_response: str = ""
    error: str | None = None


@dataclass(frozen=True)
class GeminiClientConfig:
    base_url: str
    api_key: str
    model: str = "gemini-3-flash-preview"
    api_format: str = "native"
    native_path: str = "/v1beta"
    max_tokens_per_chip: int = 600
    timeout: int = DEFAULT_TIMEOUT_SEC


def parse_jsonl_lenient(text: str, expected_count: int) -> tuple[list[dict[str, Any]], list[int]]:
    """Parse JSONL output, salvaging valid rows. Returns (parsed_dicts_in_order, missing_chip_indices).

    Tolerant to:
    - Markdown fences (```...```), stripped
    - Lines that are not valid JSON (skipped)
    - Out-of-order chip_index values
    - chip_index values outside [1..expected_count] (rejected)
    - Duplicate chip_index (last wins)
    """
    lines = [line for line in text.splitlines() if line.strip()]
    if lines and lines[0].strip().startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]

    parsed_by_index: dict[int, dict[str, Any]] = {}
    for line in lines:
        candidate = line.strip()
        if not candidate or not candidate.startswith("{"):
            continue
        try:
            obj = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict) or "chip_index" not in obj:
            continue
        try:
            idx = int(obj["chip_index"])
        except (TypeError, ValueError):
            continue
        if 1 <= idx <= expected_count:
            parsed_by_index[idx] = obj

    ordered = [parsed_by_index[i] for i in sorted(parsed_by_index)]
    missing = [i for i in range(1, expected_count + 1) if i not in parsed_by_index]
    return ordered, missing


def _json_candidate_lines(text: str) -> list[str]:
    lines = [line for line in text.splitlines() if line.strip()]
    if lines and lines[0].strip().startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    return [line.strip() for line in lines if line.strip().startswith("{")]


def validate_observation_schema(obj: dict[str, Any]) -> bool:
    if not all(k in obj for k in ("chip_index", "pv_present", "confidence", "quality_flag")):
        return False
    pv = obj.get("pv_present")
    if pv is not None and not isinstance(pv, bool):
        return False
    conf = obj.get("confidence")
    if conf is not None and not isinstance(conf, (int, float)):
        return False
    if obj.get("quality_flag") not in ("usable", "ambiguous", "unusable"):
        return False
    return True


def validate_matrix_limits(
    *,
    date_count: int,
    target_count: int,
    max_dates: int = DEFAULT_MAX_MATRIX_DATES,
    max_targets: int = DEFAULT_MAX_MATRIX_TARGETS,
    hard_max_targets: int = HARD_MAX_MATRIX_TARGETS,
    hard_max_cells: int = HARD_MAX_MATRIX_CELLS,
) -> None:
    if date_count <= 0:
        raise ValueError("date_count must be positive")
    if target_count <= 0:
        raise ValueError("target_count must be positive")
    if date_count > max_dates:
        raise ValueError(f"date_count={date_count} exceeds max_dates={max_dates}")
    if target_count > max_targets:
        raise ValueError(f"target_count={target_count} exceeds max_targets={max_targets}")
    if target_count > hard_max_targets:
        raise ValueError(f"target_count={target_count} exceeds hard_max_targets={hard_max_targets}")
    cell_count = date_count * target_count
    if cell_count > hard_max_cells:
        raise ValueError(f"cell_count={cell_count} exceeds hard_max_cells={hard_max_cells}")


def validate_matrix_observation_schema(
    obj: dict[str, Any],
    *,
    valid_date_indices: set[int],
    valid_target_labels: set[str],
    target_ids_by_label: dict[str, str],
) -> bool:
    required = (
        "cell_index",
        "date_index",
        "capture_date",
        "target_id",
        "target_label",
        "pv_present",
        "confidence",
        "quality_flag",
    )
    if not all(k in obj for k in required):
        return False
    try:
        date_index = int(obj["date_index"])
    except (TypeError, ValueError):
        return False
    if date_index not in valid_date_indices:
        return False
    target_label = str(obj["target_label"])
    if target_label not in valid_target_labels:
        return False
    if str(obj["target_id"]) != str(target_ids_by_label[target_label]):
        return False
    pv = obj.get("pv_present")
    if pv is not None and not isinstance(pv, bool):
        return False
    conf = obj.get("confidence")
    if conf is not None and not isinstance(conf, (int, float)):
        return False
    if obj.get("quality_flag") not in ("usable", "ambiguous", "unusable"):
        return False
    return True


def parse_matrix_jsonl_lenient(
    text: str,
    *,
    date_picks: list[MatrixDatePick],
    targets: list[MatrixTarget],
) -> tuple[list[dict[str, Any]], list[tuple[int, str]]]:
    valid_date_indices = {p.date_index for p in date_picks}
    valid_target_labels = {t.target_label for t in targets}
    target_ids_by_label = {t.target_label: t.target_id for t in targets}
    parsed_by_key: dict[tuple[int, str], dict[str, Any]] = {}

    for candidate in _json_candidate_lines(text):
        try:
            obj = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue
        if not validate_matrix_observation_schema(
            obj,
            valid_date_indices=valid_date_indices,
            valid_target_labels=valid_target_labels,
            target_ids_by_label=target_ids_by_label,
        ):
            continue
        key = (int(obj["date_index"]), str(obj["target_label"]))
        parsed_by_key[key] = obj

    expected_keys = [
        (date_pick.date_index, target.target_label)
        for date_pick in date_picks
        for target in targets
    ]
    ordered = [parsed_by_key[key] for key in expected_keys if key in parsed_by_key]
    missing = [key for key in expected_keys if key not in parsed_by_key]
    return ordered, missing


def _to_observation(parsed: dict[str, Any], *, decision_source: str, raw: str = "") -> GeminiObservation:
    return GeminiObservation(
        chip_index=int(parsed["chip_index"]),
        pv_present=parsed.get("pv_present"),
        confidence=float(parsed["confidence"]) if parsed.get("confidence") is not None else None,
        quality_flag=str(parsed.get("quality_flag", "unusable")),
        evidence=str(parsed.get("evidence", "")),
        notes=str(parsed.get("notes", "")),
        decision_source=decision_source,
        raw_response=raw,
    )


def _to_matrix_observation(parsed: dict[str, Any], *, decision_source: str, raw: str = "") -> GeminiMatrixObservation:
    return GeminiMatrixObservation(
        cell_index=int(parsed["cell_index"]),
        date_index=int(parsed["date_index"]),
        capture_date=str(parsed["capture_date"]),
        target_id=str(parsed["target_id"]),
        target_label=str(parsed["target_label"]),
        pv_present=parsed.get("pv_present"),
        confidence=float(parsed["confidence"]) if parsed.get("confidence") is not None else None,
        quality_flag=str(parsed.get("quality_flag", "unusable")),
        evidence=str(parsed.get("evidence", "")),
        notes=str(parsed.get("notes", "")),
        decision_source=decision_source,
        raw_response=raw,
    )


def _failed_observation(chip_index: int, error: str) -> GeminiObservation:
    return GeminiObservation(
        chip_index=chip_index,
        pv_present=None,
        confidence=None,
        quality_flag="unusable",
        evidence="",
        notes=f"gemini_failed: {error[:300]}",
        decision_source="gemini_failed",
        error=error,
    )


def _failed_matrix_observation(
    *,
    cell_index: int,
    date_pick: MatrixDatePick,
    target: MatrixTarget,
    error: str,
) -> GeminiMatrixObservation:
    return GeminiMatrixObservation(
        cell_index=cell_index,
        date_index=date_pick.date_index,
        capture_date=date_pick.capture_date,
        target_id=target.target_id,
        target_label=target.target_label,
        pv_present=None,
        confidence=None,
        quality_flag="unusable",
        evidence="",
        notes=f"gemini_failed: {error[:300]}",
        decision_source="gemini_failed",
        error=error,
    )


def _call_gemini(
    *,
    image_paths: list[Path],
    prompt: str,
    config: GeminiClientConfig,
    max_tokens: int,
    poster: Callable[..., dict[str, Any]] | None = None,
) -> tuple[str, dict[str, Any]]:
    """Call Gemini via native or openai format. Returns (response_text, raw_response_json)."""
    if config.api_format == "native":
        if poster is None:
            raw = post_native_generate_content(
                base_url=config.base_url,
                native_path=config.native_path,
                api_key=config.api_key,
                model=config.model,
                prompt=prompt,
                image_paths=image_paths,
                max_tokens=max_tokens,
                timeout=config.timeout,
            )
        else:
            raw = poster(
                api_format="native",
                base_url=config.base_url,
                native_path=config.native_path,
                api_key=config.api_key,
                model=config.model,
                prompt=prompt,
                image_paths=image_paths,
                max_tokens=max_tokens,
                timeout=config.timeout,
            )
        return native_response_text(raw), raw
    if poster is None:
        raw = post_chat_completion(
            base_url=config.base_url,
            api_key=config.api_key,
            model=config.model,
            prompt=prompt,
            image_paths=image_paths,
            max_tokens=max_tokens,
            timeout=config.timeout,
        )
    else:
        raw = poster(
            api_format="openai",
            base_url=config.base_url,
            api_key=config.api_key,
            model=config.model,
            prompt=prompt,
            image_paths=image_paths,
            max_tokens=max_tokens,
            timeout=config.timeout,
        )
    return response_text(raw), raw


def _identify_census_reference_chip(
    picks: list[BatchPick], census_mid_date_iso: str | None
) -> tuple[int | None, str | None]:
    """Pick the latest chip in the batch dated >= census_mid_date - 6 months.

    Returns (chip_index, capture_date) or (None, None) if no chip qualifies
    (e.g. walk_back rounds where every pick is pre-census).
    """
    if not census_mid_date_iso:
        return None, None
    threshold = census_mid_date_iso  # ISO YYYY-MM-DD; lex compare matches date order
    # Strip 6 months: subtract from year if month >= 7 else year-1, month+6.
    try:
        y, m, d = int(threshold[:4]), int(threshold[5:7]), int(threshold[8:10])
    except ValueError:
        return None, None
    if m > 6:
        threshold = f"{y:04d}-{m - 6:02d}-{d:02d}"
    else:
        threshold = f"{y - 1:04d}-{m + 6:02d}-{d:02d}"
    in_census = [(p.capture_date, p.chip_index) for p in picks if p.capture_date >= threshold]
    if not in_census:
        return None, None
    capture_date, chip_index = max(in_census, key=lambda x: x[0])
    return chip_index, capture_date


def _build_batch_prompt(
    picks: list[BatchPick], census_mid_date_iso: str | None = None
) -> str:
    """Render BATCH_PROMPT_TEMPLATE plus an optional census-reference clause.

    When the batch contains a chip from the census period, append a clause
    telling Gemini that this specific chip is GT-known to have PV at the
    yellow ring marker. This calibrates "what does PV look like AT THIS
    SPECIFIC ROOF" for the older chips in the same batch — handling the
    appearance variation across imagery vintages, lighting, and zoom levels.
    """
    base = BATCH_PROMPT_TEMPLATE.format(count=len(picks))
    ref_idx, ref_date = _identify_census_reference_chip(picks, census_mid_date_iso)
    if ref_idx is None:
        return base
    suffix = (
        f"\nCALIBRATION: chip {ref_idx} (capture_date {ref_date}) is the most recent\n"
        "imagery in this batch and is from the census period. Each anchor is a known\n"
        "PV installation per the higher-level ground truth, so chip {ref_idx} should\n"
        "show PV at the yellow ring marker. Use it to calibrate panel appearance for\n"
        "this exact roof: same building, same roof material, same orientation. If chip\n"
        "{ref_idx} clearly shows PV at the marker, label it pv_present=true; if you\n"
        "cannot see PV at the marker in chip {ref_idx}, that means the marker is not\n"
        "well-positioned for this anchor and you should label that chip\n"
        "quality_flag='ambiguous' rather than absent. Do NOT propagate the GT-prior to\n"
        "other chips — score each older chip on its own visual evidence at the marker,\n"
        "using chip {ref_idx} only as appearance-calibration reference.\n"
    ).replace("{ref_idx}", str(ref_idx))
    return base + suffix


def _attempt_batch(
    picks: list[BatchPick],
    *,
    config: GeminiClientConfig,
    poster: Callable[..., dict[str, Any]] | None,
    census_mid_date_iso: str | None = None,
) -> tuple[list[dict[str, Any]], list[int], str, str | None]:
    """Single batch attempt. Returns (valid_parsed, missing_indices, raw_text, error)."""
    prompt = _build_batch_prompt(picks, census_mid_date_iso=census_mid_date_iso)
    max_tokens = config.max_tokens_per_chip * len(picks) + 256
    try:
        raw_text, _raw_json = _call_gemini(
            image_paths=[p.chip_path for p in picks],
            prompt=prompt,
            config=config,
            max_tokens=max_tokens,
            poster=poster,
        )
    except Exception as exc:  # noqa: BLE001 - retry layer treats all errors uniformly.
        return [], [p.chip_index for p in picks], "", f"{type(exc).__name__}: {exc}"

    parsed, missing = parse_jsonl_lenient(raw_text, len(picks))
    valid = [p for p in parsed if validate_observation_schema(p)]
    valid_indices = {int(p["chip_index"]) for p in valid}
    final_missing = [i for i in range(1, len(picks) + 1) if i not in valid_indices]
    return valid, final_missing, raw_text, None


def _attempt_per_image(
    pick: BatchPick,
    *,
    config: GeminiClientConfig,
    poster: Callable[..., dict[str, Any]] | None,
) -> GeminiObservation:
    prompt = DEFAULT_PROMPT
    try:
        raw_text, _raw = _call_gemini(
            image_paths=[pick.chip_path],
            prompt=prompt,
            config=config,
            max_tokens=config.max_tokens_per_chip,
            poster=poster,
        )
    except Exception as exc:  # noqa: BLE001
        return _failed_observation(pick.chip_index, f"per_image_call_error: {type(exc).__name__}: {exc}")

    try:
        parsed = extract_json_object(raw_text)
    except Exception as exc:  # noqa: BLE001
        return _failed_observation(pick.chip_index, f"per_image_parse_error: {exc}")

    if not isinstance(parsed, dict):
        return _failed_observation(pick.chip_index, "per_image_response_not_object")
    parsed.setdefault("chip_index", pick.chip_index)
    if not validate_observation_schema(parsed):
        return _failed_observation(pick.chip_index, f"per_image_schema_invalid: {raw_text[:300]}")
    return _to_observation(parsed, decision_source="gemini_per_image", raw=raw_text)


def score_batch_with_fallback(
    picks: list[BatchPick],
    *,
    config: GeminiClientConfig,
    audit_writer: Callable[[dict[str, Any]], None] | None = None,
    poster: Callable[..., dict[str, Any]] | None = None,
    census_mid_date_iso: str | None = None,
) -> list[GeminiObservation]:
    """Score N chips in one batch call following Q5.6 (a') retry policy:

    1. Batch attempt 1.
    2. If schema/row-count not satisfied, batch attempt 2 with the same prompt.
    3. Salvage the best attempt's valid rows.
    4. For each missing chip_index, run per-image fallback.
    5. Any chip still unresolved is recorded as `gemini_failed`.

    `audit_writer` receives one dict per attempt (batch attempts) plus one dict
    per per-image fallback. `poster` is overridable for tests; default goes
    through `_call_gemini` which uses requests.
    """
    if not picks:
        return []

    valid1, missing1, raw1, err1 = _attempt_batch(
        picks, config=config, poster=poster, census_mid_date_iso=census_mid_date_iso
    )
    if audit_writer is not None:
        audit_writer(
            {
                "stage": "batch_attempt_1",
                "n_picks": len(picks),
                "n_valid": len(valid1),
                "missing_indices": missing1,
                "error": err1,
                "raw_response": raw1,
            }
        )

    if len(valid1) == len(picks) and not missing1:
        return [_to_observation(p, decision_source="gemini_batch", raw=raw1) for p in valid1]

    valid2, missing2, raw2, err2 = _attempt_batch(
        picks, config=config, poster=poster, census_mid_date_iso=census_mid_date_iso
    )
    if audit_writer is not None:
        audit_writer(
            {
                "stage": "batch_attempt_2",
                "n_picks": len(picks),
                "n_valid": len(valid2),
                "missing_indices": missing2,
                "error": err2,
                "raw_response": raw2,
            }
        )

    if len(valid2) == len(picks) and not missing2:
        return [_to_observation(p, decision_source="gemini_batch", raw=raw2) for p in valid2]

    salvaged_attempts = [(valid1, raw1), (valid2, raw2)]
    best_valid, best_raw = max(salvaged_attempts, key=lambda v: len(v[0]))
    salvaged_indices = {int(p["chip_index"]) for p in best_valid}
    salvaged_obs = [_to_observation(p, decision_source="gemini_batch", raw=best_raw) for p in best_valid]

    fallback_obs: list[GeminiObservation] = []
    for pick in picks:
        if pick.chip_index in salvaged_indices:
            continue
        per_image = _attempt_per_image(pick, config=config, poster=poster)
        if audit_writer is not None:
            audit_writer(
                {
                    "stage": "per_image_fallback",
                    "chip_index": pick.chip_index,
                    "result": {
                        "decision_source": per_image.decision_source,
                        "pv_present": per_image.pv_present,
                        "quality_flag": per_image.quality_flag,
                        "error": per_image.error,
                        "raw_response": per_image.raw_response,
                    },
                }
            )
        fallback_obs.append(per_image)

    by_index = {o.chip_index: o for o in (*salvaged_obs, *fallback_obs)}
    out: list[GeminiObservation] = []
    for pick in picks:
        if pick.chip_index in by_index:
            out.append(by_index[pick.chip_index])
        else:
            out.append(_failed_observation(pick.chip_index, "no_observation_after_fallback"))
    return out


def _build_matrix_prompt(date_picks: list[MatrixDatePick], targets: list[MatrixTarget]) -> str:
    target_labels = ", ".join(t.target_label for t in targets)
    target_mapping = "\n".join(f"- {t.target_label}: {t.target_id}" for t in targets)
    return MATRIX_PROMPT_TEMPLATE.format(
        date_count=len(date_picks),
        target_labels=target_labels,
        cell_count=len(date_picks) * len(targets),
        target_mapping=target_mapping,
    )


def _attempt_matrix_batch(
    date_picks: list[MatrixDatePick],
    targets: list[MatrixTarget],
    *,
    config: GeminiClientConfig,
    poster: Callable[..., dict[str, Any]] | None,
) -> tuple[list[dict[str, Any]], list[tuple[int, str]], str, str | None]:
    prompt = _build_matrix_prompt(date_picks, targets)
    cell_count = len(date_picks) * len(targets)
    max_tokens = max(config.max_tokens_per_chip * len(date_picks), cell_count * 180 + 256)
    try:
        raw_text, _raw_json = _call_gemini(
            image_paths=[p.chip_path for p in date_picks],
            prompt=prompt,
            config=config,
            max_tokens=max_tokens,
            poster=poster,
        )
    except Exception as exc:  # noqa: BLE001
        return [], [(p.date_index, t.target_label) for p in date_picks for t in targets], "", f"{type(exc).__name__}: {exc}"

    parsed, missing = parse_matrix_jsonl_lenient(
        raw_text,
        date_picks=date_picks,
        targets=targets,
    )
    return parsed, missing, raw_text, None


def score_target_date_matrix(
    date_picks: list[MatrixDatePick],
    targets: list[MatrixTarget],
    *,
    config: GeminiClientConfig,
    audit_writer: Callable[[dict[str, Any]], None] | None = None,
    poster: Callable[..., dict[str, Any]] | None = None,
    max_dates: int = DEFAULT_MAX_MATRIX_DATES,
    max_targets: int = DEFAULT_MAX_MATRIX_TARGETS,
    hard_max_targets: int = HARD_MAX_MATRIX_TARGETS,
    hard_max_cells: int = HARD_MAX_MATRIX_CELLS,
) -> list[GeminiMatrixObservation]:
    """Score a bounded date x target matrix from shared chip images.

    Inputs are date-major: each `MatrixDatePick` is one image for a chip group,
    and each target label must already be visibly annotated in every image. The
    function makes at most two matrix attempts, salvages valid cells from the
    better attempt, and emits `gemini_failed` rows for missing cells. Missing
    cells are not retried per target because there is no target-specific crop in
    this API boundary.
    """
    if not date_picks or not targets:
        return []
    validate_matrix_limits(
        date_count=len(date_picks),
        target_count=len(targets),
        max_dates=max_dates,
        max_targets=max_targets,
        hard_max_targets=hard_max_targets,
        hard_max_cells=hard_max_cells,
    )

    valid1, missing1, raw1, err1 = _attempt_matrix_batch(
        date_picks, targets, config=config, poster=poster
    )
    if audit_writer is not None:
        audit_writer(
            {
                "stage": "matrix_attempt_1",
                "n_dates": len(date_picks),
                "n_targets": len(targets),
                "n_cells": len(date_picks) * len(targets),
                "n_valid": len(valid1),
                "missing_cells": [f"{d}:{t}" for d, t in missing1],
                "error": err1,
                "raw_response": raw1,
            }
        )

    if len(valid1) == len(date_picks) * len(targets) and not missing1:
        return [_to_matrix_observation(p, decision_source="gemini_matrix", raw=raw1) for p in valid1]

    valid2, missing2, raw2, err2 = _attempt_matrix_batch(
        date_picks, targets, config=config, poster=poster
    )
    if audit_writer is not None:
        audit_writer(
            {
                "stage": "matrix_attempt_2",
                "n_dates": len(date_picks),
                "n_targets": len(targets),
                "n_cells": len(date_picks) * len(targets),
                "n_valid": len(valid2),
                "missing_cells": [f"{d}:{t}" for d, t in missing2],
                "error": err2,
                "raw_response": raw2,
            }
        )

    best_valid, best_raw = max(((valid1, raw1), (valid2, raw2)), key=lambda item: len(item[0]))
    by_key = {(int(p["date_index"]), str(p["target_label"])): p for p in best_valid}
    out: list[GeminiMatrixObservation] = []
    cell_index = 1
    for date_pick in date_picks:
        for target in targets:
            key = (date_pick.date_index, target.target_label)
            if key in by_key:
                parsed = dict(by_key[key])
                parsed["cell_index"] = int(parsed.get("cell_index") or cell_index)
                out.append(_to_matrix_observation(parsed, decision_source="gemini_matrix", raw=best_raw))
            else:
                out.append(
                    _failed_matrix_observation(
                        cell_index=cell_index,
                        date_pick=date_pick,
                        target=target,
                        error="missing_matrix_cell_after_two_attempts",
                    )
                )
            cell_index += 1
    return out


def review_one(
    *,
    image_path: Path,
    reference_images: list[Path],
    base_url: str,
    api_key: str,
    model: str,
    api_format: str,
    native_path: str,
    prompt: str,
    max_tokens: int,
    timeout: int,
) -> dict[str, Any]:
    started = time.time()
    image_paths = [*reference_images, image_path]
    record: dict[str, Any] = {
        "image_path": str(image_path),
        "reference_images": [str(p) for p in reference_images],
        "model": model,
        "api_format": api_format,
        "ok": False,
    }
    try:
        if api_format == "native":
            raw = post_native_generate_content(
                base_url=base_url,
                native_path=native_path,
                api_key=api_key,
                model=model,
                prompt=prompt,
                image_paths=image_paths,
                max_tokens=max_tokens,
                timeout=timeout,
            )
            content = native_response_text(raw)
        else:
            raw = post_chat_completion(
                base_url=base_url,
                api_key=api_key,
                model=model,
                prompt=prompt,
                image_paths=image_paths,
                max_tokens=max_tokens,
                timeout=timeout,
            )
            content = response_text(raw)
        record["response_text"] = content
        try:
            record["parsed"] = extract_json_object(content)
        except Exception as exc:  # noqa: BLE001 - preserve raw content for audit.
            record["parse_error"] = str(exc)
        record["ok"] = True
    except Exception as exc:  # noqa: BLE001 - batch jobs should keep going.
        record["error"] = str(exc)
    record["elapsed_sec"] = round(time.time() - started, 3)
    return record


def image_paths_from_manifest(path: Path, image_column: str) -> list[Path]:
    rows: list[Path] = []
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        if image_column not in (reader.fieldnames or []):
            raise ValueError(
                f"column {image_column!r} not found in {path}; "
                f"available={reader.fieldnames}"
            )
        for row in reader:
            value = (row.get(image_column) or "").strip()
            if value:
                rows.append(Path(value))
    return rows


def list_models(base_url: str, api_key: str, timeout: int) -> None:
    root = normalize_root_url(base_url)
    endpoints = [
        f"{normalize_openai_url(base_url)}/models",
        f"{root}/v1beta/models",
        f"{root}/antigravity/v1beta/models",
    ]
    for endpoint in endpoints:
        print(f"\n# {endpoint}")
        try:
            response = requests.get(endpoint, headers=auth_headers(api_key), timeout=timeout)
            response.raise_for_status()
            data = response.json()
        except Exception as exc:  # noqa: BLE001
            print(f"ERROR: {exc}")
            continue
        print(json.dumps(data, ensure_ascii=False, indent=2)[:8000])


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("images", nargs="*", type=Path, help="Image chips to review.")
    parser.add_argument("--manifest", type=Path, help="Optional CSV manifest with image paths.")
    parser.add_argument("--image-column", default="image_path", help="CSV column for --manifest.")
    parser.add_argument(
        "--reference-image",
        action="append",
        type=Path,
        default=[],
        help="Optional reference image included before each target image. Repeatable.",
    )
    parser.add_argument("--output", type=Path, help="JSONL output path. Defaults to stdout.")
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)
    parser.add_argument("--base-url", help="Sub2API root URL. Defaults to GOOGLE_GEMINI_BASE_URL.")
    parser.add_argument("--api-key", help="API key. Defaults to GEMINI_API_KEY.")
    parser.add_argument("--model", help="Model id. Defaults to GEMINI_MODEL.")
    parser.add_argument(
        "--api-format",
        choices=sorted(API_FORMATS),
        help="API shape to call. Defaults to GEMINI_API_FORMAT or native.",
    )
    parser.add_argument(
        "--native-path",
        help="Native Gemini path under the base URL. Defaults to GEMINI_NATIVE_PATH or /v1beta.",
    )
    parser.add_argument("--prompt", help="Inline prompt override.")
    parser.add_argument("--prompt-file", type=Path, help="Prompt file override.")
    parser.add_argument("--max-tokens", type=int, default=1024)
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_SEC)
    parser.add_argument("--limit", type=int, help="Process only the first N images.")
    parser.add_argument("--list-models", action="store_true", help="Print model lists and exit.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    env_file_values = load_env_file(args.env_file)

    base_url = args.base_url or env_value(env_file_values, "GOOGLE_GEMINI_BASE_URL")
    api_key = args.api_key or env_value(env_file_values, "GEMINI_API_KEY")
    model = args.model or env_value(env_file_values, "GEMINI_MODEL", "gemini-3-flash-preview")
    api_format = args.api_format or env_value(env_file_values, "GEMINI_API_FORMAT", "native")
    native_path = args.native_path or env_value(env_file_values, "GEMINI_NATIVE_PATH", "/v1beta")
    if not base_url:
        raise SystemExit("Missing GOOGLE_GEMINI_BASE_URL or --base-url")
    if not api_key:
        raise SystemExit("Missing GEMINI_API_KEY or --api-key")
    if api_format not in API_FORMATS:
        raise SystemExit(f"Unsupported API format {api_format!r}; choose one of {sorted(API_FORMATS)}")

    if args.list_models:
        list_models(base_url, api_key, args.timeout)
        return 0

    images = list(args.images)
    if args.manifest:
        images.extend(image_paths_from_manifest(args.manifest, args.image_column))
    if args.limit is not None:
        images = images[: args.limit]
    if not images:
        raise SystemExit("No images supplied. Pass image paths or --manifest.")

    prompt = read_prompt(args)
    out_fh = args.output.open("w", encoding="utf-8") if args.output else sys.stdout
    try:
        for image in images:
            record = review_one(
                image_path=image,
                reference_images=args.reference_image,
                base_url=base_url,
                api_key=api_key,
                model=model,
                api_format=api_format,
                native_path=native_path,
                prompt=prompt,
                max_tokens=args.max_tokens,
                timeout=args.timeout,
            )
            out_fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            out_fh.flush()
    finally:
        if args.output:
            out_fh.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
