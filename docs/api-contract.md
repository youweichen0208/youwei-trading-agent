# API 与任务契约 v1.2

状态：按 2026-11-12 的仓库实现更新；持续研究接口已有本地验证，新增来源及新版生产运行时仍需独立验收。架构见 [architecture](./architecture.md)，事件见 [Canvas 协议](./canvas-protocol.md)。本页 `/api/*` 和协作工具均为项目协议，不是 Hermes 原生接口。

## 1. 认证、资源与入口

用户身份来自 BFF 登录会话；所有 thread、task、workspace、dataset、result、portfolio 查询和修改均验证归属。客户端不得提交有效的 ownerId、上游地址、Hermes 凭证或 session ID。跨用户资源返回 404。金额、数量、价格为十进制字符串；日期为交易所交易日，时间戳为 UTC ISO 8601。

| 方法 | 地址 | 行为 |
|---|---|---|
| POST | /api/threads | 创建用户对话及 workspace |
| GET | /api/threads/{id} | 返回消息、研究上下文、活动 taskId、workspaceId |
| POST | /api/research | 提交普通问答或研究，返回 202 与 taskId |
| GET | /api/tasks/{id} | 状态、子任务、结果引用、错误类别、lastSequence |
| GET | /api/tasks/{id}/events | 认证 SSE，按应用事件序号恢复 |
| GET | /api/tasks/{id}/audit?after=0 | 认证审计查询；根任务包含专项历史，每页最多 200 条 |
| POST | /api/tasks/{id}/stop | 幂等取消意图，返回当前状态 |
| GET | /api/datasets/{id} | 有权限的不可变数据及元信息 |
| GET | /api/results/{id} | 研究/计算结果及血缘 |
| GET/PUT | /api/workspaces/{id} | 读取快照；以 expectedVersion 保存布局 |
| POST | /api/portfolios | 保存新的模拟组合及首个快照 |
| POST | /api/portfolios/{id}/snapshots | 创建不可变持仓版本 |
| GET | /api/portfolios/{id} | 返回授权的指定版本或最新版本 |
| GET/POST | /api/watchlist | 用户自选股 |

首版不提供独立 `/api/chat` 和公开 `/api/runs/*`。应用任务可以覆盖多个 Hermes run，二者不可互换。

提交示例，HTTP 头必须带 `Idempotency-Key`：

```json
{
  "threadId": "thread_01",
  "input": "分析 NVDA，给出下一交易日基线预测，并分析我的模拟组合",
  "portfolioSnapshotRef": "holdings_01"
}
```

```json
{"taskId":"task_01","threadId":"thread_01","status":"queued"}
```

thread 关联 workspace；用户输入和所引用的持仓版本在受理时固定。同一 thread 的回合串行执行，前一回合 unknown 时后续回合保持排队。缺少必需持仓或问题存在歧义时，research 回答澄清问题，当前回合结束，下一条用户消息形成新任务。

## 2. 持久化与幂等

逻辑实体：users、profile_instances、threads、messages、tasks、executions、task_events、datasets、results、portfolio_snapshots、workspaces。所有私有实体保留 owner；task 具有 parentTaskId、role、deadline、cancelRequestedAt；execution 保存上游 profile/session/run、原始请求、幂等键、generation 和租约。

- 提交事务同时保存用户消息、任务、请求键和排队记录。唯一约束 `(userId, requestKey)`；请求规范化后摘要相同返回原任务，不同返回 409 idempotency_conflict。身份记录保留至任务删除，首版不提供删除任务接口。
- Worker 领取有 generation 的租约；上游请求及幂等键在网络调用前提交。上游键从稳定 execution 身份生成，重试不更改 payload、凭证或目标 Profile。用户主动重新研究使用新请求键。
- 创建响应丢失先以原身份核对；锁定版本支持且仍在幂等保留期内时才允许原键精确重试。超过上游保留期或无法证明是否执行时进入 unknown，不发新 run。
- 工具 operation key 由可信执行器绑定 task 与操作身份，数据库唯一约束去重；同键参数变化拒绝。相同角色子任务请求同样去重。
- 外部执行与应用数据库不能组成全局事务，不承诺全局 exactly-once。租约过期本身不是重新执行许可；新 Worker 先恢复订阅/轮询。旧 generation 的写入被拒绝。
- 最终消息、结果关联、任务终态和终态事件在一个事务中提交。数据库不可用时不向前端发布未经持久化的完成事件。

