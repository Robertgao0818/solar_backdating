# KwaZulu-Natal municipal GIS / imagery catalog

Date: 2026-09-06. Live-probed unless marked 假设.
Purpose: layers that could construct a rooftop-PV presence/absence-over-time
panel for **eThekwini (Durban)** and **Msunduzi (Pietermaritzburg)**.

GSD (ground sample distance: 地面采样距离, 一个像素对应地面几米).
SSEG (small-scale embedded generation: 小型并网发电, 市政屋顶光伏登记).

Endpoints probed 2026-09-06:

- eThekwini ArcGIS Server 11.5: `https://gis.durban.gov.za/server/rest/services`
- eThekwini portal: `https://gis.durban.gov.za/portal`
- eThekwini Hub / Open Data: `https://gis-ethekwini.opendata.arcgis.com/`
- Durban Solar Map: `https://gis.durban.gov.za/solarmapviewer/`
- KZN CoGTA ArcGIS 11.3: `https://giswebmaps1.kzncogta.gov.za/server/rest/services`
- CD:NGI geoportal: `https://ngigeoportal.dlrrd.gov.za/cdngiportal/`
- NGI WMS mosaics: `https://BP-CDNGIAPOLLO.dlrrd.gov.za/erdas-iws/ogc/wms/CDNGI_MAP_MOSAICS`
- Msunduzi municipal site / GIS hostnames: timeout or NXDOMAIN (see §2)

Probe notes:

- `gis.durban.gov.za` TLS chain does not verify against the default CA store
  (`CERTIFICATE_VERIFY_FAILED`). HTTP redirects to HTTPS. Clients need an
  explicit CA or insecure SSL. Metadata below used unverified HTTPS, 30 s
  timeout, `?f=pjson`. **No imagery tiles were downloaded.**
- ImageServer `copyrightText` and `licenseInfo` were empty on every vintage
  probed. Portal `access=public`. Corporate GIS advertises an Open GIS Data
  download and a **Request Raster Data** form. 假设: REST view/query is
  tolerated; bulk extract and redistribution are not a CC-style grant.
- CRS for most eThekwini ImageServers is **WG31 / Lo31** (Transverse Mercator,
  central meridian 31°E, metres; Hartebeesthoek94 or a WGS84-labelled twin).
  `AerialPhotography2018` and `LowRangeAerialPhotography2025` are
  `wkid=102100` (Web Mercator). Do not mix those without a project step.

---

## 1. eThekwini Metropolitan Municipality (Durban)

Publisher of the municipal stack: eThekwini Corporate GIS
(`gis.durban.gov.za`). Photogrammetry unit also flies LiDAR and obliques
(Esri SA User Conference 2025, Luvo Qumba: aerials 10–50 cm back to 1931;
LiDAR from 2016 on a ~3-year cycle; 2025 drone + CD:NGI data-sharing
agreement). LiDAR itself is **not** on the public REST catalog.

### 1.1 Nadir aerial ImageServers (true-date candidates)

`pixelSizeX` is native mosaic GSD from REST. Capture year is the service
name; month is UNKNOWN unless noted. Coverage “metro” means the typical
WG31 box ~x −58 000…+18 000, y −3 350 000…−3 265 000 (~76 × 88 km) —
eThekwini municipal area, not CBD-only.

