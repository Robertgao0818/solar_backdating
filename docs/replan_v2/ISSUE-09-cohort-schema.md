# ISSUE-09 — CoJ cohort audit contradiction dataset schema

Status: schema-of-record for the cohort artifacts (WP-D)
Produced by: `scripts/audit/coj_cohort_join.py` (+ `coj_cohort_report.py`)
Consumed by: the ISSUE-10 gold-set sampler and the economic event study.

This documents the two published tables of the cohort-scale CoJ true-date
audit. The **long** bit table (`cohort_audit_bits.csv`) is the source of truth —
one row per *planned* fetch unit, so its denominator is every unit that was ever
planned (including coverage gaps). The **wide** anchor table
(`cohort_audit_anchors.csv`) is the ISSUE-10-facing pivot — one row per anchor.

The frozen ISSUE-08 pilot primitives (margin rule 0.30/0.95 + classifier
demotion; conservative year-only contradiction bounds) are **imported** from
`scripts/audit/coj_audit_join.py`, never re-derived. See
`docs/coj_audit_pilot_2026-07-03.md` §4–§5 for their rationale.

---

## Per-unit known-sign expectation (design amendment)

Unlike the pilot's stratum-blanket `expected_sign`, the cohort expectation is
**per unit** (`coj_cohort_join.expected_bit(row, year) -> 'present'|'absent'|None`):

- a **negative-control** unit expects `absent` (a high-margin present there is a
  false-present — the negative-control gate signal);
- a **dated** unit `(anchor, year Y)` expects `present` iff its
  `install_interval_end` parses and is **strictly before** Jan-1 of year Y
  (the pipeline already claims the install predates the layer year);
- **every other** unit carries no expectation (blank).

There is deliberately no per-unit *absent* expectation for dated anchors: a
falsification unit's "absent" is the pipeline's own claim (the ISSUE-08 s3
reclassification), not ground truth — it can raise a contradiction but never
enters a known-sign gate.

Consequence inside `c_cal_present_pre2019` (`end < 2019-01-01`, ~988 anchors):
year-2015 units carry a `present` expectation only for the ~97 anchors with
`interval_end < 2015-01-01`; the remaining 2015 units have no expectation at
year 2015 (their `present` expectation only kicks in at 2019). The unit's
`unit_purpose` label (planned by WP-A) follows the precedence rule
`calibration_present` (end < Jan-1-Y) → `falsification` (start > Dec-31-Y) →
`bisector`; `(c_probe_2023, 2023)` bisector units are labelled `headline_2023`.

---

## Cohort strata (partition of dated anchors + negative controls)

`coj_cohort_join.COHORT_STRATA`; layer plan (`coj_cohort_join.LAYER_YEARS =
(2015, 2019, 2023)`):

| stratum | rule | layers |
|---|---|---|
| `c_cal_present_pre2019` | `install_interval_end < 2019-01-01` | 2015, 2019, 2023 |
| `c_cal_present_2019_2022` | `2019-01-01 ≤ end < 2023-01-01` | 2023 (+2019 if bisector) |
| `c_probe_2023` | `end ≥ 2023-01-01 AND start ≤ 2023-12-31` | 2023 (+2019 if bisector or in falsification subsample) |
| `c_findings_s3like` | `install_interval_start > 2023-12-31` | 2019, 2023 |
| `c_negative_control` | generated true-negatives | 2019, 2023 |

`in_fals_2019` (2019 falsification subsample membership) and bisector membership
are **flags** on strata 2/3 reported as sub-breakdowns — not separate strata, so
the strata stay a clean partition. `in_fals_2019` is detected from a bit row as
`unit_purpose == 'falsification' AND year == 2019` (or an explicit
`in_fals_2019` truthy field, if the builder sets one).

---

## Coverage states (`bit` values beyond the pilot's 3)

`bit` extends the pilot's `present` / `absent` / `low_margin` with two coverage
states so the long-table denominator is every planned unit:

- `no_coverage` — `fetch_outcome == 'empty'`: the ArcGIS `exportImage` returned
  an empty body (imagery does not cover the bbox). Counts as *covered* for the
  coverage AC (a real terminal answer), never contradicts, never routed.
