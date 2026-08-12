# Cape Town 全城 Backdating 交付计划

日期：2026-08-12  
版本：**DRAFT-v2 — 工程优先 / 双轨并行**（供 fable-5 终审）  
状态：

| 轨道 | 状态 | 含义 |
|---|---|---|
| **Leg-E 工程轨** | **GO for prefetch** | 全城底图/catalog/chip 下载与 QA **可以且应当推进**；不产生日期产品 |
| **Leg-S 科学轨** | **EXPLORE / HOLD for release** | 时序方法、reference、学生模型等并行探索；**不得**用探索结果静默解锁发布 |
| **全城 scorer** | **HOLD** | 无 owner 书面 GO 不得对全城跑生产打分 |
| **对外发布** | **HOLD** | Top-52 与全城日期产品均未获准发布 |

Owner：ZAsolar install-date workstream  
上位方案：[`../install_date_optimization_v2_prd.md`](../install_date_optimization_v2_prd.md)  
Top-52 运行记录：[`RUN-cape-town-backdating-tracker-2026-07-24.md`](RUN-cape-town-backdating-tracker-2026-07-24.md)  
Top-52 准确度结果：[`RUN-cape-town-ct11-corrected-qa-2026-08-03.md`](RUN-cape-town-ct11-corrected-qa-2026-08-03.md)  
阶段简报：[`BRIEF-cape-town-status-2026-08-12.html`](BRIEF-cape-town-status-2026-08-12.html)

**本版相对 08-12 初稿的核心变更**：工程落地优先，全城影像预取与科学验收解耦；科学工作改为并行探索轨，不再阻塞底图下载。

---

## 0. 双轨原则（plan-of-record）

```text
Leg-E 工程轨（不断）                Leg-S 科学轨（并行）
─────────────────                  ─────────────────
scope freeze + owner 预取决议       人工校准 / 尺子诊断
    │                                  │
全城 catalog / chip 下载               仪器消融（拼贴 vs 分图）
    │                                  │
chip QA + 容量/磁盘治理                时序方法探索（L0/L1）
    │                                  │
frozen offline imagery ready           配准 / 池化 / 学生模型等
    │                                  │
    └──── owner scorer GO? ────否──────┴── 继续探索，不发布
                    │是
              异质 canary → waves → 交付
                    │
              仅当 Leg-S 验收通过才对外发布
```

### 0.1 三条硬边界

1. **下载 ≠ 准确度通过**。预取进度、覆盖率、chip 完整率不得写进 accuracy headline，也不得解释为 CT-11 翻盘。
2. **打分 ≠ 发布**。即使未来 owner 授权有限 scorer canary，在预注册确认通过前，结果只能进内部 run root，不得发布。
3. **科学探索不得污染密封集**。`ct11_disjoint_holdout_v1_20260804`（500+100）在方法锁定前禁止读取、渲染或间接查询。

### 0.2 为什么这样拆

- Top-52 已证明 **工程链路**（GEHI、geometry、离线扫描、配额、审计、结构化交付）可用。
- CT-11 已证明 **当前外部尺子与生产路径不对齐**，日期准确度未过关；失败数字是 Codex-reviewed QA，不是已测生产 accuracy。
- 全城底图下载耗时长、占磁盘、与方法探索 **无数据依赖**；等科学轨完成再下载会空转日历并放大沉没成本风险。
- 因此：**工程轨先跑满 imagery readiness；科学轨并行找可辩护的日期方法；两者在 scorer GO 与发布 GO 两次决策点汇合。**

---

## 1. 目标与交付口径

### 1.1 工程目标（Leg-E，近端）

把 Cape Town 上游 inventory 的 **111,801 个安装 seed** 做成：

- 冻结 scope manifest 与 grid roster；
- 全城 GEHI catalog + 2019+（及规则内 reference）chip 就绪；
- 可审计的 coverage / failure 清单；
- 容量与 wave 切分可执行；
- **随时可被未来锁定的 scorer 消费**，但当前默认不消费。

### 1.2 科学目标（Leg-S，并行）

在可信 reference 上验证 **first-visible-appearance interval** 方法，并探索：

