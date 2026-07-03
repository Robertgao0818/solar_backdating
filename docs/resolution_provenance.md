# Resolution provenance & effective-resolution accounting

Status: Phase 1 — Provenance (ISSUE-18 / PRD D17). Evidence base: the 2026-07-03
chip-geometry & resolution audit (amendment block in
[`install_date_optimization_v2_prd.md`](install_date_optimization_v2_prd.md),
D16–D18).

The install-date scan records, per scored chip, only the *ladder rung whose
download call succeeded* (`RoundResult.actual_zoom`). That number is a poor proxy
for the resolution actually delivered to the scorer. This page defines the
end-to-end resolution accountability that closes the gap: the canonical per-chip
provenance record, the cache escape hatch, the achieved-zoom re-summarizer, and
the sentinel effective-resolution estimator. It also pins the normative rule that
D2's emission matrices stratify on **achieved** zoom.

## The three silent degradation channels

The audit established, with on-disk verification against production artifacts,
three ways resolution silently degrades — none of them visible to any tool that
trusts `actual_zoom`:

1. **In-chip tile substitution.** GEHistoricalImagery's binary substitutes
   coarser tiles (up to two zoom levels, nearest-neighbour upsampled) per 256 px
   tile *inside* a nominally-successful download. `actual_zoom` records the
   ladder rung the call returned at, not the delivered pixels, so a chip labelled
   z20 can carry z18 pixels in part of its extent and nothing downstream knows.
2. **First-cached-zoom pin.** The skip-existing cache returns the first
   non-empty chip found on the ladder for an anchor/date/version forever. Neither
   production orchestrator exposed an overwrite path, so upgrading the download
   ladder (e.g. adding z20 ahead of z19) had **zero effect on already-cached
   anchors** — they stayed pinned at whatever zoom was cached first.
3. **Divergent census ladder.** `run_census2023_scan.py` drives a lower Wayback
   ladder (z19→z18) than the TM adaptive scan (z20→z19→z18). This is legitimate
   (Wayback has essentially no z20) but it is a second reason the two corpora's
   achieved-zoom mixes differ and must be read separately.

Downstream of the chip is comparatively clean (no resize/re-encode before the
scorer; multi-zoom chips are sent as separate image parts; tile-snap extent error
≤0.9%), so the resolution problem lives entirely at the chip supply.

## Canonical per-chip provenance record

Both ISSUE-18 deliverables and the docs use exactly these field names. They are
shaped to join the future ISSUE-06 scoring sidecar on
`(anchor_id, capture_date, version)` and on `chip_sha256`:

| field | meaning |
| --- | --- |
| `anchor_id`, `capture_date`, `version` | join key to scan state / scoring sidecar |
| `provider` | `TM` / `Wayback` |
| `requested_zoom_ladder` (list[int]) | the ladder the download was asked to try, in order |
| `achieved_zoom` (int \| null) | rung the download actually succeeded at (null = all failed) |
| `status` | `ok` / `skipped_existing` / `all_zooms_failed` |
| `chip_path` | path to the delivered GeoTIFF |
| `chip_sha256` | sha256 of the encoded chip bytes — the content-address / verdict-store key |
| `raster_width_px`, `raster_height_px` | delivered raster dimensions |
| `raster_crs` | CRS of the delivered raster |
| `extent_minx`, `extent_miny`, `extent_maxx`, `extent_maxy` | raster extent, raster-CRS units |
| `gsd_x_m`, `gsd_y_m` | ground sample distance, metres/pixel (geographic CRS → deg→m with cos(lat) for x) |
| `raster_error` | null when the raster read succeeded; else the message, with the other raster fields null |

**Join contract with the ISSUE-06 scoring sidecar.** The scoring sidecar records
`(scorer_identity, prompt_hash, chip_sha256, verdict)` per scoring call. The
provenance record supplies the *pixel* side of the same row: joining on
`chip_sha256` attributes a verdict to the exact delivered pixels, and joining on
`(anchor_id, capture_date, version)` attributes it to the scan slot. Because the
verdict store keys on `chip_sha256` (not capture-date/version metadata — the
provider re-renders pixels under stable metadata, D6), a churned re-render lands
as a new `chip_sha256` and cannot silently reuse a stale verdict. `achieved_zoom`
travels with both so every downstream stratification (below) can key on it.

## Cache escape hatch (`--overwrite-chips` / `--min-cache-zoom`)

Sibling deliverable in the download path. Two escapes close the first-cached-zoom
pin:

- `--overwrite-chips` — bypass the skip-existing cache entirely and re-download
  (the `overwrite=True` path in `download_chip_with_zoom_ladder`, which already
  ignores the cache scan).
- `--min-cache-zoom N` — accept a cached chip only if its cached rung is ≥ N;
  otherwise re-fetch through the ladder. This is the targeted upgrade lever: a
  ladder that newly prefers z20 has **no effect on already-cached anchors**
  unless you tell the cache that a z19-cached chip is no longer good enough.

**When to use it.** Any time the download ladder is upgraded (or a corpus is
suspected of first-cached-zoom pinning), a plain re-run changes nothing —
cached anchors are returned as `skipped_existing` at their old rung. Use
`--min-cache-zoom` to force the upgrade only where the cache is below target
(cheapest), or `--overwrite-chips` for an unconditional re-fetch (e.g. to chase a
suspected provider re-render). Leaving both off preserves byte-identical
reproducibility, which is the correct default for a re-run that must replay an
existing cohort.

