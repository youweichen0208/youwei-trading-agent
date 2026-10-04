# 上游版本与部署校验

`infra/upstreams.lock.yaml` 是候选与启用组件的登记表。文件采用 **JSON 语法（YAML 1.2 子集）**，由 Python 标准库解析，不新增 PyYAML 运行依赖。暂未选择的版本、镜像填 `null`；安装冒烟通过不等于业务接入验收通过。

当前源版本、候选状态和已登记镜像以锁文件及其验收证据为准，不从早期调研状态推断是否部署。个人 Hermes gateway 登记位于 `infra/chat/assistant-upstreams.lock.json`。2026-10-04 已按用户授权部署 youwei-webui v0.11.4、独立助手与新 Core API；当前生产平台锁为 `infra/releases/20261004/production.lock.json`；聊天栈已随后按用户选择切换官方 Hermes v2026.9.24，当前锁为 `infra/releases/20261004-hermes-workbench/chat.lock.json`，各自配对应 manifest（Core API 与 Worker 分别绑定版本）。主 catalog 和旧聊天 Compose 中的历史基线不代表本次线上版本。目标机兼容、迁移、备份恢复及限制见 [部署记录](ops/three-repo-vm-rollout-20261004.md)。

发布检查覆盖渲染配置中的全部服务镜像，包括 Core、Runner、数据库及沙箱内部镜像。源码同步、隔离验收、目标机验证和生产切换分别记录；只有实际部署证据才能证明线上版本。

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
- Hermes 个人助手按官方 v2026.9.24 使用独立 Python 3.13，研究/实验保持独立 Python 3.14；分别使用固定提交的上游 `uv.lock`；Pi 若接入则使用独立 Node 包清单和锁。youwei-webui 固定上游基线与 fork 提交，其他组件分别固定版本/镜像，不将这些依赖合并进 Core 锁。
- 升级从复制当前登记及部署清单开始；改变一项上游，构建并取得真实 digest，运行该边界的契约测试及相关目标机验收，保存报告 hash，再更新候选锁、Compose 和部署清单。未完成验收保持 disabled 或非 passed。
- 检查 `catalog` 和 `deployment` 后，将候选交给既有发布流程；版本校验不授予生产发布或 ResearchRelease 人工批准。
- 回滚使用上一份成套锁、镜像 digest、Compose 和清单；先检查当前数据库/产物格式与旧版本兼容性。旧镜像不能自动回退不兼容迁移，也不能修改历史已封存预测。

契约测试：`uv run --frozen pytest -q tests/contracts/test_upstreams.py`。测试使用合成镜像摘要与临时配置，不拉镜像、不调用供应商、不代表任何真实组件已部署。

### youwei-webui 的同步与交付

- 三仓统一以 `develop` 为默认协作和 PR 目标分支。WebUI 的 `develop` 承接原 `youwei` 定制基线，镜像工作流按 develop 验证与发布；旧 `youwei` 保留历史。三仓删除 `main` 分支；升级从 develop 建临时分支，fetch 官方上游已选 Release 后合并，验证后合回 develop；同步上游不会自动合入 develop 或部署。
- 每月检查正式 Release，安全修复及时评估。保留上游历史，按功能组织定制；配置能关闭的功能先用配置，保留认证、聊天存储和迁移机制作为初期默认。
- fork 负责源码构建和界面回归；平台仓库负责跨服务验证和部署清单。每次交付记录上游 tag/完整 SHA、fork 完整 SHA、构建镜像 digest、兼容的 Hermes 镜像与配置版本、验收证据和回滚数据要求。部署固定 digest，不使用浮动 main/latest。
- 首次将官方 v0.6.36 切换到 fork 时单独验收版本升级；现有配置脚本涉及 WebUI 持久配置与 SQLite 结构，新版本须重新核对，不能直接沿用旧版本测试结论。保留版权及适用许可，品牌定制核对所选版本 LICENSE。

### Hermes 两个运行面的升级

