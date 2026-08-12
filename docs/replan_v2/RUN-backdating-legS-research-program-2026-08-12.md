# RUN — Leg-S 科研计划(并行分支:前沿文献 + 数学方案)

日期:2026-08-12 · Status: **READY(分支可独立派发)**
拆分索引:[`REVIEW-citywide-plan-split-2026-08-12.md`](REVIEW-citywide-plan-split-2026-08-12.md)
复审协议:[`MULTIMODEL_VISUAL_REVIEW_PROTOCOL.md`](MULTIMODEL_VISUAL_REVIEW_PROTOCOL.md)

**目标**:在多模型面板参考上,锁定可辩护的 first-visible-appearance
日期方法(method lock),供 CT-CW-03 密封确认与对外发布使用。每条分支
= 一个独立 agent + 一份独立 RUN 文档 + 预注册 GO/KILL。

## 0. 全分支共同规则(硬)

1. **开发数据白名单**:旧失败 CT-11 500、面板校准 100
   (`ct11_human_calibration_v1_20260804` 面板化产出)、显式标注的探索集、
   CoJ 历史 cohort。**密封 `ct11_disjoint_holdout_v1_20260804`(500+100)
   禁止读取/渲染/间接查询**。
2. 参考仪器 = 多模型面板融合(协议 §4);旧 Codex 拼贴数字仅诊断。
   旧 KILL(recovery 37.5%、ref-anchored 30%、provenance 52.1%、DINO 头
   56.3%)相对旧参考,须在面板参考上重评,不得当永久死亡证明。
3. 每分支先填预注册表(指标、阈值、样本、KILL 条件)再看结果;
   指标默认 **year-bin agreement + interval containment + Wilson 下界**,
   exact-bracket 降为诊断。
4. 分支间共享 frozen chips 与 verdict store,只读;新增打分走自己的
   run root + 配额票。

## 1. 现有数学资产(分支的出发点,勿重造)

- `estimators/changepoint.py`:monotone changepoint,O(T) 精确 τ 枚举,
  censoring-exact(abstain 边缘化),HPD CI;明确拒绝过 HMM/BOCPD。
- `estimators/emissions.py`:3-symbol 混淆矩阵 P(symbol|state, stratum),
  cohort 级确定性 EM。
- `estimators/survival.py`:Turnbull NPMLE → 日历年 CohortPrior(EB 后
  decoder mode-hit 0.905→0.942)。
- `estimators/pava.py`:PAVA 单调 floor。D19:fractional posterior-mass
  为交付口径。
- `scan_decision.py`:规则机(病例 A–R)+ 两端锚定均匀采样 + 二分;
  **无信息增益**。
- ISSUE-24:SuperPoint+LightGlue 配准 KILL→GO(纠正后 79.6% recovery),
  weak-lock 探针 91.3% 有独立验证信号但非生产级。

---

## S1 — 观测模型升级:质量协变量 emission(L0 优先)

**假设**:CT-11 失败的一部分是把 blur/阴影/低分辨率帧当作可信 absent/
present 观测。当前混淆矩阵只按 stratum 分层,不吃帧级质量。

**数学**:把 P(symbol|state, stratum) 推广为
logistic/softmax emission P(symbol|state, x_t),x_t = (achieved zoom,
PSR/对齐分, 模糊度, 阴影指标, provider, 分辨率);在单调 changepoint
似然内做 EM(M 步 = 加权多项 logistic 回归),无需真值标签——单调性
本身提供弱监督。解码端 censoring-exact 结构不变,只换 emission 项。

**文献锚点**:covariate-dependent HMM emissions(Bartolucci et al., *LM
models for longitudinal data*);label-noise 学习(Natarajan et al. 2013);
misclassified interval-censored survival(McKeown & Jewell 2010)。

**数据/评估**:旧 500 + 面板参考;prereg 指标 = interval containment 提升
且 UNDATABLE 不升;KILL = containment 无显著提升(McNemar)。

## S2 — 贝叶斯最优选帧(active scanning,直接省钱)

**假设**:均匀采样+二分在 τ 后验已集中时浪费调用;全城 ≈290k HTTP 的
主决定因素就是选帧策略。

**数学**:贪心一步期望信息增益
`EIG(t) = H[p(τ|D)] − E_{y_t~pred} H[p(τ|D, y_t)]`,预测分布由 S1 emission
给出;τ 后验就是 changepoint decoder 的现成输出,EIG 在 O(T²) 内精确可算
(T 为 epoch 数,很小)。退化性质:无噪声+平坦先验时约化为二分——现行
策略是其特例,可作回归测试。附带产出:预算约束下的停止规则
(EIG < ε 或后验 HPD 宽度达标即停)。

**文献锚点**:Bayesian optimal experimental design 综述(Rainforth et al.
2024);noisy binary search(Karp & Kleinberg 2007;Burnashev–Zigangirov);
active changepoint localization。

