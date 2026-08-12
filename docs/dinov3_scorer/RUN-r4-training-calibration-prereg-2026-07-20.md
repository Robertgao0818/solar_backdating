# RUN — R4 v1 frozen-head training and stratified calibration prereg (2026-07-20)

Status: **FROZEN / NOT STARTED** (owner decision incorporated 2026-07-20).
This document is the binding preregistration for the first R4 run in the
Run3-native local research line. No R4 checkpoint, calibration result, or test
metric existed when these rules were frozen.

Parent records:

- [`PRD-run3-native-local-line-2026-07-19.md`](PRD-run3-native-local-line-2026-07-19.md)
- [`DATA-r3-feature-cache-2026-07-19.md`](DATA-r3-feature-cache-2026-07-19.md)
- [`DESIGN-phase0-emission-extension-2026-07-19.md`](DESIGN-phase0-emission-extension-2026-07-19.md)
- [`DATA-t0-paired-ablation-2026-07-19.md`](DATA-t0-paired-ablation-2026-07-19.md)
- [`DATA-run3native-repeat-ceiling-2026-07-19.md`](DATA-run3native-repeat-ceiling-2026-07-19.md)

## 0. Decision, scope, and attempt budget

T0 did not reproduce the old aggregate hard-MAP TVD over-band problem, but it
also did not test per-anchor fidelity: year flips remained about 31–32% and the
repeat ceiling remains only about 0.77. The owner therefore authorizes **one
bounded R4 v1 baseline**, followed by **at most one hypothesis-led correction
for each failed G1 or G2 gate**. There is no open-ended sweep. If G1 or G2 still
fails after its one correction and no new causal hypothesis exists, PRD §7.4
applies: shelve this line and move resources to Cape Town backdating.

This RUN covers only R4 training, post-training probability calibration, and
calibration-side health checks. It does not:

- open or score the R0 test split, the 576-anchor repeat-ceiling panel, or any
  R5 metric;
- train or evaluate `r1_cropgeo_v2` features;
- implement or enable the conflict-gate guard;
- train DINO, use LoRA/PEFT, alter the production scorer, or change
  `infer_install_dates.py`;
- generate crop features, call Gemini, or modify any production artifact.

The only scientific attempt licensed by this document is `r4_v1`. A crash,
pre-emption, or byte-identical resume of the same locked run is not a new
attempt. Any changed scientific field creates a new attempt and is forbidden
unless it is the single gate-specific correction described in §9.

## 1. Immutable inputs and sample roles

### 1.1 R0 manifest and split lock

The only sample list is the frozen R0 manifest. A loader must read these named
files and verify their full SHA-256 values before reading rows:

| object | rows | SHA-256 |
|---|---:|---|
| `r0_manifest_v1/manifest.parquet` | 311,195 observations / 41,393 anchors | `ffd0c174d6dc62098ce96ff3a1883b4d3a0006efd7ced99d6543c7a2f053f8cf` |
| `r0_manifest_v1/splits.parquet` | 41,393 anchors | `5a8ba02c95589a9f145d838b406129d17cce086964344356fe80bd924c418ee5` |
| `r0_manifest_v1/MANIFEST_LOCK.json` | — | `825d44fcbd294b183ad9b536ae224b3b9a356ff382d493a70c6f0b1c65b9a67d` |

Root:
`~/zasolar_data/geid_temporal/run3_native_line_2026-07/r0_manifest_v1/`.
The manifest's `split` column and `splits.parquet` must agree exactly.

Frozen top-level roles are:

| role | anchors | observations | unique feature chips |
|---|---:|---:|---:|
| train | 29,781 | 225,854 | 225,792 |
| calibration | 6,209 | 45,012 | 45,008 |
| test (R5 only) | 5,403 | 40,329 | 40,326 |

R4 may read train and calibration rows only. Test rows, test-derived summaries,
and `repeat_ceiling_v1/{panel_obs.parquet,panel_anchors.csv,scoring_provenance_*}`
are forbidden inputs, including for debugging, threshold choice, checkpoint
choice, or deciding whether a correction is promising.

### 1.2 Calibration subroles

