# RUN — R4 short-gap boundary adjudication with blinded Gemini model selection

Status: **V3 MODEL SELECTION FAILED CLOSED / NO PRODUCTION RELEASE** (designed
2026-07-23; amended and executed 2026-07-24). The R4 empty-`K_i` preflight
found 2,146 `done_appears` anchors whose teacher boundary is not representable
under the frozen 45-day decoder epoch rule.

**Executed human-reference/model-gate result (2026-07-24):** one Codex visual
reviewer completed two order-isolated passes over the 40 frozen packets, with a
third adjudication pass on four non-clear rows. This is explicitly **not**
represented as two independent human people. The locked clear denominator was
36/40. After the reference was SHA-locked and the arms were unsealed,
`gemini-3.5-flash-extra-low` matched 22/36 complete tuples (61.1%) and
`gemini-3.1-flash-lite` matched 26/36 (72.2%). Neither met the frozen 90%
competence gate. Model selection, production, consensus, and the override
sidecar therefore remain unreleased.

**V3 rerun amendment (2026-07-24, owner-authorized after the V1 panel
fail-closed):** V1 stopped when one Flash-Lite rep returned a non-usable
endpoint with a definite PV state on all three attempts. A V2 transport probe
then established that the gateway/model combination does not reliably obey
conditional `responseJsonSchema`: the extra-low arm returned empty or
incomplete objects, so V2 stopped before any schema-valid scientific rep. V3
is a new run identity (`r4_shortgap_adjudication_v3`) and reuses no V1/V2
scientific reps. It uses the short imperative prompt reproduced in §5 with the
previously working native `responseSchema`; the complete contradiction rules
remain enforced locally as the fail-closed layer. The transport release is 30
workers and a global 6 QPS per arm. Population, panel IDs, factor vocabulary,
consensus, competence gate, and model-selection rules are unchanged.

Parent contracts:

- [`RUN-r4-training-calibration-prereg-2026-07-20.md`](RUN-r4-training-calibration-prereg-2026-07-20.md)
- [`PRD-run3-native-local-line-2026-07-19.md`](PRD-run3-native-local-line-2026-07-19.md)
- [`DATA-issue25-model-smoke-2026-07-10.md`](../replan_v2/DATA-issue25-model-smoke-2026-07-10.md)

## 1. Decision and purpose

The frozen R4 preflight found **2,146 / 32,964 interval-eligible anchors** with
an empty teacher cell set `K_i`. Almost all are true short-cadence pairs (the
largest modes are 30, 31, 7, and 40 days), but visual inspection showed a
mixture of:

- genuine installation transitions;
- panels already visible in both boundary frames;
- panels absent in both frames;
- source/registration changes where the same physical target is not reliably
  comparable;
- unusable imagery incorrectly marked `usable` with high teacher confidence.

This run uses a Gemini judge to clean boundary-frame labels at scale, but the
judge is **not chosen by model-name ordering**. The ISSUE-25 smoke froze
`gemini-3.1-flash-lite`: it was better on the primary `<40 m2` exact-pattern
comparison and produced fewer abstains/non-monotonic sequences, while
cross-model exact-pattern agreement was only 12/20. Because the pair/context
packet here is a material task change, §9 first compares
`gemini-3.1-flash-lite` and `gemini-3.5-flash-extra-low` on one frozen,
human-labeled, blinded panel. Only the pre-registered winner may adjudicate the
remaining population.

The run does **not** change the frozen 45-day decoder, make a truth/accuracy
claim, or turn these anchors back into interval-loss examples. All 2,146 remain
`interval_loss_eligible=false` because the decoder cannot express their
original teacher interval. The output only determines whether the two boundary
frames can safely contribute corrected frame labels.

The R0 manifest remains immutable. Corrections are an additive, versioned
sidecar consumed only by the amended R4 loader.

## 2. Frozen population

Re-derive from the SHA-locked R0 manifest; do not select from output folders.
An anchor enters when all are true:

1. R0 split is `train` or `calibration` (never `test`);
2. frozen `scan_status == done_appears`;
3. latest effective absent before earliest effective present forms the teacher
   bracket `(lower, upper]`;
4. under `gap_days=45`, `build_k_i` returns empty.

Expected population: **2,146 anchors**. The manifest builder must fail if this
count changes before an explicit amendment. It records gap days, A24/A48,
area bin, split role, both endpoint rows, adjacent temporal flanks, cache keys,
and every source/render SHA.

