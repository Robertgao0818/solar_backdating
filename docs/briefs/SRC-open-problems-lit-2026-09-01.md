# SRC — 开放问题文献核验（A–G）

日期：2026-09-01 · Status: **VERIFIED RETURN**  
上游问题说明书：[BRIEF-open-problems-lit-intake-2026-09-01.md](BRIEF-open-problems-lit-intake-2026-09-01.md)  
下游决策：[BRIEF-lit-intake-next-plan-2026-09-01.md](BRIEF-lit-intake-next-plan-2026-09-01.md)

本文件只归档「相对 §4 已杀配方仍有机制差」的论文。DeepSolar++ / ChangeDINO / CCDC / 冻 ViT+线性头一律当对照，不当主推。Intake 原文不改。

抽查（2026-09-01）：DOI / arXiv 能打开的包括 JUST (10.3390/rs12234001)、Neural CDE (arXiv:2005.08926)、BOCPD-deforest (arXiv:2112.12899)、SelectiveNet (PMLR 97:2151)、Lechner 2009 (10.1016/j.rse.2009.06.002)、Xu NWD (10.1016/j.isprsjprs.2022.06.002)、Li TAL (10.1109/CVPR46437.2021.00826)、PeftCD (arXiv:2509.09572)。其余条目按 owner 核验收录，未逐篇重下 PDF。

---

## A. 稀疏不规则观测 → 区间删失事件时间（不要硬 MAP）

