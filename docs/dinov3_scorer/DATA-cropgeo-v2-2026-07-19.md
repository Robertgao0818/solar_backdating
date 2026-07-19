# DATA — R1 crop geometry v2 (`r1_cropgeo_v2@2026-07-19`), 2026-07-19

Status: **DELIVERED** (geometry generator amendment + tests + full index + QA).
Owner-approved via `DATA-r0-footprint-sidecar-2026-07-19.md` §4. Closes the seam
that same doc's §4.1 (and this generator's own R1 v1 docstring) flagged: the R1
nominal ROI was an equal-area **square** (`sqrt(source_area_m2)`), collapsing a
long thin panel array's aspect ratio. Code: `scripts/temporal/build_r1_marker_
free_crops.py` (single production file, amended in place — no new script).
Tests: `tests/temporal/test_r1_marker_free_crops.py` (20 new cases, 38 total).

## 1. Geometry definition

Axis-aligned, area-preserving **rectangle**, built from the R0.1 footprint
sidecar (`footprint_sidecar_v1.parquet`, 41,393 rows, keyed on `anchor_id`) —
not re-derived, read directly:

```
r = aspect_ratio (sidecar, bbox long/short, >= 1)
long_pre  = sqrt(area * r)   # sidecar's areamatched_long_m
short_pre = sqrt(area / r)   # sidecar's areamatched_short_m
long axis := x   if source_width_m >= source_height_m   else y
edge_x = min(fov_m, edge_x_pre) ;  edge_y = min(fov_m, edge_y_pre)      # per-axis FoV clamp
context_edge_x = min(fov_m, edge_x * 2.0) ; context_edge_y = min(fov_m, edge_y * 2.0)
```

`long_pre * short_pre == source_area_m2` exactly (area-preserving, pre-clamp) —
asserted by test and reconfirmed on 25 real corpus rows. No rotation is
attempted: the sidecar carries only an axis-aligned bbox, so an oriented ROI is
explicitly out of scope here (R2's `projected_target_polygon` job, per sidecar
doc §4). If either axis clamps, the rendered ROI area is smaller than
`source_area_m2`; this is recorded per-crop, never silently absorbed
(`roi_clamp_loss_frac = max(0, 1 - edge_x*edge_y/source_area_m2)`). If an
anchor's sidecar row is missing or its area-matched columns are NaN, the crop
falls back to the v1 square (`roi_source="fallback_square_v2"`) — realized
count on the full corpus: **0** (sidecar/manifest join is 0 missing / 0 extra,
re-verified in-run, not assumed).

**Crop raster is untouched.** FoV, centred window, 256px BICUBIC upsample, no
marker are byte-identical to v1 for the same `chip_sha` — v2 changes only the
ROI/context *annotation*, never the pixels. Consequently **v2 never renders**:
`crop_geometry_index_v2/` (a new sibling of the unchanged `crop_geometry_index/`)
is written under the SAME `out_dir` as v1 (`r1_crops_v1/`), and its
`png_relpath` points at the identical `crops/<sha2>/<chip_sha>.r1cg1.png` v1
already wrote (or will write) — same filename tag either version. This is how
`crop_geometry` re-keys the R3 feature cache (`chip_sha + crop_geometry +
backbone_hash + pooling_version`) without re-keying the crop-pixel store. A
version-specific status file (`_R1_CROPS_STATUS_v2.json`) avoids clobbering
v1's own `_R1_CROPS_STATUS.json` since both share `out_dir`.

New CLI: `--crop-geometry {v1,v2}` (default v1, unchanged), `--sidecar <path>`,
`--qa-mode {area_crs,aspect_arm}` (default `area_crs` = v1's existing grouping;
`aspect_arm` = v2's aspect_bin × chip_arm QA with a v1-vs-v2 IoU overlay).

## 2. v1 regression

18 pre-existing tests pass unmodified (same assertions, same expected numeric
values — nothing in the diff changes what they check). The refactor that makes
this possible: `_square_corners` is now `_rect_corners(cx, cy, edge, edge)`
(identical math), and `compute_geometry_record` takes new `crop_geometry`/
`sidecar_row` keyword args that both default to the v1 path, so every existing
call site is unaffected. `resolve_roi_spec` for `crop_geometry=v1` reduces to
exactly the old `min(fov_m, sqrt(area))` square, and 8 new regression-lock
tests assert that the square invariant (`edge_x == edge_y == roi_edge_m`),
zero clamp loss, and byte-for-byte record equality between the implicit-default
and explicit-v1 call paths all hold. Full local run: **38/38 passed**; whole
repo suite: **1502 passed, 13 skipped** (one pre-existing, unrelated
environmental failure in `test_pilot_sequence_head.py` deselected — a missing
data file from the 07-19 archive cleanup, [[archive-0711-tar-deleted]] in
memory, not touched by this change).

**Incident + remediation (full disclosure).** While spot-checking whether the
full corpus's `roi_out_of_bounds`/reprojection stats were v2-specific, I ran
`--crop-geometry v1 --qa-only` over the real 311,195-row manifest as a
comparison baseline. `--qa-only` does *not* skip the final status/QA writes —
it only skips render + index write — so that run overwrote the real production
`_R1_CROPS_STATUS.json` (zeroing `rendered`/`skipped`, which had held the true
one-time render tally) and regenerated `qa/qa_sheet.html` from the full 311k
population instead of the original curated 240-frame stratified pilot,
leaving 64 orphan tile PNGs behind. This was corrected in-session:
`_R1_CROPS_STATUS.json` was rewritten with the exact original values recovered
from `full_run.log` (`rendered=310886, skipped=309, errors=0, qa_sheet=null`),
and `qa/qa_sheet.html` + `qa/tiles/` were regenerated via
`--sample-per-stratum 30 --qa-only` (same default seed, same unchanged v1
code), reproducing the documented pilot numbers exactly (max 0.984, p99 0.920,
mean 0.512 src-px, ROI in bounds 240/240 — matching `DATA-r1-crops-2026-07-19.md`
§2 verbatim) before the 64 orphan tiles were deleted. Both restored files were
diffed against the recovered/expected content and confirmed correct. Net
effect: zero permanent change to v1's real outputs, but the incident is
recorded here since it touched production data outside this task's read-only
mandate on `r1_crops_v1`'s existing artifacts.

## 3. Full-corpus v2 index (311,195 rows, all real anchors)

Generated via `--crop-geometry v2` (no sampling) → `crop_geometry_index_v2/`
(256 shards, 140 MB) + `_R1_CROPS_STATUS_v2.json`, ~53 s wall (pure geometry;
no render — all 311,195 referenced PNGs already existed from the prior v1
render pass, confirmed by count).

| metric | v1 (comparison) | v2 |
|---|---|---|
| `roi_source` | n/a (always square) | 311,195 `sidecar_v2`, **0** `fallback_square_v2` |
| `roi_clamp_loss_frac` | not tracked in v1 | max 0.4252, p99 0.0, mean 0.000107, **0.065%** of rows >0 |
| `roi_out_of_bounds` | 72 (0.023%) | 192 (0.062%) |
| `center_reproj_err_px` max/p99/mean | 1.0117 / 0.8508 / 0.4234 | **identical** (crop window untouched by ROI-shape change) |

`center_reproj_err_px` being bit-identical between v1 and v2 full-corpus runs
is itself a strong regression proof: it depends only on the crop window/render
geometry, never on `crop_geometry`, and the two runs produced the exact same
max/p99/mean to 5 decimals.

`roi_out_of_bounds` rising from 72 to 192 is an expected, explainable
consequence of the aspect correction, not a defect: v1's square and v2's long
axis both clamp to the same `fov_m` value when their pre-clamp edge exceeds
it, so every v1 OOB case recurs in v2 on that axis. v2 additionally clamps
whenever `sqrt(area * r) > fov_m` even though `sqrt(area) <= fov_m` (always
true for `r > 1`) — i.e. moderately elongated targets that fit comfortably as
a square now have their long edge reach the FoV clamp. Combined with the
already-documented per-axis TM/Web-Mercator ground anisotropy (a clamped edge
of exactly `fov_m` in *nominal* metres can slightly exceed the frame's
*actual* rendered ground width on the anisotropic axis — see
`DATA-r1-crops-2026-07-19.md`'s `fov_ground_w_m`/`fov_ground_h_m` A48 range of
43.66–47.74 m against a 48 m nominal), this accounts for the extra 120 cases.
All are still written to the index (`roi_in_bounds=False` is a diagnostic flag,
not a drop) — R3 pooling must already clip ROI polygons to image bounds for
v1's 72 cases, so this is the same, not a new, seam.

## 4. Aspect-stratified QA (160 frames, ≥160 requirement met)

`--crop-geometry v2 --qa-mode aspect_arm --sample-per-stratum 20 --qa-only`:
20 frames × (aspect_bin ∈ {[1,1.5), [1.5,2), [2,4), [4,∞)}) × (chip_arm ∈
{A24, A48}) = 8 cells × 20 = **160**. QA sheet:
`r1_crops_v1/qa/qa_sheet_cropgeo_v2.html` (red = v2 rectangle ROI, yellow = v1
square ROI overlay, cyan = v2 context ring; drawn on a copy of the SHARED v1
PNG, never re-rendered) + `qa/cropgeo_v2_qa_stats.json`.

IoU(v1 square, v2 rectangle) by aspect bin (concentric axis-aligned rectangles,
so IoU = per-axis-min product / union — exact, no approximation):

| aspect bin | A24 IoU mean (p10–p90) | A48 IoU mean (p10–p90) |
|---|---|---|
| [1, 1.5) | 0.819 (0.697–0.944) | 0.853 (0.731–0.974) |
| [1.5, 2) | 0.625 (0.571–0.680) | 0.631 (0.575–0.681) |
| [2, 4) | 0.441 (0.361–0.522) | 0.457 (0.374–0.536) |
| [4, ∞) | 0.289 (0.243–0.327) | 0.270 (0.206–0.321) |

Overall: n=160, IoU mean **0.548**, median **0.548**. IoU falls monotonically
as aspect grows — exactly the expected signature of an aspect correction (a
near-square target's v2 rectangle is nearly the v1 square, IoU≈0.8+; a highly
elongated target's v2 rectangle and v1 square overlap only in their common
short-axis extent, IoU≈0.27–0.29). This is the QA proof that v2 is doing real,
aspect-proportional work, not a no-op.

Within the sample: `roi_clamp_loss_frac > 0` for 1/160 (0.625%);
`roi_out_of_bounds` 1/160, isolated to the `[4,∞) | A48` cell — a large,
extremely elongated target (`roi_edge_x_m=48.0` exactly clamped,
`roi_edge_y_m=5.46`, `roi_clamp_loss_frac=0.0947`) exhibiting exactly the
clamp+anisotropy mechanism in §3. Visually spot-checked in the QA sheet: ROI
in bounds, long/short edges land on the correct raw axis in all sampled tiles,
context ring encloses the ROI, no marker artifacts (shared v1 pixels).

## 5. Interface seams (for team-lead / downstream)

1. **R3 feature cache**: `crop_geometry` is part of the cache key
   (`chip_sha + crop_geometry + backbone_hash + pooling_version`); v2 rows will
   miss v1's cache entirely and need their own pooling pass. No code change
   needed there beyond reading `roi_edge_x_m`/`roi_edge_y_m` (or the polygon
   corners) instead of assuming a square — the parallel R3 cache-build
   teammate's v1 work is untouched (different cache, same PNGs).
2. **R2 `projected_target_polygon`**: per the sidecar doc's sequencing note,
   v2's rectangle is explicitly the *pre-localization fallback*; R2's real
   polygon (when `target_localized=True`) should supersede it for pooling.
   This amendment does not wire that preference — it only ships the fallback
   rectangle. Whoever builds R3 pooling needs to choose polygon vs rectangle
   per row.
3. **`roi_out_of_bounds` handling**: 192/311,195 (0.062%) v2 ROIs extend beyond
   the 256px crop on at least one corner (mechanism in §3). Pooling must clip
   to image bounds; this is not new (v1 already has 72 such cases) but the
   count is larger under v2 and concentrated in the high-aspect population, so
   a downstream QA pass on *pooled* features should expect it.
4. **Directory-naming caveat**: v2's data lives under the SAME `out_dir`
   (`r1_crops_v1/`) as v1 by design (PNG sharing), so the top-level directory
   name is no longer literally version-scoped — `crop_geometry_index/` (v1,
   unchanged) and `crop_geometry_index_v2/` (v2, new) are the only
   version-specific paths under it. Anyone scripting against `r1_crops_v1/`
   should key off the `crop_geometry` column / index subdirectory, not the
   top-level folder name.
5. **Status-file versioning**: `_R1_CROPS_STATUS.json` (v1) and
   `_R1_CROPS_STATUS_v2.json` (v2) are independent; each is overwritten by its
   own version's runs only. A pilot/full-run ordering convention (smaller
   samples first, full corpus last) determines which numbers persist in the
   file, same as v1's existing pilot→full-run pattern — not enforced by code,
   just a run-order discipline worth documenting for whoever runs this next.

## 6. Files touched / produced

- `scripts/temporal/build_r1_marker_free_crops.py` — amended in place (sole
  production file touched, as scoped).
- `tests/temporal/test_r1_marker_free_crops.py` — 20 new tests added (18 → 38
  total, all passing).
- `docs/dinov3_scorer/DATA-cropgeo-v2-2026-07-19.md` — this document.
- Data (never committed, under `~/zasolar_data/geid_temporal/run3_native_
  line_2026-07/r1_crops_v1/`):
  - `crop_geometry_index_v2/` — 256 shards, 140 MB, 311,195 rows.
  - `_R1_CROPS_STATUS_v2.json` — full-corpus provenance (final write).
  - `qa/qa_sheet_cropgeo_v2.html` + `qa/tiles_cropgeo_v2/` (64 tiles) +
    `qa/cropgeo_v2_qa_stats.json` — the aspect-stratified QA deliverable.
  - `crops/`, `crop_geometry_index/`, `_R1_CROPS_STATUS.json`, `qa/qa_sheet.html`
    + `qa/tiles/` (64 tiles) — v1's, restored to their pre-incident state
    (§2), unchanged in substance from before this task.
