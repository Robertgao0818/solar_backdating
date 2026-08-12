# Cape Town Backdating v1 — execution tracker

Source plan:
[`RUN-cape-town-backdating-plan-2026-07-24.md`](RUN-cape-town-backdating-plan-2026-07-24.md)

Status vocabulary: `ready-for-agent` (including Codex review) · `in-progress` ·
`done` · `wontfix`  
Gate vocabulary: `OPEN` · `BLOCKED` · `PASS` · `STOP`  
Last updated: 2026-08-03

This tracker is the operational source for the CT run. Method decisions belong
in the PRD; this file records task state, dependencies, evidence, and gates.
Do not mark a task `done` without a reproducible artifact or test result.

## Goal

Produce a reproducible, conservative first-visible-appearance interval
deliverable for the frozen 52-grid / 21,453-anchor high-density CT cohort,
with zero silently absorbed operational failures.

## Execution waves

```text
W0: CT-00
W1: CT-01  CT-02  CT-03  CT-04
W2: CT-05
W3: CT-06  CT-07
W4: CT-08
W5: CT-09  CT-10
W6: CT-11  CT-12
```

The waves describe dependency order, not a promise to run every task in
parallel. API quota, storage, Codex-review capacity, and owner go/no-go remain
explicit gates.

## Task table

| ID | Stage | Status | Gate | Depends on | Deliverable / evidence | Acceptance summary | Updated |
|---|---|---|---|---|---|---|---|
| CT-00 | Scope/input freeze | done | PASS | — | `ct_top52_manifest_v1.csv`, selection memo, source hashes | Owner-confirmed 52-grid tie-band scope, source inventory and no outcome-dependent substitution | 2026-07-24 |
| CT-01 | Canonical grid | done | PASS | CT-00 | rebuilt `task_grid_cpt.gpkg`, hash, 2,083-cell validation, geometry audit | Grid geometry, CPT namespace, crosswalk, centroid/containment/96 m halo checks pass; source-grid provenance is retained for explicit cross-boundary cases | 2026-07-24 |
| CT-02 | Census contract | done | PASS | CT-00 | cutoff config/test evidence, `2025-01-31` policy memo | No inference path can consume post-2025-01-31 frames; reference-only semantics tested | 2026-07-24 |
| CT-03 | CT builders/CRS | done | PASS | CT-00, CT-01, CT-02 | CT anchors/groups/builder and CT-safe deliverable changes | EPSG:32734, allowlist, zero offsets, geometry version, no JHB expected counts/paths | 2026-07-24 |
| CT-04 | Run lock/runtime | done | PASS | CT-00 | `RUN_LOCK.json`, code/data/GEHI/model hashes, fresh-canary script | Immutable run root and model-specific canary are required on every launch/resume | 2026-07-24 |
| CT-05 | Availability/catalog/chips | done | PASS | CT-01, CT-02, CT-03, CT-04 | raw TM/Wayback logs, candidates, chip manifest, decode/pixel QA report | Target-level z19 primary + z18 fallback; date dedup; every anchor has explicit outcome; Wayback-only path works | 2026-08-03 |
| CT-06 | Placement/geometry QA | done | PASS | CT-03, CT-05 | 120-anchor Codex placement sheet, verdict sidecar, and geometry summary | No systematic target misplacement; large-footprint and no-history exceptions explicitly routed; no MISALIGNED rows | 2026-08-01 |
| CT-07 | E2E smoke | done | PASS | CT-04, CT-05, CT-06 | paired 60-anchor smoke/control manifests and recursive state audit | Auth/model/schema/provenance path passes; nested operational failures = 0 | 2026-08-02 |
| CT-08 | Six-grid canary | done | PASS | CT-05, CT-06, CT-07 | `production_lite_v2_20260731/canary/gate_retry2/` plus fresh Lite canaries | All 2,700 anchors are scientifically/operationally auditable; exact Lite identity is stable. The original window budget STOP was superseded only after a new quota window/lock and fresh canary. | 2026-08-02 |
| CT-09 | Remaining production | done | PASS | CT-08 | immutable full-run root, six frozen production waves | Waves 1-6 complete; 21,453 anchor states are covered after adding the frozen canary and retry2 lineage. | 2026-08-03 |
| CT-10 | Retry/error reconciliation | done | PASS with explicit legacy sidecar | CT-09 | recursive multi-scope audit plus legacy audit-gap sidecar | Final scoring audit: 21,453 covered, terminal, 0 operational failures, 0 reference leaks, exact Lite identity. Historical ledger-only rows remain explicitly listed in `legacy_ledger_audit_gap_sidecar_20260803.json`; they are not fabricated audit rows. | 2026-08-03 |
| CT-11 | Blind Codex QA/reference set | in-progress | HOLD — remediation development only | CT-09, CT-10 | Corrected Pass-1/repeat, failed-holdout development evidence, and a sealed disjoint 500+100 confirmation holdout | Corrected holdout failed four immutable gates. A new disjoint 500+100 holdout is now hash-sealed but remains unrendered/unopened; remediation must be locked using only the old failed 500 before it can be opened once. | 2026-08-04 |
| CT-12 | Deliverable/release | in-progress | HOLD CT-11 / owner | CT-01, CT-02, CT-03, CT-09, CT-10, CT-11 | CT CSV/GPKG, README, release manifest, six/eight gate report | Structural candidate gates PASS, but corrected CT-11 acceptance failed. Do not publish or start downstream full-Cape-Town imagery download before remediation on a new holdout and owner go/no-go. | 2026-08-03 |

