# DRAFT — R4 empty-`K_i` conservative abstain amendment

Status: **OWNER CONFIRMED AND INCORPORATED AS BINDING PARENT AMENDMENT A1
2026-08-03**

Parent contract:
[`RUN-r4-training-calibration-prereg-2026-07-20.md`](RUN-r4-training-calibration-prereg-2026-07-20.md).

## Trigger

The real R4 pre-start materialization recomputed the frozen `gap_days=45`
teacher-cell contract and stopped on exactly **2,146 / 32,964**
interval-eligible anchors with empty `K_i`. All 2,146 are `done_appears`; the
two teacher boundary dates fall inside one student epoch and therefore cannot
be represented by the frozen decoder. Continuing with an empty target set or
changing the decoder gap would violate the parent preregistration.

Read-only deterministic re-derivation from the frozen R0 manifest produced:

| R0 role | anchors | boundary rows |
|---|---:|---:|
| train | 1,593 | 3,186 |
| calibration | 553 | 1,106 |
| test | 0 | 0 |
| total | 2,146 | 4,292 |

## Proposed bounded policy

For every affected anchor only:

1. keep the R0 manifest and split assignment byte-identical;
2. set `interval_loss_eligible=false` for the anchor;
3. override the exact earlier/later teacher boundary frames to
   `uninformative` for frame loss;
4. leave every non-boundary frame unchanged;
5. consume no V3, V4, V5, CoJ, CT, repeat-panel, or R5 test label;
6. retain the anchor in train/calibration frame supervision and reporting.

This is the conservative fail-closed treatment: it neither pretends that an
unrepresentable interval has a valid `K_i` nor retains the contradictory
absent/present pair as definite frame supervision. It does not alter the
decoder, head, loss coefficients, seeds, calibration roles, threshold gates,
or R5 correction budget. It is a pre-start input-contract amendment made
before any valid seed result exists.

## Owner-gated implementation

- Config:
  `configs/r4_empty_k_conservative_amendment_v1.json`
- Builder:
  `scripts/temporal/build_r4_empty_k_conservative_sidecar.py`
- Tests:
  `tests/temporal/test_build_r4_empty_k_conservative_sidecar.py`

Owner confirmation evidence is recorded in the config as user message
`确认 R4 保守修订和隔离提交` at `2026-08-03T09:00:53Z`. The implementation
tests and the real read-only population derivation pass. The frozen sidecar SHA
is `fefac6fe234e2a9e2c21ea77aa0bd7cc8b788ff19e89f89310cf0171da23a82a`;
the exact policy and hashes are incorporated as Amendment A1 in the parent
preregistration. Formal training still waits for a clean implementation commit
and successful materialization/preflight from that commit.

## Post-confirmation sequence

1. Record exact owner evidence and UTC time in the amendment config.
2. Build the sidecar from the frozen R0 manifest only.
3. Require 2,146 anchors, 4,292 unique boundary rows, test rows zero, all
   overrides `uninformative`, and all interval flags false.
4. Freeze `frame_label_overrides.parquet`, `RUN_LOCK.json`, `summary.json`, and
   `artifacts.sha256` read-only and verify every hash.
5. Amend the parent R4 preregistration to name the exact sidecar SHA and this
   frame-only rule before materialization or training.
6. Re-run `prepare` and `materialize-locks`; require empty `K_i=0`, all §1.4
   gates zero, and the 200-anchor reference/torch decoder check pass.
7. Only from an owner-authorized clean implementation commit may formal R4
   training begin.

## Confirmation requested

Required owner evidence text:

> 确认 R4 保守修订和隔离提交

The evidence now exists. Parent status remains **R4 prereg amended and frozen;
training not started** until the clean-lock preflight passes.
