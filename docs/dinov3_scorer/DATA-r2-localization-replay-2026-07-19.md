# DATA — R2 定位层最小实现:级联回放 + 净化率/误杀率实证(2026-07-19)

Status: **DELIVERED**（R2 定位层 phase_correlation/weak_lock 两级由 stub
变真,~2k 分层回放完成,产出 TLO JSONL + 净化率/误杀率表 + 盲评底稿;
**G3 阈值建议见 §6,但不建议直接采信为正式 prereg 阈值** —— 见 §5 的关键
警示发现)

Parent: [`PRD-run3-native-local-line-2026-07-19.md`](PRD-run3-native-local-line-2026-07-19.md)
§5/§6/§9 item 2、§7 G3。前置产物:R0 manifest
(`~/zasolar_data/geid_temporal/run3_native_line_2026-07/r0_manifest_v1/`)、
`TargetLocalizationObservation` schema
(`src/solar_backdating/localization/observation.py`)、级联回放骨架
(`scripts/temporal/replay_localization_cascade.py`,identity 已真实现,
phase_correlation/weak_lock 原为 stub)。

---

## 1. 文件清单

新增/改动文件(全部绝对路径):

- `/home/gao/projects/solar_backdating/scripts/temporal/replay_localization_cascade.py`
  —— 改写:`StageInput` 扩展(chip_arm/area_bin/source_area_m2/label_v1/
  quality_flag/confidence/reference_pool)、`phase_correlation_stage`/
  `weak_lock_stage` 变真实现、新增 `run_full_cascade` 优先级编排器、
  manifest 驱动的分层抽样 + lock 文件、净化率/误杀率表计算、CLI 改为默认
  `--stage cascade` 全级联回放(单级 debug 模式保留);2026-07-19 补强:
  新增 `run_periodicity_diagnostic`/`summarize_periodicity_diagnostic` +
  `--periodicity-diag` CLI 模式(§5.4)。
- `/home/gao/projects/solar_backdating/src/solar_backdating/localization/pv_mask.py`
  (新) —— PV polygon + buffer 像素掩膜(基于 `source_area_m2` 的居中方框
  代理,见 §7 开放问题 1)。
- `/home/gao/projects/solar_backdating/src/solar_backdating/localization/phase_corr.py`
  (新) —— PV 掩膜版相位相关(复用 `chip_displacement` 的纯函数
  `to_gray`/`_sobel_mag`/`_hann2d`/`_psr` 及其校准阈值,只读,未改该文件);
  2026-07-19 补强:新增 `periodicity_score`/`PeriodicityResult`(§5.4 伪峰
  假说定量化诊断)。
- `/home/gao/projects/solar_backdating/src/solar_backdating/localization/weak_lock.py`
  (新) —— PV 掩膜版 SuperPoint+LightGlue,通过 `importlib` 复用
  `pilot_learned_match_2026-07-09.py` 的 `load_matcher`/
  `get_matched_keypoints`/`ransac_translation`(未修改该脚本;仅其
  Vexcel 专用的 `build_ref_cache`/`find_anchor_dir` 未复用,因 §5.2 要求
  同域 GEHI 参考)。
- `/home/gao/projects/solar_backdating/src/solar_backdating/localization/reference.py`
  (新) —— 同域 GEHI"最新可靠 present 帧"参考选择 + self-pair 排除。
- `/home/gao/projects/solar_backdating/src/solar_backdating/localization/geo.py`
  (新) —— metric grid 平移量转投影后 lon/lat 环(`projected_target_polygon`
  用);2026-07-19 补强:新增 `target_roi_ring_lonlat`(R1-对齐的居中方框
  nominal ROI,§9 polygon 底座缝隙修复)。
- `/home/gao/projects/solar_backdating/tests/temporal/test_localization_cascade.py`
  (新,29 项测试:22 项原有 + 7 项 2026-07-19 补强的诊断性周期纹理合成
  测试,§5.4.1)。
- 本文件:
  `/home/gao/projects/solar_backdating/docs/dinov3_scorer/DATA-r2-localization-replay-2026-07-19.md`

未触碰(红线遵守):`build_run3_native_manifest.py`、
`pilot_learned_match_2026-07-09.py`、`probe_weaklock_2026-07-10.py`、
`chip_displacement.py`、`observation.py`(schema 冻结,未改字段语义)。

