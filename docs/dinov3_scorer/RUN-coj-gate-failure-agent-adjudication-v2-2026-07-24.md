# CoJ gate-failure high-thinking adjudication v2

Date: 2026-07-24  
Run root: `~/zasolar_data/geid_temporal/coj_gate_failure_agent_adjudication_v2/`  
Requested model: `gemini-3.6-flash-high`  
Exact returned version: `gemini-3.6-flash`

## Frozen panel and transport

The panel is the pre-frozen 200-anchor calibration set:

- V5/pilot disagreement: 56;
- V5/pilot exact agreement: 43;
- non-overlap monotonic, pilot boundary changed: 50;
- non-overlap monotonic, unchanged: 20;
- censored: 15;
- difficult: 16.

Native `responseSchema` was used with `maxOutputTokens=16384`, 30 workers,
6 QPS, two primary reps, and a third rep on any decisive tuple mismatch.
All smoke and production responses locked to `gemini-3.6-flash`; no
`gemini-default` or earlier agent-run response was admitted.

## Results

- 200 anchors;
- 460 valid rep rows;
- 64 anchors required a third rep;
- primary full-tuple agreement: 68.0%;
- target match: 198 same-target, 2 unclear;
- sequence status:
  - monotonic install: 138;
  - already present before F01: 16;
  - not present through F06: 20;
  - present-to-absent/nonmonotonic: 13;
  - unreviewable: 13.
- 1,027 R0 frame labels;
- 138 install intervals;
- 393 CoJ labels.

The two anchors whose primary calls repeatedly violated the
source-present/blank contradiction were fail-closed as unreviewable; they
received no synthetic frame label.

Among the 99 V5-overlap anchors:

- status agreement with V5: 46/99;
- exact status + first-frame agreement: 41/99.

Against the low-thinking pilot on all 200:

- status agreement: 152/200;
- exact status + first-frame agreement: 135/200.

The high-thinking result is therefore materially different from both prior
passes; it should be treated as a calibration/adjudication layer, not silently
merged into the production estimator without the planned agreement analysis.

## Visual cross-review

Thirty blinded conflict cases were rendered into five contact sheets under
`codex_blind_contact_sheets/` and visually inspected. The review focused on
roof identity, visible module grids, and temporal consistency. No systematic
rendering or target-mismatch defect was observed in the inspected set; the
ambiguous cases remain conservatively represented by `unreviewable` or
nonmonotonic statuses.

## Validation

`sha256sum -c artifacts.sha256` passes for the complete v2 run. Manifest
SHA256:

`df42c33005e275fe0c7632881e94a947b55c1b9382cb9be3f4bd27de450c41e3`

Focused adjudication tests pass. The broader validation suite has one
pre-existing unrelated failure because
`panel_repair_20260703/analysis_estimator_harness_extended/emissions_fitted.json`
is absent; the failure is in `test_pilot_sequence_head.py`, outside this run.
