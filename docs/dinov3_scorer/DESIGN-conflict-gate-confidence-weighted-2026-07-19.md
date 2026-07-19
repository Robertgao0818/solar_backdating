# DESIGN (v2.1 FINAL) — Conflict-gate confidence-weighted redesign (2026-07-19)

Status: **FINAL v2.1** — owner approved the five-point revision direction
(2026-07-19); team-lead reviewed, corrected, and finalized same day.
**Implementation remains gated** on §3's telemetry-first preconditions,
panel, and hard kill criteria — this document licenses the design and the
validation plan, not an immediate guard change. Zero
`.py` changes made while producing this document (analysis done via a
one-off scratchpad script, not committed, per instructions). All figures
below are computed by actually loading and joining the real R2 data products
(paths in §0); none are transcribed from the DATA memo's prose without
re-derivation from the underlying JSONL.

## Revision history

- **v1 (2026-07-19, initial delivery)**: Design A (floor-gated conflict
  check, guard change `prior_raw.psr > 0` → `prior_raw.ok`), Designs B/C/D as
  alternatives/complements, calibration plan, red-line statement.
- **v2 (2026-07-19, this revision)**: team-lead independently re-derived the
  17-item mechanism-1 population's `periodicity_alias_psr` and found v1's
  Design A opens a loophole — the `low_confidence` path is exempt from the
  cascade's own periodicity check (§1.6), and 6/17 of the conversion
  population sit at or above the already-calibrated periodicity-escalation
  threshold, including **item_13, a confirmed miskill**, which matches the
  known same-domain row-house failure signature (§11.2.3 of the DATA memo).
  Owner approved five revisions on team-lead's review: (1) Design A →
  **Design A′**, adding a periodicity gate to the accept branch (§2); (2)
  the validation panel (§3) now stratifies by `alias_psr` band with an
  explicit oversampling quota; (3) the kill criterion is now a hard
  pre-registered number, not a discretionary "report and discuss" (§3); (4)
  the `WeakLockReviewRecord` telemetry extension (§1.4) is now a **hard
  precondition** for the guard flip, not a suggestion — implementation order
  is telemetry → shadow replay → guard flip (§3); (5) the implementation
  gets a full pre-registered migration prediction + backup-dir + diff-or-
  stop acceptance protocol matching this repo's own §13.3 convention (§3).
  §4 (red lines) and §5 (limitations) are updated accordingly. All v1
  content that is still accurate is kept; superseded claims are marked
  **(v1, superseded)** in place rather than silently deleted.
- **v2.1 (2026-07-19, team-lead final review & finalization)**: three
  corrections on independent re-verification — (a) §1.6 table's …11715
  `phase_corr_psr` fixed 7.84 → 7.83 (actual 7.8345; was inconsistent with
  §1.2's own stated max); (b) §3 step 3 gained a feasibility note: the
  18/12 panel quotas exceed the current replay's in-sample stratum
  populations (11 A / 6 B), so a documented, seed-locked shadow-replay
  extension is a hard prerequisite of the panel; (c) §2 gained a note that
  the `prior_raw is None` case (single-stage debug mode only) keeps
  status-quo behavior. item_03/05/13's accept/abstain placement under A′
  re-verified from data by team-lead (5.009 / 5.154 / 5.918 vs the 5.5
  cutoff). Status flipped to FINAL.

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
estimated **~2,650 observations** affected by this exact mechanism — a
mechanical extrapolation, not a validated corpus number, flagged again in
§5). **(v2 update, see §1.6/§2)**: after adding the periodicity gate, only
11/17 of these actually convert to accept under the recommended design
(Design A′) — the corpus-scale estimate for the *accept* conversion is
therefore **~1,710** (11/2000 × 311,195), with a further **~930** (6/2000 ×
311,195) remaining forced-abstain by design, not by oversight.

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
recommending a floor-gated design (§2, Design A′) over a smooth blended one
(§2, Design B) as the primary fix.

### 1.6 (v2 addition) The loophole in v1's Design A: the `low_confidence` path is exempt from the cascade's own periodicity check

