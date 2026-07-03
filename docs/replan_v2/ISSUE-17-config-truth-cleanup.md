# ISSUE-17: Config truth — dead-knob removal + single-sourced zoom ladder

Status: done
Phase: 1 — Provenance
Blocked by: none

## Parent

[`../install_date_optimization_v2_prd.md`](../install_date_optimization_v2_prd.md) — D16. User story 36.
Evidence: 2026-07-03 chip-geometry & resolution audit (amendment block in the PRD).

## What to build

The audit found that editing most of the scan config silently does nothing:
the repo's sole YAML loader reads only the `adaptive_scan:` section, while
`anchor_manifest:` (the "adaptive chip size" knobs, whose documented 45 m
ceiling production silently exceeds) and `gehi_download:` (zoom candidates
plus an output_root that does not exist on disk) are read by nothing.
Separately, the census-narrowing scan hardcodes its own lower zoom ladder
(19,18) with no explanation, so the config's ladder does not govern it.

Make the config file truthful end-to-end: dead sections are deleted (or
explicitly annotated as dead with the replacement live knobs named in-file),
the zoom ladder has one source of truth that both production orchestrators
consume (or an explicit, in-file justified divergence), and a consumed-keys
test asserts every key present in the YAML is actually read by code — so the
next dead knob fails a test instead of misleading the next person who edits
it.

## Acceptance criteria

- [ ] `anchor_manifest:` and `gehi_download:` sections deleted or annotated-as-dead, with the live replacement knobs named in the file
- [ ] Census-narrowing scan's zoom ladder either reads the shared config or carries an explicit in-file justification for divergence
- [ ] Consumed-keys test fails when the YAML contains a key no code reads (verified by adding a dummy key)
- [ ] Editing the live zoom ladder demonstrably changes both production orchestrators' download invocation (fake-runner test)
- [ ] No behavior change to chip geometry: production chip size stays frozen at 96 m (D18)

## Blocked by

None - can start immediately
