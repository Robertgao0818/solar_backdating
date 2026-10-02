# SRC — municipal imagery / GIS layer metadata for a rooftop-PV panel

Date: 2026-09-06 · Live-probed unless marked 假设
Scratch notes: [`_muni_catalog/`](_muni_catalog/)
（CT/JHB 影像 [`_muni_catalog/cape_town_jhb_imagery.md`](_muni_catalog/cape_town_jhb_imagery.md)；
eThekwini 全表含 847k 屋顶 [`_muni_catalog/kzn.md`](_muni_catalog/kzn.md)；
其余都市 [`_muni_catalog/other_metros.md`](_muni_catalog/other_metros.md)；
NGI/SSEG [`_muni_catalog/national.md`](_muni_catalog/national.md)；
Vexcel H3-7 [`_muni_catalog/vexcel_muni_city_summary.csv`](_muni_catalog/vexcel_muni_city_summary.csv)）
Collaborator CSVs (aerial half of the two-set catalog):
[`_muni_catalog/aerial_city_summary.csv`](_muni_catalog/aerial_city_summary.csv)
(one row per municipality, municipal years + Vexcel coverage),
[`_muni_catalog/aerial_layers.csv`](_muni_catalog/aerial_layers.csv)
(one row per layer, 110 rows: municipal + Vexcel + NGI + hosted-satellite traps).
HTML briefing:
[`BRIEF-municipal-true-date-panel-2026-09-06.html`](BRIEF-municipal-true-date-panel-2026-09-06.html).
Decision memo: [`BRIEF-municipal-true-date-panel-2026-09-06.md`](BRIEF-municipal-true-date-panel-2026-09-06.md)

**What this is.** A catalog of **public municipal and national aerial layers**
that could date rooftop PV by true flight year, instead of Gemini-on-GEHI.
It is not an accuracy claim and not a production switch.

Satellite half (Google Earth TM + Esri Wayback, H3-7, z18/z19; z20 not in
the citywide catalog):
[`SRC-satellite-layers-h3r7-2026-09-06.md`](SRC-satellite-layers-h3r7-2026-09-06.md).
Vexcel urban ortho (live API, H3-7 vs municipality polygons; Cape Town
**is** covered, five 2024 tiles):
[`SRC-vexcel-layers-h3r7-2026-09-06.md`](SRC-vexcel-layers-h3r7-2026-09-06.md).

**Why it exists.** Collaborator feedback: current dating precision is not
enough. CT-11 development evidence is 68% state agreement on a 100-anchor
repeat; recovery sequences hit 37.5% exact-interval agreement
([`../replan_v2/RUN-cape-town-ct11-human-calibration-2026-08-04.md`](../replan_v2/RUN-cape-town-ct11-human-calibration-2026-08-04.md)).
GEHI z19 is ~0.25 m. Several metros already publish 5–15 cm orthos with
named capture years.

**GSD** = ground sample distance, 一个像素对应地面几米.

---

## 1. City-level verdict

| municipality | public true-date aerial stack | years usable at rooftop GSD | GSD | coverage | access 2026-09-06 | panel construction? |
|---|---|---|---|---|---|---|
| **Cape Town** | yes, almost annual | 2012, 2013, 2015–2022, 2025, 2026 | 0.08 m (2012–22); **0.05 m** (2025–26) | metro; **2024 = ~30% only** | CoCT ERDAS MapServer / WMS, no login | **strongest** year-level panel |
| **Johannesburg** | yes, ~3-year cadence | 2009, 2012, 2015, 2019, 2023 (plus coarser 2000/03/06) | 0.15 m from 2009; 0.20–0.25 m earlier | metro | CoJ ImageServer `AerialPhotography/<year>` | strong; pipeline already fetches 2015/19/23 |
| **eThekwini** | yes, near-annual from 2016 | 2016, 2018, 2019, 2020, 2022 | 0.09–0.10 m | metro; 2023 west-only (misses CBD/coast); 2025 is a small 5 cm patch | `gis.durban.gov.za` REST (untrusted TLS) | strong; 847k photogrammetry buildings with `SYear` 2016/18/19 can seed chips. No ZAsolar census yet |
| **Ekurhuleni** | sparse | 2013, 2018, 2020 | 0.10 m | metro | `gis.ekurhuleni.gov.za/Imagery` | usable if we run a new census |
| **Tshwane** | yes (later REST host) | **2018, 2023** (2024/2025 names exist; 假设 not yet proven distinct flights) | **0.10 m** | metro | `e-gis001.tshwane.gov.za` ImageServer | two-epoch panel if 2024/25 chips differ |
| **NMB (Gqeberha)** | yes, via public resource-proxy | 1997, 2004, 2009, 2011, 2013, 2017, 2018, 2022 | 0.25 → 0.10 → **0.05 m** | urban/metro; 1997 smaller | `gis.nelsonmandelabay.gov.za/resource-proxy` → internal ImageServer | **usable year panel**; 35.6万 2017 LiDAR 屋顶 |
| **Buffalo / Mangaung / Msunduzi** | no live ImageServer | — | — | — | timeout / no host | Vexcel single vintage + NGI/GEHI only |
| **National NGI** | yes, sheet-dated | 2008–16 at 0.50 m; 2017+ at **0.25 m** | 0.25–0.50 m | national, 3–5 yr cycle | geoportal live | fallback, not better than GEHI z19 |

