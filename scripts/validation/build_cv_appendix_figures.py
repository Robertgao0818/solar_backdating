#!/usr/bin/env python3
"""Build CV appendix figures D5, D3 and D4 (batch v1).

One entry point, one subcommand per figure, plus the shared parcel join.
Definitions live in cv_appendix_constants.py and cv_appendix_rules.py.
Outputs stay under ~/zasolar_data and are not written to Dropbox.

    python scripts/validation/build_cv_appendix_figures.py d5
    python scripts/validation/build_cv_appendix_figures.py cells
    python scripts/validation/build_cv_appendix_figures.py phase0
    python scripts/validation/build_cv_appendix_figures.py d3
    python scripts/validation/build_cv_appendix_figures.py d4
    python scripts/validation/build_cv_appendix_figures.py assemble
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path

import h3
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import geopandas as gpd
import shapely
import statsmodels.api as sm
from matplotlib.colors import LogNorm, TwoSlopeNorm
from scipy import stats
from shapely.geometry import shape as shapely_shape

from scripts.validation.cv_appendix_constants import (
    BATCH_DATE,
    CAPACITY_KWP_PER_M2,
    CENSUS_DATE,
    CENSUS_GRID_GPKG,
    CITY_BOUNDARY_NAME,
    CITYWIDE_CSV,
    CITYWIDE_ROWS,
    CITYWIDE_SHA256,
    CITYWIDE_USECOLS,
    CLASS_COUNTS,
    CONTAINMENT_MIN_AREA_FRACTION,
    CROSSCORR_MAX_LAG,
    CUMULATIVE_MONTH_END,
    CCT_AUTHORITY,
    ESKOM_AUTHORITY,
    ESKOM_EXCLUDE_AREA_FRACTION,
    FIG_DPI,
    FIG_WIDTH_MM,
    FULL_COVERAGE_MIN_FRACTION,
    GEOBOUNDARIES_ADM2,
    H3_RESOLUTION,
    HISTOGRAM_MONTH_END,
    HISTOGRAM_MONTH_START,
    MAIN_SNAPSHOT,
    MATCH_RATE_MIN,
    MODEL_EXCLUDED,
    MODEL_LABEL,
    N_QUANTILE_BINS,
    PANEL_SNAPSHOTS,
    PARCEL_SERVER_COUNT,
    SSEG_COMMISSIONED_ROWS,
    SSEG_CSV,
    SSEG_SHA256,
    SSEG_USECOLS,
    STOCK_RULES,
    SUPPLY_QUERY_URL,
    VISITS_CSV,
    WORK_ROOT,
    constants_payload,
)
from scripts.validation.cv_appendix_rules import (
    installed_mask,
    interval_midpoint,
    is_commissioned_status,
    is_offgrid_status,
    is_residential_cell,
    is_residential_zoning,
    lagged_pearson,
    primary_zone,
    month_index,
    month_index_range,
    index_to_label,
    polygon_interval,
    quantile_bin_means,
    spread_interval,
    supply_layer_covers_city,
)

PHASE0 = WORK_ROOT / "phase0"
STAGING = WORK_ROOT / "staging_v1"
RELEASE = WORK_ROOT / "release_v1"
PARCEL_GPKG = WORK_ROOT / "parcels" / "parcels.gpkg"
FIG_WIDTH_IN = FIG_WIDTH_MM / 25.4
MONTHS = month_index_range(HISTOGRAM_MONTH_START, HISTOGRAM_MONTH_END)
CUM_END_INDEX = month_index(pd.Timestamp(CUMULATIVE_MONTH_END + "-01"))


class Stop(SystemExit):
    """A section 7.4 stop. The message is the whole report."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=_json_default) + "\n")


def _json_default(value):
    if isinstance(value, (pd.Timestamp, np.floating)):
        if pd.isna(value):
            return None
        return float(value) if isinstance(value, np.floating) else value.isoformat()
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, float) and (np.isnan(value) or np.isinf(value)):
        return None
    raise TypeError(type(value))


def require_hashes() -> dict:
    found = {
        "citywide_sha256": sha256_file(CITYWIDE_CSV),
        "sseg_sha256": sha256_file(SSEG_CSV),
    }
    if found["citywide_sha256"] != CITYWIDE_SHA256 or found["sseg_sha256"] != SSEG_SHA256:
        raise Stop(
            "STOP: input sha256 does not match the frozen files.\n"
            f"citywide got {found['citywide_sha256']} want {CITYWIDE_SHA256}\n"
            f"sseg got {found['sseg_sha256']} want {SSEG_SHA256}"
        )
    return found


def git_commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=Path(__file__).resolve().parents[2],
            text=True,
        ).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "unknown"


def apply_style() -> None:
    plt.rcParams.update(
        {
            "font.size": 8,
            "axes.labelsize": 8,
            "axes.titlesize": 9,
            "xtick.labelsize": 8,
            "ytick.labelsize": 8,
            "legend.fontsize": 8,
            "pdf.fonttype": 42,
            "ps.fonttype": 42,
            "axes.linewidth": 0.6,
            "savefig.dpi": FIG_DPI,
            "figure.facecolor": "white",
            "axes.facecolor": "white",
        }
    )


def save_figure(fig: plt.Figure, stem: Path) -> None:
    stem.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(stem.with_suffix(".png"), dpi=FIG_DPI)
    fig.savefig(stem.with_suffix(".pdf"))
    plt.close(fig)


def classify_census(df: pd.DataFrame) -> pd.DataFrame:
    """Attach kind / interval / midpoint. Checks the scalar rule on a sample."""
    status = df["date_status"]
    blank = status.isna() | status.astype(str).str.strip().eq("")
    left_flag = df["left_censored"].fillna(0).astype(int).eq(1)
    bound_flag = df["census_bound"].fillna(0).astype(int).eq(1)
    if bool((left_flag & bound_flag).any()):
        raise Stop("STOP: a census row is both left-censored and census-bounded.")
    kind = np.full(len(df), "unexpected", dtype=object)
    kind[left_flag | status.eq("done_already_present_before_geid_history")] = "left_censored"
    kind[bound_flag | status.eq("done_installed_during_census")] = "census_bound"
    kind[status.eq("done_appears")] = "dated"
    kind[blank & (kind == "unexpected")] = "undated"
    out = df.copy()
    out["kind"] = kind
    counts = {
        "done_appears": int((kind == "dated").sum()),
        "done_installed_during_census": int((kind == "census_bound").sum()),
        "done_already_present_before_geid_history": int((kind == "left_censored").sum()),
        "undated": int((kind == "undated").sum()),
    }
    if counts != CLASS_COUNTS or len(out) != CITYWIDE_ROWS:
        raise Stop(f"STOP: census class counts {counts} rows={len(out)} do not match section 8.")
    if (kind == "unexpected").any():
        raise Stop("STOP: census rows have a date_status this plan does not define.")
    if not out["census_cutoff"].astype(str).eq(CENSUS_DATE).all():
        raise Stop("STOP: census_cutoff is not 2025-01-31 on every row.")

    start = pd.Series(pd.NaT, index=out.index, dtype="datetime64[ns]")
    end = pd.Series(pd.NaT, index=out.index, dtype="datetime64[ns]")
    dated = kind == "dated"
    bound = kind == "census_bound"
    left = kind == "left_censored"
    start.loc[dated] = pd.to_datetime(out.loc[dated, "install_interval_start"])
    end.loc[dated] = pd.to_datetime(out.loc[dated, "install_interval_end"])
    start.loc[bound] = pd.to_datetime(out.loc[bound, "latest_absent_date"])
    end.loc[bound] = pd.Timestamp(CENSUS_DATE)
    end.loc[left] = pd.to_datetime(out.loc[left, "earliest_present_date"])
    out["interval_start"] = start
    out["interval_end"] = end
    mid = pd.Series(pd.NaT, index=out.index, dtype="datetime64[ns]")
    if dated.any():
        mid.loc[dated] = start.loc[dated] + (end.loc[dated] - start.loc[dated]) / 2
        mid.loc[dated] = mid.loc[dated].dt.normalize()
    out["midpoint"] = mid
    if dated.any() and (start.loc[dated].isna().any() or end.loc[dated].isna().any()):
        raise Stop("STOP: a dated polygon is missing an interval end.")
    if bool((dated & end.notna() & start.notna() & (end < start)).any()):
        raise Stop("STOP: a dated polygon has interval end before start.")

    sample = out.sample(n=min(300, len(out)), random_state=20261013)
    for row in sample.itertuples(index=False):
        scalar_kind, scalar_start, scalar_end = polygon_interval(pd.Series(row._asdict()))
        if scalar_kind != row.kind:
            raise Stop(f"STOP: vector classification disagrees with the scalar rule ({row.kind} vs {scalar_kind}).")
        if scalar_kind == "dated" and scalar_start is not None:
            if interval_midpoint(scalar_start, scalar_end) != pd.Timestamp(row.midpoint):
                raise Stop("STOP: midpoint helper disagrees with the vector midpoint.")
    published = pd.to_datetime(out.loc[dated, "install_date"]).dt.normalize()
    disagree = int((published.to_numpy() != mid.loc[dated].to_numpy()).sum())
    out.attrs["midpoint_disagreements"] = disagree
    out.attrs["class_counts"] = counts
    return out


def load_census() -> pd.DataFrame:
    df = pd.read_csv(CITYWIDE_CSV, usecols=list(CITYWIDE_USECOLS))
    df = classify_census(df)
    df["h3_r9"] = [
        h3.latlng_to_cell(float(lat), float(lon), H3_RESOLUTION)
        for lat, lon in zip(df["centroid_lat"], df["centroid_lon"])
    ]
    return df


