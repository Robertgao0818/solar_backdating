# ISSUE-27: Anchors-table v2 clean rebuild (offset fix, source_grids recompute, legacy builder archived)

Status: implemented — 30-anchor pre-flight artifact ready; owner visual approval pending
Phase: 4 — Scan / anchors geometry (governed by D18 / ISSUE-19 geometry-version policy)
Blocked by: —
Opened: 2026-07-18 — from the build-chain audit + stale-offset postmortem
([DATA-fullscan-run2-buildchain-audit-2026-07-18.md](DATA-fullscan-run2-buildchain-audit-2026-07-18.md)),
owner decision same day: **rewrite clean, do not patch; archive the legacy
builder**.

## Problem statement (short — full evidence in the audit memo)

RUN 2's `anchors_all.csv` was built by the legacy builder archived at commit
`b76c1d3` (removed from the working tree in this slice) via `merged = dict(row)` wholesale pass-through of legacy
group-era `chip_targets.csv` columns, overriding only the 4 bbox corners.
Two columns are confirmed stale under ISSUE-25 per-target geometry:

1. `target_offset_x_m/y_m` — group-relative (0 to >30 m); true per-target
   value ≈ 0. Fed by `run_adaptive_scan.py:482-483` into every RUN 2 review
   crop (recenter-pilot memo §17; ~73% of blind-bucket b2 placement errors).
2. `source_grids` — computed from the legacy **group** footprint; the
   per-target 96 m box's true grid intersection differs for 13.08% of
   anchors, changing the ISSUE-26 resolved census cutoff for **223/41,393
   anchors** (219 of them 58/62 days too early). Quantified full-population
   in the audit memo §2.1.

The correct per-target derivation already exists and is validated:
`scripts/validation/issue25_manifest.py:378-397` (pilot lane; zeroes offsets,
resets `target_index/target_label`, sets `chip_id = target_id`; asserted by
`tests/validation/test_issue25_manifest.py`). The production builder bypassed
it. Root cause class: a derived column whose reference frame changed while
its name stayed the same, crossing a schema boundary with no invariant check.

## Decisions already made (owner, 2026-07-18 — do NOT re-litigate)

1. **Clean rewrite, not a patch.** New builder `build_fullscan_anchors_v2.py`.
2. **Archive `build_fullscan_anchors.py`** (`git rm`; git history at
   `b76c1d3` is the archive). Rationale: leaving it in the working tree keeps
   feeding future agents a worked example of the exact anti-pattern
   (wholesale pass-through) that caused the bug. RUN 2 provenance is
   unaffected: `anchors_summary.json` + the audit memo record the builder by
   commit hash, and the v1 outputs on disk are preserved untouched.
3. **Shared derivation, single code path.** Extract the per-target row
   derivation out of `issue25_manifest.py` into a shared module; both the
   manifest builder and v2 call it.
4. **Offset is recomputed + asserted, not hardcoded.**
   `offset = centroid − bbox_center`, then assert `|offset| < ε`. A future
   geometry change must fail the build loudly, not pass silently.
5. **`source_grids` recomputed from the per-target bbox** (fixes the 223
   census-cutoff anchors in the same migration).
6. **Named geometry_version, D18 discipline.** Register the per-target
   review geometry in `scripts/temporal/chip_geometry.py`; stamp it into
   every v2 row and the summary. v1 `anchors_all.csv` stays on disk
   untouched as the RUN 2 provenance record; v2 writes to a new versioned
   path. This is a documented migration, not a hotfix.
7. **The rescan launch itself is NOT part of this slice** — separate owner
   decision (population (a), 15,882 anchors, pending).

## Deliverables

### D1. Shared derivation module

