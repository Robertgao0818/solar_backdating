# ISSUE-25 — Teacher chip-geometry & stability pilot (from-scratch 24/48/96)

Status: ready-for-agent
Phase: 2/4 (feeds Panel v2 rescan geometry + the ESSD reliability story)
Blocked by: — (parallel to the student pairing line, which holds the
teacher fixed: v1
[`../dinov3_scorer/DATA-anchor-pair-pilot-prereg-2026-07-10.md`](../dinov3_scorer/DATA-anchor-pair-pilot-prereg-2026-07-10.md)
closed scope-limited KILL; live frame-level bet is pairing-v2
[`../dinov3_scorer/DATA-anchor-pair-v2-prereg-2026-07-10.md`](../dinov3_scorer/DATA-anchor-pair-v2-prereg-2026-07-10.md))

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
   **Frozen 2026-07-10:** [`issue25_manifest_20260710/`](issue25_manifest_20260710/)
   contains the 382-grid lookup, 1,200-target balanced manifest, exact
   target-centred 96 m anchors, source-snapshot hashes, and the 150-target B0
   subset. Every area-bucket × zone cell has n=100 across the full sample.
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

## Stage-B execution gate (2026-07-10)

Manifest freeze, the real adaptive API smoke, and the full-manifest initial
imagery prefetch are complete. See
[`DATA-issue25-stage-b-smoke-2026-07-10.md`](DATA-issue25-stage-b-smoke-2026-07-10.md).
Both imagery-source paths completed 20/20 targets without a crash. All 97
hosted API chunks passed schema on their first attempt with no empty/truncated
response or retry. Wayback separately had a 37% initial imagery retrieval
failure rate (53/138 adaptive observations), all caused by GEHI producing no
file after the z19→z18 ladder; these are not Gemini failures, and the smoke
does not distinguish source coverage from exporter/downloader behaviour. The
full initial prefetch then completed 6,000/6,000 TM candidates and 6,000/6,000
Wayback attempts; Wayback produced 3,726 valid TIFFs and 2,274 (37.9%) of the
same zero-tile retrieval failures.

**Stage C progress (2026-07-11):** Stage-1 (15/15 arm-reps) + B0 (5 reps) +
full test suite completed. Winner policy confirmed as
`routed_A24_A48_cut40` (Amendment 2026-07-11). Stage-2 precision extension
(800 targets × routed A24/A48 × 5 reps) is the remaining scoring block; kickoff
parameters remain `workers=40`, `qps=20`, model `gemini-3.1-flash-lite`, target
routing salt, no verdict store. Launch requires
`stage_c/.stage2_winner_confirmed` (already written).

## Downstream on adoption

- Panel v2 full rescan runs on the winning geometry + frozen model.
- The distillation corpus re-renders idempotently on the new geometry; the
  anchor-pair pilot's **architecture** verdict carries over, but any student
  must re-distill on re-banked labels before a gate-2 re-run.
- The dinov3_scorer gate verdict's banked96 conditionality clause is then
  discharged (cheap winner re-check, per the storyline conditional).

## Acceptance criteria

- [x] Sample manifest + grid→zone lookup committed (freeze point)
- [x] 20-target smoke calibration report; model frozen
- [x] Stage-B full-manifest initial imagery prefetch + API/schema smoke report
- [x] Stage-1 per-arm × per-bucket self-consistency table
- [x] B0 model-bridge table (model effect isolated from geometry effect)
- [x] Stage-1 routed counterfactual amendment frozen before Stage-2
- [ ] Stage-2 precision extension under the confirmed winner policy
- [ ] Verdict per R1 + DATA memo; CoJ disagreement queue exported

## Amendment 2026-07-11 — single-threshold area-routed Stage-2 geometry

**Status: ADOPTED (human confirmation for Stage-2 winner policy).**  
**Does not rewrite Stage-1.** Stage-1 remains the pre-registered three-arm
bake-off. This amendment only changes **how Stage-2 selects and applies** the
winning geometry after Stage-1 results were observed.