def load_sseg() -> pd.DataFrame:
    df = pd.read_csv(SSEG_CSV, usecols=list(SSEG_USECOLS))
    df["commissioned"] = df["status"].map(is_commissioned_status)
    df["offgrid"] = df["status"].map(is_offgrid_status)
    if int(df["commissioned"].sum()) != SSEG_COMMISSIONED_ROWS:
        raise Stop(
            f"STOP: commissioned rows are {int(df['commissioned'].sum())}, "
            f"section 8 requires {SSEG_COMMISSIONED_ROWS}."
        )
    df["commission_date"] = pd.to_datetime(df["date_commissioned"], format="%d %b %Y", errors="coerce")
    failed = df["date_commissioned"].notna() & df["commission_date"].isna()
    if failed.any():
        # A second pass catches a stray format without using the rounded coordinates.
        df.loc[failed, "commission_date"] = pd.to_datetime(df.loc[failed, "date_commissioned"], errors="coerce")
    wkt = df["wkt"].fillna("").astype(str).str.strip()
    has_wkt = wkt.ne("") & wkt.str.upper().ne("POINT EMPTY")
    # Missing WKT becomes an empty point so from_wkt stays rectangular.
    # get_x / get_y raise on an empty point, so those rows stay NaN.
    geoms = shapely.from_wkt(wkt.where(has_wkt, "POINT EMPTY").tolist())
    empty = np.asarray(shapely.is_empty(geoms), dtype=bool)
    ok = has_wkt.to_numpy() & ~empty
    lon = np.full(len(df), np.nan, dtype=float)
    lat = np.full(len(df), np.nan, dtype=float)
    if ok.any():
        lon[ok] = shapely.get_x(geoms[ok])
        lat[ok] = shapely.get_y(geoms[ok])
    df["lon"] = lon
    df["lat"] = lat
    usable = has_wkt & df["lon"].between(17, 20) & df["lat"].between(-35, -33)
    swapped = has_wkt & df["lat"].between(17, 20) & df["lon"].between(-35, -33)
    if int(swapped.sum()) > 0:
        raise Stop("STOP: some SSEG WKT points look like latitude/longitude swapped. Not guessing.")
    if int(usable.sum()) < int(has_wkt.sum()):
        raise Stop("STOP: some SSEG WKT points fall outside the Cape Town longitude/latitude box.")
    cells = []
    for has, lat, lon in zip(has_wkt.tolist(), df["lat"].tolist(), df["lon"].tolist()):
        cells.append(h3.latlng_to_cell(float(lat), float(lon), H3_RESOLUTION) if has else None)
    df["h3_r9"] = cells
    both = has_wkt & df["h3_level_9"].notna()
    disagree = df.loc[both, "h3_r9"].astype(str) != df.loc[both, "h3_level_9"].astype(str)
    df.attrs["h3_compare_n"] = int(both.sum())
    df.attrs["h3_disagree_n"] = int(disagree.sum())
    df.attrs["h3_disagree_rate"] = float(disagree.mean()) if both.any() else None
    comm = df["commissioned"] & has_wkt
    comm_disagree = df.loc[comm, "h3_r9"].astype(str) != df.loc[comm, "h3_level_9"].astype(str)
    df.attrs["h3_comm_compare_n"] = int(comm.sum())
    df.attrs["h3_comm_disagree_n"] = int(comm_disagree.sum())
    df.attrs["h3_comm_disagree_rate"] = float(comm_disagree.mean()) if comm.any() else None
    df["capacity_kva"] = pd.to_numeric(df["generator_capacity_(va)"], errors="coerce") / 1000.0
    df["has_wkt"] = has_wkt
    return df


def _flow(indexes: pd.Series) -> dict[int, float]:
    counts = indexes.value_counts()
    return {int(key): float(value) for key, value in counts.items()}


def run_d5() -> dict:
    require_hashes()
    print("D5 loading census", flush=True)
    census = load_census()
    print("D5 loading sseg", flush=True)
    sseg = load_sseg()
    print("D5 loading visits", flush=True)
    visits = pd.read_csv(VISITS_CSV, usecols=["capture_date"])
    visits["capture_date"] = pd.to_datetime(visits["capture_date"])
    visit_sha = sha256_file(VISITS_CSV)

    dated = census["kind"].eq("dated")
    mid_index = census.loc[dated, "midpoint"].map(month_index)
    model_flow = _flow(mid_index)
    comm = sseg["commissioned"] & sseg["commission_date"].notna()
    sseg_index = sseg.loc[comm, "commission_date"].map(month_index)
    sseg_flow = _flow(sseg_index)
    visit_index = visits["capture_date"].map(month_index)
    imaging_flow = _flow(visit_index)
    imaging_dates = (
        visits.assign(month=visit_index, day=visits["capture_date"].dt.date)
        .groupby("month")["day"]
        .nunique()
    )

    spread: dict[int, float] = {}
    for start, end in zip(census.loc[dated, "interval_start"], census.loc[dated, "interval_end"]):
        for key, weight in spread_interval(start, end).items():
            spread[key] = spread.get(key, 0.0) + weight

    # System-level stock at each month end, for the README. Not drawn on panel C.
    stocks = {rule: [] for rule in STOCK_RULES}
    for key in MONTHS:
        year, month = divmod(key, 12)
        if month == 0:
            year, month = year - 1, 12
        # Last calendar day of the month.
        snapshot = pd.Timestamp(year, month, 1) + pd.offsets.MonthEnd(0)
        for rule in STOCK_RULES:
            stocks[rule].append(
                int(installed_mask(census["kind"], census["interval_start"], census["interval_end"], snapshot, rule).sum())
            )

    pre_model = int((mid_index < MONTHS[0]).sum())
    post_model = int((mid_index > MONTHS[-1]).sum())
    pre_sseg = int((sseg_index < MONTHS[0]).sum())
    post_sseg = int((sseg_index > MONTHS[-1]).sum())
    rows = []
    cum_model = 0.0
    cum_sseg = float(pre_sseg)
    for i, key in enumerate(MONTHS):
        label = index_to_label(key)
        model_n = model_flow.get(key, 0.0)
        sseg_n = sseg_flow.get(key, 0.0)
        cum_model += model_n
        cum_sseg += sseg_n
        in_cumulative = key <= CUM_END_INDEX
        rows.append(
            {
                "month": label,
                "model_dated_flow": model_n,
                "model_dated_cumulative": cum_model if in_cumulative else np.nan,
                "model_spread_flow": spread.get(key, 0.0),
                "sseg_flow": sseg_n,
                "sseg_cumulative": cum_sseg if in_cumulative else np.nan,
                "imaging_anchor_visits": imaging_flow.get(key, 0.0),
                "imaging_unique_dates": int(imaging_dates.get(key, 0)),
                "model_stock_midpoint": stocks["midpoint"][i],
                "model_stock_lower": stocks["lower"][i],
                "model_stock_upper": stocks["upper"][i],
            }
        )
    monthly = pd.DataFrame(rows)
    # Cross-correlation uses zeros inside each series' observed span.
    model_span = {key: model_flow.get(key, 0.0) for key in MONTHS if key <= CUM_END_INDEX}
    if len(sseg_index):
        sseg_keys = range(int(sseg_index.min()), int(sseg_index.max()) + 1)
        sseg_span = {key: sseg_flow.get(key, 0.0) for key in sseg_keys}
    else:
        sseg_span = {}
    cross = pd.DataFrame(lagged_pearson(model_span, sseg_span, CROSSCORR_MAX_LAG))
    ranked = cross.dropna(subset=["pearson"]).sort_values(["pearson", "lag_months"], ascending=[False, True])
    best = ranked.iloc[0].to_dict() if len(ranked) else {"lag_months": None, "pearson": None, "n_months": 0}

    top52 = int(census["cohort"].eq("top52").sum())
    wave = int(census["cohort"].eq("citywide_27wave").sum())
    june = monthly.loc[monthly.month == CUMULATIVE_MONTH_END].iloc[0]
    totals = {
        "n_citywide": int(len(census)),
        "n_dated": CLASS_COUNTS["done_appears"],
        "n_left_censored": CLASS_COUNTS["done_already_present_before_geid_history"],
        "n_census_bound": CLASS_COUNTS["done_installed_during_census"],
        "n_undated": CLASS_COUNTS["undated"],
        "n_top52": top52,
        "n_27wave": wave,
        "midpoint_vs_install_date_disagreements": int(census.attrs["midpoint_disagreements"]),
        "n_sseg_rows": int(len(sseg)),
        "n_commissioned": SSEG_COMMISSIONED_ROWS,
        "n_commissioned_missing_date": int((sseg["commissioned"] & sseg["commission_date"].isna()).sum()),
        "n_offgrid": int(sseg["offgrid"].sum()),
        "sseg_h3_disagree_rate_all_wkt": sseg.attrs["h3_disagree_rate"],
        "sseg_h3_disagree_n_all_wkt": sseg.attrs["h3_disagree_n"],
        "sseg_h3_compare_n_all_wkt": sseg.attrs["h3_compare_n"],
        "sseg_h3_disagree_rate_commissioned": sseg.attrs["h3_comm_disagree_rate"],
        "sseg_h3_disagree_n_commissioned": sseg.attrs["h3_comm_disagree_n"],
        "pre_2019_dated": pre_model,
        "post_window_dated": post_model,
        "pre_2019_sseg": pre_sseg,
        "post_window_sseg": post_sseg,
        "model_cumulative_2024_06": float(june.model_dated_cumulative),
        "sseg_cumulative_2024_06": float(june.sseg_cumulative),
        "model_stock_midpoint_2024_06": int(june.model_stock_midpoint),
        "model_stock_lower_2024_06": int(june.model_stock_lower),
        "model_stock_upper_2024_06": int(june.model_stock_upper),
        "crosscorr_best_lag": None if best["lag_months"] is None else int(best["lag_months"]),
        "crosscorr_best_pearson": None if best["pearson"] is None else float(best["pearson"]),
        "crosscorr_best_n": int(best["n_months"]),
        "crosscorr_lag0": None if cross.loc[0, "pearson"] is None else float(cross.loc[0, "pearson"]),
        "imaging_visits": int(len(visits)),
        "visits_sha256": visit_sha,
        "sseg_commission_min": str(sseg.loc[comm, "commission_date"].min().date()),
        "sseg_commission_max": str(sseg.loc[comm, "commission_date"].max().date()),
        "model_peak_month": monthly.loc[monthly.model_dated_flow.idxmax(), "month"],
        "model_peak_count": float(monthly.model_dated_flow.max()),
        "sseg_peak_month": monthly.loc[monthly.sseg_flow.idxmax(), "month"],
        "sseg_peak_count": float(monthly.sseg_flow.max()),
    }
    # The plotted monthly flows plus the outside counts must return the class totals.
    if abs(monthly.model_dated_flow.sum() + pre_model + post_model - totals["n_dated"]) > 1e-6:
        raise Stop("STOP: D5 model flows do not add up to the dated class.")
    if abs(monthly.sseg_flow.sum() + pre_sseg + post_sseg - comm.sum()) > 1e-6:
        raise Stop("STOP: D5 SSEG flows do not add up to commissioned rows with a date.")

    STAGING.mkdir(parents=True, exist_ok=True)
    monthly.to_csv(STAGING / "fig_D5_monthly.csv", index=False)
    cross.to_csv(STAGING / "fig_D5_crosscorr.csv", index=False)
    pd.DataFrame([totals]).to_csv(STAGING / "fig_D5_totals.csv", index=False)
    write_json(STAGING / "d5_numbers.json", totals)
    apply_style()
    plot_d5(monthly, totals, STAGING / "fig_D5_temporal")
    print("D5 wrote", STAGING, flush=True)
    return totals


