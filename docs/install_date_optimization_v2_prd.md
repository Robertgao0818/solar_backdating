# PRD: Install-Date Optimization v2 — reproducibility program (decoder, provenance, accuracy channel, pipeline shape)

Date: 2026-07-03 · Status: **READY** · Triage: `ready-for-agent`
Scope: `solar_backdating` (install-date sub-line).
Source analysis: [`install_date_optimization_replan_2026-07-03.md`](install_date_optimization_replan_2026-07-03.md)
(9-agent fact-check + exploration workflow; corrections register C1–C7 there is
the evidence base for every decision below).
Relationship to existing PRD: [`dinov3_sat_scorer_backbone_prd.md`](dinov3_sat_scorer_backbone_prd.md)
remains the governing spec for the student-distillation slice (Phase 3 here);
this PRD **amends** its inputs (§Implementation Decisions D12) and supplies the
phases before and after it. Issues for this PRD live in
[`replan_v2/`](replan_v2/) (markdown tracker, same pattern as `dinov3_scorer/`).

Owner decisions locked 2026-07-03: sample-repair API budget **approved**; gold
set adjudicates the **jump critical point only** (transition window, not whole
stacks); student-head training compute = **RunPod first**. Owner decision
2026-08-01: future review is performed by Codex visual review over frozen local
artifacts; no human annotation is required. This is an external AI review
channel, not an independent human gold standard.

## Problem Statement

*From the user's perspective.*

The install-date pipeline answers "when did this PV installation first appear?"
by scanning historical satellite vintages and deciding, per vintage, whether
the panel is present. The project's core values are **data accuracy and
reproducibility**, and today the pipeline delivers neither to the standard the
economic analysis needs:

1. **The result path wobbles.** Re-running the same cohort produces different
   install dates: the adaptive search takes different paths (the dominant
   variance source), and the date-derivation chain (dip-repair → status case
   machine → point/interval extraction) turns small verdict noise into
   dated↔UNDATED flips. The shipped inventory is one favourable draw.
2. **Decisions cannot be audited or replayed.** No record of which scorer
   model version, prompt, or chip pixels produced a verdict exists — the
   hosted LLM can change under us and we cannot even detect it retroactively.
   Every re-run re-pays full API quota and re-exposes us to drift.
3. **Accuracy is unknowable.** Every current metric is agreement-with-self or
   agreement-with-teacher inside one closed loop (same imagery, same scorer
   family). When two methods disagree — e.g. the full-stack experiment dated
   15 anchors that production left undated, 15/15 in the same direction —
   nothing can arbitrate. The user cannot make a citable accuracy claim.
4. **The previously proposed fixes don't fix this.** Fact-checking showed the
   "kill the search, score everything" path's headline win was an estimator
   change in disguise (and is quota-infeasible at scale), while the
   scorer-backbone swap fixes drift and cost but by its own scoping note
   leaves the dominant variance sources untouched. Neither alone gets the
   user to reproducible-and-bounded-accurate install dates.

## Solution

*From the user's perspective.*

A five-phase layered program in which each phase independently improves
reproducibility or accuracy, gates on data already banked wherever possible,
and composes into one endgame architecture:

- **Phase 0 — a principled date decoder.** Replace the ad-hoc derivation chain
  (dip-repair, status case machine, "sustained" heuristic) with one
  deterministic estimator behind a new seam: an exact monotone-changepoint
  posterior per anchor, with a cohort-level interval-censored survival prior.
  "Undated" stops being a status cliff and becomes a probability; every anchor
  gains a credible interval — the deliverable the economic event study
  actually needs. Validated entirely on banked multi-rep data at zero API
  cost, with an isotonic floor as falsification.
- **Phase 1 — provenance and a verdict store.** Record scorer identity, prompt
  hash, and chip content hash for every scoring call, and memoize verdicts in
  a content-addressed store. A cached verdict can never drift: this delivers
  the version-drift kill *before* any student model exists, makes re-runs and
  multi-rep protocols nearly free, and turns "which model dated this roof?"
  into a lookup.
- **Phase 2 — an external review channel that breaks the closed loop.** Use the
  municipal true-flight-date aerial archive (15 cm, in-domain for the existing
  detector) to automatically bracket every dated anchor with independent dated
  presence bits, then have Codex visually adjudicate **only the jump critical
  point** (the claimed absent→present transition window) for a stratified sample
  of 300–500 anchors. This produces a structured external-AI QA result with a
  Wilson interval and arbitrates systematic disagreements no internal metric
  can. It does not create a human-gold or physical-install-date accuracy claim.
