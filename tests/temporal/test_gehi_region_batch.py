"""Unit tests for A3 region-batch geometry/crop/compare/dump. No live GEHI."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_bounds

from scripts.temporal.gehi_common import GehiRunResult
from scripts.temporal.gehi_region_batch import (
    BBox,
    cluster_waste,
    compare_chips,
    crop_chip_from_mosaic,
    dump_region_tiles,
    existing_chip_path,
    parse_world_file,
    region_anchor,
    scan_dump_tiles,
    select_centroid_cluster,
    stitch_dump_tiles,
    union_bbox,
)


def _anchor(aid: str, lon: float, lat: float, half_deg: float = 0.001) -> dict[str, object]:
    return {
        "anchor_id": aid,
        "grid_id": "CPT0001",
        "centroid_lon": lon,
        "centroid_lat": lat,
        "chip_lon_min": lon - half_deg,
        "chip_lat_min": lat - half_deg,
        "chip_lon_max": lon + half_deg,
        "chip_lat_max": lat + half_deg,
    }


def _write_tif(path: Path, bounds: tuple[float, float, float, float], data: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _, height, width = data.shape
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=width,
        height=height,
        count=3,
        dtype="uint8",
        crs="EPSG:4326",
        transform=from_bounds(*bounds, width, height),
    ) as dst:
        dst.write(data)


def test_union_bbox_is_axis_aligned_envelope() -> None:
    a = _anchor("a", 18.40, -34.00)
    b = _anchor("b", 18.41, -33.99)
    box = union_bbox([a, b])
    assert box.lon_min == a["chip_lon_min"]
    assert box.lat_min == a["chip_lat_min"]
    assert box.lon_max == b["chip_lon_max"]
    assert box.lat_max == b["chip_lat_max"]


def test_cluster_waste_overlap_makes_extra_frac_below_one() -> None:
    # Two 96 m-ish chips that heavily overlap.
    a = _anchor("a", 18.400, -34.000, half_deg=0.0005)
    b = _anchor("b", 18.4004, -34.000, half_deg=0.0005)
    waste = cluster_waste([a, b])
    assert waste["n_anchors"] == 2
    assert waste["extra_frac"] < 1.0
    assert waste["union_area_m2"] < waste["sum_chip_area_m2"]


def test_select_centroid_cluster_picks_nearest_and_is_stable() -> None:
    # Three points around 18.40,-34.00 and one far outlier. The group's
    # centroid stays near the trio, so n=2 must come from that trio.
    anchors = [
        _anchor("far", 19.00, -35.00),
        _anchor("near_b", 18.401, -34.001),
        _anchor("near_a", 18.400, -34.000),
        _anchor("near_c", 18.402, -34.000),
    ]
    picked = select_centroid_cluster(anchors, n=2)
    assert "far" not in {p["anchor_id"] for p in picked}
    assert len(picked) == 2
    again = select_centroid_cluster(anchors, n=2)
    assert [p["anchor_id"] for p in again] == [p["anchor_id"] for p in picked]


def test_select_centroid_cluster_returns_all_when_smaller_than_n() -> None:
    anchors = [_anchor("z", 18.4, -34.0), _anchor("a", 18.41, -34.01)]
    picked = select_centroid_cluster(anchors, n=12)
    assert [p["anchor_id"] for p in picked] == ["a", "z"]


def test_pad_bbox_expands_every_edge() -> None:
    box = BBox(18.1, -34.2, 18.3, -34.0)
    padded = box.padded(0.01)
    assert padded.lon_min == pytest.approx(18.09)
    assert padded.lat_min == pytest.approx(-34.21)
    assert padded.lon_max == pytest.approx(18.31)
    assert padded.lat_max == pytest.approx(-33.99)


def test_region_anchor_embeds_union_bbox() -> None:
    box = BBox(18.1, -34.2, 18.3, -34.0)
    region = region_anchor(grid_id="CPT2713", capture_date="2019-05-30", bbox=box)
    assert region["anchor_id"] == "a3_region_CPT2713_20190530"
    assert region["chip_lon_min"] == 18.1
    assert region["chip_lat_max"] == -34.0
    assert region["grid_id"] == "CPT2713"


def test_existing_chip_path_matches_production_layout(tmp_path: Path) -> None:
    path = existing_chip_path(tmp_path, "ct_full_inventory_2026_06_21_merged_t0001", "2019-05-30")
    assert path.name == "ct_full_inventory_2026_06_21_merged_t0001_20190530_vnoversion.tif"
    assert path.parent.name == "z19"


def test_crop_and_compare_identical_window(tmp_path: Path) -> None:
    mosaic_bounds = (18.399, -34.002, 18.403, -33.998)
    rng = np.random.default_rng(7)
    mosaic = rng.integers(20, 220, size=(3, 80, 80), dtype=np.uint8)
    mosaic_path = tmp_path / "mosaic.tif"
    _write_tif(mosaic_path, mosaic_bounds, mosaic)

    anchor = _anchor("chip_a", 18.401, -34.000, half_deg=0.0008)
    crop_path = tmp_path / "crop.tif"
    result = crop_chip_from_mosaic(mosaic_path, anchor, crop_path, recompress=False)
    assert crop_path.is_file()
    assert int(result["width"]) >= 10
    assert int(result["height"]) >= 10

    # Cropping the same window twice must be byte-identical on an uncompressed mosaic.
    crop_b = tmp_path / "crop_b.tif"
    crop_chip_from_mosaic(mosaic_path, anchor, crop_b, recompress=False)
    cmp = compare_chips(crop_path, crop_b)
    assert cmp["comparable"] is True
    assert cmp["mae"] == 0.0
    assert cmp["exact_match_frac"] == 1.0
    assert cmp["reprojected"] is False


def test_compare_detects_disagreement(tmp_path: Path) -> None:
    bounds = (18.40, -34.01, 18.41, -34.00)
    a = np.full((3, 32, 32), 40, dtype=np.uint8)
    b = np.full((3, 32, 32), 50, dtype=np.uint8)
    pa = tmp_path / "a.tif"
    pb = tmp_path / "b.tif"
    _write_tif(pa, bounds, a)
    _write_tif(pb, bounds, b)
    cmp = compare_chips(pa, pb)
    assert cmp["comparable"] is True
    assert cmp["mae"] == 10.0
    assert cmp["exact_match_frac"] == 0.0


def test_crop_raises_when_bbox_outside_mosaic(tmp_path: Path) -> None:
    mosaic_path = tmp_path / "tiny.tif"
    _write_tif(
        mosaic_path,
        (18.40, -34.01, 18.41, -34.00),
        np.zeros((3, 16, 16), dtype=np.uint8),
    )
    far = _anchor("far", 18.50, -34.20)
    try:
        crop_chip_from_mosaic(mosaic_path, far, tmp_path / "out.tif", recompress=False)
    except ValueError as exc:
        assert "outside mosaic" in str(exc)
    else:
        raise AssertionError("expected outside-mosaic crop to fail")


# ---------------------------------------------------------------------------
# dump arm: world files, lossless stitch, dump command wrapper.
# ---------------------------------------------------------------------------

# 2^-9 deg/px keeps every fixture coordinate exactly representable in float64.
DUMP_PX = 0.001953125
DUMP_TILE = 8
DUMP_STEP = DUMP_PX * DUMP_TILE  # 0.015625
DUMP_X0 = 18.0   # mosaic west corner
DUMP_Y0 = -34.0  # mosaic north corner


def _write_dump_tile(
    tile_dir: Path,
    *,
    zoom: int,
    col: int,
    row: int,
    grid_col: int,
    grid_row_north_up: int,
    color: tuple[int, int, int],
    pixel: float = DUMP_PX,
    tile_px: int = DUMP_TILE,
) -> Path:
    """Write one flat-color JPEG tile + .jgw. Flat colors survive JPEG
    (quality=100) byte-exactly, so stitch assertions can use exact equality.
    `grid_col`/`grid_row_north_up` place the tile on the canvas; the filename
    col/row are just labels (GEHI names rows south-up)."""
    from PIL import Image

    tile_dir.mkdir(parents=True, exist_ok=True)
    corner_x = DUMP_X0 + grid_col * tile_px * pixel
    corner_y = DUMP_Y0 - grid_row_north_up * tile_px * pixel
    jpg = tile_dir / f"z={zoom}-Col={col}-Row={row}.jpg"
    img = Image.new("RGB", (tile_px, tile_px), color)
    img.save(jpg, format="JPEG", quality=100, subsampling=0)
    # GEHI 0.5.1 .jgw C/F hold the tile CORNER, not the ESRI-convention pixel
    # center (see WorldFile docstring in gehi_region_batch.py).
    world = (pixel, 0.0, 0.0, -pixel, corner_x, corner_y)
    jpg.with_suffix(".jgw").write_text(
        "\n".join(format(v, ".17g") for v in world) + "\n", encoding="utf-8"
    )
    return jpg


def test_parse_world_file_rejects_rotation_and_bad_signs(tmp_path: Path) -> None:
    good = tmp_path / "good.jgw"
    good.write_text("0.001\n0\n0\n-0.001\n18.5\n-33.9\n", encoding="utf-8")
    world = parse_world_file(good)
    assert world.pixel_w == pytest.approx(0.001)
    assert world.pixel_h == pytest.approx(-0.001)
    assert world.corner_x == pytest.approx(18.5)

    rotated = tmp_path / "rot.jgw"
    rotated.write_text("0.001\n0.1\n0\n-0.001\n18.5\n-33.9\n", encoding="utf-8")
    with pytest.raises(ValueError, match="rotated"):
        parse_world_file(rotated)

    bad_sign = tmp_path / "bad.jgw"
    bad_sign.write_text("0.001\n0\n0\n0.001\n18.5\n-33.9\n", encoding="utf-8")
    with pytest.raises(ValueError, match="pixel sizes"):
        parse_world_file(bad_sign)


def test_stitch_dump_tiles_places_by_coordinates(tmp_path: Path) -> None:
    tile_dir = tmp_path / "tiles"
    # GEHI convention: Col grows east, Row grows NORTH (Row=0 is the south edge).
    colors = {
        (0, 0): (10, 20, 30),   # canvas col 0, row 0 (north-west)
        (1, 0): (40, 50, 60),   # canvas col 1, row 0 (north-east)
        (0, 1): (70, 80, 90),   # canvas col 0, row 1 (south-west)
        (1, 1): (100, 110, 120)
    }
    for (grid_col, grid_row), color in colors.items():
        _write_dump_tile(
            tile_dir,
            zoom=19,
            col=grid_col,
            row=1 - grid_row,  # south-up filename rows
            grid_col=grid_col,
            grid_row_north_up=grid_row,
            color=color,
        )
    out = tmp_path / "mosaic.tif"
    info = stitch_dump_tiles(tile_dir, out, zoom=19)
    assert info["n_tiles"] == 4
    assert info["n_cols"] == 2 and info["n_rows"] == 2
    assert info["filename_grid_mismatches"] == 0
    with rasterio.open(out) as src:
        assert src.width == 16 and src.height == 16
        assert src.count == 3
        assert src.crs is not None and src.crs.to_string() == "EPSG:4326"
        assert src.transform.c == pytest.approx(DUMP_X0)
        assert src.transform.f == pytest.approx(DUMP_Y0)
        assert src.transform.a == pytest.approx(DUMP_PX)
        assert src.transform.e == pytest.approx(-DUMP_PX)
        data = src.read()
    for (grid_col, grid_row), color in colors.items():
        _assert_block_color(data, grid_col, grid_row, color)


def _assert_block_color(
    data: np.ndarray,
    grid_col: int,
    grid_row: int,
    color: tuple[int, int, int],
    *,
    tol: int = 2,
) -> None:
    """Assert one canvas tile block matches `color`.

    JPEG's integer RGB<->YCbCr transform rounds color channels by +/-1 even at
    quality=100, so placement is asserted with a small tolerance; fixture
    colors are spaced >= 40 DN apart so a swapped tile still fails loudly.
    """
    block = data[
        :,
        grid_row * DUMP_TILE : (grid_row + 1) * DUMP_TILE,
        grid_col * DUMP_TILE : (grid_col + 1) * DUMP_TILE,
    ]
    assert block.shape == (3, DUMP_TILE, DUMP_TILE)
    diff = np.abs(block.astype(np.int16) - np.asarray(color, dtype=np.int16)[:, None, None])
    assert int(diff.max()) <= tol, f"tile ({grid_col},{grid_row}) color drift: {diff.max()}"


def _assert_crop_quadrant_color(
    data: np.ndarray, row_slice, col_slice, color: tuple[int, int, int], *, tol: int = 2
) -> None:
    block = data[:, row_slice, col_slice]
    diff = np.abs(block.astype(np.int16) - np.asarray(color, dtype=np.int16)[:, None, None])
    assert int(diff.max()) <= tol, f"crop quadrant color drift: {diff.max()}"


def test_stitch_dump_tiles_ignores_filename_row_direction(tmp_path: Path) -> None:
    """Placement is by world-file coordinates. If a future GEHI names Row
    north-up instead of south-up, the mosaic must come out identical."""
    tile_dir = tmp_path / "tiles"
    north = (200, 10, 10)
    south = (10, 200, 10)
    # Northern tile deliberately named Row=0 (flipped vs GEHI's convention).
    _write_dump_tile(
        tile_dir, zoom=19, col=0, row=0,
        grid_col=0, grid_row_north_up=0, color=north,
    )
    _write_dump_tile(
        tile_dir, zoom=19, col=0, row=1,
        grid_col=0, grid_row_north_up=1, color=south,
    )
    out = tmp_path / "mosaic.tif"
    info = stitch_dump_tiles(tile_dir, out, zoom=19)
    assert info["filename_grid_mismatches"] >= 1  # recorded, not fatal
    with rasterio.open(out) as src:
        data = src.read()
    _assert_block_color(data, 0, 0, north)
    _assert_block_color(data, 0, 1, south)


def test_stitch_dump_tiles_raises_on_hole(tmp_path: Path) -> None:
    tile_dir = tmp_path / "tiles"
    _write_dump_tile(
        tile_dir, zoom=19, col=0, row=1, grid_col=0, grid_row_north_up=0, color=(1, 2, 3)
    )
    _write_dump_tile(
        tile_dir, zoom=19, col=1, row=1, grid_col=1, grid_row_north_up=0, color=(4, 5, 6)
    )
    _write_dump_tile(
        tile_dir, zoom=19, col=0, row=0, grid_col=0, grid_row_north_up=1, color=(7, 8, 9)
    )
    # (grid_col=1, grid_row=1) missing -> hole in the south-east corner.
    with pytest.raises(ValueError, match="hole"):
        stitch_dump_tiles(tile_dir, tmp_path / "mosaic.tif", zoom=19)


def test_stitch_dump_tiles_raises_without_world_file(tmp_path: Path) -> None:
    tile_dir = tmp_path / "tiles"
    jpg = _write_dump_tile(
        tile_dir, zoom=19, col=0, row=0, grid_col=0, grid_row_north_up=0, color=(1, 2, 3)
    )
    jpg.with_suffix(".jgw").unlink()
    with pytest.raises(ValueError, match="missing world file"):
        stitch_dump_tiles(tile_dir, tmp_path / "mosaic.tif", zoom=19)


def test_stitch_then_crop_roundtrip_exact(tmp_path: Path) -> None:
    tile_dir = tmp_path / "tiles"
    for grid_col in (0, 1):
        for grid_row in (0, 1):
            _write_dump_tile(
                tile_dir,
                zoom=19,
                col=grid_col,
                row=1 - grid_row,
                grid_col=grid_col,
                grid_row_north_up=grid_row,
                color=(10 + 40 * grid_col, 20 + 40 * grid_row, 99),
            )
    mosaic = tmp_path / "mosaic.tif"
    stitch_dump_tiles(tile_dir, mosaic, zoom=19)
    # Pixel-aligned inner window: canvas cols/rows 4..12 -> 8x8 px crop.
    anchor = {
        "anchor_id": "inner",
        "chip_lon_min": DUMP_X0 + 4 * DUMP_PX,
        "chip_lat_min": DUMP_Y0 - 12 * DUMP_PX,
        "chip_lon_max": DUMP_X0 + 12 * DUMP_PX,
        "chip_lat_max": DUMP_Y0 - 4 * DUMP_PX,
    }
    crop_path = tmp_path / "crop.tif"
    result = crop_chip_from_mosaic(mosaic, anchor, crop_path, recompress=False)
    assert result["width"] == 8 and result["height"] == 8
    with rasterio.open(crop_path) as src:
        data = src.read()
        assert src.transform.c == pytest.approx(DUMP_X0 + 4 * DUMP_PX)
        assert src.transform.f == pytest.approx(DUMP_Y0 - 4 * DUMP_PX)
    # Rows 4..8 are the north tile row, 8..12 the south; cols 4..8 west, 8..12 east.
    _assert_crop_quadrant_color(data, slice(0, 4), slice(0, 4), (10, 20, 99))
    _assert_crop_quadrant_color(data, slice(0, 4), slice(4, 8), (50, 20, 99))
    _assert_crop_quadrant_color(data, slice(4, 8), slice(0, 4), (10, 60, 99))
    _assert_crop_quadrant_color(data, slice(4, 8), slice(4, 8), (50, 60, 99))


def _poison_runner():
    def runner(cmd_args, *, executable, timeout):
        raise AssertionError(f"runner must not be called: {cmd_args!r}")

    return runner


def _fake_dump_runner(tile_dir_out: list[Path] | None = None, returncode: int = 0):
    captured: list[list[str]] = []

    def runner(cmd_args, *, executable, timeout):
        args = [str(a) for a in cmd_args]
        captured.append(args)
        if returncode == 0:
            out_dir = Path(args[args.index("--output") + 1])
            _write_dump_tile(
                out_dir, zoom=19, col=0, row=0,
                grid_col=0, grid_row_north_up=0, color=(12, 34, 56),
            )
            if tile_dir_out is not None:
                tile_dir_out.append(out_dir)
        return GehiRunResult(
            args=tuple(args),
            returncode=returncode,
            stdout="dump ok" if returncode == 0 else "",
            stderr="" if returncode == 0 else "HTTP 403 blocked",
        )

    return runner, captured


def test_dump_region_tiles_builds_exact_date_command(tmp_path: Path) -> None:
    runner, captured = _fake_dump_runner()
    anchor = _anchor("region_a", 18.401, -33.968, half_deg=0.0008)
    outcome = dump_region_tiles(
        anchor,
        capture_date="2019-05-30",
        zoom=19,
        output_dir=tmp_path / "tiles",
        runner=runner,
    )
    assert outcome.status == "ok"
    assert outcome.n_tiles == 1
    assert len(captured) == 1
    args = captured[0]
    assert args[0] == "dump"
    assert "--exact-date" in args
    assert "--world" in args
    assert args[args.index("--date") + 1] == "2019/05/30"
    # GEHI CLI coordinates are LAT,LONG; the anchor manifest stays lon/lat.
    lower_left = args[args.index("--lower-left") + 1]
    upper_right = args[args.index("--upper-right") + 1]
    assert lower_left.split(",")[0].startswith("-33.96")  # lat first
    assert lower_left.split(",")[1].startswith("18.40")
    assert float(upper_right.split(",")[0]) > float(lower_left.split(",")[0])
    assert args[args.index("--zoom") + 1] == "19"


def test_dump_region_tiles_reuses_existing_tiles(tmp_path: Path) -> None:
    tile_dir = tmp_path / "tiles"
    _write_dump_tile(
        tile_dir, zoom=19, col=0, row=0, grid_col=0, grid_row_north_up=0, color=(1, 1, 1)
    )
    outcome = dump_region_tiles(
        _anchor("region_a", 18.401, -33.968, half_deg=0.0008),
        capture_date="2019-05-30",
        zoom=19,
        output_dir=tile_dir,
        runner=_poison_runner(),
    )
    assert outcome.status == "reused_existing"
    assert outcome.n_tiles == 1
    assert outcome.gehi_command == ""


def test_dump_region_tiles_records_nonzero_returncode(tmp_path: Path) -> None:
    runner, _ = _fake_dump_runner(returncode=1)
    outcome = dump_region_tiles(
        _anchor("region_a", 18.401, -33.968, half_deg=0.0008),
        capture_date="2019-05-30",
        zoom=19,
        output_dir=tmp_path / "tiles",
        runner=runner,
    )
    assert outcome.status == "failed"
    assert outcome.error is not None and "returncode=1" in outcome.error
    assert "403" in outcome.error


def test_dump_region_tiles_fails_when_no_tiles_written(tmp_path: Path) -> None:
    def runner(cmd_args, *, executable, timeout):
        return GehiRunResult(
            args=tuple(str(a) for a in cmd_args), returncode=0, stdout="ok", stderr=""
        )

    outcome = dump_region_tiles(
        _anchor("region_a", 18.401, -33.968, half_deg=0.0008),
        capture_date="2019-05-30",
        zoom=19,
        output_dir=tmp_path / "tiles",
        runner=runner,
    )
    assert outcome.status == "failed"
    assert outcome.error is not None and "no tiles" in outcome.error


# ---------------------------------------------------------------------------
# dump -> per-anchor manifest synthesis (quality_gate contract).
# ---------------------------------------------------------------------------

from scripts.temporal.gehi_download import FIELDS as GEHI_DOWNLOAD_FIELDS  # noqa: E402
from scripts.temporal.gehi_region_batch import (  # noqa: E402
    DUMP_MANIFEST_SIDECAR_FIELDS,
    synthesize_dump_manifest,
)
from scripts.temporal.geid_temporal_common import write_csv_rows  # noqa: E402
from scripts.temporal.run_ct05_chip_pipeline import load_manifests, quality_gate  # noqa: E402


def _qa_passing_crop(path: Path, anchor: dict[str, object], seed: int = 3) -> None:
    """Crop-shaped GeoTIFF whose bounds contain the anchor bbox and whose
    pixels pass the production pixel gate (std/p99-p01/near-black/white)."""
    rng = np.random.default_rng(seed)
    data = rng.integers(20, 220, size=(3, 32, 32), dtype=np.uint8)
    pad = 0.0002
    bounds = (
        float(anchor["chip_lon_min"]) - pad,
        float(anchor["chip_lat_min"]) - pad,
        float(anchor["chip_lon_max"]) + pad,
        float(anchor["chip_lat_max"]) + pad,
    )
    _write_tif(path, bounds, data)


def test_synthesize_dump_manifest_feeds_quality_gate(tmp_path: Path) -> None:
    anchors = [
        _anchor("ok_a", 18.401, -34.000, half_deg=0.0008),
        _anchor("ok_b", 18.411, -34.000, half_deg=0.0008),
        _anchor("failed_crop", 18.421, -34.000, half_deg=0.0008),
        _anchor("no_record", 18.431, -34.000, half_deg=0.0008),
    ]
    for row in anchors:
        row["region_key"] = "cape_town"
    crop_dir = tmp_path / "crops"
    crop_a = crop_dir / "ok_a.tif"
    crop_b = crop_dir / "ok_b.tif"
    _qa_passing_crop(crop_a, anchors[0], seed=3)
    _qa_passing_crop(crop_b, anchors[1], seed=4)
    mosaic = tmp_path / "mosaic.tif"
    _write_tif(mosaic, (18.399, -34.002, 18.433, -33.998), np.zeros((3, 8, 8), np.uint8))
    crops_by_anchor = {
        "ok_a": {"anchor_id": "ok_a", "path": str(crop_a), "status": "ok"},
        "ok_b": {"anchor_id": "ok_b", "path": str(crop_b), "status": "ok"},
        "failed_crop": {
            "anchor_id": "failed_crop",
            "path": str(crop_dir / "failed_crop.tif"),
            "status": "crop_failed",
            "error": "ValueError: requested chip bbox falls outside mosaic",
        },
        # "no_record" deliberately absent: synthesis must not silently drop it.
    }
    manifest_rows, sidecar_rows = synthesize_dump_manifest(
        anchors,
        crops_by_anchor,
        capture_date="2019-05-30",
        version="noversion",
        provider="TM",
        zoom=19,
        region_id="a3_region_CPT0001_20190530",
        tile_dir=str(tmp_path / "tiles"),
        n_tiles=25,
        mosaic_path=str(mosaic),
        dump_gehi_command="GEHistoricalImagery dump ...",
        dump_stdout_sha256="deadbeef",
    )
    assert len(manifest_rows) == 4 and len(sidecar_rows) == 4
    by_id = {str(r["anchor_id"]): r for r in manifest_rows}
    assert by_id["ok_a"]["status"] == "ok"
    assert by_id["ok_a"]["actual_zoom"] == 19
    assert by_id["ok_a"]["exact_date"] == 1
    assert by_id["ok_a"]["sha256"]
    assert by_id["ok_a"]["raster_width_px"] == 32
    assert by_id["failed_crop"]["status"].startswith("crop_failed: ValueError")
    assert by_id["failed_crop"]["actual_zoom"] == ""
    assert by_id["no_record"]["status"] == "crop_failed: no crop record"
    side_by_id = {str(r["anchor_id"]): r for r in sidecar_rows}
    assert side_by_id["ok_a"]["region_id"] == "a3_region_CPT0001_20190530"
    assert side_by_id["ok_a"]["mosaic_sha256"]
    assert set(side_by_id["ok_a"].keys()) == set(DUMP_MANIFEST_SIDECAR_FIELDS)

    manifest_path = tmp_path / "manifest.csv"
    write_csv_rows(manifest_path, manifest_rows, GEHI_DOWNLOAD_FIELDS)
    loaded = load_manifests([manifest_path])
    assert len(loaded) == 4  # keyed by (anchor_id, capture_date, version, provider)

    candidates = [
        {
            "anchor_id": str(a["anchor_id"]),
            "capture_date": "2019-05-30",
            "version": "noversion",
            "provider": "TM",
            "requested_zoom": "19",
        }
        for a in anchors
    ]
    summary = quality_gate(candidates, anchors, [manifest_path], tmp_path / "qa")
    assert summary["candidate_count"] == 4
    assert summary["release_eligible_count"] == 2
    assert summary["failure_classes"] == {"download_failed": 2}
    # Failure rows are classified, never converted to absence observations:
    # they stay in the QA CSV with release_eligible=0.
    import csv as _csv

    with (tmp_path / "qa" / "chip_qa.csv").open(newline="", encoding="utf-8") as fh:
        qa_rows = {row["anchor_id"]: row for row in _csv.DictReader(fh)}
    assert qa_rows["ok_a"]["release_eligible"] == "1"
    assert qa_rows["failed_crop"]["release_eligible"] == "0"
    assert qa_rows["failed_crop"]["failure_class"] == "download_failed"


def test_synthesize_dump_manifest_artifact_id_matches_gehi_download(tmp_path: Path) -> None:
    import hashlib

    anchor = _anchor("ok_a", 18.401, -34.000, half_deg=0.0008)
    crop = tmp_path / "ok_a.tif"
    _qa_passing_crop(crop, anchor)
    rows, _ = synthesize_dump_manifest(
        [anchor],
        {"ok_a": {"anchor_id": "ok_a", "path": str(crop), "status": "ok"}},
        capture_date="2019-05-30",
        version="noversion",
        provider="TM",
        zoom=19,
        region_id="a3_region_CPT0001_20190530",
    )
    want = hashlib.sha1(b"ok_a|19|2019-05-30|noversion").hexdigest()[:16]
    assert rows[0]["artifact_id"] == want


# ---------------------------------------------------------------------------
# full-cell probe cell selection (run_a3_fullcell_probe).
# ---------------------------------------------------------------------------

from scripts.temporal.run_a3_fullcell_probe import (  # noqa: E402
    _tile_estimate,
    group_cells,
    select_cold_candidates,
    select_fullcell_targets,
)


def _cell_anchor(aid: str, grid: str) -> dict[str, object]:
    row = _anchor(aid, 18.4, -34.0)
    row["source_grid"] = grid
    return row


def test_group_cells_excludes_top52_and_marks_grid_id() -> None:
    rows = (
        [_cell_anchor(f"h{i}", "HOT") for i in range(15)]
        + [_cell_anchor(f"c{i}", "COLD") for i in range(15)]
        + [_cell_anchor(f"t{i}", "TINY") for i in range(3)]
    )
    cells = group_cells(rows, exclude_grids={"HOT"}, min_members=12)
    assert set(cells) == {"COLD"}
    assert all(row["grid_id"] == "COLD" for row in cells["COLD"])


def test_select_fullcell_targets_picks_densest_and_reference() -> None:
    cells = {
        "DENSE": [{}] * 584,
        "MID": [{}] * 399,
        "SMALL": [{}] * 12,
    }
    picks = select_fullcell_targets(cells, reference_count=400)
    assert picks == ["DENSE", "MID"]
    # If the densest IS the reference-closest, there is no duplicate arm.
    single = select_fullcell_targets({"ONLY": [{}] * 400}, reference_count=400)
    assert single == ["ONLY"]


def test_select_cold_candidates_ranks_by_median_closeness() -> None:
    cells = {
        "BIG": [{}] * 500,
        "MED_A": [{}] * 30,
        "MED_B": [{}] * 40,
        "SMALL": [{}] * 12,
    }
    # counts: 12, 30, 40, 500 -> median 35; MED_B (40, dist 5) before MED_A (30, dist 5)? 
    # dist ties break on grid_id: MED_A < MED_B.
    picks = select_cold_candidates(cells, n=2)
    assert picks == ["MED_A", "MED_B"]


def test_tile_estimate_covers_padded_union() -> None:
    est = _tile_estimate(0.01, 0.02)  # 14.6 x 29.1 z19 tiles -> ceil
    assert est["n_tiles_x"] == 15
    assert est["n_tiles_y"] == 30
    assert est["n_tiles"] == 15 * 30
    assert _tile_estimate(1e-6, 1e-6)["n_tiles"] == 1


# ---------------------------------------------------------------------------
# production batch lane (run_a3_batch_lane): planner, canary gate, E2E.
# ---------------------------------------------------------------------------

import argparse as _argparse  # noqa: E402

from scripts.temporal.gehi_region_batch import (  # noqa: E402
    plan_cluster_tasks,
    select_canary_grids,
)
from scripts.temporal import run_a3_batch_lane as lane  # noqa: E402

LANE_PX = DUMP_PX           # 2^-9 deg/px, exactly representable
LANE_STEP = DUMP_STEP       # 8 px tile
LANE_HALF = 2 * DUMP_STEP   # anchor chip half-width -> 4-tile (32 px) chips


def _lane_anchor(aid: str, grid: str, lon: float, lat: float) -> dict[str, object]:
    row = _anchor(aid, lon, lat, half_deg=LANE_HALF)
    row["grid_id"] = grid
    row["source_grid"] = grid
    row["region_key"] = "cape_town"
    return row


LANE_ANCHORS = [
    _lane_anchor("ga_1", "GRID_A", 18.40, -34.00),
    _lane_anchor("ga_2", "GRID_A", 18.45, -34.00),
    _lane_anchor("ga_3", "GRID_A", 18.50, -34.00),
    _lane_anchor("gb_1", "GRID_B", 18.60, -34.10),
    _lane_anchor("gb_2", "GRID_B", 18.65, -34.10),
    _lane_anchor("gb_3", "GRID_B", 18.70, -34.10),
]
LANE_DATES = ("2019-05-30", "2020-01-30")


def _lane_candidates() -> list[dict[str, object]]:
    return [
        {
            "anchor_id": a["anchor_id"],
            "capture_date": date,
            "version": "",
            "provider": "TM",
            "requested_zoom": "19",
        }
        for a in LANE_ANCHORS
        for date in LANE_DATES
    ]


def test_plan_cluster_tasks_groups_by_grid_date_provider_zoom() -> None:
    index = {str(a["anchor_id"]): a for a in LANE_ANCHORS}
    tasks = plan_cluster_tasks(_lane_candidates(), index, provider="TM", zoom=19)
    assert len(tasks) == 4  # 2 grids x 2 dates
    task = tasks[0]
    assert task["task_id"] == "GRID_A_20190530_tm_z19"
    assert task["n_members"] == 3
    assert task["anchor_ids"] == ["ga_1", "ga_2", "ga_3"]
    assert task["union"]["lon_min"] == pytest.approx(18.40 - LANE_HALF)
    assert task["union"]["lon_max"] == pytest.approx(18.50 + LANE_HALF)
    assert task["padded_tile_estimate_z19"]["n_tiles"] >= 1
    # provider filter excludes non-matching candidates entirely
    wayback = [dict(c, provider="Wayback") for c in _lane_candidates()]
    assert plan_cluster_tasks(wayback, index, provider="TM", zoom=19) == []
    # unknown anchor fails loud, never silently dropped
    with pytest.raises(ValueError, match="not in anchor index"):
        plan_cluster_tasks(
            [{"anchor_id": "ghost", "capture_date": "2019-05-30",
              "provider": "TM", "requested_zoom": "19"}],
            index,
        )


def test_select_canary_grids_stratified_and_deterministic() -> None:
    counts = {f"G{i:03d}": i * 7 % 43 + 12 for i in range(90)}
    picks = select_canary_grids(counts, n=10)
    assert len(picks) == 10
    assert picks == select_canary_grids(counts, n=10)
    ordered = sorted(counts, key=lambda g: (counts[g], g))
    assert picks[0] == ordered[0] and picks[-1] == ordered[-1]
    assert select_canary_grids(counts, n=200) == ordered


def _write_noise_tile(
    tile_dir: Path, *, corner_x: float, corner_y: float, label_col: int, label_row: int, seed: int
) -> None:
    from PIL import Image

    tile_dir.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(seed)
    arr = rng.integers(20, 220, size=(DUMP_TILE, DUMP_TILE, 3), dtype=np.uint8)
    jpg = tile_dir / f"z=19-Col={label_col}-Row={label_row}.jpg"
    Image.fromarray(arr, "RGB").save(jpg, format="JPEG", quality=100, subsampling=0)
    world = (LANE_PX, 0.0, 0.0, -LANE_PX, corner_x, corner_y)
    jpg.with_suffix(".jgw").write_text(
        "\n".join(format(v, ".17g") for v in world) + "\n", encoding="utf-8"
    )


def _fake_covering_dump_runner(*, fail: bool = False):
    """Dump runner that lays a complete noise-tile grid over the requested
    bbox (snapped to the global LANE_STEP grid), like GEHI dump --world."""
    calls: list[list[str]] = []

    def runner(cmd_args, *, executable, timeout):
        import math as _math

        args = [str(a) for a in cmd_args]
        calls.append(args)
        if fail:
            return GehiRunResult(tuple(args), 1, "", "HTTP 403 blocked")
        lat0, lon0 = (float(v) for v in args[args.index("--lower-left") + 1].split(","))
        lat1, lon1 = (float(v) for v in args[args.index("--upper-right") + 1].split(","))
        out_dir = Path(args[args.index("--output") + 1])
        c0, c1 = _math.floor(lon0 / LANE_STEP), _math.ceil(lon1 / LANE_STEP)
        r0, r1 = _math.floor(lat0 / LANE_STEP), _math.ceil(lat1 / LANE_STEP)
        for ci in range(c0, c1):
            for ri in range(r0, r1):
                _write_noise_tile(
                    out_dir,
                    corner_x=ci * LANE_STEP,
                    corner_y=(ri + 1) * LANE_STEP,
                    label_col=ci - c0,
                    label_row=r1 - 1 - ri,
                    seed=ci * 131 + ri * 17,
                )
        return GehiRunResult(tuple(args), 0, "dump ok", "")

    return runner, calls


def _fake_seq_runner():
    calls: list[list[str]] = []

    def runner(cmd_args, *, executable, timeout):
        args = [str(a) for a in cmd_args]
        calls.append(args)
        lat0, lon0 = (float(v) for v in args[args.index("--lower-left") + 1].split(","))
        lat1, lon1 = (float(v) for v in args[args.index("--upper-right") + 1].split(","))
        out = Path(args[args.index("--output") + 1])
        rng = np.random.default_rng(11)
        _write_tif(out, (lon0, lat0, lon1, lat1), rng.integers(20, 220, (3, 32, 32), np.uint8))
        return GehiRunResult(tuple(args), 0, "ok", "")

    return runner, calls


def _lane_args(tmp_path: Path, *extra: str) -> _argparse.Namespace:
    candidates_csv = tmp_path / "candidates.csv"
    write_csv_rows(
        candidates_csv,
        _lane_candidates(),
        ["anchor_id", "capture_date", "version", "provider", "requested_zoom"],
    )
    anchors_csv = tmp_path / "anchors.csv"
    write_csv_rows(anchors_csv, LANE_ANCHORS, list(LANE_ANCHORS[0].keys()))
    argv = [
        "--candidates-csv", str(candidates_csv),
        "--anchors-csv", str(anchors_csv),
        "--output-dir", str(tmp_path / "lane"),
        "--no-recompress",
        *extra,
    ]
    return lane.parse_args(argv)


def _run_lane_stages(args, dump_runner, seq_runner):
    lane.stage_plan(args)
    plan = lane._load_plan(args.output_dir)
    lane.stage_run(args, plan, dump_runner=dump_runner, seq_runner=seq_runner)
    lane.stage_manifest(args, plan)
    return lane.stage_qa(args, plan)


def test_batch_lane_end_to_end_offline(tmp_path: Path) -> None:
    args = _lane_args(tmp_path, "--grids", "GRID_A,GRID_B")
    dump_runner, dump_calls = _fake_covering_dump_runner()
    seq_runner, seq_calls = _fake_seq_runner()
    qa = _run_lane_stages(args, dump_runner, seq_runner)
    assert len(dump_calls) == 4          # one dump per (grid, date)
    assert len(seq_calls) == 0           # no fallback needed
    assert qa["candidate_count"] == 12   # sequential baseline: 12 calls
    assert qa["release_eligible_count"] == 12
    manifest_rows = list(
        __import__("csv").DictReader((args.output_dir / "manifest" / "manifest.csv").open())
    )
    assert len(manifest_rows) == 12
    assert {r["status"] for r in manifest_rows} == {"ok"}
    report = lane.stage_report(args, lane._load_plan(args.output_dir))
    assert report["invocations_total"] == 4
    assert report["call_reduction"] == 3.0


def test_batch_lane_fallback_when_dump_fails(tmp_path: Path) -> None:
    args = _lane_args(tmp_path, "--grids", "GRID_A,GRID_B")
    dump_runner, dump_calls = _fake_covering_dump_runner(fail=True)
    seq_runner, seq_calls = _fake_seq_runner()
    qa = _run_lane_stages(args, dump_runner, seq_runner)
    assert len(dump_calls) == 4
    assert len(seq_calls) == 12          # every member fell back
    assert qa["release_eligible_count"] == 12
    sidecar = list(
        __import__("csv").DictReader(
            (args.output_dir / "manifest" / "manifest_sidecar.csv").open()
        )
    )
    assert all(r["crop_status"] == "sequential_fallback:ok" for r in sidecar)


def test_batch_lane_plan_fail_closed_over_canary_cap(tmp_path: Path, monkeypatch) -> None:
    # 11 grids of candidates, no env release -> plan must refuse.
    anchors = [
        _lane_anchor(f"x{i}", f"G{i:02d}", 18.4 + i * 0.2, -34.0)
        for i in range(11)
    ]
    candidates = [
        {"anchor_id": a["anchor_id"], "capture_date": "2019-05-30",
         "version": "", "provider": "TM", "requested_zoom": "19"}
        for a in anchors
    ]
    candidates_csv = tmp_path / "candidates.csv"
    write_csv_rows(candidates_csv, candidates,
                   ["anchor_id", "capture_date", "version", "provider", "requested_zoom"])
    anchors_csv = tmp_path / "anchors.csv"
    write_csv_rows(anchors_csv, anchors, list(anchors[0].keys()))
    argv = [
        "--candidates-csv", str(candidates_csv),
        "--anchors-csv", str(anchors_csv),
        "--output-dir", str(tmp_path / "lane"),
    ]
    monkeypatch.delenv("ENABLE_REGION_BATCH", raising=False)
    with pytest.raises(SystemExit, match="FAIL-CLOSED"):
        lane.stage_plan(lane.parse_args(argv))
    monkeypatch.setenv("ENABLE_REGION_BATCH", "true")
    summary = lane.stage_plan(lane.parse_args(argv))
    assert summary["n_grids"] == 11


# ---------------------------------------------------------------------------
# hole-tolerant stitching + surgical fallback (canary finding 2026-08-19).
# ---------------------------------------------------------------------------


def test_stitch_allow_holes_zero_fills_and_reports(tmp_path: Path) -> None:
    tile_dir = tmp_path / "tiles"
    _write_dump_tile(tile_dir, zoom=19, col=0, row=1, grid_col=0, grid_row_north_up=0, color=(1, 2, 3))
    _write_dump_tile(tile_dir, zoom=19, col=1, row=1, grid_col=1, grid_row_north_up=0, color=(4, 5, 6))
    _write_dump_tile(tile_dir, zoom=19, col=0, row=0, grid_col=0, grid_row_north_up=1, color=(7, 8, 9))
    # south-east cell missing -> hole at (1, 1); strict mode still refuses.
    with pytest.raises(ValueError, match="hole"):
        stitch_dump_tiles(tile_dir, tmp_path / "strict.tif", zoom=19)
    info = stitch_dump_tiles(tile_dir, tmp_path / "holed.tif", zoom=19, allow_holes=True)
    assert info["holes"] == [(1, 1)]
    assert info["n_holes"] == 1
    assert info["hole_tile_px"] == (DUMP_TILE, DUMP_TILE)
    with rasterio.open(tmp_path / "holed.tif") as src:
        data = src.read()
    _assert_block_color(data, 0, 0, (1, 2, 3))
    hole = data[:, DUMP_TILE:, DUMP_TILE:]
    assert int(hole.max()) == 0  # zero-filled, never invented pixels


def test_crop_rejects_window_intersecting_hole(tmp_path: Path) -> None:
    bounds = (18.40, -34.02, 18.44, -34.00)
    mosaic = tmp_path / "mosaic.tif"
    _write_tif(mosaic, bounds, np.zeros((3, 32, 32), np.uint8))
    # Mosaic grid: 16 px tiles. Anchor inside the south-east tile (col 1, row 1).
    width_deg = 18.44 - 18.40
    px = width_deg / 32
    inside = {
        "anchor_id": "in_hole",
        "chip_lon_min": 18.42 + px,
        "chip_lat_min": -34.01 - 2 * px,
        "chip_lon_max": 18.42 + 3 * px,
        "chip_lat_max": -34.01 - px,
    }
    with pytest.raises(ValueError, match="intersects dump hole"):
        crop_chip_from_mosaic(
            mosaic, inside, tmp_path / "x.tif",
            recompress=False, hole_cells=[(1, 1)], hole_tile_px=(16, 16),
        )
    # Same hole, window in the north-west tile: allowed.
    outside = {
        "anchor_id": "clear",
        "chip_lon_min": 18.40 + px,
        "chip_lat_min": -34.005,
        "chip_lon_max": 18.40 + 3 * px,
        "chip_lat_max": -34.004,
    }
    result = crop_chip_from_mosaic(
        mosaic, outside, tmp_path / "ok.tif",
        recompress=False, hole_cells=[(1, 1)], hole_tile_px=(16, 16),
    )
    assert Path(result["path"]).is_file()


def test_batch_lane_surgical_fallback_on_holed_dump(tmp_path: Path) -> None:
    """Hole in ga_2's tile: only ga_2 falls back, the rest crop from dump."""
    hole_ci = int(18.4375 / LANE_STEP)   # tile covering ga_2's centre lon
    hole_ri = int(-34.0 / LANE_STEP) - 1  # tile covering ga_2's centre lat

    def runner(cmd_args, *, executable, timeout):
        import math as _math

        args = [str(a) for a in cmd_args]
        lat0, lon0 = (float(v) for v in args[args.index("--lower-left") + 1].split(","))
        lat1, lon1 = (float(v) for v in args[args.index("--upper-right") + 1].split(","))
        out_dir = Path(args[args.index("--output") + 1])
        c0, c1 = _math.floor(lon0 / LANE_STEP), _math.ceil(lon1 / LANE_STEP)
        r0, r1 = _math.floor(lat0 / LANE_STEP), _math.ceil(lat1 / LANE_STEP)
        for ci in range(c0, c1):
            for ri in range(r0, r1):
                if (ci, ri) == (hole_ci, hole_ri) and lon0 < 18.55:
                    continue  # GEHI silently skips this tile (GRID_A tasks only)
                _write_noise_tile(
                    out_dir, corner_x=ci * LANE_STEP, corner_y=(ri + 1) * LANE_STEP,
                    label_col=ci - c0, label_row=r1 - 1 - ri, seed=ci * 131 + ri * 17,
                )
        return GehiRunResult(tuple(args), 0, "dump ok", "")

    args = _lane_args(tmp_path, "--grids", "GRID_A,GRID_B")
    seq_runner, seq_calls = _fake_seq_runner()
    qa = _run_lane_stages(args, runner, seq_runner)
    assert len(seq_calls) == 2  # ga_2 on the two GRID_A dates, nothing else
    assert qa["candidate_count"] == 12
    assert qa["release_eligible_count"] == 12
    sidecar = {
        (r["region_id"], r["anchor_id"]): r["crop_status"]
        for r in __import__("csv").DictReader(
            (args.output_dir / "manifest" / "manifest_sidecar.csv").open()
        )
    }
    assert sidecar[("GRID_A_20190530_tm_z19", "ga_2")] == "sequential_fallback:ok"
    assert sidecar[("GRID_A_20190530_tm_z19", "ga_1")] == "ok"
    assert sidecar[("GRID_B_20190530_tm_z19", "gb_2")] == "ok"
