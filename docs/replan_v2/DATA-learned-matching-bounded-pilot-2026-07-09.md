# Data memo: pre-registered bounded pilot for learned feature matching (SuperPoint+LightGlue / LoFTR) — KILL (2026-07-09)

Status: final verdict. **KILL — neither arm clears the pre-registered 70% bar**
on the 137-row positive control. Best-tested arm (SuperPoint+LightGlue,
68.6%) is a large improvement over the DINO-coarse precedent's 39.4% ceiling
(+29.2pp) but falls short of the bar by 1.4pp (2 rows). LoFTR (arm 2, run
because arm 1 failed) does worse (60.6%). Weak-lock probe (Step 6) not run —
gated behind a GO this pilot did not reach.

Parent: [`ISSUE-24-learned-feature-matching.md`](ISSUE-24-learned-feature-matching.md),
which itself follows the bounded-pilot discipline established by
[`DATA-dino-coarse-bounded-kill-2026-07-08.md`](DATA-dino-coarse-bounded-kill-2026-07-08.md)
(the DINO-coarse dense-patch-token line, closed KILL at 39.4% recovered on
the same positive control this pilot reuses).

**Correction (2026-07-10): this verdict is OVERTURNED, KILL → GO.**
Post-verdict diagnostics found that 30 of the 43 failure rows are
"zero-pinned" (SuperPoint+LightGlue estimates a near-zero offset while this
memo's own positive-control GT — phase-correlation's `≥5m`-filtered
"known" offset — claims a large one). A disclosed human peek at 10 sampled
zero-pinned rows found the near-zero estimate was often the alignment that
actually registered the buildings, and phase-correlation's own GT was the
misaligned one (aliasing on repetitive rowhouse fabric, which the `≥5m`
population filter selectively enriches for) — a broken-instrument finding
about the referee, not an ablation of SuperPoint+LightGlue. A pre-registered,
blinded Gemini-judge re-adjudication of all 43 disagreement rows (gated
behind a synthetic ground-truth competence check and a human-consistency
check) found 15/43 rows est-correct, correcting recovery to 109/137 (79.6%,
above the 70% bar). Full protocol and results:
[`DATA-learned-matching-gt-readjudication-2026-07-09.md`](DATA-learned-matching-gt-readjudication-2026-07-09.md).
This memo's body below is left unmodified as the original record of the
(now-superseded) KILL verdict and its reasoning at the time.

## Pre-registration (fixed before any matcher run)

- **Population**: `ref_kind=="S3_vexcel" & psr>=12 & best_offset_m>=5.0` from
  `/home/gaosh/zasolar_data/geid_temporal/gehi_displacement_audit_2026-07-06/per_chipdate_offsets.csv`.
  n=137 (verified by direct count against the CSV, 2026-07-09 — unchanged
  from the DINO sessions 2–3 population). Same rows, same file, same filter
  as the DINO-coarse precedent — this pilot benchmarks head-to-head against
  the 39.4% DINO ceiling on identical rows, not a resampled or re-filtered
  set.
- **Primary recovered definition** (row-by-row comparable to the DINO
  baseline): `error_m <= 1.5 * cell_diag_m`, where `cell_diag_m =
  hypot(extent_y_m/37, extent_x_m/37)` and `extent_y_m`/`extent_x_m` are the
  per-row chip's UTM grid extent at GSD 0.15 m (`utm_grid_for_bounds` /
  `reproject_to_grid`, the same grid `chip_displacement`/`dino_coarse_match`
  operate on). For the population's near-constant ~97.5–97.8 m chip extent
  this lands at `cell_diag_m ≈ 3.733 m`, tolerance `≈ 5.599 m` — identical to
  the DINO baseline's own 37×37/518px tier (see the DINO memo §1). Computed
  per-row from the actual reprojected grid shape, not assumed constant.
  `error_m = hypot(est_dx_m - known_dx_m, est_dy_m - known_dy_m)` against the
  CSV's `(dx_m, dy_m)` ground truth, same convention as the DINO diagnostic
  (`ref=vexcel_crop`, `mov=gehi_vintage_chip`; see `register()` in
  `audit_gehi_displacement.py`).
- **Secondary tolerances** (report only, never judged against the kill bar):
  `error_m <= 2.59 m` (the DINO "finer" tier) and `error_m <= 1.0 m`.
- **Kill bar (binary, fixed before running)**: **KILL if primary recovery
  < 70% on the full 137 rows. GO requires ≥ 70%.** Same lower edge as the
  DINO precedent's 70–80% bar, per ISSUE-24's default ("reuse 70–80%
  recovery ... deviate only with explicit justification" — no deviation
  taken here).