| municipality | layer name | source | capture time | GSD | coverage | access | live 2026-09-06 | license | why it matters for PV dating |
|---|---|---|---|---|---|---|---|---|---|
| eThekwini | `RasterCatalogueProject/AerialPhotography1994` | eThekwini GIS | 1994 | **0.80 m**, 1 band (greyscale) | metro-ish | ArcGIS ImageServer | yes | empty copyright | pre-PV; heritage only |
| eThekwini | `AerialPhotography1997` | eThekwini GIS | 1997 | **1.0 m**, RGB | **partial** (x −24k…+12k; smaller than later metros) | ImageServer | yes | empty | too coarse + incomplete for rooftop PV |
| eThekwini | `RasterCatalogueProject/AerialPhotography2001` | eThekwini GIS | 2001 | UNKNOWN | UNKNOWN | ImageServer | **service 500** (`GISService not instantiated`) | empty | do not use until the service instantiates |
| eThekwini | `RasterCatalogueProject/AerialPhotography2001_R` | eThekwini GIS | 2001 | **0.30 m**, RGB | west-truncated (xmin −30.5k vs −43.5k) | ImageServer | yes | empty | first 30 cm year; coverage hole on the far west |
| eThekwini | `RasterCatalogueProject/AerialPhotography2002` | eThekwini GIS | 2002 | **0.30 m** | **partial** (xmax −9.7k; misses the coast) | ImageServer | yes | empty | not a citywide panel year |
| eThekwini | `RasterCatalogueProject/AerialPhotography2002b` | eThekwini GIS | 2002 | **0.30 m** | same partial box as 2002 | ImageServer | yes | empty | sibling of 2002; treat as one vintage |
| eThekwini | `RasterCatalogueProject/AerialPhotography2003` | eThekwini GIS | 2003 | **0.54 m** | metro-ish | ImageServer | yes | empty | coarser than 2001_R; borderline for small arrays |
| eThekwini | `RasterCatalogueProject/AerialPhotography2004b` | eThekwini GIS | 2004 | **0.30 m** | metro | ImageServer | yes | empty | usable 30 cm year |
| eThekwini | `RasterCatalogueProject/AerialPhotograph2005` | eThekwini GIS | 2005 | **0.35 m** | metro | ImageServer | yes | empty | note spelling `Photograph` not `Photography` |
| eThekwini | `RasterCatalogueProject/AerialPhotography2006` | eThekwini GIS | 2006 | **0.30 m** | metro | ImageServer | yes | empty | |
| eThekwini | `RasterCatalogueProject/AerialPhotography2007` | eThekwini GIS | 2007 | **0.30 m** | metro | ImageServer | yes | empty | |
| eThekwini | `RasterCatalogueProject/AerialPhotography2008` | eThekwini GIS | 2008 | **0.30 m** | metro | ImageServer | yes | empty | |
| eThekwini | `RasterCatalogueProject/AerialPhotography2009` | eThekwini GIS | 2009 | **0.30 m** | metro | ImageServer | yes | empty | |
| eThekwini | `RasterCatalogueProject/AerialPhotography2010` | eThekwini GIS | 2010 | **0.30 m** | metro (xmax slightly further east, +20.8k) | ImageServer | yes | empty | |
| eThekwini | `RasterCatalogueProject/AerialPhotography2011` | eThekwini GIS | 2011 | **0.30 m** | same as 2010 | ImageServer | yes | empty | |
| eThekwini | `RasterCatalogueProject/AerialPhotography2012` | eThekwini GIS | 2012 | **0.30 m** | metro | ImageServer | yes | empty | |
| eThekwini | `RasterCatalogueProject/AerialPhotography2013` | eThekwini GIS | 2013 | **0.30 m** | metro | ImageServer | yes | empty | |
| eThekwini | `RasterCatalogueProject/AerialPhotography2014` | eThekwini GIS | 2014 | **0.30 m** | metro | ImageServer | yes | empty | |
| eThekwini | `RasterCatalogueProject/AerialPhotography2015` | eThekwini GIS | 2015 | **0.30 m** | metro (slightly wider west, xmin −58.8k) | ImageServer | yes | empty | last 30 cm year before the 10 cm jump |
| eThekwini | `RasterCatalogueProject/AerialPhotography2016` | eThekwini GIS | 2016 | **0.09 m**, RGB | metro | ImageServer | yes (catalog query count 3 406) | empty | first ~10 cm year; rooftop-resolvable |
| eThekwini | `oblique2016/Oblique2016` | eThekwini GIS | 2016 | ~0.066 m, RGB + camera pose (`SensorAzimuth`, `CAMERAID`) | metro-ish; capabilities include **Download** | ImageServer | yes | empty | **oblique, not nadir**. QA overlay only; do not feed the nadir detector |
| eThekwini | `RasterCatalogueProject/AerialPhotography2018` | eThekwini GIS | 2018 | **0.10 m** | metro, but **EPSG:3857** | ImageServer | yes | empty | CRS trap vs the WG31 stack |
| eThekwini | `2019/AerialPhotography` | eThekwini GIS | 2019 | **0.09 m** (“MRF mosaic”) | metro | ImageServer | yes | empty | |
| eThekwini | `AerialPhotography/AerialPhoto2020` | eThekwini GIS | 2020 | **0.10 m** | metro | ImageServer | yes | empty | prefer this over the root duplicate |
| eThekwini | `Aerial_Imagery_2020` | eThekwini GIS | 2020 | **0.10 m** | same box as AerialPhoto2020 | ImageServer (root) | yes | empty | **duplicate** of AerialPhoto2020 |
| eThekwini | `AerialPhotography/AerialPhotography20212022` | eThekwini GIS | 2021–2022 (name; month UNKNOWN) | **0.10 m** | metro | ImageServer | yes | empty | **not a single flight**. Do not use as one panel year. This is the Solar Map viewer backdrop |
| eThekwini | `AerialPhotography/AerialPhotography2022` | eThekwini GIS | 2022 | **0.10 m** | metro (catalog query count 4 — large mosaic tiles) | ImageServer | yes | empty | clean 10 cm year |
| eThekwini | `AerialPhotography2022RSM` | eThekwini GIS | 2022 | **0.10 m** | same extent as AerialPhotography2022 | ImageServer (root) | yes | empty | 假设 resampled/reprojected twin; do not double-count |
| eThekwini | `Hosted/AerialPhotography2021_2022Tiles` | eThekwini GIS | 2021–2022 | UNKNOWN (tiled MapServer) | metro-ish (`ORTHO_GRID` companion) | Hosted MapServer | yes | empty | display tiles, not a dating vintage |
| eThekwini | `AerialPhotography/AerialPhotography2023` | eThekwini GIS | 2023 | **0.10 m** | **west only** (xmax −13 000 vs +18 000). Misses the coastal core (Durban CBD is x ≈ +2 000 in Lo31). Catalog count 1 220 | ImageServer | yes | empty | **cannot be a citywide panel year** |
| eThekwini | `AerialPhotography/LowRangeAerialPhotography2025` | eThekwini GIS | 2025 | **0.05 m**, 4 bands | **~11 × 15 km coastal patch** (~31.03–31.13°E, ~−29.86 to −29.97°S; Web Mercator). 137 catalog items | ImageServer | yes | empty | 假设 drone / low-range AOI (Esri 2025: “Drone Acquisitions”). Not metro |

