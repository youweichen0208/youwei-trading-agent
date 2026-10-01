# S09b 隔离验收（第一轮：合成数据闭环）

日期：2026-10-02（Asia/Shanghai）。环境：sg-prod（DigitalOcean droplet，Docker 29.5.2，linux/amd64，4 vCPU / 7.8 GB）。

本报告记录 S09b 在 SG 上把 PG/API/Worker/采集真正跑起来的第一轮验收：合成数据、固定供应商响应，验证权限、任务、重试、幂等、失败处理。真实 Tiingo 采集（第二轮）与正式库初始化、候选导入、tenant 固定（S09b 其余部分）另行记录。

## 一、S09a 部署缺陷修复（5 处）

S09a 交付的生产 Compose / init 脚本 / 入口存在 5 处工程缺陷，本轮逐一修复并验证：

| # | 缺陷 | 修复 | 验证 |
| --- | --- | --- | --- |
| 1 | `production.json` 挂载路径 `./postgres/init` 相对 compose 文件位置解析不到脚本 | 改为 `../postgres/init`（`infra/compose/` → `infra/postgres/init`） | `docker compose config` 解析为 `infra/postgres/init`（绝对路径） |
| 2 | `01-roles.sh` 的 `:'password'` 在 `DO $$…$$` 块内不插值 | 改用 `\gexec` + `format('%L', :'var')` | 真实 PG 容器验证：`youwei_migrate`/`youwei_app` 角色创建、密码登录、DB owner 正确 |
| 3 | 迁移连接 `127.0.0.1:5432`（PG 未映射宿主端口） | 一次性 Core 容器连 internal `core` 网络 `postgres:5432`，用 `youwei_migrate`（DDL）跑 `alembic upgrade head` | 实际执行：33 表、`alembic_version`=`f9a0b1c2d3e4`（最新迁移） |
| 4 | `youwei-api` 监听 `127.0.0.1`（`youwei_core/api/main.py:15`） | 改为 `0.0.0.0` | 新镜像内确认 `host="0.0.0.0"`；宿主经 `127.0.0.1:8001` 访问 `/healthz` 返回 200 |
| 5 | `core-api` 仅在 `internal: true` 的 `core` 网络，容器端口无法发布到宿主 | 加非 internal 的 `edge` 网络（对齐 development 冒烟经验） | `docker port` 显示 `8000/tcp -> 127.0.0.1:8001`；宿主 curl 成功 |

第 5 处是 S09a production Compose 的遗漏：`runner-compose-smoke.md` 已记录「API 仅接 internal core 网络时宿主端口发布失败，需加普通 edge 网络」，但 S09a 的 production compose 未应用该教训。

## 二、镜像标识分类（本地构建，未发布 registry）

重新构建的 Core 镜像（tag `phase1a-s09b`）目前**只在 SG 本地**，未 push 到 GHCR（push 凭证已撤销/失效，见 §四）。三类标识必须区分：

| 标识 | 值 | 性质 |
| --- | --- | --- |
| image ID | `sha256:55678d52580fea7cf51e4a6093d8cfff4ed55c9f86a7aa4c142b9fb3de6bee80` | 本地 config digest（`docker image inspect .Id`） |
| 平台 manifest digest | `sha256:0c1f2b094e512af4000b3d9c2d6349b316d06719c84a0c51316839f7f3977e28` | 构建输出 `exporting manifest`（amd64） |
| config digest | `sha256:f65d29fbd772be9241b0e04e3300425a5804381b2c6595d6ef4e2df9a0467a7f` | 构建输出 `exporting config` |
| 索引（manifest list）digest | `sha256:55678d52580fea7cf51e4a6093d8cfff4ed55c9f86a7aa4c142b9fb3de6bee80` | 构建输出 `exporting manifest list`（单平台 buildx 时与 image ID 相同） |

`RepoDigests` 里的 `ghcr.io/youweichen0208/youwei-core@sha256:55678d52...` 是**本地推断 digest**（由 tag 派生），**不是 GHCR registry digest**——`docker manifest inspect` 对该 tag 报 `denied`（未 push）。因此：

- 当前**没有可拉取的 registry digest**，不能把 `55678d52...` 当作 GHCR 可拉取引用。
- 生产 Compose 里的 `sha256:7292c853...` 是 S09a 推送的 registry digest，但现在 pull 也 `denied`（凭证失效）。

