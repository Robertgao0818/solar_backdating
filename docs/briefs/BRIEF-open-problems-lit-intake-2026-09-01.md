# BRIEF — 开放问题文献调研入口

日期：2026-09-01 · 用途：网页端再做一轮文献调研的**问题说明书**  
不是 PRD，不是新方向决策。把仓库里已经关掉的路和还开着的洞写清楚，避免再搜回 DeepSolar++ / ChangeDINO / 「冻 ViT + 线性头」。

上游：
[`BRIEF-student-vs-registration-2026-08-13`](BRIEF-student-vs-registration-2026-08-13.md) ·
[`BRIEF-frame-state-distill-vs-geometry-2026-08-14`](BRIEF-frame-state-distill-vs-geometry-2026-08-14.md) ·
[`DATA-student-revival-paths-2026-07-10`](../dinov3_scorer/DATA-student-revival-paths-2026-07-10.md) ·
[`DATA-fidelity-gate2-dual-fail-2026-07-10`](../dinov3_scorer/DATA-fidelity-gate2-dual-fail-2026-07-10.md) ·
[`DATA-sequence-head-pilot-prereg-2026-07-10`](../dinov3_scorer/DATA-sequence-head-pilot-prereg-2026-07-10.md) ·
[`DATA-r4-v1-result-2026-08-13`](../dinov3_scorer/DATA-r4-v1-result-2026-08-13.md) ·
8 月 12–13 日已读文献：[`SRC-zasolar-literature`](SRC-zasolar-literature-2026-08-12.html) ·
[`SRC-gemini-alternatives-lit`](SRC-gemini-alternatives-lit-2026-08-12.html) ·
[`SRC-wide-install-intervals-lit`](SRC-wide-install-intervals-lit-2026-08-13.md)

**一句话：** 单帧「这张图有没有板」已经能蒸馏到 AUROC 0.93 / 一致率 ~87%；安装**区间**卡在 ~0.65 对老师、过渡带更差、小阵信息下限。下一轮文献不要再论证「该用 DINO」，要找能处理**稀疏不规则时间轴 + 不可用帧 + 亚米级小目标 + 教师自洽只有 ~77%** 的方法。

---

## 0. 给网页模型的复制块

把下面英文块整段贴进 DeepSearch / Gemini / ChatGPT / Grok。中文正文是给人读的约束；英文块才是检索任务。

