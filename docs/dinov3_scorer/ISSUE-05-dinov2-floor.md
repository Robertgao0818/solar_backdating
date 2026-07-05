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

- [x] DINOv2 ViT-S/14 scorer implements the same seam, frozen backbone + light head, anchor-conditioned identically.
- [x] Trained + calibrated on the same ISSUE-02 splits with the ISSUE-04 recipe; reuses `solar_cls` tooling where applicable.
- [x] Distinct `decision_source` value; selectable via the same backbone flag.
- [x] Frozen checkpoint + thresholds pinned under `~/zasolar_data/`.
- [x] Ready to be scored under the ISSUE-06 gate on the same three numbers as DINOv3-L-SAT.

## Blocked by

- [ISSUE-04](ISSUE-04-train-head-calibrate.md) — training + calibration recipe established; reuses the same data splits.

## Execution notes (2026-07-05)

Ran entirely on the **local RTX 4070 Laptop 8GB** — no RunPod pod needed. Peak
process VRAM stayed at ~0.37 GB across both arms (ViT-S/14 is 22M params vs
DINOv3-L-SAT's 303M), so the ISSUE-04 pod workflow was overkill here; arms ran
sequentially (never concurrent on GPU) via `scripts/temporal/train_dinov3_head.py`
(same script, `--backbone-model-id vit_small_patch14_dinov2.lvd142m`), same
extract-features → train → calibrate → evaluate chain, same ISSUE-02 splits
(n_train_anchors=601, n_val_anchors=60, n_fit_rows=5989, n_val_rows=710, class
counts present=1624/absent=3459/unusable=906, calib_anchors=82, report n=858 —
**verified identical to slice 4**, only `embed_dim` differs, 384 vs 1024).
Co-teacher dual-scoring is an ISSUE-04-only AC and was deliberately skipped here.

### Design: thin sibling subclass, not a new module

Recon's plan called for a separate `dinov2_scorer.py`, but the file-scope
constraint forbade a new module, so `Dinov2PresenceScorer` is a thin subclass
of `Dinov3PresenceScorer` inside `dinov3_scorer.py`: ~6 overridden class
attributes (name, `decision_source_ok`/`failed` frozensets,
`default_backbone_model_id`, `default_input_size=518`, `weights_env_var`,
`default_weights_cache_dir`); `_center_pool` / `_load_chip_tensor` / `_observe`
/ `score` / `batch` / `embed_chips` / the head-bundle contract are inherited
verbatim, and the DINOv3 path is byte-identical (module constants + base class
attrs unchanged). Patch size (14 vs DINOv3's 16) is derived torch-free from the
timm backbone id via regex, not an independent ctor input; `_ensure_model` now
passes `dynamic_img_size=True` (required for DINOv2, which defaults `False` and
crashes at non-518 input) and cross-checks the derived patch size against
timm's `encoder.patch_embed.patch_size`. Registered additively as
`dinov2_floor`/`dinov2_failed` next to `dinov3_frozen`/`dinov3_failed`,
selectable via the existing `--scorer` flag with zero CLI-surface change
(Gemini stays default everywhere). Full design rationale, deviations, and TDD
detail live in the implementation commit notes (11 red tests first, then
green: 90/90 across `test_dinov3_scorer.py` + `test_train_dinov3_head.py` +
`test_dinov2_floor.py`).

### Arm table (per-arm: input / k / band / calib-half agreement [selection criterion] / internal val macro-F1 / report-half coverage+agreement, n=858)

| arm | input | k | band (lo-hi) | calib-half agree (selection) | val macro-F1 | report-half cov | report-half agree |
|---|---|---|---|---|---|---|---|
| nomarker_bilinear252_k3 | 252 | 3 | 0.44–0.44 | 0.9320 | 0.6447 | 0.8240 | 0.8133 |
| **nomarker_bilinear518_k6 (winner)** | 518 | 6 | 0.35–0.41 | **0.9495** | 0.6513 | 0.8776 | 0.7968 |

Winner = max calib-half decided-agreement (0.9495 > 0.9320) — the same
selection criterion as slice 4. As in slice 4, the higher-resolution/larger-k
arm wins (slice-4 winner was also the 512/k6 shape).

Extraction throughput (local RTX 4070, both arms over the full 8,496 student
chips, `skipped_no_chip=289743`, `marker_ablation=false`): 252 arm bs=32,
165.7 chips/s, peak VRAM 356 MB; 518 arm bs=8, 40.2 chips/s, peak VRAM 369 MB.

### Winner + calibration, and the honest number

**Winner = `nomarker_bilinear518_k6`**, band **lo=0.35 / hi=0.41**. Pinned
bundle: `~/zasolar_data/models/dinov2_floor/head_v1_20260705/head_v1.{pt,json}`
(+ `head_v1.train_log.json`, `head_v1.calibration_frontier.csv`,
`reports/evaluate/`); `head_pt_sha256` in the sidecar
(`38ecbf372ee536b22f9832411bd6e23edc55c27ff7612673e6a5ddb5ba498fbd`) matches
the pinned `.pt`. Config stamped: backbone `vit_small_patch14_dinov2.lvd142m`,
patch_size=14, input 518, k6, bilinear, `chip_render_variant=nomarker_bilinear518_k6`,
`marker_ablation=false`.

**Held-out report-half (the honest headline): decided-agreement 0.7968 @
coverage 0.8776, n=858.** The calib-half number for this same winner is 0.9495
— that is the in-sample winner-selection criterion (measured on the 82-anchor
half the band was fit to) and **must never be cited as a headline**, exactly
as ISSUE-04 established for DINOv3-L-SAT.

### Seam smoke (real weights, GPU)

`get_scorer('dinov2_floor', head_checkpoint=<pinned head_v1.pt>, device=cuda)`
resolves config (backbone, input 518, k6, patch 14) + band (0.35/0.41) from
the sidecar; real chips emit calibrated present/absent from P(present) with
`decision_source=dinov2_floor`, `quality_flag=usable`; a missing chip degrades
to `pv_present=None`, `quality_flag=missing_chip`,
`decision_source=dinov2_failed`. `validate_observation()` passed for every
observation — PASS.

### Deferred to ISSUE-06

This slice produces a gate-ready floor model; it does **not** render a
verdict. The L-SAT-vs-floor comparison is deferred to
[ISSUE-06](ISSUE-06-fidelity-gate.md). For reference, the comparison anchor on
the DINOv3-L-SAT side (ISSUE-04) is held-out report-half decided-agreement
**0.797 @ coverage 0.885** — cited here only as the number ISSUE-06 will line
up against the floor's 0.7968 @ 0.8776, without judging which is better.

### Cosmetic anomaly (fixed pre-commit)

The pinned `reports/evaluate/evaluate_report.md` title used to read `# DINOv3
head — ... (ISSUE-04)` even when scoring the DINOv2 bundle — a hardcoded title
string in the shared `write_evaluate_outputs` writer that both scorers reuse
(the band/config/numbers in the body were always correctly DINOv2's). Fixed
before this slice's commit: the title now derives from the bundle's
`config.backbone_model_id` (`# DINOv2 floor head
(vit_small_patch14_dinov2.lvd142m) — held-out report-half evaluation`); the
pinned report was regenerated from the same features/head bundle and verified
byte-identical apart from the title (decided-agreement 0.796813, coverage
0.877622, n=858 unchanged).