数据产物(全部在 `~/zasolar_data/geid_temporal/run3_native_line_2026-07/r2_replay_v1/`,未提交仓库):

- `SAMPLE_LOCK.json` —— 抽样 seed/分层计数/行数锁定。
- `observations.jsonl` —— 2,000 行 TLO(§3)。
- `observations.summary.json` —— 级联统计摘要。
- `purification_table.json` / `.csv` —— 净化率/误杀率表(§5)。
- `blind_review_sheet.html` + `blind_review_answer_key.json` —— 30 项盲评
  底稿(§5.3;生成脚本是一次性 scratchpad 脚本,不在仓库内,可按 §5.3 的
  方法说明复现)。
- `periodicity_diag/{periodicity_diag.jsonl, periodicity_diag_summary.json,
  periodicity_diag_stats.json}` —— 2026-07-19 补强:2,000 obs 全量周期性
  诊断 + 分桶统计检验(§5.4)。

## 2. pytest 结果

```
tests/temporal/test_localization_cascade.py ......................  29 passed
tests/temporal/test_target_localization_observation.py ..............  51 passed
```

联跑(`pytest tests/temporal/test_localization_cascade.py
tests/temporal/test_target_localization_observation.py -v`)**80 passed**
(2026-07-19 补强前为 73 passed;新增 7 项 §5.4.1 诊断性周期纹理合成测试,
命名前缀 `test_diagnostic_*`,docstring 标注非回归门禁)。

全量 `pytest tests/temporal/`(916 passed, 10 failed)—— **10 项失败均不在
本次改动范围**:`test_r1_marker_free_crops.py`(r1-crops teammate,9 项)、
`test_run3_native_manifest.py::test_end_to_end_dedup_and_labels`(r0-manifest
teammate,1 项),报错均为 `pyarrow` parquet engine 在**全量套件跑序下**
丢失(`ImportError: Unable to find a usable engine`)。已核实:pyarrow
23.0.1 本身已装;这两个文件单独跑 / 联跑均 **33 passed, 0 failed**,且
`git stash` 排除本次改动后问题依旧存在 —— 是套件内某处的测试间状态污染
(pandas parquet engine 检测被跨文件影响),**与本次改动无关,不在本
teammate 权限范围内修复**,仅记录供 team-lead 知悉。

## 3. 抽样 lock 数字

- **seed = 20260719**(复用 R0 manifest-build 自身的 seed,非另起新值)。
- 分层维度:`chip_arm × area_bin × label_v1`(manifest 既有列),比例分配 +
  最大余数取整,per-stratum 抽样用 `np.random.default_rng(seed).choice`。
- **目标 2,000 → 实际 2,000**(`observations.jsonl` 行数与 lock 完全对账,
  无缺行/凑数)。
- 分层计数(12 个非空 cell —— **发现**:`chip_arm` 与 `area_bin` 在整个
  311,195 行 manifest 里是**确定性 1:1 关系**,A24 只出现在
  `[0,15)`/`[15,40)`,A48 只出现在 `[40,100)`/`[100,inf)`,零例外。这是
  ISSUE-27 自适应 review 几何的预期行为——小目标配 24m review、大目标配
  48m review,不是抽样 bug):

  | chip_arm | area_bin | absent | present | uninformative |
  |---|---|---|---|---|
  | A24 | [0,15) | 817 | 203 | 9 |
  | A24 | [15,40) | 499 | 210 | 5 |
  | A48 | [40,100) | 89 | 60 | 1 |
  | A48 | [100,inf) | 57 | 49 | 1 |

## 4. TLO JSONL + 级联分布

`observations.jsonl`:**2,000 行**,**1,953 个唯一 anchor**(个别 anchor
被抽中 2 帧)。`(cascade_stage, target_localized, abstain, failure_reason)`
精确分布(与 `observations.summary.json` 完全对账):

| cascade_stage | target_localized | abstain | failure_reason | n |
|---|---|---|---|---|
| phase_correlation | True | False | (none) | **1,795** |
| phase_correlation | False | True | transform_out_of_bounds | 103 |
| weak_lock | True | False | (none) | 59 |
| weak_lock | False | True | dark_zone | 18 |
| weak_lock | False | True | transform_conflict | 17 |
| weak_lock | False | True | transform_out_of_bounds | 8 |

