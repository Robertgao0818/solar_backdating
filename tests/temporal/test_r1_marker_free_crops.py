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


def test_qa_only_never_touches_production_status(tmp_path):
    """Regression lock for the --qa-only footgun: a qa-only run must NEVER
    write/clobber the production status file (STATUS_FILENAME) -- it must land
    in the separate QA_STATUS_FILENAME instead. Simulates the exact incident:
    a pre-existing production status file (standing in for real render
    provenance) must survive byte-for-byte across a --qa-only invocation."""
    mpath, _ = _synthetic_manifest(tmp_path)
    out = tmp_path / "r1_crops_v1"
    out.mkdir(parents=True)

    prod_status_path = out / R1.STATUS_FILENAME[R1.CROP_GEOMETRY_VERSION]
    fake_prod_status = json.dumps({"rendered": 12345, "skipped": 6, "sentinel": "do-not-touch"})
    prod_status_path.write_text(fake_prod_status)

    qa_status_path = out / R1.QA_STATUS_FILENAME[R1.CROP_GEOMETRY_VERSION]
    assert not qa_status_path.exists()

    args = R1.parse_args(
        ["--manifest", str(mpath), "--out-dir", str(out), "--no-sha-check", "--qa-only"]
    )
    assert R1.run(args) == 0

    # production status is byte-identical to what it was before the qa-only run.
    assert prod_status_path.read_text() == fake_prod_status
    # the qa-only run's own status landed in the separate QA status file.
    assert qa_status_path.exists()
    qa_status = json.loads(qa_status_path.read_text())
    assert qa_status["rendered"] == 0 and qa_status["skipped"] == 0


def test_qa_only_v2_never_touches_production_status(tmp_path):
    """Same lock, v2 path: crop_geometry_index_v2 has its own production/QA
    status file pair, independent of v1's."""
    mpath, spath, _ = _synthetic_manifest_and_sidecar(tmp_path)
    out = tmp_path / "r1_crops_v1"
    out.mkdir(parents=True)

    prod_status_path = out / R1.STATUS_FILENAME[R1.CROP_GEOMETRY_VERSION_V2]
    fake_prod_status = json.dumps({"rendered": 999, "sentinel": "do-not-touch-v2"})
    prod_status_path.write_text(fake_prod_status)

    args = R1.parse_args(
        [
            "--manifest", str(mpath), "--out-dir", str(out),
            "--sidecar", str(spath), "--crop-geometry", "v2",
            "--no-sha-check", "--qa-only",
        ]
    )
    assert R1.run(args) == 0

    assert prod_status_path.read_text() == fake_prod_status
    qa_status_path = out / R1.QA_STATUS_FILENAME[R1.CROP_GEOMETRY_VERSION_V2]
    assert qa_status_path.exists()


# ======================================================================== #
# r1_cropgeo_v2: area-preserving rectangle ROI (sidecar aspect ratio).
# ======================================================================== #
def _synthetic_sidecar_row(
    *, width_m: float, height_m: float, area_m2: float
) -> pd.Series:
    """A sidecar-shaped row matching build_r0_footprint_sidecar.py's math
    exactly (areamatched_long/short = sqrt(A*r)/sqrt(A/r), r = long/short)."""
    long_m = max(width_m, height_m)
    short_m = min(width_m, height_m)
    r = long_m / short_m
    return pd.Series(
        {
            "anchor_id": "synthetic_t01",
            "chip_arm": "A24",
            "source_area_m2": area_m2,
            "source_width_m": width_m,
            "source_height_m": height_m,
            "aspect_ratio": r,
            "areamatched_long_m": math.sqrt(area_m2 * r),
            "areamatched_short_m": math.sqrt(area_m2 / r),
        }
    )


class TestV1RegressionInvariants:
    """Explicit lock-in: the default (v1) path of compute_geometry_record must
    be untouched by the v2 addition -- same call signature default, same ROI
    shape invariants as before the refactor."""

    def test_default_crop_geometry_is_v1(self):
        tfx = R1._Transformers()
        rec = R1.compute_geometry_record(_synthetic_row("EPSG:4326", area_m2=16.0), tfx)
        assert rec.crop_geometry == R1.CROP_GEOMETRY_VERSION
        assert rec.roi_source == "square_v1"
        assert rec.roi_long_axis == "square"
        # square invariant: x edge == y edge == legacy roi_edge_m
        assert rec.roi_edge_x_m == pytest.approx(rec.roi_edge_y_m)
        assert rec.roi_edge_x_m == pytest.approx(rec.roi_edge_m)
        assert rec.context_edge_x_m == pytest.approx(rec.context_edge_m)
        assert rec.roi_clamp_loss_frac == pytest.approx(0.0, abs=1e-9)

    def test_v1_explicit_crop_geometry_kwarg_matches_default(self):
        tfx = R1._Transformers()
        row = _synthetic_row("EPSG:3857", area_m2=42.0)
        default_rec = R1.compute_geometry_record(row, tfx)
        explicit_rec = R1.compute_geometry_record(
            row, tfx, crop_geometry=R1.CROP_GEOMETRY_VERSION, sidecar_row=None
        )
        assert asdict_eq(default_rec, explicit_rec)


