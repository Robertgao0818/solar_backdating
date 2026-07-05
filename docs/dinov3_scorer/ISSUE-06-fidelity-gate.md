# ISSUE-06 — Fidelity gate (three numbers, both backbones)

> Tracer slice 6 of 8 · [TRACKER](TRACKER.md)
>
> **Ready to start (2026-07-05):** all three blockers ✅. Readiness map + file-level execution design (inputs inventory, tools-vs-gap, budget, non-dependency on ISSUE-21, pre-registered tie-break): [`ISSUE-06-prep-2026-07-05.md`](ISSUE-06-prep-2026-07-05.md). Nothing below is done yet.

## Parent

PRD [`../dinov3_sat_scorer_backbone_prd.md`](../dinov3_sat_scorer_backbone_prd.md)
§D7 + Q6. User stories 8, 9, 10, 11.
**Amended 2026-07-03** by the v2 program's D12.vi
([`replan_v2/ISSUE-12`](../replan_v2/ISSUE-12-student-prd-amendments.md)):
gate number (2) re-based on the Phase-0 decoder under the D8 standing rules.

## What to build

Run the **fidelity gate**, reusing/extending the existing end-to-end interval
agreement harness (`llm_endtoend_analyze` — treat one student run as one "rep",
reusing its interval-agreement key construction). Report the three gate numbers
for **both** DINOv3-L-SAT and the DINOv2-S floor:

1. **Reproducibility (primary):** student **self** rep↔rep interval agreement ≈ 1.0
   (deterministic) — proves the "no wobble" goal.
2. **No-answer-change (re-based 2026-07-03, D12.vi):** student-pipeline vs
   Gemini-pipeline interval agreement with **both pipelines decoded by the
   Phase-0 decoder**
   ([replan_v2 ISSUE-02](../replan_v2/ISSUE-02-changepoint-posterior-decoder.md))
   under the **D8 standing rules** — like-for-like estimators,
   inventory-weighted headline, dated-only denominator reported alongside.
   Bar = Gemini's own rep↔rep ceiling **re-derived under the same rules** on
   the banked rep panel. The previously quoted **~0.74 is retired as a bar**
   (it exists only in a hand-authored `derived_cuts.json` no script writes,
   and blends strata — replan C7). The bound-above logic stands: the student
   cannot agree with the teacher better than the teacher agrees with itself.
3. **Distillation fidelity (diagnostic):** held-out **chip-level** present/absent
   agreement vs Gemini.

**Accuracy is explicitly out of the gate** — no independent install-date truth
exists. Output a gate verdict doc: the numbers + pass/fail per backbone, plus the
**L-SAT-vs-floor** comparison (if the 22M floor matches L-SAT, the SAT/L bet is
falsified). This is an offline benchmark reported in `docs/`, **not** a CI assertion.

## Acceptance criteria

- [ ] Gate harness produces the three numbers for DINOv3-L-SAT, reusing the existing interval-agreement key construction.
- [ ] Same three numbers produced for the DINOv2-S floor.
- [ ] Number (1) demonstrates self rep↔rep ≈ 1.0 for the deterministic student(s).
- [ ] Number (2) reported under the D8 rules — both pipelines through the Phase-0 decoder, inventory-weighted, dated-only denominator alongside — against a teacher rep↔rep ceiling re-derived on the same cohort the Gemini reps used. No hand-authored 0.74 anywhere in the verdict.
- [ ] Gate verdict doc records pass/fail per backbone and whether the floor matches L-SAT (falsification check).
- [ ] Gate is an offline benchmark reported in `docs/`, not asserted in CI.

## Blocked by

- [ISSUE-04](ISSUE-04-train-head-calibrate.md) — trained + calibrated DINOv3.
- [ISSUE-05](ISSUE-05-dinov2-floor.md) — DINOv2-S floor model.
- [replan_v2 ISSUE-02](../replan_v2/ISSUE-02-changepoint-posterior-decoder.md) — the Phase-0 decoder is the gate's pipeline-agreement baseline (D12.vi).
