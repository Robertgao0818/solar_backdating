# RUN — Cape Town 全城批处理磁盘流水线 rev-6（Dropbox 主归档 · koko 轻载 · 单相位）

| 字段 | 值 |
|---|---|
| 文档 ID | `RUN-cape-town-citywide-batch-disk-pipeline-2026-08-18` |
| 版本 | **rev-6**（2026-08-18，owner 评审后修订） |
|  supersede | Grok rev-5 草稿（`/tmp/grok-1000/grok-design-doc-ad849d97.md`，不入仓） |
| Status | **执行中 / AWAITING OWNER E0+E6 TOKEN**（B0✓ E2✓ B1上传中 wave_01 分片就绪；见 §13 执行日志） |
| 上位 | [`RUN-cape-town-citywide-legE-production-plan-2026-08-12.md`](RUN-cape-town-citywide-legE-production-plan-2026-08-12.md) · [`RUN-cape-town-citywide-backdating-delivery-plan-2026-08-12.md`](RUN-cape-town-citywide-backdating-delivery-plan-2026-08-12.md) |
| 治理 | [`OWNER_DECISIONS.md`](OWNER_DECISIONS.md)：E0/E6 **机器 token 行**（见 §Governance） |
| 范围外 | GPU / DINOv3 student scorer / RunPod 推理 |

**给执行 agent 的一句话**：E0+E6 双 token 到位后，B0 实测 → B1 把 Top-52 逐卷 tar 直传 **Dropbox**（卷级 sha256 + rclone check + restore 演练）再 release 回收 ~59G；E2 merge PASS 后按波 `download → merge(改写 path) → QA → archive(rclone→Dropbox, BACKUP_OK) → staged → score(Gemini) → release`。home 热集 ≤2 波，双缓冲 download N+1 ∥ score N。koko 只跑 1 个下载进程、不存归档。`released` ≠ 可打分。

---

## §0 rev-6 owner 修订（相对 rev-5 的全部差异）

| # | 修订 | 理由（owner 原话摘要） |
|---|---|---|
| R1 | **Dropbox 是唯一 durable archive**（rclone remote `dropbox:` 已配好）；koko 不再承担 archive / spill | rev-5 实测 koko 全盘仅 **335G avail**，装不下 ~334G 归档总量，方案自己的 KOKO_STOP 水线第一波就会触发 |
| R2 | **hash 按包对，不按瓦片对**：只保留每卷 tar 的整卷 sha256；**删除** rev-5 的 `members.sha256.jsonl` per-file 内容哈希 | 降低 B1/archive 复杂度与耗时 |
| R3 | **koko 轻载**：8G RAM，最多 **1 个 GEHI 进程**（nice 19 + ionice idle）；lane 与 TM3 严格串行；merge 后立即删 koko work chips | owner：挂两个下载进程后 koko 基本没法用 |
| R4 | **Top-52 不留 koko**：home 直接打卷传 Dropbox；koko 上既有 23G Top-52 工作副本在 B1 BACKUP_OK + 演练后删除 | 同上 |
| R5 | **E0+E6 同时签，单相位运行**（原 Phase B 流程从 wave 01 开始）：不再有 Phase A「先 release 后 restore 再打分」的往返 | 避免每波 12G 的 Dropbox→home restore 循环；Gemini ~25 min/波 远快于下载 |
| R6 | **前 3 波实测字节/锚点** 回写 `waterlines.json`（citywide A48 占比 16.8% vs Top-52 8.0%，54 KB/chip 刻度可能低估） | owner 同意 |
| R7 | **新增 §9 下载加速计划**（box 槽位 / 加权分片 / interval 复测 / 区域批量下载 pilot 等） | owner 要求研究加速 |
| R8 | **分片必须加权**（`--route-weights`）：现有 `stable_download_route` 是均匀 hash，koko 降载后等分会拖死每一波 | 见 §8 |

---

## §1 实测基线（2026-08-18 本机实测，非转述）

