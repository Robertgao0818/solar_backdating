# ISSUE-07: Content-addressed verdict store + replay gate + churn monitor

Status: ready-for-agent
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

- [ ] Replay gate: a completed cohort slice re-run through the store produces byte-identical scan states
- [ ] Cache-miss accounting: second identical run issues zero scorer calls (measured)
- [ ] Churn monitor detects a mutated sentinel chip (unit test) and reports churn rate
- [ ] Concurrent access safe under the production parallelism pattern, or a single-writer constraint documented and enforced
- [ ] Store size/compaction characteristics documented (record-per-frame at cohort scale)

## Blocked by

- ISSUE-06 (provenance fields are the key components)
