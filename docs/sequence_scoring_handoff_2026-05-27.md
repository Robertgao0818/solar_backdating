# Sequence Scoring Handoff - 2026-05-27

## Context

We tested Gemini temporal scoring for chip-group install-date inference. The
main comparison is:

- Matrix scoring: multiple targets x multiple dates in one request.
- Sequence scoring: one target x five ordered dates in one request, returning
  a target-level temporal sequence JSON.

Current evidence favors sequence scoring as the primary path for batch
install-date inference. Matrix scoring remains useful as a diagnostic/baseline,
but it is more vulnerable to target leakage and single-date false positives.

## Relevant Code State

Subrepo: `/home/gaosh/projects/solar_backdating/`

Modified/uncommitted files before this handoff:

- `README.md`
- `scripts/temporal/gehi_common.py`
- `scripts/validation/gemini_solar_image_review.py`
- `tests/temporal/test_gehi_common.py`
- `tests/temporal/test_gemini_batch.py`
- `scripts/temporal/score_chip_group_matrix.py`
- `tests/temporal/test_score_chip_group_matrix.py`

Important current matrix entrypoint:

```bash
python scripts/temporal/score_chip_group_matrix.py \
  --chip-targets-csv <chip_targets.csv> \
  --image-artifacts-csv <gehi_image_artifacts.csv> \
  --output <presence_output.csv> \
  --audit-dir <audit_dir>
```

Relevant tests already run:

```bash
pytest tests/temporal/test_gehi_common.py \
       tests/temporal/test_gemini_batch.py \
       tests/temporal/test_score_chip_group_matrix.py
```

Result: `42 passed`. There was a `.pytest_cache` write warning only.

## JNB0150 Findings

Pilot root:

`/home/gaosh/zasolar_data/geid_temporal/pilots/jnb0150_matrix_20260526/`

Original problematic pattern on two targets:

`2018-06-30, 2022-03-30, 2023-01-30, 2024-02-29 = 0,1,0,1`

Current JSON-mode matrix rerun on the already downloaded 4-date artifacts:

`chip_group_presence_timeseries_fullrerun_current.csv`

Result for both targets:

`0,1,1,1`

Full 67-date rerun:

- Download manifest: `gehi_image_artifacts_full.csv`
- Output: `chip_group_presence_timeseries_full67_current.csv`

The original four-date anomaly is fixed in the full rerun, but the long series
still contains early isolated positives around `2012-01-30`,
`2017-10-30`, and `2017-12-30`, followed by later absences. Those rows are
marked with `non_monotonic_requires_review`.

Interpretation: matrix JSON mode improved the specific `0,1,0,1` issue, but it
does not fully solve temporal false positives.

## JNB0202 Findings

Pilot root:

`/home/gaosh/zasolar_data/geid_temporal/pilots/jnb0202_matrix_20260520/`

Five census-pre-date window:

`2018-03-30, 2019-07-30, 2021-08-30, 2022-03-30, 2024-02-29`

Matrix rerun output:

`chip_group_presence_timeseries_current_jsonmode_5date.csv`

Summary:

- 14 targets with rows.
- 65 rows written; theoretical max is 70.
- Missing rows are all `2021-08-30` for chip groups that lack that artifact.
- No `1->0` non-monotonic target sequence was found.

Important sequence-vs-matrix sample:

- Target: `jhb_full382_unified_a_merge01_c0925_t00034508` / `T02`
- Review HTML:
  `single_target_sequence_jnb0202_t00034508_T02_review.html`
- Local browser URL while the static server is running:
  `http://127.0.0.1:8765/single_target_sequence_jnb0202_t00034508_T02_review.html`
- Matrix result: `0-0-1-1-1`
- Single-target sequence result: `00000`
- Human review: sequence is correct. Matrix positives are likely
  construction/roof-frame/shadow false positives, not PV.
- Interpretation note:
  `single_target_sequence_jnb0202_t00034508_T02_interpretation.md`

## Sequence Scoring Direction

Preferred batch unit:

One target, five ordered dates, one Gemini request.

Why:

- The model sees temporal continuity for one physical target.
- The output can be judged as a sequence rather than independent cells.
- It reduces target leakage between nearby labels.
- It can suppress construction/shadow/frame false positives when they do not
  persist as PV-like modules across time.

Prompt guidance should stay compact. Avoid long hard-negative checklists that
make the model search for many categories. Recommended concise rule:

```text
Do not count construction frames, shadows, skylights, or water heaters as PV
unless a regular rectangular PV module grid is visible on the target roof
segment.
```

The main structure should be the temporal sequence task, not an exhaustive
negative taxonomy.

## Proposed Batch Script

Create:

`scripts/temporal/score_target_sequence.py`

Suggested CLI:

```bash
python scripts/temporal/score_target_sequence.py \
  --chip-targets-csv <chip_targets.csv> \
  --image-artifacts-csv <gehi_image_artifacts_precensus_dense.csv> \
  --output <target_sequence_presence.csv> \
  --audit-dir <sequence_audit_dir> \
  --dates 2018-03-30,2019-07-30,2021-08-30,2022-03-30,2024-02-29 \
  --workers 2 \
  --qps 0.5 \
  --resume
```

Recommended defaults:

- `--workers 2` initially.
- `--qps 0.3` to `0.5` initially.
- One request = one target x five images.
- JSON mode with response schema.
- Write raw response, parsed JSON, request metadata, and retry/error status.
- Retry invalid/truncated JSON once.
- Preserve failed rows as `sequence_failed`; do not silently drop targets.
- Support `--limit-targets` for smoke tests.
- Support `--resume` by skipping target/date windows that already have a
  parsed audit/output record.

## Output Schema Sketch

Target-level sequence output should keep both the compact sequence and expanded
date observations:

```json
{
  "chip_id": "...",
  "anchor_id": "...",
  "target_label": "T02",
  "date_window": "2018-03-30,2019-07-30,2021-08-30,2022-03-30,2024-02-29",
  "sequence_pattern": "0-0-0-0-0",
  "first_present_date": null,
  "first_present_date_index": null,
  "confidence": 0.95,
  "consistency_flag": "monotonic",
  "decision_source": "gemini_sequence",
  "quality_flag": "usable",
  "review_notes": "...",
  "observations": [...]
}
```

For compatibility with legacy `presence_timeseries` / interval tools, also
emit a long-form CSV with one row per target-date:

- `anchor_id`
- `grid_id`
- `chip_id`
- `target_label`
- `capture_date`
- `pv_present`
- `pv_score`
- `decision_source=gemini_sequence`
- `quality_flag`
- `sequence_pattern`
- `sequence_confidence`
- `sequence_consistency_flag`
- `gemini_evidence`
- `review_png_path`
- `source_chip_path`

## Token Budget Note

Current Gemini helper always passes `maxOutputTokens` / `max_tokens`.

For sequence scoring, add support for `max_tokens=None` and omit the field from
the request payload. The provider/model will still have a service-side maximum,
but the pipeline should not impose an unnecessarily low cap that truncates
JSON. Keep an optional CLI cap for production cost control.

## Suggested Next Discussion

Before writing the batch script, decide the grid-to-sequence pipeline:

1. Select grid(s) and source inventory targets.
2. Build fixed chip groups and target markers.
3. Discover candidate GEHI vintages.
4. Select a fixed five-date pre-census window.
5. Download or reuse GEHI chips for that window.
6. Render target review PNGs.
7. Run one-target sequence Gemini scoring.
8. Validate JSON and write audit/output.
9. Convert sequence rows into install-date intervals.
10. Compare against matrix baseline and human spot checks.

