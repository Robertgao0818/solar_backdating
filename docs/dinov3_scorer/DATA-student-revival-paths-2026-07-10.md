# DATA — student revival paths under frozen-compute constraints (2026-07-10)

Status: decision memo, living statuses. Constraints in force: **single RTX
4070 (12 GB), no LoRA / no backbone training (user veto 2026-07-10), zero
new GT** — all supervision derives from existing banked Gemini labels.

Parent: [`DATA-fidelity-gate2-dual-fail-2026-07-10.md`](DATA-fidelity-gate2-dual-fail-2026-07-10.md)
(why the frozen linear-head student failed gate-2) ·
Checker: `scripts/validation/check_student_path_gate.py` (arithmetic
verdicts for the bars below — no discretion) ·
External sources: 2026-07-10 research sweep (citations inline).

## Ranked paths (cost-effectiveness order)

| # | Path | Cost | Evidence base | Status |
|---|------|------|---------------|--------|
| **A** | **Anchor-pair head**: frozen backbone; candidate frame paired with the target's earliest teacher-absent anchor frame; small diff/cross-attn head answers "did it change vs known-absent?" instead of absolute presence | Head-only ≤10M params; pair labels derived from banked teacher labels; zero new GT | Triple convergence: DeepSolar++ LR-Siamese arm (Joule 2022), Kruitwagen CNN→RNN (Nature 2021), ChangeDINO/SemDINO frozen-DINOv3 siamese (2025). Directly targets the transition-frame confusion the oracle audit isolated | **Executed same day — R1 KILL**: transition-band FP −7% / FN −14% vs matched-capacity control (dual bar ≥30% each); results inline in [`DATA-anchor-pair-pilot-prereg-2026-07-10.md`](DATA-anchor-pair-pilot-prereg-2026-07-10.md) |
| **B** | **Sequence-level distillation head**: frame-embedding sequence into a small temporal transformer (EVL/AIM regime); distill the teacher's *decoded interval* (not per-frame hard labels), confidence-weighted by Gemini rep agreement | Same — head-only | Kim & Rush sequence-level KD: per-token/frame distillation compounds at downstream decoding — our 0.80 frame → 0.37 interval is the textbook case | **Live** — unlocked as the pre-registered next bet by A's KILL; needs its own prereg before any run |
| **C** | **Frozen-encoder swap arms**: dinov2-with-registers (zero-train drop-in); further, video-native V-JEPA2 | Free / re-extract features only | Register tokens fix low-information-patch artifacts (Darcet et al. ICLR'24); VideoGLUE: image-native FMs are systematically weaker frozen on temporal tasks than video-native — directly rationalizes the 0.80 ceiling while bypassing the no-finetune constraint | Not started; **fold in as extra arms of A/B pilots**, not standalone runs |
| **D** | **Diagnostic completion**: area_m2-stratified breakdown + decoded-only co-headline | One join | Closes the missing size-stratified check | **DONE** — [`DATA-gate2-area-stratified-2026-07-10.md`](DATA-gate2-area-stratified-2026-07-10.md) (commit `e8d177f`); co-headline rides along all future gate runs |

A and B were originally mergeable (pair input and temporal head are
orthogonal components) — moot after A's KILL: **B proceeds standalone**,
without carrying the pair input forward. The matched-capacity-control
discipline that made A's KILL attributable applies verbatim to B's prereg
(temporal head vs a per-frame head of matched capacity).

## Stop discipline (machine-checked)

Two bars, both implemented in `check_student_path_gate.py`:

1. **`anchor_pair_r1`** — verbatim from the committed prereg: transition-band
   FP ↓≥30% AND FN ↓≥30% vs the matched-capacity control, no overall
   regression, unusable recall within 2pp. GO licenses a gate-2 re-run only.
   **Applied 2026-07-10 → KILL** (FP −7% / FN −14%); the rule stays as the
   executed record and as the template for B's relative bar.
2. **`frame_bar`** (this memo's addition, advisory stop-loss): further
   student investment beyond a pilot requires **decided-agreement ≥0.90 on
   both the transition band and overall**. Rationale: the dual-fail oracle
   showed clearing the 0.7724 interval ceiling needs a material chip-level
   jump well above 0.80; below ~0.90 frames, gate-2 re-runs are
   foreseeable-FAIL and the effort should stop or pivot. This bar gates
   *investment*, not the gate-2 verdict itself (that bar is unchanged).

Run: `python scripts/validation/check_student_path_gate.py --metrics <json> --rule all`

## Two honest caveats (do not plan around them silently)

1. **Boundary ambiguity is structural.** Temporal-action literature is
   explicit that errors concentrate at transitions regardless of joint
   sequence modeling; the teacher ceiling 0.7724 is itself "Gemini disagrees
   with itself 23% of the time" (0.65–0.67 on the dominant <40 m² strata —
   see sibling memo). Even a 0.9+ frame student may land ~0.7 on the hard
   interval match; at that point the question becomes whether/how to soften
   the bar — a **caliber decision, not a model decision**, and out of scope
   for any pilot.
2. **No published answer exists to copy.** The largest published comparable
   (Microsoft Global Renewables Watch, 86k sites, arXiv 2503.14860) dates
   construction with a naive earliest-detection threshold — no decoder at
   all. "PV temporal VLM distillation" is a literature gap: nobody has
   de-risked this path for us, and a working pair/sequence student is a
   novel methods contribution (post-ESSD paper upside).

## Non-blocking reminder

This entire line is a **cost / version-drift optimization**, not a
dependency of the P0 DAG: Panel v2 rebuilds on Gemini regardless (storyline
v2 §5), and the ESSD scorer-fidelity gate refers to the Gemini pipeline's
own reproducibility. Pilots proceed at their own pace; ISSUE-25
(teacher-side geometry, `replan_v2/ISSUE-25`) runs in parallel and, on
adoption, re-banks labels — path A/B architecture verdicts carry over but
heads re-distill before any gate-2 re-run.
