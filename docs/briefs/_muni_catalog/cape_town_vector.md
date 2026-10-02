# City of Cape Town — vector / admin catalog for rooftop-PV install-date work

**Verified:** 2026-09-06 (live REST + Hub search + iteminfo; no bulk download).
**Scope:** VECTOR and administrative layers that could construct or validate a rooftop-PV install-date panel. Aerial-imagery vintages on `cityimg` are inventoried separately; they appear here only when they date a vector/DEM product.
**Not in scope:** GEHI / Google Earth historical chips, ZAsolar census polygons, or downloading LiDAR point clouds.

## How to read this catalog

- **Live-verified 2026-09-06:** endpoint answered, layer JSON / iteminfo / `query?returnCountOnly` was run this day.
- **Catalog-only:** indexed Hub page, conference paper, or PAIA list; the live service was not queried, or the named service is down.
- **假设:** inference from adjacent metadata, not read from the layer itself.

First uses: **nDSM** (normalized Digital Surface Model, 归一化数字表面模型: DSM minus DTM, so height above ground); **SSEG** (Small-Scale Embedded Generation, 小型嵌入式发电: municipal grid-tied PV/battery < 1 MVA); **cadastral** (地籍: legal land-parcel fabric); **GSD** (ground sampling distance, 地面采样间距).

## Verdict for the PV-dating panel

What this catalog says: Cape Town publishes two complementary **roof-extent** footprint layers (photogrammetric ~287k; 2023 deep-learning ~1.45M), a live cadastral/address fabric, a 2025 1 m nDSM, and **no public per-installation SSEG register with application or commissioning dates**.

Why it matters: footprints and parcels can host and spatially join a census; SSEG dates would have been the municipal “true-date” channel. They are not on the Open Data Portal. Ignoring that gap would treat city-wide kWh export tables or building-plan free text as if they were install years.

If we ignore it: we will either (a) join PV anchors to buildings/erven without a municipal date, which is still useful for roof-context and Eskom-vs-CCT supply splits, or (b) silently treat SmartFacility / export totals / 66 solar-ish building plans as a gold set. Only (a) is defensible from public data.

---

## 1. Service topology (live 2026-09-06)

User-known endpoints `Theme_Based/EGISViewer` and `Theme_Based/Open_Data_Service` **are not started**:

```
https://citymaps.capetown.gov.za/agsext/rest/services/Theme_Based/EGISViewer/MapServer
  error 500: Service Theme_Based/EGISViewer/MapServer not started

https://citymaps.capetown.gov.za/agsext/rest/services/Theme_Based/Open_Data_Service/MapServer
  error 500: Service Theme_Based/Open_Data_Service/MapServer not started
```

Google-cached pages for `EGISViewer/88` (“2D Basic Building Footprints”, Mid-2015) and `Open_Data_Service/86` / `/239` still exist. Treat those URLs as **catalog-only / stale**. Live replacements:

| Role | Live service | Notes |
|---|---|---|
| Photogrammetric 2D footprints + addresses | `Theme_Based/ODP_SPLIT_6` layers 2 and 1 | Hub item URLs also point at `esapqa.capetown.gov.za` (same layers) |
| 2023 DL “Low Detail” footprints | `Theme_Based/ODP_SPLIT_12` layer 11 | iteminfo = 2023 LiDAR nDSM + 8 cm aerial |
| Same two footprint layers, viewer copy | `Theme_Based/Basic_Services_Infrastructure` layers 20 and 31 | **live counts match ODP_SPLIT**; **iteminfo text is stale** (Mid-2015 / LiDAR 2021B) |
| Cadastre | `Theme_Based/ODP_SPLIT_4` layer 0; also `Land_Administration` | hosted AGOL copy has a slightly higher count |
| GLM contours | `Theme_Based/Spatial_Planning` layer 1 | 2018–2019 LiDAR |
| Electricity | `Theme_Based/ODP_SPLIT_1` and `_12` | supply authority, districts, capacity |
| Council solar meters | `Theme_Based/ODP_SPLIT_10` layer 13 | SmartFacility, not household SSEG |
| Elevation rasters | `https://cityimg.capetown.gov.za/erdas-iws/esri/GeoSpatial%20Datasets/rest/services/` | 2025 DTM/nDSM + GLM 2019; **not** the aerial archive inventory |

Hub search (live): `https://odp-cctegis.opendata.arcgis.com/api/search/v1/collections/all/items?q=…` (256 public items total).

---

## 2. Layer cards

