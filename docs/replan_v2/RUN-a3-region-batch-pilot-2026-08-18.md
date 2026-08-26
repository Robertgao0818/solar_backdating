# RUN — A3 区域批量下载 bounded pilot

| 字段 | 值 |
|---|---|
| 文档 ID | `RUN-a3-region-batch-pilot-2026-08-18` |
| 日期 | 2026-08-18 |
| Status | **MIXED**（机制成立，像素非逐点相同；`ENABLE_REGION_BATCH` 仍 false） |
| 上位 | [`RUN-cape-town-citywide-batch-disk-pipeline-2026-08-18.md`](RUN-cape-town-citywide-batch-disk-pipeline-2026-08-18.md) §9 A3 / PR9 |
| 开关 | `ENABLE_REGION_BATCH` **保持 false**。本 pilot 不接主流水线。 |

**一句话**：在两个已有逐目标 chip 的 Top-52 grid 上，把「一次 GEHI 调用拉同 vintage 联合框 + 本地切开」和「一次一个 96 m chip」对着比。先回答机制是否成立，再谈要不要换生产路径。

---

## §1 问题

生产下载把每个 `(anchor, date)` 做成一次 GEHI 进程。一张 96 m chip 在 z19 通常只有 1–4 块瓦片，但每次调用都要付进程启动 + dbRoot + `REQUEST_INTERVAL=2.0` 秒。限速单位是**调用次数**，不是瓦片数。

A3 的假设：同一 vintage、空间相邻的一批锚点，可以合成一个联合 bbox，一次 `download --exact-date`，再用 rasterio 按原 chip 窗切开。调用次数从 N 降到 1，间隔税只付一次。

这不是「GEHI 原生多目标」。GEHI 一次调用仍然只写一张 GeoTIFF。批量发生在我们这一侧的组框和切分。

## §2 预注册（live 下载前锁定）

### 样本

| 项 | 值 | 为什么这样选 |
|---|---|---|
| grids | `CPT2713`, `CPT3584` | Top-52 中等密度（396 / 388 锚），格子约 1.1 km，不是最密也不是最稀 |
| 每格 cluster | 距该格 centroid 最近的 **12** 个锚点 | 联合框约 260 m，不是整格 1 km；先测机制，不测「一格一张巨图」 |
| vintage | TM z19 `2019-05-30` / `version=noversion` | 两格全部 cluster 成员磁盘上已有逐目标 chip，可做像素对照 |
| 对照臂 | 同一 24 张 chip 再走一遍生产 `download_chip_with_zoom_ladder` | 同一次运行、同一 JPEG q=95、同一 limiter |
| 次对照 | 已有 Top-52 生产 chip | 只报告，不当 gate（独立 JPEG 块会和本次切开不一致） |

刻意**不**选整格 400 锚、**不**选非 Top-52 冷缓存格子。本轮要隔离的是「进程 + 间隔」税。Top-52 的 2019-05-30 瓦片几乎肯定已经在 24G tile cache 里：两边都会 cache-hot。

**这是什么**：cache-hot 下的墙钟差，测的是调用开销，不是冷启动省了多少网。  
**为什么重要**：用户抱怨的正是「小图 + 长间隔」，不是瓦片带宽。  
**忽略会怎样**：拿 cache-hot 的 4× 去外推全城冷下载日历，会把 A3 说成已经解决工期。

### 两臂

- **batch**：每格 1 次 GEHI `download --exact-date --zoom 19`，目标是 12 锚点 chip 窗的联合 bbox。当时以为 GEHI 写出未压缩 GeoTIFF、我们只在切开后 JPEG q=95；**事后证实 `download` 会把整张 mosaic 默认写成 JPEG**（见 §6 更正）。
- **sequential**：24 张 chip 各 1 次同样的 GEHI 调用，`REQUEST_INTERVAL=2.0`，写出到 pilot 目录，不覆盖 Top-52 生产芯片。

先跑 `availability --complete` 探联合框。目标日期不在 complete 列表里也继续下载，把「整框不完整」记成发现，不当脚本崩溃。

### 主终点 / 次终点

