# DATA — ISSUE-06 gate-2 dual-FAIL diagnosis (2026-07-10)

Parent: [`ISSUE-06-gate-verdict-2026-07-06.md`](ISSUE-06-gate-verdict-2026-07-06.md) ·
Harness: `scripts/validation/diagnose_fidelity_gate2.py` ·
Artifacts: `~/zasolar_data/geid_temporal/fidelity_gate_20260710/gate2_diag/{dinov3_lsat,dinov2_floor}/`

**Question:** both students fail gate-2 (A ≈ 0.64 ≪ teacher ceiling 0.7724).
Is that a measurement artifact, a backbone problem, or a decoder-amplified
chip-error problem? What would have to move to clear the bar?

---

## Headline (re-stated with the right caliber)

| caliber | L-SAT | floor | teacher ceiling |
|---|--:|--:|--:|
| Gate-2 **headline** (inv-w **all-units**) | 0.6392 | 0.6542 | **0.7724** |
| **Decoded-only** inv-w (fair vs ceiling) | **0.3706** | **0.3945** | **0.7724** |
| Clean decoded (no teacher-`unusable` frame) | 0.4152 | **0.5112** | — |
| Unusable-bearing decoded | 0.2490 | 0.1852 | — |
| Gate-3 chip decided-agreement (held-out) | 0.7971 | 0.7968 | — |

Teacher ceiling is computed on the **decoded intersection** of rep pairs
(`pairwise_ceiling` only keeps `decoded=True` units). The gate-2 headline `A`
is **all-units**, which includes splice-through rows that force
`teacher_key == student_key` by construction.

**Splice inflation (both backbones, identical unit set):**

- 1,884 unit-rep rows = **868 decoded (46%)** + **1,016 splice (54%)**
- Splice agreement = **1.000** always
- All-units ≈ `0.46 × decoded + 0.54 × 1.0` → ~0.69 unweighted, matching the run

So the headline “−12 pp vs ceiling” **understates** the real fidelity miss:

> **Fair gap (decoded student vs decoded teacher self-consistency): ~−38 pp
> (floor) / −40 pp (L-SAT).** Neither backbone is close.

Gate-1 (self-repro 1.0) is not in doubt — the students are stable; they are
stably wrong relative to Gemini-decoded intervals.

---

## Error taxonomy (decoded disagreements)

| transition | L-SAT | floor | meaning |
|---|--:|--:|---|
| `INTERVAL → INTERVAL` | **523** | **523** | same key *class*, wrong bounds |
| `INTERVAL → AP_BOUND` | 28 | 52 | student open-left (present from window start) |
| `INTERVAL → UNDATED` | 17 | 1 | student collapses to undated |
| `UNDATED → INTERVAL` | 2 | 2 | rare |
| **Total disagree** | 570 | 578 | of 868 decoded |

**~90% of disagreements are INTERVAL↔INTERVAL.** This is not a mass
class-collapse (not flooding UNDATED / AP_BOUND). The student produces a
plausible dated bracket that **does not match** the teacher's bracket.

### Where the bounds move (INTERVAL↔INTERVAL only, n=523 both)

| end-year Δ (student − teacher) | L-SAT | floor |
|---|--:|--:|
| early (Δ < 0) | 31.0% | **38.4%** |
| same end (Δ = 0) | **40.7%** | 36.9% |
| late (Δ > 0) | 28.3% | 24.7% |
| \|Δend\| ≤ 1 | 47.4% | 43.0% |
| mode | −2y (105), 0 (213) | **−2y (145)**, 0 (193) |
| same end, different start | 139/523 | 133/523 |

Interpretation:

1. **Modal miss is a 2-year-early first-present** (especially floor) — student
   calls present one vintage earlier than Gemini on the transition.
2. A large minority keep the same end-year but move the **start** (censoring
   bound / last-absent) — decoder-sensitive, not a year-MAP-only problem.
3. Near-miss (≤1y end) is only ~45% — half of INTERVAL mismatches are
   multi-year shifts, not off-by-one jitter.

---

## R3 / unusable (prep risk confirmed as primary amplifier)

Decoded inv-weighted agreement:

| | L-SAT | floor |
|---|--:|--:|
| Clean window (no teacher-`unusable`) | 0.415 | **0.511** |
| ≥1 teacher-`unusable` frame | 0.249 | 0.185 |
| Gap (clean − unusable) | 0.17 | **0.33** |

Floor is **better on clean windows** and **worse on unusable-bearing** ones —
consistent with gate-3 parity + thinner unusable recall.

**Best stratum (decoded):** `done_appears` clean ≈ 0.47–0.53. Still far below
teacher self-consistency ~0.77.

Even the **optimistic sub-caliber** (floor, clean, decoded) is **0.51**,
i.e. still **−26 pp** under the ceiling. Unusable is an amplifier, not the
sole cause.

---

## Frame-level confusion (100 disagreeing units + 40 agreeing, per backbone)

Labels: teacher/student `present`/`absent`/`unusable`/`abstain` after the
LOCKED tight12 nomarker re-render (same path as the gate).

### On units where the **interval keys disagree**

| metric | L-SAT | floor |
|---|--:|--:|
| Both-decided frame agreement | 0.775 | **0.797** |
| Teacher-decided student match | 0.651 | 0.678 |
| Teacher-`unusable` → student still decides | 75.6% | **80.1%** |
| Teacher-`unusable` correctly unusable | 24.4% | 17.4% |
| FP (teacher absent → student present) | 112 | **133** |
| FN (teacher present → student absent) | 107 | 65 |

### On units where the **interval keys agree** (baseline)

| metric | L-SAT | floor |
|---|--:|--:|
| Both-decided frame agreement | 0.914 | **0.928** |
| Teacher-`unusable` → forced decide | 80% | **95%** |

**Reading:**

1. Chip-level fidelity on disagreeing *units* is still ~0.78–0.80 when both
   sides decide — **matches gate-3 held-out ~0.80**. The student is not
   random at the frame level.
2. Agreeing *units* sit at ~0.91–0.93 frame agreement — a **~13–15 pp**
   frame gap separates interval-agree from interval-disagree units.
3. **Unusable is almost never mirrored** (15–25% correct). Student forces a
   present/absent on 75–95% of teacher-unusable frames → moves censoring
   bounds and first-present.
4. Floor is **FP-heavy** (false present); L-SAT is **balanced FP/FN**. That
   matches floor's stronger −2y early-end mode.

**Decoder amplification:** ~0.80 frame agreement (when decided) becomes
~0.37–0.39 interval agreement. The changepoint decoder turns a minority of
frame flips — especially near the absent→present edge and on unusable
frames — into different `agree_key`s. This is expected for hard-MAP
intervals; it is why gate-3 “chip OK-ish” coexists with gate-2 “interval
not OK”.

---

## Cross-backbone: same disease, not independent luck

On 868 decoded units (both students):

| | n |
|---|--:|
| Both agree with teacher | 229 |
| Both disagree with teacher | **509** |
| Floor-only correct | 61 |
| L-SAT-only correct | 69 |
| Mistake overlap | **0.797** |
| Among dual-miss: **same student key** | 260 / 509 |
| Among dual-miss: different student keys | 249 / 509 |

~80% of mistakes are **shared**. Half of dual-misses even land on the
**identical student interval key**. This is not two independent near-misses
around a ceiling — it is a **shared failure mode** (distillation / geometry /
unusable handling / decoder), not an L-SAT-vs-floor capacity gap.

That also explains R1: floor slightly wins headline (+1.5 pp) without either
being near the bar; bet survival was never the live question once gate-2
absolute level is this low.

---

## What is *not* the main story

| hypothesis | evidence |
|---|---|
| Gate harness bug / OOD banked96 scoring | Re-render active; 0/14k drops; both heads same shape |
| Non-determinism | Gate-1 exact 1.0 both |
| Mass UNDATED collapse | <3% of disagreements |
| L-SAT uniquely broken | Floor slightly better; 80% shared mistakes |
| “Just need +12 pp” | Fair decoded gap is **~38 pp**; headline is splice-masked |
| Chip-level total failure | Gate-3 ~0.80; disagree-unit both-decided ~0.78–0.80 |

---

## Causal stack (most → least load-bearing)