- **Phase 3 — the frozen student, on corrected inputs.** Proceed with the
  DINOv3-L-SAT distillation per its existing PRD, amended: corrected cohort
  counts, the chip-manifest dependency made explicit, RunPod as training
  compute, the licence reviewed, and the fidelity gate re-based on the
  Phase-0 decoder with inventory-weighted metrics.
- **Phase 4 — the endgame pipeline shape.** Migrate scoring to a fixed
  exhaustive grid in three steps that each ship alone: student picks the
  windows / teacher still scores (kills search variance with zero trust in
  student verdicts); then student owns steady-state frames and the teacher
  adjudicates only transition windows and abstain-band frames (bounded,
  cached); finally the teacher decays to a periodic sentinel audit. Hosted
  calls drop below today's volume on first run and to near zero on re-runs.

## User Stories

1. As the pipeline owner, I want install dates derived by a deterministic
   decoder instead of a heuristic chain, so that re-running a cohort on the
   same verdicts always yields the same dates.
2. As the pipeline owner, I want each anchor's output to include a posterior
   and credible interval over install epochs, so that downstream economic
   analysis can propagate dating uncertainty instead of trusting point dates.
3. As a researcher, I want "undated" expressed as P(changepoint outside the
   observed window), so that the dated↔UNDATED lottery disappears as a
   category error rather than being patched per status.
4. As a researcher, I want the ~23% of anchors with ambiguous terminal
   statuses recovered as censored observations in a cohort survival model, so
   that they contribute to aggregate curves instead of being dropped rows.
5. As an evaluator, I want the decoder gated on the banked 10-rep panel
   against the sustained estimator's own numbers, so that adopting it costs
   zero new API spend and the comparison is like-for-like.
6. As an evaluator, I want an isotonic/PAVA floor scored under the same gate,
   so that the decoder's added machinery is falsifiable.
7. As an evaluator, I want the reliability panel's dominant stratum enlarged
   (from 2 to ~8–10 units) before any cohort-wide estimator decision, so that
   the population-weighted headline is not driven by two anchors.
8. As an evaluator, I want the under-resolved small-install hypothesis tested
   by re-scoring the failed stratum at a tighter crop, so that we know whether
   that stratum's noise is an imaging artifact or scorer give-up.
9. As a maintainer, I want every scoring call to record scorer identity,
   prompt hash, and chip content hash, so that any verdict is attributable to
   the exact model, instruction, and pixels that produced it.
10. As the pipeline owner, I want a content-addressed verdict store consulted
    before any scorer call, so that a cohort re-run replays byte-identical
    results and pays only for never-seen chips.
11. As a maintainer, I want cache-churn on a sentinel chip set monitored, so
    that silent imagery re-renders surface as an alert instead of as silent
    re-scoring under a possibly-newer model.
12. As a maintainer, I want all scorer call-sites routed through the one
    PresenceScorer seam, so that backbone choice, caching, and escalation are
    single-point decisions rather than per-script copies.
13. As a maintainer, I want the scan state machine's failure vocabulary
    parameterized by scorer identity, so that a non-Gemini scorer's failures
    still trigger the >50%-failed ambiguity rule.
14. As a maintainer, I want declared quality-flag and decision-source
    vocabularies actually enforced at write time, so that a new scorer cannot
    silently emit values downstream tools don't understand.
15. As an evaluator, I want an automated audit that fetches true-flight-date
    municipal aerial chips for every dated anchor and scores them with the
    in-domain detector, so that each inferred interval is checked against
    independent dated presence bits at near-zero cost.
16. As an evaluator, I want the audit self-gated on known-sign strata and on
    monotone consistency with the existing present-clamp, so that the audit's
    own noise floor is measured before it judges the pipeline.
17. As the review operator, I want Codex to adjudicate only the jump critical
    point — the claimed latest-absent and earliest-present frames plus one or
    two flanks, with true-dated aerial overlays — so that a 300–500-anchor
    Codex-reviewed reference set is bounded and reproducible.
18. As the review operator, I want three verdicts (CONFIRM / SHIFT with a
    corrected bracket / UNDATABLE) and a portable strip UI, so that Codex review
    is blind, fast, unambiguous, and recorded in a machine-readable form.
