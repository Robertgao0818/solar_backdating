# DATA — Cape Town 全城逐次 0/1 序列补进安装日表

| 字段 | 值 |
|---|---|
| 文档 ID | `DATA-cape-town-presence-sequences-2026-09-23` |
| 日期 | 2026-09-23 |
| Status | **SEQUENCE COLUMNS ATTACHED**（研究预览的加列版；仍不是准确度声称） |
| 上位 | [`DATA-cape-town-citywide-gemini-descriptive-2026-09-02.md`](DATA-cape-town-citywide-gemini-descriptive-2026-09-02.md) |
| 产物 | `~/zasolar_data/geid_temporal/cape_town_citywide_backdating_v1_20260812/sequences_20260923/` |
| 生成器 | `scripts/temporal/build_ct_presence_sequences.py` |
| 模式 | `ct_presence_sequence_v1` |

**一句话。** 2026-09-02 那张 111,801 行的安装日表现在有一个加列版：后 27 个波次的 90,348 座安装，每一次历史影像的日期和 0/1/看不清都在同一行里。Top-52 的 21,453 座仍然没有逐次序列。冻结的交付文件和 Dropbox 包都没有改。

## 1. 为什么不改原来的交付文件

2026-09-02 的 `ct_citywide_install_dated_20260902.csv` sha256 是 `c0bf9ed149fb73a97f34354c82f881df3f45769cfafe1e44e9fc68b1e29e7563`，GPKG 是 `30251ca685d879efd4ec3b1d65cef214b68ec3fff2e671a9c372e875474f17dd`。构建前重新核算过，和交付清单一致。新文件是这份表的 37 列原样加上 7 列序列，多边形字节数在写入前后都是 307,055,189（111,801 个）。

**这说什么。** 要逐次序列，打开 `sequences_20260923/`。要和 9 月 2 日已经发出去的包对哈希，仍用 `deliverable_2026-09-02/`。  
**为什么要紧。** 两份文件各管一件事：一份是当时发出去的快照，一份是现在能还原每一次判定的工作表。  
**忽略会怎样。** 直接改 Dropbox 上的那份，对方手里的哈希对不上，而且仍然补不进 Top-52。

## 2. 新列

宽表一行一座安装。`obs_dates` 用分号分开，两个代码列用空格分开，三个字段等长、按位置对齐。日期按时间排序。90,348 份 JSON 里有 59,157 份原来不是时间顺序；长表的 `file_order` 保留 JSON 里的位置。证据日期没有重复。

| 列 | 含义 |
|---|---|
| `sequence_coverage` | `complete` 或 `missing_scan_state` |
| `obs_dates` | 参与安装日的观测日期 |
| `obs_code_raw` | 模型原始判定：`0` 没有，`1` 有，`u` 看不清 |
| `obs_code_repaired` | 写成区间时用的序列。孤立凹陷修补把夹在两次有把握的「有」中间的「没有」改成「有」 |
| `dip_repaired_dates` | 被这样改过的日期 |
| `ref_dates` / `ref_code_raw` | 普查日 2025-01-31 当天及之后，或标成 reference_only 的帧。不在 `n_observations` 里 |

`0` 和 `1` 只计质量为 usable 的帧。`u` 是质量不是 usable，或判定为空。表上的 `n_absent`、`n_present`、`n_unusable`、`n_observations` 对的是 **repaired** 列。

长表 `presence_observations`（CSV 和 GPKG 里各一份）一次观测一行，列是 `source_anchor_id`、`frame_role`（`evidence` 或 `reference`）、`obs_index`、`file_order`、`capture_date`、`code_raw`、`code_repaired`、`quality_flag`、`confidence`、`decision_source`、`dip_repaired`。

## 3. 对账

生成器调用 `infer_install_dates.py` 里同一道普查日过滤和 `apply_dip_repair`。90,348 行全部通过：

- repaired 的 0/1/u 次数 = 表上的 `n_absent` / `n_present` / `n_unusable` / `n_observations`
- `done_appears` 和 `done_installed_during_census` 的 `latest_absent_date` = repaired 序列里最后一次 `0` 的日期
- `done_appears` 和左删失的 `earliest_present_date` = repaired 序列里第一次 `1` 的日期
- 对不上的行 = 0。航飞日期替换（`present_clamped`）在这 90,348 行里出现 0 次

27-wave 的普查上界行是 22,434。这些行的 `earliest_present_date` 是上界，不是某一次影像，所以不在 `obs_dates` 里。这个数和 2026-09-02 分类表里 27-wave 的 census 行数相同。

修补改过判定的安装是 **13,157** 座、**52,212** 个日期。把 `obs_code_raw` 里的 0 加总，这 13,157 座会和表上的 `n_absent` 不一致；和表一致的是 repaired。

例：`ct_full_inventory_2026_06_21_merged_t00000011`

