# ISSUE-04 Decision Memo — Reliability panel repair (2026-07-03)

Parent: [`ISSUE-04-reliability-panel-repair.md`](ISSUE-04-reliability-panel-repair.md) ·
PRD D4, decided under D3 + D8 ([`../install_date_optimization_v2_prd.md`](../install_date_optimization_v2_prd.md))

## TL;DR

1. **The catastrophic weighted headline was an n=2 artifact.** With the
   dominant stratum enlarged 2 → 12 units, the inventory-weighted sustained
   **year**-hit recovers 0.469 → **0.757**; the weighted **date**-hit was
   never broken (0.798 → 0.798, unchanged). The fact-check's "weighted
   collapse" was driven by one unit (`c0015576/T01`, stable-UNDATED,
   year-hit 0) carrying ~34% of the headline weight at n=2.
2. **Under-resolution hypothesis: CONFIRMED.** Rescoring the
   `gemini_failed` stratum at 12 m / 256 px eliminates undated-flips
   (0.113 → 0.000) and abstains (0.025 → 0.001) and lifts sustained-year
   agreement 0.838 → 0.938. That stratum's noise is an **imaging artifact of
   the render geometry**, not scorer give-up. This changes the decoder's
   emission model and the student's chip-rendering spec (details below).
3. **Sustained remains the interim cohort-wide estimator** (beats FPD on
   every D8 variant and on the dominant stratum). The **decoder adoption
   bars are re-anchored** to the extended panel: the D4 precondition of D3
   is now satisfied; ISSUE-02's decoder is evaluated against the numbers in
   §Decision when the ISSUE-01 harness lands.
