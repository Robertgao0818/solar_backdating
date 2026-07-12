# DATA — Path C0 prereg: training-free census-anchored reverse template matching (2026-07-12)

Status: **DRAFT — numeric bars open to owner adjustment until the first eval
run; frozen at first run.** Tracker slot: [ISSUE-09](ISSUE-09-c0-reverse-template-matching.md).
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
