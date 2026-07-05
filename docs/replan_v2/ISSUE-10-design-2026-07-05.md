# ISSUE-10 design — gold-set tooling (jump-point strip UI + verdict manifest + stratified sampler)

Status: architect-signed 2026-07-05. Parent: `../install_date_optimization_v2_prd.md` D9–D11, user stories 17,18,20.
Downstream consumer: ISSUE-11 (`ISSUE-11-goldset-adjudication-run.md`) computes first-visible-appearance
interval-hit-rate (Wilson 95% CI per stratum) + inter-annotator agreement on the overlap.

Scope: adjudicate the **jump critical point only** per anchor. No whole-stack review.

## Verified inputs (all `ls`-confirmed 2026-07-05)

| Purpose | Path | Notes |
|---|---|---|
| Production per-anchor intervals (15,859 rows) | `~/zasolar_data/geid_temporal/jhb_full382_fpcut_scan_2026-06-02/install_intervals.csv` | cols incl. `status, confidence, install_interval_start/end, latest_absent_date, earliest_present_date, scan_state_path, grid_id` |
| Retained scan metadata (15,859 JSON) | `~/zasolar_data/geid_temporal/jhb_full382_fpcut_scan_2026-06-02/scan_states/<anchor_id>.json` | `spec_version=phase0_v2`; per `RoundResult`: `capture_date, version, actual_zoom, pv_present, quality_flag, chip_path` (chip_path now dead — chips deleted) |
| Cohort audit anchors (12,491 rows, READ-ONLY) | `~/zasolar_data/geid_temporal/coj_audit_cohort_20260704/cohort_audit_anchors.csv` | adds `stratum, any_contradiction, contradicts_2015/2019/2023, n_contradictions, first_present_year` |
| CoJ true-date chips (READ-ONLY) | `~/zasolar_data/geid_temporal/coj_audit_cohort_20260704/chips/{2015,2019,2023}/<anchor_id>.tif` | 2019 dir = 2,688 chips; native 0.15 m municipal aerial |
| Anchor coordinates (bbox) + dispute crosswalk | `~/zasolar_data/geid_temporal/jhb_full382_unified_A_merge01_c0925_fpcut_2026-06-01_chipgroups/chip_groups_as_anchors.csv` | col-1 `anchor_id` = the `c…` chip-group id; `chip_lon_min/lat_min/lon_max/lat_max` (scan_states carry NO coordinates); `target_anchor_ids` = `;`-list of **full-prefixed** target ids (`…_t00034036`). This is the ONLY table that maps a dispute target `t…` to its owning `c…` anchor. |
| 15 dated-vs-undated disputes | `~/zasolar_data/geid_temporal/fullstack_noscan_20260630/analysis/validity_per_unit.csv` | filter `category=='dated_vs_undated_mismatch'`; short target ids read from the column literally named **`anchor`** (NOT `anchor_id`) as `t00036959, t00034036, …`. These are **TARGET-level** ids (`t…` namespace), a different granularity from every other input (all `c…` chip-group keyed) — they resolve to an adjudicable anchor only via the chipgroups crosswalk below. Force-include per ISSUE-11. |

Reusable code (verified): `scripts/temporal/build_phase0_qa_html.py`
(`thumbnail_data_url`, `_resolve_review_png`, `render_round`, `PLACEHOLDER_PIXEL`, `CSS`);
`scripts/temporal/run_census2023_scan.py::_anchor_frames(state)` (canonical latest-absent /
earliest-present selection, lines 157–169); `scripts/temporal/gehi_download.py::download_chip_with_zoom_ladder`
(idempotent, `skipped_existing`, `stdin=DEVNULL`); `scripts/audit/coj_arcgis_fetch.py::fetch_and_save_chip`
(idempotent, `skipped_existing`); `scripts/temporal/chip_geometry.py::resolve_chip_geometry`;
`scripts/temporal/gehi_common.py::ensure_review_png`. Test precedent: `tests/temporal/test_phase0_qa_html.py`
(structural substring asserts, `monkeypatch sys.argv` + `main()`), `tests/temporal/test_verdict_store.py`
(JSONL round-trip), golden-fixture dir convention `tests/temporal/fixtures/<name>/`.

## Decisions

