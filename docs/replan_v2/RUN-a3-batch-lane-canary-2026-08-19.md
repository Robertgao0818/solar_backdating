# RUN — A3 生产 batch lane：整格 pilot + 10 格 canary（D5 执行）

| 字段 | 值 |
|---|---|
| 文档 ID | `RUN-a3-batch-lane-canary-2026-08-19` |
| 日期 | 2026-08-19（10:09Z 出数） |
| Status | **整格 pilot 过 + 10 格 canary 过（QA 99.99%）**。`ENABLE_REGION_BATCH=true`（放量）**仍待 owner 看本报告后明确放行** |
| 执行依据 | [OWNER_DECISIONS D5](OWNER_DECISIONS.md)：①dump 臂替换所有逐张 ②整格 pilot ③先跑 10 grid 再放量 |
| 前序 | [`RUN-a3-fullcell-coldcell-probe-2026-08-19.md`](RUN-a3-fullcell-coldcell-probe-2026-08-19.md)（整格 availability + 冷格墙钟 + manifest 合成） |
| 真源 | `~/zasolar_data/geid_temporal/a3_fullcell_pilot_20260819/lane/report/summary.json`、`~/zasolar_data/geid_temporal/a3_canary_20260819/lane/report/summary.json` |

**一句话**：按 D5 把 dump 臂写成了生产 lane（`run_a3_batch_lane.py`：候选→`(格,日期)` 簇任务→单次 dump→无损拼→切开→manifest 合成→生产 QA），整格 pilot **1 次调用下完 341 锚（QA 341/341）**，10 格 canary **14,796 张 chip 全部落盘、QA 14,795/14,796 放行**；canary 抓到并修掉一个真生产问题（GEHI dump 对 vintage 未覆盖的矩形区域**静默跳瓦片**），修复后等效调用数 **296 vs 逐张 14,796（~50×）**。放量闸门留给 owner。

---

## §1 生产 lane 形态（D5 ① 的落地）

新 driver `scripts/temporal/run_a3_batch_lane.py`，五阶段可恢复（plan / run / manifest / qa / report，任务级 sentinel）：

- **planner**（`gehi_region_batch.plan_cluster_tasks`）：候选按 `(grid_id, capture_date, provider, zoom)` 并簇——分组键即红线，混 vintage 会毒化安装日推断；成员保留各自 version（AGENTS.md 规则 7）。
- **run**：每簇 1 次 `dump` → 无损拼 → 切开 q=95；失败簇成员回退逐张（D5 签署语义），失败类如实进 QA，**永不转为 absence 观测**。
- **manifest**：`synthesize_dump_manifest`（FIELDS 逐字对齐）+ 附加 sidecar；fallback 行走同一 `build_chip_provenance`。
- **qa**：原封不动的生产 `quality_gate`，分母 = 全部候选。
- **fail-closed 闸门**：plan 覆盖 >10 格时必须环境变量 `ENABLE_REGION_BATCH=true`，否则拒跑（D5：放量需 owner 明确放行）。

## §2 整格 pilot（D5 ②）

CPT2598，341 锚，联合框 1116×1120 m，日期 2025-02-28（probe 所得 57 个完整日期中最新）。

| 指标 | 值 |
|---|---:|
| GEHI 调用 | **1 次 dump**（285 瓦，12.8 s 冷下载） |
| 拼接 / 切开 | 0.23 s / 2.26 s（4864×3840 画布，341 张 q=95） |
| 总墙钟 | **15.3 s** vs 逐张估算 682 s（341×2 s 间隔）≈ **44.5×** |
| QA release-eligible | **341 / 341**，0 失败类 |

整格一簇在生产尺度（~270 瓦单次 dump）工作正常：无 403、无洞、无错位。

## §3 10 格 canary（D5 ③）

选格：wave_01 TM z19 候选（110,154 行 / 3,439 锚 / 90 格）按锚数分层等距 10 格（CPT0515…CPT1634，含最小与最密），确定性可复放。范围：**14,796 候选 → 296 个簇任务**。

### 最终结果（修复后）

