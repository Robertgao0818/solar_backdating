# SRC — Vexcel ortho layers by municipality at H3-7

Date: 2026-09-06 · Method: **live Vexcel API v2**, metadata plus 120 m GSD
extracts. Grain: Uber H3 resolution 7 (same 1% overlap rule as
`vexcel_sa_h3_r7_coverage_2026-08-12`).
Companion CSVs: [`_muni_catalog/vexcel_muni_city_summary.csv`](_muni_catalog/vexcel_muni_city_summary.csv),
[`_muni_catalog/vexcel_muni_collections.csv`](_muni_catalog/vexcel_muni_collections.csv),
[`_muni_catalog/vexcel_muni_h3_r7.csv`](_muni_catalog/vexcel_muni_h3_r7.csv),
[`_muni_catalog/vexcel_vs_urban_area.csv`](_muni_catalog/vexcel_vs_urban_area.csv).
Vexcel is in the **aerial** half of the two-set catalog (not satellite):
[`_muni_catalog/aerial_layers.csv`](_muni_catalog/aerial_layers.csv)
(`family=vexcel`) and
[`_muni_catalog/aerial_city_summary.csv`](_muni_catalog/aerial_city_summary.csv).
HTML briefing:
[`BRIEF-municipal-true-date-panel-2026-09-06.html`](BRIEF-municipal-true-date-panel-2026-09-06.html).
Full copy: `~/zasolar_data/geid_temporal/vexcel_muni_h3r7_2026-09-06/`.

Municipal true-date aerials:
[`SRC-municipal-layers-2026-09-06.md`](SRC-municipal-layers-2026-09-06.md).
Satellite half (Google Earth TM + Wayback):
[`SRC-satellite-layers-h3r7-2026-09-06.md`](SRC-satellite-layers-h3r7-2026-09-06.md).

**GSD** (ground sample distance，地面采样距离) below is true ground metres:
a 120 m `layer=urban` extract in EPSG:3857 has a 7.46 cm Web-Mercator
pixel; multiply by `cos(latitude)` to get metres on the ground. Vendor
`avg-gsd` from the April 2026 YAML, where it exists, is 0.3–1.0 cm
coarser; the extract number is what a chip actually has.

**H3-7 coverage rate** = (cells with ≥1% of their area in the Vexcel
footprint **and** ≥1% in the municipality polygon) / (cells with ≥1% in
the municipality). Polygons are geoBoundaries gbOpen ZAF ADM2 (metros)
and ADM3 (Msunduzi). Equal-area CRS: EPSG:6933.

This is **one aerial vintage per collection**, not a time stack.

---

## 1. Headline

On this LSE academic token, Vexcel covers **12 urban ortho collections**
in South Africa: the seven already in
`vexcel_sa_h3_r7_coverage_2026-08-12` (806 H3-7 cells) **plus five
Western Cape 2024 collections the August catalog missed** (420 unique
cells). Union across all twelve: **1,226** unique H3-7 cells.

Cape Town is **not empty**. Five named 2024 tiles cover **367 / 603**
metro H3-7 cells (**60.9%**) and **278 / 381** of the census-polygon
cells used in the satellite SRC (**73.0%**). They are spatial partitions
of one March–April 2024 season at **~6.2 cm**, not five years.

Johannesburg Vexcel is the inner-city 2024 footprint: **74 / 350** CoJ
cells (**21.1%**). Sandton, Soweto, Randburg, Roodepoort, and Midrand
probe points returned HTTP 404. 23 cells spill into Ekurhuleni (no
Ekurhuleni collection of its own).

**What this says.** Vexcel on this token is a **6.2–6.7 cm snapshot of
part of the built city**, not of the municipality. Against GHSL 2020
Urban Centre (联合国 Degree of Urbanisation 的“城区”：连续 1 km 格、
≥1,500 人/km²、合计 ≥50,000 人) Cape Town is **93%**, NMB **100%**,
Msunduzi **94%**, eThekwini **79%**, Mangaung **72%** — and Johannesburg
is still only **27%**. CoJ is already 68% urban-centre land; the 21%
metro figure is not a rural-hinterland artefact.

