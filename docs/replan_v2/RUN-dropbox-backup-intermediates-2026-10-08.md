# RUN — 中间文件流式备份到 Dropbox

日期：2026-10-08 · 状态：**上传中**（第 1、2 份已核对并删除本地；第 3 份在传）

本地盘是 btrfs（带 zstd 压缩的文件系统），`/` 和 `/home` 共用。清理前 `df` 只剩 2.9G。已退出 Chrome，删掉网页离线缓存和 Chrome 磁盘缓存，删掉 Cursor 旧日志（正在写的 `20261007T211728` 保留），并执行 `docker builder prune -af`（构建缓存 2.84GB）。清理后可用空间是 8.9G。`du` 里大约 12G 是未压缩的逻辑大小，压缩后实际退回的块少一些。四个正在跑的 Docker 容器还在。

上传走 rclone 的远程接口，不经过 `/home/gao/Dropbox`。那个挂载是 `vfs-cache-mode=full`，整包会先占满本地缓存。会话名 `dropbox_backup_20261008`。脚本和日志在 `/home/gao/var/dropbox-backup-2026-10-08/`。每个 tar 上传结束后，远端字节数要和本地流出的 tar 一致，sha256 写在同目录。本地原件不删。

顺序和目标名（都在 `dropbox:solar_backdating_backups/`）：

| 顺序 | 本地 | 约 | 远端对象 | 核对 |
|---|---|---:|---|---|
| 1 | `gate_pilot_2026-09/learning_curve_2026-09-28/train/vit_tokens` | 35G | `gate_learning_curve_vit_tokens_2026-10-08.tar` | 37,174,446,080 字节，sha256 `f3f85f505c0ed9f4fc3f81bbaf63721312e75676845a71980232bb66f15a5d53`，18:54–20:08。本地目录已于核对后删除 |
| 2 | 同目录的 `chips` | 15G | `gate_learning_curve_chips_2026-10-08.tar` | 15,634,667,520 字节，sha256 `817053bf58a5d38c6391193b6020e1b68c1501a5e91e9649a6e47f17bd7c2cd2`，20:08–20:34。本地目录已删除 |
| 3 | `geid_raw/gehi_tile_cache` | 37G | `gehi_tile_cache_2026-10-08.tar` | 20:34 开始上传 |
| 4 | `datacenter-geo/data/raw` 里 9 月和 10 月 2 日的旧试点，加上 `pi_cooling_2026-09-04` | 约 5G | `datacenter_geo_raw_older_pilots_2026-10-08.tar` | 排队 |
| 5 | `projects/ZAsolar/results` | 12G | `zasolar_project_results_2026-10-08.tar` | 排队 |
| 6 | `projects/ZAsolar/tiles/vexcel_eval_60_osm_stratified_seed42_2x2` | 4.1G | 先和 2026-07-11 的旧 tar 对文件名；不一致才另传一份 | 排队 |

9 月 23 日的 `gehi_tile_cache_2026-09-23.tar` 保留。10 月 5 日缓存又写过，所以那份不能当作现在的完整备份。10 月 7–8 日的冷却分类、审阅表和当天的 Vexcel 芯片不在第 4 包里。开普敦全市工作树和 10 格时间线影像不在这次队列里。

第 1 份核对通过后，本地 `train/vit_tokens` 已删除。同目录的 `data`（2.5G）、`runs` 和 `scores` 不是这包 tar 的内容，还在。第 2 份核对通过后，本地 `chips` 已删除。数据区用量从 184.06GiB 降到 171.32GiB，`df` 显示大约 56G 可用。学习曲线目录还剩约 2.7G。第 3 份 GEHI 瓦片缓存从 20:34 开始上传，本地没有删。
