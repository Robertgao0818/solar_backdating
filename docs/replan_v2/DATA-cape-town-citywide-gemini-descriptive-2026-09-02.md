# DATA — Cape Town 全城 Gemini 推理描述性统计（Top-52 行级对齐 + 27 wave）

| 字段 | 值 |
|---|---|
| 文档 ID | `DATA-cape-town-citywide-gemini-descriptive-2026-09-02` |
| 日期 | 2026-09-02（同日下午对齐重做） |
| Status | **DECODE + ALIGNED DESCRIPTIVE + CITYWIDE CSV/GPKG RESEARCH PREVIEW**（仍不是 E8 完整交付：无多模型面板核验、无准确度声称） |
| 上位 | [`RUN-cape-town-citywide-batch-disk-pipeline-2026-08-18.md`](RUN-cape-town-citywide-batch-disk-pipeline-2026-08-18.md) · Leg-E E8 [`RUN-cape-town-citywide-legE-production-plan-2026-08-12.md`](RUN-cape-town-citywide-legE-production-plan-2026-08-12.md) |
| 产物根 | `~/zasolar_data/geid_temporal/cape_town_citywide_backdating_v1_20260812/e8_descriptive_20260902/` |
| HTML | `descriptive_stats_2026-09-02-citywide-aligned.html`（自包含；两张 Esri 底图是主图） |
| Dropbox 交付包 | `RA_Solar/Gao/backdating/ct_citywide_install_dated_2026-09-02_research_preview/` |
| Dropbox HTML 单文件 | `RA_Solar/Gao/backdating/descriptive_stats_2026-09-02-citywide-aligned.html`（与包内同哈希） |
| Top-52 源 | Dropbox `RA_Solar/Gao/backdating/ct_top52_install_dated_2026-08-03_research_preview/` |

**一句话。** 开普敦 111,801 个普查多边形是一张行级表：Dropbox Top-52 交付（21,453）与 27-wave Gemini（90,348）按 `anchor_id` 无重叠拼合。有界安装日 **90.3%**，真实括号 dated **65.6%**，区间中位 **37 天**，`gemini_failed` **0**。wave_04 原先 638 条「失败」是 3.6 rescue 账号 400 盖掉 Lite，已撤回；dip-repair 从中救出 380 dated + 217 左删失。同日已打成与 Top-52 / JHB 同构的 CSV+GPKG 研究预览包，放到 Dropbox `RA_Solar`。这是覆盖描述，不是准确度。

上午那版只能把冻结类别计数加总，地图上没有 Top-52 的点，也没有底图。覆盖数字没有变；变的是分母对齐和空间可读性。

---

## 1. 分母与对齐

| 队列 | 锚点数 | 来源 |
|---|---:|---|
| 全城 census | 111,801 | `anchors_v1/anchors_all.csv` |
| Top-52（冻结交付，行级） | 21,453 | Dropbox `ct_top52_install_dated_20260803_lite_v2.csv`（SHA256 `d806aeed…d76a`） |
| non-Top-52 / 27-wave | 90,348 | 上午从 scan state 解码的 `classified_citywide_27wave.csv` |

对齐闸门（`align_top52_and_restat.py`）：

- Top-52 `source_anchor_id` ∩ 27-wave `anchor_id` = ∅
- 并集 = 全城 111,801 锚点，0 缺口、0 多余
- `source_feature_id` 同样无重叠
- Top-52 `source_grid` 唯一值 = 52

27 波 primary scan state 与 wave plan 逐波对齐（A24/A48 拆分也对齐），全部 `SCORE_OK` + `RELEASE_OK`。Rescue（`gemini-3.6-flash-high`）23,928 文件全部是 primary 的子集。CPT3622 Lite rescore 336 锚点（wave_23）。合并优先级：CPT3622 rescore → 3.6-high rescue → Lite primary。

**这张表说什么。** 两段生产跑的是同一张普查清单的无重叠分割，现在可以当一张表用。  
**为什么要紧。** 经济层、外部校验、地图都需要行级 `install_interval_*`，不能只加类别计数。  
**忽略会怎样。** 继续只用 27-wave 画空间图，等于把密度最高的 52 格从开普敦地图上挖掉。

## 2. 覆盖类别（行级，与上午冻结加总一致）

Deliverable 分类与 `build_ct_install_dated_deliverable.classify` 相同：

- **dated**：`done_appears` 且括号完整（真实缺席→出席）
- **census_bound**：`done_installed_during_census`（安装 ≤ 普查日）
- **left_censored**：`done_already_present_before_geid_history`（2019 窗口前已在）
- **undated**：`done_ambiguous_*`（本批无 inverted-interval 再分类）

