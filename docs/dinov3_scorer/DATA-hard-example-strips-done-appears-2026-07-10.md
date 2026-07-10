# DATA — Hard-example strips + counterfactual repair on `done_appears` (2026-07-10)

Parent: [`DATA-fidelity-gate2-dual-fail-2026-07-10.md`](DATA-fidelity-gate2-dual-fail-2026-07-10.md) § next-step (a) ·
Harness: `scripts/validation/hard_example_strips_gate2.py` ·
Artifacts: `~/zasolar_data/geid_temporal/fidelity_gate_20260710/hard_examples_done_appears_floor/`

**Question:** on the production-dominant `done_appears` stratum (baseline
interval agreement **0.45**, clean **0.53**), can fixing **transition FP** and
**unusable forced-decide** lift agreement toward the teacher self-consistency
ceiling (**~0.77**)?

**Scope:** dinov2_floor head (gate-2 operative equivalent; L-SAT shares ~80% of
mistakes). Locked tight12 nomarker re-render + Phase-0 changepoint decoder.
Surgical counterfactual: overwrite student frame labels with teacher labels on
tagged error classes, re-decode, measure exact `agree_key` hit rate. This is an
**oracle upper bound** on what perfect repair of those frame classes could buy
— not a claim that a trainable fix reaches it.

---

## Headline

| repair (oracle frame overwrite → re-decode) | all `done_appears` | clean | unusable-bearing |
|---|--:|--:|--:|
| baseline (gate-2) | **0.4507** | **0.5302** | 0.2029 |
| fix **transition_fp** only | 0.5775 | 0.6884 | 0.2319 |
| fix **unusable_forced_decide** only | 0.4613 | 0.5302† | 0.2464 |
| fix **fp + unusable** | **0.5915** | 0.6884 | 0.2899 |
| fix **fp + fn + unusable** | **0.7993** | **0.8698** | 0.5797 |
| oracle (student ≡ teacher all frames) | 1.000 | 1.000 | 1.000 |

† clean subset has zero teacher-`unusable` frames by definition → unusable repair is a no-op there.

**Answer:**

1. **No** — transition FP + unusable forced-decide alone top out at **0.59**
   (clean **0.69**). Gap to 0.77 remains **~18 pp** (clean **~8 pp**).
2. **Yes, if and only if false negatives are repaired too** — fp+fn+unusable
   reaches **0.80** (clean **0.87**), clearing the ~0.77 ceiling.
3. Unusable forced-decide is a **weak lever on this stratum** (+1.1 pp all /
   +4.3 pp on the unusable-bearing tail only). The load-bearing chip error is
   **present↔absent confusion**, not quality-flag mirroring.

Teacher ceiling ≈ 0.77 is **decoded pairwise self-consistency**, not a clean
`done_appears` oracle. Hitting it with a student still requires material
chip-level present/absent fidelity, not an unusable-class patch.

---

## Frame-error inventory (284 unit-reps × all frames)

| tag | n frames | meaning |
|---|--:|---|
| `student_unusable_overcall` | **241** | teacher decided, student → unusable (reverse of forced-decide) |
| `transition_fp` | 211 | teacher absent → student present |
| `transition_fn` | 133 | teacher present → student absent |
| `unusable_forced_decide` | 114 | teacher unusable → student present/absent |
| `student_decides_teacher_abstain` | 80 | — |
| `student_abstain` | 39 | teacher decided, student abstains |

**Surprise relative to the dual-fail memo:** on `done_appears`, the student
calls **unusable when teacher decided** (241) more often than it **forces a
decide on teacher-unusable** (114). The dual-fail narrative correctly flagged
unusable asymmetry as an amplifier on the *full* decoded panel; on the clean
dominant stratum the residual is dominated by **PA flips** (FP 211 + FN 133).

### Among disagreeing units

