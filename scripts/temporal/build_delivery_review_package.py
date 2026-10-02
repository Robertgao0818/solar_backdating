#!/usr/bin/env python3
"""Offline review package for the frozen Cape Town and Johannesburg deliveries.

Draw a seeded stratified sample of roofs from each city's frozen install-date
table (one draw per city). Cape Town ``cohort=top52`` rows are not eligible.
Within each ``scan_status``, roofs are split into three installation-area
bands and each non-empty band contributes the same small draw. For every
selected roof,
pair the frozen status fields with the census aerial chip and the satellite
chips named in the delivered observation record. Chips already on disk are
reused. Missing satellite chips go through the existing GEHI downloader.
Missing aerial chips are cropped from a local census tile when one covers the
roof, otherwise fetched through the existing Cape Town WMS or Johannesburg
Vexcel extract path. A failed fetch stays in the sample as a visible gap.

The aerial chip is burned with the delivered installation polygon. Each
georeferenced satellite chip is burned with one mark at the frozen
``centroid_lon`` / ``centroid_lat``. The share directory is a static page plus
those display images. Frozen CSV/GPKG inputs are opened read-only.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import io
import json
import os
import re
import shutil
import sys
import tarfile
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Mapping, Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np
from PIL import Image, ImageDraw
from shapely.geometry import shape as shapely_shape

from scripts.temporal.anchor_derivation import derive_per_target_anchor_row
from scripts.temporal.geid_temporal_common import safe_task_token

SCHEMA = "delivery_review_package_v2"
DEFAULT_SEED = 20260927
# Cap per (scan_status, area band). Three bands times two roofs keeps the
# old visual budget of about six roofs per status, spread across sizes.
DEFAULT_PER_AREA_BAND = 2
DEFAULT_MAX_PER_STATUS = DEFAULT_PER_AREA_BAND
EXCLUDE_COHORTS = ("top52",)
# Cuts sit above the ~17 m² median and at the commercial tail a uniform
# draw inside a status almost never reaches.
AREA_BREAKS_M2 = (20.0, 100.0)
AREA_BAND_TOKEN = {
    "lt20_m2": "小于20平方米",
    "m20_100": "20到100平方米",
    "ge100_m2": "100平方米及以上",
    "unknown": "面积缺失",
}
AREA_BAND_ORDER = tuple(AREA_BAND_TOKEN.values())
CHIP_SIZE_M = 96.0
AERIAL_CONTEXT_M = 30.0
AERIAL_MIN_SPAN_M = 40.0
ZOOM_LADDER = (19, 18)
GEHI_VERSION = "review"

OUTLINE_RGB = (0, 200, 0)
CENTROID_RGB = (220, 0, 0)
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"

CT_CITY = "cape_town"
JHB_CITY = "johannesburg"
CT_METRIC = "EPSG:32734"
JHB_METRIC = "EPSG:32735"

TOP52_NOTE = "intermediate visits are not in the delivery"
NO_GEOREF_NOTE = "chip has no georeference; the outline or the centroid mark was not placed"
OUTLINE_UNPLACED = "the outline was not placed"
CENTROID_UNPLACED = "the centroid mark was not placed"

CT_STATUS_FIELDS = (
    "scan_status",
    "area_band",
    "area_m2",
    "install_interval_start",
    "install_interval_end",
    "latest_absent_date",
    "earliest_present_date",
    "cohort",
    "left_censored",
    "census_bound",
)
JHB_STATUS_FIELDS = (
    "scan_status",
    "area_band",
    "area_m2",
    "install_interval_start",
    "install_interval_end",
    "earliest_present_date",
    "lane",
    "left_censored",
    "census_bound",
)

DEFAULT_CT_CSV = Path(
    "~/zasolar_data/geid_temporal/cape_town_citywide_backdating_v1_20260812/"
    "deliverable_2026-09-02/ct_citywide_install_dated_20260902.csv"
).expanduser()
DEFAULT_CT_GPKG = DEFAULT_CT_CSV.with_suffix(".gpkg")
DEFAULT_CT_OBS = Path(
    "~/zasolar_data/geid_temporal/cape_town_citywide_backdating_v1_20260812/"
    "sequences_20260923/ct_citywide_presence_observations_20260923.csv"
).expanduser()
DEFAULT_CT_CHIP_ROOT = Path(
    "~/zasolar_data/geid_temporal/cape_town_citywide_backdating_v1_20260812"
).expanduser()
DEFAULT_JHB_CSV = Path(
    "~/Dropbox/RA_Solar/Gao/backdating/jhb_install_dated_2026-07-18/"
    "jhb_full382_fpcut_install_dated_2026-07-18-run3.csv"
).expanduser()
DEFAULT_JHB_GPKG = DEFAULT_JHB_CSV.with_suffix(".gpkg")
DEFAULT_JHB_OBS = Path(
    "~/zasolar_data/geid_temporal/fullscan_gemini_backdating_2026-07/"
    "deliverable_run3_sequences_2026-09-23/jhb_run3_presence_observations.csv"
).expanduser()
DEFAULT_JHB_CHIP_ROOT = Path(
    "~/zasolar_data/geid_temporal/basemap_rebuild_2026-07-13/chips"
).expanduser()
DEFAULT_CT_AERIAL_ROOT = Path("~/zasolar_data/tiles/cape_town/aerial_2025").expanduser()
DEFAULT_JHB_AERIAL_ROOT = Path("~/zasolar_data/tiles/johannesburg/vexcel_2024").expanduser()
DEFAULT_OUTPUT_DIR = Path(
    "~/zasolar_data/geid_temporal/delivery_review_package_20260927"
).expanduser()

CT_WMS_URL = "https://cityimg.capetown.gov.za/erdas-iws/ogc/wms/GeoSpatial Datasets"
CT_WMS_LAYER = "Aerial Imagery_Aerial Imagery 2025Jan"
JHB_VEXCEL_COLLECTION = "za-gp-johannesburg-2024"
JHB_VEXCEL_LAYER = "urban"
VEXCEL_BASE = "https://api.vexcelgroup.com/v2"

_TOKEN_RE = re.compile(r"(token=)[^&\s]+", re.IGNORECASE)
_IMG_EXTS = (".tif", ".tiff", ".png", ".jpg", ".jpeg")


@dataclass
class CityBundle:
    """One city's frozen rows plus the read-only joins the page needs."""

    city: str
    rows: list[dict[str, str]]
    observations: dict[str, list[dict[str, str]]] = field(default_factory=dict)
    polygons: dict[str, tuple[object, str]] = field(default_factory=dict)
    metric_crs: str = CT_METRIC
    observation_key: str = "source_anchor_id"
    chip_roots: list[Path] = field(default_factory=list)
    aerial_tile_roots: list[Path] = field(default_factory=list)


@dataclass
class ChipRequest:
    city: str
    anchor_id: str
    source_feature_id: str
    kind: str
    capture_date: str
    provider: str
    bbox: tuple[float, float, float, float]
    wave: str = ""


@dataclass
class FetchResult:
    status: str
    data: bytes | None
    error: str
    source_path: Path | None = None


@dataclass
class MarkResult:
    png_bytes: bytes
    outline_placed: bool
    centroid_placed: bool
    note: str
    outline_pixels: tuple[tuple[int, int], ...] = ()
    centroid_pixel: tuple[int, int] | None = None


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def redact_secret(text: str) -> str:
    return _TOKEN_RE.sub(r"\1REDACTED", text)


def load_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _id_sort_key(value: object) -> tuple:
    text = str(value).strip()
    if text.isdigit():
        return (0, int(text), text)
    return (1, text, text)


def _rank_token(seed: int, city: str, stratum: str, ident: str) -> str:
    payload = f"{seed}|{city}|{stratum}|{ident}".encode()
    return hashlib.sha256(payload).hexdigest()


def _in_city(row: Mapping[str, str], city: str) -> bool:
    row_city = str(row.get("city") or "").strip()
    return not row_city or row_city == city


def _cohort_excluded(row: Mapping[str, str], exclude_cohorts: Sequence[str]) -> bool:
    return str(row.get("cohort") or "").strip() in set(exclude_cohorts)