| 类 | Top-52 行级 | 27-wave | 合计 | 占 111,801 |
|---|---:|---:|---:|---:|
| dated | 13,908 | 59,428 | **73,336** | **65.6%** |
| census_bound | 5,157 | 22,434 | 27,591 | 24.7% |
| left_censored | 2,238 | 7,605 | 9,843 | 8.8% |
| undated | 150 | 881 | 1,031 | 0.9% |
| **有界（dated+census）** | 19,065 | 81,862 | **100,927** | **90.3%** |
| n | 21,453 | 90,348 | 111,801 | 100% |

Top-52 行级 `scan_status` 与冻结计数一一对应：`done_appears` 13,908 · census 5,157 · already-present 2,238 · nonmonotonic 142 · no_recent 8。没有 inverted 再分类。

**这张表说什么。** 全城约九成锚点能给出单侧或双侧安装日界，约三分之二有真实括号；高密度 52 格和其余 27 波几乎同一水平（dated 64.8% vs 65.4%，区间中位都是 37 天）。  
**为什么要紧。** 经济层可以按覆盖类分层：dated 走区间删失，census_bound 只当“不晚于 2025-01-31”，左删失当“2019 前已在”，undated 必须排除。  
**忽略会怎样。** 把 census / 左删失当点日期，会在 2024 和 2019 造两个假峰；把 1.5% undated 当随机缺失，会在 wave_04 / 歧义屋顶上产生选择偏差。

全城扫描状态（Top-52 交付 + 27-wave dip-repair 后）：

| status | n |
|---|---:|
| `done_appears` | 73,336 |
| `done_installed_during_census` | 27,591 |
| `done_already_present_before_geid_history` | 9,843 |
| `done_ambiguous_nonmonotonic` | 973 |
| `done_ambiguous_no_recent_anchor` | 58 |

Dip-repair（仅 27-wave）：50,570 次内部翻转 / 12,558 锚点。27-wave 90,348 行全部 terminal，无 load failure。Top-52 未重解码。

## 3. 两张底图（本报告主图）

PNG：`e8_descriptive_20260902/figs_aligned/map_coverage_class.png` 与 `map_median_year.png`。底图 Esri World Topo，投影 EPSG:3857，黑框 = Top-52 的 52 个 1 km 格。

### 图 1 — 覆盖类别（111,801 个质心）

dated（蓝）铺满半岛、北部郊区和东南 Helderberg；census 上界（绿）分散。未定日（橙）不再在 Muizenberg 成团：wave_04 那 638 个 `gemini_failed` 已撤回 3.6 rescue 覆盖。黑框散落在大西洋岸、北部郊区和 Helderberg 等屋顶最密的口袋。

**它说明什么。** 撤回前的橙团是 API 覆盖事故，不是缺底图、也不是西南郊没装上。  
**为什么要紧。** 经济层现在看到的橙点是真的非单调 / 缺近期锚点，可以当 undated 排除，不必当 operational failure。  
**忽略会怎样。** 继续用撤回前的 638 个 `gemini_failed` 当仪器报废，会少 380 个 dated 和 217 个左删失。

### 图 2 — H3-7 cell 的 dated 安装年中位数

111,801 个多边形按质心落入 **381** 个 Uber H3 resolution-7 cell（本纬度面积约 4.88 km²）。图上着色的是其中 **272** 个 `n_dated ≥ 8` 的 cell。Join 键 `h3_index`，行级表另有 `h3_r7`。现成的 `vexcel_sa_h3_r7_coverage_2026-08-12` 不含开普敦（与这 381 个 cell 交集为 0）——那是覆盖范围，不是 ID 对不上。

2019–2021（紫）只在少数内陆 cell 占中位；大西洋岸到 False Bay 一线大量 cell 是 2024（黄）。上色 cell 的中位年分布：中位数 2023、p75 2024。每个上色 cell 里 dated 多边形中位数 143 个（最多 1,319）。Top-52 黑框没有自成一套更早或更晚的颜色岛。

**它说明什么。** 可见安装的年份中位在全城被 2022–2024 主导；格子是全球固定索引，不是这张图私有的绘图网格。  
**为什么要紧。** 以后叠人口、普查、电网或其他 H3-7 层，用同一套 `h3_index`。  
**忽略会怎样。** 继续用 matplotlib hexbin，数字对不上任何现有 H3 表。

## 4. 全城时间、精度（含 Top-52 dated）

