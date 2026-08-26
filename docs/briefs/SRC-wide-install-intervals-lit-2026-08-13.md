# 宽安装区间如何杀死 DiD：计量 + 遥感文献速览（Theo Gao）

**日期**：2026-08-13（Pacific/Auckland，UTC+12）  
**问题设定**：屋顶 PV 安装日仅由稀疏 Google Earth Historical Imagery（GEHI）vintage 推断为区间 \([t_{\mathrm{lower}}, t_{\mathrm{upper}}]\)；经济学侧已在 Johannesburg、H3 级聚合上尝试 DiD/RD/政策分析，但区间过宽 → treatment timing 不确定 → 结果弱。季/月面板更是目标。  
**范围**：优先 2018–2026；仅收录可核验 DOI / arXiv；**不编造**。DeepSolar++ 已知，文中仅作对照、不占主位。  
**方法**：WebSearch + arXiv/出版社页面交叉核实。

---

## 1. 短诊断：为什么宽区间会同时伤 power 与 bias

把“真实安装时刻”记为 \(E_i^\*\)，观测到的只是 \(E_i^\* \in [L_i, R_i]\)。若把区间中点、上界或下界硬编码成点估计 \(\hat E_i\)，再跑 staggered DiD / event study，则：

1. **事件时间错位（mistimed treatment）**  
   真实已 treat 的期被标成 control（或反过来）→ 经典 DiD 的 \(D_{it}\) **误分类**。Denteh & Kédagni、Augustin–Gutknecht–Liu 等均表明：标准 DID/TWFE/部分 modern staggered 估计量在 timing 误分类下一般有偏；偏差可衰减，**也可改号**。

2. **预趋势被污染**  
   区间宽度 \(W_i = R_i - L_i\) 越大，越多“名义 pre 期”其实已暴露于 treatment（或名义 post 期仍未安装）。Event-study 前导系数与平行趋势检验失效；看起来像“无效应/负效应”可能只是 timing 噪声。

3. **Power 塌缩**  
   在 H3 × 季度/月度上，宽区间把采纳冲击 smear 到多个 period → ATT 被稀释；聚合层上还混入“何时开始 treat 完全未知”的单元 → 有效对照/处理对比减少。Johannesburg 面板弱结果，与此机制一致，不必先怀疑政策本身无效应。

4. **RD 更脆**  
   政策边界附近若 running variable 或采纳时点带测量误差，断点被平滑；除非误差小且分类仍正确（见 RD 测量误差文献），否则局部效应难识别。宽区间本质上是**大支撑的 timing 误差**。

5. **两条出路必须分清**  
   - **缩区间（遥感/CV）**：真的减少 \(W_i\) —— 这才恢复季/月 DiD 的信息量。  
   - **换估计量（计量）**：在给定宽区间下做 misclassification / fuzzy / bounds / 敏感性 —— **不缩小 \(W_i\)**，只改变可报告的因果对象与置信集。  
   经济学团队若只换 Callaway–Sant’Anna / Sun–Abraham 而不处理 timing 误差，通常不够。

---

## 2. 核实论文（9 篇）：A 计量 / B 遥感

### A. 计量：误时点、误分类、模糊采纳、部分识别

#### A1. Denteh, A. & Kédagni, D. — *Misclassification in Difference-in-Differences Models*  
**出处**：arXiv:2207.11890（2022 首发，持续更新；截至检索时仍为工作论文）  
**URL**：https://arxiv.org/abs/2207.11890 · https://doi.org/10.48550/arXiv.2207.11890  

**摘要（3–4 句）**：系统研究 DiD 中 **treatment 被误分类**时识别什么。误分类来源明确包括“政策时点模糊”与“从辅助数据推断 treatment”。标准 DID 估计量一般有偏，等于正确分类与误分类子群 ATT 的加权平均；权重可为负 → **符号可错**； nondifferential 情形常衰减。给定误分类程度信息时给出 ATT **bounds** 与敏感性分析，并配模拟与应用。  

**对本问题可借**：把 GEHI 区间映射到 \(D_{it}\) 时，本质就是 timing-induced misclassification。应用其 bound / sensitivity：用区间宽度或“中点规则 vs 保守上界规则”差异标定误分类率上界，报告 ATT 识别集，而不是只报一个点估计。

---

