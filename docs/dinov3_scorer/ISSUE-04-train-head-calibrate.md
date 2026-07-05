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

- [x] Light head trained with the backbone frozen; training reads only the ISSUE-02 **train** split.
- [x] Global lo/hi band calibrated on the held-out **calibration** Gemini labels; thresholds recorded alongside the checkpoint.
- [x] Frozen checkpoint + thresholds pinned under `~/zasolar_data/` (versioned, not committed); load path is deterministic.
- [x] DINOv3 `score()` now emits calibrated present/absent/abstain + `unusable`; `pv_present` / `quality_flag` derived from the band.
- [x] Held-out **chip-level** present/absent agreement vs Gemini reported (diagnostic; localizable by GSD tier and terminal-status bucket).
- [x] Upscaling-policy ablation result recorded (policy chosen + why).
- [x] Head training ran on RunPod per the repo pod workflow rules (D12.iv); pod/VRAM notes recorded for ISSUE-07's ops profile.
- [x] Co-teacher disagreement distribution reported per stratum (sub-domain × GSD tier × terminal status) on the training cohort; artifact archived for Phase 4's abstain-band / escalation-k calibration (D12.vii).

## Blocked by

- [ISSUE-02](ISSUE-02-distillation-training-set.md) — training data + anchor splits.
- [ISSUE-03](ISSUE-03-dinov3-scorer-scaffold.md) — scorer scaffold + head architecture.

## Execution notes (2026-07-05)

Trained on a RunPod RTX 5090 (secure cloud, 32 GB, driver 570.124.06) via
`scripts/temporal/train_dinov3_head.py` (extract-features → train → calibrate →
evaluate → co-teacher). Feature extraction ran the frozen backbone at ~108
chips/s, peak VRAM 1.91 GB at input 256 (well under budget). Local↔pod transfer
used `runpodctl send/receive` P2P with md5 verification (scp >100 MB banned; a
first attempt was truncated when a foreground SSH timeout killed the receiver
mid-stream and left a byte-count-correct but md5-mismatched file — re-sent with
the SSH kept alive in a background shell and a hard md5 gate before untar).

### Winner + calibration

**Winner = `nomarker_bilinear512_k6`** (input 512, center-pool-k 6, bilinear
upscale), selected by max **held-out calibration-half** decided-agreement among
the marker-free arms. Pinned bundle:
`~/zasolar_data/models/dinov3_sat/head_v1_20260705/head_v1.{pt,json}` (+
`reports/`, `head_v1.calibration_frontier.csv`); `head_pt_sha256` in the sidecar
verified against the pinned `.pt`. Calibration band **lo=0.46, hi=0.50** (82
calib anchors; in-sample decided-agreement 0.9386, abstain 0.0984). Local GPU
seam smoke confirmed `score()` resolves config + band from the sidecar and emits
calibrated present/absent/abstain/unusable from P(present) — not the LLM
self-report — with a missing chip degrading to `dinov3_failed`/`missing_chip`.

### Held-out report-half agreement (the honest generalization number)

Report half is disjoint from the calibration half. **n=858, coverage 0.885,
decided-agreement 0.797** — i.e. the calib-half 0.939 is in-sample optimism; the
band is somewhat overfit to the 82-anchor calib set (**~14 pp gap**). Downstream
(ISSUE-05 floor, ISSUE-06 gate) must cite **0.797**, not 0.939. Gradients: GSD
z19 0.870 > z20 0.779; `non_cbd_satellite` 0.802 > `cbd_aerial_mosaic` 0.750;
`le_2022` 0.831 > `ge_2023` 0.720 (recent imagery is harder). **Weak spot:**
`unusable`-class recall is poor — of 120 teacher-unusable report rows the student
called only 20 unusable (40 present, 60 absent); unusable supervision is thin
(1,252 rendered rows). Case-E recovery downstream should not lean on the
student's `unusable` call alone.

