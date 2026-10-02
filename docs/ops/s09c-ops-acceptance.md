# S09c 运维验收（进行中）

日期：2026-10-02 起。环境：sg-prod（DigitalOcean，Docker 29.5.2，4 vCPU / 7.8 GB）。本记录随 S09c 推进分段补充；全部完成后在实施计划标记 S09c 状态。

## 一、磁盘治理（2026-10-02）

- 发现：根分区 82%（125G/154G，剩 29G）。定位：Docker 29 将镜像存储与构建缓存委托给系统 containerd（namespace `moby`，数据在 `/var/lib/containerd`），因此 `du /var/lib/docker`（7.3G，仅卷与容器运行态）与 `docker system df`（Images 95.4G + Build Cache 108.9G）并不对应。
- 清理：`docker builder prune -af` 回收 **108.9G** 构建缓存（历次镜像构建累积，纯缓存无风险）；删除 73 个未用匿名卷（一次性测试容器遗留，~4.2G；仅保留两个在用具名卷 `youwei-{production,acceptance}_postgres_data`，逐名核对未用后才删，未用全局 prune——同机有其他项目）。
- 结果：**82% → 14%（134G 可用）**。存量构成（未动）：/root/.vscode-server 2.7G、/root/.cache 1.7G、swap 4.1G、journal 1G（可后续 vacuum）、系统/应用 ~10G。
- 运维注意：磁盘水位为宿主层监控；docker 空间问题优先查 `docker system df` 与 `docker builder du`（containerd 委托后 du docker 目录不反映镜像占用）。

## 二、重启恢复验证（docker 级，2026-10-02）

- 流程：记录基线（healthz 200、105 行观测、21 任务 succeeded、restarts=0）→ `docker compose stop`（三容器）→ `start`。
- 结果：postgres 先经 healthcheck 依赖门控恢复（healthy）→ API/Worker 启动；healthz 200；**数据完好**（105 行/21 任务 succeeded，卷持久）；**采集配置保留**（`start` 不重建容器，容器 env 原样）；无错误日志。
- 观察项：worker `stop` 时 exit 137（默认 10s 宽限内未退出，被 SIGKILL）——任务租约机制按崩溃安全设计覆盖该场景（过期租约由 reaper 收割），无数据风险；如需优雅退出可后续为 compose 配 `stop_grace_period` 并在 worker 入口处理 SIGTERM。
- 未做：droplet 整机重启演练（会影响同机其他服务，待项目所有者确认）。

## 三、升级/回滚兼容性 drill（r2→r1→r2，2026-10-02）

- 流程：部署 compose 镜像引用切回 r1 digest `55678d52...` → `up -d`（仅重建 core-api/core-worker）→ 验证 → 切回 r2 `53631663...` → `up -d` → 验证。
- 结果：r1 与 r2 对当前生产库均健康（healthz 200、105 行/21 任务不变、服务 healthy）——两版无 schema 差异（同一 alembic head `f9a0b1c2d3e4`），回滚路径可用。执行窗口选在采集静默期（数据齐全、无 Tiingo 调用），r1 的 token-入-日志缺陷未实际暴露。
- 仓库部署文件保持 r2（drill 只改部署目录的 compose，已恢复）。

## 四、缺陷修复：采集配置不持久（drill 中发现）

- 现象：`docker compose up -d` 在镜像变化时重建容器，重建时环境取自当前 shell 插值——未导出 `YOUWEI_COLLECT_RELEASE_ID`/`_TENANT_ID` 时新容器采集被**静默禁用**（回滚 drill 的两步重建均触发；靠事后 env 检查发现）。
- 修复：
  - `ops/s09b_deploy.py` 的 `compose_env` 增加按环境 `BASE/<env>/config.env`（非密钥配置，`setdefault` 合并——显式 shell 导出仍可覆盖）；优先级：shell 导出 > config.env > 仓库 .env > 默认空。
  - SG 写入 `/opt/youwei/production/config.env`（0600，采集 release/tenant 两行）；acceptance 无该文件、保持空（避免验收 worker 对无 release 的库反复报错）。
