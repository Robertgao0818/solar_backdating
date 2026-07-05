# ISSUE-11 prep design — verdict codebook + villa-oversample + full-stack dispute frames + n=500 package

Status: architect-signed 2026-07-05. Parent: `../install_date_optimization_v2_prd.md` D10–D11, user stories 19,21,22.
Extends (does **not** re-derive): [`ISSUE-10-design-2026-07-05.md`](ISSUE-10-design-2026-07-05.md) (WP ownership, test-plan
style, dry-run runbook style), closes the two follow-ups routed here by the ISSUE-10 evening note
([`ISSUE-10-goldset-tooling.md`](ISSUE-10-goldset-tooling.md)) plus the codebook requirements from
[`ISSUE-10-handoff-2026-07-05.md`](ISSUE-10-handoff-2026-07-05.md) §3–§4, and serves the ACs of
[`ISSUE-11-goldset-adjudication-run.md`](ISSUE-11-goldset-adjudication-run.md).

> **Provenance note (2026-07-05 evening):** this file was deleted from disk during the
> concurrent-session collision (see `ISSUE-11-prep-handoff-2026-07-05.md`) and restored verbatim
> from the accepting session's context, including its three acceptance-review fixes (runbook TAG,
> preflight glob path, page-count arithmetic). Content is otherwise the architect's original.

Four work items in one addendum. WP labels reuse ISSUE-10's: **WP-A** = strip builder
(`build_goldset_strip_html.py`), **WP-B** = sampler (`build_goldset_sample.py`), **WP-C** = re-render
(`rerender_goldset_windows.py`), shared scaffold = `goldset_schema.py`.

| WI | one-liner | WPs touched |
|---|---|---|
| WI-1 | verdict codebook (`shift_reason` enum incl. `heater_swap`; recorded `dwelling_context`) + `--page-size` chunking | schema + WP-A |
| WI-2 | optional villa-suspect grid oversample knob | WP-B |
| WI-3 | **ISSUE-11 blocker** — dispute anchors' jump windows from the full-stack arm inventory | WP-C + WP-A |
| WI-4 | the real n=500 package runbook (no new code) | docs only |

---

## Verified inputs (all `ls`-confirmed 2026-07-05; adds to the ISSUE-10 table)

| Purpose | Path | Notes |
|---|---|---|
| Full-stack frozen-TIFF inventory | `~/zasolar_data/geid_temporal/fullstack_noscan_20260630/artifacts_fullstack.csv` | cols `chip_id, capture_date, version, path, actual_zoom, status`; one row per on-disk frozen `.tif` under `mini_reliability_20260624/chips_frozen/`. **12 chip_ids** — the mini-reliability subset — and **all 6 dispute c-anchors are present**; all frames `status=ok`, alive on disk (R3 disk verification 100% clean). Loader exists: `score_chip_group_matrix.load_artifacts_by_chip`. |
| Per-target full-stack verdict summary | `~/zasolar_data/geid_temporal/fullstack_noscan_20260630/analysis/per_unit_fullstack.csv` | cols incl. `unit` (a **stringified `(chip_id, target_id, label)` tuple**), `tier`, `fpd_reps` (10 reps as `FPD\|<date>\|FPD\|<date>…` or `UNDATED`). Modal of `fpd_reps` = the target's claimed FPD + tie signal. |
| The 15 disputes (already used by WP-B) | `…/fullstack_noscan_20260630/analysis/validity_per_unit.csv` | `anchor`=short `t…`, `category==dated_vs_undated_mismatch`, `fs_year`,`prod_year`. Year-granular only — NOT a per-frame source. |
| Already-exported dry-run manifests (backward-compat targets) | `~/zasolar_data/geid_temporal/goldset_dryrun_20260705/verdicts/verdicts_dryrun_20260705_{A,B}.json` | 12 verdicts each. **Confirmed: neither carries `shift_reason` nor `dwelling_context`.** WI-1's backward-compat AC loads copies of these. |

### Join-key finding that shapes WI-3 (verified, corrects R1's short-id assumption)

The gold-set `anchor_id` and the full-stack `chip_id` are the **same full-prefixed string, byte-for-byte**:
`jhb_full382_unified_a_merge01_c0925_fpcut_2026_06_01_c0000542`. All 6 dispute c-anchors join by exact
dict-key equality against `artifacts_fullstack.csv.chip_id` — **no namespace translation, no fuzzy match.**
Per-target rows in `long_all.csv`/`per_unit_fullstack.csv` key on the **target** id
(`…_t00036959`); the short dispute `t…` id resolves to it via the trailing-token match already coded as
`goldset_schema._target_token`. `c0009873` (the quota, non-dispute, degenerate anchor) is **absent** from
`artifacts_fullstack.csv` → it has no full-stack coverage and therefore correctly stays banner-only (WI-3
AC-3.4). The disk hyphen/underscore inconsistency R1 flagged (`2026-06-01` dir vs `2026_06_01` id) exists
only in *directory names*, never in the *id values* — the join is on id values, so it is unaffected.

