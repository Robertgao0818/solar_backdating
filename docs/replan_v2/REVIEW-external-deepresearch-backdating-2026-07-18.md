# REVIEW — 两份外部 deep research 方案 vs. repo 现状 (2026-07-18)

评审两份外部 deep research 报告（ChatGPT deep research、Claude 网页版 deep research），
把它们的诊断与建议逐项对照 solar_backdating repo 在 RUN 2 fullscan 之后的实证进度，
判定每条建议属于 **done / in-progress / 已证伪 / 未做且值得 / 未做且不适用**，
并给出一个合并后的行动优先级清单。

本文是**归档 review**，不修改任何既有文档，不改 TRACKER。所有裁定证据引用 repo 内既有
DATA/ISSUE 文档的具体节号。

---

## 0. 来源与范围

### 评审对象（外部输入）

| 报告 | 分享链接 | 存档日期 | 结构 |
|---|---|---|---|
| ChatGPT deep research | https://chatgpt.com/s/t_6a5aa0f536e081918502885f39e8f4b8 | 2026-07-18 | 九节技术报告 + 四级工程优先级（本文记为 **GPT-P1…P4**） |
| Claude deep research | https://claude.ai/share/64040d7e-b658-4b22-800a-dd0db9bdfd75 | 2026-07-18 | 总体诊断 + 六问逐条 + 七段落地路线图；文末 52 条参考文献已恢复 |

### 对应的 repo 环节

两份报告针对的是同一条管线：**给定 2024 census（CoJ / Vexcel 15cm）已知的 PV polygon，
向历史卫星 vintage（GEHI）回扫，用 Gemini 逐帧判 present/absent，changepoint decoder 出安装区间。**
即本 repo 的 v2 五阶段 program（`docs/install_date_optimization_v2_prd.md` +
`docs/replan_v2/TRACKER.md`）与 RUN 2 fullscan 后的七份 DATA 文档：

- `DATA-fullscan-run2-parallax-audit-2026-07-17.md`（off-nadir / 位移审计，核心风险文档）
- `DATA-fullscan-run2-recenter-pilot-2026-07-17.md`（SP+LightGlue NO-GO + stale-offset bug 发现）
- `DATA-fullscan-run2-rescan-pilot-2026-07-17.md`（A5 offset-fix 收益量化）
- `DATA-fullscan-run2-census-bucket-audit-2026-07-17.md`（71% 盲区 off_target vs model_miss）
- `DATA-fullscan-run2-qa-sample-2026-07-17.md`（n=100 marker 渲染 QA）
- `DATA-fullscan-run2-install-intervals-2026-07-17.md`（区间推断 + 701 行 inverted-interval 缺陷）
- `DATA-fullscan-run2-vs-june-2026-07-17.md`（run2 vs June 生产交付物对比）

### 提取 caveat（如实记录，影响信度）

- **GPT 快照缺用户原始提问轮**：分享载荷里没有 `role` 字段，也没有用户 prompt 全文；
  只捕获到一条 assistant 报告（15,846 字符）。用户背景（41k 站点、24m/48m chip、56%
  census 类、12% present→absent 矛盾、Johannesburg、无 DSM）是**从 assistant 回复反推**的，
  不是用户原话。报告的 `citeturn` 引用标记未能与 URL 建立映射，只能列出正文点名的系统/数据集
  （DeepSolar++、BONAI/LOFT、BANDON/MTGCD-Net、LightGlue、Efficient LoFTR、RoMa、DUSt3R、
  EarthMarker、ECC、RCDT），无法逐条给出精确文献链接。
- **Claude 快照**：用户消息原文有单词融合/截断瑕疵（"co-registerframe"、"absennsus"、
  "perlocation" 等，经两次独立 headless render 复现，判为用户原始消息里已有的编辑残留，非提取误差）；
  **assistant 报告本身完整**，52 条参考文献 URL 已从 DOM 恢复（见提取文件文末列表）。
  两次搜索工具调用的 query 文本未恢复。
- **scratchpad 提取全文路径**（本 session，非 repo 内）：
  - `…/scratchpad/gpt-share-extracted.md`
  - `…/scratchpad/claude-share-extracted.md`
  - `…/scratchpad/repo-progress-snapshot.md`（进度快照，本 review 的 repo 侧输入）