汇总:**target_localized 92.7%(1,854/2,000)**、**abstain 7.3%
(146/2,000)**、**升级到 weak_lock 5.1%(102/2,000)**。
`building_found`/`roof_plane_matched` 均为 **2,000/2,000(100%)**——
即本次 2k 抽样里 `roof_plane_not_matched`(低纹理/corrupt 信号)**零命中**,
`_identity_fallback`(无可用参考帧)也**零命中**——每个抽中观测的
`(anchor_id, chip_arm)` 兄弟帧池里都至少有一帧可用于配准(与 manifest
均值 7.52 帧/anchor 一致)。确认锁定的 translation 偏移量分布(1,854 个
localized 观测):p50=0.75m,p90=2.46m,max=4.98m(均在 ±5m 界内,
符合预期——超界的都已被 forced-abstain 分流)。

## 5. 净化率 vs 误杀率表(分层)—— 含关键警示发现

按 `chip_arm × area_bin`,仅统计 `label_v1 == "absent"` 子集(§3.3 的污染
通道):

| chip_arm | area_bin | n_absent | n_gated | gated_rate | 净化(corrupt/artifact) | 疑似误杀(usable & conf≥0.9 的 forced-abstain) |
|---|---|---|---|---|---|---|
| A24 | [0,15) | 817 | 41 | 5.0% | **0** | 39 (95.1%) |
| A24 | [15,40) | 499 | 36 | 7.2% | **0** | 36 (100%) |
| A48 | [40,100) | 89 | 17 | 19.1% | **0** | 17 (100%) |
| A48 | [100,inf) | 57 | 9 | 15.8% | **0** | 9 (100%) |

**净化(`roof_plane_not_matched`,即配准可检出的 corrupt/blank 帧)在全部
103 个被门控的 absent 观测里命中 0 次。** 被门控的观测几乎全部(148/149
个 forced-abstain,跨全部 label,含 absent 外)来自
`transform_out_of_bounds`/`transform_conflict`/`dark_zone`——即"找到了
一个置信的但超界/冲突/暗区的几何信号",而不是"没找到可注册的结构"。

### 5.1 关键警示:抽样目视核查显示 out-of-bounds 很可能主要是相位相关在
重复纹理(排屋网格)上的**周期性 aliasing 锁定**,不是真实大幅错位

抽取盲评底稿中的具体样本核对(见 §5.3):`item_01`
(`t00006832`/2022-10-30,`transform_out_of_bounds`,`dx_m=16.02`)—— 未
校正叠加图显示一栋带规则光伏阵列/屋面网格的建筑,红绿双色边缘清晰但
错位;施加"估计校正"(16m 平移)后,画面**没有变得更对齐**,反而在
边缘出现明显裁切/断层(大幅平移把真实内容移出了公共网格)。`item_07`
(`control_localized`,`dx_m=-4.14`——在界内,被接受)展示的是一整块高度
周期性的联排小屋网格,校正前后视觉上也**看不出明显改善**。这两个例子
都指向同一个结构性风险:**相位相关是全局频域方法,对高度周期性的场景
(联排屋、规则光伏阵列)容易锁定到"错了一整个周期"的伪峰**,PSR 门槛
（≥8.0,借自同域 GEHI 位移审计的校准值)**不足以过滤这种伪峰**——伪峰
本身可以有很高的 PSR(周期性结构会产生锐利但错误的相关峰)。

**这直接削弱了本次净化率数字的可信度**:0% 的"净化"命中 + 高比例的
"疑似误杀"标记,合理的两种解释是 (a) 该语料本身 corrupt/blank 帧确实
罕见(与全量 311,195 行 manifest 里 `corrupt` 列全为 `False` 一致,佐证
上游 basemap_rebuild 管线已过滤掉明显不可读栅格),**以及/或者** (b) 相当
一部分 `transform_out_of_bounds`/`transform_conflict` 本身是配准伪峰,
既不是真腐化也不是真误杀,而是**估计器在这个语料的重复纹理上不可靠**。
本报告**不对这两种解释的相对占比下结论**——这正是盲评底稿存在的目的
(§5.3),需要人工核实。