**Why it matters.** A year-panel still has to come from municipal
aerials (previous SRC) or GEHI (satellite SRC). Vexcel is the sharp
*present-day* census sensor **where the urban centre is covered**.
Treating the five Cape Town collection names as five years, treating
the August 7-city file as “no Cape Town”, or reading JHB 21% as “the
metro is just big and rural”, would all be wrong.

**If ignored.** One might (a) skip a 6.2 cm 2024 layer that already
overlaps 73% of the Cape Town census H3-7 cells, (b) plan a Vexcel
time series that this token does not have, or (c) put Johannesburg
and Cape Town in the same “urban-core coverage” bucket.

---

## 2. Municipality coverage (union of collections)

| municipality | collections | capture | GSD | H3-7 covered / metro | H3-7 rate | area in metro | satellite H3-7 overlap |
|---|---|---|---|---:|---:|---:|---|
| **Cape Town** | 5 × `za-wc-*-2024` | 2024-03-18 … 2024-04-12 | **6.2 cm** | **367 / 603** | **60.9%** | 1,448 / 2,446 km² | **278 / 381** census cells (73.0%) |
| Johannesburg | `za-gp-johannesburg-2024` | 2024-02-17 … 2024-04-19 | 6.7 cm | 74 / 350 | 21.1% | 305 / 1,645 km² | 74 / 83 of the Vexcel-footprint cells in the satellite SRC |
| eThekwini | `za-kzn-durban-2026` | 2025-12-17 … 2026-01-06 | 6.5 cm | 166 / 532 | 31.2% | 735 / 2,556 km² | no GEHI catalog |
| Nelson Mandela Bay | `za-ec-portelizabeth-2026` | 2026-01-11 … 2026-01-21 | 6.2 cm | 179 / 443 | 40.4% | 747 / 1,957 km² | no GEHI catalog |
| Msunduzi | `za-kzn-pietermaritzburg-2025` | 2025-12-16 | 6.5 cm | 62 / 178 | 34.8% | 249 / 751 km² | no GEHI catalog |
| Tshwane | `za-gp-pretoria-2026` | 2026-02-05 … 2026-02-08 | 6.7 cm | 147 / 1,228 | 11.97% | 718 / 6,298 km² | no GEHI catalog |
| Buffalo City | `za-ec-eastlondon-2026` | 2026-01-07 … 2026-01-08 | 6.3 cm | 60 / 622 | 9.6% | 232 / 2,750 km² | no GEHI catalog |
| Ekurhuleni | *spill of JHB 2024 only* | 2024-02-17 … 2024-04-19 | 6.7 cm | 23 / 400 | 5.8% | 59 / 1,975 km² | no own collection; six probe points 404 |
| Mangaung | `za-fs-bloemfontein-2026` | 2026-01-18 | 6.5 cm | 84 / 1,978 | 4.2% | 359 / 9,886 km² | Botshabelo 404 |

### 2.1 Same numbers against GHSL urban area (2020)

Metro percentages in §2 use the **whole municipality** as denominator.
Mangaung is 9,886 km² of Free State hinterland; Tshwane is 6,298 km².
Those denominators make a city-sized Vexcel footprint look like 4–12%.
The table below clips GHSL GHS-SMOD 2020 (1 km, World Mollweide) to each
municipality.

- **Urban Centre** = SMOD class 30. UN Degree of Urbanisation *city*
  (城区).
- **Urban domain** = classes 21 + 22 + 23 + 30. Suburban / peri-urban +
  towns + city (建成区+城郊).
- Area rate = `area(Vexcel ∩ class ∩ municipality) / area(class ∩ municipality)`.
- H3-7 rate uses the same ≥1% cell rule as §2, restricted to cells that
  also have ≥1% of that urban class.

