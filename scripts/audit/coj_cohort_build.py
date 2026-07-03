"""Cohort builder + layer plan + negative controls for the CoJ audit at
cohort scale (ISSUE-09, WP-A).

This module scales the ISSUE-08 pilot's sampling stage to the full **dated**
JHB inventory. It answers three questions, all as pure, unit-testable
functions:

  1. Which cohort stratum does a dated anchor belong to?
     (`assign_cohort_stratum`, design §4c)
  2. Which CoJ layer-years should be fetched for it, and what role does each
     fetch play? (`plan_units_for_anchor`, design §2 layer plan +
     `PlannedUnit`)
  3. Which anchors enter the seeded 2019 falsification subsample?
     (`select_falsification_subsample`, design §2)

plus the negative-control geometry (design §3), expressed as four pure shapely
helpers (`lattice_centers` / `exclude_near` / `sample_negative_controls` /
`nc_bbox_4326`) so the STRtree buffer-exclude and UTM<->WGS84 reprojection are
testable with tiny synthetic shapes and no gpkg I/O.

`build_cohort` is the thin I/O wrapper (fiona + shapely for the two gpkgs, csv
for everything else) that stitches these together and writes the four build
artifacts (`cohort_anchors.csv`, `cohort_units.csv`, `negative_controls.csv`,
`dropped_units_summary.json`, design §4d). It is deliberately not unit-tested
— its pure ingredients are — and is exercised end-to-end by the WP-E smoke run.

Layer-plan semantics (design §2, verified against `install_intervals.csv`:
988 pre2019 / 3568 2019_2022 / 7577 probe_2023 / 57 s3like = 12,190 dated):

  * 2023 is fetched for **every** dated anchor (bisector / falsification /
    headline).
  * 2019 is fetched when the anchor is present-side calibration
    (`end < 2019-01-01`), a genuine 2019 bisector
    (`start < 2019-01-01 AND end > 2019-12-31`), s3-like (`start > 2023-12-31`),
    or flagged into the seeded falsification subsample (`in_fals_2019`).
  * 2015 is fetched only for the deep pre-2019 intervals (`end < 2019-01-01`).

`unit_purpose` follows the per-unit expectation amendment (overrides design §5
gate (a)'s per-stratum-blanket first bullet): a (anchor, year Y) unit's base
role is `calibration_present` if `install_interval_end < Jan-1-Y` (⇒ known
present at Y), else `falsification` if `install_interval_start > Dec-31-Y`
(⇒ pipeline claims absent at Y), else `bisector` (the interval straddles Y).
s3-like units are relabelled `findings_s3like`; a `c_probe_2023` bisector at
2023 is relabelled `headline_2023`. Consequently the 2015 unit of a
`c_cal_present_pre2019` anchor only carries a present-expectation when
`end < 2015-01-01`; the rest are bisectors/falsifications.

Date parsing is reused verbatim (read-only import) from the pilot's
`coj_audit_join._parse_date` so the two stages agree byte-for-byte on how an
ISO / empty / ``nan`` interval bound is interpreted.
"""
from __future__ import annotations

import csv
import json
import random
from collections import Counter
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Iterable

from pyproj import Transformer

# Read-only reuse of the pilot primitives (do NOT re-derive these):
from scripts.audit.coj_audit_join import EXCLUDED_STATUSES, _parse_date

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

LAYER_YEARS: tuple[int, int, int] = (2015, 2019, 2023)

C_CAL_PRESENT_PRE2019 = "c_cal_present_pre2019"
C_CAL_PRESENT_2019_2022 = "c_cal_present_2019_2022"
C_PROBE_2023 = "c_probe_2023"
C_FINDINGS_S3LIKE = "c_findings_s3like"
C_NEGATIVE_CONTROL = "c_negative_control"

COHORT_STRATA: tuple[str, ...] = (
    C_CAL_PRESENT_PRE2019,
    C_CAL_PRESENT_2019_2022,
    C_PROBE_2023,
    C_FINDINGS_S3LIKE,
    C_NEGATIVE_CONTROL,
)