### 5.2 净化-检出阈值的域适配性

`DEFAULT_MIN_TEXTURE_STD`/`DEFAULT_MIN_PSR` 借自
`chip_displacement.py`,校准语料是 `gehi_displacement_audit_2026-07-06`
在 GSD=0.15m 网格上的同域 GEHI pair;本次 R2 用的是
`basemap_rebuild_2026-07-13` 语料在 GSD=0.3m 网格(`REGISTRATION_GSD_M`)。
两者同为"同域 GEHI"但分辨率/chip 来源不同,阈值未重新校准——净化率
0% 是否是阈值域偏移导致的假阴性,也是待核实项。

### 5.3 盲评底稿(供人工核实,本 teammate 未越权自行裁定)

`blind_review_sheet.html`(30 项:10× `transform_out_of_bounds`、
6× `transform_conflict`/`dark_zone`、14× 匹配对照组 `control_localized`,
盲态乱序,只标注 `chip_arm`/`area_bin`,不标注门控状态/失败原因/偏移量)
+ `blind_review_answer_key.json`(答案表,**不应给评审者看**)。每项含
"未校正叠加图"(红=参考帧、绿=观测帧,无修正)与(除 dark_zone 外)
"估计校正叠加图"(施加级联估计的平移,即便该平移已被判定超界/冲突)。
生成脚本按 §1 所述为一次性 scratchpad 脚本(路径:
`/tmp/claude-1000/.../scratchpad/build_blind_review_sheet.py`,不在仓库
内),复现方法:以相同 `--manifest`/`--seed`/`--sample-size` 重跑
`stratified_sample` 得到与 `observations.jsonl` 逐行对齐的抽样,再按
bucket(`gated_ooB`/`gated_conflict_or_dark`/`control_localized`)分层抽
30 项渲染叠加图。**本 teammate 未执行盲评本身**(需要 team-lead/owner
人工判断),仅交付底稿。

## 5.4 诊断性附录:伪峰假说定量化(team-lead 补强任务,2026-07-19;不改变 §5-§6 任何结论,盲评前不采信为定论)

### 5.4.1 合成复现测试(存在性证明)

新增 `tests/temporal/test_localization_cascade.py::test_diagnostic_*`(7 项,
均标注"诊断性,非回归门禁"):

- **构造**:周期 P=20px 的规则网格结构(模拟联排屋屋顶轮廓——Sobel 提取
  的正是这类"每个周期结构相同、细节噪声独立"的边缘分量),ref/mov 各自
  独立加噪(模拟跨 vintage 拍摄差异),用 `np.roll` 施加已知真实平移。
- **(a) 存在性证明——确认成立**:真实平移 `true_dx ∈ {15, 25, 43}`px(均
  > P/2)时,`estimate_shift_masked` **100% 复现**"锁到错一个周期但通过
  PSR 门槛"的构造:

  | true_dx (px) | 估计 dx (px) | PSR | ok |
  |---|---|---|---|
  | 15 (=P−5) | −5.00 | 12.84 | True |
  | 25 (=P+5) | 4.90 | 14.22 | True |
  | 43 (=2P+3) | 3.00 | 14.78 | True |

  三例的 PSR(12.8–14.8)**均显著高于** `DEFAULT_MIN_PSR=8.0` 门槛,估计误
  差均落在"周期整数倍 ±1px"以内——即一个置信度看起来很高的锁定,实际
  上是错了整整一个周期。对照组(`true_dx ∈ {3,-3,5}`,同一周期结构,
  真实位移 < P/2)全部正确解出(误差 < 0.5px),证明这不是"周期纹理必
  然出错",而是"真实位移超过半个周期时才会出错"。

### 5.4.2 真实帧周期性标记(2,000 obs 全量)

新增 `periodicity_score`(`src/solar_backdating/localization/phase_corr.py`)
——单帧自相关(与 `estimate_shift_masked` 相同的 PV 掩膜/Sobel/Hann 预处理,
但用未做 phase-only 归一化的标准 Wiener-Khinchin 自相关,原因见函数
docstring:自相关的功率谱本身已是实数非负,phase-only 归一化会退化成
恒等冲激,必须用能量加权自相关才能真正反映周期性)。**判别信号是
`alias_psr`(排除零 lag 后次强峰的 PSR),不是 `score`(次峰/零峰幅值比)**
——后者被 Hann 窗本身的衰减主导,任何内容(周期或非周期)都接近,不具
判别力(已在合成数据上验证并写入测试 docstring)。

