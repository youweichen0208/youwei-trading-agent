# 上游版本与部署校验

`infra/upstreams.lock.yaml` 是候选与启用组件的登记表。文件采用 **JSON 语法（YAML 1.2 子集）**，由 Python 标准库解析，不新增 PyYAML 运行依赖。暂未选择的版本、镜像填 `null`；安装冒烟通过不等于业务接入验收通过。

当前 Hermes 固定完整提交、Pi 固定 npm 版本；两者只完成已有记录中的冒烟。LiteLLM 1.102.1 是研究候选，先前 `main-stable` 镜像冒烟不能证明该精确版本。Open WebUI / OpenViking 尚未选择版本。均未启用，不存在已验证的生产镜像 digest。

当前登记表先覆盖这些外部候选。首次实际发布时还须登记 Core、Runner、PostgreSQL 与沙箱执行镜像等部署使用的组件，取得各自真实 digest 和验收依据；发布检查覆盖渲染配置中的全部服务镜像，不能仅登记 Agent 后直接通过。

## 两种检查

登记检查不安装、不拉取或启动任何上游：

```bash
python3 infra/validate_upstreams.py --mode catalog
```

生产部署检查额外要求：至少一个启用组件；启用组件有精确源版本、`integration_status=integrated`、`verification.status=passed` 及可核对内容 hash 的验收报告；全部部署镜像固定 `@sha256:`。登记表通过不代表部署通过。

```bash
docker compose -f path/to/compose.yaml config --format json > /tmp/youwei-compose.resolved.json
python3 infra/validate_upstreams.py --mode deployment \
  --compose /tmp/youwei-compose.resolved.json \
  --manifest path/to/deployment-manifest.json
```

`--lock` 可指定另一份候选锁，`--root` 可指定验收报告相对路径的根目录，默认均针对本仓库。传入渲染后的 Compose JSON；发布检查拒绝 `build`、浮动镜像和未纳入登记的服务镜像。渲染文件可能包含环境配置，保存在受控位置，不将秘密写进版本库或验收报告。

## 镜像与清单绑定

每个启用组件的 `deployment` 有 `image` 和非空 `bindings`：

```json
{
  "image": "registry.example/youwei-core@sha256:<实际64位摘要>",
  "bindings": [
    {"service": "core-api", "field": "image"},
    {"service": "core-worker", "field": "image"}
  ]
}
```

沙箱内部运行镜像通过命名环境变量绑定，例如 `{"service":"sandbox-runner","field":"environment","name":"YOUWEI_RUNNER_IMAGE"}`。名称必须与实际 Compose 配置一致。每个服务的主镜像必须有组件绑定，同一个配置位置不能重复绑定。

部署清单结构：

```json
{
  "schema_version": 1,
  "lock_sha256": "<锁文件原始字节的SHA256>",
  "compose_sha256": "<渲染后Compose文件原始字节的SHA256>",
  "components": {
    "<启用组件名>": {"image": "<精确镜像引用>", "bindings": []}
  }
}
```

`components` 必须与所有启用组件的 `deployment` 完全一致，示例空数组只是结构占位，不能通过发布检查。校验同时检查两个文件 hash、实际 Compose 镜像/环境值以及验收报告 `verification.evidence={path,sha256}`。验收报告必须在 `--root` 内。这里证明文件与镜像引用一致，不替代镜像内容证明、人工批准、基础设施安全验收或真实部署健康检查。

## 依赖与升级

- Core 使用根 `pyproject.toml` / `uv.lock` 和 Python 3.13；Runner 自有 `services/sandbox-runner/pyproject.toml` / `uv.lock`。共享 DTO 位于 `contracts/`，通过窄契约连接进程。
- 将来接入 Hermes 时，独立 Python 3.14 构建及锁；Pi 使用独立 Node 包清单和锁。Open WebUI、OpenViking、LiteLLM 各自固定官方版本/镜像，不把它们的依赖合并进 Core 锁。
- 升级从复制当前登记及部署清单开始；改变一项上游，构建并取得真实 digest，运行该边界的契约测试及相关目标机验收，保存报告 hash，再更新候选锁、Compose 和部署清单。未完成验收保持 disabled 或非 passed。
- 检查 `catalog` 和 `deployment` 后，将候选交给既有发布流程；版本校验不授予生产发布或 ResearchRelease 人工批准。
- 回滚使用上一份成套锁、镜像 digest、Compose 和清单；先检查当前数据库/产物格式与旧版本兼容性。旧镜像不能自动回退不兼容迁移，也不能修改历史已封存预测。