- New `scripts/temporal/anchor_derivation.py` (or equivalent under
  `scripts/temporal/`): a function that takes a legacy `chip_targets.csv`
  row + chip_size_m and returns the canonical per-target anchor row —
  the logic currently at `issue25_manifest.py:378-397` (offset 0 semantics
  via recompute, `chip_id = target_id`, `target_index = 1`,
  `target_label = "T01"`, `legacy_group_anchor_id` crosswalk, per-target
  96 m box from the target's own centroid, EPSG:32735 / `always_xy=True`
  conventions identical to `build_per_target_anchors.py:82-89`).
- `issue25_manifest.py` refactored to call it; its existing tests stay green.

### D2. `build_fullscan_anchors_v2.py`

- Same three frozen inputs as v1 (per-target bbox CSV, `chip_targets.csv`,
  `chip_groups_as_anchors.csv` retained only for crosswalk/reporting), plus
  a grid-geometry source for D3.
- **Explicit column allowlist** — every output column is one of:
  (a) recomputed via D1; (b) declared safe-to-carry with a one-line reason
  in the source (per-target semantics: `source_width_m`, `source_height_m`,
  `source_area_m2`, `confidence`, `score`, `sam_score`, `n_merged`,
  `centroid_lon/lat`, `source_feature_id`, `source_fid`, `source_grid`,
  `region_key`, `grid_id`); (c) renamed `legacy_*` crosswalk. An unknown
  input column must FAIL the build, not pass through.
- Routing columns (`chip_arm`, `review_extent_m`, rule/amendment ids) reuse
  `issue25_stage_c.route_chip_arm` exactly as v1 did (byte-consistent with
  the frozen Stage-2 policy).
- `geometry_version` column on every row (from D5).
- Output: new versioned directory (e.g.
  `~/zasolar_data/geid_temporal/fullscan_gemini_backdating_2026-07/anchors_v2/`)
  with `anchors_all.csv`, `anchors_A24.csv`, `anchors_A48.csv`,
  `anchors_summary.json` (input sha256s, invariant results, census-date
  delta report vs v1). **Never overwrite the v1 files.**

### D3. `source_grids` recompute

- True grid set = per-target 96 m bbox ∩ grid polygons.
- Preferred geometry source: the authoritative
  `jhb_task_grid_unified.gpkg` / `joburg_task_grid.gpkg` (referenced by
  `ZAsolar regions.yaml:366` and `scripts/imagery/download_vexcel_jhb382.py:30`;
  **missing from this machine** — check koko (`ssh koko@koko-82xm`) and
  Dropbox first). Fallback: the reconstructed 1 km EPSG:32735 lattice from
  the 382 grid centroids (356/382 snap <1e-9 m; 26 edge cells approximate —
  method + script in scratchpad `quantify_source_grids_drift.py`, results in
  `source_grids_drift_full.csv`); if the fallback is used, record that fact
  and the 26 approximate grid ids in `anchors_summary.json`.
- Expected reconciliation: ~5,414 anchors' grid sets change; **223 anchors'
  resolved census date changes (219 later by 58/62 days, deltas only from
  {−58, +4, +58, +62})**. The v2 summary must report the actual counts and
  flag any deviation from these expectations as a stop-and-investigate.

### D4. Build-time invariants (hard failures, results echoed in summary)

- bbox is square, 96.0 m ± 0.01 m per side (metric CRS).
- bbox center ≡ target centroid, < 0.01 m.
- recomputed `|target_offset| < 0.01 m` on every row.
- `source_grids` non-empty; ⊇ {grid of `centroid`}.
- row count == 41,393; anchor_id uniqueness; arm split counts == v1's
  (36,322 / 5,071) unless a documented reason differs.

### D5. Geometry registry + renderer strict reads

- Register the per-target review geometry as a named version in
  `scripts/temporal/chip_geometry.py` (production render path currently
  bypasses the registry entirely — audit memo §2.2).
- `run_adaptive_scan.py::make_fixed_extent_review_renderer`: replace
  `.get(...) or 0.0` tolerant reads of `target_offset_x_m/y_m` with strict
  required-field reads (missing column = hard error), and thread the
  anchor's `geometry_version` into scoring provenance.
