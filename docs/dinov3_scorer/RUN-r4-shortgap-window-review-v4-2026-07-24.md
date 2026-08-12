# RUN — R4 direct short-gap window review V4

Status: **COMPLETE** (2026-07-24)

## Question

For each of the 2,146 proposed under-45-day installation windows, answer only:

- `confirmed_shortgap`: the same readable roof has no PV in EARLIER and PV in
  LATER;
- `rejected_shortgap`: the same readable roof has any other endpoint pattern;
- `unreviewable`: roof correspondence or either endpoint cannot be judged.

This replaces the V3 six-factor robustness/model-selection experiment for this
operational review. V3 outputs are not reused as V4 verdicts.

## Frozen execution

- model alias: `gemini-3.5-flash-low`;
- exact returned version: `gemini-default`;
- workers: 30;
- global QPS: 6;
- two independent primary calls per callable anchor;
- a third call only when the primary verdicts disagree;
- three exact source/render conflicts are automatically `unreviewable`;
- prompt and response schema are SHA-locked in `RUN_LOCK.json`.

The initial `maxOutputTokens=2048` completed every logical rep except one.
That rep returned `MAX_TOKENS` five times because approximately 1,963 tokens
were consumed by low-level thinking before the short JSON answer completed.
The cap was amended to 8,192 and only the still-missing logical rep resumed at
attempt 6. All earlier admitted responses had already finished with `STOP`.

## Result

| verdict | anchors |
|---|---:|
| confirmed short gap | 891 |
| rejected short gap | 1,196 |
| unreviewable | 59 |
| total | 2,146 |

Gemini reviewed 2,143 anchors. The run admitted 4,470 valid reps; 184 anchors
required a third rep. No model-version drift occurred.

The inherited empty-`K_i` population contains three decoder-chain cases whose
endpoint distance is 91/92 days, even though the operational request concerns
strictly under-45-day windows. They remain in the 2,146-row audit for
provenance and are flagged `strict_under_45_days=false`. The strict subset is
2,143 anchors: 888 confirmed, 1,196 rejected, and 59 unreviewable.

Canonical outputs:

- `window_review.parquet`
- `window_review.csv`
- `summary.json`
- `rep_verdicts.parquet`
- `RUN_LOCK.json`
- `artifacts.sha256`