## Reading the achieved-zoom summaries

`RoundResult.actual_zoom` is the achieved rung per scored chip; no scan summary
surfaced the mix until now. The re-summarizer streams a scan-state corpus (one
JSON in memory at a time, so 15k+ files summarize in ~1.5 s) and reports the
achieved-zoom histogram per-dir and combined, with a `decision_source` breakdown:

```bash
python -m scripts.temporal.summarize_scan_zoom \
  --scan-states-dir ~/zasolar_data/geid_temporal/jhb_full382_fpcut_scan_2026-06-02/scan_states \
  --scan-states-dir ~/zasolar_data/geid_temporal/jhb_full382_fpcut_wayback_2026-06-04/scan_states \
  --output /tmp/zoom_hist.json
```

It prefers `scan_state.load_scan_state` and falls back to raw-json traversal on a
spec-version `ValueError`, so a future `SPEC_VERSION` bump can never make an
existing production corpus unreadable.

### Real production histograms (measured 2026-07-03)

Re-summarizing the two banked JHB corpora reproduces the audit numbers exactly:

**`jhb_full382_fpcut_scan_2026-06-02`** (TM adaptive scan) — 15,859 files,
172,913 scored chips:

| achieved zoom | count | share |
| --- | ---: | ---: |
| z20 | 125,332 | 72.5% |
| z19 | 46,381 | 26.8% |
| z18 | 1,200 | 0.7% |

**`jhb_full382_fpcut_wayback_2026-06-04`** (Wayback census narrowing) — 1,641
files, 9,730 scored chips:

| achieved zoom | count | share (all) | share (served only) |
| --- | ---: | ---: | ---: |
| z19 | 6,002 | 61.7% | **97.0%** |
| z20 | 167 | 1.7% | 2.7% |
| z18 | 17 | 0.2% | 0.3% |
| unknown (all rungs failed) | 3,544 | 36.4% | — |

The audit's headline "97% z19" for Wayback is z19 as a share of chips that
received *any* served zoom (6,002 / 6,186). The re-summarizer additionally
surfaces the 36.4% `unknown` (all-zooms-failed) rows the headline excluded — a
strictly more complete picture. The TM corpus has no such `unknown` mass.

## Sentinel effective-resolution estimator

`actual_zoom` cannot see channel 1 (in-chip substitution): a chip can succeed at
z20 yet carry z18 pixels. The estimator (`scripts/temporal/effective_resolution.py`)
recovers the *effective* rung from the pixels and flags chips delivered ≥1 rung
coarser than nominal.

- **Metric — normalized gradient energy**, `mean(|∇I|²) / (Var(I)+ε)` on a
  central crop. Nearest-neighbour tile substitution replicates one coarse pixel
  into an f×f block of identical values, so every gradient *interior* to a block
  is exactly zero and 1-px gradient energy collapses in proportion to the
  substitution factor — a direct, content-agnostic probe of the finest scale at
  which the image actually varies. Dividing by variance removes global-contrast
  dependence. Chosen over a fixed FFT high-frequency band because NN upsampling
  injects spectral block-replicas that muddy such a band, whereas the
  interior-zero property is exact regardless of content. Absolute calibration is
  not claimed — hence the self-gate.
- **Self-gate (fail-closed, mandatory).** A sentinel manifest of KNOWN-zoom chips
  (z18/z19/z20) is scored first. The estimator may flag *anything* only if those
  sets separate: per-zoom medians strictly increase with zoom **and** every
  adjacent-pair AUC = P(metric_finer > metric_coarser) clears `--auc-threshold`
  (default 0.75), with each group at ≥ `--min-group` chips. Any shortfall →
  structured gate report, **zero flags, nonzero exit**.
- **Flagging.** A candidate's metric is mapped to an effective rung via
  geometric-mean boundaries between adjacent sentinel medians; a chip whose
  effective rung is ≥1 coarser than its nominal (`achieved_zoom`) rung is flagged.
  Output is a flags CSV plus the gate-report JSON.

```bash
python -m scripts.temporal.effective_resolution \
  --sentinel-manifest sentinels.csv \        # chip_path, known_zoom
  --candidates provenance_rows.jsonl \        # chip_path, achieved_zoom
  --output flags.csv --gate-report gate.json
```

Note: the banked JHB chip pixels were reclaimed under the WSL disk-hygiene policy,
so a real-sample self-gate requires re-downloading a small known-zoom set via
GEHI first. The estimator is validated on synthetic degradation in
`tests/temporal/test_effective_resolution.py`.

## Normative rule for D2 — stratify on ACHIEVED zoom

**D2's zoom-stratified emission matrices MUST key on achieved zoom
(`RoundResult.actual_zoom` / provenance `achieved_zoom`), never the requested
ladder rung.** Rationale: the JHB census *requested* z20 first on essentially
every chip, but only 72.5% *achieved* z20 (26.8% fell back to z19, 0.7% to z18).
Stratifying by requested rung would file ~27% of chips under a resolution they
were never delivered at, contaminating every per-zoom emission estimate. Where
in-chip substitution is a concern, the effective rung from the sentinel estimator
refines the achieved rung further — but the requested ladder is never the
stratification key.
