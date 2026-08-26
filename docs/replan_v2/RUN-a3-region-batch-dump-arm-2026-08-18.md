# RUN — A3 区域批量下载 dump 臂（无损拼接）补完

| 字段 | 值 |
|---|---|
| 文档 ID | `RUN-a3-region-batch-dump-arm-2026-08-18` |
| 日期 | 2026-08-18（同日续跑，12:44Z 出数） |
| Status | **GO（dump 臂，对预注册 7 条 bar）；owner 已于 2026-08-19 接受 ~2 DN dump chip 为打分真源（[OWNER_DECISIONS D4](OWNER_DECISIONS.md)）**。`ENABLE_REGION_BATCH` **仍 false**，等 owner 签生产路径 |
| 执行依据 | [`HANDOFF-a3-region-batch-2026-08-19.md`](HANDOFF-a3-region-batch-2026-08-19.md) §7 步骤 1–2 |
| 前序 | [`RUN-a3-region-batch-pilot-2026-08-18.md`](RUN-a3-region-batch-pilot-2026-08-18.md)（`download` 臂 MIXED） |
| 真源 | `~/zasolar_data/geid_temporal/a3_region_batch_pilot_20260818/report/dump_summary.json` |

**一句话**：把 GEHI `dump`（原生 JPEG 瓦片 + world file）→ 无损拼画布 → pad 并窗 → 切开 → 每张 chip JPEG q=95 收进了库并加了离线单测；对 CPT2713 **和 CPT3584** 两格各 1 次 live `dump`（cache-hot）跑通全流程，预注册 7 条 bar 全过，包括 `download` 臂没过的「90% chip MAE≤5」。

---

## §1 本次做了什么（对应 handoff §7）

| 步骤 | 状态 | 落点 |
|---|---|---|
| 1. dump + 无损拼 + pad + 切 + q95 收进库，加不联网单测 | 完成 | `gehi_region_batch.py`（`dump_region_tiles` / `scan_dump_tiles` / `parse_world_file` / `stitch_dump_tiles`）；`run_a3_region_batch_pilot.py` 新增 `batch_dump` / `compare_dump` / `report_dump` 三个可恢复 stage；`test_gehi_region_batch.py` 从 10 → 20 条，全离线 |
| 2. CPT3584 重复 dump 对照 | 完成 | 两格各 1 次 live dump，两格 12/12 MAE≤5 |
| 3. 冷格子墙钟 / 整格 1.1 km availability probe | **未做** | 仍写进 Open Questions，本轮不外推日历 |
| 4. A1/A2 | 不在本 RUN | 与 A3 解耦，照全城方案走 |

live  footprint：2 次 GEHI `dump` 调用（每格 1 次），`REQUEST_INTERVAL=2.0`，`--exact-date` 全程开着，无 403。Top-52 生产 chips 未动；所有产物在 pilot 目录下。

## §2 新发现：GEHI 的 .jgw C/F 是左上角**角点**，不是像素中心

这是本轮代码化过程中抓到、handoff 里没有的事实，也是为什么必须先离线对拍再上 live：

- ESRI world file 规范：第 5/6 行是左上像素**中心**。
- 实测 GEHI 0.5.1 的 dump `.jgw`：C=18.480377197265625 恰好落在 Google z19 全球瓦片网格的**整数瓦片索引** 289058（360°/2¹⁹ 每瓦片，自 −180° 起算；纬度方向同样整数）。若按规范当中心读，落在 289057.998，不可能——瓦片图一定切在全球网格整数边界上。
- 决定性证据在保真：中心解读的拼图切 24 张 vs 逐张中位 MAE **5.93**（7/24 ≤5，还不如 `download` 臂），角点解读同一批瓦片中位 **1.93**（24/24 ≤5）。

**这是什么**：GEHI 的 world file 写了角点坐标（不符合 ESRI 字面规范，但与其瓦片网格严格一致）。
**为什么重要**：任何按规范读 .jgw 的工具（GDAL 直接挂 .jgw 就会按中心读）都会把 dump 瓦片整体挪半像素；A3 的保真 bar 恰好卡在这个量级。
**忽略会怎样**：「无损拼」实际带半像素系统偏移，逐张对照 MAE 翻倍，误判「dump 臂也不过关」。

配套修正：拼接按 world file **坐标**落瓦片，不信文件名的 Row 方向（GEHI 0.5.1 的 `{r}` 自南向北编号）；文件名与坐标不一致时记录 `filename_grid_mismatches`，不静默翻转。拼图缺洞（网格不满）直接 raise，不零填充——洞就是完整性信号。

## §3 数字（dump 臂，24 张 chip）