#### A2. Augustin, C., Gutknecht, D. & Liu, C. — *Staggered Adoption DiD Designs with Misclassification and Anticipation*  
**出处**：arXiv:2507.20415（2025）  
**URL**：https://arxiv.org/abs/2507.20415 · https://doi.org/10.48550/arXiv.2507.20415  

**摘要（3–4 句）**：把 **staggered adoption** 下 **时变 treatment 误分类**与 **anticipation** 放进同一框架。证明 TWFE 与部分面向异质性的 staggered 估计量对常用参数有偏；给出修正估计量，分别恢复“观测到的切换单元 ATE”与（在额外同质性假设下）“真实切换单元 ATE”。并提供基于矩等式的检验，探测预趋势破坏 vs 误分类/预期的时点与幅度；印尼 CBT 反作弊政策应用显示预期概率可达约 15–30%。  

**对本问题可借**：你们的设定几乎是“安装日观测滞后/提前一个或多个 vintage”的 **lagged coding of adoption**。可直接借用：区分 \(\delta^S\)（按观测切换）与 \(\delta^{S^*}\)（按真实切换）；用他们的 pre-period 矩检验在 H3 季度面板上做 specification；若误分类主要集中在 \([L_i,R_i]\) 内，可尝试其 bias-corrected DID。

---

#### A3. Negi, A. & Negi, D. S. — *Difference-in-Differences With a Misclassified Treatment*  
**出处**：*Journal of Applied Econometrics* 40(4):411–423, 2025  
**URL**：https://doi.org/10.1002/jae.3116 · 预印本 https://arxiv.org/abs/2208.02412  

**摘要（3–4 句）**：研究 **内生/差分式** treatment 误分类下的 ATT。指出误分类既混淆“真处理者”，也可使平行趋势在观测 \(D\) 上失败（即使对潜在 \(D^*\) 成立）。针对常见的 **one-sided misclassification**，提出两步估计（partial observability probit + 排他限制）以点识别潜在 ATT，并用于印度项目误报/错配应用。  

**对本问题可借**：若把“区间尚未关闭就标 treat”或“只在上界后才标 treat”看成 **单侧误分类**，且能找到与误分类相关、与潜在结果趋势条件独立的工具/排他变量（例如影像可判读性、云量、分辨率档位），可尝试点识别修正；否则仍退回 A1 的 bounds。注意：排他限制在遥感 timing 误差上往往难找 —— 这本身也说明 **缩区间优先于强点识别假设**。

---

#### A4. de Chaisemartin, C. & D’Haultfœuille, X. — *Fuzzy Differences-in-Differences*  
**出处**：*Review of Economic Studies* 85(2):999–1028, 2018  
**URL**：https://doi.org/10.1093/restud/rdx049  

**摘要（3–4 句）**：标准 “Wald-DID”（结果 DID / treatment DID）在 **模糊设计**（处理组与对照组 treatment 率都可能变动）下识别 LATE，需要强同质性。提出不依赖效应同质性的替代估计量（在对照组 treatment 率稳定时可点识别）；对照组也变动时则走向 **部分识别**。配套 Stata `fuzzydid`。  

**对本问题可借**：H3 单元上“区间未闭合”意味着季度采纳强度是 **模糊的**（单元内部分屋顶可能已装、部分未装；或单元被标 treat 的概率随规则变化）。可把面板改造成 fuzzy DiD：用“上界已过的安装份额 / 区间闭合流”作强度，避免伪精确 0/1。模糊度越大，越应报告 bounds 而非单一 Wald-DID。

---

#### A5. Rambachan, A. & Roth, J. — *A More Credible Approach to Parallel Trends*  
**出处**：*Review of Economic Studies* 90:2555–2591, 2023  
**URL**：https://doi.org/10.1093/restud/rdad018 · 软件 HonestDiD  

**摘要（3–4 句）**：不要求平行趋势精确成立，而是限制 post 期趋势偏离相对 pre-trend 的幅度；因果参数变为 **部分识别**，并给出均匀有效推断。适用于 DiD / event study；推荐做敏感性分析（不同偏离限制下结论是否稳健）。  

**对本问题可借**：宽区间会制造虚假 pre-trend 或掩盖真 pre-trend。即便修正了 \(D_{it}\)，仍应用 HonestDiD 报告“在多大平行趋势松弛下效应仍显著”。**注意**：它处理的是趋势假设违背，**不是** interval-censored treatment timing 本身 —— 是互补稳健层，不是 timing 误差的专用解。

---

