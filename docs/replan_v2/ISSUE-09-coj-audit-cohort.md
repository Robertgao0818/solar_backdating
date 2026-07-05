# ISSUE-09: CoJ audit at cohort scale + contradiction dataset

Status: done — full 16,166-unit cohort run complete 2026-07-05 11:52 NZST
Phase: 2 — Accuracy channel
Blocked by: ISSUE-08

## Execution status (2026-07-04)

- Implementation committed (`9f53d15` + review fixes `440e3a4`): cohort
  builder (5 strata, 12,190 dated anchors + 300 negative controls, 16,166
  fetch units = -36% vs all-layers), concurrent fetch (5 workers, pilot
  politeness per worker), resumable scoring, cohort join/gates/report,
  orchestrator CLI `scripts/audit/coj_audit_cohort.py` + tmux launcher.
  Schema: [`ISSUE-09-cohort-schema.md`](ISSUE-09-cohort-schema.md).
- Smoke run (24 anchors + 2 NC, 55 live units incl. first live 2015-layer
  fetches) passed end-to-end; full run launched in tmux session
  `coj_cohort` → `~/zasolar_data/geid_temporal/coj_audit_cohort_20260704/`
  (fetch ETA ~14 h, then score/join/gates/report chain automatically).
- Go-condition 2 spot-check (`spotcheck_20260704/` under the pilot dir):
  all 7 gate-(c) anchors visually CONFIRMED as real PV@2023 (census
  boundary false-absents are real). CAVEAT: all 4 high-margin
  present@2019 s3 bits are audit false-positives (tile texture / roof
  clutter / bright metal) — 2019-layer present bits carry elevated FP
  risk; treat 2019-based contradictions as human-queue candidates
  (ISSUE-10/11 adjudication), not headline, and read gate_nc@2019
  closely when the run lands.

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

## Completion note (2026-07-05)

Full 16,166-unit cohort run complete 2026-07-05 11:52 NZST, resumed
idempotently after a Windows reboot killed the chain mid-score at 02:26.
Coverage 12,190/12,190 dated anchors = 100%, zero fetch failures; gate_nc
PASS (1/300 false-present). gate_a fails in exactly one cell
(`c_cal_present_pre2019@2015`, 49/55 = 0.891 vs bar 0.95) — all six
disagreements human-adjudicated 2026-07-05, splitting 3/3 between
backdating_early (incl. a heater_swap failure mode) and audit_miss_2015;
see `gate_a_2015_human_adjudication.{csv,md}`. The 8,407-bit
`human_queue.csv` is ready for ISSUE-10. Artifacts:
`~/zasolar_data/geid_temporal/coj_audit_cohort_20260704/`.
