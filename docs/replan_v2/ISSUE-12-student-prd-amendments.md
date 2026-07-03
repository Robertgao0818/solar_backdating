# ISSUE-12: Apply D12 amendments to the student PRD/issues

Status: done
Phase: 3 — Distillation (amendments)
Blocked by: none

## Parent

[`../install_date_optimization_v2_prd.md`](../install_date_optimization_v2_prd.md) — D12. User stories 23, 24, 25, 26, 27.

## What to build

A docs/spec pass over the student PRD
([`../dinov3_sat_scorer_backbone_prd.md`](../dinov3_sat_scorer_backbone_prd.md))
and its issue folder ([`../dinov3_scorer/`](../dinov3_scorer/)), landing the
seven fact-check-driven amendments so implementation starts from real
numbers:

1. Label-harvest sizing re-derived from on-disk counts (**23,147 unique
   retained anchors / 250,502 rounds** — the 26,820 figure is stale and used
   inconsistently).
2. The chip-target manifest named as a **hard dependency** for chip re-render
   (scan states carry no anchor coordinates).
3. The ~28% ambiguous-terminal-status fraction handled explicitly in
   training-set stratification (it is a major bite, not a footnote).
4. Training compute = **RunPod** (owner decision 2026-07-03), following the
   repo's pod workflow rules.
5. DINOv3 custom-licence review added as a pre-ship task (weights are
   ungated but under a non-permissive Meta licence).
6. Fidelity-gate pipeline-agreement baseline re-based on the Phase-0 decoder
   under D8 evaluation rules (inventory-weighted, like-for-like).
7. Co-teacher dual-scoring added to the training-cohort plan as a calibration
   instrument (measures the student-teacher disagreement distribution that
   sets Phase 4's abstain band).

Also: the dinov3 tracker's slice 1 marked **superseded** → ISSUE-05 here, and
stale terminology fixed (the scan-state confidence field is `confidence`, not
`pv_score`; `pv_score` names only the new seam contract).

## Acceptance criteria

- [x] All seven amendments landed in the respective docs (PRD "D12 amendments" section + in-line at Q6/Q7, D1–D3, D6, D7, US-9/15/16)
- [x] dinov3 TRACKER slice 1 marked superseded with a link to replan_v2 ISSUE-05; dependency graph updated (seam root → replan_v2 5 ✅; slice 6 gained replan_v2 2 as the decoder-baseline dependency; waves re-cut, slice 3 unblocked)
- [x] No stale 26,820 or on-disk-field `pv_score` references remain in the dinov3 docs (grep-verified; `pv_score` appears only as the seam-contract field, with the `confidence` mapping stated)
- [x] Downstream dinov3 issues (02, 04, 06, 07) reference the amended numbers/gates (02: counts + chip-manifest + ambiguous stratification; 03: unblocked via replan_v2 5; 04: RunPod + co-teacher; 06: decoder-based gate; 07: licence pre-ship)

## Blocked by

None - can start immediately