Team-lead's independent re-derivation, re-verified here against the same
data (`weak_lock_review.jsonl` × `periodicity_diag.jsonl`, joined by
`(anchor_id, capture_date)`, exactly as in §1.2): v1's Design A widens the
`else` branch (`prior_raw.ok == False`) to an unconditional accept. But the
`low_confidence` escalation path is the **only** one of the cascade's three
escalation triggers that never calls `periodicity_score` at all —
`_periodicity_escalation_reason` (`replay_localization_cascade.py:574-592`)
only fires for `tlo.cascade_stage == "phase_correlation"` observations that
are *either* a confident lock *or* an out-of-bounds abstain; a
`low_confidence` observation is neither (`target_localized=False`,
`abstain=False`, `failure_reason=="low_confidence"`), so it structurally
never reaches the periodicity gate that the pass-side/abstain-side triggers
already have. §11.2.3's row-house failure signature (weak_lock producing a
confident-looking but wrong small-offset lock on highly periodic content)
is a property of **weak_lock itself**, not of which escalation reason
invoked it — so nothing about the `low_confidence` trigger makes its
weak_lock calls immune to that signature. v1's Design A would have accepted
all 17 mechanism-1 conflicts with zero periodicity screening, reopening
exactly the failure mode §13 exists to guard against, just via a different
door.

Re-deriving `periodicity_alias_psr` for all 17 mechanism-1 conflicts
(`periodicity_score` computed on the same masked `mov_gray` the real
replay scored, via the `periodicity_diag.jsonl` sidecar — same join method
as §1.2/§1.4, cross-checked line-for-line against team-lead's independent
numbers, **exact match**):

| anchor (short) | capture_date | alias_psr | phase_corr psr | weak_lock params | known ground truth |
|---|---|---|---|---|---|
| …31836 | 2023-01-23 | 10.791 | 7.15 | 1.21/−0.44 | unconfirmed |
| …39781 | 2019-12-30 | 7.461 | 7.33 | −1.60/1.46 | unconfirmed |
| …28253 | 2021-06-30 | 7.114 | 7.43 | 0.14/1.80 | unconfirmed |
| …33041 | 2022-03-30 | 7.026 | 6.92 | −4.37/1.63 | unconfirmed |
| …16332 (item_13) | 2019-01-15 | **5.918** | 6.46 | −0.18/1.10 | **confirmed miskill (usable frame)** |
| …03401 | 2020-05-31 | 5.562 | 7.12 | 0.43/0.37 | unconfirmed |
| — **5.5 cutoff** (`PERIODICITY_ESCALATION_ALIAS_PSR`, §11.1 of the DATA memo) — | | | | | |
| …01264 | 2019-01-15 | 5.239 | 7.43 | −0.75/1.90 | unconfirmed |
| …37582 (item_05) | 2022-05-09 | 5.154 | 6.65 | −0.14/−1.19 | **confirmed miskill (usable frame)** |
| …11715 | 2019-01-15 | 5.027 | 7.83 | −0.92/1.15 | unconfirmed |
| …34377 (item_03) | 2022-05-30 | 5.009 | 7.64 | 0.23/0.27 | **confirmed miskill (usable frame)** |
| …38974 | 2019-01-15 | 4.988 | 7.82 | −0.76/0.43 | unconfirmed |
| …25514 | 2020-09-30 | 4.760 | 7.37 | −0.20/−0.66 | unconfirmed |
| …07538 | 2019-01-15 | 4.727 | 6.20 | 1.00/2.30 | unconfirmed |
| …20500 | 2019-01-15 | 4.610 | 7.60 | −0.58/−0.13 | unconfirmed |
| …32694 | 2021-08-30 | 4.372 | 7.70 | −0.62/−0.81 | unconfirmed |
| …11324 | 2019-01-15 | 4.130 | 6.82 | 0.39/0.59 | unconfirmed |
| …24989 | 2020-04-30 | 3.737 | 6.25 | −0.89/−1.05 | unconfirmed |

6/17 sit at or above the 5.5 cutoff (max 10.79, i.e. above phase_
correlation's own 8.0 confidence floor — a case where the content is
*more* internally periodic than the confidence bar the cascade otherwise
trusts). **One of those 6 is item_13** — a confirmed miskill whose ground
truth (blind panel: "usable frame, negligible correction") is identical in
kind to item_03/05's. This is the central tension v2 exists to resolve:
tightening the accept branch with a periodicity floor fixes the loophole
(and matches items 03/05, which sit safely below 5.5) but leaves item_13
(alias_psr = 5.918, just 0.4 over the cutoff) **unfixed** — see §2's honest
accounting of this cost, not smoothed over.

## 2. Candidate designs

### Design A′ (recommended, revised from v1's Design A) — confidence-floor-gated disagreement, periodicity-screened

