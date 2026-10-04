# S09b 生产启动与 r2 镜像发布（token 日志缺陷修复）

日期：2026-10-02（Asia/Shanghai；SG 为 UTC 2026-10-01 深夜至 10-02 凌晨）。环境：sg-prod（DigitalOcean droplet，Docker 29.5.2，linux/amd64）。

本记录覆盖 S09b 收尾：GHCR 凭证恢复后的镜像发布、生产 API/Worker 启动、采集启用，以及启动后发现的 token 入日志缺陷的修复与 r2 镜像重新发布。本文是 `upstreams.lock.yaml` 中 `core` 组件 `verification.status=passed` 的验收证据（最终部署镜像 = r2）。deployment 校验结果记录于实施计划 S09b 进度；本报告不构成 release 批准或正式 Campaign 授权。

## 一、首次启动（r1 镜像 `55678d52`）

1. GHCR 凭证恢复（用户提供新 token）：经 stdin 传入 `docker login ghcr.io`，不落命令行、日志、仓库或任何文档；SG `/root/.docker/config.json` 持有 base64 auth（与 S09a 既有方式一致）。
2. 推送 `phase1a-s09b`（image ID `sha256:55678d52...`）→ registry digest（OCI index）`sha256:55678d52580fea7cf51e4a6093d8cfff4ed55c9f86a7aa4c142b9fb3de6bee80`，按 digest 拉取验证通过（详见 [s09b-acceptance.md](s09b-acceptance.md) §九）。
3. 部署文件按 r1 digest 更新（production.json / upstreams.lock.yaml / deployment-manifest.json），SG 渲染校验 deployment VALID（渲染流程见 §五）。
4. `/opt/youwei/production/compose.json` 与新生成内容差异仅镜像 digest，按驱动设计显式删除后 `ops/s09b_deploy.py --env production init` 重新生成；以 `YOUWEI_COLLECT_RELEASE_ID=release-logistic-ridge-candidate-20261002-v1`、`YOUWEI_COLLECT_TENANT_ID=f497c122-45b6-497b-bb99-9c42401c3e5f` 启动 `up`（生产库已迁移+导入，不重复迁移；Tiingo token 来自 SG 仓库 `.env`）。
5. 验证：`/healthz` 200；worker 在采集窗口内（ET 17:30–次日 05:30）真实采集——21 对象（20 panel + SPY）× 最近 5 交易日（2026-09-25 → 10-01）全部 HTTP 200，`price_observations` 105 行、采集任务 21 个全部 `succeeded`；内存富余（约 6.3 GB available）。

## 二、缺陷发现：token 进入 worker 日志

- 现象：httpx 在 INFO 级打印完整请求 URL，而 `TiingoClient` 当时把 token 作为 `token` query 参数传递 → 真实 Tiingo token 明文出现在容器日志（违反「日志须脱敏」约束）。
- 影响评估：docker json-file 日志位于 root-only 路径，暴露面限于同机 root 可读；`raw_objects.query` 存规范化查询（ticker + 日期，无 token），数据库与 `s09b-acceptance.md` 引用的日志摘录（`?...` 省略）均不含 token。
- 修复：改用 `Authorization: Token <key>` 头传递（[tiingo-token-verification.md §8](../research/tiingo-token-verification.md)）。真实调用验证：有效 token 头 → 200（SPY 2026-09-30 close=762.63，与既有实测一致）；伪造头 → 403 `{"detail":"Invalid token."}`，证明头被真实校验。mock 服务器（`ops/s09b_mock_tiingo.py`）本就不校验 token，验收环境不受影响。
- 测试：`tests/test_data_tiingo.py` 断言更新为头认证 + URL 无 token，并新增 httpx 日志不泄漏回归测试；SG 真实 PG 运行 `tests/test_data_tiingo.py tests/test_data_collect.py` → **31 passed**。

## 三、r2 镜像发布（`phase1a-s09b2`）

