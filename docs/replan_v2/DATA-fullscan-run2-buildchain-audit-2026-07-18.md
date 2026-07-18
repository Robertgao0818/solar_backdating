# Data memo: build-chain audit + stale-offset postmortem (2026-07-18)

Status: owner-requested. Two deliverables: (1) a postmortem of how the stale
`target_offset_x_m/y_m` bug (recenter-pilot memo §17,
`DATA-fullscan-run2-recenter-pilot-2026-07-17.md`) got into the RUN 2
production scan; (2) a full field-semantics audit of the build chain from the
census-prediction inventory import through chip-group building, the ISSUE-25
per-target rebuild, the fullscan anchor join, scan-time rendering, interval
decoding, and the deliverable builder — looking for siblings of the same bug
class (a derived/denormalized column whose reference frame changed while its
name stayed the same).

Method: git archaeology on the three bug-site files (this session, main
lane) + three parallel audit lanes (upstream builder columns; scan-time field
consumption; downstream + repo-wide legacy-column grep) + one full-population
quantification (source_grids drift, all 41,393 anchors, no sampling).
Read-only against all real data; scratch outputs only.

## 1. Postmortem: how the stale-offset bug got in

Timeline (verified against git history):

1. **Legacy era** (`jhb_full382..._chipgroups`, 2026-06):
   `build_inventory_chip_groups.py:565-566` computes
   `target_offset_x/y_m = target.centroid − group.center` — correct under
   the one-96m-chip-per-multi-target-group scheme.
2. **2026-07-10, ISSUE-25 pilot** (`badea93`):
   `scripts/validation/issue25_manifest.py:378-397` fully re-derives
   per-target anchor rows and **explicitly sets
   `target_offset_x_m/y_m = 0.0`** (also `chip_id = target_id`,
   `target_label = "T01"`). Verified: all 1,200 rows of the frozen pilot
   manifest carry offset 0.0. `tests/validation/test_issue25_manifest.py`
   asserts it. The contract "per-target chip ⇒ offset 0" was understood and
   implemented — inside the pilot lane only.
3. **2026-07-11** (`93abe31`): `make_fixed_extent_review_renderer` added to
   `run_adaptive_scan.py` — correct against the pilot manifest it was built
   for.
4. **2026-07-13→16**: basemap rebuild downloads per-target 96 m chips via
   `build_per_target_anchors.py` (outputs only anchor_id + bbox corners; its
   own docstring documents the group-box mismatch — for the bbox).
5. **2026-07-16/17** (`b76c1d3`, committed 22:54 on the day the production
   run finished): the historical builder at commit `b76c1d3`, lines 99-101
   (`merged = dict(row)`), passes ALL legacy `chip_targets.csv` columns through, then overrides only
   the 4 `BBOX_FIELDS`. The offset columns ride through stale (group-relative,
   observed 0 to >30 m; true value ≈ 0). The renderer's tolerant read
   (`float(anchor.get("target_offset_x_m") or 0.0)`,
   `run_adaptive_scan.py:482-483`) consumed them without complaint.

