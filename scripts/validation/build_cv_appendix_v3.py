#!/usr/bin/env python3
"""Build CV appendix figure D6 (batch v3).

The figure is a histogram of parcel midpoint minus SSEG commission date.
Definitions live in cv_appendix_constants.py, cv_appendix_rules.py, and
cv_appendix_d6.py. Outputs stay under ~/zasolar_data and are not uploaded.

    python scripts/validation/build_cv_appendix_v3.py prepare
    python scripts/validation/build_cv_appendix_v3.py assemble
    python scripts/validation/build_cv_appendix_v3.py all
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import struct
import subprocess
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from scripts.validation.cv_appendix_constants import (
    BUFFER_M_SENSITIVITY,
    CENSUS_DATE,
    CITYWIDE_CSV,
    CITYWIDE_ROWS,
    CITYWIDE_SHA256,
    CLASS_COUNTS,
    D6_COMMISSION_BEFORE,
    D6_MONTH_DAYS,
    FIG_DPI,
    FIG_WIDTH_MM,
    HISTOGRAM_MONTH_START,
    MATCH_RATE_MIN,
    MODEL_EXCLUDED,
    MODEL_LABEL,
    PARCEL_SERVER_COUNT,
    SSEG_COMMISSIONED_ROWS,
    SSEG_CSV,
    SSEG_SHA256,
    SSEG_USECOLS,
    TAU_DAYS_MAIN,
    TAU_DAYS_SENSITIVITY,
    WORK_ROOT,
    constants_payload,
)
from scripts.validation.cv_appendix_d6 import (
    BIN_WIDTH_MONTHS,
    CASES,
    DISPLAY_MAX_MONTHS,
    DISPLAY_MIN_MONTHS,
    assign_exterior_metres,
    assert_interval_ordered,
    classify_dated_case,
    commission_inside_interval,
    delta_months,
    display_edges,
    earliest_commission,
    enters_delta_histogram,
    histogram_with_tails,
    reference_histogram,
    summarize_months,
    summarize_registration_lag,
)
from scripts.validation.cv_appendix_rules import interval_midpoint, is_commissioned_status, parcel_model_interval

PHASE0 = WORK_ROOT / "phase0"
STAGING = WORK_ROOT / "staging_v3"
RELEASE = WORK_ROOT / "release_v3"
PARCEL_GPKG = WORK_ROOT / "parcels" / "parcels.gpkg"
FIG_WIDTH_IN = FIG_WIDTH_MM / 25.4
WINDOW_START = pd.Timestamp(HISTOGRAM_MONTH_START + "-01")

# Phase 0 products already accepted with batch v1. A different hash means
# this script is no longer reading that parcel table.
PHASE0_SHA256 = {
    "census_links": "b165d2f08ad54c69fdbacdda873ca49344fa938a9f41e6b43a9aef60e4a7a566",
    "sseg_links": "f59985163fbe05b70c2b4aabdd24b222c9237c370611316432069e8166f2f50d",
    "parcels_gpkg": "28597a909e4b2588dd997f41fcdedbc9633eae57e24d13e6140f6c13c398f937",
}
PUBLISHED_COMMISSIONED_MATCHED = 5146

DEVIATIONS = [
    "Delta in months is an exact day count divided by 365.2425/12. The plan asks for months and does not name the divisor.",
    "The reference line is the exact mixture of per-pair discrete uniforms over inclusive calendar days. It is not a Monte Carlo draw.",
    "A parcel with both a dated polygon and a census-bounded polygon is labelled mixed. It is counted and kept out of the histogram and out of the three cases. Every mixed interval has U on or after L.",
    "The 10 m buffer is defined in EPSG:32734 metres. It added no parcels: every commissioned row with a WKT point is already strictly inside a parcel, and the unmatched commissioned rows have no WKT. Rounded latitude and longitude were not read.",
    "The strict parcel assignment published in Phase 0 is reused, including its smaller-parcel choice where one point fell in more than one parcel. Those points are not reassigned.",
    "The drawn window is -36 to 36 months. Means, medians, shares, and the three case counts use every dated pair, including pairs outside the window.",
    "Contradiction cases were not reviewed on an aerial image. The three case counts are a description of the two dates.",
    "Commissions before 2019 stay in the main sample and are counted on their own. They are not dropped.",
    "No single-record commission date falls on or after 2024-10-31, so the three-month cutoff removes nobody from this extract. The cutoff is still applied.",
]


class Stop(SystemExit):
    """A section 7.4 stop. The message is the whole report."""


def git_commit() -> str:
    """HEAD of this repo. Refuse a dirty tracked tree so the manifest matches the code."""
    repo = Path(__file__).resolve().parents[2]
    script = Path(__file__).resolve().relative_to(repo).as_posix()
    module = "scripts/validation/cv_appendix_d6.py"
    script_state = subprocess.check_output(
        ["git", "status", "--porcelain", "--", script, module],
        cwd=repo,
        text=True,
    )
    if script_state.strip():
        raise Stop(
            "STOP: the D6 script or its rule module is untracked or modified, "
            "so the figure would not match HEAD.\n" + script_state
        )
    dirty = subprocess.check_output(
        ["git", "status", "--porcelain", "--untracked-files=no"],
        cwd=repo,
        text=True,
    )
    if dirty.strip():
        raise Stop("STOP: tracked files are modified, so code_commit would not match this tree.\n" + dirty)
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sanitize(value):
    """JSON values only. NaN and infinity become null so allow_nan can stay off."""
    if isinstance(value, dict):
        return {str(key): _sanitize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_sanitize(item) for item in value]
    if isinstance(value, (np.floating, float)):
        number = float(value)
        return number if math.isfinite(number) else None
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, (pd.Timestamp,)):
        return None if pd.isna(value) else pd.Timestamp(value).date().isoformat()
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return value


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(_sanitize(payload), indent=2, allow_nan=False) + "\n"
    path.write_text(text)
    json.loads(text)


def apply_style() -> None:
    plt.rcParams.update(
        {
            "font.size": 8,
            "axes.labelsize": 8,
            "axes.titlesize": 8,
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


def _png_mm(path: Path) -> tuple[float, float]:
    raw = path.read_bytes()
    if raw[:8] != b"\x89PNG\r\n\x1a\n":
        raise Stop(f"STOP: {path} is not a PNG.")
    width, height = struct.unpack(">II", raw[16:24])
    return width / FIG_DPI * 25.4, height / FIG_DPI * 25.4


def _pdf_mm(path: Path) -> tuple[float, float]:
    raw = path.read_bytes()
    start = raw.find(b"/MediaBox")
    if start < 0:
        raise Stop(f"STOP: {path} has no MediaBox.")
    left = raw.find(b"[", start)
    right = raw.find(b"]", left)
    numbers = [float(part) for part in raw[left + 1 : right].split()]
    if len(numbers) != 4:
        raise Stop(f"STOP: {path} MediaBox is not four numbers.")
    return (numbers[2] - numbers[0]) * 25.4 / 72.0, (numbers[3] - numbers[1]) * 25.4 / 72.0


def _kind_counts(frame: pd.DataFrame) -> dict[str, int]:
    counts = frame["parcel_kind"].fillna("no_model").value_counts()
    return {str(key): int(value) for key, value in counts.items()}


def _sample_rows(scope: str, frame: pd.DataFrame) -> list[dict]:
    rows = [{"scope": scope, "group": "parcels", "n": int(len(frame))}]
    for key, value in sorted(_kind_counts(frame).items()):
        rows.append({"scope": scope, "group": key, "n": value})
    return rows


def _case_rows(sample: str, labels: pd.Series, tau_days: int) -> list[dict]:
    rows = []
    for case in CASES:
        rows.append(
            {
                "sample": sample,
                "tau_days": int(tau_days),
                "case": case,
                "n": int((labels == case).sum()),
                "n_dated": int(len(labels)),
            }
        )
    return rows


def _fmt_months(value: float) -> str:
    return f"{value:.2f}"


def _fmt_count(value: int) -> str:
    return f"{int(value):,}"


def validate_inputs() -> dict:
    """Check the frozen inputs and the Phase 0 tables D6 is allowed to read."""
    if "latitude" in SSEG_USECOLS or "longitude" in SSEG_USECOLS:
        raise Stop("STOP: SSEG_USECOLS includes a rounded coordinate column.")
    print("hashing citywide CSV", flush=True)
    citywide = sha256_file(CITYWIDE_CSV)
    print("hashing SSEG CSV", flush=True)
    sseg = sha256_file(SSEG_CSV)
    if citywide != CITYWIDE_SHA256 or sseg != SSEG_SHA256:
        raise Stop(
            "STOP: input sha256 does not match the frozen files.\n"
            f"citywide got {citywide} want {CITYWIDE_SHA256}\n"
            f"sseg got {sseg} want {SSEG_SHA256}"
        )
    found = {"citywide_sha256": citywide, "sseg_sha256": sseg}
    for name, expected in (
        ("census_links", PHASE0_SHA256["census_links"]),
        ("sseg_links", PHASE0_SHA256["sseg_links"]),
    ):
        path = PHASE0 / f"{name}.parquet"
        print(f"hashing {path.name}", flush=True)
        digest = sha256_file(path)
        if digest != expected:
            raise Stop(f"STOP: {path} sha256 {digest} does not match the Phase 0 table {expected}.")
        found[f"{name}_sha256"] = digest
    print("hashing parcels.gpkg", flush=True)
    parcel_digest = sha256_file(PARCEL_GPKG)
    if parcel_digest != PHASE0_SHA256["parcels_gpkg"]:
        raise Stop(
            "STOP: parcels.gpkg sha256 does not match the Phase 0 download.\n"
            f"got {parcel_digest} want {PHASE0_SHA256['parcels_gpkg']}"
        )
    found["parcels_gpkg_sha256"] = parcel_digest
    qa_path = PHASE0 / "match_qa.json"
    qa = json.loads(qa_path.read_text())
    if int(qa["parcel_rows"]) != PARCEL_SERVER_COUNT or int(qa["parcel_invalid"]) != 0:
        raise Stop(
            f"STOP: Phase 0 parcel QA is {qa.get('parcel_rows')} rows, "
            f"{qa.get('parcel_invalid')} invalid. Server count is {PARCEL_SERVER_COUNT}."
        )
    if int(qa["sseg_commissioned"]) != SSEG_COMMISSIONED_ROWS:
        raise Stop("STOP: match_qa commissioned count is not 5,458.")
    if int(qa["sseg_commissioned_matched"]) != PUBLISHED_COMMISSIONED_MATCHED:
        raise Stop("STOP: match_qa matched commissioned count is not the published 5,146.")
    if float(qa["sseg_commissioned_match_rate"]) < MATCH_RATE_MIN:
        raise Stop("STOP: SSEG commissioned parcel match rate is below 90%.")
    found["match_qa"] = {
        "parcel_rows": int(qa["parcel_rows"]),
        "sseg_commissioned_matched": int(qa["sseg_commissioned_matched"]),
        "sseg_commissioned_match_rate": float(qa["sseg_commissioned_match_rate"]),
        "sseg_commissioned_with_wkt": int(qa["sseg_commissioned_with_wkt"]),
        "sseg_unmatched_with_wkt_inside_approx_10m": qa["sseg_unmatched_with_wkt_inside_approx_10m"],
        "sseg_multi_parcel": int(qa["sseg_multi_parcel"]),
    }
    raw = pd.read_csv(SSEG_CSV, usecols=list(SSEG_USECOLS))
    commissioned = raw["status"].map(is_commissioned_status)
    wkt = raw["wkt"].fillna("").astype(str).str.strip()
    has_wkt = wkt.ne("") & wkt.str.upper().ne("POINT EMPTY")
    if int(commissioned.sum()) != SSEG_COMMISSIONED_ROWS:
        raise Stop(f"STOP: commissioned rows are {int(commissioned.sum())}, not {SSEG_COMMISSIONED_ROWS}.")
    if int((commissioned & has_wkt).sum()) != PUBLISHED_COMMISSIONED_MATCHED:
        raise Stop(
            "STOP: commissioned rows with WKT are "
            f"{int((commissioned & has_wkt).sum())}, not {PUBLISHED_COMMISSIONED_MATCHED}."
        )
    found["sseg_csv_commissioned_with_wkt"] = PUBLISHED_COMMISSIONED_MATCHED
    return found


def load_links() -> tuple[pd.DataFrame, pd.DataFrame]:
    census = pd.read_parquet(PHASE0 / "census_links.parquet")
    sseg = pd.read_parquet(PHASE0 / "sseg_links.parquet")
    if len(census) != CITYWIDE_ROWS:
        raise Stop(f"STOP: census_links has {len(census)} rows, not {CITYWIDE_ROWS}.")
    expected = {
        "dated": CLASS_COUNTS["done_appears"],
        "census_bound": CLASS_COUNTS["done_installed_during_census"],
        "left_censored": CLASS_COUNTS["done_already_present_before_geid_history"],
        "undated": CLASS_COUNTS["undated"],
    }
    found = {str(key): int(value) for key, value in census["kind"].value_counts().items()}
    if found != expected:
        raise Stop(f"STOP: census class counts {found} do not match section 8.")
    if int(sseg["commissioned"].sum()) != SSEG_COMMISSIONED_ROWS:
        raise Stop("STOP: sseg_links commissioned count is not 5,458.")
    matched = sseg["commissioned"] & sseg["objectid"].notna()
    if int(matched.sum()) != PUBLISHED_COMMISSIONED_MATCHED:
        raise Stop("STOP: sseg_links matched commissioned count is not 5,146.")
    unmatched_wkt = sseg["commissioned"] & sseg["objectid"].isna() & sseg["has_wkt"].astype(bool)
    if int(unmatched_wkt.sum()) != 0:
        raise Stop(
            "STOP: a commissioned row has WKT but no parcel. The 10 m buffer would have "
            "something to measure, and this build expected that set to be empty."
        )
    if int((sseg["commissioned"] & sseg["commission_date"].isna()).sum()) != 0:
        raise Stop("STOP: a commissioned row has no commission date. No rule says how to keep it.")
    return census, sseg


def parcel_intervals(census: pd.DataFrame) -> pd.DataFrame:
    """One row per parcel that contains at least one census centroid."""
    matched = census.loc[census["objectid"].notna()].copy()
    matched["objectid"] = matched["objectid"].astype("int64")
    print(f"parcel intervals for {matched['objectid'].nunique()} parcels", flush=True)
    rows = []
    invalid: list[int] = []
    for object_id, group in matched.groupby("objectid", sort=False):
        kind, start, end = parcel_model_interval(
            group["kind"].tolist(),
            group["interval_start"].tolist(),
            group["interval_end"].tolist(),
        )
        if start is not None and end is not None and pd.Timestamp(end) < pd.Timestamp(start):
            invalid.append(int(object_id))
        if kind in {"dated", "mixed", "census_bound"} and (start is None or end is None):
            raise Stop(f"STOP: parcel {object_id} is {kind} but is missing L or U.")
        rows.append(
            {
                "objectid": int(object_id),
                "parcel_kind": kind,
                "L": start,
                "U": end,
                "n_poly": int(len(group)),
                "n_dated_poly": int((group["kind"] == "dated").sum()),
                "n_left_poly": int((group["kind"] == "left_censored").sum()),
            }
        )
    if invalid:
        preview = ", ".join(str(item) for item in invalid[:8])
        raise Stop(
            f"STOP: {len(invalid)} parcels have earliest U before earliest L "
            f"(first object ids: {preview}). The interval was not swapped or dropped."
        )
    parcels = pd.DataFrame(rows)
    parcels["L"] = pd.to_datetime(parcels["L"])
    parcels["U"] = pd.to_datetime(parcels["U"])
    print("parcel kinds", parcels["parcel_kind"].value_counts().to_dict(), flush=True)
    return parcels


def _commission_table(sseg: pd.DataFrame) -> pd.DataFrame:
    comm = sseg.loc[sseg["commissioned"] & sseg["objectid"].notna(), ["objectid", "commission_date"]].copy()
    comm["objectid"] = comm["objectid"].astype("int64")
    comm["commission_date"] = pd.to_datetime(comm["commission_date"]).dt.normalize()
    if comm["commission_date"].isna().any():
        raise Stop("STOP: a matched commissioned row lost its commission date.")
    return comm


def _labelled(frame: pd.DataFrame, tau_days: int) -> pd.Series:
    labels = [
        classify_dated_case(start, end, commission, tau_days)
        for start, end, commission in zip(frame["L"], frame["U"], frame["C"])
    ]
    return pd.Series(labels, index=frame.index)


def _dated_frame(sample: pd.DataFrame) -> pd.DataFrame:
    dated = sample.loc[sample["parcel_kind"].map(enters_delta_histogram)].copy()
    if dated.empty:
        raise Stop("STOP: the dated sample is empty, so there is no histogram.")
    if dated["L"].isna().any() or dated["U"].isna().any():
        raise Stop("STOP: a dated parcel is missing L or U.")
    midpoints = []
    for start, end in zip(dated["L"], dated["U"]):
        left, right = assert_interval_ordered(start, end)
        midpoints.append(interval_midpoint(left, right))
    dated["midpoint"] = midpoints
    vector_mid = (dated["L"] + (dated["U"] - dated["L"]) / 2).dt.normalize()
    if not dated["midpoint"].eq(vector_mid).all():
        raise Stop("STOP: the calendar midpoint disagrees with the vector midpoint.")
    dated["delta_months"] = [
        delta_months(mid, commission) for mid, commission in zip(dated["midpoint"], dated["C"])
    ]
    dated["width_days"] = (dated["U"] - dated["L"]).dt.days.astype(int)
    if (dated["width_days"] < 0).any():
        raise Stop("STOP: a dated width is negative after the interval check.")
    dated["commission_inside"] = [
        commission_inside_interval(start, end, commission)
        for start, end, commission in zip(dated["L"], dated["U"], dated["C"])
    ]
    for tau in (TAU_DAYS_MAIN, *TAU_DAYS_SENSITIVITY):
        dated[f"case_{int(tau)}"] = _labelled(dated, int(tau))
        counts = dated[f"case_{int(tau)}"].value_counts()
        if int(counts.reindex(list(CASES)).fillna(0).sum()) != len(dated):
            raise Stop(f"STOP: tau {tau} cases do not cover the dated sample.")
        if set(counts.index) - set(CASES):
            raise Stop(f"STOP: tau {tau} produced a case name that is not in the three-way rule.")
    return dated


def build_comparisons(sseg: pd.DataFrame, parcels: pd.DataFrame) -> dict:
    comm = _commission_table(sseg)
    counts = comm.groupby("objectid").size()
    earliest = comm.groupby("objectid")["commission_date"].min()
    if int(counts.sum()) != PUBLISHED_COMMISSIONED_MATCHED:
        raise Stop("STOP: grouped commissioned records do not sum to 5,146.")
    single_ids = counts[counts == 1].index
    multi_ids = counts[counts >= 2].index
    single = earliest.loc[single_ids].rename("C").reset_index()
    single = single.merge(parcels, on="objectid", how="left")
    multi = earliest.loc[multi_ids].rename("C").reset_index()
    multi = multi.merge(parcels, on="objectid", how="left")
    multi["n_records"] = multi["objectid"].map(counts).astype(int)
    if int(len(single) + multi["n_records"].sum()) != PUBLISHED_COMMISSIONED_MATCHED:
        raise Stop("STOP: one-record parcels plus multi-record rows do not sum to 5,146.")
    cutoff = pd.Timestamp(D6_COMMISSION_BEFORE)
    census_day = pd.Timestamp(CENSUS_DATE)
    excluded = single.loc[single["C"] >= cutoff].copy()
    main = single.loc[single["C"] < cutoff].copy()
    if main["C"].ge(census_day).any():
        raise Stop("STOP: a main-sample commission date is on or after the census day.")
    multi_keep = multi.loc[multi["C"] < cutoff].copy()
    # The buffer has no exterior WKT point to move. Calling it on an empty
    # coordinate array still executes the metre rule.
    buffer_ids, buffer_status = assign_exterior_metres(
        np.zeros((0, 2)),
        [],
        np.array([], dtype=int),
        BUFFER_M_SENSITIVITY,
    )
    if len(buffer_ids) != 0 or len(buffer_status) != 0:
        raise Stop("STOP: the empty exterior set returned a row.")
    dated = _dated_frame(main)
    multi_dated = _dated_frame(multi_keep) if (multi_keep["parcel_kind"] == "dated").any() else multi_keep.iloc[0:0].copy()
    if not multi_dated.empty and "delta_months" not in multi_dated.columns:
        raise Stop("STOP: multi-record dated parcels were not classified.")
    pre = main.loc[main["C"] < WINDOW_START].copy()
    pre_dated = dated.loc[dated["C"] < WINDOW_START].copy()
    print(
        f"main {len(main)} dated {len(dated)} pre2019 {len(pre)} "
        f"excluded_by_cutoff {len(excluded)} multi {len(multi_keep)}",
        flush=True,
    )
    return {
        "main": main,
        "dated": dated,
        "excluded": excluded,
        "multi": multi_keep,
        "multi_dated": multi_dated,
        "pre2019": pre,
        "pre2019_dated": pre_dated,
        "n_multi_records": int(multi["n_records"].sum()),
        "buffer_added": 0,
        "buffer_ambiguous": 0,
    }


def _cohort_counts(census: pd.DataFrame, object_ids: pd.Series) -> dict[str, int]:
    matched = census.loc[census["objectid"].notna(), ["objectid", "cohort"]].copy()
    matched["objectid"] = matched["objectid"].astype("int64")
    sub = matched.loc[matched["objectid"].isin(set(object_ids.tolist()))]
    grouped = sub.groupby("objectid")["cohort"].agg(lambda values: "|".join(sorted(set(map(str, values)))))
    return {str(key): int(value) for key, value in grouped.value_counts().items()}


def _hist_frame(dated: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    edges = display_edges()
    observed, below, above = histogram_with_tails(dated["delta_months"].to_numpy(), edges)
    reference, ref_below, ref_above = reference_histogram(dated["L"], dated["U"], edges)
    if int(observed.sum()) + below + above != len(dated):
        raise Stop("STOP: the observed histogram does not include every dated pair.")
    if abs(float(reference.sum()) + ref_below + ref_above - len(dated)) > 1e-6:
        raise Stop("STOP: the reference histogram does not sum to the dated sample.")
    rows = []
    for left, right, obs, ref in zip(edges[:-1], edges[1:], observed, reference):
        rows.append(
            {
                "region": "bin",
                "bin_left_months": float(left),
                "bin_right_months": float(right),
                "n_observed": int(obs),
                "n_reference": float(ref),
            }
        )
    rows.append(
        {
            "region": "below",
            "bin_left_months": None,
            "bin_right_months": float(edges[0]),
            "n_observed": int(below),
            "n_reference": float(ref_below),
        }
    )
    rows.append(
        {
            "region": "above",
            "bin_left_months": float(edges[-1]),
            "bin_right_months": None,
            "n_observed": int(above),
            "n_reference": float(ref_above),
        }
    )
    zero = (edges[:-1] <= 0) & (edges[1:] > 0)
    info = {
        "n_below_window": int(below),
        "n_above_window": int(above),
        "reference_below_window": float(ref_below),
        "reference_above_window": float(ref_above),
        "observed_peak": int(observed.max()),
        "reference_peak": float(reference.max()),
        "zero_bin_observed": int(observed[zero].sum()),
        "zero_bin_reference": float(reference[zero].sum()),
        "display_min_months": DISPLAY_MIN_MONTHS,
        "display_max_months": DISPLAY_MAX_MONTHS,
        "bin_width_months": BIN_WIDTH_MONTHS,
    }
    return pd.DataFrame(rows), info


def _summary_metrics(dated: pd.DataFrame, hist_info: dict) -> dict:
    summary = summarize_months(dated["delta_months"].to_numpy())
    lag = summarize_registration_lag(dated["C"], dated["U"])
    inside = int(dated["commission_inside"].sum())
    width = summarize_months(dated["width_days"].to_numpy(dtype=float))
    lag_labels = dated["case_90"] if "case_90" in dated.columns else dated[f"case_{TAU_DAYS_MAIN}"]
    if int((lag_labels == "registration_lag").sum()) != lag["n"]:
        raise Stop("STOP: registration-lag rows disagree with U <= C.")
    return {
        "n_dated": summary["n"],
        "mean_delta_months": summary["mean"],
        "median_delta_months": summary["median"],
        "q1_delta_months": summary["q1"],
        "q3_delta_months": summary["q3"],
        "iqr_delta_months": summary["iqr"],
        "min_delta_months": summary["minimum"],
        "max_delta_months": summary["maximum"],
        "n_abs_delta_le_6": summary["n_abs_le_6"],
        "share_abs_delta_le_6": summary["share_abs_le_6"],
        "n_abs_delta_le_12": summary["n_abs_le_12"],
        "share_abs_delta_le_12": summary["share_abs_le_12"],
        "n_commission_inside_interval": inside,
        "share_commission_inside_interval": inside / summary["n"],
        "median_width_days": width["median"],
        "q1_width_days": width["q1"],
        "q3_width_days": width["q3"],
        "iqr_width_days": width["iqr"],
        "median_width_months": width["median"] / D6_MONTH_DAYS,
        "lag_n": lag["n"],
        "lag_median_days": lag["median_days"],
        "lag_q1_days": lag["q1_days"],
        "lag_q3_days": lag["q3_days"],
        "lag_iqr_days": lag["iqr_days"],
        "lag_median_months": lag["median_months"],
        "lag_q1_months": lag["q1_months"],
        "lag_q3_months": lag["q3_months"],
        "lag_iqr_months": lag["iqr_months"],
        **hist_info,
    }


def _long_metrics(payload: dict) -> pd.DataFrame:
    rows = []
    for key, value in payload.items():
        if isinstance(value, (dict, list)):
            continue
        rows.append({"metric": key, "value": value})
    return pd.DataFrame(rows)


def plot_d6(dated: pd.DataFrame, hist: pd.DataFrame, metrics: dict, stem: Path) -> None:
    apply_style()
    bins = hist.loc[hist["region"] == "bin"]
    edges = np.r_[bins["bin_left_months"].to_numpy(), bins["bin_right_months"].iloc[-1]]
    observed = bins["n_observed"].to_numpy()
    reference = bins["n_reference"].to_numpy()
    fig = plt.figure(figsize=(FIG_WIDTH_IN, 4.15))
    ax = fig.add_axes((0.10, 0.16, 0.86, 0.62))
    ax.bar(
        edges[:-1],
        observed,
        width=BIN_WIDTH_MONTHS,
        align="edge",
        color="#4C78A8",
        label="Dated parcels",
        zorder=2,
    )
    ax.stairs(reference, edges, color="#222222", linewidth=1.05, label="Uniform inside each interval", zorder=3)
    ax.axvline(0, color="#666666", linewidth=0.6, linestyle="--", zorder=1)
    ax.set_xlim(DISPLAY_MIN_MONTHS - 0.8, DISPLAY_MAX_MONTHS + 0.8)
    ax.set_ylim(0, max(float(observed.max()), float(reference.max())) * 1.08)
    ax.set_xlabel("Δ (months): interval midpoint − commission date")
    ax.set_ylabel("Parcels")
    ax.set_title(
        "D6. Model midpoint minus SSEG commission date\n"
        f"N = {_fmt_count(metrics['n_dated'])} dated parcels; "
        f"median {_fmt_months(metrics['median_delta_months'])} months; "
        f"{_fmt_count(metrics['n_abs_delta_le_6'])} within 6 months; "
        f"{_fmt_count(metrics['n_abs_delta_le_12'])} within 12 months",
        loc="left",
    )
    ax.legend(frameon=False, loc="upper right")
    ax.yaxis.grid(True, linewidth=0.3, color="#dddddd")
    ax.set_axisbelow(True)
    fig.text(
        0.10,
        0.035,
        "Negative Δ means the midpoint is earlier than the commission date. "
        f"Outside −36 to 36 months: {_fmt_count(metrics['n_below_window'])} below, "
        f"{_fmt_count(metrics['n_above_window'])} above. Totals use every dated parcel.",
        ha="left",
        va="bottom",
        fontsize=7.5,
    )
    save_figure(fig, stem)
    del dated


def _release_text(metrics: dict, sample: pd.DataFrame, classes: pd.DataFrame) -> tuple[str, str]:
    def kind_n(scope: str, group: str) -> int:
        hit = sample.loc[(sample["scope"] == scope) & (sample["group"] == group), "n"]
        return int(hit.iloc[0]) if len(hit) else 0

    def case_n(sample_name: str, tau: int, case: str) -> int:
        hit = classes.loc[
            (classes["sample"] == sample_name) & (classes["tau_days"] == tau) & (classes["case"] == case),
            "n",
        ]
        return int(hit.iloc[0])

    n_main = kind_n("main", "parcels")
    n_dated = metrics["n_dated"]
    lines = []
    for tau in (TAU_DAYS_MAIN, *TAU_DAYS_SENSITIVITY):
        lines.append(
            f"| {int(tau)} | {case_n('main_dated', int(tau), 'registration_lag')} | "
            f"{case_n('main_dated', int(tau), 'agreement')} | "
            f"{case_n('main_dated', int(tau), 'contradiction')} |"
        )
    case_table = "\n".join(lines)
    readme = f"""# Cape Town CV appendix figure D6, batch v3 (2026-10-14)

