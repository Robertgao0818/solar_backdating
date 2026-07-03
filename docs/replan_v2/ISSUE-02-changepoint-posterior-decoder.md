# ISSUE-02: Monotone changepoint posterior decoder

Status: ready-for-agent
Phase: 0 — Estimator
Blocked by: ISSUE-01

## Parent

[`../install_date_optimization_v2_prd.md`](../install_date_optimization_v2_prd.md) — D2, D3. User stories 1, 2, 3, 5.

## What to build

The lead estimator behind the ISSUE-01 seam: an **exact monotone-changepoint
posterior** per anchor. Latent absent→present step; changepoint τ ranges over
observed epochs plus a right-censored "beyond window" cell (so "undated"
becomes P(τ > T), not a status). Exact O(T) enumeration — no HMM machinery.
Emissions = 3-symbol confusion matrix (present/absent/abstain | state)
estimated cohort-wide by EM, stratified by quality flag, zoom, and imagery
era. Near-duplicate vintages collapse into epochs before decoding (median gap
~31 d; ~42% of gaps ≤30 d). Flat prior in this issue; the empirical-Bayes
cohort prior plugs in via ISSUE-03. Must consume production scan states and
the banked panel's frame-verdict table unchanged.

This decoder subsumes dip-repair, the status case machine, and the sustained
heuristic — sustained is its MAP under symmetric noise + flat prior, so it
must strictly generalize the incumbent.

## Acceptance criteria

- [ ] D3 gates on the banked panel: MAP mode-hit ≥ 0.911 and undated-flip ≤ 0.046
- [ ] Dominant-stratum year-stability ≥ the naive first-present estimator (the sustained failure mode is fixed, not inherited)
- [ ] HPD calibration proxy ≈ nominal (rep-i's 90% interval contains rep-j's MAP ~90%)
- [ ] Year-histogram TVD within the established 0.037–0.063 band
- [ ] Beats the PAVA floor on the same harness (else the floor falsifies the added machinery)
- [ ] Deterministic given verdicts; consumes production scan states without modification

## Blocked by

- ISSUE-01 (seam + harness)
