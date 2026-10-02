# BRIEF — 能不能用市级真日期航测另构 install-date panel

日期：2026-09-06 · 用途：合作者说「目前精度还不够」，要各市可用图层
metadata，再决定有没有别的办法建 panel。这是决策备忘录，不是 PRD。

合作者简报（HTML）：[`BRIEF-municipal-true-date-panel-2026-09-06.html`](BRIEF-municipal-true-date-panel-2026-09-06.html)

两套 CSV（航测 / 卫星）：
[`_muni_catalog/aerial_city_summary.csv`](_muni_catalog/aerial_city_summary.csv)
· [`_muni_catalog/aerial_layers.csv`](_muni_catalog/aerial_layers.csv)
· [`_muni_catalog/satellite_layers.csv`](_muni_catalog/satellite_layers.csv)

上游目录：[`SRC-municipal-layers-2026-09-06.md`](SRC-municipal-layers-2026-09-06.md)
· 卫星半边 [`SRC-satellite-layers-h3r7-2026-09-06.md`](SRC-satellite-layers-h3r7-2026-09-06.md)
· Vexcel 半边 [`SRC-vexcel-layers-h3r7-2026-09-06.md`](SRC-vexcel-layers-h3r7-2026-09-06.md)
（开普敦 **有** 五块 2024 年 6.2 cm 瓦，metro H3-7 覆盖 60.9%；对照 GHSL 2020 城区 93%。不是五年。约翰内斯堡城区只盖 27%）
· 分层笔记 [`_muni_catalog/`](_muni_catalog/)
精度现状：[`../replan_v2/RUN-cape-town-ct11-human-calibration-2026-08-04.md`](../replan_v2/RUN-cape-town-ct11-human-calibration-2026-08-04.md)
（CT-11 100 锚重复状态一致 68%；recovery 精确区间 37.5%）

**GSD**（ground sample distance，地面采样距离）= 一个像素对应地面几米。
**True-date aerial** = 市政航测，一层对应一次（或一季）真实飞行，不是
GEHI / Vexcel 那种多年拼贴。
**Year-level panel** = 每个屋顶每年（或每 2–3 年）一个有/无，不是精确到日
的安装区间。

**一句话：** 开普敦、约翰内斯堡、德班的市政航测栈是活的，分辨率比 GEHI
z19 好 2–5 倍；它们能建的是**年精度 panel**，建不成日精度。推荐先在开普
敦用现成检测器打 100 个锚、3 个年份，用这一刀判断这条路是不是真能补上
Gemini 精度缺口。

---

## 1. 目标的三种版本

### V1（最低可接受）——只交目录，不改生产

今天这份 SRC。合作者能看到：哪几个市有真日期层、哪一年、多少厘米、覆盖
全不全、哪个名字是陷阱（CoCT 2023Jan 其实是 NGI 25 cm）。

- **能回答：** 有没有别的公开影像可以当时间维？有。哪几个市值得谈？CT /
  JHB / eThekwini。NGI 25 cm 能不能救命？不能，和 GEHI z19 同一档。
- **回答不了：** 用市政层打分之后，安装年会不会比 Gemini 更准。

### V2（中间）——年精度 panel 的探针，不换全城管线

在开普敦 Top-52 里抽 100 个已有多边形（优先 CT-11 校准包那 100 个，避免
新抽样本），从 CoCT WMS 拉 **2018Feb、2022Jan、2025Jan** 三张 8 cm / 5 cm
芯片，用现成普查检测器（`unified_A`，不是 Gemini）打 present / absent /
low-margin。对照 Gemini 区间做三件事：

1. 已知「2025 普查有板」的屋顶，2018 / 2022 检测器有/无是否单调；
2. 检测器给出的「最后一次无 → 第一次有」年窗，和 Gemini 区间是否相交；
3. 小阵（`<15 m²`）在 8 cm 上是否还大量 low-margin。

约翰内斯堡作为第二城、零新代码：ISSUE-08 已经会拉 2015/2019/2023，只要
再拉 2009/2012，同一套 detector 门。

- **能回答：** 市政 8–15 cm + 普查检测器，能不能给出稳定的**安装年**；
  小阵在真 8 cm 上是不是不再是色块题。
- **回答不了：** 日精度；eThekwini / Ekurhuleni 全城（那里还没有普查多边形）。

### V3（理想）——用市政年栈替换 GEHI 回推

每个普查多边形 × 每个市政年 = 一条有/无，拼成城市级 panel，Gemini/GEHI
降为补洞。开普敦 **12** 个年份（2012, 2013, 2015–2022, 2025, 2026）、约翰内斯堡 5 个 15 cm 年。这是一篇数据论文
的时间维，不是现在的 day-interval 产品。

- **能回答：** 2012 年以来屋顶光伏采纳的年路径（CT），以及 2009 年以来
  的三年一拍路径（JHB）。
- **回答不了：** 「这片板是 2019-03-12 装的」。2023/2024 的 CoCT 层还是
  陷阱或 30% 覆盖，中间会有空洞。

---

## 2. 每种版本的取舍

