# Data memo: pre-registered bounded pilot for learned feature matching (SuperPoint+LightGlue / LoFTR) — 2026-07-09

Status: pre-registered, results pending.

Parent: [`ISSUE-24-learned-feature-matching.md`](ISSUE-24-learned-feature-matching.md),
which itself follows the bounded-pilot discipline established by
[`DATA-dino-coarse-bounded-kill-2026-07-08.md`](DATA-dino-coarse-bounded-kill-2026-07-08.md)
(the DINO-coarse dense-patch-token line, closed KILL at 39.4% recovered on
the same positive control this pilot reuses).

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

## Sections to complete after running (kept empty until results land)

1. Model/resolution identification
2. Cost profile (measured, not estimated)
3. Self-check (synthetic known-shift sign check)
4. Results (n=137)
5. Kill criterion applied — explicit GO/KILL verdict
6. Weak-lock probe (only if GO)
7. Directional findings
