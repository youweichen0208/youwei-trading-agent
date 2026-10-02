# S09c Postgres+pgBackRest 镜像发布验收报告

日期：2026-10-02。环境：sg-prod（DigitalOcean droplet，Docker 29.5.2，linux/amd64）。

本报告记录 Phase 1A 生产 PostgreSQL 自定义镜像（`ghcr.io/youweichen0208/youwei-postgres`）的构建、推送与按 digest 拉取验证，及其生产接入，作为 `upstreams.lock.yaml` 中 `postgres` 组件 `verification.status=passed` 的验收证据。本报告只证明镜像与引用一致及备份机制接入有效，不构成 release 批准或正式 Campaign 授权；运维验收的完整进行记录见 [s09c-ops-acceptance.md](s09c-ops-acceptance.md)。

## 构建

- Dockerfile：`infra/images/postgres-pgbackrest.Dockerfile`（多阶段；构建上下文 `infra/images`）
- 基底：官方 `postgres:16-alpine@sha256:721873c34ceb9f8d8fc265984940dc982404c105f19ad51be9fdc5970a6080ea`（PostgreSQL **16.15**，musl/alpine）
- pgBackRest：源码编译 **2.59.2**（github.com/pgbackrest/pgbackrest，tag release/2.59.2，meson + alpine 3.24 构建阶段，与基底同 alpine 版本）
- 新增运行时库（alpine 3.24）：`libbz2 1.0.8-r6`、`xz-libs 5.8.4-r0`、`yaml 0.2.5-r2`；libpq 用基底自带 `/usr/local/lib/libpq.so.5`
- 镜像 428MB（基底 420MB + ~8MB）；postgres 二进制与基底逐字节同源，**同 libc/collation**——存量 PGDATA 换镜像无需 dump/restore
- 构建时自检：ldd 无缺失库、`pgbackrest --version` = 2.59.2、`postgres --version` = 16.15

## 发布与验证

- 推送：`docker push ghcr.io/youweichen0208/youwei-postgres:phase1a-s09c` → `digest: sha256:8d69232c1d2b7771ae8f9dbb04cd56bbd6334972f37e90956f6a5bad05382053 size: 856`
- **registry digest（OCI index）：`sha256:8d69232c1d2b7771ae8f9dbb04cd56bbd6334972f37e90956f6a5bad05382053`**
- 按 digest 拉取验证：`Status: Image is up to date`（sg-prod）
- 离线冒烟：`--network none` 容器内 `pgbackrest --version`、`psql --version` 正常

## 备份机制验收（posix repo）

隔离演练（[ops/backup/pgbackrest_drill.sh](../../ops/backup/pgbackrest_drill.sh)）：stanza-create/check、全量备份（31MB→4MB）、时间点恢复（目标后提交不入库、alembic head、应用读路径可用）、全量丢失恢复（全部提交在库）。生产接入（2026-10-02）：postgres 容器平滑换镜像（api 零中断、数据完好），`archive_mode=on`/`archive_timeout=60`，首个生产全量备份 7s（32.9MB→4.3MB），`/v1/ops/status` `wal_archive.enabled=true`（真实归档计数与秒级新鲜度），每日全量 + 保留 7 + 周度 check 由 cron 驱动。

## 范围声明

posix repo 为同机副本：防应用级数据丢失，不构成独立故障域；异地副本为项目所有者已决策**暂缓**（2026-10-02）。RPO/RTO 为机制级数值（小库）；容量级与异地恢复演练不在本报告。
