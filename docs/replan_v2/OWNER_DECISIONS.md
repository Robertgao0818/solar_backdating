# OWNER_DECISIONS — 带日期的 owner 决议记录

本文件由 [REVIEW-citywide-plan-split-2026-08-12](REVIEW-citywide-plan-split-2026-08-12.md)
§1 要求设立：治理级决定必须在此留带日期的记录，不得静默生效。
每条 = 决议 + 日期 + 依据链接。追加式,不回改历史条目。

---

## D1 — 三分支拆分转正（论文主线范围） · 2026-08-13

**决议**：Leg-E / Leg-S / Leg-R 三分支拆分
（[REVIEW-citywide-plan-split-2026-08-12](REVIEW-citywide-plan-split-2026-08-12.md)）
签为 plan-of-record；多模型面板协议
（[MULTIMODEL_VISUAL_REVIEW_PROTOCOL](MULTIMODEL_VISUAL_REVIEW_PROTOCOL.md)）
转为正式协议（替代双人人工校准）；Leg-S 分支优先级按
[RUN-paper-program-sequencing-2026-08-13](RUN-paper-program-sequencing-2026-08-13.md)
执行（S5/R4 修正 + S3 面板 + Leg-R R1 并行先行，S2 次之，
S1→S6→S4 排队）。

**范围限定**：split review §3 的 Leg-E 专属项（全城预取授权 +
CT-11 政策覆盖记录、磁盘方案、CT-CW-03 新门槛 prereg）**仍未签**，
留待 Leg-E 自己的 E0。

**依据**：owner 于 2026-08-13 会话确认（brief
[BRIEF-student-vs-registration-2026-08-13](../briefs/BRIEF-student-vs-registration-2026-08-13.md)
三轮问答 + 方案批准）。

## D2 — R4 失败分支预决策 · 2026-08-13

**决议**：r4_v2（H1 修正后重训）若再 FAIL，授权**一轮** LoRA 级
二次投入 —— 即部分解除 2026-07-19 的 "LoRA/backbone veto"：
LoRA/PEFT 解锁，**骨干更换与全量微调仍 veto**。二次投入须先写
独立 prereg（新 GO/KILL bar 不得继承性放松），再败则学生线执行
既有 shelve-to-CapeTown 规则，Leg-R 自托管线停留在 v0 如实报低。

**理由**（在 r4_v2 结果之前写死，防结果诱导）：owner 署名回报 =
模型类论文，端到端开放学生是论文成立前提（brief §7）；但纪律
边界保持 —— 每一轮投入都有自己的 prereg 与止损。

## D3 — r4_v2 唯一修正假设 = H1 · 2026-08-13

**决议**：校准 gate 的单次假设驱动修正选定
**H1：质量头监督信号修复**（负例 410/13,797 = 3.0% 的稀释 +
AUROC 0.57–0.59 近随机是失败的最可疑机制；见
[DATA-r4-v1-result-2026-08-13](../dinov3_scorer/DATA-r4-v1-result-2026-08-13.md) §3.3）。
H2（改接受规则）落选原因：有"变相降 bar"审查风险；
H3（类不平衡重加权）视为 H1 实现内允许的组成部分而非独立假设，
但若 H1 的实现仅剩重加权而无监督信号重构，须在 r4_v2 prereg 中
如实声明。判定合格线 0.7542 / 0.7337 / 0.80 **不动**。

**消耗**：本决议用掉校准 gate 的唯一修正名额；r4_v2 再败即触发 D2。

## D4 — A3 dump 臂 chip 接受为打分真源 · 2026-08-19

**决议**：接受 A3 `dump` 臂（GEHI 原生瓦片 + 无损拼接 + pad + 切开
+ per-chip JPEG q=95）产出的 chip 作为**打分真源**——即「与逐张
`download` 差 ~2 DN（两格中位 1.93 DN，24/24 ≤5）、QA 全过」的
批量切开 chip 可用于 PresenceScorer / Gemini 打分，与逐张下载 chip
等效。A3 的机制判定随之从「dump 臂 GO，待 owner 认」转为
**owner 已认**。

**范围限定**：本决议只签**真源等效性**，不是生产开关——
`ENABLE_REGION_BATCH` **仍 false**，生产路径（manifest 合成进
per-anchor 行、全城 wave plan 接线）须 owner 单独签；Leg-E 的
E0/E6 token 状态不受本决议影响。档案级像素复现场景（非打分）
仍以逐张 `download` 为真源（HANDOFF §4 发现 7）。

