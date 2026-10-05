"""Quota classification for the full-timeline Lite runner."""

import json
from pathlib import Path

import pytest

from scripts.temporal.quota_control import QuotaCircuitBreaker, QuotaPaused, QuotaPolicy, classify_upstream
from scripts.temporal import run_reference_fulltimeline_scan as scan
from scripts.temporal.run_reference_fulltimeline_scan import (
    SCORE_IN_FLIGHT,
    WINDOW_ID,
    RawLog,
    Scorer,
    _drain,
    _make_breaker,
    _recheck,
)
from scripts.validation import gemini_solar_image_review as gemini_http


def _policy() -> QuotaPolicy:
    return QuotaPolicy(
        window_id="quota-classify-test",
        gross_safe_budget=100,
        work_budget=50,
        warning_budget=10,
        canary_reserve=0,
        retry_reserve=0,
        reset_after="2026-10-04T16:00:00Z",
    )


def _record_pool(breaker: QuotaCircuitBreaker, logical_id: str) -> None:
    breaker.after_attempt(
        {"logical_call_id": logical_id, "attempt_kind": "first_pass"},
        attempt_number=1,
        status=503,
        response_body="No available Gemini accounts: no available accounts",
    )


def test_concurrency_slot_timeout_is_not_a_quota_pause() -> None:
    body = (
        '{"error":{"code":429,"message":"timeout waiting for account concurrency slot",'
        '"status":"RESOURCE_EXHAUSTED"}}'
    )
    assert classify_upstream(429, body) == "http_429"


def test_upstream_quota_429_still_opens_the_pause() -> None:
    body = '{"error":{"status":"RESOURCE_EXHAUSTED","message":"Quota exceeded for this project"}}'
    assert classify_upstream(429, body) == "upstream_429_resource_exhausted"


def test_score_concurrency_stays_within_one_account_slot() -> None:
    assert SCORE_IN_FLIGHT <= 8


def test_default_two_pool_503s_pause(tmp_path: Path) -> None:
    breaker = QuotaCircuitBreaker(
        tmp_path / "ledger.jsonl",
        policy=_policy(),
        pause_path=tmp_path / "pause.json",
    )
    _record_pool(breaker, "a")
    with pytest.raises(QuotaPaused):
        _record_pool(breaker, "b")


def test_fulltimeline_breaker_waits_through_a_short_pool_burst(tmp_path: Path) -> None:
    breaker = _make_breaker(
        tmp_path,
        {"gross_safe_budget": 100, "work_budget": 50, "warning_budget": 10},
    )
    assert breaker.pool_503_trip_after >= 24
    for index in range(8):
        _record_pool(breaker, f"burst-{index}")
    assert breaker.paused is False


def _budget() -> dict[str, int]:
    return {
        "gross_safe_budget": 100,
        "work_budget": 50,
        "warning_budget": 10,
        "max_output_tokens": 2048,
    }


def _scorer(tmp_path: Path) -> Scorer:
    return Scorer(tmp_path, RawLog(tmp_path / "observations" / "raw.jsonl"), _budget())


def _request(image: Path, capture_date: str) -> dict[str, object]:
    return {
        "arm": "t3",
        "anchor_id": "anchor-a",
        "capture_date": capture_date,
        "stage": "first_pass",
        "degradation": "candidate_only_reference_unavailable",
        "candidate_sha256": "abc",
        "aerial_sha256": "",
        "satellite_sha256": "",
        "prompt": "candidate only",
        "image_paths": [str(image), str(image), str(image)],
        "image_roles": ["candidate", "aerial_reference", "satellite_reference"],
        "grid_id": "CPT0000",
    }


class _Response:
    def __init__(self, payload: dict[str, object]) -> None:
        self.status_code = 200
        self.reason = "OK"
        self._payload = payload
        self.text = json.dumps(payload)

    def json(self) -> dict[str, object]:
        return self._payload

    def raise_for_status(self) -> None:
        return None