| 路径 / 量 | 值 | 备注 |
|---|---:|---|
| home `/dev/nvme0n1p9` | 250G total · 159G used · **89G avail** | 热集 + Gemini 唯一宿主 |
| Top-52 chips | **64G** | B1 后 release，预期 `df` 回收 ~59G（btrfs zstd） |
| GEHI tile cache（home） | **24G** | 禁止作为首个腾空间动作删除 |
| 全城 run root | ≈14G | `ct05_catalog_v1/merged/` **为空**（E2 merge 未跑） |
| anchors 总数 | 111,801 | `groups_v1/chip_groups_as_anchors.csv` 实测行数 |
| non-Top-52 分母 | **90,348** | `ct05_catalog_v1/plan/remaining_non_top52_anchors.csv` 已在 |
| koko | 全盘 469G / **334G avail** · **6G RAM**（实测，比 owner 印象更紧） | 既有 23G Top-52 工作副本（冗余，B1 后删） |
| Dropbox | **1.904 TiB free**（实测 `rclone about`）· remote `dropbox:` 已配置 | 唯一 durable archive |
| Top-52 tar 字节 | **36.7 GiB logical / 3 卷**（实测；du 64G 含小文件块开销） | 修正归档总量：36.7 + 波 ~160 ≈ **~197 GiB** |
| rclone | `/usr/bin/rclone` | home 本机 |
| lane 默认 `REQUEST_INTERVAL` | **1.0**（`run_ct05_download_lane.sh:29`） | 编排器必须显式 export **2.0** |
| lane `dirname(RUN_ROOT)` 约定 | 已核实（`:22`） | `RUN_ROOT=$CITYWIDE/wave_NN` 布局成立 |
| E0/E6 token | **均不在** `OWNER_DECISIONS.md` | fail-closed，未签不下载 |

**像素预算**（唯一刻度 = Top-52 实测 64G / 1,178,227 candidates / 54.9 chips/anchor / ~54 KB/chip）：

- 全城 ~330G；**下载分母 = non-Top-52 ~270G**（`64G × 90348/21453 ≈ 269.5G`）。
- 单波 3500 anchors ≈ 10.4G，规划 `wave_bytes = 12G`；前 3 波实测回写（R6）。
- 归档总量 ≈ 64G（Top-52）+ ~270G（波）≈ **334G vs Dropbox 2.0T** → 容量不再是约束。

## §2 角色拓扑（rev-6）

| 主机 | 角色 | 约束 |
|---|---|---|
| **home** | Gemini 网关 + 热集（≤2 波）+ merge 控制面 + **archive 上传端**（rclone→Dropbox） | 水线见 §5 |
| **koko** | **仅** 轻载下载 egress | 8G RAM；**同时最多 1 个 GEHI 进程**；nice 19 / ionice -c3；不存归档、不做 spill；work chips 在 MERGE_OK 后立即删 |
| **box** | 可选下载 egress（OQ1 未填） | 填实后 +2 route，见 §9-A1 |
| **Dropbox** | 唯一 durable archive | `dropbox:zasolar_backdating/cape_town_citywide_v1/` |

`pipeline/hosts.json` schema 相应改为：koko 删除 `archive_root`/`spill` 角色，新增顶层：

```json
"dropbox": {
  "remote": "dropbox:",
  "archive_root": "zasolar_backdating/cape_town_citywide_v1",
  "verify": "rclone check (dropbox content hash) at upload; GNU sha256 -c at restore",
  "bwlimit": "off-peak unlimited; scoring 窗口建议 --bwlimit 20M"
}
```

## §3 目录布局（与 rev-5 一致，仅 archive 目标变化）

