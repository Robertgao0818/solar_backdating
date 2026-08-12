# Codex visual review protocol

Status: owner-approved execution policy, 2026-08-01

This protocol replaces future human review in the install-date backdating
plan with structured visual review performed by Codex over the frozen local
chip/strip artifacts. It applies to CT-06 placement/geometry QA, CT-11
jump-critical QA, and replan v2 ISSUE-11. Historical human-review records are
unchanged.

## What this review is, and is not

Codex review is an external AI review channel relative to the production
Gemini scorer. It can adjudicate placement, transition-window evidence,
missing/ambiguous imagery, and interval consistency. It is not an independent
human gold standard and does not create a physical install-date truth source.

Therefore the reports use `Codex review agreement`, `Codex repeat agreement`,
and `Codex-reviewed reference set`. They must not call these quantities human
accuracy, inter-annotator agreement, or independently verified physical
install-date accuracy.

The owner still makes the final cohort-release decision. No human annotation
is required for the review gates themselves.

## Blind input contract

The reviewer receives a frozen, hash-locked package containing:

- the target polygon/box and geometry metadata needed for placement review;
- the dated chip strip, capture dates, achieved zoom, provider, and pixel-QA
  result;
- municipal true-date/reference imagery where that channel exists; and
- the frozen review assignment and stratum.

For scientific jump-point review, production `status`, interval, confidence,
model tier, rescue outcome, and the original Gemini verdict are hidden until
the Codex verdict is written. For geometry review, the target overlay is
visible but the downstream scoring outcome is hidden. Sampling and assignment
are frozen before review; no anchor may be added, removed, or replaced after
seeing its result.

## Verdict contract

Scientific jump-point review permits only:

- `CONFIRM` — the production bracket is supported by the visible evidence;
- `SHIFT` — the transition is visible but the corrected absent/present bracket
  is different; or
- `UNDATABLE` — the available imagery cannot support a defensible transition.

Geometry review additionally records `PASS`, `MISALIGNED`, `EXCEPTION`, or
`UNREVIEWABLE`. A missing, corrupt, or low-quality chip is never converted to
`absent`; it remains an explicit non-release/undatable condition.

Every Codex review record must carry at least:

```text
review_run_id
review_pass
reviewer_type=codex_visual
reviewer_engine
review_time_utc
assignment_id
anchor_id
input_manifest_sha256
strip_or_chip_sha256
blind_to_production
verdict
corrected_latest_absent
corrected_earliest_present
review_confidence
rationale
```

The exact Codex model/runtime identity and the prompt/instruction hash are
recorded when the review run is executed. Review output is append-only and
must not overwrite scan state, verdict-store rows, or production provenance.
For the legacy ISSUE-10 JSONL schema, these review fields may be stored in a
sidecar keyed by `review_run_id`, `sample_batch_id`, and the verdict-manifest
SHA; do not mutate historical human manifests in place.

## Repeat review

At least 20% of the scientific sample is shown in a separate fresh Codex pass
with a new `review_run_id`, no access to pass-1 verdicts, and the same frozen
inputs. Report this as `codex_repeat_agreement`. It measures review stability,
not inter-annotator agreement. Disagreements remain visible and are not
resolved by confidence-based winner selection; use `UNDATABLE` or an explicit
owner decision where the evidence cannot decide.

## Gate and claim rules

- CT-06 may close when the Codex placement sheet shows no systematic
  misalignment and every exception has a disposition.
- CT-11/ISSUE-11 may close when the frozen sample, first pass, 20% repeat pass,
  verdict manifest, weighted bracket metrics, and Wilson intervals are
  complete.
- Metrics are reported overall and by risk stratum, with rescue/model tiers
  kept separate.
- The release README must say `Codex-reviewed QA` and
  `first-visible-appearance interval`; it must not claim human gold truth,
  physical install-date accuracy, or human inter-annotator agreement.