## Hard blockers

These are blockers for paid or production-scale work, not optional cleanup:

- **B1/B2 — resolved 2026-07-24:** the canonical grid is rebuilt and
  hash-locked, the frozen cohort joins it, all 96 m boxes include both the
  provenance and centroid cells, and the CT cutoff/reference-only tests pass.
- **B3 — JHB coupling:** CT-specific builder, launcher, candidate cutoff,
  and deliverable must be parameterized and tested.
- **B4 — target-level catalog absent:** no current CT catalog may be assumed
  from JHB cache or centroid-only probe artifacts.
- **B5 — dirty/unlocked runtime:** production commands require a code snapshot
  and `RUN_LOCK.json`.

## Task-level acceptance checklists

### CT-00 — Scope/input freeze

- [x] Tie-inclusive 52-grid roster is owner-approved; strict top-50 is rejected.
- [x] Inventory CSV/GPKG hashes and source layer are recorded.
- [x] Roster is frozen before any GEHI/scorer outcome is inspected.
- [x] Reserve grids, if any, are listed separately and cannot enter the core
      denominator silently.

### CT-01 — Canonical grid

- [x] `task_grid_cpt.gpkg` is present and hash-locked
      (`sha256:81d10557cdecf691e4454dd0d0d03c55debcef68444f6536461b138c681694b8`).
- [x] 2,083 CPT cells and active namespace checks pass.
- [x] Inventory `source_grid` joins and 96 m bbox containment/halo checks pass.
      The 111,801-row inventory has zero missing grid joins; all 21,453 frozen
      target boxes include their provenance and centroid cells. 3,518 boxes
      cross a cell boundary and are represented by explicit halo IDs.
- [x] Cross-boundary anchors retain their source-grid identity; 501
      source/centroid mismatches remain explicit in the manifest and audit.

### CT-02 — Census contract

- [x] `2025-01-31` is resolved from the CT registry into candidate
      construction, scan provenance, and inference.
- [x] Unit tests prove post-census frames are reference-only.
- [x] No JHB default cutoff or `2025-06-30` fallback remains on the CT path.

### CT-03 — CT builders/CRS

- [x] CT anchors have EPSG:32734 and explicit geometry version.
- [x] Anchor allowlist rejects unknown/stale columns (strict manifest schema).
- [x] Offset, square-box, arm split, source-grid, and ID invariants pass for
      the anchor and one-target group packages.
- [x] CT deliverable builder has no JHB paths, CRS, counts, or transformers;
      synthetic end-to-end tests enforce the CT join, EPSG:32734 output,
      complete-row reconciliation, and `2025-01-31` ceiling.

### CT-04 — Run lock/runtime

- [x] Code commit/diff, input/grid/roster hashes, GEHI binary, model,
      prompt/schema, and rate settings are captured.
- [x] Initial launch canary passed for all three model tiers; the canary script
      is required again before every resume and fails on returned identity drift.
- [x] Primary verdict store is configured on; repeat store IDs must use distinct
      paths and repetition IDs.

### CT-05 — Availability/catalog/chips

- [ ] TM and Wayback raw logs are retained and independently attributable.
- [ ] z19 bbox-complete is primary; z18 fallback is explicit.
- [ ] Deduplication key is `(anchor_id, capture_date)`.
- [ ] Every consumed chip passes existence, hash, decode, and pixel-quality
      checks.
- [ ] A Wayback-only fixture passes without a TM row.

### CT-06 — Placement/geometry QA

- [x] 120 anchors cover area, confidence, spatial, boundary, and arm strata.
- [x] Codex placement review finds no systematic target misplacement.
- [x] Large footprints, no-history rows, and grid-edge cases have explicit
      dispositions; no exception is silently treated as absence.

### CT-07 — E2E smoke

