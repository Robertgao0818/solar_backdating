# CoJ gate-failure high-thinking adjudication: blocked smoke

Date: 2026-07-24  
Run identity: `coj_gate_failure_agent_adjudication_v1`  
Requested alias: `gemini-3-flash-agent`  
Required exact version: `gemini-3-flash-a`

## Scope and frozen design

The run freezes a 200-anchor calibration panel:

- 56 V5/pilot disagreement anchors;
- 43 V5/pilot exact-agreement anchors;
- 50 non-overlap monotonic anchors whose pilot boundary changed;
- 20 non-overlap monotonic anchors whose pilot boundary did not change;
- 15 censored anchors;
- 16 difficult anchors.

The implementation uses native `responseSchema`, `maxOutputTokens=16384`,
two primary reps, a third rep on any decisive tuple disagreement, and a
deterministic frame-state decoder. The panel, packet provenance, prompt,
schema, and rule hashes are locked in the run `RUN_LOCK.json`.

## Smoke outcome

The authenticated smoke did not pass and production was not started.

1. An initial 10-anchor smoke at 6 QPS received both
   `gemini-default` and `gemini-3-flash-a` from the same requested alias.
   The run was quarantined as
   `coj_gate_failure_agent_adjudication_v1_failed_mixed_version_smoke`.
2. A second smoke accepted only `gemini-3-flash-a` and treated
   `gemini-default` as a retryable version rejection. At 1 QPS with 20
   attempts per rep, one anchor still exhausted all attempts with
   `gemini-default`; the run was quarantined as
   `coj_gate_failure_agent_adjudication_v1_failed_fallback_exhaustion`.
3. A direct one-image probe using model name `gemini-3-flash-a` entered the
   gateway's long `no available accounts` retry loop and was interrupted
   without a response.

The final incomplete attempt was quarantined as
`coj_gate_failure_agent_adjudication_v1_failed_high_route`. No response
returned as `gemini-default` was admitted to any consensus or sidecar.

## Verification

The new runner compiles, `git diff --check` passes, and its focused tests
pass (`17 passed` including the pilot regression tests). No production
adjudication artifacts, frame labels, or install intervals were generated.

The next safe action is to rerun the frozen panel only after the gateway can
guarantee a stable `gemini-3-flash-a` route (or after explicitly approving a
different model/version policy).