R0's calibration set is not replaced or resampled. It is deterministically
partitioned at the **anchor** level into three non-overlapping R4 roles using:

```text
b = uint64_be(sha256(UTF8("r4_v1_cal_roles@2026-07-20")
                     + NUL_BYTE + UTF8(anchor_id))[0:8]) mod 10
b in 0..3 -> cal_es       # early stopping / checkpoint selection
b in 4..6 -> cal_fit      # probability calibrator fitting
b in 7..9 -> cal_select   # threshold selection and R4 health verdict
```

The implementation must write `locks/calibration_roles.parquet` before model
initialization, with columns `anchor_id, r0_split, hash_bucket, r4_role`, then
record its row count and SHA in `RUN_LOCK.json`. The realized counts are facts
to verify: `cal_es=2,512`, `cal_fit=1,799`, `cal_select=1,898`. No row may be
moved to improve a stratum.

### 1.3 R3 feature cache lock

The sole feature source for `r4_v1` is the cache delivered by commit `d4104b9`:

```text
~/zasolar_data/geid_temporal/run3_native_line_2026-07/r3_feature_cache_v1/
```

Required lock SHA-256:
`86297de7ed35f75cd4cbd961f97b430130fbc69000979a9f0b8dade043bdcf03`.
Its cache key is fixed as:

```text
crop_geometry  = r1_cropgeo_v1@2026-07-19
backbone_hash  = vit_small_patch14_dinov2.lvd142m@sha256:cb91f5a740744d75
pooling_version = r3_pool_v1
n_rows         = 311126
```

Every manifest observation joins by `src_tiff_sha256 == chip_sha`; the four
cache-key columns must match the lock. Duplicate observations that share a
feature remain separate observations in their original anchor sequence. The
loader must never enumerate `features/*.parquet` to define the sample set.

### 1.4 Mandatory leakage and completeness gate

Before any seed starts, all of the following must equal zero; otherwise the run
is an input-contract failure and stops:

- manifest anchors assigned to more than one split;
- `src_tiff_sha256` values appearing in more than one split;
- `legacy_group_anchor_id` values appearing in more than one split;
- `leakage_component_id` values appearing in more than one split;
- manifest/splits split mismatches;
- manifest rows missing their exact cache key, unexpected cache keys, null or
  non-384-length ROI/context/full features;
- test anchors in any R4 dataloader or calibration-role file;
- duplicate `(anchor_id, capture_date)` manifest rows.

The already-observed reference values are 0 for all five cross-split checks.
The implementation must recompute them and persist the counts; it may not trust
this prose in place of a run-time assertion.

## 2. Frozen model

### 2.1 Encoder boundary

DINOv2 ViT-S/14 is completely frozen. R4 consumes cached fp16 features and
does not instantiate the backbone during training. A validation-only loader may
instantiate it solely to verify `backbone_hash`, under `eval()` and
`inference_mode()`. Any trainable backbone parameter, LoRA adapter, PEFT module,
or newly embedded chip is a hard contract failure.

### 2.2 Input combination and normalization

For each observation, upcast the three 384-d cached vectors to fp32 and
concatenate in the locked order:

```text
x = concat(feat_roi, feat_context, feat_full)  # 1152-d
```

There is no attention, branch selection, learned feature mixing, or metadata
input. Compute per-dimension mean and population standard deviation from the
**train split only**, replace standard deviations below `1e-6` with `1.0`, and
store the 1,152 rows as `locks/feature_normalizer.parquet` with columns
`feature_index, branch, branch_index, mean, std` and SHA-256 in the run lock.
The same transform is applied to every calibration and future test row.

### 2.3 Light two-head network

The exact head is:

```text
LayerNorm(1152, eps=1e-5)
Linear(1152, 256)
GELU(approximate="none")
Dropout(p=0.10)
shared hidden h (256-d)
  -> Linear(256, 1) -> sigmoid              = raw q
  -> Linear(256, 2) -> softmax [absent,present] = raw [e0,e1]
```

It has exactly **298,243 trainable parameters**: 2,304 LayerNorm + 295,168
shared Linear + 257 quality head + 514 state head. This count must be asserted.
All matrices use Xavier-uniform initialization with gain 1; all hidden and
LayerNorm biases are zero and LayerNorm scale is one. Output biases are set
from train-only empirical class priors: quality bias is `log(n_informative /
n_uninformative)`, and state biases are `log([n_absent,n_present] /
(n_absent+n_present))`. Counts and resolved biases go in the lock.

