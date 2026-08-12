#!/usr/bin/env python3
"""Freeze a sealed CT-11 holdout disjoint from every previously reviewed anchor.

The output contains no rendered sheets and is not reviewable by itself.  It
commits the private identities, blind order, 20% repeat set, input hashes and
owner-confirmed acceptance-config hash before a remediation method is locked.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import random
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable


SCHEMA_VERSION = 1
METHOD = "ct11_disjoint_gridmin_stratified_v1"


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def stratum(row: dict[str, str]) -> str:
    return f"{row['status']}__{row['confidence']}"


def proportional_counts(capacity: dict[str, int], n: int) -> dict[str, int]:
    """Largest-remainder allocation capped by each stratum's capacity."""
    total = sum(capacity.values())
    if n < 0 or n > total:
        raise ValueError(f"cannot allocate {n} from capacity {total}")
    if n == 0:
        return {key: 0 for key in capacity}
    raw = {key: n * value / total for key, value in capacity.items()}
    counts = {key: min(capacity[key], math.floor(raw[key])) for key in capacity}
    remaining = n - sum(counts.values())
    order = sorted(capacity, key=lambda key: (-(raw[key] - counts[key]), key))
    while remaining:
        progressed = False
        for key in order:
            if remaining == 0:
                break
            if counts[key] < capacity[key]:
                counts[key] += 1
                remaining -= 1
                progressed = True
        if not progressed:
            raise RuntimeError("allocation exhausted before reaching target")
    return counts


def deterministic_sample(rows: Iterable[dict[str, str]], n: int, salt: str) -> list[dict[str, str]]:
    pool = sorted(rows, key=lambda row: row["anchor_id"])
    if n > len(pool):
        raise ValueError(f"sample {n} exceeds pool {len(pool)}")
    return random.Random(salt).sample(pool, n)


def select_holdout(
    rows: list[dict[str, str]],
    excluded_ids: set[str],
    *,
    n: int,
    grid_min: int,
    seed: int,
) -> list[dict[str, str]]:
    eligible = [row for row in rows if row["anchor_id"] not in excluded_ids]
    if len({row["anchor_id"] for row in eligible}) != len(eligible):
        raise ValueError("eligible anchor IDs are not unique")
    by_grid: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in eligible:
        by_grid[row["grid_id"]].append(row)
    if not by_grid or any(len(pool) < grid_min for pool in by_grid.values()):
        raise ValueError("one or more grids cannot satisfy the frozen minimum")
    if n < len(by_grid) * grid_min:
        raise ValueError("holdout size is smaller than the grid-minimum requirement")

    selected: list[dict[str, str]] = []
    for grid_id in sorted(by_grid):
        selected.extend(
            deterministic_sample(
                by_grid[grid_id],
                grid_min,
                f"{seed}:grid-floor:{grid_id}",
            )
        )
    selected_ids = {row["anchor_id"] for row in selected}
    remainder_pool = [row for row in eligible if row["anchor_id"] not in selected_ids]
    by_stratum: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in remainder_pool:
        by_stratum[stratum(row)].append(row)
    counts = proportional_counts(
        {key: len(pool) for key, pool in by_stratum.items()},
        n - len(selected),
    )
    for key in sorted(counts):
        selected.extend(
            deterministic_sample(
                by_stratum[key],
                counts[key],
                f"{seed}:remainder:{key}",
            )
        )
    if len(selected) != n or len({row["anchor_id"] for row in selected}) != n:
        raise AssertionError("holdout selection did not produce n unique anchors")
    rng = random.Random(f"{seed}:blind-order")
    rng.shuffle(selected)
    return selected


def select_repeat(rows: list[dict[str, str]], *, n: int, seed: int) -> list[dict[str, str]]:
    by_stratum: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        by_stratum[stratum(row)].append(row)
    counts = proportional_counts({key: len(pool) for key, pool in by_stratum.items()}, n)
    repeat: list[dict[str, str]] = []
    for key in sorted(counts):
        repeat.extend(
            deterministic_sample(by_stratum[key], counts[key], f"{seed}:repeat:{key}")
        )
    random.Random(f"{seed}:repeat-order").shuffle(repeat)
    return repeat


def write_text_locked(path: Path, rendered: str) -> None:
    if path.exists():
        if path.read_text(encoding="utf-8") != rendered:
            raise ValueError(f"existing frozen output differs: {path}")
    else:
        path.write_text(rendered, encoding="utf-8")
    os.chmod(path, 0o444)