- 时序识别改进（观测模型、解码、端点稳定）；
- 配准与多目标池化；
- 学生模型 / 蒸馏与自托管 scorer；
- 更稳的评审仪器（避免拼贴 LMM 通病）。

### 1.3 产品语义（发布时仍硬约束）

> 在上游 Cape Town PV inventory 为真的条件下，基于可用 GEHI 历史影像
> cadence 的 first-visible-appearance interval（首次可见外观区间）。

不声称物理安装日、并网日、精确月份；不重新跑自由影像 PV 检测。

### 1.4 可复现性（两条定义，不得混用）

| 名称 | 定义 | 何时要 |
|---|---|---|
| **严格离线复现** | 冻结 chip + 冻结 verdict → 禁止网络下解码/交付字节一致 | scorer GO 后、发布前 |
| **Hosted 重复性** | 同 chip 重打 Gemini；frame/interval agreement（对标 Joburg ceiling ~0.77） | 生产期 sentinel；**不是** accuracy |

---

## 2. 当前事实与判定

### 2.1 Top-52 已证明（工程基线）

| 事实 | 值 | 来源 |
|---|---:|---|
| Anchors | 21,453（占全城 19.19%） | deliverable gates |
| 有历史 | 21,447 / 99.972% | n_obs>0；catalog audit |
| 2019+ 候选 | 1,178,227；z19 99.612% | CT-05 audit |
| 终态构成 | 已定 13,908；普查期内 5,157；左删失 2,238；未定日 150 | date_status |
| A24 / A48 | 19,729 / 1,724 | deliverable |
| 区间宽度（已定） | 中位 37 天；p75 91；≤180 天 84.0% | 生产自报宽度，非 accuracy |
| 结构门禁 | 7/7 PASS；0 未解决 operational failure | gates.json |
| Canary 调用密度 | A24 2.557；A48 2.694 HTTP/anchor | gate_retry2 |

→ GEHI 获取、target-centered geometry、配额断路器、恢复与结构化交付可作为 **全城工程基线**。

### 2.2 Top-52 没有证明（科学）

纠正后 CT-11（Codex 原生拼贴盲审，500+100）：

| 指标 | 实测 | 旧门槛 | 判定 |
|---|---:|---:|---|
| exact-bracket Wilson 下界 | 17.270% | ≥70% | FAIL |
| inventory-standardized exact | 20.827% | ≥75% | FAIL |
| Codex 复评状态一致 | 68.000% | ≥80% | FAIL |
| 复评 Wilson 下界 | 58.337% | ≥70% | FAIL |

**读法（强制）**：

- 点估计 exact-bracket 是 **94/453 ≈ 20.8%**；17.27% 是 Wilson **下界**，不是另一套一致率。
- 这是 **Codex 拼贴条带评审 vs 生产（多图 part 逐帧 batch + 解码）**，路径不对齐。
- **不能**写成“生产准确率 20%”，也不能写成“生产一定对”。
- 旧 70%/75%/80% 门槛 **不自动继承** 到新 reference 仪器；科学轨须 re-prereg。

Joburg 对照：Gemini `gemini-3.1-flash-lite` 独立 rep 区间 ceiling ≈ **0.7708**（&lt;40 m² ≈ 0.7529）。这是 hosted 自洽上限，不是 Cape Town 日期真值。

### 2.3 全城相对 Top-52 的分布差异

| 范围 | Anchor | 非空 grid | A24 | A48 |
|---|---:|---:|---:|---:|
| Top-52 | 21,453 | 52 | 19,729 | 1,724（8.0%） |
| 全城 | 111,801 | 1,301 | 94,891 | 16,910（15.1%） |
| Top-52 以外 | 90,348 | 1,249 | 75,162 | 15,186（16.8%） |

全城另有约 44 个边长 &gt;96 m 的 footprint exception。工程预取必须覆盖；科学验收不能只报 Top-52 密度。

### 2.4 资源上界（规划用，非最终预算）

| 资源 | Top-52 | 全城 ×5.211 上界 |
|---|---:|---:|
| 2019+ candidates | 1,178,227 | ≈ 6.14M |
| Scorer HTTP（若全打） | ≈ 55,100 | ≈ 290,000 |
| Chip 磁盘 | 口径待重测 | **110–191 GiB 量级**（禁止选小） |