19. As a researcher, I want 20% of the review set re-run in a fresh blind Codex
    pass, so that review-repeat agreement measures the stability of the review
    instrument without being mislabeled as inter-annotator agreement.
20. As a researcher, I want the gold sample stratified by terminal status,
    confidence, and audit-contradiction flag, so that the accuracy claim
    covers the strata where methods disagree, not just easy cases.
21. As a researcher, I want accuracy phrased as first-visible-appearance
    interval-hit-rate with a Wilson CI, so that the claim honestly reflects
    imagery-cadence censoring rather than implying physical install dates.
22. As the pipeline owner, I want the gold set to arbitrate the systematic
    dated-vs-undated disagreements between estimators and production, so that
    "recovers search give-ups" vs "over-dates on thin evidence" stops being a
    matter of interpretation.
23. As an ML engineer, I want the student trained on RunPod with the repo's
    existing pod workflow, so that head training does not depend on local
    VRAM.
24. As a data engineer, I want the distillation label harvest sized from
    re-derived on-disk counts and the chip-manifest dependency made explicit,
    so that training-set stratification is built on real numbers.
25. As an evaluator, I want the student's fidelity gate re-based on the
    Phase-0 decoder under inventory-weighted metrics, so that the student is
    judged against the incumbent it will actually replace.
26. As a maintainer, I want the DINOv3 licence reviewed before the student
    ships, so that a legal constraint does not surface after distillation.
27. As an ML engineer, I want student-teacher disagreement measured per
    stratum on the training cohort (co-teacher calibration), so that the
    escalation band in the hybrid pipeline is set from data, not guesses.
28. As the pipeline owner, I want a first migration step where a rough
    student only *selects* scoring windows deterministically while the
    teacher still issues every verdict, so that search variance dies before
    any trust is placed in student judgement.
29. As the pipeline owner, I want the second step to hand steady-state frames
    to the student with the teacher adjudicating only transition-window,
    abstain-band, and anomaly frames, so that hosted calls stay bounded and
    every transition is still teacher-checked.
30. As a maintainer, I want the escalation set to be a pure function of
    student scores, so that the teacher call count is enumerable up front and
    the selection is replayable.
31. As a maintainer, I want an escalation-rate monitor with an expected band,
    so that student miscalibration raises an alarm without any ground truth.
32. As the pipeline owner, I want the teacher to decay to a scheduled
    sentinel audit on a frozen stratified cohort, so that teacher and imagery
    drift stay observable at negligible cost after the student takes over.
33. As a data engineer, I want an availability-catalog cache for the imagery
    provider, so that full-stack cohort runs are not throttled by 300-second
    catalog timeouts.
34. As an evaluator, I want all cross-method comparisons to follow standing
    rules — like-for-like estimators, inventory-weighted headlines,
    dated-only denominators reported alongside, best-arm baselines, minimum
    stratum support — so that no future headline repeats the fact-check's
    failure modes.
35. As the project owner, I want PRD, issues, and a tracker in repo markdown
    with a rendered HTML view, so that both agents and I can see and update
    program state.
36. As a maintainer, I want every key in the scan config either consumed by
    code or absent, so that editing configuration can never silently do
    nothing (the 2026-07-03 audit found two entire dead sections).
37. As a maintainer, I want each chip's achieved zoom, measured extent, and
    GSD recorded and summarized per run, so that a nominally-z20 cohort
    reports its real resolution mix instead of hiding it in scan-state JSON.
38. As the pipeline owner, I want chip geometry to be a versioned parameter
    of the Phase-3 re-render, decided by the D4 crop experiment, so that
    changing chip size is a deliberate cache-aware migration rather than an
    accident of which builder ran.

## Implementation Decisions

