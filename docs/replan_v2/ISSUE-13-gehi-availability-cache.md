# ISSUE-13: GEHI availability-catalog cache

Status: done
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

- [x] Catalog hit avoids the live call (verified by call accounting on a sample cohort) —
      `scripts/temporal/gehi_catalog_cache.py` (`CatalogCache`, `CacheStats`,
      `get_or_fetch`); 5-anchor real-GEHI evidence run
      (`~/zasolar_data/geid_temporal/catalog_cache_demo_20260703/`): pass1
      `hits=0 misses=5 live_calls=5`, pass2 `hits=5 misses=0 live_calls=0`.
- [x] Prep wall-clock improvement demonstrated vs the live-catalog baseline on
      the same sample — pass1 (live) `real 0m3.575s` vs pass2 (cached)
      `real 0m0.060s` for `gehi_availability.py`; `gehi_info.py` pass1
      `real 0m2.027s` vs pass2 `real 0m0.053s` (same demo dir).
- [x] Stale-cache refresh policy documented and configurable; forced-refresh
      path tested — `docs/gehi_catalog_cache.md` "Refresh policy"; unit tests
      `test_get_or_fetch_stale_served_on_live_failure`,
      `test_get_or_fetch_stale_served_on_runtime_error`,
      `test_get_or_fetch_force_refresh_bypasses_fresh_entry`; real pass3
      (`--force-catalog-refresh`) shows `live_calls=5` again against a warm cache.
- [x] Staged-and-delete chip lifecycle documented; content hashes retained
      after deletion — `scripts/temporal/chip_lifecycle.py` `release` command;
      `docs/gehi_catalog_cache.md` "Chip lifecycle"; `tests/temporal/test_chip_lifecycle.py`.
- [x] Timeout/failure of a live refresh degrades gracefully (uses last-good
      cache with a warning, does not hang the run) —
      `test_get_or_fetch_stale_served_on_live_failure` /
      `test_catalog_cache_serves_stale_on_live_failure` (both modules);
      no-stale-entry path preserves the pre-ISSUE-13
      warn-and-continue/`RuntimeError`-propagates contract exactly
      (`test_get_or_fetch_no_stale_entry_reraises`,
      `test_get_or_fetch_no_stale_entry_timeout_reraises`).

Implementation: `scripts/temporal/gehi_catalog_cache.py` (new),
`scripts/temporal/chip_lifecycle.py` (new), `scripts/temporal/gehi_availability.py`
+ `scripts/temporal/gehi_info.py` + `scripts/temporal/run_adaptive_scan.py` (cache
wiring, `catalog_cache=None` default = byte-identical legacy behavior),
`docs/gehi_catalog_cache.md` (design doc). Deferred: a YAML knob in
`configs/geid_anchor_presence.yaml`/`scan_config.py` — both were dirty with
unrelated in-flight work, so this slice ships CLI flags + one env var only
(`SOLAR_GEHI_CATALOG_CACHE_DIR`); see `docs/gehi_catalog_cache.md` "Deferred:
YAML config".

## Blocked by

None - can start immediately
