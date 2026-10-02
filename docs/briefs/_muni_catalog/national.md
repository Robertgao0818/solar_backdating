# 国家级 / 省级公开地理图层目录（屋顶光伏安装日面板）

日期：2026-09-06 · 范围：南非全国与省级，**不是**各市自己的航飞合同。  
用途：给 rooftop-PV install-date panel 找市政航片和 GEHI 卫星 vintage 之外的、带真日期的公开层。  
不是 PRD，不下载栅格。Vexcel 已在本仓库影像登记里，本文件不重复。

**一句话：** 全国能当真日期航片用的，几乎只有 CD:NGI 的 0.5 m / 0.25 m 正射；它对小屋顶阵列的分辨力与 GEHI z19 同一量级，但正射、近似天底、按图幅有拍摄年。市政 0.08–0.15 m 才能稳定看见小板。SSEG 公开层目前没有地址级安装日。SANSA/SPOT/Sentinel 分辨率不够。

---

## 0. 读法

每个数据集四件事：它是什么、对安装日有没有用、为什么重要、忽略它会怎样。

有用性三类：

| 类 | 含义 | 对本面板 |
|---|---|---|
| **真日期航片** | 飞机拍的、有拍摄年/日、GSD 足够看见屋顶板 | 可当独立 presence bit / 外部稳健性层 |
| **行政登记** | 并网/许可/出口电量，日期是行政日不是上屋顶日 | 至多作先验或滞后对照，不能当 GT |
| **足迹** | 建筑物轮廓，没有板、没有安装日 | 只做空间框，不能定年 |

核查标记：

- **live-verified 2026-09-06**：本机 HTTP 探过门户、WMS/REST/目录，**没有**下载 GeoTIFF。
- **catalog-only**：官方页或二次文献有，本次请求失败或只读到元数据。
- **假设**：合理外推，正文里写明。

方法：web search + 门户探测。不下载栅格。

---

## 1. CD:NGI — 国家航片与 1:10 000 正射

**名称：** Colour Digital Aerial Imagery 0.5 m GSD（2008–2016）与 0.25 m GSD（2017–当前）；1:10 000 orthophoto maps。  
**发布方：** Chief Directorate: National Geo-spatial Information（CD:NGI），国土改革与农村发展部（DLRRD）国家测绘机构。  
**门户：** https://ngigeoportal.dlrrd.gov.za/cdngiportal/ （live-verified 200）。旧域名 http://www.cdngiportal.co.za/cdngiportal/ 仍 200。宣传站 https://ngi.dlrrd.gov.za/ 对本机返回 **403**，正文规格改从 DALRRD/二次文献与直播 WMS 核对。

### 时间与分辨率

- 模拟航片档案：约 1926/1930s–2008，多种比例尺，全国自 1950s 起有覆盖。正射规格里，2008 年前模拟片正射到 **0.75 m**。对 2010 年代屋顶光伏几乎无用（太早、太粗），只作超长历史底图。
- 数字彩色：2008–2016 全部 **0.5 m GSD**；官方称第一次全国 0.5 m 覆盖完成于 **2012**，第二次 **2016**。
- 2017 起新相机 **0.25 m GSD**，RGB + 近红外 + 全色，12-bit，TIFF JPEG Q=3。2016 已有少量 0.25 m 试验。OSM 社区据 NGI 2024-11 进度图称 **0.25 m 全国覆盖达到 100%**（catalog-only 转述；本次未下载进度 PDF）。
- 1:10 000 正射地图：把上述航片纠正到 1:10 000 图幅，加坐标网、等高线、地名。覆盖「所有大都市与近郊」，官方约 **30% 国土**，不是全国墙到墙的地图产品。像素来自同一套航片，不是更高分辨率。

文件名带年。规范例子：`3318CC_01_2016_953_RGB_RECT.TIF` → 1:10 000 图幅 + 正射号 + **航摄年** + 任务号 + 波段。Esri 南非公开 ImageServer 的目录名与此一致（见下）。

### 覆盖与都市重访

官方目标：**40% 国土每 3 年，其余每 5 年**。都市落在 3 年篮子里是 **假设**（规划 denser 区优先），不要写成合同。