# unit_purpose enum (design §4a)
PURPOSE_CALIBRATION = "calibration_present"
PURPOSE_BISECTOR = "bisector"
PURPOSE_FALSIFICATION = "falsification"
PURPOSE_FINDINGS_S3LIKE = "findings_s3like"
PURPOSE_HEADLINE_2023 = "headline_2023"
PURPOSE_NEGATIVE_CONTROL = "negative_control"

# The dated inventory (design §1): everything else is out of scope.
DATED_STATUSES: frozenset[str] = frozenset(
    {"done_appears", "done_already_present_before_geid_history"}
)

DEFAULT_CHIP_HALF_M = 48.0

_D_2015_01_01 = date(2015, 1, 1)
_D_2019_01_01 = date(2019, 1, 1)
_D_2019_12_31 = date(2019, 12, 31)
_D_2023_01_01 = date(2023, 1, 1)
_D_2023_12_31 = date(2023, 12, 31)


@dataclass(frozen=True)
class PlannedUnit:
    """One planned (anchor, layer-year) fetch/score unit."""

    anchor_id: str
    year: int
    stratum: str
    unit_purpose: str


# ---------------------------------------------------------------------------
# Cohort stratum assignment (design §4c)
# ---------------------------------------------------------------------------


def assign_cohort_stratum(row: dict) -> str | None:
    """Assign one ``install_intervals.csv`` row to a cohort stratum, or ``None``.

    ``None`` means the row is out of scope for the contradiction dataset: an
    excluded status, a non-dated status (``done_ambiguous_*``), or an
    unparseable ``install_interval_end`` (every stratum boundary keys on the
    earliest-present bound). Negative controls are generated, not classified
    here, so ``c_negative_control`` is never returned.

    Boundaries (mirroring the pilot's strict-inequality style):
      * ``end < 2019-01-01``                       -> pre2019
      * ``2019-01-01 <= end < 2023-01-01``         -> 2019_2022
      * ``end >= 2023-01-01 AND start > 2023-12-31`` -> s3like
      * ``end >= 2023-01-01`` otherwise             -> probe_2023
    """
    status = row.get("status")
    if status in EXCLUDED_STATUSES or status not in DATED_STATUSES:
        return None

    end = _parse_date(row.get("install_interval_end"))
    if end is None:
        return None
    start = _parse_date(row.get("install_interval_start"))

    if end < _D_2019_01_01:
        return C_CAL_PRESENT_PRE2019
    if end < _D_2023_01_01:
        return C_CAL_PRESENT_2019_2022
    # end >= 2023-01-01: s3-like requires a real start strictly past 2023.
    if start is not None and start > _D_2023_12_31:
        return C_FINDINGS_S3LIKE
    return C_PROBE_2023


# ---------------------------------------------------------------------------
# Layer plan (design §2) + unit_purpose (per-unit amendment)
# ---------------------------------------------------------------------------


def _base_purpose(start: date | None, end: date | None, year: int) -> str:
    """Precedence rule: calibration_present > falsification > bisector."""
    if end is not None and end < date(year, 1, 1):
        return PURPOSE_CALIBRATION
    if start is not None and start > date(year, 12, 31):
        return PURPOSE_FALSIFICATION
    return PURPOSE_BISECTOR


def _unit_purpose(start: date | None, end: date | None, year: int, stratum: str) -> str:
    if stratum == C_FINDINGS_S3LIKE:
        return PURPOSE_FINDINGS_S3LIKE
    base = _base_purpose(start, end, year)
    if stratum == C_PROBE_2023 and year == 2023 and base == PURPOSE_BISECTOR:
        return PURPOSE_HEADLINE_2023
    return base


