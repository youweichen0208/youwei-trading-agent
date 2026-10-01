# 仓库边界与外部组件接入

更新：2026-09-28。本文用于修改模块、依赖环境或部署边界时定位职责；实施进度与验证结果以 [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) 为准，领域术语见 [CONTEXT.md](../CONTEXT.md)。

## 1. 一个业务仓库，按权限独立运行

本仓库维护预测研究的业务规则、事务、迁移、契约、适配器和部署配置。Core API 与 Worker 共用 `youwei_core` 和同一条 Alembic 迁移链；Sandbox Runner 因持有容器运行时权限而独立打包和运行。包边界用于控制依赖与权限，不要求一开始把每个业务模块拆成服务或仓库。

| 边界 | 职责 | 依赖与权限 |
| --- | --- | --- |
| `youwei_core/` | HTTP 入口、鉴权、任务/租约、数据授权/PIT、Ledger、调度与评估记录 | Core Python 3.13 环境；持有相应业务数据库凭证，按模块控制副作用 |
| `quant/` | baseline/动量兼容载具、冻结Logistic/Ridge模型、特征与历史Trial计算；显式输入产生结果 | 不依赖 Core 数据库、HTTP、供应商或容器运行时；计算依赖scikit-learn/NumPy等由根uv.lock固定，Ledger按release选择冻结参数并记录provenance |
| `contracts/src/youwei_contracts/` | Core 与 Runner 共享的执行、冻结输入和产物传输结构 | 轻量独立包；不导入 Core、数据库模型或上游 Agent SDK |
| `services/sandbox-runner/src/youwei_runner/` | 执行 HTTP 接口、签名与租约检查、受限容器生命周期、输入与产物边界校验 | 独立 `pyproject.toml` 与 `uv.lock`；持有受限运行时权限，无业务数据库或供应商凭证 |
| `youwei_core/sandbox/` | Worker 侧授权、HTTP 客户端、当前 attempt 的产物接纳和查询 | 使用 Core 事务与 fencing；不执行生成代码、不访问容器运行时 socket |
| Alembic 迁移 | Core 业务表、约束与版本演进 | 保留单一迁移链；Runner 不建第二份业务任务库 |

以上是本次收敛的代码边界。独立安装、测试和目标机部署的完成证据分别记录，不能根据目录或锁文件存在认定服务已验收。冻结证据与研究提案的业务含义已确定，Hermes 适配器的完整实现仍随 S07 接入。

当前实际结构：

```text
youwei-trading-agent/
  youwei_core/              # API、Worker、数据、Ledger、评估及 Runner 客户端
  quant/                    # 无副作用的计算函数
  contracts/                # 独立轻量 Python 包，共享 DTO 与能力令牌
  services/
    sandbox-runner/         # 独立 Python 包、依赖锁与 HTTP 执行服务
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

`services/agent-runtime/`、`integrations/pi/`、`integrations/openwebui/` 和 `integrations/openviking/` 在对应功能实际接入时创建。当前数据逻辑继续位于 `youwei_core/data/`；有独立部署需求时再提取 Data Service。Core 保持原路径，避免单纯搬目录影响现有导入、构建和迁移。

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
| Hermes | 固定证据与研究权限；接收 ResearchProposal 后交 Controller 校验 | 已有独立 Python 3.14 适配与 Runtime 桥、冻结行引用预检；真实网关、平台工具授权与 Controller 封存接线仍待验收 |
| Pi | MVP 暂缓接入（Hermes 为唯一 Agent 框架）；若日后接入则负责受控 RPC、工具/扩展允许列表、将探索代码提交到执行链路 | 版本锁保留；无业务接入，Node/TypeScript 包与 RPC wrapper 不设于当前 MVP |
| Open WebUI | 作为可选界面消费 Core 身份、持久任务和研究结果接口 | S09 评估；不把其原生会话或执行状态作为 Core 权威 |
| OpenViking | 可选长文档派生检索适配器 | 尚未接入；事实与批准的研究记忆仍由 Core 管理 |

Core 的运行依赖不含 Runner；根 dev 组安装 Runner 仅用于集成测试。Runner 自有独立锁，Hermes 及以后实际接入的前端/检索组件分别固定其解释器、依赖锁、上游完整提交或镜像 digest；Pi 保留为可替换候选（版本锁不变，暂缓接入）。[上游管理](UPSTREAMS.md) 中的登记和校验工具绑定 Compose、镜像引用、验收报告与清单，不能代替契约、权限和端到端业务测试。

上游优先使用官方发布、配置和扩展能力；业务差异保留在本仓库适配层。必要能力确实无法通过这些入口实现时，再维护最小 fork，并登记上游基线、补丁、升级方式和相关验证。单业务仓库不意味着把 Hermes、Open WebUI、OpenViking 源码复制进 Core，也不要求现在创建尚无行为的接入目录。

## 4. 现有能力与后续范围

现有开发纵向切片覆盖持久任务、身份、PIT 日线与冻结快照、预测封存、Outcome 修订、最小评分、归档导出和 baseline/quant 管线。本次结构调整提取纯量化与共享契约，并把 Runner 的权限从 Worker 中分离；实际验证结果由实施计划记录。

以下能力仍需独立交付：外部 Agent 的正式研究链路、受控量化探索闭环（Hermes）、国内入口、受控研究记忆、正式数据总体和模型发布、生产权限与恢复验收。现有管线模型是实现验证载具，不因代码搬迁变为已批准研究模型。

SG 使用现有 DigitalOcean **4 vCPU / 7.8 GB** 主机，重计算从并发1起步；国内与 SG 的旧原型已归档移除。需要复用原型 Next.js 源码时，从 [目标机记录](research/s01-target-verification.md) 指定归档提取并重新验收，不能将旧运行状态算作新实现上线。

MVP 使用 PostgreSQL 保存当前规模的原始数据、冻结快照与受限产物，本地文件用于 Ledger 导出和 WAL/PITR 机制验证，**OSS 不在 MVP 范围**。同一管理权限下的文件与 hash 可以一起重写，本地归档不构成外部锚定或 WORM；独立恢复副本、归档调度与生产 RPO/RTO 由 S09 验收。
