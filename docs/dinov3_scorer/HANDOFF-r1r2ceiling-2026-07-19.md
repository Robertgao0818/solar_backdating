# HANDOFF — Run3-native 本地线下一波(R1/R2/ceiling),2026-07-19

给下一个 session 的开工 prompt(owner 直接粘贴使用;可按需删改)。
上一 session 交付:PRD §9 三件套 + R0 冻结(owner-confirmed 07-19)。

---

## 粘贴用 prompt

读 docs/dinov3_scorer/PRD-run3-native-local-line-2026-07-19.md 与
docs/dinov3_scorer/DESIGN-phase0-emission-extension-2026-07-19.md,以及
~/zasolar_data/geid_temporal/run3_native_line_2026-07/r0_manifest_v1/MANIFEST_LOCK.json。

背景:R0 已冻结并经我确认(含 chip_overlap 拆分图偏离追认)。TLO schema
(`src/solar_backdating/localization/`)与级联回放骨架已落地(identity 级真实现,
phase_correlation/weak_lock 是合同化 stub)。emission 设计稿仍是 DRAFT——
【owner 在此处填:①设计稿是否照签;②T0 配对消融早退检查点(§5.4)是否批准】。

第 0 步:把工作树里 §9 交付(scripts/temporal/build_run3_native_manifest.py、
replay_localization_cascade.py、src/solar_backdating/localization/、两个新测试
文件、DESIGN 文档、TRACKER 更新)按 repo 惯例 commit(feat(dinov3): …)。

然后开工 PRD §6 的下一波三件,互不阻塞,可并行:

1. **R1 marker-free student 输入**(重工程):从
   `basemap_rebuild_2026-07-13/chips/<anchor>/z19/*.tif`(注意双 CRS:
   Wayback=EPSG:3857 米制 118,810 帧,noversion/TM=EPSG:4326 度制 192,983 帧,
   逐帧 raster_crs 在 chip_provenance/manifest 里)生成与 A24/A48 同视野的
   **无十字线** crop;精确 polygon ROI + 一圈 roof/context 特征的 crop 几何
   定义;特征缓存合同按 PRD §6.4(`chip_sha + crop_geometry + backbone_hash +
   pooling_version` 键,fp16,分片 Parquet,原子完成标记 + 断点续跑)。只做
   crop 生成器 + crop_geometry 版本化 + 抽样 QA sheet,不嵌入(嵌入是 R3)。
   输入以 R0 manifest.parquet 为准(311,195 行,勿绕过它直读盘)。

2. **R2 定位层最小实现**(中等):把回放骨架的两级 stub 变真——
   phase_correlation 级(PV polygon + buffer 屏蔽,有界 translation,
   合同见 stub docstring 与 TLO schema 的 transform_within_bounds 上界)、
   weak_lock 级(复用 scripts/temporal/pilot_learned_match_2026-07-09.py /
   probe_weaklock_2026-07-10.py 的 SP+LightGlue 资产;ISSUE-24 判定 GO 但
   8.7% dark zone,不得无条件强制 warp)。在分层样本(建议 ~2k obs,
   按 chip_arm×面积段×label_v1 分层抽)上回放,产出 TLO JSONL + 净化率 vs
   误杀率表——这是 G3 prereg 阈值的实证输入。预登记口径(PRD §5.1):
   预期清理配准可检出的 corrupt/artifact,**不是** blind bucket 救援,
   报告不许事后换口径。红线:absent 只在 target_localized=true;冲突/超界
   ⇒ abstain;用 effective_label/verdict_token,不许内联映射。

3. **RUN3-native repeat ceiling 重导**(轻工程+API 消费,quota 已批):
   在 R0 test split(5,403 anchors / 40,329 obs)上抽冻结评测 panel,
   3 次独立 Gemini 重打(PRD §7:约数千至 1.4 万 obs,半小时级 quota),
   算 RUN3-native teacher ceiling + spread(替换 0.7724 旧数)。复用
   run3_v2 的打分管线与 scoring_provenance 记录格式;tmux 起长任务,
   可断点续跑;панel 抽样与 seed 写入冻结文件。若 T0 已获批,panel 设计
   要同时满足 T0 配对消融的需要(同一批 reps 两用,见设计稿 §5.3)。

若①设计稿已签:第 4 件并行——按设计稿 §6 接口清单实现 emission 扩展
(FrameEmission/frame_loglik/pool_epoch_frame_emissions/training_targets.py/
losses.py + §6.3 全部测试点;现状分支字节不变,banked-panel 回归门禁)。
未签则跳过,不许抢跑。

编排要求(沿用上一 session 的有效模式):
- 每件一个 teammate 并行:重工程/严对账用 Opus,中等工程用 Sonnet,
  深度数学/设计思考用 /codex(gpt-5.6-sol);teammate 永不用 Fable。
  任务书里写死:输入路径、验收数字、红线、"不符即停不许凑数"、不 commit
  (commit 由 team-lead 统一)。
- 文件面互不交叠地拆分;并行产物间的接口(如 R2 的 TLO 输出 × R1 的 crop
  几何 × ceiling 的 panel 定义)在交付后指派一方做**只读交叉核对**,
  发现缝隙由你裁决归属后再动手——上一轮此模式抓出 2 个真缝隙。
- 验收纪律:teammate 说"完成/在跑"必须亲验盘面(ps + 产物 + lock 数字);
  idle 通知≠完成;长进程挂后台 waiter 盯 PID;对账数字逐项 actual vs
  expected 复核后才算过。
- 长任务(下载/重打/全量 crop)一律 tmux 可续跑,不绑 agent 会话生命周期;
  数据产物只进 ~/zasolar_data/geid_temporal/run3_native_line_2026-07/,
  仓库只收代码/测试/文档。
