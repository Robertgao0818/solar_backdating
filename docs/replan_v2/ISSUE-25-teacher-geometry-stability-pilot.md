# ISSUE-25 — Teacher chip-geometry & stability pilot (from-scratch 24/48/96)

Status: ready-for-human
Phase: 2/4 (feeds Panel v2 rescan geometry + the ESSD reliability story)
Blocked by: — (parallel to the anchor-pair student pilot, which holds the
teacher fixed: [`../dinov3_scorer/DATA-anchor-pair-pilot-prereg-2026-07-10.md`](../dinov3_scorer/DATA-anchor-pair-pilot-prereg-2026-07-10.md))

## Motivation

Two independent observations converge on the same suspect — the **banked96
group-anchored chip geometry** the teacher was scored on:

1. Quantified: on banked96, the teacher's own rep↔rep self-consistency is
   only **0.65–0.67 on <40 m² targets** (vs 0.82 at ≥100 m²), and 92% of
   decoded targets are <40 m²
   ([`../dinov3_scorer/DATA-gate2-area-stratified-2026-07-10.md`](../dinov3_scorer/DATA-gate2-area-stratified-2026-07-10.md)).
   The global 0.7724 gate bar is held up by a small large-target tail.
2. Human-confirmed: on 96 m group chips a small PV is near-invisible even to
   a human with marker aid; first-round production small-target dating
   reliability is therefore an open question, not a settled fact.

Additionally, group-anchored chips place edge-of-group targets at the chip
boundary — a target-centered re-crop from banked96 rasters can be truncated.
Hence **from-scratch, target-centered downloads**, not re-crops
(user decision 2026-07-10).

The storyline plan already conditions on this: "verdict 以 banked96 几何为条
件, Panel v2 换几何后赢家须廉价复检" (storyline v2 §5 P0-2).

## Pre-registration (locked at manifest commit; result sections `TBD-run`)

1. **Imagery.** One from-scratch, **target-centered** GEHI download per
   (target, vintage) at ≥96 m extent; the 24 / 48 / 96 m arm crops are
   derived from the same raster (one download covers all arms). Vintage
   sequence = GEHI (GE Time Machine) + Wayback, merged as in production.
   Idempotent re-download keyed `(anchor, capture_date, version, zoom)`.
2. **Arms.**
   - **A24 (primary bet)** / **A48** / **A96**: target-centered chips at
     24 / 48 / 96 m, teacher-side bbox aid drawn on every arm (student
     contract stays nomarker — PRD-D4; bbox is a teacher prompt aid only).
   - **B0 (model bridge, subsample n=150):** new model × banked96
     group-geometry replication, compared against the existing banked reps
     (old model, same geometry). Without B0, "new geometry is better" is
     confounded with "new model is different".
   - **Legacy control (free):** existing banked rep1–3 per-bucket ceiling
     table from the area-stratified memo.
3. **Model.** **`gemini-3.1-flash-lite`**, frozen by the user after the
   20-target smoke calibration on 2026-07-10
   ([DATA memo](DATA-issue25-model-smoke-2026-07-10.md)). The alternate
   `gemini-3.5-flash-extra-low` tied overall exact-pattern repeatability but
   emitted frame abstains/non-monotonic sequences and failed 19/20 targets at
   `max_tokens=1024` due to JSON truncation. Same frozen model for all arms;
   model choice is never varied inside the geometry comparison.
4. **Sample.** Stratified by **area bucket × zone**; zones
   (residential / industrial / CBD) assigned from a grid→zone lookup that is
   **frozen and committed with the sample manifest before any scoring**.
   Stage 1 (geometry bake-off): n=400 core targets × 3 arms × 5 reps.
   Stage 2 (precision): winning arm × 5 reps on +800 further targets
   (total 1,200) for per-stratum CIs. Counts adjustable until manifest
   commit, frozen after.
5. **Scoring.** Production adaptive sequence scan per rep, 5 independent
   reps per (target, arm). CoJ 2023 municipal aerial (true-date, independent
   provider, JHB) scored once per target as a same-epoch **presence
   adjudication channel**: disagreement with the GEHI-sequence 2023 state →
   human review queue. This is the closest available accuracy-flavored
   check (no SA install registry exists); it is a channel, not GT.
6. **Metrics.** Per arm: 5-rep pairwise `agree_key` self-consistency
   (10 pairs/target), overall + per area bucket + per zone; unusable rate;
   CoJ-2023 presence agreement; cost per 1k rounds.
7. **Adoption rule (R1, arithmetic).** A geometry arm is adopted iff, vs the
   banked96 legacy control: small-bucket (<40 m²) self-consistency **+≥8pp**,
   large-bucket regression **≤2pp**, CoJ presence agreement not worse, and
   unusable rate ≤1.5× control. Tie between qualifying arms → **24 m**
   (primary bet). No arm qualifies → keep banked96; the small-target
   reliability bound stands as a quantified ESSD fact either way.
8. **Budget formula** (frozen numerically at kickoff): rounds ≈
   stage1 400×3×5×~8–12 adaptive rounds ≈ 48–72k, + stage2 800×1×5×~10 ≈
   40k, + B0 150×5×~10 ≈ 7.5k → **~100–120k Gemini rounds** on the chosen
   flash-tier model, plus GEHI download quota for ~1,200 targets × sequence.
   Concurrency/quota check (账号×槽位) is a kickoff AC, not an afterthought.

## Downstream on adoption

- Panel v2 full rescan runs on the winning geometry + frozen model.
- The distillation corpus re-renders idempotently on the new geometry; the
  anchor-pair pilot's **architecture** verdict carries over, but any student
  must re-distill on re-banked labels before a gate-2 re-run.
- The dinov3_scorer gate verdict's banked96 conditionality clause is then
  discharged (cheap winner re-check, per the storyline conditional).

## Acceptance criteria

- [ ] Sample manifest + grid→zone lookup committed (freeze point)
- [x] 20-target smoke calibration report; model frozen
- [ ] Stage-1 per-arm × per-bucket self-consistency table
- [ ] B0 model-bridge table (model effect isolated from geometry effect)
- [ ] Verdict per R1 + DATA memo; CoJ disagreement queue exported

## Out of scope

- Student-side gates (gate-2 bar, decoder, agree_key semantics unchanged)
- Accuracy claims beyond the CoJ presence channel
- Full Panel v2 rescan (separate, gated on this pilot's verdict)