新增 CLI 模式 `--periodicity-diag`(复用与主回放**完全相同**的
`--manifest`/`--seed=20260719`/`--sample-size=2000`,逐观测调用
`_phase_correlation_core` 拿到与真实回放**同一个** `RegistrationContext`
(同一 `mov_gray`/`mask`),按级联**最终**结果分桶(桶计数与
`observations.summary.json` 完全对账:confident_lock=1,854、
transform_out_of_bounds=111、transform_conflict=17、dark_zone=18)。数据
产物:`r2_replay_v1/periodicity_diag/{periodicity_diag.jsonl,
periodicity_diag_summary.json, periodicity_diag_stats.json}`。

**分位数对比表**(`periodicity_alias_psr`,按最终分桶):

| bucket | n | p10 | p25 | p50 | p75 | p90 | max |
|---|---|---|---|---|---|---|---|
| confident_lock | 1,854 | 4.081 | 4.338 | 4.756 | 5.387 | 6.603 | 25.379 |
| transform_out_of_bounds | 111 | 4.223 | 4.593 | **5.302** | 7.178 | 8.691 | 18.597 |
| transform_conflict | 17 | 4.275 | 4.727 | 5.027 | 5.918 | 7.253 | 10.791 |
| dark_zone | 18 | 3.993 | 4.318 | 5.246 | 5.765 | 7.787 | 9.138 |

**统计检验**(单侧 Mann-Whitney U,`scipy.stats.mannwhitneyu`,vs
confident_lock 为基线;CLES = P(该组随机一例 > confident_lock 随机一例)):

| 对比组 | n | p 值(单侧) | CLES | 自身 alias_psr≥8.0 占比 |
|---|---|---|---|---|
| transform_out_of_bounds | 111 | **6.4×10⁻⁸**(显著) | 0.649 | **12.6%**(14/111) |
| transform_conflict | 17 | 0.047(临界显著) | 0.618 | 5.9%(1/17) |
| dark_zone | 18 | 0.105(不显著) | 0.586 | 5.6%(1/18) |
| 三者合并(all_gated) | 146 | **1.5×10⁻⁸**(显著) | 0.638 | 11.0%(16/146) |
| confident_lock(基线) | 1,854 | — | — | 5.1%(95/1,854) |

全量 2,000 obs 上 `periodicity_alias_psr` 与真实锁定自身 PSR 的 Spearman
相关:**ρ=-0.048(p=0.032,近零、方向甚至反向)**——语料整体上周期性
含量**不是**驱动真实锁定置信度的主因(绝大多数锁定是真实、非周期性
驱动的)。

### 5.4.3 更新判断:**部分支持,但不是主因**(不是"支持"也不是"削弱"的
简单二元结论)

- **有统计显著性、方向正确、效应量真实但适中**:`transform_out_of_bounds`
  组的 `periodicity_alias_psr` 中位数(5.30)显著高于 confident_lock 组
  (4.76,p=6.4×10⁻⁸),CLES=0.649(即随机抽一个 out_of_bounds 案例,
  65% 概率比随机抽一个 confident_lock 案例更"周期性")。这**独立、
  定量地支持**"周期性结构确实是 out_of_bounds 案例里的一个真实致因"
  这一假说,不再只是目视个案的印象。
- **但效应量不足以解释多数案例**:out_of_bounds 组里"自身周期性分数
  就足以独立通过 PSR≥8 门槛"的比例只有 **12.6%**(14/111,相对
  confident_lock 基线 5.1% 约 2.4 倍)。这意味着即便按最宽松的口径(只
  要 `alias_psr≥8` 就算"周期性可以独立解释"),周期性伪峰**最多解释
  一小部分**(个位数至十几个)out_of_bounds 案例,**不能解释 111 个里
  的大多数**。