**D1 — Two seams, no more.** (i) **PresenceScorer** (already designed in the
student PRD's first slice): one injected callable per scoring path, extended
to cover **all four** scorer call-sites that exist today (adaptive scan,
census-narrowing scan, full-stack validation harness, chip-group matrix
scorer). The verdict store, the Gemini scorer, the student scorer, and the
Phase-4 escalation compositions are all implementations or wrappers at this
seam. (ii) **InstallDateEstimator** (new): a pure function from one anchor's
ordered per-vintage observations (plus clamp context) to `{posterior over
install epochs, MAP interval, P(undated), credible interval}`. Dip-repair, the
status case machine, and the sustained heuristic are subsumed behind it. The
Phase-2 accuracy channel is deliberately *not* a pipeline seam — it is offline
scripts producing file artifacts.

**D2 — Estimator (Phase 0).** Exact monotone-changepoint enumeration: latent
absent→present step, changepoint τ ranging over observed epochs plus a
right-censored "beyond window" cell; O(T) per anchor. Emissions = 3-symbol
confusion matrix (present/absent/abstain conditioned on state), estimated
cohort-wide by EM, stratified by quality flag, zoom, and imagery era.
Near-duplicate vintages collapse into epochs before decoding (median gap ~31
days; ~42% of gaps ≤30 days) to avoid double-counting correlated errors. The
cohort prior is the discrete-time hazard from a Turnbull NPMLE fit over all
anchors' censoring intervals (ambiguous terminal statuses become censored
observations bounded by the clamp date). Aggregation uses fractional counting
of posterior mass, not midpoint imputation; the **cohort year-histogram
deliverable and the survival curves are that fractional channel** (the
headline reporting object, formalised in D19). A PAVA/isotonic
single-changepoint fit is the mandatory falsification floor.

