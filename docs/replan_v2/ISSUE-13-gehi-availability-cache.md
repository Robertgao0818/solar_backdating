# ISSUE-13: GEHI availability-catalog cache

Status: ready-for-agent
Phase: 4 — Pipeline shape
Blocked by: none

## Parent

[`../install_date_optimization_v2_prd.md`](../install_date_optimization_v2_prd.md) — D14. User story 33.

## What to build

The fact-check found the real production wall-clock bottleneck was **not**
Gemini quota but GEHI availability-catalog calls timing out at 300 s each.
Any full-stack cohort run (Phase 4) multiplies catalog pressure, so this
lands first: a per-region/per-tile **availability-catalog cache** with a
scheduled refresh policy; scan-prep and chip re-download consult the cache
instead of issuing live catalog calls.

Chip lifecycle discipline ships alongside: full-stack renders are
staged-and-deleted with **content hashes retained** (so the verdict store's
keys survive chip deletion), respecting the documented disk-hygiene
constraints.

## Acceptance criteria

- [ ] Catalog hit avoids the live call (verified by call accounting on a sample cohort)
- [ ] Prep wall-clock improvement demonstrated vs the live-catalog baseline on the same sample
- [ ] Stale-cache refresh policy documented and configurable; forced-refresh path tested
- [ ] Staged-and-delete chip lifecycle documented; content hashes retained after deletion
- [ ] Timeout/failure of a live refresh degrades gracefully (uses last-good cache with a warning, does not hang the run)

## Blocked by

None - can start immediately
