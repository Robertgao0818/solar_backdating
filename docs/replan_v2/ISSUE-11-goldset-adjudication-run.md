# ISSUE-11: Gold-set adjudication run + accuracy report

Status: ready-for-human
Phase: 2 — Accuracy channel
Blocked by: ISSUE-09, ISSUE-10

## Parent

[`../install_date_optimization_v2_prd.md`](../install_date_optimization_v2_prd.md) — D10, D11. User stories 19, 21, 22.

## What to build

The human adjudication itself plus the report that turns it into the
program's first citable accuracy claim:

- Adjudicate n=300–500 stratified anchors (jump critical point only) in the
  ISSUE-10 UI; 20% double-annotated (~15–20 person-hours total at measured
  per-anchor rates).
- Compute **first-visible-appearance interval-hit-rate** with Wilson 95% CI,
  overall and per stratum; inter-annotator agreement on the overlap.
- Arbitrate the 15 one-way disagreements (full-stack dated / production
  undated) — the question no internal metric can answer: recovered search
  give-ups, or over-dating on thin evidence?
- Publish the accuracy report; all claims phrased per D11
  (first-visible-appearance under imagery-cadence censoring — never physical
  install dates).

## Acceptance criteria

- [ ] n ≥ 300 adjudicated; verdict manifest complete
- [ ] Inter-annotator agreement reported from the 20% overlap
- [ ] Wilson 95% CI ≤ ±5.5 pp on the headline interval-hit-rate
- [ ] The 15 dated-vs-undated disputes each carry a human verdict; systematic-over-dating question answered
- [ ] Accuracy report published with D11-compliant claim phrasing; feeds the estimator adoption decision (ISSUE-04 memo) and the student fidelity gate context

## Blocked by

- ISSUE-09 (contradiction flags for stratification)
- ISSUE-10 (tooling)
