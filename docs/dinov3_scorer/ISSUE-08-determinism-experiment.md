# ISSUE-08 — Bonus: deterministic-scorer run-to-run experiment (not gated)

> Tracer slice 8 of 8 · [TRACKER](TRACKER.md)

## Parent

PRD [`../dinov3_sat_scorer_backbone_prd.md`](../dinov3_sat_scorer_backbone_prd.md)
§Q4 (bonus) + Further Notes (determinism asterisk). User story 23.

## What to build

A bonus, **non-gated** experiment. The reproducibility study attributed the
*dominant* run-to-run variance to the adaptive search (L2) and imagery selection
(L3) layers, with the (stochastic) LLM (L1) the *smallest* source. Run the
**deterministic** frozen student twice over one fixed cohort and measure whether a
deterministic scorer *also* collapses that L2/L3 run-to-run variance, or whether
the variance persists (confirming it is genuinely search/imagery, not the scorer).

This is a learning experiment only — **no rollout decision depends on it**, and it
does not feed the gate.

## Acceptance criteria

- [ ] Deterministic student run twice on one fixed cohort under identical config.
- [ ] Run-to-run interval agreement reported and compared against the prior Gemini-era study's L2/L3 variance numbers.
- [ ] A short note states whether a deterministic scorer collapses the run-to-run variance, and how much remains attributable to L2/L3.
- [ ] Explicitly marked **not gated**; no production decision depends on the result.

## Blocked by

- [ISSUE-04](ISSUE-04-train-head-calibrate.md) — need a trained, deterministic student to run twice.