## 3. Labels, localization abstention, and teacher brackets

### 3.1 Three states are binding

The semantic labels are `present`, `absent`, and `uninformative`; code may use
the already-approved alias `abstain` for the third state. `uninformative` is
never relabeled to `absent`, never included in state-head CE, and never supplies
an absent likelihood to the decoder.

For a row with a `TargetLocalizationObservation` (TLO), effective labels and
decode inputs use the approved hard gate:

```text
tlo is None                              -> preserve label; localization_pending=true
tlo.target_localized and not tlo.abstain -> preserve label; q_effective=q_model
otherwise                                -> label=uninformative; q_effective=0;
                                            e0=e1=0.5
```

In particular, `target_localized=True and abstain=True` still abstains. Missing
TLO is the approved bridge state and is not silently treated as localization
failure. The current R4 v1 input lock has no full-corpus TLO sidecar and freezes
`tlo_source=null`; therefore rows retain R0 `label_v1` with
`localization_pending=true`, except for the exact owner-approved empty-`K_i`
boundary overrides in Amendment A1 below. This preserves the approved Phase-0
bridge semantics but does **not** claim that v1 absent labels have independent
per-frame localization proof.

Quality target is 1 for effective `present/absent`, 0 for effective
`uninformative`. State target exists only for effective `present/absent`.

### 3.2 Teacher brackets and interval-loss eligibility

Teacher brackets are derived only from each anchor's frozen manifest rows,
sorted by `(capture_date, chip_index)`, with no production output join:

| frozen `scan_status` | bracket/loss rule |
|---|---|
| `done_appears` | `interval`: latest effective absent strictly before the earliest effective present, then `(lower, upper]`; missing/inverted bounds are fatal |
| `done_installed_during_census` | `census_bound`: `upper=census_date`; the Phase-0 evidence-cutoff synthetic epoch must exist |
| `done_already_present_before_geid_history` | `left_censored`: `{tau=0}` |
| `done_ambiguous_nonmonotonic` | no interval loss; frame loss only |
| `done_ambiguous_no_recent_anchor` | no interval loss; frame loss only |

The expected anchor counts are respectively 18,340 / 17,395 / 2,119 / 3,315 /
224 and must reconcile before training. No current status maps to
`right_censored`; support remains in the approved decoder but is not fabricated
for this corpus. `build_k_i` uses date-interval overlap with the student's cells,
never teacher/student epoch-index identity, and an empty `K_i` is fatal.

### Amendment A1 — owner-approved conservative empty-`K_i` sidecar (2026-08-03)

The real pre-start materialization found exactly 2,146 `done_appears` anchors
whose two teacher boundary dates collapse into one frozen 45-day student epoch,
making `K_i` empty. Owner evidence `确认 R4 保守修订和隔离提交` was recorded at
`2026-08-03T09:00:53Z`, before any valid seed result or R5/test access.

The binding additive sidecar is:

```text
~/zasolar_data/geid_temporal/run3_native_line_2026-07/
  r4_empty_k_conservative_sidecar_v1/frame_label_overrides.parquet
```

- sidecar SHA-256:
  `fefac6fe234e2a9e2c21ea77aa0bd7cc8b788ff19e89f89310cf0171da23a82a`;
- sidecar run lock SHA-256:
  `ba083fcb8587254836ee78a493ff05802a184996f86f45a2f89089c0cbc6dd35`;
- artifact manifest SHA-256:
  `8cfcec2047415192bec12883b7496502f24dead62f23c5cf76ae472f76cbeb49`;
- amendment-config SHA-256:
  `199881012234e66472a272e8b718599e729d07f9b3159992c4e475f45f1ac576`.

It contains 4,292 unique boundary rows for 2,146 anchors: 1,593 train and
553 calibration, with zero test anchors. For each affected anchor, both exact
teacher boundary frames become `uninformative`, the anchor becomes
`interval_loss_eligible=false`, and every non-boundary row remains unchanged.
The sidecar consumes no V3/V4/V5, CoJ, CT, repeat-panel, or R5/test label and
does not rewrite R0. Independent application reconciled `empty_k_anchors=0`
over the remaining 30,818 interval-eligible train/calibration anchors.

