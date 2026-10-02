# Other metros — municipal aerial metadata

Date: 2026-09-06. Live-probed with TLS-unverified client where municipal
certs fail (eThekwini, Tshwane). Tshwane REST returned 503 today.

---

## eThekwini (Durban)

Endpoint: `https://gis.durban.gov.za/server/rest/services` (cert is untrusted;
service answers). Folders `RasterCatalogueProject` (1994–2018), `2019`,
`AerialPhotography` (2020–2025).

| layer | time | GSD | coverage | live | PV-dating note |
|---|---|---|---|---|---|
| AerialPhotography1994 | 1994 | 0.80 m, 1 band | metro-ish | yes | pre-PV / greyscale |
| AerialPhotography1997 | 1997 | 1.0 m | smaller box | yes | too coarse |
| AerialPhotography2001 | 2001 | UNKNOWN (empty ImageServer JSON) | — | service exists | treat as broken until re-probe |
| AerialPhotograph2005 | 2005 | 0.35 m | metro | yes | borderline |
| AerialPhotography2006 / 2009 / 2012 / 2015 | those years | **0.30 m** | metro | yes | same class as GEHI z19; small arrays risky |
| AerialPhotography2016 | 2016 | **0.09 m** | metro | yes | first rooftop-comfortable year |
| AerialPhotography2018 | 2018 | 0.10 m | metro (served in Web Mercator) | yes | |
| 2019/AerialPhotography | 2019 | 0.09 m | metro | yes | |
| AerialPhoto2020 / Aerial_Imagery_2020 | 2020 | 0.10 m | metro | yes | two copies of the same GSD |
| AerialPhotography20212022 | 2021–2022 (name is a range) | 0.10 m | metro | yes | **not a single year** |
| AerialPhotography2022 / 2022RSM | 2022 | 0.10 m | metro | yes | prefer the dated 2022 over the 2021–2022 mix |
| AerialPhotography2023 | 2023 | 0.10 m | **west only** (xmax ≈ −13 km vs +18 km on 2022); misses Durban CBD / coast | yes | not citywide |
| LowRangeAerialPhotography2025 | 2025 | **0.05 m**, 4-band | **small patch** ~11 × 15 km | yes | not a metro panel year |
| Oblique2016 | 2016 | 0.066 m oblique | metro | yes | QA overlay only |

Usable true-date panel years at ≤10 cm: **2016, 2018, 2019, 2020, 2022**.
2021–2022 mix and 2023 partial and 2025 low-range are not citywide years.
`Solar_Radiation` is a radiation raster. `NERSA_Map_MIL1` is electricity
supply-area boundaries, not an SSEG install register.

---

## Ekurhuleni (not in ZAsolar 8-city Vexcel list; adjacent Gauteng metro)

Endpoint: `https://gis.ekurhuleni.gov.za/arcgis/rest/services/Imagery`

| layer | time | GSD | coverage | live | PV-dating note |
|---|---|---|---|---|---|
| OrthoPhotos_2002 | 2002 | 0.50 m, 1 band | metro | yes | greyscale |
| OrthoPhotos_2003 | 2003 | 0.42 m, 1 band | metro | yes | greyscale |
| OrthoPhoto_2005 | 2005 | 0.60 m | metro | yes | too coarse |
| OrthoPhoto_2007 | 2007 | 0.20 m | metro | yes | |
| OrthoPhoto_2010 | 2010 | 0.20 m | metro | yes | |
| Orthophoto_2013 | 2013 | **0.10 m** | metro (Web Mercator) | yes | first 10 cm year |
| OrthoPhoto_2018 | 2018 | 0.10 m | metro | yes | |
| Aerial_Photos2020 | 2020 | 0.10 m | metro | yes | |
| NGI_MOSAIC_2023 | 2023 NGI | 0.25 m | wider than metro | yes | national, not municipal flight |
| NGI_MOSAIC_2024 | 2024 NGI | ~0.25 m (pixel size in degrees) | lon 27–29, lat −27 to −25.5 | yes | national |
| SANSA_2024 | 2024 SANSA | ~0.50 m (degrees) | metro box | yes | satellite, not aerial |
| Oblique2022 | 2022 | 0.06 m oblique | smaller than metro | yes | QA only |
| Orthophoto / Orthophoto_2026 MapServers | mixed viewer | n/a | metro | yes | layers are 2024 NGI + 2023 NGI + 2024 SANSA + 2019/20. **Name 2026 is a trap** — not a 2026 municipal flight |

Usable true-date municipal years at ≤10 cm: **2013, 2018, 2020**. 2007/2010
at 20 cm are secondary. This city is not in the current census grid; a panel
here would need a new detection pass on these orthos.

---

## City of Tshwane (Pretoria)

First probe hit `gis.tshwane.gov.za` **503**. Live root is
`https://e-gis001.tshwane.gov.za/server/rest/services` (see
[`gauteng.md`](gauteng.md)). ImageServers: `AERIAL_2018` and `Aerial_2023`
at **0.10 m**, metro-wide. `Aerial_2024` / `Aerial_2025` exist as services
but share the same sheet IDs — treat as unproven extra years until a chip
diff. Two-epoch panel (2018, 2023) is live.

---

## Nelson Mandela Bay (Gqeberha)

Direct `/arcgis/rest/services` is 404. The public viewer
`https://gis.nelsonmandelabay.gov.za/agsviewercm1/` plus resource-proxy
exposes internal ImageServers. Full vintage list (live 2026-09-06, see
[`ec_fs.md`](ec_fs.md)):

1997 and 2004 at 0.25 m; 2009/2011/2013 at 0.10 m; **2017/2018/2022 at
0.05 m**. Also 2017 LiDAR footprints (355,880) and 2009–2013–2017 building
change (160,637). This is a real municipal-year panel city, not a Vexcel-only
fallback.

---

## Buffalo City (East London), Mangaung (Bloemfontein), Msunduzi (Pietermaritzburg)

| city | probe 2026-09-06 | census imagery we already have |
|---|---|---|
| Buffalo City | `gis.buffalocity.gov.za` timed out | Vexcel 2026-01, 7.1 cm |
| Mangaung | `gis.mangaung.co.za` DNS NXDOMAIN | Vexcel 2026-01, 7.4 cm |
| Msunduzi | `gis.msunduzi.gov.za` timed out | Vexcel 2025-12, 6.9 cm |

No municipal multi-year aerial stack found. A panel in these cities is
**single-vintage census + GEHI/NGI**, not municipal true-date years.

---

## Ranking for a municipal-year PV panel

1. Cape Town (annual 8 cm 2012–2022, 5 cm 2025–26)
2. eThekwini (≤10 cm 2016–2022, near-annual)
3. Johannesburg (15 cm, 3-year cadence 2009–2023; 2000–2006 coarser)
4. NMB / Gqeberha (0.05–0.10 m, 1997–2022 via resource-proxy)
5. Ekurhuleni (10 cm at 2013/2018/2020 only)
6. Tshwane (10 cm 2018 + 2023 live; 2024/25 unproven)
7. Buffalo / Mangaung / Msunduzi — no live municipal year stack
