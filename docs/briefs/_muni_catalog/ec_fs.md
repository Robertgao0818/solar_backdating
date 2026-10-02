# Municipal GIS / imagery catalog — Eastern Cape & Free State metros

**Scope.** Publicly reachable imagery and vector layers that could help build a rooftop-PV presence/absence-over-time panel for:

1. Nelson Mandela Bay Metro (NMBM; Gqeberha / Port Elizabeth, Kariega, Despatch)
2. Buffalo City Metro (BCMM; East London, Qonce, Bhisho, Mdantsane)
3. Mangaung Metro (MMM; Bloemfontein, Botshabelo, Thaba Nchu)

**Not** a free-detection catalog. These cities are candidate *seed locations* for install-date inference given known PV polygons.

**Method (2026-09-06).** Official GIS pages first, then live HTTP probes (`curl` / Python `urllib`, 12–25 s timeout, TLS verify off where municipal certs were flaky). Prefer ArcGIS `?f=pjson`. Imagery tiles were **not** downloaded — metadata, layer lists, and `returnCountOnly` queries only. Unverified claims are marked **假设**.

**Probe notes.**

- NMBM public viewer is live. Internal ArcGIS (hostname `hq-gis-01.ds.local`) is reachable from outside through the public resource-proxy `https://gis.nelsonmandelabay.gov.za/resource-proxy/proxy.ashx?<internal-url>`. Direct DNS for `hq-gis-01.ds.local` does not resolve off-network.
- BCMM on-prem portal `https://gis.buffalocity.gov.za/` (linked from the municipal homepage) **timed out** at 25 s on 2026-09-06. Public AGOL “City Map” is live.
- Mangaung has no live municipal GIS hostname. Public AGOL feature services owned by `Chuma.twetwa@mangaung.co.za` are live.
- National NGI / CSG / Esri SA endpoints below were live unless marked otherwise.

---

## Shared national layers (cover all three metros)

These are not municipal products. They are listed once because they are the fallback temporal stack wherever a metro has no historical ortho vintages.