### B. 遥感 / CV：在稀疏多时相下收紧 first-appearance 区间

#### B1. Yu, R., Han, P., Myers-Dean, J., Wolters, P. & Bastani, F. — *OPTIMUS: Observing Persistent Transformations in Multi-temporal Unlabeled Satellite-data*  
**出处**：arXiv:2506.13902（WACV 2025 版本亦见）  
**URL**：https://arxiv.org/abs/2506.13902 · https://doi.org/10.48550/arXiv.2506.13902  

**摘要（3–4 句）**：自监督方法：若模型能从打乱时间的序列中恢复长期顺序，则存在 **持久变化**（相对季节噪声）。用 Siamese 网络对 query 相对锚点的时间邻近性打分，再对分数序列做 **单调性/pivot（变点）度量** 以判断是否发生持久变化，并可粗定位时间。在标注评估集上 AUROC 从基线约 0.56 提到约 0.88。  

**对本问题可借**：与你们已有的 **Turnbull prior + changepoint decoder + presence 分数序列** 高度同构。可借：(i) 用 Spearman/pivot 作为 presence 分数序列的单调质量门（过滤蒸馏失败/不可用序列）；(ii) 自监督排序预训练减轻对 Gemini 标签的依赖；(iii) 把“分数是否单调跃迁”当作区间可信度权重，供经济学侧加权或样本筛选。

---

#### B2. Madani, S., Chellappa, R. & Patel, V. M. — *DiffRegCD: Integrated Registration and Change Detection with Diffusion Features*  
**出处**：arXiv:2511.07935（2025）  
**URL**：https://arxiv.org/abs/2511.07935 · https://doi.org/10.48550/arXiv.2511.07935  

**摘要（3–4 句）**：多数 CD 假设已配准；视差/视角/长时差导致错位 → 假变化。DiffRegCD 联合稠密配准与变化检测：冻结扩散特征 + 将对应关系写成 Gaussian-smoothed classification，在 LEVIR/WHU 等与街景数据上显示对诱导错位更稳健。  

**对本问题可借**：GEHI 跨 vintage **错位是“假 absent / 假 present”的主因之一**，会 **人为拉宽** \([L,R]\)。在 presence 打分前做屋顶锚点级配准（或联合 reg+presence），专门压制“面板还在但像移了”的假 absent —— 这是 **直接缩区间** 杠杆，而非换 DiD 公式。

---

#### B3. Cullerton, M., Zhu, Z., Qiu, S., Rittenhouse, C. D. & Suh, J. W. — *Back in time: A novel time series and deep learning framework for mapping solar installations*  
**出处**：*Science of Remote Sensing* 12:100322, 2025  
**URL**：https://doi.org/10.1016/j.srs.2025.100322  

**摘要（3–4 句）**：先用 U-Net 在 Sentinel-2 上检出大型光伏场，再对 Landsat **反向运行 COLD**（从近到远）利用设施稳定性重建安装年；相对参考年的平均差约 0.125 年。证明“检测定位 + 稠密时序断点定年”管线。  

**对本问题可借**：屋顶尺度 + GEHI 稀疏度不同于 Landsat/utility-scale，**不能直接搬 COLD**。可借的是设计哲学：**(i) 空间锚点先固定；(ii) 在锚点上跑面向持久变化的时序模型；(iii) 用稳定性/单调性约束定年**。若部分 JHB 屋顶在 Sentinel/Plane 有更密时序，可作 **子集缩区间** 的外部锚定，再校准 GEHI 区间。

---

#### B4. Robinson, C., Ortiz, A., Kim, A., et al. — *Global Renewables Watch: A Temporal Dataset of Solar and Wind Energy Derived from Satellite Imagery*  
**出处**：arXiv:2503.14860（2025）  
**URL**：https://arxiv.org/abs/2503.14860 · https://doi.org/10.48550/arXiv.2503.14860 · https://github.com/microsoft/global-renewables-watch  

**摘要（3–4 句）**：在 PlanetScope **季度**底图上全球分割公用事业光伏/陆上风电，以 **首次出现季度** 作为建设日期；强调 data-centric 清洗 OSM 标签、硬负例与假阳性过滤。国家装机加总与 IRENA 相关性高（太阳能 \(r^2\approx0.96\)）。  