```text
I need a literature search, not a system design. Do not recommend
DeepSolar++, DINOv3, ChangeDINO, Scale-MAE, SatMAE, DOFA, Prithvi,
Global Renewables Watch, CCDC/COLD, or “frozen ViT + linear head”
as the main answer — those are already read or already tried.

TASK. Location-conditioned rooftop PV install-date inference.
Input: a known installation polygon + lon/lat from an upstream census.
Process: pull sparse historical Google Earth Historical Imagery (GEHI)
vintages at that location (irregular dates, not Landsat/S2 cadence),
score present/absent/unusable per vintage, decode an interval-censored
install window. This is NOT free detection.

WHAT ALREADY FAILED (treat as closed unless a paper uses a genuinely
different mechanism):

1. Frozen DINOv3-L-SAT + light head distilled from Gemini frame labels.
   Interval fidelity 0.639 vs teacher self-agreement ceiling 0.772.
   Smaller DINOv2-S floor scored 0.654 (bigger SAT backbone lost).
   ~80% of interval mistakes shared across both backbones.

2. Frame agreement ~0.80–0.87 collapses to interval agreement
   ~0.37–0.65 under a hard-MAP changepoint decoder. Teacher-unusable
   frames are forced to present/absent 75–95% of the time, which moves
   the bounds.

3. Sequence-level distillation (2026-07-10, pre-registered): frozen
   384-d pooled embeddings + 2-layer temporal Transformer, target =
   teacher’s decoded interval. Exact interval +9.4 pp (real) but
   transition-band FN worsened +72%, FP reduction CI crossed 0, overall
   frame agreement regressed. Killed. Do not re-propose pooled-embedding
   temporal transformers on the same recipe.

4. Pairing / Siamese / ChangeDINO-style heads on frozen embeddings:
   - absent-anchor pooled pairing: killed
   - Vexcel HR present template × GEHI: domain-gap smoke kill
     (cos present-GEHI×Vexcel 0.31 vs Vexcel×Vexcel 0.89)
   - same-sensor latest-present GEHI template + patch-token head:
     ranking smoke passed, trained head lost to param-matched
     single-frame control (FP worsened). Pairing line closed.

5. Registration as the primary fix (DINO-coarse matcher; then
   SuperPoint+LightGlue): not the main error source. The large
   misplacement was a stale target-offset bug, already fixed.

6. R4 (new inputs, same frozen DINOv2-S + ~300k two-head model,
   interval loss, marker-free crops, 311k observations):
   state-head AUROC 0.93; quality/usability head AUROC 0.57–0.59
   (near-random; 3% negatives, mostly mis-specified). Calibration
   fidelity plateau 0.660–0.664 at coverage ≥0.80 (bar 0.754).
   Informal r4_v2 after quality-label repair: frame ~87%, interval ~65%.
   Platform did not move. Backbone swap is vetoed; one LoRA round is
   the only licensed model escalation.

INFORMATION LIMIT. At z19 (~0.3 m/px), arrays <15 m² have ~10 native
pixels (~3 m side). Recall of “present” is 0.56 there vs 0.96 at
≥100 m². Upsampling 256→518 does not add texture. The student is
matching a teacher who also cannot see those arrays clearly.

CONSTRAINTS.
- Sparse irregular vintages (GEHI), not dense optical time series.
- No independent install-date ground truth for training. Supervision
  is Gemini labels; teacher disagrees with itself ~23% on decoded
  intervals (ceiling 0.77 overall, ~0.65–0.67 on <40 m²).
- Evaluation as “accuracy vs Gemini” is fidelity, not truth.
- Production still uses Gemini; the student is for reproducibility /
  a methods paper, not for beating a hosted VLM on date accuracy.

PLEASE SEARCH FOR (priority order):

A. Decoding sparse, irregular satellite observations into
   interval-censored event times WITHOUT brittle hard-MAP
   changepoints. Soft/survival/monotonic/multiple-instance /
   temporal-action-localization methods that treat the transition
   band as the object, not a side effect. Cadence ≠ Landsat/S2.

B. Distilling a three-state observation model
   (present / absent / uninformative-or-corrupt), especially when
   “unusable” is rare (~3%) and students collapse it to a hard
   class. How papers prevent forced decisions from moving
   censoring bounds.

C. Sub-10-pixel / few-metre objects in sub-meter RGB: information
   limits, when to abstain, when higher zoom/sensor is required,
   vs false hope from super-resolution or another ViT.

D. Sequence-level knowledge distillation or event-boundary losses
   that do NOT sit on globally pooled frame embeddings, and that
   do not inflate interval exact-match by raising transition FN.

E. Independent rooftop PV install-date references (permits,
   utility interconnection, cadastral, street-level) used as
   accuracy — not VLM self-agreement.

F. Active selection of which vintages to score when each frame is
   expensive (expected information gain, Bayesian experimental
   design, not uniform/binary search).

G. After frozen-backbone pairing failed: published evidence that
   Siamese/template matching for “is the known object there yet”
   only works with LoRA/finetune, or evidence it still fails.

Do NOT propose: a new frozen satellite ViT; CCDC on Sentinel-2;
utility-scale first-detection; “just add a temporal Transformer”;
or lowering the fidelity bar. For every paper, state: mechanism
difference vs the killed recipes above; whether it assumes dense
regular time series; whether it needs independent GT; expected
effect on TRANSITION-band FP/FN, not headline year accuracy.
Return 8–15 verifiable papers (DOI or arXiv), clustered by A–G,
plus 1 paragraph on what is still a literature gap.
```

---

## 1. 任务边界（先锁死，再查文献）

**做的事：** 上游普查已经给出屋顶光伏多边形和坐标。在这个已知位置上拉历史卫星 vintage，判断每一帧「有板 / 没板 / 这张图不能用」，再解码成安装区间。GEHI（Google Earth Historical Imagery，Google 地球历史影像）是唯一下载源；日期不规则、一年往往只有几张，不是 Landsat / Sentinel-2 那种密时间序列。

**不做的事：** 在自由影像上检测光伏（那是主仓库 ZAsolar 的普查）。也不做场站级、几十米阵列的首次出现（Global Renewables Watch / CCDC 那一档）。

**成功标准要拆开，文献经常把它们混在一起：**

