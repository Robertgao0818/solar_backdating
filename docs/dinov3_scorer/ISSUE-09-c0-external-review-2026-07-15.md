# ISSUE-09 — C0 reverse-template 方案外部评审（2026-07-15）

> Tracer slice 9 · [TRACKER](TRACKER.md) · 上游 issue：
> [`ISSUE-09-c0-reverse-template-matching.md`](ISSUE-09-c0-reverse-template-matching.md)

## 来源与范围

- **来源**：外部评审（ChatGPT 会话，标题"太阳能板序列识别"，
  <https://chatgpt.com/s/t_6a5766008be08191b32cfcf630184262>），2026-07-15 存档。
- **评审对象**：两份 C0 文档 —— ①数学与解码细节 brief；
  ②完整 preregistration
  [`DATA-c0-reverse-template-prereg-2026-07-12.md`](DATA-c0-reverse-template-prereg-2026-07-12.md)
  及其评估设计。
- **引用文献**（原文 cite 标记还原）：
  - TCM：Temporal Cluster Matching, arXiv:2103.09787
  - AnyChange：training-free change detection, arXiv:2402.01188
  - DINOv3 论文（dense feature / 中间层 correspondence 结论）
  - FeatUp：frozen-feature 上采样, arXiv:2403.10516
  - Gemini `gemini-3-flash-preview` 官方文档（preview 型号行为可变）

## 总体判断（TL;DR）

**路线值得继续**，比之前的 pooled MLP / sequence transformer 更符合任务结构
（census anchor + 冻结特征 + 向历史回溯 + 单次安装约束的 framing 是对的，
TCM 已证明该研究范式成立）。**但当前版本不足以稳定达到 Gemini 3 Flash
的窗口精度**，存在 6 个结构性问题（非调阈值 / PCA 维数 / `q=0.8` 可解）：

1. 模板帧参与自己的相似度计算 → self-match 泄漏。
2. 49 个位移取 max → 系统性抬高噪声帧分数（winner's curse）。
3. 当前 NIG 模型对 `T=3` 序列有严重问题（完美安装序列被 no-change 支配）。
4. `no_change` 没有对应清晰的物理状态。
5. 单一 footprint mean cosine 对小型 / 部分遮挡 / 扩建后 PV 过于脆弱。
6. Gemini agreement 为主评估只能证明"像 Gemini"，不能证明窗口正确。

> 结论：C0 可以成为很强的 training-free baseline，但必须先修正模板、
> 配准和短序列解码器；否则 Stage-0 AUC 即使过了，最终 exact-gap accuracy
> 也可能不理想。

**最值得保留**：census anchor、GEHI 同域模板、footprint-level DINO、
单次安装约束、blind paired evaluation。
**最需要删除或改写**：左/右帧平等竞争、模板帧参与自身打分、max-shift、
per-series `β₀`、独立 segment variance、模糊的 no-change、
把 Gemini agreement 当主要准确率指标。

修正后预计在**中大型 PV、≥4 个有效历史帧、配准质量较好**的样本上有机会
接近 Gemini；小型 PV、`T=3`、basemap source 突变的样本单靠 mean-cosine C0
难以达到同等水平，必须依赖 multi-prototype、pairwise block evidence 和
明确 abstention。

---

## 一、做得好的部分（保留）

1. **放弃 Vexcel 图像作视觉模板**：实测跨传感器 cosine 0.31 vs 同域 GEHI
   0.89；继续跨域匹配会把传感器域差异当成时间变化。保留 Vexcel 的 polygon
   geometry 和 `T_c`、模板放 GEHI 域内是合理职责拆分。
2. **footprint 而非全图 pooled feature**：PV 在 96 m crop 中面积很小，
   全图 embedding 主要表达房屋/道路/树木/城市布局。
3. **强制单次向上状态变化**（absent→present）：isotonic check、
   persistence、单 changepoint framing 都抓住了这一点。
4. **DINOv3 是合理 backbone 候选**，但注意：几何/correspondence 性能可能在
   **中间层**达峰，不要把旧 DINO 论文的"key facet 最优"当成 DINOv3 固定结论。
   应在 train split 上比较 output token / key / 中间层 output / 中间层 key
   —— **不要只做 backbone arm 而把 facet 完全 prereg 死**。

---

## 二、必须修改的结构性问题

### 1. 模板帧 self-match 泄漏（最高优先级修复）

当前 `p = mean(f_anchor)`，anchor 自己也进入序列打分，
`cos(f_anchor, p)` 天然接近 1 —— 相当于人为插入一个"肯定高分"观测，
容易把 changepoint 推到 anchor 前一个 gap。

