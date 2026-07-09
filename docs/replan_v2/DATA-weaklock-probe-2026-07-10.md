# Data memo: Step-6 weak-lock characterization probe (SuperPoint+LightGlue) (2026-07-10)

Status: executed 2026-07-10. Characterization complete — no GO/KILL verdict
(none applies; see Framing). 137/150 (91.3%) of the sampled weak-lock rows
now carry at least one positive validation signal (phase-correlation
cross-agreement or a blinded-Gemini-confirmed better registration); 13/150
(8.7%) remain unvalidated ("dark").

Parent: [`ISSUE-24-learned-feature-matching.md`](ISSUE-24-learned-feature-matching.md)
(Step 6, unlocked by the GT re-adjudication GO) and
[`DATA-learned-matching-gt-readjudication-2026-07-09.md`](DATA-learned-matching-gt-readjudication-2026-07-09.md)
(the blinded Gemini-judge instrument this probe reuses, validated there:
synthetic competence gate 8/10, conservative under-identification failure
direction, human-consistency gate not contradicted). Grandparent:
[`DATA-learned-matching-bounded-pilot-2026-07-09.md`](DATA-learned-matching-bounded-pilot-2026-07-09.md)
(the 137-row positive-control pilot, corrected recovery 109/137 = 79.6% ->
GO, which unlocked this probe).

## Framing

This is a **characterization probe**, not a GO/KILL verdict and not a
production commitment. It answers one question: on the 2,785-row PSR<12
"weak-lock" population (`ref_kind=="S3_vexcel" & psr<12` — rows where
phase-correlation itself distrusts its own lock, per ISSUE-23), how often
does SuperPoint+LightGlue produce a validated-quality registration? Outputs
are metrics plus a **non-binding** recommendation section. There is no
kill bar here — production adoption, if any, is a separate future issue.

## 1. Pre-registration (fixed before any probe-row matcher run or Gemini call)

1. **Population**: a seed=0 random sample of 150 rows from
   `load_weak_lock_population(12.0)` (`ref_kind=="S3_vexcel" & psr<12`,
   verified n=2,785 against `per_chipdate_offsets.csv` on 2026-07-10). Not
   the full 2,785-row set — a bounded subsample only, per ISSUE-24's own
   "Step 6 ... a second bounded test ... explicitly not a full 2,785-row
   commitment in this same slice" acceptance criterion.
2. **Matcher**: SuperPoint+LightGlue exactly as the pilot froze it
   (`max_num_keypoints=2048`, `ransac_thresh_px=5.0`, GSD 0.15m co-gridded
   registration, same 1-point exhaustive-RANSAC translation fit). No
   tuning of any kind after seeing results.
3. **Calibration pre-step** (runs on already-computed data, before any new
   probe-row matcher call): re-sweep `n_inliers` precision/coverage on the
   137 positive-control rows against CORRECTED labels. Corrected-recovered
   = the 94 rows where the pilot's own `recovered==True` (phase-correlation
   and SuperPoint+LightGlue agreed, exempt from adjudication) + the 15 of
   the 43 disagreement rows judged `est`-correct in
   `readjudication_43_results.csv`. The 13 `known`-correct and 15 `neither`
   rows of the 43 count as NOT recovered under corrected labels (same
   fail-closed convention the re-adjudication itself used). **If** any
   `n_inliers` threshold achieves >=90% precision at >=50% coverage under
   corrected labels, the lowest such threshold (maximizing coverage among
   qualifying cuts) becomes this probe's "confident lock" cut. **Otherwise**
   "confident lock" is not defined by `n_inliers` at all — the probe's two
   independent validation signals (cross-validation vs. phase-correlation,
   and the blinded Gemini judge) carry the characterization instead, and
   `n_inliers`/`inlier_ratio` are reported descriptively only.
