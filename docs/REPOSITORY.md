# 仓库边界与外部组件接入

更新：2026-09-28；2026-10-02 增 `apps/dashboard/`（S10a）；2026-10-04 明确独立 WebUI fork 与两类 Hermes 边界。本文用于修改模块、依赖环境或部署边界时定位职责；实施进度与验证结果以 [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) 为准，领域术语见 [CONTEXT.md](../CONTEXT.md)。

## 1. 四仓库职责

| 仓库 | 职责 | 文档入口 |
| --- | --- | --- |
| [trading_core](https://github.com/youweichen0208/trading_core) | 独立金融查询、标准化与纯计算，无 Hermes/Core 依赖 | [文档](https://github.com/youweichen0208/trading_core/blob/develop/docs/README.md) |
| [trading-assistant](https://github.com/youweichen0208/trading-assistant) | 个人 Hermes 接线、权限、会话、知识与镜像 | [文档](https://github.com/youweichen0208/trading-assistant/blob/develop/docs/README.md) |
| [youwei-webui](https://github.com/youweichen0208/youwei-webui) | 登录、聊天、工作台及界面镜像 | [文档](https://github.com/youweichen0208/youwei-webui/blob/develop/docs/README.md) |
| youwei-trading-agent | 正式研究、数据库、任务、评估与跨仓部署登记 | [文档](README.md) |

本仓库维护预测研究的业务规则、事务、迁移、契约、适配器和部署配置。Core API 与 Worker 共用 `youwei_core` 和同一条 Alembic 迁移链；Sandbox Runner 因持有容器运行时权限而独立打包和运行。包边界用于控制依赖与权限，不要求一开始把每个业务模块拆成服务或仓库。

`youwei-webui` 是独立 Open WebUI fork，负责登录与聊天体验、界面定制、上游数据库迁移兼容、镜像构建和界面测试。trading-assistant 普通仓库负责个人 Hermes gateway、平台 HTTP 插件、memory/知识、助手镜像和原生测试；本仓库负责平台领域规则、研究/实验 Hermes、跨服务契约、部署编排、镜像版本登记和整体备份调度。界面仓库通过 HTTP 和固定镜像接入，不导入 Core 包、不复制研究任务状态机。主交互及升级规则见 [架构](ARCHITECTURE.md) 的个人助手入口与 §2.2。

当前研究报告仍链接到本仓库 Dashboard；在 fork 内嵌任务卡片是后续能力，需要 WebUI 服务端鉴权适配，浏览器不持 Core key。2026-10-04 已部署 fork v0.11.4、独立助手与平台 Core API，完成目标机兼容、旧数据迁移及备份隔离恢复；版本、源码身份和未验证项见 [部署记录](ops/three-repo-vm-rollout-20261004.md)。

| 边界 | 职责 | 依赖与权限 |
| --- | --- | --- |
| `youwei_core/` | HTTP 入口、鉴权、任务/租约、数据授权/PIT、Ledger、调度与评估记录 | Core Python 3.13 环境；持有相应业务数据库凭证，按模块控制副作用 |
| `quant/` | baseline/动量兼容载具、冻结Logistic/Ridge模型、特征与历史Trial计算；显式输入产生结果 | 不依赖 Core 数据库、HTTP、供应商或容器运行时；计算依赖scikit-learn/NumPy等由根uv.lock固定，Ledger按release选择冻结参数并记录provenance |
| `contracts/src/youwei_contracts/` | Core 与 Runner 共享的执行、冻结输入和产物传输结构 | 轻量独立包；不导入 Core、数据库模型或上游 Agent SDK |
| `services/sandbox-runner/src/youwei_runner/` | 执行 HTTP 接口、签名与租约检查、受限容器生命周期、输入与产物边界校验 | 独立 `pyproject.toml` 与 `uv.lock`；持有受限运行时权限，无业务数据库或供应商凭证 |
| `youwei_core/sandbox/` | Worker 侧授权、HTTP 客户端、当前 attempt 的产物接纳和查询 | 使用 Core 事务与 fencing；不执行生成代码、不访问容器运行时 socket |
| `apps/dashboard/` | 评估界面：同源只读代理（Basic Auth + GET 白名单，服务端持 tenant key）与原生 ES modules 静态页 | 不入库、不写 Core；依赖随 Core 环境（FastAPI/httpx），静态资产无构建链、无外部 CDN |
| Alembic 迁移 | Core 业务表、约束与版本演进 | 保留单一迁移链；Runner 不建第二份业务任务库 |

以上是本次收敛的代码边界。独立安装、测试和目标机部署的完成证据分别记录，不能根据目录或锁文件存在认定服务已验收。Hermes 研究与实验适配器已实现；正式使用仍受 ResearchRelease 与权限约束。

当前实际结构：

```text
youwei-trading-agent/
  youwei_core/              # API、Worker、数据、Ledger、评估及 Runner 客户端
  quant/                    # 无副作用的计算函数
  contracts/                # 独立轻量 Python 包，共享 DTO 与能力令牌
  services/
    sandbox-runner/         # 独立 Python 包、依赖锁与 HTTP 执行服务
  apps/
    dashboard/              # 评估界面：只读代理 + 原生 ES modules 静态页（S10a）
  migrations/               # Core 唯一迁移链
  infra/
    images/                 # Core / Runner 独立镜像
    compose/development.json
    upstreams.lock.yaml
    validate_upstreams.py
  tests/
    contracts/              # HTTP 契约、上游登记与部署校验
    known_answers/          # 纯计算已知答案
    test_*.py               # 数据库与跨进程集成测试
  ops/backup/
  docs/
  pyproject.toml
  uv.lock                   # Core 运行依赖及本仓库开发依赖
```

`services/agent-runtime/`（Hermes 研究/实验实例适配器，独立 Python 3.14 环境）已随 S07 建立；`integrations/openwebui/`（Open WebUI 持久配置切换；旧研究 Pipe 已删除，S12c/S12e）已建立；`integrations/pi/` 和 `integrations/openviking/` 在对应功能实际接入时创建。当前数据逻辑继续位于 `youwei_core/data/`；有独立部署需求时再提取 Data Service。Core 保持原路径，避免单纯搬目录影响现有导入、构建和迁移。

## 2. Worker 与 Runner 的执行交接

1. Worker 从 PostgreSQL 领取任务，取得当前 attempt 与租约，校验主体对作业及快照的授权。
2. Worker 读取已冻结快照，向 Runner 提交内容、manifest、hash 和受限作业参数；签名绑定 tenant、job、attempt、请求体 hash 及租约截止时间。
3. Runner 校验签名、能力范围与输入 hash，将输入物化到自有 spool，仅按服务端模板创建受限沙箱。请求不能指定任意镜像、宿主路径或下载 URL。
4. Worker 轮询执行状态并按协议续租。Runner 到期或取消时终止执行；执行结果含已校验的产物字节及清单。
5. Worker 核对结果归属与 hash，当前 attempt 的 fencing 检查通过后保存产物和业务事件；过期 Worker 不能通过迟到返回补写业务产物。

HTTP 接口：

| 操作 | 接口 |
| --- | --- |
| 提交 | `POST /v1/executions` |
| 状态 | `GET /v1/executions/{job_id}/{attempt_no}` |
| 取消 | `DELETE /v1/executions/{job_id}/{attempt_no}` |

Runner 的状态只作短期执行缓存，PostgreSQL 是业务状态权威。Runner 重启丢失执行后，由 Core 结束或等待原 attempt 过期，再创建新 attempt；外部计算可能重复，业务产物仍通过 fencing 接纳。进程分离本身不证明 gVisor、网络限制、取消与生产配置已经验收。

## 3. 外部组件与依赖环境

| 组件 | 本仓库负责的接入 | 当前边界 |
| --- | --- | --- |
| Hermes | 个人 gateway 插件与研究/实验适配分别维护；后者输出 Proposal 交 Controller 校验 | 个人助手独立 Python 3.13，研究/实验独立 Python 3.14；profile、权限和发布分离；实际验收见 S07/S08/S12e |
| Pi | MVP 暂缓接入（Hermes 为唯一 Agent 框架）；若日后接入则负责受控 RPC、工具/扩展允许列表、将探索代码提交到执行链路 | 版本锁保留；无业务接入，Node/TypeScript 包与 RPC wrapper 不设于当前 MVP |
| youwei-webui（Open WebUI fork） | 本仓库维护 Hermes 原生连接、后台模型分流、兼容测试和部署固定 | 外部仓库维护界面源码及镜像；账户/聊天归 WebUI，平台任务与报告归 Core |
| OpenViking | 可选长文档派生检索适配器 | 尚未接入；事实与批准的研究记忆仍由 Core 管理 |

Core 的运行依赖不含 Runner；根 dev 组安装 Runner 仅用于集成测试。Runner 自有独立锁，Hermes 及以后实际接入的前端/检索组件分别固定其解释器、依赖锁、上游完整提交或镜像 digest；Pi 保留为可替换候选（版本锁不变，暂缓接入）。[上游管理](UPSTREAMS.md) 中的登记和校验工具绑定 Compose、镜像引用、验收报告与清单，不能代替契约、权限和端到端业务测试。

上游优先使用官方发布、配置和扩展能力；平台业务差异保留在本仓库适配层。用户已决定以独立 youwei-webui fork 承接持续界面定制，按正式 Release 维护可追踪差异。必要能力确实无法通过这些入口实现时，再维护最小 fork，并登记上游基线、补丁、升级方式和相关验证。单业务仓库不意味着把 Hermes、Open WebUI、OpenViking 源码复制进 Core，也不要求现在创建尚无行为的接入目录。

## 4. 现有能力与后续范围

现有开发纵向切片覆盖持久任务、身份、PIT 日线与冻结快照、预测封存、Outcome 修订、最小评分、归档导出和 baseline/quant 管线。本次结构调整提取纯量化与共享契约，并把 Runner 的权限从 Worker 中分离；实际验证结果由实施计划记录。

工程能力与正式业务验收分别记录：研究/实验链路已有实现和验收证据，Phase 1A 已按批准版本登记；Phase 1B、国内入口、受控研究记忆等后续范围见实施计划。代码搬迁不批准新的研究行为。

SG 使用现有 DigitalOcean **4 vCPU / 7.8 GB** 主机，重计算从并发1起步；国内与 SG 的旧原型已归档移除。需要复用原型 Next.js 源码时，从 [目标机记录](research/s01-target-verification.md) 指定归档提取并重新验收，不能将旧运行状态算作新实现上线。

MVP 使用 PostgreSQL 保存当前规模的原始数据、冻结快照与受限产物，本地文件用于 Ledger 导出和 WAL/PITR 机制验证，**OSS 不在 MVP 范围**。同一管理权限下的文件与 hash 可以一起重写，本地归档不构成外部锚定或 WORM；独立恢复副本、归档调度与生产 RPO/RTO 由 S09 验收。

## 个人助手边界（S12e）

个人 gateway 插件、Core HTTP 客户端、知识工具、备份模块、Dockerfile 和原生验证已迁入
[trading-assistant](https://github.com/youweichen0208/trading-assistant)，独立 Python 3.13 与锁文件，不依赖 Core 包或兄弟目录。
本仓库通过 `infra/compose/chat-assistant.json` 消费助手镜像，不再从平台源码构建个人助手。
`integrations/openwebui/configure_assistant.py`、日线 API 及数据库测试继续留在平台。
`ops/verify_assistant_webui.py --assistant-image <image> --webui-image <image>` 使用独立 mock 入口验证跨服务组合。
`ops/backup/assistant_image.py` 记录运行镜像身份并在无网络容器内恢复副本，不导入助手源码。
`infra/chat/assistant-upstreams.lock.json` 固定已发布助手提交及镜像；平台部署组合保存在 `infra/releases/20261004/`，当前金融工具上线后的聊天组合位于 `infra/releases/20261005-trading-core/`，旧 EODHD 切换记录保留在 `infra/releases/20261004-eodhd-mcp/`，此前官方 Release 切换记录保留在 `infra/releases/20261004-hermes-v20260924/`。助手仓库维护原生 MCP 配置、八项基础工具、九项 EODHD MCP 允许列表及测试；平台维护镜像消费、私密凭证注入和部署备份。正式研究供应商访问继续由 Core 管理。
详见 [个人助手接入](ops/hermes-personal-assistant.md)。历史实现路径与验证记录保留在 S12e，迁移结果见 S12g。

## 模块维护入口

Core 的 `api/routes` 按业务组织 HTTP 接口，`api/dependencies` 集中认证；`ledger/exploratory` 的 submission、execution、reports、models 分别负责提交查询、执行、报告和共享定义，包入口保持调用接口。
Runner 的 app 只装配，routes 管 HTTP，state 管授权、执行句柄与生命周期，process 共享 CLI 子进程收尾；各执行角色保留独立容器策略。
研究运行时 runtime 管 Hermes 调用和解析，usage 管计数与归因，不导入 Core。

正式 release 绑定的 logistic、dataset、model_registry、pipeline 和根锁文件本轮冻结；pipeline 仍引用本地子进程适配器，因此其必要兼容链保留。移除它需要独立处理 ResearchRelease 影响。

## 独立金融能力包（S12p）

[trading_core](https://github.com/youweichen0208/trading_core) 是私有、默认分支 `develop` 的独立 Python 3.13 包，维护免费日线取得、标准化、纯指标计算与 SEC 财报解析。不导入 Hermes、平台 Core 或数据库，不迁移本仓库 `quant/`、PIT 或已批准 ResearchRelease。

`trading-assistant` 持有三个 Hermes 工具入口、参数转换、允许列表、缓存、总时限及取消控制；从固定金融源码提交构建 wheel 并校验 hash，补充依赖须与官方 Hermes 及已有 DDGS 依赖兼容，冲突阻断构建。新增金融 worker 是同一助手容器中的固定可信 Python 入口，不接受生成代码，也不是新的服务、MCP 或 Runner。

本仓库维护跨服务验证、Compose 配置与候选/生产登记；youwei-webui 延用现有入口。四仓职责是平台、助手、界面、金融库；正式平台仍保持单业务仓和原执行边界。当前交付及真实供应商限制见 [S12p 记录](ops/trading-core-20261005.md)。