Dated 中点范围 2019-01-22 … 2024-08-30。年份计数（vintage 量子化，尖峰是公共影像日）：

| 年 | n | 占 dated |
|---|---:|---:|
| 2019 | 1,195 | 1.6% |
| 2020 | 2,146 | 2.9% |
| 2021 | 4,297 | 5.9% |
| 2022 | 14,989 | 20.4% |
| 2023 | 17,349 | 23.7% |
| 2024 | 33,360 | 45.5% |

日期精度（dated 的 absent→present 间距）：中位 **37 天**（IQR 30–91），≤180 天 **80.0%**，最长 1,258 天。Top-52 单独：中位 37、p75 91、≤180 天 84.0%（与冻结口径一致，现从行级重算）。27-wave：中位 37、p75 92、≤180 天 79.1%。接上 Top-52 之后全城精度略升，因为 Top-52 的窄括号更多。

`install_confidence`（扫描侧启发式，不是校准概率）：high 85,028（76.1%）· medium 15,449 · low 11,174 · 空白 150（Top-52 未定日）。

面积：中位 17.3 m²（IQR 11.0–28.1），范围 5.0–17,069。质心 bbox lon 18.307–18.904、lat −34.320–−33.694（EPSG:4326）。

## 5. Lite / rescue / rescore / Top-52

| source_kind | n | dated | census | left | undated |
|---|---:|---:|---:|---:|---:|
| `top52_lite` | 21,453 | 13,908 | 5,157 | 2,238 | 150 |
| `primary_lite` | 66,839 | 43,575 | 21,522 | 1,701 | 41 |
| `rescue_36high` | 23,173 | 15,653 | 811 | 5,870 | 839 |
| `rescore`（CPT3622 Lite） | 336 | 200 | 101 | 34 | 1 |

Rescue 覆盖其余 27-wave 里 Lite 判 ambiguous 的锚点。wave_04 那 638 条不再算 rescue：3.6-high 当时整波 400，已撤回 Lite。Primary Lite 现在有 41 条 undated（撤回后仍非单调的那些）。Top-52 未走 3.6 rescue。

## 6. wave_04：撤回 3.6 rescue 覆盖（2026-09-02）

不是缺底图。638 个 `gemini_failed` 的 Lite 主打其实全部打完，状态是 `done_ambiguous_nonmonotonic`；3.6-high rescue 用 standard-tier Antigravity 号，每帧 HTTP 400（`需配置 project_id`）；merge `--allow-supersede` 用这份空 rescue 盖掉了 Lite。

撤回：把 638 个 merged scan state 换回 `primary_wave04`，backup 在 `e8_descriptive_20260902/revert_w04_rescue_overlay_20260902T101143Z/`，再跑 `infer_install_dates.py`（dip-repair 开）。

| 这 638 条撤回后 | n |
|---|---:|
| dated（dip-repair 修掉有→无回跳） | 380 |
| left_censored | 217 |
| 仍非单调（真 undated） | 41 |
| 仍 `gemini_failed` | **0** |

全城因此：dated 72,956 → **73,336**；undated 1,628 → **1,031**；`gemini_failed` 638 → **0**。wave_04 现在 dated 1,916 / census 1,207 / left 285 / undated 44。

Wayback CRS 事故仍是同一波的**下载**问题（dump 切图飞窗，改走逐张回退），与这 638 条打分失败无关。没有重下 chip。

merge 代码仍允许失败 rescue 覆盖成功 Lite——这是复发点，尚未改 `merge_ct_scan_states.py`。3.6 也没有重打。

## 7. 产物与哈希

根：`~/zasolar_data/geid_temporal/cape_town_citywide_backdating_v1_20260812/e8_descriptive_20260902/`

| 文件 | sha256 |
|---|---|
| `intervals/install_intervals_all.csv`（90,348 行，撤回后重解码） | `ac1e469c6f6e61f411e98324c6b21e63c7e1918fd84c83028f0060a5831bb2db` |
| `classified_citywide_27wave.csv` | `7fc0299b36ff1a660ae7decb99805f878f61ec86120cb6eed9c5b23e8acc4a1c` |
| `classified_citywide_all.csv`（111,801 行，含 `h3_r7`） | `cc8f17ad862d8bd8438b72f31ba283f21b8b08447077bc9b50a4748f151f4830` |
| `h3_r7_cells.csv`（381 cell） | `2e2859e5e93c8bb0f9b74ab071833957d871f015469332a9135ff505568af28a` |
| `h3_r7_cells.geojson` | `ea934075e2ee673327ae939ec3ce76e0997114acaa43d627a4ee6c878da75e95` |
| `summary_aligned.json` | `1b163ecc514ad6bdbbce9152a5c3d0963d25791ba792ef7f200e547a2af7b6b2` |
| `descriptive_stats_2026-09-02-citywide-aligned.html` | `7654175cac07ff0ef1e2caebd3b54babd71587c89e82fe754e3014e41833d03c` |
| `figs_aligned/map_coverage_class.png` | `383a7482abaa634c272dbf37fe1a32489df2aa5cb14901754635b12a67b33b32` |
| `figs_aligned/map_median_year.png`（H3-7） | `5ecb614bae81853ad7e0b5eed571d60f048564af281d2a255857b3c4d07dcba0` |
| Top-52 源 CSV | `d806aeed084a41ad00c30b41490887e78c4fdf41f3d3a2e395a1a649eb53d76a` |