研究 Dockerfile 固定 `7fa45eb349a1a6f1eebc010b3fef0a9d996f386a`，个人助手仓库固定官方 v2026.9.24 的 `f97608f178d1ffeca59860195ab7da295f7c8e5f`。版本事实同时核对各自锁及 [运行时接口核实](research/hermes-pi-runtime-verification.md)。个人助手 EODHD 接入仅增加上游锁内的 MCP extra，见 [部署记录](ops/eodhd-mcp-rollout-20261004.md)，不连带升级研究运行时。

| 运行面 | 构建与登记 | 升级专属要求 |
| --- | --- | --- |
| 个人 gateway | [trading-assistant](https://github.com/youweichen0208/trading-assistant) 的 Dockerfile 与 upstreams.lock.json；平台候选 `infra/chat/assistant-upstreams.lock.json` | 无上游补丁；验证原生 API、插件注册、中间件、DDGS/网页提取、profile/知识持久化及备份恢复 |
| 研究 / 实验 | `infra/images/agent-runtime.Dockerfile`；`infra/upstreams.lock.yaml` | 验证受控适配、冻结证据、工具权限、提案契约、归因与取消；评估 ResearchRelease 影响 |

研究镜像应用 `infra/images/hermes-last-turn-model.patch`，记录供应商返回的实际模型标识。每次升级必须重新核对相关调用语义；`git apply --check` 仅证明补丁可应用。上游提供稳定返回接口后改用该接口并移除补丁；否则保留补丁原因、hash 和验收证据。个人 gateway 不自动继承此补丁。

两个运行面分别构建、登记与发布，可使用不同的已验证版本；个人助手升级不连带更新正式研究镜像。上游升级引入的 prompt、模型路由、工具或记忆行为变化须评估对正式研究的影响，按既有 ResearchRelease 审批及版本流程处理；工程通过不等于批准预测行为变更。

### 升级验收与恢复

| 边界 | 必须覆盖的行为 |
| --- | --- |
| WebUI / gateway | 发现与鉴权、旧聊天保留、完整历史追问、SSE、停止/断流/异常、后台标题与标签不触发 Hermes，包括普通模型发现故障 |
| 个人工具 | 工具允许列表、Core 越权拒绝、提交重试幂等、任务状态/报告/显式取消、日线来源与截止时间、网页读取失败如实返回 |
| 持久数据 | 来源笔记保存→新聊天召回→修订/删除→重启；会话数据库、memory、知识和 WebUI 数据的备份副本迁移及隔离恢复 |
| 研究 / 实验 | FrozenEvidence/Proposal 契约、能力令牌与租约、工具越权拒绝、模型归因补丁语义、取消及 Controller 接纳边界 |

已有检查入口包括 trading-assistant 的 `ops/verify_assistant_native.py`、本仓库的 `ops/verify_assistant_webui.py --assistant-image <verified-image> --webui-image <verified-image>` 和助手手册中的命令；测试脚本本身的固定 checkout / 镜像须随候选版本显式更新，否则只能证明旧组合。隔离 mock 测试不替代浏览器操作、目标机资源测量和真实模型业务验收。

切换前备份 WebUI 数据、Hermes profile/会话/memory/知识，并在副本验证新旧格式兼容。只切换旧镜像不能撤销不兼容的数据迁移；需要恢复时使用对应升级前副本，并事先明确恢复点之后新聊天、笔记的导出或补录方案。保留上一套镜像、配置、锁和部署清单；聊天回滚不恢复正式 Ledger。恢复步骤见 [个人助手手册](ops/hermes-personal-assistant.md)，正式发布仍按既有授权执行。

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

### EODHD Extended 候选（2026-10-05）

十九工具候选登记在 `infra/releases/20261005-eodhd-extended/assistant.candidate.lock.json`，`enabled=false`、`verification.status=smoke_only`。固定源码、真实 registry digest 与开发/目标机隔离验证见同目录证据和[验收记录](ops/eodhd-extended-20261005.md)。账户权限和实时连接未通过，生产助手登记、工作台镜像与 VM Compose 保持原值；不得把候选 catalog 通过解释为已部署。