CSV: [`_muni_catalog/vexcel_vs_urban_area.csv`](_muni_catalog/vexcel_vs_urban_area.csv).

| municipality | 城区占市域 | Vexcel / 城区 (area) | Vexcel / 城区 (H3-7) | Vexcel / 建成区+城郊 (area) | note |
|---|---:|---:|---:|---:|---|
| **Cape Town** | 31% (768 / 2,446 km²) | **93.3%** (717 / 768) | **90.8%** (197 / 217) | 90.4% | Vexcel is larger than the 2020 urban centre; leftover is mostly False Bay / agricultural fringe inside the metro, not missing suburbs of the city |
| **NMB** | 15% (294 / 1,957) | **100%** | **100%** (98 / 98) | **100%** | footprint overshoots the 2020 city; Gqeberha urban centre is fully inside the 2026 collection |
| **Msunduzi** | 21% (154 / 751) | **93.9%** | **93.3%** (42 / 45) | 54.0% | Pietermaritzburg city yes; Edendale-side peri-urban (domain 352 km²) only half |
| **eThekwini** | 33% (840 / 2,556) | **79.2%** | **74.6%** (156 / 209) | 53.4% | Durban city mostly yes; the extra 471 km² of domain (townships / peri-urban) is the gap |
| **Mangaung** | **2.0%** (200 / 9,886) | **71.6%** | 64.4% (38 / 59) | 56.4% | Bloemfontein city is covered; Botshabelo is not. The 4.2% metro rate was hinterland |
| **Buffalo City** | **4.2%** (114 / 2,750) | **67.3%** | 70.0% (28 / 40) | 37.4% | East London city mostly; Mdantsane sits in the domain and was a 404 |
| **Tshwane** | 15% (951 / 6,298) | **48.5%** | 44.6% (108 / 242) | 45.0% | Pretoria / Hatfield / Mamelodi yes; Centurion and Akasia 404. Half the urban centre is missing, not just the rural metro |
| **Johannesburg** | **68%** (1,119 / 1,645) | **26.6%** | **27.3%** (72 / 264) | 24.4% | CoJ is already a city, not a farm. Vexcel is an inner-city patch (Sandton / Soweto / Randburg 404). Hinterland cannot explain this |
| **Ekurhuleni** | 40% (789 / 1,975) | **7.4%** | 11.8% (23 / 195) | 5.4% | no own collection; 59 km² is JHB spill on the western edge |

**What this says.** Split the nine municipalities into two groups.
Cape Town, NMB, Msunduzi, eThekwini, Mangaung, Buffalo: Vexcel is a
**city-shaped** product (two-thirds to all of the 2020 urban centre).
Johannesburg and Ekurhuleni: Vexcel is a **CBD-shaped** product on a
municipality that is already urban. Tshwane sits in between (half the
urban centre).

**Why it matters.** Rooftop PV is on urban and suburban roofs. The
denominator that matches the research object is Urban Centre / urban
domain, not the cadastral metro. Under that denominator Cape Town is
ready as a Vexcel census geography; Johannesburg is not — the named
halls in Sandton and the Soweto/south-west fabric are outside the 2024
collection.

**If ignored.** One would (a) discard Mangaung / Buffalo as “4–10%
coverage” when they actually cover most of the city, or (b) treat
Johannesburg’s 21% like Mangaung’s 4% — same number, opposite geometry.

Live product-type on `/ortho/collections` is **`final-ortho`** for all
twelve, including Durban and Pretoria (the April YAML still says
`quick-ortho` for those two; trust the live field). Layer is **`urban`**.
`/ortho/dates` returns HTTP 400 unless `layer=urban` is set.

---

## 3. Cape Town collections (five 2024 tiles, not five years)

