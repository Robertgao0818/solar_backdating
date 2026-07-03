# solar_backdating

Location-conditioned PV install-date inference, complementing the
[ZAsolar](https://github.com/Robertgao0818/ZAsolar) high-resolution aerial
census pipeline.

**Task.** Given a known PV installation footprint (seed = ZAsolar `v4_high`
inventory or any GPKG of installation polygons), estimate the year the
installation appeared by scanning historical satellite/imagery vintages
(GEID, GEHistoricalImagery, future tile-history sources) at the seed location.

**Not** a free-standing detector. The seed locations come from the upstream
census; this repo's job starts at "given anchor (lon, lat, polygon), when did
it light up?"

## Status (2026-07-03)

V1.4 sub-line. Replaces the now-archived `geid_bbox` GEID free-detection
prototype (see `/home/gaosh/projects/_archive/geid_bbox_legacy_2026-05-05/`).

Milestones:
- **2026-06-04** — full JHB back-dating run complete: 92.7% of the FP-cut
  inventory point-dated (95.1% labeled incl. lower bounds), delivered to the
  economic layer.
- **2026-06-23/30** — end-to-end reproducibility study: adaptive-search path
  variance identified as the dominant instability source.
- **2026-07-03** — **install-date optimization v2** program adopted: five
  layered phases (P0 deterministic decoder → P1 provenance/verdict store →
  P2 external accuracy channel → P3 student distillation → P4 fixed-grid
  pipeline shape). **Plan-of-record:
  [`docs/install_date_optimization_v2_prd.md`](docs/install_date_optimization_v2_prd.md),
  execution: [`docs/replan_v2/TRACKER.md`](docs/replan_v2/TRACKER.md).**

Active modules:
- `scripts/temporal/` — anchor manifest, GEHI downloader wrapper, presence scorer, adaptive scan, install-date inference
- `scripts/validation/` — vintage probe, Gemini single-image review
- `src/solar_backdating/` — library code + estimator seam (replan_v2 P0)

Imagery provider: GEHistoricalImagery Time Machine is the **sole** download
provider (since 2026-05-13). Vintage discovery uses bbox-complete
availability at `z=19`, with `z=18` as the lower-zoom whole-picture fallback;
higher zooms are optional download upgrades only when that exact vintage has
complete chip coverage. See
[`docs/gehi_temporal_replacement_plan.md`](docs/gehi_temporal_replacement_plan.md).
Gemini review calls are bounded: default date batches are at most 5 images, and
multi-target matrix review is capped at 4 targets / 24 date-target cells before
splitting.

**Scorer backbone (v2 program Phase 3).** A frozen, self-hosted DINOv3-L-SAT
distilled from Gemini's own per-vintage labels replaces the hosted Gemini
presence scorer — motivated by reproducibility / no version-drift, gated on
*fidelity to Gemini* (no independent install-date truth exists, so no accuracy
claim). See
[`docs/dinov3_sat_scorer_backbone_prd.md`](docs/dinov3_sat_scorer_backbone_prd.md)
(inputs amended by the v2 PRD §D12).

Chip-group matrix review entrypoint:

```bash
python scripts/temporal/score_chip_group_matrix.py \
  --chip-targets-csv ~/zasolar_data/geid_temporal/jhb_full382_unified_A_merge01_c0925_chipgroups/chip_targets.csv \
  --image-artifacts-csv ~/zasolar_data/geid_temporal/gehi_image_artifacts.csv \
  --output ~/zasolar_data/geid_temporal/chip_group_presence_timeseries.csv
```

Single-target sequence review entrypoint:

```bash
python scripts/temporal/score_target_sequence.py \
  --chip-targets-csv ~/zasolar_data/geid_temporal/jhb_full382_unified_A_merge01_c0925_chipgroups/chip_targets.csv \
  --image-artifacts-csv ~/zasolar_data/geid_temporal/gehi_image_artifacts.csv \
  --dates 2018-03-30,2019-07-30,2021-08-30,2022-03-30,2024-02-29 \
  --output ~/zasolar_data/geid_temporal/target_sequence_presence.csv \
  --long-output ~/zasolar_data/geid_temporal/target_sequence_presence_long.csv \
  --workers 2 \
  --qps 0.3 \
  --resume
```

## Plugin model

This repo is a local plugin of the main ZAsolar repo. It does not have its
own virtualenv. It does not pip-install a copy of `core/`. At runtime it
shares ZAsolar's `.venv` and resolves shared modules (`core.region_registry`,
`core.annotation_loader`, `core.grid_utils`) via `PYTHONPATH`.

```bash
# From this repo's root
source scripts/activate_env.sh        # shares ZAsolar's .venv + PYTHONPATH
python scripts/temporal/score_anchor_presence.py --help
```

See [`SHARED_FROM_ZASOLAR.md`](SHARED_FROM_ZASOLAR.md) for the dependency
contract.

## Data

Large data lives outside the git tree, in `~/zasolar_data/`:
- `~/zasolar_data/geid_raw/` — GEID raw mosaics
- `~/zasolar_data/geid_temporal/` — anchor stacks, QA HTML, presence outputs
- `~/zasolar_data/geid_vintage_probe/` — per-region vintage probe results

Run `scripts/link_data_dirs.sh` once after cloning to create the in-repo
symlinks. The repo's `data/examples/` holds small fixtures only (committed).

## Layout

```
solar_backdating/
├── scripts/
│   ├── activate_env.sh        # Shared-venv plugin activator
│   ├── link_data_dirs.sh      # Bind data/ symlinks to ~/zasolar_data/
│   ├── temporal/              # Anchor manifest, downloader, scorer, inferer
│   └── validation/            # Vintage probe, Gemini review
├── src/solar_backdating/      # Library code (importable as solar_backdating)
├── configs/                   # YAML configs (anchor-presence, etc.)
├── schemas/                   # Stage-contract schemas (temporal_inventory.duckdb.sql)
├── tests/
│   ├── temporal/              # Pytest fixtures + smoke tests
│   └── estimator/             # Estimator seam / PAVA / parity tests (replan_v2 P0)
├── docs/                      # PRDs + plans; trackers in replan_v2/ and dinov3_scorer/
├── results/                   # Small analysis artifacts (timenode heatmaps)
├── data/examples/             # Small committed schema fixtures
└── pyproject.toml             # setuptools build config
```
