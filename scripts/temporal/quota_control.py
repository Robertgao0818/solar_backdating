"""Fail-closed Gemini quota accounting for a single production window.

The controller is intentionally small and transport-facing.  A caller reserves
one budget slot immediately before every real HTTP attempt and appends exactly
one JSONL record after that attempt returns.  Batch/schema retries therefore
consume the same ledger as first attempts; cache hits and offline GEHI work do
not.

The ledger is append-only and guarded by a separate ``flock`` file.  Only one
scan process should own a window, but the file lock also prevents an accidental
second process from interleaving rows.  The in-process reservation count closes
the race between concurrent anchor workers before their responses arrive.
"""

from __future__ import annotations

import fcntl
import json
import os
import tempfile
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping


UTC = timezone.utc
GRACE_SECONDS = 5 * 60


class QuotaControlError(RuntimeError):
    """Base class for errors that must stop the scan without making a verdict."""


class QuotaBudgetReached(QuotaControlError):
    """The configured work or hard transport budget will not admit another call."""


class QuotaPaused(QuotaControlError):
    """The shared circuit breaker is paused after an upstream quota/pool event."""


class QuotaLedgerAppendError(QuotaControlError):
    """A real attempt could not be durably recorded."""


def utc_now() -> datetime:
    return datetime.now(UTC)


def iso_z(value: datetime) -> str:
    return value.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed.replace(tzinfo=parsed.tzinfo or UTC).astimezone(UTC)


def _response_model_version(response: Any) -> str | None:
    if response is None:
        return None
    try:
        payload = response.json()
    except Exception:  # noqa: BLE001 - response bodies are diagnostic only
        return None
    if not isinstance(payload, Mapping):
        return None
    value = payload.get("modelVersion")
    return str(value).strip() if value not in (None, "") else None


def _response_body(response: Any) -> str:
    try:
        return str(response.text or "")
    except Exception:  # noqa: BLE001
        return ""


def classify_upstream(status: int | None, body: str, error: str | None = None) -> str:
    text = f"{body} {error or ''}".lower()
    if status == 429 and any(token in text for token in ("resource_exhausted", "quota", "rate limit")):
        return "upstream_429_resource_exhausted"
    if status == 429:
        return "http_429"
    if status == 503 and "no available accounts" in text:
        return "pool_503"
    if status in {502, 503, 504}:
        return f"http_{status}"
    if error:
        return "transport_exception"
    if status is None:
        return "unknown_transport"
    return "ok" if 200 <= status < 300 else f"http_{status}"


@dataclass(frozen=True)
class QuotaPolicy:
    window_id: str
    gross_safe_budget: int
    work_budget: int
    warning_budget: int
    canary_reserve: int
    retry_reserve: int
    reset_after: str
    grace_seconds: int = GRACE_SECONDS


