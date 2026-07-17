# ISSUE-09 — C0 最终评审意见：双评审交叉比对 + 独立验证（2026-07-16）

> Tracer slice 9 · [TRACKER](TRACKER.md) ·
> [`ISSUE-09-c0-reverse-template-matching.md`](ISSUE-09-c0-reverse-template-matching.md)

## 来源与方法

三方证据交叉：

1. **评审 A（外部 ChatGPT）**：
   [`ISSUE-09-c0-external-review-2026-07-15.md`](ISSUE-09-c0-external-review-2026-07-15.md)
   —— 8 个数学问题答复 + 结构性问题 + 方法扩展菜单（Idea A–E）+ 评估重定位。
2. **评审 B（claudex 数学审计）**：已落档为 binding amendment ——
   [`DATA-c0-reverse-template-prereg-2026-07-12.md` Amendment 2026-07-15](DATA-c0-reverse-template-prereg-2026-07-12.md#c0-math-amendment-2026-07-15)
   （M1–M12 + 14 项强制测试 + calibration lock）。
3. **独立验证（本次，2 个 Opus teammate）**：直接 import
   `pilot_c0_reverse_template_2026_07_12.py` 的真实解码函数逐位复现
   fixture；推导 proper 加权似然裁决 Q1 争议；statsmodels 复算 Wilson
   功效；并对仓库做覆盖审计（a–k 清单、provenance、gate 实现、测试状态）。

**结论先行：amendment 的全部裁决经独立验证成立，维持 binding 地位不变。**
本文档不修改 amendment，只做三件事：①记录验证结果；②裁决两评审的分歧点；
③把 amendment **未覆盖**的评审 A 建议逐项定性（P0 必须处理 / P1 随
amendment 实现顺手做 / P2 登记为 C0-P 候选 arm），避免它们被静默遗忘或
被静默采纳（两者都违反 stop-discipline）。

---

## 一、独立验证结果

### 1.1 两个 binding fixture：逐位确认

- **T=3**（`s=[0,0,A]`，等间隔，单位权重）：`p_nochange=0.72836`、真
  gap `q=0.20902`，对 `A∈{0.5,1,2,5}` **逐位不变**；变体 `s=[0,1,1]`
  同样 0.72836（评审 A 的 "~0.73" 与评审 B 的 0.72836 是同一计算）。
- **T=4**（`s=[0,0,A,A]`）：`p_nochange` = 0.000675 (A=0.1) →
  0.869955 (A=1) → 0.990743 (A=2)，全部对上 —— 更大的完美 step 更自信地
  判 no-change。
- **统一根因**（验证新发现，两份评审都只各看到一半）：
  `beta0 = 2·noise_var`，`noise_var = median(diff²)/2`（pilot 脚本
  `:383`）—— **噪声先验从它要检测的信号本身导出**。T=3 时所有尺度随 A
  同步增长 → 后验对幅度严格不变；T=4 时 `diff²=[0,A²,0]` 的 median=0 →
  noise_var 触底 1e-8，幅度增长反而偏向单段 marginal。**凡是 ≥ 一半
  gap 平坦的干净 step —— 恰是本解码器的目标信号 —— 都会退化。**
  M3"先验一次性在冻结 calibration 分区上拟合"的处方对症。

### 1.2 Q1 加权似然争议：裁决支持 amendment（M4）

代码（`_weighted_log_marginal` `:332-352`）实现 `α_n = α₀ + Σw/2`、
无 `½Σlog w` 项 —— 这是 **tempered/power likelihood**
`∏N(x_i|μ,σ²)^{w_i}` 的 marginal。proper 异方差模型
`x_i~N(μ,σ²/w_i)` 的正确 marginal 是混合形式：`κ_n=κ₀+Σw`、加权
mean/SS，但 **`α_n=α₀+n/2`（整数计数）+ `½Σlog w` 归一化项** —— 与 M4
条文完全一致。

- 评审 A 的窄点正确：`½Σlog w` 确实跨 hypothesis 抵消（split 不变量，
  数值验证 −0.336672 对所有 split 相同）。但这是 red herring ——
  **`Σw/2` vs `n/2` 的差异不抵消**（经 `lgamma(α_n)`、`α_n·log β_n`
  进入，无 telescoping）。非单位权重 `w=[0.3,1.7,0.5,2.0]` 实测
  `p_nc` 0.9459（代码）vs 0.9867（proper），差异实质。
- 评审 A 的主张"`Σw` 是 proper 模型的正确充分统计量" **被驳回**。
- 单位权重下两者重合 —— 所以 1.1 的 fixture 结果不受此项影响。

**最终裁决：M4 维持原文。** 若将来想保留 power-likelihood 作为
robustness arm（评审 A 的 generalized posterior 视角并非无据），按 M4
末句走新的 pre-run amendment 并 pin 温度参数，不得作为实现捷径。

### 1.3 其余实现事实确认

- **shift search 只最大化 footprint cosine**（`compute_curve`
  `:1023-1035`），ring 在获胜位移处事后评估 —— M12 对 scratch brief 的
  更正属实；评审 A 所称"两文档实现不等价"以 prereg/实现为准。
- **footprint mask 是二元 patch-center 规则**（`:791`、`:864`），
  `region_mean` 均匀平均，无面积加权；dilate-1-patch 只作用于 template
  mask，shift-search 用的 fp_mask 未膨胀 —— 评审 A 的小目标脆弱性批评
  基于事实。
- **Wilson 功效声明全部确认**：n=40 需 ≥20/40（k=19 → 下界 0.329 <
  1/3）；观测胜率 0.40 需 n=184 才首次过线。

---

## 二、两评审的一致面（已由 amendment 定案，予以背书）

| 议题 | 评审 A | 评审 B / amendment | 最终 |
|---|---|---|---|
| anchor 自匹配泄漏 | 最高优先级修复，LOO 或硬约束 | M2（含 anchor-excluded 才算 GO AUC） | ✅ 定案 |
| 左右帧竞争 | 右侧默认为主 | M12.2 anchor gate 不可 fail-open，失败落右锚 | ✅ 定案 |
| 短序列噪声先验 | pooled empirical null + shrinkage | M3 冻结 calibration 分区先验；禁 per-series | ✅ 定案（同一处方的两种表述） |
| μ₀/β₀ double dipping | robust-center + μ₀=0 + 共享方差 | M3 退役 per-target 全序列先验 | ✅ 定案 |
| gap prior 0.5 | 原疑虑自我澄清，要求显式化 | Q4 裁决"refuted"，M5 显式 0.5/0.5 因子化 | ✅ 定案（两审独立得出相同结论） |
| q=max gap mass | 拆分检测/定位 + 80% 最短日历区间 | M6（q_single 降为诊断） | ✅ 定案 |
| persistence | 由 P(z_t=1) 驱动、防回落 | M8（两个连续独立 post 帧 + 回落否决） | ✅ 定案 |
| T=3–5 可靠性 | 共享方差正向 step 模型 | M3 + M9（T=3–4 禁入 clean，clean 需 T≥5） | ✅ 定案 |
| 假设空间 | H_pre/H_gap/H_post/H_bad | M1 H_before/H_k/H_terminal/H_failure | ✅ 定案（同构） |
| 解码器替换 | monotone 正向 step + 共享噪声 | M3 单侧共享方差 precision-weighted step | ✅ 定案（除 §3.2 的 t 分布分歧） |

两份独立评审在 fixture 数值上逐位一致、在 Q4 上独立互证 —— 这本身就是
对 amendment 裁决可信度的强交叉验证。

---

## 三、分歧点裁决

### 3.1 ring contrast：替换 vs 保留为主（M11 维持）

评审 A 主张把 ring mean 换成 trimmed-median patch 级相似度 / matched
roof negatives；M11 保留 primary ring arm + 强制 robust 诊断 arm，
promotion 需 pre-run amendment。**裁决：M11 立场正确**（静默换主指标
违反 prereg 纪律，且 co-change 的经验流行率未知）。落地要求：M11 的
"至少一个 robust 诊断 arm" **就实现为评审 A 的 trimmed-median patch 级
ring**（含 temporal-MAD 剔除不稳定 patch）；matched roof negatives 登记
为 C0-P 候选。

### 3.2 残差分布：Gaussian（M3）vs Student-t（评审 A）

M3 有意选择 Gaussian 共享方差；评审 A 要 `t_ν` 抗云/阴影/错配 outlier。
**裁决：维持 M3 为 primary** —— 理由：reliability 权重 + M10 fail-closed
输入校验 + M8 persistence 已各挡一类 outlier 路径；t 分布破坏共轭、引入
ν 选择自由度，在 calibration 数据到位前是过度设计。**但登记为 P2 候选
arm**：若 calibration 分区residual 诊断显示重尾（QQ/峰度），以 pre-run
amendment 引入 t_ν（ν 在 calibration 分区一次性 pin）。

### 3.3 评估主指标：replicate-equivalence（prereg H1）vs 人工真值主指标（评审 A）

评审 A 认为 Gemini agreement 只证"像 Gemini"。这个批评在认识论上成立，
但 prereg H1 是 owner 2026-07-12 的明确设计决定（paired 比较、
no-absolute-accuracy 属 Non-goals），且 banked 真值已被判 dirty ——
**裁决：H1 不动**。补偿措施见 §4 P1-4：在不破坏 calibration/smoke/main
三方 disjoint lock 的前提下，把已标注 target 的 end-to-end exact-gap
accuracy 作为**非门控诊断**随主评估一并报告。若它与
replicate-equivalence 结论方向冲突，触发 owner 复审而非自动改判。

---

## 四、amendment 未覆盖项的最终定性

repo 覆盖审计确认：以下各项**只存在于外部评审存档中**，未进入任何
binding 文本。逐项定性如下（任何 P0/P1 的方法性变更仍需按纪律走
pre-run amendment —— 本文档是意见，不是授权）。

### P0 —— 必须在相应评估开跑前解决（阻塞性）

> **✅ 两项均已解决（owner 决定 2026-07-16，按本文档推荐选项落地）：**
> [prereg Amendment 2026-07-16](DATA-c0-reverse-template-prereg-2026-07-12.md#c0-p0-amendment-2026-07-16)。
> 实现与测试同日落档，详见下方各项的 Resolution。

1. **`c0_r1` rule-2 与 prereg 文本自相矛盾 + 功效不足。**
   prereg `:213` 写 "all three must hold, CI bounds respected"，但
   `check_student_path_gate.py`（原 `:371-379`）对 rule 2 只做点估计
   `adj_share ≥ 1/3` + `n ≥ 40` —— harness 未兑现 CI 条款；而若真加
   CI，n=40 需观测胜率 50% 才能过（已验证），对"≥1/3 即证明独立信号"
   的设计意图构成隐性加码。原选项：(a) 显式声明点估计 + 硬 N gate；
   (b) posterior gate（如 `P(p>1/3|data)≥0.95`）+ 裁决样本 ~150–200。
   推荐 (b)。
   **Resolution ✅（选 b）**：rule 2 = Jeffreys `Beta(½,½)` posterior
   gate `P(p>1/3|k,n) ≥ 0.95`，`n ≥ 150`（n=150 时观测 40.0% 即过，
   k=60 → 0.957，与设计意图对齐）；harness 要求整数计数
   `c0_adjudication_correct_n`，share/k/n 不一致、k>n、缺键均
   fail-closed。含"旧点估计过、新 gate 杀"（54/160）的回归测试。
2. **fresh Gemini round 的 provenance 缺口**（main eval 前补齐，改动
   都很小）：`temperature` 硬编码 0 但**不在** fingerprint/sidecar；
   batch 内 image order 无显式字段断言；rendered capture-date 字符串
   只 hash 了模板未存渲染结果（batch 模式甚至不发日期给模型 ——
   这一点本身要在 RUN 文档里写明）；preprocessing 无显式字段（虽然
   `chip_sha256` 已锁定实际字节）。已覆盖的：exact model ID（三处
   校验）、prompt hash、thinking config、per-call ts、retries 审计。
   **Resolution ✅**：`GENERATION_TEMPERATURE` / `IMAGE_PREPROCESSING`
   / `IMAGE_ORDER_RULE` 常量化并进入 `prompt_config_fingerprint`
   （hash 有意断代，fresh round 未启动、无比较集被切分）；
   `score_batch_with_fallback` 对 chip_index 顺序 fail-loud 断言；
   batch attempt 审计记录持久化 rendered prompt + `image_order`；
   "batch 模式不发 per-chip 日期"已写入 RUN 文档 §2 provenance notes。

### P1 —— 随 amendment 实现一并做（廉价、C0-R 范围）

3. **fractional polygon∩cell 权重**（评审 A §二.4）：二元 center mask
   已被验证为实现事实，是小 PV 的真脆弱点，且 M1–M12 未触及。属于
   measurement 变更 → 以小型 amendment（"M13"）或显式 diagnostic arm
   进入；shift-and-stitch / FeatUp 留 P2。
4. **人工标注的 end-to-end exact-gap 诊断**（§3.3 的补偿措施）：只用
   smoke 分区、非门控、随 Stage-0 一并报告。
5. **Stage-0 诊断指标扩充**：target-clustered AUC、stable-present /
   stable-absent 的 false-transition rate —— 纯增报，GO bar 不动
   （M2 已定 anchor-excluded pooled AUC ≥0.75），无纪律成本。
6. **shift-posterior entropy 记录**：M12 维持 max-shift（本文档不
   推翻），但把 49 个位移的 cosine 分布熵作为**记录字段**加入 curve
   产物近乎零成本，为评审 A 的 winner's curse 担忧提供可观测量；
   soft-max 边际化本身留 P2 arm。

### P2 —— C0-P / 未来 arm 登记（本轮不做，防遗忘）

按评审 A 原编号：**Idea B** pairwise block changepoint（独立
cross-check，优先级最高，排 Sinkhorn OT 之前）→ **Idea A**
multi-prototype present bank → **Idea E** date-level nuisance
correction（4 万目标共享 vintage，training-free）→ **Idea C**
AnyChange 双通道 → **Idea D** 低层次 PV 结构通道 → Student-t 残差
（§3.2）→ soft-max 位移边际化 → matched roof negatives →
shift-and-stitch / FeatUp → posterior entropy / log-odds margin 进入
confidence 输出契约。每项进入时各自走 pre-run amendment。

### 同时确认的实现债（已在 amendment/ISSUE-09 内，此处仅点名）

`tests/validation/test_c0_pilot.py` 现有 48 collected **全部**属于
pre-amendment 表面，14 项强制测试**零覆盖**（两个形似的
`test_isotonic_rejects_downward_step` / `test_bayes_weights_downweight_outlier`
测的是已退役函数）。Slice 9 的 ⛔ 状态与此一致。

---

## 五、最终意见

1. **Amendment 2026-07-15 全部维持**，且经第三方逐位复现获得强化：
   两个 fixture 确认、M4 的似然裁决在推导与数值上均成立、M12 的事实
   更正属实。
2. 评审 A 的价值在裁决之外的**方法菜单与评估批判**：其中 P0 两项
   （rule-2 矛盾+功效、provenance 缺口）是 amendment 的真实盲区 ——
   **已于 2026-07-16 按推荐选项解决**（prereg Amendment 2026-07-16，
   代码+测试同日落档）；P1 四项建议随实现顺手落地；P2 清单登记
   防遗忘。
3. 唯一的原则性分歧（评估主指标）按 prereg 纪律维持 H1，以非门控
   exact-gap 诊断对冲。
4. 解锁路径：amendment 2026-07-15 实现 + calibration lock + 14 项
   测试 → Stage-0 重跑 → main eval。P0 两项已不再阻塞；main eval 的
   裁决样本按新 rule 2 需 `n ≥ 150`（owner 标注工作量约为原计划
   4 倍，随 disagreement 实际数量而定）。