def area_band_token(row: Mapping[str, str]) -> str:
    """Stable band id from frozen ``area_m2``.

    Missing or non-numeric area stays in ``unknown`` so a roof is not dropped
    just because the size cell is blank. Cuts are ``[0, 20)``, ``[20, 100)``,
    and ``[100, inf)``.
    """

    raw = str(row.get("area_m2") or "").strip()
    if not raw:
        return "unknown"
    try:
        area = float(raw)
    except ValueError:
        return "unknown"
    if area < AREA_BREAKS_M2[0]:
        return "lt20_m2"
    if area < AREA_BREAKS_M2[1]:
        return "m20_100"
    return "ge100_m2"


def area_band_label(row: Mapping[str, str]) -> str:
    return AREA_BAND_TOKEN[area_band_token(row)]


def sample_roofs_by_status(
    rows: Sequence[Mapping[str, str]],
    *,
    city: str,
    seed: int = DEFAULT_SEED,
    max_per_status: int = DEFAULT_PER_AREA_BAND,
    exclude_cohorts: Sequence[str] = EXCLUDE_COHORTS,
) -> list[dict[str, str]]:
    """Seeded draw on ``(scan_status, area band)``.

    ``max_per_status`` is the cap inside one status-by-area cell, not the
    whole status. The rank of a roof is a hash of
    ``(seed, city, status, band, source_feature_id)``. City tables do not
    share a random stream, input order does not matter, and cohorts named in
    ``exclude_cohorts`` (Cape Town Top-52) never enter the draw. A cell with
    ``n`` eligible roofs contributes ``min(max_per_status, n)`` roofs.
    """

    if max_per_status < 1:
        raise ValueError("max_per_status must be at least 1")
    grouped: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    for source in rows:
        if not _in_city(source, city) or _cohort_excluded(source, exclude_cohorts):
            continue
        status = str(source.get("scan_status") or "").strip()
        if not status:
            continue
        copied = dict(source)
        token = area_band_token(copied)
        copied["area_band"] = AREA_BAND_TOKEN[token]
        grouped[(status, token)].append(copied)
    picked: list[dict[str, str]] = []
    for status, token in sorted(grouped):
        ordered = sorted(
            grouped[(status, token)],
            key=lambda row: (
                _rank_token(
                    seed,
                    city,
                    f"{status}|{token}",
                    str(row.get("source_feature_id") or "").strip(),
                ),
                str(row.get("source_feature_id") or "").strip(),
            ),
        )
        picked.extend(ordered[: min(max_per_status, len(ordered))])
    band_rank = {label: index for index, label in enumerate(AREA_BAND_ORDER)}
    picked.sort(
        key=lambda row: (
            str(row.get("scan_status") or ""),
            band_rank.get(str(row.get("area_band") or ""), 99),
            _id_sort_key(row.get("source_feature_id")),
        )
    )
    return picked


def status_counts(
    rows: Sequence[Mapping[str, str]],
    *,
    city: str | None = None,
    exclude_cohorts: Sequence[str] = (),
) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for row in rows:
        if city and not _in_city(row, city):
            continue
        if exclude_cohorts and _cohort_excluded(row, exclude_cohorts):
            continue
        status = str(row.get("scan_status") or "").strip()
        if status:
            counts[status] += 1
    return dict(counts)


def _eligible_cells(
    rows: Sequence[Mapping[str, str]],
    *,
    city: str,
    exclude_cohorts: Sequence[str],
) -> dict[tuple[str, str], int]:
    counts: dict[tuple[str, str], int] = defaultdict(int)
    for row in rows:
        if not _in_city(row, city) or _cohort_excluded(row, exclude_cohorts):
            continue
        status = str(row.get("scan_status") or "").strip()
        if not status:
            continue
        counts[(status, area_band_token(row))] += 1
    return dict(counts)


def assert_sample_contract(
    source_rows: Sequence[Mapping[str, str]],
    picked: Sequence[Mapping[str, str]],
    *,
    city: str,
    max_per_status: int = DEFAULT_PER_AREA_BAND,
    exclude_cohorts: Sequence[str] = EXCLUDE_COHORTS,
) -> None:
    """Each eligible status survives, and each populated area cell is capped."""

    cells = _eligible_cells(source_rows, city=city, exclude_cohorts=exclude_cohorts)
    source_statuses = {status for status, _token in cells}
    chosen_statuses = {
        str(row.get("scan_status") or "").strip()
        for row in picked
        if str(row.get("scan_status") or "").strip()
    }
    if chosen_statuses != source_statuses:
        raise AssertionError(
            f"{city} sample dropped or invented a status: {source_statuses ^ chosen_statuses}"
        )
    picked_cells: dict[tuple[str, str], int] = defaultdict(int)
    for row in picked:
        status = str(row.get("scan_status") or "").strip()
        token = area_band_token(row)
        label = str(row.get("area_band") or "")
        if label != AREA_BAND_TOKEN[token]:
            raise AssertionError(f"{city} area_band {label!r} does not match area_m2")
        if _cohort_excluded(row, exclude_cohorts):
            raise AssertionError(f"{city} sample kept excluded cohort {row.get('cohort')}")
        picked_cells[(status, token)] += 1
    if set(picked_cells) != set(cells):
        raise AssertionError(f"{city} sample dropped or invented an area cell: {set(cells) ^ set(picked_cells)}")
    for cell, n_source in cells.items():
        n_picked = picked_cells[cell]
        expected = min(max_per_status, n_source)
        if n_picked != expected:
            raise AssertionError(f"{city} {cell}: expected {expected} from {n_source} got {n_picked}")
    source_ids = {
        str(row.get("source_feature_id") or "").strip()
        for row in source_rows
        if _in_city(row, city) and not _cohort_excluded(row, exclude_cohorts)
    }
    picked_ids = [str(row.get("source_feature_id") or "").strip() for row in picked]
    if len(picked_ids) != len(set(picked_ids)):
        raise AssertionError(f"{city} sample has duplicate ids")
    if not set(picked_ids) <= source_ids:
        raise AssertionError(f"{city} sample is not a subset of the eligible ids")
    if source_ids and len(picked_ids) >= len(source_ids) and any(n > max_per_status for n in cells.values()):
        raise AssertionError(f"{city} sample is the full eligible table")


def _iso_day(value: object) -> str:
    text = str(value or "").strip()
    if len(text) >= 10 and text[4:5] == "-" and text[7:8] == "-":
        return text[:10]
    return ""


def _ct_code(value: object) -> str:
    token = str(value or "").strip().lower()
    if token == "0":
        return "0"
    if token == "1":
        return "1"
    return "unusable"


def _jhb_code(value: object) -> str:
    token = str(value or "").strip()
    if token in {"0", "1"}:
        return token
    return "unusable"


def observation_slots_for_roof(
    row: Mapping[str, str],
    observations: Sequence[Mapping[str, str]] | None,
    *,
    city: str,
) -> tuple[list[dict[str, str]], str]:
    """Dates and delivered codes for one roof.

    Cape Town Top-52 rows keep only the endpoint dates stored on the frozen
    row. Intermediate visits are not reconstructed. Every other roof uses the
    delivered observation rows, Cape Town repaired codes or Johannesburg raw
    ``pv_present``, deduped by capture date and ordered chronologically.
    """

    if city == CT_CITY and str(row.get("cohort") or "").strip() == "top52":
        slots = []
        for field_name in ("latest_absent_date", "earliest_present_date"):
            day = _iso_day(row.get(field_name))
            if not day:
                continue
            slots.append(
                {
                    "capture_date": day,
                    "code": "not_in_delivery",
                    "provider": "",
                    "note": (
                        f"{field_name} is an endpoint date from the frozen row; "
                        f"per-visit code is not in the delivery; {TOP52_NOTE}"
                    ),
                }
            )
        deduped: dict[str, dict[str, str]] = {}
        for slot in slots:
            deduped.setdefault(slot["capture_date"], slot)
        return [deduped[day] for day in sorted(deduped)], TOP52_NOTE

    keyed: dict[str, dict[str, str]] = {}
    for obs in observations or []:
        day = _iso_day(obs.get("capture_date"))
        if not day:
            continue
        if city == CT_CITY:
            role = str(obs.get("frame_role") or "evidence").strip()
            if role and role != "evidence":
                continue
            code = _ct_code(obs.get("code_repaired"))
            provider = str(obs.get("provider") or "").strip()
        else:
            code = _jhb_code(obs.get("pv_present"))
            provider = str(obs.get("provider") or "").strip()
        order_text = str(obs.get("obs_order") or obs.get("obs_index") or "")
        order_key = (0, int(order_text)) if order_text.isdigit() else (1, order_text)
        current = keyed.get(day)
        if current is not None and current["_order"] <= order_key:
            continue
        keyed[day] = {
            "capture_date": day,
            "code": code,
            "provider": provider,
            "note": "",
            "_order": order_key,
        }
    slots = []
    for day in sorted(keyed):
        slot = dict(keyed[day])
        slot.pop("_order", None)
        slots.append(slot)
    return slots, ""