生成器：同目录 `align_top52_and_restat.py`（对齐 + 重统计 + 两张底图）。上午的 `build_ct_citywide_descriptive.py` 仍负责 27-wave 的 merge/infer；本次没有重跑解码。

打开 HTML：

```bash
xdg-open ~/zasolar_data/geid_temporal/cape_town_citywide_backdating_v1_20260812/e8_descriptive_20260902/descriptive_stats_2026-09-02-citywide-aligned.html
```

## 8. 全城 CSV/GPKG 研究预览（同日）

本地：`~/zasolar_data/geid_temporal/cape_town_citywide_backdating_v1_20260812/deliverable_2026-09-02/`  
Dropbox：`RA_Solar/Gao/backdating/ct_citywide_install_dated_2026-09-02_research_preview/`

生成器：`scripts/temporal/build_ct_citywide_install_dated_deliverable.py`。Top-52 21,453 行从 Dropbox 交付 CSV 反演回 interval 合同，与 27-wave `install_intervals_all.csv` 做无重叠并集，再按 `source_feature_id` 接到普查多边形（EPSG:32734）。结构门全部 PASS，覆盖类与冻结分类表逐项一致。

| 文件 | sha256 |
|---|---|
| `ct_citywide_install_dated_20260902.csv`（111,801 行；Top-52 字段 + `h3_r7`/`source_kind`/`wave`/`cohort`） | `c0bf9ed149fb73a97f34354c82f881df3f45769cfafe1e44e9fc68b1e29e7563` |
| `ct_citywide_install_dated_20260902.gpkg`（layer `ct_solar_install_dated`，EPSG:32734） | `30251ca685d879efd4ec3b1d65cef214b68ec3fff2e671a9c372e875474f17dd` |
| `descriptive_stats_2026-09-02-citywide-aligned.html` | `7654175cac07ff0ef1e2caebd3b54babd71587c89e82fe754e3014e41833d03c` |
| `gates.json` | `b703ee9b6b419098d4ec2f47b62e2dea4f2d84fa2cff184d4dfa2e7f7d02fce3` |

`source_kind`：`top52_lite` 21,453 · `primary_lite` 66,839 · `rescue_36high` 23,173 · `rescore` 336。`h3_r7` 111,801/111,801 非空。Dropbox 包内 `SHA256SUMS.txt` 已 `sha256sum -c` 全绿。

这不是 hex×year 存量面板：一行仍是一个普查多边形。H3-7 只作为 join 键（`h3_r7`）和 381-cell 汇总表。

## 9. 明确没做的

1. ~~全城 CSV/GPKG 交付物~~ **已做**（§8；口径仍是 research preview）。
2. 多模型面板盲审（Leg-E E8 核验）。
3. 准确度声称。CT-11 纠正后 exact-bracket Wilson 下界仍是 17.3%，70% 门未过。
4. 给 `merge_ct_scan_states.py` 加闸（失败 rescue 不得覆盖成功 Lite）。
5. 重打 3.6-high（网关 `project_id` 配好之前不要开）。
6. 重解码 Top-52：用的是 2026-08-03 Dropbox 交付 CSV，不是从 scan state 重跑。

## 10. 2026-09-23：逐次 0/1 已补进加列版

27-wave 的 90,348 条逐次序列写进了 sibling 目录 `sequences_20260923/`（宽 CSV、GPKG，以及一次观测一行的长表）。repaired 序列和本交付表的次数、`latest_absent_date` 逐行一致。Top-52 的 21,453 行仍无 JSON，序列列为空。本目录和 Dropbox 包的哈希没有改。详见 [`DATA-cape-town-presence-sequences-2026-09-23.md`](DATA-cape-town-presence-sequences-2026-09-23.md)。
