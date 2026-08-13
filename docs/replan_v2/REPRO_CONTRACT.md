# REPRO_CONTRACT — Leg-R R1 byte-level offline replay

日期：2026-08-13 · Status: **READY for decode+deliverable**（同机双跑数据产物字节一致；
`release_manifest.json` 因绝对路径 + `--tag` 文件名仍不一致，见 §5）
计划：[RUN-backdating-legR-reproducibility-plan-2026-08-12](RUN-backdating-legR-reproducibility-plan-2026-08-12.md) R1
· 波次：[RUN-paper-program-sequencing-2026-08-13](RUN-paper-program-sequencing-2026-08-13.md) §1-C

本文件钉死 **frozen-verdict 线** 的离线解码合同：冻结 scan states + 确定性
`infer_install_dates.py` + `build_ct_install_dated_deliverable.py`。
不覆盖 hosted Gemini 生成、GEHI 下载、或 21k adaptive-scan 重放。

## 0. 环境

| 项 | 值 |
|---|---|
| Host | Linux 7.1.4-204.fc44.x86_64 |
| Python | 3.14.6（ZAsolar 共享 `.venv`） |
| `pip freeze` sha256 | `dddef243cda6fc78e08fa92b31298815e28e338540ad43aa561244be2c5dfce7` |
| 激活 | `source scripts/activate_env.sh` |
| 离线 | `env -u GEMINI_API_KEY -u GOOGLE_GEMINI_BASE_URL`；禁止 live GEHI / Gemini |
| CI 门 | `make repro-check`（6 条合成 scan state，不进 git 的 21k 状态） |

## 1. 输入清单（hash）

根：`~/zasolar_data/geid_temporal/cape_town_top52_backdating_v1_20260724/`
生产：`.../production_lite_v2_20260731/`

| 输入 | sha256 |
|---|---|
| `anchors_v1/anchors_all.csv`（21,453） | `3050f9b8dc48d76c0f63049fcaafa9589189b240d47f3c55ca2ec3dfc644b768` |
| 冻结 intervals `intervals/install_intervals_all_20260803.csv` | `81d80920c79895c92fe1207ed9e2098eff57303741f8696984032ed56e14c8c6` |
| CT inventory GPKG | `1f046ecad16e2d029a4a9c384e5c5ca2454e38894ef627fbbc18b11718a95aec` |
| 冻结 `deliverable_20260803_lite_top52/gates.json` | `48694f4218507ec9d64407f3bb32e0dffb6ca8966dfd42bcd1642b25cfb134e3` |
| 最终评分审计（21,460 files / 5 scopes） | `audits/ct_full_reconciliation_final_scoring_user60_20260803.json` |

### 1.1 冻结 5-scope scan-state 源（审计重建）

`scripts/temporal/merge_ct_scan_states.py --production-root` 展开顺序
（**先列出的赢**）：

1. `primary/scan_states/a24`
2. `primary/scan_states/a48`
3. `canary/gate_retry2/scan_states/retry2`
4. `canary/gate_retry2/scan_states/original_A24`
5. `canary/gate_retry2/scan_states/original_A48`

实测：21,460 输入文件 → 21,453 unique。6 条 no-history 在 a24/a48 字节相同；
`t00038228` 相对 canary original 仅 `started_at`/`updated_at` 不同（replay-diff
忽略字段）。`n_superseded=0`（retry2 的两锚点不在 original_* 里）。

## 2. 命令序列

产物根：`$PROD/repro_r1_20260813/`（不复制 64G chips）。

```bash
source scripts/activate_env.sh
CT_ROOT=~/zasolar_data/geid_temporal/cape_town_top52_backdating_v1_20260724
PROD=$CT_ROOT/production_lite_v2_20260731
REPRO=$PROD/repro_r1_20260813

env -u GEMINI_API_KEY -u GOOGLE_GEMINI_BASE_URL \
python scripts/temporal/merge_ct_scan_states.py \
  --production-root "$PROD" \
  --output "$REPRO/merged_scan_states" \
  --manifest "$REPRO/merge_manifest.json" \
  --allow-supersede \
  --expected-count 21453

env -u GEMINI_API_KEY -u GOOGLE_GEMINI_BASE_URL \
python scripts/temporal/infer_install_dates.py \
  --scan-states-dir "$REPRO/merged_scan_states" \
  --output "$REPRO/run_a/intervals/install_intervals_all.csv" \
  --census-mid-date 2025-01-31 \
  --require-terminal

python scripts/temporal/build_ct_install_dated_deliverable.py \
  --anchors-csv "$CT_ROOT/anchors_v1/anchors_all.csv" \
  --intervals-csv "$REPRO/run_a/intervals/install_intervals_all.csv" \
  --output-dir "$REPRO/run_a/deliverable" \
  --tag 20260813_repro_a \
  --expected-count 21453
```

