#!/usr/bin/env python3
"""R3 DINOv2-S feature cache from the frozen Run3-native manifest (PRD §6 R3).

Parent: ``docs/dinov3_scorer/PRD-run3-native-local-line-2026-07-19.md`` §6 R3 --
"只嵌入 311k 已评分帧. 特征缓存以 ``chip_sha + crop_geometry + backbone_hash +
pooling_version`` 为键写分片 Parquet, fp16 ROI feature, 原子完成标记 + 断点续跑".
This step embeds the R1 marker-free crops with a **frozen** DINOv2 ViT-S/14
backbone and caches pooled ROI / context-ring / full-crop features. It does NOT
train, calibrate, or evaluate anything (that is R4/R5 -- out of scope, PRD §0).

Authoritative inputs (never bypassed by a disk scan):
* Frozen R0 manifest -- the frame list of record
  ``run3_native_line_2026-07/r0_manifest_v1/manifest.parquet`` (311,195 rows,
  sha256 ``ffd0c174...f8cf``); the unique ``src_tiff_sha256`` set (311,126) is the
  set of crops to embed.
* R1 crop geometry index ``r1_crops_v1/crop_geometry_index/<sha2>.parquet``
  (per-crop ``roi_px`` / ``context_px`` in output-crop pixels, ``out_px``,
  ``png_relpath``) -- the pooling reads ROI / ring geometry from here, never
  recomputing it. R1's ``(chip_sha, crop_geometry)`` half of the key is frozen.

===========================================================================
Feature-cache key (R3_FEATURE_CACHE_CONTRACT, build_r1_marker_free_crops.py)
===========================================================================
``(chip_sha, crop_geometry, backbone_hash, pooling_version)``:

* ``chip_sha``        source raster SHA-256 (manifest ``src_tiff_sha256``).
* ``crop_geometry``   ``r1_cropgeo_v1@2026-07-19`` (this round; from the geometry
                      index -- the ROI + context ring the pooling reads).
* ``backbone_hash``   ``<timm_id>@sha256:<16hex>`` where the 16 hex are derived
                      from the ACTUAL loaded encoder weights (sha256 over the
                      frozen state_dict, sorted by key) -- not a hand-written
                      constant, so a silent weight swap changes the key.
* ``pooling_version`` ``r3_pool_v1`` (defined below) -- the named ROI/ring/full
                      pooling scheme over the patch-token grid.

Layout under ``r3_feature_cache_v1/`` (data only; repo gets code/tests/docs):

    features/<sha2>.parquet            one shard per chip_sha[:2] (256 shards)
    features/<sha2>.parquet.done       atomic per-shard completion marker
    _R3_FEATURE_CACHE_LOCK.json        provenance + per-shard sha + counts

Resume: a shard is skipped only when its final ``.parquet`` + ``.done`` marker
both exist and the marker row count / sha256 plus all four key columns validate
against the requested shard. Both files are written through ``.tmp`` -> atomic
``rename``. A killed or corrupted shard is therefore recomputed rather than
silently accepted. Re-running is idempotent.

===========================================================================
pooling_version ``r3_pool_v1``
===========================================================================
The frozen backbone emits a ``G x G`` patch-token grid (DINOv2 ViT-S/14 at input
518, patch 14 -> ``G=37``, ``embed_dim=384``). Three pooled features per crop,
each a mean over a subset of patch tokens (fp32 accumulate, stored **fp16**):

* ``feat_full``    global mean over all ``G*G`` patch tokens (whole-crop context).
* ``feat_roi``     masked mean over patch tokens whose CENTRE falls inside the R1
                   nominal ROI quad (``roi_px``, normalised by that crop's
                   ``out_px``). The ROI is an area-equivalent square in ground
                   metres reprojected through the frame CRS, so it is a convex
                   quad in output-crop pixels; a patch is "in ROI" iff its centre
                   passes a consistent-sign point-in-convex-quad test.
                   *Fallback* when no patch centre lands inside (ROI < one patch,
                   e.g. a sub-patch <15 m^2 target): the single patch nearest the
                   ROI centroid. Recorded as ``roi_n_patches = 0``.
* ``feat_context`` masked mean over the context RING = patches inside the
                   ``context_px`` quad but NOT inside the ROI quad (roof/context
                   band). *Fallback* when the ring is empty (ROI already fills the
                   FoV so ``context_edge_m == roi_edge_m``): ``feat_full``.
                   Recorded as ``ring_n_patches = 0``.

Stored per row: the three fp16 feature lists + ``roi_n_patches`` / ``ring_n_patches``
(0 signals the documented fallback, so every fallback is auditable) + ``embed_dim``
/ ``grid_side`` + the four key columns. Concatenation order for a single 3*384
vector, if a consumer wants one, is fixed as ``[roi, context, full]``.

Seam to cropgeo_v2 (parallel teammate): this executable is deliberately hard-
pinned to the v1 geometry index for the baseline round. A later v2 pooling pass
must add explicit geometry-index/version selection and write a distinct versioned
output root; consumers can then union the roots by the four-column key. Never
reuse one physical ``features/<sha2>.parquet`` directory for two key variants.
The same separate-root rule applies to a DINOv3-L-SAT challenger or a future
``r3_pool_v2`` pass.

Run from the shared venv (``source scripts/activate_env.sh``); CUDA used if
available (RTX 4070 8GB is sufficient -- ~2.3 GB peak at batch 64).
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

# --------------------------------------------------------------------------- #
# Frozen version identifiers (flow into every output row + the lock).
# --------------------------------------------------------------------------- #
POOLING_VERSION = "r3_pool_v1"
EXPECTED_CROP_GEOMETRY = "r1_cropgeo_v1@2026-07-19"

DEFAULT_MANIFEST = (
    Path.home()
    / "zasolar_data/geid_temporal/run3_native_line_2026-07"
    / "r0_manifest_v1/manifest.parquet"
)
EXPECTED_MANIFEST_SHA = (
    "ffd0c174d6dc62098ce96ff3a1883b4d3a0006efd7ced99d6543c7a2f053f8cf"
)
EXPECTED_MANIFEST_ROWS = 311195
EXPECTED_UNIQUE_CROPS = 311126
DEFAULT_CROP_ROOT = (
    Path.home()
    / "zasolar_data/geid_temporal/run3_native_line_2026-07/r1_crops_v1"
)
DEFAULT_OUT_DIR = (
    Path.home()
    / "zasolar_data/geid_temporal/run3_native_line_2026-07/r3_feature_cache_v1"
)

# The DINOv2-S baseline is the plan-of-record backbone (PRD §6 R3: "旧结果 floor
# 比 DINOv3-L-SAT 高 1.5pp 且算力小一个量级; LSAT 降级为 challenger"). Selectable
# via --backbone so the challenger can reuse this exact pipeline under its own key.
BACKBONE_CHOICES = {
    "dinov2_floor": "Dinov2PresenceScorer",
    "dinov3_frozen": "Dinov3PresenceScorer",
}


# --------------------------------------------------------------------------- #
# Pure pooling geometry (imported by the unit tests -- no torch here).
# --------------------------------------------------------------------------- #
def patch_centres_px(grid_side: int, out_px: float) -> tuple[np.ndarray, np.ndarray]:
    """Patch-token centre coordinates in output-crop pixels.

    Token ``(row, col)`` of a ``grid_side x grid_side`` grid covers a
    ``1/grid_side`` fraction of the crop; its centre is at normalised
    ``((col+0.5)/G, (row+0.5)/G)`` scaled by the crop's ``out_px``. Returns
    ``(cx, cy)``, each ``[G, G]`` indexed ``[row, col]`` to match the grid array.
    """
    idx = (np.arange(grid_side, dtype=np.float64) + 0.5) / grid_side * float(out_px)
    cx = np.broadcast_to(idx[None, :], (grid_side, grid_side))  # varies with col
    cy = np.broadcast_to(idx[:, None], (grid_side, grid_side))  # varies with row
    return cx, cy


def point_in_convex_quad(
    px: np.ndarray, py: np.ndarray, corners: np.ndarray, eps: float = 1e-9
) -> np.ndarray:
    """Vectorised point-in-convex-quadrilateral test.

    ``corners`` is ``[4, 2]`` (x, y) in consistent winding (the R1 ROI/context
    squares reproject to a convex quad; winding may be CW or CCW after the y-down
    pixel flip, so we accept a consistent sign either way). A point is inside iff
    the cross products of every edge with the point are all >= -eps or all <= eps.
    """
    inside_pos = np.ones(px.shape, dtype=bool)
    inside_neg = np.ones(px.shape, dtype=bool)
    for i in range(4):
        x0, y0 = corners[i]
        x1, y1 = corners[(i + 1) % 4]
        cross = (x1 - x0) * (py - y0) - (y1 - y0) * (px - x0)
        inside_pos &= cross >= -eps
        inside_neg &= cross <= eps
    return inside_pos | inside_neg


def build_pool_masks(
    roi_corners: np.ndarray,
    context_corners: np.ndarray,
    out_px: float,
    grid_side: int,
) -> tuple[np.ndarray, np.ndarray]:
    """ROI + context-ring boolean patch masks (``[G, G]`` each, indexed [row, col]).

    ``roi_mask`` = patch centres inside the ROI quad; ``ring_mask`` = inside the
    context quad AND NOT inside the ROI quad. Pure function of the R1 geometry --
    no recomputation of the crop window.
    """
    cx, cy = patch_centres_px(grid_side, out_px)
    roi_mask = point_in_convex_quad(cx, cy, roi_corners)
    ctx_mask = point_in_convex_quad(cx, cy, context_corners)
    ring_mask = ctx_mask & ~roi_mask
    return roi_mask, ring_mask


def pool_grid(
    grid: np.ndarray,
    roi_corners: np.ndarray,
    context_corners: np.ndarray,
    out_px: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, int, int]:
    """Pool one patch-token grid into (roi, context, full) fp16 features.

    ``grid`` is ``[G, G, C]`` fp16 (row-major, ``[row, col, channel]``). Means are
    accumulated in fp32 then cast to fp16 for storage. Returns
    ``(feat_roi, feat_context, feat_full, roi_n_patches, ring_n_patches)`` where
    the two counts are the number of patch tokens that fell inside each region
    (0 => the documented fallback was used).
    """
    g = grid.shape[0]
    g32 = grid.astype(np.float32)
    flat = g32.reshape(g * g, -1)
    feat_full = flat.mean(axis=0)

    roi_mask, ring_mask = build_pool_masks(roi_corners, context_corners, out_px, g)
    roi_flat = roi_mask.reshape(-1)
    ring_flat = ring_mask.reshape(-1)
    roi_n = int(roi_flat.sum())
    ring_n = int(ring_flat.sum())

    if roi_n > 0:
        feat_roi = flat[roi_flat].mean(axis=0)
    else:
        # Sub-patch ROI: nearest patch to the ROI centroid (deterministic).
        centroid = roi_corners.mean(axis=0)
        cx, cy = patch_centres_px(g, out_px)
        d2 = (cx - centroid[0]) ** 2 + (cy - centroid[1]) ** 2
        r, c = np.unravel_index(int(np.argmin(d2)), (g, g))
        feat_roi = g32[r, c]

    if ring_n > 0:
        feat_context = flat[ring_flat].mean(axis=0)
    else:
        # Empty ring (ROI fills the FoV): fall back to the whole-crop feature.
        feat_context = feat_full

    return (
        feat_roi.astype(np.float16),
        feat_context.astype(np.float16),
        feat_full.astype(np.float16),
        roi_n,
        ring_n,
    )


# --------------------------------------------------------------------------- #
# Backbone identity (content-derived hash of the actual frozen weights).
# --------------------------------------------------------------------------- #
def compute_backbone_hash(scorer: Any) -> str:
    """``<timm_id>@sha256:<16hex>`` from the ACTUAL loaded encoder weights.

    Hashes the frozen state_dict (keys sorted, key bytes + tensor bytes) so the
    key tracks the real weights, not a hand-written string: a silent backbone /
    checkpoint swap changes the hash and therefore the cache key.
    """
    sd = scorer._encoder.state_dict()
    h = hashlib.sha256()
    for k in sorted(sd):
        h.update(k.encode("utf-8"))
        t = sd[k].detach().cpu().contiguous().numpy()
        h.update(np.ascontiguousarray(t).tobytes())
    return f"{scorer.backbone_model_id}@sha256:{h.hexdigest()[:16]}"


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# --------------------------------------------------------------------------- #
# Input loading (manifest + geometry index; never a raw disk scan for the list).
# --------------------------------------------------------------------------- #
def verify_manifest(manifest: Path, check_sha: bool = True) -> None:
    if check_sha:
        actual = _sha256_file(manifest)
        if actual != EXPECTED_MANIFEST_SHA:
            raise SystemExit(
                f"[R3] manifest sha256 mismatch: {actual} != {EXPECTED_MANIFEST_SHA} "
                "-- STOP (do not fabricate the frame list)"
            )


def load_geometry_index(crop_root: Path, manifest: Path, check_sha: bool) -> pd.DataFrame:
    """Load the R1 geometry index, deduped to one row per chip_sha, and cross-check
    the crop set against the frozen manifest's unique ``src_tiff_sha256``.

    Geometry is verified identical per chip_sha upstream (a sha belongs to one
    anchor/arm), so ``drop_duplicates`` keeping first is lossless.
    """
    verify_manifest(manifest, check_sha=check_sha)
    man = pd.read_parquet(manifest, columns=["src_tiff_sha256"])
    if len(man) != EXPECTED_MANIFEST_ROWS:
        raise SystemExit(
            f"[R3] manifest rows {len(man)} != {EXPECTED_MANIFEST_ROWS} -- STOP"
        )
    man_shas = set(man["src_tiff_sha256"].astype(str))

    shards = sorted(glob.glob(str(crop_root / "crop_geometry_index" / "*.parquet")))
    if not shards:
        raise SystemExit(f"[R3] no geometry index under {crop_root} -- run R1 first")
    gi = pd.concat([pd.read_parquet(s) for s in shards], ignore_index=True)
    gi["chip_sha"] = gi["chip_sha"].astype(str)
    gi = gi.drop_duplicates(subset="chip_sha", keep="first").reset_index(drop=True)

    geom_shas = set(gi["chip_sha"])
    if geom_shas != man_shas:
        missing = man_shas - geom_shas
        extra = geom_shas - man_shas
        raise SystemExit(
            f"[R3] crop set != manifest set: {len(missing)} manifest shas missing "
            f"from geometry, {len(extra)} extra -- STOP"
        )
    bad_cg = set(gi["crop_geometry"].unique()) - {EXPECTED_CROP_GEOMETRY}
    if bad_cg:
        raise SystemExit(f"[R3] unexpected crop_geometry {bad_cg} -- STOP")
    if len(gi) != EXPECTED_UNIQUE_CROPS:
        raise SystemExit(
            f"[R3] unique crops {len(gi)} != {EXPECTED_UNIQUE_CROPS} -- STOP"
        )
    return gi


def _parse_corners(cell: Any) -> np.ndarray:
    """Parse a stored ``roi_px`` / ``context_px`` cell -> ``[4,2]`` float array.

    Stored as a JSON string of ``[[x,y],...]`` (R1 dataclass) or already a list.
    """
    if isinstance(cell, str):
        pts = json.loads(cell)
    else:
        pts = [list(p) for p in cell]
    arr = np.asarray(pts, dtype=np.float64)
    if arr.shape != (4, 2):
        raise ValueError(f"corner array shape {arr.shape} != (4,2)")
    return arr


# --------------------------------------------------------------------------- #
# Per-shard embedding + pooling.
# --------------------------------------------------------------------------- #
def _write_shard_parquet(rows: list[dict[str, Any]], out_path: Path) -> None:
    """Atomically write one shard as list<float16> parquet (.tmp -> rename)."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    def _fl(col: str) -> pa.Array:
        return pa.array([r[col].tolist() for r in rows], type=pa.list_(pa.float16()))

    table = pa.table(
        {
            "chip_sha": pa.array([r["chip_sha"] for r in rows], type=pa.string()),
            "crop_geometry": pa.array([r["crop_geometry"] for r in rows], type=pa.string()),
            "backbone_hash": pa.array([r["backbone_hash"] for r in rows], type=pa.string()),
            "pooling_version": pa.array([r["pooling_version"] for r in rows], type=pa.string()),
            "feat_roi": _fl("feat_roi"),
            "feat_context": _fl("feat_context"),
            "feat_full": _fl("feat_full"),
            "roi_n_patches": pa.array([r["roi_n_patches"] for r in rows], type=pa.int32()),
            "ring_n_patches": pa.array([r["ring_n_patches"] for r in rows], type=pa.int32()),
            "embed_dim": pa.array([r["embed_dim"] for r in rows], type=pa.int32()),
            "grid_side": pa.array([r["grid_side"] for r in rows], type=pa.int32()),
        }
    )
    tmp = out_path.with_suffix(".parquet.tmp")
    pq.write_table(table, tmp)
    os.replace(tmp, out_path)


