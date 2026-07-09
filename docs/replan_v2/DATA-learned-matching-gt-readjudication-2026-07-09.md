# Data memo: GT re-adjudication of the ISSUE-24 learned-matching pilot's 43 disagreement rows via Gemini judge (2026-07-09)

Status: final verdict. **GO — corrected recovery 109/137 (79.6%) clears the
pre-registered 70% bar.** Both gates (10-item synthetic competence gate,
human-consistency gate on the 10 disclosed-peek rows) passed. This
**overturns** the original KILL in
[`DATA-learned-matching-bounded-pilot-2026-07-09.md`](DATA-learned-matching-bounded-pilot-2026-07-09.md)
via a dated correction note there (that memo's body is left unmodified, per
house style). ISSUE-24's Step 6 weak-lock probe is now unlocked but **not
run by this memo** — out of scope here.

Parent: [`DATA-learned-matching-bounded-pilot-2026-07-09.md`](DATA-learned-matching-bounded-pilot-2026-07-09.md)
(the KILL verdict being re-adjudicated) and
[`ISSUE-24-learned-feature-matching.md`](ISSUE-24-learned-feature-matching.md).
Related: [`DATA-dino-coarse-bounded-kill-2026-07-08.md`](DATA-dino-coarse-bounded-kill-2026-07-08.md),
[`DATA-gehi-displacement-audit-2026-07-06.md`](DATA-gehi-displacement-audit-2026-07-06.md) (ISSUE-23).

## Why this re-adjudication is legitimate (not post-hoc tuning)