直播 WMS `CDNGI_PORTAL_BACKDROP`（https://bp-cdngiapollo.dlrrd.gov.za/erdas-iws/ogc/wms/CDNGI_PORTAL_BACKDROP，live-verified 2026-09-06）里，0.25 m 镶嵌按省带年：

| 图层 | 对面板的含义 |
|---|---|
| `CDNGI_Imagery_25cm_MOSAIC_Gauteng_2024` | 约翰内斯堡 / 茨瓦内 / 东兰德有一条 2024 全国航片 |
| `CDNGI_Imagery_25cm_MOSAIC_Western_Cape_2021` | 开普敦都会有一条 2021 全国航片 |
| `CDNGI_Imagery_25cm_MOSAIC_KZN_2023` | eThekwini 有一条 2023 全国航片 |
| `CDNGI_Imagery_25cm_MOSAIC_Mpumalanga_2024` | 非本面板核心都市 |
| `CDNGI_Imagery_25cm_MOSAIC_Limpopo_2024` | 同上 |
| `CDNGI_Imagery_25cm_MOSAIC_Free_State_2023` | 同上 |
| `CDNGI_Imagery_25cm_MOSAIC_North_West_2021` | 同上 |
| `CDNGI_Imagery_25cm_MOSAIC_2017` / `_2018` | 0.25 m 早期年份镶嵌 |
| `CDNGI_Imagery_50cm_MOSAIC` | 2008–2016 的 0.5 m 全国镶嵌 |

西开普 1:50 000 图幅索引（见 §3）给 3318（开普敦）填的是 **2016** 与 **2022–2023**，和 WMS 图层名「Western Cape 2021」不完全同一年。图层名是镶嵌发布年，图幅字段才是拍摄窗。**定年用图幅年，不要用 WMS 图层名。**

### 精度

正射水平精度官方 **3 m（95%）**，0.5 m 与 0.25 m 同一条。对 ~15 m² 屋顶板，3 m 位移够把板移出缓冲芯片。当独立 presence 层时必须沿用本仓库的锚点缓冲，而不是像素对齐差分。

### 访问与许可

- **钱：** 《土地测量法》收费公告 Notice 2289 of 2022：数字标准产品（含「Ortho-rectified image, panchromatic or colour, per image, where available」）标价 **R0.00**，只收介质/邮寄。第一小时检索免费，其后按工时。这是 SDI 定价政策下的「免费标准产品」，**不是** CC-BY 许可证。
- **怎么拿：** 门户可浏览 ERDAS APOLLO 目录、Clip-Zip-Ship、WMS。Kartoza 手册路径：`ROOT → IMAGERY → ORTHO_RECTIFIED → WGS84 → MOSAIC → DIGITAL`，`10K_TIF` 为单幅，`MOSAICS` 为全国镶嵌。旧公共 WMS `http://apollo.cdngiportal.co.za/ApolloCatalogWMSPublic/...` 本次 **超时**。硬盘拷贝：OSM 志愿者把盘送到 Mowbray 销售台，NGI 复制数日；https://aerial.openstreetmap.org.za/ 是社区再分发的 XYZ（约翰内斯堡 z15 瓦片 live-verified 200 JPEG）。
- **登录：** 浏览与 WMS 本次未要账号。Clip-Zip-Ship / 大下载通常要邮箱。销售：公告写 `sales@drdlr.gov.za`。
- **许可实践：** OSM 社区长期再分发，自述 NGI 未加限制、视为近似 CC-BY 4.0。这是社区解读，不是 NGI 签字的开放许可。研究使用应署 CD:NGI / DLRRD，批量再分发前问销售台。

### 每幅拍摄年是否可查

**可以，而且不止一条路。**