**Definition.** Change the conflict-check's participation guard from
`prior_raw.psr > 0` to `prior_raw.ok` (an existing, already-computed
boolean on `RawShift` — `phase_corr.py`'s `estimate_shift_masked` already
sets `ok = psr >= min_psr` after the max-offset check, so `ok` is exactly
"phase_correlation's own stage would have committed this") — **and (v2
addition, closes the §1.6 loophole) gate the newly-widened accept branch on
the same periodicity check the cascade already uses elsewhere**, reusing
the already-calibrated `PERIODICITY_ESCALATION_ALIAS_PSR = 5.5` constant
(§11.1 of the DATA memo) rather than inventing a new number. Concretely:

```
if prior_raw is not None and prior_raw.ok:
    # existing distance check, UNCHANGED (mechanism 2's territory -- §1.3)
    disagreement_m = ‖(dx,dy)_weak_lock − (dx,dy)_phase_correlation‖
    conflict = disagreement_m > CONFLICT_DISAGREEMENT_M
elif prior_raw is not None and periodicity_score(ctx.mov_gray, ctx.mask).alias_psr >= PERIODICITY_ESCALATION_ALIAS_PSR:
    # (v2) prior never cleared its own commit floor, AND the content is
    # highly periodic -- exactly the row-house/repeating-structure regime
    # weak_lock is known to mislock on (§11.2.3). Do not accept on an
    # unscreened weak_lock verdict here; keep today's status-quo behaviour
    # (transform_conflict, forced abstain) rather than opening a periodicity
    # loophole through this trigger.
    conflict = True
else:
    # prior never cleared its own commit floor, AND the content is not
    # flagged as highly periodic -- accept weak_lock's own verdict on
    # weak_lock's own terms (still subject to weak_lock's own inlier-
    # count/ratio floor and its own transform_within_bounds check --
    # nothing about THOSE gates changes).
    conflict = False
```

This is still a narrowly-scoped change: no new threshold value (the 5.5 cut
is reused, not invented, from a different-but-related calibration already
in this cascade — see §5's honest caveat on what that reuse does and does
not inherit), one new call to an already-existing pure function
(`periodicity_score`, already computed elsewhere in the same cascade run
for the pass-side/abstain-side triggers — an implementation detail worth
computing once and threading through rather than recomputing, but that is
an efficiency note, not a design concern), no schema change. (**v2.1
note**: the `prior_raw is None` case — reachable only via the single-stage
debug mode, which calls `_weak_lock_core` with `prior_raw=None` at
`replay_localization_cascade.py:538` — falls into the final `else` exactly
as it bypasses today's guard; cascade-mode escalations always carry a
prior, so no behavior change and no periodicity screen is introduced on
that debug-only path.)

**Implementation note on what "prior never cleared its own commit floor"
now costs.** v1's Design A converted all 17 mechanism-1 conflicts to
accept. Design A′ converts only the 11 with `alias_psr < 5.5`; the other 6
(`alias_psr` 5.562–10.791) remain exactly as they are today
(`transform_conflict`, forced abstain) — **including item_13** (alias_psr =
5.918), a confirmed miskill whose ground truth matches item_03/05's in
kind. This is a deliberate, acknowledged cost of closing the loophole, not
an oversight — see the honest discussion below and in §5.