This folder has one figure, D6. It compares the model interval on a cadastral parcel with the SSEG commission date on that parcel. It is a draft for comment. The counts describe the two dates. They were not reviewed on an aerial image, and they are not a score for the model.

The model is {MODEL_LABEL}. {MODEL_EXCLUDED}

## Please confirm four defaults

These were not restated by the collaborator note, so they are marked as defaults.

1. The counting unit is a cadastral parcel that contains PV, not a polygon and not a building.
2. The stock snapshot used elsewhere in this appendix is 2024-06-30. The SSEG commission dates in this extract end on {metrics['commission_date_max']}, so a later snapshot would make the register look smaller. D6 itself does not use that snapshot. It keeps a commission date when the date is before {D6_COMMISSION_BEFORE}.
3. Model capacity, where the appendix uses it, is census-polygon area times 0.19 kWp per square metre. A density near four hundredths of a kilowatt per square metre was fit to SSEG itself and is not used. D6 does not compare capacity.
4. National load-shedding hours are not drawn. They were not in the files used here.

## Sample

A parcel is in the main sample when it contains exactly one commissioned SSEG record, the record's WKT point falls inside the parcel, and the commission date is before {D6_COMMISSION_BEFORE}. That is {_fmt_count(n_main)} parcels. Commissioned means the status starts with `1.0 ` or `4.0 `. Off-grid rows are not included. Rounded latitude and longitude were not read.

