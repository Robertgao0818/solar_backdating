# RUN — A3 整格 availability probe + 冷格子墙钟 + manifest 合成

| 字段 | 值 |
|---|---|
| 文档 ID | `RUN-a3-fullcell-coldcell-probe-2026-08-19` |
| 日期 | 2026-08-19（05:43Z 出数） |
| Status | **三项遗留问题全部关闭**（整格 probe 阳性 / 冷格子 dump 臂 GO / manifest 合成过生产 QA）。`ENABLE_REGION_BATCH` **仍 false**，等 owner 签生产路径 |
| 执行依据 | [`HANDOFF-a3-region-batch-2026-08-19.md`](HANDOFF-a3-region-batch-2026-08-19.md) §7 步骤 3 + §8 未决清单；owner 2026-08-19 决议（[OWNER_DECISIONS D4](OWNER_DECISIONS.md)）后「继续推进」 |
| 前序 | [`RUN-a3-region-batch-dump-arm-2026-08-18.md`](RUN-a3-region-batch-dump-arm-2026-08-18.md)（dump 臂 GO，两格） |
| 真源 | `~/zasolar_data/geid_temporal/a3_fullcell_probe_20260819/{export/probe_summary.json, availability/summary.json, coldcell_pilot/report/dump_summary.json, coldcell_pilot/report/manifest_summary.json}` |

**一句话**：owner 接受 ~2 DN dump chip 为打分真源（D4）后，本轮把 handoff 剩下的三个经验问题一次清掉——① **整格并窗可行**：341 锚 / 1.1 km 联合框在 z19 `availability --complete` 下有 **57 个完整日期**；② **冷格子墙钟**：非 Top-52 冷格子上 dump 臂总耗时 **1.46 s vs 逐张 25.13 s（17.2×）**，与 cache-hot 的 17.8× 几乎相同，证实「税在进程+间隔，不在字节」；③ **manifest 合成**：dump 切开结果折回 `gehi_download.FIELDS` per-anchor 行，**原样过生产 `quality_gate`**（pilot 24/24、冷格子 12/12 release-eligible）。

---

## §1 本轮做了什么

| 步骤 | 状态 | 落点 |
|---|---|---|
| owner 决议记录（D4：~2 DN dump chip 接受为打分真源） | 完成 | [`OWNER_DECISIONS.md`](OWNER_DECISIONS.md) D4；dump 臂 RUN §6 对应项勾销 |
| dump → per-anchor manifest 合成 + 生产 QA 自检 | 完成 | `gehi_region_batch.synthesize_dump_manifest`（FIELDS 逐字对齐 + 漂移即 raise）；pilot driver 新 stage `manifest_dump`；offline 单测 + 两份真实输出自检 |
| 整格（~400 锚 / ~1.1 km）availability probe（只 probe 不下图） | 完成 | 新 driver `run_a3_fullcell_probe.py`（plan/availability/export，仅 availability 触网） |
| 非 Top-52 冷格子墙钟 + 第三格保真复现 | 完成 | probe export → **未改动**的 pilot driver 跑冷格子双臂 |

live footprint：probe 3 次 availability（整格 1 + 冷候选 1，早停未用满 3 候选）+ pilot driver 13 次（availability 1 + dump 1 + sequential 12）= **16 次调用**，`REQUEST_INTERVAL=2.0`，`--exact-date` 全程开着，单进程串行，**无 403**。Top-52 生产 chips 未动；产物全在 `a3_fullcell_probe_20260819/` 下。

## §2 整格 probe：~1.1 km 并窗仍 complete

选格（确定性规则，792 个合格冷格 = 非 Top-52 且 ≥12 锚）：CPT2598，**341 锚**，联合框 **1116×1120 m**，pad 后整瓦片覆盖估计 **270 块 z19**。

| 指标 | 值 | 含义 |
|---|---:|---|
| `availability --complete` 完整日期（z19, TM, 2019–2025） | **57 个**（2019-01-30 … 2025-02-28） | 整格并窗不缺常见 vintage |
| probe 墙钟 | 1.06 s / 1 次调用 | availability 是元数据 op，不随窗面积涨价 |
| 对照：12 锚小簇（08-18 pilot） | 2/2 格含目标日 | 小簇可行早已成立 |

**结论**：「一格全量并窗是否仍 complete」——**是**。生产若按整格一簇 dump，1 次调用拿 ~270 瓦 vs 341 次逐张调用；该估算是 per-cell 形状说明，**不是**全城日历外推（见 §5 纪律）。

## §3 冷格子墙钟 + 第三格保真

选格：CPT1953（85 锚的中位密度格，非 Top-52，瓦片不在 24G 共享缓存里），centroid 12 锚簇，联合框 425×216 m（pad 后 32 瓦）。日期选取规则 = 该簇完整日期中最新：**2025-10-30**（35 个完整日期）。双臂走未改动的 pilot driver。

### 保真（与预注册同一套 7 条 bar）

| 指标 | 冷格子 CPT1953 | 08-18 热格（2 格合并） | bar | 过？ |
|---|---:|---:|---|---|
| median MAE vs 同次逐张 | **2.78 DN** | 1.93 DN | ≤5 | 是 |
| MAE≤5 比例 | **12/12** | 24/24 | ≥90% | 是 |
| MAE 最大 | 3.44 | 2.89 | — | — |
| p99 最大 | 13 DN | 9 DN | — | — |
| bbox / pixel QA | 12/12 · 12/12 | 24/24 · 24/24 | ≥95% | 是 |
| 判定 | **GO** | GO | 7 条全过 | 是 |