1. **文件名年**（规范强制）：`{10k}_{nn}_{YYYY}_{job}_RGB_RECT.TIF`。
2. **Esri 南非 ImageServer 目录**（live-verified）：`https://imagery.esri-southafrica.com/server/rest/services/NGI/RSA_NGI_AERIAL/ImageServer`。`Category=1` 主影像 **47,562** 条；样例名 `3028CA_13_2009_173_RGB_RECT`，`LowPS=0.5`。西开普条目把这层写成「2014–2016、马赛克内日期未知」——**与直播目录矛盾**。年在 `Name` 里，可按图幅查询。该服务像素元数据写 0.25 m，但样例 `LowPS=0.5`，应按 0.5 m 的 2008–2016 周期来用，不要当 0.25 m。
3. **西开普图幅索引**（§3）：1:50 000 格网、2008–2025 年度字段，仅西开普。
4. **NGI 门户目录 / Clip-Zip-Ship：** UI 存在（live），本次未登录点进每幅元数据。假设与文件名一致。

没有发现全国统一、带 **日** 的公开日历；公开层停在 **年**，最多到「YYYY to YYYY+1」窗。

### 对 PV 定年的用处

**真日期航片。** 这是全国唯一接近「市政航片替代品」的公开层。

它说的是：某个 1:10 000 图幅在航摄年 *t* 的天底正射里，屋顶上有没有可见光伏。  
为什么重要：GEHI 在都市 CBD 常是航空镶嵌、在郊区是卫星，风格不稳；NGI 是统一的国家航摄计划，年标签可查，0.25 m 与 GEHI z19 标称 GSD 相同但几何更好。  
忽略它会怎样：面板在没有市政航片的都市（或市政只给最近一两年的都市）会只剩 GEHI；2012/2016 的 0.5 m 全国两轮、以及 2017 后按省的 0.25 m，是最便宜的外部 presence 位。

限度：重访 3–5 年，只能切「年」不能切「月」；0.5 m 对 <15 m² 住宅阵勉强；3 m 精度禁止像素差分。

---

## 2. SANSA / SAEOSS / DESA — 国家地球观测

**名称：** SANSA Earth Observation catalogue；South African Earth Observation System of Systems（SAEOSS）；Digital Earth South Africa（DESA）。  
**发布方：** 南非国家航天局（SANSA），科技与创新部。

| 入口 | 2026-09-06 |
|---|---|
| http://catalogue.sansa.org.za/ 与 https 同站 | live-verified 200。搜索要 **Login / Sign Up**，未登录「No results found」 |
| https://saeoss.sansa.org.za/ | live-verified 200。CKAN，**91** 条元数据；`package_search` 可用 |
| https://desa.sansa.org.za/ | live-verified 200。分析就绪数据立方、决策工具、增值产品 |

SAEOSS 检索 `aerial OR SPOT OR imagery` 返回 **29** 包，几乎全是按年的 SPOT 元数据，`isopen=false`，`license_id=null`：

- SPOT 1：1989–1992
- SPOT 4：约 1999–2012
- SPOT 5：约 2007–2015（部分带全国镶嵌）
- SPOT 6/7：2016、2017
- Landsat 9：2023 一条

没有 NGI 级航空正射。没有 2020 年代高分商业星的公开下载包。

### 分辨率与许可（对屋顶板）

- SPOT 1–4：10–20 m 量级；SPOT 5：2.5–5 m 全色 / 10 m 多光谱；SPOT 6/7：**1.5 m** 全色、6 m 多光谱。  
  15 m² 住宅阵在 1.5 m 上大约 6–7 个像素，且是卫星侧视。不能当小屋顶阵列的存在证据。
- DESA 明文：高分商业 ARD **只给南非政府、国企及其承包商**（与供应商合同）；私营/研究走公开档（Landsat 等）或向 `customers-eo@sansa.org.za` 付费。2026-02 SANSA 还在推政府多用户许可证，把高分采购收口到 SANSA。
- SAEOSS 是元数据总线，不是 0.25 m 航片仓库。2024/25 年报写 beta 已上线，与本次 91 条记录相符。

### 对 PV 定年的用处

**不是真日期屋顶航片。** SPOT 年镶嵌最多看到工业屋顶或地面电站。  
忽略 SANSA 不会丢小屋顶安装日；不要为「国家 EO 门户」去申请高分许可证，除非改做公用事业尺度。Landsat/Sentinel 真日期但 10–30 m，见 §6。