| subset | n disagree | with ≥1 FP | with ≥1 FN | with ≥1 unusable_fd | with any PA flip | zero of those tags |
|---|--:|--:|--:|--:|--:|--:|
| clean | 101 | 64 | 42 | 0 | **94** | 7 |
| unusable-bearing | 55 | 16 | 24 | 27 | 40 | 4 |

Clean disagrees almost always carry a present/absent flip (94/101). The 7
tag-free disagrees are bound/start shifts from abstain/quality edge cases
(student_abstain / student_decides_teacher_abstain / student_unusable_overcall
not included in the three primary tags).

---

## Lift decomposition

```
0.45  baseline done_appears
 +13pp  perfect transition-FP repair          → 0.58
 +1pp   perfect unusable forced-decide        → 0.59   (fp+unusable combined)
 +21pp  also perfect transition-FN            → 0.80   (clears 0.77)
 +20pp  remaining (all other frame mismatches)→ 1.00   (oracle)
```

On **clean** only:

```
0.53  baseline clean appears
 +16pp  perfect transition-FP                 → 0.69   (still −8pp vs 0.77)
 +18pp  also perfect FN (unusable n/a)        → 0.87   (clears 0.77)
```

So the path from 0.45 → ~0.77 is **not** "kill transition FP + stop forced
decide". It is **kill bidirectional present/absent error** (FP *and* FN) at the
frames the changepoint decoder treats as decisive. Unusable is secondary here.

---

## Hard-example strips

Portable HTML (28 disagreeing units with ≥1 FP or unusable_fd, prefer rep1):

```
~/zasolar_data/geid_temporal/fidelity_gate_20260710/hard_examples_done_appears_floor/strips/hard_examples.html
```

- Yellow frame border = `transition_fp`
- Purple = `unusable_forced_decide`
- Blue = `transition_fn`
- Chips = gate-2 tight12 **nomarker** re-renders (same student input contract)

Catalog (all 284 unit-reps): `hard_examples.csv`.

---

## Implications (updates dual-fail next steps)

| dual-fail next-step | update after this audit |
|---|---|
| (a) Transition-FP + unusable hard examples | **Done** for floor / done_appears. Visual strips + counterfactual bounds landed. |
| (b) Sequence-aware / census-exemplar head | **Still justified**, but the residual that matters is **symmetric PA error**, not only early-present FP. A fix that only suppresses pre-transition FP leaves clean at 0.69. |
| Fix unusable class alone | **Insufficient** on done_appears (+1 pp). Still useful for unusable-bearing tail and other strata where R3 gap is larger (dual-fail: clean 0.51 vs unusable 0.19 inv-w decoded overall). |
| Pass gate-2 under hard interval agree | Requires chip-level PA fidelity well above current ~0.80 decided-agreement on disagree units — consistent with dual-fail causal stack [1]→[3]. |

**Do not** re-open SAT/L capacity ablations on the back of this — both students
share the PA-confusion disease.

---

## Reproduce

```bash
source scripts/activate_env.sh
STORE=~/zasolar_data/geid_temporal/llm_endtoend_storebacked_20260704
OUT=~/zasolar_data/geid_temporal/fidelity_gate_20260710/hard_examples_done_appears_floor
mkdir -p "$OUT"
python scripts/validation/hard_example_strips_gate2.py \
  --reference $STORE/reference.csv --reps $STORE/rep{1,2,3} \
  --scorer dinov2_floor \
  --head ~/zasolar_data/models/dinov2_floor/head_v1_20260705/head_v1.pt \
  --drop-anchors-overlapping ~/zasolar_data/geid_temporal/dinov3_distill_20260705/chip_subset_anchors.json \
  --unit-rows ~/zasolar_data/geid_temporal/fidelity_gate_20260710/gate2_diag/dinov2_floor/unit_rows.jsonl \
  --stratum done_appears --max-strips 36 \
  --out "$OUT"
```

Runtime ~1 min on RTX 4070 once unit_rows are banked (re-scores focus
scan_states with disk PNG cache).
