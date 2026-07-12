# DATA — anchor-pair student pilot: pre-registration (2026-07-10)

Status: **executed 2026-07-10 — R1 verdict KILL (scope-limited).** Design,
arms, metrics and kill bar were locked before the run; results filled below
from `~/zasolar_data/geid_temporal/pilot_anchor_pair_20260710/`. **Live
follow-on is pairing-v2** (Vexcel present template, patch-tokens):
[`DATA-anchor-pair-v2-prereg-2026-07-10.md`](DATA-anchor-pair-v2-prereg-2026-07-10.md).

Parent: [`DATA-fidelity-gate2-dual-fail-2026-07-10.md`](DATA-fidelity-gate2-dual-fail-2026-07-10.md)
(gate-2 dual FAIL; residual = bidirectional present/absent confusion at
decisive frames, amplified by the hard-MAP changepoint decoder) ·
Sibling: [`DATA-gate2-area-stratified-2026-07-10.md`](DATA-gate2-area-stratified-2026-07-10.md)
(rules out small-target invisibility as the driver; student gap is
size-uniform and FP-flavored) ·
Constraint context: LoRA/backbone fine-tuning is **vetoed** (user decision
2026-07-10: no compute for it); everything below is frozen-backbone +
head-only, local RTX 4070, zero API spend, zero new GT.

## Hypothesis

The single-frame student answers an unnecessarily hard question — "is PV
present in this frame, absolutely?" — precisely where the teacher signal
says the question is ambiguous (transition frames). Re-posing it as a
**relative** question — "did this target change from its known-absent state?"
— by pairing each candidate frame with a per-target absent **anchor frame**
should cut transition-band FP and FN without touching the backbone.

External convergence (2026-07-10 research sweep): DeepSolar++ (Joule 2022)
uses a Siamese pair (candidate + sequence reference frame) exactly on its
low-resolution arm; Kruitwagen et al. (Nature 2021) refine per-frame CNN
outputs with a sequence-level head; ChangeDINO / SemDINO (2025) get SOTA
building-change F1 from **frozen** DINOv3 + a small Siamese difference head.
No published work does this for PV install dating — if it works it is novel.

## Pre-registration (locked before any run)

1. **Population & split.** The existing slice-4/5 distillation corpus
   (`~/zasolar_data/geid_temporal/dinov3_distill_20260705/`, tight12
   nomarker renders) with the ISSUE-02 anchor-disjoint train/report split,
   unchanged. No new imagery, no new labels.
2. **Eligibility.** A target is pair-eligible iff it has ≥1 teacher-labeled
   `absent`, non-unusable frame. Anchor frame = the **earliest** such frame
   (fixed rule; no per-target selection tuning). Ineligible targets (e.g.
   `done_already_present_before_geid_history`) are excluded and their share
   reported; the pilot claims nothing about them.
3. **Input & model.** Frozen `dinov2_floor` winner embedding config
   (`nomarker_bilinear518_k6` center-pooled tokens), reusing the slice-5
   extraction pipeline verbatim. Pair head input =
   `[emb_cand, emb_anchor, emb_cand − emb_anchor]` → MLP (≤3 hidden layers,
   ≤5M params) → 3-class (present / absent / unusable). Embedding-space
   pairing only — pixel-space differencing is explicitly rejected
   (inter-vintage displacement, ISSUE-23). Seed=0; hyperparameters fixed at
   first successful training config; no post-hoc sweeps.
4. **Arms.**
   - **Arm A (bet):** pair head as above.
   - **Arm B (capacity control):** identical MLP on `emb_cand` alone, same
     budget/seed/split. Isolates "pairing" from "MLP > linear head"
     capacity; without B, an Arm-A win is uninterpretable.
   - Slice-5 linear head numbers reported alongside as context only.
5. **Transition band (primary stratum).** Per eligible target, the frames
   dated within the teacher's decoded `[last-absent, first-present]` window
   ±1 frame. Baseline transition-band decided-agreement / FP / FN for the
   slice-5 linear head and Arm B are **measured in step 0** from existing
   report-half data before Arm A trains (`TBD-run`), not asserted.
