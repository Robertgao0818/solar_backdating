# RUN plan — left-censored cohort back-extension to 2015 (ext2015)

- **Status: DRAFT v3 — optimized from v2, pending owner approval**
  (2026-07-19).
- Upstream is immutable: RUN 3 completed 41,393/41,393 and its coverage-gain
  QA is closed. This run writes only under a new `ext2015_v1/` root.
- Goal: replace a RUN-3 `≤2019` left-censor only when older evidence yields a
  valid finite bracket or a strictly earlier censor bound. A failed extension
  must leave the RUN-3 row unchanged.

## 0. Optimization summary

v2 proposed probing and downloading TM for the whole cohort before the first
scan. v3 makes the measured Wayback floor the bounded mainline and uses TM only
as a targeted second-stage gap filler:

```text
RUN-3 left-censored cohort
        ↓
Stage A: Wayback 2012–2018 + one RUN-3 known-present control
        ↓
mechanical adoption gate
        ├─ valid finite/deeper bound → candidate result
        ├─ low-confidence gap >730 d / residual ≤2015 → targeted TM Stage B
        └─ error, contradiction, or invalid interval → carry RUN-3 row
        ↓
dominance merge → new 41,393-row deliverable
```

This ordering has four advantages:

1. The known baseline has a fixed size before any network work: **20,283 new
   pre-2019 Wayback target chips**, not an unbounded TM expansion.
2. TM availability calls and downloads are paid only for anchors whose Stage-A
   result is still coarse enough to benefit.
3. A shared run-local verdict store replays Stage-A judgments during Stage B;
   Stage B pays primarily for the new TM frames.
4. The final merge is monotone in information: no extension failure can turn a
   RUN-3 left-censored row into an undated or weaker row.

## 1. Frozen cohort and corrected evidence base

Source deliverable:
`deliverable_run3_2026-07-18/jhb_full382_fpcut_install_dated_2026-07-18-run3.csv`,
filtered on `left_censored == 1`.

- **4,058 anchors**, all
  `scan_status = done_already_present_before_geid_history`.
- Lane split: **a24 = 2,960 / a48 = 1,098**.
- RUN-3 present controls: **4,057 × `2019-01-15`** (Wayback) and
  **1 × `2019-07-30`** (TM, anchor `..._t00019246`).
- **2,506 unique legacy chip groups** after joining
  `anchors_v2/anchors_all.csv.legacy_group_anchor_id`.
- Group fan-out is material: 1,654 groups have one cohort target, 402 have two,
  200 have three, and 250 have four. Availability is probed per group, but
  chips are downloaded and scored per **target anchor**.

### 1.1 Wayback, re-derived at target level

`availability_rebuild_2026-07-12/wayback_info_full.csv` contains 4–5 metadata
candidate dates per group in 2015–2018 and an optional `2012-03-03` date.
After cohort-only target fan-out:

- **20,283** pre-2019 target/date candidates (`2012-03-03` through
  `2018-06-27`): 4 dates for 7 targets and 5 dates for 4,051 targets.
- `2012-03-03` exists for **4,002 / 4,058 targets**; the other 56 initially
  retain a `≤2015-01-15` floor unless targeted TM supplies an older usable
  frame.
- Adding exactly one known-present control per target produces a **24,341-row
  Stage-A catalog**.

These are metadata candidates, not guaranteed chips. The download manifest,
achieved zoom, raster readability, and on-disk filter are the authority used by
the scan.

### 1.2 TM correction to v2

v2's `781 / 2,506 groups` and `1,425 / 4,058 anchors` figures came from
`tm_full_reparsed.csv` alone. That file is only the first availability shard.
The complete 2019+ source union is:

- `tm_full_reparsed.csv`: 781 cohort groups / 1,425 targets;
- `tm_retry_403.csv`: the complementary 1,725 groups / 2,633 targets;
- `tm_repull_trunc_home.csv`: corrective overlaps.

Their union covers **2,506 / 2,506 groups and 4,058 / 4,058 targets** in
2019+. Therefore there is no measured 31.2% ceiling on pre-2019 TM coverage.
Pre-2019 TM remains **unmeasured** and is measured only for the Stage-B subset.

### 1.3 Other v2 corrections that change execution

- `catalog_max_date = 2019-06-30` excludes the one anchor whose RUN-3
  known-present control is `2019-07-30`. The hard ceiling is
  **`2019-07-31`**.