4. **Probe metrics** (all reported, none judged against a bar):
   - **(a) Attempt/lock rate**: of the 150 sampled rows, the fraction where
     matcher inputs (ref crop + mov chip) could be built at all
     ("attempted"), and within attempted rows, the fraction where
     SuperPoint+LightGlue actually returns a nonzero-match fit ("locked").
     Missing-data rate (rows skipped for missing anchor dir / missing
     Vexcel crop / missing mov chip / failed reprojection) reported
     separately, broken down by cause.
   - **(b) Cross-validation rate**: fraction of processed rows where
     `hypot(est_dx_m - phasecorr_dx_m, est_dy_m - phasecorr_dy_m) <=
     1.5*cell_diag_m` (the same per-row tolerance family as the positive
     control's primary tolerance; ≈5.6m for this population's near-constant
     chip extent). Two independent estimators (phase-correlation's own
     distrusted point estimate, and SuperPoint+LightGlue) agreeing within
     tolerance on a PSR<12 row is treated as strong mutual validation —
     the same logic the parent pilot used to exempt its 94 agreement rows
     from adjudication.
   - **(c) Offset-magnitude distribution**: p50/p90/max of
     SuperPoint+LightGlue's `est_offset_m` across processed rows, compared
     descriptively against the trusted S3(Vexcel) population's own
     reference distribution at the PSR>=12 default gate (p50=0.96m,
     p90=3.81m — `DATA-gehi-displacement-audit-2026-07-06.md` §3). A
     wildly heavier tail on the weak-lock population is grounds for
     suspicion; a similar shape is grounds for plausibility. Neither is a
     bar.
   - **(d) Blinded Gemini validation**: a seed=0 subsample of up to 40
     processed probe rows whose `est_offset_m >= 2.0m` (all eligible rows
     if fewer than 40 qualify). Each item shows exactly **2** images,
     labeled ALIGNMENT A / ALIGNMENT B (no separate `unshifted` preamble
     image, unlike the re-adjudication's 3-image scheme — there is no
     `known` alternative candidate here, only "no correction" vs.
     "SuperPoint+LightGlue's correction"). One slot is the unshifted
     overlay (zero offset), the other is the SuperPoint+LightGlue-aligned
     overlay; which is A and which is B is randomized independently per
     item, `seed=0`, recorded in the output CSV, never disclosed to the
     judge. Same red/green ghosting overlay recipe and judge-prompt style
     as the re-adjudication (structure-edge focus, `neither` allowed).
     Verdict in `{A, B, neither}`. **Metric**: fraction of judged items
     where the SuperPoint+LightGlue-aligned slot is judged the better
     registration. Fail-closed: `neither` and abstain (after 2 retries)
     both count as NOT-better — the same convention the re-adjudication
     used for its real 43-row rule (rule 4 there). Rows with
     `est_offset_m < 2.0m` cannot be usefully judged this way (the two
     overlay images are near-identical to a human/model eye) — they form
     a separate **"near-zero correction"** class, reported by count only;
     for them, cross-validation (b) is the sole check.