当前 `/home` 余量约 **94 GiB** 量级，**低于** 全城芯片上界。工程轨必须把 **磁盘方案** 与下载波次绑定，不能“先下完再说”。

---

## 3. Leg-E — 工程轨（优先，保持不断）

> 目标：全城 **offline imagery readiness**。  
> 允许：catalog、download、chip QA、manifest、容量测量、wave 切分演练。  
> 禁止：生产 scorer、写 interval/install_year 交付物、打开密封确认集、宣称准确度。

### CT-CW-00 — Owner 决议与 scope freeze（P0，立即）

**工作**

1. 冻结 source inventory、canonical grid（2,083 cell）、1,301 非空 source grid、111,801 `source_feature_id`。
2. **书面授权 outcome-blind 全城预取**（补录到 tracker / `OWNER_DECISIONS.md`）：范围、开始时间、执行者、run root、预算、停止条件；并写明 **不构成 CT-11 通过、不得启动全城 scorer**。
3. 零 seed 的 782 个 canonical grid：产品表不造假 anchor；覆盖报告保留零计数。
4. 工作区代码/文档提交纪律：改变工程状态的脚本与 RUN 同步进 git（与项目 DoD 一致）。

**产物**

- `OWNER_DECISIONS.md`（含 prefetch 授权）
- `ct_citywide_scope_manifest.csv`
- `ct_citywide_grid_roster.csv`
- `scope_artifacts.sha256`

**退出门**

- 111,801 行 ID 双射；决议与真实下载状态一致。

### CT-CW-05 — 全城 geometry、catalog、chip 下载（主执行包）

**Geometry**

- region=`cape_town`，metric CRS=`EPSG:32734`；
- anchor ↔ `source_feature_id` 双射；
- 常规 target-centered 96 m；&gt;96 m footprint 用版本化 exception；
- `source_grid` / `centroid_grid` / halo 分列；A24/A48/exception 进入 cache key。

**Imagery**

- GEHI 唯一 provider；
- z19 bbox-complete 主路径，z18 whole-picture fallback；
- z20/z21 仅 review upgrade；
- TM/Wayback 分源，最终 `(anchor_id, capture_date)` 去重；
- cutoff=`2025-01-31`；之后最多 3 个日期 `reference_only=true`；
- 缺 catalog / 缺文件 / download fail / decode fail / pixel-uninformative / bbox-incomplete / no-history **分状态**，禁止改写成 absent。

**下载执行（保持不断）**

- 按 grid 或密度带切 **download waves**（与未来 scorer wave 可同切分，但生命周期独立）；
- skip-existing / content-hash 复用 Top-52 已合格 chip；
- 每波结束写：`catalog_summary`、`chip_qa_manifest`、`coverage_failures.json`、`.done` sentinel；
- **只写** raw log、catalog、candidate、chip、QA；**不写** verdict / scan_state / interval。

**磁盘与 stop（硬）**

| 条件 | 动作 |
|---|---|
| 实测 projected peak 芯片占用 &gt; 可用空间 − 20% 余量 | **停止新增下载**，先归档/扩容/分层 |
| 单波 failure rate 异常或 provider 系统性故障 | 暂停该 lane，保留证据，不删锁 |
| 有人提议“先打分再下完” | 拒绝：违反本计划 |

正式预算必须用 **2019+ admitted-only、去 superseded** 的真实字节重测；禁止在 110 vs 191 GiB 中挑小的报喜。

**退出门（imagery ready）**

- scope 内 anchor 均有 catalog outcome；
- 可下载候选的 chip path+SHA256+decode+zoom QA 通过率达预注册下限（建议 ≥99% admitted chips；其余显式 failure class）；
- no-history / exception roster 冻结；
- `imagery_readiness_report.json` + hashes。

### CT-CW-05b — Wave 切分与 runtime lock 骨架（不启动 scorer）

