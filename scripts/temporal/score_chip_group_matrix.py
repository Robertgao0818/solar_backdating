#!/usr/bin/env python3
"""Score multi-target GEHI chip groups with Gemini matrix review.

Inputs come from the chip-group bridge:

- `chip_targets.csv` from `build_inventory_chip_groups.py`
- a GEHI image artifact manifest from `gehi_download.py`, where `anchor_id`
  is the chip group id

For each chip group, this script annotates each dated chip with visible
T01/T02/... markers, calls the bounded date x target matrix scorer, and writes
target-level `presence_timeseries.csv` rows compatible with the legacy interval
helpers.
"""

# ruff: noqa: E402

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.temporal.gehi_common import ReviewTargetMarker, ensure_target_review_png
from scripts.temporal.geid_temporal_common import read_csv_rows, write_csv_rows
from scripts.temporal.score_anchor_presence import PRESENCE_FIELDS
from scripts.validation.gemini_solar_image_review import (
    API_FORMATS,
    DEFAULT_MAX_MATRIX_DATES,
    DEFAULT_MAX_MATRIX_TARGETS,
    HARD_MAX_MATRIX_CELLS,
    HARD_MAX_MATRIX_TARGETS,
    GeminiClientConfig,
    GeminiMatrixObservation,
    MatrixDatePick,
    MatrixTarget,
    env_value,
    load_env_file,
    score_target_date_matrix,
)

DEFAULT_CHIP_TARGETS = (
    Path.home()
    / "zasolar_data/geid_temporal/jhb_full382_unified_A_merge01_c0925_chipgroups/chip_targets.csv"
)
DEFAULT_ARTIFACTS = Path.home() / "zasolar_data/geid_temporal/gehi_image_artifacts.csv"
DEFAULT_OUTPUT = Path.home() / "zasolar_data/geid_temporal/chip_group_presence_timeseries.csv"
DEFAULT_AUDIT_DIR = Path.home() / "zasolar_data/geid_temporal/chip_group_gemini_audit"

MATRIX_PRESENCE_FIELDS = [
    *PRESENCE_FIELDS,
    "chip_id",
    "target_label",
    "matrix_date_index",
    "matrix_cell_index",
    "actual_zoom",
    "review_png_path",
    "source_chip_path",
    "gemini_confidence",
    "gemini_error",
    "gemini_evidence",
]


@dataclass(frozen=True)
class ChipTarget:
    chip_id: str
    target_id: str
    target_label: str
    target_index: int
    region_key: str
    grid_id: str
    offset_x_m: float
    offset_y_m: float
    search_radius_m: float
    chip_size_m: float

    @property
    def matrix_target(self) -> MatrixTarget:
        return MatrixTarget(target_id=self.target_id, target_label=self.target_label)

    @property
    def review_marker(self) -> ReviewTargetMarker:
        return ReviewTargetMarker(
            target_id=self.target_id,
            target_label=self.target_label,
            offset_x_m=self.offset_x_m,
            offset_y_m=self.offset_y_m,
            search_radius_m=self.search_radius_m,
        )


@dataclass(frozen=True)
class ChipArtifact:
    chip_id: str
    capture_date: str
    version: str
    path: Path
    actual_zoom: int | None
    status: str


def _float_field(row: Mapping[str, object], field: str, *, default: float | None = None) -> float:
    value = str(row.get(field, "")).strip()
    if value == "":
        if default is None:
            raise ValueError(f"missing required numeric field {field!r}")
        return default
    return float(value)


def _int_field(row: Mapping[str, object], field: str, *, default: int = 0) -> int:
    value = str(row.get(field, "")).strip()
    return int(float(value)) if value else default


def chip_target_from_row(row: Mapping[str, object]) -> ChipTarget:
    target_id = str(row.get("anchor_id", "")).strip()
    chip_id = str(row.get("chip_id", "")).strip()
    target_label = str(row.get("target_label", "")).strip()
    if not target_id or not chip_id or not target_label:
        raise ValueError(f"chip target row missing anchor_id/chip_id/target_label: {row!r}")
    return ChipTarget(
        chip_id=chip_id,
        target_id=target_id,
        target_label=target_label,
        target_index=_int_field(row, "target_index"),
        region_key=str(row.get("region_key", "")).strip(),
        grid_id=str(row.get("grid_id", "")).strip(),
        offset_x_m=_float_field(row, "target_offset_x_m"),
        offset_y_m=_float_field(row, "target_offset_y_m"),
        search_radius_m=_float_field(row, "search_radius_m", default=10.0),
        chip_size_m=_float_field(row, "chip_size_m"),
    )


