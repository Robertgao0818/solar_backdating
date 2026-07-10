# DATA — sequence-level distillation head pilot: pre-registration (2026-07-10)

Status: **corrected run executed 2026-07-10 — B-R1 KILL; frame-bar FAIL.** The
first physical executions remain invalidated below because their target adapter
reconstructed a boundary from thresholded per-frame hard labels. The accepted
result uses the banked Phase-0 decoded teacher interval required by Path B.

Parent: [`DATA-student-revival-paths-2026-07-10.md`](DATA-student-revival-paths-2026-07-10.md)
(path B) · carry-over review:
[`DATA-anchor-pair-pilot-review-2026-07-10.md`](DATA-anchor-pair-pilot-review-2026-07-10.md)
§5 · failure diagnosis:
[`DATA-fidelity-gate2-dual-fail-2026-07-10.md`](DATA-fidelity-gate2-dual-fail-2026-07-10.md).

## Hypothesis

The current student is trained on independent frame labels and then passed
through a brittle hard-MAP changepoint decoder. About 0.80 decided frame
agreement becomes only 0.37–0.39 decoded interval agreement. A small temporal
head trained on the teacher's **decoded interval itself** should avoid the
per-frame-to-interval compounding error and reduce both early false-present and
late false-absent boundary mistakes.

This is the EVL/AIM regime: frozen image backbone, pooled per-frame embeddings,
and a light sequence adapter. Global 384-d pooling is acceptable here because
the mechanism under test is temporal aggregation, not the spatial localization
mechanism that invalidated a strong reading of the anchor-pair pilot.

## Locked population and teacher target

1. **Population.** Reuse
   `~/zasolar_data/slice5/out/nomarker_bilinear518_k6/features.npz`: 384-d
   frozen DINOv2-S pooled embeddings from the ISSUE-02 anchor-disjoint corpus.
   No chip or backbone re-extraction, new imagery, new GT or API calls.
2. **Split.** Preserve the existing ISSUE-02 `train` / `heldout` anchor split.
   Training uses only `train` anchors. Because this head has no calibrated
   threshold, the verdict uses all `heldout` anchors (no calib/report reuse and
   no post-hoc selection on heldout metrics).
3. **Sequence.** One sample is all cached frames for an anchor, sorted by
   `(capture_date, version)` and deduplicated by `(anchor_id, capture_date)`
   (stable lowest-version/row tie-break), per the repository data contract.
   Padding is masked. Calendar time is supplied through a deterministic
   sinusoidal encoding of the absolute capture ordinal; both arms receive the
   same encoding.
4. **Decoded interval target.** Join each cached frame to the banked
   `label_manifest.csv` raw `pv_present` / `confidence` / `quality_flag`, then
   run the adopted Phase-0 changepoint decoder with its locked emissions,
   empirical-Bayes cohort prior and epoch gap. The resulting canonical
   `teacher_key` (`INTERVAL`, `AP_BOUND<=`, or `UNDATED`) is the target. Vexcel
   clamp is intentionally not applied while constructing the boundary-index
   target because clamp-only dates are not cached frame positions; gate-2
   applies that external clamp later. `label_3class` is not used to construct
   the interval target.
   The loss predicts the interval kind plus lower/upper boundary indices. It
   does **not** contain an auxiliary per-frame hard-label loss.
5. **Gemini-reliability weight.** Derive terminal-status weights from the
   sanctioned rep1–3 gate diagnostic
   `fidelity_gate_20260710/gate2_diag/dinov2_floor/unit_rows.jsonl`: within each
   terminal-status stratum, pool exact pairwise equality of independently
   decoded Gemini `teacher_key`s on units with at least two decoded reps. Clip
   the resulting rate to `[0.25, 1.0]`, normalize train-sample weights to mean
   1, and use them only as loss weights. The status/weight is never a model
   input. Missing strata receive the global decoded pairwise rate before the
   same clipping.

## Leakage guard

The model input is exactly `(pooled embedding, capture ordinal, padding mask)`.
It does not receive teacher frame labels, teacher present probabilities,
decoded boundary flags, terminal status, rep-agreement weight, or any feature
computed from them. Consequently a boundary target cannot be recovered by an
identity mapping analogous to the anchor-pair pilot's `[e,e,0]` self-pair.
Training and heldout anchors remain disjoint, and any missing required input
fails closed.

## Locked arms and capacity match

Both arms optimize the same weighted sequence-level kind/lower/upper loss and
use the same valid-interval decoder (`lower < upper`). Both are below 10M
parameters.