def plot_d5(monthly: pd.DataFrame, totals: dict, stem: Path) -> None:
    apply_style()
    labels = monthly["month"].tolist()
    x = np.arange(len(labels))
    cum_x = x[monthly.model_dated_cumulative.notna().to_numpy()]
    # Height is free to grow. Width stays at the journal measure, and the
    # side-bar labels sit inside the axes so they are not clipped at 180 mm.
    fig = plt.figure(figsize=(FIG_WIDTH_IN, 10.6))
    gs = fig.add_gridspec(
        4, 1,
        height_ratios=[1.28, 1.12, 1.18, 0.78],
        hspace=0.78,
        left=0.16,
        right=0.86,
        top=0.96,
        bottom=0.07,
    )
    axes = [fig.add_subplot(gs[i]) for i in range(4)]
    blue = "#4C78A8"
    orange = "#F58518"
    grey = "#8d8d8d"

    ax = axes[0]
    ax.bar(x, monthly.model_dated_flow, width=0.86, color=blue, label="Dated midpoint")
    ax.bar([-2.2], [totals["n_left_censored"]], width=1.15, color=grey, label="Not a month")
    ax.bar([len(x) + 1.2], [totals["n_census_bound"]], width=1.15, color=grey)
    _month_ticks(ax, x, labels)
    ax.set_xlim(-4.0, len(x) + 2.6)
    top = max(float(totals["n_census_bound"]), float(monthly.model_dated_flow.max()), 1.0)
    ax.set_ylim(0, top * 1.20)
    ax.set_ylabel("Census polygons")
    ax.set_title("A. Model install month, dated polygons only")
    ax.text(
        -1.4,
        totals["n_left_censored"] + top * 0.02,
        "left-censored\nbefore 2019",
        va="bottom",
        ha="left",
        fontsize=8,
        color="#333333",
    )
    ax.text(
        len(x) + 0.4,
        totals["n_census_bound"] + top * 0.02,
        "census-bounded\nno month",
        va="bottom",
        ha="right",
        fontsize=8,
        color="#333333",
    )
    ax.legend(frameon=False, loc="upper left")

    ax = axes[1]
    ax.bar(x, monthly.sseg_flow, width=0.86, color=orange, label="Commission month")
    ax.bar([-2.2], [totals["pre_2019_sseg"]], width=1.15, color=grey, label="Before 2019")
    _month_ticks(ax, x, labels)
    ax.set_xlim(-4.0, len(x) + 1.2)
    sseg_top = max(float(totals["pre_2019_sseg"]), float(monthly.sseg_flow.max()), 1.0)
    ax.set_ylim(0, sseg_top * 1.18)
    ax.set_ylabel("SSEG registrations")
    ax.set_title("B. SSEG commission month (status 1.0 and 4.0)")
    ax.text(
        -1.4,
        float(totals["pre_2019_sseg"]) * 0.52,
        "before 2019",
        va="center",
        ha="left",
        fontsize=8,
        color="#333333",
    )
    ax.legend(frameon=False, loc="upper right")

    ax = axes[2]
    ax.plot(cum_x, monthly.loc[monthly.model_dated_cumulative.notna(), "model_dated_cumulative"], color=blue, lw=1.4, label="Model, dated midpoints")
    ax.set_ylabel("Model cumulative count", color=blue)
    ax.tick_params(axis="y", colors=blue)
    ax2 = ax.twinx()
    ax2.plot(cum_x, monthly.loc[monthly.sseg_cumulative.notna(), "sseg_cumulative"], color=orange, lw=1.4, label="SSEG commissions")
    ax2.set_ylabel("SSEG cumulative count", color=orange)
    ax2.tick_params(axis="y", colors=orange)
    _month_ticks(ax, x, labels)
    ax.set_xlim(-0.5, len(x) - 0.5)
    ax.set_title("C. Cumulative counts through 2024-06")
    h1, l1 = ax.get_legend_handles_labels()
    h2, l2 = ax2.get_legend_handles_labels()
    ax.legend(h1 + h2, l1 + l2, frameon=False, loc="upper left")
    ax.axvline(list(labels).index(CUMULATIVE_MONTH_END), color="#bbbbbb", lw=0.6, ls=":")

    ax = axes[3]
    ax.bar(x, monthly.imaging_anchor_visits, width=0.86, color="#c8c8c8")
    _month_ticks(ax, x, labels)
    ax.set_xlim(-0.5, len(x) - 0.5)
    ax.set_ylabel("Anchor-visits")
    ax.set_xlabel("Month")
    ax.set_title("C, continued. Imaging dates in the 27-wave stack")
    save_figure(fig, stem)


def _month_ticks(ax, x, labels: list[str]) -> None:
    step = 6
    pos = list(range(0, len(labels), step))
    ax.set_xticks([x[i] for i in pos])
    ax.set_xticklabels([labels[i] for i in pos], rotation=90)


def _cells_from_geom(geom) -> set[str]:
    if geom is None or geom.is_empty:
        return set()
    if geom.geom_type == "Polygon":
        return set(h3.geo_to_cells(geom.__geo_interface__, H3_RESOLUTION))
    if geom.geom_type == "MultiPolygon":
        found: set[str] = set()
        for part in geom.geoms:
            found.update(h3.geo_to_cells(part.__geo_interface__, H3_RESOLUTION))
        return found
    if geom.geom_type == "GeometryCollection":
        found = set()
        for part in geom.geoms:
            found.update(_cells_from_geom(part))
        return found
    raise Stop(f"STOP: census footprint geometry type {geom.geom_type} is not a polygon.")