**依据**：owner 于 2026-08-19 会话确认；数字真源
[RUN-a3-region-batch-dump-arm-2026-08-18](RUN-a3-region-batch-dump-arm-2026-08-18.md)
§3（预注册 7 条 bar 全过，含 `download` 臂没过的「90% chip
MAE≤5」）+ `~/zasolar_data/geid_temporal/a3_region_batch_pilot_20260818/report/dump_summary.json`。

## D5 — A3 dump 臂签为生产路径（替换全部逐张 + 整格 pilot + 10 格 canary） · 2026-08-19

**决议**：owner 对 A3 生产路径的三条指令——

1. **替换所有的**：dump 臂（并窗 dump → 无损拼 → 切开 → manifest 合成）
   签为全城下载的**唯一生产路径**，替换逐张 `download`；逐张路径仅保留为
   簇失败时的 fallback 与档案级像素真源。
2. **整格 pilot**：加一个整格真实下载 pilot（~341 锚 / ~270 瓦一簇），
   验证生产尺度下 dump 的墙钟 / 403 行为 / 拼接切开 QA。
3. **先跑 10 grid 然后再放量**：canary 10 格端到端跑通出报告，
   owner 看过报告点头后才放量。

**范围限定**：canary 限 TM z19（占 wave_01 候选 82%）；Wayback/z18 走同
一代码路径后续接入。**放量授权不在本决议内**——`ENABLE_REGION_BATCH=true`
（全城放量）需 owner 在 canary 报告后明确放行；Leg-E 的 E0/E6 token 节奏
独立于本决议。簇失败语义：失败簇成员回退逐张下载，失败类如实进 QA，
永不转为 absence 观测。

**依据**：owner 于 2026-08-19 会话确认（接续 D4 的「继续推进」）；
机制真源 [RUN-a3-fullcell-coldcell-probe-2026-08-19](RUN-a3-fullcell-coldcell-probe-2026-08-19.md)
（整格 availability 57 日期、冷格 17.2×、manifest 过生产 QA）。

## D6 — `ENABLE_REGION_BATCH=true` 放量授权 · 2026-08-26

**决议**：owner 已读
[RUN-a3-batch-lane-canary-2026-08-19](RUN-a3-batch-lane-canary-2026-08-19.md)
（整格 pilot QA 341/341；10 格 canary QA 14,795/14,796），放行
`ENABLE_REGION_BATCH=true`。含义仅限：`run_a3_batch_lane.py` 的 plan
阶段允许覆盖 **>10 格**（D5 留下的唯一放量闸门打开）。

**范围限定**：

- 不签 E0/E6。`ALLOW_CITYWIDE_DOWNLOAD` / `ALLOW_GEMINI_WAVES` 仍 false。
- 不自动改 `launch-wave`。全城编排器此刻仍起逐张
  `run_ct05_download_lane.sh`；接到 A3 dump lane 是后续接线，不是本决议。
- 运行时仍须在进程环境里出现字面值 `ENABLE_REGION_BATCH=true`
  （代码读 env，不读本文件）。本条是治理授权，不是 systemd 单元。
- Gemini 网关放哪、grok bot（tailnet `bot`，128G 盘）承担编排还是打分，
  不在本决议内，另定拓扑后再动。

**依据**：owner 于 2026-08-26 会话确认「可以，接受=TRUE」。

## D7 — 全城执行拓扑：rog 过夜开着，Gemini 留在 rog，bot 只编排 · 2026-08-26

**决议**：rog **过夜保持开机**。由此锁定角色，不再把 Gemini 网关或
chip 热集迁到 grok bot（128G 盘）。

| 主机 | 角色 | 不做什么 |
|---|---|---|
| **rog**（本机，250G，Sub2API 已在 `0.0.0.0:8080`） | Gemini 网关 + 打分进程 + ≤2 波 chip 热集 + merge/archive 控制面 + A3 dump 主下载 | 不把 64G Top-52 chip 当长期热集（B1 已 BACKUP_OK，待 release） |
| **koko** | 轻载下载 egress（最多 1 个 GEHI 进程） | 不跑 40-worker 打分；不存归档 |
| **bot**（tailnet `bot`，128G） | 编排 agent：SSH 发命令、盯 tmux/日志 | **不**跑 Sub2API、不存 chip、不跑 GEHI。128G 只留仓库与日志 |
| **Dropbox** | 唯一 durable archive | — |