| 指标 | 值 | 说明 |
|---|---:|---|
| QA release-eligible | **14,795 / 14,796（99.99%）** | 唯一失败：1 张 2020-08-30 近白图（p01=251/p99=255，该 vintage 该点确实无信息；如实记 `pixel_uninformative`，非管线 bug，不转 absence） |
| dump 成功率 | 296 / 296 | 全程无 403，`--exact-date`，间隔 2.0 s |
| 实际网络调用 | 296 dump + 847 fallback = **1,143** | 含修复前一次性 fallback（见 §4） |
| 修复版稳态调用 | **296 vs 14,796 ≈ 50×** | 同等 canary 全新跑 ≈ 296 次 dump + ~0 fallback |
| 墙钟（首轮实际） | 3,514 s ≈ 58.6 min | 含 847 次 fallback 的 ~30 min |
| 墙钟（修复版任务均值） | ~5.3 s/任务（间隔 2 s + 冷 dump + 拼切） | 冷网税实测小（§前序 RUN） |

### 对 wave_01 的形状说明（非日历承诺）

wave_01 TM z19 全量：110,154 候选 → **2,774 个簇任务 ≈ 2,774 次调用（~40×）**；按 canary 实测 ~5.3 s/任务 ≈ **4 小时级**单主机串行，对照逐张 ~64 小时。这是 per-wave 形状说明，不写进 S0/S3 日历；koko 单进程合同不变。

## §4 canary 抓到的真问题：GEHI dump 静默跳瓦片（已修）

**现象**：7/296 个簇（CPT1629×1 + CPT1634×6）dump 返回 rc=0 但瓦片网格有洞（4–24 个/簇，共 99 洞）——该 vintage 的影像不覆盖矩形并窗里的**非成员区域**（成员锚 96 m bbox 都有目录级 complete 保证，锚之间的空隙是别的 vintage 的）。

**第一轮行为（strict stitch）**：洞即败 → 整簇 847 个成员全部回退逐张（847/847 成功，QA 无损）。正确性保住了，代价是 847 次本可避免的调用。

**修复（同晚进库）**：
- `stitch_dump_tiles(allow_holes=True)`：洞零填充并如实返回洞格坐标；strict 默认不变。
- `crop_chip_from_mosaic(hole_cells=…)`：**切窗碰到洞即拒切**——洞像素永远进不了 chip，只有真正碰洞的成员回退。
- lane 全部走该路径；7 个洞任务用修复版重跑（瓦片复用，**0 网络调用**）：**847/847 成员全部从 dump 画布切出**——99 个洞全在非成员区，无一成员窗口碰洞，与「成员 bbox 目录级 complete」的机制推断一致。

**测试**：20 → 34 条全离线（洞缝合/拒切单测 + 手术级 fallback E2E：三锚中仅碰洞者回退）。

## §5 结论与剩余闸门

- D5 三条指令全部执行：dump 臂生产 lane 在库（①）、整格 pilot 过（②）、10 格 canary 过且出报告（③）。
- **剩余唯一闸门：owner 看本报告后放行 `ENABLE_REGION_BATCH=true`**，之后 wave_01（及后续 wave）按 §3 形状放量；Wayback/z18 走同一代码路径接入（`--provider/--zoom` 参数即开关），Leg-E E0/E6 节奏独立。
- 纪律保持：Top-52 生产 chips 未动；canary 产物在独立目录；失败类永不转 absence；日历不外推。

## §6 Open Questions

- [ ] **owner 放量放行**（唯一闸门）
- [ ] Wayback / z18 候选接入放量范围（同路径，未在 canary 范围）
- [ ] 放量时的 lane 接线：route 分片（home_v4/home_v6/koko_v4）如何与簇任务正交——候选里 route_id 已失去意义（调用数降 ~50×，koko 瓶颈大幅缓解）
- [ ] 99 洞全部落在非成员区是 7 个任务的样本；放量中若出现成员窗口碰洞，手术级 fallback 会如实记录其频率（sidecar `sequential_fallback:*`）

## §7 复现

```bash
source scripts/activate_env.sh
pytest tests/temporal/test_gehi_region_batch.py -q   # 34 passed，全离线
# canary（10 格，fail-closed 闸门内）
python scripts/temporal/run_a3_batch_lane.py \
  --candidates-csv <wave_01_tm_z19 合并候选> \
  --output-dir ~/zasolar_data/geid_temporal/a3_canary_20260819/lane \
  --canary-grids 10 --provider TM --zoom 19
# 放量（未放行）：ENABLE_REGION_BATCH=true 才会接受 >10 格
```

产物：`~/zasolar_data/geid_temporal/a3_fullcell_pilot_20260819/lane/`、`~/zasolar_data/geid_temporal/a3_canary_20260819/lane/`（plan/run/manifest/qa/report + per-task 记录 + sidecar）。