---

## 1. 总体判断 (TL;DR)

**两份报告独立收敛到与 repo 相同的根因方向，但都不知道 repo 的真根因，也都在"第一顺位药方"上
猜错了本 repo 的病。** repo 已经比两份报告走得更远的地方，恰恰是它们最强调的地方；两份报告的真
增量，集中在 repo 尚未做的**观测 schema 层**。

- **两份报告都对了的方向**：把"固定坐标 marker 内没看到 PV" 当作 "absent" 是错误的观测模型；
  56%/71% 的"census 前安装"类里混入了大量**不可判**样本（配准失败/视差/坏瓦片被机械转成
  false-absent）。这一诊断被 repo 独立实证（census-bucket audit：盲区问题率 33–80%，且
  **0 例 model_miss**——Gemini 本身可靠，错在 marker 几何）。

- **两份报告都猜错的第一顺位药方**：GPT-P1 与 Claude 都把"更强/逐 vintage 重配准"排在最前。
  但 repo 的 recenter-pilot 已经把 SP+LightGlue 全库重配准试过并判 **NO-GO**——因为在本 repo 的病
  上，registration 与 placement 是**正交失败模式**（盲区 0/80 anchor 超半盒）。repo 独立定位到
  真根因是一个 **repo 内 stale `target_offset_x_m/y_m` bug**（~73% 的 off-structure 由它单独解释），
  修复 = offset 归零（免费），rescan-pilot 已量化 **+43pp 恢复**。外部报告都不知道这个 bug 的存在。

- **两份报告的真增量（repo 尚未做、值得做）**：观测 schema 从二态升到**三/四态**
  （present / absent / uninformative（未定位到目标）/ corrupt），且 **absent 必须以
  building_found & target_localized 为前提**；配合 reference-conditioned 提示（2024 Vexcel 参考图 +
  历史帧并排）。这是 **A5 offset-fix 之后**的正确下一层防线，不是 A5 的替代。

**逐报告一句话：**

- **GPT 报告**——保留：四状态观测、absent 前置 localized、corrupt-tile gate、reference+context
  三联图、把 all-absent 重命名 unresolved（这些是 P0/第一优先级，正是 repo 的真增量）。必改：把
  "五级配准级联"降为**第二顺位**（repo 已证明配准不是盲区主因，先修 offset bug）。已被超越：
  "SP+LightGlue 作主配准" repo 已 NO-GO；病因诊断（归因配准/视差）repo 实证只对了一半（主因是
  自家 offset bug + 上游 segmentation 残差）。

- **Claude 报告**——保留：三值观测 HMM（含 uninformative）、reference-conditioned 屋顶模板追踪、
  Wayback 瓦片哈希去重 + 逐瓦片拍摄日期、corrupt-tile 前置门控、Google Open Buildings 2.5D
  Temporal 高度分层（独有增量）。必改：AROSICS 式逐 vintage 位移场同样应排在 offset-fix 之后。
  已被超越/需澄清：把"硬性 changepoint 换成概率 HMM"说成缺失并不准确——repo Phase-0 已有概率化
  changepoint 后验 + Turnbull survival decoder（生产默认），真正的 delta 只在**观测层加
  uninformative 一档**。

---

## 2. 两方案摘要

### 2.1 ChatGPT 报告（按其四级工程优先级组织）

报告核心诊断：**问题不是"Gemini 识别能力不够"，而是观测模型定义错了**——"固定坐标框内没看到 PV"
≠ "该建筑没有 PV"。有效 absent 必须同时满足：影像有效、找到正确建筑、屋顶可见、搜索完整候选屋顶
范围、仍无 PV。九节正文之后落到四级优先级：

- **GPT-P1（第一优先级，无需训练）**：(1) 二值观测改 **P/A/U/X** 四态（Present / Absent-on-
  localized-visible-roof / Target-Unlocalized / Image-Unusable）；(2) absent 必须以
  `target_localized=true` 为前提；(3) 加程序化 corrupt-tile gate（匀色/熵/方差/perceptual
  hash/四象限纹理，输出 VALID/BLURRED/BLANK_OR_CORRUPT/PARTIAL_TILE/HEAVILY_OCCLUDED）；
  (4) 去掉固定 crosshair，改 reference(2024 Vexcel)+context+candidate-roof 三联图；(5) 对
  present→absent 矛盾自动重配准或标 U；(6) all-absent 类重命名 unresolved。文中强调这一步的收益
  很可能大于升级 Gemini tier。
