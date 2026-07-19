# PRD — Run3-native 本地打分线受控试验（纯科研线，2026-07-19）

Status: **READY**（owner-approved 2026-07-19：①G1/G2/G3 门禁 + §7.4
搁置准则照签；②repeat-ceiling 小额 Gemini quota 批准；③LoRA/backbone
禁令维持——owner 判断本地算力大概率不可行，将来如需解禁须先做
可行性分析 + 新 prereg，见 §0.4）
Owner 决定（2026-07-19）：本地方案作为**纯科研方向**重启；若在本 PRD 的门禁下
仍表现不佳，**搁置本线，转入 Cape Town backdating**（搁置准则见 §8.3）。

Provenance：外部 Codex 方案评审（2026-07-19，两部分：受控 DINO 试验 +
语义定位层）→ 本仓库 12 项逐条核查（Opus teammate，脚本见会话 scratchpad
`agg.py`/`agg2.py`；修正登记见 §2）→ 结合既有 KILL/NO-GO 台账
（[`DATA-student-revival-paths-2026-07-10.md`](DATA-student-revival-paths-2026-07-10.md)、
[`../replan_v2/DATA-fullscan-run2-recenter-pilot-2026-07-17.md`](../replan_v2/DATA-fullscan-run2-recenter-pilot-2026-07-17.md)、
[`../replan_v2/DECISION-A-estimator-adoption-2026-07-04.md`](../replan_v2/DECISION-A-estimator-adoption-2026-07-04.md)）。

---

## 0. 定位与红线

1. **任务边界不变**：这里的 DINO 是"已知 anchor 上的历史影像
   presence/absence scorer"，**不是**自由区域光伏检测器（CLAUDE.md 的
   not-free-detection 红线）。
2. **不是生产替换**：DINO 全量替换 Gemini 维持 **NO-GO**——尚无独立
   repeat ceiling、空间留出评测和风险校准。本线一切产出只进科研评测，
   slice-7（rollout）继续 ⛔。
3. **重开许可（stop-discipline 合规）**：2026-07-10 复活备忘录关闭了
   A/A′/A″/B 全部冻结 head 复活路径，并规定重开仅限"新 prereg + 以下条件
   之一：teacher geometry 修复 + 重蒸馏 / caliber 政策变更 / 用户解除
   LoRA/backbone 禁令"。**RUN 3（anchors_v2，几何修复后的 41,393 全量干净
   标签）恰好满足第一项**——本 PRD 即该"新 prereg"的框架文档，不是对
   A/A′/A″/B 的静默重开。
4. **约束继承**：LoRA/backbone 训练禁令（owner veto 2026-07-10）**仍然
   生效**——owner 2026-07-19 复核："本地应该做不了，看情况分析"，即
   默认维持禁令（本地 4070 8GB 对 backbone 级训练大概率不可行）；仅当
   某个门禁失败的残差分析给出"必须动 backbone"的新假设时，先做
   算力/收益可行性分析（本地 vs koko vs RunPod），经 owner 批准并另立
   prereg 后方可解禁。全部监督仍来自 banked Gemini 标签，零新 GT。复活备忘录的 binding discipline 全数继承：param-matched（±10%）
   对照、多 seed + CI 判定、守住 `present→unusable` 弃权逃逸、禁止
   self-pair 泄漏。

## 1. 现状判定表（Codex 原表 + 核查修正）

