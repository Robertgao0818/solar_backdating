# Cape Town top52 grid：Gemini backdating 执行交接方案

日期：2026-07-31（Codex review policy amendment：2026-08-01）  
交接对象：GPT-5.6-luna  
上位计划：[`RUN-cape-town-backdating-plan-2026-07-24.md`](RUN-cape-town-backdating-plan-2026-07-24.md)  
状态：**执行前设计冻结，等待 CT-05 QA 与 owner go/no-go**

本版（2026-07-31）已加入 CT/JHB 可用日期密度审计与 density-aware interval
policy；在 CT-08 实测前，不把 31k logical-call 估计视为承诺。

Review policy update (2026-08-01): CT-06 placement review, CT-11 jump-critical
review, and exception review are assigned to Codex visual review. Use the
[Codex visual review protocol](CODEX_VISUAL_REVIEW_PROTOCOL.md); no future
human annotation is required. This is AI-reviewed QA, not an independent
human gold standard, so report repeat agreement and review agreement rather
than human inter-annotator agreement or physical install-date accuracy.

本文是给小模型执行的操作稿。除非本文明确写“可选”，不要自行改变
scope、模型、cutoff、geometry、provider、重试方式或输出目录。历史产物只读，
任何重跑都写新的 versioned run root。

冲突优先级：本文的 primary/rescue/配额路由覆盖旧 tracker 中 CT-04 的三层
`runtime_lock_v3` 启动配置；旧 lock/canary 只保留作历史 provenance。scope、
cutoff、geometry、GEHI provider 等仍以上位 CT PRD 为准。

给 Luna 的执行姿势：每次只推进一个 gate；先做只读检查，再做小样本，最后才
扩大。任何 gate 非 `PASS` 都停在当前 run root，写清证据和下一步，不猜测、不
删文件、不用 `touch .done`、不把失败行改成 `absent`，也不自行改变模型或 cutoff。

## 先给结论

1. **主跑继续用 `gemini-3.1-flash-lite`，不要在同一个生产 cohort 中把
   `gemini-3-flash` 当作批量加速器。** 采用“受控 burst + 配额预算 + 必要时
   冷却后 resume”，而不是撞墙后让 80 个 worker 继续重试。
2. **提高单次输入，但要提高的是首轮的有效信息量，不是盲目把上限从 5
   改成 10。** 首轮固定为最多 5 张 census 前历史证据，再加最多 1 张
   `reference_only` 影像；通用 batch cap 先锁 6。仅改
   `gemini_max_dates_per_call` 的收益很小，而且会让模型看到不同上下文。
3. **兜底模型锁为请求别名 `gemini-3.6-flash-high`，并验证精确返回版本
   `gemini-3.6-flash`。** 它只用于隔离的 anchor-level rescue/adjudication，
   不作为 Lite 配额耗尽时的透明替代，也不覆盖 Lite 的 primary 状态。
4. CT-05 的 6 个 `no_history` 锚点、1 个 Wayback-only 锚点、以及
   `t00107162` 的超 96 m footprint 必须各自进入显式 lane；不能改写为
   `absent`，也不能因 TM 为空而丢掉 Wayback。
5. CT 的可用 pre-census 日期约为 RUN 3 的 2.1 倍；因此 bisection 默认采用
   exhaustive **new-only cap6**（`222222` 分块，端点在 state 中合并），不要把
   `122221` endpoint-context 形状全量铺开。只有 `m<=4` 的小 bracket 才可在
   context cache 通过后使用 `A + new + P`；真正递归搜索必须先跑独立 pilot。

### 模型切换的决策表（给 Luna 的硬规则）

| 情形 | 允许的动作 | 不允许的动作 |
|---|---|---|
| Lite 仍健康、预算未到线 | 继续 Lite burst | 为了“更快”把同一 cohort 改成 3-flash |
| Lite 达到 `work_budget` 或确认 429 | 写 `PAUSED_QUOTA`，等 reset 后 Lite resume | 继续重试、透明切 3-flash/3.6 |
| 需要比较 3-flash 的准确率 | 单独 benchmark arm、独立 run root/store、不可合并 | 把 benchmark 行混入 primary |
| Lite operational failure 或科学 review | 独立 3.6 rescue replay | 在 primary state 内悄悄换模型 |

这里的“冷却”是可恢复的 checkpoint pause，不是让 worker 空转等待。若
optimized 6-slot canary 证明总调用数低于一个窗口的安全预算，整批可以在一个
窗口内完成；仍然保留 breaker，因为 retry、schema salvage 和 rescue 会让先验
估计失真。

因此对本次 CT top52 的二选一答案是：**选 Lite 的有效 6-slot batching +
受控冷却/resume；不选 3-flash 在同一 cohort 内混跑加速。**

## 为什么这样定：可量化的 run3 证据

对 RUN 3 的 41,393 个 `scan_state` 做了只读审计：

| 指标 | 实测 |
|---|---:|
| 总 observations | 311,195 |
| 首次 batch 请求（`batch_attempt_1`） | 91,472 |
| 二次 batch 请求 | 75 |
| per-image fallback 请求 | 102 |
| `initial` rounds | 41,393，全部 5 张 |
| `anchor_recovery` rounds | 31,087，全部 1 张 |
| 首轮漏掉最新 census 前影像、随后进入 recovery | 30,801 |
| `bisection` rounds | 16,630 |

因此：

- 只把 batch cap 从 5 改成 6，按 RUN 3 的真实 round 分布只少约 326 个
  logical chunks；改成 8 也只少约 2,232 个，且增加 schema/context 风险。
- 首轮显式纳入最新 census 前影像，理论上可消掉约 30,801 个单张
  recovery 请求。CT 的候选密度不同，不能直接承诺同样比例，必须用六网格
  canary 实测后再外推。
