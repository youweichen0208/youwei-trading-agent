# Hermes 上游源码补丁清单

本项目运行的是基于锁定上游 `nousresearch/hermes-agent` 构建的自定义镜像。对上游
Python 源码的修改不是手工编辑镜像，而是由 `services/hermes/apply_patch.py` 在
`docker build` 时应用；`services/hermes/upstream.json` 固定上游 commit、镜像摘要和
源码哈希。任一被校验文件与钉不一致，构建在写入任何补丁前失败。

本清单记录“为什么改、改在哪、对外契约是什么”，供升级上游和审计时核对。新增或修改
补丁时必须同步更新本文件与 `services/hermes/README.md`。

## 补丁文件与目的

| 上游文件 | 目的 | 对外行为 |
| --- | --- | --- |
| `gateway/platforms/api_server_runs.py` | Alpha 准入绑定：持久化 run 与能力的绑定、拒绝非持久幂等存储、禁止交给 Desktop 会话；注册 `reasoning.delta` 事件 | Runs API 只接受受信任能力；工具身份从服务端 ContextVar 注入；原生推理摘要出现在 run SSE |
| `gateway/session_api_turn.py` | 在 canonical turn 的 payload 中携带 `alpha_run_id`，供准入身份核对 | API turn 与应用 run 身份一致，不能复用其他 turn 的授权 |
| `agent/tool_executor.py` | 在执行前按角色拒绝未授权内置工具，禁用原生 `delegate_task` 工具面 | 模型只能调用该 Profile 角色目录中的项目工具 |
| `agent/anthropic_adapter.py` | 显式选择 Anthropic 兼容端点的认证头风格 | 一个端点只发送一种凭据头，凭据不进入 `config.yaml` |

`upstream.json` 的 `runtimeFiles` 与 `auditFiles` 只做哈希校验，不写入补丁；它们定义
补丁所依赖的准入、恢复、SQLite 与依赖锁边界。

## `reasoning.delta` 契约（2026-11-14 新增）

上游 canonical turn 已经支持 `reasoning_callback` 并把推理发布给 observer
（`gateway/session_api_turn.py` 的 `_OBSERVER_KEYS`），但 Runs API 启动 agent 时没有
注册该回调，原生 thinking 只存在于 CLI/TUI。补丁在 `api_server_runs.py` 中：

- 覆盖 canonical admission 与非 admission 两条执行路径，传入 `reasoning_callback`；
- 按工具边界缓冲推理文本：每次 `tool.started` 前及回合结束时各冲刷一次，避免逐 token
  生成事件；
- 使用网关既有的敏感文本脱敏函数，单块截断到 **2000 字符**；
- 以 `reasoning.delta` 事件写入 run SSE，payload 只有 `{ "text": "..." }`；
- 不并入 `run.completed.output`、最终消息或 `stream_text`。

兼容性：未应用该补丁的运行环境不会发出此事件，适配层与前端直接忽略；未知事件不会
改变任务状态。`reasoning.available`（上游提供的 ≤500 字推理预览）与
`message.interim`（中间说明）仍按原契约保留，三者都由 Worker 归档为轨迹的“思考/说明”
行，不进入最终回答。

隐私边界：这里暴露的是供应商返回的推理摘要（例如 Anthropic 的 summarized thinking），
不是完整私有思维链。原始 provider reasoning、工具参数全文、供应商错误与终态原始负载
仍不归档。

## 验证与升级

- 构建期：哈希校验 + `ast.parse` + `compileall`；任一锚点不匹配即失败，不会产出半补丁镜像。
- 隔离验收：`tests/hermes/provider_integration.py` 使用真实模型、三个角色容器和受控工具
  响应，覆盖工具往返与 research 重启重放。
- 生产 Profile：`infra/deployment/check-profiles.py` 对每个 owner 的三个 Profile 重新生成
  与新镜像绑定的证据；`activate-verified-profiles.py` 预检通过后才发布注册表。
- 升级上游：先更新 `upstream.json` 的 commit/哈希，再逐个核对补丁锚点；构建失败表示
  上游实现已变化，必须重新设计补丁并重跑上述验收，不能跳过。

## 回退

补丁是镜像内容的一部分，不能单独关闭。回退到旧镜像摘要并恢复对应的 Profile 注册表、
`preparation.json` 与 publication 指针；旧容器镜像和 publication 会保留。具体生产回退
步骤记录在 `docs/implementation-status.md` 的对应部署条目中。