def _cell_frame_from_union(union, supply: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    union = shapely.make_valid(union)
    print(f"cells candidates from h3 {H3_RESOLUTION}", flush=True)
    candidates = sorted(_cells_from_geom(union))
    polygons = []
    for cell in candidates:
        boundary = h3.cell_to_boundary(cell)
        polygons.append(shapely.polygons([(lon, lat) for lat, lon in boundary]))
    geoms = np.asarray(polygons, dtype=object)
    ratios = shapely.area(shapely.intersection(geoms, union)) / shapely.area(geoms)
    keep = ratios >= CONTAINMENT_MIN_AREA_FRACTION
    frame = gpd.GeoDataFrame(
        {
            "h3_r9": [candidates[i] for i in range(len(candidates)) if keep[i]],
            "coverage_area_fraction": ratios[keep],
        },
        geometry=list(geoms[keep]),
        crs="EPSG:4326",
    )
    metric = frame.to_crs(32734)
    cell_area = metric.geometry.area.to_numpy()
    fractions = {}
    for name, value in (("eskom", ESKOM_AUTHORITY), ("cct", "CCT")):
        poly = supply.loc[supply["supply_authority"].eq(value), "geometry"]
        if poly.empty:
            fractions[name] = np.zeros(len(frame))
            continue
        merged = shapely.make_valid(poly.union_all())
        merged_m = gpd.GeoSeries([merged], crs="EPSG:4326").to_crs(32734).iloc[0]
        fractions[name] = shapely.area(shapely.intersection(metric.geometry.values, merged_m)) / cell_area
    frame["eskom_area_fraction"] = fractions["eskom"]
    frame["cct_area_fraction"] = fractions["cct"]
    return frame


# Suburb centres used only to test whether the published supply polygons
# cover the metro. They are not a cell assignment.
_SUPPLY_REQUIRED_PLACES = (
    ("cape_town_cbd", 18.4241, -33.9249),
    ("bellville", 18.6292, -33.9002),
    ("wynberg", 18.4697, -34.0036),
    ("parow", 18.5900, -33.8970),
    ("durbanville", 18.6470, -33.8300),
    ("mitchells_plain", 18.6180, -34.0507),
    ("muizenberg", 18.4660, -34.1050),
    ("simons_town", 18.4330, -34.1930),
    ("hout_bay", 18.3560, -34.0450),
)
_SUPPLY_EXTRA_PLACES = (
    ("atlantis", 18.4830, -33.5660),
    ("somerset_west", 18.8430, -34.0780),
    ("khayelitsha", 18.6770, -34.0400),
)


def _authority_union(supply: gpd.GeoDataFrame, value: str):
    part = supply.loc[supply["supply_authority"].astype(str).str.strip().eq(value), "geometry"]
    if part.empty:
        return shapely.GeometryCollection()
    return shapely.make_valid(part.union_all())


def _area_km2(geom) -> float:
    if geom is None or geom.is_empty:
        return 0.0
    series = gpd.GeoSeries([geom], crs="EPSG:4326").to_crs(32734)
    return float(series.area.iloc[0] / 1e6)


def _supply_name(cct_geom, eskom_geom, lon: float, lat: float) -> str:
    point = shapely.Point(float(lon), float(lat))
    if cct_geom.covers(point):
        return "CCT"
    if eskom_geom.covers(point):
        return "Eskom"
    return "outside"


def _supply_coverage(supply: gpd.GeoDataFrame) -> dict:
    """Point and area check. Does not decide the cell sample by itself."""
    cct_geom = _authority_union(supply, CCT_AUTHORITY)
    eskom_geom = _authority_union(supply, ESKOM_AUTHORITY)
    places = []
    for name, lon, lat in _SUPPLY_REQUIRED_PLACES + _SUPPLY_EXTRA_PLACES:
        places.append(
            {
                "name": name,
                "lon": lon,
                "lat": lat,
                "required": name in {item[0] for item in _SUPPLY_REQUIRED_PLACES},
                "supply": _supply_name(cct_geom, eskom_geom, lon, lat),
            }
        )
    required = [place for place in places if place["required"]]
    sseg = load_sseg()
    commissioned = sseg["commissioned"] & sseg["has_wkt"]
    lon = sseg.loc[commissioned, "lon"].to_numpy()
    lat = sseg.loc[commissioned, "lat"].to_numpy()
    points = shapely.points(lon, lat) if len(lon) else np.array([], dtype=object)
    if len(lon):
        in_cct = np.asarray(shapely.covers(cct_geom, points), dtype=bool)
        in_eskom = np.asarray(shapely.covers(eskom_geom, points), dtype=bool) & ~in_cct
        outside = ~(in_cct | in_eskom)
    else:
        in_cct = in_eskom = outside = np.array([], dtype=bool)
    region = sseg.loc[sseg["commissioned"], "electricity_region"]
    region_text = region.fillna("").astype(str).str.strip().str.casefold()
    n_comm = int(commissioned.sum())
    n_outside = int(outside.sum())
    return {
        "cct_area_km2": _area_km2(cct_geom),
        "eskom_area_km2": _area_km2(eskom_geom),
        "places": places,
        "required_places_n": len(required),
        "required_places_outside": sum(place["supply"] == "outside" for place in required),
        "commissioned_with_wkt": n_comm,
        "commissioned_in_cct": int(in_cct.sum()),
        "commissioned_in_eskom": int(in_eskom.sum()),
        "commissioned_outside": n_outside,
        "commissioned_outside_rate": float(n_outside / n_comm) if n_comm else float("nan"),
        "electricity_region_eskom_n": int(region_text.eq("eskom").sum()),
    }


def _mark_analysis_cells(frame: gpd.GeoDataFrame, covers_city: bool) -> None:
    if covers_city:
        frame["eskom_excluded"] = frame["eskom_area_fraction"] >= ESKOM_EXCLUDE_AREA_FRACTION
    else:
        frame["eskom_excluded"] = False
    frame["in_analysis"] = ~frame["eskom_excluded"].astype(bool)


def load_supply() -> gpd.GeoDataFrame:
    PHASE0.mkdir(parents=True, exist_ok=True)
    target = PHASE0 / "supply_authority.geojson"
    try:
        form = urllib.parse.urlencode(
            {
                "where": "1=1",
                "outFields": "OBJECTID,SUPPLY_AUTHORITY",
                "returnGeometry": "true",
                "outSR": "4326",
                "f": "geojson",
            }
        ).encode()
        request = urllib.request.Request(SUPPLY_QUERY_URL, data=form, headers={"User-Agent": "solar-backdating-cv-appendix/1.0"})
        with urllib.request.urlopen(request, timeout=60) as response:
            target.write_bytes(response.read())
        source = "live ODP_SPLIT_1/MapServer/3"
    except Exception as exc:  # noqa: BLE001 — fall back to the cached municipal extract
        cache = PHASE0 / "supply_authority_raw.geojson"
        if not cache.exists():
            raise Stop(f"STOP: electricity supply layer unavailable ({exc}) and no cache.") from exc
        target.write_bytes(cache.read_bytes())
        source = f"cache after live failure: {exc}"
    supply = gpd.read_file(target)
    supply.columns = [column.lower() for column in supply.columns]
    if "supply_authority" not in supply.columns:
        raise Stop(f"STOP: supply layer has no SUPPLY_AUTHORITY column ({list(supply.columns)}).")
    supply.attrs["source"] = source
    return supply


def run_cells() -> dict:
    print("cells loading census grid", flush=True)
    grids = gpd.read_file(CENSUS_GRID_GPKG, columns=["gridcell_id", "coverage_fraction"])
    strict = grids.loc[grids["coverage_fraction"] >= FULL_COVERAGE_MIN_FRACTION]
    if strict.empty:
        raise Stop("STOP: no CPT cells meet the full-coverage fraction.")
    supply = load_supply()
    strict_union = strict.geometry.union_all()
    loose_union = grids.geometry.union_all()
    frame = _cell_frame_from_union(strict_union, supply)
    loose = _cell_frame_from_union(loose_union, supply)
    print("cells checking whether the supply layer covers the metro", flush=True)
    coverage = _supply_coverage(supply)
    covers_city = supply_layer_covers_city(
        coverage["required_places_outside"],
        coverage["commissioned_outside_rate"],
    )
    _mark_analysis_cells(frame, covers_city)
    if covers_city:
        print("cells supply layer covers the metro; applying the 0.5 Eskom rule", flush=True)
    else:
        print("cells supply layer does not cover the metro; excluding no cells", flush=True)
    city = gpd.read_file(GEOBOUNDARIES_ADM2)
    city = city.loc[city["shapeName"].eq(CITY_BOUNDARY_NAME)].copy()
    if city.empty:
        raise Stop("STOP: geoBoundaries extract has no City of Cape Town polygon.")
    PHASE0.mkdir(parents=True, exist_ok=True)
    frame.to_file(PHASE0 / "cell_frame.gpkg", layer="cells", driver="GPKG")
    city.to_file(PHASE0 / "city_boundary.gpkg", layer="city", driver="GPKG")
    qa = {
        "cpt_cells": int(len(grids)),
        "cpt_cells_full_coverage": int(len(strict)),
        "h3_inside_full_coverage": int(len(frame)),
        "h3_inside_all_cpt_cells": int(len(loose)),
        "h3_eskom_excluded": int(frame["eskom_excluded"].sum()),
        "h3_analysis": int(frame["in_analysis"].sum()),
        "h3_outside_both_supply_areas": int(
            ((frame["eskom_area_fraction"] < 0.01) & (frame["cct_area_fraction"] < 0.01)).sum()
        ),
        "supply_source": supply.attrs.get("source"),
        "supply_counts": supply["supply_authority"].value_counts().to_dict(),
        "coverage_min_fraction": FULL_COVERAGE_MIN_FRACTION,
        "containment_min_area_fraction": CONTAINMENT_MIN_AREA_FRACTION,
        "eskom_exclude_area_fraction": ESKOM_EXCLUDE_AREA_FRACTION,
        "supply_layer_covers_city": covers_city,
        "supply_exclusion_applied": covers_city,
        "supply_cct_area_km2": coverage["cct_area_km2"],
        "supply_eskom_area_km2": coverage["eskom_area_km2"],
        "supply_required_places_n": coverage["required_places_n"],
        "supply_required_places_outside": coverage["required_places_outside"],
        "supply_place_probe": coverage["places"],
        "supply_commissioned_with_wkt": coverage["commissioned_with_wkt"],
        "supply_commissioned_in_cct": coverage["commissioned_in_cct"],
        "supply_commissioned_in_eskom": coverage["commissioned_in_eskom"],
        "supply_commissioned_outside": coverage["commissioned_outside"],
        "supply_commissioned_outside_rate": coverage["commissioned_outside_rate"],
        "sseg_electricity_region_eskom_n": coverage["electricity_region_eskom_n"],
        "census_grid_sha256": sha256_file(CENSUS_GRID_GPKG),
        "supply_sha256": sha256_file(PHASE0 / "supply_authority.geojson"),
    }
    write_json(PHASE0 / "cells_qa.json", qa)
    print("cells", json.dumps(qa), flush=True)
    return qa


def _assign_parcels(lon: np.ndarray, lat: np.ndarray, parcels: gpd.GeoDataFrame) -> tuple[np.ndarray, int]:
    points = shapely.points(np.asarray(lon, dtype=float), np.asarray(lat, dtype=float))
    tree = shapely.STRtree(parcels.geometry.values)
    pairs = tree.query(points, predicate="within")
    objectids = np.full(len(lon), np.nan)
    multi = 0
    if pairs.shape[1] == 0:
        return objectids, multi
    match = pd.DataFrame({"point": pairs[0], "parcel": pairs[1]})
    areas = parcels["shape_area"].to_numpy()
    match["area"] = areas[match["parcel"].to_numpy()]
    sizes = match.groupby("point").size()
    multi = int((sizes > 1).sum())
    chosen = match.sort_values(["point", "area"], na_position="last").drop_duplicates("point", keep="first")
    ids = parcels["objectid"].to_numpy()
    objectids[chosen["point"].to_numpy()] = ids[chosen["parcel"].to_numpy()]
    return objectids, multi


def _near_count(lon, lat, parcels: gpd.GeoDataFrame, distance_deg: float = 0.00012) -> int:
    if len(lon) == 0:
        return 0
    points = shapely.points(np.asarray(lon, dtype=float), np.asarray(lat, dtype=float))
    tree = shapely.STRtree(parcels.geometry.values)
    pairs = tree.query(shapely.buffer(points, distance_deg), predicate="intersects")
    if pairs.shape[1] == 0:
        return 0
    return int(len(set(pairs[0].tolist())))


def run_phase0() -> dict:
    sentinel = WORK_ROOT / "parcels" / "parcels.gpkg.done"
    if not sentinel.exists():
        raise Stop(f"STOP: parcel GeoPackage is not ready ({sentinel} missing).")
    cells_qa_path = PHASE0 / "cells_qa.json"
    if not cells_qa_path.exists():
        raise Stop("STOP: cells_qa.json is missing. Rerun cells before phase0.")
    cells_qa_early = json.loads(cells_qa_path.read_text())
    if "supply_layer_covers_city" not in cells_qa_early:
        raise Stop("STOP: cell frame predates the supply-coverage check. Rerun cells.")
    hashes = require_hashes()
    cells_path = PHASE0 / "cell_frame.gpkg"
    if not cells_path.exists():
        run_cells()
    print("phase0 loading parcels", flush=True)
    parcels = gpd.read_file(PARCEL_GPKG)
    if len(parcels) != PARCEL_SERVER_COUNT:
        raise Stop(f"STOP: parcel gpkg has {len(parcels)} rows, server count is {PARCEL_SERVER_COUNT}.")
    if "zoning" not in parcels.columns:
        raise Stop("STOP: parcel table has no zoning column.")
    invalid = int((~parcels.geometry.is_valid).sum())
    if invalid:
        raise Stop(f"STOP: {invalid} parcel geometries are still invalid.")
    rep = parcels.geometry.representative_point()
    parcels["rep_lon"] = rep.x
    parcels["rep_lat"] = rep.y
    print("phase0 h3 on parcels", flush=True)
    parcels["h3_r9"] = [
        h3.latlng_to_cell(float(lat), float(lon), H3_RESOLUTION)
        for lat, lon in zip(parcels["rep_lat"], parcels["rep_lon"])
    ]
    parcels["primary_zone"] = parcels["zoning"].map(primary_zone)
    parcels["is_residential"] = parcels["zoning"].map(is_residential_zoning)

    print("phase0 loading points", flush=True)
    census = load_census()
    sseg = load_sseg()
    print("phase0 matching census centroids", flush=True)
    census_ids, census_multi = _assign_parcels(census["centroid_lon"], census["centroid_lat"], parcels)
    census["objectid"] = census_ids
    print("phase0 matching sseg", flush=True)
    sseg_ids, sseg_multi = _assign_parcels(sseg["lon"].fillna(0), sseg["lat"].fillna(0), parcels)
    # Points that were missing coordinates were filled with 0,0 for the call.
    # Put them back to unmatched. 0,0 is outside every Cape Town parcel.
    sseg_ids = sseg_ids.astype(float)
    sseg_ids[~sseg["has_wkt"].to_numpy()] = np.nan
    sseg["objectid"] = sseg_ids

    comm = sseg["commissioned"]
    matched = comm & sseg["objectid"].notna()
    rate = float(matched.sum() / comm.sum())
    by_source = {}
    for source, part in sseg.loc[comm].groupby(sseg.loc[comm, "geo_source"].fillna("missing")):
        by_source[str(source)] = {
            "n": int(len(part)),
            "matched": int(part["objectid"].notna().sum()),
            "with_wkt": int(part["has_wkt"].sum()),
            "match_rate": float(part["objectid"].notna().mean()),
            "match_rate_with_wkt": float(part.loc[part["has_wkt"], "objectid"].notna().mean()) if part["has_wkt"].any() else None,
        }
    with_wkt = sseg.loc[comm & sseg["has_wkt"]]
    near = _near_count(
        with_wkt.loc[with_wkt["objectid"].isna(), "lon"],
        with_wkt.loc[with_wkt["objectid"].isna(), "lat"],
        parcels,
    )
    frame = gpd.read_file(cells_path)
    if "supply_layer_covers_city" not in cells_qa_early:
        raise Stop("STOP: cell frame predates the supply-coverage check. Rerun cells.")
    if (not cells_qa_early["supply_layer_covers_city"]) and bool(frame["eskom_excluded"].astype(bool).any()):
        raise Stop(
            "STOP: the supply layer does not cover the metro, but this cell frame still "
            "excludes Eskom-majority cells. Rerun cells."
        )
    analysis = set(frame.loc[frame["in_analysis"].astype(bool), "h3_r9"])
    share = parcels.groupby("h3_r9")["is_residential"].mean()
    n_parcels = parcels.groupby("h3_r9").size()
    cell_table = frame[["h3_r9", "eskom_area_fraction", "cct_area_fraction", "eskom_excluded", "in_analysis", "coverage_area_fraction"]].copy()
    cell_table["residential_share"] = cell_table["h3_r9"].map(share)
    cell_table["n_parcels"] = cell_table["h3_r9"].map(n_parcels).fillna(0).astype(int)
    cell_table["is_residential_cell"] = cell_table["residential_share"].map(is_residential_cell)

    qa = {
        "input_hashes": hashes,
        "parcel_rows": int(len(parcels)),
        "parcel_invalid": invalid,
        "sg26_duplicate_codes": int(parcels["sg26_code"].duplicated().sum()),
        "census_matched": int(census["objectid"].notna().sum()),
        "census_match_rate": float(census["objectid"].notna().mean()),
        "census_multi_parcel": census_multi,
        "sseg_commissioned": int(comm.sum()),
        "sseg_commissioned_matched": int(matched.sum()),
        "sseg_commissioned_match_rate": rate,
        "sseg_commissioned_with_wkt": int(with_wkt.shape[0]),
        "sseg_commissioned_with_wkt_match_rate": float(with_wkt["objectid"].notna().mean()),
        "sseg_match_by_geo_source": by_source,
        "sseg_unmatched_with_wkt_inside_approx_10m": near,
        "sseg_multi_parcel": sseg_multi,
        "sseg_h3_disagree_rate_commissioned": sseg.attrs["h3_comm_disagree_rate"],
        "sseg_h3_disagree_n_commissioned": sseg.attrs["h3_comm_disagree_n"],
        "analysis_cells": int(len(analysis)),
    }
    h3_of = parcels.set_index("objectid")["h3_r9"]
    census_h3 = census["objectid"].map(h3_of)
    qa["census_matched_outside_analysis"] = int((census["objectid"].notna() & ~census_h3.isin(analysis)).sum())
    sseg_h3 = sseg["objectid"].map(h3_of)
    qa["sseg_matched_outside_analysis"] = int((matched & ~sseg_h3.isin(analysis)).sum())
    write_json(PHASE0 / "match_qa.json", qa)

    parcel_out = parcels.drop(columns="geometry")[
        ["objectid", "sg26_code", "zoning", "primary_zone", "is_residential", "h3_r9", "rep_lon", "rep_lat", "shape_area"]
    ]
    parcel_out.to_parquet(PHASE0 / "parcel_attr.parquet", index=False)
    cell_table.to_parquet(PHASE0 / "cell_attr.parquet", index=False)
    census[
        [
            "source_feature_id",
            "source_grid",
            "cohort",
            "area_m2",
            "kind",
            "interval_start",
            "interval_end",
            "midpoint",
            "h3_r9",
            "objectid",
            "centroid_lon",
            "centroid_lat",
        ]
    ].to_parquet(PHASE0 / "census_links.parquet", index=False)
    sseg_out = sseg[
        [
            "commissioned",
            "offgrid",
            "commission_date",
            "capacity_kva",
            "geo_source",
            "electricity_region",
            "has_wkt",
            "h3_r9",
            "objectid",
        ]
    ].copy()
    sseg_out.to_parquet(PHASE0 / "sseg_links.parquet", index=False)
    print("phase0 match rate", rate, flush=True)
    if rate < MATCH_RATE_MIN:
        raise Stop(
            "STOP: SSEG commissioned parcel match rate is below 90%.\n"
            f"matched {int(matched.sum())} / {int(comm.sum())} = {rate:.4f}.\n"
            f"By geo_source: {json.dumps(by_source)}.\n"
            f"Unmatched points with WKT that fall within about 10 m of a parcel: {near}.\n"
            "Figures D3 and D4 were not drawn. No buffer was applied."
        )
    return qa


def build_stock_tables() -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    census = pd.read_parquet(PHASE0 / "census_links.parquet")
    sseg = pd.read_parquet(PHASE0 / "sseg_links.parquet")
    parcels = pd.read_parquet(PHASE0 / "parcel_attr.parquet")
    cells = pd.read_parquet(PHASE0 / "cell_attr.parquet")
    analysis = cells.loc[cells["in_analysis"]].copy()
    h3_of = parcels.set_index("objectid")["h3_r9"]
    frame_geom = gpd.read_file(PHASE0 / "cell_frame.gpkg")
    wkt = frame_geom.set_index("h3_r9").geometry.apply(lambda geom: geom.wkt)
    analysis["cell_wkt"] = analysis["h3_r9"].map(wkt)

    panel_rows = []
    for snapshot_text in PANEL_SNAPSHOTS:
        snapshot = pd.Timestamp(snapshot_text)
        for rule in STOCK_RULES:
            mask = installed_mask(census["kind"], census["interval_start"], census["interval_end"], snapshot, rule)
            hit = census.loc[mask & census["objectid"].notna(), ["objectid", "area_m2"]]
            parcel_area = hit.groupby("objectid")["area_m2"].sum()
            parcel_h3 = parcel_area.index.to_series().map(h3_of)
            counts = parcel_h3.value_counts()
            capacity = parcel_area.groupby(parcel_h3).sum() * CAPACITY_KWP_PER_M2
            analysis[f"model_parcels_{rule}"] = analysis["h3_r9"].map(counts).fillna(0)
            analysis[f"model_kwp_{rule}"] = analysis["h3_r9"].map(capacity).fillna(0)
        for label, selector in (
            ("sseg", sseg["commissioned"]),
            ("sseg_plus_offgrid", sseg["commissioned"] | sseg["offgrid"]),
        ):
            part = sseg.loc[selector & sseg["commission_date"].notna() & (sseg["commission_date"] <= snapshot) & sseg["objectid"].notna()].copy()
            part["cell"] = part["objectid"].map(h3_of)
            counts = part.drop_duplicates("objectid")["cell"].value_counts()
            capacity = part.groupby("cell")["capacity_kva"].sum()
            analysis[f"{label}_parcels"] = analysis["h3_r9"].map(counts).fillna(0)
            analysis[f"{label}_kva"] = analysis["h3_r9"].map(capacity).fillna(0)
        piece = analysis[
            [
                "h3_r9",
                "model_parcels_midpoint",
                "model_parcels_lower",
                "model_parcels_upper",
                "model_kwp_midpoint",
                "model_kwp_lower",
                "model_kwp_upper",
                "sseg_parcels",
                "sseg_kva",
                "sseg_plus_offgrid_parcels",
                "sseg_plus_offgrid_kva",
            ]
        ].copy()
        piece.insert(1, "snapshot", snapshot_text)
        panel_rows.append(piece)
        print("stock", snapshot_text, flush=True)
    panel = pd.concat(panel_rows, ignore_index=True)
    main = panel.loc[panel["snapshot"].eq(MAIN_SNAPSHOT)].drop(columns="snapshot")
    cells_main = analysis.drop(
        columns=[column for column in analysis.columns if column.startswith("model_") or column.startswith("sseg_")]
    ).merge(main, on="h3_r9", how="left")
    return cells_main, panel, {"n_analysis": int(len(cells_main))}


def fit_ols(x: np.ndarray, y: np.ndarray, groups: pd.Series | None = None) -> dict:
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    design = sm.add_constant(x)
    if groups is not None:
        dummies = pd.get_dummies(groups, drop_first=True, dtype=float)
        if len(dummies.columns):
            design = np.column_stack([design, dummies.to_numpy()])
    if np.nanstd(x) == 0 or len(x) < 3:
        return {"n": int(len(x)), "slope": None, "se": None, "intercept": None, "r2": None, "pearson": None, "spearman": None}
    fit = sm.OLS(y, design, missing="drop").fit(cov_type="HC1")
    pearson = float(stats.pearsonr(x, y).statistic)
    spearman = float(stats.spearmanr(x, y).statistic)
    return {
        "n": int(fit.nobs),
        "slope": float(fit.params[1]),
        "se": float(fit.bse[1]),
        "intercept": float(fit.params[0]),
        "r2": float(fit.rsquared),
        "pearson": pearson,
        "spearman": spearman,
        "zero_share": float(np.mean((x == 0) & (y == 0))),
    }


def run_d3() -> dict:
    cells, panel, _ = build_stock_tables()
    specs = []
    main_x = cells["model_parcels_midpoint"].to_numpy()
    main_y = cells["sseg_parcels"].to_numpy()
    specs.append({"spec": "counts_main", "panel": "A", **fit_ols(main_x, main_y)})
    specs.append(
        {
            "spec": "capacity_main",
            "panel": "B",
            **fit_ols(cells["model_kwp_midpoint"].to_numpy(), cells["sseg_kva"].to_numpy()),
        }
    )
    keep = ~((cells["model_parcels_midpoint"] == 0) & (cells["sseg_parcels"] == 0))
    specs.append({"spec": "counts_drop_zero_zero", "panel": "A", **fit_ols(main_x[keep], main_y[keep])})
    cap_keep = ~((cells["model_kwp_midpoint"] == 0) & (cells["sseg_kva"] == 0))
    specs.append(
        {
            "spec": "capacity_drop_zero_zero",
            "panel": "B",
            **fit_ols(cells.loc[cap_keep, "model_kwp_midpoint"], cells.loc[cap_keep, "sseg_kva"]),
        }
    )
    residential = cells["is_residential_cell"]
    specs.append(
        {
            "spec": "counts_residential_cells",
            "panel": "A",
            **fit_ols(cells.loc[residential, "model_parcels_midpoint"], cells.loc[residential, "sseg_parcels"]),
        }
    )
    specs.append(
        {
            "spec": "counts_plus_offgrid",
            "panel": "A",
            **fit_ols(cells["model_parcels_midpoint"], cells["sseg_plus_offgrid_parcels"]),
        }
    )
    for rule, column in (("lower", "model_parcels_lower"), ("upper", "model_parcels_upper")):
        specs.append({"spec": f"counts_{rule}", "panel": "A", **fit_ols(cells[column], cells["sseg_parcels"])})
    specs.append(
        {
            "spec": "counts_panel_snapshot_fe",
            "panel": "A",
            **fit_ols(panel["model_parcels_midpoint"], panel["sseg_parcels"], panel["snapshot"]),
        }
    )
    specs.append(
        {
            "spec": "capacity_panel_snapshot_fe",
            "panel": "B",
            **fit_ols(panel["model_kwp_midpoint"], panel["sseg_kva"], panel["snapshot"]),
        }
    )
    ols = pd.DataFrame(specs)
    bins_a = quantile_bin_means(main_x, main_y, N_QUANTILE_BINS)
    bins_a.insert(0, "panel", "A")
    bins_b = quantile_bin_means(cells["model_kwp_midpoint"].to_numpy(), cells["sseg_kva"].to_numpy(), N_QUANTILE_BINS)
    bins_b.insert(0, "panel", "B")
    bins = pd.concat([bins_a, bins_b], ignore_index=True)
    # Recompute the drawn slope from the cell table that will ship.
    redraw = fit_ols(cells["model_parcels_midpoint"], cells["sseg_parcels"])
    if abs(redraw["slope"] - specs[0]["slope"]) > 1e-9:
        raise Stop("STOP: D3 annotation slope does not match the cell CSV.")
    STAGING.mkdir(parents=True, exist_ok=True)
    cells.to_csv(STAGING / "fig_D3_cells.csv", index=False)
    panel.to_csv(STAGING / "fig_D3_panel.csv", index=False)
    ols.to_csv(STAGING / "fig_D3_ols.csv", index=False)
    bins.to_csv(STAGING / "fig_D3_bins.csv", index=False)
    numbers = {
        "n_cells": int(len(cells)),
        "n_residential_cells": int(residential.sum()),
        "counts": specs[0],
        "capacity": specs[1],
        "n_bins_counts": int((bins.panel == "A").sum()),
        "n_bins_capacity": int((bins.panel == "B").sum()),
        "model_parcels_sum": float(cells.model_parcels_midpoint.sum()),
        "sseg_parcels_sum": float(cells.sseg_parcels.sum()),
        "model_kwp_sum": float(cells.model_kwp_midpoint.sum()),
        "sseg_kva_sum": float(cells.sseg_kva.sum()),
    }
    write_json(STAGING / "d3_numbers.json", numbers)
    apply_style()
    plot_d3(cells, bins, numbers, STAGING / "fig_D3_hex_scatter")
    print("D3 wrote", numbers["counts"], flush=True)
    return numbers


def _annotate(ax, text: str) -> None:
    ax.text(
        0.98,
        0.02,
        text,
        transform=ax.transAxes,
        ha="right",
        va="bottom",
        fontsize=8,
        bbox={"boxstyle": "round,pad=0.3", "facecolor": "white", "edgecolor": "#dddddd", "alpha": 0.92},
    )


def _ols_note(spec: dict) -> str:
    return (
        f"slope {spec['slope']:.4g} (SE {spec['se']:.4g})\n"
        f"R² {spec['r2']:.3f}    N {spec['n']:,}\n"
        f"both zero {spec['zero_share']:.1%}"
    )


def plot_d3(cells: pd.DataFrame, bins: pd.DataFrame, numbers: dict, stem: Path) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(FIG_WIDTH_IN, 3.55))
    pairs = [
        (axes[0], "model_parcels_midpoint", "sseg_parcels", "A. Parcel counts", "Model parcels with PV", "SSEG parcels with PV", "A", numbers["counts"]),
        (axes[1], "model_kwp_midpoint", "sseg_kva", "B. Capacity", "Model capacity (kWp)", "SSEG capacity (kVA)", "B", numbers["capacity"]),
    ]
    for ax, xcol, ycol, title, xlabel, ylabel, panel, spec in pairs:
        ax.scatter(
            cells[xcol],
            cells[ycol],
            s=9,
            facecolors="none",
            edgecolors="#4C78A8",
            linewidths=0.35,
            alpha=0.28,
            label="H3-9 cell",
        )
        part = bins.loc[bins.panel == panel]
        ax.scatter(part.mean_x, part.mean_y, s=22, c="black", marker="s", zorder=3, label="Bin mean")
        xs = np.linspace(0, float(cells[xcol].max()), 50)
        ax.plot(xs, spec["intercept"] + spec["slope"] * xs, color="#222222", lw=1, label="OLS")
        hi = min(ax.get_xlim()[1], ax.get_ylim()[1])
        ax.plot([0, hi], [0, hi], ls="--", color="#888888", lw=0.8, label="45°")
        ax.set_title(title)
        ax.set_xlabel(xlabel)
        ax.set_ylabel(ylabel)
        _annotate(ax, _ols_note(spec))
        ax.legend(frameon=False, loc="upper left", markerscale=1.2)
    fig.tight_layout()
    # tight_layout can change the saved width. Reset to the journal width
    # after the labels have claimed their margin, then save at that size.
    fig.set_size_inches(FIG_WIDTH_IN, 3.55)
    save_figure(fig, stem)


