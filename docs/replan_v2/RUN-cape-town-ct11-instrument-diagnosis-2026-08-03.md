# Cape Town CT-11 review-instrument diagnosis — 2026-08-03

Status: **instrument defect confirmed; CT-11 remediation required; release HOLD**

This diagnosis follows the frozen first CT-11 Codex review. It does not alter
the frozen review manifests or production scan states. Its purpose is to test
whether the observed 15.404% exact-bracket agreement can validly serve as a
release decision.

## Findings

### 1. The reviewed contact sheets discarded critical spatial resolution

The first and repeat reviews used 160×126 thumbnails. Typical source chips are
approximately 395–398×314–315 pixels, so the reviewer received about 40% of
the source linear resolution before the whole 980×4100 contact sheet was
presented. At z=19 this materially reduces the module-border evidence needed
to separate PV from skylights, roof texture, shadows, and solar water heaters.

Codex repeat agreement is strongly class-dependent despite the 72/100
aggregate:

| Pass-1 class | Repeat state agreement |
|---|---:|
| ALREADY_PRESENT | 44/49 |
| ALL_ABSENT | 10/21 |
| TRANSITION | 2/8 |
| UNDATABLE | 16/22 |

The aggregate therefore hides substantial instability in the ALL_ABSENT and
TRANSITION classes.

### 2. Sixty `done_appears` strips omitted a production boundary

Only 265/325 `done_appears` records display both exact production boundary
dates. Sixty omit at least one boundary, making exact-bracket `CONFIRM`
impossible by construction. Across all status types, 319/500 strips literally
contain both interval endpoints; census-bound endpoints are policy dates rather
than image dates, so 440/500 are operationally comparable after treating the
census ceiling correctly.

This omission does not explain the full disagreement. Restricting to
operationally comparable and adjudicable records gives 61/343 = 17.784%
exact-bracket agreement, which remains low.

### 3. Three Gemini tiers disagree with the thumbnail-based Codex result

A fixed, outcome-balanced 40-anchor subset (10 from each blind class) was
re-scored on the same full-size CT-11 strip images. Every pilot used strict
global QPS=6 from the first request, `max_in_flight=0`, eight workers, a fresh
model canary, exact returned-model enforcement, and an independent fail-closed
quota ledger.

| Tier | Codex class agreement | Codex interval agreement | HTTP evidence |
|---|---:|---:|---:|
| `gemini-3.1-flash-lite` replay | 10/40 (25.0%) | 7/40 (17.5%) | 40×200, zero retry/429 |
| `gemini-3-flash` recovery | 15/40 (37.5%) | 11/40 (27.5%) | 40×200, zero retry/429 |
| `gemini-3.6-flash` troubleshooting | 13/40 (32.5%) | 9/40 (22.5%) | 40×200, zero retry/429 |

The two stronger tiers agree with each other on class for 35/40 and interval
for 33/40. All three tiers agree on class for 27/40. A two-of-three majority
exists for 39/40, but agrees with the old Codex class on only 14/39. The
conflict is most concentrated in Codex `ALREADY_PRESENT`: recovery Flash
matches 0/10 in that stratum.

These pilots do not prove Gemini correctness. They prove that the old Codex
contact-sheet output is not a sufficiently stable external reference to
declare either the production pipeline or a replacement model correct.

## Corrected native-resolution package

A separate diagnostic package was rendered without changing the frozen QA:

`goldset_ct52_20260803_gridmin/native_resolution_diagnostic_20260803/`

It contains 500 blind records, all 1,745 frames, and 167 PNG sheets with at
most three anchors per sheet in a 2×2 native-resolution layout. It exposes no
anchor IDs, production statuses, intervals, confidence, or previous verdicts.
The package and every sheet are hash-locked; the top-level package SHA256 is
`0938f741ad1af3cb912ac08685f308324dd795e9020e956e6979bd3026cc7984`.

The three model-pilot roots are:

- `production_lite_v2_20260731/ct11_lite_replay_pilot_20260803/`
- `production_lite_v2_20260731/ct11_recovery_pilot_20260803/`
- `production_lite_v2_20260731/ct11_troubleshooting_pilot_20260803/`

Each root has a frozen `selection.json`, `summary.json`, append-only quota
ledger, per-anchor result/audit files, and a verified `pilot_outputs.sha256`.

## Decision and required remediation

The first CT-11 metrics remain useful diagnostic evidence but are invalid as
the final release acceptance test. CT-11 returns to `in-progress` under a
release HOLD.

Before re-review:

1. render native-resolution, production-boundary-complete strips; every dated
   bracket must include both exact claimed boundary frames plus flanks;
2. freeze a new randomized blind order and package hashes;
3. preregister the product acceptance lower bound before corrected verdicts
   are read;
4. run a corrected independent Codex pass and at least 20% fresh blind repeat;
5. report class-specific repeat stability as well as aggregate stability;
6. only diagnose or replace production intervals from that corrected external
   evidence, then rebuild and re-audit the 21,453-row deliverable.

Steps 1–2 are now complete in
`goldset_ct52_20260803_gridmin_corrected_native_v2/`: 500 Pass-1 anchors and a
100-anchor fresh-order repeat were frozen with zero boundary omissions and zero
post-cutoff frames. Step 3 is awaiting owner confirmation in
[`RUN-cape-town-ct11-corrected-qa-prereg-2026-08-03.md`](RUN-cape-town-ct11-corrected-qa-prereg-2026-08-03.md).

No Top-52 release or downstream full-Cape-Town imagery download is authorized
by the current evidence.
