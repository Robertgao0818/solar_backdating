"""Tests for `score_batch_with_fallback` (Task D).

Uses an injected `poster` callable to simulate Gemini API responses — no real
HTTP calls. Verifies Q5.6 retry policy (a'):
- batch attempt 1 succeeds → all gemini_batch
- attempt 1 fails, attempt 2 succeeds → all gemini_batch
- both batches partial → salvage + per-image fallback for missing
- per-image also fails → gemini_failed terminal
- all-unusable batch is accepted (not auto-retried)
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from scripts.validation.gemini_solar_image_review import (
    BATCH_PROMPT_TEMPLATE,
    BatchPick,
    GeminiClientConfig,
    GeminiObservation,
    MatrixDatePick,
    MatrixTarget,
    _build_batch_prompt,
    _build_matrix_prompt,
    _identify_census_reference_chip,
    parse_jsonl_lenient,
    parse_matrix_jsonl_lenient,
    score_batch_with_fallback,
    score_target_date_matrix,
    validate_observation_schema,
    validate_matrix_limits,
)


@pytest.fixture
def gemini_config() -> GeminiClientConfig:
    return GeminiClientConfig(
        base_url="https://stub.example",
        api_key="stub_key",
        model="gemini-3-flash-preview",
        api_format="native",
        native_path="/v1beta",
    )


@pytest.fixture
def picks(tmp_path: Path) -> list[BatchPick]:
    out: list[BatchPick] = []
    for i in range(1, 4):
        chip_path = tmp_path / f"chip_{i}.jpg"
        chip_path.write_bytes(b"FAKE_JPEG_BYTES_FOR_TEST" * 100)
        out.append(
            BatchPick(
                chip_index=i,
                chip_path=chip_path,
                capture_date=f"2020-0{i}-01",
                version=str(100 + i),
                actual_zoom=20,
            )
        )
    return out


def _native_response(text: str) -> dict[str, Any]:
    return {"candidates": [{"content": {"parts": [{"text": text}]}}]}


def _openai_response(text: str) -> dict[str, Any]:
    return {"choices": [{"message": {"content": text}}]}


def _make_jsonl(entries: list[dict[str, Any]]) -> str:
    return "\n".join(json.dumps(e) for e in entries)


def _full_response_for_picks(picks: list[BatchPick], pv_present_pattern: list[bool | None]) -> str:
    rows: list[dict[str, Any]] = []
    for p, pv in zip(picks, pv_present_pattern):
        rows.append(
            {
                "chip_index": p.chip_index,
                "pv_present": pv,
                "confidence": 0.9 if pv is not None else None,
                "quality_flag": "usable" if pv is not None else "unusable",
                "evidence": f"stub for chip {p.chip_index}",
                "notes": "",
            }
        )
    return _make_jsonl(rows)


def test_parse_jsonl_lenient_strips_markdown_fences() -> None:
    text = '```json\n{"chip_index": 1, "pv_present": true}\n{"chip_index": 2, "pv_present": false}\n```'
    parsed, missing = parse_jsonl_lenient(text, expected_count=2)
    assert len(parsed) == 2
    assert missing == []


def test_parse_jsonl_lenient_skips_invalid_lines() -> None:
    text = (
        'Some preamble\n'
        '{"chip_index": 1, "pv_present": true}\n'
        'not json at all\n'
        '{"chip_index": 2, "pv_present": false}\n'
    )
    parsed, missing = parse_jsonl_lenient(text, expected_count=2)
    assert {p["chip_index"] for p in parsed} == {1, 2}
    assert missing == []


def test_parse_jsonl_lenient_reports_missing() -> None:
    text = '{"chip_index": 1, "pv_present": true}\n{"chip_index": 3, "pv_present": false}\n'
    parsed, missing = parse_jsonl_lenient(text, expected_count=3)
    assert {p["chip_index"] for p in parsed} == {1, 3}
    assert missing == [2]


def test_parse_jsonl_lenient_rejects_out_of_range_indices() -> None:
    text = '{"chip_index": 99, "pv_present": true}\n'
    parsed, missing = parse_jsonl_lenient(text, expected_count=3)
    assert parsed == []
    assert missing == [1, 2, 3]


def test_validate_observation_schema_accepts_valid() -> None:
    obj = {"chip_index": 1, "pv_present": True, "confidence": 0.9, "quality_flag": "usable"}
    assert validate_observation_schema(obj) is True


def test_validate_observation_schema_rejects_bad_quality_flag() -> None:
    obj = {"chip_index": 1, "pv_present": True, "confidence": 0.9, "quality_flag": "MAYBE"}
    assert validate_observation_schema(obj) is False


def test_validate_observation_schema_rejects_int_pv_present() -> None:
    obj = {"chip_index": 1, "pv_present": 1, "confidence": 0.9, "quality_flag": "usable"}
    assert validate_observation_schema(obj) is False


def test_batch_first_attempt_success(picks, gemini_config) -> None:
    calls: list[dict[str, Any]] = []

    def poster(**kwargs: Any) -> dict[str, Any]:
        calls.append({"prompt_len": len(kwargs.get("prompt", "")), "n_images": len(kwargs.get("image_paths", []))})
        return _native_response(_full_response_for_picks(picks, [True, False, True]))

    audit_records: list[dict] = []
    obs = score_batch_with_fallback(
        picks, config=gemini_config, audit_writer=audit_records.append, poster=poster
    )
    assert len(obs) == 3
    assert all(o.decision_source == "gemini_batch" for o in obs)
    assert obs[0].pv_present is True
    assert obs[1].pv_present is False
    assert obs[2].pv_present is True
    assert len(calls) == 1, "should not retry when first attempt succeeds"
    assert audit_records[0]["stage"] == "batch_attempt_1"


def test_batch_retries_when_first_attempt_partial(picks, gemini_config) -> None:
    """Attempt 1 returns only 2/3 valid rows; attempt 2 returns full → all gemini_batch."""
    attempt = {"n": 0}

    def poster(**kwargs: Any) -> dict[str, Any]:
        attempt["n"] += 1
        if attempt["n"] == 1:
            partial = _make_jsonl(
                [
                    {"chip_index": 1, "pv_present": True, "confidence": 0.9, "quality_flag": "usable", "evidence": "ok", "notes": ""},
                    {"chip_index": 2, "pv_present": False, "confidence": 0.9, "quality_flag": "usable", "evidence": "ok", "notes": ""},
                ]
            )
            return _native_response(partial)
        return _native_response(_full_response_for_picks(picks, [True, False, True]))

    audit_records: list[dict] = []
    obs = score_batch_with_fallback(
        picks, config=gemini_config, audit_writer=audit_records.append, poster=poster
    )
    assert len(obs) == 3
    assert all(o.decision_source == "gemini_batch" for o in obs)
    assert attempt["n"] == 2
    assert audit_records[0]["stage"] == "batch_attempt_1"
    assert audit_records[1]["stage"] == "batch_attempt_2"


def test_batch_falls_back_to_per_image(picks, gemini_config) -> None:
    """Both batch attempts return only chips 1+2; chip 3 must come from per-image fallback."""
    attempt = {"n": 0}

    def poster(**kwargs: Any) -> dict[str, Any]:
        attempt["n"] += 1
        if attempt["n"] in (1, 2):
            partial = _make_jsonl(
                [
                    {"chip_index": 1, "pv_present": True, "confidence": 0.9, "quality_flag": "usable", "evidence": "p1", "notes": ""},
                    {"chip_index": 2, "pv_present": False, "confidence": 0.9, "quality_flag": "usable", "evidence": "p2", "notes": ""},
                ]
            )
            return _native_response(partial)
        single = json.dumps(
            {
                "chip_index": 3,
                "pv_present": True,
                "confidence": 0.85,
                "quality_flag": "usable",
                "evidence": "per_image fallback for chip 3",
                "notes": "",
            }
        )
        return _native_response(single)

    audit_records: list[dict] = []
    obs = score_batch_with_fallback(
        picks, config=gemini_config, audit_writer=audit_records.append, poster=poster
    )
    assert len(obs) == 3
    sources = {o.chip_index: o.decision_source for o in obs}
    assert sources[1] == "gemini_batch"
    assert sources[2] == "gemini_batch"
    assert sources[3] == "gemini_per_image"
    assert attempt["n"] == 3
    assert audit_records[-1]["stage"] == "per_image_fallback"
    assert audit_records[-1]["chip_index"] == 3


def test_batch_per_image_also_fails_terminal_gemini_failed(picks, gemini_config) -> None:
    """If batch + per-image both fail for a chip, mark it gemini_failed."""

    def poster(**kwargs: Any) -> dict[str, Any]:
        n_images = len(kwargs.get("image_paths", []))
        if n_images > 1:
            partial = _make_jsonl(
                [
                    {"chip_index": 1, "pv_present": True, "confidence": 0.9, "quality_flag": "usable", "evidence": "p1", "notes": ""},
                ]
            )
            return _native_response(partial)
        raise RuntimeError("simulated upstream 503")

    obs = score_batch_with_fallback(picks, config=gemini_config, poster=poster)
    assert len(obs) == 3
    by_index = {o.chip_index: o for o in obs}
    assert by_index[1].decision_source == "gemini_batch"
    assert by_index[2].decision_source == "gemini_failed"
    assert by_index[3].decision_source == "gemini_failed"
    assert by_index[2].pv_present is None
    assert "per_image_call_error" in (by_index[2].notes or "")


def test_batch_unusable_results_accepted_not_retried(picks, gemini_config) -> None:
    """All-unusable batch is treated as legitimate observations, not retried."""
    calls = {"n": 0}

    def poster(**kwargs: Any) -> dict[str, Any]:
        calls["n"] += 1
        rows = [
            {
                "chip_index": p.chip_index,
                "pv_present": None,
                "confidence": None,
                "quality_flag": "unusable",
                "evidence": "chip is too cloudy",
                "notes": "",
            }
            for p in picks
        ]
        return _native_response(_make_jsonl(rows))

    obs = score_batch_with_fallback(picks, config=gemini_config, poster=poster)
    assert len(obs) == 3
    assert all(o.quality_flag == "unusable" for o in obs)
    assert all(o.decision_source == "gemini_batch" for o in obs)
    assert calls["n"] == 1, "should not retry when all rows valid even if all unusable"


def test_batch_invalid_schema_triggers_retry(picks, gemini_config) -> None:
    """Schema-invalid rows from attempt 1 do not count; attempt 2 succeeds → gemini_batch."""
    attempt = {"n": 0}

    def poster(**kwargs: Any) -> dict[str, Any]:
        attempt["n"] += 1
        if attempt["n"] == 1:
            broken = _make_jsonl(
                [
                    {"chip_index": 1, "pv_present": "yes", "confidence": 0.9, "quality_flag": "usable", "evidence": "", "notes": ""},
                    {"chip_index": 2, "pv_present": False, "confidence": 0.9, "quality_flag": "BADFLAG", "evidence": "", "notes": ""},
                    {"chip_index": 3, "pv_present": False, "confidence": 0.9, "quality_flag": "usable", "evidence": "", "notes": ""},
                ]
            )
            return _native_response(broken)
        return _native_response(_full_response_for_picks(picks, [True, False, True]))

    obs = score_batch_with_fallback(picks, config=gemini_config, poster=poster)
    assert len(obs) == 3
    assert all(o.decision_source == "gemini_batch" for o in obs)
    assert attempt["n"] == 2


def test_empty_picks_returns_empty(gemini_config) -> None:
    obs = score_batch_with_fallback([], config=gemini_config)
    assert obs == []


def test_openai_format_returns_choices_message_content(picks, gemini_config) -> None:
    openai_config = GeminiClientConfig(
        base_url="https://stub.example",
        api_key="stub_key",
        api_format="openai",
    )

    def poster(**kwargs: Any) -> dict[str, Any]:
        assert kwargs["api_format"] == "openai"
        return _openai_response(_full_response_for_picks(picks, [True, True, True]))

    obs = score_batch_with_fallback(picks, config=openai_config, poster=poster)
    assert len(obs) == 3
    assert all(o.pv_present is True for o in obs)


def _make_picks(dates: list[str], tmp_path: Path) -> list[BatchPick]:
    out: list[BatchPick] = []
    for i, d in enumerate(dates):
        chip = tmp_path / f"chip_{i+1}.png"
        chip.write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 32)
        out.append(BatchPick(chip_index=i + 1, capture_date=d, version=0, chip_path=chip))
    return out


def test_identify_census_reference_picks_latest_in_census_window(tmp_path: Path) -> None:
    picks = _make_picks(["2018-03-30", "2021-04-30", "2024-02-29", "2025-05-30"], tmp_path)
    idx, dt = _identify_census_reference_chip(picks, "2024-06-30")
    # Threshold = 2024-06-30 minus 6mo = 2023-12-30; latest >= threshold is 2025-05-30
    assert idx == 4
    assert dt == "2025-05-30"


def test_identify_census_reference_returns_none_for_walk_back_batch(tmp_path: Path) -> None:
    """Walk-back rounds with all picks pre-census get no reference clause."""
    picks = _make_picks(["2009-03-12", "2013-12-30", "2017-12-30"], tmp_path)
    idx, dt = _identify_census_reference_chip(picks, "2024-06-30")
    assert idx is None
    assert dt is None


def test_identify_census_reference_disabled_when_no_date_provided(tmp_path: Path) -> None:
    picks = _make_picks(["2024-06-30"], tmp_path)
    idx, dt = _identify_census_reference_chip(picks, None)
    assert idx is None and dt is None


def test_build_batch_prompt_omits_calibration_clause_without_census(tmp_path: Path) -> None:
    picks = _make_picks(["2018-06-30", "2020-01-01"], tmp_path)
    prompt = _build_batch_prompt(picks, census_mid_date_iso=None)
    assert "CALIBRATION:" not in prompt
    assert prompt.startswith(BATCH_PROMPT_TEMPLATE.format(count=2))


def test_build_batch_prompt_includes_calibration_clause_when_reference_present(tmp_path: Path) -> None:
    picks = _make_picks(["2018-06-30", "2024-02-29", "2025-05-30"], tmp_path)
    prompt = _build_batch_prompt(picks, census_mid_date_iso="2024-06-30")
    assert "CALIBRATION:" in prompt
    assert "chip 3" in prompt  # latest pick is index 3
    assert "2025-05-30" in prompt
    assert "ground truth" in prompt
    assert "GT-prior" in prompt


def test_build_batch_prompt_no_clause_for_old_only_batch(tmp_path: Path) -> None:
    picks = _make_picks(["2009-03-12", "2014-04-30"], tmp_path)
    prompt = _build_batch_prompt(picks, census_mid_date_iso="2024-06-30")
    assert "CALIBRATION:" not in prompt


def _matrix_dates(tmp_path: Path, dates: list[str]) -> list[MatrixDatePick]:
    out: list[MatrixDatePick] = []
    for idx, d in enumerate(dates, start=1):
        chip = tmp_path / f"matrix_{idx}.png"
        chip.write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 64)
        out.append(MatrixDatePick(date_index=idx, chip_path=chip, capture_date=d))
    return out


def _matrix_targets(labels: list[str]) -> list[MatrixTarget]:
    return [MatrixTarget(target_id=f"target_{label.lower()}", target_label=label) for label in labels]


def _matrix_response(date_picks: list[MatrixDatePick], targets: list[MatrixTarget]) -> str:
    rows: list[dict[str, Any]] = []
    cell_index = 1
    for date_pick in date_picks:
        for target in targets:
            rows.append(
                {
                    "cell_index": cell_index,
                    "date_index": date_pick.date_index,
                    "capture_date": date_pick.capture_date,
                    "target_id": target.target_id,
                    "target_label": target.target_label,
                    "pv_present": date_pick.date_index >= 2,
                    "confidence": 0.88,
                    "quality_flag": "usable",
                    "evidence": f"{target.target_label} stub",
                    "notes": "",
                }
            )
            cell_index += 1
    return _make_jsonl(rows)


def test_matrix_limits_reject_too_many_targets() -> None:
    with pytest.raises(ValueError, match="max_targets"):
        validate_matrix_limits(date_count=4, target_count=5, max_targets=4)


def test_matrix_limits_reject_too_many_cells() -> None:
    with pytest.raises(ValueError, match="hard_max_cells"):
        validate_matrix_limits(date_count=5, target_count=5, max_targets=6, hard_max_cells=24)


def test_build_matrix_prompt_lists_targets(tmp_path: Path) -> None:
    date_picks = _matrix_dates(tmp_path, ["2020-01-01", "2024-01-01"])
    targets = _matrix_targets(["T01", "T02"])
    prompt = _build_matrix_prompt(date_picks, targets)
    assert "D=2" in prompt
    assert "D*T=4" in prompt
    assert "T01: target_t01" in prompt
    assert "date-major order" in prompt


def test_parse_matrix_jsonl_reports_missing_and_rejects_bad_target(tmp_path: Path) -> None:
    date_picks = _matrix_dates(tmp_path, ["2020-01-01"])
    targets = _matrix_targets(["T01", "T02"])
    text = _make_jsonl(
        [
            {
                "cell_index": 1,
                "date_index": 1,
                "capture_date": "2020-01-01",
                "target_id": "target_t01",
                "target_label": "T01",
                "pv_present": True,
                "confidence": 0.9,
                "quality_flag": "usable",
            },
            {
                "cell_index": 2,
                "date_index": 1,
                "capture_date": "2020-01-01",
                "target_id": "wrong",
                "target_label": "T02",
                "pv_present": False,
                "confidence": 0.9,
                "quality_flag": "usable",
            },
        ]
    )
    parsed, missing = parse_matrix_jsonl_lenient(text, date_picks=date_picks, targets=targets)
    assert len(parsed) == 1
    assert missing == [(1, "T02")]


def test_score_target_date_matrix_success(tmp_path: Path, gemini_config) -> None:
    date_picks = _matrix_dates(tmp_path, ["2020-01-01", "2024-01-01"])
    targets = _matrix_targets(["T01", "T02"])
    calls: list[dict[str, Any]] = []

    def poster(**kwargs: Any) -> dict[str, Any]:
        calls.append({"n_images": len(kwargs["image_paths"]), "prompt": kwargs["prompt"]})
        return _native_response(_matrix_response(date_picks, targets))

    audit: list[dict[str, Any]] = []
    obs = score_target_date_matrix(
        date_picks,
        targets,
        config=gemini_config,
        poster=poster,
        audit_writer=audit.append,
    )
    assert len(obs) == 4
    assert all(o.decision_source == "gemini_matrix" for o in obs)
    assert calls[0]["n_images"] == 2
    assert "target_t01" in calls[0]["prompt"]
    assert audit[0]["stage"] == "matrix_attempt_1"


def test_score_target_date_matrix_missing_cells_become_failed(tmp_path: Path, gemini_config) -> None:
    date_picks = _matrix_dates(tmp_path, ["2020-01-01"])
    targets = _matrix_targets(["T01", "T02"])

    def poster(**_kwargs: Any) -> dict[str, Any]:
        partial = _make_jsonl(
            [
                {
                    "cell_index": 1,
                    "date_index": 1,
                    "capture_date": "2020-01-01",
                    "target_id": "target_t01",
                    "target_label": "T01",
                    "pv_present": True,
                    "confidence": 0.9,
                    "quality_flag": "usable",
                    "evidence": "ok",
                    "notes": "",
                }
            ]
        )
        return _native_response(partial)

    obs = score_target_date_matrix(date_picks, targets, config=gemini_config, poster=poster)
    by_label = {o.target_label: o for o in obs}
    assert by_label["T01"].decision_source == "gemini_matrix"
    assert by_label["T02"].decision_source == "gemini_failed"
