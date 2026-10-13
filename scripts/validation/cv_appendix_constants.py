"""Frozen definitions for the CV appendix figures (D1–D6).

Every figure reads these constants. The values are the 2026-10-13 execution
plan, section 2, plus the acceptance totals in section 8. Changing a value
is a stop-and-report event, not a silent edit.

Plan: docs/replan_v2/PLAN-cv-appendix-figures-execution-2026-10-13.md
"""

from __future__ import annotations

from pathlib import Path

PLAN_ID = "PLAN-cv-appendix-figures-execution-2026-10-13"
BATCH_DATE = "2026-10-13"

# --- inputs (read-only) ----------------------------------------------------
CITYWIDE_CSV = Path(
    "~/Dropbox/RA_Solar/Gao/backdating/"
    "ct_citywide_install_dated_2026-09-02_research_preview/"
    "ct_citywide_install_dated_20260902.csv"
).expanduser()
CITYWIDE_SHA256 = "c0bf9ed149fb73a97f34354c82f881df3f45769cfafe1e44e9fc68b1e29e7563"
CITYWIDE_ROWS = 111_801

SSEG_CSV = Path("~/zasolar_data/sseg/sseg_registration_geo_2026-10-13.csv").expanduser()
SSEG_SHA256 = "40c54ab824cb74d5561359b03dcb2f909e3021352ef6de7312d088dc33fa1b14"
SSEG_COMMISSIONED_ROWS = 5_458

VISITS_CSV = Path(
    "~/Dropbox/RA_Solar/Gao/backdating/"
    "ct_citywide_27wave_robustness_2026-09-23/ct_27wave_visits_20260923.csv"
).expanduser()

CENSUS_GRID_GPKG = Path("/home/gao/projects/ZAsolar/data/task_grid_cpt.gpkg")
GEOBOUNDARIES_ADM2 = Path(
    "~/zasolar_data/geid_temporal/vexcel_muni_h3r7_2026-09-06/raw/"
    "geoboundaries_zaf_adm2.geojson"
).expanduser()
CITY_BOUNDARY_NAME = "City of Cape Town"

# Acceptance class counts (plan section 8). Key "undated" is a blank date_status.
CLASS_COUNTS = {
    "done_appears": 73_336,
    "done_installed_during_census": 27_591,
    "done_already_present_before_geid_history": 9_843,
    "undated": 1_031,
}

# --- model / SSEG cohort ---------------------------------------------------
# Citywide table: Gemini 3.1 Flash Lite, with 3.6-high rescue. 3.5 is the
# ten-grid set only and is not an input to these figures.
MODEL_LABEL = "Gemini 3.1 Flash Lite, with Gemini 3.6 high rescue"
MODEL_EXCLUDED = "Gemini 3.5 Flash Lite (ten-grid only; not in these figures)"

# status startswith these prefixes (the trailing space avoids 10.0 / 40.0).
COMMISSIONED_PREFIXES = ("1.0 ", "4.0 ")
OFFGRID_PREFIX = "2.0 "

# --- dates -----------------------------------------------------------------
MAIN_SNAPSHOT = "2024-06-30"
PANEL_SNAPSHOTS = (
    "2020-01-31",
    "2021-01-31",
    "2022-01-31",
    "2023-01-31",
    "2024-01-31",
    "2024-06-30",
)
CENSUS_DATE = "2025-01-31"
HISTOGRAM_MONTH_START = "2019-01"
HISTOGRAM_MONTH_END = "2025-01"
CUMULATIVE_MONTH_END = "2024-06"
# D6 later: commission must be at least three months before the census day.
D6_COMMISSION_BEFORE = "2024-10-31"

# Stock rule names. "lower" counts a polygon only when the interval end is on
# or before the snapshot. "upper" counts it when the interval start is on or
# before the snapshot. "midpoint" is the main rule.
STOCK_RULE_MAIN = "midpoint"
STOCK_RULES = ("midpoint", "lower", "upper")

# --- capacity --------------------------------------------------------------
# kWp per square metre of census polygon. Not the SSEG-fitted density.
CAPACITY_KWP_PER_M2 = 0.19

# --- space -----------------------------------------------------------------
H3_RESOLUTION = 9
# CPT cells in task_grid_cpt.gpkg with at least this imagery fraction are the
# census aerial footprint. H3-9 cells must lie inside that footprint.
FULL_COVERAGE_MIN_FRACTION = 0.999
# Share of an H3 cell that must sit inside the footprint.
CONTAINMENT_MIN_AREA_FRACTION = 0.999
# A cell is treated as Eskom direct supply when at least this share of its
# area lies in an Eskom supply-authority polygon. Applied only when
# supply_layer_covers_city accepts the layer. The published ODP_SPLIT_1
# layer 3 polygons do not cover the metro, so batch v1 does not apply it.
ESKOM_EXCLUDE_AREA_FRACTION = 0.5
ESKOM_AUTHORITY = "Eskom"
CCT_AUTHORITY = "CCT"
# The supply layer covers the metro only if every required city location
# falls inside some authority polygon and the share of commissioned SSEG
# points outside both polygons is below this rate.
SUPPLY_COVERS_CITY_MAX_OUTSIDE_RATE = 0.20

