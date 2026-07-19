# DESIGN — Phase-0 emission 扩展接口设计稿（三态 emission + 边界状态 + 区间级损失）

Status: **DRAFT — pending owner review**（2026-07-19）。只写接口/数学设计，不改
`src/` 代码，不 git commit。

Parent PRD: [`PRD-run3-native-local-line-2026-07-19.md`](PRD-run3-native-local-line-2026-07-19.md)
§4（数学核心）、§7（G2 门禁）、§9.3（本文档即该条任务）。
必须直面的失败记录：[`../replan_v2/DECISION-A-estimator-adoption-2026-07-04.md`](../replan_v2/DECISION-A-estimator-adoption-2026-07-04.md)。

现有实现（本设计只扩展，不新建）：
`src/solar_backdating/estimators/{seam,epochs,emissions,changepoint,survival,pava}.py`。

---

## 0. 一句话摘要

Phase-0 decoder（`changepoint.py`）已经原生具备"uninformative 边缘化"和"边界
状态"两个数学对象的**雏形**——abstain 符号在 decode 时已被渲染为状态无关常数
（correction 2 的 censoring-exact renormalization），tau=0/tau=T 两个 cell 已经
分别是"already-present"和"beyond-window"的显式表示。本设计要做的不是发明新
数学结构，而是：①把这个已有的"常数边缘化"机制从**离散符号**推广到**连续
student 概率**（`q_t, e_t(0), e_t(1)`）；②把边界状态从"靠 `None`/`is_beyond_
window` 隐式读出"升格为显式访问器；③给区间级损失定义一个明确、可判定的 `K_i`
构造规则；④给 D3 TVD 复测一个**零新增 Gemini 配额**的配对(paired)诊断协议，
并预先指出一个关键混淆因子（argmax 坍缩本身的不可约不稳定性）以防误判。

---

## 1. 三态 emission 扩展：uninformative 边缘化

### 1.1 现状：常数边缘化已经存在，只是被"焊死"在离散符号层

`changepoint.py::_epoch_loglik`（correction 2A）已经把 abstain 处理成
**状态无关、在 softmax 中抵消的常数**：三分类矩阵的 abstain 列被显式排除，
`P(sym|state,scored) = P(sym|state) / (P(absent|state)+P(present|state))`，
只在 `{absent,present}` 两列上重新归一化。这正是 PRD 要的"uninformative
对似然贡献常数，不硬塞 absent"的原则——**已经对离散符号成立**。

问题在于两处焊死：
1. 焊死在**离散符号**上——输入必须先被 `epoch_symbol()` 多数投票坍缩成
   `present/absent/abstain` 三选一，student 的连续校准概率 `q_t, e_t(0),
   e_t(1)` 没有位置可插。
2. 焊死在 **EM 拟合**上——`fit_emissions_em` 的 M 步只认离散 `(symbol,
   stratum)` 对；student 的 `e_t` 是训练直接产出的校准值，不该也不需要
   再过一次 EM。

### 1.2 决策 D-E1：新增 `FrameEmission`，不改 `SYMBOLS`/`EmissionModel`

`SYMBOLS = ("absent", "present", "abstain")` **不改名**——"uninformative
(+corrupt)"在实现层就是既有的 `"abstain"`，产品文档层面的三态标签
（present/absent/uninformative）与代码层面的三态符号（present/absent/
abstain）是同一件事的两个命名；只需在 `emissions.py` 顶部文档补一句显式
别名声明，不做代码重命名（重命名会波及 `SYMBOL_INDEX`、`changepoint.py`、
`fit_emissions_em` 全部调用点，属于纯churn，且 DECISION-A 的既有产物/测试
仍以 `"abstain"` 为准，重命名会制造无意义的向后不兼容）。

新增（`emissions.py`，纯 tuple/dataclass，无 numpy，延续本模块既有风格）：

```python
@dataclass(frozen=True)
class FrameEmission:
    """Student 两头解耦输出，decode-time 消费单元。

    q: P(usable & localized | x_t) —— 已经与 TargetLocalizationObservation
       做过硬 AND（target_localized=False 时 q 必须已被上游置零；本类型不
       自己做这层门控，见 §3.2）。
    e0: P(observe "absent"  | state=absent_state,  usable&localized)
    e1: P(observe "present" | state=present_state, usable&localized)
       —— 只需两个数（每个 state 内 present/absent 二选一，1-e0 / 1-e1
       是另一半），对称结构复用现有 Matrix 的两行设计，比照
       row_absent_state=(e0,1-e0)、row_present_state=(1-e1,e1)。
    stratum: 预留键，A24/A48 × 面积段 × 年代（§4.3 分层校准），今天可全部
       填 "default"，不参与本设计的最小实现。
    """
    capture_date: date
    q: float
    e0: float
    e1: float
    stratum: str = "default"
    source_row: int | None = None
