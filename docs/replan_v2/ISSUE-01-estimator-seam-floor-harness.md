# ISSUE-01: InstallDateEstimator seam + PAVA floor + evaluation harness

Status: done
Phase: 0 — Estimator
Blocked by: none
Completed: 2026-07-03 — seam `src/solar_backdating/estimators/` (registry: `pava`, `fpd`, `sustained`), harness `scripts/validation/estimator_harness.py`, tests `tests/estimator/` (61 tests). Regression gate PASS (fpd 0.807 / sustained 0.911 / undated-flip 0.046, per-stratum n's match). PAVA floor on panel: mode-hit 0.875 (between fpd and sustained). End-to-end 3-rep pairwise year-TVD 0.037–0.063 band PASS. Harness artifacts: `fullstack_noscan_20260630/analysis_estimator_harness/`.

**Correction (2026-07-04, record hygiene):** the "undated-flip 0.046" cited above
(and in the AC below) is FPD's banked value, not sustained's — the "published"
number this line's regression check reproduces bit-exact came from
`fullstack_noscan_analyze.py`'s original banked table, which printed one shared
undated-flip field under both the FPD and sustained rows (same bug pattern
later found in `panel_repair_d8_compare.py`; see the ISSUE-04 memo's
[Correction](ISSUE-04-decision-memo-2026-07-03.md#correction-2026-07-04)).
This harness's own per-estimator reports
(`d3_gates_banked_20260703/fit_emissions/report_sustained.md`) give sustained's
true banked undated-flip as **0.075**. The regression-reproduction claim
itself is unaffected (both scripts agree bit-exact on FPD's 0.046 and on
mode-hit 0.911/0.807); only the implicit reading of 0.046 as sustained's
number was wrong.

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
