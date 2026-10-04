# 个人助手 EODHD MCP 接入与部署（2026-10-04）

按用户确认的常用美股查询范围，sg-prod 已上线 `WebUI → Hermes Gateway → EODHD 官方 MCP`。正式研究继续通过 Core 的 PIT、快照与 ResearchRelease 流程。助手仍使用官方 Hermes v2026.9.24 / v0.21.5、Python 3.13.16，无上游补丁或锁文件修改。

## 版本与边界

- 助手仓库提交：`3d4d7a7fa7dfbb85db6f3153041e71a738ae53e0`，已推送 main；功能提交 `73bdd5eab6f3f31b9cd2c3d2b49402087a352571`。
- 镜像：`ghcr.io/youweichen0208/trading-assistant@sha256:618f820ef3934ce69e526aa2105b7a0c49da44de254f650158123ee9d1cae235`，tag `eodhd-mcp-20261004`；已 push、按 digest pull，并核对镜像插件文件与提交源码一致。
- Hermes 完整 SHA：`f97608f178d1ffeca59860195ab7da295f7c8e5f`。新增安装其 frozen lock 内的 `mcp` extra，原 messaging/DDGS 保留；基础镜像和上游 pyproject/uv.lock hash 与前一 Release 相同。
- EODHD：`https://mcp.eodhd.com/v1/mcp`，Authorization Bearer header 引用 `EODHD_API_KEY`。现有凭证由私密 secrets.env 仅新增映射至助手；不放在 URL、镜像或模型参数里。端点握手观测 `eodhd-datasets-legacy 4.0.10`，93 个工具；远端服务无法通过本地镜像锁定版本，七工具 schema hash 保存在验收记录。
- 明确开放 resolve_ticker、get_stocks_from_search、get_historical_stock_prices、get_live_price_data、get_fundamentals_data、get_company_news、get_upcoming_earnings；与原五工具共 12 项。关闭资源/提示模板自动工具，执行中间件再次限制工具名及凭证覆盖。
- 历史行情要求日期区间，新闻默认 10 条，基本面按章节请求。MCP 离线允许基础助手启动；发布时必须发现完整七工具。返回内容与 Python 日志中的实际 key 脱敏。

## 已执行验证

| 层次 | 命令或入口 | 实际结果 |
| --- | --- | --- |
| 助手单元 | `uv run --frozen pytest -q` | 20 passed |
| 原生隔离 | 固定 checkout 的 Python 执行 `ops/verify_assistant_native.py` | mock 模型/MCP：七工具、额外工具过滤、凭证覆盖拒绝及脱敏、403/429/超时、离线启动；原鉴权/并发/流式/追问/memory/知识/重启/恢复通过 |
| CI | [最终提交的 CI](https://github.com/youweichen0208/trading-assistant/actions/runs/37191938460) | 单元、原生验收、amd64 镜像构建全部通过 |
| 平台回归 | `uv run --frozen pytest -q tests/contracts/test_assistant_image_backup.py tests/contracts/test_webui_configuration.py tests/contracts/test_upstreams.py` | 21 passed；未改 Core 业务、共享 schema 或数据库，未重复平台全量 |
| VM WebUI | `ops/verify_assistant_webui.py`，显式传入助手镜像和现用 v0.11.4 镜像 | mock 全链、旧聊天、SSE、知识、重启、镜像恢复、普通模型发现故障时后台分流、关闭注册通过 |
| VM 旧备份 | `ops/verify_assistant_release.py`，备份 `chat-backup-20261004-091804` | 无网络私有副本启动/重启通过；2 个会话、12 条消息的行 hash 保持，5 个数据库完整 |
| VM 真实 MCP | `ops/verify_eodhd_live.py` | 搜索、解析、历史行情、最新报价、新闻成功；基本面和财报日历 subscription_denied |
| 生产容器 | `ops/verify_eodhd_runtime.py`，不调用模型 | 12 工具发现及通过原生执行边界调用真实 AAPL 报价成功 |
| 线上入口 | WebUI 认证模型发现、Hermes 未认证请求、公网 `/api/version` | WebUI v0.11.4、保留 5 条聊天、Hermes 401、默认助手可发现；部署 Compose 与已校验渲染 hash 一致 |
| 部署后备份 | `/opt/youwei/backups/chat/daily/chat-backup-20261004-092809` | 隔离恢复 ALL PASS：WebUI 5 聊天/44 表、LiteLLM 115 日志/7 token、Hermes 5 数据库、秘密独立 0600 |

早期验收发现两项接线/测试缺陷：使用已移除的兼容 import 导致插件拒载，改为该 Release 的正式 MCP 模块路径；真实报价字段是 `close`，验收脚本原先误用 `price`，已修正。两项修复均在生产切换前完成。

两项套餐拒绝是当前实际能力限制，不能宣称可查询完整财报或财报日历；没有自动采购。未使用真实付费模型测试自然语言选工具或分析质量，未执行正式前向评估。MCP 结果不是已冻结的正式研究证据。

## 部署与回滚

切换成功时间为 2026-10-04 09:27:50 UTC。仅更新助手镜像及 EODHD 环境接线，保留当前 profile/knowledge 卷；WebUI、Core、Worker、Runner、研究镜像及其他常驻容器 ID/StartedAt 均保持。数据无迁移。

发布锁和公开验收证据在 [`infra/releases/20261004-eodhd-mcp/`](../../infra/releases/20261004-eodhd-mcp/)，消费登记为 `infra/chat/assistant-upstreams.lock.json`。含凭证的渲染 Compose 与环境文件只存 VM `/root/eodhd-mcp-20261004/`，不上传。平台此前未提交改动保留；此次平台文档与消费登记仍在工作区。

回滚材料：VM `/root/eodhd-mcp-20261004/rollback/compose.json` 和 `secrets.env`（0600）。旧助手 digest 为 `b99e7edb7da1ffc42d7ef7bb2a47a06bb44b1332d2db083568a187cbe514aec1`。先备份当前数据，再恢复这两个配置文件并执行：

```bash
docker compose -p youwei-chat --project-directory /opt/youwei/chat \
  -f /opt/youwei/chat/compose.json --env-file /opt/youwei/chat/secrets.env \
  up -d --no-deps hermes-assistant
```

检查 health、鉴权与基础五工具。两镜像使用相同官方运行时和数据格式，沿用当前卷即可，避免恢复旧备份丢失切换后的聊天；不回滚其他服务或正式 Ledger。同机备份仍不构成独立故障域。
