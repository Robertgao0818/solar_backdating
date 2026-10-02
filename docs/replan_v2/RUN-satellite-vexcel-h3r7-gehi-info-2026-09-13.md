# RUN — Vexcel H3-7 GEHI info reconnaissance (TM + Wayback z19)

| 字段 | 值 |
|---|---|
| 文档 ID | `RUN-satellite-vexcel-h3r7-gehi-info-2026-09-13` |
| 日期 | 2026-09-13 |
| Status | **done** (centroid `info` only; no chips) |
| 样本 | all unique Vexcel-covered H3-7 cells |
| 格子 | 1148 unique H3-7 |
| 查询 | 2296 = cells × {TM, Wayback} × z19 |
| 成功 | 2296 (empty catalogs included) |
| 真源 | `~/zasolar_data/geid_temporal/satellite_vexcel_h3r7_gehi_info_2026-09-13/` |
| SRC | [`../briefs/SRC-satellite-vexcel-h3r7-gehi-info-2026-09-13.md`](../briefs/SRC-satellite-vexcel-h3r7-gehi-info-2026-09-13.md) |

Centroid `gehi_info`, not bbox-complete availability. Independent of the
2026-09-06 CT/JHB reuse catalog. Preflight Cape Town cell
`87ad36000ffffff`: TM 125 dates, Wayback 9. Probe: 2,294 live calls +
2 cache-skipped preflight rows; 0 timeout/403; 1 empty Wayback
(Msunduzi). Headline: TM is a 16–19 year axis on every Vexcel hex;
Johannesburg inner-city TM goes back to **2009** (median 90), so the
run3 2019+ window was a catalog clip, not a GEHI hole. Wayback is
usable (median 9–13) only in the large metros; Buffalo / Mangaung /
Msunduzi have 1–2 snapshots.

## City medians

| city | TM median dates | Wayback median dates | TM years | Wayback years |
|---|---:|---:|---|---|
| City of Cape Town | 113.0 | 9.0 | 18 | 13 |
| City of Johannesburg | 90.0 | 13.0 | 18 | 13 |
| eThekwini | 89.5 | 12.0 | 17 | 12 |
| City of Ekurhuleni | 129.0 | 13.0 | 18 | 11 |
| City of Tshwane | 49.0 | 10.0 | 19 | 10 |
| Nelson Mandela Bay | 91.0 | 10.0 | 19 | 10 |
| Buffalo City | 107.0 | 2.0 | 17 | 3 |
| Mangaung | 51.0 | 1.0 | 17 | 3 |
| Msunduzi | 45.0 | 2.0 | 16 | 3 |