- 当前 CT 的生产 catalog 是 21,447 个有历史的 anchor、1,178,227 个
  2019+ candidate rows；6 个 anchor 没有任何历史。CT 共有 21,453 个 anchor，
  不能把前者误当成完整 denominator。

按 RUN 3 的请求/anchor 粗略外推，旧 5 张设计的 CT primary 约为
`21,453 × 2.21 ≈ 47,400` 个 logical calls；新首轮 6-slot 设计若把大部分
recovery 消掉，约为 31k 左右。两者都只是先验，CT-08 的实测 call ledger
才是放大生产的依据。

## 配额决策与计算

已知近似配额是每个账号每 5 小时 5,000 次。RUN 3 观察到 11 个账号的
`quotaResetTimeStamp` 在约 40 秒内同步，且 Sub2API 有粗粒度
`antigravity:gemini` cooldown：Lite 撞墙后，Flash/Pro 也可能得到
`503 No available Gemini accounts`。因此不能把“每账号独立、随时切模型”当作
可靠假设。

### 安全预算

启动前读取实际 active/schedulable account 数 `N`，不硬编码 11；如果 roster
读不到或账号状态不确定，直接 STOP，不用猜一个 `N`。把一个 5 小时窗口的预算
拆成“总安全上限、canary 预留、重试预留”三部分：

`N` 只有在 gateway 能证明这些账号对 Lite 是可加总的独立 quota bucket 时才
按账号计数；若只能证明 pool-level 5,000，保守地取 `N_effective=1`，不要用
账号数量把容量放大。把这条判断和证据（roster、model-specific reset、最近
成功 attempt 计数）写入 lock。

```text
gross_safe_budget = floor(N * 5000 * 0.80)
canary_reserve = max(10, configured_canary_attempts)
retry_reserve = ceil(0.10 * projected_logical_calls)
work_budget = gross_safe_budget - canary_reserve - retry_reserve
warning_budget = floor(work_budget * 0.90)
```

`gross_safe_budget` 是任何 HTTP attempt 都不能越过的 80% 硬线；`work_budget`
是“不再接纳新 logical work”的线，二者之间只留给已在途 attempt 和其 bounded
retry。canary 和每一次真实 HTTP attempt（包括 transport retry、batch attempt 2、
per-image fallback）都记入同一个窗口账本，不能在每个 wave 重新把计数归零。
`retry_reserve` 先按预计 logical calls 的 10% 计；若
canary 测得 schema retry + transport retry 超过 10%，按实测上调。达到
`warning_budget` 后不再启动新 wave，只允许当前 bounded wave 收尾；到
`work_budget` 必须停止接纳新的 logical work，最终由 `gross_safe_budget`
作为 transport 硬停线。

gross 值示例（尚未扣 canary/retry）：

| active accounts | 原始配额 | 80% gross safe |
|---:|---:|---:|
| 10 | 50,000 | 40,000 |
| 11 | 55,000 | 44,000 |
| 12 | 60,000 | 48,000 |

例如 `N=11`、canary=10、预计 31,000 logical calls 时，
`retry_reserve=3,100`，本窗口 `work_budget=40,890`，90% warning 为 36,801。
旧 5-pick 先验约 47,400 calls 会超过该线，必须分窗；6-slot 先验约 31k 只是
canary 前的假设，不能当作承诺。

在 6 QPS 下，31k logical calls 的理想发送时间约 86 分钟，40.9k 约 114
分钟，47.4k 约 132 分钟；真实 wall time 还要加 transport/schema retry、下载
前置和 worker drain。因此 6-slot 可能把 CT 主跑压进一个窗口，但只有 ledger
和 canary 能证明这一点；不要按理想除法提前承诺完成时间。

### CT 密度校正：不能把 RUN 3 的 bisection 分布原样外推

这一点在放大生产前已经用冻结目录做了只读比较。计算口径是：对每个 anchor
按 `(anchor_id, capture_date)` 去重，用该行自己的 `census_date` 分开
pre/post；CT 的 6 个 `no_history` anchor 保留在 21,453 行总体分母中并按 0
个可用日期计。JHB 来源是
`basemap_rebuild_2026-07-13/gehi_vintage_candidates_full_run3.csv`，CT
来源是
`cape_town_top52_backdating_v1_20260724/ct05_catalog_v1/merged/`
`gehi_vintage_candidates_ct05_run3_2019plus.csv`。

| 口径 | JHB RUN 3 | CT top52 | CT/JHB |
|---|---:|---:|---:|
| anchor 分母 | 41,393 | 21,453（其中 6 个 no-history） | — |
| distinct catalog dates/anchor（全部）均值/中位数 | 28.0 / 34 | 54.9 / 56 | 1.96× |
| pre-census dates/anchor（各自 cutoff）均值/中位数 | 25.0 / 31 | 52.3 / 53 | 2.09× |
| post-census reference dates/anchor 均值 | 3.0 | 2.6 | — |
| TM dates/anchor 均值 | 22.0 | 49.9 | 2.27× |
| Wayback dates/anchor 均值 | 7.0 | 5.0 | — |

为排除 cutoff 日期不同造成的优势，再把两边都截到 `2024-02-21`：JHB
pre-census 均值为 24.69（中位数 31），CT 为 50.54（中位数 52），仍为
2.05×。日期间距也显示是“更密”而不只是“时间跨度更长”：

CT 内部按 arm 也没有明显偏差：A24 的 pre-census 均值/中位数为
52.32 / 53，A48 为 52.61 / 53；因此不能把密度差异归因于某一个 review
extent lane。

| common-cutoff 相邻 gap（所有 anchor pooled） | JHB | CT |
|---|---:|---:|
| mean / median gap | 69.3 / 31 日 | 35.5 / 30 日 |
| 75th / 90th percentile | 90 / 196 日 | 31 / 44 日 |
| gap ≤31 日 | 65.3% | 89.9% |
| gap >90 日 | 23.1% | 4.7% |