- **Confidence signal**: RANSAC inlier count and inlier ratio (inliers /
  total LightGlue matches) per row. Reported as a distribution (quartiles),
  split recovered-vs-not, alongside recovery — never recovery alone. This is
  the PSR-analog value proposition of a learned matcher over DINO-coarse
  (which had no natural per-row confidence signal beyond the raw similarity
  score).
- **Arm order**: SuperPoint+LightGlue first (lighter weight, natural
  inlier-count confidence signal). LoFTR (kornia-native, already installed)
  is arm 2, run **only if arm 1 fails the 70% bar** — per ISSUE-24 §1, not
  run "for completeness" if arm 1 clears it.
- **Environment deviation already taken, recorded here in advance**:
  `lightglue` is not on PyPI; installed via
  `pip install --no-deps git+https://github.com/cvg/LightGlue.git` into the
  shared venv (`--no-deps` to avoid touching the pinned torch/kornia/numpy
  versions already in the venv — LightGlue's own listed deps are already
  satisfied by the existing install). This ships both `SuperPoint` (the
  extractor arm 1 needs) and `LightGlue` (the matcher), matching the ISSUE-24
  brief's primary path — the kornia-DISK+LightGlueMatcher fallback was not
  needed.
- **Weak-lock probe (only if GO)**: bounded random subsample, seed=0, of
  ~150 rows from `ref_kind=="S3_vexcel" & psr<12` (2,785-row population,
  NOT a full run). No trusted GT for these rows — reported as a
  confident-lock rate at an inlier-count threshold calibrated on the
  positive control (≥95% precision among recovered positive-control rows),
  plus an offset-magnitude sanity check. Explicitly not a production
  commitment.

## 1. Model / environment identification