def _plan_2019(
    start: date | None, end: date | None, stratum: str, in_fals_2019: bool
) -> bool:
    """Whether the 2019 layer is fetched for this anchor (design §2)."""
    if end is not None and end < _D_2019_01_01:  # present-side calibration
        return True
    if (  # genuine 2019 bisector (needs a real start strictly before 2019)
        start is not None
        and end is not None
        and start < _D_2019_01_01
        and end > _D_2019_12_31
    ):
        return True
    if stratum == C_FINDINGS_S3LIKE:  # deep falsification
        return True
    if in_fals_2019:  # seeded falsification subsample
        return True
    return False


def plan_units_for_anchor(row: dict, *, in_fals_2019: bool) -> list[PlannedUnit]:
    """Plan the CoJ layer-years to fetch for one dated anchor.

    Returns the planned units in ascending layer-year order (2015, 2019,
    2023). An out-of-scope row (``assign_cohort_stratum`` -> ``None``) yields
    ``[]``. ``in_fals_2019`` is the per-anchor flag emitted by
    ``select_falsification_subsample`` — when set it forces a 2019 unit whose
    purpose is derived from the interval like any other.
    """
    stratum = assign_cohort_stratum(row)
    if stratum is None:
        return []

    anchor_id = row["anchor_id"]
    start = _parse_date(row.get("install_interval_start"))
    end = _parse_date(row.get("install_interval_end"))

    units: list[PlannedUnit] = []
    if end is not None and end < _D_2019_01_01:  # 2015 only for deep pre-2019
        units.append(PlannedUnit(anchor_id, 2015, stratum, _unit_purpose(start, end, 2015, stratum)))
    if _plan_2019(start, end, stratum, in_fals_2019):
        units.append(PlannedUnit(anchor_id, 2019, stratum, _unit_purpose(start, end, 2019, stratum)))
    units.append(PlannedUnit(anchor_id, 2023, stratum, _unit_purpose(start, end, 2023, stratum)))
    return units


# ---------------------------------------------------------------------------
# Seeded 2019 falsification subsample (design §2)
# ---------------------------------------------------------------------------


def _distribute(remainder: int, caps: dict[int, int]) -> dict[int, int]:
    """Largest-remainder (Hamilton) distribution of ``remainder`` units across
    ``caps`` keys, weighted by and capped at each key's capacity.

    Deterministic: fractional-remainder desc, tie-broken by key asc.
    """
    keys = sorted(caps)
    total_cap = sum(caps.values())
    if remainder <= 0 or total_cap <= 0:
        return {k: 0 for k in keys}
    remainder = min(remainder, total_cap)
    raw = {k: remainder * caps[k] / total_cap for k in keys}
    alloc = {k: min(caps[k], int(raw[k])) for k in keys}
    leftover = remainder - sum(alloc.values())
    order = sorted(keys, key=lambda k: (-(raw[k] - int(raw[k])), k))
    idx = 0
    while leftover > 0 and any(alloc[k] < caps[k] for k in keys):
        k = order[idx % len(order)]
        if alloc[k] < caps[k]:
            alloc[k] += 1
            leftover -= 1
        idx += 1
    return alloc


def _stratum_draw_counts(
    pool_sizes: dict[int, int], *, n_target: int, floor: int
) -> dict[int, int]:
    """Per-start-year draw counts: a floor per stratum, remainder proportional.

    If the floors alone would exceed ``n_target`` the floor is abandoned and
    ``n_target`` is distributed proportional to pool size instead (both capped
    at the available pool).
    """
    years = sorted(pool_sizes)
    floored = {yr: min(floor, pool_sizes[yr]) for yr in years}
    if sum(floored.values()) >= n_target:
        return _distribute(n_target, dict(pool_sizes))
    remainder = n_target - sum(floored.values())
    caps = {yr: pool_sizes[yr] - floored[yr] for yr in years}
    extra = _distribute(remainder, caps)
    return {yr: floored[yr] + extra[yr] for yr in years}