# A cell is residential when at least this share of its parcels have a
# residential primary zone. See cv_appendix_rules.primary_zone.
RESIDENTIAL_CELL_MIN_SHARE = 0.5

# OLS bin count for the binned scatter. A point mass at zero is its own bin
# and is not one of these quantile bins.
N_QUANTILE_BINS = 20
CROSSCORR_MAX_LAG = 12

# Section B, frozen. Used by the reusable match helpers. v1 figures do not
# apply tau; D6 will.
TAU_DAYS_MAIN = 90
TAU_DAYS_SENSITIVITY = (0, 30, 180)
BUFFER_M_SENSITIVITY = 10.0

# --- parcels service -------------------------------------------------------
PARCEL_QUERY_URL = (
    "https://citymaps.capetown.gov.za/agsext/rest/services/"
    "Theme_Based/ODP_SPLIT_4/MapServer/0/query"
)
PARCEL_LAYER_URL = (
    "https://citymaps.capetown.gov.za/agsext/rest/services/"
    "Theme_Based/ODP_SPLIT_4/MapServer/0"
)
SUPPLY_QUERY_URL = (
    "https://citymaps.capetown.gov.za/agsext/rest/services/"
    "Theme_Based/ODP_SPLIT_1/MapServer/3/query"
)
PARCEL_SERVER_COUNT = 835_018
PARCEL_PAGE_SIZE = 2_000
PARCEL_FIELDS = ("OBJECTID", "SG26_CODE", "ZONING", "SHAPE_Area")
MATCH_RATE_MIN = 0.90

# --- outputs ---------------------------------------------------------------
WORK_ROOT = Path("~/zasolar_data/geid_temporal/cv_appendix_20261013").expanduser()
FIG_WIDTH_MM = 180
FIG_DPI = 300

# SSEG columns the loaders may read. latitude / longitude are rounded to
# three decimals and must not be added here.
SSEG_USECOLS = (
    "status",
    "date_commissioned",
    "generator_capacity_(va)",
    "wkt",
    "h3_level_9",
    "geo_source",
    "electricity_region",
)

CITYWIDE_USECOLS = (
    "source_feature_id",
    "source_grid",
    "centroid_lon",
    "centroid_lat",
    "area_m2",
    "date_status",
    "install_date",
    "install_interval_start",
    "install_interval_end",
    "latest_absent_date",
    "earliest_present_date",
    "left_censored",
    "census_bound",
    "cohort",
    "wave",
    "census_cutoff",
)


def constants_payload() -> dict:
    """JSON-ready copy of the definitions that decide the numbers."""
    return {
        "plan_id": PLAN_ID,
        "model_label": MODEL_LABEL,
        "model_excluded": MODEL_EXCLUDED,
        "commissioned_prefixes": list(COMMISSIONED_PREFIXES),
        "offgrid_prefix": OFFGRID_PREFIX,
        "main_snapshot": MAIN_SNAPSHOT,
        "panel_snapshots": list(PANEL_SNAPSHOTS),
        "census_date": CENSUS_DATE,
        "histogram_month_start": HISTOGRAM_MONTH_START,
        "histogram_month_end": HISTOGRAM_MONTH_END,
        "cumulative_month_end": CUMULATIVE_MONTH_END,
        "stock_rule_main": STOCK_RULE_MAIN,
        "capacity_kwp_per_m2": CAPACITY_KWP_PER_M2,
        "counting_unit": "cadastral parcel with PV",
        "sseg_coordinate_column": "wkt",
        "h3_resolution": H3_RESOLUTION,
        "full_coverage_min_fraction": FULL_COVERAGE_MIN_FRACTION,
        "containment_min_area_fraction": CONTAINMENT_MIN_AREA_FRACTION,
        "eskom_exclude_area_fraction": ESKOM_EXCLUDE_AREA_FRACTION,
        "supply_covers_city_max_outside_rate": SUPPLY_COVERS_CITY_MAX_OUTSIDE_RATE,
        "residential_cell_min_share": RESIDENTIAL_CELL_MIN_SHARE,
        "n_quantile_bins": N_QUANTILE_BINS,
        "crosscorr_max_lag_months": CROSSCORR_MAX_LAG,
        "tau_days_main": TAU_DAYS_MAIN,
        "tau_days_sensitivity": list(TAU_DAYS_SENSITIVITY),
        "buffer_m_sensitivity": BUFFER_M_SENSITIVITY,
        "d6_commission_before": D6_COMMISSION_BEFORE,
        "parcel_server_count_expected": PARCEL_SERVER_COUNT,
        "match_rate_min": MATCH_RATE_MIN,
        "class_counts_expected": CLASS_COUNTS,
        "citywide_rows_expected": CITYWIDE_ROWS,
        "citywide_sha256": CITYWIDE_SHA256,
        "sseg_sha256": SSEG_SHA256,
        "sseg_commissioned_rows_expected": SSEG_COMMISSIONED_ROWS,
        "fig_width_mm": FIG_WIDTH_MM,
        "fig_dpi": FIG_DPI,
    }