6. **Verdict rule (R1, arithmetic — no discretion).** On the held-out
   report half, Arm A vs Arm B:
   - **GO** iff transition-band FP count ↓ ≥30% **and** FN count ↓ ≥30%,
     **and** overall decided-agreement(A) ≥ decided-agreement(B), **and**
     unusable recall(A) ≥ unusable recall(B) − 2pp.
   - **KILL** otherwise. The dual-fail oracle showed one-sided fixes cannot
     clear the ceiling (FP-only → 0.58; FP+unusable → 0.59; needs FN too),
     so a one-sided win is a KILL, not a partial credit.
7. **On GO → stage B (separate run, still zero API):** plug the pair scorer
   into `scripts/validation/fidelity_gate.py`'s student walk (anchor frame
   resolved per target inside the sequence walk) and re-run gate-2 under the
   unchanged bar: ceiling 0.7724, inv-weighted headline **with decoded-only
   as co-headline** (dual-fail memo implication (c)). This pilot's GO does
   NOT pass the gate; it only licenses the re-run.
8. **Budget.** Local RTX 4070; cached embeddings + two MLP trainings
   (minutes each) + step-0 measurement; wall-clock ≤1 day; **zero API
   spend, zero new GT, zero backbone training**.

## Out of scope (this pilot)

- Teacher-side chip-geometry rebuild (tight/adaptive chips + bbox aid +
  k-rep stability sample) — **parallel track, separate pre-registration**;
  it changes the teacher, this pilot holds the teacher fixed. If that track
  later re-banks better teacher labels, Arm A's architecture verdict carries
  over but the head must be re-distilled before any gate re-run.
- Sequence-level distillation head (EVL/AIM-style temporal head over the
  frame-embedding sequence, distilling the teacher's decoded interval
  directly) — orthogonal live bet (path B); pairing-v2 is a separate prereg
  with corrected polarity (Vexcel present), not a free re-run of this design.
- Any change to gate-2's bar, decoder, or agree_key semantics.
- SAT/L backbone ablations (bet already falsified, gate-run verdict).

## Results (executed 2026-07-10)

Harness: `scripts/validation/pilot_anchor_pair_2026_07_10.py` · seed=0 ·
MLP hidden `(512, 256)` · cached embeddings
`slice5/out/nomarker_bilinear518_k6/features.npz` · artifacts
`~/zasolar_data/geid_temporal/pilot_anchor_pair_20260710/`
(`pilot_result.json`, `summary.md`, `arm_{A,B}_*/head.{pt,json}`).

### Eligibility

| | n |
|---|--:|
| anchors total (feature cache) | 764 |
| **eligible** (≥1 teacher `absent`) | **756** (98.95%) |
| ineligible | 8 (1.05%) |
| ineligible `done_appears` | 5 |
| ineligible `done_already_present_before_geid_history` | 3 |
| eligible rows / total rows | 8428 / 8496 |
| anchors with a transition band | 742 |
| transition-band rows (all splits) | 3400 |
| pair rows (anchor emb found) | 8428 (0 missing) |

Pilot claims **nothing** about the 8 ineligible targets. All metrics below
are on the **eligible report-half** (n=857 rows; transition band n=357).

### Step 0 baselines + arms (eligible report-half)

| arm | overall decided-agr | coverage | unusable recall | TB n | TB agree | **TB FP** | **TB FN** | params |
|---|--:|--:|--:|--:|--:|--:|--:|--:|
| slice-5 linear (`nomarker_bilinear518_k6`) | 0.7979 | 0.8775 | 0.1597 | 357 | 0.7066 | 21 | 13 | linear 1.2k |
| **Arm B** MLP on `emb_cand` | **0.8115** | 0.8915 | 0.1597 | 357 | 0.7201 | **14** | **14** | 329k |
| **Arm A** pair MLP | **0.8158** | 0.8740 | 0.1429 | 357 | 0.7288 | **13** | **12** | 722k |

Calibration (held-out calib half, coverage floor 0.90):

| arm | lo–hi | calib decided-agr |
|---|---|--:|
| Arm B | 0.36–0.44 | 0.9503 |
| Arm A | 0.39–0.65 | 0.9464 |

Context note (not part of R1): the capacity-control MLP (B) already cuts
transition FP 21→14 vs the slice-5 linear head; pairing (A) adds almost
nothing on top of that.