- SuperPoint + LightGlue: `pip install --no-deps git+https://github.com/cvg/LightGlue.git`
  (not on PyPI, per ISSUE-24's environment note), resolved to commit
  `eb42fee2d71449efb0aa5c10549752b5d75384d8`, installed version tag `0.0`.
  `--no-deps` used because the venv's pinned torch/kornia/numpy already
  satisfy LightGlue's requirements — installing deps would have risked
  clobbering those pins; no clobber occurred (verified: `torch==2.10.0+cu128`,
  `kornia==0.8.2` unchanged after install). Weights: `superpoint_v1.pth`
  (4.96MB) + `superpoint_lightglue_v0-1_arxiv.pth` (45.3MB), fetched from the
  LightGlue GitHub release on first run, cached under
  `$ZASOLAR_ROOT/.cache/torch/hub/checkpoints/` (gitignored).
- LoFTR: kornia-native (`kornia.feature.LoFTR(pretrained="outdoor")`),
  already installed (`kornia==0.8.2`) — no new package. Weights
  `loftr_outdoor.ckpt` (44.2MB) fetched from the kornia-registered mirror on
  first run, cached the same way.
- Both matchers run at native chip resolution (~650×650px, no resize) — the
  ref and mov images are already co-gridded onto the identical UTM
  transform/shape by `chip_displacement.reproject_to_grid`
  (GSD 0.15m), so a keypoint pixel coordinate means the same physical
  location in both images; no separate scale-factor bookkeeping is needed,
  unlike the DINO line's `input_size`→`cell_m` conversion.
- SuperPoint keypoints are `(x, y)` = `(col, row)`; LoFTR's `keypoints0`/
  `keypoints1` are the same convention. `mov_kp − ref_kp` for a matched pair
  is therefore already `(dx_px, dy_px)` in the "mov's content sits
  right/below ref's content" convention `chip_displacement.ShiftResult` uses
  — no negation needed (unlike `skimage.phase_cross_correlation`, which the
  DINO/phase-correlation line needed to negate). Confirmed empirically, not
  just by reading the source (§3).
- RANSAC: exhaustive (or, above 3,000 matches, seed=0 fixed-subsample) 1-point
  consensus over per-match displacement vectors — every match's own
  displacement is a translation hypothesis (a pure-translation model needs
  exactly one correspondence), the hypothesis with the largest support within
  `--ransac-thresh-px` (default 5.0px ≈ 0.75m) wins, final estimate is the
  mean of its inlier set. Deterministic given the match set — chosen over
  `cv2.estimateAffinePartial2D` specifically to avoid fitting rotation/scale
  degrees of freedom the task doesn't have (same-tile-snapped-bbox content
  misregistration is a pure translation, per the audit's own framing).

## 2. Cost profile (measured, not estimated)

15-row `--limit --seed 0` smoke on each arm before committing to the full 137:

| arm | smoke (15 rows) | s/row | peak GPU mem | full-137 wall time |
|---|---:|---:|---:|---:|
| SuperPoint+LightGlue | 4.8s | 0.318s | 610MB | 31s (measured) |
| LoFTR | 7.5s | 0.500s | 1457MB | 47s (measured) |

Both arms comfortably clear the <30min sanity gate (by two orders of
magnitude) — SuperPoint+LightGlue is ~30× faster than the DINO-coarse
baseline's heaviest arm (DINOv3-L/16@1280, 579s/137 rows) despite doing
strictly more work per pair (keypoint detection + attention matching vs a
single ViT forward + integer-shift argmax). Peak GPU memory on the 8GB
laptop GPU: 610MB (SuperPoint+LightGlue) / 1457MB (LoFTR) — no OOM risk at
any point in this pilot.

## 3. Self-check (mandatory before any real pair)

**SuperPoint+LightGlue: PASS**, cleanly. Real Vexcel-crop ref image,
`mov = np.roll(ref, shift=(dy=7, dx=-4))`. Estimated `(dy_px, dx_px) =
(6.973, -4.022)`, error 0.035px against a 2.0px tolerance, 1695/1697
matches RANSAC-inlier (99.9%). Confirms the full pipeline's sign convention
(keypoint ordering, match indexing, RANSAC output) end-to-end before
touching real data.

**LoFTR: soft-FAIL on the strict bar, resolved by follow-up, not a sign bug.**
Same check: estimated `(7.936, -2.013)` vs known `(7, -4)`, error 2.196px —
just over the 2.0px tolerance (both signs correct; only the dx magnitude
undershoots). Per ISSUE-24's own discipline ("the DINO line burned a whole
session on instrument doubt — do not skip this... if the sign is inverted,
fix the estimator, not the comparison"), this was investigated before
proceeding, not waved through or treated as a blocking failure:
- Repeated on a **non-circular** synthetic shift (edge-pad + crop, avoiding
  `np.roll`'s wraparound seam — LoFTR's dense coarse-to-fine matching could
  plausibly be more sensitive to a discontinuity artifact than SuperPoint's
  sparse corners): error still 1.860px. Rules out the wraparound seam as the
  cause.
- Repeated across 4 independent shifts of varying magnitude/direction
  (`(7,-4)`, `(20,-15)`, `(40,30)`, `(-25,18)`), non-circular, real Vexcel
  content, both arms side by side:

  | shift (dy,dx) | SuperPoint+LightGlue error_px | LoFTR error_px |
  |---|---:|---:|
  | (7,-4) | 0.040 | 1.860 |
  | (20,-15) | 0.039 | 1.035 |
  | (40,30) | 0.028 | 1.784 |
  | (-25,18) | 0.006 | 2.400 |

  SuperPoint+LightGlue is consistently sub-0.05px across all four. LoFTR
  shows a **fixed ~1-2.4px noise floor that does not grow with shift
  magnitude** (2.4px error on a 25-30px shift is the same order as on a 7-8px
  shift) — the signature of a coarse-to-fine architecture's inherent
  sub-pixel localization ceiling (LoFTR's coarse stage matches on a
  downsampled grid, typically 1/8 resolution, before local fine-refinement),
  not a convention/sign bug. **Verdict: LoFTR's estimator is directionally
  and conventionally correct; its self-check "failure" is a real but
  task-irrelevant precision characteristic** — the positive control's
  recovery tolerance is ~37px (5.6m at GSD 0.15m), two orders of magnitude
  above this noise floor. Proceeded to the real 137-row run on this basis.

## 4. Results (n=137, both arms; `recovered` = `error_m <= 1.5*cell_diag_m` per the pre-registration, ≈5.6m for this population's ~97.5-97.8m chip extent)

| arm | primary recovered | secondary ≤2.59m | secondary ≤1.0m | error_m p50 / p90 |
|---|---:|---:|---:|---:|
| DINO-coarse best (precedent, session 2+3) | 39.4% (54/137) | — | — | — |
| **SuperPoint+LightGlue** | **68.6% (94/137)** | 58.4% (80/137) | 52.6% (72/137) | 0.829 / 14.573 |
| LoFTR | 60.6% (83/137) | 48.2% (66/137) | 40.1% (55/137) | 3.283 / 14.689 |

By area bucket (recovered rate, SuperPoint+LightGlue): a_xs(<15m²) 55.6%
(n=36), b_sm(15-40) 54.8% (n=31), c_md(40-100) 76.9% (n=26), d_lg(≥100)
84.1% (n=44) — recovery rises with footprint size, same directional pattern
the DINO memo found (§5 of the precedent), consistent with small
installations offering fewer/weaker distinguishing keypoints for either
matcher family.

Inlier-count distribution (SuperPoint+LightGlue), split recovered vs not:
recovered rows have a higher median `n_inliers` (275 vs 237) and a heavier
right tail, but the separation is soft, not a clean bimodal split — a
naive `n_inliers` cut trades precision for coverage roughly linearly rather
than finding a sharp "confident lock" cliff (full sweep in
`full137/pilot_summary.txt`; e.g. `t=320` reaches 100% precision but only
33% coverage on the 15-row smoke, and no cut on the full 137 clears 95%
precision at >3% coverage — see the full sweep table in the run output).
This is a real, if modest, PSR-analog signal (unlike DINO-coarse, which had
none), just not a strong one on this population.

Outputs:
`~/zasolar_data/geid_temporal/pilot_learned_match_2026-07-09/{self_check,self_check_loftr,smoke15,full137,loftr_smoke15,loftr_full137}/{pilot_results.csv,pilot_summary.txt,self_check.txt}`.

## 5. Kill criterion applied

Pre-registered bar: **GO requires ≥70% primary recovery on the full 137
rows; KILL if <70%.** SuperPoint+LightGlue: 68.6% < 70% → **KILL** for arm 1,
triggering arm 2 (LoFTR) per the pre-registered arm order. LoFTR: 60.6% <
70% → **KILL**, and worse than arm 1. **Best-tested configuration across
both arms: SuperPoint+LightGlue at 68.6%, still below the bar.**

**Overall verdict: KILL.** Neither arm reaches the pre-registered 70% floor.
This is explicit and binary per the pre-registration — 68.6% is 1.4
percentage points (2 rows) short, and the pre-registration was written
precisely to prevent treating a near-miss as a pass after the fact. The
Step 6 weak-lock probe (bounded PSR<12 subsample) is gated behind a GO this
pilot did not reach, and was not run.

## 6. Weak-lock probe

Not run — gated behind GO (acceptance criteria / ISSUE-24 §4), not reached.

## 7. Directional findings (diagnostic value, not further scope)

- **The mechanism hypothesis in ISSUE-24 was directionally right**: explicit
  sparse/dense keypoint correspondence + robust translation fit clears
  DINO-coarse's dense-token-argmax ceiling by a wide margin (68.6% vs
  39.4%, +29.2pp) using the SAME positive-control rows and the SAME
  recovery tolerance. This is real signal, not noise — it just isn't enough
  signal to clear the pre-registered bar.