```

新增边缘化似然函数：

```python
def frame_loglik(frame: FrameEmission, state_is_present: bool) -> float:
    """log( q * e(state) + (1 - q) ).

    q=0  -> log(1) = 0.0   （完全不可用/未定位：状态无关常数，softmax 中抵消，
                             与现有 abstain 的常数贡献严格一致）
    q=1  -> log(e(state))  （完全可信：退化为硬符号 decode 的对数似然）
    0<q<1 -> 两者的凸组合的对数，介于两极限之间，单调于 q（固定 e≠0.5 时）。
    """
```

这不是"新发明"，是把 `_epoch_loglik` 里"abstain 贡献 0"的既有不变量，从
"硬符号 0/1 开关"推广成"软权重 q 的连续插值"——`q→{0,1}` 两个端点分别精确
复现现状的两条既有分支（symmetric-noise fallback 的硬符号路径、abstain-drop
路径）。这是选择这个具体公式（而非其他边缘化写法）的**充分性论证**，不是
巧合。

### 1.3 决策 D-E2：EM 拟合永久保持离散符号路径不变

`fit_emissions_em` **不改**、`EmissionModel`/`Matrix`（离散 2×3 矩阵）
**不改**。这条路径永久服务于：production `sustained`/decoder 的 Gemini-
teacher-symbol 消费、DECISION-A 已判 GO 的 fractional-caliber 生产链、
`CohortPrior`/Turnbull 的既有下游。Student 的 `e0/e1` 由监督训练（§4）
直接产出、已经是"校准好的"数，不需要也不应该再过一次 cohort-wide EM——
这是**永久性分岔**，不是"以后再补"的 TODO：EM 拟合的对象是"给定一个
黑箱评分器，反推它的混淆矩阵"，而 student 训练本身就是在直接优化
`e0/e1` 的校准，两者是同一问题的两种解法，硬套在一起没有数学意义。

### 1.4 决策 D-E3：epoch 级"连续版"聚合——indicator-of-max-q，不做加权平均

现有 `pava.py`/`changepoint.py` 的核心不变量是"一个 epoch 最多贡献一个
证据单位"（`pava.py` 注释原话：count-weighted 似然会重复计数相关的近重复
帧，D2 epoch collapse 存在的意义就是避免这个）。连续版必须继承同一条
不变量，否则同一批高度相关的 near-duplicate student 打分会人为推高该
epoch 的"可用性"置信度。

```python
@dataclass(frozen=True)
class PooledEpochEmission:
    start_date: date
    end_date: date
    q: float          # = max(member.q)  —— indicator-style，见下方 rationale
    e0: float         # 取自 argmax-q 的那一帧（"代表帧"）
    e1: float
    n_members: int

def pool_epoch_frame_emissions(
    frames: Sequence[FrameEmission], gap_days: int
) -> list[PooledEpochEmission]: ...
```

**推荐：`q_epoch = max(q_t)`，`(e0,e1)_epoch` 取自该 max-q 帧**（"代表帧"
规则），而不是 soft-OR（`1-Π(1-q_t)`）或 q-加权平均。理由：soft-OR 会随
epoch 内成员数单调抬高 `q_epoch`，重新引入 pava.py 明确警告过的"重复注入
提升证据强度"问题（对 epoch 内注入更多近重复低质量帧，soft-OR 会让
`q_epoch` 趋近 1，即使没有任何一帧真正可信）；而"代表帧" 规则下，注入更
多帧最多让 `q_epoch` **不降**，不会因为帧数堆积而人为拔高，这是与现有
"epoch 是一个证据单位"设计哲学位同构的推广。**备选（不推荐，需要先做
消融）**：inverse-variance 加权平均——留作future work，标注在测试清单里
（§6 测试点 2）作为"先证伪代表帧规则不够用，再启用"的第二方案，不在本设计
的最小实现范围内。

---

## 2. 显式边界状态

### 2.1 现状：边界状态已经原生存在,只是没有名字

结论先行：**不需要新的数学对象**。现有 tau 网格（`EpochCell` 的
`index∈{0..T}`）已经精确覆盖三种边界语义：

| 产品语义 | 现有代码表示 |
|---|---|
| already-present（左删失，ext2015 线的 `left_censored`） | `map_index==0` 且 `cells[0].start_date is None`（"open-left"） |
| 仍未出现（右删失，未定级 `done_absent`/undated） | `map_index==T`，`cells[T].is_beyond_window==True`，`p_undated = posterior[T]` |
| census-bound（截至普查仍未见,但普查证实存在） | `changepoint.py::estimate_changepoint` 里 `evidence_cutoff` 分支——在 `evidence_cutoff` 处插入一个合成的 "usable present" epoch，保证该 epoch 不会与更早的 absent 帧折叠、不被更晚证据污染 |

也就是说，DECISION-A 时代的 decoder 已经用 tau=0 / tau=T 两个天然 cell、
加上截止日合成 epoch 这一个机制，把三种边界状态全部编码进了同一个 tau 枚举
里，没有专门的"third state"分支逻辑。

### 2.2 决策 D-B1：只加显式访问器，不改 `EpochCell`/`InstallDatePosterior` 字段形状

新增两个纯函数（`seam.py`，不是 dataclass 新字段——避免触碰
`InstallDatePosterior` 现有的 JSON round-trip/相等性契约）：

```python
def is_already_present(result: InstallDatePosterior) -> bool:
    """map_index==0 且 open-left（cells[0].start_date is None）。"""

