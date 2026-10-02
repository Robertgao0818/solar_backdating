#!/usr/bin/env python3
"""Emit collaborator-facing aerial + satellite catalog CSVs.

Numbers come from the 2026-09-06 SRC notes and existing satellite/vexcel
CSVs in this folder. Re-run from this directory.
"""
from __future__ import annotations

import csv
from pathlib import Path

HERE = Path(__file__).resolve().parent

AERIAL_FIELDS = [
    "family",
    "municipality",
    "municipality_name",
    "layer_id",
    "layer_name",
    "publisher",
    "capture_year",
    "capture_label",
    "gsd_m",
    "gsd_source",
    "coverage",
    "coverage_class",
    "crs",
    "access_method",
    "endpoint",
    "live_2026_09_06",
    "license",
    "usable_as_panel_year",
    "trap",
    "note",
]

CITY_FIELDS = [
    "municipality",
    "municipality_name",
    "public_true_date_stack",
    "usable_years",
    "n_usable_years",
    "gsd_m_min",
    "gsd_m_max",
    "coverage",
    "access_2026_09_06",
    "panel_verdict",
    "vexcel_collections",
    "vexcel_capture",
    "vexcel_gsd_m",
    "vexcel_h3_metro_covered",
    "vexcel_h3_metro_total",
    "vexcel_h3_metro_rate",
    "vexcel_urban_centre_area_rate",
    "traps",
    "notes",
]

SAT_FIELDS = [
    "family",
    "municipality",
    "municipality_name",
    "layer_id",
    "provider",
    "provider_label",
    "zoom",
    "gsd_m_nominal",
    "n_h3_r7",
    "n_anchors_sum",
    "n_date_rows",
    "median_dates_per_anchor",
    "min_date",
    "max_date",
    "years_union",
    "usable_as_time_axis",
    "note",
]


def L(**kwargs):
    row = {k: "" for k in AERIAL_FIELDS}
    row.update(kwargs)
    return row


def write_csv(path: Path, fields: list[str], rows: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="raise")
        w.writeheader()
        for row in rows:
            w.writerow({k: row.get(k, "") for k in fields})


COCT = "https://cityimg.capetown.gov.za/erdas-iws/esri/GeoSpatial%20Datasets/rest/services"
COCT_WMS = "https://cityimg.capetown.gov.za/erdas-iws/ogc/wms/GeoSpatial%20Datasets"
COJ = "https://ags.joburg.org.za/server/rest/services/AerialPhotography"
ETH = "https://gis.durban.gov.za/server/rest/services"
EKU = "https://gis.ekurhuleni.gov.za/arcgis/rest/services/Imagery"
TSH = "https://e-gis001.tshwane.gov.za/server/rest/services/Imagery"
NMB_PROXY = "https://gis.nelsonmandelabay.gov.za/resource-proxy/proxy.ashx?https://hq-gis-01.ds.local/image/rest/services"
NGI_WMS = "https://bp-cdngiapollo.dlrrd.gov.za/erdas-iws/ogc/wms/CDNGI_PORTAL_BACKDROP"
NGI_ESRI = "https://imagery.esri-southafrica.com/server/rest/services/NGI/RSA_NGI_AERIAL/ImageServer"
VEX = "https://api.vexcelgroup.com/v2/ortho/collections"

COCT_LIC = "CoCT service text: non-commercial unrestricted"
COJ_LIC = "CoJ copyright; T_C.pdf; no commercial redistribution without consent"
ETH_LIC = "empty copyrightText; 假设 REST view tolerated, bulk extract not CC"
EKU_LIC = "unstated on service; treat as CoE copyright 假设"
TSH_LIC = "CoT copyright; commercial exploitation forbidden"
NMB_LIC = "municipal copyright; public stream via proxy undocumented; bulk extract PAIA/GIS office"
NGI_LIC = "Land Survey Act; standard digital product R0.00; not CC-BY"
VEX_LIC = "LSE academic token; chips not redistributable"