def resolve_local_or_fetch(
    *,
    local_path: Path | None,
    fetcher: Callable[[], bytes] | None,
) -> FetchResult:
    """Reuse a non-empty local file, otherwise call ``fetcher`` once.

    A fetcher exception becomes a gap. The caller keeps the roof either way.
    """

    if local_path is not None and local_path.is_file() and local_path.stat().st_size > 0:
        return FetchResult("reused", local_path.read_bytes(), "", local_path)
    if fetcher is None:
        return FetchResult("gap", None, "no local chip and no fetcher", None)
    try:
        data = fetcher()
    except Exception as exc:  # noqa: BLE001 — recorded on the gap, roof stays
        return FetchResult("gap", None, redact_secret(f"{type(exc).__name__}: {exc}"), None)
    if not data:
        return FetchResult("gap", None, "fetcher returned empty bytes", None)
    return FetchResult("downloaded", data, "", None)


def place_package_image(package_dir: Path, relative_path: str, result: FetchResult) -> str | None:
    """Write fetched or reused bytes to the path the page will display."""

    if result.status == "gap" or result.data is None:
        return None
    destination = package_dir / relative_path
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(result.data)
    return relative_path


def _inside(col: int, row: int, width: int, height: int) -> bool:
    return 0 <= col < width and 0 <= row < height


def _pixel_pairs(rows, cols) -> list[tuple[int, int]]:
    """rowcol() returns arrays; flatten them so a one-point call still yields ints."""

    row_list = np.atleast_1d(np.asarray(rows)).reshape(-1).tolist()
    col_list = np.atleast_1d(np.asarray(cols)).reshape(-1).tolist()
    return [(int(col), int(row)) for col, row in zip(col_list, row_list)]


def _read_rgb_array(path: Path) -> tuple[np.ndarray, object, object]:
    import rasterio

    try:
        with rasterio.open(path) as src:
            if src.count >= 3:
                array = src.read((1, 2, 3))
            else:
                band = src.read(1)
                array = np.stack((band, band, band))
            array = np.moveaxis(array, 0, -1)
            if array.dtype != np.uint8:
                finite = array[np.isfinite(array)] if np.issubdtype(array.dtype, np.floating) else array
                low = float(np.min(finite)) if finite.size else 0.0
                high = float(np.max(finite)) if finite.size else 0.0
                if high <= low:
                    array = np.zeros(array.shape, dtype=np.uint8)
                else:
                    array = np.clip((array.astype(np.float64) - low) * 255.0 / (high - low), 0, 255).astype(np.uint8)
            return np.ascontiguousarray(array), src.crs, src.transform
    except Exception:
        image = Image.open(path).convert("RGB")
        return np.asarray(image), None, None


def _project_xy(
    xs: Sequence[float],
    ys: Sequence[float],
    *,
    src_crs: str,
    dst_crs: str,
) -> tuple[list[float], list[float]]:
    if str(src_crs) == str(dst_crs):
        return [float(x) for x in xs], [float(y) for y in ys]
    from pyproj import Transformer

    transformer = Transformer.from_crs(str(src_crs), str(dst_crs), always_xy=True)
    out_x, out_y = transformer.transform(list(xs), list(ys))
    return [float(v) for v in out_x], [float(v) for v in out_y]


def _iter_rings(geometry) -> list[list[tuple[float, float]]]:
    geom_type = geometry.geom_type
    if geom_type == "Polygon":
        return [list(geometry.exterior.coords)]
    if geom_type == "MultiPolygon":
        return [list(part.exterior.coords) for part in geometry.geoms]
    if geom_type == "LineString":
        return [list(geometry.coords)]
    return []


