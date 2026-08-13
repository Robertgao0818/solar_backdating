# RUN — 论文主线资源盘点与波次方案

日期：2026-08-13 · Status: **ACTIVE — Wave 1 执行中**
（Wave 0 已签 D1/D2/D3。Wave 1：1-A H1 prereg+实现已落、训练待干净
commit；1-B 缺 GPT/Grok 通道未派发，见
[DATA-s3-api-availability-2026-08-13](DATA-s3-api-availability-2026-08-13.md)；
1-C 同机 decode+deliverable 双跑 GREEN，见
[DATA-legR-r1-day1-2026-08-13](DATA-legR-r1-day1-2026-08-13.md)）
上游决策依据：[`../briefs/BRIEF-student-vs-registration-2026-08-13.md`](../briefs/BRIEF-student-vs-registration-2026-08-13.md)
（结论：owner 署名回报 = 模型/数据集论文；顺序 = V1+R4 → S3 并列 →
S2 → S4 最后）
分支框架：[REVIEW-citywide-plan-split-2026-08-12](REVIEW-citywide-plan-split-2026-08-12.md)
（Leg-E / Leg-S / Leg-R + 多模型面板协议）

## 0. 资源盘点（2026-08-13 实测）

### 0.1 算力与磁盘

| 资源 | 实测 | 含义 |
|---|---|---|
| GPU | RTX 4070 Laptop 8GB，空闲（41 MiB 占用） | R4 级训练本地可跑，无需 RunPod |
| 磁盘 `/home` | 250G 总 / 93G 余（63% 已用） | 论文主线各项都是 MB–GB 级，不受限；**Leg-E 全城 chips 上界 110–191 GiB 超余量**，E0 磁盘决策只挡 Leg-E |
| tmux 占用 | 6 条 `ct_cw_cat_rog_*` catalog lane 在跑（8-13 00:22 起） | Leg-E 处于 catalog 阶段，不占 GPU，不与论文主线争资源 |

### 0.2 重大发现：R4 v1 已执行完毕，verdict = CALIBRATION_FAILED

**两个 tracker 里"training not started"已 stale。**
`~/zasolar_data/geid_temporal/run3_native_line_2026-07/r4_v1/`
（RUN_LOCK `status: R4_COMPLETE`，repo commit `ff249ee`，产物时间戳
2026-08-03 21:05 → 08-04 00:47，即三 seed 全程 ≈3.7 小时本地 GPU）：

| 检查 | 结果 |
|---|---|
| 三 seed 训练收敛 / 无泄漏 / 解码器等价 / test 隔离 | 全 PASS |
| 状态头（present/absent）AUROC | 0.932 / 0.932 / 0.929 — **强** |
| 质量头（usable/unusable）AUROC | 0.568 / 0.591 / 0.579 — **接近随机**（负例仅 410/13,797） |
| cal_select map-in-K | 0.6637 / 0.6599 / 0.6631 |
| 阈值合格线（prereg 冻结） | Wilson 下界 ≥0.7542（overall）且 ≥0.7337（<40m²）且 coverage ≥0.80 |
| 结果 | 0.50–0.99 全扫无一阈值合格 → `selected_threshold=None` → CALIBRATION_FAILED |

解读：fidelity 平台 ~0.66 跨三 seed 稳定（与历史 0.639/0.654 同档），
是结构性缺口而非种子噪声。**prereg 许可的"每 gate 一次假设驱动修正"
尚未使用。** 无 post-run DATA memo——文书债。

### 0.3 可直接复用的资产（论文主线视角）

| 资产 | 位置 | 服务于 |
|---|---|---|
| R3 特征缓存（311,126 条，695M）+ R0 manifest/splits 锁 | `run3_native_line_2026-07/r3_feature_cache_v1/` 等 | R4 v2 重训（零特征重算） |
| R4 runner（policy 纯函数化、test-blind） | `scripts/temporal/run_r4_training.py` | R4 v2 |
| verdict store 内容寻址 replay + `replay-diff` CLI | `scripts/temporal/verdict_store.py` | Leg-R R1 字节合同、S2 离线模拟 |
| Top-52 全链冻结产物（21,453 锚点，0 operational failure；run root 79G） | `cape_town_top52_backdating_v1_*` | R1 双跑对象、S2 replay 数据源 |
| ct11 校准盲包（100 锚点，SHA `bb11e06f…`，冻结）+ 多模型面板协议 + 评估器 | `RUN-cape-town-ct11-human-calibration-2026-08-04` 资产 + `MULTIMODEL_VISUAL_REVIEW_PROTOCOL.md` + `evaluate_ct11_human_calibration.py` | S3 面板参考 v1 |
| 密封 holdout（500+100） | `ct11_disjoint_holdout_v1_20260804` | **禁开**，留 method-lock 确认 |
| changepoint/PAVA/Turnbull 确定性估计器栈 | `src/solar_backdating/estimators/` | 论文方法章 + S2 的 τ 后验 |
| `scan_decision.py`（规则机 A–R + 均匀采样 + 二分，无 EIG） | `scripts/temporal/` | S2 的被替换基线 |

