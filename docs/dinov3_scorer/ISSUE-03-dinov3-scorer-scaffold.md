# ISSUE-03 — DINOv3-L-SAT frozen scorer scaffold + backbone-selection flag

> Tracer slice 3 of 8 · [TRACKER](TRACKER.md)

## Parent

PRD [`../dinov3_sat_scorer_backbone_prd.md`](../dinov3_sat_scorer_backbone_prd.md)
§D2 / D3 / D4. User stories 13, 14, 18, 19, 26.

## What to build

A `PresenceScorer` implementation backed by a **frozen** DINOv3-L-SAT encoder
(timm `vit_large_patch16_dinov3.sat493m`, 303M, ungated) plus a light head, wired
so the swap mechanically works end-to-end **before the head is trained**. This
slice proves the *mechanism*, not the answers, using a random/placeholder head.

- **Frozen encoder** — no grad on the backbone (a frozen FM cannot be polluted by
  noisy pseudo-labels; full-FT of 303M on small noisy data is the failure to avoid).
- **Anchor conditioning** — crop centered on the anchor (reuse existing crop
  geometry), pool the center **k×k patch tokens** as the anchor-conditioned
  feature. No drawn marker (the marker was for the LLM to read; spatial pooling
  replaces it). Marker = optional ablation.
- **Light head** — linear / small MLP → 3 classes: present / absent / unusable.
- `score()` returns one `PresenceObservation` per pick with
  `decision_source="dinov3_frozen"`; `pv_score` = continuous present-class score
  (uncalibrated placeholder band for now — calibration is ISSUE-04).
- **Selection flag** — a config/CLI flag selects this backbone behind the seam so
  it can be exercised end-to-end; Gemini stays the default.

## Acceptance criteria

- [ ] DINOv3-L-SAT loads **frozen** (no backbone grad); anchor center-k×k token pooling implemented over the anchor-centered crop.
- [ ] 3-class light-head architecture defined; `score(picks, ...)` returns one observation per pick, in order, with `pv_present ∈ {True,False,None}`, finite `pv_score`, valid `quality_flag`, `decision_source="dinov3_frozen"`.
- [ ] **Backbone-parity test** on a tiny real chip fixture (shape/ordering, not accuracy).
- [ ] **Determinism test**: same fixture scored twice → identical `pv_score` (unit-level proof of the reproducibility motivation).
- [ ] **Downstream-invariance test**: canned DINOv3-mapped observations produce the same `scan_state.json` as the Gemini-mapped path for identical observation values.
- [ ] A config/CLI flag selects the DINOv3 scorer behind the seam (Gemini remains default).
- [ ] Encoder weights resolved from `~/zasolar_data/` (not committed); small-chip upscaling is a parameter, not hardcoded (its policy is validated in ISSUE-04's ablation, not frozen here).

## Blocked by

- ~~[ISSUE-01](ISSUE-01-presence-scorer-seam.md)~~ → superseded by
  [replan_v2 ISSUE-05](../replan_v2/ISSUE-05-presence-scorer-seam.md), which
  **landed 2026-07-03** (seam across all four call-sites,
  `scripts/temporal/presence_scorer.py`). This slice is **unblocked** — build
  against that seam contract.
