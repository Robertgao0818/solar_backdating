# RUN — 两份本地大目录打包进 Dropbox

日期：2026-09-23 · 状态：**已核对**（15:40–19:07 +12）

两个 tar 都在 `solar_backdating_backups/`。上传结束时远端字节数与本地流出的 tar 一致。本地原件都还在。

本地盘只剩约 86 GB，不能先在本地落盘再上传。归档经 rclone API 直接流式写入，不经过 `/home/gao/Dropbox` 这个 FUSE 挂载（该挂载是 `vfs-cache-mode=full`，整包会先占满本地缓存）。

| 本地目录 | 约 | Dropbox 对象 | 字节 | sha256 |
|---|---:|---|---:|---|
| `~/zasolar_data/geid_raw/gehi_tile_cache` | 内容 26.6 GB，占用 36 GB（约 395 万个小文件） | `solar_backdating_backups/gehi_tile_cache_2026-09-23.tar` | 29,852,334,080 | `1fdd73e9d463eed140add821dee3664a34f3996763700ac02dcb91771e14fa5a` |
| `~/zasolar_data/geid_temporal/cape_town_citywide_backdating_v1_20260812` | 86 GB | `solar_backdating_backups/cape_town_citywide_backdating_v1_20260812_2026-09-23.tar` | 92,026,398,720 | `1064606e1984e3c851ef269101287ce3af30118470b604e644aa1920d60e6319` |

先传 GEHI 缓存（15:40–16:23），再传开普敦全城回推目录（16:23–19:07）。上传结束时远端字节数与本地 tar 流一致。sha256 在 `/home/gao/var/dropbox-backup-2026-09-23/`。

这不是 `zasolar_backdating/cape_town_citywide_v1/` 那份已有归档（约 377 GB、58 个文件，含 `top52_chips_20260818/part0N.tar` 和各 wave 的大包）。本地这份仍含 wave 工作目录、`ct05_download_v1`（15 GB）、`ct05_catalog_v1`（8.5 GB）、`production_gemini_v1`（8.4 GB）。

字节数已经对上。还没有把整包再下载回来重算 sha256。要腾本地盘，可以删这两份目录，取回时用表里的 sha256 核对。日志在 `/home/gao/var/dropbox-backup-2026-09-23/status.log`。
