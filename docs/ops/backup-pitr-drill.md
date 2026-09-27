# 备份与 PITR 演练记录（S02b）

日期：2026-09-27。环境：开发机 macOS（Docker Desktop），`postgres:16-alpine`，一次性容器对（源库 + 恢复库）。脚本：[ops/backup/pitr_drill.sh](../../ops/backup/pitr_drill.sh)。

## 结论

本地 WAL 归档 + 时间点恢复（PITR）机制验证通过：

- `archive_mode=on` + `archive_command`（cp 到本地归档目录）工作正常：演练期间归档 5 段、失败 0 次。
- `pg_basebackup -Fp -Xs` 基准备份 + 归档 WAL 可将集群恢复到指定的 `recovery_target_time`。
- 基准备份**之后**、目标时间**之前**提交的数据（marker B，只能来自归档 WAL 回放）恢复后在库；目标时间**之后**提交的数据（marker C）恢复后不在库——PITR 停点正确。
- 恢复库通过 `alembic current` = head，且应用读路径（`get_run_view`）正常返回预期行。

实测（小库，机制验证量级，非容量指标）：基准备份 <1s，恢复+回放到 promote 1s。

## 演练步骤（脚本自动执行）

1. 起源库容器：`wal_level=replica`、`archive_mode=on`、`archive_command='test ! -f /wal_archive/%f && cp %p /wal_archive/%f'`、`archive_timeout=30`。
2. 走真实迁移（alembic upgrade head）与真实提交路径（`submit_run`）种入 marker A，`pg_switch_wal()` 并等待归档。
3. `pg_basebackup -D /backup -Fp -Xs -c fast`（基准备份含 marker A）。
4. 种入 marker B（备份后提交）→ 记录 `recovery_target_time = clock_timestamp()` → 种入 marker C（目标时间后提交）→ 切换并等待归档。
5. 复制基准备份为恢复目录，写 `recovery.signal` 与 `postgresql.auto.conf`（`restore_command`、`recovery_target_time`、`recovery_target_action='promote'`），起新容器恢复。
6. 验证：alembic head；`runs` 表恰含 drill-A/drill-B、无 drill-C；应用读路径 A/B 可读、C 报 RunNotFound。

失败时脚本保留工作目录（含 wal_archive/backup/restore）供排查，容器一律清理。

## 生产部署要求（SG 核心库）

| 项 | 要求 |
| --- | --- |
| 归档 | `archive_mode=on`、`archive_command` 写入本地区备份目录（架构 §10：备份任务仅允许本地区备份桶）；目录权限归 postgres |
| RPO 上界 | `archive_timeout=60`；实际 RPO = archive_timeout + 归档延迟，架构目标 RPO ≤ 15 分钟 |
| 监控 | `/v1/ops/status` 的 `wal_archive` 段（enabled / archived_count / failed_count / last_archived_age_seconds）与 `wal_archive_stale` 告警（阈值 `YOUWEI_ALERT_WAL_ARCHIVE_STALE_SECONDS`，默认 1800s；归档开启但从未成功即告警） |
| 演练 | 目标机部署后重跑本脚本记录实测 RPO/RTO（架构目标核心 RTO ≤ 4 小时）；此后每次重大版本变更或至少每季度一次 |

## 已知限制（不在此演练范围）

- 磁盘水位为宿主层监控（ECS 节点级），随 S09 MVP 运维验收落实；应用侧 `/v1/ops/status` 无法观测宿主磁盘。
- 备份异地/对象存储传输、加密、定时编排（cron/systemd timer）、云盘快照策略与 Ledger 归档桶（S05）均未包含；OSS 已排除出 MVP 范围（见 S01 记录）。
- 本演练为机制与流程验证；容量下的备份时长、恢复时长与归档积压需在目标机以真实数据量重测后才可对外承诺 RPO/RTO。
