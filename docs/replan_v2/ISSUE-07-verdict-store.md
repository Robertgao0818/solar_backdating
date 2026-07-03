# ISSUE-07: Content-addressed verdict store + replay gate + churn monitor

Status: done
Phase: 1 — Provenance
Blocked by: ISSUE-06

## Parent

[`../install_date_optimization_v2_prd.md`](../install_date_optimization_v2_prd.md) — D6. User stories 10, 11.

## What to build

A content-addressed KV store: key = (chip content hash × scorer identity ×
prompt hash × scoring mode) → observation record. Every call through the
PresenceScorer seam consults the store first; re-runs pay only never-seen
keys. Keying by **pixel hash** (not capture-date/version metadata) is the
correctness condition — the imagery provider re-renders pixels under stable
metadata.

A cached verdict can never drift: this delivers the program's version-drift
kill before any student model exists, and makes multi-rep protocols nearly
free after rep 1.

Churn monitor: track hash-churn rate on a fixed sentinel chip set; alert on
spikes; churned frames are flagged as an escalation class (re-scored
deliberately, not silently).

## Acceptance criteria

- [x] Replay gate: a completed cohort slice re-run through the store produces byte-identical scan states
- [x] Cache-miss accounting: second identical run issues zero scorer calls (measured)
- [x] Churn monitor detects a mutated sentinel chip (unit test) and reports churn rate
- [x] Concurrent access safe under the production parallelism pattern, or a single-writer constraint documented and enforced
- [x] Store size/compaction characteristics documented (record-per-frame at cohort scale)

## Landed (2026-07-03)

`scripts/temporal/verdict_store.py` + `tests/temporal/test_verdict_store.py`;
design/ops doc: [`../verdict_store.md`](../verdict_store.md). Key =
(chip pixel hash × scorer identity × prompt hash × mode × call-time
instruction extras); per-chip records for batch/`score()` (one shared shape),
window-level for sequence/matrix; failures never cached; `raw_response` not
memoized. Wired default-ON into `run_adaptive_scan` / `run_census2023_scan` /
`score_target_sequence` / `score_chip_group_matrix` (`--no-verdict-store` to
disable), OPT-IN in `fullstack_noscan_run` (a store would collapse the
rep-to-rep variance that harness measures). Wrap order: provenance sidecar
outermost, verdict cache inner — cache hits still emit ISSUE-06 rows.
Thread-safe in-process; cross-process single-writer enforced via `fcntl` lock.
CLI: `stats / compact / sentinel-init / sentinel-check / replay-diff`.

## Blocked by

- ISSUE-06 (provenance fields are the key components)
