#!/usr/bin/env python3
"""Build CV appendix batch v2: D2 preview and D1 failure gallery.

D2 picks one H3-9 cell from the v1 analysis set and draws five aerial
epochs plus a same-cell flow chart. The filename and the corner text
mark it PREVIEW. D1 draws six illustrative chips from Li grids that
were not used to calibrate the threshold. Neither figure is an accuracy
claim.

    python scripts/validation/build_cv_appendix_v2.py all
    python scripts/validation/build_cv_appendix_v2.py assemble

Outputs stay under ~/zasolar_data. Nothing is uploaded from this script.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import subprocess
import sys
from pathlib import Path

import geopandas as gpd
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import requests
import yaml
from matplotlib import patheffects
from PIL import Image, ImageDraw, ImageFont
from shapely.geometry import Point, Polygon, box

from scripts.validation.cv_appendix_constants import FIG_DPI, FIG_WIDTH_MM, WORK_ROOT

ZASOLAR_ROOT = Path("/home/gao/projects/ZAsolar")
if str(ZASOLAR_ROOT) not in sys.path:
    sys.path.insert(0, str(ZASOLAR_ROOT))

STAGING = WORK_ROOT / "staging_v2"
RELEASE = WORK_ROOT / "release_v2"
INPUTS = WORK_ROOT / "v2_inputs"
PHASE0 = WORK_ROOT / "phase0"
CELLS_CSV = WORK_ROOT / "release_v1r2" / "data" / "fig_D3_cells.csv"
PARCEL_GPKG = WORK_ROOT / "parcels" / "parcels.gpkg"
DET_ROOT = ZASOLAR_ROOT / "results" / "cape_town" / "unifiedA_li_clsonly_v2_all"
CALIBRATION_YAML = ZASOLAR_ROOT / "configs" / "eval" / "operating_point_calibration.yaml"
LI_MANIFEST = ZASOLAR_ROOT / "data" / "annotations" / "Capetown_Li" / "annotation_manifest_li.csv"
LI_REMOTE = "dropbox:RA_Solar/Li/capetown"
WMS_URL = "https://cityimg.capetown.gov.za/erdas-iws/ogc/wms/GeoSpatial Datasets"
FIG_WIDTH_IN = FIG_WIDTH_MM / 25.4
M_PER_DEG = 111_320.0
IOU_MIN = 0.5
WINDOW_START = pd.Timestamp("2020-01-01")
WINDOW_END = pd.Timestamp("2024-06-30")
# A cell is set aside when one non-residential parcel holds at least this
# share of the new-PV area. The cut is an exclusion rule for the example,
# not a stock definition.
DOMINANCE_SHARE = 0.40
MIN_RESIDENTIAL_PARCELS = 50
YEAR_COLORS = {
    "2019 or earlier": "#7f7f7f",
    "2020": "#0072B2",
    "2021": "#009E73",
    "2022": "#E69F00",
    "2023": "#D55E00",
    "2024": "#CC79A7",
}
MUNI_EPOCHS = [
    ("2020Jan", "2020-01", "City of Cape Town aerial", 0.08),
    ("2021Mar", "2021-03", "City of Cape Town aerial", 0.08),
    ("2022Jan", "2022-01", "City of Cape Town aerial", 0.08),
    ("2025Jan", "2025-01", "City of Cape Town aerial", 0.05),
]


class Stop(SystemExit):
    """A section 7.4 stop. The message is the whole report."""


def git_commit() -> str:
    repo = Path(__file__).resolve().parents[2]
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


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=str) + "\n")


def apply_style() -> None:
    plt.rcParams.update(
        {
            "font.size": 7,
            "axes.labelsize": 7,
            "axes.titlesize": 7.5,
            "legend.fontsize": 6.5,
            "pdf.fonttype": 42,
            "ps.fonttype": 42,
            "axes.linewidth": 0.5,
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


def heldout_grid_ids() -> list[str]:
    """Li grids registered as a reporting suite, not as the calibration set."""
    payload = yaml.safe_load(CALIBRATION_YAML.read_text())
    grids = list(payload["reporting_suites"]["extra"]["ct_li_heldout_16"]["grid_ids"])
    calibration = set(payload["_ct_unifiedA_li_calibration_grids"])
    overlap = calibration.intersection(grids)
    if overlap:
        raise Stop(f"STOP: held-out grids overlap the calibration set: {sorted(overlap)}")
    if len(grids) != 16:
        raise Stop(f"STOP: expected 16 held-out Li grids, found {len(grids)}.")
    return grids


def square_bbox(lon: float, lat: float, half_m: float) -> tuple[float, float, float, float]:
    """West, south, east, north. The box is square on the ground, not in degrees."""
    dlat = half_m / M_PER_DEG
    dlon = half_m / (M_PER_DEG * np.cos(np.radians(lat)))
    return lon - dlon, lat - dlat, lon + dlon, lat + dlat


def hex_polygon(cell: str) -> Polygon:
    boundary = h3_boundary(cell)
    return Polygon([(lon, lat) for lat, lon in boundary])


def h3_boundary(cell: str):
    import h3

    return h3.cell_to_boundary(cell)


def h3_center(cell: str) -> tuple[float, float]:
    import h3

    lat, lon = h3.cell_to_latlng(cell)
    return float(lat), float(lon)


def rank_cells() -> pd.DataFrame:
    """Cells with at least 50 residential parcels, ranked by new PV parcels.

    A parcel is new when its earliest dated midpoint falls in 2020-01-01
    through 2024-06-30 and the parcel has no earlier dated polygon and no
    left-censored polygon. Building footprints are not available, so the
    residential count is cadastral parcels, the same unit as D3.
    """
    cells = pd.read_csv(CELLS_CSV, usecols=["h3_r9", "n_parcels", "residential_share", "is_residential_cell"])
    links = pd.read_parquet(
        PHASE0 / "census_links.parquet",
        columns=["kind", "midpoint", "h3_r9", "objectid", "area_m2"],
    )
    parcels = pd.read_parquet(
        PHASE0 / "parcel_attr.parquet",
        columns=["objectid", "h3_r9", "is_residential", "primary_zone"],
    )
    links = links[links["h3_r9"].isin(set(cells["h3_r9"])) & links["objectid"].notna()].copy()
    links["objectid"] = links["objectid"].astype(int)
    dated = links[links["kind"] == "dated"]
    first = dated.groupby("objectid").agg(
        first_mid=("midpoint", "min"),
        new_area=("area_m2", "sum"),
        h3_r9=("h3_r9", "first"),
    )
    blocked = set(links.loc[links["kind"] == "left_censored", "objectid"])
    blocked |= set(dated.loc[dated["midpoint"] < WINDOW_START, "objectid"])
    window = (first["first_mid"] >= WINDOW_START) & (first["first_mid"] <= WINDOW_END)
    new = first.loc[window & ~first.index.isin(blocked)].reset_index()
    zone = parcels.set_index("objectid")[["is_residential", "primary_zone"]]
    new = new.join(zone, on="objectid")
    residential_n = (
        parcels[parcels["is_residential"]].groupby("h3_r9").size().rename("n_residential_parcels")
    )
    rows = []
    for cell, sub in new.groupby("h3_r9"):
        area = float(sub["new_area"].sum())
        top = sub.sort_values("new_area", ascending=False).iloc[0]
        top_residential = bool(top["is_residential"]) if pd.notna(top["is_residential"]) else False
        share = float(top["new_area"] / area) if area else 0.0
        rows.append(
            {
                "h3_r9": cell,
                "n_new_parcels": int(len(sub)),
                "new_area_m2": area,
                "top_share": share,
                "top_residential": top_residential,
                "top_zone": None if pd.isna(top["primary_zone"]) else str(top["primary_zone"]),
                "dominated": (not top_residential) and share >= DOMINANCE_SHARE,
            }
        )
    rank = pd.DataFrame(rows).merge(residential_n, on="h3_r9", how="left").merge(cells, on="h3_r9", how="left")
    rank["n_residential_parcels"] = rank["n_residential_parcels"].fillna(0).astype(int)
    rank = rank[rank["n_residential_parcels"] >= MIN_RESIDENTIAL_PARCELS].copy()
    rank = rank.sort_values(["dominated", "n_new_parcels", "new_area_m2"], ascending=[True, False, False])
    rank.to_csv(STAGING / "d2_cell_rank.csv", index=False)
    return rank


def chosen_cell(rank: pd.DataFrame) -> str:
    keep = rank[~rank["dominated"]]
    if keep.empty:
        raise Stop("STOP: every eligible D2 cell is dominated by one non-residential parcel.")
    return str(keep.iloc[0]["h3_r9"])


def cell_frame(cell: str) -> tuple[Polygon, tuple[float, float, float, float], float, float]:
    poly = hex_polygon(cell)
    lat, lon = h3_center(cell)
    minx, miny, maxx, maxy = poly.bounds
    half = max(
        (maxx - minx) * 0.5 * M_PER_DEG * np.cos(np.radians(lat)),
        (maxy - miny) * 0.5 * M_PER_DEG,
    )
    half = float(half + 20.0)
    return poly, square_bbox(lon, lat, half), lat, lon


def wms_png(year: str, bounds, pixels: int, dest: Path) -> None:
    if dest.exists() and dest.stat().st_size > 1000:
        return
    west, south, east, north = bounds
    params = {
        "service": "WMS",
        "version": "1.1.1",
        "request": "GetMap",
        "layers": f"Aerial Imagery_Aerial Imagery {year}",
        "srs": "EPSG:4326",
        "bbox": f"{west},{south},{east},{north}",
        "width": pixels,
        "height": pixels,
        "format": "image/png",
        "transparent": "false",
        "styles": "",
    }
    last = "no response"
    for _attempt in range(3):
        try:
            response = requests.get(WMS_URL, params=params, timeout=120)
        except requests.RequestException as exc:
            last = str(exc)
            continue
        if response.status_code != 200 or "image" not in response.headers.get("Content-Type", ""):
            last = f"HTTP {response.status_code} {response.text[:180]}"
            continue
        image = Image.open(io.BytesIO(response.content)).convert("RGB")
        if np.asarray(image).std() < 3:
            raise Stop(f"STOP: CoCT WMS {year} returned a blank tile for this cell.")
        dest.parent.mkdir(parents=True, exist_ok=True)
        image.save(dest)
        return
    raise Stop(f"STOP: CoCT WMS {year} failed. {last}")


def vexcel_extract(bounds, dest: Path) -> dict:
    """One 2024 ortho chip. A 401 or 403 stops the batch; it is not retried away."""
    if dest.exists() and dest.stat().st_size > 1000 and (dest.with_suffix(".json")).exists():
        return json.loads(dest.with_suffix(".json").read_text())
    from core.vexcel_auth import load_env, resolve_token

    env = load_env(ZASOLAR_ROOT / ".env")
    base = env.get("VEXCEL_API_BASE", "https://api.vexcelgroup.com/v2").rstrip("/")
    token = resolve_token(env, base)
    west, south, east, north = bounds
    lon = (west + east) / 2
    lat = (south + north) / 2
    dated = requests.get(
        f"{base}/ortho/dates",
        params={"wkt": f"POINT({lon} {lat})", "srid": 4326, "layer": "urban", "token": token},
        timeout=90,
    )
    if dated.status_code in (401, 403):
        raise Stop(f"STOP: Vexcel token was refused (HTTP {dated.status_code}).")
    if dated.status_code != 200:
        raise Stop(f"STOP: Vexcel /ortho/dates returned HTTP {dated.status_code}: {dated.text[:240]}")
    meta = dated.json()
    collection = meta.get("collection")
    if not collection or "2024" not in str(collection):
        raise Stop(f"STOP: Vexcel date probe did not return a 2024 collection: {meta}")
    ring = f"{west} {south},{east} {south},{east} {north},{west} {north},{west} {south}"
    extracted = requests.get(
        f"{base}/ortho/extract",
        params={
            "layer": "urban",
            "collection": collection,
            "wkt": f"POLYGON(({ring}))",
            "srid": 4326,
            "image-format": "tiff",
            "token": token,
        },
        timeout=180,
    )
    if extracted.status_code in (401, 403):
        raise Stop(f"STOP: Vexcel extract was refused (HTTP {extracted.status_code}).")
    if extracted.status_code != 200 or extracted.content[:4] not in (b"II*\x00", b"MM\x00*"):
        raise Stop(
            f"STOP: Vexcel extract failed HTTP {extracted.status_code}: {extracted.content[:180]!r}"
        )
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(extracted.content)
    info = {
        "collection": collection,
        "first_capture": meta.get("first-capture-date"),
        "last_capture": meta.get("last-capture-date"),
        "gsd_m": 0.062,
        "gsd_source": "DATA-ct-vexcel-municipal-recheck-2026-10-13; the date call does not return a GSD",
    }
    dest.with_suffix(".json").write_text(json.dumps(info, indent=2) + "\n")
    return info


def read_image(path: Path) -> np.ndarray:
    if path.suffix.lower() in {".tif", ".tiff"}:
        import rasterio

        with rasterio.open(path) as src:
            array = src.read()
        if array.shape[0] >= 3:
            array = np.moveaxis(array[:3], 0, -1)
        else:
            array = array[0]
        if array.dtype != np.uint8:
            array = np.clip(array.astype(float) / max(float(np.percentile(array, 99)), 1) * 255, 0, 255).astype(
                np.uint8
            )
        return array
    return np.asarray(Image.open(path).convert("RGB"))


def desaturate(image: np.ndarray) -> np.ndarray:
    gray = image.astype(float).mean(axis=-1)
    return np.stack([gray, gray, gray], axis=-1).astype(np.uint8)


def fetch_d2(cell: str) -> dict:
    _poly, bounds, lat, lon = cell_frame(cell)
    chip_dir = STAGING / "d2_chips"
    for year, _label, _source, _gsd in MUNI_EPOCHS:
        wms_png(year, bounds, 900, chip_dir / f"{cell}_{year}.png")
    vexcel = vexcel_extract(bounds, chip_dir / f"{cell}_vexcel2024.tif")
    payload = {"cell": cell, "bounds": bounds, "lat": lat, "lon": lon, "vexcel": vexcel}
    write_json(STAGING / "d2_fetch.json", payload)
    return payload


def parcel_year_table(cell: str) -> pd.DataFrame:
    links = pd.read_parquet(
        PHASE0 / "census_links.parquet",
        columns=["kind", "midpoint", "h3_r9", "objectid", "centroid_lon", "centroid_lat"],
    )
    sub = links[(links["h3_r9"] == cell) & links["objectid"].notna()].copy()
    sub["objectid"] = sub["objectid"].astype(int)
    rows = []
    for objectid, group in sub.groupby("objectid"):
        if (group["kind"] == "left_censored").any():
            year = "2019 or earlier"
        else:
            dated = group.loc[group["kind"] == "dated", "midpoint"].dropna()
            if dated.empty:
                continue
            first = dated.min()
            if first < WINDOW_START:
                year = "2019 or earlier"
            elif first <= WINDOW_END:
                year = str(int(first.year))
            else:
                continue
        rows.append(
            {
                "lon": float(group["centroid_lon"].mean()),
                "lat": float(group["centroid_lat"].mean()),
                "year": year,
                "source": "model",
            }
        )
    model = pd.DataFrame(rows)
    sseg = pd.read_parquet(
        PHASE0 / "sseg_links.parquet",
        columns=["commissioned", "commission_date", "h3_r9", "objectid"],
    )
    sseg = sseg[(sseg["h3_r9"] == cell) & sseg["commissioned"] & sseg["objectid"].notna()].copy()
    sseg["objectid"] = sseg["objectid"].astype(int)
    sseg_year = (
        sseg.groupby("objectid")["commission_date"].min().dt.year.rename("year").reset_index()
        if len(sseg)
        else pd.DataFrame(columns=["objectid", "year"])
    )
    return model, sseg_year


def draw_scalebar(ax, bounds, meters: float = 50) -> None:
    west, south, east, north = bounds
    lat = (south + north) / 2
    dlon = meters / (M_PER_DEG * np.cos(np.radians(lat)))
    x0 = west + 0.08 * (east - west)
    y0 = south + 0.08 * (north - south)
    ax.plot([x0, x0 + dlon], [y0, y0], color="black", lw=1.3, solid_capstyle="butt", zorder=6)
    text = ax.text(
        x0 + dlon / 2,
        y0 + 0.03 * (north - south),
        f"{meters:.0f} m",
        ha="center",
        va="bottom",
        fontsize=6,
        color="black",
        zorder=7,
    )
    text.set_path_effects([patheffects.withStroke(linewidth=2.2, foreground="white")])


def plot_basemap(ax, image: np.ndarray, bounds, title: str, scale_m: float = 50) -> None:
    west, south, east, north = bounds
    ax.imshow(image, extent=[west, east, south, north], origin="upper", interpolation="bilinear")
    ax.set_xlim(west, east)
    ax.set_ylim(south, north)
    lat = (south + north) / 2
    ax.set_aspect(1 / np.cos(np.radians(lat)))
    ax.set_title(title, pad=2)
    ax.set_xticks([])
    ax.set_yticks([])
    draw_scalebar(ax, bounds, scale_m)


def plot_d2(rank: pd.DataFrame, cell: str) -> None:
    fetch = json.loads((STAGING / "d2_fetch.json").read_text())
    bounds = tuple(fetch["bounds"])
    poly = hex_polygon(cell)
    chip_dir = STAGING / "d2_chips"
    vexcel = fetch["vexcel"]
    capture = str(vexcel.get("first_capture") or "")[:10]
    epochs = [
        ("2020Jan", "A1. 2020-01\nCoCT aerial, 8 cm", chip_dir / f"{cell}_2020Jan.png"),
        ("2021Mar", "A2. 2021-03\nCoCT aerial, 8 cm", chip_dir / f"{cell}_2021Mar.png"),
        ("2022Jan", "A3. 2022-01\nCoCT aerial, 8 cm", chip_dir / f"{cell}_2022Jan.png"),
        ("vexcel", f"A4. {capture}\nVexcel ortho, 6.2 cm", chip_dir / f"{cell}_vexcel2024.tif"),
        ("2025Jan", "A5. 2025-01\nCoCT aerial, 5 cm", chip_dir / f"{cell}_2025Jan.png"),
    ]
    model, sseg_year = parcel_year_table(cell)
    model.to_csv(STAGING / "d2_cell_points.csv", index=False)
    apply_style()
    fig = plt.figure(figsize=(FIG_WIDTH_IN, 5.15))
    gap = 0.012
    left = 0.02
    width = (1 - left - 0.02 - 4 * gap) / 5
    axes_a = [fig.add_axes([left + i * (width + gap), 0.56, width, 0.34]) for i in range(5)]
    for ax, (_key, title, path) in zip(axes_a, epochs):
        plot_basemap(ax, read_image(path), bounds, title)
        gpd.GeoSeries([poly]).plot(ax=ax, facecolor="none", edgecolor="black", linewidth=0.6)
    map_ax = fig.add_axes([0.04, 0.08, 0.52, 0.40])
    plot_basemap(
        map_ax,
        desaturate(read_image(chip_dir / f"{cell}_2025Jan.png")),
        bounds,
        "B. First dated year on the 2025-01 aerial",
    )
    gpd.GeoSeries([poly]).plot(ax=map_ax, facecolor="none", edgecolor="black", linewidth=0.7)
    for year, color in YEAR_COLORS.items():
        part = model[model["year"] == year]
        if part.empty:
            continue
        map_ax.scatter(
            part["lon"],
            part["lat"],
            s=9,
            c=color,
            label=f"{year} (n={len(part)})",
            linewidths=0,
            zorder=5,
        )
    map_ax.legend(loc="upper right", frameon=True, framealpha=0.85, markerscale=1.4)
    bar_ax = fig.add_axes([0.64, 0.12, 0.32, 0.34])
    years = [2020, 2021, 2022, 2023, 2024]
    model_counts = [int((model["year"] == str(year)).sum()) for year in years]
    sseg_counts = [int((sseg_year["year"] == year).sum()) if len(sseg_year) else 0 for year in years]
    xpos = np.arange(len(years))
    bar_ax.bar(xpos - 0.18, model_counts, width=0.36, color="#0072B2", label="Model parcels")
    bar_ax.bar(xpos + 0.18, sseg_counts, width=0.36, color="#E69F00", label="SSEG parcels")
    bar_ax.set_xticks(xpos, [str(year) for year in years])
    bar_ax.set_ylabel("New parcels")
    bar_ax.set_xlabel("Year")
    bar_ax.set_ylim(0, max(model_counts + sseg_counts + [1]) * 1.18)
    bar_title = "B. New parcels in this cell"
    if sum(sseg_counts) == 0:
        bar_title += "\n(no commissioned SSEG in this cell)"
    bar_ax.set_title(bar_title, pad=2)
    bar_ax.legend(frameon=False, loc="upper right")
    fig.text(
        0.01,
        0.985,
        "PREVIEW — imagery licence not cleared",
        color="#9b1c1c",
        fontsize=8,
        fontweight="bold",
        va="top",
    )
    row = rank[rank["h3_r9"] == cell].iloc[0]
    numbers = {
        "cell": cell,
        "n_new_parcels": int(row["n_new_parcels"]),
        "n_residential_parcels": int(row["n_residential_parcels"]),
        "n_parcels": int(row["n_parcels"]),
        "residential_share": float(row["residential_share"]),
        "top_share": float(row["top_share"]),
        "top_zone": row["top_zone"],
        "model_by_year": dict(zip([str(year) for year in years], model_counts)),
        "sseg_by_year": dict(zip([str(year) for year in years], sseg_counts)),
        "n_already": int((model["year"] == "2019 or earlier").sum()),
        "vexcel_collection": vexcel["collection"],
        "vexcel_first_capture": vexcel["first_capture"],
        "vexcel_last_capture": vexcel["last_capture"],
        "bounds": list(bounds),
    }
    write_json(STAGING / "d2_numbers.json", numbers)
    save_figure(fig, STAGING / "fig_D2_example_cell_preview")
    print("D2 wrote", cell, numbers["model_by_year"], numbers["sseg_by_year"], flush=True)


def ensure_li_gt(grid_id: str, src_name: str) -> Path:
    dest = INPUTS / "li_gt" / f"{grid_id}.gpkg"
    if dest.exists() and dest.stat().st_size > 1000:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    remote = f"{LI_REMOTE}/{src_name}"
    if src_name.lower().endswith(".zip"):
        zip_path = dest.with_suffix(".zip")
        subprocess.check_call(
            ["rclone", "copyto", "--retries", "5", "--low-level-retries", "10", remote, str(zip_path)]
        )
        subprocess.check_call(["unzip", "-o", "-q", str(zip_path), "-d", str(dest.parent / grid_id)])
        found = sorted((dest.parent / grid_id).rglob("*.gpkg"))
        if not found:
            raise Stop(f"STOP: {grid_id} zip contained no gpkg.")
        if len(found) == 1:
            found[0].replace(dest)
            return dest
        # G1896 is three annotation parts. Concatenate them the same way the
        # Li ingest does, and keep the result next to the other grids.
        frames = []
        for path in found:
            layers = gpd.list_layers(path)
            name = str(layers.iloc[0, 0])
            frames.append(gpd.read_file(path, layer=name))
        merged = gpd.GeoDataFrame(pd.concat(frames, ignore_index=True), crs=frames[0].crs)
        merged.to_file(dest, layer="li_ct_gt", driver="GPKG")
        return dest
    subprocess.check_call(
        ["rclone", "copyto", "--retries", "5", "--low-level-retries", "10", remote, str(dest)]
    )
    return dest


def read_gt(path: Path) -> gpd.GeoDataFrame:
    layers = gpd.list_layers(path)
    name = str(layers.iloc[0, 0]) if len(layers.columns) else str(layers[0][0])
    frame = gpd.read_file(path, layer=name)
    frame = frame[frame.geometry.notna() & ~frame.geometry.is_empty].copy()
    if frame.crs is None:
        frame = frame.set_crs(4326)
    return frame.to_crs(32734)


def read_detections(grid_id: str) -> gpd.GeoDataFrame:
    path = DET_ROOT / grid_id / "predictions_metric.gpkg"
    if not path.exists():
        raise Stop(f"STOP: census detections are not on disk for {grid_id}: {path}")
    frame = gpd.read_file(path)
    if "cls_label" in frame.columns:
        frame = frame[frame["cls_label"].astype(str).str.lower().eq("pv")].copy()
    return frame.to_crs(32734)


def match_iou(det: gpd.GeoDataFrame, gt: gpd.GeoDataFrame) -> list[tuple[int, int, float]]:
    if det.empty or gt.empty:
        return []
    pairs = []
    index = gt.sindex
    for i, geom in enumerate(det.geometry):
        for j in index.intersection(geom.bounds):
            other = gt.geometry.iloc[j]
            if not geom.intersects(other):
                continue
            inter = geom.intersection(other).area
            union = geom.area + other.area - inter
            if union <= 0:
                continue
            score = inter / union
            if score >= IOU_MIN:
                pairs.append((score, i, int(j)))
    pairs.sort(reverse=True)
    used_det, used_gt = set(), set()
    kept = []
    for score, i, j in pairs:
        if i in used_det or j in used_gt:
            continue
        used_det.add(i)
        used_gt.add(j)
        kept.append((i, j, float(score)))
    return kept


def residential_lookup(points: gpd.GeoDataFrame) -> pd.Series:
    """True when the point falls in a residential parcel. False if it falls in another parcel."""
    if points.empty:
        return pd.Series(dtype=bool)
    west, south, east, north = points.to_crs(4326).total_bounds
    pad = 0.01
    parcels = gpd.read_file(PARCEL_GPKG, bbox=(west - pad, south - pad, east + pad, north + pad))
    if parcels.crs is None:
        parcels = parcels.set_crs(4326)
    parcels = parcels.to_crs(32734)
    id_col = "OBJECTID" if "OBJECTID" in parcels.columns else "objectid"
    attr = pd.read_parquet(PHASE0 / "parcel_attr.parquet", columns=["objectid", "is_residential"])
    joined = gpd.sjoin(points[["geometry"]], parcels[[id_col, "geometry"]], how="left", predicate="within")
    joined = joined[~joined.index.duplicated(keep="first")]
    ids = pd.to_numeric(joined[id_col], errors="coerce").astype("Int64")
    lookup = attr.set_index(attr["objectid"].astype("Int64"))["is_residential"]
    flag = ids.map(lookup)
    return flag.reindex(points.index)


def build_d1_candidates() -> pd.DataFrame:
    manifest = pd.read_csv(LI_MANIFEST)
    rows = []
    for grid_id in heldout_grid_ids():
        src = manifest.loc[manifest["grid_id"] == grid_id, "src_filename"]
        if src.empty:
            raise Stop(f"STOP: {grid_id} is not in the Li manifest.")
        src_name = str(src.iloc[0])
        # The manifest note "(3 parts merged)" is not part of the Dropbox name.
        if ".zip" in src_name.lower():
            src_name = src_name[: src_name.lower().index(".zip") + 4]
        gt_path = ensure_li_gt(grid_id, src_name)
        det = read_detections(grid_id).reset_index(drop=True)
        gt = read_gt(gt_path).reset_index(drop=True)
        expected = int(manifest.loc[manifest["grid_id"] == grid_id, "n_polygons"].iloc[0])
        if len(gt) != expected:
            raise Stop(f"STOP: {grid_id} has {len(gt)} Li polygons; the manifest says {expected}.")
        matches = match_iou(det, gt)
        matched_det = {i for i, _j, _s in matches}
        matched_gt = {j for _i, j, _s in matches}
        points = gpd.GeoDataFrame(
            geometry=[geom.representative_point() for geom in list(det.geometry) + list(gt.geometry)],
            crs=32734,
        )
        flags = residential_lookup(points)
        det_res = flags.iloc[: len(det)]
        gt_res = flags.iloc[len(det) :].reset_index(drop=True)
        for i, j, score in matches:
            geom = det.geometry.iloc[i]
            rows.append(_candidate(grid_id, "tp", geom, float(det.iloc[i].get("area_m2", geom.area)), score, det_res.iloc[i], det.iloc[i]))
        for i, geom in enumerate(det.geometry):
            if i in matched_det:
                continue
            rows.append(_candidate(grid_id, "fp", geom, float(det.iloc[i].get("area_m2", geom.area)), 0.0, det_res.iloc[i], det.iloc[i]))
        for j, geom in enumerate(gt.geometry):
            if j in matched_gt:
                continue
            rows.append(_candidate(grid_id, "fn", geom, float(geom.area), 0.0, gt_res.iloc[j], None))
        print(f"D1 {grid_id} det={len(det)} gt={len(gt)} matches={len(matches)}", flush=True)
    frame = pd.DataFrame(rows)
    frame.to_csv(STAGING / "d1_candidates.csv", index=False)
    return frame


def _candidate(grid_id, kind, geom, area, score, residential, attributes) -> dict:
    lon, lat = gpd.GeoSeries([geom.centroid], crs=32734).to_crs(4326).iloc[0].coords[0]
    brightness = None
    if attributes is not None and {"mean_r", "mean_g", "mean_b"}.issubset(attributes.index):
        brightness = float(np.mean([attributes["mean_r"], attributes["mean_g"], attributes["mean_b"]]))
    return {
        "grid_id": grid_id,
        "kind": kind,
        "area_m2": area,
        "iou": score,
        "residential": None if pd.isna(residential) else bool(residential),
        "brightness": brightness,
        "lon": float(lon),
        "lat": float(lat),
        "wkt": geom.wkt,
    }


def select_d1(frame: pd.DataFrame) -> pd.DataFrame:
    """One chip per gallery slot. The slot is an illustration, not a rate."""
    picks = {
        "TP residential": _first(
            frame[(frame.kind == "tp") & (frame.residential == True) & frame.area_m2.between(8, 80)],
            ["iou", "area_m2"],
            [False, True],
        ),
        "TP commercial": _first(
            frame[(frame.kind == "tp") & (frame.residential == False) & (frame.area_m2 >= 40)],
            ["area_m2"],
            [False],
        ),
        "FP small bright": _first(
            frame[(frame.kind == "fp") & frame.area_m2.between(1.5, 12)],
            ["brightness", "area_m2"],
            [False, True],
        ),
        "FP darker roof object": _first(
            frame[(frame.kind == "fp") & frame.area_m2.between(8, 50)],
            ["brightness"],
            [True],
        ),
        # Slivers under 4 m² are not a readable "small panel" example. The
        # larger miss stays under 80 m² so one array does not set the zoom
        # for the whole gallery.
        "FN small": _first(
            frame[(frame.kind == "fn") & frame.area_m2.between(4, 12)],
            ["area_m2"],
            [False],
        ),
        "FN larger miss": _first(
            frame[(frame.kind == "fn") & frame.area_m2.between(20, 80)],
            ["area_m2"],
            [False],
        ),
    }
    missing = [name for name, row in picks.items() if row is None]
    if missing:
        raise Stop("STOP: D1 could not fill these slots from the held-out grids: " + ", ".join(missing))
    chosen = pd.DataFrame([{"slot": name, **row.to_dict()} for name, row in picks.items()])
    spans = []
    for wkt in chosen["wkt"]:
        geom = gpd.GeoSeries.from_wkt([wkt], crs=32734).iloc[0]
        spans.append(max(geom.bounds[2] - geom.bounds[0], geom.bounds[3] - geom.bounds[1]))
    half = max(25.0, 0.5 * max(spans) + 15.0)
    chosen["half_m"] = half
    chosen.to_csv(STAGING / "d1_picks.csv", index=False)
    return chosen


def _first(frame: pd.DataFrame, columns: list[str], ascending: list[bool]):
    if frame.empty:
        return None
    ordered = frame.sort_values(columns, ascending=ascending)
    return ordered.iloc[0]


def fetch_d1_chips(chosen: pd.DataFrame) -> None:
    chip_dir = STAGING / "d1_chips"
    for row in chosen.itertuples(index=False):
        dest = chip_dir / f"{row.slot.replace(' ', '_')}.png"
        wms_png("2025Jan", square_bbox(row.lon, row.lat, float(row.half_m)), 700, dest)


def plot_d1(chosen: pd.DataFrame) -> None:
    apply_style()
    fig = plt.figure(figsize=(FIG_WIDTH_IN, 5.15))
    gap_x, gap_y = 0.03, 0.07
    left, bottom, top = 0.03, 0.07, 0.90
    width = (1 - left - 0.02 - 2 * gap_x) / 3
    height = (top - bottom - gap_y) / 2
    half = float(chosen["half_m"].iloc[0])
    for index, row in enumerate(chosen.itertuples(index=False)):
        col, rank = index % 3, index // 3
        ax = fig.add_axes([left + col * (width + gap_x), top - (rank + 1) * height - rank * gap_y, width, height])
        bounds = square_bbox(row.lon, row.lat, half)
        image = read_image(STAGING / "d1_chips" / f"{row.slot.replace(' ', '_')}.png")
        plot_basemap(ax, image, bounds, f"{row.slot}\n{row.grid_id}, {row.area_m2:.0f} m²", scale_m=20)
        geom = gpd.GeoSeries.from_wkt([row.wkt], crs=32734).to_crs(4326)
        color = "#d55e00" if row.kind == "fp" else ("#0072b2" if row.kind == "fn" else "#009e73")
        geom.plot(ax=ax, facecolor="none", edgecolor="white", linewidth=2.8, zorder=5)
        geom.plot(ax=ax, facecolor="none", edgecolor=color, linewidth=1.3, zorder=6)
    fig.text(
        0.50,
        0.015,
        "Illustrative chips, not a precision or recall estimate",
        fontsize=8,
        ha="center",
        va="bottom",
    )
    save_figure(fig, STAGING / "fig_D1_failure_gallery")
    print("D1 gallery wrote", list(chosen["slot"]), "half_m", half, flush=True)


def contact_sheet(frame: pd.DataFrame) -> None:
    """Small chips of the runners-up, so a bad automatic slot can be replaced."""
    queries = {
        "tp_res": frame[(frame.kind == "tp") & (frame.residential == True) & frame.area_m2.between(8, 80)].sort_values("iou", ascending=False),
        "tp_ci": frame[(frame.kind == "tp") & (frame.residential == False)].sort_values("area_m2", ascending=False),
        "fp_bright": frame[(frame.kind == "fp") & frame.area_m2.between(1.5, 12)].sort_values("brightness", ascending=False),
        "fp_dark": frame[(frame.kind == "fp") & frame.area_m2.between(8, 50)].sort_values("brightness", ascending=True),
        "fn_small": frame[(frame.kind == "fn") & frame.area_m2.between(4, 12)].sort_values("area_m2", ascending=False),
        "fn_large": frame[(frame.kind == "fn") & frame.area_m2.between(20, 80)].sort_values("area_m2", ascending=False),
    }
    thumb = 180
    tiles = []
    for name, subset in queries.items():
        for rank, row in enumerate(subset.head(6).itertuples(index=False)):
            dest = STAGING / "d1_review" / f"{name}_{rank}.png"
            wms_png("2025Jan", square_bbox(row.lon, row.lat, 30), thumb, dest)
            image = Image.open(dest).convert("RGB")
            draw = ImageDraw.Draw(image)
            draw.rectangle((0, 0, thumb, 16), fill=(255, 255, 255))
            draw.text((2, 2), f"{name} {row.grid_id} {row.area_m2:.0f}m2", fill=(0, 0, 0))
            tiles.append(image)
    if not tiles:
        return
    cols = 6
    rows = int(np.ceil(len(tiles) / cols))
    sheet = Image.new("RGB", (cols * thumb, rows * thumb), "white")
    for i, image in enumerate(tiles):
        sheet.paste(image, ((i % cols) * thumb, (i // cols) * thumb))
    sheet.save(STAGING / "d1_candidate_sheet.png")


def assemble() -> None:
    required = [
        STAGING / "fig_D2_example_cell_preview.png",
        STAGING / "fig_D2_example_cell_preview.pdf",
        STAGING / "fig_D1_failure_gallery.png",
        STAGING / "fig_D1_failure_gallery.pdf",
        STAGING / "d2_cell_rank.csv",
        STAGING / "d2_cell_points.csv",
        STAGING / "d2_numbers.json",
        STAGING / "d1_picks.csv",
        STAGING / "d1_candidates.csv",
    ]
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise Stop("STOP: assemble is missing " + ", ".join(missing))
    if RELEASE.exists() and any(RELEASE.iterdir()):
        raise Stop(f"STOP: {RELEASE} already exists. Refusing to overwrite a batch.")
    d2 = json.loads((STAGING / "d2_numbers.json").read_text())
    picks = pd.read_csv(STAGING / "d1_picks.csv")
    rank = pd.read_csv(STAGING / "d2_cell_rank.csv")
    top3 = rank[~rank["dominated"]].head(3)
    RELEASE.mkdir(parents=True)
    (RELEASE / "figs").mkdir()
    (RELEASE / "data").mkdir()
    for name in (
        "fig_D2_example_cell_preview.png",
        "fig_D2_example_cell_preview.pdf",
        "fig_D1_failure_gallery.png",
        "fig_D1_failure_gallery.pdf",
    ):
        (RELEASE / "figs" / name).write_bytes((STAGING / name).read_bytes())
    for name in ("d2_cell_rank.csv", "d2_cell_points.csv", "d1_picks.csv"):
        frame = pd.read_csv(STAGING / name)
        if "wkt" in frame.columns:
            frame = frame.drop(columns=["wkt"])
        frame.to_csv(RELEASE / "data" / name, index=False)
    readme, captions = _release_text(d2, picks, top3)
    (RELEASE / "README.md").write_text(readme)
    (RELEASE / "captions.md").write_text(captions)
    manifest = {
        "batch": "v2",
        "revision": "v2",
        "revision_date": "2026-10-14",
        "date": "2026-10-14",
        "code_commit": git_commit(),
        "d2": d2,
        "d2_top3": top3.to_dict(orient="records"),
        "d1_slots": picks.drop(columns=["wkt"], errors="ignore").to_dict(orient="records"),
        "detection_run": str(DET_ROOT),
        "deviations": [
            "D2's residential count is cadastral parcels, not buildings. Building footprints are outside this round.",
            "The balanced sample is the v1 analysis set: H3-9 cells fully inside the January 2025 aerial footprint.",
            "A cell is skipped when one non-residential parcel holds at least 40% of the new-PV area.",
            "D1 uses the 16 Li reporting grids. The other five of the nominal 21 are not in unifiedA_li_clsonly_v2_all.",
            "D1 slots are illustrations. FP and FN visual names are area and brightness sorts, not a labelled taxonomy. FN small is 4–12 m² and FN larger is 20–80 m², so a sub-square-metre sliver or one very large array does not set the shared zoom.",
            "Vexcel GSD is the 6.2 cm figure from the 2026-10-13 recheck. The date call does not return a GSD.",
        ],
    }
    write_json(RELEASE / "release_manifest.json", manifest)
    lines = []
    for path in sorted(RELEASE.rglob("*")):
        if path.is_file() and path.name != "SHA256SUMS.txt":
            lines.append(f"{sha256_file(path)}  {path.relative_to(RELEASE).as_posix()}")
    (RELEASE / "SHA256SUMS.txt").write_text("\n".join(lines) + "\n")
    print("ASSEMBLED", RELEASE, flush=True)


def _release_text(d2: dict, picks: pd.DataFrame, top3: pd.DataFrame) -> tuple[str, str]:
    cards = "\n".join(
        f"- `{row.h3_r9}`: {int(row.n_new_parcels)} new PV parcels, "
        f"{int(row.n_residential_parcels)} residential parcels, "
        f"largest new parcel share {row.top_share:.1%} ({row.top_zone})."
        for row in top3.itertuples(index=False)
    )
    slots = "\n".join(
        f"- {row.slot}: grid {row.grid_id}, area {row.area_m2:.1f} m², IoU {row.iou:.2f}."
        for row in picks.itertuples(index=False)
    )
    capture = str(d2["vexcel_first_capture"])[:10]
    readme = f"""# Cape Town CV appendix figures, batch v2 (2026-10-14)

