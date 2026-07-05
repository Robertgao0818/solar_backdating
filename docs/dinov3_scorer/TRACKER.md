# DINOv3-SAT Scorer Swap — Execution Tracker

Source PRD: [`../dinov3_sat_scorer_backbone_prd.md`](../dinov3_sat_scorer_backbone_prd.md)
(READY — grilled Q1–Q10, 2026-06-30; **amended 2026-07-03** by the v2
program's §D12 — corrected inputs landed in the PRD and issues 02/03/04/06/07
via [`replan_v2/ISSUE-12`](../replan_v2/ISSUE-12-student-prd-amendments.md))

**Goal:** swap the install-date presence scorer backbone from hosted Gemini to a
self-hosted **frozen DINOv3-L-SAT** encoder + light head, **distilled from
Gemini's own per-vintage labels**. Primary deliverable = reproducibility (kill
version drift). Success is measured as **fidelity to Gemini, not accuracy** — no
independent install-date ground truth exists.

This effort is tracked as markdown issues in this folder. The repo plans entirely
in `docs/` markdown and the GitHub issue tracker is unused (PRD → Further Notes).

## Status legend

- ⬜ not started · 🟡 in progress · ✅ done · ⛔ blocked (a dependency is still open) · ➡ superseded (tracked elsewhere)

## Slices

| # | Slice | Status | Blocked by | Issue |
|---|-------|--------|-----------|-------|
| 1 | PresenceScorer seam — **superseded 2026-07-03** by [replan_v2 ISSUE-05](../replan_v2/ISSUE-05-presence-scorer-seam.md) (wider scope: 4 call-sites) | ➡ | — | [ISSUE-01](ISSUE-01-presence-scorer-seam.md) |
| 2 | Distillation training set (harvest + chip re-download + split) | ✅ | — | [ISSUE-02](ISSUE-02-distillation-training-set.md) |
| 3 | DINOv3-L-SAT frozen scorer scaffold + selection flag | ✅ | [replan_v2 5](../replan_v2/ISSUE-05-presence-scorer-seam.md) ✅ | [ISSUE-03](ISSUE-03-dinov3-scorer-scaffold.md) |
| 4 | Train light head + calibrate abstain band (RunPod; + co-teacher dual-scoring) | ✅ | 2, 3 | [ISSUE-04](ISSUE-04-train-head-calibrate.md) |
| 5 | DINOv2 ViT-S/14 falsification floor | ⬜ | 4 | [ISSUE-05](ISSUE-05-dinov2-floor.md) |
| 6 | Fidelity gate (three numbers, both backbones; baseline = Phase-0 decoder under D8) | ⬜ | 4, 5, [replan_v2 2](../replan_v2/ISSUE-02-changepoint-posterior-decoder.md) | [ISSUE-06](ISSUE-06-fidelity-gate.md) |
| 7 | Feature-flag rollout + ops profile | ⬜ | 6 | [ISSUE-07](ISSUE-07-rollout-ops-profile.md) |
| 8 | Bonus: deterministic run-to-run experiment (not gated) | ⬜ | 4 | [ISSUE-08](ISSUE-08-determinism-experiment.md) |

## Dependency graph

```mermaid
graph LR
    R5[replan_v2 5 · seam, 4 call-sites ✅]
    R2[replan_v2 2 · Phase-0 decoder]
    I2[2 · Training set]
    I3[3 · DINOv3 scaffold]
    I4[4 · Train + calibrate + co-teacher]
    I5[5 · DINOv2-S floor]
    I6[6 · Fidelity gate]
    I7[7 · Rollout + ops + licence]
    I8[8 · Determinism bonus]

    R5 --> I3
    I3 --> I4
    I2 --> I4
    I4 --> I5
    I4 --> I8
    I4 --> I6
    I5 --> I6
    R2 --> I6
    I6 --> I7
```

ASCII fallback:

```
        ┌──── replan_v2 5 ✅ ──► 3 ──┐
        │                            ▼
roots ──┤                            4 ──┬──► 5 ──┐
        │                            │   │        ▼
        └───────────────── 2 ────────┘   ├──► 6 ──► 7
                                         └──► 8 (bonus, not gated)
   (6 also needs 5 and replan_v2 2 — the Phase-0 decoder gate baseline)
```

Slice 1 was superseded by replan_v2 ISSUE-05 (done 2026-07-03), so slices 2
**and 3** are both unblocked roots now. Slice 6 gained an external dependency:
its no-answer-change baseline runs both pipelines through the Phase-0 decoder
(D12.vi).

## Execution waves

- **Wave A (parallel roots):** 2, 3 — the seam dependency is already ✅
  (replan_v2 ISSUE-05); run together.
  *Deviation note (2026-07-03):* the v2 PRD's phase-level ordering says
  "Phase 3 starts after 0 + 1 land (gate baseline + seam)". That is refined
  here to per-slice dependencies: the seam (Phase 1 side) is done, so slices
  2–3 may start; the Phase-0 decoder gates only slice 6 (its no-answer-change
  baseline, D12.vi) and must land before the fidelity gate runs.
- **Wave B:** 4 — after 2 **and** 3 (head training on RunPod).
- **Wave C (parallel):** 5, 8 — after 4.
- **Wave D:** 6 — after 4, 5, **and** replan_v2 2 (Phase-0 decoder).
- **Wave E:** 7 — after 6 (includes the pre-ship licence review).

