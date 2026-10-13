# PLAN — 附录图 D1–D6 执行说明（交付产物与交付方式）

| 字段 | 值 |
|---|---|
| 文档 ID | `PLAN-cv-appendix-figures-execution-2026-10-13` |
| 日期 | 2026-10-13（rev2 同日：负责人拍板后更新） |
| Status | **v1r2 已由 Opus 接受并上传**到 `dropbox:RA_Solar/appendix/ct_cv_appendix_figures_v1_2026-10-13/`（`rclone check --one-way` 0 差异，18 个文件）。`release_v1/` 与 `release_v1r1/` 未上传。**v2 已被 Opus 拒绝，没有上传。** v2r1 已由 Opus 接受（附小改），发布目录不改，尚未上传。见 [`RUN-cv-appendix-figures-v2r1-2026-10-14.md`](RUN-cv-appendix-figures-v2r1-2026-10-14.md)。被拒的 `release_v2/` 保持原样。**v2r2 已在本地组装，等验收，没有上传。** D2 是负责人选定的第 2 名格子 `89ad3617603ffff`，六张 D1 芯片不换。见 [`RUN-cv-appendix-figures-v2r2-2026-10-14.md`](RUN-cv-appendix-figures-v2r2-2026-10-14.md)。**v3 已由 Opus 接受（附小改），目录未改，尚未上传。** **v3r1 已由 Opus 接受并上传**到 `dropbox:RA_Solar/appendix/ct_cv_appendix_figures_v3_2026-10-14/`（`rclone check --one-way` 0 差异，10 个文件；只含 `release_v3r1/` 的内容）。见 [`RUN-cv-appendix-figures-v3r1-2026-10-14.md`](RUN-cv-appendix-figures-v3r1-2026-10-14.md)。负责人已于 2026-10-14 定下：矛盾数主口径是 1,008（τ = 90 天；有日期 619 + 只知到普查日 173 + 混合 216），619 是敏感性；月长维持 365.2425/12 天；混合地块不进直方图；登记表最早和最晚验收日可以公开；不新增 10 米规则。已上传的 v3r1 说明仍把 619 写成主表，本批不改那个目录，也不另建 `release_v3r2/`。Q3（L1952–54 的标注偏移）和 Q4（公开坐标是否四舍五入）仍开着。执行：Grok Build；验收：Opus 5.5 |
| 上游 | 任务清单 [`PLAN-cv-validation-appendix-tasklist-2026-10-13.md`](PLAN-cv-validation-appendix-tasklist-2026-10-13.md)（rev3）；SSEG 入库 [`DATA-sseg-registration-intake-2026-10-13.md`](DATA-sseg-registration-intake-2026-10-13.md)；市政 / Vexcel 复核 [`DATA-ct-vexcel-municipal-recheck-2026-10-13.md`](DATA-ct-vexcel-municipal-recheck-2026-10-13.md)；负责人决议 [`OWNER_DECISIONS.md`](OWNER_DECISIONS.md) D11 |
| 范围 | 只做附录的 **6 张图**（D1–D6），以及每张图的作图数据和图注草稿。表 D1–D7 和 3.1 对 3.5 的门槛比较不在本轮（见 §5） |

**一句话。** 按 D5/D3/D4 → D2（预览版）/D1 → D6 的顺序分三批交付，统一放在 Dropbox 的 `RA_Solar/appendix/` 下。第一批是不需要新影像的三张图；D2 先出预览版，不等发表许可；D6 放最后，因为它要复用前两批建好的地块匹配表。

**rev2 改动（负责人 2026-10-13 拍板）：**
1. 交付目录改为独立的 `RA_Solar/appendix/`，已建好。
2. 附录按 P1-A 改写，即"普查检测 + 卫星回溯 + 快照日期"。理由是 backdating 主要靠卫星图，不做逐年航拍检测。
3. 任务清单 §B 的 SSEG 口径定稿冻结，方括号里的建议值全部采用。D6 因此不再被阻塞。
4. D2 先出预览版。Vexcel 裁图可以直接用，图和文件名上标 PREVIEW。经济学投稿周期很长，发表许可以后再确认。
5. 分工：执行交给 Grok Build，验收交给 Opus 5.5。

---

## 0. 交付总表

