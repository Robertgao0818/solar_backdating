# ISSUE-11 prep — dispute full-stack claimed-window frames

Status: **SUPERSEDED 2026-07-05 evening** — adjudicated against
[`ISSUE-11-prep-design-2026-07-05.md`](ISSUE-11-prep-design-2026-07-05.md) §WI-3; record and
rationale in [`ISSUE-11-prep-handoff-2026-07-05.md`](ISSUE-11-prep-handoff-2026-07-05.md) §3
(sourcing: all dispute frames alive on disk, no network needed; date derivation: raw-vote
brackets shown noise-corrupted on this doc's own t00036959 example; display: no scale caveat
vs the ISSUE-10 handoff §4 requirement). One element adopted: the no-visible-dispute-badge
owner-bias policy. Kept as historical record — do not implement from this doc.

(Original header: architect-signed 2026-07-05. Parent: `ISSUE-11-goldset-adjudication-run.md`,
`ISSUE-10-design-2026-07-05.md`. Extends WP-C (`rerender_goldset_windows.py`) + WP-A
(`build_goldset_strip_html.py`).)

## Problem (precondition, committed 0324026)

The ISSUE-10 strip resolves window frames ONLY from the production `scan_state`.
For the 15 dated-vs-undated disputes (→ 6 owning c-anchors) the production scan
has NO present frame — verified: all 6 dispute c-anchors have 0 present usable
observations (0–4 absent frames; c0000542 has 0 usable at all). So the strip
shows only ABSENCE evidence and both dry-run annotators returned UNDATABLE in
seconds. ISSUE-11's arbitration (recovered search give-ups vs over-dating on thin
evidence) is unanswerable until the strip ALSO renders the full-stack arm's
claimed-window frames — the present-side pixels the pipeline never surfaced.

## Chosen full-stack evidence source

- Bracket dates: `~/zasolar_data/geid_temporal/fullstack_noscan_20260630/long_all.csv`
  — per-rep, per-frame LLM scoring. Columns used: `rep, chip_id, anchor_id,
  target_label, capture_date, pv_present`. Join key: **`chip_id` = owning
  c-anchor**, **`anchor_id` = target (`t…`)**. `latest_absent` /
  `earliest_present` are NOT precomputed — derived here.
- Vintage version + zoom (optional, fidelity only):
  `fullstack_noscan_20260630/manifest_fullstack.csv`
  (`chip_id, capture_date, actual_zoom` + `v<version>` embedded in
  `source_chip_path`). GEHI selects the vintage by `--date` (capture_date);
  `version` only names the output chip, so the manifest is optional — when
  absent, `version=""` and the default `(19,18)` zoom ladder are used.
- Dispute → c-anchor crosswalk + short target ids: already carried on each
  assignment row (`is_dispute_forced`, `dispute_target_ids`), resolved at
  sample-build time by `goldset_schema.resolve_disputes_to_anchors`. WP-C reads
  them off the row — no re-derivation.

`llm_endtoend_storebacked_20260704/` is a DIFFERENT study (changepoint decoder);
NOT the full-stack arm — ignored.

## Frame-selection rule

Per dispute c-anchor, over `long_all` rows with `chip_id == <c-anchor>` filtered
to its owned `dispute_target_ids` (fallback: all targets if the list is empty):

1. Per (target, rep): `latest_absent_r = max(capture_date | pv_present==0)`,
   `earliest_present_r = min(capture_date | pv_present==1)`.
2. Per target: modal `latest_absent` / `earliest_present` across the 10 reps
   (tie: latest date for LA, earliest for EP). Rep-support recorded per frame in
   `frame_report.csv` (`error` column, e.g. `reps=8/10`) — analysis-side, never
   in the visible caption.
3. Union the per-target `{latest_absent, earliest_present}` dates across the
   c-anchor's disputed targets; dedupe by date (a date claimed both absent and
   present keeps `earliest_present` — present evidence is the missing signal).
4. Flanks: 1 nearest full-stack scanned date strictly before `min(claimed)` and
   1 strictly after `max(claimed)`.
5. Emit each as `source="fullstack"`, role ∈ {latest_absent, earliest_present,
   flank_before, flank_after}, rendered through the SAME
   `download_chip_with_zoom_ladder` on the c-anchor bbox as production frames.

Inverted brackets are NOT dropped (e.g. c0000542: modal EP 2015-11-30 vs modal LA
2024-02-29). Surfacing both lets the annotator see the contradiction — that IS the
ISSUE-11 over-dating signal. Disagreement is surfaced (rendered + rep-support in
report), not papered over.