def burn_review_marks(
    image_path: Path,
    *,
    polygon=None,
    polygon_crs: str | None = None,
    centroid_xy: tuple[float, float] | None = None,
    centroid_crs: str | None = None,
    draw_outline: bool = True,
    draw_centroid: bool = True,
) -> MarkResult:
    """Burn a polygon outline and/or a census-centroid pixel into a display PNG.

    An image with no CRS is returned unchanged. Nothing is painted at the
    image center as a stand-in. Pixels that project outside the chip are not
    clamped onto the frame.
    """

    from rasterio.transform import rowcol

    array, crs, transform = _read_rgb_array(image_path)
    height, width = array.shape[:2]
    original = Image.fromarray(array, mode="RGB")
    unchanged = io.BytesIO()
    original.save(unchanged, format="PNG")
    unchanged_bytes = unchanged.getvalue()
    if crs is None or transform is None:
        return MarkResult(unchanged_bytes, False, False, NO_GEOREF_NOTE)

    image = original.copy()
    draw = ImageDraw.Draw(image)
    outline_pixels: list[tuple[int, int]] = []
    centroid_pixel: tuple[int, int] | None = None
    notes: list[str] = []
    dst_crs = str(crs)

    if draw_outline and polygon is not None and not polygon.is_empty:
        try:
            for ring in _iter_rings(polygon):
                xs, ys = _project_xy(
                    [point[0] for point in ring],
                    [point[1] for point in ring],
                    src_crs=polygon_crs or dst_crs,
                    dst_crs=dst_crs,
                )
                rows, cols = rowcol(transform, xs, ys)
                points = _pixel_pairs(rows, cols)
                for col_i, row_i in points:
                    if _inside(col_i, row_i, width, height):
                        outline_pixels.append((col_i, row_i))
                        image.putpixel((col_i, row_i), OUTLINE_RGB)
                if len(points) >= 2:
                    draw.line(points, fill=OUTLINE_RGB, width=3)
        except Exception as exc:  # noqa: BLE001 — placement failure stays visible
            notes.append(f"{OUTLINE_UNPLACED} ({type(exc).__name__}: {exc})")
            outline_pixels = []
    elif draw_outline:
        notes.append(OUTLINE_UNPLACED)

    if draw_centroid and centroid_xy is not None:
        try:
            xs, ys = _project_xy(
                [centroid_xy[0]],
                [centroid_xy[1]],
                src_crs=centroid_crs or "EPSG:4326",
                dst_crs=dst_crs,
            )
            row, col = rowcol(transform, xs, ys)
            col_i, row_i = _pixel_pairs(row, col)[0]
            if _inside(col_i, row_i, width, height):
                centroid_pixel = (col_i, row_i)
                radius = max(6, min(width, height) // 28)
                for band, color in ((radius + 2, (255, 255, 255)), (radius, CENTROID_RGB)):
                    for delta in range(-band, band + 1):
                        if _inside(col_i + delta, row_i, width, height):
                            image.putpixel((col_i + delta, row_i), color)
                        if _inside(col_i, row_i + delta, width, height):
                            image.putpixel((col_i, row_i + delta), color)
                image.putpixel((col_i, row_i), CENTROID_RGB)
            else:
                notes.append(f"{CENTROID_UNPLACED}; census centroid falls outside the chip")
        except Exception as exc:  # noqa: BLE001
            notes.append(f"{CENTROID_UNPLACED} ({type(exc).__name__}: {exc})")
    elif draw_centroid:
        notes.append(CENTROID_UNPLACED)

    outline_placed = bool(outline_pixels)
    centroid_placed = centroid_pixel is not None
    if draw_outline and not outline_placed and not any(OUTLINE_UNPLACED in note for note in notes):
        notes.append(OUTLINE_UNPLACED)
    if draw_centroid and not centroid_placed and not any(CENTROID_UNPLACED in note for note in notes):
        notes.append(CENTROID_UNPLACED)
    if not notes:
        notes.append("marks placed from the chip georeference")
    out = io.BytesIO()
    image.save(out, format="PNG")
    return MarkResult(
        out.getvalue(),
        outline_placed,
        centroid_placed,
        "; ".join(notes),
        tuple(outline_pixels),
        centroid_pixel,
    )


def find_satellite_chip(
    anchor_id: str,
    capture_date: str,
    roots: Sequence[Path],
    *,
    wave: str = "",
) -> Path | None:
    """Return an existing chip for this anchor and capture date, or None."""

    token = safe_task_token(anchor_id)
    day = _iso_day(capture_date).replace("-", "")
    if not day:
        return None
    candidates: list[Path] = []
    wave_text = str(wave or "").strip()
    wave_name = f"wave_{int(wave_text):02d}" if wave_text.isdigit() else ""
    for root in roots:
        if root is None or not root.exists():
            continue
        anchor_dir = root / token
        if anchor_dir.is_dir():
            for ext in _IMG_EXTS:
                candidates.extend(anchor_dir.glob(f"z*/{token}_{day}_v*{ext}"))
                candidates.extend(anchor_dir.glob(f"{token}_{day}*{ext}"))
        if wave_name:
            wave_dir = root / wave_name
            if wave_dir.is_dir():
                chip_parents = [wave_dir / "chips" / token]
                for child in wave_dir.iterdir():
                    if child.is_dir():
                        chip_parents.append(child / "chips" / token)
                for parent in chip_parents:
                    if not parent.is_dir():
                        continue
                    for ext in _IMG_EXTS:
                        candidates.extend(parent.glob(f"z*/{token}_{day}_v*{ext}"))
    if not candidates:
        return None

    def _zoom(path: Path) -> int:
        for part in path.parts:
            if part.startswith("z") and part[1:].isdigit():
                return int(part[1:])
        return -1

    candidates.sort(key=lambda path: (_zoom(path), path.name), reverse=True)
    for path in candidates:
        if path.is_file() and path.stat().st_size > 0:
            return path
    return None


def find_prepared_aerial(anchor_id: str, reuse_root: Path | None) -> Path | None:
    if reuse_root is None or not reuse_root.exists():
        return None
    token = safe_task_token(anchor_id)
    for ext in _IMG_EXTS:
        candidate = reuse_root / f"{token}{ext}"
        if candidate.is_file() and candidate.stat().st_size > 0:
            return candidate
    return None


def chip_bbox_wgs84(anchor_id: str, lon: float, lat: float, metric_crs: str) -> tuple[float, float, float, float]:
    derived = derive_per_target_anchor_row(
        {"anchor_id": anchor_id, "centroid_lon": lon, "centroid_lat": lat},
        metric_crs=metric_crs,
    )
    return (
        float(derived["chip_lon_min"]),
        float(derived["chip_lat_min"]),
        float(derived["chip_lon_max"]),
        float(derived["chip_lat_max"]),
    )


def expand_bbox_m(
    west: float,
    south: float,
    east: float,
    north: float,
    *,
    pad_m: float,
    min_span_m: float,
    metric_crs: str,
) -> tuple[float, float, float, float]:
    from pyproj import Transformer

    to_metric = Transformer.from_crs("EPSG:4326", metric_crs, always_xy=True)
    to_wgs = Transformer.from_crs(metric_crs, "EPSG:4326", always_xy=True)
    xs, ys = to_metric.transform([west, east], [south, north])
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    span_x = max(max_x - min_x, min_span_m)
    span_y = max(max_y - min_y, min_span_m)
    cx = (min_x + max_x) / 2.0
    cy = (min_y + max_y) / 2.0
    half_x = span_x / 2.0 + pad_m
    half_y = span_y / 2.0 + pad_m
    out_x, out_y = to_wgs.transform([cx - half_x, cx + half_x], [cy - half_y, cy + half_y])
    return (min(out_x), min(out_y), max(out_x), max(out_y))


def index_aerial_tiles(roots: Sequence[Path]) -> list[dict[str, object]]:
    import rasterio

    tiles: list[dict[str, object]] = []
    for root in roots:
        if root is None or not root.exists():
            continue
        for path in sorted(root.rglob("*_geo.tif")):
            try:
                with rasterio.open(path) as src:
                    bounds = src.bounds
                    tiles.append(
                        {
                            "path": path,
                            "crs": str(src.crs) if src.crs else "",
                            "left": float(bounds.left),
                            "bottom": float(bounds.bottom),
                            "right": float(bounds.right),
                            "top": float(bounds.top),
                        }
                    )
            except Exception:
                continue
    return tiles


def crop_aerial_from_tiles(
    tiles: Sequence[Mapping[str, object]],
    bbox: tuple[float, float, float, float],
    out_path: Path,
) -> Path | None:
    """Crop local georeferenced tiles to ``bbox`` (WGS84 west, south, east, north)."""

    import rasterio
    from rasterio.merge import merge

    west, south, east, north = bbox
    by_crs: dict[str, list[Path]] = defaultdict(list)
    for tile in tiles:
        crs = str(tile.get("crs") or "")
        if not crs:
            continue
        xs, ys = _project_xy(
            [west, east],
            [south, north],
            src_crs="EPSG:4326",
            dst_crs=crs,
        )
        left, right = min(xs), max(xs)
        bottom, top = min(ys), max(ys)
        if right < float(tile["left"]) or left > float(tile["right"]):
            continue
        if top < float(tile["bottom"]) or bottom > float(tile["top"]):
            continue
        by_crs[crs].append(Path(tile["path"]))
    if not by_crs:
        return None
    crs, paths = max(by_crs.items(), key=lambda item: len(item[1]))
    xs, ys = _project_xy(
        [west, east],
        [south, north],
        src_crs="EPSG:4326",
        dst_crs=crs,
    )
    bounds = (min(xs), min(ys), max(xs), max(ys))
    datasets = []
    try:
        for path in paths:
            datasets.append(rasterio.open(path))
        mosaic, transform = merge(datasets, bounds=bounds)
    except Exception:
        return None
    finally:
        for dataset in datasets:
            dataset.close()
    if mosaic.size == 0 or mosaic.shape[-1] < 2 or mosaic.shape[-2] < 2:
        return None
    out_path.parent.mkdir(parents=True, exist_ok=True)
    profile = {
        "driver": "GTiff",
        "height": mosaic.shape[1],
        "width": mosaic.shape[2],
        "count": mosaic.shape[0],
        "dtype": mosaic.dtype,
        "crs": crs,
        "transform": transform,
    }
    with rasterio.open(out_path, "w", **profile) as dst:
        dst.write(mosaic)
    if out_path.stat().st_size <= 0:
        return None
    return out_path


def _write_rgb_geotiff(path: Path, array_hwc: np.ndarray, west: float, south: float, east: float, north: float) -> None:
    import rasterio
    from rasterio.transform import from_bounds

    height, width = array_hwc.shape[:2]
    transform = from_bounds(west, south, east, north, width, height)
    data = np.moveaxis(array_hwc, -1, 0)
    path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=height,
        width=width,
        count=3,
        dtype="uint8",
        crs="EPSG:4326",
        transform=transform,
    ) as dst:
        dst.write(data)


def fetch_cape_town_wms(request: ChipRequest, destination: Path) -> bytes:
    import requests
    from PIL import Image as PilImage

    west, south, east, north = request.bbox
    params = {
        "service": "WMS",
        "version": "1.1.1",
        "request": "GetMap",
        "layers": CT_WMS_LAYER,
        "srs": "EPSG:4326",
        "bbox": f"{west},{south},{east},{north}",
        "width": 768,
        "height": 768,
        "format": "image/jpeg",
        "styles": "",
    }
    response = requests.get(CT_WMS_URL, params=params, timeout=180)
    content_type = response.headers.get("Content-Type", "")
    if response.status_code != 200 or "image" not in content_type.lower():
        detail = redact_secret(response.text[:240])
        raise RuntimeError(f"Cape Town WMS HTTP {response.status_code} {content_type}: {detail}")
    image = PilImage.open(io.BytesIO(response.content)).convert("RGB")
    _write_rgb_geotiff(destination, np.asarray(image), west, south, east, north)
    return destination.read_bytes()