def load_chip_targets_by_chip(path: Path) -> dict[str, list[ChipTarget]]:
    grouped: dict[str, list[ChipTarget]] = defaultdict(list)
    for row in read_csv_rows(path):
        target = chip_target_from_row(row)
        grouped[target.chip_id].append(target)
    return {
        chip_id: sorted(targets, key=lambda t: (t.target_index, t.target_label, t.target_id))
        for chip_id, targets in grouped.items()
    }


def artifact_from_row(row: Mapping[str, object]) -> ChipArtifact | None:
    chip_id = str(row.get("anchor_id") or row.get("chip_id") or "").strip()
    capture_date = str(row.get("capture_date", "")).strip()[:10]
    path_text = str(row.get("path", "")).strip()
    if not chip_id or not capture_date or not path_text:
        return None
    status = str(row.get("status", "")).strip()
    if status and status not in {"ok", "skipped_existing"}:
        return None
    actual_zoom_text = str(row.get("actual_zoom") or row.get("zoom") or "").strip()
    actual_zoom = int(float(actual_zoom_text)) if actual_zoom_text else None
    return ChipArtifact(
        chip_id=chip_id,
        capture_date=capture_date,
        version=str(row.get("version", "")).strip(),
        path=Path(path_text),
        actual_zoom=actual_zoom,
        status=status or "ok",
    )


def load_artifacts_by_chip(path: Path) -> dict[str, list[ChipArtifact]]:
    grouped: dict[str, list[ChipArtifact]] = defaultdict(list)
    for row in read_csv_rows(path):
        artifact = artifact_from_row(row)
        if artifact is not None:
            grouped[artifact.chip_id].append(artifact)
    return {
        chip_id: sorted(items, key=lambda item: (item.capture_date, item.version, str(item.path)))
        for chip_id, items in grouped.items()
    }


def _chunked(items: Sequence[Any], size: int) -> list[list[Any]]:
    if size <= 0:
        raise ValueError("chunk size must be positive")
    return [list(items[i : i + size]) for i in range(0, len(items), size)]


def _bool_to_csv(value: bool | None) -> str:
    if value is True:
        return "1"
    if value is False:
        return "0"
    return ""


def _presence_row(
    *,
    chip_id: str,
    target: ChipTarget,
    artifact: ChipArtifact,
    review_png: Path,
    obs: GeminiMatrixObservation,
    extra_notes: Sequence[str] = (),
) -> dict[str, object]:
    pv_present = _bool_to_csv(obs.pv_present)
    notes = "; ".join(
        part
        for part in (
            f"chip_id={chip_id}",
            f"target_label={obs.target_label}",
            f"date_index={obs.date_index}",
            f"actual_zoom={artifact.actual_zoom or ''}",
            obs.notes,
            *extra_notes,
        )
        if part
    )
    return {
        "anchor_id": target.target_id,
        "region_key": target.region_key,
        "grid_id": target.grid_id,
        "requested_date": artifact.capture_date,
        "capture_date": obs.capture_date,
        "actual_capture_dates": obs.capture_date,
        "pv_score": pv_present,
        "pv_present": pv_present,
        "decision_source": obs.decision_source,
        "quality_flag": obs.quality_flag,
        "chip_dir": str(artifact.path.parent),
        "sample_chip_path": str(review_png),
        "n_jpg": 1,
        "task_name": chip_id,
        "save_to": str(artifact.path.parent),
        "notes": notes,
        "chip_id": chip_id,
        "target_label": target.target_label,
        "matrix_date_index": obs.date_index,
        "matrix_cell_index": obs.cell_index,
        "actual_zoom": artifact.actual_zoom or "",
        "review_png_path": str(review_png),
        "source_chip_path": str(artifact.path),
        "gemini_confidence": "" if obs.confidence is None else f"{obs.confidence:.4f}",
        "gemini_error": obs.error or "",
        "gemini_evidence": obs.evidence,
    }


def _append_note(row: dict[str, object], note: str) -> None:
    existing = str(row.get("notes") or "")
    if note in existing:
        return
    row["notes"] = f"{existing}; {note}" if existing else note


