# 项目 Agent 工作指引

## 定位与工作方式

本仓库是美股研究与前向预测评估平台 v0.3 的业务代码库。使用范围为自用或受控内部研究，不自动交易、不向公众提供投资建议。

- 开始修改前，读取 [CONTEXT.md](CONTEXT.md) 的领域术语、[实施计划](docs/IMPLEMENTATION_PLAN.md) 中对应的 Sxx 任务及完成记录，并检查 `git status --short`。保留已有未提交改动，修改范围限于当前任务。
- 用中文沟通；代码标识符沿用项目英文命名。实现按纵向切片推进，每个切片交付可运行行为、相关验证和明确的剩余限制。
- 已确认的技术选型和协议直接执行；常规实现选择自行判断。涉及尚未确定且会改变目标、权限或评估语义的决策，说明影响并请求用户决定，同时继续不依赖该决定的工作。
- 完成说明应包含改动、实际执行的验证及未验证项。隔离开发测试、目标机验证、生产部署和正式前向评估分别报告。

## 按任务读取的依据

| 触发条件 | 必读内容 |
| --- | --- |
| 修改模块边界、工作流、提交或权限 | [ARCHITECTURE.md](docs/ARCHITECTURE.md) 对应章节 |
| 修改目录、包依赖或进程划分 | [REPOSITORY.md](docs/REPOSITORY.md)；保持单业务仓库、独立 Runner 与轻量 contracts |
| 修改上游版本、镜像或发布配置 | [UPSTREAMS.md](docs/UPSTREAMS.md)、`infra/upstreams.lock.yaml` 与验证命令 |
| 修改收益、交易日、截止时间、Campaign 或评分 | [协议索引](docs/protocols/README.md) 中的目标、时间、批次政策及已知答案用例 |
| 修改协议参数或版本引用 | [s00-registration.v1.json](docs/protocols/s00-registration.v1.json) 与所引用协议；核对内容 hash |
| 选择模型、特征、prompt 或研究规则 | [Trial Registry](docs/trials/registry.md)；比较前登记，保留失败和放弃的试验 |
| 接入或升级 Hermes / Pi | [运行时接口核实](docs/research/hermes-pi-runtime-verification.md)、[版本固定](docs/research/runtime-version-pinning.md) |
| 修改模型调用或网关 | [网关研究](docs/research/llm-gateway-options.md) |
| 接入 Tiingo 或解释其字段 | [能力核实](docs/research/tiingo-capabilities.md)、[真实 token 验证](docs/research/tiingo-token-verification.md) |
| 部署、资源调整、沙箱或恢复 | [目标机记录](docs/research/s01-target-verification.md)、[gVisor 验证](docs/research/gvisor-quant-stack.md)、[PITR 演练](docs/ops/backup-pitr-drill.md) |

设计依据是架构文档，目标与评估语义以登记协议为准，工程状态查实施计划中的具体完成记录和实现证据。旧评审记录用于解释背景。运行命令、依赖和配置以当前代码及锁文件核对，不从早期目录示意推断功能已经存在。

SG 当前基线为 DigitalOcean **4 vCPU / 7.8 GB**；原型已归档拆除；**OSS 已排除出 MVP 范围**。历史记录中的原环境保留为验证背景；本地 WAL/PITR 演练不等于独立故障域备份或 Ledger 外部锚定完成。

## 代码与外部组件边界

