# ISSUE-15: P4-A — bounded teacher adjudication + escalation monitor

Status: ready-for-agent
Phase: 4 — Pipeline shape
Blocked by: ISSUE-07, ISSUE-14 (+ fidelity gate, dinov3 slice 6)

## Parent

[`../install_date_optimization_v2_prd.md`](../install_date_optimization_v2_prd.md) — D13 (step A). User stories 29, 30, 31.

## What to build

The second migration step: the (fidelity-gated) student's verdicts own
steady-state frames; the teacher adjudicates only a deterministic
**escalation set** — (i) frames inside the calibrated abstain band (set from
the co-teacher disagreement distribution measured on the training cohort),
(ii) ± k frames around the student's detected transition, (iii) anomaly
patterns (isolated presents, dip shapes). The escalation set is a **pure
function of student scores**, so the teacher call count is enumerable up
front, bounded (~expected 7–18% of frames), and fully cacheable.

An **escalation-rate monitor** with the expected band ships in the same
slice: a spike in escalation rate is the ground-truth-free alarm for student
miscalibration.

## Acceptance criteria

- [ ] Escalation set enumerable before any teacher call; size bound asserted
- [ ] Hybrid vs teacher-full-stack interval agreement on the banked panel within the 0.911 band, undated-flip ≤ 0.05
- [ ] Per-stratum results no worse than the full-stack panel's per-stratum table
- [ ] Escalation rate within the expected band on the panel cohort; monitor alarms on a synthetic miscalibration test
- [ ] Transition-window frames are always teacher-adjudicated (no student-only transitions; test)

## Blocked by

- ISSUE-07 (verdict store), ISSUE-14 (P4-E in place); fidelity gate from the dinov3 tracker (slice 6)