契约测试：`uv run --frozen pytest -q tests/contracts/test_upstreams.py`。测试使用合成镜像摘要与临时配置，不拉镜像、不调用供应商、不代表任何真实组件已部署。

## 本机开发 Compose

`infra/compose/development.json` 是开发联调示例，使用本地构建和开发镜像 tag，**不会通过 deployment 发布检查**。它没有部署清单，也不代表目标机已部署或验收。Core 与 Runner 镜像分别由 `infra/images/core.Dockerfile`、`runner.Dockerfile` 构建，使用各自的锁；最终 Runner 镜像只有 Runner、共享 contracts 及 Docker CLI，Core 镜像不安装 Runner 开发依赖或 Docker CLI。

使用前在当前 shell 显式配置以下变量，缺少必填项时 Compose 插值失败：

| 变量 | 用途 |
| --- | --- |
| `YOUWEI_POSTGRES_PASSWORD` | URL-safe 开发数据库密码，例如随机十六进制串；会放入 asyncpg DSN |
| `YOUWEI_ADMIN_API_KEY` | Core 管理员引导密钥 |
| `YOUWEI_CAPABILITY_SECRET` | Core 作业能力令牌密钥 |
| `YOUWEI_RUNNER_SECRET` | Core/Runner 共同使用的独立签名密钥，至少32字符 |
| `YOUWEI_RUNNER_SPOOL_DIR` | 事先创建、只给 Runner 使用的绝对宿主机目录 |
| `YOUWEI_TIINGO_TOKEN` | 可选；仅实际采集任务需要，不配置也可测试沙箱链路 |

不要将秘密写进 Compose、镜像、测试数据或 Git。`docker compose config` 不加 `--quiet` 会渲染环境值，避免将其输出分享或写入普通日志。

```bash
docker compose -f infra/compose/development.json config --quiet
docker compose -f infra/compose/development.json build
docker compose -f infra/compose/development.json up -d postgres
docker compose -f infra/compose/development.json run --rm core-api alembic upgrade head
docker compose -f infra/compose/development.json up -d
```

API只映射本机 `127.0.0.1:8000`，并接入普通 `edge` 网络供本机发布端口使用（Docker Desktop 下只接 internal 网络时发布端口不可达）。PostgreSQL无宿主端口；`core` 与 `runner_control` 是两个 internal 网络，Runner不接 `core`，也不接 `egress`。当前采集仍由Core Worker负责，所以只有Worker同时接入egress。只有Runner挂载Docker socket，Runner没有数据库或供应商环境变量。独立网络限制正常接口流向，Docker socket持有者仍属于高权限可信进程。

**spool 必须保持宿主与 Runner 内同一绝对路径。** Runner随后让宿主Docker daemon按绝对路径挂载job输入，因此named volume或“宿主 `/a` → Runner `/b`”会让daemon找到错误目录。示例用 `YOUWEI_RUNNER_SPOOL_DIR` 同时作为bind source、bind target和 `YOUWEI_RUNNER_SPOOL_ROOT`；必须先创建目录，Compose禁止隐式创建。目录放在仓库之外，Linux使用真实绝对路径；Docker Desktop需处于允许共享的宿主路径。不要把该目录复用为数据库、密钥或项目根目录。

本机示例显式设置 `YOUWEI_RUNNER_DEVELOPMENT=true`、runtime为空，使用本机默认容器运行时。正式Runner必须development=false、sandbox镜像固定digest、runtime=runsc，并重新构建/验证发布清单；不能把开发override带入正式部署。Dockerfile的默认基础镜像同样只用于开发，发布构建时用构建参数覆盖为已核实的digest，记录构建证据。
