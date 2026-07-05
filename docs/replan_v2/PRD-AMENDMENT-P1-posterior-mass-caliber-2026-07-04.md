# PRD Amendment — Cohort deliverable caliber: posterior-mass (fractional) aggregation (P1 + P3)

Date: 2026-07-04 · Status: **ADOPTED (D19) — Option A signed 2026-07-05**
Parent: [`../install_date_optimization_v2_prd.md`](../install_date_optimization_v2_prd.md) — amends D2 and D3, adds D19.
Trigger: [`DECISION-A-estimator-adoption-2026-07-04.md`](DECISION-A-estimator-adoption-2026-07-04.md) pre-registered path **P1** (with **P3** folded in per P1's own text).
Scope: the cohort **reporting caliber** only — which aggregation of the estimator's per-anchor posterior is the operative deliverable and the operative reproducibility gate. This amendment does **not** re-open any panel-caliber gate that already passed, and does not touch estimator internals, seams (D1), or Phase 1–4 slices.

---

## 1. What this amendment is

DECISION-A (2026-07-04) ruled **NO-GO** on cohort-wide adoption of the
changepoint posterior decoder. The decoder passed every re-anchored panel
gate (D3/D8, extended 38-unit panel) and the relative survival-reproducibility
gate, but failed the D3 **year-histogram TVD band (0.037–0.063)** in
**hard-MAP** form — flat `[0.079, 0.040, 0.076]` mean 0.065, EB-prior
`[0.092, 0.047, 0.085]` mean 0.075 — on the sanctioned store-backed re-run
(`~/zasolar_data/geid_temporal/llm_endtoend_storebacked_20260704/`), after
both hypothesised remediations (verdict store; stronger prior) were tested and
refuted. One gate objectively not cleared meant no incumbent switch; sustained
was retained.

DECISION-A pre-registered three paths to GO. This document executes **P1**,
and — as P1's own text requires — resolves the band question **P3** in the same
amendment. This is a reporting-caliber change, delivered as an explicit PRD
amendment, not an adjudication footnote. Signed **Option A** on 2026-07-05
(§4); adoption effective at sign-off, PRD delta applied (§8).

### Verbatim trigger text (DECISION-A, "Pre-registered paths to GO")

> - **P1 (recommended; owner decision required).** Amend the PRD to specify
>   cohort year-histogram deliverables as **posterior-mass (fractional)
>   aggregation** — exactly what ISSUE-03 shipped. The operative
>   reproducibility gate then becomes the survival/fractional channel, which
>   passes and beats the incumbent. This is a reporting-caliber change and
>   must be an explicit PRD amendment, not an adjudication footnote; if taken,
>   resolve the band question (P3) in the same amendment since one fractional
>   pair (0.067) still exceeds the absolute band.

> - **P3 (band hygiene).** Re-establish the band from ≥5 store-backed
>   production-channel reps (≥10 pairs), pre-registered before decoding the
>   candidate. Legitimate only computed on the production channel, never tuned
>   on the decoder.

DECISION-A's scope discipline is binding on this amendment: "the
pre-registered paths below terminate this decision without re-litigating the
gates that already passed." This amendment therefore touches **only** the
year-histogram-TVD / deliverable-caliber question (D2/D3) — see §9.3.

---

## 2. Owner rationale (recorded 2026-07-04)

Recorded verbatim as the owner's stated grounds for taking P1.

**(a) Downstream consumption is econometric.** The install-date inventory
feeds RD / DiD event-study designs in which **install-date accuracy IS
treatment-timing accuracy**. Measurement error in event time attenuates DiD
coefficients toward zero and contaminates the pre-trend estimates the design
rests on. A per-anchor **posterior / credible interval** output is strictly
more informative than a hard-MAP year and avoids the argmax flip noise that a
point date injects. ISSUE-03 AC3 already demonstrated the concrete failure of
**midpoint imputation** — the specific non-fractional aggregation it measured:
midpoint aggregation shifts L1 by **18.50 / 14.76 / 17.90** install-units per
rep, over-piles 2021–2023, and **zeroes both the 2024–2025 tail and the
2009–2017 early-adopter tail that posterior mass restores** — precisely the
event-study windows the RD/DiD design is built on. Fractional posterior mass is therefore load-bearing for the
downstream estimand, not a cosmetic reporting choice.

**(b) Reproducibility doctrine.** The owner's operative definition is
**computational reproducibility**: same frozen dataset + same code ⇒ identical
results. That standard already holds by construction. The decoder is
**deterministic given verdicts** (ISSUE-02 AC: no RNG anywhere, including EM —
fixed init + tolerance; property tests), and the verdict store freezes scorer
outputs (ISSUE-03: banked Panel A baselines re-verified **bit-exact** with the
prior injected — fpd `0.807/0.046/0.814`, sustained `0.911/0.857`).
Collection-level determinism **at a hosted-LLM API boundary is not attainable**
(no seeding, silent model updates) and is **not** the standard this program
holds estimators to. Re-collection variance is a property of the collection
process — it is **measured and bounded on the fractional channel** (AC5), not
eliminated by choice of estimator. The path to deterministic re-collection is a
**local frozen-weight scorer** (the Phase-3 DINOv3 student), which is out of
scope here; until it lands, the honest object is a bounded fractional
reproducibility figure, not a hard-MAP point-histogram distance.

---

## 3. Normative amendment text

Numbered A1–A6. These are the substantive changes; §7 renders them as the
mechanical PRD patch.

**A1 — Deliverable caliber.** The cohort year-histogram deliverable is
**posterior-mass (fractional) aggregation**, produced by the ISSUE-03
`eval/aggregate.py` **fractional** channel (of its three deliberately separate
cohort-year channels: point / midpoint / fractional posterior-mass). This
formalises what D2 already specifies ("Aggregation uses fractional counting of
posterior mass, not midpoint imputation") as the headline reporting object,
not merely an internal aggregation rule.

**A2 — Operative cohort reproducibility gate.** The operative cohort
reproducibility gate is the **survival / fractional channel**, not the
hard-MAP year histogram. It **passes and beats the incumbent**, recorded here
as pass:

- AC5 official (`issue03_gates.json`): survival-curve rep-to-rep TVD mean
  **0.050** vs point-date year TVD mean **0.075**, `beats_point_date: true`.
- ISSUE-03 same-run triple: survival `[0.0504 / 0.0596 / 0.0304]` mean
  **0.0468** vs same-run point-date year `[0.090 / 0.0314 / 0.0875]` mean
  **0.0696** — beats on mean + 2/3 reps (~33% more rep-stable). (rep2 orders
  the other way because rep2's point TVD is anomalously low — an upstream
  L2-search draw, ISSUE-03 C3 — not a survival regression.)
- Independent cross-check vs sustained-fractional: decoder wins **3/3**.

**A3 — Hard-MAP year table demoted.** The hard-MAP year histogram is demoted
to a **derived diagnostic**. Its 0.037–0.063 band is retired as a gate (see A6
and §5). The optional estimator-side stabiliser P2 (deterministic
tie-breaking, epoch snapping, or margin-smoothed MAP; a **decode-only** re-run
on the existing 3 store-backed reps, **zero LLM cost**) MAY be run to tighten
the diagnostic, but is **not gate-bearing** and is not a precondition of
adoption.

**A4 — Production default.** The production default estimator switches to the
**changepoint decoder + EB/Turnbull prior**: epoch-gap **45**, EM-fitted
3-symbol emissions, cohort prior per `issue03/cohort_prior.json` (EB prior fit
once on the 15,859-state production cohort `jhb_full382_fpcut_scan_2026-06-02`,
40 EM iters, `final_loglik −18659.79`); pinned code refs **89496dd**
(store-backed end-to-end) / **1daa61d** (prior add-on worktree). The operative
rule for **when** the switch takes effect is set in §4 (owner picks Option A or
Option B at sign-off).

**A5 — Caveats that MUST travel with all fractional deliverables.** Every
fractional cohort deliverable ships with, verbatim and non-negotiable:

- The **C5 2024 prior-mass dip** (0.0135 « 2023's 0.656) is a **Vexcel
  flight-date right-censoring artifact — never a market signal**. Do not read
  it as an install-rate decline.
- Survival curves are **grid-marginalised**: censoring is only conditionally
  non-informative given grid (per-grid median cadence spans 30.5–624.5 d over
  335 grids; cadence-stratified S(2021) spreads 0.081 vs 0.908). Read cohort
  curves as grid-marginalised, not as a clean population survival function.
- **D11 first-visible-appearance scope is unchanged.** Posterior mass is over
  **first-visible-appearance epochs under imagery-cadence censoring**; it
  claims **no** new precision about physical install dates (see §9.4).
- The one **0.067** delivered-caliber (survival/fractional) pair that still
  exceeds the old absolute band is **disclosed** on every deliverable until
  the P3 re-band (§5) resolves it.

**A6 — What does NOT change.** Explicitly out of the amendment's reach:

- All already-passed **panel-caliber gates are not re-litigated** (per
  DECISION-A's own terms): MAP mode-hit ≥ 0.882 (achieved 0.905, 0.942 with
  EB prior), HPD calibration ≈ 0.90 (0.914 → 0.937), dominant-stratum
  year-stability (0.867), inventory-weighted headline (0.863 → 0.923),
  undated-flip 0.055 at parity — all stand as adjudicated.
- **ISSUE-04's standing rule** ("no change to the production default until the
  decoder clears its gates") remains in force and is now **satisfied through
  the operative gate** (A2), not waived.
- The **retired hard-MAP absolute gate** is recorded as **retired-with-cause**
  (small-sample band fit on 3 pre-store pairs; production reference channel
  itself breached it at 0.068 on 1/3 pairs; hard-MAP argmax collapse is the
  instability source, not the posterior — DECISION-A root-cause 3/4), **not
  waived**.

---

## 4. Operative adoption rule — owner decision

A4's production switch needs an explicit effective-date rule. Two options;
recommendation is **Option A**. Owner ticks exactly one at sign-off.

- [x] **Option A (recommended; SIGNED 2026-07-05).** Adoption is **effective at owner sign-off**,
  on the strength of the already-passing pre-registered relative gate (A2).
  The P3 re-band (§5) runs as a **condition-subsequent** verification, with a
  pre-registered **rollback trigger**: *if the fractional channel breaches the
  re-derived band on a majority of its first pairs (≥ ⌈n/2⌉ of the ≥10 pairs),
  revert the production default to sustained and re-open DECISION-A.* Until the
  re-band completes, the 0.067 pair is disclosed (A5).
  - **Risk:** the cohort ships on the decoder before its band is independently
    re-established; if P3 later refutes the relative gate's generalisation, one
    delivered inventory was produced under a since-reverted default (mitigated
    by the pre-registered rollback trigger and the disclosure caveat).

- [ ] **Option B (conservative).** The **caliber change is signed now**
  (A1–A3, A5, A6 land in the PRD immediately), but the **production switch is
  deferred** until the re-derived band (§5) exists and the fractional channel
  clears it. Sustained remains the production default in the interim.
  - **Risk:** the cohort keeps shipping on **sustained** — the estimator
    DECISION-A found strictly less reproducible on the fractional channel and
    strictly worse on panel accuracy — for the several-rep, real-LLM-cost
    duration of the P3 re-band, delaying the downstream RD/DiD benefit for
    reporting-hygiene reasons the relative gate already addresses.

Owner: **Robert Gao**  Date: **2026-07-05**  Option: **A**
(Sub-decision resolved 2026-07-05: the §5.3 band formula is **mean ± 2 sd**,
registered with binding refinements in
[`ISSUE-21-band-prereg-2026-07-05.md`](ISSUE-21-band-prereg-2026-07-05.md).)

---

## 5. P3 band re-derivation protocol (normative)

Resolves P3. This is real follow-up **execution** work (API cost), not a
retroactive relabelling of the existing 0.067-exceeding pair (see §9.1).

1. **Sample.** ≥ **5** store-backed **production-channel** end-to-end reps ⇒
   ≥ **10** rep-to-rep pairs. **3 store-backed reps already exist**
   (`llm_endtoend_storebacked_20260704/`); therefore **≥ 2 more reps** are
   needed. Honest cost note: these are **real Gemini sequence calls** on the
   642-anchor cohort — two-tier production routing (round1 + L_census =
   `gemini-3-flash`; round2 escalation = `gemini-3-flash-agent`; per
   `llm_endtoend_analyze.py` model_routing — corrected 2026-07-05, the
   original "gemini-3-flash-agent calls" phrasing overstated the agent-tier
   share) — not free; budget and schedule before launch.
2. **Channel.** The band is computed on the **production/delivery channel
   only** (the survival/fractional deliverable of A1), **never tuned on any
   candidate estimator** (P3 constraint — P3's verbatim wording is "never tuned
   on the decoder", generalised here to any candidate estimator). It
   characterises the collection process, not the decoder.
3. **Pre-registration.** The band **formula is written down BEFORE the new
   reps are decoded.** Proposed forms (owner picks one at sign-off): **mean ±
   2 sd** of the pairwise fractional-channel TVDs, **or** the **min–max
   envelope** of the ≥10 pairs. Whichever is chosen is committed in the
   pre-registration note before any new rep is scored. *(Picked 2026-07-05:
   **mean ± 2 sd**; the binding registration — incl. the zero floor and the
   asymmetric breach semantics (upper edge → rollback trigger; lower edge →
   variance-collapse audit, never rollback) — is
   [`ISSUE-21-band-prereg-2026-07-05.md`](ISSUE-21-band-prereg-2026-07-05.md).)*
4. **Fresh-per-rep verdict stores.** Each re-band rep MUST run against a
   **fresh, empty verdict store** (`records=0` verified at launch), exactly as
   DECISION-A verified for the existing reps ("Stores were per-rep fresh …, so
   measured variance is genuine"). A store shared across reps would let a
   cached verdict never drift (D6), silently collapsing the very rep-to-rep
   variance the band is meant to measure — see §9.5.
5. **Retirement.** On completion, the **stale 3-pair band (0.037–0.063)** — fit
   on 3 pre-store pairs, and breached by the production reference channel
   itself at **0.068 on 1/3 pairs** — is **retired**, superseded by the
   re-derived production-channel band. Until then it survives only as the
   disclosure anchor for the 0.067 pair (A5).

---

## 6. Downstream (RD/DiD) consumption spec

The deliverables handed to downstream econometrics:

- **Per-anchor posterior mass** over first-visible-appearance epochs, in the
  `year_log_mass` form (the ISSUE-03 `CohortPrior(year_log_mass,
  beyond_log_mass)` representation), plus `P(undated) = P(τ > T)` and the
  credible interval.
- **Cohort survival curves** (grid-marginalised, A5), the operative
  reproducibility object (A2).
- **Fractional cohort-year tables** (`eval/aggregate.py` fractional channel).

Recommended usage: **interval-censored event-time** or **posterior-weighted**
RD/DiD designs that propagate dating uncertainty. **Hard MAP years are display
columns only** — never the regression's event-time input (they reintroduce the
argmax flip noise A2 avoids and zero the tails A3/AC3 documents).

---

## 7. Proposed mechanical PRD delta

Adoption is a mechanical patch to
`../install_date_optimization_v2_prd.md`. Quote → replacement for each passage;
one new D-entry (next free number **D19**). Nothing here is applied by this
draft — it is the exact edit set for the sign-off step (§8).

### 7.1 D2 — formalise the deliverable (append one clause)

**Current** (final two sentences of D2):

> Aggregation uses fractional counting of posterior mass, not midpoint
> imputation. A PAVA/isotonic single-changepoint fit is the mandatory
> falsification floor.

**Replacement:**

> Aggregation uses fractional counting of posterior mass, not midpoint
> imputation; the **cohort year-histogram deliverable and the survival curves
> are that fractional channel** (the headline reporting object, formalised in
> D19). A PAVA/isotonic single-changepoint fit is the mandatory falsification
> floor.

### 7.2 D3 — demote the hard-MAP TVD gate (reword the gate sentence)

**Current** (the TVD clauses inside D3):

> … HPD calibration proxy (rep-i's 90% interval contains rep-j's MAP ≈ 90%);
> year-histogram TVD within the established 0.037–0.063 band; survival-curve
> rep-to-rep TVD beats point-date TVD. Decision on cohort-wide adoption
> additionally requires the enlarged dominant-stratum panel (D4).

**Replacement:**

> … HPD calibration proxy (rep-i's 90% interval contains rep-j's MAP ≈ 90%);
> **the operative cohort reproducibility gate is the survival/fractional
> channel — survival-curve rep-to-rep TVD beats point-date TVD (D19); the
> hard-MAP year-histogram TVD 0.037–0.063 band is demoted to a derived
> diagnostic, retired-with-cause.** Decision on cohort-wide adoption
> additionally requires the enlarged dominant-stratum panel (D4).

### 7.3 D3 — add a distinctly-tagged pointer blockquote (NOT a "Correction")

Insert directly **after** the existing `> **Correction (2026-07-04, record
hygiene only …)**` blockquote already appended to D3. Uses an **Amended**
tag, not a second same-dated "Correction" tag, to avoid two ambiguous
same-dated Correction blocks on one D-entry (see §9.8):

> **Amended (2026-07-04, D19 — deliverable caliber; substantive):** the
> year-histogram TVD (hard-MAP) 0.037–0.063 band is **demoted to a derived
> diagnostic and retired-with-cause**; the operative cohort reproducibility
> gate is the **survival/fractional channel** (passes: AC5 survival mean 0.050
> vs point-date 0.075, `beats_point_date: true`). Unlike the record-hygiene
> Correction above, this **does** change the adoption verdict — see
> [`PRD-AMENDMENT-P1-posterior-mass-caliber-2026-07-04.md`](replan_v2/PRD-AMENDMENT-P1-posterior-mass-caliber-2026-07-04.md)
> and D19.

### 7.4 New decision entry (banner + provenance + D19)

Insert after D18, before `## Testing Decisions`:

> ### Amendment 2026-07-04: cohort deliverable caliber (D19)
>
> DECISION-A (2026-07-04) ruled the changepoint decoder NO-GO cohort-wide
> solely on the D3 hard-MAP year-histogram TVD band (0.037–0.063), which failed
> on the sanctioned store-backed re-run (flat `[0.079,0.040,0.076]` mean 0.065;
> EB prior `[0.092,0.047,0.085]` mean 0.075) after the verdict-store and
> stronger-prior remediations were both refuted. Every panel-caliber gate and
> the relative survival-reproducibility gate passed. Pre-registered path P1
> (owner decision) redefines the headline deliverable; this entry lands it.
>
> **D19 — Cohort deliverable caliber = posterior-mass (fractional); operative
> gate = survival/fractional channel (owner decision required).** (i) The
> cohort year-histogram deliverable is the fractional posterior-mass channel
> (ISSUE-03 `eval/aggregate.py`), formalising D2's aggregation rule as the
> headline object. (ii) The operative cohort reproducibility gate is the
> survival/fractional channel — it passes and beats the incumbent (AC5:
> survival mean 0.050 vs point-date 0.075, `beats_point_date: true`; same-run
> `[0.0504/0.0596/0.0304]` mean 0.0468 vs point `[0.090/0.0314/0.0875]` mean
> 0.0696; cross-check 3/3). (iii) The hard-MAP year histogram is a derived
> diagnostic; its 0.037–0.063 band is retired-with-cause (small-sample fit on 3
> pre-store pairs; production reference channel breached it at 0.068 on 1/3
> pairs; instability is hard-MAP argmax collapse, not the posterior). (iv)
> Production default switches to the changepoint decoder + EB/Turnbull prior
> (epoch-gap 45, EM emissions, `cohort_prior.json`; code `89496dd`/`1daa61d`);
> effective-date rule per the P1 amendment §4. (v) P3 band re-derivation is
> mandatory follow-up: ≥5 store-backed production-channel reps (≥10 pairs),
> pre-registered before decoding, fresh-per-rep stores, never tuned on the
> decoder (→ ISSUE-21). (vi) All already-passed panel gates are not
> re-litigated; D11 first-visible-appearance scope unchanged; the C5 2024-dip
> and grid-marginalisation caveats travel with every deliverable. Full
> normative text:
> [`replan_v2/PRD-AMENDMENT-P1-posterior-mass-caliber-2026-07-04.md`](replan_v2/PRD-AMENDMENT-P1-posterior-mass-caliber-2026-07-04.md).

### 7.5 Testing Decisions — update the "gate IS the acceptance test" bullet

**Current** (InstallDateEstimator seam bullet, final sentence):

> The D3 gates run as an offline evaluation script whose outputs are asserted
> against thresholds — the gate IS the acceptance test.

**Replacement:**

> The D3 gates run as an offline evaluation script whose outputs are asserted
> against thresholds — the gate IS the acceptance test; **the operative
> cohort-reproducibility assertion is the survival/fractional channel (D19), so
> `scripts/validation/issue03_gates.py`'s AC5 survival assertion is the
> gate-bearing threshold and the retired hard-MAP year-histogram TVD band is a
> reported diagnostic, not a pass/fail assertion** (see §9.6).

### 7.6 Further Notes — add a provenance bullet

Append to `## Further Notes`:

> - Provenance (amendment): the 2026-07-04 cohort-deliverable-caliber amendment
>   (P1 + P3, D19) executes DECISION-A's pre-registered path P1; it redefines
>   the headline cohort deliverable as fractional posterior mass and the
>   operative gate as the survival channel, and schedules the P3 band
>   re-derivation. Evidence base:
>   [`replan_v2/DECISION-A-estimator-adoption-2026-07-04.md`](replan_v2/DECISION-A-estimator-adoption-2026-07-04.md),
>   ISSUE-02, ISSUE-03; full amendment in
>   [`replan_v2/PRD-AMENDMENT-P1-posterior-mass-caliber-2026-07-04.md`](replan_v2/PRD-AMENDMENT-P1-posterior-mass-caliber-2026-07-04.md).

Passages left **unchanged** (addressed only in §9, no edit): Problem Statement
item 1, Solution/Phase 0, User Stories 2–4, D1, D4, D8, D11, Out of Scope. The
first-visible-appearance framing (D11, Out of Scope) is preserved verbatim.

---

## 8. Adoption checklist

Unchecked until executed at sign-off. This draft touches only this file;
everything below is follow-up.

- [x] **Owner sign-off** — name / date / Option A|B recorded in §4. *(Done
  2026-07-05: Robert Gao, Option A.)*
- [x] **Apply the PRD delta** (§7.1–§7.6) to
  `../install_date_optimization_v2_prd.md`; flip this file's Status from
  **DRAFT — pending owner sign-off** to **ADOPTED (D19)**. *(Done 2026-07-05;
  D19 landed with "(owner decision 2026-07-05: Option A)" per the D18 tag
  precedent, and §7.5's dangling "(see §9.6)" was applied as a link to this
  amendment's §9.6.)*
- [x] **DECISION-A addendum** — append a dated block to
  `DECISION-A-estimator-adoption-2026-07-04.md` recording the verdict flip:
  NO-GO (hard-MAP caliber) → GO (fractional caliber via D19), with the
  effective-date rule (§4 option) and a link to this amendment. *(Done
  2026-07-05.)*
- [x] **TRACKER.md updates** (then re-run `python3
  docs/replan_v2/render_tracker.py`):
  - Add Slices row **20** — `| 20 | [Cohort deliverable caliber amendment
    (D19)](ISSUE-20-deliverable-caliber-amendment.md) | 0 | ready-for-agent |
    — |` (a docs delta; D2/D3/D4 already done/discharged).
  - Add Slices row **21** — `| 21 | [P3 band re-derivation reps](ISSUE-21-p3-band-rederivation.md)
    | 0 | ready-for-human | 20, 7 |` (real LLM-cost execution, fresh-per-rep
    stores; distinct from the docs delta — do not conflate "write the
    amendment" with "run ≥2 more production reps").
  - Add mermaid nodes `I20[20 caliber amendment]`, `I21[21 P3 band reps]`;
    edges `I2 --> I20`, `I3 --> I20`, `I4 --> I20`, `I20 --> I21`, `I7 --> I21`.
  - Rewrite the existing **ISSUE-02 note** paragraph: the NO-GO is now
    **qualified/flipped under the fractional deliverable definition (D19)** —
    link both DECISION-A and this amendment; add the sentence "Slice 20 was
    added 2026-07-04 from DECISION-A's P1/P3 pre-registered path (PRD amendment
    block, D19); slice 21 tracks the P3 band re-derivation reps."
  - Move slices 20 (and later 21) into "Done so far:" only once their Status
    lines actually read `done`.
- [x] **New ISSUE files** *(done 2026-07-05; ISSUE-20 born done — the §7
  delta was executed at sign-off)* — `ISSUE-20-deliverable-caliber-amendment.md`
  (Parent → D19 + user stories 2, 4; What-to-build = the §7 mechanical delta;
  Acceptance = grep-verified PRD edits) and `ISSUE-21-p3-band-rederivation.md`
  (Parent → D19 + P3; What-to-build = §5 protocol; Acceptance = ≥5
  store-backed reps, pre-registered band, fresh-per-rep stores verified
  `records=0`). Follow the ISSUE-12 shape.
- [x] **Production config + provenance** — switch the default estimator to the
  decoder + EB prior (A4); config provenance MUST carry **estimator id + prior
  hash** (`cohort_prior.json`) alongside code refs `89496dd`/`1daa61d`.
  *(Done 2026-07-05, narrow caliber, see
  [ISSUE-22](ISSUE-22-d19-production-switch.md) AC1: `estimator_endtoend_decode.py`
  default estimator/epoch-gap/prior/emissions flipped to the A4 working point;
  every run's `provenance` block carries `estimator_id`, sha256 of
  `cohort_prior.json`/emissions, `decision_refs`, and `adopted_code_refs=
  {89496dd, 1daa61d}`; regression + AC7 bit-exact guardrails PASS.)*
- [x] **Report-builder switch** — cohort report tables read the **fractional**
  channel; hard-MAP years demoted to display columns; C5-dip +
  grid-marginalisation + 0.067-disclosure caveats attached (A5).
  *(Done 2026-07-05, narrow caliber, see
  [ISSUE-22](ISSUE-22-d19-production-switch.md) AC2:
  `scripts/validation/build_cohort_fractional_year_table.py` produces the
  full-cohort (N=15,859) fractional year table from the existing production
  posterior — closed-form aggregation, not a per-anchor re-decode — with the
  A5 caveats and the 0.067 disclosure attached verbatim in
  `~/zasolar_data/geid_temporal/cohort_fractional_year_20260705/`. The
  **wide-caliber** production re-decode + main-repo report wiring is
  **deferred to ISSUE-22 AC5**, blocked by ISSUE-10/ISSUE-11.)*
- [x] **Gate runner** — annotate/retire the hard-MAP year-histogram TVD
  assertion in `scripts/validation/issue03_gates.py` (and the
  `fullstack_noscan_analyze.py` / `panel_repair_d8_compare.py` reporting path)
  so the AC5 survival assertion is the gate-bearing threshold and the hard-MAP
  band is reported as a diagnostic, not asserted pass/fail (§9.6).
  *(Done 2026-07-05, narrow caliber, see
  [ISSUE-22](ISSUE-22-d19-production-switch.md) AC3: the hard-MAP band
  assertion is report-only in `estimator_harness.py` /
  `estimator_endtoend_decode.py`; `issue03_gates.py`'s AC5 survival-gate
  structure is untouched and remains gate-bearing (only an additive
  `caliber` annotation on the point-date sub-channel); diagnostic-only notes
  added to `fullstack_noscan_analyze.py` / `panel_repair_d8_compare.py`.)*

---

## 9. Risk resolutions

Each Scout-flagged contradicting passage gets an explicit resolution.

**9.1 — P3 no-tuning constraint** ("Legitimate only computed on the production
channel, never tuned on the decoder"). Resolved by §5: the band is a **real
follow-up execution** step — ≥5 store-backed production-channel reps, ≥10
pairs, formula **pre-registered before decoding the candidate**, computed on
the production channel only. It is **not** a retroactive relabelling of the
existing 0.067-exceeding pair; the 0.067 pair is **disclosed** (A5) until the
re-band supersedes it, not silently reclassified as passing.

**9.2 — D8 rule (5) "no cohort decision from a stratum with n=2 support" + D3's
closing D4 precondition.** Both already satisfied and cited, not bypassed:
DECISION-A records "D4 is fully discharged: dominant stratum **n=2 → 12**
(≥8 required); tight-crop hypothesis CONFIRMED." This amendment inherits that
discharged precondition and does not re-open it.

**9.3 — DECISION-A scope discipline** ("terminate this decision without
re-litigating the gates that already passed"). This amendment touches **only**
the year-histogram-TVD / deliverable-caliber question (D2/D3, A1–A3). The
panel-caliber gates that already passed — MAP mode-hit ≥ 0.882, HPD
calibration ≈ 0.90, dominant-stratum year-stability — are stated as **standing,
not reopened** (A6). No panel number is recomputed or renegotiated here.

**9.4 — D11 + Out of Scope (physical install dates).** Redefining the
deliverable as posterior-mass/fractional makes **no** claim of new precision
about physical install dates. The posterior is over
**first-visible-appearance epochs under imagery-cadence censoring**; D11 and
the Out-of-Scope "first visible appearance" framing are **preserved verbatim**
(A5, §7.6). Fractional mass narrows the *representation of dating uncertainty*,
not the physical-date claim.

**9.5 — D6 verdict-store correctness vs P3 measurement validity.** A subtle
contradiction: D6's cost-saving intent (a cached verdict "can never drift")
would, if a store were shared across the P3 re-band reps, **collapse the
genuine rep-to-rep variance** the band exists to measure — invalidating the
measurement. Resolved by §5.4: re-band reps **MUST use fresh, empty
(`records=0`-verified) per-rep stores**, matching DECISION-A's own verification
that "measured variance is genuine." This is written into the ISSUE-21
acceptance criteria.

**9.6 — Testing Decisions "the gate IS the acceptance test".** A prose-only
amendment would falsify this clause, because the authoritative metric changes
from hard-MAP year-histogram TVD to the survival/fractional channel. Resolved
by §7.5: `scripts/validation/issue03_gates.py`'s **AC5 survival assertion
becomes the gate-bearing threshold**, and the hard-MAP year-histogram TVD in
`fullstack_noscan_analyze.py` / `panel_repair_d8_compare.py` is reported as a
diagnostic, not asserted. The gate script is updated in code (checklist §8), so
the clause stays true.

**9.7 — Owner-decision gating precedent.** D4 is tagged "(budget approved)",
D12.iv "(owner decision)", D18 "(owner decision 2026-07-03)"; DECISION-A's P1
is "(recommended; owner decision required)" and demands "an explicit PRD
amendment, not an adjudication footnote." This is not a routine mechanical docs
delta — it redefines the **headline cohort deliverable**. The document is
therefore **DRAFT — pending owner sign-off**, D19 carries "(owner decision
required)", and §4 requires an explicit signed option before the production
switch takes effect.

**9.8 — Numbering/dating collision on D3.** D3 already carries a
`Correction (2026-07-04, record hygiene only …)` blockquote whose tag
explicitly promises **no** verdict change. This amendment **does** change the
adoption verdict, so it must not stack a second same-dated "Correction" on D3
(two same-dated Correction blocks would be ambiguous about which supersedes).
Resolved by using a **new D19 entry** (§7.4) plus a distinctly-tagged
`Amended (2026-07-04, D19 — … substantive)` pointer on D3 (§7.3) — never a
second "Correction" tag.

---

## Provenance

- Provenance (amendment): drafted 2026-07-04 as the execution of DECISION-A's
  pre-registered path P1 (with P3 folded in per P1's text). Evidence base:
  [`DECISION-A-estimator-adoption-2026-07-04.md`](DECISION-A-estimator-adoption-2026-07-04.md)
  (verdict + gate table + root cause + P1/P3),
  [`ISSUE-02-changepoint-posterior-decoder.md`](ISSUE-02-changepoint-posterior-decoder.md)
  (decoder, determinism-given-verdicts AC),
  [`ISSUE-03-turnbull-survival-prior.md`](ISSUE-03-turnbull-survival-prior.md)
  (fractional channel, EB prior, AC5 survival gate, C-caveats),
  [`ISSUE-04-decision-memo-2026-07-03.md`](ISSUE-04-decision-memo-2026-07-03.md)
  (D4 discharge, extended-panel re-anchor, undated-flip correction lineage).
  Artifact roots: `~/zasolar_data/geid_temporal/issue03_gates_20260704/`,
  `~/zasolar_data/geid_temporal/llm_endtoend_storebacked_20260704/`.