```text
CITYWIDE=/home/gao/zasolar_data/geid_temporal/cape_town_citywide_backdating_v1_20260812
DL_ROOT=$CITYWIDE/ct05_download_v1
WAVE_RUN=$CITYWIDE/wave_NN        # dirname→$CITYWIDE → groups_v1 命中（已核实）

$DL_ROOT/
  plan/{remaining_non_top52_anchors.csv,remaining_non_top52_roster.csv,wave_plan.json}
  plan/waves/wave_NN/{wave_NN_all,wave_NN_A24,wave_NN_A48}.csv + candidates_2019plus.csv + membership_expected.json
  chips/                          # home 热集（唯一定义）
  waves/wave_NN/
    lanes_merged/<host>/**/*_manifest.csv   # path 已改写为 home 绝对路径
    merge/{membership_audit.json,MERGE_OK.json}
    qa/ ... qa/.done
    BACKUP_OK.json                # 卷名 + bytes + GNU sha256 + dropbox 路径
    SCORE_OK.json · RELEASE_OK.json
  archive_staging/                # 本波 tar 卷暂存（峰值 ≤1 卷 ≤16G，传完即删）
```

Dropbox 侧：

```text
dropbox:zasolar_backdating/cape_town_citywide_v1/
  top52_chips_20260818/{part01..partNN}.tar + BACKUP_OK.json
  waves/wave_NN/wave_NN_part01.tar + BACKUP_OK.json（由 home 上传）
```

## §4 状态机（单相位，rev-5 Phase A/B 合并）

```text
planned → downloading → merging → qa → archiving → staged → scoring → released
                                                （任一步 → failed）
```

| 状态 | chips 在 home? | 可 score? |
|---|---|---|
| `archiving` | yes | no（rclone 上传中） |
| `staged` | **yes** | **yes**（BACKUP_OK + qa/.done + 未 release） |
| `scoring` | yes | in progress |
| `released` | no | **no**；重打需 `stage-wave` 从 Dropbox 拉回（例外路径，非默认） |

`ready_to_score = (state==staged) && chips_on_home && backup_ok && qa_done`。`released ⇒ ready_to_score=false` 恒成立。
双缓冲日历：wave N+1 downloading ∥ wave N staged/scoring ∥ wave N−1 release。archive（rclone 上传 ~12G）与 download 共享 home 带宽，必要时 `--bwlimit`。

## §5 Waterlines（B0 实测后写 `pipeline/waterlines.json`）

```json
{
  "schema_version": "ct_citywide_waterlines_v2",
  "fs_size_bytes": 268435456000,
  "wave_bytes": 12884901888,
  "home_reserve_bytes": 16106127360,
  "home_hot_max_waves": 2,
  "home_avail_floor_frac": 0.20,
  "archive_volume_bytes_max": 17179869184,
  "dropbox_free_floor_bytes": 536870912000,
  "notes": "tile cache 已在 df used 内，不得重复扣减；dropbox floor=500G，低于则 L3"
}
```

- **2-wave 强制点**（不变）：`merge-wave` / `stage-wave` 入口；`hot_wave_count_on_home >= 2` → exit 2 / 阻塞。launch-wave 不强制（下载可领先）。
- **home 空间 STOP**（OR）：`avail < 0.20×fs`（=50G）；或 `avail < 2×wave_bytes + 15G`（≈39G）。
- **B1 后 home 工作点**：89+59 ≈ **148G avail**；稳态峰值 = 热集 2×12G + 工作树 ~12G + staging ≤16G ≈ 52G，余量充足。
- **Dropbox STOP**：`rclone about dropbox:` free < 500G → L3。
- koko 仅需容纳当前波 work chips（加权后 ~2.5G）+ 既有 tile cache，无水线风险；仍记 `df` 进 B0。

## §6 阶段流程

### B0 — host inventory（E0+E6 token 后）

rev-5 项目 + 新增：

```bash
rclone about dropbox:                      # free 写入 waterlines
ssh koko@koko-82xm 'df -B1 / | tail -1; free -g | head -2'   # 记录 8G RAM 事实
# koko 23G Top-52 工作副本：抽样验证其 chip 均已在 home 64G 集合内（sha1 抽样 100 个）
# → 验证通过则标记 koko_top52_copy=redundant，B1 BACKUP_OK 后删除
```