- `fetch_failed` — the fetch never yielded a scorable chip
  (`waf_challenge` / `http_error` / `exception` / `not_fetched` / blank
  `fetch_outcome`, or an `ok` chip with no readable/scored detector value).
  Counts as a coverage **gap**.

In the wide table, a layer year with no planned unit for the anchor is
`bit_Y == not_planned` (distinct from the coverage states above — the layer plan
skipped it, it was never a gap).

---

## Table 1 — `cohort_audit_bits.csv` (LONG, one row per planned unit)

Produced by `build_cohort_bit(row) -> dict`. Column order is authoritative.

<!-- LONG_TABLE_COLUMNS_START -->
| column | type | allowed / example | semantics |
|---|---|---|---|
| `anchor_id` | str | `anchor_fake_001`, `nc_0007` | dated anchor id, or `nc_XXXX` for a negative control |
| `stratum` | str | one of `COHORT_STRATA` | cohort stratum |
| `year` | int | 2015 / 2019 / 2023 | CoJ layer year of this unit |
| `unit_purpose` | str | `calibration_present` \| `bisector` \| `falsification` \| `findings_s3like` \| `headline_2023` \| `negative_control` | why this (anchor, year) unit was planned |
| `status` | str | e.g. `done_appears` | terminal install status (blank for NC) |
| `confidence` | str | high / medium / low | pipeline interval confidence (blank for NC) |
| `install_interval_start` | str/ISO | `2024-03-01` or blank | latest-absent bound |
| `install_interval_end` | str/ISO | `2018-06-01` or blank | earliest-present bound |
| `chip_path` | str | `chips/2023/anchor_fake_001.tif` | fetched chip file (blank if no chip) |
| `fetch_outcome` | str | `ok` \| `empty` \| `waf_challenge` \| `http_error` \| `exception` \| `skipped_existing` \| `not_fetched` | terminal fetch outcome |
| `detector_S` | float \| "" | `0.994` | max post-NMS detector score (blank if no scorable chip) |
| `classifier_pv_prob` | float \| "" | `0.87` | optional solar_cls corroboration (blank if unavailable) |
| `bit` | str | `present` \| `absent` \| `low_margin` \| `no_coverage` \| `fetch_failed` | presence bit; only the first three come from `classify_bit` |
| `expected` | str \| "" | `present` \| `absent` \| "" | per-unit known-sign expectation (blank when none) |
| `agrees` | bool \| "" | True / False / "" | high-margin bit vs `expected` (blank if low-margin/coverage or no expectation) |
| `contradicts_interval` | bool | True / False | conservative year-bound contradiction; only ever True for a high-margin present/absent bit |
| `routed_to_queue` | bool | True / False | low-margin bit → human queue |
<!-- LONG_TABLE_COLUMNS_END -->

---

## Table 2 — `cohort_audit_anchors.csv` (WIDE, one row per anchor) — the ISSUE-10 table

Produced by `pivot_anchors(bit_rows, anchor_meta) -> list[dict]`. One row per
anchor in `anchor_meta` (the authoritative anchor set). Column order is
authoritative; the per-year block repeats for `Y ∈ {2015, 2019, 2023}`.