Observed audit facts used only for planning, not filtering:

- 1,371 pairs are `noversion -> noversion`;
- 1,996 pairs are currently `usable -> usable`;
- 97.1% have teacher confidence >= 0.9 at both endpoints;
- 84.0% are `<40 m2`.

Same internal version is **not** an auto-reject rule. Per the GEHI contract,
multiple date labels sharing a version may still be useful observations.

## 3. Deterministic filters before Gemini

Only provable conflicts are auto-resolved. Similar-looking imagery, equal
version strings, high feature cosine, SSIM, or small date gaps are diagnostics,
not hard filters.

### 3.1 `AUTO_IDENTICAL_SOURCE_CONFLICT`

If the two boundary rows have equal `src_tiff_sha256`, opposite labels cannot
both be true. Do not call Gemini. Set both boundary labels to `uninformative`,
set `interval_loss_eligible=false`, and retain the original rows in provenance.

### 3.2 `AUTO_IDENTICAL_RENDER_CONFLICT`

If source SHAs differ but the exact R1 marker-free crop bytes have equal SHA,
apply the same rule. This is exact byte equality only, not perceptual hashing.

### 3.3 Input integrity and render preflight

Input integrity failures are **not labels**. Before the first model request,
the builder must validate every manifest-listed source and geometry object,
verify its locked SHA, and render every required boundary/context/available
flank image. Any of the following makes the build fail with no verdict
sidecar:

- a source TIFF or geometry sidecar is missing, unreadable, malformed, or has
  the wrong SHA;
- geometry cannot be transformed/rasterized under the locked renderer;
- a required render raises, produces no output, or has an unexpected
  size/channel/schema;
- a manifest-listed flank exists but cannot be read or rendered.

The failed object and exception are written to `build_failures.jsonl` for
repair, but no affected frame becomes `uninformative`. A missing temporal flank
that is absent from the frozen manifest remains an explicit blank slot and is
not a build failure.

`AUTO_CONTENT_UNUSABLE` is allowed only after the locked source and geometry
were successfully read and the renderer completed normally. If the resulting
target support has zero valid intersecting pixels/area or all target pixels are
nodata under the frozen validity-mask rule, mark only that endpoint
`uninformative`; do not call Gemini for the anchor. Persist the validity mask,
pixel/area counts, renderer version, and rendered-byte SHA so this semantic
override cannot hide a path, synchronization, or renderer fault.
The unaffected endpoint receives no override and therefore retains its frozen
R0 label. `interval_loss_eligible=false` still applies to the anchor.

Every auto row is written to the same verdict table with `decision_source`
identifying the exact code rule. No auto filter infers `present` or `absent`.

## 4. Gemini review packet

One anchor per API call. Do not batch multiple anchors into one prompt: target
identity and source-shift reasoning are the purpose of this adjudication, and
cross-anchor image ordering is an avoidable failure mode.

The model receives exactly three rendered review images, in a SHA-locked order:

1. **Boundary tight pair:** R1 marker-free earlier/later target crops, labeled
   `EARLIER` and `LATER` only.
2. **Boundary context pair:** the corresponding 96 m source views with the
   target ROI outline rendered from the geometry sidecar.
3. **Temporal strip:** up to two preceding frames, the two boundary frames,
   and up to two following frames, chronological, using tight marker-free
   crops. Missing flanks are explicit blank slots, never silently reordered.

The old Gemini labels, `done_appears`, confidence, and words
`absent`/`present` are **not sent**. Dates are shown only as neutral frame IDs.
The model is told that imagery may change sensor, GSD, color, parallax, or
registration and must first establish that it is comparing the same physical
roof.

Review PNG/JPEG bytes, their SHAs, rendered prompt, image order, endpoint source
SHAs, geometry version, and routing salt are persisted before the request.

## 5. Model, request, and exact-version lock

- candidate aliases for the §9 panel:
  **`gemini-3.1-flash-lite`** and
  **`gemini-3.5-flash-extra-low`**;
- production alias: exactly the §9 winner; no run is released if neither arm
  passes;
- API: existing native `generateContent` transport through the local gateway;
- temperature: `0`;
- `maxOutputTokens`: **8192** (the ISSUE-25 smoke showed this model truncates
  JSON under a 1024-token cap);
