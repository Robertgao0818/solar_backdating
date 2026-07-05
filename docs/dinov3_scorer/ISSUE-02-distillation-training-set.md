# ISSUE-02 — Distillation training set: label harvest + GEHI chip re-download + anchor split

> Tracer slice 2 of 8 · [TRACKER](TRACKER.md)

## Parent

PRD [`../dinov3_sat_scorer_backbone_prd.md`](../dinov3_sat_scorer_backbone_prd.md)
§D6 (training data) + Q7. User stories 15, 16, 17, 21, 24, 25, 27.
**Amended 2026-07-03** by the v2 program's D12.i–iii
([`replan_v2/ISSUE-12`](../replan_v2/ISSUE-12-student-prd-amendments.md)):
corrected pool counts, the chip-manifest hard dependency, and explicit
handling of the ambiguous-terminal-status fraction.

## What to build

Build the distillation dataset from work already done — **no new LLM calls**.

**Labels.** Harvest per-vintage labels from the retained `scan_state.json`
corpus — **23,147 unique retained anchors / 250,502 rounds** on disk (15,859
chip-group + 7,288 per-target + 11,419 census2023 + 1,641 wayback scan states;
re-derived 2026-07-03, replacing the stale 26,820 figure — D12.i): per round,
`(anchor_id, capture_date, version, actual_zoom, pv_present, confidence,
quality_flag, terminal_status)`. The persisted confidence field is
`confidence` — `pv_score` exists only at the seam contract, never on disk.
Apply the label rules: present/absent **only** from `usable` + high-confidence
rounds; `ambiguous`/`unusable` → the `unusable` class. **27.6% of retained
anchors (6,399/23,147) are `done_ambiguous_*`** (marker-misregistration) — a
major bite out of the harvestable pool, not a footnote (D12.iii): exclude or
down-weight them as a documented per-stratum choice, and size every stratum
from the post-exclusion pool.

**Images.** The `chips/` directory was deleted, and scan states carry **no
anchor coordinates**: the chip-target manifest — `chip_targets.csv` (41,394
rows, intact:
`~/zasolar_data/geid_temporal/jhb_full382_unified_A_merge01_c0925_fpcut_2026-06-01_chipgroups/chip_targets.csv`)
— is a **hard dependency** of the chip re-render (D12.ii); fail loudly if it
is missing. Re-render a **stratified ~500–1000-anchor subset** via the
existing **idempotent, LLM-free** GEHI download by joining the manifest to the
retained metadata (reuse the zoom-ladder download + anchor-centered crop
geometry; **do not modify** the GEHI stage). Chip geometry at this re-render
is a versioned, gated render parameter decided by the D4 tight-crop experiment
([replan_v2 ISSUE-19](../replan_v2/ISSUE-19-chip-geometry-policy.md)) — do not
inherit it by accident from whichever builder ran. ISSUE-19 has landed: the
re-render default is `chip_geom_v2_tight12` (the tight-crop arm), resolved from
the `scripts/temporal/chip_geometry.py` registry via `--chip-geometry` and
recorded in provenance; see the
[decision memo](../replan_v2/ISSUE-19-geometry-decision-2026-07-04.md). Two
obligations that memo assigns to THIS issue: (i) the harvested teacher labels
were produced on v1 (coarse) renders while the chips re-render at v2 — the
memo's "Distillation-label caveat" section requires an explicit written
disposition here (accept-and-document / exclude-or-down-weight
`gemini_failed`-shaped strata / re-score a calibration subset at v2); (ii)
~12% of manifest targets exceed the 12 m window and render under the
footprint-containment guard (`contain_crop_to_footprint`) — an unscored
geometry band whose re-rendered chips must be QA spot-checked before
training. Stratify by sub-domain (CBD
aerial-mosaic vs non-CBD satellite) and label balance. Drop rows whose
historical chip is no longer recoverable from GEHI and record the drop count.

**Split.** Partition **by anchor** into train / held-out (held-out feeds both
calibration and the gate). No anchor appears in both. Region resolution is
explicit `region` + registry (ADR-0002). All artifacts under `~/zasolar_data/`,
gitignored.