def asdict_eq(a, b) -> bool:
    from dataclasses import asdict as _asdict

    return _asdict(a) == _asdict(b)


def test_resolve_roi_spec_v1_square():
    spec = R1.resolve_roi_spec(16.0, 24.0, crop_geometry=R1.CROP_GEOMETRY_VERSION)
    assert spec["edge_x_pre"] == spec["edge_y_pre"] == pytest.approx(4.0)
    assert spec["long_axis"] == "square"
    assert spec["roi_source"] == "square_v1"


def test_resolve_roi_spec_v2_areamatched_math():
    # width >> height -> long axis assigned to x; area preserved pre-clamp.
    sc = _synthetic_sidecar_row(width_m=10.0, height_m=2.5, area_m2=20.0)
    spec = R1.resolve_roi_spec(
        20.0, 48.0, crop_geometry=R1.CROP_GEOMETRY_VERSION_V2, sidecar_row=sc
    )
    assert spec["long_axis"] == "x"
    assert spec["edge_x_pre"] == pytest.approx(sc["areamatched_long_m"])
    assert spec["edge_y_pre"] == pytest.approx(sc["areamatched_short_m"])
    # area-preserving invariant: long_pre * short_pre == source area, exactly.
    assert spec["edge_x_pre"] * spec["edge_y_pre"] == pytest.approx(20.0, rel=1e-9)


def test_resolve_roi_spec_v2_long_axis_on_y():
    # height >> width -> long axis assigned to y.
    sc = _synthetic_sidecar_row(width_m=2.5, height_m=10.0, area_m2=20.0)
    spec = R1.resolve_roi_spec(
        20.0, 48.0, crop_geometry=R1.CROP_GEOMETRY_VERSION_V2, sidecar_row=sc
    )
    assert spec["long_axis"] == "y"
    assert spec["edge_y_pre"] > spec["edge_x_pre"]
    assert spec["edge_x_pre"] * spec["edge_y_pre"] == pytest.approx(20.0, rel=1e-9)


def test_resolve_roi_spec_v2_per_axis_clamp_and_loss():
    # Long axis pre-clamp (huge) exceeds fov; short axis stays well inside it.
    sc = _synthetic_sidecar_row(width_m=200.0, height_m=1.0, area_m2=200.0)
    spec = R1.resolve_roi_spec(
        200.0, 24.0, crop_geometry=R1.CROP_GEOMETRY_VERSION_V2, sidecar_row=sc
    )
    assert spec["edge_x_pre"] > 24.0  # would have clamped
    assert spec["edge_x"] == pytest.approx(24.0)
    assert spec["edge_y"] == pytest.approx(spec["edge_y_pre"])  # short axis untouched
    # actual rendered ROI area is now less than the pre-clamp (== source) area.
    rendered_area = spec["edge_x"] * spec["edge_y"]
    assert rendered_area < spec["edge_x_pre"] * spec["edge_y_pre"]


def test_resolve_roi_spec_v2_area_preserved_pre_clamp_various_aspects():
    for w, h, a in [(8.0, 6.0, 30.0), (3.0, 3.0, 9.0), (50.0, 1.0, 12.0)]:
        sc = _synthetic_sidecar_row(width_m=w, height_m=h, area_m2=a)
        spec = R1.resolve_roi_spec(
            a, 96.0, crop_geometry=R1.CROP_GEOMETRY_VERSION_V2, sidecar_row=sc
        )
        assert spec["edge_x_pre"] * spec["edge_y_pre"] == pytest.approx(a, rel=1e-9)


def test_resolve_roi_spec_v2_missing_sidecar_falls_back_to_square():
    spec = R1.resolve_roi_spec(
        16.0, 24.0, crop_geometry=R1.CROP_GEOMETRY_VERSION_V2, sidecar_row=None
    )
    assert spec["roi_source"] == "fallback_square_v2"
    assert spec["long_axis"] == "square"
    assert spec["edge_x_pre"] == spec["edge_y_pre"] == pytest.approx(4.0)


def test_resolve_roi_spec_v2_nan_areamatched_falls_back_to_square():
    sc = _synthetic_sidecar_row(width_m=10.0, height_m=2.0, area_m2=16.0)
    sc["areamatched_long_m"] = float("nan")
    spec = R1.resolve_roi_spec(
        16.0, 24.0, crop_geometry=R1.CROP_GEOMETRY_VERSION_V2, sidecar_row=sc
    )
    assert spec["roi_source"] == "fallback_square_v2"


