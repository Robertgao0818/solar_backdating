# ISSUE-11: Gold-set adjudication run + accuracy report

Status: ready-for-human
Phase: 2 — Accuracy channel
Blocked by: ISSUE-09, ISSUE-10

> **Prep landed (2026-07-05, this commit):** verdict codebook (WI-1), villa oversample
> knob (WI-2), and dispute full-stack frames (WI-3) are implemented with all ACs tested
> (full suite green). Remaining before human adjudication: the **n=500 package build**
> per [`ISSUE-11-prep-design-2026-07-05.md`](ISSUE-11-prep-design-2026-07-05.md) §WI-4
> runbook (locked: n=500, seed 20260705, `--page-size 75`, 20% overlap, 15 disputes
> forced). Collision forensics + rebuild record:
> [`ISSUE-11-prep-handoff-2026-07-05.md`](ISSUE-11-prep-handoff-2026-07-05.md).
>
> **Package built (2026-07-05 late):** `~/zasolar_data/geid_temporal/goldset_real_20260705/`
> — 612 assignment rows (306/annotator: 100 double + 200 split + 6 forced disputes), all 15
> disputes resolved onto 6 c-anchors; 3894 frames re-rendered (2868 recovered — scan_tm
> 2310/2310 ok, fullstack 66/66 copied; 1026 CoJ-context frames source_missing = outside the
> ISSUE-09 audit-cohort chip coverage, expected); 10 self-contained strip pages
> (`--page-size 75`, 0 external refs; disputes land on `_p05` with fullstack rows + scale
> caveat). Preflight PASS: c0000542 rescued (not UNDATABLE), c0009873 banner-only,
> 54 UNDATABLE candidates in `strips/builder_report.csv`. Human adjudication can start.

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
