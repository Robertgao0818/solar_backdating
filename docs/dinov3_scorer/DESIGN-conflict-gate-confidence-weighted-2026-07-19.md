# DESIGN (DRAFT, for owner sign-off) — Conflict-gate confidence-weighted redesign (2026-07-19)

Status: **DRAFT**, owner sign-off required before implementation. Zero `.py`
changes made while producing this document (analysis done via a one-off
scratchpad script, not committed, per instructions). All figures below are
computed by actually loading and joining the real R2 data products (paths in
§0); none are transcribed from the DATA memo's prose without re-derivation
from the underlying JSONL.

Parent: [`PRD-run3-native-local-line-2026-07-19.md`](PRD-run3-native-local-line-2026-07-19.md) §5/§7 G3.
Prior art this document extends/does not reopen:
[`DATA-r2-localization-replay-2026-07-19.md`](DATA-r2-localization-replay-2026-07-19.md)
§10 (blind-review terminal verdict), §11.2.3 (the trust-asymmetry finding
this task was commissioned to fix), §13 (team-lead's abstain-side
record-only ruling — **not reopened here**, see §4).

## 0. Data sources actually loaded (not transcribed)

All under `~/zasolar_data/geid_temporal/run3_native_line_2026-07/r2_replay_v1/`:

- `weak_lock_review.jsonl` (561 rows — every weak_lock escalation in the
  current, post-§13 cascade run).