| | 序列 |
|---|---|
| 日期 | 2019-01-15，2020-01-30，2020-09-30，2022-04-17，2023-10-19 |
| raw | `1 u 0 1 0` |
| repaired | `1 u 1 1 0`（2020-09-30 从 0 改为 1） |
| 表上的次数 | 没有 1 次，有 3 次，看不清 1 次 |

**这说什么。** 交付表上的次数和「最后一次没有」可以从 repaired 序列逐行还原；raw 列留下模型当时的判定。序列不保证先全是 0、再全是 1。  
**为什么要紧。** 区间中点盖住了中间那些回跳。要做稳健性或重估安装日，得用逐次列，不能从次数反推。  
**忽略会怎样。** 把 raw 的 0 加起来去对 `n_absent`，13,157 座会对不上，看起来像表坏了。

## 4. 覆盖与仍然没有的部分

| 队列 | 行 | `sequence_coverage` | 证据帧 |
|---|---:|---|---:|
| 后 27 波 | 90,348 | `complete` | 1,291,477 |
| Top-52 | 21,453 | `missing_scan_state` | 0 |
| 参照帧（不进次数） | — | 在 `ref_*` 和长表 `frame_role=reference` | 89,201 |

最长的一条证据序列是 68 次。Top-52 的 `scan_state_path` 仍是 `/tmp/ct52_interval_states.izNPQ6/…`。那些 JSON 不在 `merged_scan_states/`，也不在 2026-09-02 的交付包里。这次没有编造它们的 0/1。

**这说什么。** 删掉本地 `merged_scan_states/` 之后，90,348 座的逐次序列还在这个新目录里。Top-52 的逐次序列现在仍然不在手里。  
**为什么要紧。** 交付表本身还原不出每一次的日期。这个目录补上了有 JSON 的那一部分。  
**忽略会怎样。** 只留 Dropbox 上的 9 月 2 日包，90,348 座的逐次结果仍然只在 JSON 或 92 GB 的整树 tar 里，打开表看不到。

## 5. 文件

目录约 968 MB。`sha256sum -c SHA256SUMS.txt` 已通过。Fiona 读到 111,801 个多边形，EPSG:32734，图层 `ct_solar_install_dated` 和 `presence_observations`。

| 文件 | 大小 | sha256 |
|---|---:|---|
| `ct_citywide_install_dated_20260902_with_sequences.csv` | 84 MB | `6ab3aa733b74b15c367ee907194e01226f464b8142fbd7f40c8a37a322b2995d` |
| `ct_citywide_install_dated_20260902_with_sequences.gpkg` | 705 MB | `c806d9aa60cf9a72582ebbf78a9d24acd0f040f8cb18f0ead6a6cfd9a9b44586` |
| `ct_citywide_presence_observations_20260923.csv` | 180 MB | `bc8d3988100184723d6031ec0cdb4dcfb34f67feb4d61da608b99d065f37b09f` |
| `gates.json` | | `0fcaa49bbfa697079018e2197b3c934d90bd5bb20fbb1798edb3cfaf89a50191` |

单元测试：`tests/temporal/test_build_ct_presence_sequences.py`（6 项，通过）。全量对账是上面的 0 行不一致。

## 6. 锚点×日期面板（同日）

字符串列又转置成了宽表面板。一行一座安装，一列一个证据观测日，格子是 `0` / `1` / `u`。空格子是「这一天没有拍到」，`u` 是「拍到了但看不清」。参照帧不占列。生成器：`scripts/temporal/build_ct_presence_panel.py`。写出后按列序把非空格子拼回去，和 `obs_dates` + 代码列逐行一致。

| 文件 | 大小 | 内容 |
|---|---:|---|
| `ct_citywide_presence_panel_repaired_20260923.csv` | 19 MB | 和安装日次数对齐的面板。101 列，2019-01-15 … 2025-01-30 |
| `ct_citywide_presence_panel_raw_20260923.csv` | 19 MB | 同一批列，格子是模型原始判定 |
| `ct_citywide_presence_panel_date_coverage_20260923.csv` | | 每个日期的拍摄座数和 0/1/u 计数 |

101 列、111,801 行。填了的格子 1,291,477 个，空格子 10,000,424 个。raw 和 repaired 有 52,212 个格子不同，在 13,157 座安装上。Top-52 的 21,453 行在面板里，日期格全空。

日期列很稀疏。2019-01-15 覆盖 27 波的 90,330 / 90,348 座。按一座日期算，拍到的屋顶中位数是 6,262，最少的日期只有 1 座。

**这说什么。** 横着读一行，就是这座安装被拍到的那些天；竖着读一列，是某一天拍到的屋顶里有多少个 0、1、u。  
**为什么要紧。** 空格子和 `0` 不是一回事。大多数日期只覆盖 27 波里的一小部分屋顶。  
**忽略会怎样。** 把空格子填成 0 再做回归或画时间序列，会在没拍过的日子上造出一大片「没有光伏」。

`…t00000011` 在 repaired 面板里只有五个非空格子：2019-01-15=`1`，2020-01-30=`u`，2020-09-30=`1`，2022-04-17=`1`，2023-10-19=`0`。其余 96 列为空。

