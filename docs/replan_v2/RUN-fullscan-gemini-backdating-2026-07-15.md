# RUN — Full-population Gemini backdating scan (2026-07-15)

Status: **designed; launch gated on basemap download completion.**
Owner session: 2026-07-15 evening. Executor: background monitor teammate.

## 1. Scope

One production backdating pass over the full census target population
(41,393 targets / 15,859 chip groups / 356 grids, from
`jhb_full382_unified_A_merge01_c0925_fpcut_2026-06-01.gpkg`), consuming the
per-target 96 m chips downloaded by the `basemap_rebuild_2026-07-13` run
(1,197,86x candidate rows, home+koko split). Output: per-target adaptive scan
states + Gemini audit + scoring provenance, i.e. the inputs for
`infer_install_dates.py` interval estimation and for DINOv3 distillation
labels.

## 2. Frozen decisions (with provenance)

| decision | value | source |
|---|---|---|
| Model (both round tiers) | `gemini-3.1-flash-lite` | ISSUE-25 Stage-A model smoke freeze ([DATA-issue25-model-smoke-2026-07-10.md](DATA-issue25-model-smoke-2026-07-10.md)) |
| Decoder | `changepoint` | ISSUE-25 freeze |
| Chip geometry | `routed_A24_A48_cut40`: `source_area_m2 >= 40` → A48, else A24 | Amendment 2026-07-11 ([DATA-issue25-stage1-routed-counterfactual-2026-07-11.md](DATA-issue25-stage1-routed-counterfactual-2026-07-11.md)); reuses `route_chip_arm` from `issue25_stage_c.py` |
| Provider | `Merged` (TM + Wayback union, TM precedence on date collision) | Stage-C production shape (`run_issue25_stage_c_stage1.sh`) |
| Window floor | 2019-01-01 (`catalog_min_date`, config) | basemap-rebuild decision 2026-07-12 |
| Window ceiling | ISSUE-26 per-anchor cutoff: per-grid Vexcel census date + 3 post-census reference frames, hard cap 2025-12-31 | commit cb25ee3; scan MUST pass `--vexcel-capture-csv` (`ZAsolar/data/analysis/vexcel_jhb_per_grid_capture_dates_2026-06-04.csv`) |
| Reps | 1 (single production pass; multi-rep was a stability-pilot instrument, not production) | this doc |
| Verdict store | disabled (`--no-verdict-store`); resume via scan states | Stage-C shape |
| Routing salt | `--routing-salt-mode target` | Stage-C shape |
| Gateway | sub2api `localhost:8080` (antigravity accounts); slots preflight required | Stage-C shape |
| Request identity in provenance hash | `temperature=0`, `image_preprocessing=raw_bytes_base64_no_transform`, `image_order_rule=picks_order_chip_index_1based` — now hashed into `prompt_config_hash`; batch attempts persist rendered prompt + image order in the audit sidecar | C0 prereg [Amendment 2026-07-16](../dinov3_scorer/DATA-c0-reverse-template-prereg-2026-07-12.md#c0-p0-amendment-2026-07-16), landed pre-launch |

**Provenance notes (2026-07-16, pre-launch).** (a) `prompt_config_hash`
changed when temperature/preprocessing/image-order entered the fingerprint —
this run's rows all carry the NEW hash; any older banked rows are identifiable
by the old one. (b) Documented fact: **batch mode sends no per-chip capture
dates to the model** — the only rendered date reaching Gemini is the
census-calibration suffix's `ref_date`; per-chip dates live in the sidecar
only. (c) `score_batch_with_fallback` now fail-louds if picks are not in
chip_index order 1..N.

## 3. Inputs

- Chips (download output): `~/zasolar_data/geid_temporal/basemap_rebuild_2026-07-13/chips/<anchor_id>/z19/<anchor_id>_<yyyymmdd>_v<version|noversion>.tif`
  — TM rows have `noversion`, Wayback numeric versions, so one shared root
  serves both `--merged-tm-chips-dir` and `--merged-wayback-chips-dir`.
  koko's half must be rsynced into the home root **before** launch.
- Per-target bbox: `basemap_rebuild_2026-07-13/anchors_per_target_96m.csv`
  (41,393; the chip boxes actually downloaded — chip_targets.csv's own
  `chip_*` corners are group boxes and must NOT be used).
- Target metadata: `..._chipgroups/chip_targets.csv` (source_area_m2,
  width/height, target_label, centroid).
- Group `source_grids`: `..._chipgroups/chip_groups_as_anchors.csv`
  (multi-grid census-date resolution; anchor-level `grid_id` is fallback).
- Known holdout: group `..._c0012722` has zero TM dates (permanent 403
  holdout) — Wayback-only for that group, not a bug.

## 4. Prepared artifacts

`scripts/temporal/build_fullscan_anchors.py` joins the three inputs, attaches
`chip_arm`/`review_extent_m`/provenance columns (reusing
`issue25_stage_c.route_chip_arm`), and writes under the run root:

- `anchors_all.csv` (41,393 rows, hard-validated: bijective join, area>0,
  width/height>0, per-target bbox)
- `anchors_A24.csv` / `anchors_A48.csv` (split via
  `issue25_stage_c.split_anchors_by_chip_arm`)
- `anchors_summary.json` (counts + sha256s)

## 5. Execution topology

Run root: `~/zasolar_data/geid_temporal/fullscan_gemini_backdating_2026-07/`.
Launcher: `scripts/temporal/run_fullscan_backdating.sh` (tmux session
`fullscan_backdating` on home).

**TM catalog throttle (new, required).** The scan's live catalog fetches
(`gehi_availability`/`gehi_info`) were previously unthrottled bare `run_gehi`
calls; Stage-C survived on 400 targets, but 41k anchors at worker pace would
sustain ~180 TM-metadata calls/min for many hours — well past the ~104/min
threshold that triggered the 2026-07-12 khmdb 403 ban. `run_adaptive_scan.py`
now takes `--tm-catalog-interval` (default 0 = legacy): one process-wide
`GehiRateLimiter` + `make_throttled_runner` (403/429 backoff) injected into
every live **TM** availability/info call; Wayback (ESRI, no known limit) and
chip downloads are untouched. Tests:
`tests/temporal/test_tm_catalog_throttle.py`. The full run uses `1.0` s (the
validated ~60 calls/min-per-address-family pace).

Anchor counts route as A24 = 36,322 / A48 = 5,071, so the launcher balances
the two GEHI address families by anchor count instead of by arm (three lanes,
at most two scan processes at a time):

| lane | anchors | `--review-extent-m` | GEHI family | workers / qps |
|---|---|---|---|---|
| a24_v4 | `anchors_A24_v4.csv` (~20.7k, A24 head) | 24 | IPv4 (`DOTNET_SYSTEM_NET_DISABLEIPV6=1`) | 20 / 10 |
| a48 (v6, first) | `anchors_A48.csv` (5,071) | 48 | IPv6 | 20 / 10 |
| a24_v6 (v6, after a48) | `anchors_A24_v6.csv` (~15.6k, A24 tail) | 24 | IPv6 | 20 / 10 |

Aggregate Gemini pace = Stage-C's tested 40 workers / 20 qps. Per-lane
catalog-cache dirs (anchor bboxes are disjoint across lanes; avoids
cross-process sqlite contention).

Per-lane invocation (launcher fills paths):

```bash
python -u scripts/temporal/run_adaptive_scan.py \
  --anchors-csv $RUN_ROOT/anchors_A24.csv \
  --scan-states-dir $RUN_ROOT/a24/scan_states \
  --chips-dir $RUN_ROOT/a24/unused_merged_chips \
  --merged-tm-chips-dir $DL_ROOT/chips \
  --merged-wayback-chips-dir $DL_ROOT/chips \
  --audit-dir $RUN_ROOT/a24/audit \
  --provider Merged --scorer gemini \
  --anchor-workers 20 --qps 10 \
  --round1-model gemini-3.1-flash-lite --round2-model gemini-3.1-flash-lite \
  --routing-salt-mode target --no-verdict-store \
  --review-extent-m 24 \
  --vexcel-capture-csv $ZASOLAR_ROOT/data/analysis/vexcel_jhb_per_grid_capture_dates_2026-06-04.csv \
  --tm-catalog-interval 1.0 \
  --catalog-cache-dir $RUN_ROOT/a24/catalog_cache
```

Resume semantics: rerunning the same lane command skips terminal scan states;
`.done` marker per lane only on clean exit; `.complete` when both lanes done.

## 6. Gates (launcher enforces, in order)

1. **Download-complete sentinel** `$DL_ROOT/.download_complete` — written by
   the monitor only after: every `batch_*_{tm,wb}.csv` in all four line dirs
   has a `.done` marker, no live `gehi_download.py`/GEHI processes remain,
   and the koko→home chips rsync final delta pass finished.
2. **Anchors build** validation (counts, join coverage, area routing).
3. **Gateway preflight**: `GET :8080/health` + active antigravity slots ≥ 40
   (same psql check as Stage-C).
4. **Authenticated 1-anchor preflight** (A24 lane, `--limit-anchors 1`,
   isolated dirs) validated by `issue25_stage_c.py validate-preflight`
   (schema-valid first-attempt call, provenance model ==
   `gemini-3.1-flash-lite`), **plus a cache-hit assertion**: the preflight
   anchor's chip provenance must show `skipped_existing` (proves the scan
   resolves the pre-downloaded chip layout instead of re-downloading).
