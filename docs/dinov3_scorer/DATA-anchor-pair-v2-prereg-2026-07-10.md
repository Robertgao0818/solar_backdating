# DATA — anchor-pair v2: pre-registration (2026-07-10)

Status: **executed 2026-07-10 — H3 domain-gap smoke KILL; no head training.**
Design locked before the run. Fail-closed stop per §2.7: do **not** interpret
as "pairing failed" — the frozen embedding space does not place GEHI present
near Vexcel present relative to within-Vexcel distances.

Parents:
- v1 executed KILL (scope-limited):
  [`DATA-anchor-pair-pilot-prereg-2026-07-10.md`](DATA-anchor-pair-pilot-prereg-2026-07-10.md)
- Three-way review + literature gaps:
  [`DATA-anchor-pair-pilot-review-2026-07-10.md`](DATA-anchor-pair-pilot-review-2026-07-10.md)
- Path matrix (living statuses):
  [`DATA-student-revival-paths-2026-07-10.md`](DATA-student-revival-paths-2026-07-10.md)

Constraints (unchanged): frozen backbone, local RTX 4070 (12 GB), **zero API
spend, zero new GT, no LoRA**. Supervision = banked Gemini teacher labels
(ISSUE-02 distill corpus). GO licenses a gate-2 re-run only — does not pass
the gate.

## 0. Why v2 (pivot, not re-run of v1)

v1 tested **global-pooled** embeddings with a **fixed earliest teacher-absent**
anchor and raw FP/FN counts. That was a weak test of the cited mechanism and
the wrong polarity for our residual:

| gap | v1 | v2 |
|---|---|---|
| Spatial signal | center-pooled 384-d, then differenced | **patch-token grid**, difference, **pool last** (ChangeDINO-shaped) |
| Anchor polarity | known-**absent** (earliest) | **primary = known-PRESENT Vexcel census**; absent arms are ablations only |
| Relative question | "changed from empty roof?" | **"is this the census PV?"** (template match; FP gate) |
| DeepSolar++ | mis-cited (their Siamese uses HR **present** ref) | **aligned** with DeepSolar++ LR-Siamese polarity |
| Control | architecture-matched (A 722k / B 329k) | **parameter-matched ±10%** |
| Metrics | raw counts (n≈14, power ~26%) | **rates + multi-seed pooled estimate** |
| Self-pair / abstention | `[e,e,0]` giveaway; FN escapable via unusable | self-pairs excluded; `present→unusable` guarded |

**Diagnosis that drives polarity.** Gate-2 residual is FP-flavored on
transition frames (student *sees* PV that teacher does not), not
invisibility of small targets
([dual-fail](DATA-fidelity-gate2-dual-fail-2026-07-10.md),
[area-strata](DATA-gate2-area-stratified-2026-07-10.md)). Relative-to-absent
*amplifies* non-PV appearance change as "present." Relative-to-Vexcel-present
asks whether the candidate **matches the known install** — the natural FP
filter, and the clearest image in the target's life cycle.

**Historical Phase-1 note** (`geid_temporal_anchor_presence_architecture.md`):
the ban on Siamese was for **pixel-space** GEHI−Vexcel differencing under
domain gap. v2 stays in **frozen-DINO embedding space** with banked
registration context (ISSUE-23 / ISSUE-24); pixel differencing remains
rejected.

## 1. Hypothesis (locked)

**H1 (primary):** Pairing each GEHI candidate frame with the target's
**Vexcel census present crop**, via a localized patch-token difference head,
cuts transition-band **FP rate** and **FN rate** vs a parameter-matched
single-frame control, without regressing overall decided-agreement or
escaping via `present→unusable`.

**H2 (mechanism / polarity):** If H1 holds, a nearest-teacher-absent arm of
the *same* head is weaker on FP (or no better) — i.e. the win is **present
template**, not "any second frame."

**H3 (domain-gap smoke, pre-train gate):** On targets with both a late
teacher-`present` GEHI frame and a Vexcel crop, cosine similarity in the
same frozen embedding space is **stochastically higher** for
`(late_present, Vexcel)` than for `(random other-target Vexcel, this Vexcel)`
and than for `(early_absent, Vexcel)`. If this smoke **FAILS**, stop before
training heads (domain gap kills the template; do not interpret a later KILL
as "pairing failed").

## 2. Pre-registration (locked before any run)

### 2.1 Population & split

