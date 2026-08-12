# PRD: Cape Town Backdating v1 — high-density cohort production plan

Date: 2026-07-24  
Status: **STAGE 0 ACTIVE — scope/cutoff/grid decisions confirmed; paid
GEHI/Gemini work remains gated**  
Owner: ZAsolar install-date workstream  
Parent plan: [`../install_date_optimization_v2_prd.md`](../install_date_optimization_v2_prd.md)  
Execution tracker: [`RUN-cape-town-backdating-tracker-2026-07-24.md`](RUN-cape-town-backdating-tracker-2026-07-24.md)

This is a run-specific production plan. It does not replace the v2
plan-of-record or change the already-frozen Johannesburg RUN 3 provenance.

## 1. Executive decision

The first Cape Town production should be a **high-density cohort run**, not a
city-wide estimate and not a copy of the Johannesburg launcher.

The frozen cohort is the complete tie band at the 50th grid rank:

- **52 CPT grids** (the 50th rank is a three-way tie);
- **21,453 unique installation anchors**;
- **19.19%** of the 111,801-object CT inventory;
- A24 (`area_m2 < 40`) = **19,729**; A48 (`area_m2 >= 40`) = **1,724**.

The strict top-50 alternative (20,769 anchors) is rejected for this run. The
complete 52-grid tie band is owner-confirmed and must not be reduced by an
outcome-dependent tie-break.

The release claim is:

> first-visible-appearance interval under the available GEHI observation
> cadence, for the Cape Town high-density cohort.

It is not a claim of physical commissioning date, month-level accuracy, or a
Cape Town-wide installation-year distribution.

## 2. Why a separate CT plan is required

RUN 3 demonstrated that the clean geometry rebuild and offline execution
materially improved the result, but it also exposed failure modes that a
top-level “all anchors have terminal states” check misses:

1. The final RUN 3 corpus still contained 102 nested `gemini_failed`
   observations across 42 anchors, despite the top-level cohort being
   terminal.
2. Terminal error states are skipped by a plain resume; recovery requires a
   filtered anchor manifest plus `--force-restart`.
3. A stale authenticated preflight can survive a scheduled restart.
4. A valid raster can still be black/white, corrupt, or otherwise
   decision-critical unusable.
5. A Wayback-only anchor can be mistaken for an empty TM catalog.
6. Requested zoom is not proof of achieved zoom.
7. The pre-decision CT registry value (`2025-06-30`) was a mid-year fallback,
   while
   the relevant municipal census layer is a January 2025 mosaic. Using the
   fallback would admit February–June imagery as false pre-census evidence.

The CT plan therefore makes nested observation audit, fresh canaries,
quality-gated chips, and conservative censoring release blockers.

Primary evidence: [RUN 3 completion record](RUN-fullscan-gemini-backdating-run3-2026-07-18.md),
[RUN 3 native observation manifest](../dinov3_scorer/PRD-run3-native-local-line-2026-07-19.md),
and the [RUN 3 repeatability ceiling](../dinov3_scorer/DATA-run3native-repeat-ceiling-2026-07-19.md).

## 3. Scope and frozen inputs

### 3.1 Source inventory

Primary source:

```text
/home/gao/projects/ZAsolar/results/analysis/ct_census_output_table/
  ct_full_inventory_2026-06-21_merged.csv
  ct_full_inventory_2026-06-21_merged.gpkg
```

The source README records 111,801 de-duplicated installations across 1,301
non-empty grids. The planning hashes are:

| Artifact | SHA256 |
|---|---|
| `ct_full_inventory_2026-06-21_merged.csv` | `778bfff55782e3f11e22cb0a72559c280f42b8785e4ee341037851a79d8c5f03` |
| `ct_full_inventory_2026-06-21_merged.gpkg` | `1f046ecad16e2d029a4a9c384e5c5ca2454e38894ef627fbbc18b11718a95aec` |

The selection key is `source_grid`, and the anchor join key is
`source_feature_id`. Selection must be frozen before availability, download,
or scoring results are inspected.

### 3.2 Grid roster

