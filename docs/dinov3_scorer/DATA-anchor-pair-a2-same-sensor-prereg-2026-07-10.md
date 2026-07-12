# DATA — anchor-pair A″: same-sensor present template (pre-registration, 2026-07-10)

Status: **executed 2026-07-10 — H3 SMOKE_GO; R3 KILL.** Design was locked
before smoke/train. Pairing line under frozen head-only is now fully closed
(A / A′ / A″ / B).

Parents:
- A′ Vexcel template SMOKE_KILL (domain gap under frozen DINO):
  [`DATA-anchor-pair-v2-prereg-2026-07-10.md`](DATA-anchor-pair-v2-prereg-2026-07-10.md)
- A v1 scope-limited KILL (pooled absent anchor):
  [`DATA-anchor-pair-pilot-prereg-2026-07-10.md`](DATA-anchor-pair-pilot-prereg-2026-07-10.md)
- Review + fair-test skeleton:
  [`DATA-anchor-pair-pilot-review-2026-07-10.md`](DATA-anchor-pair-pilot-review-2026-07-10.md)
- Path matrix:
  [`DATA-student-revival-paths-2026-07-10.md`](DATA-student-revival-paths-2026-07-10.md)
- Path B sequence head KILL (orthogonal; does not license A″ by itself):
  [`DATA-sequence-head-pilot-prereg-2026-07-10.md`](DATA-sequence-head-pilot-prereg-2026-07-10.md)

Constraints (unchanged): frozen backbone, local RTX 4070 (12 GB), **zero API
spend, zero new GT, no LoRA**. Supervision = banked Gemini teacher labels
(ISSUE-02 distill corpus). GO licenses a gate-2 re-run only — does not pass
the gate.

## 0. Why A″ (pivot from A′, not a re-run)

| gap | A′ (closed) | A″ (this prereg) |
|---|---|---|
| Present template | Vexcel census crop | **Latest teacher-`present` GEHI frame** (same sensor) |
| Cross-sensor | Yes — H3 SMOKE_KILL (GEHI↔Vexcel cos ~0.31 ≪ within-Vexcel ~0.89) | **No** — GEHI cand × GEHI ref only |
| Spatial signal | patch-token grid, diff, pool last | **same** (ChangeDINO-shaped) |
| Relative question | "is this the census PV?" | **"is this the same install as the known GEHI present?"** (FP template) |
| DeepSolar++ | polarity aligned, but wrong domain for frozen DINO | polarity aligned **and** same-sensor |
| Absent arms | N/E ablations | **kept** (polarity / recency controls) |
| Control | param-matched ±10% | **same** |

**What A′ proved.** Relative present-ranking vs absent vs Vexcel is not random
(`s_pos > s_abs`), but absolute retrieval across sensors fails under frozen
dinov2_floor. That kills **cross-sensor embedding template match**, not
present-template polarity and not patch-token pairing.

**What A″ tests.** Keep the polarity that matches the FP-heavy residual and
DeepSolar++, drop the cross-sensor jump. If same-sensor present matching still
fails the dual transition bar, write a scoped KILL on **present-template
pairing under frozen DINO + banked teacher labels** — not "pairing in general."

**Explicit bans (this pilot):**
- Do **not** re-use Vexcel as a head input or smoke reference.
- Do **not** re-open A′ without a new prereg that changes the encoder or
  domain adaptation (LoRA still vetoed).
- Do **not** treat path-B interval lift as license to soften bars here.

## 1. Hypothesis (locked)

**H1 (primary):** Pairing each GEHI candidate frame with the target's
**latest teacher-`present` GEHI frame**, via a localized patch-token difference
head, cuts transition-band **FP rate** and **FN rate** vs a parameter-matched
single-frame control, without regressing overall decided-agreement or escaping
via `present→unusable`.

**H2 (mechanism / polarity):** If H1 holds, a nearest-teacher-absent arm of the
*same* head is weaker on FP (or no better) — i.e. the win is **present
template**, not "any second frame."