- response MIME: `application/json` with a strict response schema;
- no model fallback and no mid-run alias substitution;
- 30 workers, global 6 qps per arm for the V3 release;
- maximum 3 transport/schema attempts per logical rep, then fail closed;
- unique routing salt per `(anchor_id, rep, attempt)`;
- requested alias and exact returned model version persisted per attempt.

The V3 prompt is:

```text
Review one target.

Use this order:
1. Tight: EARLIER, LATER.
2. Context: EARLIER, LATER.
3. Strip: F01 to F06.

First confirm the same roof.
Allow sensor or registration shifts.
Judge endpoints separately.
Ignore order as transition evidence.

Use usable="yes" only if readable.
If usable is not "yes", set pv_state="unclear".
Use transition_supported="yes" only for the same target, two usable endpoints,
EARLIER absent, and LATER present.
Otherwise use "no" or "uncertain".

Return schema-valid JSON only.
```

An alias string is not a model lock. For each panel arm, the first successful
schema-valid response establishes `exact_model_version` in `RUN_LOCK.json`.
The panel mapping and winning exact version are then frozen before production;
production's first response must equal the panel winner's exact version.
Every response, including retries, must report the same non-empty exact version
before its payload is admitted. A missing or different returned version causes
an immediate `MODEL_VERSION_DRIFT` stop: publish no consensus/override
sidecar, quarantine the entire affected arm/run, and do not resume it under the
new version. A new exact version requires a new run ID and a fresh blinded
panel. Thus a rolling gateway alias can never mix versions in one scientific
result.

## 6. Factorized response schema

The model does not directly choose an R4 action. It returns observable factors:

```json
{
  "same_physical_target": "yes | no | uncertain",
  "earlier": {
    "usable": "yes | no | uncertain",
    "pv_state": "present | absent | unclear"
  },
  "later": {
    "usable": "yes | no | uncertain",
    "pv_state": "present | absent | unclear"
  },
  "transition_supported": "yes | no | uncertain",
  "failure_modes": [
    "registration_shift | source_change | cloud_shadow | blur | occlusion | construction | wrong_roof | other"
  ],
  "confidence": 0.0,
  "reason": "one short sentence"
}
```

Validation rejects contradictions, including:

- `same_physical_target=no` with `transition_supported=yes`;
- endpoint `usable=no|uncertain` with a definite PV state;
- `transition_supported=yes` unless earlier=`absent`, later=`present`, and
  the target and both endpoints are `yes`;
- missing/extra fields or an out-of-vocabulary enum.

### 6.1 Complete derived-class truth table

After schema validation, apply the following mutually exclusive rows from top
to bottom. They cover every legal factor tuple; there is no implementation
discretion.

| row | predicate | derived class |
|---:|---|---|
| 1 | `same_physical_target=no` | `TARGET_MISMATCH_OR_SOURCE_SHIFT` |
| 2 | `same_physical_target=uncertain` | `AMBIGUOUS` |
| 3 | target=`yes` and either endpoint `usable=no` | `ONE_OR_BOTH_UNUSABLE` |
| 4 | target=`yes`, no endpoint is `no`, and either endpoint `usable=uncertain` | `AMBIGUOUS` |
| 5 | target=`yes`, both usable=`yes`, states=`absent,present`, transition=`yes` | `CLEAN_INSTALL_TRANSITION` |
| 6 | target=`yes`, both usable=`yes`, states=`present,present`, transition=`no` | `ALREADY_PRESENT_BOTH` |
| 7 | target=`yes`, both usable=`yes`, states=`absent,absent`, transition=`no` | `ABSENT_BOTH` |
| 8 | every other legal tuple | `AMBIGUOUS` |

Consequently, usable `absent,present` with transition=`no|uncertain`,
`present,absent` with any legal transition value, a usable endpoint with
`pv_state=unclear`, or same-state endpoints with transition=`uncertain` are
all `AMBIGUOUS`. `transition_supported=yes` outside row 5 is schema-invalid
and is retried, not classified. This table is unit-tested by enumerating the
complete Cartesian product of the declared enums and checking that every
schema-valid tuple hits exactly one row.

## 7. Replication and consensus

Run two independent real calls for every non-auto anchor. The verdict store is
disabled for the two primary reps so they cannot collapse to one cached answer.

A third independent call is mandatory when any is true:

- the two derived classes disagree;
- any gate-bearing factor disagrees (target identity, either endpoint
  usability, either endpoint PV state, or transition support);
- either confidence is below 0.80;
- either response contains `uncertain`/`unclear`;
- either primary rep required a schema retry.

