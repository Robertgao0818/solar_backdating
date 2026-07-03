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
inherit it by accident from whichever builder ran. Stratify by sub-domain (CBD
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

## Acceptance criteria

- [ ] A label manifest harvested from the retained scan states with at least: `anchor_id, region, grid_id, capture_date, version, actual_zoom, pv_present, confidence, quality_flag, terminal_status, label_3class (present/absent/unusable), split, sub_domain`.
- [ ] Harvest sizing re-derived from the on-disk corpus and recorded (manifest header or sibling README): 23,147 unique anchors / 250,502 rounds expected; any deviation explained. No 26,820-based numbers anywhere.
- [ ] Label rules applied: present/absent only from usable + high-confidence; ambiguous/unusable → unusable; the 27.6% `done_ambiguous_*` anchors excluded or down-weighted (documented per-stratum choice), strata sized from the post-exclusion pool.
- [ ] Chip re-render reads anchor coordinates from `chip_targets.csv` (hard dependency — fails loudly with a clear message if the manifest is missing).
- [ ] A stratified ~500–1000-anchor chip subset re-downloaded via GEHI, idempotently (re-running does not re-fetch existing chips); GEHI stage code unchanged.
- [ ] Chip geometry recorded as an explicit render parameter (per replan_v2 ISSUE-19), not silently inherited.
- [ ] Train / held-out split is anchor-disjoint — verified by an assertion/test.
- [ ] Strata counts and the unrecoverable-chip drop count are documented (manifest header or sibling README).
- [ ] Manifest keeps `sub_domain` / `actual_zoom` / `terminal_status` for ISSUE-04's co-teacher disagreement stratification.
- [ ] All artifacts under `~/zasolar_data/` and gitignored; only a tiny example fixture (a few rows + chips) committed.

## Blocked by

None — can start immediately (parallel with [ISSUE-03](ISSUE-03-dinov3-scorer-scaffold.md); the seam it needed landed as replan_v2 ISSUE-05).