| 口径 | 仓库里叫什么 | 现状 | 文献里常见的错位 |
|---|---|---|---|
| 学生像不像 Gemini | fidelity（保真度，学生解码区间对老师区间的一致率） | 门 2 要求对标老师自洽上限 0.772；实测 ~0.64–0.66 | 没有真值时硬报「安装年 accuracy」 |
| 单帧有没有板 | frame-state | AUROC 0.93，校准面 ~87% | 把这个数当成安装日准了 |
| 安装区间对不对 | interval / map-in-K | ~0.37–0.39（旧门）→ R4 ~0.66；informal r4_v2 ~65% | 精确年匹配、最早检测阈值 |
| 对独立参考准不准 | panel / CoJ / 外部记录 | 面板参考还没立起来 | 用老师自洽冒充真值 |

老师自己跟自己也不到完美：两次独立 Gemini 解码的区间键一致率上限 **0.7708 ± 0.0166**（RUN3 仪器、576 锚测试板）；占主导的 `<40 m²` 层大约 0.65–0.67。这表示大约 23% 的「错误」可能只是教师噪声。它重要，是因为再强的学生也不该被要求超过老师自洽。忽略的话，会把结构上限当成模型失败，无限换骨干。

署名回报是模型 / 数据集论文，不是经济学 top5。蒸馏「像 Gemini」是内部运维口径；论文表需要学生对**独立参考**的数字。S3 多模型面板还没做。

---

## 2. 硬约束（直接否定一类论文）

1. **时间轴稀疏且不规则。** 不能假设 CCDC / COLD / Landsat 16 天或 Sentinel-2 5 天。一篇方法如果依赖密观测和光谱突变，对 GEHI RGB chip 不适用。
2. **训练期没有独立安装日真值。** 监督来自 Gemini 标签。独立真值（CoJ 审计、多模型面板、外部记录）是验证通道，不是训练集。
3. **骨干更换和全量微调目前禁止**（owner D2：r4_v2 再败只授权一轮 LoRA）。文献若唯一处方是「换 DINOv3-L / Scale-MAE / 视频骨干并全量训」，与现行纪律冲突；可以记作对照，不能当下一步。
4. **z19 小阵接近信息下限。** `<15 m²` 约 10 个原生像素，有板召回 0.556；`≥100 m²` 是 0.960。插值不是新信息。超分 / 再训一个头，变不出板的纹理。
5. **生产仍是 Gemini。** 学生线是可复现 / 论文基础设施，不是当前全城打分器。Cape Town 全城（约 9 万非 Top-52 锚）正在用 Gemini 3.1 Flash-Lite 跑。

---

## 3. 已经读过，不要当新发现

8 月 12–13 日网页文献已经覆盖并把「可借法」写进仓库。下一轮若主推这些，等于没查。

| 文献 | 我们拿它当什么 | 为什么不够 |
|---|---|---|
| DeepSolar++ (Wang et al., 2022, *Joule*) | 屋顶自托管定年对照；伪 Siamese + HR 参考 | 我们在冻结 DINO 上复现「有板模板」失败（见 §4 A′/A″） |
| DINOv3 / ChangeDINO | 冻骨干双塔的文献借口 | 同传感器补丁头打不过逐帧对照 |
| Global Renewables Watch / CCDC / reverse-COLD | 场站尺度对照 | 分辨率和目标尺寸不覆盖屋顶；GRW 用最早检测阈值，无解码器 |
| Gemini RS caption（Barzilai 2025）| Gemini 当遥感教师的先例 | 不是 presence / 安装年 |
| 航天 VLM 蒸馏（Hicsonmez 2026）| 无独立 GT + fidelity 配方 | 领域不是屋顶 PV |
| AeroLite / SatMAE / Scale-MAE / DOFA / Prithvi | 去 API / 另一类骨干 | 换骨干被否；Scale-MAE 的 GSD 故事与「10 个原生像素」不是同一问题 |
| 宽区间杀 DiD（Denteh–Kédagni 等，8/13 速览） | 经济学动机，为什么要缩区间 | 不提供 CV 方法 |

8 月 12 日的结论「Gemini→DINOv3 发表系统基本为空」仍然成立，**但这不是下一步要论证的命题**。空白已经够用来写 Related Work。现在缺的是：空白里哪些机制还没被我们亲手杀掉。

