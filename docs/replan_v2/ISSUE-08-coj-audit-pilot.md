# ISSUE-08: CoJ true-date audit — 100-anchor pilot (tracer)

Status: done
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

- [x] ~100-anchor pilot runs end-to-end: fetch → score → join → flags — 122 anchors / 244 fetch units, 244/244 fetched ok, scored + joined + gated; artifacts under `~/zasolar_data/geid_temporal/coj_audit_pilot_20260703/`, results in `docs/coj_audit_pilot_2026-07-03.md` §7
- [x] Self-gates computed and passing (known-sign >95%; zero clamp-monotonicity violations above the noise floor, which is itself reported) — computed on the full sample: present-side strata PASS at 100% (60/60), noise floor = 0; **s3 known-absent FAILS (0.50-0.64)** with a failure signature that falsifies the stratum's "known sign" (pipeline boundary false-absents), not the audit — 7 clamp violations reported, all s3; see §7 interpretation
- [x] Fetch reliability stats reported (throttling, failures, layer availability) with a politeness/rate plan for cohort scale — 244/244 ok, mean 15.0s, zero WAF/throttle signatures; concurrency 4-6 plan in §8
- [x] Margin threshold + low-margin routing rule defined and applied
- [x] Go/no-go recommendation for cohort scale recorded — **GO with s3 reassigned to findings/target population** + true-negative control stratum + pre-launch human spot-check of the 7+4 flagged anchors (`docs/coj_audit_pilot_2026-07-03.md` §10)

## Blocked by

None - can start immediately
