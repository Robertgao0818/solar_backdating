# PRD: Swap the install-date presence scorer backbone from Gemini to DINOv3-L-SAT

Date: 2026-06-29 · Grilled & finalized: 2026-06-30
Status: **READY — grilled (Q1–Q10 resolved)**

> **Status update (2026-07-03)**: this PRD is now **Phase 3** of the
> install-date optimization v2 program — see
> [`install_date_optimization_v2_prd.md`](install_date_optimization_v2_prd.md),
> which **amends this PRD's inputs (§D12)**. The Slice-1 `PresenceScorer` seam
> is superseded by
> [`replan_v2/ISSUE-05`](replan_v2/ISSUE-05-presence-scorer-seam.md)
> (wider scope: 4 call-sites; **landed 2026-07-03**). The seven D12 amendments
> are **applied in-line below** (2026-07-03, via
> [`replan_v2/ISSUE-12`](replan_v2/ISSUE-12-student-prd-amendments.md)); the
> evidence base is the replan's corrections register (C6/C7). See the
> "D12 amendments" section for the summary.
Scope: `solar_backdating` (install-date sub-line only). This is a **scorer-backbone swap via distillation, not a pipeline rewrite.** The adaptive-scan state machine, dip-repair, interval inference, and Vexcel censoring/clamp are untouched.

## Decision (one paragraph)

Replace the hosted-LLM (Gemini) visual presence scorer with a **self-hosted, frozen DINOv3-L-SAT** encoder (timm `vit_large_patch16_dinov3.sat493m`, 303M distilled-L) + a light head, **distilled from Gemini's own per-vintage labels**. The swap happens at one new seam — a `PresenceScorer` callable that, given an ordered set of `(chip, capture_date)` for one roof, returns per-date `(pv_present, pv_score, quality_flag)`. Everything downstream already consumes only `scan_state.json` and is backbone-agnostic, so it does not change. The primary goal is **reproducibility / no version-drift**: a frozen checkpoint we own cannot silently change under us the way `gemini-3-flash-preview` can. The student is **trained to match Gemini, not beat it** — there is no independent install-date ground truth, so success is measured as *fidelity*, not accuracy.

---

## Grilling decisions (2026-06-30)

