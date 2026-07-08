# Data memo: MultiPolygon share + target size vs per-vintage GSD (2026-07-06)

Status: exploration item 3 for the SPEC session (follows the gap audit
`SPEC-GAP-AUDIT-goldset-night1-2026-07-05.md`). Feeds: (i) the target
**visibility model**, (ii) the **per-anchor window definition** (GAP-4), and
(iii) amendment **A**'s marker rule (GAP-1).
Provenance: 5-agent workflow run `wf_e1fa3c29-acb` (3 independent computes +
area×GSD join + independent re-derivation of every headline number — 5/5
CONFIRMED, one via `scripts/temporal/summarize_scan_zoom.py`).

## 1. Geometry: MultiPolygon share = 0 across the whole chain

| file | n | MultiPolygon | note |
|---|---:|---:|---|
| `jhb_full382_unified_A_merge01_c0925.gpkg` (pre-fpcut) | 47,465 | **0** | centroid-outside 113 (0.238%) |
| `..._fpcut_2026-06-01.gpkg` (production inventory) | 41,393 | **0** | centroid-outside 98 (0.237%), 0 invalid geoms |
| `chipgroups/chip_targets.gpkg` (anchor points) | 41,393 | — (Point) | points = raw `.centroid`, 0.0 m offset, **no inside-fallback applied** |
| `chipgroups/chip_groups.gpkg` | 15,859 | **0** | |

- DO NOT design multi-part handling into the new dating unit — there is
  nothing to handle (verified via header type + per-row `.geom_type`).
- The 98 centroid-outside cases are concave per-detection-merge envelopes
  (solidity median 0.78 vs 0.94 population; larger installs, median 43.8 m²);
  rate is unchanged by the FP-cut (0.238%→0.237%), i.e. shape artifact, not
  confidence-correlated. `representative_point()` recovers **98/98 (100%)**.
- **Amendment A quantified:** marker = target polygon centroid is on-panel
  99.76%; the new scheme MUST emit `representative_point()` (or fallback for
  the 98) — today's `chip_targets` writes raw centroids with no guard.
- Join trap: `chip_targets.source_feature_id` is **0-indexed positional**;
  GPKG `fid` is 1-indexed. An off-by-one join looks like 99.5% "outside".

## 2. Target size distribution (n = 41,393, per-target, m²)

| p10 | p25 | p50 | p75 | p90 | mean | share <10 m² | <20 m² | <40 m² |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 6.1 | 8.9 | 14.2 | 23.6 | 47.2 | 31.1 | 31.0% | 68.3% | 87.8% |

- sqrt(area) p50 = 3.76 m; min(width,height) p50 = 3.76 m; observed minimum
  area = 3.51 m² (floor unexplained by `v4_canonical` `min_object_area` — open
  question, descriptive only). Right-skewed (mean 2.2× median): DO model on
  log/percentiles, never mean±std.
- CSV `source_area_m2` ≡ geometry area (max reldiff 1.3e-5).
- **WARNING (side finding, verified twice):** the fpcut gpkg **attribute**
  `area_m2` is stale vs its own geometry for the ~75% unmerged rows — median
  +16.8% overstatement, worst +78%. DO NOT consume `area_m2` from the gpkg
  attribute table; use `chip_targets.csv` or recompute from geometry.

## 3. Per-vintage GSD (scan corpus: 15,859 scan states, 172,913 scored frames)

Achieved-zoom mix (deduped = raw; zero (anchor, date) re-scores exist):
**z20 72.48% / z19 26.82% / z18 0.69%** — confirms the prior hand count to
0.02 pp. GSD at JHB (lat −26.2°, <1% metro variation): **z20 0.134 / z19
0.268 / z18 0.536 m/px**.

Zoom mix by capture year (deduped frames):

| year | n | z20% | z19% | z18% |
|---:|---:|---:|---:|---:|
| 2009 | 271 | 0.0 | 68.3 | 31.7 |
| 2010 | 261 | 0.0 | 97.3 | 2.7 |
| 2011 | 180 | 0.0 | 90.6 | 9.4 |
| 2012 | 309 | 0.0 | 98.4 | 1.6 |
| 2013 | 1,183 | 0.0 | 98.3 | 1.7 |
| 2014 | 1,741 | 0.0 | 99.8 | 0.2 |
| 2015 | 2,560 | 1.7 | 98.2 | 0.1 |
| 2016 | 2,149 | 0.0 | 99.6 | 0.4 |
| 2017 | 3,022 | 3.4 | 96.0 | 0.5 |
| 2018 | 22,987 | 25.9 | 73.9 | 0.2 |
| 2019 | 15,680 | 47.3 | 52.0 | 0.7 |
| 2020 | 16,878 | 57.0 | 42.1 | 0.9 |
| 2021 | 32,808 | 91.3 | 7.8 | 0.9 |
| 2022 | 26,156 | 99.1 | 0.4 | 0.5 |
| 2023 | 2,038 | 98.4 | 0.3 | 1.2 |
| 2024 | 16,117 | 98.0 | 0.6 | 1.4 |
| 2025 | 28,573 | 99.7 | 0.0 | 0.3 |