**Why not a smooth confidence blend instead (this is Design B, below)**:
§1.5 shows raw PSR and `inlier_ratio` are not comparably scaled today (no
existing calibration bridges them — this is the same open gap flagged in
DATA-r2 §7 item 3 for weak_lock's own thresholds), and mechanism 1's
separation is categorical, not magnitude-based (a psr of 7.64 vs weak_lock's
inlier_ratio of 0.78 are not "close" or "far" on any calibrated joint scale
— they're incommensurate units). A floor-gated rule sidesteps the
calibration problem entirely by only asking a question both sides can
already answer on their own terms: "did you clear your own bar?"

**Per-case counterfactual (mechanism 1, the population Design A′ touches —
17 total, full identity list §1.6):**

| bucket | n | who's in it | matches ground truth? |
|---|---|---|---|
| accept (`alias_psr < 5.5`) | 11 | item_03 (5.009), item_05 (5.154), + 9 unconfirmed (…01264, …11715, …38974, …25514, …07538, …20500, …32694, …11324, …24989) | item_03/05: ✅ (confirmed usable frame). 9 unconfirmed — see §3 shadow panel |
| stays `transform_conflict` (unchanged, `alias_psr ≥ 5.5`) | 6 | **item_13 (5.918)**, + 5 unconfirmed (…31836 10.79, …39781 7.46, …28253 7.11, …33041 7.03, …03401 5.56) | **item_13: ✗ — a confirmed miskill remains unfixed** (the cost of closing the §1.6 loophole). 5 unconfirmed |

**Honest accounting, not smoothed over**: of the 3 confirmed mechanism-1
miskills, Design A′ fixes 2 (item_03, item_05) and leaves 1 (item_13)
exactly as miskilled as it is today. This is a direct, quantifiable price
for not reopening the periodicity loophole (§1.6) — team-lead/owner have
weighed this trade explicitly (revision history) and accepted it; this
document records the trade rather than arguing it away. If a future,
larger-n panel (§3) shows the 5.5 cutoff is miscalibrated for *this specific
population* (mechanism 1, as opposed to the pass/abstain-side population it
was originally tuned on), that is grounds to revisit the cutoff itself —
but not grounds to widen the loophole back open without redoing that
calibration.

**Per-case counterfactual (mechanism 2, the 13 rescue candidates) —
Design A′ changes nothing here, same as v1's Design A.** All 13 have
`prior_raw.ok=True` (phase_corr psr 8.7–22.8, all above the 8.0 floor), so
the `if prior_raw.ok` branch runs the **existing, unchanged** distance
check — same as today; the new `elif` branch is unreachable for this
population by construction. §13's outer red line
(`periodicity_reason == "periodicity_abstain_side" and
wl_tlo.target_localized` ⇒ discard) is a separate, later check in
`run_full_cascade_traced` that does not depend on `_weak_lock_core`'s
internal conflict logic at all — it fires regardless of what Design A′ does
inside `_weak_lock_core`. So the 13 rescue candidates (item_04/18/27
confirmed wrong, 10 unconfirmed) remain exactly as blocked as they are
today, under Design A′, with no code interaction between the two fixes.
This is verified from the control flow, not assumed (§4).

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
**(v2 note)**: Design A′ already folds in the one signal (`periodicity_
alias_psr`) that a blended score would most plausibly need for mechanism 1,
using it as a categorical gate rather than a blend term for exactly the
reason §1.6 gives (the item_13 case shows the signal is doing real,
threshold-sensitive work, not something safely softened into a weighted
average without re-deriving where the softening would move that boundary).
Design B's incremental value on top of A′ is correspondingly smaller than
it looked in v1.

### Design C (adopt alongside A′; matches stated owner preference) — trigger-reason-separated gate accounting

**Definition.** Report purification/miskill statistics for the `conflict`
bucket split by `escalation_reason` (`low_confidence` vs
`periodicity_abstain_side` vs `periodicity_pass_side`), not pooled. Today's
§10.2 headline ("误杀率 3/16 ≈ 19%, 全部来自 conflict 门") already implicitly
mixes two structurally different populations — 17/23 of current conflicts
are mechanism 1 (categorically distinguishable, addressed by Design A′) and
6/23 are mechanism 2's sibling (genuinely large, high-confidence-on-both-
sides disagreements, §1.3, with no confirmed miskill in that sub-
population). Once Design A′ ships, this split becomes almost free but no
longer total: mechanism 1's conflicts partly disappear from the bucket (the
11 with `alias_psr < 5.5`, folded into accept) but 6 remain (the
`alias_psr ≥ 5.5` subgroup, including item_13) — so the residual `conflict`
bucket after A′ is a **mix** of mechanism 2 (6 items in the 2,000-obs
sample) and the periodicity-screened remainder of mechanism 1 (6 items) —
not the fully homogeneous population a naive read of the earlier draft
might imply. **(v2 addition)**: report the conflict bucket split three
ways, not two: `escalation_reason` **and**, within `low_confidence`, whether
it was screened out by the periodicity gate (`alias_psr ≥ 5.5`) versus
never reaching the gate for some other reason (should be zero under A′,
since every `low_confidence` prior either passes to accept or hits the
periodicity gate — a useful internal-consistency check, not just a
reporting nicety). The *reason* tag (and periodicity-gate outcome) should be
persisted and reported, not just implied by absence, so a future regression
is visible without re-deriving it the way this document had to.

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
(§4), not something this document proposes doing today. **(v2 note)**:
Design A′'s own periodicity gate is, in spirit, a cheaper cousin of this
idea applied to mechanism 1 instead of mechanism 2 — both use an existing,
independently-motivated signal to withhold acceptance rather than trusting
a bare distance/magnitude comparison. Design D remains scoped to mechanism 2
only and remains not-recommended-now for the reasons already stated.

