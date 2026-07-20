# DATA — R3 DINOv2-S feature cache (PRD §6 R3), 2026-07-19

Status: **COMPLETE** — full 311k embed finished 2026-07-20 01:23 (256/256
shards, 311,126 rows, 7,940 s ≈ 2.2 h on the RTX 4070, 0 `.tmp` leftovers);
§6 reconciliation all-green 2026-07-20.
No training / calibration / evaluation here (that is R4/R5 — out of scope, PRD §0).

Parent PRD: [`PRD-run3-native-local-line-2026-07-19.md`](PRD-run3-native-local-line-2026-07-19.md)
§6 R3. Contract: `R3_FEATURE_CACHE_CONTRACT` docstring in
`scripts/temporal/build_r1_marker_free_crops.py`. Frame list = frozen R0 manifest
(`r0_manifest_v1/manifest.parquet`, 311,195 rows, sha256 `ffd0c174…f8cf`) → the
311,126 unique `src_tiff_sha256` crops. Code:
`scripts/temporal/build_r3_feature_cache.py`; tests:
`tests/temporal/test_r3_feature_cache.py`. Data → `r3_feature_cache_v1/`.

## 0. Inputs consumed (authoritative, not re-derived)

- **R0 manifest** — the frame list of record; its unique `src_tiff_sha256` set
  (311,126) is the crop set. Never a disk scan. Manifest sha256 gated at load
  (`ffd0c174…f8cf`, `EXPECTED_MANIFEST_ROWS=311195`).
- **R1 crop render** — `r1_crops_v1/` was **already fully rendered** when R3
  started (`_R1_CROPS_STATUS.json`: `rendered=310886`, `skipped=309`, `errors=0`;
  **311,126 PNGs on disk** = expected unique crops; 0 `.tmp` left). R3 did **not**
  re-render.
- **R1 geometry index** — `r1_crops_v1/crop_geometry_index/<sha2>.parquet`
  (311,153 rows, **311,126 unique chip_sha**; the 27 duplicate rows carry
  identical geometry per sha — verified 0 inconsistent — so dedup keep-first is
  lossless). Pooling reads `roi_px` / `context_px` / `out_px` / `png_relpath`
  from here; ROI geometry is **never recomputed**. Cross-check at load: geom sha
  set == manifest sha set (0 missing / 0 extra), `crop_geometry` ==
  `r1_cropgeo_v1@2026-07-19`, count == 311,126, else STOP.

## 1. File listing (`r3_feature_cache_v1/`)

```
features/<sha2>.parquet          one shard per chip_sha[:2] (256 shards)
features/<sha2>.parquet.done     atomic per-shard completion marker (n_rows + sha256)
_R3_FEATURE_CACHE_LOCK.json      provenance + per-shard sha + counts (final)
full_embed.log                   run log
```

The R3 implementation payload adds only:
`scripts/temporal/build_r3_feature_cache.py`,
`tests/temporal/test_r3_feature_cache.py`, and this DATA memo. No data is
committed to the repo; the tracker receives only the normal progress note.

## 2. Cache key & schema

Key = `(chip_sha, crop_geometry, backbone_hash, pooling_version)`:

| field | this round |
|---|---|
| `chip_sha` | source raster SHA-256 (manifest `src_tiff_sha256`) |
| `crop_geometry` | `r1_cropgeo_v1@2026-07-19` (from the R1 geometry index) |
| `backbone_hash` | `vit_small_patch14_dinov2.lvd142m@sha256:cb91f5a740744d75` |
| `pooling_version` | `r3_pool_v1` |

`backbone_hash` is **derived from the actual loaded weights** —
`compute_backbone_hash` sha256s the frozen encoder `state_dict` (keys sorted,
key+tensor bytes) and appends the first 16 hex to the timm id. A silent weight /
checkpoint swap changes the hash, hence the key. Not a hand-written constant.

Per-row parquet columns: the four key columns + `feat_roi` / `feat_context` /
`feat_full` (each `list<float16>` length 384) + `roi_n_patches` /
`ring_n_patches` (int32; **0 = documented fallback used**) + `embed_dim` (384) +
`grid_side` (37). fp16 storage verified **bit-identical** on round-trip.

## 3. Backbone

DINOv2 ViT-S/14 (`vit_small_patch14_dinov2.lvd142m`, 22M, patch 14,
`embed_dim=384`), loaded via the frozen `Dinov2PresenceScorer`
(`dinov3_scorer.py`) — **frozen** (`requires_grad_(False)` + `.eval()`,
`torch.inference_mode()`), `dynamic_img_size=True`, native input **518** → patch
grid **37×37 = 1369** tokens, `num_prefix_tokens=1` (cls only; this variant has
no register tokens). Weights cached under
`~/zasolar_data/models/dinov2_floor/hf_cache/` (no download — snapshot already
present). Preprocessing is exactly the scorer's `_load_chip_tensor`
(256-px PNG → 518 BILINEAR resize → model normalization) via
`embed_patch_grids`, so features match what the head/calibration will serve.
**No head, no LoRA, no backbone training** (owner veto, PRD §0.4).

## 4. Pooling design — `r3_pool_v1`

