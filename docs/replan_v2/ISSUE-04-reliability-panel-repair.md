# ISSUE-04: Reliability panel repair — enlarge dominant stratum + tight-crop rescore

Status: done
Phase: 0 — Estimator
Blocked by: none (API budget approved 2026-07-03)

**Completed 2026-07-03** — see the decision memo:
[`ISSUE-04-decision-memo-2026-07-03.md`](ISSUE-04-decision-memo-2026-07-03.md).
Headline: weighted year-hit 0.469→0.757 was an n=2 artifact (dominant stratum
now n=12); under-resolution hypothesis CONFIRMED (12 m/256 px kills
undated-flips); sustained retained as interim estimator; decoder gates
re-anchored to the extended panel. 3,110 calls, 0 errors.

## Parent

[`../install_date_optimization_v2_prd.md`](../install_date_optimization_v2_prd.md) — D4. User stories 7, 8.

## What to build

Repair the evidence base that any cohort-wide estimator decision rests on.
Two targeted extensions to the banked reliability panel:

1. **Enlarge the dominant stratum**: sample 6–8 additional `done_appears`
   units (population weight ~69%, currently n=2) and run the existing
   full-stack no-search protocol on them (same K=10 reps, window=8, same
   sequence instrument), extending the banked frame-verdict table.
2. **Tight-crop rescore**: re-score the failed-stratum (`gemini_failed`)
   units at a tight crop (~12 m / 256 px) to test the under-resolution
   hypothesis — if they date cleanly, that stratum's noise is an imaging
   artifact (which changes the decoder's emission model and the student's
   chip-rendering spec), not scorer give-up.

Then re-run the ISSUE-01 harness on the extended panel and produce the
decision memo: adopt/reject the decoder cohort-wide per D3 + D8 (weighted
headline now on n≥8 dominant-stratum support).

## Acceptance criteria

- [x] `done_appears` panel support ≥ 8 units, scored K=10 on the full stack (n=12: +10 new units, 4 anchors, seed-42 SRS continuation)
- [x] Weighted + unweighted estimator comparison re-computed (ISSUE-01 harness was mid-build; computed via `panel_repair_d8_compare.py`, which regression-reproduces the banked 0.911/0.807/0.046 exactly; extended panel is the harness's designated regression input)
- [x] Tight-crop result answers the under-resolution hypothesis with a clear verdict either way (CONFIRMED: undated-flip 0.113→0.000, abstain 0.025→0.001)
- [x] Decision memo recorded: estimator adoption verdict + implications for emission model / chip spec
- [x] Total API spend logged and within the approved envelope (3,110 sequence calls, 0 errors)

## Blocked by

None - can start immediately (harness from ISSUE-01 needed only for the final comparison step)