## 3. Calibration and validation plan (prereg-able)

**Honest sample-size statement up front**: the only ground truth available
is n=3 confirmed mechanism-1 miskills (2 of 3 favor Design A′'s
counterfactual — item_03, item_05; the third, item_13, is a confirmed
miskill Design A′ does **not** fix, §1.6/§2) and n=3 confirmed mechanism-2
wrong-rescues out of 13 structurally identical candidates (0 confirmed
*right*, so mechanism 2's population cannot support any positive claim,
only the already-adopted risk-averse block). **A threshold cannot be
honestly re-tuned on n=3.** Design A′'s decision boundary has two
components, both **reused, not newly invented**: (a) `prior_raw.ok`, i.e.
the *existing* `DEFAULT_MIN_PSR=8.0` / max-offset-180px floor, calibrated on
real GEHI same-anchor pairs per `chip_displacement.py`'s docstring; (b)
`PERIODICITY_ESCALATION_ALIAS_PSR=5.5`, calibrated in DATA-r2 §11.1 against
a **different** population (the pass-side/abstain-side triggers' n=4
positive-labeled cases), reused here rather than re-derived. This sidesteps
inventing a *new* number on n=3, but reusing (b) on a population it was not
originally calibrated against is itself an assumption this plan must test
(step 3 below) rather than simply assert. It does not solve the small-n
problem for Design B or a future Design D re-open.

**Implementation order (v2, hard precondition per team-lead/owner
ruling)**: **telemetry → shadow replay → blind panel → kill-criteria
evaluation → guard flip.** The guard in `_weak_lock_core` may not be changed
in code until steps 1–2 below are complete; this document's own §1.6/§2
analysis (which reconstructed `alias_psr` via a join against
`periodicity_diag.jsonl`, a diagnostic pathway not built for this purpose)
was sufficient to **design** the fix but explicitly does **not** satisfy
this precondition — it is a one-off, seed-dependent reconstruction, not the
durable, purpose-built telemetry step 1 requires.

1. **(hard precondition, ships first) Telemetry extension.** Add to
   `WeakLockReviewRecord` (record-only, non-schema, per §1.4): `phase_
   correlation_prior_psr: float | None`, `phase_correlation_prior_ok: bool`,
   `phase_correlation_prior_dx_m`/`dy_m: float | None` (the raw, uncommitted
   estimate that actually drove the conflict decision — currently lost, see
   §1.4), `periodicity_alias_psr: float | None` (persist the value Design
   A′'s gate itself computes, rather than requiring a future audit to
   re-derive it via `periodicity_diag.jsonl` the way this document had to),
   and `weak_lock_n_inliers`/`n_matches: int | None` (currently computed in
   `match_translation_masked` and discarded, §1.4). This step alone is a
   record-only addition and can ship independent of, and before, any guard
   change.
2. **Shadow replay using the new telemetry (not the periodicity_diag
   workaround)**: with step 1's fields landed, re-run the cascade (existing
   code, guard unchanged) and confirm the new `WeakLockReviewRecord` fields
   for the 17 mechanism-1 conflicts reproduce §1.6's table exactly (an
   internal-consistency check on the telemetry itself, not yet a check of
   the new rule). Then compute the *would-be* Design A′ bucket per row
   (`conflict_gate_v2_shadow.jsonl`, mirroring the existing
   `migration_matrix*.json` convention in `routing_v2/`) purely from the now
   directly-persisted fields — no more periodicity-diagnostic side-channel
   dependency. This still requires no GPU re-inference beyond the normal
   replay and makes no code change to the conflict decision itself.