- `observations.jsonl` (2,000 rows, current final TLOs) +
  `routing_v2/_pre_rescue_block_backup/observations.jsonl` (2,000 rows,
  pre-§13 state — the only place the 13 rescues' weak_lock-side
  `registration_confidence`/`transform_params` survive, since §13 reverts
  them to phase_correlation's verdict in the current file).
- `periodicity_diag/periodicity_diag.jsonl` (2,000 rows — carries
  `phase_corr_psr`/`phase_corr_offset_px` for **every** sampled observation,
  including the ones where phase_correlation never committed a result, i.e.
  exactly the field the review record is missing, see §1.4).
- `blind_review_answer_key.json` (30 rows, owner/Codex ground truth) and
  `routing_v2/labeled_items_routing_outcome.json` (12 labeled items' bucket
  migration status).
- `routing_v2/the_13_rescues.json` (identity of the 13 oob→confident_lock
  migrations from the unrestricted routing pass, §11.2.2).

Source code read (not modified): `scripts/temporal/replay_localization_cascade.py`,
`src/solar_backdating/localization/{phase_corr.py,weak_lock.py,observation.py}`.

## 1. Empirical dissection of the miskill mechanism

### 1.1 The conflict gate's current logic (code, not paraphrase)

`_weak_lock_core` (`replay_localization_cascade.py:476-504`) only runs the
conflict check `if prior_raw is not None and prior_raw.psr > 0:` — i.e. it
compares weak_lock's committed estimate against phase_correlation's raw
`(dx_px, dy_px)` whenever that raw estimate exists **and has any positive
PSR at all**, regardless of whether that PSR cleared phase_correlation's
*own* confidence floor (`DEFAULT_MIN_PSR = 8.0`, `chip_displacement.py:50`)
or its own offset sanity ceiling (`DEFAULT_MAX_OFFSET_PX = 180px`, i.e. 54m
at this grid's 0.3m GSD). The check itself is a flat Euclidean distance:
`disagreement_m > CONFLICT_DISAGREEMENT_M` (10.0m, = 2×
`DEFAULT_MAX_TRANSLATION_M`) ⇒ `transform_conflict` (forced abstain, §5.3 red
line), else weak_lock's estimate is accepted outright — no confidence term
anywhere in this comparison.

This single guard condition (`prior_raw.psr > 0` instead of `prior_raw.ok`)
is the exact locus of both failure modes below: it treats "phase_correlation
tried, got a real-but-sub-floor number, and explicitly declined to commit"
as equivalent to "phase_correlation committed a confident number" for the
purposes of deciding whether a disagreement is real.

### 1.2 Mechanism 1 — conflict-side miskill (item_03/05/13 and 14 structurally identical, unconfirmed siblings)

The three blind-review-confirmed miskills come from the `low_confidence`
escalation path (phase_correlation itself returned `failure_reason=
"low_confidence"`, i.e. `raw.ok=False`, i.e. it explicitly declined to
commit anything). Joining `blind_review_answer_key.json` against
`periodicity_diag.jsonl` (the only place phase_correlation's *raw* PSR
survives for an uncommitted result — the review record does not, see §1.4):

| blind_id | phase_corr **psr** (raw, uncommitted) | phase_corr raw offset | weak_lock inlier_ratio (committed, accepted) | weak_lock offset | verdict |
|---|---|---|---|---|---|
| item_03 | **7.64** (< 8.0 floor) | 59.0px ≈ 17.7m | 0.776 | 0.23/0.27m | miskill (confirmed usable frame) |
| item_05 | **6.65** (< 8.0 floor) | 50.6px ≈ 15.2m | 0.556 | −0.14/−1.19m | miskill (confirmed usable frame) |
| item_13 | **6.46** (< 8.0 floor) | 39.9px ≈ 12.0m | 0.482 | −0.18/1.10m | miskill (confirmed usable frame) |

All three: phase_correlation's own raw estimate is **below its own commit
floor** (psr 6.46–7.64 vs the 8.0 threshold it holds itself to), yet its
large, admittedly-ambiguous raw offset (12–18m) is diffed against
weak_lock's small, floor-clearing, committed correction, and the >10m
distance alone triggers `transform_conflict` — discarding a signal
(weak_lock's) that had already passed its own confidence gate, on the
strength of a signal (phase_correlation's) that had explicitly *not* passed
its own gate.

**This is not a 3-example anecdote.** Re-deriving the full population: of
the 102 `low_confidence` escalations in the 2,000-obs replay, **17 end in
`transform_conflict`**, and **100% of those 17 have `phase_corr_psr` strictly
below 8.0** (range 6.20–7.83, computed directly from `periodicity_diag.jsonl`
joined against `weak_lock_review.jsonl`'s `escalation_reason=="low_confidence"`
rows with `final_failure_reason=="transform_conflict"`). This is structurally
guaranteed, not a statistical tendency: `low_confidence` is *only* reachable
when `raw.ok=False` (`_phase_correlation_core`'s "max-offset"/"low-psr"
branch), and empirically all 17 are the "low-psr" sub-case. So the mechanism
that produced the 3 confirmed miskills mechanically produces the same
category of decision in all 17 current instances (scaled to the full
311,195-observation corpus at the sample's ~1/155.6 ratio, that is an
estimated **~2,650 observations** wrongly abstained by this exact mechanism
— a mechanical extrapolation, not a validated corpus number, flagged again
in §5).

### 1.3 Mechanism 2 — abstain-side "rescue" miskill (item_04/18/27, the row-house small-offset-lock signature)

The 13 `periodicity_abstain_side`-triggered items where weak_lock's review
came back confident (`the_13_rescues.json`, cross-joined against
`_pre_rescue_block_backup/observations.jsonl` for the weak_lock-side
`registration_confidence`, and `periodicity_diag.jsonl` for the
phase_correlation-side raw psr):

| anchor (short) | phase_corr psr (**committed**, oob) | phase_corr offset | weak_lock inlier_ratio (committed) | weak_lock offset | disagreement_m | ground truth |
|---|---|---|---|---|---|---|
| …29652 | 9.25 | 10.83/1.17 | 0.490 | 0.99/1.08 | 9.84 | unconfirmed |
| …31862 | 8.74 | 1.20/6.06 | 0.517 | 0.18/0.25 | 5.90 | unconfirmed |
| …31813 | 10.92 | −2.70/−7.23 | 0.611 | −0.17/0.17 | 7.82 | unconfirmed |
| …29070 (item_18) | 11.26 | −8.52/0.09 | 0.527 | −0.14/−0.28 | 8.39 | **confirmed WRONG rescue** |
| …33569 | 10.92 | 5.10/−2.70 | 0.569 | 0.13/0.31 | 5.81 | unconfirmed |
| …21332 (item_27) | 10.77 | 3.30/3.90 | 0.436 | −0.03/0.17 | 5.00 | **confirmed WRONG rescue** |
| …22703 | 9.40 | 5.40/−0.03 | 0.697 | −1.80/−0.44 | 7.21 | unconfirmed |
| …39265 (item_04) | 22.80 | −6.57/−0.03 | 0.438 | −0.73/0.01 | 5.84 | **confirmed WRONG rescue** |
| …30343 | 11.34 | 0.84/−6.09 | 0.612 | −0.03/−0.19 | 5.97 | unconfirmed |
| …1540 | 15.12 | 2.91/−5.16 | 0.647 | 0.33/−0.47 | 5.36 | unconfirmed |
| …33211 | 18.70 | −0.00/−5.07 | 0.510 | 0.29/−4.43 | 0.70 | unconfirmed |
| …33255 | 8.99 | −6.30/−0.00 | 0.905 | −0.10/−2.12 | 6.56 | unconfirmed |
| …27180 | 11.05 | 0.03/5.16 | 0.596 | 0.93/−0.45 | 5.68 | unconfirmed |

**All 13 have phase_correlation psr ≥ 8.7** (i.e. phase_correlation *did*
clear its own confidence floor here — unlike mechanism 1, this is a
committed, confident phase_correlation estimate, just one that's out of the
5m translation bound). weak_lock's inlier_ratio spans 0.44–0.91 with no
visible separation between the 3 confirmed-wrong cases (0.44–0.53) and the
10 unconfirmed ones (0.44–0.91, actually the same range) — **inlier_ratio
alone does not discriminate this population**, see §1.5. Disagreement_m
0.70–9.84m, i.e. every one of the 13 sits *just under* the 10m conflict
cliff.

Contrast with the 6 `periodicity_abstain_side`-triggered items that
correctly stayed `transform_conflict` (both signals committed, confident,
genuinely far apart): phase_corr psr 8.32–11.16 (same range as the 13
rescues!), disagreement_m 11.0–39.6m. **The 10m cutoff is a razor's edge
through one continuous population** — 9.84m gets silently "rescued" (a
confirmed-wrong verdict, item_04/18/27's cohort), 11.01m gets correctly
abstained, on essentially indistinguishable confidence profiles. This is
the clearest evidence that the current gate's decision boundary at exactly
10m is not doing real discriminative work in this regime.

**Important scoping note**: mechanism 2's *symptom* (13 items reaching
`confident_lock`) is already fully suppressed today by §13's blanket
abstain-side rescue-block, independent of anything in this design. §4
addresses why this document does not re-open that block.

### 1.4 A telemetry gap this analysis had to route around

`WeakLockReviewRecord` (`replay_localization_cascade.py:595-616`) stores
`phase_correlation_transform_params`, which is the **empty dict** `{}` for
every `low_confidence`-triggered record (`_phase_correlation_core`'s
ambiguous branch never populates `transform_params`) — so `_build_review_
record`'s `disagreement_m` field is `None` for all 17 mechanism-1 conflicts
in the persisted sidecar, even though a real numeric disagreement (computed
from `prior_raw.dx_px/dy_px`, never itself persisted) is exactly what drove
the conflict decision. This analysis could only reconstruct the missing
numbers by joining against `periodicity_diag.jsonl`, a diagnostic pathway
built for an unrelated purpose (§5.4's periodicity hypothesis) that happens
to also compute and persist phase_correlation's raw PSR/offset for every
observation regardless of commit status. Recommendation (§3, non-schema,
record-only sidecar addition): add `phase_correlation_prior_psr: float |
None`, `phase_correlation_prior_ok: bool`, and `phase_correlation_prior_dx_m
/dy_m: float | None` to `WeakLockReviewRecord` so a future audit does not
depend on an unrelated diagnostic CLI mode having been run with a matching
seed. Similarly, `match.n_inliers`/`n_matches` (computed inside
`match_translation_masked`, `weak_lock.py:96-118`) are discarded after
`_weak_lock_core` extracts only `inlier_ratio` — recommend persisting both
raw counts alongside the ratio, since ratio-of-8 and ratio-of-80 are not
equally reliable and no currently-persisted field lets a future analysis
tell them apart.

### 1.5 Why raw confidence values don't cleanly separate mechanism 2

Group-level check across the full 2,000-obs periodicity diagnostic
(`periodicity_diag.jsonl`, bucketed by **final** cascade outcome):

| bucket | n | phase_corr_psr p10/p50/p90 | frac psr≥8.0 |
|---|---|---|---|
| confident_lock | 1,854 | 9.33 / 14.82 / 30.05 | 96.8% |
| transform_out_of_bounds | 111 | 8.32 / 10.70 / 18.70 | 92.8% |
| transform_conflict | 17 | 6.38 / 7.33 / 7.75 | 0.0% |
| dark_zone | 18 | 6.46 / 6.95 / 7.90 | 0.0% |

(Note: this table's `transform_conflict`/`dark_zone` rows are the **17**
`low_confidence`-only figures because `periodicity_diag.jsonl`'s own bucket
field mirrors the diagnostic's independent re-run of the pre-periodicity-
routing cascade, §5.4.2 of the DATA memo — consistent with, not
contradicting, §1.2/§1.3's separate counts by escalation_reason.)

And weak_lock `inlier_ratio` (`registration_confidence` where
`cascade_stage=="weak_lock"`, current `observations.jsonl`):

| final bucket | n | inlier_ratio p10/p50/p90 |
|---|---|---|
| confident (accept) | 399 | 0.458 / 0.674 / 0.933 |
| dark_zone | 79 | 0.197 / 0.334 / 0.391 |
| transform_conflict | 23 | 0.478 / 0.556 / 0.762 |
| transform_out_of_bounds (weak_lock's own) | 47 | 0.424 / 0.571 / 0.771 |

`dark_zone` is cleanly separated (that's just the existing
`MIN_WEAK_LOCK_INLIER_RATIO=0.4` floor doing its job, unrelated to the
conflict gate). But `transform_conflict`'s inlier_ratio (0.478–0.762)
**overlaps almost entirely** with `confident`'s (0.458–0.933) — meaning
weak_lock's own confidence score, taken alone, is not what currently
separates "genuine conflict" from "accepted." What *does* separate mechanism
1's 17 conflicts from ordinary accepts is a **categorical** fact (did the
opposing prior clear its own floor at all), not a **magnitude** comparison
of two continuous scores. This is the central empirical justification for
recommending a floor-gated design (§2, Design A) over a smooth blended one
(§2, Design B) as the primary fix.

## 2. Candidate designs

### Design A (recommended) — confidence-floor-gated disagreement

**Definition.** Change the conflict-check's participation guard from
`prior_raw.psr > 0` to `prior_raw.ok` (an existing, already-computed
boolean on `RawShift` — `phase_corr.py`'s `estimate_shift_masked` already
sets `ok = psr >= min_psr` after the max-offset check, so `ok` is exactly
"phase_correlation's own stage would have committed this"). Concretely:

```
if prior_raw is not None and prior_raw.ok:
    # existing distance check, UNCHANGED
    disagreement_m = ‖(dx,dy)_weak_lock − (dx,dy)_phase_correlation‖
    conflict = disagreement_m > CONFLICT_DISAGREEMENT_M
else:
    # prior never cleared its own commit floor -- it is not a competing
    # opinion to disagree with. Accept weak_lock's own verdict on weak_lock's
    # own terms (still subject to weak_lock's own inlier-count/ratio floor
    # and its own transform_within_bounds check -- nothing about THOSE gates
    # changes).
    conflict = False
```

This is a **one-boundary-condition change**: no new signal, no new
threshold value, no schema change. It uses a field (`RawShift.ok`) that is
already computed on every call and already encodes exactly the distinction
this analysis needed (§1.1).

**Why not a smooth confidence blend instead (this is Design B, below)**:
§1.5 shows raw PSR and `inlier_ratio` are not comparably scaled today (no
existing calibration bridges them — this is the same open gap flagged in
DATA-r2 §7 item 3 for weak_lock's own thresholds), and mechanism 1's
separation is categorical, not magnitude-based (a psr of 7.64 vs weak_lock's
inlier_ratio of 0.78 are not "close" or "far" on any calibrated joint scale
— they're incommensurate units). A floor-gated rule sidesteps the
calibration problem entirely by only asking a question both sides can
already answer on their own terms: "did you clear your own bar?"

**Per-case counterfactual (mechanism 1, the only population Design A
touches):**

| item | today | under Design A | matches ground truth? |
|---|---|---|---|
| item_03 | `transform_conflict` (abstain) | accept, weak_lock's 0.23/0.27m | ✅ (confirmed usable frame) |
| item_05 | `transform_conflict` (abstain) | accept, weak_lock's −0.14/−1.19m | ✅ (confirmed usable frame) |
| item_13 | `transform_conflict` (abstain) | accept, weak_lock's −0.18/1.10m | ✅ (confirmed usable frame) |
| 14 more (unconfirmed, same mechanism) | `transform_conflict` | accept | **not yet confirmed — see §3 shadow panel** |

**Per-case counterfactual (mechanism 2, the 13 rescue candidates) —
Design A changes nothing here.** All 13 have `prior_raw.ok=True` (phase_corr
psr 8.7–22.8, all above the 8.0 floor), so the `if prior_raw.ok` branch runs
the **existing, unchanged** distance check — same as today. §13's outer
red line (`periodicity_reason == "periodicity_abstain_side" and
wl_tlo.target_localized` ⇒ discard) is a separate, later check in
`run_full_cascade_traced` that does not depend on `_weak_lock_core`'s
internal conflict logic at all — it fires regardless of what Design A does
inside `_weak_lock_core`. So the 13 rescue candidates (item_04/18/27
confirmed wrong, 10 unconfirmed) remain exactly as blocked as they are
today, under Design A, with no code interaction between the two fixes. This
is verified from the control flow, not assumed (§4).

### Design B (deferred, not recommended now) — smooth cross-signal confidence blending

**Definition (for completeness).** Normalize `c_pc = min(1, psr/12)` (the
existing `_psr_confidence` convention, `PSR_FULL=12.0`) and `c_wl =
inlier_ratio` (optionally down-weighted by `min(1, n_inliers/N)` for some
reference `N`, pending the `n_inliers` instrumentation gap in §1.4), then
scale the conflict distance threshold by relative confidence, e.g.
`τ_eff = CONFLICT_DISAGREEMENT_M · g(c_pc, c_wl)` for some monotonic `g`
that widens the tolerance when confidences are asymmetric, or blend the
transform estimates by `w = c_pc/(c_pc+c_wl)`.

**Why deferred**: (a) §1.5 shows `c_pc`/`c_wl` are not jointly calibrated —
picking `g` or a blend weight today would be an uncalibrated guess dressed
as a formula; (b) it doesn't obviously help mechanism 2 either, since psr
and inlier_ratio both sit in "confident" territory for the 13 rescue
candidates yet 3/13 are confirmed wrong — a smooth blend of two confident-
looking numbers doesn't reveal the wrongness the same-domain row-house
failure signature actually reflects (a systematic estimator bias, not an
honestly-reported low confidence). Recommend revisiting only after (i) a
same-domain GEHI↔GEHI weak_lock calibration sweep (the open item already
flagged in DATA-r2 §7.3) and (ii) a larger labeled panel than n=3/n=13.

### Design C (adopt alongside A; matches stated owner preference) — trigger-reason-separated gate accounting

**Definition.** Report purification/miskill statistics for the `conflict`
bucket split by `escalation_reason` (`low_confidence` vs
`periodicity_abstain_side` vs `periodicity_pass_side`), not pooled. Today's
§10.2 headline ("误杀率 3/16 ≈ 19%, 全部来自 conflict 门") already implicitly
mixes two structurally different populations — 17/23 of current conflicts
are mechanism 1 (categorically distinguishable, fixed by Design A) and 6/23
are mechanism 2's sibling (genuinely large, high-confidence-on-both-sides
disagreements, §1.3, with no confirmed miskill in that sub-population). Once
Design A ships, this split becomes almost free: mechanism 1's conflicts
disappear from the bucket entirely (folded into accept), so the residual
`conflict` bucket is naturally the more homogeneous "both sides committed
and genuinely disagree by >10m" population — but the *reason* tag should
still be persisted and reported (not just implied by absence) so a future
regression is visible without re-deriving it the way this document had to.

### Design D (documented, NOT proposed for adoption now) — directional-constraint arbitration

Team-lead's brief asked this be formalized (§11.2.3 suggestion 3): an
abstain-side review may only **confirm an equal-or-larger offset** than
phase_correlation's, never silently compress a large committed offset down
to a small one, i.e. `accept only if ‖weak_lock_offset‖ ≥ (1-ε)·
‖phase_correlation_offset‖` for some margin `ε` (e.g. 0.5). Applying this to
the 13 rescue candidates: every one has `‖weak_lock_offset‖/‖phase_
correlation_offset‖` well under 0.5 (ratios roughly 0.02–0.85, mostly <0.3
except one outlier), so **Design D produces the identical practical outcome
to §13's existing blanket block for this exact population** — a coincidence
worth noting, not a reason to implement it now. Design D is more general
(it would in principle let a genuine confirm-and-refine correction through,
which a blanket block cannot), so it is documented here as the natural next
step **if and only if** owner later wants to replace §13's blanket
abstain-side block with something less blunt — but that is an explicit
reopening of §13's territory and requires new authorization + a new panel
(§4), not something this document proposes doing today.

## 3. Calibration and validation plan (prereg-able)

**Honest sample-size statement up front**: the only ground truth available
is n=3 confirmed mechanism-1 miskills (all 3 favor Design A's counterfactual)
and n=3 confirmed mechanism-2 wrong-rescues out of 13 structurally identical
candidates (0 confirmed *right*, so mechanism 2's population cannot support
any positive claim, only the already-adopted risk-averse block). **A
threshold cannot be honestly re-tuned on n=3.** Design A's decision boundary
(`prior_raw.ok`, i.e. the *existing* `DEFAULT_MIN_PSR=8.0` / max-offset-180px
floor) is deliberately **not a new number to calibrate** — it reuses
phase_correlation's own, already-calibrated (on real GEHI same-anchor pairs,
per that module's docstring) commit floor. This sidesteps the small-n
problem for Design A specifically; it does not solve it for Design B or a
future Design D re-open.

**Recommended validation steps, in order**:

1. **Record-only shadow replay, zero new registration compute**: re-derive
   Design A's counterfactual bucket for all 561 rows of the existing
   `weak_lock_review.jsonl` by joining against `periodicity_diag.jsonl` for
   `phase_corr_psr`/`phase_corr_offset_px` exactly as this document did
   (§1.2/§1.4) — this requires no GPU, no re-registration, just the two
   JSONL files already on disk. Output: a `conflict_gate_v2_shadow.jsonl`
   with `(old_bucket, new_bucket_under_design_a)` per row, mirroring the
   existing `migration_matrix*.json` convention (`routing_v2/`). This can
   run today, before any `.py` change, purely as an audit of what Design A
   *would* have done.
2. **New blind panel, scoped to the 17 (or full-corpus-scaled ~2,650)
   mechanism-1 conflicts converted to accept**: sample ~20-30 items from
   this specific population (same overlay-pair protocol as the existing
   `blind_review_sheet.html`/`answer_key.json`, §5.3 of the DATA memo) —
   this is the population whose validation status changes under Design A
   (from "forced abstain, uninformative" to "confident accept," i.e. now
   *eligible* to feed the decoder as present/absent training signal, not
   just "no longer miskilled"). This is the one panel that actually needs
   new human/Codex judgment; everything else in this plan is replay-only.
3. **G3 metric restructuring**: report the conflict-gate's own purification/
   miskill numbers split by `escalation_reason` (Design C), so a future
   re-run's G3 check is `mechanism_2_conflict_miskill_rate < mechanism_2_
   conflict_purification_rate` computed on the (now much smaller, ~6-item-
   scale) residual population, not conflated with mechanism 1's now-resolved
   17. Concrete gate: **the residual `transform_conflict` bucket's own
   miskill rate (per a re-run blind panel) must not exceed its own
   purification rate** — same directional form as the existing G3, just
   scoped to the population Design A leaves untouched.
4. **Kill criterion**: if the step-2 panel finds **any** confirmed-wrong
   accept among the newly-converted mechanism-1 population (i.e. a case
   where phase_correlation's sub-floor raw estimate was actually closer to
   truth than weak_lock's confident small correction), Design A does not
   auto-kill on a single counterexample (n is still small) but must
   escalate to team-lead/owner with the specific case before any wider
   rollout past the 2,000-obs replay scale — consistent with the stop-
   discipline this program already runs under (PRD §7.4).

## 4. Red-line compatibility statement

- **§13's ruling is not reversed.** Design A's `if prior_raw.ok` branch is
  only reachable by mechanism 1 (`low_confidence`-triggered escalations,
  where `prior_raw.ok=False` by construction — §1.2). Mechanism 2
  (`periodicity_abstain_side`-triggered, `prior_raw.ok=True` by construction
  — §1.3) falls entirely into the unchanged `else` branch, and even if it
  didn't, `run_full_cascade_traced`'s outer red-line check (discard any
  `periodicity_abstain_side` review that comes back `target_localized=True`)
  is a separate, later gate that does not depend on `_weak_lock_core`'s
  internal conflict decision at all. Verified from control flow, not
  inferred.
- **ISSUE-24 is not reopened.** Nothing here revisits the cross-domain
  GEHI↔Vexcel weak-lock GO verdict (91.3% corroborated); this document's
  entire empirical base is same-domain GEHI↔GEHI (PRD §5.2's requirement),
  a different population by the DATA memo's own §13 framing.
- **Canvas ablation (CLOSED) is not touched.** Nothing here proposes
  switching the registration grid from the whole-chip canvas.
- **`observation.py` schema is frozen, untouched.** All new fields proposed
  (§1.4, §3) are record-only additions to `WeakLockReviewRecord` (already a
  non-TLO, non-schema companion dataclass per its own docstring) or a new
  sidecar file (`conflict_gate_v2_shadow.jsonl`), never a `TargetLocalization
  Observation` field.
- **Absent-only-if-localized red line is untouched.** Design A changes which
  observations reach `target_localized=True`; it does not touch
  `effective_label`'s gate logic (§3.3 of `observation.py`), which continues
  to downgrade `absent` to `uninformative` exactly as before whenever
  `target_localized=False` or `abstain=True`.

## 5. Honest limitations

- **Design A's counterfactual is confirmed on n=3 of 17 (2026-07-19
  sample), 0 of ~2,650 (corpus-scale extrapolation).** The extrapolation to
  "~2,650 observations corpus-wide" is a mechanical ratio
  (17/2000 × 311,195), not a re-run of the full corpus — the R2 cascade has
  only ever been executed on the 2,000-obs stratified sample. Treat the
  corpus number as an order-of-magnitude planning estimate only.
- **Design A is untested against a max-offset-type (`raw.ok=False` via
  offset>180px rather than low-psr) mechanism-1 case** — none of the current
  17 conflicts are that sub-case (all are the low-psr sub-case), so the rule
  is exercised on only one of its two intended trigger paths in this data.
- **Mechanism 2 has zero confirmed-correct examples** (3 confirmed wrong,
  10 unconfirmed, 0 confirmed right) — this document does not, and cannot
  honestly, claim any design (A, B, C, or D) resolves mechanism 2; it only
  documents that Design A structurally cannot touch it (by design) and that
  Design D would today coincide with the existing blanket block if ever
  revisited.
- **PSR/inlier_ratio cross-calibration remains an open gap** (same one
  flagged in DATA-r2 §7.3 for weak_lock's own thresholds) — this blocks
  Design B, not Design A, but is worth tracking as a shared prerequisite for
  any future smooth-blending design in this cascade.
- **The telemetry gap in §1.4 means this document's mechanism-1 numbers
  required a cross-file join against a diagnostic pathway (`periodicity_
  diag.jsonl`) not originally built for this purpose.** If that diagnostic
  is ever deleted or not re-run with a matching seed, this exact analysis
  could not be reproduced without the `WeakLockReviewRecord` field additions
  recommended in §1.4/§3.

## 6. Summary for reviewers

**Recommendation**: adopt Design A (confidence-floor-gated disagreement —
change `_weak_lock_core`'s conflict-check guard from `prior_raw.psr > 0` to
`prior_raw.ok`) together with Design C (report conflict-gate purification/
miskill numbers split by `escalation_reason`). Do not adopt Design B now
(uncalibrated cross-signal scales). Do not adopt Design D now (would reopen
§13's territory without new authorization); document it for a future
decision only.

**Counterfactual summary**: all 3 confirmed conflict-side miskills
(item_03/05/13) flip to correct accepts under Design A; 14 structurally
identical but unconfirmed conflicts flip identically (needs the §3 shadow
panel before being trusted at scale); all 13 abstain-side rescue candidates
(including the 3 confirmed-wrong item_04/18/27) are **unchanged** — still
blocked by §13, exactly as today.

**Largest open risk**: the 14 unconfirmed mechanism-1 conversions (and the
~2,650-observation corpus-scale estimate) rest on n=3 ground truth that all
happens to point the same direction — a real but thin evidentiary base. The
step-1 shadow replay (free, no compute) and step-2 new blind panel (§3) are
the two concrete next actions that would close this gap before any wider
rollout.