**建议**：

- ≥2 个已知 present 帧：leave-one-out multi-anchor template，
  每帧用不含自己的模板库 `P^(-t)` 计算。
- 仅 1 个 present anchor：作为 `z_anchor = 1` 的硬约束，
  **从 changepoint likelihood 中排除**，不当普通 emission。

### 2. 不要"左右两侧按距离竞争模板"

只要存在 `t ≥ T_c` 的可用帧，**默认右侧帧为主 prototype**。
左侧帧的风险不是普通噪声而是可能根本未安装（cosine 高可能只因屋顶材质
相同、mask 主要盖屋顶、小 PV 只有一两个 token、DINO 捕捉的是建筑结构）。
通过 gate 的左侧帧最多加入 prototype bank，不应在有右侧帧时成为唯一模板。
担心右侧扩建/维修 → 用多个右侧 prototype 解决。

### 3. 位移网格取 max 产生 winner's curse

两份文档还需统一：math brief 写最大化 `s_t`，prereg 写最大化 footprint
cosine —— **两个实现不等价**。更根本：对 49 个位置取 max，完全 absent 的
噪声帧也会获得偶然高匹配位置，低质量复杂纹理帧极值偏差更大。
"footprint+ring 一起移动"防不住这一点。

**建议**：位移给以 phase correlation 为中心的高斯先验
`p(δ) ∝ exp(-‖δ-δ̂_phase‖²/2σ_δ²)`，然后 soft-max 边际化
`s_t = τ·log Σ_δ p(δ)·exp(s_t(δ)/τ)`；同时记录位移 posterior entropy
（熵高 = 配准不确定，进入 frame weight）。每帧仅 49 候选，计算量可忽略。
替代方案：在**排除 PV polygon 的稳定屋顶区域**估计 DINO correspondence
位移，固定该位移再算 PV score —— 配准与事件检测解耦目标函数。

### 4. 小 PV 不能用"patch center 是否落入 polygon"

小型屋顶 PV 只有几个 token 时，polygon 微移 / token grid 换 offset 都会使
footprint feature 突变；膨胀一整个 patch 又会混入大量普通屋顶。

**建议**：用 polygon 与 patch cell 的**实际交叠比例**做 fractional 权重
`a_j = area(P∩C_j)/area(C_j)`，不用二元 center mask。
可再做 4 个 token-grid offset 的 shift-and-stitch（错半个 patch 后平均）；
FeatUp 作为后续 arm，**先做简单确定性的 shift-and-stitch**。

---

## 三、对我方提出的 8 个数学问题的答复

1. **Weighted NIG 是否合理？** 若假设 `x_i ~ N(μ, σ²/wᵢ)`，则
   `n_eff=Σw`、weighted mean/SS 是正确充分统计量。marginal likelihood
   严格来说少了 `½Σlog wᵢ` 项，但各 hypothesis 用同一批 frame 恰好一次时
   为全局常数，softmax 中抵消。真正的问题：① quality×registration score
   不是标定过的 inverse variance → 只是 generalized/power posterior；
   ② 低权重 segment 的 `n_eff` 可能极小，singleton segment marginal 对
   prior 极敏感。**建议**：要求每侧 `Σw ≥ 1.5~2`，或改成共享噪声尺度的
   step model。
2. **`μ₀=mean(s)` 的 double dipping**：存在但 `κ₀=0.01` 很小，非最大风险。
   更危险的是 `β₀` 与每 segment 独立 variance prior：step hypothesis 用两套
   variance prior、no-change 只用一套，Bayes factor 对 `β₀` 非常敏感。
   **建议**：序列先 robust-center（减 median），固定 `μ₀=0`，共享 variance。
3. **`T=3–4` 的 first-difference noise estimator**：`T=3` 问题严重。
   理想序列 `s=[0,1,1]` → `(Δs)²=[1,0]` → median 平均 → `σ̂²=0.25`，
   按当前完整公式等间隔日期算得：正确 gap posterior ≈ 0.21、另一 gap 0.06、
   **`p_nochange ≈ 0.73`** —— 几乎完美的三帧安装序列被 no-change 支配。
   **建议**：不从单个短序列估 noise；用 train split 上的 pooled empirical
   null（同 backbone、同面积层、相似质量等级、明确无变化 frame pair）估
   `σ²_pool = medianᵢₜ (s_{i,t+1}-s_{it})²/2`，再 shrinkage：
   `T≤4` 时 `λ_T=1`（全用 pooled），`T=5–7` 主要靠 pooled，
   更长序列才允许 series-specific scale。