**H3 (same-sensor ranking smoke, pre-train gate):** On targets with ≥2
teacher-`present` non-unusable frames (or, when only one present exists, using
second-best proxy rules in §2.7), cosine similarity in the frozen embedding
space is **stochastically higher** for
`(non-ref present, present_ref)` than for `(early_absent, present_ref)`, and
**higher than** `(other-target present_ref, this present_ref)`. If this smoke
**FAILS**, stop before training heads (same-sensor template is not a usable
nearest-neighbor match either).

## 2. Pre-registration (locked before any run)

### 2.1 Population & split

- Distill corpus: `~/zasolar_data/geid_temporal/dinov3_distill_20260705/`
  (tight12 nomarker student renders) + ISSUE-02 anchor-disjoint
  train/heldout split, **unchanged**.
- Prefer reusing GEHI patch-token grids already extracted for A′ under
  `~/zasolar_data/geid_temporal/pilot_anchor_pair_v2_20260710/patch_tokens/cand_grids.npy`
  when hash/meta match the locked preprocess; otherwise re-extract under the
  A″ artifact root. **Do not** require or load Vexcel grids.
- No new imagery API calls, no new Gemini labels.

### 2.2 Eligibility

1. **Pair-eligible:** target has ≥1 teacher `absent` **and** ≥1 teacher
   `present` non-unusable frame in the feature universe. Report share of
   distill anchors that fail (A′ measured 742/764 = 97.1%).
2. **Arm-P scorable rows:** candidate capture must **not** be the chosen
   present-ref frame (self-pair ban). Targets with only one present frame
   still contribute all *other* frames as candidates with that single ref;
   the present frame itself is excluded from Arm P rows only.
3. Pilot claims nothing about non-pair-eligible targets.

Eval-time present-ref selection uses teacher labels — **upper bound** for a
pilot (stage-B deployment resolution deferred; state in the result memo).

### 2.3 Present-ref rule (locked, no per-target tuning)

For each pair-eligible target:

- `present_ref` = the teacher-`present`, non-unusable frame with the
  **latest** `(capture_date, version)` in the sorted sequence.
- If ties remain after `(capture_date, version)`, take the last index in the
  already-sorted feature table (deterministic).
- **Never** use Vexcel, census geometry, or human-chosen crops as the ref
  for this pilot.

Nearest / earliest absent rules for ablations match A′:

- **N:** nearest-in-time teacher-`absent` strictly before the candidate when
  possible; else nearest absent by absolute time gap.
- **E:** earliest teacher-`absent` (v1 rule).

### 2.4 Transition band (primary stratum)

Unchanged: per eligible target, frames in the teacher's decoded
`[last-absent, first-present]` window **±1** frame along the sorted capture
sequence. Report n_TB on the eval set.

### 2.5 Input features (patch-token grids)

- Backbone: frozen `dinov2_floor` winner config
  (`vit_small_patch14_dinov2.lvd142m`, input 518, bilinear, same preprocess
  as slice-5 / `Dinov2PresenceScorer.embed_chips`).
- **Keep the pre-pooling patch-token grid** (ViT-S/14 @518 → 37×37×384).
  Centre-crop spatial window to **15×15** for training RAM is allowed and
  must be fixed before train (same as A′ planned path); pool **only at the
  end** of the head. Global mean-pool is **not** the pair input.
- Extract / cache under
  `~/zasolar_data/geid_temporal/pilot_anchor_pair_a2_20260710/patch_tokens/`
  (or symlink identical A′ GEHI cand grids with provenance note).

### 2.6 Head (localized differencing)

- Input channels (spatial): `[P_cand, P_ref, P_cand − P_ref]`
  (concat on channel dim after optional 1×1 proj to a shared width).
- Body: small conv stack **or** 1–2 block spatial cross-attention over
  patches (ChangeDINO-shaped, not a global MLP on pooled vectors).
- Pool **only at the end** → 3-class logits
  `(present, absent, unusable)` in fixed `CLASS_ORDER`.
- Budget: **≤5M trainable params** (backbone frozen).
- Hyperparameters fixed at first successful training config; **no post-hoc
  sweeps**. Seeds for the verdict: **{0, 1, 2}** (pooled estimate).

### 2.7 Same-sensor smoke (H3) — run before any head train

On pair-eligible report-half (or full heldout if report n small), using
**global mean-pool of the same patch grid** only for this diagnostic:

For each target with a usable `present_ref`:

1. **s_pos**  
   - Prefer: `cos(pool(P_other_present), pool(P_present_ref))` where
     `other_present` is the second-latest teacher-present (or any
     non-ref present; if several, use second-latest — fixed rule).  
   - If the target has only one present frame: skip that target for
     `s_pos` (cannot form a non-self present pair). Report n_skipped.
2. **s_abs** = `cos(pool(P_early_absent), pool(P_present_ref))`  
   (earliest teacher-absent; require ≥1 absent).
3. **s_neg** = `cos(pool(P_present_ref_other), pool(P_present_ref))`  
   (random other pair-eligible target's present_ref; fixed RNG seed
   `20260710`).

**Smoke GO:** among targets contributing all three scores,  
median `s_pos > s_abs` **and** median `s_pos > s_neg`.  
**Smoke KILL:** otherwise → **stop**; do not train P/N/E/B for verdict.  
Record distributions + n_targets in `smoke_same_sensor.json`.

Reading guide (post-hoc only): if `s_pos > s_abs` holds but `s_pos ≤ s_neg`,
same-target present identity is weaker than cross-target present similarity —
template match is not localized; do not train.

### 2.8 Calibration

Same global lo/hi band rule as ISSUE-04 / A′: max decided-agreement on
held-out **calibration half** subject to coverage ≥ 0.90; deterministic
tie-break. Calibrate **each arm independently**. Report band + calib
agreement.

### 2.9 Metrics (rate-normalized; primary = transition band)

Denote teacher label `T`, student decision `S` after calibration.

On transition-band rows of the **eval set** (see §2.10):

| metric | definition |
|---|---|
| **FP rate** | `#(T=absent, S=present) / #(T=absent, S∈{present,absent})` |
| **FN rate** | `#(T=present, S=absent) / #(T=present, S∈{present,absent})` |
| **present→unusable rate** | `#(T=present, S=unusable) / #(T=present)` |
| decided-agreement | among `S∈{present,absent}`, mean `S==T` |
| unusable recall | among `T=unusable`, mean `S==unusable` |

**Overall** (all arm-eligible eval rows, not only TB): decided-agreement,
coverage, unusable recall — regression guards.

**Ride-alongs (not in R3 bar):** area buckets (a_xs/b_sm/c_md/d_lg);
anchor–candidate time-gap strata for arms N/E; Arm P
`cos(pool(P_cand), pool(P_present_ref))` stratified by `T` (FP diagnostic);
share of rows dropped by self-pair ban.

### 2.10 Eval design (power)

1. **Primary estimate:** pool metrics over seeds `{0,1,2}` (mean of the
   three seed-level rates; also report per-seed).
2. **Primary R3 rows:** report-half transition-band rows under the
   arm-specific eligibility mask (Arm P excludes self-ref candidates).
3. If any seed has TB FP or FN **decided denominator** &lt; 40, also report
   a secondary table on **all heldout TB rows** (calib∪report) marked
   `heldout_TB_secondary` — still never train on report.
4. **Bootstrap or Wilson CI** on pooled rates (B=2000, seed=0) for the
   P−B relative reductions; report CI low for the reduction percentages.

### 2.11 Arms

| arm | ref | role |
|---|---|---|
| **P (bet)** | latest teacher-`present` GEHI | DeepSolar++ polarity; same-sensor FP template |
| **N (ablation)** | nearest teacher-`absent` GEHI | polarity / recency control |
| **E (ablation)** | earliest teacher-`absent` GEHI | comparability to v1 / A′ ablations |
| **B (control)** | none — `P_cand` alone through the **same** head body, widened so total params match arm P within **±10%** | isolates pairing from capacity |

- Exclude **self-pair** rows for P/N/E (candidate capture ≠ ref capture).
- Slice-5 linear head numbers ride along as context only (not in R3).

### 2.12 Verdict rule R3 (arithmetic — no discretion)

Compare **Arm P vs Arm B** on the primary eval set, **pooled over seeds
{0,1,2}**:

**GO** iff all of:

1. TB **FP rate** reduction `(rate_B − rate_P) / rate_B` ≥ **30%**, and
   the 95% CI low on that relative reduction is **> 0**;
