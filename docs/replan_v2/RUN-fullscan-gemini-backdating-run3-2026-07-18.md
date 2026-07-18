# RUN 3 — Full-population clean rescan on anchors_v2 (2026-07-18)

Status: **COMPLETE 2026-07-18T11:35:31Z UTC** (23:35 NZST). Final:
41,393/41,393 (a24 36,322 + a48 5,071), **zero anchors with any
error/failed status** — verified by scanning every scan_state JSON in both
lanes, not just trusting exit codes. See §9 for what actually happened
between launch and completion, including a correction to §6's rerun
assumption.

~~Status: **LAUNCHED 2026-07-18T03:54:34Z**~~ (owner approved cost 2026-07-18; all
5 gates passed; preflight 6/6 chips `skipped_existing`; tmux session `run3`,
log `$FULLSCAN_ROOT/run3_v2/launch.log`).

Successor to [RUN-fullscan-gemini-backdating-2026-07-15.md](RUN-fullscan-gemini-backdating-2026-07-15.md)
(RUN 2, completed 2026-07-17T07:31:31Z). RUN 2's review-crop rendering was
contaminated by stale `target_offset_x_m/y_m` (root cause:
[DATA-fullscan-run2-buildchain-audit-2026-07-18.md](DATA-fullscan-run2-buildchain-audit-2026-07-18.md));
ISSUE-27 (commit 762ec50) rebuilt the clean anchors_v2 package. RUN 3 rescans
from scratch consuming anchors_v2.

## 1. Scope

- **Owner decision (2026-07-18, supersedes the pending population-(a)
  15,882-anchor option in ISSUE-27 Decision 7 / TRACKER):** full clean rescan
  of all **41,393** anchors (A24 36,322 / A48 5,071), consuming
  `anchors_v2/` exclusively.
- RUN 2 outputs (`$FULLSCAN_ROOT/{a24,a48,...}`) are **immutable provenance**
  — never overwritten, never deleted. RUN 3 writes only under
  `$FULLSCAN_ROOT/run3_v2/`.
- Out of scope (unchanged owner decisions): `infer_install_dates.py` ~line 701
  inverted-interval patch; 3/4-state observation schema; corrupt-frame QA
  pass (external review 第一梯队 items 2–4).

## 2. Frozen decisions (carried from RUN 2 unless noted)

| Decision | Value | Provenance |
|---|---|---|
| Model (both rounds) | `gemini-3.1-flash-lite` | RUN 2 §10 |
| Pace | WORKERS_TOTAL=80 / QPS_TOTAL=8, one lane at a time | owner-frozen 2026-07-17 |
| Topology | sequential lanes a24 → a48 | RUN 2 v2 launcher |
| GEHI traffic during scan | zero (`--no-live-gehi`, offline TM+Wayback catalogs, `--offline-require-chip-on-disk`) | RUN 2 §10–11 |
| Anchors | `anchors_v2/` package (ISSUE-27), gate on `anchors_v2_issue27` schema invariants + `preflight_qa_30/.approved` | **new** |
| Geometry | `fullscan_target96_review{24,48}_v2`; fresh scan_states (resume guard hard-fails v1-stamped states) | **new**, ISSUE-27 |
| Offline catalog | `gehi_vintage_candidates_full_run3.csv` = RUN 2 catalog + census_v2 top-up rows; RUN 2 catalog stays byte-identical | **new**, §4 |
| Chips | reuse `basemap_rebuild_2026-07-13/chips` (bbox v1≡v2) + incremental top-up frames in same dir | owner decision 2026-07-18 |

## 3. Pre-run: census-shift top-up (launch blocker, executed 2026-07-18)

The 223 census-date-shifted anchors (ISSUE-27 reconciliation, delta histogram
{−58:1, +4:3, +58:146, +62:73}) include 186 with <3 post-census frames on
disk under the corrected `census_v2` cutoff
(`anchors_v2/census_shift_topup_check_2026-07-18.csv`; the 186-row list was
independently re-derived and matched 223/223).