| 批次 | 图 | 内容 | 主要输入 | 前置条件 | 工作量 |
|---|---|---|---|---|---|
| **v1** | **D5** | A：模型安装日期月度直方图；B：SSEG 验收日期月度直方图；C：两者的累计曲线（2019-01 至 2024-06）；另附卫星成像日期分布 | 全城行级表、SSEG、27 波 visits 表 | 无 | M |
| **v1** | **D3** | H3-9 格子散点：模型存量对 SSEG 存量，快照日 2024-06-30。A 面板是系统数，B 面板是容量。包括 OLS、分箱散点和 45° 线 | 全城行级表、SSEG、市政地块 | Phase 0 | M |
| **v1** | **D4** | 同一快照日的三张 H3-9 地图：模型、SSEG（两张共用色标）、对数比（发散色标） | 同 D3 | Phase 0 | S |
| v2 | **D2（预览版）** | 示例格子。A 面板是五个年份的航拍裁图（市政 2020Jan / 2021Mar / 2022Jan、Vexcel 2024、市政 2025Jan）；B 面板是 2025 底图上按首次出现年份着色的系统点，加每年新增数的柱状图（模型对 SSEG） | D3 的格子表、市政 WMS、Vexcel API | v1 的格子表 | M |
| v2 | **D1** | 2×3 的检测失败案例画廊：TP 住宅 / TP 工商 / FP 热水器 / FP 天窗或暗色屋面 / FN 小板或阴影 / FN 低对比 | Li 标注中**没进阈值标定**的 21 格，加普查检测结果 | 检测结果在盘上（见 §3 Phase 2） | M |
| v3 | **D6** | 逐户日期偏差 Δ = 模型区间中点 − SSEG 验收日（月）的直方图，叠加"完美一致"参考分布（按每对的实际区间宽度模拟） | Phase 0 的地块表 | 无（§B 已冻结） | M |

工作量 S/M/L 是粗估，没有实测。

---

## 1. 交付方式

### 1.1 发给合作者的包（Dropbox）

根目录：**`dropbox:RA_Solar/appendix/`**（本地挂载路径 `~/Dropbox/RA_Solar/appendix/`，2026-10-13 已创建）。每一批建一个新目录，旧批次不覆盖，方便对方对照修改。

```
RA_Solar/appendix/
└── ct_cv_appendix_figures_v1_<YYYY-MM-DD>/
    ├── README.md                 # 英文：每张图说什么、口径、已知局限、与合作者原方案的差异
    ├── captions.md               # 英文图注草稿，可直接贴进稿子
    ├── figs/
    │   ├── fig_D5_temporal.png   # 300 dpi，按期刊双栏宽 180 mm 排版
    │   ├── fig_D5_temporal.pdf   # 矢量版
    │   └── ...                   # D2 文件名带 _preview，例如 fig_D2_example_cell_preview.png
    ├── data/
    │   ├── fig_D5_monthly.csv    # 作图用的汇总数据（格子 / 月份级）
    │   └── ...
    ├── release_manifest.json     # 输入 sha256、快照日期、代码 commit、§2 口径常量
    └── SHA256SUMS.txt
```

- **图的格式。** PNG 用于浏览，PDF 用于排版。轴标签和图注用英文。
- **D2 预览版。** 图的角上加 "PREVIEW — imagery licence not cleared" 水印，README 里同样写明。
- **作图数据只给汇总级。** 交付格子级和月份级的汇总表，不放 SSEG 逐户行，也不放哈希户号。附录数据包将来可能公开（D.11），汇总级可以直接复用。
- **上传。** 用 `rclone copy --checksum` 传到 `dropbox:`，传完跑 `rclone check --one-way`，结果要是 0 差异。不要直接往 `~/Dropbox` 挂载目录里写，因为这个挂载会先把整包塞进本地缓存。
- **通知。** 每批验收通过后，执行 agent 起草一条中文群消息（链接、三句话结论、需要合作者回答的问题），由负责人转发。

### 1.2 留在本地和仓库的东西