This A1 amendment is a pre-start input-contract repair: it changes no decoder,
head, optimizer, seed, calibration role, threshold, or acceptance gate and does
not consume the post-R5 correction budget in §9. The loader must verify the
sidecar SHA from its pre-run lock before any seed starts.

## 4. Decoder and loss lock

All training, calibration, and later R5 outputs use the approved continuous
Phase-0 path:

- `FrameEmission(q,e0,e1)` and `frame_loglik = log(q*e(state)+(1-q))`;
- TLO hard AND applied before decoding;
- `gap_days=45`, epoch representative = the maximum-q frame, with its e0/e1;
- epoch abstains and is removed from the tau grid when `q < 1e-3`;
- explicit `already_present`, `beyond_window`, and census-bound accessors;
- `ClampContext.ceiling_date` is the anchor's manifest `census_date` (which must
  be non-null and constant within anchor), with no external clamp-table scan;
- the train-only cohort prior is fitted once from §3.2's eligible train brackets
  using `fit_turnbull(max_iters=500,tol=1e-10)`, then
  `to_cohort_prior(smooth_lambda=0.05,beyond_policy="terminal_year",
  temperature=1.0)`; it and all clamp inputs are SHA-locked and reused for all
  seeds and calibration roles.

For that prior fit only, translate `done_appears` to its finite interval;
translate `done_installed_during_census` to the finite interval from its latest
effective absent before census to `census_date`; and translate
`done_already_present_before_geid_history` to a left-censored interval ending at
its earliest effective present. Ambiguous statuses remain excluded. Missing or
inverted bounds are fatal, and the realized censoring-kind counts are locked.

The training implementation may be differentiable torch code, but it must be a
numerical translation of the reference Phase-0 functions. Before training, its
detached posterior must have the same MAP/boundary state and maximum absolute
posterior difference at most `1e-6` on the unit fixtures and 200 manifest-listed
train anchors selected by the smallest SHA-256 of `anchor_id`.

The sustained midpoint decoder may be emitted only as a diagnostic column. It
cannot affect loss, early stopping, calibration, threshold selection, or any
gate.

For an interval-eligible anchor:

```text
L_interval = -log(sum(posterior[k] for k in K_i))
```

For every anchor, `L_frame` is the mean of quality BCE over all frames plus the
mean state CE over informative frames. Both use train-derived inverse-square-
root class weights, normalized to mean one and capped at 10; resolved weights
are locked. An anchor with no informative frame contributes quality loss only.
The optimization objective is anchor-equal:

```text
L = mean(L_interval over eligible anchors) + 0.25 * mean(L_frame over all anchors)
```

No loss coefficient, class weighting rule, epoch pooling rule, prior, or clamp
may be chosen after observing calibration or test performance.

## 5. Optimizer, seeds, early stopping, and checkpoint choice

The three fixed seeds are **2026072001, 2026072002, 2026072003**. Each seed
controls initialization, anchor order, and dropout. Deterministic algorithms are
enabled; resolved torch/CUDA determinism settings are recorded.

Training is fixed to AdamW (`lr=3e-4`, `betas=(0.9,0.999)`, `eps=1e-8`,
`weight_decay=1e-4`), anchor batches of 128, gradient norm clipping at 1.0, at
most 80 epochs, no scheduler, and no mixed precision. One epoch visits every
train anchor exactly once; variable-length sequences are masked, never split
across batches.

After every epoch, evaluate uncalibrated interval NLL on `cal_es`. The selected
checkpoint is the lowest `cal_es` interval NLL; ties within `1e-6` use lower
`cal_es` frame loss, then the earlier epoch. Stop after 10 consecutive epochs
without an interval-NLL improvement of at least `1e-4`, but never before epoch
15. The selected checkpoint, not the last checkpoint, proceeds to calibration.
No ensemble checkpoint is permitted.