def select_falsification_subsample(
    rows: list[dict],
    *,
    seed: int,
    n_target: int = 1200,
    per_stratum_floor: int = 150,
) -> set[str]:
    """Draw the seeded 2019 falsification subsample.

    Frame = dated anchors the pipeline claims still-absent through 2019
    (``install_interval_start`` in ``(2019-12-31, 2023-12-31]``), which
    excludes s3-like (``start > 2023-12-31``) automatically. Stratified by
    ``install_interval_start`` year with a floor of ``per_stratum_floor`` per
    year, the remainder distributed proportional to remaining pool size up to
    ``n_target`` (design §2 gives 2020/2021/2022/2023 pools 772/1471/6890/1212
    -> ~188/231/565/216 at n_target=1200, floor=150).

    Deterministic for a fixed ``seed``: each start-year pool is sorted by
    ``anchor_id`` before a single ``random.Random(seed)`` draws from it in
    ascending-year order. Returns the set of selected ``anchor_id``s.
    """
    by_year: dict[int, list[str]] = {}
    for row in rows:
        if assign_cohort_stratum(row) is None:
            continue  # out of scope (excluded / non-dated / unparseable)
        start = _parse_date(row.get("install_interval_start"))
        if start is None:
            continue
        if start > _D_2019_12_31 and start <= _D_2023_12_31:
            by_year.setdefault(start.year, []).append(row["anchor_id"])

    if not by_year:
        return set()

    pool_sizes = {yr: len(ids) for yr, ids in by_year.items()}
    draw_counts = _stratum_draw_counts(
        pool_sizes, n_target=n_target, floor=per_stratum_floor
    )

    rng = random.Random(seed)
    selected: set[str] = set()
    for yr in sorted(by_year):
        pool = sorted(by_year[yr])
        k = draw_counts[yr]
        if k >= len(pool):
            selected.update(pool)
        elif k > 0:
            selected.update(rng.sample(pool, k))
    return selected


# ---------------------------------------------------------------------------
# Negative-control geometry (design §3) — pure shapely, no I/O
# ---------------------------------------------------------------------------


def lattice_centers(surveyed_union, *, spacing_m: float) -> list[tuple[float, float]]:
    """Regular ``spacing_m`` lattice of candidate centers strictly inside
    ``surveyed_union`` (a shapely geometry in the region's metric CRS).

    Row-major deterministic order (x outer, y inner). Only strictly-interior
    points are kept (``geometry.contains``) so a control never lands on the
    survey boundary.
    """
    from shapely.geometry import Point  # local: keep module import light

    minx, miny, maxx, maxy = surveyed_union.bounds
    centers: list[tuple[float, float]] = []
    eps = 1e-9
    x = minx
    while x <= maxx + eps:
        y = miny
        while y <= maxy + eps:
            if surveyed_union.contains(Point(x, y)):
                centers.append((x, y))
            y += spacing_m
        x += spacing_m
    return centers


def _as_tree(detection_index_or_polys):
    """Return (STRtree, geometries-list) from either a prebuilt STRtree or an
    iterable of shapely polygons."""
    from shapely.strtree import STRtree

    if isinstance(detection_index_or_polys, STRtree):
        return detection_index_or_polys, list(detection_index_or_polys.geometries)
    geoms = list(detection_index_or_polys)
    return STRtree(geoms), geoms


def exclude_near(
    centers: Iterable[tuple[float, float]],
    detection_index_or_polys,
    *,
    buffer_m: float,
) -> list[tuple[float, float]]:
    """Drop candidate centers within ``buffer_m`` of any detection (inclusive).

    ``detection_index_or_polys`` may be a prebuilt ``shapely.strtree.STRtree``
    or any iterable of shapely geometries (all in the same metric CRS as the
    centers). The STRtree pre-filters by bounding box; exact planar distance
    then decides, with ``distance <= buffer_m`` excluded.
    """
    from shapely.geometry import Point

    tree, geoms = _as_tree(detection_index_or_polys)
    survivors: list[tuple[float, float]] = []
    for x, y in centers:
        pt = Point(x, y)
        near = False
        for i in tree.query(pt.buffer(buffer_m)):
            if geoms[int(i)].distance(pt) <= buffer_m:
                near = True
                break
        if not near:
            survivors.append((x, y))
    return survivors


