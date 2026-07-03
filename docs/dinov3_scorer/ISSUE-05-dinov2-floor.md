# ISSUE-05 — DINOv2 ViT-S/14 falsification floor

> Tracer slice 5 of 8 · [TRACKER](TRACKER.md)

## Parent

PRD [`../dinov3_sat_scorer_backbone_prd.md`](../dinov3_sat_scorer_backbone_prd.md)
§D2 (Q2). User stories 2, 11.

## What to build

Stand up the **mandatory cheap falsification floor**: the proven DINOv2 ViT-S/14
(`vit_small_patch14_dinov2.lvd142m`, 22M) behind the **same** `PresenceScorer`
seam, **frozen backbone + light head**, anchor-conditioned the same way, trained
and calibrated with the **same recipe** as [ISSUE-04](ISSUE-04-train-head-calibrate.md)
on the **same** ISSUE-02 splits. Reuse the sibling `solar_cls` head / recipe /
chip / threshold tooling. Give it a distinct `decision_source` (e.g.
`"dinov2_floor"`), selectable via the same backbone flag.

The point is to make the DINOv3-L-SAT bet **falsifiable**: if a 14× smaller,
natural-image-pretrained backbone matches L-SAT on the gate, the SAT/L choice is
wrong (GEO-Bench-2 evidence makes this a live possibility, not a formality). This
slice produces a gate-ready floor model — it does not itself render a verdict
(that's [ISSUE-06](ISSUE-06-fidelity-gate.md)).

## Acceptance criteria

- [ ] DINOv2 ViT-S/14 scorer implements the same seam, frozen backbone + light head, anchor-conditioned identically.
- [ ] Trained + calibrated on the same ISSUE-02 splits with the ISSUE-04 recipe; reuses `solar_cls` tooling where applicable.
- [ ] Distinct `decision_source` value; selectable via the same backbone flag.
- [ ] Frozen checkpoint + thresholds pinned under `~/zasolar_data/`.
- [ ] Ready to be scored under the ISSUE-06 gate on the same three numbers as DINOv3-L-SAT.

## Blocked by

- [ISSUE-04](ISSUE-04-train-head-calibrate.md) — training + calibration recipe established; reuses the same data splits.
