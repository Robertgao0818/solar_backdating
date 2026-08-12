#!/usr/bin/env python3
"""Freeze CT-52 production waves from the roster's stable grid order."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


DEFAULT_CANARY_GRIDS = (
    "CPT2932",
    "CPT2597",
    "CPT3790",
    "CPT3677",
    "CPT2124",
    "CPT2713",
)


def _read(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write(path: Path, rows: list[dict[str, str]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def plan(
    anchors_csv: Path,
    roster_csv: Path,
    output_dir: Path,
    *,
    canary_grids: tuple[str, ...] = DEFAULT_CANARY_GRIDS,
    max_anchors: int = 3500,
) -> dict[str, object]:
    anchors = _read(anchors_csv)
    roster = _read(roster_csv)
    fields = list(anchors[0]) if anchors else []
    if not anchors or not fields:
        raise ValueError("anchors CSV is empty")
    if not roster:
        raise ValueError("grid roster is empty")
    if max_anchors <= 0:
        raise ValueError("max_anchors must be positive")
    anchor_ids = [row.get("anchor_id", "") for row in anchors]
    if len(anchor_ids) != len(set(anchor_ids)) or any(not value for value in anchor_ids):
        raise ValueError("anchors must have unique non-empty anchor_id")
    grid_order = [row.get("source_grid", "") for row in roster]
    if len(grid_order) != len(set(grid_order)) or any(not value for value in grid_order):
        raise ValueError("roster must have unique non-empty source_grid")
    anchor_by_grid: dict[str, list[dict[str, str]]] = {grid: [] for grid in grid_order}
    for row in anchors:
        grid = row.get("source_grid", "")
        if grid not in anchor_by_grid:
            raise ValueError(f"anchor {row.get('anchor_id')} uses grid absent from roster: {grid}")
        anchor_by_grid[grid].append(row)
    canary = set(canary_grids)
    if len(canary) != len(canary_grids):
        raise ValueError("duplicate canary grid")
    if not canary <= set(grid_order):
        raise ValueError(f"canary grid(s) absent from roster: {sorted(canary - set(grid_order))}")

    remaining = [grid for grid in grid_order if grid not in canary]
    waves: list[list[str]] = []
    current: list[str] = []
    current_count = 0
    for grid in remaining:
        count = len(anchor_by_grid[grid])
        if count > max_anchors:
            raise ValueError(f"single grid {grid} has {count} anchors > max_anchors={max_anchors}")
        if current and current_count + count > max_anchors:
            waves.append(current)
            current = []
            current_count = 0
        current.append(grid)
        current_count += count
    if current:
        waves.append(current)

    if output_dir.exists() and any(output_dir.iterdir()):
        raise FileExistsError(f"refusing to overwrite non-empty wave output: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    wave_records: list[dict[str, object]] = []
    for index, grids in enumerate(waves, 1):
        rows = [row for grid in grids for row in anchor_by_grid[grid]]
        wave_dir = output_dir / f"wave_{index:02d}"
        all_path = wave_dir / f"wave_{index:02d}_all.csv"
        a24 = [row for row in rows if row.get("chip_arm") == "A24"]
        a48 = [row for row in rows if row.get("chip_arm") == "A48"]
        a24_path = wave_dir / f"wave_{index:02d}_A24.csv"
        a48_path = wave_dir / f"wave_{index:02d}_A48.csv"
        _write(all_path, rows, fields)
        _write(a24_path, a24, fields)
        _write(a48_path, a48, fields)
        wave_records.append(
            {
                "wave": index,
                "grids": grids,
                "anchor_count": len(rows),
                "A24": len(a24),
                "A48": len(a48),
                "all_csv": str(all_path),
                "A24_csv": str(a24_path),
                "A48_csv": str(a48_path),
                "sha256": {
                    "all": _sha(all_path),
                    "A24": _sha(a24_path),
                    "A48": _sha(a48_path),
                },
            }
        )
    plan = {
        "schema_version": "ct52_production_wave_plan_v1",
        "anchors_csv": str(anchors_csv),
        "anchors_sha256": _sha(anchors_csv),
        "roster_csv": str(roster_csv),
        "roster_sha256": _sha(roster_csv),
        "anchor_count": len(anchors),
        "grid_count": len(grid_order),
        "canary_grids": list(canary_grids),
        "canary_anchor_count": sum(len(anchor_by_grid[g]) for g in canary_grids),
        "remaining_grid_count": len(remaining),
        "max_anchors_per_wave": max_anchors,
        "wave_count": len(wave_records),
        "wave_records": wave_records,
        "totals": {
            "anchor_count": sum(int(record["anchor_count"]) for record in wave_records),
            "A24": sum(int(record["A24"]) for record in wave_records),
            "A48": sum(int(record["A48"]) for record in wave_records),
        },
    }
    (output_dir / "wave_plan.json").write_text(
        json.dumps(plan, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return plan


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--anchors-csv", type=Path, required=True)
    parser.add_argument("--roster-csv", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--canary-grid", action="append", dest="canary_grids")
    parser.add_argument("--max-anchors", type=int, default=3500)
    args = parser.parse_args()
    canary = tuple(args.canary_grids) if args.canary_grids else DEFAULT_CANARY_GRIDS
    print(json.dumps(plan(args.anchors_csv, args.roster_csv, args.output_dir, canary_grids=canary, max_anchors=args.max_anchors), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