2. TB **FN rate** reduction ≥ **30%**, and CI low **> 0**;
3. overall decided-agreement(P) ≥ overall decided-agreement(B);
4. TB `present→unusable` rate(P) ≤ rate(B) + **2 pp** (abstention guard);
5. unusable recall(P) ≥ unusable recall(B) − **2 pp**;
6. param count \|P − B\| / B ≤ **10%**;
7. n_seeds ≥ 3.

**KILL** otherwise. One-sided wins are KILL (dual-fail oracle).

**H2 (interpretive, not a second GO):** if R3 = GO, report Arm N and Arm E
on the same metrics; expected pattern is FP-rate(P) &lt; FP-rate(N) ≤
FP-rate(E). Failure of that pattern does not revoke GO but must be written
into the result memo.

Machine check: `scripts/validation/check_student_path_gate.py --rule
anchor_pair_a2_r1` (fail-closed on missing keys).

### 2.13 On GO → stage B

Plug Arm P into `scripts/validation/fidelity_gate.py` student walk
(present-ref resolved per target from teacher labels for the pilot upper
bound; deployment resolver is a separate issue). Re-run gate-2 under the
**unchanged** bar (ceiling 0.7724; inv-weighted headline **with
decoded-only co-headline**). Pilot GO does **not** pass the gate.

### 2.14 Budget

Local RTX 4070; GEHI patch grids (reuse A′ or re-extract once) + smoke
(minutes) + 4 arms × 3 seeds head train + eval. Wall-clock ≤ 2 days.
Zero API, zero new GT, zero backbone training.

On KILL: **no hyperparameter rescue sweep.** Optional follow-ons require a
**new** prereg (e.g. domain-robust projector under still-frozen backbone, or
teacher geometry rebuild + re-distill).

## 3. Out of scope (this pilot)

- Path B sequence-level interval distillation (already closed under B-R1).
- Vexcel / cross-sensor templates (A′ closed; do not smuggle back).
- Teacher-side geometry rebuild (ISSUE-25 parallel track).
- Pixel-space GEHI−GEHI or GEHI−Vexcel differencing.
- LoRA / backbone finetune.
- Any change to gate-2 bar, decoder, or `agree_key` semantics.
- Deploy-time present-ref selection without teacher labels (stage-B only).

## 4. Artifact layout (planned)

```
~/zasolar_data/geid_temporal/pilot_anchor_pair_a2_20260710/
  patch_tokens/              # GEHI cand grids (or symlink + provenance)
  smoke_same_sensor.json
  eligibility.json
  arm_P_latest_present/{seed}/
  arm_N_nearest_absent/{seed}/
  arm_E_earliest_absent/{seed}/
  arm_B_cand_matched/{seed}/
  pilot_result.json
  pilot_metrics.json         # flat keys for check_student_path_gate
  summary.md
```

Harness (implement after this prereg locks):  
`scripts/validation/pilot_anchor_pair_a2_2026_07_10.py`

## 5. Results (fill after execution)

Harness: `scripts/validation/pilot_anchor_pair_a2_2026_07_10.py` · artifacts
`~/zasolar_data/geid_temporal/pilot_anchor_pair_a2_20260710/` · GEHI
`cand_grids` symlinked from A′ (same 8496-row distill order).

### Eligibility

| | n |
|---|--:|
| distill anchors | 764 |
| pair-eligible | **742** (97.1%) |
| arm-P scorable rows (self-pair excluded) | **7616** |
| arm-P self-pair excluded rows | 742 |
| smoke n_targets (with s_pos; report-half) | **63** (17 skipped: only one present) |

### Same-sensor smoke H3

| comparison | median cos | mean cos |
|---|--:|--:|
| s_pos (non-ref present ↔ present_ref) | **0.784** | 0.758 |
| s_abs (early absent ↔ present_ref) | **0.591** | 0.572 |
| s_neg (other present_ref ↔ present_ref) | **0.581** | 0.578 |

| check | result |
|---|:---:|
| s_pos > s_abs (0.784 > 0.591) | **PASS** |
| s_pos > s_neg (0.784 > 0.581) | **PASS** |