5. **Judge competence gate** (run before the up-to-40 real items, on THIS
   session's fresh render): 10 synthetic controls, built exactly as the
   re-adjudication did — `seed=0` selection of 10 of the 94 agreement rows,
   `correct` slot = the row's own agreed `(known_dx_m, known_dy_m)`, `fake`
   slot = the same offset plus a synthetic 13m displacement in a
   `seed=0`-random direction (same `FAKE_OFFSET_M=13.0` as the
   re-adjudication, inside its observed `error_m p90=14.573m` range),
   3-image layout (`unshifted`, `A`, `B`), slot assignment randomized the
   same way. **>=8/10 required to proceed.** If the fresh draw reproduces
   the same 10 chip/date pairs as the earlier re-adjudication run (likely,
   since both draw `seed=0` from what is very probably the same 94-row
   agreement set), that is expected and is noted explicitly in the results
   section, not treated as a bug — the protocol calls for replicating the
   re-adjudication's exact method on this session's own fresh render, not
   for drawing a disjoint sample. **<8/10 -> skip the judge arm only**:
   report metrics (a)-(c), note the skip and the score, and stop — this
   does **not** abort the whole probe, since the judge is one of two
   validation signals here (alongside cross-validation), not the probe's
   verdict.
6. **Fail-closed rules**: judge parse-failure, missing/invalid
   `alignment_verdict`, or an HTTP/timeout error, after 2 retries (3
   attempts total) on the same item -> recorded `raw_verdict="abstain"`,
   counted as NOT-better / incorrect per rule 4/5 above. All raw Gemini
   responses are archived alongside the per-item CSVs. Gateway smoke test
   (`{"ok": true}` round-trip) runs first; if it fails, the judge arm is
   skipped entirely (metrics a-c still reported) and this is stated
   plainly, not silently retried indefinitely.
7. **Disclosure**: the protocol author (this session) has seen the parent
   pilot's and the re-adjudication's results and population sizes before
   writing this pre-registration. The 150 probe rows themselves, and the
   up-to-40 rows drawn from them for the judge arm, are untouched by any
   prior analysis — no peeking, no row-level knowledge, before this run.

## 2. Tooling

New script: `scripts/temporal/probe_weaklock_2026-07-10.py`. Reuses, via
`importlib` (the frozen source has a dash in its filename, matching the
established house pattern), the frozen `pilot_learned_match_2026-07-09.py`'s
population loader (`load_weak_lock_population`), matcher loader
(`load_matcher`), matcher self-check (`run_self_check`), matched-keypoint
extraction and RANSAC translation fit (`match_translation`), and the
ref/mov reprojection helpers (`build_ref_cache`, `find_anchor_dir`,
`find_mov_path`), plus `chip_displacement.reproject_to_grid` and
`audit_gehi_displacement.{CHIP_TARGETS,load_targets}` — none of these are
modified. Overlay rendering (red=ref/green=mov ghosting composite) and the
Gemini judge-call wrapper are new code in this script, following the same
validated pattern as `readjudicate_learned_match_gt_2026-07-09.py` (also
frozen — read for the pattern, not imported; its 3-image `{unshifted,A,B}`
layout is reused verbatim for the competence gate but does not fit this
probe's 2-image `{A,B}` real-item comparison, which needed a new prompt
variant). Calls the local Gemini gateway through the existing
`scripts/validation/gemini_solar_image_review.py` client helpers
(`load_env_file`, `env_value`, `post_native_generate_content`,
`extract_json_object`, `RateLimiter`) — imported, never modified.

Runs: mandatory self-check -> calibration re-sweep (no new matcher/Gemini
calls, pure recompute over the existing 137+43-row CSVs) -> 150-row
population pass -> gateway smoke test + 10-item competence gate -> (if
passed) up-to-40-item judge arm -> summary. Outputs to
`~/zasolar_data/geid_temporal/weaklock_probe_2026-07-10/`
(`self_check/`, `calibration_sweep.csv`, `probe_150_results.csv`,
`competence_gate_results.csv`, `judge_results.csv`, `overlays/`,
`summary.txt`). Nothing but this script and this memo lives in the repo —
no imagery, overlays, or raw Gemini responses are committed. A `--smoke`
debug flag (5 population rows, 2 judge items, `_smoke`-suffixed output
files, never touching the pre-registered filenames) exists purely to
sanity-check the pipeline end-to-end before the real run; it is not part
of the pre-registered protocol and its output is not used in any reported
number below.

**Frozen, not modified by this work**: `pilot_learned_match_2026-07-09.py`,
`readjudicate_learned_match_gt_2026-07-09.py`, `dino_coarse_match.py`,
`pilot_dino_rescue_2026-07-08.py`,
`diagnose_dino_positive_control_2026-07-08.py`, `chip_displacement.py`,
`chip_geometry.py`, `audit_gehi_displacement.py`,
`gemini_solar_image_review.py`.

## 3. Results

Run 2026-07-10, `git` commit `35ead0b` (pre-registration) precedes every
matcher/Gemini call in this section. Self-check reproduced the frozen
pilot's own numbers exactly (`(dy_px,dx_px) = (6.973, -4.022)` vs. known
`(7,-4)`, error 0.035px, `n_matches=1697 n_inliers=1695`) -> **PASS**.
Outputs: `~/zasolar_data/geid_temporal/weaklock_probe_2026-07-10/`
(`self_check/`, `calibration_sweep.csv`, `probe_150_results.csv`,
`competence_gate_results.csv`, `judge_results.csv`, `overlays/`,
`summary.txt`).

