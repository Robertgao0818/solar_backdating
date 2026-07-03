# ISSUE-04 — Train light head + calibrate global abstain band

> Tracer slice 4 of 8 · [TRACKER](TRACKER.md)

## Parent

PRD [`../dinov3_sat_scorer_backbone_prd.md`](../dinov3_sat_scorer_backbone_prd.md)
§D3 / D5 + Q8. User stories 4, 5, 18, 20.
**Amended 2026-07-03** by the v2 program's D12.iv + D12.vii
([`replan_v2/ISSUE-12`](../replan_v2/ISSUE-12-student-prd-amendments.md)):
RunPod training compute and the co-teacher calibration instrument.

## What to build

Train the 3-class light head on the [ISSUE-02](ISSUE-02-distillation-training-set.md)
distillation set with the **backbone frozen** (no fine-tuning; LoRA/PEFT only if
the later gate fails). Then calibrate a **single global** lo/hi abstain band on the
**held-out Gemini labels** so the student's decisions agree with Gemini:

- `score < lo` → absent, `score > hi` → present, in between → ambiguous (abstain);
  `unusable` is its own class (feeds Case-E recovery downstream).

Freeze and **pin** the resulting checkpoint + thresholds under `~/zasolar_data/`.
After this, the DINOv3 `score()` emits calibrated present/absent/abstain, and
`pv_present` / `quality_flag` derive from the band — **not** from any LLM
self-report. Validate the small-chip upscaling policy in the first ablation here.

**Training compute = RunPod** (owner decision 2026-07-03, D12.iv): run
frozen-backbone feature extraction and head training on a pod, following the
repo's pod workflow rules (main-repo `.claude/rules/05`/`08` + the runpod-ops
skill) — local VRAM is not a dependency. Pinned artifacts still land under
`~/zasolar_data/` locally.

**Co-teacher dual-scoring (calibration instrument, D12.vii).** After training
+ calibration, score the training cohort with the student and pair it with the
already-harvested teacher labels; report the student–teacher disagreement
distribution **per stratum** (sub-domain × GSD tier × terminal status). This
distribution sets Phase 4's abstain band and escalation k
([replan_v2 ISSUE-14](../replan_v2/ISSUE-14-p4e-student-selected-windows.md) /
[ISSUE-15](../replan_v2/ISSUE-15-p4a-bounded-adjudication.md))
— it is a measurement on the training cohort, **not** a production shape
(production dual-scoring is rejected in the v2 replan).

## Acceptance criteria

- [ ] Light head trained with the backbone frozen; training reads only the ISSUE-02 **train** split.
- [ ] Global lo/hi band calibrated on the held-out **calibration** Gemini labels; thresholds recorded alongside the checkpoint.
- [ ] Frozen checkpoint + thresholds pinned under `~/zasolar_data/` (versioned, not committed); load path is deterministic.
- [ ] DINOv3 `score()` now emits calibrated present/absent/abstain + `unusable`; `pv_present` / `quality_flag` derived from the band.
- [ ] Held-out **chip-level** present/absent agreement vs Gemini reported (diagnostic; localizable by GSD tier and terminal-status bucket).
- [ ] Upscaling-policy ablation result recorded (policy chosen + why).
- [ ] Head training ran on RunPod per the repo pod workflow rules (D12.iv); pod/VRAM notes recorded for ISSUE-07's ops profile.
- [ ] Co-teacher disagreement distribution reported per stratum (sub-domain × GSD tier × terminal status) on the training cohort; artifact archived for Phase 4's abstain-band / escalation-k calibration (D12.vii).

## Blocked by

- [ISSUE-02](ISSUE-02-distillation-training-set.md) — training data + anchor splits.
- [ISSUE-03](ISSUE-03-dinov3-scorer-scaffold.md) — scorer scaffold + head architecture.