This folder has two figures: D2 (one example cell, preview) and D1 (six illustrative detection chips).
It is a draft for comment. D1 is not a precision or recall result. D2 is not cleared for publication.

The cell is H3-9 `{d2['cell']}`, the analysis cell with the most parcels whose first dated install falls in January 2020 through June 2024, among cells with at least {MIN_RESIDENTIAL_PARCELS} residential parcels. It has {d2['n_residential_parcels']} residential parcels and {d2['n_new_parcels']} new PV parcels. The largest new parcel is {d2['top_share']:.1%} of the new area ({d2['top_zone']}).

The next two cells, in the same order, are listed so the cell can be swapped before this batch is uploaded:

{cards}

Panel A uses five aerial epochs of that cell. 2023 is omitted: the municipal layer named 2023 is a 2022 image at 25 cm. 2024 is Vexcel collection `{d2['vexcel_collection']}`, first capture {capture}, not the partial municipal 2024 mosaic. The corner text says the imagery licence is not cleared.

Panel B colours one point per model parcel by the year of its first dated midpoint. Grey points were already present by 2019 ({d2['n_already']} parcels) and are not in the bars. Census-bounded parcels have no year and are not drawn. The bars count new model parcels and commissioned SSEG parcels in the cell. Model counts by year: {d2['model_by_year']}. SSEG counts by year: {d2['sseg_by_year']}.

