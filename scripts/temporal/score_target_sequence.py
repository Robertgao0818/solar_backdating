#!/usr/bin/env python3
"""Score one target across a fixed GEHI date window with Gemini sequence review.

Preferred batch unit:

- one target
- five ordered review PNGs
- one Gemini request

The scorer consumes target-centered review PNGs. For convenience, it can also
materialize those PNGs from `chip_targets.csv` plus a GEHI image artifact
manifest before scoring.
"""

# ruff: noqa: E402

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.temporal.gehi_common import ensure_single_target_review_png
from scripts.temporal.geid_temporal_common import parse_iso_date, read_csv_rows, write_csv_rows
from scripts.temporal.score_anchor_presence import PRESENCE_FIELDS
from scripts.temporal.score_chip_group_matrix import (
    ChipArtifact,
    ChipTarget,
    load_artifacts_by_chip,
    load_chip_targets_by_chip,
)
from scripts.validation.gemini_solar_image_review import (
    API_FORMATS,
    GeminiClientConfig,
    GeminiSequenceObservation,
    GeminiSequenceResult,
    SequenceDatePick,
    env_value,
    load_env_file,
    score_single_target_sequence,
)

DEFAULT_REVIEW_PNG_MANIFEST = (
    Path.home() / "zasolar_data/geid_temporal/target_sequence_review_pngs.csv"
)
DEFAULT_OUTPUT = Path.home() / "zasolar_data/geid_temporal/target_sequence_presence.csv"
DEFAULT_LONG_OUTPUT = Path.home() / "zasolar_data/geid_temporal/target_sequence_presence_long.csv"
DEFAULT_AUDIT_DIR = Path.home() / "zasolar_data/geid_temporal/target_sequence_gemini_audit"

REVIEW_PNG_FIELDS = [
    "anchor_id",
    "region_key",
    "grid_id",
    "chip_id",
    "target_label",
    "date_index",
    "capture_date",
    "review_png_path",
    "source_chip_path",
    "actual_zoom",
    "render_status",
    "render_notes",
]

SEQUENCE_TARGET_FIELDS = [
    "anchor_id",
    "region_key",
    "grid_id",
    "chip_id",
    "target_label",
    "date_window",
    "sequence_pattern",
    "first_present_date",
    "first_present_date_index",
    "confidence",
    "consistency_flag",
    "decision_source",
    "quality_flag",
    "review_notes",
    "n_dates",
    "n_missing_review_png",
    "audit_path",
]

SEQUENCE_LONG_FIELDS = [
    *PRESENCE_FIELDS,
    "chip_id",
    "target_label",
    "date_index",
    "review_png_path",
    "source_chip_path",
    "actual_zoom",
    "sequence_pattern",
    "sequence_confidence",
    "sequence_consistency_flag",
    "gemini_evidence",
    "gemini_notes",
]


@dataclass(frozen=True)
class TargetKey:
    chip_id: str
    anchor_id: str
    target_label: str


@dataclass(frozen=True)
class ReviewPng:
    anchor_id: str
    region_key: str
    grid_id: str
    chip_id: str
    target_label: str
    date_index: int
    capture_date: str
    review_png_path: Path | None
    source_chip_path: Path | None
    actual_zoom: int | None
    render_status: str
    render_notes: str

    @property
    def key(self) -> TargetKey:
        return TargetKey(self.chip_id, self.anchor_id, self.target_label)


class RateLimiter:
    def __init__(self, qps: float | None) -> None:
        self.interval = 0.0 if not qps or qps <= 0 else 1.0 / float(qps)
        self._next_at = 0.0
        self._lock = threading.Lock()

    def wait(self) -> None:
        if self.interval <= 0:
            return
        with self._lock:
            now = time.monotonic()
            if now < self._next_at:
                time.sleep(self._next_at - now)
                now = time.monotonic()
            self._next_at = now + self.interval


def _safe_token(value: object) -> str:
    text = str(value or "").strip()
    text = re.sub(r"[^A-Za-z0-9_.-]+", "_", text)
    return text.strip("_") or "unknown"