- 验证：`compose_env(\"production\")` 解析出采集配置与 Tiingo token、`compose_env(\"acceptance\")` 采集为空；`--force-recreate core-worker` 不带导出重建后容器内采集配置在位；healthz 200、数据完好。
- 运维规则（记录）：生产任何 `up -d`/重建后必须核验 worker env（采集配置 + token）；采集静默跳过不产生日志，禁用不会自报。

## 五、pgBackRest 自定义镜像（2026-10-02，已发布待接入）

- 镜像：`infra/images/postgres-pgbackrest.Dockerfile`——pinned `postgres:16-alpine@sha256:721873c3...`（PostgreSQL 16.15，musl）基底 + 源码编译 pgBackRest **2.59.2**（meson/alpine 3.24 构建阶段）。**同基底同 libc**：容器换镜像不变 collation，存量 PGDATA 无需 dump/restore（切 glibc 基底则需要）。
- 新增运行时库（alpine 3.24，随镜像 digest 固定）：`libbz2 1.0.8-r6`、`xz-libs 5.8.4-r0`、`yaml 0.2.5-r2`；libpq 用基底自带 `/usr/local/lib/libpq.so.5`。镜像 428MB（基底 420MB + 8MB）。
- 构建时验证：ldd 无缺失库、`pgbackrest --version`、`postgres --version` 一致。
- 已推送 GHCR：`ghcr.io/youweichen0208/youwei-postgres:phase1a-s09c`，**registry digest（OCI index）`sha256:8d69232c1d2b7771ae8f9dbb04cd56bbd6334972f37e90956f6a5bad05382053`**，按 digest 拉取验证通过。
- 未接入生产（upstreams.lock 的 postgres 组件待生产换镜像时更新）；接入时 postgres 服务需重启（archive_mode 是启动参数）。

## 六、pgBackRest 备份/恢复/PITR 演练（posix repo，2026-10-02）

脚本：[ops/backup/pgbackrest_drill.sh](../../ops/backup/pgbackrest_drill.sh)（SG 上通过）。与 S02b pitr_drill 同一 marker 证明结构（A 在备份内、B 在备份后目标前、C 在目标后），机制换为 pgBackRest：

- 源库：自定义镜像，`archive_mode=on` + `archive_command='pgbackrest archive-push'`（共享 socket 目录 /socket 供 backup/check 命令连接；pg1-user/pg1-database 需匹配集群超管名）。
- 验证链：stanza-create → `check`（归档往返）→ 真实迁移（alembic head `f9a0b1c2d3e4`）+ 真实提交路径种子（submit_run）→ 全量备份 → marker B/C → WAL 经 pgBackRest 归档（`pg_stat_archiver` 7 archived）。
- **PITR 恢复**（目标时间）：fresh 目录 `restore --type=time --target-action=promote` → A+B 在、C 不在、alembic head ✓。
- **全量丢失恢复**（无 target）：A+B+C 全在 ✓。
- 实测（31MB 小库，机制级非容量级）：全量备份 **30s**（4MB 备份集）；PITR 恢复+回放+promote **9s**。
- 演练观察：initdb 阶段的临时服务器也会应答 `pg_isready`，就绪等待必须用真实库连接（脚本已修）；stanza-create 前的 WAL 归档失败属预期（repo 路径未建，postgres 重试后成功）。
- repo 类型为 posix（本地目录）；**S3 repo 仅差 `[global]` 配置**，待备份目标决策后验证。

## 七、告警外发轮询（2026-10-02，通道待定但框架已上线）

