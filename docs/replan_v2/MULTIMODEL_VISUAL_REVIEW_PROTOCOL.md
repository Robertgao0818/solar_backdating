# Multi-model visual review protocol(多模型盲审面板)

Status: DRAFT for owner approval, 2026-08-12
前身:[`CODEX_VISUAL_REVIEW_PROTOCOL.md`](CODEX_VISUAL_REVIEW_PROTOCOL.md)(owner-approved 2026-08-01)
拆分索引:[`REVIEW-citywide-plan-split-2026-08-12.md`](REVIEW-citywide-plan-split-2026-08-12.md)

本协议把 install-date backdating 计划中所有"人工读图/审图"环节替换为
**跨模型家族的结构化盲审面板**。动机(owner 约束 + repo 证据):

1. 项目单人推进,双人独立盲审在日历上不可行;
2. 人工结论无法回灌模型(人与模型的识别 pattern 不一致),校准价值单向;
3. 仪器诊断([`RUN-cape-town-ct11-instrument-diagnosis-2026-08-03.md`](RUN-cape-town-ct11-instrument-diagnosis-2026-08-03.md))
   证明:三档 Gemini 彼此 class 一致 35/40,但与 Codex 拼贴评审的多数一致
   仅 14/39——**分歧发生在模型家族之间**。因此参考仪器必须跨家族,且融合
   必须建模系统性家族偏差,不能简单多数。

## 1. 面板构成

| 席位 | 家族 | 要求 |
|---|---|---|
| P1 | **Gemini 系**(最强可用档,如 `gemini-3.6-flash` 或 pro 档) | **不得**与生产 scorer 同 identity(生产为 `gemini-3.1-flash-lite`);exact returned-model 校验 |
| P2 | **GPT 系**(多模态) | 独立 API 通道;identity 记录 |
| P3 | **Grok 系**(多模态) | 独立 API 通道;identity 记录 |

- 每席位记录 `reviewer_engine`(exact model id)、endpoint、
  `prompt_config_hash`,写入本次 review run 的 runtime lock。
- **相关误差声明**:P1 与生产 scorer 同家族,其与生产的一致性天然偏高;
  跨家族独立性由 P2/P3 承担。任何"面板与生产一致"的报告必须分家族列。
- 席位准入门(预注册,建议值,owner 可收紧):同席位 20% fresh repeat 的
  state 自稳定性 **≥0.80**(Wilson 下界 ≥0.70)。不达标的席位降级为
  仅诊断,不进融合;面板至少保留 **2 个家族** 才可产出参考标签。

## 2. 盲审输入合约(继承 Codex 协议并收紧)

- **禁止拼贴缩略图**。默认输入 = 逐帧多图 part、native 分辨率
  (≈395×314 px 级)、boundary-complete(dated bracket 必含两个生产边界帧
  + flanks)——与 corrected native v2 包同构。
- 冻结、hash-locked、盲:无 anchor ID、无生产 status/interval/confidence、
  无其它席位或历史评审结论。抽样与 assignment 在评审前冻结;见结果后
  不得增删换。
- 缺帧/模糊/树荫/阴影/失焦 **永不**转 absent;只能 uninformative 或
  UNDATABLE。

## 3. verdict 合约

每席位对每条 record 独立返回(schema 与
`evaluate_ct11_human_calibration.py` 的 100 行模板兼容,新增逐帧列):

```text
independent_class ∈ {ALREADY_PRESENT, ALL_ABSENT, TRANSITION, UNDATABLE}
per_frame_state[t] ∈ {present, absent, uninformative}   # 逐帧三态,生产同构
latest_absent, earliest_present                          # 仅 TRANSITION/适用类
review_confidence ∈ {high, medium, low}
rationale(短文本)
```

记录字段沿用 Codex 协议清单(`review_run_id`、`assignment_id`、
`input_manifest_sha256`、`blind_to_production`…),`reviewer_type` 取
`panel_gemini` / `panel_gpt` / `panel_grok`。输出 append-only,不得改写
scan state、verdict store、生产 provenance。

## 4. 通道与融合