- **Arm A (bet): temporal head.** `384 → d_model=192`, two
  `TransformerEncoderLayer`s (`nhead=4`, feed-forward=384, dropout=0.1,
  GELU, pre-norm), then shared kind/lower/upper output heads. Kind uses masked
  mean pooling; lower and upper are masked per-frame logits.
- **Arm B (parameter-matched per-frame control).** A shared two-layer GELU MLP
  independently transforms each `(embedding + identical time encoding)`;
  the same output/decode interface follows. Its hidden width is selected
  deterministically, before training and without metrics, as the integer width
  in `[64, 2048]` whose exact trainable parameter count is closest to Arm A.
  The allowed ratio is `params(A) / params(B) ∈ [0.90, 1.10]`; otherwise the
  run aborts.

The control may pool independent frame representations for the sequence-kind
logit, but its per-frame boundary score cannot use any other frame. The only
difference being tested is learned cross-frame temporal interaction.

## Locked training

- Seeds: **0, 1, 2**; all three are mandatory and pooled for the verdict.
- Optimizer: AdamW, learning rate `1e-3`, weight decay `1e-4`, batch size 32,
  maximum 100 epochs, patience 12.
- Validation carve-out: deterministic `_val_anchors` carve-out from train
  anchors; early stop on weighted validation interval exact-match, tie-broken
  by lower weighted loss. No hyperparameter sweep and no seed replacement.
- Loss per sample: kind cross-entropy for all samples; lower-bound
  cross-entropy for `INTERVAL`; upper-bound cross-entropy for `INTERVAL` and
  `AP_BOUND`; available terms are averaged before the Gemini-reliability
  sample weight is applied.
- Device: local RTX 4070 when available, CPU smoke is allowed for tests only.

## Metrics and confidence intervals

Evaluation is on every heldout anchor for every mandatory seed. Point estimates
pool the three seed predictions, but uncertainty uses a **paired anchor-cluster
bootstrap**: resample heldout anchors 10,000 times with replacement and retain
all seeds/rows for a sampled anchor. Bootstrap RNG seed is `20260710`; percentile
95% intervals are reported. Seeds are therefore repeated measures, not fake
independent anchors.

Primary metrics:

1. exact decoded interval agreement rate and Arm-A minus Arm-B difference;
2. transition-band FP rate = teacher-absent → predicted-present divided by all
   teacher-absent rows in `[last_absent, first_present] ±1`;
3. transition-band FN rate = teacher-present → predicted-absent divided by all
   teacher-present rows in that band;
4. teacher-present → predicted-unusable rate in the same band (explicit
   abstention/miss channel);
5. overall and transition-band decided frame agreement, derived from the
   predicted interval (`INTERVAL`: absent through lower, present from upper,
   interior unusable; `AP_BOUND`: present from upper; `UNDATED`: unusable).

Rates use fixed teacher denominators; converting a miss to `unusable` cannot
remove it from the FP/FN denominator. Coverage and confusion tables are reported
alongside but do not replace the locked metrics.

## Verdict rule B-R1 (machine checked, no discretion)

`scripts/validation/check_student_path_gate.py --rule sequence_head_r1` returns
**GO** iff all conditions pass on the pooled heldout result:

1. exact interval agreement delta `(A − B) > 0` and its paired-cluster 95% CI
   lower bound is `> 0`;
2. transition FP-rate reduction is at least 30% and its 95% CI lower bound is
   `> 0%`;
3. transition FN-rate reduction is at least 30% and its 95% CI lower bound is
   `> 0%`;
4. overall decided frame agreement A ≥ B;
5. transition teacher-present → predicted-unusable rate A ≤ B + 2 pp;
6. parameter counts match within ±10% and all three seeds completed.

Separately, the existing `frame_bar` must pass for Arm A: decided frame
agreement ≥0.90 both overall and in the transition band. **Path B proceeds only
if B-R1 and `frame_bar` both pass. KILL otherwise.** GO licenses a clean gate-2
re-run only; it does not pass gate-2 and cannot change its 0.7724 ceiling or
agree-key caliber.

## Budget and stop discipline

Local RTX 4070, cached features, six small-head trainings total; wall-clock
budget ≤1 day, zero API spend, zero new GT, zero backbone/LoRA training. On KILL
there is no hyperparameter rescue sweep. On GO, wire the temporal scorer into a
separate `fidelity_gate.py` run with decoded-only co-headline; do not overwrite
the existing gate artifacts.

## Implementation-audit addendum (locked before corrected run)

The two-axis post-run review found three deviations before the result was
accepted:

1. **Target provenance (invalidating):** the first harness version rebuilt
   `last_absent/first_present` from thresholded `label_3class`, which is not the
   banked Phase-0 decoded teacher interval required by the handoff.
