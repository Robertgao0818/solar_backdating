# ISSUE-06: Provenance sidecar per scoring call

Status: done
Phase: 1 — Provenance
Blocked by: ISSUE-05

## Parent

[`../install_date_optimization_v2_prd.md`](../install_date_optimization_v2_prd.md) — D5. User story 9.

## What to build

At the PresenceScorer seam, record for **every scored chip**: scorer identity
(resolved model string or checkpoint hash — today the model id is resolved
from env at run time and discarded), prompt/config hash, chip content hash
(sha256 of encoded chip bytes), scoring mode, and timestamp. Stored as a
sidecar (JSONL or similar) alongside scan states — **no scan-state schema
bump**; an optional provenance pointer may ride in the existing notes field.

This makes every verdict attributable to the exact model, instruction, and
pixels that produced it — the precondition for the verdict store (ISSUE-07)
and for any retroactive drift audit going forward.

## Acceptance criteria

- [x] Every call through the seam emits sidecar rows for all scored chips
- [x] Scorer identity captured from resolved config (not re-read from env downstream); verified for both cheap and capable model tiers
- [x] Prompt/config hash stable across runs with identical config; changes when the prompt changes (test)
- [x] Chip content hash computed on encoded bytes; identical chip → identical hash across runs
- [x] Documented limitation: historical scans cannot be backfilled (no model identity was recorded) — stated where downstream consumers will see it

## Blocked by

- ISSUE-05 (seam is the single interception point)