CT 的 RUN3-window download plan 有 1,178,227 个去重候选；这里使用的是修正后的
2019+ production window，不是已废弃的 2009+ 探索目录 2,649,905 行；其中
1,025,312 为 `ok`、152,847 为 `skipped_existing`，仅 68 行是终态失败。因此这里的
若把 `ok` 与 `skipped_existing` 都视为已通过下载阶段，则完整 21,453 分母上的
有效日期均值约为 54.92（与 catalog 的 54.9 相同）。这里的“可用日期”主要是
catalog/下载 manifest 的 `(anchor,date)`，不能用某个本地
目录是否存在来重算分母；那 68 行必须在 CT-05 QA 中单独处理。

对区间搜索的直接影响如下。

1. 当前 planner 的 5 个历史首轮点在 JHB 的相邻点之间平均留下约 5.0 个未选
   日期（中位数 6）；同样的 5 点规则在 CT 留下约 11.84 个（中位数 12，
   90th percentile 15，最大 16）。因此 RUN 3 bisection 实测均值 4.40 个
   内点不能当成 CT 先验。
2. **CT 默认仍采用 exhaustive、new-only bisection，batch cap=6。** 例如有
   12 个内点就发两个 `222222` chunk；旧的 absent/present 端点在 state timeline
   中合并，不在 HTTP 请求里重复。这样保留“最近可用 absent/present 边界”的
   科学保证，并把 12 个内点的典型成本控制在 2 个 logical calls。
3. 只有当某个 bracket 的内点数 `m <= 4` 且 context-cache contract 已通过测试
   时，才允许一次 `A + m 个 new + P` 的 endpoint-context 请求（六图上限时形状
   是 `122221`，不是 `12221`）。`m > 4` 时不要为了视觉上下文把每个 chunk
   强行变成 endpoint-context；重复端点会把典型 CT bracket 从 2 calls 推到
   3 calls。
   注意当前 content-addressed verdict store 会在 HTTP 前剔除已有 per-chip
   cache hit；单纯把 A/P 加到 pick list，实际发出的请求可能又变回只有 new
   dates。若要启用 endpoint-context，必须新增 window-level
   `bisection_context` cache key（或显式 context/bypass 模式），并在 audit 中
   校验“请求实际包含的图像数和顺序”，不能只看 planner JSON。
4. 若 owner 想采用真正递归的二分（先抽少数内点、缩窄 bracket、再开下一轮），
   必须作为独立 canary arm；它不再等价于 exhaustive interval guarantee，不能
   在 primary run 中静默替换。递归 arm 至少记录漏检/非单调率和最终 interval
   宽度，再决定是否接受该 trade-off。
5. 若 density canary 证明 bisection 才是主要成本，可在 6-slot arm 之后另做
   `8 historical` benchmark：按当前均匀 rank 选点，CT 首轮相邻窗口内点的
   先验中位约从 12 降到 6. 这只是 benchmark；它不能无测试地挤掉
   `reference_only` 语义，也不能把 8/10-slot 结果写回 primary store。

仅作预算 sanity check：如果 CT 的 transition/bracket 比例暂按 RUN 3 的
`16,630 / 41,393 = 40.2%`，则约有 8,620 个 bisection brackets。用 CT 首轮
窗口的中位 `m=12` 代入，new-only cap6 约需 17,240 个 bisection calls，
endpoint-context cap6（每次 4 个新点）约需 25,860 个；再加 21,453 个
initial、残余 recovery、retry 和 rescue，优化方案的合理预期更接近 39k--48k
logical-call 区间（HTTP attempts 还要乘以 canary 测得的 retry factor），而不是未经
密度修正的 31k。这个算式只用于
设置 quota window 和 wave 大小，最终以 CT-08 ledger 为准。

CT-08 六网格 canary 必须按 anchor 记录 `n_pre_dates`、首轮 picks、bracket
宽度、内点数 `m`、new-only/context 两种 call 预测与实测 calls、最终 interval
宽度，以及 endpoint disagreement。放大前用实测分布重算预算；在 density
canary 之前，文中约 31k 的优化估计只能视为**不含 bisection 密度的乐观下界**，
不能作为单窗口完成承诺。

每次 HTTP attempt（包括 transport retry、batch attempt 2、per-image fallback、
canary）都要记入 `quota_ledger.jsonl`；不要只数 anchor 或 observations。

### 速率

- CT-07 smoke：30 workers / 4 QPS。
- CT-08 canary：40 workers / 6 QPS；前 15 分钟观察延迟、429/503、schema
  retry，再决定是否保持 6 QPS。
- 生产：40 workers / 6 QPS；离线 GEHI 下不需要 80 workers/8 QPS。只有在
  canary 证明 gateway 稳定且剩余预算充足时，才可把 workers 提到 60，QPS
  仍先保持 6。

6 QPS 不是为了“榨干配额”，而是给约 31k calls 一个可测的完成速度，同时留出
15--20% 的重试/账号计数误差余量。若 ledger 预计会越过 `work_budget`，应提前
停在 checkpoint；不要等真实 429 才开始保存状态。

### 冷却与断路器

必须在 transport 层实现共享的 `QuotaCircuitBreaker`：

1. 达到 `warning_budget` 后不启动新 wave；达到 `work_budget` 后停止接纳新
   logical batch/anchor round，只允许已在途 batch 的 bounded retry 收尾。
2. 达到 `gross_safe_budget` 时，禁止任何新的 HTTP attempt，抛出可识别的
   `QuotaBudgetReached`，不要把当前帧写成 `gemini_failed`。
3. 首次确认到上游 `429 RESOURCE_EXHAUSTED`、或连续两次全池
   `503 No available Gemini accounts` 时，设置全局 breaker；停止继续提交
   请求，保留已完成 round，写 `PAUSED_QUOTA`。不要让每个 worker 各自跑
   8 次 × 45 秒的旧 pool retry。