The 50th-count threshold is 342 objects. The tied grid IDs are
`CPT1855`, `CPT3409`, and `CPT3677`; the frozen scope includes all three.
The planning snapshot of the frozen roster is:

```text
CPT1855 CPT1956 CPT1971 CPT2011 CPT2124 CPT2174 CPT2176 CPT2177 CPT2178
CPT2283 CPT2312 CPT2366 CPT2367 CPT2369 CPT2540 CPT2541 CPT2596 CPT2597
CPT2653 CPT2654 CPT2656 CPT2713 CPT2714 CPT2761 CPT2818 CPT2877 CPT2932
CPT3188 CPT3189 CPT3292 CPT3295 CPT3351 CPT3352 CPT3408 CPT3409 CPT3412
CPT3469 CPT3473 CPT3529 CPT3584 CPT3585 CPT3636 CPT3637 CPT3641 CPT3677
CPT3694 CPT3696 CPT3697 CPT3733 CPT3750 CPT3790 CPT3791
```

The exact roster was emitted as `ct_top52_manifest_v1.csv` under
`~/zasolar_data/geid_temporal/cape_town_top52_backdating_v1_20260724/manifests/`.
Its file SHA256 is
`edfd79056938c25fcc26be4cfef1549f03844fca8a8d8278c27ed3503eb1ea82`;
the identity-roster SHA256 is
`08329c2531b3022cc96ed4914463174888a773680a3d863c8dc93630dd37bf69`.
Canonical-grid restoration did not change the 52-grid membership or counts.

The canonical `task_grid_cpt.gpkg` referenced by the main registry was rebuilt
on 2026-07-24 from the Dropbox RA1/RA2 source KMLs plus the frozen full-city WMS
coverage probe. It contains 2,083 CPT cells and carries the digit-preserving
legacy G-ID back-reference. CT-01 still requires inventory join and
containment/halo checks before its full gate may pass.

The inventory `source_grid` identity remains the provenance key even when a
96 m source box crosses a grid boundary. A neighboring grid may be included
as a spatial halo for catalog/download completeness; it must not add anchors
to the cohort or rewrite the anchor's source grid.

### 3.3 Census cutoff

Frozen production cutoff: **`2025-01-31`**, with
`capture_precision=month`.

