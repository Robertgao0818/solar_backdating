# DATA — R1 marker-free student crops (PRD §6.2), 2026-07-19

Status: **generator + tests + stratified QA DELIVERED**; full 311k generation
**NOT started** (awaits team-lead go, per task). No embedding here (that is R3).

Parent PRD: [`PRD-run3-native-local-line-2026-07-19.md`](PRD-run3-native-local-line-2026-07-19.md)
§6 R1. Frame list = frozen R0 manifest
(`run3_native_line_2026-07/r0_manifest_v1/manifest.parquet`, 311,195 rows,
sha256 `ffd0c174…f8cf`). Code: `scripts/temporal/build_r1_marker_free_crops.py`;
tests: `tests/temporal/test_r1_marker_free_crops.py`. Data → `r1_crops_v1/`.

## 1. Crop geometry `r1_cropgeo_v1@2026-07-19`

**Field of view.** The crop reproduces the exact window Gemini scored
(`fullscan_target96_review{24,48}_v2`, `gehi_common.ensure_single_target_
review_png`) **minus the crosshair marker**. It is a pixel-space centred crop of
the 96 m source chip (no reprojection), re-derived with the identical formula so
the marker-free bytes match the teacher's `draw_marker=False` path. Verified
**byte-identical (max pixel diff = 0)** for all four CRS×arm combinations.

- `crop_size_m = min(96, max(fov_m, 2·r·0.01)) = fov_m` → **24 m (A24) / 48 m
  (A48)**, sourced from `chip_geometry.py` (never hard-coded here).
- `crop_px = clamp(round(S·fov_m/96), 1, min(W,H))`, `S=min(W,H)`; centred window;
  upsample short side to **256 px** (BICUBIC) — output is 256×256.
- Target offset ≡ 0 (per-target chips; the manifest carries no offset column and
  centre ≡ centroid within <0.02 m, re-verified per frame — see §2).