### B1 — Top-52 backup（Dropbox 直传）+ restore 演练 + release

**逐卷闭环，峰值额外 ≤16G；只按卷对 hash（R2）。**

```bash
STAGING=$TOP52/ct05_download_v1/archive_staging
DBX=dropbox:zasolar_backdating/cape_town_citywide_v1/top52_chips_20260818
# part01..partNN.members.txt 预生成，每卷 ≤16G（64G → 约 4-5 卷）
for part in part01 ...; do
  tar -C "$TOP52/ct05_download_v1/chips" -cf "$STAGING/${part}.tar" -T "${part}.members.txt"
  sha256sum "$STAGING/${part}.tar" >> "$STAGING/volumes.sha256"      # 整卷 hash，唯一 hash 产物
  rclone copyto "$STAGING/${part}.tar" "$DBX/${part}.tar" --checksum # 上传即带完整性校验
  rclone check "$STAGING" "$DBX" --include "${part}.tar" --one-way   # dropbox content hash 对卷
  rm -f "$STAGING/${part}.tar"                                       # PASS 才删本地卷
done
# 写 BACKUP_OK.json：每卷 {path, bytes, gnu_sha256}
```

**Restore 演练**（release 前强制；此时原件仍在盘上，比对零成本）：

```bash
rclone copyto "$DBX/part01.tar" /tmp/top52_drill/part01.tar   # 真走 Dropbox 往返
cd /tmp/top52_drill && sha256sum -c <(grep part01 "$STAGING/volumes.sha256")
tar -C /tmp/top52_drill -xf part01.tar $(grep '^CPT2932/' part01.members.txt)
# 逐文件 cmp /tmp/top52_drill/CPT2932/... vs $TOP52/.../chips/CPT2932/... → RESTORE_DRILL_OK.json
```

**Release**（唯一删除入口 `run_ct_citywide_disk_pipeline.sh b1 --release`）：
`BACKUP_OK` + `RESTORE_DRILL_OK` 双哨兵 → `chip_lifecycle.py release --backup-ok` → `RELEASE_OK.json` → 记录 `df` 回收（期望 ~59G）→ **删 koko 23G 冗余工作副本**。中断 → `failed`，禁止续跑 release，恢复路径 = 从 Dropbox 拉回对应卷。

### E2 merge（A2 硬前置，可与 B0/B1 并行）

```bash
bash scripts/temporal/run_ct_citywide_catalog_e2.sh merge
bash scripts/temporal/run_ct_citywide_catalog_e2.sh coverage
test -f "$CITYWIDE/ct05_catalog_v1/merged/.done" || exit 2   # A2 开头硬检查
# 用实测 2019+ candidate 数回写 wave_bytes 与 ~270G 预算
```

### A2 — wave plan（分母 90,348；schema `ct_citywide_download_wave_plan_v2`）

与 rev-5 相同，**外加**：`--route-weights`（见 §8）；波内 anchors 按 grid 聚簇排序（§9-A4）。plan immutable；缩小波 = 新目录新 schema id。

### A3 — launch-wave（`RUN_ROOT=$CITYWIDE/wave_NN`；`REQUEST_INTERVAL=2.0` 显式导出）

- home：`home_v4` / `home_v6` 全配置（lane + TM3 3-lane，`TM3 PARENT_PID` 合同不变）。
- **koko：轻载合同（R3）**——同一时刻最多 1 个 GEHI 进程：

```bash
ssh koko@koko-82xm 'cd $REPO && export REQUEST_INTERVAL=2.0
  RUN=$KOKO_CITYWIDE/wave_NN
  # 1) 先主 lane（TM18/WB 残余，份额小），跑完再 TM3；严格串行
  nice -n 19 ionice -c3 bash scripts/temporal/run_ct05_download_lane.sh koko_v4 "$RUN" \
    >> "$RUN/lanes/koko_v4/launch.log" 2>&1
  # 2) 主 lane 结束后才 TM3，且 --lanes 1
  nice -n 19 ionice -c3 bash scripts/temporal/run_ct05_tm3_route.sh koko_v4 "$RUN" "$LANE_PID"'
```