- 按 ≤约 3,500 anchors/wave 预切约 32 个 **download/score-compatible** wave（grid 完整、密度与地理交错）；
- 写出 `wave_plan.json`、每波 manifest、空的 runtime lock 模板（模型/prompt 位保持 `UNSET` 直至 scorer GO）；
- 目的：下载与未来打分共用分片，避免二次切分。

### CT-CW-06E — 工程 canary（无日期验收）

在 imagery 子集上做 **下载与离线解码空转**（若已有冻结 Top-52 verdict 可做 offline replay 演练）：

- 12 个 grid 量级即可（密度带 + A48 + exception + no-history）；
- 指标：吞吐、磁盘、失败类、hash 稳定；
- **不**要求 exact-bracket 科学门。

### CT-CW-07/08 工程侧预留

仅当 **owner scorer GO** 后启用（见 §5）。在此之前目录与脚本可备好，默认不跑。

---

## 4. Leg-S — 科学轨（并行探索，不挡下载）

> 目标：弄清“谁错了”并锁定可辩护的日期方法。  
> 与 Leg-E **无阻塞依赖**；产出进入 method lock / 论文口径，**不**自动启动全城 scorer。

### CT-CW-01 — 人工参考校准（科学关键路径）

已冻结包：`ct11_human_calibration_v1_20260804/`（失败 500 中的 100；含 32 个 Codex 冲突；一人一图）。

**工作**

- 两名 human 独立盲审 → 分别冻结哈希 → 仅分歧行裁决 → `FINAL_REFERENCE_LOCK.json`。
- 报告两人 state/interval agreement（Wilson）；若 state agreement 过低（建议 PI 预注册下限，如 &lt;0.75），标签仅作定性诊断，**不得**单独锁生产方法。
- 降级：若只有一人，须预注册“一人 + 与生产完全独立的第二模型盲读”合约；禁止静默停摆。

**退出**：参考是否足以指导分叉（见下），由 PI 签字。

### CT-CW-01b — 校准后分叉（强制，禁止默认大改）

| 分叉 | 判据（定性+预注册表） | 工程轨影响 | 科学下一动 |
|---|---|---|---|
| **S1 尺子问题** | Human ≈ 生产，≠ Codex | 下载继续 | 重做 reference 仪器；**不**大改生产 |
| **S2 生产偏差** | Human ≈ 可靠 reference，≠ 生产 | 下载继续 | L0/L1 方法修复 |
| **S3 任务难/帧不足** | Human 自洽也低或三者乱 | 下载继续 | 降声称 / 改产品粒度（如 year-bin） |

### CT-CW-01c — 仪器消融探针（推荐，小配额）

旧失败集 40–100 anchors，**禁止**碰密封 holdout：

| Arm | 输入 | 输出 |
|---|---|---|
| A | 现状 collage 拼贴 | 直接区间/状态 |
| B | 多图 part（不拼贴），仍直接交区间 | 区间/状态 |
| C | 多图 part 逐帧 schema + 与生产同构解码器 | 区间 |

比较：自复现、与 human（若已有）一致、与生产 exact/year。用于决定评审仪器，不是全城验收。

### CT-CW-02 — 方法探索（L0 / L1，可并行多条）

开发 **仅** 使用：旧失败 CT-11 500、人工校准 100、以及其它显式标注的探索集。密封 500+100 **禁止**。

**L0 — 最小可锁（优先试）**

- 保持生产 batch 多图 part + 逐帧三态（present/absent/uninformative）；
- uninformative 不得变 absent；
- 冻结解码器与 prompt hash；
- 可选：关键端点双读（仅哨兵，不上全城默认）。

**L1 — 增强（有证据再上）**

- 定位/身份门；双尺度 crop；序列 flank 整段判读；third-read 队列；早期历史（仅首帧已 present）定向扩展；配准与多目标池化；学生模型蒸馏等。

**探索主题白名单（鼓励并行，各写独立 RUN）**

1. 时序识别改进（观测 + 解码）  
2. 跨日期配准 / 目标池化  
3. 学生模型（DINOv3 等）与 Gemini 蒸馏，** fidelity 对标 teacher ceiling，不冒充真值 accuracy**  
4. 评审仪器改造（分图、human 协议）  
5. year-bin / containment 等更贴经济学的指标