def sample_negative_controls(
    surviving: list[tuple[float, float]], *, seed: int, n: int
) -> list[tuple[float, float]]:
    """Seeded draw of ``n`` control centers from the survivors.

    Deterministic for a fixed ``seed`` (the survivor list is sorted before the
    ``random.Random(seed)`` draw); the selection is returned sorted so nc ids
    map to it stably. Returns all survivors when ``n >= len(surviving)``.
    """
    ordered = sorted(surviving)
    if n >= len(ordered):
        return ordered
    rng = random.Random(seed)
    return sorted(rng.sample(ordered, n))


@lru_cache(maxsize=8)
def _utm_to_wgs84(utm_epsg: int) -> Transformer:
    return Transformer.from_crs(f"EPSG:{utm_epsg}", "EPSG:4326", always_xy=True)


def nc_bbox_4326(
    center_utm: tuple[float, float], *, chip_half_m: float, utm_epsg: int
) -> tuple[float, float, float, float]:
    """Build a ``2*chip_half_m`` metric square around ``center_utm`` and
    reproject its corners to WGS84 lon/lat.

    Returns ``(lon_min, lat_min, lon_max, lat_max)`` matching the anchor bbox
    schema (``chip_lon_min`` ...). North-up UTM keeps corner ordering
    (``lon_max > lon_min``, ``lat_max > lat_min``).
    """
    x, y = center_utm
    xmin, ymin = x - chip_half_m, y - chip_half_m
    xmax, ymax = x + chip_half_m, y + chip_half_m
    tf = _utm_to_wgs84(utm_epsg)
    lon_min, lat_min = tf.transform(xmin, ymin)
    lon_max, lat_max = tf.transform(xmax, ymax)
    return (lon_min, lat_min, lon_max, lat_max)


# ---------------------------------------------------------------------------
# Thin I/O wrapper: build_cohort (untested — exercised by the WP-E smoke run)
# ---------------------------------------------------------------------------

_BBOX_COLS = ("chip_lon_min", "chip_lat_min", "chip_lon_max", "chip_lat_max")


def _read_csv_rows(path: Path) -> list[dict]:
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def _write_csv(path: Path, rows: list[dict], fieldnames: list[str]) -> None:
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def _epsg_int(crs_text: str) -> int:
    """Parse an integer EPSG code out of an ``EPSG:32735`` style string."""
    digits = "".join(ch for ch in str(crs_text) if ch.isdigit())
    return int(digits)