- **Core 使用 Python 3.13**：FastAPI、SQLAlchemy 2.0 Core、asyncpg、Alembic、Pydantic v2；依赖由 `pyproject.toml` 和 `uv.lock` 管理。API 与 Worker 共用 `youwei_core/`，分别运行。保持显式事务和模块化单包结构。
- `api/` 处理 HTTP 与鉴权接线；`jobs/` 管理任务、租约和事件；`worker/` 负责执行循环；`auth/` 管理身份与能力令牌；`data/` 管理供应商、证券、PIT 与快照；`ops/` 提供运行状态。新增能力优先进入对应模块。
- `quant/` 是纯计算包；`contracts/src/youwei_contracts/` 是无数据库依赖的共享契约。Core 的 `sandbox/` 只做授权、HTTP 调用和受 fencing 保护的入库；Docker 执行只存在于 `services/sandbox-runner/`，有独立 `pyproject.toml` / `uv.lock`。保持 Core 运行依赖不含 Runner，Runner 不导入 Core；根 dev 组安装 Runner 仅供测试。
- 确定性的 **Controller** 掌握任务状态、时间、权限与最终提交。Hermes 组织研究并返回 Proposal，由 Controller 校验后提交。
- **Hermes 使用独立的 Python 3.14 环境**，不能为了它升级 Core 解释器。Hermes 是 MVP 唯一 Agent 框架，研究角色与实验角色由独立 Hermes 实例实现。Pi 保留为可替换候选但暂缓接入（版本锁不变），具体版本查固定记录。
- 标准量化能力优先实现为经过测试的 Python 库，由受控执行入口调用。库尚未覆盖的探索由独立 Hermes 实验实例发起，生成代码作为沙箱输入（不热加载为 Extension 或生产 quant 库）。
- 外部项目默认使用官方固定版本，通过本仓库适配层集成。升级记录版本、完整提交 SHA 或镜像 digest，并执行相关契约测试。只有配置、扩展和适配层无法满足必要改动时才维护最小 fork，记录上游基线与本地补丁。
- Open WebUI / 原型 Next.js 的接入在 S09 评估，前端消费 Core 的持久任务与结果接口。OpenViking 为可选的派生文档检索层；研究记忆以 PostgreSQL 与批准的版本记录为准。

## 必须保持的业务约束

以下是实现与评审的约束，不表示对应功能已经全部验收。

### 持久执行与身份

- PostgreSQL 是执行状态的权威来源。数据库事务保持短小；供应商、LLM 和沙箱调用在事务外进行。
- 有业务副作用的完成操作校验当前租约与递增 fencing token（现为 `attempt_no`）；过期 Worker 的结果不能覆盖新 attempt。幂等键同时绑定租户和输入内容。
- 业务状态与对应事件在同一事务中提交；重试、取消、崩溃和响应丢失不能产生重复业务封存。外部调用可能重复并收费，不能宣称外部调用恰好一次。
- 租户身份取自认证结果；快照、产物和工具权限在服务端验证。作业能力令牌绑定租户、job、attempt、scope 与有效期。

### PIT、快照与评估

- 正式查询显式传入 Controller 确定的时间与权限上下文，冻结输入后不吸收截止时间以后的事实。分别保留来源可用时间、实际接收时间和系统可用时间；缺失来源时间按协议保守处理并标记依据。
- `historical_source` 重建与实际前向记录分别标识。LLM 的历史回放不能作为预测能力证据；训练数据、预处理、校准和标签成熟时间也必须满足 PIT。
- 回补、更正和重述产生新数据版本。快照固定查询、源版本、授权标签和内容 hash，历史快照与已记录输出保持可引用。
- 保持 `Campaign → Batch → ForecastCase` 层级：Campaign 是多周登记实体，Batch 对应一次 cutoff，Case 固定证券、目标和窗口。来源与 release 只在相同 Case 下配对；失败、漏跑、unresolved 和 unscorable 保留在计划分母。
- 固定入退场窗口；区分供应商晚到与市场未形成价格，按目标协议等待原时点数据，永不用复牌价替换目标价格。收益异常与评分细则查协议及已知答案，不由模型临时决定。
- 预测集合原子封存、只追加；Outcome 通过版本追加更正。评估报告固定 Case、预测、OutcomeRevision 和评分代码版本，更正生成新报告。
- 正式研究使用已批准的 ResearchRelease。Lesson、prompt、模型、工具、特征、记忆集合或回退政策变化遵守版本与审批流程；Agent 不能替人批准真实 release hash。候选 Lesson 和自动会话记忆不能绕过此流程。

### 执行隔离