- koko 份额由加权分片控制（§8），不给等分。
- Gemini 脉冲期间不启 home 新 lane（P-prefer；不用 SIGSTOP）。

### merge-wave（合同不变，两点 rev-6 注记）

入口 2-wave 闸门 + 空间公式 → host tar → 解包 `$DL_ROOT/chips` → `lanes_merged` path 改写 → membership audit → `MERGE_OK` → **删 home/koko 双方 work chips**（koko 侧删除同时释放其磁盘，R3）。koko tar 拉取 ~2.5G/波，对 koko 是一次性只读负载，可接受。

### qa-wave / archive-wave / score-wave / release-wave

- **qa-wave**：只读 `lanes_merged` 改写后 path（合同不变）。
- **archive-wave**（rev-6 重写）：members = membership ∩ QA release_eligible → 单卷 `tar -C $DL_ROOT/chips`（12G ≤ 16G 上限，一波一卷）→ `sha256sum` 入 `volumes.sha256` → `rclone copyto --checksum` → `rclone check --one-way` → 删 staging 卷 → 原子写 `BACKUP_OK.json` → 状态 `staged`。**不删 home chips。**
- **score-wave**：闸门 = E6 token + `staged` + `HOME_GEMINI_READY` + runtime lock 非 UNSET + canary preflight；`run_adaptive_scan.py` 命令行与 rev-5 §15.3 相同（分母 non-Top-52，~231k–243k HTTP；Top-52 用既有 verdict merge，KD20 不变）。
- **release-wave**：`BACKUP_OK` + `SCORE_OK` 双哨兵，缺 exit 2；只删 `$DL_ROOT/chips` 本波成员。
- **stage-wave**（例外路径）：仅当需重打 released 波，`rclone copy` 拉回 → 解包 → `staged`；同 2-wave 闸门。

## §7 Fail-closed ladder（rev-6）

| 阶 | 动作 |
|---|---|
| L0 | 暂停全部 download（不启新 lane，不 SIGSTOP 中途 chip） |
| L1 | 仅释放可回收波：`backup_ok ∧ score_ok ∧ state∉{staged,scoring}` |
| L2 | 未 BACKUP_OK 的完整波优先 archive-wave（Dropbox 有空间时） |
| L3 | Dropbox free < 500G 或水线仍红 → `STOPPED_OWNER`，owner 决议：(a) Windows C: 中转 (b) 新更小 wave plan (c) tar 保留策略（见 OQ3） |
| L4 | 单波无法满足 `HOME_GEMINI_READY` → 留 tar，`skipped_score_disk`，禁止 force-delete 打分热集 |

## §8 加权分片（R8，新代码，阻塞项）

现状：`run_ct05_chip_pipeline.py` 的 `stable_download_route` 是 `hash % len(routes)` **均匀**分配。koko 降载到 1 进程后吞吐 ≈ 0.4–0.6 chip/s（home 单 route ≈ 1 chip/s），等分会让 koko 成为每波瓶颈（波完成 = 最慢 route 完成）。

**改动**：`plan` 子命令加 `--route-weights home_v4=2,home_v6=2,koko_v4=1`（box 填入后 `box_v4=2,box_v6=2`）；分配仍用稳定 hash，但对 weight>1 的 route 复制槽位后取模。`plan_ct05_tm3.py` 同步接受每 route 候选数输入（天然按 shard 来）。启动前断言各 route shard 比例 ≈ 权重 ±5%。

## §9 下载加速计划（R7；联网调研结论 + 仓内事实）

