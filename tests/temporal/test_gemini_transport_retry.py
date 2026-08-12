"""Tests for HTTP transport-level retry + backoff in gemini_solar_image_review.

Incident context (2026-07-16 22:46-23:30 NZST): a real sub2api gateway
outage (221x upstream 502, 84x 429, timeouts) was fossilized into permanent
`gemini_failed` anchors because the existing Q5.6 (a') batch retry policy
(batch attempt 1 -> attempt 2 -> salvage -> per-image fallback) has zero
backoff and treats all exceptions uniformly. These tests cover the new
transport-level retry layer added underneath that policy:

- retryable transport errors (429/502/503/504, connection errors, timeouts)
  are retried with exponential backoff + full jitter, re-acquiring the
  shared RateLimiter slot before every attempt;
- non-retryable errors (400/401/403) fail immediately, with zero retries;
- exhausted retries propagate the exact same error shape as before this
  change (so downstream `gemini_failed` handling is unaffected);
- schema/parse failures never enter this layer at all — Q5.6's own
  two-attempt-then-salvage semantics are untouched.

Second incident context (2026-07-17 11:37-14:43 NZST): all 11 sub2api
accounts hit a shared per-model Gemini rate limit simultaneously — a whole-
pool-exhaustion condition distinct from the transient blips above, self-
clearing after ~3h05m. The transport retry's ~1-2min budget exhausted
immediately, fossilizing 1,786 anchors, and a related bug (the RuntimeError
raised on exhausted HTTP-status retries never carried a `transport_retries`
attribute) made the audit trail misreport zero retries even when the
transport layer genuinely had retried. The tests under "pool exhaustion"
below cover the resulting second retry phase (`_is_pool_exhausted_response`
+ the extended flat-interval phase in `_post_with_transport_retry`) and the
`transport_retries`-on-exhausted-RuntimeError fix.

Mirrors the mocking style of tests/temporal/test_gemini_batch.py (poster
injection) but drops one level lower, monkeypatching `requests.post`
directly, since `poster` injection bypasses the transport layer entirely.
"""

from __future__ import annotations

import json
import threading
import time
from typing import Any, Callable

import pytest
import requests

from scripts.validation import gemini_solar_image_review as gsir
from scripts.validation.gemini_solar_image_review import (
    BatchPick,
    GeminiClientConfig,
    RateLimiter,
    post_chat_completion,
    post_native_generate_content,
    score_batch_with_fallback,
)


class FakeResponse:
    """Minimal stand-in for requests.Response used by the retry tests."""

    def __init__(self, status_code: int, json_data: dict[str, Any] | None = None, reason: str = ""):
        self.status_code = status_code
        self.reason = reason or f"status_{status_code}"
        self._json = json_data if json_data is not None else {}
        self.text = json.dumps(self._json)

    def raise_for_status(self) -> None:
        if 400 <= self.status_code < 600:
            http_error = requests.HTTPError(f"{self.status_code} {self.reason}")
            http_error.response = self  # type: ignore[attr-defined]
            raise http_error

    def json(self) -> dict[str, Any]:
        return self._json


def _native_ok(text: str = "OK") -> dict[str, Any]:
    return {"candidates": [{"content": {"parts": [{"text": text}]}}]}


def _pool_exhausted_response() -> "FakeResponse":
    """Real sub2api body from the 2026-07-17 incident (verbatim shape)."""
    return FakeResponse(
        503,
        json_data={
            "error": {
                "code": 503,
                "message": "No available Gemini accounts: no available accounts",
                "status": "INTERNAL",
            }
        },
        reason="Service Unavailable",
    )


def _sleep_recorder() -> tuple[list[float], Callable[[float], None]]:
    calls: list[float] = []

    def _sleep(seconds: float) -> None:
        calls.append(seconds)

    return calls, _sleep


# --- (a) retryable errors succeed without consuming a schema attempt --------