**1. Ghaderpour & Vujadinovic, 2020.** *Change Detection within Remotely Sensed Satellite Image Time Series via Spectral Analysis* (JUST). *Remote Sensing* 12(23):4001.  
DOI: [10.3390/rs12234001](https://doi.org/10.3390/rs12234001)

- **机制差：** 在不等间隔、可加权的观测上同时估趋势+周期，用谱残差找 jump，而不是「逐帧分类再硬 MAP」。权重可以直接挂到云/糊/不可用帧上，让该帧不进似然，而不是被强迫判成有/无。
- **时间轴：** 明确为 irregular / unequally spaced；示范是植被指数，不是 Landsat-16 天 CCDC。
- **GT：** 不需要独立安装日；是无监督 jump。
- **迁移：** GEHI 每年几张、日期乱，形式上能接。但它优化的仍是「趋势里有没有一次跳」，不是把过渡带本身当预测对象；稀疏到 3–8 张时谱估计会塌。
- **针：** 可能减「不可用帧搬家边界」造成的 FP；对过渡带 FN 没有直接药。**不与冻骨干纪律冲突。**

**2. Kidger, Morrill, Foster & Lyons, 2020.** *Neural Controlled Differential Equations for Irregular Time Series.* NeurIPS 2020.  
arXiv: [2005.08926](https://arxiv.org/abs/2005.08926)

- **机制差：** 连续时间 CDE，直接吃不规则时间戳和缺失，不是把序列填成规则格再上 Transformer。Path B 杀掉的是「全局池化 384-d + 两层时间 Transformer + 区间键损失」；Neural CDE 吃的是路径（可挂未池化 token 轨迹），时间间隔进微分方程。
- **时间轴：** 就是为 irregular / missing 设计的。
- **GT：** 一般要序列级标签；可把老师解码区间当监督，但那会重现 Path B 的「对上区间键、刀口漏报」风险，必须换损失。
- **针：** 单独换时间模型**不会**自动降过渡带 FN。只有把发射做成三态、把损失做成区间删失/生存，才可能动边界。**冻骨干可接在头上，不强制换 ViT。**

**3. Wendelberger, Gray, Reich & Wilson, 2021.** *Monitoring Deforestation Using Multivariate Bayesian Online Changepoint Detection with Outliers.*  
arXiv: [2112.12899](https://arxiv.org/abs/2112.12899)

- **机制差：** BOCPD（Adams & MacKay 2007 的后验 run-length），输出的是「变点落在哪」的后验，不是硬 MAP 切一刀。显式抗 outlier，先验可补短序列。
- **时间轴：** 比 CCDC 宽松，但仍假设相对密的光学指数流（森林监测），不是一年几张 RGB chip。
- **GT：** 验证用独立砍伐记录；训练本身是贝叶斯在线。
- **针：** 软后验可把过渡带收成区间而不是点年，有利于保真度口径；观测太稀时 run-length 后验会摊成「整段都可能」，缩不了区间。适合当**解码器**，不适合当新骨干。

**A 类结论：** 文献里「软变点 / 不规则时间 / 区间删失」是分开的三摊。JUST 给加权不规则 jump，Neural CDE 给不规则序列编码器，BOCPD 给变点后验，Turnbull/AFT（仓库已有）给区间似然。**没有一篇把这三件接到亚米级 RGB、已知多边形、老师噪声 ~23% 上。** 可借的是解码器，不是再训一个时间 Transformer。

仓库对照：ISSUE-02 已明确拒绝 BOCPD/HMM，改用单调单变点的精确 τ 枚举 + 删失精确解码；D19 已把分数后验质量当交付口径、硬 MAP 降为诊断。A 类不是「还没有软后验」，是「软后验还没接到不可用帧退出似然 + 小阵弃权」。

---

## B. 三态观测：present / absent / uninformative

**4. Geifman & El-Yaniv, 2019.** *SelectiveNet: A Deep Neural Network with an Integrated Reject Option.* ICML 2019.  
arXiv: [1901.09192](https://arxiv.org/abs/1901.09192) · PMLR 97:2151–2159.

- **机制差：** 拒识头和分类头**一起训**，覆盖率是约束不是事后阈值。R4 质量头近随机（AUROC 0.57，负例 3% 且曾被污染），就是「事后第三类」塌成先验。SelectiveNet 的点是：不进似然的决策是一等公民。
- **时间轴 / GT：** 单帧自然图像，与 GEHI 无关；要覆盖率目标，不要独立安装日。
- **针：** 直接针对「不可用帧被 75–95% 硬判、搬家边界」。预期先降过渡带假边界，而不是涨 exact-year。风险：覆盖收到 2.5% 你们已经能到 0.87——问题是 **≥0.80 覆盖下校准不够**，SelectiveNet 只保证覆盖-风险曲线，不保证稀有类可学。
- **纪律：** 只动头，合法。

**5. Thulasidasan, Bhattacharya, Bilmes, Chennupati & Guan, 2019.** *Combating Label Noise in Deep Learning Using Abstention.*  
arXiv: [1905.10964](https://arxiv.org/abs/1905.10964)

- **机制差：** 训练期就允许 abstain，带惩罚；噪音/含混样本被推到拒识，真类继续学。对「老师自己 23% 不一致 + 质量标签曾把可读边界帧标成 unusable」比普通三分类更贴。
- **针：** 质量头要从近随机拉起来，前提是 unusable 的定义先修干净（r4_v2 已做标签修复、平台没动）。Abstention 防的是**强迫决定**，不创造小阵纹理。
- **缺的：** 遥感里几乎没有「稀有 unusable 类蒸馏」先例。Fabre et al. 2023 植被制图拒识、云检测文献处理的是云/影掩膜，不是「这张历史片能否支撑有/无似然」。

**B 类结论：** 可借的是拒识校准和训练期 abstain，不是骨干。屋顶 PV 三态蒸馏是空的。

仓库对照：r4_v2 / H1 已经在修质量监督源（D3）。SelectiveNet 是 H1 再败之后、D2 LoRA 之前可写进头里的机制，不是另开骨干。

---

## C. 亚米级 RGB 上 <10 个原生像素

**6. Lechner, Stein, Jones & Ferwerda, 2009.** *Remote sensing of small and linear features…* *Remote Sensing of Environment* 113:2194–2204.  
DOI: [10.1016/j.rse.2009.06.002](https://doi.org/10.1016/j.rse.2009.06.002)

- **机制差：** 纯几何信息下限，不谈 ViT。正方形斑块要 **~11 个原生像素才到 75% 分类精度**；更长更窄还要更多。Hengl (2006, DOI [10.1016/j.cageo.2005.11.008](https://doi.org/10.1016/j.cageo.2005.11.008)) 的经验规则是「检测至少 4 px、表示窄目标至少 2 px」——那是**看见有东西**，不是可靠判「这块屋顶现在有没有板」。
- **对照你们的数：** z19、`<15 m²` ≈ 10 原生像素、有板召回 0.56，已经压在 Lechner 的 75% 线以下。插值 256→518、超分、再换一个头，都变不出纹理。
- **针：** 不要按面积整层硬判，也不要按面积整层弃权。Lechner 的 11 px 是纹理分类下限；小阵若只是色块，高对比、能用、无同尺度干扰时 10 px 可以够。不满足就这帧不进区间。0.56 召回未按对比度分层，不能单独证明整层下限。

**7. Xu, Wang, Yang, Yu, Yu & Xia, 2022.** *Detecting tiny objects in aerial images: A normalized Wasserstein distance and a new benchmark.* *ISPRS JPRS* 190:79–93.  
DOI: [10.1016/j.isprsjprs.2022.06.002](https://doi.org/10.1016/j.isprsjprs.2022.06.002) · arXiv: [2206.13996](https://arxiv.org/abs/2206.13996)

- **机制差：** 平均目标只有几个到十几个像素时，IoU 对 1–2 px 位移会从 1 掉到 0；他们改用高斯 Wasserstein（NWD）做匹配/损失。这是检测分配问题，不是「换更大 ViT」。
- **迁移：** 你们是已知多边形上的存在性，不是自由检测。NWD 说明 **10 px 物体的监督信号本身极噪**——老师也看不清时，学生保真度上限会跟着老师走（小阵层老师自洽已是 0.65–0.67）。
- **针：** 不涨召回；支持「小阵单独一层、允许弃权」。与「再训一个头 / 超分救世」相反。

**C 类结论：** 共识已经够硬：10 原生像素过了可靠分类线。文献不支持用超分或换骨干救小阵。

仓库对照：[BRIEF-frame-state-distill-vs-geometry-2026-08-14](BRIEF-frame-state-distill-vs-geometry-2026-08-14.md) 已经用 R1 几何索引得到同一数字。本轮文献把这条从「仓库观察」升成「领域共识」。

---

## D. 序列级蒸馏，机制必须不同于 Path B

Path B 已杀：**全局池化向量 + 时间 Transformer + 区间键损失**（精确区间 +9.4 pp，过渡带 FN +72%）。

**8. Li, Abu Farha & Gall, 2021.** *Temporal Action Segmentation from Timestamp Supervision.* CVPR 2021.  
DOI: [10.1109/CVPR46437.2021.00826](https://doi.org/10.1109/CVPR46437.2021.00826) · arXiv: [2103.06669](https://arxiv.org/abs/2103.06669)

- **机制差：** 稀疏时间戳 + **置信度随离戳距离单调下降**的损失；边界是一等对象，不是逐帧分类的后处理。用的是帧级特征，不是全局池化后再上 Transformer。
- **时间轴：** 密视频帧，不是 GEHI。
- **GT：** 要时间戳；可用老师「最晚无 / 最早有」当弱戳，但老师在过渡带最吵。
- **针：** 这正是 Path B 反着走的方向——Path B 靠漏报过渡带去对区间键。单调边界损失的设计目标是压两端（FP 和 FN）。**有资格试，但不能做成池化向量上的区间分类。**

**9. Ju et al., 2022.** *Distilling Vision-Language Pre-training to Collaborate with Weakly-Supervised Temporal Action Localization.*  
arXiv: [2212.09335](https://arxiv.org/abs/2212.09335)

- **机制差：** 观察到分类预训练的定位**不完整（FN）**、VLP 定位**过完整（FP）**，用双分支交替蒸馏把互补错误对上。这和 Path B「一个头直接蒸馏老师区间」不是同一配方。
- **针：** 明确对着过渡带 FN/FP 的此消彼长，正是你们预注册没过的那条。迁移到 Gemini 老师时，需要老师在边界上的两种失败模式可分离——现在并不清楚。

**D 类结论：** 没有「池化 + Transformer + 区间键」的存活变体值得重开。还活着的只有：未池化 token / 连续时间路径 + **显式边界损失（同时压 FP 和 FN）** + 把 unusable 当第一输入。TAL 论文全假设密帧，接到每年几张 GEHI 上会掉一截。

---

## E. 独立真值，不是 VLM 自洽

**10. Rausch, Mayer, Arlt et al., 2020 / Mayer et al., 2022.** *An Enriched Automated PV Registry…* NeurIPS CCAI 2020；后续 *3D-PV-Locator*, *Applied Energy*.  
arXiv: [2012.03690](https://arxiv.org/abs/2012.03690) · 登记对照：德国 MaStR。

- **机制差：** 航拍检测对照**官方并网/登记簿**（容量、地址、重号、漏登），不是对照另一个 VLM。
- **关键限制：** 对照的是**有没有 / 多大**，不是安装日。MaStR 有登记日期，但论文没用它当 date accuracy。
- **针：** 给 S3 面板参考立方法：独立登记 × 检测，先报存在/容量，再谈日。CoJ 热水器当光伏那种失败，只有独立登记能抓住。

**11. Kruitwagen, Story, Friedrich et al., 2021.** *A global inventory of photovoltaic solar energy generating units.* *Nature* 598:604–610.  
DOI: [10.1038/s41586-021-03957-7](https://doi.org/10.1038/s41586-021-03957-7)

- **机制差：** 场站首次检出日对美国 EIA **投运日**；他们自己写检出日大约早两个月（开工 vs 投运）。
- **限制：** >10 kW、Sentinel-2 密序列、最早检测阈值，正是 BRIEF 禁止当主推的那一档。只作「独立行政记录怎么当 accuracy」的对照。
- **针：** 说明行政日 ≠ 影像日，中间有系统偏差；屋顶许可/并网同样会偏（许可早于上屋顶，并网晚于上屋顶）。

**数据源（不是方法论文，但 E 类必须点名）：**  
California DGStats NEM/CSI 并网申请（站点级、可回溯到 1980s）；NREL SolarTRACE / PII 周期（36 万+ 户用项目的许可、施工、验收、并网时间戳）。

**E 类结论：** 独立**存在/容量**登记很多；独立**屋顶安装日**被遥感论文拿来当 accuracy 的几乎没有。DeepSolar++ 的「安装年」GT 是人工看图，不是许可。这是论文表该走的洞，不是再跟 Gemini 比保真度。

地理限制：MaStR / NEM / SolarTRACE **不能**给开普敦屋顶打分。本仓库的独立通道仍是 S3 多模型面板（尚未派发）+ CoJ 外部稳健性（ISSUE-29，与学生线隔离）。这些欧美登记只进 Related Work 和方法对照，不当 CT accuracy 表。

---

## F. 哪几帧值得打分（昂贵观测）

**12. Houlsby, Huszár, Ghahramani & Lengyel, 2011.** *Bayesian Active Learning for Classification and Preference Learning.*  
arXiv: [1112.5745](https://arxiv.org/abs/1112.5745)（BALD）

- **机制差：** 选的是「期望降低参数后验熵」的样本，不是均匀/二分。接到「下一张 vintage」上：效用是安装区间后验的期望信息增益，不是帧分类精度。
- **时间轴：** 与传感器无关；GEHI 候选集是离散历史日期，正好是有限设计空间。
- **GT：** 离线 replay 可用老师标签当模拟观测，零配额。这就是 BRIEF 说的 S2。
- **针：** 把调用从每锚 2.56–2.69 次压到信息量最大的那几张，应优先打过渡带两侧，而不是再均匀撒。不会直接改 FP/FN，改的是同样配额下的区间宽度。

BALD 在高分卫星变化检测上有过一次应用（WorldView，Crete 城郊，2022 书章，HAL-05189866），比随机采样强，但是**选训练图**不是选历史 vintage。

**F 类结论：** 理论现成，GEHI vintage 选择论文是空的。这是最便宜、还没做的方法新颖性，不碰骨干纪律。

仓库对照：Leg-S S2 已写好 EIG 公式、GO = 调用降 ≥20% 且 containment 不降、数据源 = Top-52 verdict store 离线 replay。ISSUE-14（学生选窗、老师打分）是生产形态的表亲，依赖学生先能粗排；S2 不依赖学生。

---

## G. 配对是否只在可训练骨干上成立

**13. Dong, Hu, Wang, Chen & Meng, 2026.** *PeftCD: Leveraging Vision Foundation Models with Parameter-Efficient Fine-Tuning for Remote Sensing Change Detection.* IEEE JSTARS.  
DOI: [10.1109/JSTARS.2026.3679260](https://doi.org/10.1109/JSTARS.2026.3679260) · arXiv: [2509.09572](https://arxiv.org/abs/2509.09572)

- **机制差：** 共享权重 Siamese VFM，**冻骨干 + LoRA/Adapter** vs 纯冻编码器。多数据集上 LoRA 稳定高于 frozen（例如 SYSU-CD / WHU-CD / LEVIR-CD）。这正好是 A″「冻嵌入上配对输给逐帧对照」之后，D2 只授权一轮 LoRA 时需要的机制假设。
- **限制：** 任务是 LEVIR/WHU 式建筑变化掩膜，不是「已知小物体在这张 GEHI 里出现了没有」；也不是跨传感器模板（你们的 Vexcel×GEHI 余弦 0.31 那个洞 LoRA 未必能补）。PeftCD 的骨干之一是 DINOv3，与本仓库「骨干更换仍 veto」不冲突——D2 只开 LoRA。
- **针：** 给 LoRA 名额一个可证伪假设——若 LoRA-Siamese 仍赢不了参数匹配的逐帧头，名额不该花在配对上。

**14. Seydi, Hasanlou & Amani 一类冻编码器消融（ISPRS Annals 2023 等）** 以及 Swaminathan et al. 2025 IEEE Access：Siamese 里冻深层、只训头，F1 明显掉；解开深层才回得来。与 A″ 一致：**冻特征上的模板匹配不是没做，是做了输了。** 文献支持「配对要解开骨干」，**不支持**「解开之后一定赢逐帧」。

**G 类结论：** 「必须 LoRA」有证据；「LoRA 之后配对仍优于逐帧存在性头」没有屋顶 PV / GEHI 证据。一轮 LoRA 若要花，先写预注册：同传感器最新有板模板 × LoRA，对照 = 同容量逐帧 LoRA 头，主指标过渡带 FP/FN，不是 IoU。

---

## 一页结论（按 BRIEF §7 口径）

| 簇 | 还有没被 §4 杀掉的机制？ | 判决 |
|---|---|---|
| **A** | 有：加权不规则 jump（JUST）、连续时间路径（Neural CDE）、变点后验（BOCPD）。没有「过渡带当预测对象 + GEHI 稀疏」的成品。 | 可改**解码器**（软区间/生存/后验），不要重开硬 MAP，也不要重开 Path B。仓库已有 τ 后验 + Turnbull；缺的是不可用退出似然。 |
| **B** | 有拒识/训练期 abstain；无屋顶 PV 三态蒸馏先例。 | 借校准，不换骨干。质量头是标签+拒识问题。 |
| **C** | 共识：纹理分类 ~11 px 才到 75%；你们 10 px 已过那条线。小阵若只是色块，高对比时 10 px 可以够。 | **不要整层弃权，也不要整层硬判。** 超分/换 ViT 关掉。口径 = 能看成色块再打分。 |
| **D** | 边界单调损失、双分支互补蒸馏与 Path B 不同。全是密视频。 | 时序头不要按原配方重开；若开必须未池化 + 显式边界损失同时压 FP/FN。 |
| **E** | 登记簿很多，但几乎没人拿屋顶**安装日**当 accuracy。 | 文献空在「日」不在「有没有」。论文 Related Work 走 MaStR / NEM / SolarTRACE；CT 表仍走 S3/CoJ。 |
| **F** | BALD/EIG 现成；「下一张 GEHI vintage」是空的。 | **最便宜的方法新颖性。** 离线 replay，零配额。即已写的 S2。 |
| **G** | LoRA 让 Siamese 超过冻编码器，有公开消融。LoRA 后是否赢逐帧头，无本任务证据。 | LoRA 名额可花在配对上，但必须预注册对照逐帧 LoRA；输了就关。 |

---

## 仍空的洞（Related Work 该写的空白）

被关掉的是：冻卫星 ViT、硬 MAP、池化时间 Transformer、冻嵌入 Siamese、配准主修复、用老师自洽冒充真值。文献里**还没有**一篇方法同时处理这四件事——（1）一年只有几张的不规则 RGB vintage，（2）稀有且被标噪的 unusable 态必须能退出似然，（3）约 10 个原生像素的小阵在看不成色块时弃权、看成时才打有/无，（4）监督是自洽仅 ~0.77 的教师，评价要落到独立许可/并网日。A–G 各自有零件，没有组装图。若这一轮 8–15 篇在机制上仍落回 §4，应停，不要为「查了文献」重开 Path B 或换骨干。

本轮判定：零件还活着，组装图仍空。**停第三轮文献。** 下一步不是再搜，是把还没做的便宜零件接到已有 S1–S6 / R4 上。