对 `run_b` 重复同样命令（仅 output-dir / `--tag 20260813_repro_b`）。
`infer` 默认 `--scan-state-path-mode basename`，主 CSV 不含绝对路径。

CI 合同（不碰 21k）：

```bash
source scripts/activate_env.sh && make repro-check
```

期望：fixture intervals sha256
`48ca6e2e531b01ee2459b2c2b64420f2668c074705eb8af71aa38798616c4bfc`

## 3. 预期输出 hash（2026-08-13 同机双跑）

| 产物 | sha256 | a≡b |
|---|---|---|
| `install_intervals_all.csv` | `a7ccaf30011bc60ff83f1925a523fea47ebe48f1ebba21ae2f4dc47b0027bb0f` | 是 |
| scan_state_path sidecar | `8c711b75ab5bcf959d3f3d53a5e2b10e92d59d61015e027e18ce81738b2cf8cf` | 是 |
| deliverable CSV | `f74ce1ddb5d5f7dfd3dba8d308925f7e1ad6c01af880d401c76750f76c897912` | 是 |
| deliverable GPKG | `56ed4d55c1bc50fa4f4b3c6cd4f36f3198719731d596a9b7d2b53da284e25c30` | 是 |
| `gates.json` | `48694f4218507ec9d64407f3bb32e0dffb6ca8966dfd42bcd1642b25cfb134e3` | 是（且 = 冻结生产 gates） |
| `release_manifest.json` | a `e14060f6…` / b `cfd90573…` | **否**（绝对路径 + tag 文件名） |
| `merge_manifest.json` | `a7984cbbfcd5c334d5489d0ea2dca0cee2d2fd0c10880e9c8fe34c980bc58cbd` | n/a |
| `merge_selection.csv` | `f5c0f4fe5328e4bb425e204aab57107a6c9a3e66bbdce36c5b733d3f9de0c343` | n/a |

相对冻结 `install_intervals_all_20260803.csv`：除 `scan_state_path` 列外
**21,453/21,453 值一致**（旧列是 `/tmp/ct52_interval_states.izNPQ6/...`）。
Deliverable CSV 同样仅 `scan_state_path` 不同。

账本：`$PROD/repro_r1_20260813/dual_run_hashes.json`
（sha256 `7e130f54643b8d13ab3341c000bd5c04de78786f7b5e258b888adeac5a76b229`）

## 4. 代码合同

- `scripts/temporal/merge_ct_scan_states.py`：5-scope 合并；canonical 字节冲突默认失败；
  `--allow-supersede` 按优先级保留并记入 manifest。
- `infer_install_dates.py`：`scan_state_path` 默认 basename；绝对路径进 sidecar。
  **不改 interval 数学。**
- `build_ct_install_dated_deliverable.py`：写完 GPKG 后把 `gpkg_contents.last_change`
  冻成 `2025-01-31T00:00:00.000Z`（否则双跑差 5 个时间戳字节）。

## 5. 剩余非确定性（不挡 decode 合同）

| 源 | 状态 |
|---|---|
| `release_manifest.json` 绝对路径 / `--tag` 文件名 | 未修；比较 `content` hash 表而不是该文件 |
| sidecar 绝对路径 | 同机同 merge dest 下双跑一致；换目录会变 |
| 异机 / 新 venv | **未跑**（R1 退出门仍缺这一条） |
| 21k adaptive-scan + `verdict_store replay-diff` | 显式不在 day-1（decode 已绿） |
| hosted Gemini 生成 / GEHI 下载 | 威胁模型承认不可复现；本线冻结 verdict/chips |

止损（3 agent-day 修不完非确定性 → 升 issue）：**未触发**。数据产物同机双跑已绿。