def run_d4() -> dict:
    cells = pd.read_csv(STAGING / "fig_D3_cells.csv")
    geoms = gpd.GeoSeries.from_wkt(cells["cell_wkt"], crs="EPSG:4326")
    frame = gpd.GeoDataFrame(cells, geometry=geoms, crs="EPSG:4326")
    city = gpd.read_file(PHASE0 / "city_boundary.gpkg")
    vmax = float(max(frame["model_parcels_midpoint"].max(), frame["sseg_parcels"].max(), 1))
    ratio = np.log((1 + frame["model_parcels_midpoint"]) / (1 + frame["sseg_parcels"]))
    frame["log_ratio"] = ratio
    finite = np.abs(ratio.to_numpy())
    clip = float(np.quantile(finite, 0.99))
    clip = max(clip, 0.1)
    cells_qa = json.loads((PHASE0 / "cells_qa.json").read_text())
    numbers = {
        "vmax_parcels": vmax,
        "log_ratio_clip": clip,
        "log_ratio_min": float(ratio.min()),
        "log_ratio_max": float(ratio.max()),
        "n_cells": int(len(frame)),
        "supply_layer_covers_city": bool(cells_qa["supply_layer_covers_city"]),
    }
    frame.drop(columns="geometry").to_csv(STAGING / "fig_D4_cells.csv", index=False)
    write_json(STAGING / "d4_numbers.json", numbers)
    apply_style()
    plot_d4(frame, city, numbers, STAGING / "fig_D4_hex_maps")
    print("D4 wrote", numbers, flush=True)
    return numbers


