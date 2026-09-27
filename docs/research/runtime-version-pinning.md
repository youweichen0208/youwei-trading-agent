# Hermes / Pi 版本固定与部署清单

核实日期：2026-09-27。方法：GitHub API、raw 官方文档、npm registry、PyPI、本机 pi 内置文档（0.84.2）；未在目标服务器实际安装。引用的 `main` 会变化，实施时固定 commit 与版本。既有接口结论见 [运行时核实](hermes-pi-runtime-verification.md)，本文不重复。

## 已确认事实

| 项目 | 事实 | 来源 |
| --- | --- | --- |
| hermes 仓库状态 | main @ `7fa45eb349a1`（2026-09-27），当日仍有推送；无 release/tag 体系可依赖 | [GitHub API](https://api.github.com/repos/NousResearch/hermes-agent) |
| Python 要求 | `requires-python = ">=3.11,<3.15"`；sg-prod 的 3.12.3 满足 | [pyproject.toml](https://github.com/NousResearch/hermes-agent/blob/main/pyproject.toml) |
| 依赖管理 | uv + uv.lock：pyproject 注释明确改 pin 后需 `uv lock` 再生成；`[tool.uv]` 含 override 与 exclude-newer 隔离——固定 commit + `uv sync --frozen` 可复现 | 同上 |
| 安装方式 | 无 wheel/sdist，仅源码安装（既有结论）；镜像内 = `git clone` + `git checkout <commit>` + `uv sync` | [运行时核实](hermes-pi-runtime-verification.md) |
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

- sg-prod：Python 3.12 已有；**需安装 Node 22 LTS**（pi 要求 >=22.19）
- hermes：固定 commit 的源码安装；`uv sync --frozen` 结果打入镜像层并缓存
- pi：npm 固定版本（镜像内安装，digest 固定）
- 协议接线：Hermes（OpenAI 兼容）与 Pi（anthropic-messages）都指向自建 LLM Gateway，见 [llm-gateway-options](llm-gateway-options.md)

## 待实测（sg-prod）

1. `uv sync --frozen` 在固定 commit 的可复现安装与镜像构建
2. `AIAgent.chat()` / `run_conversation()` 的 `enabled_toolsets`、`skip_memory`、`skip_context_files` 在固定版本的确切签名（python-library.md 本次网络抓取受限；固定版本后从源码验证）
3. Node 22 LTS 安装与 pi 0.87.1 / 0.84.2 选型
4. 两者 Docker 镜像构建与 image digest 固定