4. reset 时间优先取 gateway/account 状态的最大
   `quotaResetTimeStamp`；没有可信时间时使用“本轮首个 Lite 请求时间 + 5h”。
   再加 5 分钟 grace，期间**任何 Gemini 模型都不发请求**。
5. 冷却后必须重新做 Lite canary，确认 exact `modelVersion`、schema、图像
   路径和 gateway health，再 resume。不要用 `touch .done` 绕过失败。

建议把 breaker 写成显式状态机：

```text
RUNNING
  ├─ ledger >= warning_budget    → DRAINING_QUOTA（不启新 wave）
  ├─ ledger >= work_budget       → DRAINING_QUOTA（不接新 logical work）
  ├─ ledger >= gross_safe_budget → PAUSED_QUOTA(reason=hard_budget)
  ├─ 429 RESOURCE_EXHAUSTED      → PAUSED_QUOTA(reason=upstream_429)
  └─ 2 ×全池 503                 → PAUSED_QUOTA(reason=pool_503)
DRAINING_QUOTA
  └─ in_flight == 0               → PAUSED_QUOTA(reason=budget)
PAUSED_QUOTA
  └─ now < reset+5min             → 不发任何 Gemini 请求
READY_TO_RESUME
  └─ fresh Lite canary PASS       → RUNNING
```

每个请求的最小账本行至少包含：`window_id`、UTC 时间、anchor、run/wave、
requested alias、returned `modelVersion`、model tier、round/chunk、
`n_picks`、`attempt_kind`（canary/batch1/batch2/per_image/transport_retry）、
HTTP 状态、是否计入配额、错误分类。账本追加失败时也要 STOP；不能继续跑而
事后补记。

不要让主 Python 进程 sleep 五小时。预算暂停时原子写
`PAUSED_QUOTA.json`（reason、window_id、ledger count、in-flight count、
resume_after、last completed anchor/wave），以专用非零退出码退出；外层 wrapper
只负责到点后做 canary 和重启同一命令。wrapper 不能写 `.done`，也不能把该
退出码归类成 anchor failure。

## 固定输入、目录与模型契约

执行前在 shell 中只使用这些显式路径变量（不要指向 JHB RUN 3）：

```bash
CT_ROOT="/home/gao/zasolar_data/geid_temporal/cape_town_top52_backdating_v1_20260724"
ANCHORS="$CT_ROOT/anchors_v1/anchors_all.csv"
A24="$CT_ROOT/anchors_v1/anchors_A24.csv"
A48="$CT_ROOT/anchors_v1/anchors_A48.csv"
CATALOG="$CT_ROOT/ct05_catalog_v1/merged/gehi_vintage_candidates_ct05_run3_2019plus.csv"
CHIPS="$CT_ROOT/ct05_download_v1/chips"
RUN_ROOT="$CT_ROOT/production_lite_v2_20260731"
```

冻结的科学输入：

- 52 个完整 tie-band grids，21,453 anchors；A24=19,729，A48=1,724。
- region=`cape_town`，CRS=`EPSG:32734`。
- GEHI sole provider，Merged(TM+Wayback)，发现/下载优先 z19，z18 仅明确
  fallback；候选去重 key 是 `(anchor_id,capture_date)`，不能按 version 去重。
- census cutoff=`2025-01-31`；post-census 最多 3 个 reference dates，永远
  不进入 install interval、monotonicity 或 failure percentage。
- primary alias=`gemini-3.1-flash-lite`。
- rescue alias=`gemini-3.6-flash-high`；每次成功请求记录并校验 exact
  `modelVersion=gemini-3.6-flash`。
- 本次生产 primary 不使用 `gemini-3-flash`。如 owner 要评估它，只能作为
  单独的、不可合并的模型比较 arm；应在 Lite 生产窗口之外运行，除非已证明
  使用完全隔离的 account pool。

固定异常 roster（执行时仍需从 frozen manifests 重新 assert）：

| lane | anchor / source | 处理 |
|---|---|---|
| no history | `..._t00010449`, `..._t00010452`, `..._t00038228`, `..._t00038233`, `..._t00038296`, `..._t00038301` | 不送 Gemini；显式 undated/no-history |
| Wayback only | `..._t00038314` | TM=0 仍必须由 offline Wayback 完成 |
| >96 m footprint | `..._t00107162` / source feature `107161` / `CPT3791` | exception extent 或 Codex visual review，禁止静默裁剪 |

表中的 `...` 前缀均为 `ct_full_inventory_2026_06_21_merged`；生成 manifest 时
使用完整 anchor_id，不允许把缩写写入运行输入。

不要复用 `runtime_lock_v3`：其中 round2 仍是 `gemini-3-flash`、qps=0.5，且
canary 是 JHB 图像。重新生成 `runtime_lock_v4`（或更清楚的
`runtime_lock_ct52_lite_v2`），把 planner/prompt/schema/quota-policy 的 hash
也放进去。canary 结果写独立 `canary/<timestamp>.json`；锁一旦冻结，不再由
canary 脚本原地改写。

## 必须先落地的 P0 代码修正

### P0-A：首轮 6-slot planner（保持科学语义）

修改 `scripts/temporal/scan_config.py`、`scan_decision.py`、
`run_adaptive_scan.py`：

1. 保留 `picks_per_round=5` 的含义为“历史证据数”，新增
   `initial_reference_slots=1`，并把 `gemini_max_dates_per_call` 设为 6。
2. `plan_initial_round` 先按 `capture_date < census_date` 选最多 5 张历史
   影像，再从 `capture_date > census_date` 选最多 1 张 reference-only 影像。
   两类按日期合并后送入 batch；若没有 reference，首轮仍最多 5 张。
