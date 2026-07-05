# ISSUE-11 prep handoff — post-collision state + execution plan (2026-07-05)

Status: ready-to-start for a CLEAN session. Single-owner rule: verify no other
Claude session is active on this repo (`ps aux | grep claude`, fresh mtimes on
`scripts/temporal/goldset_*.py`) before any write.

Read alongside:
- **Adopted design (execute, do not re-derive):**
  [`ISSUE-11-prep-design-2026-07-05.md`](ISSUE-11-prep-design-2026-07-05.md) — WI-1..WI-4, ACs, file whitelists.
- Superseded design (historical record only):
  [`ISSUE-11-prep-dispute-frames-2026-07-05.md`](ISSUE-11-prep-dispute-frames-2026-07-05.md) — see §3.
- Requirements source: [`ISSUE-10-handoff-2026-07-05.md`](ISSUE-10-handoff-2026-07-05.md) §3–§4;
  evening note in [`ISSUE-10-goldset-tooling.md`](ISSUE-10-goldset-tooling.md).
- Target ACs: [`ISSUE-11-goldset-adjudication-run.md`](ISSUE-11-goldset-adjudication-run.md).

## 0. Locked decisions (do not reopen)

1. **Sample size n=500** (owner decision 2026-07-05; dry-run Wilson analysis: ±4.8 pp
   worst-case vs n=300's ±6.2 pp breach). Runbook: design doc §WI-4 (`--page-size 75`,
   seed 20260705, 20% overlap, 15 disputes forced).
2. **Dispute full-stack frames follow the adopted design's §WI-3** (copy from the alive
   frozen inventory, `fsarm_` naming, sidecar, separate labeled second row + scale-caveat
   banner). Adjudicated over the superseded design on verified facts (§3).
3. **Keep the no-visible-dispute-badge owner-bias policy** (both designs agree): the
   annotator must not see which anchors are forced disputes.
4. Margin caliber 0.30/0.95 remains untouchable (ISSUE-10 handoff §4).

## 1. What happened (context, 6 lines)

Two sessions built ISSUE-11 prep concurrently in this tree on 2026-07-05 evening.
Session-A committed ISSUE-10 tooling as `0324026` (17:16) and dinov3 distillation as
`58731ca` (18:05), then implemented its own dispute-frames design; Session-B implemented
WI-1/WI-2/WI-3 of the adopted design. At 18:13–18:14 Session-A reset shared files,
wiping Session-B's finished WI-1 + WI-2; both sessions were stopped mid-flight, leaving
`build_goldset_strip_html.py` runtime-broken (details in §2). Full forensics: memory
`issue11_prep_collision` + three audit reports (2026-07-05 audit workflow `wf_be9d915a-561`).

## 2. Tree state at handoff (audited 2026-07-05 ~19:1x, HEAD = `58731ca`)

