# RUN — CoJ gate-failure recovery pilot V1

Status: **COMPLETE** (2026-07-24)

## Scope

This is an independent, additive pilot over CoJ reference-gate failures. It
does not change the CoJ audit verdicts, V4, V5, R0, or the DINO training set.
The pilot uses Gemini for basic same-target and temporal image understanding;
it is not a human gate and is not ground truth.

Population: 4,261 reference-gate-failed targets from the completed CoJ audit.

Sampling was frozen before pilot requests:

- 120-target census of `CoJ gate failed ∩ V4 population`;
  - 99 are also in V5's `rejected_shortgap` population;
  - 19 are V4 `confirmed_shortgap`;
  - 2 are V4 `unreviewable`.
- 57 non-overlap targets with one or two uninformative CoJ audit observations,
  included as a census.
- 249 proportional draws from the remaining informative targets, stratified by
  reference arm × area bin × chip arm × split.
- Nine zero-allocation micro-strata were amended with one target each,
  covering the remaining 30 targets and restoring positive inclusion
  probability to every fine stratum.

Final sample: **435 targets (10.21% of 4,261)**. Sampling weights sum exactly
to 4,261. The pre-amendment 426-row sample and packet index are retained under
`pre_amendment/` with their original SHA values in `RUN_LOCK.json`.

## Frozen visual instrument

Each packet contains:

1. confirmed-present 2024/2025 reference montage;
2. target-specific CoJ 2019/2023 montage;
3. six-slot chronological R0 target-crop strip.

The 120 V4-overlap targets use their frozen V4 temporal strip. The other 306
initial pilot targets use six outcome-independent R0 crops selected around the
persisted interval dates. One corrupt CoJ crop was re-rendered from its locked
source TIFF and target geometry inside the pilot output; the original CoJ
artifact was not modified.

- model alias: `gemini-3.5-flash-low`
- exact returned version: `gemini-default`
- native response schema
- `maxOutputTokens=8192`
- workers: 30
- QPS: 6
- two primary reps; third rep if any of five decisive tuple fields disagreed
- no two-vote full-tuple consensus: `unreviewable`

## Pilot result

| structured result | sample anchors |
|---|---:|
| `monotonic_install` | 317 |
| `already_present_before_F01` | 23 |
| `not_present_through_F06` | 47 |
| `present_to_absent_or_nonmonotonic` | 13 |
| `unreviewable` | 35 |
| total | 435 |

The run admitted 978 valid reps; 108 anchors required a third rep. 401 anchors
had a two-vote full-tuple consensus and 34 failed closed. It produced:

- 317 R0 install intervals;
- 259 changed boundaries;
- 70 censored rows;
- 13 manual-review rows;
- 2,217 R0 frame labels;
- 800 year-level CoJ state labels.

The sampling-weighted exploratory totals over the 4,261 gate-failed population
are:

- monotonic interval mass: 3,254.54 targets, **76.38%**;
- interval-boundary-changed mass: 2,638.46 targets;
- interval-eligible changed-boundary fraction: **81.07%**.

These are design-weighted pilot estimates, not full-population verified labels.
No confidence interval or economic-delivery correction is released from this
pilot alone.

## V5 overlap diagnostic

Among the 99 `CoJ gate failed ∩ V5 rejected_shortgap` anchors:

- V5/pilot sequence-status agreement: **50/99**;
- exact status + first-frame agreement: **43/99**.

The pilot includes confirmed-present reference and CoJ context, while V5 used
the temporal strip-only recovery prompt. This disagreement is scientifically
informative: the two runs must remain separate evidence layers and must not be
silently merged into a single definite interval label.

## Verification

- pilot tests: passed
- relevant pytest: 55 passed
- `python -m py_compile`: passed
- packet and sidecar date/SHA invariants: passed
- production attempts with retryable failures: 0
- authenticated smoke: 10 anchors, 20 primary reps, exact version locked
- final `sha256sum -c artifacts.sha256`: all entries passed
- artifact manifest entries: 2,203
- `artifacts.sha256` SHA-256:
  `691a5cbfad322f49e8b8a1f12b7be9ab8ab6848842630281b1124431d3e1785a`

Canonical output root:

`/home/gao/zasolar_data/geid_temporal/coj_gate_failure_recovery_pilot_v1/`