- [ ] 60 anchors (10 per canary grid) complete the full offline path.
- [ ] Output schema, provenance, interval, and retry lineage are inspectable.
- [ ] Recursive frame audit finds zero unresolved operational failures.

### CT-08 — Six-grid canary

- [ ] All six named grids complete, including `CPT3677`.
- [ ] Rolling quota/latency/error behavior is recorded.
- [ ] No systematic coverage, geometry, model, or interval issue is open.
- [ ] Owner records go/no-go decision before CT-09.

### CT-09 — Remaining production

- [ ] Remaining 46 grids use the frozen roster; no silent substitutions.
- [ ] Scan is offline after catalogs/chips are frozen.
- [ ] All anchors receive an explicit scientific/coverage state.

### CT-10 — Retry/error reconciliation

- [ ] Every state and nested result is scanned, independent of exit code.
- [ ] Failed anchors are retried from a filtered manifest with
      `--force-restart`.
- [ ] Retry results are linked to first-run provenance; no prior state is
      overwritten.
- [ ] Unresolved operational failures are excluded from release rather than
      converted to absence.

### CT-11 — Blind QA/gold set

- [x] 500 jump-critical anchor strips are sampled with per-grid minimum and
      risk stratification (the corrected frozen CT design; 52/52 grids,
      minimum 5 per grid).
- [x] At least 20% receive a fresh, blind second Codex pass with a new
      `review_run_id`; report `codex_repeat_agreement`, not inter-annotator
      agreement.
- [x] CONFIRM/SHIFT/UNDATABLE, bracket-agreement rate, weighted estimates, and
      Wilson 95% intervals are reported.
- [ ] Corrected review package preserves native detail and includes both exact
      production boundary frames for every dated bracket.
- [ ] Corrected independent pass and 20% repeat are complete; class-specific
      repeat stability is reported and instrument-validity gates pass.
- [ ] Product acceptance lower bound was preregistered before QA results.
- [x] A new 500-anchor confirmation holdout plus 100-anchor repeat was frozen
      disjoint from the failed 500, with 52/52 grids and minimum 5 per grid.
- [ ] Remediation is selected and locked using only the old failed holdout;
      the new holdout remains unrendered and unopened until then.

### CT-12 — Deliverable/release

- [ ] Row count and ID bijection equal the frozen 21,453-anchor manifest.
- [ ] Zero post-cutoff inference evidence and zero inverted intervals.
- [ ] Nested scoring/download/decoder failures are zero or explicitly
      represented as non-release coverage classes.
- [ ] CSV/GPKG centroid/CRS and provenance gates pass.
- [ ] README says “high-density 52-grid cohort” and
      “first-visible-appearance interval.”

## Release gate summary

| Gate | Required result | Evidence |
|---|---|---|
| R1 roster | exact frozen roster and hashes | manifest + `RUN_LOCK.json` |
| R2 geometry | EPSG:32734, zero stale offsets, no silent clipping | anchor summary + placement sheet |
| R3 census | no inference evidence after 2025-01-31 | candidate/scan audit |
| R4 imagery | consumed chips are present, stable, decoded, quality-checked | chip QA report |
| R5 scoring | no unresolved nested operational failures; fresh canaries | recursive state audit |
| R6 inference | zero inverted/load failures; exact rows | interval + deliverable gates |
| R7 Codex QA | Frozen CT design: 500 Codex adjudications, 20% second blind pass, weighted estimates and Wilson CIs | Executed and reported; release result HOLD because the acceptance lower bound was not preregistered and exact-bracket agreement was 15.404% |
| R8 claim | cohort-only, first-visible semantics | release README |

## Evidence and execution log

Use one row per wave:

