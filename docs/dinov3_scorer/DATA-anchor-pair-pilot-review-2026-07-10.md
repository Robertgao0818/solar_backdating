# DATA — anchor-pair pilot: three-way review, literature-fidelity gaps, fair-test spec (2026-07-10)

Status: **review memo — KILL verdict upheld as executed, but scope-limited.**
Produced by a three-agent review (implementation / statistics / literature)
of the executed pilot. Pairing-v2 fair-test **promoted to full prereg**
(with Vexcel-present polarity pivot):
[`DATA-anchor-pair-v2-prereg-2026-07-10.md`](DATA-anchor-pair-v2-prereg-2026-07-10.md).

Parent: [`DATA-anchor-pair-pilot-prereg-2026-07-10.md`](DATA-anchor-pair-pilot-prereg-2026-07-10.md)
(executed pilot, R1 KILL) ·
Path matrix: [`DATA-student-revival-paths-2026-07-10.md`](DATA-student-revival-paths-2026-07-10.md)
(A′ = pairing-v2 Vexcel-present is the live frame bet; B orthogonal) ·
Harness reviewed: `scripts/validation/pilot_anchor_pair_2026_07_10.py` +
`pilot_result.json` in `~/zasolar_data/geid_temporal/pilot_anchor_pair_20260710/`.

## 1. Verdict on the verdict

**KILL stands for the variant that was run, and it is conservative.** No
bug reverses it. Four implementation quirks were found and every one of
them biases the comparison **in Arm A's favor** — A still failed the dual
30% bar:

1. **Anchor self-pair rows** (`pair_features`, harness :243-300, asserted
   in the unit test): the anchor frame itself is a candidate row, giving
   Arm A input `[e, e, 0]` with a guaranteed `absent` label — a giveaway
   shortcut Arm B structurally cannot have.
2. **Eval-time oracle anchor** (`build_eligibility`, :166-200): the anchor
   is chosen with teacher labels on eval targets; deployment would not
   have this clean a reference.
3. **Abstention escape** (`stratum_metrics`, :639-680, counts FN only on
   explicit `absent` predictions): in the transition band Arm A pushes 18
   teacher-present rows to `unusable` vs Arm B's 4, dodging FN counts.
   TB coverage: A 0.826 vs B 0.891.
4. **Capacity mismatch**: "capacity control" is architecture-matched, not
   parameter-matched — A has 722k params vs B's 329k (2.2×).

One hygiene item (no leak, symmetric across arms): `calibrate_from_probs`
computes calib/report halves over the eligible-only anchor population (756)
while `report_half_mask` uses the full population (764). Verified
empirically: `calib ∩ report = ∅`. DO fix the population definition if the
harness is reused.

## 2. Statistical scope (what the data does and does not support)

- **Power ≈ 26%** at the pre-registered effect: with TB FP/FN base counts
  ~14 (denominators ~134/~106-123), a *true* 30% dual improvement passes
  the bar in only ~1 of 4 single runs (2M-draw simulation). False-GO under
  the null ≈ 3.1% (specificity is fine; sensitivity is not).
- **No channel separates from zero**: FP rate p=0.84, FN rate p=0.99,
  overall decided-agreement p=0.83, unusable guard p=0.72; even the most
  charitable McNemar bound is non-significant.
- **The FN "−14.3%" is mostly an abstention artifact**: rate-normalized
  over each arm's own decided denominator it collapses to −0.5%.
- Single seed, single split, no variance estimate; a reseed of a 700k-param
  MLP on ~6k rows can swing counts of this size by itself.

**Supported claim:** "pairing did not demonstrate the required effect size,
and even the face-value point estimates (−7% / −14% raw) are nowhere near
the 30% the downstream gate math requires." **Not supported:** "the pairing
mechanism has no effect." DO NOT write "pairing mechanism killed" in
papers or downstream memos — a reviewer can attack both the power and the
pooling choice (§3). Recommended one-line fix to the prereg's Implications
§1: scope the KILL to *"global-pooled-embedding pairing with a fixed
cross-vintage absent anchor, single underpowered run"*.