def _write_done_marker_atomic(out_path: Path, n_rows: int) -> Path:
    """Atomically publish a completion marker for an already-renamed shard."""
    marker = Path(f"{out_path}.done")
    tmp = Path(f"{marker}.tmp")
    tmp.write_text(
        json.dumps({"n_rows": int(n_rows), "sha256": _sha256_file(out_path)}) + "\n"
    )
    os.replace(tmp, marker)
    return marker


def _completed_shard_is_valid(
    out_path: Path,
    done_marker: Path,
    expected_chip_shas: set[str],
    backbone_hash: str,
) -> bool:
    """Return true only for an intact shard with the exact requested cache key.

    Existing v1 markers contain only row count + sha256, so the key identity is
    verified from the Parquet columns. This also prevents a challenger run from
    treating DINOv2 shards in a reused output directory as completed.
    """
    if not out_path.exists() or not done_marker.exists():
        return False
    try:
        marker = json.loads(done_marker.read_text())
        if int(marker["n_rows"]) != len(expected_chip_shas):
            return False
        if str(marker["sha256"]) != _sha256_file(out_path):
            return False
        keys = pd.read_parquet(
            out_path,
            columns=["chip_sha", "crop_geometry", "backbone_hash", "pooling_version"],
        )
    except (KeyError, OSError, ValueError, json.JSONDecodeError):
        return False

    if len(keys) != len(expected_chip_shas):
        return False
    if set(keys["chip_sha"].astype(str)) != expected_chip_shas:
        return False
    return (
        set(keys["crop_geometry"].astype(str)) == {EXPECTED_CROP_GEOMETRY}
        and set(keys["backbone_hash"].astype(str)) == {backbone_hash}
        and set(keys["pooling_version"].astype(str)) == {POOLING_VERSION}
    )