| Date/time | Task/wave | Run root | Code/input/model hashes | Gate result | Operator note |
|---|---|---|---|---|---|
| 2026-07-24 | CT-00 decisions | — | — | owner decisions confirmed | Frozen 52-grid tie band and `2025-01-31` cutoff |
| 2026-07-24 | CT-00 freeze | `~/zasolar_data/geid_temporal/cape_town_top52_backdating_v1_20260724/manifests/` | manifest `edfd7905…`; identity roster `08329c25…`; source hashes in `artifacts.sha256` | PASS | 52 grids / 21,453 anchors; A24=19,729, A48=1,724; no reserve grids; 501 source-grid/centroid-grid mismatches preserved with both IDs; one >96 m footprint explicitly routed |
| 2026-07-24 | CT-01 rebuild | `/home/gao/projects/ZAsolar/data/` | RA1 KML `0061e8cd…`; RA2 KML `69024fd…`; full grid `d1c9c3d5…`; CPT grid `81d10557…`; crosswalk `b29f4d16…` | partial pass | 4,429 unique source G cells → 2,083 kept CPT / 2,346 dropped; 119/119 anchors retained; rebuilt crosswalk byte-identical to canonical CSV |
| 2026-07-24 | CT-01 inventory join | — | inventory GPKG `1f046eca…`; CPT grid `81d10557…` | PASS | 111,801/111,801 `source_grid` values join; target-centered 96 m bbox/halo audit passed separately |
| 2026-07-24 | CT-01 geometry audit | `~/zasolar_data/geid_temporal/cape_town_top52_backdating_v1_20260724/geometry_audit/` | audit `3325efb0…`; summary `14ae7e2f…` | PASS | 21,453/21,453 boxes contain centroid and provenance cells; 3,518 explicit halo boxes (2-cell=3,364; 4-cell=154); 501 source/centroid provenance mismatches retained explicitly |
| 2026-07-24 | CT-02 cutoff | — | main registry + current code | PASS | CT/reference-only focused suite: 95 passed, 1 skipped; shared temporal smoke: 1,056 passed, 6 subtests passed |
| 2026-07-24 | CT-03 anchors | `~/zasolar_data/geid_temporal/cape_town_top52_backdating_v1_20260724/anchors_v1/` | builder `scripts/temporal/build_ct_anchors_v1.py`; anchors `3050f9b8…`; summary `fffb2764…` | PASS | 21,453 rows, unique IDs, EPSG:32734, A24/A48 counts, zero offsets, arm-matched geometry versions |
| 2026-07-24 | CT-03 groups/deliverable builder | `~/zasolar_data/geid_temporal/cape_town_top52_backdating_v1_20260724/groups_v1/` | groups `48682537…`; targets `c3160a35…`; builders `build_ct_chip_groups_v1.py`, `build_ct_install_dated_deliverable.py` | PASS | One frozen target per 96 m chip, `anchor_id == chip_id`, zero offsets; 23 group/deliverable tests pass, including CT CRS/join/cutoff gates. Formal deliverable waits for production intervals (CT-12). |
| 2026-07-24 | CT-04 pre-canary lock (superseded) | `~/zasolar_data/geid_temporal/cape_town_top52_backdating_v1_20260724/runtime_lock_v1/` | RUN_LOCK `69a4698f…`; code snapshot `d25173db…`; GEHI `f8ead960…` / `0.5.1+5abe191…` | SUPERSEDED | Earlier pre-canary lock had the two aliases reversed; do not launch from it. The corrected contract is routine `gemini-3.1-flash-lite`, recovery `gemini-3-flash`; regenerate a fresh lock before canary. |
| 2026-07-24 | CT-04 corrected pre-canary lock | `~/zasolar_data/geid_temporal/cape_town_top52_backdating_v1_20260724/runtime_lock_v2/` | RUN_LOCK `c09037d1…`; corrected source/prompt/runtime hashes | OPEN | Corrected aliases recorded: routine `gemini-3.1-flash-lite`, recovery `gemini-3-flash`; lock remains `awaiting_fresh_model_canary` until gateway-returned identities are verified. |
| 2026-07-24 | CT-04 three-tier runtime lock/canary | `~/zasolar_data/geid_temporal/cape_town_top52_backdating_v1_20260724/runtime_lock_v3/` | final RUN_LOCK `7edefb73…`; code snapshot `22a810d2…`; GEHI `f8ead960…`; canary `max_tokens=4096` | PASS | Resolved identities: routine `gemini-3.1-flash-lite`; recovery `gemini-3-flash`; troubleshooting alias `gemini-3.6-flash-high` resolved to `gemini-3.6-flash`. Third tier is limited to failed recovery, Gemini/schema failure evidence, non-monotonic conflict, or review-required anchors. |
| 2026-07-26 | CT-05 route plan + live smoke | `~/zasolar_data/geid_temporal/cape_town_top52_backdating_v1_20260724/ct05_catalog_v1/` | route plan `ct05_catalog_v1/plan/route_plan.json`; four shards `home_v4/home_v6/koko_v4/koko_v6` (5,333/5,302/5,503/5,315); probe `227be068…`; shared GEHI common `69a2efb1…`; GEHI `f8ead960…` | OPEN / in-progress | Four-route target-level probe launched after 16/16 smoke queries passed. TM uses bbox-complete z19/z18; Wayback info uses z19/z18 and preserves date labels by `(anchor_id,capture_date)`. Full catalog/chip QA and merge remain pending. |
| 2026-07-26 | CT-05 full-scale lane expansion | same CT-05 root, `remaining_plan/` + `routes_resumed/` | 674 fully completed anchors preserved; remaining 20,779 anchors split across eight stable resume shards; resume probe `5f210245…` | OPEN / in-progress | Owner approved direct full-scale execution. Home and koko each run v4×2 + v6×2 lanes. Each lane is paced at 2 s, keeping the theoretical per-address-family aggregate at 1 request/s. Initial expanded-lane audit: 117/117 queries `ok_nonempty`, zero blocked/error. |
| 2026-07-27 | CT-05 full chip download launch (superseded denominator) | `~/zasolar_data/geid_temporal/cape_town_top52_backdating_v1_20260724/ct05_download_v1/` | reconciled catalog 2,649,905 candidates / 21,453 anchors / zero operational failures; old download plan `94c606ee…`; lane runner `c4915e05…` | SUPERSEDED | The initial plan incorrectly promoted the full 2009+ probe catalog into the download denominator. Four routes were stopped on 2026-07-28; their plan and lane evidence were retained as `plan_2009_superseded/` and `lanes_2009_superseded/`. Existing chips were not deleted and may be reused only when admitted by the corrected candidate contract. |
| 2026-07-28 | CT-05 RUN3-window chip download relaunch | same root, corrected `plan/` + fresh `lanes/` | source catalog 2,649,905; explicit `min_capture_date=2019-01-01`; corrected candidates 1,178,227; filtered artifact `2fcf66f5…`; plan `d37bebba…`; CT census `2025-01-31`; max post-census dates=3 | OPEN / in-progress | Owner clarified strict RUN3 download semantics. The 2009+ availability probe remains frozen, but production downloads now admit only 2019+ dates and at most 3 dates after the Cape Town census. All four home/koko v4/v6 routes relaunched; prior compliant chips are reused through skip-existing. The cutoff is now an explicit planner default/CLI field with a regression test (6 chip-pipeline tests pass). |
| 2026-07-31 | CT-06/CT-07 paired pilot | `production_lite_v2_20260731/pilot_retry_v2/` | Lite identity stable; control 159 calls / optimized 151 calls; both operationally clean | STOP / fallback control | Optimized 6-slot reduction was 5.03%, below the preregistered 25% gate; primary remains control 5-slot, not a silent 6-slot switch. |
| 2026-08-01 | CT-06 Codex placement pass1 | `~/zasolar_data/geid_temporal/cape_town_top52_backdating_v1_20260724/codex_review_20260801/ct06_placement_pass1_final/` | placement `f9b510a1…`; package `1226bcbd…`; verdict `2701e0c4…`; renderer `40158c51…`; protocol `7bc950e4…` | PASS / explicit exceptions | 120 assignments across A24/A48; 10 CRS-aware sheets; 113 PASS, 1 EXCEPTION (`t00107162` large footprint), 6 UNREVIEWABLE (no-history), 0 MISALIGNED. Wayback-only `t00038314` passed after EPSG:3857-aware overlay. |
| 2026-07-31 | CT-08 six-grid control canary | `production_lite_v2_20260731/canary/gate_retry2/` | 2,700 anchors; 6,938 HTTP attempts; Lite exact identity; 0 nested operational failures; merged retry 0; reference leaks 0 | STOP_AT_CT08_BUDGET_GATE | Measured 5-slot calls/anchor: A24 2.55723, A48 2.69388. Remaining projection 48,157.84 attempts exceeded the window's remaining work budget 41,638, so production was not started. |
| 2026-07-31 | CT-08 resume attempt / rate-limit incident | `production_lite_v2_20260731/runtime_lock_ct52_lite_window2_20260731/` | window `ct52_lite_20260731T113315Z`; 110 attempts (108×200, 2×429); pause marker written at ledger 71 with 39 in-flight; 1,539 A24 states remained resumable/scanning | STOP / PAUSED_QUOTA | `--qps 6` paced request starts globally. The 39 in-flight count reflected request latency across 40 workers; it is not evidence of a 40-request startup burst. A temporary in-flight cap was tested afterward, but it does not replace strict start-rate pacing. |
| 2026-07-31 | CT-08 amended resume lock (superseded runtime tuning) | `production_lite_v2_20260731/runtime_lock_ct52_lite_window3_20260731/` | window `ct52_lite_20260731T114402Z`; `max_in_flight=4`; `work_budget=48,540`; reset `2026-07-31T16:44:02Z`, grace through `16:49:02Z` | SUPERSEDED | This lock records the temporary concurrency-cap interpretation. The current owner-corrected contract uses strict global QPS pacing from the first request and `max_in_flight=0`. |
| 2026-08-02 | CT-09 owner-corrected rate contract | `production_lite_v2_20260731/runtime_lock_ct52_lite_window5_appendfix_20260802/` | QPS `6`; `max_in_flight=0`; 40 workers; strict no-startup-burst regression suite 45 passed; exact model `gemini-3.1-flash-lite` | PASS | Shared `RateLimiter` releases the first request immediately and every later request at `1/QPS`; 40 workers cannot all enter sub2api at startup. Round audit now appends+flushes so checkpoint resume cannot overwrite earlier chunks. |
| 2026-08-02 | CT-09 production Waves 1-2 reconciliation | `production_lite_v2_20260731/primary/` | 6,667 terminal states; 24,532 visible scorer attempts; exact Lite identity; 0 operational failures/reference leaks | PARTIAL PASS | Wave 2 A24 completed 2,904/2,904 (39,027 observations, z19 99.7%); A48 completed 278/278 (3,727 observations, z19 99.7%). Ledger reconciliation alone fails on 30 successful legacy calls whose audit rows were overwritten by the old write-mode resume behavior; they remain explicit ledger-only gaps, not fabricated scorer audit. |
| 2026-08-02 | CT-09 Wave 3 A24 | `production_lite_v2_20260731/primary/logs_wave_03_A24_window5_appendfix_20260802.log` | 3,136/3,136; 5,265 rounds; 43,951 observations; z19 43,884 (99.8%); exact Lite identity | PASS | Exit 0; zero scorer/cache write failures and no 429 during this arm. Runtime config was QPS 6 from startup and `max_in_flight=0`. |
| 2026-08-02 | CT-09 Wave 3 A48 checkpoint pause | `production_lite_v2_20260731/primary/logs_wave_03_A48_window5_appendfix_20260802.log` | 209 anchors: 138 terminal, 71 `scanning`; 367 attempts (366×200, 1×429); first 60 seconds contained 314 completed HTTP 200 attempts; exact Lite identity on all successes | STOP / PAUSED_QUOTA | The single 429 occurred at `2026-08-02T10:32:10Z`, after startup rather than as an initial worker burst. Circuit breaker stopped new calls fail-closed. Resume is not permitted before `2026-08-02T12:27:55Z` (reset+5-minute grace) and a fresh Lite canary. Current full multi-scope audit: 10,010 states, 9,939 terminal, 71 scanning, 0 operational failures, 32,963 visible scorer attempts with no identity drift; ledger-only gaps are the 30 pre-fix HTTP-200 rows plus this explicit 429. |
| 2026-08-03 | CT-09 Waves 5-6 completion | `production_lite_v2_20260731/primary/` | Wave 5 A24 2,975/2,975; Wave 5 A48 275/275; Wave 6 A24 1,896/1,896; Wave 6 A48 172/172. All completed with strict QPS=6, `max_in_flight=0`, user concurrency=60; no new 429/pause. | PASS | Six frozen production waves are complete; canary and retry2 lineage remain immutable and are included only in reconciliation/inference. |
| 2026-08-03 | CT-10 final scoring reconciliation | `production_lite_v2_20260731/audits/ct_full_reconciliation_final_scoring_user60_20260803.json` | 21,453 expected anchor IDs covered; all terminal; 0 operational failures; 0 reference leaks; 20,390 Lite attempt records with no identity drift. | PASS with explicit legacy sidecar | The quota gate retains historical ledger-only gaps; `legacy_ledger_audit_gap_sidecar_20260803.json` records them without inventing audit rows. |
| 2026-08-03 | CT-12 interval + deliverable build | `production_lite_v2_20260731/intervals/` + `deliverable_20260803_lite_top52/` | 21,453 unique interval rows; post-cutoff=0; builder gates PASS; CSV/GPKG and release manifest emitted. | PASS structural / HOLD QA | Deliverable is a release candidate only until CT-11 Codex blind QA and owner go/no-go are complete. |
| 2026-08-03 | CT-11 blind package freeze | `production_lite_v2_20260731/goldset_ct_20260803/goldset_ct52_20260803_gridmin/` | Corrected freeze: 500 unique anchors, 52/52 grids with minimum 5 anchors, 600 assignment rows, 100 overlap anchors. Rerender now reuses retained production chips before GEHI fallback (21 focused tests PASS): 1,745/1,745 required scan frames recovered; 1,500 missing rows are optional CoJ reference slots because no CoJ root was supplied. Eight real-frame HTML pages and 25 production-blind chronological contact sheets were generated. | OPEN | Codex pass-1, fresh 20% repeat pass, verdict CSV, repeat agreement, and Wilson intervals remain to be executed. |
| 2026-08-03 | CT-11 independent pass + production join | same frozen goldset root | pass-1 `f5e82a9b…`; assignment `1c84ab3a…`; 500 reviewed; 61 CONFIRM / 335 SHIFT / 104 UNDATABLE | QA evidence complete / HOLD release | Exact-bracket agreement is 61/396 = 15.404%; Wilson 95% CI 12.183%–19.290%. Review remained blind until the independent records were frozen. |
| 2026-08-03 | CT-11 fresh blind repeat | same frozen goldset root | repeat `1cbc427e…`; repeat manifest `06fd1830…`; 100/500 anchors | PASS design / HOLD release | Independent state and interval agreement are 72%; formal joined-verdict agreement is 83%. These are Codex repeat-agreement measures, not human inter-annotator agreement. |
| 2026-08-03 | CT-11 weighted metrics/report | same frozen goldset root + `RUN-cape-town-ct11-codex-qa-2026-08-03.md` | metrics `042e934f…`; group table `5167e34d…`; enriched rows `a2df6eb3…`; all frozen `0444` and verified | QA COMPLETE / RELEASE HOLD | Inventory-weighted exact-bracket agreement is 15.500%; headline Wilson half-width 3.554pp passes the 5.5pp precision gate. No CT product acceptance lower bound was preregistered before results, so CT-12 and full-Cape-Town download remain held pending diagnosis/remediation and owner go/no-go. |
| 2026-08-03 | CT-11 instrument audit + native rerender | frozen goldset root, `native_resolution_diagnostic_20260803/` | 500 anchors / 1,745 frames / 167 native sheets; package `0938f741…`; 60/325 `done_appears` missing at least one exact claimed boundary in the original package | INSTRUMENT DEFECT / HOLD | Old 160×126 contact-sheet verdicts are diagnostic, not a valid final acceptance test. Native package exposes no production outcomes or prior verdicts and all hashes verify. |
| 2026-08-03 | CT-11 three-tier same-input pilot | `ct11_lite_replay_pilot_20260803/`, `ct11_recovery_pilot_20260803/`, `ct11_troubleshooting_pilot_20260803/` | 120/120 HTTP 200; zero retry/429; strict QPS=6, max-in-flight=0; exact identities; each root hash-locked | PASS operational / QA conflict confirmed | Codex class agreement: Lite 10/40, recovery 15/40, troubleshooting 13/40. Recovery/troubleshooting agree 35/40; all three agree 27/40; two-of-three majority matches old Codex only 14/39. Corrected native, boundary-complete external review is required before changing production or releasing. |
| 2026-08-03 | CT-11 corrected package freeze | `goldset_ct52_20260803_gridmin_corrected_native_v2/` | 500 Pass-1 / 100 repeat; 1,498 frames; 167+34 native sheets; boundary failures=0; post-cutoff=0; Pass-1 package `bb662d65…`; repeat `28b1eeff…` | READY FOR REVIEW / WAIT PREREG | Frozen assignment boundaries override raw post-dip scan extrema. Public manifests expose no identity or production outcome. Corrected sheets have not been reviewed; owner must confirm the proposed acceptance gates first. |
| 2026-08-03 | CT-11 corrected acceptance lock | `configs/ct11_corrected_acceptance_v1.json` | owner confirmation `2026-08-02T21:01:04Z`; config `770db9c49bc3a85e62a0b776ee596e66c1f443e32d08aaf4ea7e3f0c40ed5f74`; fail-closed validator PASS | REVIEW UNLOCKED | Owner message `确认门槛` received before any corrected sheet was viewed. Thresholds are now immutable for this holdout; Pass-1 must be frozen before the independently ordered repeat is opened. |
| 2026-08-03 | CT-11 corrected blind review + locked evaluation | `ct11_corrected_codex_review_20260803/` + `RUN-cape-town-ct11-corrected-qa-2026-08-03.md` | Pass-1 500/500 `822c79fc…`; repeat 100/100 `36be288d…`; metrics `ccee6364…`; joined rows `9124c6b7…`; all frozen `0444` | QA COMPLETE / RELEASE HOLD | Primary exact bracket 94/453 = 20.751%, Wilson lower 17.270%; standardized exact bracket 20.827%; repeat state 68.000%, Wilson lower 58.337%; `UNDATABLE` 9.400%, boundary failures=0, post-cutoff=0. Four immutable gates fail; no tuning on this holdout, no CT-12 publication, and no full-Cape-Town GEHI start. |
| 2026-08-04 | CT-11 disjoint confirmation holdout freeze | `goldset_ct_20260803/ct11_disjoint_holdout_v1_20260804/` | 500 unique anchors; 100 unique repeat anchors; private manifest `67612e4a…`; repeat `01ddde3d…`; acceptance config `770db9c4…`; all five outputs mode `0444`, hash verification PASS | SEALED / NOT OPENED | Zero overlap with the failed 500; 52/52 grids, realized minimum 5. `HOLDOUT_LOCK.json` records `SEALED_NOT_RENDERED_NOT_REVIEWED`, `sheets_rendered=false`, `sheets_opened=false`, and `remediation_locked=false`. Do not render or inspect this holdout until a method is locked from old-holdout development evidence only. |
| 2026-08-04 | CT-11 old-holdout remediation candidate audit | `ct11_corrected_sequence_pilot_20260803/` + `ct11_reference_anchored_pilot_20260804/` | Recovery sequence: 40×HTTP 200, class 17/40, interval 15/40. Reference-anchored Lite: 40×HTTP 200, class 13/40, interval 12/40. Both strict QPS=6, `max_in_flight=0`; zero 429/retry; artifact hashes PASS. | DEVELOPMENT KILL | Sequence agreement 42.5%/37.5% and reference-anchored agreement 32.5%/30.0% are far below the unchanged 70% Wilson-lower / 75% standardized exact-bracket acceptance contract. Neither candidate may be locked or taken to the sealed disjoint holdout. |
| 2026-08-04 | CT-11 provenance-only rule diagnostic | old failed 500 joined rows + frozen interval/state provenance | Deterministic five-fold out-of-fold comparison of logistic, random-forest, and extra-trees rules over production status/confidence, counts, interval width, round/confidence summaries, and coarse evidence-text counts. Best exact-pattern model: 247/500 overall and 236/453 (52.097%) among review-datable rows. | DEVELOPMENT KILL | Existing provenance is insufficient to predict the corrected visual adjudication near the unchanged 75% exact-bracket gate. Do not treat threshold tuning or state-machine-only changes as a viable remediation, and do not open the new holdout for them. |
| 2026-08-04 | CT-11 frozen DINOv3 visual-head diagnostic | `ct11_dinov3_dev_20260804/` | Old failed 500 only; 1,498 frozen frames; DINOv3-L-SAT 1024-d center and 4096-d center/context/full embeddings hash-locked. Five-fold GroupKFold by anchor with monotonic changepoint reconstruction. Summary `64768dd5…`: center exact 255/453 (56.291%); multiscale exact 236/453 (52.097%). | DEVELOPMENT KILL | A frozen DINOv3 backbone plus balanced linear frame head does not approach the unchanged exact-bracket gate. No new model was downloaded and no R4/R5 artifact was changed or opened. Do not lock this candidate or expose the sealed disjoint holdout. |
| 2026-08-04 | CT-11 old-set human calibration package | `ct11_human_calibration_v1_20260804/` + `RUN-cape-town-ct11-human-calibration-2026-08-04.md` | Failed 500 only; 100 unique anchors including all 32 repeat-state conflicts; 100 one-anchor native-detail sheets; blind package `bb11e06f…`; review contract `7bbb34f0…`; all files `0444`; sheet hashes PASS. A/B validation, Wilson metrics, exact conflict queue, and final-label merge have 100-row CLI smoke coverage; focused suite 10/10. | FROZEN / AWAITING TWO HUMAN REVIEWS | Blind delivery contains no anchor IDs, production outcomes, intervals, or old labels. Two independent human outputs must be frozen before disagreement adjudication. This is development calibration, not acceptance; the disjoint 500+100 holdout remains sealed and hash-verified. |
| 2026-08-12 | Citywide dual-track plan DRAFT-v2 | `docs/replan_v2/RUN-cape-town-citywide-backdating-delivery-plan-2026-08-12.md` | Engineering-first: Leg-E prefetch GO (catalog/chip only); Leg-S parallel explore; full-city scorer and publication remain HOLD; sealed 500+100 still SEALED | DRAFT-v2 / AWAITING OWNER PREFETCH AUTH + fable-5 review | Supersedes the 2026-08-03 reading that blocked *all* full-Cape-Town GEHI work. **Download/prefetch may proceed** under written owner authorization as outcome-blind readiness; it is **not** CT-11 pass and **must not** start production scoring or open the disjoint holdout. Disk stop: projected chip peak needs ≥20% free margin (current `/home` free ~94 GiB vs 110–191 GiB upper bound). |

## Change log

| Date | Change |
|---|---|
| 2026-08-12 | Citywide delivery plan rewritten as DRAFT-v2 dual-track (Leg-E prefetch continuous; Leg-S science parallel). Tracker log: full-city *scorer/publish* remain HOLD; outcome-blind *download* unblocked pending owner prefetch auth. |
| 2026-07-24 | Initial CT v1 production plan and tracker created from RUN 3 error audit and CT cohort analysis |
| 2026-07-24 | Owner confirmed 52-grid tie band and 2025-01-31 cutoff; rebuilt canonical CT grid from Dropbox RA1/RA2 KML sources |