---

## 3. 西开普省政府空间数据仓库

**名称：** Western Cape Government Spatial Data Warehouse / GIS portal。  
**发布方：** 西开普省政府（WCG），条目所有者 `WCG_SDW`。  
**入口：** https://gis.westerncape.gov.za/portal/home/ （live-verified 200）。REST：https://gis.westerncape.gov.za/server2/rest/services/SpatialDataWarehouse （v11.5，live-verified）。

这是本次唯一探到、真正把 **NGI 航片 + 都市航片 + 拍摄年索引** 放在一起的**省级**门户。KZN OTP GIS（https://gis.kznpidh.gov.za/home/，200）是行政区/设施仪表盘，首页没有航片仓库。豪登省级航片门户本次 **未 live 到**（catalog-only：GCRO / Gauteng City Region Geospatial Data Platform）。自由州/北开 Spisys 要注册，未探。

### 与 NGI 相关的省级层（真日期航片的索引/再发布）

| 项 | 类型 | 备注 |
|---|---|---|
| **NGI Aerial Photography Image Date** | MapServer | live-verified。1:50 000 格网，字段 `MAPSHEET`、`Image_Date`、以及 `ImageDate2008to2009` … `ImageDate2024to2025`。仅西开普（2628 约翰内斯堡图幅查询 0 条）。3318（开普敦）样例：`Image_Date=2016` 且 `ImageDate2022to2023='2022 to 2023'` |
| NGI Aerial photography ESRI SA 2014–2016 | Image Service | 指向 Esri 南非 `NGI/RSA_NGI_AERIAL`（§1）。门户摘要说日期未知；目录 `Name` 有年 |
| NGI Aerial Photography Western Cape 2021 | WMS | 指回 NGI `CDNGI_PORTAL_BACKDROP` |
| NGI 50cm GSD Imagery Mosaic circa 2016 | WMS | 条目 URL 主机 `cdngiapollo.dalrrd.gov.za` 本次 DNS 失败；同内容在 `bp-cdngiapollo.dlrrd.gov.za` 的 `CDNGI_Imagery_50cm_MOSAIC` |
| NGI 50cm Aerial Photography | WMS | 同上 backdrop |

### 开普敦航片在省级门户上的再发布（城市源，不是省级航飞）

用户点名的条目 **Cape Town Aerial Imagery 2023 January**（item `5a86d91cf3ed4a47abf15b0df85e29a6`）live-verified：Map Service，公开，owner `WCG_SDW`，说明写 **Imagery directly from the City of Cape Town Open Data Portal**，服务 URL 在 `cityimg.capetown.gov.za`。创建 2024-02-05，更新 2026-03-30。同类还有 2022-01、2021-03、2020-01、2019-01、2018-02、2015-02、2013-12、2011-03、2008-06，以及 1998/1996/1988 历史。

**不要把这些算成省级独立航飞。** 它们是 CoCT 城市航片的省级镜像，GSD 走城市合同（开普敦近年约 0.08–0.15 m，假设以城市目录为准），拍摄月在标题里（「2023 January」）。对本面板：西开普门户是发现器，真源仍是城市。

DEA&DP 地图查看器图层清单（2024-10 PDF，catalog-only）还列 Coastal Imagery West Coast 2012 / Overberg 2015 / Garden Route 2013，以及 NGI 2018–2021、2014–2016。海岸条带对屋顶光伏都市样本用处有限。

### 访问与许可

门户公开、无需登录即可读 REST 与 item JSON。许可未在 item 上写 CC；NGI 再发布仍属 NGI，城市航片属 CoCT 开放数据条款。WMS 指向 NGI 主机，下载大栅格仍回 NGI Clip-Zip-Ship 或城市影像服务器。

### 对 PV 定年的用处

**真日期航片的省级索引 + 城市航片发现器。**  
西开普图幅年索引让「开普敦这张 1:50 000 格在 NGI 哪一年拍过」变成可查询，不必先下 0.25 m 镶嵌。忽略它：在西开普仍能用 NGI 全国 WMS，但会失去按格网的年历，CT 的 NGI 位会从「2021 镶嵌」误读成单一日期。