def build_cohort(
    *,
    intervals_csv: Path,
    chipgroups_csv: Path,
    census_gpkg: Path,
    prefpcut_gpkg: Path,
    grid_gpkg: Path,
    output_root: Path,
    seed: int,
    n_fals: int = 1200,
    nc_n: int = 300,
    nc_buffer_m: float = 100.0,
    nc_spacing_m: float = 150.0,
    anchor_ids: set[str] | None = None,
) -> dict:
    """Build the cohort plan + negative controls and write the four §4d files.

    Reads ``install_intervals.csv`` (dated inventory) joined to
    ``chip_groups_as_anchors.csv`` (anchor bboxes); assigns strata; draws the
    seeded 2019 falsification subsample; plans per-anchor layer units; and
    generates ``nc_n`` clean negative controls inside the surveyed JNB union
    (grid cells whose ``Name`` appears as ``source_grid`` in ``census_gpkg``),
    buffer-excluding any candidate within ``nc_buffer_m`` of a ``prefpcut_gpkg``
    detection.

    ``anchor_ids`` (optional): restrict the dated frame to these ids **before**
    planning and the falsification subsample — WP-E's smoke mode passes a
    forced subset here. Negative controls are unaffected (governed by ``nc_n``).

    Writes ``cohort_anchors.csv``, ``cohort_units.csv``,
    ``negative_controls.csv`` and ``dropped_units_summary.json`` under
    ``output_root`` and returns a summary dict.
    """
    import fiona
    from shapely.geometry import shape
    from shapely.ops import transform as shp_transform
    from shapely.ops import unary_union

    from core.grid_utils import get_metric_crs

    output_root = Path(output_root)
    output_root.mkdir(parents=True, exist_ok=True)

    # --- join intervals + chip-group bboxes ---------------------------------
    intervals = _read_csv_rows(Path(intervals_csv))
    chipgroups = {r["anchor_id"]: r for r in _read_csv_rows(Path(chipgroups_csv))}

    if anchor_ids is not None:
        intervals = [r for r in intervals if r["anchor_id"] in anchor_ids]

    dated_rows: list[dict] = []
    missing_bbox: list[str] = []
    for row in intervals:
        stratum = assign_cohort_stratum(row)
        if stratum is None:
            continue
        cg = chipgroups.get(row["anchor_id"])
        if cg is None or any(not cg.get(c) for c in _BBOX_COLS):
            missing_bbox.append(row["anchor_id"])
            continue
        merged = dict(row)
        merged["stratum"] = stratum
        for c in (*_BBOX_COLS, "chip_half_m"):
            merged[c] = cg.get(c)
        if not merged.get("grid_id"):
            merged["grid_id"] = cg.get("grid_id")
        dated_rows.append(merged)

    # --- seeded 2019 falsification subsample --------------------------------
    fals_set = select_falsification_subsample(
        dated_rows, seed=seed, n_target=n_fals
    )

    # --- per-anchor plan ----------------------------------------------------
    anchor_out: list[dict] = []
    unit_out: list[dict] = []
    layer_year_counts: Counter = Counter()
    stratum_counts: Counter = Counter()
    for row in dated_rows:
        in_fals = row["anchor_id"] in fals_set
        stratum_counts[row["stratum"]] += 1
        anchor_out.append(
            {
                "anchor_id": row["anchor_id"],
                "stratum": row["stratum"],
                "status": row.get("status"),
                "confidence": row.get("confidence"),
                "install_interval_start": row.get("install_interval_start"),
                "install_interval_end": row.get("install_interval_end"),
                "latest_absent_date": row.get("latest_absent_date"),
                "earliest_present_date": row.get("earliest_present_date"),
                "grid_id": row.get("grid_id"),
                "region_key": row.get("region_key"),
                "is_negative_control": False,
                "in_fals_2019": in_fals,
                **{c: row.get(c) for c in (*_BBOX_COLS, "chip_half_m")},
            }
        )
        for u in plan_units_for_anchor(row, in_fals_2019=in_fals):
            layer_year_counts[u.year] += 1
            unit_out.append(
                {
                    "anchor_id": u.anchor_id,
                    "year": u.year,
                    "stratum": u.stratum,
                    "unit_purpose": u.unit_purpose,
                    "status": row.get("status"),
                    "confidence": row.get("confidence"),
                    "install_interval_start": row.get("install_interval_start"),
                    "install_interval_end": row.get("install_interval_end"),
                    "is_negative_control": False,
                    **{c: row.get(c) for c in _BBOX_COLS},
                }
            )

    # --- negative controls --------------------------------------------------
    nc_rows, nc_anchor_rows, nc_unit_rows, nc_epsg = _build_negative_controls(
        census_gpkg=Path(census_gpkg),
        prefpcut_gpkg=Path(prefpcut_gpkg),
        grid_gpkg=Path(grid_gpkg),
        seed=seed,
        nc_n=nc_n,
        nc_buffer_m=nc_buffer_m,
        nc_spacing_m=nc_spacing_m,
        fiona=fiona,
        shape=shape,
        shp_transform=shp_transform,
        unary_union=unary_union,
        get_metric_crs=get_metric_crs,
    )
    anchor_out.extend(nc_anchor_rows)
    unit_out.extend(nc_unit_rows)
    for u in nc_unit_rows:
        layer_year_counts[u["year"]] += 1

    # --- deliberately-dropped accounting (design §2) ------------------------
    dropped = _dropped_units_summary(dated_rows, fals_set, missing_bbox)
    dropped.update(
        {
            "n_dated_anchors": len(dated_rows),
            "n_planned_dated_units": sum(1 for u in unit_out if not u["is_negative_control"]),
            "n_negative_controls": len(nc_rows),
            "n_fals_subsample": len(fals_set),
            "strata_counts": dict(stratum_counts),
            "layer_year_unit_counts": {int(k): v for k, v in sorted(layer_year_counts.items())},
            "nc_utm_epsg": nc_epsg,
        }
    )

    # --- write --------------------------------------------------------------
    _write_csv(
        output_root / "cohort_anchors.csv",
        anchor_out,
        [
            "anchor_id", "stratum", "status", "confidence",
            "install_interval_start", "install_interval_end",
            "latest_absent_date", "earliest_present_date",
            "grid_id", "region_key", "is_negative_control", "in_fals_2019",
            *_BBOX_COLS, "chip_half_m",
        ],
    )
    _write_csv(
        output_root / "cohort_units.csv",
        unit_out,
        [
            "anchor_id", "year", "stratum", "unit_purpose", "status", "confidence",
            "install_interval_start", "install_interval_end",
            "is_negative_control", *_BBOX_COLS,
        ],
    )
    _write_csv(
        output_root / "negative_controls.csv",
        nc_rows,
        ["anchor_id", "center_x_utm", "center_y_utm", "utm_epsg", *_BBOX_COLS],
    )
    with open(output_root / "dropped_units_summary.json", "w") as f:
        json.dump(dropped, f, indent=2, default=str)

    print(
        f"[build] {len(dated_rows)} dated anchors, "
        f"{sum(1 for u in unit_out if not u['is_negative_control'])} dated units, "
        f"{len(nc_rows)} negative controls -> {output_root}"
    )
    return dropped


