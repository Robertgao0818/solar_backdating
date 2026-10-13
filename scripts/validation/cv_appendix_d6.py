"""Parcel-level commission comparison for figure D6.

Matching, the three descriptive cases, and the reference distribution live
here so a later threshold comparison can call them instead of copying the
definitions. No file I/O and no network.

The commission date C is an upper bound on the install date: registration
lag moves C later, not earlier. tau is a day tolerance around C. It is not
a claim about precision.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import shapely

from scripts.validation.cv_appendix_constants import D6_MONTH_DAYS
from scripts.validation.cv_appendix_rules import interval_midpoint

CASE_LAG = "registration_lag"
CASE_AGREE = "agreement"
CASE_CONTRADICTION = "contradiction"
CASES = (CASE_LAG, CASE_AGREE, CASE_CONTRADICTION)

# Drawn window of the histogram, in months. Statistics use every dated pair,
# including pairs that fall outside this window.
DISPLAY_MIN_MONTHS = -36
DISPLAY_MAX_MONTHS = 36
BIN_WIDTH_MONTHS = 1

KIND_DATED = "dated"


class InvalidParcelInterval(ValueError):
    """Earliest U is before earliest L. Do not invent a repair."""


def enters_delta_histogram(kind: str) -> bool:
    """True when the parcel kind is dated.

    The kind is dated only when the polygons that set L and U are dated.
    Undated polygons on the same parcel are ignored when that interval is
    formed. A dated parcel is therefore not a parcel whose every polygon
    is dated. Left-censored, census-bounded, mixed, and undated parcels
    are counted by kind and do not contribute a midpoint.
    """
    return kind == KIND_DATED


def enters_case_count(kind: str) -> bool:
    """True for kinds that receive one of the three descriptive cases.

    Dated, census-bounded, mixed, and left-censored parcels are classified.
    Undated parcels and parcels with no model polygon are not. This gate is
    separate from the histogram: only a dated parcel contributes a midpoint.
    """
    return kind in {KIND_DATED, "census_bound", "mixed", "left_censored"}


def _day(value: object) -> pd.Timestamp:
    stamp = pd.Timestamp(value)
    if pd.isna(stamp):
        raise ValueError("a D6 date is missing")
    return pd.Timestamp(stamp.date())


def assert_interval_ordered(start: object, end: object) -> tuple[pd.Timestamp, pd.Timestamp]:
    """Return calendar L and U, or raise when U is before L."""
    left = _day(start)
    right = _day(end)
    if right < left:
        raise InvalidParcelInterval(f"U {right.date()} is before L {left.date()}")
    return left, right


def classify_dated_case(start: object, end: object, commission: object, tau_days: int) -> str:
    """One dated parcel at one tau.

    registration_lag: U <= C. The panel was seen before the commission date.
    agreement: L <= C + tau and U > C.
    contradiction: L > C + tau. The model still says absent after commission.
    """
    if int(tau_days) < 0:
        raise ValueError(f"tau_days must be >= 0, got {tau_days}")
    left, right = assert_interval_ordered(start, end)
    day = _day(commission)
    limit = day + pd.Timedelta(days=int(tau_days))
    if right <= day:
        return CASE_LAG
    if left <= limit and right > day:
        return CASE_AGREE
    if left > limit:
        return CASE_CONTRADICTION
    raise ValueError("dated pair did not fall into one case")


def classify_case(kind: str, start: object, end: object, commission: object, tau_days: int) -> str:
    """One parcel that enters the case count, at one tau.

    Dated, census-bounded, and mixed parcels use the dated three-way rule
    on the interval the caller supplies. For a mixed parcel that interval
    is the section B.3 earliest L and earliest U across dated and
    census-bounded polygons. A census-bounded parcel has U equal to the
    census date. When every commission date is before that day the result
    is agreement or contradiction.

    A left-censored parcel has no L. Registration lag if U is on or before
    C, otherwise agreement. Tau does not move it, and it is never a
    contradiction.
    """
    if not enters_case_count(kind):
        raise ValueError(f"{kind} is excluded from the three cases")
    if int(tau_days) < 0:
        raise ValueError(f"tau_days must be >= 0, got {tau_days}")
    if kind == "left_censored":
        if start is not None and not pd.isna(start):
            raise ValueError("a left-censored parcel must not have L")
        if end is None or pd.isna(end):
            raise ValueError("a left-censored parcel is missing U")
        if _day(end) <= _day(commission):
            return CASE_LAG
        return CASE_AGREE
    return classify_dated_case(start, end, commission, tau_days)


def delta_months(midpoint: object, commission: object, month_days: float = D6_MONTH_DAYS) -> float:
    """Midpoint minus commission date, in months of ``month_days`` days."""
    if not np.isfinite(month_days) or float(month_days) <= 0:
        raise ValueError(f"month_days must be positive, got {month_days}")
    return (_day(midpoint) - _day(commission)).days / float(month_days)


def commission_inside_interval(start: object, end: object, commission: object) -> bool:
    """True when C is inside the closed interval [L, U]."""
    left, right = assert_interval_ordered(start, end)
    day = _day(commission)
    return left <= day <= right


def earliest_commission(dates) -> pd.Timestamp:
    """Earliest commission date. Used only outside the one-record sample."""
    days = [_day(value) for value in dates]
    if not days:
        raise ValueError("earliest_commission received no dates")
    return min(days)


def summarize_months(values: np.ndarray, month_abs_6: float = 6.0, month_abs_12: float = 12.0) -> dict:
    """Mean, median, IQR, and the two absolute-deviation shares.

    Quantiles use NumPy's linear method. IQR is the 75th percentile minus
    the 25th. Shares use a closed cutoff: |Δ| <= 6 includes a value of 6.
    """
    series = np.asarray(values, dtype=float)
    if series.size == 0 or not np.isfinite(series).all():
        raise ValueError("summarize_months needs a non-empty finite sample")
    q1, median, q3 = np.quantile(series, [0.25, 0.5, 0.75], method="linear")
    abs_value = np.abs(series)
    return {
        "n": int(series.size),
        "mean": float(np.mean(series)),
        "median": float(median),
        "q1": float(q1),
        "q3": float(q3),
        "iqr": float(q3 - q1),
        "n_abs_le_6": int((abs_value <= month_abs_6).sum()),
        "share_abs_le_6": float((abs_value <= month_abs_6).mean()),
        "n_abs_le_12": int((abs_value <= month_abs_12).sum()),
        "share_abs_le_12": float((abs_value <= month_abs_12).mean()),
        "minimum": float(series.min()),
        "maximum": float(series.max()),
    }


def summarize_registration_lag(
    commissions,
    ends,
    month_days: float = D6_MONTH_DAYS,
) -> dict:
    """Median and IQR of C − U on the parcels where U <= C.

    The lag is not a score. It describes how long after the first present
    view the commission date falls.
    """
    start_days = []
    for commission, end in zip(commissions, ends):
        day = _day(commission)
        right = _day(end)
        if right <= day:
            start_days.append(int((day - right).days))
    if not start_days:
        return {
            "n": 0,
            "median_days": None,
            "q1_days": None,
            "q3_days": None,
            "iqr_days": None,
            "median_months": None,
            "q1_months": None,
            "q3_months": None,
            "iqr_months": None,
        }
    days = np.asarray(start_days, dtype=float)
    q1, median, q3 = np.quantile(days, [0.25, 0.5, 0.75], method="linear")
    scale = float(month_days)
    return {
        "n": int(days.size),
        "median_days": float(median),
        "q1_days": float(q1),
        "q3_days": float(q3),
        "iqr_days": float(q3 - q1),
        "median_months": float(median / scale),
        "q1_months": float(q1 / scale),
        "q3_months": float(q3 / scale),
        "iqr_months": float((q3 - q1) / scale),
    }


def display_edges(
    minimum: int = DISPLAY_MIN_MONTHS,
    maximum: int = DISPLAY_MAX_MONTHS,
    width: int = BIN_WIDTH_MONTHS,
) -> np.ndarray:
    """Bin edges in months. The last bin is closed on the right, as in NumPy."""
    if width <= 0 or maximum <= minimum:
        raise ValueError("display edges need a positive width and a rising window")
    return np.arange(minimum, maximum + width, width, dtype=float)


def histogram_with_tails(values, edges: np.ndarray) -> tuple[np.ndarray, int, int]:
    """Counts per bin, plus how many values fall strictly outside the edges."""
    series = np.asarray(values, dtype=float)
    counts, _ = np.histogram(series, bins=edges)
    below = int((series < edges[0]).sum())
    above = int((series > edges[-1]).sum())
    return counts.astype(int), below, above


def reference_histogram(
    starts,
    ends,
    edges: np.ndarray,
    month_days: float = D6_MONTH_DAYS,
) -> tuple[np.ndarray, float, float]:
    """Expected bin counts if C were uniform on the inclusive days of [L, U].

    Each pair contributes 1, split across the days from L through U. The
    midpoint is the calendar midpoint of that same interval. The total of
    the bins and the two tails equals the number of pairs. This is the exact
    mixture, not a random draw.
    """
    counts = np.zeros(len(edges) - 1, dtype=float)
    below = 0.0
    above = 0.0
    n_pairs = 0
    for start, end in zip(starts, ends):
        left, right = assert_interval_ordered(start, end)
        n_days = int((right - left).days) + 1
        if n_days <= 0:
            raise InvalidParcelInterval("interval has no calendar day")
        midpoint = interval_midpoint(left, right)
        base = int((midpoint - left).days)
        delta_days = base - np.arange(n_days)
        delta_months_values = delta_days / float(month_days)
        hist, _ = np.histogram(delta_months_values, bins=edges)
        weight = 1.0 / n_days
        counts += hist * weight
        below += float((delta_months_values < edges[0]).sum()) * weight
        above += float((delta_months_values > edges[-1]).sum()) * weight
        n_pairs += 1
    total = float(counts.sum() + below + above)
    if n_pairs and abs(total - n_pairs) > 1e-6:
        raise RuntimeError(f"reference histogram sums to {total}, not {n_pairs}")
    return counts, below, above


def assign_exterior_metres(
    xy: np.ndarray,
    geometries,
    object_ids: np.ndarray,
    buffer_m: float,
) -> tuple[np.ndarray, np.ndarray]:
    """Assign points that are already known to lie outside every parcel.

    ``xy`` is in the same metre CRS as ``geometries`` (EPSG:32734 in the
    Cape Town run). A point within ``buffer_m`` of exactly one parcel is
    assigned to it. A point within ``buffer_m`` of two or more parcels is
    left unassigned and marked buffer_ambiguous. An inside match is not
    decided here: the caller keeps the strict containment result.
    """
    if not np.isfinite(buffer_m) or float(buffer_m) < 0:
        raise ValueError(f"buffer_m must be >= 0, got {buffer_m}")
    coords = np.asarray(xy, dtype=float)
    if coords.ndim != 2 or coords.shape[1] != 2:
        raise ValueError("xy must have shape (n, 2)")
    n = int(coords.shape[0])
    out_ids = np.full(n, np.nan)
    status = np.full(n, "unmatched", dtype=object)
    if n == 0:
        return out_ids, status
    geoms = np.asarray(list(geometries), dtype=object)
    ids = np.asarray(object_ids)
    if len(geoms) != len(ids):
        raise ValueError("geometries and object_ids differ in length")
    if len(geoms) == 0:
        return out_ids, status
    points = shapely.points(coords[:, 0], coords[:, 1])
    tree = shapely.STRtree(geoms)
    pairs = tree.query(shapely.buffer(points, float(buffer_m)), predicate="intersects")
    if pairs.shape[1] == 0:
        return out_ids, status
    left = pairs[0]
    right = pairs[1]
    distance = shapely.distance(points[left], geoms[right])
    keep = np.asarray(distance <= float(buffer_m))
    left = left[keep]
    right = right[keep]
    if len(left) == 0:
        return out_ids, status
    hits = pd.DataFrame({"point": left, "parcel": right})
    sizes = hits.groupby("point").size()
    unique = sizes[sizes == 1].index
    ambiguous = sizes[sizes > 1].index
    status[np.asarray(ambiguous, dtype=int)] = "buffer_ambiguous"
    if len(unique):
        chosen = hits[hits["point"].isin(unique)].drop_duplicates("point")
        point_index = chosen["point"].to_numpy(dtype=int)
        out_ids[point_index] = ids[chosen["parcel"].to_numpy(dtype=int)]
        status[point_index] = "buffer_unique"
    return out_ids, status
