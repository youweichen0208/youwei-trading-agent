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

## 五、剩余（待决策/待做）

- pgBackRest：自定义 postgres 镜像（pinned 基底 + 源码编译 pgbackrest，保持 musl 基底不变避免 collation 迁移）、备份/恢复/PITR 演练（repo 类型待定：DO Spaces S3 / 已有独立目标 / 先本地后异地）、生产启用（WAL 归档 + 定时备份 + `/v1/ops/status` wal_archive 生效）。
- 告警外发：`/v1/ops/status` 阈值评估已就绪、无投递通道；通道待定（webhook/邮件/其他）。
- droplet 整机重启演练：待项目所有者确认（影响同机 webdav 等服务）。
- 资源测量：当前为空闲/静默采集期基线（worker 130MiB/768M、api 59MiB/384M、pg 36MiB/1G、内存 available 6.3G）；采集高峰与首批 Campaign 负载下复测。
- RPO/RTO：随备份演练记录实测值。
