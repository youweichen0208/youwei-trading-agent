# MVP 闭环执行记录

最新范围：用户指定个人本地 MVP，使用 Massive 和 pi 模型；远端仓库/云 staging 延后。本轮实际行情、资格快照和本地部署证据见 [实施状态](implementation-status.md)。以下为历史执行记录，不能当作当前阻塞列表。

2026-10-31 code review 修复后，日汇总必须保留逐响应实际采集时间，小数成交量契约使用字符串；Massive 合格快照必须登记账户权限、用途许可、公司行动覆盖及追加式修订政策，缺失证据的旧记录不再可用。这些改动不替代外部资格验收，也不改变真实模型研究、有效窗口确认和实际五交易日结算仍待完成的状态。

延迟原因复核：macOS 电源日志确认标准入口真实模型运行期间发生合盖休眠（08:18:00 UTC+8）及多次后台唤醒，覆盖工具完成到首段文本的约 49 分钟间隔。该样本现标为主机休眠影响，不能用于 Provider 时延判断；无需据此重发原研究或修改应用。完整正常运行时延与部署超时验收仍待完成。

标准入口真实模型增量：新封装 BFF 的 `node dist/main.js` 已通过显式配置运行 pi 真实模型，BFF/Web/Alpha/工具无源码挂载。唯一研究完成后，原运行的冻结、模拟结算、刷新与手机历史通过，796 条事件重放通过，无重复模型请求。初次浏览器观察 300 秒超时；数据库显示工具完成到首段文字约 49 分钟，尚不能归因于 Provider、网络或本机暂停，时延/超时门槛保持未验收。证据见 `infra/verification/hermes-configured-browser-observation.json`，当前候选清单已更新。

配置入口增量：标准 BFF 启动现支持显式 HERMES origin/token/model，通过配置构造既有真实驱动；204 项 TS（含真实 PG）、typecheck/build 通过，staging/production 仍因完整门槛未验收而拒绝启用。证据见 `infra/verification/hermes-configured-driver-observation.json`。尚未重新运行该配置入口的真实模型浏览器链路；下面封装候选证据早于这一 BFF 增量。

最新增量：2026-10-30，BFF/Web 已按当前代码重新构建并完成无源码挂载的浏览器 REPLAY 验收；新独立测试库执行 27 份迁移，登录→研究→确认冻结→模拟结算→刷新/手机历史→退出通过，页面错误 0。最新候选见 `infra/verification/mvp-candidate-observation.json`；旧候选与原全量 Python/恢复记录已归档至 `infra/verification/archive/pre-current-ui-candidate/`。真实行情/LIVE、Hermes 完整门槛、正式驱动配置、远端 CI/Review 和 staging 仍未闭环。

2026-10-30：按用户要求优先完成MVP，暂停新增Beta/v1功能；用户已将当前goal重新设为完成整个MVP。范围以架构任务书1.2及15.1为准：NVDA、日线、单Profile、5交易日baseline、真实研究、确认冻结、结算与追溯、安全部署。训练模型及其审核不属于此次关键路径。

## 启动时核对结果（历史；最新状态见 implementation-status.md 页首）

| 关键路径 | 已有权威证据 | 尚缺的闭环 | 下一动作 |
| --- | --- | --- | --- |
| 真实研究 | 固定Hermes源码/镜像、pi真实模型与工具/取消/恢复探针；infra/hermes/source-lock.json六门槛仍UNVERIFIED | apps/bff/src/research-runner.ts只有ReplayResearchDriver和UnavailableResearchDriver，没有应用真实驱动 | 整理现有探针可证明的协议，落实服务端session/run映射、单次提交、SSE与未知执行对账；复用pi配置验证 |
| 真实行情 | massive.py、不可变采集/质量/日线/缓存及socket故障测试 | 实际账户权限、两年样本与公司行动覆盖、数据用途和时间口径资格；不得把REPLAY当真实行情 | 在现有候选适配器上完成授权来源的实际采集与资格核对，再接LIVE，不重写数据层 |
| 冻结/结算/历史 | Alpha事务、真实PG并发/断连/恢复；浏览器显式确认、历史及版本化结果已有证据 | 上述两个真实上游接入后的完整用户链路及真实调度 | 复用现有工作台与Jobs，贯通真实研究→确认→冻结→到期结算→固定报告，不新增模型功能 |
| 安全与部署 | 本机容器隔离、备份恢复、CI定义及本地监控 | 当前源码对应发布产物、远端CI/Review、staging HTTPS、回滚与真实告警 | 最后统一构建候选、核对迁移/权限、执行部署与恢复验收；部署前给出具体可审阅结果 |

