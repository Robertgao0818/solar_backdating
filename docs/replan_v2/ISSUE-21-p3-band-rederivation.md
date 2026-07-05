# ISSUE-21: P3 band re-derivation reps (production-channel re-band)

Status: ready-for-human
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
- [ ] ≥ 2 new store-backed production-channel reps completed; each store
  verified `records=0` at launch (logged)
- [ ] ≥ 10 pairwise fractional-channel TVDs computed; band derived exactly
  per the pre-registered formula
- [ ] Rollback trigger evaluated and the outcome recorded in a DECISION-A
  second addendum (pass ⇒ 0.067 disclosure retired; breach ⇒ revert + reopen)
- [ ] Stale 0.037–0.063 band marked retired wherever it appears as a live
  number (PRD D3 diagnostic note, amendment A5)

## Blocked by

- ISSUE-20 (done), ISSUE-07 (done). Remaining gate is **human**: the ~2-rep
  API budget (band formula registered 2026-07-05).