| 层级 | 判定 | 依据（核查后） |
|---|---|---|
| 数据合同与工程准备 | **GO** | RUN 3 完成 41,393/41,393；聚合得 **311,195** 个唯一 `(anchor_id, capture_date)` 已评分观测（精确核实，§3.1） |
| 小规模冻结骨干试验 | **GO（无 CUDA 阻塞）** | ~~"需先恢复 CUDA"~~ **核查为误报**：本机 RTX 4070 Laptop 8GB、driver 595.80/CUDA 13.2、torch 2.10.0+cu128 `cuda.is_available()=True`，随时可跑 |
| 旧 DINO head 直接复用 | **NO-GO** | gate-2 FAIL（`A_LSAT=0.6392`/`A_floor=0.6542` ≪ ceiling 0.7724，[TRACKER](TRACKER.md) slice 6）；且 07-19 空间清理后 `head_v1` checkpoint、`dinov3_distill_20260705/`、`fidelity_gate_20260710/` 特征缓存**均已不在盘上**——复活=全量重 harvest+重嵌入+重训，不是 reload |
| C0 reverse-template 主评测 | **仍 BLOCKED** | M1–M12 数学修订仍停在文档（[prereg amendment](DATA-c0-reverse-template-prereg-2026-07-12.md)），pilot 代码仍是旧解码器（power-likelihood `_weighted_log_marginal`、`beta0` 自举退化等），14 项强制测试 0 项落地。**与本 PRD 并行，不在关键路径上** |
| DINO 全量生产替换 | **NO-GO** | 同 §0.2 |

代码资产核查：slice 2–4 的三件套
`scripts/temporal/build_distillation_set.py` / `dinov3_scorer.py` /
`train_dinov3_head.py` **全部健在**，可改造复用；原始 chips 全量健在
（`basemap_rebuild_2026-07-13/chips/`，84 GB）。

## 2. 核查修正登记（写死，防止错误引用扩散）

对 Codex 方案文本的逐项核查结论（12 项，全文见会话记录）：

- **VERIFIED**：311,195 观测数（精确）；gate 数字 0.6392/0.6542/0.7724；
  `train_dinov3_head.py` 逐帧 3 分类 CE + macro-F1 早停 + 全局 lo/hi 阈值
  （类集是 `present/absent/unusable`，非 uninformative）；生产推断器仍是
  `latest_absent/earliest_present + midpoint`（`infer_install_dates.py`）
  且全量 redecode 被 ISSUE-22 AC5 显式延期；C0 数学缺陷清单与 14 项强制
  测试缺失；外部评审的三态 + `target_localized` 前置建议
  （[REVIEW-external §](../replan_v2/REVIEW-external-deepresearch-backdating-2026-07-18.md) :253-258）；
  QA 偏移数字（出错组中位 ~18–20 m vs 稳定组 4.2 m）与"剩余瓶颈 = census
  polygon 精度 + 局部屋面定位"；GEHI↔Vexcel cos 0.31 vs 同域 0.89
  （出处是 A′ KILL 备忘录与 C0 prereg :96-97）。
- **WRONG / 需改写**：
  1. **CUDA 并未损坏**（见 §1）——"恢复 CUDA"不是前置任务。
  2. `replan_v2/TRACKER.md:87` 行号错误，marker-miss 实质在 :113-117。
  3. **ISSUE-24 learned matcher 的最终判定是 GO 不是 KILL**（91.3%
     corroborated / 8.7% dark zone；KILL 于 07-10 被 GT 重裁决推翻）。
  4. "343 passed / 13 skipped" 与当前 collect 到的 1,312 项不符，按
     过期数字处理。
  5. **"连续单调 changepoint 后验解码器"不是待建件**——Phase-0
     changepoint + EB decoder 已建成于
     `src/solar_backdating/estimators/`（changepoint/epochs/survival/
     emissions/seam/pava），且已在 DECISION-A 上过完整评测：**面板门全过
     （MAP mode-hit 0.905/0.942、HPD 0.914）但 D3 年份直方图 TVD 门失败
     （带 0.037–0.063，实测 0.065 flat / 0.075 EB prior）→ NO-GO，
     sustained 保留**。见 §4.1 的处理方式。

## 3. 数据合同（Phase R0 — 冻结 Run3-native manifest）

### 3.1 语料（已核实）

- **311,195** 个唯一 `(anchor_id, capture_date)` 已评分观测（41,393
  anchors；均值 7.52 帧/anchor；round 构成：initial 206,965 / bisection
  73,118 / anchor_recovery 31,087 / walk_back 25；311,093 `gemini_batch`
  + 102 `gemini_failed`）。"later round wins" 去重删除 0 行——同一
  anchor 内无日期被重打。
