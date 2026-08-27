# DATA — A3 Wayback dump CRS (EPSG:3857) crop fix · 2026-08-28

| 字段 | 值 |
|---|---|
| 文档 ID | `DATA-a3-wayback-dump-crs-2026-08-28` |
| Status | **代码已修，离线回归过**。wave_04 不热切换；下一波进程启动后生效 |
| 上位 | [`RUN-a3-batch-lane-canary-2026-08-19.md`](RUN-a3-batch-lane-canary-2026-08-19.md) §6 Wayback 接入；全城流水线 [`RUN-cape-town-citywide-batch-disk-pipeline-2026-08-18.md`](RUN-cape-town-citywide-batch-disk-pipeline-2026-08-18.md) §9 A3 |

**一句话**：Wayback `dump` 的 `.jgw` 是 Web Mercator 米（EPSG:3857），拼接却盖上 EPSG:4326，切图把经纬度当米用，窗落到像素 `-6.8e6`。wave_03 `a3_rog_v4` 上 TM 切图 0 失败、Wayback **6667/6672 = 99.9%** 失败后逐张回退。修法：按 world-file 单位推断 CRS，切图前把锚点 bbox 投到 mosaic CRS。像素不重采样。

## 1 生产证据（wave_03 `a3_rog_v4`，不重跑 GEHI）

| provider | 成员 | 切图失败 | 失败率 |
|---|---:|---:|---:|
| TM | 51,310 | 0 | 0% |
| Wayback | 6,672 | 6,667 | 99.9% |

失败类全部是 `requested chip bbox falls outside mosaic`；0 张碰洞。同格 mosaic 元数据：

- TM：`lon 18.37–18.39`，像素 `2.68e-6` 度
- Wayback `CPT1854_20190115_wayback_z19`：`x 2,045,302–2,046,678`，像素 **0.29858 m**，4608×4608，324 瓦

锚点 `ct_full_inventory_2026_06_21_merged_t00005751`（经纬度 18.37736, −34.10455）：

| 切图假设 | window `[col_off:col_end, row_off:row_end]` |
|---|---|
| 把 lon/lat 当米（生产 bug） | `[-6849987:-6849986, -13535630:-13535629]` |
| 先投到 EPSG:3857 | `[1526:1925, 4034:4415]`（落在 4608 画布内） |

这就是「它说明什么 / 为什么重要 / 忽略会怎样」：

- **说明什么**：A3 dump 对 TM 已经按设计工作；Wayback 的 11% 候选在付逐张税，不是 dump 失败。
- **为什么重要**：wave_02/03 的 `call_reduction` 只有 6–8×（canary TM-only 是 ~50×），每波多 1.5–2 小时墙钟。
- **忽略会怎样**：后面二十多波继续为已经下过的 Wayback 瓦片再付一遍 interval。

## 2 代码

`scripts/temporal/gehi_region_batch.py`：

- `infer_world_crs`：`|corner|≤180` 且像素 `<0.01` → EPSG:4326，否则 EPSG:3857
- `stitch_dump_tiles(crs=None)`：从 world-file 推断；混 CRS 或强行盖错 CRS 会 raise
- `geographic_bbox_in_crs` + `crop_chip_from_mosaic`：锚点 bbox 永远是 WGS84 经纬度，切图前投到 mosaic CRS

不做：不把 mosaic 重投影成 4326（那会重采样，破坏 A3 无损拼）。Wayback dump chip 的 GeoTIFF CRS 是 EPSG:3857；Gemini 打分看像素，provenance 已能处理非地理 CRS。

## 3 测试

`tests/temporal/test_gehi_region_batch.py`：41 passed，全离线。新增：单位推断、混 CRS 拒绝、强行 4326 拒绝、**生产窗数字逐字节复现 bug 窗并断言 3857 窗在画布内**、2×2 米瓦片 stitch+crop。

未做：live GEHI 重放（wave_04 正在跑，不在同机加调用）。

## 4 运行时

正在跑的 `run_a3_batch_lane.py` 进程仍是旧代码。已写完 sentinel 的 Wayback 簇不会重切（chip 已由逐张回退拿到，QA 过）。**不要**为了这个修重启 wave_04。wave_05 起的新进程会自动推断 CRS。