| Path | State | Verdict |
|---|---|---|
| `scripts/temporal/rerender_goldset_windows.py` (+492 vs HEAD) | **Complete, coherent WI-3 WP-C implementation** — `--fullstack-artifacts-csv/--fullstack-units-csv/--fullstack-flank`, `resolve_fullstack_frames` (modal-FPD ± index-flank), `fsarm_` prefix, `fullstack_frames.csv` sidecar writer, GEHI fallback. Its 11 existing tests pass. | **KEEP wholesale.** Missing only tests AC-3.1/3.5/3.6/3.8 (never written). |
| `scripts/temporal/goldset_schema.py` (+14) | Clean additive `FRAME_SOURCES += ("fullstack",)`, `FRAME_ROLES += ("fullstack_window",)`. | **KEEP.** |
| `scripts/temporal/build_goldset_strip_html.py` (+262/−16) | **RUNTIME-BROKEN interleave**: Session-A's 2-tuple `build_anchor_frames`/merged-row `_build_dispute_window` pasted under Session-B's 4-tuple call sites; `render_anchor_section` calls `_fullstack_row_html` which does not exist; `FULLSTACK_ROW_LABEL`/`SCALE_CAVEAT_TEXT` undefined. 8/12 baseline strip tests fail on one root cause (`TypeError: unexpected keyword argument 'fullstack_sidecar'`). | **RESET to HEAD** (this file only), rebuild per §4 step 3. |
| `tests/temporal/fixtures/goldset_strip/expected_strip_substrings.txt` (−1) | Badge-removal fixture edit. | **RESET to HEAD together with the builder** (WI-3 re-applies badge removal + fixture edit deliberately). |
| `tests/temporal/test_goldset_rerender_to_strip_integration.py` (+179) | WI-3 AC-3.7 spec; imports the two missing constants → uncollectable until WI-3 WP-A lands. | **KEEP — executable spec.** |
| `tests/temporal/test_goldset_fullstack_frames.py` (untracked, 162 lines) | WI-3 AC-3.2/3.3/3.4 spec; same uncollectable state. | **KEEP — executable spec.** |
| `tests/temporal/fixtures/goldset_fullstack/` (untracked) | Well-formed mini fixtures (`fs_c0000542`/`fs_c0009873` dispute anchors, artifacts/units/assignments CSVs). | **KEEP.** (`golden_noop/frame_report_anchor1.norm.csv` is currently unused — wire it into AC-3.8 or drop it.) |
| `tests/temporal/test_rerender_goldset_windows.py` (+3) | Import/`FS_FIX` scaffolding only, `FS_FIX` unused (stopped mid-flight). | KEEP; the AC-3.1/3.5/3.6/3.8 tests get written here. |
| WI-1 (SHIFT_REASONS/DWELLING_CONTEXTS, SHIFT-panel selectors, `--page-size`) | **Wiped — 0 hits repo-wide.** | REBUILD per design §WI-1. |
| WI-2 (`--oversample-grid-ids`/`--oversample-factor`) | **Wiped — 0 hits.** | REBUILD per design §WI-2. |
| `tests/temporal/test_student_chip_render.py` (untracked, 490 lines, 13 tests green) | Orphaned **dinov3 track** file (Slice-4, `docs/dinov3_scorer/ISSUE-04`), unrelated to goldset. | Do NOT fold into goldset commits; leave for the dinov3 line to triage. |
| Dry-run data `~/zasolar_data/geid_temporal/goldset_dryrun_20260705/` | Intact (strips 15:09, verdicts 16:58); extra `strips_disputefix/` dir (18:04) is Session-A's rerun artifact — ignore. | Read-only reference. |
| `~/zasolar_data/geid_temporal/mini_reliability_20260624/chips_frozen/` | Intact, 780 M; **697/697 dispute-anchor artifact paths alive** (re-verified in audit). | Read-only source for WP-C copies. |

Known noise: `tests/temporal/test_dinov3_scorer.py` is intermittently flaky (~19
failures in some runs, unrelated track) — judge the gate on the goldset/temporal
modules, and rerun before concluding a regression.

## 3. Design adjudication record (why the superseded doc lost)

Verified facts, not taste (audit A3, 2026-07-05):

1. **Frame sourcing.** All full-stack frames needed for the 6 dispute c-anchors exist on
   disk (697/697 paths alive; c0009873 correctly absent). The superseded design fetched
   every frame via GEHI network for zero benefit; the adopted design copies (LLM-free,
   offline) with GEHI only as dead-path fallback.
2. **Date derivation.** The superseded design re-derived latest-absent/earliest-present
   from raw per-rep votes in `long_all.csv`. On its own headline example (`t00036959`)
   that method is corrupted by monotonicity noise (votes flip 1→0→1 after 2015), yielding
   modal latest-absent = 2024-02-29 — an artifact. The adopted design reads the fitted
   `fpd_reps` (modal FPD = 2015-11-30, consistent across reps) — the same field that
   defined the disputes in `validity_per_unit.csv`.
3. **Display.** ISSUE-10 handoff §4 REQUIRES the strip not to imply cross-source
   apparent-scale comparability. The superseded design has no scale caveat at all; the
   adopted design specifies the exact banner text + a visually distinct, labeled
   full-stack row.
4. Adopted from the superseded side anyway: the no-dispute-badge owner-bias policy (§0.3).

## 4. Execution plan (ordered; run gate after each step)

Gate: `cd /home/gaosh/projects/solar_backdating && source scripts/activate_env.sh && python -m pytest tests/temporal/ -q`
(until step 3 completes, add `--ignore=tests/temporal/test_goldset_fullstack_frames.py --ignore=tests/temporal/test_goldset_rerender_to_strip_integration.py` — those two ARE the WI-3 spec and stay uncollectable until then).