- Availability data pulled 2026-07-12 already covered the full window; the
  shortfall was candidate-selection staleness (old `census_v1` cutoff), not
  missing GEHI data: **185/186 anchors topped up with zero live GEHI calls**.
- 1 anchor (`..._t00026803`) required live queries: no later vintage exists →
  genuine short tail, proceeds under the cutoff-walk degrade-to-last-available
  behavior (`run_adaptive_scan.py` `_catalog_cutoff_candidates`).
- 366 new candidate rows (251 TM + 115 Wayback) downloaded to the shared
  chips dir; merged catalog written as `gehi_vintage_candidates_full_run3.csv`.
- Top-up artifacts: `$FULLSCAN_ROOT/run3_v2/topup_2026-07-18/`
  (recompute reconciliation, classification, download logs,
  `short_tail_final.csv`).
- Driver: `scripts/temporal/topup_census_shift_frames.py` (reuses
  `compute_cutoff_max_date` verbatim).

## 4. Launcher

`scripts/temporal/run_fullscan_backdating_v3.sh` — parameterized copy of the
v2 launcher. Differences: gate 0 refuses `RUN_ROOT` == RUN 2 root; gate 2
validates the `anchors_v2_issue27` summary schema (`invariants.passed`,
`row_count`/`unique_anchor_ids` == 41,393, `geometry_versions` ==
`fullscan_target96_review{24,48}_v2`) and requires
`preflight_qa_30/.approved`; anchors CSVs read from `ANCHORS_DIR`
(default `$FULLSCAN_ROOT/anchors_v2`); offline catalog defaults to
`gehi_vintage_candidates_full_run3.csv`; log prefix `[FULLSCAN3]`.
Gates 1/3/4/5 (inputs, gateway health + ≥80 antigravity slots, authenticated
1-anchor preflight with chip cache-hit assertion, storage projection with
20 GiB reserve) carried verbatim from v2.

Launch (tmux, per CLAUDE.md rule 6):

```bash
tmux new -d -s run3 'bash scripts/temporal/run_fullscan_backdating_v3.sh'
```

Resume after interruption: rerun the same command (terminal scan states are
skipped; `GeometryVersionMismatchError` on resume means a stale v1 state got
mixed in — stop and investigate, do not force).

## 5. Expected load / duration (from RUN 2 measured, not estimated)

RUN 2 measured: 86,774 Gemini batch calls (+~2% HTTP chunking) scoring
288,276 chip observations over 41,393 anchors (≈2.1 calls, ≈7.0 obs per
anchor); sustained 8.13 obs/s — pure QPS-bound at the 8 qps gateway cap.
RUN 3 is the same population + 366 extra frames:

- **≈87k Gemini API calls / ≈289k scored observations**
- **≈10h wall clock** (a24 ≈9.5h, a48 ≈25min), assuming the same 80/8 pace
- Storage: <1.5 GiB run root (RUN 2 measured ~16 KB/anchor ×2 safety)

## 6. Known-expected outcomes

- RUN 2's 3 permanent errors are all expected to RESOLVE in RUN 3:
  `t00033551` (chip decoder error) fixed pre-launch via quarantine +
  re-download. `t00007261`/`t00007262` ("absent from offline TM catalog"):
  root-caused 2026-07-18 post-launch as a **probe-failure artifact, not
  missing data** — their group `c0012722` hit the 07-12 403-ban cohort and
  the retry hit a 100s HTTP timeout, so all availability CSVs carried 0 TM
  rows; a fresh live probe returned **21 TM dates** (2019-01-30 →
  2025-05-30). Owner approved a mini-topup (TM chips + catalog rows appended
  to the run3 catalog mid-run; safe — the running lane read the catalog at
  startup). Secondary code-level cause, documented not fixed:
  `load_offline_provider_catalogs` only keys `tm_dates_by_anchor` on ≥1 TM
  row, so a Wayback-only anchor is indistinguishable from one absent from
  the catalog and errors before Wayback is consulted.