**Downstream calibration.** The training cohort is later dual-scored by
teacher and student ([ISSUE-04](ISSUE-04-train-head-calibrate.md), co-teacher
calibration, D12.vii) — keep `sub_domain`, GSD tier (`actual_zoom`), and
`terminal_status` columns in the manifest so the disagreement distribution can
be stratified without re-derivation.

## Distillation-label caveat — teacher/student geometry mismatch (disposition, 2026-07-05)

Raised by the [ISSUE-19 decision memo](../replan_v2/ISSUE-19-geometry-decision-2026-07-04.md)
"Distillation-label caveat" (decision-audit finding **F2**), which requires this
issue to choose a disposition **in writing**.

**The mismatch.** The harvested teacher labels were produced on **v1 geometry**
(`chip_geom_v1_banked96` — the banked ~60 m-context / ≥128 px render; the
`ensure_single_target_review_png` defaults `3.0 / 24.0 / 128` at
`gehi_common.py:552-554` were in force for the 2026-06-02 scan corpus, which
predates ISSUE-19's `--chip-geometry` flag). This issue re-renders the student's
chips at **v2 geometry** (`chip_geom_v2_tight12` — `0.5 / 12.0 / 256`, ~12 m /
256 px). The re-render changes the student's **input pixels, not the harvested
labels**: v2 pixels are paired with labels carrying v1 bias — concentrated, per
the ISSUE-04 evidence, on the small/hard units (T02: invisible small dark panels
→ false-absent at 60 m; T04: confidently-wrong presence on a bare roof at 60 m).
The ISSUE-19 mixing rule forbids silently pooling the two geometries.

**Disposition assigned: (2) exclude/down-weight _composed_ with the mandated
27.6 % handling + (1) accept-and-record the residual, with (3) direct
measurement deferred to ISSUE-04.** ISSUE-02's hard constraint — *no new LLM
calls* — makes option (3) (re-score at v2 with the teacher) structurally
impossible **inside this issue**, so the in-scope choice is (2)+(1), engineered
to make (3) cheap later:

- **(2) Exclude/down-weight — largely already paid.** The worst
  v1-render-artifact stratum, `done_ambiguous_gemini_failed`, is one of the
  `done_ambiguous_*` terminal statuses and is therefore **already removed by the
  mandated 27.6 % `done_ambiguous_*` exclusion** above — this caveat *composes*
  with that rule, it does not add a second exclusion pass. Per-stratum
  down-weighting by `actual_zoom` / footprint size stays available as a lever.
- **(1) Accept + record the residual, and make it measurable.** The residual —
  *confident* v1 labels on small/hard units (T04-shaped) — cannot be found
  LLM-free (finding it is exactly what re-scoring does), so it is accepted as a
  bounded **label-noise floor** consistent with the match-not-beat fidelity
  target (~88 % of targets are ≤ 12 m footprint / p50 6.2 m and render in the
  tested 12 m config, where v1↔v2 labels largely agree; the bias lives in the
  small/hard minority). It is made explicit and downstream-stratifiable by
  recording **`label_render_geometry`** (= `chip_geom_v1_banked96`, the geometry
  the label was produced under) in the manifest, **distinct from** the re-render
  `render_geometry` (= `chip_geom_v2_tight12`), alongside the already-required
  `sub_domain` / `actual_zoom` / footprint columns.
- **(3) Defer the direct measurement to ISSUE-04.** Re-scoring a v2 calibration
  subset with the teacher to quantify the label-geometry disagreement slots into
  ISSUE-04's **co-teacher dual-scoring** (D12.vii): it re-scores the v2 chips and
  reads the v1-label vs v2-teacher disagreement stratified by the columns this
  issue preserves. ISSUE-02's job is to make that measurable — not to measure it.

Until (3) runs in ISSUE-04, "the re-render fixes the resolution artifact" remains
a claim about **student inputs only** — not the training labels.

## Acceptance criteria

- [x] A label manifest harvested from the retained scan states with at least: `anchor_id, region, grid_id, capture_date, version, actual_zoom, pv_present, confidence, quality_flag, terminal_status, label_3class (present/absent/unusable), split, sub_domain`.
- [x] Harvest sizing re-derived from the on-disk corpus and recorded (manifest header or sibling README): 23,147 unique anchors / 250,502 rounds expected; any deviation explained. No 26,820-based numbers anywhere.
- [x] Label rules applied: present/absent only from usable + high-confidence; ambiguous/unusable → unusable; the 27.6% `done_ambiguous_*` anchors excluded or down-weighted (documented per-stratum choice), strata sized from the post-exclusion pool.
- [x] Chip re-render reads anchor coordinates from `chip_targets.csv` (hard dependency — fails loudly with a clear message if the manifest is missing).
- [x] A stratified ~500–1000-anchor chip subset re-downloaded via GEHI, idempotently (re-running does not re-fetch existing chips); GEHI stage code unchanged.
- [x] Chip geometry recorded as an explicit render parameter (per replan_v2 ISSUE-19), not silently inherited.
- [x] Manifest records `label_render_geometry` (= `chip_geom_v1_banked96`, the label-source geometry) as a column **distinct from** the re-render `render_geometry` (= `chip_geom_v2_tight12`), making the teacher/student geometry mismatch explicit (Distillation-label caveat, disposition 1+2).
- [x] The caveat disposition is documented in the sibling README, including that option (2) **composes with** — does not duplicate — the 27.6 % `done_ambiguous_*` exclusion, and that direct measurement (option 3) is deferred to ISSUE-04 co-teacher dual-scoring.
- [x] Train / held-out split is anchor-disjoint — verified by an assertion/test.
- [x] Strata counts and the unrecoverable-chip drop count are documented (manifest header or sibling README).
- [x] Manifest keeps `sub_domain` / `actual_zoom` / `terminal_status` for ISSUE-04's co-teacher disagreement stratification.
- [x] All artifacts under `~/zasolar_data/` and gitignored; only a tiny example fixture (a few rows + chips) committed.

## Execution record — 2026-07-05 (DONE)

Built by `scripts/temporal/build_distillation_set.py` (`harvest` + `render-chips`),
27 unit tests. Artifacts: `~/zasolar_data/geid_temporal/dinov3_distill_20260705/`
(label_manifest.csv 298,239 rows × 15 cols, harvest_meta.json,
chip_subset_anchors.json, chips/, chip_render_provenance.csv,
chip_render_drops.json, README.md, QA_SPOTCHECK_2026-07-05.md).

- **Sizing (re-derived, authoritative):** 23,147 unique anchors (== D12 ref) /
  298,239 post-dedup rounds across all four corpora. D12's 250,502 kept as a
  labelled EXPECTED reference — it excluded wayback+census rounds (inconsistent
  subset); README documents the reconcile. census2023 contributed 38,007 label
  rows / 11,412 anchors (7 malformed dropped, 0 interval-join misses),
  label-only (`version=""`).
- **Labels:** HIGH_CONF=0.90 (new recorded parameter); absent 133,846 /
  present 67,379 / unusable rest; `done_ambiguous_*` = 6,399/23,147 = 27.6%
  exactly, retained as `unusable`, excluded from present/absent supervision.
- **Split:** train 18,499 / heldout 4,648 anchors, intersection 0
  (independently re-verified on the real manifest).
- **Re-render:** 800-anchor stratified subset, `chip_geom_v2_tight12`;
  8,165 rounds rendered, 763 dropped `all_zooms_failed` (recorded), 0 join
  misses. Idempotency proven on real artifacts: full re-run exits 0 with
  identical counts, tif mtime snapshot byte-identical (0 re-fetches), 0 PNG
  rewrites.
- **Containment-band QA (ISSUE-19 obligation):** PASS — 10/10 band chips
  (12.2–40.6 m) show the guard widening the crop without clipping. 3 sampled
  chips are washed out by old-vintage haze (hits a 3.5 m control equally →
  source-imagery artifact, orthogonal to geometry); all carry confident v1
  labels = the documented disposition-1 residual. See
  `QA_SPOTCHECK_2026-07-05.md` (incl. an ISSUE-04 pointer: stratify co-teacher
  disagreement by old-vintage/z19).
- **Fix history:** pre-existing WIP had the CLI wired to `harvest_corpus`
  (census2023 + dedup + reconcile unreachable), silent missing-corpus skip, and
  a never-backfilled README placeholder after render — all fixed this session
  (audit → adversarial verify → fix workflow); tests 21 → 27.

## Blocked by

None — can start immediately (parallel with [ISSUE-03](ISSUE-03-dinov3-scorer-scaffold.md); the seam it needed landed as replan_v2 ISSUE-05).
