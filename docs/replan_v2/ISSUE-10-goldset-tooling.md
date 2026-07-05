# ISSUE-10: Gold-set tooling — jump-point strip UI + verdict manifest + stratified sampler

Status: done
Phase: 2 — Accuracy channel
Blocked by: none — ISSUE-09 contradiction flags are now AVAILABLE (`cohort_audit_anchors.csv`, 2026-07-05); the sampler consumes them. Start here: [`ISSUE-10-handoff-2026-07-05.md`](ISSUE-10-handoff-2026-07-05.md).

Agent-side build + tests + 10-anchor dry run complete 2026-07-05 (see
`docs/replan_v2/ISSUE-10-design-2026-07-05.md` and the progress note below).
One BLOCKING integration defect found in dry run (WP-C/WP-A chip-layout mismatch)
must be fixed before ISSUE-11 adjudication; human dry-run adjudication + timing
capture still pending.

## Parent

[`../install_date_optimization_v2_prd.md`](../install_date_optimization_v2_prd.md) — D10. User stories 17, 18, 20.

## What to build

Everything needed so a human can adjudicate the **jump critical point only**
(owner decision 2026-07-03 — no whole-stack review):

1. **Strip UI**: extend the existing per-anchor chip-strip QA HTML builder to
   render, per anchor, the claimed latest-absent and earliest-present frames
   ±1–2 flanks, overlaid/captioned with the CoJ true-date chips, the Vexcel
   chip, and any Wayback capture falling in-window. Verdict buttons:
   CONFIRM / SHIFT (with corrected bracket entry) / UNDATABLE, writing a
   machine-readable verdict manifest. Self-contained HTML (works offline).
2. **Stratified sampler**: seeded, reproducible sampling of n=300–500 anchors
   by terminal status × confidence × audit-contradiction flag, with a 20%
   double-annotation overlap assignment.
3. **Window chip re-download**: idempotent, LLM-free re-render of the window
   frames from retained scan metadata (chips were deleted in the disk
   cleanup); unrecoverable frames (imagery availability shifted) are dropped
   and reported.

## Acceptance criteria

- [x] Strip HTML renders offline and self-contained; golden-file test on the builder
- [x] Verdicts round-trip: UI → manifest → loadable records (test)
- [x] Sampler seeded/reproducible; stratification uses contradiction flags when present, degrades gracefully when absent
- [x] Double-annotation overlap emitted with annotator assignment
- [x] 10-anchor dry run completed; time-per-anchor measured and recorded (feeds the n=300 vs 500 decision)
  — human adjudication completed 2026-07-05 evening: 24 rows over 16 anchors,
  both manifests exported and loader-verified. Clean time-per-anchor (7 idle-
  contaminated rows >300 s censored, owner-confirmed): median 12 s / mean 21 s /
  p75 24 s; datable-only median 24 s. Recorded with the n=300-vs-500 analysis in
  `~/zasolar_data/geid_temporal/goldset_dryrun_20260705/DRY_RUN_REPORT.md`
  (recommendation: n=500).

## Progress note — 2026-07-05

**Landed (agent-side, all three WPs + shared scaffold):**
- `scripts/temporal/goldset_schema.py` — shared schema, dispute crosswalk
  (`read_dispute_target_ids`, `resolve_disputes_to_anchors`, `encode_target_ids`/
  `decode_target_ids`), sample-assignment + verdict-manifest I/O.
- `scripts/temporal/build_goldset_strip_html.py` (WP-A) — offline self-contained
  strip HTML builder + `load_verdict_manifest_export`.
- `scripts/temporal/build_goldset_sample.py` (WP-B) — seeded stratified sampler
  with dispute force-include and degrade-without-cohort path.
- `scripts/temporal/rerender_goldset_windows.py` (WP-C) — idempotent, LLM-free
  window chip re-render with drop-and-report.
- Tests: `tests/temporal/test_build_goldset_strip_html.py`,
  `tests/temporal/test_build_goldset_sample.py`,
  `tests/temporal/test_rerender_goldset_windows.py` (35 tests total, one per
  named AC in the design doc) + fixtures under `tests/temporal/fixtures/goldset_*/`.
- Full repo suite green: `pytest tests/ -q` → 885 passed, 6 subtests, 0 failures.

