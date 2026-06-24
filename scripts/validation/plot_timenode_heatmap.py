#!/usr/bin/env python3
"""Render a per-grid satellite time-node heatmap from a GEHI vintage-probe CSV.

Input CSV must have columns: grid_id, capture_date (YYYY-MM-DD). Builds a
grid x year matrix where each cell = number of distinct GEHI/TM capture dates
available at that grid's centroid in that year, and renders it as a heatmap PNG.

Census-anchored counting (--census-date): the heatmap is for BACKDATING, whose
reference image is the census/detection base map (CT = aerial 2025-01, JHB = Vexcel
2024). Only capture dates AT OR BEFORE the census node are *valid backdating
anchors* and are counted (blue). Imagery captured AFTER the census node still
exists in GEHI but cannot date a census-time detection, so it is shown muted
(grey) and excluded from the valid-node count. The per-grid valid-node total is
the depth of historical evidence available to date that cell's installations,
counted backward from the census node.

Outputs: matrix CSV (valid counts), per-grid valid-node summary CSV, heatmap PNG.

Usage:
  python scripts/validation/plot_timenode_heatmap.py \
      --input data/timenode_heatmaps/gehi_info_cpt.csv \
      --city "Cape Town" --region-code CT --census-date 2025-01-31 \
      --census-label "aerial census 2025-01" \
      --png results/timenode_heatmaps/ct_timenodes_heatmap.png \
      --matrix-csv results/timenode_heatmaps/ct_timenode_matrix.csv
"""

from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib.colors import LinearSegmentedColormap  # noqa: E402


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--input", type=Path, required=True, help="GEHI vintage-probe CSV (grid_id, capture_date).")
    p.add_argument("--city", required=True, help='Display name, e.g. "Cape Town".')
    p.add_argument("--region-code", required=True, help='Short code for file labels, e.g. "CT".')
    p.add_argument("--png", type=Path, required=True)
    p.add_argument("--matrix-csv", type=Path, required=True)
    p.add_argument("--census-date", help="Census/detection base-map date YYYY-MM-DD. Anchors the valid-node count.")
    p.add_argument("--census-label", default="census", help='Short label for the census node, e.g. "aerial 2025-01".')
    p.add_argument("--provider", default="TM")
    p.add_argument("--zoom", type=int, default=19)
    p.add_argument("--year-min", type=int, default=2009)
    p.add_argument("--year-max", type=int, default=2026)
    return p.parse_args()


