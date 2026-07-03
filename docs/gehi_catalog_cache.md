# GEHI catalog cache + chip lifecycle (ISSUE-13)

Status: implemented. Parent: `docs/install_date_optimization_v2_prd.md` D14,
user story 33. Tracker: `docs/replan_v2/ISSUE-13-gehi-availability-cache.md`.

## Problem

Production wall-clock on full-stack cohort runs is dominated by live GEHI
`availability`/`info` catalog subprocess calls, each of which can hang up to
the 300 s timeout. Scan-prep, chip re-download, and diagnostic re-checks
routinely re-issue the *same* catalog query (same anchor bbox/location, same
zoom, same date range) across resumed runs and repeated zoom-ladder lookups.
None of that repeated work needs to touch the network/GEHI process again.

## Design

### Two call sites, one cache

`scripts/temporal/gehi_availability.py::fetch_availability_for_anchor` and
`scripts/temporal/gehi_info.py::fetch_vintages_for_anchor` are the only two
places that shell out to `GEHistoricalImagery`. Both gained an optional
`catalog_cache: CatalogCache | None = None` kwarg (plus `force_refresh` and
`max_age_days`). `run_adaptive_scan.py`'s two catalog call paths
(`make_vintage_check`'s lazy per-zoom lookup, and
`_fetch_real_vintage_catalog`'s discovery-ladder loop) thread the same shared
cache instance into both functions, so caching one module (`gehi_catalog_cache.py`)
covers every production catalog call site.

**Scope decision (explicit, per the parent issue): `gehi_info` catalog calls
are cached too**, not just `gehi_availability`. Leaving `info` uncached would
still cost one live subprocess per anchor per discovery zoom during
scan-prep — exactly the bottleneck this issue exists to remove. Both call
sites share the same `CatalogCache`/`CacheStats`/`get_or_fetch` machinery in
`scripts/temporal/gehi_catalog_cache.py`; `kind="availability"` vs
`kind="info"` partitions the two query shapes inside one sqlite table.

### Cache correctness key

A cache entry is addressed by `CatalogCacheKey`:

```
(kind, provider, zoom, lower_left, upper_right, min_date, max_date, complete)
```

- `kind` — `"availability"` (bbox query) or `"info"` (location query).
- `provider` — `"TM"` or `"Wayback"`.
- `zoom` — the GEHI zoom level probed.
- `lower_left` / `upper_right` — for `availability`, the exact `lat,lon`
  strings passed to GEHI's `--lower-left`/`--upper-right`. For `info` (which
  has no bbox), `lower_left` instead carries the `lat,lon` location string
  passed to `--location`, and `upper_right` is left `""`.
- `min_date` / `max_date` — the exact GEHI-format (`YYYY/MM/DD`) date-range
  strings passed to `availability`. Left `""` for `info` (no date-range
  concept). **A narrower date-range query is never served from a wider-range
  entry** — the key is an exact match on the range, not a containment check;
  this is deliberately the simplest correct behavior (see `docs/replan_v2/
  ISSUE-13-gehi-availability-cache.md`'s "subset check" escape hatch, which
  this implementation does not need).
- `complete` — GEHI's `--complete` flag. `True`/`False` are different
  queries, not different freshnesses of the same query.

`anchor_id` / `region_key` / `grid_id` are **not** part of the key — they are
bucketing metadata recorded on each entry for observability only. Two
different anchors that happen to issue byte-identical GEHI commands
(identical bbox/location, zoom, provider, date range) legitimately share one
cache entry. Because `gehi_info`'s per-row dedupe (`dedupe_info_rows_by_date`)
bakes `anchor_id` into its grouping key, `gehi_info.py` caches the *raw*
(un-stamped) `parse_info_output` rows and re-stamps
`anchor_id`/`region_key`/`grid_id`/`info_stdout_sha256`/`gehi_command` fresh
on every serve (hit, live, or stale) before deduping — so a cache hit always
reflects the anchor that's asking, not whichever anchor first populated the
entry. `gehi_availability.py`'s payload (a plain list of capture-date
strings) has no such per-anchor baking, so its rows are always constructed
fresh from `(payload, current anchor context)` regardless of cache source.

