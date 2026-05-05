# AGENTS.md — solar_backdating

This repo is the V1.4 install-date sub-line of the ZAsolar project. **Not**
a free-standing solar-panel detector; not the `geid_bbox` prototype (that
repo is archived under
`/home/gaosh/projects/_archive/geid_bbox_legacy_2026-05-05/`).

## What this repo does

- Input: PV installation seeds (polygon + lon/lat) from the upstream
  ZAsolar census (`v4_high` inventory or any equivalent GPKG)
- Process: pull historical satellite vintages at each seed, score
  presence/absence per vintage, infer install year
- Output: per-anchor install-date estimates with confidence + provenance

## What this repo does NOT do

- Run a panel detector on free imagery (that's `ZAsolar/detect_and_evaluate.py`)
- Train detection models (that lives in main repo)
- Annotate ground truth (lives in `ZAsolar/data/annotations/`)
- Bbox-based GEID free detection — superseded by V1.4 pivot, see archived
  `geid_bbox_legacy_2026-05-05/`

## Plugin runtime contract

This repo expects the ZAsolar main repo to live at
`/home/gaosh/projects/ZAsolar/` (override with `ZASOLAR_ROOT` env var). It
does not have its own venv; it shares main repo's `.venv` and reads
`core.region_registry`, `core.annotation_loader`, `core.grid_utils`,
`configs/datasets/regions.yaml` via `PYTHONPATH`. See
[`SHARED_FROM_ZASOLAR.md`](SHARED_FROM_ZASOLAR.md) for the full dependency
list and the sync protocol when those modules change in the main repo.

PYTHONPATH order is enforced by `scripts/activate_env.sh`:

1. `$SOLAR_BACKDATING_ROOT` — for in-repo `from scripts.temporal.geid_temporal_common import ...`
2. `$SOLAR_BACKDATING_ROOT/src` — for `import solar_backdating`
3. `$ZASOLAR_ROOT` — for `from core import ...` (must come last so subrepo's `scripts.*` shadows main repo's older copy)

## Working constraints

1. Do not import from `scripts.training.*`, `scripts.annotation.*`, or
   any main-repo namespace except `core.*` and `configs.datasets.*`. The
   coupling boundary is small on purpose.
2. Do not write to `~/zasolar_data/` paths shared with main repo
   (`tiles/`, `coco/`, `models/`, `annotations/`) — read-only.
   This repo's outputs go to `~/zasolar_data/geid_temporal/` and
   `~/zasolar_data/geid_vintage_probe/`.
3. New probe / temporal data products go to `~/zasolar_data/`, not into
   git. Only schema fixtures (`data/examples/*.example.csv`) are committed.
4. `.env.gemini.local` and any API keys: never commit.
5. GEHistoricalImagery (`GEHI`) is the primary candidate provider for new
   historical imagery work. Probe vintages at z=19. Treat z=20/z=21 as
   optional download upgrades only after confirming the same vintage exists.
   GEHI CLI coordinates are `LAT,LONG`; anchor manifests remain lon/lat and
   wrappers own the conversion.
6. Keep CSV/JSONL/Parquet as stage contracts. DuckDB is acceptable as a local
   query layer; do not introduce a PostGIS service during the pilot.
7. Deduplicate GEHI vintage candidates by `(anchor_id, version)`. Do not use
   `(anchor_id, capture_date)` as the uniqueness key.

## Coordination with main repo

- During the deprecation window (until 2026-05-31), copies of these scripts
  also exist in `ZAsolar/scripts/temporal/` and `ZAsolar/scripts/validation/`
  with deprecation headers. Bug fixes go here first; main repo's copies are
  frozen.
- After 2026-05-31, main repo deletes the temporal copies and ROADMAP marks
  the pivot fully landed.

## Memory & cross-review

This repo uses the main project's auto-memory at
`~/.claude/projects/-home-gaosh-projects-ZAsolar/memory/`. Don't spawn a
separate memory tree.
