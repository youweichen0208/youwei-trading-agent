# MVP 是否继续接入 Pi：运行时评估

日期：2026-10-01  
状态：已采纳（2026-10-01，项目所有者决定）。S08、架构与 AGENTS 已同步更新为 Hermes 唯一 Agent 框架；未升级版本锁。

## 结论

建议 MVP 暂缓接入 Pi，优先复用 Hermes 实现研究与探索。研究实例和实验实例使用独立上下文、不同工具权限；Controller、quant 库、Sandbox Runner 和产物契约继续保留。这里减少的是需要维护的 Agent 框架，不减少实验隔离和验收要求。

此判断依据是当前任务范围和已接入资产；没有进行同模型、同任务的代码成功率、内存、时延或费用对比，不能推断 Hermes 性能优于 Pi。

## 版本与观察范围

- 项目仍固定 Hermes `7fa45eb349a1a6f1eebc010b3fef0a9d996f386a`、Pi `0.87.1`；Pi 尚未业务接入，见 [版本锁](../../infra/upstreams.lock.yaml) 与 [S08](../IMPLEMENTATION_PLAN.md#s08--接入一个-pi-探索任务phase-1b依赖-s03s07)。
- 2026-10-01 查询 Hermes 官方 API：最新发布为 `v2026.9.24`（9 月 24 日发布）；`main` 为 `12e4d3e2dd5281adf289f70e5d6c7f33427d6982`（提交时间 9 月 30 日 22:42:13 UTC）。这是观察记录，未做该提交的项目兼容性验收。[Release API](https://api.github.com/repos/NousResearch/hermes-agent/releases/latest)、[Commit API](https://api.github.com/repos/NousResearch/hermes-agent/commits/main)
- 下述 Pi 能力来自查询日官方 `main` 文档，不意味着所有接口已在项目固定的 `0.87.1` 上核实；实际接入仍须对固定版本验收。官方仓库旧地址已重定向至 `earendil-works/pi`。[官方仓库](https://github.com/earendil-works/pi/tree/main/packages/coding-agent)

## Pi 能提供什么

| 能力与官方事实 | 对本项目的价值与约束（判断） |
| --- | --- |
| SDK 可嵌入 Node/Bun；`createAgentSession` 提供会话，能显式配置工具、资源加载器、内存会话及模型。[SDK](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/sdk.md) | 有利于 TypeScript 服务与精细控制的 Agent 宿主；当前研究与 Controller 主体为 Python，新增 Node 包不是现阶段必要条件。 |
| RPC 通过子进程 stdin/stdout JSONL 提供命令、响应与事件；命令接收成功不等于 Agent 工作完成，客户端仍需处理退出、取消与期限。[RPC](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/rpc.md) | 比自行设计交互式会话协议更现成；仍需项目 wrapper，把完成、取消、重试映射到 Controller，而非直接把 RPC 状态当业务真相。 |
| Extensions 能注册工具、拦截/阻止工具调用、变换上下文和增加 UI；完整自定义终端组件只在交互模式提供，RPC 仅转发部分 UI 交互。[Extensions](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/extensions.md) | 工具路由与定制上下文是实际价值；后台研究任务不直接受益于完整 TUI，也不能由 Extension 代替服务端授权。 |
| 提供树状会话、分支、压缩、编辑器和技能/扩展分发；默认工具含 read/write/edit/bash，扩展包运行于宿主并具系统访问能力。[README](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/README.md) | 人工参与的反复改代码/探索工作台可能更有价值；当前场景须限制默认工具、自动发现资源及管理命令，生成代码依然交 Runner。 |

## Hermes 能否承接探索角色

- 官方 Python Library 支持独立 `AIAgent` 实例、工具集选择和自定义系统提示；适合分别建立研究与实验上下文。[Python Library](https://hermes-agent.nousresearch.com/docs/guides/python-library)
- 官方支持子 Agent 委派及结构化输出，但任务完成状态不保证 schema 校验成功；子 Agent 权限继承需要核对。项目仍需由 Controller 检查结果与权限。[Delegation](https://hermes-agent.nousresearch.com/docs/user-guide/features/delegation)
- 官方支持插件注册自定义工具，无需修改上游核心；本项目已有 [Hermes 工具适配](../../services/agent-runtime/src/youwei_agent_runtime/tools.py) 与 [Controller 子进程客户端](../../youwei_core/ledger/agent_client.py)。目前注册的平台工具为 `snapshot_manifest`，计算往返尚待 S08 实现。[Adding Tools](https://hermes-agent.nousresearch.com/docs/developer-guide/adding-tools/)
- Hermes 内置 `execute_code` 在 Agent 宿主子进程运行并使用 Unix socket，不等于项目 gVisor Sandbox Runner；研究/实验实例均应维持明确工具允许列表，经 Controller 授权进入 Runner。[Code Execution](https://hermes-agent.nousresearch.com/docs/user-guide/features/code-execution)

**反例检查**：如果下一阶段必须交付嵌入式 TypeScript Agent 工作台、细粒度会话分支/编辑器体验，或已有可复用 Pi 扩展需要直接运行，Pi 的现成接口可能节省开发。当前 [S08](../IMPLEMENTATION_PLAN.md#s08--接入一个-pi-探索任务phase-1b依赖-s03s07) 要求的是受控计算与产物闭环，未发现必须依赖上述 Pi 专属接口的验收项；这不是 Hermes 已覆盖所有 Pi 功能的断言。

## 建议的 S08 实现方向（待决定）

1. 保留“研究请求 → Controller 授权 → 执行 → 产物引用”闭环；标准量化直接调用已测试 quant 库。
2. 库未覆盖时，Controller 启动独立 Hermes 实验实例，输入冻结快照说明与明确问题；生成代码作为不可信作业输入，交 Runner 执行。
3. 提交、状态查询、产物读取分别授权；验收取消、重试、超时、partial、warnings、代码/环境/输入 hash 和跨作业隔离。不能仅增加 prompt 就声称完成 S08。
4. 实验输出由研究实例消费，再返回 Proposal；Controller 继续掌握任务状态和正式封存。研究/实验实例不共享可自动演化的记忆。
5. 保留 Pi 为可替换的实验 Agent 适配器候选；若 Hermes 暴露具体缺口，预登记比较后用同一任务、模型、工具与沙箱条件比较，再决定接入。

## 维护成本与下一步

继续接 Pi 将新增 Node/TypeScript 包和锁、RPC wrapper、资源发现及管理命令限制、工具适配与取消/升级契约测试；这些正是 S08 的未完成工作。单 Hermes 仍须补齐实验角色、受控工具往返与验收，不能把省掉第二套运行时等同于省掉实验工程。

暂缓 Pi 的建议不依赖立即升级 Hermes `main`；先检查当前固定版本能否实现所需接口，有明确缺口才升级。该建议已于 2026-10-01 采纳：S08 改写为「受控量化探索闭环（Hermes）」，架构与 AGENTS 的角色表述、上游锁中 Pi 的定位均已同步。