- `done_ambiguous_no_recent_anchor` cohort (1,433 in RUN 2) expected at a
  similar magnitude.
- **Expected intervention ~9.5h after launch (wrapper fail-fast gap, same as
  RUN 2):** the a24 lane will finish in substance but exit nonzero because
  t00007261/t00007262 fail during this pass (the in-memory catalog predates
  the mini-topup). Unblock: verify the failed set is exactly those 2, then
  simply **rerun the launcher** (no `.done` override) — the rerun re-reads
  the updated run3 catalog, re-attempts the 2 error-state anchors, and on
  their success exits 0, writes `a24/.done` itself, and cascades to a48.
  Expected RUN 3 end state: **41,393/41,393 with zero permanent errors**.
  (Fallback if the mini-topup chips somehow don't resolve: the RUN-2-style
  `touch $RUN_ROOT/a24/.done` + rerun override still applies.)
- **This intervention is AUTOMATED**: `$RUN_ROOT/chain_rerun_after_a24.sh`
  (tmux `run3_chain`, armed 2026-07-18T04:38Z, log `chain_rerun.log`) waits
  for the first launcher pass to exit, verifies a24 has all 36,322 states
  and the non-clean set is ⊆ {t00007261, t00007262}, then reruns the
  launcher once. Any other end state → writes `$RUN_ROOT/NEEDS_ATTENTION`
  and touches nothing.

## 7. Post-run steps (not in launcher)

1. Reconcile vs RUN 2: reuse `rescan_pilot_analyze.py` metric definitions
   (contradiction-frame flip rate, status-changed rate, bracket-changed
   rate; RUN 2 pilot baseline: A0→A5 flip 3.3%→46.7%, bracket recovery
   11/30). Marker-off-structure rate is human-graded (recenter-pilot §15
   protocol) — separate owner-optional pass.
2. Rebuild install intervals: `infer_install_dates.py` on run3 scan states
   (the ~line-701 inverted-interval defect stays unpatched per owner
   decision; `interval_invariant` gate downstream insulates the deliverable).
3. Rebuild econ deliverable: `build_install_dated_deliverable.py` with its
   self-contained `interval_invariant` gate, consuming anchors_v2 + run3
   intervals.
4. Update TRACKER.md + this doc with completion status.

## 8. Provenance / no-touch list

- `$FULLSCAN_ROOT/{a24,a48}/`, root-level `anchors_*.csv` (v1),
  `anchors_summary.json` (v1), `preflight/`, `.complete`, all RUN 2 logs —
  read-only provenance.
- `basemap_rebuild_2026-07-13/gehi_vintage_candidates_full.csv` — byte-identical
  (RUN 3 uses the `_run3.csv` merged copy).
- `rescan_pilot_*.py` intentionally consume v1 stale columns for forensics —
  do not "fix".

## 9. What actually happened (completion notes, 2026-07-18T11:35:31Z)

**§6's claim that "a plain rerun of the launcher... re-attempts the 2
error-state anchors" was wrong.** `run_adaptive_scan.py::run_one_anchor`
treats any existing scan_state with a terminal status — including
`done_ambiguous_orchestrator_error` — as done and returns it unchanged
without `--force-restart` (`is_terminal` check, `run_adaptive_scan.py`
~line 1149/1166). Two consecutive plain reruns (the scheduled 09:00
restart, and a manual 11:03 rerun) both silently no-op'd on t00007261/
t00007262: each walked all 36,322 states, found the existing error state,
and re-exited nonzero in under 2 minutes — no new API calls, no retry. The
mini-topup catalog fix from §6 was correct; it just needed a real retry to
land.

