# DATA — S3 面板参考 v1：三家族 API 可用性（Wave 1-B）

日期：2026-08-13 · Status: **NOT DISPATCHED**
协议：[MULTIMODEL_VISUAL_REVIEW_PROTOCOL](MULTIMODEL_VISUAL_REVIEW_PROTOCOL.md)
方案：[RUN-paper-program-sequencing-2026-08-13](RUN-paper-program-sequencing-2026-08-13.md) §1-B

协议要求至少 **2 个家族** 才能出参考标签；正式面板是 Gemini / GPT / Grok
三席。本检查只看工程环境里是否存在独立通道，不探测、不打印密钥。

| 席位 | 家族 | 工程环境 | 结论 |
|---|---|---|---|
| P1 | Gemini | `solar_backdating/.env.gemini.local` 含 `GEMINI_API_KEY` / `GOOGLE_GEMINI_BASE_URL` | **可用**（未做 live smoke） |
| P2 | GPT | 仓库与 ZAsolar 工程 `.env*` 无 `OPENAI_API_KEY` | **未确认** |
| P3 | Grok | 同上，无 `XAI_API_KEY` / `GROK_API_KEY` | **未确认** |

因此 1-B **不派发**。单席位 Gemini 盲审不能当 `panel_reference_v1`（协议
§1：相关误差声明 + 至少两家族才融合）。

下一步（owner）：把 P2/P3 的独立 API 通道写进本地 env（不进 git）后，
再开 S3 RUN（100 锚冻结盲包 × 3 席 + 各 20% repeat）。在此之前 S3
排队，不烧 Gemini 配额做单家族预跑。