- **`transform_conflict`/`dark_zone` 的证据更弱**(前者临界显著、后者
  不显著,n 也小,17/18),周期性假说对这两类的支持力度明显弱于
  `transform_out_of_bounds`。
- **结论(供 team-lead 参考,非最终裁定)**:伪峰假说**得到独立定量
  支持,但只是 111 个 out_of_bounds 案例里一个真实存在、统计显著、但
  量级有限(个位数~十几个量级)的贡献因子**,不是这批"疑似误杀"标记
  的主要成因。§5.3 盲评底稿里目视挑出的那几个典型例子(如 16m 偏移那
  个)很可能恰好落在这 12.6% 里;但绝大多数 out_of_bounds 案例背后的
  真实原因**仍待 §5.3 人工盲评核实**(可能是真实大幅错位、参考帧选择
  跨度过大导致的真实内容漂移,或其他估计器噪声)——本诊断**没有把
  §5 的净化率/误杀率原始数字变得可信或不可信**,只是把"目视怀疑"
  换成了一个有边界、可复现的定量判断:**周期性是真实但次要的因子**。

## 6. G3 阈值建议(仅供参考,不写入正式 prereg)

鉴于 §5.1 的警示发现,**不建议**直接把本次"净化率 0% / 误杀率
~95-100%(of gated)"作为 G3 的实证依据来定阈值——这套数字目前更可能
反映"周期性纹理导致的伪峰"而非真实的净化/误杀比例。建议:

1. **先完成 §5.3 盲评**(30 项,~15-30 分钟人工核对量级),把
   `transform_out_of_bounds`/`transform_conflict` 分成"真实几何错位"
   / "伪峰(周期性 aliasing)"/ "不确定"三类,再回填净化率/误杀率的
   真实分母。
2. **在 phase_correlation 里加一道二次峰值一致性检查**(相关面上第二
   强峰与主峰的比值/间距是否呈现周期性 pattern),作为伪峰探测的后续
   R2.1 修正,而不是急于收紧/放宽 `DEFAULT_MAX_TRANSLATION_M`
   (5.0m)——该值是 schema 冻结常量,收紧只是让更多伪峰落入
   "in-bounds 误接受",不解决根因。
3. **若 §5.3 盲评确认伪峰是主因**:G3 的门禁指标应该换成"人工复核后
   的真实净化率 vs 真实误杀率",而不是本表的原始几何门控数字;
   **建议 G3 的显著性阈值先定性(净化率显著>0 且误杀率<净化率的某个
   倍数,如 <2×),量化数值留到伪峰修正后的 R2.1 重跑再定**。
4. **若 §5.3 盲评确认多数确系真实错位**(即语料里确实存在相当比例
   ≥5m 的跨 vintage 内容漂移):当前的 0% 净化率是真实的,提示
   corrupt/blank 帧本来就罕见于此语料,G3 的"净化"表述需要向 team-lead
   请示是否改写为"去除大幅错位/存疑帧"而非"去除 corrupt/artifact"
   ——这会触碰 PRD §5.1 的预登记口径红线,**必须先停下由 team-lead/owner
   裁定,不得由本 teammate 自行改写口径**。

## 7. 未决问题 / 接口缝隙

1. **`projected_target_polygon` 与 R1 crop 几何的坐标约定 —
   **RESOLVED 2026-07-19**(见 §9,发现方 r3-ceiling 只读交叉核对,裁决
   team-lead)**:原实现的底座是 manifest 的 `chip_lon_min/lat_min/
   lon_max/lat_max`(整幅 ~96–106m 原始 chip bbox),经 14 obs 实测比
   R1 的 `roi_px` 每轴大 7.2–38.8×(均值 ~24×),0/14 落入 R1 的
   256×256 crop 画布——是真缝隙,不是坐标系/原点定义分歧(轴向/符号/
   原点与 R1 像素系一致,残差 ≤0.05m)。已修复为与 R1
   (`r1_cropgeo_v1@2026-07-19`)共享的 `roi_edge_m = min(fov_m,
   sqrt(source_area_m2))` 居中方框定义,详见 §9。