def is_beyond_window(result: InstallDatePosterior) -> bool:
    """cells[map_index].is_beyond_window —— p_undated 的 MAP-hit 版本。"""
```

理由：`map_interval_start is None` / `is_beyond_window` 这两条隐式判据
已经散落在 `changepoint.py`、`infer_install_dates.py`、以及各种下游
QA/审计脚本里各自重新推导一遍（例如 ext2015 计划里 `classify_row` 又独立
判了一次 `left_censored`）。给它们一个具名函数，不改变任何现有语义，只是
把"怎么读边界状态"这件事**从隐式约定升格为可测试的公共契约**——这直接
服务于 §4 的 `build_k_i`：K_i 构造需要精确知道"这条 anchor 的教师标签是
不是边界情形"，不该再用一次性内联判据。

### 2.3 与 ext2015 左删失线的兼容性（只需不冲突，不需接管）

ext2015 计划（`../replan_v2/RUN-leftcensored-ext2015-plan-2026-07-19.md`）
里 `left_censored` 的定义是"`install_interval_end` = 该 anchor 自己观测到
的最早已评分帧"，**没有硬编码任何窗口下界**（2019→2015→2012 只是把这个
"最早已评分帧"往前推，判定逻辑本身不变）。这与本设计 tau=0 cell 的语义
（"open-left，`start_date=None`，`end_date`=该 anchor 自己的最早 usable
帧"）是**同一个数学对象**，两条线各自独立定义各自的窗口下界，互不需要
合并观测或共享代码路径——只要求两者对"tau=0 是什么意思"这件事的理解
一致，本设计不改变这层语义，因此天然兼容，**不需要**、也不应该在本设计
里去接管 ext2015 线的窗口选择。

### 2.4 uninformative 边缘化对边界 cell 的证据完整性约束

一条需要显式写下的不变量（否则容易在实现时踩坑）：**uninformative 边缘化
的作用是"该帧不提供信息"，绝不是"该帧倾向于某个 state"**。具体到边界：
如果窗口最早的几帧因配准/定位失败被标 uninformative（`q≈0`），tau=0 cell
的"already-present"假设和 tau>0 的"absent→present"假设必须获得**相同**
的边缘常数贡献（`frame_loglik` 对两个 state 都是同一个 `log(1-q)`-类型
常数在 `q→0` 极限下都精确等于 0）——不能因为窗口早期证据稀疏就系统性地
偏向某一个假设。这一点在 §1.2 的公式设计里已经自动满足（`state_is_present`
参数只影响 `q>0` 时的 `e(state)` 项，`q→0` 时两个分支都精确坍缩到
`log(1)=0`），但值得作为一条显式回归测试点写进 §6。

---

## 3. 两头解耦接口：q_t / e_t(0,1) 进 decoder

### 3.1 推荐：软边缘化权重，非硬门控阈值

**推荐方案**：`q_t` 作为 §1.2 `frame_loglik` 里的连续软权重，不做任何
阈值二值化。**理由**（生成式论证，不是拍脑袋）：把"usable & localized"
本身建模成一个与 `state`（PV 是否存在）独立的伯努利隐变量 `u_t`——
`u_t=1` 时以 `e_t(state)` 发射一个可用符号，`u_t=0` 时不产出任何符号
（对似然贡献常数 1）。对 `u_t` 边缘化后频率学派的精确边际似然就是
`P(observe y_t|state) = q_t·e_t(state) + (1-q_t)·1`——这**正是** §1.2
给出的公式，不是近似,是在"quality/localization 与 PV 状态相互独立"这个
（合理的）建模假设下的**精确**边际化。硬阈值（`q_t>θ` 则纳入、否则丢弃）
是这个精确公式在 `q_t∈{0,1}` 处的退化特例，会丢失中间置信度的信息，且
额外引入一个需要校准的超参数 `θ`（校准误差直接传导成"该纳入还是丢弃"的
离散决策噪声——这正是 DECISION-A 诊断出的"argmax 坍缩放大证据扰动"问题
的同构版本，见 §5）。

**备选（阈值+硬门控）**：作为工程简化路径列出，供 R3 早期原型验证阶段
临时使用（例如尚未训练出良好校准的 `q_t` 时，先用一个粗阈值把 student
输出接进现有离散 `epoch_symbol()`/`EmissionModel` 路径，验证端到端管线
跑通）——但**不作为 G2 门禁评测口径**，门禁评测必须走软边缘化路径。
两条路径在 `EstimatorConfig` 里不冲突：硬阈值原型可以直接把
`(q_t 阈值化, argmax(e0,e1))` 映射回既有的 `'1'/'0'/''` 三态字符串，
灌入 `VintageObservation`，完全复用现状代码，零新接口——这是它作为
"早期原型路径"的价值，但正式训练/评测必须切到 `FrameEmission` + 软边缘化。

### 3.2 与 TargetLocalizationObservation 的组合语义：硬 AND（读两个字段），不是软加权

`absent` 只在 `target_localized=true` 时允许——这是 PRD §3.3 定的红线，
本设计必须原样落地为一条**不可绕过的硬门控**，不能被 §3.1 的软边缘化
稀释掉。**门控条件必须同时读 `target_localized` 与 `abstain` 两个字段**
（2026-07-19 与并行交付的 schema——`src/solar_backdating/localization/
observation.py`，51 项测试落地代码——交叉核对后的修订；下方 §7 说明
背景）：

```
if tlo is None:
    # 桥接态（见下）：定位层未跑，门控 no-op，绝不隐式清零
    q_t_effective = q_t_model
    localization_pending = True
