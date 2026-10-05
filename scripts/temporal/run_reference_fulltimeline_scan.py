#!/usr/bin/env python3
"""Download, render, and score the frozen 10-grid full-timeline pilot.

Stages resume from files already on disk. A second process does not delete
locks, raw responses, or fingerprint-matched successes. Stdout is a stable
score summary. Progress goes to stderr.

The model alias is gemini-3.1-flash-lite. A foreign modelVersion stops the
run. This script does not tune the prompt from development-grid labels and
does not call the adaptive early-stop picker.
"""

from __future__ import annotations

import argparse
import csv
import fcntl
import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Mapping, Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.temporal.fulltimeline_manifest import assemble_manifest, scoring_requests  # noqa: E402
from scripts.temporal.reference_fulltimeline import (  # noqa: E402
    MODEL_ALIAS,
    POLICY_CENSUS_DATE,
    POLICY_CENSUS_PRECISION,
    PROMPT,
    REFERENCE_CHECK_PROMPT,
    RESPONSE_SCHEMA,
    RawOverwriteError,
    admit_model_version,
    apply_manifest_gap_states,
    assemble_image_roles,
    cache_key,
    commit_raw_record,
    infer_interval,
    outstanding_requests,
    plan_rechecks,
    prompt_fingerprint,
    remaining_attempts,
    request_fingerprint,
    select_techcheck,
    timeline_observations,
)


DEFAULT_OUTPUT = (
    Path.home() / "zasolar_data/geid_temporal/reference_fulltimeline_lite_10grid_20261004"
)
AERIAL_ROOT = Path.home() / "zasolar_data/tiles/cape_town/aerial_2025"
RUN_ID = "reference_fulltimeline_lite_10grid_20261004"
QPS = 6
TECH_IN_FLIGHT = 12
# Steady-state ceiling. QPS is the ramp: the first second starts about 6
# requests, not this many at once. Sub2API balances each request onto an
# account; a same-instant dump fills one account's concurrency slots.
SCORE_IN_FLIGHT = 40
# One pause used to leave every remaining date queued behind the stop flag.
# A short chunk is cancelled and retried after the quota sleep.
SCORE_CHUNK = 64
ATTEMPT_LIMIT = 3
INITIAL_MAX_TOKENS = 2048
WINDOW_ID = RUN_ID
# A few gigabytes must remain for the response ledger. Below this the run
# stops with every raw row already fsynced, and the same command resumes.
DISK_FLOOR_BYTES = 3 * 1024**3


