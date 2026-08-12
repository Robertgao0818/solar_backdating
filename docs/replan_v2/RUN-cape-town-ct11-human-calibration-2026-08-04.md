# Cape Town CT-11 human-reference calibration — 2026-08-04

Status: **blind package frozen; awaiting two independent human reviews; release HOLD**

This is development work on the already failed CT-11 set. It is not a new
acceptance attempt and does not read, render, or open the sealed disjoint
500+100 confirmation holdout.

## Why this step is required

The corrected Codex review had only 68% state agreement on its 100-anchor
repeat. Old-set development also rejected every low-risk automated remediation:

- recovery sequence scoring: 37.5% exact interval agreement;
- reference-anchored Lite scoring: 30.0%;
- production-provenance rules: 52.097%;
- frozen DINOv3-L-SAT linear visual head: best 56.291%.

Training a larger model directly against the current labels would therefore
risk fitting review noise. The next evidential requirement is an independently
reviewed and adjudicated old-set calibration sample.

## Frozen package

Root:

`~/zasolar_data/geid_temporal/cape_town_top52_backdating_v1_20260724/production_lite_v2_20260731/ct11_human_calibration_v1_20260804/`

- 100 unique anchors from the failed 500 only;
- all 32 old Pass-1/repeat state conflicts included;
- remaining 68 selected by old Pass-1 class × frame-count strata;
- 41 two-frame, 27 three-frame, and 32 four-frame records;
- one anchor per native-detail sheet;
- 100/100 sheet hashes verified;
- every package file is mode `0444`;
- blind package contains no anchor IDs, production state/interval, or old label;
- package SHA256 `bb11e06f8d476161969d1a0360a4fd8eff2c1a9b068ac924b7c47915767914c0`;
- review-contract SHA256 `7bbb34f095568a16cb52f75980a4f59712cb16ace19c21a90316b34104e7503a`.

Reviewers receive only `reviewer_blind/`. `private/` is prohibited until both
independent outputs are frozen.

Blank 100-row output templates are frozen under `review_templates/`; A and B
have the identical SHA256 `874e60edeac44c49c6b16daab492e674cdabd2bf688c44bbaa4fc1ff8a5aeaee`.
Each reviewer copies their template to an independent writable location before
filling it; the frozen template itself is not edited.

## Review and adjudication contract

Two human reviewers independently return, for every calibration index:

`independent_class`, `latest_absent`, `earliest_present`,
`review_confidence`, and a short `rationale`.

Allowed classes are `ALREADY_PRESENT`, `ALL_ABSENT`, `TRANSITION`, and
`UNDATABLE`. Blur, tree cover, shadow, missing imagery, or loss of target focus
must never be converted to absence. Only rows with differing class/date
decisions are opened for adjudication after both independent files are frozen.

The resulting agreement and adjudicated labels are development evidence only.
They may be used to choose and lock a remediation method. They cannot turn the
failed CT-11 holdout into PASS and cannot replace the still-sealed disjoint
confirmation run.

`scripts/temporal/evaluate_ct11_human_calibration.py` validates both frozen
100-row files fail-closed, enforces class/date semantics, reports state and
interval agreement with Wilson 95% intervals, and emits only differing rows to
the adjudication queue. A frozen adjudication file must cover exactly that
queue; the same tool then produces the 100-row `final_reference_labels.csv` and
`FINAL_REFERENCE_LOCK.json`. Synthetic 100-row CLI smoke tests covered both the
independent-comparison and final-adjudication paths; focused tests are 10/10.

## Release consequence

Top-52 publication, CT-12 owner go/no-go, and full Cape Town GEHI acquisition
remain held. The sealed confirmation holdout is opened once only after a
remediation method is locked from old-set evidence.
