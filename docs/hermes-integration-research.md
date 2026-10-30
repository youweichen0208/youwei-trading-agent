# Hermes HTTP Runs + SSE 源码核对

核对日期：2026-10-27。范围：架构任务书 §3.1 的 TASK-007 / TASK-015 前置研究。结论仅为官方文档和固定源码的静态证据，**不是已部署或集成验收通过**。

候选源码：NousResearch/hermes-agent commit `085f562af53a6fedadac3681e2c498fc71316132`。所有下列源码链接固定此 SHA；线上文档会更新，不能代替版本锁。未执行 Hermes 源码、安装依赖、构建镜像或调用模型。

## 核对结果与接入约束

| 能力 | 固定源码证据 | 对 YTA 的约束与仍需验证事项 |
| --- | --- | --- |
| HTTP 接口与能力探测 | [Runs 路由与幂等能力](https://github.com/NousResearch/hermes-agent/blob/085f562af53a6fedadac3681e2c498fc71316132/gateway/platforms/api_server_runs.py#L240) 注册提交、状态、events、stop 等接口；[capabilities](https://github.com/NousResearch/hermes-agent/blob/085f562af53a6fedadac3681e2c498fc71316132/gateway/platforms/api_server.py#L2367) 返回 features、endpoints、server-side runtime。 | 启动检查必须保留实际响应。`run_unknown_resolution` 取决于 session authority；不能从接口名称推断当前部署启用了所有语义。 |
| Session 与上下文 | [history 解析](https://github.com/NousResearch/hermes-agent/blob/085f562af53a6fedadac3681e2c498fc71316132/gateway/platforms/api_server_runs.py#L444) 支持显式 history、previous_response_id、input 消息的优先级。 | 真实验证两轮上下文、同 session 排队、重启后的继续对话。不要每次重发旧 history 覆盖上游新上下文。API session 与 YTA session 分别持久化映射；YTA 自己的 history 不能证明上游恢复成功。 |
| 幂等持久化 | [RunIdempotencyStore](https://github.com/NousResearch/hermes-agent/blob/085f562af53a6fedadac3681e2c498fc71316132/gateway/platforms/api_server_run_idempotency.py#L53) 使用 SQLite 唯一 scope/key 及事务，默认 `runs_idempotency.db`；常量保留期 24 小时。打开 DB 失败会回退内存，并通过 `durable=false` 暴露。 | 必须要求 `runs_idempotency.supported=true` 且 `durable=true`，确认卷和目录权限。保留期不是永久去重承诺；[清理逻辑](https://github.com/NousResearch/hermes-agent/blob/085f562af53a6fedadac3681e2c498fc71316132/gateway/platforms/api_server_run_idempotency.py#L169) 还区分 acknowledged、retention_until 和 updated_at。BFF 必须保留自身请求身份和发送状态。 |
| 请求身份与重试 | [scope](https://github.com/NousResearch/hermes-agent/blob/085f562af53a6fedadac3681e2c498fc71316132/gateway/platforms/api_server_runs.py#L375) 包含 profile 和预期 API credential 的摘要；[重放响应](https://github.com/NousResearch/hermes-agent/blob/085f562af53a6fedadac3681e2c498fc71316132/gateway/platforms/api_server_runs.py#L499) 返回原 run ID，202 和 `Idempotency-Replayed`。 | 换 credential/profile 会改变作用域；不能用轮换密钥解决未知执行。验证相同请求并发只运行一次、不同 payload 冲突、响应丢失后同 key 恢复；超出保留期不能自行新建 key 重发未知请求。 |
| SSE 断线与保留窗口 | [_RunStream](https://github.com/NousResearch/hermes-agent/blob/085f562af53a6fedadac3681e2c498fc71316132/gateway/platforms/api_server_runs.py#L120) 是进程内 deque，最多 1000 项；[events handler](https://github.com/NousResearch/hermes-agent/blob/085f562af53a6fedadac3681e2c498fc71316132/gateway/platforms/api_server_runs.py#L1206) 支持 `Last-Event-ID` / `last_seq`，输出 id 和 seq，缺口输出 `replay.truncated`，慢订阅者可被断开。 | 断线续接不等于跨重启完整事件回放。BFF 要持久化已收事件、按序号去重并显式记录缺口；注释 keepalive 不是业务事件。验证工具事件、终态、1000 项溢出、慢消费者及重启后 events 404 的降级到状态查询。 |
| 重启与未知执行 | [durable status](https://github.com/NousResearch/hermes-agent/blob/085f562af53a6fedadac3681e2c498fc71316132/gateway/platforms/api_server_runs.py#L411) 优先 canonical projection，legacy stale owner 可映射 interrupted；[canonical projection](https://github.com/NousResearch/hermes-agent/blob/085f562af53a6fedadac3681e2c498fc71316132/gateway/platforms/api_server_authority_runs.py#L68) 明确保留非终态 unknown。 | 不可把所有重启统一解释为 cancelled/failed。unknown 可能阻塞后续轮次；人工协调流程与恢复依据仍待验证，禁止自动换 key 重发。 |
| Cancel / stop | [stop handler](https://github.com/NousResearch/hermes-agent/blob/085f562af53a6fedadac3681e2c498fc71316132/gateway/platforms/api_server_runs.py#L1447) 和 [authority stop](https://github.com/NousResearch/hermes-agent/blob/085f562af53a6fedadac3681e2c498fc71316132/gateway/platforms/api_server_authority_runs.py#L138) 区分 queued、started、unknown 和已终态。started 可先返回 stopping；unknown 拒绝为 unknown_execution。 | HTTP 成功仅表示控制请求结果，须继续对账真实终态；取消也不能证明已发生的工具副作用被撤销。验证重复 stop、排队/执行中 stop、断线和重启后的 stop。 |
| Profile 隔离 | [prefix 解析与 scope](https://github.com/NousResearch/hermes-agent/blob/085f562af53a6fedadac3681e2c498fc71316132/gateway/platforms/api_server.py#L1507) 拒绝未知/未服务 profile；[run scope](https://github.com/NousResearch/hermes-agent/blob/085f562af53a6fedadac3681e2c498fc71316132/gateway/platforms/api_server_runs.py#L375) 绑定身份。 | 不是应用级租户隔离已通过的证据。MVP 仅服务端 allowlist 的 alpha-orchestrator；验证错误 key、错误 profile、跨 profile run 查询/订阅/stop 全部拒绝。浏览器只访问 BFF。 |
| 运行环境与版本 | [pyproject.toml](https://github.com/NousResearch/hermes-agent/blob/085f562af53a6fedadac3681e2c498fc71316132/pyproject.toml#L5) 版本为 0.0.0；注释明确当前只支持 Python 3.14，宽 requires-python 用于旧安装升级，部分依赖受 3.14 条件控制。[Dockerfile](https://github.com/NousResearch/hermes-agent/blob/085f562af53a6fedadac3681e2c498fc71316132/Dockerfile#L1) 含独立 SQLite 构建阶段。 | 用 SHA 锁候选，不用 0.0.0 充当可识别发行版本。Hermes 应使用独立运行环境，不装入 Alpha 的 Python 3.12 环境。镜像 digest、启动命令、持久卷、运行时依赖与供应商凭证仍须部署验证。 |

## 官方文档交叉核对

[Programmatic Integration](https://hermes-agent.nousresearch.com/docs/developer-guide/programmatic-integration) 将 HTTP、TUI gateway 和 ACP 列为不同接入面；本项目仅以 HTTP Runs + SSE 为 MVP，不把其他协议的控制能力当成 HTTP 证据。

[API Server](https://hermes-agent.nousresearch.com/docs/user-guide/features/api-server) 描述启用 API server 后运行 `hermes gateway`、Bearer 鉴权、Runs 的独立幂等账本及 session 延续；也说明 API 不能直接绑定其他 surface 拥有的 session。其 Chat/Responses canonical 与 legacy 幂等描述不能直接套用到 Runs。真实 provider、工具与状态目录必须按候选版本配置后验收。

## 验收记录要求

以上每项目前均为**待真实集成验证**。应记录候选 SHA、镜像 digest、配置摘要、capabilities 原始响应、请求与 SSE 脱敏样本、持久化路径，以及每次故障注入的 run ID / request ID / key 摘要和最终状态。真实案例应覆盖多轮上下文、工具成功与失败、响应丢失、并发重复、payload 冲突、SSE 缺口、运行中重启、stop 与 profile 越权。

离线模拟或源码中的上游测试只能说明预期行为，不能替代 TASK-007 / TASK-015 的锁定版本运行证据；未验证前不得把矩阵改为已验证，也不得据此宣布 TASK-027 真实适配完成。
