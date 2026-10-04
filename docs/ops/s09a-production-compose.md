# S09a Phase 1A 生产 Compose 与部署准备

日期：2026-10-02。状态：**结构完成，待镜像发布后回填 digest**。本文件记录 Phase 1A 最小部署（PostgreSQL + Core API + Core Worker 含 Scheduler + 每日采集）的 Compose 设计、账号分离、待办与迁移/部署步骤。未启用 Runner、Hermes、网关或 CN 入口；这些在实际启用对应能力时部署。

## 部署单元（Phase 1A 最小）

| 服务 | 镜像 | 说明 |
| --- | --- | --- |
| `postgres` | `postgres:16-alpine@sha256:721873c3...` | 生产 PostgreSQL，独立持久卷；迁移/应用账号分离 |
| `core-api` | `ghcr.io/youweichen0208/youwei-core@sha256:<待发布>` | `youwei-api` 入口，绑定 `127.0.0.1:8000`（SSH 隧道访问） |
| `core-worker` | 同 Core 镜像 | `youwei-worker` 入口，含 Scheduler tick + `collect_tick` 每日采集 |

Runner 已从 Phase 1A 移除：Logistic/Ridge 直接在 Core 内执行；`runner_url` 为空时 Worker 不创建 Runner 客户端；Scheduler 已含在 Worker 内。生产 Compose 不设 `YOUWEI_RUNNER_URL`。

## 关键设计

- **镜像**：API 与 Worker 共用同一 Core 镜像（`youwei-api` / `youwei-worker` 两个 entrypoint）。生产引用固定为 `image@sha256:…`；镜像发布前 `core` 组件在 `upstreams.lock.yaml` 保持 `enabled:false`、`deployment.image:null`。
- **账号分离**：`infra/postgres/init/01-roles.sh` 创建 `youwei_migrate`（DDL，跑 Alembic）与 `youwei_app`（DML，API/Worker 连接）。应用不持 DDL 权限。
- **网络**：`core` 网络 `internal`（postgres/api/worker 内部）；worker 额外挂 `egress` 网络访问 Tiingo。API 无外网。
- **安全**：`read_only` 根文件系统 + `tmpfs` /tmp + `cap_drop ALL` + `no-new-privileges` + 资源限额（cpu/mem/pids）+ 日志轮转（json-file 10m×3）。
- **采集**：worker 通过 `YOUWEI_COLLECT_RELEASE_ID` / `YOUWEI_COLLECT_TENANT_ID` 启用 `collect_tick`（见 `youwei_core/data/collect.py`）。

## 待办（镜像发布后）

- [x] 构建 Core 镜像并推送 GHCR 私有仓库 `ghcr.io/youweichen0208/youwei-core`。（已完成 2026-10-02，digest `sha256:7292c8538e70750ebbd3c278ebd8572563b342ca5593981c34fc8659b9bd0e3c`）
- [x] 回填 `infra/compose/production.json` 的两个 `PLACEHOLDER_CORE_DIGEST` 为真实 digest。
- [x] `upstreams.lock.yaml` 的 `core` 组件补全 `deployment.image` digest + `bindings`（core-api/core-worker image）。
- [x] 将 `core` 组件置 `enabled:true`、`verification.status` 置 `passed`（附验收证据 `docs/ops/s09a-core-image-release.md`）。
- [x] 生成 deployment manifest（`infra/deployment-manifest.json`，含 `lock_sha256`/`compose_sha256`），跑 `infra/validate_upstreams.py --mode deployment` → **VALID**（2026-10-02）。

## 迁移与部署步骤（镜像发布后）

```bash
# 1. 起 PostgreSQL（首次 initdb 会跑 01-roles.sh 创建两账号）
#    注意：production.json 的挂载路径 ./postgres/init 相对 Compose 文件所在
#    目录解析，实际脚本位于 infra/postgres/init（已改为 ../postgres/init）。
docker compose -f infra/compose/production.json up -d postgres

# 2. 用迁移账号跑 Alembic（应用账号无 DDL 权限，不用于迁移）。
#    Compose 未把 PG 端口映射到宿主机（API/Worker 经 internal core 网络直连），
#    所以不能从宿主机 127.0.0.1:5432 连接；改用同一个 Core 镜像跑一次性迁移
#    容器，加入 core 网络连 postgres:5432。
export YOUWEI_MIGRATE_PASSWORD="<迁移账号密码>"
docker run --rm \
  --network youwei-production-phase1a_core \
  -e YOUWEI_DATABASE_URL="postgresql+asyncpg://youwei_migrate:${YOUWEI_MIGRATE_PASSWORD}@postgres:5432/youwei" \
  ghcr.io/youweichen0208/youwei-core@sha256:7292c8538e70750ebbd3c278ebd8572563b342ca5593981c34fc8659b9bd0e3c \
  alembic upgrade head

# 3. 起 API 与 Worker
docker compose -f infra/compose/production.json up -d core-api core-worker

# 4. 验证
curl http://127.0.0.1:8000/healthz
docker compose -f infra/compose/production.json ps
```

迁移容器说明：Core 镜像内含 `migrations/`、`alembic.ini` 与 `alembic` CLI
（pyproject 依赖），非 root（UID 10001）即可运行——Alembic 只写数据库，不写
镜像文件系统。容器用完即删（`--rm`），不残留运行态；密码经环境变量传入且只在
本次命令生命周期内存在。生产部署由 `ops/s09b_deploy.py`（或等价脚本）封装上述
步骤并管理凭证与受限 `.env`。

## 备份（pgBackRest，方案待目标落实）

备份目标尚未落实（优先复用已授权独立目标；否则 DO Spaces 私有存储）。同一 SG 主机上的其他卷或 WebDAV 容器不构成独立故障域。基础备份 + 连续 WAL 归档 + 加密远端 + 真实恢复验证按 S09c 落实；RPO/RTO 按目标机实测记录，不沿用本地小库演练数值。

## 未包含（后续阶段）

Runner / gVisor、Hermes 研究运行时、LLM 网关、CN 入口、Web 前端、多用户 RLS —— 均不在 Phase 1A 最小部署范围。