D1 is drawn from the 16 Li grids that were held out of threshold calibration. Detections are the on-disk census run `unifiedA_li_clsonly_v2_all`. A detection matches a Li polygon when the intersection-over-union is at least 0.5. The six chips share one ground scale. Green is a match, orange is a detection with no Li polygon, blue is a Li polygon with no detection.

{slots}

Four defaults from batch v1 still need a collaborator check: the counting unit is a parcel with PV; the stock snapshot is 2024-06-30; model capacity uses 0.19 kWp per square metre; national load-shedding hours are not drawn.

On the uploaded D4 map, a small enclosed hole inside the black outline is outside the analysis footprint, even when the hatch does not fall inside it. That figure was not redrawn in this batch.

The D3 upper-stock slope is lower than the midpoint slope because census-bounded polygons pile up between August and September 2024, after the 2024-06-30 snapshot used on the main panel. That scatter was not redrawn here.
"""
    captions = f"""# Captions, batch v2

## Figure D2

PREVIEW. Imagery licence not cleared. One H3-9 cell in Cape Town (`{d2['cell']}`), chosen as the analysis cell with the most parcels whose first dated install falls between January 2020 and June 2024, among cells with at least 50 residential parcels. (A) The same ground extent in five epochs: City of Cape Town aerial 2020-01, 2021-03 and 2022-01 at 8 cm; Vexcel ortho {capture} (collection `{d2['vexcel_collection']}`) at 6.2 cm; City of Cape Town aerial 2025-01 at 5 cm, which is the census layer. There is no 2023 epoch. (B) The 2025-01 frame, desaturated, with one point per model parcel coloured by the year that parcel first has a dated midpoint. Grey means already present by 2019. The bars are new parcels by calendar year for the model and for commissioned SSEG registrations in the same cell. This cell has no commissioned SSEG parcel in 2020–2024, so only the model bars are drawn. The black line is the H3-9 boundary. The scale bar is 50 m.