### Area-bucket ride-along (eligible report-half, overall decided-agr)

Buckets match the sibling area-strata memo (a_xs&lt;15 / b_sm 15–40 /
c_md 40–100 / d_lg ≥100 m²).

| bucket | n | A agree | B agree | linear agree |
|---|--:|--:|--:|--:|
| a_xs&lt;15 | 221 | 0.8653 | **0.8750** | 0.8684 |
| b_sm15–40 | 298 | 0.8031 | **0.8175** | 0.7807 |
| c_md40–100 | 185 | **0.7284** | 0.7044 | 0.7097 |
| d_lg≥100 | 153 | **0.8714** | 0.8345 | 0.8333 |

No size-local pairing win that would reverse the uniform student gap
picture from the sibling memo.

### Verdict per R1 (Arm A vs Arm B, arithmetic)

| condition | value | pass? |
|---|---|:---:|
| transition FP ↓ ≥30% | 14 → 13 (**−7.1%**) | **FAIL** |
| transition FN ↓ ≥30% | 14 → 12 (**−14.3%**) | **FAIL** |
| overall decided-agr(A) ≥ agr(B) | 0.8158 ≥ 0.8115 | PASS |
| unusable recall(A) ≥ recall(B) − 2pp | 0.1429 ≥ 0.1597 − 0.02 | PASS |

> **R1 = KILL.** Pairing does not clear the dual 30% FP+FN bar against the
> capacity-matched single-frame MLP. A one-sided or small two-sided win is
> not partial credit under the locked rule (dual-fail oracle needs both
> directions).

### Implications (post-run, not pre-registered)

**Scope of the KILL (corrected after three-way review
[`DATA-anchor-pair-pilot-review-2026-07-10`](DATA-anchor-pair-pilot-review-2026-07-10.md)):**
this run kills only **global-pooled-embedding pairing with a fixed
cross-vintage absent anchor, single underpowered seed**. It does **not**
kill the pairing *mechanism* as cited from ChangeDINO/SemDINO (those
difference dense multi-scale patch features, then pool). Do not write
"pairing mechanism killed" in papers or downstream memos.

1. **v1 design was a weak test of the cited mechanism.** Differencing two
   center-pooled 384-d vectors (`nomarker_bilinear518_k6`) discards the
   spatial localization that makes ChangeDINO/SemDINO work on small
   objects; earliest-absent anchors can sit years away from the candidate
   (vintage shift into `emb_cand − emb_anchor`); raw FP/FN counts at n≈14
   have ~26% power at the 30% dual bar. Face-value −7%/−14% is nowhere near
   the gate math, but "no effect" is not supported.
2. **Citation fidelity (post-hoc):** DeepSolar++ LR-Siamese pairs candidate
   with a **known-PRESENT high-res reference** to resolve LR blur — reverse
   polarity vs our absent-anchor re-pose. Kruitwagen's load-bearing temporal
   step is a **CNN→RNN over per-frame scores** (path B), not a pair head.
   Only ChangeDINO/SemDINO genuinely support pairing, and v1 removed their
   central ingredient (patch-level differencing).
3. **Capacity alone is not the gate-2 fix either.** Arm B lifts chip-level
   decided-agr only ~1.4 pp over the linear floor (0.798 → 0.812) and leaves
   transition FP+FN in the mid-teens — nowhere near the decoder-level move
   the dual-fail memo requires. "Capacity control" was architecture-matched
   not parameter-matched (A 722k vs B 329k).
4. **Path ranking (pivot 2026-07-10):** pairing-v2 is the **live frame-level
   bet**, with **Vexcel census present** as the primary reference (DeepSolar++
   polarity; FP template) — not a re-run of earliest-absent pooling.
   Full lock: [`DATA-anchor-pair-v2-prereg-2026-07-10.md`](DATA-anchor-pair-v2-prereg-2026-07-10.md).
   Path B (sequence-level interval distillation) stays **orthogonal and live**
   (Kruitwagen). Stage-B gate-2 re-run is **not licensed** by this v1 KILL.
5. Teacher-side chip-geometry rebuild remains the **parallel** track
   (separate pre-reg); any architecture verdict carries over if teacher
   labels are re-banked, but the head must be re-distilled before any gate
   re-run.