**关于旧 KILL 数字**（recovery 37.5%、ref-anchored 30%、provenance 52%、DINO 56%）：

- 否决仅相对于 **当时的 Codex reference**；
- reference 更换后必须重评估；
- **禁止**把旧 KILL 自动当作永久死亡证明。

**晋级**：在看校准结果前，PI 填写预注册表（对 **human 或新仪器** 的 exact/year/containment 阈值）。未填表不得声称 method locked。

### CT-CW-03 — Top-52 密封确认（一次性，科学轨）

- 包：`ct11_disjoint_holdout_v1_20260804`（500+100）。
- 仅 `remediation_locked=true` 且 reference 仪器协议已写明后开封。
- **门槛必须 re-prereg**（默认不继承 CT-11 Codex 的 70/75/80）：
  - 建议 headline：**year-bin** 与/或 **interval containment** + Wilson；
  - exact-bracket 作诊断，或仅当 reference 与生产 **同构接口** 时作硬门；
  - reference 自洽 / 生产 Gemini rep↔rep 分列报告。
- 失败 → 不发布、可继续探索；**不**回滚已下载芯片；**不**自动停 Leg-E 预取（除非 owner 另令）。

### CT-CW-04 — 非 Top-52 迁移确认（分期，非默认 1000 必跑满）

- **可早冻结** 抽样框与身份哈希（outcome-blind）。
- **评估 n** 默认两阶段：先 **300–500 unique + 20% repeat**；仅当迁移可疑再加抽至上限 1,000+200。
- 分层预注册 2–3 个主轴即可（密度带、A24/A48、边缘/z18），避免交叉层爆炸。
- 通过与否影响 **发布 GO** 与 **全城 scorer GO**，不影响已完成下载的合法性。

---

## 5. 汇合点 — Scorer GO 与发布 GO

### 5.1 全城 scorer GO（owner 书面）

**最低建议条件**（PI 可收紧，不可在看结果后放松）：

1. Leg-E：`imagery_readiness` PASS，磁盘余量 ≥20%；  
2. Leg-S：method lock 完成，且 **至少** Top-52 确认路径已定义并（若要求）通过；  
3. 运行：quota 窗口、wave plan、runtime lock（模型 identity）就绪；  
4. 明确本次是 **full city** 还是 **limited canary waves only**。

未 GO 前：任何“试打几万 anchor”须单独 owner 票，写入 run root，默认销毁或隔离，不得进交付物。

### 5.2 生产执行（仅 GO 后）— CT-CW-06S / 07 / 08

**Canary（科学+工程）**

- 12–24 grid 异质 canary：catalog→chip→scorer→decoder→deliverable；
- 记录 calls/anchor、429、identity、failure mode；
- 科学指标对照 method lock 预期区间（不是旧 Codex 门槛自动套用）。

**Waves**

- ~32 immutable score waves，与下载分片对齐；
- 每波：manifest 校验、fresh identity canary、offline chips only、QPS+breaker、append-only audit/ledger、retry 新 `retry_run_id`；
- **审计减负**：Wave-0 / 每 window 首波全量递归审计；其余波 **ledger+identity+抽样**；异常升级全量。
- 不接受 Top-52 类 ledger-only gap 装成成功。

**交付**

- Anchor：latest_absent / earliest_present、MAP 与 90% CI、删失概率、coverage class、provenance；
- City：posterior year mass、grid summary；**禁止 midpoint 主通道**；
- `reproducibility_report`：至少 **1 次全量 offline rebuild + 1 个 wave 双跑等价**（全城两次完整 replay 为理想项，非阻塞默认）；
- Hosted sentinel：跨层固定队列重打分，对标 Joburg ceiling 漂移带。

### 5.3 发布 GO

- 预注册确认集通过；reference 与 product 指标达标；
- 无 unresolved operational failure / identity drift / post-cutoff 误用；
- README 仅 first-visible / Codex-or-human-reviewed 等诚实措辞；
- 不得使用 physical installation date、citywide accuracy 等超证表述。

---

## 6. 准确度与指标命名

