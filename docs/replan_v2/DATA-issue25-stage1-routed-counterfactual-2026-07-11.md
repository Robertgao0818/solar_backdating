# DATA — ISSUE-25 Stage-1 area-routed counterfactual (2026-07-11)

Status: **executed on Stage-1 outputs; amendment adopted for Stage-2 winner
policy.** No new Gemini calls.

Parent:
[`ISSUE-25-teacher-geometry-stability-pilot.md`](ISSUE-25-teacher-geometry-stability-pilot.md)
(Amendment 2026-07-11).

Source analysis:
`/home/gaosh/zasolar_data/geid_temporal/issue25_teacher_geometry_20260710/stage_c/stage1_analysis.json`

Model / decoder (frozen): `gemini-3.1-flash-lite` / `changepoint`.

## 1. Why this needs no extra bake-off

Stage-1 already scored the **same 400 core targets** under A24, A48, and A96
(5 reps each). Pairwise `agree_key` counts are stored per area bucket. A
deterministic area route can therefore be scored by **re-pooling existing
matching_pairs / n_pairs** — not by re-calling the API.

## 2. Frozen route

```text
chip_arm(target) = A48  if source_area_m2 >= 40
                 = A24  otherwise
```

- Cut = **40 m²**, identical to the pre-registered small/large boundary.
- No second cut, no per-bucket arm shopping, no use of A96 in production route.
- `source_area_m2` / `area_bucket` come from the census fpcut footprint (upstream
  of any temporal label) and already live on the Stage-1 anchor table.

Bucket → arm under the freeze:

| area_bucket | n_targets | arm |
|---|---:|---|
| `a_xs_lt15` | 102 | A24 |
| `b_sm_15_40` | 100 | A24 |
| `c_md_40_100` | 99 | A48 |
| `d_lg_ge100` | 99 | A48 |

## 3. Per-bucket Stage-1 agreements (from analysis JSON)

| bucket | A24 agreement (pairs) | A48 agreement (pairs) | route takes |
|---|---:|---:|---|
| xs `<15` | 0.8422 (859/1020) | 0.8049 (821/1020) | A24 |
| sm `15–40` | 0.8120 (812/1000) | 0.8150 (815/1000) | A24 (by cut, not by max) |
| md `40–100` | 0.9091 (900/990) | 0.9303 (921/990) | A48 |
| lg `≥100` | 0.8919 (883/990) | 0.9646 (955/990) | A48 |

Note: sm is **not** an exact numerical tie (A48 +0.3 pp). The route still assigns
sm → A24 because the policy is the single 40 m² threshold, not a per-bucket
argmax. Treating sm as free choice would re-introduce a free parameter.

## 4. Routed aggregate

```text
overall matching = 859 + 812 + 921 + 955 = 3547
overall pairs    = 1020 + 1000 + 990 + 990 = 4000
overall agree    = 3547 / 4000 = 0.88675
```

| metric | value | single-arm reference |
|---|---:|---|
| overall (diagnostic) | **0.8868** | best single arm A48 = 0.8780 |
| small (`xs+sm`, pre-reg primary) | **0.8272** (A24) | A48 small = 0.8099 |
| large (`lg`, pre-reg guard) | **0.9646** (A48) | A24 large = 0.8919 |
| small floor (legacy+8 pp) | 0.7395 | pass |
| large floor (legacy−2 pp) | 0.7982 | pass |

The route improves **both** pre-registered primary metrics at once relative to
every pure arm: small stays at A24's best; large takes A48's best. Overall is
higher than the best single arm, but **overall is not a gate**.

## 5. What is robust vs noise

| contrast | Δ agree | n_targets | read |
|---|---:|---:|---|
| xs A24 − A48 | +3.7 pp | 102 | noise-scale; not used to pick a second cut |
| sm A24 − A48 | −0.3 pp | 100 | noise-scale; cut still forces A24 |
| md A48 − A24 | +2.1 pp | 99 | noise-scale |
| lg A48 − A24 | **+7.3 pp** | 99 | only robust bucket-level gain |

Physical prior for the lg gap: A24 review crop is 24 m edge-to-edge; large
arrays frequently exceed that width and lose panels under a fixed centred crop.
Routing larger footprints to A48 is the design that matches the instrument, not
a dredge over four bucket knobs.

## 6. Single-arm pre-reg selector (unchanged historical record)

Under the original Stage-2 single-arm rule (max small among large-safe; tie →
A24), Stage-1 still selects **A24** (`small=0.8272`, `large=0.8919`). That
selector remains in `stage1_analysis.json` as `single_arm_prereg_winner` for
audit. **Stage-2 production does not use it** after this amendment; production
uses `routed_A24_A48_cut40`.

## 7. Non-effects

- **B0 bridge:** still fixed 96 m group geometry; not re-routed.
- **Manifest hashes:** unchanged; `chip_arm` is a pure function of frozen
  `source_area_m2` materialised at Stage-2 prepare time.
- **CoJ / unusable R1 gates:** still Stage-D; not re-opened here.

## 8. Artifacts

| path | role |
|---|---|
| `stage_c/stage1_analysis.json` | arms + single-arm selector + routed block |
| `stage_c/stage2_winner_decision.json` | confirmed Stage-2 policy |
| `stage_c/.stage2_winner_confirmed` | launch sentinel (UTC) |
| `stage_c/stage2_anchors_96m.csv` | Stage-2 anchors with `chip_arm` column |