- 脚本：[ops/ops_status_poll.sh](../../ops/ops_status_poll.sh)，部署于 SG `/opt/youwei/production/ops_status_poll.sh`；root cron 每 5 分钟（`*/5 * * * *`，日志 `/var/log/youwei-alert-poll.log`）。
- 行为：每轮心跳写入 `alert-state/last-poll`；**alert 集变化时投递一次**（新增或解除，稳态不重复）；API 不可达本身作为 `api_unreachable` 告警（fail closed）；每日一条 ok 心跳到 `/var/log/youwei-alerts.log`。
- 通道：`/opt/youwei/production/alert-channel.env` 里 `YOUWEI_ALERT_WEBHOOK_URL` 配置后 POST `{"text":...}`（通道定后可调 payload）；未配置时投递到本地告警日志。
- 验证：真实 API ok 路径（心跳+无重复）；stub 触发 `wal_archive_stale` → 投递+状态更新 → 真实运行解除 → 投递 RESOLVED，两态转换各一次。

## 八、生产备份接入（2026-10-02，posix repo 同机副本）

- 范围声明：本节把备份/恢复机制接入生产（同机 posix repo）——防应用级数据丢失（误迁移、误删表、库损坏），**不满足独立故障域**（宿主机丢失不防护）；异地副本（S3/已有目标）待备份目标决策，仅差 `[global]` repo1-* 配置。
- 接入内容：
  - `infra/pgbackrest/pgbackrest.conf`（仓库内配置，驱动部署到 `/opt/youwei/<env>/pgbackrest/`；pg1-user=youwei_owner）；repo/socket/log 运行目录由驱动 `init` 创建（uid 70）。
  - production.json：postgres 镜像 → `youwei-postgres@sha256:8d69232c...`（§五）；`archive_mode=on` + `archive_timeout=60`（S02b 生产要求）+ 共享 socket 目录 + repo/log/conf 挂载；core-worker 加 `init: true`（修 PID1 信号问题，§二——worker 容器内现为 docker-init 作 PID1，SIGTERM 可这达）。
  - `s09b_deploy.py`：`_fix_postgres_mount` 泛化为 `_fix_bind_paths`（../postgres/init 与 ../pgbackrest/* 两类）；`init` 生成 pgbackrest 目录+配置；新增 `pgbackrest` 子命令（一次性容器跑 stanza-create/check/backup/info，免密钥——共享 socket + 数据卷，输出回显且过脱敏）。
- 演进切换（生产 PGDATA 免 dump/restore：同基底同 libc）：`up` 重建 postgres（新镜像+归档参数）与 worker（init:true），api 未动且全程 healthz 200（`pool_pre_ping` 生效）；数据完好（105 行/21 任务）；采集配置经 config.env 保留。
- 初始化：stanza-create → check → 首个全量备份 **20261002-022932F**（7s，32.9MB→4.3MB）。
- 监控生效：`/v1/ops/status` `wal_archive` 段 `enabled=true`（archived 4 / failed 0，last_archived_age 秒级）——S02b 要求的 WAL 归档告警接线至此在生产真实生效（`wal_archive_stale` 阈值 1800s）。
- 定时任务（SG root cron）：每日 11:15 UTC 全量备份（日志 `/var/log/youwei-pgbackrest.log`）；每周日 12:15 UTC `check`；告警轮询每 5 分钟（§七）。保留策略 `repo1-retention-full=7`。
- 观察项：`archive_timeout=60` 并不产生空段垃圾——PG 的 XLogArchiveTimeout 仅在**有新 WAL** 时才强制切换；空闲期 3 分钟零增长、archived_count 不变。即：活跃期 RPO ≤ ~1 分钟，空闲期零浪费。repo 现今 14MB（4.3MB 备份 + ~4MB 压缩 WAL + 结构）。

## 九、剩余（待决策/待做）

- 备份目标决策后：S3（或已有目标）repo 配置 + 演练（同 §六流程 + 异地恢复验证）+ 驱动/配置更新；独立故障域要求至此闭环。
- 告警通道决策后：写 `/opt/youwei/production/alert-channel.env`（如需非通用 payload 一并调整脚本）。
- droplet 整机重启演练：待项目所有者确认（影响同机 webdav 等服务）。
- 资源测量：静默采集期基线已录；采集高峰与首批 Campaign 负载下复测。
- RPO/RTO：机制级已录（§六）；生产配置下的定期恢复演练（每季度/重大变更，S02b 要求）自本接入起算。
