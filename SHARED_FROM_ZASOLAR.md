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
| `core/region_registry.py` | `core.region_registry` | `scripts/temporal/build_gt_anchor_manifest.py` |
| `core/annotation_loader.py` | `core.annotation_loader` (`AnnotationEntry`, `discover_annotations`, `load_annotation_gdf`) | `scripts/temporal/build_gt_anchor_manifest.py` |
| `core/grid_utils.py` | `core.grid_utils` (transitively, via region_registry / future scripts) | (anticipated) |
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
  training artifacts are not relevant here

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
