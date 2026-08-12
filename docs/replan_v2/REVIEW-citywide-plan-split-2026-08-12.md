# REVIEW — 全城交付计划(DRAFT-v2)审查与三分支拆分 — 2026-08-12

Status: review complete; split plan-of-record 候选,待 owner 签字
被审文档:[`RUN-cape-town-citywide-backdating-delivery-plan-2026-08-12.md`](RUN-cape-town-citywide-backdating-delivery-plan-2026-08-12.md)(DRAFT-v2,工程优先/双轨)

本 review 基于 repo 实际进度(Top-52 tracker、CT-11 corrected QA、仪器诊断、
估计器栈、verdict store、磁盘实测),给出逐节判定,并把 DRAFT-v2 拆分为
**三条可并行分支 + 一份共享复审协议**:

| 分支 | 文档 | 一句话目标 |
|---|---|---|
| **Leg-E 工程/生产** | [`RUN-cape-town-citywide-legE-production-plan-2026-08-12.md`](RUN-cape-town-citywide-legE-production-plan-2026-08-12.md) | 下载→Gemini 推理→交付数据→(抽样核验),产出全城 interval 产品(内部口径) |
| **Leg-S 科研** | [`RUN-backdating-legS-research-program-2026-08-12.md`](RUN-backdating-legS-research-program-2026-08-12.md) | 6 条前沿文献+数学分支,派发给并行 agent,各自预注册 GO/KILL |
| **Leg-R 可复现性** | [`RUN-backdating-legR-reproducibility-plan-2026-08-12.md`](RUN-backdating-legR-reproducibility-plan-2026-08-12.md) | 复现性优先(字节级 replay + 自托管确定性 scorer),准确度尽力不强求 |
| **共享协议** | [`MULTIMODEL_VISUAL_REVIEW_PROTOCOL.md`](MULTIMODEL_VISUAL_REVIEW_PROTOCOL.md) | Gemini/GPT/Grok 跨家族多模型盲审面板,全面替代人工读图 |

---

## 1. 逐节判定(DRAFT-v2 → 本拆分)

