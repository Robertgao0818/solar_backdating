#!/usr/bin/env python3
"""Freeze a blind dual-review calibration package from the failed CT-11 set.

This is development evidence only.  It deliberately consumes the already
failed/reviewed 500-anchor package and must never read the sealed disjoint
confirmation holdout.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import random
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from scripts.temporal.prepare_ct11_disjoint_holdout import proportional_counts
from scripts.temporal.render_ct11_native_review import render_native_review


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def select_calibration(
    manifest: list[dict[str, Any]],
    pass1_by_blind: dict[int, dict[str, str]],
    repeat_by_anchor: dict[str, dict[str, str]],
    *,
    n: int,
    seed: int,
) -> list[dict[str, Any]]:
    """Take every repeat-state conflict, then stratify the remaining capacity."""
    by_anchor = {row["anchor_id"]: row for row in manifest}
    if len(by_anchor) != len(manifest):
        raise ValueError("manifest anchor IDs are not unique")
    enriched: list[dict[str, Any]] = []
    for row in manifest:
        pass1 = pass1_by_blind[int(row["blind_index"])]
        repeat = repeat_by_anchor.get(row["anchor_id"])
        conflict = bool(repeat and repeat["independent_class"] != pass1["independent_class"])
        enriched.append(
            {
                **row,
                "old_pass1_class": pass1["independent_class"],
                "old_repeat_class": repeat["independent_class"] if repeat else "",
                "selection_reason": "repeat_state_conflict" if conflict else "stratified_remainder",
            }
        )
    conflicts = [row for row in enriched if row["selection_reason"] == "repeat_state_conflict"]
    if len(conflicts) > n:
        raise ValueError(f"{len(conflicts)} repeat conflicts exceed calibration n={n}")
    remainder = [row for row in enriched if row["selection_reason"] != "repeat_state_conflict"]
    strata: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in remainder:
        key = f"{row['old_pass1_class']}__{len(row['frame_dates'])}_frames"
        strata[key].append(row)
    counts = proportional_counts({key: len(pool) for key, pool in strata.items()}, n - len(conflicts))
    selected = list(conflicts)
    for key in sorted(counts):
        pool = sorted(strata[key], key=lambda row: row["anchor_id"])
        selected.extend(random.Random(f"{seed}:{key}").sample(pool, counts[key]))
    if len(selected) != n or len({row["anchor_id"] for row in selected}) != n:
        raise AssertionError("calibration selection is not n unique anchors")
    random.Random(f"{seed}:blind-order").shuffle(selected)
    return selected


def write_locked(path: Path, text: str) -> None:
    if path.exists():
        if path.read_text(encoding="utf-8") != text:
            raise ValueError(f"existing frozen output differs: {path}")
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    os.chmod(path, 0o444)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--repeat-manifest", required=True, type=Path)
    parser.add_argument("--pass1", required=True, type=Path)
    parser.add_argument("--repeat", required=True, type=Path)
    parser.add_argument("--frame-report", required=True, type=Path)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--n", type=int, default=100)
    parser.add_argument("--seed", type=int, default=2026080403)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.n != 100:
        raise SystemExit("human calibration design is locked to n=100")
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    repeat_manifest = json.loads(args.repeat_manifest.read_text(encoding="utf-8"))
    pass1_rows = read_csv(args.pass1)
    repeat_rows = read_csv(args.repeat)
    if len(manifest) != 500 or len(pass1_rows) != 500:
        raise ValueError("expected the failed 500-anchor CT-11 development set")
    if len(repeat_manifest) != 100 or len(repeat_rows) != 100:
        raise ValueError("expected the failed CT-11 100-anchor repeat set")
    pass1_by_blind = {int(row["blind_index"]): row for row in pass1_rows}
    anchor_by_repeat = {int(row["repeat_index"]): row["anchor_id"] for row in repeat_manifest}
    repeat_by_anchor = {
        anchor_by_repeat[int(row["repeat_index"])]: row for row in repeat_rows
    }
    selected = select_calibration(
        manifest, pass1_by_blind, repeat_by_anchor, n=args.n, seed=args.seed
    )
    private_rows = [
        {
            "calibration_index": index,
            "anchor_id": row["anchor_id"],
            "grid_id": row["grid_id"],
            "frame_dates": row["frame_dates"],
            "selection_reason": row["selection_reason"],
            "old_pass1_class": row["old_pass1_class"],
            "old_repeat_class": row["old_repeat_class"],
        }
        for index, row in enumerate(selected, start=1)
    ]
    render_input = [
        {
            "blind_index": row["calibration_index"],
            "anchor_id": row["anchor_id"],
            "grid_id": row["grid_id"],
            "frame_dates": row["frame_dates"],
        }
        for row in private_rows
    ]
    private_dir = args.output_root / "private"
    blind_dir = args.output_root / "reviewer_blind"
    private_path = private_dir / "calibration_selection.json"
    render_input_path = private_dir / "render_input.json"
    write_locked(private_path, json.dumps(private_rows, indent=2, sort_keys=True) + "\n")
    write_locked(render_input_path, json.dumps(render_input, indent=2, sort_keys=True) + "\n")

    package = render_native_review(
        render_input_path,
        args.frame_report,
        blind_dir,
        rows_per_sheet=1,
        prefix="ct11_human_calibration",
    )
    contract = {
        "schema_version": 1,
        "status": "FROZEN_AWAITING_TWO_INDEPENDENT_HUMAN_REVIEWS",
        "purpose": "old failed CT-11 development calibration; not acceptance",
        "n": args.n,
        "selection_seed": args.seed,
        "reviewers": 2,
        "independent_before_adjudication": True,
        "allowed_classes": ["ALREADY_PRESENT", "ALL_ABSENT", "TRANSITION", "UNDATABLE"],
        "required_fields": [
            "calibration_index", "independent_class", "latest_absent",
            "earliest_present", "review_confidence", "rationale",
        ],
        "rules": [
            "Judge only the yellow-marker target roof across the dated frames.",
            "Blur, tree cover, shadow, missing imagery, or loss of focus is never absence.",
            "TRANSITION requires a defensible last-absent and first-present frame.",
            "Use UNDATABLE when the target cannot support a defensible temporal state.",
            "Do not access private selection, production outcomes, or old verdicts.",
        ],
        "adjudication": "Only rows where the two independent class/date decisions differ are opened for adjudication.",
        "contains_new_disjoint_holdout": False,
        "blind_package_sha256": sha256_file(blind_dir / "ct11_human_calibration_package.json"),
    }
    contract_path = blind_dir / "REVIEW_CONTRACT.json"
    write_locked(contract_path, json.dumps(contract, indent=2, sort_keys=True) + "\n")
    for path in blind_dir.iterdir():
        if path.is_file():
            os.chmod(path, 0o444)
    summary = {
        "schema_version": 1,
        "status": contract["status"],
        "n": args.n,
        "n_repeat_state_conflicts": sum(
            row["selection_reason"] == "repeat_state_conflict" for row in private_rows
        ),
        "selection_reason_counts": dict(Counter(row["selection_reason"] for row in private_rows)),
        "old_pass1_class_counts": dict(Counter(row["old_pass1_class"] for row in private_rows)),
        "frame_count_counts": dict(Counter(len(row["frame_dates"]) for row in private_rows)),
        "private_selection_sha256": sha256_file(private_path),
        "render_input_sha256": sha256_file(render_input_path),
        "blind_package_sha256": sha256_file(blind_dir / "ct11_human_calibration_package.json"),
        "review_contract_sha256": sha256_file(contract_path),
        "contains_new_disjoint_holdout": False,
        "contains_production_outcomes_in_blind_package": package["contains_production_outcomes"],
    }
    summary_path = args.output_root / "CALIBRATION_LOCK.json"
    write_locked(summary_path, json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