4. Spend: **3,110 Gemini sequence calls**, 0 transport errors — within the
   approved envelope (~77% of the banked run's volume).

## What was run

Protocol identical to the banked `fullstack_noscan_20260630` run: same
sequence instrument, same model alias `gemini-3-flash-agent`, window=8,
K=10 reps, fixed exhaustive vintage grid, no adaptive search. All scoring ran
from a **frozen code worktree** at `d9be6df` plus the (untracked) Jun-30
`fullstack_noscan_*` scripts — the live tree was being refactored
concurrently (ISSUE-05 seam work), see Infrastructure notes.

| run | units | calls (jobs) | frames scored | errors |
|---|--:|--:|--:|--:|
| `done_appears` extension (60 m / 128 px, banked geometry) | 10 new | 1,710 | 13,390 | 0 |
| tight-crop rescore of `gemini_failed` (12 m / 256 px) | 8 | 1,400 | 10,800 | 0 |

**Sampling** (`scripts/validation/panel_repair_extend_sample.py`): the mini
panel's 2 `done_appears` anchors are verifiably the first 2 elements of the
seed-42 shuffle of the 10,930-anchor stratum population; the extension
continues that exact order from position 2 (a valid SRS continuation, no
re-draw). Stop rule "accumulate anchors until ≥8 new units" picked 4 anchors
(c0006367, c0015563, c0002019, c0008678) = 10 new units → stratum support
n=12. Full vintage stacks (493 chips) frozen via the standard prefetch; all
new renders use the banked crop geometry.

Artifacts: `~/zasolar_data/geid_temporal/panel_repair_20260703/`
(`sample/`, `chips_frozen/`, `fullstack_new_appears/`, `tightcrop_failed/`,
`analysis_extended/`, `code_frozen/`). The extended frame-verdict table is
`analysis_extended/long_all_extended.csv` (44,790 rows = banked 28 units +
new 10); the extended stratum map is `sample/sample_anchors_extended.csv`.

## Result 1 — Extended panel (n=38, `done_appears` n=12)

D8 comparison (`analysis_extended/d8_summary.json`, computed by
`scripts/validation/panel_repair_d8_compare.py`, which regression-reproduces
the banked 0.911 / 0.807 / 0.046 exactly before extension):

| estimator | variant | date-hit | year-hit | undated-flip |
|---|---|--:|--:|--:|
| sustained | unweighted | **0.882** | 0.842 | **0.034** |
| sustained | inventory-weighted (headline) | **0.798** | **0.757** | **0.020** |
| FPD | unweighted | 0.732 | 0.745 | 0.034 |
| FPD | inventory-weighted | 0.609 | 0.665 | 0.020 |

`done_appears` stratum (n=12, weight 0.689): sustained 0.800 date / 0.717
year / 0.025 flip vs FPD 0.542 / 0.567. Distribution: 6 units ≥ 0.9, 5 units
wobbly (0.5–0.6), 1 stable-UNDATED. The stratum is genuinely the hardest
non-trivial stratum (single small installs, mixed vintage quality) but not
the catastrophe the n=2 estimate implied:

- date-level stratum mean: 0.80 (n=2) → **0.800 (n=12)** — the n=2 estimate
  was accidentally right at date level;
- year-level stratum mean: 0.30 (n=2) → **0.717 (n=12)** — the n=2 estimate
  was an artifact of `c0015576/T01` (year-hit 0.0) being half the sample.

FPD deteriorates most exactly where the weight is (0.542 date-hit on the
dominant stratum) — single false-present frames dominate it. This closes the
case for naive FPD at any crop (see also Result 2: tight crop does *not* fix
FPD).

## Result 2 — Tight-crop rescore (under-resolution hypothesis)

Same 8 `gemini_failed` units, same frozen stacks, re-rendered at ~12 m /
256 px (`--crop-context-multiplier 0.5 --min-crop-size-m 12
--min-output-px 256`; banked render was effectively 60 m context at ≥128 px).
Full comparison: `tightcrop_failed/analysis_smoke/` (per-unit CSV + tables,
`scripts/validation/panel_repair_tightcrop_compare.py`).

| metric (mean over 8 units) | banked 60 m | tight 12 m | Δ |
|---|--:|--:|--:|
| undated-flip rate | 0.113 | **0.000** | −0.113 |
| abstain rate (frames) | 0.025 | **0.001** | −0.024 |
| sustained date-hit | 0.938 | 0.938 | 0 |
| sustained year-hit | 0.838 | **0.938** | +0.100 |
| FPD date-hit | 0.675 | 0.662 | −0.013 |

Per-unit: 6/8 keep identical modal sustained dates and tighten to ~1.0
agreement. The two changes are informative:

- `c0010157/T02`: UNDATED (0.9 flip) → dates cleanly to **2024-02-29** at
  1.0 agreement. Visual check: a small dark panel on a dark roof — invisible
  at 60 m/128 px, unambiguous at 12 m/256 px. A **recovered unit**.
- `c0010157/T04`: banked confidently said 2019-08-30 (1.0 agreement); tight
  crop shifts to 2021-10-30 at 0.5 agreement, FPD disagreeing (2017). Visual
  check: the 2019-08-30 frame shows a **bare roof** (the banked date was a
  coarse-crop false positive); the 2021 frame is unusably blurry. The unit is
  genuinely ambiguous imagery — the coarse crop wasn't just noisy here, it
  was **confidently wrong**.

**Verdict: CONFIRMED.** The `gemini_failed` stratum's failure/abstain/UNDATED
behavior is an imaging artifact of render geometry. Two caveats survive:
(i) FPD's date-level wobble is *not* resolution-driven (isolated
false-present frames persist at tight crop); (ii) coarse crops can shift the
emission *bias*, not just variance (T04) — a resolution-conditioned emission
model is required, not merely a variance inflation term. Scorer
`sequence_confidence` barely moves (0.954 → 0.936): **self-reported
confidence carries no resolution signal** and must not be used as a proxy.

### Implications

- **Decoder emission model (D2 / ISSUE-02):** estimate the 3-symbol
  confusion matrix conditioned on render resolution (crop size / output px /
  `actual_zoom`), or — better — move the scoring path to the tight crop and
  estimate emissions there. Production's `gemini_failed` sentinel should be
  treated as *censoring-with-cause (render too coarse)*, not as a behavioral
  stratum of the scorer.