## 3. 状态、取消与预算

| 状态 | 含义与允许的下一步 |
|---|---|
| queued | 尚未开始；可进入 running 或 cancelled |
| running | 正在执行；可进入 stopping、completed、failed、interrupted、unknown |
| stopping | 已发停止意图，工作尚未确认退出；可进入 cancelled、interrupted 或 unknown |
| unknown | 执行结果不确定，保留原身份；核对后进入实际状态，禁止盲目重发 |
| completed | 全部必需工作完成且最终结果已归档，终态 |
| failed | 已确认退出且不能满足请求，终态；允许保留部分结果 |
| cancelled | 取消意图已生效且所有执行确认退出，终态 |
| interrupted | 已确认运行中断且结果不完整，终态 |

停止操作原子设置 cancelRequestedAt 并阻止新子任务。queued 可直接取消；已运行的父子 Agent 与 Quant 作业都发送停止请求并核对退出。停止请求超时保留 stopping/unknown。若完成事务先提交，stop 返回 completed；若取消意图先提交，迟到结果仅归档为部分结果，不能覆盖取消。

必需子任务失败后停止其余工作，全部退出后父任务 failed；不因收到某个 Agent 的最终文本就认定 completed。上游 cancelled/interrupted 显式映射；未识别状态标为 unknown 并报警。审批能力首版不开放，遇到上游等待审批则停止并报告 unsupported_interaction，不自动批准。

工具超过观测期限进入 unknown 后，只有原执行器的实际返回才能保存退出回执并核对为终态；停止信号、超时或后续相同键查询本身不是退出证据。Quant 执行器等待子进程 close。退出回执、事实、工具终态与审计在同一事务中提交，取消后到达的有效事实标记 partial。原执行器丢失且没有持久化回执时仍保留 unknown，不自动重跑或释放 Profile。

迁移 009 起，任务创建、任务状态/取消/失败意图变化，以及工具状态变化追加到 execution_audit；工具退出另有不可变 tool_exit_receipts。旧任务不会补造历史记录。审计不受 SSE 清理影响，数据库拒绝 UPDATE、DELETE 和 TRUNCATE；这不替代部署时最小权限数据库角色。审计接口返回 `{items,nextCursor}`，序号为十进制字符串，按 `after` 独占游标升序读取。返回状态、原因代码、task/operation/receipt 标识、generation 与时间，不返回提示词、请求体、凭证或供应商原始错误。子任务查询只返回自身，所有查询验证 owner；审计不提供人工改写状态接口。

每 Profile 同时一个活动执行。当前 Worker 入口默认 `RESEARCH_SINGLE_ROOT=1`，全站同一时刻只接纳一项根研究；该根的专项任务仍可推进。每用户最多 10 个未结束交互根任务（queued/running/stopping/unknown），专项和后台研究不占用该提交配额；在用户锁内检查上限，超出返回 429 queue_full，不增加消息或画布修订。同键同内容重放先返回原任务，同键不同内容仍返回幂等冲突，不受队列上限掩盖。取消排队任务可释放一个名额，unknown 不能被丢弃以释放名额。交互根任务默认 10 分钟，子任务继承截止时间，最多 prediction/portfolio 各一个子任务；后台阶段使用独立预算，见第 7 节。Market/Quant 工具等待到上限后返回 pending，由状态查询继续；队列与资源限额不足返回 429。超时触发停止并保留 deadline_exceeded 原因。

能力令牌在任务与所有工具/专项执行确认结束后，与任务终态同事务删除摘要和加密副本；终态任务的令牌不能通过网关认证。running/stopping/unknown 的原身份和令牌保持不变，以便核对原执行；不能因时间经过而轮换令牌并重发。

## 4. 协作与数据工具

