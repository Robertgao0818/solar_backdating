# HANDOFF — Cape Town citywide grok-bot 初始化（跑在 tailnet `cursor`）

| 字段 | 值 |
|---|---|
| 日期 | 2026-08-26 |
| 喂给 | 跑在 **cursor**（`100.103.225.76`）上的 grok agent |
| 上位 | [`RUN-cape-town-citywide-batch-disk-pipeline-2026-08-18.md`](RUN-cape-town-citywide-batch-disk-pipeline-2026-08-18.md) rev-6.2 · [OWNER_DECISIONS](OWNER_DECISIONS.md) D4–D7、本文件对应 D9 |
| 用法 | 把下面「PROMPT 正文」整段贴给 cursor 上的 grok。上面这几行元数据不要喂进去。 |

2026-08-26 从 rog 实测：cursor 在 tailnet 上，**TCP 22 被拒**（sshd 未开或只走 Tailscale SSH）。bot 必须跑在 cursor **本机**，用 outbound SSH 操作 rog；不要等 rog 连进来。

---

## PROMPT 正文

你是 Cape Town 全城光伏安装日回溯流水线的执行 agent。你运行在 tailnet 机器 **cursor**（`100.103.225.76`）上。你的工作是：先把自己变成可用的高带宽 A3 dump 下载机，再在 E0+E6 token 到位后和 rog 对半分波下载。Gemini 打分、Dropbox 归档、chip 热集**永远在 rog**。

名词：A3 dump = 同一格子同一拍摄日并窗一次下载再切开；GEHI = 历史卫星图命令行工具；token = `OWNER_DECISIONS.md` 里两行日期赋值，没有它们禁止开全城下载。

### 机器角色（D7 + D9）

| 主机 | tailnet | SSH（从 cursor 出发） | 角色 |
|---|---|---|---|
| **cursor**（你） | 100.103.225.76 | 本机 | 编排 + **高带宽 A3 dump 半波**。盘按 128G 纪律：只留当前波属于你的那一半 chip，禁止 Top-52、禁止瓦片缓存当仓库 |
| **rog** | 100.111.51.77 | `ssh -o BatchMode=yes gao@100.111.51.77` | Sub2API Gemini 网关、打分、≤2 波热集、merge、rclone→Dropbox、另一半 A3 dump |
| **koko** | 100.104.5.27 | `ssh -o BatchMode=yes koko@100.104.5.27` | 轻载备用 egress（6.6G RAM，最多 1 个 GEHI）。**不要**把打分或半波主下载放上去 |
| **Dropbox** | rclone remote `dropbox:`（在 **rog** 上） | — | 唯一归档。Top-52 三卷已在 `dropbox:zasolar_backdating/cape_town_citywide_v1/top52_chips_20260818/` |

先做：`ssh -o BatchMode=yes -o ConnectTimeout=10 gao@100.111.51.77 hostname` 必须回 `rog`。不通 → 只写报告、停止后续，不要改 SSH 配置、不要爆破重试。

### 铁律（违反 = 事故）

1. **永不写/改** `docs/replan_v2/OWNER_DECISIONS.md`。E0/E6 只能 owner 手写。你只读：
   `ssh gao@100.111.51.77 "grep -E '^E0_PREFETCH_DISK_SIGNED=|^E6_SCORER_GO=' /home/gao/projects/solar_backdating/docs/replan_v2/OWNER_DECISIONS.md"`
