# SRC — GEHI satellite coverage at Vexcel H3-7 centroids

Date: 2026-09-13 · Method: **live `gehi_info` at H3-7 centroids**, TM +
Wayback, z19. Sample: every unique Vexcel-covered H3-7 cell in
[`_muni_catalog/vexcel_muni_h3_r7.csv`](_muni_catalog/vexcel_muni_h3_r7.csv)
(union of 12 urban ortho collections). **Independent of** the 2026-09-06
Cape Town / Johannesburg reuse catalog.

Companion CSVs:
[`_muni_catalog/satellite_vexcel_h3r7_cell.csv`](_muni_catalog/satellite_vexcel_h3r7_cell.csv)
· [`_muni_catalog/satellite_vexcel_h3r7_cell_provider.csv`](_muni_catalog/satellite_vexcel_h3r7_cell_provider.csv)
· [`_muni_catalog/satellite_vexcel_h3r7_city_provider.csv`](_muni_catalog/satellite_vexcel_h3r7_city_provider.csv)
· [`_muni_catalog/satellite_vexcel_h3r7_year_histogram.csv`](_muni_catalog/satellite_vexcel_h3r7_year_histogram.csv)
· [`_muni_catalog/satellite_vexcel_h3r7_layers.csv`](_muni_catalog/satellite_vexcel_h3r7_layers.csv).
RUN: [`../replan_v2/RUN-satellite-vexcel-h3r7-gehi-info-2026-09-13.md`](../replan_v2/RUN-satellite-vexcel-h3r7-gehi-info-2026-09-13.md).
Full copy: `~/zasolar_data/geid_temporal/satellite_vexcel_h3r7_gehi_info_2026-09-13/`.

**GSD** (ground sample distance，地面采样距离) is the repo convention at
South-African latitudes: z19 ≈ 0.25 m. **Time node** = one distinct
`capture_date` after `(cell, capture_date)` dedupe. Wayback dates are
Esri *captured* dates, not layer release dates.

---

## 1. What this is, and what it is not

Each H3-7 cell (~4.9 km² at Cape Town) is represented by **one point**:
the cell centroid already stored on the Vexcel coverage CSV. `gehi_info`
lists every labeled vintage at that point. That is the cheap way to ask
"does this city even have a satellite time axis, and how many years?"

It is **not**:

- bbox-complete `availability` over the whole hex (a date at the centroid
  can miss the rest of the cell, and a date covering most roofs can miss
  the centroid);
- z20 / z18;
- a per-roof catalog (Cape Town's 111,801-anchor citywide file remains
  the production stack for that city);
- a chip download.

Cape Town numbers here will **not** match the 2026-09-06 reuse catalog
(median 107 bbox-complete chip-dates / anchor). Centroid `info` and
chip-box `availability --complete` are different queries. Use this file
to compare cities; use the reuse catalog to date Cape Town roofs.

| | |
|---|---|
| Unique H3-7 cells | **1,148** |
| Queries (TM + Wayback × z19) | **2,296** |
| Successful (empty catalogs included) | **2,296 / 2,296** |
| Failures (timeout / 403 / error) | **0** |
| Empty catalogs | **1** (Msunduzi Wayback, cell `87bcf5b22ffffff`; TM on that cell still has 40 dates) |
| Wall clock | 2026-09-13 06:43Z … 09:00Z (~2.3 h), 1 req/s limiter |

---

## 2. City × provider (z19 info at centroid)

`median_dates` includes empty cells as zero. Coverage denominator is
Vexcel-covered H3-7, not the cadastral metro. Union = distinct dates
across TM and Wayback on the same centroid.

| city | TM median | TM years | TM span | Wayback median | Wayback years | union median |
|---|---:|---:|---|---:|---:|---:|
| **Cape Town** (367) | **113** | 18 | 2009-02-12 … 2026-08-11 | 9 | 13 | **122** |
| Johannesburg (74) | **90** | 18 | 2009-03-12 … 2026-07-28 | 13 | 13 | **102** |
| eThekwini (166) | **89.5** | 17 | 2009-04-09 … 2025-12-30 | 12 | 12 | **101** |
| Ekurhuleni (23, JHB spill) | **129** | 18 | 2009-03-12 … 2026-07-12 | 13 | 11 | **141** |
| NMB (179) | **91** | 19 | 2004-02-24 … 2026-01-22 | 10 | 10 | **95** |
| Buffalo City (60) | **107** | 17 | 2009-08-16 … 2026-01-05 | **2** | 3 | **109** |
| Tshwane (147) | **49** | 19 | 2005-02-08 … 2026-07-15 | 10 | 10 | **58** |
| Mangaung (84) | **51** | 17 | 2009-05-21 … 2025-02-23 | **1** | 3 | **52.5** |
| Msunduzi (62) | **45** | 16 | 2010-02-16 … 2025-10-30 | **2** | 3 | **47** |

Full nonempty / empty / fail columns:
[`_muni_catalog/satellite_vexcel_h3r7_city_provider.csv`](_muni_catalog/satellite_vexcel_h3r7_city_provider.csv).

**What this says.** On every Vexcel-covered hex, Google Earth TM at z19
is a real time axis: 45–129 distinct dates, 16–19 calendar years, no
empty TM cell. Wayback is a thin extra sensor in the large metros
(median 9–13) and almost absent in Buffalo / Mangaung / Msunduzi
(median 1–2). Two density bands: **~90–130 TM dates** (CT, JHB inner
city, eThekwini, NMB, Buffalo, Ekurhuleni spill) vs **~45–51**
(Tshwane, Mangaung, Msunduzi). Union adds about 9 extra dates where
Wayback exists, and almost nothing where it does not.

