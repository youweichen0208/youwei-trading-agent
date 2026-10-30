# Youwei Trading Agent

美股研究、预测冻结与到期验证系统。当前正在实现 NVDA、日线、单研究 Profile、5 交易日 baseline 的 MVP。完整设计见 [架构任务书](docs/Youwei_Trading_Agent_v4.0_Hermes_Headless_Architecture_and_Task_Plan.md)，实际进度见 [实施状态](docs/implementation-status.md)。

目前可运行登录、研究会话、工具事件流、图表、确认冻结、预测历史及模拟结算的浏览器闭环。BFF 通过私有 Alpha API 维护预测事实。默认本地演练使用合成 REPLAY 数据和固定流程；显式配置 Hermes 的本机真实模型证据见实施状态，行情仍为 REPLAY，不代表正式前瞻成绩。

本机开发/测试可通过标准 BFF 启动入口选择 `YTA_RESEARCH_DRIVER=HERMES`，并在私有环境中同时设置 `YTA_HERMES_ORIGIN`、`YTA_HERMES_TOKEN`、`YTA_HERMES_MODEL`。地址必须是不含路径、查询或凭证的 HTTP(S) origin，Profile 固定为 `alpha-orchestrator`；token 是独立 Gateway 凭证，不能复用 Alpha token。模型名取 pi 配置选中的模型，Provider 密钥仅交给私有 Hermes，不能写入 BFF/Web 配置。

该配置不会自动部署 Hermes 或行情服务。须先准备已锁版本的私网 Gateway、启用 scoped receipts 的金融工具服务及 Alpha，并使用独立已迁移测试库；切换前核对待处理研究队列，避免消费旧测试任务。缺少字段、遗留 Hermes 字段但未显式选择驱动、凭证复用均拒绝启动。默认 `AUTO` 保留 REPLAY 固定流程和 LIVE 不可用行为；当前 staging/production 的 Hermes 配置会明确拒绝启动，须完成能力与供应商门槛后再开放。取消、恢复和未知执行仍使用原持久化请求身份，不自动重发。

## 本地启动

需要 Node.js 22、pnpm 9.15.4、uv、Python 3.12 和 Docker Compose。所有命令在仓库根目录运行。

```sh
pnpm install --frozen-lockfile
uv sync --locked
docker compose -f infra/compose/compose.dev.yaml up -d --wait
uv run python scripts/bootstrap_local.py
pnpm --filter @yta/bff bootstrap:login
pnpm build
```

初始化脚本只连接本机 `54329`，创建 `yta` 和隔离的 `yta_test` 数据库，执行带校验和的迁移，建立仅 SELECT/INSERT 的应用角色。首次运行还生成被 Git 忽略、权限为 0600 的 `.env` 和随机服务 token；已有 `.env` 不覆盖。Compose 内的密码仅用于这套本地容器。

登录脚本创建 `youwei@local.yta`，随机密码保存在仅本机可读的 `.local/login.txt`，终端不打印密码；数据库只保存 scrypt 哈希。重复初始化保留已有账户。

在三个终端分别启动（均在仓库根目录）：

```sh
uv run uvicorn yta_alpha.api:create_app --factory --host 127.0.0.1 --port 8100
pnpm --filter @yta/bff start
pnpm --filter @yta/web start
```

打开 `http://localhost:3000`。使用 localhost 访问网页，以匹配默认 `YTA_WEB_ORIGIN` 和 CSRF 校验。BFF 监听 3100，Alpha 监听 8100，均绑定本机；浏览器只请求 Web 的 `/api/yta/v1` 转发入口。开发时可把两个 `start` 换成 `dev`。

登录 → 发送 NVDA 研究问题 → 检查草稿 → 明确确认冻结 → 查看预测记录 → 模拟到期并结算。页面持续标明 REPLAY。研究与冻结响应丢失后，页面重试保留原幂等键；事件重连只补读，不重新执行研究。执行未知的会话禁止直接重发。

执行文档中的 CLI 合成闭环：

```sh
uv run python -m yta_alpha.replay
```

该命令通过受限应用角色创建 REPLAY 草稿、确认冻结并模拟到期结算，输出预测 ID、0.5% 预测值、2% 合成实际价格变化和 1.5 个百分点误差。它只允许 development / REPLAY，使用隔离的逻辑时钟，不能运行在正式研究模式。

启动私有计算服务：

```sh
uv run uvicorn yta_alpha.api:create_app --factory --host 127.0.0.1 --port 8100
```

`/health` 是存活探针；内部计算端点需要 `Authorization: Bearer <YTA_ALPHA_TOKEN>`。生产使用真实服务器时钟；development/test 的 REPLAY 路径采用固定合成截点，模拟结算仅在该模式开放。所有路径拒绝客户端 `now` 覆盖，用户归属由 BFF 从登录会话透传。OpenAPI 保存在 `packages/contracts/alpha.openapi.json`。

## 研究请求配额

`YTA_RESEARCH_RUNS_PER_24H` 设置每个已认证用户、每个数据环境在滚动 24 小时内可新建的研究 Run 数量，默认 50，允许 1–10000。所有 BFF 实例必须使用相同值。配额使用 PostgreSQL 时钟和事务锁，在创建 Run、消息及派发之前检查；更换会话、重启或增加实例不会重置。超过上限返回 `429/RESEARCH_QUOTA_EXCEEDED`，界面提示等待窗口释放，已有研究仍可读取。

同键同正文重试返回原 Run，不重复计数；同键异参仍报冲突。已接受但失败、取消、尚未执行或执行未知的 Run 都计入，不能靠取消或异常反复领取。历史记录保留，超过 24 小时后自然退出计数窗口；REPLAY 不占用 LIVE_RESEARCH 配额。数据库不可用时不会退回内存计数。此限制是研究请求次数上限，不是模型 token、辅助调用或货币费用预算，后者仍需正式接入验收。

## 登录限流

登录密码校验前，BFF 使用 PostgreSQL 共享的 60 秒滑动窗口：同一直连对端最多 10 次，成功及密码错误均计数，超过返回 429/RATE_LIMITED。额度通过事务锁串行领取，重启或增加 BFF 实例不会重置；时间取数据库时钟，数据库故障时拒绝登录，不回退到内存限流。

同一规范化邮箱另外共享每 60 秒 10 次额度，覆盖来自不同对端的请求；账号存在与否都按相同规则检查，大小写按登录契约归一。来源与账号两份额度在同一事务中领取，任一耗尽都不保留另一份占用；拒绝请求不会追加计数或延长窗口。新表 `login_account_attempts` 只保存分域哈希和时间，不保存邮箱明文，仍使用 SELECT/INSERT 运行权限与不可变触发器。升级 BFF 前须由迁移 owner 应用 `011_login_account_attempts.sql` 并赋予 runtime 对新表的 SELECT/INSERT、identity 序列的 USAGE/SELECT 权限；缺少迁移时不会绕过限流继续验证密码。

BFF readiness 同时检查账号限流表及其不可变触发器，以及两类登录尝试、登录凭证和研究消息共四条 identity 序列的 USAGE/SELECT。缺少迁移返回 `schema_missing`，缺权限返回 `permissions_invalid`，缺少启用的不可变触发器返回 `immutable_guards_missing`，不会把数据库 owner 当成合格运行角色。执行 `pnpm --filter @yta/bff build` 后，可运行 `uv run python scripts/check_bff_readiness.py`：脚本仅连接本机开发 PostgreSQL，在随机 `_test` 新库里验证升级和权限反例并清理该库，不变更现有应用/测试数据库。

每个 BFF 进程同时最多提交 4 个 scrypt 运算，包含所有 Auth 实例的真实账户、不存在账户校验及凭证生成；没有额外等待队列。容量满时返回 503/AUTH_BUSY（可重试），不创建登录会话；已领取的对端额度仍计数。运算完成或失败后释放容量，保留原有 scrypt 参数与哈希格式。此限制保护单进程计算资源，不替代跨实例请求预算或按账号限流。

`008_login_attempts.sql` 保存不可变的对端地址 SHA-256 和尝试时间，不保存原始 IP、邮箱、密码或 Cookie；哈希仅减少直接暴露，并不等于匿名化。应用账号保持 SELECT/INSERT，部署前须应用迁移并授予新表及序列权限，BFF readiness 会检查这些权限及不可变触发器。历史记录保留，归档/保留策略仍待运维实现。

当前不信任 X-Forwarded-For/X-Real-IP，多个用户通过同一个 Web/反向代理连接 BFF 时会共享该对端额度。可信代理配置、按账号的补充防护及全站请求预算尚未实现。测试也遵守限制：快速重复执行整套登录测试可能需要等待窗口过期，不应通过删除审计记录或放宽产品限制绕过。

## 验证

```sh
pnpm typecheck
YTA_TEST_DATABASE_URL=postgresql://yta_app:local-yta-app@127.0.0.1:54329/yta_test pnpm test
pnpm build
uv run ruff check .
uv run ruff format --check .
python3 scripts/check_docs.py
uv run python scripts/export_contracts.py --check
YTA_TEST_DATABASE_URL=postgresql://yta_owner:local-yta-owner@127.0.0.1:54329/yta_test uv run pytest -q --require-postgres
pnpm exec playwright install chromium
pnpm test:e2e
```

未设置测试数据库时 PostgreSQL 用例会跳过；这不构成数据库验收。测试数据库名称必须以 `_test` 结尾。测试创建独立用户和追加记录，不清空已有数据库。

E2E 使用真实 Web、BFF、Alpha 和 `_test` 数据库，自动创建测试账户，不读取本地登录密码。运行前停止占用 3000、3100、8100 的开发服务；测试不复用现有服务。截图位于 `test-results/research-desktop.png`、`research-mobile.png` 和 `prediction-desktop.png`。CI 配置包含这些检查及截图上传，远端运行仍待验收。Python 的 `--require-postgres` 会拒绝缺失/非 `_test` 数据库配置、未选中 PostgreSQL 用例或其被跳过；默认不加此参数仍可单独运行无数据库的单元测试。

开发服务正在运行时，可用 `pnpm test:e2e:isolated` 在 13000 / 13100 / 18100 启动独立 Web、BFF、Alpha，仍只使用专用 `_test` 数据库和临时测试账户。先构建 BFF；命令会另行构建 Web 到 `.next-e2e`，使用独立 TypeScript 配置，结束后恢复 Next 自动生成的共享类型入口。它不复用或停止原开发服务，也不覆盖 `.next` 产物。可追加 `--grep 'quota refusal'` 等 Playwright 参数；隔离服务随测试结束关闭。

契约变更后执行 `uv run python scripts/export_contracts.py`，将生成的 OpenAPI 和跨语言样例一起提交。CI 会检查生成结果漂移，并运行相同数据库/计算/契约测试。备份演练后还会使用恢复输出的新库名核验原始采集证据，不上传备份归档。

BFF 请求契约位于 `packages/contracts/src/requests.ts` 与共享入口，BFF 直接使用这些 Zod schema。执行 `pnpm contracts:requests:export` 生成 `packages/contracts/bff-requests.schemas.json`，执行 `pnpm contracts:requests:check` 检查漂移；CI 已包含该检查。该文件是 13 个输入 schema 的集合，尚不是完整 BFF OpenAPI/Swagger；邮箱规范化、消息 trim 和日期跨字段约束仍以运行时 Zod 为准。

