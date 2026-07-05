# Shared dependencies on ZAsolar main repo

`solar_backdating` runs as a local plugin of the
[ZAsolar](https://github.com/Robertgao0818/ZAsolar) main repo. It shares
the main repo's virtualenv and imports a small surface of main-repo modules
at runtime via `PYTHONPATH`. This document is the contract.

## Runtime resolution order

`scripts/activate_env.sh` enforces this `PYTHONPATH` order:

1. `$SOLAR_BACKDATING_ROOT`
2. `$SOLAR_BACKDATING_ROOT/src`
3. `$ZASOLAR_ROOT` (default: `/home/gaosh/projects/ZAsolar`)

So in-repo `from scripts.temporal.geid_temporal_common import ...` resolves
to **this** repo's copy, while `from core.region_registry import ...` falls
through to main repo.

## Imported main-repo modules

| Main-repo path | Imported as | Used by |
| --- | --- | --- |
| `core/__init__.py` | `core` | (namespace) |
| `core/region_registry.py` | `core.region_registry` | `scripts/temporal/build_gt_anchor_manifest.py`, `scripts/temporal/run_adaptive_scan.py`, `scripts/audit/coj_audit_cohort.py` (`get_task_grid_path`; verified 2026-07-05) |
| `core/annotation_loader.py` | `core.annotation_loader` (`AnnotationEntry`, `discover_annotations`, `load_annotation_gdf`) | `scripts/temporal/build_gt_anchor_manifest.py` |
| `core/grid_utils.py` | `core.grid_utils` (`get_metric_crs`) | `scripts/audit/coj_cohort_build.py` (CoJ cohort negative-control grid, ISSUE-09; verified 2026-07-05) |
| `core/models/maskrcnn.py` | `core.models.maskrcnn` (`build_solar_maskrcnn`) | `scripts/audit/score_coj_chips.py` (CoJ audit detector scoring, ISSUE-08/09; verified 2026-07-05) |
| `core/inference/tile_dataset.py` | `core.inference.tile_dataset` (`SlidingWindowDataset`, `list_collate`) | `scripts/audit/score_coj_chips.py` (CoJ audit detector scoring, ISSUE-08/09; verified 2026-07-05) |
| `configs/datasets/regions.yaml` | read via `core.region_registry` | all scripts that resolve region/imagery layer paths |

## Configuration files read from main repo

- `configs/datasets/regions.yaml` — single source of truth for regions,
  imagery layers, model runs, annotation paths
- `configs/datasets/training_sets.yaml` — read indirectly if/when seed
  inventory provenance is needed

## What this repo does NOT import from main repo

- Anything under `scripts/training/`, `scripts/annotation/`,
  `scripts/analysis/` — too domain-specific, should stay in main repo
- Anything under `data/annotations/` — main repo's annotation data is
  read via `core.annotation_loader` only
- Anything under `checkpoints/`, `data/coco*/`, `data/cls_*/` — main repo's
  training artifacts are not relevant here. **One declared exception**
  (pilot caveat 3, 2026-07-05): `scripts/audit/score_coj_chips.py` reads
  `checkpoints/exp_unified_reviewall_A/best_model.pth` directly as its
  default detector checkpoint (a file-path read, not an import)

## Sync protocol

When main repo changes any module in the table above:

1. Run this repo's smoke gate locally:
   ```bash
   source scripts/activate_env.sh
   pytest tests/temporal/
   ```
2. If any test breaks, the main-repo change is breaking for `solar_backdating`.
   Either revert the main-repo change, or open a coordinated update PR
   (one in main repo, one here).
3. If smoke passes, no action needed in this repo.

This contract is intentionally one-directional: main repo does not import
from `solar_backdating`. The seed inventory flows main → here as a file
artifact (GPKG/CSV), not as a Python import.

## Override paths

- `ZASOLAR_ROOT` env var — set if main repo lives outside
  `/home/gaosh/projects/ZAsolar` (e.g., on RunPod the canonical path is
  `/workspace/ZAsolar/`).
- `SOLAR_BACKDATING_ROOT` env var — auto-derived by `activate_env.sh` from
  `BASH_SOURCE`; override only if invoking outside the script.