2. **Repository date dedup:** same-date rows were retained instead of
   deduplicating by `(anchor_id, capture_date)`. The current cache has zero such
   duplicates, so this did not move numbers, but the contract is corrected.
3. **Reporting completeness:** bootstrap CIs were missing for
   present→unusable and decided-agreement metrics, and confusion tables were not
   emitted. These are now mandatory outputs from the same paired anchor-cluster
   bootstrap/contribution path.

No architecture, optimizer, seed, split, effect-size threshold or verdict rule
changes. All earlier physical runs are implementation smoke only and do not
constitute a Path-B verdict. The corrected run must use the adapter in §Locked
population and teacher target above.

## Invalidated preliminary run (audit only; not a Path-B verdict)

Harness: `scripts/validation/pilot_sequence_head_2026_07_10.py` · artifacts:
`pilot_result.json`, `pilot_metrics.json`, `summary.md`, and per-arm/per-seed
`head.pt`, training log and heldout predictions below the output directory.

### Population and reliability weights

| item | n |
|---|---:|
| all cached anchors | 764 |
| train anchors | 601 |
| heldout anchors (verdict) | 163 |
| `INTERVAL` targets | 742 |
| `AP_BOUND` targets | 3 |
| `UNDATED` targets | 19 |

The sanctioned rep1–3 artifact contributed 673 decoded rep pairs, 453 exact
matches (global 0.6731). The strata present in this pilot received the locked
weights: `done_appears=0.8462`, `done_installed_during_census=0.7500`, and
`done_already_present_before_geid_history=0.2500` after the preregistered clip.
Weights were loss-only and normalized to train mean 1.

### Capacity and training completion

| arm | params | ratio A/B | seed 0 epochs | seed 1 | seed 2 |
|---|---:|---:|---:|---:|---:|
| A temporal transformer | 668,933 | **1.00028** | 13 | 15 | 28 |
| B independent-frame control (hidden 1157) | 668,746 | — | 14 | 13 | 13 |

All mandatory seeds completed. The first physical execution emitted a PyTorch
warning that memory-efficient attention was allowed to use a non-deterministic
CUDA algorithm because the harness used `warn_only=True`. No result from that
execution was accepted. The harness was corrected to set
`CUBLAS_WORKSPACE_CONFIG=:4096:8` and enforce deterministic algorithms, then the
same locked seeds/configuration were rerun without tuning; the numbers below are
from that deterministic rerun.

### Pooled heldout metrics (3 seeds; paired anchor-cluster 95% CI)

| metric | Arm A temporal | Arm B control | A vs B |
|---|---:|---:|---:|
| exact interval agreement | **0.2331** [0.1820, 0.2843] | 0.1677 [0.1186, 0.2209] | **+0.0654** [+0.0286, +0.1022] |
| transition FP rate | 0.0527 [0.0272, 0.0817] | **0.0387** [0.0174, 0.0637] | reduction **−36.1%** [−100.0%, −6.1%] |
| transition FN rate | 0.0863 [0.0518, 0.1244] | **0.0624** [0.0286, 0.1001] | reduction **−38.5%** [−131.6%, +2.7%] |
| transition present→unusable | **0.1978** | 0.2494 | A lower by 5.2 pp |
| overall decided frame agreement | 0.8292 | **0.8385** | −0.9 pp |
| transition decided frame agreement | 0.7186 | **0.7336** | −1.5 pp |
| overall / transition coverage | 0.8839 / 0.7939 | 0.8076 / 0.6881 | context only |

Arm A learned a real sequence-level interval signal: its exact interval rate
improved by 6.5 pp and the paired CI excludes zero. But it achieved that lift by
deciding more often and moving errors out of `unusable` into explicit opposite
present/absent calls. The load-bearing transition FP and FN channels therefore
both worsened, and overall frame fidelity regressed. This is precisely why the
preregistered abstention guard was necessary but not sufficient on its own.

### Machine verdict

| B-R1 condition | value | pass? |
|---|---:|:---:|
| interval delta >0; CI low >0 | +0.0654; +0.0286 | PASS |
| transition FP-rate reduction ≥30%; CI low >0 | −36.1%; −100.0% | **FAIL** |
| transition FN-rate reduction ≥30%; CI low >0 | −38.5%; −131.6% | **FAIL** |
| overall decided agreement A ≥ B | 0.8292 ≥ 0.8385 | **FAIL** |
| present→unusable A ≤ B+2pp | 0.1978 ≤ 0.2694 | PASS |
| parameter match ±10%; ≥3 seeds | 1.00028; 3 | PASS |
| frame bar: transition and overall ≥0.90 | 0.7186; 0.8292 | **FAIL / FAIL** |

