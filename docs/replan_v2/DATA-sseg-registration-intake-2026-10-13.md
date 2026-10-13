# DATA — SSEG 登记表入库（2026-10-13）

日期：2026-10-13 · 状态：**已入库、已备份**，口径（PLAN §B）尚未冻结，未做任何与模型结果的比对。

开普敦市 SSEG（Small-Scale Embedded Generation，小型自发电并网登记）逐户登记表。合作者经微信发来，文件名与 2026-05-10 kW 标定用过的 `ZAsolar/data/sseg_registration_geo.csv` 相同（那份已不在磁盘上，无法逐字节比对是否同一版本）。

## 位置与校验

| 项 | 值 |
|---|---|
| 本地（唯一工作副本，只读） | `~/zasolar_data/sseg/sseg_registration_geo_2026-10-13.csv`，仓库内经 `data/sseg` 符号链接访问（已 gitignore） |
| Dropbox 备份 | `dropbox:solar_backdating_backups/sseg/`（CSV + `.sha256`），`rclone check` 0 差异 |
| 大小 / 行数 | 12,616,354 字节 · 17,176 行 × 48 列 |
| sha256 | `40c54ab824cb74d5561359b03dcb2f909e3021352ef6de7312d088dc33fa1b14` |
| 来源 | 微信文件 `xwechat_files/.../msg/file/2026-10/sseg_registration_geo.csv`，2026-10-13 22:52 |

## 内容概况（只读统计）

- **状态。** commissioned 两类：`1.0 Approved grid-tied installation commissioned` 5,262 行，`4.0 … commissioned (legalised)` 196 行，合计 **5,458 行**。其余是申请中、获准安装但未验收、离网、备用电源、取消等。PLAN §B.3 "只用 commissioned" 对应的就是这 5,458 行。
- **验收日期 `date_commissioned`。** 格式 `5 Jun 2023`。全表 6,853 行有值，范围 2011-11-26 至 **2024-07-02**，与"2024-07 抽取"一致。按年：2019 年 455、2020 年 859、2021 年 1,157、2022 年 1,287、2023 年 1,541、2024 年上半年 1,167。
- **坐标。** 列 `wkt`（全精度 POINT）和 `latitude/longitude`（只保留 3 位小数，约 100 m，**不要用**）。673 行无坐标；commissioned 中 312 行无坐标。`geo_source`：commissioned 中 `contract_direct` 3,612、`device_recovered` 1,548、`unmatched` 298。
- **其他字段。** 有 `generator_capacity_(va)`（commissioned 中位 7 kVA）、`installation_type`、`customer_type`（住宅 4,834 / 工商 624）、`energy_storage`。**没有** property use code。地块号 `erf_hashed` 是哈希值，不能和市政地块表按编号连接，只能按坐标空间匹配。

## 对 PLAN §B 的含义

- commissioned 且有坐标的约 **5,150 条**是全城的匹配上限。十格（1,114 个锚点）内能匹配上多少还没数，这是 PLAN §B.7 第 2 步。
- `contract_direct` 与 `device_recovered` 两种坐标来源的落点精度可能不同，匹配率应分来源报告。
- PLAN P2 列的"需确认字段"里，property use code 缺失，其余齐全。