**Dedup against production**: a full-stack frame whose `capture_date` already has
a production scan frame is dropped (production wins) and reported
`status="skipped_scan_duplicate"`. The new value is the present-side frame
production never had.

## Presentation policy (owner-bias guard)

Decision: the strip must let the annotator judge PIXELS, not PROVENANCE. So for a
dispute anchor:

- Production + full-stack window frames render in ONE date-ordered row.
- Captions are **capture-date only** — no role label, no arm label. (Roles/source
  stay in the embedded CONTEXT JSON / `FrameIdentity` for the export manifest and
  provenance-lock hash — analysis-side, not a visible caption.)
- The visible `dispute <target_ids>` badge is REMOVED. `is_dispute_forced` and
  `dispute_target_ids` remain in the CONTEXT/manifest (ISSUE-11 join), never on
  screen.
- CoJ / Vexcel / Wayback references keep their labels — they are neutral
  third-party true-date evidence, not the pipeline-vs-full-stack arms the guard
  protects against.

Scope: this uniform-caption / no-badge presentation applies to **dispute anchors
only** (`is_dispute_forced=True`) — the only anchors where two arms' frames
co-occur. Non-dispute anchors keep the validated ISSUE-10 role-captioned
presentation (single arm, no cross-arm provenance to hide). Justification: the
owner-bias risk is exactly cross-arm mixing; scoping there is the minimal change
that removes every provenance cue (caption style, role text, dispute-id badge)
where it matters and leaves the signed-off non-dispute UI and its golden coverage
untouched.

## UNDATABLE banner

Banner fires only when there are NO window frames from EITHER source:
`not select_scan_frames(state)` AND no `fullstack_*` chips on disk. A dispute
anchor with a degenerate production scan_state but recovered full-stack frames is
now DATABLE (no banner). A dispute anchor with neither still gets the banner + a
`builder_report.csv` row.

## Failure modes (all drop-and-report, never crash)

- Full-stack long CSV not passed / c-anchor absent from it: no full-stack frames
  added; anchor falls back to production-only (banner if also degenerate).
- A claimed-date frame fails every zoom rung (`all_zooms_failed`) or manifest
  version missing: recorded in `frame_report.csv`; the strip shows a captioned
  `PLACEHOLDER_PIXEL` slot.
- Full-stack date duplicates a production date: `skipped_scan_duplicate`
  (production frame wins), no double render.
- Idempotent: `fullstack_<date>_v<ver>.png` flat path is a pure function of
  `(anchor, date, version)`; a 2nd LLM-free pass issues zero fetches.

## Files touched

- `scripts/temporal/goldset_schema.py` — additive vocab: `FRAME_SOURCES +=
  ("fullstack",)`, `FRAME_ROLES += ("fullstack_window",)`.
- `scripts/temporal/rerender_goldset_windows.py` — full-stack window resolution +
  render (WP-C).
- `scripts/temporal/build_goldset_strip_html.py` — merged date-ordered dispute row
  + widened banner + dispute-badge removal (WP-A).
- `tests/temporal/test_goldset_rerender_to_strip_integration.py` — dispute
  full-stack coverage.
- `tests/temporal/fixtures/goldset_strip/expected_strip_substrings.txt` — golden
  substring list updated for the dispute-badge removal (drops the visible
  `dispute t00034036` line); asserted by
  `test_build_goldset_strip_html.py::test_expected_substrings`.

## Not part of this extension (shared-tree hygiene)

This working tree is edited concurrently by other work items. The dispute-frames
extension footprint is EXACTLY the files above. The following working-tree
changes belong to OTHER sessions and are NOT this extension's: WI-2 goldset
oversample (`build_goldset_sample.py`, `test_build_goldset_sample.py`,
`fixtures/goldset_sample/*`, `ISSUE-11-goldset-adjudication-run.md`); the
codebook / heater-swap + pagination work (`test_build_goldset_strip_html.py`
additions, `fixtures/goldset_strip/{golden_page0,ui_export_heater_swap.json,
verdicts_dryrun_20260705_A.json}`, `ISSUE-11-prep-design-2026-07-05.md`, and the
`page_suffix` pagination edits co-resident in `build_goldset_strip_html.py`); and
the dinov3 distillation work (`build_distillation_set.py`,
`test_build_distillation_set.py`, `fixtures/dinov3_distill/`). Do not attribute
these to the dispute-frames diff.
