#!/usr/bin/env python3
"""Deterministic preparation helpers for ISSUE-25 Stage C."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as fh:
        return [dict(row) for row in csv.DictReader(fh)]


def prepare_stage1_anchors(
    manifest_path: Path,
    chip_targets_path: Path,
    output_path: Path,
    *,
    expected_count: int = 400,
) -> int:
    """Filter the frozen core and attach the teacher footprint bbox dimensions."""
    manifest = _read_csv(manifest_path)
    target_rows = _read_csv(chip_targets_path)
    bbox_by_anchor = {
        row["anchor_id"]: (row.get("source_width_m", ""), row.get("source_height_m", ""))
        for row in target_rows
    }
    selected = [row for row in manifest if row.get("sample_stage") == "stage1_core"]
    if len(selected) != expected_count:
        raise ValueError(
            f"expected {expected_count} stage1_core anchors, found {len(selected)}"
        )
    for row in selected:
        anchor_id = row.get("anchor_id", "")
        width, height = bbox_by_anchor.get(anchor_id, ("", ""))
        try:
            valid = float(width) > 0 and float(height) > 0
        except (TypeError, ValueError):
            valid = False
        if not valid:
            raise ValueError(f"missing footprint bbox for {anchor_id}")
        row["source_width_m"] = str(width)
        row["source_height_m"] = str(height)

    fieldnames = list(selected[0])
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(selected)
    return len(selected)


def validate_authenticated_preflight(root: Path, model: str) -> dict[str, int]:
    """Fail closed unless the bounded real call returned non-empty valid schema."""
    audit_rows = []
    for path in sorted((root / "audit").rglob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                audit_rows.append(json.loads(line))
    if not audit_rows:
        raise ValueError("authenticated preflight produced no audit calls")
    for row in audit_rows:
        if (
            row.get("stage") != "batch_attempt_1"
            or row.get("error") not in (None, "")
            or int(row.get("n_valid") or 0) <= 0
            or row.get("missing_indices") not in (None, [])
            or not str(row.get("raw_response") or "").strip()
        ):
            raise ValueError(f"authenticated preflight audit failed: {row}")

    provenance_path = root / "scoring_provenance.jsonl"
    provenance = [
        json.loads(line)
        for line in provenance_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if not provenance:
        raise ValueError("authenticated preflight produced no scored observations")
    wrong_models = sorted({str(row.get("model_id")) for row in provenance if row.get("model_id") != model})
    if wrong_models:
        raise ValueError(
            f"authenticated preflight model mismatch: expected {model}, got {wrong_models}"
        )
    return {
        "audit_calls": len(audit_rows),
        "scored_observations": len(provenance),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    prepare = sub.add_parser("prepare-stage1")
    prepare.add_argument("--manifest", type=Path, required=True)
    prepare.add_argument("--chip-targets", type=Path, required=True)
    prepare.add_argument("--output", type=Path, required=True)
    prepare.add_argument("--expected-count", type=int, default=400)
    validate = sub.add_parser("validate-preflight")
    validate.add_argument("--root", type=Path, required=True)
    validate.add_argument("--model", required=True)
    args = parser.parse_args()

    if args.command == "prepare-stage1":
        count = prepare_stage1_anchors(
            args.manifest,
            args.chip_targets,
            args.output,
            expected_count=args.expected_count,
        )
        print(f"Wrote {count} ISSUE-25 stage1 anchors -> {args.output}")
    elif args.command == "validate-preflight":
        summary = validate_authenticated_preflight(args.root, args.model)
        print(
            "Authenticated preflight passed: "
            f"audit_calls={summary['audit_calls']} "
            f"scored_observations={summary['scored_observations']}"
        )


if __name__ == "__main__":
    main()
