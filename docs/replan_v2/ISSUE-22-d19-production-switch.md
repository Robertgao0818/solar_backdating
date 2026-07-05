# ISSUE-22: D19 production switch (remaining §8 code items)

Status: done
Phase: 0 — Estimator (production landing)
Blocked by: 20 (done — unblocked immediately)
Completed: 2026-07-05 — narrow-caliber landing of all three PRD-AMENDMENT §8
code items: gate-runner downgrade to report-only (AC3, landed first as a
prerequisite), production default flip to the changepoint decoder + EB prior
with provenance (AC1), and the full-cohort fractional report-builder table
(AC2). PRD-AMENDMENT §8 checkboxes backfilled (AC4, this doc pass). AC5
(wide-caliber production re-decode + dated-frame redefinition + main-repo
posterior report) is registered here but explicitly **not executed** —
blocked by ISSUE-10/ISSUE-11 — and does not gate this issue's Status.

## Parent

[`../install_date_optimization_v2_prd.md`](../install_date_optimization_v2_prd.md)
— D19(iv). User stories 2, 4.

Trigger/Protocol/Evidence:

- [`PRD-AMENDMENT-P1-posterior-mass-caliber-2026-07-04.md`](PRD-AMENDMENT-P1-posterior-mass-caliber-2026-07-04.md)
  §8 adoption checklist, the three items left unchecked after ISSUE-20's
  docs-only pass (L434-444):
  > `- [ ] **Production config + provenance** — switch the default estimator to the decoder + EB prior (A4); config provenance MUST carry **estimator id + prior hash** (`cohort_prior.json`) alongside code refs `89496dd`/`1daa61d`.`
  > `- [ ] **Report-builder switch** — cohort report tables read the **fractional** channel; hard-MAP years demoted to display columns; C5-dip + grid-marginalisation + 0.067-disclosure caveats attached (A5).`
  > `- [ ] **Gate runner** — annotate/retire the hard-MAP year-histogram TVD assertion in `scripts/validation/issue03_gates.py` (and the `fullstack_noscan_analyze.py` / `panel_repair_d8_compare.py` reporting path) so the AC5 survival assertion is the gate-bearing threshold and the hard-MAP band is reported as a diagnostic, not asserted pass/fail (§9.6).`
- [`ISSUE-20-deliverable-caliber-amendment.md`](ISSUE-20-deliverable-caliber-amendment.md)
  "Out of scope (remaining §8 items, unassigned code work)" (L48-54), which
  named exactly these three items as future code work not in ISSUE-20's
  docs-only scope — this issue is that future work.
- [`ISSUE-21-band-prereg-2026-07-05.md`](ISSUE-21-band-prereg-2026-07-05.md)
  registered protocol item 6 (L37-39): "**Launch invariants per new rep.**
  Fresh empty verdict store … fractional channel produced by the
  **production default** (decoder + EB prior, D19/A4)." ISSUE-21's re-band
  reps cannot launch compliantly until AC1 lands — this issue is an implicit
  prerequisite of ISSUE-21, not merely a parallel §8 item.

## What to build

The PRD amendment (D19, Option A, signed 2026-07-05) requires three
production code changes beyond the docs-only PRD delta ISSUE-20 already
landed. This issue executes the **narrow caliber** only — the three §8 code
items exactly as scoped, with no cohort-wide per-anchor re-decode and no
cohort-frame change (red lines held throughout — see "Red-line compliance"
below):

1. **Gate runner (§8 item 3, landed first as a prerequisite).** Downgrade the
   retired hard-MAP year-histogram TVD band (0.037–0.063) from a live
   pass/fail assertion to a report-only diagnostic in
   `scripts/validation/estimator_harness.py` and
   `scripts/validation/estimator_endtoend_decode.py`; annotate (never
   restructure) `issue03_gates.py`'s AC5 survival gate as the sole
   gate-bearing threshold; add diagnostic-only notes to the
   `fullstack_noscan_analyze.py` / `panel_repair_d8_compare.py` reporting
   path (amendment §9.6).
2. **Production config + provenance (§8 item 1).** Flip the validation
   chain's default estimator to the changepoint decoder + EB/Turnbull prior
   (A4 working point: epoch-gap 45, EM-fitted 3-symbol emissions,
   `cohort_prior.json`); attach provenance (estimator id, prior/emissions
   sha256, decision refs, pinned code refs `89496dd`/`1daa61d`) to every run,
   following `scripts.temporal.scoring_provenance`'s sha256 pattern.