def _dropped_units_summary(
    dated_rows: list[dict], fals_set: set[str], missing_bbox: list[str]
) -> dict:
    """Count the design §2 'deliberately dropped' categories over the processed
    dated set (informational coverage accounting)."""
    dropped_2019_absent_outside = 0
    dropped_2015_end_ge_2019 = 0
    dropped_2019_within_year_start2019 = 0
    dropped_2019_within_year_end2019 = 0
    for row in dated_rows:
        start = _parse_date(row.get("install_interval_start"))
        end = _parse_date(row.get("install_interval_end"))
        s3like = row["stratum"] == C_FINDINGS_S3LIKE
        # 2019 dropped for absent@2019 claimants outside the subsample.
        if (
            start is not None
            and start > _D_2019_12_31
            and start <= _D_2023_12_31  # exclude s3-like (they still get 2019)
            and row["anchor_id"] not in fals_set
            and not s3like
        ):
            dropped_2019_absent_outside += 1
        # 2015 dropped for every interval whose end is not deep-pre-2019.
        if end is not None and end >= _D_2019_01_01:
            dropped_2015_end_ge_2019 += 1
        # 2019 dropped for within-year-unfalsifiable intervals (no other role).
        if start is not None and start.year == 2019:
            dropped_2019_within_year_start2019 += 1
        if end is not None and end.year == 2019:
            dropped_2019_within_year_end2019 += 1
    return {
        "anchors_missing_bbox": {"count": len(missing_bbox), "anchor_ids": sorted(missing_bbox)},
        "dropped_2019_absent_outside_subsample": dropped_2019_absent_outside,
        "dropped_2015_end_ge_2019": dropped_2015_end_ge_2019,
        "dropped_2019_within_year_start2019": dropped_2019_within_year_start2019,
        "dropped_2019_within_year_end2019": dropped_2019_within_year_end2019,
    }


