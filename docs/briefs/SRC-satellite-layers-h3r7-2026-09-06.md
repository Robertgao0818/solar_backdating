# SRC — satellite layers (Google Earth + Wayback) at H3-7

Date: 2026-09-06 · Method: **reuse existing catalogs, no live GEHI**.
Grain: Uber H3 resolution 7 (~4.9 km²/cell at Cape Town).
Companion CSVs: [`_muni_catalog/satellite_layers.csv`](_muni_catalog/satellite_layers.csv)
(city × provider × zoom, aligned to the aerial-layer schema),
[`_muni_catalog/satellite_city_provider_zoom_summary.csv`](_muni_catalog/satellite_city_provider_zoom_summary.csv),
[`_muni_catalog/satellite_h3_r7_provider_zoom.csv`](_muni_catalog/satellite_h3_r7_provider_zoom.csv),
[`_muni_catalog/satellite_year_histogram.csv`](_muni_catalog/satellite_year_histogram.csv).
HTML briefing (both halves):
[`BRIEF-municipal-true-date-panel-2026-09-06.html`](BRIEF-municipal-true-date-panel-2026-09-06.html).
Full copy + `SUMMARY.json`:
`~/zasolar_data/geid_temporal/satellite_h3r7_gehi_2026-09-06/`.

Municipal aerials (8–15 cm true-date) stay in
[`SRC-municipal-layers-2026-09-06.md`](SRC-municipal-layers-2026-09-06.md).
This file is the **satellite** half: Google Earth historical (GEHI `TM`) and
Esri Wayback (GEHI `Wayback`).

**GSD** (ground sample distance，地面采样距离) below is the repo convention
at South-African latitudes, not a per-chip measurement:
z20 ≈ 0.12 m · z19 ≈ 0.25 m · z18 ≈ 0.50 m.

---

## 1. What was queried vs what exists

| provider | what it is | how we catalog it | z20 | z19 | z18 |
|---|---|---|---|---|---|
| **TM** | Google Earth Time Machine, via `GEHistoricalImagery --provider TM` | Cape Town: bbox-**complete** `availability` on the chip box. Johannesburg: run3 vintage list (no zoom column) | **not in the citywide catalog** (optional download upgrade after z19 is complete) | production default | whole-picture fallback |
| **Wayback** | Esri World Imagery Wayback, via `--provider Wayback` | Cape Town: `info` at the centroid (not bbox-complete). Johannesburg: sqlite `info` z19 + run3 vintage list | essentially none (known) | production default | rare fallback |

Cape Town citywide already probed every one of 111,801 anchors
(13,845,832 candidate rows, 0 operational failure). Rolling that to H3-7 is
the fast path. A new z20 citywide probe would be a live GEHI campaign, not
this file.

---

## 2. City × provider × zoom

`median_dates` = median across H3-7 cells of (median dates per anchor in that
cell). Coverage = H3-7 cells that have at least one date at that zoom.

| city | provider | zoom | GSD | H3-7 cells | anchors | date rows | median dates / anchor | span |
|---|---|---|---|---:|---:|---:|---:|---|
| Cape Town | Google Earth TM | **19** | 0.25 m | **381 / 381** | 111,629 | 12,802,614 | **107** | 2009-02-12 … 2025-12-30 |
| Cape Town | Google Earth TM | 18 | 0.50 m | 321 / 381 | 30,756 | 61,841 | 1 | same span; fallback only |
| Cape Town | Wayback | **19** | 0.25 m | **381 / 381** | 111,585 | 970,180 | **9** | 2012-04-20 … 2025-10-12 |
| Cape Town | Wayback | 18 | 0.50 m | 271 / 381 | 8,569 | 11,197 | 1 | fallback |
| Johannesburg | Google Earth TM | zoom not stored (ladder z20→z19→z18) | 假设 mostly 0.25 m | 83 | 41,393 | 909,056 | 16 | **2019-01-30 … 2025-05-30** (census window, not full GEHI history) |
| Johannesburg | Wayback | 19 | 0.25 m | 83 | 15,859 chip-groups | 221,801 | 14 | 2012-03-03 … 2026-02-02 |
| Johannesburg | Wayback | zoom not stored | — | 83 | 41,393 | 289,190 | 7 | 2019+ subset of the run3 merge |

Johannesburg’s 83 H3-7 cells are the same count as the Vexcel
`za-gp-johannesburg-2024` coverage file. Cape Town’s 381 cells are the
census-polygon cells from the 2026-09-02 descriptive pack (join key `h3_r7`).

**z20 is absent from both citywide catalogs.** Policy remains: confirm the
vintage is complete at z19, then optionally pull z20 as a review upgrade.
Do not read “no z20 row” as “Google Earth has no 12 cm”. It means we did not
bbox-complete-catalog it.

---

## 3. Cape Town year mix (z19 date-rows)

