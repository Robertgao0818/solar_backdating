# DATA — South Africa layer-metadata reading pack (aerial / Vexcel / satellite)

| 字段 | 值 |
|---|---|
| 日期 | 2026-09-15 |
| Status | **done**（复核后打成阅读包，不改数字） |
| 上游 | 2026-09-06 市政航测 + Vexcel H3-7；2026-09-13 九城质心 `gehi_info`；2026-09-14 年类 / 空间图 |
| Dropbox | `RA_Solar/Gao/backdating/sa_layer_metadata_2026-09-15/` |
| 本地 | `~/zasolar_data/geid_temporal/sa_layer_metadata_2026-09-15/` |
| 入口 | 包内 `README.md` |

**一句话。** 这是给阅读用的图层 metadata 交付包，不是新探针。2026-09-15 复核：九城主数字与 CSV / HTML / SRC 一致，相关测试 15/15 通过。打开包先读 `README.md`，再打开两份 HTML。

复核通过的口径：

- 航测可用年：开普敦 12、约翰内斯堡 5、eThekwini 5、Ekurhuleni 3、茨瓦内 2、NMB 8；Buffalo / Mangaung / Msunduzi 无市政真日期栈。
- Vexcel：12 个 collection、1,148 个市政相交 H3-7；开普敦 367/603（60.9%）、城区面积 93%；约翰内斯堡 74/350（21.1%）、城区面积 27%。
- 卫星（Vexcel 质心、z19）：TM 中位开普敦 113、约翰内斯堡 90（回到 2009）；TM `sparse_1_2` 正好 9 个城×年，约翰内斯堡 2023 为 32/74。
- 约翰内斯堡城区 264 格里 191 格无 Vexcel。

已知不是错误、但必须跟着走的口径差：

1. 2026-09-06 卫星 CSV 只有开普敦 + 约翰内斯堡，且开普敦中位 **107** 是芯片框完整 `availability`。2026-09-13 九城中位（开普敦 **113**）是 H3-7 质心 `info`。两套都对，查询不同。
2. Vexcel collection 并集曾报 1,226 格；本包卫星样本是「Vexcel ∩ 九市」**1,148** 格。
3. GHSL 城区面积交和 1% H3 规则会差 1 格（开普敦 366 vs 367，约翰内斯堡 73 vs 74）。标题数字用 1% 市政目录。

不是精度声明，也没有新的 live GEHI / Vexcel 拉取。