def fetch_jhb_vexcel(request: ChipRequest, destination: Path) -> bytes:
    import requests
    from shapely.geometry import box

    from core.vexcel_auth import load_env, resolve_token

    zasolar = Path(os.environ.get("ZASOLAR_ROOT", "/home/gao/projects/ZAsolar"))
    env = load_env(zasolar / ".env")
    base = env.get("VEXCEL_API_BASE", VEXCEL_BASE)
    token = resolve_token(env, base)
    west, south, east, north = request.bbox
    params = {
        "layer": JHB_VEXCEL_LAYER,
        "collection": JHB_VEXCEL_COLLECTION,
        "wkt": box(west, south, east, north).wkt,
        "srid": "4326",
        "image-format": "tiff",
        "token": token,
    }
    try:
        response = requests.get(f"{base.rstrip('/')}/ortho/extract", params=params, timeout=180)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(redact_secret(f"Vexcel extract failed: {exc}")) from exc
    if response.status_code != 200 or response.content[:4] not in (b"II*\x00", b"MM\x00*", b"II+\x00", b"MM\x00+"):
        raise RuntimeError(
            redact_secret(f"Vexcel HTTP {response.status_code}: {response.text[:240]}")
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(response.content)
    return response.content


def make_gehi_fetcher(cache_dir: Path) -> Callable[[ChipRequest], bytes]:
    from scripts.temporal.gehi_common import GehiRateLimiter, make_throttled_runner
    from scripts.temporal.gehi_download import download_chip_with_zoom_ladder

    # Review samples must record a miss and move on. The production limiter's
    # 30-minute hard backoff is for a multi-day scan, not a few dozen roofs.
    runner = make_throttled_runner(
        limiter=GehiRateLimiter(
            min_interval_s=0.5,
            soft_backoff_s=3.0,
            hard_backoff_s=20.0,
            hard_after=8,
        ),
        max_attempts=1,
    )

    def fetch(request: ChipRequest) -> bytes:
        west, south, east, north = request.bbox
        anchor = {
            "anchor_id": request.anchor_id,
            "chip_lon_min": west,
            "chip_lat_min": south,
            "chip_lon_max": east,
            "chip_lat_max": north,
        }
        providers: list[str] = []
        if request.provider:
            providers.append(request.provider)
        for name in ("Wayback", "TM"):
            if name not in providers:
                providers.append(name)
        errors: list[str] = []
        for provider in providers:
            result = download_chip_with_zoom_ladder(
                anchor,
                capture_date=request.capture_date,
                version=GEHI_VERSION,
                zoom_ladder=ZOOM_LADDER,
                output_root=cache_dir / "gehi" / provider,
                provider=provider,
                allow_nearest=False,
                runner=runner,
            )
            if result.path is not None and result.status in {"ok", "skipped_existing"}:
                return Path(result.path).read_bytes()
            message = redact_secret(f"{provider}: {result.error or result.status}")
            errors.append(message)
            print(
                f"gehi miss {request.anchor_id} {request.capture_date} {message[:240]}",
                flush=True,
            )
        raise RuntimeError("; ".join(errors) or "GEHI download failed")

    return fetch


def _status_fields(city: str, row: Mapping[str, str]) -> dict[str, str]:
    fields = CT_STATUS_FIELDS if city == CT_CITY else JHB_STATUS_FIELDS
    visible = {}
    for name in fields:
        if name in row:
            visible[name] = row[name]
    return visible


def _roof_key(row: Mapping[str, str]) -> str:
    return safe_task_token(str(row.get("source_feature_id") or "roof"))


_FIELD_ORDER = (
    "scan_status",
    "area_band",
    "area_m2",
    "install_interval_start",
    "install_interval_end",
    "latest_absent_date",
    "earliest_present_date",
    "cohort",
    "lane",
    "left_censored",
    "census_bound",
)


def _ordered_field_names(fields: Mapping[str, str]) -> list[str]:
    names = [name for name in _FIELD_ORDER if name in fields]
    names.extend(name for name in fields if name not in names)
    return names


def render_review_html(manifest: Mapping[str, object]) -> str:
    """Static two-city page. Image paths are relative. No script and no remote URL."""

    sections: list[str] = []
    for city_block in manifest["cities"]:
        city = str(city_block["city"])
        heading = "开普敦 Cape Town" if city == CT_CITY else "约翰内斯堡 Johannesburg"
        cards: list[str] = []
        for roof in city_block["roofs"]:
            fields = roof["fields"]
            rows_html = []
            for name in _ordered_field_names(fields):
                rows_html.append(
                    "<tr><th>"
                    + html.escape(name)
                    + "</th><td>"
                    + html.escape(str(fields[name]))
                    + "</td></tr>"
                )
            identity = (
                f"<p class='id'>source_feature_id {html.escape(str(roof['source_feature_id']))}"
                f" · source_anchor_id {html.escape(str(roof['source_anchor_id']))}</p>"
            )
            note = str(roof.get("roof_note") or "")
            note_html = f"<p class='note'>{html.escape(note)}</p>" if note else ""
            figures = [_figure_html(roof["aerial"], "普查航测")]
            for slot in roof["satellites"]:
                figures.append(_figure_html(slot, "卫星"))
            cards.append(
                "<article class='roof'>"
                + identity
                + note_html
                + "<table>"
                + "".join(rows_html)
                + "</table><div class='frames'>"
                + "".join(figures)
                + "</div></article>"
            )
        sections.append(
            f"<section id='{html.escape(city)}'><h2>{heading}</h2>"
            + "".join(cards)
            + "</section>"
        )
    seed = html.escape(str(manifest.get("seed")))
    per_band = html.escape(str(manifest.get("per_area_band", manifest.get("max_per_status"))))
    return (
        "<!doctype html><html lang='zh'><head><meta charset='utf-8'>"
        "<title>安装日期交付对照</title><style>"
        "body{margin:0;background:#f4f1ea;color:#1c1915;font-family:Georgia,serif}"
        "header{padding:20px 24px;background:#1c1915;color:#f4f1ea}"
        "h1,h2{font-weight:600}h1{margin:0 0 6px;font-size:22px}h2{margin:0 0 12px}"
        "section{padding:20px 24px;border-top:8px solid #1c1915}"
        "article{background:#fff;border:1px solid #d9d3c7;border-radius:8px;padding:12px;margin:0 0 16px}"
        "table{border-collapse:collapse;margin:8px 0 12px}th,td{border:1px solid #d9d3c7;padding:4px 8px;text-align:left;font-size:14px}"
        "th{font-family:ui-monospace,monospace;font-weight:600;background:#f7f4ee}"
        ".frames{display:flex;flex-wrap:wrap;gap:12px;align-items:flex-start}"
        "figure{margin:0;width:280px}img{width:100%;height:auto;background:#111;border-radius:4px}"
        "figcaption{font-size:13px;line-height:1.35;margin-top:4px}"
        ".gap{width:280px;min-height:120px;padding:10px;background:#fff4f0;border:1px solid #e3b2a3;border-radius:4px}"
        ".note{color:#6b4f2a}"
        "</style></head><body><header><h1>安装日期交付对照</h1>"
        f"<p>冻结表按 scan_status（安装日结论）和安装面积分层。面积档是小于 20、20 到 100、100 平方米及以上，"
        f"每个结论的每个面积档最多 {per_band} 座。开普敦 Top-52 不在这包里。seed {seed}。"
        "图片与文字都在这个目录里，用浏览器直接打开本页即可。</p>"
        "</header>"
        + "".join(sections)
        + "</body></html>\n"
    )


def _figure_html(slot: Mapping[str, object], kind_label: str) -> str:
    date = html.escape(str(slot.get("capture_date") or ""))
    code = html.escape(str(slot.get("code") or ""))
    placement = html.escape(str(slot.get("placement_note") or ""))
    note = html.escape(str(slot.get("note") or ""))
    caption_bits = [kind_label]
    if date:
        caption_bits.append(date)
    if code:
        caption_bits.append(code)
    caption = " · ".join(caption_bits)
    extra = " ".join(bit for bit in (note, placement) if bit)
    if slot.get("origin") == "gap" or not slot.get("relative_path"):
        error = html.escape(str(slot.get("error") or "missing chip"))
        return (
            "<div class='gap'><p>"
            + html.escape(caption)
            + "</p><p>缺口</p><p>"
            + error
            + "</p><p>"
            + extra
            + "</p></div>"
        )
    src = html.escape(str(slot["relative_path"]))
    return (
        "<figure><img src='"
        + src
        + "' alt='"
        + html.escape(caption)
        + "'><figcaption>"
        + html.escape(caption)
        + "<br>"
        + extra
        + "</figcaption></figure>"
    )


def _slot_sidecar(display: Path) -> Path:
    return display.with_suffix(".json")


def _write_json(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _display_bytes(raw_path: Path, raw: bytes, marked: MarkResult) -> bytes:
    if (
        not marked.outline_placed
        and not marked.centroid_placed
        and raw.startswith(PNG_MAGIC)
        and NO_GEOREF_NOTE in marked.note
    ):
        return raw
    return marked.png_bytes


def _materialize_slot(
    *,
    package_dir: Path,
    cache_dir: Path,
    relative_path: str,
    request: ChipRequest,
    local_path: Path | None,
    fetcher: Callable[[ChipRequest], bytes] | None,
    polygon,
    polygon_crs: str | None,
    centroid_xy: tuple[float, float] | None,
    centroid_crs: str,
    draw_outline: bool,
    draw_centroid: bool,
    code: str,
    note: str,
    fetch_log: Path,
) -> dict[str, object]:
    display = package_dir / relative_path
    sidecar = _slot_sidecar(display)
    if display.is_file() and display.stat().st_size > 0 and sidecar.is_file():
        slot = json.loads(sidecar.read_text(encoding="utf-8"))
        print(
            f"chip {request.city} {request.kind} {request.anchor_id} {request.capture_date} "
            f"origin={slot.get('origin')} resume",
            flush=True,
        )
        return slot

    result = resolve_local_or_fetch(
        local_path=local_path,
        fetcher=(lambda: fetcher(request)) if fetcher is not None else None,
    )
    slot: dict[str, object] = {
        "role": request.kind,
        "capture_date": request.capture_date if request.kind == "satellite" else "",
        "code": code,
        "note": note,
        "origin": result.status,
        "error": result.error,
        "relative_path": "",
        "outline_placed": False,
        "centroid_placed": False,
        "placement_note": "",
        "sha256": "",
    }
    if result.status == "gap" or result.data is None:
        slot["placement_note"] = OUTLINE_UNPLACED if draw_outline else CENTROID_UNPLACED
        if display.exists():
            display.unlink()
        _write_json(sidecar, slot)
        print(
            f"chip {request.city} {request.kind} {request.anchor_id} {request.capture_date} "
            f"origin=gap error={slot['error'][:240]}",
            flush=True,
        )
        return slot

    cache_path = cache_dir / relative_path
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    source = result.source_path if result.status == "reused" and result.source_path else cache_path
    if source == cache_path:
        cache_path.write_bytes(result.data)
    marked = burn_review_marks(
        source,
        polygon=polygon if draw_outline else None,
        polygon_crs=polygon_crs,
        centroid_xy=centroid_xy if draw_centroid else None,
        centroid_crs=centroid_crs,
        draw_outline=draw_outline,
        draw_centroid=draw_centroid,
    )
    display.parent.mkdir(parents=True, exist_ok=True)
    payload = _display_bytes(source, result.data, marked)
    display.write_bytes(payload)
    slot["relative_path"] = relative_path
    slot["outline_placed"] = marked.outline_placed
    slot["centroid_placed"] = marked.centroid_placed
    slot["placement_note"] = marked.note
    slot["sha256"] = hashlib.sha256(payload).hexdigest()
    print(
        f"chip {request.city} {request.kind} {request.anchor_id} {request.capture_date} "
        f"origin={slot['origin']} error={slot['error'][:240]}",
        flush=True,
    )
    if result.status == "downloaded":
        with fetch_log.open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    {
                        "relative_path": relative_path,
                        "anchor_id": request.anchor_id,
                        "capture_date": request.capture_date,
                        "kind": request.kind,
                    },
                    sort_keys=True,
                )
                + "\n"
            )
    _write_json(sidecar, slot)
    return slot


