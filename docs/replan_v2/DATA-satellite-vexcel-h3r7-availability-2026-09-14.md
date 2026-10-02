# DATA — satellite year availability on Vexcel H3-7 (1–2 frame flags)

| 字段 | 值 |
|---|---|
| 日期 | 2026-09-14 |
| Status | **done** |
| 上游 | 2026-09-13 质心 `gehi_info`（1,148 格 × TM+Wayback z19） |
| 图 1 | [`../briefs/FIG-satellite-year-heatmap-2026-09-14.html`](../briefs/FIG-satellite-year-heatmap-2026-09-14.html) |
| 图 2 | [`../briefs/FIG-layer-availability-2026-09-14.html`](../briefs/FIG-layer-availability-2026-09-14.html) |
| 交付包 | [`../briefs/BRIEF-satellite-availability-2026-09-14.html`](../briefs/BRIEF-satellite-availability-2026-09-14.html) |
| CSV | [`../briefs/_muni_catalog/satellite_vexcel_h3r7_year_availability.csv`](../briefs/_muni_catalog/satellite_vexcel_h3r7_year_availability.csv) · [`../briefs/_muni_catalog/satellite_vexcel_h3r7_city_usability.csv`](../briefs/_muni_catalog/satellite_vexcel_h3r7_city_usability.csv) |
| 再生 | `python scripts/temporal/satellite_year_availability.py --publish` |

**一句话：** `years_union` 里有某一年 ≠ 这一年能用。约翰内斯堡 2023 TM 是 32/74 格、每格 1 帧；Wayback 和市政 15 cm 能补全市年 panel，但不能用 TM 做日区间。九城 Vexcel 足迹上卫星年 panel 都在，日精度只在 TM「可用/密」的年。

分类（分母 = 该城 Vexcel H3-7，不是全市腹地）：

| class | 规则 | 年 panel | 日区间 |
|---|---|---|---|
| `dense` | ≥5 帧/格 且 ≥90% 格子 | 是 | 是 |
| `usable` | >2 帧/格 且 ≥50% 格子 | 是 | 是 |
| `one_look` | ≤2 帧/格 但 ≥50% 格子 | 是 | 否 |
| `sparse_1_2` | ≤2 帧/格 **且** <50% 格子 | 否（除非 Wayback/航测补） | 否 |
| `noise` | 1–2 个格子 | 否 | 否 |
| `absent` | 0 | 否 | 否 |

TM `sparse_1_2`（优先标注，9 个城×年）：约翰内斯堡 2023、Ekurhuleni 2023、eThekwini 2009、NMB 2012 与 2026、Buffalo 2009/2011/2026、Mangaung 2011。

再生脚本不打 GEHI。

## H3-7 空间对齐（同日）

同一套 `h3_index`：城区格子 ∪ Vexcel 格子。不画全市腹地。

- 图：[`../briefs/FIG-h3-spatial-coverage-2026-09-14.html`](../briefs/FIG-h3-spatial-coverage-2026-09-14.html)（覆盖 / TM 密度 / 2023 三模式）
- 格子 CSV：[`../briefs/_muni_catalog/satellite_vexcel_h3r7_spatial.csv`](../briefs/_muni_catalog/satellite_vexcel_h3r7_spatial.csv)
- 城汇总：[`../briefs/_muni_catalog/satellite_vexcel_h3r7_city_spatial.csv`](../briefs/_muni_catalog/satellite_vexcel_h3r7_city_spatial.csv)
- GeoJSON：`~/zasolar_data/geid_temporal/satellite_vexcel_h3r7_gehi_info_2026-09-13/derived/h3_spatial.geojson`
- 再生：`python scripts/temporal/build_h3_spatial_availability.py --publish`

约翰内斯堡城区 264 格中 **191** 格无 Vexcel（卫星样本覆盖不了 Sandton/Soweto 一侧）。Vexcel 74 格里 TM 2023 只有 **32** 格。两层洞叠在同一套 H3-7 上。