3. 首轮必须包含最新可用的 census 前影像。若历史影像少于 5 张，不要用
   post-census 影像冒充历史证据来填空。
4. `VintageEntry`/`Pick`/`RoundResult` 至少要能追踪
   `reference_only`（或通过不可歧义的 `capture_date > census_date` 规则重建），
   并 bump scan-state spec version；旧状态禁止混入新 run root。
5. `gemini_max_dates_per_call=8/10` 暂不作为默认值。只有 6-slot canary 的
   schema 与视觉 QA 通过后，才可做一个独立 8-slot benchmark；benchmark
   不得污染 primary verdict store。

6. 当前批量 prompt 的 census-calibration helper 只会按日期猜
   “reference” chip。实现 6-slot 时必须把 `reference_only` 作为明确字段或
   sidecar 传入，同时更新 prompt：它可以帮助校准外观，但不能被 adaptive
   decision、dip repair 或 interval inference 当作 evidence。不能只靠事后按
   日期过滤来补救。

### P0-B：禁止 reference frame 泄漏到 evidence

当前 `infer_install_dates.py` 的 `infer_one` 会遍历所有 results，而不是自动
排除 state.census_date 之后的 reference。CT 必须修正为：

- `done_appears` 的 absent/present、dip repair、inverted check 只用
  `capture_date < census_date`；
- `done_installed_during_census` 的 latest absent 也只用 cutoff 前结果，
  census date 是 upper bound；
- post-census rows 保留在 scan_state/provenance 供人看，但不能令 interval
  超过 2025-01-31，也不能触发 `marker_missed_pv`；
- deliverable builder 继续保留 census ceiling 作为第二道防线。

新增一个回归测试：同一 anchor 加入 post-census absent/present 后，interval、
status 和 dip-repair 结果必须与删除这些 rows 完全一致。

### P0-C：修复 CT 离线 Merged 边界

当前 loader 把“anchor 没有 TM key”和“anchor 在 CSV 中但 TM 为空”混为一谈；
Merged 路径会在 Wayback-only anchor 还没机会进入时先对 TM 抛错。必须：

- 启动覆盖检查按 TM∪Wayback 的 union 计数，不是只看 TM；
- anchor 在 CSV 中但 provider 列表为空时传入空 list，不触发 live GEHI；
- 增加集成测试：TM=0、Wayback=9、`--no-live-gehi --offline-wayback`
  能完成 catalog build 和至少一个 score round；
- CT 的 6 个 `catalog_status=no_history` anchor 不送 Gemini。生成显式
  `no_history` lane/terminal state（建议映射到
  `done_ambiguous_no_recent_anchor`，notes 写清 `catalog_status=no_history`），
  并在最终 21,453 行交付中保留它们；绝不能把空 catalog 当作 absent。

### P0-D：模型身份和配额可审计

修改 `gemini_solar_image_review.py` 与审计 writer：

- 每个 batch attempt 记录 `requested_alias`、`returned_model_version`、
  `model_tier`、`round_id`、`n_picks`、`transport_attempts`；
- 缺少或漂移的 `modelVersion` 立即停 run，不能只记录 config 中的 alias；
- transport 层把每次真实 HTTP attempt 送入共享 quota ledger；
- `QuotaBudgetReached`/`QuotaExhausted` 必须穿过 `_attempt_batch`，不能被
  `score_batch_with_fallback` 吞成 `gemini_failed`；
- primary verdict store 只用于 primary Lite；3.6 rescue 用新 store、新 run ID。

### P0-E：当前三层路由的明确化

当前工作树已经加入 `--troubleshooting-model`，但它目前是在同一个进程内按
`review_required`/历史失败自动换模型；如果直接传 3.6，会污染 primary
cohort。先把它改成“独立 rescue manifest 才能启用”的 fail-closed 路由，并补
测试：primary 的每一条 attempt 都只能是 Lite；rescue 的每一条 attempt 都只能
是 3.6，二者 store/run ID 不得相同。

primary 命令必须显式传：

```text
--round1-model gemini-3.1-flash-lite
--round2-model gemini-3.1-flash-lite
--cheap-round-types initial,bisection,walk_back,tail,anchor_recovery
--troubleshooting-model ""
```

3.6 只在后面的独立 rescue 命令中出现。这样所有普通 adaptive rounds 是同一
Lite 仪器；3.6 不会悄悄混进普通 `anchor_recovery`。为 `review_required`、
quota pause、schema retry、model drift 分别写 routing tests。若 Luna 选择改造
CLI 而不是接受空字符串，等价要求是 primary 的 high route 必须在代码层不可达。

为 3.6 独立复核新增 `--routing-salt-seed`（或等价 `--repetition-id`），nonce
至少包含 `seed:model:anchor:round`。seed 必须进入 attempt audit 和 verdict-key
instruction extras；否则当前 deterministic salt 和 content-addressed store 会把
rep2 变成 rep1 的缓存 replay。每个 rescue repetition 还必须使用全新的 state
root 和 verdict store，不能只换 salt。

最小 focused test 集（名称可不同，但断言不可省略）：

- planner：5 个 `< census_date` 历史 + 至多 1 个 `reference_only`，且最新历史
  必选；
- inference：加入/删除 post-census rows 前后 interval、status、dip-repair
  完全相同；
- Merged offline：TM=0、Wayback>0 能 score；TM∪Wayback coverage 不漏行；
- routing：primary 永不发 3.6，rescue 永不发 Lite/3-flash，modelVersion 漂移
  和 quota breaker 都 fail-closed；重复 run 的 salt/key/store 不会 cache replay；
- ledger：每个 transport attempt 恰好一行，ledger append 失败会停止 worker。

## P1：验证顺序（先小后大）

### CT-05 退出门

只有以下都 PASS 才能用 Gemini 配额：