def _scalebar(ax, kilometers: float = 10) -> None:
    x0, x1 = ax.get_xlim()
    y0, y1 = ax.get_ylim()
    lat = (y0 + y1) / 2
    width = kilometers / (111.32 * np.cos(np.deg2rad(lat)))
    x = x0 + 0.08 * (x1 - x0)
    y = y0 + 0.07 * (y1 - y0)
    ax.plot([x, x + width], [y, y], color="black", lw=1.2, solid_capstyle="butt", zorder=5)
    ax.text(x + width / 2, y + 0.015 * (y1 - y0), f"{kilometers:.0f} km", ha="center", va="bottom", fontsize=8, zorder=5)


def plot_d4(frame: gpd.GeoDataFrame, city: gpd.GeoDataFrame, numbers: dict, stem: Path) -> None:
    fig, axes = plt.subplots(3, 1, figsize=(FIG_WIDTH_IN, 9.3))
    norm = LogNorm(vmin=1, vmax=numbers["vmax_parcels"])
    ratio_norm = TwoSlopeNorm(vmin=-numbers["log_ratio_clip"], vcenter=0, vmax=numbers["log_ratio_clip"])
    panels = [
        ("model_parcels_midpoint", "A. Model parcels with PV", norm, "viridis", "Parcels"),
        ("sseg_parcels", "B. SSEG parcels with PV", norm, "viridis", "Parcels"),
        ("log_ratio", "C. log((1 + model) / (1 + SSEG))", ratio_norm, "RdBu_r", "Log ratio"),
    ]
    bounds = frame.total_bounds
    pad_x = (bounds[2] - bounds[0]) * 0.03
    pad_y = (bounds[3] - bounds[1]) * 0.03
    for ax, (column, title, color_norm, cmap, label) in zip(axes, panels):
        city.plot(ax=ax, facecolor="#f4f4f4", edgecolor="#666666", linewidth=0.4, zorder=0)
        frame.plot(ax=ax, color="#e8e8e8", linewidth=0, zorder=1)
        drawn = frame if column == "log_ratio" else frame.loc[frame[column] > 0]
        drawn.plot(
            ax=ax,
            column=column,
            cmap=cmap,
            norm=color_norm,
            linewidth=0,
            zorder=2,
            legend=False,
        )
        ax.set_xlim(bounds[0] - pad_x, bounds[2] + pad_x)
        ax.set_ylim(bounds[1] - pad_y, bounds[3] + pad_y)
        ax.set_aspect("equal", adjustable="box")
        if column == "sseg_parcels" and not numbers.get("supply_layer_covers_city", True):
            title = "B. SSEG parcels (grey = zero; not an Eskom mask)"
        ax.set_title(title)
        ax.set_xlabel("Longitude (degrees)")
        ax.set_ylabel("Latitude (degrees)")
        _scalebar(ax)
        smap = plt.cm.ScalarMappable(norm=color_norm, cmap=cmap)
        fig.colorbar(smap, ax=ax, fraction=0.035, pad=0.02, label=label)
    fig.tight_layout()
    fig.set_size_inches(FIG_WIDTH_IN, 9.3)
    save_figure(fig, stem)


def _fmt(value, digits=3):
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return f"{value:,}"


