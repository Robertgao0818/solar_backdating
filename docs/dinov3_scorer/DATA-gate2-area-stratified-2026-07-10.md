# DATA — ISSUE-06 gate-2 area-stratified diagnosis (2026-07-10)

Status: executed 2026-07-10. Verdict: **the "student can't see small PV"
hypothesis is NOT supported as the gate-2 driver** — the size gradient in raw
agreement is real but shared with the teacher; the student-specific gap is
flat across size; the small-target error direction is early/FP, the opposite
of an invisibility signature.

Parent: [`DATA-fidelity-gate2-dual-fail-2026-07-10.md`](DATA-fidelity-gate2-dual-fail-2026-07-10.md)
(dual-FAIL diagnosis; flagged no size stratification existed) ·
Harness: `scripts/validation/diagnose_gate2_area_strata.py` (read-only join of
`unit_rows.jsonl` × `reference.csv:source_area_m2`; no re-scoring) ·
Artifacts: `~/zasolar_data/geid_temporal/fidelity_gate_20260710/gate2_diag/area_strata/`

**Question:** the registration line has direct evidence that small
installations are harder (SP+LG recovery 55.6% at <15 m² vs 84.1% at ≥100 m²,
[`replan_v2/DATA-learned-matching-bounded-pilot-2026-07-09.md`](../replan_v2/DATA-learned-matching-bounded-pilot-2026-07-09.md) §4).
Does the same size effect drive the scorer's gate-2 FAIL — i.e. is the frozen
ViT student blind to small PV?

Buckets deliberately match the registration pilot (a_xs <15 / b_sm 15–40 /
c_md 40–100 / d_lg ≥100 m²). Decoded-only caliber throughout (the fair
caliber per the dual-fail memo). n = 868 unit-rep rows / 392 targets per
backbone; teacher ceiling = unweighted pairwise rep↔rep `agree_key` agreement
on the same decoded rows.

---

## Headline table

| bucket | n_tgt | teacher ceiling | LSAT agree | **LSAT gap** | floor agree | **floor gap** | unus-bearing | Δend early/same/late (LSAT) |
|---|--:|--:|--:|--:|--:|--:|--:|---|
| a_xs <15 m² | 221 | **0.666** | 0.298 | 0.367 | 0.317 | 0.348 | 49% | 32% / 43% / 25% |
| b_sm 15–40 | 141 | 0.651 | 0.363 | **0.288** | 0.316 | 0.335 | 49% | 30% / 34% / 35% |
| c_md 40–100 | 21 | 0.791 | 0.471 | 0.320 | 0.431 | 0.359 | 24% | 22% / 67% / 11% |
| d_lg ≥100 | 9 | 0.818 | 0.708 | **0.110** | 0.708 | 0.110 | 21% | (n=6) |

Wilson 95% CI on a_xs agree: LSAT [0.259, 0.341], floor [0.277, 0.360];
d_lg [0.508, 0.851] (tiny n — directional only).

## Three discriminating signals

1. **The teacher drops the same way.** Gemini's own rep↔rep self-consistency
   falls from 0.82 (≥100 m²) to **0.65–0.67 (<40 m²)**. A large share of the
   small-target difficulty is intrinsic ambiguity — the teacher disagrees
   with itself on these units across independent reps.
2. **The student-specific gap is flat, not widening.** Gap (ceiling − student)
   across the three <100 m² buckets: LSAT 0.37 / 0.29 / 0.32, floor 0.35 /
   0.34 / 0.36. An invisibility mechanism predicts a monotonic widening as
   area shrinks; instead the b_sm bucket has the *smallest* LSAT gap. The
   student underperforms the teacher roughly **uniformly** across size.
3. **Error direction is early/FP, not late/FN.** If the student missed faint
   early presences, small-target INTERVAL misses would skew late (Δend > 0).
   Observed on a_xs: early 32–37% vs late 24–25% (both backbones) — the
   student *sees things that aren't there* on transition frames, consistent
   with the FP-heavy pattern in the dual-fail memo, inconsistent with
   blindness.

## Caveats

- d_lg has 9 targets / 22 teacher pairs — the 0.11 gap ("student nearly
  matches teacher on large installs") is a hint, not a finding.
- Unusable-bearing share is confounded with size (49% in <40 m² vs ~21–24%
  above) — part of the raw small-bucket drop is the unusable amplifier. The
  clean-subset gradient survives (floor: 0.42 / 0.43 / 0.54 / 0.90), so the
  gradient itself is real; it is its *attribution to the student* that fails.
- Stratum mix is homogeneous in area (median 12–15 m² in every production
  stratum), so stratum composition does not explain the gradient.

## Implications

1. **Do not route remediation at "make the student see small PV".** The
   student's deficit vs teacher is size-uniform and FP-flavored; the
   anchor-pair / sequence-level levers from the dual-fail memo remain the
   right targets. The registration-side size effect did not transfer as a
   mechanism — the two failures stay separately diagnosed.
2. **The binding small-target constraint is the teacher's own ceiling.**
   92% of decoded targets are <40 m² (residential census reality) and there
   the teacher self-agrees only 0.65–0.67 — the global 0.7724 bar is held up
   by a small large-target tail. Even a perfect distillation cannot be more
   stable than this on the dominant strata. This is (a) a quantified
   uncertainty fact worth surfacing in the ESSD descriptor, and (b) direct
   motivation for the teacher-side chip-geometry pilot (tight chips + bbox
   aid + k-rep stability on a stratified sample): the banked96 geometry on
   which these reps were scored renders small PV at near-invisible scale for
   the *teacher* too.
3. Gate-2 reporting: this memo's per-bucket ceiling table should ride along
   any future gate re-run (same join, one command) so size composition can't
   silently move the bar.

## Reproduce

```bash
source scripts/activate_env.sh
python scripts/validation/diagnose_gate2_area_strata.py \
  --reference ~/zasolar_data/geid_temporal/llm_endtoend_storebacked_20260704/reference.csv \
  --diag-root ~/zasolar_data/geid_temporal/fidelity_gate_20260710/gate2_diag \
  --out ~/zasolar_data/geid_temporal/fidelity_gate_20260710/gate2_diag/area_strata
```