def _log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _write_csv(path: Path, rows: Sequence[Mapping[str, Any]], fields: Sequence[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fields), lineterminator="\n", extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})
    temporary.replace(path)


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(dict(payload), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def _file_sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _hold_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        handle.close()
        raise SystemExit(f"runner lock is already held: {path}") from exc
    return handle


def _load_plan(root: Path) -> dict[str, list[dict[str, str]]]:
    plan = root / "plan"
    required = ["grid_selection.csv", "anchor_selection.csv", "candidates.csv", "references.csv", "run_lock.json"]
    missing = [name for name in required if not (plan / name).is_file()]
    if missing:
        raise SystemExit(f"frozen plan is incomplete: {missing}")
    return {
        "grids": _read_csv(plan / "grid_selection.csv"),
        "anchors": _read_csv(plan / "anchor_selection.csv"),
        "candidates": _read_csv(plan / "candidates.csv"),
        "references": _read_csv(plan / "references.csv"),
    }


class RawLog:
    """Append-only raw response log. A successful fingerprint is never rewritten."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.rows: list[dict[str, Any]] = []
        self.lock = threading.Lock()
        if self.path.exists():
            with self.path.open(encoding="utf-8") as handle:
                for line_number, line in enumerate(handle, start=1):
                    if not line.strip():
                        continue
                    try:
                        self.rows.append(json.loads(line))
                    except json.JSONDecodeError as exc:
                        raise SystemExit(f"malformed raw log {self.path}:{line_number}") from exc

    def commit(self, record: Mapping[str, Any]) -> str:
        with self.lock:
            before = len(self.rows)
            try:
                status = commit_raw_record(self.rows, record)
            except RawOverwriteError:
                raise
            if status != "appended":
                return status
            try:
                with self.path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(dict(record), ensure_ascii=False, sort_keys=True) + "\n")
                    handle.flush()
                    os.fsync(handle.fileno())
            except Exception:
                del self.rows[before:]
                raise
            return status


def _summary_counts(root: Path) -> dict[str, Any]:
    lock = json.loads((root / "plan" / "run_lock.json").read_text(encoding="utf-8"))
    return dict(lock["summary"])


def _budget_numbers(summary: Mapping[str, Any]) -> dict[str, int]:
    n_value = int(summary["N"])
    logical = int(summary["H"]) + int(summary["H_pair"]) + 8 * n_value + 100
    work = logical + max(50, logical // 5)
    gross = work + 4 * logical
    warning = max(1, work // 2)
    if not warning < work < gross:
        raise RuntimeError("quota budgets are not ordered")
    return {"logical_calls": logical, "work_budget": work, "gross_safe_budget": gross, "warning_budget": warning}


def _ensure_call_budget(root: Path, summary: Mapping[str, Any]) -> dict[str, Any]:
    path = root / "plan" / "budget.json"
    payload: dict[str, Any] = {}
    if path.exists():
        payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("work_budget") and payload.get("gross_safe_budget"):
        return payload
    payload.update(_budget_numbers(summary))
    payload.update(
        {
            "run_id": RUN_ID,
            "status": "provisional_call_budget",
            "frozen": False,
            "N": summary["N"],
            "H": summary["H"],
            "H_pair": summary["H_pair"],
            "n_paired_anchors": summary["n_paired_anchors"],
            "n_review_anchors": summary["n_review_anchors"],
            "model_alias": MODEL_ALIAS,
            "temperature": 0,
            "max_output_tokens": INITIAL_MAX_TOKENS,
            "thinking_level": "",
            "illustrative_example_used_as_count": 0,
            "image_preprocessing": "raw_bytes_base64_no_transform",
        }
    )
    _write_json(path, payload)
    return payload


def _make_breaker(root: Path, budget: Mapping[str, Any], *, window_id: str = WINDOW_ID):
    from datetime import timedelta

    from scripts.temporal.quota_control import QuotaCircuitBreaker, QuotaPolicy, iso_z, utc_now

    policy = QuotaPolicy(
        window_id=window_id,
        gross_safe_budget=int(budget["gross_safe_budget"]),
        work_budget=int(budget["work_budget"]),
        warning_budget=int(budget["warning_budget"]),
        canary_reserve=0,
        retry_reserve=0,
        reset_after=iso_z(utc_now() + timedelta(minutes=20)),
    )
    return QuotaCircuitBreaker(
        root / "quota_ledger.jsonl",
        policy=policy,
        pause_path=root / "plan" / "PAUSED_QUOTA.json",
        model_tier="primary",
        # Two gateway 503s used to pause for the whole quota window. This
        # model also returns that signature for a few-second capacity reset.
        # The transport retry absorbs a short burst; a sustained outage still pauses.
        pool_503_trip_after=24,
    )


def _client_settings() -> dict[str, str]:
    from scripts.validation.gemini_solar_image_review import env_value, load_env_file

    values = load_env_file(PROJECT_ROOT / ".env.gemini.local")
    model = env_value(values, "GEMINI_MODEL", MODEL_ALIAS)
    if model != MODEL_ALIAS:
        raise SystemExit(f"refusing model alias {model!r}; this pilot is locked to {MODEL_ALIAS}")
    return {
        "base_url": env_value(values, "GOOGLE_GEMINI_BASE_URL"),
        "api_key": env_value(values, "GEMINI_API_KEY"),
        "native_path": env_value(values, "GEMINI_NATIVE_PATH", "/v1beta"),
        "model": MODEL_ALIAS,
    }


def _fingerprint(record_fields: Mapping[str, Any]) -> str:
    return request_fingerprint(record_fields)


def _contract(params: Mapping[str, Any], *, arm: str, stage: str, degradation: str, shas: Mapping[str, str], reference: bool) -> dict[str, Any]:
    prompt = str(params.get("prompt") or "")
    if not prompt:
        prompt = REFERENCE_CHECK_PROMPT if reference else PROMPT
    return {
        "prompt_sha256": prompt_fingerprint(prompt),
        "model_alias": MODEL_ALIAS,
        "model_version": str(params.get("model_version") or ""),
        "temperature": 0,
        "thinking_level": str(params.get("thinking_level") or ""),
        "max_output_tokens": int(params["max_output_tokens"]),
        "image_preprocessing": str(params.get("image_preprocessing") or "raw_bytes_base64_no_transform"),
        "arm": arm,
        "stage": stage,
        "degradation": degradation,
        "candidate_sha256": shas.get("candidate", ""),
        "aerial_sha256": shas.get("aerial", ""),
        "satellite_sha256": shas.get("satellite", ""),
    }


def _classify_body(raw: Mapping[str, Any] | None, *, frozen_version: str | None) -> dict[str, Any]:
    from scripts.validation.gemini_solar_image_review import extract_json_object, native_response_text

    from scripts.temporal.reference_fulltimeline import interpret_model_payload

    if not isinstance(raw, Mapping):
        return {"call_status": "transport_failed", "verdict": None, "model_version": "", "reason": "no_body"}
    version = str(raw.get("modelVersion") or "").strip()
    if not admit_model_version(version, frozen_version):
        return {
            "call_status": "model_version_mismatch",
            "verdict": None,
            "model_version": version,
            "reason": "model_version",
        }
    finish = ""
    candidates = raw.get("candidates") or []
    if candidates and isinstance(candidates[0], Mapping):
        finish = str(candidates[0].get("finishReason") or "")
    usage = raw.get("usageMetadata") if isinstance(raw.get("usageMetadata"), Mapping) else {}
    base = {
        "model_version": version,
        "finish_reason": finish,
        "prompt_tokens": usage.get("promptTokenCount", ""),
        "output_tokens": usage.get("candidatesTokenCount", ""),
    }
    if finish == "MAX_TOKENS":
        return {**base, "call_status": "truncated", "verdict": None, "reason": "max_tokens"}
    try:
        payload = extract_json_object(native_response_text(dict(raw)))
    except Exception as exc:  # noqa: BLE001 - invalid model text is a call outcome
        return {**base, "call_status": "invalid_response", "verdict": None, "reason": type(exc).__name__}
    interpreted = interpret_model_payload(payload if isinstance(payload, Mapping) else None)
    if not interpreted.get("ok"):
        return {**base, "call_status": "invalid_response", "verdict": None, "reason": interpreted.get("reason", "")}
    return {
        **base,
        "call_status": "ok",
        "verdict": interpreted["verdict"],
        "reason": "",
        "quality_flag": interpreted["quality_flag"],
        "target_match": interpreted["target_match"],
        "pv_present": interpreted["pv_present"],
        "evidence": interpreted["evidence"],
        "unknown_reason": interpreted["unknown_reason"],
        "object_change_flag": interpreted["object_change_flag"],
        "confidence": interpreted["confidence"],
    }


class Scorer:
    def __init__(
        self,
        root: Path,
        raw: RawLog,
        budget: Mapping[str, Any],
        *,
        run_id: str = RUN_ID,
        window_id: str = WINDOW_ID,
    ) -> None:
        self.root = root
        self.raw = raw
        self.budget = dict(budget)
        self.run_id = run_id
        self.window_id = window_id
        self.settings = _client_settings()
        self.breaker = _make_breaker(root, budget, window_id=window_id)
        self.stop = threading.Event()
        self.pause_reason = ""
        self.budget_reason = ""
        self.mismatch = ""
        self.resumed_after_pause = False
        self.new_requests = 0
        self._count_lock = threading.Lock()

    def refresh_breaker(self) -> None:
        self.breaker = _make_breaker(self.root, self.budget, window_id=self.window_id)

    def _record(self, request: Mapping[str, Any], call: Mapping[str, Any], *, elapsed: float, body: Mapping[str, Any] | None, reference_only: int) -> None:
        shas = {
            "candidate": str(request.get("candidate_sha256") or ""),
            "aerial": str(request.get("aerial_sha256") or ""),
            "satellite": str(request.get("satellite_sha256") or ""),
        }
        contract = _contract(
            {
                **self.budget,
                "model_version": str(self.budget.get("model_version") or self._frozen_version()),
                "prompt": str(request.get("prompt") or ""),
            },
            arm=str(request["arm"]),
            stage=str(request["stage"]),
            degradation=str(request.get("degradation") or ""),
            shas=shas,
            reference=bool(reference_only),
        )
        image_paths = [str(path) for path in request.get("image_paths") or []]
        image_bytes = []
        for path in image_paths:
            file = Path(path)
            image_bytes.append(file.stat().st_size if file.is_file() else 0)
        record = {
            "arm": request["arm"],
            "anchor_id": request["anchor_id"],
            "capture_date": str(request["capture_date"])[:10],
            "stage": request["stage"],
            "call_status": call["call_status"],
            "verdict": call.get("verdict"),
            "quality_flag": call.get("quality_flag", ""),
            "target_match": call.get("target_match"),
            "model_version": call.get("model_version") or str(self.budget.get("model_version") or ""),
            "fingerprint": _fingerprint(contract),
            "candidate_sha256": shas["candidate"],
            "aerial_sha256": shas["aerial"],
            "satellite_sha256": shas["satellite"],
            "degradation": request.get("degradation") or "",
            "reference_only": reference_only,
            "prompt_tokens": call.get("prompt_tokens", ""),
            "output_tokens": call.get("output_tokens", ""),
            "latency_s": round(elapsed, 3),
            "finish_reason": call.get("finish_reason", ""),
            "reason": call.get("reason", ""),
            "raw_response": body,
            "n_images": len(image_paths),
            "image_bytes": image_bytes,
            "image_roles": list(request.get("image_roles") or []),
            "image_paths": image_paths,
            "satellite_date": str(request.get("satellite_date") or "")[:10],
        }
        self.raw.commit(record)

    def _frozen_version(self) -> str | None:
        path = self.root / "plan" / "MODEL_VERSION.json"
        if self.budget.get("model_version"):
            return str(self.budget["model_version"])
        if path.is_file():
            payload = json.loads(path.read_text(encoding="utf-8"))
            if payload.get("admitted") and payload.get("model_version"):
                return str(payload["model_version"])
        return None

    def issue(self, request: Mapping[str, Any], *, reference_only: int = 0) -> str:
        if self.stop.is_set():
            return "stopped"
        key = cache_key(request)
        if remaining_attempts(self.raw.rows, key, limit=ATTEMPT_LIMIT) <= 0:
            return "exhausted"
        paths = [Path(path) for path in request.get("image_paths") or []]
        arm = str(request.get("arm") or "")
        stage = str(request.get("stage") or "")
        # T2 and T3 never call the model with a missing image or a one-image body.
        # Preflight and reference-check arms are not historical presence scores.
        if arm in {"t2", "t3"} and stage == "first_pass":
            expected = 3 if arm == "t3" else 2
            sizes = [path.stat().st_size if path.is_file() else 0 for path in paths]
            if len(paths) != expected or any(size <= 0 for size in sizes):
                self._record(
                    request,
                    {"call_status": "not_sent", "verdict": None, "reason": "declared_image_missing"},
                    elapsed=0.0,
                    body=None,
                    reference_only=reference_only,
                )
                return "not_sent"
        elif any(not path.is_file() for path in paths):
            self._record(
                request,
                {"call_status": "image_missing", "verdict": None, "reason": "png_missing"},
                elapsed=0.0,
                body=None,
                reference_only=reference_only,
            )
            return "image_missing"
        from scripts.temporal.quota_control import QuotaBudgetReached, QuotaPaused
        from scripts.validation.gemini_solar_image_review import RateLimiter, post_native_generate_content

        started = time.perf_counter()
        context = {
            "logical_call_id": "|".join(key),
            "anchor_id": request["anchor_id"],
            "run_id": self.run_id,
            "wave_id": request.get("grid_id", ""),
            "attempt_kind": request["stage"],
            "requested_alias": MODEL_ALIAS,
            "model_tier": "primary",
        }
        thinking = str(self.budget.get("thinking_level") or "") or None
        try:
            body = post_native_generate_content(
                base_url=self.settings["base_url"],
                native_path=self.settings["native_path"],
                api_key=self.settings["api_key"],
                model=MODEL_ALIAS,
                prompt=str(request["prompt"]),
                image_paths=paths,
                max_tokens=int(self.budget["max_output_tokens"]),
                timeout=240,
                response_mime_type="application/json",
                response_schema=RESPONSE_SCHEMA,
                thinking_level=thinking,
                limiter=request.get("_limiter"),
                quota_controller=self.breaker,
                quota_context=context,
            )
        except QuotaPaused as exc:
            self.pause_reason = str(exc)
            self.stop.set()
            return "paused"
        except QuotaBudgetReached as exc:
            self.budget_reason = str(exc)
            self.stop.set()
            return "budget"
        except Exception as exc:  # noqa: BLE001 - transport outcome is stored, not coerced
            elapsed = time.perf_counter() - started
            self._record(
                request,
                {"call_status": "transport_failed", "verdict": None, "reason": type(exc).__name__},
                elapsed=elapsed,
                body=None,
                reference_only=reference_only,
            )
            with self._count_lock:
                self.new_requests += 1
            return "transport_failed"
        elapsed = time.perf_counter() - started
        frozen = self._frozen_version()
        # The preflight itself is what discovers the Lite version string.
        if frozen is None and reference_only == 0 and request.get("stage") != "preflight":
            frozen = MODEL_ALIAS
        classified = _classify_body(body, frozen_version=frozen if request.get("stage") != "preflight" else str(body.get("modelVersion") or ""))
        if request.get("stage") == "preflight":
            version = str(body.get("modelVersion") or "")
            admitted = MODEL_ALIAS in version and "3.8" not in version and "pro" not in version.lower()
            classified["call_status"] = "ok" if admitted else "model_version_mismatch"
            classified["model_version"] = version
            classified["verdict"] = None
        if classified["call_status"] == "model_version_mismatch":
            self.mismatch = classified.get("model_version") or "missing"
            self.stop.set()
        self._record(request, classified, elapsed=elapsed, body=body, reference_only=reference_only)
        with self._count_lock:
            self.new_requests += 1
        return str(classified["call_status"])

    def run_many(self, requests: Sequence[Mapping[str, Any]], *, in_flight: int, reference_only: int = 0) -> None:
        from scripts.validation.gemini_solar_image_review import RateLimiter

        if not requests:
            return
        limiter = RateLimiter(QPS, max_in_flight=in_flight)
        prepared = []
        for request in requests:
            item = dict(request)
            item["_limiter"] = limiter
            prepared.append(item)
        workers = max(1, min(in_flight, len(prepared)))
        done = 0
        self.resumed_after_pause = False
        executor = ThreadPoolExecutor(max_workers=workers)
        futures = [executor.submit(self.issue, request, reference_only=reference_only) for request in prepared]
        try:
            for future in as_completed(futures):
                future.result()
                done += 1
                if done % 50 == 0:
                    _log(
                        f"[score] completed_batch={done}/{len(prepared)} "
                        f"new_requests={self.new_requests} stop={self.stop.is_set()}"
                    )
                if self.stop.is_set():
                    for item in futures:
                        item.cancel()
                    _log(
                        f"[score] stopping batch at {done}/{len(prepared)} "
                        f"new_requests={self.new_requests}"
                    )
                    break
        finally:
            executor.shutdown(wait=True, cancel_futures=True)
        if self.budget_reason:
            raise SystemExit(f"quota budget reached before every date was terminal: {self.budget_reason}")
        if self.mismatch:
            _write_json(
                self.root / "audit" / "model_mismatch.json",
                {"model_alias": MODEL_ALIAS, "returned_model_version": self.mismatch, "admitted": False},
            )
            raise SystemExit(f"foreign modelVersion {self.mismatch}; scoring stopped")
        if self.pause_reason:
            self._sleep_pause()
            self.pause_reason = ""
            self.stop.clear()
            self.refresh_breaker()
            # The calls that tripped the breaker are not raw rows. The caller
            # must retry them after this sleep instead of treating that as a stall.
            self.resumed_after_pause = True

    def _sleep_pause(self) -> None:
        from scripts.temporal.quota_control import parse_timestamp, utc_now

        path = self.root / "plan" / "PAUSED_QUOTA.json"
        wait = 30.0
        if path.is_file():
            payload = json.loads(path.read_text(encoding="utf-8"))
            resume = parse_timestamp(str(payload.get("resume_after") or ""))
            if resume is not None:
                wait = max(30.0, (resume - utc_now()).total_seconds() + 1.0)
        _log(f"[score] quota pause; sleeping {wait:.0f}s without deleting the ledger")
        time.sleep(wait)


def _pending(requests: Sequence[Mapping[str, Any]], raw: RawLog) -> list[dict[str, Any]]:
    return outstanding_requests(requests, raw.rows, limit=ATTEMPT_LIMIT)


def _historical_request(request: Mapping[str, Any]) -> dict[str, Any]:
    degradation = str(request.get("degradation") or "")
    roles = assemble_image_roles(
        arm=str(request["arm"]),
        candidate_sha256=str(request["candidate_sha256"]),
        aerial_sha256=str(request.get("aerial_sha256") or "") or None,
        satellite_sha256=str(request.get("satellite_sha256") or "") or None,
        degradation=degradation,
        candidate_date=str(request["capture_date"])[:10],
        satellite_date=str(request.get("satellite_date") or "") or None,
    )
    paths = {
        "candidate": request.get("candidate_png") or "",
        "aerial_reference": request.get("aerial_png") or "",
        "satellite_reference": request.get("satellite_png") or "",
    }
    item = dict(request)
    item["prompt"] = PROMPT
    item["image_paths"] = [paths[role["role"]] for role in roles]
    item["stage"] = str(request.get("stage") or "first_pass")
    return item


def _download(root: Path, plan: Mapping[str, Sequence[Mapping[str, str]]]) -> None:
    if (root / "imagery" / "RENDER_DONE.json").is_file():
        _log("[download] render sentinel present; leaving chips in place")
        return
    inputs = root / "imagery" / "a3_inputs"
    inputs.mkdir(parents=True, exist_ok=True)
    a3_rows = []
    sequential = []
    seen: set[tuple[str, str]] = set()

    def _add(row: Mapping[str, str], *, kind: str) -> None:
        provider = str(row.get("provider") or "")
        zoom = str(row.get("requested_zoom") or "")
        key = (str(row["anchor_id"]), str(row["capture_date"])[:10])
        if key in seen or kind == "aerial":
            return
        item = {
            "anchor_id": row["anchor_id"],
            "capture_date": str(row["capture_date"])[:10],
            "provider": provider,
            "requested_zoom": zoom,
            "version": row.get("version", ""),
        }
        if provider == "TM" and zoom == "19":
            if key not in seen:
                a3_rows.append(item)
                seen.add(key)
        else:
            sequential.append(item)
            seen.add(key)

    for row in plan["candidates"]:
        if str(row.get("callable") or "") != "1":
            continue
        _add(row, kind="candidate")
    for row in plan["references"]:
        if str(row.get("kind")) != "satellite":
            continue
        _add(row, kind="satellite")
    _write_csv(
        inputs / "candidates.csv",
        a3_rows,
        ["anchor_id", "capture_date", "provider", "requested_zoom", "version"],
    )
    _write_csv(
        inputs / "anchors.csv",
        plan["anchors"],
        list(plan["anchors"][0].keys()) if plan["anchors"] else ["anchor_id"],
    )
    a3_dir = root / "imagery" / "a3_tm"
    a3_dir.mkdir(parents=True, exist_ok=True)
    grids = ",".join(row["grid_id"] for row in plan["grids"])
    if a3_rows:
        command = [
            sys.executable,
            "-u",
            str(PROJECT_ROOT / "scripts/temporal/run_a3_batch_lane.py"),
            "--candidates-csv",
            str(inputs / "candidates.csv"),
            "--anchors-csv",
            str(inputs / "anchors.csv"),
            "--output-dir",
            str(a3_dir),
            "--provider",
            "TM",
            "--zoom",
            "19",
            "--grids",
            grids,
            "--request-interval",
            "1.0",
            "--parallel",
            "4",
        ]
        _log(f"[download] A3 tasks for {len(a3_rows)} TM z19 rows")
        with (a3_dir / "a3.log").open("a", encoding="utf-8") as handle:
            completed = subprocess.run(command, stdout=handle, stderr=subprocess.STDOUT, check=False)
        if completed.returncode != 0:
            raise SystemExit(f"A3 lane failed with exit {completed.returncode}; see {a3_dir / 'a3.log'}")
    _download_sequential(root, plan["anchors"], sequential)
    _log(f"[download] sequential rows {len(sequential)}")


def _chip_index(root: Path) -> dict[tuple[str, str], Path]:
    found: dict[tuple[str, str], Path] = {}
    manifest = root / "imagery" / "a3_tm" / "manifest" / "manifest.csv"
    if manifest.is_file():
        for row in _read_csv(manifest):
            if row.get("status") not in {"ok", "skipped_existing"}:
                continue
            path = Path(row.get("path") or "")
            if path.is_file() and path.stat().st_size > 0:
                found[(row["anchor_id"], str(row["capture_date"])[:10])] = path
    sidecar = root / "imagery" / "sequential_index.csv"
    if sidecar.is_file():
        for row in _read_csv(sidecar):
            path = Path(row.get("path") or "")
            if path.is_file() and path.stat().st_size > 0:
                found[(row["anchor_id"], str(row["capture_date"])[:10])] = path
    return found


def _download_sequential(root: Path, anchors: Sequence[Mapping[str, str]], rows: Sequence[Mapping[str, str]]) -> None:
    if not rows:
        _write_csv(root / "imagery" / "sequential_index.csv", [], ["anchor_id", "capture_date", "path", "status"])
        return
    from scripts.temporal.gehi_download import download_chip_with_zoom_ladder

    by_id = {row["anchor_id"]: row for row in anchors}
    output = root / "imagery" / "chips"
    written = []
    for index, row in enumerate(rows, start=1):
        result = download_chip_with_zoom_ladder(
            by_id[row["anchor_id"]],
            capture_date=row["capture_date"],
            version=row.get("version") or "noversion",
            zoom_ladder=(19, 18),
            output_root=output,
            provider=row.get("provider") or "TM",
        )
        path = str(result.path or "")
        written.append(
            {
                "anchor_id": row["anchor_id"],
                "capture_date": row["capture_date"],
                "path": path,
                "status": result.status,
            }
        )
        if index % 20 == 0:
            _log(f"[download] sequential {index}/{len(rows)}")
    _write_csv(root / "imagery" / "sequential_index.csv", written, ["anchor_id", "capture_date", "path", "status"])


def _render(root: Path, plan: Mapping[str, Sequence[Mapping[str, str]]]) -> None:
    sentinel = root / "imagery" / "RENDER_DONE.json"
    if sentinel.is_file():
        _log("[render] already complete")
        return
    from scripts.temporal.build_delivery_review_package import crop_aerial_from_tiles, index_aerial_tiles
    from scripts.temporal.run_adaptive_scan import make_fixed_extent_review_renderer
    from scripts.temporal.run_ct05_chip_pipeline import inspect_raster

    chips = _chip_index(root)
    _log(f"[render] chips indexed {len(chips)}")
    tiles = index_aerial_tiles([AERIAL_ROOT])
    _log(f"[render] aerial tiles {len(tiles)}")
    renderers: dict[float, Any] = {}
    progress_path = root / "imagery" / "render_progress.jsonl"
    done: set[tuple[str, str, str]] = set()
    if progress_path.exists():
        with progress_path.open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                row = json.loads(line)
                done.add((row["anchor_id"], row["kind"], str(row["capture_date"])[:10]))
    anchors = {row["anchor_id"]: row for row in plan["anchors"]}

    def _append(row: Mapping[str, Any]) -> None:
        with progress_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(dict(row), sort_keys=True) + "\n")
            handle.flush()

    def _renderer(anchor: Mapping[str, str]):
        extent = float(anchor.get("review_extent_m") or 24)
        if extent not in renderers:
            renderers[extent] = make_fixed_extent_review_renderer(extent)
        return renderers[extent]

    def _one(anchor: Mapping[str, str], tif: Path, kind: str, capture_date: str, rank: str) -> None:
        key = (anchor["anchor_id"], kind, capture_date[:10])
        if key in done:
            return
        record: dict[str, Any] = {
            "anchor_id": anchor["anchor_id"],
            "kind": kind,
            "rank": rank,
            "capture_date": capture_date[:10],
            "source_tif": str(tif) if tif else "",
            "png_path": "",
            "sha256": "",
            "qa_pass": "0",
            "failure": "",
        }
        if not tif or not tif.is_file():
            record["failure"] = "download_failed"
            _append(record)
            done.add(key)
            return
        try:
            inspected = inspect_raster(tif, anchor)
            qa_ok = (
                inspected.get("decode_status") == "pass"
                and inspected.get("bbox_status") == "pass"
                and inspected.get("pixel_quality_status") == "pass"
            )
            png = _renderer(anchor)(tif, anchor)
            record["png_path"] = str(png)
            record["sha256"] = _file_sha(png)
            record["qa_pass"] = "1" if qa_ok and png.is_file() else "0"
            if not qa_ok:
                record["failure"] = str(inspected.get("failure_class") or "quality_failed")
        except Exception as exc:  # noqa: BLE001 - one bad chip does not drop the grid
            record["failure"] = f"image_unreadable:{type(exc).__name__}"
        _append(record)
        done.add(key)

    for anchor in plan["anchors"]:
        census = str(anchor.get("census_date") or POLICY_CENSUS_DATE)[:10]
        aerial_tif = root / "imagery" / "aerial" / f"{anchor['anchor_id']}.tif"
        if (anchor["anchor_id"], "aerial", census) not in done:
            if not aerial_tif.is_file():
                try:
                    bbox = (
                        float(anchor["chip_lon_min"]),
                        float(anchor["chip_lat_min"]),
                        float(anchor["chip_lon_max"]),
                        float(anchor["chip_lat_max"]),
                    )
                    crop_aerial_from_tiles(tiles, bbox, aerial_tif)
                except Exception as exc:  # noqa: BLE001
                    _log(f"[render] aerial crop failed {anchor['anchor_id']}: {type(exc).__name__}")
            _one(anchor, aerial_tif if aerial_tif.is_file() else Path(), "aerial", census, "1")
    work = []
    for row in plan["candidates"]:
        if str(row.get("callable") or "") != "1":
            continue
        work.append((row["anchor_id"], row["capture_date"], "candidate", ""))
    for row in plan["references"]:
        if str(row.get("kind")) != "satellite":
            continue
        work.append((row["anchor_id"], row["capture_date"], "satellite", str(row.get("rank") or "")))
    for index, (anchor_id, capture_date, kind, rank) in enumerate(work, start=1):
        anchor = anchors[anchor_id]
        _one(anchor, chips.get((anchor_id, capture_date[:10]), Path()), kind, capture_date, rank)
        if index % 200 == 0:
            _log(f"[render] {index}/{len(work)}")
    rows = []
    with progress_path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    fields = ["anchor_id", "kind", "rank", "capture_date", "source_tif", "png_path", "sha256", "qa_pass", "failure"]
    _write_csv(root / "imagery" / "render_index.csv", rows, fields)
    _write_json(sentinel, {"n_rows": len(rows), "n_chips": len(chips)})
    _log(f"[render] wrote {len(rows)} rows")


def _load_render(root: Path) -> list[dict[str, str]]:
    path = root / "imagery" / "render_index.csv"
    if not path.is_file():
        raise SystemExit("render index missing")
    return _read_csv(path)


def _reference_requests(plan: Mapping[str, Sequence[Mapping[str, str]]], render_rows: Sequence[Mapping[str, str]], raw: RawLog, anchor_ids: set[str] | None) -> list[dict[str, Any]]:
    requests = []
    for row in render_rows:
        if row.get("kind") not in {"aerial", "satellite"}:
            continue
        if row.get("qa_pass") != "1" or not row.get("png_path"):
            continue
        if anchor_ids is not None and row["anchor_id"] not in anchor_ids:
            continue
        arm = "reference_aerial" if row["kind"] == "aerial" else "reference_satellite"
        request = {
            "arm": arm,
            "anchor_id": row["anchor_id"],
            "capture_date": row["capture_date"][:10],
            "stage": "reference_check",
            "degradation": "",
            "candidate_sha256": row["sha256"],
            "aerial_sha256": "",
            "satellite_sha256": "",
            "prompt": REFERENCE_CHECK_PROMPT,
            "image_paths": [row["png_path"]],
            "grid_id": "",
        }
        requests.append(request)
    return _pending(requests, raw)


def _manifest(root: Path, plan: Mapping[str, Sequence[Mapping[str, str]]], raw: RawLog) -> list[dict[str, Any]]:
    return assemble_manifest(
        plan["anchors"],
        plan["candidates"],
        plan["references"],
        _load_render(root),
        raw.rows,
    )


def _write_manifest(root: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    if not rows:
        return
    _write_csv(root / "imagery" / "manifest.csv", rows, list(rows[0].keys()))


def _preflight(root: Path, scorer: Scorer) -> str:
    path = root / "plan" / "MODEL_VERSION.json"
    if path.is_file():
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("admitted") and payload.get("model_version"):
            scorer.budget["model_version"] = payload["model_version"]
            _log(f"[preflight] reusing {payload['model_version']}")
            return str(payload["model_version"])
    from PIL import Image

    image_path = root / "audit" / "preflight_card.png"
    image_path.parent.mkdir(parents=True, exist_ok=True)
    if not image_path.is_file():
        Image.new("RGB", (64, 64), (20, 40, 60)).save(image_path)
    request = {
        "arm": "preflight",
        "anchor_id": "preflight",
        "capture_date": "2019-01-01",
        "stage": "preflight",
        "degradation": "candidate_only_reference_unavailable",
        "candidate_sha256": _file_sha(image_path),
        "aerial_sha256": "",
        "satellite_sha256": "",
        "prompt": (
            "Gateway identity preflight. This image is not a solar installation. "
            "Return one JSON object with candidate_index 1, pv_present null, "
            "quality_flag unusable, target_match null, evidence preflight, "
            "unknown_reason preflight."
        ),
        "image_paths": [str(image_path)],
        "grid_id": "",
    }
    # Preflight uses its own prompt, so assemble_image_roles is intentionally
    # not required to describe a triplet.
    status = scorer.issue(request, reference_only=0)
    version = ""
    for row in reversed(scorer.raw.rows):
        if row.get("stage") == "preflight":
            version = str(row.get("model_version") or "")
            break
    admitted = bool(version) and MODEL_ALIAS in version and "3.8" not in version and "pro" not in version.lower()
    _write_json(
        root / "audit" / "preflight.json",
        {
            "requested_alias": MODEL_ALIAS,
            "model_version": version,
            "call_status": status,
            "admitted": admitted,
        },
    )
    if not admitted:
        raise SystemExit(f"preflight modelVersion {version!r} is not {MODEL_ALIAS}")
    _write_json(path, {"model_alias": MODEL_ALIAS, "model_version": version, "admitted": True})
    scorer.budget["model_version"] = version
    _log(f"[preflight] admitted {version}")
    return version


def _latest_status(raw: RawLog, key: tuple[str, str, str, str]) -> str:
    status = "missing"
    for row in raw.rows:
        if cache_key(row) != key:
            continue
        if str(row.get("call_status")) == "ok":
            return "ok"
        status = str(row.get("call_status") or "call_failed")
    return status


def _drain(scorer: Scorer, requests: Sequence[Mapping[str, Any]], *, in_flight: int, reference_only: int = 0) -> None:
    pending = _pending(requests, scorer.raw)
    while pending:
        free = shutil.disk_usage(scorer.root).free
        if free < DISK_FLOOR_BYTES:
            _log(f"[score] free disk {free} bytes is below {DISK_FLOOR_BYTES}; stopping with the ledger intact")
            raise SystemExit("disk free space below 3 GiB; ledger left intact")
        before = scorer.new_requests
        chunk = pending[: max(SCORE_CHUNK, in_flight)]
        scorer.run_many(chunk, in_flight=in_flight, reference_only=reference_only)
        pending = _pending(requests, scorer.raw)
        paused = scorer.resumed_after_pause
        scorer.resumed_after_pause = False
        if scorer.new_requests == before and pending:
            if paused:
                _log(f"[score] pause cleared; retrying {len(pending)} requests")
                continue
            _log(f"[score] no progress on {len(pending)} requests")
            break


def _techcheck(root: Path, plan: Mapping[str, Sequence[Mapping[str, str]]], scorer: Scorer) -> None:
    frozen_path = root / "plan" / "PARAMS_FROZEN.json"
    if frozen_path.is_file():
        payload = json.loads(frozen_path.read_text(encoding="utf-8"))
        if int(payload.get("truncation_count", 1)) == 0:
            scorer.budget.update(payload)
            _log("[techcheck] parameters already frozen")
            return
    tech = select_techcheck(plan["anchors"], plan["candidates"])
    if not tech:
        raise SystemExit("technical check found no development dates")
    tech_ids = {row["anchor_id"] for row in tech}
    tech_keys = {(row["anchor_id"], row["capture_date"]) for row in tech}
    _log(f"[techcheck] anchors={len(tech_ids)} dates={len(tech_keys)}")
    _drain(scorer, _reference_requests(plan, _load_render(root), scorer.raw, tech_ids), in_flight=TECH_IN_FLIGHT, reference_only=1)
    scorer.budget["max_output_tokens"] = INITIAL_MAX_TOKENS
    manifest = _manifest(root, plan, scorer.raw)
    requests = [
        _historical_request(row)
        for row in scoring_requests(manifest, plan["anchors"], extra_paired_ids=tech_ids)
        if (row["anchor_id"], row["capture_date"]) in tech_keys
    ]
    _drain(scorer, requests, in_flight=TECH_IN_FLIGHT)
    truncated = [
        row
        for row in requests
        if _latest_status(scorer.raw, cache_key(row)) == "truncated"
    ]
    if truncated:
        _log(f"[techcheck] {len(truncated)} truncated at {INITIAL_MAX_TOKENS}; raising the cap once to 4096")
        scorer.budget["max_output_tokens"] = 4096
        _drain(scorer, requests, in_flight=TECH_IN_FLIGHT)
        truncated = [row for row in requests if _latest_status(scorer.raw, cache_key(row)) == "truncated"]
    ok_rows = []
    for row in scorer.raw.rows:
        if str(row.get("call_status")) != "ok" or str(row.get("stage")) != "first_pass":
            continue
        if (str(row.get("anchor_id")), str(row.get("capture_date"))[:10]) in tech_keys:
            ok_rows.append(row)
    if truncated or not ok_rows:
        raise SystemExit(
            f"refusing to freeze parameters; truncation={len(truncated)} technical_successes={len(ok_rows)}"
        )
    prompt_tokens = [float(row["prompt_tokens"]) for row in ok_rows if str(row.get("prompt_tokens") or "").strip()]
    output_tokens = [float(row["output_tokens"]) for row in ok_rows if str(row.get("output_tokens") or "").strip()]
    latency = [float(row["latency_s"]) for row in ok_rows if str(row.get("latency_s") or "").strip()]

    def _mean(values: list[float]) -> float:
        return round(sum(values) / len(values), 3) if values else 0.0

    params = {
        "model_alias": MODEL_ALIAS,
        "model_version": scorer.budget.get("model_version") or scorer._frozen_version(),
        "temperature": 0,
        "thinking_level": str(scorer.budget.get("thinking_level") or ""),
        "max_output_tokens": int(scorer.budget["max_output_tokens"]),
        "image_preprocessing": "raw_bytes_base64_no_transform",
        "truncation_count": 0,
        "n_tech_successes": len(ok_rows),
        "prompt_sha256": prompt_fingerprint(),
        "prompt_tuned_from_development_labels": False,
        "measured_prompt_tokens_mean": _mean(prompt_tokens),
        "measured_output_tokens_mean": _mean(output_tokens),
        "measured_latency_s_mean": _mean(latency),
    }
    _write_json(frozen_path, params)
    budget = dict(scorer.budget)
    budget.update(params)
    budget["status"] = "frozen"
    budget["frozen"] = True
    _write_json(root / "plan" / "budget.json", budget)
    scorer.budget = budget
    _log("[techcheck] froze parameters with truncation_count=0")


def _score(root: Path, plan: Mapping[str, Sequence[Mapping[str, str]]], scorer: Scorer) -> tuple[int, int]:
    frozen_path = root / "plan" / "PARAMS_FROZEN.json"
    if not frozen_path.is_file():
        raise SystemExit("refusing score stage until plan/PARAMS_FROZEN.json exists")
    params = json.loads(frozen_path.read_text(encoding="utf-8"))
    if int(params.get("truncation_count", 1)) != 0:
        raise SystemExit("refusing score stage because truncation_count is not zero")
    scorer.budget.update(params)
    anchor_ids = {row["anchor_id"] for row in plan["anchors"]}
    before = scorer.new_requests
    _drain(
        scorer,
        _reference_requests(plan, _load_render(root), scorer.raw, anchor_ids),
        in_flight=SCORE_IN_FLIGHT,
        reference_only=1,
    )
    manifest = _manifest(root, plan, scorer.raw)
    _write_manifest(root, manifest)
    requests = [_historical_request(row) for row in scoring_requests(manifest, plan["anchors"])]
    skipped = len(requests) - len(_pending(requests, scorer.raw))
    _drain(scorer, requests, in_flight=SCORE_IN_FLIGHT)
    manifest = _manifest(root, plan, scorer.raw)
    _write_manifest(root, manifest)
    pending_manifest = sum(1 for row in manifest if str(row.get("terminal_state") or "") in {"", "pending"})
    if pending_manifest == 0:
        _write_json(root / "observations" / "SCORE_DONE.json", {"pending_manifest": 0, "n_requests": len(requests)})
    return scorer.new_requests - before, skipped if before == 0 else len(requests) - len(_pending(requests, scorer.raw))


def _recheck(root: Path, plan: Mapping[str, Sequence[Mapping[str, str]]], scorer: Scorer) -> int:
    done_path = root / "observations" / "RECHECK_DONE.json"
    # A recheck sentinel written while historical dates were still pending is
    # not a finished recheck. Honor it only after the score sentinel exists.
    if done_path.is_file() and (root / "observations" / "SCORE_DONE.json").is_file():
        _log("[recheck] already complete")
        return 0
    manifest = _manifest(root, plan, scorer.raw)
    by_anchor_manifest: dict[str, list[Mapping[str, Any]]] = {}
    for row in manifest:
        by_anchor_manifest.setdefault(str(row["anchor_id"]), []).append(row)
    requests = []
    incomplete = 0
    for anchor in plan["anchors"]:
        anchor_id = anchor["anchor_id"]
        observations = timeline_observations(
            apply_manifest_gap_states(plan["candidates"], manifest),
            scorer.raw.rows,
            anchor_id=anchor_id,
            arm="t3",
        )
        if any(not int(row.get("terminal") or 0) for row in observations):
            incomplete += 1
            continue
        interval = infer_interval(
            observations,
            expected_dates=[row["capture_date"] for row in observations],
            aerial_pv="unknown",
            aerial_date=str(anchor.get("census_date") or POLICY_CENSUS_DATE),
            aerial_precision=str(anchor.get("census_date_precision") or POLICY_CENSUS_PRECISION),
        )
        dates = plan_rechecks(interval, observations)
        stage = "conflict_recheck" if interval.get("status") == "conflict_pending_review" else "boundary_recheck"
        manifest_by_date = {str(row["capture_date"])[:10]: row for row in by_anchor_manifest.get(anchor_id, [])}
        for day in dates:
            source = manifest_by_date.get(day)
            if source is None or not source.get("candidate_sha256"):
                continue
            request = _historical_request(
                {
                    **source,
                    "arm": "t3",
                    "stage": stage,
                    "degradation": source.get("t3_degradation") or "",
                }
            )
            requests.append(request)
    before = scorer.new_requests
    _drain(scorer, requests, in_flight=SCORE_IN_FLIGHT)
    if incomplete:
        _log(f"[recheck] {incomplete} anchors still have non-terminal dates; not writing RECHECK_DONE")
        return scorer.new_requests - before
    if not _pending(requests, scorer.raw):
        _write_json(done_path, {"n_recheck_requests": len(requests)})
    return scorer.new_requests - before


def _emit(new_requests: int, skipped: int, pending_manifest: int, recheck_new: int) -> None:
    sys.stdout.write(
        "\n".join(
            [
                "stage=score",
                f"new_requests={new_requests}",
                f"skipped_successes={skipped}",
                f"pending_manifest={pending_manifest}",
                f"recheck_new_requests={recheck_new}",
            ]
        )
        + "\n"
    )


def _load_v2_plan(plan_dir: Path) -> dict[str, list[dict[str, str]]]:
    required = ["grid_selection.csv", "anchor_selection.csv", "candidates.csv", "references.csv"]
    missing = [name for name in required if not (plan_dir / name).is_file()]
    if missing:
        raise SystemExit(f"frozen plan is incomplete: {missing}")
    return {
        "grids": _read_csv(plan_dir / "grid_selection.csv"),
        "anchors": _read_csv(plan_dir / "anchor_selection.csv"),
        "candidates": _read_csv(plan_dir / "candidates.csv"),
        "references": _read_csv(plan_dir / "references.csv"),
    }


def _v2_budget(root: Path, run_id: str) -> dict[str, Any]:
    """Write the measured 51,212 / 7,102 budget. A stale lite count is replaced."""

    path = root / "plan" / "budget.json"
    summary = {
        "N": 1114,
        "H": 51212,
        "H_pair": 7102,
        "n_paired_anchors": 149,
        "n_review_anchors": 85,
    }
    numbers = _budget_numbers(summary)
    payload: dict[str, Any] = {}
    if path.is_file():
        payload = json.loads(path.read_text(encoding="utf-8"))
    stale = int(payload.get("H") or 0) != summary["H"] or int(payload.get("H_pair") or 0) != summary["H_pair"]
    if payload.get("work_budget") and payload.get("gross_safe_budget") and not stale:
        payload["max_output_tokens"] = 4096
        payload["run_id"] = run_id
        return payload
    payload = {
        **summary,
        **numbers,
        "run_id": run_id,
        "status": "provisional_call_budget",
        "frozen": False,
        "model_alias": MODEL_ALIAS,
        "temperature": 0,
        "max_output_tokens": 4096,
        "thinking_level": "",
        "illustrative_example_used_as_count": 0,
        "image_preprocessing": "png_lossless_same_grid",
    }
    _write_json(path, payload)
    return payload


def _v2_open_dates(plan: Mapping[str, Sequence[Mapping[str, str]]], raw_rows: Sequence[Mapping[str, Any]]) -> int:
    from scripts.temporal.reference_fulltimeline import POLICY_CENSUS_DATE, WINDOW_START
    from scripts.temporal.score_v2_fulltimeline import t3_status_for_rows

    grouped: dict[tuple[str, str], list[Mapping[str, Any]]] = {}
    for row in raw_rows:
        if str(row.get("arm") or "") != "t3" or str(row.get("stage") or "") != "first_pass":
            continue
        key = (str(row.get("anchor_id") or ""), str(row.get("capture_date") or "")[:10])
        grouped.setdefault(key, []).append(row)
    open_count = 0
    for candidate in plan["candidates"]:
        day = str(candidate["capture_date"])[:10]
        if str(candidate.get("callable") or "") not in {"1", "true", "True"}:
            continue
        if not (WINDOW_START <= day < POLICY_CENSUS_DATE):
            continue
        if not t3_status_for_rows(grouped.get((candidate["anchor_id"], day), [])):
            open_count += 1
    return open_count


def _check_v2_smoke(raw_rows: Sequence[Mapping[str, Any]], keys: set[tuple[str, str]]) -> None:
    """Stop the full window unless the logged smoke bodies have the right images."""

    found: dict[tuple[str, str, str], Mapping[str, Any]] = {}
    for row in raw_rows:
        if str(row.get("stage") or "") != "first_pass" or str(row.get("call_status") or "") != "ok":
            continue
        key = (str(row.get("arm") or ""), str(row.get("anchor_id") or ""), str(row.get("capture_date") or "")[:10])
        if (key[1], key[2]) in keys:
            found[key] = row
    problems: list[str] = []
    for anchor_id, day in sorted(keys):
        t3 = found.get(("t3", anchor_id, day))
        t2 = found.get(("t2", anchor_id, day))
        if t3 is None or int(t3.get("n_images") or 0) != 3:
            problems.append(f"t3 {anchor_id[-8:]} {day} images={None if t3 is None else t3.get('n_images')}")
        elif list(t3.get("image_roles") or []) != ["candidate", "aerial_reference", "satellite_reference"]:
            problems.append(f"t3 roles {t3.get('image_roles')}")
        elif any(int(size) <= 0 for size in t3.get("image_bytes") or []):
            problems.append(f"t3 empty image {anchor_id[-8:]} {day}")
        elif str(t3.get("model_version") or "") != MODEL_ALIAS:
            problems.append(f"t3 model {t3.get('model_version')}")
        if t2 is None or int(t2.get("n_images") or 0) != 2:
            problems.append(f"t2 {anchor_id[-8:]} {day} images={None if t2 is None else t2.get('n_images')}")
        elif list(t2.get("image_roles") or []) != ["candidate", "aerial_reference"]:
            problems.append(f"t2 roles {t2.get('image_roles')}")
        elif str(t2.get("satellite_sha256") or ""):
            problems.append(f"t2 carried a satellite hash {anchor_id[-8:]} {day}")
        elif str(t2.get("model_version") or "") != MODEL_ALIAS:
            problems.append(f"t2 model {t2.get('model_version')}")
        elif t3 is not None and (
            t3.get("candidate_sha256") != t2.get("candidate_sha256")
            or t3.get("aerial_sha256") != t2.get("aerial_sha256")
        ):
            problems.append(f"t2/t3 bytes differ {anchor_id[-8:]} {day}")
    if problems:
        raise SystemExit("smoke refused the full window: " + "; ".join(problems))


def _require_v2_audit(text: str) -> None:
    fields = {}
    for line in text.splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        fields[key] = value
    problems: list[str] = []
    if fields.get("terminal_t3_count") != "51212":
        problems.append(f"terminal_t3_count={fields.get('terminal_t3_count')}")
    if fields.get("one_image_successful_t3_count") != "0":
        problems.append(f"one_image_successful_t3_count={fields.get('one_image_successful_t3_count')}")
    if fields.get("sampled_t3_n_images") != "3" or fields.get("sampled_t3_bytes_nonempty") != "1":
        problems.append("sampled T3 body is not three non-empty images")
    if fields.get("sampled_t3_image_roles") != "candidate,aerial_reference,satellite_reference":
        problems.append(f"sampled roles={fields.get('sampled_t3_image_roles')}")
    if int(fields.get("paired_effect_denominator") or "0") <= 0:
        problems.append("paired effect denominator is 0")
    if fields.get("paired_rows") != "7102":
        problems.append(f"paired_rows={fields.get('paired_rows')}")
    if fields.get("candidate_byte_agreement") != "1" or fields.get("aerial_byte_agreement") != "1":
        problems.append("paired byte agreement is not 1")
    if fields.get("model_identity") != MODEL_ALIAS:
        problems.append(f"model_identity={fields.get('model_identity')}")
    for key, value in fields.items():
        if key.startswith("white_") and value != "pixel_uninformative":
            problems.append(f"{key}={value}")
    if fields.get("incomplete_coverage_coded_absent") != "0":
        problems.append("incomplete coverage was coded absent")
    if fields.get("incomplete_coverage_unscored") != "4555":
        problems.append(f"incomplete_coverage_unscored={fields.get('incomplete_coverage_unscored')}")
    if fields.get("post_census_reference_violations") != "0":
        problems.append("a pre-census frame was used as the T3 reference")
    if fields.get("labels_modified_rows") != "0":
        problems.append("labels were modified")
    role_summary = fields.get("role_summary") or ""
    if "development_n=" not in role_summary or "evaluation_n=" not in role_summary:
        problems.append("development and evaluation interval counts are not separate")
    if problems:
        raise SystemExit("v2 audit refused SCORE_DONE: " + "; ".join(problems))


def _run_v2(root: Path, plan_dir: Path, *, smoke: bool) -> None:
    """Score the v2 pack. Smoke does not write the completion sentinel."""

    from scripts.temporal.reference_fulltimeline import PIXEL_UNINFORMATIVE_DATES
    from scripts.temporal.score_v2_fulltimeline import (
        EXPECTED_CALLABLE,
        V2_RUN_ID,
        audit_text,
        build_job,
        smoke_selection,
        write_products,
    )

    if root.resolve() == DEFAULT_OUTPUT.resolve():
        raise SystemExit("v2 refuses the voided lite root")
    lock = _hold_lock(root / "plan" / "runner.lock")
    try:
        plan = _load_v2_plan(plan_dir)
        budget = _v2_budget(root, V2_RUN_ID)
        raw = RawLog(root / "observations" / "raw.jsonl")
        scorer = Scorer(root, raw, budget, run_id=V2_RUN_ID, window_id=V2_RUN_ID)
        _preflight(root, scorer)
        only = smoke_selection(plan["anchors"], plan["candidates"]) if smoke else None
        _log(f"[v2] building requests smoke={int(smoke)}")
        noncalls, requests = build_job(root, plan, only=only)
        if smoke:
            if noncalls:
                raise SystemExit(
                    "smoke dates are not sendable: "
                    + ", ".join(f"{row['anchor_id'][-8:]}:{row['call_status']}" for row in noncalls)
                )
        else:
            unexpected = [
                f"{row['anchor_id'][-8:]}:{row['capture_date']}:{row['call_status']}"
                for row in noncalls
                if row["call_status"] != "pixel_uninformative"
                or (row["anchor_id"], row["capture_date"]) not in PIXEL_UNINFORMATIVE_DATES
            ]
            if unexpected or len(noncalls) != len(PIXEL_UNINFORMATIVE_DATES):
                raise SystemExit(
                    f"refusing to score; non-calls={len(noncalls)} unexpected={unexpected[:8]}"
                )
        for row in noncalls:
            scorer.raw.commit(row)
        pending = _pending(requests, scorer.raw)
        _log(f"[v2] requests={len(requests)} pending={len(pending)} noncalls={len(noncalls)}")
        _drain(scorer, requests, in_flight=SCORE_IN_FLIGHT)
        if smoke:
            _check_v2_smoke(scorer.raw.rows, only or set())
            _log("[v2] smoke passed; SCORE_DONE was not written")
            sys.stdout.write("stage=v2_smoke\nsmoke_ok=1\nscore_done=0\n")
            return
        still_open = _pending(requests, scorer.raw)
        open_dates = _v2_open_dates(plan, scorer.raw.rows)
        if still_open or open_dates:
            raise SystemExit(
                f"scoring stopped with pending_requests={len(still_open)} pending_callable_t3={open_dates}"
            )
        _log("[v2] writing status, paired comparison, and intervals")
        write_products(root, plan, scorer.raw.rows)
        text = audit_text(root, plan, scorer.raw.rows)
        _require_v2_audit(text)
        _write_json(
            root / "observations" / "SCORE_DONE.json",
            {
                "pending_manifest": 0,
                "terminal_t3_count": EXPECTED_CALLABLE,
                "n_requests": len(requests),
            },
        )
        sys.stdout.write(text)
    finally:
        lock.close()


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, default=None)
    parser.add_argument(
        "--stage",
        choices=["download", "render", "preflight", "techcheck", "score", "recheck", "all", "resume", "v2"],
        default="all",
    )
    parser.add_argument("--plan-dir", type=Path, default=None)
    parser.add_argument("--smoke", action="store_true")
    args = parser.parse_args(argv)
    if args.stage == "v2":
        from scripts.temporal.score_v2_fulltimeline import LITE_PLAN, V2_PACK

        root = V2_PACK if args.output_root is None else args.output_root
        if root.resolve() == DEFAULT_OUTPUT.resolve():
            raise SystemExit("v2 refuses the voided lite root")
        plan_dir = LITE_PLAN if args.plan_dir is None else args.plan_dir
        _run_v2(root, plan_dir, smoke=args.smoke)
        return
    root = DEFAULT_OUTPUT if args.output_root is None else args.output_root
    lock = _hold_lock(root / "plan" / "runner.lock")
    try:
        plan = _load_plan(root)
        summary = _summary_counts(root)
        stage = "all" if args.stage == "resume" else args.stage
        if stage in {"download", "all"}:
            _download(root, plan)
        if stage in {"render", "all"}:
            _render(root, plan)
        if stage == "download":
            return
        if stage == "render":
            return
        budget = _ensure_call_budget(root, summary)
        raw = RawLog(root / "observations" / "raw.jsonl")
        scorer = Scorer(root, raw, budget)
        if stage in {"preflight", "techcheck", "score", "recheck", "all"}:
            _preflight(root, scorer)
        if stage in {"techcheck", "score", "recheck", "all"}:
            _techcheck(root, plan, scorer)
        new_requests = 0
        skipped = 0
        recheck_new = 0
        if stage in {"score", "all"}:
            new_requests, skipped = _score(root, plan, scorer)
        manifest = _manifest(root, plan, scorer.raw) if (root / "imagery" / "render_index.csv").is_file() else []
        pending_manifest = sum(1 for row in manifest if str(row.get("terminal_state") or "") in {"", "pending"})
        if stage == "recheck" or (stage == "all" and pending_manifest == 0):
            recheck_new = _recheck(root, plan, scorer)
        elif stage == "all" and pending_manifest:
            _log("[recheck] skipped; historical dates are still pending")
        if stage in {"score", "recheck", "all"}:
            _emit(new_requests, skipped, pending_manifest, recheck_new)
        if stage in {"score", "all"} and pending_manifest:
            raise SystemExit(f"scoring stopped with pending_manifest={pending_manifest}")
    finally:
        lock.close()


if __name__ == "__main__":
    main()