第三格、第三个 vintage（2025-10-30）、冷缓存，dump 臂仍全过——保真结论不再是「两格一个日期的巧合」。

### 冷墙钟（本轮的核心新数据）

| 臂 | 调用 | 墙钟 | 单次拆解 |
|---|---:|---:|---|
| dump 臂 | **1** | **1.458 s** | dump 1.375 s（32 块**冷**瓦，`--parallel 4`）+ stitch 0.021 + crop 0.062 |
| 逐张臂 | 12 | 25.125 s | 每张 1.0–2.82 s，中位 ~2.2 s |
| 比值 | 12× | **17.2×** | 热格 17.8× |

**逐张臂的冷网税实测只有 ~0.1–0.7 s/张**（2.0 s 间隔之外的部分），与热格逐张均值 2.14 s 几乎一样。这坐实了 handoff P1 的机理判断：**瓶颈是调用次数（进程 + 2 s 间隔税），不是字节**。08-18「cache-hot 不能外推」的保留意见现在有界：冷、热样本的比值一致（17.2× vs 17.8×），因为税根本不在网络上。注意边界：这是 1 格 × 1 日期 × 本机网络的刻画（characterization），不写成全城日历承诺。

## §4 manifest 合成：dump 切进生产 QA 合同

`quality_gate` 消费的是 `gehi_download.FIELDS` 的 per-anchor manifest 行。合成规则（`gehi_region_batch.synthesize_dump_manifest`）：

- 每张切开 chip → 一行，**字段与 `gehi_download` 逐字一致**（函数内置漂移检查：key 集合/顺序不符即 raise）；`status=ok`、`actual_zoom=19`、`exact_date=1`、sha256 实算、raster provenance 走同一个 `build_chip_provenance`（ISSUE-18）。
- 切开失败 / 无记录 → `crop_failed: ...` 行，QA 归类为 download failure——**是失败类，永远不会变成 absence 观测**（与 ct05 合同语义一致）。
- 批量出处（哪次 dump / 哪张 mosaic）不进 FIELDS，进**附加 sidecar**（region_id、tile_dir、n_tiles、mosaic_path+sha256、crop sha256、dump 命令与 stdout sha）——ISSUE-06 的「provenance 只加不破」原则。

验证（全离线，不花 live 调用）：

| 验证 | 结果 |
|---|---|
| 单测（4 锚夹具：2 ok + 1 crop_failed + 1 无记录） | FIELDS 对齐；quality_gate 2 eligible / 2 download_failed；artifact_id 公式与 `gehi_download` 一致；22 → 26 条全过 |
| 08-18 pilot 真实输出回放量产 QA | **24/24 release-eligible**，0 失败类 |
| 冷格子真实输出回放量产 QA | **12/12 release-eligible**，0 失败类 |

产物：`{a3_region_batch_pilot_20260818,a3_fullcell_probe_20260819/coldcell_pilot}/batch_dump/{manifest.csv, manifest_sidecar.csv, qa/}` + `report/manifest_summary.json`。

## §5 结论的变化与不变

- A3 机制链闭合：**并窗 complete（小簇→整格都成立）→ dump 无损拼过保真 bar（三格三 vintage）→ ~2 DN 被 owner 接受为打分真源（D4）→ 合成 manifest 过生产 QA**。handoff §8 未决清单全部清空。
- 不变：`ENABLE_REGION_BATCH` **false**；E0/E6 未签；全城下载 lane 未动；本轮没有翻任何生产开关。
- 纪律保持：17.2× 是 per-cell 刻画，**不写进 S0/S3 全城日历**；整格 270 瓦估算是形状说明，不是承诺。

## §6 Open Questions（继承 + 新增）

- [ ] 生产路径 owner 签署：dump 臂 + manifest 合成接进 wave plan（接哪条 lane、`ENABLE_REGION_BATCH` 翻转条件）——**这是剩下的唯一闸门**
- [ ] 整格 dump 真实下载未做（本轮按 handoff 只 probe）；270 瓦单次 dump 的 GEHI 行为（墙钟、403 敏感度）只有小簇样本
- [ ] 冷墙钟是 1 格 × 1 日期的刻画；更多冷格可加固但不改机理结论
- [ ] GDAL 直接挂 .jgw 会按 ESRI 中心解读——其他工具消费 dump 瓦片时须在入口纠正半像素（本仓 stitcher 已按角点处理并留注释）

## §7 复现

```bash
source scripts/activate_env.sh
pytest tests/temporal/test_gehi_region_batch.py -q            # 26 passed，全离线
# manifest 合成（离线，吃既有输出）
python scripts/temporal/run_a3_region_batch_pilot.py --only manifest_dump
# 整格 probe + 冷格选格（availability 触网，probe-only）
python scripts/temporal/run_a3_fullcell_probe.py               # plan -> availability -> export
# 冷格子双臂（live，命令由 export 阶段打印）
python scripts/temporal/run_a3_fullcell_probe.py --only export # 见 probe_summary.json 的 pilot_command
```

产物根：`~/zasolar_data/geid_temporal/a3_fullcell_probe_20260819/`。