3. **Report-builder switch (§8 item 2).** Produce a full-cohort
   (15,859-anchor) region-level **interval/fractional** year table from the
   existing production posterior via the ISSUE-03 aggregation primitives
   (`cohort_year_histogram_from_fit` / `year_mass`) — a closed-form
   aggregation over already-scored intervals, **not** a per-anchor
   re-decode and **not** a cohort-frame rebuild — with the A5 caveats and
   the 0.067 disclosure attached verbatim.

Then, closing this issue: backfill the three PRD-AMENDMENT §8 checkboxes
(AC4) and register — without executing — the wide-caliber follow-up as AC5.

## Acceptance criteria

- [x] **AC1 — Production config + provenance.** Validation chain default
  estimator = changepoint decoder + EB prior (`--estimator` default flipped
  in `estimator_endtoend_decode.py`); `--decoder-epoch-gap-days` default =
  **45**; `--cohort-prior-json` / `--emissions-json` default to the canonical
  A4 artifacts (with `--no-cohort-prior` / `--no-emissions` escape hatches
  preserving the old flat path). Every run's `endtoend_decode_tvd.json`
  carries a `provenance` block: `estimator_id`, `is_adopted_default`,
  `decision_refs`, `adopted_code_refs={89496dd, 1daa61d}`, `working_point`
  (incl. `cohort_prior_sha256` / `emissions_sha256`), `provenance_sha256`.
  Explicit `--estimator pava` under the new defaults is **byte-identical**
  to the old flat `pava` config (`--no-cohort-prior --no-emissions
  --decoder-epoch-gap-days 30`) — the default flip changes nothing for
  non-adopted estimators. `test_regression_panel` (fpd 0.807/sustained
  0.911, TOL=5e-4) and AC7 `baselines_bit_exact` PASS. *(Done 2026-07-05.)*
- [x] **AC2 — Report-builder switch.**
  `scripts/validation/build_cohort_fractional_year_table.py` (new script,
  pure addition) produces a full-cohort (N=15,859) region-level fractional
  year table straight from the production Turnbull fit — no per-anchor
  re-decode, no cohort-frame rebuild. Artifact:
  `~/zasolar_data/geid_temporal/cohort_fractional_year_20260705/`
  (`cohort_year_fractional_table.{csv,md}`, `per_anchor_year_fractions.csv`,
  `fit_metadata.json`); the `.md` table carries the A5 three caveats + the
  0.067 disclosure **verbatim** in its header. Sanity: Σ year_mass +
  beyond_mass = 1.0000000000; Σ cohort_count = 15859.0000 (Δ = +4.93e-10 vs
  N); support = [2009, 2024] ⊆ imagery coverage window; 40 EM iterations /
  `final_loglik = −18659.7929` matches A4's recorded fit exactly; 2023/2024
  mass (0.6564 / 0.0135) matches A5's cited numbers exactly; per-anchor
  E-step cross-check max|Δ| = 5.71e-07. *(Done 2026-07-05.)*
- [x] **AC3 — Gate runner downgrade.** The retired hard-MAP
  year-histogram TVD band assertion is report-only in `estimator_harness.py`
  (`_run_endtoend` now unconditionally `return True`; JSON payload tags
  `rep_to_rep_band_status="retired_diagnostic_only"`) and
  `estimator_endtoend_decode.py` (`_band_report`'s `gate_pass` renamed
  `within_upper_edge`; JSON adds `status` + rewritten
  DIAGNOSTIC-ONLY `interpretation`). `issue03_gates.py`'s **AC5 survival
  gate structure is untouched** — verified: `build_ac5` still returns
  `verdict=PASS`, `beats_point_date=True`; only a `caliber` annotation was
  added to the point-date sub-channel (`point_date_year_tvd.caliber=
  "diagnostic_only_retired_hard_MAP_..."`), `rep_to_rep`/`mean` values
  unchanged. `fullstack_noscan_analyze.py` / `panel_repair_d8_compare.py`
  carry added diagnostic-only notes (summary dict + table.md header).
  `test_issue03_integration` (15 tests, incl. AC5/AC7 gate structure) PASS.
  *(Done 2026-07-05.)*
- [x] **AC4 — PRD-AMENDMENT §8 checkboxes backfilled.** The three unchecked
  §8 items ("Production config + provenance", "Report-builder switch",
  "Gate runner") flipped to `[x]` in
  `PRD-AMENDMENT-P1-posterior-mass-caliber-2026-07-04.md`, each dated
  2026-07-05, tagged "narrow caliber, see ISSUE-22". *(Done 2026-07-05, this
  doc pass.)*