### Storage

`CatalogCache` (`scripts/temporal/gehi_catalog_cache.py`) is a stdlib
`sqlite3` table in WAL journal mode, guarded by an internal `threading.Lock`
so one instance can be shared safely across `run_adaptive_scan.py`'s
`ThreadPoolExecutor` of anchor workers. Each row stores the key columns plus
`payload_json` (the JSON-serialized dates list or info rows),
`fetched_at` (ISO-8601 UTC), `stdout_sha256`, `gehi_command`, and the
anchor/region/grid bucketing metadata.

Default location: `~/zasolar_data/geid_temporal/catalog_cache/gehi_catalog_cache.sqlite3`,
overridable via the `SOLAR_GEHI_CATALOG_CACHE_DIR` env var or `--catalog-cache-dir`.
There is **no YAML knob** in this slice — see "Deferred: YAML config" below.

### Refresh policy

- **Fresh hit** (`age_days <= max_age_days`, default 30d): no live call at
  all. `CacheStats.hits` increments.
- **Miss** (no entry, or entry older than `max_age_days`): a live call is
  attempted. `CacheStats.misses` and `CacheStats.live_calls` increment.
  - **Live call succeeds**: the cache is updated (upsert) and the fresh
    result is returned.
  - **Live call fails** (timeout, GEHI non-zero exit via `assert_gehi_success`
    raising `RuntimeError`, or any other exception) **and a last-good entry
    exists** (fresh or stale — `get_any`, not `get_fresh`): that stale entry
    is served instead, with a warning to stderr, and `CacheStats.stale_served`
    increments. The run does not hang or abort just because one refresh
    attempt failed.
  - **Live call fails and there is no entry at all**: behavior matches the
    pre-ISSUE-13 contract exactly — `subprocess.TimeoutExpired` and generic
    `Exception` are swallowed (print a warning, return `[]`, keep the batch
    running); a `RuntimeError` from `assert_gehi_success` (GEHI exiting
    non-zero for a reason other than the known availability-chooser
    interactive-exit) still propagates. `CacheStats.refresh_failures`
    increments either way.
- **Forced refresh** (`--force-catalog-refresh` / `force_refresh=True`):
  bypasses the fresh-hit short-circuit unconditionally and always issues a
  live call, still updating the cache on success (and still degrading to
  stale on failure, per the rule above).

`CacheStats` (hits / misses / live_calls / stale_served / refresh_failures)
is accumulated across an entire run and its `summary()` one-liner is printed
at the end of `gehi_availability.py`'s / `gehi_info.py`'s `main()` and
`run_adaptive_scan.py`'s `main()`.

### CLI flags

Shared by all three CLIs via `gehi_catalog_cache.add_catalog_cache_cli_args`:

| flag | meaning |
| --- | --- |
| `--no-catalog-cache` | disable the cache; always issue live calls (byte-identical to pre-ISSUE-13 behavior) |
| `--force-catalog-refresh` | bypass fresh hits; always attempt a live call (still degrades to stale on failure) |
| `--catalog-max-age-days` | TTL before an entry is "stale" (default 30) |
| `--catalog-cache-dir` | override the cache directory (default: `$SOLAR_GEHI_CATALOG_CACHE_DIR` or `~/zasolar_data/geid_temporal/catalog_cache/`) |

These are unrelated to GEHI's own `--no-cache` flag, which controls GEHI's
internal raw-*tile* cache, not this catalog cache. Never conflate the two.

Cache defaults to **ON** in `gehi_availability.py`, `gehi_info.py`, and
`run_adaptive_scan.py`. `catalog_cache=None` (the Python-level default when
calling the fetch functions directly, e.g. from tests or one-off scripts) is
byte-identical to the pre-ISSUE-13 code path — no cache object is ever
touched.

