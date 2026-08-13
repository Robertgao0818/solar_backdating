# DATA — R4 v1 质量头监督诊断（H1 证据锁）

日期：2026-08-13 · Status: **诊断定案 — 不算修正**
范围：R0 **train + calibration only**（未开 test / repeat-ceiling / R5）
服务于：[OWNER_DECISIONS D3](../replan_v2/OWNER_DECISIONS.md) 已选定的
H1，以及
[RUN-r4-v2-h1-quality-supervision-prereg-2026-08-13](RUN-r4-v2-h1-quality-supervision-prereg-2026-08-13.md)

诊断不算校准 gate 的修正名额。本 memo 只锁机制与数字。

## 1. 今天质量头在学什么

训练路径把 `q_target = 1` 当且仅当 `effective_label ∈ {present, absent}`。
`effective_label` 是 **A1 empty-K sidecar 覆盖后的 `label_v1`**，不是
teacher `quality_flag`，也不是 D12.iii 的质量行。

| 步骤 | 函数 | 文件 |
|---|---|---|
| R0 `label_v1` | `classify_label`：仅 `unusable` / `gemini_failed` → uninformative；`ambiguous` 且有 verdict 仍为 present/absent | `scripts/temporal/build_run3_native_manifest.py` |
| A1 覆盖 | `apply_shortgap_sidecar`：2,146 锚的两个边界帧 → `uninformative` | `scripts/temporal/run_r4_training.py` |
| 质量 BCE | `anchor_loss`：`q_target = label != "uninformative"` | 同上 |
| 校准评估 | `calibration_slice_report(target="quality")`：同一目标 | 同上 |

TLO 桥（prereg §3.1）在 v1 惰性：`tlo_source=null`，训练路径不调用
`effective_label(..., tlo)`。

## 2. 为什么 cal_select 负例只有 410/13,797（3.0%）

train+calibration = 270,866 帧 / 35,990 锚。A1 sidecar SHA
`fefac6fe234e2a9e2c21ea77aa0bd7cc8b788ff19e89f89310cf0171da23a82a`。

### 2.1 A1 之前（原生 R0）

| `label_v1` | 帧 | % |
|---|---:|---:|
| absent | 198,865 | 73.4 |
| present | 69,723 | 25.7 |
| uninformative | 2,278 | **0.84** |

| `quality_flag` | 帧 | % |
|---|---:|---:|
| usable | 260,600 | 96.2 |
| ambiguous | 8,033 | 3.0 |
| unusable | 2,233 | 0.8 |

交叉表：

| | ambiguous | unusable | usable |
|---|---:|---:|---:|
| uninformative | 45 | 2,233 | 0 |
| present/absent | 7,988 | 0 | 260,600 |

`quality_flag=ambiguous` 且带 P/A verdict 的 **7,988** 帧在 v1 是质量头正例。
这与 D12（ambiguous → unusable 类）相反。

按 R4 角色，A1 前 uninformative：train 2,088/225,854（0.92%）；
cal_select **64/13,797（0.46%）**。

### 2.2 A1 之后（v1 实际监督）

| 角色 | uninformative | % |
|---|---:|---:|
| train | 5,274 | 2.34 |
| cal_es | 519 | 2.86 |
| cal_fit | 367 | 2.81 |
| **cal_select** | **410** | **2.97** |

cal_select 410 负例分解：

| 来源 | 帧 | `quality_flag` | 语义 |
|---|---:|---|---|
| R0 原生 uninformative | 64 | 62 unusable + 2 ambiguous | 真质量/打分失败 |
| A1 边界补丁 | 346 | **341 usable** + 5 ambiguous | **不是**质量失败 — 只是 gap=45 下 `K_i` 空 |

**346/410 = 84% 的质量头负例是区间可表示性补丁。** 头被要求把高置信
usable 边界帧学成“无信息影像”。这就是稀释。

## 3. 与 D12.iii 的差

ISSUE-04 / `build_distillation_set.select_student_rows`：

- present/absent 只来自 `usable` + 高置信；
- 质量驱动的 `unusable`/`ambiguous` 才进负类；
- **`done_ambiguous_*` 整串不得进 unusable 类监督**（标记错位，不是影像质量）。

run3-native `label_v1` 是 **verdict 是否可用**，不是 **影像是否可读**。
A1 又把 4,292 条可用边界帧灌进负类。质量头 AUROC 0.57–0.59、校准 Brier
几乎等于常数先验，与此同构。

## 4. H1 重构后的锁定计数（train+cal，A1 仍作用于区间资格）

规则（正式写进 v2 prereg）：

```text
exclude from quality BCE:
  A1 边界覆盖  OR  scan_status ∈ {done_ambiguous_nonmonotonic,
                                  done_ambiguous_no_recent_anchor}
q_target = 1  iff  quality_flag==usable AND label_v1_r0 ∈ {present,absent}
q_target = 0  otherwise (among eligible)
state CE / decoder / A1 区间资格  — 不变
```

| 池 | 帧 |
|---|---:|
| 合格监督 | 245,862 |
| 排除 A1 | 4,292 |
| 排除 ambiguous-status（D12.iii） | 20,712 |
| q=1 / q=0 | 236,315 / **9,547（3.88%）** |
| q=0 构成 | ambiguous 7,672 + unusable 1,875 |

按角色：

| 角色 | 合格 | q=0 | q=1 | q=0 率 |
|---|---:|---:|---:|---:|
| train | 205,604 | 8,375 | 197,229 | 4.07% |
| cal_es | 16,078 | 482 | 15,596 | 3.00% |
| cal_fit | 11,693 | 303 | 11,390 | 2.59% |
| cal_select | 12,487 | 387 | 12,100 | 3.10% |

cal_select 相对 v1：负例数接近（387 vs 410），但 **341 条 usable A1 补丁
不再当质量负例**；负例改为 teacher 质量旗。train 质量偏置
`log(197229/8375) ≈ 3.159`（v1 在 A1 后约为 3.73）。

类权重按新 q 计数重算（inverse-sqrt、cap 10、均值为 1）——这是 D3 允许的
H3 子件，不是独立假设。

## 5. 不做什么

- 不改 R0 split / 种子 / R3 缓存 / 298,243 参数头 / 合格线 0.7542/0.7337/0.80
- 不加 TLO sidecar（v1 无全库 TLO；另开才算新输入）
- 不改 state CE 监督（状态头 AUROC 0.93，动它会混进第二个假设）
- 不读 test