| 指标 | 值 | 预注册 bar | 过？ |
|---|---:|---|---|
| 联合框 `availability --complete` 含 2019-05-30 | 2 / 2（沿用 08-18 availability stage） | 两格都要 | 是 |
| dump crop bbox QA pass | 24 / 24 | ≥ 95% | 是 |
| dump crop pixel QA pass | 24 / 24 | ≥ 95% | 是 |
| vs 同次 sequential median MAE | **1.93 DN** | ≤ 5 | 是 |
| vs sequential MAE≤5 比例 | **24 / 24 = 100%** | ≥ 90% | 是 |
| MAE≤15 | 24 / 24 | KILL 若 < 80% | 未触 KILL |
| GEHI 调用 | dump **2** vs sequential **24** | batch < sequential 且 > 0 | 是（12×） |
| 墙钟（cache-hot） | dump 臂 **2.89 s** vs sequential **51.40 s** | ≥ 2× | 是（**17.8×**，含义同前：进程+间隔税，非冷网） |

分格：CPT2713 中位 2.42 / 12·of·12 ≤5 / 最大 2.89；CPT3584 中位 **1.75** / 12·of·12 ≤5 / 最大 1.85。p99 最大 9 DN。每格 25 块瓦片（5×5，含 2 m pad），拼 1280×1280 无损 GeoTIFF，切 12 张 397×315 q=95 JPEG GeoTIFF（生产同款落盘形态）。

判定逻辑与 08-18 预注册逐字一致（`_evaluate_verdict` 两臂共用）：**dump 臂 GO**。

## §4 移植验证（离线，未花 live 调用）

收进库的 stitcher 先用 08-18 手跑的 `dump_cpt2713/` 25 块瓦片离线对拍：

- 拼出的画布与手跑 `dump_cpt2713_mosaic_uncompressed.tif` **逐字节一致**（MAE 0，exact_match 1.0，免重投影）——手跑当时就是角点解读，移植无回归。
- 从该画布切 12 张 q95 crop vs sequential：中位 2.42、12/12 ≤5，复现手跑对照（2.42–2.49）。

## §5 结论的变化

- 08-18 正式判定「MIXED（`download` 臂）」不变——那是 `download` 臂的如实记录。
- 本轮把 handoff §7 的步骤 2 坐实：**`dump` 无损拼接臂在两格上都过预注册保真 bar**，A3 的机制问题从「待证」变为「dump 臂 GO，待 owner 认」。
- 生产路径若上 A3，必须用 `dump` 臂，且必须走「坐标落瓦片 + 角点解读 + 缺洞即败」的拼接实现；`download` 出大图只留作对照。
- `ENABLE_REGION_BATCH` **保持 false**。GO 是对预注册 bar 的判定，不是生产授权。

## §6 Open Questions（原样保留 + 更新）

- [x] ~~非 Top-52 冷格子墙钟（真网络税）~~——2026-08-19 已测（[RUN-a3-fullcell-coldcell-probe-2026-08-19](RUN-a3-fullcell-coldcell-probe-2026-08-19.md) §3）：冷格 17.2× ≈ 热格 17.8×，税在进程+间隔不在字节；仍为 per-cell 刻画，不进全城日历
- [x] ~~一格全量（~400 锚 / ~1.1 km）并窗是否仍 `availability --complete`~~——2026-08-19 probe-only 实测：CPT2598（341 锚 / 1.1 km）57 个完整日期，同上 RUN §2
- [x] ~~`dump` 的 limiter / 403 行为是否同 `download`~~——`dump_region_tiles` 走同一个 `GehiRateLimiter` + `make_throttled_runner`，行为一致（本轮 2 次调用无 403）
- [x] ~~生产 manifest / QA 如何把「一簇 dump」合成现有 per-anchor 行~~——2026-08-19 完成：`synthesize_dump_manifest`（FIELDS 逐字对齐 + sidecar），pilot 24/24、冷格 12/12 过生产 `quality_gate`，同上 RUN §4
- [x] ~~owner 是否接受「与逐张差 ~2 DN、QA 全过」的 dump chip 作为打分真源~~——**已接受（2026-08-19，[OWNER_DECISIONS D4](OWNER_DECISIONS.md)）**：~2 DN dump chip 作打分真源与逐张等效；范围仅限打分真源，生产开关另签
- [ ] GDAL 直接挂 .jgw 会按 ESRI 中心解读——若未来有别的工具消费 dump 瓦片，要在其入口显式纠正半像素（本仓 stitcher 已按角点处理并留注释）

## §7 复现

```bash
source scripts/activate_env.sh
pytest tests/temporal/test_gehi_region_batch.py -q        # 20 passed，全离线
python scripts/temporal/run_a3_region_batch_pilot.py --only batch_dump compare_dump report_dump
```

产物：`~/zasolar_data/geid_temporal/a3_region_batch_pilot_20260818/{batch_dump,compare_dump,report/dump_summary.json}`。