def aerial_layers() -> list[dict]:
    rows: list[dict] = []

    def ct(**kw):
        rows.append(
            L(
                family="municipal",
                municipality="cape_town",
                municipality_name="City of Cape Town",
                publisher="City of Cape Town (ERDAS / cityimg)",
                crs="EPSG:20482 (Lo19)",
                access_method="ERDAS MapServer / WMS",
                endpoint=COCT,
                live_2026_09_06="yes",
                license=COCT_LIC,
                **kw,
            )
        )

    ct(
        layer_id="coct_2026jan",
        layer_name="Aerial Imagery 2026Jan",
        capture_year=2026,
        capture_label="2026-01 v2, not final",
        gsd_m=0.05,
        gsd_source="service description",
        coverage="metro",
        coverage_class="metro",
        usable_as_panel_year="yes",
        trap="",
        note="Newest 5 cm. Do not treat as locked census vintage.",
    )
    ct(
        layer_id="coct_2025jan",
        layer_name="Aerial Imagery 2025Jan",
        capture_year=2025,
        capture_label="2025-01 v3",
        gsd_m=0.05,
        gsd_source="service description",
        coverage="metro",
        coverage_class="metro",
        usable_as_panel_year="yes",
        trap="",
        note="Current ZAsolar census layer (aerial_2025). Mid-date frozen 2025-01-31.",
    )
    ct(
        layer_id="coct_2024",
        layer_name="Aerial Imagery 2024",
        capture_year=2024,
        capture_label="2024 (no month)",
        gsd_m="",
        gsd_source="UNKNOWN; 假设 0.05–0.08 m",
        coverage="~30% of metro",
        coverage_class="partial",
        usable_as_panel_year="no",
        trap="partial_coverage",
        note="Cannot be a citywide panel year.",
    )
    ct(
        layer_id="coct_2023jan",
        layer_name="Aerial Imagery 2023Jan",
        capture_year=2022,
        capture_label="name 2023; content NGI 2022",
        gsd_m=0.25,
        gsd_source="service description",
        coverage="NGI extract, not CCT 8 cm",
        coverage_class="ngi_extract",
        usable_as_panel_year="trap",
        trap="name_is_year_content_is_ngi",
        note="Naming trap. Not a municipal 8 cm flight.",
    )
    for lid, name, year, label in [
        ("coct_2022jan", "Aerial Imagery 2022Jan", 2022, "2022-01 v5"),
        ("coct_2021mar", "Aerial Imagery 2021Mar", 2021, "2021-03 v3"),
        ("coct_2020jan", "Aerial Imagery 2020Jan", 2020, "2020-01"),
        ("coct_2019jan", "Aerial Imagery 2019Jan", 2019, "2019-01"),
        ("coct_2018feb", "Aerial Imagery 2018Feb", 2018, "2018-02"),
    ]:
        ct(
            layer_id=lid,
            layer_name=name,
            capture_year=year,
            capture_label=label,
            gsd_m=0.08,
            gsd_source="service description",
            coverage="metro",
            coverage_class="metro",
            usable_as_panel_year="yes",
            trap="",
            note="Last full 8 cm year before 5 cm." if year == 2022 else "",
        )
    ct(
        layer_id="coct_2017jan",
        layer_name="Aerial Imagery 2017Jan",
        capture_year=2017,
        capture_label="2017-01",
        gsd_m=0.08,
        gsd_source="OSM/JOSM; service desc empty. 假设 0.08 m",
        coverage="metro",
        coverage_class="metro",
        usable_as_panel_year="yes",
        trap="",
        note="CBD True Ortho sibling also exists. GSD 假设 from OSM.",
    )
    ct(
        layer_id="coct_2016jan",
        layer_name="Aerial Imagery 2016Jan",
        capture_year=2016,
        capture_label="2016-01",
        gsd_m=0.08,
        gsd_source="service description",
        coverage="metro",
        coverage_class="metro",
        usable_as_panel_year="yes",
        trap="",
        note="CBD True Ortho sibling.",
    )
    ct(
        layer_id="coct_2015feb",
        layer_name="Aerial Imagery 2015Feb",
        capture_year=2015,
        capture_label="2015-02",
        gsd_m=0.08,
        gsd_source="service text 0.08 m (OSM listed 12 cm TMS)",
        coverage="metro",
        coverage_class="metro",
        usable_as_panel_year="yes",
        trap="",
        note="Trust service text 0.08 m, not the OSM 12 cm listing.",
    )
    ct(
        layer_id="coct_2014_ngi",
        layer_name="Aerial Imagery 2014_NGI",
        capture_year="",
        capture_label="mixed 2014+2016+2022 NGI, resampled",
        gsd_m=0.25,
        gsd_source="service description",
        coverage="wider than metro",
        coverage_class="mixed_ngi",
        usable_as_panel_year="trap",
        trap="mixed_years_resampled",
        note="Not a single capture date. Unusable as a panel year.",
    )
    ct(
        layer_id="coct_2013dec",
        layer_name="Aerial Imagery 2013Dec",
        capture_year=2013,
        capture_label="2013-12",
        gsd_m=0.08,
        gsd_source="service description",
        coverage="metro; planimetric ±1 m",
        coverage_class="metro",
        usable_as_panel_year="yes",
        trap="",
        note="First 8 cm year in this probe.",
    )
    ct(
        layer_id="coct_2012dec",
        layer_name="Aerial Imagery 2012Dec",
        capture_year=2012,
        capture_label="2012-12",
        gsd_m=0.08,
        gsd_source="service description",
        coverage="east truncated (xmax ≈ −6000 vs +1000 later)",
        coverage_class="east_truncated",
        usable_as_panel_year="yes",
        trap="",
        note="Coverage hole on the east. Still a panel year for the rest of the metro.",
    )
    ct(
        layer_id="coct_2009nov",
        layer_name="Aerial Imagery 2009Nov",
        capture_year=2009,
        capture_label="2009-11",
        gsd_m=0.125,
        gsd_source="service description",
        coverage="smaller box than 2013+",
        coverage_class="partial",
        usable_as_panel_year="secondary",
        trap="",
        note="Still rooftop-resolvable; smaller extent.",
    )
    ct(
        layer_id="coct_2008june",
        layer_name="Aerial Imagery 2008June",
        capture_year=2008,
        capture_label="2008-06",
        gsd_m=0.20,
        gsd_source="service description",
        coverage="metro-ish",
        coverage_class="metro",
        usable_as_panel_year="secondary",
        trap="",
        note="Borderline for small arrays.",
    )
    ct(
        layer_id="coct_2005feb",
        layer_name="Aerial Imagery 2005Feb",
        capture_year=2005,
        capture_label="2005-02",
        gsd_m=0.25,
        gsd_source="service description",
        coverage="metro-ish",
        coverage_class="metro",
        usable_as_panel_year="secondary",
        trap="",
        note="Same GSD class as GEHI z19.",
    )
    ct(
        layer_id="coct_2000apr",
        layer_name="Aerial Imagery 2000Apr",
        capture_year=2000,
        capture_label="2000-04",
        gsd_m=0.25,
        gsd_source="service description",
        coverage="patchwork of built-up areas, not full metro",
        coverage_class="patchwork",
        usable_as_panel_year="no",
        trap="incomplete_coverage",
        note="Coverage incomplete.",
    )

    def jhb(**kw):
        year = kw.get("capture_year")
        defaults = dict(
            family="municipal",
            municipality="johannesburg",
            municipality_name="City of Johannesburg",
            publisher="CoJ CGIS",
            access_method="ArcGIS REST ImageServer",
            endpoint=f"{COJ}/{year}/ImageServer" if year else COJ,
            live_2026_09_06="yes",
            license=COJ_LIC,
            coverage="metro",
            coverage_class="metro",
        )
        defaults.update(kw)
        rows.append(L(**defaults))

    jhb(
        layer_id="coj_2000",
        layer_name="AerialPhotography/2000",
        capture_year=2000,
        capture_label="2000 year-only",
        gsd_m=0.25,
        gsd_source="REST pixelSizeX",
        crs="Lo29 TM",
        usable_as_panel_year="secondary",
        trap="",
        note="Same class as GEHI z19; early rooftop PV rare. Guaranteed-absent baseline.",
    )
    jhb(
        layer_id="coj_2003",
        layer_name="AerialPhotography/2003",
        capture_year=2003,
        capture_label="2003 year-only",
        gsd_m=0.25,
        gsd_source="REST pixelSizeX",
        crs="Lo29 TM",
        usable_as_panel_year="secondary",
        trap="",
        note="",
    )
    jhb(
        layer_id="coj_2006",
        layer_name="AerialPhotography/2006",
        capture_year=2006,
        capture_label="2006 year-only",
        gsd_m=0.20,
        gsd_source="REST pixelSizeX",
        crs="Lo29 TM",
        usable_as_panel_year="secondary",
        trap="",
        note="",
    )
    for year in (2009, 2012, 2015, 2019):
        extra = ""
        if year == 2009:
            extra = "First 15 cm year."
        elif year == 2015:
            extra = "Already in coj_arcgis_fetch.py; Imagery_Year present."
        elif year == 2019:
            extra = "Already used in ISSUE-08/09/29."
        jhb(
            layer_id=f"coj_{year}",
            layer_name=f"AerialPhotography/{year}",
            capture_year=year,
            capture_label=f"{year} year-only",
            gsd_m=0.15,
            gsd_source="REST pixelSizeX",
            crs="Lo29 TM",
            usable_as_panel_year="yes",
            trap="",
            note=extra,
        )
    jhb(
        layer_id="coj_2023",
        layer_name="AerialPhotography/2023",
        capture_year=2023,
        capture_label="May 2023 flight (CGIS FAQ); year-only on ImageServer",
        gsd_m=0.15,
        gsd_source="REST pixelSizeX / publisher 15 cm",
        crs="EPSG:3857",
        usable_as_panel_year="yes",
        trap="",
        note="Already used. CRS differs from 2015/2019. Do not mix without a project step.",
    )
    jhb(
        layer_id="coj_aerial_mosaic",
        layer_name="AerialPhotography/CoJ_Aerial",
        capture_year="",
        capture_label="2000–2019 mosaic; Imagery_Year field",
        gsd_m=0.15,
        gsd_source="REST pixelSizeX",
        crs="Lo29 TM",
        endpoint=f"{COJ}/CoJ_Aerial/ImageServer",
        usable_as_panel_year="trap",
        trap="multi_year_mosaic",
        note="Not a single flight. Do not use as one panel year. 2023 is not in this stack.",
    )
    jhb(
        layer_id="coj_vivid_2025_2026",
        layer_name="SatelliteImagery/2025_2026",
        family="hosted_satellite",
        capture_year="",
        capture_label="service title 2025/2026; no scene date; 假设 Maxar Vivid 30 cm blend",
        gsd_m=0.30,
        gsd_source="REST pixelSizeX 0.299 m EPSG:3857",
        crs="EPSG:3857",
        endpoint="https://ags.joburg.org.za/server/rest/services/SatelliteImagery/2025_2026/ImageServer",
        usable_as_panel_year="trap",
        trap="blended_satellite_not_flight",
        note="Not a true-date aerial. Post-2023 look only.",
    )
    jhb(
        layer_id="coj_oblique_2019",
        layer_name="AerialObliqueImagery/2019",
        capture_year=2019,
        capture_label="2019 year-only",
        gsd_m=0.053,
        gsd_source="REST pixelSizeX; oblique not nadir",
        crs="Lo29 TM",
        endpoint="https://ags.joburg.org.za/server/rest/services/AerialObliqueImagery/2019/ImageServer",
        coverage="metro-ish, slightly larger than ortho",
        coverage_class="metro",
        usable_as_panel_year="no",
        trap="",
        note="Oblique. QA overlay only; do not feed the nadir detector.",
    )

    def eth(**kw):
        rows.append(
            L(
                family="municipal",
                municipality="ethekwini",
                municipality_name="eThekwini",
                publisher="eThekwini Corporate GIS",
                access_method="ArcGIS REST ImageServer (TLS untrusted)",
                live_2026_09_06=kw.pop("live_2026_09_06", "yes"),
                license=ETH_LIC,
                **kw,
            )
        )

    eth(
        layer_id="eth_1994",
        layer_name="RasterCatalogueProject/AerialPhotography1994",
        capture_year=1994,
        capture_label="1994",
        gsd_m=0.80,
        gsd_source="REST pixelSizeX; 1-band greyscale",
        coverage="metro-ish",
        coverage_class="metro",
        crs="WG31 / Lo31",
        endpoint=f"{ETH}/RasterCatalogueProject/AerialPhotography1994/ImageServer",
        usable_as_panel_year="no",
        trap="",
        note="Pre-PV; heritage only.",
    )
    eth(
        layer_id="eth_1997",
        layer_name="AerialPhotography1997",
        capture_year=1997,
        capture_label="1997",
        gsd_m=1.0,
        gsd_source="REST pixelSizeX",
        coverage="partial (smaller box)",
        coverage_class="partial",
        crs="WG31 / Lo31",
        endpoint=f"{ETH}/AerialPhotography1997/ImageServer",
        usable_as_panel_year="no",
        trap="",
        note="Too coarse + incomplete for rooftop PV.",
    )
    eth(
        layer_id="eth_2001",
        layer_name="RasterCatalogueProject/AerialPhotography2001",
        capture_year=2001,
        capture_label="2001",
        gsd_m="",
        gsd_source="UNKNOWN (empty ImageServer JSON)",
        coverage="UNKNOWN",
        coverage_class="unknown",
        crs="",
        endpoint=f"{ETH}/RasterCatalogueProject/AerialPhotography2001/ImageServer",
        live_2026_09_06="service_500",
        usable_as_panel_year="no",
        trap="service_not_instantiated",
        note="GISService not instantiated. Do not use until it answers.",
    )
    eth(
        layer_id="eth_2001r",
        layer_name="RasterCatalogueProject/AerialPhotography2001_R",
        capture_year=2001,
        capture_label="2001",
        gsd_m=0.30,
        gsd_source="REST pixelSizeX",
        coverage="west-truncated",
        coverage_class="partial",
        crs="WG31 / Lo31",
        endpoint=f"{ETH}/RasterCatalogueProject/AerialPhotography2001_R/ImageServer",
        usable_as_panel_year="secondary",
        trap="",
        note="First 30 cm year; coverage hole on the far west.",
    )
    for year, lid, name, coverage, cclass, usable, trap, note, gsd in [
        (2002, "eth_2002", "RasterCatalogueProject/AerialPhotography2002", "partial (misses the coast)", "partial", "no", "incomplete_coverage", "Not a citywide panel year.", 0.30),
        (2002, "eth_2002b", "RasterCatalogueProject/AerialPhotography2002b", "same partial box as 2002", "partial", "duplicate", "duplicate_vintage", "Sibling of 2002; treat as one vintage.", 0.30),
        (2003, "eth_2003", "RasterCatalogueProject/AerialPhotography2003", "metro-ish", "metro", "no", "", "0.54 m; coarser than 2001_R; borderline for small arrays.", 0.54),
        (2004, "eth_2004b", "RasterCatalogueProject/AerialPhotography2004b", "metro", "metro", "secondary", "", "Usable 30 cm year.", 0.30),
        (2005, "eth_2005", "RasterCatalogueProject/AerialPhotograph2005", "metro", "metro", "secondary", "", "Note spelling Photograph not Photography.", 0.35),
    ]:
        eth(
            layer_id=lid,
            layer_name=name,
            capture_year=year,
            capture_label=str(year),
            gsd_m=gsd,
            gsd_source="REST pixelSizeX",
            coverage=coverage,
            coverage_class=cclass,
            crs="WG31 / Lo31",
            endpoint=f"{ETH}/{name}/ImageServer",
            usable_as_panel_year=usable,
            trap=trap,
            note=note,
        )
    for year in range(2006, 2016):
        eth(
            layer_id=f"eth_{year}",
            layer_name=f"RasterCatalogueProject/AerialPhotography{year}",
            capture_year=year,
            capture_label=str(year),
            gsd_m=0.30,
            gsd_source="REST pixelSizeX",
            coverage="metro" + (" (xmax slightly further east)" if year == 2010 else " (slightly wider west)" if year == 2015 else ""),
            coverage_class="metro",
            crs="WG31 / Lo31",
            endpoint=f"{ETH}/RasterCatalogueProject/AerialPhotography{year}/ImageServer",
            usable_as_panel_year="secondary",
            trap="",
            note="Last 30 cm year before the 10 cm jump." if year == 2015 else "Same class as GEHI z19; small arrays risky.",
        )
    eth(
        layer_id="eth_2016",
        layer_name="RasterCatalogueProject/AerialPhotography2016",
        capture_year=2016,
        capture_label="2016",
        gsd_m=0.09,
        gsd_source="REST pixelSizeX",
        coverage="metro",
        coverage_class="metro",
        crs="WG31 / Lo31",
        endpoint=f"{ETH}/RasterCatalogueProject/AerialPhotography2016/ImageServer",
        usable_as_panel_year="yes",
        trap="",
        note="First ~10 cm year; rooftop-resolvable.",
    )
    eth(
        layer_id="eth_oblique_2016",
        layer_name="oblique2016/Oblique2016",
        capture_year=2016,
        capture_label="2016",
        gsd_m=0.066,
        gsd_source="REST; RGB + camera pose",
        coverage="metro-ish",
        coverage_class="metro",
        crs="WG31 / Lo31",
        endpoint=f"{ETH}/oblique2016/Oblique2016/ImageServer",
        usable_as_panel_year="no",
        trap="",
        note="Oblique, not nadir. QA overlay only.",
    )
    eth(
        layer_id="eth_2018",
        layer_name="RasterCatalogueProject/AerialPhotography2018",
        capture_year=2018,
        capture_label="2018",
        gsd_m=0.10,
        gsd_source="REST pixelSizeX",
        coverage="metro",
        coverage_class="metro",
        crs="EPSG:3857",
        endpoint=f"{ETH}/RasterCatalogueProject/AerialPhotography2018/ImageServer",
        usable_as_panel_year="yes",
        trap="",
        note="CRS trap vs the WG31 stack. Do not mix without a project step.",
    )
    eth(
        layer_id="eth_2019",
        layer_name="2019/AerialPhotography",
        capture_year=2019,
        capture_label="2019",
        gsd_m=0.09,
        gsd_source="REST pixelSizeX (MRF mosaic)",
        coverage="metro",
        coverage_class="metro",
        crs="WG31 / Lo31",
        endpoint=f"{ETH}/2019/AerialPhotography/ImageServer",
        usable_as_panel_year="yes",
        trap="",
        note="",
    )
    eth(
        layer_id="eth_2020",
        layer_name="AerialPhotography/AerialPhoto2020",
        capture_year=2020,
        capture_label="2020",
        gsd_m=0.10,
        gsd_source="REST pixelSizeX",
        coverage="metro",
        coverage_class="metro",
        crs="WG31 / Lo31",
        endpoint=f"{ETH}/AerialPhotography/AerialPhoto2020/ImageServer",
        usable_as_panel_year="yes",
        trap="",
        note="Prefer this over the root duplicate Aerial_Imagery_2020.",
    )
    eth(
        layer_id="eth_2020_dup",
        layer_name="Aerial_Imagery_2020",
        capture_year=2020,
        capture_label="2020",
        gsd_m=0.10,
        gsd_source="REST pixelSizeX",
        coverage="same box as AerialPhoto2020",
        coverage_class="metro",
        crs="WG31 / Lo31",
        endpoint=f"{ETH}/Aerial_Imagery_2020/ImageServer",
        usable_as_panel_year="duplicate",
        trap="duplicate_service",
        note="Duplicate of AerialPhoto2020.",
    )
    eth(
        layer_id="eth_20212022",
        layer_name="AerialPhotography/AerialPhotography20212022",
        capture_year="",
        capture_label="2021–2022 (name is a range)",
        gsd_m=0.10,
        gsd_source="REST pixelSizeX",
        coverage="metro",
        coverage_class="metro",
        crs="WG31 / Lo31",
        endpoint=f"{ETH}/AerialPhotography/AerialPhotography20212022/ImageServer",
        usable_as_panel_year="trap",
        trap="year_range_not_one_flight",
        note="Not a single flight. Solar Map viewer backdrop. Do not use as one panel year.",
    )
    eth(
        layer_id="eth_2022",
        layer_name="AerialPhotography/AerialPhotography2022",
        capture_year=2022,
        capture_label="2022",
        gsd_m=0.10,
        gsd_source="REST pixelSizeX",
        coverage="metro",
        coverage_class="metro",
        crs="WG31 / Lo31",
        endpoint=f"{ETH}/AerialPhotography/AerialPhotography2022/ImageServer",
        usable_as_panel_year="yes",
        trap="",
        note="Clean 10 cm year.",
    )
    eth(
        layer_id="eth_2022rsm",
        layer_name="AerialPhotography2022RSM",
        capture_year=2022,
        capture_label="2022",
        gsd_m=0.10,
        gsd_source="REST pixelSizeX",
        coverage="same extent as AerialPhotography2022",
        coverage_class="metro",
        crs="WG31 / Lo31",
        endpoint=f"{ETH}/AerialPhotography2022RSM/ImageServer",
        usable_as_panel_year="duplicate",
        trap="duplicate_resample",
        note="假设 resampled/reprojected twin; do not double-count.",
    )
    eth(
        layer_id="eth_2023",
        layer_name="AerialPhotography/AerialPhotography2023",
        capture_year=2023,
        capture_label="2023",
        gsd_m=0.10,
        gsd_source="REST pixelSizeX",
        coverage="west only (xmax −13 km vs +18 km). Misses Durban CBD/coast",
        coverage_class="partial",
        crs="WG31 / Lo31",
        endpoint=f"{ETH}/AerialPhotography/AerialPhotography2023/ImageServer",
        usable_as_panel_year="no",
        trap="west_only_misses_cbd",
        note="Cannot be a citywide panel year. CBD is east of that cut.",
    )
    eth(
        layer_id="eth_2025_lowrange",
        layer_name="AerialPhotography/LowRangeAerialPhotography2025",
        capture_year=2025,
        capture_label="2025",
        gsd_m=0.05,
        gsd_source="REST pixelSizeX; 4 bands",
        coverage="~11 × 15 km coastal patch",
        coverage_class="patch",
        crs="EPSG:3857",
        endpoint=f"{ETH}/AerialPhotography/LowRangeAerialPhotography2025/ImageServer",
        usable_as_panel_year="no",
        trap="drone_patch_not_metro",
        note="假设 drone / low-range AOI. Not metro.",
    )

    def eku(**kw):
        family = kw.pop("family", "municipal")
        publisher = kw.pop("publisher", "CoE GIS")
        access_method = kw.pop("access_method", "ArcGIS REST ImageServer")
        license_ = kw.pop("license", EKU_LIC)
        rows.append(
            L(
                family=family,
                municipality="ekurhuleni",
                municipality_name="City of Ekurhuleni",
                publisher=publisher,
                access_method=access_method,
                live_2026_09_06="yes",
                license=license_,
                **kw,
            )
        )

    eku(
        layer_id="eku_2002",
        layer_name="Imagery/OrthoPhotos_2002",
        capture_year=2002,
        capture_label="2002 year-only",
        gsd_m=0.50,
        gsd_source="REST pixelSizeX; 1-band greyscale",
        coverage="metro",
        coverage_class="metro",
        crs="Lo29 TM",
        endpoint=f"{EKU}/OrthoPhotos_2002/ImageServer",
        usable_as_panel_year="no",
        trap="",
        note="50 cm greyscale — marginal for small residential PV.",
    )
    eku(
        layer_id="eku_2003",
        layer_name="Imagery/OrthoPhotos_2003",
        capture_year=2003,
        capture_label="2003 year-only",
        gsd_m=0.42,
        gsd_source="REST pixelSizeX; 1-band",
        coverage="metro",
        coverage_class="metro",
        crs="Lo29 TM",
        endpoint=f"{EKU}/OrthoPhotos_2003/ImageServer",
        usable_as_panel_year="no",
        trap="",
        note="Greyscale.",
    )
    eku(
        layer_id="eku_2005",
        layer_name="Imagery/OrthoPhoto_2005",
        capture_year=2005,
        capture_label="2005",
        gsd_m=0.60,
        gsd_source="REST pixelSizeX",
        coverage="metro",
        coverage_class="metro",
        crs="Lo29 TM",
        endpoint=f"{EKU}/OrthoPhoto_2005/ImageServer",
        usable_as_panel_year="no",
        trap="",
        note="Too coarse.",
    )
    eku(
        layer_id="eku_2007",
        layer_name="Imagery/OrthoPhoto_2007",
        capture_year=2007,
        capture_label="June 2007 (viewer)",
        gsd_m=0.20,
        gsd_source="REST pixelSizeX",
        coverage="metro",
        coverage_class="metro",
        crs="Lo29 TM",
        endpoint=f"{EKU}/OrthoPhoto_2007/ImageServer",
        usable_as_panel_year="secondary",
        trap="",
        note="First colour vintage that can see larger arrays.",
    )
    eku(
        layer_id="eku_2010",
        layer_name="Imagery/OrthoPhoto_2010",
        capture_year=2010,
        capture_label="May 2010 (viewer)",
        gsd_m=0.20,
        gsd_source="REST pixelSizeX",
        coverage="metro",
        coverage_class="metro",
        crs="Lo29 TM",
        endpoint=f"{EKU}/OrthoPhoto_2010/ImageServer",
        usable_as_panel_year="secondary",
        trap="",
        note="",
    )
    eku(
        layer_id="eku_2013",
        layer_name="Imagery/Orthophoto_2013",
        capture_year=2013,
        capture_label="May 2013 (viewer)",
        gsd_m=0.10,
        gsd_source="REST pixelSizeX",
        coverage="metro",
        coverage_class="metro",
        crs="EPSG:3857",
        endpoint=f"{EKU}/Orthophoto_2013/ImageServer",
        usable_as_panel_year="yes",
        trap="",
        note="First 10 cm year. Start of a usable residential-PV series.",
    )
    eku(
        layer_id="eku_2015",
        layer_name="Imagery/Orthophoto MapServer L24 2015",
        capture_year=2015,
        capture_label="October 2015 (viewer). No dedicated ImageServer",
        gsd_m=0.15,
        gsd_source="mosaic footprints LowPS=0.15 m",
        coverage="metro",
        coverage_class="metro",
        crs="",
        endpoint=f"{EKU}/Orthophoto/MapServer",
        access_method="REST MapServer mosaic / WMS",
        usable_as_panel_year="secondary",
        trap="",
        note="True-date 15 cm. Must be pulled from the combined MapServer.",
    )
    eku(
        layer_id="eku_2018",
        layer_name="Imagery/OrthoPhoto_2018",
        capture_year=2018,
        capture_label="October 2018 (viewer) / November 2018 (older metadata)",
        gsd_m=0.10,
        gsd_source="REST pixelSizeX",
        coverage="metro",
        coverage_class="metro",
        crs="Lo29 TM",
        endpoint=f"{EKU}/OrthoPhoto_2018/ImageServer",
        usable_as_panel_year="yes",
        trap="",
        note="Year is solid; month 10 vs 11 is a catalog conflict.",
    )
    eku(
        layer_id="eku_2019_20",
        layer_name="Imagery/Aerial_Photos2020",
        capture_year="",
        capture_label="combined MapServer labels this 2019/20",
        gsd_m=0.10,
        gsd_source="REST pixelSizeX",
        coverage="metro",
        coverage_class="metro",
        crs="Lo29 TM",
        endpoint=f"{EKU}/Aerial_Photos2020/ImageServer",
        usable_as_panel_year="yes",
        trap="",
        note="True-date-ish 10 cm. Year is 2019/20, not a single month. SRC usable-years lists 2020.",
    )
    eku(
        layer_id="eku_ngi_2023",
        layer_name="Imagery/NGI_MOSAIC_2023",
        family="ngi",
        publisher="CD:NGI hosted by CoE",
        capture_year=2023,
        capture_label="2023 NGI programme year",
        gsd_m=0.25,
        gsd_source="REST pixelSizeX",
        coverage="regional, wider than metro",
        coverage_class="regional",
        crs="EPSG:3857",
        endpoint=f"{EKU}/NGI_MOSAIC_2023/ImageServer",
        license=NGI_LIC,
        usable_as_panel_year="secondary",
        trap="",
        note="National 25 cm, not a municipal camera flight. GEHI-z19 class.",
    )
    eku(
        layer_id="eku_ngi_2024",
        layer_name="Imagery/NGI_MOSAIC_2024",
        family="ngi",
        publisher="CD:NGI hosted by CoE",
        capture_year=2024,
        capture_label="2024; catalog names 2528CA_##_2024_1835_RGB_RECT",
        gsd_m=0.25,
        gsd_source="degrees ≈ 0.25 m",
        coverage="regional 27–29E, −27 to −25.5",
        coverage_class="regional",
        crs="EPSG:4326",
        endpoint=f"{EKU}/NGI_MOSAIC_2024/ImageServer",
        license=NGI_LIC,
        usable_as_panel_year="secondary",
        trap="",
        note="Best post-2020 true-date layer on this metro until CoE flies again. Still 25 cm.",
    )
    eku(
        layer_id="eku_sansa_2024",
        layer_name="Imagery/SANSA_2024",
        family="hosted_satellite",
        publisher="SANSA hosted by CoE",
        capture_year=2024,
        capture_label="2024 year-only",
        gsd_m=0.50,
        gsd_source="degrees ≈ 0.50 m",
        coverage="metro + buffer",
        coverage_class="metro",
        crs="EPSG:4326",
        endpoint=f"{EKU}/SANSA_2024/ImageServer",
        license="SANSA copyright 假设",
        usable_as_panel_year="no",
        trap="satellite_not_aerial",
        note="50 cm satellite. Too coarse for small rooftop PV.",
    )
    eku(
        layer_id="eku_orthophoto_2026",
        layer_name="Imagery/Orthophoto_2026 MapServer",
        capture_year="",
        capture_label="name 2026; layers are 2024 NGI + 2023 NGI + 2024 SANSA + 2019/20",
        gsd_m="",
        gsd_source="n/a (viewer stack)",
        coverage="metro",
        coverage_class="metro",
        crs="",
        endpoint=f"{EKU}/Orthophoto_2026/MapServer",
        access_method="REST MapServer",
        usable_as_panel_year="trap",
        trap="name_2026_is_viewer_not_flight",
        note="Name 2026 is a trap — not a 2026 municipal flight.",
    )
    eku(
        layer_id="eku_oblique_2022",
        layer_name="Imagery/Oblique2022",
        capture_year=2022,
        capture_label="2022",
        gsd_m=0.06,
        gsd_source="REST; SensorAzimuth / CameraID",
        coverage="smaller than metro",
        coverage_class="partial",
        crs="Lo29 TM",
        endpoint=f"{EKU}/Oblique2022/ImageServer",
        usable_as_panel_year="no",
        trap="",
        note="Oblique. QA only.",
    )

    def tsh(**kw):
        rows.append(
            L(
                family="municipal",
                municipality="tshwane",
                municipality_name="City of Tshwane",
                publisher="City of Tshwane Corporate Geo-Information",
                access_method="ArcGIS REST ImageServer",
                live_2026_09_06="yes",
                license=TSH_LIC,
                coverage="metro",
                coverage_class="metro",
                **kw,
            )
        )

    tsh(
        layer_id="tsh_2018",
        layer_name="Imagery/AERIAL_2018",
        capture_year=2018,
        capture_label="2018 year-only; month UNKNOWN",
        gsd_m=0.10,
        gsd_source="REST pixelSizeX",
        crs="Lo29 / TM29 (WG29)",
        endpoint=f"{TSH}/AERIAL_2018/ImageServer",
        usable_as_panel_year="yes",
        trap="",
        note="First dated municipal vintage on REST. Fine enough for residential PV.",
    )
    tsh(
        layer_id="tsh_2018_tiff",
        layer_name="Imagery/AERIAL_2018_TIFF",
        capture_year=2018,
        capture_label="2018",
        gsd_m=0.10,
        gsd_source="REST pixelSizeX",
        crs="Lo29",
        endpoint=f"{TSH}/AERIAL_2018_TIFF/ImageServer",
        usable_as_panel_year="duplicate",
        trap="duplicate_storage_format",
        note="Same 2018 flight, TIFF mosaic. Do not count as a second date.",
    )
    tsh(
        layer_id="tsh_2018_wm",
        layer_name="Imagery/Aerial2018_WM",
        capture_year=2018,
        capture_label="2018",
        gsd_m=0.10,
        gsd_source="REST pixelSizeX",
        crs="EPSG:3857 cached",
        endpoint=f"{TSH}/Aerial2018_WM/ImageServer",
        usable_as_panel_year="duplicate",
        trap="duplicate_cache",
        note="Same 2018 flight, Web-Mercator cache. Prefer for chip export in 3857.",
    )
    tsh(
        layer_id="tsh_2023",
        layer_name="Imagery/Aerial_2023",
        capture_year=2023,
        capture_label="2023 year-only; catalog names NGI-style 10k IDs",
        gsd_m=0.10,
        gsd_source="REST pixelSizeX",
        crs="EPSG:3857",
        endpoint=f"{TSH}/Aerial_2023/ImageServer",
        usable_as_panel_year="yes",
        trap="",
        note="Together with 2018 this is a two-epoch panel. Tile IDs are sheet names, not capture dates.",
    )
    tsh(
        layer_id="tsh_2024",
        layer_name="Imagery/Aerial_2024",
        capture_year=2024,
        capture_label="service name 2024; same 2527BD## IDs as 2023/2025",
        gsd_m=0.10,
        gsd_source="REST pixelSizeX",
        crs="EPSG:3857",
        endpoint=f"{TSH}/Aerial_2024/ImageServer",
        usable_as_panel_year="unproven",
        trap="same_sheet_ids_as_2023",
        note="假设 a new 2024 flight, not proven. Needs a visual chip-diff. Extent ymax corrupted in metadata.",
    )
    tsh(
        layer_id="tsh_2025",
        layer_name="Imagery/Aerial_2025",
        capture_year=2025,
        capture_label="service name 2025; same 2527BD## names",
        gsd_m=0.10,
        gsd_source="REST pixelSizeX",
        crs="Lo29-like (wkid not set)",
        endpoint=f"{TSH}/Aerial_2025/ImageServer",
        usable_as_panel_year="unproven",
        trap="same_sheet_ids_as_2023",
        note="Same caveat as 2024. Do not add 2024 and 2025 until chips differ.",
    )

    def nmb(**kw):
        family = kw.pop("family", "municipal")
        publisher = kw.pop("publisher", "NMBM Corporate GIS")
        license_ = kw.pop("license", NMB_LIC)
        rows.append(
            L(
                family=family,
                municipality="nelson_mandela_bay",
                municipality_name="Nelson Mandela Bay",
                publisher=publisher,
                access_method="ImageServer via public resource-proxy",
                live_2026_09_06="yes",
                license=license_,
                **kw,
            )
        )

    nmb(
        layer_id="nmb_1997",
        layer_name="Aerial/Aerial_1997",
        capture_year=1997,
        capture_label="1997",
        gsd_m=0.25,
        gsd_source="REST pixelSizeX",
        coverage="core PE, not full metro",
        coverage_class="partial",
        crs="TM25",
        endpoint=f"{NMB_PROXY}/Aerial/Aerial_1997/ImageServer",
        usable_as_panel_year="yes",
        trap="",
        note="Pre-rooftop-PV baseline at 25 cm, core PE only. SRC keeps 1997 in the usable-year list at the coarse end of 0.25→0.05 m.",
    )
    nmb(
        layer_id="nmb_2004",
        layer_name="Aerial/Aerial_2004",
        capture_year=2004,
        capture_label="2004",
        gsd_m=0.25,
        gsd_source="REST pixelSizeX",
        coverage="broader urban",
        coverage_class="metro",
        crs="TM25",
        endpoint=f"{NMB_PROXY}/Aerial/Aerial_2004/ImageServer",
        usable_as_panel_year="yes",
        trap="",
        note="Early baseline. Rooftop PV still rare.",
    )
    nmb(
        layer_id="nmb_2007_2004",
        layer_name="Aerial/Aerial_2007_2004",
        capture_year="",
        capture_label="named 2007/2004 mix",
        gsd_m=0.25,
        gsd_source="REST pixelSizeX",
        coverage="same envelope as 2004",
        coverage_class="metro",
        crs="TM25",
        endpoint=f"{NMB_PROXY}/Aerial/Aerial_2007_2004/ImageServer",
        usable_as_panel_year="trap",
        trap="mixed_2007_onto_2004",
        note="假设 a 2007 update mosaicked onto 2004. Confirm seam dates before using as 2007.",
    )
    nmb(
        layer_id="nmb_2009",
        layer_name="Aerial/Aerial_2009",
        capture_year=2009,
        capture_label="2009",
        gsd_m=0.10,
        gsd_source="REST pixelSizeX",
        coverage="urban core",
        coverage_class="metro",
        crs="TM25",
        endpoint=f"{NMB_PROXY}/Aerial/Aerial_2009/ImageServer",
        usable_as_panel_year="yes",
        trap="",
        note="First 10 cm municipal epoch.",
    )
    nmb(
        layer_id="nmb_2009_ngi",
        layer_name="Aerial/Aerial_2009_NGI",
        family="ngi",
        publisher="NGI republished by NMBM",
        capture_year=2009,
        capture_label="2009 NGI",
        gsd_m=0.50,
        gsd_source="pixelSize ≈ 4.49e-6 deg",
        coverage="wider than municipal 10 cm 2009",
        coverage_class="regional",
        crs="EPSG:4326",
        endpoint=f"{NMB_PROXY}/Aerial/Aerial_2009_NGI/ImageServer",
        license=NGI_LIC,
        usable_as_panel_year="secondary",
        trap="",
        note="Fills rural/Coega fringe that 10 cm 2009 misses.",
    )
    nmb(
        layer_id="nmb_2011",
        layer_name="Aerial/Aerial_2011",
        capture_year=2011,
        capture_label="2011",
        gsd_m=0.10,
        gsd_source="REST pixelSizeX",
        coverage="urban",
        coverage_class="metro",
        crs="TM25",
        endpoint=f"{NMB_PROXY}/Aerial/Aerial_2011/ImageServer",
        usable_as_panel_year="yes",
        trap="",
        note="Second 10 cm epoch.",
    )
    nmb(
        layer_id="nmb_2013",
        layer_name="Aerial/Aerial_2013",
        capture_year=2013,
        capture_label="Nov 2012 – Feb 2013 (service description)",
        gsd_m=0.10,
        gsd_source="REST pixelSizeX",
        coverage="same as 2011",
        coverage_class="metro",
        crs="TM25",
        endpoint=f"{NMB_PROXY}/Aerial/Aerial_2013/ImageServer",
        usable_as_panel_year="yes",
        trap="",
        note="Best-dated vintage in the stack.",
    )
    nmb(
        layer_id="nmb_2017",
        layer_name="Aerial/Aerials_2017",
        capture_year=2017,
        capture_label="2017",
        gsd_m=0.05,
        gsd_source="REST pixelSizeX",
        coverage="near-metro",
        coverage_class="metro",
        crs="TM25",
        endpoint=f"{NMB_PROXY}/Aerial/Aerials_2017/ImageServer",
        usable_as_panel_year="yes",
        trap="",
        note="First 5 cm epoch. Pairs with 355,880 LiDAR 2017 footprints.",
    )
    nmb(
        layer_id="nmb_2018",
        layer_name="Aerial/Aerials_2018",
        capture_year=2018,
        capture_label="2018",
        gsd_m=0.05,
        gsd_source="REST pixelSizeX",
        coverage="slightly smaller than 2017/2022",
        coverage_class="metro",
        crs="TM25",
        endpoint=f"{NMB_PROXY}/Aerial/Aerials_2018/ImageServer",
        usable_as_panel_year="yes",
        trap="",
        note="Public viewer operational layer. 2017 vs 2018 is only a 1-year step.",
    )
    nmb(
        layer_id="nmb_2022",
        layer_name="Aerial/Aerials_2022",
        capture_year=2022,
        capture_label="2022",
        gsd_m=0.05,
        gsd_source="REST pixelSizeX",
        coverage="same envelope as 2017",
        coverage_class="metro",
        crs="TM25",
        endpoint=f"{NMB_PROXY}/Aerial/Aerials_2022/ImageServer",
        usable_as_panel_year="yes",
        trap="",
        note="Latest municipal ortho. No 2019–2021 municipal vintage found.",
    )
    nmb(
        layer_id="nmb_oblique_2022",
        layer_name="Obliques/Obliques_2022",
        capture_year=2022,
        capture_label="2022",
        gsd_m=0.033,
        gsd_source="REST; oblique not nadir",
        coverage="wider than ortho",
        coverage_class="metro",
        crs="TM25",
        endpoint=f"{NMB_PROXY}/Obliques/Obliques_2022/ImageServer",
        usable_as_panel_year="no",
        trap="",
        note="Facade/roof confirmation, not a dating grid.",
    )

    # No municipal ImageServer: Buffalo, Mangaung, Msunduzi — city summary only.

    # Vexcel collections (one aerial vintage per city / tile)
    vex_rows = [
        ("cape_town", "City of Cape Town", "za-wc-avondale-2024", "2024-04-11", "2024-04-11", 0.0622, "Atlantis patch; 0 of 381 census H3-7 cells", "patch"),
        ("cape_town", "City of Cape Town", "za-wc-bellville-2024", "2024-03-27", "2024-03-30", 0.0619, "Big eastern tile; 196 of 381 census cells", "partial"),
        ("cape_town", "City of Cape Town", "za-wc-capetown-2024", "2024-04-12", "2024-04-12", 0.0619, "CBD tile; 17 of 381 census cells", "partial"),
        ("cape_town", "City of Cape Town", "za-wc-rondebosch-2024", "2024-04-12", "2024-04-12", 0.0620, "Peninsula tile; 74 of 381 census cells", "partial"),
        ("cape_town", "City of Cape Town", "za-wc-tablemountain-2024", "2024-03-18", "2024-03-20", 0.0619, "Peninsula tile; 85 of 381 census cells", "partial"),
        ("johannesburg", "City of Johannesburg", "za-gp-johannesburg-2024", "2024-02-17", "2024-04-19", 0.0669, "Inner-city 2024 footprint; Sandton/Soweto/Randburg 404", "partial"),
        ("ekurhuleni", "City of Ekurhuleni", "za-gp-johannesburg-2024", "2024-02-17", "2024-04-19", 0.0669, "JHB spill only; no own collection; 23 H3-7 cells", "spill"),
        ("tshwane", "City of Tshwane", "za-gp-pretoria-2026", "2026-02-05", "2026-02-08", 0.0672, "Hatfield/CBD/Mamelodi hit; Centurion/Akasia 404", "partial"),
        ("ethekwini", "eThekwini", "za-kzn-durban-2026", "2025-12-17", "2026-01-06", 0.0647, "All five probe points hit", "partial"),
        ("nelson_mandela_bay", "Nelson Mandela Bay", "za-ec-portelizabeth-2026", "2026-01-11", "2026-01-21", 0.0619, "Gqeberha urban centre fully inside", "partial"),
        ("buffalo_city", "Buffalo City", "za-ec-eastlondon-2026", "2026-01-07", "2026-01-08", 0.0626, "Mdantsane and King William's Town 404", "partial"),
        ("mangaung", "Mangaung", "za-fs-bloemfontein-2026", "2026-01-18", "2026-01-18", 0.0652, "Bloemfontein city; Botshabelo 404", "partial"),
        ("msunduzi", "Msunduzi", "za-kzn-pietermaritzburg-2025", "2025-12-16", "2025-12-16", 0.0649, "CBD, Edendale, Northdale all hit", "partial"),
    ]
    for muni, name, coll, first, last, gsd, note, cclass in vex_rows:
        rows.append(
            L(
                family="vexcel",
                municipality=muni,
                municipality_name=name,
                layer_id=f"vexcel_{coll}" + ("_ekurhuleni" if muni == "ekurhuleni" else ""),
                layer_name=coll,
                publisher="Vexcel urban ortho (LSE academic token)",
                capture_year=int(first[:4]),
                capture_label=f"{first} … {last}" if first != last else first,
                gsd_m=gsd,
                gsd_source="live 120 m extract EPSG:3857 × cos(lat)",
                coverage=note,
                coverage_class=cclass,
                crs="EPSG:3857",
                access_method="Vexcel API v2 layer=urban",
                endpoint=f"{VEX}?collection={coll}",
                live_2026_09_06="yes",
                license=VEX_LIC,
                usable_as_panel_year="no",
                trap="one_season_not_a_year_stack" if muni != "cape_town" else "five_tiles_are_one_2024_season",
                note="One aerial vintage, not a time stack. Use as high-res present, not the time axis. Cape Town five ids are spatial tiles of March–April 2024.",
            )
        )

    # National NGI
    ngi_common = dict(
        family="ngi",
        municipality="national",
        municipality_name="South Africa (CD:NGI)",
        publisher="CD:NGI / DLRRD",
        live_2026_09_06="yes",
        license=NGI_LIC,
        usable_as_panel_year="secondary",
    )
    rows.append(
        L(
            **ngi_common,
            layer_id="ngi_50cm_mosaic",
            layer_name="CDNGI_Imagery_50cm_MOSAIC",
            capture_year="",
            capture_label="2008–2016 digital 0.50 m programme (sheet-dated)",
            gsd_m=0.50,
            gsd_source="NGI spec",
            coverage="national",
            coverage_class="national",
            crs="",
            access_method="NGI WMS backdrop",
            endpoint=NGI_WMS,
            trap="",
            note="Fallback, not better than GEHI z19 for small arrays. Date from sheet year, not WMS layer name.",
        )
    )
    rows.append(
        L(
            **ngi_common,
            layer_id="ngi_25cm_programme",
            layer_name="CDNGI 25 cm digital aerials 2017+",
            capture_year="",
            capture_label="2017–current; ~3–5 year national cycle; sheet-dated",
            gsd_m=0.25,
            gsd_source="NGI spec",
            coverage="national (urban priority 假设)",
            coverage_class="national",
            crs="",
            access_method="geoportal Clip-Zip-Ship + WMS mosaics",
            endpoint="https://ngigeoportal.dlrrd.gov.za/cdngiportal/",
            trap="",
            note="Same GSD class as GEHI z19. Does not fix the small-array problem. Date from filename/sheet, not mosaic layer year.",
        )
    )
    for lid, name, year, where in [
        ("ngi_gauteng_2024", "CDNGI_Imagery_25cm_MOSAIC_Gauteng_2024", 2024, "JHB / Tshwane / Ekurhuleni"),
        ("ngi_wc_2021", "CDNGI_Imagery_25cm_MOSAIC_Western_Cape_2021", 2021, "Cape Town metro; mosaic publish year, not sheet year"),
        ("ngi_kzn_2023", "CDNGI_Imagery_25cm_MOSAIC_KZN_2023", 2023, "eThekwini / Msunduzi"),
    ]:
        rows.append(
            L(
                **ngi_common,
                layer_id=lid,
                layer_name=name,
                capture_year=year,
                capture_label=f"WMS mosaic name {year}",
                gsd_m=0.25,
                gsd_source="WMS layer name",
                coverage=where,
                coverage_class="provincial_mosaic",
                crs="",
                access_method="NGI WMS backdrop",
                endpoint=NGI_WMS,
                trap="mosaic_year_is_not_sheet_year",
                note="定年用图幅年，不要用 WMS 图层名。",
            )
        )
    rows.append(
        L(
            **ngi_common,
            layer_id="ngi_esri_rsa_aerial",
            layer_name="NGI/RSA_NGI_AERIAL",
            capture_year="",
            capture_label="Esri SA republish; year in catalog Name (e.g. 3028CA_13_2009_173_RGB_RECT)",
            gsd_m=0.25,
            gsd_source="service pixelSize 0.25 m; sample LowPS=0.5 for 2008–2016 sheets",
            coverage="national",
            coverage_class="national",
            crs="EPSG:3857",
            access_method="Esri SA ImageServer Catalog",
            endpoint=NGI_ESRI,
            trap="",
            note="View/stream, not a bulk-download licence. One service, many sheet years.",
        )
    )
    return rows


