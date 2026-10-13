# RUN — 开普敦附录图 v2r2（D2 换成第 2 名格子）

| 字段 | 值 |
|---|---|
| 日期 | 2026-10-14 |
| 状态 | Opus 2026-10-14 **ACCEPT WITH MINOR FIXES**。`release_v2r2/` 原样接受并已上传。F1(a) 改的是本文对 6.2 cm 的解释。F1(b)、O1、O2 留到下一次重画。发布目录没有改 |
| 计划 | [`PLAN-cv-appendix-figures-execution-2026-10-13.md`](PLAN-cv-appendix-figures-execution-2026-10-13.md) §1.1、§3 Phase 2 |
| 负责人决定 | 2026-10-14 08:14 NZDT。D2 用第 2 名格子 `89ad3617603ffff`。六张 D1 芯片不换。M3、M4 随这次重画一起做 |
| 验收依据 | 本包：`~/zasolar_data/geid_temporal/cv_appendix_20261013/acceptance/review_v2r2_opus_20261014T0840.md`。上一包的 M3、M4 在 `review_v2r1_opus_20261014T0322.md` |
| 代码 / 出图 | `c523bcf9b6e8878f2a7603d77c3ca3be6391f432`。清单里的 `code_commit` 和 `render_commit` 都是这一次。上一提交 `c43b8bc` 加上了 `--revision`、图例留白和按这张芯片量出的地面采样距离；这一提交把 “1 parcel” 的单数写进合作者说明，然后才出图 |
| 本地包 | `~/zasolar_data/geid_temporal/cv_appendix_20261013/release_v2r2/` |
| 作图中间文件 | `~/zasolar_data/geid_temporal/cv_appendix_20261013/staging_v2r2/` |
| 不改的目录 | `release_v2/`、`release_v2r1/`、`release_v1r2/`、`release_v3/`、`release_v3r1/`，以及对应的 staging。v2r1 清单的修改时间仍是 `2026-10-14 03:19:02.451095779 +1300`，sha256 仍是 `a2cd342a37c40939b646542f90932585efc3056397ebd48fac0bda2f2d5bcd63`。v3 清单 sha256 仍是 `0841976e3b1824260e23c3b298d2e435556203177611c7dabf83075b4a5c9b36`。v3r1 清单 sha256 仍是 `62a911aab085a59ad7121d98ffa4096acba3fd8d22f8337c9b4da7abc0cb674d` |
| 脚本 | `scripts/validation/build_cv_appendix_v2.py` |

这一批仍是两张图。D2 换成负责人选定的第 2 名格子。D1 仍是 v2r1 已经接受的那六个对象。没有重跑检测，没有调用 Gemini，没有重新向市政 WMS 或 Vexcel 要图。芯片是从 `staging_v2r1/` 按字节复制进来的。

H3-9 是一种六边形地理索引，这一级大约 0.1 平方公里。GSD（地面采样距离）是一个像素在地面上的宽度。SSEG 是市政的小规模嵌入式发电登记。Eskom 在这里指供电机构图层里的多边形，不是把格子从分析里删掉。IoU（交并比）是两个多边形重叠面积除以并集面积。PREVIEW 表示影像许可还没有清，这张图还不能当正式发表图。

## 1. 包里有什么

`sha256sum -c SHA256SUMS.txt` 在 `release_v2r2/` 列出的 10 个文件全部通过。清单 `release_manifest.json` 的 sha256 是 `f3a780b0c005b5f6b5c5691d62131eceba2f2fc983baea9d2778890125ad152d`。它用严格 JSON 写出，没有 `NaN`。`code_commit` 等于 `render_commit`。

D2 的 PNG 是 2125×1605 像素，折合 179.92×135.89 mm；PDF 页面是 510.236×385.2 pt，折合 180.00×135.89 mm。D1 的 PNG 是 2125×1545 像素，折合 179.92×130.81 mm；PDF 是 510.236×370.8 pt，折合 180.00×130.81 mm。计划要求的期刊双栏宽度是 180 mm。PNG 少 0.08 mm 是像素取整。没有用 `bbox_inches='tight'`。

- `figs/fig_D2_example_cell_preview.png` 和 `.pdf`。文件名带 `_preview`。五个小图、地图和整页左上角都有 PREVIEW。
- `figs/fig_D1_failure_gallery.png` 和 `.pdf`。和 `release_v2r1/figs/` 里的同名 PNG 逐像素相同，最大绝对差是 0。
- `data/d2_cell_rank.csv`：2,878 行。其中 306 行因为一块非住宅地块占了新光伏面积的 40% 以上而被标成不采用，剩下 2,572 行。
- `data/d2_cell_points.csv`：45 个模型地块的代表点，列是 `lon,lat,year,source`。45 个点都落在图幅里。没有 SSEG 户，没有哈希账号，没有 `objectid`。WKT 留在中间文件里，没有放进发布包。
- `data/d1_picks.csv`：六张芯片的格子、面积、IoU、`max_iou_other` 和 `candidate_id`。槽名仍是 “FP darker roof object”，没有按可选项 O1 改名。
- `README.md`、`captions.md`、`release_manifest.json`、`SHA256SUMS.txt`。

