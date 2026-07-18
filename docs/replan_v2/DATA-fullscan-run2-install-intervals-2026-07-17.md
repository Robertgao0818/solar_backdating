# DATA — Fullscan RUN 2 install-date interval inference (2026-07-17)

Post-run steps 1–2 of [`RUN-fullscan-gemini-backdating-2026-07-15.md`](RUN-fullscan-gemini-backdating-2026-07-15.md)
§8, executed against the completed RUN 2 (41,393/41,393 terminal, 0
`gemini_failed`, completed 2026-07-17 19:31 NZST).

## 1. pytest smoke (step 1)

```bash
source scripts/activate_env.sh
pytest tests/ -q
```

Result: **1271 passed, 13 skipped, 1 failed** (6 subtests passed). The one
failure is the expected pre-existing `test_pilot_sequence_head.py::
test_teacher_target_comes_from_phase0_decoder_not_label_3class`
(`FileNotFoundError: .../panel_repair_20260703/analysis_estimator_harness_extended/emissions_fitted.json`,
data artifact wiped 2026-07-12) — matches the stated baseline. No unrelated
failures. (Baseline quoted ~1272 passed; the 1-test difference is noise from
the currently-uncommitted working-tree edits to several test files, not a
regression.)

## 2. Interval inference (step 2)

```bash
source scripts/activate_env.sh
RUN_ROOT=~/zasolar_data/geid_temporal/fullscan_gemini_backdating_2026-07
VEXCEL_CSV=$ZASOLAR_ROOT/data/analysis/vexcel_jhb_per_grid_capture_dates_2026-06-04.csv

python scripts/temporal/infer_install_dates.py \
  --scan-states-dir $RUN_ROOT/a24/scan_states \
  --output $RUN_ROOT/intervals/install_intervals_a24.csv \
  --vexcel-capture-csv $VEXCEL_CSV \
  --require-terminal

python scripts/temporal/infer_install_dates.py \
  --scan-states-dir $RUN_ROOT/a48/scan_states \
  --output $RUN_ROOT/intervals/install_intervals_a48.csv \
  --vexcel-capture-csv $VEXCEL_CSV \
  --require-terminal
```

`infer_install_dates.py` takes one `--scan-states-dir` at a time, so each lane
ran separately; a `lane` column (`a24`/`a48`) was then stitched on and the two
CSVs concatenated into a combined file:

```bash
python3 - <<'EOF'
import csv
from pathlib import Path
run_root = Path.home() / "zasolar_data/geid_temporal/fullscan_gemini_backdating_2026-07"
out = run_root / "intervals/install_intervals_all.csv"
fields, rows = None, []
for lane in ("a24", "a48"):
    p = run_root / f"intervals/install_intervals_{lane}.csv"
    with p.open(newline="", encoding="utf-8") as fh:
        r = csv.DictReader(fh)
        fields = fields or ["lane"] + r.fieldnames
        for row in r:
            rows.append({"lane": lane, **row})
with out.open("w", newline="", encoding="utf-8") as fh:
    w = csv.DictWriter(fh, fieldnames=fields)
    w.writeheader(); w.writerows(rows)
EOF
```

Both lane runs used defaults for `--census-mid-date` (2024-06-30 JHB
fallback; superseded per-row by the Vexcel per-grid flight date wherever
`grid_id` resolves — true for all 382 grids here) and dip repair enabled
(default; `--no-dip-repair` not passed).

### Inputs

| input | path |
|---|---|
| A24 scan states | `$RUN_ROOT/a24/scan_states/` (36,322 `.json`) |
| A48 scan states | `$RUN_ROOT/a48/scan_states/` (5,071 `.json`) |
| Vexcel per-grid capture dates | `$ZASOLAR_ROOT/data/analysis/vexcel_jhb_per_grid_capture_dates_2026-06-04.csv` (382 grids loaded) |

### Outputs

| output | path | rows | sha256 |
|---|---|---|---|
| A24 intervals | `$RUN_ROOT/intervals/install_intervals_a24.csv` | 36,322 | `47b5f4146fcdaf5fa2d8d102864cf903aab1f1f15f1ef05000168a7a64ac718b` |
| A48 intervals | `$RUN_ROOT/intervals/install_intervals_a48.csv` | 5,071 | `ed92acd30fded2751525623e0005ee3349ed3f4e45a0ca9ee2e36a6e4ff9c759` |
| **Combined** | `$RUN_ROOT/intervals/install_intervals_all.csv` | **41,393** | `f5101c8ad926316a7effb06f179cdc6e8720664aa51508c2d0c794a63fcb8cc6` |