- CT catalog summary 的 21,453 anchor denominator 与 frozen anchors bijective；
- 6 no-history、1 Wayback-only、所有 `release_eligible=0`/download QA failure
  都有独立清单；
- 每个被 scorer 允许的 `(anchor,date)` chip 存在、hash stable、decode/pixel
  QA 通过；z19/z18 achieved zoom 已写入 sidecar；
- catalog/candidate/chip manifest SHA 写入新 lock；
- no-live-GEHI 试跑确认不会产生任何 GEHI subprocess。

### CT-06/CT-07：几何与请求形状 pilot

冻结 120 个 placement strata（A24/A48、面积 bin、boundary/halo、z18 fallback、
Wayback-only、低 confidence、review_required）和 60 个 E2E smoke（每个六网格
canary grid 10 个）。在同一批 anchor 上做两个**独立 run roots**：

- control：旧 5-pick initial planner；
- optimized：5 historical + 1 explicit reference planner。

两臂都用 Lite、temperature=0、分别用 `--routing-salt-seed control` / `optimized`
和不同 verdict store；不要把 control 的缓存 replay 到 optimized。预注册以下
go/no-go：

- exact model identity、JSON schema、chip provenance、state/interval schema 全部
  PASS；
- optimized 首次 batch 完整率与 control 相当，所有 unresolved transport/schema
  failures 最终为 0；
- logical calls/anchor 至少下降 25%；
- final coverage class、A24 小面积层、z18 fallback 层无系统性偏移；差异 anchor
  全部进入 review sheet，而不是按 confidence 自动择优。

这项 calls/anchor 比较必须把 bisection 内点数按密度分层，不能只比较首轮的
6-slot 节省。CT 的参考 arm 固定为 exhaustive new-only cap6；若 canary 的
`m` 中位数落在 5--12，另开 recursive-search pilot 与它配对比较；若中位数
超过 12，先暂停放大并由 owner 明确是否接受非 exhaustive 的区间保证。endpoint
context 仅在 `m <= 4` 的小 bracket 上作为诊断臂，不得作为 CT 全量默认。

任一项失败，primary 回退到 5-pick planner，仍按本文 quota ledger/cooldown
执行，不临时改用 3-flash。

CT-06 placement pass1 已于 2026-08-01 由 Codex 完成：冻结的 120 条 assignment
生成 10 张 CRS-aware contact sheet 和 append-only verdict sidecar。结果为
113 `PASS`、1 个明确的大 footprint `EXCEPTION`、6 个 no-history
`UNREVIEWABLE`、0 个 `MISALIGNED`；Wayback-only anchor 在 EPSG:3857 overlay
修复后通过。证据目录为
`~/zasolar_data/geid_temporal/cape_town_top52_backdating_v1_20260724/codex_review_20260801/ct06_placement_pass1_final/`。
这只关闭 placement/geometry QA；CT-05 的整体状态和 CT-08 的 quota STOP 不受影响。

### CT-08：六网格真实 canary

固定 2,700 anchors：`CPT2932`、`CPT2597`、`CPT3790`、`CPT3677`、`CPT2124`、
`CPT2713`（A24=2,455，A48=245）。A24 与 A48 必须分两次调用，因为
`--review-extent-m` 不同；两次共用同一 primary store，但不能并发打开同一个
store writer。

观察至少 15 分钟并写 `canary_metrics.json`：

- actual HTTP attempts、logical batch calls、observations/anchor；
- batch attempt 2、per-image fallback、429/502/503/504、latency p50/p95；
- Lite requested/exact model version；
- achieved z19/z18、pixel/decode failure、status/coverage distribution；
- quota ledger 用量、`work_budget` 和 `gross_safe_budget` 的剩余量。

另写一份 density 子表（不能只从总 observations 反推）：每个 anchor 的
`n_pre_dates`、五个历史首轮 pick 的日期/rank、实际 absent→present bracket 的
`m_interior`、`ceil(m/6)` new-only calls、若使用 context 则为 `ceil(m/4)`
calls。当前目录由五点首轮几何得到的相邻窗口内点中位 `m≈12`，所以看到 31k
的 calls 预测时必须把
bisection 贡献重新算进去；若实际中位 `m > 12`，CT-09 自动 STOP，转为
recursive-vs-exhaustive 小样本决策，不得靠加 workers 或切模型掩盖预算问题。

把 canary 测得的 `HTTP attempts / logical call`（含 schema/transport retry）
乘到剩余 logical-call 预测上，得到 `projected_remaining_http_attempts`；
`ledger_attempts` 必须来自同一 `window_id` 的追加账本，而不是日志行数猜测。
只有当 `projected_remaining_http_attempts <= (work_budget - ledger_attempts)` 且 owner
明确 go 才能进入 CT-09。不能用“进程退出码为 0”代替 nested audit。

## P2：生产波次

CT-08 通过后，剩余 46 grids 按 frozen manifest 生成稳定 wave files。建议每波
不超过 3,500 anchors，预期 6 波；脚本按 manifest 中 source_grid 的固定顺序
生成并把 SHA/列表写入 `production/waves/wave_plan.json`，不要手工改 grid。

预期波次（仅用于 sanity check，最终以生成器输出和 hash 为准）：

| wave | grids | anchors | A24 | A48 |
|---:|---|---:|---:|---:|
| 1 | CPT1855…CPT2283 | 3,489 | 3,245 | 244 |
| 2 | CPT2312…CPT2653 | 3,224 | 2,904 | 320 |
| 3 | CPT2654…CPT3188 | 3,154 | 2,846 | 308 |
| 4 | CPT3189…CPT3412 | 3,039 | 2,817 | 222 |
| 5 | CPT3469…CPT3641 | 3,485 | 3,236 | 249 |
| 6 | CPT3694…CPT3791 | 2,362 | 2,226 | 136 |