- 对照口径：RUN 2 实测 288,276 是**另一次 run 的数字**，RUN 3 实际打了
  更多帧，两者不冲突。
- 完整 vintage 目录另有 1,198,246 帧（~29 帧/anchor）可作后续扩展，
  **本 PRD 只用已评分的 311k**，不先嵌入 113 万帧全 stack。

### 3.2 Manifest 要求

版本化 manifest（parquet + 锁定哈希），至少含：`anchor_id`、
`capture_date`、round/decision provenance、Gemini verdict + confidence +
quality_flag、源 TIFF SHA、逐帧 TFW、`geometry_version`
（`fullscan_target96_review{24,48}_v2`）、census_date、`source_area_m2`。
train/calibration/test 按**空间 grid + legacy group** 双重隔离拆分
（防同屋顶/相邻目标/重叠像素跨集泄漏），拆分文件与哈希一并冻结。

### 3.3 标签语义（采纳外部评审三态制）

`present / absent / uninformative(+corrupt)`；旧类集的 `unusable` 并入
uninformative。**`absent` 只在 `target_localized=true` 时允许**；
building 找不到 ⇒ uninformative，不得回落为 absent。这直接针对旧 head
把 placement 失败学成 absent 的污染通道。

## 4. 数学核心（对 Codex 提案的修正采纳）

### 4.1 解码器：扩展 Phase-0，不新建（关键修正）

Codex 提议的单调序列后验
`log P(τ=k|x) ∝ log π_k + Σ_{t<k} log e_t(0) + Σ_{t≥k} log e_t(1)`
在结构上就是已建成的 Phase-0 decoder。因此：

- **D1 决策：复用并扩展 `src/solar_backdating/estimators/`**，新增
  三态 emission 的 uninformative 边缘化（uninformative 帧对似然贡献
  常数，不硬塞 absent）与显式边界状态（already-present / 仍未出现 /
  census-bound），而不是另写一个解码器。
- **必须直面 DECISION-A 的失败记录**：D3 年份直方图 TVD 门（带
  0.037–0.063）是它当年 NO-GO 的唯一原因。本线的评测必须把 TVD 门列为
  正式门禁之一（§7），并检验"三态 emission + 区间级训练"是否恰好修复
  当年 flat/EB prior 两个变体都略超带的问题。**若 TVD 门再次失败且无新
  假设，不得以'科研线'为名绕过。**
- **先统一解码合同再训练**：训练目标、校准与验收全部走 Phase-0 decode
  口径；`infer_install_dates.py` 的 sustained 口径仅作 legacy 对照列
  报告，避免"在一个目标上训练、被另一个目标验收"。生产链是否切换仍归
  ISSUE-22 AC5 的原有决策流程，不在本线范围内。

### 4.2 模型输出：两头解耦

- `q_t = P(usable & localized | x_t)`：质量/定位头；
- `e_t(0), e_t(1)`：在 usable & localized 条件下的校准 emission。

**依据**（gate-2 残差审计，
[DATA-hard-example-strips](DATA-hard-example-strips-done-appears-2026-07-10.md)）：
oracle 帧修复实验中 FP+FN+unusable 三类同修可把 `done_appears` 层位从
0.45 抬到 **0.80，越过 0.7724 ceiling**——残差是双向 P/A 混淆 +
质量误判的复合，单一 3 分类共享边界正是旧 head 的结构性天花板。解耦
两头 + uninformative 边缘化是对这个实测残差结构的直接回应。

### 4.3 训练目标：区间级损失

设 anchor i 的 RUN 3 标签允许的 change cells 为 K_i：

`L_i = −log Σ_{k∈K_i} P_θ(τ=k | x_i) + λ·L_frame/quality`

