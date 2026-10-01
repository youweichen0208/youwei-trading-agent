# S09a Core 镜像发布验收报告

日期：2026-10-02。环境：sg-prod（DigitalOcean droplet，Docker 29.5.2，linux/amd64）。

本报告记录 Phase 1A Core 镜像（`ghcr.io/youweichen0208/youwei-core`）的构建、推送与按 digest 拉取验证，作为 `upstreams.lock.yaml` 中 `core` 组件 `verification.status=passed` 的验收证据。本报告只证明镜像与引用一致，不替代生产部署健康检查、基础设施安全验收或人工批准（见 UPSTREAMS.md）。

## 构建

- Dockerfile：`infra/images/core.Dockerfile`
- 构建上下文：仓库根目录（`COPY pyproject.toml uv.lock contracts/ youwei_core/ quant/ migrations/ alembic.ini`）
- 入口：`youwei-api`（uvicorn API，绑 127.0.0.1:8000）与 `youwei-worker`（Scheduler + collect_tick）
- 依赖：`uv sync --frozen --no-dev --no-editable`，Python 3.13，不含 Runner（`--no-dev` 排除 sandbox-runner）

## 镜像引用

- 仓库：`ghcr.io/youweichen0208/youwei-core`
- tag：`phase1a`（仅作人类可读别名；正式部署引用固定 digest，不用 tag）
- **digest（manifest list）**：`sha256:7292c8538e70750ebbd3c278ebd8572563b342ca5593981c34fc8659b9bd0e3c`
- 单平台 manifest digest：`sha256:065bb7cfc5fa9c3687f2752c2d06bb84af877db3e87e2c55f25056d5981be1c8`

## 验证

1. **推送成功**：`docker push ghcr.io/youweichen0208/youwei-core:phase1a` → `digest: sha256:7292c8538e70750ebbd3c278ebd8572563b342ca5593981c34fc8659b9bd0e3c size: 856`。
2. **按 digest 拉取成功**（sg-prod）：`docker pull ghcr.io/youweichen0208/youwei-core@sha256:7292c8538e70750ebbd3c278ebd8572563b342ca5593981c34fc8659b9bd0e3c` → `Digest: sha256:7292c8538...`，`Status: Image is up to date`。
3. **Compose 校验**：`docker compose -f infra/compose/production.json config --quiet`（占位环境变量）通过。
4. **登记校验**：`python3 infra/validate_upstreams.py --mode catalog` → VALID。

## 剩余（不在本报告范围）

- 生产运行验收（S09b：PG/API/Worker/采集/租户登记闭环）
- 运维验收（S09c：远端恢复、重启恢复、资源测量、告警、RPO/RTO）
- 人工批准 release + Campaign（S06）
- 本报告不授予生产发布或 ResearchRelease 人工批准。