Of these parcels, {_fmt_count(n_dated)} have a dated model interval and are the histogram. {_fmt_count(kind_n('main', 'left_censored'))} were already present before the 2019 satellite window, so they have no interval start and cannot be a contradiction. {_fmt_count(kind_n('main', 'census_bound'))} are known only up to the census date {CENSUS_DATE}. {_fmt_count(kind_n('main', 'mixed'))} have both a dated polygon and a census-bounded polygon. {_fmt_count(kind_n('main', 'undated'))} are undated. {_fmt_count(kind_n('main', 'no_model'))} contain the SSEG point and no census polygon. Those five groups are counted and are not in the histogram.

The parcel interval uses the earliest start and the earliest end among the dated polygons on the parcel. The midpoint is the calendar midpoint of that interval. Where one point fell in more than one parcel, the published Phase 0 assignment is kept.

{_fmt_count(kind_n('main_pre2019', 'parcels'))} main-sample commissions are before 2019. {_fmt_count(kind_n('main_pre2019', 'left_censored'))} of them are left-censored, which is the pattern the 2019 window leads one to expect. {_fmt_count(kind_n('main_pre2019', 'dated'))} of them are dated and stay in the histogram. They are listed in `fig_D6_classes.csv` under `main_pre2019_dated`.

No main-sample commission date is on or after {D6_COMMISSION_BEFORE} (excluded parcels: {kind_n('excluded_cutoff', 'parcels')}). The latest commissioned date in the extract is {metrics['commission_date_max']}.