1. **Pass-1**:三席位各自独立全量盲审。
2. **Repeat**:每席位 ≥20% fresh 重复(新 `review_run_id`、新随机序),
   报 per-model repeat agreement(分 class 列,汲取 CT-11 教训:aggregate
   72/100 曾掩盖 TRANSITION 2/8)。
3. **融合(参考标签)**:
   - **L0(默认起步)**:类级多数;2:1 时取多数但 `panel_confidence=low`;
     三方全分裂 → `UNDATABLE(panel_split)`。区间端点取多数类内席位的
     并集包络(最晚 latest_absent 之前 ∧ 最早 earliest_present 之后取宽)。
   - **L1(正式参考,预注册后启用)**:Dawid–Skene 型 EM——每席位估
     4×4 类混淆矩阵 Λ_k,item 后验 P(z_i|verdicts) 由 EM 得出;输出
     后验参考标签 + 后验置信 + 每席位混淆矩阵(本身就是 Leg-S S3 分支
     的产出物)。逐帧层同法可得 per-frame 融合三态,供解码器消费。
   - 分歧**不得**用 confidence 挑赢家;后验置信低于预注册阈值的 item
     标 `reference_grade=weak`,不进硬门分母(但要报占比)。
4. **升级读**(可选,预注册触发条件):仅对 panel_split 或 weak item,
   允许一次带增强证据(z20 upgrade chip、双尺度 crop)的定向重读;
   新 `review_run_id`,原 verdict 不覆盖。

## 5. 面板自身的校准(启用前必做,一次性)

复用已冻结的 `ct11_human_calibration_v1_20260804` 盲包(100 锚点,
package SHA `bb11e06f…`,模板/评估器不变,reviewer 换成三席位):

1. 三席位各审 100 + 各 20 repeat;
2. 报:per-model self-stability、两两跨家族 agreement 矩阵、L0/L1 融合
   稳定性(bootstrap);
3. **消融并入**(替代原 CT-CW-01c):同一 40–100 子集跑
   Arm B(多图直接给区间)vs Arm C(逐帧三态 + 生产同构解码)——
   以自复现与跨家族一致选定后续默认 arm(先验预期 C 胜,与生产接口同构);
4. 产出 `PANEL_CALIBRATION_LOCK.json`(席位、prompt hash、arm、融合法、
   自稳定性数字)。此 lock 之后席位/prompt/融合法的任何变更都要新 lock。

## 6. 命名与声称纪律(硬)

| 允许 | 禁止 |
|---|---|
| `multi-model panel agreement` | human accuracy / inter-annotator agreement |
| `panel-reviewed reference set` | human gold truth / independently verified |
| `panel repeat agreement`(稳定性) | physical install-date accuracy |
| `reference–product agreement`(唯一可称 accuracy 的通道,且 reference=面板融合) | citywide accuracy(未经预注册确认集) |

面板参考不是物理真值;它是"当前最优可辩护外部参考"。owner 仍是
release 决策者。

## 7. 本协议在各分支中的角色

| 分支 | 用法 |
|---|---|
| Leg-E 生产 | 交付前抽样核验(分层 300–500),报 panel agreement 分层表;不设硬 accuracy 门,只设"无系统性放置/边界缺陷"结构门 |
| Leg-S 科研 | 替代 CT-CW-01 人工校准 → 三分叉判据;各方法分支的开发参考与 KILL 重开评估;CT-CW-03 re-prereg 的参考仪器 |
| Leg-R 可复现 | 准确度跟踪通道(非门禁):repro-grade 产品 vs 面板参考,只报数不卡关 |

## 8. 与旧协议/旧数字的关系

- Codex 拼贴评审退役为历史诊断通道;其 70/75/80 门槛不继承。
- 旧 KILL 数字(recovery 37.5%、ref-anchored 30%、provenance 52.1%、
  DINO 头 56.3%)相对旧 Codex 参考,面板参考建立后须重评。
- 密封 `ct11_disjoint_holdout_v1_20260804`(500+100)仍禁开,直至
  method lock + 新门槛 re-prereg 完成;开封时的参考仪器即本协议面板。