def test_native_502_twice_then_success(monkeypatch: pytest.MonkeyPatch) -> None:
    responses = [
        FakeResponse(502, reason="Bad Gateway"),
        FakeResponse(502, reason="Bad Gateway"),
        FakeResponse(200, json_data=_native_ok("hello")),
    ]
    call_count = {"n": 0}

    def fake_post(*_args: Any, **_kwargs: Any) -> FakeResponse:
        call_count["n"] += 1
        return responses[call_count["n"] - 1]

    monkeypatch.setattr(gsir.requests, "post", fake_post)
    sleeps, sleep_fn = _sleep_recorder()

    result = post_native_generate_content(
        base_url="https://stub.example",
        native_path="/v1beta",
        api_key="k",
        model="gemini-3-flash-preview",
        prompt="hi",
        image_paths=[],
        max_tokens=None,
        timeout=30,
        sleep_fn=sleep_fn,
    )

    assert call_count["n"] == 3
    assert result["_transport_retries"] == 2
    assert len(sleeps) == 2  # one backoff sleep before each retry
    assert gsir.native_response_text(result) == "hello"


def test_connection_error_twice_then_success(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts = {"n": 0}

    def fake_post(*_args: Any, **_kwargs: Any) -> FakeResponse:
        attempts["n"] += 1
        if attempts["n"] <= 2:
            raise requests.ConnectionError("connection reset")
        return FakeResponse(200, json_data=_native_ok("recovered"))

    monkeypatch.setattr(gsir.requests, "post", fake_post)
    sleeps, sleep_fn = _sleep_recorder()

    result = post_native_generate_content(
        base_url="https://stub.example",
        native_path="/v1beta",
        api_key="k",
        model="m",
        prompt="hi",
        image_paths=[],
        max_tokens=None,
        timeout=30,
        sleep_fn=sleep_fn,
    )

    assert attempts["n"] == 3
    assert result["_transport_retries"] == 2
    assert len(sleeps) == 2


def test_timeout_retries_then_success(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts = {"n": 0}

    def fake_post(*_args: Any, **_kwargs: Any) -> FakeResponse:
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise requests.Timeout("timeout waiting for user concurrency slot")
        return FakeResponse(200, json_data=_native_ok("ok"))

    monkeypatch.setattr(gsir.requests, "post", fake_post)
    sleeps, sleep_fn = _sleep_recorder()

    result = post_native_generate_content(
        base_url="https://stub.example",
        native_path="/v1beta",
        api_key="k",
        model="m",
        prompt="hi",
        image_paths=[],
        max_tokens=None,
        timeout=30,
        sleep_fn=sleep_fn,
    )
    assert attempts["n"] == 2
    assert result["_transport_retries"] == 1
    assert len(sleeps) == 1


def test_429_is_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts = {"n": 0}

    def fake_post(*_args: Any, **_kwargs: Any) -> FakeResponse:
        attempts["n"] += 1
        if attempts["n"] == 1:
            return FakeResponse(429, reason="Too Many Requests")
        return FakeResponse(200, json_data=_native_ok("ok"))

    monkeypatch.setattr(gsir.requests, "post", fake_post)
    _, sleep_fn = _sleep_recorder()

    result = post_native_generate_content(
        base_url="https://stub.example",
        native_path="/v1beta",
        api_key="k",
        model="m",
        prompt="hi",
        image_paths=[],
        max_tokens=None,
        timeout=30,
        sleep_fn=sleep_fn,
    )
    assert attempts["n"] == 2
    assert result["_transport_retries"] == 1


# --- (b) non-retryable errors fail immediately, zero retries ----------------


@pytest.mark.parametrize("status", [400, 401, 403])
def test_non_retryable_status_fails_immediately(monkeypatch: pytest.MonkeyPatch, status: int) -> None:
    call_count = {"n": 0}

    def fake_post(*_args: Any, **_kwargs: Any) -> FakeResponse:
        call_count["n"] += 1
        return FakeResponse(status, reason="client error")

    monkeypatch.setattr(gsir.requests, "post", fake_post)
    sleeps, sleep_fn = _sleep_recorder()

    with pytest.raises(RuntimeError, match=str(status)):
        post_native_generate_content(
            base_url="https://stub.example",
            native_path="/v1beta",
            api_key="k",
            model="m",
            prompt="hi",
            image_paths=[],
            max_tokens=None,
            timeout=30,
            sleep_fn=sleep_fn,
        )

    assert call_count["n"] == 1, "non-retryable status must not be retried"
    assert sleeps == []


def test_openai_400_no_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    call_count = {"n": 0}

    def fake_post(*_args: Any, **_kwargs: Any) -> FakeResponse:
        call_count["n"] += 1
        return FakeResponse(400, reason="Bad Request")

    monkeypatch.setattr(gsir.requests, "post", fake_post)
    _, sleep_fn = _sleep_recorder()

    with pytest.raises(RuntimeError, match="400"):
        post_chat_completion(
            base_url="https://stub.example",
            api_key="k",
            model="m",
            prompt="hi",
            image_paths=[],
            max_tokens=None,
            timeout=30,
            sleep_fn=sleep_fn,
        )
    assert call_count["n"] == 1


# --- (c) exhausted retries propagate the same error shape as today ---------


def test_502_exhausted_retries_propagates_runtime_error_same_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    call_count = {"n": 0}

    def fake_post(*_args: Any, **_kwargs: Any) -> FakeResponse:
        call_count["n"] += 1
        return FakeResponse(502, reason="Bad Gateway")

    monkeypatch.setattr(gsir.requests, "post", fake_post)
    sleeps, sleep_fn = _sleep_recorder()

    with pytest.raises(RuntimeError) as excinfo:
        post_native_generate_content(
            base_url="https://stub.example",
            native_path="/v1beta",
            api_key="k",
            model="m",
            prompt="hi",
            image_paths=[],
            max_tokens=None,
            timeout=30,
            sleep_fn=sleep_fn,
            max_transport_attempts=3,
        )

    # Same message shape as the pre-retry code: "<status> <reason> from <endpoint>: <body>".
    assert str(excinfo.value).startswith("502 Bad Gateway from")
    assert call_count["n"] == 3
    assert len(sleeps) == 2  # backoff before attempt 2 and attempt 3, none after the last


def test_connection_error_exhausted_retries_reraises_original_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_post(*_args: Any, **_kwargs: Any) -> FakeResponse:
        raise requests.ConnectionError("upstream refused connection")

    monkeypatch.setattr(gsir.requests, "post", fake_post)
    _, sleep_fn = _sleep_recorder()

    with pytest.raises(requests.ConnectionError):
        post_native_generate_content(
            base_url="https://stub.example",
            native_path="/v1beta",
            api_key="k",
            model="m",
            prompt="hi",
            image_paths=[],
            max_tokens=None,
            timeout=30,
            sleep_fn=sleep_fn,
            max_transport_attempts=2,
        )


# --- (d) backoff delays follow the schedule (sleep mocked) ------------------


def test_backoff_schedule_matches_base_multiplier_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    """base=2, multiplier=3, cap=60 -> ceilings 2, 6, 18, 54 for attempts 1..4."""

    def fake_post(*_args: Any, **_kwargs: Any) -> FakeResponse:
        return FakeResponse(503, reason="Service Unavailable")

    monkeypatch.setattr(gsir.requests, "post", fake_post)
    # Remove jitter for a deterministic schedule: uniform(0, ceiling) -> ceiling.
    monkeypatch.setattr(gsir.random, "uniform", lambda _lo, hi: hi)
    sleeps, sleep_fn = _sleep_recorder()

    with pytest.raises(RuntimeError):
        post_native_generate_content(
            base_url="https://stub.example",
            native_path="/v1beta",
            api_key="k",
            model="m",
            prompt="hi",
            image_paths=[],
            max_tokens=None,
            timeout=30,
            sleep_fn=sleep_fn,
            max_transport_attempts=5,
        )

    assert sleeps == [2.0, 6.0, 18.0, 54.0]


def test_backoff_delay_helper_respects_cap() -> None:
    delay = gsir._transport_backoff_delay(10, base_delay=2.0, multiplier=3.0, cap=60.0)
    assert 0 <= delay <= 60.0


# --- RateLimiter is re-acquired on every attempt, including retries --------


def test_limiter_reacquired_before_every_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    class CountingLimiter(RateLimiter):
        def __init__(self) -> None:
            super().__init__(None)
            self.wait_calls = 0

        def wait(self) -> None:  # noqa: D102 - test double
            self.wait_calls += 1

    attempts = {"n": 0}

    def fake_post(*_args: Any, **_kwargs: Any) -> FakeResponse:
        attempts["n"] += 1
        if attempts["n"] < 3:
            return FakeResponse(502, reason="Bad Gateway")
        return FakeResponse(200, json_data=_native_ok("ok"))

    monkeypatch.setattr(gsir.requests, "post", fake_post)
    limiter = CountingLimiter()
    _, sleep_fn = _sleep_recorder()

    post_native_generate_content(
        base_url="https://stub.example",
        native_path="/v1beta",
        api_key="k",
        model="m",
        prompt="hi",
        image_paths=[],
        max_tokens=None,
        timeout=30,
        sleep_fn=sleep_fn,
        limiter=limiter,
    )
    assert attempts["n"] == 3
    assert limiter.wait_calls == 3  # once per attempt, including the two retries


def test_limiter_has_no_startup_burst_from_first_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A large worker pool must not be dumped into sub2api at startup.

    The production contract is a strict shared request-start pacer from request
    one.  At 8 QPS, the 481st admitted request may start at 60 seconds, but no
    two starts may share the initial instant or any later 125 ms slot.
    """
    now = 0.0

    def fake_monotonic() -> float:
        return now

    def fake_sleep(seconds: float) -> None:
        nonlocal now
        now += seconds

    monkeypatch.setattr(gsir.time, "monotonic", fake_monotonic)
    monkeypatch.setattr(gsir.time, "sleep", fake_sleep)

    limiter = RateLimiter(8, max_in_flight=0)
    starts: list[float] = []
    for _ in range(481):
        limiter.acquire()
        starts.append(fake_monotonic())
        limiter.release()

    assert starts[0] == pytest.approx(0.0)
    assert starts[1] == pytest.approx(0.125)
    assert starts[-1] == pytest.approx(60.0)
    assert all(
        later - earlier == pytest.approx(0.125)
        for earlier, later in zip(starts, starts[1:])
    )


def test_limiter_caps_simultaneous_http_attempts(monkeypatch: pytest.MonkeyPatch) -> None:
    """QPS pacing must be paired with an explicit in-flight cap.

    A slow gateway response can leave many worker threads inside requests even
    when request starts are paced.  The CT production route uses this cap to
    avoid exhausting the gateway's per-user concurrency slots.
    """
    active = 0
    max_active = 0
    lock = threading.Lock()

    def fake_post(*_args: Any, **_kwargs: Any) -> FakeResponse:
        nonlocal active, max_active
        with lock:
            active += 1
            max_active = max(max_active, active)
        time.sleep(0.02)
        with lock:
            active -= 1
        return FakeResponse(200, json_data=_native_ok("ok"))

    monkeypatch.setattr(gsir.requests, "post", fake_post)
    limiter = RateLimiter(None, max_in_flight=2)
    errors: list[BaseException] = []

    def run_one() -> None:
        try:
            post_native_generate_content(
                base_url="https://stub.example",
                native_path="/v1beta",
                api_key="k",
                model="m",
                prompt="hi",
                image_paths=[],
                max_tokens=None,
                timeout=30,
                limiter=limiter,
            )
        except BaseException as exc:  # pragma: no cover - assertion below reports it
            errors.append(exc)

    threads = [threading.Thread(target=run_one) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert not errors
    assert max_active <= 2


def test_limiter_slot_released_when_before_attempt_fails() -> None:
    limiter = RateLimiter(None, max_in_flight=1)

    with pytest.raises(RuntimeError, match="stop"):
        gsir._post_with_transport_retry(
            lambda: FakeResponse(200, json_data=_native_ok("unreachable")),
            limiter=limiter,
            before_attempt=lambda _attempt: (_ for _ in ()).throw(RuntimeError("stop")),
        )

    # If the fail-closed callback leaked the slot, this acquire would block.
    limiter.acquire()
    limiter.release()


# --- integration: score_batch_with_fallback does not fossilize a transient ---
# --- gateway blip into a second (or failed) batch attempt -------------------


def test_batch_with_fallback_absorbs_transient_502_within_one_batch_attempt(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A 502-then-recover blip must resolve inside batch_attempt_1's transport
    retry, never triggering Q5.6's batch_attempt_2 or per-image fallback."""
    picks = []
    for i in range(1, 4):
        chip_path = tmp_path / f"chip_{i}.jpg"
        chip_path.write_bytes(b"FAKE" * 10)
        picks.append(
            BatchPick(chip_index=i, chip_path=chip_path, capture_date=f"2020-0{i}-01")
        )

    rows = [
        {
            "chip_index": p.chip_index,
            "pv_present": True,
            "confidence": 0.9,
            "quality_flag": "usable",
            "evidence": "ok",
            "notes": "",
        }
        for p in picks
    ]
    jsonl_text = "\n".join(json.dumps(r) for r in rows)

    call_count = {"n": 0}

    def fake_post(*_args: Any, **_kwargs: Any) -> FakeResponse:
        call_count["n"] += 1
        if call_count["n"] == 1:
            return FakeResponse(502, reason="Bad Gateway")
        return FakeResponse(200, json_data=_native_ok(jsonl_text))

    monkeypatch.setattr(gsir.requests, "post", fake_post)
    _, sleep_fn = _sleep_recorder()

    config = GeminiClientConfig(
        base_url="https://stub.example",
        api_key="k",
        model="gemini-3-flash-preview",
        api_format="native",
        native_path="/v1beta",
    )
    audit_records: list[dict[str, Any]] = []
    obs = score_batch_with_fallback(
        picks,
        config=config,
        audit_writer=audit_records.append,
        sleep_fn=sleep_fn,
    )

    assert len(obs) == 3
    assert all(o.decision_source == "gemini_batch" for o in obs)
    assert call_count["n"] == 2  # one HTTP retry, transparent to the batch policy
    assert [r["stage"] for r in audit_records] == ["batch_attempt_1"]
    assert audit_records[0]["transport_retries"] == 1


# --- (e) whole-account-pool-exhaustion: a second, longer retry phase -------
# Incident 2026-07-17 11:37-14:43 NZST (see module docstring).


def test_is_pool_exhausted_response_detects_real_incident_body() -> None:
    assert gsir._is_pool_exhausted_response(_pool_exhausted_response()) is True


def test_is_pool_exhausted_response_false_for_generic_503() -> None:
    resp = FakeResponse(503, reason="Service Unavailable")  # empty body, no marker
    assert gsir._is_pool_exhausted_response(resp) is False


def test_is_pool_exhausted_response_false_for_non_503_status() -> None:
    resp = FakeResponse(
        429,
        json_data={"error": {"message": "no available accounts"}},
        reason="Too Many Requests",
    )
    assert gsir._is_pool_exhausted_response(resp) is False


def test_pool_exhaustion_phase_recovers_after_extended_retries() -> None:
    """Standard phase (5 attempts) exhausts entirely on the pool-exhaustion
    signature; the extended phase then recovers on its 3rd attempt. Total
    reported retries must cover both phases."""
    call_count = {"n": 0}

    def fake_post() -> "FakeResponse":
        call_count["n"] += 1
        if call_count["n"] <= 7:  # phase 1 (5) + first 2 of phase 2
            return _pool_exhausted_response()
        return FakeResponse(200, json_data=_native_ok("recovered"))

    sleeps, sleep_fn = _sleep_recorder()
    response, retries = gsir._post_with_transport_retry(
        fake_post,
        max_attempts=5,
        pool_exhausted_max_attempts=8,
        sleep_fn=sleep_fn,
    )

    assert call_count["n"] == 8  # 5 (phase 1) + 3 (phase 2, recovers on the 3rd)
    assert response.status_code == 200
    assert retries == 7  # 4 phase-1 retries + 3 phase-2 retries
    assert sleeps[-3:] == [gsir.POOL_EXHAUSTED_RETRY_INTERVAL_SEC] * 3


def test_pool_exhaustion_both_phases_exhausted_raises_with_combined_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An outage that outlasts even the extended budget (this incident's did)
    still ultimately raises the same RuntimeError shape as any other
    exhausted-retries case, now carrying the combined retry count from both
    phases instead of silently reporting 0."""

    def fake_post(*_args: Any, **_kwargs: Any) -> "FakeResponse":
        return _pool_exhausted_response()

    monkeypatch.setattr(gsir.requests, "post", fake_post)
    _, sleep_fn = _sleep_recorder()

    with pytest.raises(RuntimeError) as excinfo:
        post_native_generate_content(
            base_url="https://stub.example",
            native_path="/v1beta",
            api_key="k",
            model="m",
            prompt="hi",
            image_paths=[],
            max_tokens=None,
            timeout=30,
            sleep_fn=sleep_fn,
            max_transport_attempts=2,
        )

    assert str(excinfo.value).startswith("503 Service Unavailable from")
    # phase 1: 2 attempts (1 retry) + phase 2: default 8 attempts (8 retries).
    assert excinfo.value.transport_retries == 1 + gsir.POOL_EXHAUSTED_MAX_ATTEMPTS


def test_generic_503_without_pool_marker_does_not_engage_extended_phase(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A plain 503 that doesn't match the pool-exhaustion signature must
    behave exactly as before this feature existed: no extended-phase sleeps
    or extra calls beyond the standard transport retry."""
    call_count = {"n": 0}

    def fake_post(*_args: Any, **_kwargs: Any) -> "FakeResponse":
        call_count["n"] += 1
        return FakeResponse(503, reason="Service Unavailable")  # generic, no marker

    monkeypatch.setattr(gsir.requests, "post", fake_post)
    sleeps, sleep_fn = _sleep_recorder()

    with pytest.raises(RuntimeError, match="503"):
        post_native_generate_content(
            base_url="https://stub.example",
            native_path="/v1beta",
            api_key="k",
            model="m",
            prompt="hi",
            image_paths=[],
            max_tokens=None,
            timeout=30,
            sleep_fn=sleep_fn,
            max_transport_attempts=3,
        )

    assert call_count["n"] == 3  # only the standard phase's 3 attempts
    assert gsir.POOL_EXHAUSTED_RETRY_INTERVAL_SEC not in sleeps


def test_exhausted_generic_5xx_runtime_error_now_carries_transport_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression test for the 2026-07-17 forensics finding: exhausting the
    standard retry budget on a persistently-bad HTTP status used to raise a
    RuntimeError with NO `transport_retries` attribute set at all, so
    callers' `getattr(exc, "transport_retries", 0)` silently read 0 even
    though real retries (with real backoff sleeps) had happened — every
    fossilized anchor from the outage showed `transport_retries: 0` in its
    audit despite the transport layer genuinely retrying underneath. This
    test guards the fix."""
    call_count = {"n": 0}

    def fake_post(*_args: Any, **_kwargs: Any) -> "FakeResponse":
        call_count["n"] += 1
        return FakeResponse(502, reason="Bad Gateway")

    monkeypatch.setattr(gsir.requests, "post", fake_post)
    _, sleep_fn = _sleep_recorder()

    with pytest.raises(RuntimeError) as excinfo:
        post_native_generate_content(
            base_url="https://stub.example",
            native_path="/v1beta",
            api_key="k",
            model="m",
            prompt="hi",
            image_paths=[],
            max_tokens=None,
            timeout=30,
            sleep_fn=sleep_fn,
            max_transport_attempts=4,
        )

    assert call_count["n"] == 4
    assert excinfo.value.transport_retries == 3  # was silently 0 (getattr default) before this fix


def test_pool_exhaustion_limiter_reacquired_during_extended_phase() -> None:
    class CountingLimiter(RateLimiter):
        def __init__(self) -> None:
            super().__init__(None)
            self.wait_calls = 0

        def wait(self) -> None:  # noqa: D102 - test double
            self.wait_calls += 1

    call_count = {"n": 0}

    def fake_post() -> "FakeResponse":
        call_count["n"] += 1
        if call_count["n"] <= 3:
            return _pool_exhausted_response()
        return FakeResponse(200, json_data=_native_ok("ok"))

    limiter = CountingLimiter()
    _, sleep_fn = _sleep_recorder()

    response, retries = gsir._post_with_transport_retry(
        fake_post,
        max_attempts=1,  # phase 1 exhausts immediately (1 attempt, pool-exhausted)
        pool_exhausted_max_attempts=5,
        limiter=limiter,
        sleep_fn=sleep_fn,
    )

    assert response.status_code == 200
    assert call_count["n"] == 4  # 1 (phase 1) + 3 (phase 2, recovers on the 3rd)
    assert limiter.wait_calls == 4  # once per attempt across both phases