**对本问题可借**：展示“固定时间网格 + first-appearance”如何服务政策/容量时间序列。对你们：**(i) 主动选择/请求能切开宽区间的 vintage（active vintage selection）比均匀扫更重要**；(ii) 假阳性过滤与标签清洗直接决定首次 present 是否可信；(iii) 公用事业季度网格提醒：屋顶若只能稀疏 GEHI，经济学面板应 **原生保留区间**，不要假装有 Planet 级季度真值。

---

> **已知对照（不占名额）**：Wang et al., 2022, *DeepSolar++*, *Joule*, https://doi.org/10.1016/j.joule.2022.09.011 — HR 参考 + LR 历史的 pseudo-Siamese presence → 安装年；你们已熟悉。Gemini 为当前 scorer、学生蒸馏曾未过 fidelity gate —— 与 DeepSolar++/OPTIMUS 路线一致的改进方向是 **可复现的本地 presence scorer + 单调/变点解码**，而非再换一个闭源 VLM。

---

## 3. 建议“先读这 5 篇”（有序）

| 顺序 | 论文 | 为什么先读 |
|:---:|:---|:---|
| 1 | **A2 Augustin et al. (2025)** arXiv:2507.20415 | 与“观测安装切换 ≠ 真实切换”最贴；有修正估计量 + 检验，可直接改 Johannesburg staggered 规格 |
| 2 | **A1 Denteh & Kédagni** arXiv:2207.11890 | 最短路径理解：宽区间 → \(D_{it}\) 误分类 → 衰减/改号；学会用 bounds 做敏感性 |
| 3 | **B1 OPTIMUS** arXiv:2506.13902 | 与 Turnbull+changepoint+presence 分数最同构；指导如何用单调性收紧/加权区间 |
| 4 | **B2 DiffRegCD** arXiv:2511.07935 | 配准是缩区间的高杠杆；减少假 absent 往往比换 loss 更赚宽度 |
| 5 | **A4 Fuzzy DiD** doi:10.1093/restud/rdx049 | 当区间无法再缩时，把 H3 采纳做成模糊强度 + 部分识别，避免伪精确事件研究 |

（补读：A5 HonestDiD 作平行趋势敏感性；B3/B4 作定年管线对照；A3 仅在有可信排他限制时考虑点修正。）

---

## 4. 具体 Playbook：哪些杠杆真缩宽，哪些只改估计量

### 4.1 真正缩小区间宽度 \(W_i = R_i-L_i\) 的杠杆（优先做）

| 杠杆 | 机制 | 预期对 \(W_i\) | 备注 |
|:---|:---|:---|:---|
| **Active vintage selection** | 在当前 \((L,R)\) 内插入能二分的新 GEHI/其他源日期 | **直接砍半或更好** | 价值函数 ≈ 期望区间缩减 × 该屋顶对 H3 面板的权重；优先宽区间 + 高装机面积单元 |
| **配准 / 视角校正（B2）** | 减少错位导致的假 absent → 更早可信的 present 或更晚可信的 absent | 中–高 | 屋顶锚点 chip 级；比整幅仿射更贴 |
| **更稳的 presence 分数 + 降 unusable（B1 / DeepSolar++ 思路）** | unusable 不参与收紧；提高可判 vintage 比例 | 中 | Gemini→可复现学生模型需过 **fidelity gate**；失败则宁用 Gemini+人工抽检，勿用烂蒸馏撑月度面板 |
| **单调 / 变点解码质量门（已有 Turnbull+decoder）** | 剔除非单调、多峰、低置信序列；对通过者收紧 support | 中（对通过子集） | OPTIMUS 的 pivot/Spearman 可作第二门控 |
| **多源时序锚定（B3/B4 哲学）** | 对少数屋顶用更密传感器定年，校准 GEHI 偏差 | 对锚定子集高；可外推纠偏 | Utility-scale CCDC **不能**直接当屋顶解 |

### 4.2 不缩小 \(W_i\)、只改变估计量/报告方式的杠杆（区间已定时）