def test_resolve_roi_spec_unknown_version_raises():
    with pytest.raises(ValueError):
        R1.resolve_roi_spec(16.0, 24.0, crop_geometry="bogus_version")


def test_compute_geometry_record_v2_end_to_end():
    tfx = R1._Transformers()
    row = _synthetic_row("EPSG:4326", area_m2=20.0)
    sc = _synthetic_sidecar_row(width_m=10.0, height_m=2.5, area_m2=20.0)
    rec = R1.compute_geometry_record(
        row, tfx, crop_geometry=R1.CROP_GEOMETRY_VERSION_V2, sidecar_row=sc
    )
    assert rec.crop_geometry == R1.CROP_GEOMETRY_VERSION_V2
    assert rec.roi_source == "sidecar_v2"
    assert rec.roi_long_axis == "x"
    assert rec.roi_edge_x_m > rec.roi_edge_y_m
    assert rec.roi_in_bounds
    roi = json.loads(rec.roi_px)
    assert len(roi) == 4
    # context ring generalizes edge*2.0 per axis, clamped to FoV.
    assert rec.context_edge_x_m == pytest.approx(min(24.0, rec.roi_edge_x_m * 2.0), abs=1e-3)
    assert rec.context_edge_y_m == pytest.approx(min(24.0, rec.roi_edge_y_m * 2.0), abs=1e-3)
    # png_relpath is IDENTICAL in shape to v1's (same chip_sha, same tag) --
    # the "v2 refs v1 pixels" contract lives in this shared filename scheme.
    assert rec.png_relpath.endswith(f".{R1.CROP_GEOMETRY_TAG}.png")


def test_concentric_rect_iou_identical_squares_is_one():
    assert R1.concentric_rect_iou(10.0, 10.0, 10.0, 10.0) == pytest.approx(1.0)


def test_concentric_rect_iou_known_value():
    # square 4x4 vs rectangle 8x2: both area 16; overlap = min(4,8)*min(4,2) = 4*2=8
    # union = 16+16-8=24; iou = 8/24 = 1/3.
    assert R1.concentric_rect_iou(4.0, 4.0, 8.0, 2.0) == pytest.approx(1.0 / 3.0)


def test_aspect_bin_label():
    assert R1.aspect_bin_label(1.2) == "[1.0,1.5)"
    assert R1.aspect_bin_label(1.8) == "[1.5,2.0)"
    assert R1.aspect_bin_label(3.0) == "[2.0,4.0)"
    assert R1.aspect_bin_label(5.0) == "[4.0,inf)"
    assert R1.aspect_bin_label(float("nan")) == "unknown"
    assert R1.aspect_bin_label(None) == "unknown"


# ------------------------------------------------------------------------ #
# End-to-end v2 pipeline run: version propagation, index dir, PNG reuse.
# ------------------------------------------------------------------------ #
def _synthetic_manifest_and_sidecar(tmp_path: Path) -> tuple[Path, Path, Path]:
    mpath, tiff = _synthetic_manifest(tmp_path)
    sc = _synthetic_sidecar_row(width_m=10.0, height_m=2.5, area_m2=20.0)
    sc_df = pd.DataFrame([sc])
    spath = tmp_path / "footprint_sidecar_v1.parquet"
    sc_df.to_parquet(spath, index=False)
    return mpath, spath, tiff


def test_v2_index_dir_and_version_propagate(tmp_path):
    mpath, spath, _ = _synthetic_manifest_and_sidecar(tmp_path)
    out = tmp_path / "r1_crops_v1"
    # v1 run first to render the shared PNG.
    v1_args = R1.parse_args(
        ["--manifest", str(mpath), "--out-dir", str(out), "--no-sha-check"]
    )
    assert R1.run(v1_args) == 0

    v2_args = R1.parse_args(
        [
            "--manifest", str(mpath), "--out-dir", str(out),
            "--sidecar", str(spath), "--crop-geometry", "v2", "--no-sha-check",
        ]
    )
    assert R1.run(v2_args) == 0

    idx_v2 = pd.concat(
        pd.read_parquet(p) for p in (out / "crop_geometry_index_v2").glob("*.parquet")
    )
    assert (idx_v2["crop_geometry"] == R1.CROP_GEOMETRY_VERSION_V2).all()
    assert idx_v2["roi_source"].iloc[0] == "sidecar_v2"
    # png_relpath still carries the SAME (v1) tag -- v2 does not mint new pixels.
    assert idx_v2["png_relpath"].str.contains(f".{R1.CROP_GEOMETRY_TAG}.png").all()
    # v1's own index directory/status file are untouched by the v2 run.
    idx_v1 = pd.concat(
        pd.read_parquet(p) for p in (out / "crop_geometry_index").glob("*.parquet")
    )
    assert (idx_v1["crop_geometry"] == R1.CROP_GEOMETRY_VERSION).all()
    assert (out / "_R1_CROPS_STATUS.json").exists()
    assert (out / "_R1_CROPS_STATUS_v2.json").exists()