4. **Gap-length prior 与 no-change prior**：当前公式其实自洽 ——
   `P(H_k) ∝ Δd_k` 且 `P(H_∅) ∝ d_T−d_1` 归一化后恰好 `P(H_∅)=0.5`，
   不随 window 长度增长。但应显式写成
   `P(H_∅)=π_∅`、`P(H_k)=(1−π_∅)·Δd_k/(d_T−d_1)`，
   否则加入 `present_before_window` / `after_last_frame` 后语义不透明。
5. **`q = max gap mass`**：确实不适合 posterior 向相邻 gap 扩散的情形；
   但 top-2 adjacent mass 也没考虑日历间隔（相邻两 gap 可能共 3 个月也可能
   6 年）。**建议输出**：①最短连续 80% credible interval（按日历宽度
   argmin）；②该 interval 日历宽度；③posterior entropy；④MAP 与第二名的
   log-odds margin。"clean" 应同时要求 80% mass 集中于有限日历宽度、
   熵低、非异常配准、transition polarity 为正。
6. **Ring contrast**：`cos(mean(r), p)` 易被持续性 ring 变化污染
   （新屋顶、车棚、泳池、树冠、新铺路面、相邻 PV），shift search 和
   persistence 处理不了。**建议**：先对每个 ring patch 算相似度再取
   trimmed median `R_t = trimmed_mean_{j∈ring} cos(h_tj, P⁺)`；
   按整个时序的 temporal MAD 移除长期不稳定 patch；更进一步把 ring 换成
   "同一屋顶上、与 PV polygon 相邻但不重叠的 matched roof negatives"。
7. **Persistence 在 pre 只有一帧时**：弃用 pre/post median midpoint。
   由模型直接产生 `P(z_t=1|data)`，要求安装后状态 posterior 保持较高。
   规则版：≥2 个 post frame → 至少两个 `P(present)>0.7`；只有 1 个 post
   frame 且是 guaranteed-present anchor → 允许输出但降级 confidence；
   只有 1 个 pre frame → 用 pooled noise 判断该帧与整个 post prototype
   bank 的 separation。
8. **`T=3–5` 可靠性**：`T=3` 时每个 changepoint hypothesis 至少一侧只有
   1 个观测，同时估两均值+两方差+changepoint+no-change 信息明显不足，
   posterior 基本由 prior 决定；`T=4–5` 稍好但独立 segment variance
   仍过于灵活。**短序列应使用共享 variance、正向 step amplitude 的模型。**

---

## 四、建议直接替换的解码器

方向约束的 monotone step model：

```
s_t = μ + Δ·z_t + ε_t,   z_t = 1{t > τ},   Δ ≥ 0
ε_t ~ t_ν(0, σ²_pool / w_t)
```

关键变化：

- 一个共享 noise scale（`σ_pool` 在 train split 稳定序列上估计）；
- Student-t 处理云 / 阴影 / 错配 outlier；
- step amplitude 明确限制为正 → 不再需要 isotonic 事后补丁；
- 每个 gap 对 `μ`、`Δ` 数值积分即可，T 很小计算成本不高。

**Hypothesis 空间改写** —— 不要只有模糊 `no_change`，区分：

| Hypothesis | 语义 |
|---|---|
| `H_pre` | 安装早于第一帧 |
| `H_k` | 安装发生在 `(d_k, d_{k+1}]` |
| `H_post` | 安装发生在最后历史帧之后、`T_c` 之前 |
| `H_bad` | 图像证据不足或观测模型不适用 |

census 已确认 `T_c` 时存在的情况下，"全程低分但 no-change"不是真实安装
状态 —— 更可能是安装在最后一帧以后 / 特征模型失败 / 模板错误 / 配准失败，
不应被一个 `H_∅` 混在一起。

---

## 五、比单模板 cosine 更有希望的 idea（按优先级）

- **Idea A — 多 prototype present bank**：用最近 2–3 个 guaranteed-present
  右侧帧 `P⁺={p₁,p₂,p₃}`，
  `s_t⁺ = median_{p∈P⁺} TopKMean_{j∈footprint} cos(h_tj, p)`。
  处理季节 / 阴影 / 屋顶朝向 / 扩建 / 部分遮挡；比 Sinkhorn OT 便宜且稳。
- **Idea B — pairwise block changepoint**：取消单一模板，构造全部 frame
  两两 footprint-set 相似度 `K_ij`，真正的安装 changepoint 呈两块结构；
  每个候选 gap 打分 `B(k) = K̄_pre,pre + K̄_post,post − 2·K̄_pre,post`。
  无模板泄漏、不依赖单 anchor 光照、维修后不同 present 外观仍可聚成同一
  temporal cluster，可作为与 reverse-template 近乎独立的 cross-check。
  **建议排在 Sinkhorn OT 之前实施。**