- **Sibling builder, not in-place edit.** New `build_goldset_strip_html.py` reuses the Phase-0 builder's
  primitives (import `thumbnail_data_url`, `_resolve_review_png`, `PLACEHOLDER_PIXEL`) but renders a
  narrow jump-window view with interactive controls. Rationale: keeps the pure-display Phase-0 page
  untouched and its golden test stable.
- **Offline verdict capture = in-page JS state + explicit export button.** No server. Each strip page
  holds a `verdicts` JS object; a "Download manifest" button serializes to a `Blob` + `<a download>`
  JSON file. Rationale: self-contained HTML AC; matches the `score_anchor_presence.py` localStorage
  precedent but upgrades copy-paste → real file download for a machine-readable manifest.
- **localStorage = autosave, download = commit.** Every button click persists `verdicts` to
  `localStorage` keyed by `sample_batch_id + annotator_id`; page load rehydrates from it (resume).
  The JSON download is the authoritative hand-off artifact. Rationale: crash-safety without a server;
  the human can close/reopen mid-batch.
- **Import-to-resume.** An "Import manifest" file-picker rehydrates `verdicts` from a previously
  downloaded JSON. Rationale: lets an owner move between machines and continue.
- **Per-anchor timing from timestamps, not a stopwatch.** On first reveal of an anchor the JS records
  `opened_ts_utc`; on verdict submit it records `submitted_ts_utc` and sets
  `adjudication_seconds = submitted - opened`. Re-opening an already-answered anchor does not reset the
  open stamp unless the verdict is cleared. Rationale: computable per-anchor time from the manifest for
  the n=300-vs-500 decision, robust to the page being left open.
- **Verdict vocabulary is its own axis.** `CONFIRM | SHIFT | UNDATABLE` declared in `goldset_schema.VERDICTS`,
  never overloaded onto scorer `decision_source`. Rationale: human adjudication outcome ≠ scorer
  failure/success mode; keeps shared FP-tooling (churn monitor, `DEFAULT_FAILURE_DECISION_SOURCES`) unconfused.
- **SHIFT corrected bracket mirrors pipeline field names.** `corrected_interval_start/end` mirror
  `install_interval_start/end`. Rationale: ISSUE-11 diffs corrected vs pipeline bracket directly, no
  name translation.
- **Manifest is a sibling JSONL artifact, not a `VerdictStore` row.** The `VerdictStore` is scoped to
  `PresenceScorer` seam calls keyed by pixel hash for memoization; a one-off human judgment has no such
  replay semantics. Rationale (from verdict-provenance map): borrow the envelope shape
  (`schema_version, ts_utc, …`) but keep it separate.
- **Strip provenance lock = `frame_content_hash`.** Each verdict carries the hash over its frames'
  identity tuples (source/role/date/version/chip_sha256). Rationale: proves which exact frames a human
  saw; a re-render with different pixels changes the hash → detectable.
- **Frame selection reuses `_anchor_frames`.** Promote/import the census2023 helper rather than
  re-deriving latest-absent / earliest-present. Flanks = the immediately-adjacent usable scan slots by
  `capture_date` around each bound (±1–2). Rationale: single source of truth already consumed downstream.
- **Chip geometry for re-render = `chip_geom_v1_banked96`.** The human re-adjudicates the *original*
  jump-point verdict, which was scored under the banked 96 m context render; `v2_tight12` is scoped to
  the DINOv3 distillation consumer. Strip uses `ensure_review_png` (full-chip PNG), not the single-target
  tight crop. Rationale: show the same context the pipeline decided on.
- **Sampler strata = terminal status × confidence × audit-contradiction flag.** Join
  `install_intervals.csv` (status/confidence/interval) to `cohort_audit_anchors.csv` (stratum,
  `any_contradiction`) by `anchor_id`. Rationale: the three-way cell is what ISSUE-11 slices Wilson CI on.
- **Graceful degradation without contradiction flags.** If the cohort file/column is absent for an
  anchor, the contradiction axis collapses to `""` and the stratum key falls back to
  `f"{status}_x_{confidence}"`. Rationale: AC requires degrade-not-crash; ~3,669 production anchors sit
  outside the cohort.
- **Seeded, reproducible draw.** `random.Random(seed)`; anchors sorted by `anchor_id` before sampling so
  the draw is deterministic for a given `(seed, n)`. Proportional allocation across strata with a
  largest-remainder top-up to hit exactly `n`. Rationale: reproducibility AC + stable test.
