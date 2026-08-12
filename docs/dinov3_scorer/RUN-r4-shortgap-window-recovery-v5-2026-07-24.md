# RUN — R4 short-gap window recovery V5

Status: **COMPLETE** (2026-07-24)

## Scope

V5 reviewed only the 1,196 `rejected_shortgap` rows from the completed V4
direct window review. It did not rerun the V3 six-factor experiment, modify the
R0 manifest, overwrite V4, or infer labels from free-text reasons.

The output root is:

`/home/gao/zasolar_data/geid_temporal/r4_shortgap_window_recovery_v5/`

## Frozen execution

- model alias: `gemini-3.5-flash-low`
- exact returned version: `gemini-default`
- native `responseSchema`
- `maxOutputTokens=8192`
- workers: 30
- global QPS: 6
- two primary reps per anchor
- third rep when either `sequence_status` or `first_pv_frame` disagreed
- two votes required for the exact `(sequence_status, first_pv_frame)` tuple
- no two-vote result: fail closed to `unreviewable`
- recovery rule:
  `r4_shortgap_recovery_rules_v1@2026-07-24`

The 10-anchor authenticated smoke completed 20 primary reps and locked
`gemini-default` before production. Production admitted 2,796 valid reps;
404/1,196 anchors required a third rep, for 66.2% primary exact-tuple
agreement. There were 2,874 production attempt rows, including 78 retryable
transport failures. No model-version drift occurred. A post-run production
resume reused all stored logical reps without new model calls.

Frozen hashes:

- prompt:
  `8342d72f5d2ec0f134b205ff02b2faffe799ab8fa3ba9e1271d1cb63640e2b42`
- response schema:
  `b5006dec78bf0b5f27906571499134ca1f9ed21367f2d540b82db2dd1e2f5590`
- recovery rule:
  `8f03ab804b3adbede61121ef8d6b0d4359cd92c478b795efe3d4961971d83821`

## Recovery result

| V5 outcome | anchors |
|---|---:|
| monotonic install interval | 509 |
| └─ moved boundary | 412 |
| └─ original boundary re-confirmed | 97 |
| left-censored / already present | 265 |
| right-censored / absent through strip | 224 |
| nonmonotonic manual review | 97 |
| unreviewable | 101 |
| total | 1,196 |

Of the 101 unreviewable rows, 86 had no exact tuple receiving two of three
votes. V5 recovered 5,248 definite frame labels across 998 anchors: 2,834
present and 2,414 absent. Every monotonic decision had a real valid frame
before the first-present frame, so all 509 produced an expressible interval;
there were no monotonic frame-only cases without a left boundary.

The combined accounting retains the 888 V4-confirmed strict `<45 day`
windows. The three inherited non-strict 91/92-day rows are all V4-confirmed
and remain outside the V5 rejected population.

## Additive products

- `recovery_verdicts.parquet`
- `recovered_frame_labels.parquet`
- `recovered_install_intervals.parquet`
- `left_censored.parquet`
- `right_censored.parquet`
- `manual_review_queue.parquet`
- `summary.json`
- `RUN_LOCK.json`
- `artifacts.sha256`

Each recovered label carries its source slot/date/SHA, agreeing reps,
confidence, model alias and exact version, prompt/schema/rule hashes, V5 and
source-V4 packet hashes, and temporal-strip SHA. Blank slots never produce
labels. Nonmonotonic and unreviewable rows never enter monotonic interval
loss.

## Verification

- relevant pytest: 48 passed
- `python -m py_compile`: passed
- `git diff --check`: passed
- sidecar uniqueness, date direction, partition coverage, and provenance
  invariants: passed
- `sha256sum -c artifacts.sha256`: all 2,833 entries passed
- final `artifacts.sha256` SHA-256:
  `d981604da5fdad0fdd5f7c025d36ebeadd58d7ec20ecc07d7ddbebbd19e57bfa`