- The fresh ext run has fresh scan-state directories. RUN-3 terminal states do
  not affect it, so `--force-restart` is **not** a mainline requirement. It is
  reserved for filtered retry sets inside an ext stage.
- `--tm-catalog-interval` belongs to `run_adaptive_scan.py`; it is not a flag
  on the existing availability CLI. A paced, resumable TM probe driver is a
  prerequisite, not an invocation detail to assume.
- A fabricated TM sentinel row is not an acceptable catalog contract. The
  known merged-offline loader defect is fixed and tested before launch (P0).

## 2. Locked design and owner checkpoints

| ID | Decision | v3 disposition |
|---|---|---|
| D1 | Historical floor | **Recommend `2012-01-01`**. It adds 4,002 candidate chips and converts most residual `≤2015` cases into either `≤2012-03-03` or a finite 2012–2015 bracket. Owner approval required before download. |
| D2 | Provider topology | **Wayback baseline, targeted TM gap-fill.** Do not probe/download all-cohort TM speculatively. |
| D3 | Window ceiling | **`2019-07-31`**, so every target has exactly one previously observed present control in scope. Do not add other 2019+ dates to Stage A. |
| D4 | Imagery access during scoring | **Strictly offline.** All GEHI calls finish in download/probe phases; scan uses `--no-live-gehi --offline-require-chip-on-disk`. |
| D5 | Chip/cache isolation | **Run-local chip root.** Hard-link or reflink the 4,058 immutable control chips from the RUN-3 cache; download old chips locally. Do not add files to the RUN-3 shared chip directory or change its `.download_complete`. |
| D6 | CoJ 2015 aerial | Optional QA overlay, off by default. A positive can confirm presence; an absence is never changepoint evidence because the 2015 layer's measured FN rate failed gate A. |
| D7 | Reproducibility | Enable one explicit run-local verdict store for pilot, Stage A, and Stage B. Do **not** carry RUN 3's `--no-verdict-store` choice into this run. |
| D8 | Merge policy | **Conservative dominance merge**, never blanket replacement of all 4,058 rows. Invalid, contradictory, or weaker ext results fall back to the exact RUN-3 interval row. |
| D9 | Model/quota | Pin both rounds to `gemini-3.1-flash-lite`, 10 workers / 2 qps. No mid-run model fallback; pause and resume the same model after quota recovery. |

## 3. Run-local contracts

Root:
`~/zasolar_data/geid_temporal/ext2015_v1/`.

```text
ext2015_v1/
  inputs/                  frozen cohort, groups, config, hashes
  chips/                   run-local controls + historical chips
  download/                candidate slices, manifests, raw GEHI logs
  stage_a/pilot/           48-anchor pilot states and QA
  stage_a/full/{a24,a48}/  full Wayback-baseline states
  stage_b/                 optional targeted-TM probe/download/rescan
  merge/                   candidate intervals, adoption ledger, full stitch
  deliverable/             new CSV/GPKG/README/stats only
  qa/                      final sample, sheets, grades, DATA memo
```

Every generated CSV is written atomically and has a row-count, unique-key,
source-hash, and sha256 entry in `inputs/run_manifest.json`. Stage contracts
remain CSV/JSONL; no database service is introduced.

Required frozen inputs:

- RUN-3 deliverable CSV and full 41,393-row intervals CSV;
- `anchors_v2/{anchors_all,anchors_A24,anchors_A48}.csv`;
- RUN-3 a24/a48 scan states for control-frame provenance;
- `wayback_info_full.csv` and the RUN-3 offline catalog;
- Vexcel per-grid capture-date CSV;
- run-local fully expanded YAML.

The YAML explicitly pins at least:

```yaml
adaptive_scan:
  spec_version: ext2015_v1
  round_1_floor_year: 2018
  walk_back_years: 5
  picks_per_round: 5
  tail_round_threshold: 3
  download_zoom_ladder: [19, 18]
  discovery_zoom_ladder: [19, 18]
  catalog_min_date: "2012-01-01"
  catalog_max_date: "2019-07-31"
```

z19 is the primary whole-bbox level and z18 the whole-picture fallback. z20 is
not attempted for old dates unless that same vintage has separately confirmed
complete z20 coverage.

For Wayback, `info` remains the capture-date discovery source because its
`availability` output exposes layer-release labels. A candidate is admitted
only after an exact **whole target-bbox** download succeeds at z19 or z18; that
successful raster, not the metadata row, is the bbox-completeness confirmation.