- **GPT-P2（重构配准）**：72–120m registration chip（大范围配准、小范围判断）；五级配准级联
  ——phase correlation 粗种子 → SuperPoint+LightGlue 主流程 → Efficient LoFTR fallback → RoMa
  处理 5–15% 疑难帧 → ECC 仅 refinement；把各帧组成"配准图"，对 2024 + 相邻帧 + 最相近 2–3 帧
  联合鲁棒求解，cycle consistency 找错误边；每帧输出 registration covariance/grade。
- **GPT-P3（屋顶级视差）**：地面配准与屋顶定位分开（d ≈ h·tanθ）；footprint 只作身份/搜索先验
  不作固定 mask；用 41k 站点估"无 DSM 视差场"（每 vintage/tile 标定主视差方向 + 沿方向搜多高度
  假设）；BONAI/LOFT 风格 roof-to-footprint offset head；阴影测高只作 LOW/MID/HIGH-RISE 分层。
- **GPT-P4（替换识别器）**：训练 reference-conditioned Siamese PV 模型作主打分器，Gemini 退为
  困难样本 adjudication / 可解释 QA / 模型间冲突复核。
- **端到端先例**：DeepSolar++ 最近邻（OOD 帧不进时间推断、低分辨率走 target-reference Siamese、
  时序决策与单帧分类分离）；BONAI/LOFT（roof-footprint offset）、BANDON/MTGCD-Net（off-nadir
  时序 roof matching）。GPT 明确指出没有成熟发表系统覆盖"Google/Esri 历史底图逐 vintage 回溯屋顶
  小目标 + 局部配准 + off-nadir 视差"的组合。
- **验证集**：建议 1,500–2,000 sites 分层 gold set + synthetic displacement stress test；核心指标
  应是 roof-localization success、false-absent given localized roof、coverage、contradiction rate，
  而非 per-frame accuracy。

### 2.2 Claude 报告（按其六问 + 路线图组织）

总体诊断：**证据锚点选错了**——把"固定地理坐标 + marker"当真值锚。最高杠杆不是把每帧配得更准，
而是把判定单元从"坐标框内"改成"追踪到的那个屋顶"（逐站点 2024 Vexcel 模板，在历史帧有界搜索窗内
稠密模板匹配，让 marker 跟屋顶走，同时吸收配准误差与视差）。第二关键修复：硬 changepoint 换带
非对称观测噪声的 HMM，schema 层区分 uninformative 与 absent。

- **问 1（多时相配准）**：芯片尺度以平移为主，稠密/区域法优于稀疏关键点法。**AROSICS**（频域相位
  相关 + MSSIM 校验 + 平移 RANSAC）作逐 vintage 局部位移场；**CFOG**（定向梯度 + 3D FFT 模板匹配）
  作逐站点首选，天然抗排屋重复纹理混叠。学习型匹配器（SP+LightGlue/LoFTR/RoMa）在 KOMPSAT 评测里
  对独立检查点精度约 2.8m 且彼此无显著差异——选择主要影响对应密度而非大地精度；对 SP+LightGlue 的
  直接改进 = 限平移模型 + 位移幅度先验（|shift|≤12–15m 拒绝）+ 次峰比检验（排屋误配跳一个房距
  7–10m）。误差量级：Google Earth 历史影像按每 vintage 1–10m、方向随机做预算；Esri Wayback 是
  Maxar Vivid 混合精度（4m CE90 起），**Wayback 列表日期是发布日期非拍摄日期**——务必按瓦片哈希
  去重 + 读逐瓦片拍摄日期元数据（否则同影像重复计数 + 发布日期误当拍摄日期）。
- **问 2（无 DSM 视差）**：屋顶径向位移 d = h·tanθ（θ=25°、h=10m 约 4.7m，比小 PV 阵列还大，
  可解释 12% 矛盾）。**Google Open Buildings 2.5D Temporal**（2016–2023 逐年建筑高度，非洲覆盖，
  有效分辨率 4m）直接可用于给每站点标 host 建筑高度 → 划视差风险层 + 设搜索半径 r = 场残差 +
  h·tan30°。阴影测高只作高度界。逐 vintage 视角几何反演（挑几十栋已知高度高层量屋顶-足迹位移，
  鲁棒平均得 θ/方位）作廉价标定。