| 什么 | 位置 |
|---|---|
| 作图脚本（一个入口，每张图一个子命令） | `scripts/validation/build_cv_appendix_figures.py`，提交进仓库 |
| 地块下载脚本（可断点续传） | `scripts/validation/fetch_coct_parcels.py`，提交进仓库 |
| 中间产物（地块缓存、匹配表、格子表、裁图） | `~/zasolar_data/geid_temporal/cv_appendix_20261013/`，不进 git |
| 每批的运行记录 | `docs/replan_v2/RUN-cv-appendix-figures-v{n}-<date>.md`：输入 hash、关键数字、QA 结果、和上一批的差异 |
| 状态 | 每批交付当天更新 `TRACKER.md` 和本文档的 Status |

### 1.3 分工

- **执行：Grok Build。** 按 §7 的执行说明做。
- **验收：Opus 5.5。** 按 §8 的清单逐项核对。不通过就打回给 Grok，不上传 Dropbox。
- **负责人。** 从 D1 的候选里挑 6 张，从 D2 的前三名里定一个格子，转发群消息。

---

## 2. 所有图共用的口径

这些约定决定数字本身。全部写死在脚本里的一个常量模块，每张图都从这里读，并原样写进 `release_manifest.json`。

1. **输入固定。**
   - 全城行级表：`~/Dropbox/RA_Solar/Gao/backdating/ct_citywide_install_dated_2026-09-02_research_preview/ct_citywide_install_dated_20260902.csv`，111,801 行。sha256 要和同目录 `SHA256SUMS.txt` 一致。
   - SSEG：`~/zasolar_data/sseg/sseg_registration_geo_2026-10-13.csv`，sha256 `40c54ab824cb74d5561359b03dcb2f909e3021352ef6de7312d088dc33fa1b14`。
   - 输入一换，批次号就要跟着换。
2. **模型。** 全城表用的是 Gemini 3.1 Flash Lite，加 3.6-high 补救。3.5 只覆盖十格，不进附录图。图注里写明模型版本。
3. **SSEG 子集。** 主口径只用 commissioned 记录，即 `status` 以 `1.0 ` 或 `4.0 ` 开头的 5,458 行。坐标用 `wkt` 列；`latitude/longitude` 只保留 3 位小数，**禁止使用**。敏感性口径另外加上离网 `2.0`。
4. **快照日期。** 主快照取 **2024-06-30**，因为 SSEG 最晚的验收日是 2024-07-02，再往后 SSEG 必然偏少。面板版的快照取 2020–2024 年每年 1 月 31 日，再加 2024-06-30。
5. **模型在快照 S 的存量判定。**
   - 区间终点 ≤ S：已装。区间起点 > S：未装。
   - 区间跨过 S：主口径按中点判定；另按上界和下界各算一版，用作敏感性。
   - 左删失（2019 年前已有）：所有快照都算已装。
   - census_bound：按同样的规则处理，区间取 [`latest_absent_date`, 2025-01-31]。
   - 未定日的 1,031 行：不计入存量，在图注里报告数量。
6. **计数单位：有光伏的地块。** 普查多边形的质心落进地块，SSEG 的点也落进地块，两边都统计"有光伏的地块数"。地块数据来自市政 `ODP_SPLIT_4/MapServer/0`。建筑轮廓（P3）推迟，不在本轮。
7. **容量。** SSEG 用 `generator_capacity_(va)`。模型侧用多边形面积 × **0.19 kWp/m²**。**不能用** 0.040 kW/m²：那个系数是拿 SSEG 拟合出来的，再拿它和 SSEG 比较就是循环论证。图注要写明，多边形面积不等于组件面积，所以斜率的绝对值不能直接解读。
8. **格子范围。** 取完全落在普查航拍覆盖内的 H3-9 格子，并排除 Eskom 直供区（见 §6 第 1 条）。
9. **日期点。** 直方图里的模型日期取区间中点。另画一版把每个区间均匀摊到它跨过的月份上，作为稳健性检查，只放进 README。
10. **§B 已冻结（D6 用）。** 主样本是地块上恰好一条 commissioned 记录、坐标落在地块内。验收日 C 当作安装日期的上界。τ = 90 天。敏感性取 τ = 0、30、180 天，以及 10 m 缓冲区匹配。以上全部按任务清单 §B.3–B.5 执行，不得改动。

---

## 3. 分阶段执行

### Phase 0 — 公共底座（v1 的前置条件）