elif tlo.target_localized and not tlo.abstain:
    q_t_effective = q_t_model
else:
    q_t_effective = 0.0
```

（这是本设计**唯一**的规范伪码；下文各分支的展开说明均以此块为准，
不得另行简写成单表达式——单表达式极易把 `tlo is None` 归并进清零分支。）

**为什么不能只读 `target_localized`**：schema 允许一个合法组合
`target_localized=True ∧ abstain=True`（`effective_label` 的
docstring 显式列出：定位/配准两层都confirm了，但更上层的置信度/冲突
检查仍然选择弃权——例如 `failure_reason∈{low_confidence, dark_zone}`
这类"建筑找到了、屋面也配上了，但最终证据不足以下判断"的场景，
`observation.py::__post_init__` 允许 `abstain=True ∧ target_localized=
True`，只要求此时 `failure_reason` 非空）。`effective_label`（标签层，
observation.py:444）对这个组合的处理是：`base=="absent"` 且
`(tlo.abstain or not tlo.target_localized)` → 降级为 `uninformative`
——即标签层把 `abstain` 与 `not target_localized` **同等对待**为
"不得判 absent"的触发条件。似然层如果只读 `target_localized`（漏掉
`abstain`）,会对这个合法组合错误地给出满权重 `q`,与标签层刚好矛盾——
PRD §5.3"两信号冲突或偏移超界 ⇒ abstain,绝不判 absent"要求似然层同等
尊重 `abstain`,不只是 `target_localized`。

**`tlo is None`（桥接态,当前 311k 全量观测的实际状态)时门控必须是
no-op,不是隐式判"未定位"**：

```
if tlo is None:
    q_t_effective = q_t_model          # no-op：不清零
    localization_pending = True        # provenance 下传，非 gating 信号
```

`observation.py::effective_label` 对 `tlo is None` 的既定策略是"保留
legacy 三态标签不降级"（`return {"label": base, "localization_pending":
True, ...}`，docstring 原话："preserves legacy semantics unchanged...
not... guessing a gate outcome"）——这是因为 R2 定位层尚未跑到这批观测
（"pre-R2"),没有定位层就没有证据说它"没定位成功",不能把"没有 TLO"隐式
等同于"`target_localized=False`"（那会是无凭据地惩罚全部旧观测,与标签层
"未降级"的既定选择正好相反）。**明确禁止**下面这种写法：

```
# 错误：把"无 TLO"隐式等同"target_localized=False"，与标签层语义矛盾
q_t_effective = q_t_model if (tlo and tlo.target_localized) else 0.0
```

`localization_pending=True` 只是向下游（QA/审计/后续重新门控）传递
provenance 的旗标，**不参与** `q_t_effective` 的计算——一旦 R2 定位层
跑过这条观测（`tlo is not None`),门控立刻按上面的双字段规则生效，不需要
额外分支。

进一步：`target_localized=False` 或 `abstain=True` 时，`e0/e1` 本身
**不应被计算/消费**——即使 student 网络在这种情况下仍然吐出了某个
`(e0,e1)` 数值，decode 侧也必须直接忽略（`q=0` 已经让它们在似然里失去
作用，这里是双重保险：具体到接口层面，建议 `FrameEmission` 的构造函数/
构造脚本在门控触发时把 `e0=e1=0.5`（无信息先验值）而非留空——因为
`q=0` 已经让这两个值在数学上不起作用,填 0.5 只是防止意外情况下有代码
路径忘记检查 `q` 就直接消费 `e0/e1`，属于纵深防御，不是核心逻辑）。

---

## 4. 区间级损失与 decode 合同统一

### 4.1 K_i 构造：区间重叠，不是 epoch 下标恒等

`L_i = -log Σ_{k∈K_i} P_θ(τ=k|x_i) + λ·L_frame`。关键设计问题：`K_i`
（RUN 3 标签允许的 change cell 集合）**不能**通过"教师解码时的 epoch
下标"直接搬过来，因为学生自己的 tau 网格（因 uninformative 帧被剔除、
gap 聚合规则可能不同）可能与教师的网格帧数不同。正确规则是**区间重叠**：

```python
@dataclass(frozen=True)
class TeacherBracket:
    """RUN 3 交付物里,一个 anchor 的教师侧解码结果,归一化成 3 种边界形状之一。"""
    kind: str            # "interval" | "left_censored" | "right_censored" | "census_bound"
    lower: date | None    # (lower, upper] —— 语义与 EpochCell 完全对齐
    upper: date | None