The [City of Cape Town 2025 January imagery service](https://cityimg.capetown.gov.za/erdas-iws/esri/GeoSpatial%20Datasets/rest/services/Aerial%20Imagery_Aerial%20Imagery%202025Jan/MapServer)
identifies the reference layer as “2025 January orthorectified colour
mosaic.” The repository's [timenode report](../../scripts/validation/build_timenode_heatmap_report.py)
also treats the CT census node as 2025-01-31 and identifies the large February
2025 Google vintage as post-census. If exact per-grid flight dates become
available, they may replace this conservative month-end bound, but never
extend it later.

Post-census frames may be retained as `reference_only=true` for visual
context. They must not influence the changepoint, monotonicity, or install
interval.

### 3.4 Region, CRS, and geometry

| Contract | Frozen value |
|---|---|
| Region key | `cape_town` |
| Active grid namespace | `CPT` |
| Metric CRS | `EPSG:32734` |
| Source box | 96 m square, target-centered |
| Review arms | `fullscan_target96_review24_v2` and `fullscan_target96_review48_v2` |
| Arm rule | A24 for `<40 m²`; A48 for `>=40 m²` |
| Offset | recomputed per target and asserted near zero |

The frozen-manifest audit flags one footprint wider than the nominal source
window: source feature `107161` in `CPT3791` is 101.28 m wide. It is explicitly
marked `source_footprint_exceeds_96m` and must enter an exception lane (larger
source box or Codex visual review); it must not be silently clipped.

### 3.5 Imagery provider and zoom

GEHistoricalImagery (GEHI) remains the sole imagery download provider.

1. Probe target-level, bbox-complete availability at **z19**.
2. Use **z18** only as the whole-picture fallback when z19 is incomplete.
3. Treat z20/z21 as optional review upgrades only after complete same-vintage
   coverage is proven.
4. Preserve TM and Wayback provenance separately, then merge for scoring.
5. Deduplicate by `(anchor_id, capture_date)`, never by internal version.
6. Freeze raw availability logs, candidate CSVs, chip manifests, file hashes,
   and achieved zoom before offline scoring.

The minimum history probe should start at 2009-01-01. If an anchor has no
usable pre-census history, it remains explicitly left-censored/undated rather
than being silently treated as a 2019-start observation.

The production chip download window is narrower and follows the RUN 3
contract: retain captures from **2019-01-01** onward, use the Cape Town census
date **2025-01-31**, and retain at most the first **3** distinct post-census
reference dates per anchor. The 2009-2018 availability catalog remains frozen
as provenance but is not a production download denominator.

### 3.6 Scorer and reproducibility

Gemini remains the production scorer. The current DINO fidelity gate has not
passed, so DINO may be run only as a shadow diagnostic.

The CT run must record both the requested model alias and the resolved model
identity returned by the gateway. A model identity change mid-run is a stop
condition unless a separately approved, like-for-like canary has passed.

The content-addressed verdict store is **on** for the primary run. Independent
repeat scoring uses a fresh store and a distinct run/repetition ID.

### 3.7 Review operator: Codex visual review

Owner decision (2026-08-01): future CT placement, jump-critical, and exception
review is performed by Codex over frozen local chip/strip artifacts. The full
blindness, verdict, repeat-pass, provenance, and claim rules are in the
[Codex visual review protocol](CODEX_VISUAL_REVIEW_PROTOCOL.md). This replaces
future human annotation; historical human-review records remain historical
evidence and are not rewritten.

Codex review is an external AI review channel relative to Gemini, not an
independent human gold standard. The release language therefore uses
`Codex-reviewed QA` and `Codex review agreement`; it does not turn those
metrics into physical install-date accuracy or human inter-annotator claims.
The owner retains the final release go/no-go decision.

## 4. Product semantics and confidence tiers

Every anchor remains in the output, including `undated`, `left_censored`,
`census_bound`, `no_recent`, and `needs_review` cases.

The primary interval fields are:

```text
latest_absent
earliest_present
install_interval_start
install_interval_end
install_year_low
install_year_high
coverage_class
confidence_tier
review_required
```

Suggested evidence tiers:

| Tier | Meaning |
|---|---|
| A — repeat-confirmed | Correct placement, valid/decode-clean frames, no operational failures, endpoint repeat agrees, and no unresolved non-monotonic ambiguity |
| B — bounded | Valid evidence but wide interval, z18 fallback, census/left censoring, or only one usable endpoint |
| C — review | Endpoint disagreement, dip repair, non-monotonic sequence, low-quality frame, model/schema retry, or unresolved placement question |

The model's self-reported confidence is not the release confidence. Release
confidence is derived from evidence quality, censoring, repeat agreement,
achieved zoom, and adjudication outcome.

## 5. End-to-end workflow

```text
frozen CT roster + source hashes
    → canonical CT grid + anchor/geometry manifests
    → target-level GEHI availability (TM + Wayback)
    → z19 download / z18 fallback + decode/pixel QA
    → frozen offline candidate/chip manifests
    → Gemini adaptive scan with verdict store
    → recursive frame/error audit + filtered force-retry
    → interval inference with 2025-01-31 ceiling
    → CT-safe CSV/GPKG deliverable
    → stratified blind Codex QA and release decision
```

## 6. Stage plan and gates

The task IDs below are maintained in the
[execution tracker](RUN-cape-town-backdating-tracker-2026-07-24.md).

### Stage 0 — Scope, inputs, and run lock (`CT-00`–`CT-04`)

**Entry:** this plan accepted for execution.  
**Work:** confirm the tie-band roster, restore/hash the canonical grid, fix
the cutoff contract, implement CT-specific builders/CRS/allowlists, and
create an immutable run lock.  
**Exit:** no unresolved P0 blocker; exact roster, input hashes, cutoff,
geometry version, code snapshot, GEHI binary, and scorer contract are
recorded.

### Stage 1 — Availability and chip readiness (`CT-05`)

**Entry:** Stage 0 exit.  
**Work:** target-level TM/Wayback availability, z19/z18 fallback, date
deduplication, candidate construction, downloads, decode/pixel QA, and
on-disk coverage accounting.  
**Exit:** every anchor has an explicit catalog/download outcome; every
chip consumed by the scorer is present, hash-stable, and quality-checked; no
Wayback-only anchor is lost due to an empty TM row.

### Stage 2 — Geometry and end-to-end smoke (`CT-06`–`CT-07`)

**Entry:** Stage 1 has a usable chip subset.  
**Work:** 120-anchor Codex placement review and a 60-anchor end-to-end smoke
(10 per selected canary grid). Each launch/resume performs a fresh
model-specific canary.  
**Exit:** target placement and schema invariants pass; no nested operational
failure is accepted; model output schema and provenance are inspectable.

### Stage 3 — Full-grid canary (`CT-08`)

**Entry:** Stage 2 exit.  
**Work:** complete six-grid canary (approximately 2,700 anchors):
`CPT2932`, `CPT2597`, `CPT3790`, `CPT3677`, `CPT2124`, and `CPT2713`.  
**Exit:** catalog, download, scorer, resume, retry, interval, and deliverable
gates pass on a realistic mixed cohort. Any systematic issue stops expansion.

### Stage 4 — Remaining-cohort production (`CT-09`–`CT-10`)

**Entry:** Stage 3 owner go decision.  
**Work:** run the remaining 46 grids offline, with conservative rate ramp,
quota breaker, nested state audit, and filtered `--force-restart` recovery.  
**Exit:** all anchors have a terminal scientific state and zero unresolved
operational evidence; first-run and retry provenance remain immutable.

### Stage 5 — QA, deliverable, and release (`CT-11`–`CT-12`)

**Entry:** Stage 4 exit.  
**Work:** build intervals and CT-safe CSV/GPKG; Codex blind-review 520
jump-critical anchor strips (at least five per grid, with risk-stratified
remainder and a 20% second Codex pass); calculate weighted bracket-agreement
metrics and Wilson 95% intervals.  
**Exit:** all hard release gates pass and the owner approves a cohort-scoped
release. Otherwise publish only an internal diagnostic artifact.

## 7. RUN 3 error controls

| Failure class | Required CT defense |
|---|---|
| Stale target offsets / changed source grids | Clean per-target derivation, explicit geometry version, zero-offset invariant, no legacy state mixing |
| Nested `gemini_failed` hidden by terminal anchor state | Recursively scan every round/result; release requires zero unresolved failed frames |
| Plain resume skips terminal failures | Generate affected-anchor retry manifest and use `--force-restart`; rerun interval inference |
| Stale preflight after quota interruption | Fresh model-specific canary on every initial launch and resume |
| 403/timeout false-empty catalogs | Raw logs, retry/backoff, explicit catalog status, TM/Wayback union |
| Wayback-only loader path | Unit/integration test with zero TM rows and positive Wayback rows |
| Decoder-only corruption detection | Decode plus pixel statistics/black-white/variance gate before scoring |
| Requested versus achieved zoom mismatch | Persist achieved zoom/GSD; stratify and downgrade rather than silently substitute |
| Post-census contamination | Apply the 2025-01-31 cutoff before planning; post-census frames are reference-only |
| Inverted intervals / dip repair | Inverted interval is a hard failure; dip-repair and single-frame early presence enter the Codex review tier |
| Model quota exhaustion or alias drift | Shared limiter, breaker, explicit resolved model, no silent fallback |

## 8. Data contracts

### 8.1 Frozen manifest

Required fields include:

```text
anchor_id
source_feature_id
source_grid
region_key
centroid_lon
centroid_lat
metric_crs
source_width_m
source_height_m
chip_arm
review_extent_m
geometry_version
input_inventory_sha256
roster_sha256
```

Build-time invariants:

- anchor count equals the frozen roster inventory count (21,453 for the
  frozen tie-inclusive scope);
- `source_feature_id` and `anchor_id` are unique and bijective;
- target centroid and source-box center agree within tolerance;
- source box is square and 96 m unless an explicit exception is recorded;
- offsets are recomputed and near zero;
- source grid is non-empty and includes the centroid grid;
- no unknown/retired grid namespace.

### 8.2 Catalog and chip manifest

Each `(anchor_id, capture_date)` row records provider, date, query result,
requested zoom, achieved zoom, bbox completeness, local path, hash, decode
status, pixel-quality status, and `reference_only`.

An empty query, a missing local file, a decoder failure, and an uninformative
image are distinct states. None may be rewritten as `absent`.

### 8.3 Scan and interval outputs

The scan state must persist model/prompt/provenance, geometry version,
census cutoff, catalog/chip references, retry lineage, every frame-level
decision source, and terminal status. A release audit reads all nested
results rather than relying on process exit code or a `.complete` sentinel.

## 9. Release gates

Release is allowed only when all gates below pass:

1. **Roster gate:** exact frozen grid roster, source hashes, and 21,453-row
   anchor bijection.
2. **Geometry gate:** EPSG:32734, clean geometry version, zero stale offsets,
   no silent clipping or grid reassignment.
3. **Census gate:** no inference evidence later than 2025-01-31; reference-only
   post-census rows are separated.
4. **Imagery gate:** every consumed chip is on disk, hash-stable,
   bbox-complete at z19 or explicit z18 fallback, decode/pixel QA passed.
5. **Scoring gate:** zero unresolved nested `gemini_failed`, orchestrator,
   schema, or truncation failures; fresh canary evidence exists for every
   restart.
6. **Inference gate:** zero inverted intervals, zero load failures, exact row
   count, and all censoring/status invariants pass.
7. **QA gate:** 520-anchor blind Codex adjudication completed, with a 20%
   second Codex pass, weighted bracket-agreement metrics, and Wilson 95%
   intervals reported.
8. **Claim gate:** deliverable and README state the cohort scope and
   first-visible-appearance semantics; no city-wide or physical-install-date
   claim is made.

The acceptable accuracy lower bound is a product decision and must be
pre-registered before reading the CT QA results. The plan does not infer a
city-wide error rate from the non-random RUN 3 convenience sample.

Diagnostic rows with an unresolved operational failure may be retained for
forensics, but they are not release-eligible and cannot be silently counted
as absence. A full production release requires the operational-failure
denominator to be zero after the filtered retry process.

## 10. Resource envelope

Using RUN 3 as a throughput reference, the proposed cohort is roughly
45,000 Gemini batch calls and 160,000 observations before targeted repeats
and retries, or about five hours at the RUN 3 8-QPS pace. This is only a
planning estimate: CT availability density, achieved zoom, endpoint repeats,
and download volume must be measured in Stage 1. RUN 3's 80-worker/8-QPS
setting is not automatically inherited.

## 11. Risks, stop conditions, and rollback

Stop the current wave if any of the following occurs:

- canonical grid or cutoff cannot be verified;
- a systematic placement/CRS/geometry mismatch appears;
- quota or model identity changes without a passing canary;
- unresolved nested failures persist after the retry budget;
- post-census evidence enters the inference set;
- corrupt/low-quality frames are being scored;
- interval or ID invariants fail.

Rollback means stopping the wave and preserving its immutable run root,
catalog, chips, logs, and scan states. It does not mean deleting or
overwriting a prior run. Recovery uses an explicit filtered manifest and a
new run/retry ID.

## 12. Decision log

| Date | Decision | State |
|---|---|---|
| 2026-07-24 | Use the complete 52-grid tie band; reject arbitrary strict top-50 cut | **Confirmed by owner** |
| 2026-07-24 | Use 2025-01-31 conservative CT census ceiling | **Confirmed by owner; registry and execution-path tests updated** |
| 2026-07-24 | Rebuild the missing canonical CT grid from Dropbox RA1/RA2 KML sources and the frozen full-city coverage probe | **Confirmed and rebuilt; CT-01 downstream join/containment checks remain** |
| 2026-07-24 | Keep Gemini as production scorer; DINO shadow only | Carried from current fidelity gate |
| 2026-07-24 | Require recursive frame audit and fresh canary on resume | Proposed hard release policy |

## 13. Execution log

Record each wave, command/run root, code and input hashes, operator, start/end
time, gate result, and any retry or owner decision in the companion tracker.