每波都拆成 `wave_N_A24.csv`、`wave_N_A48.csv`，按下面模板顺序执行；不要
把 A24/A48 混在一个 `--review-extent-m` 调用中：

```bash
python -u scripts/temporal/run_adaptive_scan.py \
  --anchors-csv "$WAVE_A24" \
  --scan-states-dir "$RUN_ROOT/primary/scan_states/a24" \
  --chips-dir "$RUN_ROOT/primary/unused_merged_chips" \
  --merged-tm-chips-dir "$CHIPS" \
  --merged-wayback-chips-dir "$CHIPS" \
  --audit-dir "$RUN_ROOT/primary/audit" \
  --provider Merged --scorer gemini \
  --anchor-workers 40 --qps 6 \
  --round1-model gemini-3.1-flash-lite \
  --round2-model gemini-3.1-flash-lite \
  --troubleshooting-model "" \
  --cheap-round-types initial,bisection,walk_back,tail,anchor_recovery \
  --routing-salt-mode target \
  --verdict-store "$RUN_ROOT/primary/verdict_store.jsonl" \
  --review-extent-m 24 \
  --census-mid-date-override 2025-01-31 \
  --offline-tm-catalog-csv "$CATALOG" --offline-wayback \
  --no-live-gehi --offline-require-chip-on-disk \
  --catalog-cache-dir "$RUN_ROOT/primary/catalog_cache" \
  2>&1 | tee -a "$RUN_ROOT/primary/logs/wave_N_a24.log"
```

`--troubleshooting-model ""` 是故意的：primary 禁止内嵌升级。lock 仍要记录
rescue alias，但 3.6 只能由后面的独立 rescue run 使用；如果 Luna 已将 CLI
改造成独立 rescue 模式，使用等价的显式禁用开关，并把实际命令/hash 写入 lock。

然后对 A48 重复，改 `WAVE_A48`、`scan_states/a48`、`--review-extent-m 48`。
主 run 不传 `--force-restart`；resume 只对 non-terminal state 做普通 resume。
每个 wave/arm 完成后立即跑 nested audit、quota ledger 汇总和 state count，
通过后才启动下一 arm。

### 波次中断与重启

- 正常断电/进程退出：保留 `scanning` state，重新执行同一命令 resume；先做
  fresh Lite canary。
- `done_ambiguous_gemini_failed` 或 `done_ambiguous_orchestrator_error` 是
  terminal，普通 resume **不会**重试它们。生成只含受影响 anchor 的 filtered
  CSV，写入 `retries/lite_retry_N/`，用 `--force-restart` 指向新的 state root；
  不要在 primary state 上原地覆盖。
- 重试成功后，重新跑 interval inference；保留 first-run 与 retry 的双份
  provenance，并在 merge manifest 中记录 precedence。
- 任何失败都不能用 `touch .done` 伪造 lane 完成。

## 3.6 Flash-High rescue 契约

### 触发集合

先用 Lite 在健康窗口完成所有可重试的 operational recovery。只有以下 anchor
才进入 3.6：

- Lite nested `gemini_failed`、schema/truncation 或 orchestrator error 在 Lite
  重试后仍存在；
- `review_required=true`、残余 non-monotonic、single-frame early presence、
  大于预设 interval gap，且需要 Codex visual/high-thinking review；
- 明确的 model/schema failure evidence，而不是普通 `done_installed...`。

### 执行与合并

1. 从 primary state/audit 生成 `rescue_manifest.csv`，每行带 reason、affected
   dates、primary model/exact version、chip hashes。
2. 新建 `rescue_3p6_<timestamp>/`，primary state/store 不可写；对每个 anchor
   从头 replay，所有 ordinary rounds 都用 `gemini-3.6-flash-high`，不要只替换
   一个 recovery frame 后假装同一仪器。
3. high canary 先确认 requested alias 与 exact `gemini-3.6-flash`。困难/会改变
   interval 的 anchor 至少跑 `rep1`、`rep2` 两个独立 high run（各自 state
   root、verdict store、routing-salt seed）；tuple/bracket 不一致才跑 `rep3`，
   仍不一致标为 `needs_review`。
4. 3.6 的结果只在 primary 是 operational failure 且 high response schema-valid、
   model identity stable 时补齐；若 primary 与 high 都有科学 verdict 但冲突，
   **不按 confidence 选赢家**，保留 primary、高结果和 `needs_review`。
5. 最终 CSV/GPKG 要有 `model_tier`、`primary_status`、`rescue_status`、
   `exact_model_version`、`rescue_reason`，不能把 high rescue 行伪装成 Lite。

3.6 使用独立的 `quota_ledger_3p6.jsonl`；在确认它自己的 roster/配额前不假设
“还有 5,000 次”。默认只开 4 workers / 2 QPS，单 anchor 最多两次独立 response，
分歧才用第三次；达到该模型安全预算、429 或连续全池 503 就暂停。Lite 的
`PAUSED_QUOTA` 窗口未结束时，即使 3.6 alias 看起来可用也先不发请求，因为
Sub2API 的 `antigravity:gemini` 粗粒度 cooldown 可能把它一起挡掉。

rescue 的命令形状应是独立 run（示意）：

```bash
python -u scripts/temporal/run_adaptive_scan.py \
  --anchors-csv "$RESCUE_MANIFEST" \
  --scan-states-dir "$RESCUE_REP_ROOT/scan_states" \
  --chips-dir "$RESCUE_REP_ROOT/unused_merged_chips" \
  --merged-tm-chips-dir "$CHIPS" \
  --merged-wayback-chips-dir "$CHIPS" \
  --audit-dir "$RESCUE_REP_ROOT/audit" \
  --provider Merged --scorer gemini \
  --round1-model gemini-3.6-flash-high \
  --round2-model gemini-3.6-flash-high \
  --troubleshooting-model "" \
  --cheap-round-types initial,bisection,walk_back,tail,anchor_recovery \
  --routing-salt-mode target \
  --routing-salt-seed "$RESCUE_REP_ID" \
  --verdict-store "$RESCUE_REP_ROOT/verdict_store.jsonl" \
  --review-extent-m "$RESCUE_EXTENT_M" \
  --census-mid-date-override 2025-01-31 \
  --offline-tm-catalog-csv "$CATALOG" --offline-wayback \
  --no-live-gehi --offline-require-chip-on-disk \
  --catalog-cache-dir "$RESCUE_REP_ROOT/catalog_cache" \
  --anchor-workers 4 --qps 2
```

