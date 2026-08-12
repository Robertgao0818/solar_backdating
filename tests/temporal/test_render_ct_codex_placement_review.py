from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import numpy as np
import rasterio
from PIL import Image
from rasterio.transform import from_bounds
from shapely.geometry import Point

from scripts.temporal.render_ct_codex_placement_review import render_one


def test_render_one_reprojects_overlay_to_chip_crs(tmp_path: Path) -> None:
    lon, lat = 18.5, -33.8
    center_3857 = (
        gpd.GeoSeries([Point(lon, lat)], crs="EPSG:4326")
        .to_crs("EPSG:3857")
        .iloc[0]
    )
    target_32734 = (
        gpd.GeoSeries([Point(lon, lat)], crs="EPSG:4326")
        .to_crs("EPSG:32734")
        .iloc[0]
        .buffer(10)
    )

    chip = tmp_path / "wayback_3857.tif"
    bounds = (
        center_3857.x - 100,
        center_3857.y - 100,
        center_3857.x + 100,
        center_3857.y + 100,
    )
    data = np.zeros((3, 100, 100), dtype=np.uint8)
    data[0] = np.arange(100, dtype=np.uint8)[None, :]
    data[1] = np.arange(100, dtype=np.uint8)[:, None]
    data[2] = 80
    with rasterio.open(
        chip,
        "w",
        driver="GTiff",
        width=100,
        height=100,
        count=3,
        dtype="uint8",
        crs="EPSG:3857",
        transform=from_bounds(*bounds, 100, 100),
    ) as dataset:
        dataset.write(data)

    output = tmp_path / "render.png"
    raster_crs = render_one(
        {
            "anchor_id": "test_anchor",
            "source_grid": "CPT0001",
            "centroid_grid": "CPT0001",
            "chip_arm": "A24",
            "area_m2": "20",
            "centroid_lon": str(lon),
            "centroid_lat": str(lat),
            "_capture_date": "2024-01-01",
            "_provider": "Wayback",
        },
        chip,
        19,
        target_32734,
        output,
    )

    assert raster_crs == "EPSG:3857"
    assert output.is_file()
    image = np.asarray(Image.open(output).convert("RGB"))
    assert image.shape == (696, 864, 3)
    # The rendered image contains both the raster and the non-black overlay.
    assert int(image.max()) > 0