There is **no public 2017** ImageServer and **no citywide 2024**. The 30 cm
series is annual 2004–2015 (plus 2001_R); the 10 cm series is 2016, 2018,
2019, 2020, 2022.

### 1.2 Vector / derived layers

| municipality | layer name | source | capture time | scale / count | coverage | access | live 2026-09-06 | license | why it matters for PV dating |
|---|---|---|---|---|---|---|---|---|---|
| eThekwini | Photogrammetry Building Area (`WebViewers/Open_Spaces/MapServer/3`) | eThekwini GIS photogrammetry | `SYear` ∈ {2016, 2018, 2019} (plus 49 with `SYear=0`) | **846 698** polygons. `Class` + `RoofArea`. Counts: blank 524 363, Residential 197 606, Rural 85 642, Industry Light 14 539, Mix Use 11 042, Informal 4 671, … | metro | ArcGIS MapServer query | yes | empty | roof polygons for chip extraction. `SYear` is **building capture year**, not PV install date |
| eThekwini | HERE Building Area (`Open_Spaces/MapServer/4`) | HERE (via eThekwini) | UNKNOWN | 56 983 named polygons; `HEIGHT` mostly null on sample | metro | MapServer query | yes | HERE terms 假设 apply | commercial footprints; sparser than photogrammetry; not dated |
| eThekwini | Approved Parcels (`MapServices/Parcels/MapServer/0`; also `EXT_Cadastral` layer 17) | eThekwini GIS / SG | living cadastre (sample `STATUS=REGISTERED`, `DOCREF` e.g. SG 4224/1996) | **521 245** polygons. Fields: `PROPKEY`, `ERF`, `PORTION`, `SUBURB`, `PROPERTYID`, `STRNUM`/`STRNAME` | metro | MapServer query | yes | empty | seed join key (erf / address). Not a date |
| eThekwini | `Hosted/Approved_Parcels` | eThekwini GIS | living | 516 129 (slightly fewer than MapServices) | metro | FeatureServer Query,Extract | yes | empty | extractable twin of parcels |
| eThekwini | Zoning (`EXT_Cadastral_Services` layer 28) | eThekwini town planning | `APPROVALDATE`, `LANDUSECAPTUREDATE`, `AMENDMENTDATE` present | parcel-scale | metro | MapServer query | yes | empty | land-use filter (residential vs industrial). Dates are zoning events, not PV |
| eThekwini | `Warehouse/Cadastral_Services` | eThekwini GIS | living | parcels + `Valroll3` + leases | metro | MapServer **and** FeatureServer (`Query,Create,Update,Delete`) | yes | empty | valuation roll join. Editing caps on a public URL — treat as read-only |
| eThekwini | Addresspoint (`EXT_Cadastral` layer 1) | eThekwini GIS | UNKNOWN | point addresses | metro | MapServer | yes | empty | geocode seeds |
| eThekwini | 2 m contours (`EXT_Cadastral` layer 6; also Environmental_Services) | eThekwini GIS | UNKNOWN | 2 m interval polylines | metro | MapServer | yes | empty | terrain, not DSM of panels |
| eThekwini | Electrical_Services MapServer | eThekwini Electricity | UNKNOWN | HV/MV/LV lines, substations, CDUs, streetlights | metro (extent looks messy) | MapServer | yes | empty | network context, **not** an SSEG register |
| eThekwini | Solar Radiation (`Solar_Radiation/MapServer/0`) | eThekwini GIS | UNKNOWN | raster, GSD UNKNOWN | metro | MapServer raster | yes | empty | insolation surface for the Solar Map calculator; **not** installed PV |
| eThekwini | Durban Solar Map Viewer | eThekwini Energy / GIS | viewer live; backdrop = `AerialPhotography20212022` | n/a | metro | web app (`/solarmapviewer/`). Config still cites dead `silverfox.esriza-kzn.com` and `Solar_Final/MapServer` for find | yes (app + 2021/22 ImageServer); find widget 假设 broken | municipal disclaimer: estimates only, &lt;100 kW | **potential** calculator, not an inventory of existing arrays |
| eThekwini | SSEG requirements PDF | eThekwini Electricity | policy current (PDF live) | n/a | municipal electricity customers | download `ee-services.durban.gov.za/.../SSEG_requirements.pdf`; application via eServices | PDF live; **no spatial register on REST** | municipal | process exists; **no public GIS layer of approved SSEG** |
| eThekwini | eThekwini land use scheme 2020 | KZN CoGTA | 2020 | 320 540 polygons | eThekwini | `Land_use_schemes_2023/FeatureServer/6` | yes | empty; FeatureServer also advertises editing | provincial copy of zoning; prefer the municipal Zoning layer |
| eThekwini | Corporate GIS Hub / Open Data | eThekwini | n/a | Hub site live; Hub dataset search returned **0** items | metro | `gis-ethekwini.opendata.arcgis.com`; “Request Raster Data” advertised on the corporate site | Hub HTML live; download catalog empty on this probe | 假设 request-gated | TUMI still points at an old Hub building-footprint item (`7b5b229c9a0247cd8627798dec652ae1`); that ID was **not** re-verified as a live dataset |
| eThekwini | Digital Twin / 3D | eThekwini GIS | UNKNOWN | Visualization Twin linked from portal; Planning Twin “under construction” | 假设 metro | portal apps, login-ish | portal page live; 3D services **not** on anonymous REST | UNKNOWN | 假设 LiDAR-derived; not a panel vintage |
| eThekwini | LiDAR / DSM | eThekwini photogrammetry | 2016 onward, ~3-year cycle (Esri 2025) | UNKNOWN | 假设 metro | **not on public REST**; raster request form | catalog-only (Esri talk) | request-gated 假设 | would date panel *height* change if released; currently unusable |