- **Idea C — DINO + AnyChange 双通道**：AnyChange（training-free
  bitemporal latent matching，支持 point-query）对每历史帧 vs present
  anchor 算 `c_t = area(Mask_t ∩ P)/area(P)`，作为第二个 emission
  `y_t = [s_t^DINO, c_t^AnyChange]` —— 两者错误模式不同。
- **Idea D — 低层次 PV 结构证据**：Gemini 可能还利用深色规则矩形、平行边、
  重复 module texture、阵列边界、电气设备/阴影等；DINO 在 PV 只有一两个
  patch 时可能把这些压缩掉。可加 training-free 辅助通道：oriented gradient
  concentration、平行线密度、polygon 内外局部对比度、blue/gray-darkness
  rank、texture entropy、rectangular edge consistency。不手工加权 ——
  各通道在 train split 上 rank-normalize 后输入同一 monotone decoder。
- **Idea E — date-level nuisance correction**：4 万+目标很多共享同一
  basemap capture/vintage，可按日期/imagery batch 估全局偏移
  `b_g = median_{i∈stable controls} s_ig`，用 `s'_it = s_it − b_g(t)`
  消除整批影像偏暗 / sharpening / 颜色处理 / sensor batch change。
  比单目标自己的 ring 更稳，仍是 training-free。

---

## 六、评估方案需重新定位

1. **Gemini agreement 不能作主精度指标**（rep-to-rep agreement 度量
   reliability 而非 validity；高一致可能是双方被同一 basemap artifact 骗，
   或主类别占比太高双方都输出 no-change）。
   - **Primary**：人工 present/absent/unsure frame labels 构造真实 interval
     `(last absent, first present]`，报告 exact-gap accuracy、adjacent-gap
     accuracy、interval overlap（日历天）、posterior coverage、credible
     interval width、risk–coverage curve、polarity error。
   - **Secondary**：C0–Gemini agreement、Gemini rep–rep agreement、
     disagreement adjudication。
   - 已计划的 150 个人工标注 target 是最宝贵的 end-to-end ground truth，
     **不要只用于 pooled frame AUC**。
2. **Pooled frame AUC 不足以过 Stage-0**：frame AUC 高仍可能 changepoint
   错 1–2 个 gap。Stage-0 同时报：target-clustered AUC、每 transition
   target 的 pre/post pairwise ranking accuracy、exact-gap accuracy、
   stable-present targets 的 false-transition rate、stable-absent 历史窗口
   的 false-transition rate。GO bar 可仍以 AUC 为主但不能只看 pooled AUC。
3. **`n≥40` 的 disagreement adjudication 太小**：要求"C0 正确比例 95% CI
   下界 > 1/3"，`n=40` 时需 ≥20/40=50% 才能过；真实胜率 40% 时需
   `n≈184` Wilson 下界才刚过 1/3。三选一：① `n≥40` 定义为探索性
   adjudication 不要求 CI 下界；②正式样本提高到 150–200；③gate 改成
   posterior probability，如 `P(p_C0 > 1/3 | data) ≥ 0.95`。
4. **必须固定 Gemini 版本**：`gemini-3-flash-preview` 是 preview 型号，
   行为可能随服务更新变化。provenance 至少记录：exact model ID、API date、
   prompt hash、thinking 配置、image preprocessing、image order、
   temperature、retries、capture dates 文本格式。否则 fresh Gemini round
   无法复现。

---

## 七、建议的开发顺序

### C0-R：先修复当前路线

1. 右侧优先的 multi-anchor prototype；
2. anchor leave-one-out 或从 likelihood 排除；
3. fractional polygon-token weights；
4. 位移 posterior marginalization；
5. pooled noise + shared-variance positive-step decoder；
6. `H_pre / H_gap / H_post / H_bad` hypothesis 空间；
7. 80% 最短日历 credible interval；
8. 人工 exact-gap 验证。

### C0-P：增加独立 training-free arm

1. pairwise block changepoint；
2. robust patch-level TopK / trimmed matching；
3. stable-roof negative bank；
4. date-level nuisance correction；
5. AnyChange overlap feature。

### C1：仅当 C0 信号明确但精度不够时再训练

不再训练复杂 transformer。只训练极低容量、带单调约束的 emission
calibration：`P(z_t=1 | s^DINO, s^pairwise, c^AnyChange, q_t)`
（logistic regression 或 ordinal calibration），时序推断仍由 monotone
changepoint decoder 完成。