| 角色 | 指标 | 口径 |
|---|---|---|
| 主 · 保真 | 生产 QA（decode / bbox contain / pixel informative） | 与 `run_ct05_chip_pipeline.inspect_raster` 同一套 |
| 主 · 保真 | 同次 sequential 对照的 median MAE | 切开 crop 配准到 sequential 网格 |
| 主 · 吞吐 | GEHI 调用次数 | mosaic 1 次/格 vs chip 1 次/锚 |
| 次 · 吞吐 | 墙钟比 sequential/batch | 含 2.0 s interval；注明 cache-hot |
| 次 · 覆盖 | 联合框 `availability --complete` 是否含 2019-05-30 | 回答「小窗完整 ≠ 并窗完整」 |

### GO / MIXED / NO-GO

**GO**（机制成立，值得写生产路径草案）须同时满足：

1. 两个联合框的 `availability --complete` 都含目标日期
2. batch crop 的 bbox QA pass ≥ 95%
3. batch crop 的 pixel QA pass ≥ 95%
4. vs sequential 的 median MAE ≤ 5 DN
5. vs sequential 的 MAE≤5 比例 ≥ 90%
6. batch 调用次数 < sequential，且 batch > 0
7. 墙钟比 ≥ 2×

**NO-GO**（机制在这个尺度上不可用）任一成立：

- bbox QA pass < 80%
- median MAE > 15 DN
- MAE≤15 比例 < 80%

其余为 **MIXED**：有加速、保真不够干净，或反过来。不改 `ENABLE_REGION_BATCH`。

**KILL 本轮、不扩到整格** 的额外条件：出现硬封（连续 403 → 30 min backoff）或联合框下载把日期拼成混日。`--exact-date` 必须一直开着。

## §3 实现

| 路径 | 职责 |
|---|---|
| `scripts/temporal/gehi_region_batch.py` | 联合框、cluster、crop、像素对照（无 GEHI） |
| `scripts/temporal/run_a3_region_batch_pilot.py` | 可恢复六阶段 driver |
| `tests/temporal/test_gehi_region_batch.py` | 几何 / crop / compare，不联网 |

输出根：`~/zasolar_data/geid_temporal/a3_region_batch_pilot_20260818/`。

```bash
source scripts/activate_env.sh
pytest tests/temporal/test_gehi_region_batch.py -q
python scripts/temporal/run_a3_region_batch_pilot.py
```

## §4 明确不做什么

- 不改 `run_ct05_download_lane.sh` / citywide wave plan
- 不把 koko 拉进来
- 不关 `--exact-date`
- 不用 `--date a,b,c` 当多 vintage（那是混日填洞）
- 不在同一地址族上再开 GEHI 进程
- 不把 cache-hot 墙钟写成全城工期

## §5 若 GO，下一步才上桌

1. 非 Top-52 冷格子复测（真网络）
2. 一格全量（~400 锚 / ~1.1 km）是否仍 complete
3. 不同 vintage 不能并框，要按 `(grid, date)` 分组
4. owner 签字后才翻 `ENABLE_REGION_BATCH`

## §6 结果（2026-08-18T11:52Z）

输出：`~/zasolar_data/geid_temporal/a3_region_batch_pilot_20260818/report/summary.json`  
测试：`tests/temporal/test_gehi_region_batch.py` 10 passed。

### 数字

| 指标 | 值 | 预注册 bar | 过？ |
|---|---:|---|---|
| 联合框 `availability --complete` 含 2019-05-30 | 2 / 2 | 两格都要 | 是 |
| batch crop bbox QA pass | **24 / 24** | ≥ 95% | 是 |
| batch crop pixel QA pass | **24 / 24** | ≥ 95% | 是 |
| vs 同次 sequential median MAE | **4.72 DN** | ≤ 5 | 是 |
| vs sequential MAE ≤ 5 的比例 | **15 / 24 = 62.5%** | ≥ 90% | **否** |
| vs sequential MAE ≤ 15 | 24 / 24 | KILL 若 < 80% | 未触 KILL |
| GEHI 调用 | batch **2** vs sequential **24** | batch < sequential | 是 |
| 墙钟（cache-hot） | batch **2.92 s** vs sequential **51.40 s** | 比 ≥ 2× | 是（**17.6×**） |

联合框几何：CPT2713 约 259 × 254 m，`extra_frac=0.60`；CPT3584 约 288 × 314 m，`extra_frac=0.82`。12 张 96 m chip 重叠很多，并窗比逐张面积之和更小。