Accept a consensus only when at least two reps agree on all gate-bearing
factors: target identity, endpoint usability, both endpoint PV states, and
transition support; the derived class must therefore also agree.

Any consensus action that would write `present` or `absent` at either endpoint
requires at least two of those fully agreeing reps to each have
`confidence >= 0.80`. The threshold is class-independent. If fewer than two
qualifying reps exist, no definite endpoint label may be written:

- `CLEAN_INSTALL_TRANSITION`, `ALREADY_PRESENT_BOTH`, or `ABSENT_BOTH` becomes
  final `AMBIGUOUS`;
- `ONE_OR_BOTH_UNUSABLE` may retain that class for reporting, but every
  otherwise-definite usable endpoint is downgraded to `uninformative`;
- fail-closed `uninformative` actions for mismatch, unusable, or ambiguity do
  not require a confidence floor.

There is no confidence averaging and a high-confidence rep cannot rescue a
single low-confidence agreeing rep. If no two reps achieve full-factor
agreement at all, the final class is `AMBIGUOUS`.

Expected budget before deterministic filters: 4,292 primary calls plus third
rep calls for disagreements/low-confidence cases. Calls are resumable by
`(anchor_id, rep)`; an interrupted call is never counted as a scientific rep.

## 8. Label-override policy

Write a sidecar; never rewrite R0:

```text
r4_shortgap_adjudication_v1/
  candidate_manifest.parquet
  auto_verdicts.parquet
  build_failures.jsonl  # present only for a failed build
  review_packets/<sha-prefix>/<anchor_id>/*.jpg
  panel/panel_manifest.parquet
  panel/human_annotations.jsonl
  panel/human_reference.parquet
  panel/model_selection.json
  attempts.jsonl
  rep_verdicts.parquet
  consensus.parquet
  frame_label_overrides.parquet
  summary.json
  RUN_LOCK.json
  artifacts.sha256
```

Consensus actions:

| class | earlier override | later override | interval loss |
|---|---|---|---|
| `CLEAN_INSTALL_TRANSITION` | absent, only with §7 confidence gate | present, only with §7 confidence gate | disabled |
| `ALREADY_PRESENT_BOTH` | present, only with §7 confidence gate | present, only with §7 confidence gate | disabled |
| `ABSENT_BOTH` | absent, only with §7 confidence gate | absent, only with §7 confidence gate | disabled |
| `TARGET_MISMATCH_OR_SOURCE_SHIFT` | uninformative | uninformative | disabled |
| `ONE_OR_BOTH_UNUSABLE` | apply endpoint table below | apply endpoint table below | disabled |
| `AMBIGUOUS` | uninformative | uninformative | disabled |

For `ONE_OR_BOTH_UNUSABLE`, each endpoint independently uses this exhaustive
table; “retain” is not an implementation option:

| consensus endpoint factors | override |
|---|---|
| `usable=no|uncertain` | `uninformative` |
| `usable=yes`, `pv_state=unclear` | `uninformative` |
| `usable=yes`, `pv_state=present|absent`, with two fully agreeing reps each at confidence `>=0.80` | that exact consensus state |
| any other/unmatched condition | `uninformative` |

Only the two boundary rows may be overridden in v1. Flanks are context, not new
labels. After applying the sidecar, R4 recomputes frame class counts and teacher
brackets. These anchors remain frame-loss-only even if their corrected sequence
would happen to form another bracket.

## 9. Release gates and frozen human/model panel

### 9.1 Exact panel manifest and quotas

The competence/model-selection panel is the 40 anchor IDs in Appendix A.
Selection salt is
`r4_shortgap_model_panel_v3@2026-07-24`; within each locked
`(gap, area, split)` quota cell, selection is by ascending
`sha256(UTF8(salt) + NUL_BYTE + UTF8(anchor_id))`, with the gap-30 rows
additionally required to be same-version. The canonical LF-terminated,
lexicographically sorted ID list has SHA-256
`80bcccd8e175acfdd8be23de4677f3e0d3d10416f3ea441b8d4f3ff5bd702ea1`.

The exact marginal quotas are:

| factor | locked counts |
|---|---|
| gap days | 7: 10; 30: 10; 31: 10; 40: 10 |
| source area | `<15`: 16; `15-40`: 16; `>=40 m2`: 8 |
| endpoint version relation | same: 20; cross: 20 |
| crop arm | A24: 32; A48: 8 |
| R0 role | train: 30; calibration: 10; test: 0 |

