# ISSUE-09: CoJ audit at cohort scale + contradiction dataset

Status: ready-for-agent
Phase: 2 — Accuracy channel
Blocked by: ISSUE-08

## Parent

[`../install_date_optimization_v2_prd.md`](../install_date_optimization_v2_prd.md) — D9. User stories 15, 20.

## What to build

Scale the pilot to the full dated JHB inventory (~11.8k anchors): fetch
2019 + 2023 chips (plus 2015 for pre-2019 intervals) under the pilot's
politeness/rate plan, score, and publish the **cohort contradiction
dataset** — per-anchor dated presence bits, contradiction flags against
inferred intervals, and detector margins — in a documented schema consumable
by the gold-set sampler (ISSUE-10) and the economic event study.

The 2019/2023 flights bracket the load-shedding boom where most installs
fall; with median interval width ~475 days, a single 2023 bit can split or
falsify a large share of intervals — per-stratum contradiction rates are the
headline output.

## Acceptance criteria

- [ ] Coverage ≥ 95% of dated anchors (fetch failures enumerated, not silently dropped)
- [ ] Dataset schema documented; consumed successfully by the ISSUE-10 sampler
- [ ] Per-stratum contradiction-rate table published
- [ ] Self-gates from the pilot still pass at scale (known-sign, clamp monotonicity, noise floor)
- [ ] Server load kept within the pilot's politeness plan (rate/backoff logged)

## Blocked by

- ISSUE-08 (pilot go/no-go + margin rule)