**Actual fix:** the same filtered-CSV + `--force-restart` mechanism
already used by `rescan_gemini_failed_run3.sh` for the `gemini_failed`
cohort, applied ad hoc to a 2-row CSV for just these two anchors (2
workers/2qps, same `a24/scan_states` dir). Both resolved on the first real
attempt: `done_installed_during_census`. `a24/.done` was then written
manually (the documented fallback in §6, invoked for a different reason
than anticipated) and the launcher rerun, which correctly skipped a24 and
ran a48 clean (24 min, matching the ~25 min estimate). `.complete` landed
at 11:32:38Z; `rescan_gf` (armed since 09:48Z) woke within ~90s and
cleared all 38 `done_ambiguous_gemini_failed` a24 anchors via the same
force-restart pattern (`done_appears`=21, `done_installed_during_census`=16,
`done_already_present_before_geid_history`=1). a48 had zero
`gemini_failed`, nothing to rescan there. "All lanes swept" 11:35:31Z.

**Lesson for any future run reusing this launcher/chain pattern:** never
rely on a bare launcher rerun to retry a terminal-with-error anchor state.
`chain_rerun_after_a24.sh`'s "single auto-rerun on EXPECTED failure set"
design has the same latent bug — if reused, replace the "rerun the
launcher" action with a filtered-CSV `--force-restart` pass instead.

Full narrative + a24-vs-RUN2 status-distribution comparison:
[memory `fullscan-run3-2026-07-18`] (not in-repo; ask the assistant to
recall it, or see `docs/replan_v2/TRACKER.md` for the tracked summary under
the Slice 27 "RUN 3 completion note").

## 10. Post-run pipeline (§7 items 1-3), executed 2026-07-18

1. **Reconcile vs RUN 2** —
   [`DATA-fullscan-run3-reconcile-vs-run2-2026-07-18.md`](DATA-fullscan-run3-reconcile-vs-run2-2026-07-18.md),
   script `scripts/temporal/reconcile_run2_vs_run3.py` (reuses
   `rescan_pilot_analyze.py`'s flip-rate / status-changed-rate /
   bracket-changed-rate definitions, applied to the full 41,393-anchor
   join instead of the 30-anchor pilot sample — clean 1:1 join, 0
   RUN2-only/RUN3-only anchors). Headline: 49.3% status_changed; 82.0% of
   RUN2's 4,645 `done_ambiguous_marker_missed_pv` (census-GT contradiction)
   anchors resolved to a real dated status in RUN3; coverage-class
   transitions show 81.1% undated→dated recovery vs 6.7% dated→undated
   regression; bounded coverage 78.4%→83.2%. Directionally confirms the
   ISSUE-27 offset fix; independent QA sampling still needed before calling
   it a confirmed accuracy gain rather than a different failure-mode mix.
2. **Rebuild install intervals** — `infer_install_dates.py` run separately
   on `run3_v2/a24/scan_states` and `run3_v2/a48/scan_states` (identical
   invocation to the RUN2 pattern in
   [`DATA-fullscan-run2-install-intervals-2026-07-17.md`](DATA-fullscan-run2-install-intervals-2026-07-17.md)),
   stitched into `run3_v2/intervals/install_intervals_all.csv` (41,393
   rows, 36,322 + 5,071 exact). Same unpatched line-701-class defect as
   RUN2 (inverted-interval `done_appears` rows keep the label with blank
   dates) — left as-is per the same owner decision.
3. **Rebuild econ deliverable** — `build_install_dated_deliverable.py`
   against `anchors_v2/anchors_all.csv` + the new intervals CSV, output
   `deliverable_run3_2026-07-18/jhb_full382_fpcut_install_dated_2026-07-18-run3.{csv,gpkg}`
   + `README_install_dated_2026-07-18-run3.md`. All six build gates pass
   (row_count, sfid_bijective, interval_invariant, flight_date_ceiling,
   coverage_reconcile, csv_gpkg_centroid_agreement) — zero violations.
4. **Tracker/doc update** — this section + `docs/replan_v2/TRACKER.md`
   Slice 27 note, same pass.