优化目标与最终 install interval 一致，避免逐帧 CE + 全局阈值化在边界
帧上的误差放大（旧 sequence revival B 线已实证该放大效应；注意 B 线
KILL 的是"蒸馏 teacher 已解码区间"的旧配方，本处是以 RUN 3 帧标签为
证据的区间边缘似然，属新 prereg 范畴）。校准从"全局 lo/hi 阈值"改为
**分层校准**（A24/A48 × 面积段 × 年代/清晰度），面积分层带 <40 m²
co-headline（该层占 corpus 87.7%，teacher 自一致性仅 0.65–0.67）。

## 5. 语义定位层（"目标语义定位 + 屋面几何配准"）

### 5.1 与既有 NO-GO/GO 台账的相容性（必须写清）

- **07-17 recenter pilot 的 NO-GO 针对的是"SP+LightGlue 重定位作为
  placement 修复"**：blind bucket（67.6% corpus）0/472 重定位越过半箱，
  198 例中仅 3 例过置信门；结论"registration 与 placement 是正交失败
  模式"。Codex 提案是**不同的东西**——有界配准作为 gating/abstain 层
  （PV 区域屏蔽、不搬 marker、冲突即弃权）——**不被该 NO-GO 直接否定**。
- **但同一 pilot 的正交性发现压低其预期收益**：主导失败形态（十字线在
  车道/路面/草坪上）**配准完全正常**（正确配准到了错误位置），
  registration-quality 门抓不到它。因此本层的定位是
  **训练标签去污染 + uninformative 门控**（防止 placement 失败进入
  absent 训练信号），**不是** blind bucket 的救援手段——预期收益要按
  这个口径预登记，不许事后换口径。
- ISSUE-24 learned matcher（SP+LightGlue）最终判定 **GO**（91.3%
  corroborated，8.7% dark zone）——可作为 weak-lock 级联件使用，但
  dark zone 决定了不得对每帧无条件强制 warp。

### 5.2 三层拆分

| 层 | 任务 | 方法 |
|---|---|---|
| 语义关联 | 是否同一栋建筑/同一屋面片 | 地理先验 + DINO 全局/上下文特征 |
| 几何配准 | 屋面在该 vintage 的平移/旋转 | phase correlation → weak-lock 才 SP+LightGlue → RANSAC；只允许有界 translation/similarity |
| PV 判定 | 投影后目标区域是否有 PV | §4 的 polygon ROI head + Phase-0 解码 |

DINO 不再承担亚像素配准（DINO-coarse 已 KILL，2026-07-08，不重开）。
参考影像用**同域 GEHI** 最新可靠 present 帧；Vexcel 只保留 polygon/
census 日期/候选位置，不作 DINO 模板（A′ KILL：跨域 cos 0.31）。
配准特征屏蔽 PV polygon 及 buffer（用屋檐/屋脊/天窗/邻建/道路等稳定
结构），防止配准利用待判定目标。

### 5.3 输出合同 `TargetLocalizationObservation`

每个 `(anchor_id, capture_date)` 至少：`building_found`、
`roof_plane_matched`、`target_localized`、`transform_type/params`、
`registration_confidence`、`shift_uncertainty_m`、
`projected_target_polygon`、`failure_reason`。默认信任 TFW/Run3 几何，
仅在有可靠证据时施加**有界**修正；两信号冲突或偏移超界 ⇒ abstain
（uninformative），绝不判 absent。不确定度下传：保留 top-K transform
或位移协方差；简化版用不确定度扩张 ROI，但扩张不得跨入相邻屋面，
否则 abstain。

## 6. 工程路线

1. **R0 冻结数据合同**（§3）——纯 CPU/IO，可立即开始。
2. **R1 marker-free student 输入**：从 96m TIFF 生成与 A24/A48 同视野
   的无十字线 crop；精确 polygon ROI pooling + 一圈 roof/context 特征；
   不让 DINO 学到 marker，也不用整幅 96m 稀释小目标。
3. **R2 定位层最小实现**（§5）：先在分层样本上回放级联，产出
   `TargetLocalizationObservation`，量化 uninformative 门控对训练集的
   净化率（预登记预期：主要清理配准可检出的 corrupt/artifact 类，
   而非 blind bucket）。