def flag_non_monotonic_rows(rows: list[dict[str, object]]) -> None:
    """Mark target time series with usable present->absent transitions.

    Matrix scoring can split one chip across several date chunks, where each
    chunk has local date_index values. The final rows carry true capture_date,
    so monotonic checks are deliberately done here at the CSV-row layer.
    """
    by_target: dict[tuple[str, str], list[dict[str, object]]] = defaultdict(list)
    for row in rows:
        key = (str(row.get("chip_id") or ""), str(row.get("anchor_id") or ""))
        if not all(key):
            continue
        by_target[key].append(row)

    note = "non_monotonic_requires_review: usable binary series has present->absent transition"
    for target_rows in by_target.values():
        seen_present = False
        flagged = False
        for row in sorted(target_rows, key=lambda r: str(r.get("capture_date") or "")):
            if row.get("decision_source") != "gemini_matrix":
                continue
            if row.get("quality_flag") != "usable":
                continue
            value = str(row.get("pv_present") or "")
            if value == "1":
                seen_present = True
            elif value == "0" and seen_present:
                flagged = True
                break
        if flagged:
            for row in target_rows:
                _append_note(row, note)



def _audit_writer_for(
    audit_dir: Path | None,
    *,
    chip_id: str,
    date_chunk_index: int,
    target_chunk_index: int,
) -> Callable[[dict[str, Any]], None] | None:
    if audit_dir is None:
        return None
    path = (
        audit_dir
        / chip_id
        / f"matrix_dates{date_chunk_index:03d}_targets{target_chunk_index:03d}.jsonl"
    )
    path.parent.mkdir(parents=True, exist_ok=True)

    def _write(payload: dict[str, Any]) -> None:
        record = dict(payload)
        record["chip_id"] = chip_id
        record["date_chunk_index"] = date_chunk_index
        record["target_chunk_index"] = target_chunk_index
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    return _write