---

## 4. 已经试过并关掉的机制

数字均来自预注册运行或锁定 memo，不是印象。

### 4.1 冻 ViT + 单帧轻头（2026-07-10 保真度门）

- DINOv3-L-SAT：区间保真度 **0.6392**
- DINOv2-S 地板：**0.6542**（更大的 SAT 骨干输了 +1.5 pp）
- 老师上限：0.7724（后被 RUN3 仪器重测为 0.7708 ± 0.0166）
- 两边约 80% 的区间错是同一类；一半还落到同一个区间键
- 双方都决定时，单帧一致率仍有 ~0.78–0.80（门 3）
- 老师 `unusable` 几乎不被复制（正确率 15–25%）；学生把 75–95% 硬判成有或无

**因果栈：** 单帧 ~0.80 → 不可用帧被强迫决定 → 硬 MAP 变点把少数帧翻转放大成不同区间（0.80 帧 → 0.37–0.39 区间）。门 3「芯片还行」和门 2「区间不行」可以同时成立。

它说的是：瓶颈不在骨干容量。忽略的话，会把「换一个卫星 ViT」当成还没做的实验。

### 4.2 时序头 Path B（2026-07-10）— 你想做的「时间序列识别」已经做过

冻结 384 维全局池化嵌入 → 两层时间 Transformer（668,933 参数）→ 蒸馏老师的**解码区间**（种类 + 上下界）。对照是参数匹配、不能看跨帧的逐帧 MLP。三 seed，heldout 163 锚，成对锚聚类 bootstrap。

| 指标 | 对照 B | 时序头 A | 预注册门槛 |
|---|---:|---:|---|
| 精确区间一致率 | 0.1963 | **0.2904**（+9.4 pp，CI 不含 0） | 增量 >0 且 CI 下界 >0 → 过了这一条 |
| 过渡带假阳性降幅 | — | 9.8%，CI 穿过 0 | ≥30% 且 CI 下界 >0 → 未过 |
| 过渡带假阴性 | 0.0733 | **0.1258（+71.7%）** | 必须下降 → 反向 |
| 总体已决定帧一致率 | 0.8340 | 0.8210（退步） | 不得退步 |
| 过渡带 / 总体帧门 | — | 0.711 / 0.821 | 两边 ≥0.90 |

Verdict：B-R1 KILL + frame-bar FAIL。无救援扫参，无门 2 重跑。

它说的是：看见整条时间线能对上老师的区间键，但靠的是在刀口上更猛地漏报「已经出现」。安装日最值钱的就是过渡带。忽略的话，会被「区间 +9.4 pp」带去重做同一配方。

复活备忘录写过：时序动作识别文献本来就认为错集中在边界，联合建模也消不掉。即便单帧学生到 0.90，硬区间匹配仍可能停在 ~0.70——那是**口径**问题，不是再换一个头。

### 4.3 配对 / 模板 / ChangeDINO 式（同一天三条全关）

| 路径 | 设定 | 结果 |
|---|---|---|
| A | 最早老师「没有板」当锚 + 全局池化 | R1 KILL（范围仅限这个弱设定） |
| A′ | Vexcel 航测「有板」× GEHI | 烟测死：有板 GEHI×Vexcel 余弦 **0.31**，Vexcel×Vexcel **0.89**。头没训。不要把航测模板硬接到冻结 DINO 上 |
| A″ | 同传感器、最新老师「有板」GEHI + 补丁 token 头 | 烟测过（0.78 > 0.59 > 0.58）；训完后假阳性比逐帧对照更差，总体 0.803 < 0.817。配对线关闭 |

文献缺口曾经是：ChangeDINO / SemDINO 用密多尺度补丁差，DeepSolar++ 用已知有板的高分辨率参考。A″ 已经是同传感器 + 补丁头，仍然打不过逐帧对照。下一轮若有人再推「Siamese 即可」，必须指出与 A″ 的机制差异（例如必须 LoRA、必须像素空间、必须未池化的多层特征），否则是重复。

### 4.4 R4：换了输入，平台没动

新预注册，不是把上面的 KILL 重跑。311,195 条观测、无准星 ROI、三态软发射、短间隔标签回收 5,248 帧、冻结 DINOv2-S 特征缓存。头约 30 万参数，状态 + 质量两个头，损失含区间项。