- Resume guard (closes audit memo §2.2's dormant risk): when a scan resumes
  a non-terminal state, fail loudly if the state was created under a
  different `geometry_version` than the current anchors row (stamp it into
  new scan states; treat absent stamp in old states as legacy-v1).

### D6. Archive the legacy builder

- `git rm scripts/temporal/build_fullscan_anchors.py`.
- Grep and update every reference to the filename (known:
  `run_fullscan_backdating.sh` / `run_fullscan_backdating_v2.sh` header
  comments/steps, docs mentions incl. CLAUDE.md quick-start if present,
  memory files are out of repo scope). Runner scripts that would invoke it
  must point at v2 or state the anchors file is prebuilt.
- Tombstone note: one line in this issue doc's log + the audit memo already
  records commit `b76c1d3` as the historical location.

### D7. Tests + pre-flight render QA gate

- Unit tests: D1 derivation (offset recompute + assert, crosswalk columns),
  D2 allowlist (an unexpected input column fails), D4 invariants (violations
  fail), D3 grid recompute on synthetic fixtures.
- Existing suites stay green (`pytest tests/` smoke gate; the one known
  pre-existing unrelated failure is `test_pilot_sequence_head`).
- Pre-flight QA gate script (cheap, reusable): render N=30 random v2
  anchors' review crops (A24/A48 mix) with the production renderer and the
  census/Vexcel reference side by side; a human (or the owner) eyeballs that
  the marker encloses the detection before any full-population scan consumes
  v2. Modeled on the recenter-pilot §15 census-placement check, run as a
  gate instead of a post-hoc forensic.

### D8. Install-dated deliverable builder rewired to v2 (added 2026-07-18)

- `scripts/temporal/build_install_dated_deliverable.py` (+ its test) — the
  econ-analysis deliverable builder born in the 2026-07-17 install-intervals
  work — is adopted into this issue's deliverable list: its `--anchors-csv`
  input now points at the ISSUE-27 v2 `anchors_all.csv` (v1 builder archived
  at `b76c1d3`), and the 2026-07-18 build-chain audit lists it as a v2
  consumer (geometry-column-insulated, own `interval_invariant` gate).
  Owner confirmed keep + in-scope on 2026-07-18.

## Verification tasks for the executing agent (check before coding)

1. Confirm the audited consumer list of `anchors_all.csv` columns is complete
   for the scan path you touch (audit memo §2 has the three-lane coverage
   lists; `rescan_pilot_*.py` intentionally consume the stale column for
   forensics — do not "fix" those).
2. Locate the authoritative grid GPKG (koko, Dropbox) before falling back to
   the reconstructed lattice.
3. Check `run_fullscan_backdating*.sh` for how the anchors CSV path is wired
   (env var vs hardcoded) and where `.done`-gating assumes v1 paths.
4. Confirm `issue25_manifest.py`'s frozen outputs are NOT regenerated by the
   refactor (the manifest CSVs in `docs/replan_v2/issue25_manifest_20260710/`
   are frozen artifacts; refactor must be behavior-identical — consider a
   golden-file test against the frozen manifest).
5. Sanity-check the census-date delta reconciliation (D3) against the
   scratchpad CSV if still present; regenerate from scratch if not.

## Acceptance criteria

- [x] D1 shared derivation module extracted; `issue25_manifest.py` calls it;
      tests + frozen-manifest compatibility check pass.
- [x] D2 `build_fullscan_anchors_v2.py` built 41,393 rows to the new
      versioned output dir with explicit allowlists; unknown columns fail.
- [x] D3 `source_grids` recomputed; summary reports 5,414 grid-set changes,
      223 census-date changes, and exact {−58,+4,+58,+62} reconciliation.
- [x] D4 invariants are hard failures and all pass on the real inputs.
- [x] D5 geometry_version registered, renderer reads strict, version
      stamped into new scan states + scoring provenance; resume guard in
      place.
- [x] D6 legacy builder removed from the working tree; remaining filename
      mentions are tombstones pointing to commit `b76c1d3`.
- [x] D7 code tests pass (full suite: 1,298 passed / 13 skipped / one known
      unrelated `test_pilot_sequence_head` fixture failure — re-verified
      independently 2026-07-18); balanced 15+15 pre-flight QA strip produced.
      Owner reviewed the full strip 2026-07-18 (pure Vexcel zoom extracts, no
      placement issues) — `preflight_qa_30/.approved` created.
- [x] D8 install-dated deliverable builder adopted into scope and rewired to
      the v2 anchors (owner decision 2026-07-18).

## Execution log (2026-07-18)

- Built the immutable v2 package at
  `~/zasolar_data/geid_temporal/fullscan_gemini_backdating_2026-07/anchors_v2/`:
  41,393 rows, A24=36,322, A48=5,071. v1 files were not overwritten.
- Authoritative grid polygons were not recoverable locally, on Dropbox, or on
  `koko`; used the documented centroid-lattice fallback. The build summary
  records all 60 centroid-shifted/non-fullish cells (>1 m residual), the 26
  severe cells (>200 m), and 285 anchors whose bbox also touches an unresolved
  outside-lattice cell. Every anchor still has a non-empty grid set containing
  its centroid grid.
- Reconciliation stop gate passed exactly: 5,414 changed grid sets; 223 changed
  census dates; delta histogram −58:1, +4:3, +58:146, +62:73.
- Real-input invariant maxima: bbox side error 1.9e-9 m, bbox-centre error
  1.9e-9 m, persisted target-offset norm 0; all population, uniqueness, grid,
  and arm-count gates passed.
- Produced the deterministic placement gate at
  `anchors_v2/preflight_qa_30/index.html` with 15 A24 + 15 A48 anchors. Four
  spot-checks visibly enclosed the census PV footprint; the owner must review
  the full strip before creating `preflight_qa_30/.approved` or launching any
  v2-consuming scan.
- Tombstone: the removed v1 builder remains available in git history at commit
  `b76c1d3`.
- 2026-07-18 (post-session): full `pytest tests/` re-run independently confirmed
  1,298 passed / 13 skipped / 1 known unrelated failure. Owner reviewed the full
  30-anchor QA strip and approved (crops are pure Vexcel zoom extracts, all
  markers enclose the detections); `preflight_qa_30/.approved` written. D7
  closed; issue complete.

## Out of scope (tracked elsewhere)

- Launching the offset-fix rescan (owner decision; population (a),
  15,882 anchors, ~3.5–4 h).
- Observation-schema three/four-state change (REVIEW memo §4 item 2).
- `infer_install_dates.py` 701-row inverted-interval patch (owner decision,
  install-intervals memo).
- Upstream segmentation-precision residual (~27% of b2; census-side).

## References

- [DATA-fullscan-run2-buildchain-audit-2026-07-18.md](DATA-fullscan-run2-buildchain-audit-2026-07-18.md)
  (postmortem + full audit; §3 is the guard list this slice implements)
- [DATA-fullscan-run2-recenter-pilot-2026-07-17.md](DATA-fullscan-run2-recenter-pilot-2026-07-17.md)
  §17 (bug discovery + dose-response)
- [DATA-fullscan-run2-rescan-pilot-2026-07-17.md](DATA-fullscan-run2-rescan-pilot-2026-07-17.md)
  (A5 offset-zero recovery quantification)
- `scripts/validation/issue25_manifest.py:378-397` (reference-correct
  derivation), `scripts/temporal/build_per_target_anchors.py` (bbox
  derivation + self-check), ISSUE-19 (geometry-version policy), ISSUE-26
  (census-cutoff semantics)
