# ISSUE-03: Turnbull survival prior + posterior-mass aggregation

Status: done
Phase: 0 — Estimator
Blocked by: ISSUE-01 (integration AC additionally needs ISSUE-02)
Completed: 2026-07-04 — cohort layer shipped as three modules + one gate runner:
`src/solar_backdating/estimators/survival.py` (deterministic Turnbull NPMLE
self-consistency EM over calendar-year atoms — year atoms because the frozen
decoder scores cells at year granularity via `_year_day_fractions`; Cromwell
floor `(1-λ)m_y + λ/K` so no −inf inside the window; `beyond_log_mass` set by
explicit `beyond_policy` (default `terminal_year`), never `log(beyond_mass≈0)`),
`eval/cohort.py` (every terminal status → `CensoringInterval`; ambiguous family
recovered via maximal-usable-absent-prefix rule, intervals can never invert;
reproduces `infer_one` bounds incl. clamp_inverted / marker_missed_pv
reclassification), `eval/aggregate.py` (three deliberately separate cohort-year
channels: point / midpoint / fractional posterior-mass; survival curves; TVD on
the repo-wide `metrics.tvd` ruler), `scripts/validation/issue03_gates.py`
(all seven ACs, one consolidated JSON), additive `--cohort-prior-json` on
`estimator_harness.py` / `estimator_endtoend_decode.py` (absent ⇒ flat ⇒
banked regression bit-exact — re-verified with the prior injected: fpd
0.807/0.046/0.814, sustained 0.911/0.857 exact). EB prior fit ONCE on the
15,859-state production cohort (`jhb_full382_fpcut_scan_2026-06-02`), 40 EM
iters, final_loglik −18659.79, empirical beyond_mass=0.

**Headline: the EB prior IMPROVES the decoder** — extended-panel mode-hit
0.905 → **0.942** (+3.7pp), inv-weighted 0.863 → **0.923** (+6.0pp), year
0.874→0.897, HPD 0.914→0.937, done_appears year 0.867→0.900, undated-flip
0.055 unchanged (prior sharpens dated MAP without disturbing the
dated/undated boundary). Robust across an 18-config hyperparameter sweep
(λ∈{0.02,0.05,0.1} × 3 beyond-policies × 2 temperatures: 0.942 everywhere,
one 0.939 dip). AC5 gate: survival-curve rep-to-rep TVD [0.0504/0.0596/0.0304]
mean **0.0468** vs same-run point-date year TVD [0.090/0.0314/0.0875] mean
0.0696 — beats on mean + 2/3 reps (~33% more rep-stable; both bands sit below
the ISSUE-02 pre-verdict-store [0.081/0.059/0.079]).

Disclosed caveats (none gate-blocking): (C1) AC4 `unknown`-cadence sub-fit
(n=1,837, median interval width 963 d) non-converged in isolation —
diagnostic-only, low-confidence curve; main AC2 fit converged. (C2) recovered
count 5,514 = **34.8%** of cohort (4,681 natively-ambiguous 29.5% + 651
clamp_inverted + 182 marker_missed_pv reclassifications), above the spec's
~23% row-count framing; detector-FP contamination of the recovered stratum is
the load-bearing downstream audit risk. (C3) AC5 rep2 orders the other way
(survival 0.0596 > point 0.0314) — rep2's point TVD is anomalously LOW
(upstream L2-search draw, same rep is lowest in the ISSUE-02 band), not a
survival regression. (C4) PAVA HPD 0.941 marginally exceeds EB 0.937 (both
≫0.88); EB wins the primary judges (+5.3pp mode-hit vs PAVA). (C5) the
prior's 2024 mass dip (0.0135 « 2023 0.656) is a Vexcel flight-date
right-censoring artifact — DO NOT read it as a market signal. AC4 bias note:
JHB-only cohort makes the region axis degenerate; per-grid median cadence
spans 30.5–624.5 d across 335 grids, and cadence-stratified S(2021) spreads
0.081 (≤90 d) vs 0.908 (>365 d) — censoring is only conditionally
non-informative given grid; read cohort curves as grid-marginalised. AC6:
dropping the recovered stratum shifts the curve by TVD 0.195/0.210/0.232
(rep1 S(2022) 0.534→0.730 recovered-in) — an order of magnitude above
rep-to-rep noise, so the recovery mandate is load-bearing. AC3: midpoint
imputation vs posterior mass moves L1 18.50/14.76/17.90 install-units per
rep; midpoint over-piles 2021–2023 and zeroes both the 2024–2025 tail and
the 2009–2017 early-adopter tail that posterior mass restores.

Artifacts: `~/zasolar_data/geid_temporal/issue03_gates_20260704/`
(`issue03_gates.json`, `cohort_prior.json`, `intervals_{prod,rep1,rep2,rep3}.csv`,
`report.md`, `harness_panelA_gate/`). tests/estimator: 180 passed (full
suite 799).

## Parent

[`../install_date_optimization_v2_prd.md`](../install_date_optimization_v2_prd.md) — D2 (prior + aggregation), D3. User stories 2, 4.

## What to build

The cohort layer: map **every** anchor — including ambiguous terminal
statuses — to a censoring interval (appears → (latest absent, earliest
present]; already-present → left-censored; ambiguous/no-recent → (last
confident absent, clamp flight date]), then fit a **Turnbull NPMLE /
discrete-time hazard** over the cohort. The fitted hazard becomes the
empirical-Bayes prior for the ISSUE-02 decoder. Grid-cell aggregation switches
from midpoint imputation to **fractional counting of posterior mass** across
vintage gaps.

Concrete win this issue must deliver: the ~23% of anchors with ambiguous
terminal statuses stop being dropped rows and become censored observations
contributing to aggregate curves.

## Acceptance criteria

- [x] All retained anchors mapped to censoring intervals (ambiguous strata recovered, count reported)
  *(15,859/15,859 prod anchors mapped, 0 dropped; 5,514 recovered = 34.8%, see C2.)*
- [x] Turnbull fit converges; hazard exported in a form the decoder consumes as prior
  *(40 iters, loglik −18659.79; exported as `CohortPrior(year_log_mass, beyond_log_mass)` JSON.)*
- [x] Cell-level expected counts via posterior mass; comparison vs midpoint imputation reported
  *(L1 shift 18.50/14.76/17.90 install-units per rep; midpoint zeroes both tails.)*
- [x] Non-informative-censoring check: cadence/region stratification or covariate, with a bias note where cadence correlates with geography
  *(region axis degenerate JHB-only; cadence↔grid correlation strong — conditional non-informativeness only; `unknown` sub-fit non-converged, diagnostic-only.)*
- [x] Gate: cohort survival-curve rep-to-rep TVD (3 end-to-end reps) beats point-date year TVD
  *(0.0468 vs 0.0696 mean, 2/3 reps; rep2 flip is upstream rep noise, see C3.)*
- [x] Sensitivity report: curves with vs without the recovered ambiguous anchors
  *(TVD 0.195/0.210/0.232 — recovery is load-bearing; marker_missed_pv sub-axis ~0.005.)*
- [x] Integration (blocked by ISSUE-02): decoder with EB prior re-passes all D3 gates
  *(all 7 gate booleans PASS; mode-hit 0.942 > 0.882 bar and > PAVA 0.889; banked Panel A baselines bit-exact with prior injected.)*

## Blocked by

- ISSUE-01 (harness); final integration AC blocked by ISSUE-02
