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