---

## WI-1 — verdict codebook extension + `--page-size` (schema + WP-A)

Absorbs the gate_a@2015 adjudication requirements (handoff §3.1/§3.2) and the missing pagination R2 found.

### Decisions

- **`shift_reason` is a SHIFT-only field carried on the verdict**, a new open/extensible vocabulary tuple in
  `goldset_schema.SHIFT_REASONS`:
  `("date_correction", "heater_swap", "imagery_cadence", "search_giveup_recovered", "other")`.
  - `date_correction` (default) = generic: pipeline bracket wrong, corrected to the true first-visible
    interval, no special mechanism.
  - `heater_swap` (handoff §3.1) = date anchored on a pre-existing pool/solar-water heater and pulled early.
  - `imagery_cadence` = jump is real, pipeline picked the wrong cadence-censored bound (off-by-one vintage).
  - `search_giveup_recovered` = production gave up / degenerate scan_state, the full-stack row (WI-3) reveals
    a datable jump the human aligns to. This is the reason that answers ISSUE-11 AC "systematic-over-dating
    question answered" for the recovered-give-up disputes.
  - `other` = catch-all, use `notes`.
  Extensible: append to the tuple, never repurpose (mirrors `FRAME_SOURCES`/`VERDICTS` discipline). Validation
  is soft: `VerdictRecord.__post_init__` rejects a **non-empty** `shift_reason` not in the tuple, but allows
  `""` (so CONFIRM/UNDATABLE and every already-exported manifest load unchanged).
- **`dwelling_context` ∈ `goldset_schema.DWELLING_CONTEXTS = ("detached_villa", "townhouse", "other")`**
  (+ `""` = unset), recorded per adjudicated anchor. Same soft validation (non-empty must be in the tuple).
- **UI placement decision: both selectors live inside the already-`hidden` SHIFT bracket panel**
  (`render_anchor_section` lines 344–349, shown only when `verdict==='SHIFT'`), so the median-12s CONFIRM
  keyboard flow (`1`→CONFIRM submit, no bracket) is **byte-for-byte untouched** — CONFIRM and UNDATABLE emit
  `shift_reason=""`, `dwelling_context=""` and never render a new required control.
  - `shift_reason` = a `<select>` defaulting to `date_correction`; zero added friction (the SHIFT panel was
    already conditional).
  - `dwelling_context` = a `<select>` defaulting to `""`, **soft-required only when
    `shift_reason==='heater_swap'`** via a `confirm()` guard at export time that mirrors the existing
    empty-SHIFT-bracket guard (lines 481–486): the guard lists heater_swap SHIFT anchors missing a dwelling
    label and asks "export anyway?". Optional (never blocking) for every other reason.
  - **Justification against the villa-hypothesis test (handoff §3.3):** the test asks whether `heater_swap`
    concentrates in `detached_villa` suburbs, so it needs `dwelling_context` populated on **exactly the
    heater_swap cases** and nowhere else. `heater_swap` is by construction a SHIFT reason (a heater-anchored
    early date is always a *correction* → SHIFT), so co-locating both selectors in the SHIFT panel and
    soft-requiring dwelling on heater_swap guarantees 100% dwelling coverage on the cases the test consumes,
    while adding zero fields to the ~90% CONFIRM/UNDATABLE path. The selector is recordable on **any**
    `shift_reason` (not gated to villa areas), honouring the handoff §3.3 caveat that flat-plate SWH geysers
    reproduce the same early-date error on ordinary townhouse roofs — the annotator records `townhouse` on a
    non-villa heater_swap; no pre-filtering to villa suburbs.
- **`grid_id` needs no change** — it is already in `SAMPLE_ASSIGNMENT_FIELDS` and `_context_entry`, so
  heater_swap verdicts already carry the grid for the post-hoc suburb/dwelling join (handoff §3.3a done).
- **Manifest round-trip + backward compat.** `VerdictRecord` gains `shift_reason: str = ""` and
  `dwelling_context: str = ""`. Both flow automatically through `asdict()`-based `_record_to_json` and the
  field-name-whitelist `_record_from_json` (schema lines 321–322), and through WP-A's `_record_from_export`
  (add two `.get(...,"")` lines) and `_context_entry` (embed both) + the client-JS `ensureRec`/`applyRec`/
  `buildManifest`/import-hydration blocks. **`SCHEMA_VERSION` stays 1** — additive defaulted fields, per the
  module's stated additive policy; `read_verdict_manifest`'s version-mismatch raise (line 316) never fires on
  the existing manifests.