- **Student chip-rendering spec (D12 / ISSUE-19):** adopt target-centered
  **~12 m / 256 px** as the scoring-crop candidate for the Phase-3 re-render.
  ISSUE-19's dependency on ISSUE-04 is now satisfied with a concrete,
  evidence-backed recommendation (this is the "D4 crop experiment" the PRD's
  user story 38 refers to).

## Decision (per D3 + D8)

1. **Interim estimator: sustained is adopted / retained cohort-wide.** It
   beats FPD on every D8 variant (weighted, unweighted, dated-only) and on
   the dominant stratum (0.800/0.717 vs 0.542/0.567). No change to the
   production default until the decoder clears its gates.
2. **Decoder cohort-wide adoption: precondition SATISFIED, decision now
   well-posed.** D3's requirement of an enlarged dominant-stratum panel (D4)
   is met (n=12 ≥ 8). The decoder cannot be adopted or rejected today —
   ISSUE-01's harness and ISSUE-02's decoder are still in build — but the
   acceptance bars are now fixed on the extended panel and supersede the
   stale constants (0.911 / 0.046, which were n=28 numbers with n=2 dominant
   support):
   - MAP mode-hit ≥ **0.882** and undated-flip ≤ **0.034**, unweighted, on
     the extended 38-unit panel ("beat sustained on its own turf");
   - inventory-weighted headline ≥ **0.798 date / 0.757 year**;
   - dominant-stratum year-stability ≥ FPD's **0.567**, target ≥ sustained's
     **0.717**;
   - HPD-calibration and TVD gates as written in D3 (unchanged).
3. **Emission model and chip spec change as per Result 2 implications** —
   these are inputs to ISSUE-02 (decoder) and ISSUE-19 (chip-geometry
   policy) respectively.

## API spend

| item | calls | notes |
|---|--:|---|
| tight-crop rescore | 1,400 | 50 workers, qps 10, ~5 min, 0 errors |
| `done_appears` extension | 1,710 | 50 workers, qps 10, ~9 min, 0 errors |
| preflights + model listing | 3 | gateway migration checks |
| **total** | **≈3,113** | ≈77% of the banked run's 4,020 |

All `gemini-3-flash-agent` (antigravity upstream), zero paid-API dollars
(subscription quota). Within the D4 "targeted flash calls" envelope.

## Infrastructure notes (for the record)

- **Gateway migration:** sub2api's postgres was found re-initialized empty
  at 2026-07-03 12:11 (accounts + API keys lost; last successful service
  2026-06-30; the three docker anonymous volumes are empty stubs — old data
  not recovered). Scoring was migrated to **cli-proxy-api
  (localhost:8317)** — same antigravity upstream, same model alias, 13
  auto-refreshing accounts. `solar_backdating/.env.gemini.local` updated
  (old config in `.env.gemini.local.bak-20260703-sub2api`). Throughput at 50
  workers / qps 10: 3.1–5.0 job/s (banked era: 2.6 job/s at 35/8).
- **Concurrent-edit hazard:** the live tree was mid-refactor (ISSUE-05
  PresenceScorer seam) during this session, and `fullstack_noscan_run.py`
  itself was rewritten mid-run-setup. All scoring therefore ran from a git
  worktree pinned at `d9be6df`
  (`~/zasolar_data/geid_temporal/panel_repair_20260703/code_frozen/`) with
  the Jun-30 runner restored verbatim. The extended panel is bit-compatible
  with the banked protocol.
- The extended panel (`long_all_extended.csv` + `sample_anchors_extended.csv`)
  is the designated regression input for the ISSUE-01 harness (its
  mode-hit/tier mechanics were ported 1:1; `panel_repair_d8_compare.py`
  reproduces the banked numbers exactly and provides an independent
  cross-check).