def write_json_locked(path: Path, payload: Any) -> None:
    write_text_locked(path, json.dumps(payload, indent=2, sort_keys=True) + "\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--intervals", required=True, type=Path)
    parser.add_argument("--exclude-manifest", required=True, action="append", type=Path)
    parser.add_argument("--acceptance-config", required=True, type=Path)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--n", type=int, default=500)
    parser.add_argument("--grid-min", type=int, default=5)
    parser.add_argument("--selection-seed", type=int, default=2026080701)
    parser.add_argument("--repeat-seed", type=int, default=2026080702)
    parser.add_argument("--repeat-fraction", type=float, default=0.20)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.n != 500 or args.grid_min != 5 or args.repeat_fraction != 0.20:
        raise SystemExit("CT-11 confirmatory design is locked to n=500, grid_min=5, repeat=20%")
    rows = read_csv(args.intervals)
    required = {"anchor_id", "grid_id", "status", "confidence"}
    if len(rows) != 21453 or not rows or not required.issubset(rows[0]):
        raise ValueError("interval population does not match the frozen CT cohort contract")
    if len({row["anchor_id"] for row in rows}) != 21453:
        raise ValueError("interval population anchor IDs are not bijective")
    grids = sorted({row["grid_id"] for row in rows})
    if len(grids) != 52:
        raise ValueError(f"expected 52 grids, got {len(grids)}")

    excluded_ids: set[str] = set()
    exclude_hashes = []
    for path in args.exclude_manifest:
        document = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(document, list):
            raise ValueError(f"exclude manifest is not a list: {path}")
        ids = {str(row["anchor_id"]) for row in document}
        excluded_ids.update(ids)
        exclude_hashes.append({"path": str(path), "sha256": sha256_file(path), "n": len(ids)})
    if not excluded_ids:
        raise ValueError("at least one prior reviewed anchor must be excluded")

    selected = select_holdout(
        rows,
        excluded_ids,
        n=args.n,
        grid_min=args.grid_min,
        seed=args.selection_seed,
    )
    repeat_n = round(args.n * args.repeat_fraction)
    repeat = select_repeat(selected, n=repeat_n, seed=args.repeat_seed)
    if excluded_ids & {row["anchor_id"] for row in selected}:
        raise AssertionError("new holdout overlaps a prior reviewed manifest")

    private_rows = [
        {
            "blind_index": index,
            "anchor_id": row["anchor_id"],
            "grid_id": row["grid_id"],
            "production_status": row["status"],
            "production_confidence": row["confidence"],
            "production_interval_start": row.get("install_interval_start", ""),
            "production_interval_end": row.get("install_interval_end", ""),
        }
        for index, row in enumerate(selected, start=1)
    ]
    blind_index = {row["anchor_id"]: row["blind_index"] for row in private_rows}
    repeat_rows = [
        {
            "repeat_index": index,
            "blind_index": blind_index[row["anchor_id"]],
            "anchor_id": row["anchor_id"],
            "grid_id": row["grid_id"],
        }
        for index, row in enumerate(repeat, start=1)
    ]

    args.output_root.mkdir(parents=True, exist_ok=True)
    private_path = args.output_root / "private_holdout_manifest.json"
    repeat_path = args.output_root / "private_repeat_manifest.json"
    write_json_locked(private_path, private_rows)
    write_json_locked(repeat_path, repeat_rows)
    grid_counts = Counter(row["grid_id"] for row in private_rows)
    stratum_counts = Counter(
        f"{row['production_status']}__{row['production_confidence']}" for row in private_rows
    )
    commitment = {
        "schema_version": SCHEMA_VERSION,
        "status": "SEALED_NOT_RENDERED_NOT_REVIEWED",
        "method": METHOD,
        "n_anchors": len(private_rows),
        "n_repeat": len(repeat_rows),
        "n_grids": len(grid_counts),
        "grid_min_realized": min(grid_counts.values()),
        "grid_counts": dict(sorted(grid_counts.items())),
        "stratum_counts": dict(sorted(stratum_counts.items())),
        "private_holdout_manifest_sha256": sha256_file(private_path),
        "private_repeat_manifest_sha256": sha256_file(repeat_path),
        "contains_rendered_sheets": False,
        "contains_review_verdicts": False,
    }
    commitment_path = args.output_root / "public_holdout_commitment.json"
    write_json_locked(commitment_path, commitment)
    lock = {
        "schema_version": SCHEMA_VERSION,
        "status": "SEALED_NOT_RENDERED_NOT_REVIEWED",
        "method": METHOD,
        "intervals": {"path": str(args.intervals), "sha256": sha256_file(args.intervals), "n": len(rows)},
        "excluded_manifests": exclude_hashes,
        "excluded_unique_anchors": len(excluded_ids),
        "acceptance_config": {
            "path": str(args.acceptance_config),
            "sha256": sha256_file(args.acceptance_config),
        },
        "selection_seed": args.selection_seed,
        "repeat_seed": args.repeat_seed,
        "n": args.n,
        "grid_min": args.grid_min,
        "repeat_fraction": args.repeat_fraction,
        "private_holdout_manifest_sha256": sha256_file(private_path),
        "private_repeat_manifest_sha256": sha256_file(repeat_path),
        "public_commitment_sha256": sha256_file(commitment_path),
        "sheets_rendered": False,
        "sheets_opened": False,
        "remediation_locked": False,
    }
    lock_path = args.output_root / "HOLDOUT_LOCK.json"
    write_json_locked(lock_path, lock)
    files = [private_path, repeat_path, commitment_path, lock_path]
    hashes_path = args.output_root / "holdout_outputs.sha256"
    write_text_locked(
        hashes_path,
        "".join(f"{sha256_file(path)}  {path.name}\n" for path in files),
    )
    print(json.dumps(commitment, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