| # | Question | Resolution |
|---|---|---|
| Q1 | Why DINOv3-**L**-SAT? | **SAT pretraining** chosen for domain match — backdating scores blurry historical *satellite* imagery (GEHI z=19 ≈ 0.25 m), close to SAT's ~0.6 m pretrain, unlike the 6.7 cm aerial census where this family failed a gate. **L is forced**: the SAT line ships only a 6.7B teacher + this 303M distilled-L; no S/B SAT variant exists. |
| Q2 | Bake-off? | **L-SAT is the lead hypothesis, but DINOv2 ViT-S/14 (22M, proven) is a mandatory cheap falsification floor in the gate.** If 14× smaller beats it, SAT bet is falsified. |
| Q3 | Match or beat Gemini? | **Distillation — match, not beat.** Motivation is reproducibility + cost, not accuracy. |
| Q4 | Which reproducibility? | **(1) version-drift elimination = primary deliverable** (frozen checkpoint; the repro study was blind to this). **(2) run-to-run determinism = bonus experiment** — test whether a *deterministic* scorer collapses the L2-search / L3-imagery variance that the study attributed to a (still-stochastic) LLM. Not gated. |
| Q5 | Per-chip vs sequence-aware? | **v1 = (a) pure per-chip independent** (cheapest, most deterministic, matches the superseded pre-spec). **(b) census-GT reference exemplar = gate-fail backup.** **(c) sequence-aware = deferred** (add later). |
| Q6 | Gate without truth? | **Fidelity gate, three numbers:** (1) DINOv3 self rep↔rep ≈ 1.0 → proves reproducibility; (2) DINOv3-pipeline vs Gemini-pipeline interval agreement — *baseline re-based 2026-07-03 (D12.vi):* both pipelines decoded by the **Phase-0 decoder** under the **D8 standing rules** (like-for-like, inventory-weighted, dated-only denominator alongside); bar = Gemini's own rep↔rep ceiling **re-derived under those rules** (the hand-authored ~0.74 is retired — no script writes it, and it blends strata; replan C7); (3) held-out chip-level present/absent agreement vs Gemini → distillation fidelity. **Accuracy is NOT gated** (no truth exists). |
| Q7 | Training data source? | Labels harvested from the retained `scan_state.json` corpus — **23,147 unique retained anchors / 250,502 rounds** on disk (15,859 chip-group + 7,288 per-target + 11,419 census2023 + 1,641 wayback scan states; re-derived 2026-07-03 — the earlier 26,820 figure was stale, D12.i). **Chip images were deleted** and scan states carry **no anchor coordinates** → the chip-target manifest (`chip_targets.csv`) is a **hard dependency** of the stratified GEHI re-render (idempotent, LLM-free; D12.ii). present/absent from `usable` + high-confidence rounds; `ambiguous`/`unusable` → the unusable class; **27.6% of retained anchors (6,399/23,147) are `done_ambiguous_*`** (marker-misregistration) — handled explicitly in stratification (D12.iii), not as a footnote. |
| Q8 | Training regime? | **Frozen backbone + light head (linear / small MLP), ~500–1000 anchors, NO full fine-tuning.** Frozen ⇒ the FM cannot be "polluted" by noisy pseudo-labels; full-FT on small noisy data is exactly what risks feature collapse. LoRA/PEFT only if the frozen gate fails. |
| Q9 | Anchor conditioning? | **Crop centered on anchor + pool the center k×k patch tokens** (no drawn marker; marker = optional ablation). Anchor is at chip center by construction. |
| Q10 | Sub-domain calibration? | **v1 = single global threshold** (lo/hi abstain band) calibrated on held-out Gemini labels. **Per-sub-domain (CBD aerial-mosaic vs non-CBD satellite) deferred to v1.1**, triggered only if the gate shows cross-sub-domain skew. |
| — | Hand-labeling | **v1 needs none.** Train / calibration / gate all use Gemini labels (held-out by anchor). Hand-labeled install-date GT is an *optional* future effort, only if an accuracy claim is ever required. |

---

## D12 amendments (applied 2026-07-03)