浏览器 API 定义另见 `packages/contracts/bff.openapi.json`，按 [OpenAPI 3.1](https://spec.openapis.org/oas/v3.1.0.html) 描述当前全部 24 个路由操作。运行 `pnpm contracts:bff:export` 重新生成，`pnpm contracts:bff:check` 检查请求 schema 和 API 定义漂移，`uv run openapi-spec-validator packages/contracts/bff.openapi.json` 做独立规范校验；这两项已纳入 CI。契约测试另外核对控制器路由/成功状态码、引用完整性、Cookie/CSRF 和 SSE 定义。

定义包括登录、研究、预测、评测及健康接口；区分开发 Cookie 与生产 Secure Cookie、明确确认、幂等规则、readiness 503 对象和 SSE 事件格式。Alpha 行情响应复用其输出 schema，不混用输入 Decimal 表达。历史结算结果和审计 payload 按版本保留开放对象，目前未做到全部历史变体的字段级校验；该文件也不代替真实运行验收。

登录后打开 `http://localhost:3000/api/yta/v1/docs` 查看应用内 Swagger；JSON 定义位于 `/api/yta/v1/docs/openapi.json`。文档和所有资源沿用会话鉴权，未登录或会话撤销返回 401。固定版本 Swagger UI 资源随 BFF 打包，通过 Web 的同源转发提供，不依赖 CDN 或外部校验服务。页面用于只读查看，关闭 Try it out、URL 配置覆盖和授权持久化；不会替代产品中的研究与冻结确认流程。文档资源均 no-store，未知资源路径不作为目录公开。

| 请求位置 | 共享契约与边界 |
|---|---|
| 登录请求体 | loginRequestSchema；邮箱转小写，密码不 trim，拒绝额外字段 |
| 会话/评测列表查询 | pageQuerySchema；UUID cursor，可选 limit 为 1–100 的十进制整数字符串 |
| 预测列表查询 | predictionQuerySchema；在分页参数之外允许既定 status 枚举 |
| 消息历史查询 | messageQuerySchema；cursor 为至多 18 位数字字符串，默认 0，不转为 JavaScript number |
| SSE 查询/恢复头 | eventQuerySchema；查询仅允许 UUID cursor；有效 Last-Event-ID 优先于查询游标，两者均须合法 |
| 创建会话/发送消息/冻结与更正/评测请求体 | createSessionSchema、sendMessageSchema、freezeRequestSchema、evaluationRequestSchema |
| 资源 ID/证券/幂等键/空请求体 | resourceIdSchema、stockSymbolSchema、idempotencyKeySchema、emptyRequestSchema |

上述分页、消息与事件查询拒绝未知字段、重复参数产生的数组，以及不符合格式的游标；返回 422/INVALID_REQUEST。客户端不能通过 owner_id 或 data_mode 查询参数改变身份或数据模式。SSE 参数错误在发送流式响应头之前返回 JSON 错误；游标归属与存在性仍由服务端持久化记录核对。

BFF JSON 请求体上限为 32 KiB，按解压后的字节数计算，分块传输同样受限。解析错误在业务调用前返回统一 `{error: {code, message, request_id, retryable}}`，请求 ID 与响应头 `X-Request-ID` 一致，不回显原始请求体或解析器诊断。

| 请求体问题 | HTTP / code |
|---|---|
| JSON 语法错误或不允许的顶层原始值 | 400 / MALFORMED_JSON |
| 损坏的压缩体等读取错误 | 400 / INVALID_BODY |
| 超过解压后的 32 KiB 上限 | 413 / REQUEST_TOO_LARGE |
| 不支持的 Content-Encoding | 415 / UNSUPPORTED_ENCODING |
| 不支持的字符集 | 415 / UNSUPPORTED_CHARSET |

这些错误均标记 `retryable: false`，需修正请求后重试。合法 JSON 的字段错误仍返回 422/INVALID_REQUEST；未知服务器错误仍返回脱敏 500，不采用任意异常对象携带的 HTTP 状态。

## 发布输入核对

发布准备可运行 `uv run python scripts/release_inventory.py --inspect-local-images > .local/release-inventory.json`。报告列出应用/迁移/Hermes 的不可变镜像摘要、迁移文件哈希、Hermes 门槛状态和已记录源码与当前文件的一致性；仅执行本地 Docker image inspect，不拉取或启动镜像。省略选项则不检查本地镜像是否存在。源文件变更、缺失、迁移镜像目录落后或本地镜像缺失会返回非零退出码，供重新验证输入使用。

可选追加 `--database-env YTA_RELEASE_DATABASE_URL`，从明确指定的环境变量读取连接地址，在只读事务中核对目标数据库的迁移注册表。`consistent` 表示记录的校验和一致，`pending` 表示尚有目录内迁移未执行，`conflict` 表示校验和不同或数据库包含当前目录之外的迁移；连接、权限及元数据问题也会返回非零。该检查不执行/修复迁移，不输出地址和凭证；未提供选项时数据库保持 `NOT_QUERIED`。显式指定但未设置的环境变量会失败，不悄悄跳过。

**退出码 0 只表示被检查的输入一致，不是发布许可。** 清单不覆盖所有构建输入，不验证目标机器架构、实际表结构/运行权限、真实行情资格、模型身份、staging、远端 CI 或人工验收；迁移注册表一致也不能代替运行角色 readiness。`release_qualification` 始终为 `NOT_ASSESSED`。禁止以该命令成功替代发布 Gate。报告不读取 pi 凭证或任何 `.env` 文件。

## 代码结构

- `apps/bff`：会话鉴权/CSRF、持久化研究事件、取消与恢复、Alpha 私有调用。
- `apps/web`：Next.js 工作台、Lightweight Charts 多窗格、确认弹窗与预测记录。
- `services/alpha-engine`：交易日历、Decimal baseline、技术指标、内部 API、不可变预测事实仓库。
- `packages/contracts`：Zod 公共边界、事件与 Canvas 命令，以及生成的 Alpha OpenAPI。
- `infra/migrations`：追加事实与幂等、审计、结算版本约束。
- `infra/compose`：仅绑定本机端口的独立开发数据库。
- `tests/integration`：真实 PostgreSQL 的并发、权限与重试测试。

真实接入与上线必须按任务书完成供应商资格核验、Hermes 锁版本与能力实测、生产权限审查、完整异常场景 E2E、备份恢复及部署验收。当前代码不会把这些未完成项目标为已通过。


## Hermes 候选源码核验

候选 commit 为 `085f562af53a6fedadac3681e2c498fc71316132`，锁文件位于 `infra/hermes/source-lock.json`，审查结论见 [Hermes 源码核对](docs/hermes-integration-research.md)。对已检出的干净源码目录运行：

```sh
python3 scripts/check_hermes_source.py --checkout /path/to/hermes-agent
```

检查器验证 HEAD、28 个审查文件的 SHA-256 及工作区状态；修改、缺失、未跟踪文件或路径越界会失败。它不安装依赖、不执行 Hermes，成功也始终返回 `integration_status: UNVERIFIED`。该候选使用独立 Python 3.14 环境，不应安装进 Alpha 的 Python 3.12 环境。本机 arm64 候选已构建，镜像摘要见锁文件，启动与只读探测证据见 `infra/hermes/runtime-observation.json`。真实运行能力仍待验收，当前真实 Adapter 继续报告不可用。

如果 Git 的按需 blob 下载不可用，可以下载锁文件 `source_archive.url` 指定的官方归档，再用已取得锁定 commit/tree 的 Git checkout 做完整验证：

```sh
uv run python scripts/prepare_hermes_archive.py \
  --checkout /path/to/hermes-agent \
  --archive /path/to/source.tar.gz \
  --output-parent /path/to/build-sources
```

输出目录为 `hermes-agent-<commit>`，必须尚不存在。脚本先验证全部路径集合、Git blob 内容及可执行位，再解压；拒绝链接、越界路径和新增/缺失文件。仅当锁定 `.gitattributes` 明确要求 PowerShell CRLF，且规范化后的内容哈希与 Git 一致时接受该换行差异；归档原内容保持不变。当前候选 21,418 文件、38 个此类换行差异及归档哈希已记录于锁文件。须使用 `uv run python` 的 Python 3.12 安全解压过滤器。

本地启动探测配置位于 `infra/compose/compose.hermes-probe.yaml`，用于固定版本的启动、能力声明与独立的无凭证失败路径探测。它要求已构建的本地镜像（`pull_policy: never`），采用 UID/GID 10000、Docker init、只读根文件系统、独立数据卷和临时目录；禁用网络且不映射端口，不挂载现有应用数据或模型凭证。当前已实测启动及空闲重启前后的三个 GET：匿名/错误凭证均 401，有效凭证 200，声明持久幂等可用。启动探测阶段未提交 Run；这些启动观察不证明执行、停止或恢复能力。后续独立失败 Run 探测见下文。

构建成功并检查 revision 标签及实际镜像 ID 后，在权限为 0600 的 `.local/hermes-probe.env` 中保存 `YTA_HERMES_PROBE_IMAGE=sha256:<实际镜像ID>` 和随机生成的独立 `YTA_HERMES_PROBE_TOKEN`（至少 32 字符）。该 token 仅用于探测实例的 API 鉴权，不是模型供应商密钥。使用以下命令启动和停止；停止保留独立数据卷：

```sh
docker compose --env-file .local/hermes-probe.env \
  -f infra/compose/compose.hermes-probe.yaml up -d
docker compose --env-file .local/hermes-probe.env \
  -f infra/compose/compose.hermes-probe.yaml down
```

该实例没有宿主机可访问端口，须在容器内执行探测：通过 `docker exec -i` 的标准输入将脚本写入可写 `/tmp`（Docker 会拒绝向只读根容器 `docker cp`），以 UID 10000 从实例环境变量生成私有 token 文件，然后使用镜像 Python 运行下述探测命令（替换为容器内路径），并将证据以 0600 保存回本机。重启会清空 `/tmp`，须重新传入脚本。不要为宿主机探测临时开放端口。对于另行部署且仅监听本机的隔离实例，可直接运行：

```sh
uv run python scripts/probe_hermes.py \
  --origin http://127.0.0.1:8642 \
  --token-file .local/hermes-probe.token \
  --output .local/hermes-capabilities.json
```

token 文件权限必须为 0600；不要在命令行或聊天中填写密钥。探测只允许 loopback IP 的 HTTP(S) origin，禁用代理与重定向，依次发送无凭证、错误凭证和有效凭证的三个 GET，不创建 Run。响应有超时和 64 KiB 限制；证据文件以 0600 新建，拒绝覆盖，终端不打印原始响应或凭证。`required_claims_present` 仅表示声明符合预期，始终保留 `integration_status: UNVERIFIED` 和 `runtime_revision_verified: false`；必须另做锁定镜像、会话、工具、停止、恢复及隔离实测。

候选镜像的应用 Python 内建 SQLite 3.53.1，系统 Python 使用单独编译的 3.53.4；不能用系统解释器的检查代替应用解释器。应用 source ID 与 [SQLite 3.53.1 发布记录](https://sqlite.org/releaselog/3_53_1.html)一致，该发布已包含 WAL-reset 修复。可用以下独立临时数据库检查实际应用的 WAL、锁冲突、回滚、重开与 FTS5；它不读写 Hermes 数据卷，也不代表任务运行恢复已验收：

```sh
docker run --rm -i --network none --read-only --user 10000:10000 \
  --cap-drop ALL --security-opt no-new-privileges:true \
  --tmpfs /tmp:rw,nosuid,nodev,size=16m,mode=1777 \
  --entrypoint /opt/hermes/.venv/bin/python \
  sha256:67f82b05c5c083f2795cff9c361b4f5c464116fcc4301671709376e6f64e3226 \
  -I -S - < scripts/check_hermes_sqlite.py
```

Hermes 的待接入研究配置见 `infra/hermes/alpha-orchestrator.bootstrap.yaml`。这是尚未开放金融工具与 Provider 的 bootstrap，不是可执行研究的完整 Profile。API/CLI 显式使用 `no_mcp` 并禁用 `x_search`：锁定版本的空列表会自动加入全局 MCP；只有 `no_mcp` 时，检测到 xAI 凭证仍会加入搜索工具。配置中的 12 次工具迭代和 120 秒闲置限制也不等于金额预算或总运行时限。

`scripts/check_hermes_tool_policy.py` 必须在候选镜像内执行，使用上游实际 YAML 与工具集解析器。已验证 API/CLI 各自的基础、意外全局 MCP、额外凭证三种情形均不开放工具；移除 `x_search` 禁用项的反向检查会失败。它不启动 MCP 子进程或模型请求，不验证请求级覆盖、工具执行权限或完整 Profile 隔离。金融 MCP 接入后必须更新白名单并重新验证这些边界。

```sh
docker run --rm -i --network none --read-only --user 10000:10000 \
  --cap-drop ALL --tmpfs /tmp:rw,nosuid,nodev,size=32m,mode=1777 \
  --env HERMES_HOME=/tmp/yta-policy \
  --mount "type=bind,source=$PWD/infra/hermes/alpha-orchestrator.bootstrap.yaml,target=/yta-bootstrap.yaml,readonly" \
  --entrypoint /opt/hermes/.venv/bin/python \
  sha256:67f82b05c5c083f2795cff9c361b4f5c464116fcc4301671709376e6f64e3226 \
  - /yta-bootstrap.yaml < scripts/check_hermes_tool_policy.py
```

Alpha 已提供金融工具专用的两个私有 GET 端点：`/internal/v1/tools/market-snapshot` 返回原始快照及 Alpha 计算的 `data_snapshot_hash`；`/internal/v1/tools/indicators` 返回同一计算口径的指标、图表条目、数据模式、来源、截点与哈希。配置独立的 `YTA_ALPHA_TOOLS_TOKEN` 后才能访问，未配置时一律 401。该值须与 BFF/Jobs 使用的 `YTA_ALPHA_TOKEN` 不同，不交给浏览器，也不提供给模型上下文。

工具凭证只能访问这两个端点，不能访问预测、草稿、冻结、结算、报告或其他内部接口；普通服务凭证也不能替代工具凭证访问它们。LIVE_RESEARCH 没有真实行情时继续返回 503/DATA_UNAVAILABLE。工具进程只取得这个专用凭证，当前 Hermes bootstrap 仍不开放工具。

`services/tools` 提供独立 stdio MCP 进程 `yta-finance`，仅注册无参数的 `market_snapshot` 和 `indicators`。它使用 [官方 Python SDK](https://github.com/modelcontextprotocol/python-sdk) 2.3.0，与候选 Hermes 锁定的 SDK 版本一致；安装通过可选 `tools` extra，Alpha 生产镜像不安装 MCP 依赖。

在权限为 0600 的 `.local/tools.env` 中配置 `YTA_TOOLS_ALPHA_URL`（私有 Alpha origin）、`YTA_TOOLS_ALPHA_TOKEN`（对应 Alpha 的独立工具凭证）及 `YTA_TOOLS_DATA_MODE`（明确选择 REPLAY 或 LIVE_RESEARCH）。工具设置不自动读取项目 `.env`，不需要 BFF 服务凭证或数据库账号。可由 MCP host 使用以下命令启动，stdout 专用于协议：

```sh
uv run --locked --extra tools --env-file .local/tools.env python -m yta_tools.server
```

模型参数不能覆盖 URL、所有者、模式或凭证；任何额外参数被拒绝。只向固定两个路径发送 GET，关闭环境代理和重定向，限制响应为未压缩 JSON、2 MiB、单次网络等待 5 秒及整体 15 秒。校验快照/指标结构、模式、日期、哈希和十进制字符串，保持缺失 null。失败返回 MCP `isError` 和固定错误码，不输出内部地址、原始错误正文或凭证，不回退 fixtures。stdio 与私网 HTTP 验证均使用合成 REPLAY Alpha；真实供应商及模型驱动工具调用仍待验收。

工具服务也提供私网 Streamable HTTP `/mcp`，启动入口为 `yta_tools.http:create_http_app`。额外要求 `YTA_TOOLS_MCP_TOKEN`，必须与 Alpha 工具凭证不同：Hermes 只持有 MCP 凭证，工具服务持有 Alpha 工具凭证。拒绝匿名/错误/重复 Authorization、所有浏览器 Origin、不在白名单的 Host；限制请求体 32 KiB，响应 no-store。只支持私有服务凭证，不提供公网 OAuth 或浏览器接入。

隔离演练配置为 `infra/compose/compose.finance-probe.yaml`：Alpha 与工具服务均非 root、只读、无公开端口、无 PostgreSQL，使用两个 internal 网络。Hermes 探测容器只加入 tools 网络，不能解析 Alpha。先构建 tools target；在 0600 的 `.local/finance-probe.env` 中设置两项实际本地镜像 ID `YTA_FINANCE_ALPHA_IMAGE` / `YTA_FINANCE_TOOLS_IMAGE`，以及三个分别生成的随机 token `YTA_FINANCE_SERVICE_TOKEN` / `YTA_FINANCE_ALPHA_TOKEN` / `YTA_FINANCE_MCP_TOKEN`。另建 0600 的 `.local/hermes-finance-probe.env`，仅包含相同的 `YTA_FINANCE_MCP_TOKEN`，不要复制其余凭证。

```sh
docker build -f infra/docker/alpha.Dockerfile --target tools -t yta-finance-tools:local .
docker compose --env-file .local/finance-probe.env -f infra/compose/compose.finance-probe.yaml up -d
docker run --rm --init -i --network yta-finance-probe_tools --read-only \
  --user 10000:10000 --cap-drop ALL --security-opt no-new-privileges:true \
  --tmpfs /tmp:rw,nosuid,nodev,size=64m,mode=1777 \
  --env HERMES_HOME=/tmp/yta-finance-probe/profiles/alpha-orchestrator \
  --env-file .local/hermes-finance-probe.env \
  --entrypoint /opt/hermes/.venv/bin/python \
  sha256:67f82b05c5c083f2795cff9c361b4f5c464116fcc4301671709376e6f64e3226 \
  - < scripts/probe_hermes_finance.py
docker compose --env-file .local/finance-probe.env -f infra/compose/compose.finance-probe.yaml down
```

该探测在一次性 Profile 中使用固定 Hermes 的实际 MCP discovery/registry，验证仅注册两工具、选择 yta-finance、成功结果的 REPLAY/哈希/字符串/null、参数注入拒绝及 Alpha DNS 隔离，禁用 MCP sampling。证据见 `runtime-observation.json` 的 `finance_bridge`。它没有创建 Gateway Run 或调用模型，不证明 SSE、取消、运行中恢复、跨用户授权或完整 Profile 隔离已通过。

### 无模型凭证的 Run 失败路径探测

`scripts/probe_hermes_failure.py` 只允许在显式探测模式、仅 loopback 网卡启用的容器内运行，拒绝已配置的常见模型 API key。使用相同固定 Hermes 镜像与 Compose 文件，但指定独立项目 `-p yta-hermes-failure-probe` 和单独 0600 env 文件，保留独立卷；不要复用正式会话数据。

将脚本通过 stdin 写入容器 `/tmp/probe_hermes_failure.py` 后，执行：

```sh
docker exec --env YTA_HERMES_FAILURE_PROBE=1 \
  yta-hermes-failure-probe-hermes-1 python /tmp/probe_hermes_failure.py submit
```

`submit` 在提交前独占创建 0600 请求收据，不覆盖旧收据；失败后使用 `verify` 沿用同一个键核对，不删除收据或自动换键。重启同一实例并重新传入 tmpfs 脚本后，使用 `replay` 检查持久状态与同键冲突行为。脚本只输出脱敏协议摘要，收据包含请求身份并保留在独立卷中。

实际观察：未认证提交 401，已认证接收 202，未配置模型 Provider 导致 failed；同键同正文返回原 Run，同键不同正文 409。重启前 SSE 提供 run.failed，已读游标恢复不重复发送；重启后原 Run 仍可查询且同键不新增记录，但旧 SSE 返回 404。该实测仅覆盖一个失败 Run，不证明模型执行、多轮会话、运行中取消/崩溃恢复或完整 Profile 隔离；所有总体门槛仍为 UNVERIFIED。

### Hermes 合成模型与实际金融工具链演练

`infra/compose/compose.hermes-model-probe.yaml` 使用固定 Hermes、Alpha 与 tools 镜像，模型端由 `hermes_model_fixture.py` 返回固定工具调用及明确标注的合成文本。三个网络均为 internal，无宿主机端口；Hermes 只能访问合成模型和 MCP，不能解析 Alpha。所有行情为 REPLAY，不请求真实模型或行情供应商。

在单独 0600 的 `.local/hermes-model-probe.env` 设置三个不可变镜像 ID：`YTA_HERMES_PROBE_IMAGE`、`YTA_FINANCE_ALPHA_IMAGE`、`YTA_FINANCE_TOOLS_IMAGE`；另独立生成五个随机 token：`YTA_HERMES_PROBE_TOKEN`、`YTA_MODEL_FIXTURE_TOKEN`、`YTA_FINANCE_SERVICE_TOKEN`、`YTA_FINANCE_ALPHA_TOKEN`、`YTA_FINANCE_MCP_TOKEN`。模型 token 只用于这个合成端点，不填入真实模型密钥。

```sh
docker compose --env-file .local/hermes-model-probe.env -f infra/compose/compose.hermes-model-probe.yaml up -d
docker exec yta-hermes-model-probe-hermes-1 python -c 'import urllib.request; print(urllib.request.urlopen("http://127.0.0.1:8642/health").status)'
docker exec yta-hermes-model-probe-hermes-1 python /probe/probe_hermes_model.py submit --case direct
docker exec yta-hermes-model-probe-hermes-1 python /probe/probe_hermes_model.py verify --case direct
docker compose --env-file .local/hermes-model-probe.env -f infra/compose/compose.hermes-model-probe.yaml down
```

等待健康检查成功再提交。脚本先独占保存 0600 请求收据；中断或失败后使用 verify 沿用同一键，不能删除收据改键重发。已保留卷的 direct 用例只能 verify；重启后内存 SSE 可能已消失，不能把 404 当作未执行。down 保留独立卷及收据。

锁定版本禁止命名 Profile 默认独立启动，因此本演练显式设置 `gateway.standalone: true` 兼容选项；并以 `tools.tool_search.enabled: off` 直接暴露两个 MCP schema。默认工具搜索路径、Profile 多路复用与真实模型仍未验收。实际已观察两组 tool.started/tool.completed（无错误且调用 ID 对应）、message.delta、run.completed，同键复查和末尾游标无重复；这些只证明此配置下的合成模型协议链，不证明真实推理质量、取消或崩溃恢复。

### 使用 pi agent 的真实模型配置

用户已选择沿用 `~/.pi/agent/settings.json` 的默认 Provider/模型及 `models.json` 中对应路由。本机实测选择为 `volc-glm / deepseek-v4-pro`，Anthropic Messages 兼容接口；直接请求返回模型版本 `deepseek-v4-pro-ga-260813`。这条真实模型路径独立于上面的合成模型服务。

`uv run python scripts/prepare_pi_model_probe.py` 只读取选中的路由，导出 `.local/pi-model-probe.json`（0600、独占创建，不覆盖）；不复制其他 Provider、不修改 pi 配置、不执行配置中的 shell 凭证命令。模型地址必须为无内嵌凭证的 HTTPS。私有快照包含所需鉴权头，仅传入独立 Hermes 数据卷，不挂载整个 pi 目录。

新环境首次为固定镜像预置私有卷：

```sh
docker run --rm -i --network none --read-only --user 10000:10000 \
  -v yta-hermes-pi-probe-data:/opt/data --entrypoint python \
  sha256:67f82b05c5c083f2795cff9c361b4f5c464116fcc4301671709376e6f64e3226 \
  -c 'import os,sys; f=os.fdopen(os.open("/opt/data/pi-route.json",os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600),"wb"); f.write(sys.stdin.buffer.read()); f.close()' \
  < .local/pi-model-probe.json
```

另建 0600 的 `.local/hermes-pi-probe.env`，配置 `YTA_HERMES_PROBE_IMAGE`、`YTA_FINANCE_ALPHA_IMAGE`、`YTA_FINANCE_TOOLS_IMAGE` 的固定镜像 ID，以及四个独立随机 token：`YTA_HERMES_PROBE_TOKEN`、`YTA_FINANCE_SERVICE_TOKEN`、`YTA_FINANCE_ALPHA_TOKEN`、`YTA_FINANCE_MCP_TOKEN`。真实模型密钥不放入此 Compose env 文件。

```sh
YTA_FINANCE_ALPHA_IMAGE=$(docker image inspect yta-alpha:local --format '{{.Id}}') \
  docker compose --env-file .local/hermes-pi-probe.env -f infra/compose/compose.hermes-pi-probe.yaml up -d
docker exec yta-hermes-pi-probe-hermes-1 python -c 'import urllib.request; print(urllib.request.urlopen("http://127.0.0.1:8642/health").status)'
docker exec yta-hermes-pi-probe-hermes-1 python /probe/probe_hermes_pi.py submit
docker exec yta-hermes-pi-probe-hermes-1 python /probe/probe_hermes_pi.py verify
docker compose --env-file .local/hermes-pi-probe.env -f infra/compose/compose.hermes-pi-probe.yaml down
```

健康检查成功后再提交。保留卷中已有请求时使用 verify，不删除收据重新提交；重启后 SSE 不保留的边界与前述相同。Hermes 有模型出网网络，无公开端口；金融工具及 Alpha 仍在 internal 网络，Hermes 无 Alpha DNS 可见性。模型使用 pi 的思考设置，off 映射到锁定版本的 `agent.reasoning_effort: false`；最多 4 轮工具循环、Gateway 超时 120 秒。该配置仍采用 standalone 兼容选项和直接工具 schema。

真实模型已成功调用 market_snapshot、indicators，完成 SSE、关联工具 ID、明确披露 REPLAY、同键复查和游标验证。行情依然是合成 REPLAY，不能用于 LIVE_RESEARCH；模型调用通过不代表取消、未知执行恢复、完整 Profile 隔离、预算控制或 staging 验收通过。Gateway 的 usage 是该 Run 报告的用量，不是账户账单或辅助请求总量。脱敏证据见 `infra/hermes/runtime-observation.json`。

### 真实模型运行中取消与崩溃观察

在上述独立 pi 演练项目启动且健康后，`python /probe/probe_hermes_cancel.py submit`（通过 docker exec 执行）先保存独立请求身份，再等待真实模型首段非空 message.delta 后发送 stop。实测取消终态、run.cancelled、重复 stop、同键复查及游标均通过；错误 token 的 stop 返回 401。后续同进程检查用 verify；重启同一卷后用 restart，实测取消终态保留，同键仍指向原 Run，但旧 SSE 为 404。已有收据不可删除后重发。

`probe_hermes_crash.py start` 使用另一独立身份，在实际输出后核对状态 running 并保存 admission_id / execution_generation。仅对此演练项目的 Hermes 容器执行 SIGKILL（本次退出 137），随后启动同一容器与数据卷。observe 实测上游状态字面值为 `unknown`，执行代次保留；同键不会生成另一个 Run，普通 stop 返回 409/unknown_execution，旧 SSE 返回 404。

resolve 是本演练明确执行的操作员确认：错误代次返回 409/stale_generation 且保持 unknown；精确 admission_id + execution_generation 的确认及其重复请求均成功，状态转为 cancelled，相同请求键仍返回原 Run。确认只解除该未知执行阻塞，不代表上游没有执行过，也不授权重发提示。未来 Adapter 应保留 EXECUTION_UNKNOWN 与人工确认的审计含义，不能仅凭确认后的 cancelled 抹去不确定历史。

这些观察覆盖模型文字输出中的取消/崩溃，不证明工具执行中取消、外部计算或计费立即停止、金融副作用恢复、全部 Profile 隔离或 staging 通过。所有总体门槛继续 UNVERIFIED；不把命令超时当作进程已结束，也不自动创建新键重试。

### 真实模型会话连续性

同一独立 pi 演练项目中，`probe_hermes_session.py` 的 seed / recall / isolated / restart 阶段分别验证初始记忆、同会话回忆、独立会话分离和 Gateway 重启后的回忆。通过 docker exec 执行；restart 阶段前实际重启 Hermes 并等待健康检查成功。

所有阶段的请求键、两个随机 X-Hermes-Session-Key 和随机标记在首次提交前一起保存为 0600 收据。后续请求只含 model/input，不再次提供标记、session_id 或 conversation_history；会话完全由头部选择。实测两次回忆均精确返回原标记，共用原 session_id；独立会话返回 NONE 且 session_id 不同。每一阶段的同键复查均返回原 Run；重复执行阶段沿用收据，不重新调用模型生成新 Run。

此证据验证单一服务凭证下两个会话的上下文分离，不代表不同用户授权或跨 Profile 凭证隔离；BFF 仍须从已认证所有者映射会话，不能直接信任浏览器提供的上游 session key。上下文压缩和会话轮换尚未覆盖。

### 幂等保留期与恢复边界

`probe_hermes_retention.py` 在固定镜像的无网络容器中加载哈希匹配的真实 RunIdempotencyStore，以临时 SQLite 和受控时钟验证默认 86400 秒边界、四类终态过期、活动/未知状态保留、显式延长及冲突请求不能延长、重新打开后的身份、12 个连接竞争只产生一份预留。它不修改运行中 Gateway 的时钟，不调用模型。

`probe_hermes_retention_http.py` 是独立 HTTP 演练：复用无网络启动 Compose，但使用新项目 `yta-hermes-retention-http`、单独 token 和数据卷，不配置任何模型凭证。先运行 `probe_hermes_failure.py submit` 建立已确认 failed 的记录；再仅将该测试记录的 updated_at 设为当前时间减 86401 秒。实际同键同正文 POST 返回新的 Run（replayed=false），后续复查才返回这个新 Run；旧 Run 仍可按原 ID 查询。演练保留两个身份，拒绝重复运行过期实验。脚本通过 docker exec stdin 写入 /tmp；只读容器不使用 docker cp 修改根文件系统。

这不是等待了真实 24 小时的耐久测试，也不代表允许对业务请求修改时间或重发。**原键只能在上游仍保留其记录时防重，不能作为永久 exactly-once 保证。** BFF 必须保留本地发送/接收身份；已知 Run 优先查状态，记录不存在或接受状态不明时保留 EXECUTION_UNKNOWN，不把过期/404 当作未执行。现有 Runner 对持久 run.started 的恢复不调用 driver，且未知会话拒绝新提交；真实 Adapter 尚未接入，不能把该本地防线冒称为已完成跨系统恢复。

### 多 Profile HTTP 权限演练

`compose.hermes-profile-probe.yaml` 创建单独 network-none Gateway 和数据卷，通过原生 `gateway.multiplex_profiles: true` 服务 default 与 alpha-orchestrator。两个 Profile 具有独立随机 API_SERVER_KEY，禁用 MCP，无模型凭证；这仅是权限测试，不增加 MVP 产品中的 Profile。

在 0600 的 `.local/hermes-profile-probe.env` 中设置固定 `YTA_HERMES_PROBE_IMAGE` 及分别生成的 `YTA_HERMES_PROBE_TOKEN` / `YTA_PROFILE_PROBE_ALPHA_TOKEN` 后运行：

```sh
docker compose --env-file .local/hermes-profile-probe.env -f infra/compose/compose.hermes-profile-probe.yaml up -d
docker exec yta-hermes-profile-probe-hermes-1 python -c 'import urllib.request; print(urllib.request.urlopen("http://127.0.0.1:8642/health").status)'
docker exec yta-hermes-profile-probe-hermes-1 python /probe/probe_hermes_profiles.py submit
docker exec yta-hermes-profile-probe-hermes-1 python /probe/probe_hermes_profiles.py verify
docker compose --env-file .local/hermes-profile-probe.env -f infra/compose/compose.hermes-profile-probe.yaml restart hermes
# 等待健康检查成功后，使用原收据重复 verify。
docker exec yta-hermes-profile-probe-hermes-1 python /probe/probe_hermes_profiles.py verify
docker compose --env-file .local/hermes-profile-probe.env -f infra/compose/compose.hermes-profile-probe.yaml down
```

实测自身凭证访问成功，匿名与另一 Profile 凭证 401；用本 Profile 有效凭证访问另一 Profile 的 Run 状态、SSE、stop、resolve-unknown 均 404。两个 Profile 使用同一请求键/正文仍创建各自的失败 Run，自身重放与正文冲突符合预期，未配置 Profile 返回 404。6 个线程混合发送 36 个只读请求，200/401/404 各 12 次；重启前后均通过，同键身份保留。

Run 因没有模型凭证而失败，外部模型请求为 0。此矩阵证明 HTTP 认证、归属与请求作用域边界，不证明模型凭证、文件系统、Skill 或全部工具隔离；后续须独立验证这些边界，不能将 network-none 的权限演练视为完整真实研究接入。

### pi 模型凭证的跨 Profile 正反例

`compose.hermes-secret-probe.yaml` 是另一个独立测试项目，允许模型出网但没有公开端口或金融工具。按前述私有 route 预置步骤建立 `yta-hermes-secret-probe-data`，仅把选中的 pi 路由写入该卷的 pi-route.json；单独 0600 env 文件配置固定镜像及两个独立 API token。启动脚本只给 default Profile 的 .env / custom_providers 写入模型密钥与鉴权头，alpha-orchestrator 保留相同模型路由但不配置凭证。

```sh
docker compose --env-file .local/hermes-secret-probe.env -f infra/compose/compose.hermes-secret-probe.yaml up -d
# 健康检查成功后首次提交；已有收据时仅 verify。
docker exec yta-hermes-secret-probe-hermes-1 python /probe/probe_hermes_secrets.py submit
docker exec yta-hermes-secret-probe-hermes-1 python /probe/check_hermes_secret_scope.py
docker exec yta-hermes-secret-probe-hermes-1 python /probe/probe_hermes_secrets.py verify
docker compose --env-file .local/hermes-secret-probe.env -f infra/compose/compose.hermes-secret-probe.yaml down
```

实测 default 使用 pi 的 deepseek-v4-pro 完成固定校验回答；alpha-orchestrator 返回 failed 和供应商 HTTP 401，报告 token 用量为 0，没有取得另一 Profile 的成功回答。它确实访问了配置的供应商，因此不能把该反例描述成“本地直接拒绝、没有网络请求”。重启后同键复查保持两份原 Run。

另外在固定运行时代码中显式设置 ambient OPENAI_API_KEY，按 Profile 作用域核对密钥及 custom-provider headers：default 匹配，alpha-orchestrator 为空，未绑定作用域的读取抛出 UnscopedSecretError。6 个线程交错检查 48 次通过；两 Profile 的已解析工具定义均为空。检查不输出凭证。**Profile 共享进程 UID 与文件系统，这不是操作系统沙箱**；本轮不能证明允许任意文件/终端工具后仍能保密，也不替代真实工具执行权限验证。

## 研究报告契约

单 Profile 结构化研究协议见 [alpha-orchestrator SOP](infra/hermes/prompts/alpha-orchestrator.md) 与 [research-report-v1 schema](packages/contracts/research-report.schema.json)。`validateResearchReport` 以服务端验证过的 Run 上下文核对证据、截点、模式及草稿引用；JSON 字段合法不等于来源真实，也不构成冻结授权。协议支持 DRAFT_READY/UNAVAILABLE，金融数值留在 Alpha 草稿中。REPLAY 成功流程现从 Alpha 的受服务鉴权 `/internal/v1/market/evidence` 取得原始快照及哈希，结合指标响应、实际工具调用 ID 和草稿构建报告，保存在 `message.completed.payload.report`，通过既有 `/runs/{id}` 返回；旧事件没有 report 时返回 null。证据冲突时不发布草稿/报告。工作台的“研究依据与风险”可展开查看来源、风险、截点与快照，重开会话后恢复；异常报告会被拒绝展示并清除冻结入口。当前尚未装入真实 Hermes Driver。使用 `pnpm contracts:report:export` 更新 JSON Schema、`pnpm contracts:report:check` 检查漂移；CI 已加入检查，远端执行待验收。

## 行情供应商候选

官方接口、价格/许可边界与未完成资格项见 [供应商研究](docs/market-provider-research.md)。当前尚未选择供应商或配置真实账户。`yta_alpha.massive.MassiveCandidate` 是默认不接入服务的采集候选：固定官方 HTTPS origin、NVDA 未复权日线及当前拆股端点，保留每页原始 bytes、请求范围、SHA-256 和实际采集时间；JSON 小数直接解码为 Decimal，拒绝重复键及非有限值。

候选采集器限制每页 2 MiB、每个端点最多 10 页、整体 60 秒、单次网络等待 5 秒；不跟随重定向，分页不得换 host/path 或修改原查询范围。401/403/429/网络错误明确失败，不自动重试、补价格缺口或回退 fixtures。采集结果始终为 UNVERIFIED，不归一为 MarketSnapshot。`AcquisitionStore.save` 可将结果及明确的 data_mode 写入迁移 009 的独立原始证据表；同一证据重试去重，新采集时间或原始响应变化追加新记录，并原子记录审计。运行角色仅需 SELECT/INSERT；部署时按既有流程使用独立 owner 迁移并授予新增表权限。当前仅在专用测试库验证，尚未挂接 API/Worker 定时采集。公司行动覆盖、收盘价口径、实际账户两年权限、许可及版本证据完成前，API/Worker 的 LIVE_RESEARCH 仍保持不可用。

候选行情日线存储：迁移 `013_market_bars.sql` 增加不可变 OHLCV，`AcquisitionStore.save` 与原始证据原子提交。`daily_bars(hash, data_mode=...)` 复核原文后返回 Decimal 字符串、来源页/行号、纽约日期及采集时间；`published_at=null`、`available_at=retrieved_at`，始终 RAW/UNVERIFIED，不填缺口或选择“最新合格价格”。旧采集缺少投影时读取明确失败，可使用已验证的原始 Acquisition 按原键 save 补齐，无需重新请求供应商；没有自动批量回填。部署采集器前须应用 013 并授予 market_bars 的 SELECT/INSERT，预检会检查权限。当前仅本地 yta_test 已升级；Alpha/BFF/Web 镜像已重建并通过本地 REPLAY 全栈容器检查，实际供应商和 staging 验收仍未完成。

候选采集运行入口为 `python -m yta_alpha.acquire`。先由独立 owner 应用迁移并给 runtime 角色授予新增表 SELECT/INSERT；命令会拒绝持有 UPDATE/DELETE/TRUNCATE 的账号。通过私有环境配置 `YTA_ACQUIRE_MASSIVE_TOKEN` 和 `YTA_ACQUIRE_DATABASE_URL`，不会自动读取项目 `.env`，不要把凭证放在命令参数中。选择供应商并确认账户使用边界后，可执行：

```sh
uv run python -m yta_alpha.acquire \
  --start 2024-10-27 --end 2026-10-27 --data-mode LIVE_RESEARCH
```

此命令会请求真实 Massive，可能消耗账户配额；不包含 fixture 回退或自动重试。日期和数据模式必须显式指定，实际历史权限以账户和响应为准。采集预检、写入和复查的数据库连接等待上限为 5 秒、锁等待 5 秒、单条 SQL 执行 15 秒；这些是单次操作时限，不是全流程绝对 deadline。锁/语句超时会回滚当前事务并明确失败，不自动重复请求供应商。成功只输出原始采集哈希、模式、页数/记录数及 UNVERIFIED；LIVE_RESEARCH 标签不会将候选证据提升为已核验快照或正式评测输入。每次正式尝试先追加 provider.acquire_started 审计，成功结束事件与原始证据同事务提交；失败仅保存白名单错误码。成功输出包含 attempt_id，开始后的失败也返回该 ID 以便追溯。失败输出固定错误码及非零退出码，不打印响应体、凭证或数据库连接字符串。只有开始事件而无终态代表中断或结果尚待核对，不能当作已失败后自动重发。当前验证使用模拟 HTTP 响应与真实测试数据库，没有执行上述真实供应商命令。

采集后可在新进程中读取并核验原始证据；只需 `YTA_ACQUIRE_DATABASE_URL` 和数据库 SELECT 权限，不需要行情凭证，也不会发出供应商请求：

```sh
uv run python -m yta_alpha.inspect_acquisition \
  --hash <采集输出的acquisition_hash> --data-mode LIVE_RESEARCH
```

检查会重算 manifest 和每页内容哈希，验证页数、顺序、元数据及日线/拆股规则，并从原始 bytes 重建精确数值。哈希不存在或模式不匹配返回 NOT_FOUND；证据不一致返回 ACQUISITION_INTEGRITY_FAILED，保持原始记录不变。`integrity_checked=true` 只说明保存证据通过一致性检查，不证明供应商账户权限、行情覆盖或收盘语义合格，资格仍为 UNVERIFIED。

检查结果中的 `quality` 按已保存行情页的实际采集时间对照交易日历，列出缺失已收盘交易日、非交易日日线、收盘前采集的日线、非交易日拆股及尚未到期交易日；半日市与 DST 使用实际日历收盘时间。不会补价格或将后来的检查时间当作原始采集时间。当前日历为 XNYS 代理并显式标记 NVDA 资格待核验；CHECKS_PASSED 只代表这些检查无异常，不代表覆盖、许可或供应商资格获批。

命令失败或中断后，可按 attempt_id 查询审计状态；仍只需要数据库配置，不需要供应商凭证：

```sh
uv run python -m yta_alpha.inspect_acquisition \
  --attempt-id <采集输出的attempt_id> --data-mode LIVE_RESEARCH
```

返回 COMPLETED 前会核验关联原始证据及范围；FAILED 只返回固定错误码；UNRESOLVED 表示只有开始事件，不能据此认定进程仍在运行或已失败，也不会触发重试。跨模式或不存在的 ID 返回 NOT_FOUND；重复/非法审计事件或缺失成功证据会返回 ACQUISITION_ATTEMPT_INTEGRITY_FAILED。`--attempt-id` 与 `--hash` 互斥，修订对比只用于 `--hash`。

复查供应商修订时，可同时传入同一日期范围、同一数据模式的旧采集哈希：

```sh
uv run python -m yta_alpha.inspect_acquisition \
  --hash <新采集哈希> --compare-hash <旧采集哈希> --data-mode LIVE_RESEARCH
```

`comparison` 保留两份证据哈希，按美东日期列出日线变化，按供应商事件 ID 列出拆股变化，新增/缺失一侧表示为 null。价格、成交量和股数比例以 Decimal 字符串输出；仅 JSON 格式、小数位写法或重新采集时间变化不算数值修订，原始页哈希变化单独报告。不同范围/模式返回 ACQUISITION_SCOPE_MISMATCH（CLI 跨模式读取按 NOT_FOUND 处理）。这是一份证据差异报告，缺失条目不等于已确认市场事实被撤销，不批准供应商资格、不修改冻结价格或触发重结算。

## 固定版本评测报告

Alpha 提供不可变报告 CLI，使用 `.env` 配置的数据模式与受限数据库账号。owner 是已有用户 UUID（登录后的 `/api/yta/v1/me` 可查看），不是邮箱。下面是合成回放日期示例：

```sh
uv run python -m yta_alpha.evaluation \
  --owner <已有用户UUID> \
  --base-from 2024-11-22 --base-to 2024-11-22 \
  --cutoff 2024-12-03T00:00:00Z
```

报告纳入该用户、数据模式、模型/协议和日期范围内的全部预测；保存所用 outcome ID/版本、结果与哈希。后续修订不会改变旧报告。同样内容重跑返回原 report_id；待处理/失败/不可评分分别计数，无有效样本的指标为 null。CLI 属于可信本地运维入口，不是公开用户鉴权接口。登录后可从预测记录进入「基线评测」页面，选择基准日区间及 UTC 事实截止时间，保存和查看报告。

## 本地容器验证

多阶段 Dockerfile 位于 `infra/docker/`：Node 镜像分别生成 BFF 生产依赖包和 Web standalone 产物；Alpha 镜像也供 jobs 使用。基础镜像按 digest 固定，pnpm/uv 使用锁文件；构建上下文排除 `.env`、本地数据和测试产物。Python 基础镜像使用 [Docker 在 ECR Public 发布的镜像](https://gallery.ecr.aws/docker/)。

先按前文启动本地 PostgreSQL、安装依赖并运行 `scripts/bootstrap_local.py`，使 `yta_test` 和受限应用账号就绪，再执行：

```sh
docker compose -f infra/compose/compose.app-local.yaml build
docker compose -f infra/compose/compose.app-local.yaml up -d --wait --wait-timeout 120
uv run python scripts/check_container_stack.py
docker compose -f infra/compose/compose.app-local.yaml down
```

此验证栈使用已有 `yta-dev_default` 数据库网络及专用 `yta_test`，只在宿主机 `127.0.0.1:3300` 发布 Web。测试账户与日常本地 `yta` 用户不同。BFF、Alpha 不发布宿主端口；Web 不持有数据库/Alpha 凭证，也不加入数据库网络。容器采用非 root、只读根文件系统、临时 `/tmp`、禁用提权、丢弃 capabilities 和有界日志轮转。服务间网络仍允许必要的外连，尚未配置生产出口策略。

报告功能的容器主闭环验证：完成上述启动与 `scripts/check_container_stack.py` 检查后，运行 `pnpm exec playwright test --config playwright.container.config.ts --grep 'authenticated replay:'`。该配置仅连接本机 3300 端口及专用 `yta_test` 数据库，不启动宿主应用服务；测试创建独立合成用户，验证报告证据展示/恢复、显式确认冻结、结算及手机布局。最新证据见 `infra/docker/report-container-observation.json`，不代表真实行情或 staging 验收。

默认不启动持续结算 Worker；验证脚本只运行 `jobs --queue-only`。需要持续回放时，可显式使用 `docker compose -f infra/compose/compose.app-local.yaml --profile worker up -d jobs`；它处理测试库中当前模式的全部用户，逻辑时间固定，进度文件保存在容器 `/tmp/worker.json`。不要把此 Compose 的测试凭证和 REPLAY 配置用于 staging/production。

本地进程的 BFF 仍默认绑定 `127.0.0.1`，只有显式 `YTA_BFF_HOST=0.0.0.0` 才绑定容器网络。Web 仅在构建时 `YTA_STANDALONE=1` 生成 standalone；容器构建参数 `YTA_BFF_ORIGIN` 默认 `http://yta-bff:3100`，该代理目的地址固化在构建产物中，修改后需重新构建。应用运行镜像不包含迁移 SQL 或数据库 owner 凭证，启动不自动迁移；迁移由下述独立镜像和 owner 执行。

`check_container_stack.py` 检查非 root/只读/端口边界、Web 凭证缺失及后端 DNS 隔离、代理 readiness、匿名请求 401、登录页与静态资源及 Worker 只读查询。GitHub Actions 的 `container-smoke` 任务执行相同流程；远端 CI、amd64 运行、HTTPS 反向代理、真实 Hermes/Provider、生产迁移和回滚仍须独立验收。

镜像内配额验证使用独立测试用户：以 `YTA_RESEARCH_RUNS_PER_24H=2 docker compose -f infra/compose/compose.app-local.yaml up -d --wait` 启动，再运行 `docker compose -f infra/compose/compose.app-local.yaml exec -T yta-bff node --input-type=module < scripts/check_container_quota.mjs`。脚本只允许 test / REPLAY / `_test` 数据库且配额恰好为 2，验证实际认证请求、新账号限流记录、数据库就绪、第三次拒绝、同键重放、持久化行数及镜像内 OpenAPI；最后停止自身接受的 Run，保留合成事实。CI 容器任务已加入该步骤。最近本机镜像摘要与证据位于 `infra/docker/app-local-observation.json`，不代表云端部署已通过。

### 独立数据库迁移镜像

```sh
docker build -f infra/docker/alpha.Dockerfile --target migrator -t yta-migrator:local .
uv run python scripts/check_migration_container.py
docker build -f infra/docker/alpha.Dockerfile --target runtime -t yta-alpha:local .
uv run python scripts/check_acquisition_container.py
```

验证脚本仅连接前述本地 Compose PostgreSQL，创建随机 `yta_migrate_<uuid>_test` 空库，测试缺少凭证/应用账号拒绝、owner 首次应用全部迁移及重复执行不变，然后删除本次创建的空测试库。它不会重建 `yta` 或 `yta_test`，不接受远端数据库参数。CI 容器任务包含这一验证步骤以及生产包采集/检查演练；Alpha Compose 构建显式指定 `target: runtime`，避免 Dockerfile 的其他 stage 被当作 API 镜像。

正式执行迁移时，向迁移容器单独注入 `YTA_MIGRATION_DATABASE_URL`，连接到已选定的目标数据库，并在启动新应用版本前等待迁移退出 0。不要将 owner 凭证注入 Web/BFF/Alpha/Worker，也不要把 URL 放进 Dockerfile 或构建参数。迁移镜像使用非 root OS 用户；数据库 owner 与容器 OS 用户是不同权限边界。账号创建、CONNECT/USAGE、运行账号 SELECT/INSERT/序列权限等仍由独立数据库初始化流程负责，不因迁移镜像成功而视为已配置。

容器检查在随机命名的专用 `_test` 数据库中运行并自动清理：迁移检查覆盖全新安装、从上一迁移版本升级、原有审计证据保留、重复执行和 owner/runtime 隔离；采集检查使用镜像内已安装包、模拟 HTTP 和真实 PostgreSQL，验证非 root/只读容器中的非空日线采集、另一个容器中的证据检查与质量报告，以及结构化日线的高精度数值、来源时间、原键重试和模式隔离。重复读取后仍只有一条日线和一份采集审计，无合格快照。不会调用真实供应商或修改正常应用库。

迁移在一个事务及 PostgreSQL advisory lock 内执行，校验已应用文件哈希，拒绝缺失/空目录、空脚本、漏掉已应用历史的旧产物及校验和变化；失败整体回滚。CLI 错误仅输出 `MIGRATION_FAILED` 和非零退出码，不回显连接字符串或数据库异常。原始库结构未改变时可重复执行。代码回滚不能借助旧迁移产物降级数据库；生产发布仍需兼容性检查、备份与独立的回滚方案。

### 请求日志与关联排查

BFF 向 stdout 输出 JSON：`bff.request` 包含服务端生成的 request_id、固定路由模板、HTTP 状态、耗时和 `completed`；响应的 `X-Request-ID` 与错误正文使用同一个 ID。调用方提供的 X-Request-ID 不被采纳。SSE 连接提前断开会记录 `completed=false`，不能仅看到 HTTP 200 就认定流已正常结束。

`bff.alpha_request` 用同一 BFF request_id 关联 Alpha 响应的 `upstream_request_id`，记录上游状态和 success/failed/canceled。Alpha ID 缺失或格式异常时为 null，不猜测关联；定时任务没有 HTTP 上下文时 request_id 也为 null。`bff.research_admitted` 将 HTTP request_id 映射到持久化研究 run_id，供后续查询事件/失败记录；它表示请求已被接收，不表示研究完成，其 duration_ms=0 不用于耗时统计。

新增日志不包含请求/响应正文、Cookie、Authorization、CSRF、幂等键、用户身份、URL 查询值或具体预测 ID。未知路径统一为 UNMATCHED；研究 run_id 仅在明确的关联事件中输出。日志写入失败不改变 API/事实事务结果。现有 Compose 提供有界日志轮转；集中采集、OTel 跨进程 trace、工具跨度、告警和生产留存策略仍待部署。

## 到期结算 Worker

开发回放使用明确的逻辑时点运行一批任务；`--owner` 可限制到某个已有用户，省略时处理当前数据模式的所有用户：

```sh
uv run python -m yta_alpha.jobs --replay-at 2024-12-03T00:00:00Z
```

仅扫描到期、没有 outcome、且重试时间已到的预测。失败和待数据任务保留重试时间；再次运行时使用更晚的回放时间即可继续。任务记录与预测事实均追加保存，不覆盖旧结果。

真实环境可用 `uv run python -m yta_alpha.jobs --watch --interval 60` 持续轮询；该环境禁止 `--replay-at`，使用服务器时间。目前真实供应商尚未接通，因此会明确记录 DATA_UNAVAILABLE 并退避，不会拿 fixture 结算。生产进程托管与供应商验收完成前，不视为上线就绪。

### Worker 进度检查与停止

持续运行时可增加 `--heartbeat-file .local/settlement-worker.json`，父目录需预先存在。每个进程使用独立文件；监控以同一 OS 用户读取，文件权限为 0600。回放示例：

```sh
uv run python -m yta_alpha.jobs --watch --interval 60 --replay-at 2024-12-03T00:00:00Z --heartbeat-file .local/settlement-worker.json
uv run python -m yta_alpha.worker_health --file .local/settlement-worker.json --expect-mode REPLAY --max-age 180
```

第二条命令在另一个终端/监控进程执行；就绪返回 0，否则返回 1。真实环境使用 `--expect-mode LIVE_RESEARCH`。现有真实供应商未接通，因此即使 Worker 正在轮询，探针仍报告 `provider_unavailable`。该探针独立于 BFF readiness，尚未接到生产告警系统。

进度文件只在批次完成后刷新，时间使用真实 UTC 墙上时钟，不使用回放逻辑时间。缺失/损坏/过期/未来时间、模式不符、启动中、停止或失败均不就绪。强制终止或卡住后的最后一次成功记录会在 `--max-age` 后失效；这不是即时的进程存活判断。阈值应大于轮询间隔加预期最长批次耗时，超时应调查原因。就绪仅表示近期完成一次扫描且供应商实现可用，不表示所有预测均已评分。

SIGTERM/SIGINT 会结束轮询等待；正在处理的批次完成后退出，不中途拆开事实事务。退出写 `stopped`，批次异常写 `failed` 并以状态码 1 退出；stderr 只给出 `WORKER_FAILED`。单批运行结束也写 `stopped`。阻塞中的供应商调用仍需其自身超时和进程托管的终止期限，进度探针不能代替取消调用。REPLAY 的 `--replay-at` 保持固定，推进重试逻辑时间仍需以更晚的时点重新运行。

### 查看结算积压

每轮 Worker JSON 日志增加 `queue` 汇总。也可单独读取，不处理任务、不调用供应商、不写进度文件：

```sh
uv run python -m yta_alpha.jobs --queue-only --replay-at 2024-12-03T00:00:00Z
```

真实模式省略 `--replay-at`。`--owner` 可限制到一个用户；省略时汇总当前数据模式的所有用户，属于可信本地运维入口，不是公开用户 API。输出不含用户或预测 ID。

| 字段 | 含义 |
|---|---|
| `due_count` | 已到期、尚无 outcome 的全部预测数量，不受批次 limit 截断 |
| `eligible_count` / `backoff_count` | 已到重试时间 / 仍在退避的数量；eligible 不代表执行租约一定可取得 |
| `waiting_data_count` / `failed_count` / `unattempted_count` | 按最新 Worker attempt 分类；不是历史尝试次数 |
| `oldest_due_at` / `oldest_overdue_seconds` | 最早未结算目标收盘时刻 / 距队列时钟的整秒数；无积压为 null |
| `data_mode` / `scope` / `queue_time` | 当前模式、用户范围和队列时间，REPLAY 明确使用指定逻辑时间 |

这些指标来自单次数据库查询快照，已评分及不可评分 outcome 都退出该队列；不做历史状态回溯，也不代表模型成绩。`due_count = eligible_count + backoff_count`；等待/失败分类与退避分类会交叉，不能再次相加。进度新鲜度仍使用墙上时钟。监控可依据持续积压、失败及逾期增长配置告警，但目前尚未部署采集和告警规则。

## 不可评分证据

可信的内部 Provider 可以返回 `UnscorableEvidence`，由 Alpha 的 `record_unscorable` 追加结果。允许原因仅为目标日停牌无收盘价、退市无有效价格和无法可靠解释的公司行动；普通行情缺失继续等待数据，不生成不可评分结论。

证据携带来源标识、文档哈希、原文说明及发布时间/可用时间/采集时间。改判已有结果须引用最新结算版本，重复证据幂等。预测详情展示证据而非虚构的零收益；评测单列不可评分数量。这是内部可信采集接口，没有允许浏览器或模型提交判定的公开路由，真实供应商资格核验仍未完成。

## 基准价格修订

供应商更正基准交易日收盘价时，可信内部调用可将单基准日 `MarketSnapshot` 作为 `base_revision` 传给 `ResearchStore.settle_prediction`，并用 `supersedes_version` 引用当前最新结算版本。来源须匹配原供应商，快照保留新行情版本、来源和可获知时间。

Alpha 以修订价格追加新的 `settlement-price-v2` 结果，保留冻结原价及价差，不改写预测、旧 outcome 或旧评测报告。预测详情可同时查看两个版本。真实供应商的修订采集和自动扫描仍待接入，此能力没有允许浏览器直接提交价格的公开端点。

## 本地备份与空库恢复演练

在本地 Compose PostgreSQL 运行时执行：

```sh
uv run python scripts/backup_local.py --source yta --drill
```

备份文件和清单保存在 `.local/backups/`（目录 0700、文件 0600）。`--drill` 创建新的 `yta_restore_<随机ID>_test` 空库，恢复并校验各表内容哈希、领域快照/模型哈希、冻结预测重算、结算重算、序列和只追加权限/触发器；不会覆盖已有数据库。恢复库保留供检查，命令输出其名称。

已有可信备份可独立恢复，不要求源数据库仍存在：

```sh
uv run python scripts/backup_local.py --restore-manifest .local/backups/<备份名称>.json
```

对包含原始行情证据的 `yta_test` 备份，可使用恢复输出中的新库名继续验证候选证据读取；演练期间保持测试源库静止，否则当前源库与备份快照不同会导致比较失败：

```sh
uv run python scripts/check_acquisition_restore.py \
  --restored <restore_drill输出的yta_restore_UUID_test>
```

此检查只支持本地测试源库和指定格式的恢复库，不写入任一数据库。它比较两张原始证据表及 market_bars 的全部行与字节，再通过 runtime 角色读取每份证据和结构化日线：完整记录必须精确重建，测试故意保留的异常记录必须继续被拒绝。原始证据与日线投影分别计数；缺少历史投影也必须保持原有拒绝行为，检查不会自动回填。需要源库和恢复库都包含迁移 013。不将备份恢复通过视为供应商资格批准。恢复库和 0600 备份文件保留用于复查。

脚本会先检查配对 `.dump` 的 SHA-256，损坏时拒绝创建恢复库。只恢复自己生成的可信备份。数据库角色及密码不包含在 dump 中；此本地演练使用初始化脚本建立的 migration owner 和 runtime role，恢复后重新授予 SELECT/INSERT。

每日 03:00 的配置示例位于 `infra/cron/yta-local-backup.example`；填写仓库与 uv 绝对路径后才能安装，目前没有修改系统 crontab。文件包含研究内容和账户哈希，不能作为公开 CI artifact 上传。当前备份未加密、未异地复制、未自动清理，保留策略、生产角色重建、调度告警、RPO/RTO 和灾备演练仍待部署阶段完成。

备份数据与校验清单使用同一导出快照，避免并发写入造成误报；实现依据 PostgreSQL 17 的 [快照同步](https://www.postgresql.org/docs/17/functions-admin.html#FUNCTIONS-SNAPSHOT-SYNCHRONIZATION)、[pg_dump](https://www.postgresql.org/docs/17/app-pgdump.html) 和 [pg_restore](https://www.postgresql.org/docs/17/app-pgrestore.html) 文档。

## 存活与就绪监测

`/api/yta/v1/health` 只表示 BFF 进程存活。`/api/yta/v1/ready` 检查数据库、权限、Alpha 与研究模式，全部可用才返回 200，否则返回 503。Alpha 对应的 `/internal/v1/ready` 需要内部服务 token，浏览器无需也不能获取该 token。

```sh
uv run python scripts/check_health.py --expect-mode REPLAY
```

脚本经 Web 转发检查 BFF，不就绪时退出码 1，可供外部监控运行器接入。部署真实环境时用 `--expect-mode LIVE_RESEARCH --url https://<站点>/api/yta/v1/ready`；目前真实行情/Hermes 未验证，因此真实模式不会通过 readiness。告警发送和定时监测尚未部署。

## 评测报告 API

登录后可通过 BFF `POST /api/yta/v1/evaluation-reports` 生成并保存不可变报告，请求需携带会话 Cookie、同源 Origin 和当前会话的 `X-CSRF-Token`。请求体仅包含 `base_from`、`base_to`（交易日期，YYYY-MM-DD）和带时区的 `cutoff`。例如：

```json
{"base_from":"2024-11-22","base_to":"2024-11-22","cutoff":"2024-12-03T00:00:00Z"}
```

所有者取自登录会话，数据模式取自服务配置；不能提交 owner、模式或任意预测 ID 子集。报告包含该区间内截止时点已冻结的全部匹配预测、固定 outcome 版本和 Decimal 字符串指标；没有已评分样本时指标为 null。同样参数和相同事实生成相同报告，事实修订后可能生成新报告。网络重试时保留原请求参数。

`GET /api/yta/v1/evaluation-reports/{report_id}` 读取保存版本，需要登录；不属于当前用户或当前数据模式的报告返回 404。计算、存储及审计全部由 Alpha 执行。浏览器 `/evaluations` 提供生成、分页列表及报告详情，披露全部样本、更正数、按基准日分组数量及未评分状态；旧版报告未包含更正数时明确说明。报告保存后固定结算版本；预测详情可能已有更新版本。Web 会校验报告版本、当前数据模式、样本/状态/更正数量与结算版本映射；不一致的响应显示错误并支持重试。数值计算仍由 Alpha 完成，前端校验不替代事实审计。完整评测任务仍在进行中。


`GET /api/yta/v1/evaluation-reports?limit=25` 返回当前用户、当前模式的报告摘要，按保存时间倒序排列；响应为 `items` 和 `next_cursor`。后续页原样传入 `cursor=<next_cursor>`，游标为 null 表示已到末页。每页默认 25 条、最多 100 条；创建时间相同的报告用 ID 排序。翻页期间新增的最新报告需刷新首页查看，已经读取的记录不会因插入新报告而重复。

摘要不包含完整 `manifest` 和按基准日分组的 `base_session_counts`，这些内容保留在详情接口。摘要中的 `created_at` 是报告保存时间，`cutoff` 才是评测事实截止时刻。游标必须属于当前用户与模式，否则返回 404；列表请求不接受客户端 owner 或模式参数。

## 研究会话分页

研究侧栏默认载入最新 25 个会话，通过“加载更早会话”继续读取历史；请求失败后可点击同一按钮重试，已有记录保留。`GET /api/yta/v1/sessions` 接受可选 `cursor`、`limit`（1–100），返回 `items` 与 `next_cursor`。列表仅包含当前登录用户、当前服务数据模式的会话，游标同样按归属与模式校验。新建会话会出现在顶部；历史消息仍使用独立的消息分页回放接口。

BFF 调用 Alpha 时对响应体设置 4,000,000 字节上限（解压后计数），超过即停止读取；15 秒截止时间覆盖连接和响应体读取。网络失败不会自动重发写请求，调用者应保留原参数及幂等键核对结果。大型报告详情超过上限时会明确报错，不能把失败当作报告不存在。

## 预测历史分页与筛选

预测记录页每次加载 25 条，可点击“加载更早记录”继续，失败时保留已加载记录并支持重试。状态筛选在 Alpha 查询全部当前用户/当前模式的历史，切换状态会从首页重新加载。

`GET /api/yta/v1/predictions` 支持 `cursor`、`limit`（1–100）和可选 `status`（FROZEN、SETTLED、UNSCORABLE、WAITING_DATA、SETTLEMENT_FAILED），响应包含 `items`、`next_cursor`。游标使用原样返回的预测 ID，跨用户或跨模式游标返回 404。排序为冻结时间、ID 倒序，时间比较保留数据库精度。每页内使用一致读取快照；跨页期间新结算/新冻结可能改变筛选集合，刷新页面可查看最新状态。

研究会话和 Run 的直接访问、消息重试、SSE、取消及后台恢复均绑定 BFF 当前 `YTA_DATA_MODE`。即使登录用户相同，另一模式的会话/Run ID 也不能在当前环境读取或操作；必须在对应隔离环境中访问，不能通过请求参数切换模式。

## 指标计算口径

Alpha 指标响应现为 `indicators-v3`，保留原有序列并增加：

- `RETURN_1D`：拆股可比收盘价的简单日收益，首个样本为 null。
- `VOLUME_CHANGE_1D`：将成交股数换算到最后交易日股数口径后计算日环比；首个样本及前日成交量为零时为 null，当日为零而前日非零时为 -1。
- `HIST_VOL20`：最近 20 个简单日收益的样本标准差（分母 19）乘以 √252，需 21 个连续有效交易日收盘价；前 20 项为 null。252 是该版本固定的年化约定，不是对某一年的实际交易日计数。

全部生产计算使用 Decimal 50 位精度，最后按 HALF_EVEN 输出 12 位小数字符串；只使用 as_of 前可获知的拆股证据，缺失交易日直接拒绝，不填补价格。这些是研究描述指标，没有改变 momentum20-v1 的输入与预测公式，当前图表仍展示原有指标组合。


图表仅请求 `/stocks/NVDA/indicators`，同一响应包含拆股可比 OHLC、成交股数及指标，避免跨请求混用行情版本。`share_basis_session` 为共同股数基准日，`data_snapshot_hash` 引用原始快照；每根 K 线记录所用 `split_factor`。价格除以累计拆股因子、成交量乘以该因子，由 Alpha 用 Decimal 计算；反向拆股后的可比成交量允许小数。原始市场快照和冻结价格不改写。

Web 使用共享图表契约校验日期对应、长度、价格口径和必需序列；不一致时显示错误并允许重新加载，不将原始价格与调整后指标混画。零值输出普通十进制字符串，不使用 `0E-12` 表示法。

## NVDA 概览

登录后通过顶部“NVDA 概览”或首页 `/` 查看独立概览，包含同一快照的行情与指标、来源时间、调整口径、研究范围和工作台入口。行情不可用时显示错误并允许重新加载，不显示替代行情。当前本地运行仍为明确标注的合成 REPLAY 演练。

## 预测更正 API

`POST /api/yta/v1/predictions/{原预测ID}/corrections` 接受新的 `draft_id` 和 `confirmed: true`，需要登录 Cookie、同源 Origin、会话 CSRF 与稳定的 `Idempotency-Key`。客户端不能覆盖预测值。原预测和新草稿须同属当前用户/模式、证券、跨度、基准日、目标日与协议，且都在原研究窗口内；不能用下一交易日的新草稿追改旧判断。

更正生成新的冻结预测，并返回 `supersedes_prediction_id`；原预测、旧结算和旧报告不变。同一草稿不能先普通冻结再改成更正，或换一个原预测引用。成功后的精确重试即使超过窗口仍返回原结果，不能换键或换草稿掩盖未知执行结果。

新生成报告使用 `price-evaluation-v2`，全部原预测与更正均纳入 cohort，披露 `correction_count` 并保留每条引用；历史 v1 报告保持原样。从原预测详情点击“研究并更正此预测”，生成新草稿后点击“检查并更正预测”，核对原判断与新草稿并明确确认。详情保留返回原预测的链接。

可选链路追踪：BFF 和 Alpha 均设置 `YTA_OTLP_TRACES_URL=http://collector:4318/v1/traces` 后启用 OTLP/HTTP 导出，未配置则关闭。地址只接受 HTTP(S) `/v1/traces`，不接受 URL 凭证、查询或 fragment。手动跨度记录固定路由、状态和服务生成的 request_id，不记录请求正文、Cookie、Authorization 或查询值。公开 BFF 创建独立根跨度；Alpha 只接受携带有效内部服务凭证的 traceparent，不传播 baggage/tracestate。导出采用有界队列和超时。收集端部署、访问控制、保留策略和告警尚待验收。

`uv run python scripts/check_tracing.py` 使用随机回环端口启动真实 Alpha 与 BFF 传输探针，并由本地 OTLP 接收端核对三个跨度的父子关系和敏感字段缺失；需要已安装 Python/Node 依赖，不需要数据库或外部服务。该检查验证 BFF→Alpha，不代表 Hermes/工具跨度或生产追踪验收。

采集端故障回归可分别执行 `uv run python scripts/check_tracing.py --collector reject`、`--collector stall`、`--collector refused`。这三种模式验证健康请求成功且耗时小于 3 秒、探针进程正常退出，并检查 Alpha 在 10 秒退出期限内结束；503 和延迟响应场景还确认两种语言的 exporter 都实际访问了故障采集端。BFF 关闭时追踪刷新失败仅输出固定的 `bff.tracing_flush_failed` 诊断，不将其变成业务服务退出错误。以上是本地健康请求/传输故障验证，不代替负载测试或生产可用性验收。

金融工具进程使用独立的 `YTA_TOOLS_OTLP_TRACES_URL` 和 `YTA_TOOLS_ENV` 配置。启用后，每个工具调用创建服务生成 request_id 的根跨度，并将 Alpha 客户端子跨度的 traceparent 传给 Alpha；Alpha 返回的 request_id 记录在子跨度上。只记录 market_snapshot / indicators 或固定 UNKNOWN_TOOL、允许的路由和错误码，不采集 MCP 参数、任意工具名、客户端 metadata、凭证或响应正文。stdio 与私有 HTTP 的 lifespan 退出时刷新采集队列；不会向 stdio 协议通道打印追踪数据。当前工具根跨度独立于 Hermes Run，Hermes→工具的完整关联仍待协议验证与接入；容器启用需要显式传入这些变量及可达的采集端，现有演练镜像未因此自动更新。

固定 Hermes 镜像的 Profile 上下文读取探针（同时验证仅金融工具时 Skill 索引/自动加载为空，并以启用 Skill 工具作为正例；这不是 OS 文件隔离或完整 Gateway 验收）：

```sh
docker run --rm -i --network none --read-only --user 10000:10000 \
  --tmpfs /tmp:rw,nosuid,nodev,size=256m -e YTA_CONTEXT_SCOPE_PROBE=1 \
  --entrypoint /opt/hermes/.venv/bin/python \
  sha256:67f82b05c5c083f2795cff9c361b4f5c464116fcc4301671709376e6f64e3226 \
  - < scripts/check_hermes_context_scope.py
```

探针在临时目录写两个合成 Profile，运行固定版本真实 scope、SOUL/技能索引加载及缓存函数，验证 8 线程、64 次交错读取、嵌套作用域恢复及 2 次显式 home 的无上下文工作线程读取。内存只核对目录解析，不执行内存写入。正向读取也确认同一 UID 可以直接访问另一个 Profile 文件，因此上下文隔离不是操作系统文件沙箱；仍须依赖禁用文件/终端工具、私有挂载和单 Profile 配置。该检查不调用模型或 Gateway Runs，不提升完整接入门槛状态；证据见 `runtime-observation.json` 的 `locked_context_scope_probe`。

本地监控可在应用 Compose 上叠加 `infra/compose/compose.monitoring-local.yaml`：

```sh
docker compose -f infra/compose/compose.app-local.yaml -f infra/compose/compose.monitoring-local.yaml up -d
```

Prometheus 面板仅发布到 `http://127.0.0.1:39090`；Blackbox 不发布端口，不挂载 Docker socket，也不取得服务/数据库凭证。探测 BFF 和 Web 的公开 readiness，要求 HTTP 200 且正文 status=ready，并拒绝重定向。BFF readiness 本身检查数据库权限、Alpha 和研究模式，因此失败能被汇总发现，但这不是 Hermes 独立健康探针。当前真实研究仍未验收，不能据此宣称 LIVE_RESEARCH 就绪。

规则包含持续 2 分钟未就绪、持续 1 分钟采集失败和固定目标指标缺失；监控数据缺失不会当作健康。指标保留限制为 7 天/512MB，容器 JSON 日志轮转 10MB×3；开发 PostgreSQL 也补上同样轮转配置，须下次重建该容器才生效，本次未重启数据库。尚未配置 Alertmanager、外部通知和生产阈值，告警仅在 Prometheus 内显示。[Prometheus 告警规则文档](https://prometheus.io/docs/prometheus/latest/configuration/alerting_rules/)说明了告警状态与通知的区别。

`uv run python scripts/check_monitoring.py` 需要本地 `yta-alpha:local`（仅作为合成 HTTP 服务的 Python 运行时）及配置中固定摘要的两份监控镜像。它使用真实 promtool 验证配置、等待阈值、恢复、采集失败、指标缺失和健康反例，再启动临时容器验证正常/错误正文/503/重定向四类 HTTP 探测，最后清理容器和网络；不改变现有业务服务，不发送通知。配置语法依据 [Blackbox 官方配置](https://github.com/prometheus/blackbox_exporter/blob/master/CONFIGURATION.md)，规则测试采用 [Prometheus 官方测试格式](https://prometheus.io/docs/prometheus/latest/configuration/unit_testing_rules/)。镜像使用固定 manifest 摘要，本机仅验证 arm64；CI/amd64、真实持续告警、通知与 staging 故障演练仍待验收。

`uv run python scripts/check_monitoring_lifecycle.py` 补充实际 Prometheus 告警生命周期演练，通常约 3 分钟。它在随机独立网络中运行固定监控镜像及两个合成 readiness 目标，挂载原始配置和原始 2 分钟规则，不缩短阈值；断言两目标健康、故障进入 pending、持续故障进入 firing、恢复后告警消失。输出各阶段耗时，finally 清理容器/网络；只操作自身合成目标，不停止正常 BFF/Alpha/PostgreSQL，不发送通知。此结果不能替代真实依赖故障或 staging 演练。

实际 Alpha 中断演练：先启动 `compose.app-local.yaml` 与 `compose.monitoring-local.yaml` 的测试栈，再运行 `uv run python scripts/check_dependency_outage.py`。脚本核对 Compose 项目身份及 BFF 的 test/REPLAY/`_test` 配置，只停止该项目的 alpha-engine，验证数据库仍 ready、BFF liveness=200/readiness=503，等待两个真实 readiness 告警 firing，然后在 finally 中启动原 Alpha 容器并等待就绪及告警恢复。通常约 3 分钟；不会停止 PostgreSQL、正常宿主服务或发送通知。不要把本地 REPLAY 演练解释为真实行情、Hermes 或 staging 可用性验收。

冻结事务故障回归已纳入真实 PostgreSQL 套件：`uv run pytest tests/integration/test_research_store.py -k 'termination_before_commit or lost_commit_ack' --require-postgres`。使用现有专用 `_test` 测试配置，提交前故障只终止本次测试的 runtime backend；提交后故障通过回环 TCP relay 丢弃 PostgreSQL 的真实 COMMIT 回包，不使用模拟数据库响应。验证前者回滚预测/审计/幂等记录，后者虽然客户端失败，但原键在草稿过期后仍返回原预测且不重复事实。该 relay 只允许本机测试库、关闭加密以解析协议，不用于部署流量；没有停止共享 PostgreSQL 或修改其权限。

结算也覆盖真实提交边界：`uv run pytest tests/integration/test_research_store.py -k 'settlement_termination or reconciles_committed_settlement' --require-postgres`。提交前终止只回滚本次结算，原预测不变；COMMIT 回包丢失后，Worker 通过独立连接核对已提交结果，将 attempt 记录为 SETTLED，不生成失败事件或重复版本。这里保持 Worker 的 lease/核对连接可用，不等同于整个数据库中断。

迁移 `012_symbols.sql` 增加不可变证券参考历史，版本记录保留状态、有效日期区间、更名后代码、来源和记录时间；不推断未提供的历史日期。NVDA 初始行仅标识 MVP 范围，status=UNVERIFIED、有效日期为空，不代表已验收的上市历史或行情数据。Alpha readiness 现在要求此表及正确运行权限/不可变触发器；已有环境需由迁移 owner 升级并授予 runtime SELECT/INSERT，运行角色仍不能 UPDATE/DELETE/TRUNCATE。

服务凭证可访问 `GET /internal/v1/symbols/{symbol}`。接口仅接受 MVP NVDA，允许 ASCII 大小写和首尾空白标准化，拒绝 Unicode 相似字符、内部空白和路径/查询注入；其他合法代码返回 UNSUPPORTED_SCOPE。金融工具凭证不能访问此接口。返回完整参考版本，不提供“按历史时刻已获知”的声明，也不自动开放新证券或 LIVE_RESEARCH。冻结快照的 symbol 仍要求规范值 NVDA。实际迁移镜像验证见 `infra/docker/symbol-registry-migration-observation.json`。

BFF 的 `/api/yta/v1/stocks/{symbol}/bars` 与 `/indicators` 请求入口也使用同一组标准化样例：ASCII 大小写和首尾空白可规范到 NVDA，非法拼写及其他证券在访问 Alpha 前拒绝。共享样例位于 `packages/contracts/fixtures/symbol-inputs.json`，由 TS 和 Python 两侧验证。请求拼写的容忍不改变不可变金融 DTO：forecast/snapshot 的 symbol 仍必须是 canonical `NVDA`。


草稿历史行情证据由私有 Alpha `GET /internal/v1/prediction-drafts/{draft_id}/evidence` 提供，需要服务凭证及由已认证 BFF 会话派生的 `X-Yta-Owner-Id`。响应使用现有 ToolMarketSnapshot 契约，读取草稿创建时的不可变快照；不会重新请求当前 Provider，也不因草稿过期而丢失历史证据。归属不符、跨模式或不存在均返回 404；保存内容的结构、哈希或截点不一致返回 EVIDENCE_INVALID（503）。BFF 提供已登录的 `GET /api/yta/v1/prediction-drafts/{id}/evidence`，归属只取登录会话，并验证严格响应契约和当前数据模式；报告面板提供“查看草稿历史行情”，按需加载并核对报告哈希/模式，分页查看原始日线、版本与可用/采集时间及拆股记录；加载失败或不一致时不展示数据，可手动重试。Alpha 私有端点不能直接暴露给浏览器或金融工具。


迁移 `014_evidence_items.sql` 为保存的 MarketSnapshot 增加不可变内容索引，主键为已有规范化内容哈希，记录来源、提供方、数据模式、信息截点及输入证据最晚可用时间。Alpha 保存快照时在同一事务写入索引，重复保存必须同时匹配原快照内容和索引元数据。此索引不代表真实供应商资格通过；迁移不会自动回填旧快照，已存在草稿请求的直接幂等返回也不会触发回填。运行角色需对新表授予 SELECT/INSERT，迁移仍使用独立 owner；正常应用库和旧镜像须按部署流程升级，不能仅凭本机测试库迁移宣称部署完成。


历史证据索引回填使用 `uv run python -m yta_alpha.backfill_evidence <content_hash> --mode REPLAY`，数据库取进程环境中的 `YTA_DATABASE_URL`（运行角色）。默认开启只读事务并返回 missing/already_indexed；核对后添加 `--apply` 才补写缺失索引和同事务 EVIDENCE_INDEX_BACKFILLED 审计。每次只处理一个明确哈希，模式必须显式指定；结构、哈希、元数据冲突会拒绝，不覆盖原记录，也不把回填当作行情资格认证。已有索引的历史读取会验证其元数据；旧记录没有索引仍可从原始快照校验后读取，用户读取不会触发回填。


证据功能最新容器/迁移/恢复实测见 `infra/docker/evidence-container-observation.json`。`uv run python scripts/check_evidence_restore.py` 会建立 UUID 命名的本机专用合成数据库，迁移后保存一条预测与证据，实际备份并恢复到新库，检查恢复后证据读取及冲突拒绝，最后删除本次创建的源库；私有备份和恢复库保留供复核。包含故意损坏事实的整套 yta_test 可能被域完整性检查拒绝，这不是备份恢复通过；不要删除负例或绕过验证来使其变绿。恢复检查对含 evidence_items 的备份新增索引与原始快照一致性验证，旧版本无此表的备份继续按原有域验证执行。


Alpha 私有 `GET /internal/v1/market/freshness` 返回 FRESH/STALE/UNAVAILABLE、data_mode、缓存键、TTL 和最后有效快照的来源/哈希/截点。状态接口返回 200 不代表存在可用行情；LIVE_RESEARCH 无已核验供应商时为 UNAVAILABLE，实际行情接口仍 503。当前每进程只缓存一个配置来源的 NVDA 快照，TTL 30 秒，以单调时钟控制刷新和失败重试；缓存键包含模式、来源、日历版本和原始快照版本。交易日收盘切换可在 TTL 内使旧快照变为 STALE，周末不会虚构新交易日。REPLAY 使用快照逻辑时钟，与真实模式隔离。缓存刷新失败保留旧证据元数据，但研究/指标/新草稿接口拒绝旧快照；未实现跨实例共享、后台刷新或真实 Provider 资格，BFF `GET /api/yta/v1/stocks/NVDA/freshness` 验证状态结构与环境模式后返回；概览页明确显示回放数据可用、行情已过期、行情不可用或状态未知。页面可见时每 30 秒检查、重新可见时检查，也可手动刷新；读取失败会清除旧可用提示。


完整测试数据集的恢复回归：`uv run python scripts/check_test_database_restore.py` 在新建的本机 UUID `_test` 库中运行 Python 全量真实 PG 套件，然后备份、恢复并核对域事实和供应商采集/日线投影；结束后只删除本轮源库，保留私有日志/备份与恢复库。故意损坏快照或索引的完整性反例使用各自临时数据库并在结束时清理，不污染共享的正向恢复数据。现存 yta_test 的历史坏测试事实不会被脚本修改或删除；其严格恢复检查仍可能拒绝。最新结果见 `infra/docker/test-suite-restore-observation.json`，并非远端 CI/staging 运行证据。

供应商 HTTP 故障专项：`uv run pytest services/alpha-engine/tests/test_provider_http_faults.py -q`。使用本机随机端口和合成凭证，覆盖限流、错误状态、断流、采集总时限、拆股失败及缓存 STALE/恢复，不调用真实账户或模型。成功返回仍为 UNVERIFIED；该测试不授予供应商资格，也不是 staging 演练。

Gateway 上下文隔离演练使用 `infra/compose/compose.hermes-context-probe.yaml`：设置 `YTA_FINANCE_ALPHA_IMAGE` 为当前本机 Alpha 镜像的不可变 ID 后启动；Gateway ready 后运行 `docker exec yta-hermes-context-probe-hermes-1 python /probe/probe_hermes_gateway_context.py probe --revision paired-env`。专用 internal 网络、无发布端口，模型为合成协议服务；两个 Profile 共8个 Run 校验实际系统消息中的 SOUL 标记及禁用 Skill 不加载。重复运行只查询已保存 Run，不重发未知请求；保留数据卷供重启核对。结束用同一 Compose 文件 `down` 清理容器/网络，保留卷。此证据不等同真实推理或 OS 文件隔离，不能解除 Hermes 接入门槛。


候选行情 Parquet 归档（TASK-046）：`uv run python -m yta_alpha.lake_cli export --root .local/market-lake --hash <acquisition-hash> --data-mode REPLAY` 使用既有 `YTA_ACQUIRE_DATABASE_URL` 运行角色配置，只读取并核对 PostgreSQL 原始证据与 market_bars。归档路径为 `<root>/<data_mode>/<hash>/symbol=NVDA/year=<year>/bars.parquet`；临时目录内完成文件、内容哈希与 manifest 校验后原子发布，同 hash 并发/重复执行只核对已有内容，损坏归档不自动覆盖。

`uv run python -m yta_alpha.lake_cli query --root .local/market-lake --hash <acquisition-hash> --data-mode REPLAY --symbol NVDA --year 2024` 不需要数据库或供应商凭证。查询先验证完整 manifest/文件/行内容，再通过 DuckDB 读取指定分区并核对结果；缺失年份返回空列表，不补行情缺口。保留 page/row 引用、source_page_hash、available_at/retrieved_at 和 published_at=null，所有结果明确 UNVERIFIED。哈希提供本地完整性检查，不是数据来源的数字签名；这些候选归档不进入 LIVE 研究或评分。

DuckDB 固定为 1.5.6，禁用扩展自动安装/加载，限定2线程/256MB内存且不自动溢写临时文件。OHLCV 使用 VARCHAR 十进制字符串而非浮点或定宽 DECIMAL，避免 PostgreSQL 原始高精度被截断；DuckDB 的 DECIMAL 上限和除法行为见[官方1.5数值类型文档](https://duckdb.org/docs/1.5/sql/data_types/numeric)。金融计算继续由 Alpha 的 Decimal 协议负责。当前仅本地 NVDA 候选归档，逐次核验全部文件适合现有规模；多证券、云对象存储、索引/大规模性能及真实供应商验收尚未完成。

备份、可信旧归档恢复、候选Parquet重建、事故对账及RTO/RPO记录见[恢复Runbook](docs/recovery-runbook.md)。`uv run python scripts/check_disaster_recovery.py` 实际创建独立合成源库，恢复并比较 baseline 配置、归属证据及重建Parquet的文件哈希；不执行服务切换。

BFF写入入口可通过服务端 `YTA_WRITE_ADMISSION=OPEN|PAUSED` 控制，默认OPEN。PAUSED时六类业务写入在鉴权/CSRF之后返回503 `WRITE_ADMISSION_PAUSED`，查询、登录登出和取消继续可用；ready明确PAUSED且503，health仍200。配置需更新所有实例并重启生效，不能停止已接收Run、在途请求或Jobs，也不是全库只读。包括原键POST在内均暂停，保留原身份以GET核对/恢复后原键重试；具体部署与排空限制见[恢复Runbook](docs/recovery-runbook.md)。

结算Jobs另有服务端 `YTA_JOBS_DISPATCH=OPEN|PAUSED`（默认OPEN）。PAUSED仅查询队列、输出PAUSED和空results，watch心跳明确paused而不伪装ready。SIGTERM/SIGINT阻止后续派发，已进入Provider/事务的工作继续完成核对，进程最终退出；未派发记录不新增attempt或取消状态。配置需重启生效，不控制其他进程/BFF/Alpha写入；详见[恢复Runbook](docs/recovery-runbook.md)。

[威胁模型与预算](docs/threat-model-and-budget.md)列出信任边界、高风险接口和费用限制。只读检查：`uv run python scripts/check_cost_budget.py --input infra/budget/monthly.example.json`；示例未配置预算/账单，预期UNCONFIGURED及退出1。该工具不控制Provider、通知或派发，不把未知费用视为零。


### 五跨度离线标签（TASK-119 部分实现）

`yta_alpha.labels.build_label(base, observation, horizon_sessions=..., cutoff=..., calendar=...)`
接受两份分别固定的 `MarketSnapshot`，支持 1/3/5/10/20 个交易所交易日。
`base` 最后一根日线确定基准价，输入截点必须处于该交易日收盘后的 EOD 窗口；
`observation` 提供精确目标日价格及已核验公司行动覆盖。两份证据必须属于同一数据模式，
且均不晚于显式实验 cutoff。函数复用正式结算的 Decimal 50位、12位 HALF_EVEN
价格收益公式，不把结果端数据作为特征，不返回校准概率，也不写入预测事实。

返回不可变 `PriceLabel`，含跨度、目标交易日/实际收盘时刻、两份快照哈希、原始价格与修订、
拆股因子、收益/方向、日历版本。JSON 中 Decimal 为字符串。`label_available_at`
保守取整个结果快照的 `as_of`，不能把今天获取的修订标签倒填为目标日即可获知；
训练时仍须根据此时间和标签窗口做 purge/embargo；下面的离线切分模块已实现，训练流水线尚未接入。
缺少目标日价格返回 `WAITING_DATA`，未到期返回 `NOT_DUE`，不延长目标日、不沿用前价。
调用方应逐跨度记录失败原因；无隐式补齐或“全部成功”批处理。

本模块仅提供离线标签构建，不能证明行情供应商授权或数据质量资格。
目前预测 API、冻结、Jobs、页面及模型配置保持五日 MVP 协议；完整五跨度服务由 TASK-119 继续实现。


### Walk-forward 切分（TASK-058 部分实现）

`yta_alpha.walk_forward.walk_forward(labels, config=SplitConfig(...), calendar=calendar)`
将同证券、同数据模式、同跨度和同日历版本的 `PriceLabel` 切成扩展训练窗口。
配置包含有序且互不重叠的 `TestWindow(start, end)`、`embargo_sessions`（默认0）
和最少训练样本数（默认1）。日期必须是交易日，每个测试窗口必须具有逐交易日的完整标签；
不会把缺失的测试样本静默丢掉。数据集每个基准日只能选一个标签版本，修订选择由调用方明确完成。

每折以首个测试样本的 feature_as_of 为训练截点，只考虑测试起点前的样本：
先 purge 目标日大于或等于测试起点的标签，再排除 label_available_at 晚于训练截点的标签。
Embargo 采用明确的折间规则：某折最后一个测试标签的目标日之后 N 个交易日中产生的样本，
不进入后续折的训练集；当前折只使用过去训练数据，因此该折自己的后置 embargo 对当前训练集无影响。
测试样本不因 embargo 消失。历史测试样本在后续折满足所有条件后可以加入训练。
这不是随机K折，也不把自然日间隔当作交易日 embargo。

返回不可变清单，记录配置、按时间排序的完整标签集合哈希、日历版本、每折训练/测试标签哈希，
以及 purge、尚未获知、embargo 三类排除记录（按此顺序归类）。输入重排不改变清单，标签修订会改变身份。
训练不足则拒绝整个实验配置，不返回看似有效的空折。该模块不读取供应商、调用模型或写入数据库；
它验证标签时间元数据，不能独立认证源快照或特征内容。训练特征构建、LightGBM接入、独立概率校准、
实验登记和真实数据评测仍待完成，不能由此推导模型表现或收益结论。


### 将特征绑定到标签与时间切分

`yta_alpha.training_data.prepare_dataset(labels, snapshots, config=..., calendar=...)`
将前述标签、精确输入快照和切分配置绑定为 `PreparedDataset`。
每个标签必须找到对应 base_snapshot_hash 的原始输入快照，且证券、模式、信息截点、
基准交易日、基准价格和修订一致。传入结果快照、无关/重复快照或缺失输入均拒绝。
每份输入重新执行 indicators-v3 的时间与交易日连续性检查，再选取最后一个交易日的15个指标。
列顺序固化为 FEATURE_NAMES；只有输入侧指标进入 features，标签/未来价格保留在独立 labels 中。

预热不足、零成交量分母等产生的 null 原样保留，不填0、不删除样本、不进行全数据归一化。
特征使用现有指标的 Decimal 字符串口径；尚未转换成训练库的浮点矩阵。
数据按基准日排序，并绑定经过验证的 Walk-forward 清单。
`prepared_dataset_hash(dataset)` 包含精确特征、标签版本和切分配置，输入重排不改变身份。
本模块不自动登记或加载模型，无法独立认证供应商和标签来源；后续训练入口必须校验制品与来源，
不能把任意反序列化的 PreparedDataset 当作已核验数据。


### 实验 LightGBM 训练与制品推理（TASK-057 部分实现）

训练依赖固定 LightGBM 4.6.0，包含在开发依赖中；独立训练环境使用
`uv sync --locked --extra training`。macOS需要 `brew install libomp`，Debian/Ubuntu需要
`libgomp1`。本机安装了libomp 22.1.6；现有Alpha运行镜像没有启用training extra，在线服务不自动训练。
CI配置显式安装libgomp1，但尚无远端执行证据。

```bash
uv run python -m yta_alpha.train_cli train --input experiment.json --output artifacts/h5.json
uv run python -m yta_alpha.train_cli predict --input inference.json --artifact artifacts/h5.json --sha256 <上一步返回并可信保存的artifact_sha256>
```

experiment.json遵守 `yta_alpha.train_cli.TrainingInput`：labels、snapshots（输入证据）、
observations（结果证据）、split_config、config、fold_index和带时区cutoff。
输入和结果快照分别按哈希去重；训练前重算特征及每条标签，拒绝篡改标签或缺失证据。
每份实验只允许同一跨度，五种跨度各自调用，互不混合。inference.json只有snapshots数组。
命令失败输出固定EXPERIMENT_FAILED并非零退出；Python API保留具体域错误。

训练参数仅开放轮数、种子、叶数和最小叶样本数；使用CPU单线程、固定学习率0.05、
deterministic与force_col_wise。参数依据[LightGBM 4.6.0官方说明](https://lightgbm.readthedocs.io/en/v4.6.0/Parameters.html#core-parameters)。
训练器只构造所选折的训练Dataset，测试样本不参与分箱、拟合、早停或调参。
null转NaN，真实0仍为0；不做全数据归一化。特征输入float64，LightGBM标签内部float32；
这是近似统计学习边界，原始标签/财务规则仍保留Decimal，转换溢出和非零下溢拒绝。
预测转Decimal并按12位HALF_EVEN输出，非法价格收益拒绝而非截断；测试MAE及零收益对照以Decimal计算。

制品为含原生模型文本的JSON（非pickle），保存配置、数据/代码哈希、训练信息截点、
精确训练/测试/排除样本ID、日历、模式、跨度、测试预测及指标；状态固定EXPERIMENTAL，概率为null。
0600临时文件完成写入后原子发布，不覆盖已有目标。重新加载必须提供外部可信SHA256，
并核对模型文本哈希及特征顺序；推理拒绝早于训练信息截点、不同模式/日历或过期EOD窗口的输入。
哈希证明一致性，不证明来源授权或模型质量；不要给未知制品自行计算一个哈希后当作可信模型加载。

本机已对五跨度执行真实CPU训练、CLI保存/重载/推理和错误哈希拒绝，观察见
infra/verification/lightgbm-training-observation.json；数据明确为合成REPLAY，不是实际研究成绩。
同环境同数据的确定性已验证；官方说明不同编译器/系统/版本可能产生不同结果，不作跨平台逐字节承诺。
数据库模型登记、在线预测/冻结接入、独立校准、人工晋升和真实数据评测仍待完成。


### 实验模型数据库登记

配置私有 `YTA_MODEL_DATABASE_URL` 为具有既定SELECT/INSERT权限的运行角色连接，
不要使用迁移owner，也不要把它传到浏览器。命令如下：

```bash
uv run python -m yta_alpha.train_cli register --artifact artifacts/h5.json --sha256 <可信制品哈希>
uv run python -m yta_alpha.train_cli predict-registered --input inference.json --model-id <登记返回的model_id>
```

登记复用不可变 `yta.model_versions`，model_kind=TRAINED、制品状态固定EXPERIMENTAL；
config保存完整模型JSON及其外部文件哈希，sha256为整个登记配置的规范内容哈希，
区别于模型文本哈希和外部文件字节哈希。版本ID由登记内容生成，格式为lightgbm-experimental-<hash>。
相同内容的并发/重复登记返回同一版本，只追加一次model.experimental_registered审计；
插入及审计在同一事务，任何失败整体回滚。此私有运维入口以数据库权限认证，审计actor_id=null，
不伪造BFF用户身份；注册既不批准模型晋升，也不提供冻结授权。

读取核验版本ID、类型、登记内容哈希、制品结构及内部模型文本哈希。
数据库保留原生模型文本，原本机文件移走后仍能加载实验模型进行推理。
这不意味着已备份全部训练快照或能从JSON重现任意原文件的空白格式；输入证据仍按哈希追溯。
备份恢复额外检查所有TRAINED记录的嵌套制品完整性，无需加载原生训练库。
正常在线API、用户模型选择、冠军审批、五跨度冻结/结算仍待接入。


### Alpha 内部实验预测接口

`POST /internal/v1/experimental-models/{model_id}/predict` 接受
`{"snapshot": <MarketSnapshot>, "horizon_sessions": 5}`，显式跨度必须与已登记模型相同。
需要BFF使用的服务Bearer凭证；工具专用Token不能调用。默认YTA_EXPERIMENTAL_MODELS=DISABLED返回503，
开启需设置ENABLED并安装training extra/OpenMP。独立镜像可用：

```bash
docker build --target training-runtime -f infra/docker/alpha.Dockerfile -t yta-alpha-training:local .
docker compose -f infra/compose/compose.app-local.yaml -f infra/compose/compose.experimental-inference.yaml config --quiet
```

第二条仅验证本地REPLAY覆盖配置，不启动服务。需要启动时沿用现有专用测试库准备流程，
把同一覆盖文件加入Compose启动命令。默认runtime镜像/Compose不启用实验推理，
该覆盖文件不暴露Alpha公网端口，也不新增BFF或浏览器接口。

接口检查环境模式、服务器时间、最新已收盘日及EOD窗口、模型跨度/训练截点/日历，
从数据库加载模型并重算输入指标。仅development/test的REPLAY采用输入as_of作为回放逻辑时点，
且输入仍不得超过服务器时间；LIVE使用服务器当前窗口，不接受历史输入冒充当前研究。
运行库缺失或版本不符明确503，不回退baseline或fixture。

响应契约ExperimentalPrediction含EXPERIMENTAL/TRAINED、模型和输入哈希、跨度、基准/目标交易日、
目标收盘时间、训练截点、Decimal字符串收益、方向和null概率。无模型文本、凭证、草稿ID或确认权限；
调用只读、不创建草稿/冻结/评测事实。正式冻结入口仍只接受已有草稿及用户确认。
来源资格仍由上游证据流程负责，内部接口不会把未核验数据升级为LIVE成绩。
本机HTTP容器观察见infra/verification/experimental-api-observation.json。


### 独立窗口概率校准与可靠性报告（TASK-059 部分实现）

```bash
uv run python -m yta_alpha.calibrate_cli --input calibration-input.json --artifact model.json --sha256 <可信模型制品哈希> --output calibration-report.json
```

输入遵守CalibrationInput：calibration_labels、evaluation_labels、snapshots、observations、
带时区cutoff和embargo_sessions。校准样本必须精确等于模型制品的预留fold.test，
因此该模型原test_mae从此只能视为校准窗口诊断，不能作为新的独立最终评测成绩。
最终评测基准日必须严格晚于所有校准目标日及可选交易日embargo，且在首次评测特征截点前
所有校准标签均已可获知。两段窗口要求完整交易日样本，验证模式/跨度/日历、精确快照并重算标签。
既有训练样本不能改名后用于校准；最终评测结果不参与校准拟合。

用模型预测收益作为排序分数、UP为1（FLAT/DOWN为0），在校准窗口拟合单调非递减映射。
实现为Decimal50位加权PAVA：同分数合并、相邻违序块按样本权重合并，节点间线性插值，
域外使用端点概率。拟合比较使用整数权重交叉乘积，最终概率及指标HALF_EVEN到12位。
至少需要两类标签；此数学条件不是统计质量门槛。方法背景见
[独立校准与评分说明](https://scikit-learn.org/stable/modules/calibration.html)和
[IsotonicRegression文档](https://scikit-learn.org/stable/modules/generated/sklearn.isotonic.IsotonicRegression.html)。
本实现不增加scikit-learn依赖。

报告包含模型/输入/代码哈希、两段精确标签ID、拟合节点、校准分数及目标、最终概率及目标、
Brier和固定10个等宽分箱的ECE/可靠性表。区间左闭右开，最后一箱包含1；空箱均值/频率为null，
空评测集合的基础指标函数返回null而非0，完整实验入口则拒绝空窗口。
Brier综合反映概率预测质量，不能单独当作校准良好证明；报告始终EXPERIMENTAL/UNVERIFIED。
0600报告原子发布且禁止覆盖，CLI返回可信保存用的文件SHA256。

本机真实CPU回归模型的合成REPLAY校准/评测及CLI证据见infra/verification/calibration-observation.json。
现有预测接口和登记模型仍返回probability_up=null；尚未将校准制品接入登记/在线概率输出，
也未完成真实数据样本规模、跨期稳定性和人工Review，不能将小样本数值解释为已验证置信度。


### 校准报告不可变登记与重放

迁移015_calibration_records新增独立事实表，外键绑定已登记模型；不混入正式预测评测表。
迁移仍用owner角色，运行时按既有授权流程仅授予新表SELECT/INSERT。准备私有YTA_MODEL_DATABASE_URL后：

```bash
uv run python -m yta_alpha.calibration_registry create --model-id <已登记实验模型ID> --input calibration-input.json
uv run python -m yta_alpha.calibration_registry get --id <返回的calibration-experimental-ID>
```

create读取数据库模型，重新验证输入证据并拟合/评测，不直接接受调用方声明的指标。
记录保存CalibrationInput完整快照、标签、截点及报告，可脱离原本机输入文件重放。
ID和内容哈希覆盖整个记录；并发/相同内容重试只有一条记录和一条审计，审计失败整笔回滚。
审计使用私有运行角色入口、actor_id=null，状态保持EXPERIMENTAL/UNVERIFIED。
不同输入截点、样本/模型/代码版本生成新记录，不覆盖历史。

读取先核对内容身份，再从保留的模型和证据重算报告，验证概率、样本划分和指标语义相同；
历史code_sha256原样保留，不伪装成当前代码哈希。CLI get只返回报告，不默认输出全部原始证据。
备份恢复对存在的校准表执行同样重放；因此有校准记录的完整恢复核验需要training extra、
OpenMP及匹配LightGBM版本。缺依赖或跨平台无法重现时应明确失败，不跳过后声称恢复成功。
原生学习浮点结果仍受运行环境影响，源数据/模型及配置均保留以便调查。

该登记不批准在线概率、模型晋升或正式预测。当前没有浏览器/BFF读取入口，
现有实验预测接口仍返回probability_up=null；真实样本验证及独立Review仍待完成。


### 显式校准版本的私有概率预测

`POST /internal/v1/experimental-models/{model_id}/calibrations/{calibration_id}/predict`
沿用ExperimentalRequest、服务Token和YTA_EXPERIMENTAL_MODELS开关。
调用方显式选择已登记模型与校准ID，两者必须匹配；不存在或不匹配时拒绝，
不自动选择“最新”记录，也不回退另一模型/校准。

先重放校准报告，再检查输入as_of不早于整个报告cutoff及校准标签可用时间；
这样不会在最终评测结果尚未知时使用经过该报告选择的校准版本。
LIVE还要求as_of不早于数据库实际created_at，不能由请求正文覆盖登记时间。
只有development/test REPLAY按实验逻辑截点判断，始终不能进入LIVE成绩。
此外保留校准标签/embargo区间、模型训练截点、模式、跨度、日历及服务器EOD窗口校验。

独立响应CalibratedExperimentalPrediction增加明确的calibration_id、calibration_available_at、
Decimal字符串probability_up和固定probability_qualification=UNVERIFIED，整体仍EXPERIMENTAL。
这仅表示采用了该实验映射，不表示已达到概率质量门槛。未指定校准的原实验接口继续返回null概率。
两个接口都只读，不创建草稿、冻结、评测或审计事实，不获得模型晋升权限。
BFF/浏览器展示与真实数据质量、人工审批尚待接入。


未校准的实验模型接口同样核对模型实际登记时间：非回放输入as_of早于model_versions.created_at时
返回MODEL_NOT_AVAILABLE，不以training_cutoff替代模型登记可用性。REPLAY仍使用显式实验逻辑时钟，
用于回放既有实验；该例外不适用于LIVE或正式成绩。模型登记时间由数据库读取，不能从请求正文覆盖。

### 实验模型元数据目录

`GET /internal/v1/experimental-models?limit=20&horizon_sessions=5&cursor=<model_id>`
使用服务Token及默认关闭的`YTA_EXPERIMENTAL_MODELS`开关。服务端配置决定data_mode，
请求不能切换模式。limit为1–50，horizon可选1/3/5/10/20；游标必须属于当前模式及跨度。
按数据库登记时间、ID降序分页，返回items与next_cursor；末页游标为null。
并发新增记录可能出现在后续重新查询的首页，跨页不承诺数据库快照一致性。

返回模型身份、哈希、登记/训练截点、跨度、样本数和保留窗口MAE十进制字符串。
所有条目均为EXPERIMENTAL/UNVERIFIED；metric_scope固定为
RESERVED_WINDOW_NOT_QUALIFIED_FINAL_EVALUATION，因为该窗口可能随后用于校准，不能当作最终成绩。
逐条校验登记与模型文本哈希，数据库只读并逐条读取完整制品，响应不含权重、原始特征或配置。
此接口尚未接入BFF/浏览器，不赋予模型晋升、预测冻结或用户确认权限。

### BFF 实验模型目录

已登录用户可通过`GET /api/yta/v1/experimental-models`读取上述元数据，使用相同的
limit/horizon_sessions/cursor参数。BFF与Alpha均须显式设置`YTA_EXPERIMENTAL_MODELS=ENABLED`，
默认关闭；本地`compose.experimental-inference.yaml`覆盖文件会为两个服务开启该能力，
使用前仍需构建当前镜像。目录为系统模型的共享元数据，不是某个用户的研究记录。

BFF拒绝客户端data_mode/owner_id和未知、重复、越界查询参数；模式由服务端配置决定。
上游响应按严格Zod契约验证：额外字段、其他模式/跨度、重复模型、身份哈希不一致、
错误或不前进的游标、超出页大小均返回通用502错误，不将响应正文作为错误详情返回。
十进制指标字符串原样保留，BFF不计算金融指标。浏览器不接触Alpha地址或服务凭证。
当前只提供目录API，尚无实验模型页面；私有预测接口尚未公开到BFF。

本地实验镜像验证（依赖已有`yta-dev_default`、`yta_test`及合成REPLAY模型/校准记录）：

```bash
docker build -f infra/docker/alpha.Dockerfile --target training-runtime -t yta-alpha-training:local .
docker build -f infra/docker/node.Dockerfile --target bff -t yta-bff:local .
pnpm build
uv run python scripts/check_model_catalog_containers.py
```

脚本启动无宿主机端口映射、只读根文件系统、非root的独立容器，验证实际登录、模型分页、
客户端模式覆盖拒绝、Alpha原生推理和登记时间边界，并比对镜像模块与本机源码/构建产物哈希。
BFF通过打包后的createBff关闭研究恢复worker启动，避免扫描共享测试库中的其他测试Run。
脚本生成合成测试登录记录并注销，finally清理演练容器和临时凭证文件；不修改模型、预测或结算事实。
这属于本机Linux容器验证，不代表远端部署、真实行情或模型质量验收。

### 实验模型页面

登录后访问`/models`查看实验模型目录。BFF的`/me`返回
`experimental_models_enabled`能力标记；仅启用时显示导航入口，未启用的直接访问显示明确说明。
页面只调用BFF，支持五种跨度筛选、前后分页、完整模型ID/哈希与UTC时间展开、加载/空/失败重试状态。
切换筛选取消前一个请求，旧请求结果不能覆盖新筛选；登录失效转到登录页。

每个版本显示训练及保留窗口样本数、两种误差的原始十进制字符串。
保留窗口可能随后用于校准，版本之间样本与窗口可能不同，因此没有冠军排名或晋升按钮。
页面尚未提供已审批候选/冠军的最终评测分布比较。REPLAY明确提示合成历史回放的局限。
桌面及390px手机布局已通过浏览器验证；当前目录及报告页面已构建进本机实验镜像并完成真实容器链路验证。

不替换响应的实际容器页面验证：在构建当前Alpha training、BFF和Web镜像后，执行
`node scripts/check_model_catalog_browser.mjs`。脚本要求`yta_test`至少有11个已登记的合成REPLAY模型，且已登记共享契约的`experimental-price-report.json`及`experimental-report-comparison.json`对应报告，
以及空闲的本机3300端口；使用独立Compose项目，仅Web映射到127.0.0.1。
它以随机测试账号真实登录，核对数据库与前两页顺序、跨度筛选、完整版本和原始误差展示，
再从模型报告列表进入详情，将HTTP整份报告与数据库载荷比较，检查指标及逐样本证据展示。
再通过两个模型列表加入比较，验证刷新保留、整份比较载荷与数据库一致，以及移除后清空结果。
随后真实提交一份合成候选提案、同键精确重试、打开详情并返回原表单，核对数据库只有一份提案和一次审计。
为验证历史分页，再通过认证BFF追加10份独立合成提案；清空会话缓存，从页面按10＋1条翻页，
核对列表与各次登记响应完全相等，并从第二页进入原提案详情。
测试库还须有已登记的REPLAY五交易日质量规则。脚本从提案进入质量核验，显式选择规则、
提交、精确重试并打开/刷新固定详情，核对数据库规则/核验载荷及单次审计；全程无响应替换。
检查390px手机溢出、同源请求和Web网络隔离。BFF关闭研究恢复worker，未启动Jobs。
脚本注销会话并清理自己的容器/网络；截图与观察记录保留在`.local/model-catalog-browser/`。
这会追加合成测试账号、登录/注销、候选提案、质量核验及相应审计，不修改模型制品或预测事实，也不会请求真实行情/LLM。

### 独立窗口价格评测与同样本比较

在已有实验模型与校准记录后，可使用运行角色数据库连接生成只读报告：

```bash
uv run python -m yta_alpha.model_evaluation --calibration-id <registered-calibration-id>
# 比较两个版本：重复参数，最多10个；必须使用完全一致的样本证据及cutoff。
uv run python -m yta_alpha.model_evaluation --calibration-id <first-id> --calibration-id <second-id>
```

连接取自`YTA_MODEL_DATABASE_URL`。读取时重放登记校准记录，检查标签、原始快照及校准/评测窗口隔离，
再对evaluation_ids对应的独立后续样本计算模型MAE和方向命中率，提供零收益对照；
五日模型还使用同一输入快照计算momentum20-v1对照。其他跨度的该对照为null，原因FIVE_SESSION_ONLY。
使用Decimal50、12位HALF_EVEN和sign-zero-v1；JSON数值指标为十进制字符串，空统计为null。

报告保留逐样本标签ID、输入/目标证据哈希、收益及时间，连同校准记录身份、样本集合哈希、
计算代码哈希和完整内容哈希。日期相同但行情版本或信息截点不同的报告不能比较。
结果为EXPERIMENTAL/UNVERIFIED的历史实验重算，不是不可变前瞻预测成绩；
它不写evaluation_reports、不修改模型状态，也不构成晋升批准。
当前入口为私有CLI，尚未提供报告登记/API/页面，尚无真实样本质量或跨期稳定性验收。

### 固定实验价格评测记录

```bash
uv run python -m yta_alpha.model_evaluation_registry create --calibration-id <registered-calibration-id>
uv run python -m yta_alpha.model_evaluation_registry get --id <model-evaluation-experimental-id>
```

迁移016提供model_evaluation_records，引用校准记录与模型版本。create从已验证证据重算报告，
以报告内容哈希登记；同一报告重复/并发登记返回同一ID，只追加一次审计，审计失败回滚报告。
运行角色仅SELECT/INSERT，表级触发器拒绝UPDATE/DELETE/TRUNCATE。get核对字段、内容哈希，
并重放校准和独立窗口的原生预测/指标；历史源码哈希保持原值，但计算语义必须一致。
这是私有实验登记，actor_id=null不代表任何用户批准；不会晋升模型或进入正式前瞻指标。
恢复验证也重放全部实验报告，需要固定版本training依赖/OpenMP；失败不能跳过。

### 私有实验评测读取接口

`GET /internal/v1/experimental-evaluations/{evaluation_id}`使用服务Token，工具Token不能读取。
它沿用默认关闭的`YTA_EXPERIMENTAL_MODELS`开关，按Alpha服务端data_mode过滤报告；
未知ID或其他模式记录统一404，并在任何原生重放之前检查模式。
开启后读取会核对原始证据、模型和报告内容，返回严格ExperimentalPriceReport契约。
原生训练运行库不可用返回503，不使用样例或未经重放的结果代替。

响应保留登记报告内时间字符串的原始时区拼写、十进制字符串和指标别名，
因此可移除content_hash后按原规范再次核对内容哈希；不会把+00:00自动改写成Z。
接口只读，不创建报告或审计，也不获得冻结/晋升权限。BFF报告路由与页面尚未接入。

### 登录用户读取固定实验评测

`GET /api/yta/v1/experimental-evaluations/{evaluation_id}`通过BFF登录会话鉴权，
沿用默认关闭的实验模型开关；不接受查询参数或客户端模式/所有者覆盖。
这是已登记系统模型的共享实验报告，不是归属其他用户的研究事实。

BFF校验严格共享Zod契约、精确样本顺序/数量、基线适用跨度、模式、报告ID及内容/样本集合哈希，
保留原始时间与Decimal字符串。上游多余字段、数字代替Decimal字符串、错误身份或内容变化
返回通用502，不输出原始异常载荷。Alpha仍负责金融计算和原始证据重放；BFF仅校验传输身份和结构。
报告路由已进入登录后的OpenAPI文档，并接入下述列表和详情页面；审核入口见文末，正式晋升资格与冠军切换尚未完成。

### 按模型发现已登记实验评测

`GET /internal/v1/experimental-models/{model_id}/evaluations?limit=20&cursor=<evaluation_id>`
仅供内部服务调用，沿用实验开关和服务鉴权。服务器data_mode与模型ID共同限定范围，
未知/其他模式模型404，游标必须属于同一模型和模式；limit为1–50，按登记时间/ID倒序分页。
无报告返回空items与null游标，不承诺并发插入下跨页数据库快照一致性。

每项只含报告身份、内容/样本集合哈希、登记/信息截点、跨度、模式和样本数，不返回价格指标或模型权重。
列表核对严格报告结构、身份和内容哈希，但不会执行原生预测；完整语义重放由单份报告读取接口负责。
因此列表条目仍为UNVERIFIED，不能据此认定模型通过评测。


登录后的 `GET /api/yta/v1/experimental-models/{model_id}/evaluations` 提供同样的分页参数，
BFF校验模式、模型身份、页大小及游标前进；不接受客户端指定模式。
开启实验模型开关后，从 `/models` 的“查看独立窗口评测”进入模型报告列表，
点击报告进入 `/model-evaluations/{evaluation_id}`。详情展示同样本的模型/零收益/适用的五日动量指标、
逐样本结果、完整版本身份和复现清单；Decimal保持字符串精度。
默认关闭时不请求实验报告；读取失败可重试，错误模式或身份的响应不会展示。
当前仅展示历史实验报告，尚不提供候选/冠军分布比较或审批晋升。


### 比较明确选定的已登记报告

私有 `POST /internal/v1/experimental-evaluations/compare` 接受
`{"evaluation_ids": ["model-evaluation-experimental-<hash1>", "model-evaluation-experimental-<hash2>"]}`。
仅服务Token可调用，沿用默认关闭的实验开关；请求不接受模式、审批或其他字段。
选择2–10份不同报告，每份按服务端模式读取并重放验证，返回原有报告身份、载荷和输入顺序。
标签集合、信息截点、模式、跨度、日历、价格协议和收益口径必须一致；同日期但目标行情修订也拒绝。
缺失报告返回404，模型运行库不可用返回503，不临时生成替代报告或返回部分比较。

也可使用 `uv run python -m yta_alpha.model_evaluation_registry compare --id <report1> --id <report2>`，
连接取自私有 `YTA_MODEL_DATABASE_URL`。这是只读比较，不创建新报告、审计或审批记录；
结果仍为EXPERIMENTAL/UNVERIFIED，保留各报告历史源码哈希，不自动挑选冠军。
BFF比较接口及浏览器选择见下文；候选审批尚待接入。


登录用户可调用 `POST /api/yta/v1/experimental-evaluations/compare`，请求体与内部比较接口一致。
沿用实验模型开关，要求登录Cookie、同源Origin及当前会话CSRF Token，不接受查询参数或模式覆盖。
BFF核对返回的完整报告集合、请求顺序、共同样本哈希以及每份报告的内容身份和模式；
缺项、调换报告、内容被改写或额外字段返回固定502，不泄露上游载荷。
接口不写入事实或进行模型审批，暂停新研究写入时仍可使用这项只读比较。


开启实验功能后，可在任意模型的评测列表点击“加入比较”，再进入 `/models/compare`。
支持跨模型选择2–10份报告、移除和清空；选择按登录账号和数据模式保存在当前标签页会话中，刷新后保留。
显式点击“比较所选报告”才请求计算。失败保留选择，样本不匹配时提示重新选择；
更改选择会清除旧比较结果，比较期间锁定选择。页面按所选顺序展示模型及适用基线的原始Decimal指标，
可核对共同样本哈希、完整模型身份并打开原报告，不按成绩排名或提供模型审批。


### 从原始训练证据重新训练核对

`replay-training` 使用与 `train` 相同的TrainingInput，重建原始标签、指标特征和walk-forward切分后执行真实CPU训练，并逐项比较可信制品的全部19个字段，包括模型文本、测试预测/误差、样本集合、配置、日历和源码哈希。

```bash
uv run python -m yta_alpha.train_cli replay-training \
  --input training-input.json \
  --artifact artifacts/h5.json \
  --sha256 <可信制品文件哈希> \
  --output artifacts/h5-training-replay.json
```

完全一致输出MATCHED并返回退出码0；原生训练完成但存在差异时保存MISMATCH报告并返回2。坏哈希、缺失标签证据或运行时不可用返回1，不保存成功报告；领域错误只输出错误码，不回显内部异常。旧命令的通用错误格式保持。

报告绑定规范训练输入哈希、可信原制品文件哈希、两份制品内容/模型哈希、两份源码哈希和逐项差异，不包含模型文本或原始行情。文件以0600权限原子创建，已有路径不会覆盖，CLI输出可用于校验文件的SHA256。匹配是本次输入、代码与运行环境下的精确一致性，不保证其他平台也一致；代码变化即使不改变预测，也会作为源码差异保留。

该文件核对命令要求输入快照单独保管，不登记训练证据到数据库；完整输入可使用下述登记命令保留。两者都不取得供应商授权或point-in-time资格。结果固定UNVERIFIED且promotion_allowed=false，现有提案/审核不会因这个文件自动获得批准。原始登记的推理重放与完整重新训练是两种不同证据。


### 原始训练证据的不可变登记

迁移024增加 `training_evidence`，由模型外键绑定完整TrainingInput、原始登记/制品文件哈希及重新训练比较结果。模型对象来自可信登记，调用者不能提交“已成功重训”的报告作为事实。创建先实际重新训练；MATCHED和MISMATCH都可以登记，内容去重，证据及一次系统审计在同一事务内提交。运行角色仅SELECT/INSERT，owner也不能UPDATE/DELETE/TRUNCATE。

```bash
# YTA_MODEL_DATABASE_URL 使用私有数据库连接；YTA_DATA_MODE 明确选择环境模式。
uv run python -m yta_alpha.train_cli register-training-evidence \
  --model-id <固定模型ID> --input training-input.json
uv run python -m yta_alpha.train_cli read-training-evidence --id <登记返回的ID>
```

这两个登记/读取命令成功返回0表示操作成功，必须查看status区分MATCHED和MISMATCH；它们与上一节文件核对命令的退出码2含义不同。CLI只输出身份、模式、状态、输入哈希及差异字段，完整输入留在私有数据库。默认模式REPLAY，跨模式模型或记录不可见。

读取会从保存的完整输入重新训练，并核对模型登记、输入、比较语义及内外内容身份。历史核验器源码哈希保留，当前重放的比较语义必须相同；底层训练源码变化导致新的差异时，不以旧MATCHED记录冒充当前可重现。运行时不可用明确失败，备份恢复也必须执行这些重训检查。

这仍不是数据授权、point-in-time或生产资格批准。现有回测、质量核验与审核尚未绑定该训练证据ID，仍保持各自的未核验声明；不能仅因数据库存在MATCHED记录就自动晋升。


### 训练证据的私有读取接口

`GET /internal/v1/training-evidence/{id}` 接受Alpha服务Token，不接受工具Token；实验功能默认关闭，模式由服务端配置决定。读取先从固定模型登记及完整输入重新训练，再返回严格摘要。缺少数据库/原生运行时明确不可用，跨模式或缺失身份返回404。

响应含证据ID/内容哈希、模型ID及登记/文件哈希、输入哈希和重训报告，不返回原始行情、标签、特征矩阵或模型文本。报告匹配/差异字段必须排序、无重复、互斥并覆盖制品全部字段；状态、模型/源码哈希关系、模式和输入引用必须一致，晋升标志严格为false。报告内容哈希可独立校验，但公开摘要不足以重建包含私有输入的登记哈希；该检查仍由完整登记重放完成。

登录用户可通过 `GET /api/yta/v1/training-evidence/{id}` 读取固定摘要，仍需显式启用实验模型功能。BFF拒绝额外查询参数，绑定身份、服务端模式、字段划分和模型/输入引用，再验证报告内容哈希；不提供原始快照或创建接口。源码元数据键按Python Unicode码点排序参与哈希，保持跨语言一致。上游不可用时明确失败，不用缓存样例代替重训。

Alpha私有目录 `GET /internal/v1/experimental-models/{model_id}/training-evidence` 支持 `limit`（1–50，默认20）和 `cursor`。按登记时间及身份倒序分页，游标必须属于同模型及服务端模式。目录校验保存输入、报告哈希和登记模型绑定，返回状态、输入哈希及登记时间摘要，不执行原生重新训练；`verification=stored-integrity-checked-retraining-on-detail` 明确此区别。MATCHED只表示保存时的比较结论，详情读取才重新训练。

登录用户可通过 `GET /api/yta/v1/experimental-models/{id}/training-evidence` 使用相同分页参数；BFF限制查询字段、模型/模式范围、页面大小、重复身份和游标循环。公开目录保持存储完整性标记，详情才重新训练。模型目录提供训练证据入口 `/models/{id}/training-evidence`，固定详情位于 `/training-evidence/{id}`，详情读取会重新训练；审核固定引用仍待接入。读取接口本身不授予审核或晋升权限。


### 候选模型提案的持久化基础

迁移017新增不可变 `model_proposals`。内部Python `ModelProposalRegistry.create(owner_id, request_id, mode, request)`
接受已验证提出者身份和幂等请求UUID；`ProposalRequest`仅含候选评测ID、可选参考评测ID和1–2000字符非空理由。
模型身份、样本哈希及报告哈希由完整重放后的登记报告派生，不能由提案正文覆盖。
参考报告必须属于不同模型且使用相同样本/截点；无参考模型时保留候选报告已有的基线对照。

同一提出者/模式/请求ID重复提交返回原记录，不同正文返回409；提案和一次提出者审计同事务写入。
读取限定提出者及模式并重放引用证据。状态固定SUBMITTED/UNVERIFIED，提交不授予审批或冠军权限。
当前提供内部持久化模块及下述私有HTTP接口，已接入下述BFF接口，浏览器提案入口见下文；入口必须从认证BFF会话派生提出者，
模型/工具输出不能提供审批身份。质量门槛、人工审批、冠军切换和回滚仍待实现。


私有 `POST /internal/v1/model-proposals` 创建提案，`GET /internal/v1/model-proposals/{uuid}` 读取。
两者只接受Alpha服务Token，工具Token不能调用；沿用默认关闭的实验模型开关。
提出者取自可信BFF传递的 `X-YTA-Owner-ID` UUID头，POST另要求 `Idempotency-Key` UUID头；
请求体仅含候选评测ID、可选参考评测ID和理由，不接受owner、模式、模型值或审批字段。
读取限定提出者及服务端模式；缺失或其他用户的提案404。创建成功和同键重试均返回200及同一记录，
同键异参409。响应保留原始提案正文、内容哈希及登记时间，可再次核对哈希。
内部服务鉴权不替代最终用户认证；BFF会话和页面接入见下文，人工审批仍待实现。


登录用户可通过 `POST /api/yta/v1/model-proposals` 提交，或通过
`GET /api/yta/v1/model-proposals/{uuid}` 读取自己的提案。默认关闭实验开关；创建要求会话、同源Origin、
CSRF及UUID Idempotency-Key，受写入准入暂停控制，所有者只从会话派生。
请求不允许额外查询/所有者/审批字段，参考报告省略时明确为null；理由最多2000个Unicode字符。
BFF核对响应所有者、模式、请求UUID、提交正文、报告引用身份和完整内容哈希，额外或不一致响应固定502。
响应丢失时保留原UUID及正文精确重试；新UUID会表示另一份提案。提交状态仍SUBMITTED/UNVERIFIED。


从实验报告详情点击“以此报告提出候选模型”，使用该报告已有基线作对照；
或在恰好两份报告的比较结果中选择候选，另一份自动作为参考模型报告。
填写理由并显式提交，成功后可打开 `/model-proposals/{uuid}` 详情。

提交前将请求UUID与完整正文保存在当前标签页会话存储中，按账号/模式/候选/参考隔离。
无法保存时不发送请求；发送后理由固定，响应丢失、写入暂停或异常响应时显示“重试原提案”，
刷新不自动重发，手动重试沿用原UUID和正文。成功后保留详情入口，避免在同一路径误建新提案。
页面仅显示SUBMITTED/UNVERIFIED；审批及冠军切换尚未开放。


私有提案历史通过 `GET /internal/v1/model-proposals?limit=20&cursor={uuid}` 查询；
`limit` 为1–50，首请求省略cursor，响应为items/next_cursor。按登记时间与ID降序分页，
限定可信BFF所有者头及服务端数据模式；其他用户或模式的游标拒绝。列表核对持久化提案身份、
引用组合及正文/请求哈希，不重复运行原生模型；详情仍完整重放引用报告。
登录用户可通过 `GET /api/yta/v1/model-proposals` 使用相同分页参数读取自己的历史。
BFF从会话派生所有者，拒绝所有者/模式覆盖和额外查询，核对每条内容哈希、身份/模式、
页大小、重复项及游标前进。该只读入口不受创建写入暂停开关限制，实验功能默认关闭。
登录后从模型目录的“我的模型提案”进入 `/model-proposals`，按登记顺序翻页并打开详情。
列表展示固定理由、候选/参考模型与提交状态，刷新从服务器读取，不依赖表单的会话缓存。
空列表、加载失败重试和默认关闭均有独立状态；分页失败会清除上一页内容，避免误认。

### 历史候选质量核验

内部Python `ModelEvaluationRegistry.assess_quality(report_id, policy, reference_id=...)`
先按策略模式读取并完整重放显式登记报告，再执行数值核验；不写事实或审批记录。
`HistoricalQualityPolicy` 必须显式提供 `policy_version`、`data_mode`、`horizon_sessions`、
`minimum_samples`、`maximum_mae`、`minimum_direction_accuracy`、`minimum_mae_improvement`
和 `require_reference`。没有内置的生产阈值；测试中的宽松规则仅用于合成数据回归。
阈值使用十进制字符串，最多12位小数；样本数是严格正整数，方向命中率在0–1之间。

核验保留报告ID、样本哈希、完整规则及规则哈希、各项观测/要求/通过结果和整体内容哈希。
MAE改善按“对照MAE − 候选MAE”计算，零收益始终检查，5交易日还检查五日动量，
提供参考报告时还检查同样本不同模型；缺失指标或要求的参考报告均不能通过。
`numeric_pass` 仅表示满足所提供的历史数值规则，`promotion_allowed` 固定为false，
资格仍为UNVERIFIED。真实数据资格、概率可靠性、正式门槛登记/审批和冠军切换仍待接入，
不得把此内部筛选结果当作晋升许可。

迁移018加入不可变 `model_quality_policies` 与 `model_quality_assessments`。
内部 `ModelQualityRegistry.register_policy(policy)` 登记内容寻址规则；相同版本/模式/跨度
不能绑定不同阈值，更改规则需要新版本。`create(policy_id, candidate_id, reference_id=...)`
完整重放登记报告后保存核验结果，同一内容并发重复登记仅保留一条记录及一次审计。
`get(id, data_mode=...)` 会校验规则、重放引用报告并重算全部核验，拒绝结果篡改。
规则和结果的事实写入与审计同事务；运行角色仅SELECT/INSERT。该内部登记接口不代表
已认证用户审批，审计actor为空，也没有激活规则或切换冠军的行为。

私有质量API默认关闭，启用实验模型后仍仅接受服务鉴权，工具Token不可调用：

- `GET /internal/v1/historical-quality-policies/{id}` 返回固定规则、内容哈希和ID。
- `POST /internal/v1/historical-quality-assessments` 接受 `policy_id`、
  `candidate_evaluation_id`、可选 `reference_evaluation_id`，只使用已登记规则。
- `GET /internal/v1/historical-quality-assessments/{id}` 完整重放并返回固定核验结果。

POST成功及相同引用重试均返回200；结果ID为 `historical-quality-assessment-` 加返回的
`content_hash`。服务端限定数据模式，未知/跨模式身份404；正文不接受阈值、模式或审批字段。
缺少要求的参考报告可以保存为未通过的核验证据；原生库不可用503，不生成替代结果。
登录用户可通过同名 `/api/yta/v1/historical-quality-policies/{id}` 和
`/api/yta/v1/historical-quality-assessments` 的POST/GET入口访问。创建要求同源/CSRF，
受写入准入控制；BFF严格核对模式、固定规则/报告引用、检查集合及完整内容哈希。
创建重试须保留原来的规则和报告引用，内容身份保证相同结果不重复登记；它不是用户审批。
没有通过HTTP注册/修改/激活规则的操作。

`GET /api/yta/v1/historical-quality-policies` 发现当前模式下已登记规则，支持可选
`horizon_sessions=1|3|5|10|20`、`limit=1..50`（默认20）和上一页返回的 `cursor`。
私有对应路径为 `/internal/v1/historical-quality-policies`；按登记时间/ID倒序分页，
游标绑定当前模式及跨度筛选。返回完整固定规则及哈希，读取不运行模型推理、不激活规则。
BFF拒绝客户端模式覆盖，核对页大小、游标前进、重复记录、模式、跨度和每条规则内容哈希。

报告详情与已提交提案提供历史质量核验入口：`/models/quality/{candidate}/{reference}`，
`baseline`表示无额外参考报告。页面核对报告后，仅列出当前模式/跨度的已登记规则，
逐条展示阈值并要求明确选择和提交，不自动选中规则或运行核验。响应丢失/写入暂停后
保留选择，手动重试使用相同规则和报告；切换规则或翻页会清除旧结果，刷新不自动提交。
结果展示原始数值、逐项要求及通过状态，链接到 `/model-quality/{assessment_id}` 固定详情。
详情刷新直接读取服务器记录；数值达标仍明确显示未批准，无规则激活或冠军切换。

模型审批权限的基础检查由BFF管理：`YTA_MODEL_REVIEWER_IDS` 是逗号分隔的用户UUID，
默认空列表，最多100个不同UUID。邮箱、客户端角色/所有者请求头或模型输出都不能授予权限。
仅在实验功能启用、当前登录用户在该列表时，`GET /me` 的 `can_review_models` 为true。
`Auth.modelReviewMutation` 每次重新校验登录、同源、CSRF及配置身份，会话撤销立即失效。
修改名单须重启所有BFF实例以加载一致配置；不要只更新部分实例。该字段只表示访问权限，
不表示证据合格或已经批准。目前尚无公开审批写入或冠军切换接口，也未给任何实际用户配置权限。

内部 `ModelReviewService.evaluate(reviewer_id, request_id, mode, request)` 提供审核预检，
正文仅接受 `proposal_id`、`assessment_id`、`action=APPROVE|REJECT` 和非空理由。
审核人/请求身份由可信调用者单独传入，未来HTTP入口必须先使用BFF审批权限检查。
预检从数据库取得提案所有者，完整重放提案与核验，要求候选/参考报告、样本集合、模式及
内容身份完全匹配。不得用另一组报告的通过结果审核当前提案。
当前历史证据未取得晋升资格，APPROVE得到BLOCKED，数值不达标时另列失败检查；
REJECT得到REJECTED预检结果。结果固定请求、审核人、理由、证据与内容哈希，
`promotion_allowed`始终false。此方法只读，不保存审核事实、改变提案状态或批准模型；
审核预检本身不写入；登记接口见下文，公开入口仍待实现。

迁移019和内部 `ModelReviewRegistry` 保存不可变审核事实。`create(reviewer_id, request_id,
mode, submission)` 接受上述审核正文及 `expected_previous_review_id`（首次为null）。
同一提案串行检查上一条审核，旧状态提交409；同一审核人/模式/请求UUID的同正文重试返回
原记录，即使后来已追加新审核，同键异参409。事实与审核人审计同事务，审计失败回滚。
BLOCKED之后可基于该记录再次审核；REJECTED关闭此提案，后续须另提新提案。
读取和备份恢复会重放整条链、提案与核验；请求/内容哈希、前驱及修订号不一致拒绝。
该内部持久化接口要求可信BFF先校验审核权限；公开入口已接入下述权限检查，没有APPROVED或
冠军状态。历史数值达标不能绕过未取得资格的证据约束。

私有 `POST /internal/v1/model-reviews` 接受审核正文和预期前驱，要求服务鉴权、UUID
`Idempotency-Key` 及可信BFF的 `X-YTA-Owner-ID`（审核人身份）；工具Token不可调用。
`GET /internal/v1/model-reviews/{uuid}` 按服务端模式完整重放审核链。响应固定包含审核人、
请求、证据哈希、修订号及BLOCKED/REJECTED状态，成功和精确重试均200。
状态变更/已关闭/同键异参409，跨模式或缺失404，默认关闭或重放不可用503。
这些私有端点信任BFF完成权限校验，不直接暴露给浏览器。

公开 `POST /api/yta/v1/model-reviews` 和 `GET /api/yta/v1/model-reviews/{uuid}`
只允许服务端 `YTA_MODEL_REVIEWER_IDS` 中的已登录审核人，默认名单为空且要求实验功能开启。
提交还要求同源、CSRF、写入准入及UUID幂等键；审核人来自会话，正文不能授予权限。
BFF核对模式、内容哈希、固定请求及审核人/幂等键，异常上游返回502。配置中的审核人可读取
当前模式的其他审核人记录，普通用户不可读取；写入暂停时仍可读取。审核页面使用下述独立审核入口。


私有 `GET /internal/v1/model-proposals/{uuid}/reviews` 提供当前模式的审核历史，
默认20条、最多50条，按修订号倒序，`cursor` 为上一页最后一条审核UUID。
游标必须属于同一提案/模式，后续新增审核不会插入已有游标之后的分页；刷新首页才能看到新记录。
空提案返回空列表，缺失或跨模式提案404。每页完整重放所选审核及前驱链与证据，
游标本身也验证，不将未校验的历史当作审核依据。该历史入口已接到下述BFF路由与审核界面。
提交仍必须带用户看到的 `expected_previous_review_id`；查询成功不能消除并发提交产生的409。

公开 `GET /api/yta/v1/model-proposals/{uuid}/reviews` 沿用审核人会话及实验开关限制，
接受严格的 `limit`/`cursor` 查询，不接受客户端模式或所有者。BFF校验每条记录的模式、
提案归属和内容哈希，以及页内唯一性、连续递减修订号、前驱UUID和末项游标。
非首项不能是已终止的REJECTED记录；根修订必须终止分页。错误上游统一502。
审核人可读取当前模式的提案审核历史，此权限不改变原提案详情的所有者限制。


审核提案发现使用独立的 `GET /api/yta/v1/model-review-proposals` 和
`GET /api/yta/v1/model-review-proposals/{uuid}`。两者仅允许配置中的已登录审核人，
实验开关关闭或审核名单为空时不可用；普通提案列表/详情继续限定所有者。
列表按服务端模式读取所有所有者的已提交提案，按登记时间/ID倒序分页，默认20条/最多50条，
严格接受limit/cursor；不接受客户端所有者或模式。它不是仅含待处理项的队列，
当前审核状态须查询审核历史。列表验证不可变提案元数据/哈希，详情完整重放报告与证据。
Alpha对应的私有model-review-proposals路由只接受服务鉴权，信任BFF执行审核人权限检查。
BFF在独立响应校验中允许不同所有者，但仍验证模式、身份、完整内容哈希及分页边界。


已配置审核人的会话可从导航进入 `/model-review-proposals`，查看所有已提交提案并进入
`/model-review-proposals/{uuid}`。页面展示原始提案、报告链接与审核历史，用户需选择
固定规则完成质量核验、选择审核动作并填写理由，才可显式提交。当前APPROVE仍会因
历史证据未取得资格而记录BLOCKED；REJECT关闭该提案，两者均不切换冠军模型。

提交前将UUID幂等键、正文与看到的前驱保存到按账户/模式/提案隔离的sessionStorage，
存储失败不发送。响应不确定时锁定正文，刷新后手动重试原请求；不会自动提交或换键。
服务端明确返回前驱冲突/提案关闭后，须重新加载最新状态并重新核对；已拒绝提案不能继续审核。
成功记录可从 `/model-reviews/{uuid}` 刷新读取。普通账户直接访问时只显示不可用提示，
不请求审核证据；BFF权限检查仍是实际授权边界。浏览器回归使用真实登录和受控证据响应，
审核页面另有下述实际容器链路验证。


`scripts/check_model_catalog_browser.mjs` 已覆盖实际审核页面链路：重建当前training-runtime、
BFF及Web镜像后，在独立Compose项目为新建合成用户临时配置审核权限，真实登录、选择规则、
提交APPROVE并取得BLOCKED、同键重试及详情刷新，再按前驱提交REJECT并验证关闭。
完整记录和审核人审计与yta_test一致，关闭后原请求仍返回原结果。脚本退出清理自己的
容器/网络/临时配置，保留不可变合成事实；不配置真实用户权限，不运行研究worker或LLM。
最新观察见 `infra/verification/model-review-container-browser-observation.json`，不代表生产资格或staging验收。


私有 `GET /internal/v1/experimental-calibrations/{calibration-experimental-hash}`
读取已登记校准报告。入口要求服务鉴权与实验开关，先按服务端模式查询，再完整重放登记输入、
模型制品、独立拟合及后续评测证据。跨模式/未知ID404，非法ID422，功能关闭或原生重放不可用503；
工具Token不可调用。没有登记或晋升写入，不返回底层原始行情输入。

响应包含登记ID/时间、模型ID、原登记内容哈希和独立report_hash，以及原始报告：校准/评测样本ID、
拟合分数/标签、单调结点、评测概率/标签、10个可靠性分箱、样本数及Brier/ECE。
十进制与时间文本保留原拼写，空分箱指标为null；严格校验概率范围、样本长度、窗口ID不重叠、
结点单调性、分箱计数与报告哈希。EXPERIMENTAL/UNVERIFIED状态固定，不能将读取成功解释为
概率可靠性或生产晋升资格。BFF已接入下述读取入口与固定报告页面。


公开 `GET /api/yta/v1/experimental-calibrations/{id}` 要求登录和实验功能开启，拒绝额外查询，
服务端选择数据模式。BFF使用严格共享契约校验登记ID/内容身份、报告hash、模式、样本长度、
窗口ID不重叠、分箱计数及指标缺失状态；错误上游固定502，不泄露附加字段。
所有概率与分数保持十进制字符串，包括历史Decimal记录中的 `0E-12`；概率范围通过精确
文本/指数检查而非浮点舍入验证，Brier/ECE和校准计算仍在Alpha完成。该接口只读，不授予批准权。


价格评测详情的“查看固定概率校准报告”链接进入 `/model-calibrations/{id}`，
保持报告引用的校准版本。页面展示原始Brier/ECE、样本数/实际上涨数、10个可靠性分箱、
独立拟合/后续评测的时间与交易日间隔、逐样本概率/实际类别，以及登记/报告/模型/代码身份。
空分箱显示“无样本”，逐样本结果每页20条，详细拟合窗口/分数/标签/结点可展开核对。
刷新读取相同ID，错误模式或身份拒绝；失败清除旧指标，实验功能关闭时不请求报告。
手机页面的宽表局部横向滚动。所有值仅用于解释固定历史证据，不自动评定概率资格或批准晋升。

`scripts/check_model_catalog_browser.mjs` 同时覆盖概率报告的真实容器页面链路：从价格报告进入
固定校准版本，核对完整响应与登记fixture及数据库report，检查10个分箱、原始指标、详细身份、
刷新与390px手机布局。最新镜像和观察见 `infra/verification/calibration-report-container-browser-observation.json`。


内部 `CalibrationRegistry.assess_quality(calibration_id, policy)` 提供概率数值门槛核验。
`ProbabilityQualityPolicy` 必须显式提供版本、模式、跨度、最低拟合/评测样本数、最低评测上涨/
非上涨样本数及最高Brier/ECE；没有生产默认阈值，两个类别的最低样本数均为正整数。
入口先按模式完整重放固定校准登记，再用逐样本概率/实际类别重新计算可靠性摘要并核对，
逐项比较明确阈值。返回固定登记/报告/模型/窗口集合身份、规则正文/哈希、每项观测与结果及总哈希。

`numeric_pass` 只说明这份固定历史报告满足所给数值规则，样本数不是独立样本量证明。
结果仍UNVERIFIED且promotion_allowed=false，不能替代真实数据来源、时间可用性、概率资格或人工批准。
该入口只读，无事实/审计写入。

迁移020及内部 `ProbabilityQualityRegistry` 另行提供不可变规则和结果登记。
`register_policy` 按版本、模式和跨度绑定完整规则，同版本异参返回409；
`create(policy_id, calibration_id)` 完整重放固定校准证据后登记结果，内容身份支持并发精确去重。
规则和结果各与一次登记审计同事务，审计失败回滚；审计actor为空，不代表人工批准。
两张事实表拒绝UPDATE、DELETE及TRUNCATE，运行角色只获SELECT/INSERT权限。
`get` 重放校准证据并重算全部检查；备份恢复验证同时检查规则身份和核验结果。
Alpha私有接口提供 `GET /internal/v1/probability-quality-policies` 及固定ID详情、
`POST /internal/v1/probability-quality-assessments` 和
`GET /internal/v1/probability-quality-assessments/{id}`。仅服务Token可访问，工具Token拒绝，
实验开关默认关闭，模式来自服务端设置。POST正文只接受已登记的policy_id与calibration_id，
完整重放后登记，重复请求返回同一结果；规则登记仍通过内部登记器进行。
响应契约验证身份哈希、规则阈值、检查顺序/结果及类别计数。原生重放不可用返回503，
不能以旧缓存或合成数据替代。规则列表按服务端模式及可选horizon_sessions筛选，
按登记时间/ID倒序分页，limit默认20、范围1–50；游标必须属于同一模式和跨度范围。
发现接口只读并验证规则身份，不执行原生推理、不激活规则。
BFF在 `/api/yta/v1` 下提供同名概率规则列表/详情、核验POST及固定ID GET。
所有入口要求登录及实验开关；POST另外要求同源、CSRF和写入准入。
严格拒绝额外正文/查询，核对服务端模式、请求引用、内容与规则哈希；错误上游返回固定502。
浏览器仍只访问BFF，指标计算和证据完整重放留在Alpha。
校准报告页面提供“选择规则并核验概率质量”入口，进入 `/probability-checks/{calibration_id}`。
页面先核对固定校准报告，再按模式/跨度分页列出规则；必须明确选择后提交。
失败保留选择，手动重试相同规则和校准引用；切换规则/分页清除旧结果。
`/probability-quality/{assessment_id}` 支持固定结果刷新读取，展示六项原始阈值/观测、
校准/报告/样本窗口身份，数值通过仍不授予批准。手机明细表局部横向滚动。
组合门槛、正式证据资格及批准仍待实现。


概率核验实际容器验证已纳入 `scripts/check_model_catalog_browser.mjs`：从校准报告进入，
明确选择规则、提交并精确重试，核对六项明细、原始指标、数据库完整载荷及单条登记审计，
再刷新固定详情并检查390px布局。运行要求yta_test迁移至020，已有合成报告及对应跨度规则。
最新镜像摘要与完整观察见 `infra/verification/probability-quality-container-browser-observation.json`。


内部 `ModelQualityGateService.evaluate(owner_id, mode, request)` 组合价格与概率数值核验。
请求只接受proposal_id、price_assessment_id、probability_assessment_id；所有者和模式来自可信调用者。
先完整重放所有者提案、两份核验和候选报告，再绑定候选/参考引用、模型、校准登记、
评测样本集合、模式及跨度。返回固定证据/规则哈希、两组失败项和组合numeric_pass。
任何一组未通过都会保留对应阻止原因；即使两组通过，仍UNVERIFIED且promotion_allowed=false。
该服务只读，不登记组合事实，也尚未接入审核HTTP或用户界面；不能当作已批准或生产资格。


迁移021和内部 `ModelQualityGateRegistry` 提供组合结果的不可变登记。
`create(owner_id, mode, request)` 先执行上述完整重放，再按内容身份去重写入组合事实及所有者审计，
审计失败整体回滚。审计动作是登记，不是批准。表引用提案、价格核验与概率核验，
拒绝UPDATE/DELETE/TRUNCATE；运行角色只有SELECT/INSERT。
`get(owner_id, mode, id)` 先限制提案所有者/模式，再重新验证所有引用及组合结果。
备份恢复验证器按依赖顺序重放提案、两份核验和候选报告后检查组合事实。
Alpha私有 `POST /internal/v1/model-quality-gates` 与固定ID GET 接收服务Token和可信
X-Yta-Owner-Id，工具Token拒绝；实验开关默认关闭，模式由服务端决定。
POST正文仅提案/两份核验ID，按内容身份去重；GET保持所有者隔离并完整重放。
响应严格验证哈希、失败项顺序/去重、双方数值结果及阻止原因；不可用时返回503。
BFF公开 `/api/yta/v1/model-quality-gates` POST及固定ID GET，所有者来自登录会话，
POST还须同源/CSRF和写入准入，实验开关默认关闭。严格拒绝额外参数，核对模式、
请求三项引用、固定ID及内容哈希；非法上游统一502。审核提交与页面引用组合事实、正式资格与批准仍待实现。


内部 `CombinedModelReviewService.evaluate` 提供组合证据版审核预检，输出
`model-review-decision-v2`。请求仅包含提案ID、固定quality_gate_id、APPROVE/REJECT和理由；
审核人/请求UUID与模式来自可信调用方，调用前仍必须由BFF检查审核权限。
服务读取提案真实所有者并完整重放组合事实；相同模型的另一提案也不能借用该组合结果。
请求批准仍因历史资格未通过而BLOCKED，并分别保留价格与概率失败原因；拒绝返回REJECTED。
这是只读预检，不保存审核事实、不授予冠军资格。v1旧审核记录及请求语义保持不变，
组合审核已接通公开BFF提交，页面支持读取新旧记录；审核表单分别选择价格/概率规则，明确固定组合证据后提交新版审核。


迁移022为同一model_reviews表追加quality_gate_id，并按v1/v2版本校验对应证据引用。
原v1正文与请求哈希保持原样，v2记录绑定组合核验及其价格/概率证据。
内部 `ModelReviewRegistry.create_combined` 与原create共用提案锁、revision、预期前驱、
拒绝关闭及审核人/模式/请求键空间；同键跨版本属于异参冲突。
读取/分页按每条记录版本重放完整混合链，旧请求在新审核后仍精确返回原记录。
Alpha与BFF的提交/读取契约支持两版记录，页面历史和固定详情展示各版本证据。v2请求通过quality_gate_id绑定组合事实，不能同时携带旧assessment_id；审核权限仍取自BFF会话和服务端审核人配置。新表单提交v2；已有v1请求仍按原键和正文恢复、重试，不自动升级或替换。
备份恢复同样按版本重放，新增组合证据不改变旧审核的含义。


审核人组合核验入口为 `POST /model-review-quality-gates` 与固定ID GET。
它们要求BFF服务端配置的审核人会话；POST还要求同源、CSRF与写入准入。
Alpha从提案查出真实所有者后完整重放证据，首次登记审计记录实际审核人。
原 `/model-quality-gates` 仍仅供所有者使用。两入口对相同内容复用同一份不可变事实，
重复读取/创建不会改写首次审计，不构成模型批准。


审核页的组合证据流程：分别完成价格与概率规则核验，点击“固定组合证据”，
再填写动作、理由并确认提交。核验失败的数值也可用于拒绝或记录被阻止的批准请求；
组合登记不会自动提交审核。更换规则或重新核验会清除已固定组合。
组合提交失败可手动重试原证据对；审核提交前保存请求键、正文与前驱，
失响应后保持原请求，刷新不会生成新键。历史资格仍未通过，不能晋升模型。


内部回测单次持仓计算见[独立回测执行口径](docs/backtest-execution-protocol.md)。显式费用/滑点、下一交易日开盘代理和同窗口LONG基准独立于研究价格标签；新增固定观察快照的单资金槽报告，重叠与证据不足逐条保留，权益未知时不输出总收益；登记模型原生推理来源已可只读重放；一般组合资金分配、完整训练来源资格、用户生成流程和晋升资格尚未实现。


迁移023支持内部回测报告不可变登记：完整模型推理/计算重放、固定输入和代码哈希、模型外键引用、内容去重与事务审计；恢复演练逐条重放。Alpha私有POST/GET `/internal/v1/backtest-reports`已支持严格响应投影，服务Token限定且默认关闭；已接入登录限定的BFF固定读取 `/api/yta/v1/backtest-reports/{id}`，校验模式、引用与分层内容哈希；已支持同路径目录GET的模型/完整性筛选和游标分页；目录明确标记原生推理在详情重放，`/backtests` 目录和 `/backtests/{id}` 固定详情已接入实验模型入口，展示费用、逐笔结果、退出权益及缺证据空值。

### pi 真实模型应用链路探针（合成 REPLAY）

`scripts/start_hermes_application_probe.py` 启动独立数据库与私网容器，复用 pi 选中模型；
`scripts/probe_hermes_application.mjs` 经真实 BFF 登录/CSRF/消息接口调用 Hermes，核验两种工具回执、
模型报告、同请求去重、无自动冻结，再通过测试用户的显式确认接口冻结 REPLAY 预测并注销。
它不是 LIVE 行情或 staging 验收。前提是本机测试 PostgreSQL，以及本轮构建的
`yta-alpha:mvp`、`yta-finance-tools:mvp`、`yta-bff:local` 和锁定 Hermes 镜像；先构建当前 BFF dist。
Node 使用当前工作区只读挂载，不能把该探针当作密封发布制品。

运行 `uv run python scripts/start_hermes_application_probe.py`。首次启动保存
`.local/hermes-application-state.json`（0600），其中有 Compose 命令、独立数据库和私密卷身份。
已有状态会拒绝重新启动：应先读取原应用日志、数据库及 Hermes 终态，不能删除状态后盲目重发。
探针使用全新空库，并在启动研究队列前检查没有历史 run；不得改用共享测试库。
成功或失败后按保存的 Compose 命令清理容器，保留原数据库和私密卷用于核对。
模型密钥只传入隔离 Hermes 私密卷，不放入聊天、公开证据或 Compose env。

### 本机个人 Massive LIVE 环境

本地 LIVE 入口为 `http://localhost:3301`；保留的合成回放入口为 `http://localhost:3300`。登录信息保存在权限 0600 的 `.local/massive-access.json`。LIVE 使用独立数据库与 Docker 网络，Alpha/Jobs 只读已核验快照；Massive key 只供主机采集脚本使用，pi 模型凭证只进入 Hermes 私有卷。此入口不是云端发布验收。

已完成初始化后：

```sh
# 检查/补采最新已收盘交易日；不触发模型请求
uv run python scripts/refresh_massive_local.py
# 单进程后台模式：每 30 分钟检查一次，采集请求间隔至少 15 秒
uv run python scripts/refresh_massive_local.py --watch
# 浏览器验证登录、真实行情/指标与移动布局；不创建预测
node scripts/check_massive_local_browser.mjs
```

采集进程锁阻止重复 watcher。当前后台进程 PID/日志为 `.local/massive-collector.pid`、`.local/massive-collector.log`；终端启动的 watcher 不会在电脑重启后自动恢复。合盖/休眠会暂停本地任务；醒来后重新检查数据，不补造价格。原始数据、日汇总证据和核验快照追加保存；现有有界 Nasdaq 日历资格截止 2026-12-31，超出范围需重新核验。旧日汇总与修订后的日线不一致时，保留旧缓存版本并重新核对。

首次研究应在收盘日线就绪后、下一交易日开盘前执行。模型只能形成草稿；需要你在页面确认冻结。实际五交易日结算依赖到期的真实行情，不能用模拟时间完成 LIVE。价格/成交量使用精确 Decimal，缺失数据不会用回放填充。
