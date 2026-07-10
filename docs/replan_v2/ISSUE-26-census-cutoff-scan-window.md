# ISSUE-26: Census-date scan-window cutoff (download + decoder semantics)

Status: ready-for-agent
Phase: 4 — Scan / GEHI provider
Blocked by: —
Opened: 2026-07-10 — three-agent read-only audit (bug-locator /
data-footprint / impact-analyst) confirmed the defect and quantified its
impact on the 2026-06-02 production run.

## Problem statement

The adaptive scan does not align its vintage window with the census imagery
date. The catalog upper bound is a **global static constant**
(`catalog_max_date="2025-12-31"`, `scripts/temporal/scan_config.py:31-47`,
mirrored in `configs/geid_anchor_presence.yaml:37-38`) that ignores region
and census vintage entirely. Worse, round planning **actively anchors on the
newest available vintage**: `scan_decision.py::plan_initial_round`
(L166-197) always picks the last element of the vintage list, and
`plan_anchor_recovery_round` (L328-362) picks the most recent unscored
vintage (`reverse=True`). For JHB (census flights 2024-02-17 → 2024-04-19)
this pulls 2024-H2/2025 frames into Round 1 scoring.

The census date is already computed in-code —
`run_adaptive_scan.py::_resolve_census_mid_date` (L1014-1041) reads
`census_imagery_mid_date` from the main repo's `regions.yaml` — but it is
only threaded into the Gemini scoring prompt
(`_score_batch_picks_chunked` → `scorer.batch(..., census_mid_date_iso=...)`,
L479/L531). It never clips the catalog or the pick planner.

## Measured impact (2026-06-02 full run, 15,859 anchors)

Evidence: `~/zasolar_data/geid_temporal/jhb_full382_fpcut_scan_2026-06-02/`
(`README_install_dated_2026-06-04.md`, `pre_clamp_backup_2026-06-04/`,
`scan_states/*.json`).

- **Result contamination (post-hoc patched):** pre-patch, **9,308 anchors**
  had `earliest_present` after the flight date (2024-H2/2025 — physically
  impossible). The 2026-06-04 report-layer clamp
  (`infer_install_dates.py --census-mid-date` / per-grid Vexcel
  `present_clamp`, L293-400) tightened them in place. Current deliverable
  verified clean (max date = 2024-04-19).
- **Completeness loss (NOT repaired, irreversible under current scan):**
  - **371 anchors** terminated `done_ambiguous_nonmonotonic` —
    `scan_decision.py::is_nonmonotonic` (L135-145) kills the whole anchor
    on any present→absent transition, so a single phantom/washed
    post-census frame ends the scan with no recovery path.
  - **1,023 anchors → 1,820 polygons** reclassified
    `done_ambiguous_clamp_inverted` (even last-absent post-dates the
    flight); point-dated fell 38,351 → 36,531.
- **Volume waste is NOT the cost:** GE availability after census is
  naturally sparse — mean 2.2 post-census frames/anchor (median 2); frames
  beyond a keep-3 policy are 0.7–4.9% of post-census frames (~23 MB
  full-run estimate). DO NOT justify this fix on bandwidth; justify it on
  the ≥2,200-anchor completeness loss above.
- The decoder itself is date-blind: `src/solar_backdating/estimators/
  changepoint.py::estimate_changepoint` (L243-278) consumes all
  observations; its `clamp.ceiling_date`/`census_end_date` are
  report-layer only (comment at L72-76 — "never mutate the posterior").
- CT is mechanically exposed too (`aerial_2025`,
  `census_imagery_mid_date: 2025-06-30` vs static 2025-12-31) but has no
  backdating data yet — fix must land before any CT scan.
- Related but distinct: ISSUE-03's "2024 dip = clamp artifact" is an EB
  prior / interval-midpoint effect. DO NOT merge the two mechanisms in
  analysis or fix.

## What to build

Two layers; the decoder-semantics layer is the one that prevents the
completeness loss from recurring.

1. **Catalog/planner cutoff (download layer).** Per-anchor vintage upper
   bound = census flight date + N reference frames (N=2–3, config knob,
   default 3). Touch points:
   - `scan_config.py::AdaptiveScanConfig.catalog_max_date` — allow
     per-region/per-anchor override instead of global static.
   - `run_adaptive_scan.py::_fetch_real_vintage_catalog` (L897-975) and
     `make_vintage_check` (L372-426) — pass the computed per-anchor bound
     to `fetch_availability_for_anchor` instead of the static constant.
   - `run_adaptive_scan.py::main` (`census_by_anchor` construction,
     L1112-1117) — natural injection point for the per-anchor bound,
     reusing `_resolve_census_mid_date`.
   - `scan_decision.py::plan_initial_round` + `select_evenly_spaced_picks`
     (L67-102) and `plan_anchor_recovery_round` — plan over the truncated
     list; the "newest end" anchor pick must not reach past the cutoff.

2. **Post-census frames are reference-only (decoder/termination
   semantics).** The census mosaic itself is the strongest presence
   evidence at its date. Frames after the census date MUST NOT act as
   evidence that can kill or move an estimate: exclude them from
   `is_nonmonotonic` (an absent reading after census presence is a frame-
   quality signal, not a removal signal) and from changepoint likelihood;
   keep them only as human-review reference. This is what prevents the
   371-anchor nonmonotonic kill and the 1,023-anchor clamp_inverted class.

## Acceptance criteria

- AC1: scan of a JHB anchor produces no picks later than
  flight_date + N frames; per-anchor bound recorded in scan-state
  provenance.
- AC2: `is_nonmonotonic` and the changepoint observation set exclude
  post-census frames; unit test covering a phantom absent frame after
  census presence (must NOT terminate ambiguous).
- AC3: re-scan (or replayed decode) of the 371 nonmonotonic + 1,023
  clamp_inverted anchors, reporting how many convert to clean
  `done_appears` / point-dated — this is the fix's headline number.
- AC4: CT path verified (config-only check; no CT scan required) — the
  bound resolves from `regions.yaml` for cape_town without code edits.
- AC5: the 2026-06-04 report-layer clamp remains in place (defense in
  depth), but a post-fix run must show 0 anchors needing it.

## Non-goals

- No re-run of the full 382-grid census scan inside this issue; AC3 is a
  bounded replay of the ~1,400 affected anchors only.
- No change to ISSUE-03 EB-prior/interval-midpoint machinery.
- No cleanup of the 2.46× duplicate chip copies across rep/quarantine dirs
  (separate hygiene task; different root cause).