def _bool_to_csv(value: bool | None) -> str:
    if value is True:
        return "1"
    if value is False:
        return "0"
    return ""


def _float_to_csv(value: float | None) -> str:
    return "" if value is None else f"{value:.4f}"


def parse_dates_arg(value: str) -> list[str]:
    dates = [part.strip()[:10] for part in value.split(",") if part.strip()]
    if not dates:
        raise ValueError("--dates must contain at least one YYYY-MM-DD date")
    out: list[str] = []
    for item in dates:
        parsed = parse_iso_date(item)
        if parsed is None:
            raise ValueError(f"invalid date in --dates: {item!r}")
        out.append(parsed.isoformat())
    if len(set(out)) != len(out):
        raise ValueError(f"--dates contains duplicates: {value}")
    return out


def _int_text(value: object) -> int | None:
    text = str(value or "").strip()
    if not text:
        return None
    return int(float(text))


def _review_png_from_row(row: Mapping[str, object]) -> ReviewPng | None:
    anchor_id = str(row.get("anchor_id", "")).strip()
    chip_id = str(row.get("chip_id", "")).strip()
    target_label = str(row.get("target_label", "")).strip()
    capture_date = str(row.get("capture_date", "")).strip()[:10]
    if not anchor_id or not chip_id or not target_label or not capture_date:
        return None
    review_path_text = str(row.get("review_png_path") or row.get("image_path") or "").strip()
    source_path_text = str(row.get("source_chip_path") or row.get("chip_path") or "").strip()
    status = str(row.get("render_status") or row.get("status") or "ok").strip() or "ok"
    date_index = _int_text(row.get("date_index")) or 0
    return ReviewPng(
        anchor_id=anchor_id,
        region_key=str(row.get("region_key", "")).strip(),
        grid_id=str(row.get("grid_id", "")).strip(),
        chip_id=chip_id,
        target_label=target_label,
        date_index=date_index,
        capture_date=capture_date,
        review_png_path=Path(review_path_text) if review_path_text else None,
        source_chip_path=Path(source_path_text) if source_path_text else None,
        actual_zoom=_int_text(row.get("actual_zoom")),
        render_status=status,
        render_notes=str(row.get("render_notes") or row.get("notes") or "").strip(),
    )


def load_review_png_manifest(path: Path) -> list[ReviewPng]:
    out: list[ReviewPng] = []
    for row in read_csv_rows(path):
        item = _review_png_from_row(row)
        if item is not None:
            out.append(item)
    return out


def _artifact_by_date(artifacts: Sequence[ChipArtifact]) -> dict[str, ChipArtifact]:
    out: dict[str, ChipArtifact] = {}
    for artifact in sorted(artifacts, key=lambda a: (a.capture_date, a.version, str(a.path))):
        out.setdefault(artifact.capture_date, artifact)
    return out