- **SuperPoint+LightGlue clearly beats LoFTR on this task** (68.6% vs
  60.6%, and a much tighter error_m p50: 0.829m vs 3.283m). This matches the
  self-check finding: SuperPoint's sparse-detector + attention-matcher
  pipeline localizes far more precisely than LoFTR's coarse-to-fine dense
  matching for this small-chip, small-shift regime. LoFTR's extra compute
  (1.6× the time, 2.4× the peak memory) bought no benefit here — the
  ISSUE-24 gating logic (only pay for LoFTR if the cheaper arm falls short)
  was the right call operationally, even though in this case the cheaper
  arm was already the better one.
- **The confidence signal (`n_inliers`) is real but soft**, unlike
  DINO-coarse which had no useable per-row confidence signal at all. This is
  the one piece of ISSUE-24's value proposition that held up even under the
  overall KILL — worth keeping in mind if a future issue revisits
  registration confidence scoring, but not itself grounds to relitigate this
  verdict (a soft confidence signal on a method that misses the bar doesn't
  change the bar).
- **What is explicitly NOT tested and NOT concluded here, staying in scope
  discipline**: no RANSAC-threshold sweep, no `max_num_keypoints` increase,
  no alternative SuperPoint/LightGlue confidence-filtering tuning was tried
  after seeing the 68.6% number — per the same "no further ablation without
  a new hypothesis" discipline the DINO memo closed on. 68.6% being close to
  70% is a fact worth recording, not an invitation to keep turning
  hyperparameter dials until it crosses; the bar was fixed precisely to
  prevent that. A genuinely new hypothesis (not "try a bigger
  `max_num_keypoints`" or "loosen RANSAC threshold" — both minor variants of
  what was just run) would be needed to reopen this line.
- **Arm 0 / Arm 1 (widen phase-correlation's search window; calibrate-correct
  the PSR 8-12 band) remain untested**, per the DINO memo's carried-forward
  item and ISSUE-24's explicit out-of-scope note — unaffected by this
  verdict, still the next candidate for anyone picking up weak-lock recall
  work.