def _lite_response() -> _Response:
    label = {
        "candidate_index": 1,
        "pv_present": False,
        "quality_flag": "usable",
        "target_match": True,
        "confidence": 0.9,
        "evidence": "bare roof segment",
        "unknown_reason": None,
        "object_change_flag": False,
    }
    return _Response(
        {
            "modelVersion": "gemini-3.1-flash-lite",
            "candidates": [
                {
                    "finishReason": "STOP",
                    "content": {"parts": [{"text": json.dumps(label)}]},
                }
            ],
            "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 8},
        }
    )


def test_drain_retries_after_a_pause_that_records_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    image = tmp_path / "chip.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\nnot-a-real-png")
    pause_path = tmp_path / "plan" / "PAUSED_QUOTA.json"
    pause_path.parent.mkdir(parents=True)
    pause_path.write_text(
        json.dumps(
            {
                "window_id": WINDOW_ID,
                "reason": "pool_503",
                "resume_after": "2099-01-01T00:00:00Z",
                "status": "PAUSED_QUOTA",
            }
        ),
        encoding="utf-8",
    )
    calls: list[str] = []

    def fake_post(*_args: object, **_kwargs: object) -> _Response:
        calls.append("post")
        return _lite_response()

    def fast_sleep(_seconds: float) -> None:
        current = json.loads(pause_path.read_text(encoding="utf-8"))
        current["resume_after"] = "2020-01-01T00:00:00Z"
        pause_path.write_text(json.dumps(current), encoding="utf-8")

    monkeypatch.setattr(gemini_http.requests, "post", fake_post)
    monkeypatch.setattr(scan.time, "sleep", fast_sleep)
    scorer = _scorer(tmp_path)
    requests = [_request(image, "2020-01-01"), _request(image, "2020-06-01")]
    _drain(scorer, requests, in_flight=2)
    assert calls == ["post", "post"]
    assert [row.get("call_status") for row in scorer.raw.rows] == ["ok", "ok"]
    assert scorer.breaker.paused is False


def test_one_image_t3_is_not_posted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    image = tmp_path / "chip.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\nnot-a-real-png")

    def forbid_post(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("a one-image T3 must not call Gemini")

    monkeypatch.setattr(gemini_http.requests, "post", forbid_post)
    scorer = _scorer(tmp_path)
    request = _request(image, "2020-01-01")
    request["image_paths"] = [str(image)]
    assert scorer.issue(request) == "not_sent"
    assert [row.get("call_status") for row in scorer.raw.rows] == ["not_sent"]
    assert scorer.raw.rows[0]["n_images"] == 1


def test_drain_stops_when_a_batch_records_nothing_without_a_pause(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def forbid_post(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("a stall without a pause must not call Gemini")

    monkeypatch.setattr(gemini_http.requests, "post", forbid_post)
    scorer = _scorer(tmp_path)
    scorer.stop.set()
    _drain(scorer, [_request(tmp_path / "missing.png", "2020-01-01")], in_flight=1)
    assert scorer.new_requests == 0
    assert scorer.raw.rows == []


def test_stale_recheck_sentinel_is_not_finished(tmp_path: Path) -> None:
    observations = tmp_path / "observations"
    observations.mkdir()
    (observations / "RECHECK_DONE.json").write_text("{}\n", encoding="utf-8")
    scorer = _scorer(tmp_path)
    with pytest.raises(SystemExit, match="render index missing"):
        _recheck(tmp_path, {"anchors": [], "candidates": [], "references": []}, scorer)


def test_recheck_done_after_score_done_is_finished(tmp_path: Path) -> None:
    observations = tmp_path / "observations"
    observations.mkdir()
    (observations / "RECHECK_DONE.json").write_text("{}\n", encoding="utf-8")
    (observations / "SCORE_DONE.json").write_text("{}\n", encoding="utf-8")
    scorer = _scorer(tmp_path)
    assert _recheck(tmp_path, {"anchors": [], "candidates": [], "references": []}, scorer) == 0