5. **First-200 storage projection** (A24 lane, `--limit-anchors 200`):
   measure review-PNG + fresh-raw growth, project ×(41,393/200), require
   projected growth + 20 GiB reserve < free space. (138 G free at design
   time; GEHI tile cache ~100 G is reclaimable after the scan smoke passes
   if pressure appears — `chip_lifecycle.py` never touches it, prune
   manually.)

## 7. Expected load / duration

- Gemini: ~8–12 calls/target (Stage-C adaptive-round observation) → ~350–500k
  calls; at aggregate 20 qps ≈ 5–7 h if API-bound.
- GEHI TM catalog: ~4–5 TM metadata calls per anchor (info z19+z18 +
  complete-availability z19+z18, more per extra cutoff candidate; per-target
  bboxes don't share cache keys with the group-level 07-12 pulls) → ~170–200k
  TM calls at 1 req/s × 2 families. **The run is TM-catalog-bound: expect
  ~20–30 h wall clock.** Wayback info calls (~2/anchor) run unthrottled.
- If 20–30 h is unacceptable, the lever is reducing per-anchor TM calls
  (e.g. discovery ladder [19] only) — a semantics change that must be
  decided against ISSUE-19/Stage-C parity first, NOT by raising the TM rate.
- Chips: overwhelmingly `skipped_existing`; fresh downloads only for
  ladder-zoom fallbacks and the handful of deleted hang rows.

## 8. Post-run steps (not in launcher)

1. `pytest tests/` smoke.
2. `infer_install_dates.py` over both lanes' scan states (+ Vexcel CSV) →
   install-date intervals; write a DATA memo with abstain/left-censor/quality
   distributions.
3. QA sample: `build_phase0_qa_html.py`-style strip review on a random ~100
   targets before declaring the corpus good for distillation (v2 Phase 3).
4. Decide tile-cache prune / disk reclamation.

## 9. Risks / standing guidance

- **Wayback CDN instability** (07-15 evening outage pattern): silent hangs
  are a *download-phase* failure mode; the scan mostly cache-hits, but fresh
  Wayback fetches can still hang. Monitor lanes for stalled progress
  (scan-state mtime), kill+rerun lane on stall (resume is cheap).
- **TM 403**: stay at ≤1 req/s per family (built-in limiter default);
  never raise catalog pace without re-reading the 2026-07-12 ban history.
- **Lanes finish unevenly**: acceptable; do not rebalance mid-run — each
  lane's scan states live under its own lane dir and resume against that
  lane's anchors CSV.
- **Gateway slot exhaustion**: preflight checks ≥40 slots; if accounts drop
  mid-run, Gemini retries surface as batch failures in lane logs — pause the
  lane, restore gateway, rerun.
- A concurrent monitor from an earlier session may still be babysitting the
  download (fixes observed as late as 21:49 on 07-15). The download playbook
  in the memory file `gehi-pilot2023-download-2026-07-13.md` is the single
  source of truth for hang detection/fixes; any fixer must re-verify a hang
  twice ~1 min apart before killing.

## 10. Amendment 2026-07-16: offline TM catalog (mid-run restart)

Approved by owner 2026-07-16 (~16:00 NZST); lanes killed and relaunched at
16:23 NZST with `--offline-tm-catalog-csv` (resume path — 0 completed
anchors lost).

- **Why.** Measured throughput (~330–450 anchors/h/lane) sat exactly on the
  TM catalog limiter ceiling (5 live TM metadata calls/anchor at 1 req/s per
  family), while Gemini ran at ~6% of its `--qps 10` budget. Two of the 5
  calls (z19/z18 `info`) re-asked what the download-phase candidates CSV
  (`basemap_rebuild_2026-07-13/gehi_vintage_candidates_full.csv`, per-target
  TM capture-date lists) already answers; the other 3 (`availability
  --complete` z18/19/20) asked a per-target bbox-completeness question the
  owner ruled is the download layer's job ("if the chip downloaded, it was
  complete at the zoom the ladder chose").
- **What changed.** `run_adaptive_scan.py --offline-tm-catalog-csv` builds
  the TM vintage catalog offline (see `_build_offline_tm_catalog`): 0 live
  TM metadata calls; every discovery+download ladder zoom marked available
  so `make_vintage_check` never lazy-fetches; ISSUE-26 census-cutoff walk
  preserved. Offline picks carry `version="noversion"`, which also fixes a
  latent cache-miss bug: live TM picks carried numeric versions (e.g. v296)
  that never matched the pre-downloaded `_vnoversion.tif` chips, so every TM
  pick had been re-downloading its chip. Anchors missing from the CSV
  (2/41,393) fall back to the live TM path under the existing 1 req/s
  limiter. Wayback catalog calls unchanged (live, unthrottled).
- **Expected effect.** TM catalog traffic ≈ 0; binding constraint moves to
  Gemini/Wayback/IO. Tests: `tests/temporal/test_offline_tm_catalog.py`.

## 11. Amendment 2026-07-16 (same day): offline Wayback catalog

Follow-up to §10: the offline-TM change removed the TM-side live metadata
calls, leaving the remaining serial per-anchor overhead on the Wayback side —
2 live `info` calls (z19/z18) plus a z20 lazy-query on download.
`--offline-wayback` closes that gap from the **same** CSV
(`gehi_vintage_candidates_full.csv`), no second CSV.

- **Data source.** The CSV's `Wayback` rows, whose `version` column is a real
  numeric layer-capture id (unlike offline TM's `"noversion"` sentinel), so
  offline picks resolve the real pre-downloaded `_v<version>.tif` chips
  (verified against production data: e.g. anchor
  `jhb_full382_unified_a_merge01_c0925_fpcut_2026_06_01_t00038052` carries
  version `20220427` for capture_date `2019-01-15`). Rows with empty/
  non-numeric version are skipped (mirrors the live info-parsing loop's own
  `int(version_raw)` validation) — 0 such rows found in production data.
- **What changed.** `_build_offline_wayback_catalog` precisely mirrors the
  live Wayback path (`_fetch_real_vintage_catalog`'s non-coverage-gated
  `else:` branch, which Wayback always takes) with one **deliberate**
  divergence: z20 (any download-only zoom not in the discovery ladder) is
  marked UNAVAILABLE (empty set) rather than left for `make_vintage_check` to
  lazy-fetch live. Production Wayback z20 coverage is ~97% absent, so this
  trades away the rare live z20 hits for zero live Wayback calls at that
  zoom. z19/z18 (discovery ladder) get the full pre-fetched date set, so
  `make_vintage_check` never lazy-fetches those either. Unlike
  `_build_offline_tm_catalog`, there is **no** post-census-reference-frame
  advance loop — the live Wayback path never advances past the first
  `_catalog_cutoff_candidates(...)` element (that loop only exists in the
  coverage-gated TM branch), so the offline builder takes `[0]` directly.
  (For pure pre-fetched date sets the two approaches are provably
  value-identical — see `test_offline_wayback_cutoff_is_exactly_the_first_candidate`
  — the divergence is structural/live-availability-only, not observable here.)
- **Loader.** `load_offline_provider_catalogs` reads the CSV once, producing
  both the TM dates map and the Wayback (date, version) map, so enabling both
  offline flags together costs one 43 MB CSV pass (~4s), not two.
  `load_offline_tm_catalog` stays a thin back-compat wrapper — unchanged
  signature/behavior for existing callers/tests.
- **Expected effect.** Both providers' catalogs now build with zero live
  metadata calls; the fullscan's only remaining live catalog traffic is the
  rare per-anchor fallback (anchor missing from the CSV) under the existing
  TM 1 req/s limiter, and any lazy z20 TM lookup on download (unaffected by
  this change). Tests: `tests/temporal/test_offline_wayback_catalog.py`.

## 12. Post-run: interval inference (2026-07-17)

RUN 2 completed 2026-07-17 19:31 NZST (41,393/41,393 terminal, 0
`gemini_failed`). §8 steps 1–2 executed: pytest smoke matches baseline (1
pre-existing unrelated failure), and `infer_install_dates.py` ran per-lane
with `--vexcel-capture-csv` + dip repair, producing a combined
`install_intervals_all.csv` (41,393 rows). Full breakdown — coverage
(80.1% dated / 4.9% left-censored / 15.1% abstain), confidence, interval
widths, year histogram, and a flagged finding (11.2% of raw
`done_installed_during_census` anchors contradict the census-GT prior and
are reclassified `done_ambiguous_marker_missed_pv`) — is in
[`DATA-fullscan-run2-install-intervals-2026-07-17.md`](DATA-fullscan-run2-install-intervals-2026-07-17.md).