- Distill corpus: `~/zasolar_data/geid_temporal/dinov3_distill_20260705/`
  (tight12 nomarker student renders) + ISSUE-02 anchor-disjoint
  train/heldout split, **unchanged**.
- Cached single-frame floor features remain
  `slice5/out/nomarker_bilinear518_k6` for context only; v2 **re-extracts
  patch-token grids** (see §2.4).
- No new imagery API calls beyond reading banked Vexcel crops / tiles
  already on disk. No new Gemini labels.

### 2.2 Eligibility (two nested sets)

1. **Pair-eligible (shared):** target has ≥1 teacher `absent` **and** ≥1
   teacher `present` non-unusable frame in the feature universe (needs a
   transition story). Report share of distill anchors that fail.
2. **Vexcel-eligible (Arm V universe):** pair-eligible **and** a banked
   Vexcel census crop exists for that target (or parent chip-group with
   documented join). Banked path (2026-07-10):
   `~/zasolar_data/geid_temporal/gehi_displacement_audit_2026-07-06/vexcel_crops/`
   (~792 chips; ~514 full-id overlap with the 764-anchor feature cache —
   mostly `c*` chip-groups). For `t*` per-target anchors: join via
   `chip_targets.csv` / group membership or re-crop from banked Vexcel
   tiles using inventory geometry (**offline, no API**). Report
   Vexcel-eligible share; pilot claims nothing about targets without a
   Vexcel crop on Arm V.

Eval-time reference selection still uses teacher labels for absent arms
and census geometry for Vexcel — **upper bound** for a pilot (stage-B
deployment resolution deferred; state this in the result memo).

### 2.3 Transition band (primary stratum)

Unchanged definition from v1: per eligible target, frames in the teacher's
decoded `[last-absent, first-present]` window **±1** frame along the sorted
capture sequence. Report n_TB on the eval set.

### 2.4 Input features (patch-token grids)

- Backbone: frozen `dinov2_floor` winner config
  (`vit_small_patch14_dinov2.lvd142m`, input 518, bilinear, same preprocess
  as slice-5 / `Dinov2PresenceScorer.embed_chips`).
- **Keep the pre-pooling patch-token grid** (ViT-S/14 @518 → 37×37×384).
  Center-pooled global vector is **not** the pair input.
- Extract once; cache under
  `~/zasolar_data/geid_temporal/pilot_anchor_pair_v2_20260710/patch_tokens/`
  (npz or memmap; disk over VRAM; batch forward on 4070).
- Vexcel crops: same scorer preprocess (resize/bilinear to 518, no marker).
  Record crop path + any apply of banked GEHI↔Vexcel bias/offset in
  provenance; **no pixel-space GEHI−Vexcel residual as a feature**.

### 2.5 Head (localized differencing)

- Input channels (spatial): `[P_cand, P_ref, P_cand − P_ref]`
  (concat on channel dim after optional 1×1 proj to a shared width).
- Body: small conv stack **or** 1–2 block spatial cross-attention over
  patches (ChangeDINO-shaped, not a global MLP on pooled vectors).
- Pool **only at the end** → 3-class logits
  `(present, absent, unusable)` in fixed `CLASS_ORDER`.
- Budget: **≤5M trainable params** (backbone frozen).
- Hyperparameters fixed at first successful training config; **no post-hoc
  sweeps**. Seeds for the verdict: **{0, 1, 2}** (pooled estimate).

### 2.6 Arms

| arm | ref | role |
|---|---|---|
| **V (bet)** | Vexcel census present crop | DeepSolar++ polarity; FP template |
| **N (ablation)** | nearest-in-time teacher-`absent` GEHI frame (strictly before candidate when possible; else nearest absent) | polarity / recency control |
| **E (ablation)** | earliest teacher-`absent` GEHI frame (v1 rule) | comparability to v1 KILL |
| **B (control)** | none — `P_cand` alone through the **same** head body, widened so total params match arm V within **±10%** | isolates pairing from capacity |

- Exclude **self-pair** rows: candidate capture must not be the chosen GEHI
  ref frame (N/E); Vexcel is never a self-pair.
- Train/eval masks use the same exclusion.
- Slice-5 linear head numbers ride along as context only (not in R2).

### 2.7 Domain-gap smoke (H3) — run before any head train

On Vexcel-eligible report-half (or full heldout if report n small), using
**global mean-pool of the same patch grid** only for this diagnostic:

1. `s_pos = cos(pool(P_late_present), pool(P_vexcel))` per target  
2. `s_abs = cos(pool(P_early_absent), pool(P_vexcel))`  
3. `s_neg = cos(pool(P_vexcel_other), pool(P_vexcel))` (random other target)