**调研结论（GEHistoricalImagery 上游）**：官方文档无任何 rate-limit/403 指南；`--parallel`（默认 ALL_CPUS，仓内固定 4）只控制**单次调用内**的 tile 并发，Wayback 侧经验上限 10。403 软封是**本仓 2026-07-12 实测**的 TM 行为（~1 req/s + 60s soft / 1800s hard backoff），瓶颈单位是「每地址族 GEHI 调用次数/秒」，不是 tile 数。政策红线（rev-5 §Security 保留）：**不做源 IP 轮换等绕封禁手段**。

| ID | 杠杆 | 预期收益 | 成本 / 状态 |
|---|---|---|---|
| A1 | **填 box 槽位**（+2 全速 route；候选：Windows 机 WSL2 走独立 IPv6——同 LAN 的 v4 共 NAT 无效，v6 全局地址独立有效；或其它独立 egress） | 4.5 vs 2.5 chip/s ≈ **工期 −45%** | 仅 B0 填槽 + 403 smoke；**优先级最高** |
| A2 | `REQUEST_INTERVAL` 复测 canary：单 route 试 1.5s × 1h，盯 403 计数；干净则推广 | 每 route **+33%** | 1 小时实验；403>0 即回退 2.0 |
| A3 | **区域批量下载 pilot**：一次 GEHI 调用覆盖同 vintage 多 anchor 的 bbox，本地切开成 per-anchor chips | 对 TM-z19 主体 **2–4×**（投机） | **pilot MIXED 2026-08-18**（[`RUN-a3-region-batch-pilot-2026-08-18.md`](RUN-a3-region-batch-pilot-2026-08-18.md) · 交接 [`HANDOFF-a3-region-batch-2026-08-19.md`](HANDOFF-a3-region-batch-2026-08-19.md)）。`download` 整图 JPEG 把 MAE 做高；`dump` 无损拼在 CPT2713 上 12/12 MAE≤5。生产应 dump。开关仍 false |
| A4 | 波内 anchors 按 grid 聚簇排序，最大化 24G 共享 tile cache 命中（相邻 A48 同 vintage 共享 z19 瓦片） | 每 chip 网络量下降，间接提速 | 仅 plan 排序，零风险，A2 顺带做 |
| A5 | （可选探测）Wayback provider 是**另一套限速域**（Esri），可作补充 route family；但 capture-date 逐瓦片查询慢（上游建议 layer-date 才快），且扫描器按 captured date 推理 | 不确定 | 先 probe CT 2019+ Wayback 覆盖再议；非承诺 |
| A6 | （owner 决策项）降低需求：vintage 抽稀（如每季 1 个）直接减总量 | 线性 | **默认否**，损精度；仅在 S0 日历不可接受时上桌 |

**日历重估（26 波，下载 bound；Gemini 25 min/波 永不瓶颈）**：

| 场景 | 有效吞吐 | 全城下载 |
|---|---|---|
| S0 现状（home×2 + koko 轻载，无 box） | ~2.5 chip/s | **~20–23 天** |
| S1 = S0 + box（A1） | ~4.5 chip/s | ~13 天 |
| S2 = S1 + interval 1.5s（A2） | ~6 chip/s | ~9–10 天 |
| S3 = S2 + 区域批量 pilot 命中（A3） | 投机 | ~4–6 天 |

> 诚实注记：koko 轻载（R3）相对 rev-5 的 4 全速 route 让 S0 从 12–15 天涨到 20–23 天；A1/A2 是补回这段的正路。

## §10 Governance tokens（rev-6：双 token 同签，R5）

`OWNER_DECISIONS.md` 新带日期标题下，独立一行等号赋值（fail-closed，禁止 grep 叙述段）：

```text
E0_PREFETCH_DISK_SIGNED=2026-MM-DD
E6_SCORER_GO=2026-MM-DD
```

| 开关 | 默认 | 翻转条件 |
|---|---|---|
| `ALLOW_CITYWIDE_DOWNLOAD` | false | E0+E6 token + B0 + B1 + E2 merge PASS |
| `ALLOW_GEMINI_WAVES` | false | E6 token + lock 非 UNSET + preflight |
| `ENABLE_BOX_ROUTES` | false | box 槽位填实 + 16-query + 1h download 403 smoke |
| `ENABLE_REGION_BATCH` | false | A3 pilot PASS 后 owner 决议 |