---

## 4. 市政 SSEG / 分布式电源公开登记

SSEG = Small-Scale Embedded Generation（小规模嵌入式发电，市政电网对屋顶光伏/电池的并网登记）。

**没有全国地址级登记。** https://www.sseg.org.za/ 是 AMEU–SALGA 文件库（live-verified 200），不是安装点图层。https://apply.sseg.org.za/ 是 <1000 kVA 的**申请**门户（live-verified 200）；市政名单页为前端渲染，本次 HTML 抽不到表。搜索索引列出 NMB、Ekurhuleni、Buffalo City 及一批地方市；**开普敦、约翰内斯堡、茨瓦内、eThekwini 不走这个全国表**，各有通道。

NERSA 许可证/登记针对较大电源；屋顶 SSEG 多按《电力管制法》Schedule 2 豁免。DFFE REEA 是公用事业 EIA 点，不是屋顶。

本仓库已有结论（`docs/install_date_optimization_replan_2026-07-03.md`）：SSEG `date_commissioned` 只作未来 CT 跑的**聚合先验**，滞后有偏；JHB 准确率不用 SSEG。下列 live 核查没有推翻这一点。

### 开普敦

- **申请：** e-Services Energy Services，https://www.capetown.gov.za/ApplyforSSEG 。2024-03 上线；官方称标准住宅常当天 PTI。2025-05 内部材料：12,819 份并网申请已批（catalog-only 转述）。
- **公开数据（live-verified ODP）：**
  - **Customer Solar Energy Exports 2014 to 2023**：CSV，「按电价类别的年出口 kWh」。**不是地址、不是系统、不是委托日。**
  - **SmartFacility Solar**：市属建筑 AMI。不是居民屋顶登记。
- **没有**地址级 SSEG 图层、没有 `date_commissioned` 下载。城市自己用航片找未授权系统（官方 SSEG 页原文），说明内部空间库存在，但未开放。
- 完整性：只覆盖市电供应区（Eskom 直供街区走 Eskom）；2023-10 后新系统一律按并网处理；未登记量未知。GreenCape 2024-06：约 6,400 套已授权并网 vs 航空普查会看到的「屋顶上有板」。委托日晚于上屋顶日（要 PTI、施工、CoC、commissioning letter）。

### 约翰内斯堡 / City Power

- 申请长期邮件/表格；2026-04 SAPVIA 仍在处理 2022–2025-08 积压。citypower.co.za live-verified 200；深页超时。
- **没有**公开地址级登记或仪表盘。City Power 与 Eskom 供应区并存。登记常伴随预付费改后付费，激励不足，登记率偏低是 **假设**（与积压、收费结构一致，无公开分母）。

### 茨瓦内

- 市电部门有 SSEG 流程；https://www.tshwane.gov.za/ 200。e-GIS（https://e-gis002.tshwane.gov.za/E_GIS_Web/，200）是地块/街道查询，未声称 SSEG 层。
- **没有**公开地址级登记。

### eThekwini

- 申请：https://ee-services.durban.gov.za/onlineservices/pages/eg/ live-verified 200。行业报道称 2026 初新门户。
- **没有**公开地址级登记。

### 纳尔逊·曼德拉湾

- 出现在 apply.sseg.org.za 参与市名单（catalog-only 转述 + 门户 live）。市政官网 200。行业材料称流程相对快。
- **没有**发现地址级公开 dump。

### Eskom Direct

- 宣传页 https://www.eskom.co.za/distribution/small-scale-embedded-generators/ live-verified 200。selfservice.eskom.co.za 本次 DNS 失败。约 450 万直供客户，与市政互斥。
- **没有**公开安装点图层。

### 对 PV 定年的用处

**行政登记，而且公开层几乎只有开普敦的年度出口合计。**  
它说的是：谁向市政申请了并网、哪年出口了多少电——不是哪天板上了屋顶。  
为什么重要：CT 的委托日分布仍可当 ISSUE-03 式年先验；出口时间序列可对「普查看见的板」做滞后上界。  
忽略它：JHB 本来就不该用 SSEG 当准确率；CT 会少一条弱先验，但不影响 GEHI/航片主链。  
**不要**把申请门户当成可爬的登记簿（登录、PII、不是研究协议）。