3. **New blind panel, stratified by `alias_psr` band (v2 revision — explicit
   oversampling quota, not uniform sampling)**: draw ~30 items from the
   shadow-replay-predicted population, split into two strata with the
   **higher-alias_psr stratum deliberately oversampled relative to its
   population share**, because it is the stratum that already contains one
   known miskill (item_13) in an n=6 in-sample group — uniform sampling
   would systematically under-power exactly the subgroup this design's own
   trade-off analysis (§2) flagged as the highest-scrutiny one:
   - **Stratum B (`alias_psr ≥ 5.5`, stays `transform_conflict` under A′,
     ~35% of the mechanism-1 population)**: allocate **12 of 30** panel
     slots here (proportional allocation would be ~10-11/30 at this
     population's true share once scaled to appear alongside stratum A in a
     combined draw — allocate above that, weighted toward the low end near
     the 5.5 cutoff, since near-boundary cases are where a wrongly-drawn
     line costs the most). Purpose: characterize how many of the
     "correctly-kept-abstain-per-A′" items are actually further item_13-like
     miskills (the cost side of the trade) versus genuine unclear/risky
     content (the benefit side).
   - **Stratum A (`alias_psr < 5.5`, converts to accept under A′, ~65% of
     the mechanism-1 population)**: allocate the remaining **18 of 30**
     slots, itself sub-stratified to oversample the near-boundary band
     (`alias_psr` in `[4.5, 5.5)`, e.g. 10 of the 18) over the clearly-low
     band (`< 4.5`, e.g. 8 of the 18) — the near-boundary items are the ones
     most likely to be periodicity-driven mislocks that slipped under the
     cutoff by a small margin, so precision there matters more than
     precision on the unambiguous cases.
   - **Feasibility note (v2.1, team-lead final edit)**: the current
     2,000-obs replay contains only **11 stratum-A and 6 stratum-B**
     in-sample candidates — the 18/12 quotas cannot be filled from it.
     Filling the panel therefore requires first **extending the shadow
     replay to additional manifest observations** (drawn from the frozen R0
     manifest with the same stratified-sampling + seed-lock discipline as
     `SAMPLE_LOCK.json`, the extension lock persisted alongside it) until
     each stratum holds at least its quota. With step 1's telemetry landed
     this is a mechanical replay run, not new design work — but it is a
     real prerequisite step the panel cannot skip.
   - Use the existing overlay-pair blind protocol (`blind_review_sheet.
     html`/`answer_key.json` generation method, §5.3 of the DATA memo).
     This is the one step in this plan that requires new human/Codex
     judgment; steps 1–2 and 4 below are mechanical/replay-only.