def build_k_i(bracket: TeacherBracket, cells: Sequence[EpochCell]) -> frozenset[int]:
    """按 bracket.kind 分派:
    - "left_censored"  -> {0}                         (tau=0, open-left cell)
    - "right_censored" -> {len(cells)-1}               (beyond-window cell)
    - "census_bound"   -> 与 census 合成 epoch 唯一对应的那个 cell
                          (evidence_cutoff 机制保证这个 cell 存在且唯一)
    - "interval"       -> 学生自己 cells 中,与
                          (bracket.lower, bracket.upper] 有非空日期重叠的
                          全部 cell 下标 —— 不要求恰好一个,教师区间可能
                          横跨学生的多个更细 cell,或反之。
    """
```

`interval` 分支的重叠判据：`cell` 与 `bracket` 重叠当且仅当
`max(cell.start_date or -inf, bracket.lower) < min(cell.end_date or +inf,
bracket.upper)`（半开区间标准重叠公式，"or ±inf" 处理 open-left/
beyond-window 两种 `None` 边界）。这保证了"教师标注更粗、学生网格更细"
与"学生网格因过滤 uninformative 帧而更粗"两种方向都能正确构造出一个
**非空**（否则是数据/管线 bug，必须 raise，不能静默吞掉）的 `K_i`。

uninformative 帧对 `K_i` 构造的影响是**免费继承**的：因为学生自己的
`cells` 已经是"剔除 uninformative-only epoch 之后"的网格（§1.4 的
`pool_epoch_frame_emissions` 只保留 `q_epoch` 超过某个近零阈值的
epoch——具体见下方"epoch 是否 abstain"判据），所以 `build_k_i` 不需要
再单独处理 uninformative 帧,它看到的 `cells` 已经是干净的。

**epoch 是否整体 abstain 的判据**（`estimate_changepoint` 的连续分支需要
这条,类比现有 `epoch_symbol()`）：`PooledEpochEmission.q < ε`
（建议 `ε=1e-3`，与 `emissions.py` 现有 add-k smoothing 常数 `k=1e-3`
同一量级，便于统一心智模型）——低于此阈值的 epoch 不进入 tau 网格,恰好
镜像现有 `epoch_symbol()`"无 usable-scored 成员则整体 abstain"的规则,
只是判据从"离散计数为零"换成了"连续 q 低于近零阈值"。

### 4.2 L_frame：辅助校准正则,不是主目标

`L_frame` 是在教师有 usable 标签的帧上,对 `(q_t, e_t)` 做直接监督:

```python
def frame_calibration_loss(
    predicted: FrameEmission,
    teacher_symbol: str,       # "present" | "absent" | "abstain",教师侧 epoch_symbol() 输出
) -> float:
    """teacher_symbol == "abstain" -> 只对 q_t 做校准(目标 q≈0),不对 e0/e1 罚分
    (没有 state 监督信号)。否则对 e_t(teacher_state) 做交叉熵,并对 q_t 做
    "teacher 判定为 usable" 的二元校准(目标 q≈1)。"""