**10-anchor dry run** (`~/zasolar_data/geid_temporal/goldset_dryrun_20260705/`):
sampler → re-render → strip build → verdict round-trip all ran end-to-end;
16 anchors (10 quota + 6 forced dispute c-anchors), all 15 disputes attributed,
0 unresolved, 20% overlap correct, strips self-contained (0 external refs),
round-trip preserves SHIFT bracket + dispute ids. Full report:
`~/zasolar_data/geid_temporal/goldset_dryrun_20260705/DRY_RUN_REPORT.md`.

**Blocking finding (must fix before ISSUE-11):** WP-C writes re-rendered scan
chips as `.tif` nested under per-anchor `z19/`/`z20/` sub-dirs; WP-A's
`resolve_scan_chip` does a non-recursive glob on a flat directory expecting
`scan_<date>_v<version>.(png|tif)`, so it finds nothing and every scan frame
falls back to a placeholder in the strip (only the small set of flat CoJ PNGs
render). Each WP's own unit tests pass — this is a cross-WP integration gap,
not a unit regression. Fix is one-sided: either normalize WP-C's output path/
format to the documented flat layout (render a PNG, drop the zoom sub-dir), or
make WP-A resolve recursively and convert `.tif`→PNG. Not fixed here (dry run
only; strict WP file-ownership boundary).

**Secondary note:** two sampled anchors (`c0000542` forced-dispute,
`c0009873` quota) have degenerate `done_ambiguous_no_recent_anchor` scan_states
with no dated rounds, so they render empty strips (no crash) — flag for
ISSUE-11 that a forced-dispute anchor may currently be unadjudicable.

**What remains before ISSUE-11 can start:** (1) fix the WP-C/WP-A chip-layout
mismatch above, (2) a human runs the dry-run browser step, adjudicates the
16 sampled anchors, downloads the verdict manifest, and the resulting
`adjudication_seconds` values feed the n=300-vs-500 sample-size decision,
(3) decide the `c0000542`/`c0009873` degenerate-scan_state handling for
ISSUE-11's forced-dispute completeness claim.

**Update 2026-07-05 (integration fix):** DO treat the blocking finding as
resolved — WP-C now normalizes each recovered scan chip to the flat
`scan_<date>_v<version>.png` the builder resolves (WP-A convention unchanged),
WP-A renders a `NO USABLE FRAMES — UNDATABLE candidate` banner + `builder_report.csv`
for degenerate scan_states, a WP-C→WP-A integration test guards the seam
(`tests/temporal/test_goldset_rerender_to_strip_integration.py`, full suite 889
passed), and the dry-run strips were rebuilt from the existing chips (no
re-download): 55/55 scan frames now embed, HTML 636/552 KB → ~4.6/4.9 MB, both
degenerate anchors surfaced. Remaining item (1) is DONE; (2) and (3) still open.

**Update 2026-07-05 (evening — dry-run adjudication done, issue closed):**
Human adjudicated both pages; remaining items (2) and (3) are resolved/handed
off. Results: 8/8 overlap verdict agreement; quota-only mix 6 CONFIRM /
1 SHIFT / 3 UNDATABLE (30% attrition); recommendation **n=500** (Wilson
worst-case ±4.8 pp vs n=300's ±6.2 pp breach; 5–15 person-hours at measured
rates). Two follow-ups route to ISSUE-11 preparation, not this issue:
- **Dispute-frame gap (structural):** all 6 sampled forced disputes were
  UNDATABLE-by-construction — strips render only the production scan_state's
  frames, and production gave up on exactly those anchors. DO extend
  re-render/builder to also source dispute anchors' windows from the
  full-stack arm's scan metadata before the real ISSUE-11 run.
- **SHIFT-bracket guard (fixed):** a real export contained SHIFT with an empty
  corrected bracket; the export handler now confirm()-warns listing offending
  anchors before an explicit-override export (+1 test; suite 890 passed).
Degenerate-anchor handling (item 3): surfaced via banner + `builder_report.csv`;
adjudication policy belongs to ISSUE-11's completeness accounting.
Full analysis: `DRY_RUN_REPORT.md` addendum in the dry-run dir.

## Blocked by

None - can start immediately