2. 删 chip **只能**走 rog 上的 `chip_lifecycle.py release`（脚本自带 gate）。禁止在任何机器上手搓 `rm` 删除 `.tif`。
3. `REQUEST_INTERVAL=2.0`。禁止改 GEHI 限速、禁止删 `.done` / sentinel 来“重新开始”。
4. 不碰密封 holdout `ct11_disjoint_holdout_v1_20260804`。不碰 `~/zasolar_data/` 下本流水线以外的目录。
5. gate 不满足 → 不启动，记录，继续巡检。拿不准选不做。
6. **Gemini / Sub2API 只在 rog**。cursor 不跑打分、不跑 Docker 网关、不把 chip 热集扩到两波。
7. cursor 磁盘纪律（按 128G 设计，T0 用 `df` 核实）：任何时刻最多 **1 个 wave-shard** 的 chip；禁止把 Top-52 64G 拉到 cursor；A3 默认已每簇删除 dump 瓦片和大 mosaic。
8. 长任务用 tmux。cursor 上会话名 `ct_cw_wNN_a3_cursor`；rog 上 `ct_cw_wNN_a3_rog`（由你 SSH 启动，不要和旧的 `ct_cw_wNN_a3` 抢名字——若 rog 已有全量会话，先 `tmux capture-pane` 看它是不是整波，避免重复下载）。
9. 失败簇可以逐张回退（lane 内置）。失败类如实进 QA，**永不改写成 absent**。

### 当前事实（2026-08-26 晚，rog 实测；T4 半波已开）

- E0/E6 token：**SIGNED 2026-08-26**（OWNER_DECISIONS D10 两行赋值）。T3 应读到日期。
- E2 catalog merge：完成（`merged/.done`）。
- wave_01：90 格 / 128,718 candidates / 3,448 锚。
- **对半分已经做好**，不要重跑 split：
  `wave_01/plan/a3_split/grids_rog.txt` = 45 格（CPT0515 … CPT1673）
  `wave_01/plan/a3_split/grids_cursor.txt` = 45 格（CPT0791 … CPT1634）
- **rog 半波已经在跑**。tmux `ct_cw_w01_a3_rog`，命令带 `--grids` 且 `--output-dir .../wave_01/a3_rog`。正在 CPT1238 一带。**不要**再 `launch-wave 1`，**不要**再开第二个 rog A3，**不要**杀现有会话。
- 打分合同（只在 rog，你不要跑）：`--anchor-workers 40 --qps 6 --gemini-max-in-flight 40`。QPS 6 是爬坡，40 是稳态上限。Sub2API 现网 5 个号。本 bot 禁止打分。
- **Top-52 B1**：三卷 tar 已在 Dropbox；**本地 64G chip 已 release**（`RELEASE_OK` 2026-08-26 08:32:42Z，1,208,195 个 `.tif` 已删）。不要在 cursor 再存 Top-52，不要再跑 b1 release。

  | 卷 | Dropbox 字节 | GNU sha256（上传时） |
  |---|---:|---|
  | part01.tar | 17,564,518,400 | `0e012414ec150262f599cb536b8a38dcda1d351119b07ac8801329498eaf9ef3` |
  | part02.tar | 17,550,131,200 | `27a55b7c3a8d26bf66fe919d047c6b4502dc26271a5612d8f49855f7aa02c70d` |
  | part03.tar | 6,480,343,040 | `117044f1de0b6e746cfbfbcabf42b4cb84b66d12a8bc34c800660bd2a4d839f6` |

- cursor 从 rog 发起的 SSH：**Connection refused**。半波由你在 cursor **本地**跑，chip **推**到 rog。

### 仓库与路径

rog：

```text
REPO=/home/gao/projects/solar_backdating
CITYWIDE=/home/gao/zasolar_data/geid_temporal/cape_town_citywide_backdating_v1_20260812
PIPE=$REPO/scripts/temporal/run_ct_citywide_disk_pipeline.sh
PY=/home/gao/projects/ZAsolar/.venv/bin/python
```

cursor 上路径 T0 测完再写进报告。若还没有仓库：从 rog rsync **代码**（不要 rsync chip / Top-52 / tile cache）：

```bash
mkdir -p ~/projects
rsync -a --exclude '.git' gao@100.111.51.77:/home/gao/projects/solar_backdating/ ~/projects/solar_backdating/
# ZAsolar 只需能 import core：至少 rsync core/ 与 configs/datasets/，或整个 ZAsolar 仓库但不含 .venv 也可在 cursor 用现成 venv
```