| DRAFT-v2 节 | 判定 | 理由(repo 证据) |
|---|---|---|
| §0 双轨原则、三条硬边界 | **保留** | 边界 1/2/3 全部继承到三分支 |
| §0 "Leg-E GO for prefetch" | **保留,但必须补记 override** | CT-11 corrected QA 的政策后果原文是"全城 GEHI 下载不得启动"([`RUN-cape-town-ct11-corrected-qa-2026-08-03.md`](RUN-cape-town-ct11-corrected-qa-2026-08-03.md) §Decision)。DRAFT-v2 把它改成"预取 GO"是一次治理覆盖——合理(下载 outcome-blind、与准确度无耦合),但**必须**作为带日期的 owner 决议写进 `OWNER_DECISIONS.md`,不能静默生效。Leg-E 计划的 E0 把这条列为 P0 第一项 |
| §2 事实盘点 | **确认无误** | 与 tracker/审计一致:Top-52 全链路 done(catalog→download→chip→score→decode→deliverable→audit),21,453 锚点 0 operational failure;CT-11 四硬门 FAIL(exact-bracket Wilson 下界 17.27%、复评 68%) |
| §2.4 磁盘 | **确认且收紧** | 实测 `/home` 250G/已用 153G/**余 94G**;Top-52 run root 79G(chips ≈70G,含 2009+ superseded 层)。全城 chip 上界 110–191 GiB > 余量,E0 磁盘方案为硬前置,且 2009+ superseded chips 是明确的可归档回收项 |
| §3 Leg-E(CT-CW-00/05/05b/06E) | **保留并延伸** | 原文停在 imagery ready;按 owner(单人推进)意图,Leg-E 延伸到 scorer→decode→deliverable→抽样核验,以**内部 release、诚实措辞**交付;对外发布仍留给 Leg-S 验收。工程机制全部复用 Top-52 已验证的脚本栈 |
| §4 CT-CW-01 双人人工校准 | **替换** | owner 约束:单人推进、不保留人工读图;且人工结论无法回灌模型(pattern 不一致)。替换为多模型面板校准(见协议);已冻结的 `ct11_human_calibration_v1_20260804` 盲包(100 锚点,SHA `bb11e06f…`)**原样复用**为面板首个校准输入——盲审合约、模板、评估器(`evaluate_ct11_human_calibration.py`)结构不变,reviewer 从 2 个 human 换成 3 个模型家族 |
| §4 CT-CW-01b 三分叉 | **保留(reviewer 换面板)** | S1/S2/S3 分叉逻辑不变;判据里 "Human" 全部替换为 "面板融合参考" |
| §4 CT-CW-01c 仪器消融 | **保留,升为面板协议的前置校准** | 诊断已证明拼贴缩略图是坏仪器(线性分辨率≈40%);逐帧多图 native 分辨率是默认输入形态,消融 Arm A/B/C 并入协议 §5 |
| §4 CT-CW-02 L0/L1 | **拆细为 Leg-S 六分支** | 原文只有主题白名单;本拆分给每条分支写了文献锚点、数学表述、开发数据、预注册 GO/KILL 骨架,可直接派发 agent |
| §4 CT-CW-03/04 密封确认 | **保留** | 密封 500+100 继续禁开;门槛 re-prereg 时参考仪器 = 多模型面板(不再是 Codex 拼贴) |
| §5 汇合点 | **保留,scorer GO 由 owner 清单化执行** | Leg-E §6 给出 GO 清单;owner 即用户本人,签字进 `OWNER_DECISIONS.md` 即可推进,不再是无限期 HOLD |
| §6 指标命名 | **保留 + 新增** | 新增 `multi-model panel agreement`(取代 `Codex collage agreement` 的评审主尺);命名红线同样禁止称 human accuracy |
| §10 红线 | **全部继承** | 三分支文档各自复述适用红线 |

## 2. review 中发现的、原计划没写的事实

1. **自适应扫描是规则机+均匀采样+二分,不是信息增益**
   (`scan_decision.py`:病例 A–R + `select_evenly_spaced_picks`)。全城
   ~290k HTTP 上界直接由此决定;贝叶斯最优选帧是 Leg-S 里期望收益最大的
   工程回报分支(省配额),原计划未提。
2. **verdict store 已实现内容寻址离线 replay**(key =
   `chip_hash × scorer × prompt_hash × mode × extras`,`replay-diff` CLI)。
   Leg-R 的"严格离线复现"不是从零建,而是把已有机制升级成端到端字节级
   合同。
3. **跨家族分歧才是核心问题**:仪器诊断显示三档 Gemini 彼此 class 一致
   35/40,但两/三多数与 Codex 拼贴只有 14/39 一致。这否定了"同家族多数
   投票当真值"的做法,是多模型协议必须跨家族 + 统计融合(而非简单多数)
   的直接证据。
4. **旧 KILL 数字(recovery 37.5% / ref-anchored 30% / provenance 52.1% /
   DINO 头 56.3%)全部相对旧 Codex 拼贴参考**——参考仪器退役后这些线
   在 Leg-S 里全部有条件重开(原计划 §4 已写,本拆分落实到分支)。
5. **DINOv3 学生线现状**:gate-2 FAIL(A_LSAT=0.6392 vs ceiling 0.7724),
   R4 prereg 已冻结、训练未开。它同时出现在 Leg-S(蒸馏分支)和 Leg-R
   (确定性自托管 scorer,准确度尽力不强求)——两处目标不同,不要合并。

## 3. owner 决策清单(替代 DRAFT-v2 §9)

1. 签署全城 outcome-blind 预取授权 + CT-11 政策覆盖记录(Leg-E E0)。
2. 磁盘方案三选一/组合:归档 2009+ superseded chips、外置盘、扩容(E0)。
3. 批准"多模型面板替代双人人工校准"为正式协议(协议 §1 的降级条款
   本来就预留了此路径,现在升为主路径)。
4. 预注册:各面板成员自稳定性下限、面板融合参考的 method 晋级指标、
   CT-CW-03 新门槛(默认不继承 70/75/80)。
5. 确认 Leg-E 交付口径 = 内部 release(诚实措辞),对外发布仍以 Leg-S
   验收为门。
6. Leg-S 六分支的优先级与配额上限;Leg-R 与 Leg-S 的同步节奏(每次
   method lock 后 Leg-R 吸收一次)。

## 4. 不变的红线(三分支共同)

- 预取/打分完成率不得冒充准确度;
- 密封 `ct11_disjoint_holdout_v1_20260804`(500+100)在 method lock 前禁开;
- 不得发布 physical install date / citywide accuracy 等超证措辞;
- midpoint 不作主统计通道(D19 fractional posterior-mass 是生产口径);
- 磁盘余量 <20% 时停新增下载;
- 任何评审指标不得命名为 human accuracy / gold truth。