本轮运行了scripts/release_inventory.py --inspect-local-images，结果为stale_or_missing（.local/mvp-release-inventory.json）。Docker服务可用；该工具引用的旧app-local-observation四个应用/迁移摘要在本机不存在，Hermes摘要存在，多处源码已变化。这说明旧发布清单不适用于当前代码，不能据此声称所有较新镜像缺失，也不能用旧测试记录证明新发布。

根目录没有.git，尚无可核验的远端CI/PR执行记录。配置文件存在不算部署或Review完成。外部接入信息需要时应从本机/已连接服务安全读取，不能要求用户在聊天粘贴密钥。

## 执行顺序与验收边界

1. 先完成Hermes应用驱动及真实pi模型链路。未知执行保留原请求身份核对，禁止换键重发；不把探针全部通过等同应用接入通过。
2. 接真实行情及资格证据，复用已有Alpha baseline与Jobs完成真实冻结/结算。未来5个交易日不能用伪造时钟冒充LIVE到期；历史协议可用隔离回放验证，实际前瞻结果单列等待。
3. 对15.1的13条Gate逐条记录实际运行证据和未满足项，统一执行适用回归、部署候选、迁移、备份恢复与故障演练，再请求必要的最终部署批准。
4. 按代码变更范围运行测试，不为每个文档/页面改动重复全量数据库恢复；发布候选集中执行完整套件。任务DoD及外部验收不降级，不用已启动任务比例冒充完成比例。

本记录不是MVP验收通过报告。当前仍有真实上游和部署工作，goal保持active。

## 2026-10-30 客户端落地

TASK-027已开始：私有固定Profile传输、无自动重发、状态/停止、有界SSE及真实锁版本无凭证失败运行通过，见infra/verification/hermes-client-observation.json。应用仍使用UnavailableResearchDriver；下一步是持久化映射与接入驱动，不能把此客户端验证当作真实模型工作台完成。

## 2026-10-30 持久化发送边界

迁移025及HermesStore/admitHermesRun已实现单次发送登记、会话运行域绑定与回执固定。真实PG并发、HTTP接收后失响应、不可变/审计回滚及恢复后不重发通过，见infra/verification/hermes-store-observation.json。下一个关键动作是ResearchRunner接入、事件转换/持久化和取消终态对账，随后复用pi配置做真实应用链路。

## 2026-10-30 驱动与取消对账

HermesResearchDriver已有单次admission、受限事件转换与实际取消终态对账。修复ResearchRunner及数据库完成/取消竞态误报取消的问题，完整TS189项通过。仍需事件游标持久化、恢复和可信报告完成回调，默认应用未切换到Hermes；见infra/verification/hermes-driver-observation.json。

## 2026-10-30 游标恢复完成

迁移026和HermesStore已将上游游标、工具关联状态与公开事件原子保存。实际工作进程恢复测试从已保存序号续读，零prompt重发、零重复文字；最终完成事件原子提交。真实PG恢复核对15请求和12条上游事件，完整TS192项、浏览器65项通过。当前关键动作收敛到可信工具结果/报告绑定与pi真实模型应用链路；默认驱动仍未启用Hermes。

## 2026-10-30 可信工具回执推进中

Alpha新增研究run固定快照及不可变工具回执，MCP显式上下文模式已实现，真实PG/MCP专项通过。BFF尚未用这些回执完成报告核验，pi真实模型应用链路、027恢复及发布验证仍待完成。工具回执ID独立于Hermes事件call ID，不将两者视为相同身份。