| 杠杆 | 做什么 | 不做什么 |
|:---|:---|:---|
| **保守 / 宽松 \(D_{it}\) 规则** | 主设定：仅 \(R_i \le t\) 才 treat；稳健性：\(L_i \le t\) 或中点 | 不要只报中点当真理 |
| **Misclassification bounds（A1）** | 用区间规则差异估计误分类幅度 → ATT 上下界 | 不是点估计替代品 |
| **Staggered + mistiming 修正（A2）** | 估计观测切换 vs 真实切换参数；做误分类检验 | 假设要核对（误分类是否主要在切换前一期等） |
| **Fuzzy DiD / 强度（A4）** | H3 内“已过上界的面积份额”作连续/模糊处理 | 对照组强度若也漂移 → 走部分识别 |
| **HonestDiD（A5）** | 平行趋势敏感性 | 不修正 timing 误差本身 |
| **样本修剪** | 主结果仅用 \(W_i \le\) 1–2 个季度的单元；宽区间作 bounds 层 | 选择样本 → 外推需谨慎 |
| **聚合时间** | 若月度不可识别，退回 **半年/年** 事件研究，诚实降频 | 假月度面板伤害可信度 |

### 4.3 建议操作顺序（给 CV + 经济学联合）

1. **冻结区间生成器**（权重哈希、标签快照、规则版本）—— 经济学 replication 硬约束。  
2. **报告 \(W_i\) 分布**（屋顶级与 H3 级）：宽度分位数、与政策窗口重叠比例。  
3. **CV 冲刺缩宽**：配准 → active vintage → presence 门控（B2→active→B1）。  
4. **经济学双层结果**：  
   - Layer A：窄区间子样本 + 保守 \(D_{it}\) + modern staggered；  
   - Layer B：全样本 + A1/A2/A4 bounds 或模糊强度。  
5. **全程 HonestDiD** 作平行趋势敏感性（A5）。  
6. **勿**在宽区间上硬上月度 event-study 并宣称政策无效应。

---

## 5. 明确说明：区间删失的是 **treatment timing** 时，计量文献偏薄

**结论：薄，而且容易和“结果区间删失”文献混淆。**

- 检索到的相近工作，多数落在：  
  - **treatment 误分类 / 时点模糊**（A1、A2、A3）；  
  - **模糊采纳强度**（A4）；  
  - **预期 / 预趋势敏感性**（A2、A5）；  
  - 以及 **结果为区间值** 的 DiD（例如 arXiv:2512.08759 *Difference-in-Differences with Interval Data* —— 处理的是 **outcome 区间**，**不是** treatment 日期区间；**不可直接当 timing 解**）。  
- **几乎没有**（截至 2026-08-13 公开可核验文献）把“每个单元观测到 \([L_i,R_i]\) 的吸收采纳时刻、再跑 staggered ATT”写成完整、可即用的标准工具箱（akin to Turnbull-for-outcomes）。  
- Survival 的 Turnbull NPMLE 处理的是 **结果/事件时间** 的区间删失分布估计，你们已在 CV 侧用作 prior；把它接到 **DiD 的 group-time ATT** 仍属研究前沿/需自建，而非现成 off-the-shelf。  

**含义**：经济学侧应 **诚实做 misclassification / fuzzy / bounds**，同时把主要增益押在 **遥感缩区间**；不要等待“区间删失 treatment 的完美 DiD 论文”再推进 Johannesburg 面板。

---

## 6. 与本项目的一句话对齐

> Johannesburg 屋顶 PV 的 GEHI 区间太宽，使 H3×季/月 DiD 同时遭受 **误分类偏误** 与 **功效稀释**；计量上优先 A2+A1（修正/界）与 A4（模糊强度），遥感上优先 **配准 + 主动选 vintage + 单调 presence 门控** 真缩 \(W_i\)。DeepSolar++ 已是任务同构基线；下一步增益不在再调 Gemini，而在可复现 scorer 与区间感知的因果报告分层。

---

## 附录：核实状态

| ID | 标识符 | 核验方式 |
|:---|:---|:---|
| A1 | arXiv:2207.11890 | arXiv abs + html |
| A2 | arXiv:2507.20415 | arXiv abs + html（全文） |
| A3 | doi:10.1002/jae.3116 | Wiley / RePEc / arXiv:2208.02412 |
| A4 | doi:10.1093/restud/rdx049 | Restud / RePEc |
| A5 | doi:10.1093/restud/rdad018 | Restud |
| B1 | arXiv:2506.13902 | arXiv abs + html |
| B2 | arXiv:2511.07935 | arXiv abs + html |
| B3 | doi:10.1016/j.srs.2025.100322 | ScienceDirect / 二次目录作者核对 |
| B4 | arXiv:2503.14860 | arXiv abs + html |
| 对照 | doi:10.1016/j.joule.2022.09.011 | DeepSolar++（已知，不占名额） |

*未纳入任何无法提供稳定 DOI/arXiv 的条目。*