对比：`postgres:16-alpine@sha256:721873c3...` 是 Docker Hub registry digest，`RepoDigests` 确认为 `postgres@sha256:721873c3...`（可拉取）。

**构建来源**：源码基准 + 修改内容。当前改动（4 处缺陷修复 + 新增 `ops/s09b_*.py`）**尚未提交**（SG 副本非 git 仓库），因此不能仅以 HEAD 代表此镜像。镜像源码版本 = 本地工作区（未提交 diff 见下）。

## 三、部署目录与凭证

- 部署目录 `/opt/youwei/`：`production/` 与 `acceptance/` 两个环境，各自含 compose.json + `postgres/init/01-roles.sh` + 独立 `.env`（0600）。
- 凭证目录 `/opt/youwei/secrets/`（0700），每环境独立：PG 三密码、admin bootstrap key、capability secret，每项 32 字节随机 hex，首次生成后续复用，脚本不覆盖。
- 验收 API 绑 `127.0.0.1:8001`（生产为 `127.0.0.1:8000`），验收 worker 的 Tiingo base_url 指向 mock 边车。
- API/Worker 以 `youwei_app`（DML）连接；迁移步骤单独用 `youwei_migrate`（DDL）。
- 部署驱动：`ops/s09b_deploy.py`（init / migrate / up / down / status），`REPO`/`BASE` 可经环境变量覆盖。

## 四、GHCR 凭证状态

SG 上 `.docker/config.json` 的 ghcr.io `auth` 已失效：`docker push` 与 `docker pull`（含旧 digest `7292c853`）均报 `denied`。**本地隔离验收可继续**（用本地镜像 + `pull_policy: never`）；**新镜像正式发布待 GHCR 凭证恢复**。恢复后：用 write:packages 登录推送、取得并验证 registry digest、更新生产 Compose / upstreams.lock.yaml / deployment-manifest，再按 digest 拉取验证。正式 Campaign 与 release 批准仍按原流程执行。

**2026-10-02 更新：凭证已恢复，推送与拉取验证完成（见 §九）。**凭证经 stdin 传入 docker login，不落命令行、日志、仓库或本报告。

## 五、第一轮验收：合成数据闭环（12/12 通过）

验收 harness：`ops/s09b_acceptance.py`（在一次性 Core 容器内跑，连 core 网络；`ops/s09b_mock_tiingo.py` 提供固定供应商响应）。结果：

```
PASS  admin creates tenant
PASS  admin creates api key
PASS  no key -> 401
PASS  security created (TEST1)
PASS  submit run (201)
PASS  run reached terminal
PASS  idempotent replay (200)
PASS  idempotent same run
PASS  synthetic bars ingested
PASS  failure path reached terminal
PASS  FAIL ticker recorded failure
PASS  cross-tenant read -> 404
```

Worker 日志佐证真实执行（非替身）：

- TEST1：`GET http://mock-tiingo:8080/daily/TEST1/prices?... "HTTP/1.0 200 OK"` → 入库（`price_observations` 有行）。
- FAIL：`GET .../daily/FAIL/prices?... "HTTP/1.0 500 Internal Server Error"` → `handler failed ... tiingo daily/FAIL HTTP 500` → run `failed`。

DB 状态：TEST1 run `succeeded`、FAIL run `failed`，两者 `attempt_count=1, max_attempts=1`（确定性失败不无限重试）。

## 六、验证命令摘要

```bash
# 部署（SG 上）
python3 ops/s09b_deploy.py --env acceptance init
python3 ops/s09b_deploy.py --env acceptance migrate   # 起 PG + 真实 Alembic
python3 ops/s09b_deploy.py --env acceptance up

# 验收 harness（一次性 Core 容器，连 core 网络）
docker run --rm --network youwei-acceptance_core \
  -v .../ops/s09b_acceptance.py:/harness/s09b_acceptance.py:ro \
  -e YOUWEI_ADMIN_API_KEY=... -e YOUWEI_APP_DB_URL=... \
  ghcr.io/youweichen0208/youwei-core:phase1a-s09b \
  python /harness/s09b_acceptance.py --api http://core-api:8000
```

## 七、第二轮验收：真实 Tiingo 采集（45/45 通过）

