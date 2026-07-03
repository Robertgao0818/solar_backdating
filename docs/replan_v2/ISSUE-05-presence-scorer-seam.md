# ISSUE-05: PresenceScorer seam across all four call-sites

Status: done
Phase: 1 — Provenance
Blocked by: none

Supersedes: [`../dinov3_scorer/ISSUE-01-presence-scorer-seam.md`](../dinov3_scorer/ISSUE-01-presence-scorer-seam.md)
(the old slice covered only the adaptive-scan path; the fact-check found four
scorer call-sites, and the seam now serves the whole program — verdict store,
student, escalation compositions — not only the distillation).

## Parent

[`../install_date_optimization_v2_prd.md`](../install_date_optimization_v2_prd.md) — D1(i), D7. User stories 12, 13, 14.

## What to build

One injected **PresenceScorer** callable through which every scoring path
scores chips: the adaptive scan, the census-narrowing scan, the full-stack
validation harness, and the chip-group matrix scorer. Hardwired scorer
imports at all four sites are removed; the Gemini scorer becomes the first
registered implementation (selected by config/flag); the existing dry-run
stub keeps working.

State-machine decoupling ships in the same slice: the >50%-failed ambiguity
rule's failure sentinel becomes scorer-parameterized (each scorer
implementation declares its failure decision-source), and the declared
quality-flag / decision-source vocabularies are enforced at write time, with
new values registrable additively.

## Acceptance criteria

- [x] All four call-sites route through the seam; no hardwired scorer imports remain
- [x] Fake-scorer injection tests extended from the existing sequence-path pattern to the adaptive-scan and census-scan paths
- [x] Downstream-invariance test: identical verdicts through the seam → byte-identical scan states (`test_seam_downstream_invariance.py`, cross-implementation)
- [x] Ambiguity rule triggers for a non-Gemini scorer's declared failure source (unit test)
- [x] Unknown quality-flag / decision-source values rejected at write time; registration path for additive values tested (scan-state persistence + all four call-site ingest points via `validate_emission`)
- [x] Dry-run stub and existing production flags regression-clean

## Blocked by

None - can start immediately