合作者说明里的年份计数写成了句子，例如 “2020: 2, 2021: 4, 2022: 7, 2023: 10, 2024: 21”，不是 Python 字典。说明里没有仓库命令，也没有 “before this batch is uploaded”。这些命令留在下面第 6 节。

## 2. 输入核对

这些 sha256 与 v1 冻结值和 v2r1 用过的文件相同。组装时脚本核对过，不一致会停。这一批没有重下地块，没有改地块数，也没有重跑检测。

| 输入 | sha256 | 用来做什么 |
|---|---|---|
| v1r2 的 `fig_D3_cells.csv` | `f6ee5a8b6e0ff47bfe040688b119b9e7937ffc9a4071eee10433ac9766d3a07f` | 分析格子，以及每个格子的 Eskom 面积比例、模型存量和 SSEG 户数 |
| `census_links.parquet` | `b165d2f08ad54c69fdbacdda873ca49344fa938a9f41e6b43a9aef60e4a7a566` | 模型地块的首次日期 |
| `parcel_attr.parquet` | `4d024733bd42ab59fc7a1197b72b1191834509352cce8d950c5aea3f6f75bbc8` | 地块属于哪个 H3-9。键是 `parcel_attr.h3_r9`，和 D3 相同 |
| `sseg_links.parquet` | `f59985163fbe05b70c2b4aabdd24b222c9237c370611316432069e8166f2f50d` | 已验收 SSEG 的年份 |
| `parcels.gpkg` | `28597a909e4b2588dd997f41fcdedbc9633eae57e24d13e6140f6c13c398f937` | 地块几何。发布包里不带户级行 |
| 标定 yaml | `98e3324f9f0652b55b3ebd197f6820342978af3683ba51fd846d68f0c29af203` | 哪些 Li 格子进过阈值标定 |
| 16 个留出格子的检测拼接 | `50c580355638555c0f93b27a41394f2c0d2d4ec93225e21bebf0ac031d88a07b` | D1 用的已有检测，没有重跑 |

D2 的四张市政 PNG 和 Vexcel 的 tif，与 `staging_v2r1/d2_chips/` 里同名文件的 sha256 相同。D1 的六张芯片也相同。`staging_v2r1/` 里那份旧的 Vexcel 旁路 JSON 没有被改写，它仍写着 2026-10-13 的复核笔记。新目录里的旁路 JSON 改成了这一张芯片自己的测量。

## 3. D2：第 2 名格子

自动第 1 名仍是 `89ad3601c7bffff`。它有 48 块新光伏地块、124 块住宅地块，Eskom 面积比例是 1.0，已验收 SSEG 是 0。比例 1.0 的意思是：供电图层把这个格子标成 Eskom 供电，市政 SSEG 登记本来就不会出现在这里。如果拿它当例子，柱状图上的 0 会被读成“有板但没登记”，而那更可能只是供电机构不同。这一批不画它。排序规则没有改。脚本在不指定 `--cell` 时，如果第 1 名不再是这个格子，仍会停下来。

负责人选的是第 2 名 `89ad3617603ffff`（Rondebosch）。它有 143 块住宅地块、44 块窗口内新出现的光伏地块。最大的一块新地块占新面积的 6.2%，用地是 Single Residential 1。Eskom 面积比例是 0.000，所以说明里没有那句 Eskom 供电的警告。D3 的中点存量是 45，已验收 SSEG 是 7。44 和 45 差 1，是因为有 1 块在 2019 年或更早已经出现，它画成灰点，不进柱子。

这个格子的意思是：给合作者看一块市政供电、住宅为主、模型新增和 SSEG 新增可以并排看的地面。忽略“第 2 名”这个选择，合作者会继续对着 Eskom 格子理解登记缺口。换格子不改变全市的 D3、D4、D5 数字。那些图在 v1r2 里，这一批不重画。

模型新地块按年是 2020: 2，2021: 4，2022: 7，2023: 10，2024: 21，合计 44，与排序表的 `n_new_parcels` 相同。已验收 SSEG 按年是 2020: 1，2021: 0，2022: 1，2023: 1，2024: 4，合计 7，与 D3 的 SSEG 户数相同。地图上的点是 45。

前三名留在这里，不放进合作者说明：