class QuotaCircuitBreaker:
    """Thread-safe budget gate and JSONL attempt ledger."""

    def __init__(
        self,
        ledger_path: str | Path,
        *,
        policy: QuotaPolicy,
        pause_path: str | Path | None = None,
        model_tier: str = "primary",
    ) -> None:
        if not policy.window_id:
            raise ValueError("quota window_id must be non-empty")
        for name in ("gross_safe_budget", "work_budget", "warning_budget"):
            if int(getattr(policy, name)) <= 0:
                raise ValueError(f"{name} must be positive")
        if policy.work_budget > policy.gross_safe_budget:
            raise ValueError("work_budget cannot exceed gross_safe_budget")
        if policy.warning_budget > policy.work_budget:
            raise ValueError("warning_budget cannot exceed work_budget")
        self.ledger_path = Path(ledger_path)
        self.lock_path = Path(str(self.ledger_path) + ".lock")
        self.pause_path = Path(pause_path) if pause_path else self.ledger_path.parent / "PAUSED_QUOTA.json"
        self.policy = policy
        self.model_tier = model_tier
        self._lock = threading.RLock()
        self._reserved = 0
        self._in_flight = 0
        self._count = 0
        self._seen_logical: set[str] = set()
        self._pool_503_streak = 0
        self._paused_reason: str | None = None
        self._last_completed_anchor = ""
        self._last_completed_wave = ""
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        self._load_window_rows()
        self._load_existing_pause()

    @property
    def ledger_count(self) -> int:
        with self._lock:
            return self._count

    @property
    def in_flight(self) -> int:
        with self._lock:
            return self._in_flight

    @property
    def paused(self) -> bool:
        with self._lock:
            return self._paused_reason is not None

    def _load_window_rows(self) -> None:
        if not self.ledger_path.exists():
            return
        with self.ledger_path.open("r", encoding="utf-8") as handle:
            for raw in handle:
                if not raw.strip():
                    continue
                try:
                    row = json.loads(raw)
                except json.JSONDecodeError as exc:
                    raise QuotaLedgerAppendError(
                        f"quota ledger contains malformed JSON: {self.ledger_path}"
                    ) from exc
                if row.get("window_id") != self.policy.window_id:
                    continue
                self._count += 1
                logical = str(row.get("logical_call_id") or "")
                if logical:
                    self._seen_logical.add(logical)
                if row.get("anchor_id"):
                    self._last_completed_anchor = str(row["anchor_id"])
                if row.get("wave_id"):
                    self._last_completed_wave = str(row["wave_id"])

    def _load_existing_pause(self) -> None:
        """Honor a still-active pause when a worker process is restarted."""
        if not self.pause_path.exists():
            return
        try:
            payload = json.loads(self.pause_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise QuotaLedgerAppendError(
                f"cannot read quota pause marker {self.pause_path}: {exc}"
            ) from exc
        if payload.get("window_id") != self.policy.window_id:
            return
        resume_after = parse_timestamp(payload.get("resume_after"))
        if resume_after is not None and utc_now() < resume_after:
            self._paused_reason = str(payload.get("reason") or "paused_marker")

    def _write_pause_locked(self, reason: str) -> None:
        self._paused_reason = reason
        reset = parse_timestamp(self.policy.reset_after) or (utc_now() + timedelta(hours=5))
        resume_after = reset + timedelta(seconds=self.policy.grace_seconds)
        payload = {
            "schema_version": "quota_pause_v1",
            "status": "PAUSED_QUOTA",
            "reason": reason,
            "window_id": self.policy.window_id,
            "ledger_path": str(self.ledger_path),
            "ledger_count": self._count,
            "in_flight_count": self._in_flight,
            "reserved_count": self._reserved,
            "resume_after": iso_z(resume_after),
            "reset_after": iso_z(reset),
            "last_completed_anchor": self._last_completed_anchor,
            "last_completed_wave": self._last_completed_wave,
            "model_tier": self.model_tier,
        }
        self.pause_path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(
            prefix=self.pause_path.name + ".", dir=str(self.pause_path.parent)
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, self.pause_path)
        except Exception:
            Path(temp_name).unlink(missing_ok=True)
            raise

    def _trip_locked(self, reason: str, exc_type: type[QuotaControlError] = QuotaPaused) -> None:
        if self._paused_reason is None:
            self._write_pause_locked(reason)
        raise exc_type(
            f"quota circuit breaker {self._paused_reason}: "
            f"window={self.policy.window_id} ledger={self._count} "
            f"in_flight={self._in_flight}"
        )

    def before_attempt(self, context: Mapping[str, Any], *, attempt_number: int) -> None:
        """Reserve one HTTP attempt, raising before any network I/O."""
        logical = str(context.get("logical_call_id") or "")
        with self._lock:
            if self._paused_reason is not None:
                self._trip_locked(self._paused_reason)
            observed = self._count + self._reserved
            is_existing_logical = logical in self._seen_logical or attempt_number > 1
            limit = self.policy.gross_safe_budget if is_existing_logical else self.policy.work_budget
            if observed >= limit:
                reason = "hard_budget" if limit == self.policy.gross_safe_budget else "work_budget"
                self._trip_locked(reason, QuotaBudgetReached)
            self._reserved += 1
            self._in_flight += 1

    def _append_locked(self, row: Mapping[str, Any]) -> None:
        line = json.dumps(dict(row), ensure_ascii=False, sort_keys=True)
        try:
            fd = os.open(self.lock_path, os.O_RDWR | os.O_CREAT, 0o644)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX)
                with self.ledger_path.open("a", encoding="utf-8") as handle:
                    handle.write(line + "\n")
                    handle.flush()
                    os.fsync(handle.fileno())
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)
        except Exception as exc:  # noqa: BLE001
            raise QuotaLedgerAppendError(
                f"failed to append quota ledger row {self.ledger_path}: {exc}"
            ) from exc

    def after_attempt(
        self,
        context: Mapping[str, Any],
        *,
        attempt_number: int,
        status: int | None,
        response_body: str = "",
        returned_model_version: str | None = None,
        error: str | None = None,
    ) -> None:
        """Durably record one completed HTTP attempt and trip on quota signals."""
        with self._lock:
            self._reserved = max(0, self._reserved - 1)
            self._in_flight = max(0, self._in_flight - 1)
            error_class = classify_upstream(status, response_body, error)
            row = {
                "schema_version": "quota_ledger_v1",
                "window_id": self.policy.window_id,
                "ts_utc": iso_z(utc_now()),
                "anchor_id": context.get("anchor_id", ""),
                "wave_id": context.get("wave_id", ""),
                "run_id": context.get("run_id", ""),
                "logical_call_id": context.get("logical_call_id", ""),
                "round_id": context.get("round_id", ""),
                "round_type": context.get("round_type", ""),
                "chunk_index": context.get("chunk_index", ""),
                "n_picks": context.get("n_picks", ""),
                "attempt_kind": (
                    "transport_retry"
                    if attempt_number > 1
                    else context.get("attempt_kind", "http")
                ),
                "transport_attempt": attempt_number,
                "requested_alias": context.get("requested_alias", ""),
                "model_tier": context.get("model_tier", self.model_tier),
                "returned_model_version": returned_model_version,
                "http_status": status,
                "counted": True,
                "error_class": error_class,
                "error": (error or "")[:500],
            }
            self._append_locked(row)
            self._count += 1
            logical = str(context.get("logical_call_id") or "")
            if logical:
                self._seen_logical.add(logical)
            if context.get("anchor_id"):
                self._last_completed_anchor = str(context["anchor_id"])
            if context.get("wave_id"):
                self._last_completed_wave = str(context["wave_id"])

            if error_class == "upstream_429_resource_exhausted":
                self._trip_locked("upstream_429")
            if error_class == "pool_503":
                self._pool_503_streak += 1
                if self._pool_503_streak >= 2:
                    self._trip_locked("pool_503")
            else:
                self._pool_503_streak = 0

            if self._count >= self.policy.gross_safe_budget:
                self._trip_locked("hard_budget", QuotaBudgetReached)

    def http_before(self, context: Mapping[str, Any], attempt_number: int) -> None:
        self.before_attempt(context, attempt_number=attempt_number)

    def http_after(
        self,
        context: Mapping[str, Any],
        attempt_number: int,
        response: Any = None,
        error: BaseException | None = None,
    ) -> None:
        status = getattr(response, "status_code", None) if response is not None else None
        self.after_attempt(
            context,
            attempt_number=attempt_number,
            status=int(status) if status is not None else None,
            response_body=_response_body(response),
            returned_model_version=_response_model_version(response),
            error=f"{type(error).__name__}: {error}" if error else None,
        )

    def mark_wave(self, wave_id: str) -> None:
        with self._lock:
            self._last_completed_wave = wave_id

    def summary(self) -> dict[str, Any]:
        with self._lock:
            return {
                "window_id": self.policy.window_id,
                "ledger_count": self._count,
                "reserved_count": self._reserved,
                "in_flight_count": self._in_flight,
                "gross_safe_budget": self.policy.gross_safe_budget,
                "work_budget": self.policy.work_budget,
                "warning_budget": self.policy.warning_budget,
                "remaining_gross": self.policy.gross_safe_budget - self._count - self._reserved,
                "remaining_work": self.policy.work_budget - self._count - self._reserved,
                "paused_reason": self._paused_reason,
            }
