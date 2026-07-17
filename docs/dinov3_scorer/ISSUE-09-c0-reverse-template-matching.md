# ISSUE-09 — Path C0: training-free census-anchored reverse template matching

> Tracer slice 9 · [TRACKER](TRACKER.md)
>
> **BLOCKED 2026-07-15 — binding mathematical amendment.** The original
> weighted two-NIG-segment decoder and anchor-included smoke are superseded by
> [`DATA-c0-reverse-template-prereg-2026-07-12.md`, Amendment 2026-07-15](DATA-c0-reverse-template-prereg-2026-07-12.md#c0-math-amendment-2026-07-15).
> No Stage-0 or held-out result is prereg-conformant until the census-conditioned
> one-sided/shared-variance decoder, anchor leave-out, calibration lock, and
> mandatory regression tests land.
>
> Final consolidated review (external ChatGPT review × amendment audit ×
> independent numerical verification, incl. un-adopted-item register and two
> P0 pre-main-eval blockers):
> [`ISSUE-09-c0-final-review-2026-07-16.md`](ISSUE-09-c0-final-review-2026-07-16.md)

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
| B (pooled sequence transformer) | FN +71.7%, overall regressed | no training; spatial footprint preserved; **replacement** one-sided/shared-variance monotone decoder required by the 2026-07-15 amendment (the retired midpoint-count persistence rule did not actually forbid flip-flop) |

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
   - Decode (superseded 2026-07-15): census-conditioned, one-sided
     shared-variance Bayesian step regression with explicit boundary/failure
     states, full posterior, and weighted constrained-step cross-check — exact
     normative contract in the mathematical amendment.
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

- [x] Original prereg committed before any eval run; `c0_s0` + `c0_r1` rules
      landed in `check_student_path_gate.py` (2026-07-12). The original harness
      has 48 passing tests, but its decode math is superseded and those tests are
      not sufficient evidence under the 2026-07-15 amendment.
- [x] Binding mathematical-audit amendment landed before Stage-0 / held-out
      evaluation (2026-07-15); current implementation explicitly marked
      non-conformant and blocked.
- [ ] Replacement measurement/decode implementation lands: per-target
      census-conditioned support, template-frame leave-out, proper one-sided
      shared-variance precision-weighted step model, explicit boundary/failure
      states, full posterior serialization, and corrected confidence outputs.
- [ ] Calibration/smoke manifests are deterministically split and frozen;
      prior, boundary, confidence, whitener, and quality-rule digests are in the
      run identity before scoring.
- [ ] All 14 mandatory amendment regression/property tests pass, including
      post-census support, self-anchor AUC, `T=3–4`, variance-only/downward
      changes, constrained-step, flip-flop persistence, duplicates, and NaNs.
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
- ~~legacy scan-states dependency~~ ✅ resolved 2026-07-13 — the basemap
  rebuild wiped `scan_states` and the legacy chip stacks entirely; the
  harness's embed/curve stages now support the rebuild's
  `basemap_rebuild_2026-07-13/chips/<target_id>/z<zoom>/` layout via
  `--stack-format basemap96` (own `geometry_version basemap96_z19_v1`,
  exact per-frame TFW footprint projection — see amendment 2026-07-13 in
  the prereg doc), and smoke's label source moved to a manual-annotation
  `--labels-csv` (also amendment 2026-07-13). Stage-0 smoke is now blocked
  on the manual label CSV actually being filled in (template + reserved-
  target-list generator: `--stage label_template`) and on enough downloaded
  frames existing per anchor (download in progress as of 2026-07-13; most
  targets have 1 vintage so far, none yet ≥3 — the smoke AUC needs ≥3
  usable frames per anchor and ≥60 transition anchors, per the unchanged
  bars). Verified on 3 real 2-vintage targets: embed + curve ran end to
  end, `fp_source=tfw_polygon` on all 3 (see prereg amendment for the
  EPSG:32735 GPKG-CRS finding this required fixing for the new path).
- **2026-07-15 mathematical amendment implementation (hard blocker):** replace
  the retired decoder/decision logic; make phase-correlation registration an
  applied transform rather than a diagnostic-only displacement; freeze a
  non-null anchor gate; enforce calibration/smoke/held-out disjointness and
  complete run hashing; land all mandatory tests. The existing
  `embed_patch_grids()` scorer scaffold remains reusable, but the current
  `chip_displacement.py` result is not yet applied as the preregistered first
  registration layer.
- After the amendment implementation lock: owner sign-off on any remaining
  numeric replacement-prior/confidence bars, frozen before scoring.
