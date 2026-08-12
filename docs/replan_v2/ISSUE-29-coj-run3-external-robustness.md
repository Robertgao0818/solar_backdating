# ISSUE-29: CoJ 2019/2023 fixed-epoch external robustness check for RUN 3

Status: ready-for-agent
Phase: 2 — external accuracy channel
Blocked by: dinov3 slice 10 R4/R5 closure (owner sequencing gate only; not a scientific dependency)

## Decision and scope

Use the already-downloaded City of Johannesburg (CoJ) 2019 and 2023 aerial
chips as an **independent-imagery, fixed-epoch partial robustness check** of the
completed RUN 3 install intervals.

This is deliberately narrower than a new backdating run:

- RUN 3 remains frozen and is not re-decoded or retrained.
- CoJ observations do not enter R0/R3/R4/R5 training, calibration, threshold
  selection, or model correction.
- The result tests whether a subset of RUN 3's temporal claims is compatible
  with independent-provider observations at two fixed epochs. It does not
  establish exact install dates and CoJ is not called ground truth.
- Execution is deferred until the DINO R4/R5 work is complete. The CoJ corpus
  must be retained locally until this issue is executed or separately backed
  up with a verified manifest.

Parent records:

- RUN 3 plan:
  [`RUN-fullscan-gemini-backdating-run3-2026-07-18.md`](RUN-fullscan-gemini-backdating-run3-2026-07-18.md)
- RUN 3 coverage QA:
  [`DATA-fullscan-run3-qa-coverage-2026-07-19.md`](DATA-fullscan-run3-qa-coverage-2026-07-19.md)
- Earlier CoJ audit lineage:
  [`ISSUE-08-coj-audit-pilot.md`](ISSUE-08-coj-audit-pilot.md) and
  [`ISSUE-09-coj-audit-cohort.md`](ISSUE-09-coj-audit-cohort.md)
- DINO sequencing gate:
  [`../dinov3_scorer/RUN-r4-training-calibration-prereg-2026-07-20.md`](../dinov3_scorer/RUN-r4-training-calibration-prereg-2026-07-20.md)

## Frozen facts at issue creation (2026-07-23)

CoJ corpus:

```text
~/zasolar_data/geid_temporal/coj_prefetch_allgroups_2026-07-12/
  cohort_units.csv
  fetch_stats.jsonl
  fetch_failures.csv
  chips/2019/<legacy_group_anchor_id>.tif
  chips/2023/<legacy_group_anchor_id>.tif
```

- 31,718 planned and downloaded TIFFs: 15,859 legacy group anchors × 2 epochs.
- `fetch_failures.csv` contains no failed data row.
- The RUN 3-native R0 manifest has 41,393 target anchors and exactly the same
  15,859 unique `legacy_group_anchor_id` values: CoJ↔R0 group intersection is
  15,859/15,859.
- None of the 311,195 R0 observations reads these CoJ files. R0 providers are
  TM (192,584) and Wayback (118,611); CoJ-prefetch source paths = 0.
- Therefore the population aligns, while the imagery channel is external to
  RUN 3.

These counts are discovery facts, not substitutes for execution-time hashes
and assertions.

## Unit-of-analysis constraint

The CoJ files are one 96 m chip per **legacy group anchor**, whereas RUN 3
contains one or more **target anchors** per group. A group-level `PV present`
bit must never be copied to every child target.

The primary analysis is target-level:

1. Join R0 target anchors to a CoJ chip only through
   `legacy_group_anchor_id`.
2. Project each target footprint/centroid into the CoJ raster using committed
   geometry utilities and raster georeferencing.
3. Render a target-specific ROI and context window from the group chip.
4. Mark the row geometry-ineligible if the target footprint or required
   context is not completely covered. Do not pad or silently shrink the ROI.
5. Preserve `legacy_group_anchor_id` for clustered sampling and uncertainty.

A secondary group-level `any target present` summary is allowed only as a
diagnostic and must not be presented as target-level accuracy.

## Pre-registration and data lock

Before scoring or viewing any CoJ-vs-RUN3 agreement:

1. Build `COJ_INPUT_LOCK.json` containing SHA-256 for `cohort_units.csv`,
   `fetch_stats.jsonl`, all 31,718 TIFFs, the exact RUN 3 interval table, the
   R0 manifest/splits locks, and the repository commit.
2. Resolve the authoritative acquisition date for each CoJ layer from its
   service metadata and store it in the lock. If only the year is defensible,
   use a frozen year interval rather than inventing a day.
3. Materialize `target_epoch_eligibility.parquet` before scoring, with one row
   per `(anchor_id, coj_epoch)` and at least:
   `legacy_group_anchor_id`, CoJ path/SHA, target geometry source/hash,
   coverage-complete flag/reason, RUN 3 interval kind/bounds, expected-state
   class, area bin, chip arm, split, and sampling hash.
4. Freeze all samples by SHA-256 of stable IDs. No outcome-dependent redraw is
   permitted.
5. Write the scorer/instrument config and its thresholds before joining any
   CoJ verdict to RUN 3 intervals.

The DINO test split may appear here only after R4/R5 is finalized and closed.
This issue cannot feed back into model selection or consume a correction
budget.

## RUN 3 claim at a CoJ epoch

For a finite RUN 3 interval `(lower, upper]` and a defensible point acquisition
date `d`:

- `d <= lower` → expected `absent`;
- `d > upper` → expected `present`;
- `lower < d <= upper` → `non_identifying` and excluded from agreement-rate
  denominators.

Left-censored, census-bound, and ambiguous RUN 3 states require explicit,
tested mappings from their persisted interval semantics. If the CoJ source is
locked only to a year interval `[d0,d1]`, assign an expected state only when
the whole CoJ acquisition interval lies unambiguously before or after the RUN
3 install interval; otherwise mark `non_identifying`.

No midpoint imputation is allowed.

## Execution stages

### Stage A — geometry and instrument smoke

- Deterministic 40-target sample balanced across area (`<40`/`>=40 m²`),
  expected state (`absent`/`present` where available), epoch, and
  single-target/multi-target legacy groups.
- Verify projection, target containment, context completeness, orientation,
  scale, and blank/corrupt handling.
- Render a blinded HTML sheet showing CoJ target crops without RUN 3 dates or
  expected-state labels.
- This stage may fix implementation bugs but may not tune a decision threshold
  against RUN 3 agreement.

#### Stage-A instrument amendment (2026-07-23, owner-directed)

The first smoke mistakenly batched unrelated target crops together. Although
the prompt said that images were independent, Gemini transferred evidence
between targets (including an explicit reference to another image number).
That instrument is rejected before Stage B and its artifacts are retained.

The replacement instrument uses exactly five images for one target per call:
the two target-specific CoJ 2019/2023 crops plus three R0 target crops chosen
outcome-blind as the earliest, temporal-median, and latest distinct available
capture dates. Selection reads only `anchor_id`, `capture_date`, and image
path—not RUN 3 interval bounds, expected state, or prior Gemini verdicts. The
model scores all five cells, but ISSUE-29 metrics consume only the two CoJ
cells. CoJ labels remain year-only. This is reported as a contextual external
check because external-provider CoJ imagery is interpreted with fixed RUN 3
image context; it is not described as a fully context-independent blind read.

### Stage B — frozen bounded pilot

- Freeze up to 800 target anchors, clustered by legacy group and stratified by
  epoch × expected state × area bin × RUN 3 interval kind.
- Score both available epochs when geometry-complete, yielding at most 1,600
  target-epoch observations.
- The automated observation vocabulary is
  `present | absent | uninformative`; `uninformative` is never converted to
  absent.
- Draw a blinded validation panel of at least 200 target-epoch observations,
  including automated-present, automated-absent, uninformative, all primary
  strata, and multi-target groups. Human adjudicators see CoJ imagery and
  target geometry but not RUN 3 bounds or expected state.

Instrument gate:

- automated-vs-blind-human balanced accuracy ≥0.85;
- present and absent recall each ≥0.80;
- geometry-invalid rows never receive an informative verdict.

