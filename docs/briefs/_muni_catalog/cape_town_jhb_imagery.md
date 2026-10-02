# Cape Town + Johannesburg municipal aerial metadata

Date: 2026-09-06. Live-probed unless marked 假设.
Purpose: true-date imagery layers that could construct a rooftop-PV presence panel
without GEHI/Gemini dating.

Endpoints:
- CoCT ERDAS: `https://cityimg.capetown.gov.za/erdas-iws/esri/GeoSpatial%20Datasets/rest/services`
- CoCT WMS: `https://cityimg.capetown.gov.za/erdas-iws/ogc/wms/GeoSpatial%20Datasets`
- CoJ ArcGIS: `https://ags.joburg.org.za/server/rest/services`

CRS: CoCT MapServers report `wkid=20482` (Hartebeesthoek94 / Lo19). CoJ 2000–2019
ImageServers are Gauss Conform Lo29 (metres, no EPSG wkid in JSON); CoJ 2023 is
`wkid=102100` (Web Mercator). Do not mix those without a project step.

License (CoCT service descriptions, live): "No restrictions on the digital file
for non-commercial purposes." CoJ ImageServers returned empty `copyrightText`.

---

## City of Cape Town — nadir aerials usable for PV

Modern municipal orthos are named `Aerial Imagery_Aerial Imagery <date>`.
Pixel size in the description is mosaic GSD (ground sample distance: 地面采样距离,
一个像素对应地面几米). Extent for the 2013–2022 8 cm series is roughly
65 km × 99 km in Lo19, i.e. metro-wide, not CBD-only.

| layer (service name) | capture time | GSD | coverage | access | live 2026-09-06 | PV-dating note |
|---|---|---|---|---|---|---|
| Aerial Imagery 2026Jan | 2026-01 (v2; "not the final image for 2026") | **0.05 m** | metro (same Lo19 box as 2019–2022) | ERDAS MapServer / WMS | yes | newest 5 cm; do not treat as locked census vintage |
| Aerial Imagery 2025Jan | 2025-01 (v3) | **0.05 m** | metro | same | yes | current ZAsolar census layer (`aerial_2025`); mid-date already frozen as 2025-01-31 |
| Aerial Imagery 2024 | 2024 (no month) | UNKNOWN (假设 0.05–0.08 m) | **~30% of metro only** | same | yes | cannot be a citywide panel year |
| Aerial Imagery 2023Jan | name says 2023; description is **NGI 2022 ortho, 0.25 m** | **0.25 m** | NGI extract, not CCT 8 cm | same | yes | **naming trap**. Not a municipal 8 cm flight |
| Aerial Imagery 2022Jan | 2022-01 (v5) | 0.08 m | metro | same | yes | last full 8 cm year before 5 cm |
| Aerial Imagery 2021Mar | 2021-03 (v3) | 0.08 m | metro | same | yes | |
| Aerial Imagery 2020Jan | 2020-01 | 0.08 m | metro | same | yes | |
| Aerial Imagery 2019Jan | 2019-01 | 0.08 m | metro | same | yes | |
| Aerial Imagery 2018Feb | 2018-02 | 0.08 m | metro | same | yes | |
| Aerial Imagery 2017Jan | 2017-01 | 0.08 m (OSM/JOSM; service desc empty) | metro | same | yes (service exists; GSD 假设 from OSM) | CBD True Ortho sibling also exists |
| Aerial Imagery 2016Jan | 2016-01 | 0.08 m | metro | same | yes | CBD True Ortho sibling |
| Aerial Imagery 2015Feb | 2015-02 | 0.08 m | metro | same | yes | OSM listed 12 cm for a 2015 TMS; **service text says 0.08 m** |
| Aerial Imagery 2014_NGI | mixed 2014 + 2016 + 2022 NGI tiles, resampled to 0.25 m | 0.25 m | wider than metro | same | yes | **not a single capture date**. Unusable as a panel year |
| Aerial Imagery 2013Dec | 2013-12 | 0.08 m | metro; planimetric ±1 m | same | yes | first 8 cm year in this probe |
| Aerial Imagery 2012Dec | 2012-12 | 0.08 m | **east truncated** (xmax ≈ −6000 vs +1000 on later years) | same | yes | coverage hole on the east |
| Aerial Imagery 2009Nov | 2009-11 | 0.125 m | smaller box than 2013+ | same | yes | still rooftop-resolvable |
| Aerial Imagery 2008June | 2008 | 0.20 m | metro-ish | same | yes | borderline for small arrays |
| Aerial Imagery 2005Feb | 2005-02 | 0.25 m | metro-ish | same | yes | same GSD class as GEHI z19 |
| Aerial Imagery 2000Apr | 2000-04 | 0.25 m | **patchwork of built-up areas**, not full metro | same | yes | coverage incomplete |
| Aerial Imagery 1958 | 1958 | 0.20 m greyscale | Parow–CBD–South Peninsula only | same | yes | pre-PV; heritage only |