**Two resolution regimes, not a curve:** ≤2017 is GSD-floored at z19
(0.27 m/px) with a pre-2013 z18 tail; ≥2022 is z20-floored (0.13 m/px); the
transition concentrates in 2018–2021. DO give the visibility model a rung
fixed effect; DO NOT fit a smooth GSD-vs-year interpolation.

Caveat (D17/ISSUE-18): `actual_zoom` is the delivered ladder rung, not
pixel-effective resolution — GEHI can silently substitute coarser tiles
in-chip; the effective-resolution sentinel is the separate guard.

## 4. Visibility join: share of 41,393 targets below extent_px = √area / GSD

| extent_px < | z18 | z19 | z20 |
|---:|---:|---:|---:|
| 3 | 0.0% | 0.0% | 0.0% |
| 5 | 14.9% | 0.0% | 0.0% |
| 8 | **64.3%** | **3.1%** | 0.0% |
| 12 | 88.3% | 32.8% | 0.0% |
| 16 | 93.3% | 64.3% | 3.1% |

- Min-detectable area at z19: **≈4.6 m² @ 8 px** ("shape-confirmable") or
  ≈1.8 m² @ 5 px ("bare blob") — pick the convention deliberately.
- ~3.1% of the census (~1,271 targets) is sub-8px even at z19 → some
  `ambiguous_*` statuses (13.0% nonmonotonic + 16.4% no_recent_anchor) may be
  small-target GSD noise; DO run a size-stratified status check before
  attributing ambiguity to scene change.
- Expected non-visible share per capture year (zoom-mix ⟂ size assumption):
  E[<8px] = 22.5% (2009) → 4–9% (2010–2013) → ~3% (2014–2017) → 2.4% (2018)
  → <1% (2021+).

## 5. Per-anchor window facts → GAP-4 definition

- Earliest observed capture per anchor: **94.6% bottom out at 2018**
  (round-1 default `window_start_date = 2018-03-30` — a scan-policy artifact,
  NOT "GEID starts in 2018"); tail of 5.4% (859 anchors) reaches 2009–2015.
  Latest capture = 2025 for 100% of anchors. Distinct dates per anchor:
  p10/p50/p90 = 7/9/13.
- Status tally: done_appears 68.9%, ambiguous_no_recent_anchor 16.4%,
  ambiguous_nonmonotonic 13.0%, installed_during_census 1.1%,
  already_present_before_history 0.4%, gemini_failed 0.2%.
- **Window-definition implication:** `window_start(available)` vs
  `window_start(visible, extent_px≥8)` barely diverge for the 2018-floored
  94.6% (E[non-visible] ≈ 2.4%); they diverge materially only inside the
  2009–2011 sub-tail (8.8–22.5% sub-8px years). DO carry per-anchor
  `window_start`/`window_end` (GAP-4) **plus** the target's `extent_px` per
  scored rung, so the deliverable can distinguish "not visible yet" from
  "absent" without a wholesale window redefinition.
- `gehi_availability_raw.jsonl` is a 10-line legacy `johannesburg_G0922_*`
  probe — not joinable to this corpus; availability facts above come from the
  scan states themselves.

## Reproduce

```bash
# zoom mix: scripts/temporal/summarize_scan_zoom.py --scan-states-dir \
#   ~/zasolar_data/geid_temporal/jhb_full382_fpcut_scan_2026-06-02/scan_states
# geometry: pyogrio over results/analysis/full382_merge01_2026-05-15/*.gpkg (native 32735)
# area: chipgroups/chip_targets.csv source_area_m2 (validated vs geometry)
# join: extent_px = sqrt(area_m2)/GSD(z); GSD = 156543.03392*cos(-26.2°)/2^z
# full agent reports: workflow wf_e1fa3c29-acb journal (session 2e7fb9a3)
```
