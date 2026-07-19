# DATA — T0 paired ablation (three-state emission vs discrete incumbent)

Status: **COMPLETE — reports a §7.4 early-exit candidate to owner (report only,
not self-shelving).** Protocol: DESIGN-phase0-emission-extension-2026-07-19 §5.3
stage T0 (owner-approved early-exit checkpoint). Zero new Gemini quota.

- Script: `scripts/temporal/run_t0_paired_ablation.py`
- Protocol lock + data:
  `~/zasolar_data/geid_temporal/run3_native_line_2026-07/t0_ablation_v1/`
  (`T0_LOCK.json`, `t0_results.json`, `per_anchor_years.csv`)
- Inputs (banked, read-only, SHA-pinned in `T0_LOCK.json`):
  `repeat_ceiling_v1/scoring_provenance_rep{1,2,3}.jsonl` (576 anchors × 4276
  frames each), `panel_anchors.csv`, `PANEL_LOCK.json` (all SHAs matched the
  frozen panel lock before any TVD was computed).

## TL;DR

**The control (incumbent discrete path) does NOT reproduce DECISION-A's
~0.065-class over-band hard-MAP TVD** on this RUN3-native repeat-ceiling panel —
control mean = **0.032**, *below* the [0.037, 0.063] band's lower edge and less
than half of 0.065. The §5.3 point-1 alternative explanation ("geometry/routing
fix already fixed the aggregate TVD") is **CONFIRMED**. The three-state emission
treatment moves nothing (mean **0.0308**, Δ = 0.0012, statistically
indistinguishable) because there is no over-band residual for it to close. This
is a **premise failure, not emission-hypothesis falsification**. Per §5.4 it is
a candidate §7.4 early-exit trigger: **owner decision required.**

## Panel (why it is cleaner than DECISION-A's)

This is a **repeat-ceiling** run: the *same fixed* 576-anchor / 4276-frame panel
re-scored 3×. Geometry and adaptive routing are therefore held **constant by
construction** — the only rep-to-rep variable is Gemini verdict/confidence
stochasticity. DECISION-A's own root-cause #3 named **adaptive-search routing
divergence amplified by MAP-argmax collapse** as the residual TVD driver; that
driver is **absent here by design**. So a low control TVD is *expected*, and its
being sub-band directly corroborates DECISION-A's attribution (the over-band was
routing/geometry, not emission definition).

## Protocol (locked before any number was computed)