| 步 | 做什么 | QA 检查（不过就不往下走） |
|---|---|---|
| 0.1 | 校验两个输入文件的 sha256，写进 manifest | 和 §2 第 1 条一致 |
| 0.2 | 下载市政地块：`https://citymaps.capetown.gov.za/agsext/rest/services/Theme_Based/ODP_SPLIT_4/MapServer/0/query`，按 `OBJECTID` 分页，每页 ≤2,000 条，共约 420 次请求。在 tmux 里跑，可断点续传，结果存成 gpkg | 总数等于服务端 `returnCountOnly=true` 返回的数（2026-10-13 记录为 835,018）；几何有效；字段里有 `ZONING` |
| 0.3 | 给普查行加 `h3_r9`；用 `wkt` 重新计算 SSEG 的 H3-9，和表里自带的 `h3_level_9` 对照 | 报告两者不一致的比例；只要有不一致，一律以重算值为准 |
| 0.4 | 普查质心落地块、SSEG 点落地块，生成地块级表：地块 ID、`ZONING`、模型区间（取地块内最早的 L 和 U）、SSEG 最早验收日、记录数、坐标来源 | 报告匹配率，按 `contract_direct` / `device_recovered` 分开报。SSEG commissioned 的匹配率低于 90% 时，停下来报告 |
| 0.5 | 找 Eskom 直供区的边界（见 §6 第 1 条），并标记受影响的格子 | 写清楚边界来源；找不到就按 §6 的兜底方案处理，并在 RUN 文档里注明 |

### Phase 1 — v1：D5、D3、D4

- **D5。**
  - 按月统计，横轴 2019-01 至 2025-01。
  - A 面板只画有日期的系统。左删失和 census_bound 在两侧各单独画一根柱子并标注，不混进月度柱。
  - B 面板画 SSEG 的验收日。
  - C 面板把两条累计曲线截到 2024-06-30，再叠上卫星成像日期的分布。27 波的成像日期来自 `ct_27wave_visits_20260923.csv`（Dropbox `RA_Solar/Gao/backdating/ct_citywide_27wave_robustness_2026-09-23/`）。Top-52 没有成像序列，图注写明这一点。
  - 全国限电小时数由合作者提供，没给就先不画。
  - 月度新增数的交叉相关（错开 0–12 个月）只写进 README 和 CSV，不画进图。
- **D3。**
  - 主图用 2024-06-30 单快照。
  - OLS 带常数项，用稳健标准误。图上画空心半透明散点、20 分位分箱均值、拟合线和 45° 线，并在图内注明斜率（SE）、R²、N，以及"两边都为零"的格子占比。
  - 稳健性放进 README：面板版加快照固定效应，剔除零-零格子，只看住宅格子（按地块 `ZONING`），SSEG 加上离网。
- **D4。** 三张 H3-9 地图，底图淡化并画出市界。模型和 SSEG 两张共用一个对数色标；第三张画 log((1+模型)/(1+SSEG))，用发散色标。

### Phase 2 — v2：D2（预览版）、D1

- **D2。**
  - 按任务清单 D.5 的规则选格子：至少 50 个住宅地块，并且 2020-01 至 2024-06 新增的光伏地块最多。
  - 列出前三名的候选卡片，由负责人选定一个。被单个大工商屋顶或伪影主导的格子要剔除。
  - 航拍裁图：
    - 市政各期用 WMS `GetMap`。可以参考 `~/zasolar_data/geid_temporal/coct_muni_recheck_20261013/sample_coct_wms_coverage.py`。
    - Vexcel 2024 用 ZAsolar 的 `core.vexcel_auth`（`load_env` / `resolve_token`），调用方式参考 `~/zasolar_data/geid_temporal/vexcel_ct_recheck_20261013/probe_ct_vexcel.py`。
  - 所有面板统一范围和比例尺。图注逐期写明来源、GSD 和拍摄日期。
  - 加 PREVIEW 水印。
