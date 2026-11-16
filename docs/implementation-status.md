# 实施证据与剩余工作

最新追加：2026-11-12 持续研究与预测闭环已完成本地实现与验收，尚未发布到生产。下方首版部署记录不代表本次扩展已经上线。

更新：2026-11-07。首版 M1–M8 已完成实现与分层验收，并部署到目标服务器。运行边界和逐项证据见下文。

最新进展：已接入用户指定的 Claude Code 模型 `glm-5.3-flash` 和 Alpaca IEX 行情。
本地三个真实角色容器、真实模型与行情、Worker/工具/数据库/计算及五类 Canvas
完整研究链路已通过。现已部署到 DigitalOcean，并通过阿里云 HTTPS 入口提供访问；
服务器容器与公网浏览器验收结果见文末最新记录。

## 初始计算模块阶段记录

- Python 环境与依赖已通过 uv.lock 锁定；`uv sync` 成功。
- `services/quant/alpha_quant/core.py`：Decimal 字符串验证、XNYS 交易日校验、MA20、Wilder RSI14、固定 20 日均收益预测与 60 点滚动评估、模拟组合估值/权重/集中度。
- `uv run pytest -q`：17 passed。覆盖节假日、行情缺口、指标预热/缺失重置、已知 RSI 数值、未来价格修改不影响历史预测、样本不足、缺价/不同日组合、零资产及十进制准确性。
- `uv run ruff check .`：通过。
- exchange-calendars 依赖内部产生 4 条 pandas 弃用警告，当前测试通过；未隐藏警告。

## 首版验收状态

| 阶段 | 当前状态 | 验收证据 |
|---|---|---|
| M1 登录与工作台 | PASS | 两用户权限测试、目标环境 HTTPS 登录、刷新后重开历史会话与手机布局通过 |
| M2 用户 × Profile | PASS | 目标服务器两用户六容器隔离、三个正式 Profile 协议验收、独立目录/密钥/网络与按需启停通过；新账户保持运营验收门禁 |
| M3 持久化任务 | PASS | 目标服务器真实容器受控故障验收覆盖丢失响应、Worker SIGKILL 同键接管、取消等待工具退出；公网真实研究通过 |
| M4 可信数据 | PASS | 目标服务器真实 Alpaca IEX、不可变数据/结果引用、Python Quant、权限边界及价格口径校验通过 |
| M5 三角色协作 | PASS | 目标环境真实模型/行情研究通过，公网三个正式角色完成研究、预测和模拟组合分析 |
| M6 Canvas | PASS | 五类组件真实生成并在公网浏览器恢复；既有布局/历史、陈旧更新和引用安全测试通过 |
| M7 恢复 | PASS | 目标服务器 Worker/Hermes 故障恢复测试通过；关闭整个浏览器后后台完成，重新登录及刷新重开恢复全部结果且不重复提交 |
| M8 失败与审计 | PASS | 目标服务器 115 项后端测试与容器故障验收通过；保留无退出证明时的 unknown 门禁，不将部分结果当作成功 |

计算模块与数据服务已通过私有网关接通完整 Hermes 进程，既有受控故障测试，也有真实模型与 Alpaca IEX 的完整本地三角色研究证据。交易日完成性、数据来源/口径、不可变引用与用户权限已有服务集成测试。人工行情只用于指定测试，不是运行时行情回退；目标部署和公网真实浏览器验收现已通过。

模型、行情与目标服务器均已按用户提供的配置接通。下面早期阶段记录保留当时状态，当前结论以上表和文末部署验收为准。

## PostgreSQL 任务与登录存储（阶段记录）

- 使用独立 Compose 项目 hermes-alpha-lab 和本机 55439 端口，未改动其他已有数据库或容器。测试数据库 alpha_test，每次测试创建独立随机 schema，结束只删除该 schema。
- 两份带校验和的迁移：用户、thread/workspace、任务、执行身份、消息、事件、不可变数据/结果、登录会话及 Profile 身份。
- TaskStore：按用户串行化任务变更、并发幂等提交、Profile 互斥领取、租约 generation、过期接管标记 unknown、子任务去重、取消传播、终态原子提交、REPEATABLE READ 恢复快照。
- AuthStore：scrypt 密码摘要、随机会话令牌（数据库只保存摘要）、过期/注销、原子限流、注册时三角色独立身份。
- `pnpm typecheck` 通过；真实 PostgreSQL `pnpm test` 15 项通过，未跳过数据库测试。包括多 Worker 争抢、跨用户访问、旧租约拒写、上游身份不允许替换、父子取消、失败子任务不能产生成功父任务、删除增量事件后快照恢复、登录和限流。
- 这些是存储层集成证据；Worker、HTTP 路由、SSE、权限最小化数据库运行角色、授权数据引用解析和真实 Hermes 尚未实现。Profile 身份行不代表已经创建容器。

## Web / BFF（阶段记录）

- Next.js 16.4.5、React 19.3.1、assistant-ui LocalRuntime、Zustand 与 Lightweight Charts 已安装并锁定。
- Web 已实现登录/注册、研究列表、统一提问、SSE 状态、取消、刷新后重新打开对话、持仓版本表单、自选股、桌面/手机布局。手机菜单可访问研究记录和持仓。
- BFF 使用 HttpOnly/SameSite Cookie、明确 APP_ORIGIN 的写请求验证、JSON 请求体上限、严格输入 Schema；所有业务查询校验用户归属。SSE 从一致数据库快照开始，随后读取持久化事件；会话注销/过期会关闭订阅。
- `pnpm typecheck` 通过。数据库/接口测试合计 18 项通过，包括跨站写入、owner 注入、持仓版本越权和布局版本冲突。
- Playwright 使用本机 Chrome：1 项完整流程通过（注册、建研究、提交、取消、刷新、持仓保存、手机菜单和无横向溢出），无 pageerror。截图为 test-results/workspace-desktop.png 与 workspace-mobile.png，仅本地验证产物。
- Canvas 已有五种组件视图，尚无真实数据链和服务端产物验证，因此不能把空画布浏览器测试视为图表/预测端到端通过。
- 独立 Worker 尚未实现；页面上的任务只能排队或取消，Profile 显示待连接。生产构建与本地开发流程不代表已部署上线。
- Next.js 自动生成 apps/web/AGENTS.md；相关框架指南已读取。迁移代码从 Web 运行时模块拆分，避免数据库迁移目录进入 Web bundle。

- 本轮最终 `pnpm build`（Next.js webpack 生产构建）通过，生成静态首页及动态 BFF 路由。浏览器验证后已停止本轮 dev 进程；PostgreSQL 开发容器保留。


## 独立 Worker 与 Hermes 适配器（最新）