**Smoke GO:** median `s_pos > s_abs` and median `s_pos > s_neg`  
**Smoke KILL:** otherwise → **stop**; do not train V/N/E/B for verdict.
Record distributions in `smoke_domain_gap.json`.

### 2.8 Calibration

Same global lo/hi band rule as ISSUE-04 / v1: max decided-agreement on
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

**Overall** (all eligible eval rows, not only TB): decided-agreement,
coverage, unusable recall — regression guards.

**Ride-alongs (not in R2 bar):** area buckets (a_xs/b_sm/c_md/d_lg);
anchor–candidate time-gap strata for arms N/E; Arm V
`cos(pool(P_cand), pool(P_vexcel))` stratified by `T` (FP diagnostic).

### 2.10 Eval design (power)

Report-half TB alone is underpowered (v1 ~357 rows, FP/FN counts ~14).
Locked design:

1. **Primary estimate:** pool metrics over seeds `{0,1,2}` (mean of the
   three seed-level rates; also report per-seed).
2. **Eval rows for the rate denominators:** **cross-fit** on all
   pair-eligible (resp. Vexcel-eligible) transition-band rows:
   - fold = ISSUE-02 train vs heldout is **not** crossed for fitting heads
     (heads still fit only `split==train`);
   - for **evaluation counts only**, use the **union of report-half TB
     rows across the three seeds' calibrated models** is insufficient —
     instead: evaluate each seed's model on the **fixed report half**, and
     if report TB decided denominators for FP or FN are &lt; 40, **also**
     report a secondary cross-fit table where each fold trains on
     train∪calib and scores report (still no report in train).  
   Pragmatic lock: **primary R2 uses report-half TB pooled over 3 seeds**;
   if any seed has TB FP or FN **decided denominator** &lt; 40, widen
   secondary to **all heldout TB rows** (calib∪report) for that metric
   and mark the table `heldout_TB_secondary` — still never train on report.

3. **Bootstrap or Wilson CI** on pooled rates (B=2000, seed=0) for the
   V−B differences; report CI low for the reduction percentages.

### 2.11 Verdict rule R2 (arithmetic — no discretion)

Compare **Arm V vs Arm B** on the primary eval set, **pooled over seeds
{0,1,2}**:

**GO** iff all of:

1. TB **FP rate** reduction `(rate_B − rate_V) / rate_B` ≥ **30%**, and
   the 95% CI low on that relative reduction is **> 0**;
2. TB **FN rate** reduction ≥ **30%**, and CI low **> 0**;
3. overall decided-agreement(V) ≥ overall decided-agreement(B);
4. TB `present→unusable` rate(V) ≤ rate(B) + **2 pp** (abstention guard);
5. unusable recall(V) ≥ unusable recall(B) − **2 pp**;
6. param count \|V − B\| / B ≤ **10%**.

**KILL** otherwise. One-sided wins are KILL (dual-fail oracle).

**H2 (interpretive, not a second GO):** if R2 = GO, report Arm N and Arm E
on the same metrics; expected pattern is FP-rate(V) &lt; FP-rate(N) ≤
FP-rate(E) (present template best for FP). Failure of that pattern does
not revoke GO but must be written into the result memo.

Machine check: `scripts/validation/check_student_path_gate.py --rule
anchor_pair_v2_r1` (fail-closed on missing keys).

### 2.12 On GO → stage B

Plug Arm V into `scripts/validation/fidelity_gate.py` student walk
(Vexcel crop resolved per target; GEHI frames scored with the pair head).
Re-run gate-2 under the **unchanged** bar (ceiling 0.7724; inv-weighted
headline **with decoded-only co-headline**). Pilot GO does **not** pass
the gate.

### 2.13 Budget

Local RTX 4070; patch-token extract (hours, once) + smoke (minutes) +
4 arms × 3 seeds head train (each minutes–tens of minutes) + eval.
Wall-clock ≤ 2 days. Zero API, zero new GT, zero backbone training.

## 3. Out of scope (this pilot)

- Path B sequence-level interval distillation (separate prereg; orthogonal).
- Teacher-side geometry rebuild (ISSUE-25 parallel track).
- Pixel-space GEHI−Vexcel differencing.
- LoRA / backbone finetune.
- Any change to gate-2 bar, decoder, or `agree_key` semantics.
- Deploy-time anchor selection without teacher labels (stage-B only).