4. **R3 DINOv2-S 主基线**（旧结果 floor 比 DINOv3-L-SAT 高 1.5pp 且
   算力小一个量级；LSAT 降级为 challenger）：只嵌入 311k 已评分帧。
   特征缓存以 `chip_sha + crop_geometry + backbone_hash +
   pooling_version` 为键写分片 Parquet/Arrow，fp16 ROI feature，
   原子完成标记 + 断点续跑；不再 PNG/NPZ 洪泛（旧缓存已被清理，正好
   换合同）。
5. **R4 训练 + 分层校准**（§4.2/4.3），单机 4070 可行（gate-2 全量
   重打分当年就在本机零 API 跑完）；必要时 koko/RunPod 扩容。
6. **R5 评测过门**（§7）→ 若过门，才谈 hybrid（DINO 高置信本地推断 +
   abstain 回落 Gemini）的**下一份** prereg；本 PRD 不含任何生产切换。

## 7. 评测协议与门禁

报告口径（全部 inventory-weighted 为 headline，分层为诊断；
dated-only 并列报告）：

- exact interval agreement + interval overlap / boundary error；
- transition band FP/FN（param-matched 对照，±10%）；
- coverage–risk 曲线（abstain 率 vs 错误率）；
- 分层：A24/A48 × 面积（<15 / 15–40 / 40–100 / ≥100 m²，<40 m²
  co-headline）× 年代 × 影像质量；
- 3 个随机种子 + CI；
- **独立 Gemini repeat ceiling（RUN 3-native 重导）**：在冻结评测
  panel 上 3 次独立重打（~panel×7.5 帧×3 reps，约数千至 1.4 万 obs，
  半小时级 quota，**owner 已批准 2026-07-19**）——不再沿用 RUN 2
  时代的 0.7724 作为本线 ceiling；
- 盲态人工 disagreement adjudication（顺序重注册，沿 goldset 协议）。

**门禁**：

- **G1（fidelity）**：student 区间一致性达到 RUN 3-native teacher
  ceiling − S（ceiling 自带 spread）以内，且 <40 m² 层不垫底于
  teacher 同层自一致性。
- **G2（decoder）**：Phase-0 扩展版通过 DECISION-A 的原有面板门 **加**
  D3 TVD 门（带 0.037–0.063）——这是当年 NO-GO 的心结，必须正面过。
- **G3（去污染有效性）**：定位层的 uninformative 门控在盲评样本上
  净化率显著 > 误杀率（预登记阈值待 R2 回放后定，写入正式 prereg）。

### 7.4 搁置准则（owner 2026-07-19 决定的落地）

预登记的尝试预算内（建议：R3 基线 + 每门至多一次修正迭代，全程零
backbone 训练）若 **G1 或 G2 仍失败且无新假设**，本线搁置：TRACKER
标记 shelved（保留 manifest/特征缓存合同以便将来续跑），资源转入
**Cape Town backdating**。不做无预登记的第三次迭代——与 07-10 复活
备忘录的 stop-discipline 保持同构。

## 8. 非目标 / 边界

- 不做全量 1,198,246 帧嵌入（后续扩展另立 prereg）；
- 不做重型全量屋顶分割模型；
- 不动生产链（RUN 3 deliverable、`infer_install_dates.py`、ISSUE-22
  流程均不受本线影响）；
- census polygon 精度问题在主仓库边界外（QA coverage 备忘录 :330 的
  路由结论），本线只消费其结果；
- C0 线（ISSUE-09）继续按其 binding amendment 独立推进或搁置，本 PRD
  不接管。

## 9. 立即可动的三件基础件（无 GPU 依赖也可开工，CUDA 实测可用）

1. R0 manifest 生成器 + 空间拆分 + 哈希锁定；
2. `TargetLocalizationObservation` schema + 级联回放脚本骨架；
3. Phase-0 emission 扩展的接口设计稿（uninformative 边缘化 + 边界
   状态），附 DECISION-A TVD 复测挂钩。