## How to use this tracker

1. Pick a slice whose **Blocked by** entries are all ✅. Flip its status to 🟡.
2. Each issue is a **tracer bullet** — it must be verifiable on its own
   (its acceptance criteria pass: tests green / artifact exists / gate number
   reported) before it goes ✅. Check off criteria in the issue file as you land them.
3. On completion, flip the slice to ✅ here, append a dated line to the log, and
   re-scan for newly unblocked slices.
4. The **PRD stays authoritative** for decisions. If you deviate, record it as a
   note row below rather than silently editing scope.

## Decision invariants (carried from the PRD — do not drift)

- Frozen backbone, light head only. No full fine-tune in v1 (LoRA/PEFT only on gate failure).
- Distillation **matches** Gemini; accuracy is **out of the gate** (no truth exists).
- `scan_state.json` schema unchanged; `decision_source` values are additive.
- Gemini stays behind the same seam as fallback / A-B comparator; flag-gated, Gemini default until the gate passes.
- v1 = per-chip independent scoring, single global abstain band. Sequence-aware (c), census-GT reference exemplar (b), and per-sub-domain calibration are deferred.
- Region via explicit `region` + registry (ADR-0002). Weights + chips under `~/zasolar_data/`, not committed.
- **D12 amendments (2026-07-03) are binding:** label pool = 23,147 anchors /
  250,502 rounds (26,820 retired); `chip_targets.csv` is a hard re-render
  dependency; 27.6% `done_ambiguous_*` handled explicitly in stratification;
  training compute = RunPod; DINOv3 licence review pre-ship; gate baseline =
  Phase-0 decoder under D8 rules (hand-authored 0.74 retired); co-teacher
  dual-scoring = calibration instrument only. `pv_score` names only the seam
  field — on disk the field is `confidence`.

## Progress log

- 2026-06-30 — Tracker + 8 issue files created from the grilled PRD.
- 2026-07-03 — Slice 1 superseded by replan_v2 ISSUE-05 (seam landed across
  all four call-sites). D12 amendments landed in the PRD and issues
  02/03/04/06/07 (replan_v2 ISSUE-12): corrected label-pool counts,
  chip-manifest hard dependency, ambiguous-fraction stratification, RunPod
  compute, pre-ship licence review, gate re-based on the Phase-0 decoder
  under D8, co-teacher dual-scoring. Slice 3 unblocked; slice 6 gained
  replan_v2 2 (decoder) as a baseline dependency.
- 2026-07-05 — Slice 2 done: `build_distillation_set.py` (harvest +
  render-chips, 27 tests). 23,147 anchors / 298,239 post-dedup rounds across
  all four corpora (D12's 250,502 was an inconsistent subset — reconciled in
  the artifact README); 27.6% `done_ambiguous_*` retained as unusable;
  anchor-disjoint split 18,499/4,648; 800-anchor stratified subset re-rendered
  at `chip_geom_v2_tight12` (8,165 rounds, 763 unrecoverable drops recorded),
  idempotency + containment-band QA verified on real artifacts. Artifacts:
  `~/zasolar_data/geid_temporal/dinov3_distill_20260705/`. **Slice 4 (train
  head) is now unblocked** (2 ✅ + 3 ✅ — Wave B may start).
- 2026-07-05 — Slice 4 done: light head trained (frozen backbone) + calibrated
  on RunPod (RTX 5090, D12.iv). Winner **`nomarker_bilinear512_k6`** (input 512,
  center-pool-k 6, bilinear), band **lo=0.46/hi=0.50**, pinned at
  `~/zasolar_data/models/dinov3_sat/head_v1_20260705/` (sha256-verified). Honest
  **held-out report-half agreement = 0.797 @ 0.885 coverage** (calib-half 0.939
  is in-sample — ~14 pp optimism gap on an 82-anchor calib set; downstream cites
  0.797). Upscaling ablation: bilinear≡bicubic@256, 512/k6 wins by +0.42 pp, the
  marked arm (+0.6–1 pp) is a D4-forbidden diagnostic only. Co-teacher: 35
  strata, overall disagreement 0.237, per-stratum table archived for Phase-4.
  Weak spot: thin unusable-class recall (20/120 report rows). Two design
  decisions (unusable-class supervision via 1,252 quality rows composing D12.iii;
  marker-free `.nomarker.png` render, PRD D4) + a fixed latent CUDA scaffold bug
  recorded in ISSUE-04 execution notes. `score()` now emits the calibrated band
  (local GPU seam smoke green; 76 slice-4 tests pass). **Slices 5 (DINOv2 floor)
  and 8 (determinism bonus) unblocked — Wave C may start.**
- 2026-07-04 — Slice 3 done: scaffold landed
  (`scripts/temporal/dinov3_scorer.py` — frozen timm
  `vit_large_patch16_dinov3.sat493m`, center-k×k token pooling, fixed-seed
  placeholder Linear head, weights cached under
  `~/zasolar_data/models/dinov3_sat/`), registered as `--scorer dinov3_frozen`
  behind the seam (Gemini default unchanged), 11 tests incl. real-weight
  backbone parity, determinism, downstream byte-identical scan_state
  invariance. All acceptance criteria checked off in ISSUE-03. Slice 4
  (train head) now waits only on slice 2 (training set).
