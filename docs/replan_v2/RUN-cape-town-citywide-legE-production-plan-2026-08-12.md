# RUN — Leg-E 全城工程/生产落地计划(下载 → Gemini 推理 → 交付 → 核验)

日期:2026-08-12 · Status: **READY(E0 待 owner 签字)**
拆分索引:[`REVIEW-citywide-plan-split-2026-08-12.md`](REVIEW-citywide-plan-split-2026-08-12.md)
上位:[`RUN-cape-town-citywide-backdating-delivery-plan-2026-08-12.md`](RUN-cape-town-citywide-backdating-delivery-plan-2026-08-12.md)(DRAFT-v2 §3/§5)
复审协议:[`MULTIMODEL_VISUAL_REVIEW_PROTOCOL.md`](MULTIMODEL_VISUAL_REVIEW_PROTOCOL.md)

**给执行 agent 的一句话**:把 Top-52 已全通的链路
(catalog→download→chip QA→runtime lock→canary→waves→decode→deliverable→audit)
按本文件的分期扩到 111,801 锚点全城;每期有退出门;所有长任务进 tmux;
交付口径为**内部 release、first-visible-appearance interval 诚实措辞**;
对外发布仍由 Leg-S 验收把门。

## 0. 范围与口径

- 输入:全城 inventory 111,801 seeds / 1,301 非空 grid(canonical 2,083 cell,
  782 零 seed grid 保留零计数);A24 94,891 / A48 16,910;约 44 个 >96 m
  footprint exception。
- 产品语义:first-visible-appearance interval;不声称物理安装日;
  D19 fractional posterior-mass 为主统计通道,禁 midpoint 主通道。
- 复用 Top-52 基线:GEHI 唯一 provider;z19 主 / z18 fallback / z20-21 仅
  review upgrade;cutoff `2025-01-31`,cutoff 后 ≤3 帧 `reference_only=true`;
  `(anchor_id, capture_date)` 去重;失败分状态永不改写为 absent。
- run root 建议:`~/zasolar_data/geid_temporal/cape_town_citywide_backdating_v1_<date>/`。

## E0 — Owner 决议、scope freeze、磁盘方案(P0,阻塞一切)

1. `OWNER_DECISIONS.md` 补记(带日期):
   - 全城 outcome-blind 预取授权(范围/预算/停止条件);
   - **明示覆盖** CT-11 corrected QA 的"全城下载不得启动"政策后果,理由
     =下载 outcome-blind、与准确度声称解耦;
   - 全城 scorer GO 的前置清单(见 E6)与"内部 release"交付口径。
2. scope freeze:`ct_citywide_scope_manifest.csv`(111,801 行,
   `source_feature_id` 双射)+ `ct_citywide_grid_roster.csv` +
   `scope_artifacts.sha256`。
3. **磁盘(硬前置)**:实测 `/home` 余 94 GiB < 全城 chip 上界 110–191 GiB。
   - 第一动作:归档/迁移 Top-52 `ct05_download_v1/` 中 **2009+ superseded**
     chip 层(tracker 已标 superseded;2019+ 层保留复用),预计可回收
     两位数 GiB——先 `du` 实测再动,归档到外置盘并留 sha256 清单;
   - 用 3–5 个异质 grid 实跑,测 **2019+ admitted-only、去 superseded**
     的真实字节/锚点 → 外推 projected peak;禁止在 110/191 里挑小的;
   - projected peak > (可用 − 20%) 时:外置盘挂载或分层(按 wave 下载→
     打分→归档滚动)方案写死后才准开 E3。

**退出门**:决议入档;manifest 双射校验;字节口径实测表 + 磁盘方案。

## E1 — 全城 geometry / anchors / groups

- 复用 `build_ct_anchors_v1.py` / `build_ct_chip_groups_v1.py` 模式扩到
  1,301 grid(region=`cape_town`,EPSG:32734;A24/A48/exception 进 cache key;
  >96 m exception 用版本化 geometry)。
- **退出门**:anchors 行数=111,801;groups 一锚一 96 m chip;spot-check
  几何与 Top-52 重叠锚点逐字节一致(hash 复用生效的前提)。

## E2 — 全城 catalog 波次

- `run_ct05_catalog_probe.py`(plan/probe/merge/remaining)按 grid 分波;
  失败≠空目录,`no_history` 显式分类。
- **退出门**:scope 内每锚点有 catalog outcome;
  `catalog_summary` + coverage 报表;2019+ 候选量级与 ≈6.14M 外推对账。

## E3 — 下载波次 + chip QA(主执行包,保持不断)

- 分片:`plan_ct05_tm3.py` 车道 + `plan_ct52_production_waves.py` 模式预切
  ~32 个 download/score 兼容 wave(≤~3,500 anchors/wave,grid 完整、
  密度/地理交错)→ `wave_plan.json`。
- 执行:`run_ct05_chip_pipeline.py plan` → `run_ct05_download_lane.sh`
  (tmux,每车道一 session,resumable)+ `check_ct05_download_health.sh`
  定期巡检;skip-existing/content-hash 复用 Top-52 已合格 chip。
- QA:每波 `run_ct05_chip_pipeline.py qa`(存在/hash/decode/像素/bbox);
  每波写 `catalog_summary`、`chip_qa_manifest`、`coverage_failures.json`、
  `.done` sentinel。
- **硬 stop**(继承 DRAFT-v2):projected peak > 可用−20% → 停新增,先
  归档/扩容;单波 failure 异常/provider 系统故障 → 暂停 lane 留证据;
  预取进程只写 raw log/catalog/candidate/chip/QA,**不写** verdict/
  scan_state/interval。