## 4. Artifact layout (planned)

```
~/zasolar_data/geid_temporal/pilot_anchor_pair_v2_20260710/
  patch_tokens/           # candidate + vexcel grids
  smoke_domain_gap.json
  arm_V_vexcel/{seed}/
  arm_N_nearest_absent/{seed}/
  arm_E_earliest_absent/{seed}/
  arm_B_cand_matched/{seed}/
  pilot_result.json
  summary.md
```

Harness (to implement after this prereg locks):  
`scripts/validation/pilot_anchor_pair_v2_2026_07_10.py`

## 5. Results (executed 2026-07-10)

Harness: `scripts/validation/pilot_anchor_pair_v2_2026_07_10.py` · artifacts
`~/zasolar_data/geid_temporal/pilot_anchor_pair_v2_20260710/`
(`smoke_domain_gap.json`, `patch_tokens/{cand,vexcel}_grids.npy`,
`vexcel_png/`, `summary.md`).

### Eligibility

| | n |
|---|--:|
| distill anchors | 764 |
| pair-eligible (≥1 absent **and** ≥1 present) | **742** (97.1%) |
| Vexcel-eligible (banked crop, direct or via group) | **742** (100% of pair) |
| pair / vexcel rows | 8358 |

### Domain-gap smoke H3 (report-half Vexcel-eligible, n=80)

Global mean-pool of frozen dinov2_floor patch grids (same space as v2 head):

| comparison | median cos | mean cos |
|---|--:|--:|
| **s_pos** = late GEHI `present` ↔ this Vexcel | **0.314** | 0.321 |
| **s_abs** = earliest GEHI `absent` ↔ this Vexcel | **0.214** | 0.237 |
| **s_neg** = other-target Vexcel ↔ this Vexcel | **0.888** | 0.864 |

Locked rule: median `s_pos > s_abs` **and** median `s_pos > s_neg`.

| check | result |
|---|:---:|
| s_pos > s_abs (0.314 > 0.214) | **PASS** |
| s_pos > s_neg (0.314 > 0.888) | **FAIL** |

> **H3 = SMOKE_KILL.** Head training **not run** (fail-closed).  
> Reading: within the GEHI→Vexcel comparison, late present *is* closer to the
> census crop than early absent (+0.10 cos) — weak ranking signal exists —
> but absolute GEHI↔Vexcel similarity (~0.31) is far below within-Vexcel
> similarity (~0.89). **Sensor/domain gap dominates the frozen DINO space.**
> A Vexcel present template is not a usable nearest-neighbor match for GEHI
> candidates under this encoder without further adaptation (banned: backbone
> finetune / LoRA).

Implementation notes (not bar changes):

- Vexcel GeoTIFFs converted to RGB PNG via rasterio (PIL libtiff XMP crash).
- Full 37×37 grids cached float16 on disk; training path would centre-crop
  to 15×15 for RAM (never reached).

### Arms / R2

- Arm V vs B: **not run** (smoke gate)
- Arms N/E: **not run**
- Verdict per R2: **n/a — STOPPED_SMOKE_KILL**

### Implications (post-run)

1. **Vexcel-present pairing as embedding-space template match is blocked
   under frozen dinov2_floor** by domain gap, not by a failed head. Re-opening
   requires either (a) same-sensor present ref (latest teacher-`present` GEHI —
   DeepSolar++ polarity without cross-sensor jump), (b) an explicit
   domain-robust projector (new prereg; still no LoRA if user veto holds),
   or (c) dropping embedding match for a different Vexcel use (e.g. FP
   audit strips / human review only).
2. **s_pos > s_abs still holds** — absent vs present ordering vs Vexcel is
   not random; the template idea is not nonsense, only out-of-domain for
   absolute retrieval.
3. **Path B (sequence-level head on GEHI frame embeddings)** was the orthogonal
   live bet after this smoke; it was later executed and **B-R1 KILL** —
   [`DATA-sequence-head-pilot-prereg-2026-07-10.md`](DATA-sequence-head-pilot-prereg-2026-07-10.md).
4. **Follow-on on the pairing line is A″** (same-sensor latest teacher-present
   GEHI template) — separate prereg, no Vexcel:
   [`DATA-anchor-pair-a2-same-sensor-prereg-2026-07-10.md`](DATA-anchor-pair-a2-same-sensor-prereg-2026-07-10.md).
5. Do not write "pairing mechanism killed" — write "**cross-sensor Vexcel
   template failed domain-gap smoke under frozen DINO**."