```

`λ` 的作用是防止模型只优化区间边缘似然而在个体帧层面失去可解释性——这对
QA 复核（人工抽查某一帧的 present/absent 判断是否合理）和 D3 诊断（区分
"argmax 坍缩不稳定"与"emission 本身失准"两种失败模式,见 §5）都是必要的
调试信号,不是锦上添花。

### 4.3 训练/校准/验收全部走 Phase-0 decode 口径

`infer_install_dates.py` 的 sustained 口径（`latest_absent/earliest_
present + midpoint`）**只作 legacy 对照列**——即评测报告里可以并列展示
sustained 的输出做参照,但训练目标、校准分层（A24/A48 × 面积段 × 年代,
`FrameEmission.stratum` 字段预留的键）、G1/G2/G3 门禁的判定,全部只认
Phase-0 `InstallDatePosterior.posterior`/`map_index`/`K_i` 重叠似然这一套
口径,不允许"用 Phase-0 目标训练,却用 sustained 目标验收"的口径错配（PRD
原文原话,直接采纳)。

---

## 5. D3 TVD 复测挂钩（重点）

### 5.1 先厘清一个容易读错的地方：本 PRD 的 G2 目标不是"重新证明 DECISION-A 现在是 GO"

DECISION-A 现在的生产判定确实是 **GO**——但那是通过 2026-07-05 的 P1
修正案切换到**fractional/survival 口径**（`survival_curve_tvd`,ISSUE-21
重新冻结的宽带 `[0.0243, 0.0787]`）才翻盘的,操作性的复现性门禁换成了
"posterior-mass 聚合",而不是原始的 **hard-MAP 年份直方图 TVD**（带
`0.037–0.063`,flat 实测 0.065 / EB prior 实测 0.075,两者都 FAIL,且从未
被重新推导或"修好"过——它只是不再是生产的*操作性*门禁,数字本身原封不动
留在记录里)。本 PRD §4.1/§7 明确要求把**这个原始 hard-MAP 门禁**重新
挂为正式门禁——即,检验"三态 emission + 区间级训练"这个新假设,能不能
让 hard-MAP 复现性本身变好,而不是像 P1 修正案那样绕开 hard-MAP、改用更
宽容的口径。这两件事不能混为一谈：**production 已经 GO 的事实,不能被
引用为本 PRD 的 G2 已经过关**。

### 5.2 关键混淆因子（必须预先控制,否则会误判机制假设）

DECISION-A 自己的根因分析第 3 条明确写了："the instability is a property
of collapsing a posterior to a hard MAP year, not of the posterior
itself"——即,fractional 后验本身已经比 sustained 的点估计更稳定,**不
稳定性是 argmax 离散坍缩这个操作本身带来的**,不是 emission 模型标定
误差的产物。如果两个候选年份的后验质量本来就很接近（真实的视觉歧义,不是
标注污染),任何模型侧的改进都无法让 argmax 100% 稳定——这是数学上不可约
的。

因此,如果三态 emission + 区间级训练之后 hard-MAP TVD 仍然超带,**在
认定"假设被证伪"之前**,必须先排除这个混淆:用 DECISION-A 自己提出但
从未测试过的 **P2 路径**（确定性 tie-break、margin-based MAP 平滑）在
**同一批** decode 结果上做一次事后稳定化,看看能不能把残余的超带部分
吃掉。如果 P2 稳定化之后仍然超带,才能确认是 emission/训练侧假设本身的
问题;如果 P2 就能吃掉残差,说明本设计的贡献仅仅是"减少了污染驱动的近
平局",argmax 坍缩本身的不可约部分需要 P2 类的 decode 侧修补,这不算
本设计假设的失败,但也不是它单独就能过关的证明——报告里必须把两种贡献
拆开讲清楚,不能笼统写"过/不过"。

### 5.3 复测协议——分两阶段,第一阶段零新增 Gemini 配额

**阶段 T0（配对消融,无需训练 student,复用已批 quota,可立即执行）**

不新收集数据。PRD §7 已经批准"独立 Gemini repeat ceiling（RUN 3-native
重导）"——在冻结评测 panel 上做 3 次独立重打,这笔 quota 本来是为 G1
（student fidelity）准备的。T0 把这**同一批 3 reps** 拿来做一次**配对**
diagnostic:

1. 对照组：3 reps 各自走**现状**离散路径（`fit_emissions_em` + 现有
   `epoch_symbol()` 三态,`ambiguous`/`unusable` 收进 abstain 列,不做
   `target_localized` 门控)——精确复现 DECISION-A 当年的设置,验证能否
   在 RUN3-native 的几何修复标签上复现 0.065 级别的超带（排除"RUN3 几何
   修复本身顺带修好了 TVD"这个另一种解释)。
2. 处理组：**同一 3 reps 的同一批原始观测**,只是重新按新三态标签规则
   （uninformative 边缘化,配合一个 `target_localized` 代理信号——旧
   Gemini-only 标注没有真正的 TLO,可以先用 `quality_flag∈{ambiguous,
   unusable}` 作代理,承认这是代理,不是真的 localization 观测)重新分类、
   重新走 `frame_loglik` 软边缘化路径 decode。
3. 在**完全相同**的 3 reps 上比较两组的 pairwise 年份直方图 hard-MAP
   TVD（3 对）。配对设计的价值：控制了"cohort/几何修复本身"这个协变量,
   把差异精确归因到"emission 定义变了"这一个变量。

**决策规则**：
- 处理组 TVD 明显低于对照组、且接近或落入 `[0.037,0.063]` → 机制假设
  获得初步支持,值得投入 R3–R5 的训练算力。
- 处理组与对照组几乎无差异 → 应用 §5.2 的 P2 混淆控制（在**处理组**结果
  上做一次确定性 tie-break/margin 平滑,免训练、免新数据）,如果 P2 后
  仍无改善,这是"无新假设"的强信号——按 PRD §7.4 的搁置准则**在 R3 训练
  开工之前**就可以触发预登记的搁置讨论（见 §5.4 的建议修正案）。

**阶段 T1（全管线,post-R4,PRD 已注册的正式 G2 门禁）**

3 个独立训练 seed（PRD §7 已经要求"3 个随机种子 + CI"）——因为训练好的
student 是确定性的（固定权重 + 固定输入 → 固定输出),这里的"独立重复"
的噪声来源从"LLM API 的随机性"换成了"训练随机性",这是对 DECISION-A
原始设计意图的忠实迁移,不是偷换概念。在同一个冻结评测 cohort 上用 3 个
seed 各自训练出的模型 decode,计算 3 对（C(3,2)=3）hard-MAP 年份直方图
TVD,判定标准沿用原始带 `[0.037, 0.063]`（PRD 原文明确要求测这个带,不是
ISSUE-21 那个更宽的 fractional 带)。

### 5.4 建议的 PRD 修正（需 owner 批准,本设计不能单方面追加门禁）

本设计建议在 §7 的 G2 门禁**之前**加一个可选的**早退检查点**：T0 配对
消融（含 P2 混淆控制）若显示"三态 emission 重分类本身不改变 TVD",且
不存在其他新假设,允许在 R3 训练**开工前**触发 §7.4 的搁置流程,而不是
必须等到 R4 训练完成才能第一次判 G2。这是因为 T0 是零成本诊断（复用已批
quota,不训练),提前暴露"假设从根上就不成立"能省下整个 R3–R5 的算力
投入。**这是本设计对 PRD 的建议性修正,不是既成事实**——需要 owner 在
签署本设计稿或后续 prereg 时明确批准,才能作为搁置流程的合法触发点;在
批准之前,T0 只是诊断性参考,不改变 PRD §7.4 原有"G1 或 G2 仍失败"的
唯一触发条件。

---

## 6. 接口变更清单（供实现直接照做）

### 6.1 新增文件/符号

| 位置 | 符号 | 性质 |
|---|---|---|
| `emissions.py` | `FrameEmission`（dataclass） | 新增，纯 tuple/float 字段 |
| `emissions.py` | `frame_loglik(frame, state_is_present) -> float` | 新增 |
| `emissions.py` | `PooledEpochEmission`（dataclass） | 新增 |
| `emissions.py` | `pool_epoch_frame_emissions(frames, gap_days) -> list[PooledEpochEmission]` | 新增 |
| `seam.py` | `EstimatorConfig.frame_emissions: Sequence[FrameEmission] \| None = None` | 新增字段，默认 `None`，向后兼容 |
| `seam.py` | `is_already_present(result) -> bool` | 新增纯函数 |
| `seam.py` | `is_beyond_window(result) -> bool` | 新增纯函数 |
| `changepoint.py` | `estimate_changepoint` 内新增 `config.frame_emissions is not None` 分支 | 新增分支，现状分支逻辑字节不变 |
| 新模块 `training_targets.py` | `TeacherBracket`（dataclass）、`build_k_i(bracket, cells) -> frozenset[int]` | 新增模块 |
| 新模块 `losses.py` | `interval_marginal_nll(posterior, k_i) -> float`、`frame_calibration_loss(predicted, teacher_symbol) -> float` | 新增模块，纯 Python float，无 torch 依赖（供未来训练脚本 import，保持 `estimators/` 现有零 numpy/torch 依赖的风格） |

### 6.2 修改点（均为新增分支/新增函数，不修改现状分支的既有逻辑）

- `EstimatorConfig`：新增字段后，`estimate_changepoint`/`fit_emissions_em`
  的现状分支（`config.emissions`/symmetric-noise fallback）**不读取**新
  字段，保持字节级不变（回归测试见 6.3）。
- `estimate_changepoint`：需要在函数入口新增一条互斥校验——
  `config.emissions is not None and config.frame_emissions is not None`
  → `raise ValueError`（仿照 `EmissionModel.__post_init__` 对重复
  stratum 的"宁可显式报错也不要静默双写"风格）。

### 6.3 测试点清单

1. `frame_loglik` 极限值：`q=0.0 → 0.0` 精确成立（两个 state 分支都是）；
   `q=1.0 → math.log(e)` 精确成立；固定 `e≠0.5` 时对 `q` 单调。
2. `pool_epoch_frame_emissions` 的**重复注入不变性**：向一个 epoch 内
   注入一个 `q` 不超过既有最大值的近重复帧，`PooledEpochEmission` 不变
   （直接对标 `pava.py`/`changepoint.py` 已有的 duplicate-injection
   invariance 测试传统）。
3. TLO 硬门控组合：构造 `target_localized=False` 的 `FrameEmission`
   （或其上游代理），断言 `q_effective` 精确为 `0.0`，不论 quality
   head 原始输出多高（这是一条独立单测，不能只靠集成测试覆盖，因为
   红线本身就是这条组合规则）。
   1. **`target_localized=True ∧ abstain=True`**（`observation.py` 允许
      的合法组合，如 `failure_reason="low_confidence"`/`"dark_zone"`）：
      断言 `q_effective` 精确为 `0.0`——只读 `target_localized` 会漏判
      这一条,必须显式覆盖。
   2. **`tlo=None`（桥接态）**：断言 `q_effective == q_model`（no-op，
      不清零）且 `localization_pending=True` 被下传;并断言"把 `tlo=
      None` 当 `target_localized=False`"的错误实现（`q if (tlo and
      tlo.target_localized) else 0.0`）在这条用例上会给出错误结果
      （回归护栏，防止未来重构悄悄倒退成这个错误写法）。
4. `build_k_i`：`left_censored → {0}`；`right_censored → {T}`；
   `interval` 情形分别测"教师区间恰好等于学生一个 cell"（退化到
   `{单一下标}`）与"教师区间粗于/细于学生网格"（覆盖多个/被多个覆盖）
   两种非平凡重叠场景；空重叠必须 `raise`，不能静默返回空集。
5. `interval_marginal_nll`：`len(K_i)==1` 时精确退化为
   `-log(posterior[k])`（与普通 NLL 一致的代数性质检验）；在 `K_i` 内部
   任意重新分配后验质量（保持总和不变）时,损失值不变（只有集合内的和
   重要，不是分布形状——直接的代数性质测试）。
6. **回归门禁（最重要的一条）**：现状离散符号路径（`fit_emissions_em`
   + `estimate_changepoint` 的 `config.emissions`/symmetric-noise
   分支）在银行化 panel 上的输出，扩展前后**字节级不变**——直接复用
   `epochs.py` 模块文档里已经用过的"banked-panel regression gate"惯例。
7. 互斥校验：`EstimatorConfig(emissions=..., frame_emissions=...)`
   同时非空 → `estimate_changepoint` 必须 `raise ValueError`。
8. §5.3 T0/T1 用到的 TVD 统计量本身需要一个独立小测试：验证顺序无关性
   （对 reps 输入重新排列不改变 pairwise TVD 集合），并与
   `issue03_gates.py` 里已经验证过的参考实现做一次数值对照（同一份小
   合成数据，两个实现输出必须一致），防止本设计新写的复测脚本引入
   与既有 D3/AC5 口径不一致的计算误差。

---

## 7. 非目标 / 与本 PRD 其他条目的边界

- 本设计不涉及 §5 语义定位层的具体级联实现——`TargetLocalizationObservation`
  的 schema 已由并行工作流交付为落地代码（`src/solar_backdating/
  localization/observation.py`，51 项测试），本文只消费它的两个字段
  `target_localized` 与 `abstain`（§3.2，2026-07-19 交叉核对后从"只读
  一个布尔字段"订正为"两个字段都读"），以及 `tlo is None` 桥接态下
  `effective_label` 已实现的"不降级、`localization_pending=True`"策略。
- 本设计不改变生产链（`infer_install_dates.py`、ISSUE-22 AC5、DECISION-A
  已判 GO 的 fractional-caliber 生产默认）——所有新增代码路径只在
  `config.frame_emissions` 被显式设置时才激活，缺省行为字节不变。
- 不做 LoRA/backbone 训练相关的接口设计（owner veto 仍然生效，PRD §0.4）。
- R2 定位层的净化率门禁（G3）、R3 特征缓存格式不在本设计范围内。

---

## 修订记录

- **2026-07-19，交叉核对修订**：TLO schema 作者（`src/solar_backdating/
  localization/observation.py`，落地代码 + 51 项测试）对本稿 §3.2 做
  交叉核对，发现两处与已实现 schema 语义不符的缝隙，经 team-lead 裁决
  由本稿收口（schema 侧不动）：①硬 AND 公式漏读 `abstain` 字段——
  `target_localized=True ∧ abstain=True` 是 schema 允许的合法组合
  （如 `low_confidence`/`dark_zone`），标签层 `effective_label` 对该
  组合同等降级为 `uninformative`，似然层原表述未同等处理；②`tlo is
  None`（桥接态）被错误地隐式等同 `target_localized=False`，与
  `effective_label` 对 `tlo is None` 的既定"不降级 + `localization_
  pending=True`"策略相反。已订正 §3.2 公式、§6.3 新增两条测试点、§7
  措辞（"另一份并行设计稿"→"已交付落地代码"，消费字段从一个改为两个）。
- **2026-07-19，team-lead 收口**：§3.2 修订后曾同时存在"单表达式公式
  （`tlo is not None` 收进条件、else 清零）"与"`tlo is None` no-op
  条款"两个代码块——单看前者会把 `None` 清零，恰是缝隙②想防的误读。
  已合并为唯一的三分支规范伪码，并注明不得简写回单表达式。
