# DATA -- RUN 3 vs RUN 2 full-population reconciliation

Joined on `anchor_id`: 41393 common anchors (0 RUN2-only, 0 RUN3-only).

## Headline metrics (rescan_pilot_analyze.py definitions, full population)

| n | status_changed_rate | contradiction_flip_rate (n=4645) | bracket_changed_rate (of 28235 dated-in-both) | median |bracket_shift_days| (changed only) |
|---:|---:|---:|---:|---:|
| 41393 | 49.3% | 82.0% | 42.5% | 282 |

`contradiction_flip_rate`: of 4645 RUN2 anchors flagged `done_ambiguous_marker_missed_pv` (census-GT contradiction -- scan still read absent on/after the grid's Vexcel flight date), the share that resolved to a real bounded status (`done_appears`/`done_installed_during_census` with a non-blank interval) in RUN3.

## Coverage-class transition matrix (RUN2 -> RUN3)

| RUN2 class | RUN3 class | n | % of RUN2 class |
|---|---|---:|---:|
| dated | dated | 28235 | 87.0% |
| undated | dated | 5622 | 81.1% |
| dated | undated | 2181 | 6.7% |
| dated | left_censored | 2024 | 6.2% |
| left_censored | left_censored | 1351 | 66.9% |
| undated | left_censored | 683 | 9.8% |
| undated | undated | 630 | 9.1% |
| left_censored | dated | 590 | 29.2% |
| left_censored | undated | 77 | 3.8% |

## Status distribution, RUN2 vs RUN3 (common anchors only)

| status | RUN2 n | RUN3 n |
|---|---:|---:|
| done_installed_during_census | 23317 | 15664 |
| done_appears | 9824 | 19617 |
| done_ambiguous_marker_missed_pv | 4645 | 1731 |
| done_already_present_before_geid_history | 2018 | 4058 |
| done_ambiguous_no_recent_anchor | 1433 | 224 |
| done_ambiguous_nonmonotonic | 151 | 98 |
| done_ambiguous_orchestrator_error | 3 | 0 |
| done_ambiguous_clamp_inverted | 2 | 1 |

## Top status transitions (status_changed anchors only)

| RUN2 status | RUN3 status | n |
|---|---|---:|
| done_installed_during_census | done_appears | 8947 |
| done_ambiguous_marker_missed_pv | done_installed_during_census | 2103 |
| done_ambiguous_marker_missed_pv | done_appears | 1790 |
| done_installed_during_census | done_already_present_before_geid_history | 1629 |
| done_appears | done_installed_during_census | 1262 |
| done_installed_during_census | done_ambiguous_marker_missed_pv | 1205 |
| done_ambiguous_no_recent_anchor | done_installed_during_census | 625 |
| done_ambiguous_no_recent_anchor | done_appears | 569 |
| done_appears | done_already_present_before_geid_history | 473 |
| done_ambiguous_marker_missed_pv | done_already_present_before_geid_history | 449 |
| done_already_present_before_geid_history | done_appears | 348 |
| done_already_present_before_geid_history | done_installed_during_census | 265 |
| done_appears | done_ambiguous_marker_missed_pv | 149 |
| done_installed_during_census | done_ambiguous_no_recent_anchor | 119 |
| done_ambiguous_no_recent_anchor | done_already_present_before_geid_history | 108 |
| done_ambiguous_nonmonotonic | done_appears | 64 |
| done_ambiguous_no_recent_anchor | done_ambiguous_marker_missed_pv | 64 |
| done_ambiguous_nonmonotonic | done_already_present_before_geid_history | 48 |
| done_already_present_before_geid_history | done_ambiguous_marker_missed_pv | 34 |
| done_installed_during_census | done_ambiguous_nonmonotonic | 33 |