| Municipality | Layer name | Data source / publisher | Capture time | Resolution / GSD or vector scale | Spatial coverage | Access method | Live-verified 2026-09-06 vs catalog-only | License / terms | Why it matters for PV dating |
|---|---|---|---|---|---|---|---|---|---|
| All three | RSA_NGI_AERIAL | National Geo-spatial Information (NGI / CD:NGI), published via Esri South Africa ImageServer | UNKNOWN mosaic date; NGI digital programme is 50 cm 2008–2016 then 25 cm from 2017, country cycled ~3 years (**假设** this service is the current 25 cm national mosaic, not a dated vintage stack) | 0.25 m (`pixelSizeX/Y` = 0.25) | National (Web Mercator extent covers RSA) | ArcGIS ImageServer REST: `https://imagery.esri-southafrica.com/server/rest/services/NGI/RSA_NGI_AERIAL/ImageServer` | **Live-verified** (catalog + `?f=pjson`: capabilities `Catalog,Image,Metadata`) | Not stated on the service. NGI products are issued under the Land Survey Act 8 of 1997; bulk supply is fee-listed. Treat Esri SA republish as **view/stream, not a bulk-download licence**. | Only nationwide 25 cm aerial currently confirmed as a REST image service. One epoch, not a time series — use as a high-res *present-day* check, not as a dating panel. |
| All three | CDNGI Geospatial Portal (aerial / ortho catalog) | NGI, DALRRD | Historical analogue from ~1930s; digital 50 cm 2008–2016; 25 cm from 2017; 1:10 000 sheet tiles | 0.50 m (2008–2016); 0.25 m (2017–); analogue ortho ~0.75 m pre-2008 | National, tiled to 1:10 000 sheet index. EC sheets around 3325 / 3327; FS around 2926. | Browser catalog: `https://ngigeoportal.dlrrd.gov.za/cdngiportal/`. Clip-Zip-Ship + ERDAS APOLLO catalog. Old WMS `http://apollo.cdngiportal.co.za/ApolloCatalogWMSPublic/...` **did not return** within 25 s (probe hung). Old host `http://www.cdngiportal.co.za/cdngiportal/` now an “Important Notice” stub. | Portal HTML **live-verified**. Historical mosaic WMS **catalog-only / stale**. Sheet-level vintages **catalog-only** until a GetCapabilities or catalog browse is completed. | NGI scale of fees (GG notices). Viewing on the portal is public; extracts and prints are charged. | The only official *multi-decade* South African aerial archive. Needed for Buffalo City and Mangaung, where no municipal vintage stack is public. Sheet capture dates, not calendar months, set the dating grain. |
| All three | CSG Property Viewer (erven / farms / holdings) | Chief Surveyor-General, DALRRD | Continuously updated cadastral database. Snapshot date UNKNOWN. | Vector, surveyed cadastral scale (erven typically 1:1 000–1:4 000 **假设**) | National | ArcGIS MapServer REST: `https://csggis.dlrrd.gov.za/server/rest/services/Property_Viewer/MapServer` (v11.3). Viewer portal: `https://csg.dlrrd.gov.za/`. Geocoders under `PropertyViewer/` (Erven, Holdings, Parent_Farm, SG_Codes). Old host `csggis.drdlr.gov.za` **NXDOMAIN**. | **Live-verified** (folders, layer list, capabilities `Map,Query,Data`) | Official CSG data. Viewing public; bulk digital extracts via SG offices / fees. | National erf/farm polygons + 21-digit SG codes. Join key for municipal cadastre and for any future SSEG register. Not a date of construction. |
| All three | GISCOE South Africa Cadastre + 50 cm imagery overlay | GISCOE (private republisher of CSG + NGI) via Esri SA | UNKNOWN | Cadastre: vector. Nested layer “South Africa 50cm Imagery” (GSD not separately advertised; name says 50 cm) | National | MapServer: `https://imagery.esri-southafrica.com/server/rest/services/GISCOE/South_Africa_Cadastre/MapServer` | **Live-verified** (layer list includes Cadastre, Erven, Parcels, “South Africa 50cm Imagery”) | Copyright text “GISCOE”. Not an open licence. | Convenient national cadastre+imagery mashup. Do not treat as a dating vintage. Prefer CSG + NGI sources of truth. |
| All three | Google Open Buildings v3 footprints | Google Research (Ghana / Open Buildings) | Single-epoch detections from high-res satellite; imagery years mixed. **Not** a dated install year. | Vector footprints from ~0.5 m class satellite | Africa incl. ZA (Gqeberha used in published WRI flood work; coverage expected for all three metros) | Download (S2 L4 CSV / GCS `gs://open-buildings-data/v3/`); Earth Engine | **Catalog-only** (site not fully fetched this run; dataset is public) | CC BY 4.0 | Roof-polygon prior for scoring chips. No capture date per building. |
| All three | Google Open Buildings 2.5D Temporal | Google Research | Annual 2016–2023 presence / count / height rasters | Raster, ~building-scale (not rooftop-PV GSD) | Global South incl. ZA | Earth Engine ImageCollection; GCS | **Catalog-only** | CC BY 4.0 **假设** (same family as v3) | Coarse “building appeared” signal. Too coarse to date a rooftop array, useful as a negative-control for new houses. |
| All three | Microsoft Global ML Building Footprints | Microsoft | UNKNOWN (Bing imagery mix) | Vector | Includes South Africa | GitHub country extracts; ODbL | **Catalog-only** | ODbL | Second building-footprint prior, uncorrelated with Google. Same limitation: no date. |
| All three | Esri World Imagery / Wayback | Esri / Maxar / community | Mixed commercial vintages; Wayback stores historical WMTS | Typically 0.3–0.6 m in SA metros **假设** | Global | ArcGIS REST / Wayback WMTS | **Catalog-only** this run | Esri / Maxar terms; not redistributable as raw tiles | Complementary to GEHI. Maxar dates are useful when a Wayback epoch exists, but licensing is stricter than NGI. |
| All three | GEHistoricalImagery (GEHI) | This repo’s sole download provider | Per-anchor capture dates | Chip GSD depends on GEHI vintage / zoom (z19 primary) | Global, queried per seed | Internal pipeline (not a municipal portal) | In-repo (not re-probed here) | Google Earth historical terms | Default true-date stack for Buffalo City and Mangaung until a municipal/NGI vintage stack is in hand. |
| All three | SSEG Online Application Portal | SALGA / AMEU / GIZ SSEG programme (`apply.sseg.org.za`) | Application dates (if a municipality participates and exports) | Tabular, not a GIS layer | Participating municipalities. Web search index listed **Buffalo City** and **Nelson Mandela Bay**. Mangaung not seen in the snippet. Page is JS-rendered; this run did not parse the live table. | Login wall for officials; public application UI | **Catalog-only** for the participant list | Municipal personal-information / PAIA. Not an open geodata dump. | If a municipality will export registered SSEG with GPS + commission date, that is a labelled true-date sample. No public spatial layer was found. |

