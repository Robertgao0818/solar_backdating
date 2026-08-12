#!/usr/bin/env python3
"""Render a blind Cape Town placement package for Codex visual review.

The package is intentionally downstream-blind: it contains the frozen target
geometry, one suitable pre-census GEHI chip per placement anchor, and geometry
metadata, but no scan state, Gemini verdict, interval, or confidence outcome.
It writes only to the explicitly supplied external data output directory.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import sys
from pathlib import Path
from typing import Iterable

import geopandas as gpd
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import rasterio
from PIL import Image
from shapely.geometry import Point

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def chip_path(chip_root: Path, row: dict[str, str], zoom: int) -> Path:
    anchor = row["anchor_id"]
    capture_date = row["capture_date"][:10].replace("-", "")
    version = row.get("version", "").strip() or "noversion"
    return chip_root / anchor / f"z{zoom}" / f"{anchor}_{capture_date}_v{version}.tif"


def choose_chip(
    rows: Iterable[dict[str, str]], chip_root: Path, census_date: str
) -> tuple[dict[str, str], Path, int] | None:
    candidates = sorted(
        (row for row in rows if row.get("capture_date", "")[:10] < census_date),
        key=lambda row: (row.get("capture_date", "")[:10], row.get("provider", "")),
        reverse=True,
    )
    for row in candidates:
        for zoom in (19, 18):
            path = chip_path(chip_root, row, zoom)
            if path.is_file() and path.stat().st_size > 0:
                return row, path, zoom
    return None


def _rgb(data: np.ndarray) -> np.ndarray:
    if data.shape[0] == 1:
        data = np.repeat(data, 3, axis=0)
    if data.shape[0] < 3:
        raise ValueError(f"chip has fewer than 3 bands: shape={data.shape}")
    image = np.moveaxis(data[:3], 0, -1)
    if image.dtype != np.uint8:
        image = image.astype(np.float32)
        lo = np.nanpercentile(image, 1)
        hi = np.nanpercentile(image, 99)
        image = np.clip((image - lo) / max(hi - lo, 1e-6), 0, 1)
    return image


def render_one(
    row: dict[str, str],
    chip: Path,
    zoom: int,
    geometry,
    output: Path,
) -> str:
    with rasterio.open(chip) as dataset:
        image = _rgb(dataset.read())
        bounds = dataset.bounds
        raster_crs = dataset.crs or "EPSG:4326"

    target = geometry
    if target is None or target.is_empty:
        raise ValueError("empty target geometry")
    target = gpd.GeoSeries([target], crs="EPSG:32734").to_crs(raster_crs).iloc[0]
    centroid = gpd.GeoSeries(
        [Point(float(row["centroid_lon"]), float(row["centroid_lat"]))],
        crs="EPSG:4326",
    ).to_crs(raster_crs).iloc[0]

    fig, ax = plt.subplots(figsize=(7.2, 5.8), dpi=120)
    ax.imshow(
        image,
        extent=(bounds.left, bounds.right, bounds.bottom, bounds.top),
        origin="upper",
    )
    gpd.GeoSeries([target], crs=raster_crs).boundary.plot(
        ax=ax, color="#ff2458", linewidth=2.0, alpha=0.95
    )
    ax.scatter(
        [centroid.x],
        [centroid.y],
        marker="+",
        s=90,
        linewidths=1.8,
        color="#00e5ff",
    )
    title = (
        f"{row['anchor_id'].split('_')[-1]} | {row.get('source_grid','')}→"
        f"{row.get('centroid_grid','')} | {row.get('chip_arm','')} | "
        f"{row.get('area_m2','')} m²\n"
        f"capture {row.get('_capture_date','')} / {row.get('_provider','')} / z{zoom}"
    )
    ax.set_title(title, fontsize=8)
    ax.set_xlabel("red = source polygon   cyan = anchor centroid", fontsize=7)
    ax.set_xticks([])
    ax.set_yticks([])
    fig.tight_layout(pad=0.5)
    fig.savefig(output, facecolor="black")
    plt.close(fig)
    return str(raster_crs)


def make_sheets(image_paths: list[Path], output_dir: Path, per_sheet: int) -> list[Path]:
    sheet_dir = output_dir / "sheets"
    sheet_dir.mkdir(parents=True, exist_ok=True)
    sheets: list[Path] = []
    columns = 3
    rows = max(1, math.ceil(per_sheet / columns))
    for start in range(0, len(image_paths), per_sheet):
        batch = image_paths[start : start + per_sheet]
        figure, axes = plt.subplots(rows, columns, figsize=(18, rows * 5.0), dpi=100)
        axes = np.atleast_1d(axes).reshape(rows, columns)
        for axis, image_path in zip(axes.flat, batch):
            axis.imshow(np.asarray(Image.open(image_path).convert("RGB")))
            axis.axis("off")
        for axis in axes.flat[len(batch) :]:
            axis.axis("off")
        sheet = sheet_dir / f"placement_sheet_{start // per_sheet + 1:02d}.png"
        figure.tight_layout(pad=0.2)
        figure.savefig(sheet, facecolor="black")
        plt.close(figure)
        sheets.append(sheet)
    return sheets


def build(
    *,
    placement_csv: Path,
    catalog_csv: Path,
    chip_root: Path,
    inventory_gpkg: Path,
    output_dir: Path,
    census_date: str,
    per_sheet: int,
    review_run_id: str,
) -> dict[str, object]:
    if output_dir.exists() and any(output_dir.iterdir()):
        raise FileExistsError(f"refusing to overwrite non-empty output: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    image_dir = output_dir / "images"
    image_dir.mkdir()

    placement = read_csv(placement_csv)
    catalog_by_anchor: dict[str, list[dict[str, str]]] = {}
    for row in read_csv(catalog_csv):
        catalog_by_anchor.setdefault(row["anchor_id"], []).append(row)

    source_ids = {int(row["source_feature_id"]) for row in placement if row.get("source_feature_id")}
    inventory = gpd.read_file(inventory_gpkg, columns=["source_feature_id", "geometry"])
    inventory = inventory[inventory["source_feature_id"].isin(source_ids)].copy()
    inventory = inventory.set_index("source_feature_id")

    manifest_rows: list[dict[str, str]] = []
    image_paths: list[Path] = []
    counts: dict[str, int] = {}
    for index, row in enumerate(placement, start=1):
        anchor_id = row["anchor_id"]
        record = {
            "review_index": str(index),
            "anchor_id": anchor_id,
            "source_feature_id": row.get("source_feature_id", ""),
            "source_grid": row.get("source_grid", ""),
            "centroid_grid": row.get("centroid_grid", ""),
            "chip_arm": row.get("chip_arm", ""),
            "area_m2": row.get("area_m2", ""),
            "pilot_strata": row.get("pilot_strata", ""),
            "geometry_exception": row.get("geometry_exception", ""),
            "review_required": row.get("review_required", ""),
            "catalog_status": row.get("catalog_status", ""),
            "review_verdict": "",
            "review_notes": "",
            "chip_path": "",
            "chip_sha256": "",
            "capture_date": "",
            "provider": "",
            "achieved_zoom": "",
            "raster_crs": "",
            "image_path": "",
            "sheet_path": "",
        }
        if row.get("catalog_status") == "no_history":
            counts["no_history_no_image"] = counts.get("no_history_no_image", 0) + 1
            manifest_rows.append(record)
            continue

        selected = choose_chip(catalog_by_anchor.get(anchor_id, []), chip_root, census_date)
        if selected is None:
            counts["no_chip"] = counts.get("no_chip", 0) + 1
            manifest_rows.append(record)
            continue
        catalog_row, chip, zoom = selected
        feature_id = int(row["source_feature_id"])
        geometry = inventory.loc[feature_id, "geometry"] if feature_id in inventory.index else None
        if geometry is None:
            counts["no_geometry"] = counts.get("no_geometry", 0) + 1
            manifest_rows.append(record)
            continue

        image_name = f"{index:03d}_{anchor_id}.png"
        image_path = image_dir / image_name
        render_row = dict(row)
        render_row["_capture_date"] = catalog_row["capture_date"][:10]
        render_row["_provider"] = catalog_row.get("provider", "")
        raster_crs = render_one(render_row, chip, zoom, geometry, image_path)
        image_paths.append(image_path)
        record.update(
            {
                "chip_path": str(chip),
                "chip_sha256": sha256(chip),
                "capture_date": catalog_row["capture_date"][:10],
                "provider": catalog_row.get("provider", ""),
                "achieved_zoom": str(zoom),
                "raster_crs": raster_crs,
                "image_path": str(image_path),
            }
        )
        counts["rendered"] = counts.get("rendered", 0) + 1
        manifest_rows.append(record)

    sheets = make_sheets(image_paths, output_dir, per_sheet)
    by_image = {str(path): path for path in image_paths}
    for row in manifest_rows:
        image = by_image.get(row["image_path"])
        if image is not None:
            sheet_index = image_paths.index(image) // per_sheet
            row["sheet_path"] = str(sheets[sheet_index])

    manifest_path = output_dir / "placement_review_manifest.csv"
    fields = list(manifest_rows[0]) if manifest_rows else []
    with manifest_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(manifest_rows)

    payload = {
        "schema_version": "codex_placement_review_v1",
        "review_run_id": review_run_id,
        "reviewer_type": "codex_visual",
        "reviewer_engine": "Codex view_image",
        "blind_to_production": True,
        "status": "ready_for_codex_review",
        "census_date": census_date,
        "placement_count": len(placement),
        "rendered_count": len(image_paths),
        "counts": dict(sorted(counts.items())),
        "input_sha256": {
            "placement_csv": sha256(placement_csv),
            "catalog_csv": sha256(catalog_csv),
            "inventory_gpkg": sha256(inventory_gpkg),
        },
        "renderer_sha256": sha256(Path(__file__).resolve()),
        "manifest_path": str(manifest_path),
        "sheet_paths": [str(path) for path in sheets],
        "verdict_vocabulary": ["PASS", "MISALIGNED", "EXCEPTION", "UNREVIEWABLE"],
        "note": "No downstream scan state, Gemini verdict, interval, or confidence outcome is included.",
    }
    (output_dir / "CODEX_REVIEW_RUN.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--placement-csv", type=Path, required=True)
    parser.add_argument("--catalog-csv", type=Path, required=True)
    parser.add_argument("--chip-root", type=Path, required=True)
    parser.add_argument("--inventory-gpkg", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--census-date", default="2025-01-31")
    parser.add_argument("--per-sheet", type=int, default=12)
    parser.add_argument(
        "--review-run-id",
        default="ct06_codex_placement_20260801_pass1",
    )
    args = parser.parse_args()
    if args.per_sheet < 1:
        raise SystemExit("--per-sheet must be positive")
    result = build(
        placement_csv=args.placement_csv,
        catalog_csv=args.catalog_csv,
        chip_root=args.chip_root,
        inventory_gpkg=args.inventory_gpkg,
        output_dir=args.output_dir,
        census_date=args.census_date,
        per_sheet=args.per_sheet,
        review_run_id=args.review_run_id,
    )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