Each card: name, source, time, resolution/scale, coverage, access, verification, license, why it matters for PV dating.

### 2.1 2D Building Footprints (photogrammetric / `SL_CGIS_2D_BLD_FTPR`)

| Field | Value |
|---|---|
| **Name** | 2D Building Footprints (roof extents from 3D building models). Internal feature-class name in metadata: `SL_CGIS_2D_BLD_FTPR`. |
| **Source** | City of Cape Town Geomatics / IKM. Derived from photogrammetrically captured LOD2.2 3D models, verified against land parcels, ortho mosaic, and oblique imagery. |
| **Time** | **Live-verified capture years in `ACQS_PRD` (YYYYMM):** 201312 (96,299), 201601 (24,622), 201701 (41,609), 201802 (44,682), 201901 (26,449), 202001 (9,681), 202103 (10,145), 202201 (10,400), 202301 (14,235), 202501 (9,196), plus 2 at 201801. **No 2024 bin.** Layer is “updated partially on an annual basis”; Hub item modified 2026-07-28. |
| **Resolution / scale** | Vector roof polygons + `BLD_HGT`. Source imagery GSD is **not in iteminfo**. FIG 2026 paper (Röttcher) states the parent 3D models come from **annual 5 cm aerials**, ~30 cm positional accuracy, LOD2.2, ≥10,000 new models/year — **假设** that this is the parent of the public 2D layer. `ACQS_MTHD` live: Photogrammetry 265,686 / Photogrammetric 21,584 / Photogrammetry+ 55. |
| **Coverage** | Metro, but **not wall-to-wall**: 287,328 polygons vs 1.45M on the DL layer. Extent roughly Cape Town municipal area (Web Mercator). The BSI iteminfo still says “coverage up till Mid2015”; **that sentence is false against live attributes**. |
| **Access** | Open query/download via REST (max 2,000 records/page). Hub: [2D Building Footprints](https://odp-cctegis.opendata.arcgis.com/datasets/4a542172a2cc430898a5e635d688eee3). REST: `https://citymaps.capetown.gov.za/agsext/rest/services/Theme_Based/ODP_SPLIT_6/MapServer/2` (also FeatureServer/2). Viewer twin: `Basic_Services_Infrastructure/MapServer/20` (same 287,328 count). Full 3D models are **purchase / City Maps**, not this layer. |
| **Verified** | **Live-verified 2026-09-06** (count, `ACQS_PRD` histogram, fields, iteminfo). BSI/20 description = stale catalog text. |
| **License** | Hub: City Open Data Terms of Use, `license=custom`. Layer iteminfo: “All rights reserved… without permission from IKM… acknowledgement must be made to CCT.” Not CC-BY. |
| **Why it matters** | Best **formal-building roof outline** with a **per-feature imagery date**. `ACQS_PRD` is a *building-model capture date*, not a PV install date. It can (1) host census polygons on a named roof, (2) flag buildings that did not exist before YYYYMM, (3) join to parcels. Ignoring the Mid-2015 iteminfo and using live `ACQS_PRD` is the difference between a frozen 2015 mask and a 2013–2025 vintage stack. |

Fields (live): `OBJECTID`, `ACQS_MTHD`, `ACQS_PRD`, `DMLT_PRD` (demolition period; present on ODP_SPLIT_6, absent on BSI/20), `DATA_SRC` (almost unused), `BLD_HGT`, Shape.

---

### 2.2 2D Building Footprints Low Detail (2023 LiDAR nDSM + 8 cm aerial, deep learning)

| Field | Value |
|---|---|
| **Name** | 2D Building Footprints Low Detail. Complement to `SL_CGIS_2D_BLD_FTPR`. |
| **Source** | Automated extraction: Mask R-CNN on 2023 nDSM (P/R/F1 0.85/0.70/0.77) + U-Net on 8 cm RGB aerial (P/R/F1 0.97/0.89/0.93), then ModelBuilder regularisation. Manual QA stated on Hub snippet. |
| **Time** | **Live `ACQS_PRD` = `2023` for 1,444,948 / 1,444,950 features.** `ACQS_MTHD` = `Automated - Deep Learning`. Hub created 2025-03-17, metadata touched 2026-07-29. |
| **Resolution / scale** | Vector. Source: **8 cm** ortho (RGB) + LiDAR **10 pts/m²**, vertical accuracy **0.1 m (95%)**. Mean height `BLD_HGHT` from 2023 nDSM. FIG 2026 paper quotes 5 cm aerial and 20 pts/m² for the same programme — **do not silently reconcile**; public iteminfo is 8 cm / 10 pts. Paper also quotes ~950,000 buildings; **live count is 1,444,950**. |
| **Coverage** | Intended metro-wide, including informal / backyarder stock that the photogrammetric layer misses. `DATA_SRC` live: Aerial Imagery 1,094,194; LiDAR 350,754. |
| **Access** | Hub: [2D Building Footprints Low Detail](https://odp-cctegis.opendata.arcgis.com/datasets/77bc6b3b6f3c404faf3f0181f7bbfc2f). REST: `https://citymaps.capetown.gov.za/agsext/rest/services/Theme_Based/ODP_SPLIT_12/MapServer/11`. Viewer twin: `Basic_Services_Infrastructure/MapServer/31` (**same live count**, **stale iteminfo still says LiDAR 2021B beta**). |
| **Verified** | **Live-verified 2026-09-06** (count, `ACQS_PRD`/`DATA_SRC`/`ACQS_MTHD`, ODP_SPLIT_12 iteminfo). BSI/31 iteminfo = stale. Cached `Open_Data_Service/239` = stale 2021B text. |
| **License** | Hub Terms of Use (`custom`). ODP_SPLIT_12 iteminfo: “No restrictions on the digital file for **non-commercial** purposes. All rights reserved… permission from Geomatics Branch… acknowledgement to CCT.” Tighter than a blanket open licence. |
| **Why it matters** | Wall-to-wall **2023 roof mask** plus height. Use it to (1) attach census PV to a building in informal/backyarder areas, (2) compare roof presence 2023 vs photogrammetric vintages, (3) **not** as a PV layer — DL footprints are buildings, not panels. If ignored, small rooftop systems on backyarder units have no municipal roof polygon to sit on. |

Known: this **is** the 2023 LiDAR nDSM (10 pts/m²) + 8 cm aerial product named in the task. It replaced the 2021B “beta” LiDAR-vectorisation layer in the live attributes, even where old descriptions linger.

---

### 2.3 Land Parcels (cadastre)

| Field | Value |
|---|---|
| **Name** | Land Parcels / Property. City cadastre as populated by the Property Value Chain. |
| **Source** | Development Management / multiple internal systems. Hub item `7c59bb7a1b724d11a70d3db591233df1`. |
| **Time** | Live layer; Hub “Data Updated” May 2026. No construction-year field on the public schema. |
| **Resolution / scale** | Vector polygons. Identifiers: `SG26_CODE`, `SL_LAND_PRCL_KEY` (ISIS/LIS), `PRTY_NMBR`, address parts, `ZONING`, `LU_LGL_STS_DSCR`. |
| **Coverage** | Metro. Live counts **do not agree across copies:** `ODP_SPLIT_4/MapServer/0` = **835,018**; hosted `services6…/Property/FeatureServer/0` = **847,424**. Use one copy and record which. |
| **Access** | Hub: [Land Parcels](https://odp-cctegis.opendata.arcgis.com/datasets/7c59bb7a1b724d11a70d3db591233df1). REST: `https://citymaps.capetown.gov.za/agsext/rest/services/Theme_Based/ODP_SPLIT_4/MapServer/0`. Also `Land_Administration/MapServer/7` (and `/14` Property). |
| **Verified** | **Live-verified 2026-09-06** (schema + two counts). |
| **License** | Open Data Terms of Use, `custom`. |
| **Why it matters** | Join key from SSEG applications, building plans (`ERF_Number`), and municipal accounts onto a polygon. Without parcels, a “commissioning date” (if ever obtained) cannot be snapped to a roof. It is **not** an install-date layer. |

Related: **Zoning** Hub `f9fa900cf08d4036a5a8f033a14ff8a0` (join-to-parcels / split zoning) — **catalog-only** beyond Hub snippet; **Sectional Title Scheme** Hub `aea9409a2a74456f8015ebabf66b73d1` (bodies corporate; SSEG needs 75% resolution — context only).

---

### 2.4 Street Address Numbers

| Field | Value |
|---|---|
| **Name** | Address / Street Address Numbers / Street Number. Official situational (street) address points. |
| **Source** | Development Management; Street Naming and Numbering Policy; ramped into SAP LUM. Hub `c2101858187f424298f85e60f9706533`. |
| **Time** | Live maintained. `LU_LIFE_CL_STG_KEY` / `LU_ADR_STS_KEY` exist; **not** decoded this day. |
| **Resolution / scale** | Points. Fields include `ADR_NO`, prefix/suffix, `STR_NAME`, `OFC_SBRB_NAME`, `FULL_ADR` (on ODP_SPLIT_6/1). |
| **Coverage** | Metro. Live count on `ODP_SPLIT_6/MapServer/1`: **859,886**. |
| **Access** | REST `…/ODP_SPLIT_6/MapServer/1`. Viewer: `Land_Administration/MapServer/13` (Street Number; fewer address-text fields). |
| **Verified** | **Live-verified 2026-09-06** (count + fields on ODP_SPLIT_6/1). |
| **License** | Open Data Terms of Use, `custom`. |
| **Why it matters** | Geocoding key for any future SSEG dump that is address-only. Not a date. |

---

### 2.5 Electricity Supply Authority (CCT vs Eskom)

| Field | Value |
|---|---|
| **Name** | Electricity Supply Authority |
| **Source** | Electricity Generation and Distribution. Hub `bdd9bc5101134f96a647acb7a8675aa3`. |
| **Time** | Live administrative boundaries; no vintage attribute used. |
| **Resolution / scale** | Polygons. Field `SUPPLY_AUTHORITY`. |
| **Coverage** | Metro split. Live: **11 CCT + 10 Eskom** polygons (21 features). |
| **Access** | `…/ODP_SPLIT_1/MapServer/3`. Also BSI group “Electricity”. |
| **Verified** | **Live-verified 2026-09-06**. |
| **License** | Open Data Terms of Use, `custom`. |
| **Why it matters** | City SSEG authorisation **only covers the City electricity supply area**. Anchors in Eskom pockets will not appear in a CCT SSEG register even if one is later obtained. Ignoring this mixes two legal regimes into one “municipal date” prior. |

Related electricity (lower priority for dating):

- **Electricity Distribution Capacity** — Hub `9da6c1a24d2d4c3ea1a3b997c414d9fc`, REST `ODP_SPLIT_12/37`, 88 polygons, `Year` + MVA fields. Network capacity, not installs. Live-verified count.
- **Electricity District Areas / Public Lighting / Generation and Distribution Districts** — `ODP_SPLIT_1` layers 0–2. Administrative.
- **Suburb Level Electricity Billing 2021 to June 2026** — Hub `f764cc3a7371414a914be4f64699618e`. Aggregate billing, not rooftop dates. Catalog-only beyond Hub hit.
- **Household Electricity Connections** — CSV of electrification progress, not SSEG. Catalog-only.

---

### 2.6 SmartFacility Solar (council buildings only)

| Field | Value |
|---|---|
| **Name** | SmartFacility Solar / Smart Facilities Solar |
| **Source** | Sustainable Energy Markets; AMI meters on **council** facilities via PnPScada. Hub `a5e7b9702a974f23aa6d3698b32c0ea0`. |
| **Time** | Monthly/annual generation attributes (`MNTH_SLR_GNRT`, `ANL_SLR_GNRT`, `CLND_YEAR`, `LAST_RDNG_DATE_TIME`). These are **meter readings**, not permission-to-install dates. |
| **Resolution / scale** | Points (facility centroids). 839 features live. |
| **Coverage** | City-owned metered facilities, not private rooftops. Extent is a subset of the metro. |
| **Access** | `…/ODP_SPLIT_10/MapServer/13`. Companion **SmartFacility Energy** Hub `e9ab50ff0b35457c9d046e41645e7aec`. |
| **Verified** | **Live-verified 2026-09-06** (fields + count). |
| **License** | Open Data Terms of Use, `custom`. |
| **Why it matters** | Tiny, high-quality **municipal-roof** solar subset. Usable as a handful of known-present sites, **not** as a citywide install-year panel. Treating 839 council meters as the SSEG register would bias to public buildings. |

---

### 2.7 Customer Solar Energy Exports 2014–2023 (SSEG kWh, city-wide)

| Field | Value |
|---|---|
| **Name** | Customer Solar Energy Exports 2014 to 2023 |
| **Source** | Hub CSV `49db667dcb334afd9f2da765c6e3ba6b`. Tags explicitly “SSEG purchases”. |
| **Time** | Annual columns 2014–2023. File size 1,358 bytes. |
| **Resolution / scale** | **Not spatial.** Rows = tariff class (Domestic, Small Power User, Large Power Users, Municipal: Small Power User, …). Values are kWh exported (negative in the file). |
| **Coverage** | City customers as billed; no erf, no address, no system. |
| **Access** | Public CSV via AGOL item `/data`. |
| **Verified** | **Live-verified 2026-09-06** (file contents read; small enough that this is not a bulk download). |
| **License** | Open Data Terms of Use, `custom`. |
| **Why it matters** | Confirms SSEG **exists in open data only as an aggregate energy time series**. Useful as a city-level prior for “exports ramped from ~2015”, useless as a per-roof date. If ignored, one might search the portal for “SSEG”, find this hit, and assume a register. |

---

### 2.8 Building Plan Approvals 2014 to 2026

| Field | Value |
|---|---|
| **Name** | Building Plan Approvals 2014 to 2026 (table title still `…_2014_to_2025`) |
| **Source** | Building Development Management / DAMS. Hub `a9a32e2ecc074b318ee0ad136fb7e2af`. |
| **Time** | `Submission_Date`, `Approval_Date` (dates); `Commencement_Date`, `Completion_Date`, `OCC_Issued_Date` (strings); `Financial_Year`. 247,845 rows. |
| **Resolution / scale** | **Non-spatial table.** Join keys: `ERF_Number`, `Suburb`, `Ward_No`. No lat/lon. |
| **Coverage** | Approved building plans metro-wide, 2014–2026. Solar is **not a category**. `Primary_Category__` is Additions/Residential/Other/Non-residential. Free-text `Type_of_Work_Description` LIKE `%SOLAR%` returned **66** rows; samples include solar geyser, solar carport, “Omit Solar Panel”, “solarium”, and a few true “solar panel” strings. |
| **Access** | `https://services6.arcgis.com/nyYfO9SxHU2ChQd9/arcgis/rest/services/Building_Plan_Approvals_2014_to_2025/FeatureServer/0` |
| **Verified** | **Live-verified 2026-09-06** (schema, count, category histogram, solar text sample). |
| **License** | Open Data Terms of Use, `custom`. POPIA-stripped. |
| **Why it matters** | Has real **approval dates** and erf numbers, but rooftop PV in Cape Town is authorised as **electrical SSEG**, not as a building-plan class. Using these 66 rows as install years would be both incomplete and semantically mixed (geysers, carports, solariums). Join-to-parcel is possible; it is not a PV panel. |

---

### 2.9 Ground Level Map 2019 (5 m DEM + 2 m contours)

| Field | Value |
|---|---|
| **Name** | CCT GLM 2019 5 m DEM; Ground Level Map 2 m Contours |
| **Source** | LiDAR **2018 + 2019A + 2019B** (LiDAR2019B compilation). Legal Existing Ground Level under the Municipal Planning By-law. Council approved 27 Jul 2023. |
| **Time** | Capture 2018–2019. Legal product 2023/24. |
| **Resolution / scale** | 5 m bare-earth DEM, vertical accuracy **0.3 m**. Contours every 2 m. Source LiDAR **2–3 pts/m²**, 0.1 m (95%) on the point cloud (Hub contour page). |
| **Coverage** | Full municipal area. |
| **Access** | Raster REST: `https://cityimg.capetown.gov.za/erdas-iws/esri/GeoSpatial%20Datasets/rest/services/Elevation_CCT_GLM2019_5m_DEM/MapServer`. Vector: `Spatial_Planning/MapServer/1`; Hub `bbd5614ce9154e649265dae5e9d26018`. Document Hub `dfed46fcf1784aafacb146b14f01cd49`. |
| **Verified** | **Live-verified 2026-09-06** (cityimg service description + Spatial_Planning layer fields). |
| **License** | Open Data Terms of Use for the published services; GLM is also a statutory map. |
| **Why it matters** | Dates a **2018–2019** LiDAR epoch (see §3). Bare-earth only — no rooftop PV signal. Needed if anyone differences later DSMs against legal ground. |

---

### 2.10 2025 Height Map (1 m nDSM) and 2025 DTMs

| Field | Value |
|---|---|
| **Name** | `Elevation_Height_Map_1m`; `Elevation_DTM_2m` / `_5m` / `_10m` |
| **Source** | cityimg GeoSpatial Datasets. Height map = 2025 1 m DSM − 2025 1 m DEM. DTMs “derived from 2025 LIDAR data”. |
| **Time** | **2025 LiDAR epoch.** This is in addition to 2017/2018–19/2023. |
| **Resolution / scale** | nDSM: **1 m**, heights **< 2 m excluded** (service text: high-res noise; overlay aerials or footprints). DTMs: 2 / 5 / 10 m bare earth. CRS WKID 20482 (Hartebeesthoek94 / Lo19). |
| **Coverage** | Municipal clip (same family of extents as GLM). |
| **Access** | WMS/REST on cityimg (see §1). **Point clouds are not in these services** — PAIA lists LiDAR LAS for purchase via City Maps. |
| **Verified** | **Live-verified 2026-09-06** (service JSON descriptions). 2025 1 m DSM/DEM as separate named services: **not listed** under those names; only the nDSM and DTMs. |
| **License** | City raster services; same Open Data / City Maps regime. Commercial bulk LiDAR remains a tariff/PAIA product (**catalog-only** for the LAS itself). |
| **Why it matters** | A second-date **height surface** after the 2023 nDSM used for footprints. 1 m GSD cannot resolve panels, but a 2023-vs-2025 nDSM difference **could** flag new bulky roof plant (假设; 2023 nDSM is not published as its own cityimg service). If ignored, the catalog would stop at “2017 DEM + 2023 LiDAR” and miss a live 2025 epoch. |

---

### 2.11 Tree Canopies (2018 LiDAR + CIR)

| Field | Value |
|---|---|
| **Name** | Tree Canopies |
| **Source** | 2018 NIR/CIR aerial + LiDAR + auto-correlated DSM; vegetation > 2.75 m. Hub `069d19c0afb64bbbbd37322f516a20d6`. |
| **Time** | 2018. |
| **Resolution / scale** | Vector canopy polygons; 5×5 km processing grid (147 blocks). |
| **Coverage** | Metro (by design). Count not queried this day. |
| **Access** | Hosted FeatureServer `Tree_Canopies`. |
| **Verified** | Hub metadata **live-read 2026-09-06**; feature count not queried → remaining geometry **catalog-only**. |
| **License** | Open Data Terms of Use, `custom`. |
| **Why it matters** | Independent confirmation of an **2018 LiDAR** epoch. Occlusion context for rooftop visibility, not a PV date. |

---

### 2.12 Energy Performance Certificates (city buildings, Excel)

| Field | Value |
|---|---|
| **Name** | Energy Performance Certificates |
| **Source** | Hub `e572e77290fc48838145e2e6ddcb0280`. 69 City-issued EPCs. Tags include “Rooftop solar Data (kWh)”. |
| **Time** | Published May 2024. Not a time series of installs. |
| **Resolution / scale** | Spreadsheet, ~21 kB. Building-level energy, not a map. |
| **Coverage** | City-owned buildings meeting DMRE EPC rules, not private roofs. |
| **Access** | Public Excel on Hub. File not opened this day (schema from Hub tags only). |
| **Verified** | Hub record **live-read**; workbook contents **catalog-only / 假设** from tags. |
| **License** | Open Data Terms of Use, `custom`. |
| **Why it matters** | At most a few dozen municipal roofs with a solar-kWh column. Not a dating panel. |

---

### 2.13 Cape Town State of Energy and Carbon 2025

| Field | Value |
|---|---|
| **Name** | Cape Town State of Energy and Carbon 2025 |
| **Source** | Sustainable Energy Markets. Hub `46c2d508166042d0acac9ae87d528e9b`. |
| **Time** | Analysis vintage 2014–2023 aggregates. |
| **Resolution / scale** | Desktop application / annexures. City-scale energy and GHG. |
| **Coverage** | City energy system, not rooftops. |
| **Access** | Hub app template. |
| **Verified** | Hub snippet **live-read**. |
| **License** | Open Data Terms of Use, `custom`. |
| **Why it matters** | Context for SSEG growth narrative only. |

---

### 2.14 SSEG application / commissioning register (NOT on the Open Data Portal)

| Field | Value |
|---|---|
| **Name** | Energy Services Applications (e-Services) — Permission to Install (PTI) and Commissioning Approval. Internal “SSEG info in GIS” (Jefferies, Sep 2025). |
| **Source** | City Electricity Generation and Distribution; e-Services portal. GreenCape CCT SSEG case study (2025); DG Africa workshop slides (May 2025 stats); capetown.gov.za Apply-for-SSEG pages (still live Aug 2026). |
| **Time** | Process dates exist **inside** the application: PTI letter date; commissioning-report date; Commissioning Approval Letter date; AMI-meter install date (feed-in tariff). Public headline stocks: ~6,400 authorised systems / 140.6 MVA by Jun 2024; 12,819 grid-tied apps approved by 30 May 2025 / 227 MVA; 13,398 systems / 251.6 MVA by Jun 2025 (ITWeb citing City). Jefferies slide: 32,311 applications (16,893 approved, 10,010 cancelled, 4,699 waiting on customer). |
| **Resolution / scale** | Per-property application, tied to BP/erf in SAP. **Not published as a feature layer.** Slides show GIS maps of SSEG internally. |
| **Coverage** | **Incomplete by construction:** (1) City supply area only (see 2.5); (2) large lag / non-completion between PTI and commissioning (GreenCape 2025: customers install and switch on without finishing commissioning); (3) pre-2024 paper applications; (4) illegal / unregistered systems; (5) Eskom-supplied erven. |
| **Access** | Owner/installer login on e-Services. No ODP dataset named SSEG, embedded generation, or rooftop PV register (Hub `q=SSEG` returns only the exports CSV). `apply.sseg.org.za` does **not** list City of Cape Town (City uses its own e-Services). |
| **Verified** | Process and **absence from ODP: live-verified 2026-09-06**. Internal GIS screenshots and MVA totals: **catalog-only**. Per-record dates: **not public**. |
| **License** | Personal/account data; POPIA. Not an open layer. |
| **Why it matters** | This would be the municipal install-year channel (`date_commissioned` in the v2 PRD). **It is admin, lag-biased, and not downloadable.** A research ask to ElectricityApplication.Queries / City Maps could request a POPIA-safe extract (erf + PTI date + commissioning date + kVA, no names). Until that lands, Cape Town has **no public true-date panel**. |

There is **no public SSEG dashboard** on the ODP “Apps and Dashboards” strip (Map Viewer, Water Quality, Data Analytics, Waste Recyclers, StreetSignal). City energy pages are application how-tos, not a map of authorised systems.

---

### 2.15 Other hits (low value for dating, recorded so they are not re-searched)

| Name | What it is | PV-dating use |
|---|---|---|
| CCT Buildings (`ODP_SPLIT_7/10`) | City-owned facility points (offices, clinics, …), not all roofs | Join to SmartFacility; not private PV |
| Aerial Photography Grid 1 km | Frozen geocell index | Indexing only; “not being updated” |
| Line indicating different heights on same property (`ODP_SPLIT_11/4`) | Planning height-split lines | Weak |
| Raster Imagery document (`739759d8127f4d1f9ba8ef9019878147`) | Pointer into cityimg (aerials + GLM + DEMs) | Gateway to rasters; aerial vintages are the other inventory |
| Spatial Data Services Tariff CSV | PAIA / City Maps price list including **LiDAR data** | Confirms LAS is sold, not opened |
| System Energy 2017–Jun 2026 | City vs Eskom energy balance table | No rooftops |
| Loadshedding schedule / prepaid vendors | Operational | No |

Hub `q=rooftop` only hits the EPC spreadsheet. `q=nDSM` only hits Low Detail footprints. **No public 3D building-model layer** on ODP (LOD2.2 models are City Maps / purchase; PAIA lists “3D building models” and “Building footprints (2D and 3D)” for copying/purchase).

---

## 3. LiDAR years besides 2023 / “2017 DEM”

cityimg + ODP + iteminfo, live 2026-09-06:

| Epoch | Evidence | Product on the open stack | Density / notes |
|---|---|---|---|
| **2017–2019 ground LiDAR** | cityimg `Backdrops_Hillshade 2017_2m DEM`: “2 m hillshade produced from **2017-2018-2019** CCT lidar ground classified data”, clipped to 2019 municipal boundary | Hillshade only (8-bit jp2) | Confirms 2017 as a **start of a multi-year campaign**, not a lone 2017 nDSM |
| **2018 / 2019A / 2019B** | GLM 2019 service text; contour Hub page | 5 m DEM + 2 m contours | **2–3 pts/m²**, 0.1 m (95%) on cloud; 0.3 m on 5 m DEM |
| **2018** (same campaign) | Tree Canopies metadata (LiDAR + CIR + DSM) | Tree canopy vectors | Independent 2018 timestamp |
| **2021B** | BSI/31 and cached Open_Data_Service/239 iteminfo | **Not in live attributes** | Old “beta” footprint extraction. Live Low Detail is 2023. Treat 2021B footprints as **superseded**. Whether 2021B LAS is still sold by City Maps: **假设 / ask City Maps** |
| **2023** | ODP_SPLIT_12/11 iteminfo; Hub Low Detail snippet; Röttcher/Makungo Nov 2025 slides (“8 cm + 10 pts/m²”) | DL building footprints + mean height. **2023 nDSM raster is not a named cityimg service** | **10 pts/m²**, 0.1 m (95%); 8 cm aerial |
| **2025** | cityimg `Elevation_Height_Map_1m`, `Elevation_DTM_2m/5m/10m` | 1 m nDSM (heights <2 m dropped); 2/5/10 m DTMs | Density **not stated** on the raster services. FIG 2026: LiDAR “captured annually… 20 pts/m², 15 cm” — **conflicts with 10 pts / 0.1 m** on the 2023 footprint iteminfo |

Also: `Digital Maps_DM_COCT_DEM_10m_Lidar 10m _DEM.tif` exists on cityimg but has **empty description** (catalog-only year).

LinkedIn (Lara Röttcher, Nov 2024, catalog-only): “updated every two years, latest 2023… 10 pts/m², 15 cm”. FIG 2026 (same shop): annual, 20 pts/m². **Do not pick a density without asking Geomatics.** What is live-verified is: **2017–19 campaign, 2023 extraction inputs, 2025 DTM/nDSM on cityimg**.

Point clouds / classified LAS: PAIA Annexure B and City Maps tariff. Contact `city.maps@capetown.gov.za`. Not ODP.

---

## 4. License (applies to almost every ODP layer)

- Portal: [Open Data Terms of Use](https://www.capetown.gov.za/general/terms-of-use-open-data) + [Open Data Policy (2020)](https://resource.capetown.gov.za/documentcentre/Documents/Bylaws%20and%20policies/Open_Data_Policy.pdf). Data “as is”, no warranty, free of charge, no registration.
- Hub items: `license=custom`, not CC.
- Some layer iteminfo (Low Detail, photogrammetric footprints) still say **all rights reserved** and, for Low Detail, **non-commercial** unless Geomatics permits.
- Practical implication for this repo: query/cache for research is consistent with the portal ToU; **redistributing a derived commercial product of the DL footprints would need a written Geomatics OK**. 假设: a methods paper using footprints as a mask is acknowledgement-level, not a licence gap — still cite CCT and the ToU.

---

## 5. What is missing that a dating panel would want

1. **Per-system SSEG register** (erf / LIS key, kVA, PTI date, commissioning date, status). Exists internally; not on ODP.
2. **Published 2023 nDSM raster** (only the 2025 Height Map is named on cityimg).
3. **2021B and 2023 LAS** as open download (purchase only).
4. **LOD2.2 3D models** as open download (purchase / City Maps).
5. **Photogrammetric footprint GSD** on the layer itself (paper says 5 cm parent imagery; iteminfo silent).
6. **2024 building-model vintage** (`ACQS_PRD` jumps 202301 → 202501).

---

## 6. Suggested use in the Cape Town install-date stack

Priority if we actually pull vectors (not done today):

1. **Electricity Supply Authority** — split the census into CCT vs Eskom before any SSEG talk.
2. **Low Detail 2023 footprints** — host informal/backyarder anchors; height as a 2023 roof snapshot.
3. **Photogrammetric footprints + `ACQS_PRD`** — formal roofs with a capture-year stack 2013–2025.
4. **Land Parcels + Address** — join keys for a future SSEG extract and for DAMS plans.
5. **2025 1 m nDSM** — optional height-change experiment vs 2023 mean `BLD_HGHT` (假设, different GSD/process).
6. **Do not** build a gold date from SmartFacility, export CSV, or DAMS “solar” text.

---

## 7. Source log (2026-09-06)

- Hub search API: `https://odp-cctegis.opendata.arcgis.com/api/search/v1/collections/all/items?q=`
- citymaps REST folder listing and layer JSON / iteminfo / `query` counts and `groupBy` on `ACQS_PRD`, `DATA_SRC`, `SUPPLY_AUTHORITY`
- cityimg `GeoSpatial Datasets` service list + DTM/nDSM/GLM/hillshade descriptions
- Hub items cited by id in §2
- [capetown.gov.za Apply for SSEG](https://www.capetown.gov.za/City-Connect/Apply/Municipal-services/Electricity/apply-for-authorisation-to-install-a-small-scale-embedded-generation-system)
- GreenCape, *City of Cape Town’s Pioneering in SSEG* (2025 PDF); Ismail Jefferies, *Grid Impact Studies – City of Cape Town* (Sep 2025 PDF); Röttcher, FIG 2026 TS11D paper; Röttcher & Makungo, Esri SA User Forum, 4 Nov 2025
- PAIA Annexure B (GIS layers available for viewing vs purchase)
)