- [ ] **AC5 — Wide-caliber production re-decode (deferred, registered — not
  executed).** Full-cohort **per-anchor** re-decode under the new
  production default (as opposed to the closed-form aggregation AC2 used),
  a redefinition of the cohort **dated frame**, and the corresponding
  posterior-report changes in the ZAsolar main-repo census reporting layer.
  **Blocked by [ISSUE-10](ISSUE-10-goldset-tooling.md)** (gold-set
  jump-point tooling — the frame decision needs the tooling to exist first)
  **and [ISSUE-11](ISSUE-11-goldset-adjudication-run.md)** (gold-set
  adjudication run — the frame decision needs adjudicated ground truth).
  Registered here rather than spun into a new issue, because the eventual
  frame rebuild should happen **once**, jointly with whatever frame change
  ISSUE-10/11's gold-set adjudication motivates — a second, separate frame
  rebuild for AC5 alone would duplicate work. Does not gate this issue's
  Status.

## Red-line compliance

Verified across all three landed items (AC1–AC3):

- `infer_install_dates.py` and `run_adaptive_scan.py` — **untouched** (grep
  confirmed: zero diff hunks touch either file).
- No cohort-frame rebuild, no full-cohort per-anchor re-decode — AC2 is a
  closed-form aggregation (`cohort_year_histogram_from_fit` / `year_mass`)
  over the **existing** production posterior; that wide-caliber work is
  exactly what AC5 registers and defers.
- ZAsolar main repo (`/home/gaosh/projects/ZAsolar`) — **untouched**; all
  edits are confined to `solar_backdating`.
- `issue03_gates.py`'s AC5 gate structure — **untouched**, only an
  additive `caliber` annotation (see AC3 evidence above); `test_regression_panel`
  (fpd/sustained, TOL=5e-4) and AC7 `baselines_bit_exact` PASS throughout.
- No `git add` / `git commit` / `git push` performed at any point; all
  changes live in the working tree.

## Evidence

### AC3 — gate downgrade (files touched)

| File | Lines | Change |
|---|---|---|
| `scripts/validation/estimator_harness.py` | 258-278 | `_run_endtoend` computes + prints the old-band comparison as diagnostic-only, then unconditionally `return True` — no exit-code effect |
| `scripts/validation/estimator_harness.py` | 255-257 | `endtoend_tvd.json` gains `rep_to_rep_band_status="retired_diagnostic_only"` |
| `scripts/validation/estimator_harness.py` | 68-71 | `REP_TO_REP_TVD_BAND` constant comment marks retired-per-PRD-AMENDMENT-P1 |
| `scripts/validation/estimator_endtoend_decode.py` | 283-311 | `_band_report`: `gate_pass` → `within_upper_edge`; `gate_pass_upper_edge_only` kept for artifact back-compat, new `status` + DIAGNOSTIC-ONLY `interpretation` |
| `scripts/validation/estimator_endtoend_decode.py` | 484-486 | print line tagged `[diagnostic-only, band retired per PRD-AMENDMENT-P1/ISSUE-22]` |
| `scripts/validation/estimator_endtoend_decode.py` | 54-59, 107 | module docstring + constant comment note retirement |
| `scripts/validation/issue03_gates.py` | 433-440 | `build_ac5`'s `point_date_year_tvd` gains `caliber="diagnostic_only_retired_hard_MAP_..."`; `rep_to_rep`/`mean`/`beats_point_date`/`verdict` logic unchanged |
| `scripts/validation/issue03_gates.py` | 106-111 | `POINT_DATE_BAND` constant comment marks the hard-MAP caliber retired, AC5's relative gate unchanged |
| `scripts/validation/fullstack_noscan_analyze.py` | 194-198, 218-220 | summary dict `caliber_note` + table.md diagnostic-only header line |
| `scripts/validation/panel_repair_d8_compare.py` | 170-173, 182-184 | `d8_notes` caveat + `d8_table.md` diagnostic-only header line |

`scripts/validation/panel_repair_tightcrop_compare.py` shows as modified in
`git status` but is a **pre-existing** dirty file from before this
workflow's scope (confirmed against the pre-session snapshot) — not touched
by this issue's work.