Seed summaries report every seed separately plus median and min–max. The
pre-registered deployable research artifact is the **median-ranked seed by
`cal_select` interval-eligible `MAP-in-K_i` rate**, with ties broken by lower
`cal_select` interval NLL and then lower numeric seed. This choice is made once,
before R5, and cannot change after test is opened. R5 must still report all
three seeds and seed-clustered uncertainty; the median seed is not a substitute
for multi-seed reporting.

For calibration-side rates, report a 95% percentile interval from 2,000
anchor-cluster bootstrap resamples with fixed bootstrap seed `2026072099`,
separately for each training seed. The across-seed summary remains the median
and min–max of the three point estimates; three seeds are not misrepresented as
enough observations for a parametric confidence interval.

## 6. Post-training probability calibration

Calibration is fitted independently per seed after checkpoint selection, using
only `cal_fit`. Raw logits are mapped by a deterministic hierarchical Platt
calibrator:

```text
calibrated_logit = softplus(a_raw) * raw_logit + b
                 + delta_geometry + delta_era + delta_quality
```

There is one calibrator for q and one for the present-vs-absent log-odds.
Categorical residuals use L2 penalty 1.0; the global slope/intercept are not
penalized. Optimize binary NLL with full-batch LBFGS, maximum 500 iterations,
gradient tolerance `1e-9`, in float64 on CPU. Geometry has the four observed
`chip_arm|area_bin` cells. Era bins are `2019–2020`, `2021–2022`, and
`2023–2025`. Image-quality bins use raw q available at inference:
`low=[0,0.5)`, `medium=[0.5,0.8)`, `high=[0.8,1]`. Unknown categories at future
inference back off to zero residual (global calibration).

The state calibrator fits informative rows only. The q calibrator fits all
rows. Calibrated state output is `[1-p_present,p_present]`; therefore e0+e1=1.
Calibrators and their category maps are JSON, with resolved coefficients and
SHA in the run lock. Teacher `quality_flag` may be used for diagnostic reporting
but is forbidden as a calibrator/model input because it is unavailable to the
local scorer at inference.

Calibration reporting on untouched `cal_select` must include Brier score,
binary NLL, 15 equal-count-bin ECE, AUROC, and reliability-bin counts for both
heads, overall and by:

- A24 vs A48;
- area `<15`, `15–40`, `40–100`, `>=100 m²`;
- the three frozen era bins and each capture year;
- raw-q quality bin and teacher `quality_flag` (`usable/ambiguous/unusable`,
  diagnostic only).

`<40 m²` is a co-headline beside the overall result. A slice with fewer than
200 rows or fewer than 20 examples of either binary class is marked
`underpowered` and reported without a slice-level pass/fail; it is never merged
post hoc to hide a result.

## 7. Accept/abstain threshold lock

Soft q is never thresholded before Phase-0 likelihood; `q<1e-3` only implements
the approved no-information epoch rule. The final anchor acceptance score is
the maximum calibrated Phase-0 posterior cell mass. Candidate thresholds are
the fixed grid `{0.50,0.51,...,0.99}`.

On `cal_select`, choose the **lowest** threshold satisfying all three:

1. among interval-eligible accepted anchors, the event `MAP index in K_i` has a
   one-sided 95% Wilson lower bound at least 0.7542 overall;
2. the same lower bound is at least 0.7337 for the `<40 m²` co-headline;
3. overall anchor coverage is at least 0.80.

The values 0.7542 and 0.7337 are the frozen RUN3-native repeat ceilings minus
their max–min spreads: `0.7708-0.0166` and `0.7529-0.0192`. If no candidate
qualifies, threshold selection fails; the code must not relax a bound or choose
the visually nicest operating point. The chosen threshold and the full grid
table are persisted before R5. Test performance cannot change it.
Ambiguous-status anchors remain in the coverage denominator and all frame
metrics but, by §3.2, cannot enter the bracket-hit denominator. This R4
`MAP-in-K_i` check is a calibration-side proxy, not a substitute for R5's exact
repeat-teacher interval agreement.

R5 will count rejected anchors as abstentions in all-unit fidelity and will
also report accepted-only risk and coverage. Thus `present -> uninformative`
cannot manufacture a gate pass by shrinking the denominator.

## 8. R4 health verdict (before R5)