def _scope_text(cells_qa: dict) -> dict[str, str]:
    """English sentences that state whether Eskom cells were dropped."""
    if "supply_layer_covers_city" not in cells_qa:
        raise Stop("STOP: cells_qa has no supply-coverage decision. Rerun cells.")
    n_hex = cells_qa["h3_inside_full_coverage"]
    n_loose = cells_qa["h3_inside_all_cpt_cells"]
    if cells_qa["supply_layer_covers_city"]:
        return {
            "gap": (
                "Eskom direct-supply cells are already left out of D3 and D4, "
                "because the register does not cover them."
            ),
            "cells": (
                f"Cells are H3-9 hexes that lie fully inside the CPT census cells whose January 2025 "
                f"aerial coverage fraction is at least {FULL_COVERAGE_MIN_FRACTION}. That is {n_hex:,} hexes, "
                f"of which {cells_qa['h3_eskom_excluded']:,} are excluded because at least half of the hex "
                f"lies in an Eskom supply polygon (`ODP_SPLIT_1` layer 3, field `SUPPLY_AUTHORITY`). "
                f"{cells_qa['h3_analysis']:,} hexes remain. Using every CPT census cell, including partly "
                f"imaged edge cells, would have produced {n_loose:,} fully contained hexes before the Eskom cut."
            ),
            "limits": (
                "Eskom direct supply is excluded as above. Cells inside the City supply area can still "
                "show many unregistered systems. Contiguous low-SSEG areas that remain are not, by "
                "themselves, evidence of a missed Eskom pocket."
            ),
            "caption_d3": (
                "H3 resolution-9 cells fully inside the January 2025 census aerial footprint, "
                "excluding cells with at least half their area in an Eskom supply polygon"
            ),
            "caption_d4": "",
            "deviation": (
                "Eskom exclusion is an area share of at least 0.5 inside a supply-authority "
                "layer that the coverage probe accepted as covering the metro."
            ),
        }
    return {
        "gap": (
            "The published supply-authority polygons do not cover the metro, so Eskom "
            "direct-supply cells could not be removed. A cell with no SSEG registration can be "
            "either an Eskom pocket or an unregistered system inside the City supply area."
        ),
        "cells": (
            f"Cells are H3-9 hexes that lie fully inside the CPT census cells whose January 2025 "
            f"aerial coverage fraction is at least {FULL_COVERAGE_MIN_FRACTION}. That is {n_hex:,} hexes, "
            f"and every one of them is kept. The City's Electricity Supply Authority layer "
            f"(`ODP_SPLIT_1` layer 3, field `SUPPLY_AUTHORITY`) was checked as the Eskom boundary. "
            f"It contains about {cells_qa['supply_cct_area_km2']:.0f} km² labelled CCT and "
            f"{cells_qa['supply_eskom_area_km2']:.0f} km² labelled Eskom, but it does not cover the metro. "
            f"{cells_qa['supply_required_places_outside']} of {cells_qa['supply_required_places_n']} named "
            f"city locations, including the CBD, Bellville, Wynberg, Parow, Durbanville, Mitchells Plain, "
            f"Muizenberg, Simon's Town and Hout Bay, fall in neither polygon. Of commissioned SSEG rows "
            f"with coordinates, {cells_qa['supply_commissioned_outside']:,} of "
            f"{cells_qa['supply_commissioned_with_wkt']:,} fall in neither polygon. Excluding hexes that "
            f"overlap the Eskom-labelled polygons would drop the wrong area, so the 0.5 area rule is not "
            f"applied. Of these hexes, {cells_qa['h3_outside_both_supply_areas']:,} have essentially no "
            f"overlap with either published polygon. Grey cells on the SSEG map are zero registrations. "
            f"A grey region cannot be separated into an Eskom direct-supply pocket and unregistered "
            f"systems inside the City supply area. The SSEG column `electricity_region` equals Eskom on "
            f"{cells_qa['sseg_electricity_region_eskom_n']:,} commissioned rows, which is not a cell "
            f"boundary. Using every CPT census cell, including partly imaged edge cells, would have "
            f"produced {n_loose:,} fully contained hexes."
        ),
        "limits": (
            "No Eskom mask is applied, for the reason above. Grey cells on Figure D4 are zero "
            "SSEG registrations, not a supply-authority boundary. Contiguous grey areas cannot, "
            "by themselves, be read as missed Eskom pockets."
        ),
        "caption_d3": (
            "H3 resolution-9 cells fully inside the January 2025 census aerial footprint. "
            "No cell is dropped for Eskom supply, because the published supply-authority "
            "polygons do not cover the metro"
        ),
        "caption_d4": (
            " Grey cells are zero SSEG registrations and are not an Eskom mask: the published "
            "supply-authority layer leaves most of the metro, including the CBD, outside both "
            "its CCT and Eskom polygons, so a grey region cannot be read as direct Eskom supply."
        ),
        "deviation": (
            "ODP_SPLIT_1 layer 3 does not cover the metro: required city locations and most "
            "commissioned SSEG points fall outside both the CCT and Eskom polygons. Section 6 "
            "fallback: no cell is dropped. Grey zero-SSEG cells on D4 are not an Eskom mask. "
            "The 0.5 area rule stays in the code and is not applied."
        ),
    }


def write_release_text(d5: dict, d3: dict, d4: dict, match: dict, cells_qa: dict) -> tuple[str, str]:
    counts = d3["counts"]
    capacity = d3["capacity"]
    scope = _scope_text(cells_qa)
    readme = f"""# Cape Town CV appendix figures, batch v1 ({BATCH_DATE})

This folder has three figures: D5 (timing), D3 (cell-level scatter) and D4 (cell-level maps).
It is a draft for comment. It is not a detection-accuracy result and it is not an
install-date accuracy result.

The figures follow the redesign agreed on 2026-10-13. Cape Town has one census
detection, on the January 2025 municipal aerial, plus a satellite backdating
interval for each detected polygon. The figures do not come from detecting panels
again on every historical aerial year.

## Please confirm four defaults

These were not restated by the collaborator note, so they are marked as defaults.

1. The counting unit is a cadastral parcel that contains PV, not a polygon and not a building.
2. The stock snapshot is 2024-06-30. The SSEG commission dates in this extract end on {d5['sseg_commission_max']}, so a later snapshot would make the register look even smaller.
3. Model capacity is census-polygon area times {CAPACITY_KWP_PER_M2:.2f} kWp per square metre. A density near four hundredths of a kilowatt per square metre was fit to SSEG itself and is not used. Polygon area is not module area, so the slope is not a yield.
4. National load-shedding hours are not drawn. They were not in the files used here. Panel C can take that series later without changing the other layers.

## Why the model stock is much larger than the SSEG stock

The census table has {d5['n_citywide']:,} polygons. Commissioned SSEG rows number {d5['n_commissioned']:,}. At the snapshot, the mapped cells contain {d3['model_parcels_sum']:,.0f} model parcels and {d3['sseg_parcels_sum']:,.0f} SSEG parcels. Most of that gap is systems that were never commissioned in the municipal register. It should not be read as a detection-error rate. {scope['gap']}

## What each figure uses

The model is {MODEL_LABEL}. {MODEL_EXCLUDED}.

A dated polygon has a closed interval from the last absent satellite visit to the first present visit. The histogram uses the midpoint of that interval. {d5['n_left_censored']:,} polygons were already present before the 2019 satellite window (left-censored). {d5['n_census_bound']:,} are known only to have appeared by the census date {CENSUS_DATE} (census-bounded). {d5['n_undated']:,} are undated. The two censored classes are drawn beside the monthly axis and are not placed in a month. Putting either class on a single month, or treating the published sentinel dates as install months, creates a false spike.

SSEG uses status values that start with `1.0 ` or `4.0 ` ({d5['n_commissioned']:,} rows). Coordinates come from the `wkt` column. The rounded latitude and longitude columns are not used. Recomputing H3-9 from `wkt` disagrees with the stored `h3_level_9` on {d5['sseg_h3_disagree_n_commissioned']:,} commissioned rows that have coordinates (rate {d5['sseg_h3_disagree_rate_commissioned']:.4f}). The recomputed cell is the one used.

D3 and D4 count parcels. A census polygon contributes to a parcel when its centroid falls inside the parcel. An SSEG row contributes when its point falls inside the parcel. The parcel is then placed in the H3-9 cell of a point that lies inside the parcel. A parcel counts as installed when at least one of its polygons is installed. The main rule for an interval that crosses the snapshot is the midpoint. The lower rule requires the interval to have ended, and the upper rule requires it to have started. Left-censored polygons count as installed at every snapshot. Undated polygons never count. Census-bounded polygons use the interval from the last absent date to {CENSUS_DATE}.

{scope['cells']}

## Figure notes

D5 panel C cumulates the monthly bars in panels A and B and stops at 2024-06. The model curve therefore omits the left-censored and census-bounded classes. The SSEG curve includes {d5['pre_2019_sseg']:,} commissions from before 2019 in its level. At 2024-06 the dated-model cumulative is {d5['model_cumulative_2024_06']:,.0f} and the SSEG cumulative is {d5['sseg_cumulative_2024_06']:,.0f}. The system-level midpoint stock, which does include the censored classes under the stock rule, is {d5['model_stock_midpoint_2024_06']:,} at the same date (lower {d5['model_stock_lower_2024_06']:,}, upper {d5['model_stock_upper_2024_06']:,}). Those stock columns are in the monthly CSV and are not extra lines on the figure.

The imaging strip is the count of anchor-visits by capture month in the 27-wave file ({d5['imaging_visits']:,} visits). The Top-52 cohort ({d5['n_top52']:,} polygons) has no visit sequence, so it is not in that strip.

Monthly new counts, not the cumulative curves, give a Pearson correlation of {_fmt(d5['crosscorr_lag0'])} at lag 0. Shifting SSEG later by 0 to 12 months, the highest correlation is {_fmt(d5['crosscorr_best_pearson'])} at {d5['crosscorr_best_lag']} months (n = {d5['crosscorr_best_n']} months). This is a description of the two flow series. It is not a pass/fail threshold. Spreading each dated interval uniformly across the months it covers, instead of placing it on the midpoint, is the column `model_spread_flow`. It is not drawn.

D3 draws one point per analysis cell. The fit is OLS with an intercept and HC1 robust standard errors. For counts the slope is {_fmt(counts['slope'], 4)} (SE {_fmt(counts['se'], 4)}), R² {_fmt(counts['r2'])}, N {counts['n']:,}, and {counts['zero_share']:.1%} of cells are zero on both axes. For capacity the slope is {_fmt(capacity['slope'], 4)} (SE {_fmt(capacity['se'], 4)}), R² {_fmt(capacity['r2'])}. The black squares are bin means: a bin for cells with a zero model value, then up to {N_QUANTILE_BINS} quantile bins of the remaining cells. The dashed line is 45 degrees. The same CSV supports the checks that are not drawn: dropping zero-zero cells, keeping cells whose parcels are mostly residential, adding off-grid SSEG status `2.0`, using the lower and upper stock rules, and a stacked snapshot panel with snapshot fixed effects. Those fits are in `fig_D3_ols.csv`.

D4 uses the same cells and the same snapshot. The first two maps share a log scale from 1 to {d4['vmax_parcels']:.0f} parcels. Zero cells are grey. The third map is clipped for display at the 99th percentile of the absolute log ratio (±{d4['log_ratio_clip']:.2f}). The unclipped range is {d4['log_ratio_min']:.2f} to {d4['log_ratio_max']:.2f}, and the CSV has the unclipped value. Red means more model parcels than SSEG parcels.

## Limits

{scope['limits']}

The midpoint of a long interval is not an install month. Census-bounded and left-censored rows are the cases where that mistake is largest.

Building footprints are not used. A parcel can hold more than one array, and a point can fall in the wrong parcel when the geocode is off. The commissioned match rate is {match['sseg_commissioned_match_rate']:.1%} ({match['sseg_commissioned_matched']:,} of {match['sseg_commissioned']:,}). Rates by coordinate source are in the run note. The figures do not use a distance buffer.

No claim is made about precision, recall, or date error. Those are later tables, and the date comparison has its own sample rule.

## Files

`figs/` has PNG at 300 dpi and a vector PDF. The figure is composed at {FIG_WIDTH_MM} mm wide. `data/` has the cell-level and month-level tables behind the figures and the captions. It has no SSEG row and no hashed account, erf, or device number. `release_manifest.json` repeats the frozen constants and the input hashes.

Municipal parcels and the supply-authority layer are City of Cape Town open data, used under the City's terms and acknowledged here. Their geometries are not in this folder.
"""
    captions = f"""# Captions, batch v1

## Figure D5

Timing of detected rooftop PV and municipal SSEG commissioning in Cape Town. (A) Monthly counts of census polygons whose install-date interval has a midpoint in that month. Only closed absent-to-present intervals are included (n = {d5['n_dated']:,}). Polygons already present before the 2019 satellite window (left-censored, n = {d5['n_left_censored']:,}) and polygons known only to have been installed by the {CENSUS_DATE} census aerial (census-bounded, n = {d5['n_census_bound']:,}) are the grey bars and are not assigned to a month. Undated polygons (n = {d5['n_undated']:,}) are omitted. (B) Monthly counts of commissioned SSEG registrations, status codes 1.0 and 4.0 (n = {d5['n_commissioned']:,}). The commission date is the municipal acceptance date. Commissions before 2019 (n = {d5['pre_2019_sseg']:,}) are the grey bar. (C) Cumulative counts of the series in A and B through {MAIN_SNAPSHOT}. The model curve does not include the two censored classes. The SSEG curve includes pre-2019 commissions in its level. At {MAIN_SNAPSHOT} the dated-model cumulative is {d5['model_cumulative_2024_06']:,.0f} and the SSEG cumulative is {d5['sseg_cumulative_2024_06']:,.0f}. The lower strip is the number of satellite anchor-visits by month in the 27-wave stack. The Top-52 cohort (n = {d5['n_top52']:,}) has no visit sequence and is not in that strip. Load-shedding hours are not shown. Install dates are from {MODEL_LABEL}. {MODEL_EXCLUDED}.

## Figure D3

{scope['caption_d3']} (n = {d3['n_cells']:,}). Snapshot {MAIN_SNAPSHOT}. A cell's model count is the number of cadastral parcels that contain the centroid of at least one census polygon classified as installed on that date under the midpoint rule. The SSEG count is the number of parcels that contain at least one commissioned registration on or before that date. Model capacity is census-polygon area times {CAPACITY_KWP_PER_M2:.2f} kWp per square metre. SSEG capacity is `generator_capacity_(va)` in kVA. Polygon area is not module area. Hollow circles are cells, black squares are bin means (a zero bin plus up to {N_QUANTILE_BINS} quantile bins), the solid line is OLS with an intercept and HC1 standard errors, and the dashed line is 45 degrees. Counts: slope {counts['slope']:.4g} (SE {counts['se']:.4g}), R² {counts['r2']:.3f}, N {counts['n']:,}, both axes zero in {counts['zero_share']:.1%} of cells. Capacity: slope {capacity['slope']:.4g} (SE {capacity['se']:.4g}), R² {capacity['r2']:.3f}.

## Figure D4

The same cells and snapshot as Figure D3. The first two maps share a logarithmic colour scale of parcel counts (1 to {d4['vmax_parcels']:.0f}); zero cells are grey. The third map is log((1 + model parcels) / (1 + SSEG parcels)), clipped for display at ±{d4['log_ratio_clip']:.2f} (the 99th percentile of the absolute value). Positive values are cells with more model parcels than SSEG parcels. The grey outline is the City of Cape Town boundary from geoBoundaries ADM2. The scale bar is 10 km.{scope['caption_d4']}
"""
    return readme, captions