Pre-2000 layers exist back to **1926** (1935, 1938, 1945, 1953, 1968, 1969, 1971,
1980–1988, 1992–1998). They are not PV-era. Do not spend probe time on them
for install dates.

Also on the same server, **not** for rooftop dating:
- monthly Sentinel RGB composites 2024-01 … 2026-04 (~10 m)
- 2018 Feb colour-infrared
- 2017 2 m DEM hillshade; DTM 2/5/10 m; 1 m height map

### What is usable for a true-date panel (Cape Town)

A **year-level** presence panel is constructable from the municipal 8 cm / 5 cm
stack: 2012, 2013, 2015–2022, 2025, 2026. Skip 2014_NGI and 2023Jan (NGI 25 cm)
and 2024 (30% coverage). Capture labels are month-named mosaics, not per-roof
flight days — same year-only conservatism already used on CoJ ISSUE-08.

That stack is in-domain for the census detector (Mask R-CNN on `aerial_2025`).
It does **not** replace a day-level GEHI interval; it replaces "Gemini on 0.25 m
satellite" with "detector on 5–8 cm true-year aerials".

---

## City of Johannesburg — nadir aerials

Public ImageServers under `AerialPhotography/<year>/ImageServer`. Pixel size
is native GSD from REST `pixelSizeX`. Time field on 2015 (and CoJ_Aerial) is
`Imagery_Year` — year only, already encoded in ISSUE-08.

| year | GSD | bands | extent (metres) | CRS | live | PV-dating note |
|---|---|---|---|---|---|---|
| 2000 | 0.25 m | 3 | ~55 × 72 km | Lo29 TM | yes | same class as GEHI z19; early rooftop PV rare |
| 2003 | 0.25 m | 3 | similar | Lo29 TM | yes | |
| 2006 | 0.20 m | 3 | similar | Lo29 TM | yes | |
| 2009 | 0.15 m | 3 | similar | Lo29 TM | yes | first 15 cm year |
| 2012 | 0.15 m | 3 | 63 × 84 km | Lo29 TM | yes | |
| 2015 | 0.15 m | 3 | 63 × 84 km | Lo29 TM | yes | already in `coj_arcgis_fetch.py`; `Imagery_Year` present |
| 2019 | 0.15 m | 3 | 63 × 84 km | Lo29 TM | yes | already used in ISSUE-08/09/29 |
| 2023 | 0.15 m | 3 | similar, but **EPSG:3857** | Web Mercator | yes | already used; CRS differs from 2015/2019 |
| CoJ_Aerial | 0.15 m | 3 | 63 × 84 km | Lo29 TM | yes | mosaic with `Imagery_Year` field — **not a single flight**. Do not use as one panel year |

Sibling services (not nadir true-date RGB at rooftop GSD):
- `Solar/2019` ImageServer: **1 m, 1 band** — solar-radiation raster, not PV inventory
- `Solar/SolarRadiation` MapServer layers: `Solar Radiation 2019`, `Building footprints` — radiation + footprints, not install dates
- `SatelliteImagery/2025_2026`: **0.30 m** RGB — satellite, not municipal aerial
- `AerialObliqueImagery/2019`: ~0.05 m oblique (camera pose fields). Hard for the nadir detector; QA overlay only

### What is usable for a true-date panel (Johannesburg)

Eight dated flights, three-year cadence 2000–2023, 15 cm from 2009. Metro-wide.
The audit pipeline already fetches 2015/2019/2023; 2000/2003/2006/2009/2012 are
the unused true-date years. Year-only labels stay. This is the strongest
external-robustness channel we already designed (PRD D9) — unused years are
the cheap expansion.

---

## Contrast with what the census currently uses

| city | census imagery in `regions.yaml` | municipal archive sitting unused |
|---|---|---|
| Cape Town | 2025 Jan WMS only | ~11 full 8 cm years 2012–2022 plus 5 cm 2026 |
| Johannesburg | Vexcel 2024 (6.7 cm, blended flight window) + aerial_2023 tiles | 8 true-year ImageServers 2000–2023 |

Vexcel remains the JHB *census* picture (sub-10 cm, 2024). It is a blended
mosaic, not a panel year. CoJ 2023 15 cm is the last true-date municipal year.