- `89ad3601c7bffff`：48 块新光伏地块，124 块住宅地块，Eskom 面积比例 1.0。不画。
- `89ad3617603ffff`：44 块新光伏地块，143 块住宅地块，Eskom 面积比例 0。这一张图。
- `89ad361804fffff`：41 块新光伏地块，101 块住宅地块，Eskom 面积比例 0。不画。

面板 C 的最高柱是 2024 年的模型 21。纵轴上限是这个高度的 1.35 倍，也就是 28.35，刻度画到 27。图例放在左上。我看过 PNG：图例没有压住柱子，PREVIEW 水印在，A4 写的是 6.2 cm。2021 年的 SSEG 是 0，所以那一年只有蓝柱，这是计数，不是缺柱。

因为选中的格子就是以前的对照格子，这次指定了 `--cell`，没有再写 `compare_89ad3617603ffff/`。

### 这一张芯片的 Vexcel 像素间距

量的是这张已经在磁盘上的 tif，没有重新提取。文件是 EPSG:3857。EPSG:3857 是 Web 墨卡托，一种网络地图投影。0.0746455354347541 米正好是这种投影第 21 级瓦片在赤道上的像素宽度，也就是 2π × 6378137 / 256 / 2²¹。第 21 级是固定的缩放层级。像素宽度乘以纬度的余弦，得到的是接口交出来的地面像素间距。原生地面采样距离是相机拍摄时一个像素对应的地面宽度。日期接口不返回那个数。

- 集合：`za-wc-rondebosch-2024`
- 首张拍摄：`2024-04-12T11:23:26`。末张：`2024-04-12T11:47:38`
- 投影像素：0.0746455354347541 米，即第 21 级赤道像素
- 芯片中心：经度 18.476869，纬度 −33.979003
- 交付像素间距：0.0746455354347541 × cos(纬度) = 0.061899 米，也就是 6.19 cm
- 图上写成 6.2 cm，因为市政图层用的是一位小数（8 cm、5 cm），6.19 cm 在这一位上是 6.2 cm

6.2 cm 这个数的意思是交付网格有多细。在开普敦这个纬度，任何一张按第 21 级交出来的 Vexcel 芯片都会得到大约 6.19 cm。仓库里其他城市从元数据记下的 Vexcel 原生地面采样距离是 6.9–7.4 cm。同一行里市政的 8 cm 和 5 cm 来自服务说明，是原始分辨率。把 6.2 cm 和它们当成同一种清晰度来比，Vexcel 那一格可能被高估大约 1 cm。没有任何计数受影响，图仍标着 PREVIEW。清单里的偏离仍把这项写成 ground sample distance。下一次重画 D2，或在这张图离开 PREVIEW 之前，要么从集合元数据取出原生地面采样距离，要么把 A4 改成 “6.2 cm pixel”，并在说明里写明这是交付像素间距、原生地面采样距离没有返回。这次不改图，也不改已经上传的说明和清单。如果一直把 6.2 cm 读成相机分辨率，合作者会以为这张正射比 8 cm 的市政航拍更清楚，而交付网格不能单独证明这一点。

## 4. D1：六个对象没换

六张芯片从 `staging_v2r1/d1_chips/` 复制。选择函数没有重跑。槽名、格子、`candidate_id`、面积和 IoU 与 `release_v2r1/data/d1_picks.csv` 一致。`render_commit` 改成了这一次出图的提交，因为组装要求它等于 HEAD。图是用这些同一张芯片、同一组多边形重画的。重画之后的 PNG 与 v2r1 发布图逐像素相同。

| 槽 | 格子 | candidate_id | 面积 m² | IoU | 对另一层的最大 IoU |
|---|---|---:|---:|---:|---:|
| TP residential | L1896 | 422 | 22.3 | 0.91 | 0.91 |
| TP commercial | L1898 | 745 | 720.3 | 0.61 | 0.61 |
| FP small bright | L1899 | 801 | 10.4 | 0.00 | 0.00 |
| FP darker roof object | L1896 | 287 | 13.0 | 0.00 | 0.00 |
| FN small | L1900 | 915 | 11.9 | 0.00 | 0.00 |
| FN larger miss | L1897 | 539 | 27.8 | 0.00 | 0.00 |

这些芯片的意思是：给合作者看六种外观，不是一类的错误率。图注里仍有 “They are not a class-wise error rate.” 这句话是在拒绝把这六张当成错误率。如果把槽名读成检测准确率，这一批的图就被用错了。可选项 O1（把 darker roof object 改成 darker object，以及给第 1 名格子加一句“2020–2022 年在盖房子”）没有做，因为任务书没有要求，而且第 1 名格子已经不画了。

## 5. 测试和没有做的事