def _assert_existing_cache_identity(feat_dir: Path, backbone_hash: str) -> None:
    """Refuse to overwrite a physical shard directory with a different key axis."""
    for path in sorted(feat_dir.glob("*.parquet")):
        try:
            keys = pd.read_parquet(
                path, columns=["crop_geometry", "backbone_hash", "pooling_version"]
            )
        except (KeyError, OSError, ValueError) as exc:
            raise SystemExit(f"[R3] unreadable existing shard {path}: {exc}") from exc
        identity = (
            set(keys["crop_geometry"].astype(str)),
            set(keys["backbone_hash"].astype(str)),
            set(keys["pooling_version"].astype(str)),
        )
        expected = (
            {EXPECTED_CROP_GEOMETRY},
            {backbone_hash},
            {POOLING_VERSION},
        )
        if identity != expected:
            raise SystemExit(
                f"[R3] existing cache identity mismatch in {path}; use a distinct "
                "--out-dir for each crop_geometry/backbone_hash/pooling_version"
            )


def embed_shard(
    scorer: Any,
    gi_shard: pd.DataFrame,
    crop_root: Path,
    backbone_hash: str,
    batch_size: int,
) -> list[dict[str, Any]]:
    """Embed + pool every crop in one chip_sha[:2] shard, in a stable order."""
    gi_shard = gi_shard.sort_values("chip_sha").reset_index(drop=True)
    paths = [str(crop_root / rel) for rel in gi_shard["png_relpath"]]
    rows: list[dict[str, Any]] = []
    for start in range(0, len(paths), batch_size):
        batch_paths = paths[start : start + batch_size]
        grids = scorer.embed_patch_grids(batch_paths, batch_size=batch_size)  # [B,G,G,C]
        for j, grid in enumerate(grids):
            r = gi_shard.iloc[start + j]
            roi_c = _parse_corners(r["roi_px"])
            ctx_c = _parse_corners(r["context_px"])
            feat_roi, feat_ctx, feat_full, roi_n, ring_n = pool_grid(
                grid, roi_c, ctx_c, float(r["out_px"])
            )
            rows.append(
                {
                    "chip_sha": str(r["chip_sha"]),
                    "crop_geometry": str(r["crop_geometry"]),
                    "backbone_hash": backbone_hash,
                    "pooling_version": POOLING_VERSION,
                    "feat_roi": feat_roi,
                    "feat_context": feat_ctx,
                    "feat_full": feat_full,
                    "roi_n_patches": roi_n,
                    "ring_n_patches": ring_n,
                    "embed_dim": int(grid.shape[-1]),
                    "grid_side": int(grid.shape[0]),
                }
            )
    return rows