def _prune_unsampled_images(output_dir: Path, city: str, keep_ids: set[str]) -> None:
    """Drop image folders left by an earlier draw, including Top-52 roofs."""

    root = output_dir / "images" / city
    if not root.is_dir():
        return
    keep_names = {safe_task_token(sfid) for sfid in keep_ids}
    for child in list(root.iterdir()):
        if child.is_dir() and child.name not in keep_names:
            shutil.rmtree(child)


def _filter_fetch_log(path: Path, keep_prefixes: set[str]) -> None:
    """Keep download-log lines whose image path is still in the sample."""

    if not path.is_file():
        return
    original = path.read_text(encoding="utf-8")
    kept: list[str] = []
    changed = False
    for line in original.splitlines():
        try:
            relative = str(json.loads(line).get("relative_path") or "")
        except json.JSONDecodeError:
            changed = True
            continue
        if any(relative.startswith(prefix) for prefix in keep_prefixes):
            kept.append(line)
        else:
            changed = True
    if not changed:
        return
    text = "\n".join(kept)
    if kept:
        text += "\n"
    path.write_text(text, encoding="utf-8")


def build_package(
    cities: Sequence[CityBundle],
    output_dir: Path,
    *,
    seed: int = DEFAULT_SEED,
    max_per_status: int = DEFAULT_MAX_PER_STATUS,
    cache_dir: Path | None = None,
    satellite_fetcher: Callable[[ChipRequest], bytes] | None = None,
    aerial_fetcher: Callable[[ChipRequest], bytes] | None = None,
    satellite_roots: Sequence[Path] | None = None,
    aerial_reuse_root: Path | None = None,
    aerial_tiles: Sequence[Mapping[str, object]] | None = None,
) -> dict[str, object]:
    """Sample, materialize chips, and write the share directory plus archive."""

    output_dir.mkdir(parents=True, exist_ok=True)
    cache = cache_dir or (output_dir.parent / f"{output_dir.name}_cache")
    cache.mkdir(parents=True, exist_ok=True)
    fetch_log = output_dir / "fetch_log.jsonl"
    extra_roots = [Path(path) for path in (satellite_roots or [])]
    tiles = list(aerial_tiles) if aerial_tiles is not None else []
    if aerial_tiles is None:
        tile_roots: list[Path] = []
        for bundle in cities:
            tile_roots.extend(bundle.aerial_tile_roots)
        if tile_roots:
            tiles = index_aerial_tiles(tile_roots)

    draws: list[tuple[CityBundle, list[dict[str, str]]]] = []
    keep_prefixes: set[str] = set()
    for bundle in cities:
        picked = sample_roofs_by_status(
            bundle.rows,
            city=bundle.city,
            seed=seed,
            max_per_status=max_per_status,
        )
        assert_sample_contract(bundle.rows, picked, city=bundle.city, max_per_status=max_per_status)
        keep_ids = {str(row.get("source_feature_id") or "").strip() for row in picked}
        _prune_unsampled_images(output_dir, bundle.city, keep_ids)
        keep_prefixes.update(
            f"images/{bundle.city}/{safe_task_token(sfid)}/" for sfid in keep_ids
        )
        draws.append((bundle, picked))
    _filter_fetch_log(fetch_log, keep_prefixes)

    city_blocks = []
    for bundle, picked in draws:
        roofs = []
        for row in picked:
            sfid = str(row.get("source_feature_id") or "").strip()
            anchor_id = str(row.get("source_anchor_id") or sfid).strip()
            obs_key = str(row.get(bundle.observation_key) or "").strip()
            observations = bundle.observations.get(obs_key, [])
            slots, roof_note = observation_slots_for_roof(row, observations, city=bundle.city)
            polygon_crs = None
            polygon = None
            packed = bundle.polygons.get(sfid)
            if packed is not None:
                polygon, polygon_crs = packed
            lon = float(row["centroid_lon"])
            lat = float(row["centroid_lat"])
            centroid = (lon, lat)
            sat_bbox = chip_bbox_wgs84(anchor_id or sfid, lon, lat, bundle.metric_crs)
            if polygon is not None:
                aerial_bbox = _aerial_bbox_from_polygon(polygon, polygon_crs or bundle.metric_crs, bundle.metric_crs)
            else:
                aerial_bbox = sat_bbox
            roof_dir = f"images/{bundle.city}/{_roof_key(row)}"
            aerial_name = f"{roof_dir}/aerial.png"
            prepared = find_prepared_aerial(anchor_id, aerial_reuse_root)
            if prepared is None and tiles:
                crop_path = cache / roof_dir / "aerial_crop.tif"
                if not (crop_path.is_file() and crop_path.stat().st_size > 0):
                    cropped = crop_aerial_from_tiles(tiles, aerial_bbox, crop_path)
                    prepared = cropped
                else:
                    prepared = crop_path
            aerial_request = ChipRequest(
                city=bundle.city,
                anchor_id=anchor_id,
                source_feature_id=sfid,
                kind="aerial",
                capture_date="",
                provider="",
                bbox=aerial_bbox,
                wave=str(row.get("wave") or ""),
            )
            aerial_slot = _materialize_slot(
                package_dir=output_dir,
                cache_dir=cache,
                relative_path=aerial_name,
                request=aerial_request,
                local_path=prepared,
                fetcher=aerial_fetcher,
                polygon=polygon,
                polygon_crs=polygon_crs,
                centroid_xy=centroid,
                centroid_crs="EPSG:4326",
                draw_outline=True,
                draw_centroid=False,
                code="",
                note="delivered installation polygon" if polygon is not None else "delivered polygon was not available",
                fetch_log=fetch_log,
            )
            satellite_slots = []
            search_roots = list(bundle.chip_roots) + extra_roots
            for slot in slots:
                day = slot["capture_date"]
                relative = f"{roof_dir}/sat_{day}.png"
                local = find_satellite_chip(anchor_id, day, search_roots, wave=str(row.get("wave") or ""))
                request = ChipRequest(
                    city=bundle.city,
                    anchor_id=anchor_id,
                    source_feature_id=sfid,
                    kind="satellite",
                    capture_date=day,
                    provider=slot.get("provider") or "",
                    bbox=sat_bbox,
                    wave=str(row.get("wave") or ""),
                )
                satellite_slots.append(
                    _materialize_slot(
                        package_dir=output_dir,
                        cache_dir=cache,
                        relative_path=relative,
                        request=request,
                        local_path=local,
                        fetcher=satellite_fetcher,
                        polygon=polygon,
                        polygon_crs=polygon_crs,
                        centroid_xy=centroid,
                        centroid_crs="EPSG:4326",
                        draw_outline=False,
                        draw_centroid=True,
                        code=slot["code"],
                        note=(slot.get("note") + "; " if slot.get("note") else "") + "census centroid",
                        fetch_log=fetch_log,
                    )
                )
            roofs.append(
                {
                    "source_feature_id": sfid,
                    "source_anchor_id": anchor_id,
                    "fields": _status_fields(bundle.city, row),
                    "roof_note": roof_note,
                    "aerial": aerial_slot,
                    "satellites": satellite_slots,
                }
            )
        full_counts = status_counts(bundle.rows, city=bundle.city)
        eligible_counts = status_counts(bundle.rows, city=bundle.city, exclude_cohorts=EXCLUDE_COHORTS)
        band_counts: dict[str, int] = defaultdict(int)
        for row in picked:
            band_counts[str(row.get("area_band") or "")] += 1
        city_blocks.append(
            {
                "city": bundle.city,
                "n_source_rows": sum(full_counts.values()),
                "n_eligible_rows": sum(eligible_counts.values()),
                "n_excluded": sum(full_counts.values()) - sum(eligible_counts.values()),
                "status_counts_source": full_counts,
                "status_counts_eligible": eligible_counts,
                "status_counts_sample": status_counts(picked, city=bundle.city),
                "area_band_counts_sample": dict(band_counts),
                "sample_ids": [str(row.get("source_feature_id") or "").strip() for row in picked],
                "roofs": roofs,
            }
        )

    manifest: dict[str, object] = {
        "schema": SCHEMA,
        "seed": seed,
        "per_area_band": max_per_status,
        "max_per_status": max_per_status,
        "area_breaks_m2": list(AREA_BREAKS_M2),
        "area_band_labels": dict(AREA_BAND_TOKEN),
        "excluded_cohorts": list(EXCLUDE_COHORTS),
        "cities": city_blocks,
    }
    _write_json(output_dir / "manifest.json", manifest)
    (output_dir / "index.html").write_text(render_review_html(manifest), encoding="utf-8")
    readme = (
        "用浏览器打开 index.html。页面、图片和 manifest.json 都在这个文件夹里，不需要网络。\n"
        "开普敦和约翰内斯堡分开排列。每座屋顶旁边的 scan_status 和区间字段来自冻结交付表。\n"
        f"抽样种子 {seed}。开普敦 cohort=top52 不进入抽样。\n"
        "每个 scan_status 再按安装面积分三档：小于 20、20 到 100、100 平方米及以上。\n"
        f"每个结论的每个面积档最多 {max_per_status} 座。面积缺失的屋顶单独成档，不丢弃。\n"
        "航测图上的绿线是交付多边形。卫星图上的红点是冻结表的 census centroid。\n"
        "标成缺口的格子是补下失败，错误原文写在格子里，屋顶仍保留。\n"
    )
    (output_dir / "README.txt").write_text(readme, encoding="utf-8")
    archive = write_share_archive(output_dir)
    manifest["archive"] = str(archive)
    return manifest