| | Control | Treatment |
|---|---|---|
| Path | discrete `fit_emissions_em` + `epoch_symbol` majority vote | continuous `FrameEmission` + `frame_loglik` soft marginalization |
| Emission | ONE matrix fit on pooled 3-rep obs (gap=45), reused across reps (mirrors DECISION-A's single cohort-wide fit) | per-frame `e0/e1` from confidence (reliability `r = max(conf, 0.5)`; present→`e1=r,e0=1-r`; absent→`e0=r,e1=1-r`) |
| ambiguous/unusable | → abstain (usable-only vote); **no** `target_localized` gate | → `q=0` uninformative (target_localized **PROXY** = quality_flag ∈ {ambiguous, unusable}; None verdict → `q=0` too) |
| Decoder | `changepoint`, gap=45, **flat clamp** | same |
| Statistic | pairwise C(3,2) hard-MAP install-year histogram TVD (`estimators.losses.pairwise_tvd`, cross-checked vs `eval.metrics.tvd`) | same |
| Weighting | corpus_share-weighted primary (R0 MANIFEST corpus_share / anchors_by_stratum — same source as `CEILING_RESULT.json`); unweighted secondary | same |
| Band | **[0.037, 0.063]** — original hard-MAP band (DECISION-A), NOT the ISSUE-21 fractional band | |

**Proxy disclosure (required by §5.3):** `target_localized` is a **proxy**
(quality_flag membership). The banked Gemini-only labels carry no true
`TargetLocalizationObservation`; this stands in for one and is explicitly not a
real localization observation.

## Results — pairwise hard-MAP year-histogram TVD (corpus_share-weighted)

| pair | Control | Treatment | Control+P2 | Treatment+P2 |
|---|---|---|---|---|
| rep1–rep2 | 0.0430 | 0.0402 | 0.0320 | 0.0307 |
| rep1–rep3 | 0.0291 | 0.0320 | 0.0207 | 0.0299 |
| rep2–rep3 | 0.0241 | 0.0202 | 0.0221 | 0.0238 |
| **mean** | **0.0320** | **0.0308** | **0.0249** | **0.0281** |
| max | 0.0430 | 0.0402 | 0.0307 | 0.0307 |

Unweighted means (secondary): control **0.0285**, treatment **0.0273**,
control+P2 **0.0227**, treatment+P2 **0.0243** — same picture, all sub-band.

Dated anchors per rep: control 482/479/484, treatment 477/469/477 of 576 (~83 %;
undated excluded from the year histogram, matching `issue03_gates`' point
channel). Year mass spreads across 2019–2025 (not a degenerate single-year
collapse).

## Decision — rule triggered

`CONTROL_SUBBAND__GEOMETRY_ROUTING_FIX_ALTERNATIVE_CONFIRMED`

- **Control reproduces over-band?** NO (0.032 < 0.063; < 0.037 band floor; < ½ of
  0.065). The §5.3 point-1 guard fails.
- **Treatment vs control?** Indistinguishable (Δmean = 0.0012). Nothing to close.
- Because the premise (an over-band residual) does not hold, the paired contrast
  **cannot support or falsify** the emission hypothesis on this panel.

## §5.2 contribution decomposition (reported separately, never a blanket verdict)

- **Emission-definition effect** (control_mean − treatment_mean) = **0.0012** —
  the contamination-driven near-tie reduction from switching to three-state soft
  emission. Negligible, as expected when control is already sub-band.
- **Argmax-collapse residual addressable by P2** (treatment_mean −
  treatment_p2_mean) = **0.0027** — the irreducible-argmax part P2 (posterior
  year-mass argmax, train-free/data-free) can still remove. P2 helps control
  more (0.032→0.0249) than treatment (0.0308→0.0281), consistent with the
  residual being argmax collapse rather than emission mis-calibration (DECISION-A
  root-cause #3 / §5.2). **Neither contribution is a mechanism win**; both are
  small because there is no over-band problem to begin with.

## The one signal that is NOT sub-band: per-anchor instability

The **per-anchor** hard-MAP year flip rate (a year not identical across all 3
reps) is **high** — control **30.7 %**, treatment **32.1 %** (P2: 31.6 % /
32.6 %). This matches `CEILING_RESULT.json`'s ~76–78 % per-anchor agreement
(≈22–24 % disagree on the exact interval; year is coarser but flips more). The
aggregate histogram TVD is low precisely because these per-anchor flips are
roughly symmetric and **cancel in a distributional distance** — exactly
DECISION-A root-cause #3 ("the instability is a property of collapsing a
posterior to a hard MAP year, not of the posterior itself").

**Scope note:** T0 tests the *aggregate hard-MAP histogram TVD* (the DECISION-A
band). It does **not** test *per-anchor* date fidelity — the separate, still-open
problem the CEILING statistic (0.7724 / ~0.76) measures. Whether a trained
three-state student improves per-anchor fidelity is untouched by this diagnostic
and is not adjudicated here.

## Recommendation to owner (report only)

Per DESIGN §5.4, the owner-approved early-exit checkpoint may fire **before** R3
training when T0 shows "three-state emission reclassification does not change the
TVD and no other new hypothesis exists." On this panel:

1. The DECISION-A aggregate-TVD problem **does not reproduce** — there is no
   over-band residual for the emission change to justify R3–R5 compute *against
   the aggregate-histogram-TVD objective*.
2. This corroborates DECISION-A's own routing/argmax attribution rather than the
   emission-mis-calibration hypothesis.
3. **Caveat that keeps this from being a blanket kill:** the panel removes
   routing divergence by construction, so it cannot separate "geometry repair
   fixed the TVD" from "this panel has no routing divergence to begin with" —
   both are confounded, and both point *away* from emission definition as the
   lever for aggregate TVD. And the per-anchor fidelity objective is untested.

Therefore this is reported as a **§7.4 early-exit trigger candidate** for the
aggregate-TVD objective, with the per-anchor-fidelity question explicitly left
open. **Owner decides**; this memo does not shelve anything.

## Reproduce

```bash
source scripts/activate_env.sh
python scripts/temporal/run_t0_paired_ablation.py
# writes T0_LOCK.json (protocol + input SHAs) BEFORE any TVD, re-checks SHAs,
# then t0_results.json + per_anchor_years.csv
```