### Motivation (post-Stage-1)

Stage-1 scored the same 400 core targets under A24 / A48 / A96. The
pre-registered single-arm Stage-2 selector (max small-bucket agreement among
large-safe arms; tie → A24) yields **A24**. Per-bucket results show a
physically expected size interaction: A24 wins xs/sm, A48 wins md/lg, and
A24's large-bucket gap is consistent with chip crop truncating wide arrays
(24 m edge vs installations often >15–25 m wide).

A single-threshold route therefore re-uses already-paid Stage-1 reps; it is
not a new bake-off.

### Frozen policy (no new free parameters)

| item | freeze |
|---|---|
| Route | `chip_arm = A48` if `source_area_m2 >= 40`, else `A24` |
| Threshold | **40 m² exactly** — the pre-registered small/large boundary; not tuned on Stage-1 |
| Excluded | multi-threshold / per-bucket arm shopping; A96 stays bake-off only |
| Provenance column | `chip_arm ∈ {A24, A48}` materialised at Stage-2 anchor prepare from frozen `source_area_m2` (census gpkg / fpcut; no temporal label) |
| Single source of truth | still the anchor table + deterministic rule; sample manifest hashes unchanged |
| Winner id | `routed_A24_A48_cut40` |

### Per-stratum gates (blend may not launder a failed stratum)

Stage-2 / Stage-D R1 still evaluates **small** and **large** strata
separately against the pre-registered floors. The mixed overall agreement is
diagnostic only.

Using Stage-1 pairs under the route (counterfactual, same 400 targets):

| stratum | source arm | agreement | floor | pass |
|---|---|---:|---:|---|
| small (`<40 m²`) | A24 | 0.8272 | 0.7395 | yes |
| large (`≥100 m²`) | A48 | 0.9646 | 0.7982 | yes |
| overall (diagnostic) | mix | 0.8868 | — | — |

Single-arm reference: best overall A48 = 0.878; A24 small = 0.8272 / large =
0.8919. The route **weakly dominates** every single arm on the two
pre-registered primary metrics simultaneously (A24 small retained; A48 large
taken). Full arithmetic:
[`DATA-issue25-stage1-routed-counterfactual-2026-07-11.md`](DATA-issue25-stage1-routed-counterfactual-2026-07-11.md).

### Four constraints that remain binding

1. **Post-hoc rule change.** Documented here before Stage-2 starts. Threshold
   locked to the pre-registered 40 m² cut; md (+2.1 pp) and xs (+3.7 pp) shifts
   are noise-scale (~100 targets each) — only lg (+7.3 pp) is robust; do **not**
   promote multi-cut optimization.
2. **B0 unaffected.** B0 uses fixed 96 m group chips (`chip_half_m=48`) as an
   independent model-bridge instrument.
3. **Instrument heterogeneity (ESSD).** Two review extents = two instruments.
   Downstream size-stratified install-date comparisons must not confuse size
   effects with instrument effects. Stage-1 is a 400-target dual-instrument
   overlap sample for calibration; optional Stage-2 ~5% dual-size rescore is a
   monitoring add-on, not required to start Stage-2.
4. **Group-chip routing conflicts.** Route **per target**. Do not force a
   shared review extent across targets that only share a legacy group id.
   Stage-2 does not cross-size batch groups.

### Stage-2 human confirmation object

Confirmation is no longer “single arm A24”. Confirmed winner policy:

```text
routed(A24/A48, cut=40 m²)  id=routed_A24_A48_cut40
```

Artifact: `stage_c/stage2_winner_decision.json` +
`stage_c/.stage2_winner_confirmed` written only after this amendment is on
disk. Stage-2 launch scripts must refuse to start unless that sentinel exists
and names the routed policy.

## Out of scope

- Student-side gates (gate-2 bar, decoder, agree_key semantics unchanged)
- Accuracy claims beyond the CoJ presence channel
- Full Panel v2 rescan (separate, gated on this pilot's verdict)
- Multi-threshold or per-bucket arm re-optimization after this freeze
