# Hermes / Pi 版本固定与部署清单

核实日期：2026-09-27。方法：GitHub API、raw 官方文档、npm registry、PyPI、本机 pi 内置文档（0.84.2）；未在目标服务器实际安装。引用的 `main` 会变化，实施时固定 commit 与版本。既有接口结论见 [运行时核实](hermes-pi-runtime-verification.md)，本文不重复。

## 已确认事实

| 项目 | 事实 | 来源 |
| --- | --- | --- |
| hermes 仓库状态 | main @ `7fa45eb349a1a6f1eebc010b3fef0a9d996f386a`（2026-09-27）。官方 tag 体系存在（v2026.9.7 / v2026.9.21 / v2026.9.24，`git ls-remote --tags` 证实）；钉法：比较 tag 与 main 后固定完整 SHA。当前 3.14 支持尚未进入 tag（v2026.9.24 的上界仍是 `<3.14`），官方下个 tag 发布后可改钉 tag | [GitHub API](https://api.github.com/repos/NousResearch/hermes-agent) |
| Python 要求 | main：`requires-python = ">=3.11,<3.15"`，仓库 `.python-version = 3.14`；pyproject 注释明示 **"we \*only\* support 3.14"**（保留 >=3.11 仅为让旧安装完成升级）。**镜像固定 Python 3.14**（实测 3.14.7）。注意 tag v2026.9.24 上界为 `<3.14` 且 `.python-version=3.11`——3.14 支持在 tag 之后合入 main | [pyproject.toml](https://github.com/NousResearch/hermes-agent/blob/main/pyproject.toml) |
| 依赖管理 | uv + uv.lock：pyproject 注释明确改 pin 后需 `uv lock` 再生成；`[tool.uv]` 含 override 与 exclude-newer 隔离——固定 commit + `uv sync --frozen` 可复现 | 同上 |
| 安装方式 | 无 wheel/sdist，仅源码安装（既有结论）；镜像内 = `git clone` + `git checkout <完整 SHA>` + `uv sync --frozen --python 3.14`（tag 的 `.python-version` 是 3.11，显式传 `--python` 最稳）。**已在 sg-prod 实测**：py3.14.7 + main `7fa45eb` 安装成功，`hermes --version` 正常，venv 141MB | [运行时核实](hermes-pi-runtime-verification.md) + [实测记录](s01-target-verification.md) |
| 记忆隔离 | `memory.memory_enabled: false` + `memory.user_profile_enabled: false` 双关时：memory 工具从 schema 移除、system prompt 指引一并移除；外部 provider（`memory.provider`）**不受这两个开关影响**，需列入 `agent.disabled_toolsets` 才一并隐藏；`write_approval` 为独立写入审批键 | [memory.md](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/features/memory.md) |
| 会话检索 | `session_search` 为独立工具；会话存 SQLite（`~/.hermes/state.db`，FTS5 全文检索） | 同上 |
| 自定义 LLM | `provider: custom` + `base_url`（OpenAI 兼容协议），写入 config.yaml 持久化；官方列举 Ollama/vLLM/llama.cpp/SGLang/LocalAI | [FAQ](https://hermes-agent.nousresearch.com/docs/reference/faq) |
| pi npm 版本 | 最新 `0.87.1`（2026-09-22 发布）；engines 要求 `node >=22.19.0` | [npm registry](https://www.npmjs.com/package/@earendil-works/pi-coding-agent) |
| 本机 pi 版本 | pi.app 内置 0.84.2（同一 node engines 要求）；部署固定 0.87.1 还是 0.84.2 待选型 | 本机 package.json |
| pi models.json | `providers.{id}.baseUrl` + `api`（`openai-completions` / `anthropic-messages`）；本机已用 `anthropic-messages` + 火山引擎 baseUrl 生产运行 | [models 文档](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/models.md) + 本机配置 |
| pi RPC 命令全集 | prompt / steer / follow_up / abort / new_session；get_state / get_messages；set_model / cycle_model / get_available_models；set_thinking_level / cycle_thinking_level / get_available_thinking_levels；set_steering_mode / set_follow_up_mode；compact / set_auto_compaction；set_auto_retry / abort_retry；**bash / abort_bash**；get_session_stats / export_html / switch_session / fork / clone / get_fork_messages | 本机 0.84.2 [rpc.md](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/rpc.md) |

## RPC 白名单要点

外层 wrapper 必须只放行业务所需命令（prompt / steer / abort / new_session / get_state 一类），**显式排除**：`bash`、`abort_bash`（独立 shell 通路）、`switch_session` / `fork` / `clone`（会话逃逸）、`export_html`（文件写出）。与架构 §10 "Pi RPC wrapper 允许列表化管理命令，不能把独立的 RPC bash 等命令透传给模型"一致。

## 部署清单（建议）

- Hermes 镜像：`python:3.14-slim`（固定 digest）；固定 commit `7fa45eb349a1a6f1eebc010b3fef0a9d996f386a` + `uv sync --frozen --python 3.14`
- pi：npm 固定版本（镜像内安装，digest 固定），基础镜像 node:22
- sg-prod 无需宿主机 Python/Node（容器化部署单元）
- 协议接线：见 [llm-gateway-options](llm-gateway-options.md) 的候选方案比较

## 待实测

1. ~~`AIAgent.chat()` / `run_conversation()` 的 `enabled_toolsets`、`skip_memory`、`skip_context_files` 在固定版本的确切签名（python-library.md 网络抓取受限；固定版本后从源码验证）~~ **已在 commit `7fa45eb` 上实测确认（2026-09-28）**：`AIAgent.__init__` 位于 `run_agent.py`，显式含 `enabled_toolsets`、`disabled_toolsets`、`skip_memory`、`skip_context_files`、`skip_background_review`、`provider`、`base_url`、`api_key`、`api_mode`、`model`、`platform`、`session_id`、`gateway_session_key`、`iteration_budget`、`run_budget_seconds` 等参数；`chat(message, stream_callback=None) -> str`（`agent/turn_facade.py` 的 `TurnFacadeMixin`），内部返回 `run_conversation(...)["final_response"]`。构造逻辑经 `agent.agent_init.init_agent` 转发。
2. pi 0.87.1 与 0.84.2 的选型；两者生产 Docker 镜像构建与 digest 固定

## 记忆/工具隔离键运行时验证（2026-09-28）

在 sg-prod 上以固定 commit `7fa45eb` 构造 `AIAgent(provider="custom", skip_memory=True, skip_context_files=True, skip_background_review=True, enabled_toolsets=[], disabled_toolsets=[])`（不调用 LLM），实测隔离键**实际生效**：

- `_memory_store = None`、`_memory_enabled = False`、`_user_profile_enabled = False`（内置 MEMORY.md / USER.md 不加载）
- `valid_tool_names = []`（无任何工具加载，终端/文件/浏览器/web/session_search 全部不可达；构造日志明示 "No tools selected / No tools loaded"）
- `skip_context_files = True`、`skip_background_review = True`（上下文文件与后台复盘关闭）

结论：`enabled_toolsets=[]`（空列表，非 None）是研究角色的正确隔离面——Hermes 内置工具集全关，仅后续注册的平台工具（snapshot_manifest / quant_run / sandbox_submit / sandbox_status / artifact_read）可被调用，且每个都经服务端按 run 能力令牌授权。此验证对应 `services/agent-runtime/src/youwei_agent_runtime/adapter.py` 的 `ISOLATION_KWARGS`。

## 平台工具注册机制核实（2026-09-28）

在 sg-prod 上固定 commit `7fa45eb` 的源码中核实：Hermes **没有**在 `AIAgent.__init__` 提供运行时自定义工具注入参数（无 `tools` / `custom_tools` / `register_tool` 关键字）。自定义工具只能通过 **plugin 系统**注册到全局 `tools.registry`，再经 `enabled_toolsets` 选中：

- `hermes_cli.plugins.PluginContext.register_tool(name, toolset, schema, handler, check_fn=None, requires_env=None, is_async=False, description="", emoji="", override=False)` 是注册入口（`hermes_cli/plugins.py`）；同名内置工具需 `override=True` + operator 在 config.yaml 显式 `allow_tool_override`，否则拒注。
- plugin 在 `_load_tools`（`agent/agent_init.py`）时经 `discover_plugins()` 发现，工具进全局 `tools.registry`（按 `toolset` 分组），`model_tools.get_tool_definitions(enabled_toolsets=..., disabled_toolsets=...)` 生成该实例的工具快照。
- 因此平台研究工具的正确接入形态是：实现一个 Hermes plugin，`toolset="youwei-research"`（或类似名），在 agent-runtime 进程内注册 `snapshot_manifest` / `quant_run` / `sandbox_submit` / `sandbox_status` / `artifact_read`；`ISOLATION_KWARGS` 改为 `enabled_toolsets=["youwei-research"]`（不再是空列表）。每个 handler 必须经服务端按 run 能力令牌授权（agent-runtime 无 DB 凭证，授权回调 Controller），不能因本地可读到 FrozenEvidence 就放行任意读取。

此核实解答了 S07c「需确认 Hermes 自定义工具注册机制 `ctx.register_tool`」的遗留项；`ctx.register_tool` 的 `ctx` 即 plugin 的 `PluginContext`。

## 平台工具直连注册路径核实（S07i，2026-09-28）

agent-runtime 作为独立子进程（`research-once`）运行，不经过 Hermes 的 plugin 发现流程（`discover_plugins()`），因此不能走 `PluginContext.register_tool`，而是直接驱动全局 `tools.registry`。在 sg-prod 固定 commit `7fa45eb` 上核实其确切接口：

- 模块级单例在 `tools/registry.py` 第 1015 行 `registry = ToolRegistry()`；`tools/__init__.py` 是 side-effect free，**不** re-export `registry`，正确导入是 `from tools.registry import registry`（不是 `from tools import registry`）。
- `ToolRegistry.register(name, toolset, schema, handler, check_fn=None, requires_env=None, is_async=False, description="", emoji="", max_result_size_chars=None, dynamic_schema_overrides=None, override=False, scope=None)`（第 666 行）：非 plugin 调用方 `owner=None`、`scope=None` 即进程全局 map；同 toolset 重复注册会覆盖（幂等），跨 toolset 同名 shadow 被拒（除非 `override=True`）。
- `get_definitions(tool_names, quiet)`（第 843 行）生成 `{"type":"function","function":{**entry.schema,"name":entry.name}}`：`name` 从 `entry.name` 注入（覆盖 schema 内冗余的 `name`），`description` 在 register 时 `description or schema.get("description")` 回退。
- `dispatch(name, args, **kwargs)`（第 888 行）：sync handler 以 `handler(args, **kwargs)` 调用（`kwargs` 经 `_kwargs_accepted_by` 按签名裁剪），`is_async=True` 才走 `_run_async`；handler 返回 `str`（或 `{"_multimodal": True, "content": [...]}`）为合法结果，异常统一转 `tool_error`。

据此 `services/agent-runtime/src/youwei_agent_runtime/runtime.py::_register_research_tools` 用 `from tools.registry import registry` + `registry.register(name=..., toolset=RESEARCH_TOOLSET, schema=..., handler=..., is_async=False, description=schema["description"])` 直连注册；handler 签名为 `(args: dict, **_) -> str`，与 dispatch 契约一致。真实注册与工具调用仍待 SG 部署环境（Hermes venv 需补齐 pydantic/youwei-contracts，见 S07h 剩余限制，随 S09 镜像切片）。

## SG 实测：平台工具注册与 handler 授权（S07i，2026-09-28）

sg-prod 的 Hermes venv 已补齐 pydantic 2.13.4（S07h 遗留项已解决），同步本机 S07i 源码后实测：

- **注册生效**：`_register_research_tools()` 后 `registry.get_entry("snapshot_manifest")` 返回 entry（toolset=`youwei-research`，schema name=`snapshot_manifest`）；`make_agent` 构造日志 `✅ Enabled toolset 'youwei-research': snapshot_manifest`、`🛠️  Final tool selection (1 tools): snapshot_manifest`；`agent._memory_store is None`（隔离键生效）。
- **handler 授权生效（正/反两面）**：`registry.dispatch("snapshot_manifest", {})` 在无 run context 时返回 `no research run context; tool called outside a run`；设置 `ToolContext`（合法 `llm_call` scope + evidence）后返回正确的 manifest JSON（as_of/content_sha256/kind/mode 等）；用 `snapshot_read` scope（缺 `llm_call`）时返回 `capability missing required scope 'llm_call'`。证明防御纵深授权在真实 Hermes dispatch 路径上成立。
- **Tool Search 渐进披露（需记录的默认行为）**：Hermes 的 `tools.tool_search` 默认开启，把「plugin 工具」（非 `toolsets._HERMES_CORE_TOOLS`、非 session-gated GUI 的自定义 toolset）折叠到 `tool_call`/`tool_describe`/`tool_search` 三个桥工具后。因此 `agent.valid_tool_names` 显示 `['tool_call', 'tool_describe', 'tool_search']` 而非直接列出 `snapshot_manifest`；模型需先 `tool_search` 查目录、再 `tool_call` 调用。这**不影响**注册与 handler 授权（dispatch 路径不变），只影响模型看到工具面的方式。是否关闭 tool_search（`tools.tool_search.enabled: off`）让 snapshot_manifest 直接暴露给模型，属工具暴露语义，留待真实 LLM 研究调用切片（Phase 1B 启用）时评估。