### Deferred: YAML config

`configs/geid_anchor_presence.yaml` (`AdaptiveScanConfig` /
`scan_config.py`) is where cache policy would naturally live long-term
(e.g., per-region TTLs). Both files were dirty with unrelated in-flight work
at the time this landed, so this slice deliberately ships **CLI flags + one
env var only**, with zero changes to `scan_config.py`. Follow-up: fold
`--catalog-max-age-days`/`--catalog-cache-dir` into `AdaptiveScanConfig` once
that file is free to edit.

## Chip lifecycle: stage → hash → delete

`scripts/temporal/chip_lifecycle.py` is the repo-tracked release tool this
issue asked for. Content-hash machinery already existed
(`gehi_download.build_chip_provenance` writes `chip_provenance.jsonl`
sidecars keyed on `chip_sha256`, documented in
`docs/resolution_provenance.md`); there was no tracked *deletion* tool before
this, only manual ad hoc cleanup. `chip_lifecycle.py` imports
`sha256_file`/`CHIP_PROVENANCE_FIELDS` from `gehi_download.py` rather than
reimplementing hashing.

```
python scripts/temporal/chip_lifecycle.py release --run-dir <dir> [--dry-run] [--force]
```

Discipline:

1. **Discover** every `*.tif` under `--run-dir`, every `chip_provenance.jsonl`
   sidecar, and every `scan_states/*.json` state file.
2. **Refuse** the whole command (dry-run or real) if any `scan_states/*.json`
   was modified in the last 30 minutes — that's the signature of an actively
   running scan, and this tool must never race it. `--force` overrides.
3. **Backfill before delete**: each chip is matched against its *nearest
   enclosing* `chip_provenance.jsonl` (the sidecar whose parent directory is
   the deepest ancestor of the chip path — this handles multi-run trees like
   a reliability study with one sidecar per repetition). A chip with no
   covering provenance record is hashed and appended as a
   `status="backfilled_by_chip_lifecycle"` record **before** anything is
   deleted.
4. **Deletion manifest**: every chip about to be deleted (backfilled or
   already-provenanced) gets one line in `deletion_manifest.jsonl`, written
   next to the sidecar that governs it: `{path, chip_sha256, size_bytes,
   deleted_at}`.
5. **Delete `.tif` only.** Review PNGs, JSONL sidecars, scan-state JSON, and
   everything else under `--run-dir` are left untouched — PNGs are cheap,
   human-legible audit artifacts; `.tif` chips are the actual disk cost.
6. Order is load-bearing: hash → write backfill record → write manifest line
   → unlink. A chip's hash is durably on disk in two places
   (`chip_provenance.jsonl` and `deletion_manifest.jsonl`) before its bytes
   are removed, so the scoring sidecar's `chip_sha256` join key survives
   deletion exactly as `docs/resolution_provenance.md` requires.

`--dry-run` prints the plan (`ReleasePlan.describe()`: chip count, total MB,
backfill count) and touches nothing on disk.

## Testing

`tests/temporal/test_gehi_catalog_cache.py` — cache correctness (exact-key
isolation on date range / complete flag / kind / zoom / provider), TTL
staleness, `get_or_fetch`'s fresh-hit short-circuit / miss-then-live /
force-refresh / stale-degrade-on-failure (including the `RuntimeError` path)
/ no-stale-entry-reraises, `CacheStats` accounting, and a 2-thread concurrent
put/get smoke test. `tests/temporal/test_gehi_availability.py` and
`tests/temporal/test_gehi_info.py` add cache-integration tests alongside the
pre-existing resilience tests (which are asserted to still pass unmodified —
the `catalog_cache=None` contract). `tests/temporal/test_chip_lifecycle.py`
covers backfill hashing, deletion-manifest correctness, the nearest-sidecar
resolution for nested run trees, the refusal window (including `--force`),
and dry-run no-ops. All tests are offline (fake `runner=`/tmp_path sqlite/
tmp_path chip trees); none invoke the real GEHI binary.
