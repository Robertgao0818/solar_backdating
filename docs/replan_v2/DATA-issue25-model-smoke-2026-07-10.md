# DATA — ISSUE-25 Stage-A model smoke (2026-07-10)

Status: executed. Human freeze: **`gemini-3.1-flash-lite`**.

Parent: [`ISSUE-25-teacher-geometry-stability-pilot.md`](ISSUE-25-teacher-geometry-stability-pilot.md)

## Setup

- Reused the existing banked96 target review PNGs; no imagery was downloaded.
- Took the deterministic first 20 targets from the frozen parity manifest.
- Area mix: 14 targets `<40 m2`, 2 targets `40–100 m2`, and 4 targets
  `>=100 m2`.
- Scored eight fixed vintages per target with three independent reps per model.
- Disabled the verdict store so every rep was a real hosted call.
- Started at `workers=40`, global `qps=20` per invocation.
- Tested the uncapped sequence path plus explicit `max_tokens=1024` and
  `max_tokens=8192` checks.

Models:

- `gemini-3.1-flash-lite`
- `gemini-3.5-flash-extra-low`

## Results

| metric | gemini-3.1-flash-lite | gemini-3.5-flash-extra-low |
|---|---:|---:|
| no-cap first-attempt API/schema success | 60/60 | 60/60 |
| no-cap final failures | 0/60 | 0/60 |
| no-cap frame abstains | **0/480** | 13/480 (2.7%) |
| no-cap non-monotonic target reps | **0/60** | 4/60 (6.7%) |
| overall pairwise exact-pattern agreement | 0.867 | 0.867 |
| overall pairwise first-present-date agreement | 0.867 | **0.883** |
| `<40 m2` pairwise exact-pattern agreement | **0.905** | 0.881 |
| `<40 m2` pairwise first-present-date agreement | 0.905 | 0.905 |
| all-three exact-pattern targets | 16/20 | 17/20 |
| all-three first-present-date targets | 16/20 | 17/20 |
| `max_tokens=1024` first-attempt success | **20/20** | 0/20 |
| `max_tokens=1024` final success after retry | **20/20** | 1/20 |
| `max_tokens=1024` final failure | **0/20** | 19/20 (JSON truncation) |
| `max_tokens=8192` first-attempt success | 20/20 | 20/20 |

Cross-model agreement was only 12/20 exact patterns in every matched rep
(first-present-date agreement 12/20, 13/20, 12/20). Model choice is therefore a
material intervention and must stay fixed inside the geometry comparison.

## Decision

**Freeze `gemini-3.1-flash-lite`.** It ties overall pattern repeatability, is
better on the pre-registered primary `<40 m2` exact-pattern comparison, emits
no frame abstains or non-monotonic sequences in this smoke, and is robust to
the low-token-cap footgun.

Stage-B scoring keeps the model fixed. The end-to-end adaptive API smoke still
must pass before the download/scoring run is released at scale.

## Artifacts

Raw score CSVs and per-attempt audit JSONL files:
`/tmp/issue25_stageA/` on the 2026-07-10 execution host.