GEHI 二进制若缺失，从 rog 拷（不要拷 24G tile cache）：

```bash
mkdir -p ~/zasolar_data/tools/GEHistoricalImagery
rsync -a gao@100.111.51.77:/home/gao/zasolar_data/tools/GEHistoricalImagery/GEHistoricalImagery \
  ~/zasolar_data/tools/GEHistoricalImagery/
```

### 任务序列（每步先查 gate）

**T0｜本机盘点 + 到 rog 的 SSH**（不需要 token）

在 cursor 本机执行并写入报告：

```bash
hostname    # 必须是 cursor
df -h /
free -h
which python3 tmux rclone || true
ls ~/zasolar_data/tools/GEHistoricalImagery/GEHistoricalImagery
ssh -o BatchMode=yes -o ConnectTimeout=10 gao@100.111.51.77 hostname
ssh -o BatchMode=yes -o ConnectTimeout=10 koko@100.104.5.27 hostname || echo KOKO_OPTIONAL
```

把 `df` 的 Size/Avail 写进报告。若根盘 **小于 80G 可用**，下载半波前必须先清自己的无关数据，禁止拿 Top-52 或别的城市缓存来填。

巡检日志追加到 rog（你 SSH 过去写）：

```bash
ssh gao@100.111.51.77 'mkdir -p /home/gao/zasolar_data/geid_temporal/cape_town_citywide_backdating_v1_20260812/pipeline/watch'
ssh gao@100.111.51.77 "echo '$(date -u +%Y-%m-%dT%H:%M:%SZ) cursor T0 ...' >> /home/gao/zasolar_data/geid_temporal/cape_town_citywide_backdating_v1_20260812/pipeline/watch/watch_cursor.log"
```

**T1｜软件**（不需要 token）

- 仓库 `~/projects/solar_backdating` 与 rog 同 rev（`git rev-parse HEAD` 两边一致；若 cursor 无 git，rsync 后再比 `sha256sum scripts/temporal/run_a3_batch_lane.py`）。
- Python：优先用 rog 同款 venv。cursor 上 `python3 -c "from scripts.temporal.gehi_region_batch import plan_cluster_tasks"` 要能 import（先 `source scripts/activate_env.sh`，若 `ZASOLAR_ROOT` 不对就 export 到你 rsync 来的 ZAsolar 路径）。
- GEHI 可执行。
- 不装 Sub2API。

**T2｜单次 dump 连通烟**（不需要 token；**禁止**写进 `wave_01`）

独立目录，一个小框即可，跑完删除输出：

```bash
# 能跑通 `GEHistoricalImagery --help` 并且一次 `dump` 对 Cape Town 某点返回 rc=0 即 PASS
# 输出放到 /tmp/a3_cursor_smoke_$$ ，完后 rm -rf
# 403 → 停，报告，不要加大并行、不要改 interval
```

**T3｜读 token**（只读；已签）

```bash
ssh gao@100.111.51.77 'cd /home/gao/projects/solar_backdating && bash scripts/temporal/run_ct_citywide_disk_pipeline.sh gate'
```

E0/E6 必须都是 `2026-08-26`。不是日期 → 停。是日期且 T0/T2 PASS → T4。不要重写 OWNER_DECISIONS。

**T4｜只开 cursor 半波**（gate：E0+E6 双签 + B1 BACKUP_OK + RESTORE_DRILL_OK + T2 PASS）

**不要再切分。** rog 上已有 `wave_01/plan/a3_split/grids_cursor.txt`（45 格）和 `grids_rog.txt`（45 格）。rog 半波 **已经在跑**（tmux `ct_cw_w01_a3_rog`）。你只做 cursor 这一半。

把 grids/candidates/anchors 从 rog rsync 到 cursor（代码也要同一 rev）：

```bash
SPLIT=zasolar_data/geid_temporal/cape_town_citywide_backdating_v1_20260812
ssh gao@100.111.51.77 "cat /home/gao/$SPLIT/wave_01/plan/a3_split/grids_cursor.txt"
# 必须是 45 格，以 CPT0791 开头、CPT1634 结尾。不是 90 格，不是 grids_rog.txt。
```

