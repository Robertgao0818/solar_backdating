# DATA — Leg-R R1 day-1：字节级离线 replay 合同

日期：2026-08-13
Status: **decode+deliverable 同机双跑 GREEN**；`make repro-check` 绿；
`release_manifest.json` 仍因绝对路径不一致。未开密封 holdout，未调 GEHI/Gemini，
未提交。

计划：[RUN-backdating-legR-reproducibility-plan-2026-08-12](RUN-backdating-legR-reproducibility-plan-2026-08-12.md) R1
· 合同：[REPRO_CONTRACT.md](REPRO_CONTRACT.md)
· 产物根：`~/zasolar_data/geid_temporal/cape_town_top52_backdating_v1_20260724/production_lite_v2_20260731/repro_r1_20260813/`

## 1. 做了什么

A. `scripts/temporal/merge_ct_scan_states.py` + 合成 pytest。从最终评分审计重建
   5 个 `--scan-states-dir`（primary a24/a48 + gate_retry2 retry2/original_A24/A48）。
B. `infer_install_dates.py` 的 `scan_state_path` 默认写成 `<anchor_id>.json`；
   绝对路径进 sidecar。Interval 数学未改。
C. 合并 21,453 状态后离线双跑 infer + deliverable。
D. 本 memo + `REPRO_CONTRACT.md`。
E. `make repro-check`（6 条 committed fixture scan states）。
F. GPKG `last_change` 冻成 `2025-01-31T00:00:00.000Z`（否则双跑差时间戳）。

## 2. 合并实测

| 项 | 值 |
|---|---|
| 输入文件 | 21,460 |
| unique anchors | 21,453（= 冻结 roster） |
| identical-byte dups | 6（no-history 同时在 a24 与 a48） |
| timestamp-only dups | 1（`t00038228` vs canary original；仅 started_at/updated_at） |
| canonical supersede | 0（`--allow-supersede` 备着，这次没用上） |

## 3. 双跑结果

| 产物 | run_a ≡ run_b | sha256（前缀） |
|---|---|---|
| intervals CSV | **是** | `a7ccaf30011b…` |
| sidecar | 是（同 merge dest） | `8c711b75ab5b…` |
| deliverable CSV | **是** | `f74ce1ddb5d5…` |
| deliverable GPKG | **是**（冻 last_change 之后） | `56ed4d55c1bc…` |
| `gates.json` | **是**，且 = 冻结生产 `48694f42…` | `48694f421850…` |
| `release_manifest.json` | **否** | a `e14060f6…` / b `cfd90573…` |

相对冻结 `install_intervals_all_20260803.csv`（`81d80920…`）：
**值列 21,453/21,453 一致**，仅 `scan_state_path` 从
`/tmp/ct52_interval_states.izNPQ6/<id>.json` 变成 `<id>.json`。
Deliverable CSV 对冻结生产同样只差这一列。

Infer 状态（dip repair 后，与冻结交付一致）：
`done_appears` 13,908 · census 5,157 · already-present 2,238 ·
nonmonotonic 142 · no_recent 8。Dip repair：17,044 flips / 4,971 anchors。

## 4. 未做 / 失败项

- **未** 21k adaptive-scan 重放（day-1 红线：decode 绿则不启动）。
- **未** 异机 / 新 venv 复验（R1 退出门仍缺）。
- `release_manifest.json` 仍写入绝对路径和 `--tag` 文件名 → 双跑 hash 不同。
  不挡 CSV/GPKG/gates 合同。
- 止损（3 agent-day）**未触发**。

## 5. 下一步

1. 可选：canonicalize `release_manifest.json` 为 role→sha 键（去掉绝对路径）。
2. R1 退出门剩余：异机或新 venv 跑一遍 §2 命令，对上 §3 hash。
3. **不要** 在未写独立授权前开 21k scan replay 或密封 holdout。
4. R2（自托管 scorer 线）等 Leg-S / R4 v2，不在本 day-1。

CI 回归：

```bash
source scripts/activate_env.sh && make repro-check
```