### 3.1 Calibration re-sweep (137-row positive control, corrected labels)

Corrected recovery (94 agreement + 15 `est`-correct of the 43): **109/137
(79.6%)**, matching the re-adjudication memo's own number exactly (cross-check
that the join between `pilot_results.csv` and `readjudication_43_results.csv`
is correct). Full sweep in `calibration_sweep.csv` (137 threshold rows). At
every `n_inliers` cut with coverage >=50%, precision under corrected labels
tops out at **86.96%** (`t=255`, n=69) — never reaches the 90% bar. At full
coverage (`t=1`), precision is 79.6% (the base rate, as expected). **No
threshold clears >=90% precision at >=50% coverage** -> per the
pre-registration, the "confident lock" cut is **not** defined by `n_inliers`
in this probe; `n_inliers`/`inlier_ratio` are reported descriptively only,
and the two independent validation signals (cross-validation, judge) below
carry the characterization.

### 3.2 Probe population (n=150, seed=0, `psr<12`)

**(a) Attempt/lock rate**: 150/150 (100%) rows had ref+mov successfully
built (zero missing-anchor / missing-Vexcel / missing-mov / reprojection
failures) and 150/150 (100%) produced a nonzero-match SuperPoint+LightGlue
fit. No data-availability problem on this population — unlike the
DINO-coarse line's earlier population gaps, tile/annotation coverage is not
the bottleneck here. Wall time: 71s for 150 rows (0.47s/row), consistent
with (slightly above, single-process cold-start amortized) the pilot's own
0.318s/row on the 137-row control.

**(b) Cross-validation vs. phase-correlation's own (untrusted) point
estimate**: **133/150 (88.7%)** of rows have
`hypot(est_dx_m-phasecorr_dx_m, est_dy_m-phasecorr_dy_m) <= ~5.6m`. Two
independent, individually-distrusted estimators agreeing this often on a
population phase-correlation itself flagged as low-confidence is a strong
signal — consistent with (not identical to, since this population's own PSR
gate differs) the parent pilot's exemption logic for its 94 agreement rows.

**(c) Offset-magnitude distribution**: `est_offset_m` p50=0.861m,
p90=2.879m, max=26.009m (n=150). The trusted S3(Vexcel) reference
population (psr>=12 gate) has p50=0.96m, p90=3.81m — the weak-lock probe's
central tendency (p50, p90) is **not** heavier than the trusted population's;
if anything slightly lighter at both quantiles. The tail is the one place
this population looks different: 2/150 rows exceed 10m, 1/150 exceeds 20m
(max 26.0m) — a small number of likely-genuine mismatches, not a
systematically heavier distribution.

**Near-zero-correction class**: 123/150 (82.0%) rows have
`est_offset_m < 2.0m` — SuperPoint+LightGlue estimates these chips need
little or no correction. Of these, 111/123 (90.2%) cross-validate against
phase-correlation's own near-zero-or-small estimate; 12/123 (9.8%) do not
(both estimators are individually noisiest at small true offsets, so some
disagreement here is expected rather than alarming).