R4 v1（2026-08-03/04，本地 4070，3.7 小时，三 seed，verdict 锁定）：

- 状态头 AUROC 0.932 / 0.932 / 0.929
- 质量头 AUROC 0.568 / 0.591 / 0.579；负例 410/13,797 = **3.0%**；其中 84% 其实是可读的边界帧被错当成「图不可用」
- 校准面 map-in-K 0.660–0.664
- 覆盖率 ≥0.80 时最好 Wilson 下界 0.6725，门槛 0.7542，差 8.2 pp
- 覆盖收到 2.5% 才能到 0.87：排序信号在，仪器在要求覆盖下不够

与 7 月 0.639 / 0.654 **同一档**。run3-native 输入修正没有移动平台。

8 月 14 日 informal r4_v2（修了质量监督，**不是**已锁定的三 seed 合同结果）：单帧 ~87%，区间 ~65%；与 v1 同一 seed 的状态一致率 87.08% vs 87.06%。**「还没做单帧蒸馏」不成立。**

### 4.5 配准当主修复：也 NO-GO 过

DINO 粗 token 匹配器正控最好 39.4%（门槛 70–80%）。SuperPoint+LightGlue 纠正 GT 后 79.6% 过门，弱锁探针自己的结论是非生产级。RUN2 大错位的主因是陈旧 `target_offset`，ISSUE-27 + RUN3 已修。配准是残差层。8 月 13 日 BRIEF 把它排到面板参考之后、最后做。

---

## 5. 真正还开着的科学问题（请按这个搜）

下面每一条都是「文献可能有我们没试过的机制」。不是「再训一个头」。

### A. 稀疏不规则观测 → 区间删失事件时间，不要硬 MAP

问题：0.80–0.87 的单帧，被变点硬 MAP 放大成 0.37–0.65 的区间。仓库已有 PAVA 单调地板和 Turnbull 生存先验，生产用的是软一点的三态发射，但 R4 仍过不了 0.75 @ 覆盖 0.80。

要找的：把过渡带本身当预测对象的方法（时间动作定位、单调软变点、生存 / 区间删失损失、多实例学习把整条序列当成一个事件）。不要 CCDC 式密时间光谱突变。

问论文：它的时间步是不规则的吗？它优化的是边界还是逐帧分类再后处理？过渡带假阴性会不会为了「区间对上」而变差（我们的 Path B 就是这样）？

### B. 三态观测：present / absent / uninformative

问题：质量头近随机；不可用帧被强迫决定，直接移动删失边界。负例只有 3%，而且标签曾被污染（可读边界帧当 unusable）。

要找的：拒识 / 选择性预测 / 损坏标签 / 三态发射；稀有「不可用」类怎么蒸馏，才不会塌成先验。

问论文：学生可否输出「这一帧不进似然」？还是总在有/无上求最大？

### C. 亚米级 RGB 上不到 10 个原生像素的物体

问题：`<15 m²` 召回 0.56，几何上约 10 px。人眼在不知标签时也可能看不清。

要找的：小目标信息下限、该弃权而不是硬判、何时必须更高变焦 / 换传感器。不要超分救世、不要「再换 ViT」。

问论文：作者有没有按原生 GSD × 目标尺寸做分层，而不是只报总体 AP？

### D. 序列级蒸馏，但机制必须不同于 Path B

Path B 杀掉的是：**全局池化向量 + 时间 Transformer + 区间键损失**。还没杀的可能包括：未池化的时空 token、显式过渡带损失（同时压 FP 和 FN）、把质量 / 不可用当第一类输入、不规则时间间隔的连续时间模型。

若一篇论文的配方在这四件事上都和 Path B 同类，直接标「重复」。

### E. 独立真值，不是 VLM 自洽

CoJ 2015 层已经暴露过热水器被当成光伏这种失败。CT-11 修正 holdout 没过门。S3 多模型面板还没跑。

要找的：屋顶光伏安装日的外部记录（并网、许可、地籍、街景时间戳）怎么当 accuracy；多评估者融合（Dawid–Skene 一类）在遥感变化检测里怎么用。不要再用「跟 Gemini 一致」冒充准。

### F. 哪几帧值得打分（昂贵观测）

现行扫描是规则机 + 均匀 / 二分，每锚大约 2.56–2.69 次调用。S2 主动选帧是仓库里最便宜、还没做的方法新颖性（离线 replay，零配额）。