---

## 5. 建筑物足迹（微软 / OSM / Google / Overture）

足迹没有光伏、没有安装日。用处是给锚点一个屋顶多边形，或与普查多边形做空间对照。不能定年。

### Microsoft Global ML Building Footprints

- **发布方：** Bing Maps / Microsoft。https://github.com/microsoft/GlobalMLBuildingFootprints  
- **时间：** 源影像 2014–2024（Maxar、Airbus、IGN 等）。`dataset-links.csv` live-verified 2026-09-06：`Location=SouthAfrica` **320** 个 quadkey，`UploadDate=2026-02-23`，对象键含 `2026-02-03`。2024-08-27 更新曾单独写南非约 150 万条高度估计。
- **分辨率：** 多边形来自 Bing 高分；不是航片。
- **覆盖：** 全国，不只都市。
- **访问 / 许可：** 公开下载，GitHub README **CDLA Permissive 2.0**（live-verified）。Planetary Computer 页仍写 ODbL——以 GitHub 为准，引用时核对。
- **核查：** live-verified 索引；未下载 csv.gz。
- **用处：** 足迹。可当缺省屋顶框。混了多年影像，不能当「该年已有建筑」更不能当「该年已有板」。

### OpenStreetMap buildings

- **发布方：** OSM 贡献者。Geofabrik `africa/south-africa` live-verified 200。
- **时间：** `start_date` / `building:year` 极少。都市建筑轮廓在 CT/JHB 较密，乡镇稀。
- **许可：** ODbL。
- **用处：** 足迹。可与微软/Google 做差集看谁漏了棚屋或新房。不能定年。NGI 航片是 OSM 南非描建筑的背景（`source=ngi-aerial`），不要循环用 OSM 建筑去验证 NGI。

### Google Open Buildings v3

- **发布方：** Google Research。https://sites.research.google/open-buildings/ live-verified 200。
- **时间：** 推断 **2023-05**；无逐栋日期。非洲在覆盖内。
- **许可：** CC-BY-4.0。Earth Engine `GOOGLE/Research/open-buildings/v3/polygons`。
- **用处：** 足迹，与微软互补（非洲召回通常更好，**假设**沿用 Google 自己的非洲指标，未在 SA 都市重测）。

### Google Open Buildings 2.5D Temporal

- 2016–2023 **年**层，建筑存在/计数/高度，有效约 **4 m**，从 Sentinel-2 来。https://sites.research.google/gr/open-buildings/temporal live-verified。
- **不能**看见屋顶板。最多看新区建筑出现年，与光伏安装不是一回事。

### Overture Maps buildings

- 合并 OSM + 微软 + Google。月度发布，开放许可。catalog-only（本次未拉 theme）。
- 用处同足迹；`sources` 字段有时能追溯，仍无安装日。

---

## 6. 其他全国真日期影像（不含 Vexcel）

「真日期」这里指元数据里有采集日或年，不是「分辨率够看见板」。