## What the figure shows

Δ is the midpoint minus the commission date, in months of {D6_MONTH_DAYS:.6f} days (365.2425 / 12). Negative Δ means the midpoint is earlier than the commission date. That is the direction registration lag produces, because a commission date is an upper bound on the install date, not a point estimate.

On the {_fmt_count(n_dated)} dated parcels, the mean is {_fmt_months(metrics['mean_delta_months'])} months and the median is {_fmt_months(metrics['median_delta_months'])} months. The interquartile range is {_fmt_months(metrics['iqr_delta_months'])} months (25th percentile {_fmt_months(metrics['q1_delta_months'])}, 75th percentile {_fmt_months(metrics['q3_delta_months'])}). {_fmt_count(metrics['n_abs_delta_le_6'])} have |Δ| at most 6 months, and {_fmt_count(metrics['n_abs_delta_le_12'])} have |Δ| at most 12 months. The commission date falls inside the closed interval on {_fmt_count(metrics['n_commission_inside_interval'])} parcels.

The black line is the histogram that would arise if each commission date were equally likely to fall on any inclusive calendar day inside that pair's own interval. Pairs with a short interval pile up near zero. The median interval width is {_fmt_months(metrics['median_width_days'])} days, so the line is not a band of fixed width. The drawn window is −36 to 36 months. {_fmt_count(metrics['n_below_window'])} dated parcels fall below it and {_fmt_count(metrics['n_above_window'])} fall above it. The totals above include them.