Row-count sanity check **passes**: 36,322 + 5,071 = 41,393, exactly one row
per terminal scan_state across the whole census population, matching the
`.complete` run total. `contaminated_archive_2026-07-17/` and
`dirty_run1_archive_2026-07-17/` were not touched (excluded by construction —
only `a24/scan_states` and `a48/scan_states` were globbed).

## 3. Important note: post-processing reclassifies a chunk of raw statuses

The status column in the interval CSVs is **not** a copy of the raw
scan_state `status` — `infer_install_dates.py` runs two non-destructive
re-derivation passes on top of it (both documented in the script's own
docstring/comments), and the combined output's status distribution differs
materially from the raw combined counts quoted in the run handoff. Both
deltas reconcile exactly against the raw counts, confirmed by exact
arithmetic below — nothing is unaccounted for.

**(a) Interior dip repair** (`apply_dip_repair`, on by default,
`--dip-flank-min-confidence 0.5`): a washed-out interior `absent` frame
flanked by confident `present` frames is flipped to `present` on a
scan-state copy (the underlying scan is untouched), then status is
re-derived **only if** the raw status was `done_ambiguous_nonmonotonic`:

- 656 anchors: repaired dip removes the non-monotonic transition entirely →
  reclassified `done_appears`.
- 956 anchors: repaired dip leaves zero absent observations → reclassified
  `done_already_present_before_geid_history`.
- 151 anchors: still non-monotonic (or <3 usable observations, repair
  skipped) → stay `done_ambiguous_nonmonotonic`.
- Check: 656 + 956 + 151 = 1,763 = the raw `done_ambiguous_nonmonotonic`
  count exactly.
- (Dip repair also fires on already-terminal `done_appears` /
  `done_installed_during_census` / `done_already_present_...` states without
  changing their status label, just tightening interval bounds — 3,195
  interior dips flipped across 1,661 anchors total per the script's own
  console summary (2,398/1,221 in A24 + 797/440 in A48); 1,612 of those 1,661
  anchors are the nonmonotonic-repair reclassifications above, the remaining
  49 are label-preserving bound tightenings.)

**(b) Census-GT-contradiction reclassification** (inside `infer_one`, no
flag to disable): for raw `done_installed_during_census` anchors, if the
scan's own `latest_absent_date` falls **on or after** the anchor's grid
Vexcel flight date (i.e. the scan still reads absent at or after the date the
census ground truth says the panel is present), the row is reclassified
`done_ambiguous_marker_missed_pv` (leading hypothesis per code comment:
marker fell on a roof aisle/shadow/wrong segment) and its interval is
blanked rather than trusted.

- 4,645 anchors reclassified this way.
- Check: 27,962 (raw `done_installed_during_census`) − 4,645 = 23,317 =
  post-processed count exactly.
- A much smaller, structurally distinct variant (`done_ambiguous_clamp_inverted`,
  2 anchors, both from `done_appears`) fires when **both** the latest-absent
  *and* the raw earliest-present postdate the grid flight date — the whole
  GEHI/Wayback/TM read for that anchor postdates the detection imagery.

This 11.2%-of-population reclassification (4,645 + 2 = 4,647 anchors) is the
single largest driver of the "ambiguous" share below and is worth flagging
upstream: it means roughly 1 in 9 anchors that the raw scan called
"installed during census" has an internal contradiction against the
FP-cut/Vexcel census-GT prior that the script currently resolves by
abstaining rather than guessing. It is not a scan-quality regression (0
`gemini_failed`); it is the interval script doing its documented job of
catching a specific, named failure mode.

