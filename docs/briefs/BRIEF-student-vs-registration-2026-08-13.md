# BRIEF — 下一步优化方向：学生模型 vs 配准+池化

日期：2026-08-13 · 触发：owner 对方向不确定 + Grok 文献简报
（`~/下载/wide_install_intervals_lit_2026-08-13.md`）
依据文档：[REVIEW-citywide-plan-split-2026-08-12](../replan_v2/REVIEW-citywide-plan-split-2026-08-12.md) ·
[RUN-backdating-legR](../replan_v2/RUN-backdating-legR-reproducibility-plan-2026-08-12.md) ·
[RUN-backdating-legS](../replan_v2/RUN-backdating-legS-research-program-2026-08-12.md) ·
[dinov3 TRACKER](../dinov3_scorer/TRACKER.md) ·
[R4 prereg](../dinov3_scorer/RUN-r4-training-calibration-prereg-2026-07-20.md)

Owner 已澄清（2026-08-13 问答）：

1. 可复现性的核心诉求 = **同一个安装结果不会漂移**；对外 replication
   是下一步，可以只交数据集或权重。
2. 瓶颈资源 = **GPU 算力 + 本人注意力**（不是 Gemini 配额，不是经济学
   deadline）。
3. 配准+池化的吸引力 = 直觉上是 ViT/遥感路线"最顺的下一步"，
   不是某个具体下游需求。

## 0. 先拆掉一个隐含等式

「先保证可复现性 → 所以先训学生模型」这个推理在本仓库不成立。
"结果不漂移"有两个独立层：

- **重放层**（今天就能锁死）：chips 字节冻结 + Gemini verdict 一经产生
  即冻结为数据（verdict store 内容寻址命中）+ 确定性解码
  （changepoint/PAVA/Turnbull 都是确定性算法）。这就是 Leg-R R1 的
  字节级合同——**零 GPU、零配额**。
- **重打分层**（才需要学生模型）：没有 Gemini 也能对新 chip 打分。
  这只在"扩新城市 / Gemini 停服"场景下是关键路径。

对 owner 问的「我可以只提交权重或数据集？」——**可以，而且数据集
就够**：一个合法的 replication package = 冻结 chips（sha256 清单）+
verdict store（把 Gemini 判读当作一次性采集的数据，如同问卷原始
数据）+ 确定性解码代码 + `repro_lock.json` 环境锁。权重只在你要
声称"端到端自托管"时才进包。

## 1. 目标的三个版本

### V1（最低可接受）——结果不漂移，今天可锁

Leg-R R1：Top-52 冻结产物上双跑字节一致 + `make repro-check` 进
smoke gate + `REPRO_CONTRACT.md`。

- **能回答**：同一输入永远同一区间；任何人拿包能逐字节重建交付。
- **回答不了**：没有 Gemini 时如何给新 chip 打分；区间准不准。

### V2（中间）——V1 + 自托管学生 v1（R4 照跑）

按已冻结的 R4 prereg 执行训练：DINOv2-S 冻结骨干 + 298,243 参数
轻头，三个固定 seed，特征缓存（R3，311,126 条）已建完，training
runner 已落（2026-08-13 提交）。fidelity 对标 teacher ceiling
0.7708 ± 0.0166，不冒充真值 accuracy。

- **能回答**：端到端无 hosted 依赖的打分线是否可用；Leg-R 自托管线
  从 v0（现成 DINOv3 头，对旧参考 56.3%）升 v1。
- **回答不了**：区间是否更窄更准（fidelity 是"像老师"，不是"更对"）。

### V3（理想）——V2 + 配准+池化（Leg-S S4）缩窄/稳住区间

SuperPoint+LightGlue 配准预处理 + 邻近锚点证据池化 + 序列级判读，
在多模型面板参考上验 TRANSITION containment 提升。

- **能回答**：区间宽度/稳定性能否真的改善（Grok 简报 B2 的杠杆）。
- **回答不了**：它假设面板参考仪器（S3）已立——现在还没有。

## 2. 每个版本的取舍

| 版本 | GPU | 注意力 | 放弃了什么 |
|---|---|---|---|
| V1 | 0 | 低（数天级排查非确定性源） | 不动准确度；不解锁新城市 |
| V2 | 小（轻头 + 已缓存特征，本地 4070 可跑；时长为**假设**：小时级） | 中（prereg 已冻结，照跑即可，决策成本≈0） | 若 fidelity 仍差 ceiling 10+pp，只能"如实报低"，自托管线准确度难看 |
| V3 | 中 | **高**（新 prereg + 面板参考先立 + weak-lock 重评） | 占用最稀缺的注意力；且探针本身要新打分（见 §3） |

