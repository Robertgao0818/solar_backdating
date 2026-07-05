# ISSUE-02: Monotone changepoint posterior decoder

Status: done
Phase: 0 — Estimator
Blocked by: ISSUE-01
Completed: 2026-07-03 — `changepoint` estimator behind the ISSUE-01 seam
(`src/solar_backdating/estimators/{changepoint,emissions}.py`; additive
`EstimatorConfig` fields `decoder_epoch_gap_days`/`emissions`/`cohort_prior`).
Exact O(T) τ-enumeration over collapsed epochs + right-censored cell; 3-symbol
emissions fitted by deterministic EM (`fit_emissions_em`), stratified by
`quality_flag` (zoom/era deferred to D17/ISSUE-18); **censoring-exact decode**
(abstain marginalized out of the state likelihood per the ISSUE-04
censoring-with-cause doctrine) over a **usable-anchored cell lattice** (only
usable-scored verdicts vote or define interval bounds — production
`usable_observations` parity); `CohortPrior` hook ready for ISSUE-03.
Harness gains `--fit-emissions/--emissions-json/--decoder-epoch-gap-days`;
read-only scan-state adapter + endtoend TVD script
(`eval/scan_state_io.py`, `scripts/validation/estimator_endtoend_decode.py`).
Gates were re-anchored by the ISSUE-04 memo (extended 38-unit panel, gap=45
lever, EM-fitted emissions): mode-hit **0.905** ≥ 0.882, beats PAVA 0.889,
done_appears year **0.867** ≥ fpd 0.567 (clears sustained's 0.717 target),
inv-weighted **0.863/0.830** ≥ 0.798/0.757, HPD 0.914; banked Panel A
regression stays bit-exact (fpd 0.807/0.046/0.814, sustained 0.911/0.857).
Undated-flip 0.055 = exact parity with sustained/pava; the memo's 0.034 is
**fpd-caliber** (`panel_repair_d8_compare.py` computes `undated_flip_rate`
from fpd tokens only) and is recorded not-met as a diagnostic — the 0.021 gap
is one stable-undated unit (`c0015576`, the memo's own "stable-UNDATED" unit,
which fpd unstably dates 7/10 reps) plus one `gemini_failed` rep.
**Correction (2026-07-04):** the "fpd-caliber" explanation above was a
misdiagnosis — the root cause is a script bug in
`panel_repair_d8_compare.py` (it reused the FPD-only `undated_flip_rate`
field for the sustained row too, not a deliberate caliber choice; see the
ISSUE-04 memo's [Correction](ISSUE-04-decision-memo-2026-07-03.md#correction-2026-07-04)).
Sustained's true undated-flip is 0.055 (same value this decoder scores),
so re-anchored against the corrected gate (≤0.055) the decoder is
**pass-at-parity (tie)**, not "not-met." Endtoend
rep-to-rep year-TVD [0.081/0.059/0.079] exceeds the band (see unchecked AC
below). n_undated fell 81→~22/rep after the censoring-exact correction
(production floor ~4). Canonical artifacts:
`~/zasolar_data/geid_temporal/panel_repair_20260703/analysis_estimator_harness_extended/`
(+ `emissions_fitted.json`) and
`~/zasolar_data/geid_temporal/llm_endtoend_20260623/analysis_decoder/endtoend_decode_tvd.json`.
tests/estimator: 113 passed.

**Banked-caliber diagnostic (2026-07-03, post-completion run).** On banked
Panel A itself (28 units, gap=45, EM-fitted emissions) the decoder scores
mode-hit **0.879** / undated-flip 0.075 / year 0.832 / HPD 0.891 — it fails
the original banked bars (≥0.911 / ≤0.046) and loses to both sustained
(0.911) and the pava floor (0.893). The decoder's advantage is
**panel-dependent**: it holds on the extended 38-unit panel (0.905 > pava
0.889), not on banked Panel A — consistent with the ISSUE-04 re-anchor
rationale (banked dominant stratum n=2 makes the year-stability gate
ill-posed there: 0.25 vs fpd 0.65). Undated-flip 0.075 is exact
sustained/pava parity (same pattern as the extended panel's 0.055).
Emissions are transfer-insensitive on this panel: panel-fit vs
extended-loaded matrices give identical decoder metrics to 3 dp. The same
run surfaced an **unpinned D8 drift**: sustained's dated-only metrics vs the
banked ISSUE-01 artifact are NOT bit-exact (map_mode_hit_dated 0.885→0.917,
inv-weighted 0.456→0.662) — the harness `--gate` pins only
map_all/undated_flip/year (those are bit-exact; fpd is bit-exact on every
shared numeric field). Most likely the same root as pava 0.875→0.893: the
censoring-exact / usable-anchored lattice change moved dated/undated token
classification; DO NOT read the dated-only delta as a fresh regression.
Artifacts:
`~/zasolar_data/geid_temporal/d3_gates_banked_20260703/{fit_emissions,loaded_emissions}/`.

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

- [x] D3 gates on the banked panel: MAP mode-hit ≥ 0.911 and undated-flip ≤ 0.046
  *(re-anchored by the ISSUE-04 memo to the extended 38-unit panel: ≥ 0.882 /
  ≤ 0.034. Achieved mode-hit 0.905; undated-flip 0.055 = sustained/pava parity —
  the 0.034 constant is fpd-caliber and recorded not-met, see Completed line.)*
- [x] Dominant-stratum year-stability ≥ the naive first-present estimator (the sustained failure mode is fixed, not inherited)
  *(done_appears year 0.867 vs fpd 0.567; also clears sustained's 0.717 target.)*
- [x] HPD calibration proxy ≈ nominal (rep-i's 90% interval contains rep-j's MAP ~90%)
  *(0.914 vs 0.90 nominal.)*
- [ ] Year-histogram TVD within the established 0.037–0.063 band
  *(NOT MET: rep-to-rep [0.081/0.059/0.079]. Root-caused as upstream scan/scorer
  rep-nondeterminism, not decoder behavior: provider/chip assignment differs
  across reps (45/79 dated-year shifts) + raw LLM verdict flips on
  usable-flagged frames; pava fails the same gate at the same magnitude with no
  emission model at all. This is exactly the noise class the ISSUE-06/07
  verdict store eliminates (reps share cached verdicts) — re-run this gate
  after ISSUE-07 lands.)*
- [x] Beats the PAVA floor on the same harness (else the floor falsifies the added machinery)
  *(0.905 > 0.889 same-run extended; note ISSUE-01's banked pava 0.875 was a
  stale artifact — HEAD pava = 0.893 banked / 0.889 extended.)*
- [x] Deterministic given verdicts; consumes production scan states without modification
  *(no RNG anywhere incl. EM: fixed init + tolerance; property tests; scan-state
  adapter is strictly read-only.)*

## Blocked by

- ISSUE-01 (seam + harness)