<!-- WIDE_TABLE_COLUMNS_START -->
| column | type | allowed / example | semantics |
|---|---|---|---|
| `anchor_id` | str | `anchor_fake_001` | anchor id |
| `stratum` | str | one of `COHORT_STRATA` | cohort stratum |
| `status` | str | e.g. `done_appears` | terminal install status |
| `confidence` | str | high / medium / low | pipeline interval confidence |
| `install_interval_start` | str/ISO | `2022-01-01` | latest-absent bound |
| `install_interval_end` | str/ISO | `2024-06-01` | earliest-present bound |
| `latest_absent_date` | str/ISO | `2022-01-01` | latest-absent observation date (from anchor meta) |
| `earliest_present_date` | str/ISO | `2024-06-01` | earliest-present observation date (from anchor meta) |
| `grid_id` | str | `JNB0101` | JNB task-grid cell |
| `is_negative_control` | bool | True / False | whether this anchor is a generated negative control |
| `bit_2015` | str | `present` \| `absent` \| `low_margin` \| `no_coverage` \| `fetch_failed` \| `not_planned` | 2015 presence bit (`not_planned` if the layer plan skipped 2015) |
| `detector_S_2015` | float \| "" | `0.99` | 2015 detector score (blank if not_planned) |
| `classifier_pv_prob_2015` | float \| "" | `0.9` | 2015 classifier prob (blank if not_planned) |
| `contradicts_2015` | bool \| "" | True / False / "" | 2015 contradiction flag (blank if not_planned) |
| `bit_2019` | str | as `bit_2015` | 2019 presence bit |
| `detector_S_2019` | float \| "" | `0.12` | 2019 detector score |
| `classifier_pv_prob_2019` | float \| "" | `0.05` | 2019 classifier prob |
| `contradicts_2019` | bool \| "" | True / False / "" | 2019 contradiction flag |
| `bit_2023` | str | as `bit_2015` | 2023 presence bit |
| `detector_S_2023` | float \| "" | `0.98` | 2023 detector score |
| `classifier_pv_prob_2023` | float \| "" | `0.88` | 2023 classifier prob |
| `contradicts_2023` | bool \| "" | True / False / "" | 2023 contradiction flag |
| `any_contradiction` | bool | True / False | ≥1 planned bit contradicts the interval |
| `n_contradictions` | int | 0 / 1 / 2 / 3 | count of contradicting planned bits |
| `n_high_margin_bits` | int | 0..3 | count of high-margin (present/absent) planned bits |
| `first_present_year` | int \| "" | 2015 / 2019 / 2023 / "" | earliest year with a high-margin present bit (blank if none) |
<!-- WIDE_TABLE_COLUMNS_END -->

---

## Headline — `contradiction_rate_by_stratum.csv`

Produced by `contradiction_rate_by_stratum(bit_rows) -> list[dict]`, serialised
by `coj_cohort_report.py`. Columns: `level, stratum, year, flag,
n_denominator, n_numerator, rate, ci_low, ci_high` (`ci_*` = Wilson score
interval). Row kinds by `level`:

- `stratum` — anchor-level: denominator = anchors with ≥1 high-margin bit,
  numerator = anchors with ≥1 contradiction.
- `stratum_year` — bit-level per (stratum × year): denominator = high-margin
  bits, numerator = contradiction bits.
- `flag` — the `in_fals_2019` sub-breakdown (anchor-level, over the 2019
  falsification subsample).

---

## Self-gates — `gates_report.json`

`compute_cohort_gates(bit_rows, nc_anchor_ids=...) -> {gate_a, gate_nc, gate_b,
clamp_findings}` (see the module docstring). `coj_cohort_report.build_cohort_report`
expects `gates_report.json` to carry `{"gates": <compute_cohort_gates output>,
"coverage": <coverage_report output>}`.

- `gate_a` — present-side known-sign agreement per (stratum × year) over
  expectation-bearing high-margin non-control units, target ≥ 0.95 per cell.
- `gate_nc` — negative-control false-present count; PASS iff the gate is
  **evaluable** (≥ `NC_SCORED_THRESHOLD` = 0.95 of the declared controls produced
  a scored present/absent/low_margin bit — a gate that never scored a control,
  all fetch_failed/no_coverage or none declared, is *not* evaluable and cannot
  pass vacuously) **and** the false-present count ≤ 2; + scored fraction + rate +
  Wilson CI.
- `gate_b` — within-audit monotonicity noise floor (present@earlier ∧
  absent@later over {2015,2019,2023}); reported count + ids, not a hard gate.
- `clamp_findings` — count + ids of anchors with a high-margin present@2023 bit
  contradicting a clamped interval, excluding `EXCLUDED_STATUSES`.

Coverage AC: `coverage_report(planned_units, bit_rows)` →
`covered_anchors / dated_anchors ≥ 0.95` (an anchor is covered iff all its
planned units yielded a real terminal presence bit —
`present`/`absent`/`low_margin`/`no_coverage` — i.e. none is `fetch_failed`;
coverage keys on the computed `bit`, not on `fetch_outcome`, so an `ok` fetch
that produced no scorable chip is a gap, enumerated with a `<outcome>_unscorable`
reason). Every gap is enumerated into `fetch_failures.csv`.

---

## ISSUE-10 consumption

The gold-set sampler consumes `cohort_audit_anchors.csv` and stratifies on
`status × confidence × any_contradiction`, rendering the jump window from
`install_interval_start` / `install_interval_end`. It degrades gracefully if the
file is absent (per the ISSUE-10 AC).