这是 P0-E 增加 `--routing-salt-seed` 后的逻辑模板，不是让 Luna 复制未定义的
路径；`RESCUE_REP_ID` 分别取 `rep1`/`rep2`/必要时 `rep3`，每次对应一个新
`RESCUE_REP_ROOT`。实际 rescue manifest 必须先由 primary audit 生成并
hash-lock。若 Lite 处于 pool cooldown，3.6 也可能返回 503，因此 rescue 排队到
fresh high canary 能通过之后再发起。

已有 3.6 high adjudication 结果显示 exact full-tuple agreement 约 68%，且与
低思考 arm 有实质差异；所以 3.6 是校准/救援层，不是 population-wide silent
replacement。

## P3：递归审计、区间与交付

### Recursive audit

写一个只读 `audit_ct52_scoring_run.py`，递归读取每个 state、每个 round JSONL、
每条 attempt，输出：

- 21,453 anchor 是否各有 terminal/scientific state；
- nested `gemini_failed`、schema、transport、download、decode/pixel failure；
- requested/exact model identity 是否漂移；
- primary/rescue/unknown model tier 数量；
- `reference_only` 是否曾进入 evidence；
- achieved z19/z18 与 chip hash join；
- quota ledger 与 state/audit 的 call 数是否一致。

Release gate 要求 operational failure 为 0；不能把 unresolved failure 当作
`absent`。audit 失败时不进入 interval inference。

### Interval inference

对 A24/A48 各跑一次，明确传 CT cutoff，并保留 stdout/log：

```bash
python scripts/temporal/infer_install_dates.py \
  --scan-states-dir "$RUN_ROOT/primary/scan_states/a24" \
  --output "$RUN_ROOT/intervals/install_intervals_a24.csv" \
  --census-mid-date 2025-01-31 --require-terminal

python scripts/temporal/infer_install_dates.py \
  --scan-states-dir "$RUN_ROOT/primary/scan_states/a48" \
  --output "$RUN_ROOT/intervals/install_intervals_a48.csv" \
  --census-mid-date 2025-01-31 --require-terminal
```

把两表合并为 `install_intervals_all.csv` 时加 `lane`，并检查：

- 21,453 行、anchor_id exact bijection；
- 0 post-cutoff interval、0 inverted interval；
- `done_appears` 空日期行显式记为 undated，不可只按 status 计 dated；
- no-history 六行和所有 rescue/review reason 都保留。

最后调用 CT 专用 builder，输出目录必须是全新目录：

```bash
python scripts/temporal/build_ct_install_dated_deliverable.py \
  --anchors-csv "$ANCHORS" \
  --intervals-csv "$RUN_ROOT/intervals/install_intervals_all.csv" \
  --output-dir "$RUN_ROOT/deliverable" \
  --tag 20260731_lite_v2
```

### CT-11 Codex blind QA

冻结 520 条 jump-critical strips：每个 52 grid 至少 5 条，剩余按
`coverage_class × area_bin × achieved_zoom × model_tier × review_required ×
nonmonotonic × interval_width` 风险分层抽样；至少 20% 用新的
`review_run_id` 做第二次 Codex 盲复核。标签只允许 `CONFIRM`、`SHIFT`、
`UNDATABLE`，不得看到模型 verdict 后再改抽样。

报告 Codex-reviewed bracket agreement、Codex repeat agreement、
dated/census-bound/left-censored/undated、每个 area bin 的 Wilson 95% CI，
并把 3.6 rescue 单独列为 diagnostic stratum。报告中不得把这些结果命名为
human accuracy 或 physical install-date accuracy。

## 硬停止条件

出现以下任一项，立即停止当前 wave，保留 run root，不删除或覆盖已有产物：

- roster、catalog、chip hash、geometry/census cutoff 不一致；
- Wayback-only 或 no-history 被误判为 TM empty/absent；
- exact model version 缺失或漂移；
- quota breaker 触发但 worker 仍继续发请求；
- post-census frame 影响 interval/monotonicity；
- nested failure 非零、state 数与 manifest 不一致；
- A24/A48 extent、CRS、offset 或 source_grid 被静默改变；
- 3.6 与 Lite 冲突却有人试图按 confidence 自动覆盖。

## 交付给 Luna 的最短 checklist

- [ ] 读完本文、上位 CT plan、RUN 3 completion/error sections。
- [ ] 先修 P0-A 到 P0-E，并补 focused tests；不要先启动 21,453 全量。
- [ ] 新建 lock/run root，锁 Lite primary、3.6 rescue、exact version policy、
      planner/prompt/quota hashes。
- [ ] 不复用 runtime_lock_v1/v2/v3；primary attempt audit 中 3-flash/3.6 数必须为 0。
- [ ] CT-05 QA + no-history/Wayback-only integration test PASS。
- [ ] 120/60 paired pilot PASS，owner 批准 6-slot planner。
- [ ] 六网格 2,700 canary 的 quota projection PASS。
- [ ] 按 wave、按 A24/A48、按 ledger 执行；每次 resume fresh canary。
- [ ] 递归 audit、interval、CT deliverable、520 Codex blind QA 全部 PASS，且
      owner 完成最终 go/no-go 后才发布。
