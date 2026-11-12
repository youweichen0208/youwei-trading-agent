# Hermes Alpha Lab

研究、预测、组合 Profile 自动协作的美股研究工作台，支持多用户与独立执行上下文。持续研究、5/20 个交易日预测、到期验证、复盘和独立经验试验已于 **2026-11-12 上线生产环境**。长期预测表现和经验效果仍需真实交易日数据积累。

设计见 [架构](docs/architecture.md)、[API](docs/api-contract.md)、[Canvas](docs/canvas-protocol.md)。实际进度与验证边界见 [实施证据](docs/implementation-status.md)。上线后的数据源配置、待开发能力和长期验证见[生产待补齐清单](docs/production-follow-up.md)。

## 当前上线状态（2026-11-12）

访问 [生产工作台](https://trading.youwei-agent.com/lab)，核对持仓、添加自选股后即可开始研究，也可直接询问一只临时关注的股票。

| 项目 | 已核验状态 |
| --- | --- |
| 应用服务 | Web、Worker、Tools、Supervisor、Preparer、飞书六个服务正常运行 |
| Agent 运行环境 | 三个生产上下文、9 个 Profile 全部通过真实工具调用、取消、幂等及重启恢复检查 |
| 数据库 | 已迁移至 031，既有运行账户增量授权完成 |
| 网页 | 公网登录、研究接口、预测页、刷新和 390px 手机布局验收通过 |
| 数据来源 | Alpaca IEX 行情、新闻及真实市场扫描已验证；扫描保留缺数和截断状态 |
| 后台与通知 | 结算检查、覆盖检查和摘要已有成功运行记录；研究任务已入队；飞书连接和单条实际投递已验证 |
| 预测效果 | 尚无长期生产准确率或经验改进效果结论；隔离验收预测未导入生产库 |

上线前数据库及运行状态已备份，公网入口与源站静态构建同步。详细证据和部署边界见[实施记录](docs/implementation-status.md)。

## 持续研究与预测

登录后访问 `/lab`：研究池、预测记录、经验试验、研究设置集中展示，保留原聊天与 Canvas。持仓优先，其次自选、临时观察和市场发现；用户临时问到的股票在建立研究上下文时进入 20 个交易日观察期，可加入长期自选、暂停或移除。持仓始终来自最新用户快照，Agent 不改写数量。

- research 获取行情和公司证据，冻结当时已知的上下文；prediction 发布 5/20 个交易日的预期收益、上涨概率、80% 收益区间、依据和失效条件。改判追加版本，原预测继续验证。
- 预测从发布后首个尚未开盘的交易日开盘价开始，到第 5/20 个交易日收盘；等待收盘后 20 分钟再结算。缺少股票或 SPY 结算行情时保留待补数状态。旧的一日统计回溯基线继续保留，单独展示。
- 对正确与错误预测均可复盘。新经验先进入影子试验：两个独立会话使用同一证据和现行方法，只有候选臂读到待验证经验。主预测仅引用已晋升的 active 经验，不把一次复盘直接变成长期规则。
- Worker 按交易日执行持仓/自选盘前检查、收盘后覆盖检查与市场扫描，默认每日深研最多 30 只股票、其中新增市场候选最多 10 只。超额工作保存为 `deferred`，之后按优先级和等待时间继续，不因预算用完丢弃。默认上海时间 08:00 生成摘要；网页通知持久保存，已绑定飞书且开启通知时由飞书进程投递。

证据保存来源公开时间、系统获取时间、原文和版本；上下文按获取时间截断。公司知识、同业关系、预测结果和经验版本保存于 PostgreSQL，Hermes 的会话记忆不承担预测账本。市场扫描保留实际股票范围、缺失行情和失败原因；按相对 SPY 的绝对涨跌幅、量比及已取得的新闻数量排名，默认前八名加两个确定性探索名额。新闻计数不是完整公告覆盖，当前股票范围也不是历史成分重建。

### 配置来源与启用

所有凭证只通过服务端环境配置；`/lab` 显示配置缺项与实际采集失败。配置存在仅表示可以尝试调用，不代表账户权限或内容覆盖已经验收。

| 配置 | 用途 |
| --- | --- |
| `MARKET_PROVIDER=alpaca`、`ALPACA_DATA_KEY`、`ALPACA_DATA_SECRET`、`ALPACA_DATA_FEED=iex\|sip` | 行情、新闻、结算和市场扫描；保留 feed 标识 |
| `ALPACA_ASSET_API_ORIGIN` | 股票目录账户地址，可选 paper/live Alpaca；默认 `https://paper-api.alpaca.markets` |
| `SEC_USER_AGENT` | SEC 请求身份，填写应用说明和联系邮箱；读取公司申报、财务 facts 和部分原文 |
| `BRAVE_SEARCH_API_KEY` | 搜索新闻、公司 IR 和讨论网页；显式提供网页 URL 时可直接获取公开 HTML |
| `X_BEARER_TOKEN` | X 近期搜索，当前代码取最近约六天、单次最多 50 条；实际可用性取决于账户访问能力 |
| `FRED_API_KEY` | 宏观数据及指定日期的 vintage |
| `FORECAST_MODEL_VERSION` | 保存模型与提示词版本标识，默认记录固定 Profile 运行时标识 |

Worker 和 Tools 都需要相应来源配置，Web 的来源状态应与它们一致。未配置的渠道返回明确的 `unavailable`，不会填充演示新闻。每轮证据采集共享 22 秒截止时间，已取得的内容可部分返回；公开 HTML 读取不包含脚本渲染评论、付费全文或音视频转录。

升级既有环境时，以部署账户执行迁移，再对原运行角色增量授权：

```sh
pnpm db:migrate
pnpm exec tsx scripts/upgrade-multiuser-access.ts WEB_ROLE WORKER_ROLE TOOLS_ROLE FEISHU_ROLE
pnpm hermes:catalog
```

迁移 029 增加预测与经验账本，030 增加证据、上下文、研究池和扫描记录，031 增加后台任务、预算积压和通知。`upgrade-multiuser-access.ts` 已包含这些对象的增量授权；使用部署中的真实角色名，不重建账户或轮换密码。首次安装仍使用 `pnpm db:access`。重新生成的工具目录需要随新版 Hermes 镜像/运行文件部署，并完成对应运行时校验后重启 Tools、Worker 和 Profile；仅升级 Web 不会启动持续研究。Worker 入口默认启用 `RESEARCH_SINGLE_ROOT=1`，后台分析分阶段运行，交互研究在阶段之间优先。

运行 `pnpm worker` 和 `pnpm tools` 使用各自数据库角色与既有 Hermes 注册表/发布指针；通知投递还需要现有飞书进程。生产 Next.js、Worker、Tools 均须能找到仓库的 `services/quant` 与 Python 环境：默认从工作目录向上寻找仓库，解释器为仓库 `.venv/bin/python`；可设置 `QUANT_PROJECT_ROOT` 为仓库绝对路径、`QUANT_PYTHON` 为其他已安装依赖的 Python。仅复制 Next 构建产物而缺少 Quant 源码或 Python 环境会返回 `quant_unavailable`。

验证覆盖真实 PostgreSQL 与 Python 计算、冻结证据/价格引用、独立双臂试验、预算恢复、来源超时和扫描缺数。最终后端回归 180 项通过，类型检查和生产构建通过；隔离环境真实 Hermes + Alpaca 链路已保存 5/20 个交易日预测并完成浏览器验证。真实市场扫描保留实际覆盖、缺数和新闻截断；SEC、Brave、X、FRED 目前仅有适配器及受控测试，尚待配置和真实联网验收。

## 还需要补齐什么

### 已实现，等待配置

生产 Web、Worker、Tools 在上线核验时均缺少以下配置。配置存在不等于数据源接通，必须验证实际采集结果。

| 优先级 | 来源 | 需要提供 | 接通后的能力 |
| --- | --- | --- | --- |
| P1 | SEC | `SEC_USER_AGENT`：应用名称与真实联系邮箱，不是 API Key | 公司披露、部分申报正文和财务指标 |
| P1 | 网页搜索 | `BRAVE_SEARCH_API_KEY` | 自动发现新闻、投资者关系和讨论网页，并读取公开 HTML |
| P1 | 宏观数据 | `FRED_API_KEY` | 利率、收益率曲线、通胀、失业率与波动率相关序列 |
| P2 | X | `X_BEARER_TOKEN`，账号可调用近期搜索接口 | 股票相关英文讨论、作者信息及采集时的互动指标 |

下一轮建议先补 SEC、Brave、FRED，再接 X。计划使用服务器私有 `research-sources.env`，由 Web、Worker、Tools 一致加载；该独立文件尚未创建或挂载。实际凭证不写入 README。配置流程、范围限制和逐项验收见[数据源待补齐清单](docs/production-follow-up.md)。

### 用户研究设置

核对实际持仓和长期自选股；默认每日研究最多 30 只、新候选最多 10 只，候选预算可设为 0。后台研究和飞书通知默认开启，可按个人偏好调整。这里的预算是标的数量，不是金额或 token 上限。

### 需要继续开发

- 股东大会、财报电话会：会议发现、文字稿获取、音视频转写和可引用的发言片段。
- X 专业作者体系：关注作者名单、历史观点兑现追踪和来源权重；目前仅按股票代码搜索。
- 网页评论：动态加载、分页、回复关系及去重；现有公开 HTML 提取不等于评论采集。
- 公司事件与更多因素：结构化财报日历、预期与实际差异、指引、回购，以及按需求接入期权、空头、预期修订和行业关系数据。
- 长期运维：周期备份与恢复演练、调度失败告警、来源可用率和模型用量监控；本次上线备份不代表周期备份已配置。

### 需要随运行积累

真实生产预测到期后，验证 5/20 个交易日结算、缺数处理、成功与失败复盘。经验晋升评估至少需要 30 个新事件样本，通用经验还须覆盖至少 5 家公司；样本量达标不代表自动晋升。后续继续验证独立对照试验、经验降级和不同置信度的实际表现。

完整优先级、实施边界和完成标准见[生产待补齐清单](docs/production-follow-up.md)。

## 已部署环境

访问 [Hermes Alpha Lab](https://trading.youwei-agent.com)。主应用、数据库及隔离 Profile
部署在 DigitalOcean，阿里云提供 HTTPS 入口。模型使用已授权的 Claude Code 配置快照，
行情使用 Alpaca IEX；本项目只做研究和模拟组合分析。

当前发布目录为 `/opt/hermes-alpha-lab/releases/20261112-forecast-production`，公网与源站构建 ID 为 `CuLib1B_up84rxCkS2fQL`。生产 Hermes 镜像为：

```text
sha256:d3da8c7bc3cc0770bf6eabd109238a6d853a9e0d9cbf273c00ac990c8c141da5
```

主注册表通过 `/var/lib/hermes-alpha-lab/worker/forecast-publications-20261112-r3/current.json` 发布，兼容注册表也已同步为 9 个已验证 Profile。新建 Profile 的准备策略使用同一镜像。多人、准入等功能是否开放仍由既有生产开关决定，不因代码支持多用户就自动开放全部入口。

上线前备份位于服务器私有目录 `private/forecast-production-20261112-r2/`；最终切换备份和逐组验证证据位于 `private/forecast-production-20261112-r3/`。回退前需核对上线后的新增任务与数据，不能直接用旧快照覆盖。

当前发布复用 `20261112-multiuser-candidate` 的 `node_modules` 和 `.venv`，清理旧发布前必须先解除依赖并重新验证。后续 Web 发布需同时同步阿里云入口的匹配静态构建。

初始账户为 `owner@alpha.local`，随机密码保存在本地私有文件 `.local/provider/deployment-login.json`，没有写入仓库。部署历史、服务、验收证据和回退边界见[部署记录](docs/implementation-status.md)。

## 开发验证

需要 uv 与 Python 3.12（uv 可安装对应版本）。

```sh
uv sync
uv run pytest
uv run ruff check .
```

## PostgreSQL 与 TypeScript 验证

需要 Docker、Node.js 24+、pnpm，并先运行 `uv sync`（后端数据链测试会调用真实 Python 计算进程）。

```sh
pnpm install
docker compose -f infra/compose.yaml up -d --wait
DATABASE_URL=postgresql://alpha:local-development-only@127.0.0.1:55439/alpha_test pnpm db:migrate
pnpm typecheck
TEST_DATABASE_URL=postgresql://alpha:local-development-only@127.0.0.1:55439/alpha_test pnpm test
```

凭证仅供本地隔离开发使用。测试必须显式提供名称以 `_test` 结尾的数据库，不会默认连接其他数据库。迁移有校验和，已应用文件不可原地修改。

## 本地工作台

完成数据库迁移后启动：

```sh
DATABASE_URL=postgresql://alpha:local-development-only@127.0.0.1:55439/alpha_test APP_ORIGIN=http://localhost:3400 pnpm dev
```

打开 http://localhost:3400 注册账户。可使用研究历史、任务提交/取消、模拟持仓快照和自选股。独立 Worker 需要另开进程启动。未配置真实 Hermes 时，Worker 会明确报告 Profile 不可用，不产生伪造研究结果。

```sh
pnpm build
# 保持本地 dev 服务运行后，在另一个终端验证浏览器流程（使用已安装的 Chrome）：
pnpm exec playwright test
```

生产环境要求 HTTPS APP_ORIGIN。请使用独立正式数据库及最小权限运行角色；当前 Compose 凭证只供本地测试。

已提供 `pnpm db:access`，创建 Web、Worker、Tools 独立运行账户及私有连接文件。迁移/部署账户与运行账户分开；本地 `.local/database-access` 已生成三份配置。权限范围、启动方法与验证边界见[数据库运行账户](docs/database-access.md)。

## 独立任务 Worker

```sh
DATABASE_URL=postgresql://alpha:local-development-only@127.0.0.1:55439/alpha_test pnpm worker
```

Worker 与 Web 使用同一数据库，浏览器断开不影响任务调度。SIGINT/SIGTERM 停止领取新任务，等待当前有界步骤结束；崩溃遗留租约在 30 秒后接管。父任务等待子任务期间会释放 Worker 槽位。

每用户最多接受 10 个未结束交互根研究，包含 unknown；超过上限返回 `429 queue_full`。后台研究使用独立股票日预算，不占用这十个交互名额。重复提交同一请求键仍返回原任务；取消排队任务可释放名额。

Worker 启动及每分钟分批清理超过 7 天的增量事件、过期登录会话和认证限流桶，每类每轮最多 1000 条，积压留待下一轮。清理不删除任务、消息、快照、数据/结果、执行回执或审计；过期事件由快照恢复。多 Worker 通过数据库锁协调，清理错误不会停止任务处理。

真实运行时配置使用服务端环境变量 `HERMES_PROFILE_REGISTRY` 指向私有 JSON 注册表。注册表绑定数据库的 Profile UUID，而非用户提交的地址。每条记录格式：

```json
{
  "profileId": "数据库中的-profile_instances-id",
  "origin": "http://research-profile:8642",
  "tokenEnv": "HERMES_RESEARCH_TOKEN",
  "imageDigest": "sha256:实际镜像摘要的64位小写十六进制",
  "evidenceFile": "research-contract-evidence.json"
}
```

文件顶层是记录数组。同一 origin 不可分配给多个 Profile。令牌仅从服务器环境读取；注册表与证据不要放进前端。凭证、地址或镜像摘要改变后，旧执行保持 unknown，不能在新身份下重放。

`evidenceFile` 相对注册表目录解析，需要实际锁定部署的集成报告，包含匹配的 `profileId`、`origin`、`imageDigest`、ISO `checkedAt`、验证过的 `retentionMs`（60 秒至 24 小时），以及 `checks` 中全部为 true 的 `profileIsolation`、`durableIdempotency`、`restartReplay`、`sessionIdentity`、`stopWaitsForExit`、`terminalOutput`。当前生产 9 个 Profile 的实际部署报告已生成并保存于服务器私有目录；新部署仍需生成与自身运行身份匹配的报告，不能复用其他环境的报告。报告是部署验证证据，不代表 Worker 自动审计了容器隔离。

Worker 在发送前保存执行请求、幂等键和运行身份。创建响应丢失时，只能在验证过的保留期内使用原键恢复；窗口过期、已知 run 消失或运行身份改变均保留 unknown。停止请求必须等到上游终态才释放 Profile。最终回执先持久化，再等待专项任务，避免上游状态过期导致丢失结果。

本地隔离容器、真实模型/行情研究链路，以及生产 9 个 Profile 的运行时注册与重启恢复均已验证。Worker 现按独立上游游标分批读取 Hermes SSE，事务保存主回答增量、工具进度与文本快照。事件缺口不触发重算；完成状态仍依据持久化终态回执、工具退出及最终回答。


## 可信数据与计算服务

`packages/server/src/data-service.ts` 通过私有工具网关提供 Market / Quant 服务；Hermes 可信工具适配层已完成真实链路联调，生产环境使用已验收的运行时注册表。本地单独运行 `pnpm worker` 仍需配置数据库、数据源、能力密钥及自身已验证的 Hermes 运行环境。服务测试通过 PostgreSQL 与真实 Python 子进程验证引用链路，行情测试输入只存在于 `tests/fixtures/synthetic-market.json`。

Market 参数仅包括股票、日期和 raw/split 口径。服务校验交易日和收盘后 20 分钟的保守等待窗口，获取额外 20 个交易日供指标预热；缺失行情保存为 null 并附带原因。数据、来源/feed、抓取时间、截至时间、口径、内容哈希和不可变版本一起保存，不宣称供应商历史数据是严格 point-in-time 数据。

Quant 参数只允许数据集或持仓版本引用；research、prediction、portfolio 分别允许指标、预测、组合计算。跨用户引用返回 not_found，模型价格数组或身份覆盖字段被拒绝。计算使用 Decimal，结果与输入版本、参数、代码版本、精度及时间一起保存。缺价组合保留部分明细，总值与权重为 null。

已提供 [Alpaca 日线适配器](https://docs.alpaca.markets/us/reference/stockbars)，固定使用官方只读行情地址，处理分页并保留原始数字精度。服务端工厂支持 `MARKET_PROVIDER=alpaca`、`ALPACA_DATA_KEY`、`ALPACA_DATA_SECRET` 和显式 `ALPACA_DATA_FEED=iex|sip`；没有默认供应商、自动切换 feed 或演示行情回退。用户提供的凭证已完成 IEX 历史日线读取验证；本机私有配置保存在 `.local/provider/alpaca.json` 和 `.local/provider/alpaca.env`（0600），后者供工具服务启动时加载。IEX 覆盖范围随数据的 feed 一起记录，不能当作全市场行情。完整联调证据见 [实施状态](docs/implementation-status.md)。

工具操作使用持久化 operation key 去重。取消会中断读取/计算，并等待 Python 进程退出；最终结果与操作状态在同一事务中保存。执行器丢失后保留 unknown，不自动重算；人工核对/解除流程仍待实现。

## 私有工具网关与 Canvas

```sh
DATABASE_URL=postgresql://alpha:local-development-only@127.0.0.1:55439/alpha_test pnpm tools
```

网关默认监听 `127.0.0.1:3410`，与浏览器 BFF 分离。`GET /tools` 返回当前角色的工具 Schema；`POST /call` 接收 `{ "tool": "工具名", "arguments": {} }`。两者都必须携带单次执行能力令牌 `Authorization: Bearer …`；写调用还需要由可信执行器产生且重试保持不变的 `X-Alpha-Operation-Id`。模型参数不包含 owner、task、Profile、令牌或上游地址。

配置真实运行环境注册表时，Worker 要求 `TOOL_CAPABILITY_KEY`：32 个随机字节的 64 位小写十六进制表示。该密钥必须跨重启保持不变，不能加入前端环境。Worker 在发送 run 前生成能力令牌，数据库保存摘要和 AES-256-GCM 密文；恢复同一执行时取回原令牌。

任务和所有工具/专项确认结束后，同事务清除能力令牌摘要和密文，旧令牌返回 401。unknown/stopping 执行仍保留原令牌用于核对，不按时间自动轮换。审计查询 `GET /api/tasks/{id}/audit?after=0` 使用用户登录会话，历史结果访问不依赖已退休的工具令牌。

适配器通过私有 `X-Alpha-Tool-Capability` 请求头传给运行环境。**原生 Hermes 不会自动把这个自定义头传给工具。** [可信运行时桥接](services/hermes/README.md)已提供针对固定版本的核心补丁和插件：令牌绑定原始 run，持久化队列只携带不含秘密的运行 ID，工具传输注入令牌与稳定调用 ID。已有本地、源码契约和完整 Hermes 进程验证；三个角色通过实际 Worker、数据库工具网关与 Python 计算保存了五类画布，创建响应丢失后的同键恢复也已验证。早期测试使用受控模型与行情夹具，后续已完成定制镜像、容器隔离、真实模型及 Alpaca 链路验收；其他来源的待接通状态见上文。注册表报告的 `checks` 除前述六项外，还必须包含经过实测的 `toolIdentityInjection: true`。生产报告已通过这一检查；新环境仍需验证，不能跳过门槛直接连接原生 Hermes。

工具包括行情、引用计算、授权数据/结果/用户选定持仓读取、专项任务分派与查询、研究解释保存，以及 Canvas upsert/patch/remove。只有 research 可以分派；prediction 和 portfolio 各最多一个任务，不允许递归。查询专项结果最多等待 25 秒，随后返回当前状态。

Canvas 接受首版五类组件及新增的 `forecast_result`，均使用已保存引用，股票、口径、时区、来源等信息由服务端事实生成。修改与事件同事务提交，重复调用不会重复创建，未改变内容不增加版本。新研究绑定新修订，旧任务产物进入历史，不覆盖当前画布。页面支持实时事件更新、顺序调整、刷新恢复和显式查看历史产物。

本地浏览器验证覆盖五类组件、页面关闭后完成计算再恢复、排序保存和两用户交叉访问拒绝。这些模拟测试与真实验收分别记录；后续真实 Hermes + Alpaca 链路和公网浏览器检查也已通过，详细结果见实施记录。

页面断线会自动重连同一任务，不重新提交研究。保留的事件按游标续传；事件缺失时恢复一致快照。浏览器离线、后台完成计算、重新联网后的自动恢复已通过测试，历史画布选择也会保留。

`pnpm hermes:prepare` 可为数据库中的用户生成三套独立 Profile 启动配置，具体输入和验证边界见[运行时接入说明](services/hermes/README.md)。配置生成、Compose 校验、真实容器部署和模型联调均已有验收证据；新生成的 Profile 仍需经过自身验证才能投入运行。

迁移 017 后，新注册会原子创建配置准备任务。`pnpm hermes:prepare-auto PRIVATE_SETTINGS PRIVATE_OUTPUT_ROOT LAST_PORT` 使用受限 Worker 账户按私有策略自动分配端口和生成配置；支持并发互斥、进程中断恢复与显式重试，保留原密钥。此命令先准备未验证配置；生产 Preparer 已启用 `HERMES_AUTO_VERIFY=1`，后续自动执行真实验收并发布注册表，失败时不会当作已验证运行环境使用。详见[自动准备](services/hermes/README.md#automatically-prepare-new-registrations)。


`pnpm hermes:supervise VERIFIED_REGISTRY PREPARED_DIRECTORY...` 提供独立宿主机 Profile 监管进程：按任务启动已验证的运行环境，空闲 5 分钟后停止，保留 home、容器和会话身份。Worker 在 ready 前不提交运行请求；异常退出且仍有已提交任务、控制结果不确定或容器身份改变时保留 unknown，不自动替换运行。新生成的 Compose 关闭 Docker 自动重启，已有配置不会被覆盖。数据库升级和权限见[运行账户](docs/database-access.md)，启动要求、已通过的 Docker Desktop 容器验收及生产部署边界见[Hermes 说明](services/hermes/README.md#on-demand-lifecycle-supervisor)。


Profile 发生不确定启停时，可用 `pnpm hermes:reconcile inspect VERIFIED_REGISTRY PREPARED_DIRECTORY PROFILE_ID` 查看状态；确认原容器已停止且无未核清执行后，使用 `confirm-stopped` 子命令并提供原容器完整 ID。恢复会写入不可变生命周期审计，不修改任务结果。命令与边界见 [Hermes 恢复说明](services/hermes/README.md#inspecting-and-reconciling-a-stopped-profile)，升级权限见 [数据库账户](docs/database-access.md#从迁移-013-升级)。


已知原生 unknown run 可由受信任宿主机使用 `pnpm hermes:reconcile interrupt-run VERIFIED_REGISTRY PREPARED_DIRECTORY PROFILE_ID TASK_ID` 核对。该命令会在确认无未核清工具/子任务后停止并重启原容器，按原 admission/generation 结束未知回合，分阶段保存与审计；不会创建新 run 或直接改写任务结果。条件、失败后继续方式与当前验收边界见 [运行核对说明](services/hermes/README.md#interrupt-a-known-unknown-run)。
