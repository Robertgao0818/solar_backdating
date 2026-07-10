# ISSUE-06 — Fidelity gate verdict

Status: **GATE-RUN COMPLETE 2026-07-10** (LOCKED sections unchanged since
2026-07-06; only `TBD-gate-run` sections filled below).
Parent: [`ISSUE-06-fidelity-gate.md`](ISSUE-06-fidelity-gate.md) · prep/design:
[`ISSUE-06-prep-2026-07-05.md`](ISSUE-06-prep-2026-07-05.md) (R1 rule
pre-registered there, commit `9ea5405`).

This skeleton existed to make the tie-break rule **operationally binding before
the gate-2 numbers land** (prep §Risks R1: "written … so the verdict cannot be
back-fit to the numbers"). Sections marked `TBD-gate-run` are filled only
after the runs complete; nothing in a `LOCKED` section may be edited afterwards
except to fix a typo that does not change semantics (note any such edit here).

**Run stamp (2026-07-10):** local RTX 4070 Laptop GPU; zero Gemini/API spend;
teacher ceiling = CPU re-decode of banked rep1–3; student = tight12 nomarker
re-render + local re-score. Artifacts:
`~/zasolar_data/geid_temporal/fidelity_gate_20260710/{dinov3_lsat,dinov2_floor}/`.

---

## LOCKED — operationalized tie-break rule (R1, binding)

The prep doc pre-registered the three-step rule; this section pins the exact
arithmetic so no discretion remains at verdict time:

1. **Gate-3 equivalence test.** `|Δ decided-agreement| < 1 binomial SE`, where
   SE = `sqrt(p̄(1−p̄)/n_decided_min)`, `p̄` = mean of the two decided-agreement
   values, `n_decided_min = min(759, 753)`. With the verified raw numbers
   (below) Δ = 0.000288 and SE ≈ 0.0146 → **gate-3 verdict = EQUIVALENT; the
   SAT/L bet is falsified on gate 3.** (Locked from the ISSUE-04/05 artifacts,
   which predate this skeleton and cannot be influenced by gate-2.)
2. **Bet-survival test (gate 2).** Define, per backbone, `A_backbone` = the
   **mean over rep1/rep2/rep3** of the per-rep **inventory-weighted, all-units**
   student-vs-teacher interval agreement (the D8 headline caliber). Define the
   **ceiling spread** `S` = `max − min` of the three pairwise inventory-weighted
   teacher rep↔rep agreements on the **sanctioned rep1–3 panel** (pairs 1-2,
   1-3, 2-3; same decoder, same drop-set, same unit set as `A`). The SAT/L bet
   **survives iff** `A_LSAT − A_floor > S`. Otherwise the bet is **falsified**
   and the cheaper floor is the operative equivalent (report both, adopt the
   floor per the D-plan falsification logic). Degenerate case: if `S = 0`
   (all three pairs identical), any `A_LSAT − A_floor > 0` survives — note it
   as degenerate if hit.
3. **Coverage tie-break.** Only if (1) and (2) are both equivalence/falsified
   does coverage break the tie, to the higher-coverage backbone — a tie-break,
   never a bet-survival signal.

**Locked caliber definitions used above and everywhere below:**

- Decoder: `changepoint` + EB cohort prior at the **A4 signed working point**
  (`decoder_epoch_gap_days = 45`, `cohort_prior.json` from
  `issue03_gates_20260704`, `emissions_fitted.json` from `panel_repair_20260703`),
  identical for teacher pipeline, student pipeline, and ceiling — like-for-like
  (D8.1). Vexcel per-grid present-clamp applied identically on all three.
- Headline = inventory-weighted, all-units (D8.2). **Dated-only denominator**
  (D8.3, reported alongside, never headline): restrict to units where **both
  sides of the pair** decode to a non-`UNDATED` key.
- Unit = the 642-target reference cohort **minus the locked leakage drop**
  (dynamic intersection with `chip_subset_anchors.json`; expected ≤6 target /
  ≤2 group anchors — exact ids recorded below at run time).
- No cohort conclusion may be drawn from a stratum with n≤2 support (D8.5).
- Splice-through units (provider not in {gehi_main, gehi_pertarget}) pass the
  same delivery values through both pipelines and inflate agreement identically
  in gate and ceiling; the verdict must also report the **decoded-only** subset
  as a diagnostic split so this inflation is visible.

## LOCKED — student input contract + render-drop policy (added 2026-07-06, still pre-gate-2)

Recon on the banked chips found a two-axis input mismatch the prep doc missed:
banked rep chips are `chip_geom_v1_banked96` renders **with** the yellow
review marker (the teacher's verdicts narrate the marker), while both heads
were trained on `chip_geom_v2_tight12` **nomarker** renders. Scoring banked
chips directly would be out-of-distribution and corrupt the gate. Locked
handling:

- **Student input** = per-frame re-render of the banked georeferenced source at
  `chip_geom_v2_tight12`, `draw_marker=False`, using the same renderer chain the
  distillation set used (`ensure_single_target_review_png` +
  `resolve_chip_geometry`, per `build_distillation_set.py`). Teacher verdicts
  stay as banked (each pipeline under its own input contract — the marked arm
  is D4-forbidden for the student anyway).
- **Render-drop policy (primary caliber):** a frame that cannot be re-rendered
  is dropped from **both** pipelines' observation sequences for that unit
  (like-for-like frame sets within student-vs-teacher); drop counts + affected
  anchors reported. The teacher ceiling stays on full banked frame sets (its
  own variance already includes frame-set differences between reps).
- **Pollution stop-rule:** if re-render drops exceed **2% of frames** on either
  backbone's pass, STOP — do not ship gate-2 numbers; reassess the render path
  first and record the reassessment here.
- Diagnostic split reported alongside: agreement for anchors with ≥1 dropped
  frame vs none (mirrors the R3 unusable split).

## LOCKED — decoder-baseline honesty chain (carried verbatim, per prep)

> The changepoint decoder's **hard-MAP** year-histogram TVD was **NOT MET** on
> the 2026-07-04 store-backed re-run (flat prior `[0.079/0.040/0.076]`, mean
> 0.065; EB prior **worse**, `[0.092/0.047/0.085]`, mean 0.075 — a sharper
> posterior is more argmax-sensitive), and the production reference channel
> itself breached the band on 1/3 pairs. **DECISION-A (2026-07-04)** adjudicated
> **NO-GO cohort-wide under the hard-MAP caliber**. **PRD-AMENDMENT-P1 Option A
> (signed 2026-07-05)** then moved the operative gate to the **survival /
> fractional channel** — which **passes and beats point-date** — and set the
> production default = **decoder + EB prior**, with the P3 band re-derivation
> (ISSUE-21) as **condition-subsequent** (since discharged: band
> `[0.0243, 0.0787]` frozen 2026-07-05, rollback trigger PASS).

## Gate 3 — held-out chip-level agreement (verified from raw artifacts 2026-07-06)

| backbone | n | decided | agree | coverage | decided-agreement |
|---|--:|--:|--:|--:|--:|
| DINOv3-L-SAT (`head_v1_20260705`) | 858 | 759 | 605 | 0.884615 | **0.797101** |
| DINOv2-S floor (`head_v1_20260705`) | 858 | 753 | 600 | 0.877622 | **0.796813** |

Both are the **held-out report half**, anchor-disjoint from the 82-anchor
calibration half. The calib-half values (0.939 / 0.9495) are **in-sample
calib-half only** — forbidden as headline or generalization numbers anywhere
in this verdict (the ~14 pp optimism gap is the ISSUE-04 R2 lesson).

Gate-3 verdict per LOCKED rule 1: **EQUIVALENT** (Δ = 0.029 pp < 1 SE ≈ 1.46 pp)
→ **SAT/L bet falsified on gate 3**; survival now rests solely on LOCKED rule 2.

## Gate 1 — student self rep↔rep reproducibility (filled 2026-07-10)

Demonstrated by two full student re-score passes over rep1 (cache OFF) +
byte-compare of decoded agree_keys — not asserted.

| backbone | self-agreement | n_decoded compared | n_mismatches | pass |
|---|--:|--:|--:|---|
| DINOv3-L-SAT | **1.0** | 289 | 0 | **PASS** |
| DINOv2-S floor | **1.0** | 289 | 0 | **PASS** |

Primary "no wobble" goal: **met for both backbones**.

## Gate 2 — pipeline interval agreement vs re-derived teacher ceiling (filled 2026-07-10)

Caliber = LOCKED definitions above. Student chips re-rendered at
`chip_geom_v2_tight12` / `draw_marker=False` before scoring (LOCKED input
contract). Render drops: **0 / 14,002 frames (0.000%)** on both backbones —
pollution stop-rule not triggered. No hand-authored 0.74 bar anywhere.

### Teacher rep↔rep ceiling (rep1–3, full banked frame sets)

| pair | n_intersection | inv-weighted (headline) | dated-only inv-weighted |
|---|--:|--:|--:|
| 1–2 | 222 | **0.7708** | 0.7709 |
| 1–3 | 218 | **0.7674** | 0.7685 |
| 2–3 | 233 | **0.7790** | 0.7790 |

- Ceiling mean (inv-weighted) = **0.7724**
- Ceiling spread `S` = max − min = **0.0116**

### Student-vs-teacher (`A_backbone` = mean of per-rep inv-weighted all-units)

| backbone | rep1 | rep2 | rep3 | **A mean (headline)** | decoded-only pooled | dated-only pooled |
|---|--:|--:|--:|--:|--:|--:|
| DINOv3-L-SAT | 0.6653 | 0.6028 | 0.6495 | **0.6392** | 0.3706 | 0.3706 |
| DINOv2-S floor | 0.6650 | 0.6345 | 0.6630 | **0.6542** | 0.3945 | 0.3946 |

n_all_units per rep = 628 (642 − 14 leakage-dropped rows); n_decoded pooled =
868 / 1,884 unit-rep rows (splice-through inflates all-units — decoded-only
reported as diagnostic per LOCKED caliber).

**Gate-2 vs ceiling (PRD: student should reach the teacher self-consistency
ceiling):** both backbones miss by ~12–13 pp (LSAT −0.133 / floor −0.118 vs
ceiling mean 0.7724). **Gate 2 = FAIL for both** (no-answer-change not
demonstrated at production-swap bar). Bound-above still holds: neither exceeds
the ceiling.

### R3 unusable-frame split (pooled decoded-only, inv-weighted)

| backbone | with ≥1 teacher-`unusable` frame | no unusable frame |
|---|---|---|
| DINOv3-L-SAT | n=406, agree=**0.2490** | n=462, agree=**0.4152** |
| DINOv2-S floor | n=406, agree=**0.1852** | n=462, agree=**0.5112** |

The known thin unusable-class recall (prep R3) is visible: agreement is
materially worse on windows that contain teacher-`unusable` frames. Floor is
worse than L-SAT on the unusable-bearing subset and better on the clean subset.

### Leakage drop actually applied

Intersection of `chip_subset_anchors.json` (800) with the 642-row reference:

- **14 rows dropped** / **2 group** / **6 target** anchors (matches expected
  ≤2 / ≤6)
- group: `…_c0003649`, `…_c0012245`
- target: `…_t00000046`, `…_t00009160`, `…_t00015343`, `…_t00021645`,
  `…_t00023953`, `…_t00032299`
- source_feature_ids: 45, 7975, 7977, 7978, 7980, 9159, 15342, 21644, 23952,
  30199, 30203, 30204, 30207, 32298
- n_reference_rows after drop = **628**

## Composite verdict (filled 2026-07-10)

### Pass/fail per backbone

| backbone | gate 1 (repro) | gate 2 (vs ceiling) | gate 3 (chip fidelity) |
|---|---|---|---|
| DINOv3-L-SAT | **PASS** (1.0) | **FAIL** (A=0.6392 ≪ ceiling 0.7724) | 0.7971 @ 0.885 cov (held-out) |
| DINOv2-S floor | **PASS** (1.0) | **FAIL** (A=0.6542 ≪ ceiling 0.7724) | 0.7968 @ 0.878 cov (held-out) |

### L-SAT-vs-floor bet (LOCKED R1 arithmetic — no discretion)

1. Gate 3: **EQUIVALENT** (locked pre-run) → SAT/L falsified on chip fidelity.
2. Gate 2 bet-survival: `A_LSAT − A_floor = 0.6392 − 0.6542 = **−0.0150**`,
   `S = 0.0116`. Survives iff `A_LSAT − A_floor > S` → **−0.0150 ≯ 0.0116 →
   FALSIFIED**. Floor is the operative equivalent on the headline caliber
   (and is actually **+1.5 pp better** than L-SAT).
3. Coverage tie-break: not reached as a bet-survival signal (rule 2 already
   falsified); for the record L-SAT still holds the gate-3 coverage edge
   (+0.70 pp), which does not override rule 2.

**SAT/L bet overall: FALSIFIED.** Cheaper DINOv2-S floor is the operative
student equivalent — **but neither backbone reaches the teacher rep↔rep
ceiling, so neither is a production scorer swap candidate.**

### Production implication

- **Gemini stays the default scorer** (feature flag unchanged).
- **ISSUE-07 rollout remains blocked** on a fidelity-gate pass.
- **P4 (replan ISSUE-15/16) and Panel v2 full rescan stay on Gemini quota** —
  the student did not clear the no-answer-change bar that would unlock a
  Gemini-free rescore path.
- Conditionality from plan doc §5 P0-2 (banked96 geometry): this run used the
  LOCKED tight12 nomarker re-render, so the gate-2 numbers are **not** the
  OOD banked96-scoring path; a future Panel v2 re-render still needs a cheap
  fidelity re-check of any winner if geometry moves again.
- Honesty chain for the decoder baseline: carried unchanged in the LOCKED
  section above (hard-MAP NO-GO → Option-A caliber flip → ISSUE-21 band
  discharged).

### Artifacts

| path | contents |
|---|---|
| `~/zasolar_data/geid_temporal/fidelity_gate_20260710/dinov3_lsat/` | gate1/2 JSON, provenance, summary, render_drop_report |
| `~/zasolar_data/geid_temporal/fidelity_gate_20260710/dinov2_floor/` | same |
| `~/zasolar_data/geid_temporal/fidelity_gate_20260710/logs/` | full run logs |
| banked teacher input (read-only) | `…/llm_endtoend_storebacked_20260704/rep{1,2,3}/` |

Head sha256 verified at run: DINOv3 `bc053cbd…`, floor `38ecbf37…`.
Re-render: 0 drops / 14,002 frames on both passes.