def score_chip_group_matrices(
    *,
    artifacts_by_chip: Mapping[str, Sequence[ChipArtifact]],
    targets_by_chip: Mapping[str, Sequence[ChipTarget]],
    config: GeminiClientConfig,
    audit_dir: Path | None = None,
    scorer: Callable[..., list[GeminiMatrixObservation]] = score_target_date_matrix,
    max_dates: int = DEFAULT_MAX_MATRIX_DATES,
    max_targets: int = DEFAULT_MAX_MATRIX_TARGETS,
    hard_max_targets: int = HARD_MAX_MATRIX_TARGETS,
    hard_max_cells: int = HARD_MAX_MATRIX_CELLS,
    limit_chips: int | None = None,
) -> list[dict[str, object]]:
    if max_dates <= 0:
        raise ValueError("max_dates must be positive")
    if max_targets <= 0:
        raise ValueError("max_targets must be positive")
    if hard_max_cells <= 0:
        raise ValueError("hard_max_cells must be positive")

    rows: list[dict[str, object]] = []
    chip_ids = sorted(set(targets_by_chip) & set(artifacts_by_chip))
    if limit_chips is not None:
        chip_ids = chip_ids[:limit_chips]

    for chip_id in chip_ids:
        targets = list(targets_by_chip[chip_id])
        artifacts = [a for a in artifacts_by_chip[chip_id] if a.path.exists()]
        if not targets or not artifacts:
            continue
        for target_chunk_index, target_chunk in enumerate(_chunked(targets, max_targets), start=1):
            if len(target_chunk) > hard_max_targets:
                raise ValueError(
                    f"target chunk for {chip_id} has {len(target_chunk)} targets, "
                    f"exceeding hard_max_targets={hard_max_targets}"
                )
            date_chunk_size = min(max_dates, max(1, hard_max_cells // len(target_chunk)))
            for date_chunk_index, artifact_chunk in enumerate(
                _chunked(artifacts, date_chunk_size),
                start=1,
            ):
                matrix_targets = [target.matrix_target for target in target_chunk]
                target_lookup = {target.target_label: target for target in target_chunk}
                date_picks: list[MatrixDatePick] = []
                review_png_by_date_index: dict[int, Path] = {}
                artifact_by_date_index: dict[int, ChipArtifact] = {}
                markers = [target.review_marker for target in target_chunk]
                chip_size_m = target_chunk[0].chip_size_m
                for local_idx, artifact in enumerate(artifact_chunk, start=1):
                    review_png = ensure_target_review_png(
                        artifact.path,
                        markers,
                        chip_size_m=chip_size_m,
                    )
                    review_png_by_date_index[local_idx] = review_png
                    artifact_by_date_index[local_idx] = artifact
                    date_picks.append(
                        MatrixDatePick(
                            date_index=local_idx,
                            chip_path=review_png,
                            capture_date=artifact.capture_date,
                            version=artifact.version,
                            actual_zoom=artifact.actual_zoom,
                        )
                    )
                observations = scorer(
                    date_picks,
                    matrix_targets,
                    config=config,
                    audit_writer=_audit_writer_for(
                        audit_dir,
                        chip_id=chip_id,
                        date_chunk_index=date_chunk_index,
                        target_chunk_index=target_chunk_index,
                    ),
                    max_dates=max_dates,
                    max_targets=max_targets,
                    hard_max_targets=hard_max_targets,
                    hard_max_cells=hard_max_cells,
                )
                for obs in observations:
                    target = target_lookup[obs.target_label]
                    artifact = artifact_by_date_index[obs.date_index]
                    review_png = review_png_by_date_index[obs.date_index]
                    rows.append(
                        _presence_row(
                            chip_id=chip_id,
                            target=target,
                            artifact=artifact,
                            review_png=review_png,
                            obs=obs,
                        )
                    )
    flag_non_monotonic_rows(rows)
    return rows


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
    max_tokens_per_chip = args.max_tokens_per_chip or int(
        env_value(env, "GEMINI_MAX_TOKENS_PER_CHIP", "600")
    )
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
        max_tokens_per_chip=max_tokens_per_chip,
        timeout=timeout,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chip-targets-csv", type=Path, default=DEFAULT_CHIP_TARGETS)
    parser.add_argument("--image-artifacts-csv", type=Path, default=DEFAULT_ARTIFACTS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--audit-dir", type=Path, default=DEFAULT_AUDIT_DIR)
    parser.add_argument("--env-file", type=Path, default=_default_env_file())
    parser.add_argument("--base-url")
    parser.add_argument("--api-key")
    parser.add_argument("--model")
    parser.add_argument("--api-format", choices=sorted(API_FORMATS))
    parser.add_argument("--native-path")
    parser.add_argument(
        "--max-tokens-per-chip",
        type=int,
        help=(
            "Gemini output-token budget multiplier for matrix scoring. "
            "Defaults to GEMINI_MAX_TOKENS_PER_CHIP or 600."
        ),
    )
    parser.add_argument(
        "--timeout",
        type=int,
        help="Gemini request timeout in seconds. Defaults to GEMINI_TIMEOUT or 120.",
    )
    parser.add_argument("--max-dates", type=int, default=DEFAULT_MAX_MATRIX_DATES)
    parser.add_argument("--max-targets", type=int, default=DEFAULT_MAX_MATRIX_TARGETS)
    parser.add_argument("--hard-max-targets", type=int, default=HARD_MAX_MATRIX_TARGETS)
    parser.add_argument("--hard-max-cells", type=int, default=HARD_MAX_MATRIX_CELLS)
    parser.add_argument("--limit-chips", type=int)
    parser.add_argument(
        "--no-audit",
        action="store_true",
        help="Do not write per-call Gemini audit JSONL files.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.chip_targets_csv.exists():
        raise SystemExit(f"chip targets CSV not found: {args.chip_targets_csv}")
    if not args.image_artifacts_csv.exists():
        raise SystemExit(f"image artifacts CSV not found: {args.image_artifacts_csv}")
    targets_by_chip = load_chip_targets_by_chip(args.chip_targets_csv)
    artifacts_by_chip = load_artifacts_by_chip(args.image_artifacts_csv)
    config = _load_gemini_config_from_args(args)
    audit_dir = None if args.no_audit else args.audit_dir
    rows = score_chip_group_matrices(
        artifacts_by_chip=artifacts_by_chip,
        targets_by_chip=targets_by_chip,
        config=config,
        audit_dir=audit_dir,
        max_dates=args.max_dates,
        max_targets=args.max_targets,
        hard_max_targets=args.hard_max_targets,
        hard_max_cells=args.hard_max_cells,
        limit_chips=args.limit_chips,
    )
    write_csv_rows(args.output, rows, MATRIX_PRESENCE_FIELDS)
    print(f"Wrote {len(rows)} matrix presence rows -> {args.output}")
    if audit_dir is not None:
        print(f"Wrote Gemini matrix audit JSONL under -> {audit_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
