# ISSUE-21: P3 band re-derivation reps (production-channel re-band)

Status: done
Phase: 0 — Estimator (band hygiene)
Blocked by: 20, 7

## Parent

[`../install_date_optimization_v2_prd.md`](../install_date_optimization_v2_prd.md) — D19(v).
Protocol: [`PRD-AMENDMENT-P1-posterior-mass-caliber-2026-07-04.md`](PRD-AMENDMENT-P1-posterior-mass-caliber-2026-07-04.md) §5
(executes DECISION-A path **P3**). Under Option A (signed 2026-07-05) this is
the **condition-subsequent** verification of the production switch.

## What to build

Re-establish the cohort reproducibility band on the production/delivery
(survival/fractional) channel:

1. ≥ **5** store-backed production-channel end-to-end reps ⇒ ≥ **10**
   rep-to-rep pairs. **3 reps already exist**
   (`~/zasolar_data/geid_temporal/llm_endtoend_storebacked_20260704/`), so
   ≥ **2 new reps** are needed. Cost note: real Gemini sequence calls on the
   642-anchor cohort, two-tier production routing (round1 + L_census =
   `gemini-3-flash`; round2 escalation = `gemini-3-flash-agent`) — budget and
   schedule before launch.
2. Band computed on the production/delivery channel **only** — P3's verbatim
   constraint is "never tuned on the decoder", generalised in the amendment
   to any candidate estimator.
3. **Pre-registration BEFORE any new rep is decoded** — resolved 2026-07-05:
   **mean ± 2 sd** registered, binding note
   [`ISSUE-21-band-prereg-2026-07-05.md`](ISSUE-21-band-prereg-2026-07-05.md)
   (lower limit floored at 0; **asymmetric breach semantics**: upper edge
   feeds the rollback trigger, lower edge is a variance-collapse audit
   canary; min–max envelope reported as a non-gate diagnostic; target 6 reps
   if budget allows).
4. **Fresh-per-rep verdict stores** — each rep launches against an empty
   store, `records=0` verified (a shared store would let cached verdicts
   never drift, collapsing the very rep-to-rep variance being measured —
   amendment §9.5).
5. On completion: retire the stale 3-pair band (0.037–0.063); evaluate the
   Option-A **rollback trigger** — fractional channel breaching the
   re-derived band on ≥ ⌈n/2⌉ of the pairs ⇒ revert the production default to
   sustained and re-open DECISION-A. Otherwise retire the A5 0.067
   disclosure.

## Acceptance criteria

- [x] Dated pre-registration note committed BEFORE any new rep is scored
  (band formula + channel + rep count + this issue linked)
  *(Done 2026-07-05: [`ISSUE-21-band-prereg-2026-07-05.md`](ISSUE-21-band-prereg-2026-07-05.md);
  no rep had been launched at registration time.)*
- [x] ≥ 2 new store-backed production-channel reps completed; each store
  verified `records=0` at launch (logged)
  *(Done 2026-07-05: rep4/rep5 both EXITCODE=0, per-layer stores `records=0`
  logged at open in `rep4.log`/`rep5.log`; launch note
  `llm_endtoend_storebacked_20260704/launch_shim/ISSUE-21_rep45_launch_note.md`.)*
- [x] ≥ 10 pairwise fractional-channel TVDs computed; band derived exactly
  per the pre-registered formula
  *(Done 2026-07-05: 10 TVDs → band [0.0243, 0.0787] (mean ± 2 sd) in
  `llm_endtoend_storebacked_20260704/analysis_issue21_5rep/`.)*
- [x] Rollback trigger evaluated and the outcome recorded in a DECISION-A
  second addendum (pass ⇒ 0.067 disclosure retired; breach ⇒ revert + reopen)
  *(Done 2026-07-05: 0/10 pairs above m+2s ⇒ PASS, recorded in the DECISION-A
  second addendum; A5 0.067 disclosure retired.)*
- [x] Stale 0.037–0.063 band marked retired wherever it appears as a live
  number (PRD D3 diagnostic note, amendment A5)
  *(Done 2026-07-05: annotated in PRD D3/D19 and amendment A3/A5/§4/§5.)*

## Blocked by

- ISSUE-20 (done), ISSUE-07 (done). Budget gate cleared 2026-07-05: the 2 new
  reps (rep4/rep5) ran, the band is frozen and the rollback trigger PASSed — issue done.