Google Earth is dense every year 2009–2025. Wayback is a thin complement
(about 9 dates/anchor): it fills some 2022–2025 holes and is almost empty in
2018/2020/2021.

| year | Google Earth TM z19 | Wayback z19 |
|---:|---:|---:|
| 2009 | 124,950 | 0 |
| 2010 | 114,485 | 0 |
| 2011 | 222,802 | 0 |
| 2012 | 321,469 | 19,908 |
| 2013 | 433,813 | 61,579 |
| 2014 | 1,061,438 | 0 |
| 2015 | 1,229,667 | 102,755 |
| 2016 | 1,235,146 | 113,566 |
| 2017 | 1,285,596 | 116,824 |
| 2018 | 1,141,317 | 455 |
| 2019 | 1,302,341 | 111,564 |
| 2020 | 1,315,176 | 12 |
| 2021 | 1,088,126 | 7 |
| 2022 | 1,419,572 | 173,767 |
| 2023 | 118,838 | 101,733 |
| 2024 | 209,571 | 59,088 |
| 2025 | 178,307 | 108,922 |

**What this says.** At H3-7, every Cape Town census cell already has a Google
Earth z19 stack (~100 complete chip-dates per roof) plus a short Wayback
list. Wayback is not a second 100-date stack; it is the sparse extra sensor
that sometimes has 2022–2025 when TM is thin.

**Why it matters.** A satellite-year panel at H3-7 is already buildable from
the catalog we have. Adding z20 would change GSD from 25 cm to 12 cm on
*some* dates, not the set of years. Municipal 8 cm (previous SRC) is still
sharper than both.

**If ignored.** One might (a) launch a new z20 citywide catalog to “get
resolution”, or (b) treat Wayback as a dense peer of TM. (a) is expensive
and does not add years; (b) overstates 2018/2020/2021 Wayback.

---

## 4. Johannesburg caveats

- TM run3 vintages are **clipped to the 2019+ census window**. They are not
  the 2009–2018 Google Earth history. Wayback sqlite `info` *does* go back
  to 2012.
- Grain differs: TM vintage CSV is 41,393 **targets**; Wayback sqlite is
  15,859 **chip-groups**. Do not divide the two date counts as if they were
  the same unit.
- Zoom for TM is not in the CSV. Achieved-zoom audits (ISSUE-18) say TM
  mixes z20/z19 and Wayback is almost all z19.

---

## 5. Other metros

No GEHI catalog was rolled to H3-7. Vexcel is **not** only the seven
non-CT collections in `vexcel_sa_h3_r7_coverage_2026-08-12` (806 cells).
A 2026-09-06 live API pass found five Western Cape 2024 tiles as well
(Cape Town union **367 / 603** metro H3-7 cells, **278 / 381** census
cells). That is still **one vintage of ~6.2–6.7 cm aerial**, not a
satellite time stack. Full municipality table:
[`SRC-vexcel-layers-h3r7-2026-09-06.md`](SRC-vexcel-layers-h3r7-2026-09-06.md).

**Live GEHI `info` on all Vexcel H3-7 cells: done 2026-09-13.** Owner
constraint was aerial-covered first, not the cadastral metro. The run
probed every unique Vexcel cell including CT/JHB (1,148 centroids ×
{TM, Wayback} × z19 = 2,296 queries, 0 failures). Independent write-up
and CSVs:
[`SRC-satellite-vexcel-h3r7-gehi-info-2026-09-13.md`](SRC-satellite-vexcel-h3r7-gehi-info-2026-09-13.md).
This 2026-09-06 file remains the Cape Town / Johannesburg *reuse*
catalog (chip-box / run3), not the centroid reconnaissance.

---

## 6. How this sits next to municipal aerials

| layer | GSD | time grain | H3-7 coverage we actually have |
|---|---|---|---|
| CoCT municipal aerial | 0.05–0.08 m | year / season, true flight | not rolled here (see municipal SRC) |
| CoJ municipal aerial | 0.15 m | year | not rolled here |
| Vexcel urban ortho | 0.062–0.067 m | one season per city | CT 367/603 metro, 278/381 census; JHB 74/350 (see Vexcel SRC) |
| Google Earth TM z19 | 0.25 m | many dates/year, bbox-complete at CT | CT 381 cells; JHB 83 cells (2019+) |
| Wayback z19 | 0.25 m | ~9–14 dates/location | CT 381; JHB 83 |
| NGI digital aerial | 0.25–0.50 m | 3–5 year national cycle | not GEHI |
| GEHI z20 | 0.12 m | same dates as z19 when complete | **not catalogued citywide** |

Satellite layers do **not** replace the municipal 8 cm year-panel idea.
They are the dense-but-coarser time axis we already scored with Gemini.
The H3-7 join key is the same `h3_index` / `h3_r7` used on the 2026-09-02
Cape Town choropleth.
