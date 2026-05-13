# GEHI Temporal Provider Replacement Plan

Date: 2026-05-05

## Decision

Use GEHistoricalImagery (GEHI) Google Earth Time Machine (`provider=TM`) as the
primary historical imagery provider for install-date back-dating. Keep the
existing GEID CLI/direct chain as a legacy fallback for one milestone only.

This is a provider swap, not a pipeline rewrite. The existing anchor manifest,
presence time-series, Gemini/manual review, and install-interval inference
contracts remain the stage boundaries.

## Corrections From GEID V1

- Vintage probing is done at `z=19`, not `z=21`. In South Africa smoke probes,
  z=21 often exposes only a tiny recent subset; z=19 is the practical
  install-date sweet spot with useful history and still-readable PV signal.
- Download zoom is a separate decision. Start with z=19; try z=20/z=21 only
  when the same vintage exists at higher GSD.
- Coordinates are converted once at the GEHI wrapper boundary. Anchor CSVs
  store lon/lat fields; GEHI CLI expects `LAT,LONG`.
- Duplicate candidate vintages are deduped by `(anchor_id, version)`, not by
  date. Multiple date labels can map to the same mosaic/version.
- No PostGIS service in the pilot. CSV/Parquet remain the stage contracts; an
  optional local DuckDB schema is enough for joins and audits.

## Phase 0 Smoke Gate

Use the existing `jhb_vexcel10_smoke` regression data, not ad hoc anchors.

Inputs:

- `~/zasolar_data/geid_temporal/jhb_vexcel10_smoke/anchors.csv`
- `~/zasolar_data/geid_temporal/jhb_vexcel10_smoke/presence_timeseries_extended_with_web_20150830.csv`
- `~/zasolar_data/geid_temporal/jhb_vexcel10_smoke/manual_decisions_extended_with_web_20150830.csv`

Minimum smoke:

1. Select web-reviewed anchors with known GEHI/Web labels, currently
   `johannesburg_G0922_a000005` and `johannesburg_G0922_a000010` at
   `2015-08-30`, plus one adjacent reviewed anchor/date from the same smoke
   set when the third web-reviewed row is added.
2. Run `gehi_info.py --zoom 19` for those anchors and confirm the target date
   appears in GEHI metadata.
3. Run `gehi_availability.py --zoom 19 --complete` over the anchor bbox and
   confirm the target dates are complete for the chip.
4. Run `gehi_download.py --zoom 19 --exact-date` for the selected
   `(anchor_id, version)` rows.
5. Compare downloaded chip date/provenance and visual PV label against the
   existing web-reviewed presence rows.

Pass condition:

- The GEHI candidate catalog contains the reviewed date/version.
- Exact-date download creates a non-empty GeoTIFF for that date.
- The same anchor/date remains absent/present under manual or Gemini review.

GDAL CRS/bounds checks are still useful, but they are secondary. The primary
test is consistency against known web-reviewed temporal labels.

## Provider Wrappers

- `scripts/temporal/gehi_info.py`
  - centroid-level vintage catalog
  - parser source for `date/version/path`
  - writes version-deduped `gehi_vintage_candidates.csv`

- `scripts/temporal/gehi_availability.py`
  - bbox-level complete/partial coverage check
  - GEHI v0.5.1 prints dates then enters an interactive chooser; wrapper
    tolerates the known non-zero `Cannot read keys` exit after parsing dates

- `scripts/temporal/gehi_download.py`
  - exact-date GeoTIFF download
  - artifacts default to `~/zasolar_data/geid_temporal/gehi_chips/`
  - manifest records path/hash/status/provenance only

## Data Model

Committed stage files stay small CSV/JSONL fixtures. Runtime outputs live under
`~/zasolar_data/`.

Optional local query layer:

- `schemas/temporal_inventory.duckdb.sql`
- unique candidate constraint is `(anchor_id, version)`
- raster files stay out of the DB; store path/hash/CRS/bounds metadata

## Legacy Sunset

**Completed 2026-05-13.** GEHI is the sole download path. Removed:

- `scripts/temporal/download_geid_historical_direct.py` (GEID direct downloader)
- `scripts/temporal/export_geid_temporal_tasks.py` (GEID CLI task exporter)
- `tests/temporal/test_download_geid_historical_direct.py`

`geid_temporal_common.py` stays as the shared CSV/IO/path utility module for
the temporal pipeline (used by all `gehi_*.py` wrappers); the module name is
legacy but the contents are provider-agnostic.

`~/zasolar_data/geid_raw/` remains on disk as historical mosaic input for
Phase 0 cross-checks against GEHI candidates.

## 18-Anchor Run

After Phase 0 passes:

1. Generate the z=19 GEHI candidate catalog for all 18 anchors.
2. Deduplicate by version and compute per-anchor date spans.
3. Download only staged candidates: earliest baseline, latest/current, annual
   points, then densify around absent-to-present intervals.
4. Run Gemini/manual review into the existing `presence_timeseries.csv`
   schema.
5. Infer intervals with the existing monotonic breakpoint logic.
6. Compare against the GEID/web smoke rows and flag non-monotonic or
   low-quality intervals instead of forcing dates.

