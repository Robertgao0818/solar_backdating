# ISSUE-07 — Feature-flag rollout + ops profile

> Tracer slice 7 of 8 · [TRACKER](TRACKER.md)

## Parent

PRD [`../dinov3_sat_scorer_backbone_prd.md`](../dinov3_sat_scorer_backbone_prd.md)
§D7 (rollout) / D8 (data discipline) + Further Notes (VRAM). User stories 1, 2, 3, 22, 26.
**Amended 2026-07-03** by the v2 program's D12.iv–v
([`replan_v2/ISSUE-12`](../replan_v2/ISSUE-12-student-prd-amendments.md)):
the DINOv3 licence review is a pre-ship task; compute is RunPod.

## What to build

Productionize the swap behind the feature flag. Gemini stays the **default** and
A-B fallback until the gate passes; based on the
[ISSUE-06](ISSUE-06-fidelity-gate.md) verdict, set the production default backbone
(or document keeping Gemini if the gate failed). Make provenance per-round
traceable, confirm region + data discipline, and document the ops profile so pods
are sized without OOM.

- **Licence (pre-ship, D12.v):** DINOv3 weights are ungated but distributed
  under **Meta's custom non-permissive DINOv3 licence**. Complete a licence
  review — redistribution, commercial-use, and derivative-artifact terms
  (trained head + pinned checkpoint) — and record the outcome in the
  verdict/rollout doc **before** the student becomes the production default.
  A legal constraint must not surface after distillation.
- **Provenance:** `decision_source` identifies DINOv3-scored rounds end-to-end; the
  Case-E rule (>50% failed → ambiguous) maps to a **backbone-appropriate** failure
  terminal status.
- **Region/data discipline:** region resolution via explicit `region` + registry
  (ADR-0002, no grid-ID pattern matching); weights + re-downloaded chips under
  `~/zasolar_data/`, only fixtures committed.
- **Ops:** document a VRAM/throughput profile for the 303M backbone on the
  RunPod pod class used for training/inference (D12.iv) — batch size, peak
  VRAM off the first batch, recommended pod / parallelism — heed the prior
  5090 parallel-OOM history; default to conservative parallelism.

## Acceptance criteria

- [ ] Feature flag selects the production scorer; Gemini default until the gate passes, then default set per gate verdict (decision recorded in the verdict doc / tracker).
- [ ] DINOv3 licence review completed and its outcome recorded **before** the default flips to the student (pre-ship task, D12.v).
- [ ] Gemini retained behind the same seam as fallback / A-B comparator.
- [ ] `decision_source` identifies DINOv3-scored rounds end-to-end; Case-E failure maps to a backbone-appropriate terminal status.
- [ ] Region resolution uses explicit `region` + registry (no grid-ID pattern matching).
- [ ] VRAM/throughput profile for the 303M backbone documented (batch size, peak VRAM, recommended pod/parallelism).
- [ ] Data-discipline check: weights + re-downloaded chips under `~/zasolar_data/`, gitignored; only fixtures committed.

## Blocked by

- [ISSUE-06](ISSUE-06-fidelity-gate.md) — the gate verdict drives the default-backbone decision.
