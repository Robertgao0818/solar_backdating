# DATA — anchor-pair student pilot: pre-registration (2026-07-10)

Status: **pre-registered 2026-07-10, not yet executed.** Design, arms,
metrics and kill bar are locked by this doc before any training run; result
sections are `TBD-run`.

Parent: [`DATA-fidelity-gate2-dual-fail-2026-07-10.md`](DATA-fidelity-gate2-dual-fail-2026-07-10.md)
(gate-2 dual FAIL; residual = bidirectional present/absent confusion at
decisive frames, amplified by the hard-MAP changepoint decoder) ·
Sibling: [`DATA-gate2-area-stratified-2026-07-10.md`](DATA-gate2-area-stratified-2026-07-10.md)
(rules out small-target invisibility as the driver; student gap is
size-uniform and FP-flavored) ·
Constraint context: LoRA/backbone fine-tuning is **vetoed** (user decision
2026-07-10: no compute for it); everything below is frozen-backbone +
head-only, local RTX 4070, zero API spend, zero new GT.

## Hypothesis

The single-frame student answers an unnecessarily hard question — "is PV
present in this frame, absolutely?" — precisely where the teacher signal
says the question is ambiguous (transition frames). Re-posing it as a
**relative** question — "did this target change from its known-absent state?"
— by pairing each candidate frame with a per-target absent **anchor frame**
should cut transition-band FP and FN without touching the backbone.

External convergence (2026-07-10 research sweep): DeepSolar++ (Joule 2022)
uses a Siamese pair (candidate + sequence reference frame) exactly on its
low-resolution arm; Kruitwagen et al. (Nature 2021) refine per-frame CNN
outputs with a sequence-level head; ChangeDINO / SemDINO (2025) get SOTA
building-change F1 from **frozen** DINOv3 + a small Siamese difference head.
No published work does this for PV install dating — if it works it is novel.

## Pre-registration (locked before any run)

1. **Population & split.** The existing slice-4/5 distillation corpus
   (`~/zasolar_data/geid_temporal/dinov3_distill_20260705/`, tight12
   nomarker renders) with the ISSUE-02 anchor-disjoint train/report split,
   unchanged. No new imagery, no new labels.
2. **Eligibility.** A target is pair-eligible iff it has ≥1 teacher-labeled
   `absent`, non-unusable frame. Anchor frame = the **earliest** such frame
   (fixed rule; no per-target selection tuning). Ineligible targets (e.g.
   `done_already_present_before_geid_history`) are excluded and their share
   reported; the pilot claims nothing about them.
3. **Input & model.** Frozen `dinov2_floor` winner embedding config
   (`nomarker_bilinear518_k6` center-pooled tokens), reusing the slice-5
   extraction pipeline verbatim. Pair head input =
   `[emb_cand, emb_anchor, emb_cand − emb_anchor]` → MLP (≤3 hidden layers,
   ≤5M params) → 3-class (present / absent / unusable). Embedding-space
   pairing only — pixel-space differencing is explicitly rejected
   (inter-vintage displacement, ISSUE-23). Seed=0; hyperparameters fixed at
   first successful training config; no post-hoc sweeps.
4. **Arms.**
   - **Arm A (bet):** pair head as above.
   - **Arm B (capacity control):** identical MLP on `emb_cand` alone, same
     budget/seed/split. Isolates "pairing" from "MLP > linear head"
     capacity; without B, an Arm-A win is uninterpretable.
   - Slice-5 linear head numbers reported alongside as context only.
5. **Transition band (primary stratum).** Per eligible target, the frames
   dated within the teacher's decoded `[last-absent, first-present]` window
   ±1 frame. Baseline transition-band decided-agreement / FP / FN for the
   slice-5 linear head and Arm B are **measured in step 0** from existing
   report-half data before Arm A trains (`TBD-run`), not asserted.
6. **Verdict rule (R1, arithmetic — no discretion).** On the held-out
   report half, Arm A vs Arm B:
   - **GO** iff transition-band FP count ↓ ≥30% **and** FN count ↓ ≥30%,
     **and** overall decided-agreement(A) ≥ decided-agreement(B), **and**
     unusable recall(A) ≥ unusable recall(B) − 2pp.
   - **KILL** otherwise. The dual-fail oracle showed one-sided fixes cannot
     clear the ceiling (FP-only → 0.58; FP+unusable → 0.59; needs FN too),
     so a one-sided win is a KILL, not a partial credit.
7. **On GO → stage B (separate run, still zero API):** plug the pair scorer
   into `scripts/validation/fidelity_gate.py`'s student walk (anchor frame
   resolved per target inside the sequence walk) and re-run gate-2 under the
   unchanged bar: ceiling 0.7724, inv-weighted headline **with decoded-only
   as co-headline** (dual-fail memo implication (c)). This pilot's GO does
   NOT pass the gate; it only licenses the re-run.
8. **Budget.** Local RTX 4070; cached embeddings + two MLP trainings
   (minutes each) + step-0 measurement; wall-clock ≤1 day; **zero API
   spend, zero new GT, zero backbone training**.

## Out of scope (this pilot)

- Teacher-side chip-geometry rebuild (tight/adaptive chips + bbox aid +
  k-rep stability sample) — **parallel track, separate pre-registration**;
  it changes the teacher, this pilot holds the teacher fixed. If that track
  later re-banks better teacher labels, Arm A's architecture verdict carries
  over but the head must be re-distilled before any gate re-run.
- Sequence-level distillation head (EVL/AIM-style temporal head over the
  frame-embedding sequence, distilling the teacher's decoded interval
  directly) — the pre-registered next bet **iff** this pilot KILLs on the
  pairing mechanism specifically.
- Any change to gate-2's bar, decoder, or agree_key semantics.
- SAT/L backbone ablations (bet already falsified, gate-run verdict).

## Results (`TBD-run`)

- Step 0 baselines: `TBD-run`
- Arm A vs Arm B table (overall + transition band + per-area-bucket ride-along
  per the sibling memo's implication 3): `TBD-run`
- Verdict per R1: `TBD-run`