def test_v2_does_not_rerender_reuses_v1_png(tmp_path):
    mpath, spath, _ = _synthetic_manifest_and_sidecar(tmp_path)
    out = tmp_path / "r1_crops_v1"
    v1_args = R1.parse_args(
        ["--manifest", str(mpath), "--out-dir", str(out), "--no-sha-check"]
    )
    assert R1.run(v1_args) == 0
    pngs = list(out.rglob(f"*.{R1.CROP_GEOMETRY_TAG}.png"))
    assert len(pngs) == 1
    mtime_before = pngs[0].stat().st_mtime_ns
    bytes_before = pngs[0].read_bytes()

    v2_args = R1.parse_args(
        [
            "--manifest", str(mpath), "--out-dir", str(out),
            "--sidecar", str(spath), "--crop-geometry", "v2", "--no-sha-check",
        ]
    )
    status = json.loads(
        (out / "_R1_CROPS_STATUS.json").read_text()
    )  # v1 status, for baseline comparison only
    assert R1.run(v2_args) == 0
    status2 = json.loads((out / "_R1_CROPS_STATUS_v2.json").read_text())
    assert status2["rendered"] == 0 and status2["skipped"] == 0  # v2 never renders
    # exactly one PNG on disk still -- no second copy was written for v2.
    pngs_after = list(out.rglob(f"*.{R1.CROP_GEOMETRY_TAG}.png"))
    assert len(pngs_after) == 1
    assert pngs_after[0].stat().st_mtime_ns == mtime_before
    assert pngs_after[0].read_bytes() == bytes_before


def test_v2_sidecar_sha_gate_rejects_wrong_data(tmp_path):
    _, spath, _ = _synthetic_manifest_and_sidecar(tmp_path)
    with pytest.raises(SystemExit):
        R1.load_sidecar(spath, check_sha=True)


def test_v2_missing_sidecar_anchor_falls_back_and_is_recorded(tmp_path):
    mpath, tiff = _synthetic_manifest(tmp_path)
    # sidecar with a DIFFERENT anchor_id -- the manifest's anchor won't match.
    sc = _synthetic_sidecar_row(width_m=10.0, height_m=2.5, area_m2=20.0)
    sc["anchor_id"] = "some_other_anchor"
    spath = tmp_path / "footprint_sidecar_v1.parquet"
    pd.DataFrame([sc]).to_parquet(spath, index=False)

    out = tmp_path / "r1_crops_v1"
    v2_args = R1.parse_args(
        [
            "--manifest", str(mpath), "--out-dir", str(out),
            "--sidecar", str(spath), "--crop-geometry", "v2", "--no-sha-check",
        ]
    )
    assert R1.run(v2_args) == 0
    status = json.loads((out / "_R1_CROPS_STATUS_v2.json").read_text())
    assert status["sidecar_missing_rows"] == 1
    assert status["roi_source_counts"] == {"fallback_square_v2": 1}


def test_qa_mode_aspect_arm_requires_v2(tmp_path):
    mpath, _ = _synthetic_manifest(tmp_path)
    out = tmp_path / "r1_crops_v1"
    args = R1.parse_args(
        [
            "--manifest", str(mpath), "--out-dir", str(out),
            "--qa-mode", "aspect_arm", "--no-sha-check",
        ]
    )
    with pytest.raises(SystemExit):
        R1.run(args)


@requires_data
def test_real_frame_v2_areas_match_source_pre_clamp():
    """Real-corpus spot check: v2's rectangle is area-preserving pre-clamp for
    genuine sidecar rows (not just synthetic ones)."""
    sidecar_path = (
        Path.home()
        / "zasolar_data/geid_temporal/run3_native_line_2026-07"
        / "r0_manifest_v1/footprint_sidecar_v1.parquet"
    )
    if not sidecar_path.exists():
        pytest.skip("footprint sidecar not present")
    df = pd.read_parquet(MANIFEST)
    sidecar = pd.read_parquet(sidecar_path)
    merged = df.merge(sidecar, on="anchor_id", how="inner", suffixes=("", "_sc")).head(25)
    tfx = R1._Transformers()
    for _, row in merged.iterrows():
        spec = R1.resolve_roi_spec(
            float(row["source_area_m2"]), 24.0,
            crop_geometry=R1.CROP_GEOMETRY_VERSION_V2, sidecar_row=row,
        )
        assert spec["edge_x_pre"] * spec["edge_y_pre"] == pytest.approx(
            float(row["source_area_m2"]), rel=1e-6
        )


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