def aerial_cities() -> list[dict]:
    return [
        {
            "municipality": "cape_town",
            "municipality_name": "City of Cape Town",
            "public_true_date_stack": "yes_almost_annual",
            "usable_years": "2012,2013,2015,2016,2017,2018,2019,2020,2021,2022,2025,2026",
            "n_usable_years": 12,
            "gsd_m_min": 0.05,
            "gsd_m_max": 0.08,
            "coverage": "metro; 2024 ~30% only; 2012 east truncated",
            "access_2026_09_06": "CoCT ERDAS MapServer / WMS, no login",
            "panel_verdict": "strongest year-level panel",
            "vexcel_collections": "za-wc-avondale-2024|za-wc-bellville-2024|za-wc-capetown-2024|za-wc-rondebosch-2024|za-wc-tablemountain-2024",
            "vexcel_capture": "2024-03-18 … 2024-04-12",
            "vexcel_gsd_m": 0.062,
            "vexcel_h3_metro_covered": 367,
            "vexcel_h3_metro_total": 603,
            "vexcel_h3_metro_rate": 0.6086,
            "vexcel_urban_centre_area_rate": 0.9327,
            "traps": "2023Jan=NGI 2022 25 cm; 2014_NGI mixed years; 2024 30% metro; Vexcel five tiles = one 2024 season",
            "notes": "Census layer is 2025Jan 5 cm. Pre-2000 heritage exists back to 1926, not PV-era.",
        },
        {
            "municipality": "johannesburg",
            "municipality_name": "City of Johannesburg",
            "public_true_date_stack": "yes_3year_cadence",
            "usable_years": "2009,2012,2015,2019,2023",
            "n_usable_years": 5,
            "gsd_m_min": 0.15,
            "gsd_m_max": 0.15,
            "coverage": "metro",
            "access_2026_09_06": "CoJ ImageServer AerialPhotography/<year>",
            "panel_verdict": "strong; pipeline already fetches 2015/19/23",
            "vexcel_collections": "za-gp-johannesburg-2024",
            "vexcel_capture": "2024-02-17 … 2024-04-19",
            "vexcel_gsd_m": 0.0669,
            "vexcel_h3_metro_covered": 74,
            "vexcel_h3_metro_total": 350,
            "vexcel_h3_metro_rate": 0.2114,
            "vexcel_urban_centre_area_rate": 0.2656,
            "traps": "CoJ_Aerial multi-year mosaic; SatelliteImagery/2025_2026 is blended 30 cm satellite; Vexcel is inner-city only (Sandton/Soweto 404)",
            "notes": "Coarser 2000/03/06 also live. CoJ is 68% urban-centre land — 21% Vexcel metro rate is not hinterland.",
        },
        {
            "municipality": "ethekwini",
            "municipality_name": "eThekwini",
            "public_true_date_stack": "yes_near_annual_from_2016",
            "usable_years": "2016,2018,2019,2020,2022",
            "n_usable_years": 5,
            "gsd_m_min": 0.09,
            "gsd_m_max": 0.10,
            "coverage": "metro; 2023 west-only; 2025 small 5 cm patch",
            "access_2026_09_06": "gis.durban.gov.za REST (untrusted TLS)",
            "panel_verdict": "strong; 847k photogrammetry buildings with SYear 2016/18/19 can seed chips. No ZAsolar census yet",
            "vexcel_collections": "za-kzn-durban-2026",
            "vexcel_capture": "2025-12-17 … 2026-01-06",
            "vexcel_gsd_m": 0.0647,
            "vexcel_h3_metro_covered": 166,
            "vexcel_h3_metro_total": 532,
            "vexcel_h3_metro_rate": 0.312,
            "vexcel_urban_centre_area_rate": 0.7915,
            "traps": "20212022 is a range; no public 2017 or citywide 2024; 2023 misses CBD/coast; Aerial_Imagery_2020 and 2022RSM are duplicates",
            "notes": "30 cm annual 2004–2015 is secondary (GEHI-class).",
        },
        {
            "municipality": "ekurhuleni",
            "municipality_name": "City of Ekurhuleni",
            "public_true_date_stack": "sparse",
            "usable_years": "2013,2018,2020",
            "n_usable_years": 3,
            "gsd_m_min": 0.10,
            "gsd_m_max": 0.10,
            "coverage": "metro",
            "access_2026_09_06": "gis.ekurhuleni.gov.za/Imagery",
            "panel_verdict": "usable if we run a new census",
            "vexcel_collections": "za-gp-johannesburg-2024 (spill only)",
            "vexcel_capture": "2024-02-17 … 2024-04-19",
            "vexcel_gsd_m": 0.0669,
            "vexcel_h3_metro_covered": 23,
            "vexcel_h3_metro_total": 400,
            "vexcel_h3_metro_rate": 0.0575,
            "vexcel_urban_centre_area_rate": 0.0741,
            "traps": "Orthophoto_2026 is a viewer of NGI/SANSA/2019-20, not a 2026 flight; no own Vexcel collection",
            "notes": "2015 exists as 15 cm MapServer mosaic (secondary). After 2019/20 only NGI 25 cm.",
        },
        {
            "municipality": "tshwane",
            "municipality_name": "City of Tshwane",
            "public_true_date_stack": "yes_later_rest_host",
            "usable_years": "2018,2023",
            "n_usable_years": 2,
            "gsd_m_min": 0.10,
            "gsd_m_max": 0.10,
            "coverage": "metro",
            "access_2026_09_06": "e-gis001.tshwane.gov.za ImageServer (gis.tshwane.gov.za is 503/TLS fail)",
            "panel_verdict": "two-epoch panel if 2024/25 chips differ",
            "vexcel_collections": "za-gp-pretoria-2026",
            "vexcel_capture": "2026-02-05 … 2026-02-08",
            "vexcel_gsd_m": 0.0672,
            "vexcel_h3_metro_covered": 147,
            "vexcel_h3_metro_total": 1228,
            "vexcel_h3_metro_rate": 0.1197,
            "vexcel_urban_centre_area_rate": 0.4847,
            "traps": "Aerial_2024 and Aerial_2025 share 2023 sheet IDs — unproven extra years",
            "notes": "No public building-footprint service. Six-year gap swallows the load-shedding boom.",
        },
        {
            "municipality": "nelson_mandela_bay",
            "municipality_name": "Nelson Mandela Bay",
            "public_true_date_stack": "yes_via_resource_proxy",
            "usable_years": "1997,2004,2009,2011,2013,2017,2018,2022",
            "n_usable_years": 8,
            "gsd_m_min": 0.05,
            "gsd_m_max": 0.25,
            "coverage": "urban/metro; 1997 smaller",
            "access_2026_09_06": "gis.nelsonmandelabay.gov.za/resource-proxy → internal ImageServer",
            "panel_verdict": "usable year panel; 355880 2017 LiDAR roofs",
            "vexcel_collections": "za-ec-portelizabeth-2026",
            "vexcel_capture": "2026-01-11 … 2026-01-21",
            "vexcel_gsd_m": 0.0619,
            "vexcel_h3_metro_covered": 179,
            "vexcel_h3_metro_total": 443,
            "vexcel_h3_metro_rate": 0.4041,
            "vexcel_urban_centre_area_rate": 1.0,
            "traps": "Aerial_2007_2004 is a mix; viewer only exposes 2018 and 2022; no 2019–2021 municipal vintage",
            "notes": "Direct /arcgis/rest/services is 404. 5 cm from 2017.",
        },
        {
            "municipality": "buffalo_city",
            "municipality_name": "Buffalo City",
            "public_true_date_stack": "no",
            "usable_years": "",
            "n_usable_years": 0,
            "gsd_m_min": "",
            "gsd_m_max": "",
            "coverage": "—",
            "access_2026_09_06": "gis.buffalocity.gov.za timeout; AGOL City Map vectors live",
            "panel_verdict": "no municipal year stack; Vexcel single vintage + NGI/GEHI",
            "vexcel_collections": "za-ec-eastlondon-2026",
            "vexcel_capture": "2026-01-07 … 2026-01-08",
            "vexcel_gsd_m": 0.0626,
            "vexcel_h3_metro_covered": 60,
            "vexcel_h3_metro_total": 622,
            "vexcel_h3_metro_rate": 0.0965,
            "vexcel_urban_centre_area_rate": 0.6731,
            "traps": "",
            "notes": "Tariff book sells aerial tiles; on-prem portal unreachable 2026-09-06. Cadastre ~200k on AGOL.",
        },
        {
            "municipality": "mangaung",
            "municipality_name": "Mangaung",
            "public_true_date_stack": "no",
            "usable_years": "",
            "n_usable_years": 0,
            "gsd_m_min": "",
            "gsd_m_max": "",
            "coverage": "—",
            "access_2026_09_06": "gis.mangaung.co.za NXDOMAIN; AGOL cadastre live",
            "panel_verdict": "no municipal year stack; Vexcel single vintage + NGI/GEHI",
            "vexcel_collections": "za-fs-bloemfontein-2026",
            "vexcel_capture": "2026-01-18",
            "vexcel_gsd_m": 0.0652,
            "vexcel_h3_metro_covered": 84,
            "vexcel_h3_metro_total": 1978,
            "vexcel_h3_metro_rate": 0.0425,
            "vexcel_urban_centre_area_rate": 0.7164,
            "traps": "4.2% metro Vexcel looks empty because of Free State hinterland; urban centre is 72%",
            "notes": "Cadastre 230k. Botshabelo not in Vexcel.",
        },
        {
            "municipality": "msunduzi",
            "municipality_name": "Msunduzi",
            "public_true_date_stack": "no",
            "usable_years": "",
            "n_usable_years": 0,
            "gsd_m_min": "",
            "gsd_m_max": "",
            "coverage": "—",
            "access_2026_09_06": "gis.msunduzi.gov.za timeout / NXDOMAIN",
            "panel_verdict": "no municipal year stack; Vexcel single vintage + NGI/GEHI",
            "vexcel_collections": "za-kzn-pietermaritzburg-2025",
            "vexcel_capture": "2025-12-16",
            "vexcel_gsd_m": 0.0649,
            "vexcel_h3_metro_covered": 62,
            "vexcel_h3_metro_total": 178,
            "vexcel_h3_metro_rate": 0.3483,
            "vexcel_urban_centre_area_rate": 0.9393,
            "traps": "",
            "notes": "2017/18 aerial-mapping tender exists on paper, not as a service. CoGTA land-use scheme 98,699 parcels.",
        },
        {
            "municipality": "national",
            "municipality_name": "South Africa (CD:NGI)",
            "public_true_date_stack": "yes_sheet_dated",
            "usable_years": "sheet-dated 2008–2016 at 0.50 m; 2017+ at 0.25 m",
            "n_usable_years": "",
            "gsd_m_min": 0.25,
            "gsd_m_max": 0.50,
            "coverage": "national, 3–5 yr cycle",
            "access_2026_09_06": "ngigeoportal.dlrrd.gov.za geoportal live",
            "panel_verdict": "fallback, not better than GEHI z19",
            "vexcel_collections": "",
            "vexcel_capture": "",
            "vexcel_gsd_m": "",
            "vexcel_h3_metro_covered": "",
            "vexcel_h3_metro_total": "",
            "vexcel_h3_metro_rate": "",
            "vexcel_urban_centre_area_rate": "",
            "traps": "WMS mosaic year ≠ sheet capture year; 3 m (95%) horizontal accuracy",
            "notes": "Only nationwide true-date aerial archive. Use where a metro has no municipal stack.",
        },
    ]