**范围限定**：不签 E0/E6。全城仍 fail-closed。bot 当前 tailnet offline
（2026-08-26 实测，上次出现约 6 天前），在它上线之前编排仍由 rog 本机
会话承担。`launch-wave` 接到 A3 dump 仍是后续接线。

**依据**：owner 于 2026-08-26 会话确认「rog能过夜」。

## D9 — grok bot 跑在 cursor；cursor 可承担半波 A3 dump · 2026-08-26

**决议**：执行 agent 的宿主是 tailnet **`cursor`**（`100.103.225.76`），
不是已离线的 `bot`（`100.86.6.50`）。cursor 上下行带宽高，授权它做
**A3 dump 下载 egress（按格子对半分波）**，与 rog 并行。

仍锁定：Gemini 网关、打分、Dropbox 归档、≤2 波热集合集在 **rog**。
cursor 按 128G 纪律：只留当前波属于自己的那一半 chip；禁止 Top-52
副本；禁止把 dump 中间 mosaic 留盘（lane 默认每簇删除）。chip 完成后
**从 cursor rsync 推到 rog**，不依赖 cursor 开 22 端口给 rog。

**范围限定**：不签 E0/E6。全城仍 fail-closed。2026-08-26 从 rog 测
cursor TCP 22 = connection refused，故半波下载由 cursor 本机 agent
启动，而不是 `launch-wave` SSH 进去。koko 轻载合同不变。

**依据**：owner 于 2026-08-26 会话确认 bot 机器名 = cursor，并要求
「下载可以分一部分给 bot，它的上下行带宽都很高」。交接 prompt：
[HANDOFF-ct-citywide-cursor-bot-2026-08-26](HANDOFF-ct-citywide-cursor-bot-2026-08-26.md)。

## D10 — E0 全城预取 + E6 打分放行 · 2026-08-26

**决议**：授权 Cape Town 全城 outcome-blind 预取，并授权各波 chip
staged 之后用本机 Sub2API + `gemini-3.1-flash-lite` 打分。交付口径 =
内部 release，不构成 CT-11 翻盘，不得写入对外准确度声称。
明示覆盖 CT-11「全城下载不得启动」。

范围：non-Top-52 90,348 锚 / 27 波 /
`cape_town_citywide_backdating_v1_20260812`。
磁盘：Dropbox 归档 + rog 热集 ≤2 波；停止条件 = 流水线 §5/§7 水线。
拓扑：D7（rog 过夜，Gemini 留在 rog）+ D9（cursor 半波 A3 dump）。
下载路径：A3 dump（D5/D6）。

owner 于 2026-08-26 会话确认「帮我签完E0+E6,我授权你，日期写今天」。

E0_PREFETCH_DISK_SIGNED=2026-08-26
E6_SCORER_GO=2026-08-26

## D11 — 合作者附录：P1-A 定案、SSEG 口径 §B 冻结 · 2026-10-13

**决议**：
1. 附录按 P1-A 改写，即"普查检测（CoCT 2025Jan）+ 卫星回溯 + 快照日期"，不做逐年航拍检测。理由：backdating 主要靠卫星图。
2. [`PLAN-cv-validation-appendix-tasklist-2026-10-13.md`](PLAN-cv-validation-appendix-tasklist-2026-10-13.md) §B 的 SSEG 对比口径冻结，方括号里的建议值全部采用（τ=90 天、航拍窗口 C+30 天、对照 50 个、双人判读 20%、bootstrap 2,000 次、停止阈值上界 ≤10%、改进 <2 个百分点、宽度和未定日比例变差 ≤20%）。之后不再改口径。
3. 附录图交付到独立目录 `dropbox:RA_Solar/appendix/`；D2 先出预览版，发表许可以后再确认。
4. 执行交给 Grok Build，验收交给 Opus 5.5。

执行说明：[`PLAN-cv-appendix-figures-execution-2026-10-13.md`](PLAN-cv-appendix-figures-execution-2026-10-13.md)。

owner 于 2026-10-13 会话确认："拍板两条都确定，因为backdating主要还是靠卫星图"。