Test results: `test_issue03_integration` 15 passed (2.78–2.95s across
reps); `test_regression_panel` 5 passed (0.94–1.00s); `tests/estimator/`
full suite 180 passed (6.32–6.83s) — no regression surface from a
report-only downgrade.

### AC1 — production default flip + provenance (all in `estimator_endtoend_decode.py`, atop the AC3 edits)

| Lines | Change |
|---|---|
| 375-381 | `--estimator` default `pava` → `ADOPTED_ESTIMATOR="changepoint"` |
| 398-403 | `--decoder-epoch-gap-days` default `None` → `ADOPTED_DECODER_EPOCH_GAP_DAYS=45` (A4 working point) |
| 388-393 | `--cohort-prior-json` default `None` → `DEFAULT_COHORT_PRIOR_JSON` (canonical `issue03_gates_20260704/cohort_prior.json`) |
| 382-387 | `--emissions-json` default `None` → `DEFAULT_EMISSIONS_JSON` (canonical `panel_repair_20260703/.../emissions_fitted.json`) |
| 394-397, 387-388 | new `--no-cohort-prior` / `--no-emissions` escape hatches; `main()` (419-425) nulls the corresponding paths |
| 105-131, 60-67 | new A4 working-point constants block + docstring section |
| 313-363 | `_build_estimator_provenance(...)`: lazy `ChipHasher` sha256 (never raises; records `*_sha256_error` on missing files) + order-independent `canonical_hash` |
| 478-489 | output payload gains top-level `"provenance"` block |
| 501-508 | new provenance print lines |

Red-line check: `--estimator pava` (new defaults, ignored by pava) vs
`--estimator pava --no-cohort-prior --no-emissions --decoder-epoch-gap-days
30` (old flat config) — decode numerics, replacement counts, and delivery
reference are **byte-identical**; only provenance metadata differs (records
whichever config was actually assembled). A full default-`changepoint` run's
`unmodified_delivery_reference` rep-to-rep exact-matches the ISSUE-01 banked
`[0.0595, 0.0625, 0.0374]` — the flip does not perturb the baseline/delivery
channel.

Provenance sample (default changepoint run, `endtoend_decode_tvd.json`):

```json
{
  "estimator_id": "changepoint",
  "is_adopted_default": true,
  "adopted_default_estimator": "changepoint",
  "decision_refs": [
    "docs/replan_v2/DECISION-A-estimator-adoption-2026-07-04.md",
    "docs/replan_v2/PRD-AMENDMENT-P1-posterior-mass-caliber-2026-07-04.md"
  ],
  "adopted_code_refs": {
    "issue02_store_backed_endtoend_decoder": "89496dd",
    "issue03_eb_prior_addon": "1daa61d"
  },
  "working_point": {
    "decoder_epoch_gap_days": 45,
    "epoch_gap_days": 16,
    "clamp_used": true,
    "cohort_prior_json": ".../issue03_gates_20260704/cohort_prior.json",
    "cohort_prior_sha256": "dc67dc9c8f425f48e1d3fd4cbcce360cbf10abaadf71350407bce6412d0b4ec3",
    "cohort_prior_sha256_error": null,
    "emissions_json": ".../analysis_estimator_harness_extended/emissions_fitted.json",
    "emissions_sha256": "eb6b06f06eefafc3f65ea97e9d848a26ac3c61ac4cc89d2986c740f1e3b906d5",
    "emissions_sha256_error": null
  },
  "provenance_sha256": "sha256:595404b46e791616b24866fee5b3f5dfd6679e6eeed0d4113de7d0c3a489abf3"
}
```

`cohort_prior_sha256` matches DECISION-A's pin (`dc67dc9c…`); explicit-pava
run shows `is_adopted_default=false`, `estimator_id=pava`.

Test results: `test_regression_panel` 5 passed (0.96s); `test_issue03_integration`
15 passed (2.95s); AC7 `baselines_bit_exact` ok=True 5/5 (fpd .807/.046/.814,
sustained .911/.857); `tests/estimator/` full suite 180 passed (6.44s).

### AC2 — full-cohort fractional year table

Script: `scripts/validation/build_cohort_fractional_year_table.py` (new file,
pure addition — reads `intervals_prod.csv` → `fit_turnbull` →
`cohort_year_histogram_from_fit`). Artifact root:
`~/zasolar_data/geid_temporal/cohort_fractional_year_20260705/`.

Cohort-level fractional year table (N=15,859):

