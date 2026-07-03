# ISSUE-08: CoJ true-date audit — 100-anchor pilot (tracer)

Status: in-progress
Phase: 2 — Accuracy channel
Blocked by: none

## Parent

[`../install_date_optimization_v2_prd.md`](../install_date_optimization_v2_prd.md) — D9. User stories 15, 16.

## What to build

End-to-end pilot of the municipal true-date audit on ~100 dated anchors:
fetch anchor-centered chips from the City of Johannesburg 2019 and 2023
aerial layers (true single flight dates, 15 cm — in-domain GSD for the
existing census detector/classifier), score PV presence, join against the
anchors' inferred install intervals, and emit per-anchor **dated presence
bits** plus a **contradiction flag**.

Self-gates ship with the pilot: known-sign strata (anchors whose status
implies a known expected reading on the 2023 layer must agree >95%) and
monotone consistency with the Vexcel present-clamp (present@2023 ⇒
present@2024). Only high-margin detector calls count as bits; a margin
threshold and low-margin routing rule (→ human queue) are defined here.

## Acceptance criteria

- [ ] ~100-anchor pilot runs end-to-end: fetch → score → join → flags — sample drawn (122 anchors / 244 fetch units) and pipeline validated end-to-end on a real subset; full-sample fetch was still in progress (~15%, zero failures) when this pass ended — resumable, see `docs/coj_audit_pilot_2026-07-03.md` §7 for exact resume commands
- [ ] Self-gates computed and passing (known-sign >95%; zero clamp-monotonicity violations above the noise floor, which is itself reported) — gate math implemented + unit-tested; numeric results pending full-sample completion
- [ ] Fetch reliability stats reported (throttling, failures, layer availability) with a politeness/rate plan for cohort scale — partial-run numbers reported (36/36 ok, ~15.1s mean latency) + a concurrency-4-6 recommendation recorded; final numbers pending full-sample completion
- [x] Margin threshold + low-margin routing rule defined and applied
- [ ] Go/no-go recommendation for cohort scale recorded — conditional go recorded pending final gate numbers (`docs/coj_audit_pilot_2026-07-03.md` §10)

## Blocked by

None - can start immediately
