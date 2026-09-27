# Hermes / Pi Runtime 能力核实

核实日期：2026-09-26。依据当前官方文档与官方仓库示例；没有部署 Hermes、Pi 或 gVisor，也没有验证用户的 ECS 环境。以下结论证明存在集成接口，不证明整套方案已经满足安全、隔离或恢复要求。引用的 `main` 会变化，实施时应固定版本与提交。

## 已确认能力及其边界

| 项目 | 官方可确认 | 对本架构的影响 |
| --- | --- | --- |
| Hermes 程序化运行 | Python `AIAgent.chat()` / `run_conversation()`；支持 `enabled_toolsets`、`skip_memory`、`skip_context_files`；实例不能跨并发任务共享。[Python Library](https://hermes-agent.nousresearch.com/docs/guides/python-library) | 每个研究任务创建独立实例，通过应用 Adapter 调用；不要把共享聊天会话当多租户运行容器。 |
| Hermes 并行委派 | `delegate_task(tasks=[...])` 可并行；子任务有独立上下文，继承父工具权限；支持输出 schema，但校验失败仍可能返回 `completed` 与 `schema_valid=false`。[Delegation](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/features/delegation.md) | 应用必须自行检查结果 schema，再决定是否完成研究步骤。 |
| Hermes 故障恢复 | 完成事件可持久化；官方明确正在执行的 child 不会因该机制而在崩溃后续跑，执行状态可能变为 `unknown`。[Delegation：Durable background completions](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/features/delegation.md#durable-background-completions) | 运行状态、步骤重试、幂等入账、租约与取消应由应用 Workflow 持有；Hermes 负责研究规划和综合。 |
| Pi 嵌入运行 | TypeScript SDK `createAgentSession()` 可配置模型、工具、资源加载与会话；可用内存 SessionManager。[SDK](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/sdk.md) | 采用 Node/TypeScript Pi Worker 较自然；Hermes / 核心业务可继续使用 Python。 |
| Pi 跨语言调用 | `pi --mode rpc` 使用 stdin/stdout JSONL，支持运行事件、取消和会话控制。[RPC](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/rpc.md) | Python 也能通过受控子进程集成，但 RPC 不是授权或沙箱边界。 |
| Pi 替换工具 | 同名 `registerTool()` 可以替换内置工具，官方示例提到远端执行和权限检查。[Tool override example](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/examples/extensions/tool-override.ts) | 向 Sandbox Runner 路由有官方扩展点；完整覆盖仍需测试。 |
| Pi Extension 权限 | Extension 与 Pi 进程享有相同 OS 权限，能接触文件、凭证、提示词和会话。[Extensions](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/extensions.md) | 只加载人工审核、固定版本的扩展；模型生成代码必须留在沙箱，禁止生成后热加载为 Worker 扩展。 |
| LLM Gateway 接入 | Hermes 支持 custom provider / `base_url`；Pi 支持 `models.json` 的 `baseUrl`、API 类型与认证配置。[Hermes FAQ](https://hermes-agent.nousresearch.com/docs/reference/faq#can-i-use-it-offline--with-local-models)、[Pi models](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/models.md#configure-a-compatible-endpoint) | 接入私有网关具备接口基础；流式工具调用、用量归集、取消和限额须对选定网关实测。 |

旧 `badlogic/pi-mono` 官方链接当前重定向到 `earendil-works/pi`，当前 SDK 文档的包名为 `@earendil-works/pi-coding-agent`。已有旧版本可以继续按其文档集成，但不能混用新旧 SDK 参数。[当前 SDK](https://github.com/badlogic/pi-mono/blob/main/packages/coding-agent/docs/sdk.md)

## Hermes 的隐式记忆与进化通路

内置 `MEMORY.md` / `USER.md` 可以一起关闭；仅关闭前者仍会保留 profile 工具。独立外部 memory provider 不受这两个开关控制。后台自我复盘还能修改 memory 和 skills；关闭通知只改变显示，关闭自动 review 则使用 `auxiliary.background_review.enabled=false`。会话检索 `session_search` 是另一种历史输入来源。[Memory](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/features/memory.md)

建议研究 Runtime 使用以下配置意图，具体生效路径在固定版本上验证：

```yaml
memory:
  memory_enabled: false
  user_profile_enabled: false
auxiliary:
  background_review:
    enabled: false
```

Adapter 使用 `skip_memory=True`、`skip_context_files=True`，采用研究工具白名单；禁用会话跨任务检索、技能写入和通用终端工具。技能作为只读、版本化的部署产物，所有业务研究经验来自带 `as_of` 的 ResearchMemory。

Hermes 提供 `skills.write_approval`，将 `skill_manage` 写入暂存等待审批；内容扫描设置不等于批准控制。[Skills](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/features/skills.md#gating-agent-skill-writes-skillswrite_approval) 平台已有自己的候选登记和人工批准流程，建议只从该流程发布研究规则，避免两套审批与状态来源。

## Pi 的隔离接口应收紧

当前 CLI 区分 `--no-builtin-tools`（保留扩展工具）和 `--no-tools`（关闭全部工具），还可关闭资源自动发现。过去示例里的参数语义不能直接套用当前版本。[CLI](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/cli.md#tools)

RPC 自带单独的 `bash` 命令以及会话文件切换、HTML 导出能力，因此不能仅凭模型看不到内置 bash，就断言整个 Worker 无法在本地执行或访问文件。[RPC commands](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/rpc-commands.md#bash) 若使用 RPC，外层只接受平台 Job，严格白名单 RPC 命令；不把原始 RPC 暴露给用户或模型。优先用 SDK 显式构造资源加载器和工具集，官方已有完全接管资源发现的示例。[Full control example](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/examples/sdk/12-full-control.ts)

推荐工具面只保留 `snapshot_manifest`、`quant_run`、`sandbox_submit`、`sandbox_status`、`artifact_read`。模型提供的数据对象 ID 必须经服务端按租户与 run 授权。若保留交互式 `read/write/edit/bash`，全部目标均须是隔离 job 文件系统，并验证路径穿越、符号链接、并发读写和取消。

标准 event study / IC / backtest 直接调用版本化 quant 库；只有需要生成新代码的探索性工作进入 Pi。这样同一个确定性分析既可由系统预测调用，也可由 Pi Extension 调用，避免每次标准计算都支付 Agent loop 成本。此段属于架构建议。

## Phase 0 验收应从“跑通”改成故障与权限证明

1. 固定 Hermes/Pi commit、依赖锁与镜像摘要；Hermes 当前官方说明依赖源码环境，并没有支持的 wheel/sdist 安装包，不应假设普通 PyPI SDK 发布流程。[Python Library](https://hermes-agent.nousresearch.com/docs/guides/python-library#installation)
2. 两个租户并行执行，检查上下文、memory、skills、会话记录和工具结果不串任务；重启后不加载未经批准的规则。
3. 在三个研究角色完成不同阶段时杀死进程，确认 Workflow 可以恢复；迟到或重复回包只完成对应 attempt，三来源预测不会重复入账。
4. 向 Worker 输入要求运行本地 bash、读取凭证、加载扩展、读其他 job 的样例，验证访问被拒绝；沙箱超时必须终止计算并回收资源。
5. 对选定 LLM Gateway 验证所有主调用、子 Agent、重试、压缩与辅助调用均经过网关；run 级成本与取消能闭合；并发预算扣减不会超卖。
6. 使用真实依赖在目标 ECS + gVisor 中执行 quant 作业，验证 Parquet 读写、内存/磁盘/进程上限、网络隔离和产物校验；这些部署能力本次未验证。

核心结论：Hermes + Pi 可以保留，但应通过薄 Adapter 接入由业务代码管理的持久执行流程。将研究规划能力、可信扩展能力、不可信代码执行能力分别约束，才能使 Ledger、PIT 和受控进化原则在运行时成立。