2. **PV polygon 代理**:R0 manifest 未携带真实 polygon 顶点(只有
   `source_area_m2` + nominal bbox),`pv_mask.py` 用居中方框
   (`sqrt(area)*1.3/2 + 3m buffer`,封顶 60% 短边)做代理。真实 polygon
   顶点若后续从 census `.gpkg` 补充进 manifest,`pv_mask.py` 的接口
   (`build_centered_mask`)需要换成真实多边形栅格化,不是本次 API 的
   自然扩展点(需要新函数,不是改参数)。
3. **weak_lock 置信阈值未按本域重新校准**:`MIN_WEAK_LOCK_INLIERS=8`/
   `MIN_WEAK_LOCK_INLIER_RATIO=0.4` 是本次选定的保守默认值,ISSUE-24
   自己的校准扫描(`probe_weaklock_2026-07-10.py`)是针对**跨域**
   GEHI↔Vexcel weak-lock 语料,不直接适用于本层的**同域** GEHI↔GEHI
   配对。
4. **`roi_expansion_*` 未实现**:PRD §5.3 称之为"简化版可选",本次
   R2 全程 `roi_expansion_m=0.0`,未做不确定度扩张 ROI 的逻辑。
5. **Metric CRS 用固定 `EPSG:32735` 回退**:`core.grid_utils.get_metric_crs`
   依赖的 task grid gpkg 文件在本 checkout 上不存在
   (`FileNotFoundError: No task grid files found`),与
   `pilot_learned_match_2026-07-09.py` 自己的 try/except 回退行为一致,
   直接固定用 UTM 35S(覆盖约翰内斯堡全域),未按 grid_id 精确选带。

## 8. 复现命令

```bash
source scripts/activate_env.sh
pytest tests/temporal/test_localization_cascade.py tests/temporal/test_target_localization_observation.py -v

python scripts/temporal/replay_localization_cascade.py \
  --sample-size 2000 --seed 20260719 --stage cascade \
  --out-dir ~/zasolar_data/geid_temporal/run3_native_line_2026-07/r2_replay_v1
```

单级 debug:`--stage identity|phase_correlation|weak_lock`(跳过级联优先级
编排,逐观测强制跑指定一级)。全量回放耗时约 3m20s(2,000 观测,
RTX 4070 8GB,102 个升级到 weak_lock 的观测触发 GPU 推理,其余纯 CPU)。

§5.4 周期性诊断(与主回放同一 seed/sample-size,逐观测最终分桶与
`observations.summary.json` 完全对账):

```bash
python scripts/temporal/replay_localization_cascade.py \
  --sample-size 2000 --seed 20260719 --periodicity-diag \
  --out-dir ~/zasolar_data/geid_temporal/run3_native_line_2026-07/r2_replay_v1
```

耗时约 4m38s(同样受 weak_lock 升级子集的 GPU 推理主导)。统计检验
(`periodicity_diag_stats.json`)用一次性 `scipy.stats.mannwhitneyu`/
`spearmanr` 调用生成,未固化进 CLI(纯后处理,输入是
`periodicity_diag.jsonl`,可随时重算)。

## 9. Polygon 底座缝隙修复(2026-07-19,发现方 r3-ceiling 交叉核对,裁决 team-lead)

### 9.1 缝隙

r3-ceiling teammate 只读交叉核对时发现:`_identity_ring`(旧实现,
:157-165)用 manifest 的 `chip_lon_min/lat_min/lon_max/lat_max`(整幅
~96–106m 原始 chip bbox)作 `projected_target_polygon` 的底座。14 obs 实测:
比 R1(`build_r1_marker_free_crops.py` 的 `roi_px`)每轴大 **7.2–38.8×**
(均值 ~24×),**0/14** 落入 R1 的 256×256 crop 画布。TLO schema 里这个
字段的语义是"目标 polygon",原实现对象错误——好消息是轴向/符号/原点与
R1 像素系完全一致(残差 ≤0.05m),不是坐标系分歧,是"用错了底座对象"
这一种更简单的缝隙。

### 9.2 修复(最小面,遵红线:未改 observation.py schema,未 commit)

