"""Unit + integration tests for the R1 marker-free crop generator (PRD §6.2).

Two tiers:

* Pure/synthetic (always run): the crop-window formula, world<->pixel maths,
  ROI/context geometry on a hand-built dual-CRS frame, resume idempotency on a
  synthetic TIFF, and ``crop_geometry`` version propagation.
* Real-frame (skipped when ``~/zasolar_data`` is absent): one genuine EPSG:3857
  (Wayback) frame and one EPSG:4326 (TM) frame from the frozen manifest -- the
  <1 source-pixel crop-centre reprojection invariant, ROI-in-bounds, A24/A48 FoV
  sizes, and byte-identity to the teacher's ``draw_marker=False`` render.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from scripts.temporal import build_r1_marker_free_crops as R1

MANIFEST = (
    Path.home()
    / "zasolar_data/geid_temporal/run3_native_line_2026-07"
    / "r0_manifest_v1/manifest.parquet"
)
_HAS_DATA = MANIFEST.exists()
requires_data = pytest.mark.skipif(
    not _HAS_DATA, reason="frozen R0 manifest / zasolar_data drive not present"
)


# ======================================================================== #
# Pure crop-window formula.
# ======================================================================== #
def test_crop_window_a24_fraction_and_upsample():
    # 96 m chip, 24 m FoV -> quarter of the short side; upsampled to 256.
    win = R1.compute_crop_window(355, 363, fov_m=24.0, min_output_px=256)
    assert win.crop_size_m == 24.0
    assert win.crop_px == round(355 * 24.0 / 96.0)  # short side = 355
    assert win.out_px == 256
    assert win.scale == pytest.approx(256.0 / win.crop_px)
    # centred window, clamped inside the raster
    assert 0 <= win.left <= 355 - win.crop_px
    assert 0 <= win.top <= 363 - win.crop_px


def test_crop_window_a48_is_double_fov():
    a24 = R1.compute_crop_window(356, 356, fov_m=24.0)
    a48 = R1.compute_crop_window(356, 356, fov_m=48.0)
    assert a24.crop_size_m == 24.0 and a48.crop_size_m == 48.0
    # 48 m window spans ~2x the pixels of the 24 m window on the same raster.
    assert a48.crop_px == pytest.approx(2 * a24.crop_px, abs=1)


def test_arm_render_params_from_registry():
    # arm FoV is sourced from chip_geometry, not hard-coded.
    assert R1.arm_render_params("A24") == (24.0, 256)
    assert R1.arm_render_params("A48") == (48.0, 256)


def test_world_to_src_px_roundtrip():
    tfw = (0.3, -0.3, 1000.0, 2000.0)  # A, E<0, C, F
    a, e, c, f = tfw
    col, row = 40.7, 88.2
    x = c + col * a
    y = f + row * e
    gc, gr = R1.world_to_src_px(x, y, tfw)
    assert gc == pytest.approx(col)
    assert gr == pytest.approx(row)


# ======================================================================== #
# Synthetic dual-CRS geometry (no real data): a frame whose centroid sits at
# the raster centre must reproject to the crop centre within <1 px, for BOTH a
# degree-metric (4326) and a metre-metric (3857) frame.
# ======================================================================== #
def _synthetic_row(crs: str, area_m2: float = 20.0) -> pd.Series:
    """Build a manifest-shaped row with the centroid exactly at the raster
    centre, TFW consistent with the CRS. Reuses pyproj so the metric transforms
    are exercised for real."""
    from pyproj import Transformer

    lon, lat = 28.09, -26.118  # Johannesburg, matches the corpus
    W = H = 356
    if crs == "EPSG:4326":
        px_deg = 2.682209e-06
        a, e = px_deg, -px_deg
        cx, cy = lon, lat  # centre in degrees
        c = cx - (W / 2.0) * a
        f = cy - (H / 2.0) * e
        clon, clat = lon, lat
    else:  # EPSG:3857
        tf = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)
        mx, my = tf.transform(lon, lat)
        a = 0.29858
        e = -a
        c = mx - (W / 2.0) * a
        f = my - (H / 2.0) * e
        clon, clat = lon, lat
    return pd.Series(
        {
            "anchor_id": "synthetic_t01",
            "capture_date": "2020-01-01",
            "chip_arm": "A24",
            "source_area_m2": area_m2,
            "raster_crs": crs,
            "provider": "TM" if crs == "EPSG:4326" else "Wayback",
            "raster_width_px": W,
            "raster_height_px": H,
            "tfw_a": a,
            "tfw_e": e,
            "tfw_c": c,
            "tfw_f": f,
            "centroid_lon": clon,
            "centroid_lat": clat,
            "src_tiff_sha256": "deadbeef" * 8,
        }
    )


@pytest.mark.parametrize("crs", ["EPSG:4326", "EPSG:3857"])
def test_synthetic_center_reproj_under_one_pixel(crs):
    tfx = R1._Transformers()
    rec = R1.compute_geometry_record(_synthetic_row(crs), tfx)
    assert rec.raster_crs == crs
    assert rec.center_reproj_err_px < 1.0
    # ROI square well inside the crop for a 20 m² (~4.5 m) target in a 24 m FoV.
    assert rec.roi_in_bounds
    roi = json.loads(rec.roi_px)
    assert len(roi) == 4
    for ox, oy in roi:
        assert 0 <= ox <= rec.out_px
        assert 0 <= oy <= rec.out_px


@pytest.mark.parametrize("crs", ["EPSG:4326", "EPSG:3857"])
def test_synthetic_roi_and_context_edges(crs):
    tfx = R1._Transformers()
    rec = R1.compute_geometry_record(_synthetic_row(crs, area_m2=16.0), tfx)
    # sqrt(16)=4 m ROI edge; context = min(fov, 4*2)=8 m.
    assert rec.roi_edge_m == pytest.approx(4.0, abs=1e-6)
    assert rec.context_edge_m == pytest.approx(8.0, abs=1e-6)


def test_large_target_roi_clamped_to_fov():
    # area far larger than the FoV: ROI edge clamps to the review extent (24 m).
    tfx = R1._Transformers()
    rec = R1.compute_geometry_record(_synthetic_row("EPSG:4326", area_m2=4000.0), tfx)
    assert rec.roi_edge_m == pytest.approx(24.0, abs=1e-6)
    assert rec.context_edge_m == pytest.approx(24.0, abs=1e-6)


# ======================================================================== #
# Render + resume idempotency on a synthetic TIFF (no real data).
# ======================================================================== #
def _write_synthetic_tiff(path: Path, w: int = 356, h: int = 356) -> str:
    from PIL import Image

    rng = np.random.default_rng(7)
    arr = rng.integers(0, 255, size=(h, w, 3), dtype=np.uint8)
    img = Image.fromarray(arr, "RGB")
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, format="TIFF")
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


def _synthetic_manifest(tmp_path: Path) -> tuple[Path, Path]:
    tiff = tmp_path / "src" / "synthetic.tif"
    sha = _write_synthetic_tiff(tiff)
    row = _synthetic_row("EPSG:4326").to_dict()
    row["src_tiff_sha256"] = sha
    row["src_tiff_path"] = str(tiff)
    df = pd.DataFrame([row])
    mpath = tmp_path / "manifest.parquet"
    df.to_parquet(mpath, index=False)
    return mpath, tiff


def test_render_and_resume_idempotent(tmp_path):
    mpath, _ = _synthetic_manifest(tmp_path)
    out = tmp_path / "r1_crops_v1"
    args = R1.parse_args(
        ["--manifest", str(mpath), "--out-dir", str(out), "--no-sha-check"]
    )

    assert R1.run(args) == 0
    status1 = json.loads((out / "_R1_CROPS_STATUS.json").read_text())
    assert status1["rendered"] == 1 and status1["skipped"] == 0

    # the one crop exists, marker-free crop_geometry tag in the filename
    pngs = list(out.rglob(f"*.{R1.CROP_GEOMETRY_TAG}.png"))
    assert len(pngs) == 1
    mtime1 = pngs[0].stat().st_mtime_ns

    # second run: everything skipped, nothing re-rendered, byte-stable
    assert R1.run(args) == 0
    status2 = json.loads((out / "_R1_CROPS_STATUS.json").read_text())
    assert status2["rendered"] == 0 and status2["skipped"] == 1
    assert pngs[0].stat().st_mtime_ns == mtime1


def test_crop_geometry_version_propagates(tmp_path):
    mpath, _ = _synthetic_manifest(tmp_path)
    out = tmp_path / "r1_crops_v1"
    args = R1.parse_args(
        ["--manifest", str(mpath), "--out-dir", str(out), "--no-sha-check"]
    )
    assert R1.run(args) == 0
    idx = pd.concat(
        pd.read_parquet(p) for p in (out / "crop_geometry_index").glob("*.parquet")
    )
    assert (idx["crop_geometry"] == R1.CROP_GEOMETRY_VERSION).all()
    # the output png path carries the geometry tag
    assert idx["png_relpath"].str.contains(f".{R1.CROP_GEOMETRY_TAG}.png").all()


def test_manifest_sha_gate_rejects_wrong_data(tmp_path):
    mpath, _ = _synthetic_manifest(tmp_path)
    # sha check ON against a synthetic (wrong-sha) manifest must abort loudly.
    with pytest.raises(SystemExit):
        R1.load_manifest(mpath, check_sha=True)


# ======================================================================== #
# Real-frame integration (skipped without the drive).
# ======================================================================== #
@requires_data
def test_real_manifest_sha_locks():
    got = R1.sha256_file(MANIFEST)
    assert got == R1.EXPECTED_MANIFEST_SHA
    df = pd.read_parquet(MANIFEST)
    assert len(df) == R1.EXPECTED_MANIFEST_ROWS


@requires_data
@pytest.mark.parametrize("crs", ["EPSG:3857", "EPSG:4326"])
def test_real_frame_center_reproj_under_one_pixel(crs):
    df = pd.read_parquet(MANIFEST)
    tfx = R1._Transformers()
    # sample a handful per CRS; the <1 src-px invariant must hold for all.
    sub = df[df.raster_crs == crs].head(25)
    errs = []
    for _, row in sub.iterrows():
        rec = R1.compute_geometry_record(row, tfx)
        assert rec.raster_crs == crs
        assert rec.roi_in_bounds
        errs.append(rec.center_reproj_err_px)
    assert max(errs) < 1.0


@requires_data
def test_real_frame_fov_sizes_per_arm():
    df = pd.read_parquet(MANIFEST)
    tfx = R1._Transformers()
    a24 = df[df.chip_arm == "A24"].iloc[0]
    a48 = df[df.chip_arm == "A48"].iloc[0]
    r24 = R1.compute_geometry_record(a24, tfx)
    r48 = R1.compute_geometry_record(a48, tfx)
    assert r24.review_extent_m == 24.0 and r24.crop_size_m == 24.0
    assert r48.review_extent_m == 48.0 and r48.crop_size_m == 48.0
    # ground FoV within a pixel-quantization tolerance of the nominal extent
    # (per-axis; TM frames are mildly anisotropic in ground metres by design).
    for r, nom in ((r24, 24.0), (r48, 48.0)):
        assert abs(r.fov_ground_w_m - nom) / nom < 0.15
        assert abs(r.fov_ground_h_m - nom) / nom < 0.15


@requires_data
@pytest.mark.parametrize("crs", ["EPSG:3857", "EPSG:4326"])
def test_real_crop_matches_teacher_markerfree(crs):
    """R1 crop bytes must equal the teacher render with draw_marker=False (same
    FoV, marker removed) -- the student sees exactly what Gemini saw, sans cross."""
    from PIL import Image

    from scripts.temporal.gehi_common import (
        ReviewTargetMarker,
        ensure_single_target_review_png,
    )
    from scripts.temporal.chip_geometry import (
        FULLSCAN_GEOMETRY_VERSION_BY_ARM,
        resolve_chip_geometry,
    )

    df = pd.read_parquet(MANIFEST)
    row = df[df.raster_crs == crs].iloc[0]
    arm = str(row.chip_arm)
    g = resolve_chip_geometry(FULLSCAN_GEOMETRY_VERSION_BY_ARM[arm])
    marker = ReviewTargetMarker(
        target_id="T01", target_label="T01",
        offset_x_m=0.0, offset_y_m=0.0, search_radius_m=10.0,
    )
    teacher = ensure_single_target_review_png(
        Path(row.src_tiff_path), marker, chip_size_m=96.0,
        crop_context_multiplier=g.crop_context_multiplier,
        min_crop_size_m=g.min_crop_size_m, min_output_px=g.min_output_px,
        draw_marker=False,
    )
    fov_m, min_out = R1.arm_render_params(arm)
    win = R1.compute_crop_window(
        int(row.raster_width_px), int(row.raster_height_px), fov_m, min_output_px=min_out
    )
    r1 = np.asarray(R1.render_marker_free_crop(row.src_tiff_path, win).convert("RGB"))
    tv = np.asarray(Image.open(teacher).convert("RGB"))
    assert r1.shape == tv.shape
    assert int(np.abs(r1.astype(int) - tv.astype(int)).max()) == 0
