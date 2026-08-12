# RUN — Leg-R 可复现性分支(复现性优先,准确度尽力)

日期:2026-08-12 · Status: **READY(与 Leg-S 同步并行)**
拆分索引:[`REVIEW-citywide-plan-split-2026-08-12.md`](REVIEW-citywide-plan-split-2026-08-12.md)
复审协议:[`MULTIMODEL_VISUAL_REVIEW_PROTOCOL.md`](MULTIMODEL_VISUAL_REVIEW_PROTOCOL.md)

**排序原则(本分支唯一特殊处)**:可复现性是**硬门**,识别区间准确度是
**尽力目标**——准确度只对照面板参考跟踪、报数,不作为放行条件;任何
"更准但引入不可复现因素"的改动一律拒收。

## 0. 威胁模型:哪里不可复现

| 环节 | 可复现? | 处置 |
|---|---|---|
| GEHI 下载 | 否(服务端 vintage 可变) | 冻结 chip 字节 + sha256,下载视为一次性采集 |
| Hosted Gemini **生成** | **否**(模型漂移、采样) | 承认不可复现;verdict 一经产生即冻结为数据 |
| Hosted Gemini **重放** | 是 | verdict store 内容寻址命中(key=`chip_hash×scorer×prompt_hash×mode×extras`),已实现 |
| 自托管学生 scorer | 是(可做到) | 固定权重 hash + 确定性推理(R2) |
| changepoint/PAVA/Turnbull 解码 | 是(确定性 EM/枚举) | 纳入字节级合同(R1) |
| deliverable 构建 | 是 | 已有结构门,补 hash 合同 |

两条产品线,命名不得混用(继承 DRAFT-v2 §1.4):

- **repro-grade / frozen-verdict 线**:冻结 chips + 冻结 Gemini verdicts →
  确定性解码 → 交付;字节级可复现,准确度=生产同源。
- **repro-grade / self-hosted 线**:冻结 chips + 确定性学生 scorer →
  确定性解码 → 交付;端到端无 hosted 依赖,准确度尽力
  (现成资产 DINOv3 头对旧 Codex 参考 56.3%,面板参考上待重评——
  低就低,如实报)。

## R1 — 字节级离线 replay 合同(第一优先,零新配额)

**工作**(全部在 Top-52 冻结产物上,后扩全城):

1. 端到端两次独立重建:frozen chips + verdict store →
   `infer_install_dates.py` → `build_ct_install_dated_deliverable.py`,
   断网(`no_live_gehi=True` + 无 API key 环境)执行;
2. 逐文件 sha256 比对两次输出;`verdict_store replay-diff` 全量为空;
3. 排查并修死非确定性源:dict/glob 排序、浮点归约顺序、线程竞争、
   时间戳/主机名入文件——时间戳移到 sidecar,产物本体 canonical 化;
4. 固化为 `make repro-check`(或单脚本)+ 小型 fixture 的 pytest 回归,
   进 smoke gate;
5. 产出 `REPRO_CONTRACT.md`:输入清单(hash)、环境、命令序列、
   预期输出 hash 清单。

**退出门**:同机双跑字节一致;异机(或新 venv)一跑一致;CI fixture 绿。

## R2 — 确定性自托管 scorer 线

1. 以现成 **冻结 DINOv3-L-SAT 线性头** 为 v0(权重 sha256 入锁;
   `torch.use_deterministic_algorithms(True)`、固定 seed/CUDA 后端,
   或直接 CPU 推理换确定性);Leg-S S5 的 R4 学生训完后作 v1 替换,
   同一合同重验;
2. 全链跑通:frozen chips → 学生逐帧三态 → 同一 changepoint 解码 →
   `repro_grade_intervals.csv`(Top-52 全量);双跑字节一致;
3. 准确度**跟踪**(非门):对多模型面板参考报 year-bin / containment
   分层表,与 frozen-verdict 线并列;明确标注 `scorer=selfhosted_v0`,
   不进任何 release headline。

**退出门**:双跑一致 + 跟踪报表存在。准确度无阈值。

## R3 — 环境与身份钉死

- `repro_lock.json`:python/venv 包冻结(pip freeze hash)、模型权重
  hash、GEHI 二进制版本、chip 集 sha256 清单、解码 config hash、
  prompt hash(对 frozen-verdict 线);
- runtime lock v2 已覆盖大半,缺的是 **环境层**(venv/权重/二进制)——
  只补缺,不重造;
- 所有 repro 产物 mode 0444 + 顶层 sha256,与现行冻结纪律一致。

## R4 — 与 Leg-S 的同步节奏(准确度"尽力"的实现方式)

- Leg-S 每完成一次 method lock(如 S1 emission 升级、S4 配准预处理、
  S6 conformal 校准——这些全是确定性计算),Leg-R 吸收一次:
  新 config hash → 重跑 R1 合同 → 版本号 +1(`repro_v1, v2, …`),
  旧版本产物不删;
- **拒收清单**:任何需要在线 API 的推理路径、任何无 seed 的采样、
  任何"每次跑分不同"的组件(哪怕更准);
- 面板参考更新时只更新跟踪报表,不回改历史版本数字。

## 交付物

| 产物 | 说明 |
|---|---|
| `REPRO_CONTRACT.md` + `repro_lock.json` | 复现合同与环境锁 |
| `make repro-check` / pytest fixture | 一键验证 |
| `repro_grade_intervals_<ver>.csv` ×2 线 | frozen-verdict 线 + self-hosted 线 |
| 跟踪报表 | vs 面板参考的 year-bin/containment 分层表(报数不卡关) |

## 红线

- 不得为提升准确度引入不可复现组件;
- 不得把 repro-grade 数字写进对外 accuracy headline(那是 Leg-S 验收的事);
- 不写共享数据区;所有输出进本分支 run root;
- 密封 holdout 同样禁开。
