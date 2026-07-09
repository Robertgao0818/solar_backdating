# Data memo: Step-6 weak-lock characterization probe (SuperPoint+LightGlue) (2026-07-10)

Status: pre-registered, not yet run.

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

_Filled in after the run._

## 4. Honest characterization

_Filled in after the run._

## 5. Recommendation (non-binding)

_Filled in after the run — explicitly not a production commitment; any
next step listed here is a candidate for a future issue, not a decision
made by this memo._