## Figure D1

Six chips from Li-annotated Cape Town grids that were not used to set the detection threshold. The background is the January 2025 City of Cape Town aerial. All six chips use the same ground scale; the scale bar is 20 m. Green outline: a detection that overlaps a Li polygon at IoU ≥ 0.5. Orange: a detection with no such overlap. Blue: a Li polygon with no such overlap. The labels name the sort that picked the chip (residential match, non-residential match, small bright unmatched detection, darker unmatched detection, small unmatched Li polygon, larger unmatched Li polygon). They are examples. They are not a class-wise error rate.
"""
    return readme, captions


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("step", choices=["rank", "d2", "d1", "all", "assemble"])
    args = parser.parse_args()
    STAGING.mkdir(parents=True, exist_ok=True)
    try:
        if args.step == "assemble":
            assemble()
            return 0
        rank = rank_cells()
        cell = chosen_cell(rank)
        print("D2 cell", cell, flush=True)
        if args.step == "rank":
            print(rank[~rank["dominated"]].head(3).to_string(index=False))
            return 0
        if args.step in ("d2", "all"):
            fetch_d2(cell)
            plot_d2(rank, cell)
        if args.step in ("d1", "all"):
            candidates = build_d1_candidates()
            contact_sheet(candidates)
            chosen = select_d1(candidates)
            fetch_d1_chips(chosen)
            plot_d1(chosen)
    except Stop as exc:
        print(exc, flush=True)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