- **问 3（配准容忍变化检测）**：现代 CD 模型假设像素级配准、现实很少成立；应改为
  **reference-guided verification 而非自由变化检测**（手里有 2024 Vexcel 黄金参考）。先例 = Stanford
  DeepSolar Siamese（内含 cross-correlation 层 = 可学习的邻域搜索容差）。解码层换**两状态 HMM**
  （absent/present，拆除概率极小先验，发射概率显式建模非对称噪声，随面积/暗屋顶/oblique/匹配相关
  分层，从现有 QA 与 12% 矛盾率标定），输出日期后验区间。**最重要 schema 改动：每帧引入第三观测值
  uninformative，HMM 对其零信息处理**——"没找到证据"与"证据表明没有"必须在数据结构层分开。
- **问 4（VLM 提示工程）**：Set-of-Mark / red-circle 证据——标记形状不是主要矛盾，**标记语义**才是
  （当前提示等于宣称"marker 处即目标"）。两条叠加改法：(a) 语义降级（提示 marker 可能偏移 N 米，
  先找最匹配建筑）；(b) reference-guided（2024 Vexcel 芯片 + 历史帧并排，"找同一栋建筑，报告板是否
  已存在"）。双尺度输入（48m 语境 + 12–16m 特写）。多帧联合只作序列级复核 pass 查矛盾，逐帧二值判定
  保留（HMM 条件独立性需要）。三层防护对付空白/损坏瓦片：经典检测器前置 gate + 强制结构化输出
  `{image_usable, building_found, pv_status, confidence}`（`building_found=false ⇒ uninformative`）+
  几何一致性校验"从未定位到目标"。
- **问 5（0.3m 小目标 PV）**：0.3m 下标准组件 ~5×3px，识别地板；**超分在 30cm→15cm 这个点收益最大**
  （mAP +13–36%），送判定器统一 2× SR，人工 QA 用原始像素（GAN 纹理幻觉风险）。主打分器建议微调
  轻量分割/分类器（自有 Vexcel 检测 + BDAPPV 训练），VLM 只裁决低置信/矛盾。
- **问 6（端到端先例）**：DeepSolar++ / DeepSolar timelapse（HR/LR/blur-detection 三模型 = 逐帧质量
  门控 + 分辨率分层 + HR 样例参考比较，无显式逐帧配准、靠建筑级比较吸收失配）；Kruitwagen 2021 /
  TZ-SAM（含噪时序观测 → 装机日期区间的解码思路可移植，但它们是 10m 级大目标）。Claude 同样明确指出
  "Google/Esri 历史底图逐 vintage 回溯屋顶小目标 + 显式处理配准"在文献里是空档。
- **路线图**：①数据卫生 + 位移场（哈希去重 + corrupt gate + AROSICS 逐 vintage 局部配准）→
  ②屋顶模板追踪（足迹∩Vexcel 模板，CFOG/梯度 NCC 有界匹配）→ ③改 VLM 接口（双尺度 + reference-
  guided + 结构化四字段 + 对称措辞）→ ④换解码器（三值观测 HMM）→ ⑤分层重设 QA 抽样迭代标定。

---

## 3. 逐项对照矩阵（外部建议 × repo 现状）

状态定义：**done** = repo 已实现/已交付；**in-progress** = 部分完成或工具已就绪待推进；
**已证伪** = repo 实证否决；**未做·值得** = repo 未做且对本病有价值；**未做·不适用** = 对本 repo
的病不是第一顺位或不适用。

### 3.1 观测模型 / schema（两报告最强共识，也是 repo 的真增量）