```
[1] Chip present/absent fidelity ceiling ~0.80 (gate-3)
        │  residual: FP-heavy transition errors (esp. floor −2y)
        ▼
[2] Unusable almost never reproduced (75–95% forced decide)
        │  moves bounds even when present/absent mostly OK
        ▼
[3] Changepoint hard-MAP interval key is brittle to [1]+[2]
        │  0.80 frame → 0.37–0.39 interval (decoded)
        ▼
[4] Splice-through (54% of rows) inflates all-units A by ~0.25–0.30
        │  headline looks −12 pp; fair is −38 pp
        ▼
[5] SAT/L capacity is irrelevant at this level (shared mistakes)
```

---

## What would have to change to pass gate-2

Teacher ceiling ≈ **0.77 decoded**. Need student decoded inv-w ≳ that.

| lever | status | expected lift |
|---|---|---|
| Fix unusable class (stop forced decide) | known weak spot ISSUE-04/05 | cleans R3 gap; clean floor already only 0.51 |
| Reduce FP on pre-transition frames (early −2y) | frame FP 133 on floor disagree sample | moves modal INTERVAL miss |
| Sequence-aware / census-exemplar student (PRD Q5 backups) | deferred | may align first-present |
| Soft / fractional agree_key (not hard interval match) | would re-define gate | **changes the bar**, not the student |
| Bigger backbone / more train | L-SAT ≯ floor | low — shared mistakes |
| Ignore splice in headline | reporting only | makes FAIL *more* visible, not a pass |

Even zeroing the unusable penalty leaves floor clean decoded at **0.51** —
still **−26 pp** short. Passing gate-2 under the current hard
interval-agreement definition requires a **material chip-level jump**
(well above 0.80 decided-agreement) *or* a **different student regime**
(sequence / exemplar / repair of transition FP), not a small calibration
tweak.

---

## Implications

1. **Gemini stays default** — confirmed; dual FAIL is not a measurement fluke.
2. **Slice 7 rollout stays blocked** — absolute fidelity, not R1, is the stop.
3. **Panel v2 / P4 Gemini-free rescore is not unlocked** — student is not a
   drop-in for interval-stable production answers.
4. **Next work (if pursuing student path):**
   - (a) Transition-FP + unusable audit with hard examples — **DONE 2026-07-10**
     on `done_appears` / floor
     ([DATA-hard-example-strips-done-appears-2026-07-10](DATA-hard-example-strips-done-appears-2026-07-10.md)):
     oracle fix of FP+unusable → **0.59** (clean **0.69**), still short of
     0.77; clearing 0.77 requires **FN as well** (fp+fn+unusable → **0.80**).
     Unusable forced-decide alone is +1 pp on this stratum. Residual is
     bidirectional PA confusion, not unusable-class only.
   - (b) Pilot sequence-aware or census-exemplar head only if the residual is
     sequential — (a) says the load-bearing error is present↔absent at
     decisive frames (FP *and* FN), not unusable alone.
   - (c) Optional: report gate-2 with **decoded-only as co-headline** so
     splice inflation cannot mask the gap in future runs.
5. **Do not** spend SAT/L capacity ablations — the bet is already dead for
   the right reason (shared failure, not L underperforming S).

---

## Artifact index

```
~/zasolar_data/geid_temporal/fidelity_gate_20260710/gate2_diag/
  dinov2_floor/{diagnosis.json,diagnosis.md,unit_rows.jsonl,frame_sample_disagree.json}
  dinov3_lsat/{...same...}
scripts/validation/diagnose_fidelity_gate2.py   # re-runnable
```

Reproduce:

```bash
source scripts/activate_env.sh
STORE=~/zasolar_data/geid_temporal/llm_endtoend_storebacked_20260704
python scripts/validation/diagnose_fidelity_gate2.py \
  --reference $STORE/reference.csv --reps $STORE/rep{1,2,3} \
  --scorer dinov2_floor \
  --head ~/zasolar_data/models/dinov2_floor/head_v1_20260705/head_v1.pt \
  --drop-anchors-overlapping ~/zasolar_data/geid_temporal/dinov3_distill_20260705/chip_subset_anchors.json \
  --out ~/zasolar_data/geid_temporal/fidelity_gate_20260710/gate2_diag/dinov2_floor
```