def satellite_layers() -> list[dict]:
    notes = {
        ("cape_town", "TM", "18"): "Fallback only. Whole-picture when z19 is incomplete.",
        ("cape_town", "TM", "19"): "Production default. Bbox-complete availability on the chip box. Dense every year 2009–2025.",
        ("cape_town", "Wayback", "18"): "Rare fallback.",
        ("cape_town", "Wayback", "19"): "Thin complement (~9 dates/anchor). Almost empty in 2018/2020/2021; fills some 2022–2025 holes.",
        ("johannesburg", "TM", "unknown"): "Run3 vintage list, no zoom column. Clipped to 2019+ census window, not 2009–2018 GEHI history. 假设 mostly 0.25 m.",
        ("johannesburg", "Wayback", "19"): "sqlite info at z19. Goes back to 2012. Grain is 15,859 chip-groups, not 41,393 targets.",
        ("johannesburg", "Wayback", "unknown"): "2019+ subset of the run3 merge. Do not divide date counts against TM as if same unit.",
    }
    usable = {
        ("cape_town", "TM", "19"): "yes_dense",
        ("cape_town", "TM", "18"): "fallback",
        ("cape_town", "Wayback", "19"): "yes_thin_complement",
        ("cape_town", "Wayback", "18"): "fallback",
        ("johannesburg", "TM", "unknown"): "yes_census_window_only",
        ("johannesburg", "Wayback", "19"): "yes_thin_complement",
        ("johannesburg", "Wayback", "unknown"): "yes_2019plus_subset",
    }
    names = {"cape_town": "City of Cape Town", "johannesburg": "City of Johannesburg"}
    rows = []
    with (HERE / "satellite_city_provider_zoom_summary.csv").open(encoding="utf-8") as f:
        for rec in csv.DictReader(f):
            key = (rec["city"], rec["provider"], rec["zoom"])
            rows.append(
                {
                    "family": "satellite",
                    "municipality": rec["city"],
                    "municipality_name": names[rec["city"]],
                    "layer_id": f"{rec['city']}_{rec['provider']}_z{rec['zoom']}",
                    "provider": rec["provider"],
                    "provider_label": rec["provider_label"],
                    "zoom": rec["zoom"],
                    "gsd_m_nominal": rec["gsd_m_nominal"],
                    "n_h3_r7": rec["n_h3_r7"],
                    "n_anchors_sum": rec["n_anchors_sum"],
                    "n_date_rows": rec["n_date_rows"],
                    "median_dates_per_anchor": rec["median_of_cell_median_dates"],
                    "min_date": rec["min_date"],
                    "max_date": rec["max_date"],
                    "years_union": rec["years_union"],
                    "usable_as_time_axis": usable[key],
                    "note": notes[key],
                }
            )
    return rows


def main() -> None:
    layers = aerial_layers()
    write_csv(HERE / "aerial_layers.csv", AERIAL_FIELDS, layers)
    write_csv(HERE / "aerial_city_summary.csv", CITY_FIELDS, aerial_cities())
    write_csv(HERE / "satellite_layers.csv", SAT_FIELDS, satellite_layers())
    n_yes = sum(1 for r in layers if r["usable_as_panel_year"] == "yes")
    n_trap = sum(1 for r in layers if r["usable_as_panel_year"] == "trap")
    print(
        f"aerial_layers {len(layers)} rows ({n_yes} yes, {n_trap} trap); "
        f"aerial_city_summary {len(aerial_cities())}; "
        f"satellite_layers {len(satellite_layers())}"
    )


if __name__ == "__main__":
    main()
