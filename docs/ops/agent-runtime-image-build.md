# agent-runtime 无头镜像构建与 digest 固定（S07 剩余）

日期：2026-10-01  
环境：sg-prod（DigitalOcean droplet，Docker 29.5.2，linux/amd64）  
状态：镜像构建与容器内无费用冒烟完成；**部署引用保持未就绪**（尚无镜像仓库，digest 为本地 manifest，未 push）。

## 交付物

- Dockerfile：`infra/images/agent-runtime.Dockerfile`（精简无头镜像，非 Hermes 官方产品镜像）
- 构建脚本：`infra/build-agent-runtime.sh`
- 容器冒烟脚本：`services/agent-runtime/smoke/container_smoke.py`
- 源码修复：`services/agent-runtime/src/youwei_agent_runtime/main.py`（Hermes 启动 banner 污染 stdout → 重定向到 stderr）

## 镜像与固定信息

| 项 | 值 |
| --- | --- |
| 镜像 tag | `youwei-agent-runtime:dev` |
| manifest digest（单平台，无 provenance） | `sha256:31b22113b2deee2476c94ddf290aa58f9b76c647742a60e5fd5f64cffa3dd02d` |
| OCI 产物（SG） | `/root/agent-runtime-image/youwei-agent-runtime-dev.tar`（153 MB） |
| OCI 产物内容 hash | `sha256:fc72bbd5132ab265dc52e9718957182cd466b34059a8d26f684970f0a3a2138b` |
| 基础镜像 | `python:3.14-slim@sha256:51dafde81dbdb6ebde285137a295cf18a47ca95234fe388a343719cb97305b3d` |
| uv | `ghcr.io/astral-sh/uv:0.11.16@sha256:440fd6477af86a2f1b38080c539f1672cd22acb1b1a47e321dba5158ab08864d` |
| Hermes commit | `7fa45eb349a1a6f1eebc010b3fef0a9d996f386a` |
| 运行解释器 | Python 3.14.7；pydantic 2.13.4（Hermes uv.lock 锁定的版本） |
| 镜像解压大小 | ~676 MB（压缩层 ~153 MB） |

## 依赖策略（单一 venv，Hermes 锁为权威）

Hermes 不可 pip 安装，以源码 `git init` + `git fetch --depth 1 origin <SHA>` + `git checkout FETCH_HEAD` 获取，再用 `uv sync --frozen --no-dev --python /usr/local/bin/python` 装核心依赖到 `/opt/hermes/.venv`（pydantic 由 Hermes 锁为 2.13.4）。

`youwei-contracts` 与 `youwei-agent-runtime` 用 `uv pip install --no-deps` 装入**同一个** venv，避免第二个锁（agent-runtime 锁 pydantic 2.13.5）静默升级 pydantic 破坏 Hermes 精确 pin。两者均为 hatchling wheel 项目，`--no-deps` 跳过 pydantic（已由 Hermes 提供，`>=2.10,<3` 约束被 2.13.4 满足）。

研究角色仅需 Hermes 核心 `[project].dependencies`（openai/httpx/rich 等），无需 `pm` 的二进制工具包（Chromium/ffmpeg 等）或任何 extra。冒烟已证明 `run_agent.AIAgent` + `tools.registry` + 平台工具 `snapshot_manifest` 在该最小依赖集上完整可用。

## 镜像形态

- `PYTHONPATH=/opt/hermes`（`run_agent.py`、`tools/registry.py` 均在 Hermes 仓库根）。
- 非 root 运行（UID 10001），`HOME=/tmp`、`HERMES_HOME=/tmp/hermes-home` 为可写暂存（memory/session 已禁用，仅临时文件）。
- `ENTRYPOINT ["youwei-agent-runtime"]`，入口子命令 `research-once`。
- stdin/stdout 保持 JSON 契约；Hermes 的启动 banner / API 进度 / 会话日志全部走 stderr（见源码修复）。

## 容器内无费用冒烟（5/5 通过）

用本地 mock 网关（OpenAI 兼容 SSE 流式）+ 真实 `research-once` 入口，验证：

| 场景 | 结果 |
| --- | --- |
| 合法能力令牌（`llm_call` scope + 匹配 tenant） | `ok=true`，produced proposal，p=0.6，1 个已解析引用，usage=`session_delta` 完整 |
| 错 tenant | 拒绝：`capability tenant does not match the evidence bundle's tenant` |
| 缺 scope（`snapshot_read`） | 拒绝：`capability missing required scope 'llm_call'` |
| 坏签名 | 拒绝：`capability rejected: signature mismatch` |
| 过期令牌 | 拒绝：`capability rejected: capability expired` |

同时验证：`run_agent.AIAgent` import 成功、`snapshot_manifest` 工具注册成功（toolset=`youwei-research`）、Python 3.14.7 / pydantic 2.13.4、隔离键生效（`agent memory store: None`、工具面仅 `snapshot_manifest` + tool_search 桥）。

## 源码修复：stdout 污染

首次冒烟发现 `research-once` 的 stdout 被 Hermes 的 `AIAgent.__init__` banner（`🤖 AI Agent initialized...`、`🔗 Using custom base URL...`、API 进度、会话日志）污染，JSON 响应不可解析。Hermes 用裸 `print()`（`agent/agent_init.py` 第 721/736/978/980/1128 行等）输出到 stdout，stderr 为空。

修复：`main.py::_research_once` 在整个 turn 期间把 `sys.stdout` 重定向到 `sys.stderr`（覆盖 `_register_research_tools` + `honor_request`/`run_research`），仅在写最终结果或错误响应前恢复到真实 stdout。结果与错误始终经 `real_stdout.write` 写出，保持 stdout 只输出单行 JSON。

## 剩余限制（部署引用未就绪）

- manifest digest 是**本地镜像 digest**，不是 registry 引用；尚无镜像仓库，`docker save` 的 OCI tar 与 digest 仅作产物固定，`upstreams.lock.yaml` 的 `hermes.deployment.image` 保持 `null`。
- 真实 LLM 网关调用、取消计费语义、数据源 LLM 转发授权、Phase 1B 正式启用（新 Campaign + 人工批准 release）仍待后续，不在本片范围。
- 本片只验证「镜像可运行 research-once 与授权边界」，不验证 Controller 跨容器接线、生产资源压测或 gVisor 运行。

## 验证命令

```bash
# SG 上构建
cd /tmp/agent-runtime-build && docker build --provenance=false \
    -f infra/images/agent-runtime.Dockerfile -t youwei-agent-runtime:dev .

# 容器内冒烟（5 场景）
docker run --rm --network host --entrypoint python \
    -v /tmp/agent-runtime-build/services/agent-runtime/smoke:/smoke:ro -w /smoke \
    youwei-agent-runtime:dev /smoke/container_smoke.py --secret <secret> --gateway-port 9902
```