## Three descriptive cases

tau is 90 days in the main count. Registration lag means the interval end is on or before the commission date. Agreement means the interval start is on or before the commission date plus tau, and the interval end is after the commission date. Contradiction means the interval start is after the commission date plus tau. A contradiction can be a missed panel or a bad match. This batch does not separate those.

| tau (days) | registration lag | agreement | contradiction |
|---|---:|---:|---:|
{case_table}

The median width of {_fmt_months(metrics['median_width_days'])} days sits next to these counts because a wider interval would move parcels out of the contradiction column. The dated share of the main sample is {_fmt_count(n_dated)} / {_fmt_count(n_main)}.

On the { _fmt_count(metrics['lag_n']) } parcels with the interval end on or before the commission date, the lag from the interval end to the commission date has median {_fmt_months(metrics['lag_median_days'])} days ({_fmt_months(metrics['lag_median_months'])} months) and interquartile range {_fmt_months(metrics['lag_iqr_days'])} days.

tau of 0, 30, and 180 days is the sensitivity in the same table. The 10 m buffer, in metres on EPSG:32734, adds {metrics['buffer_added']} parcels and leaves {metrics['buffer_ambiguous']} points ambiguous, because no commissioned point with a coordinate sits outside a parcel. Parcels with more than one commissioned record ({_fmt_count(kind_n('multi', 'parcels'))} parcels, {_fmt_count(metrics['n_multi_records'])} records) use the earliest commission date and are in `fig_D6_classes.csv` under `multi_earliest_dated`. They are not in the histogram.