`pytest tests/validation/test_cv_appendix_rules.py tests/validation/test_cv_appendix_d6.py` 在出图用的这次提交上通过，29 项。新增的检查是：不带 `--revision` 或指向 v2r1、v3r1 会停；合作者说明里的计数是句子，并且没有仓库命令。被拒绝的密度乘数 `* 0.04` 的扫描现在也包含这个 v2 脚本。纵轴用的 `* 1.35` 不是那个密度。

没有上传。没有群消息。没有改 v1、v3 的已公布数字。没有做另一种 10 米规则（见下一节）。Q3 和 Q4 仍然开着，见第 7 节。

## 6. 留给本仓库的命令

这些命令不放进合作者说明。`--revision v2r2` 写到 `staging_v2r2/` 和 `release_v2r2/`。不带这个参数会停，避免再写入已经接受的 v2r1。`--d1-source v2r1` 只读 v2r1 的芯片和挑选结果，再在新目录里重画。

```bash
python scripts/validation/build_cv_appendix_v2.py all --revision v2r2 --cell 89ad3617603ffff --d1-source v2r1
python scripts/validation/build_cv_appendix_v2.py assemble --revision v2r2
```

先提交脚本，再出图，这样清单里的两个提交号才能相等。文档提交在组装之后，不再次组装。

## 7. 仍开着的问题

Q3。L1952、L1953、L1954 的 Li 多边形大约偏北 1.4 米。要不要为这个偏移另做一套留出样本的精确率/召回率检查，这一批不回答，也不给出精确率或召回率。这三个格子仍然不进假阳性和假阴性的池子。如果现在就报一个精确率，那会是没有预先登记的结论。

Q4。`d2_cell_points.csv` 里的公开坐标要不要四舍五入，这一批不回答。文件里仍是脚本写出的经纬度。如果先四舍五入再给合作者，以后就分不清哪一位是测量、哪一位是舍入。

## 8. 同一天的 D6 决定，不在这个包里改图

负责人同一天把 D6 的主口径定为 1,008。619 是只含 2,755 块有日期地块的敏感性。一个月仍是 365.2425 / 12 = 30.436875 天。混合地块仍不进直方图。登记表全表最早、最晚验收日 2011-11-26 和 2024-07-02 可以公开。不增加任何新的 10 米规则。现有的“点落到地块外再找地块”的 10 米缓冲，配上的仍是 0 块。

1,008 的意思是：按任务清单 §B.4 的字面，τ = 90 天时，有日期的 619 块、只知到普查日的 173 块、混合的 216 块，都算矛盾。左删失加上去仍是 0。619 的意思是：只在模型给出完整起止日期的地块上数矛盾。忽略这个选择，合作者会继续把 619 当成以后航拍要看的总数，漏掉只知到普查日的那 173 块。

已上传的 v3r1 README 仍把 619 写成主表，并写着负责人还没有选定。这一批不改那个目录，也不重新上传。没有另建 `release_v3r2/`。验收把这句主口径放进 v2r2 的群消息草稿，草稿没有发送。N1（说明里的负号）和 N2（脚注上方的空白）仍留到下一次真的重画 D6。

## 9. 上传

上传前，`rclone lsf dropbox:RA_Solar/appendix/` 里没有 `ct_cv_appendix_figures_v2_2026-10-14`。直接探测该路径时 rclone 退出码 3，stderr 是 `directory not found`。然后执行 `rclone copy --checksum`，源是 `release_v2r2/` 里面的文件，目标是 `dropbox:RA_Solar/appendix/ct_cv_appendix_figures_v2_2026-10-14/`。复制退出码 0。随后：

```
rclone check --one-way
2026/10/14 08:47:32 NOTICE: Dropbox root 'RA_Solar/appendix/ct_cv_appendix_figures_v2_2026-10-14': 0 differences found
2026/10/14 08:47:32 NOTICE: Dropbox root 'RA_Solar/appendix/ct_cv_appendix_figures_v2_2026-10-14': 11 matching files
```

11 个文件是 README、图注、三张表、四张图、清单和 SHA256SUMS。本地发布目录没有被这次上传改写。0 差异的意思是 Dropbox 上的字节和本地这一包相同。如果忽略这一步，合作者打开的会是另一份或一份不完整的目录。

群消息草稿写在 `acceptance/group_msg_v2r2_draft.md`，没有发送。草稿里写了 D6 的主口径 1,008，并写明已上传的 v3r1 说明早于这个决定。

上传之后，`staging_v2r2` 和 `release_v2r2` 放进脚本的冻结名单。再跑 `--revision v2r2` 会停。上面第 6 节的命令是出这一包时用的，现在不能再跑。Q4 仍开着：这次上传带去的坐标没有降精度。若要先降精度，需要新的一版，不能改这个已经上传的目录。F1(b)、地图灰点的白边（O1）和说明里两句内部措辞（O2）留到下一次重画。