- 供应商访问集中在数据接入模块，模型访问统一经网关。密钥只交给对应服务，日志和研究产物须脱敏。
- Hermes 研究/实验实例的工具、扩展使用明确允许列表；Hermes 内置 `execute_code` 在宿主子进程运行，不等于 gVisor Sandbox Runner，生成代码一律经 Controller 授权进入 Runner。Pi 若日后接入，其 RPC 管理命令同样须允许列表化管理。只有小型可信 Sandbox Runner 持有容器运行时权限；生成代码在无密钥、默认无网络的受限沙箱执行。
- 快照挂载只读；产物校验路径、链接、类型、数量、大小与 hash。外部文本、生成代码和产物均视为不可信输入。

## 开发与验证

在仓库根目录使用以下命令；依赖版本保持锁定：

```bash
uv sync --frozen --group dev --python 3.13
uv run --frozen pytest -q tests/test_worker.py
uv run --frozen pytest -q
```

- **集成测试需要可用的 Docker daemon**：`tests/conftest.py` 创建一次性 PostgreSQL 容器，执行真实 Alembic 迁移，每个测试清表，结束后删除容器；Runner 集成测试另外启动本地 HTTP 子进程与受限容器。`tests/contracts/` 和 `tests/known_answers/` 覆盖清表 fixture，可独立运行且不需 PG。缺 Docker 导致的 skip 不算通过，清表逻辑只能运行于一次性测试库；通过 fixture 传测试配置，不直接导入名为 `conftest` 的模块。
- 修改任务、权限、PIT、迁移或收益计算时，按 TDD 逐个验收行为推进：先观察相应测试失败，再实现并通过。测试公共行为与反例，优先真实 PostgreSQL；供应商调用可用固定响应，真实收费调用单独验收。
- 新增表或约束同步更新 `youwei_core/db/meta.py`、Alembic 迁移及测试清理表清单。已应用迁移通过新增 revision 演进。评估迁移的锁、数据兼容性和恢复方式，测试走实际升级路径。
- 先运行受影响测试；修改共享 schema、fixture 或跨模块契约后运行完整测试集。纯文档修改检查引用、命令与内容一致性即可。检查工具以已配置项为准，不能报告未执行或不存在的检查通过。
- 开发库迁移用 `uv run --frozen alembic upgrade head`；Alembic 直接读取导出的 `YOUWEI_DATABASE_URL`，不会自动加载 `.env`。先核对目标是自己的开发库。API 和 Worker 分别由 `uv run --frozen youwei-api`、`uv run --frozen youwei-worker` 启动，配置见 `youwei_core/config.py`。
- 初始资源保持保守：重计算并发 1，API / Worker 小连接池、独立资源限额。根据目标机测量调整。
- Runner 独立安装验证：`uv sync --project services/sandbox-runner --frozen --no-dev`；其配置使用 `YOUWEI_RUNNER_*`，不加载 Core `.env`。默认生产模式强制沙箱镜像 digest 和 `runsc`，本机测试必须显式选择 development。`infra/compose/development.json` 是本地联调配置，不能视为生产验收。
- 上游登记检查：`python3 infra/validate_upstreams.py --mode catalog`。正式发布按 UPSTREAMS 文档校验渲染后的 Compose、镜像 digest、验收证据与部署清单；catalog 通过不授予发布权限。

## 文档与交付

- 任务完成后在实施计划对应 Sxx 记录实现位置、实际验证命令/结果和遗留依赖；以验收证据更新状态，不复制固定测试数量到本文件。
- 协议被 Campaign 或批准记录绑定内容 hash 后，语义变化创建新版本；尚未绑定的草案修订也需同步机器登记引用及 hash。保留真实试验与批准记录，未取得的引用保持未就绪。
- 此文件维护工作规则与阅读入口；进度写实施计划，领域术语写 CONTEXT，技术事实和来源写对应研究记录，避免多处维护同一事实。
- 提交时按任务组织可审查的差异，使用 Conventional Commits；仅包含本任务文件。生产发布、数据源采购、正式 Campaign 与 release 批准依各自授权执行，不由本地测试通过推导为已获批准。