- **20% double-annotation.** After the draw, 20% of sampled anchors (seeded pick) are duplicated: one
  row `annotator_id=A, is_overlap=True` and one `annotator_id=B, is_overlap=True`; the rest split
  A/B round-robin with `is_overlap=False`. Rationale: ISSUE-11 inter-annotator agreement on the overlap.
- **15 disputes resolve to 6 owning c-anchors, force-included, flagged, counted outside quota.**
  The 15 disputes are TARGET ids in the `t…` namespace read from the `anchor` column
  (`goldset_schema.read_dispute_target_ids` → `DISPUTE_ANCHOR_COLUMN="anchor"`, filter
  `category=="dated_vs_undated_mismatch"`). They are NOT joinable to any other input as-is:
  0 of the 15 exist as `install_intervals.csv` `anchor_id`, as `scan_states/<id>.json`, or as
  chipgroups col-1 `anchor_id`. **Crosswalk** (`goldset_schema.resolve_disputes_to_anchors`): match
  each dispute's trailing `tNNNN` token against the `target_anchor_ids` `;`-list in the chipgroups CSV
  (prefix-robust — the chipgroups store full-prefixed `…_t00034036`) and take the owning row's
  `anchor_id` (the `c…` chip-group). **Verified on real data: all 15 resolve, 0 unresolved, collapsing
  to 6 distinct c-anchors** — c0000542, c0001985(×3), c0009311(×2), c0009952(×4), c0010052, c0010157(×4).
  Because scan_state / interval / jump-strip are per chip-group, the 4 many-to-one groups are **deduped**:
  each owning c-anchor is force-included **exactly once** (both A and B), never once per dispute — so
  there is exactly one jump verdict per strip, no duplicate/conflicting assignment rows. Each forced row
  carries `is_dispute_forced=True` and `dispute_target_ids` = the `;`-joined short target ids it owns
  (`goldset_schema.encode_target_ids`). Forced anchors are added regardless of the stratified quota.
  The 6 c-anchors **all** exist in `install_intervals.csv`, `scan_states/`, and the chipgroups bbox table
  (verified), so their forced rows populate `pipeline_interval_*`, `scan_state_path`, `grid_id`, and bbox
  through the exact same path as sampled anchors — no missing-scan_state special case. Rationale: ISSUE-11
  must carry a verdict attributable to each of the 15; they are a separate arbitration set (user story 22).
- **Dispute resolution is drop-and-report, never crash.** `resolve_disputes_to_anchors` returns
  `(anchor_to_targets, unresolved)`. Today `unresolved == []`, but if a future validity export names a
  target absent from the chipgroups `target_anchor_ids`, WP-B writes it to `dispute_resolution_report.csv`
  (short target id + reason `no_owning_chipgroup`) and continues the draw. Rationale: AC requires
  degrade-not-crash; a dispute that can't be located must be surfaced, not silently dropped or fatal.
- **Re-render idempotency = skip-if-exists on deterministic path.** WP-C reuses
  `download_chip_with_zoom_ladder` (`skipped_existing`) and `fetch_and_save_chip` (`skipped_existing`);
  the output path is a pure function of `(anchor_id, capture_date, version, zoom)` / `(anchor_id, year)`.
  Rationale: LLM-free re-run safety AC.
- **Unrecoverable frames dropped and reported.** Frames that fail every zoom-ladder rung (`all_zooms_failed`)
  or return `empty`/`waf_challenge` after retries are written to `rerender/frame_report.csv` with the
  existing outcome vocabulary and omitted from the strip (a `PLACEHOLDER_PIXEL` slot notes the drop).
  Rationale: AC requires drop-and-report, no silent holes. Note: this covers only **per-frame** zoom-ladder
  failures. A **wholly-missing scan_state** is not reachable for dispute rows any more — disputes resolve
  to c-anchors that own real `scan_states/<c-anchor>.json` + interval rows (verified), so WP-C reads their
  strip like any sampled anchor and WP-B fills `pipeline_interval_*/scan_state_path/grid_id/bbox` normally.
  Any genuinely un-resolvable dispute is caught earlier by `dispute_resolution_report.csv` (drop-and-report),
  so it never reaches WP-C as an empty strip. The dry-run runbook therefore completes for the dispute subset.
