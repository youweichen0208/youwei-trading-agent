# EODHD Extended 十九工具候选验收

日期：2026-10-05（上海）；供应商验收发生于 2026-10-04 UTC。用户要求实现 EOD + Intraday All-World Extended 的十九项查询，并在账户验证通过后部署 sg-prod。**实现和候选镜像已交付，生产切换被账户/连接验收阻断。**

后续状态：用户确认购买的是 Marketplace 指数历史成分产品；另按原有七项 + 两项指数工具发布，见[后续记录](eodhd-marketplace-rollout-20261005.md)。下文“当前生产”均指本实验记录时点，不代表最新部署。

## 交付身份

| 项目 | 固定值 |
| --- | --- |
| 助手 develop 源码 | `d9f69c596c90219c17aab5b7714a1d06f0baab4e`，已推送 |
| amd64 候选镜像 | `ghcr.io/youweichen0208/trading-assistant@sha256:43a518a58a8e81a8e4f0625fa08940ff5bc1a81e91d7f854e6eef2f0d754589e` |
| 发布 tag | `eodhd-extended-d9f69c5`（运行身份以 digest 为准） |
| 官方 Hermes | v2026.9.24 / `f97608f178d1ffeca59860195ab7da295f7c8e5f`，Python 3.13.16，依赖锁/基础镜像不变 |
| 当前生产助手及 skills | `ghcr.io/youweichen0208/trading-assistant@sha256:5686c66c00568511ea645c8402cfad6b37ae4019aceb12674ba27eb999618171`，未切换 |
| 当前 WebUI（无源码修改） | `ghcr.io/youweichen0208/youwei-webui@sha256:171e0e83a7b03b29d1d3552dc637b1e71526085278a3717dc92989ce4cbd630f` |

镜像在 sg-prod 独立目录 `/root/eodhd-extended-20261005/assistant` 从 `git archive` 构建并推送；源码包 hash 见[候选证据](../../infra/releases/20261005-eodhd-extended/candidate-evidence.json)。没有重建生产容器。平台使用独立的[禁用候选锁](../../infra/releases/20261005-eodhd-extended/assistant.candidate.lock.json)登记，现行 `infra/chat/assistant-upstreams.lock.json` 与工作台 release 清单继续代表线上版本。

## 助手实现

- `integrations/hermes/policy.py`：显式十九工具；保留基础五工具，移除基本面与财报日历，不自动开放 resources/prompts 或远端新增工具。
- `eodhd.py`：日线、历史公司行为、情绪、词频、指标要求有序日期；盘中要求明确起止时间，国债要求明确年份。新闻/词频/筛选默认 10 条、最多 50 条；WebSocket 默认 5 秒，最多 10 秒/100 条/1 MiB，连接超时最多 5 秒，非法类型和越界参数调用前拒绝。MCP 调用超时维持 30 秒。保留上游结果中的采集时间/截断信息。
- 既有 Authorization header 服务端 Key 注入、递归凭证覆盖拒绝、返回/日志脱敏继续生效。没有向模型、镜像或仓库写入真实 Key。
- `ops/eodhd-schemas.json` 保存官方 `tools/list` 的十九项真实 inputSchema，原生 mock 使用该 schema；账户报告逐项记录其 SHA256。
- `ops/verify_eodhd_live.py` 解包 MCP structuredContent/文本中的 JSON，检查真实数据结构、证券、日期及关键字段，区分空结果、403、限流、参数错误、断连和无效响应。旧脚本只看 `isError` 会漏掉技术指标和筛选嵌套的 403，本次增加回归测试并重跑全部查询确认分类。

## 账户实测

复用 VM 助手现有 `EODHD_API_KEY`，只读调用官方 `https://mcp.eodhd.com/v1/mcp`，无付费模型。发现 93 项工具，目标十九项均存在。详细参数、状态、行数、schema hash 见[账户证据](../../infra/releases/20261005-eodhd-extended/account-evidence.json)。