| 名称 | 含义 | 用途 |
|---|---|---|
| offline replay agreement | 冻结输入下交付 hash 一致 | 工程复现 |
| Gemini rep↔rep | 同 chip 重打生产 scorer | 漂移 / ceiling |
| reference–product agreement | 产品区间 vs 锁定 reference | **唯一可称 accuracy 的通道** |
| Codex collage agreement | 历史 CT-11 | **仅诊断，退役为验收主尺** |

报告时同时给 inventory-weighted 与 unweighted 分层；false-early / false-late 分开；year-bin 与 exact 分列。旧失败 500 开发数字不得进最终 headline。

---

## 7. 任务表与依赖

| ID | 任务 | 轨 | 状态 | 依赖 | 阻塞 scorer? | 阻塞下载? |
|---|---|---|---|---|---|---|
| CT-CW-00 | Owner 决议 + scope + **预取授权** | E | **立即做** | — | 否* | **是**（无授权则停） |
| CT-CW-05 | 全城 catalog/chip 下载+QA | E | **主路径执行** | 00 | 否 | — |
| CT-CW-05b | Wave plan / lock 骨架 | E | 随 05 | 00 | 否 | 否 |
| CT-CW-06E | 工程 canary | E | 随 05 | 05 子集 | 否 | 否 |
| CT-CW-01 | 双人人工校准 | S | READY | — | 间接 | **否** |
| CT-CW-01b | 校准分叉 | S | 待 01 | 01 | 间接 | **否** |
| CT-CW-01c | 仪器消融 | S | 推荐 | 可选 01 | 否 | **否** |
| CT-CW-02 | L0/L1 与探索项目 | S | 并行 | 01b 指导 | 间接 | **否** |
| CT-CW-03 | Top-52 密封确认 | S | SEALED | method lock | **是**（默认） | **否** |
| CT-CW-04 | 非 Top-52 迁移确认 | S | 可冻样本 | 03 或预注册弱门 | 默认是 | **否** |
| CT-CW-06S/07 | Scorer canary + waves | E∩S | HOLD | **scorer GO** | — | 否 |
| CT-CW-08 | 交付与发布 | E∩S | HOLD | 07 + 发布 GO | — | 否 |

\*无 scope 冻结也可小规模探针下载，但全城 111,801 必须以 00 为准。

```text
Leg-E:  00 ──► 05 ──► 05b/06E ──► (wait scorer GO) ──► 06S/07 ──► 08
Leg-S:  01 ──► 01b ──► 01c/02 ──► 03 ──► 04 ──► 发布 GO
                 └── 多条探索 RUN 并行 ─────────────┘
```

---

## 8. 近端执行顺序（工程优先）

| 顺序 | 动作 | 轨 | 完成标志 |
|---:|---|---|---|
| 1 | 补录 prefetch owner 决议 + scope freeze | E | `OWNER_DECISIONS.md` |
| 2 | 实测 CT chip 字节口径 + 磁盘方案（扩容/归档/分盘） | E | 预算表；余量≥20% 或已执行归档 |
| 3 | **启动/继续全城 download waves** | E | 每波 `.done` + coverage |
| 4 | 并行：派发两人（或降级）人工校准 | S | 两份冻结结果 |
| 5 | 并行：仪器消融 40–100 | S | 短 DATA memo |
| 6 | 校准分叉后开 L0/探索项目 | S | 各 RUN + 是否 method lock |
| 7 | imagery ready 后 **停在 scorer 前** 等 owner | E | readiness report |
| 8 | 科学验收通过后 scorer GO → canary → waves → 发布 | 汇合 | 发布 GO |

工期：下载按配额与磁盘实测；**不**用科学轨日历绑架下载，也 **不**用下载进度绑架发布。

---

## 9. PI 决策清单

1. **确认全城 outcome-blind 预取授权**（范围、预算、stop、与 CT-11 HOLD 脱钩表述）。  
2. 磁盘：扩容 / 分层归档 / 外置路径 — 三选一或组合，写进 00。  
3. 指定第二 human reviewer 或批准降级第二读者。  
4. 预注册：human 自洽下限；method 晋级指标；CT-CW-03 新门槛（默认不继承 70/75/80）。  
5. 全城 scorer 是否要求 CT-CW-04 先过，或允许 “Top-52 确认 + 有限 canary” 分阶段。  
6. 发布主通道确认：interval / posterior mass，非单一 `install_date`。  
7. 探索项目优先级（时序 / 配准池化 / 学生模型）与配额上限，避免与生产预取抢磁盘到危险区。

