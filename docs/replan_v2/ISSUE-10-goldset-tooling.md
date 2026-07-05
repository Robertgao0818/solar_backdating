# ISSUE-10: Gold-set tooling — jump-point strip UI + verdict manifest + stratified sampler

Status: ready-for-agent
Phase: 2 — Accuracy channel
Blocked by: none — ISSUE-09 contradiction flags are now AVAILABLE (`cohort_audit_anchors.csv`, 2026-07-05); the sampler consumes them. Start here: [`ISSUE-10-handoff-2026-07-05.md`](ISSUE-10-handoff-2026-07-05.md).

## Parent

[`../install_date_optimization_v2_prd.md`](../install_date_optimization_v2_prd.md) — D10. User stories 17, 18, 20.

## What to build

Everything needed so a human can adjudicate the **jump critical point only**
(owner decision 2026-07-03 — no whole-stack review):

1. **Strip UI**: extend the existing per-anchor chip-strip QA HTML builder to
   render, per anchor, the claimed latest-absent and earliest-present frames
   ±1–2 flanks, overlaid/captioned with the CoJ true-date chips, the Vexcel
   chip, and any Wayback capture falling in-window. Verdict buttons:
   CONFIRM / SHIFT (with corrected bracket entry) / UNDATABLE, writing a
   machine-readable verdict manifest. Self-contained HTML (works offline).
2. **Stratified sampler**: seeded, reproducible sampling of n=300–500 anchors
   by terminal status × confidence × audit-contradiction flag, with a 20%
   double-annotation overlap assignment.
3. **Window chip re-download**: idempotent, LLM-free re-render of the window
   frames from retained scan metadata (chips were deleted in the disk
   cleanup); unrecoverable frames (imagery availability shifted) are dropped
   and reported.

## Acceptance criteria

- [ ] Strip HTML renders offline and self-contained; golden-file test on the builder
- [ ] Verdicts round-trip: UI → manifest → loadable records (test)
- [ ] Sampler seeded/reproducible; stratification uses contradiction flags when present, degrades gracefully when absent
- [ ] Double-annotation overlap emitted with annotator assignment
- [ ] 10-anchor dry run completed; time-per-anchor measured and recorded (feeds the n=300 vs 500 decision)

## Blocked by

None - can start immediately