| 数据集 | 发布方 | 时间 | 分辨率 | 覆盖 | 访问 | 核查 | 对小屋顶板 |
|---|---|---|---|---|---|---|---|
| Sentinel-2 L2A | ESA / Copernicus | 2015– 约 5 日 | 10 m（可见/近红外） | 全国 | 公开。https://browser.dataspace.copernicus.eu/ 200 | live 门户；未下景 | **看不见。** 15 m² ≪ 1 像素 |
| Landsat 8/9 | USGS / SANSA 有 2023 元数据 | 2013– / 2021– | 15 m 全色 / 30 m 多光谱 | 全国 | 公开 | SAEOSS 有 2023 Landsat 9 包 | **看不见** |
| SPOT 1–7 年镶嵌 | SANSA | 1989–2017 元数据 | 1.5–20 m | 全国（合同年） | 目录要登录；高分 ARD 限政府 | SAEOSS 29 包 live | 1.5 m 仍不够小板；大工商业屋顶 **假设** 勉强 |
| Planet NICFI | Planet / NICFI | 2015– 月合成 | ~4.8 m | 热带带；南非大部分在 30°S 以北边缘或以外 | 申请 | 本次 planet.com/nicfi 超时 | **看不见** 小板；覆盖还不全 |
| Maxar/Airbus 政府多用户 | SANSA 采购 | 不定期 | 0.3–0.5 m 量级 | 任务区 | 只给政府 | catalog-only | 分辨率够，但不是公开面板层 |
| NGI 模拟航片 1926–2008 | CD:NGI | 有拍摄年 | 变化大，正射常 0.75 m | 全国历史 | 同 NGI 门户/销售 | catalog-only 规格 | 光伏时代之前 |
| DFFE SANLC 土地覆盖 | DFFE / EGIS | 1990、2014、2018、2020、2022 | **20 m** Sentinel-2 | 全国 | https://www.dffe.gov.za/egis 200；egis.environment.gov.za SSL 失败 | 土地覆盖不是影像 | 73 类里没有屋顶光伏 |
| DFFE REEA | DFFE | 季报 | 点 | 全国公用事业 EIA | 同 EGIS | catalog-only | 地面电站，不是屋顶 |

**结论：** 公开、全国、真日期、且 GSD 配得上住宅屋顶的，仍然只有 NGI 数字航片。卫星公开档解决不了小阵。

---

## 7. 比较：谁能看见小屋顶阵列

本仓库已量过：GEHI z19 ≈ **0.25 m**，`<15 m²` ≈ **10 个原生像素**；小阵是色块题不是纹理题，高对比、无同尺度干扰时 10 px 可以够，不能整层弃权。下面用同一把尺子。

取一块 **15 m²**、短边约 **2 m** 的住宅阵（约 6–8 块板）：

| 源 | 标称 GSD | 15 m² 像素数 | 2 m 短边 | 几何 / 压缩 | 小阵判读 | 定年粒度 |
|---|---|---|---|---|---|---|
| **市政航片** 0.08–0.15 m | 8–15 cm | 约 670–2,300 | 13–25 px | 天底正射、都市合同、常有拍摄日/月 | **能看见板条/阵列形状**；小阵主证据 | 常年更或两年；标题到月 |
| **NGI 0.25 m**（2017–） | 25 cm | 240 | 8 px | 天底正射；水平 3 m（95%）；JPEG Q=3 | **高对比时能当色块**；与 GEHI z19 同量级但几何更稳；单板条纹弱 | **年**（3–5 年重访） |
| **GEHI z19** | ≈25 cm | 240 | 8 px | 地球时间机器：CBD 常航空镶嵌、郊区卫星、侧视、缩略压缩、配准噪声 | 本仓库生产路径；小阵召回已知吃力 | 不规则 vintage，有时一年多景，日期到日 |
| **NGI 0.5 m**（2008–2016） | 50 cm | 60 | 4 px | 同正射计划 | **住宅小阵勉强**；工商业屋顶更稳 | 全国两轮：约 2012、2016 |
| GEHI z20 | ≈12 cm | ~1,000 | ~17 px | 仅当该 vintage 芯片完整 | 接近市政；不是全国墙到墙 | 同 GEHI |
| SPOT 6/7 | 1.5 m | ~7 | 1.3 px | 卫星 | 看不见小板 | 年镶嵌 |
| Sentinel-2 | 10 m | 0.15 | 0.2 px | 卫星 | 看不见 | 真日期无意义 |

**哪一层「真的能看见」：**

- **要看清小板（住宅 <15 m²）：** 市政 0.08–0.15 m 是唯一舒服的公开几何。NGI 0.25 m 与 GEHI z19 都在色块门槛上，可以做 presence bit，不要指望数板。
- **NGI 0.25 m vs GEHI z19：** 标称 GSD 相同。NGI 赢在天底、正射、图幅年可查、都市按省镶嵌已在 WMS 上（Gauteng 2024、WC 2021、KZN 2023）。GEHI 赢在同一锚点上 **更密的时间样本** 和到日的日期。NGI 替代不了 GEHI 时间链；GEHI 替代不了 NGI 作为独立、可引用的国家航摄年。
- **忽略市政高分辨率：** 小阵会掉回 10 px 色块，假阴性升。  
  **忽略 NGI：** 在没有市政航片的都市，失去 2012/2016/2017–2024 的独立年位。  
  **把 NGI 0.25 m 当成市政 8 cm：** 会高估小板可分性。