- **D1。**
  - 只从 Li 标注（`/home/gao/projects/ZAsolar/data/annotations/Capetown_Li`）里**没进阈值标定**的格子中挑。哪些格子进过标定，以 ZAsolar `configs/eval/operating_point_calibration.yaml` 为准。按任务清单的记录，74 格减去 53 格，应剩 21 格。
  - 先按 IoU ≥ 0.5 匹配检测和标注，自动分出 TP / FP / FN 候选。每类给 5–10 个候选，拼成一个审阅页，由负责人挑出 6 个。
  - 画廊只作示例，不声称准确度。
  - **开工前先核实**：这些格子的普查检测结果在不在盘上。不在就停下来报告，**不要自行重跑检测**。

### Phase 3 — v3：D6

- 用 Phase 0 的地块表，按 §2 第 10 条取主样本，并限定验收日早于 2024-10-31（普查日前 3 个月）。
- Δ 只对有日期的系统计算。census_bound 和左删失只报数量和类别，不进直方图。
- 参考分布：假设真实日期在每对自己的区间内均匀分布，模拟出 Δ 的分布。因此每一对的参考宽度不同。
- 需要报告：N、均值、中位数、IQR，|Δ| ≤ 6 个月和 ≤ 12 个月的占比，验收日落在区间内的占比，以及登记滞后 C − U 的中位数和 IQR。
- 匹配和分类的代码写成可复用的函数，以后 3.1 对 3.5 的门槛比较直接调用，避免两边口径分叉。

---

## 4. 拍板状态

| # | 问题 | 结论 | 影响 |
|---|---|---|---|
| 1 | 附录是否按 P1-A 改写 | **已定：是**（负责人 2026-10-13，理由是 backdating 主要靠卫星图） | D2、D3、D4 |
| 2 | 计数单位 | 默认：有光伏的地块 | D3、D4、D6 |
| 3 | 主快照日期 | 默认：2024-06-30 | D3、D4 |
| 4 | 模型侧容量系数 | 默认：0.19 kWp/m² | D3 B 面板 |
| 5 | Vexcel 能否发表 | **D2 先出预览版**，发表许可以后再确认 | D2 |
| 6 | 限电小时序列 | 默认：由合作者提供；没有就不画 | D5 C 面板 |
| 7 | §B 口径冻结 | **已定：冻结**，方括号建议值全部采用（负责人 2026-10-13） | D6 |

第 2、3、4、6 条是默认值。README 里要列出来，请合作者确认；合作者没有异议就照用。

---

## 5. 不在本轮的

- **表 D1–D7。** 等图交付后再做。表 D5 的相关系数、表 D6 的匹配率会直接复用本轮的格子表和地块表。
- **3.1 对 3.5 的 SSEG 门槛。** §B 已冻结，可以另开 RUN 文档执行。它会复用 Phase 3 的匹配代码，但只在十格样本上比较，不在本轮做。
- **建筑轮廓归属（P3）、新测试集（P4）、日期准确度（P5）。** 本轮不解决，在 README 的局限一节里写明。

---

## 6. 已知风险

1. **Eskom 直供区。** 市政 SSEG 只登记由市政供电的用户，Eskom 直供区的屋顶光伏不会出现在表里。如果不排除这些格子，D3 和 D4 会显示"大量未登记"，但那其实是供电归属造成的。处理办法：在 Phase 0.5 找市政供电区的边界，可以查开放数据门户，也可以看 SSEG 的 `electricity_region` / `bulk_supply_zone` 字段。找不到时，在 D4 上标出 SSEG 零登记的连片区域，并在图注里说明。
2. **两边数量级差很多。** 普查有 111,801 个多边形，SSEG commissioned 只有 5,458 条。即使都换算成地块，模型存量也会远大于 SSEG，D3 的斜率会远离 1。这主要反映未登记的系统（会议已定不处理低报），不是检测误差。README 开头就要讲清楚。
3. **中点日期会造出假峰。** census_bound 和左删失如果当作点日期，会在 2025 年和 2019 年各堆出一个尖峰。README 要提醒合作者重画时不要这样处理。
4. **Top-52 没有成像序列。** 这 21,453 行没有重新解码，所以 D5 的成像日期分布只覆盖 27 波。
5. **地块匹配率。** ZAsolar `validation_strategy.md` 记录过 SSEG 地理编码有错配。0.4 先看匹配率，偏低就停下排查，不要硬出图。

---

## 7. 给执行 agent（Grok Build）的说明

### 7.1 环境