| 外部建议 | 出处 | repo 现状 | 判定 | repo 证据 |
|---|---|---|---|---|
| 二值观测 → 多态（present/absent/uninformative(+corrupt)），absent 必须以 target_localized 为前提 | GPT-P1(1)(2)；Claude 问3/问4 | RUN 2 仍是二值 present/absent；无 uninformative 一档 | **未做·值得（真增量，A5 之后）** | census-bucket §4（0 model_miss，问题全在 marker 几何）；parallax-audit §盲区定义（71% 从不记录 confident-present） |
| 去固定 crosshair，改 reference(2024 Vexcel)+context+candidate-roof 提示 | GPT-P1(4)；Claude 问4 | 当前 marker = 固定坐标十字（还喂了 stale offset） | **未做·值得** | recenter §17.1（renderer 把 stale offset 喂给 marker+crop 中心）；qa-sample §v2（十字压根不在屋顶上） |
| all-absent 类重命名 unresolved / 输出后验区间而非"近期安装" | GPT-P1(6)；Claude 问3 | **已做**：Phase-0 decoder 输出 credible interval，`done_installed_during_census` = censored 上界（非点估计），交付语言已校准 | **done** | install-intervals（bounded 78.4%/left-censored/undated 三分）；census-bucket §5（交付语言校准）；快照术语表 |
| corrupt/blank tile 前置经典门控 | GPT-P1(3)；Claude 问4/路线图① | 已**扫描量化**（908 corrupt 帧、43.1% 被自信打分、25 帧 decision-critical），但尚无**前置生产 gate** | **in-progress（已量化，待落 gate）** | parallax-audit §corrupt 扫描 |
| present→absent 矛盾自动路由重配准/标 U（不当拆除） | GPT-P1(5)；Claude 问2/问3 | 已识别为信号（12% 矛盾；F1 指纹），install-intervals 有 dip-repair + nonmonotonic 再分类，但未做"路由到重配准队列" | **in-progress** | install-intervals §dip-repair；parallax-audit §F1 指纹 |

### 3.2 配准 / 重居中（两报告的第一顺位，repo 的第二顺位）

| 外部建议 | 出处 | repo 现状 | 判定 | repo 证据 |
|---|---|---|---|---|
| SP+LightGlue 逐 vintage 重配准作主流程 | GPT-P2；Claude 问1 | **已试并 NO-GO**（全库生产 geometry_version） | **已证伪（作全库主药方）** | recenter §13 NO-GO；§5 盲区 0/80 超半盒；registration 与 placement 正交 |
| 诊断"固定坐标 marker 是错误观测模型" | GPT 总诊断；Claude 总诊断 | **repo 独立实证且更深**：真根因 = 自家 stale offset bug + 上游 segmentation 残差 | **done（且比外部更深）** | recenter §17（~73% 由 stale-offset bug 单独解释）；census-bucket §4（0 model_miss） |
| phase correlation / ECC / CFOG / AROSICS 逐 vintage 局部位移场 | GPT-P2；Claude 问1/路线图① | 未做（learned matching 试过；经典稠密法未系统试） | **未做·值得（但排在 offset-fix 之后）** | recenter §13（配准非盲区主因）；DATA-learned-matching-*（ISSUE-24 已试 learned 分支） |
| 72–120m registration chip（大配准小判断） | GPT-P2 | per-target 96m chip 已居中；但判定 chip 是 A24(24m)/A48(48m) | **部分 done / 部分未做** | parallax-audit §渲染分辨率（A24 拉伸 3.19×）；快照术语表 A24/A48 |
| Wayback 瓦片哈希去重 + 逐瓦片拍摄日期（勿把发布日期当拍摄日期/同影像重复计数） | Claude 问1 | GEHI 是唯一 provider；快照未记录显式瓦片哈希去重层 | **未做·值得（Claude 独有的具体工程点）** | 快照 GEHI 术语；无 dedup 层记录 |
| 学习型 matcher 对独立检查点无显著精度差异、应加位移先验 + 次峰比 | Claude 问1 | ISSUE-24 已表征 SP+LightGlue（91.3% 验证），recenter 门控用 n_inliers≥20 | **done（表征一致）** | ISSUE-24（learned matching GO after GT re-adjudication）；recenter §门控 |

### 3.3 视差 / 无 DSM（方向一致，Claude 有独有高度数据增量）