def _aerial_bbox_from_polygon(polygon, polygon_crs: str, metric_crs: str) -> tuple[float, float, float, float]:
    minx, miny, maxx, maxy = polygon.bounds
    if str(polygon_crs) != "EPSG:4326":
        xs, ys = _project_xy([minx, maxx], [miny, maxy], src_crs=polygon_crs, dst_crs="EPSG:4326")
        west, east = min(xs), max(xs)
        south, north = min(ys), max(ys)
    else:
        west, east, south, north = min(minx, maxx), max(minx, maxx), min(miny, maxy), max(miny, maxy)
    return expand_bbox_m(
        west,
        south,
        east,
        north,
        pad_m=AERIAL_CONTEXT_M,
        min_span_m=AERIAL_MIN_SPAN_M,
        metric_crs=metric_crs,
    )


def write_share_archive(package_dir: Path) -> Path:
    archive = package_dir.parent / f"{package_dir.name}.tar.gz"
    if archive.exists():
        archive.unlink()
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(package_dir, arcname=package_dir.name)
    return archive


def enrich_providers_from_scan_states(
    observations: dict[str, list[dict[str, str]]],
    state_dir: Path,
) -> None:
    """Fill an empty provider from the read-only merged scan-state JSON.

    Cape Town visit rows store the repaired code but not which GEHI provider
    served that date. The scan state does, and an exact-date download only
    succeeds on the provider that was scored.
    """

    if not state_dir.is_dir():
        return
    for anchor_id, rows in observations.items():
        path = state_dir / f"{anchor_id}.json"
        if not path.is_file():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        by_date: dict[str, str] = {}
        for rnd in payload.get("rounds", []):
            for result in rnd.get("results", []):
                day = _iso_day(result.get("capture_date"))
                provider = str(result.get("provider") or "").strip()
                if day and provider and day not in by_date:
                    by_date[day] = provider
        for row in rows:
            if str(row.get("provider") or "").strip():
                continue
            provider = by_date.get(_iso_day(row.get("capture_date")))
            if provider:
                row["provider"] = provider


def load_observation_index(
    path: Path,
    *,
    city: str,
    wanted: set[str],
) -> dict[str, list[dict[str, str]]]:
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    if not wanted or not path.exists():
        return {}
    key = "source_anchor_id" if city == CT_CITY else "source_feature_id"
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            ident = str(row.get(key) or "").strip()
            if ident not in wanted:
                continue
            grouped[ident].append(dict(row))
    return grouped


