#!/usr/bin/env python3
"""Aggregate JHB install-date scan_state JSONs into a per-grid observed-capture-date table.

This is the SUPPLEMENTARY / cross-check view for the JHB time-node heatmap: it reports
the GEHI capture dates the adaptive install-date scan actually *observed* (downloaded &
scored) per grid. It is a subset of true availability (the adaptive scan samples, it does
not enumerate), so it is used to cross-validate the centroid `info` availability probe:
every observed date should appear in (be a subset of) the probed availability for its grid.

Output: long CSV grid_id, capture_date, n_anchors (distinct anchors in the grid that
observed that date).

Usage:
  python scripts/validation/aggregate_scan_states_timenodes.py \
      --scan-states-dir ~/zasolar_data/geid_temporal/jhb_full382_fpcut_scan_2026-06-02/scan_states \
      --output data/timenode_heatmaps/jhb_scan_observed_dates.csv
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--scan-states-dir", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    return p.parse_args()


def main() -> None:
    args = parse_args()
    files = sorted(args.scan_states_dir.glob("*.json"))
    if not files:
        raise SystemExit(f"No scan_state JSONs under {args.scan_states_dir}")

    # (grid_id, capture_date) -> set of anchor_ids
    obs: dict[tuple[str, str], set[str]] = defaultdict(set)
    n_anchors_per_grid: dict[str, set[str]] = defaultdict(set)
    for f in files:
        d = json.loads(f.read_text())
        grid = d.get("grid_id")
        anchor = d.get("anchor_id")
        if not grid:
            continue
        n_anchors_per_grid[grid].add(anchor)
        for rnd in d.get("rounds", []):
            # results = actually fetched+dated chips; picks = requested. Union both,
            # keyed on capture_date (the GEHI-reported actual date).
            for src in ("results", "picks"):
                for item in rnd.get(src, []):
                    cd = item.get("capture_date")
                    if cd:
                        obs[(grid, cd[:10])].add(anchor)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    rows = sorted(obs.items(), key=lambda kv: (kv[0][0], kv[0][1]))
    with args.output.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["grid_id", "capture_date", "n_anchors"])
        for (grid, cd), anchors in rows:
            w.writerow([grid, cd, len(anchors)])

    grids = sorted(n_anchors_per_grid)
    n_pairs = len(rows)
    print(f"[scan-agg] {len(files)} scan_states | {len(grids)} grids | {n_pairs} (grid,date) pairs -> {args.output}")
    print(f"[scan-agg] grids {grids[0]}..{grids[-1]}")


if __name__ == "__main__":
    main()