| 外部建议 | 出处 | repo 现状 | 判定 | repo 证据 |
|---|---|---|---|---|
| 地面配准与屋顶定位分开（d≈h·tanθ 量级足以移出目标框） | GPT-P3；Claude 问2 | 已诊断为风险（parallax-audit 核心），但未实现分离式定位 | **in-progress（诊断 done，方法未做）** | parallax-audit（off-nadir 位移审计） |
| 屋顶模板追踪（逐站点 2024 Vexcel 模板 + 有界搜索窗稠密匹配，同时吸收配准+视差） | Claude 总诊断/问3/路线图② | 未做；机制上**绕开** recenter pilot 证明的正交性问题（逐帧目标定位 ≠ 全局重配准） | **未做·值得（A5 后重评估）** | recenter §13（NO-GO 针对全局重配准，不自动覆盖逐帧模板追踪）；static/drift 各 54%/43%，drift 部分才是模板追踪目标 |
| Google Open Buildings 2.5D Temporal 高度数据分层视差风险（非洲覆盖，无需自建 DSM） | Claude 问2 | 未做 | **未做·值得（Claude 独有增量）** | 快照未记录任何高度数据源 |
| BONAI/LOFT roof-to-footprint offset head；用 41k 站点估视差场 | GPT-P3 | 未做 | **未做·不适用（重训练；offset-fix 后规模会小很多）** | rescan §A5 恢复 +43pp（免费修复先摘走大部分） |
| 阴影测高只作 LOW/MID/HIGH-RISE 分层 | GPT-P3；Claude 问2 | 未做 | **未做·不适用（低优先）** | — |

### 3.4 打分器 / VLM（Phase 3 现实约束）

| 外部建议 | 出处 | repo 现状 | 判定 | repo 证据 |
|---|---|---|---|---|
| 训自托管打分器替换 Gemini（presence scoring 蒸馏） | GPT-P4 隐含；Phase 3 目标 | **已证伪**：DINOv3-L-SAT 蒸馏 fidelity gate-2 FAIL，dinov3 TRACKER slice 7 rollout blocked | **已证伪（presence-scoring 蒸馏）** | 快照 Phase 3；`docs/dinov3_scorer/TRACKER.md` slice 6 gate-2 FAIL |
| reference-conditioned Siamese（成对 2024 Vexcel vs 历史帧）作主打分器 | GPT-P4；Claude 问3/问5 | **未试**——与已证伪的 presence-scoring 蒸馏是**不同任务形式** | **未做·中期可考虑（≠ 已证伪）** | dinov3 gate FAIL 仅针对 presence-scoring 蒸馏；Siamese verification 未 gate |
| Gemini 退为困难样本 adjudication | GPT-P4；Claude 问5 | 与 Phase 4 A/D 形状一致，但依赖 student，student FAIL 使 Phase 4 受阻 | **in-progress（受阻于无可用 student）** | TRACKER slice 14/15/16 ready-for-agent；快照 Phase 4 |
| 双尺度输入 + reference-guided + 结构化四字段 + 对称措辞 | Claude 问4/问5 | 未做（与 §3.1 提示改造同条） | **未做·值得** | qa-sample §marker 渲染问题 |
| 超分 30→15cm 有实证收益、人工 QA 用原图 | Claude 问5 | rescan **部分证伪**：A1 放大对小目标反而有害（Step 0：2/3 case 净负） | **已证伪（本 repo 语境，通用放大）** | rescan §Step 0 + §A1（放大不推荐，小目标有害）；parallax-audit §渲染分辨率 |

### 3.5 解码器 / 时序（需澄清：repo 已有概率 decoder）

| 外部建议 | 出处 | repo 现状 | 判定 | repo 证据 |
|---|---|---|---|---|
| 硬 changepoint → 带非对称噪声的概率 HMM/贝叶斯变点，输出后验区间 | Claude 问3；GPT 隐含 | **大部分已做**：Phase-0 = 确定性单调 changepoint 后验 + Turnbull survival 先验，输出 credible interval，已是生产默认 | **done（decoder 本体）；真 delta 仅"观测层加 uninformative"** | TRACKER slices 1/2/3/20/21/22 done；快照 Phase 0 |
| 三值观测（uninformative）进解码层零信息处理 | Claude 问3/问4/路线图④ | 未做（观测仍二值） | **未做·值得（唯一的真解码层增量）** | 同 §3.1 第一行 |
| 序列级复核 pass 查 present→absent 矛盾 | GPT 第二阶段；Claude 问4 | F1–F5 指纹 + install-intervals 再分类部分覆盖，但非独立 VLM 复核 pass | **in-progress** | parallax-audit §F1–F5；install-intervals §再分类 |

