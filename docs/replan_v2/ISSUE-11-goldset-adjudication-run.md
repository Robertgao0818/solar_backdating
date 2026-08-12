# ISSUE-11: Codex-reviewed reference set + QA report

Status: ready-for-agent
Phase: 2 — Accuracy channel
Blocked by: ISSUE-09, ISSUE-10

> **Review-mode decision (2026-08-01):** the owner replaced future human
> adjudication with Codex visual review over the frozen local strips. This is
> an external AI review channel relative to Gemini, not an independent human
> gold standard. Use the protocol in
> [`CODEX_VISUAL_REVIEW_PROTOCOL.md`](CODEX_VISUAL_REVIEW_PROTOCOL.md), report
> Codex repeat/review agreement, and do not claim physical install-date accuracy.
>
> **Prep landed (2026-07-05, this commit):** verdict codebook (WI-1), villa oversample
> knob (WI-2), and dispute full-stack frames (WI-3) are implemented with all ACs tested
> (full suite green). Remaining before Codex review: the **n=500 package build**
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
> 54 UNDATABLE candidates in `strips/builder_report.csv`. Codex review can start.

## Parent

[`../install_date_optimization_v2_prd.md`](../install_date_optimization_v2_prd.md) — D10, D11. User stories 19, 21, 22.

## What to build

The Codex visual adjudication plus the report that turns it into the
program's first structured external-AI QA result:

- Review n=300–500 stratified anchors (jump critical point only) in the
  ISSUE-10 UI or equivalent frozen strip package; run a fresh blind Codex pass
  on at least 20% with a new `review_run_id`.
- Compute **Codex-reviewed bracket agreement** with Wilson 95% CI, overall and
  per stratum; report `codex_repeat_agreement` on the overlap.
- Adjudicate the 15 one-way disagreements (full-stack dated / production
  undated) as Codex evidence, preserving `CONFIRM`/`SHIFT`/`UNDATABLE` and the
  rationale instead of treating the result as human truth.
- Publish the QA report with D11-compliant wording: first-visible-appearance
  under imagery-cadence censoring, never physical install dates or human-gold
  accuracy.

## Acceptance criteria

- [ ] n ≥ 300 adjudicated; verdict manifest complete
- [ ] Codex repeat agreement reported from the 20% fresh blind overlap
- [ ] Wilson 95% CI ≤ ±5.5 pp on the headline Codex-reviewed bracket-agreement rate
- [ ] The 15 dated-vs-undated disputes each carry a Codex verdict; systematic-over-dating question documented as QA evidence
- [ ] QA report published with D11-compliant claim phrasing; feeds the estimator adoption decision (ISSUE-04 memo) and the student fidelity gate context without claiming human/physical truth

## Blocked by

- ISSUE-09 (contradiction flags for stratification)
- ISSUE-10 (tooling)
