# ISSUE-26 bounded replay — census cutoff decoder semantics

Date: 2026-07-10
Scope: read-only replay of the 2026-06-02 JHB production scan states; no GEHI,
Gemini, or full-census rerun.

## Result

| affected class | unique scan-state anchors | point-dated after ISSUE-26 | undated |
|---|---:|---:|---:|
| `done_ambiguous_clamp_inverted` | 1,023 | **1,023 (100%)** | 0 |
| `done_ambiguous_nonmonotonic` | 341 | **316 (92.7%)** | 25 |
| **total** | **1,364** | **1,339 (98.2%)** | **25** |

The ISSUE-26 problem statement's “371 nonmonotonic” number is the final
deliverable's **polygon** count (`undated_reason=done_ambiguous_nonmonotonic`),
not the number of unique scan-state anchors. The auditable interval inputs
contain 205 main anchors + 136 per-target anchors = 341 unique affected
anchors. This replay uses the anchor denominator because the decoder consumes
one scan state at a time.

All 25 residual undated anchors have **zero usable pre-census observations**
(19 main + 6 per-target). ISSUE-26 deliberately does not turn census presence
alone into a point estimate: the census observation may close an existing
historical bracket, but it cannot invent a bracket where no historical
evidence exists.

## Population construction

- Main intervals:
  `pre_clamp_backup_2026-06-04/main_install_intervals.csv`
  (`bb725ac79386fd9c91d99f630ffc374bd13fafab44e82c507a85a6708fb39a66`)
- Per-target recovery intervals:
  `pre_clamp_backup_2026-06-04/norecent_install_intervals_pertarget.csv`
  (`9a00ef101c1349e7b11102da8001f26b3d2a0e6dd196d1ef5c05b9d386bb5263`)
- Nonmonotonic population: rows whose interval status is
  `done_ambiguous_nonmonotonic` (205 main + 136 per-target).
- Clamp-inverted population: pre-clamp `done_appears` rows whose
  `latest_absent_date` and `earliest_present_date` are both later than their
  grid's Vexcel `last_capture_date` (658 main + 365 per-target).
- All 1,364 referenced scan-state JSON files existed; none were dropped.

Per-grid flight dates came from
`ZAsolar/data/analysis/vexcel_jhb_per_grid_capture_dates_2026-06-04.csv`
(`27c148eb38fc1dab1617bf9a1b02238d0d792b07a4bcfbd4a0c8dd0752e3357d`).

## Replay working point

Each affected scan state was loaded read-only through
`solar_backdating.eval.scan_state_io.load_scan_observations` and decoded with
the adopted production working point:

- estimator: `changepoint`
- `decoder_epoch_gap_days=45`
- fitted emissions:
  `emissions_fitted.json`
  (`eb6b06f06eefafc3f65ea97e9d848a26ac3c61ac4cc89d2986c740f1e3b906d5`)
- EB/Turnbull prior:
  `cohort_prior.json`
  (`dc67dc9c8f425f48e1d3fd4cbcce360cbf10abaadf71350407bce6412d0b4ec3`)
- cutoff/presence date: per-grid Vexcel `last_capture_date`

ISSUE-26 semantics exclude GEHI observations on/after the cutoff from the
historical likelihood, add the known-positive census mosaic as an independent
presence epoch at the cutoff, and leave later GEHI frames reference-only.

## Interpretation

This bounded replay satisfies AC3's decoder branch: 1,339/1,364 affected unique
anchors become point-dated without rescoring imagery, while the 25 anchors
with no pre-census evidence remain honestly undated. It does not claim that a
fresh adaptive scan would choose byte-identical historical brackets; that
would require new download/scoring calls and is explicitly outside this
issue's full-run non-goal.

## AC5 defense-in-depth check

The same adopted decoder working point was replayed over all 15,859 main scan
states. Result: 13,995 point-dated, 1,864 undated, **0**
`clamped_earliest_present` notes, and **0** `clamp_inverted` notes. The legacy
report-layer clamp code remains in place, but no post-fix decoder result needed
it in this replay.