- **退出门(imagery ready)**:admitted chips QA 通过率 ≥99%(其余显式
  failure class);no-history/exception roster 冻结;
  `imagery_readiness_report.json` + hashes。

## E4 — 工程 canary(无日期验收)

- 12 grid 量级(密度带+A48+exception+no-history)下载/离线解码空转 +
  Top-52 冻结 verdict 的 offline replay 演练(verdict store `replay-diff`)。
- 指标:吞吐、磁盘增速、失败类分布、hash 稳定。**不设**科学门。

## E5 — Runtime lock + no-history 终态

- `prepare_ct_runtime_lock_v2.py`:冻结 anchors/catalog/chips sha256、
  `no_live_gehi=True`、Lite-only 模型 identity、QPS/workers、动态配额、
  verdict_store 路径。scorer GO 前模型位可留 `UNSET` 模板。
- `initialize_ct_no_history_states.py`:no-history 锚点写显式终态,不调
  Gemini,分母完整。

## E6 — Owner scorer GO(清单化,不是无限期 HOLD)

owner(=用户本人)核对并签进 `OWNER_DECISIONS.md`:

1. E3 imagery ready PASS,磁盘余量 ≥20%;
2. 多模型面板已完成协议 §5 校准(`PANEL_CALIBRATION_LOCK.json` 存在)——
   这是交付核验仪器,不是 accuracy 门;
3. wave plan + runtime lock(模型 identity 落定)+ 配额窗口就绪;
4. 写明本次是 full city 还是 limited canary waves。

**配额算术**(参数化,启动前代入实际 key 数):调用密度按 Top-52 canary
实测 2.56–2.69 HTTP/anchor → 全城 ≈ **287k–301k HTTP**;
日预算 `gross_safe = N_keys × 5000 × 0.80`,扣 canary/retry 得
`work_budget`;90% warning。以单 key 计约 72–75 个配额日 → 多 key/多窗口
并行为默认,窗口切换须 fresh Lite canary。

## E7 — 生产打分(canary → waves)

- 异质 canary 12–24 grid:catalog→chip→scorer→decode→deliverable 全链;
  记录 calls/anchor、429、identity、failure mode;对照 Top-52 基线带
  (2.56–2.69 calls/anchor)而非旧 Codex 门槛。
- Waves:~32 个 immutable wave 与下载分片对齐;每波 manifest 校验、
  fresh identity canary、offline chips only、QPS=6 全局起速、
  `max_in_flight=0`、fail-closed 配额账本(`quota_control.py`)、
  append-only audit、retry 用新 `retry_run_id`。
- 审计减负:Wave-0 与每配额窗口首波跑 `audit_ct52_scoring_run.py` 全量
  递归;其余波 ledger+identity+抽样;异常升级全量。ledger-only gap 必须
  sidecar 明示,不得装成功。

## E8 — 解码、交付、核验

1. **解码**:`infer_install_dates.py`(changepoint decoder + EB/Turnbull
   prior,D19 fractional 通道;epoch gap 用生产锁定值)→
   `install_intervals_all.csv`(111,801 行)。
2. **交付**:`build_ct_install_dated_deliverable.py` → CSV/GPKG:
   latest_absent/earliest_present、MAP+90% HPD、p_undated、coverage class、
   provenance;city 级 posterior year mass + grid summary;全部结构门
   (row_count/bijective/interval_invariant/flight_date_ceiling/
   coverage_reconcile/centroid_agreement)PASS。
3. **核验(协议 §7)**:分层抽 300–500 锚点(密度带 × A24/A48 ×
   date_status × Top-52 内外)→ 多模型面板盲审 → 报
   `panel agreement` 分层表 + 分家族表。**门是结构性的**:无系统性放置
   缺陷、无边界缺帧类缺陷、无 post-cutoff 泄漏;数值一致率如实报告、
   不设硬门(准确度声称归 Leg-S)。
4. **README 措辞**:first-visible-appearance interval /
   multi-model-panel-reviewed QA / 上游 inventory 条件为真;禁
   physical install date、citywide accuracy。
5. 复现性最低配(与 Leg-R 接口):交付时附
   `reproducibility_report.json` ≥ 1 次全量 offline rebuild hash 一致 +
   1 个 wave 双跑等价。

## 执行纪律

- 长任务全部 `tmux new -d -s <name>`,脚本 resumable,进度读日志;
- 改变工程状态的脚本与 RUN 文档同步 git;
- 每期退出门未过不得进下一期;磁盘/配额红线触发即停,留证据。

## 任务表

| ID | 任务 | 阻塞 | 退出门 |
|---|---|---|---|
| E0 | 决议+scope+磁盘 | — | 决议/双射/磁盘方案 |
| E1 | geometry/anchors/groups | E0 | 111,801 双射;重叠锚点 hash 一致 |
| E2 | catalog 波次 | E1 | 全锚点 outcome;候选对账 |
| E3 | download+QA 波次 | E2 | imagery_readiness PASS |
| E4 | 工程 canary | E3 子集 | 吞吐/失败类/hash 报表 |
| E5 | runtime lock + no-history | E1 | lock 冻结;终态写入 |
| E6 | scorer GO | E3,E5,面板校准 | OWNER_DECISIONS 签字 |
| E7 | canary+waves 打分 | E6 | 全锚点 terminal;审计 PASS |
| E8 | decode+deliverable+核验 | E7 | 结构门 PASS;面板核验报表;README 合规 |
