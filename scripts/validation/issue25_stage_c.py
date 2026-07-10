#!/usr/bin/env python3
"""Deterministic preparation helpers for ISSUE-25 Stage C."""

from __future__ import annotations

import argparse
import csv
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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    prepare = sub.add_parser("prepare-stage1")
    prepare.add_argument("--manifest", type=Path, required=True)
    prepare.add_argument("--chip-targets", type=Path, required=True)
    prepare.add_argument("--output", type=Path, required=True)
    prepare.add_argument("--expected-count", type=int, default=400)
    args = parser.parse_args()

    if args.command == "prepare-stage1":
        count = prepare_stage1_anchors(
            args.manifest,
            args.chip_targets,
            args.output,
            expected_count=args.expected_count,
        )
        print(f"Wrote {count} ISSUE-25 stage1 anchors -> {args.output}")


if __name__ == "__main__":
    main()