`Imagery/` folder on the REST root exists and is empty. `Zoning/` folder
returns **Token Required**. Several Hosted `service_*` FeatureServers look
like Survey123 internals — ignored.

### What is usable for a true-date panel (eThekwini)

A **year-level** rooftop presence panel is constructable from the public
ImageServers, with the same year-only conservatism used on CoJ ISSUE-08.

- **10 cm class (in-domain for a rooftop detector):** 2016, 2018, 2019, 2020,
  2022. Skip `AerialPhotography20212022` (blended), `AerialPhotography2022RSM`
  and `Aerial_Imagery_2020` (duplicates), 2023 (west-only, misses the coastal
  core), and LowRange 2025 (drone patch).
- **30 cm class (GEHI-z19 equivalent, still rooftop-visible for larger
  arrays):** 2001_R, 2004, 2005, 2006–2015. Skip 2002/2002b (coast missing)
  and 2003 (0.54 m).
- **Do not use as panel years:** 1994 greyscale, 1997 1 m, 2001 (500), 2017
  (absent), 2023 partial, 2024 (absent), 2025 local.

That is a stronger municipal stack than Johannesburg’s 3-year cadence, and
weaker than Cape Town’s 5–8 cm months. Capture labels are year-named mosaics,
not per-roof flight days. Photogrammetry buildings (`SYear` 2016/2018/2019)
are the seed polygons; they are **not** PV dates. There is **no** public SSEG
point layer, so dating stays imagery-first.

