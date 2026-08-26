# HANDOFF — A3 GEHI 区域批量下载（给 2026-08-19 接手）

| 字段 | 值 |
|---|---|
| 写给 | 下一任 agent（owner 说交 Kimi 看） |
| 日期 | 2026-08-19 |
| 作者会话 | Grok，2026-08-18 当晚做完 pilot + 同日更正 |
| 正式判定 | **MIXED**（预注册的 `download` 臂）。`ENABLE_REGION_BATCH` **仍是 false** |
| 真源 | [`RUN-a3-region-batch-pilot-2026-08-18.md`](RUN-a3-region-batch-pilot-2026-08-18.md) |
| 上位加速菜单 | [`RUN-cape-town-citywide-batch-disk-pipeline-2026-08-18.md`](RUN-cape-town-citywide-batch-disk-pipeline-2026-08-18.md) §9 |

本文是交接摘要，不替代 RUN。数字以 RUN §6 和
`~/zasolar_data/geid_temporal/a3_region_batch_pilot_20260818/report/summary.json`
为准。

---

## 0 先读什么

1. 本文件（问题清单 + 发现 + 不要做什么）
2. RUN-a3 全文（预注册 bar、数字、`dump` 更正表）
3. 全城方案 §9 A1–A6（A3 只是加速菜单里的一项，A1/A2 日历上更便宜）

不要从这次对话的早期回复里抄结论。第一版把「整图 JPEG」说成拼接的必要步骤，**已被 owner 当场纠正**，RUN §6「像素差从哪来」已改写。

---

## 1 一句话现状

GEHI（GEHistoricalImagery，本仓唯一历史影像下载器，二进制 0.5.1）**不能**一次下多个离散目标。它可以一次下一块区域。生产路径却是「一个锚点 × 一张 96 m 小图 × 一次进程 × 再空等 2 秒」。A3 的想法是：同一 vintage、空间相邻的锚点并成一个框，一次调用，本地切开。

两格 × 12 锚的 bounded pilot 已经跑完。吞吐和 QA 过了；预注册的像素 bar 在 `download` 臂上没过。随后用 `dump` 在其中一格证明：没过的很大一块是命令选错，不是「并窗切开就不齐」。生产开关没翻，主流水线没动。

---

## 2 背景：为什么会做这件事

全城 Cape Town 下载大约 90k 非 Top-52 锚点 × 约 55 张 chip。限速单位是**每个地址族、每秒几次 GEHI 调用**，不是瓦片数。2026-07-12 实测：TM 大约 1 次/秒以上会 403，软封 60 秒，硬封 30 分钟。全城方案把 `REQUEST_INTERVAL` 显式抬到 2.0 秒。

koko（8G RAM）合同是同时最多 1 个 GEHI 进程。S0 日历约 20–23 天。owner 问「能不能一次下多个目标」，点名先试 A3。

GEHI 0.5.1 的事实（本机 `download --help`）：

- `download`：一块区域 → **一张** GeoTIFF。`--parallel` 只是这一块里的瓦片并发。
- `--date a,b,c` 是同一张图里用多个日期凑瓦片（混日填洞），**不是**下 N 个 vintage、出 N 个文件。生产必须 `--exact-date`。
- 0.5.1 **没有** `--co COMPRESS=NONE` / `--of`。试过，命令直接打 help、不写文件。
- `dump`：同一块区域 → 文件夹里的原生 JPEG 瓦片 + `.jgw` world file。

---

## 3 碰到的问题（按出现顺序）

### P1 — 一次一个目标 + 小图 + 长间隔

**现象**：`gehi_download.py` 对每个 `(anchor, date)` 新起一个 .NET 进程，拉 1–4 块 z19 瓦片（96 m chip ≈ 64 m/瓦），再被 limiter 卡住 2 秒。`--parallel 4` 几乎白给。