| collection | where a probe hit | capture (hit-point min/max) | GSD | H3-7 in metro | of 381 census cells |
|---|---|---|---|---:|---:|
| `za-wc-bellville-2024` | Bellville, Khayelitsha, Table View | 2024-03-27 … 2024-03-30 | 6.2 cm | 207 / 603 (34.3%) | **196** |
| `za-wc-tablemountain-2024` | CBD, Simon's Town | 2024-03-18 … 2024-03-20 | 6.2 cm | 101 / 603 (16.7%) | 85 |
| `za-wc-rondebosch-2024` | Table View (also CBD `/ortho/dates`) | 2024-04-12 | 6.2 cm | 88 / 603 (14.6%) | 74 |
| `za-wc-avondale-2024` | Atlantis | 2024-04-11 | 6.2 cm | 61 / 603 (10.1%) | **0** (Atlantis is outside the census-polygon cells) |
| `za-wc-capetown-2024` | CBD | 2024-04-12 | 6.2 cm | 18 / 603 (3.0%) | 17 |
| **union** | | 2024-03-18 … 2024-04-12 | 6.2 cm | **367 / 603 (60.9%)** | **278 / 381 (73.0%)** |

The five footprints overlap (sum of per-collection cells 475, union
367). Bellville is the big eastern tile; Table Mountain / Rondebosch /
Cape Town cover the peninsula; Avondale is the Atlantis patch.

Cape Town Vexcel dates are **hit-point samples**, not a full-footprint
`/ortho/dates` grid. The collection window could be a few days wider.
They are still all March–April 2024.

CoCT municipal 2025Jan (5 cm, metro-wide) remains the census layer.
Vexcel 2024 is a second 6.2 cm look at 61% of the metro, useful as an
independent 2024 observation, not as a replacement year stack.

---

## 4. Other collections (one vintage each)

| municipality | collection | capture window | window source | GSD (extract) | vendor avg-gsd |
|---|---|---|---|---|---|
| Johannesburg | `za-gp-johannesburg-2024` | 2024-02-17 … 2024-04-19 | YAML (grid-sampled 2026-06-04); CBD `/ortho/dates` 2024-02-21 sits inside | 6.7 cm | 7.03 cm |
| Tshwane | `za-gp-pretoria-2026` | 2026-02-05 … 2026-02-08 | YAML; Hatfield/CBD/Mamelodi hit, Centurion/Akasia 404 | 6.7 cm | 7.2 cm |
| eThekwini | `za-kzn-durban-2026` | 2025-12-17 … 2026-01-06 | YAML; all five probe points hit | 6.5 cm | 6.9 cm |
| NMB | `za-ec-portelizabeth-2026` | 2026-01-11 … 2026-01-21 | YAML; CBD `/ortho/dates` 2026-01-13 | 6.2 cm | 7.2 cm |
| Buffalo City | `za-ec-eastlondon-2026` | 2026-01-07 … 2026-01-08 | YAML; Mdantsane and King William's Town 404 | 6.3 cm | 7.1 cm |
| Mangaung | `za-fs-bloemfontein-2026` | 2026-01-18 | YAML; Botshabelo 404 | 6.5 cm | 7.4 cm |
| Msunduzi | `za-kzn-pietermaritzburg-2025` | 2025-12-16 | YAML; CBD, Edendale, Northdale all hit | 6.5 cm | 6.9 cm |

Johannesburg 83 collection cells = the same count the satellite SRC used
as its JHB universe. 74 of those 83 have ≥1% in CoJ; 23 have ≥1% in
Ekurhuleni; 14 sit on the municipal boundary (counted in both). Do not
read 74 + 23 = 97 as a new collection.

---

## 5. How this sits next to the other two catalogs

