# Full-timeline Lite reference pilot — 2026-10-05

Status: VOID as a reference-pilot result, owner decision 2026-10-05.
The replacement design is the [v2 ten-grid plan](PLAN-fulltimeline-reference-v2-10grid-2026-10-05.md),
which permits backup recovery and new downloads and requires actual T2/T3
image-role checks. This does not revalidate any v1 labels or results.
Scoring finished at 02:44Z with exit 0 and `pending_manifest=0`, and the
offline tables were written later that day. Every Gemini request carried
one image. Aerial bytes were never attached, and the prompt still described
an aerial reference. Intervals, the paired comparison, the adaptive replay,
and these labels are not usable for the pilot's claims. On the same day the
satellite chip pixels were deleted to free disk: `imagery/a3_tm/chips` and
`imagery/chips`. Raw responses, the plan, the audit tables, the A3 manifest,
and the quota ledger stay. Do not rerun the same 10 grids against the same
local aerial store.

The selection uses Cape Town `centroid_grid`, seed `20261004`, and the window
`2019-01-01 <= capture_date < 2025-01-31`. The census date is the published
policy bound (`policy_census_bound`), not a per-grid flight day. Risk grids
are those with any geometry exception or an A48 share of at least one half,
because the inventory has no clarity field. Development grids are not risk
grids.

Measured counts from the anchor table and the GEHI catalog:

| item | value |
| --- | --- |
| grids | 10 (2 development, 8 evaluation) |
| development | CPT2304, CPT3359 |
| evaluation | CPT1524, CPT1956, CPT2274, CPT2305, CPT2434, CPT2935, CPT2947, CPT3234 |
| N | 1114 |
| H | 51142 |
| H_pair | 7067 |
| paired anchors | 149 |
| nested review anchors | 85 |

Short grids keep every anchor, so the paired count is below 20 times 10.
Review anchors sit inside the paired set. Anchors with no in-window history
stay in the anchor table with `history_reason=no_history_in_window`.

The model alias is `gemini-3.1-flash-lite`. The gateway preflight returned
that same `modelVersion`. The prompt text is the spec wording; development
labels do not rewrite it. Temperature stays 0. The output cap starts at 2048
and may move once to 4096 if the technical check is truncated. Products stay
under `~/zasolar_data/geid_temporal/reference_fulltimeline_lite_10grid_20261004/`.
The tmux session is `fulltimeline_lite_10grid`. Scoring keeps at most 8
requests in flight. The gateway account serving this model allows 10
concurrent calls. A cap of 40 made requests wait 30 seconds for a slot and
return HTTP 429. Those bodies were local slot timeouts. A real upstream
quota 429 still pauses the run. A short burst of gateway 503 "no available
accounts" is retried: this model's capacity message resets in about ten
seconds, and the transport retry covers that burst. A sustained run of those
503s still pauses. The quota ledger is kept.

At 2026-10-04 17:03Z the scorer exited 1 with `pending_manifest=49768`.
The historical batch had reached a quota pause before any raw row in that
batch was stored (`new_requests` stayed 3871). After the sleep, the drain
logged `no progress on 56659 requests` and stopped. `SCORE_DONE.json` was
not written. `RECHECK_DONE.json` was written from the anchors that already
looked terminal (66 recheck calls) and is not a finished recheck. The drain
now retries a batch that paused without a new raw row, and a recheck
sentinel is honored only after `SCORE_DONE.json` exists. The premature
recheck sentinel is removed. Raw responses, chips, and the quota ledger stay.
The pause file's `resume_after` was `2026-10-04T16:46:38Z` (`pool_503`) and
had already passed before the resume. The first resume then spent about
eight minutes building the manifest, and each later chunk of 64 calls scanned
every candidate against the whole raw log before the next chunk. Raw lookup
for that manifest and for the pending set is now one index. The same tmux
session is resumed so the already stored Lite labels are kept.

At 2026-10-04 18:05Z that session had stored about 4743 new Lite calls in
this process, then an upstream 429 paused it. At that moment only account 16
mapped `gemini-3.1-flash-lite`, and its Lite reset is
`2026-10-04T21:37:15Z`. Wakes at 18:22Z and 18:47Z each got an instant 503
`No available Gemini accounts: no available accounts`. Additional accounts
were then given that same model map. The same process, still pid 205691,
woke at 20:27Z and continued. From 20:25Z the gateway logged Lite usage on
accounts 18, 14, 1, 7, 17, 3, 2, and 11. The alias did not change. At 2026-10-05 02:44Z the session was gone, no scorer process remained, and
`run.log` ended with `pending_manifest=0`, `recheck_new_requests=2242`,
and `EXIT:0`. `SCORE_DONE.json` says `n_requests=58205`.
`RECHECK_DONE.json` says `n_recheck_requests=2308`. The last raw rows are
`boundary_recheck` and `conflict_recheck` with `call_status=ok` and
`model_version` `gemini-3.1-flash-lite`. Raw responses and the quota ledger
stay. The watcher exited after reporting that clean finish.

