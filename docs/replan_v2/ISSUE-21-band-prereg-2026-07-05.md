# ISSUE-21 pre-registration — P3 re-band formula (binding)

Date: 2026-07-05 · Status: **REGISTERED — binding before any new rep is scored**
Parent: [`ISSUE-21-p3-band-rederivation.md`](ISSUE-21-p3-band-rederivation.md) ·
amendment [`§5`](PRD-AMENDMENT-P1-posterior-mass-caliber-2026-07-04.md) · PRD D19(v) ·
DECISION-A path P3.
Owner decision (2026-07-05): band formula = **mean ± 2 sd**, with the
refinements below, per the §5.3 delegation ("owner picks one at sign-off …
committed in the pre-registration note before any new rep is scored").

## Registered protocol

1. **Sample.** ≥ 5 store-backed production-channel end-to-end reps total: the
   3 existing (`~/zasolar_data/geid_temporal/llm_endtoend_storebacked_20260704/`,
   reps 1–3) + ≥ 2 new; **target 6 reps (15 pairs) if budget allows**. ALL
   completed reps enter; ALL C(n_reps, 2) pairwise TVDs enter. A rep may be
   excluded ONLY for a documented launch-invariant violation (non-fresh store
   at launch, unpinned code, incomplete run), recorded **before** its TVDs
   are decoded — never for its values. No pair-level exclusions.
2. **Channel.** Pairwise TVDs are computed on the **fractional/survival
   deliverable channel** (amendment A1) only. Never tuned on any candidate
   estimator (P3 verbatim: "never tuned on the decoder").
3. **Formula.** `band = [ max(0, m − 2s), m + 2s ]`, where `m` = mean and
   `s` = Bessel-corrected (n−1) sd over all pairs. The band is **frozen** once
   computed; any future re-derivation requires a new pre-registration.
4. **Breach semantics (asymmetric).**
   - **Upper-edge breach = instability.** The Option-A rollback trigger reads:
     a majority (≥ ⌈n/2⌉) of monitored pairs **above `m + 2s`** ⇒ revert the
     production default to sustained and re-open DECISION-A.
   - **Lower-edge breach = variance-collapse canary** (suspected determinism
     leak / store reuse / non-fresh rep) ⇒ triggers a store-freshness and
     code-pinning audit. It NEVER feeds the rollback trigger — "too
     consistent" must not revert production.
5. **Diagnostics (non-gate-bearing).** The min–max envelope of the same pairs
   is reported alongside. Per-rep `gemini_failed` rate is reported against the
   historical 3.9–5.0% range (context only; not an exclusion rule by itself).
6. **Launch invariants per new rep.** Fresh empty verdict store (`records=0`
   verified and logged at launch); pinned code ref recorded; fractional
   channel produced by the production default (decoder + EB prior, D19/A4).
7. **On completion.** Retire the stale 0.037–0.063 band; evaluate the rollback
   trigger; record the outcome in a DECISION-A second addendum; retire or keep
   the A5 0.067 disclosure accordingly.

## Disclosures (recorded at registration)

- **Dependence.** The C(n,2) pairs share reps (each rep appears in n−1
  pairs); the effective sample size is closer to the rep count than the pair
  count, so `s` underestimates the true spread. The 2× multiplier partially
  compensates; min–max would suffer the same dependence with no compensation.
- **Partial blindness.** 3 of the eventual pairs are already observed —
  `[0.0504, 0.0596, 0.0304]`. Registration cannot be fully blind to them; the
  ≥ 7 unseen pairs (2 new reps × 3 old + new×new) dominate the computation.
- **Incidental effect, not a selection reason.** A trial computation on the 3
  known pairs gives ≈ `[0.017, 0.077]`; the disclosed 0.067 pair would likely
  fall inside the eventual band. The formula was chosen on small-n robustness
  grounds — edges set by all pairs rather than two order statistics (the old
  3-pair band's fragility class); expected min–max coverage at n=10 iid is
  only ≈ (n−1)/(n+1) ≈ 82%, worse under dependence, i.e. chronic single-pair
  false alarms; a high sample-min would false-alarm genuinely quiet future
  pairs (the observed 0.030 class) at the lower edge. These grounds hold
  without knowledge of the 0.067 pair. Recorded here so it cannot later be
  read as ruler-tuning (amendment §9.1).

## Completion record (2026-07-05)

Executed exactly as registered on the §1 store-backed sample. Two new reps
(rep4, rep5) completed 2026-07-05, bringing the sample to 5 reps / 10 pairs;
all launch invariants (§6) held (pinned worktree @
`89496dde59f1f6f1ae6514c35832bdc0fd63b8a7`, fresh per-layer stores `records=0`
logged at open, `FROZEN_CHIPS_DIR` unset). All C(5,2)=10 pairwise
fractional-channel TVDs (Python `combinations` order): (rep1,rep2)=0.039,
(rep1,rep3)=0.0451, (rep1,rep4)=0.0551, (rep1,rep5)=0.0285, (rep2,rep3)=0.0672,
(rep2,rep4)=0.0506, (rep2,rep5)=0.0459, (rep3,rep4)=0.0673, (rep3,rep5)=0.0454,
(rep4,rep5)=0.0705.

Band per the registered §3 formula (mean ± 2 sd, Bessel n−1): m = 0.05146,
s = 0.0136041824, **frozen band = [0.0243, 0.0787]** (full precision
[0.0242516353, 0.0786683647]; lower limit unclamped). **Rollback trigger (§4):
0 of 10 pairs above m+2s (threshold ⌈10/2⌉ = 5) ⇒ NOT met ⇒ PASS**; lower-edge
variance-collapse canary 0 of 10, not tripped; min–max envelope [0.0285, 0.0705]
inside the band. Outcome recorded in the DECISION-A **second addendum**
([`DECISION-A-estimator-adoption-2026-07-04.md`](DECISION-A-estimator-adoption-2026-07-04.md));
the stale 0.037–0.063 band and the A5 0.067 disclosure are retired.

**Provenance discrepancy note.** The §Disclosures "partial blindness" figure
cited `[0.0504, 0.0596, 0.0304]` — those are PRE-STORE 2026-06-23 values
(`llm_endtoend_20260623`, via `issue03_gates_20260704`). The §1 store-backed
sample governed the computation and yields `[0.039, 0.0451, 0.0672]` for reps
1–3 on the same registered channel. The band was computed on the §1
store-backed sample exactly as registered; the §Disclosures citation was
registration-time incidental (its own "Incidental effect, not a selection
reason") and never entered band math.
