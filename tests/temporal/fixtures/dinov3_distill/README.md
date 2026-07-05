# dinov3 distillation-set example fixture (ISSUE-02)

Tiny, fully **fabricated** example for `scripts/temporal/build_distillation_set.py`.
Nothing here is real corpus data — it is a documentation aid and a byte-stable
reference. The unit tests (`tests/temporal/test_build_distillation_set.py`)
fabricate their own inputs in `tmp_path` and do **not** read this directory.

## Contents

- `scan_states/*.json` — four fabricated `ScanState` files exercising every
  branch of the label rule:
  - `example_distill_t00000001` — clean per-target (`t`) anchor, CBD grid
    `JNB0133`, present + absent usable high-conf rounds.
  - `example_distill_c0000001` — clean chip-group (`c`) anchor, non-CBD grid,
    absent + present rounds.
  - `example_distill_t00000002` — `done_ambiguous_nonmonotonic` anchor: rounds
    are retained but forced to `label_3class=unusable` (anchor-level veto).
  - `example_distill_t00000003` — low-confidence (0.80) and abstain
    (`pv_present=None`) rounds → `unusable`.
- `chip_targets_example.csv` — a couple of chip-target rows for the render join
  (`t`-anchor joins on `anchor_id`, `c`-anchor on `chip_id`).
- `expected_label_manifest.csv` — the manifest the harvester emits for the four
  scan states above with `cbd_grids={JNB0133}` and `assign_splits(..., salt="example",
  heldout_frac=0.5)`. Regenerate with the snippet in the ISSUE-02 handoff.
- `example_chip.png` — a 204×204 tiny GEHI review PNG (copied from the sibling
  `dinov3_chips` fixture) standing in for a re-rendered scoring crop. Real chips
  are never committed; they live under `~/zasolar_data/` (gitignored).

## Provenance

Fabricated 2026-07-04 for ISSUE-02. Grid ids, dates, versions and confidences
are synthetic. Real harvest artifacts land under
`~/zasolar_data/geid_temporal/dinov3_distill_<date>/` and are gitignored.