`submit_specialist(role, requestKey, question, datasetRefs, portfolioSnapshotRef?, researchContextRef?)` 仅供 research 调用，role 只允许 prediction/portfolio。前瞻预测传递冻结的 `researchContextRef`，组合任务仍需持仓版本。返回 childTaskId；`get_specialist_result(childTaskId)` 返回状态与授权结果引用。用户、父任务和执行身份从网关可信上下文取得，不能通过模型参数覆盖。

Worker 在用户 × thread × role 范围维护 session 映射。子任务只获得最小上下文和同用户引用。专项结果写入结果库，research 汇总可见结论；父任务仍有活动子任务时不得完成。Worker 调度不得因等待父任务而占尽专项任务执行槽。

Market 工具输入股票/区间/价格口径，输出持久化 datasetRef；Quant 工具只接受 datasetRef、计算类型及参数白名单。数据集包含 symbol、interval、exchangeTimezone、sessionRange、asOf、source、adjustment、version、contentHash；结果包含输入引用、参数、代码版本、状态及缺失原因。禁止任意 URL/本地路径充当引用。

持仓输入包含 asOfSession、positions（symbol/quantity）和 cashUsd；禁止负数与非有限值，同一 symbol 不重复。持仓写接口只能由用户会话调用，Agent 工具无写权限。

## 5. 无连接恢复与适配门槛

Worker 常驻消费 PostgreSQL 队列并收集上游事件，不依赖浏览器 SSE。任务和结果持续保留；增量事件首版保留 7 天，过期客户端使用持久化快照恢复。Worker 重启扫描非终态记录，先核对 Hermes 与工具状态，不重新提交不确定工作。

Worker 启动及每分钟尝试一次有界清理：删除超过 7 天的 task_events、已过期登录会话和已过期认证限流桶，并清除旧版本遗留的终态能力令牌，每类每次最多 1000 条。积压分多轮处理，不能假定满 7 天立即删除。数据库 advisory lock 避免同一 schema 多 Worker 同时清理，SKIP LOCKED 跳过正在处理的记录；清理失败不阻止任务调度。任务、消息、原执行身份与回执、数据/结果、工作区、审计持续保留。

应用保存渲染所需最终文本和 Canvas 产物，不能只保存 Hermes run_id。仅上游事件丢失但最终状态/结果可核对时可以补偿；无法取得最终文本或确认完整性则显示 interrupted/unknown，保留已有结果并允许用户明确发起新任务。