## 1. Wave 0 — 文书与决策清账（1 个 agent 会话，零算力）

1. **写 R4 v1 结果 memo**（`docs/dinov3_scorer/DATA-r4-v1-result-2026-08-13.md`）：
   §0.2 的数字 + 阈值扫描曲线 + 质量头切片表；同步修正两个 tracker
   的 stale 行。这是"markdown 单一真源"纪律的欠账。
2. **owner 决策包一次签**（进 `OWNER_DECISIONS.md`）：
   a. 三分支拆分签为 plan-of-record（split review §3 的 6 项待签里
      与论文主线相关的 3 项：拆分本身、面板协议转正、Leg-S 优先级）；
   b. **R4 失败分支预决策**（brief §6/§7）：v2 再败后 shelve 还是
      解锁 LoRA 级二次投入——必须在 v2 启动前写死；
   c. R4 v2 的**唯一修正假设**选择（见 Wave 1-A 候选）。

## 2. Wave 1 — 三线并行（互不争资源）

### 1-A. R4 v2（prereg 许可的一次修正；GPU ≈4h）

先出诊断包再选假设（诊断不算修正）：质量头负例仅 410（3%）且
AUROC 近随机 → 候选修正假设（owner 三选一，只许一个）：

- H1 质量头监督信号修复：unusable 类监督来自 D12.iii 质量行的
  构成方式在 run3-native 语料下稀释——重构质量标签源/加权；
- H2 阈值语义修复：质量头无效时 `max_posterior` 混入无信息帧的
  后验质量，接受集被污染——改用状态头置信 + 质量头旁路的组合
  接受规则（需论证不是降 bar）；
- H3 类不平衡处理：质量头改 focal/重加权 + 分层重采样。

判定沿用冻结合格线（0.7542/0.7337/0.80），不许动 bar。
**产出即论文素材**：无论 GO/KILL，"蒸馏在 frame-state 上成立
（AUROC 0.93）而在质量判读上失败"本身是可写的 negative result。

### 1-B. S3 面板参考 v1（论文 accuracy 表的仪器；小额 API 配额）

按 `MULTIMODEL_VISUAL_REVIEW_PROTOCOL.md`：100 锚点冻结盲包 ×
3 模型家族（Gemini/GPT/Grok）逐帧 native 分辨率盲审 →
Dawid–Skene 融合 → `panel_reference_v1`（面板参考区间 + 每席位
混淆矩阵）。前置：三家族 API 可用性确认（配额量级：百锚点级，
远小于全城）。产出直接解锁：论文 validation 表、S1/S4 的评估
参考、R4 学生的 fidelity-to-panel 辅助报表。

### 1-C. Leg-R R1 字节合同（纯 CPU，1–3 agent 工作日）

照 [legR 计划](RUN-backdating-legR-reproducibility-plan-2026-08-12.md)
R1 条目执行：Top-52 冻结产物断网双跑 → 逐文件 sha256 → 修死
非确定性源 → `make repro-check` + `REPRO_CONTRACT.md`。
止损：3 个 agent 工作日修不完非确定性源 → 停手升 issue。

## 3. Wave 2 — 依 R4 v2 结果分叉

- **READY_FOR_R5** → R5 按线走；学生对 `panel_reference_v1` 报
  accuracy（论文表）；Leg-R R2 以学生为 v1 重验字节合同。
- **再 FAIL** → 执行 Wave 0-2b 预决策（shelve 或预授权二次投入），
  不许临场加改。
- **并行启动 S2 离线探针**（零配额零 GPU，Leg-S 标记的最便宜分支）：
  Top-52 verdict store replay 模拟 EIG 选帧 vs 现行规则机；
  GO = 终态区间不变（或 containment 不降）且 calls/anchor 从
  2.56–2.69 降 ≥20%。GO 即成为论文方法章的新颖性主张 +
  全城配额的直接节省。

## 4. Wave 3+（不在本方案内启动，按序排队）

S1（质量协变量 emission——注意与 R4 质量头失败可能同根，等 1-A
诊断包出来再定形）→ S6（conformal 区间）→ **S4（配准+池化）最后**
（前置 = panel_reference_v1 存在 + R4 结果落地；理由见 brief §3/§5）。

## 5. 红线（继承，不重述理由）

- 密封 holdout 禁开；midpoint 不作主通道；不得称 human accuracy；
- R4 判定 bar 不许动；一 gate 一修正；被结果诱导改纪律 = 违规；
- 论文发布物 = 标签/区间/代码/权重；GEHI chips 不随论文再分发
  （许可审查沿用 pre-ship licence review 条目，DINOv2-S 为
  Apache-2.0 背书权重发布）；
- 本方案不触碰 Leg-E（磁盘决策、预取授权归 Leg-E 自己的 E0）。

---

**签字项**：Wave 0-2 的 a/b/c 三项。签完后 Wave 1 三线可同日派发
（1-A、1-C 各一个 agent，1-B 待 API 可用性确认后派发）。
