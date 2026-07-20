"""Unit tests for R3 DINOv2-S feature cache (build_r3_feature_cache.py).

Pure-geometry + pooling + storage tests are always-on (no torch / GPU / drive).
The backbone-hash and real-embedding checks are guarded (torch / drive).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "temporal"))

import build_r3_feature_cache as r3  # noqa: E402


# --------------------------------------------------------------------------- #
# patch_centres_px
# --------------------------------------------------------------------------- #
def test_patch_centres_px_grid4():
    cx, cy = r3.patch_centres_px(4, 256.0)
    assert cx.shape == (4, 4) and cy.shape == (4, 4)
    # centres at (col+0.5)/4*256 -> {32,96,160,224}
    np.testing.assert_allclose(cx[0], [32, 96, 160, 224])
    np.testing.assert_allclose(cy[:, 0], [32, 96, 160, 224])
    # cx varies along columns, cy along rows
    assert (cx[0] == cx[1]).all()
    assert (cy[:, 0] == cy[:, 1]).all()


def test_patch_centres_scale_with_out_px():
    cx, _ = r3.patch_centres_px(4, 326.0)
    np.testing.assert_allclose(cx[0, 0], 0.5 / 4 * 326.0)


# --------------------------------------------------------------------------- #
# point_in_convex_quad
# --------------------------------------------------------------------------- #
def _square(cx, cy, half):
    # CCW-in-y-up (== CW in y-down pixels); matches R1 _square_corners order.
    return np.array(
        [[cx - half, cy - half], [cx + half, cy - half], [cx + half, cy + half], [cx - half, cy + half]],
        dtype=np.float64,
    )


def test_point_in_quad_inside_outside():
    q = _square(128, 128, 64)  # covers [64,192]^2
    px = np.array([128.0, 96.0, 200.0, 64.0])
    py = np.array([128.0, 96.0, 128.0, 64.0])
    inside = r3.point_in_convex_quad(px, py, q)
    assert inside.tolist() == [True, True, False, True]  # corner counts as inside


def test_point_in_quad_winding_invariant():
    q = _square(128, 128, 64)
    q_rev = q[::-1].copy()  # opposite winding
    px = np.array([128.0, 200.0])
    py = np.array([128.0, 128.0])
    a = r3.point_in_convex_quad(px, py, q)
    b = r3.point_in_convex_quad(px, py, q_rev)
    assert a.tolist() == b.tolist() == [True, False]


# --------------------------------------------------------------------------- #
# build_pool_masks
# --------------------------------------------------------------------------- #
def test_build_pool_masks_roi_subset_of_context():
    roi = _square(128, 128, 64)  # centre 2x2 of a 4x4 grid
    ctx = _square(128, 128, 128)  # whole crop
    roi_mask, ring_mask = r3.build_pool_masks(roi, ctx, 256.0, 4)
    assert roi_mask.sum() == 4  # the inner 2x2 patches
    assert ring_mask.sum() == 12  # everything else
    # ROI and ring are disjoint
    assert not (roi_mask & ring_mask).any()
    # ROI is exactly the inner block
    assert roi_mask[1:3, 1:3].all() and roi_mask.sum() == roi_mask[1:3, 1:3].sum()


# --------------------------------------------------------------------------- #
# pool_grid (means + fallbacks)
# --------------------------------------------------------------------------- #
def _grid_from_values(vals):
    """vals: [G,G] scalar -> grid [G,G,C] with each patch a constant C-vector."""
    g = vals.shape[0]
    c = 3
    grid = np.zeros((g, g, c), dtype=np.float16)
    for r in range(g):
        for col in range(g):
            grid[r, col, :] = vals[r, col]
    return grid


def test_pool_grid_means_and_counts():
    vals = np.arange(16, dtype=np.float32).reshape(4, 4)
    grid = _grid_from_values(vals)
    roi = _square(128, 128, 64)  # inner 2x2 -> rows/cols {1,2}
    ctx = _square(128, 128, 128)  # all
    fr, fc, ff, roi_n, ring_n = r3.pool_grid(grid, roi, ctx, 256.0)
    assert roi_n == 4 and ring_n == 12
    # full mean = mean(0..15) = 7.5
    np.testing.assert_allclose(ff.astype(np.float32), 7.5, atol=0.01)
    # roi patches = vals[1:3,1:3] = [5,6,9,10] -> mean 7.5
    np.testing.assert_allclose(fr.astype(np.float32), 7.5, atol=0.01)
    # ring = all 16 minus those four -> mean = (120-30)/12 = 7.5 (symmetric here)
    np.testing.assert_allclose(fc.astype(np.float32), 7.5, atol=0.01)
    assert fr.dtype == np.float16 and fc.dtype == np.float16 and ff.dtype == np.float16


def test_pool_grid_roi_mean_distinct():
    vals = np.zeros((4, 4), dtype=np.float32)
    vals[1:3, 1:3] = 10.0  # only the ROI patches are hot
    grid = _grid_from_values(vals)
    roi = _square(128, 128, 64)
    ctx = _square(128, 128, 128)
    fr, fc, ff, roi_n, ring_n = r3.pool_grid(grid, roi, ctx, 256.0)
    np.testing.assert_allclose(fr.astype(np.float32), 10.0, atol=0.01)  # ROI = hot
    np.testing.assert_allclose(fc.astype(np.float32), 0.0, atol=0.01)  # ring = cold
    np.testing.assert_allclose(ff.astype(np.float32), 40.0 / 16, atol=0.01)


def test_pool_grid_empty_roi_nearest_patch_fallback():
    vals = np.arange(16, dtype=np.float32).reshape(4, 4)
    grid = _grid_from_values(vals)
    # Sub-patch ROI at (100,100), half=1 -> covers [99,101], no patch centre
    # (32/96/160/224) inside; nearest centre is (96,96) == patch (row1,col1).
    roi = _square(100, 100, 1)
    ctx = _square(128, 128, 128)
    fr, fc, ff, roi_n, ring_n = r3.pool_grid(grid, roi, ctx, 256.0)
    assert roi_n == 0  # fallback path
    np.testing.assert_allclose(fr.astype(np.float32), vals[1, 1], atol=0.01)


def test_pool_grid_empty_ring_full_fallback():
    vals = np.arange(16, dtype=np.float32).reshape(4, 4)
    grid = _grid_from_values(vals)
    roi = _square(128, 128, 128)  # ROI fills the crop
    ctx = _square(128, 128, 128)  # same -> empty ring
    fr, fc, ff, roi_n, ring_n = r3.pool_grid(grid, roi, ctx, 256.0)
    assert ring_n == 0
    np.testing.assert_array_equal(fc, ff)  # context falls back to full


# --------------------------------------------------------------------------- #
# _parse_corners
# --------------------------------------------------------------------------- #
def test_parse_corners_json_and_list():
    js = json.dumps([[1.0, 2.0], [3.0, 4.0], [5.0, 6.0], [7.0, 8.0]])
    a = r3._parse_corners(js)
    b = r3._parse_corners([[1, 2], [3, 4], [5, 6], [7, 8]])
    assert a.shape == (4, 2)
    np.testing.assert_array_equal(a, b)


def test_parse_corners_bad_shape():
    with pytest.raises(ValueError):
        r3._parse_corners([[1, 2], [3, 4]])


# --------------------------------------------------------------------------- #
# storage round-trip (fp16 bit-identical) + schema/keys
# --------------------------------------------------------------------------- #
def _fake_row(sha):
    rng = np.random.default_rng(int(sha[:6], 16))
    return {
        "chip_sha": sha,
        "crop_geometry": r3.EXPECTED_CROP_GEOMETRY,
        "backbone_hash": "fake@sha256:deadbeef",
        "pooling_version": r3.POOLING_VERSION,
        "feat_roi": rng.standard_normal(384).astype(np.float16),
        "feat_context": rng.standard_normal(384).astype(np.float16),
        "feat_full": rng.standard_normal(384).astype(np.float16),
        "roi_n_patches": 5,
        "ring_n_patches": 12,
        "embed_dim": 384,
        "grid_side": 37,
    }


def test_shard_parquet_roundtrip_bit_identical(tmp_path):
    import pandas as pd

    rows = [_fake_row(f"{i:064x}") for i in range(3)]
    out = tmp_path / "ab.parquet"
    r3._write_shard_parquet(rows, out)
    assert out.exists()
    df = pd.read_parquet(out)
    assert list(df.columns) == [
        "chip_sha", "crop_geometry", "backbone_hash", "pooling_version",
        "feat_roi", "feat_context", "feat_full",
        "roi_n_patches", "ring_n_patches", "embed_dim", "grid_side",
    ]
    for i, r in enumerate(rows):
        for col in ("feat_roi", "feat_context", "feat_full"):
            back = np.asarray(df.iloc[i][col], dtype=np.float16)
            assert np.array_equal(back.view(np.uint16), r[col].view(np.uint16))
    # key uniqueness within the shard
    keys = list(zip(df.chip_sha, df.crop_geometry, df.backbone_hash, df.pooling_version))
    assert len(set(keys)) == len(keys)


def test_write_shard_atomic_no_tmp_left(tmp_path):
    rows = [_fake_row(f"{i:064x}") for i in range(2)]
    out = tmp_path / "cd.parquet"
    r3._write_shard_parquet(rows, out)
    assert not (tmp_path / "cd.parquet.tmp").exists()


def test_done_marker_atomic_and_completed_shard_validation(tmp_path):
    import pandas as pd

    rows = [_fake_row(f"{i:064x}") for i in range(2)]
    out = tmp_path / "ef.parquet"
    r3._write_shard_parquet(rows, out)
    marker = r3._write_done_marker_atomic(out, len(rows))
    expected = set(pd.DataFrame(rows)["chip_sha"])

    assert marker.exists()
    assert not Path(f"{marker}.tmp").exists()
    assert r3._completed_shard_is_valid(
        out, marker, expected, rows[0]["backbone_hash"]
    )


def test_completed_shard_rejects_bad_marker_or_cache_key(tmp_path):
    rows = [_fake_row(f"{i:064x}") for i in range(2)]
    out = tmp_path / "f0.parquet"
    r3._write_shard_parquet(rows, out)
    marker = r3._write_done_marker_atomic(out, len(rows))
    expected = {r["chip_sha"] for r in rows}

    assert not r3._completed_shard_is_valid(out, marker, expected, "other-backbone")
    marker.write_text(json.dumps({"n_rows": 2, "sha256": "0" * 64}) + "\n")
    assert not r3._completed_shard_is_valid(
        out, marker, expected, rows[0]["backbone_hash"]
    )


def test_existing_cache_identity_refuses_cross_key_overwrite(tmp_path):
    rows = [_fake_row(f"{i:064x}") for i in range(2)]
    out = tmp_path / "f1.parquet"
    r3._write_shard_parquet(rows, out)

    r3._assert_existing_cache_identity(tmp_path, rows[0]["backbone_hash"])
    with pytest.raises(SystemExit, match="distinct --out-dir"):
        r3._assert_existing_cache_identity(tmp_path, "other-backbone")


# --------------------------------------------------------------------------- #
# backbone hash (torch-guarded)
# --------------------------------------------------------------------------- #
def test_compute_backbone_hash_deterministic_and_content_sensitive():
    torch = pytest.importorskip("torch")

    class _Stub:
        backbone_model_id = "vit_small_patch14_dinov2.lvd142m"

        def __init__(self, seed):
            g = torch.Generator().manual_seed(seed)

            class _Enc:
                def state_dict(self_inner):
                    return {"w": torch.randn(4, 4, generator=g), "b": torch.zeros(4)}

            self._encoder = _Enc()

    h1 = r3.compute_backbone_hash(_Stub(1))
    h1b = r3.compute_backbone_hash(_Stub(1))
    h2 = r3.compute_backbone_hash(_Stub(2))
    assert h1 == h1b  # deterministic for identical weights
    assert h1 != h2  # content-sensitive
    assert h1.startswith("vit_small_patch14_dinov2.lvd142m@sha256:")
    assert len(h1.split(":")[-1]) == 16


# --------------------------------------------------------------------------- #
# real-frame integration (drive + torch + GPU) -- skipped without inputs
# --------------------------------------------------------------------------- #
_CROP_ROOT = (
    Path.home()
    / "zasolar_data/geid_temporal/run3_native_line_2026-07/r1_crops_v1"
)


@pytest.mark.skipif(
    not (_CROP_ROOT / "crop_geometry_index").exists(),
    reason="R1 crops not on this machine",
)
def test_real_embed_determinism_small():
    """Re-embedding the same crops twice yields bit-identical fp16 features."""
    import glob

    import pandas as pd

    pytest.importorskip("torch")
    from dinov3_scorer import Dinov2PresenceScorer  # noqa

    gi = pd.read_parquet(
        sorted(glob.glob(str(_CROP_ROOT / "crop_geometry_index" / "*.parquet")))[0]
    ).drop_duplicates("chip_sha").head(8)
    scorer = Dinov2PresenceScorer(device="cuda" if _cuda() else "cpu")
    scorer._ensure_model()
    bh = r3.compute_backbone_hash(scorer)
    r1 = r3.embed_shard(scorer, gi, _CROP_ROOT, bh, batch_size=8)
    r2 = r3.embed_shard(scorer, gi, _CROP_ROOT, bh, batch_size=8)
    for a, b in zip(r1, r2):
        for col in ("feat_roi", "feat_context", "feat_full"):
            assert np.array_equal(a[col].view(np.uint16), b[col].view(np.uint16))


def _cuda() -> bool:
    try:
        import torch

        return torch.cuda.is_available()
    except Exception:
        return False