**评估**:对 Top-52 冻结 verdict 做 **离线 replay 模拟**(verdict store
命中,零新调用):同样终态下 calls/anchor 从 2.56–2.69 降多少;
prereg GO = 终态区间不变(或 containment 不降)且调用降 ≥20%。
纯离线即可 GO/KILL,是六分支里最便宜的。

## S3 — 多评审融合与参考构造(与协议 L1 共生)

**数学**:Dawid–Skene EM(每席位 4×4 混淆矩阵 + item 后验);
升级项:GLAD 式 item 难度参数;3+ 条件独立评审者下的可辨识性与
spectral 初始化(Zhang, Chen, Zhou, Jordan 2016);席位相关性检验
(P1 与生产同家族的条件依赖建模——Ipeirotis 型 correlated annotators)。
逐帧层:融合三态直接喂 changepoint decoder,得"面板参考区间"。

**文献锚点**:Dawid & Skene 1979;Whitehill et al. 2009 (GLAD);
Zhang et al. 2016 spectral+EM;Snorkel/generative labeling(Ratner 2017)。

**评估**:协议 §5 校准数据;prereg = 融合标签 bootstrap 稳定性、
留一席位敏感性;产出直接成为其它分支的参考仪器。

## S4 — 配准 + 多目标池化 + 序列级判读

**假设**:帧间错位与逐帧孤立判读是 TRANSITION 类不稳定(repeat 2/8)的
主因;整段序列判读已有正向证据(recovery pilot 加权单调质量 76.4%)。

**内容**:(a) SuperPoint+LightGlue 配准作为 chip 预处理(ISSUE-24 GO
线复活,面板参考上重评 weak-lock);(b) 同 grid 邻近锚点共享帧的
证据池化 = 耦合 changepoint(共享 per-frame 质量潜变量,变分或
交替最大化);(c) 序列级 VLM 判读(整段 flank 一次给模型)与逐帧三态
的融合权重。

**文献锚点**:LightGlue(Lindenberger 2023)/LoFTR;satellite time-series
change detection(SITS-BERT、UTAE 一线);multi-image temporal reasoning
in VLMs(近两年 MLLM 时序基准)。

**评估**:旧 500 TRANSITION/ALL_ABSENT 子集;prereg = 面板参考上
TRANSITION containment 提升;KILL 沿用 bounded-pilot 纪律。

## S5 — 学生模型蒸馏(R4/R5 续线,自托管伏笔)

现状:gate-2 FAIL(A_LSAT=0.6392 vs teacher ceiling 0.7724/0.7708);
R4 prereg 已冻结(含 empty-`K_i` 保守 sidecar A1)、训练未开;V4/V5
short-gap 标签修复已回收 5,248 帧。

**内容**:按已冻结 prereg 执行 R4 训练(fidelity 对标 teacher ceiling,
不冒充真值 accuracy);R5 视 R4 结果。噪声标签纪律:面板参考可作
teacher 标签的仲裁层(fidelity-to-panel 作辅助报表)。

**文献锚点**:satellite foundation models(DINOv3-SAT、SatMAE、Prithvi、
Clay);distillation under label noise(DivideMix、co-teaching)。

**评估**:R4 prereg 已冻结,照跑;不得因面板出现就中途改 prereg。
产出同时供 Leg-R(确定性 scorer)消费。

## S6 — 区间校准与人群级去偏(经济学口径护航)

**内容**:(a) **conformal 化区间**:以面板参考为校准集,对 HPD 区间做
split-conformal 调整,得分布无关的 (1−α) containment 保证——
"可复现性优先、准确度可辩护"的最干净声称形态;(b) **人群级混淆矫正**:
用面板估计的类混淆矩阵 Λ,对 city 级 posterior year mass 做
去偏(矩阵校正 / 贝叶斯 deconvolution),给经济学交付一条
"即便锚点级噪声大、年度曲线仍可辩护"的通道;(c) misclassification-aware
Turnbull(E 步吸收 Λ)。

**文献锚点**:conformal prediction(Vovk;Angelopoulos & Bates 2023);
measurement-error correction / matrix method(Buonaccorsi);
current-status data with misclassification。

**评估**:旧 500 + 面板;prereg = 名义 90% 区间的实测 containment ∈
[85%, 95%];年度曲线去偏前后 TVD 报表。

---

## 晋级与汇合

- 任一分支 GO 后进 **method lock 候选**:冻结 config/prompt/解码器 hash,
  在面板参考上跑预注册确认 → owner 决定是否进 CT-CW-03 开封序列。
- CT-CW-03 新门槛 re-prereg(默认不继承 70/75/80;headline =
  year-bin + containment + Wilson);通过才谈对外发布。
- 分支间优先级建议:**S2(纯离线、直接省全城配额)→ S1/S3(方法与
  参考共生)→ S4 → S6 → S5(算力最大)**。单 agent 一分支,各写
  `RUN-legS-<branch>-<date>.md`,失败照实 KILL,不许静默调参续命。