**这是什么**：瓶颈是调用次数，不是网速。  
**为什么重要**：全城日历被 2 秒 × 几百万次调用绑住。  
**忽略会怎样**：只调 `--parallel` 或再开同 NAT 进程，只会一起撞 403。

### P2 — GEHI 把请求框往里咬不到 1 个像素

**现象**：第一轮按 chip 窗并集原样去下，3/24 张边缘 chip 切不出来（`window col_off=-1` 或超出 mosaic 高）。`availability --complete` 当时已经报目标日期完整。

**处理**：联合框每边 pad `2e-5` 度（开普敦约 2 m，约 7 个 z19 像素）。重下后 24/24 切开。常量在 `gehi_region_batch.py`：`DEFAULT_MOSAIC_PAD_DEG`。

**这是什么**：输出按 GEHI 自己的像素网格咬合，最外一圈可以缺不到 1 px。不是大框缺景。  
**为什么重要**：生产如果按并集原样下，每簇都会丢大约十分之一的边缘锚点。  
**忽略会怎样**：批量看起来成功，manifest 缺边，QA 打 missing，加速被缺口吃掉。

### P3 — 用 `download` 做 mosaic，整图被默认压成 JPEG

**现象**：预注册以为「mosaic 不整图 JPEG、只在切开后压」。实测两张 mosaic 都是 `compress=jpeg, photometric=ycbcr`。再切开、再 q=95，等于整图有损一次。vs 逐张：24 张中位 MAE 4.72，MAE≤5 只有 15/24，预注册 90% 条没过。

**owner 当场纠正**：瓦片已经是 JPEG，拼图画布不该再整图压。

**同日更正（只做了 CPT2713）**：`dump` 出 25 块瓦片（5×5）→ 无损拼 → 切。中位 MAE 从 5.84 降到 **2.49**，12/12 MAE≤5。切完再 q=95 仍是 2.42 / 12/12。

**这是什么**：MIXED 的保真缺口，很大一块是命令选错。  
**为什么重要**：生产路径若继续 `download` 出大图，会把本来过得了的 bar 自己做掉。  
**忽略会怎样**：误判「切开就不齐」，或把有损 mosaic 写进生产。

### P4 — 切开窗和逐张窗差 1 个像素

即使无损拼，对照仍全部 `reprojected=True`。剩余 ~2.5 DN 来自网格咬合 + 逐张臂自己也走过 `download` 小图 JPEG。不是混日。`--exact-date` 全程开着。没有 403。

### P5 — cache-hot 墙钟不能外推全城

样本是 Top-52、日期 2019-05-30，瓦片几乎一定在 24G 共享缓存里。2.9 s vs 51.4 s（17.6×）测的是**进程 + 2 秒间隔**，不是冷启动省了多少网。逐张臂 51 s / 24 张 ≈ 2.14 s/张，几乎就是 interval。

没测：非 Top-52 冷格子；一格全量 ~400 锚 / ~1.1 km 并窗是否仍 complete。

---

## 4 现有发现（可以交给下一任当前提）

1. **GEHI 没有多目标 API。** 批量只能「一块更大的区域 + 我们切」。
2. **限速单位是调用次数。** 并窗的收益首先是少付 interval，其次才是少下重复瓦片。
3. **小簇并窗在这两格上是 complete 的。** 2019-05-30、约 260–310 m 联合框，`availability --complete` 两格都含该日。
4. **并窗必须 pad。** 约 2 m。已进库。
5. **A3 生产应走 `dump`，不走 `download` mosaic。** 0.5.1 关不掉 `download` 的整图 JPEG。
6. **`dump` 在一格上能过预注册保真 bar。** 另一格未重测，所以正式判定不改 GO。
7. **对 Gemini 打分，2–5 DN 几乎一定看不见。** 若有人要把 chip 当档案原件做像素级复现，逐张 `download` 仍是当前真源。
8. **不同 vintage 不能并框。** `--date a,b,c` 是混日填洞，会破坏安装日推断。分组键必须是 `(grid_id, capture_date, provider, zoom)`。
9. **A3 不阻塞主流水线。** E0/E6 token 仍未签，全城下载 fail-closed。A1（box 独立 egress）和 A2（1.5 s interval canary）仍是不改下载语义、更快能补工期的两刀。