Local aerial tiles under `~/zasolar_data/tiles/cape_town/aerial_2025` cover 41
other grids and none of these 10. `crop_aerial_from_tiles` therefore returns
no file for every frozen anchor. The render records those frames as
`download_failed`. Scoring degrades to `candidate_only_reference_unavailable`
and those anchors stay out of the two-frame versus three-frame contrast.
No Vexcel online fetch is used. The response schema keeps `candidate_index`
as an integer, and its enum is the string `"1"`. A numeric enum value is
rejected by the gateway with HTTP 400 (`TYPE_STRING`). One real chip under
that schema returned integer 1 and `modelVersion` `gemini-3.1-flash-lite`.
A zero index, and the string `"1"`, stay invalid responses and are not
rewritten into a present or absent label. The prompt string is unchanged.
The frozen `prompt_sha256` `861a9de3cea3…` is that string plus the technical-check
schema, where `candidate_index` had no enum. Later calls record enum `"1"`
in their own fingerprint. `plan/PARAMS_FROZEN.json` is not rewritten.
About 930 first-pass calls failed once on the numeric enum and remain in
the raw log for retry. Fourteen keys already used three attempts and stay
gaps. Historical candidate chips are unaffected:
the A3 manifest is 53035/53035 ok, and the 180 z18 sequential rows are ok.

The inventory candidate table marks only `not_downloadable`. Quality and
download failures are copied from the scored manifest onto those rows before
interval inference and boundary rechecks (`apply_manifest_gap_states`). A
scored date is left blank so the raw first-pass label stays the verdict. The
process that started the download loaded the scorer before this copy existed.
If that process writes `observations/RECHECK_DONE.json`, the corrected recheck
is a new `--stage recheck` after removing only that sentinel. Raw responses,
chips, and the quota ledger stay in place.

## Offline product audit — 2026-10-05

The tables below are retained only to show what the one-image run produced.
They are void for the pilot's claims.

`evaluate_reference_fulltimeline_pilot.py` was run twice against the finished
raw log. Both stdout blocks agree:
`audit_ok=true`, `valid_response_rate=0.999725`,
`candidate_byte_agreement=1.000000`, `foreign_model_versions=0`,
`reference_as_evidence=0`, `raw_overwrites=0`, `accuracy_pass=false`,
`n_grids=10`. Products are under `results/`, with `SHA256SUMS.txt`.
`intervals_adjudicated.csv` is a header only. No human labels were added.

The two-frame / three-frame contrast was not run. All 1,114 aerial renders
are `download_failed` with an empty PNG. Every stored first-pass row, on
both `t2` and `t3`, has degradation `candidate_only_reference_unavailable`
and empty aerial and satellite hashes. `paired_comparison.csv` has 7,064
date rows on 148 anchors, and `in_effect_comparison` is 0 on every row.
The candidate PNG hash matches on all 7,064 rows. The two arms disagree on
514 dates (131 anchors), including 51 present/absent swaps on 35 anchors.
Those disagreements are two calls on one candidate image. They are not a
frame-count effect. Satellite reference PNGs did pass QA (3,336). The
frozen role rule does not attach a satellite frame when the aerial frame
is missing, so those PNGs stay out of both arms.

Single-candidate T3 intervals, labels unmodified: bounded 434,
`conflict_pending_review` 448, undated 232. All 232 undated rows have
reason `aerial_reference_unknown`. There is no left-censored row and no
aerial upper bound. Rechecks exist for 882 anchors: 705 agreed on every
rechecked date (`intervals_repeat_supported.csv`) and 177 disagreed. The
232 undated anchors were not rechecked. Disagreements stay unresolved.

Against the published intervals, all 1,114 anchors match an old row. Width
is comparable for 422 bounded roofs: narrower 54, wider 218, same width 150.
`year_bound_changed` is 859. Of those, 670 have an empty new bracket
(conflict or undated) and 189 keep a new bracket whose year endpoint
differs. A narrower bracket is not an accuracy gain. Old raw sequences
were recovered for 708 anchors; the other 406 stay `not_recovered`.

Adaptive replay on these labels picked 16,913 dates and skipped 38,776.
No pre-2019 label entered the planner, and no reference frame was counted
as historical evidence. The skip list is the coverage loss of the current
select-and-stop rule on these single-candidate labels. It is not the set
of dates the old production scan actually skipped, and it is not a
three-frame result.