单元测试：`tests/temporal/test_build_ct_presence_panel.py`（4 项，通过）。

## 7. 合作者交付：只要后 27 波，不做 101 列（2026-09-23）

Top-52 不进这个包。101 列日历表面板留给本地核对；交给合作者的是长表加每座一行的稳健性表。空着的日期不是 0，所以不再横着铺开。

本地：`~/zasolar_data/geid_temporal/cape_town_citywide_backdating_v1_20260812/deliverable_27wave_20260923/`

Dropbox：`RA_Solar/Gao/backdating/ct_citywide_27wave_robustness_2026-09-23/`（rclone `--checksum` 已传完，6 个文件）。

生成器：`scripts/temporal/build_ct_27wave_collaborator_delivery.py`。`u` 不进入可用序列，也不把一串「有」打断。连续指按时间排的可用观测，不是日历上相邻的两天。两次「有」隔了多久看 `k2_run_span_days`。

| 文件 | 大小 | 给谁开 |
|---|---:|---|
| `ct_27wave_install_dated_20260902.csv` | 44 MB | 90,348 行安装日表，Excel 可以开 |
| `ct_27wave_robustness_20260923.csv` | 30 MB | 单调、反转次数、k=1/2/3 确认日。Excel 可以开 |
| `ct_27wave_visits_20260923.csv` | 88 MB | 1,291,477 次证据观测。Stata / R。Excel 行数不够 |

修补后的次数与 2026-09-02 表逐行一致。已定日且修补后单调的行，k=1 的最后一次「没有」和第一次「有」与发表区间一致。

| 检验 | 座数 | 占 90,348 |
|---|---:|---:|
| 原始序列单调 | 76,389 | 84.6% |
| 修补后单调 | 89,517 | 99.1% |
| 只有修补之后才单调 | 13,128 | 14.5% |
| 修补后能凑齐连续 2 个「有」 | 43,234 | 47.8% |
| 修补后能凑齐连续 3 个「有」 | 32,624 | 36.1% |

至少有过一次「有」的是 67,864 座；其中 43,234 座能再找到下一个「有」。已定日 59,428 座在修补后全部单调，其中 35,546 座有第二个连续的「有」。这 35,546 座从第一个「有」到第二个「有」，日历间隔中位 37 天、90% 分位 341 天。

**这说什么。** 合作者用稳健性表就能看出序列是否先 0 后 1，以及把确认标准从 1 张「有」抬到 2 张、3 张时，确认日落到哪一天、隔了多少天。  
**为什么要紧。** 101 列里的空格会被当成 0。长表只留真实影像，k 列把「几个连续 1」直接算好。  
**忽略会怎样。** 用宽表把空格填成 0，单调性会被没拍过的日子弄假；只看修补后的序列，会把 13,128 座本来有回跳的屋顶看成从一开始就是单调的。

## 8. 与约翰内斯堡相同的长表面板（2026-09-23）

规则就是 `build_jhb_run3_install_panel.py` 的 `classify_panel_date`：模型采纳的景标为 `observed`；不晚于最后一次「没装」的未判候选日记为 `implied_absent`；不早于第一次「已装」或普查截止日的记为 `implied_present`；区间内部、没有发布区间、以及左删失之前的更早候选日留空。

只含后 27 波 90,348 座。候选日是 `gehi_vintage_candidates_ct05_run3_2019plus.csv`，另加 22,434 个不在清单里的 `2025-01-31`。`observed` 1,207,026 行，等于可用证据帧数。发布区间与解码后的 0/1 没有冲突。

| 项 | 值 |
|---|---|
| 文件 | `deliverable_27wave_20260923/ct_27wave_install_panel.csv`（421 MB） |
| Dropbox | `RA_Solar/Gao/backdating/presence_panels/cape_town/ct_27wave_install_panel.csv` |
| 行数 | 5,061,495 |
| sha256 | `09611aca054d75a110b3d68e5245925d8efe54b8995b1b6064a99a60ce3761ba` |
| 原始 JSON | `presence_panels/cape_town/scan_states/ct_27wave_scan_states.tar.zst`，90,348 个文件，sha256 `ad80a9e159f45cf061e85e0ad37a1e79119e9dc2c24b242c15d7e0a37153461d` |

`label_source`：`observed` 1,207,026；`implied_absent` 2,987,865；`implied_present` 803,429；`unknown_in_interval` 12,216；`unknown_undated` 45,433；`unknown_before_left_censor` 5,526。

**这说什么。** 合作者拿到的是和约翰内斯堡同一列名的逐日「有没有板」，空白是不能填的日子。  
**为什么要紧。** `installed` 可以直接当某一天有没有板，但 `implied_*` 不是模型又看过那一景。  
**忽略会怎样。** 把 63,175 个空白改成 0，安装区间内部会被写成还没装；把 298 万行 `implied_absent` 当成模型判定，会把「由端点推出来的」说成「模型看过」。