| 工具 | 实测结果 |
| --- | --- |
| resolve_ticker / get_stocks_from_search | 成功；核对 AAPL.US / US |
| get_historical_stock_prices | 成功；日期区间内 5 行 |
| get_intraday_historical_data | **subscription_denied，403** |
| get_live_price_data / get_us_live_extended_quotes | 成功；核对证券、价格及时间戳 |
| get_historical_dividends / get_historical_splits | 成功；3 条分红、2020-08-31 拆股记录 |
| get_company_news / get_sentiment_data / get_news_word_weights | 成功；新闻字段、情绪日期及词频数值校验通过 |
| get_technical_indicators / stock_screener | **subscription_denied，403，错误嵌套于 isError=false 响应** |
| get_historical_commodity_prices | 成功；WTI annual，40 行；接口本身无日期范围参数 |
| get_ust_bill_rates / get_ust_long_term_rates | 成功；年份内 1330/570 行；接口**忽略 limit=1**，已标记，不宣称分页限量生效 |
| get_ust_yield_rates / get_ust_real_yield_rates | 成功；各 1 行 |
| capture_realtime_ws | **connection_failed**；正在持续交易的 crypto/BTC-USD，5 秒采集配置；服务端报 `no close frame received or sent`，未证实消息接收 |

合计：15 项有效，3 项套餐拒绝，1 项连接失败；本次没有合法空结果、限流或参数错误。403 的供应商文案称该 Key 仅允许 EOD；这证明目标能力当前不可用，不据此推断用户是否已经购买或付款。实时连接失败原因未确定，不能归类为套餐拒绝。没有静默移除失败工具、扩大权限、替换 Key 或采购。

## 实际验证与隔离

| 层次 | 命令/结果 |
| --- | --- |
| 助手单元测试 | `uv run --frozen pytest -q`，50 passed；允许列表、查询界限、凭证、脱敏、结构/日期及嵌套错误验收 |
| TDD | 允许工具测试先 1 failed；限制测试先 19 failed；响应校验先因缺少实现失败，再全部通过 |
| 原生开发验证 | 固定 Hermes checkout 的 `ops/verify_assistant_native.py`，十九工具循环、403/429/超时/离线启动、鉴权/并发/历史/流式/记忆/备份恢复 PASS |
| 独立镜像 | arm64 Docker build 通过；镜像内 `--network none` 原生 mock PASS，逐文件核对插件源码，使用官方 schema |
| amd64 发布 | sg-prod 干净源码构建与 registry push 通过；只有临时验证容器使用新镜像 |
| 候选真实原生接口 | `ops/verify_eodhd_runtime.py` 在新 amd64 容器、临时 HOME、无生产卷下，完整发现 24 工具，AAPL 真实报价字段校验 PASS，无 LLM |
| 平台契约 | `uv run --frozen pytest -q tests/contracts/test_webui_configuration.py tests/contracts/test_assistant_image_backup.py tests/contracts/test_upstreams.py`，21 passed |
| 登记与 Compose | 主 catalog 和禁用候选 catalog 均通过；chat + assistant + workbench 三份 Compose 占位渲染 `config --quiet` 通过，无启动 |

WebUI 跨服务 mock：VM 上以新助手 digest 和现有 WebUI digest 运行 `ops/verify_assistant_webui.py`，模型发现、对话、完整历史追问/SSE、知识工具、重启/备份恢复、后台任务分流（含模型发现不可用）、关闭注册和历史保留全部 PASS；临时网络、卷、容器已清理。该链路不带真实供应商凭证；十九项 EODHD 执行另由原生 MCP mock 与账户查询分别证明。目标机验证入口使用独立 `verify-venv`，httpx 0.28.1 与平台锁一致；宿主默认 Python 缺 httpx、旧验证环境解释器链接失效，未更改生产 Python/依赖。

## 生产阻断与剩余步骤

按用户计划，预期能力出现权限拒绝或接口不兼容就暂停切换。当前没有操作生产 Compose、Key、网络、卷、WebUI 或 Core 数据链路；没有运行生产发布、付费模型自然语言选工具质量或正式前向评估。Core 业务未修改，未重复执行全量数据库测试。

恢复上线前须确认既有 Key 的目标套餐权限，并使短时实时采集实际收到消息；重跑全部账户验收及原生边界检查。账户通过后，重新备份当时生产配置和助手数据、在镜像内隔离恢复，再仅更新 `hermes-assistant` 和 `hermes-workbench-skills`；完成健康、工作台鉴权、完整发现及代表性真实查询。失败则恢复备份的旧配置与旧镜像。**本轮未执行这套切换前备份/生产副本恢复或切换后验收**，不能用历史备份或合成恢复代替。