> Smoke verdict: **SMOKE_GO.** Same-sensor present identity ranks above both
> early-absent and cross-target present refs under frozen dinov2_floor.
> (Contrast A′: GEHI↔Vexcel s_pos 0.31 ≪ within-Vexcel 0.89.)

### Arms / R3 (pooled seeds {0,1,2}, report-half TB)

| | Arm P (latest present) | Arm B (matched single-frame) |
|---|--:|--:|
| params | 443,139 (width 128) | 429,411 (width 144) |
| ratio P/B | **1.032** | — |
| TB FP rate | 0.1288 | **0.1036** |
| TB FN rate | 0.1137 | 0.1155 |
| overall decided-agr | 0.8025 | **0.8167** |
| TB present→unusable | **0.1101** | 0.1284 |
| overall unusable recall | **0.3058** | 0.2066 |

Relative reductions (P vs B): FP **−24.3%** (CI low −166%); FN **+1.6%**
(CI low −126%). Machine check:

| R3 condition | value | pass? |
|---|---|:---:|
| TB FP-rate reduction ≥30%; CI low >0 | −24.3%; −166% | **FAIL** |
| TB FN-rate reduction ≥30%; CI low >0 | +1.6%; −126% | **FAIL** |
| overall decided-agr(P) ≥ agr(B) | 0.8025 ≥ 0.8167 | **FAIL** |
| present→unusable P ≤ B + 2pp | 0.110 ≤ 0.148 | PASS |
| unusable recall P ≥ B − 2pp | 0.306 ≥ 0.187 | PASS |
| param ratio ±10%; n_seeds≥3 | 1.032; 3 | PASS |

> **R3 = KILL.** Same-sensor present template does not beat a
> parameter-matched single-frame patch head on the dual transition bar; it
> **raises** TB FP rate and slightly regresses overall decided-agreement.
> No gate-2 re-run licensed. No rescue sweep.

#### H2 polarity ride-along (TB FP rates; interpretive only)

| arm | TB FP rate | TB FN rate |
|---|--:|--:|
| P latest present | 0.1288 | 0.1137 |
| N nearest absent | **0.1153** | 0.1285 |
| E earliest absent | 0.1266 | 0.1241 |
| B single-frame | **0.1036** | 0.1155 |

Expected pattern FP(P) &lt; FP(N) ≤ FP(E) **does not hold** — B is best on FP,
then N, then E≈P. Present-template polarity is not load-bearing under this
frozen patch head.

Per-seed report-half TB (context; not a second bar):

| seed | P fp/fn | B fp/fn | overall agr P/B |
|---|---|---|---|
| 0 | 0.087 / 0.140 | 0.145 / 0.121 | 0.824 / 0.801 |
| 1 | 0.144 / 0.101 | 0.115 / 0.120 | 0.804 / 0.806 |
| 2 | 0.156 / 0.100 | 0.050 / 0.105 | 0.780 / 0.843 |

High seed variance (esp. B seed-2 FP 0.05) is already absorbed by the
multi-seed pool + CI; it does not reverse the KILL.

### Implications (post-run)

1. **Smoke GO + R3 KILL** = the frozen embedding space *can* rank same-sensor
   present frames above absent / other-target, but a ChangeDINO-shaped
   pair head on that template does **not** improve transition FP/FN vs a
   capacity-matched single-frame control. Do not write "pairing mechanism
   killed globally" — write "**same-sensor present-template pairing under
   frozen dinov2_floor + banked Gemini labels failed R3**."
2. **Pairing line closed under current constraints.** A (pooled absent), A′
   (Vexcel cross-sensor smoke), A″ (same-sensor present) are all closed.
   Path B (sequence interval head) already B-R1 KILL. No licensed student
   gate-2 re-run from frozen head-only pilots.
3. **H2 failed:** present ref is not better than nearest-absent for FP; both
   lose to single-frame B on pooled FP.
4. **What remains (requires new prereg, not a free re-run):**
   - Teacher-side geometry rebuild (ISSUE-25) + re-distill, then re-test
     architecture verdicts;
   - Softening the hard interval caliber (policy, not model);
   - Encoder / domain adaptation still under LoRA veto unless user lifts it;
   - Stay on Gemini for production scorer (slice 7 remains blocked).