发布前记录 Hermes 版本、镜像摘要、API 认证、session 续接、幂等期限、事件类型、停止/未知恢复及工具身份注入的集成证据。实际进程与受控服务已有联调证据，真实部署报告仍待完成。原生能力与应用契约由 Adapter 显式映射，参考 [官方 API](https://hermes-agent.nousresearch.com/docs/user-guide/features/api-server)，不能用最新网页替代锁定版本测试。


## 6. 上游事件归档（迁移 012）

Worker 每步对原 run 做有界 SSE 读取，独立保存 executions.event_cursor；不会把它用作浏览器 Last-Event-ID。每次最多 256 条事件、1 MiB、1.5 秒，帧上限 64 KiB；未完成的帧不推进游标，下次从已提交游标继续。只映射主任务 message.delta 为 assistant.delta，以及工具开始/完成为 run.progress；工具预览、参数、推理、供应商错误与原始终态负载不归档。专项文本不加入主回答。

游标、主任务 stream_text（最多 500,000 字符）与应用事件同事务保存，受 Worker generation/租约和上游 run 身份约束；重复上游序号不会重复追加。state.snapshot 含 task.stream_text 与稳定的 task.stream_message_id，终态消息沿用该 ID，但内容以可核对的最终回答为准。清理 7 天增量事件不清理文本快照。

原生重放截断、序号缺口或终态事件不可取时记录 event_gap 和 event_history_gap 进度；出现缺口后不拼接不连续的主回答片段。不能用 SSE 的 run.completed 直接结束应用任务。Worker 先保存 GET 状态取得的终态回执，再分批归档余下事件；即使之后上游状态过期，仍可使用回执等待所有工具和专项退出并归档最终回答。

原任务临时 unknown 时，新工具写入的接纳事务可等待最多 10 秒核对；只重试明确在接纳前回滚的事务，不执行供应商/计算、不持有数据库锁等待，也不重发任务。取消、失败、期限过期立即拒绝；已运行工具的退出回执不受此等待限制。


## 7. 持续研究工作台与工具

`/lab` 使用已有登录会话读取个人研究数据。新增接口不接受客户端 owner 或 Profile 身份，群研究不能调用个人持续研究工具。后台任务不占用交互提交的十个根任务配额；调度器另按股票和交易日管理研究预算。

| 方法 | 地址 | 返回或修改 |
|---|---|---|
| GET | `/api/research-hub` | `coverage`、`forecasts`、`lessons`、`notifications`、`settings`、`capabilities`、`jobs`、`summary`、最新 `scan` |
| GET / PUT | `/api/coverage` | 研究池；写入 `{symbol,action}`，action 为 `watch` / `pause` / `resume` / `remove` |
| GET / PUT | `/api/agent-settings` | 持续研究、预算、摘要时段及飞书通知设置，PUT 合并传入字段 |
| GET | `/api/stocks/{symbol}` | 公司研究摘要、证据、预测、经验、采集状态及历史数量 |
| GET | `/api/forecasts/{id}` | 预测、评估、复盘、修订关联和原始证据引用 |
| GET | `/api/experiences` | 经验版本、当前状态和配对试验统计 |
| GET | `/api/notifications` | 最近持久通知 |
| POST | `/api/notifications/{id}/read` | 请求体 `{}`，幂等标记本人通知已读 |

`/api/coverage` 保留 `tier` 并提供展示用的 `kind` 与 `status=active|paused`。加入自选不会把持仓降级；移除持仓标记为暂停跟踪，不改写持仓数量。临时/候选观察的恢复重新计算 20 个交易日期限。`/api/watchlist` 的既有添加操作仍可用，研究池同步读取其结果。

设置默认值如下；`dailyResearchLimit` 范围 1–100、`dailyCandidateLimit` 范围 0–30 且不大于前者，`digestHour` 范围 0–23，解释为 Asia/Shanghai 小时。关闭 `enabled` 不删除已有预测或停止确定性到期结算。

```json
{
  "enabled": true,
  "dailyResearchLimit": 30,
  "dailyCandidateLimit": 10,
  "digestHour": 8,
  "feishuNotifications": true
}
```

主预测展示状态为 `open`、`pending_data`、`evaluated`；与 task 的执行状态独立。研究工作状态为 `deferred`、`pending`、`running`、`completed`、`failed`、`blocked`。超预算的 `deferred` 保留 checkpoint 后续续跑；不会伪报完成或自动替代 unknown 的原任务。汇总按 5/20 个交易日分别报告样本数、Brier、MAE、方向命中、区间覆盖/评分及超额收益。

### 证据与冻结上下文

`collect_evidence` 仅供 research：`{symbol,sources?,start?,end?,urls?}`。来源名为 `market_news`、`sec`、`web`、`x`、`macro`；最多五类来源、八个显式 URL，日期为 ISO 日期。操作身份仍由可信网关注入，不在模型参数中传入。

返回 `collectionRef`、`status`、`evidenceRefs`、内容变化用的 `fingerprint`、逐来源状态和 `failures`。整轮状态为 `completed`、`partial`、`unavailable`，同键采集尚未结束时为 `running`；来源状态区分 `available`、有效查询无结果的 `empty`、`partial` 与 `unavailable`。缺凭证、没有访问权限、分页截断和网络超时均会报告。网络阶段最多 22 秒，共享取消信号覆盖响应体读取，不遗留后台抓取。

`read_research_context({symbol,contextRef?,query?})` 返回原冻结快照，或创建新的 `contextRef`。先获取并保存行情和证据，再创建用于发布的快照；其 `evidenceRefs`、`datasetRefs` 固定当时可用版本。快照含公开/获取时间、公司/SIC 同业关系、用户持仓、旧预测和 active 经验。`trialCandidates` 仅含候选版本与适用条件，不包含规则正文；证据顶层 `eventKey` 为来源及源标识的稳定哈希。没有可信公开时间的证据不能用于声明“经验形成后出现的新事件”。

### 发布、结算与复盘

`publish_forecast` 仅供 prediction，输入如下。收益与概率均为十进制字符串，区间是 80% 收益区间；服务器校验区间与概率，自动固定发布时间和未来开收盘日期，调用者不能回填历史发布时间。

```text
symbol, horizon: 5 | 20,
meanReturn, lowerReturn, upperReturn, upProbability,
researchContextRef, evidenceRefs[], datasetRefs[], lessonVersionRefs[],
eventKey, thesis, invalidators[], supersedesForecastId?
```

所有引用必须属于该用户并存在于对应冻结上下文；引用行情需为同股票拆股调整版本，创建时间和行情 asOf 不得晚于快照。返回 `forecastId` 和 `resultRef`，Canvas 使用新增 `forecast_result`。`read_forecasts({symbol? ,forecastId?})` 至少提供一个定位字段，返回主预测和到期结果，不返回影子试验臂。

发布后首个尚未开盘的交易日为起点，第 5/20 个交易日含起点日；以起点开盘到目标收盘计算收益，目标收盘后再等 20 分钟结算。缺股票或 SPY 价格记录 `pending_reason`，待补数后结算。改判必须引用 `supersedesForecastId`，不覆盖旧判断；尚未到期的相同事件不允许伪装成新原始预测。旧期限结束后可以开启新周期。汇总主成绩排除修订记录，详情仍保留修订和原始记录。

`record_review` 仅供 research，输入 `{forecastId,text,attribution?,lesson?}`，只接受已结算预测。归因值为 `supported` / `hypothesis` / `unresolved`，默认 hypothesis；可选 lesson 包含标题、规则正文、company/industry/general 范围、股票、5/20 日期限、事件类型、适用条件和失效条件。返回复盘及可选经验版本引用，新经验默认 shadow。

### 经验试验与交付

双臂任务由服务器绑定独立 thread/session；prediction 在试验任务中仅使用 `get_trial_context({})` 和 `publish_forecast_trial_arm({trialId,arm,distribution})`。身份决定可访问的试验臂，不能通过参数切换臂或读取另一臂结果。分布字段沿用主预测，必须在冻结日程的开盘前提交；它不会创建用户主预测。

服务器对同一新事件的两臂结果配对，按模型/运行时/方法/已采用经验版本分组，用按时间顺序取得的完整新批次评估。至少 30 个不同的“股票 × 事件”样本，行业/通用经验还需覆盖 5 家公司；晋升条件为 Brier 改善至少 0.01，MAE 与区间评分劣化均不超过 5%。后续新批次失败退回 shadow。经验、试验臂、批次结果均保留版本，不通过修改记忆文件绕过评估。

通知先写入数据库。飞书绑定与通知设置允许投递时，再进入 outbox，固定消息 UUID 支持重试；发送结果不确定时保留 uncertain，不假设送达。前端关闭不影响后续结算和通知生成。

本节依赖迁移 029–031、更新后的运行角色授权、`pnpm hermes:catalog` 生成的工具目录及常驻 Worker；启动步骤和来源配置见 README。fixture 验证不代表真实 SEC/Brave/X/FRED 账户或新版生产 Hermes 已验收。

## 运维核对已知 unknown run

`pnpm hermes:reconcile interrupt-run` 仅在受信任宿主机显式调用，不增加浏览器审批/控制接口，也不是 Worker 自动重试。它要求原执行/部署/容器绑定、可查询的原生 unknown admission/generation，且该 Profile 的未退出工具、无响应副作用调用和未结束子任务均已核清。先关闭应用提交入口，确认原容器停止，再启动原容器并核对同一 admission，最后通过原生 resolve-unknown 结束未知回合。admission ID 按原始字符串保存，不作 UUID 格式转换。

每个外部动作前持久化阶段，阶段变化与不可变 recovery.* 审计同事务；任务审计返回 recovery_id 供核对。超时或响应丢失保留阶段与入口暂停，重复命令先重读实际状态，不创建新 run。命令不写应用任务结果或工具退出回执；Worker 仍从上游实际终态和工具账本完成归档。未验证的外部工具执行、缺少原 run 身份或不可达的运行环境不会被该命令强制结算。
