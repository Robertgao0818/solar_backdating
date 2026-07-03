# ISSUE-01: InstallDateEstimator seam + PAVA floor + evaluation harness

Status: done
Phase: 0 — Estimator
Blocked by: none
Completed: 2026-07-03 — seam `src/solar_backdating/estimators/` (registry: `pava`, `fpd`, `sustained`), harness `scripts/validation/estimator_harness.py`, tests `tests/estimator/` (61 tests). Regression gate PASS (fpd 0.807 / sustained 0.911 / undated-flip 0.046, per-stratum n's match). PAVA floor on panel: mode-hit 0.875 (between fpd and sustained). End-to-end 3-rep pairwise year-TVD 0.037–0.063 band PASS. Harness artifacts: `fullstack_noscan_20260630/analysis_estimator_harness/`.

## Parent

[`../install_date_optimization_v2_prd.md`](../install_date_optimization_v2_prd.md) — D1(ii), D2 (floor), D3/D8 (harness). User stories 1, 5, 6, 34.

## What to build

The tracer bullet for the whole estimator line: a new **InstallDateEstimator
seam** — a pure function from one anchor's ordered per-vintage observations
(capture date, presence verdict, confidence, quality flag, plus clamp context)
to `{posterior over install epochs, MAP interval, P(undated), credible
interval}` — with the **PAVA/isotonic single-changepoint estimator** as its
first implementation (the mandatory falsification floor), and the **offline
evaluation harness** that scores any estimator implementation on the banked
10-rep full-stack panel (`fullstack_noscan_20260630`) and the 3 end-to-end
reps, emitting the full D3/D8 metric set.

The harness is the acceptance surface every later estimator (ISSUE-02/03)
reuses: MAP-interval mode-hit, undated-flip, per-stratum table,
inventory-weighted headline alongside unweighted, dated-only denominator
alongside all-units, year-histogram TVD.

## Acceptance criteria

- [x] Seam interface defined; implementations selectable by name in the harness
- [x] PAVA floor implemented as a deterministic pure function (property test: same input → same output)
- [x] Harness regression-reproduces the published sustained/FPD numbers on the banked panel (mode-hit 0.911 / 0.807, undated-flip 0.046) before any new estimator is trusted
- [x] Harness reports inventory-weighted + unweighted + dated-only metric variants per D8
- [x] Shared synthetic fixtures: known changepoint recovered; all-absent sequence → high P(undated); duplicate frames invariant under epoch collapsing

## Blocked by

None - can start immediately