### 3.6 验证 / gold set

| 外部建议 | 出处 | repo 现状 | 判定 | repo 证据 |
|---|---|---|---|---|
| 分层 gold set + Wilson CI + 核心指标不是 per-frame accuracy | GPT 第八节；Claude 路线图⑤ | **工具已就绪、待人工**：TRACKER slice 11 ready-for-human，8,407-bit human_queue 已备 | **in-progress（ready-for-human）** | TRACKER slice 11；快照 Phase 2 |
| synthetic displacement stress test | GPT 第八节 | 未做 | **未做·值得（低成本，可搭 offset-fix 验证）** | — |

---

## 4. 建议的合并行动顺序

把两方案的增量与 repo 既有待决项合并成一条优先级链。**方括号标注来源**：
`[repo]` = repo 已自行发现且走得更远；`[外部新]` = 外部方案带来的、repo 尚未做的真增量；
`[外部·repo已证伪]` = 外部建议但 repo 已实证否决，不进清单主线。

**第一梯队（今日最可行动，纯 repo 内，免费或近免费）**

1. **`[repo]` 修 stale `target_offset_x_m/y_m` bug + 发起 offset-fix 重扫。** 这是本 repo 的真根因，
   两份外部报告都不知道其存在。ISSUE-25 per-target 重建未重算 offset，`run_adaptive_scan.py` 渲染器
   把陈旧组级偏移喂进 RUN 2 全部 41,393 anchor 的 review crop。~73% 的 marker-off-structure 由此单独
   解释。修复 = offset 归零（chip 已居中）。rescan-pilot 已量化：large-offset census core 上净恢复
   ~43pp、11/30 从占位符恢复为真 bracket。**决策待定**：是否发起 population (a) 15,882-anchor 重扫
   （8 workers ~3.5–4h，预期 ~5,800 anchor 转真 bracket）。按 D18/ISSUE-19 需 documented
   geometry_version 迁移，非静默 hotfix。**证据**：recenter §15–17、rescan §2/§4/§5。

2. **`[外部新]` 观测 schema 三/四态化（A5 之后的下一层防线）。** 两份报告最强共识、也是 repo 尚未做
   的真增量：present / absent / uninformative(未定位到目标) (+corrupt)，**absent 必须以
   building_found & target_localized 为前提**；Gemini 输出改结构化 `{image_usable, building_found,
   pv_status, confidence}`，`building_found=false ⇒ uninformative`。这与 census-bucket audit 的
   0-model_miss 发现完全咬合——A5 修复后，残余 placement/视差错误不再被机械吸进 census bucket。
   **注意定位**：这是 A5 的**下一层**，不是替代 A5。**证据**：GPT-P1、Claude 问3/问4；census-bucket §4。

3. **`[外部新]` corrupt-frame QA pass + 前置 gate（独立有用、便宜）。** repo 已扫出 908 corrupt 帧、
   43.1% 被自信打分、25 帧 decision-critical。落一个经典前置门控（匀色/熵/Laplacian 方差 +
   瓦片哈希去重）即可。这也是 recenter NO-GO 之后 repo 自列的替代路线之一。**证据**：parallax-audit
   §corrupt；快照 §3.6。

4. **`[repo]` patch `infer_install_dates.py` 的 701 行 inverted-interval 漏标。** 状态机把
   `done_appears` 但 latest_absent > earliest_present 的行清空日期却没归 nonmonotonic；deliverable
   builder 已 workaround，脚本本身待 patch（owner 决策）。**证据**：install-intervals §缺陷。

**第二梯队（accuracy 里程碑，需人工/需一次组合验证）**

5. **`[repo]` slice 11 gold-set 人工裁决（ready-for-human）。** 唯一卡在人工的准确度里程碑，n=500
   裁决，8,407-bit human_queue 已就绪。这是把"fidelity-only"变成"可引用准确度声明"的必经步骤，也是
   两份外部报告都强调的"核心指标不是 per-frame accuracy"的落地口。**证据**：TRACKER slice 11。

6. **`[外部新]` A2 parallax-prompt 小规模组合验证 + reference-guided 双尺度提示。** rescan 建议 A5+A2
   组合验证（~50–100 anchor）；叠加 Claude 问4 的 reference(2024 Vexcel)+历史帧并排 + 双尺度
   （48m 语境 + 特写）。**证据**：rescan §5、§3.3；Claude 问4。

