#!/usr/bin/env python3
"""Validate two frozen CT-11 human reviews and build the adjudication queue."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
from pathlib import Path


CLASSES = {"ALREADY_PRESENT", "ALL_ABSENT", "TRANSITION", "UNDATABLE"}
CONFIDENCES = {"HIGH", "MEDIUM", "LOW"}
FIELDS = [
    "calibration_index", "independent_class", "latest_absent",
    "earliest_present", "review_confidence", "rationale",
]
ADJUDICATION_FIELDS = [
    "calibration_index", "adjudicated_class", "latest_absent",
    "earliest_present", "adjudicator_id", "rationale",
]


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def wilson(k: int, n: int, z: float = 1.959963984540054) -> tuple[float, float]:
    if n <= 0:
        raise ValueError("Wilson interval requires n > 0")
    p = k / n
    denominator = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denominator
    radius = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denominator
    return center - radius, center + radius


def validate_review(
    path: Path, frames_by_index: dict[int, list[str]], *, require_read_only: bool = True
) -> dict[int, dict[str, str]]:
    if require_read_only and path.stat().st_mode & 0o222:
        raise ValueError(f"review must be frozen read-only before comparison: {path}")
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != FIELDS:
            raise ValueError(f"review fields differ from contract: {reader.fieldnames}")
        rows = list(reader)
    by_index: dict[int, dict[str, str]] = {}
    for row in rows:
        index = int(row["calibration_index"])
        if index in by_index or index not in frames_by_index:
            raise ValueError(f"duplicate or unknown calibration_index: {index}")
        state = row["independent_class"].strip().upper()
        confidence = row["review_confidence"].strip().upper()
        latest = row["latest_absent"].strip()
        earliest = row["earliest_present"].strip()
        rationale = row["rationale"].strip()
        dates = frames_by_index[index]
        if state not in CLASSES or confidence not in CONFIDENCES or not rationale:
            raise ValueError(f"incomplete/invalid decision at calibration_index={index}")
        validate_dates(index, state, latest, earliest, dates)
        by_index[index] = {
            **row,
            "independent_class": state,
            "review_confidence": confidence,
            "latest_absent": latest,
            "earliest_present": earliest,
            "rationale": rationale,
        }
    if set(by_index) != set(frames_by_index):
        missing = sorted(set(frames_by_index) - set(by_index))
        raise ValueError(f"review is incomplete; missing indices: {missing[:10]}")
    return by_index


def validate_dates(index: int, state: str, latest: str, earliest: str, dates: list[str]) -> None:
    if state == "ALREADY_PRESENT":
        if latest or earliest != dates[0]:
            raise ValueError(f"ALREADY_PRESENT must use first frame as earliest_present: {index}")
    elif state == "ALL_ABSENT":
        if latest != dates[-1] or earliest:
            raise ValueError(f"ALL_ABSENT must use last frame as latest_absent: {index}")
    elif state == "TRANSITION":
        if latest not in dates or earliest not in dates or dates.index(latest) >= dates.index(earliest):
            raise ValueError(f"invalid TRANSITION bracket: {index}")
    elif state == "UNDATABLE":
        if latest or earliest:
            raise ValueError(f"UNDATABLE dates must be empty: {index}")
    else:
        raise ValueError(f"unknown class at calibration_index={index}: {state}")


def validate_adjudication(
    path: Path, conflict_indices: set[int], frames_by_index: dict[int, list[str]]
) -> dict[int, dict[str, str]]:
    if path.stat().st_mode & 0o222:
        raise ValueError(f"adjudication must be frozen read-only: {path}")
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != ADJUDICATION_FIELDS:
            raise ValueError(f"adjudication fields differ from contract: {reader.fieldnames}")
        rows = list(reader)
    decisions: dict[int, dict[str, str]] = {}
    for row in rows:
        index = int(row["calibration_index"])
        if index in decisions or index not in conflict_indices:
            raise ValueError(f"duplicate or non-conflict adjudication index: {index}")
        state = row["adjudicated_class"].strip().upper()
        latest, earliest = row["latest_absent"].strip(), row["earliest_present"].strip()
        if not row["adjudicator_id"].strip() or not row["rationale"].strip():
            raise ValueError(f"incomplete adjudication at calibration_index={index}")
        validate_dates(index, state, latest, earliest, frames_by_index[index])
        decisions[index] = {
            "independent_class": state,
            "latest_absent": latest,
            "earliest_present": earliest,
            "adjudicator_id": row["adjudicator_id"].strip(),
            "rationale": row["rationale"].strip(),
        }
    if set(decisions) != conflict_indices:
        raise ValueError(f"adjudication must cover exactly {len(conflict_indices)} conflict rows")
    return decisions


def compare_reviews(
    review_a: dict[int, dict[str, str]], review_b: dict[int, dict[str, str]]
) -> tuple[dict, list[dict[str, str]]]:
    state_matches = interval_matches = 0
    conflicts: list[dict[str, str]] = []
    for index in sorted(review_a):
        a, b = review_a[index], review_b[index]
        state_match = a["independent_class"] == b["independent_class"]
        interval_match = state_match and (
            a["latest_absent"], a["earliest_present"]
        ) == (b["latest_absent"], b["earliest_present"])
        state_matches += state_match
        interval_matches += interval_match
        if not interval_match:
            conflicts.append(
                {
                    "calibration_index": str(index),
                    "reviewer_a_class": a["independent_class"],
                    "reviewer_a_latest_absent": a["latest_absent"],
                    "reviewer_a_earliest_present": a["earliest_present"],
                    "reviewer_a_confidence": a["review_confidence"],
                    "reviewer_a_rationale": a["rationale"],
                    "reviewer_b_class": b["independent_class"],
                    "reviewer_b_latest_absent": b["latest_absent"],
                    "reviewer_b_earliest_present": b["earliest_present"],
                    "reviewer_b_confidence": b["review_confidence"],
                    "reviewer_b_rationale": b["rationale"],
                }
            )
    n = len(review_a)
    metrics = {
        "n": n,
        "state_matches": state_matches,
        "state_agreement": state_matches / n,
        "state_wilson_95": wilson(state_matches, n),
        "interval_matches": interval_matches,
        "interval_agreement": interval_matches / n,
        "interval_wilson_95": wilson(interval_matches, n),
        "n_adjudication_rows": len(conflicts),
    }
    return metrics, conflicts


def write_locked(path: Path, content: str) -> None:
    if path.exists():
        if path.read_text(encoding="utf-8") != content:
            raise ValueError(f"existing frozen output differs: {path}")
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    os.chmod(path, 0o444)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--blind-manifest", required=True, type=Path)
    parser.add_argument("--review-a", required=True, type=Path)
    parser.add_argument("--review-b", required=True, type=Path)
    parser.add_argument("--adjudication", type=Path)
    parser.add_argument("--output-root", required=True, type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    blind = json.loads(args.blind_manifest.read_text(encoding="utf-8"))
    frames = {int(row["blind_index"]): list(row["frame_dates"]) for row in blind}
    if len(frames) != 100 or set(frames) != set(range(1, 101)):
        raise ValueError("blind calibration manifest must contain indices 1..100")
    a = validate_review(args.review_a, frames)
    b = validate_review(args.review_b, frames)
    metrics, conflicts = compare_reviews(a, b)
    args.output_root.mkdir(parents=True, exist_ok=True)
    queue_path = args.output_root / "adjudication_queue.csv"
    queue_fields = list(conflicts[0]) if conflicts else [
        "calibration_index", "reviewer_a_class", "reviewer_a_latest_absent",
        "reviewer_a_earliest_present", "reviewer_a_confidence", "reviewer_a_rationale",
        "reviewer_b_class", "reviewer_b_latest_absent", "reviewer_b_earliest_present",
        "reviewer_b_confidence", "reviewer_b_rationale",
    ]
    import io
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=queue_fields, lineterminator="\n")
    writer.writeheader()
    writer.writerows(conflicts)
    write_locked(queue_path, buffer.getvalue())
    summary = {
        "schema_version": 1,
        "status": "AWAITING_ADJUDICATION" if conflicts else "NO_ADJUDICATION_REQUIRED",
        "purpose": "old failed CT-11 human calibration; not acceptance",
        "blind_manifest_sha256": sha256_file(args.blind_manifest),
        "review_a_sha256": sha256_file(args.review_a),
        "review_b_sha256": sha256_file(args.review_b),
        "metrics": metrics,
        "adjudication_queue_sha256": sha256_file(queue_path),
        "contains_new_disjoint_holdout": False,
    }
    summary_path = args.output_root / "independent_review_metrics.json"
    write_locked(summary_path, json.dumps(summary, indent=2, sort_keys=True) + "\n")
    if args.adjudication is not None:
        conflict_indices = {int(row["calibration_index"]) for row in conflicts}
        decisions = validate_adjudication(args.adjudication, conflict_indices, frames)
        final_rows = []
        for index in sorted(frames):
            if index in decisions:
                decision = decisions[index]
                source = "human_adjudication"
                rationale = decision["rationale"]
                adjudicator_id = decision["adjudicator_id"]
            else:
                decision = a[index]
                source = "independent_exact_agreement"
                rationale = f"A: {a[index]['rationale']} | B: {b[index]['rationale']}"
                adjudicator_id = ""
            final_rows.append(
                {
                    "calibration_index": index,
                    "reference_class": decision["independent_class"],
                    "latest_absent": decision["latest_absent"],
                    "earliest_present": decision["earliest_present"],
                    "label_source": source,
                    "adjudicator_id": adjudicator_id,
                    "rationale": rationale,
                }
            )
        final_path = args.output_root / "final_reference_labels.csv"
        final_fields = list(final_rows[0])
        final_buffer = io.StringIO(newline="")
        final_writer = csv.DictWriter(final_buffer, fieldnames=final_fields, lineterminator="\n")
        final_writer.writeheader()
        final_writer.writerows(final_rows)
        write_locked(final_path, final_buffer.getvalue())
        final_summary = {
            "schema_version": 1,
            "status": "HUMAN_CALIBRATION_COMPLETE",
            "purpose": "old failed CT-11 development reference; not acceptance",
            "n": len(final_rows),
            "n_independent_exact_agreement": len(final_rows) - len(decisions),
            "n_adjudicated": len(decisions),
            "adjudication_sha256": sha256_file(args.adjudication),
            "final_reference_labels_sha256": sha256_file(final_path),
            "contains_new_disjoint_holdout": False,
        }
        final_summary_path = args.output_root / "FINAL_REFERENCE_LOCK.json"
        write_locked(final_summary_path, json.dumps(final_summary, indent=2, sort_keys=True) + "\n")
        summary["final_reference"] = final_summary
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