### Upscaling-policy ablation (AC6)

| arm | input | k | upscale | band | calib decided-agree | val macro-F1 |
|---|---|---|---|---|---|---|
| nomarker_bilinear256 | 256 | 3 | bilinear | [0.42,0.44] | 0.9344 | 0.6335 |
| nomarker_bicubic256 | 256 | 3 | bicubic | [0.42,0.44] | 0.9344 | 0.6309 |
| **nomarker_bilinear512_k6** | 512 | 6 | bilinear | [0.46,0.50] | **0.9386** | 0.6348 |
| marked_bilinear256 (diag) | 256 | 3 | bilinear | [0.30,0.74] | 0.9445 | 0.5942 |

Conclusions: (1) **bilinear ≡ bicubic** at 256 — identical decided-agreement and
band, negligible val-F1 delta → interpolation kernel is immaterial; keep
bilinear (canonical, cheaper). (2) **512/k6 > 256/k3** by +0.42 pp
decided-agreement + lower abstain → higher effective resolution helps marginally;
chosen as winner. (3) The **marked** arm (drawn ring/cross overlay) scores +0.6–1
pp over the nomarker arms — a real but small train/serve-skew crutch. It is
**diagnostic only** and never production-eligible (PRD D4 forbids a drawn marker
on the student); the ~1 pp nomarker penalty is accepted.

### Co-teacher dual-scoring (D12.vii, AC8)

Full training cohort (8,496 rows, 35 strata) scored with the student and paired
with the teacher labels: overall disagreement-over-decided **0.2367** (122
abstains). Per-stratum table archived at
`reports/co_teacher/co_teacher_disagreement.csv` for Phase-4 abstain-band /
escalation-k calibration. Worst strata concentrate in low-zoom satellite
(z18 `non_cbd_satellite`, 0.576, n=33 — mostly student→unusable), the
`done_already_present_before_geid_history` terminal status (inherently
ambiguous), and recent CBD-aerial (`cbd_aerial_mosaic` z20 `ge_2023`, 0.342) —
consistent with the report-half gradients.

### Design decisions resolved during execution

1. **Unusable-class supervision** was absent from the slice-2 rendered chips.
   Rendered 1,252 quality-driven `unusable` student rows (label_arm=`unusable`),
   composing with D12.iii's exclusion of `done_ambiguous_*` rows (they never
   become `unusable`-class supervision). Student selection = subset anchors ∩
   `version!=""` ∩ (present/absent ∨ (unusable ∧ not `done_ambiguous_*`)).
2. **Marker-free render (PRD D4).** All slice-2 PNGs carry a drawn target
   marker; the student must not (train/serve skew). Added a `.nomarker.png`
   re-render variant (`gehi_common.draw_marker`, default `True` = byte-identical
   old path) and kept the marked variant as the diagnostic ablation arm above.

### Findings recorded for downstream

- **census-2023 label–chip mispairing risk (from slice 2):** 1,513 `version==""`
  census2023 rows were date-nearest matched at render time (921 rendered),
  risking label↔chip mispairing. The student render **excludes** them by the
  `version!=""` rule, so the pinned head never trained on them.
- **CUDA `_load_chip_tensor` device-mismatch bug (latent ISSUE-03 scaffold
  defect):** the chip tensor was normalized on CPU against device-resident
  `_mean`/`_std`, crashing any CUDA scorer. Never surfaced because every prior
  test/smoke ran on CPU. Fixed (move tensor to device before normalizing) with a
  `meta`-device regression test that reproduces the mismatch without a GPU.

### Known limitation for ISSUE-06 / ISSUE-07

The 82-anchor calibration half is small; the 14 pp calib→report optimism gap
means the pinned band is a v1 operating point, not a converged one. A larger
calibration set (or per-sub-domain bands, currently deferred) is the obvious
lever if the fidelity gate wants tighter coverage/agreement.