- **所有数据都在 `rog` 上**（tailnet `100.111.51.77`），工作也在这台机器上做。如果你在别的主机上，用 `ssh -o BatchMode=yes gao@100.111.51.77` 登录。
- 仓库：`/home/gao/projects/solar_backdating`。先运行 `source scripts/activate_env.sh`。解释器是 `/usr/bin/python3`，包装在 `~/.local`，已有 h3 4.x、geopandas 1.1、matplotlib 3.10。不要新建虚拟环境，也不要 pip 升级已有的包。
- Dropbox：rclone remote 叫 `dropbox:`，在 rog 上已经配好。
- 超过 30 分钟的任务（地块下载、航拍裁图）必须在 tmux 里跑，命名为 `tmux new -d -s cvapp_<step>`，脚本要能断点续传，进度写进日志。

### 7.2 硬规则

1. **输入只读。** 不改动、不移动、不重写 §2 第 1 条的两个输入文件。
2. **口径不改。** 改 §2 里的任何一条，都要先停下来报告，不能自己调整。
3. **不提交数据。** 只提交脚本和 docs。数据写到 `~/zasolar_data/geid_temporal/cv_appendix_20261013/`。
4. **不碰门槛。** 不要在十格上做 3.1 对 3.5 的比较，也不要把 D6 的数字说成"模型准确度"。
5. **不重跑检测或 Gemini。** 需要新的模型推理时，停下来报告。
6. **验收通过前不上传。** 每一批先在本地出齐，交 Opus 验收，通过后才上传到 `dropbox:RA_Solar/appendix/`。

### 7.3 每批的步骤

1. 按 §3 实现并运行，产物放在本地的 `cv_appendix_20261013/release_v{n}/` 下，目录结构与 §1.1 相同。
2. 写 `docs/replan_v2/RUN-cv-appendix-figures-v{n}-<date>.md`，内容包括：输入 hash、各 QA 检查的结果、关键数字（匹配率、N、斜率、相关系数）、所有偏离本计划的地方及原因。
3. 提交脚本和 RUN 文档，commit message 用英文。
4. 通知验收。说明本地产物路径和 RUN 文档路径。
5. 验收通过后：`rclone copy --checksum` 上传，然后 `rclone check --one-way`，要求 0 差异。之后更新 `TRACKER.md` 和本文档的 Status，并起草群消息。

### 7.4 必须停下来报告的情况

- 输入的 sha256 不一致。
- 地块总数和服务端计数不一致，或下载反复失败。
- SSEG commissioned 的地块匹配率低于 90%。
- D1 所需的检测结果不在盘上。
- Vexcel token 失效，或 API 返回权限错误。
- 任何一张图需要改动 §2 的口径才能画出来。

---

## 8. 验收清单（Opus 5.5）

每批都要全部打勾才能上传。

- [ ] **口径。** `release_manifest.json` 里的常量与 §2 逐条一致。脚本里没有绕开常量模块的硬编码数字。
- [ ] **输入。** 两个 sha256 都对。
- [ ] **总量能对上。** SSEG commissioned 是 5,458 行；全城表是 111,801 行，四类计数分别是 73,336 / 27,591 / 9,843 / 1,031。存量和匹配统计都能从这些总数推回去。
- [ ] **坐标列。** SSEG 只用了 `wkt`，没有用 3 位小数的 `latitude/longitude`。
- [ ] **删失处理。** 左删失和 census_bound 没有当作点日期混进月度直方图。
- [ ] **容量。** 没有用 0.040 kW/m²。
- [ ] **数字可复现。** 从 `data/` 里的 CSV 重算图注中的每个数字，结果一致。至少抽 5 个数字核对。
- [ ] **隐私。** `data/` 里没有 SSEG 逐户行或哈希户号。
- [ ] **D2。** 有 PREVIEW 水印，文件名带 `_preview`，图注逐期写了来源、GSD 和日期。
- [ ] **D6。** 主样本和 τ 与 §B 一致，敏感性完整，没有准确度措辞。
- [ ] **文档。** README 写了 §4 第 2、3、4、6 条默认值请合作者确认，也写了 §6 的风险；RUN 文档记录了所有偏离。
- [ ] **视觉。** 抽看全部图：轴标签是英文、有单位，色标有说明，在 180 mm 宽度下字号可读。
