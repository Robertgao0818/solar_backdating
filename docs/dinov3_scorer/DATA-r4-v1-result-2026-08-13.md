# DATA — R4 v1 训练与校准结果：CALIBRATION_FAILED

日期：2026-08-13（补记；运行发生于 2026-08-03 21:05 → 08-04 00:47）
Status: **结果定案 — verdict `CALIBRATION_FAILED`；prereg 许可的
单次修正已由 owner 选定 H1（见 [OWNER_DECISIONS](../replan_v2/OWNER_DECISIONS.md) D3）**

> 文书债说明：本次运行完成后未写结果 memo，两个 tracker 的
> "training not started" 因此 stale 了 9 天。本 memo 补记，
> tracker 同日修正。

## 1. 运行身份

- Prereg：[RUN-r4-training-calibration-prereg-2026-07-20](RUN-r4-training-calibration-prereg-2026-07-20.md)
  （含 2026-08-03 A1 empty-`K_i` 保守 sidecar 修正，sidecar SHA `fefac6fe…`）
- Runner：`scripts/temporal/run_r4_training.py`，repo commit `ff249ee`
  （"temporal: prepare owner-approved R4 v1 training"）
- 产物根：`~/zasolar_data/geid_temporal/run3_native_line_2026-07/r4_v1/`
  （RUN_LOCK `status: R4_COMPLETE`；locks/seeds/metrics/predictions 齐备，
  `artifacts.sha256` 封存）
- 算力实测：本地 RTX 4070 Laptop 8GB，三 seed 全程 ≈3.7 小时
  （目录时间戳 08-03 21:05 config → 08-04 00:47 locks 封存）

## 2. Verdict 与检查项

`R4_HEALTH.json`：**verdict = CALIBRATION_FAILED**。

| 检查 | 结果 |
|---|---|
| all_three_seeds_present / values_finite / training_improvement≥1% | PASS |
| leakage_counts_zero / test_access_guard / clean_repository_lock | PASS |
| decoder_equivalence（200 锚点） | PASS |
| at_least_two_calibration_healthy | **FAIL**（0/3 seed 校准健康） |
| median_seed_threshold_valid | **FAIL**（`selected_threshold=None`） |

## 3. 数字

### 3.1 逐 seed（cal_select 面）

| seed | map-in-K | selected_threshold | 质量头 AUROC | 状态头 AUROC |
|---|---|---|---|---|
| 2026072001 | 0.6637 | None | 0.568 | 0.932 |
| 2026072002 | 0.6599 | None | 0.591 | 0.932 |
| 2026072003（median-ranked） | 0.6631 | None | 0.579 | 0.929 |

### 3.2 阈值扫描（median seed 2026072003；合格线 = Wilson overall
≥0.7542 且 under40 ≥0.7337 且 coverage ≥0.80，prereg 冻结）

| 扫描点 | coverage | Wilson overall | Wilson under40 | 判定 |
|---|---|---|---|---|
| t=0.50 | 0.9305 | 0.6517 | 0.6222 | 不合格 |
| t=0.57（coverage≥0.80 内最优） | 0.8051 | **0.6725** | 0.6399 | 不合格（差 8.2pp） |
| t=0.99（全扫最优） | 0.0248 | 0.8714 | 0.8714 | 不合格（coverage 崩） |

读法：`max_posterior` 的排序信号是真实的（收紧到 2.5% 覆盖时
fidelity 升到 0.87），但在 prereg 要求的 0.80 覆盖下 fidelity 平台
钉死在 ~0.66–0.67。0.50–0.99 全网格无一点合格 → 三 seed 全部
`selected_threshold=None` → `calibration_failed=True`（触发项即
threshold-None；NLL/ECE/Brier 各校准器质量检查本身均通过）。

### 3.3 质量头病灶（校准失败的最可疑机制）

- 负例（unusable）在校准面仅 **410/13,797 = 3.0%**；under40 切片
  330/12,007，40plus 切片 80/1,790；
- AUROC 0.568/0.591/0.579 —— 接近随机；calibrated Brier 仅比
  constant-train-prevalence 基线好千分位（0.02858 vs 0.02887）,
  即质量头**几乎没有学到超出先验的东西**；
- 对照：状态头（present/absent）AUROC 0.93、ECE 0.011、Brier 大幅
  优于常数基线 —— **蒸馏在 frame-state 上成立，在质量判读上失败**。
- 与三态软解码的耦合：Phase-0 解码消费 (q, e0, e1)，质量头无信息
  时 q 以噪声形式进入后验 → 拉低 map-in-K 与 `max_posterior` 的
  可分性。这是 H1（修质量头监督信号）的机制假设，待 r4_v2 检验。

## 4. 定位与历史对照

fidelity 平台 0.660–0.664 跨三 seed 稳定，与旧 gate-2 的
A_LSAT=0.6392 / A_floor=0.6542 同档 —— run3-native 的输入修正
（marker-free crop、short-gap 标签修复 5,248 帧、A1 sidecar）
**没有移动平台**。结构性缺口，非种子噪声、非数据管道事故。

论文视角（[BRIEF §7](../briefs/BRIEF-student-vs-registration-2026-08-13.md)）：
"蒸馏在 frame-state 强（0.93）、在质量判读近随机（0.58）"本身是
可发表的 negative-result 素材，r4_v2 无论 GO/KILL 都不白跑。

## 5. Prereg 纪律状态

- 校准 gate 的**单次假设驱动修正尚未使用**；owner 2026-08-13 选定
  **H1 = 质量头监督信号修复**（唯一修正,判定 bar 不动），见
  [OWNER_DECISIONS D3](../replan_v2/OWNER_DECISIONS.md)。
- 再败分支已预决策（同文档 D2）：授权一轮 LoRA 级二次投入
  （新 prereg 前置；骨干更换仍 veto）。
- 密封 test split 未触碰（`test_access_guard` PASS，runner 构造上
  不可读 test）。