## 4. Execution phases and hard gates

### P0 — close the two code seams and freeze inputs

Implement small, tested run support before any paid/network work:

1. **Offline merged-provider empty-side fix.** When an anchor is present in the
   union offline catalog, one provider may legitimately have an empty list.
   Missing from both providers remains a hard error under `--no-live-gehi`.
   Startup reports union, TM, and Wayback coverage separately. Tests pin:
   Wayback-only success, TM-only success, absent-from-both failure, and key
   preservation after disk filtering. This replaces v2's fake sentinel rows.
2. **Idempotent ext input builder.** Produce cohort all/a24/a48 CSVs, a
   cohort-only 2,506-group manifest whose `target_anchor_ids` contain no
   non-cohort siblings, the Stage-A target-level candidates, control manifest,
   and `run_manifest.json`. Hard assertions: 4,058 unique targets, 2,960/1,098
   lanes, 2,506 groups, 20,283 historical Wayback rows, 4,058 controls, no
   duplicate `(anchor_id, provider, capture_date)`. `all_capture_dates` is
   scrubbed so the downloader's expanded input is also exactly 20,283 rows;
   shared internal versions never collapse distinct capture dates.
3. **Paced/resumable TM probe driver for P4.** Reuse GEHI bbox-complete
   availability with a shared `GehiRateLimiter` at no faster than 1 request/s;
   query z19 first and z18 as fallback. Persist one status row per
   `(group_id, zoom)` so a valid empty result is distinguishable from an
   unattempted, timed-out, or blocked request. Preserve raw stdout/stderr hashes.
4. **Adoption/stitch tool.** It must select among RUN 3, Stage A, and Stage B by
   the dominance rules in P5 and emit a 4,058-row decision ledger plus one
   complete 41,393-row intervals CSV.

Gate P0 passes only when targeted unit tests plus the existing temporal suite
pass and the input builder reproduces every frozen count above. No changes are
made to RUN-3 artifacts.

### P1 — Stage-A chips and on-disk catalog

1. Resolve exactly one RUN-3 usable-present control result per cohort target.
   Copy by hard-link/reflink into `ext2015_v1/chips`; verify its sha256 against
   the path recorded in the RUN-3 scan state. Controls are 4,057 Wayback
   `2019-01-15` plus one TM `2019-07-30`.
2. Split the 20,283 historical Wayback candidates into deterministic
   approximately 1,000-row shards and download exact dates into the run-local
   chip root with the `[19,18]` ladder. Run one GEHI download process at a time
   so the shared limiter remains authoritative; each shard has its own manifest,
   raw log, and `.done` marker. `--allow-failures` is acceptable for a shard;
   failures are data, not permission to put phantom rows into the scan catalog.
3. Build `stage_a_catalog_on_disk.csv` from successful/readable manifest rows
   plus the verified controls. Never build the scan catalog from metadata-only
   candidates.
4. Build `stage_a_scannable_{a24,a48}.csv`. An anchor is scannable only if it
   has its verified present control and at least three distinct on-disk
   pre-2019 dates, including one date `≤2015-12-31` and one date `≥2018-01-01`.
   Non-scannable anchors are recorded as `carry_run3_input_shortfall` and incur
   zero Gemini calls.

Download gate:

- 4,058/4,058 control hashes match RUN 3;
- every catalog row resolves to a non-empty readable raster at z19 or z18;
- candidate, success, failure, and per-anchor usable-date counts reconcile;
- no file was written beneath the RUN-3 run or shared chip roots.

### P2 — Stage-A pilot (48 anchors)

Use a deterministic sample with 48 unique groups where possible:

- 24 a24 / 24 a48;
- broad grid and source-area coverage;
- at least 4 of the 56 no-2012 targets;
- the `2019-07-30` control target;
- all achieved-zoom strata, with z18-heavy a24 cases deliberately included.

Use fresh pilot states, the run-local config/catalog/chips, and the shared
run-local verdict store. Pilot, lane, retry, and Stage-B writers run
**sequentially** because the verdict store enforces a single writer.
`--force-restart` is unnecessary on the first pass.

Pilot passes only if:

1. scan provenance contains zero live GEHI subprocess calls and every chip is
   an on-disk cache hit;
2. after a same-model filtered retry of transient failures, there are zero
   orchestrator/download/Gemini terminal errors;