**Verdict: not a failure to understand the geometry change.** The change was
correctly understood twice (manifest builder zeroed the offsets; anchor
builder's docstring names the group-vs-disk mismatch for the bbox). The
failure is that the contract lived in two parallel code paths written a week
apart and was never reified into anything checkable:

- **Column name carries no reference frame.** "offset relative to what" is
  unstated; the frame changed, the name didn't, the join passed silently.
- **Two builders for one schema, no shared derivation.** The bake-off
  validated `issue25_manifest.py`'s derivation; production ran a freshly
  written builder (now archived at commit `b76c1d3`) that bypassed the
  validated code path.
- **Wholesale `dict(row)` pass-through** moved 27+ legacy columns across a
  schema boundary with no per-column semantic review.
- **Tolerant consumer reads** (`.get(...) or 0.0`) make "column missing"
  (pilot: correct 0) and "column wrong" (production: stale value)
  indistinguishable.
- **No invariant check anywhere.** A one-line
  `assert |offset| ≤ ε for per-target anchors` (or
  `offset ≈ centroid − bbox_center`) at build time would have caught it.
  `build_per_target_anchors.py`'s own `self_check` even printed the exact
  quantity that went stale (offset of new centers from old group centers,
  p50/p90/max) on rebuild day; row-count and positivity were validated,
  reference frames were not.

## 2. Audit findings

### 2.1 Confirmed production bugs

1. **(known) Stale `target_offset_x_m/y_m`** — sole production consumer
   confirmed to be `run_adaptive_scan.py:482-483`
   (`make_fixed_extent_review_renderer` → the review PNG Gemini scored). No
   second independent production render path found. 99.2% of the 41,393
   `chip_targets.csv` rows carry a nonzero (stale) offset. Pixel math itself
   (`gehi_common.py:642-643/828-829`) is clean; `chip_size_m=96.00` is
   coincidentally identical across both geometry eras; Web-Mercator/UTM
   isotropy verified against a real download manifest (EPSG:3857, equal x/y
   GSD). With offset zeroed, the crop is exactly target-centred (bbox
   midpoint ≡ target centroid to <0.001 m; sub-pixel rounding only).

2. **(new, REAL-IMPACT, small) Stale `source_grids` shifts the ISSUE-26
   census cutoff for 223/41,393 anchors (0.54%).**
   `run_adaptive_scan.py:1907-1963` resolves each anchor's census date as
   `max(last_capture_date)` over the grids in `source_grids` — a column
   computed from the **legacy group** footprint
   (`build_inventory_chip_groups.py:534`) and passed through by the historical
   builder at commit `b76c1d3`, lines 103-108. The per-target re-centred 96 m box
   can touch a different set of ~1 km Vexcel grids. Full-population
   recomputation (nominal 1 km lattice reconstructed from the 382 grid
   centroids; 356/382 snap to <1e-9 m residual; production
   `--vexcel-capture-csv` path confirmed live in `chain_launch.log`):
   13.08% of anchors have a differing true grid set; **223 get a different
   resolved census date; 219/223 shift later by 58 or 62 days** (exactly the
   JHB flight-date gaps Feb-17/Feb-21 → Apr-19), i.e. production used a
   too-early cutoff for those anchors. Not yet traced: whether any of the
   223 actually flip a picked scan window or decision (needs a per-anchor
   cutoff-walk replay). Caveats: lattice is reconstructed (authoritative
   grid GPKG absent from this machine — may exist on koko/Dropbox); 285
   boundary anchors conservatively excluded (223 is a floor).
   Per-anchor result table:
   scratchpad `source_grids_drift_full.csv` + `quantify_source_grids_drift.py`.
   **Recommendation**: fold the fix into the offset-fix rescan — recompute
   `source_grids` from the per-target box (or resolve census dates directly
   from the bbox-grid intersection) in the same geometry_version migration.

### 2.2 Stale-risks / design gaps (no current production impact; on record)

- **No geometry-epoch stamp on scan states.** `scan_state.json` persists no
  geometry; a non-terminal state resumed after an in-place anchors-CSV
  geometry change would silently mix old- and new-geometry rendered evidence
  in one decision. Dormant (RUN 2 has zero non-terminal states; the two
  geometry eras use disjoint `_c`/`_t` anchor-id namespaces) — guard before
  any future in-place regeneration.
- **Legacy-frame consumers with no schema/epoch validation** would reproduce
  the bug if ever repointed at `anchors_all.csv`:
  `pilot_c0_reverse_template_2026_07_12.py` (legacy branch, currently
  correctly bifurcated), `score_chip_group_matrix.py`,
  `build_distillation_set.py` (all internally self-consistent on group-era
  files today). `rescan_pilot_step0.py:205-206` reads the stale offset with
  no A5-style acknowledgment — superseded by `rescan_pilot_run.py`; do not
  reuse for new renders.
- **Production render path is outside the `chip_geometry.py` registry.**
  `run_adaptive_scan.py` never imports the versioned `ChipGeometry` registry;
  the fixed-extent renderer hardcodes its parameters, so the production
  review-crop geometry is not tracked as a named `geometry_version` in
  provenance (D18 governance gap — the offset fix must introduce one).
- **`source_feature_id` is a positional index** assigned before filtering
  (clean today) but contingent on GPKG read order, with no content-hash
  guard on the source inventory; regenerating the GPKG would silently re-key
  every `anchor_id` downstream.
- **Same-name columns with two scopes**: `source_width_m/height_m` and
  `centroid_lon/lat` exist at both group level (packed-group) and target
  level (footprint) across `chip_groups_as_anchors.csv` vs
  `chip_targets.csv`; all current consumers read the correct scope, but a
  suffix-undisciplined merge would silently pick the wrong one.
- `alignment_note` metadata string predates the rebuild and gives no warning
  that offsets need re-derivation — update alongside the fix.

### 2.3 Ruled out (verified clean)

- `infer_install_dates.py`: the 701-row inverted-interval defect is a
  **singleton, not a class** — every other blanking branch
  (`clamp_inverted`, `marker_missed_pv` contradiction) correctly
  reclassifies before blanking; `build_install_dated_deliverable.py` is
  insulated from all geometry columns and its `interval_invariant` gate is
  an existing defense.
- `scan_state.py` / `scan_decision.py` / `chip_lifecycle.py`: persist no
  geometry at all.
- Scorer stack (`score_anchor_presence.py`, `presence_scorer.py`,
  `scoring_provenance.py`), `scan_config.py`, `effective_resolution.py`:
  no geometry-field consumption.
- `rescan_pilot_run.py`: arms A0–A3 intentionally reproduce the stale offset
  (production-faithful baseline); arm A5 correctly zeroes it
  (`dataclasses.replace(marker, offset_x_m=0.0, offset_y_m=0.0)`).
- Marker sign conventions, axis order (`always_xy=True` throughout),
  projection choices in `build_per_target_anchors.py`: verified consistent.
- Full clean-column coverage lists: see the three audit-lane reports
  (session transcript); headline columns `anchor_id`, `centroid_lon/lat`
  (target-level), `source_width_m/height_m` (target-level),
  `search_radius_m`, `chip_size_m`, `chip_half_m`, `confidence`/`score`/
  `sam_score`/`n_merged`, `n_targets`/`target_anchor_ids` all verified.

## 3. Guards to land with the offset fix (recommendations)

1. **Single source of truth for per-target anchor derivation** — extract the
   `issue25_manifest.py:378-397` re-derivation into a shared function; both
   the manifest and any future anchors builder call it. No more
   `dict(row)` pass-through across the group→target schema boundary
   (explicit column allowlist).
2. **Build-time invariants** in the anchors builder: for per-target anchors,
   assert offset ≈ 0 (or recompute as `centroid − bbox_center` and assert
   consistency), assert bbox is square/96 m, assert `source_grids` ⊇
   bbox-grid intersection.
3. **Strict reads in production renderers**: replace `.get(...) or 0.0`
   with required-field reads; missing ≠ zero.
4. **Name the geometry**: route the production review renderer through the
   `chip_geometry.py` registry and stamp `geometry_version` into scoring
   provenance and scan states (also closes the resume-across-epoch gap).
5. **Pre-flight render QA gate**: before any full-population scan, render
   N≈30 random anchors' review crops against the census/Vexcel reference and
   verify the marker encloses the detection (the §15 census-placement check,
   run as a cheap gate instead of a post-hoc forensic).

## 4. Artifacts

- This memo (committed). TRACKER.md updated same day (external-review note).
- Scratchpad (session-local, reproducible read-only):
  `quantify_source_grids_drift.py`, `source_grids_drift_full.csv`
  (41,393-row per-anchor true-vs-stored grid sets + resolved census dates).
- Audit-lane raw reports: session transcript (three lanes + quantification).