def main() -> None:
    args = parse_args()
    df = pd.read_csv(args.input, dtype={"grid_id": str})
    df = df[df["capture_date"].notna()].copy()
    df["date"] = pd.to_datetime(df["capture_date"], errors="coerce")
    df = df[df["date"].notna()]
    df["year"] = df["date"].dt.year
    df = df[(df["year"] >= args.year_min) & (df["year"] <= args.year_max)]
    df = df.drop_duplicates(["grid_id", "capture_date"])  # distinct (grid, date)

    census = pd.Timestamp(args.census_date) if args.census_date else None
    if census is not None:
        df["valid"] = df["date"] <= census          # valid backdating anchor
    else:
        df["valid"] = True

    years = list(range(args.year_min, args.year_max + 1))
    grids = sorted(df["grid_id"].unique())

    def year_matrix(sub: pd.DataFrame) -> pd.DataFrame:
        c = sub.groupby(["grid_id", "year"]).size().rename("n").reset_index()
        return (
            c.pivot(index="grid_id", columns="year", values="n")
            .reindex(index=grids, columns=years)
            .fillna(0)
            .astype(int)
        )

    matrix_valid = year_matrix(df[df["valid"]])
    matrix_post = year_matrix(df[~df["valid"]])

    # valid matrix is the headline artifact
    args.matrix_csv.parent.mkdir(parents=True, exist_ok=True)
    matrix_valid.to_csv(args.matrix_csv)

    # per-grid valid-node summary (the "count backward from census" deliverable)
    valid_df = df[df["valid"]]
    summ = (
        valid_df.groupby("grid_id")
        .agg(
            n_valid_timenodes=("capture_date", "nunique"),
            earliest_date=("date", "min"),
            latest_valid_date=("date", "max"),
        )
        .reindex(grids)
    )
    summ["n_post_census_timenodes"] = (
        df[~df["valid"]].groupby("grid_id")["capture_date"].nunique().reindex(grids).fillna(0).astype(int)
    )
    summ["census_date"] = args.census_date or ""
    summ = summ.reset_index()
    summary_csv = args.matrix_csv.with_name(f"{args.region_code.lower()}_valid_timenodes_per_grid.csv")
    summ.to_csv(summary_csv, index=False)

    Mv = matrix_valid.to_numpy()
    Mp = matrix_post.to_numpy()
    n_grids = Mv.shape[0]

    # ---- summary stats ----
    valid_per_grid = matrix_valid.sum(axis=1)
    med_valid = float(np.median(valid_per_grid))
    min_valid, max_valid = int(valid_per_grid.min()), int(valid_per_grid.max())
    total_valid = int(Mv.sum())
    total_post = int(Mp.sum())
    valid_yearly_cov = (Mv > 0).mean(axis=0)
    post_yearly_cov = (Mp > 0).mean(axis=0)

    # ---- figure ----
    blue = LinearSegmentedColormap.from_list("tn", ["#f7fbff", "#9ecae1", "#4292c6", "#08519c", "#08306b"])
    grey = LinearSegmentedColormap.from_list("tg", ["#f5f5f5", "#cccccc", "#969696", "#636363"])
    vmax = np.percentile(Mv[Mv > 0], 98) if (Mv > 0).any() else 1

    fig, (ax, axb) = plt.subplots(
        2, 1, figsize=(14, 9), gridspec_kw={"height_ratios": [6, 1], "hspace": 0.30}
    )
    extent = [args.year_min - 0.5, args.year_max + 0.5, n_grids - 0.5, -0.5]

    # post-census (grey) underneath, valid (blue) on top; mask zeros so they don't paint
    ax.imshow(
        np.ma.masked_where(Mp <= 0, Mp), aspect="auto", cmap=grey, vmin=0.5,
        vmax=max(np.percentile(Mp[Mp > 0], 98) if (Mp > 0).any() else 1, 1),
        extent=extent, interpolation="nearest",
    )
    im = ax.imshow(
        np.ma.masked_where(Mv <= 0, Mv), aspect="auto", cmap=blue, vmin=0.5,
        vmax=max(vmax, 1), extent=extent, interpolation="nearest",
    )

    # census node line
    if census is not None:
        cx = census.year - 0.5 + (census.month - 0.5) / 12.0
        ax.axvline(cx, color="#d62728", lw=2.0, ls="--")
        ax.text(
            cx, -0.5, f"  census node: {args.census_label} ",
            color="#d62728", fontsize=9, fontweight="bold", va="bottom", ha="left",
        )

    ax.set_xticks(years)
    ax.set_xticklabels([str(y) for y in years], rotation=45, ha="right", fontsize=9)
    ax.set_ylabel(f"Grid cell  (n={n_grids}, sorted by grid id)", fontsize=10)
    if n_grids > 20:
        step = max(1, n_grids // 18)
        yt = list(range(0, n_grids, step))
        ax.set_yticks(yt)
        ax.set_yticklabels([grids[i] for i in yt], fontsize=6)
    else:
        ax.set_yticks(range(n_grids))
        ax.set_yticklabels(grids, fontsize=7)
    anchor_note = (
        f"  ·  blue = valid backdating anchors (≤ census) · grey = post-census imagery"
        if census is not None else ""
    )
    ax.set_title(
        f"{args.city} — per-grid satellite time-node availability for backdating\n"
        f"GEHI / Google Time Machine (provider={args.provider}, zoom={args.zoom}, centroid probe){anchor_note}",
        fontsize=12.5, fontweight="bold", loc="left",
    )
    cbar = fig.colorbar(im, ax=ax, pad=0.015, fraction=0.025, extend="max")
    cbar.set_label("# valid (≤ census) capture dates / grid / year", fontsize=9)

    # bottom marginal: valid coverage (blue) + post-census coverage (grey)
    axb.bar(years, valid_yearly_cov, color="#08519c", width=0.8, label="valid (≤ census)")
    axb.bar(years, post_yearly_cov, color="#969696", width=0.5, alpha=0.8, label="post-census")
    if census is not None:
        axb.axvline(census.year - 0.5 + (census.month - 0.5) / 12.0, color="#d62728", lw=1.5, ls="--")
    axb.set_xlim(args.year_min - 0.5, args.year_max + 0.5)
    axb.set_ylim(0, 1)
    axb.set_xticks(years)
    axb.set_xticklabels([str(y) for y in years], rotation=45, ha="right", fontsize=9)
    axb.set_ylabel("frac. grids\nw/ imagery", fontsize=8)
    axb.grid(axis="y", alpha=0.25)
    axb.legend(fontsize=7, loc="lower left", ncol=2, framealpha=0.9)
    for spine in ("top", "right"):
        axb.spines[spine].set_visible(False)

    subtitle = (
        f"{n_grids} grid cells · {total_valid:,} valid (grid×date) backdating anchors "
        f"(+{total_post:,} post-census, shown grey) · "
        f"valid time-nodes/grid: min {min_valid} / median {med_valid:.0f} / max {max_valid}.  "
        f"Cell = # distinct GEHI captures that year; counts taken backward from the census node."
    )
    fig.text(0.5, 0.005, subtitle, ha="center", fontsize=8, color="#333333", wrap=True)

    args.png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(args.png, dpi=150, bbox_inches="tight")
    plt.close(fig)

    print(f"[plot] {args.city}: {n_grids} grids x {len(years)} years; census={args.census_date}")
    print(f"[plot] valid matrix   -> {args.matrix_csv}")
    print(f"[plot] per-grid summary-> {summary_csv}")
    print(f"[plot] png            -> {args.png}")
    print(f"[plot] valid nodes/grid: min {min_valid} median {med_valid:.0f} max {max_valid}; "
          f"total valid {total_valid:,}, post-census {total_post:,}")


if __name__ == "__main__":
    main()
