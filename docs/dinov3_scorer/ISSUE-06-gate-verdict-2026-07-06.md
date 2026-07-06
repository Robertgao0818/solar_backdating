# ISSUE-06 — Fidelity gate verdict

Status: **SKELETON — locked 2026-07-06 BEFORE any gate-2 number was produced.**
Parent: [`ISSUE-06-fidelity-gate.md`](ISSUE-06-fidelity-gate.md) · prep/design:
[`ISSUE-06-prep-2026-07-05.md`](ISSUE-06-prep-2026-07-05.md) (R1 rule
pre-registered there, commit `9ea5405`).

This skeleton exists to make the tie-break rule **operationally binding before
the gate-2 numbers land** (prep §Risks R1: "written … so the verdict cannot be
back-fit to the numbers"). Sections marked `TBD-gate-run` are filled only
after the runs complete; nothing in a `LOCKED` section may be edited afterwards
except to fix a typo that does not change semantics (note any such edit here).

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

## Gate 1 — student self rep↔rep reproducibility `TBD-gate-run`

- DINOv3-L-SAT: TBD (expect exact 1.0, demonstrated by two full passes +
  byte-compare of decoded intervals, not asserted)
- DINOv2-S floor: TBD

## Gate 2 — pipeline interval agreement vs re-derived teacher ceiling `TBD-gate-run`

- Teacher ceiling (rep1–3 pairs, inv-weighted / dated-only / decoded-only): TBD
- Ceiling spread `S`: TBD
- `A_LSAT` (per-rep + mean): TBD
- `A_floor` (per-rep + mean): TBD
- R3 split (anchors with ≥1 teacher-`unusable` frame in window vs none): TBD
- Leakage drop actually applied (ids + counts): TBD
- No hand-authored bar anywhere; the retired ~0.74 must not appear as a bar.

## Composite verdict `TBD-gate-run`

- Pass/fail per backbone (gate 1 / gate 2 vs ceiling / gate 3): TBD
- L-SAT-vs-floor bet: TBD strictly by the LOCKED rules above.
- Artifacts: `~/zasolar_data/geid_temporal/fidelity_gate_20260706/` (fresh dir;
  the banked `llm_endtoend_storebacked_20260704/` was read-only input).