def run(args: argparse.Namespace) -> int:
    crop_root = Path(args.crop_root)
    out_dir = Path(args.out_dir)
    feat_dir = out_dir / "features"
    feat_dir.mkdir(parents=True, exist_ok=True)

    gi = load_geometry_index(crop_root, Path(args.manifest), check_sha=not args.no_sha_check)
    gi["shard"] = gi["chip_sha"].str[:2]
    all_shards = sorted(gi["shard"].unique())
    if args.shards:
        wanted = set(args.shards.split(","))
        all_shards = [s for s in all_shards if s in wanted]
    if args.limit:
        gi = gi.groupby("shard", group_keys=False).head(max(1, args.limit // max(1, len(all_shards))))

    from dinov3_scorer import Dinov2PresenceScorer, Dinov3PresenceScorer

    cls = {"dinov2_floor": Dinov2PresenceScorer, "dinov3_frozen": Dinov3PresenceScorer}[args.backbone]
    scorer = cls(device=args.device)
    scorer._ensure_model()
    backbone_hash = compute_backbone_hash(scorer)
    print(f"[R3] backbone={args.backbone} hash={backbone_hash} pooling={POOLING_VERSION}", flush=True)
    _assert_existing_cache_identity(feat_dir, backbone_hash)

    t0 = time.time()
    done = 0
    total = 0
    for i, shard in enumerate(all_shards):
        out_path = feat_dir / f"{shard}.parquet"
        done_marker = feat_dir / f"{shard}.parquet.done"
        gi_shard = gi[gi["shard"] == shard]
        expected_chip_shas = set(gi_shard["chip_sha"].astype(str))
        if _completed_shard_is_valid(
            out_path, done_marker, expected_chip_shas, backbone_hash
        ):
            done += 1
            total += len(gi_shard)
            continue
        if out_path.exists() or done_marker.exists():
            print(f"[R3] shard {shard} incomplete/invalid; recomputing", flush=True)
        rows = embed_shard(scorer, gi_shard, crop_root, backbone_hash, args.batch_size)
        _write_shard_parquet(rows, out_path)
        _write_done_marker_atomic(out_path, len(rows))
        done += 1
        total += len(rows)
        if (i + 1) % 8 == 0 or i + 1 == len(all_shards):
            rate = total / max(1e-6, time.time() - t0)
            print(
                f"[R3] shard {i+1}/{len(all_shards)} ({shard}) rows_so_far={total} "
                f"~{rate:.0f} crops/s",
                flush=True,
            )

    write_lock(out_dir, feat_dir, gi, backbone_hash, args)
    print(f"[R3] done: {done} shards, {total} rows in {time.time()-t0:.0f}s", flush=True)
    return 0


def write_lock(
    out_dir: Path, feat_dir: Path, gi: pd.DataFrame, backbone_hash: str, args: argparse.Namespace
) -> None:
    """Write the cache lock: per-shard sha + row counts + generation params."""
    shard_paths = sorted(glob.glob(str(feat_dir / "*.parquet")))
    per_shard = []
    total_rows = 0
    for p in shard_paths:
        pp = Path(p)
        n = int(pd.read_parquet(pp, columns=["chip_sha"]).shape[0])
        total_rows += n
        per_shard.append({"shard": pp.stem, "n_rows": n, "sha256": _sha256_file(pp)})
    lock = {
        "pooling_version": POOLING_VERSION,
        "crop_geometry": EXPECTED_CROP_GEOMETRY,
        "backbone": args.backbone,
        "backbone_hash": backbone_hash,
        "manifest": str(args.manifest),
        "manifest_sha256_expected": EXPECTED_MANIFEST_SHA,
        "expected_unique_crops": EXPECTED_UNIQUE_CROPS,
        "n_shards": len(per_shard),
        "n_rows": total_rows,
        "batch_size": args.batch_size,
        "device": args.device,
        "feature_columns": ["feat_roi", "feat_context", "feat_full"],
        "embed_dim": 384,
        "grid_side": 37,
        "concat_order": ["roi", "context", "full"],
        "per_shard": per_shard,
    }
    tmp = out_dir / "_R3_FEATURE_CACHE_LOCK.json.tmp"
    tmp.write_text(json.dumps(lock, indent=2) + "\n")
    os.replace(tmp, out_dir / "_R3_FEATURE_CACHE_LOCK.json")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    p.add_argument("--crop-root", default=str(DEFAULT_CROP_ROOT))
    p.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    p.add_argument("--backbone", default="dinov2_floor", choices=sorted(BACKBONE_CHOICES))
    p.add_argument("--device", default="cuda")
    p.add_argument("--batch-size", type=int, default=64)
    p.add_argument("--shards", default="", help="comma-separated sha2 prefixes (debug/subset)")
    p.add_argument("--limit", type=int, default=0, help="cap total crops (debug)")
    p.add_argument("--no-sha-check", action="store_true", help="skip manifest sha gate (debug only)")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    return run(parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