Traps (do not use as a panel year):

- CoCT **2023Jan** = NGI 2022 at 0.25 m, not municipal 8 cm.
- CoCT **2014_NGI** = mixed 2014+2016+2022 resampled to 0.25 m.
- CoCT **2024** = ~30% of metro.
- CoJ **CoJ_Aerial** = multi-year mosaic with `Imagery_Year`.
- eThekwini **20212022** = range, not one year. No public 2017 or citywide 2024.
- eThekwini **2023** = west only; CBD is east of that cut.
- Ekurhuleni **Orthophoto_2026** = viewer of NGI/SANSA/2019-20, not a 2026 flight.

---

## 2. Cape Town municipal aerials (live)

Endpoint: `https://cityimg.capetown.gov.za/erdas-iws/esri/GeoSpatial%20Datasets/rest/services`

| service | capture | GSD | coverage | license (service text) |
|---|---|---|---|---|
| Aerial Imagery 2026Jan | 2026-01 v2, not final | 0.05 m | metro | non-commercial unrestricted |
| Aerial Imagery 2025Jan | 2025-01 v3 | 0.05 m | metro | same — **current census layer** |
| Aerial Imagery 2024 | 2024 | UNKNOWN | ~30% metro | same |
| Aerial Imagery 2023Jan | NGI 2022 | 0.25 m | NGI extract | — |
| Aerial Imagery 2022Jan … 2015Feb, 2013Dec | month-named | 0.08 m | metro | same |
| Aerial Imagery 2012Dec | 2012-12 | 0.08 m | east truncated | same |
| Aerial Imagery 2009Nov | 2009-11 | 0.125 m | smaller box | same |
| Aerial Imagery 2008June / 2005Feb / 2000Apr | those dates | 0.20 / 0.25 / 0.25 m | 2000 is built-up patchwork | same |
| 1926–1998 | historic | various, often greyscale | partial | heritage |

CRS: EPSG:20482 (Lo19). Full table:
[`_muni_catalog/cape_town_jhb_imagery.md`](_muni_catalog/cape_town_jhb_imagery.md).

---

## 3. Johannesburg municipal aerials (live)

Endpoint: `https://ags.joburg.org.za/server/rest/services/AerialPhotography`

| year | GSD | CRS | already in our code? |
|---|---|---|---|
| 2000, 2003 | 0.25 m | Lo29 TM | no |
| 2006 | 0.20 m | Lo29 TM | no |
| 2009, 2012 | 0.15 m | Lo29 TM | no |
| 2015, 2019 | 0.15 m | Lo29 TM | yes (`coj_arcgis_fetch.py`) |
| 2023 | 0.15 m | **EPSG:3857** | yes |
| CoJ_Aerial | 0.15 m mosaic | Lo29 TM | no — not a year |

`Imagery_Year` is year-only. `Solar/2019` is 1 m radiation, not PV.
`SatelliteImagery/2025_2026` is 0.30 m satellite.

---

## 4. What this can and cannot build

**Can.** A **year-level presence panel**: for each census polygon, present /
absent / unusable on each municipal flight year, scored with the existing
census detector (in-domain GSD), decoded with the same conservative
year-bound rules as ISSUE-08. Cape Town would have **12** observations
(2012, 2013, 2015–2022, 2025, 2026); Johannesburg 5 at 15 cm; eThekwini 5 at 10 cm.

**Cannot.** Day-level install dates. Month-named CoCT mosaics are still
flight *seasons*, not per-roof capture days. SSEG registers cannot
substitute (address points, incomplete). NGI 0.25 m does not fix the
small-array problem that GEHI already has.

**Does not replace.** The 2025/2024 Vexcel *census* of where panels are
now. It only dates the ones we already have.

Cape Town **vectors** (live 2026-09-06, see
[`_muni_catalog/cape_town_vector.md`](_muni_catalog/cape_town_vector.md)):
photogrammetric roof footprints **287,328** with per-feature `ACQS_PRD`
(building-model capture YYYYMM, 201312…202501, no 2024 bin); 2023
deep-learning footprints **1.45M** (wall-to-wall, informal included);
cadastre ~835k–847k; **no public per-installation SSEG layer**. What *is*
on the Open Data Portal: city-wide SSEG kWh exports 2014–2023 (aggregate,
not roofs), SmartFacility solar on **839 council buildings**, and 66
building-plan rows whose free text happens to contain “solar” (geysers /
carports mixed in). Internal Energy Services applications (PTI /
commissioning) exist and have been mapped in GIS (Jefferies, Sep 2025) —
that register is login/PAIA, not a download. Building `ACQS_PRD` can bound
“this roof did not exist before YYYYMM”; it is not a PV install date.

---

## 5. Probe log

- CoCT ERDAS REST + five recent MapServer descriptions + 15 older
  descriptions: 200.
- CoJ AerialPhotography folder + 8 ImageServers + Solar/Satellite/Oblique: 200.
- eThekwini REST (untrusted TLS) + RasterCatalogue + AerialPhotography
  ImageServers: 200.
- Ekurhuleni Imagery folder + 12 ImageServers: 200.
- Tshwane REST: 503. NMB guessed REST: 404. Buffalo timeout. Mangaung
  NXDOMAIN. Msunduzi timeout. NGI geoportal: HTML 200.