def load_polygons(path: Path, layer: str, wanted: set[str]) -> dict[str, tuple[object, str]]:
    if not wanted or not path.exists():
        return {}
    import geopandas as gpd

    ids = ",".join(str(int(item)) for item in sorted(wanted, key=lambda value: int(value)) if str(item).isdigit())
    frame = gpd.read_file(path, layer=layer, where=f"source_feature_id IN ({ids})")
    out: dict[str, tuple[object, str]] = {}
    crs = str(frame.crs) if frame.crs is not None else ""
    for _, row in frame.iterrows():
        sfid = str(int(row["source_feature_id"]))
        geom = row.geometry
        if geom is None:
            continue
        out[sfid] = (shapely_shape(geom.__geo_interface__), crs)
    return out


def _wanted_ids(rows: Sequence[Mapping[str, str]], key: str) -> set[str]:
    return {str(row.get(key) or "").strip() for row in rows if str(row.get(key) or "").strip()}


def build_real_package(args: argparse.Namespace) -> dict[str, object]:
    ct_rows = load_csv(args.ct_csv)
    jhb_rows = load_csv(args.jhb_csv)
    ct_picked = sample_roofs_by_status(ct_rows, city=CT_CITY, seed=args.seed, max_per_status=args.max_per_status)
    jhb_picked = sample_roofs_by_status(jhb_rows, city=JHB_CITY, seed=args.seed, max_per_status=args.max_per_status)
    ct_obs = load_observation_index(
        args.ct_observations,
        city=CT_CITY,
        wanted=_wanted_ids(ct_picked, "source_anchor_id"),
    )
    enrich_providers_from_scan_states(
        ct_obs,
        args.ct_chip_root / "e8_descriptive_20260902" / "merged_scan_states",
    )
    jhb_obs = load_observation_index(
        args.jhb_observations,
        city=JHB_CITY,
        wanted=_wanted_ids(jhb_picked, "source_feature_id"),
    )
    ct_polygons = load_polygons(args.ct_gpkg, "ct_solar_install_dated", _wanted_ids(ct_picked, "source_feature_id"))
    jhb_polygons = load_polygons(args.jhb_gpkg, "solar_install_dated", _wanted_ids(jhb_picked, "source_feature_id"))
    reuse_roots = list(args.satellite_reuse_root or [])
    aerial_roots = list(args.aerial_tile_root or [DEFAULT_CT_AERIAL_ROOT, DEFAULT_JHB_AERIAL_ROOT])
    cache_dir = args.cache_dir or (args.output_dir.parent / f"{args.output_dir.name}_cache")
    if args.fixture_fetch_dir is not None:
        satellite_fetcher = _fixture_fetcher(args.fixture_fetch_dir, "satellite")
        aerial_fetcher = _fixture_fetcher(args.fixture_fetch_dir, "aerial")
        tiles: list[dict[str, object]] | None = []
    else:
        satellite_fetcher = make_gehi_fetcher(cache_dir)
        aerial_fetcher = _live_aerial_fetcher(cache_dir)
        tiles = None
    cities = [
        CityBundle(
            city=CT_CITY,
            rows=ct_rows,
            observations=ct_obs,
            polygons=ct_polygons,
            metric_crs=CT_METRIC,
            observation_key="source_anchor_id",
            chip_roots=[args.ct_chip_root],
            aerial_tile_roots=aerial_roots,
        ),
        CityBundle(
            city=JHB_CITY,
            rows=jhb_rows,
            observations=jhb_obs,
            polygons=jhb_polygons,
            metric_crs=JHB_METRIC,
            observation_key="source_feature_id",
            chip_roots=[args.jhb_chip_root],
            aerial_tile_roots=aerial_roots,
        ),
    ]
    return build_package(
        cities,
        args.output_dir,
        seed=args.seed,
        max_per_status=args.max_per_status,
        cache_dir=cache_dir,
        satellite_fetcher=satellite_fetcher,
        aerial_fetcher=aerial_fetcher,
        satellite_roots=reuse_roots,
        aerial_reuse_root=args.aerial_reuse_root,
        aerial_tiles=tiles,
    )


def _fixture_fetcher(root: Path, kind: str) -> Callable[[ChipRequest], bytes]:
    def fetch(request: ChipRequest) -> bytes:
        token = safe_task_token(request.anchor_id)
        if kind == "aerial":
            error_path = root / "aerial" / f"{token}.error"
            image = None
            for ext in _IMG_EXTS:
                candidate = root / "aerial" / f"{token}{ext}"
                if candidate.is_file():
                    image = candidate
                    break
        else:
            day = _iso_day(request.capture_date)
            error_path = root / token / f"{day}.error"
            image = None
            for ext in _IMG_EXTS:
                candidate = root / token / f"{day}{ext}"
                if candidate.is_file():
                    image = candidate
                    break
        if error_path.is_file():
            raise RuntimeError(error_path.read_text(encoding="utf-8").strip() or "fixture provider error")
        if image is None:
            raise FileNotFoundError(f"fixture {kind} miss for {request.anchor_id} {request.capture_date}")
        return image.read_bytes()

    return fetch


def _live_aerial_fetcher(cache_dir: Path) -> Callable[[ChipRequest], bytes]:
    def fetch(request: ChipRequest) -> bytes:
        destination = cache_dir / "aerial_fetch" / request.city / f"{safe_task_token(request.anchor_id)}.tif"
        if destination.is_file() and destination.stat().st_size > 0:
            return destination.read_bytes()
        if request.city == CT_CITY:
            return fetch_cape_town_wms(request, destination)
        if request.city == JHB_CITY:
            return fetch_jhb_vexcel(request, destination)
        raise RuntimeError(f"no aerial fetcher for {request.city}")

    return fetch


def _hash_inputs(args: argparse.Namespace) -> dict[str, str]:
    hashes = {}
    for label, path in (
        ("ct_csv", args.ct_csv),
        ("ct_gpkg", args.ct_gpkg),
        ("jhb_csv", args.jhb_csv),
        ("jhb_gpkg", args.jhb_gpkg),
    ):
        if path and Path(path).is_file():
            hashes[label] = sha256_file(Path(path))
    return hashes


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument(
        "--max-per-status",
        type=int,
        default=DEFAULT_PER_AREA_BAND,
        help="roofs to draw from each scan_status × area-band cell (default 2)",
    )
    parser.add_argument("--ct-csv", type=Path, default=DEFAULT_CT_CSV)
    parser.add_argument("--ct-gpkg", type=Path, default=DEFAULT_CT_GPKG)
    parser.add_argument("--ct-observations", type=Path, default=DEFAULT_CT_OBS)
    parser.add_argument("--ct-chip-root", type=Path, default=DEFAULT_CT_CHIP_ROOT)
    parser.add_argument("--jhb-csv", type=Path, default=DEFAULT_JHB_CSV)
    parser.add_argument("--jhb-gpkg", type=Path, default=DEFAULT_JHB_GPKG)
    parser.add_argument("--jhb-observations", type=Path, default=DEFAULT_JHB_OBS)
    parser.add_argument("--jhb-chip-root", type=Path, default=DEFAULT_JHB_CHIP_ROOT)
    parser.add_argument("--aerial-tile-root", type=Path, action="append")
    parser.add_argument("--aerial-reuse-root", type=Path, default=None)
    parser.add_argument("--satellite-reuse-root", type=Path, action="append")
    parser.add_argument("--fixture-fetch-dir", type=Path, default=None)
    parser.add_argument("--cache-dir", type=Path, default=None)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    before = _hash_inputs(args)
    print(f"frozen_sha256_before {json.dumps(before, sort_keys=True)}", flush=True)
    manifest = build_real_package(args)
    after = _hash_inputs(args)
    print(f"frozen_sha256_after {json.dumps(after, sort_keys=True)}", flush=True)
    if before != after:
        raise SystemExit("frozen delivery hashes changed during the package build")
    n_roofs = sum(len(city["roofs"]) for city in manifest["cities"])
    n_gap = 0
    n_image = 0
    for city in manifest["cities"]:
        for roof in city["roofs"]:
            for slot in [roof["aerial"], *roof["satellites"]]:
                if slot["origin"] == "gap":
                    n_gap += 1
                elif slot.get("relative_path"):
                    n_image += 1
    print(
        f"package roofs={n_roofs} images={n_image} gaps={n_gap} "
        f"html={args.output_dir / 'index.html'} archive={manifest.get('archive')}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
