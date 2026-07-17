# DATA — Path C0 prereg: training-free census-anchored reverse template matching (2026-07-12)

Status: **DRAFT — amended 2026-07-15 after the mathematical audit; the
2026-07-12 decoder is superseded and MUST NOT be used for Stage-0 or held-out
evaluation until the amendment's implementation lock and mandatory tests are
complete. Numeric replacement-prior/confidence bars remain open only on the
reserved calibration split and freeze before any smoke/held-out scoring.**
Tracker slot: [ISSUE-09](ISSUE-09-c0-reverse-template-matching.md).
Governed by the stop-discipline in
[DATA-student-revival-paths-2026-07-10](DATA-student-revival-paths-2026-07-10.md).

## H1 (hypothesis)

A deterministic, training-free similarity-to-reference curve — footprint
patch-token template from the census-anchored known-present GEHI frame,
matched backwards through the vintage stack, decoded with a
single-changepoint posterior — is **replicate-equivalent to a fresh Gemini
run**: its interval agreement with a fresh Gemini round on the rebuilt
full-GEHI stacks is statistically no worse than that round's own rep↔rep
self-agreement, and it contributes independent signal in adjudicated
disagreements.

**Design reframe (owner decision, 2026-07-12): paired comparison, not
triage.** Triage/routing is deferred. Motivation: banked production Gemini
verdicts are considered **dirty** (pre-rebuild state); C0 is training-free,
so nothing in it is fitted to legacy labels — it is immune to that
contamination in a way no trained student could be. Main eval runs paired
against a **fresh** Gemini round on the post-rebuild full GEHI download;
C0 verdicts are computed and locked **blind to** (before or without access
to) the fresh Gemini verdicts on each stack.

## Prior art (round-2 lit survey, 2026-07-12)

- **Temporal Cluster Matching** (RegLab/MS AI4G, arXiv 2103.09787) — closest
  published prior: known footprint + time series, dates construction via
  interior-vs-ring distribution divergence, threshold on the 1-D curve,
  training-free. C0 = TCM with frozen-DINO tokens instead of k-means color
  clusters. Validates the framing; the DINO-feature version appears novel.
- **AnyChange** (arXiv 2402.01188) — mask-averaged latents + dim-normalized
  cosine; bidirectional latent matching, training-free, SOTA unsupervised.
- **Amir et al.** (arXiv 2112.05814) — match the ViT **key facet**, not
  output tokens (co-seg Jaccard 79.5 vs 69.2).
- **Global Renewables Watch** (arXiv 2503.14860) — per-frame detector +
  first-detection rule = the naive baseline in this space; no temporal model.
- Changepoint math: GLR/max-CUSUM scan is exact for single step
  (arXiv 2210.07066); offline Bayesian posterior is closed-form under
  Gaussian-conjugate and emits a credible interval; Turnbull (1976) for
  interval-censored population aggregation; BFAST/CCDC do **not** transfer to
  T≈10 irregular VHR series — borrow only the ≥2-frame persistence rule.

## Mechanism deltas vs killed paths (stop-discipline requirement)

| Killed path | Death | C0 delta |
|---|---|---|
| A (pooled pair-diff MLP) | dual improvement bar | no trained head at all; footprint tokens, not pooled crop |
| A′ (Vexcel census crop anchor) | cross-sensor cos 0.31 smoke kill | anchor = latest **GEHI** present frame; census contributes footprint geometry only |
| A″ (patch-token pair head) | trained head FP +24% | keeps A″'s validated smoke signal (cos 0.784/0.591), removes the trained head |
| B (pooled sequence transformer) | FN +71.7%, overall regression | zero training; spatial footprint preserved; monotone decode forbids frame flip-flop |

## Method (pinned)

**Inputs.** Per-target vintage stacks from the rebuilt full GEHI download,
`.nomarker` crops, tight12 geometry, phase-correlation registration (reuse
`chip_displacement.py`). **No Gemini input anywhere at inference**: frame
usability comes from deterministic image-quality heuristics (blur/contrast/
cloud statistics) plus registration confidence; low-quality frames are
down-weighted (recorded, not imputed).

**Template.** Census-anchored, teacher-free, **nearest-frame both-sides
rule** (owner refinement 2026-07-12). Let `T_c` = census detection date.
Presence semantics are asymmetric around `T_c`:
- **Right side (t ≥ T_c):** presence guaranteed by the census detection
  itself + low removal prior (拆除率低) — eligible at any distance, with
  proximity preferred (a panel 2 y later may be extended/repaired and drift
  from what census saw — cf. `panel_repair_extend_sample.py`).