## 2026-10-30 pi真实模型应用验收通过（REPLAY行情）

已用pi选中volc-glm/deepseek-v4-pro完成已认证BFF→Hermes→MCP→Alpha→独立PG的真实调用：两种金融工具回执、固定快照草稿、模型报告核验与原子完成均通过。验证模型没有自动冻结，随后通过登录测试用户显式确认冻结一条REPLAY预测；同消息重试仍为原run。见infra/verification/hermes-application-observation.json。

下一阶段仍需真实行情资格及LIVE链路、浏览器模型呈现核验、027恢复与封装发布部署验收。此前共享测试库遗留任务被探针队列扫描的问题已改为全新独立空库并加启动检查，原运行均已核对终态。不能将此次真实模型配合合成行情的成功当作MVP全部Gate通过。

## 2026-10-30 历史浏览器与恢复验收推进

真实pi模型数据已备份恢复：49表、47不可变保护、6序列、1份冻结预测、2份工具回执重放通过，恢复后原请求无新发送许可。浏览器实际登录读取恢复记录，摘要/来源/风险、刷新、手机与预测详情通过。修复已完成会话重新订阅历史SSE导致原始JSON覆盖摘要的问题。仍需浏览器新研究完整交互、真实行情资格/LIVE与发布部署，不能关闭全部MVP Gate。

## 2026-10-30 当前发布候选与全量恢复

当前BFF/Web/Alpha/finance-tools/migrator固定镜像记录在infra/verification/mvp-candidate-observation.json。打包模块及27份迁移哈希核对通过，migrator全新/升级/角色测试通过；全量Python843项和真实备份恢复通过（49表、47不可变保护、46份预测、20份结算）。候选仍为LOCAL_CANDIDATE_NOT_RELEASE_QUALIFIED，尚需封装镜像整链路运行、真实行情资格、远端CI/Review与staging验收。

## 2026-10-30 封装候选默认REPLAY整链路通过

固定候选镜像无源码挂载运行，浏览器完成登录、研究、确认冻结、模拟结算、刷新及历史展示；1预测/1outcome，页面错误0，已完成历史不重放SSE。27份迁移由封装migrator执行，Alpha/BFF未发布宿主端口。见infra/verification/mvp-container-observation.json。该证据补齐候选默认REPLAY运行，不证明真实LLM/LIVE或staging部署。

## 2026-10-30 浏览器新提交真实pi模型流程通过（REPLAY）

浏览器实际登录并新提交唯一研究，pi模型完成两种工具调用及可信报告；随后页面确认冻结、模拟结算、刷新/手机历史、注销通过。默认驱动不变，测试显式注入Hermes；/me新增实际运行方式，页面不再把模型REPLAY误标为固定流程。见infra/verification/hermes-live-browser-observation.json。增量结构化输出的用户呈现仍需核验，真实行情和发布部署条件仍未满足。

## 2026-10-30 结构化流展示修复

Hermes JSON流现在仅渐进展示summary，原始协议字段不直接显示。202项TS、66项浏览器回归通过；真实pi模型已保存689片段重放形成212次渐进摘要更新，最终与核验报告一致，不新增模型调用。剩余关键交付仍是真实行情/LIVE、Hermes完整资格与当前源码封装发布、远端CI/Review及staging验收。

## 2026-10-30 Hermes 未授权工具调用验收增量

固定 Hermes 镜像在无外网、无真实凭证的合成模型环境中完成 8 次 Gateway Run、2 Profile 并发验证：terminal/read_file/write_file/delegate_task 共 32 次调用收到明确拒绝结果，文件副作用标记不存在。见 `infra/verification/hermes-forbidden-tools-observation.json`。初始 3 次运行因探针对拒绝文本尾部空格的严格比较失败，已核对终态并保留记录；修正后独立合成批次通过。该证据仅覆盖空工具目录下全非法批次，不证明金融工具混合批次、OS 文件沙箱或全部 Hermes Gate；任务仍 IN_PROGRESS，真实行情/LIVE 与发布部署仍待验收。
