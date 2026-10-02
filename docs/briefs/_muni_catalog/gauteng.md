# Gauteng municipal GIS / imagery catalog (PV install-date panel)

Date probed: **2026-09-06**. Scope: City of Johannesburg (JHB), City of Tshwane
(Pretoria), City of Ekurhuleni. Purpose: list publicly reachable layers that
could help build a **true-date presence/absence panel** of rooftop PV
(orthophoto / satellite vintages with a known capture year, plus building
footprints, cadastre, SSEG registers).

**True-date aerial** here means a municipal (or NGI) flight whose capture year
is the service identity, not a blended commercial mosaic. That is the opposite
of Vexcel / GEHI mixed-vintage tiles. GSD (ground sample distance,
地面采样距离：一个像素代表地面多少米) is reported from REST `pixelSizeX`
plus the native CRS. Values on Hartebeesthoek Lo29 / TM29 are metres on the
ground. Values on EPSG:3857 are Web-Mercator metres (about 10% smaller than
true metres at 26°S). Publisher-stated centimetres, when they exist, are
preferred for interpretation.

## Method

- Web search for official GIS / e-Services / open-data portals.
- Live HTTP GET, 30 s timeout, metadata only (`?f=pjson` on ArcGIS REST;
  WMS `GetCapabilities` where advertised). **No imagery tiles downloaded.**
- Starting roots:
  - CoJ: `https://ags.joburg.org.za/server/rest/services?f=pjson` (ArcGIS
    10.91, TLS OK)
  - Tshwane: `https://e-gis001.tshwane.gov.za/server/rest/services?f=pjson`
    (ArcGIS 11.5, TLS OK) and viewer `https://e-gis002.tshwane.gov.za/E_GIS_Web/`
  - Ekurhuleni: `https://gis.ekurhuleni.gov.za/arcgis/rest/services?f=pjson`
    (ArcGIS 11.3, TLS OK) and viewer `https://gis.ekurhuleni.gov.za/`
- `gis.tshwane.gov.za` TLS failed even with an insecure retry; that hostname
  is not a live GIS root as of this probe.
- Unverified inferences are marked **假设**.

CoJ `AerialPhotography/{2000,2003,2006,2009,2012,2015,2019,2023}/ImageServer`
was already known. Those eight services were **not treated as a discovery**.
They are listed once in §1 as a re-verified baseline. The CoJ work below is
the `Solar`, `SatelliteImagery`, `FeatureServices`, `Development_Planning`
folder probe, plus e-Services download paths.

---

## 1. City of Johannesburg (JHB)

