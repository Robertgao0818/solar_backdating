#!/usr/bin/env python3
"""Spatial choropleth of per-grid backdating time-node availability.

Geographic complement to the time-axis heatmap: maps each task-grid cell coloured by
(1) # valid (<= census) time-nodes available — the depth of backdating evidence, and
(2) the year of the most-recent valid capture — how fresh that evidence is.

Usage:
  python scripts/validation/plot_timenode_spatial.py \
      --grid-gpkg ~/projects/ZAsolar/data/task_grid_cpt.gpkg --grid-id-col gridcell_id \
      --valid-csv results/timenode_heatmaps/ct_valid_timenodes_per_grid.csv \
      --city "Cape Town" --region-code CT \
      --png results/timenode_heatmaps/ct_timenodes_spatial.png
"""

from __future__ import annotations

import argparse
from pathlib import Path

import geopandas as gpd
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--grid-gpkg", type=Path, required=True)
    p.add_argument("--grid-id-col", required=True)
    p.add_argument("--valid-csv", type=Path, required=True)
    p.add_argument("--city", required=True)
    p.add_argument("--region-code", required=True)
    p.add_argument("--png", type=Path, required=True)
    p.add_argument("--grid-filter", help="Optional regex to filter grid ids (e.g. '^JNB\\d+$').")
    p.add_argument("--census-label", default="")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    g = gpd.read_file(args.grid_gpkg)
    if args.grid_filter:
        g = g[g[args.grid_id_col].astype(str).str.match(args.grid_filter)].copy()
    g["grid_id"] = g[args.grid_id_col].astype(str)

    v = pd.read_csv(args.valid_csv, dtype={"grid_id": str})
    v["latest_valid_year"] = pd.to_datetime(v["latest_valid_date"], errors="coerce").dt.year
    g = g.merge(v[["grid_id", "n_valid_timenodes", "latest_valid_year"]], on="grid_id", how="left")

    # reproject to Web Mercator for an equal-ish city-scale map
    g = g.to_crs(3857)

    n = len(g)
    n_missing = int(g["n_valid_timenodes"].isna().sum())

    fig, axes = plt.subplots(1, 2, figsize=(15, 8))
    panels = [
        ("n_valid_timenodes", "YlGnBu", "# valid (≤ census) time-nodes / grid", "Backdating depth"),
        ("latest_valid_year", "plasma", "year of most-recent valid capture", "Recency of valid evidence"),
    ]
    for ax, (col, cmap, clab, sub) in zip(axes, panels):
        g.plot(
            column=col, ax=ax, cmap=cmap, legend=True,
            legend_kwds={"label": clab, "shrink": 0.6, "fraction": 0.04, "pad": 0.02},
            missing_kwds={"color": "#dddddd", "label": "no data"},
            edgecolor="#ffffff", linewidth=0.05,
        )
        ax.set_title(sub, fontsize=12, fontweight="bold")
        ax.set_xticks([])
        ax.set_yticks([])
        for s in ax.spines.values():
            s.set_visible(False)
        ax.set_aspect("equal")

    census = f" · census node: {args.census_label}" if args.census_label else ""
    fig.suptitle(
        f"{args.city} — spatial distribution of satellite backdating time-nodes  "
        f"({n} grid cells{census})",
        fontsize=14, fontweight="bold", y=0.98,
    )
    med = g["n_valid_timenodes"].median()
    fig.text(
        0.5, 0.02,
        f"Left: depth of historical evidence per cell (median {med:.0f} valid time-nodes). "
        f"Right: how recent the freshest valid capture is. GEHI/TM centroid probe, z19."
        + (f"  {n_missing} cells without data shown grey." if n_missing else ""),
        ha="center", fontsize=9, color="#333333",
    )

    args.png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(args.png, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"[spatial] {args.city}: {n} cells ({n_missing} missing) -> {args.png}")
    print(f"[spatial] depth median={med:.0f}, recency year range "
          f"{int(g['latest_valid_year'].min())}-{int(g['latest_valid_year'].max())}")


if __name__ == "__main__":
    main()
