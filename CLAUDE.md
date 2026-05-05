# CLAUDE.md — solar_backdating

Location-conditioned PV install-date inference. Plugin of ZAsolar main repo.

**Identity.** This is the V1.4 install-date sub-line. Given a known PV
installation polygon, estimate when it appeared by scanning historical
satellite vintages (GEID / GEHistoricalImagery / etc.) at that location.

**Not** a free-detection repo. The legacy `geid_bbox` GEID free-detection
prototype lives at `/home/gaosh/projects/_archive/geid_bbox_legacy_2026-05-05/`
(cold archive, not git-initialized).

## Plugin contract

- Shares ZAsolar's `.venv`. No own venv.
- Imports `core.*` and `configs/datasets/regions.yaml` from main repo via
  PYTHONPATH (set by `scripts/activate_env.sh`).
- Subrepo `scripts.temporal.*` shadows any older copy in main repo via
  PYTHONPATH ordering.

## Quick start

```bash
source scripts/activate_env.sh           # shared venv + PYTHONPATH
./scripts/link_data_dirs.sh              # one-time symlink to ~/zasolar_data/
pytest tests/temporal/                   # smoke gate
python scripts/temporal/score_anchor_presence.py --help
```

## Working rules

1. **Plugin boundary.** Only import from `core.*` and `configs/datasets/*`
   in main repo. No cross-imports from `scripts.training.*`, etc.
2. **Data discipline.** Only `data/examples/*.example.csv` is committed.
   All real data products go to `~/zasolar_data/geid_temporal/` and
   `~/zasolar_data/geid_vintage_probe/`.
3. **Secrets.** `.env.gemini.local` and any API key file is gitignored —
   never commit.
4. **Deprecation window.** Until 2026-05-31, identical script copies
   exist in `ZAsolar/scripts/temporal/`. Fix bugs **here first**; main
   repo's copies are frozen.
5. **Sub-task only.** This repo is downstream of ZAsolar's census output.
   Do not redefine V1.4 task semantics here; refer to
   `ZAsolar/docs/validation_strategy.md`.

## Key references

- Plugin runtime contract: [`SHARED_FROM_ZASOLAR.md`](SHARED_FROM_ZASOLAR.md)
- Phase-0 architecture: [`docs/geid_temporal_anchor_presence_architecture.md`](docs/geid_temporal_anchor_presence_architecture.md)
- Main project rules: `/home/gaosh/projects/ZAsolar/CLAUDE.md`
- V1.4 validation framework: `/home/gaosh/projects/ZAsolar/docs/validation_strategy.md`
- Region registry: `/home/gaosh/projects/ZAsolar/configs/datasets/regions.yaml`

## Environment

- Same Python venv as main repo (`ZAsolar/.venv`)
- CUDA GPU only required if running full presence scoring on long stacks;
  Phase-0 anchor-presence scoring is mostly CPU + I/O bound
- `ZASOLAR_ROOT` env var overrides default path to main repo
  (defaults to `/home/gaosh/projects/ZAsolar`)