---

## 10. 发布与运行红线

**永久红线**

- 用预取完成率或 scorer 完成率冒充准确度；  
- 打开或调参污染密封 holdout；  
- 发布 physical install / commissioning / citywide accuracy 等超证措辞；  
- midpoint 硬分配作主统计通道；  
- ledger/audit 对不齐却交付。

**工程轨红线**

- 磁盘无 20% 余量仍继续新增下载；  
- 预取进程写 interval/verdict；  
- 无 scorer GO 的全城打分。

**科学轨红线**

- 用 Codex 拼贴一致率当生产 accuracy；  
- 未 re-prereg 继承旧门槛；  
- 探索集与确认集身份泄漏。

---

## 11. 过度工程的明确裁剪（相对初稿）

| 初稿 | 本版 |
|---|---|
| 科学门禁阻塞一切全城工作 | **仅阻塞 scorer/发布；下载 GO** |
| CT-CW-02 六件套一次上齐 | **L0 优先，L1 有证据再上** |
| 非 Top-52 固定 1000+200 必跑 | **样本可冻，评估 300–500 起** |
| 每波同等科研级递归审计 | **首波/窗口重审 + 其余轻量** |
| 全城两次完整 offline replay | **全量 rebuild + 单 wave 双跑默认** |
| 默认双读三读上全城 | **哨兵或 L1** |
| Codex 70/75/80 原样进全城 | **退役为历史；新仪器 re-prereg** |

---

## 12. 证据来源

- [`RUN-cape-town-backdating-plan-2026-07-24.md`](RUN-cape-town-backdating-plan-2026-07-24.md)  
- [`RUN-cape-town-backdating-tracker-2026-07-24.md`](RUN-cape-town-backdating-tracker-2026-07-24.md)  
- [`RUN-cape-town-ct11-instrument-diagnosis-2026-08-03.md`](RUN-cape-town-ct11-instrument-diagnosis-2026-08-03.md)  
- [`RUN-cape-town-ct11-corrected-qa-prereg-2026-08-03.md`](RUN-cape-town-ct11-corrected-qa-prereg-2026-08-03.md)  
- [`RUN-cape-town-ct11-corrected-qa-2026-08-03.md`](RUN-cape-town-ct11-corrected-qa-2026-08-03.md)  
- [`RUN-cape-town-ct11-human-calibration-2026-08-04.md`](RUN-cape-town-ct11-human-calibration-2026-08-04.md)  
- [`BRIEF-cape-town-status-2026-08-12.html`](BRIEF-cape-town-status-2026-08-12.html)  
- [`../install_date_optimization_v2_prd.md`](../install_date_optimization_v2_prd.md)  
- Joburg Gemini ceiling：`docs/dinov3_scorer/DATA-run3native-repeat-ceiling-2026-07-19.md`

---

## 13. Changes from draft-v1（给 fable-5）

1. **工程优先双轨**：Leg-E 预取 GO；Leg-S 并行探索；scorer/发布仍 HOLD。  
2. **CT-11 叙事钉死**：Codex 拼贴 QA ≠ 生产 accuracy；路径不对齐写明。  
3. **校准三分支** + 仪器消融探针；旧 KILL 相对 Codex 可重开。  
4. **CT-CW-02 拆 L0/L1**；探索主题（时序/配准池化/学生模型）白名单并行。  
5. **确认集门槛 re-prereg**；非 Top-52 分期；审计与 replay 减负。  
6. **磁盘硬 stop**；prefetch 授权与 CT-11 脱钩为 P0。  
7. 删除“科学未过则不得启动全城下载”的隐含耦合。

---

本文件为全城扩展 **DRAFT-v2 plan-of-record 候选**。fable-5 终审通过并经 owner 签字后升格；此后变更须带日期追加，不得静默改门禁或用预取进度覆盖科学失败事实。