4. **G3 metric restructuring (Design C)**: report the conflict-gate's own
   purification/miskill numbers split three ways (§2's Design C amendment):
   `escalation_reason`, and — within `low_confidence` — the periodicity-gate
   outcome. Concrete gate: **the residual `transform_conflict` bucket's own
   miskill rate (per the step-3 panel) must not exceed its own purification
   rate**, evaluated separately for stratum A (post-A′ accept population)
   and stratum B (post-A′ still-abstain population) — not pooled, since they
   are answering different questions (did we open a new false-accept
   channel? vs. is the cost of keeping this stratum abstain justified?).
5. **Kill criterion (v2 revision — hard pre-registered number, not
   discretionary)**:
   - **≥ 2 confirmed-wrong accepts** in the step-3 panel's stratum A
     (a case where phase_correlation's sub-floor raw estimate was actually
     closer to truth than weak_lock's confident small correction, i.e. an
     accept Design A′ produced that ground truth contradicts) ⇒ **Design A′
     is rolled back**: stratum A (`alias_psr < 5.5`, mechanism-1 uncommitted
     priors) reverts in full to the pre-A′ conflict-abstain behavior — not a
     partial patch, not a re-tuned threshold, a full reversion of the guard
     change for that population. No further discussion gate — this is a
     hard stop.
   - **Exactly 1 confirmed-wrong accept** ⇒ **freeze rollout**: do not scale
     Design A′ past the 2,000-obs replay/shadow scale; escalate the specific
     case to team-lead/owner before any further action. Not an automatic
     kill, but not a silent continue either.
   - **0 confirmed-wrong accepts** ⇒ proceed to the step-6 implementation
     acceptance protocol.
6. **Implementation acceptance protocol (v2 addition, matches this repo's
   own §13.3 convention)**: before writing the guard-flip code,
   pre-register the exact predicted migration for the 2,000-obs sample:
   **11 named `(anchor_id, capture_date)` rows** (§1.6's stratum-A list)
   flip `low_confidence`-triggered `transform_conflict` → confident accept
   with their exact `weak_lock` transform_params; **6 named rows** (§1.6's
   stratum-B list, including item_13's anchor) remain byte-for-byte
   unchanged; **all other rows in the 2,000-obs sample remain field-for-
   field identical** (no other bucket, `cascade_stage`, or `transform_
   params` value changes — mechanism 2, `dark_zone`, `periodicity_pass_
   side`, and identity-stage rows are all untouched by construction, §2).
   After implementing and re-running: compute actual vs. predicted
   row-by-row; **any mismatch stops the rollout** (same discipline as
   `DATA-r2-localization-replay-2026-07-19.md` §13.3's "不符即停"). Tag
   `observations.summary.json` with a new `conflict_gate_version` field
   (e.g. `"v1_distance_only"` vs. `"v2_confidence_floor_periodicity_
   gated"`) so any downstream consumer can tell which rule produced a given
   replay's outputs without inferring it from behavior. Back up the
   pre-change `observations.jsonl`/`weak_lock_review.jsonl` to a
   `_pre_conflict_gate_v2_backup/` directory before implementing, matching
   the existing `_pre_routing_backup/`/`_pre_rescue_block_backup/`
   convention already used in this repo's `routing_v2/`.

## 4. Red-line compatibility statement

- **§13's ruling is not reversed.** Design A′'s `if prior_raw.ok` branch is
  only reachable by mechanism 1 (`low_confidence`-triggered escalations,
  where `prior_raw.ok=False` by construction — §1.2). Mechanism 2
  (`periodicity_abstain_side`-triggered, `prior_raw.ok=True` by construction
  — §1.3) falls entirely into the unchanged first branch, and even if it
  didn't, `run_full_cascade_traced`'s outer red-line check (discard any
  `periodicity_abstain_side` review that comes back `target_localized=True`)
  is a separate, later gate that does not depend on `_weak_lock_core`'s
  internal conflict decision at all. Verified from control flow, not
  inferred.
- **(v2 addition) Risk-direction discussion — Design A′'s new accept path
  moves the same direction (abstain → localized) that §13's blocked rescue
  moved, and that is exactly why it needs its own periodicity screen.** Any
  path that converts a forced abstain into `target_localized=True` is, by
  the same logic §13 already applied, moving in the contamination-
  increasing direction (a wrong accept can feed a training signal; a wrong
  abstain only costs coverage). This is not a reason to avoid Design A′
  altogether — it is the reason v1's Design A was incomplete and v2 adds the
  periodicity gate rather than shipping the bare floor-gate alone. The
  distinction that makes Design A′'s accept path defensible despite sharing
  that risk direction with the blocked rescue: (a) it only fires when
  phase_correlation had **no** confident competing opinion to override
  (`prior_raw.ok=False`) — unlike the abstain-side rescue, which specifically
  discarded a phase_correlation estimate that **was** confident; and (b) it
  is now screened by the identical periodicity check that motivates §13's
  block in the first place, using the same calibrated threshold, not a
  weaker or newly-invented one. This does not eliminate the risk (§1.6/§2's
  honest accounting of item_13 shows the screen is imperfect on this
  population), but it is a materially different, more constrained situation
  than the one §13 shut down, and the validation plan (§3) is designed to
  test that claim rather than merely assert it.
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
- **Absent-only-if-localized red line is untouched.** Design A′ changes
  which observations reach `target_localized=True`; it does not touch
  `effective_label`'s gate logic (§3.3 of `observation.py`), which continues
  to downgrade `absent` to `uninformative` exactly as before whenever
  `target_localized=False` or `abstain=True`.
- **(v2 addition) The periodicity threshold reused in Design A′ is not a new
  number invented on this task's small sample.** `PERIODICITY_ESCALATION_
  ALIAS_PSR = 5.5` is DATA-r2 §11.1's own calibrated constant (chosen there
  to catch all 4 known positives in a **different** population — the pass-
  side/abstain-side triggers). Reusing it here is consistent with this
  document's own stated principle (v1 §3, restated in v2 §3) of not
  inventing thresholds on tiny n — but reuse across populations is an
  assumption, not a free pass, which is exactly why §3's stratified panel
  exists to test it on *this* population specifically.

## 5. Honest limitations

- **Design A′'s counterfactual is confirmed on n=2 of 11 accept-side
  conversions (item_03, item_05), and demonstrably wrong on n=1 of 6
  stay-abstain items (item_13) — 0 of the ~1,710/~930 corpus-scale
  extrapolations are validated.** The extrapolations are a mechanical ratio
  (11/2000 × 311,195 and 6/2000 × 311,195 respectively), not a re-run of the
  full corpus — the R2 cascade has only ever been executed on the 2,000-obs
  stratified sample. Treat both corpus numbers as order-of-magnitude
  planning estimates only.
- **Design A′ is untested against a max-offset-type (`raw.ok=False` via
  offset>180px rather than low-psr) mechanism-1 case** — none of the current
  17 conflicts are that sub-case (all are the low-psr sub-case), so the rule
  is exercised on only one of its two intended trigger paths in this data.
- **item_13 is a known, acknowledged cost, not a hidden one** — Design A′
  leaves a confirmed miskill unfixed in exchange for closing the §1.6
  loophole. Whether this trade is net-favorable at corpus scale (i.e.
  whether stratum B's ~930 estimated observations contain proportionally
  more item_13-like miskills than genuinely-risky content) is precisely what
  §3's stratum-B-oversampled panel is designed to determine — this document
  does not yet know the answer.
- **The 5.5 periodicity cutoff's applicability to mechanism 1 is itself
  unvalidated** — it was calibrated (DATA-r2 §11.1) against the pass-side/
  abstain-side triggers' n=4 positive-labeled cases, a different population
  from mechanism 1's `low_confidence` trigger. Reuse is principled (not
  inventing a new number) but not yet empirically justified for *this*
  population — see §4's honest framing and §3 step 3's stratified panel.
- **Mechanism 2 has zero confirmed-correct examples** (3 confirmed wrong,
  10 unconfirmed, 0 confirmed right) — this document does not, and cannot
  honestly, claim any design (A′, B, C, or D) resolves mechanism 2; it only
  documents that Design A′ structurally cannot touch it (by design) and that
  Design D would today coincide with the existing blanket block if ever
  revisited.
- **PSR/inlier_ratio cross-calibration remains an open gap** (same one
  flagged in DATA-r2 §7.3 for weak_lock's own thresholds) — this blocks
  Design B, not Design A′, but is worth tracking as a shared prerequisite
  for any future smooth-blending design in this cascade.
- **The telemetry gap in §1.4/§1.6 means this document's mechanism-1 numbers
  (including the periodicity re-derivation in §1.6) required a cross-file
  join against a diagnostic pathway (`periodicity_diag.jsonl`) not
  originally built for this purpose.** If that diagnostic is ever deleted or
  not re-run with a matching seed, this exact analysis could not be
  reproduced without the `WeakLockReviewRecord` field additions recommended
  in §1.4/§3 — which is precisely why §3 now makes those additions a hard
  precondition rather than a suggestion.

## 6. Summary for reviewers

**Recommendation**: adopt Design A′ (confidence-floor-gated disagreement,
periodicity-screened — change `_weak_lock_core`'s conflict-check guard from
`prior_raw.psr > 0` to a two-part check: `prior_raw.ok` for the existing
distance-check branch, and — when `prior_raw.ok` is false — accept only if
`periodicity_score(...).alias_psr < PERIODICITY_ESCALATION_ALIAS_PSR`)
together with Design C (report conflict-gate purification/miskill numbers
split by `escalation_reason` and periodicity-gate outcome). Do not adopt
Design B now (uncalibrated cross-signal scales). Do not adopt Design D now
(would reopen §13's territory without new authorization); document it for a
future decision only. Implementation is gated on the telemetry-first
precondition and hardened kill criteria in §3 — this is a recommendation to
design and validate, not to ship the guard change immediately.

**Counterfactual summary**: of the 17 mechanism-1 conflicts, **11 flip to
correct/plausible accepts under Design A′** (including 2 of the 3 confirmed
miskills — item_03, item_05), and **6 remain exactly as forced-abstain as
today** (including **item_13, a confirmed miskill that stays unfixed** — an
acknowledged cost, §1.6/§2/§5). All 13 abstain-side rescue candidates
(including the 3 confirmed-wrong item_04/18/27) are **unchanged** — still
blocked by §13, exactly as today, with the interaction verified from control
flow (§4).

**Largest open risk**: whether the reused 5.5 periodicity cutoff is
correctly calibrated for mechanism 1's population specifically (as opposed
to the population it was tuned on) — item_13 sitting only 0.4 above that
cutoff, while item_03/05 sit safely below it, means this design's practical
value hinges on a boundary this document did not itself calibrate and
cannot yet vouch for beyond n=3. The §3 stratified panel (oversampling
exactly the boundary-adjacent and stay-abstain populations) is the concrete
next action that would close this gap, gated by the hardened kill criteria
before any code change ships.