---

## 5 代码、测试、产物

| 路径 | 内容 |
|---|---|
| `scripts/temporal/gehi_region_batch.py` | 并窗、centroid cluster、pad、crop、像素对照。无 GEHI 进程 |
| `scripts/temporal/run_a3_region_batch_pilot.py` | 六阶段可恢复 driver（plan / availability / batch=`download` / sequential / compare / report） |
| `tests/temporal/test_gehi_region_batch.py` | 10 passed，不联网 |
| `~/zasolar_data/geid_temporal/a3_region_batch_pilot_20260818/` | plan、两张 `download` mosaic、24 张 batch crop、24 张 sequential、compare CSV、report |
| 同上 `dump_cpt2713/` | CPT2713 的 25 块原生瓦片 + `.jgw` |
| 同上 `dump_cpt2713_mosaic_uncompressed.tif` | 无损拼画布 |
| 同上 `dump_crops_uncompressed/` | dump 臂切开（未进正式 report） |

driver **还不会** `dump`。更正是会话里手跑的一次性对照，没有写成第二臂。

---

## 6 不要做什么

- 不要翻 `ENABLE_REGION_BATCH` / `ALLOW_CITYWIDE_DOWNLOAD`（E0+E6 未签，且 A3 非正式 GO）
- 不要改 `run_ct05_download_lane.sh` 或 citywide wave plan
- 不要关 `--exact-date`，不要用 `--date a,b,c` 当多 vintage
- 不要在同一地址族上再开 GEHI 进程，不要做源 IP 轮换
- 不要把 17.6× cache-hot 墙钟写进 S0/S3 全城日历
- 不要删 `~/zasolar_data/geid_raw/gehi_tile_cache`（24G，生产热缓存）
- 不要覆盖 Top-52 生产 chips（`cape_town_top52_backdating_v1_20260724/ct05_download_v1/chips`）
- 不要用 Haiku 做数据结论；保真数字以 RUN 和 `report/summary.json` 为准，不要从聊天里摘

---

## 7 若 owner 让你继续：建议顺序

未授权前只读。若授权继续 A3，建议按这个顺序，不要一上来写进 lane：

1. 把 `dump` + 无损拼 + pad + 切 + 每张 chip JPEG q=95 收进库，加单测（不联网拼小瓦片夹具即可）。
2. 对 **CPT3584** 重复 dump 对照（现在只有 CPT2713）。两格都过 MAE≤5 的 90% 条，才能把「`download` 臂 MIXED」改写成「`dump` 臂待 owner 认 GO」。
3. 仍不要外推日历，除非另做：一个非 Top-52 冷格子的墙钟；以及「一格全量并窗是否 complete」的 availability probe（可以先不下图）。
4. A1 / A2 仍可并行，不依赖 A3。

---

## 8 未测 / 未决（写进下一份 RUN 的 Open Questions）

- CPT3584 的 `dump` 保真是否同样过 bar
- 1.1 km 整格并窗在常见 TM 日期上是否仍 `--complete`
- 冷缓存下 `dump` 25 瓦 vs 12 次小 `download` 的真实网时（cache-hot 已证明 interval 税，没证明网络税）
- `dump` 的 limiter / 403 行为是否和 `download` 同一套（应走现有 `GehiRateLimiter`）
- 生产 manifest / QA 如何把「一簇 dump」合成现有 per-anchor 行（`run_ct05_chip_pipeline.quality_gate` 合同）
- owner 是否接受「和逐张差 ~2.5 DN、QA 全过」作为打分用 chip 的真源

---

*2026-08-19 交接。数字来自 2026-08-18 live pilot + 同日 dump 更正，未在 19 日重跑 GEHI。*