- 新增迁移 004：任务调度时间、上游身份摘要、提交可能已发生的时间标记、持久化终态回执。
- `pnpm worker` 为独立 Node 进程，按用户和角色领取任务。每个步骤使用 generation 租约；等待上游或子任务时释放槽位。SIGTERM/SIGINT 停止领取并等待当前步骤完成。
- 每用户/thread/Profile 持久化独立 session UUID；请求在发送前保存。客户端只由服务端私有注册表解析，不接受模型输入的 URL、用户或角色覆盖。
- 创建响应丢失只在验证过的时间窗内重放原请求/原键；已知 run 不再调用 create。身份变化、未知执行、404/连接失败与窗口过期不会冒充成功或启动替代运行。
- 停止会调用上游 stop 并持续核对，直到取得终态；未发送的任务可直接取消。终态回执先归档，之后等待所有专项任务结束，再原子保存父任务状态、最终消息和应用事件。
- 注册表要求锁定镜像摘要与部署集成报告，且不允许多个 Profile 共享 origin。当前尚无真实部署报告/供应商凭证；这个门禁不等于隔离容器已创建或验证。
- 新增 12 项测试：真实 PostgreSQL + 模拟上游验证创建响应丢失、恢复接管、旧租约拒写、幂等窗口、身份变化、停止前后竞态、未知/丢失状态、缺失最终文本、等待子任务时回执保留；本地 HTTP 服务验证认证头/幂等键/身份校验/拒绝重定向；独立 Worker 子进程验证队列处理与 SIGTERM 退出。
- 适配器以 [Hermes Runs API 文档](https://hermes-agent.nousresearch.com/docs/user-guide/features/api-server) 为实现参考。模拟服务的契约测试不等于锁定 Hermes 版本的集成验证；尚未收集上游 token/tool SSE，也未接通真实专项工具调用。

- 最新验证：`pnpm typecheck`、真实 PostgreSQL 的 `pnpm test`（30 项，无跳过）、`pnpm build` 全部通过。迁移 004 已应用至本地独立 alpha_test；Worker 进程测试退出成功，未留下常驻 Worker。此前 17 项 Quant 与 1 项浏览器验证仍是各自阶段证据，本轮未重复执行。


## 可信数据与 Quant 引用链（最新）

- 迁移 005/006 新增持久化工具操作账本、数据/结果的操作关联、失败停止意图及取消原因。相同操作键重试返回原记录，改变参数拒绝；每根任务最多 100 次工具操作。未知操作阻止新操作，不能换键重算。
- DataService 从数据库校验 owner/task/Profile 执行身份与角色权限。Market 只接受 symbol/区间/口径；Quant 只接受数据集或持仓快照引用，禁止模型价格/身份/任意地址输入。
- Market 先取得交易日计划与 20 日预热区间，验证 OHLC 数值关系、顺序、重复、交易日、未完成日线，缺少的交易日写入 null + missing_bar。提前收盘日使用交易所日历；收盘后保守等待 20 分钟。不填充价格，也不把历史供应商修订视为已验证的 point-in-time 数据。
- 不可变 dataset 记录包括 symbol、USD、1d、XNYS、America/New_York、展示/完整区间、收盘/抓取时间、来源/feed、口径、schemaVersion、唯一版本 ID 与规范化 SHA-256。读取计算输入重新验证内容哈希，避免 JSONB 键顺序影响哈希一致性。
- Python stdin 桥只接收可信服务解析后的数据，独立进程不继承数据库和供应商凭证，有输入/输出大小、15 秒计算时限与 SIGTERM/SIGKILL 退出等待。公开工具接口不直接暴露这个内部桥。
- 计算结果保存输入引用/哈希/元信息、参数、Decimal 精度、代码版本与计算时间。组合缺价时 partial + null 总值/权重，预测标记探索性回溯基线。
- 可选 Alpaca 适配器固定官方只读行情地址，显式选择 feed 和口径，完整处理分页，不通过二进制浮点解析金融数字。未配置或鉴权/网络失败时显式失败，无供应商回退。本轮没有真实供应商请求。
- Worker 现在等待工具退出，并归档最终数据/结果引用；关键工具失败且本次任务没有后续成功调用时请求停止上游，退出后 failed；采集/读取类工具失败按覆盖缺失保留。审批等待标记 unsupported_interaction，截止时间保留 deadline_exceeded。工具执行器丢失为 unknown，真实核对/解除流程尚待实现。
- 工具服务尚未通过认证网关暴露给 Hermes，Canvas 尚未自动消费这些结果；内部集成测试不等于真实三角色端到端完成。

- 最新验证：真实 PostgreSQL 后端集成测试 **42 项通过**，Python **20 项通过**，`pnpm typecheck`、`uv run ruff check .`、`pnpm build` 通过。Python 日历依赖产生 8 条 pandas 弃用警告，未隐藏。迁移 005/006 已应用到本地独立 alpha_test。新数据链未进行真实供应商或浏览器 Canvas 端到端验证。

## Tool gateway and Canvas: latest evidence

- Migrations 007/008 bind tasks to workspace revisions and add encrypted execution capabilities, durable tool-call receipts, task artifacts and persisted ordering. Applied to the isolated local alpha_test database.
- The private gateway resolves owner/task/Profile from an execution-scoped token. Tokens never appear in prompts or model arguments; old execution calls never become calls for a newer task on the same Profile. Role schemas, reference ownership, selected holdings, dispatch depth and operation identities are enforced.
- Research dispatches at most one prediction and one portfolio task. The three-role fixture flow produces all five Canvas components with real PostgreSQL and Python calculations. The actual TaskWorker then aggregates saved result references. Gateway crashes after a data commit are reconciled from the durable data receipt.
- Canvas validates component/reference types and controlled display properties, limits each research revision to 50 artifacts, and atomically commits artifacts, versions, events and receipts. Old revisions archive without overwriting the current workspace. Layout no-ops preserve versions; ordering survives in task history.
- Browser state ignores duplicate/archived events and reloads snapshots on version gaps. The UI renders candles, volume, indicators, interpretation, prediction and simulated holdings; supports reorder and explicit historical views. Display percentages do not change stored financial precision.
- Backend tests: 48 passed, none skipped. This includes a real standalone gateway process with authenticated HTTP requests and SIGTERM shutdown, encrypted capability recovery, cross-user/role rejection, all component types, stale revision handling, order persistence and Worker result aggregation.
- Chrome: both existing workflow and new Canvas workflow passed. The Canvas test observes the first chart via SSE, closes the page while remaining calculations complete, reopens all five artifacts and final text, saves order and verifies reload, opens historical artifacts, checks mobile overflow and verifies a second browser user's requests to task/thread/workspace/dataset/result are all 404. Desktop/mobile screenshots were inspected.
- These tests use synthetic data and a controlled executor. Native Hermes does not propagate the custom capability header automatically. A trusted runtime tool bridge, isolated Profile containers, real market credentials, locked-version integration and unknown-state resolution remain unverified or unfinished. The new toolIdentityInjection evidence gate remains closed.
- Final checks: pnpm typecheck and pnpm build passed. The 48-test backend suite passed; an additional targeted Worker capability test then passed (49 verified test cases in total), proving the same encrypted capability survives a lost create response and is never inserted into model request content. The owned Web preview and standalone gateway test process exited cleanly; PostgreSQL remains available for development.

## 固定版本 Hermes 可信桥接（最新）

- 新增 `services/hermes`，固定上游 v0.23.0 的提交 `2bd378ed1b8d6bf2192cb26e0a55ff3091e6bd19`，记录镜像清单摘要与源文件 SHA-256。补丁工具先校验全部目标文件，任何版本偏差均拒绝修改。
- Runs 入口只接受 Worker 的固定请求形状，要求原生持久化幂等存储；同键不同能力令牌冲突。能力令牌在队列接纳前按 run 持久化绑定，原生接纳记录只保存不含秘密的 run ID，不写入会话设置或模型参数。
- 工具核心在真实调度调用处绑定 ContextVar 身份；不依赖会失败后放行的插件 middleware。缺少身份时处理器独立拒绝，拒绝并发调用时仍推进启动队列。解析阶段拒绝终端、文件、原生委派等非目录工具。
- 插件目录由服务端 Schema 生成，三种角色使用同一契约源。工具请求通过固定私有地址发送 bearer 和稳定 operation ID，禁用重定向、环境代理和自动重试；不向模型返回原始传输错误或回显令牌。
- Profile 私有绑定文件权限为 0600，新目录为 0700。该文件保存明文 bearer，必须使用独立私有持久卷；不能把文件权限测试当作容器隔离证明。
- 验证：完整后端 **49 项通过**；Python **26 项通过**（20 项计算 + 6 项桥接），仍有 8 条日历依赖弃用警告；显式固定版本源码审计 **4 项通过**，使用上游真实 SQLite 幂等实现验证重新打开后的重放、变更令牌拒绝、接纳前绑定及内置工具拒绝。`pnpm typecheck`、`uv run ruff check .` 通过。
- 已提供定制镜像构建文件，但尚未构建镜像或启动真实 Hermes。源码审计使用受控边界，不代表完整进程、子进程上下文、真实模型、网络隔离或真实重启验收通过。`toolIdentityInjection` 部署门槛保持关闭；Profile 自动部署与 M7/M8 剩余工作继续进行。
- 已启动本地定制镜像构建，官方摘要解析成功，目前正在下载基础镜像层，尚未得到构建通过结果。保留同一构建进程继续观察，不因下载等待启动重复构建。

## Profile 启动准备与自动重连（最新）

- `pnpm hermes:prepare` 从 PostgreSQL 读取指定用户的三种 Profile 身份，生成独立 home/网络/运行 API 密钥/只读凭证挂载和本机回环 API 端口，附带 CPU、内存、PID 和日志限制。不会覆盖已有目录或轮换已有凭证，不会把状态写成 ready。
- 启动程序验证 home 的 owner/Profile/role 标记，拒绝无标记或其他身份的目录；保留记忆与运行绑定，只写非秘密配置。启动 gateway 前过滤继承环境，数据库、Market 和 Worker 加密密钥不会传入。当前模型配置支持显式 OpenAI-compatible custom provider。
- 两用户六 Profile 的生成与权限测试通过；使用本地测试用户生成的三 Profile 文件通过 `docker compose config --quiet`，未启动这些容器。生成的 registry 没有通过报告，Worker 仍会拒绝连接。
- 浏览器 SSE 现在会自动重连原任务、携带 Last-Event-ID、丢弃重复事件，发现序号缺口重新同步；空闲超时与浏览器 offline/online 事件参与恢复。根任务终态停止订阅；专项终态不会提前结束主订阅；401/403/404 明确停止重试。
- BFF 在事件保留范围内重放后续事件；事件缺失/清理时返回一致快照。注销会关闭现有订阅并拒绝重连。后台快照同步保留用户显式打开的历史画布，不用旧快照覆盖更新版本。
- Chrome 离线测试验证：首个图表出现后断网，后台继续完成五类产物；联网后不刷新页面即可恢复图表和最终回答，研究 POST 只有一次。随后关闭/重开、排序、历史视图、移动端及第二用户越权检查仍通过。
- 最新验证：完整后端 **62 项通过**，Python **30 项通过**（8 条已有日历依赖警告），固定源码审计 **4 项通过**，浏览器 **2 条流程通过**，类型检查、Ruff、Prettier 和 `pnpm build` 生产构建通过。验证用 Web 进程已退出。
- 第一次定制镜像构建已明确失败：Docker Desktop/containerd 提交下载层时报告 `rename ... no such file or directory`，尚未执行项目源码补丁。未清理其他项目镜像或重启 Docker；保留缓存启动了一次新构建重试，结果待确认。真实 Hermes 运行、供应商/模型联调、按需容器调度和 M8 收尾仍未完成。
- 重试进程仍在下载官方基础镜像层，尚无构建通过证据；构建输出写入忽略目录 `.local/hermes-build.log`。磁盘检查显示宿主机有可用空间，未对 Docker 全局镜像、卷或缓存做清理。

## 工具退出核对与状态审计（最新）

- 修复工具超过 45 秒被观测为 unknown 后，原执行器实际返回也无法结束操作的问题。只有最初取得执行资格的调用在 awaited work 返回后保存退出回执；重放请求不执行工作，没有回执的失联操作仍保持 unknown。
- 迁移 009 新增不可变 tool_exit_receipts 与 execution_audit。退出回执、数据事实、操作终态及状态审计同事务提交；保存失败全部回滚。任务创建、状态变化、取消/失败意图与工具状态变化自动追加审计，不记录提示词或令牌，也不补造旧记录。
- 停止信号不等于退出。测试中原执行器在收到 abort 后继续被阻塞，Worker 保留 unknown、后续研究保持排队；实际退出后，Worker 结合已有上游终态回执结束原任务并释放 Profile。相同操作键没有额外执行。取消后仍到达的有效数据保留为 partial。
- 新增认证接口 GET /api/tasks/{id}/audit?after=0，根任务包含专项记录，专项查询仅自身；每页 200 条，字符串 bigint 游标。跨用户返回 404；UPDATE、DELETE、TRUNCATE 被拒绝，SSE 事件清理不删除审计记录。
- 验证：真实 PostgreSQL 完整后端 **67 项通过、无跳过**；类型检查、Prettier 和 `pnpm build` 通过。迁移 009 已应用本地独立 alpha_test。Python 与浏览器实现未修改，本轮未重复其此前 30 项和 2 条流程验证。
- 边界：这条恢复路径要求原执行器仍能返回并提交。进程彻底丢失、没有退出回执时的外部核对与管理员恢复尚未完成；Hermes 完整运行验证、按需容器调度、供应商/模型联调、剩余限额与数据库最小权限仍待完成。Docker 重试 session 3186 已再次确认存活，仍在缓慢下载基础镜像层，未取得构建成功证据。

## 队列配额、保留清理与令牌退休（最新）

- 每用户最多 10 个未结束根研究，queued/running/stopping/unknown 均占名额；在用户锁内做原子检查。超出返回 429 queue_full，不变更消息或画布；重复键先核对/返回原任务，专项任务仍可在满额时派发，取消排队任务释放名额。页面提供对应中文错误提示。
- Worker 启动及每分钟执行有界清理，每类每轮最多 1000 条：7 天前的增量事件、过期登录会话、过期认证限流桶与旧版本遗留终态能力令牌。多 Worker 由 schema 范围 advisory lock 协调，跳过行锁，清理异常不停止任务调度。迁移 010 增加筛选索引，已应用本地独立 alpha_test。
- 任务终态与能力令牌摘要/密文清除原子提交，网关拒绝所有终态任务令牌；历史结果继续由 BFF 登录身份读取。running/stopping/unknown 身份和令牌不受清理影响。
- 验证：完整后端 **70 项通过、无跳过**，类型检查、Prettier、生产构建通过。覆盖 20 个并发提交仅接纳 10 个、同键重放与冲突、不同用户独立配额、专项分派、取消释放名额；1005 条过期记录分两批清理、快照/事实/审计保留、锁竞争和 unknown 身份保留。另加强并重跑独立 Worker 测试，实际子进程启动清理过期事件、继续处理队列并以 SIGTERM 正常退出。
- 定制 Hermes 镜像重试 session 3186 仍确认存活，基础层下载缓慢，尚无构建成功。真实运行与供应商/模型联调、按需 Profile 生命周期、失联进程外部核对及数据库最小权限仍未完成；本轮没有将这些门槛标为通过。

## 数据库服务权限（最新）

- 迁移 011 新增受控 owner 行锁函数，避免运行服务为了加锁而获得 users 更新权限。审计触发器改为固定搜索路径的 SECURITY DEFINER，运行账户不能直接追加或修改审计。固定保留清理也收进受控函数，Worker 无登录会话读取/修改与任意删除权限。
- `pnpm db:access` 为 Web、Worker、Tools 生成独立随机密码的登录角色，显式授予表/列/序列/函数权限，不给超级用户、建库、建角色、复制、BYPASSRLS、DDL 或任务/事实/审计删除权限。工具服务只能读取执行令牌摘要，不能读取密文；Web 不读 executions，Worker/Tools 不读用户密码或登录会话。
- 安装器要求只含本项目对象且表/函数为当前部署账户所有的 schema，拒绝其他应用对象、既有角色和既有输出目录。私有目录 0700、连接文件 0600；授权提交失败时保留生成文件以核对实际状态，不自动轮换凭证或覆盖部署。
- 已在本地独立 alpha_test 应用迁移 011，创建 alpha_runtime_web / worker / tools，私有文件保存于忽略目录 `.local/database-access`。启动 Web 使用真实受限 Web 登录账户，不使用迁移账户。
- 验证：真实 PostgreSQL 完整后端 **73 项通过、无跳过**；三登录账户实际完成三角色工具流程、五类 Canvas 与归档，权限拒绝及临时表/search_path 攻击测试通过；Web 使用受限账户的 **2 条 Chrome 浏览器流程通过**；类型检查、Prettier 和生产构建通过。验证 Web 进程已停止。具体边界见 [数据库运行账户](database-access.md)。
- 该边界是服务职责隔离，owner 行级授权仍由 BFF/工具网关执行，不宣称数据库 RLS。真实生产数据库部署尚未进行。Hermes 构建 session 3186 仍确认运行且缓慢下载；真实模型/供应商联调、按需容器生命周期、失联进程核对与上游事件采集仍未完成。


## 完整 Hermes 进程与数据库工具链（最新）

- 已取得固定提交的完整源码，独立安装 Python 3.14 与上游冻结的 core/messaging 依赖；验证补丁目标、关键运行时源码、锁文件和桥接副本。测试只启动该目录的绝对 Hermes 命令，使用独立临时 home 和过滤后的环境，不使用用户已有 Hermes 登录。
- 实际运行发现默认延迟工具发现会把角色目录换成 tool_search/tool_call 包装器，继而被可信解析器拒绝，导致模型能回答却没有调用任何项目工具。启动配置现已显式关闭该功能及 connectors；测试核对真实工具请求，不能只凭最终文本判断成功。
- 完整进程测试覆盖三个角色的实际授权头、稳定操作 ID、角色工具目录、同键重放、改令牌冲突，以及 research 停止后重启读取原最终结果，无重复工具调用。这是原生进程内执行线程路径，不宣称可选 managed-subprocess 路径也已验证。
- 新增显式 system_integration：三个实际 Hermes gateway 通过真实 HermesClient、TaskWorker、HTTP 工具处理器、PostgreSQL 和 Python Quant 完成两专项与五类 Canvas。模型决策和行情是测试夹具，所有 dataset/result/child 引用均来自真实服务返回。
- 测试在上游接纳 root 创建后丢弃响应，重建 Worker 对象，只凭数据库状态恢复：四次创建请求对应三个 native run，行情只执行两次；三个 Profile/session 独立，最终回执与能力令牌退休、审计、跨用户结果拒绝均有断言。research 同次返回两个工具调用，实际完成两个画布写入；固定版本的原生调度器将插件工具视为串行屏障，因此不把此证据当作单 Profile 内并行调度通过。Worker 对象重建不等于 OS 进程崩溃测试。
- 进程测试的准备命令和边界见 [Hermes 接入说明](../services/hermes/README.md)。没有生成生产 registry 通过报告，容器隔离、真实供应商、按需生命周期、失联进程外部核对与上游事件采集仍未完成。
- Docker 构建 session 3186 已再次轮询确认存活，仍下载基础层。该构建上下文早于关闭延迟工具发现的修复；即使完成，也需要从最新源码重新构建并验收后才能注册，不能把旧构建当作修复后的镜像。
- 最终验证：基础 Python 30 项、源码审计 4 项、完整进程 3 项、数据库/Worker 全链路 1 项，合计 **38 passed**；8 条现有日历依赖警告。Ruff、测试 Node peer 的严格类型检查及 Prettier 通过。测试进程已退出；此前后端 73 项、浏览器 2 条与生产构建是各自阶段证据，本轮未修改这些生产 TypeScript/UI 模块，未重复执行。


## 上游事件归档、文本恢复与接纳竞态（最新）

- 新增迁移 012：执行独立上游游标/缺口/关闭标记，以及任务连续文本快照和稳定消息 ID。游标、文本和应用事件同事务保存，generation/租约与 run 身份校验拒绝旧收集器；重放不重复追加。主回答最多 500,000 字符，专项文本不进入主消息。
- HermesClient 读取固定版本原生 SSE，每步最多 256 条、1 MiB、0.5 秒；验证 run/seq/id、UTF-8 和帧大小，未完成帧不推进游标。只保存主回答增量及工具开始/完成的受控字段，不保存原始预览、参数、推理或供应商错误。
- 原生只保留内存中最近 1,000 条事件，进程重启后可能无法重放。截断/缺口记录 event_history_gap，后续不拼接缺段文本；上游事件结束不等于应用任务结束。Worker 先归档状态查询所得终态回执，再分批读取余下事件；注册表不可用或上游状态过期不会让已保存回执失效，最终仍等待工具和专项退出。
- 浏览器刷新后读取文本前缀，再接续后续增量；最终回答替换前缀，消息 ID 稳定。已用真实受限 Web 数据库账户验证刷新前半段、刷新后后半段，以及离线恢复、五类 Canvas 和既有工作流。
- 实际 Hermes 联调暴露创建响应丢失后的接纳竞态：任务临时 unknown 时原进程已抵达工具入口。现仅对在数据库接纳之前明确回滚的事务等待最多 10 秒核对；不持锁等待、不执行供应商/计算、不重发任务，取消/失败立即拒绝。退出回执不走此等待逻辑，既有 unknown 工具恢复测试继续通过。
- 本地 alpha_test 已应用迁移 012，并只向既有 alpha_runtime_worker 增加新列 UPDATE 权限；新安装器包含对应权限。升级方法见 [数据库账户](database-access.md)。没有修改已应用迁移、其他数据库或其他服务凭证。
- 已通过：真实 PostgreSQL 后端 **79 项，无跳过**；真实三角色 Hermes 全链路（实际文本增量与工具事件、三执行游标闭合且无缺口、专项文本隔离）；Chrome **2 条流程**；类型检查、Prettier、Ruff 与生产构建。基础 Python/源码审计未改动，本轮未重复此前的对应验证。验证 Web 进程已退出。
- 真实供应商、隔离容器、按需 Profile 生命周期、运行中崩溃/失联进程核对仍未完成；不生成生产通过报告。Docker 基础镜像下载仍需继续观察，当前构建上下文仍早于上一轮 bootstrap 修复。


## Profile 按需生命周期（最新）

- 新增独立 `pnpm hermes:supervise`，只管理明确传入的已准备目录和已通过原有证据门槛的 registry。校验 owner/role/Profile、固定自定义镜像 ID、配置与凭证指纹、home 的文件系统身份；Web/Tools/Hermes 无 Docker 控制入口。
- 新增迁移 013。存在该角色未结束任务时启动，空闲 5 分钟后停止；保留原容器/home/session，不拉取镜像、不构建、不强制重建、不删除卷。生成配置改为 restart=no，防止 Docker 绕过未知执行核对自动重启。旧配置不自动覆盖。
- Worker 在同一 owner 锁下核对 managed Profile ready 后才写 submission_started_at，启动期间只等待，不创建上游 run。停止事务先关闭提交入口，再操作 Docker；期间新到达的任务等待停止与再启动完成。
- 多监管进程通过数据库 session advisory lock 互斥。已提交且未结束的执行随容器退出时保持 unknown，不自动启动替代；容器 ID 变化、停止结果不确定及监管进程遗留的停止意图均阻止自动接纳新运行。人工核对/解除命令仍待实现。
- 控制器测试验证首次启动、复用容器与会话、空闲停止、双进程互斥、停止与新任务竞态、异常退出和不确定控制结果。驱动契约测试拒绝外部 owner、Docker socket 额外挂载、额外能力、配置漂移和自动重启策略；这些使用受控 Docker 返回，不冒充实际容器部署验收。
- 本地独立 alpha_test 已应用迁移 013，仅增加既有 Worker 的 Profile 生命周期列更新权限；真实受限账户测试通过。页面区分启动中、停止中、休眠和待核对，并在页面可见时刷新状态。
- 已验证完整后端 **86 项通过、无跳过**；随后新增“创建响应丢失且容器退出”用例，并重跑生命周期/Worker **25 项通过**（累计 87 个已验证用例），真实数据库权限 **3 项**与 Chrome **2 条流程**通过。类型与格式检查通过。实际定制镜像仍未构建成功，容器隔离、挂载权限、按需启停验收、新用户配置自动准备、真实供应商及失联执行核对仍未完成；goal 不标记完成。


## Profile 核对入口与生命周期审计（最新）

- 新增 `pnpm hermes:reconcile inspect` 只读状态与 `confirm-stopped` 核对命令，沿用已验证 registry 和私有准备目录。检查 owner/role/Profile、部署指纹、原容器完整 ID、实际隔离配置及停止状态；不接纳替代容器或漂移配置。
- 核对与监管进程共用每 Profile 的数据库 session advisory lock，Docker 检查期间不持有 owner 事务。检查前后在 owner 锁内核对执行/工具账本，存在已提交未终结执行、unknown 工具或无响应调用时拒绝解除。排队的新任务可以进入，Worker 在核对完成前不能提交。
- 仅当原容器明确停止且无未核清执行时清除 Profile 错误、置为 stopped；不启动/停止容器、不改写任务结果、不补造退出回执。监管进程之后可按需求复用原容器。创建响应丢失后没有 run ID 的任务仍阻止解除。
- 迁移 014 新增不可变 `profile_lifecycle_audit`，状态/错误/容器和部署绑定变化与审计同事务，记录实际数据库登录，不记录凭证或提示词。固定搜索路径的特权触发器追加；运行账户无直接审计写入权。该记录不是具体操作人员身份或独立 Docker 证明，升级不补造旧记录。
- 本地独立 alpha_test 已应用迁移 014，并仅增加既有 Worker 对新审计表的 SELECT 权限。新安装器同步维护对象清单与授权；Web、Tools 不获得该运维审计权限。
- 验证：完整 PostgreSQL 后端 **92 项通过、无跳过**，包含 **12 项生命周期测试**与真实三服务账户权限验证；类型检查、变更文件 Prettier、`pnpm build` 通过。命令缺失必要参数时以固定错误代码拒绝。Python/浏览器实现未变更，未重复先前对应验证。
- Docker 构建 PID 33436 已检查确认仍存活；下载日志尚无完成结果，仍未得到定制镜像。此轮没有启动实际 Profile 容器或生成部署通过报告。该恢复入口只处理已停止原容器的控制不确定性；失联任务/工具的外部退出证据与结算、新用户自动准备、真实供应商、容器隔离和运行中崩溃验收仍需继续，goal 保持 active。


## Worker 进程崩溃接管与真实链路取消（最新）

- 将现有生产 Worker 扫描、维护和调度循环提取为 `runWorkerService`，生产入口继续负责已验证 registry、数据库连接与进程信号。测试子进程只注入本地测试运行绑定并调用同一循环；没有新增生产绕过证据门槛的环境开关或虚构镜像报告。
- 实际三个固定源码 Hermes 进程联调：本地代理在 Hermes 接纳研究创建后扣住响应，测试对 Worker 子进程发送 SIGKILL 并确认退出，再启动不同 PID 的 Worker。保留真实 30 秒租约，不通过修改数据库缩短等待；提前不能重放，到期后原请求/session/幂等键/能力令牌和 native run 均保持一致。四次研究链创建请求仍对应三个原生 run，行情仍只执行两次，五类 Canvas、专项、文本事件、审计与跨用户拒绝继续通过。
- 追加两个取消场景：分别阻塞在行情返回前和实际 Python 数据校验后。收到原生 stop 应答后，应用仍等待工具、后续研究仍排队、工具退出回执仍不存在。原执行器返回后才结束 cancelled；未校验行情不保存数据，已校验迟到事实以 partial 结果和 cancelled 回执保留。测试中的后续排队任务取消后没有创建上游运行。
- 新 Worker 最终通过 SIGTERM 正常退出；测试独立 schema 和自有进程清理。故障代理与延迟执行器仅用于测试，模型决策和行情仍是受控夹具，不能据此宣称真实模型、行情供应商或 Docker 隔离已验收。
- 验证：增强后的真实 Hermes 全链路 **1 项通过（包含崩溃接管和两类取消场景）**；Worker 专项 **18 项通过**，生产与测试 peer 严格类型检查、Prettier、Ruff 和生产构建通过。此前完整后端 92 项是上一阶段证据，本轮未重复整套。进程组清理增强后再次运行通过（51.30 秒），已检查无遗留测试 peer/Worker 进程。
- 尚待完成：Hermes 本身运行中崩溃/失联后的外部核对、新用户配置自动准备、真实模型与行情、定制容器启停隔离验收。Docker 构建 PID 33436 此轮仍检查存活，Docker daemon 可正常响应，但下载日志无完成证据；不因客户端进程存在而认定下载有进展。目标仍覆盖完整 M1–M8，保持 active。


## Hermes 运行中崩溃与容器启动观察（最新）

- 新增实际网关进程崩溃测试：在模型请求阻塞时 SIGKILL research 进程组并等待退出，再使用同一 home 启动。固定版本 canonical authority 返回原 admission/generation 的 unknown；同键返回原 run，不重复模型/工具，后续同会话 run 保持 queued。不能用兼容幂等存储的 interrupted 分支推断 canonical 行为。
- 测试确认原进程退出且从未启动工具后，显式调用原生 resolve-unknown；错误 admission/generation 被拒绝，精确重复核对幂等。原回合以 interrupted 结果终结（API 映射 cancelled），仅后续回合执行工具。三个恢复关键源码文件已与固定提交原始归档逐字核对，并加入完整运行测试的 SHA-256 清单。
- 迁移 015 为 Profile 增加原样 Docker 启动时间观察，为执行增加首次提交时的容器 ID/启动时间/部署指纹。观察变化进入不可变生命周期审计，重试不覆盖原提交记录，旧任务不补造历史；托管 ready 但缺少启动观察时不提交。时间变化本身不是原 run 的退出证明，没有据此开启自动恢复。
- 本地独立 alpha_test 已应用迁移 015，只增加既有 Worker 所需列更新权限。新安装器已同步；没有修改其他项目或数据库。
- 完整后端 **94 项通过、无跳过**（包含重启前后原观察保留、缺少观察拒绝提交、Docker 纳秒时间和受限服务账户）；固定版本完整进程测试 **4 项通过（57.06 秒）**，覆盖三角色与新增崩溃核对场景；类型检查、Prettier、Ruff 和生产构建通过。测试进程已退出。
- Docker daemon 和 BuildKit history 都确认构建 scvobyrs028phcme29r0e0vux 仍为 running；直接读取该构建日志也停在基础层下载，未获得终态或镜像成功证据，不启动重复构建。应用级失联 run/工具恢复入口、新用户自动准备、真实供应商、实际容器验收仍未完成，goal 保持 active。


## 应用级已知 unknown run 核对（最新）

- 新增 `pnpm hermes:reconcile interrupt-run VERIFIED_REGISTRY PREPARED_DIRECTORY PROFILE_ID TASK_ID`。它是明确的运维中断操作，不是自动重试：要求原容器/部署/执行绑定、可查询的 canonical unknown run，且未退出工具、无响应副作用调用、未结束子任务及同 Profile 的其他已提交任务全部核清。
- 先持久化恢复意图并关闭提交入口，再停止并验证原容器退出、重启同一容器、核对新启动状态和原 admission/generation，最后调用原生 resolve-unknown 并读取匹配终态。仅启动时间变化不构成退出证明。全过程不创建新 run，不写应用任务结果或工具回执，Worker 继续按实际终态与账本归档。
- 迁移 016 保存 stopping/stopped/starting/started/resolving/resolved 六个阶段。每个控制动作之前提交意图，外部调用期间不持 owner 事务；与监管进程共享每 Profile advisory lock。身份和已有观察不可改写、阶段不能回退、记录不可删除，恢复阶段通过特权触发器追加不可变任务审计。Web 的 owner 审计可读 recovery_id，数据库服务登录只在运维证据中保留。
- 停止、启动和原生核对确认丢失均有恢复测试：重复命令从已保存步骤重读事实继续，已观察到停止/启动不重复对应控制，已读到终态不再发送原生核对。未知工具、跨 owner/部署/运行身份、变化的 admission 均拒绝；未结束核对保持 run_reconciliation_pending，监管进程和 confirm-stopped 不绕过该暂停。
- 真实联调发现 admission ID 是原始 hex 字符串，不能按应用 UUID 解析或通过 PostgreSQL uuid 列规范化；现按不透明字符串保存并原样回传。与真实固定版本 Hermes 进程、PostgreSQL、HermesClient、RuntimeRecovery 和 Worker 联调通过：初始进程 SIGKILL 后保留原 run，应用实际停止/重启进程组完成核对，六项审计齐全，最终 interrupted、令牌退休、事件缺口明确，没有重复模型/工具执行。测试进程驱动不等于生产 Docker 驱动验收。
- 本地独立 alpha_test 已应用迁移 016，只给既有 Worker 增加恢复表 SELECT/INSERT 和阶段/观察列 UPDATE；新安装器同步对象清单和授权。真实受限 Worker 完成恢复并由正常 Worker 归档，Web/Tools 不获得该运维表权限。
- 最终验证：完整后端 **103 项通过、无跳过**；两项真实 Hermes 系统联调 **2 项通过（74.76 秒）**，包括上一轮 Worker 强制终止与两类取消，以及本轮应用恢复；类型检查、测试 peer 严格检查、Prettier、Ruff、生产构建通过。缺少命令参数按固定错误拒绝，测试进程均已退出。
- 当前入口不处理缺失原 run ID、不可查询的运行环境、替代容器，或没有退出证据的失联外部工具；这些继续保留 unknown。新用户自动准备、真实模型/行情与 Docker 容器验收仍需完成。构建 PID 33436 此轮检查存活，但基础层日志无新增完成证据，不宣称镜像构建成功；goal 保持 active。


## 新用户 Profile 配置自动准备

- 迁移 017 为新注册创建持久化准备任务，与用户及三角色身份同事务提交；入队失败会回滚整个注册。不自动登记旧用户，避免重新生成已有手动部署的身份/凭证。
- 新增 `pnpm hermes:prepare-auto PRIVATE_SETTINGS PRIVATE_OUTPUT_ROOT LAST_PORT`，支持常驻扫描、`--once` 和 `--retry OWNER_UUID`。只读取明确提供的私有模型策略，使用受限 Worker 数据库账户。单 schema 绑定单宿主机、私有输出根目录及不可变策略；配置、模型密钥、镜像、端口池或根目录变更会拒绝，不静默轮换。
- 每 owner 使用 PostgreSQL session advisory lock；端口分配在独立短事务中串行预留三个端口，与新手动准备共享账本，避免相邻区间重叠。端口耗尽进入 blocked，不自动回收。数据库预留不等于占用操作系统 socket，也不发现升级前/其他应用的端口，部署仍需实际检查。
- 每个请求先原子保存私有密钥种子，再在私有临时目录写入并 flush 完整配置，原子重命名发布。崩溃恢复保留原端口、三份 API 密钥和文件；已发布目录只在文件内容、权限及身份全部匹配时接纳，缺失种子、符号链接、配置漂移或根目录变化均阻止自动修复。遗留未发布临时目录不自动删除。
- 手动与自动准备共用配置渲染器，手动任务标为 manual 并从自动扫描排除。已准备配置仍是 prepared_unverified，数据库 Profile 保持 unprovisioned；没有启动 Docker、创建通过报告或绕过 registry 验收。当前未提供真实私有策略，因此没有为现有用户启动自动准备服务。
- 新增真实 CLI 子进程故障测试：配置发布后在准备状态提交处阻塞，SIGKILL 客户端并终止其测试专属数据库连接，确认 preparing 仍持久化，再启动不同 PID 恢复。特别确认杀客户端不等于 SQL 回滚：原连接可能继续提交，因此测试明确验证所需数据库故障边界，不靠子进程退出推断事务结果。
- 真实受限 Worker 完成自动准备；Web 只有 INSERT(owner_id)，不能读取策略/端口/准备表或指定准备状态，Tools 无新增权限。请求身份、已绑定端口/配置摘要、策略和预留记录有不可变约束；既有本地 alpha_test 已应用 017 和最小增量授权，没有重新创建账户或轮换密码。
- 最终验证：完整后端 **111 项通过、无跳过**；生产/测试类型检查、变更 TypeScript/JSON 格式检查、`pnpm build` 通过。测试覆盖注册回滚、并发准备、原密钥稳定、手动与自动端口冲突、耗尽、策略/权限/符号链接/文件漂移、缺失种子及真实进程中断恢复。此轮未修改 Python、浏览器或 Hermes 执行器，未重复其上一阶段验证。
- Docker 构建 `scvobyrs028phcme29r0e0vux` 已由 BuildKit history 确认为 **failed**，结束于 2026-11-07 11:32:44 +08:00，原 PID 已不存在。再次失败于 containerd 基础镜像层提交时 rename ENOENT；没有成功镜像，也未自动启动第三次构建或清理其他项目资源。实际容器部署/隔离/启停、真实模型与行情、无退出证据的外部工具恢复仍未完成，完整 M1–M8 goal 保持 active。


## 定制镜像与六个真实 Profile 容器验收

- 改用项目专属 `alpha-lab-hermes` BuildKit 容器和独立缓存后成功构建定制镜像；未重启 Docker、修改全局镜像源或清理其他项目资源。最终构建 `6r7mkvwwb17ceg2h7i8t3nb44` 已退出 0，`docker image inspect` 确认本地镜像 `sha256:4396d6643f6e46c5d5d968facacea3d505982c1621ddaaa7f6d8b7811d4d27ed`。前面的下载/层提交失败是历史记录，不再是当前阻塞。
- 镜像构建在任何补丁写入前验证全部 28 个锁定文件（3 个补丁目标、25 个未修改依赖），覆盖原生接纳/恢复、依赖锁、dotenv 和容器启动链。审计逐个修改未打补丁依赖，确认拒绝构建且不留下半写补丁。
- 真实容器暴露并修复两处启动问题：上游生成的 `.env` 密钥覆盖挂载身份导致 401；裸 `gateway run` 转交 s6 子服务后丢失角色/模型密钥/绑定路径，导致工具插件不可用和 Runs 500。bootstrap 原子写入私有权威 API 配置；模型密钥只在过滤后的环境中传递。保留上游 entrypoint、s6 初始化和降权，替换原生 gateway 自动登记 hook，并以前台 `--no-supervise --external-supervisor` 启动受管 gateway，生命周期由宿主应用控制。
- 新增 `tests/hermes/container_integration.py` 与 TypeScript 准备/控制端：真实注册两用户、生成六份配置并启动六个容器，验证固定镜像、owner/Profile 标签、独立 home/只读凭证挂载、独立网络、loopback 端口、UID 10000、能力限制与 CPU/内存/PID 限额。每个角色用自己的记忆和工具 capability 完成原生 run，其他 Profile API 密钥被拒绝，模型请求没有其他记忆或运行凭证。
- 正常停止/启动后，同键请求返回原 run，不重复工具调用。直接 SIGKILL gateway 导致容器退出，未被 s6 或 Docker 自动替换；随后从残留原生 running 状态启动也不生成独立 s6 gateway。真实 DockerProfileDriver 检查六容器，ProfileSupervisor 以测试专用 1 秒空闲阈值停止 research 容器，真实 TaskStore 入队触发同容器启动并记录 ready/新启动观察；其他五容器持续运行，原结果可读。
- 扩展为真实 Worker 接纳模型回合后 SIGKILL gateway：容器退出，普通监管连续检查仍保持 unknown、不自动重启，API 不可查询时恢复入口明确拒绝。测试显式恢复原容器 API（模拟运维前置操作，不是新增自动恢复能力），随后真实 RuntimeRecovery/DockerProfileDriver 完成停止原容器、确认退出、再次启动、匹配原 admission/generation 并取消 unknown run。Worker 按原生终态归档 interrupted，原 run/幂等键不变、令牌退役、事件缺口记录、六步审计通过；模型只调用一次且没有新增工具执行。恢复控制器本身没有伪造任务终态。
- 三角色真实进程系统测试增加 NVDA 研究后重启 gateway、只发送“把它改成一年”的追问：保留原 session，从原生历史获得标的和截止日期，真实数据/计算处理器保存一年区间与新产物；新任务权限独立，旧产物保留。
- 本阶段验证：默认 Python **32 项通过**（8 条已有交易日历依赖警告），源码审计 **6 项通过**，修复后真实 Hermes 系统测试 **2 项通过（84.72 秒）**，最终六容器、真实生命周期与运行中故障恢复测试 **1 项通过（101.48 秒）**；Ruff 与新增测试端严格 TypeScript 检查通过。此前完整后端 111 项和生产构建通过；此阶段未修改 TypeScript 业务代码。测试容器/子进程已退出清理，项目专属构建缓存保留。
- 边界：容器测试使用 Docker Desktop 与受控模型/工具响应，不冒充真实供应商数据。直接从准备文件建立测试内绑定，没有生成通过 registry 或启用生产 Profile。生产 Linux 目录权限/网络、真实模型与行情、不可查询 API 的恢复操作流程、没有退出证据的失联外部工具仍待完成。完整 M1–M8 goal 保持 active。


## Linux 私有 home 权限与初始化拒绝

- 使用 Docker daemon 内部 Linux 卷复现宿主 UID 不同的启动失败，排除 Docker Desktop bind mount 的 UID 翻译影响。上游已将 home 目录改为 UID/GID 10000、权限 0700，但应用身份标记仍为准备用户 17001、权限 0600；降权后目录可访问、标记不可读，bootstrap 明确拒绝启动。该问题不是网络或模型调用失败。
- 新增 `alpha_hermes.home_init`，在既有上游 home 初始化后、应用降权前读取固定挂载 spec，校验完整 owner/Profile/role 身份，再将该标记交给容器实际 hermes UID/GID，保持 0600。通过目录 fd 和 O_NOFOLLOW 打开，拒绝符号链接、硬链接、非普通文件和超限文件；对已核对 fd 执行 fchown/fchmod，不递归修改用户 home，不触碰无关文件。
- 另一个真实拒绝测试证明 s6 默认会忽略 cont-init 失败：runtime UID 拥有的非法硬链接遭初始化拒绝后仍能启动 gateway。镜像新增 `S6_BEHAVIOUR_IF_STAGE2_FAILS=2`，初始化失败必须终止容器。修改后的测试确认无绑定库、无模型/工具调用，没有靠降权后的读取失败间接阻止启动。
- 新增 Linux 卷集成测试五条：正常异 UID 私有 home 启动并完成原生工具调用；不匹配身份、符号链接、准备 UID 的硬链接和 runtime UID 的硬链接全部拒绝。合法标记仍为 0600，无关文件保持原 UID/GID 和权限。单元测试另覆盖 FIFO、目录、超限文件、home 符号链接及拒绝 root 目标用户。没有放宽目录/文件权限作为修复。
- 最终镜像 `sha256:3aacc8a539875d590282f888f66ee09404edc37be39466af8476fd080db9a253`，项目专属 BuildKit 构建 `cin16dfyirxf90fyy8iask3uq` 成功。默认 Python **40 项通过**（8 条已有交易日历依赖警告），Linux 卷集成 **5 项通过（42.02 秒）**，新版镜像六容器隔离/启停/运行中故障恢复 **1 项通过（112.73 秒）**；Ruff、shell 语法检查通过。
- 所有本轮测试容器、临时 Linux 卷及测试子进程已结束清理，仅保留项目构建缓存和开发 PostgreSQL。该证据覆盖 Docker Linux VM 的真实权限语义，不代表已在目标 Linux 服务器安装并验证宿主服务账户、网络或真实供应商。完整 M1–M8 仍待真实模型/行情和部署验收，goal 保持 active。


## 真实容器中的完整研究链路

- 将已有完整三角色系统场景复用于真实容器，新增 `tests/hermes/container_system_integration.py`。Node 端使用真实注册/准备函数生成当前 owner 的三个 Profile 配置，Python 端通过 Compose 启动同一锁定镜像；没有改写运行时配置来迁就测试，也不要求本机另装 Hermes 源码。
- 实际链路为容器内 Hermes → 私有 HTTP 工具网关 → Task/Business/DataService → PostgreSQL 与 Quant 子进程 → Canvas/Worker 归档。只有模型决策和行情条目来自测试夹具；工具不再是六容器基础测试中的简单响应模拟器。
- 验证首次原生接纳后丢失响应并 SIGKILL Worker；新 Worker 等待真实 30 秒租约到期，以原请求、session、幂等键和 capability 接管。原研究与两个专项共三个 run，只发生一次原键重放，无重复行情副作用；五类 Canvas、专项结果引用及跨用户拒绝通过。
- research 容器停止/启动后，确认仍为原容器且启动 PID 改变，再仅发送“把它改成一年”。原生对话历史保留 NVDA 与截止日，保存一年数据和新组件，旧研究产物保留。两条取消场景分别延迟供应商返回和已验证事实提交，确认停止回执不等于工具退出，后续任务在实际退出回执前保持排队，迟到有效事实按 partial 处理。
- 测试 Worker 的 runtime identity 现在包含实际镜像 ID（进程模式保留原有源代码测试标记）；私有测试绑定和响应丢失代理只存在于 tests。生产 registry 门禁没有被绕过，也没有生成通过报告。
- 验证结果：完整容器研究链路 **1 项通过（81.08 秒）**；共享场景重构后的真实进程系统回归 **2 项通过（73.82 秒）**。新增/变更测试端严格 TypeScript、Prettier 与 Ruff 均通过。使用上一阶段已验证的镜像 `sha256:3aacc8a539875d590282f888f66ee09404edc37be39466af8476fd080db9a253`，本阶段无需重建镜像或重新执行无关前端构建。
- 临时 Compose 项目、Worker/Hermes 测试进程和随机 schema 已清理。当前仍缺少用户指定的真实模型/行情私有配置及部署目标，已请求配置路径和授权数据源；不会借用其他项目凭证、生成演示行情回退或将受控模型通过冒充真实供应商通过。完整目标保持 active。


## 完整范围核对与浏览器关闭验收（最新）

- 对照 architecture.md 的 M1–M8 以及第 8 节逐项核对现有实现和测试，没有用受控模型/测试行情替代真实供应商验收。浏览器用例发现一个覆盖缺口：原用例在任务执行时断网，但直到任务完成后才关闭页面；不能把这段证据直接描述为“执行途中关闭页面”。
- 在 `tests/browser/canvas.spec.ts` 增加独立场景：确认 task 仍为 running、仅一个产物，关闭整个用户浏览器上下文；后续真实工具/Quant/存储操作在页面均已关闭时完成。新上下文用保存的登录态重新进入，确认原任务 completed、完整回答与五个产物恢复，且没有再次 POST 研究请求。
- 新场景使用受限 Web 数据库账户和实际 Chrome，**1 项通过（12.2 秒，场景执行 9.1 秒）**；严格 TypeScript、Prettier 通过。它使用测试研究执行器与合成行情来验证浏览器生命周期，不冒充真实模型浏览器全链路。容器/Worker 的独立完整链路证据见上一阶段。本轮拥有的 Web 预览已停止。

| 必须覆盖的情形 | 当前可复核的证据入口 | 边界 |
|---|---|---|
| 跨用户引用、记忆及角色权限 | tests/server/gateway.test.ts、data.test.ts；tests/hermes/container_integration.py | 两用户六容器；受控模型 |
| 伪造价格和身份输入 | tests/server/data.test.ts、gateway.test.ts | 实际工具参数和资源授权 |
| 同键重放及冲突 | tests/server/tasks.test.ts、worker.test.ts；tests/hermes/runtime_integration.py | 应用与原生接纳双层验证 |
| 创建响应丢失、Worker/Hermes 重启 | tests/hermes/container_system_integration.py、container_integration.py | 真实进程/容器和数据库；非真实供应商 |
| 停止竞态、迟到事实和子任务失败 | tests/server/tasks.test.ts、worker.test.ts、data.test.ts；容器系统测试 | 终态必须等待实际执行证据 |
| 断线超出事件保留期 | tests/server/http.test.ts、tasks.test.ts、task-stream.test.ts | 用持久化快照恢复，不依赖永久上游事件 |
| 执行中关闭页面、全文与画布恢复 | tests/browser/canvas.spec.ts | 新增 Chrome 用例；测试执行器 |
| 过期画布更新、历史和布局恢复 | tests/server/tasks.test.ts、http.test.ts、canvas-client.test.ts；浏览器 Canvas 用例 | owner、revision、version 校验 |
| NVDA 追问“一年” | tests/hermes/container_system_integration.py | 原容器重启后只提交追问，保留 session |
| 预测样本不足 | tests/quant/test_core.py | 不输出无依据预测 |
| 持仓缺价和零资产 | tests/quant/test_core.py；tests/server/data.test.ts | partial/null，不补造价格或估值 |
| 登录/CSRF、限额和不可变审计 | tests/server/auth.test.ts、http.test.ts、tasks.test.ts、database-access.test.ts、maintenance.test.ts | 后端与受限数据库账户的阶段验收记录 |

本次范围核对时缺少模型、行情及部署输入。后续用户已指定复用 Claude Code 模型配置，接入证据见下一节；当前仍需行情供应商及已授权 feed、实际部署目标。生产运行环境仍需按原 registry 门禁验收并登记，不能把测试内绑定提升为生产配置。

API 契约第 72 行要求没有原执行器退出回执的外部工具继续保留 unknown、不自动重跑或释放 Profile。该行为已有拒绝与可见状态测试；不能把“无证据也要强制结算”增加为完成目标。已知且可查询的 native unknown run 有显式运维核对入口；不可查询 API 的恢复仍需目标部署下的运维验证。以上核对没有宣称 M1–M8 整体通过，goal 保持 active。

## Claude Code 模型接入与真实三角色验收（最新）

- 用户明确指定使用当前 Claude Code 中配置的模型。读取本机 `.claude/settings.json`，选取 `ANTHROPIC_MODEL=glm-5.3-flash`、现有 Anthropic 兼容网关及其已授权 token；未修改 Claude 配置，也未导入其他登录信息。仅将所需模型字段保存为项目 `.local/provider/claude-code-model.json` 私有快照（文件 0600、目录 0700），未将密钥写入源码、文档或命令参数。该快照不会自动跟踪后续 Claude 设置变更。
- 实际网关探测证明默认 `x-api-key` 返回 401，相同配置使用 Bearer 返回 200，响应模型为 `glm-5.3-flash`。Provision/bootstrap 新增 `anthropic_messages`，仅该协议允许显式 `authMode=api_key|bearer`。原生 Anthropic SDK 构建点通过小型源码补丁读取经过 bootstrap 校验的选择；只对当前 Profile 的精确 endpoint 和 credential 生效，其他路由仍保留原生行为。SDK 仅发送一种认证头，模型密钥仍不进入 `config.yaml` 或 `.env`。
- 锁定源码范围扩展为 33 个文件（4 个补丁目标、29 个未改动依赖），新增文件均与原始固定版本归档和已安装源码比对后记录摘要。项目专属 BuildKit 构建成功；`docker image inspect` 确认新镜像 `sha256:9fae9a02322d55719e73bf5189f2093ac6e6cf6ab981e9cbaf639977ae1b9fe2`。本地进程测试安装副本同步到相同补丁和桥接代码。
- 新增显式运行的 `tests/hermes/provider_integration.py`：真实 PostgreSQL 注册/准备 → 三个真实角色容器 → 原生 Runs → 真实网关/模型 → get_workspace → 模型最终回答。工具只返回运行时随机 nonce，三个角色均准确读回，确认工具结果实际经过模型；服务端 capability/operation ID 正确。research 容器停止/启动后，同 session/幂等键恢复原 run 和最终文本，没有重复工具执行。
- 实时模型验收 **1 项通过（63.31 秒）**；默认 Python **43 项通过**（8 条已有日历依赖警告）；源码审计 **7 项通过**，新增原生 SDK 单认证头/跨路由隔离检查；真实数据库准备测试 **5 项通过**；项目和测试辅助程序类型检查、变更文件 Ruff/Prettier 通过。新镜像的原有 OpenAI 协议六容器隔离、生命周期和运行中崩溃恢复回归 **1 项通过（105.54 秒）**。
- 此轮真实模型使用受控 workspace 工具响应，没有真实行情，也没有宣称完整真实研究协作、五类画布或生产部署已通过。未生成通过生产 registry，测试 Compose 项目、进程和随机数据库 schema 已清理。原始、脱敏验证记录分别在 `.local/live-provider-verified-image.log` 和该测试目录内 `provider-evidence.json`，兼容性回归记录在 `.local/anthropic-image-regression.log`。
- 当前外部输入缺口缩减为：行情供应商及已授权 feed/私有凭证路径、最终部署目标。模型配置缺口已解决；整体 M1–M8 仍未完成，需在这些输入明确后继续完整联调和部署验收。

## Alpaca IEX 接入与完整真实研究（最新）

- 用户提供 Alpaca 凭证后，仅保存到 `.local/provider/alpaca.json` 与工具服务环境文件 `.local/provider/alpaca.env`（0600，父目录 0700）。使用此前说明的 IEX feed，未尝试 SIP 或交易接口。生产 AlpacaProvider 直接读取 NVDA 2025-01-02 至 2025-08-01 的 145 条真实日线，价格与成交量保持十进制字符串；只输出脱敏连接证据。行情凭证没有进入 Hermes Profile 的模型凭证文件，也没有写入源码/文档。
- 新增显式执行的 `tests/hermes/live_research_integration.py` 与 `tests/support/hermes-live.ts`。真实模型决策和真实 Alpaca 行情经过实际三角色容器、原生 Runs、TaskWorker、HTTP 工具网关、PostgreSQL、Quant 和 Canvas。仅测试账户及手工选择的模拟持仓是验收输入；没有模型、行情或工具响应替身，也没有交易调用。测试通过私有临时绑定驱动真实 Worker，不生成通过生产 registry。
- 首轮真实联调暴露原生 Anthropic 工具转换丢弃顶层 anyOf/oneOf：原先的 quant_calculate 和 canvas_upsert 被转换为空 properties，模型因此猜参数或发送空对象。固定版本转换函数的快速回归测试先以 `dict_keys([])` 失败（0.08 秒），修复后通过（0.05 秒）。Quant 目录现在仅发布当前角色的单一计算 Schema；Canvas 发布普通对象、角色允许的组件及明确的引用规则。服务端仍使用原有严格判别联合校验，不接受额外价格、身份或错误引用。
- 后续联调还暴露角色交接问题：组合角色使用 split 数据得到明确的 inconsistent_price_basis/空总值，协调角色尝试重复派发同一专项并被 409 拒绝，本轮正确归档为 failed。补充研究与组合角色指令及 dispatch 字段说明：组合只接收对应持仓日期的 raw 数据，或空引用由专项自行获取；在原专项内纠正口径，不要求协调角色重复派发；确实缺失的事实仍按 partial 报告。没有移除价格口径校验、单角色单次派发或失败保护。
- 最终真实研究 **1 项通过（202.11 秒）**：3 个独立 native run/角色/session、2 份真实数据、5 份计算/研究结果、29 次工具请求，生成 stock_chart、indicator_panel、prediction_result、portfolio_summary、research_summary 五类产物。验证 60 点回顾预测评估、组合 Canvas 引用的结果无缺价且总值为十进制字符串、最终中文回答、终态归档及令牌退役；另一用户无法读取任务或产物引用。
- 最终镜像 `sha256:7d07e0a9b7ce0e5f93d9f7afa1dc1144f49a344ad195e73fbc43dcfe28fe27b6`，项目专属构建 `toj4dvfgrssg38s2rrb264jte` 成功。源码锁定增加 Anthropic 转换与 Schema sanitizer，共 35 个文件（4 个补丁目标、31 个未修改依赖）；本机进程测试目录同步到同一目录生成结果。
- 验证：后端完整 **114 项通过**；后续角色交接说明变更的工具目录/网关/Worker 回归 **27 项通过**；默认 Python **43 项通过**（8 条既有交易日历依赖警告）；完整源码审计 **8 项通过**，最终目录另通过原生转换针对性回归。项目及测试辅助程序类型检查、变更代码 Ruff/Prettier 通过；本轮生产构建通过。新增测试验证生成目录与服务端一致，并确认模型接口兼容处理没有放宽权威字段/角色/引用校验。
- 脱敏证据位于 `.local/alpaca-connection-evidence.json`、`.local/live-research-handoff-fixed.log`，以及该验收目录内 `live-research-evidence.json`；私有 `research-result.json` 保留研究文本及产物引用。三轮各自的 Compose 项目、测试 Worker 和随机数据库 schema 已结束清理；未修改其他项目容器、现有凭证或生产 Profile 状态。
- 模型和行情输入缺口已解决。当前仍需用户指定最终部署目标，并在该环境验证服务账户/网络、生产注册表、实际浏览器链路及恢复场景。此前的故障测试仍是各自标注的受控场景，不能把一次正常真实研究通过等同于所有生产失败场景通过。完整 M1–M8 尚未完成。

## DigitalOcean / 阿里云项目替换（2026-11-07）

用户明确授权清理两台服务器的旧项目并替换成本项目。主应用部署在
`sg-prod`（DigitalOcean，x86_64、4 CPU、约 8 GB 内存），阿里云
`aliyun-akshare` 仅保留 HTTPS 入口代理。访问地址为
`https://trading.youwei-agent.com`；旧 market/dash/chat 地址跳转到新工作台。
代理验证上游 TLS 证书，SSE 禁用缓存和缓冲。

- 部署版本目录：`/opt/hermes-alpha-lab/releases/20261107T0800Z`。
  `infra/deployment/` 保存这次部署使用的安装、数据库、服务、代理与验收脚本。
  脚本固定了本次部署路径和摘要，不应直接用于覆盖其他环境。
- Node 26.0.0、pnpm 9.15.4、uv 0.11.16 安装在项目 runtime 目录，未替换系统版本。
  Node 在该 Ubuntu 主机需要 `libatomic1`，已安装；下载校验和已核验。
- 服务器实际构建的 Hermes 镜像为
  `sha256:170f35a77c5e2e895cefc88786283e487f5af1444d3c774c09e33226c981904e`，
  架构 amd64。构建通过全部 35 个固定源码校验。
- 独立 PostgreSQL 17 使用持久卷 `hermes-alpha-lab-production_postgres_data`，
  仅监听 `127.0.0.1:55439`；生产库 `alpha_production`，验收库
  `alpha_acceptance_test`。Web、Worker、Tools 使用三个最小权限数据库账户。
- systemd 服务为 `alpha-lab-web`、`alpha-lab-worker`、`alpha-lab-tools`、
  `alpha-lab-supervisor`、`alpha-lab-preparer`，已设开机启动。
  Web/Worker/Tools 使用不同非 root 系统账户；只有宿主监管与准备进程有 Docker/私有目录权限。
  Web 监听 `127.0.0.1:3400`；工具网关监听 Docker 宿主网关 `172.17.0.1:3410`，
  三个 Profile API 仅监听 `127.0.0.1:19000–19002`。
- 模型与行情凭证位于服务器 `/opt/hermes-alpha-lab/private/provider/`（目录 0700、文件 0600）。
  初始登录账户 `owner@alpha.local`，随机密码只存于服务器
  `/opt/hermes-alpha-lab/private/initial-login.json` 和本地
  `.local/provider/deployment-login.json`；源码、文档和命令参数没有密码。
- 三个正式 Profile 在原部署目录上逐一通过真实模型与受控 workspace 工具的协议验收：
  独立密钥/网络/home、外来 Profile 密钥拒绝、工具身份传递、真实终态文本、
  原会话身份、同键去重、每个容器停止/启动后恢复原 run，以及工具未退出时取消不能提前终态。
  仅全部成功后生成 `profiles.verified.json` 和每 Profile 的 evidence。
  此协议检查的 workspace 响应明确为 fixture，不作为行情/计算真实性证据。
- 服务器严格 umask 暴露了配置准备问题：0444 挂载文件会变成 0400，导致容器非 root 用户不可读。
  `writePreparation` 现仅对私有临时目录中独占创建的文件设置精确权限；父目录仍为 0700，
  身份标记仍为 0600。新增严格 umask 回归测试，服务器 115 项后端测试全部通过。
- Linux 容器集成测试显式支持 `ALPHA_TEST_BIND_HOST=172.17.0.1`，避免把 Docker Desktop
  的回环网络行为误当作 Linux 行为。两用户六容器隔离/恢复和三容器真实工具/Worker/Quant
  故障注入验收已在这台服务器通过。完整真实供应商研究和公网浏览器结果另行记录。

旧项目清理范围：DigitalOcean 上的 Hermes v0.22、Open WebUI、usstock、Canvas API、
volc-proxy 容器与对应旧镜像；`/opt/ai-workbench` 全部代码、数据、旧备份；
旧项目 cron、独立 Docker 网络及旧项目日志。阿里云 `/var/www/canvas` 旧前端已删除，
两台服务器的旧应用 Nginx 配置已替换。清理前备份已传回本地并通过 gzip 完整性检查：
`.local/deployment-backups/sg-prod-old-apps-20261107.tar.gz` 和
`.local/deployment-backups/aliyun-old-apps-20261107.tar.gz`，目录 0700、文件 0600。

保留边界：个人 WebDAV/Obsidian 归档与两个原有 410 站点尚未获明确删除选择，暂保留；
SSH、云厂商代理、realm 转发、系统服务与 TLS 证书保留。没有删除云实例或操作未识别的云账号资源。
DigitalOcean 根盘已用空间从约 29 GB 降至约 15 GB。

运行维护：`systemctl status alpha-lab-{web,worker,tools,supervisor,preparer}` 查看状态，
`journalctl -u alpha-lab-worker -n 50 --no-pager` 查看 Worker 运行记录。
新注册会自动准备三个隔离 Profile，但必须完成真实运行验收、合并受信任 registry
并重新加载 Worker/监管服务后才可运行；不会对未验证的新账户自动放行。


### 目标环境最终验收结果

- 服务器完整后端：115 passed；三个显式容器验收：3 passed（916.63 秒）。
  涵盖两用户六容器隔离/记忆与重启、真实 Worker/工具/Quant 的受控故障，以及真实模型与行情完整研究。
- 服务器真实供应商研究：3 个独立 native run，2 份行情数据，4 份计算/摘要结果，29 次工具请求，
  5 类 Canvas，跨用户引用拒绝。实际模型为 `glm-5.3-flash`，行情为 Alpaca IEX，无模型或行情替身。
  证据原文件中 “local Docker acceptance” 指隔离的宿主机 Docker 验收；此轮执行主机为 DigitalOcean，
  不代表开发电脑，也不替代下述公网正式账户验收。
- 正式账户公网浏览器研究 `bf666ca5-ffb4-47ce-a77d-ea77b868f0b7`：研究/预测/组合均 completed。
  验收输入明确标注模拟 NVDA 2 股及 USD 100 现金，并非用户真实账户；没有交易调用。
  提交后关闭整个 Chrome，服务器继续完成；重新启动浏览器、重新登录并选择原研究后，完整文本和五类组件恢复。
- 首次浏览器脚本误要求刷新后自动选择会话；现有交互是从研究记录重开。
  按实际交互补做只读恢复验收后通过，没有重新提交付费研究：0 次重复提交、0 个 pageerror，
  桌面与手机截图已保存。该项不宣称刷新自动保留当前选择。
- 公网首页 HTTP 200、TLS 校验成功，未登录 session 返回 401；旧 market/sg-chat 地址返回 301。
  阿里云上游证书校验保留开启，并设验证深度为 3 以容纳实际证书链。
- 本地额外检查：TypeScript 类型检查、格式检查、修改文件 Ruff 与 shell 语法检查通过；
  Python 默认测试 43 passed，依赖现有的 8 条 pandas/calendar 弃用警告仍明确保留。
- 最终证据：`.local/deployment-evidence/server-acceptance.log`、
  `.local/deployment-evidence/server-live-research.json`、
  `.local/deployment-browser/evidence.json`、`research-result.json`、`desktop.png` 和 `mobile.png`。
  服务器原始日志在 `/opt/hermes-alpha-lab/logs/`，正式 Profile 的逐实例协议报告在其私有 `evidence/` 目录。

## 网站打开速度优化（2026-11-07）

针对用户反馈的首屏慢，先在真实公网入口执行三次无缓存 Chrome 测量，再逐项修改。
原始应用服务器首页处理约 4 ms，瓶颈在浏览器访问链路、阿里云跨境回源和登录前的工作台代码加载。

- 阿里云本地提供当前构建的公开静态 HTML 和 `/_next/static/`，启用预压缩 gzip；
  带内容哈希的资源长期缓存，HTML 保持 `no-cache` 重验证。保留旧构建资源，避免已有页面加载旧 chunk 失败。
  RSC 请求、非静态路由、账户/研究 API 和 SSE 继续转发到 DigitalOcean；不缓存用户资源。
- `GET /api/auth/session` 完全缺少登录 Cookie 时在入口返回同样的 401/no-store；
  任何存在的 Cookie 都交给应用验证，不在入口推断已登录身份。
- Chat / Canvas 改为按需加载；登录前 JS 解压后体积从 1,206,927 字节降到 470,269 字节，减少 61%。
- Web 新构建位于 `/opt/hermes-alpha-lab/releases/20261107T0855Z`，只重启 Web；
  Worker/Tools/监管/数据库继续使用原部署。阿里云静态发布根目录为 `/var/www/hermes-alpha-lab`。
  `/opt/hermes-alpha-entry` 保存入口配置和静态发布工具；`current` 原子链接切换 HTML 版本，
  `assets/_next/static` 累积保留带哈希的旧资源。

三次冷启动中位数（相同机器、相同入口，耗时为登录按钮可见可用）：

| 条件 | 首次打开中位数 |
|---|---|
| 优化前，使用机器原有系统代理 | 10,495 ms |
| 仅入口静态文件本地化，同一代理 | 5,535 ms |
| 全部优化后，同一代理 | 5,659 ms |
| 全部优化后，仅测试 Chrome 绕过系统代理 | 775 ms |

代理链路存在明显波动，拆包和无 Cookie 快速拒绝的收益不能仅靠三个时间样本单独归因；
已确定的收益是同链路整体中位数约降低 46%，初始代码体积下降 61%，跨境静态请求消除。
原机器启用了本机 HTTP/HTTPS/SOCKS 代理，未修改用户系统或代理规则。
三秒首屏预算在直连测量通过，在原系统代理下仍未达到，不能宣称所有网络条件下一秒内打开。

回归通过：生产构建与类型检查；实际公网登录和历史会话五类画布恢复；无重复提交和 pageerror；
无 Cookie / 无效 Cookie 拒绝、登录态与匿名请求隔离、CSRF、私有响应 no-store、静态 immutable、
HTML 重验证及 RSC 转发。没有重新触发付费研究。
`infra/deployment/check-opening.mjs` 保留公网冷启动及 600 KB 初始代码预算检查；
`ALPHA_BROWSER_DIRECT=1` 仅控制该次隔离浏览器，不改系统设置。
原始测量与鉴权检查保存在 `.local/performance/`。

后续发布 Web 时必须同时发布匹配的静态构建，不能只更新 DigitalOcean：从已完成构建的
`.next` 目录打包 `static`、`server/app/index.html`、`BUILD_ID`，上传阿里云并解压后运行
`python3 /opt/hermes-alpha-entry/publish-edge.py EXTRACTED_NEXT_DIRECTORY`。
只有公开静态文件进入入口目录，不上传服务端代码或私有配置。首次部署时，缺失的本地文件仍回源，
所以新版 Nginx 配置可在静态发布之前启用。回滚需同时恢复旧 Web service 的 release 路径及入口
`current` 指向的旧 BUILD_ID；旧静态资源无需删除。

## 参考图登录入口改版（2026-11-07）

按用户提供的参考图重做公开入口：黑色背景、青绿色曲线、居中标题、工作台预览、六张功能卡、
研究流程与底部入口。预览明确标注示意数据，文案对应已有研究、预测和模拟组合能力。
登录与注册使用原有 API，通过原生 dialog 展示；支持密码显示、错误后保留输入、Escape 关闭、
键盘焦点循环及关闭后焦点恢复。手机与横屏均无水平溢出，保留减少动态效果偏好。

Web 当前版本为 `/opt/hermes-alpha-lab/releases/20261107T1530Z`，
入口同步构建 `ETfefFcfR-6D7h2yNcUQV`；五项应用服务均 active，Nginx 配置校验通过。
后端服务版本及数据保持原部署。工作台继续按需加载，未添加网络字体、图片或图表依赖。

验证结果：生产类型检查与构建通过；本地和公网登录页面浏览器用例均 2 passed，
覆盖桌面、手机、横屏、注册表单、登录错误与键盘操作。公网真实账户登录后恢复原研究五类组件，
刷新后从历史记录重新打开成功，0 次重复提交、0 个页面错误。
公网直连三次冷启动到登录弹窗可用分别为 3806 / 1825 / 1789 ms，中位数 1825 ms，
满足三秒中位数预算；首屏 JS 解压后为 486,166 字节，比最初版本减少约 60%。
新版计时包含点击打开弹窗，且网络有波动，不与旧版时间作严格同比；用户系统代理未修改。

证据：`.local/login-public-tests.log`、`.local/login-public-results/`、
`.local/login-redesign-workspace.log`、`.local/login-redesign-speed.json`。
`tests/browser/login.spec.ts` 保存入口交互回归，部署浏览器检查已适配新登录入口。

## 深色研究工作台改版（2026-11-08）

按用户提供的终端截图调整登录后的工作台：黑色背景、青绿色图表、细边框、左侧自选股与
研究记录、中间研究画布、右侧 Hermes 对话。Agent 状态移入左侧，模拟持仓入口、任务历史、
画布排序、连接恢复和五类结果保留。样式限定在 `.app`，公开登录入口保持原设计。

- 行情卡直接读取已有数据集，展示股票代码、收盘价、相邻数据日涨跌、开高低和成交量，
  标明数据日期、调整方式及非实时行情。不引入参考图中的虚构评分、价格、订单或七日预测。
- 增加青绿色收盘走势，与 K 线切换；1M / 3M / ALL 仅筛选已保存的历史区间，不发起新研究。
  固定图表容器高度，避免 autoSize 造成尺寸反馈循环与按钮持续移动。
- 结果快捷导航可跳到行情、指标、预测、组合或摘要，不改变用户保存的结果排序。
  自选股点击后建立研究并填入问题草稿，需用户发送后才提交研究。
- 无数据时使用明确的空状态；手机纵向排列，研究记录、自选股、Profile 状态和持仓可展开。
  用户与助手消息使用独立气泡样式，保留键盘焦点、持仓弹窗 Escape 关闭和减少动态效果偏好。

生产 Web 为 `/opt/hermes-alpha-lab/releases/20261108Tterminal2`，阿里云同步公开构建
`LmM9OieR5G7Rua2vKxFOd`。仅切换 Web 和公开静态资源，后台研究服务及数据未迁移。
五项服务 active，Nginx 配置校验通过；回滚原工作台可恢复
`/opt/hermes-alpha-lab/private/web-before-terminal.service` 并切回入口旧构建。

验证：TypeScript、格式检查和服务器生产构建通过。本地和最终公网 UI 用例各 4 passed，
涵盖登录回归、三栏位置、结果导航、走势图/K 线及区间切换、排序、历史切换、自选股草稿、
375px 手机、横屏和持仓弹窗。UI 用例使用浏览器拦截的固定数据。
另以真实账户只读验证既有完整研究：刷新后重开历史、恢复五类真实产物、行情切换、手机持仓弹窗
均通过，0 次研究提交、0 个页面错误。未重新调用模型或修改用户持仓与结果排序。

最终构建三次直连冷启动至登录弹窗可用为 1152 / 3403 / 1179 ms，中位数 1179 ms；
首屏 JS 解压后 488,005 字节，三秒中位数和 600 KB 预算均通过。该时间包含网络波动，
不代表所有网络条件。工作台和图表继续按需加载，没有新增依赖或外部字体。
证据位于 `.local/terminal-public-tests.log`、`.local/terminal-live/`、
`.local/terminal-live-check.log` 和 `.local/terminal-final-speed.json`；
浏览器交互回归保存于 `tests/browser/terminal.spec.ts`。

## 按 Figma 预览切换浅色工作台（2026-11-08）

根据用户确认的 `hermes-research-workbench.kui.fig` 内嵌预览，工作台改为白色侧栏、
浅灰蓝画布背景和蓝色操作按钮。保留三栏结构，右侧助手直接从顶栏下方展开，中央新增
收盘价、价格走势、预测评估、模拟组合四张概览卡。收盘价读取已保存数据集及其展示区间，
切换研究后清除旧值；空数据使用破折号，不复制设计稿中的示例行情。
自选股输入移至列表上方；右侧两个示例问题和底部提问入口接入现有建研究流程，
进入空对话后聚焦真实输入框，用户发送后才提交研究。图表、消息、弹窗同步浅色样式，
手机使用两列概览卡与纵向画布、助手布局。

生产 Web 为 `/opt/hermes-alpha-lab/releases/20261108Tlight`，阿里云入口构建为
`uAwQRIJAtO35t9lstSg9B`。在独立发布目录完成构建后切换 Web；五项服务均 active。
原 Web 单元备份为 `/opt/hermes-alpha-lab/private/web-before-light.service`，
上一入口构建 `LmM9OieR5G7Rua2vKxFOd` 及其静态资源保留。

验证：类型检查、格式检查、服务器生产构建通过；本地和公网浏览器用例各 4 passed，
覆盖三栏位置、真实格式的行情概览与清空、图表切换、结果排序、历史查看、输入框聚焦、
手机和横屏、持仓弹窗及登录回归。真实账号恢复已有研究五类产物、刷新后重开历史、
行情区间和图形切换均通过，0 次研究提交、0 个页面错误。
公网直连三次冷启动至登录弹窗可用为 625 / 562 / 2838 ms，中位数 625 ms；
首屏 JS 解压后 490,227 字节，满足 3 秒中位数和 600 KB 预算。网络条件会影响实际时间。
证据：`.local/light-local-tests.log`、`.local/light-public-tests.log`、
`.local/light-live/`、`.local/light-live-check.log`、`.local/light-speed.json`。

## 新账号 Profile 未接入运行环境修复（2026-11-08）

用户任务 `014d230d-d8d7-4ff4-b7b4-00de6bd09efa` 在 Worker 的运行环境解析阶段以
`profile_unavailable` 失败，尚未提交给 Hermes。根因：自动准备服务已为新账号生成三套
独立配置，但生产 Worker 的验证注册表和 supervisor 参数只包含初始验收账号。
准备完成不等于运行环境已验证、已启用；此前只用初始账号做线上验收未覆盖这个缺口。

对受影响账号三个 Profile 运行真实模型契约验证，全部通过，包括独立身份、工具调用身份、
幂等、重启恢复和运行中停止。随后合并两个账号共六个已验证绑定，将新账号的凭据加入
Worker 与 supervisor 服务并重启这两项服务；五项应用服务均正常。没有共用账号环境、
复制通过报告或直接修改数据库就绪状态。验证期间工具服务使用退出 trap 自动恢复。

新增 `infra/deployment/check-owner-runtime.ts`，直接调用生产 `loadRegistry` 检查指定账号
三个角色的运行绑定及 lifecycle 管理状态：修复前断言失败，修复后两个账号六个角色均通过。
新增 `activate-verified-profiles.py`，通过实际 `loadDeployments` 预检，拒绝丢失旧 Profile，
复制已有真实验证证据并配置服务；旧注册表及服务单元保存在
`/opt/hermes-alpha-lab/private/activation-20261108T010513Z`。
新注册用户仍需完成真实契约验证和运营启用，自动准备不会自动执行这些检查；操作流程补入
`services/hermes/README.md`，不能将 prepared 当作可用。

在原对话中以独立幂等键补跑一次明确的 TSLA 请求，任务
`3a15d030-dda7-40f3-b865-c9280dc7bfa9` completed，Alpaca/IEX 行情和指标计算均 completed，
真实数据末日为 2026-11-06，含预热区间共 111 行。已保存行情图与研究摘要，中文结果含
MA20、RSI 数值。原失败任务保留，未修改其历史状态。这里验证的是行情查询，不是模拟交易。
类型检查、Python 语法检查和生产加载预检通过。证据：`.local/profile-runtime-before.log`、
`.local/profile-runtime-after.log`、`.local/profile-activation.log`、`.local/profile-repair-evidence.json`；
服务器契约检查日志 `/opt/hermes-alpha-lab/logs/user-profile-contract.log`。

## 对话优先与可收起画布（2026-11-08）

按 `hermes-research-workbench.kui (1).fig` 内嵌预览的三个状态改版：欢迎页为中央提问框，
有对话时以消息为主，画布默认收起；点击消息下的研究结果卡或顶栏画布计数，在右侧展开。
侧栏采用暖灰背景，Hermes 标识移入侧栏，保留自选股、历史、Profile 状态和模拟持仓。
桌面支持放大/还原画布，手机在对话与画布间切换；关闭与 Escape 恢复触发按钮焦点。
画布开合不重建对话运行时，正在输入的草稿保留。新建研究和示例问题先形成草稿，首次发送
才创建服务端对话并提交任务；提交响应或打开对话失败时保留同一幂等键，重试不重复提交研究。

行情采用蓝色走势，保留 K 线、区间切换、排序、历史产物和来源信息。下方指标卡展示收盘价、
MA20、RSI14 和当前显示区间涨跌；MA20/RSI14 仅采用引用同一行情数据集、同一交易日的
已保存指标结果，无匹配值显示缺失状态。设计图里的示例价格与预测场景没有作为业务数据写入。

Web 发布目录 `/opt/hermes-alpha-lab/releases/20261108Tconversation`，阿里云入口构建
`_m8dezUw8nW0NFQ54FeuN`。通过独立目录构建后切换，前一 Web 单元保存在
`/opt/hermes-alpha-lab/private/web-before-conversation.service`，旧入口构建
`uAwQRIJAtO35t9lstSg9B` 及资源保留。后台 Profile 的六个绑定及 Worker/supervisor 未更改。

验证：类型检查、格式检查、生产构建通过；界面用例本地/公网各 6 passed，覆盖画布三态、
焦点、手机、图表、草稿、幂等重试及登录。另以独立本地 `alpha_conversation_test` 数据库
运行 3 项集成用例通过，覆盖真实存储和合成行情/Quant 的后台完成、断网重连、刷新恢复、
五类产物、排序、取消、持仓及跨账号访问隔离；没有连接生产数据库执行这些用例。
真实线上账号只读恢复已有研究，确认收盘价及匹配指标、展开/收起/放大、手机画布、持仓弹窗
均正常，0 次研究重提、0 个页面错误。

公网直连三次冷启动至登录弹窗可用为 723 / 635 / 3407 ms，中位数 723 ms；首屏 JS
解压后 490,891 字节，满足 3 秒中位数和 600 KB 预算。网络条件会影响实际时间。
证据：`.local/conversation-public-tests.log`、`.local/conversation-backend-tests.log`、
`.local/conversation-live/`、`.local/conversation-live-check.log`、`.local/conversation-speed.json`。
`check-restored-browser.mjs` 和既有浏览器回归已适配画布默认收起的交互。

## 界面审查修复与发布（2026-11-08）

按 better-interface 审查的 7 项发现完成修复：辅助文字与涨跌文字加深并测量实际背景对比；
业务表格通过 `.data-table` 隔离，避免覆盖 Lightweight Charts 内部表格、裁切价格轴；
行情和指标新增原生 details 数据表，键盘可展开，行情数据表随显示区间变化；
追问提交失败显示错误与重试入口，重试沿用同一请求标识及原用户消息；
结果卡通过消息 task_id 与画布 revision 关联，不把旧结果挂到失败的新回复；
未发送草稿按 thread ID 保存在页面内存，切换研究及画布开合可恢复，退出清空；
手机端自选股、模拟持仓和持仓选择字段均为 16px。

验证：本地类型检查、格式检查通过；服务器生产类型检查与构建通过；
本地工作台 7 项浏览器回归通过，发布后的公网工作台与登录界面 9 项回归通过（API 模拟）。
复测 320/375/720/768/1051/1440px，无页面横向溢出或价格轴裁切；
原审查标记的普通正文/控件文字对比问题已消除，头像装饰字母不计入文字对比验收。
线上只读恢复已有真实研究，五类结果、开合放大、手机持仓入口均正常，0 次研究重提、0 个 pageerror。
原生屏幕阅读器、iOS Safari 真机与浏览器 200% 缩放未验证，不宣称完整 WCAG 认证。

Web 发布目录 `/opt/hermes-alpha-lab/releases/20261108Tinterfacefix`，公网构建
`ITIx2Vm70Asun5x_8KiWN`。Web 单元备份为
`/opt/hermes-alpha-lab/private/web-before-interfacefix.service`；旧入口构建
`_m8dezUw8nW0NFQ54FeuN` 及静态资源保留。仅切换 web 服务工作目录及入口静态资源，
Worker、tools、supervisor、preparer 保持原版本。

验证记录与截图：`.local/interface-fix/public-tests.log`、`.local/interface-fix/results.json`、
`.local/interface-fix/live-check.log`、`.local/interface-fix/live/`。

## 异步失败反馈修复与发布（2026-11-08）

自选股和模拟持仓列表现在分别显示加载中、成功空数据和加载失败，失败时提供独立重试入口。
共用账号列表 hook 通过账号与请求代次校验，丢弃退出或切换账号后的旧响应；成功修改列表后，
更早的读取也不能覆盖修改结果。退出请求增加等待状态和重复点击保护，失败时保留当前工作台，
在顶部显示“尚未退出”及重试按钮；提示在画布放大与手机画布模式中仍可见。

验证：先新增 3 项失败用例复现问题，再修复至通过，另补充旧账号延迟响应回归。
本地与公网浏览器回归各 13 passed（失败场景使用 API 模拟）；类型检查、格式检查、
服务器生产构建通过。1440×1000 和 320×740 错误状态无页面横向或纵向溢出，
手机画布中退出失败提示可见。线上真实账号只读恢复已有研究的五类结果，
0 次研究重提、0 个页面错误；5 个应用服务及入口 nginx 正常，web 自动重启计数为 0。

Web 发布目录 `/opt/hermes-alpha-lab/releases/20261108Tasyncfix`，公网构建
`cUaW_Yiel10U9C4VqbY1B`。Web 单元备份为
`/opt/hermes-alpha-lab/private/web-before-asyncfix.service`，旧入口构建
`ITIx2Vm70Asun5x_8KiWN` 和静态资源保留，可配合旧 web 单元恢复。
Worker、tools、supervisor、preparer 保持原版本。

证据保存在 `.local/async-fix/`：`before.log`、`regression.log`、`public-tests.log`、
`build.log`、`public-visual.log`、`live-check.log` 和截图。
本次仅处理第二轮审查的异步失败项；持仓保存重复提交保护、结果卡可访问名称仍待处理。

## better-interface 阻断修复与复审（2026-11-08）

### Scope and coverage

范围为当前线上工作台：研究对话、画布、自选股、模拟持仓的空数据、加载、失败、
恢复及窄屏状态。不包含登录营销页和后台运维界面。采用现有 Next.js、React、
assistant-ui、Lightweight Charts 与普通 CSS，不改变现有浅色工作台设计。
遵循 `apps/web/AGENTS.md`、`GLOSSARY.md`、架构与画布协议；没有独立设计规范或 Storybook。
复审加载并使用 better-accessibility、better-layout、better-writing、better-typography、
better-colors、better-ui 六个领域的规则与相关参考。

| Domain | Evidence inspected | Result |
| --- | --- | --- |
| Accessibility | `chat.tsx` 结果卡名称；`page.tsx` 持仓提交；键盘打开画布、弹窗焦点、数据表与结果重试 | Clear（原生读屏未验证） |
| Layout | `page.tsx` 全局错误位置；320/375/720/768/1051/1440px 截图；展开画布后的错误提示 | Clear（原生 200% 缩放未验证） |
| Writing | `ArtifactView` 局部错误与下一步；自选股、研究记录失败；持仓保存成功与列表刷新失败区分 | Clear |
| Typography | `workspace.css` 与渲染字号、换行、数字样式；320/375px 输入框 16px | Clear |
| Colors | 页面文字检查及 Canvas 实际 fillText/fill 绘制颜色；MA20、RSI、K 线标签 | Clear |
| UI | 对话/画布开合、按钮等待与恢复状态；`prefers-reduced-motion: reduce` 下无活动动画 | Clear |

### Findings

No actionable interface findings remain within the inspected scope.

本轮原有 3 项 HIGH、2 项 MEDIUM 均已修复：

- 通用错误移至始终可见的顶部区域，添加自选股和打开研究失败均说明具体重试位置，
  放大画布或使用手机画布时不会隐藏提示。持仓表单错误仍留在弹窗内。
- MA20 与 RSI 标签背景加深，实际白字对比度为 5.10:1、5.04:1；
  复查确认 K 线标签存在同类问题，一并采用中性深色背景，对比度为 5.69:1。
  成交量标签同样指定深色价格标签颜色，自定义曲线颜色不覆盖指标标签的对比度保障。
- 数据引用 404 不再错误地宣称整个研究不存在；每项结果提供读取失败说明和重新读取按钮，
  缺少引用也有明确下一步。重试只 GET 已有引用，不重提研究任务。
- 持仓保存增加同步防重入、等待标签、`aria-disabled` 与成功提示；
  保存成功后由账号列表 hook 刷新，刷新失败仅重试 GET，不重新 POST 创建快照。
- 删除结果卡覆盖性的 `aria-label`，使用可见研究标题及结果数量作为原生按钮名称。

### Verification

先用新增 4 项浏览器用例复现错误（4 failed），再修复至通过。最终：

- `pnpm typecheck`、修改文件的 `pnpm exec prettier --check` 通过；服务器 `pnpm build` 通过。
- `pnpm exec playwright test --config .local/interface-approve/playwright.config.ts`：17 passed。
- `pnpm exec playwright test --config .local/interface-approve/public.config.ts`：17 passed。
  API 模拟覆盖 404、503、延迟、重复点击、保存成功后刷新失败及恢复；没有写入生产数据。
- `ALPHA_REVIEW_ORIGIN=https://trading.youwei-agent.com pnpm exec tsx .local/interface-approve/baseline.mjs`：
  六种宽度没有页面横向溢出，既有草稿、画布、数据表、弹窗检查通过，0 个页面错误。
- 同目录 `chart-colors.mjs` 和 `visual-errors.mjs` 在公网运行通过：
  验证实际 Canvas 绘制颜色、标签对比度及放大画布/320px 下错误仍可见。
- `infra/deployment/check-restored-browser.mjs` 用真实账号只读恢复已完成研究的五类结果，
  0 次研究重提、0 个页面错误。5 个应用服务与 nginx 正常，web 自动重启计数为 0。

Not verified：原生屏幕阅读器、浏览器原生 200% 缩放、iOS 真机与 RTL。
这是一轮范围明确的界面复审，不代表完整 WCAG 认证。

证据目录 `.local/interface-approve/` 包含 `before.log`、`regression.log`、`public-tests.log`、
`build.log`、`public-baseline.log`、`public-colors.log`、`public-errors.log`、
`live-check.log`、实际颜色与布局 JSON、桌面及手机截图。

发布目录 `/opt/hermes-alpha-lab/releases/20261108Tinterfaceapprove`，公网构建
`xz4Nk3zupnyVC-gOZodaa`；回退用 web 单元备份
`/opt/hermes-alpha-lab/private/web-before-interfaceapprove.service`，旧入口构建
`cUaW_Yiel10U9C4VqbY1B` 与资源保留。仅更新 web，后台服务保持原版本。

### Verdict

**Approve** — 在以上已检查范围内无剩余 HIGH、MEDIUM 或 LOW 发现。

归档说明：发布与公网验收完成后，同步本节报告副本到服务器时遇到 SSH 超时，
本地文档为最新记录。随后再次检查公网首页为 200、未登录 session API 为预期的 401。

## 慢响应归属隔离修复与复验（2026-11-08）

本轮 better-interface 发现一个系统性 HIGH：旧研究详情在退出并切换账号后回填，
以及旧持仓保存回执关闭后来重新打开的编辑窗口、丢失新输入。

增加 `apps/web/lib/use-response-scope.ts`，以请求代次生成可撤销的回执校验函数。
`page.tsx` 分别管理登录会话、研究选择、研究列表请求和持仓编辑窗口的代次。
账号切换同步撤销旧回执，重新登录同一账号也产生新会话；回调绑定发起请求的登录会话，
旧回调不能在新会话里开始后续操作。研究详情、历史产物、列表、研究提交、
自选股写入及 Profile 更新在落入界面状态前检查归属。

关闭持仓编辑（包括 Escape）会结束该编辑窗口的代次并重置其等待状态。
旧保存成功仍可刷新同一账号的持仓列表，但不能改变新窗口的输入、持仓选择、错误或等待状态；
旧失败同样不能写入新窗口。已发出的保存请求不因关闭编辑窗口而被撤销。

验证先新增 5 个用例并确认全部失败，再修复为通过；另补充新旧编辑窗口同时保存、
同一账号重新登录后的列表延迟响应用例。最终结果：

- `pnpm typecheck`、修改文件格式检查、服务器 `pnpm build` 均通过。
- `pnpm exec playwright test --config .local/lifecycle-fix/playwright.config.ts`：24 passed。
- `pnpm exec playwright test --config .local/lifecycle-fix/public.config.ts`：24 passed。
- 原始复现脚本 `review.mjs` 在本地和公网均通过：旧保存返回后弹窗仍打开，
  `TSLA 25` 保留；新账号研究列表为 0、画布为 0，不显示旧账号的 NVDA 对话。
- 覆盖成功与 503 失败回执、关闭按钮与 Escape、新建研究取代延迟历史选择、
  旧保存不能结束新保存的等待状态，以及重新登录同一账号后的旧列表失效。
- API 故障与延迟均为浏览器模拟，没有向生产创建测试研究或持仓。
  真实账号只读恢复原有研究的五类结果，0 次任务重提、0 个页面错误。
- 5 个应用服务及入口 nginx 正常；web 自动重启计数为 0。

证据保存在 `.local/lifecycle-fix/` 的 `before.log`、`targeted.log`、`regression.log`、
`public-tests.log`、`public-review.log`、`live-check.log`、`build.log`、JSON 和截图。
本轮只复验上述异步归属发现及相关回归；原生读屏、iOS 真机、原生 200% 缩放未补测。

Web 发布目录 `/opt/hermes-alpha-lab/releases/20261108Tlifecyclefix`，公网构建
`Z9KQy1K7i8tXRJUwwZaYh`。回退单元备份
`/opt/hermes-alpha-lab/private/web-before-lifecyclefix.service`，旧入口构建
`xz4Nk3zupnyVC-gOZodaa` 与静态资源保留。只切换 web，后台服务保持原版本。
本次通过入口服务器中转完成发布资源传输，并校验两端 SHA-256 一致。

复验结论：**Approve** — 本轮 HIGH 的两个复现场景及相关过期回执场景均已通过。

## 原生缩放与低高度画布修复（2026-11-09）

补充审查使用 Chrome 设置接口启用原生浏览器缩放，并校验实际视口和
`devicePixelRatio`，没有用 CSS 缩放或仅调整截图尺寸代替。200% 缩放原本通过；
1280×1000 窗口在 400% 缩放后可用视口为 320×228，固定画布标题、导航和页脚
挤占高度，使 `.artifacts` 实测高度为 0，K 线按钮虽可聚焦却无法显示。

`workspace.css` 在已有 `max-height: 600px` 条件下让整个画布纵向滚动，
结果区和空态保持内容高度。`canvas.tsx` 的结果导航改用原生 `scrollIntoView`，
同时适配常规窗口中的结果区滚动和低高度窗口中的整块画布滚动。

验证结果：

- 新增低高度浏览器回归用例，修复前稳定失败于 K 线按钮不可见，修复后通过。
  覆盖键盘切换图表、展开数据表、导航至模拟组合及 Escape 关闭。
- 本地及公网完整界面回归均为 25 passed；类型检查、修改文件格式检查及服务器生产构建通过。
- 本地和公网原生缩放验证通过：200% 的 1440×1000、1280×900 窗口，
  以及 400% 的 1280×1000 窗口。图表按钮、数据表、持仓保存均可到达，
  弹窗焦点约束和关闭后的焦点恢复正常。另验证 320×228 空画布的新建入口可达。
- 公网首次 200% 的 1440×1000 检查在页面加载阶段超过脚本的 10 秒上限，
  将该次检查导航超时调整为 30 秒后单独重试通过，保留首次与重试记录。
- 真实账号只读恢复已有研究的五类结果，0 次研究重提、0 个页面错误。
  故障模拟和缩放测试使用浏览器 API fixture，没有创建生产测试研究或持仓。
- 五个应用服务及入口 nginx 正常，Web 自动重启计数为 0；公网首页 200，
  未登录 session API 为预期的 401。

证据位于 `.local/zoom-fix/`：`before.log`、`targeted.log`、`regression.log`、
`public-tests.log`、`zoom-confirmed.json`、`public-zoom/zoom-confirmed.json`、
`public-zoom/zoom-confirmed-retry.json`、`live-check.log`、`build.log` 和截图。
原生读屏、iOS 真机和 RTL 仍未验证。

Web 发布目录为 `/opt/hermes-alpha-lab/releases/20261109Tzoomfix`，公网构建
`MKjc5CqXGucmiXnfst30M`。回退单元备份为
`/opt/hermes-alpha-lab/private/web-before-zoomfix.service`；上一版源站目录
`20261108Tlifecyclefix` 与入口构建 `Z9KQy1K7i8tXRJUwwZaYh` 保留。
源站与入口分别切换 Web 服务和静态页面，后台服务保持原版本。

复验结论：**Approve** — 本轮缩放导致画布内容不可达的 HIGH 已修复并在公网复验通过。

## 多错误提示与缩放叠加修复（2026-11-09）

补充审查发现上一轮仅处理画布内部滚动，外层 `.app` 仍固定 `height: 100dvh`。
添加自选股和退出登录同时失败时，两条不可收缩的提示加上导航会耗尽可用高度，
使整个 `.desk` 和 `.canvas-panel` 变为 0px。该问题在原生 200% 的 640×600
窗口（可用 320×256）及原生 400% 的 1280×1000 窗口（可用 320×228）均已复现。

在现有 `max-height: 600px` 规则中将 `.app` 设置为 `height: auto`，保留
`min-height: 100dvh`，让错误提示和工作区撑开页面并通过外层纵向滚动访问。
保留上轮画布滚动和原生结果定位；恢复较高窗口时仍使用原有固定高度布局。

验证结果：

- 新增两个 320×256、320×228 双错误场景回归用例，修复前均因 K 线按钮不可见失败，
  修复后通过。覆盖完整按钮可见性、键盘切换图表、组合导航、草稿保留和退出重试。
- 本地完整回归 27 passed，公网完整回归 27 passed；类型检查、修改代码格式检查、
  服务器生产构建通过。本地与服务器 CSS 的 SHA-256 一致。
- 本地及公网分别运行六组原生缩放检查，均通过。双错误场景覆盖 200% 的
  640×600、1280×1000 窗口，以及 400% 的 1280×1000 窗口，并检查恢复正常尺寸；
  无错误场景覆盖 200% 的 1440×1000、1280×900 窗口及 400% 的 1280×1000 窗口，
  验证图表、数据表、持仓保存、弹窗焦点约束和焦点恢复。
- 真实账号只读恢复既有研究的五类结果，0 次研究重提、0 个页面错误。
  故障场景通过浏览器 API fixture 模拟，没有创建生产测试研究或持仓。
- 五个应用服务及入口 nginx 正常，Web 自动重启计数为 0；公网首页 200，
  未登录 session API 为预期的 401。

证据位于 `.local/overflow-fix/`：`before.log`、`targeted.log`、`regression.log`、
`public-tests.log`、`zoom-errors.json`、`normal-zoom/zoom-confirmed.json`、
`public-errors/zoom-errors.json`、`public-normal/zoom-confirmed.json`、`build.log`、
`live-check.log` 及截图。原生读屏、iOS 真机、RTL 仍未验证。

Web 发布目录 `/opt/hermes-alpha-lab/releases/20261109Toverflowfix`，公网构建
`IETK73C_A0lh8mYWG0Lsw`。回退单元备份为
`/opt/hermes-alpha-lab/private/web-before-overflowfix.service`；保留上一版源站目录
`20261109Tzoomfix` 和入口构建 `MKjc5CqXGucmiXnfst30M`。只切换 Web 服务及入口静态页面。

复验结论：**Approve** — 本轮双错误提示与缩放叠加导致工作区不可达的 HIGH 已修复。

## 持仓弹窗焦点与滚动边界修复（2026-11-09）

修复 better-interface 的两项发现：画布打开时，关闭持仓弹窗会被画布 effect
再次聚焦到关闭按钮（MEDIUM）；低高度窗口中，持仓弹窗滚到边缘后会带动背景页面（LOW）。

`page.tsx` 将画布开启时的首次聚焦与 Escape 监听拆开。首次聚焦只依赖
`canvasOpen`；Escape 监听仍在持仓弹窗打开时暂停，避免一次 Escape 同时关闭两层。
弹窗关闭时保留原有触发按钮焦点恢复。`workspace.css` 为 `.modal` 增加
`overscroll-behavior: contain`，阻止弹窗内部滚动到边缘后向背景传递。

验证结果：

- 新增焦点回归用例，修复前因未返回持仓按钮失败，修复后通过；覆盖画布开关两种状态、
  Escape 和关闭按钮两种方式，确认弹窗关闭后画布保留，下一次 Escape 仍可正常收起画布。
- 本地和公网完整回归均为 28 passed；类型检查、修改代码格式检查及生产构建通过。
- 本地和公网独立复现脚本均通过：两种画布状态都返回持仓按钮；弹窗顶部上滚 600px
  后，背景滚动位置保持 1981px，关闭弹窗后位置不跳动且焦点返回原按钮。
- 本地和公网三组原生缩放检查均通过：200% 的 1440×1000、1280×900 窗口，
  400% 的 1280×1000 窗口；图表、数据表、保存按钮和弹窗焦点约束可用。
- 真实账号只读恢复已有研究五类结果，0 次研究重提、0 个页面错误；五个应用服务与
  入口 nginx 正常，Web 自动重启计数为 0，公网首页 200、未登录 session API 401。

证据位于 `.local/modal-fix/` 的 `before.log`、`targeted.log`、`regression.log`、
`public-tests.log`、`modal-focus.json`、`modal-scroll.json`、`public/`、`public-zoom/`、
`build.log`、`live-check.log` 及截图。原生读屏、iOS 真机、RTL 未补测。

Web 发布目录 `/opt/hermes-alpha-lab/releases/20261109Tmodalfix`，公网构建
`iHlCn0VB8wqC_whs9ZxBN`。回退单元备份
`/opt/hermes-alpha-lab/private/web-before-modalfix.service`；上一版源站目录
`20261109Toverflowfix` 和入口构建 `IETK73C_A0lh8mYWG0Lsw` 保留。只更新 Web 服务与入口静态页面。

复验结论：**Approve** — 本轮持仓弹窗的两项发现均已修复并在公网复验通过。

## 云端 Hermes v0.23.1 升级（2026-11-12）

DigitalOcean `sg-prod` 已于 06:48 UTC 从 v0.23.0 切换至 v0.23.1。
上游提交 `3b421578588243f920886e6fe79a6faa87ea026e`，基础镜像固定为
`nousresearch/hermes-agent@sha256:276d44685c65c0cb2a25f190b63c65c8906f9dc76486fb1c05ae71b5826bda08`。
生产自定义镜像为
`sha256:dcfb1d2dc1a4e4375dad793de523611e2eeac80b36d954b3b0ab533a6e960465`。
两个既有用户的六个 Profile、Worker 注册表、Supervisor 注册表及未来注册的准备策略均使用新镜像。
保留原 Profile 身份、端口、凭据、会话、记忆和历史执行记录；只在确认容器停止且无未完成执行后重新绑定运行容器。
历史准备任务及其 settings hash 保留；自动准备策略的 image/hash 在停机期间做了显式事务迁移。

验证及修复：

- 31 项源码审计/桥接/启动/目录权限测试通过；两个工具目录与权限校验测试、TypeScript 类型检查通过。
- 云端六容器隔离、重启重放、进程崩溃恢复测试通过。模型连通性测试最初因测试服务只绑定 loopback 失败，改为支持 `ALPHA_TEST_BIND_HOST` 后三个角色及重启重放全部通过。
- 首次真实研究中模型把组合结果引用用于研究摘要，接口正确返回 422。补充了工具目录中的组件/结果类型对应关系后，最终镜像的完整真实研究通过：GLM 5.3 Flash、Alpaca IEX、三个角色、两个行情数据集、四个计算结果、五种 Canvas 组件、24 次工具请求及跨用户拒绝验证。
- 生产每个 Profile 都通过真实模型调用、隔离、持久幂等、停止等待工具退出、终态输出、会话身份与重启重放检查。首轮因模型调用超过 120 秒自动回滚；重试使用 240 秒等待上限，未放宽功能断言。停止 Supervisor 引发的 `supervisor_check_failed` 仅在原容器身份匹配、实际停止且无未完成执行时清除，其他未知状态不予放行。
- Web、Worker、Tools、Supervisor、Preparer、Feishu 六个服务 active，所查后台服务 NRestarts 均为 0，恢复后无 warning 日志；公网首页 200，未登录 session API 401。
- 生产任务仍为原来的 8 completed / 2 failed，没有重提用户研究。六个 Profile 无需求时保持 stopped，由 Supervisor 按需启动。

运行脚本及日志：`/opt/hermes-alpha-lab/upgrades/hermes-0231/`，成功切换日志
`upgrade-attempt3.log`，最终验收脚本 `verify.py`。生产逐 Profile 证据位于各私有 Profile 目录的 `evidence/`。
私有回滚备份：`/opt/hermes-alpha-lab/private/hermes-upgrade-v0231-attempt3/`，包含数据库 dump、运行目录、配置和迁移前状态；初次回滚证据也保留。
旧镜像仍保留。后续如需回滚，应先停止任务接收/处理并核对新增执行与会话，不能直接覆盖升级后的用户数据。

`activate-verified-profiles.py --no-start` 可在维护期间先发布已经验证的注册表，待其他服务健康后再恢复 Worker。
本次没有更换模型凭据、修改飞书应用配置或发布新的 Web 构建；没有通过飞书发送验收消息。

## 飞书多人接入开发状态（2026-11-12，尚未发布）

本地已加入 020–028 兼容迁移：飞书身份映射、私聊登录挑战、OAuth 状态、执行上下文与 Profile 归属、群准备任务、绑定挑战、验收容量预约、研究来源。新增用户无邮箱/密码；现有个人 Profile ID 保持不变。群与个人 Profile、目录及 Compose 身份已区分，网页群研究只读。

Worker/Supervisor 支持动态发布指针，发布器先验证完整快照再切换 current.json；自动准备可通过 HERMES_AUTO_VERIFY=1 和 HERMES_PUBLICATION_ROOT 接入受限工具真实验收。当前未在服务器启用这些开关。验收默认使用 3411，保持生产 Tools 3410；异常预约保留 unknown，必须确认执行和容器退出后才能释放。

服务器只读检查：sg-prod 的 Web、Worker、Supervisor、飞书服务 active，PostgreSQL healthy，无正在运行的 Profile 容器。Web 工作目录仍为 /opt/hermes-alpha-lab/releases/20261109Tmodalfix；本轮没有生产数据库迁移、服务切换或飞书后台发布。现有角色应使用 scripts/upgrade-multiuser-access.ts 增量授权，不能重跑首次角色创建脚本。

本地生产构建通过。测试覆盖身份并发、登录重放/浏览器边界、绑定冲突、上下文选择、群只读、预约互斥、运行身份保留与发布失败保留旧指针；不代表真实外部企业账号、手机飞书或生产自动开通链已通过。

上线前仍须逐项验证：生产副本迁移与授权、完整群引用隔离、真实契约验收与发布、重启恢复/取消容量、网页登录与手机历史恢复、真实行情与五类图表、飞书对外共享及 OAuth 回调配置。不得仅凭本地测试开启对外试用。

### 隔离候选验证

候选版本已上传至 sg-prod 的 `/opt/hermes-alpha-lab/releases/20261112-multiuser-candidate`。生产库备份保存在服务器私有目录 `private/multiuser-verification/baseline.dump`，恢复至独立的 `alpha_multiuser_20261112_test`，未修改生产数据库。

`scripts/check-multiuser-migration.ts` 已在该副本运行通过：账户、线程、任务、Profile、会话、持仓记录数量不变；数据集、结果、消息、Profile 会话、持仓快照逐行摘要一致；原 Profile ID/所有者/角色不变，个人上下文完整回填；迁移可重复运行。候选构建与服务端测试日志保留在同一私有验证目录。此检查不等于生产发布或真实飞书外部用户验收。

### 本人账号端到端验证与有限上线

后续已完成生产兼容迁移至 028，并为既有 Web/Worker/Tools/飞书数据库角色增量授权；切换 Web 与飞书至候选目录，入口服务器同步构建 AMgTs54c3WGscvLuDy-iY。systemd 的 90-multiuser-login.conf 仅启用 FEISHU_LOGIN_ENABLED，其余多人、新增准入、群研究、OAuth 开关保持 0。Worker/Tools/Supervisor/Preparer 仍运行原版本，尚非完整多人上线。切换前备份为 private/multiuser-verification/pre-login-cutover.dump；原入口目标记录在入口服务器 /tmp/youwei-pre-login-edge-target。

用户已实际完成网页生成挑战、飞书私聊确认、原浏览器完成登录；后台验证 1 次完成登录、映射到原账户、无登录命令进入研究任务。本人 /status 回复正常。

独立测试群上下文的三个 Profile 已完成真实模型调用、工具身份、取消等待工具退出、重启后同键恢复，6 条观察分别为 completed/cancelled。契约后发布暴露两处问题：生成文件权限 0644 被私有发布器拒绝，以及部署校验仍依赖进程环境密钥。已修正为 0077 文件生成掩码及使用已加载运行身份校验，重放已有真实证据后的发布成功；7 项准备回归测试通过。验收结束后全部测试容器移除，原六项服务 active。

外部企业账号暂不可用，用户当前只有本人账号，因此跨企业验收仍未完成。新的 AAPL 私聊研究正在等待用户发起；不能将同意继续测试记录为研究链路通过。

### 群环境联调开始

用户确认网页刷新、退出后重新飞书登录均能恢复历史，已创建测试群并邀请助手。后续已切换 Worker/Tools/Supervisor/Preparer 至候选版本，使用保留原六个 Profile 的发布指针；配置单根容量控制与自动验收。发布快照中的证据复制到 Worker 可读的私有版本目录，由显式配置的 Worker UID/GID 持有，未开放模型凭据目录。

群开关已为原本人账号开启，多人和新增用户准入仍关闭。用户在群中实际 @ 后发送 /status，原群回复成功；独立上下文 e241924e-ae07-45b7-af0f-cd2004c38dbc 自动创建三个新 Profile，预约端口 19006–19008。真实验收已自动启动，期间生产数据库持有 acceptance_capacity 预约，研究执行数为 0，运行 Profile 容器数为 3。此时尚未记录群研究通过；须待验收、发布、受管登记后验证。


## 持续研究与预测闭环（2026-11-12，本地验收，尚未发布）

已接入现有 research / prediction / portfolio Profile、PostgreSQL、Task Worker、工具网关和 Quant：

- 个人研究池以持仓与自选优先，陌生股票建立临时档案，临时/市场候选默认保留 20 个交易日。每日扫描、默认 30 只深研及 10 只新候选预算，超额工作持久保存并在后续交易日续跑；交互任务在阶段之间优先。
- 新闻、SEC、公司 IR / 公开网页、X 和 FRED 适配器记录来源状态、公开/获取时间及不可变内容。整轮采集共享 22 秒截止时间，缺配置与缺行情保留原因。
- 发布 5/20 个交易日的预期收益、80% 区间及上涨概率，冻结证据与行情版本。原判断不可改写，修订另存；到期同一事件可以建立新的原始预测期。确定性到期结算保存方向、概率及区间评分与 SPY 参照。
- 成功和失败均进入复盘。候选经验通过两个独立会话试验，候选臂之外不能读取候选规则；达到新事件批次门槛才采用，后续退化会降回影子状态。
- `/lab` 展示研究池、预测记录、经验试验、研究设置和通知；Canvas 与飞书结果卡支持新预测。主动飞书通知使用持久 outbox 和相同请求 UUID 重试。

最终验证：

| 检查 | 结果 |
|---|---|
| 完整 PostgreSQL / TypeScript 服务测试 | 178 passed，0 skipped；含真实受限 Web / Worker / Tools 登录、后台调度、未知提交结果恢复、双臂经验与到期续期 |
| Python 全套测试 | 48 passed；日历依赖产生 12 条 Pandas4Warning |
| Chrome 浏览器用例（分组执行） | 32 passed；包含研究池操作、设置刷新恢复、预测证据与结算、原有聊天/Canvas、断线及移动布局 |
| TypeScript 与生产构建 | `pnpm typecheck`、`pnpm build` 通过；构建包含 `/lab` |
| 工具目录与格式 | `pnpm hermes:catalog` 已更新；新增核心文件 Prettier 检查通过；本次 Quant 文件 Ruff 检查通过 |

全库 `uv run ruff check .` 仍报告 9 处既有部署脚本格式问题，位于 `infra/deployment/activate-verified-profiles.py` 和 `infra/deployment/check-profiles.py`；本次未修改这些脚本。

后端测试每次使用独立随机 schema。浏览器使用新建的本地 `alpha_forecast_test` 数据库与合成证据，未对真实账户发出通知。新增来源仅验证受控响应、分页、超时与缺项，尚未完成真实 SEC / Brave / X / FRED 账户或全市场扫描的联网验收。新增迁移 029–031 只应用于本地测试数据库；新版代码、工具目录和运行角色授权尚未部署生产。升级步骤见 [README](../README.md#配置来源与启用)。


## 持续研究真实端到端验收（2026-11-12，隔离环境）

当前结论：**真实研究、浏览器与飞书通知投递链路通过。用户明确授权后已完成单条通知发送核验；未切换生产服务。**

在 sg-prod 的独立目录 `releases/20261112-forecast-acceptance`、独立数据库 `alpha_acceptance_test` 的随机 schema 中，使用真实 GLM 5.3 Flash、Alpaca IEX、三个独立 Hermes 容器与生产 Task Worker 执行逻辑。没有使用合成模型或合成行情替代真实调用。

第三轮验收 `run3` 于 11:29 UTC 通过，用时 411 秒：

- 用户模拟持仓是 NVDA，临时询问 AMD；AMD 自动进入 `temporary` 研究池。
- research、prediction、portfolio 三个原生执行均 completed，共 52 次工具请求，保存 50 条真实新闻、两个行情数据集和五项结果。
- 保存 5/20 个交易日原始预测，起算日 2026-11-12，到期日分别为 2026-11-18 和 2026-12-10。
- Canvas 包含股价图、指标、两张前瞻预测卡、组合估值与最终研究摘要；跨用户读取被拒绝。
- Chrome 通过真实账户登录读取同一批预测，验证详情、刷新恢复和 390px 手机布局。浏览器记录的 forecast IDs 与原生执行验收文件逐项一致。
- NVDA 已在第二轮发布两个真实预测并通过浏览器检查，但第二轮根任务因卡片选择错误失败，不能当作完整任务成功。
- 真实扫描取得 6,633 个目录标的中的 6,354 个有效观测，279 个缺少完整数据；新闻窗口截断，扫描状态正确保留 `partial`。保存 50 个排序候选供后续预算选择，不等于执行了 50 个深研任务。
- 当前时间调用真实结算服务，没有提前结算未到期预测。未来实际收益与经验晋升仍需后续交易日数据，本次没有伪造未来结果。

真实验收发现并修复：

1. 单次上下文约 178 KB，被 Hermes 转存为当前工具集无法读取的文件。新增模型侧证据分页，同一真实快照首屏缩至约 7.3 KB，完整快照和证据不变。
2. 后续分页只传快照 ID 时被要求重复传股票代码。现在可直接按快照 ID 分页；若同时传股票代码，仍校验匹配。
3. 模型把组合结果用于预测验证卡片，导致整个任务立即失败。现在返回可纠正的组件提示，协调者可展示已授权的子任务结果；终态前没有保存正确卡片仍判失败。原错误回执保留。

修复后完整后端测试 180 passed，专门覆盖未纠正不能完成、纠正后可完成；类型检查与最终生产构建通过。来源验收目前仅实际接通 Alpaca 新闻/行情与市场扫描：SEC、Brave、X、FRED 尚缺相应配置，未宣称这些来源已通过。股东大会/评论的动态内容及音视频仍未实现。

飞书通知已经在第二轮测试 schema 中持久准备，notification ID 为 `c5ce571f-5d0e-41ac-bcfb-f45f782d9cda`，`sent=false`。请求发送给本人绑定账号的单条测试消息已提交给用户确认，尚未执行外部发送；不把排队成功当作真实投递成功。

证据：服务器私有目录 `/opt/hermes-alpha-lab/private/forecast-acceptance/` 保存 run1/run2 失败记录、run3 成功记录、镜像构建与真实扫描结果。本地 `.local/forecast-acceptance/` 保存成功报告、浏览器结果、截图及扫描报告。隔离验收镜像 digest 为 `sha256:d3da8c7bc3cc0770bf6eabd109238a6d853a9e0d9cbf273c00ac990c8c141da5`。三个验收容器均已清理；生产六项服务仍 active，运行目录未改变。


### 飞书实际投递验收完成（用户明确授权后）

用户明确同意向本人绑定的飞书账号发送一条验收测试通知后，执行了预先准备的通知投递，没有重新创建通知。飞书 API 确认成功并返回消息 ID：`om_x100b606e15f1acb4dc02eec4dbe4675`。

数据库复核：原 notification `c5ce571f-5d0e-41ac-bcfb-f45f782d9cda` 对应 `delivery_mode=create`、`sent=true`、`uncertain=false`；已发送记录数量为 1。没有重复发送。此证据证明飞书接口接受消息并返回已创建消息标识，不代表用户已读。

投递证据保存在服务器私有目录 `forecast-acceptance/notification-delivery.json`，本地副本为 `.local/forecast-acceptance/notification-delivery.json`。本轮隔离环境端到端验收完成；新增 SEC/Brave/X/FRED 配置、未来交易日实际收益检验和生产版本切换仍属于后续工作，不纳入本轮已通过声明。


## 预测与研究沉淀版本生产上线（2026-11-12）

已按用户明确的生产上线请求发布至 https://trading.youwei-agent.com 。
六个 systemd 服务（Web、Worker、Tools、Supervisor、Preparer、飞书）统一运行
`/opt/hermes-alpha-lab/releases/20261112-forecast-production`，既有账号、Profile ID、
密钥、持久化目录及功能开关保留。数据库增量迁移 029–031 与既有运行角色授权已完成。
Node/Python 依赖复用同机 `20261112-multiuser-candidate` 的 node_modules/.venv；
因此该旧目录仍是当前版本依赖，不应清理。Worker/Tools 配置了新版量化源码路径，
Worker/Web/Tools 使用已有 Alpaca 数据源配置。

生产镜像为 `sha256:d3da8c7bc3cc0770bf6eabd109238a6d853a9e0d9cbf273c00ac990c8c141da5`。
三个生产上下文的全部 9 个 Profile 真实通过工具调用、取消等待、幂等、重启恢复及隔离检查；
新建 Profile 的 preparation policy 同步至该镜像。主注册表使用
`/var/lib/hermes-alpha-lab/worker/forecast-publications-20261112-r3/current.json`，
兼容注册表也同步为全部 9 个已验证 Profile。
阿里云入口静态资源与源站构建一致：`CuLib1B_up84rxCkS2fQL`。

上线前完整备份位于服务器私有目录 `private/forecast-production-20261112-r2/`，
含迁移前数据库与运行状态；最终切换备份及逐组验证证据位于
`private/forecast-production-20261112-r3/`。首次排空检查遇到 supervisor 退出竞态，
经真实 Docker 退出核对后继续；一次注册表发布因部署进程缺少能力密钥而中止并恢复旧服务，
随后补齐部署环境、完成发布预检并重新验证全部 Profile 后成功切换。
回退应先停止接收和调度新任务、核对全部执行终态，再恢复旧运行配置与入口目标；
029–031 为增量迁移，可保留。恢复数据库或 Profile home 必须评估上线后的新增数据，
不可直接覆盖。旧入口目标保存在本地 `.local/forecast-production/previous-edge-target`。

上线验证：六服务 active、飞书 connected；真实公网账号登录 200、research-hub 200，
预测页、刷新和 390px 手机布局通过，无 pageerror/5xx；后台 settle/close/digest 已产生
成功记录，研究任务已按持仓和候选池入队。生产库未导入隔离验收库的测试预测。
本地证据为 `.local/forecast-production/verification.json`、`browser-result.json`、
`desktop.png`、`mobile.png`、`deploy.log`。此次没有另发飞书验收通知。

当前接入 Alpaca 行情与新闻；SEC_USER_AGENT、BRAVE_SEARCH_API_KEY、X_BEARER_TOKEN、
FRED_API_KEY 尚未配置，相关来源明确显示缺失。真实 5/20 交易日到期后的结算和经验效果
仍需随运行积累，不能将上线健康检查当作预测准确率验证。

## 后台研究修复部署（2026-11-13）

Worker、Tools 已切换到 `/opt/hermes-alpha-lab/releases/20261113-research-repair`，通过 `zz-research-repair.conf` 设置工作目录和 Quant 路径；Web 和入口静态构建保持原版。本次仅复制 tasks、worker、data-service、research-scheduler 四个服务端文件和迁移 032，其余文件来自既有生产发布。

生产迁移 032 已应用：1 个已绑定飞书账号每日候选预算为 0，20 条候选覆盖暂停。63 条历史失败研究保留，未重跑。数据库 dump 和原 Worker/Tools drop-in 备份位于 `/opt/hermes-alpha-lab/private/research-repair-20261113/`。回退代码可移除两服务的 `zz-research-repair.conf` 后 daemon-reload 并重启；候选暂停应保留，禁止直接恢复 dump 覆盖新增数据。

本地类型检查、构建及 14 项 PostgreSQL 调度测试通过，服务器类型检查通过。切换后两服务启动成功，公网返回 200。真实 AAPL 后台采集、分析和 5/20 日预测完整验收尚未完成，不能把本次部署健康检查视为预测闭环通过。

## 后台真实 AAPL 端到端验收（2026-11-13）

在已部署的 `20261113-research-repair` 目录运行新增 `tests/hermes/live_background_integration.py` / `tests/support/hermes-background-live.ts`，调用 ResearchScheduler.enqueueJob 与 advanceJobs，真实 Worker、Hermes 模型和 Alpaca 行情完成 collect → analyze → publish。独立 alpha_acceptance_test 随机 schema、账号和容器，不使用生产账号持仓，不绑定飞书（绑定及 outbox 均为 0）。测试通过，耗时 616.06 秒。

三阶段及 prediction 子任务均 completed，预算依次 300/300/600 秒；子任务 deadline 与发布父任务一致。AAPL 行情截止 2026-11-12，两条预测引用同一冻结上下文，包含 50 条证据及 1 个行情数据集，保存 2 个预测卡片记录：
- 5 日：219b2dae-f289-4808-90fa-d87bb6fe95bc
- 20 日：dd47a917-c666-44d5-bcb6-a053f7bd51ae

证据保留在服务器 private/background-acceptance-20261113/run1 和本地 .local/background-acceptance-20261113/research-result.json。测试容器全部退出，生产 Worker/Tools 仍 active。本次验证了后台执行与持久化，不包含公网测试账号登录后的浏览器渲染，也不证明未来预测准确率。部分可选来源仍未配置；模型存在重复读取上下文，未导致本次失败。

## 自适应证据分页部署与验收回退（2026-11-13）

优化版本 `20261113-research-pagination` 部署到 Worker/Tools，通过服务器类型检查与两项分页回归。随后在该目录运行独立账号、独立测试库及真实模型/Alpaca AAPL 后台验收，未接入飞书。

本轮未通过：总计 731.98 秒，研究执行 641.993 秒。采集、分析完成；上下文调用从上轮 20 次降至 8 次（本轮 51 条证据，上轮 50 条，模型路径不同，非严格同输入基准）。publish_forecast 五次返回 400，预测记录为 0，发布任务以 required_tool_failed 结束。模型关于“网关不稳定”的解释没有得到独立证据支持；尚不能确定参数错误根因或与分页的因果关系。调用减少不等于总耗时改善。

生产无活动任务后，移除 zzz-research-pagination.conf（保存到 private/pagination-acceptance-20261113）并重启 Worker/Tools，恢复到 20261113-research-repair。未变更数据库或网页构建。测试容器已退出，原始结果保存在服务器 private/pagination-acceptance-20261113/run1 和本地 .local/pagination-acceptance-20261113/research-result.json。优化版本尚未通过上线验收，不保留为生产版本。

## 飞书响应分流与等待反馈（2026-11-13）

生产记录确认 hello 曾排队 690 秒、运行环境启动约 33 秒、最终 765 秒回复；“你是谁”两次约 56/59 秒。固定问候与身份介绍不应占用研究任务。

已部署 Feishu 到 `20261113-feishu-fast-replies-r2`：问候直接生成 outbox；“查看 AAPL 研究/最新预测”和 /research AAPL 按账号与 execution context 读取已有摘要/预测，并显示时间与过期提示，无结果不自动启动模型。支持摘要 text 及 lineage 关联股票。仍需研究的请求保持异步；30 秒后按 queued/running/stopping 状态生成去重进度通知。未强制抢占后台执行，不承诺任意对话秒回。

6 项本地回归及本地/服务器类型检查通过。生产角色 alpha_live_feishu 在事务回滚测试中，hello/身份介绍约 3–4ms、已有结果查询约 13ms，未新增任务或投递外部消息；只证明服务端处理路径，不能当作飞书客户端端到端延迟。已补充 forecasts/research_contexts SELECT 授权并同步授权脚本；查询强制按 owner/context 隔离。回退可将 zzz-fast-replies.conf 工作目录恢复到前一发布再重启 Feishu，不需要数据回滚。

后续补验：r3 已部署，投递时重新检查 queued/running/stopping 状态，已结束或已转阶段的进度消息不再发送。对应回归先复现旧提示误发 3 条，再修复为 0 条，10 项相关测试通过。已有结果测试追加了真实表结构中的 5/20 日预测、收益/概率显示、过期标记及群聊不可读取个人预测的断言，均通过。

截至本次检查，上线后 feishu_inbox 新消息数为 0。已请求用户从私聊和群 @ 发起真实查询，用于核对从接收到投递的时间；此项尚无实测证据，不能把服务端毫秒级测试表述为用户侧秒回。

## 自然语言查股价直连行情（2026-11-13）

用户真实截图对应请求“可以帮我查询一下英伟达的股票吗”：03:20:23 UTC 接收，1 秒确认、36 秒提交模型、190 秒最终回复。固定快捷语法没有覆盖该自然语言，原流程错误地启动了完整研究。此前“你好”已记录为不足 1 秒回复。

现将简单价格查询在入库研究前分流，识别明确股票代码与常用中文公司名；显式分析、历史、预测、多股票混合等不降级为单股报价。直接调用既有 Alpaca latest trade 接口，6 秒超时；注明成交时间、USD、IEX/SIP 来源及非实时买卖报价，失败不使用旧值替代、不转为研究。官方接口依据 https://docs.alpaca.markets/us/reference/stocklatesttradesingle-1 。查询在数据库事务外完成。

生产服务器真实请求耗时 875ms（不含飞书投递），返回 NVDA 167.1 USD，成交时间 2026-11-12T21:58:26.191520776Z，Alpaca IEX。12 项相关回归及服务器类型检查通过。Feishu 已切换到 20261113-feishu-quotes 并加载既有只读行情凭据环境文件；未发测试飞书消息。下一次真实用户查询仍需核对飞书接收至投递时延。

## 公司名称误作代码修复（2026-11-13）

真实消息“可以帮我查询一下spacex”于 03:34:43 UTC 收到，0.99 秒发出失败回复，未创建研究任务。延迟已改善，但未解析公司名称便把 SPACEX 当股票代码，且错误统一提示稍后重试。现补充常见英文公司别名；未映射的长字母名称先澄清公司资料/股票代码，不对其上市状态作推断；行情 HTTP 404 单独说明代码或覆盖不可用，不要求盲目重试。四项单测在修复前复现两个失败，修复后本地/服务器通过，类型检查通过。Feishu 发布更新为 20261113-feishu-quotes-r2。此改动尚非通用公司实体解析服务。

## 快捷查询澄清上下文（2026-11-13）

用户反馈 SpaceX 澄清后回复“股票行情”，旧流程将该短句送入历史模型会话，返回无关 AAPL/NVDA。现迁移 033 在 feishu_chats 增加 lookup_context；按既有发送者/聊天键保存 target/at，10 分钟过期，其他非行情输入（含 /new）清除。价格类短句在无上下文时要求明确对象，有上下文时只延续该对象，未确认代码时不会生成其他股票的研究任务。

发布为 20261113-feishu-context，服务器类型检查、五项报价路由测试通过；生产 Feishu 角色能读写新列（回滚事务验证），服务重新连接成功。尚未自动解决未知公司对应上市标的的通用实体识别；不得据此宣称 SpaceX 上市或未上市。该次生产修复不补发用户消息。

## SpaceX 官方代码核实与真实行情闭环（2026-11-13）

此前仅用长度启发式拒绝 SPACEX，反复让用户提供代码，没有解决用户查询目标。此次核实官方 https://ir.spacex.com/faqs-and-contact/ 及 Nasdaq 官方公告：代码为 SPCX，2026-06-12 开始交易。添加带核实日期/来源的 SpaceX → SPCX 映射；旧澄清上下文 SPACEX 也重新解析后查询 SPCX，显示 SpaceX（SPCX）。未知名称的澄清测试改用 unknownco，不能继续把已核实名称的“无法查询”作为通过标准。

在生产服务器候选目录以真实凭据测试用户原句及“股票行情”追问，分别 937ms/720ms，均返回 140.17 USD、成交时间 2026-11-12T21:29:49.677280387Z、Alpaca IEX。不把该成交价称为实时全市场报价。服务器类型检查和五项回归通过，Feishu 切换到 20261113-feishu-symbols；未发送测试消息。此次解决了 SpaceX 的实际查询，尚不代表通用公司名称解析已完成。

## GitHub 同步验证（2026-11-13）

从远程 develop 的 12b30c5 基线建立独立检出，与当前工作目录同步 25 个变更文件；排除 .local、环境凭据、依赖、缓存、浏览器截图及临时验收产物。类型检查、Next 构建与全部 192 项服务端测试通过。修正后台验收脚本卡片联表为 source_task_id（匹配实际 schema），本次未重新跑真实模型验收。当前完成/剩余事项已集中更新 production-follow-up.md；历史失败与回退结论保留。GitHub 代码快照包含未通过真实验收的自适应分页候选，不等于生产统一发布版本。

## 研究轨迹工具摘要与失败策略发布（2026-11-14）

发布范围为本次会话的定向变更，不包含本地保留但生产已回退的自适应证据分页候选：

- Web：`20261114-tool-trace-web`（首个 BUILD_ID `w50Um-25TksiGcEqY3cmr`，随后追补为 `7Gh5dl6sNvUrbDa_pCnf1`），在 `20261115-workbench-v2` 基础上覆盖研究轨迹组件、进度聚合、样式及 BFF 快照字段。
- Worker：`20261114-tool-trace-worker`，在 `20261113-research-repair` 基础上覆盖事件映射、任务归档、Worker 失败判定及新增 `tool-summary`/`tool-policy`/`failure-text`。
- Tools/Feishu 未变更；无数据库迁移。阿里云入口通过 `publish-edge.py` 发布同一构建，公网 `/` 的 SHA-256 与源站 `server/app/index.html` 完全一致，静态 chunk 返回 200，无 Cookie 的 `/api/auth/session` 仍返回 401。

本次行为变化：工具开始/完成合并为一行并按真实工具名显示；白名单工具摘要（标的、日期范围、来源、计数、耗时）写入 `run.progress.detail`，原始预览、推理和参数全文仍不归档；终态消息与「正在停止」行显示失败原因；仅关键工具在无后续成功调用时判定任务失败，采集/读取类失败按覆盖缺失保留；失败任务头部不再显示绿色对勾。

验证：两个发布目录 `pnpm typecheck` 通过；服务器独立测试库 `alpha_acceptance_test` 上 `pnpm test` 196/196 通过；Next 构建成功。切换前确认无 queued/running/stopping/unknown 任务。修正了三处旧测试：快照对比需排除已随事件过期的 `progress` 字段。

未验证：发布后未触发真实模型研究，因此生产环境的实时轨迹渲染、工具摘要和失败原因展示尚缺用户侧截图证据；本次健康检查不等同于真实研究端到端通过。

回退：删除 `alpha-lab-web.service.d/zzz-tool-trace.conf` 与 `alpha-lab-worker.service.d/zzz-tool-trace.conf`，`systemctl daemon-reload` 后重启两服务即可回到 `20261115-workbench-v2` 和 `20261113-research-repair`；入口 `current` 可指回 `vl5YJ7kT0pMn73kcAZKIO` 构建，旧静态资源保留。

### 追补：模型说明与上游思考预览（2026-11-14）

生产截图反馈仍看不到模型思考过程。核查锁定镜像 `alpha-lab-hermes:forecast-acceptance-20261112-r3` 确认：Runs API 未向 agent 传 `reasoning_callback`（`gateway/platforms/api_server_runs.py:1156`），原生 reasoning 不进入 run SSE；仅在模型未走原生 reasoning 时提供 ≤500 字符的 `reasoning.available` 预览，另有少量 `message.interim` 中间说明。私有思维链不可获得。

本次追补：

- Web 更新为 BUILD_ID `7Gh5dl6sNvUrbDa_pCnf1`：轨迹按工具切分显示模型可见说明（取上一个工具之后、当前工具之前的 `assistant.delta` 文本，截取 800 字符、最多 4 行）；`reasoning.available` / `message.interim` 以「思考 / 说明」行展示；低于 0.1 秒的耗时不再显示。
- Worker 同步归档上述两类上游文本（脱敏、≤600 字符）；原始参数与供应商私有推理仍不归档。
- 验证：worker 发布目录 `pnpm typecheck` 与 197/197 测试通过；Web 构建成功；公网 `/` 的 SHA-256 与源站 `server/app/index.html` 一致，静态 chunk 返回 200，无 Cookie 的 `/api/auth/session` 返回 401。首次 Web 切换时有一个研究任务正在运行，因此先只重启 Web；任务自然结束后确认活动任务为 0 才重启 Worker，未打断执行。
- 未验证：当前模型（`anthropic_messages`）未观察到 `reasoning.available`/`message.interim`；「模型说明」来自可见回复文本，需要用户下一次研究确认显示效果。若需要真正的原生推理链，必须修改 Hermes 镜像转发 reasoning 流，属于独立变更。

## Hermes 原生推理链部署（2026-11-14）

为在轨迹中展示 Hermes 的原生思考，构建了新的自定义 Hermes 镜像并完成生产重新验证：

- 补丁（`services/hermes/apply_patch.py`）：Runs API 此前没有向 canonical turn 传 `reasoning_callback`（`session_api_turn.py` 本身已支持 observer）。现按工具边界缓冲模型原生 reasoning，脱敏后以 `reasoning.delta` 事件写入 run SSE，每块上限 2000 字符；同时覆盖 admission 与非 admission 两条执行路径。推理文本不并入最终回答或 `stream_text`。补丁清单、事件契约与升级规则见 [Hermes 上游源码补丁清单](hermes-upstream-patches.md)。
- 镜像：`sha256:cd54b181d56b6b4a794e5e1c3d2417d234aa30c41bdc206b7f63d86bfe0965dd`（`alpha-lab-hermes:reasoning-20261114`），基于锁定上游 v0.23.1 构建，未改变 `upstream.json` 的哈希钉。
- 适配层/Worker/Web：消费 `reasoning.delta` 并显示为「思考」行；`pnpm typecheck` 通过，服务端测试 197/197；Web 构建 `pRdX6uxRQp1Zal6BrmrX0`，公网 `/` 与源站 `server/app/index.html` 的 SHA-256 一致。

验证证据：

- 隔离真实模型验收（`tests/hermes/provider_integration.py`，新镜像）：3 个角色全部通过，包含工具往返与 research 重启重放，耗时 130.77 秒。
- 定向探针读取真实 run SSE：research/prediction/portfolio 分别收到 2/2/1 个 `reasoning.delta`，内容为可读的推理摘要；确认不是只改镜像而无事件。
- `check-profiles.py` 对三个 owner、9 个 Profile 逐一通过（真实模型、工具服务停止、合成工具响应），每次生成新镜像证据。
- `activate-verified-profiles.py` 激活 9 个 Profile；由于 Supervisor 使用独立的 publication 指针，另以 `publishDeployment` 在 `forecast-publications-20261114-reasoning` 生成新版本并切换 `HERMES_PUBLICATION_POINTER`。五个服务 active，Worker 与 publication 注册表均为新镜像，活动任务为 0。

回退：恢复 `reasoning-image-backup-20261114/` 下的 `compose.json`、`preparation.json`、两个 registry；删除 `alpha-lab-supervisor.service.d/zzz-reasoning-publication.conf`；恢复 `activation-20261114T225134Z/previous-worker-registry.json` 到 Worker registry；重启 Worker 与 Supervisor。旧镜像 `d3da8c7b…` 与旧 publication 仍保留。

未验证：切换后尚未发起真实用户研究，生产页面的「思考」行渲染尚缺用户侧截图；隔离探针只证明事件与内容存在。

## 镜像切换后的队列阻塞与恢复（2026-11-14）

镜像切换后用户提交的研究任务在队列停留超过 4 分钟。任务本身没有失败，Worker 也没有领取；容量闸门被 Profile 生命周期状态挡住。

根因链：

1. `compose.json` 的镜像变更会改变 `deploymentHash`（`digest({files,homeIdentity})`，其中包含 compose 内容），但 `profile_instances.deployment_hash` 仍指向旧部署。
2. Supervisor 每次 tick 在 `profile_deployment_mismatch` 处抛错，catch 将 Profile 记为 `unknown / supervisor_check_failed`，不会自动重绑。
3. `capacityAvailable` 将任何 lifecycle-managed 的 `unknown/starting/ready/stopping` Profile 视为占用（即使属于同一 owner 的另一套 context 或其他 owner），因此任务无法被领取。
4. `check-profiles.py` 使用生产 compose 与生产容器名启动验收容器，结束时 `compose down` 移除它们；DB 中记录的旧 `container_id` 已不存在，tick 随后进入 `container_identity_changed`，而该状态按设计拒绝自动恢复。

恢复步骤（已执行）：

- 用 `loadDeployments` 重新计算 9 个注册 Profile 的 `deploymentHash`，备份 `profile_instances` 后以事务更新。
- 对 4 个 `container_identity_changed` 的 Profile 运行只读 `hermes:reconcile inspect`：`submitted/tools/calls` 均为 0；`docker inspect` 确认记录的容器 ID 全部不存在。
- 再次备份后，以事务将这四个 Profile 置为 `stopped`、清空 `lifecycle_error`、`container_id`、`container_started_at`；不修改任务结果或执行证据。
- Supervisor 随即用新镜像启动 research Profile（容器镜像 `cd54b181d56b`，状态 ready），Worker 提交执行，任务进入 running。

发布流程缺口与修复（P1，已实现）：

- `activate-verified-profiles.py` 现在在预检中通过 `loadDeployments` 重算哈希，并在同一数据库事务里重绑所有 Profile 的 `profile_instances.deployment_hash`；数据库连接来自私有 `database-services/worker.env`。2026-11-14 在生产重跑激活，输出 `hashesSynced: 9`，服务正常重启。
- 新增 `hermes:reconcile confirm-missing`：要求原 64 位容器 ID 与托管容器名都不存在、且 `submitted/tools/calls` 均为 0，才把 Profile 置为 stopped 并清空 `container_id`/`container_started_at`；存活的容器（即使已停止）返回 `container_present`。它写入同一条不可变生命周期审计，不改变任务结果或执行证据。
- 验证：服务端测试 198/198 通过（新增 confirm-missing 的缺失容器、替换容器、未完成执行拒绝用例）；生产冒烟中错误容器 ID 返回 `container_identity_changed`，`inspect` 显示 `state=stopped, submitted/tools/calls=0`。Supervisor 发布目录已更新并重启（`Profile supervisor started { profiles: 9 }`）。
- 剩余风险：`check-profiles.py` 仍复用生产容器名，验收会移除原容器；已记录为后续改进（隔离命名），当前可用 `confirm-missing` 受控恢复。

### 追补：推理链中文化（2026-11-14）

角色指令（research/prediction/portfolio，含后台阶段与试验臂）追加统一的输出语言要求：推理、进度说明与最终回答使用简体中文；工具名、参数名、标的代码、ISO 日期、JSON 键和数值保持原样，不翻译标识符。Worker 在无活动任务时重启加载该指令（23:24 UTC）。

边界：原生推理语言由模型决定，指令不保证所有供应商都遵循；已归档的英文推理不会回溯翻译，只影响新任务。若新任务仍为英文，可再评估展示层翻译（需要额外模型调用）或仅展示中文可见说明。

### 追补：轨迹可读性与中文优先（2026-11-14）

- 思考块不再作为编号步骤：只有工具与任务状态步骤显示序号，头部步数只统计实际步骤；流式占位「模型思考」不再渲染为时间线行（头部仍显示生成状态）。
- 思考块改为带左侧竖线的独立区域；连续推理合并；推理文本中的工具/组件标识映射为中文名称，截断产生的半词开头会被清理。
- 中文优先：默认展示模型可见说明（中文，作为「思考」块）；原生英文推理收进可展开的「推理原文」；没有中文说明的步骤（例如专项子任务）默认展开原文。
- 实测语言指令未改变原生推理语言：最新 `reasoning.delta` 仍为英文，只有模型正文为中文。因此采用中文优先展示，而不是额外调用翻译。
- Web 构建 `FrqFaYL-XP4LenBLcOsZJ`，公网 `/` 与源站 `index.html` 的 SHA-256 一致，未登录 API 仍返回 401。
- 限制：中文「思考」来自流式可见文本，仅实时任务可见；刷新后不会重建中文块，仍可展开英文「推理原文」。若要持久化中文说明，需要把每个工具前的助手文本片段归档到进度事件。
- 终态图标修正：任务 completed 时头部固定显示完成图标，不再取最后一行（思考）的状态；非运行中的历史「思考」点改为静态，不再播放运行中动画。Web 构建 `8gGkz80uboq4ff7OHNWM4`。
- 重复工具调用折叠：连续、同一工具/角色、都已完成的步骤合并为一行，显示 `×N` 并保留各次摘要的 tooltip；不同工具或中间插入思考块时不合并。头部步数与编号按合并后行数计算。Web 构建 `LhaUBtINdeXY1udvrYfd8`。

## 上下文增量读取（P0，2026-11-15）

真实任务记录显示，一次 80 条证据的研究里 `read_research_context` 被调用了 12 次：每次采集后重新冻结产生新的 `contextRef`，模型又从 offset 0 重新翻页。生产 Tools 每页 5 条，主要浪费是“新快照重复返回已读证据”。

实现：

- `tool-gateway.ts` 在非分页（无 `evidenceOffset`）、非查询（无 `query`）的新上下文读取中，查找同一任务上一次 `read_research_context` 的 `contextRef`，加载旧快照并按证据 `id` 做差集。
- 只返回新增证据（最多 20 条），附 `delta: {previousContextRef,newEvidence,unchangedEvidence,totalEvidence}` 和明确说明“旧证据未变化、已省略”；无新增时返回空证据的短回执。
- 显式 `evidenceOffset`/`query` 保持普通分页；相同 `contextRef` 不触发增量；符号不同则回退完整视图。
- 不改变证据存储或快照语义，只改变模型传输视图。

验证：新增 `research-context-delta.test.ts`（新增/无新增/超限截断）；服务端测试 **201/201** 通过；Tools 发布目录类型检查通过，服务重启于 00:03:34 UTC。尚未用真实任务复测调用次数，需下一次研究观察 `delta` 是否减少翻页。

回退：恢复 `20261113-research-repair/packages/server/src/tool-gateway.ts` 的上一版本并删除 `research-context-delta.ts`，重启 Tools；不涉及数据库变更。

## 回答正文逐字显示发布（2026-11-15）

问题：回答正文虽然走 `assistant.delta` 流式，但每 2–3 秒整段到达。三层延迟叠加：Worker 每次只向上游 SSE 采样 1.5 秒、随后空等 1 秒；BFF 事件端点每 500ms 轮询一次持久化游标；前端到多少渲染多少。

改动：

- Worker：上游采样窗口 1500ms → 500ms，`defer` 空等 1000ms → 250ms（租约仍释放，停止与状态检查不变）。
- BFF SSE：游标轮询 500ms → 200ms；keepalive 限流 5 秒、会话校验限流 2 秒，避免轮询提速后数据库调用同比上升。
- Web：新增 `useSmoothText` 逐字展开（新块约 420ms 追上；历史消息首屏整段显示不重放；最终回答与前缀不一致时直接吸附；`prefers-reduced-motion` 下整段显示），流式中末尾显示细光标。重连续流路径（`runtime.thread.reset`）同样生效。

发布：

- Web `20261115-stream-text-web`，BUILD_ID `ApSM4D5y4fKE5-2IUeOmt`，基于 `20261114-tool-trace-web`；Worker `20261115-stream-text-worker`，基于 `20261114-tool-trace-worker`；对应 drop-in `zzzz-stream-text.conf`。
- Web 发布目录不覆盖 `packages/server/src/worker.ts`：该谱系缺少 `failure-text.ts` 等 Worker 专用模块，覆盖会使目录类型检查失败；Web 进程不加载该文件。Worker 发布目录不覆盖 Web 组件，同理。
- Tools、Feishu、数据库与 Hermes 镜像未变更；无迁移。入口发布同一构建。

验证：两个发布目录 `pnpm typecheck` 通过；Worker 发布目录在 `alpha_acceptance_test` 上 `pnpm test` **201/201** 通过；Web 发布目录 `smooth-text`/`http`/`task-stream`/`upstream-events` 通过；Next 构建成功。切换前确认无 queued/running/stopping/unknown 任务。重启后 Web ready、Worker `Task Worker started`，`/lab` 返回 200，无 Cookie 的 `/api/auth/session` 返回 401。入口 `current` 指向同一 BUILD_ID，公网 `/` 与源站 `server/app/index.html` SHA-256 一致（`08cad6874d6f…`），新 CSS chunk 返回 200 且包含 `.stream-caret`。

未验证：发布后未触发真实研究任务，生产模型链路上的逐字观感与首字延迟仍待用户侧确认；健康检查不等于端到端通过。

限制：轨迹中的「思考」块仍按工具调用边界整块出现，这是 Hermes 上游补丁（`reasoning.delta` 按工具边界缓冲）的有意设计；如需逐字显示思考，需改 `services/hermes/apply_patch.py` 并重建镜像。

回退：删除 `alpha-lab-web.service.d/zzzz-stream-text.conf` 与 `alpha-lab-worker.service.d/zzzz-stream-text.conf`，`systemctl daemon-reload` 后重启两服务，回到 `20261114-tool-trace-web` / `20261114-tool-trace-worker`；入口 `current` 指回 `LhaUBtINdeXY1udvrYfd8`。

## Web 对话/画布比例修复部署（2026-11-15）

- Web 发布目录 `/opt/hermes-alpha-lab/releases/20261115-layout-split`，BUILD_ID `vLDmwS-Nq68Pyl3uvqKZD`。目录基于 `20261115-stream-text-web` 复制，仅覆盖 `apps/web/app/split.css` 与 `apps/web/components/workspace.tsx` 两个文件；Worker/Tools/Feishu/数据库均未变更，无迁移。
- 行为：对话与画布默认 45% / 55%（对话最小 420px、最大 60%，可拖拽分隔条调整）；canvas 不再无条件吃满剩余宽度。≥1100px 时「研究记录与持仓」抽屉改为占位推挤，不再覆盖对话列；窗口或抽屉宽度变化时通过 `ResizeObserver` 重新收敛对话宽度；双击分隔条恢复默认比例。持久化键升级为 `hermes:chat-width:v2`，避免沿用旧的 420px 偏好。
- 验证：服务器 `pnpm typecheck` 通过、Next 构建成功；源站 `/`、`/lab` 返回 200，无 Cookie 的 `/api/auth/session` 返回 401；切换前活动任务为 0。入口 `current` 指向 `vLDmwS-Nq68Pyl3uvqKZD`，公网 `/` 与源站 `server/app/index.html` SHA-256 一致（`bcba1781aabdbdf46df8ebaa73070691088ea421d029b4da2b4f1b548079cbff`），两个 CSS chunk 返回 200 且包含新的 `min-width:1100px` 桌面规则。
- 回归：`alpha_acceptance_test` 上 `pnpm test` 179/183；4 个失败（maintenance 2 项、tasks/worker 各 1 项）在既有 `20261115-stream-text-web` 目录以相同环境复跑同样失败，属于既有环境/用例问题，与本次纯前端改动无关。
- 未验证：未用生产账号在 1920px 浏览器里做登录态截图核对；布局依据构建产物、静态资源内容与本地真实 CSS 渲染验证。
- 回退：删除 `/etc/systemd/system/alpha-lab-web.service.d/zzzzz-layout-split.conf`，`systemctl daemon-reload && systemctl restart alpha-lab-web`；入口把 `/var/www/hermes-alpha-lab/current` 指回 `ApSM4D5y4fKE5-2IUeOmt`（旧静态资源保留）。

## Web 历史栏常驻与工作区重设计部署（2026-11-15）

- Web 发布目录 `/opt/hermes-alpha-lab/releases/20261115-history-rail`，BUILD_ID `tnM0K5p6wC4llPJcUc_bg`。基于 `20261115-layout-split` 复制，覆盖 `apps/web/app/split.css`、`apps/web/components/workspace.tsx`、`apps/web/components/canvas.tsx`；Worker/Tools/Feishu/数据库未变更，无迁移。
- 行为：
  - ≥1100px 时「最近研究」历史栏常驻左侧（280px），移除顶栏汉堡与「研究记录与持仓」折叠按钮；<1100px 保留抽屉。历史列表在栏内滚动并占满剩余高度。
  - 对话/画布改为 **50% / 50%**，并改为按**比例**存储与重算（`hermes:chat-ratio:v3`），窗口、侧栏或画布尺寸变化时保持比例，不再沿用首次挂载时的像素宽度。
  - 画布区域使用独立浅色表面；空状态改为带虚线边框的卡片，并补充「行情走势 / 技术指标 / 新闻与结论」三个能力标签。
  - 欢迎区内容限制在 680px，避免宽对话列下按钮和输入框过宽。
- 验证：服务器 `pnpm typecheck` 通过、Next 构建成功；源站 `/`、`/lab` 200，无 Cookie 的 `/api/auth/session` 401；切换前活动任务为 0。入口 `current` 指向 `tnM0K5p6wC4llPJcUc_bg`，公网 `/` 与源站 `server/app/index.html` SHA-256 一致（`82c3c66a495e2a35ab92741907d58ab57139d765fdb6b94bf1ab59b4e5a71ac2`），CSS chunk `d3ef556b3740d0f6` 返回 200 并包含 `min(var(--chat-width,50%))`、`min-width:1100px`、`flex:0 0 280px`。
- 未验证：未用生产账号在登录态截图核对；布局尺寸使用真实 CSS 在 1920/1440/1280/1100 视口验证（1920：侧栏 280、对话 820、画布 814）。
- 回退：删除 `/etc/systemd/system/alpha-lab-web.service.d/zzzzzz-history-rail.conf`，`systemctl daemon-reload && systemctl restart alpha-lab-web`；入口 `current` 指回 `vLDmwS-Nq68Pyl3uvqKZD`。

## 飞书风格工作区部署与轨迹修复（2026-11-15）

- Web 发布目录 `/opt/hermes-alpha-lab/releases/20261115-feishu-ui`，当前 BUILD_ID `3hO7ZrwnjPCj9wcCHGhru`（首次发布 `wq9VDCv7qQpgVE8RArmfl`）。覆盖令牌层、侧栏常驻、居中对话与画布分栏的 11 个前端文件；本轮修复另覆盖 `research-status.tsx`、`research-progress.ts`、`packages/server/src/tasks.ts`。Worker/Tools/Feishu 与 Hermes 镜像未变更；本轮新增 `034_thread_cleanup.sql`（线程软删除列，可空、无回填）。
- 轨迹顺序：`ResearchTrace` 从消息列表之前移到 `ThreadPrimitive.Messages` 之后，执行轨迹出现在用户问题与模型回答的下方；桌面宽度 100%，移动端不再重复扣减内边距。
- 思考保留：快照查询改为「最近 100 条工具/状态事件 + 最多 200 条思考事件」，客户端 `trimProgress` 同样优先保留 `reasoning.delta` / `reasoning.available` / `message.interim`，避免超过 100 条工具/状态事件的长任务丢弃早期思考。任务 `7115ad26…` 只有 21 条 `run.progress`，本次修复前也已全部返回；该问题的直接原因是下方镜像缓冲，不是前端截断。
- 等待态：任务已运行但尚未收到工具/推理事件时，轨迹标题显示「模型正在思考」并使用思考动画，不再显示容易误解为排队等待的「等待研究响应」。
- 首轮验证（BUILD_ID `gglpe8elJiyVhrOsPJdi3`）：本地与服务器 `pnpm typecheck` 通过，`tests/server/research-progress.test.ts` 11/11；服务器 Next 构建成功；源站 `/` 200、无 Cookie `/api/auth/session` 401；入口 `current` 指向 `gglpe8elJiyVhrOsPJdi3`，公网 `/` 与源站 `server/app/index.html` SHA-256 一致（`559d5f6b2f9e77a41b0b71f195e93286a40e76acb23c9a2b6238e9479d5a48a1`），轨迹 CSS chunk `f54f45b69f04165b` 返回 200 且包含 `width:100%`。
- 未修复（需重建 Hermes 镜像）：上游 `reasoning_callback` 虽按 token 触发，`apply_patch.py` 仅在累计 6000 字符、工具开始或回合结束时 `reasoning.delta`；截图链路首次思考事件在任务开始后 76 秒才出现。要更早看到思考内容，需将缓冲改为短周期冲刷并重新验证 9 个 Profile；本轮未改动生产推理镜像。
- 轨迹终态（BUILD_ID `_n1V_ls805DUWG-dNL6JY`）：任务结束后丢弃 `queued/running/stopping/unknown` 状态行，只保留终态行，避免已完成轨迹里残留绿色运行脉冲；新增 `finished traces drop stale queue and running rows` 回归测试。
- 思考卡片按语雀文档风格重做：外层轨迹改为白底描边卡片；「推理原文」改为「思考过程」，头部含灯泡图标、角色标签、折叠态一行预览与 chevron；正文白底、14px/1.75、段落分隔，并标注「模型原始推理，工具名已转为中文」；叙述块改为中性灰引用，不再使用蓝色左条。角色标签移入卡片头部，避免与分组标题重复。
- 验证：`research-progress` 12/12；服务器 `pnpm typecheck` 与 Next 构建通过；源站 `/` 200、无 Cookie `/api/auth/session` 401；入口 `current` 指向 `_n1V_ls805DUWG-dNL6JY`，公网 `/` 与源站 `server/app/index.html` SHA-256 一致（`b4fa47c7919de2f0f22eb7bf9360dd714c29bd06928a9d74698a268fdbb8acf0`），思考卡片 CSS chunk `059906a5634e6dbd` 返回 200 且包含 `.trace-body{max-height:360px}` 与 `.trace-thought-text` 的 14px 规则。
- 视觉核对：本地以生产 CSS 渲染 824px 预览，卡片 690×215、头部 36px、正文 14px/24.5px、无横向溢出；未用生产账号做登录态截图。
- 回退：发布目录与入口静态资源保留；将入口 `current` 指回 `wq9VDCv7qQpgVE8RArmfl` 可恢复上一版页面。

## 侧栏语雀化与会话清理部署（2026-11-15）

- 发布目录 `/opt/hermes-alpha-lab/releases/20261115-feishu-ui`，BUILD_ID `3hO7ZrwnjPCj9wcCHGhru`。前端覆盖 `workspace.tsx`、`workspace.css`、`client.ts`；服务端覆盖 `business.ts`、`http.ts`、`tasks.ts`、`apps/web/app/api/[...path]/route.ts`；新增迁移 `infra/migrations/034_thread_cleanup.sql`。Worker/Tools/Feishu/Hermes 镜像未变。
- 侧栏列表改为语雀文档列表：每条会话两行（标题 / 来源 · 日期），活跃项浅蓝底与品牌色标题；删除按钮 34px 悬停/聚焦/选中时出现，移动端 40px；「最近研究」分组增加分隔线与计数。
- 删除行为为软删除：`threads.deleted_at` 置位，列表与单条读取过滤；不删除 messages、tasks、task_events、workspaces、datasets、results、forecasts，研究事实与画布保留。仍有 `queued/running/stopping/unknown` 任务的会话返回 `thread_busy` (409)，前端提示等待或停止后再删。
- API：新增 `DELETE /threads/:id`（owner 校验、`FOR UPDATE`、CSRF Origin 校验沿用）；`research` 提交与背景线程查找拒绝已删除线程。前端确认弹窗含标题、保留范围说明、取消/删除、Escape 关闭、Tab 焦点循环，删除中禁用重复提交。
- 迁移：`pnpm db:migrate` 已在生产应用（`Migrations applied`），迁移前活动任务为 0；`deleted_at` 初始为空。
- 验证：`pnpm typecheck` 本地/服务器通过；`tests/server/http.test.ts` 在 `alpha_acceptance_test` **8/8** 通过，覆盖运行中 409、停止后 200、列表隐藏、单条 404、他人 404；Next 构建成功；源站 `/` 200、无 Cookie `/api/auth/session` 401；入口 `current` 指向 `3hO7ZrwnjPCj9wcCHGhru`，公网 `/` 与源站 `server/app/index.html` SHA-256 一致（`1502354868bd5050330ec2c6d794b3924e5651e64f4a25710eb4f16887b20475`），侧栏 CSS chunk `380e446e4e7e28fa` 返回 200 且包含 `.thread-row`/`.thread-open`。
- 回归：发布目录自带的旧测试副本跑出 180/184；4 个失败均为旧测试仍要求快照在事件清理后完全不变。同步当前 `maintenance.test.ts` / `worker.test.ts` / `tasks.test.ts` 后，对应套件 **20/20 + 16/16** 通过，确认失败来自测试漂移而非本次代码。
- 视觉核对：本地以生产 CSS 渲染 280px 侧栏，行高 57px、活跃底 `rgb(232,240,255)`、标题省略、删除按钮选中可见/空闲隐藏/悬停渐显；确认弹窗 400×204、危险按钮 `rgb(197,39,29)` 白字。未用生产账号做登录态截图。
- 回退：入口 `current` 指回 `_n1V_ls805DUWG-dNL6JY` 可恢复上一版前端；旧代码不读取 `deleted_at`，已软删除会话会重新出现在列表中。若要彻底回退，需删除 `034_thread_cleanup.sql` 对应列并清理 `migrations` 记录，属独立数据变更，未执行。

## 会话删除 503 修复：threads 列授权（2026-11-15）

- 现象：生产删除会话弹窗返回「服务暂时不可用，请稍后重试」，`alpha-lab-web` journal 记录 `BFF request failed error`。根因是 Web 数据库角色 `alpha_live_web` 仅有 `threads.title` 的列级 `UPDATE`，执行 `UPDATE threads SET deleted_at=now()` 时抛 `42501 permission denied`，被 BFF 兜底为 503 `service_unavailable`；`http.test.ts` 使用 owner 连接，未覆盖受限角色。
- 修复：`grantResearchAccess` 增加 `GRANT UPDATE(deleted_at) ON threads TO web`（初始建号与 `upgradeRuntimeAccess` 共用），已在生产执行 `scripts/upgrade-multiuser-access.ts alpha_live_web alpha_live_worker alpha_live_tools alpha_live_feishu`，输出 `multiuser_permissions_upgraded`；BFF 兜底日志改为打印 `error.name: message`。
- 验证：生产 owner 查询 `has_column_privilege('alpha_live_web','threads','deleted_at','UPDATE')` 为 `t`；以 `SET ROLE alpha_live_web` 在事务内对真实会话执行 `FOR UPDATE` + `UPDATE threads SET deleted_at=now()` 返回 `UPDATE 1` 后回滚，行仍为未删除。`tests/server/database-access.test.ts` 新增「web role can soft-delete its own threads」并以受限 web 角色跑通，**8/8** 通过；`pnpm typecheck` 通过。
- 发布：同一 release 重新构建，BUILD_ID `WKEuGizy0Ktw8wrrisl37`；入口 `current` 指向该目录，公网 `/`、源站 `server/app/index.html`、入口 `current/index.html` SHA-256 均为 `29bfdde74d4cafec71a7390ba3a6be77b927878976369a5c9a3cca4721000cad`；公网 `/` 200、无 Cookie `/api/auth/session` 401、CSS chunk `5db54892259ff52d` 200。
- 回退：数据库授权可用 `REVOKE UPDATE(deleted_at) ON threads FROM alpha_live_web` 撤销；前端/接口无行为变化，回退授权会使删除恢复 503。

## Hermes 模型切至 deepseek-v4.1-flash（2026-11-15）

- 迁移 `035_profile_policy_versions`：新增只追加的 `profile_preparation_policy_versions`，旧 `profile_preparation_policy` 保留为第 1 代；`AutoProfilePreparer` 读取最新代际，Worker 获得 `SELECT/INSERT`。生产迁移后第 1 代为 glm hash `fe827a7d…`。
- 轮换命令 `pnpm hermes:rotate-model CURRENT_SETTINGS NEW_SETTINGS PRIVATE_ROOT LAST_PORT BACKUP_ROOT [--owner UUID] [--apply]`：逐 owner 校验 runtime 指纹、备份并原地重写 `runtime.json`/`credentials.json`/`preparation.json`/seed，全部成功后追加策略代际并替换 settings；失败按备份恢复。新表禁止 UPDATE/DELETE/TRUNCATE，Web/Tools 无访问权。
- 生产执行：3 个 owner（170a9f10…、4b776186…、e241924e…）共 9 个 Profile 全部切到 `deepseek-v4.1-flash`，新代际 hash `1775b3d2…`。`private/profile-settings.json`、`private/provider/claude-code-model.json`、`private/provider/news-translation.json` 均只保留 deepseek；备份在 `private/rotation-backups/20261115T143937Z`。
- 过程问题与修复：首次轮换把 `preparation.json.image` 从部署镜像 `cd54b181…` 改成 settings 的 `d3da8c7b…`，导致 `loadDeployments` 以 `verified_profile_registry_required` 拒绝；代码修复为始终保留现有 manifest image（回归测试覆盖），三份生产 manifest 已从备份恢复 image，9 个 `profile_instances.deployment_hash` 重新绑定到轮换后的 runtime/compose。
- 运行态：旧 prediction `1d32a0ff…` 的 `unknown/supervisor_check_failed` 经 `hermes:reconcile confirm-stopped`（原容器 ID 匹配且实际停止）清除；`alpha-lab-preparer` 通过 drop-in 切到 `releases/20261115-feishu-ui` 并重启，日志无 `preparation_policy_changed`。
- 验证：`tests/server/model-rotation.test.ts` **13/13**（含镜像保留、旧代际 owner、busy、回滚、dry-run）；3 个 owner 的 `verify-prepared-profile` 均输出 `acceptance_verified`（真实 deepseek + 合成工具网关，9 个 Profile 全绿）；新闻翻译配置实测返回 `deepseek-v4-1-flash-261017` 与中文 JSON。`pnpm typecheck` 通过；`/` 200、无 Cookie `/api/auth/session` 401；supervisor 启动日志 `profiles: 9`。
- 回退：用备份的旧 `profile-settings.json` 作为 NEW、当前 deepseek 作为 CURRENT 再执行 `--apply`，会恢复 artifacts、写回旧 settings 并追加旧 hash 代际；再重启 preparer/supervisor。旧 glm 配置仅存在于 `rotation-backups/20261115T143937Z`，确认无需回退后可手动删除。

- 发布：重新 `pnpm build` 后 BUILD_ID `qE4HG3RC5TUEPFQLZc3bj`；入口 `current` 指向该目录，公网 `/`、源站 `server/app/index.html`、入口 `current/index.html` SHA-256 均为 `671af8b7efb69ed62514473eb8f2a4e4c8ecfadd1096c0ea8562bbe50d691392`；公网 `/` 200、无 Cookie `/api/auth/session` 401、CSS chunk `5db54892259ff52d` 200。回归：`model-rotation`/`auto-preparation`/`database-access`/`provision` 共 **35/35** 通过。

- 终审修复：`hermes:rotate-model --apply` 现在同时拒绝运行中的 preparer 和存在 `queued/preparing` 准备任务；单 owner 写入失败会先用备份自恢复再抛原错误（`rotation_restore_failed` 表示恢复失败）。`model-rotation` **14/14**；全量服务端 **199/200**，唯一失败为既有 `task-stream` 重连用例的偶发时序，单独重跑 7/7 通过。

## 知识沉淀页面部署（2026-11-15）

- Web 发布目录 `/opt/hermes-alpha-lab/releases/20261115-knowledge-base`，BUILD_ID `svQKDz7ZsXHj1bhy8d-y_`，基于 `20261115-feishu-ui` 复制后覆盖 10 个文件（`research-api.ts`、`workspace.tsx`、`layout.tsx`、新增 `knowledge.css`、`knowledge-base.tsx`、`knowledge/page.tsx`，以及 3 个测试/夹具文件）。Worker/Tools/Feishu/数据库/Hermes 镜像均未变更，无迁移。
- 接口：新增 `GET /api/knowledge`（owner 过滤），返回经验版本与 `kind='research_summary'` 的研究摘要（经 `research_contexts` 归属股票）。缺 `lineage.researchContextRef` 的历史摘要不返回，不猜测归属。
- 切换：新增 drop-in `zzzzzzzz-knowledge-base.conf`，`systemctl daemon-reload && systemctl restart alpha-lab-web`；MainPID 227535 → 291316。
- 验证：发布目录 `tsc --noEmit`（根 + `apps/web`）通过；`next build` 成功并产出 `/knowledge` 路由；源站 `/`、`/lab`、`/knowledge` 均 200，无 Cookie `/api/knowledge` 401；公网 `/` 200、`/knowledge` 200、`/api/knowledge` 401；CSS chunk `92afa933a3fe2223.css` 200 且包含全部 `.knowledge-*` 规则（`knowledge-layout`、`knowledge-doc` 等）。journal 无 error。
- 测试（部署前）：`research-integration.test.ts` **6/6**（含新 knowledge 断言与跨用户隔离）；Playwright `knowledge.spec.ts` 桌面 + 390px 通过；`/lab` 与 `/` 回归通过。
- 回退：删除 `/etc/systemd/system/alpha-lab-web.service.d/zzzzzzzz-knowledge-base.conf`，`systemctl daemon-reload && systemctl restart alpha-lab-web`，回到 `20261115-feishu-ui`（BUILD_ID `qE4HG3RC5TUEPFQLZc3bj`）。无数据变更，无需回滚数据库。

## 咨询任务与队列修复（2026-11-16）

- 问题：所有用户消息都变成 `research` 任务，「帮我看一下知识沉淀」会冻结证据并派发预测/组合子任务。根因是 `messages.task_id` 非空且提交入口只有 `POST /api/research`。
- 修复：`packages/server/src/research-intent.ts` 在入库前分类，非股票问题落 `role='consult'`；迁移 `036_consult_tasks.sql` 放宽 `tasks_role_check` 与 `tasks_check`（根任务允许 `research|consult`）。consult 只拿 7 个只读工具、不占 10 个交互研究槽位、不写研究产物。
- 部署：web `20261115-knowledge-base`（BUILD_ID `XKFDew1XQ2smZ24iEht5J`）、worker `20261115-stream-text-worker`、tools `20261113-research-repair` 三处同步；生产 `tsc` 通过，迁移 `036` 已应用。
- 测试：`tasks`/`consult`/`research-intent`/`research-progress` 共 **35/35**；生产实测「可以帮我看一下我当前的知识沉淀内容吗」落库为 `consult`。
- 队列停摆排查：`acceptance_capacity` 是全局单例闸门，被测试账号的 `unknown` 预留占死，叠加 9 条 `unknown`/`queued` 任务，导致新任务永不执行。已清除孤儿预留与不可变审计之外的残留任务，并把测试账号的 `research_coverage`（110 条）删除以停止后台调度。
- 待办：新注册账号的 profile 仍为 `unprovisioned`，preparer 报 `profile_acceptance_waiting_or_requires_reconciliation`（真实原因为 `acceptance_waiting_for_capacity`），需要继续排查验收链路；`owner@alpha.local` 有 1 条 11-13 起的 `deferred` 研究任务。

## 知识库按股票分类（2026-11-16）

- 目录结构：`个股研究` 下按 `symbol` 新增一层股票文件夹（`NVDA`、`AAPL` 等），每个文件夹显示摘要数并可独立折叠；文件夹顺序沿用文档的最近更新时间降序，标题仍为「`SYMBOL` 研究摘要」。`策略经验` 保持平铺，经验自身已有 scope/状态维度。
- 交互：股票文件夹与一级文件夹共用 `aria-expanded` 折叠语义；开始在搜索框输入时自动展开全部文件夹，避免命中的文档被折叠隐藏。嵌套文档行使用 `.kb-doc-sub` 缩进，计数 `font-variant-numeric: tabular-nums`。
- 变更：`apps/web/components/knowledge-base.tsx`（`toEntry` 提升为模块级、按 symbol 分桶）、`apps/web/app/knowledge.css`（`.kb-branch`、`.kb-folder-sub`、`.kb-doc-sub`）、`tests/support/knowledge-fixture.ts`（新增 AAPL 上下文与摘要，覆盖两个股票目录）、`tests/browser/knowledge.spec.ts`（子文件夹数量/计数/折叠/搜索自动展开断言，新增桌面截图 `knowledge-desktop.png`）。
- 验证：`pnpm typecheck`（根 + `apps/web`）通过；`playwright test tests/browser/knowledge.spec.ts` 通过（桌面 1280px 与 390px，`overflow-x` 为 0）；截图确认 `个股研究 > AAPL/NVDA > 摘要` 两级缩进与选中态。
- 环境：本地 3400 的旧 dev server 已死锁（TCP 可连、HTTP 240s 无响应、`.next` 停在 11/14），已重启；`alpha_test` 库按 README 执行 `pnpm db:migrate` 补齐 `execution_contexts` 等表后才可跑浏览器用例。
- 生产发布：Web 目录 `/opt/hermes-alpha-lab/releases/20261116-stock-folders`（由 `20261115-knowledge-base` 复制后覆盖 `knowledge-base.tsx`、`knowledge.css`、浏览器用例与夹具），BUILD_ID `EcUaP6Uqr1YiWGB38SpGv`；`tsc -p apps/web` 与 `next build` 通过。未同步 `20261115-knowledge-base` 缺失的 `packages/server/src/research-context-delta.ts`、`failure-text.ts`（`tool-gateway.ts`/`worker.ts` 已 import，属既有缺口且不在 Web 依赖图内），根 `pnpm typecheck` 仍报这两处，`.next` 构建与路由不受影响。
- 切换：新增 drop-in `/etc/systemd/system/alpha-lab-web.service.d/zzzzzzzzz-stock-folders.conf`，`daemon-reload && restart alpha-lab-web`，MainPID 2424016 → 3138703。
- 入口：边缘 `python3 /opt/hermes-alpha-entry/publish-edge.py` 发布 shell 与静态资源，`/var/www/hermes-alpha-lab/current` → `EcUaP6Uqr1YiWGB38SpGv`。
- 验证：源站 `/`、`/knowledge`、`/lab` 200，无 Cookie `/api/knowledge` 401，CSS chunk `d8851cf121ad4b58.css` 200 且包含 `.kb-doc-sub`、`.kb-folder-sub`；公网 `trading.youwei-agent.com` 的 `/`、`/knowledge`、该 CSS 均 200，`/api/knowledge`、`/api/auth/session` 401。登录态下的目录展开为本地浏览器用例证据，未用生产账号目视复核。
- 回退：删除 `zzzzzzzzz-stock-folders.conf` 后 `daemon-reload && systemctl restart alpha-lab-web` 回到 `20261115-knowledge-base`；入口把 `current` 指回 `I6TvDPw94IhNxEm2vLJAR`。无数据变更，不需要迁移回滚。

## 知识沉淀改为 Hermes agent 共享池（2026-11-16）

- 语义：知识沉淀属于同一个 Hermes agent，不再按账号过滤；任何已登录账号看到同一份研究摘要与经验版本。其余接口（预测、通知、经验试验、持仓、任务）继续按 `owner_id` 隔离，写入仍从任务 owner 进入。
- 变更：`packages/server/src/research-api.ts` 的 `knowledge()` 去掉 owner 条件；`packages/server/src/experience-store.ts` 的 `list(owner|null)` 支持共享列表；`docs/api-contract.md` 记录该接口是唯一跨账号可读的业务接口。
- 归属规则不变：摘要仍要求 `lineage.researchContextRef` 指向同所有者 `research_contexts`；生产 35 条摘要中 29 条可归属，6 条无上下文的保持不可见。
- 测试：`tests/server/research-integration.test.ts` 跨账号断言改为账号 B 可见账号 A 的摘要；浏览器夹具改为每次生成唯一 tag（符号 `N/A + tag`、经验标题带 tag），`knowledge.spec.ts` 按 tag 收敛断言，避免共享池导致计数漂移。
- 生产发布：release `20261116-shared-knowledge`，BUILD_ID `Xuq5MawMzDvPHW_6fNR8-`，drop-in `zzzzzzzzzz-shared-knowledge.conf`，MainPID 3138703 → 3208969；边缘 `current` 同 BUILD_ID。
- 验证：根与 `apps/web` `tsc` 通过，`knowledge.spec.ts` 通过；生产登录 `owner@alpha.local` 后 `/api/knowledge` 返回 **29** 条（自有 25 条可归属 + 另一账号 4 条），页面 22 个股票目录、`/knowledge` 200，公网 `/` 200、`/api/auth/session` 401，journal 无 error。
- 回退：删除 drop-in 后 `daemon-reload && systemctl restart alpha-lab-web` 回到 `20261116-stock-folders`；边缘 `current` 指回 `EcUaP6Uqr1YiWGB38SpGv`。

## 知识沉淀页视觉重构（2026-11-16）

- 参考语雀文档工作台改为三栏：目录 260px（`#fafbfc`）· 正文（白色、上限 720px）· 大纲 244px；56px 文档栏 + 2px 细分割线，页面级绿色强调 `--kb-accent #00875a` 仅用于操作、选中图标与大纲指示。
- 阅读排版：正文 15px / 行高 1.85，文档标题 30px/1.32，`一、` 小节标题 17px，段落与列表 1em 节奏；目录行 13px、元信息 11px，日期收短为 `MM/DD`。
- 交互：大纲可点击跳转（`href="#kb-b-n"`，`prefers-reduced-motion` 时取消平滑滚动），滚动时按章节位置高亮（`aria-current="location"`）；切换文档回到正文顶部；目录自动滚动到当前选中项；≤900px 目录改为「目录」抽屉并在选中后收起；新增「跳到正文」跳转链接。
- 可读性：`--kb-ink-3` 调至 `#646a73`（白底 5.45:1、侧栏 5.26:1）；大纲选中态用 `--kb-accent-hover`（软底 5.51:1）；计数使用 tabular-nums；超长大纲标题 2 行截断并附 `title` 全文。
- 断点：≥1360px 三栏；<1360px 隐藏大纲（1280px 正文 676px）；≤900px 目录抽屉化、正文单列。
- 测试：`knowledge.spec.ts` 桌面视口改为 1440×1000，新增大纲 `aria-current`/跳转与移动抽屉开关断言；移动截图前隐藏离屏固定侧栏，规避 Chromium 合成残影（仅测试证据处理，不改产品样式）。
- 生产：release `20261116-knowledge-yuque`，BUILD_ID `_TV4MKzR41pONyGxrUwxP`，drop-in `zzzzzzzzzzz-knowledge-yuque.conf`，MainPID 3208969 → 3277253；边缘 `current` 同 BUILD_ID。生产实测 1440px 下正文 592px、大纲 244px、6 个章节标题；1280px 隐藏大纲、正文 676px；`/`、`/knowledge` 200，`/api/auth/session` 401，journal 无 error。
- 回退：删除 drop-in 后 `daemon-reload && systemctl restart alpha-lab-web` 回到 `20261116-shared-knowledge`；边缘 `current` 指回 `Xuq5MawMzDvPHW_6fNR8-`。

## 全局品牌色与外壳对齐语雀风格（2026-11-16）

- 单一强调色：`--brand` 由飞书蓝改为知识绿 `#007d55`（`--bg` 4.8:1、白底 5.2:1、填充按钮白字 5.2:1），`--brand-text #006b48`（白底 6.6:1、软底 5.9:1），`--brand-soft #e7f6ef`；登录页示意图表与个股价格线改用 class + token，不再硬编码 `#3370ff`。
- 侧栏：行高 32 / 圆角 6 / 间距 2，选中态改为浅灰底 + 墨色字 + 绿色图标（与知识库文档行一致）；`新建研究` 34px 圆角 6；logo 24px。
- 顶栏：`研究产物` 28px 圆角 6、计数 16px；账号摘要 28px、头像 24px、邮箱 12px；模式 chip 11px 直角。
- 知识页 `--kb-accent*` 改为引用全局 brand token，页面内不再重复定义绿色。
- 验证：`apps/web` tsc 通过；`knowledge.spec.ts` 通过；登录视觉用例在 HEAD 上已因标题文案变更失效（断言「研究，洞察，进化」在 `login-landing.tsx` 中不存在），与本次配色无关。
- 生产：release `20261116-shell-green`，BUILD_ID `FT4SJaQDnaEzkpCUKXVgi`，drop-in `zzzzzzzzzzzz-shell-green.conf`，MainPID 3277253 → 3308149；边缘 `current` 同 BUILD_ID；`--brand` 实测 `#007d55`，`/`、`/knowledge` 200，`/api/auth/session` 401。
- 回退：删除 drop-in 并重启回到 `20261116-knowledge-yuque`（BUILD_ID `_TV4MKzR41pONyGxrUwxP`）；边缘 `current` 指回该 BUILD_ID。
