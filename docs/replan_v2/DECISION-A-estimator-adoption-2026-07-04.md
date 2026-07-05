# Decision A — Cohort-wide estimator adoption verdict (changepoint decoder)

Date: 2026-07-04 · Status: **DECIDED — NO-GO today, sustained retained**
Scope: the adoption decision required by D3's closing sentence
(`docs/install_date_optimization_v2_prd.md`, "Decision on cohort-wide adoption
additionally requires the enlarged dominant-stratum panel (D4)"), adjudicated
after ISSUE-02 + ISSUE-04 completed and the store-backed TVD re-run
(TRACKER's ISSUE-02 note) was executed.
Candidate: changepoint posterior decoder (ISSUE-02, epoch-gap=45), assessed
both flat and with the ISSUE-03 EB/Turnbull cohort prior (commit `1daa61d`).
Incumbent: sustained.

## Verdict

**The decoder is NOT adopted cohort-wide today. Sustained remains the
production default**, per the ISSUE-04 memo's standing rule ("no change to
the production default until the decoder clears its gates").

The decoder passes every re-anchored panel gate (D3/D8, extended 38-unit
panel) and the relative survival-reproducibility gate, but the D3
**year-histogram TVD gate (0.037–0.063 band) is not met** on the sanctioned
store-backed re-run — in either decoder configuration — after both
hypothesized remediations (verdict store; stronger prior) were tested and
refuted. One gate objectively not cleared means no incumbent switch; the
pre-registered paths below terminate this decision without re-litigating the
gates that already passed.

## Gate table (as adjudicated, corrected thresholds)

Panel caliber — extended 38-unit panel, inventory-weighted where noted
(source: `panel_repair_20260703/analysis_estimator_harness_extended/report_*.md`,
independently re-read and verified 2026-07-04):

| Gate | Threshold | Decoder | Result |
|---|---|---|---|
| MAP mode-hit (unweighted) | ≥ 0.882 | 0.905 (0.942 with EB prior) | PASS, beats sustained |
| Beats PAVA floor | > 0.889 | 0.905 | PASS |
| Inv-weighted headline date / year | ≥ 0.798 / 0.757 | 0.863 / 0.830 | PASS |
| `done_appears` year-stability (n=12) | ≥ 0.717 target (0.567 floor) | 0.867 | PASS, beats sustained's own 0.717 |
| HPD calibration proxy | ≈ 0.90 | 0.914 | PASS |
| Undated-flip | ≤ 0.055 (corrected; was buggy 0.034) | 0.055 | PASS at parity (tie, not beat) |

End-to-end caliber — 3 store-backed reps, 642-anchor cohort
(`llm_endtoend_storebacked_20260704/`, code pinned `89496dd` + archived
launch shim; prior add-on decoded from worktree at `1daa61d`):

| Gate | Threshold | Decoder | Result |
|---|---|---|---|
| Year-histogram TVD (hard MAP) | within 0.037–0.063 | flat [0.079, 0.040, 0.076] mean 0.065; EB prior [0.092, 0.047, 0.085] mean 0.075 | **FAIL** (2/3 pairs above band; prior worsens it) |
| Survival-curve TVD beats point-date | relative | AC5 official: survival mean 0.050 vs point-date mean 0.075, `beats_point_date: true`; cross-check vs sustained-fractional: decoder wins 3/3 | PASS (relative). Caveat: one pair 0.067 still above the absolute band |

## Why the TVD gate fails (root-cause status)

1. **Verdict-store hypothesis refuted as sufficient.** Store-backed reps
   improved all three pairs only marginally (pre-store [0.081, 0.059, 0.079]
   → [0.079, 0.040, 0.076]). Stores were per-rep fresh (`records=0` verified
   at each launch), so measured variance is genuine.
2. **Prior hypothesis refuted.** The EB prior improves accuracy (mode-hit
   0.905→0.942) but *worsens* rep-to-rep MAP stability on all 3 pairs — a
   sharper posterior is more argmax-sensitive to small evidence
   perturbations. Accuracy and hard-MAP reproducibility trade off here.
3. **Residual driver = adaptive-search routing divergence** (which chips get
   queried per rep) **amplified by MAP-argmax collapse.** The decoder's
   fractional posterior is *more* stable than the incumbent's point dates
   (both comparisons above); the instability is a property of collapsing a
   posterior to a hard MAP year, not of the posterior itself.
4. **The band itself is suspect.** It was fit on 3 pre-store pairs; the
   production/delivery reference channel breached it on the same new reps
   (0.068 > 0.063, 1/3 pairs). Small-sample band, possibly stale.

## Established regardless of the verdict (recorded, not lost)

- Decoder (esp. with EB prior) is the superior **panel-caliber** estimator:
  all accuracy gates pass; dominant-stratum year-stability 0.867 vs
  incumbent's 0.717.
- Decoder's fractional/survival representation is **more reproducible than
  the incumbent's point dates** (official AC5 channel and independent
  glue-script cross-check agree).
- D4 is fully discharged: dominant stratum n=2→12 (≥8 required); tight-crop
  hypothesis CONFIRMED (attribution corrected 2026-07-04, verdict unchanged).
- The undated-flip gate family was corrected before adjudication: three
  scripts shared an FPD-only flip field mislabeled as sustained's
  (`fullstack_noscan_analyze.py` origin → `panel_repair_d8_compare.py`,
  `panel_repair_tightcrop_compare.py`); all fixed, artifacts regenerated
  with `.pre_flipfix.bak` backups, eight docs carry dated corrections.
  Corrected sustained flip: extended 0.055/0.06, banked 0.075.

## Pre-registered paths to GO (any one, met as written, flips the verdict)

- **P1 (recommended; owner decision required).** Amend the PRD to specify
  cohort year-histogram deliverables as **posterior-mass (fractional)
  aggregation** — exactly what ISSUE-03 shipped. The operative
  reproducibility gate then becomes the survival/fractional channel, which
  passes and beats the incumbent. This is a reporting-caliber change and
  must be an explicit PRD amendment, not an adjudication footnote; if taken,
  resolve the band question (P3) in the same amendment since one fractional
  pair (0.067) still exceeds the absolute band.
- **P2 (estimator-side).** Stabilize hard-MAP decoding (deterministic
  tie-breaking, epoch snapping, or margin-based MAP smoothing) and re-run
  the decode-only gate on the same 3 reps (free, no LLM calls).
- **P3 (band hygiene).** Re-establish the band from ≥5 store-backed
  production-channel reps (≥10 pairs), pre-registered before decoding the
  candidate. Legitimate only computed on the production channel, never tuned
  on the decoder.

## Protocol deviations + incidents of the re-run (full detail in run root)

Gateway cli-proxy-api:8317 → sub2api localhost:8080/antigravity (user
instruction, 2026-07-04), workers 30→40 / qps 8→10 (`gemini_failed` stayed
in the historical 3.9–5.0% band). Code pinned via git worktree at `89496dd`
(+1-line cwd shim, archived in `launch_shim/`); census-column projection
shim added after `achieved_zoom_counts` (diagnostic-only, post-ISSUE-13/18
column) broke strict splice column-matching on rep1/rep2 — hardened into
the launch flow before rep3 (ran fully unattended). One accidental 90 s
write into the 6-23 baseline root occurred and was independently audited:
scan_states and delivery files untouched, banked numbers unaffected —
see `~/zasolar_data/geid_temporal/llm_endtoend_20260623/INCIDENT_20260704.md`.
Engineering debt filed by the run (non-blocking): hardcoded `cd` in
`run_endtoend_rep.sh`; `VerdictStore.put()` reopens by path (not
fail-closed under directory moves); L_census has no resumability; GEHI
subprocess cleanup on parent exit incomplete.

## Artifacts

- Store-backed reps + analyses: `~/zasolar_data/geid_temporal/llm_endtoend_storebacked_20260704/`
  (`analysis_decoder/` flat TVD + survival glue; `analysis_decoder_prior/`
  flat regression check, EB-prior TVD, official `issue03_gates.json`)
- Corrected panel artifacts: `panel_repair_20260703/analysis_extended/`,
  `fullstack_noscan_20260630/analysis/`, `panel_repair_20260703/tightcrop_failed/analysis_smoke/`
  (originals as `*.pre_flipfix.bak`)
- Pinned worktrees (retain until this memo is committed):
  `/home/gaosh/projects/solar_backdating_tvdrerun` (89496dd),
  `/home/gaosh/projects/solar_backdating_tvdrerun_prior` (1daa61d)

## Addendum — verdict flip via P1 (2026-07-05)

The owner signed PRD Amendment P1 on 2026-07-05, **Option A** (adoption
effective at sign-off): the cohort deliverable caliber is **posterior-mass
(fractional) aggregation** and the operative cohort reproducibility gate is
the **survival/fractional channel** (PRD **D19**). Under the operative gate
the decoder **passes and beats the incumbent** (AC5 survival mean 0.050 vs
point-date 0.075, `beats_point_date: true`; independent cross-check 3/3), so
this decision's verdict flips **NO-GO → GO (fractional caliber, D19)**.
Production default: changepoint decoder + EB/Turnbull prior (amendment A4).
Condition-subsequent: the P3 band re-derivation (ISSUE-21 — ≥5 store-backed
production-channel reps, ≥10 pairs, fresh-per-rep stores `records=0`, formula
pre-registered before decoding) with the pre-registered rollback trigger:
fractional channel breaching the re-derived band on ≥⌈n/2⌉ of the pairs ⇒
revert the production default to sustained and re-open this decision. Until
the re-band completes, the 0.067 delivered-caliber pair is disclosed on every
deliverable (amendment A5). No gate adjudicated above is re-litigated. Full
terms:
[`PRD-AMENDMENT-P1-posterior-mass-caliber-2026-07-04.md`](PRD-AMENDMENT-P1-posterior-mass-caliber-2026-07-04.md).