## 3. Literature-fidelity gaps (why this was a weak test of the cited mechanism)

Checked against the primary sources (arXiv 2511.16322 ChangeDINO;
arXiv 2606.09772 SemDINO; DeepSolar++ Joule 2022 + `DeepSolar_timelapse`
repo; Kruitwagen 2021 Nature):

1. **Pooling-before-differencing removes the cited signal.**
   ChangeDINO/SemDINO difference **dense multi-scale patch features**
   (per-level diffs / cross-temporal attention; pooling only at the end).
   The pilot differenced two **center-pooled 384-d global vectors**
   (`nomarker_bilinear518_k6`). A small PV array is a small fraction of the
   chip; pooling first averages the localized change signal away. The pilot
   tested "pairing after discarding spatial structure."
2. **Two of three citations were misattributed.** Kruitwagen's load-bearing
   temporal mechanism is an **RNN over per-frame CNN scores** — a
   sequence-level head, i.e. path B, not a pair head. DeepSolar++'s Siamese
   arm pairs the candidate with a **known-PRESENT high-resolution
   reference** to resolve LR blur — reverse polarity and a different
   problem. Only ChangeDINO/SemDINO genuinely support pairing, and the
   pilot removed their central ingredient (1).
3. **Anchor recency unexamined.** Earliest-absent anchors can sit years and
   imagery-vintages away from the candidate (cf. ISSUE-23 displacement,
   domain-gap memos); vintage shift leaks into `emb_cand − emb_anchor`.
   The pilot reports no anchor–candidate time-gap distribution and no
   stratification by it.

## 4. Fair-test spec — pairing v2

**Promoted to a full pre-registration (pivot 2026-07-10):**  
[`DATA-anchor-pair-v2-prereg-2026-07-10.md`](DATA-anchor-pair-v2-prereg-2026-07-10.md).

Material change vs this section's first draft: the **primary** reference is
the **Vexcel census present crop** ("is this the census PV?"), not
nearest/earliest absent. Absent arms remain ablations (polarity / recency).
DeepSolar++ polarity and the FP-heavy residual both point at present-template
matching; relative-to-absent was the wrong default.

Skeleton retained for audit (full lock is in the v2 prereg):

1. Patch-token grids (pool last), not global-pooled vectors.
2. Localized diff / short cross-attn head, ≤5M params.
3. Arms: **V = Vexcel present (bet)**; N = nearest absent; E = earliest
   absent; B = parameter-matched single-frame control (±10%).
4. No self-pairs; eval-time teacher/census refs stated as upper bound.
5. Rate-normalized FP/FN + `present→unusable` guard; ≥3 seeds; CI low >0
   on relative reductions.
6. Domain-gap smoke (late GEHI present vs Vexcel) **before** head train.
7. ISSUE-02 splits, calib floor 0.90, `anchor_pair_v2_r1` in
   `check_student_path_gate.py`, GO → gate-2 re-run only.

Priority: **A′ live frame-level bet**; path B orthogonal (interval
distillation), not a hard "B first" gate on A′.

## 5. Carry-over lessons for path B's prereg (do these regardless)

- Matched-capacity control = **parameter-matched** per-frame head.
- Verdict on **rates + CI / multi-seed**, never raw counts at n≈14; make
  the abstention/`unusable` escape route explicitly guarded.
- No self-referential inputs analogous to the `[e,e,0]` giveaway (for a
  temporal head: beware target-frame label leakage through the sequence
  when distilling the decoded interval).
- Pooled per-frame embeddings are acceptable for B (its mechanism is
  temporal aggregation, not spatial localization), but say so explicitly
  so the pooling critique from §3 cannot be transplanted.