### 一次失败、一次修正（未改 bar）

第一轮未给联合框加边，GEHI 把输出往里收到不足 1 个像素，3 张落在包络边缘的 chip 切不出来（`window col_off=-1` 或 `row_end = height+1`）。这不是「大框不完整」——availability 已经报 complete。给 mosaic 请求加 `DEFAULT_MOSAIC_PAD_DEG = 2e-5`（开普敦约 2 m，约 7 个 z19 像素）后重下两张 mosaic，24/24 切开。

**这是什么**：GEHI 按自己的像素网格咬合输出，请求框的最外一圈可以缺不到 1 px。  
**为什么重要**：生产路径如果按「chip 窗并集」原样去下，每簇都会丢掉最边上的锚点。  
**忽略会怎样**：批量下载看起来成功，manifest 却缺 10% 左右的边缘 chip，QA 会把它们打成 missing，加速被缺口吃掉。

### 像素差从哪来（2026-08-18 更正）

预注册臂用的是 GEHI `download`。这个命令会把瓦片拼完之后，**整张 mosaic 默认写成 JPEG GeoTIFF**（0.5.1 没有 `--co COMPRESS=NONE`）。我们再从这张有损大图上切 chip，等于多做了一次整图 JPEG。这不是拼接的必要步骤，是选错了命令。

瓦片本身已经是 Google 的 JPEG。正确做法是 `dump`（把瓦片原样倒进文件夹，带 world file）→ 无损拼到画布上 → 按 chip 窗切 → 如需落盘再对**每张 chip**做和生产一样的 JPEG q=95。

同日对 CPT2713 的 12 张做了这个对照（25 块 z19 瓦片，5×5，cache-hot）：

| 切法 | vs 同次逐张 median MAE | MAE≤5 |
|---|---:|---|
| `download` 整图 JPEG 再切（预注册臂） | 5.84 | 5 / 12 |
| `dump` 无损拼再切 | **2.49** | **12 / 12** |
| 上一行的 crop 再 JPEG q=95 | 2.42 | 12 / 12 |

剩余 ~2.5 DN 来自切开窗和逐张窗差 1 个像素（24 张当初全部 `reprojected=True`），加上逐张臂自己也经过 GEHI `download` 的小图 JPEG。不是混日。`--exact-date` 全程开着。没有 403。

**这是什么**：预注册没过的「90% MAE≤5」很大一块是 `download` 的整图 JPEG，不是 A3 这个想法本身。  
**为什么重要**：生产路径若继续用 `download` 出 mosaic，会把本来过得了的保真 bar 自己做掉，也会让人误以为「批量切开就是对不齐」。  
**忽略会怎样**：要么因为一个可修的实现细节把 A3 丢掉，要么把错误的 `download` mosaic 写进生产。

### 判定

**MIXED（对预注册的 `download` 臂）。** 吞吐和 QA 过了；「90% MAE≤5」在整图 JPEG 切法下没过。KILL 条都没触发。

同日 `dump` 更正（只做了 CPT2713 一格）：12/12 MAE≤5，中位 2.49。若生产走 `dump` 无损拼，预注册那条保真 bar 在这一格上会过。另一格还没重测，所以正式判定不改成 GO，但原因要从「切开就不齐」改成「`download` 会整图有损，不该用它做 mosaic」。

对 Gemini 打分，2–5 DN 几乎一定看不见。若有人要把 chip 当档案原件做像素级复现，逐张 `download` 仍是当前真源；A3 生产路径应改用 `dump`，不要用 `download` 出大图。

墙钟 17.6× 测的是「进程 + 2.0 s 间隔」，不是冷缓存省了多少网。sequential 51 s / 24 张 ≈ 2.14 s/张，几乎就是 interval 本身。这正是你上次指出的税。外推全城冷下载日历仍然投机——没测过 1.1 km 整格并窗是否还 complete，也没测过非 Top-52 冷瓦片。

`ENABLE_REGION_BATCH` 保持 false。下一步若要升级：  
1. 生产路径默认带 2 m pad（已写进 `gehi_region_batch.py`）  
2. 按 `(grid, date)` 分组，不同 vintage 绝不并框  
3. 选一个非 Top-52 冷格子复测墙钟  
4. 再决定要不要试整格 ~400 锚 / ~1.1 km