V2 的历史底色（不粉饰）：fidelity gate 2026-07-10 双骨干 FAIL
（A_LSAT=0.6392 / A_floor=0.6542 vs ceiling 0.7724）；学生复活线
A/A′/A″/B 全部 KILL。但 R4 是换了输入的新预注册线（run3-native
311k 观测、marker-free crop、3-state 软解码、V4/V5 short-gap 修复
回收 5,248 帧），先验不高但**执行便宜、纪律已锁**（bounded baseline
+ 每 gate 至多一次修正）。

V3 的反证据（不粉饰）：recenter pilot 已把"配准作为主修复"NO-GO
过一次——RUN2 错位的主因是自家 stale offset bug（ISSUE-27 已修，
RUN3 rescan 确认收益）；ISSUE-24 配准 GO（纠正后 79.6%）但
weak-lock 探针的 91.3% 验证信号是自指的、无独立 GT，"非生产级"
是它自己的结论。"最顺"是路线直觉，仓库证据说它是**第二修复**
（offset 修掉后的残差层），不是第一杠杆。

## 3. 最小验证实验

- **V1 的探针 = 它自己**：R1 双跑 diff。成本 ≈ 1–3 个agent工作日
  （排查 dict 排序/浮点归约/时间戳入文件），零 GPU 零配额。
- **V2 的探针 = R4 本体**：prereg 已冻结所以不需要再设计探针；
  三 seed 训练在缓存特征上跑，成本估计（**假设**，待 runner 实测）
  本地 RTX 4070 数小时～1 天。G1/G2 gate 直接给 GO/KILL。
- **V3 的探针不便宜，这本身是个论据**：配准后的 chip 必须**重新
  打分**才能看区间变化——要么烧 Gemini 配额，要么用学生模型。
  最便宜的形态是"配准前后用现成 DINOv2/v3 头各打一遍旧 500
  TRANSITION 子集看翻转率"（纯本地 GPU），但仪器 fidelity 只有
  ~0.64/0.56，读数弱。**若 R4 学生成了，V3 的探针成本立刻降一个
  量级**——这是先 V2 后 V3 的结构性理由。

## 4. 止损条件（可判定）

- **V1**：双跑 diff 非空且 3 个 agent 工作日内未能定位并修死全部
  非确定性源 → 停手上升为 issue，不无限排查。
- **V2**：沿 R4 prereg 既有纪律——G1/G2 各允许至多一次假设驱动的
  修正；修正后仍 FAIL → 触发已批准的 shelve-to-CapeTown 规则，
  学生线搁置，Leg-R 自托管线停留在 v0（如实报低）。**不许**为救
  fidelity 加改 prereg、换骨干、开 LoRA（owner veto 维持）。
- **V3**：S3 面板参考未立之前不开 S4 prereg（硬前置）；开了之后
  沿 bounded-pilot 纪律，TRANSITION containment 在面板参考上无
  预注册幅度的提升 → KILL，不静默调参续命。

## 5. 推荐 + 理由

**推荐：V1 立即做，V2 同步照跑，V3 推迟到 S3 面板参考立起且
R4 出结果之后。** 即：不是"二选一"，而是两个便宜的先走、贵的
排队。

理由：

1. Owner 的核心诉求（结果不漂移）被 V1 完全覆盖，且零 GPU——
   与瓶颈资源正交。
2. V2 的决策成本已经付清（prereg 冻结、修正已签、runner 已落），
   剩下的是执行；GPU 成本小到不触碰瓶颈。它同时给 V3 送一个
   便宜的探针仪器。
3. V3 消耗的恰好是最稀缺的注意力，前置（面板参考）未立，
   且仓库已有一次"配准作为主修复"的 NO-GO。直觉上的"顺"
   来自路线叙事，不来自本仓库的证据链。

**风险最大的假设**：R4 在缓存特征上的轻头训练能以小时级 GPU
成本完成、且新输入（run3-native + marker-free + short-gap 修复）
足以把 fidelity 从 0.64 档拉到接近 ceiling 0.77 档。前半（成本）
大概率成立；后半是真赌注——但 prereg 的止损纪律已经把输的代价
封在"搁置 + v0 如实报低"，不会污染 V1 的可复现声称。

## 6. 追问补充（2026-08-13）：econ top5 目标是否抬高 R4？

Owner 追问：对标文献（DeepSolar++）都是端到端自托管的，PI 目标定在
econ top5，是否需要更关注 R4？