def render_review_png_manifest(
    *,
    targets_by_chip: Mapping[str, Sequence[ChipTarget]],
    artifacts_by_chip: Mapping[str, Sequence[ChipArtifact]],
    dates: Sequence[str],
    crop_context_multiplier: float = 3.0,
    min_crop_size_m: float = 24.0,
    min_output_px: int = 128,
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for chip_id in sorted(targets_by_chip):
        artifacts_by_date = _artifact_by_date(artifacts_by_chip.get(chip_id, ()))
        for target in sorted(
            targets_by_chip[chip_id],
            key=lambda t: (t.target_index, t.target_label, t.target_id),
        ):
            for date_index, capture_date in enumerate(dates, start=1):
                artifact = artifacts_by_date.get(capture_date)
                if artifact is None:
                    rows.append(
                        {
                            "anchor_id": target.target_id,
                            "region_key": target.region_key,
                            "grid_id": target.grid_id,
                            "chip_id": chip_id,
                            "target_label": target.target_label,
                            "date_index": date_index,
                            "capture_date": capture_date,
                            "review_png_path": "",
                            "source_chip_path": "",
                            "actual_zoom": "",
                            "render_status": "failed",
                            "render_notes": "missing_source_artifact_for_date",
                        }
                    )
                    continue
                if not artifact.path.exists():
                    rows.append(
                        {
                            "anchor_id": target.target_id,
                            "region_key": target.region_key,
                            "grid_id": target.grid_id,
                            "chip_id": chip_id,
                            "target_label": target.target_label,
                            "date_index": date_index,
                            "capture_date": capture_date,
                            "review_png_path": "",
                            "source_chip_path": str(artifact.path),
                            "actual_zoom": artifact.actual_zoom or "",
                            "render_status": "failed",
                            "render_notes": "source_chip_missing",
                        }
                    )
                    continue
                try:
                    review_png = ensure_single_target_review_png(
                        artifact.path,
                        target.review_marker,
                        chip_size_m=target.chip_size_m,
                        crop_context_multiplier=crop_context_multiplier,
                        min_crop_size_m=min_crop_size_m,
                        min_output_px=min_output_px,
                    )
                    rows.append(
                        {
                            "anchor_id": target.target_id,
                            "region_key": target.region_key,
                            "grid_id": target.grid_id,
                            "chip_id": chip_id,
                            "target_label": target.target_label,
                            "date_index": date_index,
                            "capture_date": capture_date,
                            "review_png_path": str(review_png),
                            "source_chip_path": str(artifact.path),
                            "actual_zoom": artifact.actual_zoom or "",
                            "render_status": "ok",
                            "render_notes": "",
                        }
                    )
                except Exception as exc:  # noqa: BLE001 - keep target/date rows auditable.
                    rows.append(
                        {
                            "anchor_id": target.target_id,
                            "region_key": target.region_key,
                            "grid_id": target.grid_id,
                            "target_label": target.target_label,
                            "chip_id": chip_id,
                            "date_index": date_index,
                            "capture_date": capture_date,
                            "review_png_path": "",
                            "source_chip_path": str(artifact.path),
                            "actual_zoom": artifact.actual_zoom or "",
                            "render_status": "failed",
                            "render_notes": f"{type(exc).__name__}: {exc}",
                        }
                    )
    return rows


def _group_review_pngs(items: Sequence[ReviewPng]) -> dict[TargetKey, list[ReviewPng]]:
    grouped: dict[TargetKey, list[ReviewPng]] = defaultdict(list)
    for item in items:
        grouped[item.key].append(item)
    return {
        key: sorted(values, key=lambda v: (v.capture_date, v.date_index, str(v.review_png_path or "")))
        for key, values in grouped.items()
    }


def _select_window_rows(
    rows: Sequence[ReviewPng],
    *,
    dates: Sequence[str],
) -> tuple[list[ReviewPng | None], list[str]]:
    by_date: dict[str, ReviewPng] = {}
    for row in rows:
        if row.capture_date not in dates:
            continue
        existing = by_date.get(row.capture_date)
        row_usable = (
            row.render_status in {"ok", "skipped_existing"}
            and row.review_png_path is not None
            and row.review_png_path.exists()
        )
        existing_usable = (
            existing is not None
            and existing.render_status in {"ok", "skipped_existing"}
            and existing.review_png_path is not None
            and existing.review_png_path.exists()
        )
        if existing is None or (row_usable and not existing_usable):
            by_date[row.capture_date] = row
    selected: list[ReviewPng | None] = []
    missing: list[str] = []
    for date in dates:
        item = by_date.get(date)
        if (
            item is None
            or item.render_status not in {"ok", "skipped_existing"}
            or item.review_png_path is None
            or not item.review_png_path.exists()
        ):
            missing.append(date)
        selected.append(item)
    return selected, missing


def _target_metadata(key: TargetKey, rows: Sequence[ReviewPng | None]) -> dict[str, str]:
    present = next((row for row in rows if row is not None), None)
    return {
        "anchor_id": key.anchor_id,
        "region_key": "" if present is None else present.region_key,
        "grid_id": "" if present is None else present.grid_id,
        "chip_id": key.chip_id,
        "target_label": key.target_label,
    }


def _audit_writer_for(audit_dir: Path | None, key: TargetKey) -> tuple[Callable[[dict[str, Any]], None] | None, Path | None]:
    if audit_dir is None:
        return None, None
    path = (
        audit_dir
        / _safe_token(key.chip_id)
        / f"{_safe_token(key.anchor_id)}_{_safe_token(key.target_label)}_sequence.jsonl"
    )
    path.parent.mkdir(parents=True, exist_ok=True)

    def _write(payload: dict[str, Any]) -> None:
        record = dict(payload)
        record["chip_id"] = key.chip_id
        record["anchor_id"] = key.anchor_id
        record["target_label"] = key.target_label
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    return _write, path


def _target_row(
    *,
    key: TargetKey,
    selected: Sequence[ReviewPng | None],
    dates: Sequence[str],
    result: GeminiSequenceResult,
    missing_count: int,
    audit_path: Path | None,
) -> dict[str, object]:
    meta = _target_metadata(key, selected)
    return {
        **meta,
        "date_window": ",".join(dates),
        "sequence_pattern": result.sequence_pattern,
        "first_present_date": result.first_present_date or "",
        "first_present_date_index": result.first_present_date_index or "",
        "confidence": _float_to_csv(result.confidence),
        "consistency_flag": result.consistency_flag,
        "decision_source": result.decision_source,
        "quality_flag": result.quality_flag,
        "review_notes": result.review_notes,
        "n_dates": len(dates),
        "n_missing_review_png": missing_count,
        "audit_path": "" if audit_path is None else str(audit_path),
    }


def _pending_result(dates: Sequence[str], *, quality_flag: str, notes: str) -> GeminiSequenceResult:
    return GeminiSequenceResult(
        sequence_pattern="-".join("?" for _ in dates),
        first_present_date=None,
        first_present_date_index=None,
        confidence=None,
        consistency_flag=quality_flag,
        quality_flag=quality_flag,
        review_notes=notes,
        observations=[
            result_observation(index, capture_date, notes=notes)
            for index, capture_date in enumerate(dates, start=1)
        ],
        decision_source="sequence_pending",
    )


def result_observation(index: int, capture_date: str, *, notes: str) -> GeminiSequenceObservation:
    return GeminiSequenceObservation(
        date_index=index,
        capture_date=capture_date,
        pv_present=None,
        pv_score=None,
        evidence="",
        notes=notes,
    )


def _long_rows(
    *,
    key: TargetKey,
    selected: Sequence[ReviewPng | None],
    dates: Sequence[str],
    result: GeminiSequenceResult,
) -> list[dict[str, object]]:
    meta = _target_metadata(key, selected)
    by_date = {row.capture_date: row for row in selected if row is not None}
    obs_by_date = {obs.capture_date: obs for obs in result.observations}
    rows: list[dict[str, object]] = []
    for date_index, capture_date in enumerate(dates, start=1):
        review = by_date.get(capture_date)
        obs = obs_by_date.get(capture_date) or result_observation(
            date_index,
            capture_date,
            notes="missing_sequence_observation",
        )
        review_png_path = "" if review is None or review.review_png_path is None else str(review.review_png_path)
        source_chip_path = "" if review is None or review.source_chip_path is None else str(review.source_chip_path)
        source_parent = "" if not source_chip_path else str(Path(source_chip_path).parent)
        rows.append(
            {
                **meta,
                "requested_date": capture_date,
                "capture_date": capture_date,
                "actual_capture_dates": capture_date,
                "pv_score": _float_to_csv(obs.pv_score),
                "pv_present": _bool_to_csv(obs.pv_present),
                "decision_source": result.decision_source,
                "quality_flag": result.quality_flag,
                "chip_dir": source_parent,
                "sample_chip_path": review_png_path,
                "n_jpg": 1 if review_png_path else 0,
                "task_name": key.chip_id,
                "save_to": source_parent,
                "notes": result.review_notes,
                "date_index": date_index,
                "review_png_path": review_png_path,
                "source_chip_path": source_chip_path,
                "actual_zoom": "" if review is None or review.actual_zoom is None else review.actual_zoom,
                "sequence_pattern": result.sequence_pattern,
                "sequence_confidence": _float_to_csv(result.confidence),
                "sequence_consistency_flag": result.consistency_flag,
                "gemini_evidence": obs.evidence,
                "gemini_notes": obs.notes,
            }
        )
    return rows


def _resume_rows(
    *,
    target_output: Path,
    long_output: Path,
    dates: Sequence[str],
) -> tuple[dict[tuple[str, str, str, str], dict[str, str]], dict[tuple[str, str, str, str], list[dict[str, str]]]]:
    if not target_output.exists() or not long_output.exists():
        return {}, {}
    date_window = ",".join(dates)
    target_rows = {
        (
            str(row.get("chip_id", "")),
            str(row.get("anchor_id", "")),
            str(row.get("target_label", "")),
            str(row.get("date_window", "")),
        ): row
        for row in read_csv_rows(target_output)
        if str(row.get("date_window", "")) == date_window
    }
    long_rows: dict[tuple[str, str, str, str], list[dict[str, str]]] = defaultdict(list)
    for row in read_csv_rows(long_output):
        key = (
            str(row.get("chip_id", "")),
            str(row.get("anchor_id", "")),
            str(row.get("target_label", "")),
            date_window,
        )
        if key in target_rows:
            long_rows[key].append(row)
    complete_long = {
        key: sorted(rows, key=lambda r: str(r.get("capture_date", "")))
        for key, rows in long_rows.items()
        if len(rows) >= len(dates)
    }
    target_rows = {key: row for key, row in target_rows.items() if key in complete_long}
    return target_rows, complete_long


def score_target_sequences(
    *,
    review_pngs: Sequence[ReviewPng],
    dates: Sequence[str],
    config: GeminiClientConfig,
    audit_dir: Path | None = None,
    scorer: Callable[..., GeminiSequenceResult] = score_single_target_sequence,
    max_tokens: int | None = None,
    workers: int = 1,
    qps: float | None = None,
    limit_targets: int | None = None,
    resume_target_rows: Mapping[tuple[str, str, str, str], Mapping[str, object]] | None = None,
    resume_long_rows: Mapping[tuple[str, str, str, str], Sequence[Mapping[str, object]]] | None = None,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    if workers <= 0:
        raise ValueError("workers must be positive")
    grouped = _group_review_pngs(review_pngs)
    jobs = sorted(grouped.items(), key=lambda item: (item[0].chip_id, item[0].anchor_id, item[0].target_label))
    if limit_targets is not None:
        jobs = jobs[:limit_targets]

    date_window = ",".join(dates)
    prior_targets = resume_target_rows or {}
    prior_longs = resume_long_rows or {}
    limiter = RateLimiter(qps)

    target_rows: list[dict[str, object]] = []
    long_rows: list[dict[str, object]] = []
    pending_jobs: list[tuple[TargetKey, list[ReviewPng]]] = []
    for key, rows in jobs:
        resume_key = (key.chip_id, key.anchor_id, key.target_label, date_window)
        if resume_key in prior_targets and resume_key in prior_longs:
            target_rows.append(dict(prior_targets[resume_key]))
            long_rows.extend(dict(row) for row in prior_longs[resume_key])
        else:
            pending_jobs.append((key, rows))

    def run_one(key: TargetKey, rows: list[ReviewPng]) -> tuple[dict[str, object], list[dict[str, object]]]:
        selected, missing = _select_window_rows(rows, dates=dates)
        audit_writer, audit_path = _audit_writer_for(audit_dir, key)
        if missing:
            result = _pending_result(
                dates,
                quality_flag="sequence_pending_missing_review_png",
                notes=f"missing review PNG for dates: {','.join(missing)}",
            )
            return (
                _target_row(
                    key=key,
                    selected=selected,
                    dates=dates,
                    result=result,
                    missing_count=len(missing),
                    audit_path=audit_path,
                ),
                _long_rows(key=key, selected=selected, dates=dates, result=result),
            )
        valid_rows = [row for row in selected if row is not None]
        picks = [
            SequenceDatePick(
                date_index=index,
                chip_path=row.review_png_path or Path(""),
                capture_date=date,
                actual_zoom=row.actual_zoom,
            )
            for index, (date, row) in enumerate(zip(dates, valid_rows, strict=True), start=1)
        ]
        limiter.wait()
        result = scorer(
            picks,
            config=config,
            audit_writer=audit_writer,
            max_tokens=max_tokens,
        )
        return (
            _target_row(
                key=key,
                selected=selected,
                dates=dates,
                result=result,
                missing_count=0,
                audit_path=audit_path,
            ),
            _long_rows(key=key, selected=selected, dates=dates, result=result),
        )

    if workers == 1:
        for key, rows in pending_jobs:
            target_row, target_long_rows = run_one(key, rows)
            target_rows.append(target_row)
            long_rows.extend(target_long_rows)
    else:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(run_one, key, rows) for key, rows in pending_jobs]
            for future in as_completed(futures):
                target_row, target_long_rows = future.result()
                target_rows.append(target_row)
                long_rows.extend(target_long_rows)

    target_rows.sort(key=lambda r: (str(r.get("chip_id", "")), str(r.get("anchor_id", "")), str(r.get("target_label", ""))))
    long_rows.sort(
        key=lambda r: (
            str(r.get("chip_id", "")),
            str(r.get("anchor_id", "")),
            str(r.get("target_label", "")),
            str(r.get("capture_date", "")),
        )
    )
    return target_rows, long_rows


def _default_env_file() -> Path:
    local = PROJECT_ROOT / ".env.gemini.local"
    if local.exists():
        return local
    zasolar_root = Path(os.environ.get("ZASOLAR_ROOT", "/home/gaosh/projects/ZAsolar"))
    main_local = zasolar_root / ".env.gemini.local"
    return main_local if main_local.exists() else local


def _load_gemini_config_from_args(args: argparse.Namespace) -> GeminiClientConfig:
    env = load_env_file(args.env_file)
    base_url = args.base_url or env_value(env, "GOOGLE_GEMINI_BASE_URL")
    api_key = args.api_key or env_value(env, "GEMINI_API_KEY")
    model = args.model or env_value(env, "GEMINI_MODEL", "gemini-3-flash-preview")
    api_format = args.api_format or env_value(env, "GEMINI_API_FORMAT", "native")
    native_path = args.native_path or env_value(env, "GEMINI_NATIVE_PATH", "/v1beta")
    timeout = args.timeout or int(env_value(env, "GEMINI_TIMEOUT", "120"))
    if not base_url:
        raise SystemExit(
            f"Missing GOOGLE_GEMINI_BASE_URL (set in {args.env_file} or pass --base-url)"
        )
    if not api_key:
        raise SystemExit(f"Missing GEMINI_API_KEY (set in {args.env_file} or pass --api-key)")
    if api_format not in API_FORMATS:
        raise SystemExit(f"Unsupported API format {api_format!r}; choose {sorted(API_FORMATS)}")
    return GeminiClientConfig(
        base_url=base_url,
        api_key=api_key,
        model=model,
        api_format=api_format,
        native_path=native_path,
        timeout=timeout,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    input_group = parser.add_mutually_exclusive_group(required=True)
    input_group.add_argument("--review-png-manifest", type=Path)
    input_group.add_argument("--chip-targets-csv", type=Path)
    parser.add_argument("--image-artifacts-csv", type=Path)
    parser.add_argument("--review-png-manifest-output", type=Path, default=DEFAULT_REVIEW_PNG_MANIFEST)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--long-output", type=Path, default=DEFAULT_LONG_OUTPUT)
    parser.add_argument("--audit-dir", type=Path, default=DEFAULT_AUDIT_DIR)
    parser.add_argument("--dates", required=True, help="Comma-separated ordered capture dates, e.g. 2018-03-30,2019-07-30,...")
    parser.add_argument("--env-file", type=Path, default=_default_env_file())
    parser.add_argument("--base-url")
    parser.add_argument("--api-key")
    parser.add_argument("--model")
    parser.add_argument("--api-format", choices=sorted(API_FORMATS))
    parser.add_argument("--native-path")
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=None,
        help="Optional Gemini output cap. Default omits max_tokens/maxOutputTokens.",
    )
    parser.add_argument("--timeout", type=int, help="Gemini request timeout in seconds. Defaults to GEMINI_TIMEOUT or 120.")
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--qps", type=float, default=0.3)
    parser.add_argument("--limit-targets", type=int)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--no-audit", action="store_true")
    parser.add_argument("--crop-context-multiplier", type=float, default=3.0)
    parser.add_argument("--min-crop-size-m", type=float, default=24.0)
    parser.add_argument("--min-output-px", type=int, default=128)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    dates = parse_dates_arg(args.dates)
    if args.review_png_manifest:
        if not args.review_png_manifest.exists():
            raise SystemExit(f"review PNG manifest not found: {args.review_png_manifest}")
        review_pngs = load_review_png_manifest(args.review_png_manifest)
        review_manifest_path = args.review_png_manifest
    else:
        if not args.chip_targets_csv or not args.chip_targets_csv.exists():
            raise SystemExit(f"chip targets CSV not found: {args.chip_targets_csv}")
        if not args.image_artifacts_csv or not args.image_artifacts_csv.exists():
            raise SystemExit("--image-artifacts-csv is required when using --chip-targets-csv")
        targets_by_chip = load_chip_targets_by_chip(args.chip_targets_csv)
        artifacts_by_chip = load_artifacts_by_chip(args.image_artifacts_csv)
        review_rows = render_review_png_manifest(
            targets_by_chip=targets_by_chip,
            artifacts_by_chip=artifacts_by_chip,
            dates=dates,
            crop_context_multiplier=args.crop_context_multiplier,
            min_crop_size_m=args.min_crop_size_m,
            min_output_px=args.min_output_px,
        )
        write_csv_rows(args.review_png_manifest_output, review_rows, REVIEW_PNG_FIELDS)
        review_pngs = [_review_png_from_row(row) for row in review_rows]
        review_pngs = [row for row in review_pngs if row is not None]
        review_manifest_path = args.review_png_manifest_output

    resume_target_rows: dict[tuple[str, str, str, str], dict[str, str]] = {}
    resume_long_rows: dict[tuple[str, str, str, str], list[dict[str, str]]] = {}
    if args.resume:
        resume_target_rows, resume_long_rows = _resume_rows(
            target_output=args.output,
            long_output=args.long_output,
            dates=dates,
        )

    config = _load_gemini_config_from_args(args)
    audit_dir = None if args.no_audit else args.audit_dir
    target_rows, long_rows = score_target_sequences(
        review_pngs=review_pngs,
        dates=dates,
        config=config,
        audit_dir=audit_dir,
        max_tokens=args.max_tokens,
        workers=args.workers,
        qps=args.qps,
        limit_targets=args.limit_targets,
        resume_target_rows=resume_target_rows,
        resume_long_rows=resume_long_rows,
    )
    write_csv_rows(args.output, target_rows, SEQUENCE_TARGET_FIELDS)
    write_csv_rows(args.long_output, long_rows, SEQUENCE_LONG_FIELDS)
    print(f"Review PNG manifest -> {review_manifest_path}")
    print(f"Wrote {len(target_rows)} target sequence rows -> {args.output}")
    print(f"Wrote {len(long_rows)} sequence long rows -> {args.long_output}")
    if audit_dir is not None:
        print(f"Wrote Gemini sequence audit JSONL under -> {audit_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