Failure produces `INSTRUMENT_FAILED`: report the failure and stop without
issuing a RUN 3 robustness verdict. Any changed scoring instrument requires a
new committed amendment and new blinded validation sample.

### Stage C — partial external robustness report

Only after the instrument gate passes:

- overall informative agreement with RUN 3;
- separate expected-absent and expected-present agreement;
- contradiction rate and uninformative rate;
- 2019 vs 2023;
- area bins `<15`, `15–40`, `40–100`, `>=100 m²` with `<40 m²` co-headline;
- A24 vs A48, interval kind, grid/zone where sufficiently powered;
- single-target vs multi-target legacy groups;
- geometry eligibility and attrition from the full 41,393-target population.

Use legacy-group-clustered bootstrap confidence intervals (2,000 resamples,
frozen seed `2026072901`). Every table reports numerator, denominator, excluded
non-identifying rows, geometry failures, and uninformative rows. No
target-epoch independence assumption is allowed.

Verdict vocabulary:

- `SUPPORTIVE_PARTIAL`: overall 95% CI lower bound ≥0.85, both expected-state
  point agreements ≥0.80, and no powered primary area stratum has agreement
  below 0.75.
- `CONTRADICTORY_PARTIAL`: overall 95% CI upper bound <0.85 or either
  expected-state 95% CI upper bound <0.80.
- `MIXED_PARTIAL`: instrument passes but neither rule above is met.
- `INSTRUMENT_FAILED`: Stage-B instrument gate fails.

These labels apply only to identifiable, geometry-eligible CoJ observations.
Even `SUPPORTIVE_PARTIAL` is not a full-population accuracy certificate.

### Stage D — optional expansion

No full-corpus scoring is automatic. Expansion beyond the frozen pilot needs an
owner decision after the Stage-C report and must reuse the locked instrument
unchanged. A full expansion may improve precision but cannot change R4/R5 or
retroactively alter the Stage-C verdict.

## Outputs

All data products remain outside git:

```text
~/zasolar_data/geid_temporal/coj_run3_external_robustness_2026-07/
  locks/COJ_INPUT_LOCK.json
  locks/scorer_config.json
  target_epoch_eligibility.parquet
  samples/pilot_targets.parquet
  samples/blind_validation.parquet
  crops/<epoch>/<anchor_id>.png
  observations.parquet
  human_adjudication.csv
  metrics/strata.csv
  metrics/bootstrap.json
  metrics/VERDICT.json
  qa/blind_sheet.html
  artifacts.sha256
```

Git receives implementation/tests and a `DATA-coj-run3-external-robustness-*.md`
result memo, not imagery or scored data.

## Acceptance criteria

- [ ] Input lock hashes all 31,718 CoJ TIFFs and exact frozen RUN 3/R0 inputs.
- [ ] 15,859/15,859 group join and target multiplicity reconcile; zero
      group-level labels are copied directly to child targets.
- [ ] Acquisition-date precision is documented honestly (point date or year
      interval) and tested in expected-state classification.
- [ ] Geometry eligibility is materialized before scoring; incomplete coverage
      is excluded with an enumerated reason.
- [ ] Stage-A blinded geometry smoke passes.
- [ ] Stage-B sample and instrument are frozen before RUN 3 agreement is read.
- [ ] Blind-human instrument gate produces pass or `INSTRUMENT_FAILED`.
- [ ] If the instrument passes, Stage-C clustered metrics and exactly one
      partial-verdict label are emitted with all exclusions/denominators.
- [ ] No CoJ result is read by or fed back into DINO R4/R5.
- [ ] Result memo and `artifacts.sha256` are written; source chips remain
      recoverable until the issue is closed.

## Out of scope

- Re-running RUN 3 or changing its delivered intervals.
- Treating a CoJ group chip as a target label without target localization.
- Calling CoJ or an automated scorer installation-date ground truth.
- Selecting a DINO checkpoint, calibration rule, crop geometry, or correction.
- Cape Town validation; CoJ is Johannesburg-specific.
- Inferring within-year installation dates from a year-only CoJ layer.
