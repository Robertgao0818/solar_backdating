#!/usr/bin/env python3
"""Split a wave's grids across A3 dump hosts by candidate count.

GEHI is paced per process (REQUEST_INTERVAL), so two hosts roughly double
dump throughput. High-bandwidth hosts (cursor) also finish large --parallel
dumps faster. Grids are assigned greedily: largest remaining grid goes to the
host with the lowest load/weight so a single dense cell cannot unbalance the
split.

Does not talk to GEHI. Writes grids_<host>.txt (one grid_id per line) and
split_summary.json.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


def parse_shares(raw: str) -> dict[str, int]:
    shares: dict[str, int] = {}
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        if "=" not in part:
            raise ValueError(f"share {part!r} is not host=weight")
        host, weight_s = part.split("=", 1)
        host = host.strip()
        weight = int(weight_s)
        if not host or weight < 1:
            raise ValueError(f"invalid share {part!r}")
        shares[host] = weight
    if len(shares) < 2:
        raise ValueError("need at least two host=weight shares")
    return shares


def assign_grids(
    counts: dict[str, int],
    shares: dict[str, int],
) -> dict[str, list[str]]:
    """Greedy largest-first assignment. Ties break by host name."""
    hosts = sorted(shares)
    load = {h: 0 for h in hosts}
    assigned: dict[str, list[str]] = {h: [] for h in hosts}
    for grid, n in sorted(counts.items(), key=lambda item: (-item[1], item[0])):
        host = min(hosts, key=lambda h: (load[h] / shares[h], h))
        assigned[host].append(grid)
        load[host] += n
    for host in hosts:
        assigned[host].sort()
    return assigned


def load_grid_counts(candidates_csv: Path, anchors_csv: Path) -> dict[str, int]:
    index: dict[str, str] = {}
    with anchors_csv.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            aid = str(row.get("anchor_id", "")).strip()
            grid = str(row.get("grid_id", "")).strip() or str(
                row.get("source_grid", "")
            ).strip()
            if aid:
                index[aid] = grid
    counts: Counter[str] = Counter()
    missing = 0
    with candidates_csv.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            aid = str(row.get("anchor_id", "")).strip()
            grid = index.get(aid, "")
            if not grid:
                missing += 1
                continue
            counts[grid] += 1
    if missing:
        raise SystemExit(f"{missing} candidates have no grid_id in {anchors_csv}")
    if not counts:
        raise SystemExit(f"no grids in {candidates_csv}")
    return dict(counts)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidates-csv", type=Path, required=True)
    parser.add_argument("--anchors-csv", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument(
        "--shares",
        default="rog=1,cursor=1",
        help="host=weight list; cursor high-BW still uses weight 1 unless owner says otherwise",
    )
    args = parser.parse_args(argv)
    shares = parse_shares(args.shares)
    counts = load_grid_counts(args.candidates_csv, args.anchors_csv)
    assigned = assign_grids(counts, shares)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    summary_hosts: dict[str, dict[str, object]] = {}
    for host, grids in assigned.items():
        path = args.out_dir / f"grids_{host}.txt"
        path.write_text("".join(f"{g}\n" for g in grids), encoding="utf-8")
        n_cand = sum(counts[g] for g in grids)
        summary_hosts[host] = {
            "n_grids": len(grids),
            "n_candidates": n_cand,
            "weight": shares[host],
            "grids_file": str(path),
        }
    payload = {
        "schema_version": "ct_citywide_a3_grid_split_v1",
        "created_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "candidates_csv": str(args.candidates_csv),
        "anchors_csv": str(args.anchors_csv),
        "shares": shares,
        "n_grids_total": len(counts),
        "n_candidates_total": sum(counts.values()),
        "hosts": summary_hosts,
        "notes": (
            "Assignment is by candidate count, not grid count. "
            "Each host runs run_a3_batch_lane.py --grids <csv of its file>."
        ),
    }
    (args.out_dir / "split_summary.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(payload, indent=2, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
