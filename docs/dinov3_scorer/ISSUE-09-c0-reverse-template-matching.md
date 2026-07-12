# ISSUE-09 — Path C0: training-free census-anchored reverse template matching

> Tracer slice 9 · [TRACKER](TRACKER.md)

## Parent

Student-revival memo
[`DATA-student-revival-paths-2026-07-10.md`](DATA-student-revival-paths-2026-07-10.md)
("Next only under a new prereg"). This issue is that new prereg's tracker slot.
Idea origin: user proposal 2026-07-12 — anchor on the census-known-present
frame and search **backwards** for when the panel was not there.

## Why this does not reopen A/A′/A″/B

All four killed paths died at (or because of) a **trained head** on ~800
distilled anchors. C0 trains **nothing**: it is a deterministic, training-free
latent-matching score + changepoint decode. Mechanism deltas:

| Killed path | Death | C0 delta |
|---|---|---|
| A (pooled pair-diff MLP) | improvement under dual bar | no head, no pooling — footprint patch tokens |
| A′ (Vexcel census crop as anchor) | cross-sensor cos 0.31 smoke kill | anchor = latest **GEHI** present frame (same sensor); census contributes the **polygon/footprint**, not pixels |
| A″ (patch-token pair head) | trained head FP +24% | keeps A″'s *smoke signal* (cos 0.784/0.591 ranking), drops the trained head that killed it |
| B (pooled sequence transformer) | FN +71.7%, overall regressed | no training; spatial footprint preserved; monotone step decode structurally forbids frame-level flip-flop |

## What to build

1. **Prereg first** (stop-discipline): `DATA-c0-reverse-template-prereg-2026-07-12.md`
   with named machine-checkable bars in
   `scripts/validation/check_student_path_gate.py` (rule name `c0_r1`),
   committed **before** the eval run. Bars TBD pending the round-2 literature
   survey (matching metric + changepoint math), but must include: agreement vs
   teacher on held-out anchors, non-regression vs Phase-0 decoder baseline,
   and a coverage-at-fidelity triage curve.
2. **Eval harness** `scripts/validation/pilot_c0_reverse_template_2026_07_12.py`:
   - Reference = latest high-confidence teacher-labeled-present GEHI frame.
   - Template = patch tokens inside the census polygon footprint
     (`embed_patch_grids()`, `.nomarker` crops, tight12 geometry).
   - Per earlier frame: register (phase-correlation, reuse
     `chip_displacement.py` machinery), compute footprint-region similarity to
     template → one scalar per frame → similarity time series.
   - Decode: monotone step fit over the series (exact method from round-2 lit
     survey; candidates: single-changepoint least squares / isotonic fit,
     BOCPD posterior) → install interval + confidence.
   - Backbones: DINOv2-S floor **and** DINOv3 (size per round-2 survey — see
     open question below).
   - Benchmark on the ISSUE-06/Path-B held-out anchors vs teacher verdicts and
     Phase-0 decoder intervals. Zero API spend, zero training.
3. ~~Triage analysis~~ **Reframed 2026-07-12 (owner): paired independent
   channel, not triage.** Banked Gemini verdicts are dirty (pre-rebuild);
   C0 is training-free so nothing is fitted to them. Main eval = paired
   comparison against a **fresh** Gemini round on the rebuilt full-GEHI
   stacks (C0 locked blind first), judged by replicate-equivalence to fresh
   rep↔rep self-agreement + blind adjudication of disagreements. Triage
   revisited only after that verdict.

## Open questions (feeding the prereg)

- Matching math: mean-token cosine vs set-to-set (Chamfer/OT) vs whitened
  metric; per-frame normalization against radiometric/sensor drift.
- Changepoint math on short (T≈5–20), irregularly-sampled, noisy series with
  unusable-frame gaps; interval (not point) output; survival/interval-censored
  framing compatibility with the ISSUE-21 band channel.
- DINOv3 small variants: does a small distilled DINOv3 (web or SAT domain)
  exist that beats DINOv2-S on **dense** matching (gram-anchoring claim)?
  Gate-2 falsified SAT-L for *presence classification*; dense template
  matching is a different task and the bet may land differently.

## Acceptance criteria

- [x] Prereg committed before any eval run; `c0_s0` + `c0_r1` rules landed in
      `check_student_path_gate.py` (2026-07-12; harness
      `scripts/validation/pilot_c0_reverse_template_2026_07_12.py`, tests
      `tests/validation/test_c0_pilot.py`, 48 passing incl. legacy gate tests).
- [ ] Mechanism-delta table (above) carried into the prereg.
- [ ] Eval on held-out anchors: agreement vs teacher, vs Phase-0 decoder;
      3-way (C0 / decoder / teacher) interval comparison.
- [ ] Coverage-at-fidelity triage curve reported.
- [ ] Verdict DATA- doc in this folder; **no production wiring in this issue**
      (rollout remains gated behind ISSUE-07 discipline).

## Blocked by

- ~~Round-2 literature survey~~ ✅ done 2026-07-12 — math pinned in the
  prereg: [DATA-c0-reverse-template-prereg-2026-07-12](DATA-c0-reverse-template-prereg-2026-07-12.md).
  Key findings: Temporal Cluster Matching (arXiv 2103.09787) is the direct
  prior (footprint-vs-ring, training-free); key-facet matching (Amir et al.);
  offline Bayesian single-changepoint posterior for interval output; SAT-493M
  line confirmed L/7B only — no small SAT checkpoint exists.
- Owner sign-off on the prereg's numeric bars (frozen at first eval run).
- **Main eval only:** full GEHI re-download + owner's fresh Gemini round
  (incl. rep↔rep reliability sample) on the rebuilt stacks. Stage-0 smoke
  is NOT blocked on this — it runs on currently-available stacks.
- Nothing else: `embed_patch_grids()` ✅ (scorer scaffold);
  `chip_displacement.py` registration ✅.