**结论：top5 目标改变 R4 的定位（→ 审稿周期保险），不改变排序。**

1. **Replication 合规由 V1 满足，不需要自托管。** AEA 系数据政策
   要求"数据 + 代码可重建论文结果"，不要求测量仪器可重跑。冻结
   Gemini verdict = 原始标注数据（等同于一次性调查/人工标注/专有
   卫星产品），是顶刊的标准合规形态。
2. **DeepSolar++ 是错误对标。** 它发 *Joule*，模型本身是贡献，
   所以必须端到端。econ top5 论文的贡献是因果估计，仪器是手段；
   仪器部分的顶刊标准是透明 + 误差刻画 + 独立验证，不是自托管。
3. **R4 的真价值 = R&R 保险。** 顶刊审稿周期 2–4 年内 hosted
   Gemini 版本大概率停服；审稿人若要求加城市/换口径重打分，
   同一仪器必须还在——这是自托管学生的实质意义。注意 R4 的
   fidelity 是"像 Gemini"，不增加测量的科学可信度。
4. **审稿人真正会攻击的两处 R4 都不覆盖**：区间宽度杀 DiD
   （S1/S4/S6 + 经济学侧 bounds 报告）与独立真值验证（S3 面板、
   CoJ audit、外部记录）。"更关注 R4"若挤占这两块，是资源错配。
5. **唯一被此目标改变的决策：R4 失败分支要预先写死。** 现行规则
   是两 gate 各一次修正后 shelve、LoRA/换骨干被 owner veto。若
   R4 定位为投稿保险，owner 应在 R4 出结果**之前**决定：失败后
   (a) 解锁第二轮投入（推翻 veto），还是 (b) 接受"投稿仪器 =
   冻结数据集"兜底框架（本身可发表）。不许被结果诱导着临时改纪律。

## 7. 二次追问补充（2026-08-13）：owner 的署名回报 = 模型类论文

Owner 澄清：经济学部分不参与、不署名 top5；本人回报大概率是一篇
DeepSolar++ 式的模型/数据集论文（Joule / RSE / Science of Remote
Sensing / NeurIPS D&B / EarthVision 一类 venue）。

**这改变目标函数：仪器从手段变成贡献本身。** 修正如下：

1. **R4 再抬一档：保险 → 论文核心基础设施。** "调 Gemini 冻结输出"
   在 ML/遥感审稿下不成立（闭源老师、无法复跑）；开放学生 + 端到端
   管线是论文成立的前提。失败分支预决策随之倾斜：此 reframe
   **支持**预授权一轮结构化二次投入（如解锁 LoRA），因为 shelve
   意味着论文故事退化为"闭源老师的数据集论文"。仍须在 R4 出结果
   前写死，不许事后改。
2. **S3 与 R4 并列上抬。** 论文表格必须是对独立参考的 accuracy
   （学生与老师都对 S3 多模型面板 + 外部锚报数）；"fidelity to
   Gemini" 是内部运维口径，上不了论文。R4 过 fidelity gate 既不
   必要也不充分——论文需要的是"学生 vs 面板 ≈ 老师 vs 面板"。
3. **最便宜的新颖性是 S2，不是蒸馏。** 蒸馏是标准技术；区别于
   DeepSolar++/GRW/OPTIMUS 的是 censoring-exact 单调 changepoint +
   Turnbull（已有）、主动选帧 EIG（S2，纯离线零配额）、质量协变量
   emission（S1）。方法论文骨架 = 开放学生 + 区间感知解码 +
   主动扫描省调用。
4. **发布暗雷**：GEHI 衍生 chips 大概率不可随论文再分发（Google
   Earth 条款）；可发的是标签/区间/代码/权重。R4 骨干 DINOv2-S
   为 Apache 2.0，比 DINOv3 许可干净——对发权重有利；与 tracker
   既有的 pre-ship licence review 衔接。

**修正后的顺序**：V1 + R4 现在跑（不变）→ S3 升为与 R4 并列的
论文关键路径 → S2 作为新颖性探针（最便宜）→ S6 → S4 仍最后。

---

**一句话决策**：本周跑绿 Leg-R R1 字节合同 + 照冻结 prereg 执行
R4（定位 = 你自己论文的核心基础设施），S3 面板参考升为并列关键
路径，S2 作为新颖性探针排第三；R4 失败后是否解锁 LoRA 级二次
投入，请在训练启动前明确写下（本 reframe 支持解锁，但决定权在
你）——请确认或修正这个顺序。