## §11 PR Plan（rev-6 重排；PR 序号即依赖序）

| PR | 内容 | 相对 rev-5 |
|---|---|---|
| PR1 | hosts.json（koko 轻载角色 + dropbox 段）+ B0（含 `rclone about`）+ token gate + status schema | 改 |
| PR2 | box route 白名单 + citywide health 默认路径 | 同 |
| PR3 | `chip_lifecycle --anchor-ids-file --backup-ok` | 同 |
| PR4 | **`archive_rclone.py`**：卷 tar → 整卷 sha256 → `rclone copyto --checksum` → `rclone check` → BACKUP_OK；restore helper（Dropbox 拉回 + `sha256sum -c` + grid cmp） | **替换** rev-5 PR4（删 members 内容哈希） |
| PR5 | B1：Dropbox 逐卷 + drill + release + 删 koko 冗余 Top-52 副本 | 改 |
| PR6a | wave plan + **`--route-weights`**（§8）+ grid 聚簇排序（A4）+ `--no-canary` | 改 |
| PR6b | merge-wave + path 改写 + 2-wave 闸门 + koko work chips 删除 | 同 |
| PR6c | launch-wave：koko 串行轻载合同（nice/ionice、lane→TM3 顺序、TM3 `--lanes 1`） | 改 |
| PR7 | score-wave + stage-wave + release-wave（单相位；SCORE_OK 后才 release） | 改（去 Phase A 分支） |
| PR8 | observability + halt-ladder + **`rclone about` 监控 + 403 计数** | 改 |
| PR9 | （独立、可并行）A3 区域批量下载 bounded pilot（≤2 grid；含切开 + manifest 合成 + QA） | **MIXED 2026-08-18**，见 [`RUN-a3-region-batch-pilot-2026-08-18.md`](RUN-a3-region-batch-pilot-2026-08-18.md)；不接主流水线 |
| PR10 | （小）A2 interval canary 脚本：单 route 1.5s × 1h + 403 报表 | **新** |

## §12 Open Questions（rev-6）

1. box 槽位：Windows/WSL2 是否具备独立全局 IPv6？`rog`/`box` 是否同机？（A1 前置）
2. E0+E6 签字日。
3. tar 保留策略：Dropbox 2T 下当前无需 pruning；若未来全城+其它城市累积 >1.6T，owner 决定是否对已交付波删 tar（届时改本文档）。
4. A5 Wayback 补充 route：是否授权一次 CT 2019+ 覆盖 probe？
5. A6 vintage 抽稀：默认否；仅 S0/S1 日历不可接受时再议。
6. Top-52 不重打 Gemini、merge 旧 verdict（承 rev-5 KD20，owner 已默认）。

---

## §13 执行日志（2026-08-18，rev-6.1）

token 未签前的 pre-flight 全部完成；以下相对 rev-6 正文的偏差即为现行事实：