> **B-R1 = KILL; investment frame-bar = STOP.** The sequence head demonstrates
> a statistically positive exact-interval lift but fails the bidirectional
> transition-error mechanism the downstream gate requires. It is not licensed
> for a gate-2 rerun. No rescue sweep is allowed under the locked stop discipline.

### Implication

Path B does not solve the frozen pooled-embedding student under the current
hard interval caliber. Combined with path A's scoped KILL, there is no licensed
student gate-2 rerun from these two head-only pilots. The 0.7724 teacher ceiling
remains unchanged; any future decision to soften the interval caliber is a
separate caliber decision, not a model result.

## Corrected accepted result (Phase-0 decoded teacher target)

Artifacts:
`~/zasolar_data/geid_temporal/pilot_sequence_head_20260710_corrected/`
(`pilot_result.json`, `pilot_metrics.json`, `summary.md`, per-seed heads,
training logs and heldout predictions). The invalidated preliminary artifacts
remain in the unsuffixed directory for audit and must not be cited.

### Target provenance and population

- 8,496/8,496 cached feature rows joined exactly to `label_manifest.csv` raw
  `pv_present` / `confidence` / `quality_flag`.
- Adopted `changepoint` decoder, epoch gap 45 days, locked fitted emissions and
  EB cohort prior; no Vexcel clamp at boundary-index target construction.
- 764 anchors = 601 train + 163 heldout; decoded targets: **750 INTERVAL / 3
  AP_BOUND / 11 UNDATED**.
- Same rep-agreement loss weights, arms, parameter match, seeds and verdict
  rule as locked above. Training epochs A = 16/15/25; B = 18/14/18.

### Pooled heldout metrics (3 seeds; paired anchor-cluster 95% CI)

| metric | Arm A temporal | Arm B control | A vs B |
|---|---:|---:|---:|
| exact decoded interval agreement | **0.2904** [0.2331, 0.3517] | 0.1963 [0.1493, 0.2474] | **+0.0941** [+0.0491, +0.1391] |
| transition FP rate | **0.0504** [0.0274, 0.0761] | 0.0559 [0.0307, 0.0833] | reduction **9.8%** [−11.9%, +29.8%] |
| transition FN rate | 0.1258 [0.0798, 0.1756] | **0.0733** [0.0400, 0.1107] | reduction **−71.7%** [−144.4%, −29.5%] |
| transition present→unusable | **0.1746** [0.1318, 0.2187] | 0.2662 [0.2154, 0.3186] | A lower by 9.2 pp |
| overall decided frame agreement | 0.8210 [0.7868, 0.8549] | **0.8340** [0.8003, 0.8667] | −1.3 pp |
| transition decided frame agreement | 0.7107 [0.6618, 0.7613] | **0.7207** [0.6717, 0.7712] | −1.0 pp |
| overall / transition coverage | 0.8963 / 0.8077 | 0.8147 / 0.6750 | context only |

Complete overall and transition teacher×prediction confusion tables are in
`summary.md` and `pilot_result.json`. The load-bearing transition counts are:

| channel | Arm A | Arm B |
|---|---:|---:|
| teacher-absent → present (FP) | 46 / 912 | 51 / 912 |
| teacher-present → absent (FN) | **103 / 819** | 60 / 819 |
| teacher-present → unusable | 143 / 819 | 218 / 819 |

The temporal head learned a stronger exact-interval representation: +9.4 pp,
with the paired CI comfortably above zero. But its increased coverage mainly
converted teacher-present abstentions into explicit absent calls. FP improved
only 9.8% with a CI crossing zero, while FN worsened 71.7% with the entire CI
below zero. Thus the interval lift is real but is not the bidirectional
transition-error repair the gate math requires.

### Final machine verdict

| B-R1 condition | value | pass? |
|---|---:|:---:|
| interval delta >0; CI low >0 | +0.0941; +0.0491 | PASS |
| transition FP-rate reduction ≥30%; CI low >0 | +9.8%; −11.9% | **FAIL** |
| transition FN-rate reduction ≥30%; CI low >0 | −71.7%; −144.4% | **FAIL** |
| overall decided agreement A ≥ B | 0.8210 ≥ 0.8340 | **FAIL** |
| present→unusable A ≤ B+2pp | 0.1746 ≤ 0.2862 | PASS |
| parameter match ±10%; ≥3 seeds | 1.00028; 3 | PASS |
| frame bar: transition and overall ≥0.90 | 0.7107; 0.8210 | **FAIL / FAIL** |

> **B-R1 = KILL; investment frame-bar = STOP.** No gate-2 rerun is licensed,
> and the locked stop discipline forbids a rescue sweep. Under the current hard
> interval caliber, Path B is closed for this pooled frozen-embedding regime.