Three pooled features per crop over the 37×37 patch grid, fp32 accumulate →
**fp16 store**:

- **`feat_full`** — mean over all 1369 patch tokens (whole-crop context).
- **`feat_roi`** — masked mean over patch tokens whose **centre** falls inside
  the R1 nominal ROI quad. ROI = area-equivalent square
  (`roi_edge_m = min(fov_m, sqrt(source_area_m2))`) reprojected through the
  frame CRS → a convex quad in output-crop pixels (`roi_px`), normalised by that
  crop's `out_px`. Membership = consistent-sign point-in-convex-quad test
  (winding-invariant, so both 3857 north-up and 4326 anisotropic frames work).
  *Fallback* (`roi_n_patches=0`, sub-patch ROI < one 14-px patch): the single
  patch nearest the ROI centroid (deterministic argmin).
- **`feat_context`** — masked mean over the context **ring** = patches inside the
  `context_px` quad (`context_edge_m = min(fov_m, roi_edge_m·2)`) but **not**
  inside the ROI quad. *Fallback* (`ring_n_patches=0`, ROI fills the FoV so
  ring is empty): `feat_full`.

Single-vector concat order, if a consumer wants one: `[roi, context, full]`
(1152-d). ROI/context pixel boxes come **only** from the R1 geometry index —
no geometry recomputation in R3.

## 5. Throughput (measured)

- Backbone forward is the bottleneck (PIL 256→518 resize + ViT-S forward). Batch
  sweep on RTX 4070 8GB: bs=32 → 36 crops/s; **bs=64 → 43 crops/s (peak
  2.3 GB)**; bs=96 → 42 crops/s. Full run uses **bs=64**.
- Full 311,126 crops ≈ **2.0–2.3 h** single-process, single-GPU. tmux job
  `r3_embed` (>30 min rule). Per-shard atomic write + `.done` marker →
  resumable; a killed run re-runs only unfinished shards.

## 6. Reconciliation (actual vs expected)

Recomputed independently from the 256 Parquet shards and frozen manifest on
2026-07-20; the audit also re-hashed every shard against both its `.done` marker
and the final lock.

| check | expected | actual |
|---|---|---|
| feature rows | 311,126 | ✅ 311,126 |
| unique keys (no dup) | 311,126 | ✅ 311,126 |
| manifest join (every one of 311,195 rows hits a chip_sha) | 0 missing | ✅ 0 missing |
| feature schema | 3 × 384 fp16, no nulls | ✅ min=max=384, 0 null rows |
| shard marker + lock integrity | 256 matching SHA-256s | ✅ 256/256 marker, 256/256 lock |
| re-embed ≥200 crops bit-identical (fp16) | identical | ✅ shard 00, 1,260 crops (§7) |
| kill→resume == one-shot (shard sha) | identical | ✅ (§7) |
| tests | all green | ✅ 18 passed |

## 7. Determinism & resume verification

- **Shard-level bit-identity**: shard `00` embedded to two independent output
  dirs → **byte-identical parquet** (same sha256
  `55b2a81d…ce36`, 1,260 rows), also identical to the production shard. fp16
  feature bits therefore match across all three products. This is stronger than
  the requested 200-crop deterministic sample.
- **Real kill→restart** (RTX 4070, 2026-07-20): the first shard-00 process was
  sent `SIGINT` while `embed_patch_grids` was running. The interrupted output
  contained no Parquet, `.done`, or `.tmp`. Restarting the exact command completed
  1,260 rows; an independent one-shot run produced the same Parquet SHA
  `55b2a81d…ce36` and the same marker SHA. Both Parquet and marker publish through
  `.tmp → os.replace`; resume validates row count, file SHA, chip set, and all
  four cache-key columns before skipping a shard.
- **Tests**: `pytest tests/temporal/test_r3_feature_cache.py` → **18 passed**
  (pure pooling geometry / point-in-quad / mask / fallback / fp16 round-trip /
  backbone-hash determinism / atomic marker + resume validation / cross-key
  overwrite refusal, + a real-frame GPU determinism check on 8 crops).

## 8. Known seams / limitations

1. **cropgeo_v2 (parallel teammate)** — this cache is written under
   `crop_geometry = r1_cropgeo_v1@2026-07-19`. The current executable is hard-
   pinned to that v1 geometry index. The v2 follow-up must add explicit geometry
   selection and write a **distinct versioned output root**, then consumers may
   union v1/v2 by the four-column cache key. Reusing one physical
   `features/<sha2>.parquet` directory across geometry/backbone/pooling variants
   is refused to prevent in-place overwrite.
2. **Nominal ROI, not footprint polygon** (inherited R1 seam §4.1) — ROI is a
   `sqrt(area)` square, so a long thin panel array pools as a square. R2's
   `TargetLocalizationObservation.projected_target_polygon` supersedes it once
   the localization cascade runs; an `r3_pool_v2` keyed on the corrected polygon
   is the upgrade path (new pooling_version, additive).
3. **fp16 storage** — features stored fp16 (contract). Pooling accumulates in
   fp32 then casts; downstream should upcast to fp32 before matmul.
4. **Single-vector consumers** — three features stored separately; concat order
   is fixed `[roi, context, full]` in the lock (`concat_order`).