R4 produces exactly one of these machine-readable verdicts.

### `TRAINING_FAILED`

Any input/leakage assertion fails; the trainable-parameter/backbone contract
fails; a seed is missing, non-finite, or has no eligible checkpoint; selected
`cal_es` interval NLL does not improve by at least 1% over that seed's epoch-0
initialized head; or a purported deterministic rerun of the same seed changes
the selected checkpoint SHA or predictions. Infrastructure interruption alone
is not a scientific failure if an exact resume completes.

### `CALIBRATION_FAILED`

Training is healthy, but any of the following holds on `cal_select` for two or
more seeds: either head has non-finite probabilities; calibrated overall NLL is
worse than its raw-head NLL by more than 0.005 per row; overall ECE exceeds
0.05; calibrated q or state Brier score is not better than its constant
train-prevalence baseline; or no frozen threshold meets §7. Underpowered slice
diagnostics cannot alone create this verdict.

### `READY_FOR_R5`

All three seeds satisfy the training contract, at least two seeds avoid every
calibration failure above, the median-ranked seed has a valid frozen threshold,
and every required artifact in §10 reconciles. This verdict means only “worth
an untouched R5 evaluation”; it is not a G1/G2/G3 pass and not production GO.

R4 never computes G1, G2, or G3. In particular, the R5 test split and the
repeat-ceiling panel are not model-selection instruments.

A code fix that merely restores conformance to this already-frozen config,
before any valid seed result exists, is not a scientific correction. If
`TRAINING_FAILED` reflects a valid run's optimization failure, or if
`CALIBRATION_FAILED` requires changing any model, loss, calibration, or
threshold rule, the only possible retry is a committed `r4_g1c1` amendment and
it immediately consumes the G1 correction budget below. There is no separate
R4 health-rescue budget.

## 9. Correction and kill discipline

The baseline scientific attempt is `r4_v1`. The following budget is absolute:

- **G1:** at most one `r4_g1c1` correction after an R5 G1 failure;
- **G2:** at most one `r4_g2c1` correction after an R5 G2 failure. The approved
  P2 deterministic tie/margin treatment counts as this G2 correction if used;
- a single changed run may address both gates, in which case it consumes both
  correction budgets; there is no `c2`, rescue sweep, seed search, or silent
  architecture/crop/calibration variant selection.

A correction requires a committed amendment **before it runs**, naming one
observed failure signature, one causal hypothesis, one bounded component to
change, an expected directional effect, and a numeric kill rule. It must retain
the frozen R0 splits, seeds, R3 v1 cache, test-blind threshold procedure, and
backbone veto. Any head-capacity change must remain within ±10% of the v1
298,243-parameter head unless a separately parameter-matched control is included
in the amendment. Once an R5 test has been opened, that same test cannot provide
a new confirmatory verdict for a changed model; a correction needs an
owner-approved, disjoint locked confirmation panel, otherwise it remains
diagnostic and the line shelves.

T0 changes the G2 interpretation in one precise way: `[0.037,0.063]` remains
the historical reference band, but the **rejecting boundary is TVD > 0.063**.
A value below 0.037 is reported as sub-band, not failed merely for being more
stable; T0's control mean was 0.032. R4 does not compute this statistic.

If G1 or G2 remains above its rejecting boundary after the one licensed
correction, or if no concrete new hypothesis exists, mark slice 10 `SHELVED`
under PRD §7.4 and route work to Cape Town. G3 remains separately required
before any future production discussion and cannot rescue G1/G2.

## 10. Artifact contract

All data/model outputs live outside git under:

```text
~/zasolar_data/geid_temporal/run3_native_line_2026-07/r4_v1/
```

Required layout:

```text
config/r4_v1.yaml
locks/RUN_LOCK.json
locks/calibration_roles.parquet
locks/feature_normalizer.parquet
locks/train_cohort_prior.json
seeds/<seed>/train.jsonl
seeds/<seed>/checkpoints/epoch_<NNN>.safetensors
seeds/<seed>/checkpoints/selected.json
seeds/<seed>/predictions_cal_es.parquet
seeds/<seed>/predictions_cal_fit.parquet
seeds/<seed>/predictions_cal_select.parquet
seeds/<seed>/anchors_cal_es.parquet
seeds/<seed>/anchors_cal_fit.parquet
seeds/<seed>/anchors_cal_select.parquet
seeds/<seed>/calibrators/quality.json
seeds/<seed>/calibrators/state.json
seeds/<seed>/thresholds.json
seeds/<seed>/metrics.json
predictions/calibration_all_seeds.parquet
metrics/R4_HEALTH.json
metrics/seed_summary.json
artifacts.sha256
```

`train.jsonl` contains one row per epoch with seed, epoch, train losses
(total/interval/frame/q/state), `cal_es` losses, learning rate, gradient norm,
wall time, and checkpoint SHA. `selected.json` records the exact selection
tuple and checkpoint SHA.

Prediction Parquet is one row per `(seed, anchor_id, capture_date)` and includes
at least: split role, chip/cache keys, capture date, effective label,
localization flags, raw and calibrated q/e0/e1, raw logits, teacher quality,
geometry/area/era strata, and checkpoint/calibrator/config hashes. The required
anchor companion tables are one row per `(seed, anchor_id)` and include cell
boundaries, posterior vector, MAP index, boundary state, max posterior,
accept/abstain, teacher bracket, K_i, and sustained diagnostic output.

`thresholds.json` contains the fixed grid, every candidate's overall and
`<40 m²` counts/coverage/agreement/Wilson bound, selected threshold, and rule
version. `metrics.json` contains frame, interval, calibration, coverage-risk,
and all required slice tables with denominators. `R4_HEALTH.json` contains one
of the three §8 verdicts plus every atomic check.

`RUN_LOCK.json` is written before initialization and finalized additively. It
must contain:

- absolute input paths, row counts, every SHA in §1, and every R3 shard SHA
  transitively via the R3 lock SHA;
- derived calibration-role/normalizer/prior hashes and leakage counts;
- full resolved config, attempt ID, seeds, label/bracket/decoder/calibration/
  threshold rule versions;
- repository commit used by the implementation and `git_dirty=false`;
- Python, OS, CPU, GPU, driver, CUDA, torch, pyarrow, pandas, numpy and
  safetensors versions plus `pip freeze` SHA;
- all checkpoint, calibrator, threshold, prediction and metric hashes.

Atomic temporary files use `.tmp -> os.replace`; incomplete outputs have no
completion marker and cannot be selected. No data artifact is committed. Git
may contain only implementation code/tests, this RUN/prereg, and tracker
updates.

## 11. Cropgeo-v2 and production boundaries

`r1_cropgeo_v1` is the sole R4 v1 baseline. Future cropgeo-v2 evaluation must
build a cache under a distinct versioned root (for example
`r3_feature_cache_cropgeo_v2/`) with `crop_geometry=r1_cropgeo_v2...`; it may
not reuse or union-write `r3_feature_cache_v1/features/`. A v1/v2 ablation must
reuse the same R0 rows, calibration roles, seeds, head/loss/calibration/threshold
rules, and report both arms regardless of outcome. It must be preregistered
before either arm's R5 result is viewed. Test results may not choose the crop
version.

Nothing in R4 changes Gemini defaulting, the PresenceScorer seam, RUN3
deliverables, the Phase-0 production default, the conflict-gate state, or slice
7. Even a future R5 pass authorizes only the next research prereg, not rollout.

## 12. Pre-start checklist

R4 implementation may start only after all boxes can be made machine checks:

- [ ] hashes/counts and all §1.4 leakage gates pass;
- [ ] test-access guard is active and tested;
- [ ] train-only normalizer, prior, bracket map and calibration-role lock are
      materialized without disk-derived sample discovery;
- [ ] exact 298,243-parameter head and frozen-backbone assertions pass;
- [ ] three-state/TLO/`K_i`/soft-emission regression tests pass;
- [ ] checkpoint, calibration, threshold and artifact schemas have round-trip
      tests;
- [ ] a tiny **train + calibration only** smoke completes all three artifact
      stages without producing or reading test predictions;
- [ ] implementation commit is clean and recorded in the pre-run lock.

Until then, tracker status remains **R4 prereg frozen; training not started**.