3. at least **44/48** known-present controls re-score usable-present; any failed
   control is mechanically non-adoptable regardless of the aggregate rate;
4. at least **60%** of pilot rows pass the Stage-A mechanical adoption gate;
5. visual review of every mechanically adoptable change is ≥90%
   defensible-or-plausible, with no common-mode registration error;
6. no adopted a24 transition depends solely on a visually ungradable z18
   boundary frame;
7. inferred `done_appears` rows with blank/inverted intervals are counted and
   rejected by the stitch tool, not patched or silently shipped.

Failure of items 1, 2, 5, or 6 is STOP. Failure of the yield gates (3–4) sends
the plan back for owner review; it does not justify a full run on hope.

### P3 — Stage-A full cohort

After owner approval of the pilot, scan the scannable cohort sequentially by
lane at 10 workers / 2 qps. Use fresh
`stage_a/full/{a24,a48}/scan_states`, the same config and verdict store, and no
live GEHI.

- The launcher marks a lane done only after expected-state count and terminal
  status reconciliation pass.
- Terminal error rows are retried with a newly generated filtered anchors CSV
  plus `--force-restart` against that ext lane only. A bare rerun is not a
  retry because terminal error states no-op.
- No automatic switch to another Gemini model is allowed.

Run `infer_install_dates.py` per lane, re-inject `lane`, and stitch the Stage-A
candidate intervals. Produce an interim adoption ledger before deciding whether
Stage B has enough work to justify running.

### P4 — targeted TM gap-fill (conditional)

Stage-B eligibility is intentionally narrow:

- a mechanically valid `done_appears` interval wider than **730 days** (the
  existing inference code's low-confidence boundary); or
- a valid residual left-censor bound at/after `2015-01-15`, normally because
  no usable 2012 Wayback frame exists.

Infrastructure errors, failed present controls, nonmonotonic results, and
blank/inverted intervals are not TM-densification targets; they carry RUN 3
unless independently repaired and re-piloted.

For eligible targets only:

1. deduplicate their legacy groups and run the paced bbox-complete TM probe at
   z19, with z18 fallback, over `2012-01-01..2019-07-31`;
2. select TM dates strictly inside each unresolved interval. Deterministically
   choose at most two dates per target, greedily minimizing the largest
   remaining temporal gap; for a residual left-censor, choose the earliest
   usable candidates before the current bound;
3. fan out only to eligible cohort targets, download to the run-local chip
   root, and rebuild an on-disk combined catalog;
4. run a small 24-anchor Stage-B pilot if any TM candidates exist, then rescan
   only the eligible subset into fresh Stage-B states using the same verdict
   store. Previously scored Wayback/control pixels should be cache hits.

Before network work, write a Stage-B budget sheet with exact group, target,
probe-call, candidate-chip, and projected Gemini-call counts. Stage B requires
owner approval at that measured checkpoint and is skipped cleanly when it has
no useful candidates.

### P5 — inference, dominance merge, and deliverable

Infer Stage-B intervals and choose one candidate per cohort anchor using these
ordered rules:

1. A candidate must have a usable-present control from its own scan, a supported
   terminal status, nonblank required dates, valid chronological invariants,
   and no contradicting/nonmonotonic evidence.
2. Stage A may replace RUN 3 only when it supplies either a finite valid bracket
   whose end is no later than the RUN-3 bound, or a strictly earlier
   left-censor bound.
3. Stage B may replace Stage A only when its finite interval is contained in
   and narrower than Stage A's interval, or its left-censor bound is strictly
   earlier. Otherwise keep Stage A.
4. If neither ext candidate dominates, carry the RUN-3 interval row
   field-for-field, including its original `scan_state_path`.

The adoption ledger has exactly 4,058 rows and records:
`anchor_id,run3_class,stage_a_class,stage_b_class,selected_source,
adopted,reason,control_date,control_present,old_bound,new_bound,
old_interval_days,new_interval_days,selected_scan_state_path`.

Only after that decision, build a complete intervals CSV:

- 37,335 non-cohort RUN-3 rows unchanged;
- 4,058 cohort rows selected by the ledger;
- exactly 41,393 unique anchor IDs;
- `lane` present on every row;
- row-specific Stage-A/Stage-B/RUN-3 `scan_state_path` preserved.

Run `build_install_dated_deliverable.py` with `--expected-count 41393` and all
six existing gates. Add ext-specific gates:

- non-cohort interval fields exactly equal RUN 3;
- every fallback cohort row exactly equal RUN 3;
- every adopted row passes the dominance rule;
- no resulting adopted deliverable row has `undated_reason`, a blank
  interval/bound, or a bound later than its RUN-3 bound;
- CSV/GPKG and adoption-ledger counts reconcile.

Write a new deliverable directory atomically. Never modify
`deliverable_run3_2026-07-18/`.

### P6 — final QA and documentation

Create an ext-specific deterministic sampler; do not reuse
`qa_run3_coverage_sample.py`'s RUN2→RUN3-specific strata. Target 80 rows
(take all and redistribute when a stratum is short), sampled from:

- RUN3 left-censored → finite dated;
- RUN3 left-censored → deeper left-censored;
- Stage A → Stage B narrowed;
- ext rejected → RUN-3 fallback;
- a24/a48, z19/z18, no-2012, and the `2019-07-30` special case.

`qa_run3_render_sheets.py` may be reused with explicit paths; the sampler and
grade ledger are ext-specific. CoJ-2015 may appear only as a positive-reference
overlay. Promotion requires ≥90% defensible-or-plausible among sampled adopted
rows, zero adopted row with a confirmed invalid transition, and no common-mode
failure by arm, zoom, or vintage date. A miss stops promotion; it does not alter
the conservative row-level fallback already produced.

The new README must define left-censoring by each row's actual
`install_interval_end`, not by a global 2019 floor. Recompute class counts,
bounded coverage, confidence/interval-width tables, hashes, and descriptive
statistics. Publish a DATA memo, then update TRACKER and this plan to COMPLETE
only after QA and owner promotion approval.

## 5. Cost and duration envelope

Known Stage-A envelope before downloads:

- **20,283** new historical Wayback chips. At the downloader's 1 s pacing plus
  jitter, the request-spacing floor is roughly **5.6–6.4 h** before retries.
- Existing compressed 2019 cohort chips average about 32 KB; 20,283 similarly
  compressed chips project to roughly **0.65 GB**. Reserve 2 GiB and gate on
  measured free space rather than this estimate.
- At most 24,341 Stage-A observations exist, but the adaptive round shape is
  normally one or two batches per anchor. Budget roughly **8.1k routine Gemini
  calls**, then replace the estimate with pilot-measured calls/anchor. At 2 qps,
  the rate-limit floor is about 1.1 h; allow 1.5–2 h wall clock.
- Stage B is not estimated speculatively. Its exact cap is at most two new TM
  chips per eligible target plus one or two availability calls per unique
  eligible group; the P4 budget sheet is authoritative.

## 6. Risks, containment, and rollback

| Risk | Containment / stop condition |
|---|---|
| Old Wayback metadata advertises phantom dates | Catalog is rebuilt from successful readable files only; scanner also enforces on-disk mode. |
| Old-frame registration or z18 visibility is poor | Balanced pilot, explicit a24/z18 review, and per-row conservative fallback. CoJ absence is never used to force a transition. |
| Known-present frame flips absent under model drift | Present-control gate on every adopted row; run-local verdict store pins the first ext verdict; aggregate pilot threshold stops common-mode drift. |
| Gemini quota outage | 2 qps, no model mixing, pause and filtered same-model retry after recovery. Failures are never cached. |
| Inverted `done_appears` inference defect | No global patch in this run; blank/inverted candidates are rejected and carry RUN 3. |
| TM 403/429 or ambiguous empty probe output | TM is conditional and bounded; shared limiter, raw/status ledger, resumability, z18 fallback, and no fabricated catalog rows. |
| Catalog/provider empty-side loader bug | Fixed with union-coverage semantics and tests in P0, not hidden with sentinel data. |
| Provenance contamination | All new chips, states, catalogs, verdicts, intervals, and deliverables are run-local. |

Rollback is trivial and lossless: do not promote the new deliverable and keep
using RUN 3. No RUN-3 state, catalog, chip, interval, CSV, GPKG, README, or hash
is changed by this plan.

## 7. Owner approvals

Four explicit approvals remain:

1. **D1 / Stage-A download:** accept the 20,283-chip Wayback baseline, including
   the recommended 2012 floor and its 4,002-chip increment over a 2015 floor.
2. **P2 → P3:** accept the measured pilot accuracy/yield and full Stage-A
   Gemini spend.
3. **P4:** approve the measured targeted-TM budget if triggered.
4. **Promotion:** approve the QA-closed deliverable for economics use.
