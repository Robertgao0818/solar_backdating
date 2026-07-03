# ISSUE-18: Resolution provenance + cache escape hatch

Status: done
Phase: 1 — Provenance
Blocked by: none (sidecar field shape coordinates with slice 6)

## Parent

[`../install_date_optimization_v2_prd.md`](../install_date_optimization_v2_prd.md) — D17. User story 37.
Evidence: 2026-07-03 chip-geometry & resolution audit (amendment block in the PRD).

## What to build

Today the recorded zoom reflects only which ladder rung's download call
succeeded: the imagery binary silently substitutes coarser tiles inside a
"successful" chip, the skip-existing cache pins every anchor/date to the
first-cached zoom forever (neither production orchestrator can force a
re-fetch, so a ladder upgrade has zero effect on cached anchors), and the
achieved-zoom mix (72.5% z20 in the JHB census; 97% z19 in the Wayback
corpus) is only recoverable by parsing raw scan-state JSON.

Deliver end-to-end resolution accountability: every downloaded chip records
requested ladder, achieved rung, measured raster extent + GSD, and content
hash (fields shaped to join the provenance sidecar from slice 6); the
download path gains an explicit overwrite / minimum-zoom re-fetch escape
hatch; every scan summary reports the achieved-zoom distribution; and a
sentinel-set effective-resolution estimate flags chips whose delivered
pixels are coarser than their nominal rung (silent per-tile substitution).
The estimator's zoom-stratified emission matrices (D2) key on achieved zoom,
not requested.

## Acceptance criteria

- [ ] Per-chip provenance record carries requested ladder, achieved rung, measured extent, GSD, and content hash
- [ ] Escape hatch demonstrably re-fetches an anchor/date previously pinned at a lower zoom (staged-cache test)
- [ ] Scan summary includes the achieved-zoom histogram; verified by re-summarizing an existing production scan-state corpus
- [ ] Sentinel effective-resolution estimator separates known z18/z19/z20 chips before it flags anything (self-gate)
- [ ] Zoom stratification for the estimator (D2) documented to key on achieved zoom, not requested

## Blocked by

None - can start immediately (field names coordinate with slice 6's provenance sidecar)