## Known limits

The published supply-authority polygons do not cover the metro, so this figure does not drop parcels by supply authority. A commissioned SSEG row is a municipal registration. A roof in an Eskom direct-supply area is unlikely to appear here. The gap between the census and this matched sample is mostly systems that were never commissioned in the municipal register.

The census table has 111,801 polygons and the register has 5,458 commissioned rows. This figure uses only the matched one-record parcels. Most detected polygons have no commissioned row.

Left-censored and census-bounded parcels are not given a midpoint in the histogram. Placing either class on a single month would create a false spike.

The Top-52 cohort has no per-visit sequence in the imaging file used by the timing figure. D6 uses the published interval on each polygon, whichever cohort it came from. Cohort counts for the dated parcels are in `fig_D6_sample.csv`.

SSEG coordinates are known to be wrong for some rows. The commissioned point-in-parcel match rate is 5,146 / 5,458. Rows without a WKT point stay unmatched. This figure does not correct a coordinate by hand.
"""
    captions = f"""# Captions, batch v3

## Figure D6

Histogram of Δ, the calendar midpoint of a parcel's model interval minus the SSEG commission date, in months of {D6_MONTH_DAYS:.6f} days. The parcel is in the histogram when it has exactly one commissioned SSEG record inside it, the commission date is before {D6_COMMISSION_BEFORE}, and every model polygon used for the interval is dated (N = {_fmt_count(n_dated)}). Parcels already present before 2019, parcels known only up to the census date, mixed parcels, undated parcels, and parcels with no model polygon are counted in the README and are not bars. The black line gives, for each pair, equal weight to every inclusive calendar day inside that pair's own interval; it is taller near zero because the median interval is {_fmt_months(metrics['median_width_days'])} days. Negative Δ means the midpoint is earlier than the commission date. Mean {_fmt_months(metrics['mean_delta_months'])} months, median {_fmt_months(metrics['median_delta_months'])} months, interquartile range {_fmt_months(metrics['iqr_delta_months'])} months. {_fmt_count(metrics['n_abs_delta_le_6'])} parcels have |Δ| at most 6 months and {_fmt_count(metrics['n_abs_delta_le_12'])} have |Δ| at most 12 months. The commission date lies inside the interval for {_fmt_count(metrics['n_commission_inside_interval'])} parcels. The window is −36 to 36 months; {_fmt_count(metrics['n_below_window'])} parcels are below it and {_fmt_count(metrics['n_above_window'])} are above it, and the totals include them. At 90 days, {_fmt_count(case_n('main_dated', 90, 'registration_lag'))} parcels have the interval end on or before the commission date, {_fmt_count(case_n('main_dated', 90, 'agreement'))} agree within the tolerance, and {_fmt_count(case_n('main_dated', 90, 'contradiction'))} have an interval start more than 90 days after the commission date. Those three counts were not checked against an aerial image.
"""
    return readme, captions


def prepare() -> None:
    if RELEASE.name != "release_v3":
        raise Stop(f"STOP: refusing to write {RELEASE}.")
    head = git_commit()
    STAGING.mkdir(parents=True, exist_ok=True)
    hashes = validate_inputs()
    census, sseg = load_links()
    parcels = parcel_intervals(census)
    compared = build_comparisons(sseg, parcels)
    dated = compared["dated"]
    hist, hist_info = _hist_frame(dated)
    metrics = _summary_metrics(dated, hist_info)
    metrics["n_main"] = int(len(compared["main"]))
    metrics["dated_share_of_main"] = metrics["n_dated"] / metrics["n_main"]
    with_model = int(compared["main"]["parcel_kind"].notna().sum())
    metrics["n_main_with_model"] = with_model
    metrics["dated_share_of_main_with_model"] = metrics["n_dated"] / with_model
    metrics["n_excluded_cutoff"] = int(len(compared["excluded"]))
    metrics["n_pre2019"] = int(len(compared["pre2019"]))
    metrics["n_pre2019_dated"] = int(len(compared["pre2019_dated"]))
    metrics["n_multi_parcels"] = int(len(compared["multi"]))
    metrics["n_multi_records"] = int(compared["n_multi_records"])
    metrics["n_multi_dated"] = int(len(compared["multi_dated"]))
    metrics["buffer_m"] = BUFFER_M_SENSITIVITY
    metrics["buffer_added"] = int(compared["buffer_added"])
    metrics["buffer_ambiguous"] = int(compared["buffer_ambiguous"])
    metrics["buffer_crs"] = "EPSG:32734"
    metrics["month_days"] = D6_MONTH_DAYS
    metrics["tau_days_main"] = TAU_DAYS_MAIN
    metrics["commission_before"] = D6_COMMISSION_BEFORE
    metrics["census_date"] = CENSUS_DATE
    metrics["model_window_start"] = WINDOW_START.date().isoformat()
    commissioned = sseg.loc[sseg["commissioned"], "commission_date"]
    metrics["commission_date_min"] = pd.to_datetime(commissioned).min().date().isoformat()
    metrics["commission_date_max"] = pd.to_datetime(commissioned).max().date().isoformat()
    metrics["render_commit"] = head
    metrics["main_kinds"] = _kind_counts(compared["main"])
    metrics["pre2019_kinds"] = _kind_counts(compared["pre2019"])
    metrics["cohort_dated"] = _cohort_counts(census, dated["objectid"])

    sample_rows = []
    sample_rows.extend(_sample_rows("main", compared["main"]))
    sample_rows.extend(_sample_rows("main_pre2019", compared["pre2019"]))
    sample_rows.extend(_sample_rows("multi", compared["multi"]))
    sample_rows.extend(_sample_rows("excluded_cutoff", compared["excluded"]))
    for cohort, count in sorted(metrics["cohort_dated"].items()):
        sample_rows.append({"scope": "main_dated_cohort", "group": cohort, "n": int(count)})
    sample_rows.append(
        {
            "scope": "commissioned_records",
            "group": "unmatched_no_wkt",
            "n": int((sseg["commissioned"] & sseg["objectid"].isna()).sum()),
        }
    )
    sample = pd.DataFrame(sample_rows)

    class_rows: list[dict] = []
    for tau in (TAU_DAYS_MAIN, *TAU_DAYS_SENSITIVITY):
        class_rows.extend(_case_rows("main_dated", dated[f"case_{int(tau)}"], int(tau)))
        class_rows.extend(_case_rows("buffer_dated", dated[f"case_{int(tau)}"], int(tau)))
        if len(compared["multi_dated"]):
            labels = _labelled(compared["multi_dated"], int(tau))
        else:
            labels = pd.Series(dtype=object)
        class_rows.extend(_case_rows("multi_earliest_dated", labels, int(tau)))
        if len(compared["pre2019_dated"]):
            pre_labels = compared["pre2019_dated"][f"case_{int(tau)}"]
        else:
            pre_labels = pd.Series(dtype=object)
        class_rows.extend(_case_rows("main_pre2019_dated", pre_labels, int(tau)))
    classes = pd.DataFrame(class_rows)
    # The buffer added nobody, so its case counts have to equal the main counts.
    main_cases = classes.loc[classes["sample"] == "main_dated", ["tau_days", "case", "n"]]
    buffer_cases = classes.loc[classes["sample"] == "buffer_dated", ["tau_days", "case", "n"]]
    if not main_cases.reset_index(drop=True).equals(buffer_cases.reset_index(drop=True)):
        raise Stop("STOP: buffer case counts differ from the main counts, but the buffer added no points.")

    summary = _long_metrics(metrics)
    hist.to_csv(STAGING / "fig_D6_hist.csv", index=False)
    summary.to_csv(STAGING / "fig_D6_summary.csv", index=False)
    classes.to_csv(STAGING / "fig_D6_classes.csv", index=False)
    sample.to_csv(STAGING / "fig_D6_sample.csv", index=False)
    pair_cols = [
        "objectid",
        "L",
        "U",
        "C",
        "midpoint",
        "delta_months",
        "width_days",
        "commission_inside",
        "case_0",
        "case_30",
        "case_90",
        "case_180",
    ]
    dated.loc[:, pair_cols].to_parquet(STAGING / "d6_dated_pairs.parquet", index=False)
    plot_d6(dated, hist, metrics, STAGING / "fig_D6_commission_delta")
    png_mm = _png_mm(STAGING / "fig_D6_commission_delta.png")
    pdf_mm = _pdf_mm(STAGING / "fig_D6_commission_delta.pdf")
    if abs(png_mm[0] - FIG_WIDTH_MM) > 0.15 or abs(pdf_mm[0] - FIG_WIDTH_MM) > 0.15:
        raise Stop(f"STOP: figure width is PNG {png_mm} PDF {pdf_mm}, not {FIG_WIDTH_MM} mm.")
    metrics["png_mm"] = [round(png_mm[0], 2), round(png_mm[1], 2)]
    metrics["pdf_mm"] = [round(pdf_mm[0], 2), round(pdf_mm[1], 2)]
    readme, captions = _release_text(metrics, sample, classes)
    (STAGING / "README.md").write_text(readme)
    (STAGING / "captions.md").write_text(captions)
    write_json(
        STAGING / "d6_numbers.json",
        {
            "render_commit": head,
            "inputs": hashes,
            "metrics": metrics,
            "deviations": DEVIATIONS,
        },
    )
    print("PREPARED", STAGING, "HEAD", head, flush=True)
    print(
        f"dated {metrics['n_dated']} mean {metrics['mean_delta_months']:.4f} "
        f"median {metrics['median_delta_months']:.4f} iqr {metrics['iqr_delta_months']:.4f}",
        flush=True,
    )


def assemble() -> None:
    if RELEASE.name != "release_v3":
        raise Stop(f"STOP: refusing to write {RELEASE}.")
    required = [
        STAGING / "fig_D6_commission_delta.png",
        STAGING / "fig_D6_commission_delta.pdf",
        STAGING / "fig_D6_hist.csv",
        STAGING / "fig_D6_summary.csv",
        STAGING / "fig_D6_classes.csv",
        STAGING / "fig_D6_sample.csv",
        STAGING / "README.md",
        STAGING / "captions.md",
        STAGING / "d6_numbers.json",
    ]
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise Stop("STOP: assemble is missing " + ", ".join(missing))
    if RELEASE.exists() and any(RELEASE.iterdir()):
        raise Stop(f"STOP: {RELEASE} already exists. Refusing to overwrite a batch.")
    head = git_commit()
    numbers = json.loads((STAGING / "d6_numbers.json").read_text())
    if numbers.get("render_commit") != head:
        raise Stop(
            "STOP: render_commit does not equal HEAD. Draw the figure again from this commit "
            f"before assemble.\nHEAD {head}\nrender {numbers.get('render_commit')}"
        )
    summary = pd.read_csv(STAGING / "fig_D6_summary.csv")
    looked = summary.set_index("metric")["value"]
    if int(float(looked["n_dated"])) != int(numbers["metrics"]["n_dated"]):
        raise Stop("STOP: fig_D6_summary.csv does not match d6_numbers.json.")
    for name in ("README.md", "captions.md"):
        text = (STAGING / name).read_text().lower()
        if "accuracy" in text or "error rate" in text:
            raise Stop(f"STOP: {name} uses a score word this batch does not claim.")
    RELEASE.mkdir(parents=True)
    (RELEASE / "figs").mkdir()
    (RELEASE / "data").mkdir()
    for name in ("fig_D6_commission_delta.png", "fig_D6_commission_delta.pdf"):
        (RELEASE / "figs" / name).write_bytes((STAGING / name).read_bytes())
    for name in ("fig_D6_hist.csv", "fig_D6_summary.csv", "fig_D6_classes.csv", "fig_D6_sample.csv"):
        (RELEASE / "data" / name).write_bytes((STAGING / name).read_bytes())
    (RELEASE / "README.md").write_text((STAGING / "README.md").read_text())
    (RELEASE / "captions.md").write_text((STAGING / "captions.md").read_text())
    manifest = {
        "batch": "v3",
        "revision": "v3",
        "revision_date": "2026-10-14",
        "date": "2026-10-14",
        "code_commit": head,
        "render_commit": head,
        "constants": constants_payload(),
        "inputs": numbers["inputs"],
        "d6": numbers["metrics"],
        "deviations": numbers["deviations"],
    }
    write_json(RELEASE / "release_manifest.json", manifest)
    lines = []
    for path in sorted(RELEASE.rglob("*")):
        if path.is_file() and path.name != "SHA256SUMS.txt":
            lines.append(f"{sha256_file(path)}  {path.relative_to(RELEASE).as_posix()}")
    (RELEASE / "SHA256SUMS.txt").write_text("\n".join(lines) + "\n")
    print("ASSEMBLED", RELEASE, flush=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("step", choices=["prepare", "assemble", "all"])
    args = parser.parse_args()
    try:
        if args.step in ("prepare", "all"):
            prepare()
        if args.step in ("assemble", "all"):
            assemble()
    except Stop as exc:
        print(exc, flush=True)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