| 项 | 状态 / 偏差 |
|---|---|
| B0 | **DONE**（pre-token snapshot）：`pipeline/hosts.json` + `pipeline/waterlines.json` 已写实测值 |
| E2 merge | **DONE**：111,801/111,801 outcomes、**0 operational failure**、2019+ candidates **6,217,288**（外推比 1.013）、55.6 dates/anchor；`merged/.done` 已写 |
| E2 merge 实现 | **重写为流式**（`run_ct05_catalog_probe.py`）：原实现把 22.3M+ catalog 行全量物化 → OOM 杀机（30G 机器）；且 `routes_observed` 是 O(anchors×outcomes) 嵌套扫描，全城规模下不可行。新实现：outcomes 流式建索引 → catalog 行 slim TSV → 外部排序（LC_ALL=C, stable）→ 按 anchor 组流式判决；峰值 RSS ~0.5G。wrapper 的 pandas coverage 段（系统 python3，6.1GB CSV 上 segfault）替换为 `e2_merge_coverage.py`（csv 流式）。8 个既有测试全过 |
| B1 | **上传中**（tmux `ct_cw_b1`）：3 卷 × ≤16.4 GiB（tar 含 ~1KB/文件头开销，volume 上限按 content 15.5GiB + 开销理解）；`b1_top52_dropbox_archive.sh {plan\|upload\|drill\|status}`；drill = part01 拉回 + 卷 sha256 + 200 文件抽样 `cmp` 对盘上原件（替代原 CPT2932 grid grep，anchor 目录不以 grid 前缀） |
| launch gate | **微调**：硬门 = E0+E6 token + `merged/.done` + B1 `BACKUP_OK` + `RESTORE_DRILL_OK`；`RELEASE_OK` 可尾随（lifecycle 全量 hash 需数小时，wave_01 峰值 ~24G 远小于 89G 余量）。KD7 的可恢复性含义不变 |
| lane/TM3 协调 | **去信号化**：新增 `CT05_LANE_SKIP_TM_Z19=1` knob（lane 跑完 TM18/WB18/WB19 后等 TM3 marker 再写 `.complete`），替代 Top-52 时代的人工 SIGSTOP/SIGCONT；TM3 加 `TM3_LANES` env 与缺 CSV 跳过；两个脚本白名单加 `box_v4\|box_v6` |
| koko 轻载落地 | lane-only（不跑 TM3），`nice -n 19 ionice -c3`，任意时刻 1 个 GEHI 进程 |
| A2 | **DONE**：roster 由 remaining anchors 派生（source_grid 排序去重）；`plan_ct52_production_waves.py --no-canary` 新增；**27 波 × 3500**（grid 聚簇，A4 顺带落地）；schema `ct_citywide_download_wave_plan_v2` |
| A3 预备 | **DONE**：`--route-weights` 已实现（稳定 slot 展开，home:koko=2:2:1，±5% 断言）；`filter_wave_candidates.py`；**wave_01 分片就绪**：128,718 candidates（home_v4/v6 各 ~40% + TM3 3-lane 计划，koko_v4 ~20%），域外断言通过 |
| 编排器 | `run_ct_citywide_disk_pipeline.sh`：gate / plan-waves / plan-wave-shards / launch-wave / merge-wave（2-wave+水线闸门、rsync `--remove-source-files`、path 改写、membership 审计）/ qa-wave（≥99% admit 门）/ archive-wave（rclone 单卷 + BACKUP_OK）/ prepare-score-lock / score-wave（staged-only）/ release-wave（BACKUP_OK+SCORE_OK 双哨兵 + `--anchor-ids-file` 子集删除） |
| PR3 | **DONE**：`chip_lifecycle release` 非 dry-run 必须 `--backup-ok`；`--anchor-ids-file` 子集删除；测试更新 23 过 |
| box（A1） | tailnet 上发现 **rog-1**（100.111.51.77，离线）与新机 **bot**（100.86.6.50，在线但 SSH 未授权常见用户）。等 owner 给 bot 的 SSH 用户/授权后填槽 + 403 smoke |
| 下载吞吐实测基准 | wave_01：128,718 candidates；home 单 route ≈1 chip/s、koko 轻载 ≈0.5 chip/s → **~14–15h/波**（与 S0 日历一致） |

**token 到位后的首个动作序列**：`b1 drill` → `launch-wave 1`（home×2+TM3、koko_v4）→ `b1 --release`（lifecycle 全量 hash，tmux 数小时）→ 波流水线双缓冲启动。

*rev-6 修订依据：owner 评审意见（2026-08-18）+ 本机实测（home 89G / koko 334G·6G RAM / Dropbox 1.904TiB / rclone dropbox: 已配）+ GEHI 上游文档调研（无 rate-limit 官方指南；--parallel 语义；Wayback layer-date 提速不适用于 captured-date 扫描）。*