**Why it matters.** A satellite *year* panel is already buildable on
the Vexcel footprint of all nine municipalities — every city has TM
in nearly every year 2010–2025. A satellite *day-interval* panel is
only in the dense band. Tshwane's municipal aerials are two epochs
(2018, 2023) sitting on a TM stack half as thick as Cape Town's; the
satellite side can still fill the six-year load-shedding gap, just
with fewer revisits.

**If ignored.** One might (a) treat Johannesburg's old run3 catalog
(median 16 dates, 2019+ census window) as "JHB has no 2009–2018
Google Earth" — this probe shows the inner-city Vexcel footprint has
median **90** TM dates back to 2009; (b) plan a Wayback year panel
for Buffalo / Mangaung / Msunduzi, where Wayback is 1–2 snapshots;
(c) equate these medians with Cape Town's 107 bbox-complete chip-dates.

---

## 3. Calibration against the 2026-09-06 reuse catalog

| | 2026-09-06 reuse (chip / target) | this probe (H3-7 centroid `info`) |
|---|---|---|
| Cape Town TM z19 | 381 census cells, median **107** bbox-complete dates/anchor, 2009–2025 | 367 Vexcel cells, median **113**, 2009–2026 |
| Cape Town Wayback z19 | median **9** | median **9** |
| Johannesburg TM | 83 cells, median **16**, **2019–2025 only** (census window) | 74 Vexcel cells, median **90**, **2009–2026** |
| Johannesburg Wayback z19 | median 14 (chip-groups) | median **13** |

Cape Town TM 113 vs 107 is the same order of magnitude on two different
queries and two slightly different footprints (Vexcel 367 vs census 381).
That is the calibration: centroid `info` is a fair city-to-city ruler,
not a replacement for the roof catalog.

Johannesburg is the correction. The 2019+ window was a property of the
run3 vintage CSV, not of Google Earth. Inside the 2024 Vexcel inner-city
patch, TM history is as long as Cape Town's, ~20% fewer dates per point.

---

Availability figures and the 1–2-frame year list (2026-09-14):
[`BRIEF-satellite-availability-2026-09-14.html`](BRIEF-satellite-availability-2026-09-14.html)
· [`FIG-satellite-year-heatmap-2026-09-14.html`](FIG-satellite-year-heatmap-2026-09-14.html)
· [`FIG-layer-availability-2026-09-14.html`](FIG-layer-availability-2026-09-14.html)
· H3-7 spatial
[`FIG-h3-spatial-coverage-2026-09-14.html`](FIG-h3-spatial-coverage-2026-09-14.html).
Classifier: [`../replan_v2/DATA-satellite-vexcel-h3r7-availability-2026-09-14.md`](../replan_v2/DATA-satellite-vexcel-h3r7-availability-2026-09-14.md).

## 4. Years that are thin, not missing

TM is present in almost every year 2009/2010–2025 in every city. The
holes worth naming are **thin years** (<50% of that city's Vexcel cells
have a date that year), not empty cities:

| city | TM years with <50% of Vexcel cells |
|---|---|
| Cape Town | none (2023 is sparse in *count* — 353 date-rows vs ~4,000 in 2019 — but still 327 / 367 cells) |
| Johannesburg / Ekurhuleni | **2023** (32 / 74 and 4 / 23) |
| eThekwini | 2009 (44 / 166) |
| NMB | 2004 (1 cell), 2012 (56 / 179), 2026 (38 / 179) |
| Buffalo | 2009, 2011, 2026 |
| Tshwane | 2005 (1 cell — do not advertise a 2005 citywide year) |
| Mangaung | 2011 |
| Msunduzi | none |

Wayback in Cape Town still has the known 2018 / 2020 / 2021 near-empty
years (34, 1, 2 cells). Buffalo / Mangaung / Msunduzi Wayback is only
three isolated years total.

**What this says.** 2023 is the shared thin TM year in Gauteng inner
city; Cape Town 2023 is thin in revisits, not in spatial coverage.
Single-cell 2004/2005 dates are noise, not a panel year.

**Why it matters.** A year-panel codebook should mark 2023 as "TM may
be missing on half of JHB Vexcel cells" and should not list Tshwane
2005 or NMB 2004 as municipal-scale observations.

**If ignored.** A 2023 present/absent bit in Johannesburg would be
defined on 32 of 74 hexes and silently missing on the rest.

---

## 5. How to read a time node

- TM: Google Earth Time Machine labeled date at z19.
- Wayback: Esri World Imagery Wayback *captured* date at z19.
- Union (cell CSV): distinct dates across both providers. Same calendar
  day from both sensors still counts once.
- Years: distinct calendar years with ≥1 date. A year with one date and
  a year with forty dates both count as one year-node for a year panel;
  `n_dates` is what you need for day-interval work.

Failed queries (`timeout` / `blocked` / `error`) are **not** empty
catalogs. This run had none. The one `ok_empty` is a real empty
Wayback list, not a failed call.

---

## 6. What this does not authorize

- No z20 campaign. Resolution is still ~25 cm; municipal 5–15 cm and
  Vexcel ~6.2 cm remain sharper.
- No whole-metro hinterland grid (Tshwane 1,228 / Mangaung 1,978).
  This sample is the Vexcel urban footprint only.
- No replacement of the Cape Town citywide Gemini table. This is
  metadata for "where else a satellite time axis exists."
- No V2 detector probe. That is still the unsigned CoCT 2018/2022/2025
  100-anchor experiment in
  [`BRIEF-municipal-true-date-panel-2026-09-06.md`](BRIEF-municipal-true-date-panel-2026-09-06.md).
