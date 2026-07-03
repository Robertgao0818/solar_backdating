# ISSUE-16: P4-D — sentinel audits, teacher exits hot path

Status: ready-for-agent
Phase: 4 — Pipeline shape
Blocked by: ISSUE-15

## Parent

[`../install_date_optimization_v2_prd.md`](../install_date_optimization_v2_prd.md) — D13 (step D). User story 32.

## What to build

The endgame steady state: the frozen student scores everything on the fixed
grid; the teacher exits the hot path and is retained as a **scheduled
sentinel audit** — a frozen stratified sentinel cohort (~228 chips across all
terminal strata, ≈3.6k calls per audit) re-scored by the *current* teacher on
a schedule. Because the student is frozen, movement in the
student-vs-current-teacher agreement time series isolates teacher/imagery
drift. A human escalation queue remains for high-value anchors, and the
year-histogram TVD against the frozen production distribution runs as a
population-level plausibility invariant.

## Acceptance criteria

- [ ] Sentinel run automated and schedulable; produces a per-stratum agreement time-series artifact
- [ ] Alert thresholds defined on the time series (agreement drop / TVD excursion)
- [ ] Hot path issues zero hosted calls on a cohort run (measured)
- [ ] Human queue path for high-value anchors functional and documented
- [ ] Rollback documented and tested: one flag returns the pipeline to P4-A (bounded adjudication)

## Blocked by

- ISSUE-15 (P4-A in production)