**D3 — Estimator gates (all on banked data).** MAP mode-hit ≥ 0.911 and
undated-flip ≤ 0.046 on the 10-rep panel (beat sustained on its own turf);
per-stratum year-stability on the dominant stratum ≥ the naive first-present
estimator (the sustained failure mode); HPD calibration proxy (rep-i's 90%
interval contains rep-j's MAP ≈ 90%); **the operative cohort
reproducibility gate is the survival/fractional channel — survival-curve
rep-to-rep TVD beats point-date TVD (D19); the hard-MAP year-histogram TVD
0.037–0.063 band is demoted to a derived diagnostic, retired-with-cause**
(superseded 2026-07-05 by the re-derived production-channel band [0.0243, 0.0787]; ISSUE-21).
Decision on cohort-wide adoption additionally requires the enlarged
dominant-stratum panel (D4).

> **Correction (2026-07-04, record hygiene only — this gate is already
> superseded by the ISSUE-04 re-anchor, see D4/below):** `0.046` was never
> sustained's own banked undated-flip; it is FPD's. `fullstack_noscan_analyze.py`
> (the script behind the original banked `0.911/0.807/0.046` triple) computed one
> shared `undated_flip_rate` from the FPD-derived `derive_install()` undated
> flag and printed it under both the FPD and the sustained row of its table —
> `panel_repair_d8_compare.py` later inherited the identical bug (see the
> ISSUE-04 memo's [Correction](replan_v2/ISSUE-04-decision-memo-2026-07-03.md#correction-2026-07-04)).
> Sustained's true banked-panel undated-flip (`estimator_harness.py`'s
> per-estimator `report_sustained.md`) is **0.075**, not 0.046. "Beat sustained
> on its own turf" on undated-flip was therefore never well-posed at 0.046; the
> live gate is the ISSUE-04-reanchored extended-panel bar (mode-hit ≥0.882,
> undated-flip ≤0.055, pass-at-parity).

> **Amended (2026-07-04, D19 — deliverable caliber; substantive):** the
> year-histogram TVD (hard-MAP) 0.037–0.063 band is **demoted to a derived
> diagnostic and retired-with-cause** (superseded 2026-07-05 by the re-derived
> production-channel band [0.0243, 0.0787]; ISSUE-21); the operative cohort reproducibility
> gate is the **survival/fractional channel** (passes: AC5 survival mean 0.050
> vs point-date 0.075, `beats_point_date: true`). Unlike the record-hygiene
> Correction above, this **does** change the adoption verdict — see
> [`PRD-AMENDMENT-P1-posterior-mass-caliber-2026-07-04.md`](replan_v2/PRD-AMENDMENT-P1-posterior-mass-caliber-2026-07-04.md)
> and D19.

**D4 — Sample repair (budget approved).** Enlarge the reliability panel's
dominant stratum from 2 to ~8–10 units (targeted flash calls), and re-score
the failed-stratum units at a tight crop (~12 m / 256 px) to test the
under-resolution hypothesis. Both results feed the estimator decision and the
student's chip-rendering spec.

**D5 — Provenance record (Phase 1).** Every scoring call records: scorer
identity (model string or checkpoint hash), prompt/config hash, and chip
content hash (sha256 of encoded chip bytes). Stored as a sidecar first — no
scan-state schema bump; a provenance pointer may ride in the existing notes
field. A schema-version bump adding first-class fields is deferred until the
sidecar proves the shape.

**D6 — Verdict store (Phase 1).** Content-addressed KV: key = (chip content
hash × scorer identity × prompt hash × scoring mode) → observation record.
All calls through the PresenceScorer seam consult it first. Keying by pixel
hash (not capture-date/version metadata) is the correctness condition, because
the imagery provider re-renders pixels under stable metadata. Churn monitor:
hash-churn rate on a fixed sentinel chip set, alert on spikes; churned frames
become an escalation class.

**D7 — State-machine decoupling (Phase 1).** The >50%-failed ambiguity rule's
failure sentinel becomes scorer-parameterized (each scorer implementation
declares its failure decision-source); declared quality-flag and
decision-source vocabularies become enforced at write time; new
decision-source values are additive.

**D8 — Standing evaluation rules (immediate).** (1) like-for-like estimators
only; (2) inventory-weighted metrics are the headline, stratified unweighted
means are diagnostics; (3) dated-only denominator reported alongside
all-units agreement; (4) baselines always include the best frozen arm; (5) no
cohort decision from a stratum with n=2 support.

**D9 — Municipal true-date audit (Phase 2a).** For every dated anchor in the
JHB inventory: fetch anchor-centered chips from the municipal 2019 and 2023
aerial layers (true single flight dates, 15 cm), score PV presence with the
existing census detector / classifier (in-domain GSD), and emit per-anchor
dated presence bits plus a contradiction flag against the inferred interval.
Only high-margin presence calls count; low-margin calls route to the Codex
review queue. Self-gates: known-sign strata (>95% expected agreement) and monotone
consistency with the Vexcel present-clamp.

**D10 — Codex-reviewed reference set (Phase 2b, owner decision).** Codex visual
adjudication of the **jump critical point only**: the claimed latest-absent and
earliest-present frames ±1–2 flanks, overlaid with municipal true-date chips,
the Vexcel chip, and any Wayback capture in-window. Verdicts CONFIRM / SHIFT
(corrected bracket) / UNDATABLE. n = 300–500, stratified by terminal status ×
confidence × audit-contradiction flag; 20% receives a fresh blind Codex pass.
Tooling extends the existing per-anchor chip-strip QA HTML builder. Output
metrics: Codex-reviewed bracket agreement and Codex repeat agreement, each with
Wilson 95% CI. These metrics are not human inter-annotator agreement or physical
install-date accuracy.

**D11 — Claim phrasing.** All accuracy claims are phrased as
first-visible-appearance under imagery-cadence censoring; physical install
dates remain out of reach and out of claim.

**D12 — Amendments to the student PRD (Phase 3).** (i) Label-harvest sizing
re-derived from on-disk counts (23,147 unique retained anchors / 250,502
rounds — not 26,820); (ii) the chip-target manifest is named as a hard
dependency for chip re-render (scan states carry no anchor coordinates);
(iii) the ~28% ambiguous-terminal-status fraction is handled explicitly in
training-set stratification; (iv) training compute = **RunPod** (owner
decision), following the repo's pod workflow rules; (v) DINOv3 custom licence
review is a pre-ship task; (vi) the fidelity gate's pipeline-agreement
baseline becomes the Phase-0 decoder under D8 rules; (vii) co-teacher
dual-scoring runs on the training cohort as a calibration instrument (not a
production shape) to measure the disagreement distribution that sets Phase 4's
abstain band.

**D13 — Pipeline shape migration (Phase 4).** Three steps, separately gated:
**E** — student pre-scores the full vintage stack; a fixed deterministic rule
selects 2–3 windows bracketing the coarse transition; the teacher scores
exactly those (call volume ≈ today's; zero trust in student verdicts; can ship
with a rough head before the fidelity gate). **A** — student owns steady-state
frames; teacher adjudicates only abstain-band frames, ± k frames around the
detected transition, and anomaly patterns; the escalation set is a pure
function of student scores (bounded, enumerable, cacheable; expected 7–18% of
frames). **D** — teacher exits the hot path; a frozen stratified sentinel
cohort is re-scored on a schedule and student-vs-current-teacher agreement is
tracked as a drift time series; a Codex review queue remains for high-value
anchors. The review protocol is
[`replan_v2/CODEX_VISUAL_REVIEW_PROTOCOL.md`](replan_v2/CODEX_VISUAL_REVIEW_PROTOCOL.md).
The escalation policy is part of the scorer-side composition at the
PresenceScorer seam (owner declined a third seam).

**D14 — GEHI throughput prerequisite (Phase 4).** An availability-catalog
cache (per region, scheduled refresh) must land before any full-stack cohort
run; full-stack chip renders are staged-and-deleted with content hashes
retained, respecting the documented disk-hygiene constraints.

**D15 — Program tracker.** PRD + issues in repo markdown under the docs tree
(same pattern as the student-PRD tracker); a render script generates a
self-contained HTML tracker view (progress, dependency graph, per-issue
status) from the markdown. Markdown is the single source of truth; agents
edit markdown, humans may read either.

### Amendment 2026-07-03: chip-geometry & resolution audit (D16–D18)

A 4-track adversarially-verified audit of the chip supply chain (19 agents;
full structured output archived in the session workflow journal) established,
with on-disk verification against production artifacts:

- **Chip size is not adaptive in production.** `build_inventory_chip_groups.py`
  hardcodes a fixed 96 m chip — `chip_half_m` = 48.0 on 100% of the 15,859
  production anchors and all ~36,600 anchor rows on disk, against installation
  footprints spanning 3.7–3,376 m². The adaptive clamp formula exists only in
  the orphaned pilot `build_gt_anchor_manifest.py` (where it works correctly);
  the production constant (48 m half) even exceeds the config's own documented
  45 m ceiling.
- **Two YAML sections are dead configuration.** The repo's sole YAML loader
  (`scan_config.load_config()`) reads only `adaptive_scan:`. The
  `anchor_manifest:` section (chip_min/max_half_m, mask_margin_m,
  search_radius_m) and the `gehi_download:` section (download_zoom_candidates,
  default_download_zoom, an output_root that does not exist on disk) are read
  by nothing; editing them changes nothing.
- **Resolution degrades through three silent channels.** (i) GEHI's binary
  substitutes coarser tiles (up to 2 zoom levels, nearest-neighbour upsampled)
  per 256 px tile inside a nominally-successful download — `actual_zoom`
  records only the ladder rung, not delivered pixels, and nothing downstream
  knows; (ii) the skip-existing cache pins each anchor/date to the
  first-cached zoom forever (neither production orchestrator exposes an
  overwrite path, so a ladder upgrade has zero effect on cached anchors);
  (iii) `run_census2023_scan.py` hardcodes its own lower ladder (19,18)
  independent of config. Empirically the JHB census achieved z20 on only
  72.5% of its 172,913 chip-dates (26.8% z19, 0.7% z18; the Wayback corpus is
  97% z19), visible only in raw scan-state JSON — no summary reports it.
- **Downstream is comparatively clean.** No resize/re-encode before the
  scorer; multi-zoom chips are sent as separate image parts; the tile-snap
  requested-vs-actual extent error is ≤0.9% and the nominal-`chip_size_m`
  marker math is off by ≤1.4% — accepted and documented, not fixed (D18).

**D16 — Config truth & dead-knob removal (Phase 1).** Delete or explicitly
annotate-as-dead the `anchor_manifest:` and `gehi_download:` YAML sections;
the zoom ladder becomes single-sourced (the census-narrowing scan either
consumes the same config or carries an explicit in-file justification for
divergence); a consumed-keys test asserts every key present in
`geid_anchor_presence.yaml` is read by code, so future config drift fails
loudly instead of silently doing nothing.

**D17 — Resolution provenance & cache escape (Phase 1).** Chip-side
provenance joins the D5 sidecar: requested ladder, achieved rung, measured
raster extent and GSD, content hash. The download path gains an explicit
cache-refresh escape hatch (overwrite / minimum-zoom re-fetch), closing the
first-cached-zoom pin. Every scan summary surfaces the achieved-zoom
distribution. A sentinel-set effective-resolution estimate
(sharpness/frequency based, self-gated on known z18/z19/z20 chips) detects
in-chip tile substitution that rung-level `actual_zoom` cannot see. D2's
zoom-stratified emission matrices key on **achieved** zoom, not requested.

**D18 — Chip geometry is a gated decision, not a hotfix (Phase 3 coupling;
owner decision 2026-07-03).** No retro-wiring of adaptive sizing into the
legacy builder mid-program: chip pixels are verdict-store keys (D6) and the
banked reps' comparability depends on the frozen 96 m geometry. The D4
tight-crop re-score is the experiment that decides whether geometry
materially affects scorer accuracy; its outcome lands at the Phase-3 chip
re-render (chip-target manifest → renderer, D12.ii), where chip geometry
becomes an explicit, versioned, provenance-recorded render parameter. Any
future geometry change is a deliberate cache-aware migration — never an
accident of which builder ran.

### Amendment 2026-07-04: cohort deliverable caliber (D19)

DECISION-A (2026-07-04) ruled the changepoint decoder NO-GO cohort-wide
solely on the D3 hard-MAP year-histogram TVD band (0.037–0.063; superseded
2026-07-05 by the re-derived production-channel band [0.0243, 0.0787], ISSUE-21),
which failed
on the sanctioned store-backed re-run (flat `[0.079,0.040,0.076]` mean 0.065;
EB prior `[0.092,0.047,0.085]` mean 0.075) after the verdict-store and
stronger-prior remediations were both refuted. Every panel-caliber gate and
the relative survival-reproducibility gate passed. Pre-registered path P1
(owner decision) redefines the headline deliverable; this entry lands it.

**D19 — Cohort deliverable caliber = posterior-mass (fractional); operative
gate = survival/fractional channel (owner decision 2026-07-05: Option A).**
(i) The cohort year-histogram deliverable is the fractional posterior-mass
channel (ISSUE-03 `eval/aggregate.py`), formalising D2's aggregation rule as
the headline object. (ii) The operative cohort reproducibility gate is the
survival/fractional channel — it passes and beats the incumbent (AC5:
survival mean 0.050 vs point-date 0.075, `beats_point_date: true`; same-run
`[0.0504/0.0596/0.0304]` mean 0.0468 vs point `[0.090/0.0314/0.0875]` mean
0.0696; cross-check 3/3). (iii) The hard-MAP year histogram is a derived
diagnostic; its 0.037–0.063 band is retired-with-cause (small-sample fit on 3
pre-store pairs; production reference channel breached it at 0.068 on 1/3
pairs; instability is hard-MAP argmax collapse, not the posterior; superseded
2026-07-05 by the re-derived production-channel band [0.0243, 0.0787], ISSUE-21). (iv)
Production default switches to the changepoint decoder + EB/Turnbull prior
(epoch-gap 45, EM emissions, `cohort_prior.json`; code `89496dd`/`1daa61d`);
effective-date rule per the P1 amendment §4 — **Option A signed 2026-07-05**:
effective at sign-off, with the P3 re-band as condition-subsequent
verification under a pre-registered rollback trigger. (v) P3 band
re-derivation is mandatory follow-up: ≥5 store-backed production-channel reps
(≥10 pairs), pre-registered before decoding, fresh-per-rep stores, never
tuned on the decoder (→ ISSUE-21). (vi) All already-passed panel gates are not
re-litigated; D11 first-visible-appearance scope unchanged; the C5 2024-dip
and grid-marginalisation caveats travel with every deliverable. Full
normative text:
[`replan_v2/PRD-AMENDMENT-P1-posterior-mass-caliber-2026-07-04.md`](replan_v2/PRD-AMENDMENT-P1-posterior-mass-caliber-2026-07-04.md).

## Testing Decisions

A good test in this program exercises **external behavior at a seam** with
recorded or synthetic data — never implementation details, and (for Phases
0–2) never live API calls.

- **InstallDateEstimator seam**: pure-function tests. Fixtures = (i) the
  banked 10-rep × 28-unit frame-verdict table and the 3 end-to-end reps
  (real, messy), (ii) synthetic monotone sequences with injected flip/abstain
  noise at known rates (exactness checks: known changepoint recovered, P(undated)
  correct on all-absent and all-present sequences, epoch collapsing invariant
  to duplicate frames). The D3 gates run as an offline evaluation script whose
  outputs are asserted against thresholds — the gate IS the acceptance test;
  **the operative cohort-reproducibility assertion is the survival/fractional
  channel (D19), so `scripts/validation/issue03_gates.py`'s AC5 survival
  assertion is the gate-bearing threshold and the retired hard-MAP
  year-histogram TVD band is a reported diagnostic, not a pass/fail
  assertion** (see §9.6 of
  [the P1 amendment](replan_v2/PRD-AMENDMENT-P1-posterior-mass-caliber-2026-07-04.md)).
- **PresenceScorer seam**: fake-scorer injection, following the existing
  sequence-scoring unit tests that already inject a fake scorer callable —
  extend that prior art to the adaptive-scan and census-scan paths. Verdict
  store: replay test (a completed cohort slice re-run through the store
  produces byte-identical scan states); cache-miss accounting test (second
  run issues zero scorer calls); churn-monitor test with a mutated chip.
- **State-machine decoupling**: unit tests that a non-Gemini scorer's declared
  failure source still triggers the ambiguity rule, and that unknown
  quality-flag/decision-source values are rejected at write time.
- **Municipal audit**: known-sign strata assertions and clamp-monotonicity
  run as self-gates inside the audit script; unit tests use a stubbed layer
  fetcher and a stubbed detector.
- **Gold-set tooling**: golden-file test on the strip-HTML builder (existing
  QA-HTML builder tests are prior art); verdict-manifest round-trip test.
- **Config truth & resolution provenance (D16/D17)**: a consumed-keys test
  over `geid_anchor_presence.yaml` (fails on any key no code reads — verified
  by adding a dummy key); unit tests that the cache escape hatch re-fetches an
  anchor/date pinned at a lower zoom and that scan summaries carry the
  achieved-zoom histogram; the sentinel effective-resolution estimator must
  separate known z18/z19/z20 chips before it gates anything.
- **Phase 4 policies**: property tests that the window-selection and
  escalation rules are pure functions (same inputs → same selection), plus
  budget-bound tests (selection size ≤ enumerable bound).
- Reliability/agreement protocols (mini-reliability, full-stack panels) are
  *evaluation harnesses*, not CI tests — they run on demand and their outputs
  are archived artifacts.

## Out of Scope

- CT and Wayback cohort backdating runs (all CT numbers remain projections;
  the SSEG prior is scheduled for the future CT run, not JHB).
- Beating Gemini's accuracy (Phase 3 remains match-not-beat); any claim about
  physical install dates as opposed to first visible appearance.
- Pure Path 1 (Gemini full-stack in production), per-frame K-rep consensus at
  cohort scale, bi-temporal pair scoring, SITS/temporal foundation models,
  BOCPD, permits/foreign-dataset gold sources — all rejected with reasons in
  the replan's §4; do not re-investigate.
- Changing detection-side semantics (V1.3/V1.4), the aerial census, or the
  classifier subrepo.
- A persistent GPU worker / queue architecture for scoring (noted as the
  right long-term shape elsewhere; not part of this program).
- Sequence-aware student heads (temporal transformer over frozen embeddings)
  — deferred until the per-chip student passes its fidelity gate.

## Further Notes

- The corrections register (C1–C7) in the replan doc is normative context for
  implementers: several numbers quoted in older docs (26,820; "pv_score";
  0.911-vs-0.875 as a pipeline comparison; the hand-authored 0.74) are wrong
  or misleading; when in doubt, re-derive from artifacts.
- Phases 0, 1, and 2a are independent and can run concurrently. Phase 3
  starts after 0 + 1 land (gate baseline + seam). Phase 4-E needs Phase 3's
  scaffold (a rough head suffices); 4-A needs the fidelity gate; 4-D needs
  4-A in production.
- The repo plans entirely in `docs/` markdown; the GitHub issue tracker is
  unused. Issues for this PRD: `docs/replan_v2/ISSUE-*.md` + `TRACKER.md`,
  rendered to `tracker.html` by a script (D15).
- Provenance: this PRD synthesizes the 2026-07-03 fact-check/exploration
  workflow (5 verification clusters, 4 exploration lenses, 9 agents; full
  structured output archived in the session workflow journal).
- Provenance (amendment): the 2026-07-03 chip-geometry & resolution audit
  (4 investigation tracks + adversarial verification, 19 agents) is the
  evidence base for D16–D18 and issues 17–19; verified findings are
  summarized in the amendment block above, full structured output archived
  in the session workflow journal.
- Provenance (amendment): the 2026-07-04 cohort-deliverable-caliber amendment
  (P1 + P3, D19) executes DECISION-A's pre-registered path P1; it redefines
  the headline cohort deliverable as fractional posterior mass and the
  operative gate as the survival channel, and schedules the P3 band
  re-derivation. Owner signed **Option A** 2026-07-05; adoption effective at
  sign-off. Evidence base:
  [`replan_v2/DECISION-A-estimator-adoption-2026-07-04.md`](replan_v2/DECISION-A-estimator-adoption-2026-07-04.md),
  ISSUE-02, ISSUE-03; full amendment in
  [`replan_v2/PRD-AMENDMENT-P1-posterior-mass-caliber-2026-07-04.md`](replan_v2/PRD-AMENDMENT-P1-posterior-mass-caliber-2026-07-04.md).