恢复冻结 panel bundle + 构建日历后，用真实 Tiingo token 采集 SPY + 20 panel 成员（最近 5 交易日）。harness：`ops/s09b_real_collect.py`（一次性 Core 容器，连 core + egress 网络，挂载 panel-bundle.json + references.json）。

关键点：
- 凭证/网络：真实 token 对 SPY 调用成功（最近交易日 2026-09-30，close=762.63，字段完整 date/close/volume/adj_close/div_cash/split_factor）。
- panel bundle 恢复：`restore_registration_bundle` 校验内容 hash 通过，恢复 503 历史成员 + 20 selected + 源/raw 对象。
- benchmark（SPY）不在 panel bundle 内（bundle 仅含 panel 20 只）；用冻结 UUID `ee161699-08b6-4250-986a-b39a393a68df` 单独创建 + ticker identity（`valid_from=2026-10-01`）。
- 小规模（SPY + 2 只）先跑：GL/TYL 各 5 行 `created=True`；重跑时 `created=False`（幂等去重生效）。
- 完整 21 只：45/45（21×采集验证 + 21×入库验证 + 3 前置检查），真实 Tiingo 21 次调用，全部入库。

验收库最终：securities 506、security_identities 506、price_observations 110、raw_objects 23、panel_registrations 1、calendar_days 3391（2016–2028）、data_sources 2（Tiingo + EODHD）。

20 panel 成员 ticker（panel selected 顺序）：AVY GL LOW DVN CCI VRTX GWW FFIV JKHY ICE PODD GE KIM MLM SJM TYL CEG TMUS TGT SO；benchmark SPY。

## 八、剩余

- 正式库初始化 + 候选导入（`release-logistic-ridge-candidate-20261002-v1`）+ 正式 tenant `youwei-internal-research` 固定：已完成，见 [s09b-production-db-prep.md](s09b-production-db-prep.md)。
- GHCR 凭证恢复后的推送、registry digest、部署清单更新、按 digest 拉取验证：已完成，见 §九。
- 生产 API/Worker 启动 + 采集启用 + deployment 校验：见 [s09b-production-launch.md](s09b-production-launch.md)。
- 本报告不构成 release 批准或正式 Campaign 授权；批准记录 0、Campaign 0 保持。

## 九、镜像正式发布（2026-10-02，凭证恢复后）

GHCR 凭证恢复后，在 SG 上完成已验收镜像的正式发布：

- 登录：`docker login ghcr.io`（token 经 stdin 传入，不落命令行/日志/仓库；SG `/root/.docker/config.json` 持有 base64 auth，与 S09a 既有方式一致）。
- 推送：`docker push ghcr.io/youweichen0208/youwei-core:phase1a-s09b` → `digest: sha256:55678d52580fea7cf51e4a6093d8cfff4ed55c9f86a7aa4c142b9fb3de6bee80 size: 856`。
- **registry digest（OCI index）`sha256:55678d52580fea7cf51e4a6093d8cfff4ed55c9f86a7aa4c142b9fb3de6bee80`**，与 §二 的本地索引 digest 一致；`docker buildx imagetools inspect` 确认 tag 指向该 index，合 amd64 平台 manifest `sha256:0c1f2b094e512af4000b3d9c2d6349b316d06719c84a0c51316839f7f3977e28`（与 §二 一致）与 unknown/unknown attestation manifest `sha256:b17bf46f0a12223669d49ac43a2d41ece70a7e95dfd5d14483994aeb7490e033`（buildx 默认附加）。
- 按 digest 拉取验证：`docker pull ghcr.io/youweichen0208/youwei-core@sha256:55678d52580fea7cf51e4a6093d8cfff4ed55c9f86a7aa4c142b9fb3de6bee80` → `Status: Image is up to date`。
- 生产 Compose（`infra/compose/production.json`）、`infra/upstreams.lock.yaml`、`infra/deployment-manifest.json` 已按此 digest 更新（deployment 校验与生产启动验证见 [s09b-production-launch.md](s09b-production-launch.md)）。

本节证明镜像与 registry 引用一致；不替代生产运行验收（S09c）、不构成 release 批准或正式 Campaign 授权。

**后续（同日）：该镜像在生产启动后发现 token 入日志缺陷，修复后重建为 `phase1a-s09b2`（registry digest `sha256:53631663...`）并重新发布；生产最终固定 r2 digest，`core` 组件验收证据相应移至 [s09b-production-launch.md](s09b-production-launch.md)。**