0. Confirm single ownership (§ header). Snapshot `git status --porcelain > /tmp/pre_rebuild_status.txt`.
1. **Scoped reset of the broken file + its fixture (ONLY these two):**
   `git checkout HEAD -- scripts/temporal/build_goldset_strip_html.py tests/temporal/fixtures/goldset_strip/expected_strip_substrings.txt`
   → gate (with the two ignores) must return to all-green (baseline 890-suite state).
2. **WI-1 rebuild** (schema + strip builder + its test file + goldset_strip fixtures) per
   design §WI-1, ACs 1.1–1.5. Fully specified; nothing to salvage. Reference: the prior
   implementation report (AC→test mapping, mechanism notes) is in session
   `e0a04a30-cca2-41b9-bdc0-3ca9efe2af55` workflow journal `wf_ad6308e6-69e` — reference
   only, rewrite the code.
3. **WI-3 WP-A rebuild** on top of WI-1, per design §WI-3: `FULLSTACK_ROW_LABEL` +
   `SCALE_CAVEAT_TEXT` constants (exact text in design doc), `_fsarm_chips_on_disk` +
   `load_fullstack_sidecar` + `FULLSTACK_SIDECAR_NAME`, `build_anchor_frames` → 4-tuple
   `(frames, display, fullstack_display, off_roof)`, `render_anchor_section` gains
   `fullstack_display`/`off_roof_marker` kwargs + a real `_fullstack_row_html`,
   `resolve_scan_chip` exclusion tuple gains `"fsarm_"` (do NOT add a `"fullstack_"`
   entry — no such legacy prefix exists at HEAD), re-apply the badge removal + the
   one-line `expected_strip_substrings.txt` edit. The two kept spec test files must now
   collect and pass (AC-3.2/3.3/3.4/3.7). Do NOT reintroduce `_build_dispute_window`/
   `_scan_display` (gone with the reset).
4. **WI-3 WP-C missing tests** in `test_rerender_goldset_windows.py`: AC-3.1 (copy+dedup,
   no runner call), AC-3.5 (GEHI fallback via injected runner), AC-3.6 (all-UNDATED →
   `fullstack_no_window`), AC-3.8 (no-op default byte-identical). Implementation already
   works — pure test-writing. Fixtures exist under `fixtures/goldset_fullstack/`.
5. **WI-2 rebuild** per design §WI-2, ACs 2.1–2.4 (independent; parallelizable with 2–4).
6. **Full gate**: `python -m pytest tests/ -q` — target: baseline 890 + all new ACs, 0 failures.
7. **Commit** (repo convention: one commit per issue-slice):
   `feat(goldset): ISSUE-11 prep — codebook, oversample, dispute full-stack frames (replan_v2)`
   — include the two design docs + this handoff; EXCLUDE `test_student_chip_render.py`
   (dinov3 track). Then regenerate the tracker if any `Status:` line changed:
   `python3 docs/replan_v2/render_tracker.py`.
8. **n=500 package build** per design §WI-4 runbook (tmux, GEHI ~30–90 min idempotent,
   `--page-size 75`, dispute frames via the copy path — no network). Run the §WI-4
   preflight checklist before handing pages to the human. ISSUE-11 then proceeds as
   written (human adjudication, Wilson CI, dispute arbitration).

## 5. Salvage map (where prior work lives if needed)

- Adopted design doc: restored verbatim in-repo (its provenance note refers to this handoff).
- Prior WI-1/WI-2 implementation reports (mechanisms, AC→test names, deviations):
  workflow journal `wf_ad6308e6-69e` in session dir
  `~/.claude/projects/-home-gaosh-projects-ZAsolar/e0a04a30-cca2-41b9-bdc0-3ca9efe2af55/subagents/workflows/`.
- Audit reports (tangle forensics / behavior / adjudication): `wf_be9d915a-561`, same parent dir.
- 18:45 tangle diff snapshot (already stale for the strip builder, fine for the rest):
  session scratchpad `tangle_20260705_1845.diff` (tmp — may not survive reboot; nothing
  in it is load-bearing given the reset-and-rebuild plan).
- Memory entries: `issue11_prep_collision` (state), `multi_session_same_repo` (discipline).
