# EODHD Marketplace 指数查询部署（2026-10-05）

用户确认购买的是 **Indices Historical Constituents Data API**，并授权加入 Hermes 允许列表和部署。官方 MCP 已提供 `mp_indices_list` 与 `mp_index_components`，无须新增 REST 代理或另购 Token。本次按确认后的范围保留生产七项查询、增加这两项，共九项 MCP + 五项基础工具；此前 Extended 十九项候选仍是被账户验收阻断的历史实验。

## 源码与镜像

- trading-assistant develop：`cc33c14ca9ed10c98a295fdbdea42a3861a72010`（功能提交 `16cd41fab6a1dbc8f3447ff90b991f0558312efd`，后续提交仅延长验收脚本启动等待）；已推送。
- linux/amd64 registry 镜像：`ghcr.io/youweichen0208/trading-assistant@sha256:0e67de9ad23c66f61453f72c8acde9cbc4aff2f004eb3b602728e3ba39db460e`，由该固定提交 `git archive` 在 sg-prod 构建并推送，tag 使用完整源码 SHA。
- 官方 Hermes v2026.9.24 / `f97608f178d1ffeca59860195ab7da295f7c8e5f`，Python 3.13.16、依赖锁和基础镜像不变。
- WebUI 镜像继续使用 `ghcr.io/youweichen0208/youwei-webui@sha256:171e0e83a7b03b29d1d3552dc637b1e71526085278a3717dc92989ce4cbd630f`；Core、Worker、Runner、数据库与模型网关不更新。

## 允许范围与权限

允许 `resolve_ticker`、`get_stocks_from_search`、`get_historical_stock_prices`、`get_live_price_data`、`get_fundamentals_data`、`get_company_news`、`get_upcoming_earnings`、`mp_indices_list`、`mp_index_components`。九项来自代码政策，关闭 resources/prompts 自动工具。保持基础五工具和原生执行中间件防护。

指数列表无必填参数；成分查询显式传单个指数代码，如 `GSPC.INDX`，拒绝路径、批量代码、非 JSON 格式及任何凭证覆盖。先查列表再选指数，不自动批量下载。原样区分 Components 与 HistoricalTickerComponents，历史调入/调出日期不等于系统在当时已知，不能作为历史权重或正式 PIT 输入。

账户基础 subscriptionType 与 Marketplace 独立授权分开判断；控制台的 Marketplace 用量不会覆盖其他 API。原有基本面/财报日历的套餐拒绝限制保留；未声称九项全部有数据权限。盘中/技术指标/筛选与实时采集未启用。

此前 Token 轮换后，线上旧 Key 实测 401。本次只更新助手的 `EODHD_API_KEY` 为所有者提供的新 Key；Authorization header 注入、模型参数拒绝覆盖、日志及返回脱敏继续生效。未更新 Core 凭证，不在代码、镜像或证据保存 Key。

## 验证记录

- 助手 TDD：允许列表先 1 failed、参数边界先 6 failed、结构验证先 1 failed；最终完整 `uv run --frozen pytest -q` **60 passed**，`git diff --check` 通过。
- 固定 checkout 及本地独立镜像的原生 mock：九工具发现/调用、越界/凭证/脱敏、403/429/超时/离线、鉴权/并发/流式/历史/知识/记忆/重启/备份恢复 PASS。
- VM 前两次原生 mock 触发旧脚本 30 秒启动等待窗（脚本主动 SIGTERM）；修改为 120 秒，仅改变测试等待，MCP 业务调用仍为 30 秒。最终 amd64 九工具原生 mock **PASS**，见 release preflight evidence。
- 新 Token、隔离 HOME、无生产卷，候选镜像原生发现完整 14 工具；AAPL 报价证券/价格/时间戳有效；指数列表 110 项，GSPC 当前和历史成分非空、代码和日期字段校验通过。只执行供应商查询，不调用 LLM；见 [账户证据](../../infra/releases/20261005-eodhd-marketplace/account-evidence.json)。
- 官方 schema 与 SHA256 记录于 [marketplace-schemas.json](../../infra/releases/20261005-eodhd-marketplace/marketplace-schemas.json)，mock 使用相同 schema。
- 平台 `uv run --frozen pytest -q tests/contracts/test_webui_configuration.py tests/contracts/test_assistant_image_backup.py tests/contracts/test_upstreams.py` **21 passed**；未改 Core 业务，未重复全量数据库测试。

## 备份、切换与回滚

切换前运行现有 `chat_backup.sh`，备份 `chat-backup-20261005-061148`；`chat_backup_verify.sh` ALL PASS。候选镜像另在无网络、无生产卷容器中恢复该助手归档，五个数据库及全部文件校验通过。读取 0600 归档的临时容器使用 root，隔离输出仅在临时容器 `/tmp`，生产仍为 UID 10001。备份在同一 VM，不等于独立故障域。

回滚目录：`/root/eodhd-marketplace-20261005/rollback/`。部署只更改 Compose 两个 image 字段，沿用网络、挂载卷、其他环境与 WebUI；秘密配置只替换 EODHD Key。旧助手及 skills 镜像为 `ghcr.io/youweichen0208/trading-assistant@sha256:5686c66c00568511ea645c8402cfad6b37ae4019aceb12674ba27eb999618171`。

回滚时恢复该目录 compose.json 到 `/opt/youwei/chat/compose.json`，保留已验证的新 Token（旧 Token 已失效），再执行：

```bash
docker compose -p youwei-chat --project-directory /opt/youwei/chat \
  -f /opt/youwei/chat/compose.json --env-file /opt/youwei/chat/secrets.env \
  up -d --no-deps hermes-assistant hermes-workbench-skills
```

不恢复旧秘密文件覆盖有效 Key，不删除数据卷。含秘密的渲染 Compose 仅存在于 VM 0600 文件，仓库只存 hash、镜像锁和不含秘密的验收证据。部署状态以 release postflight evidence 为准。

## 生产结果

- 两个助手容器已切至登记 digest；Hermes healthy，skills 认证读取成功。其余 12 个既有容器的 ID 与 StartedAt 完全一致，含 WebUI、LiteLLM、Core、Worker、Runner 和数据库。
- 生产原生发现完整 14 工具，AAPL 报价、110 项指数列表、GSPC 当前/历史成分真实查询全部 PASS；没有通过付费模型发起这些请求。
- 工作台：未认证 gateway/skills/bridge 均 401；5 分钟 owner JWT 下 config、capabilities、gateway、skills（53 项）、sessions、jobs、models 全部 200。Token 只在 WebUI 容器内生成使用，没有输出。
- 最终固定镜像与现有 WebUI 的隔离 mock 全部 PASS：发现/工具执行、对话、完整历史追问、SSE、知识、重启、备份恢复、后台分流、关闭注册和历史保留。
- 平台 `validate_upstreams.py --mode deployment` **VALID**；上线后重新渲染实际 Compose，与已验证 manifest 对应字节完全一致。主 catalog、助手 catalog 和 release catalog 均通过。
- 切换后备份 `chat-backup-20261005-062649`，恢复校验 **ALL PASS**。临时凭证副本已删除；回滚配置和单独秘密备份保留于受限目录。本次未触发回滚。
- [完整生产证据](../../infra/releases/20261005-eodhd-marketplace/postflight-evidence.json)及前后容器记录保存在本 release 目录。

## 验收边界

真实付费模型的自然语言选工具质量、人工浏览器交互及正式前向评估不在本次验收范围。WebUI→助手链路以 mock 模型验证，供应商工具另以真实账户和 Hermes 原生执行边界验证，不能将两者描述为真实付费模型端到端验收。