Publisher: City of Johannesburg Corporate Geo-Informatics (CGIS), Development
Planning. REST root
`https://ags.joburg.org.za/server/rest/services`. Viewer:
[e-Services Online Maps](https://eservices.joburg.org.za/onlinemaps)
(FAQ: registration is no longer required). Mobile viewer
`https://ags.joburg.org.za/cgismobi`. Terms:
[`T_C.pdf`](https://ags.joburg.org.za/cgismobi/T_C.pdf) — CoJ copyright; no
sublicence / sale / commercial redistribution without written consent; if
extracted, display “Copyright and all rights reserved by the City of Joburg”.

### 1.1 Already-known true-date aerials (re-verified, not a discovery)

| municipality | layer name | data source / publisher | capture time | resolution / GSD | spatial coverage | access method | live-verified 2026-09-06 vs catalog-only | license / terms | why it matters for PV dating |
|---|---|---|---|---|---|---|---|---|---|
| JHB | `AerialPhotography/2000` | CoJ CGIS | 2000 (year-only) | 0.25 m; 3-band RGB; Lo29 | Metro-wide (same envelope as later years; REST extent Lo29 x −136000…−73000, y −2943000…−2859000 ≈ 27.66–28.26°E, 26.57–25.86°S) | Public ArcGIS REST ImageServer `exportImage` | **live-verified** | CoJ copyright; T_C.pdf; digital copies on request `cgisprojects@joburg.org.za` | True-date aerial. Pre-rooftop-PV era; useful as a guaranteed-absent baseline. |
| JHB | `AerialPhotography/2003` | CoJ CGIS | 2003 (year-only; tariff sheet also says 25 cm) | 0.25 m RGB; Lo29 | Metro-wide, same envelope | REST ImageServer | **live-verified** | same | True-date aerial. Still pre-boom. |
| JHB | `AerialPhotography/2006` | CoJ CGIS | 2006 (year-only) | 0.20 m RGB; Lo29 | Metro-wide | REST ImageServer | **live-verified** | same | True-date aerial. |
| JHB | `AerialPhotography/2009` | CoJ CGIS | 2009 (year-only) | 0.15 m RGB; Lo29 | Metro-wide | REST ImageServer | **live-verified** | same | True-date aerial. First 15 cm vintage. |
| JHB | `AerialPhotography/2012` | CoJ CGIS | 2012 (year-only) | 0.15 m RGB; Lo29 | Metro-wide | REST ImageServer | **live-verified** | same | True-date aerial. |
| JHB | `AerialPhotography/2015` | CoJ CGIS | 2015 (REST `Imagery_Year` time extent is 2015-01-01 only) | 0.15 m RGB; Lo29 | Metro-wide | REST ImageServer | **live-verified** | same | True-date aerial. |
| JHB | `AerialPhotography/2019` | CoJ CGIS | 2019 (year-only; field `Imagery_Year` present) | 0.15 m RGB; Lo29 | Metro-wide | REST ImageServer | **live-verified** (already used in ISSUE-08/29) | same | True-date aerial. Production audit vintage. |
| JHB | `AerialPhotography/2023` | CoJ CGIS | **May 2023** flight (e-Services FAQ); products handed over Oct 2023; viewer from 9 Apr 2024 | 0.15 m RGB (publisher: 15 cm); EPSG:3857 extent 27.664–28.264°E, 26.572–25.858°S | Metro-wide 1 644 km² + Driefontein WWTW | REST ImageServer; WMS wrapper `WMS/CoJ_AerialPhotos2023` advertised on REST, SOAP GetCapabilities returned **Access is denied** | **live-verified** (already used in ISSUE-08/29) | Copyright text on service: “CoJ Aerial Photography 2023”; T_C.pdf | Best current true-date aerial. Year-only on the ImageServer itself; month comes from CGIS FAQ, not the raster attribute table. |

Time-enabled stack of the same vintages (2000–2019, **not** 2023):
`AerialPhotography/CoJ_Aerial` ImageServer, 0.15 m native, field
`Imagery_Year`, `timeExtent` 2000-01-01 … 2019-01-01. Live-verified. Useful
as a single time-slider service; 2023 still has to be called separately.

CGIS has flown the city on a **3-year cycle since 1997**. Tender
COJ/DEVP001/25-26 is for a 2026 15 cm ortho + LiDAR recapture; that layer is
**not live yet**.

### 1.2 New CoJ imagery / solar / vector finds (this probe)

| municipality | layer name | data source / publisher | capture time | resolution / GSD | spatial coverage | access method | live-verified 2026-09-06 vs catalog-only | license / terms | why it matters for PV dating |
|---|---|---|---|---|---|---|---|---|---|
| JHB | `SatelliteImagery/2025_2026` | CoJ CGIS hosting a commercial mosaic. Catalog raster names are `Vivid_Standard_30_*` → **假设 Maxar Vivid Standard 30 cm**, not a CoJ camera flight | Service title 2025/2026; **no scene date field**. Vivid mosaics are typically blended; treat capture as UNKNOWN month and possibly mixed | REST `pixelSizeX` 0.299 m on EPSG:3857; 3-band RGB; 125 catalog items | Metro-wide (27.69–28.26°E, 26.54–25.88°S) | Public REST ImageServer | **live-verified** | CoJ host; underlying Vivid licence is commercial (not stated on the service). T_C.pdf still applies to the CoJ endpoint | **Not a true-date aerial.** Post-2023 look, but a blended satellite mosaic cannot date an install the way 2019/2023 orthos can. Use only as a “still present in 2025/26?” check, and say so. |
| JHB | `AerialObliqueImagery/2019` | CoJ CGIS | 2019 (year-only) | REST 0.053 m; 3-band; fields `SensorAzimuth`, `SensorElevation`, `CAMERAID` — this is **oblique**, not nadir GSD | Metro-ish Lo29 envelope, slightly larger than the ortho | REST ImageServer (Catalog, Mensuration 3D) | **live-verified** | CoJ copyright; T_C.pdf. 2023 oblique exists as a CGIS *product* (e-Services news) but **no 2023 Oblique ImageServer** was on REST today | Side-on view can confirm panel texture when nadir is ambiguous. Not a dating stack by itself (view angle, occlusion). 2023 oblique is catalog-only until it appears on REST. |
| JHB | `Solar/2019` | CoJ CGIS (derived) | 2019 (name); **not imagery** | 1 m, 1-band Float32. Catalog names `solar_radiation__*` | Metro-wide Lo29 | REST ImageServer | **live-verified** | CoJ | Solar **insolation / radiation** surface, not a photo. Cannot see panels. Weak for dating; at best a roof-potential covariate. |
| JHB | `Solar/SolarRadiation` MapServer | CoJ CGIS | 2019 mosaic + building footprints | Raster 1 m; vector building polygons (live count **574 425**) | Metro-wide Lo29 | REST MapServer (Query, Map, Data) | **live-verified** | CoJ | Same radiation product plus a second building-footprint layer. Not SSEG. |
| JHB | `Property/MapServer/1` **Building Footprints** | CoJ CGIS | UNKNOWN (no capture-year field). Layer description still says CBD / priority-zoning capture | Vector polygons; fields `ELEVATION` only. Live count **550 540** | Description: Johannesburg, Randburg, Roodepoort, Sandton, Midrand, Baralink, Jabulani CBDs + business/industrial + 1 000 m buffer. Count is large enough that coverage is probably wider than CBD-only — **假设 metro-majority, not proven complete** | REST MapServer Query (maxRecord 1000). Also on `WMS/CoJ_core` L5 | **live-verified** | CoJ; T_C.pdf; cadastre extracts also via Online Maps Extract Tool (shp/kml/dxf) | Building footprint for chip alignment / roof mask. **Not dated**, so it does not tell you when the roof (or the PV) appeared. |
| JHB | `FeatureServices/CGIS_CORE_DATA` | CoJ CGIS | Cadastre updated weekly (e-Services FAQ) | Vector. Layers: CITY, REGION, WARD_2021, ADDRESS_LINE, STREET_CENTRELINE, SCHEME_UNITS, SCHEME_VIEW, SPLIT_ZONING, USE_ZONE, **PROPERTY** (live count **828 867**), TOWNSHIP | Metro-wide | REST **FeatureServer** capabilities `Query,Extract` (maxRecord 1000). Lo29 / Hartebeesthoek | **live-verified** | CoJ. 2026/27 tariff: cadastre listed Free per extract; Developer Licence ~R19 350/year if you want a licensed copy with cadastre | Property / zoning join key for any future SSEG admin table. **No PV / SSEG field.** |
| JHB | `Property/MapServer` stands, sectional schemes, townships, farms, informal settlements | CoJ CGIS | Cadastre weekly; roads last updated Aug 2019 (FAQ) | Vector, Lo29 | Metro-wide | REST MapServer Query; Online Maps Extract Tool | **live-verified** | CoJ | Same join-key role. Informal settlements polygon is a coverage caveat for rooftop PV. |
| JHB | `Development_Planning` MapServer `BAS`, `TAS` | CoJ Development Planning | Activation / deactivation dates on area codes | Vector polygons (`BAS_AREA_CODE`, `TAS_AREA_CODE`) | Metro-wide | REST MapServer Query | **live-verified** | CoJ | **Not a building-permit or SSEG register.** BAS/TAS are administrative area codes (Building / Township Application System geography), not application records. |
| JHB | `CityServices` L1 **City Power Areas of Supply** + L2 Eskom supply | City Power / CoJ | UNKNOWN | Vector; 12 City Power depot polygons | Splits JHB into City Power vs Eskom | REST MapServer Query | **live-verified** | CoJ | Tells you **which SSEG office** would hold a register (City Power vs Eskom Direct). Not the register itself. |
| JHB | e-Services Online Maps Extract Tool | CoJ CGIS | Same as live cadastre / current aerial | shp / kml / dxf for *certain* vector layers; aerials are viewable, full ortho/LiDAR **on request** not a public bulk download | Current map extent (user zooms, then Extract) | Browser tool; FAQ says no e-Services registration required. Digital data orders: `cgisprojects@joburg.org.za` | Viewer page **live-verified**; extract contents not downloaded in this probe | T_C.pdf; 2023 15 cm ortho + LiDAR (≥4 pts/m²) + 25 cm contours “available on request” | Practical path for a small cadastre extract. **Not** a bulk true-date chip pipeline — REST ImageServer is the chip path. |

### 1.3 CoJ SSEG / open data — negative findings

- Folder `Solar` is insolation + footprints. **No SSEG customer register,
  no PV polygon layer, no inverter table.**
- `FeatureServices` is core cadastre/zoning. No PV attributes.
- `Development_Planning` is BAS/TAS area codes, not permits.
- City Power SSEG is an **email / office process** (`jozipvpower@citypower.co.za`),
  with a published backlog. MyBroadband (2025-07) quoted City Power as
  *piloting* satellite detection of unregistered SSEG — that system is not on
  this REST tree.
- Full-resolution orthos / LiDAR are **not** an open bulk download. They are
  REST-viewable and orderable. 2026/27 tariffs list older aerial sets as
  “Free” per tile, but the T_C still forbid commercial redistribution without
  consent.

### What is usable for a true-date panel (JHB)

The usable panel is the already-known **2000–2023 three-year true-date ortho
stack** (0.25 → 0.15 m), of which 2019 and 2023 are already in the CoJ audit
path. That is the only JHB source whose capture year is the service identity
and whose GSD is fine enough to see residential rooftop PV. The 2025/26
`Vivid_Standard_30` ImageServer is a bonus *look* after 2023, not a dating
vintage — if we ignore that distinction we will treat a blended mosaic as a
flight date and invent false install years. Building footprints (550k) and
PROPERTY (829k) are join geometry, not dates. There is **no public SSEG GIS
register**; any admin ground truth has to be requested from City Power, and
even then it will be a biased subset of registered systems. 2023 oblique is
a CGIS product that is not on REST yet; 2019 oblique is.

---

## 2. City of Tshwane (Pretoria)

Publisher: City of Tshwane Corporate Geo-Information Management. REST:
`https://e-gis001.tshwane.gov.za/server/rest/services`. Public viewer:
[e-GIS Viewer](https://e-gis002.tshwane.gov.za/E_GIS_Web/) (no login on the
landing page). Static PDF maps on
[tshwane.gov.za General Maps](https://www.tshwane.gov.za/?page_id=38704) are
not imagery. Copyright on REST: “Copyright and all rights reserved by the
City of Tshwane” / “Terms of Use are subject to the Terms and Conditions on
the City of Tshwane's Maps and GIS homepage.” Viewer: downloadable content
is “freely available” but **“Commercial exploitation of any of the content
is strictly forbidden.”** Electronic data orders:
`GeoInfoService@tshwane.gov.za` / `geoinfo@tshwane.gov.za`.

Live ImageServers under `Imagery/`:

| municipality | layer name | data source / publisher | capture time | resolution / GSD | spatial coverage | access method | live-verified 2026-09-06 vs catalog-only | license / terms | why it matters for PV dating |
|---|---|---|---|---|---|---|---|---|---|
| Tshwane | `Imagery/AERIAL_2018` | City of Tshwane | 2018 (year-only; month UNKNOWN) | **0.10 m** RGB; Lo29 / TM29 (`WG29`) | Metro-wide. Lo29 x −120457…15110, y −2888071…−2777120 ≈ 27.80–29.15°E, 26.10–25.10°S | REST ImageServer (Catalog, Image, Metadata, Mensuration) | **live-verified** | CoT copyright, all rights reserved | True-date 10 cm aerial. Fine enough for residential PV. First dated municipal vintage on REST. |
| Tshwane | `Imagery/AERIAL_2018_TIFF` | City of Tshwane (same 2018 flight, TIFF mosaic) | 2018 | 0.10 m RGB; Lo29; same extent as `AERIAL_2018` | Metro-wide | REST ImageServer | **live-verified** | Terms → CoT Maps and GIS homepage | Duplicate of 2018 in a different storage format. Do not count as a second date. |
| Tshwane | `Imagery/Aerial2018_WM` | City of Tshwane | 2018 | 0.10 m RGB; **cached** tile service, EPSG:3857, minScale 2.3e6 maxScale 1128 | Same metro envelope 27.799–29.150°E, 26.100–25.100°S | REST ImageServer, `singleFusedMapCache=true` | **live-verified** | CoT | Same 2018 flight, Web-Mercator cache. Prefer this for chip export in 3857; still one date. |
| Tshwane | `Imagery/Aerial_2023` | City of Tshwane | 2023 (year-only; month UNKNOWN). Catalog names are NGI-style 10k IDs (`2527BD14`, …), 358 items | **0.10 m** RGB; EPSG:3857 | Metro-wide, same envelope as 2018_WM | REST ImageServer (Image, Metadata, Catalog). **No WMSServer** (404 / 400 on GetCapabilities) | **live-verified** | CoT (copyright text empty on this service) | True-date 10 cm aerial. Together with 2018 this is a two-epoch panel. Tile IDs are sheet names, not capture dates. |
| Tshwane | `Imagery/Aerial_2024` | City of Tshwane | Service name 2024; month UNKNOWN. Catalog names **the same** `2527BD##` IDs as 2023/2025. 702 items because each tile appears twice (LowPS 0.10 and 1.0). FullExtent ymax is a bogus `0.5` (mosaic artefact) | REST 0.10 m; 3-band `Processed`; EPSG:3857 | Intended metro-wide (xmin/ymin match 2023); ymax is corrupted in the service metadata | REST ImageServer | **live-verified service**; **假设 a new 2024 flight, not proven** — filenames do not carry a year | CoT | *If* it is a distinct flight, it is a third true-date epoch. If it is a republish of 2023/2025, using it as a date will fabricate installs. Needs a visual chip-diff before any panel use. |
| Tshwane | `Imagery/Aerial_2025` | City of Tshwane | Service name 2025; month UNKNOWN. Same `2527BD##` names; 698 items (again 0.10 + 1.0 pairs). Extent ymax also `0.5` | 0.10 m; Lo29-like envelope (wkid not set) | Intended metro-wide | REST ImageServer | **live-verified service**; **假设 a new 2025 flight, not proven** | CoT | Same caveat as 2024. Do not add 2024 and 2025 to a dating panel until chips show they actually differ. |
| Tshwane | `Imagery/Tshwane_Hillshade_WM` | City of Tshwane | UNKNOWN (derived) | Hillshade MapServer, not RGB | Metro | REST MapServer | **live-verified** | CoT | Terrain only. No PV signal. |
| Tshwane | `Other_WS/Land_Parcel` + `IdentifyServices` L0 Land Parcel | City of Tshwane | Living cadastre; history layers exist (`Landparcel History`) | Vector polygons. Live count **682 848** parcels | Metro-wide | REST MapServer Query (maxRecord 1000). IdentifyServices copyright points at CoT Maps and GIS T&Cs | **live-verified** | CoT copyright | Property join key. No PV field. |
| Tshwane | `IdentifyServices` / `Viewer/IdentifyServicesCombined` | City of Tshwane | Mixed | Vector: zoning, sectional schemes, informal structure (**57 552**), urban edge, MSDF/RSDF, utilities | Metro-wide | REST MapServer Query | **live-verified** | CoT | Informal-structure polygons flag roofs that are not formal cadastre. Still not a PV layer. **No building-footprint layer found.** |
| Tshwane | e-GIS Viewer downloads | City of Tshwane | Same as viewer layers | Viewer claims downloadable content is freely available | Metro | Browser viewer; no REST WFS advertised | Viewer **live-verified**; actual download formats not exercised | Free to use; commercial exploitation forbidden | Possible shp extract for parcels. Not a chip pipeline. |

### 2.1 Tshwane gaps

- **No public building-footprint service** on `e-gis001` (Housing folder empty;
  Viewer/Other_WS/WebApps have cadastre, informal structures, zoning, not
  footprints).
- **No SSEG GIS layer.** SSEG is `sseg@tshwane.gov.za` plus a hard-copy file
  at Middestad Building (MyBroadband 2025). Policy PDF (2025) still says
  online submission is “in the pipeline”.
- Historical 2009 aerial mentioned on an old CGIS contact card is **not** on
  the current REST Imagery folder.
- ImageServers do not advertise WMS (`supportedExtensions` empty).
- `gis.tshwane.gov.za` is not a working GIS host (TLS failure).

### What is usable for a true-date panel (Tshwane)

The only dates we can defend today are **2018 and 2023**, both 0.10 m,
metro-wide, public ImageServer. That is a two-epoch presence panel, denser
in GSD than CoJ’s 15 cm but with a six-year gap that will swallow the
load-shedding PV boom. `Aerial_2024` and `Aerial_2025` look like extra
epochs on paper; their mosaic catalogues reuse the same sheet IDs and have
broken extents, so they are **suspect until a chip comparison**. If we skip
that check we risk counting one flight three times. There is no footprint
layer and no SSEG register, so Tshwane dating would be imagery-only plus
our own census polygons. Cadastre (683k parcels) is the join key if an
admin extract ever arrives.

---

## 3. City of Ekurhuleni

Publisher: City of Ekurhuleni GIS Division. Viewer
[gis.ekurhuleni.gov.za](https://gis.ekurhuleni.gov.za/) (lists imagery through
Oct 2018; REST is newer). REST
`https://gis.ekurhuleni.gov.za/arcgis/rest/services`. Contact
`onlinemaps@ekurhuleni.gov.za`. No licence text on most ImageServers.
WMS **does** work for the combined orthophoto MapServer
(`…/arcgis/services/Imagery/Orthophoto/MapServer/WMSServer`, GetCapabilities
200). ImageServers themselves do not advertise WMS.

### 3.1 Municipal true-date orthophotos

| municipality | layer name | data source / publisher | capture time | resolution / GSD | spatial coverage | access method | live-verified 2026-09-06 vs catalog-only | license / terms | why it matters for PV dating |
|---|---|---|---|---|---|---|---|---|---|
| Ekurhuleni | `Imagery/OrthoPhotos_2002` | CoE GIS | 2002 (year-only) | 0.50 m, **1-band** (greyscale) | Metro Lo29 envelope x −100141…−39854, y −2932639…−2862977 | REST ImageServer | **live-verified** | Unstated on service; treat as CoE copyright **假设** | True-date but 50 cm greyscale — marginal for small residential PV. Pre-boom baseline. |
| Ekurhuleni | `Imagery/OrthoPhotos_2003` | CoE GIS | 2003 | 0.42 m, 1-band | Same metro envelope | REST ImageServer | **live-verified** | Unstated | Same caveat as 2002. Viewer/contact card called this “Aerial Imagery (Black n White 2003)”. |
| Ekurhuleni | `Imagery/OrthoPhoto_2005` | CoE GIS | 2005 | 0.60 m RGB | Metro Lo29 (slightly larger) | REST ImageServer | **live-verified** | Unstated | True-date but coarse. |
| Ekurhuleni | `Imagery/OrthoPhoto_2007` | CoE GIS | **June 2007** (viewer) | 0.20 m RGB | Metro Lo29 | REST ImageServer | **live-verified** (year); month from viewer catalog | Unstated | First colour vintage that can see larger arrays. |
| Ekurhuleni | `Imagery/OrthoPhoto_2010` | CoE GIS | **May 2010** (viewer) | 0.20 m RGB | Metro Lo29 | REST ImageServer | **live-verified** (year); month from viewer | Unstated | True-date aerial. |
| Ekurhuleni | `Imagery/Orthophoto_2013` | CoE GIS | **May 2013** (viewer) | **0.10 m** RGB; EPSG:3857 28.048–28.538°E, 26.475–25.894°S | Metro-wide | REST ImageServer | **live-verified** (year); month from viewer | Unstated | True-date 10 cm. Start of a usable residential-PV series. |
| Ekurhuleni | `Imagery/Orthophoto` MapServer L24 **2015** | CoE GIS | **October 2015** (viewer). No dedicated `OrthoPhoto_2015` ImageServer | Mosaic footprints `LowPS=0.15` m; 3×3 km tiles `AO114`… | Metro-wide (same tile grid as 2018) | REST MapServer mosaic / WMS | **live-verified** mosaic; GSD from footprint query | Unstated | True-date 15 cm. Must be pulled from the combined MapServer, not a standalone ImageServer. |
| Ekurhuleni | `Imagery/OrthoPhoto_2018` | CoE GIS | **October 2018** (viewer) / **November 2018** (older MapServer metadata). Year is solid; month 10 vs 11 is a catalog conflict | **0.10 m** RGB; Lo29 x −94000…−46000, y −2931000…−2865000 | Metro-wide | REST ImageServer | **live-verified** | Unstated | True-date 10 cm. Last municipal camera flight that is both on REST *and* on the public viewer list. |
| Ekurhuleni | `Imagery/Aerial_Photos2020` | CoE GIS | Combined MapServer labels this **2019/20**. Month UNKNOWN | **0.10 m** RGB; same Lo29 envelope as 2018. Mosaic footprints LowPS 0.10 | Metro-wide | REST ImageServer | **live-verified** | Unstated | True-date-ish 10 cm. Viewer homepage still stops at 2018 — REST is ahead of the HTML list. Year is “2019/20”, not a single month. |
| Ekurhuleni | `Imagery/Orthophoto` and `Orthophoto_2026` MapServers | CoE GIS | Stack of the vintages above + NGI/SANSA. **Despite the `_2026` name, layer list has no 2026 flight** (top layer is 2024 NGI) | See component layers | Metro / regional | REST MapServer; WMS on `Orthophoto` | **live-verified** | Unstated | Convenience stack for a viewer. `Orthophoto_2026` is a clone of the historical stack, **not** a 2026 recapture. |

### 3.2 Republished national / satellite (not CoE camera flights)

| municipality | layer name | data source / publisher | capture time | resolution / GSD | spatial coverage | access method | live-verified 2026-09-06 vs catalog-only | license / terms | why it matters for PV dating |
|---|---|---|---|---|---|---|---|---|---|
| Ekurhuleni | `Imagery/NGI_MOSAIC_2023` | CD:NGI, hosted by CoE | 2023 (NGI programme year). Filenames not sampled on this service | 0.25 m RGB; EPSG:3857 | **Regional**, 27.00–29.00°E, 27.00–25.50°S (bigger than the metro) | REST ImageServer | **live-verified** | NGI / DALRRD state copyright (not printed on the service). NGI 25 cm is generally redistributable via the CDNGI portal under NGI terms, **not** CoE’s | National 25 cm true-date-ish mosaic. Usable where CoE 10 cm stops (after 2019/20), but 25 cm is the edge of residential PV visibility. |
| Ekurhuleni | `Imagery/NGI_MOSAIC_2024` | CD:NGI, hosted by CoE | 2024. Catalog names `2528CA_##_2024_1835_RGB_RECT` (NGI job 1835) | ~0.25 m (2.25e-6 ° ≈ 0.25 m); EPSG:4326 | Same regional envelope 27–29°E, 27.00–25.50°S | REST ImageServer | **live-verified** | NGI / DALRRD | Same as 2023, one year later. Best post-2020 true-date layer on this metro until CoE flies again. |
| Ekurhuleni | `Imagery/SANSA_2024` | SANSA, hosted by CoE | 2024 (year-only) | ~0.50 m (4.49e-6 °); 3-band RGB; EPSG:4326 | 27.95–28.65°E, 26.54–25.84°S (metro + buffer) | REST ImageServer | **live-verified** | SANSA copyright **假设**; not stated | 50 cm satellite. Combined MapServer also has `2020 SANSA (Spot7)`. Too coarse for small rooftop PV; OK for large commercial arrays. Not a camera-flight date. |
| Ekurhuleni | `Imagery/Orthophoto` L16 **2020 SANSA (Spot7)** | SANSA SPOT-7, hosted by CoE | 2020 | SPOT-7 is 1.5 m native pan-sharpened to ~1.5 m; REST GSD not separately ImageServer’d | Metro | Combined MapServer / WMS only | **live-verified** as a named mosaic layer | SANSA | Even coarser. Dating use is near-zero for residential PV. |
| Ekurhuleni | `Imagery/Oblique2022` | CoE GIS | 2022 | REST 0.061 m; fields `SensorAzimuth`, `CameraID` — oblique | Sub-metro Lo29 (narrower than the ortho envelope) | REST ImageServer | **live-verified** | Unstated | Confirmation view, not a nadir dating layer. Coverage may not be metro-wide. |

### 3.3 Ekurhuleni vectors

| municipality | layer name | data source / publisher | capture time | resolution / GSD | spatial coverage | access method | live-verified 2026-09-06 vs catalog-only | license / terms | why it matters for PV dating |
|---|---|---|---|---|---|---|---|---|---|
| Ekurhuleni | `Ekurhuleni/Building_footprints` L2 **Building Footprints (Google)** | Google Open Buildings, clipped by CoE. Service description quotes the Open Buildings social-good blurb | UNKNOWN (Open Buildings model date, not a flight). Fields `CONFIDENCE`, `LATITUDE`, `LONGITUDE`, `AREA_IN_ME`, `FULL_PLUS_` | Vector. Live count **139 237** | Envelope is metro-wide (28.05–28.54°E, 26.48–25.89°S) but **139k is far below ~673k stands** — **假设 a confidence / size subset, not a complete footprint census** | REST MapServer Query | **live-verified** | Google Open Buildings is typically CC-BY-4.0; CoE page does not restate it | Roof mask only, and likely incomplete. Not dated. Do not treat 139k as “Ekurhuleni has 139k buildings”. |
| Ekurhuleni | `Ekurhuleni/Ekurhuleni_Propety_Data_Map` | CoE GIS (typo in the service name is upstream) | Living cadastre | Vector: Addresses, **Stands (672 740)**, Zoning, Proposed Stands, Townships, Farms, admin boundaries | Metro-wide | REST MapServer Query; WMS advertised | **live-verified** | Unstated | Property join key. No PV field. |
| Ekurhuleni | `Hosted/BuildingControl_gdb` L0 BuildingContraventions | CoE Building Control | UNKNOWN | Point. Capabilities include Create/Update/Delete/Editing | Unknown completeness | REST FeatureServer | **live-verified** | Unstated; editing caps suggest an internal app accidentally public | Building *contraventions*, not PV. Do not scrape as SSEG. Editing-enabled public FS is a governance smell, not a research input. |
| Ekurhuleni | SSEG policy / application forms | CoE Electricity | Admin process, not a map | n/a | Metro electricity customers | PDF + email/hand-in ([ekurhuleni.gov.za/current-campaigns/solar-panels](https://www.ekurhuleni.gov.za/current-campaigns/solar-panels/)); June 2025 CoE slide deck claims 287 MW approved EG, 45 MW estimated unregistered residential | Policy pages catalog; **no GIS layer** | Municipal SSEG policy | SSEG exists as an **admin register**, not a spatial layer. A data-sharing request to Electricity would be the path, not REST. |

### What is usable for a true-date panel (Ekurhuleni)

Ekurhuleni is the **longest public true-date camera stack** of the three
metros: 2002/03 greyscale → 2005 60 cm → 2007/2010 20 cm → 2013/2015/2018/2019–20
at 10–15 cm, all on REST, metro-wide. For rooftop PV the usable camera
epochs start in **2013**. After 2019/20 the city did **not** publish a new
10 cm flight; the 2023 and 2024 layers are **NGI 25 cm** (regional), which
will miss small residential arrays that 10 cm would have caught. If we
pretend NGI 25 cm is the same sensor as CoE 10 cm we will mix detectability
with time and bias install-year estimates toward large systems. Google
footprints are a thin, undated clip. There is no SSEG map. The practical
panel is CoE 2013–2019/20 at 10–15 cm, with NGI 2023/24 as a coarser
after-period, and a hard note on the GSD break.

---

## 4. Cross-metro comparison (what actually dates PV)

| City | True-date camera vintages fine enough for residential PV | GSD | Public REST? | Building footprints | SSEG GIS | After-2020 gap |
|---|---|---|---|---|---|---|
| JHB | 2009, 2012, 2015, **2019**, **2023** (plus coarser 2000/03/06) | 0.15 m from 2009 | Yes, ImageServer | 550k (priority/CBD description; count is large) | **None** | 2025/26 Vivid 30 cm mosaic (not true-date); 2026 15 cm tender not live |
| Tshwane | **2018**, **2023** (2024/2025 unproven) | 0.10 m | Yes, ImageServer | **None** | **None** | 2024/2025 services exist but may be duplicates |
| Ekurhuleni | **2013**, **2015**, **2018**, **2019/20** (plus coarser 2007/10) | 0.10–0.15 m | Yes, ImageServer + WMS stack | Google Open Buildings clip 139k, likely incomplete | **None** | NGI 25 cm 2023/2024 only |

None of the three metros expose a spatial SSEG register. That finding is
itself the result: admin PV dates, if we want them, are a records request
(City Power / `sseg@tshwane.gov.za` / CoE Electricity), not a GIS download.

National fallback not catalogued as a municipal layer but overlapping all
three: CD:NGI 25 cm (2017–ongoing) via
[CDNGI portal](https://ngigeoportal.dlrrd.gov.za/cdngiportal/) and the
community XYZ `https://aerial.openstreetmap.org.za/layer/ngi-aerial/{z}/{x}/{y}.jpg`.
Ekurhuleni is the only Gauteng metro that already republishes 2023/2024 NGI
on its own REST.

---

## 5. Probe notes / caveats

- Probe agent did not fetch pixels. “Live-verified” means the *service
  metadata* answered `?f=pjson` on 2026-09-06, plus catalog queries
  (`returnCountOnly`, sample `Name`/`LowPS`).
- CoJ SOAP WMS GetCapabilities on
  `…/services/WMS/CoJ_AerialPhotos2023/MapServer/WMSServer` returned XML
  `Access is denied`. REST ImageServer for the same 2023 flight is public.
- No `/portal/home/` on the three hosts (404). Login walls were not the
  blocker; the blocker for SSEG is that the layer does not exist.
- `Hosted/BuildingControl_gdb` on Ekurhuleni advertises editing. Treat as
  off-limits for research writes.
- Month precision: only CoJ 2023 (May, from CGIS FAQ) and Ekurhuleni viewer
  labels (Jun 2007, May 2010, May 2013, Oct 2015, Oct 2018) go finer than
  year. REST attribute tables are year-or-nothing.
- CRS: CoJ and Tshwane native services are often Hartebeesthoek **TM / Lo
  29** (central meridian 29°E). Wrappers exist in EPSG:3857. Chip code that
  already talks to CoJ 2019 in Lo29 will need a 3857 path for Tshwane 2023
  and Ekurhuleni 2013/NGI.