- **`--page-size N` chunking (the WP-A pagination R2 found missing).** `main()` currently emits exactly one
  HTML per `(sample_batch_id, annotator_id)` — 2 files regardless of `--n`, ≈120 MB/page at n=500 (R2:
  ~400 KB/anchor × ~306 anchors/annotator). Add `--page-size N` (default `0` = unlimited = today's behavior):
  when `N>0`, slice each annotator's `grp` (after the `sorted(groups.items())` at line 748) into ≤N-anchor
  chunks; filename gains a `_pNN` suffix (`verdicts_<batch>_<ann>_p01.html`); `_client_js` gains an optional
  `page_suffix` param appended to **`LS_KEY` and the export download filename only** (NOT to
  `manifest.sample_batch_id` — that must stay the true tag so ISSUE-11 concatenates chunks into one batch).
  **Default `0` ⇒ `page_suffix=""` ⇒ `LS_KEY` and filenames byte-identical to today.** `builder_report.csv`
  is written once for the whole batch (unchunked), unchanged. See WI-4 for the recommended value.

### ACs (one test each; all in WI-1-owned `test_build_goldset_strip_html.py`)

- **AC-1.1 shift_reason round-trip.** A UI-export JSON with `verdict=SHIFT, shift_reason=heater_swap` →
  `load_verdict_manifest_export` → `write_verdict_manifest` → `read_verdict_manifest` preserves
  `shift_reason`; a non-empty invalid `shift_reason` raises `ValueError` from `__post_init__`.
- **AC-1.2 dwelling placement + heater_swap guard.** Structural asserts: both `<select>`s are inside
  `class='shift-bracket'`; the emitted client JS contains a `confirm(` guard for heater_swap SHIFT with an
  empty `dwelling_context`, positioned **before** `new Blob(` (string-index assert, mirrors the existing
  empty-bracket-guard test). No dwelling/shift_reason control appears in `verdict-controls` (the CONFIRM row).
- **AC-1.3 backward compat.** Load a committed fixture that is a copy of the real
  `verdicts_dryrun_20260705_A.json` (no new keys) → every record has `shift_reason=="" and
  dwelling_context==""`; re-serialize via `write_verdict_manifest` and reload with no error and no field loss.
- **AC-1.4 CONFIRM fast-path unchanged.** A CONFIRM export entry round-trips with `shift_reason=="" and
  dwelling_context==""`; assert the keyboard `'1'`→CONFIRM path in the emitted JS submits with no bracket/
  reason interaction (structural: `submitVerdict(current,'CONFIRM')` present, unconditional).
- **AC-1.5 page-size no-op + chunking.** `--page-size 0` (or omitted) → identical filenames and identical
  HTML bytes to the pre-change builder on the same fixture (golden); `--page-size k` with a group of >k
  anchors → multiple `_pNN` files, disjoint anchor sets whose union = the group, each self-contained
  (0 external refs), distinct `LS_KEY` per chunk, and every chunk manifest keeps `sample_batch_id==tag`.

---

## WI-2 — optional villa-suspect grid oversample knob (WP-B)

Serves handoff §3.3b: let the prevalence estimate for `heater_swap` not be swamped by a uniform draw.

### Decisions

- **Two new CLI flags, both default off:** `--oversample-grid-ids FILE` (a newline-delimited list of
  `grid_id`s, e.g. villa-suspect suburbs) and `--oversample-factor F` (float ≥ 1.0, default 1.0). When
  `--oversample-grid-ids` is omitted the sampler is **byte-identical to today** (AC-2.3).
- **Mechanism = seeded stratum split, not weighted within-stratum sampling.** When the file is provided,
  `_make_stratum(...)` appends an `_os` axis suffix to an anchor's stratum key **iff** its `grid_id` is in
  the oversample set (only when oversample is active — so keys are unchanged when off, preserving the
  degrade/no-op paths). `allocate_quota` then multiplies the proportional weight of each `_os` sub-stratum
  by `F` (effective mass = `F·|sub-stratum|` for `_os` keys, `1·|·|` otherwise), largest-remainder top-up
  unchanged. Within each (now villa-isolated) sub-stratum the draw stays the existing uniform
  `rng.sample`, so reproducibility and the `f"{seed}:{stratum}"` per-stratum RNG are untouched. This pulls
  more matching-grid anchors into the quota **without** disturbing the non-villa strata's draws.
- **Degrade path untouched.** Cohort-absent stratum fallback (`status_x_confidence`) still applies; the
  `_os` axis composes onto whichever base key exists. An oversample file whose grid_ids match zero anchors →
  no `_os` sub-stratum is created → a warning to stderr, no crash, result identical to off.
- **Forced dispute rows are never oversampled** — they sit outside the quota (`build_forced_rows`), so the
  knob only reweights the stratified quota, exactly as specified.

### ACs (one test each; all in WI-2-owned `test_build_goldset_sample.py`)

- **AC-2.1 oversample reweights.** Same `--seed --n`, with vs without `--oversample-grid-ids` (`F=3`): the
  count of drawn anchors whose `grid_id` is in the oversample set is **strictly greater** with the knob on.
- **AC-2.2 reproducible.** Two runs, identical `--seed --n --oversample-grid-ids --oversample-factor` →
  identical `sample_assignments.csv` rows.
- **AC-2.3 no-op default.** Without the flags, `sample_assignments.csv` rows are identical to a golden
  captured from the pre-change sampler for the dry-run command (byte-identical re-run AC).
- **AC-2.4 degrade untouched.** Oversample on + `--cohort-audit-csv` absent → no crash, stratum keys are
  `status_x_confidence[_os]`; an oversample file with grid_ids absent from the population → warning, no
  crash, output identical to off.

---

## WI-3 — dispute anchors' jump windows from the full-stack arm (WP-C + WP-A) — the ISSUE-11 blocker

Without this, all ~15 disputes return UNDATABLE by construction (the strip only sourced the *production*
scan_state, and production gave up on exactly these anchors). This adds a **new frame source** that reads
the frozen full-stack inventory (paths alive on disk — **no GEHI network**), renders a **visually distinct
second row** on the strip, and rescues `c0000542`.

### Decisions

- **New frame source `fullstack`, LLM-free copy from `artifacts_fullstack.csv`.** WP-C gains three flags,
  all default `None`/off (byte-identical dry-run when omitted): `--fullstack-artifacts-csv`
  (artifacts_fullstack.csv), `--fullstack-units-csv` (per_unit_fullstack.csv, for the per-target modal FPD),
  `--fullstack-flank INT` (default `2`). The anchor→targets mapping is read from the assignment row's
  existing `dispute_target_ids` column — no new crosswalk input. Join `anchor_id → chip_id` is **exact
  dict-key equality** (verified byte-identical above); short dispute `t…` → full unit id via
  `goldset_schema._target_token` trailing-token match.
- **Per-target window = modal-FPD ±flank over the chip's *sorted artifact-date list*** — NOT R3's
  vote-bracket, and emphatically NOT `per_frame_flip.csv`'s chip-aggregate modal (R3's central finding: the
  chip pools votes across co-located targets and over-pulls 20–121 frames). For each owned dispute target:
  parse `fpd_reps` from `per_unit_fullstack.csv` → the **modal FPD date(s)** (Counter over the reps); take
  the artifact rows within **±`fullstack_flank` index positions** of that date in the chip's date-sorted
  `artifacts_fullstack` list. Index-bounded (not date-range) neighborhood **inherently caps the noisy-target
  blow-up** R3 hit (the `chaotic` target t00012135's 58-date vote-run collapses to ≤5 frames). Fallbacks:
  - **Tie** (e.g. t00014389 4-vs-4, t00012135 2-vs-2): include the ±flank neighborhood of **each** modal-max
    date (union), so the human sees both candidate jumps.
  - **UNDATED** (e.g. t00012136, reps mostly `UNDATED`, no modal FPD): contribute **no frames**, emit a
    `fullstack_no_window` report row so ISSUE-11's completeness accounting sees it (AC-3.6).
- **Dedup across the anchor's targets:** frames are chip-level, so the per-target neighborhoods are unioned
  and **deduped by `(capture_date, version)`** — a date claimed by two targets is copied **once**. The
  frame's owning target(s) + claimed FPD(s) are recorded for the caption.
- **Output naming that CANNOT collide with production `scan_<date>_v<ver>.png`:** distinct prefix
  `fsarm_<capture_date>_v<version>.png` (fallback `.tif`), materialized by the same
  `_materialize_flat_scan_chip` machinery (it already accepts an arbitrary readable `source_chip`). **Critical
  WP-A guard:** `resolve_scan_chip`'s loose fallback (`build_goldset_strip_html.py:154-158`) globs
  `*<capture_date>*` and excludes only `coj_`/`wayback_`/`vexcel` — an `fsarm_` file would otherwise be
  mis-picked as a production scan frame. **WI-3 adds `"fsarm_"` to that exclusion tuple** (AC-3.2). The
  production `scan_<date>_v<ver>.png` contract is otherwise untouched.
- **WP-C writes a caption sidecar `fullstack_frames.csv`** in the rerender dir (columns:
  `anchor_id, capture_date, version, chip_filename, owning_target_ids` (`;`-joined short),
  `claimed_fpds` (`;`-joined, aligned to owning_target_ids), `is_modal_fpd_frame`, `status`). WP-A reads it to
  build per-target FPD captions; both ends are WI-3 code. `frame_content_hash` folds the new
  `FrameIdentity(source="fullstack", …)` records in automatically (order-independent), so
  `strip_content_hash` proves the human saw them.
- **Second row on the strip (WP-A), visually distinct + scale caveat.** `build_anchor_frames` gains a third
  return `fullstack_display` (discovered by a new `_fullstack_frames(chip_dir)` globbing `fsarm_*` + reading
  the sidecar). `render_anchor_section` renders the production `chip-strip` as today, then — only when
  `fullstack_display` is non-empty — a **separate** `<div class='chip-strip fullstack'>` (its own div because
  `.chip-strip` has no `flex-wrap`, R2 seam) preceded by a row label and the caveat banner:
  - Row label: **`FULL-STACK ARM — recovered jump-window (production scan gave up on this anchor)`**
  - Per-frame caption: **`fsarm · <capture_date> · z<zoom> · <tid> FPD=<fpd>[, <tid2> FPD=<fpd2>…]`**, with a
    `◀ claimed FPD` marker on the modal-FPD frame(s).
  - **Cross-source scale caveat banner (handoff §4), exact text:**
    **`⚠ SCALE NOT COMPARABLE ACROSS SOURCES — CoJ municipal aerial, GEHI / full-stack satellite, and Vexcel frames differ in GSD and in apparent house size (undiagnosed, handoff §4). Judge PV present/absent within each frame; do NOT compare panel size or area across rows.`**
    Rendered once per anchor whenever a full-stack row is shown (and it is the row where cross-source
    comparison is most tempting). New CSS class `.chip-strip.fullstack` (amber left-border) + `.scale-caveat`
    (amber) — additive, no change to existing classes.
- **`anchor_is_undatable` rescue.** Signature gains the fullstack frame list: an anchor is UNDATABLE **iff
  production `select_scan_frames` is empty AND `fullstack_display` is empty.** `c0000542` (degenerate
  `done_ambiguous_no_recent_anchor` production scan_state, but present in `artifacts_fullstack.csv`) →
  fullstack frames exist → **datable** (AC-3.4). `c0009873` (quota, degenerate, **absent** from full-stack) →
  both empty → stays UNDATABLE, banner-only. `collect_undatable_anchors` consults the same fullstack presence
  so `builder_report.csv` reason becomes `no_scan_state_no_fullstack` / `no_usable_dated_rounds_no_fullstack`
  when the fullstack row is also empty. c0000542 additionally carries a `notes`-visible flag surfacing the
  off-roof-marker geometry issue R3 found (not fixable by frame sourcing; put it in the fullstack row label
  as `· off-roof marker — verify geometry` when the sidecar marks the anchor).
- **GEHI fallback kept for missing frozen TIFFs.** If an `artifacts_fullstack` path is dead on disk, WP-C
  falls back to `download_chip_with_zoom_ladder` (bbox from chipgroups + `capture_date` + `version` + the
  frame's `actual_zoom` ladder) and materializes the result under the same `fsarm_` name. All 118 target
  frames are verified alive (R3), so the fallback is a safety net — tested with an injected runner.
- **`frame_report.csv` new source/status values (spelled out).** `source="fullstack"`,
  `role="fullstack_window"`. New statuses: `fullstack_copied` (frozen tif copied/converted into an `fsarm_`
  chip), `fullstack_skipped_existing` (idempotent skip), `fullstack_source_missing` (dead path, GEHI fallback
  not attempted/failed), `gehi_fallback_ok` / `gehi_fallback_failed` (dead frozen tif re-fetched via GEHI),
  `fullstack_no_window` (an owned dispute target yielded no modal-FPD window; one row per such target).
  WP-C's `RECOVERED_STATUSES` frozenset gains `fullstack_copied`, `fullstack_skipped_existing`,
  `gehi_fallback_ok`. `FRAME_SOURCES` (+`"fullstack"`) and `FRAME_ROLES` (+`"fullstack_window"`) are the
  additive schema constants — **added by WI-1** (which owns `goldset_schema.py`); WI-3 only reads them.

### ACs (one test each)

- **AC-3.1 fullstack copy + dedup** (WI-3, `test_rerender_goldset_windows.py`). With fullstack flags + a
  fixture chip owning two targets that share a modal-FPD date, assert `fsarm_<date>_v<ver>.png` files are
  materialized (no GEHI runner call), a shared date is copied **once**, and `frame_report.csv` carries
  `source=fullstack, status=fullstack_copied` rows.
- **AC-3.2 no collision with production scan naming** (WI-3, `test_goldset_fullstack_frames.py`). Unit on
  `resolve_scan_chip`: a dir containing both `scan_2015-11-30_v1.png` and `fsarm_2015-11-30_v9.png` resolves
  the production scan chip for date `2015-11-30` and **never** returns the `fsarm_` file (exact + loose paths).
- **AC-3.3 distinct row + captions + scale caveat** (WI-3, `test_goldset_fullstack_frames.py`). Build a strip
  for a dispute anchor with fullstack chips; assert the exact row-label substring, the exact scale-caveat
  banner substring, the `.chip-strip.fullstack` container, and a `FPD=<date>` caption token per owned target.
- **AC-3.4 c0000542 datable / c0009873 banner-only** (WI-3, `test_goldset_fullstack_frames.py`). Two-anchor
  fixture: one degenerate production scan_state **with** fullstack chips → `anchor_is_undatable`→False, no
  UNDATABLE banner; one degenerate **without** fullstack chips → True, banner + `builder_report.csv` row.
- **AC-3.5 GEHI fallback for missing frozen tif** (WI-3, `test_rerender_goldset_windows.py`). An artifact row
  whose `path` is absent on disk triggers the injected `download_chip_with_zoom_ladder` runner →
  `gehi_fallback_ok` row + `fsarm_` chip; a runner returning `all_zooms_failed` → `fullstack_source_missing`,
  no chip.
- **AC-3.6 no_window fallback** (WI-3, `test_rerender_goldset_windows.py`). A dispute target with
  `fpd_reps` all `UNDATED` (t00012136-shaped) contributes no frames and emits exactly one
  `status=fullstack_no_window` report row; the batch does not crash.
- **AC-3.7 WP-C→WP-A integration seam** (WI-3, `test_goldset_rerender_to_strip_integration.py`). End-to-end
  on a fixture dispute anchor: run the WP-C fullstack copy, then the WP-A builder against the same rerender
  dir; assert the strip embeds the `fsarm_` frames in a distinct fullstack row and that `strip_content_hash`
  differs from the same anchor rendered without fullstack chips (provenance-lock covers the new frames).
- **AC-3.8 no-op default** (WI-3, `test_rerender_goldset_windows.py`). Without the fullstack flags, WP-C
  frame_report + chips dir + the WP-A strip are byte-identical to the pre-change tools on the ISSUE-10
  fixture (byte-identical dry-run AC).

---

## WI-4 — the real n=500 package build (runbook, no new code)

Pagination **is** missing (R2 §4: one file per annotator, ≈120 MB/page at n=500) → the only code needed is
WI-1's `--page-size` (AC-1.5). WI-4 itself ships no code; it is the runbook + preflight, mirroring the
ISSUE-10 dry-run runbook style.

**Page-count / size estimate (from R2's measured ~400 KB/anchor at `thumbnail_size=220`).** n=500 quota +
15 disputes → ~306 anchors/annotator (20% overlap ≈100 double-annotated + 200/200 split + 6 forced). At one
file/annotator that is ≈120 MB — too heavy to open reliably. **Recommendation: `--page-size 75`** → 5
pages/annotator (4×75-anchor pages ≈30 MB each + a small remainder page), **10 HTML files total**, each
comfortably browser-openable and self-contained.

```bash
cd /home/gaosh/projects/solar_backdating
source scripts/activate_env.sh
TAG=real_20260705               # real batch tag (output dir becomes goldset_real_20260705)
ROOT=~/zasolar_data/geid_temporal/goldset_${TAG}
INTERVALS=~/zasolar_data/geid_temporal/jhb_full382_fpcut_scan_2026-06-02/install_intervals.csv
SCANSTATES=~/zasolar_data/geid_temporal/jhb_full382_fpcut_scan_2026-06-02/scan_states
COHORT=~/zasolar_data/geid_temporal/coj_audit_cohort_20260704/cohort_audit_anchors.csv
DISPUTES=~/zasolar_data/geid_temporal/fullstack_noscan_20260630/analysis/validity_per_unit.csv
CHIPGROUPS=~/zasolar_data/geid_temporal/jhb_full382_unified_A_merge01_c0925_fpcut_2026-06-01_chipgroups/chip_groups_as_anchors.csv
COJ=~/zasolar_data/geid_temporal/coj_audit_cohort_20260704/chips
FS_ART=~/zasolar_data/geid_temporal/fullstack_noscan_20260630/artifacts_fullstack.csv
FS_UNITS=~/zasolar_data/geid_temporal/fullstack_noscan_20260630/analysis/per_unit_fullstack.csv

# 1. Sample n=500 (seed, 20% overlap, 15 disputes forced -> 6 c-anchors). Villa oversample is OPTIONAL:
#    add `--oversample-grid-ids villa_grids.txt --oversample-factor 3` only if running the prevalence variant.
python scripts/temporal/build_goldset_sample.py \
  --intervals-csv $INTERVALS --cohort-audit-csv $COHORT \
  --disputes-csv $DISPUTES --chipgroups-csv $CHIPGROUPS \
  --n 500 --seed 20260705 --overlap-frac 0.20 --tag ${TAG}
# -> $ROOT/sample_assignments.csv ; inspect stratum spread + is_dispute_forced/dispute_target_ids
# -> $ROOT/dispute_resolution_report.csv (expect empty: all 15 -> 6 c-anchors)

# 2a. Production window frames via GEHI TM — LONG, run in tmux (SSH-drop safe), EXPECT ~30-90 min,
#     idempotent skip-if-exists so a drop/outage resumes with zero re-fetch.
tmux new -s rerender 'cd /home/gaosh/projects/solar_backdating && source scripts/activate_env.sh && \
python -u scripts/temporal/rerender_goldset_windows.py \
  --assignments '"$ROOT"'/sample_assignments.csv \
  --scan-states-dir '"$SCANSTATES"' --chipgroups-csv '"$CHIPGROUPS"' \
  --coj-chips-dir '"$COJ"' \
  --fullstack-artifacts-csv '"$FS_ART"' --fullstack-units-csv '"$FS_UNITS"' --fullstack-flank 2 \
  --tag '"$TAG"' ; bash'
# tmux attach -t rerender  to watch. The --fullstack-* flags add the dispute rows from the frozen
# inventory (NO network) in the same pass; c0000542 gets a fullstack row, c0009873 does not.
# -> $ROOT/rerender/chips/<anchor>/{scan_*,coj_*,fsarm_*}.png + frame_report.csv + fullstack_frames.csv

# 3. Strips, chunked to keep each page openable.
python scripts/temporal/build_goldset_strip_html.py \
  --assignments $ROOT/sample_assignments.csv --scan-states-dir $SCANSTATES \
  --rerender-dir $ROOT/rerender --page-size 75 --tag ${TAG}
# -> $ROOT/strips/verdicts_<batch>_{A,B}_pNN.html  (~10 files, ~25 MB each) + builder_report.csv

# 4. Preflight checklist (before handing pages to the human):
#   [ ] sample_assignments.csv: ~1000 quota rows + 12 forced rows (6 c-anchors x A/B); dispute_resolution_report.csv empty
#   [ ] frame_report.csv: recovered >> dropped; every `all_zooms_failed`/`fullstack_source_missing` reviewed
#   [ ] fullstack_frames.csv: exactly the 6 dispute c-anchors present (c0009873 ABSENT); c0000542 present
#   [ ] builder_report.csv: c0009873 listed UNDATABLE; c0000542 NOT listed (rescued by fullstack row)
#   [ ] self-contained: `grep -c 'src=.http' strips/*.html` == 0 for every page (no external refs)
#   [ ] frame embeds: `grep -c 'data:image/png;base64' strips/*.html` > 0 per page; spot-open one A + one B page
#   [ ] scale caveat present on every dispute-anchor section; fullstack row visually distinct
python - <<'PY'
import glob, re, pathlib
for p in sorted(glob.glob(str(pathlib.Path.home()/ "zasolar_data/geid_temporal/goldset_real_20260705/strips/*.html"))):
    h = pathlib.Path(p).read_text(encoding="utf-8")
    ext = len(re.findall(r"src=['\"]https?:", h))
    emb = h.count("data:image/png;base64")
    print(f"{pathlib.Path(p).name}: external={ext} embeds={emb} MB={len(h)/1e6:.1f}")
PY
```

Then the human adjudicates in-browser, exports each page's manifest into `$ROOT/verdicts/`, and ISSUE-11
concatenates all `_pNN` manifests per batch (they share `sample_batch_id`), computes the Wilson interval-hit
rate + inter-annotator agreement, and explodes `dispute_target_ids` across the 6 forced verdicts to attribute
all 15 disputes (each carrying a `shift_reason` — `search_giveup_recovered` vs a real correction vs UNDATABLE
answers the over-dating question).

---

## Hard constraints (restated as checks)

- **Strictly disjoint file ownership**, with **exactly one** intentional sequenced sharing:
  `build_goldset_strip_html.py` is edited by **WI-1 first (codebook UI + `--page-size`), then WI-3 (additive,
  presence-gated fullstack row)** — never concurrently. Every other source/test/fixture file has a single
  owner (whitelist below). `goldset_schema.py` is **WI-1-exclusive** (WI-1 adds the `fullstack` /
  `fullstack_window` constants WI-3 merely reads), so WI-3 never edits schema.
- **Every new behavior has a named AC + one test**; the WP-C→WP-A fsarm seam has a dedicated integration test
  (AC-3.7) on top of the unit tests, matching ISSUE-10 granularity.
- **WP-A's existing flat `scan_<date>_v<ver>.png` contract for PRODUCTION frames is unchanged** — the new
  `fsarm_` prefix is distinct and is added to `resolve_scan_chip`'s exclusion set (AC-3.2); production
  resolution paths are untouched.
- **Margin caliber 0.30 / 0.95 is untouched** — none of the four WIs reads, re-derives, or gates on the
  present/absent margin rule; WI-3 is a copy/display path only.
- **All new CLI flags default to off/no-op** (`--page-size 0`, `--oversample-grid-ids`/`--oversample-factor
  1.0`, `--fullstack-artifacts-csv`/`--fullstack-units-csv` None) **so the ISSUE-10 dry-run commands re-run
  byte-identically** — stated as AC-1.5 / AC-2.3 / AC-3.8.

## Existing tests that could break (and why)

| Test | Risk | Owner / mitigation |
|---|---|---|
| `test_build_goldset_strip_html.py` (golden/structural + confirm-guard ordering) | **Will change** — WI-1 adds the two SHIFT-panel `<select>`s + a heater_swap dwelling `confirm()` guard, altering the SHIFT-panel HTML and the export JS. | **WI-1 owns it** and updates the assertions in the same WI; the empty-bracket `confirm(...)`-before-`Blob` ordering assert must still hold (add the dwelling guard alongside, not reordering it). |
| `test_build_goldset_sample.py` (reproducibility golden rows) | Low — oversample defaults off, stratum keys unchanged when off. | WI-2 owns it; AC-2.3 pins byte-identical no-op. |
| `test_rerender_goldset_windows.py` (idempotency, drop-report, bbox join) | Low — fullstack flags default None → today's code path. | WI-3 owns it; AC-3.8 pins no-op. |
| `test_goldset_rerender_to_strip_integration.py` (frame-embed seam) | Low — fullstack rendering is presence-gated; ISSUE-10 fixtures have no `fsarm_` chips, so the existing scan-frame-embed asserts are unaffected. WI-1's SHIFT-panel additions don't touch scan-frame embedding. | **WI-3 owns it** (extends it for AC-3.7). WI-1 must not perturb its scan-embed asserts (it won't — additive SHIFT-panel HTML only). |
| `test_verdict_store.py`, other temporal tests | None — unrelated to the goldset modules. | — |

## Per-WI file whitelist (implementation agents sandboxed to exactly these)

**WI-1** (schema + WP-A codebook + pagination):
- `scripts/temporal/goldset_schema.py`
- `scripts/temporal/build_goldset_strip_html.py`  *(shared — edit before WI-3)*
- `tests/temporal/test_build_goldset_strip_html.py`
- `tests/temporal/fixtures/goldset_strip/**`  *(add a copy of a real `verdicts_dryrun_20260705_*.json` for AC-1.3; a SHIFT+heater_swap export JSON)*

**WI-2** (WP-B oversample):
- `scripts/temporal/build_goldset_sample.py`
- `tests/temporal/test_build_goldset_sample.py`
- `tests/temporal/fixtures/goldset_sample/**`  *(add an `oversample_grids.txt`)*

**WI-3** (WP-C fullstack + WP-A second row):
- `scripts/temporal/rerender_goldset_windows.py`
- `scripts/temporal/build_goldset_strip_html.py`  *(shared — edit AFTER WI-1 merges; additive fullstack rendering + `resolve_scan_chip` exclusion + `anchor_is_undatable`/`collect_undatable_anchors` fullstack-aware signatures only)*
- `tests/temporal/test_rerender_goldset_windows.py`
- `tests/temporal/test_goldset_fullstack_frames.py`  *(new)*
- `tests/temporal/test_goldset_rerender_to_strip_integration.py`
- `tests/temporal/fixtures/goldset_fullstack/**`  *(new: mini `artifacts_fullstack.csv`, mini `per_unit_fullstack.csv` incl. a tie + an UNDATED target, an assignments CSV with a dispute anchor)*

**WI-4** (runbook): this doc only — no code files.

## Sequencing

- **Wave 1 (parallel):** WI-1 ‖ WI-2 — fully disjoint files.
- **Wave 2 (after WI-1 merges):** WI-3 — depends on WI-1's `goldset_schema` constants and on WI-1's
  `build_goldset_strip_html.py` edit landing (WI-3 layers the additive fullstack row on top). WI-3's
  fullstack rendering is presence-gated, so it leaves WI-1's fixtures/tests byte-identical.
- **WI-4** drafting can overlap Waves 1–2; **finalize/validate last** (it exercises `--page-size` from WI-1
  and the fullstack flags from WI-3). No code, so it cannot block the others.
- Run gate for each WI: `cd /home/gaosh/projects/solar_backdating && source scripts/activate_env.sh &&
  pytest tests/temporal/`.
