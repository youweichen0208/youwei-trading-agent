# 独立金融包与个人 Hermes 部署（2026-10-05）

按所有者计划新增私有 [trading_core](https://github.com/youweichen0208/trading_core)，默认分支 develop；平台保留正式研究、数据库、量化管线及批准版本。**已部署到 sg-prod**：所有者补充 SEC 联系信息后，真实财报及申报原文数值复核通过，两个助手服务已切换。下面保留首轮候选阶段的失败与限制；后续验收和生产结果见文末。

## 身份

| 项目 | 固定身份 |
| --- | --- |
| trading_core | `b915800aef7682010db36fb911fe5669ce45c37d` |
| trading-assistant | `0480ccec2bf221556106b8b4d7ef7a739955ad5f` |
| 金融 wheel | `trading_core-0.1.0-py3-none-any.whl` |
| wheel SHA256 | `64734560ea7446d50a7ece645e42b10c4a351e2df042f2007e552bed5cfeddbd` |
| amd64 registry 镜像 | `ghcr.io/youweichen0208/trading-assistant@sha256:ffd1768a4eb036083c37f4d16627922db66dd4318b4d949336c815e9ba4dcdc1` |
| Hermes | 官方 v2026.9.24 / `f97608f178d1ffeca59860195ab7da295f7c8e5f`；无上游补丁 |
| Python 基础镜像 | 3.13，原固定 digest 不变；目标镜像 Python 3.13.16 |

完整金融源码 SHA、两仓锁 hash、补充依赖 hash、首轮验收文件 hash 见 [source-evidence.json](../../infra/releases/20261005-trading-core/source-evidence.json)。助手将固定提交的 wheel 纳入 vendor，构建无需获取私有源码；重复从 `git archive` 构建得到相同 wheel hash。原候选登记 [disabled / smoke_only](../../infra/releases/20261005-trading-core/candidate.lock.json) 保留历史；当前生产发布使用本目录对应的 chat.lock.json 与 chat.manifest.json。

## 行为和边界

- 金融库公共接口：价格查询、财报查询、纯指标计算；不导入 Hermes、youwei_core 或数据库。美股/ETF 单证券日线最多五年，日期起含止不含；OHLC 保留供应商原值（供应商仍可能处理拆股），Adj Close 单独保存。
- 指标只用 Adj Close，包含 SMA20/50/200、Wilder RSI14、区间收益、日收益样本标准差×√252、峰值相对最大回撤。平盘 RSI=50，缺失数据不跨越/补齐，样本不足给出原因。
- SEC 使用 ticker→CIK、Company Facts 与 submissions；默认四季、最多八季/五年度。明确单季75–105天、年度350–380天，资产/负债按期末时点；仅 US-GAAP/USD，不从累计数据推导季度或 Q4，不转换单位。保留申报日期、来源标签、accession 和申报索引 URL。重述选择最新申报的适用事实；自定义/缺失标签、非USD、冲突事实保留缺失原因。
- 三工具 `trading_price_history`、`trading_indicators`、`trading_financials` 注册在现有助手插件。普通日线/指标/财报默认免费源；指定 EODHD/平台和指数按原入口。失败不静默混用供应商。
- 固定可信 Python worker 不接受代码、URL、路径或凭证。助手单并发、40秒总截止、停止时回收进程、512KiB响应上限、16条/120秒内存缓存。SEC 单HTTP请求8秒，最多两次请求，429不重试；Yahoo持久缓存禁用。worker只继承必要系统环境及SEC联系配置，不继承Core/LLM/EODHD密钥。
- 所有返回标明最新取得历史、`pit=false`。未接入正式输入链路，不代表正式评估或预测能力。

## 首轮候选验证

隔离开发：

- 金融包先观察缺失模块/API失败，再实现；`uv run --frozen pytest -q`：23 passed。覆盖计算已知答案、平盘/单调、Wilder种子、缺失/不足样本、拆股口径、累计/季度、非自然期间、单位、重述和缺失标签。
- 助手金融入口先观察缺失模块失败，再实现；同命令完整67 passed。覆盖注册/允许列表、超界/凭证拒绝、超时/取消回收、并发拒绝、返回大小、缓存/限流、脱敏及旧工具回归。
- `uv build --wheel`，固定提交重建 hash 一致；新独立 venv 仅安装 wheel 和锁定依赖，从 `/tmp` 使用 `python -I` 验证计算、无Hermes/Core模块。
- `infra/install_finance.py` 曾发现 lxml 6.1.3 与原 DDGS 6.1.2冲突并阻断；金融锁随后对齐6.1.2。最终安装前后已有包版本完全不变，`uv pip check` 109包兼容。官方 Hermes 锁未修改。
- 固定Hermes checkout `ops/verify_assistant_native.py` 通过，包含三个新工具的原生参数拒绝、九项MCP回归、鉴权/流式/追问/历史/并发/记忆/知识/重启/备份恢复。
- 平台 `uv run --frozen pytest -q tests/contracts/test_webui_configuration.py tests/contracts/test_assistant_image_backup.py tests/contracts/test_upstreams.py`：21 passed；主catalog及候选catalog VALID。

目标机：

- 从助手固定提交归档构建amd64并推送，取得上述真实registry digest。生产容器没有使用该候选。
- 无网络候选运行 `ops/verify_finance_runtime.py`：三个工具执行、参数拒绝与Hermes中断通过。发现8项基础工具；该隔离场景不配置EODHD，另由完整原生mock验证全部17项工具。[离线证据](../../infra/releases/20261005-trading-core/finance-offline.json)。
- 候选amd64完整 `ops/verify_assistant_native.py /opt/hermes` 通过，与本地覆盖相同。
- `--live` 真实请求：2026-09-01（含）至2026-10-03（不含），AAPL/MSFT/SPY各23行，三项日线和三项指标通过，验证证券、有效样本和SMA20，未仅凭HTTP200。[真实查询证据](../../infra/releases/20261005-trading-core/finance-live.json)。本地较早AAPL查询曾返回rate_limited，未冒充通过。
- AAPL/MSFT财报均返回 `sec_contact_not_configured`；未发送实际SEC请求、未验证数值与具体申报。此项是生产切换阻断条件。

## 首轮候选备份与切换条件（历史）

备份配置在 `/root/trading-finance-20261005/rollback/`，权限受限，秘密不进入仓库。隔离备份根位于同目录 `backups/`，本次为 `chat-backup-20261005-090321`；备份恢复校验 ALL PASS。候选无网络、无生产卷恢复助手归档：5个SQLite数据库、5个文件通过；恢复副本不接管线上数据。这仍是同机备份，非独立故障域。

当前生产仍为 `infra/releases/20261005-eodhd-marketplace/chat.lock.json`。联系信息通过运行环境 `TRADING_SEC_USER_AGENT="应用名称 实际联系邮箱"` 注入；模型不能覆盖。继续验收需先运行候选SEC真实查询，核对AAPL/MSFT财报期间/单位/值与实际申报来源；失败保持候选。仅在核心数据验收通过后刷新切换前备份、生成绑定实际渲染Compose的正式manifest，再只替换 hermes-assistant 和 hermes-workbench-skills，执行健康、工作台鉴权、旧新工具和真实查询检查；失败用切换前镜像/配置恢复。当前无生产切换，故无切换后验收或回滚执行。

未调用真实付费模型；自然语言选工具质量、浏览器人工操作、SEC真实数据和正式前向评估不能由mock推导通过。未改Core业务/共享schema，不重复完整数据库测试。

CI：金融仓固定提交 [run 37286713934](https://github.com/youweichen0208/trading_core/actions/runs/37286713934) 和助手固定提交 [run 37286907174](https://github.com/youweichen0208/trading-assistant/actions/runs/37286907174) 均 success。助手CI包括测试、固定上游安装/兼容检查、原生mock和镜像构建。

目标机已准备受限权限 `candidate-compose.json`：只修改助手/skills镜像及助手SEC联系环境引用，保留其他配置；未覆盖 `/opt/youwei/chat/compose.json`。14个既有平台/聊天/验收/Dashboard容器ID、StartedAt及镜像均保持一致，见 [身份比较](../../infra/releases/20261005-trading-core/production-unchanged.json)。

WebUI跨服务候选验收最终通过：使用既有WebUI镜像及mock模型验证三个新工具参数拒绝、发现、聊天、完整历史追问、SSE、知识、重启、备份恢复、后台分流、关闭注册和历史保留。首次宿主系统Python缺httpx；随后容器化验证到备份环节因容器临时路径不在宿主导致挂载失败；改用目标机既有项目venv运行相同脚本，完整PASS。保留失败背景，不把环境重跑描述为未发生失败。该测试没有调用真实付费模型，也没有把fixture财报视为SEC真实验收。

## SEC 联系配置与真实来源复核（2026-10-05，后续验收）

所有者提供实际联系邮箱后，服务器以0600环境文件注入 `TRADING_SEC_USER_AGENT`；邮箱未写入源码、镜像、部署锁或仓库证据。候选重新执行全部8项真实查询均通过，包括AAPL/MSFT财报。

[申报原文对照](../../infra/releases/20261005-trading-core/sec-filing-values.json)从submissions定位7份实际申报主文档，解析inline XBRL的consolidated context、USD unit、scale/sign及事实值，对照返回的32个非缺失数值，期间/单位/值全部相符。文档URL和SHA256随证据保存；缺失季度/累计现金流仍以null及原因保留，不推导补齐。可复核脚本为 [verify-sec-filing-values.py](../../infra/releases/20261005-trading-core/verify-sec-filing-values.py)。

切换前重新备份 `chat-backup-20261005-091659`，原镜像恢复校验ALL PASS；候选无网络恢复5个SQLite数据库和5个文件通过。受限回滚配置保存在 `/root/trading-finance-20261005/rollout-rollback/`，同时保留切换前secrets.env。此次沿用全部原数据卷，无数据迁移。发布配置只变更两个助手image字段及助手SEC联系环境引用，正式渲染Compose与 [chat.manifest.json](../../infra/releases/20261005-trading-core/chat.manifest.json)绑定；`validate_upstreams.py --mode deployment` 已通过。[切换脚本](../../infra/releases/20261005-trading-core/cutover-contact.py)在任何健康或上线复查失败时恢复两服务的切换前配置及镜像。

## 生产结果

- `hermes-assistant` 与 `hermes-workbench-skills` 已切换上述amd64 digest，助手healthy；其余12个既有聊天/平台/验收/Dashboard容器ID及启动时间不变，见 [切换后身份](../../infra/releases/20261005-trading-core/after-contact-rollout.json)。
- 生产原生发现17个工具；AAPL/MSFT/SPY日线和指标及AAPL/MSFT SEC全部通过，见 [金融实查](../../infra/releases/20261005-trading-core/postflight-finance.json)。既有EODHD报价、110项指数列表、GSPC当前/历史成分查询通过，见 [旧工具复查](../../infra/releases/20261005-trading-core/postflight-eodhd.json)。这不改变历史基本面/财报日历套餐拒绝限制。
- 工作台gateway/skills/bridge未认证均401；owner config、capabilities、gateway、skills（53项）、sessions、jobs、models均200。临时owner token仅在WebUI容器中使用，未输出，见 [工作台证据](../../infra/releases/20261005-trading-core/workbench-postflight.json)。
- 上线后实际Compose渲染字节与发布前校验内容相同，manifest匹配；本轮未触发回滚。当前发布锁为本目录对应的chat.lock.json；原candidate.lock.json/source-evidence.json保留历史，不再代表当前上线状态。
- 上线后备份 `chat-backup-20261005-092410`，恢复校验ALL PASS。完整汇总见 [生产证据](../../infra/releases/20261005-trading-core/contact-postflight.json)。
- 未调用真实付费模型、未执行正式前向评估，没有修改Core、研究版本、WebUI镜像、网络或数据卷。