- **新增** `src/solar_backdating/localization/geo.py::target_roi_ring_lonlat`
  ——与 R1(`r1_cropgeo_v1@2026-07-19`)共享的居中方框定义:
  `roi_edge_m = min(fov_m, sqrt(source_area_m2))`,中心 = 目标质心
  (manifest `centroid_lon`/`centroid_lat`),`fov_m` = manifest
  `review_extent_m`(A24→24 / A48→48),metric CRS 与 R1 一致固定用
  EPSG:32735。这是**canonical home**(team-lead 裁决)——R1 后续可能改为
  引用此函数替代自己的内联计算,本 teammate 未动 R1 文件。
- `StageInput` 新增 `centroid_lon`/`centroid_lat`/`fov_m`(均为
  `float | None`,默认 `None`)。`_identity_ring` 改为:三者都非空时走新
  R1-对齐公式;否则(仅 legacy `--anchors-csv`/`--provenance-jsonl` join
  debug 路径,该路径本就不携带这些 manifest-only 列)回落旧的整幅
  bbox 环,不报错、不静默猜测。
- `load_manifest` 的 required 列集新增 `centroid_lon`/`centroid_lat`/
  `review_extent_m`(均已在 R0 manifest 里,非新依赖)。
- **§5.2 配准画布本轮未改**(按 team-lead 明确指示):`_build_grid_and_mask`
  仍用整幅 chip bbox 构建 phase_correlation/weak_lock 的配准网格——大画布
  用邻建/道路等稳定结构是 §5.2 允许的设计,只有 `projected_target_polygon`
  这个**输出字段**的底座错了。
- **画布尺寸对照轴(可选项)未做**:team-lead 允许"若便宜可做,不强求"
  ——对 111 个 `transform_out_of_bounds` 里挑子样本、换用 FoV(24/48m)画布
  重跑 phase_correlation、看伪峰/超界是否消减,需要新增一条自定义 bbox 的
  配准路径且不能碰本轮"画布不改"的红线,评估后判断非"廉价"操作,
  **登记为待做**,未实现。
- 新增测试(`tests/temporal/test_localization_cascade.py`,4 项):
  `test_identity_ring_r1_aligned_edge_length_bounded_by_fov`、
  `test_identity_ring_r1_aligned_edge_length_clamped_to_fov_for_large_area`、
  `test_identity_ring_r1_aligned_center_matches_centroid`、
  `test_identity_ring_falls_back_to_bbox_when_centroid_or_fov_missing`——
  用与生产路径相同的 UTM 35S 精确反投影核对边长/居中,非近似容差。
  全量 `test_localization_cascade.py` + `test_target_localization_observation.py`
  联跑:**84 passed**(§5.4 交付时 80 passed + 本次 4 项)。

### 9.3 重跑 2,000 obs 对账

用修复后代码重跑(`observations.summary.json` 新增 `generator`/
`polygon_base_version: "r1_cropgeo_v1_aligned@2026-07-19"` 字段标注)。修复
前版本备份在 `r2_replay_v1/_pre_polygon_fix_backup/`(供审计对比,未删除)。
**逐观测字段级对账**(2,000 行,`anchor_id`+`capture_date` 对齐):

- **除 `projected_target_polygon` 外,全部字段逐行完全一致**(`target_localized`
  /`abstain`/`failure_reason`/`transform_type`/`transform_params`/
  `registration_confidence`/`cascade_stage` 等 0 处不一致)——**确认
  gating 决策不依赖 polygon 底座,无隐藏耦合**,不需要停下报告。
- 汇总统计与修复前**逐项相同**:`target_localized=1854`、`abstain=146`
  (`transform_out_of_bounds=111`/`transform_conflict=17`/`dark_zone=18`)、
  `cascade_stage:phase_correlation=1898`/`weak_lock=102`。
- `projected_target_polygon`:1,854 个从 None 变为非 None 的目标定位成功
  观测里,**1,854 个全部变化**(旧底座 vs 新底座,逐一不同,符合预期);
  146 个 forced-abstain 观测两版本都是 `None`(未变化,符合预期——
  abstain 从不产出 polygon)。
- 新底座边长分布(2,000 obs 里 1,854 个非空 polygon,UTM 35S 精确量测):
  p10=2.54m、p50=3.80m、p90=6.66m、**max=48.00m**(精确等于 A48 的 fov
  上限,0 例超界)——相比修复前(几乎全部 ~24–106m)是数量级下降,
  与 R1 的 `roi_edge_m` 定义完全对齐。