**Nominal ROI (pre-localization).** Axis-aligned square, centred on the target
centroid, edge `roi_edge_m = min(fov_m, sqrt(source_area_m2))` (area-equivalent
square, clamped to the FoV). **Context ring** = annulus between the ROI square and
a `context_edge_m = min(fov_m, roi_edge_m·2.0)` square (roof/context band). Both
polygons are recorded in **output-crop pixels** *and* **lon/lat** (the "像素/米两
套坐标" contract; UTM-35S corners recoverable from lon/lat).

**Dual-CRS handling (red line).** ROI/context corners are defined in true ground
metres via **UTM 35S (EPSG:32735)** then mapped through *each frame's own* CRS —
EPSG:3857 (Wayback, Web-Mercator-inflated metres: chip span ≈106 map-units ≈95 m
ground) and EPSG:4326 (TM, square-in-degrees ⇒ non-square-in-ground, gsd_x≈0.268
vs gsd_y≈0.299 m/px). Both paths have dedicated real-frame tests. The TM path's
crop is therefore mildly anisotropic in ground metres (A24 ≈22.0×24.3 m for a
nominal 24 m FoV) — a faithful property of the teacher render, recorded per crop
as `fov_ground_w_m` / `fov_ground_h_m`.

**Output key** `(chip_sha, crop_geometry)`: a `chip_sha` (`src_tiff_sha256`)
maps to exactly one anchor/arm (verified: 0 shas span arm or review extent), so
the pair is unambiguous. **311,126 unique crops** for 311,195 manifest rows (69
byte-identical rasters shared across two capture_dates collapse to one crop).

Layout: `crops/<sha2>/<chip_sha>.r1cg1.png` (atomic .tmp→rename),
`crop_geometry_index/<sha2>.parquet` (pure fn of manifest; always rewritten),
`qa/`, `_R1_CROPS_STATUS.json`. Resume = skip existing non-empty PNG.

## 2. QA results (stratified sample, 240 frames)

30 frames per (chip_arm × area_bin × CRS) cell = **240**, exactly balanced
(≥200 requirement met):

| stratum | EPSG:3857 | EPSG:4326 |
|---|---|---|
| A24 \| [0,15) | 30 | 30 |
| A24 \| [15,40) | 30 | 30 |
| A48 \| [40,100) | 30 | 30 |
| A48 \| [100,inf) | 30 | 30 |

- **Crop-centre reprojection error** (centroid → frame CRS → source px vs crop
  window centre): **max 0.984, p99 0.920, mean 0.512 source-pixel** — under the
  <1 px invariant. (Diagnostic output-pixel value max 2.36 is the same error in
  the ~3× upscaled 256-px frame; the source pixel is the native ~0.3 m GSD
  reprojection scale, so the invariant is asserted there.)
- **ROI in bounds**: 240/240.
- **Ground FoV**: A24 21.96–23.87 m (w) / 23.71–24.36 m (h); A48 43.66–47.74 (w)
  / 47.42–48.43 (h) — within ±15 % of nominal, per-axis TM anisotropy visible.
- **Teacher byte-identity**: max pixel diff 0 across all 4 CRS×arm cells.
- QA sheet `r1_crops_v1/qa/qa_sheet.html` (crop + red ROI / cyan context overlay
  on a copy; stored PNGs carry no overlay, no crosshair). Eyeballed A24 + A48,
  both CRS: no marker, correct FoV, ROI on target.

**Tests**: `pytest tests/temporal/test_r1_marker_free_crops.py` → **18 passed**
(12 synthetic/pure always-on: crop-window formula, world↔px, dual-CRS synthetic
geometry, ROI/context edges, FoV clamp, render+resume idempotency, version
propagation, sha gate; 6 real-frame integration, skipped without the drive).

## 3. Throughput estimate (full 311k)

- Manifest load ≈1.3 s; **geometry ≈6,500 frames/s** → whole corpus ≈0.8 min.
- **Render ≈56 frames/s single-process** → 311,126 unique crops ≈**93 min**,
  disk ≈**21.4 GB** (avg 67 KB/PNG). Resumable; a tmux job is appropriate
  (>30 min rule). Trivially parallelizable if a faster turnaround is wanted
  (render loop is embarrassingly parallel by chip_sha; not wired up to keep the
  first pass simple/correct).

## 4. Known limitations & interface seams (for team-lead)

1. **ROI is a `sqrt(area)` square, not the true footprint polygon.** The manifest
   carries `source_area_m2` but **not** `source_width_m/height_m` (they exist in
   `anchors_v2/anchors_all.csv` but were intentionally dropped from R0). Per the
   manifest-authoritative red line I did **not** join a second source; the
   area-equivalent square loses aspect ratio (a long thin panel array becomes a
   square). This is the R1 **nominal** ROI only.
   - *Seam to R2*: `TargetLocalizationObservation.projected_target_polygon`
     (`src/solar_backdating/localization/observation.py`) is the **corrected**
     polygon; R3 pooling should prefer it when `target_localized=True`/present and
     fall back to this nominal ROI otherwise. The two must agree on coordinate
     frame — R1 stores ROI in output-crop px + lon/lat; the TLO stores lon/lat
     (or WKT). **Recommend**: if aspect ratio matters for pooling quality, either
     (a) extend R0 to carry width/height and cut `r1_cropgeo_v2`, or (b) have R2
     always emit `projected_target_polygon` from the real footprint. Flagging for
     decision, not silently reaching past the manifest.
2. **Panel-definition seam**: R1 has no notion of individual PV panels — the ROI
   is the whole census footprint. If R3 pooling wants a tighter "panel-only"
   mask, that is a new pooling_version + a mask source, not an R1 crop change.
3. **Offset assumption**: crop centre = image centre (teacher used offset≡0). Held
   within 0.98 src-px against the reprojected centroid across all sampled frames;
   if any future arm uses non-zero target offsets, `compute_crop_window` needs the
   offset plumbed (currently absent from the manifest).
4. **Upscale vs native**: crops are upsampled to 256 px (teacher parity). R3 sees
   256-px BICUBIC crops, not native ~85-px A24 windows — matches what Gemini saw,
   but worth noting for backbone input-resolution choices.

## 5. R3 feature-cache contract (defined, not implemented)

Documented in the script's `R3_FEATURE_CACHE_CONTRACT` docstring: key =
`chip_sha + crop_geometry + backbone_hash + pooling_version`; fp16 pooled ROI
feature; sharded Parquet (by `chip_sha[:2]`, matching R1); atomic completion
marker + resume. R1 guarantees the `(chip_sha, crop_geometry)` half is stable and
records `roi_*_px` / `context_*_px` for the pooling to read.