- 构建：SG 上以同一 Dockerfile（`infra/images/core.Dockerfile`）+ 修复后源码重建，tag `phase1a-s09b2`，本地 manifest list digest `sha256:53631663f04096f3bcb19d4ee14d1c5598785f1bb39a12c75a736691d9cc1848`。
- 镜像内验证：一次性容器（`--network none`）`inspect.getsource(TiingoClient.daily_prices)` 确认含 Authorization 头、无 query token。
- 推送：`docker push ghcr.io/youweichen0208/youwei-core:phase1a-s09b2` → **registry digest（OCI index）`sha256:53631663f04096f3bcb19d4ee14d1c5598785f1bb39a12c75a736691d9cc1848`**（与本地构建一致）；按 digest 拉取验证 `Status: Image is up to date`。
- r1（`55678d52`）保留在 registry 为历史；生产 Compose / upstreams.lock.yaml / deployment-manifest.json 均已按 r2 digest 更新。

## 四、重新部署与最终验证

1. production.json 更新为 r2 digest → 同步 SG → `/opt/youwei/production/compose.json` 差异仅镜像 digest，显式删除后重新 init → 以同一采集配置 `up`。容器重建同时删除了 r1 旧容器及其含 token 的日志文件。
2. 验证（2026-10-02 UTC ~00:00）：
   - 三服务 Up、core-api healthy、`/healthz` 200、worker `restarts=0`、进程在跑（`youwei-worker`）。
   - 新 worker 日志 `token=` 出现次数 = **0**。
   - DB：`price_observations` 105、采集任务 21 全部 `succeeded`、`campaigns` 0、`release_approvals` 0（约束保持：本轮不产生批准或 Campaign）。
3. 采集行为说明：数据已齐全时 `collect_tick` 按 `missing_trading_days` 静默跳过（无日志、不建任务，设计行为）；后续自然确认点为下一交易日 ET 17:30 首采——届时将以头认证真实调用，日志 URL 不含 token。
4. 当前 SG 同时运行 production 与 acceptance 两套环境（独立 project/卷/网络/凭证），内存充足；acceptance 环境的留存/拆除随 S09c 决定。

## 五、deployment 校验渲染流程（可复现）

```bash
# SG /root/youwei-trading-agent（渲染含绝对路径，绑定该目录）
env YOUWEI_POSTGRES_OWNER_PASSWORD=placeholder \
    YOUWEI_POSTGRES_MIGRATE_PASSWORD=placeholder \
    YOUWEI_POSTGRES_APP_PASSWORD=placeholder \
    YOUWEI_ADMIN_API_KEY=placeholder \
    YOUWEI_CAPABILITY_SECRET=placeholder \
    YOUWEI_TIINGO_TOKEN=placeholder \
  docker compose -f infra/compose/production.json config --format json \
  > /root/youwei-rendered/production.compose.json   # 0700 目录 / 0600 文件，不入库
sha256sum /root/youwei-rendered/production.compose.json   # -> deployment-manifest.compose_sha256
python3 infra/validate_upstreams.py --mode deployment \
  --compose /root/youwei-rendered/production.compose.json \
  --manifest infra/deployment-manifest.json
```

占位值只用于渲染（`:?` 必填项），真实凭证只在 `/opt/youwei/secrets/`（0600）与仓库 `.env`；渲染文件不含真实秘密（已逐项核对全部环境值为 placeholder/结构值/空）。

## 六、剩余

- S09c 运维验收：远端恢复、重启恢复、资源测量、告警、RPO/RTO；pgBackRest 备份目标落实。
- 组装批准包（release `bdb8bbe0...` + 计划 `d403c8e5...` + scope_manifest `0b899b00...` + 使用范围 + 验收证据）交项目所有者一次性确认。
- GHCR token 已入 SG docker 配置；其轮换/撤销策略随运维流程（S09c）确定。