- **Left side (t < T_c):** presence NOT guaranteed (install may postdate
  the frame) — eligible only if `T_c − t ≤ Δ_left` (**12 months**,
  prereg'd), where the install-in-the-gap probability is small.

Selection: among eligible usable frames on both sides, pick
`argmin |t − T_c|`. If a **left** frame wins and any right frame exists,
apply a deterministic **anchor-consistency gate**: footprint cosine between
the left candidate and the nearest right frame must clear `θ_anchor`
(pinned on train split). Failure ⇒ the install likely falls in
`(t_left, T_c)` — switch to the nearest right anchor and record
`anchor_left_rejected` (itself a useful narrow-interval signal, logged as a
diagnostic, not used by the decode in v0). Targets with no right frame at
all may use a left anchor but are flagged `anchor_side=left_only` and
reported as their own stratum.

Extract patch tokens via `embed_patch_grids()`; template = mean over tokens
whose centers fall inside the census polygon footprint (dilated by 1 patch).

**Why not the Vexcel census frame itself (owner question 2026-07-12).**
Path A′ tested exactly that (DeepSolar++-style) and died at smoke:
cos(GEHI-present, Vexcel) = 0.31 vs same-domain 0.89 — in frozen-feature
cosine space a Vexcel template is near-orthogonal to real GEHI presence.
DeepSolar++ bridges domains with an end-to-end **trained** CNN; C0 is
training-free by design, so the pixel channel cannot cross sensors. Vexcel
still contributes through the two domain-gap-free channels: the panel-level
**polygon geometry** (Vexcel-resolution Mask R-CNN output, projected into
the GEHI patch grid as the footprint mask) and the **census date** `T_c`.
*Optional smoke-only diagnostic arm (non-gating):* degradation-matched
Vexcel template — downsample to GEHI GSD + PSF blur + histogram match
before embedding (A′ died on RAW Vexcel; the degraded variant is untested).
Reported next to the GEHI-anchor arms; promotion would require a prereg
amendment. Reference-guided super-resolution of GEHI frames is explicitly
rejected (SR hallucination is evidence contamination in a dating task).

**Score (per earlier frame t).**
`s_t = cos(f_t, template) − cos(r_t, template)`
where `f_t` = footprint-mean token vector of frame t, `r_t` = mean over a
surrounding ring (footprint dilated 3–6 patches, minus footprint). The ring
contrast is TCM's interior-vs-neighborhood normalization — cancels
scene-wide radiometric/sensor shift. PCA whitening fit on train-split frames,
applied everywhere, before all cosines.

**Registration & small-target robustness (three layers).** Residual
misregistration is the dominant small-target failure mode: at tight12 one
patch ≈ 0.66 m ground, so a 2 m residual shifts the footprint ~3 patches —
off a small panel entirely (displacement audit: median <1 m, but 13–15 % of
tight12 frames lose >50 % of footprint).
1. *Pre-registration:* phase-correlation alignment (`chip_displacement.py`)
   on the full 96 m download extent before cropping, per frame.
2. *Feature-space shift search:* `s_t` is computed over a local shift grid
   `δ ∈ {−3..+3}²` patches (~±2 m); the shift maximizing footprint cosine is
   applied to footprint **and ring together** (so the contrast cannot be
   gamed by sliding onto a brighter roof). Best shift per frame is recorded;
   a frame whose feature-space shift disagrees with the phase-correlation
   estimate by >1.5 m is flagged `reg_suspect`.
3. *Decode weighting:* per-frame inverse-variance weights in the changepoint
   likelihood combine the deterministic image-quality heuristics with
   registration confidence (phase-correlation peak sharpness; `reg_suspect`
   frames down-weighted, never silently dropped).
Residual failures are expected to surface as messy curves → low cleanliness
`q` → the target is reported low-confidence rather than confidently
mis-dated. `q` remains a reported confidence stratifier; its use for
routing is deferred with the triage decision.

**Decode.** Offline Bayesian single-changepoint posterior: Gaussian
likelihood with Normal-Inverse-Gamma conjugate segments, τ ranging over the
real-date gaps between usable vintages;
`P(τ | s_{1:T}) ∝ m(s_{≤τ}) · m(s_{>τ})` normalized over the T−1 gaps plus
"no change in window" and "present before window" hypotheses. Output =
posterior over inter-vintage gaps → install interval (credible mass) + MAP
gap. Point-estimate cross-check: single-step least-squares (= max-CUSUM/GLR)
and isotonic step fit must agree with MAP gap; disagreement ⇒ target flagged
not-clean. **Persistence rule:** the post-step regime must hold for ≥2
subsequent usable frames, else the step is rejected (cloud-spike guard).
**Cleanliness score** `q` = max single-gap posterior mass; prereg'd
threshold `q ≥ 0.8` defines the "clean" triage bucket.

**Arms.**
- Backbone: `dinov2_floor` (ViT-S/14, primary — cheapest, Apache-2.0,
  already integrated) · `dinov3_web_s` (ViT-S/16 — gram-anchoring dense bet)
  · `dinov3_l_sat` (existing SAT-493M L).
- Facet: **key** (primary, lit-backed) · output-token (fallback if the timm
  qkv hook is unstable).
- Aggregation: footprint-mean cosine (primary) · Sinkhorn-OT token-set match
  (secondary, only if the mean-cosine arm's failures correlate with partial
  occlusion).

No training anywhere. No LoRA, no fine-tuning, no fitted head — the only
fitted objects are the PCA whitening basis and the prereg'd thresholds, both
pinned on the train split before held-out is touched.

## Stage-0 smoke (GO/KILL before the main paired eval)

Runs on currently-available stacks; does **not** wait for the fresh Gemini
round. Legacy-label caveat: the banked verdicts are considered dirty, so
the smoke restricts to high-confidence, non-`ambiguous` legacy transitions
and treats them as a **noisy indicative reference only** — the smoke bar
screens for "is there any usable step signal in frozen features", it does
not certify fidelity (that is the paired eval's job).

On ≥60 such anchors: AUC of `s_t`
separating post-install from pre-install frames (per-anchor, pooled).
AUC additionally reported **stratified by footprint area** (small / medium /
large, same strata as the gate-2 area analysis) and with the shift search
ablated (δ=0), so small-target registration sensitivity is visible, not
averaged away. GO/KILL is judged on the pooled number only; the small
stratum and the δ=0 ablation are diagnostics.
**GO bar: AUC ≥ 0.75** for at least one backbone arm; else **C0-S0 KILL**
(frozen features carry no usable temporal step — closes the training-free
line and is strong evidence against any Path-C training investment).

## Main eval — paired design against the fresh Gemini round

Waits on: full GEHI re-download + the owner's fresh Gemini round on the
rebuilt stacks. Protocol:

- **Same stacks, both channels.** C0 and fresh Gemini run on identical
  per-target vintage stacks. C0 verdicts are computed and content-hash
  locked (VerdictStore-style provenance) **before** the fresh Gemini
  verdicts for those targets are read — blindness is auditable, not
  claimed.
- **Rep↔rep yardstick.** The fresh round includes an
  `llm_reliability_sample.py` rep↔rep measurement on the same stacks —
  fresh Gemini's self-agreement is the yardstick C0 is judged against
  (the legacy 0.7724 number is retired along with the dirty verdicts).
- **Disagreement adjudication.** A prereg'd random sample (n ≥ 40,
  stratified by footprint area) of C0-vs-Gemini interval disagreements is
  adjudicated by the owner using the hard-example strip tooling, blind to
  which channel produced which verdict. This is the only step with human
  input, and it is what allows C0 to *win* disagreements rather than being
  capped as a student.
- Baselines reported: naive fixed-threshold-on-curve (shows decode value);
  Phase-0 decoder re-run on fresh labels (continuity reference).
- Anchor-cluster bootstrap, 10,000 draws, as in Path B. All metrics and
  the `q` (cleanliness) distribution reported **by footprint-area stratum**.

**Kill rule `c0_r1`** (to land in `check_student_path_gate.py` with the
harness; all three must hold, CI bounds respected):

1. **Replicate-equivalence:** `agr(C0, Gemini_fresh) −
   agr(Gemini_rep1, Gemini_rep2)` ≥ −5 pp, bootstrap CI lower bound above
   the bar, on decided intervals.
2. **Adjudication share:** in adjudicated disagreements, C0 is the correct
   channel in ≥ 1/3 of cases (Gemini must not dominate; parity ≈ 1/2 would
   mean C0 adds a fully independent replicate's worth of signal).
3. **Safety:** polarity errors where C0 says absent at/after
   `max(T_c, t_anchor)` at rate ≈ 0 — these are internal-consistency
   violations of C0's own anchor and each one is individually inspected.
   For left-anchored targets the window between `t_anchor` and `T_c` is
   exempt from this check (presence there is prior, not guaranteed).

Fail any ⇒ **C0-R1 KILL**, verdict DATA- doc, line closed; no silent reopen.

## Non-goals

- **No triage/routing** (owner decision 2026-07-12; revisit only after the
  paired eval verdict).
- No production wiring (ISSUE-07 discipline unchanged; Gemini stays default).
- Legacy banked verdicts are **not** used as eval truth anywhere (dirty);
  their only residual role is the Stage-0 smoke's noisy indicative reference.
- No absolute-accuracy claim vs ground truth — replicate-equivalence and
  adjudicated disagreement share only. Turnbull aggregation of C0 intervals
  is reported for calibration context, not gated.
- DINOv3 weights licence ("DINOv3 License", non-permissive; SAT gated on HF)
  — review required before any shipped use of dinov3 arms; the primary arm
  (DINOv2-S, Apache-2.0) is licence-clean.

## Artifacts

Harness: `scripts/validation/pilot_c0_reverse_template_2026_07_12.py` (to
build; reuse Path-B split/bootstrap helpers). Outputs under
`~/zasolar_data/geid_temporal/pilot_c0_reverse_template_20260712/`.

## Amendment 2026-07-13 — smoke label source

**What changed.** The basemap rebuild (started 2026-07-13) wiped every
`scan_states` dir and legacy chip stack this harness originally targeted —
the Stage-0 smoke's label source (`done_appears` scan states + confidence
≥ 0.7, see "Stage-0 smoke" above) no longer exists anywhere under
`~/zasolar_data`. Only geometry survived the wipe (`chip_targets.csv` /
`chip_groups.gpkg` under `jhb_full382_unified_A_merge01_c0925_fpcut_2026-06-01_chipgroups/`);
imagery is being re-downloaded fresh to
`~/zasolar_data/geid_temporal/basemap_rebuild_2026-07-13/chips/`.

**Replacement.** The harness's smoke stage now accepts `--labels-csv
<path>` (columns `target_id,frame_date,label`, `label ∈
{present,absent,unsure}`; `unsure` rows excluded) as an alternative to the
dead `--scan-states` path — see `_labels_from_csv` /
`scripts/validation/pilot_c0_reverse_template_2026_07_12.py`. Labels are
**manual annotations on a pilot2023 subset**, drawn as follows:

- Source population: `gehi_vintage_candidates_pilot2023.csv` under
  `basemap_rebuild_2026-07-13/` (a 2023-vintage-restricted slice of the same
  41,393-target full-rebuild anchor set — pilot2023 and the full candidates
  list cover the IDENTICAL set of target ids, so "pilot2023 subset" here
  means a date-restricted subset, not a distinct target population).
- Sample: `sample_label_template_targets()` draws `n=150` target ids via
  `random.Random(seed=20_260_713)` over the SORTED distinct `anchor_id` set
  (deterministic, independent of the source CSV's row order). Run via
  `--stage label_template --pilot2023-candidates <csv> --label-template-out
  <csv> --label-template-n 150 --label-template-seed 20260713`.
- **Disjointness from main eval.** There is no pre-existing main-eval /
  smoke split to consume (pilot2023's target-id set is identical to
  full's) — the draw above CREATES the reservation: the 150 sampled target
  ids are written to a companion `..._reserved_targets.csv` file
  (`emit_label_template(..., reserved_out=...)`) and MUST be excluded from
  the main-eval target pool when that pool is assembled, keeping the smoke
  annotation disjoint from main eval by construction. Zero Gemini
  involvement in the smoke labels (pure human annotation of the
  `.nomarker` GEHI crops) — the blind-lock (C0 verdicts locked before
  reading fresh Gemini verdicts) stays intact.
- The template CSV has one blank-`label` row per (sampled target,
  available pilot2023 capture date) for the annotator to fill with
  present/absent/unsure directly.

**Bars unchanged.** The Stage-0 GO/KILL bars are untouched by this
amendment: pooled AUC ≥ 0.75 (`c0_smoke_auc_go` / `check_c0_s0` in
`check_student_path_gate.py`), ≥ 60 transition anchors
(`c0_smoke_min_anchors`) — only the *source* of the per-frame present/
absent labels feeding those computations changed, not the stratified-AUC
math, the area-tertile stratification, or the δ=0 ablation. The
`smoke_metrics_*.json` output now also records `c0_smoke_label_source`
(`"manual_csv_2026_07_13"` vs the legacy `"legacy_scan_states"`) for
provenance.

## Amendment 2026-07-13 — basemap96 stack adapter (embed/curve)

The embed and curve stages now support the `basemap_rebuild_2026-07-13`
per-target layout (`chips/<target_id>/z<zoom>/<target_id>_<capture_ymd>_
v<vintage_ymd>.tif` + sibling `.tfw`) via `--stack-format basemap96`
(mutually compatible with the original `--stack-format legacy` scan-based
enumeration, which is unchanged). Notable deltas from the tight12 design
above:

- **Geometry.** The per-target tif IS the scoring crop (no render-crop
  call — `ensure_single_target_review_png`'s marker-offset crop logic does
  not apply, since each basemap96 target already has its own tif). This is
  recorded under a DISTINCT `geometry_version` (`basemap96_z19_v1`, see
  `scripts/temporal/chip_geometry.py`) rather than reusing
  `chip_geom_v2_tight12`, so config-hash provenance cannot conflate the two
  geometries. Realized crop extent is ~95–97 m (chip_targets.csv's nominal
  96 m, inflated slightly by GEHI's tile-snapped download bbox) — recorded
  per anchor as `realized_crop_w_m`/`realized_crop_h_m` in
  `curve_anchors_*.csv`.
- **Footprint projection.** Each frame ships an exact `.tfw` world file
  (verified EPSG:3857, GEHI z19 tile resolution). The curve stage projects
  the census polygon through its OWN native CRS (verified EPSG:32735 / UTM
  35S for the `full382_merge01_2026-05-15` GPKG — NOT EPSG:4326, which the
  legacy `_footprint_mask`'s aeqd path incorrectly assumes; the basemap96
  path reads the GPKG's own `.crs` instead) → EPSG:3857 → the frame's TFW →
  the patch-token grid, exactly, rather than the legacy aeqd approximation.
  `fp_source` records `tfw_polygon` when this succeeds or `tfw_radius_disk`
  when it falls back to a disk centred on the target's own centroid.
- CRS/provenance findings, real-target verification (3 targets, 2 vintage
  frames each, `fp_source=tfw_polygon` on all 3), and open questions are
  recorded in the ISSUE-09 tracker entry dated 2026-07-13.

<a id="c0-math-amendment-2026-07-15"></a>

## Amendment 2026-07-15 — mathematical audit and decoder re-freeze

Date: 2026-07-15 · Status: **ADOPTED AS A BINDING DESIGN AMENDMENT; current
implementation non-conformant and evaluation-blocked**.

**Trigger.** Before Stage-0 or held-out evaluation, the pinned decoder math was
subjected to three independent cross-checks plus direct source-level
reproduction against
`scripts/validation/pilot_c0_reverse_template_2026_07_12.py`. The audit found
that the 2026-07-12 implementation does not represent the scientific
hypothesis stated above. In particular, it can assign high install-gap mass to
post-census dates, template self-matches, downward changes, and variance-only
changes; its per-series empirical prior has severe `T=3–4` pathologies. The 48
existing C0 tests pass but do not exercise these cases.

**Effective-date rule.** This amendment supersedes only the **template
measurement / decode / confidence** clauses below. The score/backbone arms,
manual-label reservation, basemap96 geometry amendment, paired fresh-Gemini
evaluation design, and already-pinned GO/KILL bars remain in force except where
this amendment explicitly changes their inputs. No Stage-0 GO/KILL result and
no held-out C0 interval produced by the pre-amendment implementation counts as
preregistered evidence.

### Audit adjudication

The eight questions in the external-cross-check brief are resolved as follows.

| # | Question | Adjudication | Binding consequence |
|---|---|---|---|
| 1 | weighted NIG marginal | **Confirmed, high severity.** Code is a power/fractional likelihood, not the documented `N(mu, sigma²/w)` likelihood. | Replace the likelihood; do not describe `sum(w)` as Gaussian variance degrees of freedom. |
| 2 | whole-series empirical-Bayes `mu0` | **Confirmed, high severity.** The null is centered for free while every split pays duplicated prior-centering penalties. | No per-target whole-series mean prior. Hyperparameters come from a frozen calibration artifact. |
| 3 | first-difference `beta0` at small `T` | **Confirmed, critical at `T=3–4`.** Signal contaminates the noise prior and interacts with the floor. | No target-level noise prior from two or three differences. |
| 4 | `log(total window days)` on `H_empty` | **Original concern refuted for valid distinct day dates.** Since `sum(gap_days)=total_days`, the current construction already implies `P(H_empty)=0.5`. | Preserve the 0.5 top-level prior unless a later pre-run lock explicitly changes it; write it directly rather than via duplicate duration sums. |
| 5 | `q=max(single-gap mass)` | **Confirmed as an inadequate compound confidence measure.** | Separate change probability from conditional localization concentration. |
| 6 | ring co-change | **Mechanism confirmed; empirical prevalence unknown.** Persistence cannot restore a signal cancelled or reversed before decode. | Add robust-ring/support diagnostics; keep the primary ring arm only with the stated sensitivity. |
| 7 | one-frame pre regime | **Confirmed, with stronger persistence failures.** | Clean output requires minimum independent support on both sides and a monotone persistence rule. |
| 8 | general `T=3–5` reliability | **Confirmed, high severity.** | `T=3–4` cannot enter the clean bucket before a separately preregistered calibration demonstrates otherwise. |

Two reproduced failures are binding regression fixtures, not illustrative
anecdotes:

1. For `s=[0,0,A]` at equally spaced dates, every non-floor amplitude gives
   approximately `p_nochange=0.72836`, true-gap `q=0.20902`; a larger perfect
   step cannot rescue the canonical `T=3` sequence.
2. For `s=[0,0,A,A]`, `p_nochange` rises from `0.000675` at `A=0.1` to
   `0.869955` at `A=1` and `0.990743` at `A=2`. A stronger perfect step becomes
   more confidently no-change. This behavior is forbidden by the replacement
   model's property tests.

### Normative amendment text

**M1 — Census-conditioned support is mandatory.** Let usable, distinct,
non-template acquisition dates be `d_1 < ... < d_T`, and let `T_c` be the
per-target census-known-present date. An interior candidate gap has feasible
length

\[
\ell_k=\max\{0,\min(d_{k+1},T_c)-d_k\}.
\]

Only gaps with `ell_k > 0` may receive install posterior mass. A gap wholly
after `T_c` has exactly zero prior; a gap crossing `T_c` ends at `T_c`. The
decode input and output artifact MUST carry the per-target `T_c`, and every
emitted interval MUST satisfy `interval_end <= T_c`.

The final hypothesis/action space MUST distinguish:

- `H_before`: installation at or before the first usable acquisition;
- `H_k`: installation in feasible interior gap `k`;
- `H_terminal`: installation in `(d_T,T_c]` when `d_T < T_c`;
- `H_failure`: no detectable score response / model misspecification.

Until train-pinned state-emission/prior terms for these boundary hypotheses are
frozen, non-interior outcomes MUST be reported only as
`no_detectable_step`; the decoder MUST NOT infer `present_before_window` from
the sign of a stationary-series mean.

**M2 — Template-contributing frames are state constraints, not exchangeable
score observations.** Every frame used to build the template is excluded from:

- the Stage-0 smoke AUC;
- the Bayesian/likelihood decoder;
- GLR/constrained-step cross-checks;
- persistence counting.

Its known-present state constrains install-time support under M1. If a template
uses multiple present frames, each constituent frame is scored only by a
leave-one-reference-out template when a diagnostic self-score is required.
`is_anchor` exclusion is fail-closed and recorded in every metrics artifact.

The Stage-0 bar remains pooled AUC `>=0.75` on `>=60` transition anchors, but
an eligible transition anchor now requires at least one **non-template**
present frame and one absent frame. Anchor-included AUC is diagnostic-only and
cannot satisfy the GO bar.

**M3 — Replacement statistical model: one-sided mean step, shared residual
variance.** The two-independent-NIG-segment model is retired. For each feasible
interior gap `k`, the primary model is the proper precision-weighted Bayesian
linear step

\[
s_t=\mu+\delta\,\mathbf 1(t>k)+\epsilon_t,\qquad
\delta>0,\qquad
\epsilon_t\sim N(0,\sigma^2/r_t),
\]

with one common residual variance `sigma²` across both regimes. `H_failure` /
no detectable step uses the corresponding intercept-only model. A variance
change or a downward mean change is not an installation hypothesis; if later
retained as a diagnostic, it receives a separate non-install label and cannot
produce `verdict=interval`.

Implementation MAY use conjugate Bayesian regression with a one-sided step
coefficient. If an unconstrained conjugate marginal is used internally, the
one-sided marginal MUST include the prior/posterior truncation factor for
`delta>0`; applying directionality only after posterior normalization is not
conformant.

The regression prior (`b0`, covariance/scale, `alpha0`, `beta0`) is fitted or
selected exactly once on a declared calibration partition, serialized with the
partition manifest and source revision, and frozen before smoke/held-out
scoring. It is never re-estimated from a target's complete score series. In
particular, `mu0=mean(s)` and `beta0` from that target's first differences are
retired.

**M4 — Weight contract is proper and single-valued.** Reliability values
`r_t` are relative observation precisions in `(0,1]`, not fractional replicate
counts. For the stated proper likelihood

\[
s_t\sim N(\mu+\delta I_t,\sigma^2/r_t),
\]

the variance-shape update is based on the integer observation count `n/2`, not
`sum(r_t)/2`; the Gaussian normalization contains
`0.5*sum(log r_t) - n/2*log(2*pi)`.

The implementation MUST distinguish:

- unreadable/corrupt/missing frames: excluded, with an explicit reason;
- readable low-quality frames: retained with `0 < r_t <= 1`.

No quality mapping may create `r_t>1`. The same retained frame set and
reliability interpretation apply to prior calibration, the primary likelihood,
and weighted cross-checks. Unweighted fits remain diagnostics only and cannot
veto a clean result. Any later generalized/power-likelihood arm requires a new
pre-run amendment that pins its global learning-rate/temperature; it is not an
implementation shortcut permitted by this text.

**M5 — Prior factorization is explicit.** The valid-date behavior of the
2026-07-12 duration terms is written as a two-family prior:

\[
P(H_{interior})=1-\pi_0=0.5,
\qquad
P(H_{noninterior})=\pi_0=0.5,
\qquad
P(H_k\mid H_{interior})=
\frac{\ell_k}{\sum_{j\in\mathcal K}\ell_j}.
\]

Before boundary calibration, `H_noninterior` is reported only as
`no_detectable_step`. Once the boundary model is live, its 0.5 family mass is
partitioned among `H_before`, `H_terminal`, and `H_failure` by the mandatory
pre-run calibration lock; no post-hoc sign rule is permitted. Any change to the
0.5 family split itself requires a new pre-run amendment.

This corrects the rationale, not the valid-date 0.5 behavior. A
`gap_prior_by_days=False` sensitivity, if retained, keeps the same 0.5/0.5
family split and distributes the interior 0.5 uniformly over feasible gaps; it
MUST NOT use the old equal-over-all-hypotheses behavior in which the lone
noninterior hypothesis had mass `1/T`.

**M6 — Detection and localization confidence are separate outputs.** Every
decode artifact serializes the full normalized hypothesis posterior and
reports at minimum:

- `p_interior_step = P(H_interior | s)`;
- `p_noninterior` before boundary calibration, then separately named
  `p_before`, `p_terminal`, and `p_failure` once the boundary model is live;
- `p_install = 1 - p_failure` only after those boundary terms are identified;
- posterior over feasible gaps conditional on an interior step;
- the shortest contiguous calendar interval carrying 80% of conditional gap
  posterior mass;
- single-gap maximum mass `q_single` as a diagnostic only;
- all tied MAP gaps / intervals, not an undocumented earliest-gap tie break.

The old rule `clean iff q_single >= 0.8` is suspended. Replacement numeric
thresholds for `p_step`, conditional interval width/mass, and any action loss
are selected only on the frozen calibration partition, added to the run lock,
and frozen before the first smoke/held-out decode. Exact-bin concentration may
be reported but cannot be the sole localization gate.

**M7 — Cross-check semantics are corrected.** The current `isotonic_step()`
implementation (unconstrained GLR followed by a direction veto) is not an
isotonic/constrained estimator and is retired under that name. The required
cross-check scans all feasible gaps under the same retained rows and
reliabilities and minimizes weighted two-level SSE subject to
`mean_post > mean_pre`. GLR and its own polarity check count as one diagnostic,
not two independent estimators.

A clean candidate does not require exact equality between a calendar-prior MAP
gap and a prior-free likelihood split. Instead, the weighted constrained split
must fall inside the primary model's preregistered conditional credible
interval. The artifact separately records whether adding the calendar prior
changed the likelihood-only mode.

**M8 — Persistence means a persistent regime.** Clean output requires at least
two consecutive, distinct-acquisition, non-template post-gap frames. Duplicate
capture dates or duplicate image/content hashes count once. The first two such
frames must be post-like under the frozen directional rule, and a later return
to the pre regime vetoes clean status. A mere count of any two values above a
midpoint is not conformant.

A candidate gap that cannot possibly satisfy the persistence requirement is
not silently treated like an ordinary clean-eligible gap. It is either removed
from the clean action space or reported as `interval_needs_confirmation`;
posterior mass and action eligibility are both serialized. The census/template
known-present constraint may serve as boundary confirmation only when encoded
explicitly under M1/M2.

**M9 — Small-stack rule.** After template exclusion and date/content
deduplication:

- fewer than three usable observations: `insufficient_frames` as before;
- `T=3–4`: diagnostic posterior only, never `clean`;
- clean interior intervals require `T>=5` and at least two distinct
  non-template observations on each side of the candidate gap;
- any future relaxation requires a preregistered simulation/calibration result
  stratified by `T`, weight pattern, and date geometry.

**M10 — Correlation, duplicates, and input domains fail closed.** Before any
marginal/fit, the decoder asserts equal one-dimensional lengths; finite scores,
dates, and reliabilities; strictly increasing unique dates after deterministic
deduplication; score range within cosine-contrast support `[-2,2]` up to a
numerical epsilon; and `0 < r_t <= 1`.

Persistence requires independent acquisitions, not repeated rows. The
calibration report includes sensitivity to residual AR(1) correlation at
`rho in {0,0.3,0.6}` (or a stronger continuous-time correlation model frozen
before use), because positive temporal correlation both reduces effective
information and suppresses first-difference variance.

**M11 — Ring robustness remains a required sensitivity.** The primary score
continues to use the registered footprint cosine minus ring cosine, but the
artifact also records footprint and ring components, shifted ring-support
fraction, and failures where ring support clips or vanishes. Shifts with
material ring-support loss are invalid. At minimum one robust diagnostic arm
(median/trimmed patch-level ring similarity or sectorized annuli) is reported;
promotion of a robust arm to primary requires a pre-run amendment, not a
post-hoc choice.

**M12 — Registration, anchor gate, whitening, and run identity are part of the
method.** Before Stage-0:

1. Phase-correlation registration is actually applied to the image/feature
   geometry before scoring, or the prereg claim is explicitly removed in a new
   amendment. Computing a displacement only to set `reg_suspect` is not the
   pinned first registration layer.
2. `theta_anchor` is non-null and frozen. If a left-anchor consistency gate
   cannot be evaluated while a usable right anchor exists, selection fails to
   the right anchor; it does not fail open.
3. The whitening fit consumes only a declared calibration manifest; train /
   smoke / held-out target disjointness is asserted. The whitener content
   digest is stored.
4. The run/config hash covers all active method inputs, including census-date
   manifest, minimum-quality rules, calibration/whitener manifests and
   digests, statistical-prior constants, boundary/confidence thresholds,
   score/backbone/facet/geometry arms, and source revision.

The scratch cross-check brief's statement that shift search maximizes the full
contrast `s_t` is corrected: the official 2026-07-12 method and implementation
maximize footprint cosine, then evaluate the ring at the same shift. Any move
to contrast-maximizing search is a separate arm and cannot be introduced
silently.

### Mandatory regression and property tests

The replacement decoder/measurement implementation is not prereg-conformant
until all of the following pass in `tests/validation/test_c0_pilot.py` or a
successor test module:

1. every emitted interval ends on or before the per-target `T_c`;
2. template-contributing frames are absent from smoke AUC, decode,
   cross-checks, and persistence;
3. exact `T=3` and `T=4` plateau steps exercise the reproduced failures above;
4. within score support, increasing a fixed clean upward step cannot reverse a
   step decision toward no-change solely through prior construction;
5. a pure variance change cannot produce an install interval;
6. a downward change cannot receive install-gap posterior mass;
7. the true constrained-upward split is found when the unconstrained GLR's
   best split is downward (fixture `s=[0,1,10,0,0]`);
8. alternating/reverting post sequences cannot be clean (fixture
   `[-1,-1,-1,-1,-1,-1,-1,1,-1,1,-1]`);
9. final-gap / boundary-censoring behavior is explicit and cannot silently
   fail only because one observed post frame exists;
10. a low-reliability outlier cannot dominate prior fitting or a mandatory
    unweighted veto;
11. duplicate dates/content cannot create a zero-width clean interval or count
    twice toward persistence;
12. NaN/inf/out-of-range scores and invalid reliabilities fail closed;
13. valid distinct-date duration priors imply the documented top-level 0.5
    no-step mass; the uniform-gap sensitivity also retains that 0.5 mass;
14. anchor-excluded smoke AUC is the gate-bearing number, with an explicit toy
    preventing a self-anchor from manufacturing the `0.75` GO threshold.

### Calibration lock and stop discipline

The existing 150-target manual-label reservation MUST be subdivided by a
committed deterministic manifest into non-overlapping calibration and Stage-0
smoke target sets before labels or scores are used to choose replacement prior
or confidence values. Exact membership, counts, seed/rule, and manifest digest
are added to this document or a linked pre-run lock record. Main-eval targets
remain disjoint from both.

No implementation choice left open by M1–M12 may be selected after examining
held-out C0-vs-Gemini results. Any unresolved numeric value is a hard blocker,
not permission to use the 2026-07-12 default. ISSUE-09 remains open and blocked
until the implementation delta, calibration lock, and mandatory tests are
landed; only then may Stage-0 be rerun under this amended prereg.

<a id="c0-p0-amendment-2026-07-16"></a>

## Amendment 2026-07-16 — c0_r1 adjudication gate and fresh-Gemini provenance completion

Date: 2026-07-16 · Status: **ADOPTED; implemented and tested the same day.**
Trigger: the two P0 blockers in the consolidated review
[`ISSUE-09-c0-final-review-2026-07-16.md`](ISSUE-09-c0-final-review-2026-07-16.md)
(owner decision 2026-07-16: adopt the review's recommended option for both).

### A. Kill rule `c0_r1`, rule 2 — posterior gate replaces the point estimate

The main-eval section above says "all three must hold, CI bounds respected",
but the harness implemented rule 2 as a bare point fraction; independent
verification showed a CI-bound reading of the old `n ≥ 40` bar would demand an
observed win share of 50% (20/40), and an observed 40% win rate would need
n≈184 to clear a Wilson lower bound of 1/3. Both readings are rejected in
favor of an explicit Bayesian gate:

- **Rule 2 (amended):** with `k` = adjudicated disagreements where C0 was the
  correct channel out of `n` adjudicated,
  `P(p_C0 > 1/3 | k, n) ≥ 0.95` under a Jeffreys `Beta(1/2, 1/2)` prior.
- **Sample floor raised:** `n ≥ 150` (was 40), still stratified by footprint
  area as pinned above. At `n = 150` the gate passes from an observed share of
  0.400 (`k = 60`, posterior 0.957) — aligned with the design intent that a
  true win rate near 40% demonstrates an independent replicate's worth of
  signal, while the 1/3 boundary itself cannot pass.
- **Metrics contract:** the eval harness must emit the integer count
  `c0_adjudication_correct_n`; the reported share is cross-checked against
  `k/n` (tolerance 0.005) and the rule fails closed on mismatch, on `k > n`,
  and on missing keys.

Implemented in `check_student_path_gate.py` (`check_c0_r1`,
`C0_ADJUDICATION_*` constants) with regression tests in
`tests/validation/test_c0_pilot.py`, including a fixture where the old
point-estimate bar passes (54/160 = 0.3375 > 1/3) and the posterior gate
correctly kills (posterior ≈ 0.55).

The rules 1 and 3 bars and the "adjudication share" language elsewhere in
this document are unchanged in intent; where the old "≥ 1/3, n ≥ 40" wording
appears, this amendment governs.

### B. Fresh-Gemini round provenance completion

The paired eval's reproducibility demands request-identity coverage the
2026-07-15 audit found incomplete. Changes (all landed in code, effective for
every scoring row from this date):

1. **Temperature** is now a named constant (`GENERATION_TEMPERATURE = 0`,
   `gemini_solar_image_review.py`) used by both request builders and hashed
   into `prompt_config_fingerprint` — it was previously hardcoded per-call and
   absent from provenance.
2. **Image preprocessing** is pinned and hashed:
   `image_preprocessing = "raw_bytes_base64_no_transform"` (chips are sent as
   raw base64 bytes; `chip_sha256` in the sidecar already binds the exact
   scored bytes).
3. **Image order** is pinned and hashed
   (`image_order_rule = "picks_order_chip_index_1based"`), enforced fail-loud:
   `score_batch_with_fallback` raises if picks do not arrive in chip_index
   order 1..N, and each batch-attempt audit record now carries the explicit
   `image_order` list.
4. **Rendered prompt persisted:** each batch-attempt audit record now stores
   the exact rendered prompt. This captures the only rendered capture-date
   string that reaches the model in batch mode — the census-calibration
   suffix's `ref_date`. **Documented fact:** batch mode sends NO per-chip
   capture dates to the model; per-chip dates exist only in the sidecar.
5. **Hash discontinuity, on purpose:** adding fields 1–3 to the fingerprint
   changes `prompt_config_hash` for all rows scored from this date. No fresh
   Gemini round has started, so no comparison set is split; any pre-amendment
   rows are identifiable by their old hash.

Tests: `tests/temporal/test_presence_scorer.py`
(`test_gemini_fingerprint_pins_request_identity_fields`),
`tests/temporal/test_gemini_batch.py` (audit prompt/order round-trip,
out-of-order rejection).