def assemble() -> Path:
    required = [
        STAGING / "fig_D5_temporal.png",
        STAGING / "fig_D5_temporal.pdf",
        STAGING / "fig_D5_monthly.csv",
        STAGING / "fig_D5_crosscorr.csv",
        STAGING / "fig_D5_totals.csv",
        STAGING / "d5_numbers.json",
        STAGING / "fig_D3_hex_scatter.png",
        STAGING / "fig_D3_hex_scatter.pdf",
        STAGING / "fig_D3_cells.csv",
        STAGING / "fig_D3_panel.csv",
        STAGING / "fig_D3_ols.csv",
        STAGING / "fig_D3_bins.csv",
        STAGING / "d3_numbers.json",
        STAGING / "fig_D4_hex_maps.png",
        STAGING / "fig_D4_hex_maps.pdf",
        STAGING / "fig_D4_cells.csv",
        STAGING / "d4_numbers.json",
        PHASE0 / "match_qa.json",
        PHASE0 / "cells_qa.json",
    ]
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise Stop("STOP: assemble is missing " + ", ".join(missing))
    if RELEASE.exists() and any(RELEASE.iterdir()):
        raise Stop(f"STOP: {RELEASE} already exists. Refusing to overwrite a batch.")
    d5 = json.loads((STAGING / "d5_numbers.json").read_text())
    d3 = json.loads((STAGING / "d3_numbers.json").read_text())
    d4 = json.loads((STAGING / "d4_numbers.json").read_text())
    match = json.loads((PHASE0 / "match_qa.json").read_text())
    cells_qa = json.loads((PHASE0 / "cells_qa.json").read_text())
    # Caption numbers have to be recoverable from the CSVs.
    monthly = pd.read_csv(STAGING / "fig_D5_monthly.csv")
    june = monthly.loc[monthly.month == CUMULATIVE_MONTH_END].iloc[0]
    if abs(june.model_dated_cumulative - d5["model_cumulative_2024_06"]) > 1e-6:
        raise Stop("STOP: D5 caption cumulative does not match the monthly CSV.")
    if abs(june.sseg_cumulative - d5["sseg_cumulative_2024_06"]) > 1e-6:
        raise Stop("STOP: D5 SSEG cumulative does not match the monthly CSV.")
    cells = pd.read_csv(STAGING / "fig_D3_cells.csv")
    redraw = fit_ols(cells["model_parcels_midpoint"], cells["sseg_parcels"])
    if abs(redraw["slope"] - d3["counts"]["slope"]) > 1e-8:
        raise Stop("STOP: D3 caption slope does not match fig_D3_cells.csv.")
    forbidden_columns = {"latitude", "longitude", "contract_account_hashed", "erf_hashed", "wkt"}
    for csv_path in STAGING.glob("fig_*.csv"):
        header = pd.read_csv(csv_path, nrows=0).columns
        leaked = forbidden_columns.intersection(header)
        if leaked:
            raise Stop(f"STOP: {csv_path.name} has forbidden columns {sorted(leaked)}.")
    RELEASE.mkdir(parents=True)
    (RELEASE / "figs").mkdir()
    (RELEASE / "data").mkdir()
    for name in (
        "fig_D5_temporal.png",
        "fig_D5_temporal.pdf",
        "fig_D3_hex_scatter.png",
        "fig_D3_hex_scatter.pdf",
        "fig_D4_hex_maps.png",
        "fig_D4_hex_maps.pdf",
    ):
        target = RELEASE / "figs" / name
        target.write_bytes((STAGING / name).read_bytes())
    for name in (
        "fig_D5_monthly.csv",
        "fig_D5_crosscorr.csv",
        "fig_D5_totals.csv",
        "fig_D3_cells.csv",
        "fig_D3_panel.csv",
        "fig_D3_ols.csv",
        "fig_D3_bins.csv",
        "fig_D4_cells.csv",
    ):
        target = RELEASE / "data" / name
        target.write_bytes((STAGING / name).read_bytes())
    readme, captions = write_release_text(d5, d3, d4, match, cells_qa)
    (RELEASE / "README.md").write_text(readme)
    (RELEASE / "captions.md").write_text(captions)
    parcel_qa_path = WORK_ROOT / "parcels" / "parcels_qa.json"
    manifest = {
        "batch": "v1",
        "date": BATCH_DATE,
        "code_commit": git_commit(),
        "constants": constants_payload(),
        "inputs": {
            "citywide_csv": {"path": str(CITYWIDE_CSV), "sha256": CITYWIDE_SHA256, "rows": CITYWIDE_ROWS},
            "sseg_csv": {"path": str(SSEG_CSV), "sha256": SSEG_SHA256, "commissioned_rows": SSEG_COMMISSIONED_ROWS},
            "visits_csv": {"path": str(VISITS_CSV), "sha256": d5["visits_sha256"]},
            "parcels_gpkg": {"path": str(PARCEL_GPKG), "sha256": sha256_file(PARCEL_GPKG), "rows": PARCEL_SERVER_COUNT},
            "census_grid": {"path": str(CENSUS_GRID_GPKG), "sha256": cells_qa["census_grid_sha256"]},
            "supply_authority": {"path": str(PHASE0 / "supply_authority.geojson"), "sha256": cells_qa["supply_sha256"], "source": cells_qa["supply_source"]},
        },
        "snapshot_date": MAIN_SNAPSHOT,
        "d5": d5,
        "d3": d3,
        "d4": d4,
        "match": match,
        "cells": cells_qa,
        "parcel_download": json.loads(parcel_qa_path.read_text()) if parcel_qa_path.exists() else None,
        "deviations": [
            "OLS uses HC1 robust standard errors. The task list's clustered errors apply when the row is finer than the cell; here the row is the cell.",
            "Quantile bins keep an explicit zero bin, then up to 20 quantile bins of positive model values.",
            "Panel C cumulates dated midpoints only. Left-censored and census-bounded polygons stay off that curve. SSEG's cumulative level includes pre-2019 commissions.",
            _scope_text(cells_qa)["deviation"],
            "The aerial footprint is the CPT census cells with imagery coverage fraction >= 0.999, and an H3-9 cell must be at least 0.999 inside that union.",
            "A parcel is assigned to the H3-9 cell of a representative point inside the parcel.",
            "Residential primary zone drops Transport and Utility tokens before taking the mode.",
            "The approximate 10 m diagnostic uses a 0.00012 degree buffer and is not an input to any figure.",
        ],
    }
    write_json(RELEASE / "release_manifest.json", manifest)
    lines = []
    for path in sorted(RELEASE.rglob("*")):
        if path.is_file() and path.name != "SHA256SUMS.txt":
            digest = sha256_file(path)
            lines.append(f"{digest}  {path.relative_to(RELEASE).as_posix()}")
    (RELEASE / "SHA256SUMS.txt").write_text("\n".join(lines) + "\n")
    print("ASSEMBLED", RELEASE, flush=True)
    return RELEASE


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("d5", "cells", "phase0", "d3", "d4", "assemble", "v1"))
    args = parser.parse_args()
    WORK_ROOT.mkdir(parents=True, exist_ok=True)
    if args.command == "d5":
        run_d5()
    elif args.command == "cells":
        run_cells()
    elif args.command == "phase0":
        run_phase0()
    elif args.command == "d3":
        run_d3()
    elif args.command == "d4":
        run_d4()
    elif args.command == "assemble":
        assemble()
    else:
        run_d5()
        if not (PHASE0 / "cell_frame.gpkg").exists():
            run_cells()
        run_phase0()
        run_d3()
        run_d4()
        assemble()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Stop as exc:
        print(exc, flush=True)
        sys.exit(2)
