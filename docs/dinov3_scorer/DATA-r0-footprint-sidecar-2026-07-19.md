# DATA — R0.1 footprint sidecar (PRD §3.2 amendment), 2026-07-19

Status: **DELIVERED** (generator + tests + sidecar/lock written). Owner-approved
R0.1 (PRD §3.2 amendment, commit 563a772). Closes the seam reported in
[`DATA-r1-crops-2026-07-19.md`](DATA-r1-crops-2026-07-19.md) §4.1 ("sqrt(area)
square drops aspect ratio"). Pure additive; the frozen R0 products are untouched.

- Code: `scripts/temporal/build_r0_footprint_sidecar.py`
- Tests: `tests/temporal/test_r0_footprint_sidecar.py` (6 passed)
- Data: `r0_manifest_v1/footprint_sidecar_v1.parquet` (41,393 rows,
  sha256 `a1f1bd9cd81f21e3…`) + `FOOTPRINT_SIDECAR_LOCK.json`
- Version: `footprint_v1@2026-07-19`

## 1. Schema (keyed on `anchor_id`)

| column | meaning |
|---|---|
| `anchor_id` | key (unique, 41,393) |
| `chip_arm` | A24 / A48 |
| `source_area_m2` | footprint polygon area (== manifest, verified) |
| `source_width_m`, `source_height_m` | **axis-aligned bbox** dims (m) from CSV |
| `footprint_long_m`, `footprint_short_m` | max/min of width/height |
| `bbox_area_m2` | `w·h` |
| `bbox_fill` | `area/(w·h)` — 1.0 = perfect axis-aligned rect; <1 = rotated/irregular |
| `aspect_ratio` | `long/short` (bbox) |
| `sqrt_area_m` | `sqrt(area)` = the current R1 square edge |
| `areamatched_long_m`, `areamatched_short_m` | proposed v2 ROI: `sqrt(A·r)` / `sqrt(A/r)` — area-preserving rectangle at the bbox aspect |
| `footprint_version` | `footprint_v1@2026-07-19` |

**Source self-check**: `anchors_all.csv` has **no polygon/WKT and no
orientation/angle column** — only width/height/area. The tight oriented polygon
lives only in the legacy source gpkgs (`legacy_source_inventory_path`); reading
it is **out of scope** here and belongs to R2's `projected_target_polygon`.

## 2. Reconciliation (all hard gates PASS)

- rows = **41,393**; `anchor_id` unique; set vs manifest: **0 missing / 0 extra**.
- CSV `source_area_m2` vs manifest `source_area_m2` per anchor: **max abs diff 0.0**
  (shared origin confirmed).
- **Frozen-file freeze guard**: `manifest.parquet` / `splits.parquet` /
  `MANIFEST_LOCK.json` sha256 **byte-identical before and after** the run
  (asserted in-code; run aborts non-zero if any changed).

## 3. Footprint shape distributions (the cropgeo_v2 decision input)

**Aspect ratio** (axis-aligned bbox `long/short`):

| p50 | p90 | p95 | p99 | max |
|---|---|---|---|---|
| 1.54 | 2.99 | 3.71 | 5.42 | 10.56 |

- frac aspect >1.5: **52.7%**; >2: **28.6%**; >3: **10.0%**; >4: **3.9%**.
- By arm — A24 (n=36,322): p50 1.54 / p90 2.95 / 28.0% >2; A48 (n=5,071): p50 1.61
  / p90 3.33 / **33.3% >2** (larger installs are more elongated).

**bbox fill** (`area/(w·h)`): p50 **0.625**, p90 0.89; **44.5% < 0.6**, only 2.0%
> 0.95. → `source_width_m/height_m` are loose axis-aligned boxes that **overstate**
the tight extent for rotated/irregular footprints; using raw `w·h` as an ROI would
over-cover (include non-panel roof). Also implies the bbox aspect is a **lower
bound** on true panel elongation (a 45°-rotated thin array reads near-square in an
axis-aligned bbox).

**R1 square under-reach** (`long_side / sqrt(area)`): p50 1.60, p90 2.20, max 5.91;
**91.6% > 1.25**, 63.5% > 1.5, 18.4% > 2. → the current `sqrt(area)` square edge is
shorter than the long footprint side for ~92% of targets.

## 4. cropgeo_v2 recommendation (suggest only, not implemented)

1. **Adopt an aspect-aware, area-matched rectangle ROI**, not raw `w·h`:
   `long = sqrt(A·r)`, `short = sqrt(A/r)` with `r = aspect_ratio`
   (`areamatched_long_m/short_m`, already in the sidecar). It preserves the
   footprint's elongation while staying area-faithful — so it does **not**
   over-cover the way loose `w·h` (fill 0.63) would. This upgrades the ROI for the
   **28.6%** of targets with aspect >2 (and the ~92% where the square under-reaches
   the long side) at zero extra data cost.
2. **Keep it axis-aligned in the crop** unless orientation is available. The bbox
   gives no reliable rotation; a rotated thin array still can't be tightly boxed
   from width/height alone (fill tail to 0.17). The rotated/irregular remainder is
   **R2's job**: `projected_target_polygon` from the real gpkg geometry is the only
   way to recover orientation — cropgeo_v2 should prefer it when
   `target_localized=True` and fall back to the area-matched rectangle otherwise.
3. **Version discipline**: cropgeo_v2 is a new named `crop_geometry` string; it
   re-keys the R3 feature cache (`chip_sha + crop_geometry + backbone_hash +
   pooling_version`), so v1 crops/features stay valid and comparable. A pooling
   change (masked mean over the rectangle vs the square) is a separate
   `pooling_version`.
4. **Whether v2 is worth building now**: the 28.6%-aspect>2 / 92%-under-reach
   numbers say the square is a materially lossy nominal ROI, but the fill/rotation
   caveat says the *biggest* gains still need R2's true polygon. Suggest sequencing
   v2 **after** R2 lands `projected_target_polygon`, so v2 pooling can consume the
   corrected polygon where available and the area-matched rectangle only as the
   pre-localization fallback — avoiding two ROI rewrites. Decision is owner's; this
   sidecar unblocks either path.

## 5. Limitations

- width/height are axis-aligned bbox, not oriented extent (fill median 0.63);
  aspect is a lower bound on true elongation. No orientation recoverable here.
- No polygon carried (CSV has none); true footprint geometry deferred to R2 via
  the legacy gpkgs.
- The sidecar is descriptive/geometry only — it does not alter labels, splits, or
  any frozen R0 contract.
