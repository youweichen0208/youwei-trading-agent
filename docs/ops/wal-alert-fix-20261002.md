# WAL 归档告警判定修复（积压驱动替代 wall-clock）

日期：2026-10-02。范围：Core `/v1/ops/status` 的 `wal_archive_stale` 告警语义、生产部署（core 镜像 r3）与 `pg_monitor` 授权。不改变备份/恢复/PITR 语义。

## 决策（所有者 2026-10-02）

旧规则以"距上次成功归档的时间"超过阈值（1800s）为告警条件。空闲库不产生 WAL，`archive_timeout` 不触发切换，告警在生产空闲库上**持续误报**（2026-10-02 11:55 UTC 起 `wal_archive_stale` 常挂；只读核查：已归档 51 段、`failed_count=0`、待归档 `.ready` 为 0）。

所有者决策：**修正判定逻辑**，不调大阈值、不接受持续误报。

- 主告警条件：归档已启用 **且** 存在待归档 WAL **且** 最老待归档文件等待超过 1800 秒。
- `last_archived_age` 保留为诊断信息，单独过期不报警。
- LSN、归档成功/失败计数辅助诊断，快照带 `stats_reset`（计数重置可解释）。
- 指标读取失败报告**监测异常**（`wal_archive_monitor_error`），不能当作积压为零。

## 实现（commit 27c7f3d）

`youwei_core/ops/service.py`：

- `ops_snapshot`：WAL 块改为单一守护块——`pg_stat_archiver`（含 `stats_reset`）+ `pg_ls_archive_statusdir()`（`.ready` 文件数与最老 `modification` 年龄）+ `pg_current_wal_lsn()`；任何读取失败产出 `monitor_error` 快照（不拖垮整个 ops status）。
- `evaluate_alerts`：`monitor_error` → `wal_archive_monitor_error`；否则 `enabled` 且 `pending_wal_files > 0` 且（最老等待为 None 或 > 阈值）→ `wal_archive_stale`（读不到等待时长按超时处理，绝不视为新鲜）。空闲库（无待归档）无论上次归档多久前都健康。
- `config.py`：`alert_wal_archive_stale_seconds` 注释更新为积压语义（值保持 1800s）。

权限：`pg_ls_archive_statusdir()` 需 `pg_monitor`（`pg_stat_archiver` 与 `pg_current_wal_lsn()` 应用角色本可读）；部署时 `GRANT pg_monitor TO youwei_app`（只读监控角色，无写权限扩展）。

## 测试（TDD，先红后绿）

`tests/test_ops.py`：空闲不告警（旧规则的失败案例）、宽限期内积压不告警、积压超阈值告警、恢复后停止告警、积压但等待时长未知保守告警、disabled 不评估、监测异常自成告警且绝不读作健康；集成测试断言新快照字段（测试容器 archiving off：`enabled=False`、`pending_wal_files=0`）。本机全量 `uv run --frozen pytest -q` → **590 passed, 1 skipped**（skip 为既有沙箱 docker-CLI 环境限制，与本改动无关）。

## 部署（2026-10-02，sg-prod）

1. 源码 rsync 至 `/root/youwei-trading-agent`（md5 双端核对），SG 构建 `infra/images/core.Dockerfile` → tag `phase1a-r3`。
2. 推送 GHCR：registry digest（OCI index）`sha256:71d15917270c9eda7c8bcf5cc906c7e21222780eac23313bb188c7b09c33f2d6`，按 digest 拉取验证 `Image is up to date`。
3. `GRANT pg_monitor TO youwei_app`（`pg_has_role` 复核 t）。
4. 生产 compose 更新 core-api/core-worker 引用至新 digest；`s09b_deploy.py --env production up` 滚动更新。

### 事故记录（如实）

首次部署用仓库 `infra/compose/production.json` **整文件覆盖**了 `/opt/youwei/production/compose.json`，而部署副本的挂载路径与仓库版不同（部署副本 `./pgbackrest/...`、`./postgres/init`；仓库版 `../pgbackrest/...`——相对 `infra/compose/`）。覆盖后 postgres 挂载到错误目录（`/opt/youwei/postgres/...`），`/socket` 不可写 → PG `FATAL: could not create lock file` 重启循环，**生产 PG 中断约 2 分钟（14:29–14:31 UTC）**。轮询 fail-closed 如实捕获（`api_unreachable` 14:30:01）。

恢复：还原部署副本（备份 `compose.json.bak-20261002-walfix`），仅以 sed 替换两个 core digest 字符串，`up` 重建后 postgres healthy（WAL 归档恢复推送，`archived_count` 51→52）、core-api/worker 上新镜像。

**教训（操作规程）**：SG 部署副本与仓库 compose 存在路径适配差异，更新部署副本时**只做字段级替换，禁止整文件覆盖**；该差异本身是否收敛为部署脚本统一改写，归后续部署工作评估。

## 部署后验证（2026-10-02 实测）

- `/v1/ops/status`：`status: ok`、`alerts: []`——`wal_archive_stale` 解除；快照 `enabled=true, pending_wal_files=0, oldest_pending_age_seconds=null, archived_count=52, failed_count=0, last_archived_age_seconds≈164（诊断）, stats_reset 与 current_lsn 在位，无 monitor_error`（`pg_monitor` 授权生效）。
- 轮询：`last-alerts` 清空，告警日志 `14:30:01 api_unreachable`（事故捕获）→ `14:32:34 alerts RESOLVED`。
- Worker：`youwei-worker` 进程运行（调度循环活动、`restarts=0`、队列 0 积压）；安静无日志为常态（无任务时不输出）。
- 告警投递：webhook URL 仍按所有者决策搁置（S09c），当前仅写 `/var/log/youwei-alerts.log`。

## 剩余

- 首个真实积压场景（归档管道真故障）的告警触发尚未自然发生（演练可用 `archive_command` 故意失败模拟——未执行，避免扰动生产归档）。
- 部署副本/仓库 compose 路径差异的收敛（见事故教训）。
- r2 digest `sha256:53631663…` 留存 registry 作历史。