---

## 2. Msunduzi Local Municipality (Pietermaritzburg)

No municipal ArcGIS/WMS host answered on 2026-09-06.

| hostname | result |
|---|---|
| `https://www.msunduzi.gov.za/` | timeout (30 s) |
| `https://gis.msunduzi.gov.za/` / `http://gis.msunduzi.gov.za/` | timeout |
| `maps.msunduzi.gov.za` | NXDOMAIN |

The municipality **does** fly and maintain spatial data internally. Evidence
is tenders, not services: SCM 43 of 17/18 (aerial mapping of the municipal
area, Land Survey contact Sibonelo Zulu); SCM 22 of 24/25 (develop and
maintain a cadastral dataset in ESRI, upload to the central municipal spatial
database). Those products are not on a public REST catalog.

SSEG process exists on paper (`Part 1_Msunduzi SSEG Requirements Amended
12112021.pdf`; application form asks for erf + GPS). **No spatial SSEG
register** was found.

### 2.1 Layers that actually cover Msunduzi

| municipality | layer name | source | capture time | GSD / scale | coverage | access | live 2026-09-06 | license | why it matters for PV dating |
|---|---|---|---|---|---|---|---|---|---|
| Msunduzi | Msunduzi land use scheme 2022 | KZN CoGTA (source data: Msunduzi / SiVEST scheme maps) | scheme map Dec 2021 / layer titled 2022; `DATE_EDIT` present | cadastral polygons, **98 699** features. Fields include `SG21CODE`, `SG26CODE`, `ERFNO`, `ZONING`, `LAND_USE_Z`, `OWNER`, `STREET_NAM`, `DEVELOPED` | Msunduzi LM box ~29.95–30.54°E, 29.52–29.83°S (Hartebeesthoek94 / 4148) | `https://giswebmaps1.kzncogta.gov.za/server/rest/services/Land_use_schemes_2023/FeatureServer/12` | yes | empty; service advertises editing — treat as read-only | erf/SG join + residential filter. **Not** a date. Owner field is PII — do not publish |
| Msunduzi | ILIS Base map 2024 | KZN CoGTA ILIS | 2024 name | wards, LM/DM boundaries, towns, roads, floodlines, census access-to-electricity | KZN-wide (includes Msunduzi) | `ILIS/ILIS_Base_map_2024/MapServer` | yes | empty | context only |
| Msunduzi | ILIS Land_information / Land_information_system / Land_valuation | KZN CoGTA | UNKNOWN | UNKNOWN | 假设 KZN parcels / valuation | FeatureServer + MapServer listed | **Token Required** (login wall) | UNKNOWN | would be the provincial cadastre if the token is obtainable; unusable anonymously |
| Msunduzi | ILIS_public_portal, MUNICIPALVIEWERS, Mapviewer, LUMSchemeMapping, BasemapILIS/Base_map, MPRA Registered_properties | KZN CoGTA | UNKNOWN | UNKNOWN | KZN | REST folder listed | **Token Required** | UNKNOWN | login wall |
| Msunduzi | CD:NGI digital aerials 0.50 m (2008–2016) and 0.25 m (2017–current) | CD:NGI / DLRRD | programmatic; ~40% of SA every 3 years, rest every 5 (NGI docs). **Per-sheet capture year for 2930/2931 UNKNOWN without catalog browse** | 0.50 m then 0.25 m RGB (+NIR). Accuracy spec 3 m @ 95% | national, including Msunduzi (sheets in the 2930 / 2931 block) | geoportal browse + Clip-Zip-Ship (`https://ngigeoportal.dlrrd.gov.za/cdngiportal/`). Old `apollo.cdngiportal.co.za` WMS **timed out**. Public WMS `CDNGI_MAP_MOSAICS` is **50k maps + landcover, not 25 cm aerials** | geoportal HTML live; mosaic WMS live but wrong product; 25 cm aerial WMS **not** confirmed as an anonymous layer | SDI Act custodian; NGI states datasets are available (historically “freely available” via Apollo). Current clip-zip is email-gated 假设 | **only** true-date rooftop-class imagery found for Pietermaritzburg. 25 cm is GEHI-z19 class, not 10 cm municipal |
| Msunduzi | NGI historical aerial archive 1926–2008 | CD:NGI | sheet-level, various photo scales | analogue → typically 0.75 m ortho | national | geoportal / walk-in / email | catalog (portal live; individual KZN vintages not enumerated in this probe) | NGI | pre-digital; 假设 too coarse / incomplete for PV |
| Msunduzi | NGI 1:10 000 orthophoto maps / 1:50 000 topo | CD:NGI | map edition, not flight day | 1:10k / 1:50k | 2930CB Pietermaritzburg etc. | geoportal; Avenza lists 2930CB (2013 edition) | portal live | NGI | cartographic, not a PV vintage |
| Msunduzi | Chief Surveyor-General cadastre (PMB office) | CSG / SG Pietermaritzburg | living | surveyed parcels, diagrams | KZN (PMB SG office) | `https://csg.dlrrd.gov.za/data.htm` Auto-Emailer; CIS spatial + document images | CSG page live | statutory; diagram downloads often paid | erf geometry independent of CoGTA LUS. Not a date |
| Msunduzi | Google Open Buildings | Google Research | mosaic of satellite epochs; **not a year** | polygon footprints, Africa | national / Msunduzi | download (CC-BY-4.0) | site live | CC-BY-4.0 | seed roofs where municipal photogrammetry is missing. **Not** a panel year |
| Msunduzi | OSM buildings (HOT export) | OSM contributors | volunteered, mixed dates | polygons | national | HDX | catalog | ODbL | sparse vs Google; same caveat |
| Msunduzi | Msunduzi SSEG requirements + application form | Msunduzi Electricity | policy Nov 2021 | n/a | municipal electricity customers | PDF on `msunduzi.gov.za` (site itself timed out this probe; PDFs still indexed) | catalog-only this probe (site timeout) | municipal | process exists; **no GIS register** |
| Msunduzi | 2017/18 aerial mapping contract | Msunduzi Land Survey | tender for capture; product year 假设 2018 | UNKNOWN | municipal area | not published | catalog-only (tender PDF) | municipal, not released | 假设 a 10–20 cm flight exists on an internal ESRI GDB. Unusable until they publish |