Seven fact-check-driven corrections from the v2 program
([`install_date_optimization_v2_prd.md`](install_date_optimization_v2_prd.md)
§D12; evidence = the replan's corrections register C6/C7), landed in-line in
this PRD and in the issue folder by
[`replan_v2/ISSUE-12`](replan_v2/ISSUE-12-student-prd-amendments.md):

1. **Label-harvest sizing re-derived from on-disk counts**: **23,147 unique
   retained anchors / 250,502 rounds** (15,859 chip-group + 7,288 per-target +
   11,419 census2023 + 1,641 wayback scan states). The 26,820 figure was stale
   and is retired.
2. **`chip_targets.csv` is a hard dependency of the chip re-render** — scan
   states carry no anchor coordinates (→ D6, ISSUE-02).
3. The **27.6% `done_ambiguous_*` fraction** (6,399/23,147 anchors) is a major
   bite out of the harvestable pool, handled explicitly in training-set
   stratification (→ D6, ISSUE-02).
4. **Training compute = RunPod** (owner decision 2026-07-03), per the repo's
   pod workflow rules (→ D3, ISSUE-04).
5. **DINOv3 licence review is a pre-ship task** — weights are ungated but
   under Meta's custom non-permissive DINOv3 licence (→ D2, ISSUE-07).
6. The fidelity gate's **pipeline-agreement baseline is the Phase-0 decoder
   under the D8 standing rules** (like-for-like, inventory-weighted); the
   hand-authored ~0.74 ceiling is retired and re-derived (→ D7, ISSUE-06).
7. **Co-teacher dual-scoring** runs on the training cohort as a calibration
   instrument — not a production shape — measuring the per-stratum
   student–teacher disagreement distribution that sets Phase 4's abstain band
   and escalation k (→ D6, ISSUE-04).

Terminology fix (C6): the persisted scan-state confidence field is
**`confidence`**; **`pv_score` names only the seam-contract field** (D1).
There is no on-disk field named `pv_score`.

---

## Problem Statement

*From the user's perspective.*

The install-date back-dating pipeline answers "when did this known PV installation first appear?" by scanning historical satellite vintages and deciding, per vintage, whether the panel is present. Today that per-vintage present/absent judgement is made by **Gemini** (a hosted LLM vision API) over rendered chips.

The core problem the user wants solved is **reproducibility — the result path must stop wobbling**:

- **Version drift (primary pain).** `gemini-3-flash-preview` can change under us at any time; the same anchor scanned months apart may date differently for reasons we neither control nor observe. The end-to-end reproducibility study was *blind* to this (its reps ran close in time on one Gemini version), so this risk is real and unmeasured.
- **A hosted dependency we don't own.** Decisions cannot be frozen, inspected, or pinned; scoring stalls on external failures (gateway 401s, Gemini 503s on the capable tier) and is throttled by qps / account-pool fan-out.
- **Per-call cost at census scale.** Every vintage of every anchor is a metered call across the JHB 382-grid inventory, the CT 2083 cohort, and the Wayback 2023-census cohort.
- **A robustness tax intrinsic to using an LLM.** The scorer carries JSON salvage, schema validation, a batch→batch→per-image retry ladder, and abstain-on-malformed handling — all only because an LLM emits malformed/missing rows.

Honest scoping note: the reproducibility study found the **adaptive search (L2)** and **imagery selection (L3)** are the *dominant* run-to-run variance sources, with the LLM (L1) the *smallest*. So swapping the scorer is **not** primarily a fix for run-to-run stochasticity — it is a fix for **version drift** (a frozen checkpoint is permanently reproducible) and a removal of the hosted dependency and its cost/robustness tax.

## Solution

*From the user's perspective.*

Keep the entire install-date machine as-is and replace only the brain that looks at a chip and says "panel / no panel / can't tell." The new brain is a **self-hosted, frozen DINOv3-L-SAT** encoder + light head, **distilled from Gemini's own decisions**, run on our GPU.

1. A new `PresenceScorer` seam is introduced. Both the production **adaptive-scan** path and the **sequence/census** path call presence scoring through this one callable. The Gemini scorer becomes one implementation; the DINOv3 scorer another, selected by config/flag. Gemini stays as fallback / A-B comparator.
2. The DINOv3 scorer consumes anchor-centered historical chips and emits the same narrow record downstream expects: per-date `pv_present` (with abstain), a continuous `pv_score` (persisted as the scan-state `confidence` field — the name `pv_score` exists only at the seam, D1), and a `quality_flag`. Because the adaptive-search state machine, dip-repair, interval inference, and Vexcel clamp read only that record, install-date outputs are produced by unchanged code.
3. The student is **trained to reproduce Gemini's per-vintage labels** (harvested from completed scans). Once it does, we run the frozen student instead of the live LLM: same answers, but reproducible from a pinned checkpoint, free of API cost, and free of the LLM-output robustness machinery.

The user gets back a result *path that does not wobble across time*, at GPU batch throughput and zero API spend.

## User Stories

1. As the pipeline owner, I want the presence decision frozen in a checkpoint I own, so that re-running a cohort months later does not silently change because a hosted model changed version.
2. As the pipeline owner, I want the scorer to be self-hosted, so that census-scale back-dating costs no per-call API spend.
3. As the pipeline owner, I want scoring throughput bounded by GPU batch size rather than gateway qps, so that a cohort scans on hardware I control instead of a throttled external queue.
4. As a researcher, I want to distill Gemini's decisions into a small frozen model, so that I keep Gemini's judgement quality without keeping Gemini in the loop.
5. As a maintainer, I want to drop the LLM-output robustness ladder (JSON salvage, retry fallback, abstain-on-malformed) on the self-hosted path, so that failures are perceptual, not parsing.
6. As a maintainer, I want one `PresenceScorer` seam shared by both scoring paths, so that backbone choice is a single config point, not duplicated logic.
7. As a maintainer, I want Gemini retained behind the same seam, so that I can A-B the backbones and fall back if the student underperforms the fidelity gate.
8. As an evaluator, I want to prove the student is reproducible by showing its self rep↔rep agreement ≈ 1.0, so that the primary "no wobble" goal is demonstrated, not asserted.
9. As an evaluator, I want to prove the swap did not change the answers by showing DINOv3-pipeline vs Gemini-pipeline interval agreement reaches Gemini's own rep↔rep ceiling — both pipelines decoded by the Phase-0 decoder under the D8 standing rules (D12.vi) — so that "same dates, just frozen" is demonstrated like-for-like.
10. As an evaluator, I want held-out chip-level present/absent agreement vs Gemini, so that distillation fidelity is localizable (which GSD tier, which terminal-status bucket).
11. As an evaluator, I want a cheap DINOv2 ViT-S/14 floor scored under the same gate, so that the DINOv3-L bet is falsifiable — if 14× smaller matches it, the SAT/L choice is wrong.
12. As a downstream consumer, I want `scan_state.json` to keep its exact schema, so that dip-repair, interval inference, and the Vexcel clamp run unmodified.
13. As a downstream consumer, I want a new `decision_source` value identifying DINOv3-scored rounds, so that provenance is per-round traceable and the Case-E (>50% failed → ambiguous) rule maps to a backbone-appropriate failure source.
14. As a maintainer, I want the adaptive-scan path's scorer injected (not a hardwired local import), so that swapping is symmetric with the sequence path that already injects its scorer.
15. As a data engineer, I want to harvest present/absent labels from the retained scan-state corpus (23,147 unique anchors / 250,502 rounds — D12.i), so that the training set comes free from work already done.
16. As a data engineer, I want to re-download a stratified chip subset via GEHI from the chip-target manifest plus retained metadata (`chip_targets.csv` is the coordinate source — D12.ii), so that I recover the (deleted) training images idempotently without re-invoking the LLM.
17. As a data engineer, I want present/absent drawn only from `usable` + high-confidence rounds and `ambiguous`/`unusable` mapped to the unusable class, so that teacher noise and marker-misregistration cases do not silently corrupt the labels.
18. As an ML engineer, I want the backbone frozen with only a light head trained on ~500–1000 anchors, so that a 303M FM is not feature-collapsed by a small noisy pseudo-label set.
19. As an ML engineer, I want anchor conditioning by pooling center k×k patch tokens of an anchor-centered crop, so that the model scores at the installation, not anywhere in the scene, without an occluding drawn marker.
20. As an ML engineer, I want `pv_score` continuous with a calibrated lo/hi abstain band, so that `pv_present` and `quality_flag` derive from thresholds, not from an LLM's self-reported "unusable".
21. As an evaluator, I want train / calibration / gate split by **anchor** (no leakage), so that held-out agreement measures generalization, not memorization.
22. As the pipeline owner, I want the swap behind a feature flag with Gemini default until the gate passes, so that production back-dating is never blocked on an unproven student.
23. As a researcher, I want a bonus experiment running the deterministic student twice on one cohort, so that I learn whether a deterministic scorer also collapses the L2/L3 run-to-run variance the study blamed on the LLM.
24. As a maintainer, I want region resolution via explicit `region` + the registry (ADR-0002), so that overlap grid IDs are never mis-resolved.
25. As a maintainer, I want weights and re-downloaded chips under `~/zasolar_data/` (not committed), so that the subrepo data-discipline rule holds.
26. As an operator, I want a documented VRAM/throughput profile for the 303M backbone, so that pods are sized without OOM (prior 5090 parallel-OOM history).
27. As the pipeline owner, I want the GEHI vintage-discovery and chip-download stages untouched, so that the change surface is confined to scoring.
28. As a researcher, I want the census-GT reference exemplar (a known-present chip) kept ready as a gate-fail backup conditioning input, so that the most valuable cross-date signal can be added without committing to it upfront.

## Implementation Decisions

### D1 — The seam: a `PresenceScorer` callable (the single highest seam)

Both scoring paths reduce to the same contract; introduce one seam and make both call it.

**Contract** (encodes the decision; shape, not an implementation):

```
# Input: ordered chips for ONE roof across dates (anchor-centered, same crop geometry the pipeline renders)
picks: list[Pick]            # Pick: chip_path, capture_date, version, actual_zoom
# Output: one observation per pick, in order
class PresenceObservation:
    pv_present: bool | None   # None = abstain
    pv_score:   float         # continuous, calibrated; abstain band derived from this
    quality_flag: str         # "usable" | "ambiguous" | "unusable"
    decision_source: str      # e.g. "dinov3_frozen" | "gemini_batch"

def score(picks, *, config, ...) -> list[PresenceObservation]: ...
```

- The **sequence/census path already injects** its scorer — DINOv3 is a drop-in; only a CLI flag to select it is missing.
- The **production adaptive-scan path hardwires** a local import of the Gemini batch scorer. Promote that to an injected callable (mirror the sequence path). This is the only structural refactor.
- Downstream is unchanged: the observation is mapped into the existing persisted round record; `scan_state.json` schema is unchanged.
- **Status 2026-07-03:** the seam **landed** via [`replan_v2/ISSUE-05`](replan_v2/ISSUE-05-presence-scorer-seam.md), with wider scope than this slice planned — all **four** scorer call-sites (adaptive scan, census-narrowing scan, full-stack validation harness, chip-group matrix scorer) route through it (`scripts/temporal/presence_scorer.py`).
- **Terminology (D12/C6):** `pv_score` is the **seam-contract name only**. The persisted scan-state field is `confidence`; the Gemini wrapper maps `confidence`→`pv_score` at the seam and call-sites persisting rounds map it back. No on-disk field named `pv_score` exists.

### D2 — Backbone: DINOv3-L-SAT (lead) + DINOv2 ViT-S/14 (falsification floor)

- **Lead:** DINOv3 ViT-L/16 SAT-493M distilled — timm `vit_large_patch16_dinov3.sat493m` (303M; ungated, but distributed under **Meta's custom non-permissive DINOv3 licence — a licence review is a pre-ship task**, D12.v, tracked in ISSUE-07). Chosen for **satellite-domain match**: GEHI historical chips (z=19 ≈ 0.25 m; z=20 ≈ 0.12 m; z=18 ≈ 0.5 m) sit ~2.4× from SAT's ~0.6 m pretrain, far closer than the ~9× aerial gap that confounded its failed Phase-0 segmentation gate. **"L" is not a size choice — the SAT line offers only a 6.7B teacher and this 303M distilled-L.**
- **Floor (mandatory in the gate):** DINOv2 ViT-S/14 (`vit_small_patch14_dinov2.lvd142m`, 22M) — the proven production classifier backbone, with reusable head/recipe/chip/threshold tooling. If it matches L-SAT on the gate, the SAT/L bet is falsified. GEO-Bench-2 evidence (natural-image pretrain can match satellite-RS on RGB-only) makes this a live possibility, not a formality.

### D3 — Frozen encoder + light head (distillation, no fine-tune)

- **Frozen backbone.** Weights are not updated — a frozen FM cannot be polluted by noisy pseudo-labels. Full fine-tuning of a 303M model on ~500–1000 noisy-labeled anchors is the failure mode to avoid (feature collapse / catastrophic forgetting). LoRA/PEFT is a fallback only if the frozen gate fails.
- **Light head:** linear / small MLP on pooled anchor tokens → **three classes: present / absent / unusable**. `unusable` (cloud/occlusion/off-marker) feeds Case-E recovery downstream.
- `pv_score` = continuous present-class score; `pv_present` / `quality_flag` derive from a calibrated lo/hi band (D5).
- **Training compute = RunPod** (owner decision 2026-07-03, D12.iv): frozen-backbone feature extraction and head training run on a pod per the repo's pod workflow rules (main-repo `.claude/rules/05`/`08` + the runpod-ops skill); local VRAM is not a dependency.

### D4 — Anchor conditioning & chips

- **Crop centered on the anchor** (reuse existing GEHI download + crop geometry); **pool the center k×k patch tokens** as the anchor-conditioned feature. No drawn marker (the marker was for the LLM to read; spatial pooling replaces it). Marker = optional ablation.
- Historical chips are small (z=19 ≈ 67 px) and upscaled to the encoder input; upscaling policy validated in the first ablation, not frozen here.

### D5 — Calibration: single global threshold (v1)

- Calibrate a lo/hi band on **held-out Gemini labels** so the student's present/absent/abstain decisions agree with Gemini's: `score<lo`→absent, `score>hi`→present, between→ambiguous(abstain); unusable as its own class.
- **Per-sub-domain calibration (CBD aerial-mosaic vs non-CBD satellite) is deferred to v1.1**, added only if the gate shows cross-sub-domain skew (the inference-time sub-domain-routing problem is solved then, not now).

### D6 — Training data (distillation set)

- **Labels:** harvest from the retained `scan_state.json` corpus — **23,147
  unique retained anchors / 250,502 rounds** (15,859 chip-group + 7,288
  per-target + 11,419 census2023 + 1,641 wayback scan states; re-derived on
  disk 2026-07-03, D12.i): `(anchor_id, capture_date, version, actual_zoom,
  pv_present, confidence, quality_flag, terminal_status)`. The persisted
  confidence field is `confidence` — `pv_score` exists only at the seam (D1).
  present/absent from `usable` + high-confidence; `ambiguous`/`unusable` →
  unusable class.
- **Ambiguous fraction (D12.iii):** **27.6% of retained anchors (6,399/23,147)
  are `done_ambiguous_*`** (marker-misregistration) — a major bite out of the
  harvestable pool, not a footnote. Exclude or down-weight them as a
  documented per-stratum choice, and size every stratum from the
  post-exclusion pool.
- **Images:** the `chips/` directory was deleted, and scan states carry **no
  anchor coordinates** — the chip-target manifest (`chip_targets.csv`, 41,394
  rows, intact on disk) is a **hard dependency** of the chip re-render
  (D12.ii). Re-render a **stratified subset (~500–1000 anchors v1)** via GEHI
  by joining the manifest to the retained metadata (idempotent, LLM-free,
  rate-limited). Stratify by sub-domain and label balance. Chip geometry at
  this re-render is a versioned, gated render parameter decided by the D4
  tight-crop experiment (v2 PRD D18 / replan_v2 ISSUE-19), not inherited by
  accident from whichever builder ran.
- **Split by anchor** into train / held-out (calibration + gate); no anchor appears in both.
- **Co-teacher dual-scoring (calibration instrument, D12.vii):** once the head
  is trained, both teacher and student score the training cohort; the
  per-stratum / per-GSD-tier student–teacher disagreement distribution sets
  Phase 4's abstain band and escalation k. This is a measurement, not a
  production shape (dual-scoring in production is rejected in the v2 replan).
- Risk: GEHI availability may have shifted since the scan, making a few historical chips unrecoverable — drop those rows.

### D7 — Gate & rollout (fidelity, not accuracy)

- **Feature-flagged.** Gemini default until the gate passes.
- **Three gate numbers** (all reuse / extend existing reliability harnesses):
  1. **Reproducibility (primary):** DINOv3 self rep↔rep interval agreement ≈ 1.0 (deterministic).
  2. **No-answer-change (re-based 2026-07-03, D12.vi):** DINOv3-pipeline vs Gemini-pipeline interval agreement, with **both pipelines decoded by the Phase-0 decoder** and scored under the **D8 standing rules** — like-for-like estimators, inventory-weighted headline, dated-only denominator reported alongside. Bar = Gemini's own rep↔rep ceiling **re-derived under the same rules** on the banked rep panel (reuse `llm_endtoend_analyze`, treating DINOv3 as one rep). The previously quoted **~0.74 is retired as a bar**: it exists only in a hand-authored `derived_cuts.json` no script writes, and it blends strata (replan C7). The bound-above logic stands — the student cannot agree with the teacher better than the teacher agrees with itself.
  3. **Distillation fidelity (diagnostic):** held-out chip-level present/absent agreement vs Gemini.
- **DINOv2-S/14 floor** scored on the same three numbers.
- **Accuracy is explicitly out of the gate** — no independent install-date truth exists.

### D8 — Region / data discipline

- Region resolution via explicit `region` + registry (ADR-0002); no grid-ID pattern matching.
- Weights + re-downloaded chips under `~/zasolar_data/`; only example fixtures committed.

## Testing Decisions

A good test exercises **external behavior at the seam**, not encoder internals (no asserts on attention maps, token shapes, or timm revisions).

- **Seam contract test (primary).** Inject a stub `PresenceScorer` returning canned observations; assert the adaptive-scan path produces the expected `scan_state.json` / round sequence. Prior art: the existing `poster=` HTTP stub and the `scorer=` injection already used by the sequence path, plus `tests/temporal/`.
- **Backbone parity test.** With a tiny real chip fixture, assert the DINOv3 scorer returns one observation per pick, in order, with `pv_present ∈ {True,False,None}`, finite `pv_score`, valid `quality_flag`. Shape/ordering, not accuracy.
- **Determinism test.** Score the same fixture twice; assert identical `pv_score`. This is the unit-level proof of the reproducibility motivation.
- **Downstream invariance test.** Feed identical canned observations through the Gemini-mapped and DINOv3-mapped round mapping; assert identical `scan_state.json`, proving dip-repair / interval / clamp are unperturbed.
- **Fidelity gate (offline benchmark, not CI).** The three D7 numbers on held-out anchors, reusing `llm_endtoend_analyze`. Reported in the doc, not asserted in CI.
- Modules tested: the `PresenceScorer` seam + the adaptive-scan round mapping. Not unit-tested: GEHI download, vintage discovery, the state machine (already covered), encoder weights.

## Out of Scope

- The adaptive-scan **state machine**, **dip-repair**, **interval inference**, **Vexcel clamp** — unchanged.
- **Beating Gemini / accuracy claims** — distillation matches the teacher; no independent truth exists.
- **Full fine-tuning / unfreezing** the backbone in v1 (LoRA/PEFT only on gate failure).
- **Sequence-aware (c)** and **census-GT reference conditioning (b)** — backups, not v1.
- **Per-sub-domain calibration** — deferred to v1.1.
- **Hand-labeled install-date GT** — optional future, only if an accuracy claim is needed.
- The **detector / aerial census**, **segmentation**, the **two-stage skylight FP-review** — separate concerns.
- **Change-detection / Siamese / multispectral** — rejected by the pre-spec; GEHI is RGB.
- **GEHI vintage-discovery / chip-download** stages, and **the L2-search / L3-imagery variance fix** (a separate PRD if the bonus experiment shows it is still needed) — untouched here.

## Further Notes

- **Phase-0 caveat.** DINOv3-L-SAT lost its only in-project gate, but that was segmentation, at 6.7 cm, random-init decoder, frozen-only, cross-region — a stack of confounds, and the *opposite* GSD regime to this task. For binary anchor-conditioned presence on ~0.25 m historical chips, the relevant evidence is unmeasured; the DINOv2-S/14 floor (D2) guards against repeating the unhedged big-FM bet.
- **The distillation is bounded by the teacher.** The student converges to Gemini's decisions, including Gemini's errors and any marker-misregistration noise. This is acceptable under "match, not beat"; it is the reason accuracy is out of the gate.
- **Determinism asterisk.** A frozen backbone is bit-reproducible only with fixed precision/kernels. Swapping the scorer removes version drift and scorer stochasticity, but the study's dominant L2/L3 variance is out of scope — the bonus experiment (US-23) tests whether a deterministic scorer happens to collapse it too.
- **No GitHub issue is filed.** This ecosystem plans entirely in `docs/` markdown (the issue tracker is unused); this file is the deliverable. Respect ADR-0002; keep the cross-repo import contract one-directional.