No public **SSEG GIS register**, **municipal LiDAR point cloud**, or **dated building-permit GIS with PV attributes** was found for Buffalo City or Mangaung. NMBM has LiDAR-derived footprints and a building-change layer (below), still not an SSEG register.

---

## 1. Nelson Mandela Bay Metro (Gqeberha / Port Elizabeth)

**Official GIS page:** [https://www.nelsonmandelabay.gov.za/page/gis](https://www.nelsonmandelabay.gov.za/page/gis)

**Public viewer (Web AppBuilder 2.26, title “NMBM Public Viewer”):** [https://gis.nelsonmandelabay.gov.za/agsviewercm1/](https://gis.nelsonmandelabay.gov.za/agsviewercm1/)  
(The 2017 manual still cites `/AGSViewerCM/`; that path is **404**. Use `agsviewercm1`.)

**Shapefile downloads (Document Library catID=66):** allotments, registered full-title, sectional title, proposed cadastral, street addresses, 2021 wards, 5 m contours, plus waste/cemetery layers.

**Internal REST (via public resource-proxy):**

- Image/map: `https://hq-gis-01.ds.local/image/rest/services/...`
- Vector/ops: `https://hq-gis-01.ds.local/arcgis/rest/services/...`
- Proxy: `https://gis.nelsonmandelabay.gov.za/resource-proxy/proxy.ashx?<full-internal-url>`

Public viewer operational layers only expose **Aerials 2018** and **Aerials 2022**. The ImageServer catalog behind the proxy has the full vintage list.

| Municipality | Layer name | Data source / publisher | Capture time | Resolution / GSD or vector scale | Spatial coverage | Access method | Live-verified 2026-09-06 vs catalog-only | License / terms | Why it matters for PV dating |
|---|---|---|---|---|---|---|---|---|---|
| NMBM | Aerial_1997 | NMBM Corporate GIS (ImageServer `Aerial/Aerial_1997`) | 1997 (year from service name) | 0.25 m RGB | Smaller than later vintages (TM25 m extent ~29.2–66.2 km E, 3.725–3.770 Mm S) — **core PE, not full metro** | ImageServer via resource-proxy. Capabilities `Image,Metadata,Catalog`. No Download capability advertised. | **Live-verified** | Municipal copyright. Public stream through the proxy is undocumented; treat bulk extract as PAIA / GIS office. Website T&Cs apply to the viewer. | Pre-rooftop-PV baseline. Too early for SA residential PV; useful as “definitely absent”. |
| NMBM | Aerial_2004 | NMBM Corporate GIS | 2004 | 0.25 m RGB | Broader urban (TM25 ~17–83 km E, 3.714–3.770 Mm S) | Same proxy ImageServer | **Live-verified** | Same | Early baseline. Rooftop PV still rare. |
| NMBM | Aerial_2007_2004 | NMBM Corporate GIS | Named 2007/2004 mix — **假设** a 2007 update mosaicked onto 2004 | 0.25 m RGB | Same envelope as 2004 | Same | **Live-verified** (name only; no per-raster date table pulled) | Same | Transitional vintage. Confirm seam dates before using as a 2007 epoch. |
| NMBM | Aerial_2009 | NMBM Corporate GIS | 2009 | 0.10 m RGB | Urban core (TM25 ~20.0–77.9 km E, 3.728–3.769 Mm S) | Same | **Live-verified** | Same | First 10 cm municipal epoch. Rooftop arrays become visually countable. |
| NMBM | Aerial_2009_NGI | NMBM republish of NGI | 2009 (NGI) | ~0.50 m (`pixelSize` ≈ 4.49e-6 deg in EPSG:4326) | Wider than municipal 10 cm (lon 25.00–26.00, lat −34.06 to −33.50) | Same | **Live-verified** | NGI + municipal | Fills rural/Coega fringe that 10 cm 2009 misses. |
| NMBM | Aerial_2011 | NMBM Corporate GIS | 2011 | 0.10 m RGB | Urban (TM25 ~20–78.5 km E, 3.728–3.769 Mm S) | Same | **Live-verified** | Municipal | Second 10 cm epoch. 2009→2011 is a usable 2-year panel step. |
| NMBM | Aerial_2013 | NMBM Corporate GIS | **Nov 2012 – Feb 2013** (service description) | 0.10 m RGB | Same as 2011 | Same; capabilities include Mensuration | **Live-verified** | Municipal | Best-dated vintage in the stack. Use as the 2013 epoch. |
| NMBM | Aerials_2017 | NMBM Corporate GIS | 2017 | 0.05 m RGB | Near-metro (TM25 ~17.8–80.9 km E, 3.714–3.770 Mm S) | Same | **Live-verified** | Municipal | First 5 cm epoch. Pairs with LiDAR 2017 footprints. |
| NMBM | Aerials_2018 | NMBM Corporate GIS | 2018 | 0.05 m RGB | Slightly smaller than 2017/2022 (TM25 ~18.8–78.9 km E, 3.727–3.769 Mm S) | Same. Also an operational layer in the public viewer. | **Live-verified** | Municipal | Viewer-default historical layer. 2017 vs 2018 is only a 1-year step — check actual flight months before treating as independent. |
| NMBM | Aerials_2022 | NMBM Corporate GIS | 2022 | 0.05 m RGB | Same envelope as 2017 | Same. Public viewer operational layer. Web map tags: “Imagery 2022”, “Imagery 2018”. | **Live-verified** | Municipal | Latest municipal ortho. Strong “present” epoch for 2018–2022 change. No 2019–2021 municipal vintage found. |
| NMBM | Obliques_2022 | NMBM Corporate GIS | 2022 | ~0.033 m RGB, **oblique** (not nadir ortho) | Wider than ortho (TM25 ~13.1–85.4 km E) | ImageServer `Obliques/Obliques_2022` via proxy | **Live-verified** | Municipal | Facade/roof confirmation, not a dating grid. Do not mix with nadir scores. |
| NMBM | Hillshade | NMBM Corporate GIS | UNKNOWN | 20 m | Metro-ish | ImageServer | **Live-verified** | Municipal | Terrain only. Not PV-relevant. |
| NMBM | Topo50 | NMBM Corporate GIS (NGI 1:50 000 raster **假设**) | UNKNOWN | ~4.2 m | Metro-ish | ImageServer | **Live-verified** | NGI / municipal | Context only. |
| NMBM | Registered Cadastral | NMBM Corporate GIS (`NMBM_PublicWebmap` L2) | Continuously maintained. Snapshot UNKNOWN. | Vector erf polygons. Fields include `SG21Code`, `MetroTag`, `ErfNo`, `Allotment`, `ZoningScheme`, `Ward`, `Suburb`, `StreetName`, `StreetNo`, `ILIS_KEY`. | Metro. **243 655** features (`returnCountOnly`). | MapServer query via proxy; shapefile zip `cadastralregistered_*.zip` from Document Library. Search widget uses `MetroTag`. | **Live-verified** | Municipal GIS download (disclaimer click-through on Document Library). | Parcel universe + join keys for any SSEG/billing extract. |
| NMBM | Sectional Cadastral | NMBM (`NMBM_PublicWebmap` L3) | UNKNOWN | Vector | Metro | Same + `cadastralsectionalshp_*.zip` | **Live-verified** | Same | Sectional-scheme roofs (blocks of flats / complexes). |
| NMBM | Proposed Cadastral | NMBM (`NMBM_PublicWebmap` L4) | UNKNOWN | Vector | Metro | Same + `cadastralproposed_*.zip` | **Live-verified** | Same | Future stands; low value for historical dating. |
| NMBM | Allotments / Wards 2021 / street numbers | NMBM | Wards: 2021 demarcation. Addresses: UNKNOWN. | Vector | Metro | Viewer + shapefile zips (`allotment_*.zip`, `wards2021_*.zip`, `addresspoints_*.zip`) | **Live-verified** | Same | Sampling frame and address geocoding. |
| NMBM | Building Footprints LIDAR 2017 | NMBM (`NMBM_Building_Plans` L22) | 2017 (name). Elevation field present. | Vector polygons. Fields: `LAYER`, `ELEVATION`, `AREA`. | Metro. **355 880** footprints. | MapServer query via proxy. **Not** in the public viewer layer list. | **Live-verified** | Municipal. Internal-ops service exposed by the same proxy; more sensitive than the public webmap. | Roof outlines contemporaneous with 5 cm 2017 ortho. Best municipal building prior. Not a PV layer. |
| NMBM | Change Detection 2009 vs 2013 vs 2017 | NMBM (`NMBM_Building_Plans` L24) | Change among 2009 / 2013 / 2017 aerials | Vector on cadastral keys. Fields: `SG21Code`, `ErfNo`, `MetroTag`, `CHANGED`, `CHECKED`, `CHANGETYPE`, `CD_Years`. | Metro. **160 637** features. | Same internal MapServer via proxy | **Live-verified** | Municipal / internal | Parcel-level *building* change, not PV change. Can mask new houses so a 2017-present PV appearance is not confused with a new roof. |
| NMBM | Building sketch plans / floor plates | NMBM (`NMBM_Building_Plans` L6–L17) | UNKNOWN (plan-status, not as-built date) | Vector | Metro | Same | **Live-verified** (layer names only) | Municipal | Weak dating signal. Plan approval ≠ install year. |
| NMBM | Cadastral zonings (PE / Despatch / Uitenhage / Kwanobuhle / Khayamnandi) | NMBM (`NMBM_Cadastral_Zonings`) | UNKNOWN | Vector | Metro | Same | **Live-verified** | Municipal | Land-use filter (residential vs industrial vs informal). |
| NMBM | 5 m contours | NMBM | UNKNOWN | Vector, 5 m interval | Metro | Shapefile `contours5m_*.zip` | **Live-verified** (download listed) | Municipal | Roof-pitch prior is weak at 5 m. LiDAR footprints are better. |
| NMBM | Electricity / SSEG | NMBM Electricity & Energy | SSEG applications exist (form + GPS; email `solarapplications@mandelametro.gov.za`). REST folder `Electricity` returns **Token Required (499)**. | No public GIS layer found | Metro supply area | PDF form on website; SALGA portal may include NMBM (**catalog-only**) | Electricity REST **live-verified as locked**. SSEG spatial register **not found**. | Personal information / PAIA | A GPS+commission-date extract would be the labelled sample. It is not on the open GIS. |
| NMBM | Other internal MapServers (billing, ownership, building-plans search, SG diagrams, housing, SDF, …) | NMBM | Mixed | Mixed | Metro | `/arcgis/rest/services` via proxy lists ~50 services. Electricity folder is token-gated; others responded without a token in this probe. | Catalog **live-verified**; contents not fully inventoried | Municipal / likely confidential | Do not scrape billing, debt, or customer-network layers. Cadastre + imagery + footprints are the PV-relevant subset. |

### What is usable for a true-date panel (NMBM)

NMBM is the only metro in this file with a **publicly streamable multi-vintage ortho stack**. Live ImageServers give 1997 (25 cm, core city), 2004/2007 (25 cm), 2009 NGI (~50 cm, wider), 2009/2011/2013 (10 cm), 2017/2018/2022 (5 cm). That is a real presence/absence panel at rooftop-PV GSD, with a 4-year hole after 2018 and no post-2022 municipal epoch. Pair 2017 5 cm with the 355 880 LiDAR footprints and the 160 637 parcel change-detection records so new buildings are not scored as new PV. Cadastre (`SG21Code` / `MetroTag`, 243 655 erven) is the sampling frame. There is **no** public SSEG spatial register; electricity REST is locked. GEHI still fills 2019–2021 and post-2022. If this stack is ignored, NMBM gets treated like Buffalo City — throwing away the only municipal true-date imagery found in EC/FS.

---

## 2. Buffalo City Metro (East London)

**Homepage GIS link (2026-09-06):** `https://gis.buffalocity.gov.za/portal/apps/sites/#/gis-site-1-1`  
**On-prem probe:** TCP timeout at 25 s for `/`, `/portal/sharing/rest`, `/server/rest/services`, `/arcgis/rest/services`. Treat the corporate GIS as **advertised but unreachable** from this network today.

**Public AGOL City Map (owner `CamillaD_BCMMGIS`, org `VeXHjqBHnpY8EFv9`):**

- Feature service: `https://services.arcgis.com/VeXHjqBHnpY8EFv9/arcgis/rest/services/City_Map/FeatureServer` (capabilities `Query,Extract`)
- Web AppBuilder: `https://bcmmgis.maps.arcgis.com/apps/webappviewer/index.html?id=2cf9645f165b48159d5da53726fc1d93`
- Experience Builder: `https://experience.arcgis.com/experience/8361942dd03d4d34ac8a75acfa9e0a65/`
- Item updated Jun 2024. Description: “Base map of the Buffalo City Metropolitan Municipality.”

**2021 Public Web Viewer user guide** (PDF on buffalocity.gov.za) documents a Web AppBuilder with Identify / Layers / cadastre / infrastructure. That viewer’s live URL was not recovered; it may now sit behind the timed-out portal.

**GIS sales (2025/26 tariff book, live PDF):** digital data sold per 3 km² tile (aerial 40% of tile value, cadastre 20%, contours 30%, road centreline 10%). Paper aerial prints are priced. This is a **paid extract** channel, not an open image service.

| Municipality | Layer name | Data source / publisher | Capture time | Resolution / GSD or vector scale | Spatial coverage | Access method | Live-verified 2026-09-06 vs catalog-only | License / terms | Why it matters for PV dating |
|---|---|---|---|---|---|---|---|---|---|
| BCMM | BCMM Cadastre (City_Map L8) | BCMM GIS Unit / AGOL | UNKNOWN (service published/updated 2024-06) | Vector erven. **199 783** polygons. | Metro (item extent ~27.04–28.18 E, 33.29–32.66 S) | FeatureServer query + Extract | **Live-verified** | No licence text on the item (“no special restrictions” AGOL boilerplate). Still municipal IP. | Sampling frame. No building date. |
| BCMM | gisdb.LDR.cadastre_sectional_scheme (L7) | BCMM | UNKNOWN | Vector | Metro | Same | **Live-verified** | Same | Sectional-title roofs. |
| BCMM | Urban Edge / Wards 2021 / BCMM Boundary | BCMM | Wards: 2021. Urban edge: UNKNOWN | Vector | Metro | Same (L9, L17, L18) | **Live-verified** | Same | Sampling / rural-urban split. Large rural hinterland should not be treated as rooftop-PV dense. |
| BCMM | Contour (50 cm) and Contour (1 m) | BCMM | UNKNOWN | Vector contours, 0.5 m and 1 m interval | Metro (likely urban mapping area **假设**) | Same (L10, L11) | **Live-verified** (layer names; no densify/coverage probe) | Same | 0.5 m contours imply a photogrammetric or LiDAR source exists internally. The source DSM/LAS is **not** public. Weak rooftop-pitch prior. |
| BCMM | Electricity / Water / Sewers / Servitudes / facilities | BCMM | UNKNOWN | Vector networks and POIs | Metro | Same | **Live-verified** | Same | Electricity *lines* are not an SSEG register. Do not infer PV from LV traces. |
| BCMM | Corporate GIS portal (on-prem aerials, full cadastre, possibly historical orthos) | BCMM GIS Unit | 2021 manual + 2025/26 tariffs imply current aerial photography is sold as tiles. Vintage years **UNKNOWN**. | Tariff “aerial photo” product; GSD UNKNOWN. 假设 similar to other SA metros (10–15 cm urban). | Metro, tiled 3 km² | `https://gis.buffalocity.gov.za/` — **timeout**. Paid GIS office extracts. | Portal **unreachable 2026-09-06**. Existence of aerial product **catalog-only** (tariff book). | PAIA / tariff. Not open. | If the on-prem ImageServer is ever reachable, it is the first place to look for a dating stack. Today it contributes nothing. |
| BCMM | SSEG applications | BCMM (and SALGA `apply.sseg.org.za` per indexed municipality table) | Application/commission dates if exported | Tabular + GPS **假设** | BCMM electricity supply area (urban edge; Eskom supplies some hinterland) | Online portal (login). No GIS layer. | **Catalog-only** (JS page not parsed; web index listed BCMM) | Personal information | Potential labelled sample, not a layer. |
| EC DoH | Buffalo City Metro Settlements and Wards | Eastern Cape Department of Health (`lubabalo.cishe@echealth.gov.za`) | 2024-11 | Vector settlements + wards | BCMM | `https://www.arcgis.com/home/item.html?id=8ac6f43f34f2445daebb82efffc5b0e1` | **Live-verified** (item JSON) | Not specified | Admin geography only. |

### What is usable for a true-date panel (BCMM)

Nothing municipal currently supplies a dated ortho time series. The live public object is a 2024 vector basemap: ~200 k cadastre polygons, sectional schemes, 0.5 m/1 m contours, and utility lines. That is a sampling frame and a hint that BCMM *holds* photogrammetry internally (they sell aerial tiles; they published 50 cm contours), but the imagery REST is behind a portal that timed out. A true-date panel for East London / Mdantsane / Qonce therefore depends on **NGI 25 cm sheet vintages + GEHI**, with CSG/BCMM cadastre as the join key. Ignoring that gap would look like we have “Buffalo City GIS” when we only have polygons. Do not wait on the on-prem portal for production scoring; treat a later portal revival as an upgrade, not a blocker.

---

## 3. Mangaung Metro (Bloemfontein)

**No live `gis.mangaung.co.za` / `maps.mangaung.co.za`.** The municipal website does not expose a GIS viewer link (homepage scrape, 2026-09-06).

**Public AGOL (owner `Chuma.twetwa@mangaung.co.za`, org `ho5ShYx4j27gPCg4`).** 60 public items. PV-relevant subset:

- Mangaung Municipality Public Viewer — `https://services6.arcgis.com/ho5ShYx4j27gPCg4/arcgis/rest/services/Mangaung_Municipality_Public_Viewer/FeatureServer` (Query only)
- GIS PLATFORM ONLINE — `.../GIS_PLATFORM_ONLINE/FeatureServer` (HOD presentation, 2024-04-29)
- Mangaung Billing Stands — `.../Mangaung_Billing_Stands/FeatureServer`
- MMM_Cadastre — `.../MMM_Cadastre/FeatureServer`
- Web Experience “MMM INTERNAL MAP VIEWER” — `https://experience.arcgis.com/experience/f7ed049a984e4a1bba1afb9cb08a9e03` (name says internal; item is listed public)

No ImageServer, no aerial vintage, no building footprints, no LiDAR, no SSEG layer in the public item list.

Electricity in Mangaung is largely **CENTLEC** (municipal entity), not a GIS-published SSEG register.

| Municipality | Layer name | Data source / publisher | Capture time | Resolution / GSD or vector scale | Spatial coverage | Access method | Live-verified 2026-09-06 vs catalog-only | License / terms | Why it matters for PV dating |
|---|---|---|---|---|---|---|---|---|---|
| MMM | Cadastre (Public Viewer L20) | Mangaung GIS / AGOL | UNKNOWN | Vector. **230 420** polygons. | Metro (Bloemfontein–Botshabelo–Thaba Nchu + rural; SR `Mangaung_TM27`) | FeatureServer Query (no Extract capability advertised) | **Live-verified** | Empty `licenseInfo`. AGOL public. Still municipal IP. | Sampling frame. Largest public cadastral count of the three metros. |
| MMM | Cadastral (GIS_PLATFORM_ONLINE L28) / MMM_Cadastre | Same | UNKNOWN | Vector | Metro | Same org FeatureServers | **Live-verified** | Same | Duplicate/overlapping cadastre publications. Prefer Public Viewer L20 unless schema differs. |
| MMM | Mangaung Stands / Meter / No Meter / Prepaid Water / Sectional Title (Billing Stands) | Mangaung billing GIS | UNKNOWN (item created ~2026-02 from epoch 1770024869000; last edited 2026-08) | Vector stands + meter flags | Metro | FeatureServer Query | **Live-verified** (layer names) | Same; **billing attributes are sensitive** | Metered vs prepaid is not PV. Do not use as an SSEG proxy. Cadastre/sectional layers inside this service are redundant with Public Viewer. |
| MMM | LUMS / Urban_Edge_2019 / Urban_BFN / Holdings / Future_Residential_2019 | Mangaung SDF/LUMS | 2019 in several layer names | Vector | Metro | GIS_PLATFORM_ONLINE | **Live-verified** | Same | Urban-edge filter for rooftop vs farm-scale PV. 2019 edge is stale relative to 2026. |
| MMM | Suburbs / Wards / Roads / sewer-water networks / facilities | Mangaung | UNKNOWN | Vector | Metro (separate Bloemfontein / Botshabelo / Thaba Nchu sewer layers) | Public Viewer | **Live-verified** | Same | Geography only. |
| MMM | Historical aerial / ortho | Not found in public AGOL. NGI national programme covers FS 2926 sheets. | NGI cycle years (see national table) | 0.25 m (from 2017) / 0.50 m (2008–2016) | Metro via NGI, not via MMM | NGI portal / Esri SA ImageServer | Municipal aerial **absent**. NGI **live** as national. | NGI fees | No municipal dating stack. |
| MMM | SSEG / CENTLEC register | CENTLEC / Mangaung | UNKNOWN | No GIS layer found | CENTLEC supply area | Not found | **Not found** | — | Labelled PV dates would have to be requested from CENTLEC. |

### What is usable for a true-date panel (Mangaung)

Public Mangaung GIS is **vector-only**: ~230 k cadastre polygons, 2019 SDF/LUMS, billing stands, and engineering networks. That is a good erf universe for Bloemfontein / Botshabelo / Thaba Nchu and a land-use filter. It is not a presence/absence panel. There is no municipal ortho vintage, no footprints, no LiDAR, no SSEG map. A true-date panel here is **NGI 25 cm sheet dates + GEHI chips**, joined to MMM cadastre. Ignoring that would mean scoring Mangaung as if “GIS PLATFORM ONLINE” were imagery — it is a HOD web map of pipes and zonings.

---

## Cross-city verdict (for a rooftop-PV dating panel)

| Need | NMBM | BCMM | Mangaung |
|---|---|---|---|
| Multi-vintage ortho at PV GSD | **Yes** (1997–2022, 5–25 cm, proxy REST) | No public imagery | No public imagery |
| Building footprints | **Yes** (LiDAR 2017, 356 k) | No | No |
| Cadastre (public query) | 244 k erven + shapefile zip | 200 k (AGOL Extract) | 230 k (Query only) |
| SSEG GIS | Not public (Electricity 499) | Not public | Not public |
| Practical dating stack today | Municipal orthos + GEHI to fill 2019–21 / post-2022 | GEHI + NGI sheets | GEHI + NGI sheets |

**What this says.** Eastern Cape’s two metros are not interchangeable: NMBM already has the municipal time series this project usually has to buy from GEHI; Buffalo City and Mangaung do not.

**Why it matters.** A true-date panel needs repeated *pictures*, not just erven. Cadastre without vintages only tells you where a roof *could* be.

**If ignored.** Research effort gets spent negotiating Buffalo City / Mangaung GIS offices for imagery that is not on the open internet, while NMBM’s 5 cm 2017/2018/2022 stack sits unused behind a public proxy.

---

## Probe log (short)

| Endpoint | Result 2026-09-06 |
|---|---|
| `https://gis.nelsonmandelabay.gov.za/agsviewercm1/config.json` | 200, Web AppBuilder 2.26, item `210a76f6d87740b4aebaaccbecbf67b0` |
| `https://gis.nelsonmandelabay.gov.za/resource-proxy/proxy.ashx?https://hq-gis-01.ds.local/image/rest/services?f=pjson` | 200, folders Aerial, Obliques; services CommunityServices, NMBM_PublicWebmap |
| `.../Aerial?f=pjson` | 10 ImageServers (1997–2022 + Hillshade + Topo50) |
| `.../NMBM_Building_Plans/MapServer/22/query?returnCountOnly=true` | 355 880 |
| `https://gis.buffalocity.gov.za/` and `/portal/sharing/rest` | timeout 25 s |
| `https://services.arcgis.com/VeXHjqBHnpY8EFv9/arcgis/rest/services/City_Map/FeatureServer` | 200, 21 layers, cadastre count 199 783 |
| `https://services6.arcgis.com/ho5ShYx4j27gPCg4/arcgis/rest/services/Mangaung_Municipality_Public_Viewer/FeatureServer` | 200, cadastre count 230 420 |
| `https://imagery.esri-southafrica.com/server/rest/services/NGI/RSA_NGI_AERIAL/ImageServer?f=pjson` | 200, 0.25 m |
| `https://csggis.dlrrd.gov.za/server/rest/services/Property_Viewer/MapServer?f=pjson` | 200 |
| `http://apollo.cdngiportal.co.za/ApolloCatalogWMSPublic/...GetCapabilities` | hung / no timely response |
| `https://ngigeoportal.dlrrd.gov.za/cdngiportal/` | 200 (HTML catalog; WMS URLs not extracted from obfuscated JS) |
