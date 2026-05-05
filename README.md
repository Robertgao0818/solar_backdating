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

## Status (2026-05-05)

V1.4 sub-line pivot. Replaces the now-archived `geid_bbox` GEID
free-detection prototype (see
`/home/gaosh/projects/_archive/geid_bbox_legacy_2026-05-05/`).

Phase-0: anchor-presence scoring with Gemini visual review for QA. Active
modules:
- `scripts/temporal/` — anchor manifest, GEHI/legacy GEID downloader wrappers, presence scorer, install-date inference
- `scripts/validation/` — legacy GEID vintage probe, Gemini single-image review

Current provider decision: GEHistoricalImagery Time Machine is the primary
candidate for historical imagery. Vintage discovery uses `z=19`; higher zooms
are optional download upgrades only when that exact vintage exists. See
[`docs/gehi_temporal_replacement_plan.md`](docs/gehi_temporal_replacement_plan.md).

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
├── tests/temporal/            # Pytest fixtures + smoke tests
├── docs/                      # Architecture, plans
└── data/examples/             # Small committed schema fixtures
```