cursor 本机（tmux `ct_cw_w01_a3_cursor`）：

```bash
export ENABLE_REGION_BATCH=true REQUEST_INTERVAL=2.0
CITYWIDE=$HOME/zasolar_data/geid_temporal/cape_town_citywide_backdating_v1_20260812
GRIDS=$(paste -sd, "$CITYWIDE/wave_01/plan/a3_split/grids_cursor.txt")
# GRIDS 必须含逗号、约 45 个 CPT；禁止空、禁止 90 格
python -u ~/projects/solar_backdating/scripts/temporal/run_a3_batch_lane.py \
  --candidates-csv "$CITYWIDE/ct05_download_v1/plan/waves/wave_01/candidates_2019plus.csv" \
  --anchors-csv "$CITYWIDE/anchors_v1/anchors_all.csv" \
  --output-dir "$CITYWIDE/wave_01/a3_cursor" \
  --provider '' --zoom 0 \
  --grids "$GRIDS" \
  --request-interval 2.0
```

**禁止**：无 `--grids` 的 lane；`launch-wave 1`；再开 `ct_cw_w01_a3_rog`；杀 rog 现有会话；把 cursor 输出写到 `wave_01/a3_rog`。

**T5｜cursor → rog 推 chip**（cursor 的 `stages/qa.done` 之后）

```bash
rsync -a --remove-source-files \
  ~/zasolar_data/geid_temporal/cape_town_citywide_backdating_v1_20260812/wave_01/a3_cursor/chips/ \
  gao@100.111.51.77:/home/gao/zasolar_data/geid_temporal/cape_town_citywide_backdating_v1_20260812/wave_01/a3_cursor/chips/
rsync -a \
  ~/zasolar_data/geid_temporal/cape_town_citywide_backdating_v1_20260812/wave_01/a3_cursor/manifest/ \
  gao@100.111.51.77:/home/gao/zasolar_data/geid_temporal/cape_town_citywide_backdating_v1_20260812/wave_01/a3_cursor/manifest/
```

然后在 rog 上 merge。若编排器还只认 `$wave/a3/`（rog 单机布局），把两边 chip/manifest 收进 `wave_01/a3/` 再 `merge-wave 1`：rog 半波目录 `wave_01/a3_rog`，cursor 的 `wave_01/a3_cursor`，合并到 `wave_01/a3` 后再 merge。合并时 **不要**丢 sidecar。拿不准就停，把目录清单写进报告。

**T6｜巡检**（每 120 min）

1. cursor `df -h /`：Avail < 25G → 停止开新 dump，报告。
2. rog `df -h /home`：Avail < 50G → L0，不启新波。
3. 两边 tmux / `pgrep -af run_a3_batch_lane`。日志 15 min 不动且进程没了 → 用原命令重启（sentinel 可续）。
4. `grep -c 403` 两边 raw.jsonl。持续 403 只记录；4h 零成功才重启一次。
5. 进度：两边 `run/summary.json` 的 `n_crops_ok` 之和 vs 128,718。
6. 一行 UTC 追加到 rog `watch_cursor.log`。

### 不要做的事

- 不要签 E0/E6，不要改 OWNER_DECISIONS。
- 不要再跑 `b1 release`（Top-52 本地 chip 已经删完）。备份在 Dropbox。
- 不要在 cursor 上跑 `score-wave` / Sub2API。
- 不要给 koko 加第二个 GEHI 进程。
- 不要把 wave_01 的 A3 输出写到 `/tmp` 当生产（烟可以，生产必须在上面的 run root）。

### 交班报告要有的字段

写到 rog：
`$CITYWIDE/pipeline/watch/report_cursor_init.md`

至少：hostname、df、free、到 rog 的 SSH、GEHI 路径、T2 dump rc、token 是否存在、是否已开 T4、两边 n_crops_ok、403 计数、cursor Avail。