| layer | GSD | time grain | H3-7 coverage we actually have |
|---|---|---|---|
| CoCT municipal aerial | 0.05–0.08 m | year / season, true flight | not rolled to H3-7 (municipal SRC) |
| CoJ municipal aerial | 0.15 m | year | not rolled to H3-7 |
| **Vexcel urban ortho (this file)** | **0.062–0.067 m** | **one season per city** | CT 367/603 metro, 278/381 census; JHB 74/350; others in §2 |
| Google Earth TM z19 | 0.25 m | many dates/year | CT 381/381; JHB 83 Vexcel-footprint cells |
| Wayback z19 | 0.25 m | ~9–14 dates | CT 381; JHB 83 |
| NGI digital aerial | 0.25–0.50 m | 3–5 year national cycle | not this API |

Vexcel does **not** replace the municipal 8–15 cm year-panel idea. It is
sharper than GEHI z19 and CoJ 15 cm, in the same class as CoCT 8 cm, and
it has **no second year** on this token. Use it as the high-resolution
census / 2024–2026 present, not as the time axis.

---

## 6. Method (live 2026-09-06)

1. Mint a JWT from ZAsolar `.env` (`VEXCEL_USER` / `VEXCEL_PASSWORD`).
   Token not stored in this repo.
2. `GET /v2/ortho/collections?wkt=POINT(lon lat)` at 4–6 points per
   municipality, with and without `layer=urban`. Same 12 collections
   either way; `/ortho/dates` needs `layer=urban`.
3. Fetch each collection footprint (`/ortho/collections?collection=`).
   Properties returned only `collection` and `product-type` — no GSD,
   no dates, no area.
4. Collection windows: YAML 2026-04-26 (itself from this API) for the
   seven known cities, confirmed by a live `/ortho/dates` point sitting
   inside the window. Cape Town windows = min/max of live `/ortho/dates`
   at hit points.
5. GSD: 120 m `layer=urban` GeoTIFF extract, read the EPSG:3857 pixel,
   multiply by `cos(lat)`. Seven extracts, all 7.46 cm Web-Mercator.
6. Municipality polygons: geoBoundaries gbOpen ZAF ADM2 / ADM3.
7. H3-7: polyfill + 1-ring, keep cells with ≥1% equal-area overlap
   (EPSG:6933). Same rule as the 2026-08-12 catalog.
8. Urban area: GHS-SMOD R2023A epoch 2020, 1 km, ESRI:54009. Class 30 =
   Urban Centre; 21–23+30 = urban domain. Clip to each municipality,
   then intersect Vexcel. Pixel is 1 km² (equal-area Mollweide).

No bulk imagery was pulled beyond the seven 120 m GSD chips.

---

## 7. Traps

- The 2026-08-12 file is **not** “all SA Vexcel”. It only fetched the
  seven collections listed in `vexcel_urban_coverage.yaml`. Cape Town
  lives under `za-wc-*`, which that YAML never named.
- Five Cape Town collection ids are **five spatial tiles of 2024**, not
  five survey years.
- A 404 at Sandton / Centurion / Germiston is “not this urban
  collection”, not “Vexcel has no South Africa product”.
- Ekurhuleni’s 23 cells are the Johannesburg footprint crossing the
  municipal line, not an Ekurhuleni survey.
- `/ortho/dates` without `layer=urban` is HTTP 400. Do not read that as
  missing dates.
- YAML `quick-ortho` for Durban / Pretoria is stale; live
  `product-type` is `final-ortho`.
- Coverage rates in §2 use the **full municipality** as denominator.
  Tshwane 12% and Mangaung 4% look small because the metros include
  large rural hinterlands. Against GHSL Urban Centre those two become
  48% and 72%. Johannesburg does **not** get the same reprieve: CoJ is
  68% urban-centre land and Vexcel still only covers 27% of it.
- GHSL epoch is **2020**. Vexcel is 2024–2026. Urban Centre area is a
  lower bound on today’s built city; NMB/CT footprints larger than the
  2020 city include some post-2020 expansion, not only “rural”. This
  does not repair Johannesburg: the missing Sandton/Soweto fabric was
  already urban in 2020.
- Licence is the shared LSE academic account. Chips are not
  redistributable.
