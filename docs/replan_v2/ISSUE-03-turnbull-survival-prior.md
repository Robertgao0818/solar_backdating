# ISSUE-03: Turnbull survival prior + posterior-mass aggregation

Status: ready-for-agent
Phase: 0 — Estimator
Blocked by: ISSUE-01 (integration AC additionally needs ISSUE-02)

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

- [ ] All retained anchors mapped to censoring intervals (ambiguous strata recovered, count reported)
- [ ] Turnbull fit converges; hazard exported in a form the decoder consumes as prior
- [ ] Cell-level expected counts via posterior mass; comparison vs midpoint imputation reported
- [ ] Non-informative-censoring check: cadence/region stratification or covariate, with a bias note where cadence correlates with geography
- [ ] Gate: cohort survival-curve rep-to-rep TVD (3 end-to-end reps) beats point-date year TVD
- [ ] Sensitivity report: curves with vs without the recovered ambiguous anchors
- [ ] Integration (blocked by ISSUE-02): decoder with EB prior re-passes all D3 gates

## Blocked by

- ISSUE-01 (harness); final integration AC blocked by ISSUE-02