- **ISSUE-11 join.** Manifest carries `(anchor_id, capture_date, version)` per frame (the repo-wide scan
  slot key) + `stratum, terminal_status, confidence, any_contradiction` (per-stratum CI) +
  `annotator_id, is_overlap` (agreement) + `pipeline_* / corrected_*` (hit/miss + shift distance) +
  `is_dispute_forced` (flags the 6 forced c-anchors) + **`dispute_target_ids`** (the original short target
  ids each forced anchor covers, `;`-joined). ISSUE-11 **explodes `dispute_target_ids`** to attribute all
  15 disputes across the 6 forced verdicts (the completeness claim: every one of the 15 maps to exactly
  one forced c-anchor's verdict), rather than expecting 15 separate anchor rows. Rationale: ISSUE-11 needs
  zero extra lookups and the many-to-one collapse stays lossless.

## WP file ownership (strictly disjoint)

Shared scaffold (built by architect, imported-only by all WPs — **do not edit in any WP**):
- `scripts/temporal/goldset_schema.py` — schema constants, `VerdictRecord`/`FrameIdentity` (now incl.
  `dispute_target_ids`), paths, `frame_content_hash`, sample-assignment CSV I/O, verdict-manifest JSONL I/O,
  and the **dispute crosswalk** (`read_dispute_target_ids`, `resolve_disputes_to_anchors`,
  `encode_target_ids`/`decode_target_ids`, `DISPUTE_ANCHOR_COLUMN`, `DISPUTE_CATEGORY`). Already created +
  smoke-tested against real data (15 disputes → 6 c-anchors, 0 unresolved).

### WP-A — strip UI builder + verdict manifest loader
- `scripts/temporal/build_goldset_strip_html.py` — new. Reads sample-assignments + scan_states +
  re-rendered chips + CoJ/Wayback frames; emits self-contained per-batch strip HTML with CONFIRM/SHIFT/
  UNDATABLE buttons, SHIFT bracket inputs, timing JS, localStorage autosave, Blob-download export,
  import-to-resume. Also hosts `load_verdict_manifest_export(json_path) -> list[VerdictRecord]` (thin
  loader mapping a UI JSON export through `goldset_schema`).
- `tests/temporal/test_build_goldset_strip_html.py` — golden-file + structural asserts + round-trip.
- `tests/temporal/fixtures/goldset_strip/` — committed mini fixture (2–3 scan_states JSON, a tiny
  assignments CSV, a small PNG, expected HTML snippet/substrings, a sample UI-export JSON).

### WP-B — stratified sampler
- `scripts/temporal/build_goldset_sample.py` — new. Inputs: intervals CSV, cohort-audit CSV, disputes
  (`validity_per_unit.csv`), **and the chipgroups CSV** (dispute crosswalk source). Resolves disputes via
  `goldset_schema.read_dispute_target_ids` + `resolve_disputes_to_anchors` (short `t…` → owning `c…` anchor,
  many-to-one deduped); force-includes each of the (currently 6) owning c-anchors once, both A and B, with
  `is_dispute_forced=True` and `dispute_target_ids=encode_target_ids(...)`. Joins intervals + cohort audit;
  seeded stratified draw; 20% overlap + A/B assignment; writes `sample_assignments.csv` via `goldset_schema`.
  Writes `dispute_resolution_report.csv` for any unresolved dispute (drop-and-report). `--dry-run` prints
  the stratum table + the dispute→c-anchor collapse without writing.
- `tests/temporal/test_build_goldset_sample.py` — reproducibility (same seed → identical rows), degrade
  path (no cohort file), overlap fraction, **dispute force-include: assert the many-to-one collapse
  (multiple short `t…` sharing a c-anchor produce exactly one forced row, both A and B), `dispute_target_ids`
  carries every owned short id, forced anchors sit outside the quota, and an unlocatable dispute lands in
  `dispute_resolution_report.csv` without crashing.**
- `tests/temporal/fixtures/goldset_sample/` — mini intervals CSV + mini cohort audit CSV + mini disputes CSV
  (short `t…` ids in an `anchor` column, incl. a many-to-one pair + one unresolvable id) + mini chipgroups
  CSV (`anchor_id` + `target_anchor_ids` full-prefixed).

### WP-C — window chip re-render
- `scripts/temporal/rerender_goldset_windows.py` — new. Reads sample-assignments + scan_states (for
  per-anchor frame dates/versions/zoom) + chipgroups CSV (for bbox); re-invokes
  `download_chip_with_zoom_ladder` (TM + Wayback) and copies/links CoJ chips into
  `rerender/chips/<anchor_id>/`; writes `rerender/frame_report.csv`. Idempotent, LLM-free.
- `tests/temporal/test_rerender_goldset_windows.py` — idempotency (2nd run zero fetches via injected
  runner), drop-and-report on `all_zooms_failed`, bbox join from chipgroups.
- `tests/temporal/fixtures/goldset_rerender/` — mini assignments + scan_states + chipgroups CSV.

No two WPs write the same file. All three import `goldset_schema` read-only.

## Test plan

Conventions (from `tests/temporal/`): `monkeypatch.setattr("sys.argv", [...])` + call `main()`;
`tmp_path` for all scratch; `pytest.importorskip("PIL")` for thumbnailing; ISSUE-numbered docstring header
listing which of the 5 ACs each test covers; golden fixtures under `tests/temporal/fixtures/<name>/`.

- **WP-A golden-file (builder determinism).** Render the fixture strip page with a **fixed
  `sample_batch_id`, fixed seed, and NO wall-clock in the HTML bytes** (timing JS reads `Date.now()` at
  *runtime in the browser*, never baked into the static HTML — the emitted page is timestamp-free).
  Assert the rendered HTML equals a committed `expected_strip.html` fixture byte-for-byte, OR (following
  the softer `test_phase0_qa_html.py` precedent) assert on stable structural substrings: the three verdict
  buttons per anchor, `data-anchor-id`, `data:image/png;base64` embeds, the SHIFT bracket inputs,
  `PLACEHOLDER_PIXEL` for a dropped frame. Guard PIL with `importorskip`.
- **WP-A round-trip (UI → manifest → loadable records).** Construct a UI-export JSON (as the JS would
  emit), call `load_verdict_manifest_export`, `write_verdict_manifest`, `read_verdict_manifest`; assert
  the reloaded `VerdictRecord`s equal the input (verdict, corrected bracket on SHIFT, annotator,
  `adjudication_seconds`, `frames`, `strip_content_hash`). Mirrors `test_verdict_store.py`.
- **WP-B reproducibility.** Two runs, same `--seed --n`, assert identical `sample_assignments.csv` rows.
  Different seed → different (but valid) draw. Assert stratum proportional allocation sums to `n`, overlap
  fraction ≈ 0.20, both A and B rows for each overlap anchor. **Dispute force-include: every owning
  c-anchor present with `is_dispute_forced=True` (both A and B) exactly once despite the many-to-one
  collapse; the union of all `dispute_target_ids` equals the input dispute set; an unlocatable dispute
  appears in `dispute_resolution_report.csv` and does not crash the draw.**
- **WP-B degrade path.** Run with the cohort CSV absent: assert no crash, `any_contradiction=""`, stratum
  keys fall back to `status_x_confidence`.
- **WP-C idempotency + drop-report.** Inject a fake `download_chip_with_zoom_ladder` runner: 1st pass
  records fetches, 2nd pass (files present) yields zero real fetches (`skipped_existing`). A frame whose
  fake runner returns `all_zooms_failed` appears in `frame_report.csv` and is absent from the chips dir.
  Assert bbox is joined from the chipgroups fixture, never invented.

Run gate: `cd /home/gaosh/projects/solar_backdating && source scripts/activate_env.sh && pytest tests/temporal/`.

## Dry-run runbook (10 anchors)

Output dir: `~/zasolar_data/geid_temporal/goldset_dryrun_20260705/` (= `goldset_schema.goldset_root("dryrun_20260705")`).

```bash
cd /home/gaosh/projects/solar_backdating
source scripts/activate_env.sh
TAG=dryrun_20260705
ROOT=~/zasolar_data/geid_temporal/goldset_${TAG}

# 1. Sample 10 anchors (seeded). Uses cohort contradiction flags when present.
python scripts/temporal/build_goldset_sample.py \
  --intervals-csv ~/zasolar_data/geid_temporal/jhb_full382_fpcut_scan_2026-06-02/install_intervals.csv \
  --cohort-audit-csv ~/zasolar_data/geid_temporal/coj_audit_cohort_20260704/cohort_audit_anchors.csv \
  --disputes-csv ~/zasolar_data/geid_temporal/fullstack_noscan_20260630/analysis/validity_per_unit.csv \
  --chipgroups-csv ~/zasolar_data/geid_temporal/jhb_full382_unified_A_merge01_c0925_fpcut_2026-06-01_chipgroups/chip_groups_as_anchors.csv \
  --n 10 --seed 20260705 --overlap-frac 0.20 --tag ${TAG}
# -> $ROOT/sample_assignments.csv       (inspect stratum spread before proceeding)
# -> $ROOT/dispute_resolution_report.csv (any unlocatable dispute; empty today — all 15 → 6 c-anchors)
# The 15 disputes collapse to 6 forced c-anchors, so the 10-anchor draw yields
# >=6 forced rows (dispute) + the stratified quota; inspect is_dispute_forced/dispute_target_ids.

# 2. Re-render the window frames for the sampled anchors (idempotent, LLM-free).
python scripts/temporal/rerender_goldset_windows.py \
  --assignments $ROOT/sample_assignments.csv \
  --scan-states-dir ~/zasolar_data/geid_temporal/jhb_full382_fpcut_scan_2026-06-02/scan_states \
  --chipgroups-csv ~/zasolar_data/geid_temporal/jhb_full382_unified_A_merge01_c0925_fpcut_2026-06-01_chipgroups/chip_groups_as_anchors.csv \
  --coj-chips-dir ~/zasolar_data/geid_temporal/coj_audit_cohort_20260704/chips \
  --geometry-version chip_geom_v1_banked96 --tag ${TAG}
# -> $ROOT/rerender/chips/<anchor>/...  + $ROOT/rerender/frame_report.csv

# 3. Build the strip pages.
python scripts/temporal/build_goldset_strip_html.py \
  --assignments $ROOT/sample_assignments.csv \
  --scan-states-dir ~/zasolar_data/geid_temporal/jhb_full382_fpcut_scan_2026-06-02/scan_states \
  --rerender-dir $ROOT/rerender --tag ${TAG}
# -> $ROOT/strips/verdicts_<batch>_A.html , _B.html  (open offline in a browser)

# 4. Verify: adjudicate a couple anchors in-browser, click "Download manifest",
#    save the JSON into $ROOT/verdicts/, then round-trip it:
python - <<'PY'
from pathlib import Path
from scripts.temporal.build_goldset_strip_html import load_verdict_manifest_export
from scripts.temporal.goldset_schema import goldset_root, verdict_manifest_path, write_verdict_manifest, read_verdict_manifest
root = goldset_root("dryrun_20260705")
recs = load_verdict_manifest_export(next(root.glob("verdicts/*.json")))
out = verdict_manifest_path(root, recs[0].annotator_id, recs[0].sample_batch_id)
write_verdict_manifest(recs, out)
back = read_verdict_manifest(out)
print("round-trip OK:", len(back), "records; median adjudication_seconds:",
      sorted(r.adjudication_seconds for r in back)[len(back)//2])
PY
```

Fallbacks if GEHI/CoJ fetch fails for some frames:
- Partial re-render is fine — `frame_report.csv` lists every dropped frame with its outcome
  (`all_zooms_failed` / `empty` / `waf_challenge` / `http_error`); the strip renders the recovered frames
  and a captioned `PLACEHOLDER_PIXEL` slot for drops. The dry run still completes and timing is still
  measured on whatever renders.
- If GEHI is entirely unavailable (network/pod down), re-run step 2 later — it is skip-if-exists, so it
  resumes without refetching recovered frames. The CoJ municipal chips are already on disk (READ-ONLY
  cohort dir), so step 3 can build strips with municipal + any recovered scan frames even before all
  Wayback/TM frames land.
- Record wall-clock per-anchor from the manifest's `adjudication_seconds` (the owner does the actual
  adjudication timing; the tooling only *captures* it). This feeds the n=300 vs n=500 sample-size call.
```

## Regenerate tracker after flipping this ISSUE's Status

```bash
python3 docs/replan_v2/render_tracker.py   # writes docs/replan_v2/tracker.html
```