def _build_negative_controls(
    *,
    census_gpkg: Path,
    prefpcut_gpkg: Path,
    grid_gpkg: Path,
    seed: int,
    nc_n: int,
    nc_buffer_m: float,
    nc_spacing_m: float,
    fiona,
    shape,
    shp_transform,
    unary_union,
    get_metric_crs,
):
    """Generate negative-control centers + bboxes (design §3). Returns
    (nc_rows, nc_anchor_rows, nc_unit_rows, utm_epsg)."""
    from pyproj import Transformer as _Transformer

    # 1. surveyed JNB grid names = source_grid values in the census gpkg.
    surveyed: set[str] = set()
    with fiona.open(census_gpkg) as src:
        for feat in src:
            g = feat["properties"].get("source_grid")
            if g:
                surveyed.add(str(g))

    # 2. metric CRS (looked up, never hardcoded) + surveyed union (metric).
    sample_grid = sorted(surveyed)[0] if surveyed else "JNB0001"
    metric_crs = get_metric_crs(sample_grid, region="johannesburg")
    utm_epsg = _epsg_int(metric_crs)

    to_metric = _Transformer.from_crs("EPSG:4326", f"EPSG:{utm_epsg}", always_xy=True)
    cells = []
    with fiona.open(grid_gpkg) as src:
        for feat in src:
            if str(feat["properties"].get("Name")) in surveyed and feat["geometry"] is not None:
                geom = shape(feat["geometry"])
                cells.append(shp_transform(lambda xx, yy, z=None: to_metric.transform(xx, yy), geom))
    surveyed_union = unary_union(cells) if cells else None

    # 3. lattice -> buffer-exclude near detections -> seeded draw.
    centers = lattice_centers(surveyed_union, spacing_m=nc_spacing_m) if surveyed_union else []

    det_geoms = []
    with fiona.open(prefpcut_gpkg) as src:
        det_epsg = _epsg_int(str(src.crs.to_string()) if hasattr(src.crs, "to_string") else src.crs)
        det_to_metric = (
            None
            if det_epsg == utm_epsg
            else _Transformer.from_crs(f"EPSG:{det_epsg}", f"EPSG:{utm_epsg}", always_xy=True)
        )
        for feat in src:
            if feat["geometry"] is None:
                continue
            g = shape(feat["geometry"])
            if det_to_metric is not None:
                g = shp_transform(lambda xx, yy, z=None: det_to_metric.transform(xx, yy), g)
            det_geoms.append(g)

    survivors = exclude_near(centers, det_geoms, buffer_m=nc_buffer_m) if centers else []
    chosen = sample_negative_controls(survivors, seed=seed, n=nc_n)

    nc_rows, nc_anchor_rows, nc_unit_rows = [], [], []
    for i, (x, y) in enumerate(chosen):
        nc_id = f"nc_{i:04d}"
        bbox = nc_bbox_4326((x, y), chip_half_m=DEFAULT_CHIP_HALF_M, utm_epsg=utm_epsg)
        bbox_map = dict(zip(_BBOX_COLS, bbox))
        nc_rows.append(
            {"anchor_id": nc_id, "center_x_utm": x, "center_y_utm": y, "utm_epsg": utm_epsg, **bbox_map}
        )
        nc_anchor_rows.append(
            {
                "anchor_id": nc_id,
                "stratum": C_NEGATIVE_CONTROL,
                "status": "",
                "confidence": "",
                "install_interval_start": "",
                "install_interval_end": "",
                "latest_absent_date": "",
                "earliest_present_date": "",
                "grid_id": "",
                "region_key": "johannesburg",
                "is_negative_control": True,
                "in_fals_2019": False,
                "chip_half_m": DEFAULT_CHIP_HALF_M,
                **bbox_map,
            }
        )
        for year in (2019, 2023):
            nc_unit_rows.append(
                {
                    "anchor_id": nc_id,
                    "year": year,
                    "stratum": C_NEGATIVE_CONTROL,
                    "unit_purpose": PURPOSE_NEGATIVE_CONTROL,
                    "status": "",
                    "confidence": "",
                    "install_interval_start": "",
                    "install_interval_end": "",
                    "is_negative_control": True,
                    **bbox_map,
                }
            )
    return nc_rows, nc_anchor_rows, nc_unit_rows, utm_epsg