The ISSUE-24 pilot KILLed SuperPoint+LightGlue at 68.6% (94/137) against a
pre-registered 70% bar. Post-verdict diagnostics (still within the closed
pilot's own record, not a new run) found that 30 of the 43 failures are
"zero-pinned": SuperPoint+LightGlue's estimate is a near-zero offset (<2m)
while the positive control's GT — phase-correlation's `(known_dx_m,
known_dy_m)` — claims an offset ≥5m (the population filter itself:
`best_offset_m>=5.0`). A human visual inspection of 10 sampled zero-pinned
rows (red/green ghosting overlay: red=Vexcel ref, green=GEHI mov aligned by
each candidate offset) found that on the majority of the 10, the near-zero
SuperPoint+LightGlue estimate is the alignment that actually registers
building edges, and phase-correlation's "known" offset is the one that
misaligns them. The mechanism: phase-correlation aliasing on repetitive
rowhouse/townhouse roof fabric — nearby, near-identical roof units give
phase correlation a confident wrong lock, and the pilot's own `≥5m` positive-
control filter selectively enriches for exactly these aliased locks (a true
near-zero registration would fail that filter and never enter the
population).

The overlay-rendering sign convention (which direction to shift the moving
image to align it with a given `(dx, dy)` estimate) was independently
validated by a synthetic check before the human inspection: `np.roll(ref,
shift=(dy_px, dx_px))` to build a synthetic `mov`, corrected by
`scipy.ndimage.shift(mov, shift=(-dy_px, -dx_px))`, recovers `ref` with
MAE 0.0000. This is a genuinely new fact discovered after the KILL verdict
was written — a broken-instrument discovery about the positive control's own
GT, not an ablation on the matcher under test. Re-opening a closed
bounded-kill verdict on this basis is a legitimate exception to the "no
further ablation without a new hypothesis" discipline the KILL memo itself
states (§7): the new hypothesis is about the referee (phase-correlation GT),
not about SuperPoint+LightGlue.

Per user decision, GT re-adjudication on the 43 disagreement rows is done by
a Gemini judge (`gemini-3-flash-agent`, local gateway) rather than full human
adjudication, under the pre-registered protocol below — written and
committed **before** any Gemini call on real data.

## 1. Pre-registration (fixed before any Gemini call)

1. **Population**: the 43 `recovered==False` rows of
   `~/zasolar_data/geid_temporal/pilot_learned_match_2026-07-09/full137/pilot_results.csv`
   (`recovered` = the pilot's own primary tolerance, `error_m <=
   1.5*cell_diag_m`, per the parent pilot's pre-registration). The 94
   `recovered==True` rows are **exempt from adjudication**: two independent
   estimators (phase-correlation and SuperPoint+LightGlue) agreeing within
   tolerance is treated as valid GT on its own, without needing a third
   judge.
2. **Judge**: `gemini-3-flash-agent` via the local Sub2API gateway
   (`GOOGLE_GEMINI_BASE_URL=http://localhost:8080/antigravity`,
   `GEMINI_API_FORMAT=native`, `GEMINI_NATIVE_PATH=/v1beta`), one call per
   row/item. Output schema, strict JSON, no markdown fences:
   ```json
   {"alignment_verdict": "A" | "B" | "neither", "confidence": 0.0-1.0, "reasoning": "<brief>"}
   ```
   `reasoning` is instructed to stay to one short sentence — `max_tokens`
   is capped at the repo's existing `GEMINI_MAX_TOKENS_PER_CHIP=4000`
   (generous relative to a one-sentence answer), because a known Gemini
   failure mode on this gateway is abstention from token exhaustion on
   verbose reasoning, not from the visual task itself.
3. **Blinding**: every item shows exactly 3 overlay images, always in this
   fixed order:
   1. `unshifted` — the two photos overlaid with no alignment correction.
   2. `alignment A`
   3. `alignment B`

   Each overlay renders one photo in the RED channel and the other in the
   GREEN channel (red=Vexcel reference, green=GEHI moving image aligned by
   the candidate offset — an implementation detail never disclosed to the
   judge). Correctly-aligned real-world edges cancel to a neutral grey/olive
   line; misaligned edges show a doubled red/green "ghost" edge. The prompt
   explains this ghosting semantics generically (as an image-registration
   visual, not tied to any provider) and asks which of A or B better
   resolves building/roof edges, or "neither" if both still show clear
   ghosting on structure edges.

   Which physical candidate (phase-correlation's `known` offset vs.
   SuperPoint+LightGlue's `est` offset, for the 43 real items; the
   agreed-correct offset vs. the synthetic fake offset, for the 10
   competence-gate controls) is assigned to slot A vs. slot B is randomized
   **independently per item**, `seed=0`, via a dedicated
   `numpy.random.default_rng(0)` stream documented in the script. The
   assignment is recorded in the output CSV (never sent to the judge). The
   prompt text never mentions phase-correlation, SuperPoint, LightGlue,
   RANSAC, "known", "estimate", or which method produced which candidate.
4. **Fail-closed rules**:
   - Parse failure, missing/invalid `alignment_verdict`, or an HTTP/timeout
     error, after 2 retries (3 attempts total) on the same item → recorded
     `raw_verdict="abstain"`. For the 43 real rows, abstain counts **NOT
     recovered**. For the 10 competence-gate controls, abstain counts as an
     incorrect answer.
   - `"neither"` counts **NOT recovered** for the 43 real rows (and
     incorrect for the competence gate).
   - Only an unblinded verdict of "the `est` (SuperPoint+LightGlue) slot is
     correct" counts as **recovered**, for the 43 real rows.
5. **Judge competence gate (run BEFORE the 43 real items)**: 10 synthetic
   control items, seed=0-selected from the 94 `recovered==True` agreement
   rows, where ground truth is known by construction — not by any estimator.
   For each selected row: slot "correct" = the row's own agreed
   `(known_dx_m, known_dy_m)` (phase-correlation and SuperPoint+LightGlue
   already agree within tolerance on this row); slot "fake" = the same
   offset plus a synthetic 13m displacement in a `seed=0`-random direction
   (13m sits inside this population's observed aliasing-error range — see
   the parent pilot's `error_m p90=14.573` for SuperPoint+LightGlue,
   §4 — so the fake is a realistic decoy, not a trivially-obvious one).
   Slot assignment (which of correct/fake is A vs. B) is randomized the same
   way as rule 3. The judge must identify the correct slot on **≥8/10**. If
   the score is **<8/10 → ABORT** the entire re-adjudication: report the
   score, do not run the 43 real items, do not recompute anything, and fall
   back to human adjudication as the next step (out of scope for this
   memo/script).
6. **Human-consistency gate** (run after the 43, before recompute): before
   this protocol was written, the human (disclosed peek) looked at 10 of the
   43 zero-pinned rows and formed an aggregate impression ("majority
   est-correct"); no per-row labels were recorded from that peek. The 10
   rows (`chip_id` suffix / `capture_date`):

   | chip_id suffix | capture_date |
   |---|---|
   | c0014433 | 2015-07-30 |
   | c0011139 | 2022-10-30 |
   | c0005840 | 2018-12-30 |
   | c0006653 | 2018-12-30 |
   | c0005937 | 2022-10-30 |
   | c0004809 | 2021-10-30 |
   | c0005924 | 2022-04-30 |
   | c0002980 | 2018-12-30 |
   | c0005409 | 2016-11-30 |
   | c0004084 | 2021-10-30 |

   After running the 43, Gemini's majority verdict (est vs. not-est) on
   these same 10 rows must also be est-correct. If it **contradicts** the
   human's aggregate read (majority NOT est-correct on these 10) → **STOP**:
   report the contradiction, do **not** apply the recompute rule below —
   escalate instead of auto-overturning the KILL on a result the disclosed
   human peek would not itself endorse.
7. **Recompute rule** (applied only if gates 5 and 6 both pass):
   - **Primary (strictest)**: denominator stays the full 137. Corrected
     `recovered = 94 (exempt agreement rows) + #(43 rows judged est-correct)`.
     **GO iff corrected recovered ≥ 96/137 (70%)**; otherwise **KILL stands**.
   - **Secondary (report-only, never the ruling)**: a variant dropping
     "neither"-judged rows from the denominator (i.e. treating "neither" as
     an indeterminate exclusion rather than a recovered-negative); verdict
     counts by category (est / known / neither / abstain); confidence-score
     distribution (quartiles) split by category.
8. **Disclosure**: the 10-row human peek happened before this protocol was
   written, and the protocol's author knew the human's aggregate direction
   before drafting rules 3–6. Mitigations taken: (a) full blinding of the
   judge to method identity and slot assignment (rule 3); (b) a synthetic,
   ground-truth-known competence gate the judge must pass *before* touching
   real data (rule 5); (c) fail-closed defaults on every ambiguous outcome
   (rule 4); (d) a human-consistency check that can only **block** the
   recompute, never manufacture it (rule 6) — the disclosed peek can stop
   this re-adjudication from overturning the KILL, but cannot by itself
   cause an overturn; only the blinded Gemini judge's verdicts on the full
   43 do that.

## 2. Tooling

New script: `scripts/temporal/readjudicate_learned_match_gt_2026-07-09.py`.
Renders overlays for the 43 disagreement rows + 10 synthetic competence-gate
controls (JPEG, ~560px side, quality 85), reusing (via `importlib`, no
copy-paste) `build_ref_cache` / `find_anchor_dir` / `find_mov_path` from the
frozen `pilot_learned_match_2026-07-09.py` and
`chip_displacement.reproject_to_grid` for the mov-side reprojection — the
same recipe validated by the pre-verdict synthetic sign check (see
"Why this re-adjudication is legitimate" above), carried into this repo via
`--self-check` mode (synthetic `np.roll`/`nd_shift` round-trip, MAE
tolerance, PASS/FAIL). Calls the local Gemini gateway through the existing
`scripts/validation/gemini_solar_image_review.py` client helpers
(`load_env_file`, `env_value`, `post_native_generate_content`,
`extract_json_object`, `RateLimiter`) — imported, never modified. Runs the
competence gate, then (if passed) the 43 real items, at ~1 qps. Writes
per-row CSVs + a summary txt to
`~/zasolar_data/geid_temporal/gt_readjudication_2026-07-09/`. Nothing but
this script and this memo lives in the repo; no imagery, overlays, or raw
Gemini responses are committed.

**Frozen, not modified by this work**: `dino_coarse_match.py`,
`pilot_dino_rescue_2026-07-08.py`,
`diagnose_dino_positive_control_2026-07-08.py`,
`pilot_learned_match_2026-07-09.py`, `chip_displacement.py`,
`chip_geometry.py`, `audit_gehi_displacement.py`,
`gemini_solar_image_review.py`.

## 3. Results

Full run: pre-registration commit `2259593`, script run 2026-07-09 23:44–23:49
(local), `~/zasolar_data/geid_temporal/gt_readjudication_2026-07-09/`
(`competence_gate_results.csv`, `readjudication_43_results.csv`,
`summary.txt`, `overlays/`).

### 3.1 Gateway smoke test

PASS — a trivial `{"ok": true}` round-trip returned `finishReason=STOP`,
`modelVersion=gemini-3-flash-a`, before any real item was rendered or sent.

### 3.2 Competence gate (10 synthetic ground-truth-known controls)

**8/10 correct → PASS** (bar ≥8/10). The 2 misses were both `"neither"`
verdicts on items where one slot was in fact correct by construction — in
neither miss did the judge actively pick the fake (wrong-by-13m) slot. That
is the conservative failure direction: the judge's error mode here is
under-identification ("I can't tell, call it neither"), not
over-identification (confidently endorsing a decoy). Combined with the
fail-closed rule that `"neither"` counts as NOT recovered on the real 43,
this means the judge's known bias works *against* a false GO, not for one —
if anything, some genuine est-correct or known-correct rows in the 43 are
likely mis-called `"neither"` and undercounted in the recompute below.

### 3.3 Human-consistency gate

**6/10 PASS** — Gemini's majority verdict on the same 10 rows the human
disclosed-peeked before this protocol was written is est-correct (6 est / 4
not-est), consistent with the human's aggregate "majority est-correct" read.
Gate not contradicted; recompute proceeds.

### 3.4 43-row category counts

| category | n | share |
|---|---:|---:|
| est (SuperPoint+LightGlue judged correct) | 15 | 34.9% |
| known (phase-correlation judged correct) | 13 | 30.2% |
| neither | 15 | 34.9% |
| abstain | 0 | 0.0% |

By area bucket (est / known / neither, n in parens): a_xs(<15) 6/3/7 (16),
b_sm(15–40) 3/6/5 (14), c_md(40–100) 1/4/1 (6), d_lg(≥100) 5/0/2 (7).
Small-n caveat applies to every bucket. Confidence (0–1) by category: est
mean 0.757 (0.70–0.80), known mean 0.731 (0.70–0.80), neither mean 0.693
(0.60–0.75) — compressed into a narrow band overall, but ordered in the
expected direction.

### 3.5 Recompute and re-ruling

**Primary** (denominator 137, rule 7): corrected recovered = 94 (agreement,
exempt) + 15 (est-correct on the 43) = **109/137 = 79.6%**. GO bar ≥96/137
(70%). **109 ≥ 96 → GO.** This **overturns** the original KILL.

**Secondary** (report-only): dropping the 15 `"neither"` rows from the
denominator, 109/(137−15) = 109/122 = **89.3%**.

### 3.6 Directional findings

- The zero-pinned-failure / phase-correlation-aliasing hypothesis is
  confirmed on new, blinded data, not just the disclosed 10-row human peek:
  15/43 (34.9%) of the original "failures" are cases where
  SuperPoint+LightGlue's estimate is the alignment that actually registers
  the buildings, and phase-correlation's own claimed GT is the misaligned
  one.
- 13/43 (30.2%) go the other way — phase-correlation's offset is confirmed
  correct and SuperPoint+LightGlue's estimate is genuinely wrong on that
  row. The aliasing artifact does not absorb the entire KILL: SuperPoint+
  LightGlue has real, if smaller, failure modes on this population beyond
  the contaminated-GT rows.
- 15/43 (34.9%) are `"neither"`. Given the competence gate's own bias
  (§3.2 — both misses were `"neither"`-on-a-valid-case, never a
  wrong-slot pick), this bucket likely contains some genuine est-correct
  or known-correct rows the judge under-called. Under the pre-registered
  fail-closed rule they are counted NOT recovered regardless, so the
  109/137 (79.6%) headline is a conservative floor, not an inflated number.
- By area bucket, est-correct concentrates at the small end (a_xs 6/16 —
  smallest, most repetitive-rowhouse-prone installations, consistent with
  the aliasing mechanism) and the large end (d_lg 5/7, with 0/7 judged
  known-correct in that bucket — SuperPoint+LightGlue's dense corner
  detection has the most structure to work with on large roofs).
  Small-n, descriptive only.
- No retuning of the matcher itself (RANSAC threshold, keypoint cap,
  confidence filtering) was done anywhere in this re-adjudication — it only
  relitigates the referee (phase-correlation GT) on the 43-row disagreement
  set, per the pre-registered scope and the parent KILL memo's own "no
  further ablation without a new hypothesis" discipline.

## 4. Verdict

**GO.** SuperPoint+LightGlue's corrected recovery on the 137-row positive
control is 109/137 (79.6%), clearing the pre-registered 70% bar, with the
human-consistency gate confirming the direction of the disclosed pre-protocol
peek rather than contradicting it. ISSUE-24's Step 6 weak-lock probe
(bounded PSR<12 subsample, pre-registered in the parent pilot memo's
"Weak-lock probe (only if GO)" clause) is now unlocked but **not run by this
memo** — reported as unlocked only, out of scope here per the execution
brief.