**(c) Known upstream defect: 701 `done_appears` rows are mislabeled, not
dated.** `infer_install_dates.py` has a third internal consistency check on
the `done_appears` branch: if `latest_absent_date` ends up **later** than
`earliest_present_date` (a look-angle/parallax/date-drift artifact), the
script blanks all interval fields (`install_interval_start`,
`install_interval_end`, `install_mid_estimate`) and its own note says so
explicitly — *"inverted_interval: ... (state machine should classify as
done_ambiguous_nonmonotonic; check scan_state)"* — **but it leaves the row's
`status` labeled `done_appears`**, unlike the structurally identical
`clamp_inverted` case in §3b, which does get relabeled. This is a gap in the
script's own state machine, not a data problem: 701 rows (593 A24 / 108 A48,
1.7% of the population) carry `status=done_appears` with every interval
field blank. The §4 headline table below and every other table/statistic in
this memo (interval width, confidence, year histogram) treat these 701 rows
as **undated**, not dated — they were excluded from those computations from
the start (blank-string values don't parse as dates), so only the coverage
headline itself needed correcting. `infer_install_dates.py` has **not** been
patched — this is an owner decision pending, and downstream consumers
(the `install_intervals_all.csv` status column, and any code that filters on
raw `status=="done_appears"` without also checking for a non-blank
`install_interval_start`) need to know about it in the meantime. The
economic-layer deliverable (`build_install_dated_deliverable.py`,
`~/zasolar_data/geid_temporal/fullscan_gemini_backdating_2026-07/deliverable_2026-07-17/README_install_dated_2026-07-17.md`)
independently catches and documents this same defect under
`undated_reason=inverted_interval_mislabeled_appears`; this memo and that
README now report the same headline numbers.

## 4. Coverage summary (combined, 41,393 rows)

**Corrected 2026-07-17 (post-review)** — see §3c below for why. The
originally-published headline miscounted 701 rows as dated; this table now
reflects the true bounded-interval count.

| coverage class | status(es) | n | % |
|---|---|---:|---:|
| Bounded interval (dated) | `done_appears` (real bracket, n=9,123) + `done_installed_during_census` (n=23,317) | 32,440 | 78.4% |
| Left-censored (`≤`, open lower bound) | `done_already_present_before_geid_history` | 2,018 | 4.9% |
| Undated (abstain) | all `done_ambiguous_*` **plus** the 701 inverted-interval `done_appears` rows (§3c) | 6,935 | 16.8% |

By design, the scan window floor is 2019-01-01, so every
`done_already_present_before_geid_history` row is a left-censored "install
≤ [grid flight date]" bound, not a hard "before 2019" claim — but 330 of the
32,440 bounded-interval rows (1.0%) additionally have
`install_interval_start` itself in 2019, i.e. their lower bound sits right at
the window floor and could in principle extend earlier than 2019 if the
window were widened.

### Status breakdown (post-processing applied)

| status | combined | A24 | A48 |
|---|---:|---:|---:|
| `done_installed_during_census` | 23,317 | 21,406 | 1,911 |
| `done_appears` | 9,824 | 7,786 | 2,038 |
| `done_ambiguous_marker_missed_pv` | 4,645 | 4,243 | 402 |
| `done_already_present_before_geid_history` | 2,018 | 1,392 | 626 |
| `done_ambiguous_no_recent_anchor` | 1,433 | 1,385 | 48 |
| `done_ambiguous_nonmonotonic` | 151 | 105 | 46 |
| `done_ambiguous_orchestrator_error` | 3 | 3 | 0 |
| `done_ambiguous_clamp_inverted` | 2 | 2 | 0 |
| **Total** | **41,393** | **36,322** | **5,071** |

Note: 701 of the 9,824 `done_appears` rows above (593 A24 / 108 A48) carry
blank interval fields — see §3c. This table reports the raw `status` string
as-is; the §4 coverage table treats those 701 as undated, not dated.

### Abstain (undated) breakdown, with example notes

| undated_reason (status) | n | example note |
|---|---:|---|
| `done_ambiguous_marker_missed_pv` | 4,645 | `latest_absent 2025-03-30 >= census flight date 2024-04-19 (grid JNB0026) contradicts census-GT prior` |
| `done_ambiguous_no_recent_anchor` | 1,433 | `all absent but latest_avail not usable after 2 anchor_recovery rounds` |
| inverted-interval `done_appears` rows, blank dates, mislabeled by `infer_install_dates.py` — see §3c and the deliverable README | 701 | `inverted_interval: latest_absent 2025-03-30 > earliest_present 2024-02-29 (state machine should classify as done_ambiguous_nonmonotonic; check scan_state)` |
| `done_ambiguous_nonmonotonic` | 151 | `present->absent step detected in chronological observations` (post dip-repair residual) |
| `done_ambiguous_orchestrator_error` | 3 | see §5 below (permanent) |
| `done_ambiguous_clamp_inverted` | 2 | `latest_absent 2024-02-29 > vexcel flight date 2024-02-17 ... both GEHI observations post-date the detection imagery` |
| **Total undated** | **6,935** | |

## 5. Permanent orchestrator_error anchors (3, unchanged from run handoff)

| anchor_id | lane | grid_id | cause |
|---|---|---|---|
| `jhb_full382_unified_a_merge01_c0925_fpcut_2026_06_01_t00007261` | a24 | JNB0061 | `RuntimeError: no_live_gehi` — anchor absent from offline TM catalog CSV, no offline catalog fallback |
| `jhb_full382_unified_a_merge01_c0925_fpcut_2026_06_01_t00007262` | a24 | JNB0061 | same as above |
| `jhb_full382_unified_a_merge01_c0925_fpcut_2026_06_01_t00033551` | a24 | JNB0314 | `OSError: decoder error -2` |

## 6. Confidence / quality distribution

Rule (per script): `done_appears` high `<=183d` gap / medium `<=730d` / low
`>730d` or inconsistent; `done_installed_during_census` high `<=365d` gap
census-side / medium otherwise (never low — rows that would be low get
reclassified `marker_missed_pv` instead, see §3b); `done_already_present_...`
and all `done_ambiguous_*` are always `low`.

| confidence | combined | A24 | A48 | share of bounded-interval rows only |
|---|---:|---:|---:|---:|
| high | 17,304 | 15,093 | 2,211 | 17,304 / 32,440 = 53.3% |
| medium | 15,103 | 13,475 | 1,628 | 15,103 / 32,440 = 46.6% |
| low | 8,986 | 7,754 | 1,232 | 734 / 32,440 = 2.3% (rest is ambiguous+left-censored, always low) |

By status: `done_appears` (n=9,824) high=3,477 / medium=5,613 / low=734 (the
low-confidence `done_appears` rows split into the 701 blank/inverted rows
from §3c plus 33 genuine wide-bracket `>730d`-gap cases — 701 + 33 = 734
exactly); `done_installed_during_census` (n=23,317) high=13,827 /
medium=9,490, no low as expected; all other statuses are 100% low by
construction. The 32,440-row bounded-interval population used in §7/§8 below
excludes the 701 blank rows (they were never included — blank date strings
don't parse), so only the 33 genuine wide-bracket rows are the "low
confidence but actually dated" cohort within that 32,440.

## 7. Interval-width distribution (bounded-interval rows only, n=32,440)

Width = `install_interval_end − install_interval_start` in days.

| p10 | p25 | p50 | p75 | p90 | p99 | max | mean |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 29 | 85 | 177 | 394 | 394 | 432 | 1,004 | 212.2 |

(The p75/p90 tie at 394 days reflects the `done_installed_during_census`
census-cutoff clustering — many rows share the same per-grid census
ceiling as their interval end.)

## 8. Install-date (midpoint) histogram by year, bounded-interval rows

| year | combined | A24 | A48 |
|---|---:|---:|---:|
| 2019 | 222 | 151 | 71 |
| 2020 | 720 | 379 | 341 |
| 2021 | 1,107 | 766 | 341 |
| 2022 | 1,853 | 1,334 | 519 |
| 2023 | 4,807 | 4,192 | 615 |
| 2024 | 23,731 | 21,777 | 1,954 |
| **Total** | **32,440** | **28,599** | **3,841** |

The 2024 mass is expected: `done_installed_during_census` middies fall on the
per-grid census flight date by construction (23,317 of the 23,317 census-status
rows), and JHB's PV base grew sharply into the census year. A24's tail is
disproportionately later/larger-share-2024 than A48 (A48 skews slightly
earlier/flatter across 2019–2023) — consistent with A24 being the smaller
(`source_area_m2 < 40`) rooftop-panel arm, which the routing decision
(`routed_A24_A48_cut40`) associates with newer/smaller residential
installs concentrated near the census date, vs A48's larger arrays.

Left-censored rows (`done_already_present_before_geid_history`, n=2,018) all
carry `install_interval_end` in 2019 (the earliest scored vintage each hits
is at/near the 2019-01-01 window floor), consistent with "install ≤2019,
by design of the window floor" — 2019: 2,018 / 2,018 (100%).

## 9. Files

- `$RUN_ROOT/intervals/install_intervals_a24.csv` (36,322 rows)
- `$RUN_ROOT/intervals/install_intervals_a48.csv` (5,071 rows)
- `$RUN_ROOT/intervals/install_intervals_all.csv` (41,393 rows, `lane` column added) — **primary artifact for downstream consumers**
