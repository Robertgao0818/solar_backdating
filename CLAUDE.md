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
- Subrepo root precedes `$ZASOLAR_ROOT` on `PYTHONPATH`. Main repo's
  temporal copies were deleted on 2026-05-13 — this repo is the sole home
  of `scripts.temporal.*`.

## Quick start

```bash
source scripts/activate_env.sh           # shared venv + PYTHONPATH
./scripts/link_data_dirs.sh              # one-time symlink to ~/zasolar_data/
pytest tests/                            # smoke gate (tests/temporal/ + tests/estimator/)
python scripts/temporal/score_anchor_presence.py --help
```

## Working rules

1. **Plugin boundary.** Only import from `core.*` and `configs/datasets/*`
   in main repo. No cross-imports from `scripts.training.*`, etc.
2. **Data discipline.** Only `data/examples/*.example.csv` is committed.
   All real data products go to `~/zasolar_data/geid_temporal/` (primary)
   and `~/zasolar_data/geid_raw/vintage_probe/` (vintage probe outputs).
3. **Secrets.** `.env.gemini.local` and any API key file is gitignored —
   never commit.
4. **Sole home / sole provider.** Main repo deleted its
   `scripts/temporal/` and `scripts/validation/` copies on 2026-05-13; all
   temporal code lives here. GEHistoricalImagery is the **sole** imagery
   download provider — remaining `geid_*` filenames are legacy naming, not
   a live GEID download path.
5. **Sub-task only.** This repo is downstream of ZAsolar's census output.
   Do not redefine V1.4 task semantics here; refer to
   `ZAsolar/docs/validation_strategy.md`.

## Key references

- **Current plan-of-record: [`docs/install_date_optimization_v2_prd.md`](docs/install_date_optimization_v2_prd.md)**
  (v2 五阶段 program, READY 2026-07-03) + execution tracker
  [`docs/replan_v2/TRACKER.md`](docs/replan_v2/TRACKER.md)
- DINOv3 scorer distillation PRD (= v2 program Phase 3, inputs amended by v2
  §D12): [`docs/dinov3_sat_scorer_backbone_prd.md`](docs/dinov3_sat_scorer_backbone_prd.md)
  + [`docs/dinov3_scorer/TRACKER.md`](docs/dinov3_scorer/TRACKER.md)
- GEHI provider plan: [`docs/gehi_temporal_replacement_plan.md`](docs/gehi_temporal_replacement_plan.md)
- Plugin runtime contract: [`SHARED_FROM_ZASOLAR.md`](SHARED_FROM_ZASOLAR.md)
- Phase-0 architecture (historical — superseded 2026-05-13 by the GEHI plan):
  [`docs/geid_temporal_anchor_presence_architecture.md`](docs/geid_temporal_anchor_presence_architecture.md)
- Main project rules: `/home/gaosh/projects/ZAsolar/CLAUDE.md`
- V1.4 validation framework: `/home/gaosh/projects/ZAsolar/docs/validation_strategy.md`
- Region registry: `/home/gaosh/projects/ZAsolar/configs/datasets/regions.yaml`

## Environment

- Same Python venv as main repo (`ZAsolar/.venv`)
- CUDA GPU only required if running full presence scoring on long stacks;
  Phase-0 anchor-presence scoring is mostly CPU + I/O bound
- `ZASOLAR_ROOT` env var overrides default path to main repo
  (defaults to `/home/gaosh/projects/ZAsolar`)