Within every gap, area quotas are 4/4/2 in the displayed area order. For gaps
7 and 31 the split quotas within those area cells are 3/1, 3/1, and 1/1
train/calibration; for gaps 30 and 40 they are 3/1, 3/1, and 2/0. The panel
builder writes `panel_manifest.parquet` with endpoint/flank row keys, source
and render SHAs, all quota columns, selection hash, and packet SHA. Its complete
file SHA is frozen in `RUN_LOCK.json` before any human label or model call.
Any ID, quota, packet-byte, or SHA mismatch is fatal; there is no replacement
sampling.
If a panel row hits an exact-conflict or `AUTO_CONTENT_UNUSABLE` rule, the
model-comparison panel is not 40 callable anchors and cannot release either
arm; stop and amend/re-freeze the panel rather than substituting a row.

Visual failure modes are human/model outcomes, not pre-existing sampling
strata. Their realized counts are reported and cannot be used to swap panel
rows.

### 9.2 Human rubric and reference

Two named annotators independently label all 40 packets, blinded to R0 labels,
teacher confidence, candidate model, model output, and each other's answers.
They use the exact §6 factor schema plus `human_visually_clear=yes|no` and a
reason code. Applicability is fixed:

- a clearly mismatched target is target=`no`, endpoint states=`unclear`,
  transition=`no`;
- an endpoint that is clearly unusable is usable=`no`, state=`unclear`;
- target=`yes` and usable=`yes` requires a definite endpoint state to be
  considered visually clear;
- the §6 truth table derives the human class; annotators never choose an
  override directly.

An anchor enters the clear reference denominator only when both annotators mark
it clear and exactly agree on **all six gate-bearing factors** (target
identity, two usability values, two PV states, transition support). All other
anchors go to a third named senior annotator for a documented adjudication but
remain outside the gate denominator; adjudication cannot convert them into
clear rows. `N_clear` must be at least **30 of 40**, otherwise the panel is
incapable of releasing either model and the run stops. Human files, annotator
IDs, rubric version, timestamps, and their SHA-256 values are locked before
unmasking model arms.

The scoring unit is one anchor's complete six-factor tuple. On a clear human
row, a model `AMBIGUOUS`, abstention, low-confidence downgrade, or any
factor mismatch counts as an error; class-only or endpoint-only agreement is
reported but cannot satisfy the gate.

### 9.3 Blinded model comparison and selection

Both aliases receive byte-identical panel packets, prompt/schema, and §7
two-primary-plus-conditional-third protocol. An independent panel controller
randomizes the presentation/routing order and exposes only arm `A`/`B` to the
human scorer and metric report. The alias-to-arm map and each arm's exact
version lock are sealed until the human reference and model outputs are
SHA-locked.

For each arm, primary accuracy is
`complete_tuple_matches / N_clear`. An arm is competent only at **>=90%**.
Selection is deterministic:

1. if neither arm is competent, stop with no scale release;
2. if exactly one is competent, select it;
3. if both are competent, select the arm with more complete-tuple matches;
4. if tied, select `gemini-3.1-flash-lite`, the prior ISSUE-25 freeze.

No secondary metric breaks the tie. The selected alias **and exact returned
version** become the sole production lock under §5.

### 9.4 Remaining release gates

1. Unit tests: population derivation, input-failure hard stops,
   `AUTO_CONTENT_UNUSABLE`, exact-conflict filters, packet order/hash, exhaustive
   factor-tuple enumeration, contradiction rejection, confidence consensus,
   endpoint overrides, exact-version drift, panel quotas/SHA, sidecar round
   trip, and R4 loader application.
2. Authenticated 10-anchor transport smoke for each model arm before panel
   scoring: 20 real primary calls per arm, zero final API/schema failures, and
   exact requested/returned versions locked.
3. Complete §9.1-§9.3 human/model panel and select exactly one competent arm.
4. Packet audit: no prompt contains original endpoint labels or teacher
   confidence; every image order matches its persisted sidecar.

If any gate fails, stop. Do not prompt-tune on the 2,146 population; any prompt,
schema, packet, rubric, confidence, model-version, or selection-rule change
requires a new run version and a newly frozen panel.

## 10. Reporting

Report consensus class and override counts overall and by:

- gap days;
- A24/A48;
- area `<15`, `15-40`, `40-100`, `>=100 m2`;
- same-version vs cross-version and exact version pair;
- train vs calibration role;
- primary-rep agreement, third-rep rate, API/schema failure rate;
- confidence bucket and failure mode.

Headline quantities are the fraction confirmed clean, the fraction with a
corrected endpoint label, and the fraction downgraded to uninformative. These
are data-cleaning measurements, not independent install-date accuracy.

No R4 training starts until this run closes, its sidecar is SHA-locked, the
R4 prereg is amended to name the sidecar and frame-only rule, and the worktree
is clean at the recorded implementation commit.

## Appendix A. Frozen 40-anchor competence/model-selection panel

Canonical order for the ID-list hash is lexicographic:

```text
jhb_full382_unified_a_merge01_c0925_fpcut_2026_06_01_t00001760
jhb_full382_unified_a_merge01_c0925_fpcut_2026_06_01_t00001893
jhb_full382_unified_a_merge01_c0925_fpcut_2026_06_01_t00002141
jhb_full382_unified_a_merge01_c0925_fpcut_2026_06_01_t00004986
jhb_full382_unified_a_merge01_c0925_fpcut_2026_06_01_t00008447
jhb_full382_unified_a_merge01_c0925_fpcut_2026_06_01_t00009606
jhb_full382_unified_a_merge01_c0925_fpcut_2026_06_01_t00012322
jhb_full382_unified_a_merge01_c0925_fpcut_2026_06_01_t00012540
jhb_full382_unified_a_merge01_c0925_fpcut_2026_06_01_t00013761
jhb_full382_unified_a_merge01_c0925_fpcut_2026_06_01_t00014140
jhb_full382_unified_a_merge01_c0925_fpcut_2026_06_01_t00016822
jhb_full382_unified_a_merge01_c0925_fpcut_2026_06_01_t00016869
jhb_full382_unified_a_merge01_c0925_fpcut_2026_06_01_t00017574
jhb_full382_unified_a_merge01_c0925_fpcut_2026_06_01_t00018629
jhb_full382_unified_a_merge01_c0925_fpcut_2026_06_01_t00022066
jhb_full382_unified_a_merge01_c0925_fpcut_2026_06_01_t00022133
jhb_full382_unified_a_merge01_c0925_fpcut_2026_06_01_t00022777
jhb_full382_unified_a_merge01_c0925_fpcut_2026_06_01_t00023713
jhb_full382_unified_a_merge01_c0925_fpcut_2026_06_01_t00024779
jhb_full382_unified_a_merge01_c0925_fpcut_2026_06_01_t00027511
jhb_full382_unified_a_merge01_c0925_fpcut_2026_06_01_t00028153
jhb_full382_unified_a_merge01_c0925_fpcut_2026_06_01_t00028253
jhb_full382_unified_a_merge01_c0925_fpcut_2026_06_01_t00029552
jhb_full382_unified_a_merge01_c0925_fpcut_2026_06_01_t00030368
jhb_full382_unified_a_merge01_c0925_fpcut_2026_06_01_t00032426
jhb_full382_unified_a_merge01_c0925_fpcut_2026_06_01_t00033275
jhb_full382_unified_a_merge01_c0925_fpcut_2026_06_01_t00033549
jhb_full382_unified_a_merge01_c0925_fpcut_2026_06_01_t00034698
jhb_full382_unified_a_merge01_c0925_fpcut_2026_06_01_t00035051
jhb_full382_unified_a_merge01_c0925_fpcut_2026_06_01_t00035684
jhb_full382_unified_a_merge01_c0925_fpcut_2026_06_01_t00035946
jhb_full382_unified_a_merge01_c0925_fpcut_2026_06_01_t00036170
jhb_full382_unified_a_merge01_c0925_fpcut_2026_06_01_t00037113
jhb_full382_unified_a_merge01_c0925_fpcut_2026_06_01_t00037214
jhb_full382_unified_a_merge01_c0925_fpcut_2026_06_01_t00037235
jhb_full382_unified_a_merge01_c0925_fpcut_2026_06_01_t00037516
jhb_full382_unified_a_merge01_c0925_fpcut_2026_06_01_t00037743
jhb_full382_unified_a_merge01_c0925_fpcut_2026_06_01_t00038603
jhb_full382_unified_a_merge01_c0925_fpcut_2026_06_01_t00039693
jhb_full382_unified_a_merge01_c0925_fpcut_2026_06_01_t00039728
```