建议用法（不是本文件的授权）：市政航片做「最近一景高分辨率真值/夹击」；NGI 0.25/0.5 m 做「全国一致的稀疏真日期夹击」；GEHI 继续做密时间链；SSEG 只在 CT 做聚合先验。

---

## 8. 对安装日面板怎么用

1. **立刻可查询、不用下栅格：** 西开普 NGI 图幅年 REST；Esri `RSA_NGI_AERIAL` 目录 `Name` 年；NGI WMS 图层名里的省-年镶嵌。这三样已经够排一条「每都市有哪些 NGI 年」的表。
2. **值得当外部稳健性层：** Gauteng 2024 0.25 m、KZN 2023 0.25 m、WC 2021/2022–23 0.25 m，加上 0.5 m 的 2012/2016 全国轮。与 CoJ 2019/2023 市政航片不冲突：一个是市合同，一个是国家计划。
3. **不要做：** 用 SANSA/SPOT/Sentinel 给住宅屋顶定年；爬 SSEG 申请门户当 GT；用微软/OSM 足迹当安装年；把 WMS 镶嵌年当成每个屋顶的拍摄日。
4. **许可：** NGI 数字正射官方免费但不是 CC；研究署名 CD:NGI。批量硬盘/Clip-Zip-Ship 走销售台。OSM 再分发 XYZ 可预览，生产芯片应用官方正射以免再分发压缩。

---

## 9. 本次 live 探测摘要（2026-09-06）

| URL | 结果 |
|---|---|
| https://ngigeoportal.dlrrd.gov.za/cdngiportal/ | 200，ERDAS 门户 |
| https://ngi.dlrrd.gov.za/ | 403 |
| https://bp-cdngiapollo.dlrrd.gov.za/.../CDNGI_PORTAL_BACKDROP GetCapabilities | 200，含各省 25 cm 年镶嵌 |
| https://imagery.esri-southafrica.com/.../NGI/RSA_NGI_AERIAL/ImageServer | 200，47,562 主影像，文件名含年，LowPS=0.5 |
| https://aerial.openstreetmap.org.za/layer/ngi-aerial/{z}/{x}/{y}.jpg | 约翰内斯堡 z15 200 JPEG |
| http(s)://catalogue.sansa.org.za/ | 200，搜索需登录 |
| https://saeoss.sansa.org.za/ | 200，91 包，SPOT 为主 |
| https://desa.sansa.org.za/ | 200 |
| https://gis.westerncape.gov.za/portal/... 与 Image Date MapServer | 200，CT 图幅 2016 + 2022–2023 |
| WCG item Cape Town Aerial Imagery 2023 January | 200，源=CoCT |
| https://odp-cctegis.opendata.arcgis.com solar/SSEG | 出口 kWh 合计 + 市属设施；无地址登记 |
| https://apply.sseg.org.za/ 、https://www.sseg.org.za/ | 200，申请/文件，不是点层 |
| Microsoft dataset-links.csv | 200，SouthAfrica 320 quadkeys，2026-02-23 |
| https://sites.research.google/open-buildings/ | 200 |
| https://download.geofabrik.de/africa/south-africa.html | 200 |
| https://egis.environment.gov.za/ | SSL 失败；https://www.dffe.gov.za/egis 200 |
| 旧 apollo.cdngiportal.co.za 公共 WMS | 超时 |
| Planet NICFI 首页 | 超时 |

未核实（假设/catalog-only）：NGI 0.25 m「100% 全国」进度图原文；都市是否都落在 3 年篮子；市政 8–15 cm 的逐市 GSD（留给各市目录）；apply.sseg.org.za 完整市政表（前端渲染）。