**第三梯队（中期，A5 修复后重新评估适用性）**

7. **`[外部新/待重评]` 屋顶模板追踪（逐帧目标定位，绕开正交性）。** Claude 的 per-site Vexcel 模板 +
   有界搜索窗稠密匹配（CFOG/梯度 NCC）机制上不同于 recenter pilot 的全局重配准——它同时吸收配准误差
   与视差。**NO-GO 不自动覆盖这条路线**，但适用性要在 A5 修复后重评（A5 后残余规模小很多，static/drift
   里的 drift 部分才是它的目标）。**证据**：recenter §13（NO-GO 限全局重配准）、§6（static/drift 54/43）。

8. **`[外部新]` Google Open Buildings 2.5D Temporal 高度分层。** Claude 独有增量：无 DSM 下按建筑
   高度划视差风险层 + 设搜索半径。作 §7 屋顶模板追踪的先验层。**证据**：Claude 问2。

9. **`[外部新/待评]` reference-conditioned Siamese verification 打分器。** 与已证伪的 presence-scoring
   蒸馏是**不同任务形式**（成对 Vexcel vs 历史帧），未被试过——归"未做、中期可考虑"，**不能说已证伪**。
   若上线，让 Gemini 退为困难样本 adjudication。**证据**：dinov3 gate FAIL 仅针对蒸馏；GPT-P4/Claude 问3。

**明确不进主线（外部建议但 repo 已证伪或不适用）**

- **`[外部·repo已证伪]` SP+LightGlue 全库重配准作主药方**——recenter §13 NO-GO，正交于 placement。
  仅可作 detector（门控真超盒少数 anchor 走 scoped re-crop）。
- **`[外部·repo已证伪]` 通用放大/超分作视觉证据**——rescan Step 0/A1：对小目标净有害。
- **`[外部·不适用]` BONAI 风格 roof-offset head 重训练 / 41k 站点视差场**——offset-fix 免费摘走大部分
  后规模大减，重训练性价比低，降为观察项。

---

## 5. 抽查核对结论 / 与快照·裁定不符之处

对 recenter-pilot（§13 NO-GO、§5 0/80、§17 stale-offset bug + ~73%）、rescan-pilot（§4 A5 46.7% /
净 +43pp、§2 mid-flight offset 发现、§3 A5 net +24pp）、census-bucket-audit（§4 0 model_miss、§3.1
分状态问题率 32.9%/65%/80%）、TRACKER（slice 11 ready-for-human、slice 7 verdict-store done、
slice 14/15/16 ready-for-agent、slice 24 GO、slice 25 ready-for-agent）逐一核对：**快照与裁定意见
所引用的数字、slice 编号、判决与 repo 文档一致，未发现实质性冲突。**

两点**非冲突的表述性 nuance**，为避免读者误读，已在正文按此处理（如实报告，未改动裁定）：

1. **"盲区"百分比有两个口径**：parallax-audit 与 census-bucket doc 用 **71%**
   （= `done_installed_during_census` 23,317 + `marker_missed_pv` 4,645 + `no_recent_anchor` 1,433 ≈
   29,395 / 41,393）；recenter-pilot §11 的 `b_blind_bucket` 用 **67.6%**（27,962 anchor）——后者是
   pilot 分层抽样时对盲区的一个略窄的操作性定义。两者不矛盾，是两个略异的 population 界定；本文引用时
   保留"71%"作 program 级口径、"0/80"作 pilot 级证据，未混用。

2. **"slice 7" 分属两个 tracker**：v2 `TRACKER.md` 的 slice 7 = verdict store（Phase 1，**done**）；
   Phase 3 "slice 7 rollout blocked" 指的是 **`docs/dinov3_scorer/TRACKER.md`** 的 slice 7。快照在
   上下文中是准确的，本文在 §3.4 已显式写明"dinov3 TRACKER slice 7"以免与 v2 slice 7 混淆。

此外，GPT 快照缺用户原始提问轮、Claude 快照用户消息有单词融合瑕疵——已在 §0 caveat 如实记录，不影响
两份报告 assistant 正文的可评审性（两份报告正文均完整）。