| | 数据 | 人力 | 精度上限 | 时间 | 放弃什么 |
|---|---|---|---|---|---|
| V1 | 已有 | 本备忘录 | 无新精度 | 已付 | 合作者仍然只有「目录」 |
| V2 | CoCT WMS 抽 300 张芯片（100 锚 × 3 年）；JHB 可复用 fetch | 1 个 tmux 下午 + 人工抽查 ~20 张 | **年**，不是日 | 1–2 天墙钟 | 不修 Gemini、不回切全城表 |
| V3 | 全城 × 12 年，CT 芯片量大约是一次普查的十倍量级（假设，未测） | 新下载车道 + 磁盘 | 年路径，覆盖空洞要写进数据说明 | 数周 | 日精度产品叙事；和 Leg-E Gemini 表并存时要防混口径 |

选 V2 意味着：**不把市政层当成 Gemini 的替代老师去蒸馏**，只当成独立的、
更清晰的观测。选 V3 意味着论文时间维改口，从「区间中位 37 天」改成「年」。

---

## 3. 最小验证实验

**对象：** CT-11 校准包的 100 个锚（已经冻结、有人工门的位置），不要新抽。

**三层：** CoCT `2018Feb`（8 cm）、`2022Jan`（8 cm）、`2025Jan`（5 cm，普查
年，先验应为 present）。

**打分：** 普查 Mask R-CNN + 现成 PV/non-PV 分类器，阈值先移植 ISSUE-08
的 0.30 / 0.95，low-margin 进人工队列。**不要**对这 300 张再打 Gemini。

**预注册门（建议，签字前可改）：**

- (a) 2025Jan：已知普查有板的锚，high-margin present ≥ 0.90（检测器在本
  域不该在普查年大量漏）。
- (b) 时间单调：present@2018 的锚，2022 和 2025 不得 high-margin absent。
- (c) 与 Gemini 年窗：对 Gemini `done_appears` 且两端都落在 2018–2025 内
  的子集，检测器年窗与 Gemini 年相交的比例报告出来——**这一条先当描述
  统计，不当 pass/fail**，因为 Gemini 自己就不稳，相交失败可能是老师错。

**成本：** WMS 300 张小芯片，小时级；检测器本地 CPU/GPU 分钟级；人工抽查
20 张约 1 小时。不耗 Gemini 配额。

**第二城（可选、更便宜）：** 只把 CoJ fetch 的年份列表从 `{2015,2019,2023}`
扩到 `{2009,2012,2015,2019,2023}`，在 ISSUE-29 已有锚上跑，不新下载普查。

---

## 4. 止损条件

写成可判定的，不写「效果不好就停」：

1. **2025Jan 门 (a) 失败**（high-margin present < 0.90 于普查有板锚）→
   检测器在市政 5 cm 上并不比 Gemini 更稳，停 V3，目录留着当外部稳健性
   通道，不改产品叙事。
2. **导出被拒或 ToS 卡住**：CoCT 服务写的是 non-commercial；若抽芯片被
   IP 封、或合作者机构不能走 non-commercial，V2 停在「能看不能下」。
3. **2018 与 2022 的 high-margin 比特 < 50 锚**（大量 low-margin）→ 8 cm
   仍然不够给小阵下结论，年 panel 的有效样本太稀，不扩到全城。
4. **发现更多「名字是年、内容是 NGI 混拼」**（已经有 2014_NGI、2023Jan）
   且 2018/2022 也是混拼 → 那两年从探针名单剔除；若可年数 < 4，V3 对 CT
   不成立。

忽略这些止损的后果：会把 NGI 25 cm 或 30% 覆盖的 2024 当成「又一个真年」，
panel 的时间维会掺进和 GEHI 同类的模糊观测，精度故事写不干净。

---

## 5. 推荐 + 理由

**推荐 V2。** 目录已经证明开普敦有一条几乎逐年的 8 cm / 5 cm 栈，这是
GEHI 路径没有的东西；探针用现成检测器、现成 100 锚、三张公开 WMS，成本
低于再开一轮 Gemini。最大的风险假设是：**普查检测器在 2018 年 8 cm 上
对小阵仍然大量 low-margin**——如果成立，市政层只对大阵有用，年 panel
的样本会偏商用屋顶，不能代表全城采纳。

不推荐直接 V3：全城 × 11 年是一次新的 Leg-E，磁盘和 ToS 都还没过门。
不推荐把 NGI 25 cm 或公开 SSEG 表当成第三条路：前者不比 GEHI 更清；ODP
上的「SSEG」是全市 kWh 合计和 839 个市政府屋顶，不是安装日。内部 PTI /
commissioning 登记据 2025 年幻灯片存在、且已进 GIS——那是 **PAIA / 数据
共享请求**，不是今天能下的图层。建筑足迹的 `ACQS_PRD` 只能卡住「这栋楼
哪年才出现」，不能当装机年。

---

## 需要你签的一句话

**是否授权 V2：用 CT-11 的 100 个锚，拉 CoCT 2018 / 2022 / 2025 三层，
普查检测器打分，不打 Gemini，不回切全城表？**
