# ISSUE-20: Apply the D19 deliverable-caliber amendment (PRD delta)

Status: done
Phase: 0 — Estimator (amendment)
Blocked by: none (2, 3, 4 already done/discharged)
Completed: 2026-07-05 — the §7 mechanical delta of
[`PRD-AMENDMENT-P1-posterior-mass-caliber-2026-07-04.md`](PRD-AMENDMENT-P1-posterior-mass-caliber-2026-07-04.md)
was applied at owner sign-off (Option A): PRD D2 clause + D3 gate reword +
D3 `Amended` pointer blockquote + Amendment-2026-07-04 banner with **D19** +
Testing-Decisions bullet + Further-Notes provenance bullet; DECISION-A
addendum records the verdict flip **NO-GO → GO (fractional caliber, D19)**;
TRACKER rows 20/21, mermaid nodes/edges, and the ISSUE-02 note rewrite
landed; amendment file Status flipped to **ADOPTED (D19)**.

## Parent

[`../install_date_optimization_v2_prd.md`](../install_date_optimization_v2_prd.md) — D19. User stories 2, 4.
Trigger: [`DECISION-A-estimator-adoption-2026-07-04.md`](DECISION-A-estimator-adoption-2026-07-04.md) path P1 + the P1 amendment (Option A signed 2026-07-05).

## What to build

The §7 mechanical delta from the P1 amendment — a docs pass only (the §8
code-level items are explicitly NOT in this issue's scope):

1. §7.1 D2 — fractional channel formalised as the headline reporting object.
2. §7.2 D3 — operative gate reworded to the survival/fractional channel;
   hard-MAP band demoted to derived diagnostic, retired-with-cause.
3. §7.3 D3 — distinctly-tagged `Amended (2026-07-04, D19 … substantive)`
   pointer blockquote (NOT a second same-dated "Correction").
4. §7.4 — Amendment banner + D19 entry inserted after D18.
5. §7.5 — Testing-Decisions bullet: AC5 survival assertion is gate-bearing.
6. §7.6 — Further-Notes provenance bullet.

Plus: DECISION-A addendum, TRACKER updates (rows 20/21 + mermaid + ISSUE-02
note rewrite + `render_tracker.py`), and the amendment's own Status flip.

## Acceptance criteria

- [x] All six §7 edits present in the PRD (grep: `D19` appears in D2, D3,
  the amendment banner, Testing Decisions, and Further Notes)
- [x] D3 carries the `Amended` (not a second `Correction`) pointer
- [x] DECISION-A addendum records NO-GO → GO (fractional caliber, D19),
  Option A, 2026-07-05, with the rollback trigger and 0.067 disclosure
- [x] TRACKER rows 20/21 + mermaid nodes/edges + ISSUE-02 note rewritten;
  `tracker.html` regenerated
- [x] Amendment file Status = **ADOPTED (D19)**

## Out of scope (remaining §8 items, unassigned code work)

Production config/provenance switch (A4: estimator id + prior hash),
report-builder fractional switch (A5 caveats attached), and the gate-runner
assertion change (`issue03_gates.py` AC5 gate-bearing; hard-MAP band
reported-not-asserted — amendment §9.6). These are code changes tracked by
the amendment's §8 unchecked items.

## Blocked by

None — dependencies 2, 3, 4 were done/discharged before sign-off.