Hosted CoGTA layers around **Edendale** (`10KmBuffer_of____Edendale_Area_DALRRD_Settlements`, CSIR settlements) are settlement-planning buffers, not buildings.

### What is usable for a true-date panel (Msunduzi)

**Not yet.** There is no public municipal ImageServer. The only rooftop-class
imagery path is **CD:NGI 0.25 m / 0.50 m sheet mosaics** via the geoportal
Clip-Zip-Ship, and those vintages were **not** live-enumerated per 2930/2931
sheet in this probe (old Apollo WMS is dead; the public WMS is 1:50k maps).
Until someone browses the Apollo catalog for Pietermaritzburg sheets and
records capture years, Msunduzi cannot support a true-date panel the way
eThekwini, Cape Town, or Johannesburg can.

What *is* usable now: CoGTA `Msunduzi land use scheme 2022` (98 699 parcels)
as the seed frame, plus Google Open Buildings as roof polygons. Dating would
still be GEHI / Gemini / NGI-once-clipped — not a municipal aerial stack.
SSEG remains a PDF process, not a layer.

If the 2017/18 aerial-mapping contract produced a citywide ortho, that would
be the missing CoJ-style vintage. It is currently a tender, not a service.

---

## 3. Shared national layers (both cities)

| layer | source | capture time | GSD | access | live 2026-09-06 | PV-dating note |
|---|---|---|---|---|---|---|
| CD:NGI 0.25 m / 0.50 m digital aerials | DLRRD CD:NGI | 2008–2016 @ 0.50 m; 2017–current @ 0.25 m; sheet-level dates | 0.25–0.50 m | geoportal catalog + clip-zip (email). **Not** on `CDNGI_MAP_MOSAICS` WMS | portal live; anonymous 25 cm WMS **not** verified | fallback where municipal 10 cm is missing (Msunduzi; eThekwini 2017/2024 holes) |
| CD:NGI landcover 2013–14 / 2017–18 / 2020 / 2022 | CD:NGI | those years | 20–30 m class 假设 | WMS `CDNGI_MAP_MOSAICS` | yes | far too coarse for rooftop PV |
| CD:NGI 1:50k current / 2019 / 1st-edition mosaics | CD:NGI | map editions | 1:50k | same WMS | yes | cartographic |
| Sentinel-2 / Landsat | ESA / USGS | 5–16 day | 10–30 m | public | not re-probed | too coarse; already known |

---

## 4. Contrast with the census stack

| city | municipal true-date aerials sitting unused | SSEG GIS | building footprints | blocker |
|---|---|---|---|---|
| eThekwini | ~15 years of 30 cm (2004–2015) + 5 years of 10 cm (2016, 2018–2020, 2022) on a public ImageServer | none public | 847k photogrammetry polygons with `SYear` | TLS CA; empty license; 2023/2025 not citywide |
| Msunduzi | none public | none public | none municipal; CoGTA LUS parcels + Google Open Buildings | no GIS host; NGI 25 cm not yet sheet-dated |

eThekwini is the Cape Town / Johannesburg class of find: a public, year-named
nadir archive. Msunduzi is not. Do not design a KZN two-city panel that
pretends they are the same data-generating process.