| Year | year_mass | Cohort count | Cumulative fraction | Cumulative count |
|---|---:|---:|---:|---:|
| 2009 | 0.0002 | 2.4 | 0.0002 | 2.4 |
| 2010 | 0.0000 | 0.0 | 0.0002 | 2.4 |
| 2011 | 0.0001 | 1.2 | 0.0002 | 3.6 |
| 2012 | 0.0002 | 2.7 | 0.0004 | 6.3 |
| 2013 | 0.0006 | 9.2 | 0.0010 | 15.6 |
| 2014 | 0.0015 | 24.4 | 0.0025 | 39.9 |
| 2015 | 0.0031 | 49.0 | 0.0056 | 89.0 |
| 2016 | 0.0010 | 15.4 | 0.0066 | 104.4 |
| 2017 | 0.0086 | 136.1 | 0.0152 | 240.4 |
| 2018 | 0.0203 | 321.7 | 0.0354 | 562.1 |
| 2019 | 0.0229 | 362.4 | 0.0583 | 924.5 |
| 2020 | 0.0389 | 617.6 | 0.0972 | 1542.1 |
| 2021 | 0.0733 | 1162.2 | 0.1705 | 2704.3 |
| 2022 | 0.1596 | 2531.4 | 0.3301 | 5235.6 |
| 2023 | 0.6564 | 10409.1 | 0.9865 | 15644.7 |
| 2024 | 0.0135 | 214.3 | 1.0000 | 15859.0 |

No `BEYOND` row (`beyond_mass=0` — the production cohort has no
right-censored intervals). A5 caveats + the 0.067 disclosure ship verbatim
in `cohort_year_fractional_table.md`'s header:

> - The **C5 2024 prior-mass dip** (0.0135 « 2023's 0.656) is a **Vexcel
>   flight-date right-censoring artifact — never a market signal**.
> - Survival curves are **grid-marginalised** — read cohort curves as
>   grid-marginalised, not a clean population survival function.
> - **D11 first-visible-appearance scope is unchanged** — no new precision
>   about physical install dates claimed.
> - The one **0.067** delivered-caliber (survival/fractional) pair that
>   still exceeds the old absolute band is **disclosed** on every
>   deliverable until the P3 re-band (§5) resolves it.

Sanity checks: Σ cohort_count = 15859.0000 (Δ = +4.93e-10 vs N=15,859); Σ
year_mass + beyond_mass = 1.0000000000; support [2009, 2024] ⊆ imagery
coverage window (2000, 2025); 40 EM iterations, `final_loglik = −18659.7929`
— matches A4's recorded fit ("40 EM iters, final_loglik −18659.79")
verbatim; 2023/2024 mass (0.6564 / 0.0135) matches the A5 caveat's cited
numbers ("2023's 0.656", "0.0135") exactly; per-anchor E-step column sums
vs cohort counts max|Δ| = 5.71e-07 (M-step fixed-point, well under 1e-6).

Test results: `test_regression_panel` + `test_issue03_integration` → 20
passed (3.91s); this addition is pure-increment, no regression surface.

### Final verification (2026-07-05)

| Check | Result |
|---|---|
| `pytest tests/estimator/test_regression_panel.py -q` | PASS — 5 passed, 0.94s (fpd 0.807/0.046/0.814, sustained 0.911/0.857, TOL=5e-4) |
| AC7 `baselines_bit_exact` (banked Panel A + banked prior `dc67dc9c…`) | PASS — ok=True, 5/5 metrics exact |
| `pytest tests/estimator/test_issue03_integration.py -q` | PASS — 15 passed, 2.86s (AC5/AC7 gate structure intact) |
| `pytest tests/estimator/ -q` (full suite) | PASS — 180 passed, 6.83s (= pre-change baseline, no regression) |
| `build_cohort_fractional_year_table.py --help` + scratchpad smoke run | PASS — N=15859, 40 iters converged, loglik=-18659.7929, Σ=15859.0000 (Δ+4.9e-10), PMF=1.0, support [2009,2024], beyond_mass=0 |

Zero file changes in the final verification pass; no `git add` / `commit` /
`push` at any point in this issue's execution.

## Blocked by

- [ISSUE-20](ISSUE-20-deliverable-caliber-amendment.md) (done) — the
  docs-only PRD delta this issue's code lands against. Unblocked
  immediately.
- **AC5 only:** [ISSUE-10](ISSUE-10-goldset-tooling.md),
  [ISSUE-11](ISSUE-11-goldset-adjudication-run.md) — see AC5 above. Does not
  block this issue's own Status.