**(d) Blinded Gemini judge — up to 40 items, `est_offset_m>=2.0m`**: only
**27/150 (18.0%)** rows qualified (fewer than the pre-registered 40 cap, so
all 27 were judged, per the pre-registration's "fewer if <40 eligible"
clause). **Competence gate: 8/10 -> PASS** — the fresh seed=0 draw from the
94 agreement rows reproduced the identical 10 chip/date pairs as the
2026-07-09 re-adjudication's own competence gate (verified: 10/10 overlap)
and scored identically (8/10, same 2 `neither`-miss items, same conservative
under-identification failure direction) — exactly the outcome flagged as
expected, not a bug, in the pre-registration (§1.5). **SuperPoint+LightGlue-aligned
judged better: 23/27 (85.2%)**. Category counts: `sp_lg`=23, `unshifted`=3,
`neither`=1, `abstain`=0. Judge confidence tightly banded (p25/p50/p75 =
0.70/0.70/0.75), same compressed range the re-adjudication observed. Of the
27 judged rows, 22/27 (81.5%) also cross-validate against phase-correlation
(metric b); 19/27 have **both** signals positive simultaneously.

### 3.3 Combined validation coverage

Treating a row as "validated" if it has cross-validation agreement (b) OR a
judge-confirmed better registration (d, only computable for the 27
judge-eligible rows) — **137/150 (91.3%)** of the sampled weak-lock rows
carry at least one positive independent signal. The remaining **13/150
(8.7%)** are "dark": 12 are near-zero-correction rows where SuperPoint+LightGlue's
small estimate does not cross-validate against phase-correlation's own
small estimate (plausible mutual noise at small offsets, not necessarily a
matcher failure), and 1 is a judge-eligible row the judge explicitly rejected
(`neither`/`unshifted`) — a genuine candidate miss.

## 4. Honest characterization

On this bounded 150-row sample of the 2,785-row PSR<12 weak-lock population,
SuperPoint+LightGlue attempts and locks every row (100%), and **91.3% (137/150)
of rows now carry at least one independent validation signal** that its
estimate is directionally trustworthy — either agreement with
phase-correlation's own (individually distrusted) point estimate, or a
blinded Gemini judge confirming the SuperPoint+LightGlue-aligned overlay
resolves structure-edge ghosting better than doing nothing. The offset
magnitudes it produces are not systematically larger than the trusted
population's own reference distribution at the center, though a small
(2/150) heavy tail exists. **What remains genuinely dark is a specific,
small slice: 8.7% (13/150) of rows, dominated by near-zero corrections that
don't cross-validate against phase-correlation** rather than large,
confidently-wrong estimates — there is no evidence in this probe of
SuperPoint+LightGlue systematically producing large, unvalidated,
confidently-wrong locks on this population. `n_inliers` itself, however,
is **not** a usable stand-alone confidence signal here (§3.1) — any future
use of this matcher on the weak-lock population needs the cross-validation
and/or judge signals, not a bare `n_inliers` cut, to separate trustworthy
rows from the dark 8.7%.

## 5. Recommendation (non-binding)

This is a characterization result, not a production decision — the options
below are candidates for a future issue, not a commitment made here:

- **Do not wire this into production as-is.** 91.3% validated-signal
  coverage on a 150-row bounded sample is encouraging but this probe
  deliberately used two *self*-referential validation signals (an untrusted
  estimator cross-check, and an LLM judge whose own competence gate tops out
  at 8/10) — neither is a substitute for held-out human-labeled GT at
  production scale.
- **If pursued further**: (a) a proper GT-labeled subsample of the
  weak-lock population (even 30-50 rows) would let this probe's
  cross-validation + judge signals be checked against real ground truth
  instead of each other; (b) the "dark" 12 near-zero-uncorroborated rows are
  a natural target for a small follow-up — are they genuinely near-zero
  offsets, or is one/both estimators failing quietly at small magnitudes;
  (c) Arm 0 / Arm 1 (widen phase-correlation's own search window;
  calibrate-correct the PSR 8-12 band) remain untested and orthogonal, per
  ISSUE-24's own out-of-scope note — still available to whoever picks up
  weak-lock recall work next, independent of this probe's outcome.
- **Not recommended**: further hyperparameter tuning of SuperPoint+LightGlue
  itself on this population — this probe found no evidence of a specific,
  fixable failure mode (like the phase-correlation-aliasing bug the parent
  pilot found) that would motivate it; the 8.7% dark slice looks like
  ordinary estimator noise at small true offsets, not a bug.
