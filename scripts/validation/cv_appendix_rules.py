"""Pure rules shared by the appendix figures.

Stock at a snapshot, residential zoning, monthly flows, and the parcel-level
interval used later by D6 all live here so a figure script cannot invent a
second definition. No file I/O.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from scripts.validation.cv_appendix_constants import (
    CAPACITY_KWP_PER_M2,
    CENSUS_DATE,
    COMMISSIONED_PREFIXES,
    N_QUANTILE_BINS,
    OFFGRID_PREFIX,
    RESIDENTIAL_CELL_MIN_SHARE,
    SUPPLY_COVERS_CITY_MAX_OUTSIDE_RATE,
)

_ROAD_PREFIX = "Transport"
_IGNORE_TOKEN = "Utility"
_RESIDENTIAL_PREFIXES = ("Single Residential", "General Residential")


def is_commissioned_status(status: object) -> bool:
    text = "" if status is None or (isinstance(status, float) and np.isnan(status)) else str(status)
    return text.startswith(COMMISSIONED_PREFIXES)


def is_offgrid_status(status: object) -> bool:
    text = "" if status is None or (isinstance(status, float) and np.isnan(status)) else str(status)
    return text.startswith(OFFGRID_PREFIX)


def zoning_tokens(zoning: object) -> list[str]:
    if zoning is None or (isinstance(zoning, float) and np.isnan(zoning)):
        return []
    text = str(zoning).strip()
    if not text:
        return []
    return [part.strip() for part in text.split(",") if part.strip()]


def primary_zone(zoning: object) -> str | None:
    """Most common substantive zone on a parcel.

    CoCT stores split zoning as a repeated comma-separated list. Road and
    utility tokens are often a sliver of the same erf, so they are dropped
    before the mode is taken. Ties keep the first token in the original order.
    """
    tokens = zoning_tokens(zoning)
    substantive = [
        token for token in tokens if not token.startswith(_ROAD_PREFIX) and token != _IGNORE_TOKEN
    ]
    use = substantive or tokens
    if not use:
        return None
    counts: dict[str, int] = {}
    order: list[str] = []
    for token in use:
        if token not in counts:
            order.append(token)
            counts[token] = 0
        counts[token] += 1
    best = max(counts.values())
    for token in order:
        if counts[token] == best:
            return token
    return None


def is_residential_zoning(zoning: object) -> bool:
    zone = primary_zone(zoning)
    if zone is None:
        return False
    return zone.startswith(_RESIDENTIAL_PREFIXES)


def is_residential_cell(residential_share: float) -> bool:
    if residential_share is None or (isinstance(residential_share, float) and np.isnan(residential_share)):
        return False
    return float(residential_share) >= RESIDENTIAL_CELL_MIN_SHARE


def interval_midpoint(start: pd.Timestamp, end: pd.Timestamp) -> pd.Timestamp:
    """Calendar date of the midpoint of a closed interval."""
    mid = pd.Timestamp(start) + (pd.Timestamp(end) - pd.Timestamp(start)) / 2
    return pd.Timestamp(mid.date())


def _stamp(value: object) -> pd.Timestamp | None:
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return None
    if isinstance(value, str) and not value.strip():
        return None
    stamp = pd.Timestamp(value)
    if pd.isna(stamp):
        return None
    return pd.Timestamp(stamp.date())


def polygon_interval(row: pd.Series) -> tuple[str, pd.Timestamp | None, pd.Timestamp | None]:
    """Return (kind, start, end) for one census polygon.

    kind is one of dated, left_censored, census_bound, undated, unexpected.
    Left-censored rows have no start. Census-bounded rows use
    [latest_absent_date, census date].
    """
    status = row.get("date_status")
    blank = status is None or (isinstance(status, float) and np.isnan(status)) or str(status).strip() == ""
    left = int(row.get("left_censored") or 0) == 1
    bound = int(row.get("census_bound") or 0) == 1
    if left or (not blank and status == "done_already_present_before_geid_history"):
        return "left_censored", None, _stamp(row.get("earliest_present_date"))
    if bound or (not blank and status == "done_installed_during_census"):
        return "census_bound", _stamp(row.get("latest_absent_date")), _stamp(CENSUS_DATE)
    if blank:
        return "undated", None, None
    if status == "done_appears":
        return "dated", _stamp(row.get("install_interval_start")), _stamp(row.get("install_interval_end"))
    return "unexpected", None, None


def interval_installed(
    kind: str,
    start: pd.Timestamp | None,
    end: pd.Timestamp | None,
    snapshot: pd.Timestamp,
    rule: str,
) -> bool:
    """Whether one polygon counts as installed at snapshot under rule."""
    snap = pd.Timestamp(pd.Timestamp(snapshot).date())
    if kind == "undated" or kind == "unexpected":
        return False
    if kind == "left_censored":
        return True
    if rule == "lower":
        return end is not None and end <= snap
    if rule == "upper":
        return start is not None and start <= snap
    if rule != "midpoint":
        raise ValueError(f"unknown stock rule: {rule}")
    if start is None or end is None:
        return False
    return interval_midpoint(start, end) <= snap


def polygon_installed(row: pd.Series, snapshot: pd.Timestamp, rule: str) -> bool:
    kind, start, end = polygon_interval(row)
    return interval_installed(kind, start, end, snapshot, rule)


def installed_mask(
    kinds: pd.Series,
    starts: pd.Series,
    ends: pd.Series,
    snapshot: pd.Timestamp,
    rule: str,
) -> pd.Series:
    """Vector form of interval_installed. kinds are already classified."""
    if rule not in {"lower", "midpoint", "upper"}:
        raise ValueError(f"unknown stock rule: {rule}")
    snap = pd.Timestamp(pd.Timestamp(snapshot).date())
    kind = kinds.astype(str)
    start = pd.to_datetime(starts)
    end = pd.to_datetime(ends)
    out = pd.Series(False, index=kinds.index)
    out.loc[kind == "left_censored"] = True
    use = kind.isin(["dated", "census_bound"])
    if rule == "lower":
        out.loc[use] = end.loc[use].notna() & (end.loc[use] <= snap)
    elif rule == "upper":
        out.loc[use] = start.loc[use].notna() & (start.loc[use] <= snap)
    else:
        mid = start.loc[use] + (end.loc[use] - start.loc[use]) / 2
        out.loc[use] = mid.notna() & (mid.dt.normalize() <= snap)
    return out


def model_capacity_kwp(area_m2: float) -> float:
    return float(area_m2) * CAPACITY_KWP_PER_M2


def month_index(stamp: pd.Timestamp) -> int:
    day = pd.Timestamp(stamp)
    return int(day.year) * 12 + int(day.month)


def month_index_range(start: str, end: str) -> list[int]:
    months = pd.period_range(start, end, freq="M")
    return [int(period.year) * 12 + int(period.month) for period in months]


def index_to_label(index: int) -> str:
    year, month = divmod(index, 12)
    if month == 0:
        year -= 1
        month = 12
    return f"{year:04d}-{month:02d}"


def spread_interval(start: pd.Timestamp, end: pd.Timestamp) -> dict[int, float]:
    """Spread one system uniformly over the months its interval covers.

    Weight of a month is the share of inclusive days that fall in that month.
    """
    start_d = pd.Timestamp(start).normalize()
    end_d = pd.Timestamp(end).normalize()
    if end_d < start_d:
        return {}
    days = pd.date_range(start_d, end_d, freq="D")
    if len(days) == 0:
        return {}
    weight = 1.0 / len(days)
    out: dict[int, float] = {}
    for day in days:
        key = int(day.year) * 12 + int(day.month)
        out[key] = out.get(key, 0.0) + weight
    return out


def lagged_pearson(
    left: dict[int, float],
    right: dict[int, float],
    max_lag: int,
) -> list[dict]:
    """corr(left_t, right_{t+k}) for k = 0..max_lag.

    A positive lag moves the right-hand series later. For these figures the
    left series is the model flow and the right series is SSEG commissions,
    so a positive lag is registration lag.
    """
    rows = []
    left_keys = sorted(left)
    for lag in range(0, max_lag + 1):
        xs: list[float] = []
        ys: list[float] = []
        for key in left_keys:
            other = key + lag
            if other in right:
                xs.append(float(left[key]))
                ys.append(float(right[other]))
        pearson = None
        if len(xs) >= 3 and float(np.std(xs)) > 0 and float(np.std(ys)) > 0:
            pearson = float(np.corrcoef(xs, ys)[0, 1])
        rows.append({"lag_months": lag, "n_months": len(xs), "pearson": pearson})
    return rows


def quantile_bin_means(x: np.ndarray, y: np.ndarray, n_bins: int = N_QUANTILE_BINS) -> pd.DataFrame:
    """Bin means for a scatter.

    Values of x that are exactly zero form their own bin. The remaining x
    values are cut into up to n_bins quantile bins. Empty cuts are dropped,
    which happens when many cells share the same count.
    """
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    rows = []
    zero = x == 0
    if zero.any():
        rows.append(
            {
                "bin": 0,
                "kind": "zero",
                "n": int(zero.sum()),
                "mean_x": 0.0,
                "mean_y": float(np.mean(y[zero])),
            }
        )
    positive = ~zero
    if positive.sum() == 0:
        return pd.DataFrame(rows)
    ranks = pd.Series(x[positive])
    try:
        cats = pd.qcut(ranks, q=min(n_bins, int(positive.sum())), duplicates="drop")
    except ValueError:
        cats = pd.Series(["all"] * int(positive.sum()))
    y_pos = y[positive]
    x_pos = x[positive]
    categories = list(cats.cat.categories) if isinstance(cats.dtype, pd.CategoricalDtype) else ["all"]
    for i, cat in enumerate(categories, start=1):
        mask = np.asarray(cats == cat)
        rows.append(
            {
                "bin": i,
                "kind": "quantile",
                "n": int(mask.sum()),
                "mean_x": float(np.mean(x_pos[mask])),
                "mean_y": float(np.mean(y_pos[mask])),
            }
        )
    return pd.DataFrame(rows)


def parcel_model_interval(kinds: list[str], starts: list, ends: list) -> tuple[str, object, object]:
    """Parcel-level interval for a later D6 comparison.

    Returns (parcel_kind, L, U).

    If any polygon is left-censored, the parcel has no L: the plan treats
    that parcel as unable to contradict an early commission date. U is the
    earliest present date among polygons that have one. Otherwise L is the
    earliest interval start and U is the earliest interval end.
    """
    if not kinds:
        return "no_model", None, None
    if any(kind == "left_censored" for kind in kinds):
        # Left-censored end is the first history date, which is a present date.
        present_dates = [
            pd.Timestamp(value) for value in ends if value is not None and not pd.isna(value)
        ]
        earliest_u = min(present_dates) if present_dates else None
        return "left_censored", None, earliest_u
    usable = [
        (pd.Timestamp(start), pd.Timestamp(end))
        for kind, start, end in zip(kinds, starts, ends)
        if kind in {"dated", "census_bound"} and start is not None and end is not None and not pd.isna(start) and not pd.isna(end)
    ]
    if not usable:
        return "undated", None, None
    if any(kind == "census_bound" for kind in kinds) and not any(kind == "dated" for kind in kinds):
        kind_out = "census_bound"
    elif any(kind == "census_bound" for kind in kinds):
        kind_out = "mixed"
    else:
        kind_out = "dated"
    return kind_out, min(pair[0] for pair in usable), min(pair[1] for pair in usable)


def supply_layer_covers_city(required_places_outside: int, commissioned_outside_rate: float) -> bool:
    """Whether a supply-authority polygon can be used to drop cells.

    One required city location outside both authorities rejects the layer.
    So does a commissioned-point outside rate at or above the frozen maximum.
    Rejecting the layer means keeping every footprint cell, which is the
    section 6 fallback, not a new stock definition.
    """
    if int(required_places_outside) > 0:
        return False
    try:
        rate = float(commissioned_outside_rate)
    except (TypeError, ValueError):
        return False
    if not np.isfinite(rate):
        return False
    return rate < SUPPLY_COVERS_CITY_MAX_OUTSIDE_RATE