要找的：期望信息增益 / 贝叶斯实验设计 / 主动变化检测，在稀疏历史影像上选下一张 vintage。

### G. 配对是否只在可训练骨干上成立

A″ 在冻结嵌入上失败。D2 允许一轮 LoRA。文献若能给出「模板匹配必须解开骨干」的证据，那一轮 LoRA 才有机制假设；若解开骨干之后配对仍不赢逐帧头，LoRA 名额不该花在 Siamese 上。

---

## 6. 建议检索式（英文）

不要只搜 `DINOv3 solar panel`。优先：

```
interval-censored event time satellite imagery irregular revisits
temporal action localization change detection sparse observations
soft changepoint monotonic posterior remote sensing
selective prediction abstention unusable image distillation
small object information limit sub-meter RGB rooftop
sequence-level knowledge distillation event boundary loss
not globally pooled embeddings
photovoltaic interconnection permit records install date ground truth
Dawid-Skene aerial change detection
expected information gain next-frame satellite scoring
Siamese change detection LoRA vs frozen backbone ablation
```

负向过滤（命中这些当对照，不要当主推）：`DeepSolar++` `ChangeDINO` `CCDC` `Sentinel-2 first detection` `utility-scale PV`.

---

## 7. 什么算有用的调研产出

请网页模型 / 你自己整理时，按 A–G 分簇，每篇只保留：

1. **机制差**：和 §4 已杀配方差在哪（输入、损失、时间模型、要不要密观测、要不要真值）。
2. **可迁移性**：能否用在 GEHI RGB、每锚每年几张、已知多边形。
3. **预计动哪根针**：过渡带 FP / FN、不可用帧是否进入似然、`<15 m²` 是弃权还是硬判。不要只报 overall year accuracy。
4. **和现行纪律冲不冲突**：冻骨干 / 只 LoRA / 不新开独立 GT 训练集。

有用的一页结论长这样：

- A 类找到了 X，和硬 MAP 的差别是 …，GEHI 稀疏度下可能成立 / 不成立，因为 …
- B 类没有屋顶 PV 先例，最接近的是 …，可借的是拒识校准，不是骨干
- C 类共识是低于 N 个原生像素应弃权；我们的 10 px 已经过线 / 没过线
- D 类没有不同于 Path B 的存活配方 → 时序头不要重开
- 仍空的洞是 …（这才是论文缺口，不是「还没人用 DINO」）

无用的产出：再列一遍 DeepSolar++、建议换更大 ViT、建议在 Sentinel-2 上跑 CCDC、建议「加一个 LSTM/Transformer」。

止损：如果 8–15 篇核实文献在机制上都落在 §4 已杀集合里，就停这一轮，不要为了「查了文献」去重开 Path B / 换骨干。

---

## 8. 仓库指针（网页模型可忽略）

| 要核对的数字 | 文件 |
|---|---|
| 门 2 双骨干 FAIL | `docs/dinov3_scorer/ISSUE-06-gate-verdict-2026-07-06.md` · `DATA-fidelity-gate2-dual-fail-2026-07-10.md` |
| 时序头 KILL | `docs/dinov3_scorer/DATA-sequence-head-pilot-prereg-2026-07-10.md` |
| 配对三条关闭 | `docs/dinov3_scorer/DATA-student-revival-paths-2026-07-10.md` |
| R4 v1 校准失败 | `docs/dinov3_scorer/DATA-r4-v1-result-2026-08-13.md` |
| 小阵 10 px / 召回 0.56 | `docs/briefs/BRIEF-frame-state-distill-vs-geometry-2026-08-14.md` |
| 失败分支：一轮 LoRA，禁换骨干 | `docs/replan_v2/OWNER_DECISIONS.md` D2/D3 |
| 已读文献 | `docs/briefs/SRC-*-2026-08-12.html` · `SRC-wide-install-intervals-lit-2026-08-13.md` |

调研结果请另存为 `docs/briefs/SRC-<topic>-<date>.md`（或 `.html`），不要改本文件。本文件只锁问题，不锁答案。

**需要你做的一句话：** 用 §0 的英文块做检索；回来时按 A–G 告诉我哪一类还有没被 §4 杀掉的机制，哪一类可以宣布文献也空。
