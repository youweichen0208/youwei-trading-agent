# 实施状态与验收证据

## 2026-10-30 本地个人 Massive MVP（进行中）

用户已明确：个人使用、本机作为测试环境、暂不建立远端仓库。本轮以独立本地 LIVE_RESEARCH 环境验收；远端 CI、云 staging 另列后续范围，不再作为本地试用的前置阻塞。完整架构发布 DoD 不据此自动完成。

- 已初始化本地 Git；基线 b3e8a7e。API key 仅存在被忽略的 0600 私有配置，不进入 Git、BFF 或浏览器。
- 实际账户成功采集 2024-10-31 至 2026-10-29 的 NVDA RAW 日线 500 根、完整拆股响应一页；最近 21 日逐日与 Daily Ticker Summary 比较全部相等。采集 hash：744b6c7aff5c6de6cb37cae455c356c6668c5a697723a06d80231f00ac966ed3。无缺失 session/提前最终日线，采集时间作为 available_at。
- 迁移 028 追加 market_qualifications 和审计；原始 acquisition 永远 UNVERIFIED。发布校验完整原始分页、21 日摘要、Nasdaq 有界日历（2024-10-31 至 2026-12-31）及 5 日目标范围；读取时重新构建并比较证据/快照，避免只信任通过标记。超出日历范围拒绝，不能外推其他证券。
- 实际数据含小数成交量。MarketBar 新增精确 Decimal/string 支持；旧整数快照保留原序列化以保持哈希可重现。依据 [Massive 官方说明](https://massive.com/knowledge-base/article/why-does-volume-return-as-a-decimal-value-from-the-aggregates-endpoint)，不舍入或截断。
- Alpha API 和 Jobs 共享只读合格快照；配置 MASSIVE 仅允许 local development/test + LIVE_RESEARCH + DB。供应商访问仅由独立采集脚本执行，免费额度每次请求前等待 15 秒以上；模型、浏览器请求不触发行情下载。到期无目标行情继续 WAITING_DATA。
- 最终真实 PostgreSQL 全量 855 项通过（另新增限速测试单独通过）；备份恢复核对 50 张表、48 组不可变保护和 6 个序列。TS 205 项（43 契约 + 162 BFF，含实际 PG）通过；typecheck/build、Ruff、契约和文档检查通过。
- 浏览器 LIVE 登录、500 根实际日线、指标、桌面/手机布局通过，页面错误 0、浏览器请求仅 localhost:3301。两个环境共用数据库网络导致 Alpha DNS 名冲突，已通过独立 LIVE 网络和明确别名修复。
- 实际行情库备份恢复后重建快照 hash 一致；后台采集器和 Jobs 已运行。真实模型配置已读取 pi agent，但当前冻结窗口关闭，尚未向模型提交 LIVE 研究。证据：`infra/verification/massive-local-observation.json`。
- 当前未声称真实预测闭环完成：需有效冻结窗口内用户确认，并等待真实第 5 个交易日。2026-10-30 盘中不能用 10-29 基准绕过到期草稿限制。


更新：2026-10-30。用户重新设置 goal 为“请最快完成mvp落地”，当前状态为 blocked（恢复后连续三轮仍缺少外部接入信息）；Beta/v1 扩展暂停。上一 goal 因真实行情、staging 和仓库接入信息缺失标为 blocked，这些条件仍未补齐。新一轮已恢复本机可访问服务并完成登录/历史验证，未将本机 REPLAY 可用等同整个 MVP 完成。最新结论见本节，后面的初始记录与增量按时间保留。

## 当前验收摘要

本机交付入口：`http://localhost:3300`。封装 Web/BFF/Alpha/工具与锁定 Hermes 已恢复运行，使用既有独立测试库；启动前核对无未完成任务，恢复未新增模型请求。实际浏览器验证就绪、登录、已完成历史、模型/REPLAY 标识及退出通过，页面错误 0；证据见 `infra/verification/local-mvp-access-observation.json`。登录信息位于忽略目录 `.local/mvp-access.json`（0600，不写入文档），服务保留运行供用户访问。该地址仅绑定本机，不是 staging 或 LIVE 数据服务。

逐项剩余工作见 [13 条 MVP 发布门槛核对](mvp-release-audit.md)。真实 LIVE 快照加载目前仍返回 DATA_UNAVAILABLE；因此剩余工作包括供应商资格和实际接线，不能简化为“已有全部代码，只差密钥”。

延迟复核（2026-10-30）：`pmset -g log` 显示 08:18:00（UTC+8）因合盖休眠，之后反复后台唤醒/休眠，与上次工具完成至首段文本的约 49 分钟间隔重合。已将 `hermes-configured-browser-observation.json` 标为受主机休眠影响的样本；不能把该间隔归为模型推理耗时，也不能据此认定正常时延/超时门槛通过。下文“原因未确认”为当时记录，现有主机电源证据补充于同一 observation。

- 已完成本机真实 pi 模型浏览器研究、两个金融工具回执、用户确认冻结、模拟结算与历史恢复；行情为合成 REPLAY，默认服务未启用真实 Hermes 驱动。证据：`infra/verification/hermes-live-browser-observation.json`。
- 最近回归：TypeScript 204 项（含真实 PostgreSQL）；此前浏览器 66 项、完整 Python 843 项及 PostgreSQL 备份恢复通过。不同套件对应各自记录的源码范围，不以测试总数推算发布完成率。
- Hermes 已取得空工具目录与金融工具混合批次的未授权调用拒绝证据；完整锁版本门槛仍 UNVERIFIED。
- 最新 BFF/Web 已重新封装，固定镜像在无源码挂载环境完成浏览器 REPLAY 全流程；27 份迁移在新独立测试库执行，页面错误为 0。见 `infra/verification/mvp-candidate-observation.json` 和 `infra/verification/mvp-container-observation.json`。旧候选与其 Python 全量恢复证据保存在 `infra/verification/archive/pre-current-ui-candidate/`；Alpha/工具/迁移源码未变。
- 剩余：真实行情资格与 LIVE 研究/结算、Hermes 完整门槛、真实驱动的正式配置与封装验收、远端 CI/Review、staging HTTPS/监控/备份/回滚验收。当前候选仅完成本机默认 REPLAY 验证，仍未取得发布资格。

最新配置增量：标准 BFF 入口支持显式 HERMES 驱动配置，构造已有持久化 HermesStore、报告核验及取消恢复驱动，无需测试脚本注入。缺少 origin/token/model、URL 携带凭证或路径、复用 Alpha token、未选择驱动却遗留配置均拒绝；staging/production 门槛仍关闭。真实 PG 集成验证配置构造与鉴权 `/me` 的凭证隔离，204 项 TS、typecheck/build、文档检查通过。配置测试见 `infra/verification/hermes-configured-driver-observation.json`。

后续封装真实模型验收：BFF 新镜像通过 `node dist/main.js` 标准入口运行，BFF/Web/Alpha/工具均无源码挂载；pi 当前 `volc-glm/deepseek-v4-pro` 完成一条真实研究。浏览器首轮 300 秒观察超时，数据库记录工具完成与首段文字约相隔 49 分钟，原因未确认，不能认定时延或超时门槛通过。没有重发 prompt；原运行完成后，796 条事件重放及用户确认冻结、模拟结算、刷新/手机历史、退出成功。数据库仅 1 条研究请求、2 份回执、1 条预测/1 条结算。见 `infra/verification/hermes-configured-browser-observation.json`。候选已更新为本次镜像，旧封装记录保存在 `infra/verification/archive/pre-configured-driver-candidate/`；真实 LIVE、部署及延迟原因仍待验收。

混合批次证据见 `infra/verification/hermes-mixed-tools-observation.json`：两个金融工具成功，terminal/read_file/write_file/delegate_task 四类非法调用明确被拒绝，无非法工具分发事件和文件副作用。模型收到不可信结果包装与 spillover 预览，测试另行核验完整归档 JSON 为 REPLAY；不将归档核验等同模型读取完整行情。初次探针误按纯 JSON 解析包装导致 failed，核对终态后修正探针并用独立合成批次通过。该测试没有外部模型调用，不证明 OS 文件沙箱或全部发布门槛。

## 初始闭环证据（后续增量见下文）

| 部分 | 当前证据 | 对应任务 |
|---|---|---|
| 工作区 | pnpm 四个 workspace、Python 3.12、锁文件；TypeScript 检查及生产构建通过 | 002、004、023、069 |
| 共享契约 | Zod 预测/冻结/事件/Canvas，Alpha OpenAPI 和合成样例生成漂移检查；3 个契约测试通过 | 025 |
| Alpha 计算 | Decimal baseline、零收益对照、交易日目标、时间截断、拆股、指标 warm-up；20 个单元/API 用例通过 | 041、051–056、060、064 |
| 预测事实仓库 | PostgreSQL 17、迁移校验和、SELECT/INSERT 应用角色；冻结幂等、所有者隔离、到期约束、修订、拒绝 UPDATE/DELETE/TRUNCATE、内部 HTTP 闭环；8 个实际数据库用例通过 | 061–063、068、106 |
| 登录与 BFF 边界 | scrypt、随机不透明 Cookie、会话哈希/撤销/过期、凭证轮换、同源与 CSRF；owner 仅由会话派生，冻结字段白名单 | 024、026、070 |
| 研究运行 | 持久化 session/run/message/event、并发幂等、事件序号和游标、SSE 重连、停止、超时、数据库执行租约；已启动但无终态的运行标 EXECUTION_UNKNOWN，不自动重发 | 028–034、037、072–074 |
| BFF 验证 | 2 个安全单测与 5 个真实 PostgreSQL 集成用例通过；Alpha 上游在该套测试中是 HTTP fixture | 026、030–034、106 |
| 浏览器工作台 | Next.js 登录/会话/研究、500 根日线、成交量/RSI/MACD、SMA/BOLL 开关、工具联动、草稿确认、冻结详情/历史、模拟结算、手机 Chat/Canvas Tab | 069–078、080、081、084、085 |
| 浏览器 E2E | 3 个 Chromium 用例通过，实际 Web→BFF→Alpha→PostgreSQL；验证显式确认、模拟结算、刷新恢复、手机无横向溢出、退出、研究及冻结响应丢失后的同键重试不重复写入 | 086、109 |
| 文档 | 120 个任务唯一、依赖 DAG、优先级闭包、里程碑覆盖检查通过 | 003、006 |

初始闭环验证：`pnpm typecheck`、带 `_test` 数据库的 `pnpm test`（10 项）、`pnpm build`、`uv run ruff check .`、`uv run ruff format --check .`、带 `_test` 数据库的 `uv run pytest -q`（28 项）、`python3 scripts/check_docs.py`、`uv run python scripts/export_contracts.py --check`、`pnpm test:e2e`（3 项）。数据库用例实际执行，未计跳过为通过。

E2E 使用受限应用账号和隔离测试账户；截图输出 `test-results/research-desktop.png`、`prediction-desktop.png`、`research-mobile.png`。截图已检查，未出现浏览器运行时异常。合成预测 0.005、实际 0.02、绝对误差 0.015；这些只是固定回放流程证据，不能用于宣称真实模型成绩。

启动及复现命令见根目录 README。新增 `002_bff_sessions.sql`，不改已应用迁移；本机 `yta`、`yta_test` 已迁移。登录密码仅保存在忽略且 0600 的 `.local/login.txt`，未打印或写入文档。

## 初始阶段未完成项（历史记录，当前以页首及后续增量为准）

- 真实 Hermes Adapter 尚未实现；固定版本已有真实模型、工具、取消与恢复的局部实测，完整能力门槛仍未验收。`ReplayResearchDriver` 是固定回放驱动，不是 Hermes HTTP Mock 或 LLM；它可验证界面与工具闭环，不能替代锁版本契约证据。Hermes session 映射、真实取消/审批/恢复仍待接入。
- 无真实行情 Provider、授权或 point-in-time 证据。LIVE_RESEARCH 明确返回不可用，不退回合成数据。XNYS 日历用于本地测试；NVDA/Nasdaq 正式资格核验仍需完成。
- PostgreSQL 结算函数支持等待数据及目标输入修订；不可评分证据的领域存储与界面已实现，真实证据采集仍待供应商验收；基准行情修订的领域计算/存储已实现，真实修订采集仍待接入；评测的更正预测统计及浏览器基础报告已实现；自动到期 Worker 核心已实现，真实供应商和部署仍待接入；固定 cohort/outcome 版本的基础报告已实现，见下节。
- 消息、session/prediction 列表已具备游标分页，独立 NVDA Overview 和图表响应契约已补齐（见后续增量）；其余前端响应契约及扩展错误场景仍待完善。当前使用 CSS tokens，尚未引入任务书中的 Tailwind。
- 登录为本地开发初始化账户；生产账户管理、可信代理/全站限流、最小权限拆分、安全审查及安全部署配置仍需验收；账号与来源登录限流的本机证据见后续增量。
- BFF 已区分存活和依赖 readiness，Swagger 页面已有本机鉴权/浏览器/打包证据；监控部署、staging、压测和部署 Gate 尚未完成；本地备份恢复演练已通过，生产每日/异地备份仍待部署。
- GitHub Actions 已包含数据库测试及浏览器测试/截图上传，但没有远端执行或 PR Review 证据。
- Python 上游仍有 Starlette TestClient 和 pandas 时区数组弃用提示，当前用例通过。

## 后续顺序

1. 按锁版本协议接入真实 Hermes，并完成取消/未知执行等能力矩阵。
2. 接入有授权和可追溯时间的行情源，完善质量与降级路径后启用 LIVE_RESEARCH。
3. 接入结算 Jobs 的真实数据源和生产调度，补齐不可评分/修订、评测更正统计，以及历史分页和协议边界。
4. 完成 staging、监控、备份恢复、安全审查和 MVP 发布 Gate，再推进 Beta/v1。

模型已按用户选择复用 pi agent 的默认配置，并取得本机真实调用/金融工具 Run 证据（见末尾增量）。正式 Hermes 部署地址、行情供应商授权及完整真实集成仍待验收；不要让用户在聊天中粘贴密钥。

## 任务状态规则

已有代码的任务保持 IN_PROGRESS。完整 DoD 包括前置依赖、真实集成、PR/CI/Review 与对应部署验收；本机测试只是一部分证据。没有实现的能力仍为 TODO，不将本次浏览器闭环标为整个 MVP 或发布完成。

## 后续增量：不可变评测报告

- 新增 `003_evaluation_reports.sql`，本机 `yta`、`yta_test` 已应用；报告受 SELECT/INSERT 权限及禁止 UPDATE/DELETE/TRUNCATE 的触发器保护。
- Alpha `evaluation.py` 按 owner、data_mode、基准日区间、固定模型/协议及 cutoff 纳入全部符合条件的预测，不提供任意 prediction ID 子集筛选。一个数据库查询快照选择截止时间之前的最新 outcome，固化 prediction/outcome ID、settlement_version、forecast hash、代码版本和结果。
- Decimal 50 位计算方向命中率、MAE、零收益对照 MAE；无评分样本时返回 null。单列 SETTLED、UNSCORABLE、WAITING_TARGET、WAITING_DATA、SETTLEMENT_FAILED，并按基准日披露数量。
- 内容哈希去重及事务审计；后续结算修订产生新报告，历史报告不变。读取校验所有者。
- 新增 2 个单元用例及 2 个真实 PostgreSQL 用例，覆盖 Decimal 精度、空指标、区间/时区、历史 cutoff、结算版本固定、模式/归属隔离、完整 cohort、并发去重及权限。Python 共 32 项通过；Ruff、生成契约检查和文档检查通过。此增量未改变 Web/BFF，沿用上一轮前端构建和 E2E 证据。
- 当前提供内部 Python API 与 CLI；公开 BFF/报告界面、更正预测数量、训练模型比较和正式数据验收仍待实现，TASK-064 保持 IN_PROGRESS。

## 后续增量：到期结算 Worker

- 新增 `004_settlement_attempts.sql`，本机两个数据库已迁移；每次处理追加 attempt、重试时间和审计，应用角色仍只有 SELECT/INSERT。
- `yta_alpha.jobs` 支持单批或持续轮询，按配置 data_mode 扫描到期且没有 outcome 的预测。数据库连接级 advisory lock 防止多个 Worker 同时取数；连接退出释放执行锁。锁内再次核对到期、模式、结果和重试时间。
- 缺少行情进入 WAITING_DATA，供应商异常进入 SETTLEMENT_FAILED；指数退避加有界抖动（首次 45–60 秒，上限 6 小时）。只保存规范错误码，异常文本/密钥不入库。成功结果不受迟到失败覆盖。
- 生产读取接口目前明确返回 DATA_UNAVAILABLE；REPLAY 仅在 development/test 且显式指定逻辑时间时使用固定 fixture。此队列不将无数据推断成不可评分，不延长目标交易日，不覆盖已结算结果。修订仍通过独立领域调用，自动修订扫描尚未实现。
- 新增 2 个单元测试、3 个 PostgreSQL 用例：到期约束、退避、持久化恢复、错误脱敏、并发锁、手动结算竞态、连接退出恢复和模式隔离。Python 共 37 项通过；Ruff、契约与文档检查通过。
- 另在独立测试用户和 `_test` 数据库运行真实 CLI：第一次单批 SETTLED，第二次空批次。没有启动持续后台任务，也未更改用户已有预测。
- 真实供应商的超时/熔断、进程托管、任务监控、生产压力/故障演练及不可评分/基准修订仍未验收；TASK-063 保持 IN_PROGRESS。任务开始/结束时间当前记录该轮逻辑时钟，不是独立的性能耗时测量。

## 后续增量：证据支持的不可评分结果

- 新增 `005_unscorable_evidence.sql` 并在本机两个数据库迁移；保存不可变证据及 outcome 的外键关联。Worker attempt 支持 UNSCORABLE 终态，无后续自动重试。
- `resolution.UnscorableEvidence` 仅允许目标日停牌无收盘、退市无有效价格、无法解释公司行动三种原因；供应商延迟/错误不能被当作不可评分。校验确切目标交易日、证券、数据模式、来源文档哈希及发布/可用/采集时间；结论必须在目标收盘后形成。
- `record_unscorable` 在 Alpha 事务中锁定预测、追加 evidence/outcome/event/audit，同证据并发调用只生成一个结果。评分字段为 null。改判已有结果必须引用最新 settlement_version；后续有效行情追加新版本，原预测、证据、结果和报告不覆盖。
- 可信 Provider 接口可返回证据对象，Worker 直接进入 UNSCORABLE。该接口没有浏览器或模型输入路由；schema 校验不等于来源资格核验，真实来源的采集和可信性仍须验收。
- 预测详情展示原因、证据文字、来源、可用时间、原文和证据哈希；历史列表增加不可评分/等待行情/失败筛选。不可评分记录不渲染虚构的收益、误差或方向。
- 新增 1 个单元用例、3 个真实 PostgreSQL 用例和 1 个浏览器用例。当前 Python 41 项、TypeScript 10 项、浏览器 4 项全部通过；typecheck/build、Ruff、契约漂移和文档检查通过。浏览器新增用例通过内部领域调用写入独立测试用户的真实 PostgreSQL 记录，再经 Web/BFF/Alpha 读取，没有 Mock 详情响应。截图 `test-results/unscorable-desktop.png` 已查看。
- 导出 `packages/contracts/unscorable-evidence.schema.json`，与 OpenAPI 一起纳入生成漂移检查。真实公告原文存储/采集、基准价格修订、人工审核工作流、生产演练仍未完成，TASK-063 保持 IN_PROGRESS。

## 后续增量：基准价格修订

- 新增 `006_outcome_base_revisions.sql`，两个本机数据库已迁移；outcome 与不可变基准修订快照通过外键关联，原预测不更新。
- Alpha `settle(..., base_revision=...)` 和 `ResearchStore.settle_prediction` 接受可信内部传入的修订快照。要求原供应商、相同证券/数据模式、精确基准交易日、原始价格口径、新版本标识及研究截点后的可获知时间；拒绝未来信息。对已有结算追加基准修订时须引用最新 supersedes_version，拒绝时间倒退。
- 修订结算使用 `settlement-price-v2`，保留 frozen_base_close、实际采用的 base_close、Decimal 价差、来源版本/截点/快照与拆股因子。未修订的 v1 输入幂等标识保持兼容；已保存的旧 payload 不改写。
- 新基准快照纳入结算输入哈希，同证据并发重试仅生成一版。使用过修订基准后，新的目标结果必须显式携带基准证据，即使中间经过不可评分版本也不静默退回冻结原价。
- 预测详情按结算版本显示冻结原价、修订价、价差、来源和快照。合成样例 100→101 的基准修订使目标价 102 的收益从 0.02 变为 0.009900990099；原预测 0.005、原结算及旧报告仍保持不变。
- 新增 7 个领域用例、2 个 PostgreSQL 用例、1 个真实浏览器用例。当前 Python 50 项、TypeScript 10 项、浏览器 5 项通过；typecheck/build、Ruff、生成契约和文档检查通过。截图 `test-results/base-revision-desktop.png` 已查看。新增 `settled-outcome.schema.json` 进入漂移检查。
- 尚无真实供应商修订采集/自动修订扫描、管理员审核入口和生产验收；公开浏览器不能提交或覆盖基准价，TASK-063 保持 IN_PROGRESS。

## 后续增量：本地备份与空库恢复验证

- 新增 `scripts/backup_local.py`：仅连接本机 54329 的隔离 Compose 数据库，支持 yta/yta_test。pg_dump 和表清单共享导出的 REPEATABLE READ 快照；生成私有 custom archive 与 SHA-256/表记录数/表内容哈希清单。
- 恢复先校验 archive 哈希，只创建新的 `yta_restore_<随机ID>_test` 空库，不覆盖任何已有数据库。单事务 pg_restore 后核对全部表、快照/模型/证据/报告哈希，重算冻结预测和各版本结算，验证身份序列、runtime SELECT/INSERT 权限及每张事实表的 mutation 拒绝。保留恢复库供检查。
- 实际演练：24 张表一致、22 个不可变触发器、4 个身份序列；131 条冻结预测和 79 个结算结果重现成功。第二次恢复数据库为 `yta_restore_bb51dbc08b8840a681b8b39882e6dcb6_test`，验证耗时约 0.98 秒（仅此小型本地 fixture，不代表生产 RTO）。备份清单 `.local/backups/yta_test-20261027T034019Z-99146d9b.json`，原库未改动。
- 新增 3 个边界测试：拒绝非白名单数据库、文件权限/拒绝覆盖、损坏备份在连接数据库之前失败。Python 共 53 项通过；Ruff、契约和文档检查通过。Web/BFF 未变，沿用上一轮前端验证证据。
- GitHub Actions 新增真实容器备份/恢复步骤，远端未运行。每日 03:00 配置示例在 `infra/cron/yta-local-backup.example`，未安装系统定时任务。备份不会上传为公开 artifact。
- 此工具不提供生产凭证、远程恢复、角色密码备份、加密异地副本、保留/清理策略、调度告警或正式 RPO/RTO；代码和依赖仍需从可信 checkout/锁文件恢复。TASK-021 保持 IN_PROGRESS，生产灾备不能以本机测试替代。

## 后续增量：依赖就绪检查

- Alpha 保留 `/health` 存活端点，新增需要服务 token 的 `/internal/v1/ready`；检查关键事实表、SELECT/INSERT 与禁止 UPDATE/DELETE/TRUNCATE 权限、不可变触发器及数据模式。缺库、缺表、权限过宽、触发器缺失返回分类状态，响应不回显连接串/诊断异常。
- BFF 新增公开只读 `/api/yta/v1/ready`，检查身份/研究表与权限，调用 Alpha 私有 readiness，核对服务和数据模式；依赖未就绪返回 503。数据库连接/SQL 设置超时，BFF 探针连接在 3 秒执行期限后销毁，Alpha 请求设置 3 秒取消信号。存活端点保持独立。
- 当前真实 Provider/Hermes 尚未通过接入门槛，LIVE_RESEARCH readiness 明确不通过，不以 REPLAY 就绪冒充生产就绪。这不是完整的业务、性能或部署 Gate。
- 新增 `scripts/check_health.py`，默认探测 Web 转发的 readiness，模式须匹配；非就绪/不可达退出码为 1。响应限制 8KB、不跟随重定向、不打印原始响应或异常。没有通过邮件/消息发送告警，也未部署定时监测。
- PostgreSQL 集成覆盖受限账号成功、owner 账号权限拒绝、真实数据未验收；BFF 测试覆盖 Alpha 503、数据库不可达、存活与就绪分离及敏感信息不泄漏。监测脚本通过本地 HTTP 服务验证异常响应、模式错配与重定向拒绝。
- 当前 Python 62 项、TypeScript 11 项、浏览器 5 项通过；typecheck/build、Ruff、契约/文档检查通过。Playwright 启动门槛改为 BFF readiness，最后的用户表权限补充另跑 BFF 类型/构建/集成测试通过。本机服务恢复后，REPLAY 探测成功，期望 LIVE_RESEARCH 时按设计返回非零。
- 实际告警通道、日志轮转、队列/Worker 健康、服务器重启和网络故障演练、生产限流及监控部署仍待完成，TASK-020 保持 IN_PROGRESS。

## 后续增量：评测报告鉴权 API

- Alpha 新增内部报告生成和按 ID 读取端点，均需服务 token 及 BFF 提供的 owner。生成校验配置模式，读取同时校验所有者与环境模式；异主/异模式返回 404。
- BFF 新增 `POST /api/yta/v1/evaluation-reports` 和 `GET /api/yta/v1/evaluation-reports/:id`。生成要求登录、同源 Origin 与会话 CSRF；请求严格只接受基准日区间及带时区 cutoff，拒绝客户端 owner、模式和预测 ID 子集。模式由 BFF 配置注入，财务计算仍由 Alpha 完成。
- 复用现有全 cohort、Decimal、不可变报告、内容哈希去重和审计事务；相同事实/参数重试返回原报告。未新增迁移或修改既存报告。
- 新增真实 PostgreSQL HTTP 用例覆盖身份、模式、所有权、重复生成及未结算/null 指标；BFF 使用真实身份数据库与受控 Alpha 协议替身验证 CSRF、参数拒绝及 owner/mode 转发。Python 63 项、TypeScript 12 项通过，typecheck/build、Ruff、生成契约与文档检查通过。共享 contracts 需先 build 再执行依赖它的类型检查。本轮未修改 Web，未重跑浏览器测试。
- 报告列表/页面、预测更正数量、真实前瞻数据与完整发布验收仍未完成；TASK-064 保持 IN_PROGRESS。

## 后续增量：评测报告列表与分页

- Alpha/BFF 新增报告列表 GET 端点，所有者取自登录会话、模式取自服务配置；默认 25、最多 100 条。通过 `(created_at,id)` 倒序游标分页，游标先按 owner/mode 查找，跨用户、跨模式或不存在的游标返回 404。
- 列表只返回保存时间、哈希和评测摘要；完整 manifest 及基准日分组仍在详情响应中，避免列表重复搬运整份证据。未改变报告内容、金融计算或已有迁移。
- 真实 PostgreSQL 新增测试覆盖相同创建时间、多页遍历、翻页期间插入新报告、空列表、所有者/模式隔离、分页参数与内部服务认证。BFF 权限测试追加列表认证、游标转发、非法页大小及客户端模式/owner 拒绝。
- Python 64 项、TypeScript 12 项通过；typecheck/build、Ruff、生成契约和 120 项任务文档检查通过。本轮没有 UI 改动，未重跑浏览器测试。
- 分页不是跨请求的数据库快照；新增最新报告需刷新首页查看。报告页面、预测更正数量及真实集成验收仍待实现，TASK-064 继续 IN_PROGRESS。

## 后续增量：研究会话历史分页

- 替换会话列表固定 100 条截断，新增默认 25、最大 100 的游标分页；按当前 owner/data_mode 过滤，游标同域校验，返回 next_cursor。时间与 UUID 的排序比较在 PostgreSQL 内完成，保留微秒精度。
- Web 研究侧栏支持“加载更早会话”、加载中禁用、失败后同游标重试及合并去重；新建会话仍插入顶部，异步首页响应不会覆盖已新建的会话。移除会随分页变化的倒序序号。
- 真实 PostgreSQL 集成覆盖相同时间、仅微秒差异、分页期间新插入、跨所有者/模式游标及非法参数。TypeScript 13 项通过，typecheck/build 通过。
- Playwright 6 项全部通过。新增用例通过真实 BFF 创建 28 个会话，验证首屏 25 条、网络失败重试、追加无重复、末页按钮消失及最早会话历史可读。截图 `test-results/session-pagination-desktop.png` 已检查；既有移动端及预测闭环回归通过。
- 生成契约和文档检查通过；本轮未修改 Python 或迁移，沿用上一轮 Python 64 项证据。真实 Hermes 历史恢复、完整部署与 Review 尚未验收，TASK-033 保持 IN_PROGRESS。

## 后续增量：Alpha 响应流量与超时边界

- 修复 BFF AlphaClient 在 `response.text()` 完成后才检查长度的问题。现在逐块计数解压后的字节，最多 4,000,000 bytes；超限取消读取，返回 UPSTREAM_INVALID/502。增量 UTF-8 解码保留跨块字符，不依赖 Content-Length。
- 将响应体读取纳入统一异常处理；读取中断和默认 15 秒截止时间返回 ALPHA_UNAVAILABLE/503，调用方取消继续传播原取消原因。既有禁止重定向与不自动重试行为保留；未知写入结果必须用原请求键核对。
- 新增真实本地 HTTP 协议测试 7 项：精确上限/跨块中文、未结束超大 chunked 响应、gzip 解压膨胀、响应头后取消、真实 15 秒响应体超时、截断响应、非法 JSON/重定向。验证失败不会触发第二次调用。
- 全量 TypeScript 在新增默认超时用例前 19 项通过，追加后单独重跑 transport 7 项通过（当前合计 20 项）；typecheck/build、契约及文档检查通过。无 Python、数据库或 UI 修改，本轮未重跑 Python/浏览器测试。
- 这是 Alpha 传输边界修复，不是 Hermes 锁版本接入验证或生产容量验收。真实 Provider/Hermes、全局限流和部署 Gate 仍未完成。

## 后续增量：预测历史分页与全历史状态筛选

- 新增 Alpha prediction_page，公开列表替换固定 100 条截断：默认 25、上限 100，按 created_at/id 游标分页，所有者/模式隔离及游标校验。每页使用 REPEATABLE READ READ ONLY，将筛选与详情读取固定在同一快照；保留旧内部 list_predictions 供现有调用使用。
- 状态筛选移至 Alpha，根据最新追加事件查询全部匹配历史；BFF 严格校验 cursor/limit/status，拒绝客户端模式参数。没有修改财务计算、冻结事实或数据库迁移。
- Web 支持继续加载、失败后原游标重试、合并去重与已加载数量；切换筛选重新读首页，并用请求代次防止旧分页响应覆盖新筛选。没有匹配记录时显示对应空态。
- 真实 PostgreSQL 覆盖相同冻结时间、跨页新增、末页结算后的全历史筛选、归属/模式隔离及参数权限。新增真实浏览器用例生成 28 条合成冻结预测，验证分页故障重试、唯一详情链接、不可评分筛选/详情与手机宽度。截图 `test-results/prediction-pagination-mobile.png` 已查看。
- Python 65 项、TypeScript 21 项、Playwright 7 项通过；typecheck/build、Ruff、生成契约和文档检查通过。跨请求不是全历史快照，翻页期间状态变化需刷新查看；真实集成、Review 和发布验收未完成，TASK-081 保持 IN_PROGRESS。

## 后续增量：研究会话直接访问与恢复队列的模式隔离

- 修复研究仓库只校验 owner、未在按 ID 访问中校验 data_mode 的缺口。ResearchRepository 绑定 BFF 配置模式；消息、Run 详情、事件及游标、取消通过统一查询隔离，跨模式返回 404。
- 提交消息在查找幂等旧结果前验证会话归属/模式；精确旧请求不能绕过当前环境限制。创建和列表参数需匹配仓库模式。
- 恢复队列在 SQL 层按模式过滤后再取上限，避免另一模式任务占用待处理扫描。Runner 拒绝直接启动异模式 Run，追加事件同时检查运行模式与数据库归属，避免伪造内存模式绕过。
- 新增真实 PostgreSQL 用例：同一用户的 LIVE_RESEARCH 会话/Run 在 REPLAY 环境不可读、不可订阅、不可取消、不可精确重试；验证无取消写入/无事件追加，队列隔离及同模式重试有效。测试 LIVE 记录是隔离 `_test` 库的合成协议数据，不是真实行情或 Hermes 验证。
- TypeScript 22 项、typecheck/build、生成契约和文档检查通过；本轮无 Python、迁移或 UI 改动，未重跑 Python/浏览器。TASK-110 按现有本机证据改为 IN_PROGRESS，真实安全集成、CI/staging 与发布 Gate 仍待验收。

## 后续增量：补齐收益、成交量变化与历史波动率

- 对照任务书 8.2 补齐 RETURN_1D、VOLUME_CHANGE_1D、HIST_VOL20，指标响应升级为 indicators-v2 并附定义。拆股可比简单收益、成交股数反向调整、20 收益样本标准差乘以 √252 年化均使用 Decimal；前置样本和零成交量分母按明确规则返回 null。
- 不改变冻结预测、模型版本、baseline 输入或事实事务。真实行情缺失继续拒绝，不引入 fixture fallback；当前 Web 未增加这三个指标的图层。
- 新增 3 个测试：固定随机序列与 Python statistics 独立比较新指标；拆股前后价格/成交量变化中性、短窗口、缺交易日、未来拆股证据拒绝；原有 12 个指标序列与 pandas rolling/ewm 独立实现对比，涵盖所有 warm-up null，绝对误差阈值 1e-10（新指标 1e-12）。生产实现未引入浮点计算。
- Python 全量 68 项通过（含真实 PostgreSQL）；最后成交量改为直接乘拆股因子后，指标 6 项专项复验通过。Ruff 和生成契约检查通过。Web/BFF 未修改，沿用既有类型/构建/浏览器证据。
- 真实供应商数据、生产验收、Review 仍未完成；TASK-053/054 保持 IN_PROGRESS。年化 252 是固定计算约定，不能解释为实际交易收益或未来波动保证。

## 后续增量：图表价格与指标统一拆股基准

- 修复 Web 用原始 OHLC/成交量叠加拆股调整指标的问题。Alpha indicators-v3 在同一快照响应中附带拆股可比 bars、每根 split_factor、share_basis_session 和原始 data_snapshot_hash；价格除以累计因子、成交股数乘以因子，均使用 Decimal，原始快照与冻结记录不改写。
- Web 只读取一个图表/指标响应，避免两个请求跨行情版本；共享 chartDataSchema 校验口径、日期对应、长度及必需指标，失败显示错误/重试。数据来源文字明确标注拆股可比价格与成交量。
- 图表普通十进制字符串序列化修复零值 `0E-12` 不符合共享 decimalSchema 的问题；新增由 Python 生成的 chart fixture 纳入 export_contracts 漂移检查。
- Python 验证正向拆股的连续性及反向拆股 OHLC/小数成交股数/原始快照不变；共享契约验证原始口径、日期错位、缺失/截断序列拒绝。新增浏览器用例模拟日期错位并验证恢复，确认图表不再请求原始 bars。
- Python 69 项通过，最后十进制格式调整后指标 7 项专项复验通过；TypeScript 24 项、Playwright 8 项、typecheck/build、Ruff、契约/文档检查通过。真实供应商拆股验收及完整 Review/发布仍待完成，TASK-075/076 保持 IN_PROGRESS。


## 后续增量：独立 NVDA 概览页

- 首页 `/` 从跳转工作台改为受登录保护的 NVDA 概览；主导航增加概览入口，复用既有同快照行情图、范围/指标开关与来源时间/拆股口径。
- 概览说明固定 NVDA、日线、5 个交易日、20 日动量基线与零收益参照，并提供工作台/预测记录入口；REPLAY 明确标注合成演练且不计入正式前瞻成绩，无多证券/Profile 未实现入口。
- 复用 CSS tokens 和卡片样式，桌面图表/说明两列、手机纵向；未改变 Alpha/BFF、冻结确认或登录后工作台跳转。
- typecheck/build 通过；Playwright 全部 9 项通过。新增概览用例覆盖匿名跳转、来源与口径、图表加载、503 时无虚构价格/图表、重试恢复、375px 无横向溢出和工作台导航。桌面/手机截图 `test-results/overview-desktop.png`、`overview-mobile.png` 已查看。
- 本轮仅 Web 与浏览器测试变化，沿用上一轮 Python 69 项、TypeScript 24 项证据。真实供应商新鲜度、证券注册表和完整部署验收未完成，TASK-071 保持 IN_PROGRESS。

## 后续增量：不可变预测更正与数量披露

- 新增 007_prediction_corrections.sql，在本机 yta/yta_test 应用。预测行增加可空 supersedes_prediction_id，复合外键约束原预测同 owner/data_mode，禁止自引用，沿用不可变触发器和 runtime SELECT/INSERT 权限。
- Alpha freeze 增加可信更正引用，验证原预测所有者、相同证券/模式/跨度/基准日/目标日/协议、原窗口、信息与创建时间不倒退。新记录追加 FROZEN/审计引用；原记录不覆盖。普通冻结既有幂等哈希保持兼容，更正哈希纳入原预测 ID；同草稿异引用返回冲突。
- Alpha/BFF 新增独立 corrections POST 端点，BFF 从会话取身份并校验同源、CSRF、明确确认和幂等键；普通 predictions 请求仍只接受草稿/确认。未添加 Agent 自动更正权限。
- 新评测 price-evaluation-v2 将原预测与所有更正全部计入，新增 correction_count、manifest 引用并保留基准日分组。已有报告不改写。
- Python 72 项通过，扩展他人原预测/跨模式拒绝后更正专项 3 项复验通过；TypeScript 25 项、typecheck/build、Ruff、契约/文档检查通过。本轮无 Web 改动，未重跑浏览器。
- 实际备份/空库恢复通过：24 表、22 不可变触发器、4 序列一致，476 条预测和 229 个结算重现。清单 `.local/backups/yta_test-20261027T050405Z-412eb1d0.json`，恢复库 `yta_restore_e9dfbfe6ee1c42238b84974631e22c86_test`；这是本地合成数据演练。
- 更正选择/确认界面、实际供应商修订流程和完整集成/发布验收仍待实现，TASK-062/064 继续 IN_PROGRESS。

## 后续增量：用户确认预测更正界面

- 原预测详情新增“研究并更正此预测”入口；工作台以 URL 中的原预测引用加载并展示原判断，在新草稿生成后提供更正确认。未确认不发送更正请求，原记录读取失败/窗口模式不匹配时禁用提交，不回退成普通冻结。
- 确认框显示原预测区间/收益与新草稿事实，明确新增记录、原预测继续参与统计。更正请求使用独立 API，幂等键绑定草稿和原预测；网络响应丢失时在当前页面用相同键核对。
- 新记录详情显示原预测链接，刷新后仍保留。真实模式过期记录不展示更正入口，最终窗口约束仍由 Alpha 校验；REPLAY 使用既定隔离逻辑时间。
- typecheck/build、契约/文档检查通过；Playwright 11 项全部通过。新增更正闭环验证返回检查不提交、响应丢失后同键重试、原记录含历史 outcome 完全不变、刷新后引用可追溯；另验证不存在的原预测不能触发普通冻结。确认截图 `test-results/correction-confirm-desktop.png` 已查看。
- 浏览器测试改为在内存复用仍有效的登录 Cookie、撤销后重新登录，避免测试数量增长触发原有每分钟登录限流；没有放宽产品限流或保存测试会话文件。本轮无后端/迁移修改，沿用 Python 72 项、TypeScript 25 项证据。
- 真实供应商更正数据和生产验收未完成；TASK-062/080/081 保持 IN_PROGRESS。

## 后续增量：基线评测浏览器页面

- 预测记录新增基线评测入口，`/evaluations` 提供日期区间和 UTC 事实截止时间表单、保存报告及游标分页；详情展示全部/已评分/更正数量、未评分各状态、方向命中率、MAE 与零收益对照。
- 展示固定样本清单、结算版本、更正引用、基准日分组数量及报告哈希/模型/协议/代码版本。明确预测详情可能已有新结算版本；无评分样本显示“—”，旧报告缺少更正数时显示未披露，不推断为零。
- 沿用 BFF 登录/CSRF 和 Alpha 事实计算；本轮未修改后端、金融计算、数据库迁移或权限。页面仅支持现有基线统计，不代表 Beta 训练模型比较或完整评测模块完成。
- typecheck/build、生成契约及文档检查通过；Playwright 13 项全部通过。新增用例经真实 Web/BFF/Alpha/PostgreSQL 验证报告保存响应丢失后的同参数重试去重、刷新读取、更正及不可评分/等待行情披露、空样本 null、分页失败后恢复与不重复；修复手机状态标签溢出，375px 页面无横向溢出。
- 桌面及手机截图 `test-results/evaluation-desktop.png`、`evaluation-mobile.png` 已查看。Python 72 项和 TypeScript 25 项沿用此前证据，本轮未重跑对应套件；测试数据库验证由上述浏览器闭环实际执行。
- README 已补充浏览器入口并删除过时的“尚无报告页面/更正数量”说明。TASK-064 继续 IN_PROGRESS，真实数据、集成 Review 及发布验收仍待完成。

## 后续增量：评测响应契约与异常恢复

- 共享契约新增 evaluationReportSchema、evaluationSummarySchema、evaluationPageSchema，支持已保存 v1/v2 报告。校验版本、时间/哈希格式、统计数量及空值规则、分页唯一性/游标、清单唯一性、日期范围、状态与 outcome ID/版本是否匹配、基准日分组、更正数量及引用。v2 必须披露更正数，v1 缺失时保持未披露。
- Decimal 统计保留原字符串，兼容旧报告中的 `0E-12`；拒绝数值类型、负误差、非有限显示值及越界命中率。前端仅做结构/计数/展示范围验证，不重算金融指标，不验证报告哈希内容，不取代 Alpha 的事实审计。
- Web 在首次列表、分页、详情及生成响应各入口解析未知响应，并要求当前登录环境的数据模式。异常结果不渲染、不跳转；分页异常保留已验证记录，表单保留参数，读取异常可重新加载。异步读取被取消后不再写入页面状态。
- TypeScript 全量 30 项通过（含实际 PostgreSQL BFF 集成），新增 5 项契约测试覆盖历史兼容、异常统计/清单/分页及 null 规则。Playwright 全量 15 项通过，新增异常列表、跨模式详情和异常生成响应的拒绝及重试用例；既有真实 Alpha 报告闭环继续通过。首次新增测试因 Next 路由播报器也带 alert role 导致定位歧义，已将断言限定在评测页面后复验通过。
- typecheck/build、生成契约漂移和文档检查通过。本轮无 Python/数据库变更，未重跑 Python 套件，沿用此前 72 项证据。TASK-025/064 仍为 IN_PROGRESS；其他 API 响应契约、真实集成与发布验收尚未完成。

## 后续增量：结算 Worker 进度探针与优雅停止

- Worker 增加可选 `--heartbeat-file`；同目录原子替换、0600 权限、每进程 run_id，内容不含账户、预测或数据库凭证。仅完成一批扫描后更新 ready，使用真实 UTC 墙上时钟；启动/停止/失败可区分，REPLAY 逻辑时间不影响进度新鲜度。
- 新增 `yta_alpha.worker_health` CLI，检查模式、新鲜度、运行状态及供应商实现是否就绪；有界读取、脱敏输出、非就绪退出 1。真实供应商仍为 UnavailableProvider，持续轮询也不能报告就绪。BFF readiness 未冒充 Worker 健康状态。
- SIGTERM/SIGINT 以 Event 结束轮询等待，当前批次完成后退出，保留原有 Alpha 事务/租约/重试规则；数据库批次异常以脱敏 WORKER_FAILED 和退出码 1 结束。CLI 提前验证批次数量及回放时区。未改迁移或金融计算。
- Python 全量 78 项通过，含实际 PostgreSQL。新增子进程测试在受限应用账号、隔离测试用户下执行真实空队列扫描，验证 SIGTERM 在 600 秒等待中及时退出、重启 run_id 变化、SIGKILL 后记录过期、连接失败脱敏及 failed 状态。随后增加探针 CLI 退出码用例，worker health 专项共 5 项复验通过。
- Ruff、生成契约及文档检查通过。Web/BFF 未修改，沿用此前 TypeScript 30 项和浏览器 15 项证据。README 补充命令、每实例独立文件、阈值及信号处理约束。
- 该探针尚未部署到监控/告警系统；真实供应商超时、进程托管、积压指标和 staging 故障演练仍待完成。TASK-020/063 保持 IN_PROGRESS，不据此宣布生产就绪。

## 后续增量：结算队列积压指标

- SettlementWorker 新增只读 queue_status，以单个 PostgreSQL 查询快照按当前 data_mode/可选 owner 聚合：到期无 outcome 总数、重试时间已到/退避中、最新 attempt 的等待/失败/尚未尝试数量、最早目标收盘时刻及逾期整秒数。无积压时日期/时长为 null；已评分及不可评分均不进入待处理队列。
- 每批日志增加 queue 汇总，独立于本批 limit；进度 ready 在批次和队列查询均成功后刷新。增加 `--queue-only` 可信运维命令，禁止同时 watch/写 heartbeat；无供应商调用、结算写入或 owner/预测 ID 输出。模式、范围及队列时间明确返回，回放时间与进度墙上时钟继续分离。
- 新增真实 PostgreSQL 用例覆盖各状态、退避到期、最新 attempt 替代旧状态、终态排除、空值、owner/模式隔离、无时区拒绝和读取前后事实不变；子进程验证 queue-only 不结算可处理的回放预测。既有 Worker 进程健康/退出测试继续通过。
- Python 全量 81 项、Ruff、生成契约漂移及文档检查通过。没有新增迁移或改变金融计算；Web/BFF 未改，沿用 TypeScript 30 项及浏览器 15 项证据。
- README 记录各指标定义、交叉分类不可重复求和、eligible 不保证获得租约、当前队列并非历史评测快照等约束。采集部署、告警阈值、规模压测和真实 Provider 仍待验收；TASK-020/063 保持 IN_PROGRESS。

## 后续增量：应用容器打包与本地边界验证

- 新增 Node/Alpha 多阶段 Dockerfile，基础镜像固定 digest，Node 构建 pnpm 9.15.4 冻结锁文件，Alpha 使用 uv 0.11.16 锁文件和非 editable 安装。运行层不复制构建阶段的 pnpm/uv 和完整开发工作区；BFF/共享契约仅打包 dist，Web 按显式 YTA_STANDALONE=1 构建独立产物。本地进程构建/启动保留原方式。
- BFF 增加受枚举约束的 YTA_BFF_HOST：默认 127.0.0.1，容器显式绑定 0.0.0.0。新增安全配置测试；未变更公开权限或金融协议。
- 新增 compose.app-local.yaml，复用本地隔离 yta_test 和受限账号；仅 Web 发布 127.0.0.1:3300，Web 与数据库网络分离，BFF/Alpha 无宿主端口。运行非 root、只读根、tmpfs、no-new-privileges、cap_drop ALL 和日志轮转。jobs 为手动启用 profile，默认不持续处理测试记录。
- 三个 arm64 镜像实际构建成功，Compose health 全部通过。check_container_stack.py 实测 Web→BFF→Alpha→PG readiness、匿名 401、登录页与静态资源、Web 无服务凭证且无法解析后端服务名、非 root/只读/端口边界及 jobs 只读查询。首次 edge internal 配置导致宿主 Web 端口无法访问，改为普通 edge 网络后完整复验通过；后端仍不发布端口，尚无生产出口策略。
- Docker Hub Python 元数据请求连接超时，验证 Docker 官方 ECR Public 发布源后，改用固定 digest 的 Python 镜像并完成构建；没有换用未经核验的第三方应用镜像。
- 当前镜像 ID：Web `sha256:8000b15913e8ac5684736507f04e439303dfaf94d6e7728d7d1d84936fb326ed`，BFF `sha256:255499d25f478463fd7655f0da4ae7e6577cd6b44e89015f91a4dbe3faf98210`，Alpha `sha256:0e17a2ed5c9e14a7a7f6bbb35b1f8f9f6a4e4b9e790679fcc155eeb4cc9c40a6`。均为本机验证镜像，未发布 registry。
- typecheck/build、TypeScript 31 项（含真实 PG）、Playwright 15 项、Ruff/格式、契约及文档检查通过。Python 业务未修改，沿用前轮 81 项证据；容器另实测 Python API 与 Worker 入口。新增独立 container-smoke CI job，但尚无远端/amd64 执行证据。
- 本地测试栈不是 staging/production 部署；HTTPS、真实 Hermes/Provider、专用迁移镜像/流程、CI Review、发布回滚和生产网络策略仍待验收，相关任务保持未完成状态。

## 后续增量：独立迁移镜像及迁移输入约束

- Alpha Dockerfile 增加独立 migrator target，只在该产物内复制版本化 SQL；默认最后阶段仍为 API runtime，不包含 SQL、不自动迁移。owner 连接只在执行迁移时单独注入，运行镜像无 owner 凭证。新增本地容器迁移验证脚本和 CI 步骤。
- migrate 在连接前拒绝缺失/空目录和空脚本，避免错误镜像路径被误判为成功；在原事务/advisory lock 内完整核验历史哈希并拒绝遗漏已应用迁移的产物。保持单事务 DDL 与记录写入，连接超时 5 秒；CLI 批次失败输出脱敏 MIGRATION_FAILED、退出码 1。未修改任何已应用 SQL。
- Python 全量 83 项通过（含真实 PostgreSQL）：新增无效目录不连接、历史遗漏拒绝、失败 DDL 和迁移记录同时回滚、并发重复执行幂等。原校验和测试继续通过。Ruff/格式、契约和文档检查通过。
- 实际构建并运行 migrator：随机新建的 `_test` 空库中，缺少凭证/应用账号无法迁移，owner 应用全部 7 个版本，重复运行无新增，应用账号仍拒绝；验证后仅删除本次创建的临时库。镜像 `sha256:4a309ac079f218566418454e1f5a6b998021f310290b24594e70905f35243f87`。
- API 默认 runtime 重新构建并实测可导入 Alpha、不包含 `/app/infra/migrations`；当前镜像 `sha256:c694b9fc17cd835ec2af29b34fd9a123eee45194444e12603f825437095d41ee`。Web/BFF 无改动，沿用此前 TypeScript 31 项、Playwright 15 项及容器全链路证据。
- README 说明运行账号授权初始化与迁移成功是不同步骤，旧迁移产物不能用于数据库降级。生产凭证分发、迁移发布顺序、expand/contract 回滚、远端 CI/staging/Review 仍未完成，不能视为发布 Gate 已通过。

## 后续增量：BFF 请求与上游关联日志

- 新增 observability 模块，通过 AsyncLocalStorage 隔离并发请求上下文；服务端 request_id 同时用于响应头、错误正文和结构化请求日志。日志包含固定路由、方法、状态、耗时及完成标志；finish/close 只记录一次，SSE 中断保留 completed=false。
- AlphaClient 记录请求/响应读取及解析的整体耗时、成功/失败/取消和经格式验证的 Alpha request_id；不增加重试、不转发外来 trace ID、不改变金融或授权语义。缺失/异常上游 ID 为 null，无 HTTP 上下文的后台调用不伪造 BFF 请求关联。
- 新增 bff.research_admitted，将实际接收的 run_id 与 HTTP request_id 关联，供衔接持久化运行事件；不记录正文、凭证、用户身份或幂等键。未知路由统一 UNMATCHED，资源路径转为模板；日志 sink 抛错不会改变请求结果。
- TypeScript 全量 37 项通过（含实际 PostgreSQL）。新增实际 HTTP 用例验证 4 个并发请求不串号、上游 ID 关联、敏感输入不出现于日志、401/404、SSE 中断只记录一次、未知 ID/路径拒绝及日志 sink 故障隔离；PG 闭环另验证接收事件和持久化 run_id 对应，错误正文 ID 与响应头一致。
- typecheck/build、Playwright 15 项、契约与文档检查通过。本轮无 Python 或数据库修改，沿用 Python 83 项证据；未重建发布镜像，现有本地进程使用新构建，后续容器构建会纳入此模块。
- TASK-114 调整为 IN_PROGRESS，增加本机证据链接。OTel 跨服务 trace、工具跨度、集中采集、告警阈值与生产留存/Review 仍未完成；这些日志不是完整分布式追踪验收。

## 后续增量：跨进程登录限流

- 新增 008_login_attempts.sql，在本机 yta/yta_test 应用并授予应用账号 SELECT/INSERT 和序列 USAGE。记录对端哈希和数据库时间，带索引与不可变触发器；不写原始地址、邮箱、密码、Cookie。未更改任何旧迁移。
- Auth 移除进程内 Map，使用 PostgreSQL 对端事务锁和单次条件 INSERT，在密码校验前领取过去 60 秒最多 10 次的额度；成功/失败均计数，超额返回既有 429，拒绝请求不新增槽位。锁后读取数据库时钟，绑定语句/锁超时，数据库异常不放行。IPv4 mapped 地址统一，转发头仍不受信任。
- BFF readiness 增加新表权限、不可变触发器及序列 USAGE 检查；没有给运行角色增加 UPDATE/DELETE/TRUNCATE。新额度读取旧历史，无需修改/删除过期记录。
- TypeScript 全量 41 项通过，随后新增 HTTP 登录路径测试，限流专项 5 项复验通过；真实 PostgreSQL 覆盖 24 次并发跨连接池恰好放行 10 次、新实例仍受限、过期恢复/历史不变、权限拒绝、数据库失效拒绝、两个 Auth 实例的成功/失败计数及伪造转发头不能绕过。Python 全量 83 项、Playwright 15 项、typecheck/build、Ruff、契约/文档检查通过。
- BFF/迁移镜像重新构建；新库容器迁移验证全部 8 个版本、重复执行及 owner 隔离，临时库已清理；应用容器 smoke 验证新 readiness、端口/凭证边界和 Worker 查询通过，随后关闭测试栈。BFF 镜像 `sha256:6ce71e17832254d73da87eaecf6bd1226fac7ad75585c3f0dc609289fc1c5a9b`，migrator `sha256:6aa72ff70dc5e1efd6d1ccb5dc8aaa922fe53f9f3a38ca631b12fdcd3a8f7fa4`。
- 当前限制的是直连对端；代理后的用户共享额度，可信代理配置、账号维度/全站预算、归档策略与生产安全 Review 仍待完成。TASK-026/110 保持 IN_PROGRESS，不据此宣布安全验收完成。

## 后续增量：Hermes 候选源码锁与接入边界核验

- 官方仓库候选固定为 `085f562af53a6fedadac3681e2c498fc71316132`；`infra/hermes/source-lock.json` 保存 28 个相关文件的 SHA-256，镜像摘要为空，全部运行能力门槛保持 UNVERIFIED。详见 [源码核对记录](hermes-integration-research.md)。
- 新增只读 `scripts/check_hermes_source.py`，核对 HEAD、文件哈希、路径边界及干净工作区；禁用 Git fsmonitor。成功结果也不能提升为运行验收通过。未安装或执行上游源码、构建 Hermes 镜像或调用模型。
- 静态审查明确幂等存储可能回退内存、SSE 事件窗口不等同于跨重启回放、stop 请求成功不等于运行已停止。候选运行环境应与 Alpha 分离。上述结论用于制定真实验收用例，不替代真实集成证据。
- 本机候选 28 文件核验通过；专项 3 项测试覆盖匹配但不授予运行通过、修改/缺失/未跟踪文件、错误 commit、路径越界及符号链接拒绝。Ruff、120 任务依赖/文档检查与生成契约漂移检查通过。本轮未改业务服务或数据库，未重跑 TypeScript、全量 Python 和浏览器套件。
- TASK-007 更新为 IN_PROGRESS；镜像、启动、持久卷、回滚与 TASK-015 真实能力实测仍待完成，真实 Adapter 继续不可用。未据此宣布真实数据接通或发布完成。

## 后续增量：登录密码计算并发保护

- 将 scrypt 派生集中于 BFF `password.ts`，每进程最多提交 4 个计算，跨 Auth 实例共享容量，包含凭证生成、真实账户与不存在账户校验。超额不排队，返回可重试的 503/AUTH_BUSY；PostgreSQL 对端额度仍计数。完成、同步异常与异步回调失败均释放容量。
- 保留 scrypt 参数、随机盐和既有哈希格式；不存在账户使用固定的非账户 dummy 输入完成相同计算，不再由每个 Auth 实例启动额外派生。未知账户始终不能验证成功。未更改会话、所有者或冻结授权规则，无数据库迁移。
- TypeScript 全量 44 项通过（包含实际 PostgreSQL）；随后新增过载 HTTP 测试，BFF 集成专项 13 项通过，验证 503 含 request_id/retryable、不签发 Cookie、不新增会话。真实密码计算与后端故障专项 3 项通过，覆盖容量共享、拒绝等待、完成恢复及同步/回调失败恢复。typecheck/build 通过。
- 本轮未改 Python、前端或数据库，未重跑 Python/浏览器套件或重建容器镜像。此限制仅保护单进程计算资源；跨实例全站预算、账号限流、生产压测和安全 Review 仍待完成，TASK-026/110 保持 IN_PROGRESS。

## 后续增量：BFF 共享请求契约与生成检查

- 登录、分页、预测筛选、消息/事件游标、UUID、证券、幂等键及空请求体的校验收敛到共享 Zod 契约，BFF 直接使用。分页仍限 1–100，消息序号保留字符串精度，登录保留邮箱小写及原密码字节语义。
- 消息与 SSE 接口改为解析完整查询对象，不再忽略额外字段；拒绝伪造 owner/data_mode、重复查询参数数组及非法游标。SSE 在发送响应头前返回 422 JSON；合法 Last-Event-ID 继续优先于查询 cursor，两处输入均须合法。未更改归属、模式或金融事实规则。
- 新增请求 schema 导出命令与 `bff-requests.schemas.json`，包含 13 项输入定义；CI 增加漂移检查。生成内容不是完整 OpenAPI，不表达全部规范化/跨字段约束；这些仍以实际 Zod 为准，完整响应契约及 Swagger 继续待完成。
- TypeScript 全量 50 项通过（共享契约 13、BFF 37），含实际 PostgreSQL。新增边界用例覆盖分页字符串/数组/数值、bigint 精度、登录与幂等键限制，以及 HTTP 参数拒绝与 SSE 恢复头优先级；无 Alpha 调用副作用。typecheck/build、请求 schema 漂移、Python 契约漂移、Ruff 和文档检查通过。
- 本轮无 Python、数据库迁移或前端行为变更，未重跑 Python/浏览器套件、容器或远端 CI。TASK-023/025 保持 IN_PROGRESS，真实 Hermes/Provider 集成及完整发布验收仍待完成。

## 后续增量：请求体解析错误与大小边界

- 实际 HTTP 测试复现：超大请求、不支持的编码及损坏 gzip 原先落入 500，畸形 JSON 仅返回泛化 HTTP_400。新增解析器边界映射，将已知解析失败转为 400/MALFORMED_JSON 或 INVALID_BODY、413/REQUEST_TOO_LARGE、415/UNSUPPORTED_ENCODING 或 UNSUPPORTED_CHARSET，均不可盲目重试。
- 映射仅发生于 express.json 的可信回调，不扩展为信任任意业务/供应商异常的 status 字段。沿用统一错误响应和请求 ID，不输出底层诊断或原始请求体；未知内部异常仍按既有 500 处理。
- 保留 32 KiB 解压后字节上限。新增 6 项真实 HTTP 用例，验证畸形/原始值 JSON、恰好上限及多一字节、多字节字符、正常/超限/损坏 gzip、非法编码/字符集、无 Content-Length 分块超限及失败后服务仍可访问；解析失败均未调用数据库，也未签发 Cookie。
- TypeScript 全量 56 项通过（共享契约 13、BFF 43），包括既有实际 PostgreSQL 集成；typecheck/build、请求 schema 与 Alpha 契约漂移检查、Ruff 通过。README 补充错误码与大小边界。
- 本轮无 Python、数据库或前端变更，未重跑 Python/浏览器套件或容器部署。TASK-024/110 保持 IN_PROGRESS，完整协议、安全及生产验收仍待完成。

## 后续增量：BFF OpenAPI 定义与规范校验

- 新增 `packages/contracts/bff.openapi.json` 与生成器，覆盖当前控制器的 24 个路由操作，记录请求体、路径/查询/头参数、成功状态码、响应结构和统一错误模型。只包含公开操作可达的 schema，不导出内部路由或服务 Bearer 凭证。
- 显式描述开发/生产 Cookie 的二选一鉴权、认证写入所需 CSRF 与 Origin、冻结用户确认、消息幂等身份、取消与未知执行限制、REPLAY 结算边界；SSE 单列事件 envelope、恢复头和流关闭语义，readiness 503 单列实际探针响应。
- 行情引用 Alpha 的输出 schema；初次引用完整性测试发现输入/输出命名差异，修正为读取实际接口响应引用后通过。历史结算 result 与审计 payload 目前仍是注明版本语义的开放对象，未冒充所有历史变体的字段级验证；Zod 跨字段与规范化约束仍以运行时为准。
- 共享契约 16 项通过，新增 3 项核对全部控制器路由/成功状态、唯一 operation ID、本地引用完整性、Cookie/CSRF 与 SSE/readiness。OpenAPI 3.1 独立规范校验、生成漂移、typecheck、Ruff 和 Alpha 契约漂移检查通过。锁定开发依赖 openapi-spec-validator 0.7.2 及 uv.lock；CI 增加该验证与 BFF 文档漂移检查，远端未执行。
- 本轮未改业务运行代码或数据库，未重跑 BFF/Python/浏览器全量套件或应用构建，沿用此前对应证据；共享契约构建由生成检查执行。应用内 Swagger 页面、完整历史响应校验、真实集成与发布验收仍待完成，TASK-023/025 保持 IN_PROGRESS。

## 后续增量：应用内 Swagger 与部署资源验证

- 新增登录保护的 `/api/yta/v1/docs` 页面及四个固定资源路由；浏览器经现有 Web→BFF 转发访问。HTML、OpenAPI、初始化脚本和 Swagger UI 资源均校验当前会话，撤销后拒绝访问，统一 no-store；不公开资源目录。文档资源路由不计入既有 24 个业务/健康操作。
- 固定 swagger-ui-dist 5.34.0，脚本/样式随 BFF 本地打包，OpenAPI 文件纳入 contracts 的 package exports/files。页面只读，关闭 Try it out、URL 配置覆盖、外部 validator 和授权持久化；增加同源 CSP。未给浏览器增加 Alpha/Hermes/数据库访问或凭证。
- TypeScript 全量 60 项通过（共享契约 16、BFF 44），含实际 PostgreSQL：新增匿名/有效/撤销会话、所有资源、禁止目录访问与缓存策略验证。首次类型检查发现路由返回类型无法生成可移植声明，补显式类型后 typecheck/build 通过。
- Playwright 全量 16 项通过，新增 Swagger 渲染 24 操作、展开冻结定义、无外部请求/业务写入及无页面异常。截图 `test-results/swagger-desktop.png` 已查看。首次 E2E setup 暴露密码生成间接导入 ESM 契约的问题，测试及本地账户初始化改为直接导入密码模块，随后全量复验通过。
- 生产 BFF 镜像构建通过：`sha256:8003afbea047af0415d94cc6e5e4c358280adaca719d40733a630170c7fc684d`。只读、无网络容器实际加载打包资源并验证五个匿名路由均 401；应用 Compose smoke 增加相同文档路由校验并通过。请求/OpenAPI 与 Alpha 契约漂移、独立规范校验、Ruff 和文档检查通过。
- 无数据库迁移或金融计算修改，未重跑 Python 全量套件；测试栈验证后关闭，本地 Web/BFF/Alpha 已恢复且 REPLAY readiness 通过。历史响应全字段约束、真实接入、远端 CI/Review 及发布验收仍待完成，TASK-023/025 保持 IN_PROGRESS。

## 后续增量：Hermes 完整构建源与只读探测工具

- Git 按需下载完整源码时连接超时，改用同一固定 commit 的官方 codeload 归档。新增 `prepare_hermes_archive.py` 对照已取得的 Git tree，验证完整路径集合、Git blob 内容与可执行位后安全解压，不覆盖已有构建目录。拒绝路径越界、链接和内容增删改。
- 实际候选验证 21,418 个文件，tree `2a7a6b5d48185c667bd49f95202770aa60a974b2`，归档 SHA-256 `9479f45286c36ba8903541b4b80834af4e032df72cf7573ccb88dab0685d200f`。38 个 PowerShell 文件按该 tree 的 `.gitattributes` 采用 CRLF，规范化校验后内容一致；未修改归档或上游代码。锁文件补充完整归档证据。
- 使用官方 Dockerfile、默认非桌面配置和 revision 标签开始本机构建 `yta-hermes:085f562-candidate`。截至本次记录仍在下载固定 Debian 基础层，尚无完成镜像或运行证据；`image_digest` 保持 null，所有真实能力门槛保持 UNVERIFIED。
- 新增 `probe_hermes.py`：只允许 loopback origin，禁止代理/重定向，三个 GET 检查匿名/错误凭证拒绝及有效凭证 capabilities。记录能力声明、持久幂等声明、端点形状和鉴权状态；有响应大小/超时约束，0600 新建证据文件，不提交 Run 或模型请求。能力声明不能提升运行验收状态。
- 归档与探测专项 18 项测试通过：包含篡改/缺失/新增/可执行位/链接/路径逃逸拒绝，以及真实本地 HTTP fixture 的鉴权、重定向、超限、内存幂等与非 loopback 拒绝。这里的 HTTP fixture 不是 Hermes 集成通过证据。真实探测将在候选镜像构建、隔离启动后执行；供应商及完整矩阵仍待确认。
- Python 全量 104 项通过（含真实 PostgreSQL），随后补充慢速响应总时限用例，探测专项 11 项复验通过；要求未压缩响应并按原始网络分片限制总时长，避免固定块缓冲使慢速字节流绕过时限。Ruff、格式、文档及 Alpha 契约漂移检查通过。Ruff 显式排除 `.local`，避免将下载的上游源码当作 YTA 代码检查；未修改上游代码以迎合本项目 lint。
- 本轮未改 BFF/Web 业务或数据库迁移，未重跑 TypeScript/浏览器/应用容器套件。本地应用服务保持运行；Hermes 构建仍在独立 Docker 进程中，后续应继续观察现有构建，不因下载缓慢重复启动。

## 后续增量：Hermes 隔离启动配置与构建重试

- 首次官方 Dockerfile 构建已终止：固定 Debian 基础层下载完成，但 apt 获取 trixie-updates 索引时代理连接被重置，返回 HTTP 500；未产生完成镜像。确认终态后使用同一源码、Dockerfile、revision 标签重试，基础层命中缓存，之前失败的索引已成功下载，正在安装依赖。没有关闭软件包签名验证或修改上游源码。
- 新增 `infra/compose/compose.hermes-probe.yaml`，要求显式本地镜像 ID 与独立 API token，禁止拉取远程同名镜像；以 UID/GID 10000 前台运行 Gateway，使用只读根、独立持久卷、临时目录、资源与日志上限，移除 capabilities 并禁止提权。无外部网络、无公开端口、无现有应用卷或模型凭证。
- Compose 解析及解析后隔离属性核对通过；README 补充私有 env 文件、启停和容器内只读探测要求。此证据只验证配置，不证明镜像内 Gateway 能在这些限制下启动；真实启动、数据卷权限、capabilities、重启与回滚仍须实测。
- 文档依赖检查通过；本轮只改本地探测配置及说明，无业务或数据库行为变更，未重复全量业务测试。镜像摘要保持 null，能力门槛保持 UNVERIFIED，TASK-007/012/015 未提升为完成。

## 后续增量：Hermes 候选镜像与真实只读启动探测

- 同一官方 Dockerfile 重试构建成功，本机 arm64 镜像 ID/索引摘要 `sha256:67f82b05c5c083f2795cff9c361b4f5c464116fcc4301671709376e6f64e3226`，平台 manifest `sha256:d8724a5ee2bd26bea9cc57db9b561e8ddac798a83328db19f75ef5ff01f43e9e`。revision 标签与固定 SHA 一致；无网络、非 root 的独立 Python 读取验证 18 个镜像内关键源码文件哈希一致。锁文件补充镜像摘要，全部能力门槛仍为 UNVERIFIED。
- 实际运行 Python 3.14.9、SQLite 3.53.1；虽然 Dockerfile 单独编译 3.53.4，应用 Python 仍报告 3.53.1，不能用构建阶段输出代替运行依赖证据。上游本地构建的安装版本标记是 fallback，不能仅凭该标记认定 commit；此次使用构建源验证、OCI 标签和镜像内哈希共同记录候选身份。
- 隔离实例实际以 UID 10000、只读根、network none、无端口绑定启动，数据卷属主 10000，可创建 runs_idempotency.db、state.db、response_store.db 等状态文件。首次启动提示 PID 1 无 init，增加 Compose `init: true` 后重新创建并核对实际 HostConfig。
- 首次与空闲重启后分别执行三次真实 GET：匿名 401、错误凭证 401、有效凭证 200。capabilities 声明包含预期接口、session header、鉴权、持久幂等和工具/停止/未知执行能力。原始响应保存于本地 0600 文件，脱敏观察见 `infra/hermes/runtime-observation.json`。未提交任何 Run 或模型请求；声明和空闲重启不能证明运行中的幂等、SSE、停止或恢复。
- 只读根容器拒绝 `docker cp` 写入，改用 `docker exec -i` 将脚本传入 tmpfs；重启后重新传入，未放宽根文件系统权限。README 记录实际操作限制。实例使用独立随机 API token，未配置供应商凭证，仍与现有应用隔离。
- TASK-007 保持 IN_PROGRESS；真实 Provider、alpha-orchestrator 工具、完整协议矩阵、SQLite 兼容性、云端/amd64、回滚与发布审查仍未完成。未据此启用真实 Adapter 或提升 TASK-012/015 状态。
- 归档、源码锁和探测工具专项 22 项通过；28 文件 Git 源码核验、Compose 解析和 120 任务文档检查通过。本轮无金融/数据库/BFF/Web 业务变更，未重复其全量套件。探测完成后已关闭并移除探测容器，保留独立数据卷及私有证据文件。

## 后续增量：Hermes SQLite 运行版本差异诊断

- 最小容器复现确认应用 SQLite 为 3.53.1，要求等于构建共享库 3.53.4 的诊断断言失败。逐项检查确认 `_sqlite3` 的 module origin 为 built-in；通过 ctypes 单独加载镜像共享库返回 3.53.4，系统 `/usr/bin/python3` 也返回 3.53.4。原因是应用 Python 内建模块不使用该共享库，并非库未打入镜像。
- 应用 SQLite source ID 为 `2026-05-05 10:34:17 c88b22011a54b4f6fbd149e9f8e4de77658ce58143a1af0e3785e4e6475127e9`，与 [官方 3.53.1 发布记录](https://sqlite.org/releaselog/3_53_1.html)一致；该发布包含 WAL-reset 修复。原先“应用应等于共享库版本”的假设不成立，未为消除版本差异而修改上游源码、替换 Python 或设置 LD_PRELOAD。
- 新增 `scripts/check_hermes_sqlite.py`，在同一固定镜像的实际应用解释器内，以非 root、无网络、只读根和独立 tmpfs 执行。实测 WAL、读隔离、写锁 SQLITE_BUSY、回滚、checkpoint、重开、完整性、FTS5 trigram 共 8 项通过；所有测试数据库自动清理，没有操作 Hermes 持久卷或 YTA PostgreSQL。
- `runtime-observation.json` 补充原因、实际 source ID 和检查范围；README 提供可复现命令。该 smoke 不验证历史版本全部漏洞、断电恢复或 Hermes 任务库并发，因此整体能力状态仍为 UNVERIFIED，真实 Provider/工具及运行中恢复门槛不变。

## 后续增量：研究 Profile bootstrap 工具选择边界

- 在固定候选镜像内实际调用上游 `_get_platform_tools`，复现 API 空工具列表自动加入全局 MCP；改为 `no_mcp` 后能阻止该路径，但加入合成 xAI 凭证仍自动启用 `x_search`。仅有空列表不能作为研究工具白名单证据。
- 新增 `alpha-orchestrator.bootstrap.yaml`，API/CLI 显式设置 `no_mcp`、全局禁用 `x_search`，关闭持久记忆/用户画像，限定 12 次工具迭代和 120 秒闲置。当前无金融 MCP、模型或凭证，尚未部署为命名 Profile；这些限制不等于费用预算或绝对运行时间上限。
- 新增镜像内检查器 `check_hermes_tool_policy.py`，使用上游实际 YAML 和工具集解析器。首次执行发现上游不提供 PyYAML，改用其 `hermes_yaml` 后验证：API/CLI 各自的基础、意外 MCP、额外凭证六种情形均为空；移除 x_search 限制的反向配置正确失败。没有运行模型、MCP 子进程或业务工具，临时配置和状态目录随检查清理。
- 证据写入 `runtime-observation.json`，README 提供复现命令。尚未证明模型执行入口、请求级覆盖、插件或完整 Profile 隔离；TASK-014/015 保持未完成，金融工具接入后必须复验，不把“全部工具暂时关闭”视为研究功能完成。

## 后续增量：Alpha 金融工具只读凭证与快照契约

- 新增可选 `YTA_ALPHA_TOOLS_TOKEN`，至少 32 字符，拒绝与现有服务凭证相同、示例占位值、空白及非 ASCII。缺省不启用工具访问，验证错误不显示凭证。未修改现有 BFF/Jobs 凭证的权限。
- 新增两个仅接受工具凭证的 GET 端点 `/internal/v1/tools/market-snapshot` 与 `/internal/v1/tools/indicators`。前者通过 `ToolMarketSnapshot` 返回完整原始快照和 Alpha 既有规范化哈希；后者复用 Alpha 指标计算，保留数据模式、来源/截点、哈希、Decimal 字符串与缺失 null。没有新增写入、冻结授权或数据库权限。
- 新增 10 项 HTTP/配置测试，覆盖原始快照及指标哈希一致、数值传输、所有其他内部路由拒绝工具凭证、匿名/错误/普通服务凭证拒绝工具端点、未配置默认关闭、LIVE 缺少 Provider 不回退 REPLAY、不同权限凭证不可复用与错误脱敏。
- Python 全量 115 项通过，包含真实专用 PostgreSQL 测试库；Ruff 通过，Alpha OpenAPI 已重新导出。README 与 `.env.example` 记录独立凭证配置。未向本地运行服务配置新凭证，未启用 Hermes 工具；MCP 桥接、真实供应商和完整研究 Profile 仍待完成，TASK-088 保持 IN_PROGRESS。

## 后续增量：金融 MCP stdio 进程与协议验证

- 新增 `services/tools/src/yta_tools`，独立进程只注册 `market_snapshot` / `indicators` 两个无参数只读工具，固定调用 Alpha 工具端点。采用官方 MCP SDK 2.3.0，与候选 Hermes 相同；通过可选 tools extra 安装，开发组纳入测试，Alpha 生产依赖保持不含 MCP。
- 配置使用独立 `YTA_TOOLS_` 环境变量，要求明确 URL、专用工具凭证与数据模式，不自动加载项目 .env。拒绝模型提供额外参数或任意工具名；不接受 URL/owner/mode/credential 覆盖，不提供冻结或其他写入。关闭代理和重定向，限制 2 MiB 未压缩 JSON、5 秒网络等待及 15 秒总时限。
- 快照复用 Alpha DTO，指标新增传输契约校验，检查模式、时间、哈希、图表/序列对齐及 Decimal 字符串/null，不在工具层重算金融指标。已修正首次验证中遗漏的 split_factor 字段，以及损坏压缩响应的错误分类。错误仅返回 MCP isError 和固定码，不反射上游文本、凭证或内部 URL。
- 新增 23 项测试：真实 MCP 协议客户端到 Alpha ASGI、独立 stdio 子进程到真实 loopback HTTP Alpha、工具发现与两次读取、注入/未知工具无网络副作用、鉴权/503/重定向/内部错误脱敏、跨模式拒绝、无效 origin、响应上限/编码/JSON、网络错误/总时限、浮点/NaN/日期/哈希/数组损坏拒绝。所有成功行情均为明确标记的合成 REPLAY，不是实际供应商验收。
- Python 全量 138 项通过，包含真实专用 PostgreSQL；Ruff、契约漂移、文档检查通过。wheel 构建并确认包含工具模块、不含本地 env 文件；Alpha Dockerfile 补充工具源码以支持统一 wheel 打包，生产镜像重新构建为 `sha256:3619347ed66e3355881bccee52f9650db4546e37247c31e188d7e2df9ee78536`。
- 未改 BFF/Web 或数据库迁移，未重复 TypeScript/浏览器套件；未修改已有本地服务凭证或启用 Hermes 工具。真实 Hermes 注册、模型调用、MCP 容器部署及完整工具隔离仍待验证，TASK-088 与整体真实接入仍未完成。

## 后续增量：私网 HTTP MCP 与固定 Hermes 实际工具注册

- 新增私网 Streamable HTTP 入口，单独的 MCP Bearer token 必须不同于 Alpha 工具凭证；不提供公网 OAuth。拒绝匿名/错误/重复 Authorization、任何 Origin，限制 Host 为内部服务与 loopback，入站 body 32 KiB，所有响应 no-store。stdio 入口保持可用。
- 新增 HTTP 鉴权/边界、实际 HTTP MCP 客户端到 Alpha、凭证不可复用三项测试；首次测试暴露 SDK session manager 实例不能重复启动，改为每项测试创建独立 app 后通过。Python 全量 141 项通过，包含专用 PostgreSQL 与现有 stdio 测试。
- tools Docker target 构建成功，镜像 `sha256:80268bcfff89d420d551407f9dbe5f283e1c2123d722b8b6254c74be2c5396a6`。新增隔离 Compose，两条 internal 网络分开 Alpha 与 Hermes；工具服务连接两者但仅持有只读 Alpha 凭证。无公开端口、数据库配置或宿主机数据挂载；实际 inspect 核对非 root、只读和网络配置。初次 tmpfs YAML flow 解析错误已修正为带引号条目后成功启动。
- 固定 Hermes 镜像内创建一次性 alpha-orchestrator Profile，调用真实 MCP discovery/registry：仅注册 `mcp__yta_finance__market_snapshot` 与 `mcp__yta_finance__indicators`，选中工具集仅 yta-finance，MCP sampling 禁用。两个实际调用的 REPLAY 快照哈希相同，价格为字符串，缺失 SMA 为 null；URL 参数注入被拒绝，Hermes 不能解析 Alpha。脱敏证据保存于 `runtime-observation.json.finance_bridge`。
- 这是实际 Hermes 工具注册与调用证据，不是模型驱动的 Gateway Run：未发送模型请求，未验证 SSE、取消、崩溃恢复、跨用户/完整 Profile 隔离。金融源仍是合成 REPLAY，真实 Provider 仍未接入；所有总体集成门槛保持 UNVERIFIED。测试后已关闭容器及演练网络，独立私有 env 文件保留。

## 后续增量：行情供应商资格研究与 Massive 原始采集候选

- 检查当前项目配置，未发现已配置的行情/模型 Provider 项；已询问供应商及模型名称，不索取聊天明文密钥。官方资料核对记录见 `market-provider-research.md`，不是账户或实际请求证据。Massive 公开 RAW 日线和拆股参数较明确；Finnhub RAW 口径仍待核验，尚未替用户选定服务或套餐。
- 新增 `yta_alpha.massive.MassiveCandidate`，固定 `api.massive.com`、NVDA daily `adjusted=false` 与当前 `/stocks/v1/splits`，凭证只入 Authorization header。保留不可变原始页 bytes、请求范围、实际 UTC 采集时间与 SHA-256；直接将 JSON 小数解析为 Decimal，拒绝重复字段、非有限数和错误顶层结构。
- 网络关闭环境代理/重定向，限制单页 2 MiB、每端点 10 页、60 秒整体 deadline / 5 秒网络等待。分页必须同 HTTPS host/path，有非空 cursor，不能带 apiKey、重复查询键、变更筛选或形成循环；权限、429、网络及无效响应返回脱敏错误，不自动重试或回退数据。
- 24 项离线测试覆盖两类正确端点/请求参数、18 位小数精度、原始哈希/时间、空结果不补齐、分页成功/循环/页数边界/跨域与范围拒绝、鉴权/限流/错误脱敏、复权响应拒绝、恰好字节上限及超一字节、总时限。没有请求真实供应商或消耗付费额度。Python 全量 165 项通过，包含真实专用 PostgreSQL；Ruff、契约漂移和任务文档检查通过。
- 采集结果固定为 UNVERIFIED，未转为 MarketSnapshot、未持久化、未绑定 API/Worker。官方响应缺少历史可获知时间及覆盖证明，完整公司行动覆盖和日线收盘语义仍须资格审查。TASK-039/042 改为 IN_PROGRESS；真实账户两年样本、价格/拆股规范化、证据落库及实际接入尚未完成。无 BFF/Web 或数据库变更，未重跑其构建/E2E，也未更新已构建运行镜像。

## 后续增量：候选原始日线质量边界

- Massive 采集在完成日线分页后、请求公司行动前校验逐条 OHLCV：价格必须为正的 JSON 精确数值，成交量非负，拒绝布尔、字符串、缺失字段及不一致的高低价范围。保留原始响应和原精度，不补价格、不改写数值。
- 聚合开始时间要求整数毫秒，使用整数时间运算转为 America/New_York 日期；日期必须在请求范围且跨页严格递增。重复日期（即使时间戳不同）、倒序、越界及时间溢出均拒绝。该日期仅为供应商聚合日期，不声称已完成交易所日历、正常时段收盘或公司行动资格验证。
- 新增 15 项测试，覆盖字段损坏、零成交量有效、跨夏令时偏移、UTC/美东日期边界、缺失日期保持原样、跨页重复与倒序。候选专项共 39 项通过；Python 全量 180 项通过，包含真实专用 PostgreSQL。Ruff、契约漂移及 120 任务文档检查通过；现有 4 条 pandas 弃用警告不变。
- 仍未调用真实供应商、持久化采集证据或接通 LIVE_RESEARCH；拆股规范化、账户授权、完整覆盖及实际两年样本待完成。TASK-039/042 保持 IN_PROGRESS。本轮未改 BFF/Web、数据库迁移或运行镜像，未重复其构建及浏览器套件。

## 后续增量：候选拆股事件规范化

- Massive 候选采集增加不可变 `CandidateSplit`：保留供应商事件 ID、执行日期、原始 Decimal 股数分子/分母、行动类型、实际采集时间作为保守 available_at，以及对应原始页 SHA-256。累计 historical_adjustment_factor 不参与单次比例解释；原始页仍保留该字段。
- 校验 NVDA、规范日期与请求范围、正数精确比例、正向/反向类型和比例方向一致；跨页拒绝重复 ID、重复日期及倒序。当前领域快照不支持同日多个行动，因此不自动合并。未知类型、stock_dividend 或缺失类型明确返回 UNSUPPORTED_CORPORATE_ACTION，不静默删除或猜测 1:1。
- 新增 23 项测试，包括正向/反向、原始证据关联、18 位小数比例、错误字段/日期/方向、重复与跨页重复、未知行动和空结果不证明覆盖。Python 全量 203 项通过，包含真实专用 PostgreSQL；Ruff、契约漂移及文档检查通过。现有 4 条 pandas 弃用警告不变。
- 该对象仍为候选事件，不声称执行日期已验证为交易所 session，不生成 MarketSnapshot 或 actions_verified_through，不执行收益/结算。真实账户、供应商资格、完整覆盖、数据持久化及 LIVE 接入继续未完成；TASK-039/042 保持 IN_PROGRESS。未改数据库、BFF/Web 或运行镜像，未重复其构建/E2E。

## 后续增量：原始采集证据持久化

- 迁移 009 新增独立 provider_acquisitions / provider_response_pages，不写入已核验 data_snapshots。manifest 包含供应商、NVDA、显式 data_mode、请求起止、每页路径/查询、采集时间和内容哈希；原始 bytes 存 bytea，数据库核对 SHA-256 及页大小。资格固定 UNVERIFIED。
- AcquisitionStore.save 先复核日期/日线/拆股及证据边界，再以 manifest 哈希加事务 advisory lock 去重。manifest、全部页和 provider.acquire 审计同事务提交；相同证据重试不增行，重新采集、原始内容修订或数据模式变化追加独立记录。保存成功不是供应商资格批准，也不代表数据可进入 LIVE 指标。
- 新增 9 项测试：真实 PostgreSQL 中 12 次并发重试仅一份事实/审计、原始字节和元数据往返、修订/采集时间/模式区分、两张表 owner 触发器与 runtime 权限拒绝修改删除截断、数据库哈希约束失败后整批回滚；另验证空页集、敏感查询键、错误路径、无时区时间在连接数据库前拒绝。
- 迁移已在 yta_test 应用，运行角色仅授予新增表 SELECT/INSERT。全量 Python 212 项通过；Ruff、契约漂移与 120 任务文档检查通过，已有 4 条 pandas 弃用警告不变。TASK-043 改为 IN_PROGRESS，不把原始证据表视为完整行情规范化落库完成。
- 未运行真实供应商请求、未修改正常应用数据库或部署镜像、未挂接 API/Worker。仍需账户/许可核验、实际两年数据、完整公司行动覆盖、交易日/收盘口径资格和供应商采集运行入口。本轮无 BFF/Web 修改，未重复其构建/E2E。

## 后续增量：候选行情采集命令入口

- 新增 `python -m yta_alpha.acquire`，显式接收起止日期与 data_mode，使用独立 YTA_ACQUIRE_MASSIVE_TOKEN / YTA_ACQUIRE_DATABASE_URL 环境配置，不自动加载 .env，不接受命令行凭证。配置错误、范围错误在网络请求前拒绝；先检查证据/审计表存在、runtime SELECT/INSERT 及无 UPDATE/DELETE/TRUNCATE，再请求 Massive 并保存校验后的证据。
- 成功输出仅含采集哈希、来源、模式、页数、记录数及 UNVERIFIED；提供者、数据库及未知异常均输出脱敏错误码和非零退出码，不反射响应体、连接字符串或 traceback。没有自动重试、fixture 回退、资格批准或写入正式市场快照的路径。
- 新增 8 项验证：无配置真实子进程退出、三类失败脱敏、错误范围无 I/O、存储未就绪无供应商请求、模拟 HTTP → 实际命令 main → 真实 PostgreSQL 两页落库、owner 凭证在请求前被拒绝。专项与已有存储测试合计 17 项通过；全量 Python 220 项通过，包含真实 PostgreSQL。Ruff、契约漂移、文档检查及 CLI help 通过，已有 4 条 pandas 弃用警告不变。
- README 记录配置、权限、运行命令及证据边界。未执行真实 Massive 请求或改动运行服务/部署镜像，未接入 API/Worker 调度。真实账户、模型 Provider、供应商资格与完整 LIVE 研究仍待完成；TASK-039/042/043 保持 IN_PROGRESS。无 BFF/Web 变化，未重复其构建/E2E。

## 后续增量：持久证据读取与完整性检查

- AcquisitionStore.load 按采集哈希及显式 data_mode 读取，模式不匹配与不存在统一 NOT_FOUND。读取后复核 manifest 哈希、每页原始 bytes 哈希、数量与连续序号、元数据、请求范围和已有日线/拆股规则；从原始证据重建 Decimal 拆股事件。任何不一致返回 ACQUISITION_INTEGRITY_FAILED，不改写事实或提供已核验快照。
- 新增独立只读命令 `python -m yta_alpha.inspect_acquisition --hash ... --data-mode ...`，仅需数据库环境配置，不需要供应商凭证，不调用外部行情。输出计数、范围、最后采集时间及 integrity_checked；供应商 qualification_status 仍为 UNVERIFIED，完整性通过不能替代资格审查。
- 新增 7 项真实 PostgreSQL 验证：原始对象往返及模式隔离、四类直接 INSERT 的不一致证据被检测（缺页/元数据/manifest 哈希/页序）、无供应商凭证的独立子进程检查、18 位小数拆股从原始 bytes 重建。没有禁用不可变触发器或修改既有事实来构造异常。
- 全量 Python 227 项通过，含真实 PostgreSQL；Ruff、契约漂移、CLI help 和任务文档检查通过，已有 4 条 pandas 弃用警告不变。README 增加复查命令。未修改迁移、正常运行数据库或镜像，无 BFF/Web 变化，未重复其构建/E2E。
- 真实供应商、行情资格、API/Worker 调度与完整 LIVE 研究仍未完成；TASK-039/042/043 保持 IN_PROGRESS。

## 后续增量：候选行情交易日质量报告

- 新增 acquisition_quality.assess，并接入已有只读证据检查命令。报告对照查询范围内交易日，按行情页最新实际采集时间列出已收盘但缺失的 session；逐页检查每根日线是否早于其实际收盘时间采集，同时列出非交易日日线、非交易日拆股、未到期 session 和最新应有 session。不会填价、延长目标日或使用后来的检查时钟伪造历史可得性。
- TradingCalendar 新增允许边界为周末/假日的 sessions_between，保留已有严格 sessions 行为。报告记录日历版本及 XNYS_PROXY_REQUIRES_NVDA_QUALIFICATION，资格始终 UNVERIFIED；CHECKS_PASSED 不是实际供应商资格批准或公司行动覆盖证明。
- 新增 8 项测试：缺口清单且原始 bytes 不变、半日市收盘前后、DST 前后精确收盘边界、周末日线/行动、纯周末范围和日历越界/反向范围、后页采集不能把前页盘中 bar 变成最终值。全量 Python 235 项通过，含真实 PostgreSQL；之后强化独立检查子进程的质量结果断言并专项通过。Ruff、契约漂移与文档检查通过，现有 4 条 pandas 弃用警告不变。
- TASK-044 改为 IN_PROGRESS；尚未取得真实供应商样本、NVDA 交易所资格和完整公司行动覆盖，未启用正式行情或结算。未修改数据库迁移、运行镜像或 BFF/Web，未重复其构建/E2E。README 已记录质量字段及资格边界。

## 后续增量：候选采集生产镜像与迁移升级验证

- 重新构建 migrator 与 Alpha runtime，纳入采集/检查/质量模块及迁移 009。当前本机镜像 ID：migrator `sha256:0e748882e3c1febeb6713223144216507bca1f192cc42e87eba3365375e89f2a`；Alpha `sha256:58ab704db7a77659cbcd933a7cf86c76616cc460f84c39736e75193baa699c6d`。此前金融桥接证据中的旧镜像摘要仍对应当时实测，没有改写为新镜像的验证声明。
- 扩展 check_migration_container.py，除全新 001–009 安装、重复执行、缺配置/runtime 拒绝外，新增 001–008 旧库升级：通过当前 migrator 仅应用 009，已有审计记录保留，重复升级无动作。全部实际容器/专用 PostgreSQL 检查通过。
- 新增 check_acquisition_container.py，固定解析当前 runtime 镜像 ID，以 UID 10001、只读根、cap-drop/no-new-privileges，无源码挂载执行已安装 Python 包。模拟供应商 HTTP → 实际采集 CLI → 真实隔离 PostgreSQL 两页与一条审计；另一个无供应商凭证容器执行只读检查，验证完整性与三天缺口报告，未写已核验快照。确认 Alpha 基础生产依赖未意外引入 MCP。
- 实际供应商请求数为 0。所有临时数据库均由 finally 清理，随后查询确认演练库剩余数 0；没有修改正常应用数据库或替换运行服务。README 增加构建和验证命令。
- Ruff 与文档检查通过。本轮未修改业务实现、契约或 BFF/Web，未重复上一轮已通过的 235 项 Python 或 TS/浏览器套件。验证范围是本机容器与测试数据库，不代表 staging 发布、真实供应商资格或完整 LIVE/Hermes 验收；相关任务仍未完成。

## 后续增量：原始采集修订差异报告

- 新增 acquisition_diff.compare，通过已核验完整性的 StoredAcquisition 对比同一范围和模式的两份证据。按美东日期列出日线新增/缺失/字段变化，按供应商事件 ID 列出行动变化；价格、成交量、股数比例按 Decimal 精确比较并以字符串输出。保留前后原始数据，不更新冻结价或自动重结算。
- 仅 JSON 格式、等值小数写法或采集时间变化不报告数值修订；原始页哈希变化和前后实际采集时间单独返回。不同范围/模式明确拒绝，缺失一侧用 null 表示，不将记录消失推断为市场事实已撤销或自动获得覆盖证明。
- 检查 CLI 新增可选 --compare-hash，同一显式数据模式读取两份记录后输出前后哈希与差异。README 记录用法、范围与资格边界。
- 新增 11 项验证：18 位小数价格变化、格式/数值尺度等价、重复采集时间、日期增减排序、拆股比例更正及事件增减、范围/模式拒绝，以及真实 PostgreSQL 读取两版本进行 CLI 对比且审计和原始事实不变。全量 Python 246 项通过，含真实 PostgreSQL；Ruff、契约漂移、文档检查通过，现有 4 条 pandas 弃用警告不变。
- 未调用真实供应商、修改数据库迁移或接通 LIVE。上轮构建镜像尚不包含本轮新增对比模块；未重建镜像或重复 BFF/Web 检查。TASK-044 保持 IN_PROGRESS，真实数据资格、完整异常隔离及生产修订流程仍待验证。

## 后续增量：原始行情证据备份恢复演练

- 对当前专用 yta_test 执行真实 pg_dump / pg_restore 演练，私有归档 `yta_test-20261027T085436Z-b6b32f5e` 与 manifest 均为 0600。恢复到新库 `yta_restore_f963e293317144998815b8ba273492a4_test`，没有覆盖源库或正常应用库；按既有恢复工具约定保留该库供复查。
- 原有恢复验证通过：27 张表的行数/内容哈希一致，25 个不可变触发器实际拒绝写变更，5 个 identity 序列正常；1452 条冻结预测、523 条成功结算按保存快照重新计算一致。新原始证据两表已由动态 inventory、权限与触发器检查自动覆盖，无需修改既有备份格式。
- 新增 check_acquisition_restore.py，以本地 runtime 角色读取源/恢复库全部候选证据，比较原始表内容、重建对象和完整性结果。实测 92 份采集 / 180 页原始响应一致，其中 76 份通过完整性检查并精确重建，16 份由测试直接 INSERT 的异常证据在恢复前后都被拒绝；没有把这些异常记录修补或误报为可用行情。供应商资格仍为 UNVERIFIED。
- README 记录复查命令、静止测试源库要求和两类计数含义。Ruff、格式与文档检查通过。本轮未改业务实现、迁移或运行镜像，未重复上一轮 246 项 Python / BFF/Web 套件；真实备份/恢复与证据读取属于本轮新增实测证据。
- 此演练限本机专用测试库，不证明生产异地备份、定时策略、staging 恢复目标或真实供应商质量；整体 goal 与相关发布任务仍未完成。

## 后续增量：CI 采集验证与 Alpha 构建目标修正

- 检查 CI 发现 Compose Alpha 构建未指定 target，而 Dockerfile 在加入独立 tools target 后最后阶段已是工具服务；默认构建会使 Alpha 服务使用错误入口。compose.app-local.yaml 显式锁定 target: runtime。实际 Compose 重建 Alpha/BFF/Web 后检查 Alpha Cmd 为 uvicorn yta_alpha.api:create_app、端口 8100，三个服务均健康。
- 实际 check_container_stack.py 通过私网/非 root/只读/前端隔离、静态资源、鉴权/readiness 和 Worker 检查；实际采集容器检查再次通过，真实供应商请求为 0。当前 Alpha 镜像 `sha256:f4a81ff78238a7a84309d315b9998622a6d661be985b2fcf9a2bec9441fe0807` 包含前轮修订对比代码。检查后已关闭并移除应用演练容器及 edge 网络，保留 PostgreSQL。
- CI 容器任务新增 check_acquisition_container.py；数据库/契约任务将备份演练结果保存为本地 JSON，再取恢复库名执行 check_acquisition_restore.py。归档不作为公开 artifact 上传。Workflow YAML 本地解析通过，尚未推送或取得 GitHub Actions 远端运行证据。
- 根 conftest 新增可选 --require-postgres，CI 默认启用：缺少配置、库名非 _test、没有选中 postgres 用例或用例跳过均失败；错误不回显 DSN。5 项独立临时 pytest 子进程验证这些通过/失败路径；这些合成 marker 测试不代替实际数据库验证。
- 本轮完整 `uv run pytest -q --require-postgres` 在真实专用 PostgreSQL 下 251 项通过，未跳过数据库用例；现有 4 条 pandas 弃用警告不变。Ruff、全库格式、文档检查通过，容器构建包含 BFF TypeScript 与 Web Next 构建。没有修改 TypeScript/Web 业务，未重复宿主机 pnpm test 或浏览器 E2E。
- 真实供应商/模型、锁定 Hermes 全协议、staging/amd64/远端 CI/Review 等仍待验收，未将本地检查视作发布完成。

## 后续增量：采集数据库等待时限与安全重试

- 原采集网络请求已有 deadline，但 PostgreSQL advisory lock / SQL 等待未设显式上限。采集存储新增共享连接入口，将 connect_timeout 固定为 5 秒、服务端 lock_timeout 5 秒、statement_timeout 15 秒；采集预检、保存和证据读取共用。它们是单次操作限制，不宣称全采集流程具有绝对墙钟时限。
- 超时经原有事务上下文回滚，CLI 继续输出脱敏存储错误，不自动重新请求供应商。保存已知证据仍使用同一内容哈希进行幂等重试，不引入覆盖写入或新幂等身份。
- 新增 3 项真实 PostgreSQL 验证（测试内缩短超时至 50ms）：另一连接持有同采集 advisory lock 时失败且没有采集/审计残留，释放后重试只生成一份；pg_sleep 被 statement_timeout 取消且回滚后连接可继续使用；表被 ACCESS EXCLUSIVE 锁定时读取按时失败，释放后能精确重建证据。专项 22 项、完整 --require-postgres 套件 254 项通过，无数据库跳过。
- Ruff、契约漂移和文档检查通过，既有 4 条 pandas 弃用警告不变。README 说明各时限与重试边界。本轮未改迁移、BFF/Web 或运行镜像，未重复容器/浏览器构建；真实供应商与 LIVE 研究仍未接通，发布任务保持未完成。

## 后续增量：采集尝试与失败审计

- 采集命令在发出供应商请求前追加 provider.acquire_started，生成独立 attempt_id，记录固定供应商/证券、日期范围与模式。成功事件 provider.acquire_completed 与原始采集事实在同一事务提交，关联 acquisition_hash；重复完成请求不重复追加终态。
- 已确认供应商失败追加 provider.acquire_failed，仅保存白名单固定码，未知错误归为 ACQUISITION_FAILED，不保存异常消息、上游响应或凭证。CLI 成功及开始后的失败返回 attempt_id。存储提交不确定、终止或取消不会伪造失败记录；只有开始事件的尝试需核对，不自动重发请求。
- 以 attempt advisory lock 串行化终态，校验开始范围/模式，拒绝终态改写；完成审计虽先在事务内插入，但任何后续事实写入/校验失败都会一起回滚，避免没有证据的成功审计。复用既有不可变 audit_events，无新增迁移或权限。
- 新增 6 项真实 PostgreSQL 验证：429 与未知异常经 CLI 脱敏并可用 attempt_id 查到审计；成功终态幂等且不能改为失败；原始页哈希失败回滚成功事件；范围/模式不符拒绝及未知码清洗；成功/失败并发竞争只提交一种终态，采集事实存在与成功终态一致。
- 完整 --require-postgres 套件 260 项通过，未跳过数据库测试；Ruff、契约漂移及文档检查通过，既有 4 条 pandas 弃用警告不变。README 记录尝试身份和未决状态边界。未调用真实供应商、改动 BFF/Web 或重建运行镜像，未重复其构建/E2E。真实资格、LIVE 与发布验收仍未完成。

## 后续增量：采集尝试只读核对入口

- 检查 CLI 新增与 --hash 互斥的 --attempt-id，按显式 data_mode 查审计；无记录/跨模式返回 NOT_FOUND。只有开始事件返回 UNRESOLVED，不将其解释为进程仍运行或已失败。失败只返回白名单码；成功先读取核验关联采集及范围，再返回 COMPLETED 与哈希。
- 新增 get_attempt 审计序列核验，拒绝重复开始/多终态、缺失成功证据和未知失败码，返回 ACQUISITION_ATTEMPT_INTEGRITY_FAILED；只读查询不会终结尝试或请求供应商。迁移 010 为三类采集审计增加 resource_id/id 部分索引，无事实覆盖和新增角色权限。
- 新增 7 项真实 PostgreSQL 验证：三种状态及模式/不存在隔离、三种审计异常、无需供应商凭证的只读 CLI 保持未决状态。完整 --require-postgres 套件 267 项通过，未跳过数据库验证；Ruff、全库格式、契约和文档检查通过，既有 4 条 pandas 弃用警告不变。
- 重建 migrator `sha256:6f73ebb645cade62be6766067bb9143a2064c04a51e169bc3fbc62597506512e` 与 Alpha `sha256:461c53c6db5515655061dd5979677b6e166c246d8b2bf326f460bcb79a6a8205`。实际迁移容器验证全新 001–010、001–009 到 010 升级、重复执行、原审计保留与角色隔离通过。采集容器新增按 attempt_id 查询成功状态及证据核验，实际生产包演练通过，供应商请求为 0，临时库自动清理。
- 迁移仅应用于专用测试/演练库，未更新正常应用库或部署服务。README 已记录状态含义与查询命令；无 BFF/Web 修改，未重复其构建/E2E。真实供应商资格、LIVE/Hermes 完整接入与发布门槛继续未完成。

## 后续增量：固定 Hermes 真实失败 Run、幂等与重启观察

- 回到真实主链路，在固定候选镜像 `sha256:67f82b05c5c083f2795cff9c361b4f5c464116fcc4301671709376e6f64e3226` 上新建独立 yta-hermes-failure-probe 项目与数据卷。实际 inspect 确认 UID 10000、只读根、network none、无映射端口；无模型凭证、无外部网络。没有修改原 startup/finance 探测卷或 YTA 应用库。
- 新增 probe_hermes_failure.py，提交前保留唯一请求键与正文，后续核验/重启沿用该键。真实 POST：匿名/错误认证 401，有效请求 202；Run 进入 failed，Gateway 日志确认原因是未连接模型 Provider 的 AuthError。同键同正文返回同一 Run（202/replayed），同键不同正文返回 409/idempotency_key_conflict。
- 同进程失败 SSE 实际返回 run.failed（seq=0）；使用该 Last-Event-ID 恢复无重复数据。重启同一实例后状态查询仍为 failed，同键仍返回同一 Run，持久 run_idempotency 表记录数为 1；原 events 端点返回 404。后续 Adapter 必须区分持久状态与内存事件流，不能把状态恢复视为事件历史可回放。
- 首次探测暴露两处脚本假设错误：Docker network none 内有未启用的内核隧道设备，改为核对启用网卡仅 lo（同时 inspect 证实 network none）；SSE 事件字段为 event 而非 type。错误发生后使用已保存的同一键核验，没有改键重发或新增研究 Run。原始收据和脱敏结果均保存在独立卷/本地 0600 文件；公共观察见 runtime-observation.json，未保存 token 或请求键。
- 协议观察后将收据更新改为 fsync + 原子 replace，避免进程中断截断原始键；两项本地测试验证独占创建、0600、正常更新及替换失败保留旧身份。观察文件分别记录协议实测版本和当前脚本 SHA，未将后续收据写法的单元验证冒称为同一次容器实测。
- Hermes 归档/锁/探测专项共 24 项通过；28 个锁定源码文件复核通过，Ruff、格式与文档检查通过。本轮未改 YTA 业务实现、数据库或 BFF/Web，未重复此前 267 项全量/浏览器套件。探测容器已停止并移除，保留独立数据卷供复查。
- 这是一个真实失败 Run 的协议证据，不证明成功模型推理、多轮会话、工具流、运行中取消、崩溃未知执行恢复或完整 Profile 隔离。所有总体门槛继续 UNVERIFIED；真实 Adapter/Provider 与完整发布验收仍未完成。

## 后续增量：合成模型驱动固定 Hermes 的实际工具 Run

- 新增独立 hermes-model-probe Compose、合成模型 HTTP 服务、Profile 启动脚本及 Run 探测脚本。实际 inspect 验证四个容器非 root、只读、无公开端口，三个网络均 internal；Hermes 不能解析私有 Alpha。固定 Hermes 镜像未修改，Alpha 与工具镜像摘要记录在 runtime-observation.json，未使用实际模型/行情凭证或请求外部服务。
- 启动曾因命名 Profile 默认禁止单独 Gateway 而退出 78。仅此演练配置启用锁定版本的 gateway.standalone 兼容选项后，实际 Gateway 健康检查通过。该选项不证明生产 Profile 多路复用隔离。
- 前两个已确认 completed 的诊断 Run 没有金融工具事件：模型实际收到的是 tool_search/tool_describe/tool_call，说明默认 MCP 渐进式暴露与合成脚本预期不匹配。探测器明确失败，没有把 completed 冒称工具成功。保留原请求身份和收据，随后独立 direct 用例显式关闭 tools.tool_search；默认搜索路径仍未验证。
- direct 用例实际完成 Gateway → 合成模型 → Hermes MCP → tools → Alpha REPLAY。SSE 顺序为两组 tool.started/tool.completed、message.delta、run.completed；两个金融工具均无错误且调用 ID 对应，序号唯一递增。使用相同幂等键复查仍返回原 Run，末尾 Last-Event-ID 恢复无重复事件。收据先于提交以 0600 独占创建，更新使用原子替换；未知执行不换键重发。
- 新增 9 项测试，覆盖合成模型按关联结果推进两个工具、拒绝其他模型，以及探测器拒绝无工具、工具错误/缺失错误标志、错配调用 ID、重复序号及失败终态。Hermes 相关套件合计 33 项通过。实际容器验证另行执行，不以单元测试替代；本轮未改金融业务、数据库或 BFF/Web，未重复此前 267 项全量与浏览器套件。
- TASK-015 更新为 IN_PROGRESS；所有整体能力门槛仍为 UNVERIFIED。真实模型、会话连续性、运行中取消、崩溃未知执行恢复、默认工具搜索、staging/远端 CI 与发布验收继续待完成。演练结束移除容器和网络，保留独立数据卷及私有收据。

## 后续增量：复用 pi 默认配置完成真实模型联调

- 用户明确选择使用 pi agent 的 LLM 配置。只读解析 ~/.pi/agent/settings.json 与 models.json，选中 volc-glm / deepseek-v4-pro、Anthropic Messages 兼容路由及对应鉴权头；不读取其他 Provider 凭证、不执行配置中的 shell 命令、不修改 pi 配置。选中路由导出为独占创建的 0600 私有文件，通过 stdin 写入独立 Hermes 数据卷，未将密钥加入 Compose 环境或仓库。
- 在固定 Hermes 镜像的 SDK 中执行一次真实小输出请求，关闭 SDK 自动重试，30 秒超时、64 token 上限。返回 deepseek-v4-pro-ga-260813，固定校验文本一致，报告 input=16 / output=5；此步骤是实际远程模型请求，不是合成服务。
- 新增 compose.hermes-pi-probe.yaml 与实际模型 Run 探测。Hermes 具备模型出网网络、无映射端口、非 root/只读；Alpha 与金融工具仅使用 internal 网络，Hermes 不能解析 Alpha。配置仍为命名 Profile standalone 兼容模式与直接 MCP schema，最多 4 轮、Gateway 超时 120 秒。
- 首轮真实模型 Run 已完成两个金融工具及 SSE，usage 为 input=6816 / output=492。随后核对锁定源码发现顶层 reasoning.enabled 不是 Gateway 读取的字段，改为 agent.reasoning_effort=false；实际运行时解析为 enabled=false，再用独立且预先保存身份的用例复测成功，usage 为 input=10445 / output=1722。保留两次证据和各版本脚本哈希，不把第二次配置冒称为第一次设置；这些为 Run 报告用量，非账户账单或辅助调用总量。
- 两次 Run 均验证 market_snapshot、indicators 各一次、无工具错误、开始/完成调用 ID 对应、唯一递增 SSE、run.completed、最终回答披露 REPLAY。相同键复查返回原 Run，末尾游标恢复无重复；无未知执行换键重发。财务数据仍是合成 REPLAY，不因真实模型接通而进入 LIVE_RESEARCH。
- 新增 8 项测试：选中路由和鉴权/思考设置保留，拒绝非 HTTPS、URL 凭证/查询、未知协议及 shell 凭证，交错工具事件相关性。真实专用 PostgreSQL 下全量 --require-postgres 套件 286 项通过，无数据库跳过；Ruff、格式、契约及 120 任务文档检查通过，原有 4 条 pandas 弃用提示不变。未修改 BFF/Web 或金融持久化逻辑，未重复 TS/浏览器套件。
- 扫描未发现实际导入密钥进入项目源文件、容器日志或 Docker 配置元数据。TASK-013 更新为 IN_PROGRESS，真实模型选择不再是缺失项；取消、未知执行恢复、会话连续性、完整隔离、预算/超额策略、行情资格、staging 与发布门槛仍未完成。演练容器和网络已清理，保留私有卷及收据供后续核验。

## 后续增量：真实模型取消与崩溃未知执行确认

- 继续使用用户指定的 pi 默认模型，在同一固定 Hermes 镜像和独立演练卷新增两个预先持久化身份的用例。未修改 pi 配置、YTA 应用数据库、金融规则或生产服务。
- 取消用例先观察真实模型非空 message.delta，再发送 stop；错误认证返回 401，有效停止后 status=cancelled、SSE 以 run.cancelled 结束，没有工具执行、run.completed 或 run.failed。重复停止、同键复查及末尾游标均通过。重启后取消终态和同键身份保留，旧 SSE 返回 404。
- 崩溃用例在观察实际模型输出且状态 running 后保存 admission_id/execution_generation，由宿主脚本确认项目标签及非 root/只读边界，再仅对该演练 Hermes 容器执行 SIGKILL。实际退出 137，随后启动同一容器和卷。重启后 GET 状态为上游字面值 unknown，同键返回原 Run、执行代次不变，stop 返回 409/unknown_execution，旧 SSE 404。
- 在此测试用例上明确执行未知状态确认：错误代次返回 409/stale_generation，状态保持 unknown；精确身份与代次 resolve-unknown 成功，重复确认成功，终态 cancelled，同键仍为原 Run。源码和实测一致：确认表示将丢失的执行标为 interrupted，不重排队；并不证明远端从未执行或授权换键重发。未来 Adapter 必须保留未知历史及确认审计，不能把确认后的 cancelled 当作原执行已知安全取消。
- 新增 8 项探测防误报测试，覆盖取消终态/输出要求、完成或失败/工具执行/重复序号拒绝、空 delta 不算真实输出和停止前已结束的拒绝。实际远程模型/容器崩溃证据独立记录于 runtime-observation.json，不以模拟 SSE 单元测试替代。
- Hermes / pi 相关专项共 49 项通过；Ruff、格式、契约及文档检查通过。无金融业务、数据库或 BFF/Web 变化，未重复此前 286 项全量及 TS/浏览器套件。演练容器与网络已移除，保留私有卷、原请求收据与脱敏证据。
- 本轮覆盖文字生成中的取消及丢失 owner epoch；工具执行中取消、金融副作用、远端计算/计费是否立即停止、会话连续性、完整 Profile 隔离、staging/预算/真实行情仍未验收。TASK-015 与总体能力门槛继续 IN_PROGRESS / UNVERIFIED。

## 后续增量：真实模型会话连续性与重启后回忆

- 新增 probe_hermes_session.py，使用现有 pi 真实模型、固定 Hermes 镜像与独立演练卷。首次提交前一次性保存随机 128 位标记、两个独立会话键及四个独立请求键；私有收据独占创建、原子更新，重复执行阶段使用原身份，不换键生成新的研究 Run。
- seed 发送标记并要求 ACK；recall 仅要求回忆，精确返回原标记且 session_id 与 seed 一致。isolated 使用另一 X-Hermes-Session-Key，精确返回 NONE，session_id 不同。实际重启 Gateway 并等待健康后，restart 阶段再次精确返回原标记，仍为原 session_id。四轮均完成，逐轮同键复查返回原 Run。
- 后续请求未包含标记、显式 session_id 或 conversation_history；没有把测试端提供的历史误当作服务端持久化。四轮 usage 分别为 input/output：2478/19、2517/26、2468/46、2564/57，均为 Run 报告值而非账户账单。
- 新增两项测试，防止后续请求携带答案/历史造成虚假通过，并核验每次探测及各阶段身份独立。架构第 3.1 节同步为局部实测证据及具体缺口，不把所有项目继续笼统写成无证据，也不将整体门槛标为通过。
- Hermes / pi 专项共 51 项通过，Ruff、格式、契约和文档检查通过。格式修正后重查四个原 Run，同键身份及答案仍一致，未再生成模型轮次。无金融、数据库或 BFF/Web 业务变化，未重复此前全量套件；演练结束移除容器和网络，保留独立卷及收据。
- 该证据只覆盖同一服务凭证下的会话上下文分离与本机重启；不同用户权限、跨 Profile 隔离、上下文压缩/轮换、保留期过期、完整断线窗口、工具中取消、staging 与真实行情资格仍待完成。TASK-015 保持 IN_PROGRESS，总体能力 UNVERIFIED。

## 后续增量：固定 Hermes 幂等保留期实测

- 新增 probe_hermes_retention.py，在固定镜像、UID 10000、只读根、network none、临时 SQLite 中运行哈希匹配的上游 Store。受控时钟只影响这个独立探测进程，不修改真实 Gateway。验证默认 86400 秒边界、四类终态过期、运行中/两种未知状态保留、scope 隔离、延长与冲突不可延长、重新打开后未知身份保留，以及 12 个独立 SQLite 连接只创建一份预留。
- 新增独立 yta-hermes-retention-http 项目与卷，无外网和模型凭证。先生成已确认 failed 的 Run，再只将该演练终态行的 updated_at 设为 24 小时前。实际 HTTP 同键同正文创建新 Run，replayed=false；再提交才重放新 Run，原 Run 仍可 GET 查询且保持 failed。两份身份均保留，脚本独占收据防止误重复实验。没有对业务/未知执行请求进行该操作，未修改 YTA 数据库事实，外部模型请求数为 0。
- 已验证“沿用旧键”不等于永久防重：过期记录清理后旧键可再次被接受。文档明确真实 Adapter 应优先核对已知 Run，不将过期/404 或缺失记录解释为未执行；本地发送身份和未知历史须长期保留。现有 BFF Runner 的 run.started 恢复分支不会再次调用 driver，pending 查询没有按年龄淘汰派发记录；真实 transport 尚未启用。
- 在真实专用 PostgreSQL 上重跑对应 BFF 恢复用例：1 项通过，确认遗留 admission 标记 EXECUTION_UNKNOWN、driver 计数不增加及 SESSION_BUSY 阻止新提交；同文件其余 14 项按名称过滤未执行，不计为数据库验证通过。两个新增固定镜像探测均通过；Ruff、格式、文档和契约检查通过。未改业务逻辑，未重复全量 Python / TS / 浏览器套件。独立演练容器已移除，保留私有卷及两份请求身份。
- 此证据使用受控时钟/已失败测试行时间设置，不是实际等待 24 小时的耐久测试；不证明全部 grant/ack 保留策略、所有超时窗口、跨 Profile 隔离或完整生产恢复。总体门槛继续 UNVERIFIED，TASK-015 保持 IN_PROGRESS。

## 后续增量：原生多 Profile HTTP 权限与并发隔离

- 新增单独 compose.hermes-profile-probe.yaml、启动配置脚本和实际 HTTP 探测。固定镜像在 network none、非 root、只读根、独立卷下启用原生 multiplex_profiles，服务两个测试 Profile：default 与 alpha-orchestrator；各自 .env/config 为 0600，API token 独立随机。没有模型凭证、MCP 或外部请求，不修改产品的单 Profile 注册表及 pi 配置。
- 各自凭证访问各自模型列表 200，匿名或另一 Profile 凭证 401。两个 Profile 使用同一请求键和正文创建不同的 Run，均因缺少模型凭证而 failed；自身同键重放返回原 Run，同键异参 409。用本 Profile 有效凭证访问另一 Profile 的状态、SSE、stop、resolve-unknown 均返回 404；未知 Profile 路由 404。
- 每轮另外以 6 个线程混合执行 36 个只读请求，实际结果为 200/401/404 各 12 次，未出现并发作用域串用。实际重启 Gateway 后重复完整矩阵通过，两份原 Run 的幂等身份仍保留。脱敏结果、镜像和脚本哈希见 runtime-observation.json。
- 首次脚本把匿名头写成带尾空格的 Bearer，httpx 在发出请求前拒绝；改为空 Authorization 后沿用已保存请求键继续，没有删收据换键。此为探测脚本问题，不是 Gateway 权限失败。
- 本轮是实际固定 Gateway 的权限测试，未请求真实模型；不宣称模型 Provider 凭证、Skill、文件系统、全部工具或生产部署隔离已验收。TASK-015 与总体能力门槛仍为 IN_PROGRESS / UNVERIFIED。
- Ruff、格式、契约及文档检查通过；无 YTA 业务、金融、数据库或 BFF/Web 修改，未重复其全量套件。TASK-014 同步为 IN_PROGRESS 并注明剩余隔离要求。独立演练容器已移除，保留私有卷与原请求身份供复查。

## 后续增量：pi 真实模型凭证跨 Profile 正反例

- 新增独立 hermes-secret-probe 项目与卷，固定 Hermes 镜像、非 root、只读、无公开端口，允许真实模型出网。只把用户已选择的 pi 路由通过 stdin 私有预置到该卷，不挂载整个 pi 目录。启动脚本仅给 default Profile 的 .env/config 注入模型 key 与鉴权头；alpha-orchestrator 使用同一模型和接口但没有这些凭证，两个 API token 独立。
- default 的真实 deepseek-v4-pro Run 精确返回固定校验文本，报告 input=1382 / output=32；alpha-orchestrator Run 进入 failed，诊断为供应商 HTTP 401，报告 input/output/total 均 0。反例确实向指定供应商发出了被拒请求，不是本地完全阻断。首次脚本要求失败 output 为空而误判；改为核验明确 401、零报告用量且无校验回答后，仅复查原身份，没有重发模型研究。
- 额外运行固定代码的作用域检查：即使探测进程显式带有真实 OPENAI_API_KEY，default 读取自己的 key 和 headers，alpha-orchestrator 均读取不到；未绑定作用域时抛 UnscopedSecretError。6 个线程交错 48 次没有串用，两个 Profile 的实际已解析工具定义均为空。此组件检查未调用模型。
- 重建容器并复用原卷后，同键复查仍返回原两份 Run 和相同结果。密钥未出现在容器日志或 Docker 配置元数据中；脱敏用量、正反例、作用域结果及脚本/镜像证据存入 runtime-observation.json。未修改 pi 原配置、应用数据库或金融事实。
- 这验证逻辑 secret/config 作用域，不是操作系统级隔离：两个 Profile 共享 UID 和文件系统。任意终端/文件工具、Skill、完整工具派发限制、上下文轮换及 staging 仍待验证，不能因此放开相关工具或宣布总体接入门槛通过。
- 固定运行时正反例与 48 次作用域检查通过；Ruff、格式、文档及契约检查通过。没有 YTA 业务或持久化修改，未重复原全量套件。演练容器已移除，保留独立私有卷和两份原请求收据；总体能力继续 UNVERIFIED。

## 后续增量：固定 Hermes 实际执行循环拒绝越权工具

- 新增 probe_hermes_tool_execution.py，在锁定镜像、network none、UID 10000、只读根和临时目录中运行未修改的 AIAgent。回环合成模型故意在同一批响应中混入一个允许的 todo_list 和六个未授权工具；该反例需要确定性注入，不计作 pi 真实模型调用。
- 实际循环仅公布并执行 todo_list，正例确实返回临时任务内容。terminal、write_file、read_file、execute_code、delegate_task、tool_call 均收到明确拒绝；执行开始/结束回调只有 todo_list，临时副作用标记不存在。验证覆盖实际模型响应到工具派发的路径，不只是 schema 列表检查。
- 外部模型请求为 0，没有挂载 pi 凭证、访问业务数据库或修改上游镜像。一次性容器已自动移除；镜像摘要、探测脚本 SHA 和脱敏结果保存于 runtime-observation.json。
- 本证据限定直接工具模式的实际 Agent 循环，不代替 Gateway 生产 Profile、默认工具搜索桥、Skill 或操作系统隔离验收。总体能力保持 UNVERIFIED，真实 Adapter 仍受前置验收约束。
- Ruff、102 个文件格式检查、120 任务文档检查及契约一致性检查通过。本轮未改业务或持久化逻辑，未重复全量 Python、TS 和浏览器套件。

## 后续增量：固定 Hermes SSE 丢失窗口与溢出边界

- 新增 probe_hermes_event_window.py，在固定镜像、network none、非 root、只读根下运行真实 APIServerAdapter、SSE HTTP 处理器和 _RunStream，并核验上游模块 SHA。事件由测试预置，不经模型或 POST Run；此范围是实际 HTTP 事件处理器集成测试，不冒称完整 Gateway 模型压力测试。
- 预置 1100 条 delta、完成事件和结束哨兵，默认 1000 条缓存实际保留序号 102–1101，其中哨兵不作为数据事件输出。7 组 HTTP 游标验证通过：无游标、旧游标及非法游标返回 replay.truncated 和剩余 999 条事件；边界 101 无截断；1099 只返回完成事件；1100 和未来 9999 均无数据并关闭。匿名访问 401，连接关闭后订阅者释放。
- 截断通知本身没有 seq，不能当成普通有序消息丢弃。未来游标不会报错而会静默返回空流，因此未来 Adapter 必须保存本地已消费的上游游标，禁止使用浏览器任意游标直接驱动上游恢复；空流不能证明事件齐全或请求未执行。断窗后应核对持久状态并明确提示历史缺失，不伪造工具结果、不重新提交推理。
- 另在实际 _RunStream 中填满默认 256 条订阅队列：第 257 条触发移除慢订阅者并留下溢出哨兵；从已消费序号 0 重新 attach 可完整取得 1–256。此项是组件级受控队列验证，不宣称验证了 HTTP 写入超时或真实网络拥塞。
- 两项范围的脱敏结果、镜像及脚本 SHA 已写入 runtime-observation.json；无外部模型调用、业务数据库或金融事实变化，一次性容器自动移除。完整部署、工具中取消及生产恢复处理仍未验收，总体状态继续 UNVERIFIED。
- Ruff、103 个文件格式检查、120 任务文档依赖检查和契约一致性检查通过；同步修正 TASK-015 中仍称真实模型未验证的过时证据描述。本轮没有业务修改，未重复全量 Python、TS 或浏览器测试。

## 后续增量：pi 真实模型在 MCP 工具执行中取消

- 新增独立 compose.hermes-tool-cancel-probe.yaml、专用启动脚本、可控制完成屏障的诊断 MCP 工具及提交/同键核验脚本。继续使用用户选择的 pi deepseek-v4-pro 路由，固定 Hermes 与 tools 镜像；两个容器均非 root、只读、无公开端口，工具只有 internal 网络。模型凭证仅经 stdin 预置于独立私有卷，不挂载 pi 目录、不改原配置。
- 提交前以 0600 独占保存请求身份；真实模型发起一次 delayed_diagnostic 调用，工具服务确认 started=1、finished=cancelled=timed_out=0 后才发送 stop。错误认证 401，有效 stop 200。等待 2 秒后，Run 已是 cancelled，但工具仍保持 started=1、finished=cancelled=timed_out=0；随后主动解除诊断屏障，工具实际完成，finished=1。
- 同一 Run 的 SSE 为 tool.started → tool.completed(error=true, duration=0) → run.cancelled，工具调用 ID 一致。这里的 tool.completed 表示 Hermes 收到中断后的错误结束，不能解释为远端工具实际成功或已停止；下游确实在 Run 取消后继续完成。本测试没有金融写入或用户数据副作用，不能推出金融操作已经回滚。
- 重复 stop 与同键复查均成功且仍为原 Run，工具调用次数保持 1。补充严格事件相关性核验后只复查原 Run，没有再请求模型生成；初次实测和后续脚本版本分别记录哈希，见 runtime-observation.json。
- 新增 8 项防误报测试，拒绝错配/缺失调用 ID、无错误标志或误报成功、错误工具、重复序号和完成终态；8 项通过。密钥扫描确认导入 key 不在容器日志或 Docker 配置元数据中。诊断不改变生产工具白名单，真实 Adapter 仍需保留工具结果与取消的区别；完整接入门槛保持 UNVERIFIED。
- Hermes / pi 专项共 59 项通过；Ruff、107 个文件格式检查、120 任务文档依赖检查及契约一致性检查通过。没有业务或数据库修改，未重复全量 Python、TS 与浏览器套件。独立演练容器和网络已移除，保留私有卷及原请求收据。

## 后续增量：BFF 持久研究请求配额

- 在实际 ResearchRepository.submit 事务增加每个已认证 owner、每个 data_mode 的滚动 24 小时新 Run 配额；配置 YTA_RESEARCH_RUNS_PER_24H 默认 50，允许 1–10000。BFF 启动传入配置，所有实例须保持一致；SQL 使用数据库 statement_timestamp 和 owner/mode 事务锁，跨会话/连接池串行检查后才追加 Run 与用户消息，无新迁移或事实 UPDATE/DELETE。
- 幂等重放与冲突检查先于额度检查：同键同正文在额度耗尽后仍返回原 Run，同键异参仍 409。取消、失败、未知执行及尚未启动的已接受 Run 都计数，不退款；历史记录自然移出时间窗口且保留原事实。不同 owner 与 REPLAY/LIVE_RESEARCH 分开计数，数据库故障不回退到进程内额度。
- 超额返回 429/RESEARCH_QUOTA_EXCEEDED、retryable=false，且不创建 Run/消息、不派发 driver。浏览器增加中文提示，已有研究可继续读取；README、示例环境变量与生成 BFF OpenAPI 同步说明。它是请求次数上限，不代表 token、辅助模型调用或货币费用封顶；真实 Hermes transport 仍未启用。
- 新增 5 项测试，其中 4 项实际使用专用 yta_test 和受限 yta_app：两个池同时对 12 个会话提交，上限 3 时恰好接受 3；新池保留限制及同键身份；失败/取消/未知不退款；owner/mode 隔离；25 小时旧记录释放额度且不删历史；错误归属不收费；实际 HTTP 429 不触发第二次 driver。另验证无效/无限/小数配置拒绝。测试产生的合成 Run 追加终态收尾，保留历史。
- 首轮全量回归发现原有 HTTP 集成测试启动后台全库扫描，会消费并行测试的待运行数据并使自身用例排队超时。该 HTTP 测试改为只自动派发自身请求，恢复行为仍由单独用例验证；新增配额测试只对自身随机 owner 的数据追加收尾事件，没有删除或改写其他测试事实。
- 修正后 pnpm test 共 65 项通过（contracts 16、BFF 49），实际 PostgreSQL 用例无跳过；pnpm typecheck、pnpm build、Ruff、文档和 Alpha 契约检查通过。本轮未修改 Python 金融计算或数据库模式，未重复 Python 全量与浏览器 E2E。TASK-013 保持 IN_PROGRESS，模型用量/费用预算、staging 与真实行情资格仍待完成。

## 后续增量：超额拒绝后的浏览器恢复与独立端口 E2E

- 实际发现研究发送界面将明确的 429/RESEARCH_QUOTA_EXCEEDED 当作响应丢失处理，锁住问题输入及会话切换。现仅在这一明确未创建 Run 的响应下清除待核对身份，保留文本和中文额度提示，允许编辑/读取历史；不会自动重发。网络中断等不确定结果继续保留原请求键，避免重复执行。
- 新增浏览器用例：通过网络拦截提供明确额度拒绝，验证输入保留且可编辑、会话新建可用、无虚构用户消息和自动重发；随后显式点击发送才以新身份创建一次真实 REPLAY Run。此拒绝分支是浏览器 HTTP fixture；实际 BFF/数据库超额无执行证据来自上一轮 PostgreSQL/HTTP 集成测试。既有响应丢失用例继续验证原键重试不重复冻结。
- 新增 pnpm test:e2e:isolated：独立 13000/13100/18100 端口、.next-e2e 构建目录和 TypeScript 配置；测试前后保留共享 next-env.d.ts，使用原有专用 _test 数据库和随机测试用户。原 3000/3100/8100 的三个开发进程 PID 前后相同，无需停止开发服务。
- 首次浏览器用例因 Next 路由播报器也使用 role=alert 而定位歧义，已限定页面 main 区域；全量首次运行另发现两个旧测试把 CSRF Origin 写死为 3000，已改为当前页面 origin，没有放宽服务端 CSRF 检查。随后完整 17 个 Chromium 用例通过，涵盖新额度分支、原键核对、冻结/结算、分页及移动布局；独立 Web 生产构建成功。
- 已查看 research-quota.png，额度提示、保留的问题文本和可用导航显示正确。TypeScript、Ruff、文档及契约检查通过。没有 BFF/数据库或 Python 金融实现变化，未重复上一轮 65 项 TypeScript 单元/数据库套件及 Python 全量。独立服务测试后已关闭；真实模型费用预算、完整接入、行情资格与 staging 仍待验收。

## 后续增量：最新应用镜像与真实容器配额验收

- 修正 .dockerignore，排除独立浏览器构建目录 .next-e2e；Compose 本地容器增加显式可配置 YTA_RESEARCH_RUNS_PER_24H。重新构建 Alpha runtime、BFF 和 Web standalone 镜像，构建中实际执行 TypeScript 与 Next 生产打包，未替换正常宿主机开发服务。
- 专用 app-local REPLAY 容器栈以额度 2 运行，三个服务非 root、只读、健康；现有 check_container_stack.py 验证 Web 无数据库/服务凭证及私有后端 DNS、无内部公开端口、代理 readiness、匿名 401、登录静态资源、Swagger 鉴权和 Worker 只读队列通过。
- 新增 check_container_quota.mjs，从已打包 BFF 内部创建独立随机测试账户并发起实际登录/研究 HTTP：2 次接受，第三次 429/RESEARCH_QUOTA_EXCEEDED，retryable=false；同键重试仍为原 Run，数据库恰好 2 条 Run / 2 条用户消息，镜像内 OpenAPI 有 429 契约。测试仅访问 yta_test 与合成 REPLAY，不请求外部模型或行情，最后停止自身接受的 Run，保留事实。
- 实测 Web 镜像不存在 .next-e2e、.local 和 .env。容器重建后同一批镜像再次全部健康，镜像摘要、arm64、源码/检查脚本 SHA 和具体检查范围保存于 infra/docker/app-local-observation.json。一次本机元数据记录命令误用了系统 Python 3.9 的 datetime.UTC，改由项目 uv 环境运行并在重建后的实际容器上重新检查，没有把失败记录当作通过。
- CI container-smoke 配置额度 2 并增加同一镜像内配额检查；Workflow YAML、Ruff、文档及契约检查通过，远端 CI 尚未运行。本轮没有业务/数据库模式变化，未重复前轮 65 项 TS 套件、17 项浏览器和 Python 全量；测试容器及 edge 网络已清理，保留数据库和镜像。
- 这仍是本机打包与容器证据，云端 staging、HTTPS/正式凭证、完整 Hermes 接入、行情授权和费用预算尚未完成。发布任务继续 IN_PROGRESS / TODO，不将容器健康等同正式可用。

## 后续增量：跨来源账号登录限流

- Auth 在账户查询及密码验证前，按登录契约规范化后的邮箱领取账号额度；原来源限制继续生效。新增分域 SHA-256 邮箱哈希和每 60 秒 10 次窗口，使用数据库时钟及两个按固定顺序获取的事务锁；跨实例、来源变化和邮箱大小写不绕过额度。账号存在与否均执行相同策略。
- 新增迁移 011_login_account_attempts.sql，独立账号尝试表/哈希时间索引，不复用语义为 peer 的列。仅保存哈希与时间，运行账号 SELECT/INSERT、identity USAGE/SELECT；不可变触发器禁止 UPDATE/DELETE/TRUNCATE。账号和来源份额一起提交，任一耗尽会回滚另一份，不把拒绝请求计入或延长窗口。错误仍为统一 429/RATE_LIMITED，不公开哪个额度耗尽。
- 迁移仅应用于专用 yta_test，重复迁移为空；真实受限 yta_app 验证无 UPDATE/DELETE 权限，迁移 owner 的 UPDATE/DELETE/TRUNCATE 也实测被触发器拒绝（55000），未修改或删除历史。正常 yta 数据库及运行服务未变；部署新 BFF 前须先迁移和授权，缺表时不绕过限制。
- 新增 4 项实际 PostgreSQL/HTTP 用例：24 个不同来源、两个池、大小写交错只放行 10；重建池仍拒绝；61 秒历史自然过期且保留；互相拒绝时份额原子回滚；实际登录接口对真实/未知账号在不同来源下前 10 次均 401、之后均 429，无 Cookie 和新会话。原成功/失败混合、伪造转发头和来源限流用例继续通过。
- pnpm typecheck、pnpm build、pnpm test 共 69 项通过（contracts 16 / BFF 53）；真实专用 PostgreSQL 下 uv run pytest -q --require-postgres 共 304 项通过，无数据库跳过，原 4 条 pandas 弃用提示不变。完整独立端口浏览器 17 项通过；Ruff、107 文件格式、文档与契约检查通过。
- README 更新升级依赖和限流语义。前轮应用镜像尚不包含此次账号限流/011 迁移，未将旧镜像证据冒称为本轮部署；云端账号管理、代理配置、全站限流、安全审查及真实接入仍待完成。

## 后续增量：BFF 升级就绪门槛与权限反例

- 修复新账号限流表尚未纳入 BFF readiness 的遗漏：现在 login_account_attempts 的存在性、SELECT/INSERT 且禁止 UPDATE/DELETE/TRUNCATE 的运行权限、启用的 immutable_fact 触发器都参与判断。序列检查由一条扩展为两类登录尝试、登录凭证、研究消息共四条，分别要求存在及 USAGE/SELECT。
- 新增 check_bff_readiness.py，实际编译 BFF 后由迁移 owner 创建随机 yta_ready_*_test 数据库。先仅迁移 001–010，检查返回 schema_missing；补 011 但不授权返回 permissions_invalid；只授权表仍因序列不足被拒；表/序列完整授权后 ready。另撤销研究消息序列 USAGE、禁用账号表触发器，分别返回 permissions_invalid / immutable_guards_missing；恢复后 ready；owner 身份仍被拒绝当作运行角色。
- 上述 8 个场景在真实 PostgreSQL 与实际编译的 databaseStatus 中通过，没有 mock SQL 响应，没有修改正常 yta 或共享 yta_test 的结构/权限。临时数据库 finally 清理，随后独立查询确认剩余 0。此验证覆盖数据库就绪组件；公开 /ready 的依赖失败与 /health 分离由既有 HTTP 回归覆盖。
- CI 在 pnpm build 后加入同一验证，Workflow YAML 本地解析通过，远端执行尚待。README 补充升级检查分类与复现命令。pnpm typecheck、BFF 编译、pnpm test 共 69 项通过；Ruff、108 文件格式、文档与契约检查通过。
- 本轮无数据库迁移/金融/Web 行为变化，未重复前轮 304 项 Python 与 17 项浏览器套件。前轮应用镜像仍未包含账号限流及本次 readiness 更新，未宣布部署或发布验收完成。

## 后续增量：账号限流与就绪检查的镜像升级验证

- 重建迁移镜像及应用 Compose 镜像，使已验证的账号限流/新 readiness 进入实际打包产物。check_migration_container.py 在独立随机 _test 新库验证全量 001–011；另在仅有 001–010 的库先追加一条审计证据，再由新镜像仅应用 011，原证据保留，重复迁移无新操作。未提供 owner 凭证及使用 runtime 角色均拒绝迁移。
- 迁移演练的两个随机数据库均在 finally 删除，独立查询确认残留 0；没有升级正常 yta 库或替换宿主机服务。应用演练继续使用已迁移的专用 yta_test，三个实际容器健康，非 root/只读/私网边界与静态资源/Swagger/readiness/Worker 检查通过。
- 扩展镜像内 check_container_quota.mjs：导入打包后的 loginAccountHash/databaseStatus，实际登录后查询新表，恰好保存 1 份账号尝试；数据库就绪为 ready。随后两个 Run 接受、第三个 429、原键重放、2 条用户消息及打包 OpenAPI 检查均通过；没有外部模型/行情请求，停止自身已接受 Run 后保留合成事实。
- 最新镜像摘要、源码及检查脚本 SHA、迁移/登录/配额结果写入 infra/docker/app-local-observation.json，并完整保留前次观察，不将旧镜像验证改写为新结果。确认 Web 无 .next-e2e/.local/.env 文件。CI 原容器步骤将自动执行扩展检查，远端执行仍待验收。
- 构建实际执行 BFF TypeScript 和 Web Next 生产打包；Ruff、文档与契约检查通过。本轮无业务/数据库模式变更，未重复前轮 69 项 TS、304 项 Python 和 17 项浏览器套件。应用演练容器/edge 网络清理，保留数据库与镜像。正式部署、真实行情和完整 Hermes 接入仍未完成。

## 后续增量：发布输入版本清单

- 新增只读 release_inventory.py，汇总应用/迁移/Hermes 五个镜像摘要、11 份迁移文件哈希、完整 Hermes 门槛状态与既有容器观察记录的源码哈希。可选执行本地 docker image inspect，绝不 pull/build/run；不连接目标数据库、不读取 .env 或 pi 私有配置、不自动改写原验证证据。
- 明确区分 input_consistency 与 release_qualification：源码改动/缺失、迁移目录落后或镜像缺失返回非零；即使全部一致，发布资格也始终 NOT_ASSESSED。检查范围仅为已记录文件，不声称证明完整镜像来源、所有构建输入、目标架构或实际部署。模型身份、真实行情、目标数据库、staging、远端 CI/Review 等仍须各自验收。
- 新增 10 项测试，覆盖一致但未获得发布资格、源码修改/丢失、新迁移导致过期、禁止可变镜像 tag、越界证据路径、无效哈希/空记录/缺失门槛，以及缺失镜像只 inspect 不拉取/执行。10 项通过。
- 在实际工作区执行 --inspect-local-images，五个已记录镜像均存在，当前 20 项输入检查一致，迁移目录 11 份；报告输出 .local/release-inventory.json。Hermes 总体仍 UNVERIFIED，数据库实际版本 NOT_QUERIED，没有将历史局部测试提升为当前发布通过。
- TASK-112 更新为 IN_PROGRESS，反映已有本地 CI/镜像/迁移准备和输入清单；真实 staging、回滚和远端验收仍缺失。README 记录命令、退出码及边界。本轮未修改业务、持久化或应用镜像，未重复前轮完整应用/数据库/浏览器套件。
- Ruff、110 文件格式、120 任务文档依赖及契约一致性检查通过。

## 后续增量：发布输入的目标数据库只读核对

- release_inventory.py 新增可选 --database-env，以明确指定的环境变量取得连接；未指定保持 NOT_QUERIED，指定但未设置则非零失败。连接设置默认只读事务、连接/语句/锁超时，并实际确认 transaction_read_only=on；仅读取 public.yta_migrations，不执行 DDL、修复记录或赋权。
- 比较当前目录和已应用记录，分别报告 consistent、pending、conflict；冲突区分校验和变化与当前发布目录缺少已应用迁移。无注册表、无读取权限、元数据异常及连接失败都不能通过输入一致性检查；异常和报告不包含 DSN、凭证或原始连接诊断。
- 新增 6 项测试，其中 5 项实际 PostgreSQL：当前 11 份迁移完全一致且读取前后记录不变；新增待执行目录项、遗漏已应用项及变更校验和均拒绝；受限运行角色没有迁移元数据权限时明确返回 metadata_permission_missing。另验证连接失败脱敏且输入状态非通过。初次单元/集成测试文件同名导致 pytest 收集冲突，已改为唯一文件名后重新验证，没有将未收集用例计为通过。
- 在真实专用 yta_test 上执行清单 CLI，五个本地镜像、源码记录和数据库共 21 项输入检查一致，数据库 applied_count=11、只读、无待执行或冲突；报告仍 NOT_ASSESSED。另实际验证显式缺失环境变量返回失败，未静默跳过数据库。
- 专项 16 项通过；真实 PostgreSQL 下完整 uv run pytest -q --require-postgres 共 320 项通过，无数据库跳过，原 4 条 pandas 弃用提示不变。Ruff、111 文件格式、文档与契约检查通过。本轮无业务、数据库模式或应用镜像变化，未重复 TS/浏览器套件。
- README 明确注册表一致不能证明实际表结构、运行权限或发布资格，必须结合 readiness、真实接入及 staging 验收。未访问生产数据库、修改正常 yta 或执行发布。

## 后续增量：BFF 到 Alpha 的可选 OTLP 链路

- 新增手动 OpenTelemetry 埋点和显式 YTA_OTLP_TRACES_URL 开关，默认关闭；BFF HTTP 根跨度、AlphaClient 子跨度和 Alpha HTTP 服务跨度包含固定路由、状态及服务生成的 request_id。公开入口忽略客户端 traceparent；Alpha 仅从有效内部服务凭证的请求接收 traceparent，不接受 baggage/tracestate。不记录正文、Cookie、Authorization、查询值或异常正文。
- 使用有界批处理队列和 3 秒导出超时；BFF/Alpha 关闭时调用 provider shutdown。锁定 JS API 1.10.0、SDK 2.12.0、HTTP exporter 0.300.0，Python SDK/HTTP exporter 1.45.1。配置拒绝带凭证、查询、fragment 或错误路径的采集地址。
- 现有并发观测测试增强为真实 SDK 内存导出验证，覆盖 4 个隔离根跨度、正确 Alpha 子跨度、trace 日志关联、敏感字段排除和 SSE 中断错误状态。新增 7 项 Python 测试使用实际 HTTP/protobuf 接收端，验证认证父上下文、未认证根跨度、未知路由、关闭刷新及配置反例。
- check_tracing.py 实际启动随机本地端口的 Alpha/Uvicorn、TypeScript BFF 传输探针和 OTLP 接收端，验证同一 trace 下 BFF server→Alpha client→Alpha server 三个跨度、父子 ID 和敏感字段缺失。初次接收端未处理 JS chunked HTTP 导致失败，补齐分块读取后通过；没有外部采集端、数据库或模型请求。CI 已加入命令，远端执行未验收。
- pnpm typecheck、pnpm build、Ruff/114 文件格式、文档与契约检查通过。真实 PostgreSQL 全量 Python 327 项通过，无跳过（4 条既有 pandas 提示）；TS 初次误用 owner 导致 readiness/权限 3 项失败，改用规定 runtime 角色后 70 项全部通过。隔离浏览器 17 项全部通过，宿主正常开发服务未被替换。
- 本轮未重建应用镜像，既有镜像观察不能证明包含本次依赖和追踪代码。TASK-114 仍 IN_PROGRESS；Hermes/工具跨度、采集端故障专项、集中采集与告警、生产留存和 staging/Review 仍待完成。真实模型继续使用此前验证的 pi agent 配置，真实行情授权和完整 Hermes 能力门槛仍未完成。

## 后续增量：采集端故障隔离与退出回归

- check_tracing.py 增加 reject（503）、stall（接收后延迟 5 秒）、refused（无监听端口）三种实际 HTTP 故障模式。BFF 探针断言健康请求成功且耗时小于 3 秒；脚本检查 Node 正常退出、Alpha 在 10 秒 join 期限内结束以及输出无测试私密标记。503/延迟场景确认两个 service.name 的 OTLP 载荷都到达故障采集端，避免仅验证未启用追踪的空路径。
- 初次 503 演练复现：健康请求已经成功，但 provider.shutdown 的导出拒绝导致 BFF 探针非零退出。修复 startTracing 返回的关闭函数，捕获刷新错误并只输出固定 bff.tracing_flush_failed 事件，不记录上游异常/响应正文；业务服务的其他关闭错误仍由原 main 路径处理。
- 修复后成功、503、延迟、拒绝连接四种跨语言探针均通过；Python exporter 在故障场景仍输出自身失败/重试诊断，没有伪报导出成功。新增三种故障命令进入 CI，远端执行未验收。README 和 TASK-114 当前证据同步更新，任务仍 IN_PROGRESS。
- pnpm typecheck、BFF 编译、真实 runtime PostgreSQL 下 pnpm test 70 项、Python tracing 专项 7 项、Ruff/114 文件格式、文档与契约检查通过。无 Python 业务/数据库/Web 变更，未重复前轮完整 327 项 Python 与 17 项浏览器套件；应用镜像尚未重建。本次验证范围是健康请求与追踪传输，不是生产负载、持久化事务或完整工具故障追踪验收。

## 后续增量：金融工具到 Alpha 的可选追踪

- 金融工具 Settings 新增 YTA_TOOLS_OTLP_TRACES_URL / YTA_TOOLS_ENV，默认禁用并校验采集端地址。共享 Python 的有界 provider 构造，resource 使用 yta-finance-tools；provider 在 MCP lifespan 内创建并在线程中关闭，未注册全局 provider，不改变 Alpha/其他组件的实例生命周期。
- 为工具调用添加固定名称的 SERVER 跨度和 Alpha CLIENT 子跨度；未知名字归一为 UNKNOWN_TOOL，服务生成 request_id。只传播规范 traceparent，并记录 Alpha 返回的有效 request_id；拒绝记录参数、客户端 metadata、工具原始未知名字、凭证和异常正文。禁用该 server 默认 MCP OTel middleware，使用显式字段埋点，避免另一个全局 provider 意外采集模型输入。现有鉴权/只读/模式/响应限额保持原行为。
- 新增 8 项专项：实际 MCP Client 调用与 Alpha ASGI 应用、实际 OTLP/protobuf 接收端验证三个跨度和 request_id；未知工具/参数拒绝、上游 503 固定错误码及敏感内容排除；取消经 MCP 传播至挂起 HTTP 调用后两跨度均结束并标记错误；私有 Streamable HTTP 的 lifespan 刷新、未认证拒绝、已认证调用及外部 traceparent 不采纳；4 个采集配置反例。未使用真实 Hermes Run，Alpha 网络传输在上述专项中为 ASGI/MockTransport，不能升级为云端或 Hermes 链路验收。
- 真实 owner PostgreSQL 下全量 uv run pytest -q --require-postgres 334 项通过，无数据库跳过；随后补充私有 HTTP 专项，完整工具追踪专项 8 项通过。BFF→Alpha 成功/503/延迟/连接拒绝四种实际网络探针在共享 provider 提取后全部通过。Python exporter 在一次重试期限边界打印 timeout=0 拒绝诊断，但业务响应与服务退出断言通过，不将失败导出记作采集成功。
- Ruff、115 文件格式、文档与契约检查通过。没有 TS/Web/数据库模式变化，未重复前轮 70 项 TS 与 17 项浏览器套件，未重建镜像。TASK-114 仍 IN_PROGRESS；Hermes Run→工具关联、集中监控告警、实际部署/留存与 Review 仍未验收。真实模型继续采用用户指定的 pi agent 配置。

## 后续增量：固定 Hermes 的 Profile 上下文读取隔离

- 回到真实接入门槛中的文件/技能上下文证据缺口，新增 check_hermes_context_scope.py，在既有锁定镜像摘要中以 uid 10000、只读根文件系统、临时 tmpfs 和 network none 执行；只使用合成 SOUL/Skill 文件，无凭证、外部请求或模型调用。
- 通过固定源码的 _profile_runtime_scope、load_soul_md、build_skills_system_prompt 和 get_memory_dir，8 线程交错 64 次读取各自内容及共享缓存，检查另一 Profile 标记缺失；嵌套进入另一 scope 后原 scope 恢复。另在没有调用者 ContextVar 的 2 个线程中使用显式 home/skills 路径，均读取目标 Profile。最终脚本完整执行通过并保存 SHA、镜像、时间和边界到 runtime-observation.json，未覆盖旧证据。
- 同时实际确认共享 UID 能直接读取另一 Profile 的 SOUL 文件。这不是泄漏工具调用，也不是 OS 文件访问隔离；该结果限制了可以作出的安全声明。内存只验证目录选择，不证明读写事务或历史数据隔离。完整 Gateway 运行、全部工具/文件访问策略、云端部署和回滚仍需独立验收。
- README 提供可复现命令。总体 integration_status 与六项门槛仍 UNVERIFIED，TASK-007/014/015 保持 IN_PROGRESS，真实 Adapter 未启用。此次没有业务、数据库、依赖或应用镜像变更，未重复应用套件；Ruff、格式与文档检查作为本轮脚本检查。

## 后续增量：本地 readiness 监控与告警规则

- 新增可叠加的 monitoring-local Compose、Prometheus/Blackbox 配置与固定 manifest 摘要。Blackbox 无宿主端口、Docker socket 或服务凭证，只探测 Web/BFF readiness；Prometheus 面板限定宿主回环 39090。指标保留 7 天/512MB，监控日志 10MB×3；开发 PostgreSQL 补同样轮转配置但未重建正在运行的数据库。
- 三条规则区分持续 2 分钟 probe_success=0、持续 1 分钟 up=0、固定 Web/BFF 的 probe_success 序列缺失。没有配置 Alertmanager/通知接收器，没有发送任何外部消息。现有 BFF readiness 汇总 DB/Alpha/研究模式；不冒充 Hermes 独立探针或真实研究已验收。
- check_monitoring.py 使用实际 promtool 验证配置及 4 组时序场景（等待阈值、触发、恢复、采集失败、缺失及健康反例），再用真实 Blackbox 向临时合成 HTTP 服务探测：ready=1、200/not_ready=0、503=0、302=0。结果、脚本/配置哈希及镜像摘要写入 infra/monitoring/local-observation.json。首次 promtool 只读容器缺少临时写入目录，补 /tmp tmpfs；本机仅 internal 网络未提供宿主发布端口，改为普通隔离 bridge 后实际测试通过。
- 完整检查器最后一次运行通过；临时容器/网络清理，正常开发服务/数据库未变更。Ruff/117 文件格式、Compose 合并校验通过。CI container-smoke 加入固定镜像拉取与同一检查，远端尚未执行。本次不改业务/持久化代码，未重复应用套件。
- 本机只验证 arm64，Prometheus 实际持续运行的告警生命周期、通知渠道、Hermes 专项、云端 staging/故障演练和生产容量仍待验收；TASK-020/114 保持 IN_PROGRESS，整体 goal 未完成。

## 后续增量：实际 Prometheus 告警触发与恢复

- 新增 check_monitoring_lifecycle.py，在随机独立 Docker 网络启动固定摘要的真实 Prometheus/Blackbox 与仅含 Python HTTP 服务的合成目标。挂载原始 prometheus.yaml、blackbox.yaml、yta-alerts.yaml，保留 15 秒采集/评估和 2 分钟 for，不用缩短阈值替代实际持续时间。
- 真实 HTTP 查询 Prometheus 指标确认两个目标均健康且无 YTA 告警后，将合成服务切换为 503；观察两个 YTAServiceNotReady 进入 pending，再进入 firing；恢复目标后两个 probe_success=1 且 YTA 告警全部消失。阶段耗时为启动后 15.85 秒健康、33.29 秒 pending、152.96 秒 firing、182.57 秒恢复；pending→firing 119.67 秒。全过程未配置通知接收器、未发送消息。
- 完整脚本退出 0，证据追加至 infra/monitoring/local-observation.json 的 alert_lifecycle，保留前次规则/HTTP 探针观察。容器和网络 finally 清理；本轮未操作正常 BFF、Alpha 或 PostgreSQL，也不声称已验证其真实停机情形。
- CI container-smoke 加入同一生命周期检查，README 提供约 3 分钟的复现命令。Ruff/118 文件格式及文档检查通过。本轮仅新增运维验证，无业务/数据库模式变化，未重复应用套件。
- 本地真实 Prometheus 的告警生命周期现在有直接证据；Alertmanager/通知、真实依赖故障、Hermes 独立监控及 staging 演练仍未验收，TASK-020/114 保持 IN_PROGRESS。

## 后续增量：追踪代码打包与实际 Alpha 中断告警

- 重建 Alpha/BFF/Web 应用镜像，包含新的 OTel 依赖、请求埋点和 BFF 安全刷新代码；实际启动于 app-local 的 test/REPLAY/专用 yta_test。三服务健康；镜像内检查通过私网、非 root、只读、静态资源、鉴权、ready、Worker，以及账号限流记录、2 次接受/第三次 429、同键原 Run 和打包 OpenAPI。BFF 打包 tracing 模块实际导入成功。未启用容器内 OTLP 导出，不将主机探针证据提升为镜像内导出验证；finance-tools 和 migrator 镜像本轮未重建。
- 新增 check_dependency_outage.py，严格核对 app-local 项目身份和 BFF test/REPLAY/_test 后，只停止该测试栈的 Alpha。实际观测 BFF liveness=200、readiness=503，checks.database=ready、checks.alpha=not_ready；真实 Prometheus/Blackbox 对 BFF/Web 两个目标在 138.82 秒进入 firing。
- finally 启动原 Alpha 容器，153.50 秒时两个目标恢复 ready 且告警消失，三个应用容器重新 healthy。未停止数据库、修改正常宿主服务、调用模型/行情或发送通知。宿主 Python/Next/BFF 原 PID 仍存在。
- 新镜像摘要、脚本与源码 SHA、配额结果和故障阶段写入 app-local-observation.json，保留前次观察及旧镜像摘要。迁移记录显式标注为此前验证，本次未重建。清理测试应用/监控容器和 edge/monitoring 网络，保留数据库、镜像与监控数据卷。
- CI 加入实际依赖故障演练，并扩展 finally cleanup 覆盖监控 overlay；远端执行尚待。构建实际执行 TypeScript/Next 生产编译，Ruff/119 文件格式、文档、契约检查通过。本轮没有业务或数据库模式变化，未重复前轮应用单元/浏览器套件。
- 本次证明本地 REPLAY 下真实 Alpha 中断的检测、告警和恢复；不证明 PostgreSQL/Hermes 故障、外部通知、真实行情或 staging 验收。相关任务和整体 goal 继续进行。

## 后续增量：冻结事务提交边界的真实断连验证

- 现有冻结/幂等测试补两项实际 PostgreSQL 故障。提交前：冻结语句执行后、连接 context 提交前，仅对本次 runtime 连接的 backend PID 调用 pg_terminate_backend，额外约束当前测试库与 yta_app 用户；调用抛 OperationalError，随后确认无预测、冻结审计及幂等记录，原键正常重试产生一份 FROZEN 事实。
- 提交后：使用只允许 loopback/_test 的 TCP relay 传输真实 PostgreSQL 协议，接收到服务器实际 CommandComplete(COMMIT) 后截断客户端连接、丢弃回包。冻结调用实际收到 OperationalError；独立数据库连接确认事实已提交。草稿过期 100 天后用原键重试，仍返回原预测，事件、预测、审计、幂等记录各保持单份。未使用模拟数据库返回或人为插入“已提交”结果。
- 测试只影响自己创建的合成 REPLAY 用户/连接；未停止共享 PostgreSQL、修改表权限/约束或更改金融实现。relay 不打印或保存协议内容，测试结束关闭监听/socket/thread。该证据覆盖冻结事务边界，不扩大为 settlement/研究派发或整台数据库重启验收。
- 两项专项通过；增强审计计数断言后，真实专用 PostgreSQL 下全量 uv run pytest -q --require-postgres 337 项通过，无数据库跳过，原 4 条 pandas 提示不变。Ruff/119 文件格式、文档与契约检查通过；既有 CI PostgreSQL 套件自动包含新测试，远端运行仍待。
- README 记录复现命令及作用域。无运行时代码、镜像或数据库迁移变化，未重复 TS/浏览器/镜像套件。任务完整 DoD 与整体 goal 仍未完成。

## 后续增量：结算事务与 Worker 的提交回包丢失

- 在真实 PostgreSQL 套件新增两项结算边界验证。提交前只终止本次结算 runtime backend：调用抛 OperationalError，预测完整内容与冻结时相同，无结果/结算审计；用同一观察输入重试后得到唯一 version=1，冻结 forecast 保持不变。
- 复用真实 TCP relay 丢弃服务器 COMMIT 回包，只将 Worker 的结算事务导向该 relay，lease 和后续核对连接正常。Worker 实际捕获连接错误后重新读取当前结果，返回 SETTLED、error_code=null、next_attempt_at=null；持久化仅一份 SETTLED attempt、一份结果、一份结算审计及 FROZEN/SETTLED 两事件。再次结算相同输入返回原结果，重建 Worker 后 tick 不再调用 provider。
- 两项专项通过，全量真实专用 PostgreSQL uv run pytest -q --require-postgres 339 项通过，无数据库跳过；4 条既有 pandas 提示不变。Ruff/119 文件格式、文档与契约检查通过。现有 CI 自动包含新用例；没有运行时代码/镜像/迁移变化，未重复 TS/浏览器套件。
- 本次证明单个结算连接断连及已提交事实的核对行为，不证明整台数据库故障时的 Worker 恢复，也不扩大为真实行情或 staging 验收。README 提供命令；任务完整 DoD 和整体 goal 仍未完成。

## 后续增量：真实行情前置门槛与 Nasdaq 日历例外

- 核对采集到研究的现状：AcquisitionStore 只保存原始证据，Alpha LIVE_RESEARCH 仍明确 DATA_UNAVAILABLE，未擅自把原始采集/质量检查提升为可冻结快照。真实供应商授权、账户数据及公司行动覆盖仍缺；已向用户请求供应商/凭证配置路径和 staging 目标，不要求发送密钥内容。
- 从 Nasdaq Trader 官方年度日历及 2024-12-30 公告取得独立日期参考：2026 年公布的休市与提前收盘，以及 2025-01-09 特殊休市。新增带来源和范围说明的 fixture 与 test_calendar_reference.py；11 个关闭日期、2 个提前收盘以及一组 horizon/latest_closed 边界，共 14 项通过。
- 修正 TradingCalendar docstring 的“verified shared schedule”过强描述，明确 XNYS proxy 和例外日期回归的限定范围。运行时算法/数据模式没有变化，没有将小范围日期核对作为完整 Nasdaq 或供应商资格通过。
- Ruff、120 文件格式、文档与契约检查通过。本轮无金融算法、持久化、TypeScript 或运行镜像变化，仅新增独立参考测试与描述纠正，未重复前轮全量 PostgreSQL/浏览器套件；既有全量 Python 339 项结果不含此次新增 14 项，不混报为已执行的 353 项。

## 后续增量：证券参考历史与标准化接口

- 推进 TASK-040：新增 012_symbols.sql，symbols 以 symbol/version 唯一追加，包含 UNVERIFIED/ACTIVE/RENAMED/DELISTED、effective_from/effective_to、successor_symbol、source_id、recorded_at；约束有效区间、更名后代码及 canonical 格式。不可变触发器拒绝 owner 修改/删除/清空，运行角色仅 SELECT/INSERT。种子 NVDA 仅来源 yta:mvp-scope，未虚填上市日期、证券历史或供应商资格。
- 新增 Alpha symbols 模块与受服务凭证保护的 GET /internal/v1/symbols/{symbol}，只读事务、参数化查询、有界历史返回；独立标准化函数接受 ASCII 大小写/首尾空白，拒绝相似 Unicode、内部空白和注入字符。公开此私有查询仍只允许 NVDA，金融工具凭证不具有权限；参考记录永远不自动提升为行情资格。现有冻结/行情 wire 值继续要求 canonical NVDA，未扩展 MVP universe。
- Alpha readiness 纳入新表、运行权限和 immutable_fact；生成的 Alpha OpenAPI 已同步。012 只应用于已有专用 yta_test，并授予 runtime SELECT/INSERT，正常 yta 未升级。
- 18 项专项覆盖标准化/非法输入、真实 PG 历史/约束、owner/runtime 不可变性、API 鉴权及范围。初次专项的 HTTP 预期误写 400（实际统一 DomainError 为 422）、savepoint 异常捕获顺序错误，修正测试后通过。合成更名/退市历史在强制回滚事务中验证，不污染持久参考数据。
- 真实 PostgreSQL 完整 Python 371 项通过，无数据库跳过（包含前轮新增的 14 项 Nasdaq 日期测试）；TS 70 项、pnpm typecheck/build、Ruff/122 文件格式、文档与契约检查通过。无前端交互变化，未重复浏览器套件。
- 重建 migrator 镜像，在随机 _test 新库验证全量 001–012；另验证 001–011 升到 012、原审计证据保留、重复执行、runtime/缺失 owner 凭证拒绝及清理。镜像/文件 SHA 和范围保存在 symbol-registry-migration-observation.json。应用/工具镜像本轮未重建，旧应用镜像不包含新接口，不提升为新版本运行验收。
- TASK-040 从 TODO 改为 IN_PROGRESS；真实供应商/证券历史、标准化接口的后续消费者整合和完整 DoD 仍待验收。外部行情授权与 staging 信息仍待用户补充，整体 goal 未完成。

## 后续增量：BFF 行情入口的证券标准化

- 将既有 stockSymbolSchema 从单一字面输入扩展为有界 ASCII NVDA 拼写规则，允许大小写与首尾空格/tab/CR/LF 后统一输出 NVDA；禁止 Unicode 相似字、内部空白、垂直制表符、路径/查询字符及其他证券。既有 bars/indicators 路由直接消费该请求契约，无新行情源或额外金融逻辑。
- 新增共享 symbol-inputs fixture，TS 与 Python 分别验证合法标准化、其他合法但不在 MVP 的代码、非法值。明确 forecastSchema 仍拒绝 lowercase symbol，未将请求归一化扩散到冻结事实或模型输出。
- 真实 PostgreSQL 支持的 BFF HTTP 集成验证两个入口：混合大小写/编码空白成功调用固定 Alpha 路径；非法/不支持代码返回 422 且 Alpha 调用数不变；未登录仍 401。Alpha 为该 BFF HTTP 套件既有 stub，未冒充真实行情验证。
- TS 全量 74 项通过（contracts 19 / BFF 55）；Python symbol 专项 17 项通过。pnpm typecheck/build、请求/BFF OpenAPI 导出与一致性检查、Alpha 契约、Ruff/122 文件格式和文档检查通过。Python 运行时未变，未重复前轮 371 项全套；不将新增 parity 用例混报为已运行的 372 项。无 UI 交互/数据库模式变化，未重复浏览器套件。
- TASK-040 继续 IN_PROGRESS；参考历史私有接口、真实供应商历史验收与完整消费者集成仍需后续证据。应用镜像未因本轮契约源码变化自动重建，发布输入核对不能据旧镜像宣告通过。

## 后续增量：结构化候选日线与原始证据同事务持久化

- 新增 013_market_bars.sql：采集哈希/日期主键，来源页/行唯一键及原始响应页外键，OHLCV 无固定 scale 的 numeric，价格/成交量有限值与 OHLC 范围约束。保存供应商聚合时间；模式、供应商、证券通过不可变采集关联，日线强制 RAW/UNVERIFIED。owner 禁止 UPDATE/DELETE/TRUNCATE，runtime 仅 SELECT/INSERT。
- AcquisitionStore.save 在同一事务写入原始证据、日线和采集审计，使用既有采集 advisory lock；精确重试核对全部投影，不将 ON CONFLICT 当作数据一致证明。新采集/修订保留新哈希及旧事实；旧采集原键重试可以补齐日线，没有自动全库回填或额外供应商调用。投影失败回滚整次采集。
- daily_bars 内部读取要求显式 data_mode，先校验原始证据再逐行比较持久化日线；缺失/错误投影明确失败。输出价格/成交量字符串、来源页哈希/行号、纽约日期、published_at=null 与 available_at=retrieved_at。不填缺日，不把聚合起点当成发布时间，不提供合格快照或改变 API/Worker 的 LIVE_RESEARCH 行为。采集预检纳入新表及权限。
- 新增 10 项实际 PG 用例，覆盖高精度/小数成交量、并发重试、修订保留、模式隔离、owner/runtime 不可变性、投影失败原子回滚、旧采集补齐、冲突检测、多页时间及缺口保留、5 类非法价格。全量 uv run pytest -q --require-postgres 382 项通过，无数据库跳过；保留 4 条既有 pandas 弃用提示。Ruff/123 文件格式、文档及契约检查通过。
- 013 仅应用到 yta_test 并授予 runtime 新表 SELECT/INSERT，正常 yta 未迁移。重建 migrator 镜像并实际验证随机新库 001–013、012→013、原审计保留、重复迁移、缺凭证/runtime 拒绝与临时库清理。摘要和文件哈希见 market-bars-migration-observation.json。应用/工具镜像未重建，本轮没有 TS/UI 变更，未重复前端/浏览器套件。
- TASK-043 保持 IN_PROGRESS：此为未验收候选日线持久化，真实供应商授权、完整 point-in-time 资格、快照消费与发布门槛仍待完成，整体 goal 继续进行。

## 后续增量：包含结构化日线的真实备份恢复

- 扩展 check_acquisition_restore.py：除两张原始证据表外，比较 market_bars 全表行数/内容哈希，并通过受限 runtime 账号调用 daily_bars，核对 Decimal 字符串、日期、时间、来源关联与模式。原始证据无效、日线缺失/冲突分别计数；源库拒绝的记录在恢复库也必须拒绝，不写入或回填任一数据库。
- 实际执行 backup_local.py --source yta_test --drill，新建 yta_restore_81870a31fb894a3ea4e81326e2b0d673_test。30 张表一致、28 个不可变保护和 6 个身份序列验证通过；2,101 条预测及 746 个结算结果重现，恢复验证耗时 5.769 秒，仅代表这份本地合成数据。
- 新版采集恢复检查退出 0：393 份有效原始采集重建、88 份异常原始采集继续拒绝；有效原始采集中 392 份日线投影一致（含空结果），合计 13 条日线重建，1 份异常投影继续拒绝。未将空投影计作非空日线，也未将异常修复视为恢复成功。证据写入 infra/docker/market-bars-restore-observation.json，私有备份和恢复库保留供复查。
- Ruff/格式和文档检查通过。既有 CI 备份步骤直接调用同一检查器，无需新增工作流步骤；远端 CI 未执行。本轮无应用行为、模式、金融算法或 UI 变化，未重复前轮 382 项应用套件。异地加密备份、生产调度/角色重建及正式 RPO/RTO 仍未完成，TASK-021/043 和整体 goal 保持进行中。

## 后续增量：日线采集的实际运行镜像验证

- 重建 yta-alpha:local，包含证券参考和结构化日线代码。扩展 check_acquisition_container.py，使用无源代码挂载的已安装包、非 root/只读容器、合成 HTTP 与随机专用 PostgreSQL 数据库，实际采集一条高精度日线和空拆股响应；另外的容器完成 attempt 复查、质量检查和 daily_bars 读取。
- 验证 29 位小数价格及小数成交量原样保留、纽约日期、来源页哈希/行号、available_at=retrieved_at、published_at=null、RAW/UNVERIFIED。用恢复出的原始采集执行同键 save 后仍为一条日线、一份采集审计；LIVE_RESEARCH 读取 REPLAY 采集返回 NOT_FOUND，data_snapshots 保持空。质量检查继续报告两天缺失，没有补价格。
- 首次新增 HTTP 样例缺少 JSON Content-Type，采集命令返回 INVALID_PROVIDER_RESPONSE；定位后仅修正合成响应头，未放宽运行时验证。最终检查退出 0，外部供应商请求数 0，随机测试库及临时容器清理。镜像摘要、检查结果和文件哈希见 infra/docker/market-bars-container-observation.json。
- Ruff/123 文件格式及文档检查通过。既有 CI 容器任务包含此检查，远端未执行；本轮无业务实现变化，未重复 382 项应用套件。只重建 Alpha，BFF/Web/工具镜像和更新后的全栈启动仍待验证；不覆盖原有 app-local 历史观察，也不宣告供应商或发布资格通过。

## 后续增量：更新后的全栈容器与证券请求验证

- 重建 Alpha/BFF/Web 并以独立 app-local、test/REPLAY、专用 yta_test 启动，三服务均 healthy。扩展容器 smoke，经实际 Alpha HTTP 验证无凭证 401、nvda 标准化查询成功、NVDA 产品范围种子及未知上市日期保留 null、其他证券 422；仍显示 UNVERIFIED。
- 扩展既有容器配额检查：登录独立合成账户后，实际 BFF→Alpha 的 bars/indicators 对 canonical 与大小写/首尾空白请求返回相同内容，其他证券、内部空白和 Unicode 相似代码返回 422。随后验证研究配额 2 次接受/第 3 次 429、原键返回原 Run、两份持久消息、登录限流记录和打包 OpenAPI；最后取消本次 Run。
- 非 root、只读、私网、Web 无凭证/测试产物、静态资源、鉴权/readiness、Worker 队列检查及 BFF tracing 模块导入通过。app-local-observation.json 保存新镜像摘要和证据，深拷贝保留此前观察；013 迁移验证引用此前实际结果及其时间，不伪称本轮重做。没有启用 OTLP 导出或调用真实模型/行情。
- 全部测试容器与 edge 网络已清理，共享 PostgreSQL 和其他既有容器未停止。Ruff/123 文件格式、Node 脚本语法及文档检查通过；镜像构建包含 TS/Next 生产编译，本轮无业务源码变化，未重复应用/浏览器套件。
- release_inventory 的镜像/源码/001–013 迁移输入核对为 consistent，release_qualification 仍是 NOT_ASSESSED。首次用 runtime 读取迁移登记表按权限被拒绝，改用既有 owner 配置执行只读检查成功，未扩大 runtime 权限。工具镜像、真实供应商、完整 Hermes 门槛、远端 CI/staging 与发布验收仍未完成，整体 goal 继续进行。

## 后续增量：真实模型 admission 响应丢失与并发同键核对

- 新增 probe_hermes_admission_loss.py，挂入固定 pi 探针 Compose。执行前用 O_EXCL/0600/fsync 保存请求键、session 身份及正文；已有收据不允许 submit 创建新身份。局部 HTTP 代理实际向 Hermes 提交请求，取得 202 并保存原 Run ID 后不发送任何响应字节；实际客户端发生 ReadTimeout，并核对上游接受时间早于客户端超时。
- 随后 4 个并发精确同键 POST 均返回 replayed=true、同一原 Run 和相同 session key。继续查询原 Run 到 completed，真实 pi 配置模型 deepseek-v4-pro 返回 ACK；SSE 中一个 run.completed、没有 tool.started，同键不同正文返回 409。服务返回 usage 为输入 2,448、输出 2、合计 2,450 tokens，不等同于独立供应商计费核验。
- 实际固定镜像与 source-lock 摘要一致，结果及脚本哈希保存到 runtime-observation.json 的 pi_admission_response_loss_probe。此探针证明响应丢失后的 Run 身份核对，不宣称独立核验了供应商请求次数、所有超时路径或生产部署。六项门槛状态仍为 UNVERIFIED，真实 Adapter 未越过前置条件启用。
- 首次启动发现私有探针 env 中旧 Alpha 镜像已不存在；通过本次命令的固定本地 Alpha 镜像 ID 覆盖恢复启动，没有修改 pi 原始配置或输出凭证。探针所用 tools 镜像沿用原固定版本，本次未调用金融工具。测试结束清理全部探针容器/网络，保留外部私有卷及请求收据。Ruff/124 文件格式与文档检查通过；无应用/数据库变化，未重复应用套件或加入自动收费 CI。

## 后续增量：真实模型同会话排队取消

- 新增 probe_hermes_queued_cancel.py，在固定 Hermes 镜像和 pi 选定 deepseek-v4-pro 下，先提交长文本首轮并实际观察 message.delta，再以同一 X-Hermes-Session-Key 提交第二轮。第二轮状态查询实际返回 queued，随后 stop 得到 cancelled；重复 stop 保持 cancelled，精确同键重试仍返回原 Run。
- 第二轮 SSE 仅有 run.cancelled，没有 message.delta、tool.started 或 run.completed。停止首轮后确认首轮 cancelled，第二轮仍 cancelled，没有因会话释放而恢复。脚本 finally 对所有已保存的本次 Run 再次停止；请求键、正文及 Run ID 预先/逐步持久化，已有收据禁止用新身份重跑。
- 实际检查退出 0；镜像摘要、脚本及依赖哈希、脱敏观察写入 runtime-observation.json 的 pi_queued_cancel_probe。此证据限于单服务身份下的同会话排队，既非供应商计费/调用次数审计，也不证明工具副作用回滚或完整生产恢复。架构能力矩阵补充局部结果，总体门槛保持 UNVERIFIED。
- 探针容器和网络已清理，私有收据卷保留；无数据库或应用实现变化，未重复应用套件。Ruff/125 文件格式和文档检查通过；未将真实模型测试加入自动收费 CI，整体 goal 继续进行。

## 后续增量：单 Profile 报告契约、可信引用校验及 SOP

- 推进 TASK-087：共享 contracts 新增 research-report-v1 严格联合 schema，固定 NVDA/alpha-orchestrator，携带 data_mode/as_of/summary/risks、工具调用与来源/快照哈希/可用时间引用。DRAFT_READY 必须提供服务端 draft_id，UNAVAILABLE 必须 draft_id=null 并携带受限失败码；额外 owner、确认、金融数值、权限字段被拒绝。金融事实仍由 Alpha 草稿提供。
- 新增 BFF validateResearchReport，接收由调用方从已认证 Run 和已验证服务响应建立的可信上下文；校验模式/截点、工具调用与来源逐字段一致、唯一引用、两个工具种类、草稿归属上下文及快照一致、失败码匹配。时间比较保留微秒及更细小数，拒绝同一毫秒内晚于 as_of 的证据，支持时区等价时间。校验器不生成确认、不调用冻结、不声称验证了自由文本的事实真实性。
- 新增 alpha-orchestrator SOP，明确只读工具顺序、证据对齐、服务端草稿、不可用分支、REPLAY 披露及权限边界；使用 writing-for-agents skill 整理指令。JSON Schema 导出/漂移检查命令和 CI 步骤已加入。当前没有把 SOP 安装进 Hermes 或将新报告接入既有 REPLAY 消息事件；真实 Driver 的可信上下文收集、报告展示/持久化与模型遵循性仍需后续实现。
- 新增 18 项测试覆盖有效报告、未知字段/确认/金融数据注入、伪造工具/来源/哈希/时间、重复引用、错误草稿、未来微秒、时区、真实失败分支。TS 全量 92 项通过（contracts 19、BFF 73），无 PG 跳过；类型检查首次发现正则分组可能 undefined，补显式检查后 typecheck、18 项专项及 pnpm build 通过。报告 Schema 漂移、Alpha 契约、Ruff、文档检查通过。无 Python/数据库/UI 行为变化，未重复 Python/浏览器套件，镜像未重建。
- TASK-087 改为 IN_PROGRESS，当前 64 项进行中、56 项 TODO；这不是完成率或发布通过。整体 goal 继续进行。

## 后续增量：REPLAY 报告可信上下文与持久化恢复

- Alpha 新增受服务 token 保护的 /internal/v1/market/evidence，返回同一原始 MarketSnapshot 及 Alpha 计算的 snapshot hash；工具 token 无权访问该服务接口，LIVE_RESEARCH 无已核验供应商仍为 503。原始行情端点格式保持兼容，哈希与金融权威未转移到 BFF。
- ReplayResearchDriver 从该响应和 indicators 响应记录真实工具调用 ID、来源、快照哈希、固定截点及原始输入的最晚可用时间（不是指标生成/报告写入时间）。与服务端 Alpha 草稿核对后，才发布 prediction.draft 及带可选 report 的 message.completed；工具/草稿快照冲突时失败，不发布草稿或报告。报告仍是确定性合成 REPLAY，不冒充模型产出。
- 报告存入既有不可变 agent_events，无新迁移；runSummary 通过既有 owner/mode 检查返回 report，旧事件返回 null。可选 report 兼容历史消息事件。更新 Alpha/BFF OpenAPI 及 SOP 部署状态；尚未新增报告面板，也没有接入真实 Hermes Driver。
- 真实 PG 集成验证报告引用指向成功工具调用、与草稿一致、重建 Repository 后恢复相同内容、他人无法读取，以及快照冲突后无草稿/报告发布。浏览器主闭环增加实际 API 报告核对和再次打开研究后的持久一致断言，冻结前仍无预测，确认流程保持独立。
- 首轮浏览器回归发现证据 Pydantic 响应的 Z 与指标 isoformat 的 +00:00 被字符串比较误判；改用保留小数精度的双向时刻比较，并把两种格式加入 BFF 集成 fixture。修复后 TS 全量 93 项（contracts 19/BFF 74）、Python 全量真实 PG 383 项、隔离浏览器 17 项通过。类型检查、生产构建、契约漂移、Ruff/126 文件格式和文档检查通过；新增 API 测试最初使用过短合成 token，补足测试配置后通过。
- 应用镜像未随本轮源码重建；此前镜像/输入一致性记录属于旧版本，不能据此声称新版本部署验收。TASK-087 与整体 goal 保持进行中。

## 后续增量：工作台研究依据与风险面板

- 工作台新增原生 details 折叠区，显示数据模式、来源数量、信息截点、风险、行情/指标来源及各自可用时间；快照哈希单独折叠，沿用既有字号、颜色和间距。桌面与手机均在研究对话内查看，来源和哈希可换行，不增加外部链接或执行模型 HTML。BFF 合成报告风险文案改为中文。
- 历史恢复先验证 Run/session/mode、草稿 ID/forecast 与 report schema，再核对报告和草稿的模式/截点/快照；实时 message.completed 使用已接收的当前草稿核对。跨模式或错误草稿报告不展示，清空草稿/报告并关闭确认框；旧记录 report=null/缺失继续兼容。此为浏览器显示防线，来源权威仍由 BFF 的可信上下文校验提供，用户确认流程保持独立。
- 浏览器主闭环新增展开风险/来源、核对完整快照哈希、重新打开会话恢复、390px 无横向溢出验证；另两项受控网络反例覆盖历史报告跨模式和流式报告草稿引用不符，均不展示报告且无冻结入口。全量 19 项通过，截图取景调整后主闭环再跑 1 项通过，report-desktop.png/report-mobile.png 已实际查看，手机来源区域可滚动阅读。
- pnpm typecheck、pnpm test（93 项：contracts 19/BFF 74，实际 PG）、pnpm build 与文档检查通过。本轮没有 Python/数据库模式变化，未重复 Python 383 项套件；隔离浏览器服务已正常退出。应用镜像未重建，真实 Hermes 模型遵循性和正式集成仍待验收；TASK-087 与整体 goal 继续进行。


## 后续增量：pi 真实模型报告契约遵循性样本

- 使用用户指定的 pi agent 模型配置，在固定 Hermes 镜像上执行 deepseek-v4-pro 的两个独立报告样本。prepare_report_model_probe.py 从当前 Alpha ASGI 接口和真实专用 PostgreSQL 获取 REPLAY 证据、指标及一个服务端草稿；LIVE_RESEARCH 实际返回 DATA_UNAVAILABLE，未使用合成数据替代。服务端预先收集上下文，模型没有调用工具，不代表完整真实 Driver 编排。
- probe_hermes_report.py 将实际 SOP、报告 JSON Schema 和可信上下文提供给模型。两个输出均通过当前编译的 BFF validateResearchReport：DRAFT_READY 保留两条正确引用和草稿 ID；UNAVAILABLE 没有证据或草稿。check_report_model_output.mjs 独立读取可信输入，并验证添加 confirmed、篡改模式及伪造工具引用被拒绝。
- 就绪样本用量为 input 5956 / output 951 tokens，不可用样本为 input 5618 / output 284 tokens。就绪样本首次读取 SSE 超过公共探针原 64 KiB 上限；实际终态已完成，事件响应为 88388 字节。将本探针读取上限调整为 2 MiB 后，使用已保存的相同请求键和正文恢复原 Run，未新建模型执行。
- 实际数据库核对为一个草稿、零冻结预测。镜像、脚本/SOP/Schema/校验器哈希及输出摘要记录在 infra/hermes/runtime-observation.json 的 pi_report_contract_probe；私有输入和请求收据保留。探针容器与网络已清理，PostgreSQL 和其他既有服务保留；未将付费模型调用加入自动 CI。
- 两个样本只证明本次契约和引用遵循性。人工检查发现就绪摘要将所有行情/指标精确值指向草稿，而草稿只承载预测事实，并包含面向用户不必要的 BFF 术语；报告文字质量仍需改进。没有独立供应商计费审计、真实行情资格或完整 Driver/发布验收，所有 Hermes 整体能力门槛保持 UNVERIFIED。
- 本轮重新运行两个已保存输出的实际 BFF 校验、JS 语法检查、Ruff 全量及 128 文件格式检查，均通过。没有修改应用行为或数据库模式，不重复此前 Python 383 / TS 93 / 浏览器 19 项全量套件；应用镜像未重建。TASK-087 与整体 goal 保持进行中。


## 后续增量：报告事实归属指引修正与真实模型复验

- 按 writing-for-agents 指引修正 alpha-orchestrator SOP：草稿承载预测事实，行情与指标属于各自工具响应；只有引用元数据时，说明证据可用性和限制，不推断价格、指标或方向。用户文案使用确认动作说明，避免内部服务术语。
- 探针与独立 BFF 校验脚本支持显式 wording-v2 测试版本，使用独立收据，原始请求/输出保留。实际检查两份新收据都包含当前 SOP，且与原测试请求键和 Run 不同；同一版本的恢复仍复用已保存身份，修改指引不覆盖过去的实测证据。
- 使用同一 pi 配置 deepseek-v4-pro、固定 Hermes 镜像及原服务端可信上下文，两个新样本均完成并通过实际 BFF 校验及篡改反例。就绪样本 input 6034 / output 1086 tokens；不可用样本 input 5696 / output 295 tokens。模型未调用工具，此轮未创建新草稿或执行冻结。
- 人工核对就绪文本明确声明仅有引用元数据、不能推断数值或方向，不再把行情/指标精确值指向草稿，且两个样本均未使用 BFF 术语。不可用文本仍出现 as_of 术语和泛化基线提示，不能宣称文案已全面验收。脱敏结果与版本哈希保存为 runtime-observation.json 的 pi_report_wording_v2_probe；旧观察保持原样。
- Ruff、128 文件格式、JS 语法、文档检查通过；仅修改 SOP 与手动探针，没有应用/金融/数据库行为变化，未重复全量应用测试或重建镜像。两个样本不代表广泛模型可靠性或真实 Driver 集成；TASK-087 及整体 goal 继续进行。


## 后续增量：报告功能应用镜像与实际容器浏览器闭环

- 重新构建 Alpha/BFF/Web 三个镜像，将报告证据端点、BFF 报告校验/持久化及工作台报告面板打包。使用 compose.app-local.yaml 的 test/REPLAY/yta_test 配置启动，三个服务实际 healthy；镜像摘要记录在 infra/docker/report-container-observation.json，旧 app-local-observation.json 保留原版本验证范围。
- check_container_stack.py 通过实际容器非 root、只读根、私网隔离、静态资源、鉴权/ready、证券历史及只读 Worker 队列检查。新增 playwright.container.config.ts，限定本机数据库与 3300 端口，复用现有主闭环测试。首次 defineConfig 合并保留宿主 webServer 项，检测到已有端口后退出；改为单配置对象覆盖后，仅访问既有容器栈，未重启宿主服务。
- 实际浏览器主闭环 1 项通过：报告来源/风险/哈希、持久化报告恢复、确认前无冻结、取消确认仍无冻结、显式确认冻结、结算及刷新、390px 手机无横向溢出。此次没有重复其余 18 项浏览器测试。pnpm typecheck 通过；没有修改业务或数据库模式，沿用此前 Python 383 / TS 93 项源码验证，不据此声明新镜像通过全部套件或 staging 验收。
- README 已记录可复现命令。正式 Hermes Driver、真实行情及完整发布门槛仍待完成，整体 goal 保持进行中。


## 后续增量：报告模型探针恢复避免过期幂等键重复执行

- 审查真实接入缺口时按架构 3.1 保持 TASK-027 未启用：TASK-007/015 的完整接入门槛尚未通过，不能以局部模型/报告证据绕过。发现报告探针原先对已有收据再次 POST 相同请求；Hermes 已实测终态幂等记录有保留期限，原键不能永久防重。
- probe_hermes_report.py 改为只有本进程以 O_EXCL/fsync 新建请求收据后才允许首次 POST。已有 Run ID 仅 GET 原 Run 和事件；既有收据缺少 Run ID 时返回 admission_unresolved_no_resubmit，保留身份等待核对。状态 404、execution_unknown 或事件丢失均不会自动换键或重发；这意味着首次 POST 前崩溃也会保守要求核对，不假定上游未执行。
- 新增 8 项协议回归验证已知 Run/过期键零重发、未决收据零请求、首次提交持久化、接受后响应超时/异常再启动不重发，以及 Run 404、unknown 和事件 404 的恢复边界。专项 8 项及相关 Hermes/pi 探针回归共 56 项通过，Ruff、129 文件格式与文档检查通过。该修复未调用真实模型或更改历史观察文件；之前样本的旧脚本哈希仍只对应当时执行。
- 本轮没有金融、数据库、应用接口变化，不重复全量应用/数据库/浏览器套件或重建镜像。修复局限于报告探针，不据此声称全部历史探针或未来真实 Adapter 的恢复协议已验收；整体 goal 继续进行。


## 后续增量：固定 Hermes Skill 索引与自动加载工具门槛

- 扩展 check_hermes_context_scope.py，在两个合成 Profile 中实际写入带独立标记的 SKILL.md，并在各自配置中设置 skills.auto_load。调用固定版本真实 agent.system_prompt 的 _skills_prompt / _auto_load_parts：仅有两个金融工具时目录索引为空、自动加载为空；显式启用 skills_list/skill_view 后两者均包含本 Profile 标记，两个正例排除文件缺失造成的假通过。
- 在固定镜像、network none、非 root、只读根、cap_drop ALL 和 no-new-privileges 条件下实际退出 0；原有 64 次并发上下文读取与两个跨线程显式 home 检查也通过。没有外部模型调用。结果和脚本/审阅源码哈希记录在 runtime-observation.json 的 locked_skill_prompt_gates_probe，原上下文观察保留。
- 此证据验证真实提示构造函数的工具门槛，不代表完整 Gateway 请求、插件/所有上下文注入路径或操作系统文件隔离。共享 UID 仍能直接读取另一 Profile 文件，不能把上下文路由当作安全沙箱；TASK-014/015 及整体能力门槛仍为 IN_PROGRESS/UNVERIFIED。
- Ruff、脚本格式和文档检查通过。未修改应用或持久化行为，未重复全量应用/数据库/浏览器测试，也未改动生产部署或真实 Adapter。整体 goal 继续进行。

## 后续增量：草稿历史行情证据读取与完整性校验

- 推进 TASK-049：Alpha 新增私有 GET /internal/v1/prediction-drafts/{draft_id}/evidence，复用 ToolMarketSnapshot 契约；只接受 BFF 服务凭证和可信 owner header，金融工具 token 无权读取。按草稿归属及配置数据模式查询不可变 data_snapshots，不从当前 Provider 重新取数，也不因草稿过期而拒绝历史追溯。
- ResearchStore 在返回前重建 MarketSnapshot/Forecast，核对规范化内容哈希、草稿引用、模式、provider 和 as_of。不存在/他人/跨模式均 404；无效结构、哈希或截点不一致返回固定 EVIDENCE_INVALID/503，不把异常保存内容透传。十进制仍以字符串返回，不执行金融重算、写入或冻结。
- 新增 5 项真实 PostgreSQL 用例覆盖原始快照一致性、Repository 重建恢复、归属与模式边界、伪造哈希/无效 payload/截点不一致、服务/工具鉴权、缺失 owner、十进制响应与读取后无冻结。完整 Python 396 项通过，使用专用 yta_test 和独立运行角色，无跳过；4 条既有 pandas 弃用警告不变。Ruff、129 文件格式、Alpha OpenAPI 导出/漂移及文档检查通过。
- TASK-049 调整为 IN_PROGRESS，当前 65 项进行中、55 项 TODO，不等于完成率。独立 evidence_items 索引、BFF/报告追溯入口、供应商完整血缘及真实数据资格仍待完成。未修改 BFF/Web 或数据库模式，未重复其测试/构建；上轮应用镜像不包含本次新端点，部署需后续重建验证。整体 goal 保持进行中。

## 后续增量：BFF 草稿历史证据 API 与共享响应契约

- BFF 新增 GET /api/yta/v1/prediction-drafts/{id}/evidence，服务端从已认证登录会话派生 owner；浏览器查询字段不能替换 owner。使用 UUID 路径校验和既有 Alpha 私网客户端，沿用不跟随重定向、超时及响应大小上限，凭证不进入响应。
- contracts 新增 marketEvidenceSchema，严格定义快照/日线/拆股字段、字符串十进制、日期时间和行数上限；BFF 对额外字段、浮点价格等异常响应返回固定 UPSTREAM_INVALID/502，配置模式不符返回 404。金融 OHLC 计算、规范化哈希及历史完整性仍由 Alpha 负责，BFF 不重新计算金融事实。OpenAPI 和安全日志路由白名单同步更新。
- 真实 PostgreSQL BFF 集成（上游 HTTP fixture）验证未登录不访问 Alpha、会话 owner 正确转发、他人带伪造 owner 查询仍被拒绝、精确十进制、额外敏感字段不透传、跨模式和浮点响应拒绝。pnpm test 共 94 项通过（contracts 19 / BFF 75），typecheck/build 及 BFF 契约漂移检查通过。
- 隔离浏览器 2 项通过：Swagger 展示新增端点共 25 个操作；主闭环经真实 BFF→Alpha→PostgreSQL 读取历史快照，核对报告哈希、REPLAY 模式、日线数量及十进制字符串，并继续验证报告恢复、显式冻结、结算和手机布局。测试服务正常退出。本轮无 Python 或数据库模式变化，沿用上一轮 396 项 Python 证据；未重复其余 17 项浏览器测试或重建镜像。
- README 和 TASK-049 当前证据同步更新；报告面板内的追溯入口、独立证据索引和完整供应商血缘仍待完成，整体 goal 保持进行中。

## 后续增量：报告面板历史行情追溯入口

- 报告面板新增“查看草稿历史行情”，点击后通过已登录 BFF 按需读取历史快照；浏览器再次检查严格响应契约、数据模式及报告引用哈希，失败时不显示数据并提供显式重试。重复点击加载中按钮不会重复请求，组件卸载取消未完成请求，按草稿身份隔离显示状态。
- 展示保存时的来源、提供方、信息截点、原始日线 OHLC/成交量/版本/可用与采集时间以及公司行动覆盖和拆股记录。日线由近到远，每页 20 条；价格直接显示原始十进制字符串，不转换 Number、不重新计算或补值。明确说明为历史原始日线，未拆股调整，冻结仍需独立确认。
- 新增浏览器反例：返回不符的快照哈希时无表格，用户显式重试后恢复；主闭环增加实际证据加载、20 行分页、切换更早/较近日线、历史恢复后再次读取和手机无横向页面溢出。全量浏览器 20 项通过，截图取景复验 1 项通过。检查截图后限制表格高度、固定表头并对长精度值换行，主闭环再跑 1 项通过；最终桌面/手机截图已实际查看。
- pnpm typecheck/build 通过；首次类型检查发现 Web 没有直接 zod 依赖，改用已有 schema.parse 的返回类型，无新增依赖。末次样式由隔离浏览器生产构建验证。无 Python/BFF/数据库行为变化，不重复此前 Python 396 / TS 94 项套件；应用镜像未重建。README/TASK-049 当前证据和文档检查已更新；独立证据索引、完整供应商血缘与发布验收仍待完成，goal 继续进行。


## 后续增量：不可变行情证据索引与快照同事务写入

- 新增迁移 014_evidence_items.sql：以 data_snapshots 内容哈希为主键/外键，固定 MARKET_SNAPSHOT/market-snapshot-v1，保存 source_id/provider/data_mode/as_of/available_at，检查可用时间不晚于截点；提供模式/截点索引，并通过语句级不可变触发器拒绝 UPDATE/DELETE/TRUNCATE。
- Alpha _save_snapshot 在原事务中核对已有快照 payload、写入索引并核对重复键元数据。最晚可用时间取日线和拆股输入证据时间，不以写入时间代替；内容或索引冲突返回 EVIDENCE_CONFLICT，事务不能继续产生新草稿。新草稿/结算使用此路径，不把已有幂等请求直接返回或整个历史数据集冒充已回填。
- 实际在专用 yta_test 应用 014，运行角色只增加 SELECT/INSERT；重复迁移退出 0 且无新应用项，正常 yta 未变更。真实 PG 验证 4 并发保存仅一条索引、runtime 修改/删除/清空权限拒绝、owner 也被不可变触发器拒绝、快照和索引共同回滚，以及索引来源冲突阻止草稿。完整 Python 399 项通过；随后新增原快照哈希冲突反例并复验相关 4 项通过，未将其宣称为重跑 400 项全量。
- Ruff、129 文件格式、Alpha 契约与文档检查通过，4 条既有 pandas 弃用警告不变。无 TS/Web 变化，未重复其套件；应用/migrator 镜像、恢复演练尚未纳入新迁移，本机旧镜像证据仍只覆盖原版本。
- TASK-049 保持 IN_PROGRESS；历史索引回填与读取一致性、供应商到合格快照的血缘及完整部署/Review 待完成。整体 goal 继续进行。


## 后续增量：历史证据索引读取验证与显式审计回填

- 提取 evidence 元数据/索引读写模块，供快照事务和回填工具复用。草稿历史证据读取在已有索引时核对来源、提供方、模式、截点及最晚可用时间，不一致返回 EVIDENCE_INVALID；旧快照没有索引时保持原始内容哈希校验后的只读兼容，不通过用户 GET 隐式回填。
- 新增 yta_alpha.backfill_evidence CLI：一个明确内容哈希和显式模式，默认 SET TRANSACTION READ ONLY 检查；--apply 才写入缺失索引，并同事务追加 EVIDENCE_INDEX_BACKFILLED 审计。先验证保存 payload 的模型结构、规范化哈希、模式/provider 和证据可用时间；已有冲突拒绝，绝不更新原始快照/索引。插入 RETURNING 决定是否追加审计，并发者不重复审计。
- 新增真实 PG 测试覆盖旧草稿读取不写索引、只读检查、4 并发回填仅一个 indexed/一条审计、重复检查已索引、已有冲突使读取和回填均失败、跨模式拒绝、审计准备失败回滚新索引。完整 Python 404 项通过，4 条既有 pandas 警告不变；Ruff、131 文件格式、契约和文档检查通过。
- 另对专用 yta_test 中一个有效合成旧快照实际运行 CLI 检查→--apply→检查，状态为 missing→indexed→already_indexed；观察和代码哈希保存为 infra/docker/evidence-backfill-observation.json。未批量回填包含故意损坏测试事实的整个数据库，也未触碰正常应用库。
- README/TASK-049 已更新。无 BFF/Web 或模式迁移变化，未重复其测试/构建；运行镜像仍未纳入最新证据模块。全量历史验收、供应商血缘、真实数据资格和发布门槛仍待完成，goal 继续进行。


## 后续增量：证据功能容器、迁移升级及恢复演练

- 重建 migrator/Alpha/BFF/Web 镜像，包含迁移 014、历史证据 API/面板、索引和回填模块。固定迁移器实际通过全新 001–014、013→014、重复执行和运行角色拒绝；升级用例新增真实合成快照，验证 payload 和已有审计不变、证据索引为空（无隐式回填），两个临时迁移库已清理。
- 本地 test/REPLAY/yta_test 容器栈全部 healthy，既有隔离/非 root/只读根/鉴权/队列 smoke 通过，完整浏览器 20 项通过。镜像内安装后的 backfill 模块只读检查通过；首次选择示例 source_id 不存在导致探针失败，改为按实际已保存且来源一致的索引选择后通过，未修改业务数据以绕过检查。
- 对含故意无效快照的整套 yta_test 执行备份恢复，域验证按预期拒绝 data_snapshots 内容哈希不一致，不能作为正向验收；失败恢复库和私有归档保留。新增 check_evidence_restore.py 创建独立干净合成源库，实际 pg_dump/pg_restore 成功：31 张表、29 个不可变保护、6 个序列、1 条预测重现和1条证据索引校验通过，恢复后按 owner 读取草稿快照一致。
- backup_local 新增证据索引与原始快照的元数据校验，旧备份没有此表时兼容原有验证；只额外允许严格 UUID 命名的本机备份 fixture 源库。实际恢复库中插入冲突元数据的事务被域验证拒绝并回滚，再次检查仍只有1条有效索引。备份边界7项通过，Ruff/格式/文档检查通过。
- 新增证据记录 infra/docker/evidence-container-observation.json，保留旧镜像观察的版本范围。恢复演练的源库已删除，恢复库和0600私有备份保留；本轮应用容器栈已清理。没有重跑未变更的应用计算/TS套件，沿用此前 Python404/TS94 的源码证据；本机演练不证明生产 RPO/RTO、真实供应商资格或 staging 发布，goal 继续进行。

## 后续增量：Alpha 行情缓存与新鲜度私有 API

- 推进 TASK-045：新增 MarketCache，每进程只保留一个配置来源的 NVDA 有效快照；缓存键包含 data_mode/provider/日历版本/原始快照版本，30 秒单调时钟 TTL，锁内刷新避免并发重复请求。快照重新执行结构和已有金融输入时点验证，模式/提供方不符或无当前交易日数据均不替换旧有效版本；REPLAY 使用快照逻辑时钟，不能填入 LIVE 缓存。
- 私有 GET /internal/v1/market/freshness 返回明确 FRESH/STALE/UNAVAILABLE 和最后有效来源/哈希/截点。刷新失败的旧数据仅用于说明 STALE，snapshot/evidence/indicators/新草稿链路通过 require_fresh 拒绝消费并返回 503；无真实 Provider 仍 UNAVAILABLE，未退回合成数据。TTL 内遇到新交易日收盘也会标记旧数据过期，周末不虚构新 session。
- 新增 10 项缓存测试覆盖16并发仅一次加载、TTL 到期失败、失败重试间隔与恢复、跨模式拒绝、周末/收盘边界、提供方/未来数据/无效结构及非法 TTL；新增 API 模式/鉴权用例。完整真实 PG Python 419 项通过。随后补充真实 ASGI 失败→STALE 元数据→行情/指标/新草稿503→恢复场景；首次草稿 fixture 缺 request_id 被422拒绝，补齐合法请求后 API 7 项专项通过，未宣称重跑420项全量。
- Ruff、134文件格式、Alpha/BFF OpenAPI 导出和漂移检查通过；pnpm build 通过，隔离浏览器 Swagger 与研究/证据/确认冻结/结算主闭环2项通过并正常退出。没有数据库模式或TS业务变化，未重复TS全量和其他浏览器用例；应用镜像尚未纳入本轮缓存实现。
- TASK-045 改为 IN_PROGRESS，当前66项进行中、54项TODO，不等于完成率。BFF/页面新鲜度展示、真实供应商资格、多实例/后台刷新策略和完整发布验收仍待完成；goal 继续进行。

## 后续增量：BFF 与概览页行情新鲜度展示

- 共享 contracts 新增严格状态联合 marketFreshnessSchema：FRESH/STALE 必须包含来源/哈希/截点，FRESH reason=null，STALE 使用受限失败原因；UNAVAILABLE 必须无旧证据。BFF 新增登录保护的 /stocks/{symbol}/freshness，固定 NVDA，验证响应及运行模式；异常状态、额外字段或跨模式响应返回固定 UPSTREAM_INVALID，不透传私有错误/凭证。OpenAPI 和日志路由同步更新。
- 概览页复用现有证据卡，分别展示回放数据可用、真实日线上次检查可用、行情过期、不可用及状态未知。可见时30秒检查、重新可见时检查、手动更新；每次检查清除旧可用提示，10秒超时，卸载取消请求，过期时仅说明上次有效来源/截点，不把其声明为实时行情或允许研究的依据。
- 真实 PG BFF 集成（HTTP fixture）验证未登录/非 NVDA 不访问上游、STALE 正常传递、UNAVAILABLE携带旧证据/缺失来源/跨模式/额外敏感字段拒绝。TS全量95项通过（contracts19/BFF76），typecheck/build、BFF契约漂移和文档检查通过。
- 新增浏览器状态转换测试：真实初始FRESH后受控响应模拟定时检查STALE、请求失败清除旧提示、UNAVAILABLE、跨模式未知和显式恢复。首轮更新Swagger操作数时误将三个分页测试的期望25改成26；仅恢复误改的期望后，全量21项通过。Swagger实际26个操作；桌面过期状态及375px概览截图已查看，无横向溢出，隔离测试服务正常退出。
- 本轮无 Python/数据库变化，未重复前轮Python419全量及后补API7项证据；应用镜像尚未纳入本轮新鲜度页面/BFF。TASK-045仍IN_PROGRESS，真实Provider、多实例策略和完整发布验收待完成，goal继续进行。


## 后续增量：隔离损坏事实反例，恢复 CI 正向备份验证

- 审查故障测试时发现 CI 在全量测试后对共享 yta_test 做严格恢复，而新增快照/索引完整性负例把损坏事实永久留在该库，必然使后续正向恢复失败。未放宽域校验，也未删除已有共享库事实；将六个参数化损坏场景移到各自 UUID 临时 `_test` 数据库，用同样迁移和运行角色权限执行，finally 只删除本轮创建的临时库。
- 新增 check_test_database_restore.py，在全新本机测试库运行真实 PG 全量 Python 后，实际备份/恢复并执行域校验。第一次完整流程420项通过且域恢复通过；随后调用采集检查时误用了默认 yta_test 源库，与新库恢复当然不匹配。改为在本轮源库删除前显式传入实际源库，采集验证仅额外允许严格 UUID fixture 命名，原 CI 默认源库行为不变。
- 再次完整流程420项通过（4条既有pandas警告，无跳过）；恢复31张表、29个不可变保护、6个序列，重现42条预测、17条结算，核对9条证据索引。实际源库/恢复库的原始采集及日线表一致：28份有效采集可重建、4份故意异常采集仍拒绝；27份有效日线投影共10条日线，1份缺失/异常投影仍拒绝。这里保留了采集负例的预期拒绝，不将其伪报为有效数据。
- 后补数据库来源边界反例，备份专项11项通过；Ruff、135文件格式与文档检查通过。观察和文件哈希写入 infra/docker/test-suite-restore-observation.json。两个全套源库及损坏反例临时库均已清理；恢复库、0600私有日志/备份保留。现存共享 yta_test 未被修复或清空。
- 此轮修复测试隔离和验证流程，未修改应用行为或重建镜像；没有远端 CI/staging 执行证据。真实Provider故障集成及完整发布验收继续待完成，goal保持进行中。


## 后续增量：供应商真实 HTTP socket 故障回归

- 推进 TASK-108，新增10项本机随机端口 HTTP 测试，使用合成凭证/数据：401/403/429/500、302不跟随、响应正文提前断流、采集总时限、日线成功但拆股失败均明确返回域错误；检查实际请求次数，拒绝隐式重发、部分成功和错误正文泄密。成功响应保留原始字节与高精度 Decimal，资格仍为 UNVERIFIED。
- 独立的可信合成快照 HTTP 服务验证缓存 FRESH→429刷新失败→STALE→恢复；旧哈希仅用于说明，require_fresh 拒绝继续消费，TTL 内不重复请求。这不是把 Massive 候选自动认定为合格快照，未接通 LIVE，也未调用真实行情账户或模型。
- 专项10项通过；随后在全新 UUID 专用测试库运行完整真实 PG Python 套件，434项通过、4条既有pandas警告、无跳过。实际备份恢复校验31张表、29个不可变保护、6个序列、42条预测、17条结算、9条证据索引；原始采集/日线一致，28份有效采集和27份有效投影可重建，预期无效的4份采集/1份投影仍拒绝。
- Ruff、136文件格式、Alpha契约漂移和文档检查通过。观察及源码哈希保存至 infra/docker/provider-http-fault-observation.json；临时源库已清理，私有备份/日志和恢复库保留。本轮仅增加测试与文档，未改变应用行为或重建镜像，未重复未变更的 TS/Web 套件。
- 默认 pytest 路径与现有 CI 命令会收集这些测试，但没有远端 CI/staging 执行证据。TASK-108 改为 IN_PROGRESS，当前67项进行中、53项TODO，不能视为完成率；真实供应商资格、Hermes完整集成和发布验收仍待完成，goal保持进行中。


## 后续增量：LIVE 缓存信息截点回退保护与新鲜度容器验收

- 复现同一已收盘交易日内的信息截点回退：两个快照单独均通过结构/时点验证，但刷新时较早 as_of 会替换已有较新快照并被标为 FRESH。新增回归用例在修复前确实失败，随后在 LIVE 缓存替换前拒绝 as_of 倒退，保留原哈希/截点作为 STALE 元数据，require_fresh 返回 STALE_DATA。原截点重新核验或更新截点均可恢复；REPLAY 保持隔离逻辑时钟可回拨。
- 此保护只比较信息截点，不把 as_of 当作供应商修订号，也不宣称提供跨进程/重启后的持久高水位。实际 LIVE Provider 仍未接通，测试使用明确合成快照；没有改写任何已保存事实或冻结/结算逻辑。
- 缓存与真实 HTTP 故障专项22项通过；完整专用 PostgreSQL Python 套件436项通过（37.13秒，4条既有pandas警告，无跳过）。Ruff、136文件格式、Alpha契约漂移和文档检查通过。
- 重建 Alpha/BFF/Web 本机生产包镜像，纳入前轮缓存/新鲜度接口和页面及本轮回退保护。专用 test/REPLAY/yta_test 容器栈全部 healthy，私网/非root/只读/鉴权/readiness/Worker smoke 通过；完整容器浏览器21项通过（22.5秒），包含新鲜度状态转换。镜像内实际导入的缓存模块 SHA-256 与当前源码一致，镜像ID和观察见 infra/docker/freshness-container-observation.json。
- 本轮无TS源码或持久化变化，未重复TS单测和备份恢复；容器构建/浏览器完成后清理本轮应用栈，保留原有 PostgreSQL。TASK-045/108仍IN_PROGRESS，67项进行中/53项TODO不变；本机验证不替代真实供应商、远端CI、staging和完整发布验收，goal继续进行。


## 后续增量：Hermes 完整 Gateway 的 Profile 上下文隔离

- 将此前内部 lookup/提示组装证据推进到锁定镜像的实际 Gateway：专用 internal 网络内运行两个无工具 Profile 和合成 OpenAI 协议端点；端点检查收到的真实 system 消息包含本 Profile 的 SOUL 标记，不含另一 Profile 标记、已配置 auto_load 的禁用 Skill 内容或工具声明。两个并发客户端共8个 Run 全部 completed；端点共接受8次请求，工具调用0，外部模型请求0。
- 首次配置遗漏 OPENAI_BASE_URL，锁定 Hermes 的主机配对保护没有将 OPENAI_API_KEY 发送到未明确配对的模型地址，3个已提交 Run 均确认 failed（401）。补齐合成配置后使用明确 paired-env 实验版本，保留原失败收据；没有把未知执行当作重发许可，也没有修改上游安全逻辑。
- Gateway 重启后以同批收据只 GET 原 Run，8个结果仍可核对；合成端点累计请求数仍为8，恢复没有新增推理。实际 inspect 确认两容器非root/只读/无发布端口、唯一网络 internal；完整观察、镜像和脚本哈希写入 infra/hermes/runtime-observation.json 的 locked_gateway_context_probe。
- 新增7项正反控制验证端点会拒绝缺少自身身份、身份只在用户消息、跨Profile标记、Skill泄漏和工具声明，避免空提示假通过。完整真实 PostgreSQL Python443项通过（21.79秒，4条既有pandas警告，无跳过）；Ruff、138文件格式、契约和文档检查通过。无应用/TS变化，未重复Web构建或浏览器套件。
- 已清理专用容器/网络，保留本次持久卷及已完成/失败Run收据。此次只证明无工具配置的 Gateway 提示隔离；finance工具配置有此前独立证据，但不能合并宣称完整部署已验收。共享UID文件可读问题、任意插件/工具边界、真实部署仍待验证；source-lock全部接入门槛仍UNVERIFIED，TASK-027仍不越过前置门槛。整体67项IN_PROGRESS/53项TODO不变，goal继续进行。


## 后续增量：候选行情 Parquet 分区与 DuckDB 查询

- 推进 TASK-046，新增 market_lake 模块和 lake_cli export/query。导出从 AcquisitionStore.daily_bars 只读核对原始证据及 PostgreSQL 日线投影，按 data_mode/acquisition_hash 固定归档版本，再按 symbol/year 写 ZSTD Parquet。OHLCV 使用十进制字符串，保留原始精度、页/行引用、来源哈希、可用/采集时间和 published_at=null；不补缺口、不合并修订或模式，始终 UNVERIFIED。
- 严格 manifest 保存版本、采集身份、行内容哈希、分区文件哈希与行数。文件及 manifest 在私有临时目录内完成校验后原子重命名发布，失败只清理本轮临时目录；并发/重复导出核对已有完整归档，损坏时拒绝覆盖。查询校验 manifest、模式、固定路径、符号链接、文件/内容哈希后使用 DuckDB 参数化只读分区查询，并与已验证行核对；无需数据库或供应商凭证。
- DuckDB 固定1.5.6并锁依赖；禁用扩展自动安装/加载，2线程/256MB内存、不自动溢写。没有把 DuckDB 用作金融计算权威；使用字符串是为了保留 PostgreSQL 超过38位的源数值，依据官方数值类型限制，链接见 README。当前仅本地 NVDA 候选归档，全量完整性读取的成本尚未做规模优化。
- 新增10项真实PG测试：跨年/缺口与DB完全一致、50位以上小数与空发布时间、并发一次发布、重复导出、损坏文件/manifest/路径遍历/符号链接/错误模式拒绝、写故障不发布、空归档及离线CLI、修订/模式各自保留。完整新建专用数据库套件453项通过（22.45秒，4条既有pandas警告，无跳过），实际备份/恢复校验31表/29保护/6序列、42预测/17结算/9证据；39份有效采集与38份日线投影共23条日线重建一致，原有故意无效采集4份/投影1份仍拒绝。
- 全量之后补充归档根目录0700创建，相关10项PG测试再次通过；实际CLI导出2条高精度数据到两个年份并在去除DB配置后查询一致。重建Alpha Linux镜像，在只读非root运行时中实际导入 DuckDB1.5.6、从测试PG导出并查询核对通过，安装模块哈希与源码一致；容器自动清理，没有启动正常应用服务。观察、镜像和代码哈希保存为 infra/docker/market-lake-observation.json。
- Ruff、140文件格式、Alpha契约与文档检查通过。无TS/Web变化，未重复其套件；无数据库迁移或事实写入逻辑变化。专用全套测试源库已清理，恢复库/私有日志备份和本机Parquet示例保留。TASK-046改为IN_PROGRESS，当前68项进行中/52项TODO，不代表完成率；真实供应商、多证券/云存储、规模性能及完整部署Review仍待完成，goal继续进行。


## 后续增量：恢复 Runbook 与数据库/Parquet 联合演练

- 新增 docs/recovery-runbook.md，明确 PostgreSQL事实、baseline配置、原始采集、Parquet、Hermes卷、密钥和训练制品各自的恢复范围。包含本机完整演练、创建备份、可信旧manifest恢复、新目录Parquet重建、失败判定、未知Run对账、服务切换/回退门槛及演练记录；明确当前脚本不会切换正常服务。
- 新增 check_disaster_recovery.py：创建独立UUID测试源库，写入1条合成REPLAY冻结预测及跨年候选行情，先导出Parquet，再实际pg_dump/pg_restore到新库。恢复31表、29个不可变保护、6序列，重现1条预测、校验1条证据索引和1份baseline配置哈希；按owner读取证据一致，恢复库重建的2个分区/2条行情与原manifest、文件哈希和按年查询完全一致。
- 实测备份0.242秒、PG恢复/验证0.382秒、恢复加重建0.463秒，仅代表该本机小样本阶段耗时；服务RTO和生产RPO保持null。当前备份created_at生成于dump之后，不能假装是精确恢复点；cron仅示例未部署，不能据此宣称24小时RPO。Hermes一致备份、异地取回、训练权重、业务流量切换仍待验收。
- 又实际执行Runbook中的 --restore-manifest 入口，使用前述可信归档恢复到另一全新库，全部校验通过（0.542秒），确认没有覆盖上次恢复结果。源库删除已通过pg_database查询确认；两个恢复库、私有备份、原始/重建Parquet与观察留存。无实际Provider/模型调用，正常服务未重启。
- Ruff、142文件格式、文档任务图和Runbook本地链接检查通过。此轮只新增运维演练脚本/文档，未改变业务计算或数据库模式，没有重复前轮453项全量和TS/Web套件。观察及源码哈希见 infra/docker/disaster-recovery-observation.json。TASK-113改为IN_PROGRESS，当前69项进行中/51项TODO，完整DoD未完成，goal继续进行。


## 后续增量：BFF 业务写入入口暂停控制

- 依据恢复Runbook中识别的缺口，新增服务端 YTA_WRITE_ADMISSION=OPEN|PAUSED，默认OPEN、非法值拒绝启动。暂停时新会话、研究消息、预测冻结/更正、评测保存和REPLAY结算在鉴权/CSRF之后、持久化/Alpha调用之前返回503 WRITE_ADMISSION_PAUSED。客户端Header/正文不能改变策略；所有同键POST也暂停，保留原请求身份用于GET对账及恢复后精确重试。
- 登录登出、查询和取消继续允许；readiness加入必填checks.admission，PAUSED返回503而health仍200，OpenAPI同步描述控制范围及错误。没有公开管理端点，配置启动时读取；在途请求、已接收Run、Runner、Jobs和私有Alpha调用不被该开关停止，不能宣称数据库只读或全局瞬时屏障。
- 真实PG BFF HTTP测试验证六类写入均不增加研究会话/Run或调用上游、原键阻挡、伪造策略Header无效、未登录/CSRF仍拒绝、登录登出/读取/取消正常、ready/health分离及开放实例按原键找到原Run。TS全量97项通过（contracts19/BFF78），typecheck/build和BFF/Alpha契约漂移检查通过。
- 浏览器新增受控503场景，确认页面保留原研究和冻结请求标识、只有显式点击才重试且恢复后使用相同key；完整隔离浏览器22项通过（30.3秒）。该浏览器用例使用响应fixture，服务端实际PAUSED行为由前述真实HTTP/PG测试覆盖。未改Web源码，也未调用真实模型或行情账户。
- .env.example、隔离Compose配置和恢复Runbook更新。明确所有BFF实例需更新，readiness摘流可能同时影响读取；本地Compose首次PAUSED启动会阻止依赖健康的Web启动，因此不可把它当作新的正常启动配置。没有更改现有进程环境、重建镜像或执行部署暂停；远端入口/调度排空验收仍待完成。
- Ruff及文档检查通过；无Python业务/持久化变化，未重复前轮453项Python全量。源码哈希与范围见 infra/verification/write-admission-observation.json。TASK-112/113保持IN_PROGRESS，整体69项进行中/51项TODO不变，goal继续进行。


## 后续增量：结算 Jobs 停派配置与批次间停止

- 新增YTA_JOBS_DISPATCH=OPEN|PAUSED，默认OPEN、非法值启动失败。PAUSED的CLI仅查询队列，输出dispatch_status=PAUSED及空results；watch写paused心跳，健康探针明确dispatch_paused而非ready。设置独立于BFF入口开关，配置在启动时读取。
- CLI将SIGTERM/SIGINT停止事件绑定到Worker派发检查，tick入口、每条处理前和取得租约后的Provider边界均检查。越过最后派发检查的工作按在途处理，继续Provider读取、结算提交及已提交结果核对；停止后不开始后续记录，不为未派发任务新增attempt/backoff或伪造CANCELED。指令级竞争窗口和独立实例仍存在，不能宣称全局事务屏障或强制中止外部副作用。
- 新增10项验证，覆盖暂停时不触达执行依赖、5个非法配置、paused心跳/退出、真实PG Provider执行中停派仍提交当前结果且下一条未尝试、租约后停派不调用Provider/不记attempt、实际CLI watch子进程PAUSED→SIGTERM→退出0/stopped且零attempt。恢复Worker只处理剩余记录，原冻结事实和已结算结果不变。
- 完整真实PG Python463项通过（25.35秒，4条既有pandas警告，无跳过），Ruff、142文件格式、契约漂移和文档检查通过。重建Alpha Linux镜像并实际执行专用REPLAY停派Jobs：输出PAUSED/空results，前后结算attempt和outcome计数一致，镜像导入的Jobs哈希与源码一致；临时容器自动清理，正常服务和既有Worker配置未改动。
- .env.example、Jobs Compose配置和恢复Runbook同步更新。观察/镜像/源码哈希保存为 infra/verification/jobs-dispatch-observation.json。无TS/Web或数据库模式变化，未重复其套件及备份恢复；TASK-063/112/113仍IN_PROGRESS，整体69项进行中/51项TODO不变。真实Provider、跨实例排空、部署恢复与完整DoD仍待完成，goal继续进行。


## 后续增量：威胁模型与离线费用预算检查

- 推进 TASK-008，新增 docs/threat-model-and-budget.md，列出12项风险、信任边界、已有控制与待验证项，覆盖会话/CSRF/所有权、模型越权、未知执行、行情截点、不可变事实、工具隔离、SSRF、密钥、资源成本、恢复与停派。没有将本机控制等同于部署安全验收。
- 新增 scripts/check_cost_budget.py，只读调用方提供的单币种费用，使用 Decimal 汇总已发生费用与未结算承诺；80%预警、100%达到上限，未知费用保持缺失状态。月度上限未配置时明确 UNCONFIGURED；不获取账单、不自动停用供应商、不修改 pi 模型配置，也不授予实际支出额度。
- 13项专项测试通过，覆盖精确阈值、承诺费用、缺失数据、非法金额/月份/时区、重复字段及输入长度限制。示例实际执行退出1、UNCONFIGURED、complete=false，符合预期；未将未知费用当成完整零费用。观察、文档本地链接校验和源码哈希见 infra/verification/cost-budget-observation.json。
- Ruff、145文件格式和120任务文档图检查通过。此轮新增独立运维检查器及文档，未修改应用、数据库或TS/Web行为；未重复上一轮463项真实PG Python全量及TS/浏览器套件，13项专项不冒充全量结果。
- TASK-008保持IN_PROGRESS。真实账单核验、批准的月度额度、通知/执行机制、远端CI、staging及完整DoD仍待完成；真实行情授权与staging配置尚未确认，goal继续进行。


## 后续增量：五交易跨度离线标签与结算公式复用

- 推进 TASK-119 的标签部分，新增 Alpha labels.build_label，支持1/3/5/10/20个交易日。两份固定快照分别提供基准输入与未来结果，明确实验cutoff、同模式校验和EOD输入窗口；原始基准价不被结果侧修订替换。结果包含跨度、目标日/实际收盘时刻、两份快照哈希、价格/修订、拆股因子、收益方向及日历版本。
- 提取现有结算的 price_return 供标签与结算共用，保持Decimal50位、HALF_EVEN量化12位、拆股区间(base,target]与零方向规则。label_available_at保守取结果快照as_of，后来的修订不能倒填成早期已知标签。缺少目标日明确WAITING_DATA，未到期NOT_DUE，不以相邻日或复牌价格替代，不写预测事实。
- 新增27项测试，覆盖五跨度跨感恩节目标日、半日市、夏令时、正反拆股与边界、五日标签和现有结算精确一致、外部Decimal精度、舍入方向、非法跨度/布尔/浮点、模式/时点/公司行动缺失、输入窗口关闭、前价/复牌价拒绝及修订保留。首轮专项与结算45项通过；补充边界后完整真实PG Python503项通过（24.58秒，4条既有pandas警告，无跳过），包括前轮预算检查器。
- Ruff、147文件格式、Alpha契约漂移和任务图检查通过；观察及源码哈希见 infra/verification/multi-horizon-label-observation.json。无数据库模式、TS/Web或公开API变化，未重复TS/Web套件、备份恢复或重建镜像。
- TASK-119改为IN_PROGRESS。预测/冻结服务、Jobs、界面仍为五日MVP范围，训练切分与LightGBM尚未接入；不能据此宣称Beta五跨度全链路完成。真实行情与staging仍待配置、完整DoD仍未完成，goal继续进行。


## 后续增量：Walk-forward 时间切分与标签泄漏约束

- 推进 TASK-058，新增 Alpha walk_forward 模块和不可变 SplitConfig/Fold/SplitManifest。按显式有序测试窗口构建扩展训练集，只接收同证券/模式/跨度/日历的一份明确标签版本；检查交易日目标映射、实际收盘、输入EOD窗口及标签最早可用时间。重复基准日版本、缺失测试日、逆序/重叠窗口和训练不足均拒绝。
- 每折只考虑测试起点前的样本，purge 目标日达到或越过测试起点的标签，再排除训练截点尚未获知的标签。Embargo 明确定义为此前测试折最后目标日之后N个交易日产生的样本不得进入后续折训练；不删除测试样本，当前折只训练过去数据。历史测试样本满足时间与embargo条件后可用于后续折。
- 清单保存配置、完整标签集合哈希、日历、每折训练截点、训练/测试标签身份及三类排除原因。输入顺序变化不影响清单，修订改变样本与数据集身份；训练只使用合格ID，模块不读取行情、不写事实、不执行模型训练。
- 新增28项测试：五种跨度的严格边界、目标日等于测试起点时剔除、未来样本隔离、延迟结果先拒后准、获知时刻等于截点、跨交易日embargo与原测试样本后续可用、确定性/修订身份，以及不合法数据/配置拒绝。完整真实PG Python531项通过（24.16秒，4条既有pandas警告，无跳过）；Ruff、149文件格式、Alpha契约与120任务图检查通过。
- 观察及源码哈希见 infra/verification/walk-forward-observation.json；README记录精确切分约定与局限。此轮无数据库模式、TS/Web或公开API变化，未重复其套件、恢复演练或镜像构建。TASK-058改为IN_PROGRESS，前置训练模型、特征内容泄漏验证、真实数据实验、独立校准及完整DoD仍待完成，goal继续进行。


## 后续增量：训练特征、标签与时间切分绑定

- 推进 TASK-054/058 的训练数据准备，新增 training_data.prepare_dataset，将每条PriceLabel绑定到其确切base_snapshot_hash。核对模式、证券、as_of、基准交易日/价格/修订，重新运行原始快照的指标与连续交易日校验；拒绝无关结果快照、缺失/重复输入和哈希改变的证据。
- 按冻结列顺序取15项indicators-v3末日指标，构成不可变FeatureRow；指标预热/零分母缺失保持null，不补零、不丢样本、不做全数据归一化。PreparedDataset分别保存特征、标签与已核验Walk-forward清单，按基准日排序；整体哈希覆盖输入特征、标签版本和切分规则。尚未向训练库浮点矩阵转换或自动加载模型。
- 新增12项专项测试通过，验证指标数值/顺序/Decimal字符串/null、与样本ID绑定的训练及剔除记录、输入重排确定性、未来结果改变只影响标签不影响特征，以及证据缺失/重复/无关、输入元数据不匹配、交易日缺口和未来可获知时间拒绝。
- 完整真实PG Python543项通过（24.00秒，4条既有pandas警告，无跳过）；Ruff、151文件格式、Alpha契约漂移与任务图检查通过。观察及哈希见 infra/verification/training-data-observation.json；README补充用法和来源验证局限。
- 无公开API、数据库模式或TS/Web变化，未重复其套件、恢复演练或镜像构建。TASK-054/058保持IN_PROGRESS，TASK-057尚未实现模型训练/登记/推理，仍TODO；真实数据资格、五跨度服务与完整DoD仍待完成，goal继续进行。


## 后续增量：五跨度真实CPU实验训练、制品与重载推理

- 推进TASK-057，固定LightGBM4.6.0的training extra及开发依赖/uv锁文件；本机首次导入因缺少OpenMP失败，安装Homebrew libomp22.1.6后成功。现有在线Alpha镜像没有启用training extra，未更改pi LLM配置、模型供应商或正常服务；CI声明libgomp1安装但没有远端运行证据。
- 新增training.train_fold与train_cli train/predict。训练前从精确原始输入/结果快照重新构建特征、标签及时间切分，拒绝伪造标签；每次只训练一跨度的一折，CPU单线程、确定性列分箱、固定学习率和有界参数。测试集不进入训练Dataset、分箱、早停或调参；null→NaN、真实0保留。float64特征/float32内部标签为明确的近似学习边界，溢出/非零下溢拒绝；协议标签和评测仍以Decimal保留，预测非法收益不截断。
- JSON制品保存原生模型文本/哈希、训练配置/信息截点、数据集与源码哈希、精确训练/测试/排除样本、日历/模式/跨度及Decimal测试MAE/零收益对照。私有临时文件完成后原子发布到新路径、拒绝覆盖；加载需外部可信SHA256并核验内部模型文本及列顺序。推理校验模式/日历/信息截点/EOD窗口，固定EXPERIMENTAL且概率为null，没有冠军晋升或事实冻结权限。
- 新增14项测试，五跨度均实际训练出多棵树，保存/重载后推理等同原结果，文件权限0600、覆盖和篡改拒绝；重复训练同环境模型哈希一致，修改测试目标仅改变评测不改变模型/预测，伪造标签/非法数值和旧时点/跨模式推理拒绝。另用真实CLI分别训练并保存五份合成REPLAY制品，逐份新进程重载推理一致，错误可信哈希均退出1/EXPERIMENT_FAILED；私有输入/制品保留。
- 完整真实PG Python557项通过（26.56秒，4条既有pandas警告，无跳过）；Ruff、154文件格式、Alpha契约与任务图通过。观察、制品路径/哈希、五跨度收据与源码哈希见 infra/verification/lightgbm-training-observation.json。README链接官方4.6.0参数文档并说明跨系统/编译器不可据此承诺逐字节重现。
- 无数据库模式或TS/Web变化，未重复其套件、恢复演练或镜像构建。TASK-057改为IN_PROGRESS；目前训练/推理仅为本机实验入口，数据库模型登记、公开预测服务、五跨度冻结/Jobs/界面、独立校准、人工晋升及真实数据评测仍待完成。合成样本指标不是实际研究表现，goal继续进行。


## 后续增量：实验模型不可变数据库登记与恢复推理

- 推进TASK-061/057，新增ModelRegistry，复用既有model_versions表而不增加迁移。注册前检查可信外部制品SHA256、内部模型文本及特征列；配置保存完整实验模型JSON和原文件哈希，登记sha256为配置规范内容哈希，ID由内容生成，model_kind=TRAINED且制品固定EXPERIMENTAL。
- 相同版本并发/重试仅插入一条模型和一条model.experimental_registered审计，模型与审计同事务；私有数据库运行角色负责鉴权，审计actor_id=null并明确入口，不伪造用户确认。读取使用只读事务，检查ID/类型/配置哈希/嵌套模型；既有SELECT/INSERT权限和不可变触发器继续拒绝UPDATE/DELETE/TRUNCATE。
- train_cli新增register和predict-registered，连接只来自私有YTA_MODEL_DATABASE_URL，凭证不打印。数据库保存模型文本，因此源制品文件移走后仍可推理；不授予冠军/正式冻结权限，不增加浏览器或公开HTTP入口。备份域校验增加TRAINED登记及嵌套模型文本完整性检查，不需要原生训练库。
- 9项真实PG专项通过：8次并发登记只产生一版本一审计、审计失败回滚及重试、三种事实变更拒绝、实际CLI登记及删掉临时源文件后数据库推理一致、错误制品哈希拒绝、未知/baseline ID拒绝、外层哈希重算后嵌套模型篡改仍拒绝。
- 在全新UUID专用测试库运行完整真实PG Python566项通过（28.05秒，4条既有pandas警告，无跳过），实际备份恢复31表/29保护/6序列、46预测/20结算/9证据；采集原始表和投影一致，40有效采集/39有效投影共23日线重建，原有4份/1份异常仍拒绝。恢复库7个实验模型均经运行角色只读加载，35个测试预测与保存值一致，无需原制品文件。
- Ruff、156文件格式、Alpha契约与任务图通过；观察及源码哈希见 infra/verification/model-registry-observation.json。测试源库按流程清理，恢复库/私有日志备份保留；正常服务未重启，无TS/Web或数据库模式变化，未重建镜像或重复其套件。TASK-057/061保持IN_PROGRESS，在线预测/五跨度冻结结算、审批晋升、真实数据与部署验收仍待完成，goal继续进行。


## 后续增量：Alpha 私有实验模型预测接口及Linux容器验证

- 推进TASK-057，新增POST /internal/v1/experimental-models/{model_id}/predict与独立ExperimentalRequest/ExperimentalPrediction契约。默认YTA_EXPERIMENTAL_MODELS=DISABLED返回503；开启后仍需服务Bearer，工具专用Token拒绝。请求只接受快照及整数跨度，必须匹配部署模式和登记模型，不接受confirmed等额外授权字段。
- 校验服务器时间、最新收盘日和EOD输入窗口；仅development/test REPLAY可使用受服务器未来时间约束的回放as_of，LIVE按服务器当前窗口判断。复用数据库只读校验、模型训练截点/模式/日历检查和指标重算；缺失/错版LightGBM明确503，不回退baseline或fixture。响应含模型/快照哈希、交易日映射、Decimal收益/方向、EXPERIMENTAL状态及null概率，不含模型文本或草稿ID，不追加任何冻结/草稿/评测事实。
- 登记/HTTP专项11项通过，新增鉴权、默认关闭、跨度/类型/模式/额外字段拒绝、未知模型、未来输入、缺库503及预测/草稿/审计计数不变验证；另补3项服务器时钟边界，确认未来输入、下一开盘和新一日收盘过时在模型查询前拒绝。完整真实PG Python571项通过（31.00秒，4条既有pandas警告，无跳过）。
- 新增training-build/training-runtime Docker目标，固定依赖并安装libgomp1，保留正常runtime/tools目标行为；本机实际构建Linux训练镜像。独立只读/nonroot容器使用专用yta_test中的已登记合成REPLAY模型，经真实loopback HTTP验证匿名401、工具Token401、服务Token200，收益与既有制品完全一致；镜像安装模块哈希等于源码。容器和临时凭证文件已清理，正常服务未重启。
- 新增显式本地Compose覆盖文件和.env.example默认关闭配置，合并配置校验通过但未启动整套覆盖栈。Alpha OpenAPI已更新，漂移和规范校验通过；Ruff、157文件格式、任务图通过。观察/镜像/源码哈希见 infra/verification/experimental-api-observation.json。无TS/Web或数据库写入逻辑/模式变化，未重复其套件和备份恢复。
- TASK-057继续IN_PROGRESS。该接口属于内部实验预测，尚未接入BFF/用户界面、正式五跨度冻结结算、模型审批晋升或真实数据资格验收；不能视为完整Beta上线，goal继续进行。


## 后续增量：独立窗口单调概率校准与Brier/ECE报告

- 推进TASK-059，新增calibration模块和calibrate_cli。校准样本精确限定为模型预留fold.test，原模型该窗口的评测从此只能作校准诊断；最终评测必须晚于全部校准目标/embargo，并在首个特征时刻已获知所有校准标签。逐交易日完整性、来源快照、标签重算、模式/跨度/日历均校验，训练窗口不能冒充校准窗口。
- 使用预测收益为排序分数、UP=1且FLAT/DOWN=0，Decimal50位加权PAVA合并同分数及相邻违序块，节点间线性插值、域外端点概率，输出12位HALF_EVEN。拒绝单类/非法数据，不输出未经独立窗口拟合的概率。报告记录两段标签ID、模型/输入/代码哈希、节点、分数/目标、最终概率、Brier及10等宽箱ECE；空箱为null、空基础指标不伪报0，完整实验拒绝空窗口。
- 新增21项专项通过，覆盖精确加权/重复分数/插值、0/1分箱边界、空样本、单类/非法输入、实际CPU模型独立校准、最终目标改变不影响拟合/概率、目标重叠/embargo、延迟标签、缺失交易日和证据篡改/未来信息。首次测试发现零分数Decimal字符串用科学计数形式，与固定小数约定不同；统一报告格式后通过。
- 实际CLI以合成REPLAY数据生成5个校准/5个最终评测样本的报告，两个校准类别均存在；0600文件哈希与收据一致，重复运行拒绝覆盖且原报告不变。输入、模型和报告保留在私有.local目录，观察及源码哈希见 infra/verification/calibration-observation.json。小样本仅验证流程，始终EXPERIMENTAL/UNVERIFIED，不作为置信度或收益证据。
- 完整真实PG Python592项通过（28.90秒，4条既有pandas警告，无跳过）；Ruff、160文件格式、Alpha契约和任务图通过。无API/数据库/TS/Web变更，未重复其套件、备份恢复或镜像构建。TASK-059改为IN_PROGRESS，校准制品登记、在线概率、真实样本质量/跨期稳定性和完整DoD仍待完成；既有接口probability_up继续null，goal继续进行。


## 后续增量：校准报告不可变登记与恢复重放

- 推进TASK-059，迁移015新增独立calibration_records事实表，外键绑定已登记模型，保存完整CalibrationInput及报告；内容哈希/版本ID覆盖模型、快照、标签、截点与报告。数据库约束保持EXPERIMENTAL/UNVERIFIED并禁止UPDATE/DELETE/TRUNCATE，不混入正式预测评测，也不增加晋升或用户确认权限。
- 新增CalibrationRegistry及私有create/get CLI，使用YTA_MODEL_DATABASE_URL运行角色。创建从登记模型和输入证据重新生成报告，不接受调用方声明的指标；并发重复仅一记录一审计，报告/审计同事务且失败回滚。读取先校验身份/模式/模型，再从保存证据重算完整报告；保留历史code_sha256，不用当前代码哈希替换历史来源。CLI默认不输出原始快照。
- 7项真实PG专项通过，覆盖并发去重、审计故障原子回滚、三类事实修改拒绝、外层重算哈希后的假指标仍拒绝，以及实际CLI创建、删掉临时输入文件后从数据库读取重放。测试库应用015并按现有运行角色流程授予新表SELECT/INSERT；正常yta库未迁移。
- 新建UUID专用库运行完整真实PG Python599项通过（34.12秒，4条既有pandas警告，无跳过），实际备份恢复32表、30保护、6序列，46预测/20结算/9证据重现，采集与投影一致并维持原负例拒绝。恢复库两条校准记录经运行角色再次读取，均从数据库保存的模型/输入成功重算，最终评测各5样本；不依赖原本机文件。
- 重建migrator镜像，实际容器验证全新建库/升级、原证据保留、重复执行、角色边界及临时库清理通过。Ruff、162文件格式、Alpha契约和任务图通过。观察、恢复库/记录ID、镜像和源码哈希见 infra/verification/calibration-registry-observation.json；源测试库已清理，恢复库与私有日志/备份保留。
- README与恢复Runbook明确：包含校准记录的语义恢复验证需要匹配的训练依赖/OpenMP；缺依赖或不能重现不得跳过后宣称通过。无TS/Web或HTTP行为变化，未重复其套件或重建正常应用镜像。TASK-059保持IN_PROGRESS，在线概率、真实样本质量与独立Review未完成，既有API概率仍null，goal继续进行。


## 后续增量：显式校准版本的内部概率预测与时点边界

- 推进TASK-059，新增独立POST /internal/v1/experimental-models/{model_id}/calibrations/{calibration_id}/predict。沿用默认关闭开关及服务鉴权，要求模型/校准版本明确匹配；不存在/不匹配拒绝，不选最新、不回退。原无校准实验接口保持null概率，未修改正式预测/冻结契约。
- 登记读取新增真实created_at返回，保持原get载荷契约。输入as_of必须不早于整个校准报告cutoff及拟合标签可用时刻；防止在报告最终评测尚未知时使用该报告选定校准。非回放还要求不早于数据库真实登记时间。仅development/test REPLAY使用实验逻辑截点；继续检查标签/embargo区间和既有模型/环境/交易日/EOD窗口规则。
- 新响应CalibratedExperimentalPrediction要求概率、校准ID及可用时间，整体EXPERIMENTAL且probability_qualification固定UNVERIFIED。概率由已重放校准曲线作用于模型收益分数产生，Decimal字符串输出；没有草稿ID/确认权限，不追加预测、草稿、校准或审计事实。
- 新增3组端到端测试，登记/API专项合计10项通过：匿名/工具Token拒绝、明确模型匹配、默认关闭、未知版本、输入额外概率字段拒绝、原接口仍null、数据库事实计数不变，以及拟合后但报告前时点拒绝、真实登记前拒绝、报告cutoff等号边界允许。完整真实PG Python602项通过（37.34秒，4条既有pandas警告，无跳过）。
- 重建training-runtime镜像，独立非root/只读Linux容器实际HTTP200，完整响应与主机从相同PostgreSQL记录重放结果一致，安装模块哈希与源码一致；临时容器/凭证文件清理，正常服务未重启。Alpha OpenAPI更新且规范/漂移校验通过，必填响应字段核对通过；Ruff、163文件格式和任务图通过。
- 观察/镜像/源码哈希见 infra/verification/calibrated-api-observation.json。无TS/Web、数据库模式或写入行为变化，未重复其套件和恢复演练。TASK-059继续IN_PROGRESS，BFF/界面、真实概率质量、人工Review/晋升与完整DoD未完成，不将实验概率解释为已验证置信度，goal继续进行。


## 后续增量：未校准模型的真实登记时间边界

- 审查发现未校准实验接口只检查training_cutoff，没有检查模型实际登记时间；将服务器时钟固定在有效EOD输入时点、使用之后才登记的真实数据库模型，新增回归用例在修复前确实未抛错并失败。
- ModelRegistry新增读取created_at的方法，原get返回载荷保持兼容；未校准实验预测在非回放路径要求snapshot.as_of>=真实登记时间，否则MODEL_NOT_AVAILABLE。该时间来自数据库，不接受客户端覆盖；显式development/test REPLAY保持实验重现行为，校准路径也继续保留原有报告/登记可用性限制。
- 新增4个回归/边界用例，核对查询时间与数据库一致、前后1微秒及等号、明确REPLAY保留；模型与校准登记/API专项25项通过。完整真实PG Python606项通过（31.13秒，4条既有pandas警告，无跳过），Ruff、163文件格式、Alpha契约漂移和任务图通过。
- 观察及源码哈希见 infra/verification/model-availability-observation.json。没有数据库模式、写入逻辑、TS/Web或公开响应契约变化，未重复其套件/恢复演练，也未重建镜像；当前修复是已测试源码，尚未部署进此前容器镜像。整体74项IN_PROGRESS/46项TODO，尚无完整DoD通过项；真实集成与发布验收仍待完成，goal保持active。

## 后续增量：实验模型目录的分页与隔离

- 推进TASK-061，新增私有GET /internal/v1/experimental-models，沿用服务鉴权与默认关闭实验开关；服务端模式过滤、可选五种跨度、1–50条分页，游标限定相同模式/跨度，按真实登记时间和ID降序。分页不承诺并发写入下跨请求快照一致性。
- 只读事务、服务端游标逐条读取制品并校验登记/原生模型文本哈希；仅返回明确允许的元数据，不返回权重、特征或配置。保留窗口MAE以十进制字符串输出，metric_scope明确不是已验收最终评测，状态固定EXPERIMENTAL/UNVERIFIED。
- 新增3组真实PG测试，核对全部分页与数据库排序一致、无重复、响应字段允许清单、跨模式/跨度及未知游标拒绝、页大小/跨度校验、匿名/工具Token拒绝、默认关闭和客户端不能覆盖服务端模式。最初HTTP测试将现有DomainError的422误写为400，修正为检查422及INVALID_CURSOR错误码。
- 完整真实PG Python609项通过（32.45秒，4条既有pandas警告，无跳过）；Ruff、Alpha OpenAPI规范及契约漂移检查通过。契约导出已更新，观察及源码哈希见infra/verification/model-catalog-observation.json。
- 无数据库模式或写入逻辑变化，未重复备份恢复；无TS/Web源码变化，未重复其套件。未重建镜像，目录和前轮登记时间修复均尚未部署进已有镜像。BFF/页面、真实数据评测及完整Review/发布验收仍待完成；74项IN_PROGRESS/46项TODO不变，goal保持active。

## 后续增量：BFF 实验模型目录与跨语言响应验证

- 为TASK-061模型版本查询补充已登录GET /api/yta/v1/experimental-models。BFF默认关闭实验开关，鉴权后才访问Alpha；目录是共享系统元数据，不改变归属用户的研究/预测权限。客户端不能指定模式/所有者，查询严格限定分页及跨度。
- 新增Zod请求/响应契约，保留Decimal字符串和EXPERIMENTAL/UNVERIFIED/保留窗口指标口径；验证身份哈希、允许字段、页大小、模式、跨度、重复ID和游标一致性/前进。上游不合规时返回固定502错误，不透传权重或额外字段。BFF OpenAPI与请求契约导出已同步，观察路由不记录查询值。
- 新增3项契约测试和3组真实PG BFF集成测试：使用Python真实目录输出的REPLAY元数据样例，核对登录、默认关闭、查询拒绝、两位已登录用户读取共享目录、异常上游拦截。完整pnpm test共103项通过（contracts22、BFF81），数据库集成未跳过；pnpm typecheck/build、请求/BFF契约漂移检查通过。
- 另启动实际Alpha进程与构建后的BFF连接yta_test：匿名401、真实登录后目录200、返回内容与直接Alpha响应完全一致、第二页无重复，注销200；进程按finally清理。该实测使用本地合成REPLAY模型，不证明真实行情或云端部署。
- 本地实验Compose覆盖文件同时为BFF/Alpha打开开关，合并配置验证通过；README记录使用与边界。Ruff、Alpha契约和120任务文档图检查通过。无Python业务、数据库或UI源码变化，未重复609项Python全量、数据库恢复及浏览器套件，未重建容器镜像。
- 观察与源码哈希见infra/verification/model-catalog-bff-observation.json。模型页面、真实数据评测、人工晋升及完整Review/发布验收仍待完成；74项IN_PROGRESS/46项TODO不变，goal继续active。

## 后续增量：实验模型目录与登记边界的实际镜像验证

- 重建Alpha training-runtime与BFF生产包镜像，纳入此前模型登记时间修复、Alpha目录、BFF鉴权/契约接入。实际镜像摘要及模块哈希保存于infra/verification/model-catalog-container-observation.json；不改写此前旧镜像证据。
- 新增scripts/check_model_catalog_containers.py，使用既有专用yta_test与合成REPLAY校准记录，启动独立非root、只读根、drop ALL能力、无宿主机映射端口的512MB/1CPU容器。验证匿名401、真实登录目录200、与直接Alpha响应完全一致、第二页非空且无重复、客户端覆盖模式422，并注销测试会话。
- 在安装后的Alpha包上验证实际数据库登记时间：历史非回放输入返回MODEL_NOT_AVAILABLE；通过实际HTTP执行REPLAY原生LightGBM推理，结果与同库同版本直接推理完全一致。Alpha四个源码模块及BFF三个编译模块哈希与当前本机实现一致。
- 初次完整默认BFF入口演练通过；随后将可重复脚本改为从打包的createBff关闭研究恢复worker启动并再次通过，避免共享测试库中无关Run被后续演练恢复。两次容器均已清理；正常服务未启动，保留原有PostgreSQL与无关容器。脚本会追加合成登录/注销事实，不宣称数据库完全无写入。
- Ruff、165文件格式、Alpha契约漂移检查通过；本轮没有应用业务或数据库模式变更，未重复此前103项TS和609项Python全量、浏览器与恢复套件。README补充可重复验证命令及前置条件。
- 仅证明本机Linux arm64打包与实验HTTP链路，未证明云端staging、真实行情资格、模型质量或正式发布。页面、人工晋升和完整Review仍待完成，任务统计74项IN_PROGRESS/46项TODO不变，goal继续active。

## 后续增量：实验模型目录页面与移动端验证

- 推进TASK-083，新增/models页面：跨度筛选、前后分页、训练/保留窗口样本数、模型与零收益对照的原始Decimal误差字符串、完整模型身份/哈希及UTC时间展开。遵循现有配色和排版，以交易日跨度标记版本，不把不同窗口指标排名或误称最终评测。
- BFF /me增加experimental_models_enabled布尔能力字段，仅开启时显示导航；关闭时直接访问页面有明确说明。浏览器只调用BFF，严格校验响应模式/跨度/页大小/游标，处理加载、空、失败重试与登录失效。筛选变化中止旧请求，过期结果不能覆盖当前筛选。
- 新增4项Playwright场景：默认关闭、桌面/390px手机分页筛选与完整身份、错误模式拒绝及重试、晚到结果隔离。初轮测试使用import.meta触发当前CommonJS测试加载错误，修正路径；随后发现重复登录触发真实限流和alert定位与Next播报元素冲突，改为复用测试会话及main内定位，没有降低鉴权/限流。初轮限流造成后续依赖研究样本的用例连锁失败，修正后完整26项浏览器测试通过（30.3秒）。
- 更新Swagger浏览器验证，按实际已公开契约计数，避免新增API后固定26个操作的旧断言失效。103项TS测试通过（contracts22/BFF81，真实PG启用），pnpm typecheck/build、BFF请求/OpenAPI与Alpha契约漂移、Ruff通过。桌面与手机截图已实际检查，390px没有页面横向溢出。
- 证据及截图副本见infra/verification/model-catalog-ui-observation.json。无Python业务/数据库模式变更，未重复609项Python或备份恢复；未重建本轮BFF能力字段/Web页面镜像。UI场景采用真实登录及受控目录响应，前轮真实跨服务/容器目录证据独立保留，不混称端到端真实模型页面测试。
- TASK-083改为IN_PROGRESS，当前75项进行中/45项TODO，仍无完整DoD通过项。候选/冠军分布、晋升工作流、真实数据评测、staging及完整Review待完成，goal保持active。

## 后续增量：无响应替换的模型页面容器端到端验证

- 重建当前BFF和Web镜像，纳入/me能力字段、实验模型导航及/models页面；Alpha使用上一轮已核验的training-runtime镜像。实际三镜像摘要见infra/verification/model-catalog-browser-observation.json，不据旧镜像推断页面已部署。
- 新增scripts/check_model_catalog_browser.mjs，以独立Compose项目启动Web→BFF→Alpha→真实yta_test；没有Playwright路由替换或Mock响应。BFF关闭研究恢复worker，不启动Jobs。三服务非root、只读根、drop ALL能力，只有Web映射127.0.0.1:3300；实际确认Web无服务/数据库凭证且不能解析Alpha/PostgreSQL地址。
- 使用随机合成账号真实登录，服务端能力为true，浏览器导航进入模型页面；前两页各10条ID与数据库按登记时间/ID排序的前20条逐项一致。筛选5交易日返回10条REPLAY记录，完整身份和原始十进制误差在页面可见；390px手机无文档横向溢出，pageerror为0，所有浏览器请求同源且没有internal路径。
- 桌面和手机真实截图保存在.local/model-catalog-browser/，已检查。脚本正常注销会话，finally清理演练容器/独立网络和临时配置；原有PostgreSQL和无关服务保持运行。脚本追加测试身份/登录事实，不改模型、预测或结算事实。
- Node语法、Ruff和120任务文档图检查通过。本轮无应用业务代码变化，未重复前轮103项TS、26项浏览器受控响应套件、609项Python或数据库恢复；本次证据为新增的实际Linux容器页面链路，不等同云端staging或真实行情/模型质量验收。
- TASK-083及整体goal继续进行中，75项IN_PROGRESS/45项TODO不变；候选/冠军最终评测比较、审批晋升、真实供应商及发布Review仍待完成。

## 后续增量：独立评测窗口的价格指标与同样本比较

- 推进TASK-059/064的实验模型评测依据，新增model_evaluation只读模块与CLI。从已登记校准记录开始重放证据及窗口隔离，只使用evaluation_ids对应的后续独立样本，重新执行原生LightGBM预测；不把原模型保留窗口或校准拟合样本混入最终窗口。
- 使用Decimal50/12位HALF_EVEN计算MAE、方向命中率，并提供同样本零收益对照。五日跨度同时重放momentum20-v1；其他跨度明确为null/FIVE_SESSION_ONLY，不外推现有五日规则。空统计为null，非Decimal、非有限、越界及未对齐输入拒绝。
- 报告保留模式/跨度/日历/信息截点、精确label_ids、逐样本输入和目标快照哈希/收益/时间、模型与校准身份、计算源码哈希及内容哈希。比较2–10个不同校准版本时要求cohort_hash完全一致；日期相同但目标事实修订或cutoff不同均拒绝。所有结果仍EXPERIMENTAL/UNVERIFIED，无自动选择或晋升。
- 新增7项Decimal输入/精度测试、3组真实PG回归：独立窗口成员和校准成员互斥、指标独立核算、模型/零收益/五日基线同样本、读取不追加审计/评测事实、相同集合比较成功、截点/目标修订拒绝、CLI复现与固定错误码。专项20项通过，最终完整真实PG Python619项通过（34.02秒，4条既有pandas警告，无跳过）。
- 另实际执行CLI并保存.local/model-price-evaluation-report.json，五个合成REPLAY评测样本完成三种价格指标计算；该小样本不证明真实预测能力或达到晋升门槛。观察及源码哈希见infra/verification/model-price-evaluation-observation.json。
- Ruff、167文件格式、Alpha契约漂移通过。无数据库模式、事实写入、HTTP或TS/Web修改，未重复数据库恢复、TS/浏览器套件或镜像构建；新模块尚未进入前轮训练镜像。报告登记/API/展示、正式质量门槛和人工审批仍待完成，75项IN_PROGRESS/45项TODO不变，goal继续active。

## 后续增量：不可变实验价格评测登记与恢复

- 推进TASK-059/064，新增迁移016及ModelEvaluationRegistry私有create/get CLI。报告以完整内容身份登记，外键引用校准与模型版本，强制EXPERIMENTAL/UNVERIFIED及模式一致；缺少必要JSON身份字段也被数据库CHECK拒绝。并发幂等登记只追加一份报告和一次审计，审计失败整笔回滚。
- 运行角色只允许SELECT/INSERT，immutable_fact触发器拒绝UPDATE/DELETE/TRUNCATE。读取验证内容身份、引用与模式，并重放校准原始证据和独立窗口预测/价格指标；不能通过重算外层哈希掩盖错误指标。历史源码哈希保留，不要求伪装为当前源码，但所有计算语义必须相等。CLI无审批权限，审计actor_id=null不构成人工批准。
- 新增8项真实PG用例，覆盖4路并发、审计回滚、三种修改拒绝、指标篡改重哈希拒绝/历史源码哈希兼容、CLI登记和重放、缺失JSON身份拒绝；登记专项21项通过。
- 首次恢复演练发现驱动返回的DSN省略密码，重建连接失败；提取evaluate_verified_evidence计算入口，使恢复检查直接使用现有连接读取并验证的校准/模型证据，避免重建凭证连接。失败未计为恢复通过；修正后重新执行全新测试库的完整Python627项通过（36.57秒，4条既有pandas警告，无跳过）。
- 实际备份恢复33张表、31个不可变保护和6个身份序列通过，46预测/20结算/9证据重现，采集投影及异常拒绝检查通过。恢复库中7份实验价格报告使用运行角色逐一读取并重放成功，不依赖原本机报告文件。测试源库自动清理，恢复库与私有日志/备份保留。
- migrator镜像已重建，真实容器的全新001–016、升级、重复执行、已有证据保留及角色隔离检查通过；正常yta应用库未迁移。Ruff、168文件格式、Alpha契约漂移检查通过。README及恢复Runbook更新，证据见infra/verification/model-evaluation-registry-observation.json。
- 无TS/Web或HTTP变更，未重复其套件；未重建Alpha训练/BFF/Web镜像。评测报告API/页面、候选/冠军比较、人工审批及真实样本质量/发布验收仍待完成，75项IN_PROGRESS/45项TODO不变，goal保持active。

## 后续增量：私有实验评测读取接口与精确响应契约

- 新增GET /internal/v1/experimental-evaluations/{evaluation_id}，仅服务鉴权、沿用默认关闭实验开关；工具Token拒绝。ModelEvaluationRegistry.get支持可选服务端模式过滤，未知ID/其他模式统一404，模式检查发生在原生重放之前；原CLI不传过滤参数的兼容行为保留。
- 新增严格ExperimentalPriceReport及指标/样本/代码哈希子契约。时间字段验证显式时区但保留原始字符串，不自动规范化为Z；Decimal仍为字符串，指标别名与登记报告一致，HTTP JSON可再次核对原content_hash。实际读取继续重放原始证据和指标，库不可用返回503，没有fixture回退或写入行为。
- 新增3组真实PG/HTTP用例，覆盖匿名/工具Token拒绝、默认关闭、未知版本、完整返回等于登记载荷、重新计算内容哈希、客户端模式参数不能覆盖服务端、读取审计数量不变、跨模式在重放之前拒绝、原生库不可用没有metrics。专项24项通过；完整真实PG Python630项通过（38.96秒，4条既有pandas警告，无跳过）。
- Alpha OpenAPI导出更新，BFF文档依赖的组件同步导出；22项共享契约测试、Alpha/BFF OpenAPI规范及漂移检查、Ruff、169文件格式通过。观察与源码哈希见infra/verification/model-evaluation-api-observation.json。
- 无数据库模式/写入逻辑或BFF/Web业务源码变化，未重复备份恢复、81项BFF或浏览器套件，未重建镜像。BFF报告读取、报告发现/比较页面和审批工作流仍待接入；75项IN_PROGRESS/45项TODO不变，goal继续active。

## 后续增量：BFF固定实验评测读取与内容核验

- 新增已登录GET /api/yta/v1/experimental-evaluations/{id}，沿用默认关闭实验开关；拒绝非法ID、额外查询/模式覆盖，传递会话认证所有者。报告属于共享系统实验数据，无写入、冻结或晋升能力。
- 共享Zod报告契约明确实验/历史范围、模型/校准身份、精确样本清单、价格指标和源码哈希；核对样本顺序/数量、基线适用跨度、重复样本、方向命中率范围和Decimal字符串。BFF使用与Python一致的固定ASCII键排序及原始字符串，验证报告内容/集合哈希、请求ID和服务端模式；不重算金融指标。
- 新增2项共享契约测试、2项BFF跨语言内容校验测试和1组真实PG登录/HTTP回归，采用Python从真实登记表读取的合成REPLAY报告fixture。验证原始时间文本保持、指标/哈希/时间/身份篡改拒绝、额外字段不泄露、禁用状态无上游请求、日志仅记录路由形状。完整TS108项通过（contracts24/BFF84，真实PG启用），pnpm typecheck/build通过。
- 实际启动当前Alpha与构建后的BFF连接yta_test，真实登录后读取登记报告200，BFF/Alpha响应与登记报告完整相等；测试同时保持匿名401、注销200和目录分页检查，finally清理进程。证据为本机真实进程/合成REPLAY，不是新容器镜像或真实行情资格。
- 请求/BFF OpenAPI更新（报告ID使用专用schema），规范与漂移检查通过；Ruff及Alpha契约检查通过。证据见infra/verification/model-evaluation-bff-observation.json。无Python业务/数据库变化，未重复前轮630项Python、数据库恢复或浏览器套件；未重建镜像。
- 报告发现/比较页面、候选/冠军审批和正式质量/发布验收仍待完成；75项IN_PROGRESS/45项TODO不变，goal保持active。

## 后续增量：按模型发现实验评测的分页接口

- 新增私有GET /internal/v1/experimental-models/{model_id}/evaluations，服务鉴权、默认关闭实验开关、服务端模式及模型双重范围过滤。未知/其他模式模型404；游标必须属于同一模型/模式；1–50条分页按登记时间/ID倒序，空列表明确null游标，不宣称并发插入下跨页快照一致性。
- 只读事务和服务端游标逐条读取，严格解析报告并核对记录ID、内容/样本集合哈希及模式。输出仅含发现报告所需的身份、哈希、登记/信息截点、跨度与样本数；不返回价格指标或权重，不在翻页时重复执行原生预测。列表不替代单份详情的完整语义重放，资格始终UNVERIFIED。
- 新增2组真实PG/HTTP测试：多页与数据库完整排序一致、无重复、空模型列表、跨模型/未知游标拒绝、跨模式模型拒绝、边界limit、匿名/工具Token拒绝、默认关闭及客户端模式参数不能覆盖服务端。完整真实PG Python632项通过（39.76秒，4条既有pandas警告，无跳过）。
- Alpha OpenAPI及BFF文档依赖组件导出更新，24项共享契约测试、Alpha/BFF规范与契约漂移、Ruff、170文件格式通过。证据见infra/verification/model-evaluation-catalog-observation.json。
- 无数据库模式/写入逻辑或BFF/Web业务变更，未重复数据库恢复、BFF/浏览器全套或镜像构建。BFF列表、报告详情/比较页面、审批与正式验收仍待完成；75项IN_PROGRESS/45项TODO不变，goal继续active。


## 后续增量：BFF评测发现与报告详情页面

- 新增已登录GET /api/yta/v1/experimental-models/{model_id}/evaluations，默认关闭实验开关。严格限定模型/分页查询，验证响应身份、模式、页大小、重复项及游标前进；异常上游返回固定错误，不输出额外字段。共享Zod契约、请求导出和BFF OpenAPI同步。
- 模型目录新增独立窗口评测入口，列表按模型分页；详情展示同样本模型/零收益/适用的五日动量MAE与方向命中率、逐样本结果、报告/模型/校准身份及代码/快照清单。原始Decimal字符串不改精度；明确历史实验和UNVERIFIED，无排名、审批或晋升行为。浏览器仅通过BFF，支持禁用、加载、错误重试及401登录跳转，旧请求取消。
- 初次测试因使用系统Python生成fixture失败导致缺失文件，未计为通过；改用项目uv环境读取真实登记的合成REPLAY列表/报告后，完整TS110项通过（contracts25、BFF85，真实PG启用），pnpm typecheck/build通过。
- 完整Playwright28项通过（30.2秒），新增桌面/390px手机报告列表进入详情、三组指标/逐样本证据、失败重试及跨模式/错误身份拒绝。截图已人工检查并保留.local/model-report-pages/；手机表格区域可横向滚动，页面整体无横向溢出。UI测试使用真实登录与受控报告响应，不混称实际供应商端到端验收。
- 另启动实际Alpha与构建后的BFF连接yta_test，真实登录后报告列表与直接Alpha完全一致，下一页非空且无重复，详情与登记载荷完全相等；匿名401、注销200，finally清理进程。仅追加合成测试身份/登录事实。观察记录见infra/verification/model-evaluation-pages-observation.json。
- BFF/Alpha契约漂移、Ruff、120任务文档图检查通过。无Python业务或数据库模式变化，未重复前轮632项Python、备份恢复或镜像构建。新的列表与页面尚未进入此前容器镜像；候选/冠军分布、审批、真实数据资格和正式发布验收仍待完成。75项IN_PROGRESS/45项TODO不变，goal保持active。


## 后续增量：实验评测页面的真实容器链路

- 重建Alpha training-runtime、BFF与Web镜像，纳入实验评测计算/登记/读取/发现接口和报告页面。实际运行镜像摘要记录于infra/verification/model-evaluation-container-browser-observation.json；不以此前目录镜像代表本轮部署。
- 扩展scripts/check_model_catalog_browser.mjs，在独立Compose项目执行真实Web→BFF→Alpha→yta_test。无路由替换或Mock响应；真实登录后从按模型报告列表进入详情，完整HTTP载荷与数据库登记JSON逐项相等，三组指标与五条样本、原始Decimal及快照哈希在页面可见。并保留此前目录20条数据库排序/跨度筛选验证。
- 桌面1440px和手机390px截图已检查并保存.local/model-catalog-browser/report-*.png。页面无整体横向溢出、pageerror为0，浏览器所有请求同源且无internal路径。容器非root/只读根/drop ALL，只有Web映射本机端口；Web无Alpha/数据库凭证且无法解析两者地址。
- 演练关闭研究恢复worker且未启动Jobs，注销测试会话后清理容器/独立网络及临时配置。原有PostgreSQL和无关容器保持运行；新增合成账号/登录事实保留，不修改模型或预测事实。
- 脚本语法、Ruff与120任务文档图检查通过。本轮没有应用业务或数据库模式变更，未重复前轮110项TS、28项浏览器受控响应套件、632项Python及备份恢复。新增证据证明本机打包后的实际报告链路，不证明云端staging或真实预测质量。审批晋升、真实行情资格及完整Review/发布验收仍待完成，75项IN_PROGRESS/45项TODO不变，goal继续active。


## 后续增量：显式登记报告的同样本比较

- ModelEvaluationRegistry新增compare及CLI compare --id重复参数，严格要求2–10份不同已登记报告；按明确ID逐份调用模式限定读取及完整语义重放，不以当前新生成报告替换旧报告。保留输入顺序、原始载荷、历史源码哈希和报告身份；要求cohort内容/哈希、协议及收益口径一致，无排序、自动晋升或写入行为。
- 新增私有POST /internal/v1/experimental-evaluations/compare和严格请求/响应契约，仅服务鉴权且默认关闭。客户端模式/其他字段拒绝，未知或跨模式404，原生库不可用503，不返回部分结果或fixture替代。
- 新增2组真实PG测试，覆盖两份不同模型身份的同样本报告/反向输入顺序、CLI一致性、读取无新增审计、不同截点、数量/重复/格式拒绝、鉴权/工具Token/默认关闭/模式/原生库不可用；扩展既有目标事实修订测试，确保相同日期但修订标签的已登记报告也不能比较。模型样例为合成REPLAY，变体模型使用不同来源身份但相同原生权重，不据此宣称模型质量差异。
- 首轮专项27通过/1失败，原因是测试通过model_copy写入字符串模式绕过枚举验证；修正测试为DataMode枚举后，完整真实PG Python634项通过（42.67秒，4条既有pandas警告，无跳过）。25项共享契约测试、Alpha/BFF OpenAPI规范及漂移、Ruff、170文件格式检查通过。
- 证据见infra/verification/model-evaluation-comparison-observation.json。无数据库模式或写入逻辑变化，未重复备份恢复；无BFF/Web业务变更，未重复其全量或浏览器套件，也未重建本轮Alpha镜像。BFF多报告比较、页面选择、候选/冠军审批、真实数据质量与发布Review仍待完成，75项IN_PROGRESS/45项TODO不变，goal保持active。


## 后续增量：BFF显式报告比较与跨语言核验

- 新增已登录POST /api/yta/v1/experimental-evaluations/compare，沿用默认关闭实验开关和同源/CSRF校验；严格2–10个不同登记ID，不接受额外查询/模式覆盖。作为只读计算不使用研究写入准入开关，也不提供审批或晋升权限。
- 新增共享比较请求/响应Zod契约、请求导出及公开OpenAPI。BFF逐项核对请求顺序、报告集合、共同cohort_hash和每份已登记报告的内容/样本哈希及服务模式；上游遗漏、重排、重复、额外字段、错误哈希或模式拒绝。仍由Alpha负责金融计算和原始证据重放，BFF不重算指标。
- 从真实登记表导出合成REPLAY比较fixture。新增共享契约、BFF内容核验及真实PG登录HTTP回归，检查正常比较、匿名/CSRF/来源拒绝、非法请求不访问Alpha、固定502不泄露载荷、默认关闭无上游调用。完整TS113项通过（contracts26/BFF87，真实PG启用）；pnpm typecheck/build通过。
- 实际启动当前Alpha和构建后的BFF连接yta_test，真实登录比较200，BFF与直接Alpha、已登记报告集合完全一致，目录/详情验证保持通过；注销200并finally清理进程。观察见infra/verification/model-evaluation-comparison-bff-observation.json。
- BFF OpenAPI规范、Alpha/BFF契约漂移、Ruff及120任务文档图检查通过。无Python业务/数据库/UI变化，未重复前轮634项Python、备份恢复和浏览器套件；未重建容器镜像。页面多报告选择、候选/冠军审批、真实数据及完整Review仍待完成，75项IN_PROGRESS/45项TODO不变，goal继续active。


## 后续增量：跨模型报告选择与比较页面

- 新增/models/compare，模型评测列表支持加入/移出比较并进入已选列表。最多10份，可跨模型选择；会话存储仅保存登记ID，按认证账号和数据模式隔离，严格校验缓存身份/数量/重复，拒绝无效缓存。刷新保留当前标签页选择，支持移除/清空，存储失败明确提示。
- 用户显式点击后通过BFF/CSRF比较，检查报告顺序、集合和模式，展示共同样本上的模型与适用基线MAE/方向命中率、样本数、截点及完整身份，原始Decimal不改精度。比较期间锁定选择、离开页面中止请求、更改选择清除旧结果；失败保留选择，样本不匹配给出重新选择指引，401返回登录。默认关闭时不请求实验报告，无自动排名或审批。
- 新增4项浏览器场景，覆盖跨模型选择/导航/刷新、精确请求与指标、桌面和390px手机、错误截点提示/重排/跨模式响应拒绝及重试、默认关闭、账号和模式隔离。首轮测试在导航完成前刷新导致停留旧列表，修正等待页面就绪；下一轮发现手机完整身份字段撑宽页面，增加长字段换行后最终完整32项通过（32.7秒）。桌面/手机截图已检查并保存.local/model-comparison-ui/。
- 完整TS113项通过（contracts26/BFF87，真实PG启用），pnpm typecheck/build、Alpha/BFF契约漂移、Ruff、文档图检查通过。本轮无Python业务或数据库变更，未重复634项Python/备份恢复。浏览器采用真实登录与受控登记报告响应，前轮实际BFF/Alpha/PG比较证据独立保留；尚未重建比较页面镜像，不混称容器端到端验证。
- 观察与源码哈希见infra/verification/model-evaluation-comparison-ui-observation.json。候选/冠军正式评测分布和审批晋升、真实数据质量与完整Review/发布仍待完成。75项IN_PROGRESS/45项TODO不变，goal继续active。


## 后续增量：跨模型比较的实际容器页面验证

- 重建当前Alpha training-runtime、BFF和Web镜像，纳入已登记报告比较接口与/models/compare页面。运行时镜像摘要保存在infra/verification/model-comparison-container-browser-observation.json，不沿用旧镜像部署结论。
- 扩展scripts/check_model_catalog_browser.mjs，以独立Compose项目完成真实Web→BFF→Alpha→yta_test，无浏览器响应替换。分别从两个模型列表加入报告，导航到比较页并刷新保留两项选择；显式POST报告ID顺序与所选一致，完整返回等于数据库两份登记载荷及预期比较集合。报告为合成REPLAY、不同来源身份但相同原生权重，不把相同指标解释为真实模型质量结论。
- 页面可见六行模型/适用基线指标、原始Decimal及共同样本哈希；桌面1440px/手机390px截图已检查。移除首份报告后旧比较结果清除且不足两份时按钮禁用；pageerror为0、无页面整体横向溢出、请求均同源。保留模型目录分页/筛选、单报告详情、Web凭证与DNS隔离、非root/只读根/drop ALL检查。
- 测试注销会话，finally清理独立容器/网络和临时配置；原PostgreSQL及无关容器保持运行。仅追加合成账号/认证事实，不修改模型或预测事实。截图保存在.local/model-catalog-browser/comparison-*.png。
- Node语法、Ruff和120任务文档图检查通过。本轮无应用业务/数据库变更，未重复前轮113项TS、32项浏览器受控响应测试、634项Python或数据库恢复。新增证据证明本机打包后的实际链路，不证明云端staging、真实行情或正式晋升质量。审批晋升和完整Review/发布验收仍待完成，75项IN_PROGRESS/45项TODO不变，goal继续active。


## 后续增量：不可变候选模型提案与恢复

- 推进TASK-067，新增迁移017及内部ModelProposalRegistry.create/get。提案固定候选报告、可选参考报告、提出者、理由及请求UUID；模型/报告/样本哈希由完整重放证据派生。参考模型必须不同且同样本/截点；可不选参考模型，保留候选报告自身基线。状态固定SUBMITTED/UNVERIFIED，尚无审批、冠军指派或对外HTTP能力。
- 同提出者/模式/请求ID幂等，正文不同409；4路并发同请求仅一份记录/一次提出者审计，审计失败事务回滚。读取先做所有者/模式过滤，再重放引用报告；运行角色SELECT/INSERT，owner和runtime均不能通过UPDATE/DELETE/TRUNCATE覆盖事实。备份恢复校验提案哈希、请求及证据绑定。
- 新增7项真实PG用例，涵盖并发/重试/冲突、所有者/模式、参考模型/截点拒绝、仅基线对照提交、三种不可变保护、审计失败回滚、重算外层哈希后篡改状态/模型/提出者仍拒绝。专项35项通过；全新专用测试库完整Python641项通过（48.99秒，4条既有pandas警告，无跳过）。
- 实际备份恢复34张表、32个不可变保护、6个身份序列，46预测/20结算/9证据和6份提案核验通过；采集/投影及异常拒绝恢复检查通过。随后以运行角色逐一读取重放恢复库6份提案成功。源测试库自动清理，恢复库/私有备份保留；正常应用yta库未迁移。
- migrator镜像重建，实际新建001–017、升级、重复执行、已有证据保留和角色隔离验证通过。Ruff、171文件格式及Alpha契约漂移通过。无TS/Web/HTTP变更，未重复其套件或重建应用镜像。观察见infra/verification/model-proposals-observation.json。
- TASK-067改为IN_PROGRESS，现76项进行中/44项TODO，仍无完整DoD验收通过项。认证会话提案入口、正式质量门槛、人工审批、冠军切换/回滚和真实数据/发布验收继续待办，goal保持active。


## 后续增量：提案私有创建/读取API与响应契约

- 新增POST /internal/v1/model-proposals与GET /internal/v1/model-proposals/{uuid}，服务鉴权、工具Token拒绝、默认关闭实验开关。沿用可信BFF的X-YTA-Owner-ID头派生提出者，创建要求UUID Idempotency-Key。严格正文不接受owner/data_mode/model/status注入；读取限定提出者和服务端模式，未知/跨用户/跨模式404。
- 新增ProposalPayload/ProposalResponse严格Pydantic契约，保留原始正文哈希及登记时间。创建/同键重试200且完全同记录，同键不同理由409；响应仅SUBMITTED/UNVERIFIED，不代表审批。原生库不可用503无替代提案，客户端查询参数不能覆盖服务端模式。
- 新增2组真实PG/HTTP回归：匿名/工具Token拒绝、缺失/错误所有者及幂等键验证、正文敏感字段拒绝、响应内容哈希核验、同键无重复记录/审计、不同正文冲突、跨用户/模式与未知读取、默认关闭和库不可用不新增事实。完整真实PG Python643项通过（50.04秒，4条既有pandas警告，无跳过）。
- Alpha OpenAPI及BFF文档依赖组件更新，26项共享契约测试、Alpha/BFF规范与漂移、Ruff、171文件格式通过。观察见infra/verification/model-proposals-api-observation.json。无数据库模式/事务规则变化，未重复前轮备份恢复；无BFF/Web业务变更，未重复其全套和浏览器验证，未重建镜像。
- BFF会话派生/CSRF/写入准入、公开页面、质量门槛、审批和冠军切换/回滚仍待实现；不得将服务头可用等同用户入口已验收。76项IN_PROGRESS/44项TODO不变，goal继续active。


## 后续增量：BFF会话绑定的提案创建与读取

- 新增POST /api/yta/v1/model-proposals与GET /api/yta/v1/model-proposals/{uuid}。所有者从认证会话派生；创建要求同源/CSRF、UUID幂等键并受写入准入控制，实验开关默认关闭。键大小写规范化为同一UUID，拒绝客户端所有者/模式/审批字段及额外查询。
- 共享Zod请求/响应契约明确SUBMITTED/UNVERIFIED，校验参考报告/模型的空值组合和引用哈希。BFF核对响应所有者、模式、请求键、候选/参考/原始理由及完整内容哈希，读取还核对请求ID。无金融重算或审批权限；异常上游固定502，不输出额外字段。
- 新增真实Python登记的中文/emoji理由fixture及4项共享/BFF测试，覆盖Unicode内容一致性、2000字符界限、错误身份/模式/状态/请求/哈希、会话/CSRF/同源、幂等键传递、默认关闭及写入暂停。首轮回归误将新增POST根路径加入GET查询检查，修正为详情路径后，完整TS117项通过（contracts27/BFF90，真实PG启用）。pnpm typecheck/build通过。
- 实际启动Alpha及构建后的BFF连接yta_test，真实登录创建/同键重试/读取200且响应完全一致，与直接Alpha内容一致。数据库核对同owner/request仅1份提案、1次提交审计；原报告/比较链路保持通过。注销200并finally清理进程，保留合成提案/用户事实。
- BFF OpenAPI包含专用UUID键及写入暂停说明；规范、Alpha/BFF契约漂移、Ruff/文档图检查通过。观察见infra/verification/model-proposals-bff-observation.json。无Python业务/数据库模式变化，未重复643项Python或备份恢复；无页面变化，未重复浏览器套件，未重建容器镜像。
- 页面提案入口、质量门槛、人工审批与冠军切换/回滚仍待完成；76项IN_PROGRESS/44项TODO不变，goal继续active。


## 后续增量：候选提案提交/详情页面与响应丢失恢复

- 新增/models/propose/{candidate}/{reference}表单（baseline表示仅报告内基线）和/model-proposals/{id}详情。实验报告详情及双报告比较提供入口；加载固定报告并核对身份/模式，同样本的不同模型可作参考。填写理由后显式提交，详情显示固定模型/样本/内容身份及SUBMITTED未验收状态，无审批或切换操作。
- 提交前在会话存储保存UUID及完整正文，按账号、模式、候选/参考报告隔离。保存失败不发请求；提交后理由锁定，响应丢失/写入暂停/错误返回保留原身份，刷新不自动重发，手动重试精确复用UUID和正文。成功保存详情ID，返回同一路径提供既有详情；组件离开中止客户端等待，不假定服务端未执行。
- 新增4项浏览器场景：丢失响应后刷新精确重试与详情、暂停/不匹配响应拒绝及恢复、错误所有者不展示、默认关闭不访问实验接口、存储失败零创建请求。首次新增3项后完整35项通过，补存储失败场景后完整36项通过（33.4秒）。桌面/390px手机截图已检查，无页面整体横向溢出，保存.local/model-proposal-ui/。
- 完整TS117项通过（contracts27/BFF90，真实PG启用），pnpm typecheck/build、Alpha/BFF契约漂移、Ruff、文档图检查通过。无Python业务/数据库变更，未重复643项Python或备份恢复。浏览器使用真实登录和受控提案/报告响应；前轮真实BFF/Alpha/PG幂等写入证据独立保留，不称本轮UI为实际容器端到端验证，镜像未重建。
- 观察与源码哈希见infra/verification/model-proposals-ui-observation.json。提案历史发现、正式质量门槛、人工审批与冠军切换/回滚、真实数据和发布Review仍待完成。76项IN_PROGRESS/44项TODO不变，goal继续active。


## 后续增量：提案提交与详情的实际容器链路

- 重建当前Alpha training-runtime、BFF与Web镜像，纳入迁移017后提案模块、私有/公开API和页面。实际运行镜像摘要见infra/verification/model-proposals-container-browser-observation.json，未以此前比较页面镜像替代本轮证据。
- 扩展scripts/check_model_catalog_browser.mjs，在独立Compose项目执行真实Web→BFF→Alpha→yta_test，无响应Mock。随机账号真实登录后填写中文/emoji理由显式提交候选+参考报告，服务端提出者与会话相符；同UUID/正文精确POST重试及GET详情均与首次响应完整相等。数据库核对仅1份提案、1次提出者审计，载荷/哈希与响应一致。
- 返回原表单提供既有提案详情入口，无新提交按钮；状态保持SUBMITTED/UNVERIFIED。桌面表单及详情、390px手机详情截图保存在.local/model-catalog-browser/proposal-*.png，已检查表单与手机截图。无整体横向溢出、pageerror为0，同源请求及Web凭证/DNS隔离、非root/只读根/drop ALL检查保持通过。精确重试不冒称本轮制造了实际网络丢包；丢响应UI恢复证据仍来自前轮受控浏览器测试。
- 演练关闭研究恢复worker且未启动Jobs，注销会话并finally清理容器/独立网络/临时配置。原PostgreSQL与无关服务保持运行；保留合成用户、认证事实及1份提案/审计，不修改模型或预测事实。
- Node语法、Ruff及120任务文档图检查通过。本轮无应用业务/数据库变更，未重复117项TS、36项受控浏览器套件、643项Python或备份恢复。新增证据仅证明本机容器实际提案链路，未证明云端staging或人工审批。提案历史发现、质量门槛、审批/冠军切换/回滚与正式发布仍待完成，76项IN_PROGRESS/44项TODO不变，goal保持active。


## 后续增量：按提出者和模式隔离的提案历史私有接口

- 新增GET /internal/v1/model-proposals，仅服务鉴权且默认关闭实验开关。按可信BFF提出者与服务端模式查询，登记时间/ID降序游标分页，每页1–50条；跨用户、跨模式、未知或格式错误的游标拒绝，客户端查询不能覆盖所有者或模式。
- 查询在只读事务内进行，核对提案载荷与列身份、候选/参考引用组合及内容/请求哈希，保留原始登记时间和SUBMITTED/UNVERIFIED。列表不重复运行原生模型，完整引用报告重放仍由详情承担；不宣称多个分页请求共享同一数据库快照。
- 新增2组真实PG测试，验证连续分页与数据库排序一致、无重复、所有者/模式隔离、游标和页大小拒绝、篡改检测、无审计新增、无原生推理调用，以及服务/工具鉴权和默认关闭。完整Python645项通过（52.33秒，4条既有pandas警告，无跳过）。
- Alpha OpenAPI及BFF依赖组件已导出，27项共享契约测试、双端OpenAPI规范/漂移、Ruff、172文件格式和120任务文档图检查通过。观察见infra/verification/model-proposals-history-observation.json。无数据库模式或写入逻辑变化，未重复备份恢复；无BFF/Web业务变化，未重复其全套、浏览器或容器构建。
- BFF历史入口和页面仍待接入，正式质量门槛、人工审批、冠军切换/回滚和真实数据/发布验收继续待办。76项IN_PROGRESS/44项TODO不变，goal保持active。

## 后续增量：BFF会话绑定的提案历史分页

- 新增GET /api/yta/v1/model-proposals，从认证会话派生所有者，默认关闭实验开关。严格接受1–50页大小及规范化UUID游标，拒绝所有者/模式覆盖、重复参数和额外查询；只读历史不使用创建写入准入。
- 新增共享查询/分页Zod契约与公开OpenAPI。BFF逐条验证载荷、所有者、模式和内容哈希，拒绝超页大小、重复记录、游标不属于末项和游标不前进；异常上游固定502，不泄露载荷。
- 新增共享契约、BFF响应核验和真实PG登录HTTP回归各1项。完整TS120项通过（contracts28/BFF92，真实PG启用），pnpm typecheck/build通过。
- 实际启动Alpha与构建后的BFF连接yta_test，随机账号真实登录，提交两份合成REPLAY提案并按每页1条发现；两页与原登记响应完全一致，首个列表与直接Alpha一致，跨用户游标422、匿名401。已有提案同键重试/读取和模型报告比较探针继续通过。注销200并finally清理进程，保留合成身份与提案/审计事实。
- 观察见infra/verification/model-proposals-history-bff-observation.json。公开OpenAPI、双端契约漂移、Ruff及120任务文档图检查通过。无Python业务/数据库变化，未重复前轮645项Python或备份恢复；无页面变化，未重复浏览器套件或重建镜像。
- 历史页面、正式质量门槛、人工审批、冠军切换/回滚及真实数据/发布验收仍待完成。76项IN_PROGRESS/44项TODO不变，goal继续active。

## 后续增量：提案历史页面与服务器记录发现

- 新增/model-proposals页面，模型目录和提案详情/表单提供入口。按当前会话和模式读取服务器分页，展示原始理由、候选/参考模型、登记时间与SUBMITTED未验收状态，支持前后页及详情跳转。刷新和清空会话缓存后仍可发现已提交记录。
- 页面校验所有者、模式、页大小及游标前进；错误响应不展示旧页，支持重试和空状态，默认关闭不请求历史。账号/模式变化重新挂载，离开页面中止等待，401返回登录。
- 新增3项浏览器场景。首轮38通过/1失败，原因为alert定位器同时匹配Next路由播报器；限定主内容后完整39项通过（34.1秒）。桌面与390px手机截图已检查，无页面整体横向溢出，保存在.local/model-proposal-history-ui/。
- 完整TS120项通过（contracts28/BFF92，真实PG启用），pnpm typecheck/build、双端契约漂移、Ruff及文档图检查通过。观察见infra/verification/model-proposals-history-ui-observation.json。无Python/数据库变化，未重复前轮645项Python与备份恢复。
- 浏览器测试使用真实登录与受控提案响应；前轮实际BFF/Alpha/PG历史分页证据独立保留。当前页面尚未重建并验证容器链路，正式质量门槛、人工审批、冠军切换/回滚及发布验收继续待办。76项IN_PROGRESS/44项TODO不变，goal保持active。

## 后续增量：提案历史的实际容器页面验证

- 重建Alpha training-runtime、BFF和Web，纳入提案历史私有/公开接口与页面。实际镜像摘要及完整观察保存在infra/verification/model-proposals-history-container-browser-observation.json。
- 扩展scripts/check_model_catalog_browser.mjs，以独立Compose项目运行真实浏览器→Web→BFF→Alpha→yta_test，无响应替换。随机用户真实登录后，经认证BFF登记11份合成REPLAY提案，保留原提案同键重试/详情/数据库单事实单审计检查。
- 清空会话缓存，从表单进入历史页面；按10＋1分页的完整响应与11次登记内容逐项一致，第二页下一页禁用，并能进入原提案详情。截图保存在.local/model-catalog-browser/proposal-history-*.png，390px手机截图已检查，无页面整体横向溢出。
- 原有模型目录、评测报告、跨模型比较、提案提交/重试链路保持通过。页面错误为0、浏览器请求同源且不含internal路径，容器非root/只读根/drop ALL及Web凭证/DNS隔离保持通过。
- 注销后清理演练容器、独立网络及临时配置，原PostgreSQL与无关服务保持运行。新增合成账号、登录及11份提案/审计事实保留；未运行研究worker或Jobs，也未修改模型制品与预测事实。
- Node语法、Ruff及120任务文档图检查通过。本轮无应用业务或数据库模式变更，未重复前轮120项TS、39项受控浏览器套件、645项Python及备份恢复。该证据证明本机打包后的实际链路，不代表staging或模型质量验收。质量门槛、人工审批、冠军切换/回滚与正式发布仍待完成，76项IN_PROGRESS/44项TODO不变，goal继续active。

## 后续增量：显式规则的历史候选数值核验

- 新增HistoricalQualityPolicy及ModelEvaluationRegistry.assess_quality。策略显式提供版本、模式、跨度、最低样本数、最高MAE、最低方向命中率、最低MAE改善和是否要求参考报告；无默认生产阈值。入口先按模式完整重放显式登记报告，再执行只读核验。
- 使用Decimal50精度及12位HALF_EVEN改善值，检查零收益、5交易日动量及可选同样本不同模型参考。缺失指标或必需参考不能通过，策略作用域/参考样本不匹配或内容哈希错误拒绝。结果保留策略正文/哈希、报告身份、逐项观测/阈值/结果与整体内容哈希。
- numeric_pass仅表示给定历史规则达标；promotion_allowed固定false，保留UNVERIFIED及历史证据未取得资格/需要人工审批原因。没有把历史数值筛选当成完整晋升门槛，也未选择生产阈值。
- 新增14项单元回归和1项真实PG集成，覆盖精度边界、缺失指标、严格输入、作用域/哈希、登记报告重放、确定性结果、不同模型同样本参考、跨模式拒绝及无审计写入。完整Python660项通过（54.00秒，4条既有pandas警告，无跳过）；Ruff、174文件格式和Alpha契约漂移通过。
- 观察见infra/verification/model-quality-screen-observation.json。无数据库模式/写入逻辑、HTTP或TS变化，未重复备份恢复、TS/浏览器套件或镜像构建。规则/核验不可变登记、API/页面、真实数据与概率质量资格、人工审批和冠军切换/回滚仍待完成。76项IN_PROGRESS/44项TODO不变，goal保持active。

## 后续增量：历史质量规则与核验结果不可变登记

- 新增迁移018和ModelQualityRegistry。规则使用内容寻址ID，固定版本/模式/跨度下的阈值，更改同版本内容409。核验引用固定规则及候选/可选参考登记报告，先完整重放再计算；相同内容并发登记只生成一份事实与一次审计，审计失败回滚事实。
- 读取校验规则身份、引用列及完整核验重算结果。UPDATE/DELETE/TRUNCATE由不可变保护拒绝，运行角色仅SELECT/INSERT。记录仍UNVERIFIED且promotion_allowed=false，内部登记审计actor为空，不冒充会话用户批准或激活生产规则。
- 新增10项真实PG测试，覆盖四路并发、版本冲突、缺失参考的失败记录、模式隔离、两表三种不可变操作、规则/核验审计失败回滚、重算外层哈希后的批准/通过状态篡改拒绝。专项50项通过；全新专用测试库完整Python670项通过（59.28秒，4条既有pandas警告，无跳过）。
- 备份恢复36张表、34个不可变保护、6个序列，46预测、20结算、9证据、15提案及9份规则/9份核验结果通过验证；采集/投影恢复及异常拒绝保持通过。恢复后另以运行角色读取重放全部9份核验。源测试库自动清理，恢复库/私有备份保留，正常应用yta库未迁移。
- migrator镜像重建，实际新建001–018、升级、重复执行、已有证据保留和角色隔离通过，演练数据库已清理。Ruff、175文件格式、Alpha契约漂移与文档图检查通过。证据见infra/verification/model-quality-registry-observation.json。
- 无TS/Web/HTTP变更，未重复120项TS和39项浏览器套件，应用镜像未重建。正式规则选定/激活、质量API/页面、真实数据与概率资格、人工审批和冠军切换/回滚仍待完成。76项IN_PROGRESS/44项TODO不变，goal保持active。

## 后续增量：已登记质量规则与核验的私有API

- 新增私有规则GET、核验POST/GET，仅服务鉴权且默认关闭。POST只接受固定规则和候选/可选参考报告ID，先按服务端模式读取规则，再完整重放并登记核验；相同引用重试返回相同200结果。没有规则注册/激活或批准操作，派生审计不冒充认证用户。
- 新增严格Pydantic响应，分别限定样本/指标/参考/改善检查的数据类型，核对完整检查顺序、整体通过值、策略/内容哈希及固定未批准状态。未知或跨模式404，错误身份格式/正文注入422，工具Token401，原生库不可用503且无替代写入；规则读取不依赖原生推理。
- 新增2组真实PG/HTTP回归，涵盖鉴权、严格正文、模式隔离、默认关闭、登记/读取/重试完整一致、单次审计、响应哈希验证及原生库不可用不产生新核验。完整Python672项通过（61.92秒，4条既有pandas警告，无跳过）。
- Alpha及BFF依赖OpenAPI已导出，28项共享契约测试、双端规范/漂移、Ruff、176文件格式与文档图检查通过。证据见infra/verification/model-quality-api-observation.json。无数据库模式或事务规则变化，未重复备份恢复；无BFF/Web业务变化，未重复其全量或浏览器套件，应用镜像未重建。
- BFF会话入口、质量页面、正式阈值与规则激活、真实数据/概率资格、人工审批和冠军切换/回滚仍待完成。76项IN_PROGRESS/44项TODO不变，goal保持active。

## 后续增量：BFF会话绑定的历史质量核验

- 新增已登录规则GET、核验POST/GET公共接口，沿用默认关闭实验开关。创建要求同源/CSRF和写入准入；拒绝阈值/模式/审批字段注入及额外查询，向Alpha传递会话身份但不将派生核验审计冒称人工批准。
- 新增共享严格规则/核验Zod契约及请求/OpenAPI导出。BFF核对数据模式、规则/内容哈希、固定候选/参考引用、检查顺序及整体通过值，保留十进制字符串，不重算金融指标。错误上游固定502且不泄露多余载荷。
- 由真实登记的合成REPLAY结果导出fixture；新增共享契约1项、BFF核验2项、真实PG登录HTTP1项，扩展默认关闭和写入暂停回归。完整TS124项通过（contracts29/BFF95，真实PG启用），pnpm typecheck/build通过。
- 实际启动Alpha及构建后的BFF连接yta_test，真实登录读取规则、POST核验/重复提交及GET结果均与登记载荷和直接Alpha完全一致，数据库审计仍1条；原目录/报告/比较/提案探针保持通过。匿名401、注销200并finally清理进程。仅追加合成登录/提案事实，引用的核验复用既有内容身份。
- 公开OpenAPI、双端契约漂移、Ruff和文档图检查通过。观察见infra/verification/model-quality-bff-observation.json。无Python业务/数据库变化，未重复前轮672项Python及备份恢复；无UI变化，未重复39项浏览器套件或重建镜像。
- 规则发现/选择和质量页面、正式阈值/激活、真实数据与概率资格、人工审批和冠军切换/回滚仍待完成。76项IN_PROGRESS/44项TODO不变，goal保持active。

## 后续增量：质量规则发现与跨度分页

- 新增私有和BFF GET historical-quality-policies。按服务端模式、可选1/3/5/10/20跨度筛选，登记时间/ID倒序游标分页，1–50条默认20；游标须属于相同模式及筛选范围。只读事务校验完整规则与列/内容身份，不执行原生推理或激活规则。
- BFF要求登录和实验开关，严格查询参数，校验页大小、重复项、末项游标和游标前进，并逐项核对模式、跨度及规则哈希。共享请求/分页契约与双端OpenAPI同步。
- 新增2组真实PG分页/HTTP测试及3项TS契约/响应/HTTP测试，覆盖数据库顺序、连续分页无重复、模式/跨度游标隔离、非法参数、无原生推理/审计写入、鉴权、默认关闭及上游异常。完整Python674项通过（63.01秒，4条既有pandas警告，无跳过）；完整TS127项通过（contracts30/BFF97，真实PG启用），pnpm typecheck/build通过。
- 实际Alpha/BFF/PG探针以真实登录读取两页规则，完整载荷与数据库顺序及直接Alpha一致；质量创建/重试/读取和前述目录/报告/比较/提案链路保持通过。注销并finally清理进程，固定规则未改变。
- 观察见infra/verification/model-quality-catalog-observation.json。双端OpenAPI规范/漂移、Ruff、177文件格式及文档图检查通过。无数据库模式/写入变化，未重复备份恢复；无页面变化，未重复浏览器套件或重建镜像。规则选择与质量页面、正式阈值/激活、真实数据资格、审批与冠军切换/回滚继续待办，76项IN_PROGRESS/44项TODO不变，goal保持active。

## 后续增量：规则选择与历史质量核验页面

- 新增/models/quality/{candidate}/{reference}和/model-quality/{id}，从报告详情和已提交提案进入。先核对固定候选/参考报告，只列当前模式及跨度的已登记规则，展示完整阈值/引用并分页；用户明确选择后运行，无默认规则或自动提交。
- 响应丢失/写入暂停保留规则选择，手动重试精确复用规则/报告引用；核验中禁用选择/分页，切换规则或分页清除旧结果。结果核对模式、跨度、规则、报告及样本身份，展示原始数值/阈值/通过与缺失状态，固定详情可刷新读取。数值达标仍未批准，无激活规则或冠军切换操作。
- 新增3项浏览器场景，覆盖规则分页、显式选择、丢响应精确重试、固定详情刷新/手机、跨模式规则和错误候选拒绝、写入暂停恢复、默认关闭不请求证据。首轮42项通过后补充另一规则选择立即清除旧结果断言，最终完整42项通过（35.3秒）。桌面和390px手机截图已检查并保存.local/quality-ui/，无页面整体横向溢出，手机明细表可横向滚动。
- 完整TS127项通过（contracts30/BFF97，真实PG启用），pnpm typecheck/build、双端契约漂移、Ruff及文档图检查通过。观察见infra/verification/model-quality-ui-observation.json。无Python或数据库变化，未重复674项Python或备份恢复。
- 浏览器采用真实登录及受控的实际登记合成REPLAY报告/规则响应；前轮实际BFF/Alpha/PG证据独立保留。本轮尚未重建并验证质量页面容器链路。正式阈值/激活、真实数据/概率资格、人工审批和冠军切换/回滚继续待办，76项IN_PROGRESS/44项TODO不变，goal保持active。

## 后续增量：历史质量核验的实际容器页面链路

- 重建Alpha training-runtime、BFF与Web镜像，包含迁移018后的质量规则/核验、发现接口及选择/结果页面。实际运行镜像摘要见infra/verification/model-quality-container-browser-observation.json。
- 扩展scripts/check_model_catalog_browser.mjs，在独立Compose项目完成真实浏览器→Web→BFF→Alpha→yta_test，无响应替换。真实登录后从提案详情进入核验，读取REPLAY五交易日规则，确认未选择时提交禁用，显式选择并提交固定规则和候选/参考报告。
- 提交返回的规则/结果与数据库JSON完整相等，精确重复POST与首次相等，数据库只有1份对应核验和1次登记审计。打开固定详情并刷新保持可读，qualification仍UNVERIFIED且promotion_allowed=false。桌面和390px手机截图保存在.local/model-catalog-browser/quality-*.png，手机截图已检查，无页面整体横向溢出。
- 原模型目录/报告/比较、提案创建/重试/详情及11条历史10＋1分页继续通过。页面错误为0、浏览器仅同源请求，容器非root/只读根/drop ALL及Web凭证/DNS隔离检查通过。研究worker关闭且未启动Jobs。
- 注销后清理演练容器、独立网络和临时配置，原PostgreSQL及无关服务保持运行；保留合成用户、提案、质量核验与相应审计事实。未修改模型制品或预测事实，也未请求真实行情/LLM。
- Node语法、Ruff与120任务文档图检查通过。本轮无应用业务/数据库变化，未重复127项TS、42项受控浏览器套件、674项Python及备份恢复。新增证据证明本机打包链路，不证明生产规则、staging或审批资格。正式阈值/激活、真实数据/概率资格、人工审批和冠军切换/回滚仍待完成，76项IN_PROGRESS/44项TODO不变，goal保持active。

## 后续增量：模型审批的服务端权限前置检查

- BFF新增YTA_MODEL_REVIEWER_IDS配置，默认空，严格接受最多100个不同UUID并规范大小写。身份必须来自已认证会话；邮箱、任意角色/所有者请求头和模型输出不能授予权限，实验开关关闭时也拒绝。
- Auth.modelReviewMutation复用当前登录、同源和CSRF校验后检查审批人列表；/me新增can_review_models，仅披露当前用户布尔能力，不返回名单。公开OpenAPI、Web会话类型和示例配置同步。未配置实际审批人，未开放批准或冠军切换接口。
- 新增配置1项及真实PG会话集成1项，验证默认拒绝、UUID格式/重复/空白、有效身份、伪造其他用户头、CSRF/来源拒绝、名单移除、功能禁用和持久化会话撤销后拒绝；另启动BFF验证不同会话/me能力隔离。完整TS129项通过（contracts30/BFF99，真实PG启用），pnpm typecheck/build通过；Web类型字段补齐后另行typecheck通过。
- OpenAPI规范、双端契约漂移、Ruff及文档图检查通过。观察见infra/verification/model-review-permission-observation.json。无Python或数据库模式变化，未重复674项Python/备份恢复；无交互变化，未重复42项浏览器套件或重建镜像。
- 该检查是后续审批写入的权限前提，不是模型质量资格或人工批准事实。正式规则选定/激活、审批持久化、真实数据/概率资格、冠军切换/回滚继续待办，76项IN_PROGRESS/44项TODO不变，goal保持active。

## 后续增量：绑定固定提案证据的审核预检

- 新增ModelReviewRequest和内部ModelReviewService.evaluate。正文仅提案/核验ID、APPROVE或REJECT及非空理由；审核人和请求UUID单独由可信调用者传入，未来HTTP入口须先经过BFF审批权限检查。本轮未开放绕过会话的接口。
- 从数据库读取提案所有者，按服务端模式读取并完整重放提案与质量核验。校验请求身份、提案内容哈希、候选/参考报告、样本集合及核验内容身份一致；另一组证据的通过结果不能替代当前提案。
- 当前历史UNVERIFIED证据的APPROVE请求得到BLOCKED，保留历史资格未通过及适用的数值失败原因；REJECT形成REJECTED预检结果。固定审核人/请求/理由/证据引用、失败检查及内容哈希，promotion_allowed=false。只读预检不保存审核事实、不改提案状态，也不授予冠军资格。
- 新增5项单元及1项真实PG回归，覆盖确定性、内容哈希、请求身份/模式、重算外层哈希后的候选/参考/样本替换、正文权限字段拒绝、真实报告重放、数值失败与只读审计计数。完整Python680项通过（67.65秒，4条既有pandas警告，无跳过）。
- Ruff、179文件格式、Alpha契约漂移及文档图检查通过。观察见infra/verification/model-review-preflight-observation.json。无数据库模式、HTTP或TS变化，未重复备份恢复、129项TS、42项浏览器套件或镜像构建。审核事实持久化、并发状态约束、正式规则/资格与冠军切换回滚仍待完成；76项IN_PROGRESS/44项TODO不变，goal保持active。

## 后续增量：不可变审核事实、并发前驱与恢复

- 新增迁移019及ModelReviewRegistry。提交固定审核人/请求UUID、提案/核验及预期上一条审核；按提案事务锁串行检查前驱并追加revision，不修改提案事实。旧状态409，同审核人/模式/请求同正文精确重试返回原记录，即使随后已有审核；同键异参409。
- BLOCKED后可基于当前审核再次提交，REJECTED关闭原提案，后续需另建提案。当前没有APPROVED或冠军状态。审核事实与审核人审计同事务，失败回滚；读取重放完整前驱链、提案及核验，验证请求/内容身份，拒绝链环、缺失前驱、修订号及重算外层哈希后的批准状态篡改。
- 新增7项真实PG回归，覆盖四路幂等、不同审核人竞争只有一个成功、旧状态/终止状态、审核人审计、同键冲突、模式隔离、三种不可变操作、审计回滚及篡改。首轮专项60通过/1失败，因新外键使旧提案TRUNCATE测试先触发FK拒绝；改用CASCADE继续验证不可变触发器，补充测试后全新专用库完整Python687项通过（79.48秒，4条既有pandas警告，无跳过）。
- 实际备份恢复37张表、35个不可变保护、6个序列，46预测/20结算/9证据、23提案、26规则/21核验及7条审核事实验证通过。采集/投影重建与异常拒绝通过；恢复后另以运行角色逐条重放全部7条审核链。源测试库已自动清理，恢复库/私有备份保留，正常应用yta库未迁移。
- migrator镜像重建，实际新建001–019、升级、重复执行、已有证据保留及角色隔离通过并清理演练库。Ruff、180文件格式、Alpha契约漂移及文档图检查通过。观察见infra/verification/model-review-registry-observation.json。
- 无HTTP/TS/Web变化，未重复129项TS、42项浏览器套件或重建应用镜像。审核私有/公开入口与界面、正式规则与证据资格、实际批准和冠军切换/回滚仍待完成；76项IN_PROGRESS/44项TODO不变，goal保持active。

## 后续增量：审核私有API与严格记录契约

- 新增POST /internal/v1/model-reviews和GET /internal/v1/model-reviews/{uuid}，仅服务鉴权、工具Token拒绝、实验开关默认关闭。POST要求UUID幂等键与可信BFF审核人头，正文只接受提案/核验、动作、理由及预期前驱；不能注入审核人、模式、批准状态或权限。
- 新增ModelReviewRecord/Response严格契约，验证固定BLOCKED/REJECTED状态与动作、阻止原因、失败检查去重、前驱/修订关系、核验身份及整体内容哈希。创建/读取完整重放，原请求精确重试在后续审核关闭提案后仍返回原记录。
- 新增2组真实PG/HTTP回归：匿名/工具Token、缺失或非法审核人/幂等键、正文注入、状态冲突409、拒绝关闭、原请求重试、缺失/跨模式404、默认关闭503、原生重放不可用503且无新增审核。完整Python689项通过（89.35秒，4条既有pandas警告，无跳过）。
- Alpha OpenAPI及BFF依赖组件更新，30项共享契约测试、双端OpenAPI规范/漂移、Ruff、181文件格式和文档图检查通过。观察见infra/verification/model-review-api-observation.json。无数据库模式/事务变化，未重复备份恢复；无BFF/Web业务变化，未重复其全套、浏览器或容器验证。
- 私有服务头不是用户授权；BFF审批权限检查尚未接到公开审核路由。公开入口、审核发现/界面、正式规则与证据资格、实际批准及冠军切换/回滚仍待完成，76项IN_PROGRESS/44项TODO不变，goal保持active。


## 后续增量：审核人限定的BFF公开审核入口

- 新增公开model-reviews POST和固定ID GET，均要求已认证会话属于服务端审核名单且实验功能启用。POST额外要求同源、CSRF、写入准入及UUID幂等键；拒绝正文审核人注入和额外查询。GET允许配置中的审核人读取当前模式的其他审核人记录，普通用户拒绝，写入暂停不阻断读取。
- 新增共享严格审核记录契约及BFF响应校验，核对模式、完整内容哈希、固定请求引用、审核人/请求身份、动作/状态/阻止原因与前驱修订关系。上游非法载荷固定502，不透出额外字段。公开OpenAPI与请求导出同步。
- 新增4项TS契约/响应/真实PG登录HTTP回归，覆盖匿名、伪造身份、普通用户读取、CSRF/来源、输入注入、幂等键、错误上游及写入暂停。完整TS133项通过（contracts31/BFF102，真实PG启用）；pnpm typecheck/build通过。
- 实际启动构建后的BFF及Alpha连接yta_test，只给随机合成用户配置审核权限。真实登录创建绑定同一提案报告的质量核验，提交APPROVE得到BLOCKED且promotion_allowed=false；同键重试、GET、直接Alpha及数据库完整载荷一致，只有1条审核事实和1条对应审核人审计。原目录/报告/比较/提案/规则分页链路保持通过，注销并finally清理进程。
- 双端契约漂移、Ruff及120任务文档图检查通过。证据见infra/verification/model-review-bff-observation.json。无Python、数据库模式或UI行为变更，未重复前轮689项Python、42项浏览器及备份恢复，应用镜像未重建；未配置真实用户审批权限。
- 审核发现/界面、正式规则与证据资格、实际批准和冠军切换/回滚仍待完成。76项IN_PROGRESS/44项TODO不变，goal保持active。


## 后续增量：提案审核历史私有查询

- 新增GET /internal/v1/model-proposals/{uuid}/reviews，服务鉴权、实验开关及服务端模式隔离，默认20条/最多50条，按修订号倒序。游标绑定同一提案/模式，新审核追加不改变旧游标后续分页；空提案返回空页，缺失/跨模式404，无效游标422。
- 复用完整审核链重放，先校验游标记录再读取前驱，逐条验证提案、核验与链身份。没有只读取未经核验的状态字段，也不改变审核写入的前驱冲突检查。分页仅限制返回条数，完整证据重放成本仍随链长增长。
- 新增2组真实PG/HTTP回归，覆盖空历史、不同审核人多修订、分页间追加、终止拒绝、末页、无效游标、模式隔离、页大小、鉴权和只读审计计数。首轮专项11通过/1失败为无效游标测试预期错误（400而项目统一422），修正后完整Python691项通过（97.40秒，4条既有pandas警告，无跳过）。
- 实际Alpha/BFF/PG探针真实登录并登记合成REPLAY提案/审核，私有历史响应与创建/重试/读取及数据库载荷一致；只增加一次审核事实和对应审核人审计，注销并清理进程。既有目录、报告、比较、提案、质量链路保持通过。
- 31项共享契约、双端OpenAPI规范/漂移、Ruff、181文件格式及120任务文档图检查通过。证据见infra/verification/model-review-history-api-observation.json。无数据库模式/写入变化，未重复备份恢复；无BFF/Web业务变化，未重复其全量测试、浏览器或镜像构建。
- 历史BFF入口、审核页面、正式证据资格、批准及冠军切换/回滚仍待完成。76项IN_PROGRESS/44项TODO不变，goal保持active。


## 后续增量：审核历史BFF权限与分页校验

- 新增GET /api/yta/v1/model-proposals/{uuid}/reviews，要求配置中的已登录审核人及实验开关，只接受严格limit/cursor，不接受客户端模式/所有者。沿用私有历史的模式作用域和完整证据重放。
- 新增共享严格分页契约，BFF逐条核对哈希、模式和提案身份，并检查页大小、唯一记录、连续递减修订号、前驱ID、不能接续终止拒绝以及末项游标。根修订结束分页，非根末项必须提供下一页，错误上游固定502。公开OpenAPI同步。
- 新增1组页完整性单测并扩展真实PG登录HTTP测试，覆盖有效多修订、重复/倒序/缺失前驱、错误提案/模式/游标、匿名、普通用户伪造身份、非法查询和上游载荷。完整TS134项通过（contracts31/BFF103，真实PG启用），pnpm typecheck/build通过。
- 实际构建BFF→Alpha→yta_test，随机合成审核人登录后创建BLOCKED审核并追加REJECTED，首页/两页游标历史与两次创建的完整记录一致；原精确重试仍单事实单审计。匿名401，注销并finally清理进程。未配置真实用户权限，未请求真实LLM或行情。
- 双端OpenAPI规范/漂移、Ruff及文档图检查通过。证据见infra/verification/model-review-history-bff-observation.json。无Python/数据库/UI行为变化，未重复691项Python、42项浏览器或备份恢复，未重建应用镜像。
- 审核页面与提案发现、正式资格、批准及冠军切换/回滚仍待完成。76项IN_PROGRESS/44项TODO不变，goal保持active。


## 后续增量：审核人独立提案发现与跨所有者详情

- 新增私有/公开model-review-proposals列表与固定ID详情。Alpha仅服务鉴权、实验开关及模式隔离；BFF要求配置中的已登录审核人，拒绝普通用户和伪造所有者头。普通model-proposals列表/详情继续限定会话所有者。
- 列表覆盖当前模式所有已提交提案，按登记时间/ID倒序分页，默认20条/最多50条，游标不能跨模式；它不是仅含待处理项的队列，状态仍由审核历史读取。列表校验不可变元数据/哈希，详情完整重放。BFF独立响应读取器允许不同提案所有者，仍核对身份、模式、哈希和分页，不放宽普通读取器。
- 新增2组真实PG/HTTP测试、1组BFF响应单测并扩展实际PG登录HTTP回归，覆盖跨所有者读取、普通路径拒绝、模式/游标隔离、默认关闭、匿名/工具Token、页大小/查询注入和错误上游。Python专项4项通过，完整693项通过（100.29秒，4条既有pandas警告，无跳过）；完整TS135项通过（contracts31/BFF104，真实PG启用）。
- pnpm typecheck/build通过。实际BFF→Alpha→yta_test探针验证审核列表顺序与数据库一致，另一所有者详情与直接Alpha一致，同一登录用户使用普通详情仍404；原两页审核历史、提交/重试和单事实单审计验证保持通过。注销并清理进程，仅保留合成REPLAY事实，未配置真实审核人或请求真实LLM/行情。
- 双端OpenAPI规范/漂移、Ruff、181文件格式和120任务文档图检查通过。证据见infra/verification/model-review-proposals-observation.json。无数据库模式/写入或UI变化，未重复备份恢复/浏览器测试，未重建应用镜像。
- 审核页面、正式证据资格、实际批准和冠军切换/回滚仍待完成；76项IN_PROGRESS/44项TODO不变，goal保持active。


## 后续增量：人工审核页面与精确重试

- 新增审核人导航及/model-review-proposals列表、提案详情和/model-reviews固定记录页面。列表允许当前模式不同所有者，普通账户不显示导航且直接访问不请求审核证据。详情展示固定提案、报告链接和分页审核历史，质量组件复用于明确选择规则并核验当前提案报告。
- 完成质量核验、选择动作及填写理由后显式提交。按账户/模式/提案在sessionStorage保存UUID键、正文和用户看到的前驱后才发送；存储失败不发送，响应不确定锁定正文，刷新后可精确手动重试，不自动换键。提交和恢复结果核对请求/审核人/模式/证据身份。
- 服务端明确前驱冲突或关闭后提示重新加载并重新核对；已拒绝提案禁用继续审核。当前请求批准仅记录BLOCKED及原因，REJECT关闭提案，两者均无冠军切换。成功记录支持直接刷新读取，历史中相同记录不重复展示。
- 新增3项浏览器场景。首轮43通过/2失败为提示定位同时匹配Next路由播报器，限定main后第二轮44通过/1失败为导航完成前刷新旧页面；增加URL确认后最终完整45项通过（36.6秒）。覆盖失响应/刷新/原键正文不变、固定记录刷新、并发冲突/关闭、存储失败不发送、普通账户无证据请求及390px无页面横向溢出。
- 桌面/手机表单和审核记录截图保存.local/review-ui/，已检查。沿用现有海军蓝/青绿/琥珀色和证据卡片样式，移动明细表局部横向滚动。完整TS135项（contracts31/BFF104，真实PG启用）、pnpm typecheck/build、双端契约漂移、Ruff及文档图检查通过。
- 证据见infra/verification/model-review-ui-observation.json。浏览器使用真实登录及受控合成证据响应，不替代实际容器审核页面验证；此前真实BFF/Alpha/PG审核接口证据独立保留。无Python/数据库行为变化，未重复693项Python和备份恢复，应用镜像未重建。
- 审核页面实际容器链路、正式资格与规则激活、实际批准及冠军切换/回滚仍待完成。76项IN_PROGRESS/44项TODO不变，goal保持active。


## 后续增量：审核页面实际容器链路验证

- 重建Alpha training-runtime、BFF和Web，包含审核记录/历史/提案发现及页面。实际镜像摘要见infra/verification/model-review-container-browser-observation.json。
- 扩展scripts/check_model_catalog_browser.mjs，在独立Compose项目为随机新建合成用户配置审核权限，不修改正式环境或真实用户。真实浏览器→Web→BFF→Alpha→yta_test，无响应替换；研究runner关闭。
- 从审核列表进入固定提案，明确选择质量规则、核验并填写理由，请求批准返回BLOCKED且promotion_allowed=false；精确同键重试及固定审核详情刷新一致。重新加载前驱后提交REJECT得到revision2/REJECTED，页面关闭继续审核，历史完整载荷与两次创建一致，关闭后重试原批准请求仍返回原记录。
- 数据库恰有2条该提案审核事实及对应2条审核人审计，完整payload与浏览器响应一致。初次演练在末尾数据库核对失败，因脚本误用审计时间列created_at；更正为实际occurred_at后完整重跑通过，初次合成事实保留，未修改产品业务逻辑。
- 桌面表单/历史及390px手机表单/记录截图保存.local/model-catalog-browser/review-*.png并检查，无页面整体横向溢出。原模型目录、报告/比较、提案创建/精确重试/11条历史分页和质量链路保持通过。浏览器错误0、仅同源请求；容器非root/只读根/drop ALL和Web凭证/DNS隔离保持通过。
- 注销后清理演练容器、网络及临时配置；确认只剩原PostgreSQL与无关服务运行。合成用户/提案/审核及审计事实保留，未运行Jobs或真实行情/LLM请求。
- Node语法、Ruff和文档图检查通过。本轮无应用业务或数据库模式变化，未重复前轮135项TS、45项浏览器套件、693项Python及备份恢复。此证据证明本机打包后的审核链路，不代表正式模型资格、staging或发布验收。
- 正式规则与数据/概率资格、实际批准及冠军切换/回滚、真实供应商/Hermes完整门槛和部署验收仍待完成。76项IN_PROGRESS/44项TODO不变，goal继续active。


## 后续增量：概率校准报告私有读取

- 审核资格缺口核对发现：已有不可变校准登记、Brier/ECE及完整重放，但缺少可供页面读取的报告接口。新增GET /internal/v1/experimental-calibrations/{id}，服务鉴权、默认关闭实验开关及模式隔离，先拒绝跨模式身份，再完整重放登记输入、制品和独立窗口证据。
- 新增严格报告响应：登记身份/时间、模型ID、原登记哈希和独立report_hash；报告保留原始十进制和时间拼写、独立校准/评测样本、拟合分数/标签、单调结点、评测概率/标签、10个可靠性分箱和Brier/ECE。校验范围、样本对齐/窗口ID不重叠、结点单调、分箱边界/计数及哈希；空分箱指标保持null。底层原始行情输入不返回，无写入或晋升操作，EXPERIMENTAL/UNVERIFIED固定。
- 新增2组真实PG/HTTP及严格响应回归，覆盖完整登记载荷一致、重新计算外层哈希后的资格/范围/计数/窗口篡改、匿名/工具Token拒绝、默认关闭、模式隔离、非法身份、原生重放不可用503。专项3项通过，完整Python695项通过（103.42秒，4条既有pandas警告，无跳过）。
- 从真实yta_test登记以运行角色完整重放导出calibration-report.json，与现有价格报告引用的校准身份一致。31项共享契约、双端OpenAPI规范/漂移、Ruff、183文件格式及120任务文档图检查通过。证据见infra/verification/calibration-report-api-observation.json。
- 无数据库模式或事务变化，未重复备份恢复；无BFF/Web业务变化，未重复完整TS/浏览器或重建镜像。概率报告BFF/页面、正式规则/数据与概率资格、实际批准和冠军切换/回滚仍待完成；76项IN_PROGRESS/44项TODO不变，goal继续active。


## 后续增量：概率报告BFF读取与精确Decimal文本

- 新增公开GET /api/yta/v1/experimental-calibrations/{id}，要求登录及实验开关，拒绝额外查询，按服务端模式读取。共享严格报告契约和BFF校验固定登记ID、报告哈希、模式、窗口样本对齐/不重叠、分箱计数及空值；异常上游固定502，不透出额外字段。概率计算和完整语义重放仍由Alpha执行。
- 接入发现历史Decimal的str拼写可为0E-12，新概率报告契约此前只接受普通小数。Alpha/BFF现在保留有限科学记数文本，概率范围按精确Decimal或文本/指数验证，包含带符号零，拒绝略大于1、负非零、非数值及数字类型；不使用JS浮点舍入决定范围，不改写已登记报告或其哈希。
- 新增1项Python格式边界、1项共享契约、2项BFF响应及1项真实PG登录HTTP回归，扩展默认关闭检查。完整Python696项通过（103.05秒，4条既有pandas警告，无跳过）；完整TS139项通过（contracts32/BFF107，真实PG启用），pnpm typecheck/build通过。
- 实际启动构建后的BFF与Alpha连接yta_test，真实登录读取报告，与真实登记fixture、直接Alpha和数据库report完整一致；匿名401、注销200并finally清理进程。只追加合成登录事实，未登记新的模型/校准或请求真实LLM/行情。
- 双端OpenAPI规范/漂移、Ruff、184文件格式和120任务文档图检查通过。证据见infra/verification/calibration-report-bff-observation.json。无数据库模式/写入变化，未重复备份恢复；无UI变化，未重复45项浏览器套件或重建应用镜像。
- 概率报告页面、正式数据与概率资格、实际批准和冠军切换/回滚仍待完成，76项IN_PROGRESS/44项TODO不变，goal继续active。


## 后续增量：概率报告页面及实际容器链路

- 新增/model-calibrations/{id}，从价格评测详情的固定calibration_id进入。展示原始Brier/ECE、评测/上涨样本数、10个可靠性分箱、独立拟合/后续评测边界、逐样本概率/类别和版本身份；拟合样本/分数/标签/结点及源码身份可展开核对，逐样本本地每页20条。空箱显示无样本，不补零；未提供通过门槛或生产资格声明。
- 页面核对模式/固定身份，重新读取先清除旧数据，失败可显式重试，功能关闭不请求报告；长表在手机局部横向滚动。新增3项浏览器场景，覆盖价格报告链接、刷新同ID、空分箱与原始值/身份、错误身份/模式、不可用时无旧指标及默认关闭。完整48项通过（39.6秒），桌面/390px截图已检查并保存.local/calibration-ui/。
- 完整TS139项通过（contracts32/BFF107，真实PG启用），pnpm typecheck/build、契约漂移、Ruff和文档图检查通过。UI证据见infra/verification/calibration-report-ui-observation.json。无Python/数据库行为变化，未重复前轮696项Python和备份恢复。
- 同轮重建Alpha training-runtime/BFF/Web，扩展实际容器浏览器脚本。从价格报告打开固定校准版本，完整响应与登记fixture、PostgreSQL report相等，10个分箱、原始Brier/ECE、拟合身份和刷新/手机布局验证通过。无响应替换、页面错误0、浏览器仅同源；原目录/比较/提案/质量/两步审核与关闭后精确重试链路继续通过。
- 镜像摘要及完整观察见infra/verification/calibration-report-container-browser-observation.json。实际手机截图已检查，容器非root/只读根/drop ALL与Web凭证/DNS隔离保持通过；注销后清理演练容器/网络/临时配置，确认只剩原PostgreSQL和无关服务。保留合成事实，不配置真实审核人或运行真实行情/LLM。
- 该证据完成本机报告读取/展示链路，不代表正式概率质量、真实数据资格或staging。正式规则/资格、实际批准、冠军切换/回滚及发布验收仍待完成；76项IN_PROGRESS/44项TODO不变，goal继续active。


## 后续增量：显式概率数值门槛

- 新增ProbabilityQualityPolicy和CalibrationRegistry.assess_quality。规则必须显式提供版本、模式/跨度、最低拟合/评测样本数、最低上涨/非上涨评测样本数、最高Brier/ECE；没有默认生产阈值。入口先按模式完整重放固定校准登记，再执行只读数值检查。
- Alpha用逐样本概率和实际标签重新计算可靠性摘要，拒绝修改Brier/ECE后重新生成报告哈希的汇总篡改；使用精确Decimal阈值比较。结果保留固定登记/报告/模型及两个窗口集合哈希、规则正文/哈希、逐项观测/要求/结果和整体哈希。numeric_pass仍不授予历史证据资格或人工批准，qualification固定UNVERIFIED、promotion_allowed=false。
- 新增14项单元及1项真实PG回归，覆盖恰好达标与小数点后12位差异、低全局Decimal精度、四类样本不足、单类别评测、严格显式输入、模式/跨度、重算外层哈希后的指标篡改，以及实际登记重放和无审计写入。专项15项通过；完整Python711项通过（105.00秒，4条既有pandas警告，无跳过）。
- Ruff、186文件格式、Alpha契约漂移和120任务文档图检查通过。证据见infra/verification/probability-quality-screen-observation.json。无数据库模式/写入、HTTP或TS变化，未重复备份恢复、139项TS或48项浏览器套件，应用镜像未重建。
- 概率规则/结果不可变登记、组合质量门槛及接口、正式真实数据/概率资格、实际批准和冠军切换/回滚仍待完成。76项IN_PROGRESS/44项TODO不变，goal继续active。


## 后续增量：概率规则与核验结果不可变登记

- 新增迁移020和ProbabilityQualityRegistry。规则版本、模式及跨度绑定完整显式阈值，同版本异参409；结果引用固定规则与校准登记，完整重放后以内容身份登记。并发精确重试只产生一份事实及一次登记审计，审计失败同事务回滚。审计actor为空，不构成人工批准。
- 两张新事实表拒绝UPDATE、DELETE及TRUNCATE；运行角色只有SELECT/INSERT。读取重新验证规则、校准证据及全部数值检查，重算外层哈希的资格/晋升/数值结果篡改仍被拒绝。UNVERIFIED和promotion_allowed=false不变。
- 新增10项真实PG专项并全部通过，覆盖四路并发、版本冲突、模式隔离、严格规则数值失败、所有者/运行角色不可变保护、审计回滚及篡改。全新专用测试库完整Python721项通过（100.73秒，4条既有pandas警告，无跳过）。
- 备份恢复验证39张表、37个不可变保护、6个序列，含46预测、20结算、9证据、30提案、32历史规则、27历史核验、14审核、10概率规则和9概率核验。采集与投影重建及异常拒绝通过；恢复后以运行角色逐条重放全部10份概率规则与9份核验。源演练库已清理，恢复库和私有备份保留，正常应用yta库未迁移。
- 重建migrator镜像，实际001–020新建、升级、重复执行、已有证据保留和角色隔离通过并清理演练库。Ruff、187文件格式、Alpha契约漂移及文档图检查通过。证据见infra/verification/probability-quality-registry-observation.json。
- 无HTTP、TS或Web行为变化，未重复此前139项TS与48项浏览器套件，也未重建应用镜像。概率核验接口及组合门槛、正式真实数据/概率资格、实际批准和冠军切换/回滚、Hermes完整验证及部署验收仍待完成；76项IN_PROGRESS/44项TODO不变，goal保持active。


## 后续增量：概率核验私有接口与严格响应

- 新增GET /internal/v1/probability-quality-policies/{id}、POST /internal/v1/probability-quality-assessments及固定ID GET。仅服务鉴权，工具Token拒绝；实验开关默认关闭、模式来自服务端。POST仅接受已登记规则和校准引用，完整重放后登记，精确重复请求返回同一结果及单次登记审计。
- 新增严格规则/核验响应，保留模型、校准、报告、两个样本窗口和规则身份。除哈希外核对六项检查顺序、规则阈值、观测/通过值、整体数值结果和上涨/非上涨总数；重算外层哈希不能掩盖内部矛盾或批准状态篡改。底层证据仍由登记器完整语义重放，响应校验不替代来源资格。
- 新增两组真实PG HTTP测试及一组契约篡改回归，覆盖服务/工具权限、正文注入、精确重试、缺失身份、默认关闭、跨模式、原生重放不可用503，以及重算哈希后的阈值/结果/计数/顺序/批准/资格篡改。专项HTTP两项通过；完整Python724项通过（108.99秒，4条既有pandas警告，无跳过、真实PG必需）。证据见infra/verification/probability-quality-api-observation.json。
- Alpha OpenAPI和BFF依赖契约同步；32项共享契约测试、双端规范和漂移检查、Ruff、188文件格式及文档图检查通过。无数据库模式/事务或Web变化，未重复备份恢复、完整BFF/浏览器套件或重建应用镜像。
- BFF/界面与规则发现、组合门槛、正式真实数据/概率资格、实际批准和冠军切换/回滚及发布验收仍待完成。76项IN_PROGRESS/44项TODO不变，goal保持active。


## 后续增量：概率规则发现与跨度分页

- 新增私有GET /internal/v1/probability-quality-policies。按服务端模式和可选1/3/5/10/20交易日跨度筛选，登记时间/ID倒序游标分页，默认20条、范围1–50；游标须属于当前模式和跨度。仅服务Token可访问，工具Token拒绝，实验开关默认关闭。
- 列表使用只读事务，逐项验证完整规则、内容哈希及列身份，不执行原生推理、不写审计或激活规则。严格分页响应与Alpha OpenAPI同步。
- 新增两组真实PG/HTTP测试，专项通过，覆盖完整数据库顺序、连续分页无重复、模式/跨度/未知游标拒绝、非法limit/horizon、鉴权、默认关闭及无原生计算/审计写入。完整Python726项通过（108.86秒，4条既有pandas警告，无跳过、真实PG必需）。证据见infra/verification/probability-quality-catalog-observation.json。
- 32项共享契约测试、双端OpenAPI规范/漂移、Ruff和189文件格式检查通过。无数据库模式/写入及BFF/Web行为变化，未重复备份恢复、完整BFF/浏览器套件或重建应用镜像。
- 概率BFF和页面、组合门槛、正式真实数据/概率资格、实际批准与冠军切换/回滚及部署验收继续待办；76项IN_PROGRESS/44项TODO不变，goal保持active。


## 后续增量：概率规则与核验的BFF入口

- 新增公开probability-quality-policies列表/详情及probability-quality-assessments创建/固定读取。要求已认证会话和实验开关；POST额外要求同源、CSRF和写入准入。模式由服务端决定，严格拒绝额外查询/正文；错误上游固定502，不泄露其额外字段。
- 新增共享概率规则、六项检查、核验及分页契约。保留Decimal文本，复用概率报告的精确范围检查；核对阈值、检查顺序、样本计数和整体数值标志，BFF进一步核对内容/规则哈希、请求校准引用、模式、固定ID及分页范围。金融指标计算与证据重放仍由Alpha完成。
- 从真实登记以运行角色完整重放导出概率核验fixture。新增2项共享契约、3项响应校验及2组真实PG登录HTTP测试，扩展默认关闭/写入暂停覆盖。完整TS146项通过（contracts34/BFF112，真实PG启用），pnpm typecheck/build通过；共享生成声明刷新后类型检查通过。
- 实际启动构建后的BFF和Alpha连接yta_test，合成用户真实登录后提交固定规则/校准、精确重复请求、读取结果与直接Alpha/数据库/fixture完整相等；恰有一份对应事实和一次登记审计。规则列表顺序与数据库一致，规则详情相等，匿名401、注销200并finally清理进程。未请求真实LLM/行情或授予真实审核人权限。
- 双端OpenAPI规范/漂移、Ruff和文档图检查通过。证据见infra/verification/probability-quality-bff-observation.json。无Python、数据库模式/事务或UI变化，未重复前轮726项Python、备份恢复及浏览器套件，也未重建应用镜像。
- 概率页面、组合门槛、正式真实数据/概率资格、实际批准和冠军切换/回滚及部署验收仍待完成。76项IN_PROGRESS/44项TODO不变，goal保持active。


## 后续增量：概率规则选择与固定核验页面

- 校准报告新增规则核验链接，进入/probability-checks/{calibration_id}；固定结果在/probability-quality/{assessment_id}读取。先校验报告身份/模式，再按跨度与环境分页列出已登记规则，无默认选择或自动提交。
- 展示最低拟合/评测/上涨/非上涨样本及最高Brier/ECE六项规则、原始观测和通过状态，保留固定校准/模型/报告及两个样本集合身份。提交结果核对规则、模式、跨度、校准登记和报告引用；错误结果不展示，失败保留选择供手动精确重试。选择或分页改变清除旧结果，固定详情刷新重新读取。
- 从真实登记完整重放导出配套校准fixture。新增3组浏览器场景，覆盖校准页面入口、规则分页与显式选择、失响应后相同正文重试、详情刷新、写入暂停、错误模式/校准拒绝、切换规则清空及默认关闭不请求证据。完整51项通过（40.2秒）。桌面与390px手机截图已检查，保存.local/probability-ui/，无页面整体横向溢出；明细表局部横向滚动。
- 完整TS146项通过（contracts34/BFF112，真实PG启用），pnpm typecheck/build、双端契约漂移、Ruff及文档图检查通过。证据见infra/verification/probability-quality-ui-observation.json。无Python/数据库行为变化，未重复前轮726项Python及备份恢复，应用镜像尚未重建。
- 浏览器使用真实登录和受控的已登记合成REPLAY响应，尚未替代实际容器概率核验页面验证；前轮BFF→Alpha→PG实测证据独立保留。实际容器页面链路、组合门槛、正式真实数据/概率资格、实际批准和冠军切换/回滚及部署验收仍待完成，76项IN_PROGRESS/44项TODO不变，goal保持active。


## 后续增量：概率核验实际容器页面链路

- 重建Alpha training-runtime、BFF与Web，包含迁移020后的概率规则/核验API、BFF及页面。实际镜像摘要见infra/verification/probability-quality-container-browser-observation.json。
- 扩展scripts/check_model_catalog_browser.mjs，在独立Compose项目运行真实浏览器→Web→BFF→Alpha→yta_test，无响应替换。校准报告进入概率核验，未选择前提交禁用，明确选择当前模式/跨度规则后提交，核对规则、模型、校准登记与报告身份、六项明细及原始Brier/ECE。
- 精确重复POST与首次完整相等；打开固定详情并刷新保持可读。浏览器响应与数据库规则/核验完整载荷一致，仅一份对应事实与一次登记审计。qualification仍UNVERIFIED且promotion_allowed=false。
- 桌面/390px手机截图保存在.local/model-catalog-browser/probability-quality-*.png，手机截图已检查，无页面整体横向溢出。原目录/报告/比较、提案创建/重试/11条历史分页、历史质量、两步审核/关闭后原请求重试和校准报告链路保持通过。
- 页面错误0、浏览器仅同源请求；容器非root、只读根、drop ALL及Web凭证/DNS隔离保持通过。研究runner关闭，未启动Jobs或请求真实LLM/行情。注销后清理演练容器、网络和临时配置，确认只剩原PostgreSQL与无关服务；合成事实/审计保留，未配置真实用户审核权限。
- Node语法、Ruff和文档图检查通过。本轮无应用业务或数据库模式变化，未重复前轮146项TS、51项浏览器套件、726项Python和备份恢复。该证据证明本机打包链路，不代表正式数据/概率资格或staging发布验收。
- 组合门槛、正式资格、实际批准和冠军切换/回滚、Hermes完整验证与部署验收仍待完成；76项IN_PROGRESS/44项TODO不变，goal保持active。


## 后续增量：价格与概率的组合证据门槛

- 新增内部ModelQualityGateRequest、combine_quality和ModelQualityGateService.evaluate。请求只接受固定提案、价格核验及概率核验ID，所有者和模式单独由可信调用者传入；按所有者/模式读取提案并完整重放两份核验和候选价格报告。
- 绑定提案候选/参考报告、模型、价格样本集合、校准登记及内容哈希、概率评测样本集合和双方规则跨度。另一模型/校准版本/样本集合的数值通过不能替代当前提案证据。结果保存固定引用、规则和报告哈希，分别保留价格/概率失败项，numeric_pass取两者同时通过；资格仍UNVERIFIED，promotion_allowed=false。
- 新增4组真实PG回归，覆盖确定性与内容哈希、跨所有者/模式404、原生重放不可用503、无审计写入、实际其他模型概率核验/缺失参考拒绝、严格规则双失败、重算哈希后的模型/校准/样本引用替换及正文权限注入拒绝。首轮2通过/2失败，定位为价格契约序列化未保留指标字段别名；改用原始传输别名计算哈希后4组专项全部通过。
- 该服务目前只读，尚未登记组合事实或接入审核提交/界面；未把已有仅价格审核记录改写为组合审核。完整Python730项通过（118.66秒，4条既有pandas警告，无跳过、真实PG必需）。Ruff、190文件格式、Alpha契约漂移及文档图检查通过；证据见infra/verification/model-quality-gate-observation.json。
- 无HTTP、TS、Web或数据库模式/写入变化，未重复此前146项TS、51项浏览器套件、备份恢复或重建镜像。组合事实登记/审核接入、正式数据与概率资格、实际批准及冠军切换/回滚和发布验收仍待完成；76项IN_PROGRESS/44项TODO不变，goal保持active。


## 后续增量：组合核验不可变登记

- 新增迁移021和ModelQualityGateRegistry，组合事实通过外键绑定提案、价格核验与概率核验。创建先按所有者/模式完整重放证据，再以内容身份并发去重；一份事实对应一次所有者登记审计，审计失败同事务回滚。登记审计不构成人工批准。
- 新表拒绝UPDATE、DELETE与TRUNCATE，运行角色只获SELECT/INSERT。读取先用提案所有者及服务端模式限制访问，再重放完整证据并核对内容哈希、引用列与结果；重算外层哈希不能伪造批准、资格或组合数值结果。
- 新增6项真实PG专项并全部通过，覆盖四路并发、精确重试、跨所有者/模式创建读取拒绝、单事实单审计、所有者/运行角色不可变保护、审计回滚和重算哈希/引用列篡改。全新专用库完整Python736项通过（123.11秒，4条既有pandas警告，无跳过）。证据见infra/verification/model-quality-gate-registry-observation.json。
- 备份验证器按依赖顺序完整重放两类核验、提案与候选报告，再核对组合事实。migrator镜像重建后实际新建001–021、升级、重复执行、已有证据保留、角色隔离及演练库清理通过；正常应用yta库未迁移。
- 实际备份恢复40张表、38个不可变保护及6个序列，包含46预测、20结算、9证据、40提案、43价格规则/39核验、29概率规则/23核验、14审核和5组合核验。采集/投影重建与异常拒绝通过，运行角色逐条重放恢复后的5份组合核验通过；源演练库已清理，恢复库和私有备份保留。Ruff、191文件格式、Alpha契约漂移及文档图检查通过。
- 无HTTP、TS或Web变化，未重复此前146项TS和51项浏览器套件，未重建应用镜像。组合审核入口/界面、正式真实数据与概率资格、实际批准和冠军切换/回滚及部署验收仍待完成；76项IN_PROGRESS/44项TODO不变，goal保持active。


## 后续增量：组合核验私有接口与严格响应

- 新增POST /internal/v1/model-quality-gates及固定ID GET，仅服务Token与可信BFF所有者头可访问，工具Token拒绝，实验开关默认关闭。正文仅提案、价格核验和概率核验ID；模式来自服务端，所有者归属由登记器校验。精确重复提交返回同一不可变结果。
- 新增严格组合响应契约，保存所有固定引用和哈希，核对两类失败项的顺序/去重、双方numeric_pass、整体结果及完整阻止原因；重算外层哈希不能伪造批准、资格或互相矛盾的检查结果。来源语义仍由登记器完整重放，响应校验不替代资格。
- 新增两组真实PG HTTP及一组重算哈希契约回归，专项3项通过。覆盖匿名/工具Token、缺失/非法所有者、正文权限注入、跨所有者创建读取404、精确重复提交单审计、缺失/非法ID、默认关闭、跨模式、存储未配置及原生重放不可用503。完整Python739项通过（137.30秒，4条既有pandas警告，无跳过、真实PG必需）。证据见infra/verification/model-quality-gate-api-observation.json。
- Alpha OpenAPI与BFF依赖契约同步，34项共享契约测试、双端规范/漂移、Ruff和192文件格式检查通过。无数据库模式/事务、BFF业务或UI变化，未重复备份恢复、完整BFF/浏览器套件或重建镜像。
- 组合BFF与审核提交/页面引用、正式真实数据及概率资格、实际批准和冠军切换/回滚及部署验收仍待完成，76项IN_PROGRESS/44项TODO不变，goal保持active。


## 后续增量：组合核验BFF入口与所有者隔离

- 新增公开POST /model-quality-gates及固定ID GET。所有者只来自已认证会话，实验开关默认关闭；POST复用同源、CSRF及写入准入。严格拒绝额外正文/查询和非法ID，客户端不能指定所有者、模式或通过状态。
- 新增共享组合响应契约及BFF读取器，核对两组失败项、整体结果与阻止原因、服务端模式、三项请求引用、固定ID及完整内容哈希；非法上游固定502且不透出额外字段。完整语义重放和金融计算留在Alpha。
- 从真实登记以运行角色完整重放导出组合fixture。新增1项共享契约、2项BFF校验及1组真实PG登录HTTP回归，扩展默认关闭与写入暂停覆盖。完整TS150项通过（contracts35/BFF115，真实PG启用），pnpm typecheck/build通过。
- 实际启动构建后的BFF与Alpha连接yta_test。随机合成用户真实登录，为自己的新提案登记组合核验；重复提交、固定读取、直接Alpha与数据库完整相等，单事实单所有者审计。匿名401，其他所有者的组合读取/提案创建均404；注销200并finally清理进程，保留合成事实，不请求真实行情/LLM或授予真实审核权限。
- 双端OpenAPI规范/漂移、Ruff及文档图检查通过。证据见infra/verification/model-quality-gate-bff-observation.json。无Python、数据库模式/事务或UI行为变化，未重复前轮739项Python、备份恢复、浏览器套件，也未重建应用镜像。
- 组合审核提交与页面引用、正式真实数据及概率资格、实际批准和冠军切换/回滚及部署验收仍待完成；76项IN_PROGRESS/44项TODO不变，goal保持active。


## 后续增量：绑定组合事实的新版审核预检

- 新增CombinedReviewRequest、combined_review_decision及CombinedModelReviewService，输出model-review-decision-v2。请求只接受提案、quality_gate_id、审核动作及理由；审核人、请求UUID和模式独立传入，调用前仍须由BFF验证审核权限，模型输出不能提供授权。
- 服务读取提案真实所有者并完整重放组合登记，绑定提案ID/内容、候选/参考、模型及样本集合。同模型的另一提案也不能复用原提案组合结果；重算外层哈希后的提案/模型/样本/参考替换仍被拒绝。
- 请求批准保留历史资格未通过，并分别记录价格与概率数值失败；请求拒绝输出REJECTED，仍保留两组失败项与固定规则/核验引用。两种动作均promotion_allowed=false，不自动切换冠军。
- 新增4组真实PG回归且专项通过，覆盖确定性/内容哈希、读取重放且无审计写入、跨模式404、运行时不可用503、另一提案拒绝、双数值失败及正文权限字段拒绝。修正测试model_copy传入字符串UUID产生的序列化警告，完整Python743项通过（151.49秒，4条既有pandas警告，无跳过、真实PG必需）。Ruff、193文件格式、Alpha契约漂移及文档图检查通过。证据见infra/verification/model-review-v2-preflight-observation.json。
- 本轮是内部只读预检，不写v2审核事实、不改变v1审核请求或记录语义。无HTTP、数据库模式/事务、TS或Web变化，未重复150项TS、51项浏览器、备份恢复或重建镜像。新版审核持久化与混合历史链/旧请求重试、接口及页面、正式资格与批准、冠军切换/回滚和部署验收仍待完成；76项IN_PROGRESS/44项TODO不变，goal保持active。


## 后续增量：新旧审核混合链与组合事实持久化

- 新增迁移022，在model_reviews追加quality_gate_id，并用版本化约束区分v1价格核验与v2组合核验引用。旧payload和请求哈希不改写；v2仍保留对应价格核验外键，组合事实另有外键绑定。
- ModelReviewRegistry.create_combined复用原提案事务锁、revision、预期前驱和拒绝关闭规则，与v1共用审核人/模式/请求键空间。跨版本同键异参409，精确旧请求在新增或终止审核后仍返回原记录；审核事实与审核人审计同事务。
- 读取和分页逐条按版本重放完整前驱链，核对v2组合证据与价格引用；Alpha响应契约支持两版记录。抽出共有失败项类型避免契约循环依赖，不改变旧版字段含义。恢复验证器先重放组合事实，再验证混合审核链。
- 新增4组真实PG回归并与旧版重试测试一起专项5项通过（35.72秒），覆盖v1→v2→v1拒绝关闭、旧请求重试、分页与响应契约、四路v2去重、v1/v2并发只有一份成功、审计回滚、引用列/重算哈希篡改及v2拒绝记录。完整测试与恢复证据见infra/verification/model-review-v2-registry-observation.json。
- migrator镜像重建，实际新建001–022、从旧版本升级、重复执行、已有证据保留、角色隔离与演练库清理通过。恢复工具的完整套件超时上限由180秒调整到300秒，以容纳新增完整链重放测试，不改变任何通过判据。
- 35项共享契约测试、双端OpenAPI规范/漂移、Ruff和193文件格式检查通过。v2尚无公开创建入口，BFF/页面仍待支持混合记录；未重建应用镜像或重复完整TS/浏览器套件。正式资格、实际批准及冠军切换/回滚、Hermes与部署验收仍待完成；76项IN_PROGRESS/44项TODO不变，goal保持active。

- 混合链最终核验：完整Python747项通过（169.45秒，4条既有警告，无跳过、真实PG必需）；恢复40张表、38个不可变保护和6个序列，包含21条审核和16份组合核验。运行角色完整重放21条审核（v1共17条、v2共4条），均不允许晋升；采集和投影重建、异常拒绝验证通过。结果已汇总至上述registry证据文件。


## 后续增量：新版组合审核私有提交接口

- Alpha的POST /internal/v1/model-reviews接受严格的旧版价格核验请求或新版组合核验请求，按校验后的类型调用对应登记器。旧请求字段及语义保留；同时提供两种证据字段会被拒绝。审核人和幂等键只能由可信服务头提供，服务端决定模式，实验功能默认关闭。
- 将原审核HTTP集成测试参数化用于两版请求，真实PostgreSQL专项4项通过（21.48秒，4条既有警告）。覆盖匿名/工具Token、缺失或非法身份/幂等键、正文权限注入、混合字段拒绝、创建/固定读取/精确重试、同键异参、前驱冲突、拒绝关闭后原请求重试、跨模式和原生证据重放不可用。首次失败发生在测试准备误调用旧版登记方法，修正按请求类型准备记录后全部通过。
- Alpha与BFF依赖OpenAPI同步，pnpm typecheck、35项共享契约、双端契约漂移、Ruff、193文件格式及文档检查通过。证据见infra/verification/model-review-v2-api-observation.json。无数据库事务变更，未重复上轮完整747项Python和备份恢复，未重建应用镜像。
- 公开BFF及页面仍待支持v2提交与混合历史；该私有接口不授予真实用户审核权限，不能替代正式资格。实际批准、冠军切换/回滚、Hermes及部署验收仍待完成；76项IN_PROGRESS/44项TODO不变，goal保持active。


## 后续增量：组合审核BFF提交与混合记录展示

- 公开POST /model-reviews支持严格v1价格请求或v2组合请求，拒绝同时提供两种证据字段。审核人仍来自已认证会话及服务端明确配置；同源、CSRF、请求键、写入准入和默认关闭保持。新版响应校验绑定模式、审核人、请求键、原请求quality_gate_id、动作、理由、前驱和内容哈希；新旧版本不能互相替代。
- 共享响应改为按schema_version区分的两版契约，核对组合证据身份、阻止原因、失败项去重和前驱修订。混合历史继续验证倒序连续链、游标与提案归属。页面历史和固定详情展示相应价格/概率链接与组合身份；旧表单恢复/重试显式拒绝错误版本响应。
- 从真实登记以运行角色完整重放导出v2 fixture。新增共享契约、BFF响应/混合分页测试，现有公开权限HTTP测试覆盖两版提交。完整TS154项通过（contracts36/BFF118，真实PG启用），typecheck/build通过。
- 新增浏览器混合历史、详情刷新和手机链接/宽度验证，完整52项通过（41.6秒）。首次新用例因点击后立即刷新抢在导航完成前而失败，增加URL等待后通过；原51项保持通过。浏览器证据使用真实登录与受控合成响应，不代替实际数据库链路。
- 实际构建BFF与当前Alpha连接yta_test，以临时合成审核人真实登录，新建提案与组合核验、提交v2阻止记录，再提交v1拒绝关闭。固定读取、混合历史与数据库两份载荷完整相等，恰有两次审核人审计；关闭后原v2请求精确重试返回原记录，新请求409。注销并清理进程，保留合成事实，无真实审核权限或行情/LLM调用。
- 证据见infra/verification/model-review-v2-bff-observation.json。双端OpenAPI漂移、Ruff及文档检查通过。无Python/数据库事务变化，未重复前轮完整Python及备份恢复，未重建应用容器。组合证据选择与v2表单、正式资格、实际批准与冠军切换/回滚、Hermes及部署验收仍待完成；76项IN_PROGRESS/44项TODO不变，goal保持active。


## 后续增量：审核人跨所有者组合核验入口

- 为组合审核表单补齐权限前置：新增私有及公开model-review-quality-gates创建/固定读取，仅BFF配置的审核人可调用。POST复用会话、同源、CSRF与写入准入，正文不能指定所有者或审核人。普通所有者接口不扩权。
- Alpha从不可变提案查询真实所有者，按原所有者范围完整重放组合证据。登记复用内容身份与事务，将首次审计归于实际审核人；并发或随后所有者重复创建不追加事实、不改写首次审计。审核入口与原入口共享同一内容身份，登记本身仍不是批准。
- 新增真实PG并发/跨所有者审计与HTTP测试，并覆盖原组合登记回归，专项7项通过（18.35秒，4条既有警告）。新增BFF登录HTTP测试覆盖普通账户/伪造身份拒绝、CSRF/同源、非法正文/查询、严格上游响应及暂停写入但允许读取。完整TS155项通过（contracts36/BFF119，真实PG启用），typecheck/build通过。
- 实际BFF→Alpha→yta_test由一名合成用户提交提案、另一名合成审核人登记组合核验并提交v2阻止记录，再以v1拒绝关闭。新入口固定读取/重复创建/直接Alpha与数据库一致，首次审计归于审核人；原所有者入口对该审核人404。混合历史、两份审核及两次审核人审计、关闭后原请求重试均通过，注销并清理进程。
- 本轮无页面变化，未重复前轮52项浏览器测试或重建应用容器。双端OpenAPI同步/漂移、Ruff、193文件格式和文档检查通过。组合选择与新版提交表单、正式资格及冠军切换/回滚等仍待完成；76项IN_PROGRESS/44项TODO不变，goal保持active。

- 最终核验：全新专用库Python750项通过（183.24秒，4条既有警告，无跳过），实际恢复40张表、38个不可变保护与6个序列，重放24条审核及19份组合核验。采集/投影重建与异常拒绝通过；演练源库清理，恢复库和私有备份保留。证据见infra/verification/model-review-quality-gate-observation.json。


## 后续增量：组合证据选择与新版审核表单

- 审核页新增组合证据区域，分别显式选择价格与概率规则、核验后再点击固定组合。概率组件支持嵌入及结果回调，使用独立单选组避免与价格规则互相取消；候选报告提供固定校准引用，禁止自行替换校准版本。
- 组合响应绑定提案内容、候选/参考报告、模型/样本、两份核验、校准身份、概率报告/样本集合、双方规则及数值标志。创建通过审核人专用入口，不能自动提交审核决定；失响应手动重试相同证据对。重新核验或更换规则清除旧组合，即使返回相同核验内容也需重新固定。
- 新审核使用v2 quality_gate_id，提交前保存请求键、正文与前驱。恢复同时接受旧v1或新v2请求，分别核对返回版本和对应证据，不自动升级旧请求。原同键重试、存储失败禁止发送、前驱冲突和拒绝关闭流程保持。
- 从真实校准/概率登记完整重放导出与价格fixture一致的合成REPLAY证据。扩展原审核失响应/刷新、存储失败/关闭用例至新版表单，新增组合失响应/错误提案/重新核验清空及旧版已保存请求精确恢复测试。完整54项浏览器通过（43.0秒），完整TS155项通过（contracts36/BFF119，真实PG启用）。
- 桌面与390px手机截图保存.local/combined-review-form/，手机截图已检查、无页面整体横向溢出。typecheck/build、双端契约漂移、Ruff及文档检查通过。证据见infra/verification/model-review-v2-form-observation.json。
- 本轮浏览器以真实登录和受控合成响应验证交互，更新后的实际容器页面链路仍待执行；未重建应用镜像。无Python/数据库行为变化，未重复前轮750项Python及备份恢复。正式资格、实际批准/冠军切换与回滚、Hermes和部署验收仍待完成；76项IN_PROGRESS/44项TODO不变，goal保持active。


## 后续增量：组合审核实际容器页面链路

- 重建当前Alpha training-runtime、BFF与Web，包含迁移022对应的新旧审核、审核人跨所有者组合入口与新版表单。镜像摘要和完整观察见infra/verification/model-review-v2-container-browser-observation.json。
- 更新scripts/check_model_catalog_browser.mjs，真实浏览器→Web→BFF→Alpha→yta_test，无响应替换。价格/概率分别显式选规则并核验，再固定组合、填写动作与理由提交v2审核。组合重复请求完整相等，第二次审核重新核验/固定仍引用同一组合。
- 首次请求批准记录BLOCKED；固定详情刷新可读，随后v2拒绝记录以正确前驱形成revision2并关闭提案。页面隐藏继续提交，历史完整相等；关闭后精确重试首次请求仍返回原记录。数据库恰有两份审核、两次实际审核人审计，以及一份组合事实和一次登记审计。
- 原模型目录/过滤/比较、提案创建/分页/重试、价格质量、校准报告及独立概率核验链路保持通过。桌面/390px手机截图保存在.local/model-catalog-browser/，新版手机详情已检查，无整体横向溢出。页面错误0，浏览器请求仅同源；非root、只读根、drop ALL及Web凭证/DNS隔离检查保持通过。
- 注销后演练容器、网络和临时配置已清理，只剩原PostgreSQL与无关服务；合成事实保留。未配置真实审核权限、未调用行情/LLM或启动研究Jobs。Node语法、Ruff和文档检查通过；本轮无业务代码或数据库变化，未重复前轮155项TS、54项浏览器、750项Python与恢复套件。
- 本机打包链路证据不代表正式数据资格、实际批准或staging发布验收。冠军切换/回滚、Hermes完整验证及其他任务仍待完成；76项IN_PROGRESS/44项TODO不变，goal保持active。


## 后续增量：独立回测执行与双边成本

- 核对正式晋升前置后，确认数据湖/训练证据仍未取得来源资格；本轮未改写资格或批准逻辑。开始TASK-065的独立回测执行基础，新增内部simulate_trade与强制显式费用配置。执行口径详见[回测协议](backtest-execution-protocol.md)。
- 决策必须在输入信息可用后且次交易日开盘前；该次日开盘代理入场，持有1/3/5/10/20个交易日后开盘代理出场。原始价格与拆股计算独立于收盘研究标签；双边滑点/佣金按实际买卖金额计算，LONG/CASH都保留同窗口同成本LONG基准。Decimal50/HALF_EVEN12，结果绑定所有输入及内容哈希。
- REPLAY/DEMO隔离，拒绝LIVE_RESEARCH或混合模式；日期、缺失交易日、零成交量、公司行动覆盖、非交易日拆股和未来信息严格检查，不能顺延或回填价格。历史开盘价不保证真实可成交，资格仍UNVERIFIED。当前尚无多笔资金调度、停牌/退市裁定、幸存者偏差控制或完整报告。
- 20项专项通过（0.17秒），覆盖精确双边成本、拆股、现金与基准、不同全局Decimal精度、五种持仓跨度与节假日、缺失入场/持仓/出场、零成交量、时间窗口、非法费用/模式/动作及model_copy再校验。全量Python770项通过（205.79秒，4条既有警告，无跳过、真实PG必需）。Ruff、196文件格式、Alpha契约漂移及文档检查通过。
- 证据见infra/verification/backtest-execution-observation.json。无持久化、HTTP、TS或页面变化，未重复前轮TS/浏览器/备份恢复或重建容器。TASK-065由TODO转为IN_PROGRESS，目前77项IN_PROGRESS/43项TODO，仍无任务凭局部测试满足完整DoD，goal保持active。


## 后续增量：固定观察快照的多信号回测报告

- 新增BacktestSignal和build_report，全部信号共享固定观察快照；按入场时间排序，拒绝重复身份/同一开盘歧义。单资金槽占用完整窗口，CASH与LONG共用调度；重叠信号明确记录EXCLUDED_OVERLAP及占用者，不能按事后收益选择。
- 缺失/零成交量/未到期等证据问题逐笔记录EVIDENCE_UNAVAILABLE及错误码，失败不能释放窗口让重叠信号事后入选。未知或运行时错误继续失败，不伪装成缺行情。后续独立单次结果仍可查看，但权益未知后不恢复虚构资金，最终收益及回撤为null；空输入为EMPTY而非零收益。
- 已知权益从1开始，以各笔净收益复利并在每次退出HALF_EVEN12量化；同时计算同调度窗口LONG成本基准。曲线/回撤只代表退出时点的已实现权益，不冒充逐日盯市。来源模型/信号哈希绑定报告，但明确标记调用者提供且未重放，不构成资格。
- 新增8项报告测试，与单次执行合计28项专项通过（0.18秒）。覆盖顺序确定性、同一开盘退出再入场、现金保留窗口、重叠显式记录、证据失败后保持未知权益、失败仍占位、空集合/重复身份、成本回撤、来源内容变化、模式与运行时错误。首轮发现model_copy布尔跨度经过JSON序列化转为整数，改为先按Python原值重新校验后拒绝该输入。
- 全量Python778项通过（205.65秒，4条既有警告，无跳过、真实PG必需）；Ruff、198文件格式、Alpha契约漂移及文档检查通过。合成示例中3信号分别记录2计算/1重叠，或1证据不足/1重叠/1计算且总收益null，输出保存在.local/backtest-report-examples.json；证据见infra/verification/backtest-report-observation.json。
- 无持久化、HTTP、TS或页面变化，未重复备份恢复/前端套件或重建镜像。信号来源重放、事件裁定、一般资金分配、登记/API/页面及完整DoD仍待完成；77项IN_PROGRESS/43项TODO不变，goal保持active。


## 后续增量：登记模型回测信号原生重放

- 新增RegisteredBacktestRequest和只读BacktestSignalService。请求仅接受信号UUID、固定模型ID、输入快照与决策时间；服务读取登记并调用原生实验推理，按量化收益正值LONG、其余CASH确定动作，持仓跨度固定为模型跨度。调用者不能覆盖动作/来源哈希/持仓跨度。
- 来源保留实际登记时间、登记内容/产物文件哈希、完整推理、决策与映射规则。历史模式以训练截止检查时点可用性，同时明确披露实际登记时间，不宣称当时已上线。原生推理已重放，训练证据未重新训练或取得来源资格，仍UNVERIFIED。
- 所有信号（含随后重叠排除项）先重放，再构造固定观察快照报告，输出registered-next-open-backtest-report-v2，按调度顺序绑定来源。原v1调用者信号接口保持原有未重放标记。固定持仓h从次日开盘入场后计数，因此退出比收盘标签目标再晚一个交易日，协议明确披露此执行映射。
- 新增5项真实PG专项并全部通过（1.33秒），使用实际登记与原生LightGBM，验证直接推理相等、来源/报告哈希与顺序、只读无审计、正文字段注入、模式/缺失模型/决策窗口/未来训练截止拒绝、原生运行时不可用503以及观察价格变化不改变信号来源。
- 完整Python783项通过（202.24秒，4条既有警告，无跳过、真实PG必需）。Ruff、199文件格式、Alpha契约漂移及文档检查通过。证据见infra/verification/backtest-signals-observation.json。无持久化、HTTP、TS或UI变化，未重复备份/前端套件或重建镜像。
- 回测报告登记、公开接口/页面、一般组合分配与事件裁定、完整数据/训练来源资格仍待完成；77项IN_PROGRESS/43项TODO不变，goal保持active。


## 后续增量：回测报告不可变登记与恢复重放

- 新增迁移023及BacktestRegistry，登记固定输入、完整v2报告、代码哈希；模型集合由独立不可变关联表外键绑定。输入1–500条请求按UUID规范排序，同内容并发去重，报告/模型引用/一次系统登记审计同事务，失败全部回滚。运行角色SELECT/INSERT，两个表均拒绝UPDATE/DELETE/TRUNCATE。
- 读取按模式隔离，重新执行登记模型原生推理与完整报告计算，核对规范输入、来源模型集合、语义及内容身份。重算内外哈希仍不能伪造收益；历史代码哈希保留，当前重放语义必须相同。INCOMPLETE报告可固定保存，收益null；成本/证据变化登记新版本，不改写旧结果。
- 加固单次执行与共同观察快照的Python原值再校验，布尔成交量不能经JSON序列化变成可交易整数。新增2项回归。登记专项10项先通过（2.13秒），随后增加不完整报告/成本版本测试，完整套件全部通过；覆盖四路并发、逆序输入去重、单审计、两表六类不可变操作、审计回滚、引用遗漏、重算哈希篡改、模式及原生不可用。
- 全新专用库完整Python796项通过（194.23秒，4条既有警告，无跳过、真实PG必需）。实际备份恢复42张表、40个不可变保护与6个序列，完整重放11份回测报告；运行角色另行逐条读取重放11份（10份COMPLETE、1份INCOMPLETE且收益null）。采集/投影重建与异常拒绝保持通过，源演练库清理，恢复库和私有备份保留。
- migrator重建后实际001–023新建/升级、已有证据保留、重复执行、角色隔离和清理通过；正常yta库未迁移。Ruff、200文件格式、Alpha契约漂移及文档检查通过。证据见infra/verification/backtest-registry-observation.json。
- 本轮无HTTP/TS/页面变化，未重复前端套件或重建应用镜像。回测公开契约/API/页面、一般组合分配与事件裁定、完整训练来源资格及完整DoD仍待完成；77项IN_PROGRESS/43项TODO不变，goal保持active。


## 后续增量：回测严格响应契约与Alpha私有接口

- 新增严格Python响应契约及安全投影，Alpha私有POST/GET /internal/v1/backtest-reports在返回前完整重放登记模型和报告。仅服务Token可调用，匿名/工具Token拒绝；实验功能默认关闭，模式由服务端确定，数据库或原生推理不可用时返回失败，不回退fixture。
- 响应保留登记身份、输入哈希、代码哈希与完整报告，不返回原始输入快照。登记身份依赖完整私有输入，不能仅凭投影input_hash独立重建；完整登记验证仍由Alpha负责。报告契约检查来源/动作/持仓/模式/成本引用、重叠占位、逐笔成本收益、退出权益复利与回撤，保持Decimal文本精度及不完整报告null语义。
- 新增3项真实PG专项通过（1.89秒），覆盖权限/正文注入、默认关闭/模式/存储/原生不可用、创建读取精确重试且单次审计，以及重算内容哈希后篡改成本、权益、来源、计数、重叠或将null改零的拒绝。完整Python799项通过（208.42秒，4条既有警告，无跳过，真实PG必需）。
- 导出并再次严格验证COMPLETE与INCOMPLETE两份合成报告fixture。共享契约36项回归、Alpha/BFF契约漂移、Ruff及202文件格式检查通过；共享TS通过不代表已实现回测Zod契约。证据见infra/verification/backtest-api-observation.json。
- 无持久化行为或页面变化，未重复恢复/浏览器套件或重建应用镜像。公开BFF响应契约、目录与页面、一般资金分配/事件裁定、正式来源资格及完整DoD仍待完成；77项IN_PROGRESS/43项TODO不变，goal保持active。


## 后续增量：回测共享契约与BFF固定读取

- 新增严格共享Zod报告契约，逐层拒绝额外字段，保留十进制文本并用词法检查精确范围；核对来源/模型/动作/模式/成本/时间引用、资金占用及重叠、数量、曲线对应和证据不足后持续null。报告及每份来源/单次执行的内容哈希由BFF逐层验证。金融收益与权益运算仍由Alpha完整重放，不在BFF重复计算。
- 新增GET /api/yta/v1/backtest-reports/{id}，登录与实验功能开关前置，拒绝额外查询/非法身份。仅私网服务Token访问Alpha，客户端不指定模式或提交原始输入。错误不回显上游载荷；日志将报告身份归一为路由模板。
- 新增共享契约、BFF读取及登录HTTP测试。完整TS160项通过（contracts38/BFF122，真实PG运行角色）；随后补充路由日志脱敏测试，回测BFF专项3项通过。首次全量误用owner连接，既有运行角色权限/就绪检查3项失败；改为yta_app后完整通过，未改动这些断言。
- 实际构建BFF连接Alpha与yta_test，以临时合成用户登录，读取COMPLETE/INCOMPLETE两份固定报告，均与直接Alpha完整重放及固定fixture完全相等，不完整收益为null且无原始输入快照。注销并清理服务进程，保留合成账户/审计；不调用LLM或真实行情。实际观察见.local/backtest-bff-live.json。
- typecheck/build、双端契约漂移、Ruff及文档检查通过。证据见infra/verification/backtest-bff-observation.json。本轮无Python或持久化行为变化，未重复前轮799项Python、恢复或浏览器套件，未重建应用镜像。
- 报告目录/页面、用户生成流程、正式来源资格与完整DoD仍待完成；77项IN_PROGRESS/43项TODO不变，goal保持active。


## 后续增量：回测目录、筛选与稳定分页

- 新增Alpha私有及BFF登录限定回测目录GET，默认实验开关关闭。支持model_id包含筛选、COMPLETE/INCOMPLETE状态筛选和1–50条分页，按登记时间/身份倒序；游标必须属于当前模式与筛选范围。LIVE_RESEARCH不能查询为历史回测，非法或跨范围游标明确失败。
- 流式逐条读取已存不可变载荷，验证规范输入、登记/报告身份、报告内容与模型外键引用，仅投影模型、时间、状态和数量。目录明确verification=stored-integrity-checked-native-replay-on-detail，不声称已执行原生推理；固定详情仍完整重放，原生运行时不可用时可以失败，目录可读不代表资格已取得。
- 新增3项真实PG目录测试，覆盖分页无重复、筛选/模式/游标、只读无新增登记审计、无原生重放、重算哈希后收益/输入/模型引用篡改、HTTP服务权限和默认关闭。完整Python802项通过（210.23秒，4条既有警告，无跳过，真实PG必需）；随后将只读审计断言限制到本测试报告，避免并行无关请求干扰，3项专项再次通过（1.98秒）。
- 导出实际登记摘要fixture，新增共享契约、BFF范围校验及登录HTTP测试，完整TS164项通过（contracts39/BFF125，真实PG运行角色）。查询拒绝额外模式、重复参数/非法大小/状态/身份；错误响应不泄露上游字段。
- 实际构建BFF→Alpha→yta_test，以合成用户登录完成4页/4份报告读取，无重复且每页与直接Alpha相等，状态筛选通过；原完整/不完整固定详情仍与fixture和Alpha完全相等。注销、临时进程清理完成；观察见.local/backtest-catalog-live.json。
- typecheck/build、双端契约漂移、Ruff、203文件格式及文档检查通过，证据见infra/verification/backtest-catalog-observation.json。无数据库事务或迁移变化，未重复恢复、浏览器套件或重建应用镜像。报告页面、用户生成流程、正式来源资格与完整DoD仍待完成；77项IN_PROGRESS/43项TODO不变，goal保持active。


## 后续增量：回测目录与固定报告页面

- 新增/backtests及/backtests/{id}，实验模型目录提供全局回测和指定模型链接，沿用实验开关/登录与主导航。目录支持完整性筛选、模型范围、分页和固定身份导航，明确目录未执行原生推理；切换筛选取消旧请求并清除游标。
- 详情在完整重放成功后展示覆盖数量、策略/同窗口LONG基准净收益、已实现回撤、双边佣金/滑点及时间口径。原始十进制文本保持，零值正常显示；证据不足的总收益/回撤为“无法计算”，退出权益为“未知”。逐笔保留重叠、缺行情等原因，每页20条，可展开模型/决策/训练/实际登记/执行价格/拆股及固定哈希。明确退出点回撤不代表逐日盯市，实际登记不证明历史部署。
- 新增6项浏览器用例：默认关闭不请求、分页/筛选/固定导航、完整报告/刷新/手机、缺证据空值与原因、身份不符/原生不可用后同身份重试、迟到筛选响应与错误模式拒绝。完整60项通过（42.4秒）；首次59项通过、1项新测试因选择器匹配两个登记标签而失败，限定到展开信号后全通过。完整TS164项通过（contracts39/BFF125，真实PG运行角色）。
- 实际Web→BFF→Alpha→yta_test，无响应替换，合成用户浏览器登录后读取4份目录记录、完整及不完整详情，响应与固定fixture及直接Alpha重放完全相等，三个缺证据指标保持无法计算。页面错误0、仅同源请求，390px手机无整页横向溢出。注销并清理浏览器和临时服务，正常构建随后恢复默认代理配置。
- 桌面/手机截图保存在.local/backtest-ui/，完整与不完整手机及实际目录截图已检查。typecheck/build、双端契约漂移、Ruff及文档检查通过。证据见infra/verification/backtest-ui-observation.json。
- 本轮无Python或持久化变化，未重复前轮802项Python与恢复；验证实际本机构建但未重建/部署应用镜像。用户生成流程、正式来源资格、实际晋升/回滚、Hermes和其他任务完整DoD仍待完成；77项IN_PROGRESS/43项TODO不变，goal保持active。


## 后续增量：回测实际容器页面验收

- 重建当前Alpha training-runtime、BFF与Web镜像，覆盖迁移023回测登记、私有重放、公开目录/详情及桌面/手机页面。镜像摘要与完整观察见infra/verification/backtest-container-browser-observation.json。
- 扩展scripts/check_model_catalog_browser.mjs，真实浏览器→Web→BFF→Alpha→yta_test，无响应替换。验证模型及完整性筛选、COMPLETE/INCOMPLETE详情与刷新、逐笔重叠记录和展开依据；响应与固定fixture及数据库报告完全相等。不完整报告的策略/基准收益及回撤均显示无法计算，完整报告保留原十进制零值。
- 390px手机无整页横向溢出，页面错误0，浏览器仅同源请求。截图位于.local/model-catalog-browser/backtest-*.png，不完整手机截图已检查。非root、只读根、drop ALL、仅Web环回端口，以及Web无Alpha/PG凭证和DNS访问的隔离检查保持通过。
- 原模型目录/独立报告/比较、提案分页、价格质量、组合审核、校准与概率质量链路同时通过。首次脚本在完整详情阶段失败，信号身份选择器限定到展开的执行依据后全链通过，并补充HTTP/载荷/渲染/刷新的阶段定位，未修改业务代码或验收断言。
- 注销后临时容器、网络和配置已清理；只保留原PostgreSQL、无关服务及合成不可变事实。未配置真实审核权限、未调用LLM/行情或启动研究Jobs。Node语法、Ruff与文档检查通过；同步修正TASK-059/067摘要中已过时的BFF/界面与审核实现描述，保留正式资格和实际晋升的未完成状态。
- 本轮无业务代码或持久化行为变化，未重复前轮802项Python、164项TS、60项浏览器及备份恢复。容器演练不代表staging验收或正式来源资格；用户生成流程、实际批准/冠军切换与回滚、Hermes及其余任务仍待完成。77项IN_PROGRESS/43项TODO不变，goal保持active。


## 后续增量：原始训练证据重新训练核对

- 在已有文件哈希校验/推理重放之外，新增离线replay-training CLI。使用原始TrainingInput重新构造标签、指标特征、walk-forward/purge/embargo及真实CPU训练，逐项比较ModelArtifact全部19个字段，包括模型文本、预测与误差、配置、样本集合、源码和日历。Python原值先重新校验，model_copy绕过校验的布尔配置不能经序列化变为整数。
- 完全一致MATCHED退出0；完成训练但有差异时保存MISMATCH报告并退出2，记录匹配/差异字段和双方内容/模型/源码哈希。错误退出1且不产生成功记录；新命令的领域错误只输出错误码，运行时不可用不伪造复现结果，旧命令错误格式保持。
- 输出绑定规范训练输入哈希和可信原制品文件SHA，报告不包含原始行情、标签或模型文本；文件0600权限原子创建，已有路径不覆盖，返回文件SHA。状态固定UNVERIFIED且不允许晋升。输入证据需另外保管，当前没有新数据库训练事实或审批接入。
- 7项专项通过（3.59秒，4条既有警告），覆盖原生重新训练、指标/源码/配置差异、原值校验、缺失标签证据、运行时不可用、CLI三类退出码、内容哈希及原子不覆盖。独立CLI对1/3/5/10/20五跨度均得到MATCHED、19字段一致，示例观察及报告保存在.local/training-replay-examples.json和.local/training-replay/。
- 完整Python809项通过（219.80秒，4条既有警告，无跳过，真实PG必需）。Ruff、205文件格式、Alpha契约漂移及文档检查通过。证据见infra/verification/training-replay-observation.json，使用方法见README。
- 无HTTP/TS/UI或数据库事务变化，未重复前端、恢复套件或重建应用镜像。精确重训只是本次代码/输入/环境下的一致性，不能替代供应商授权、point-in-time资格、历史部署证明或人工批准。正式资格与晋升等任务仍待完成；77项IN_PROGRESS/43项TODO不变，goal保持active。


## 后续增量：原始训练证据不可变登记与恢复重训

- 新增迁移024与TrainingEvidenceRegistry，保存完整TrainingInput、模型外键、登记/制品文件哈希和重训比较结果。报告由登记模型及真实重新训练生成，不能提交成功标记代替计算。MATCHED/MISMATCH均保留，内容去重，记录及一次系统审计同事务；运行角色SELECT/INSERT，owner及运行角色均不能修改或删除事实。
- 读取按模式隔离，从保存的输入重新训练，核对模型登记、输入、内外哈希及全部比较语义。历史核验器源码哈希保留；当前重放必须得到同样的比较结论和字段，不以旧MATCHED冒充代码变化后仍可复现。原生运行时不可用明确失败。
- 新增register-training-evidence与read-training-evidence私有CLI，连接/模式取环境，输出身份、状态、输入哈希和差异字段摘要，不输出完整训练快照。原始输入留在私有数据库。登记成功退出0并不等于MATCHED，需检查返回状态；批准与晋升始终关闭。
- 11项真实PG专项通过（6.49秒，4条既有警告），覆盖四路并发/单审计、模型外键、owner/runtime六类不可变操作、审计失败回滚、MISMATCH保留、重算哈希伪造成功/输入/绑定拒绝、模式/原生不可用、CLI登记读取及跨模式错误。
- 全新专用库完整Python820项通过（200.08秒，4条既有警告，无跳过，真实PG必需）。实际备份恢复43张表、41个不可变保护与6个序列，新增2份训练证据完整重训；随后运行角色另行核对MATCHED和MISMATCH各1份、原始输入均存在。原回测24份、审核/质量/预测及采集投影重建保持通过；源演练库清理，恢复库及私有备份保留。
- migrator镜像重建并完成001–024新建/升级、已有事实保留、重复执行、角色隔离和清理，正常yta库未迁移。Ruff、207文件格式、Alpha契约漂移及文档检查通过。证据见infra/verification/training-evidence-observation.json，CLI用法见README。
- 无HTTP/TS/UI变化，未重复前端或重建应用镜像。现有回测/质量/审核尚未显式绑定这些训练证据，不因MATCHED自动取得来源资格或批准。真实授权、point-in-time、正式晋升与其余完整DoD仍待完成；77项IN_PROGRESS/43项TODO不变，goal保持active。


## 后续增量：训练证据严格摘要与私有读取接口

- 新增GET /internal/v1/training-evidence/{id}，仅Alpha服务Token，拒绝匿名/工具Token；实验功能默认关闭，模式来自服务端。每次读取先从登记模型和完整输入重新训练，存储/原生不可用明确失败，跨模式或缺失记录404。
- 严格Python摘要仅包含证据身份、模型与登记/文件哈希、输入哈希及重训报告，不返回原始行情、标签、特征或模型文本。匹配/差异字段必须有序、无重复、互斥并覆盖全部制品字段，绑定状态、模型/源码哈希关系、模式及输入身份。promotion_allowed严格布尔且只能false；报告内容哈希校验保持。
- 公开摘要不能仅凭input_hash独立重建含私有输入的登记哈希，完整登记验证仍由Alpha重放负责。导出MATCHED（19字段一致）与MISMATCH（14一致、5差异）两份实际登记摘要fixture，运行角色完整重训后生成。
- 新增4项真实PG专项通过（4.97秒，4条既有警告），覆盖两种结果的严格摘要、固定读取重试/单审计、身份校验、默认关闭/存储/模式/原生不可用，以及重算哈希后伪造字段划分、晋升标志、源哈希/输入/模型绑定的拒绝。完整Python824项通过（228.50秒，4条既有警告，无跳过，真实PG必需）。
- 共享契约39项回归、双端契约漂移、Ruff、209文件格式和文档检查通过。共享TS通过不代表已实现训练证据Zod契约。证据见infra/verification/training-evidence-api-observation.json。无持久化事务变化，未重复前轮恢复、浏览器套件或重建应用镜像。
- 公开BFF/页面、审核固定引用、正式来源资格及实际晋升仍待完成。77项IN_PROGRESS/43项TODO不变，goal保持active。


## 后续增量：训练证据共享契约与BFF读取

- 新增严格共享Zod摘要与GET /api/yta/v1/training-evidence/{id}。登录及实验开关前置，拒绝额外查询参数和非法身份；模式/身份/模型/输入引用固定。匹配/差异字段有序无重复、互斥覆盖19字段，状态和模型/源码哈希关系必须一致，晋升标志严格false。响应内容哈希由BFF校验，原始训练输入不返回浏览器。
- 扩展统一报告哈希的键排序为Python Unicode码点顺序，避免非BMP源码元数据键的UTF-16排序造成误拒绝；数组顺序与文本原拼写保持。新增Python生成的跨语言向量，覆盖BMP/非BMP、控制字符、嵌套结构和特殊自有键；既有报告哈希回归保持通过。
- 新增共享契约、BFF读取/哈希/路由脱敏及登录HTTP测试，覆盖两种结果、字段划分、模式/身份、附加字段和伪造报告拒绝。完整TS169项通过（contracts41/BFF128，真实PG运行角色）；typecheck/build及双端契约漂移通过。
- 实际构建BFF→Alpha→yta_test，普通合成用户登录后读取MATCHED/MISMATCH摘要，与Alpha直接完整重训及固定fixture完全相等。停止临时Alpha后同一读取返回503 ALPHA_UNAVAILABLE，不回退样例；用户注销，临时服务清理。观察见.local/training-evidence-bff-live.json。
- Ruff与文档检查通过，证据见infra/verification/training-evidence-bff-observation.json。本轮无Python/数据库行为变化，未重复前轮824项Python、恢复、浏览器套件或重建应用镜像。
- 训练证据目录/页面、审核固定引用、正式数据资格及晋升仍待完成；77项IN_PROGRESS/43项TODO不变，goal保持active。


## 后续增量：按模型分页发现训练证据

- 新增Alpha私有GET /internal/v1/experimental-models/{model_id}/training-evidence，服务Token鉴权、实验开关及服务端模式隔离。按created_at/id倒序分页，limit为1–50，游标必须属于相同模型和模式；只读事务及逐行读取，不增加审计或重新训练。
- 目录严格校验存储信封、规范训练输入、报告字段划分/哈希、登记身份、文件哈希及原模型内容/源码绑定。仅返回身份、模型、模式、输入/内容哈希、保存状态和登记时间；verification明确stored-integrity-checked-retraining-on-detail，保存的MATCHED不能代表本次重训通过。详情仍执行完整原生重训，资格保持UNVERIFIED。
- 18项真实PG训练证据专项通过（12.20秒，4条既有警告），新增分页/只读无审计/原生库不可用仍可发现、重算哈希篡改拒绝、鉴权/默认关闭/模式/存储检查。运行角色实际读取MATCHED/MISMATCH各1份，并使用另一个已登记模型验证跨模型游标INVALID_CURSOR；观察保存在.local/training-evidence-catalog-runtime.json及.local/training-evidence-catalog-scope.json。
- 本轮仅增加私有查询，无迁移或持久化事务变化。公开BFF目录、页面、审核引用、正式来源资格及晋升仍待完成；77项IN_PROGRESS/43项TODO不变，goal保持active。

- 最终验证：完整Python827项通过（240.96秒，4条既有警告，无跳过，真实PG必需），共享契约41项通过；双端契约漂移、Ruff、210文件格式及文档检查通过。证据见infra/verification/training-evidence-catalog-observation.json。无持久化事务变化，未重复恢复、完整BFF/浏览器或重建应用镜像。


## 后续增量：训练证据公开分页目录

- 新增共享严格目录/查询契约及GET /api/yta/v1/experimental-models/{id}/training-evidence。登录与实验开关前置，服务端固定模式，limit接受1–50规范十进制字符串；多值、额外字段、非法模型/游标在调用Alpha前拒绝。
- BFF校验模型/模式、摘要身份哈希、页面上限、重复记录、尾游标及输入游标循环，拒绝上游附加私有字段且不泄露响应。目录保留stored-integrity-checked-retraining-on-detail及UNVERIFIED，不把保存的MATCHED声明为本次重训。
- 完整TS172项通过（contracts42/BFF130，真实PG运行角色），新增目录契约/错误标记/查询、BFF分页/范围/游标、登录HTTP和默认关闭验证。首次typecheck发现契约dist尚未重建，导出重建后typecheck/build全部通过；双端契约漂移、Ruff与文档检查通过。
- 实际构建BFF→Alpha→yta_test，普通合成用户登录后逐页读取2页/2份记录，每页与直接Alpha完全相等且无重复。两份固定详情再次原生重训并与fixture相等；停止Alpha后目录和详情均返回503，不回退样例。注销与临时服务清理完成，观察见.local/training-evidence-list-live.json。
- 证据见infra/verification/training-evidence-list-observation.json。无Python/持久化行为变化，未重复上一轮827项Python、备份恢复或浏览器套件，未重建应用镜像。目录/详情页面、审核引用、正式来源资格与实际晋升仍待完成；77项IN_PROGRESS/43项TODO不变，goal保持active。


## 后续增量：训练证据页面与MVP优先级收敛

- 模型目录新增训练证据入口，模型范围分页与固定详情页面均接登录及实验开关。目录明确展示保存时结果；详情重新训练后显示19字段的一致/差异、模型/输入/文件及源码哈希。模式/身份不符或服务不可用隐藏结果，支持同身份显式重试。
- 完整65项浏览器通过（45.2秒），新增5项覆盖默认关闭无请求、分页/导航、两种结果/刷新/手机、失败/身份错误重试、错模式与空目录；typecheck/build通过。390px手机无整页横向溢出，差异详情截图已检查。
- 实际浏览器Web→BFF→Alpha→yta_test，无响应替换，目录2份记录与fixture相等；MATCHED/MISMATCH详情与直接Alpha重训和固定fixture相等，刷新通过。页面错误0、仅同源请求，注销及临时服务清理完成，正常构建恢复默认BFF代理。证据见infra/verification/training-evidence-ui-observation.json。
- 无Python/数据库变化，未重复前轮827项Python及恢复，未重建应用镜像。审核引用和来源资格仍未完成。
- 用户要求加速MVP闭环后，停止继续扩展Beta模型功能，优先真实Hermes应用驱动、行情资格及真实研究/冻结/结算链路，最后收敛部署验收。当前缺口与执行顺序见[mvp-closure.md](mvp-closure.md)。完整项目目标保留，任务状态未因部分证据提升为完成。


## 后续增量：MVP私有Hermes HTTP与SSE客户端

- 开始TASK-027，新增HermesClient，固定/p/alpha-orchestrator路径与服务端连接，Bearer凭证不进入返回错误。提交只携带原请求键、独立会话键、当前prompt及选定模型，不重发history；客户端不自动重试。提交失响应/非法响应/拒绝/重定向一律保守返回EXECUTION_UNKNOWN，不据此换键。
- 状态读取核对run身份，保留execution_unknown/interrupted；停止仅返回回执，不宣称已经取消。HTTP响应与SSE消费受截止时间及解压后字节上限限制，UTF-8流式解码，禁止重定向；SSE核对run、id/seq连续性，支持Last-Event-ID，显式拒绝replay.truncated、序列缺口、重复和不完整帧。
- 7项本地HTTP专项通过，完整TS179项通过（contracts42/BFF137，真实PG），typecheck、BFF构建、Ruff、Alpha契约及文档检查通过。
- scripts/check_hermes_client.mjs在真实锁版本Hermes及同一internal网络的Node客户端容器验证：固定Profile提交、同键原run、无模型凭证导致failed、run.failed事件、游标续读无重复。无模型凭证、无外网或端口发布，临时容器/网络清理，保留原PG和无关服务。首次尝试从宿主访问internal网络发布端口失败，尚未提交请求；改为同网客户端后通过。
- 证据见infra/verification/hermes-client-observation.json。当前未接入应用ResearchRunner、未实现持久化会话/run映射或实际取消对账；真实模型验证仍将使用pi配置。六项Hermes门槛保持UNVERIFIED，不启用LIVE或更改现有研究行为。全项目78项IN_PROGRESS/42项TODO，MVP P0为67项IN_PROGRESS/12项TODO，完整DoD仍未全部通过。


## 后续增量：Hermes持久化映射与单次发送登记

- 新增迁移025，私有session binding、run request和receipt三表均为不可变事实，关联真实会话/运行/所有者。运行账号只有SELECT/INSERT。会话绑定运行域哈希（固定origin、Profile及凭证），不保存明文Token；原请求正文、模型与哈希只在私有存储中。
- HermesStore在事务内锁会话、校验真实run和模式、登记固定请求键并追加审计。并发仅一个调用获得send=true；进程重启、发送前崩溃或回执丢失后的既有记录均不能重新获准发送。不同正文/模型拒绝；同一会话的运行域或凭证变化要求核对原运行。
- admitHermesRun先提交发送意图再调用HTTP，随后持久化回执。已知回执直接返回原run；无回执明确EXECUTION_UNKNOWN，不自动重发POST。上游同一run不能映射到两个应用run，回执冲突回滚。
- 新增6项真实PG测试，覆盖多连接八路并发/单审计、恢复、模式/归属/正文/凭证冲突、同run唯一映射及运行账号不可变。实际HTTP提交成功后断开响应，再次进入流程不发第二次。完整TS185项通过（contracts42/BFF143），迁移/备份Python专项12项通过，typecheck/BFF构建、Ruff、212文件格式和契约检查通过。
- 新建专用数据库执行上述真实PG套件，额外验证9类owner不可变操作及审计失败时请求/会话事务整体回滚。实际备份恢复46表、44个不可变保护、6序列；恢复后7请求、4回执、3未知回执保持一致，无新发送许可或审计。此演练没有金融预测样本，不代替此前完整金融重放演练。脚本为scripts/check_hermes_store_restore.py，源库清理，恢复库/私有备份保留。
- migrator重建并完成001–025新建/升级、已有证据、重复执行、角色与清理检查。正常yta库未迁移。证据见infra/verification/hermes-store-observation.json。早期演练先被FK TRUNCATE顺序、重复日志路径及备份库命名白名单拒绝，调整演练脚本后通过，未放宽数据库或备份保护。
- ResearchRunner尚未接Hermes，事件持久化/转换、取消终态对账和pi真实模型应用链路待完成。全项目78项IN_PROGRESS/42项TODO，MVP67项IN_PROGRESS/12项TODO保持，未宣称完整DoD通过；当前goal仅推进MVP。


## 后续增量：Hermes驱动事件转换与取消终态对账

- 新增HermesResearchDriver，使用持久化admission后订阅固定run事件。转换消息delta/interim，校验两种白名单金融工具与唯一call ID的开始/结束关系；原始preview、reasoning及未知工具/审批事件不能成为授权。最终状态须与流终态一致，完成结果交给必须显式提供的可信报告核验回调。
- 驱动停止使用独立5秒对账预算：POST stop只是回执，随后查询到cancelled才抛出ConfirmedRunCancellation。completed/failed/unknown/interrupted或无法核对均不冒充取消；已登记请求拒绝盲目重发或从头重播事件。
- ResearchRunner为远端驱动增加确认取消策略，避免原本的controller取消原因覆盖EXECUTION_UNKNOWN。ResearchRepository在远端完成与本地取消请求发生事务竞态时追加run.execution_unknown，而不是虚构run.canceled；原REPLAY驱动默认语义保持。
- 新增4项真实PG/HTTP协议测试，覆盖事件转换与私有字段不转发、同请求不再发、停止后的cancelled/completed/unknown、runner确认/未确认取消，以及完成/取消事务竞态。当前Hermes存储/驱动专项10项通过，完整TS189项通过（contracts42/BFF147，真实PG）；typecheck、BFF构建、Ruff、Alpha契约与文档检查通过。
- 证据见infra/verification/hermes-driver-observation.json。驱动仍通过现有依赖注入显式提供，默认应用模式未切换；尚缺持久化事件游标、恢复对账与完整可信报告/工具结果绑定，未运行本轮pi真实模型应用链路。无新迁移或Python行为变化，未重复恢复、Python金融套件及浏览器。MVP仍未完成，任务计数不变。


## 后续增量：Hermes事件游标、原子投影与工作进程恢复

- 迁移026新增不可变hermes_run_events。保存规范化上游序号、内容哈希及对应公开事件引用；消息/工具关联字段白名单化，不保存工具preview、任意工具结果或reasoning文本。原序号相同内容去重，改变内容、序号缺口、错误run、重复工具身份/不配对结果拒绝。
- 将ResearchRepository追加逻辑提取为同事务入口；Hermes事件与公开页面事件同事务提交，事件账本写入失败时公开投影一起回滚。最终完成回调改为返回经服务端验证的事件数组，由finish事务一次追加详情/报告及终态；其中任一事件无效整体回滚，重入不重复完成。
- ResearchRunner对已有run.started且驱动支持resume的运行调用恢复入口；Hermes恢复只读取已保存回执与游标，重建工具配对状态，并以Last-Event-ID续读原run。没有回执或上游历史缺口保持EXECUTION_UNKNOWN，不重发prompt。已经保存上游终态时可直接核对状态并完成，不要求从头订阅。
- 新增3项真实PG/HTTP测试覆盖五路事件去重、变更重播/缺口拒绝、私有字段丢弃、完成事务回滚/去重，以及工具执行中工作进程恢复后零POST、文字无重复、只一个run.started。专项13项通过；完整TS192项通过（contracts42/BFF150，真实PG），浏览器65项通过（47.1秒）。BFF构建、Ruff、Alpha契约和文档检查通过。
- 全新专用库实际备份恢复47表、45不可变保护及6序列。恢复后15请求、12回执、3未知回执及12条上游事件完整核对，游标/公开投影一致，无新发送许可或审计。额外验证12类owner不可变操作、请求审计失败回滚、事件账本失败导致公开投影回滚。该数据集不含金融预测，不替代历史金融重放演练；源库清理，恢复库/私有备份保留。
- migrator重建及001–026新建/升级/已有证据/重复执行/角色/清理通过。正常yta库未迁移。证据见infra/verification/hermes-events-observation.json。可信金融工具结果与最终研究报告绑定、pi真实模型应用链路及正式门槛仍待完成，默认应用驱动未切换；goal保持active。


## 后续增量：研究固定快照与可信工具回执

- 新增迁移027：已认证研究run的固定行情快照、Alpha生成的工具结果与内容哈希均不可变，运行账号仅SELECT/INSERT。创建上下文需要服务Token和BFF提供的owner；工具Token只能读取既有上下文并登记实际计算结果，不能指定owner或创建草稿/冻结。
- 相同上下文重入沿用原快照，不重新读取最新行情。工具请求身份锁与上下文锁串行化并发登记；相同身份异参冲突，100份回执预算满后仍允许已成功请求精确重试。审计随回执事务提交；读取重新计算两类工具结果并校验保存哈希。
- MCP新增显式scoped_receipts配置（默认关闭），只接受context_id，服务端生成回执请求身份；调用Alpha固定POST路径，校验返回身份、模式、快照哈希、信息截点及严格结果契约。回执身份与Hermes事件call ID不同，不能混同。原无参数工具模式保留用于既有独立探针。
- 第一轮52项专项通过（5.99秒，4条既有警告），含真实PG并发/单审计、所有者/模式隔离、缺少上下文、同身份异参、两张表owner/runtime不可变、HTTP凭证分离、真实MCP→Alpha→PG及预算重试。补充7类篡改响应拒绝及API/迁移回归后，最终70项通过（6.26秒，4条既有警告，无跳过）；日志为.local/research-tools-tests.log。迁移027仅应用到专用yta_test，正常yta未迁移。
- Ruff、Alpha契约漂移及文档检查通过。尚未执行027备份恢复或重建发布镜像；未运行本轮pi真实模型。BFF报告绑定与默认应用接入仍待完成，本段不代表MVP验收通过。


## 后续增量：BFF可信报告协调与固定快照草稿

- Alpha新增已认证上下文draft接口，使用run固定快照和既有幂等草稿事务；REPLAY使用固定截点，其他模式使用服务端时钟。重入校验保存草稿的模式、截点和快照身份，拒绝同请求绑定不同快照；不执行冻结。
- BFF新增严格上下文读取，逐条校验回执UUID、run、模式、截点、来源、快照、可获知时间及重复身份。HermesResearch在发送前提供固定上下文、服务端草稿、两工具参数规则和输出契约；用户原文以数据形式嵌入。完成时重新读取可信回执和草稿，验证模型JSON后生成受限Canvas、草稿及报告事件，交给既有原子完成事务。
- HermesResearchDriver支持发送前协调回调，准备后的完整prompt进入原持久化发送登记；已有发送意图/回执不会重新准备或重发。尚未改为默认应用驱动；仅完成可显式注入的协调实现，未把合成协议测试声明为真实模型验证。
- 19项Python专项通过（2.72秒，真实PG，无跳过，4条既有警告），23项报告TS专项通过。新增固定快照草稿幂等/所有者/模式检查及无冻结事实、可信回执篡改/缺失/重复、模型权限注入和错误草稿拒绝。BFF typecheck、Ruff及Alpha契约漂移检查通过；完整TS和构建验证另行记录。
- pi真实模型应用端到端、真实行情资格、027恢复与发布部署仍未完成，goal保持active。

- 最终回归：完整TS197项通过（contracts42/BFF155，真实PG运行账号），全工作区typecheck与BFF构建通过。持久化驱动测试额外核对完整准备prompt已保存、重复进入不会再次准备或提交。日志见.local/hermes-research-full-ts.log、.local/hermes-research-typecheck.log及.local/hermes-research-build.log。没有UI行为变化，未重复浏览器套件；未重建部署镜像。


## 后续增量：pi真实模型贯通已认证BFF与固定工具证据

- 新增隔离应用探针与Compose overlay，从pi当前设置只读取选中路由（volc-glm/deepseek-v4-pro），不修改pi配置、不输出密钥。锁版本Hermes通过真实模型调用两种scoped金融工具；Alpha/工具使用本轮重建镜像，BFF使用当前构建代码挂载到Node容器。此方式是集成验证，不是封装完成的发布镜像。
- 全新独立yta_pi_app_8be890c2af0d_test迁移001–027；启动BFF队列前强制检查不存在研究run。普通合成账号实际登录、CSRF及消息接口提交研究，完成run为2eb41cb6-8355-429d-900e-b0d7bf7e02c1。没有替换模型或HTTP响应；持久化两种工具各1份真实回执、两次Hermes工具完成、585条流式delta及原子Canvas/草稿/可信报告终态。
- 验证报告DRAFT_READY与Alpha草稿一致，完成前预测表无自动冻结；重复同一消息返回原run。随后用已登录测试用户的显式确认接口冻结REPLAY预测5af28410-9ad5-42ca-aa49-6b5bddf4a253，并注销。应用探针退出0，全部临时容器/网络清理；独立数据库、原运行身份及私密卷保留供恢复核验。
- 首次启动暴露两项验收脚本问题：旧工具镜像缺OpenTelemetry exporter；共享yta_test遗留待处理研究被队列扫描，产生7次真实模型提交。停止应用后逐个核对原Hermes运行，5次completed、2次failed，无仍运行请求；这些不计验收，不换键重发。重建Alpha/工具并使用独立空库后完成上述有效验收。第一次私密卷与原状态保留，不覆盖证据。
- 证据见infra/verification/hermes-application-observation.json及.local/hermes-application.log，含源码哈希、运行镜像身份、实际run/预测身份及限制。Ruff和文档检查通过；本轮主要新增运行脚本，没有重复上一轮197项TS与Python专项。
- 这是实际LLM配合合成REPLAY行情的应用验证。真实行情资格、浏览器真实模型呈现、027备份恢复、封装发布、远端CI及staging验收仍未完成；Hermes六门槛仍UNVERIFIED，不启用默认LIVE，MVP goal保持active。


## 后续增量：真实模型历史文本修复与027恢复

- 从真实模型应用记录发现刷新聊天历史会显示结构化JSON：终态落库只拼接message.delta，没有采用经核验的message.completed文本；没有delta时也会遗漏最终回复。两个真实PG回归先失败，准确复现这两种情况。
- ResearchRepository完成时优先保存最终完成文本；失败/取消仍保留partial流片段。历史读取为已完成的assistant消息从完成事件投影最终文本，使旧不可变记录也能正确显示，不UPDATE旧事实或删减原始上游事件。补充旧原始JSON消息的只读投影测试。
- 首轮完整TS198项通过（contracts42/BFF156，真实PG），typecheck及BFF构建通过；旧记录投影增加后另行运行相关PG/HTTP回归，结果另记。原始失败日志.local/hermes-history-red.log保留。
- 新增scripts/check_research_context_restore.py：从已停止的真实模型应用独立数据库创建临时副本，不中止源连接、不改原库；备份并恢复到全新库后，逐表内容哈希、权限、不可变保护和序列验证通过。49表、47不可变保护、6序列，重新计算1份冻结预测；恢复后以运行角色读取固定上下文，重新计算两种工具结果并校验2份回执，审计数不变。没有结算样本，不声称完成LIVE结算验证。
- 结果见.local/research-context-restore.json，恢复库yta_restore_a9c081ecc27c4fcd86713e77770a26a6_test。临时源副本清理，原pi应用库、恢复库与私密备份保留。真实行情、浏览器模型验收和部署仍待完成，goal保持active。

- 最终旧历史投影验证：56项相关PG/HTTP回归通过，全工作区typecheck、BFF构建及Ruff通过。在真实pi数据的恢复库实际读取历史，assistant文本与可信report.summary相等；Hermes游标589及run.completed完整保留，原请求claim返回send=false，没有新增发送。该核验没有再次调用模型。


## 后续增量：真实模型历史浏览器验收与终态流重放修复

- 使用已恢复的真实pi模型研究数据，经真实Web→BFF→Alpha→PG登录读取，未替换HTTP响应、未重新推理。检查摘要与已保存报告相等、两项来源及风险、刷新恢复、390px手机无整页横向溢出、预测详情与注销；页面错误0，同源请求，Hermes请求数不增加。
- 截图人工复核发现原测试漏掉的竞态：打开已完成会话后，页面仍从头订阅SSE，重放原始JSON流覆盖最终摘要并短暂恢复RUNNING。增加“已完成历史零SSE请求”断言先失败（实际1，预期0）；修复终态会话不再创建EventSource，未完成运行保留原订阅流程。真实记录复验通过、桌面截图已检查，最终摘要稳定，停止按钮不再错误出现。
- 新增scripts/check_hermes_history_browser.mjs，证据infra/verification/hermes-history-browser-observation.json及.local/hermes-history-browser.json，截图在.local/hermes-history-browser。独立恢复库新增仅合成账号登录凭据，不改原始研究事实；临时服务已清理。首轮选择器歧义已修正；此前宽松断言通过不能替代加强后验收。
- 浏览器常规研究用例新增终态不重订阅、摘要保持和无停止按钮断言；完整浏览器回归另记。本轮验证既有真实模型记录的展示，不声称已通过浏览器新提交真实模型或LIVE行情验收。

- 最终回归：完整65项浏览器通过（46.5秒），全工作区typecheck/build通过，正常Web构建恢复默认BFF代理；Ruff与文档检查通过。日志.local/hermes-history-e2e.log、.local/hermes-history-browser-typecheck.log及.local/hermes-history-browser-full-build.log。本轮没有Python或持久化行为变化，未重复金融全套/备份恢复。


## 后续增量：当前源码发布候选打包（未获发布验收）

- 重建BFF、Web和001–027 migrator，复用上一轮当前Alpha/finance-tools镜像，新增scripts/check_mvp_candidate.py以固定镜像ID在无网络/只读容器中检查打包模块。BFF包含HermesResearch/driver与应用入口，Alpha包含固定上下文回执实现，工具包包含scoped_receipts及实际依赖；migrator内27份SQL哈希逐一匹配源码。
- 记录267个构建输入文件哈希和5个固定镜像身份到infra/verification/mvp-candidate-observation.json，状态明确LOCAL_CANDIDATE_NOT_RELEASE_QUALIFIED。不覆盖早期app-local-observation历史验证，不将旧镜像的运行测试套用到新镜像。
- 新migrator实际完成全新库、升级、已有证据保留、重复执行、缺少凭证/运行角色拒绝及临时库清理，日志.local/mvp-migration-check.log。正常yta库未迁移。BFF/Web/migrator构建日志为.local/mvp-*-build.log。
- 全量Python正在全新专用库执行，随后进行实际备份恢复；最终结果另记。尚缺当前封装候选的整套运行验收、真实行情资格、远端CI/Review及staging/回滚。Hermes六门槛保持UNVERIFIED，默认真实驱动未启用，goal保持active。

- 最终全量Python843项通过（224.59秒，4条既有警告，无跳过，真实PG必需）。全新数据集实际备份恢复49表、47不可变保护、6序列；重现46份预测、20份结算、10份行情证据，以及既有模型/质量/回测/训练记录。采集原始事实及行情表一致，重建40份有效采集、39份有效投影与23份日线记录，4份无效采集及1份缺失/无效投影仍拒绝。源测试库清理，恢复库/私密备份保留。
- 完整输出.local/mvp-full-python-restore.json及.local/clean-suite-d03b6c72bab04121b94e02049808adcf.log；已纳入候选观察文件。Ruff、Alpha契约漂移和文档检查通过。以上是本地回归与恢复证据，不提升真实来源资格，也不是远端CI或staging验收。


## 后续增量：封装候选镜像浏览器整链路（REPLAY）

- 新增scripts/check_mvp_container.py与probe_mvp_container_browser.mjs，使用候选观察文件内固定Alpha/BFF/Web/migrator摘要。全新独立yta_candidate_61a6b5848f316d371fb60614_test由实际migrator容器执行27份迁移；Compose禁止构建/拉取，应用运行不挂载工作区源码。
- 实际检查3个应用容器健康、根文件系统只读、无bind挂载，Alpha/BFF不发布宿主端口；Web仅本机3300可达。浏览器普通合成账号登录/CSRF后提交NVDA研究，校验DRAFT_READY和0.50% baseline，确认前预测数0；显式确认后1份预测、模拟到期后1份outcome，实际收益2.00%与误差1.50个百分点符合fixture。
- 结算刷新、历史摘要/两份来源、已完成历史零SSE请求、390px手机无整页横向溢出、注销均通过；页面错误0，仅同源请求。结算桌面截图已检查。证据infra/verification/mvp-container-observation.json，截图.local/mvp-container-browser；本次为默认固定REPLAY驱动，不包含真实LLM或LIVE行情。
- 初始脚本在研究完成后因Playwright API使用相对URL失败；核对为脚本问题后改绝对URL，继续原数据库和原run b9c38b3c-2c2a-4bf2-81f8-2c06966505a1，没有再提交研究。原错误日志保留。临时应用容器/网络清理，专用数据库保留；原开发PG与无关服务不受影响。
- Ruff与文档检查通过。本轮仅新增验收脚本，无应用逻辑改动，未重复843项Python及65项浏览器回归。当前候选的合成REPLAY运行验证已补齐；真实模型新研究浏览器交互、Hermes全部门槛、真实行情资格、真实代码版本/远端CI/Review及staging仍待完成。


## 后续增量：浏览器新提交pi真实模型研究与运行方式标记

- /me运行方式从实际注入的ResearchDriver读取：Hermes驱动返回hermes，数据模式仍独立为REPLAY。更新BFF OpenAPI枚举，页面区分“合成行情·模型研究·不计入真实前瞻成绩”和固定回放流程，不将真实模型等同真实行情。默认驱动与LIVE门槛未改变。
- 新增浏览器BFF测试入口和Compose overlay，start_hermes_application_probe.py --browser使用独立状态文件、私密卷和全新yta_pi_app_5fe1da877d37_test；沿用pi选中volc-glm/deepseek-v4-pro，提交前检查队列为空。浏览器通过真实Web/BFF登录提交唯一run f35c4cda-4aa8-4602-944f-773bd5610391，两种真实金融工具各完成一次，产生689条流式delta、可信报告及Canvas/草稿。
- 页面等待完成后核对报告/草稿和2份固定工具回执；确认前预测0，页面显式确认后1份预测，模拟到期后1份outcome。结算显示2.00%与1.50个百分点误差，刷新、历史零SSE重播、手机无整页横向溢出、注销通过。页面错误0，全部浏览器请求同源，未换键重发。真实运行方式横幅及结算截图已检查。
- 证据infra/verification/hermes-live-browser-observation.json、.local/hermes-live-browser.json，截图.local/hermes-live-browser。测试结束核对唯一run.completed后清理容器/网络；独立数据库与私密卷保留。此探针BFF挂载当前构建并显式注入驱动，不是默认LIVE部署；上一轮封装候选仍对应旧源码，不能冒充包含本轮运行方式字段。
- 新增真实PG/HTTP测试验证运行方式与数据模式分离，完整TS199项通过（contracts42/BFF157），全工作区typecheck及BFF构建通过。初次typecheck发现驱动联合类型未显式标注，修正后通过，无运行请求重发。BFF契约及完整构建最终结果另记。
- 新研究期间仍传输模型结构化JSON流，最终显示核验后的摘要；本轮未验证增量JSON对用户的呈现体验，不能将最终完成截图当作该项通过。真实行情资格、Hermes完整门槛、真实代码版本/远端CI与staging仍待完成，goal保持active。

- 最终验证：BFF契约漂移、全工作区build、Ruff与文档检查通过，Web恢复默认代理构建。本轮没有Python/持久化变更，未重复843项Python或数据库恢复。真实模型浏览器验收通过不改变供应商资格或发布门槛。


## 后续增量：结构化模型流仅渐进展示摘要

- 新增浏览器显示层reportStreamSummary，只从不完整JSON对象顶层summary逐步解码文字；跳过完整嵌套字段，处理转义、中文与Unicode分段，截断代理对不显示半个字符，非法/非对象/超长输入不展示原始协议。此函数不验证研究报告，也不读取模型权限字段。
- Hermes运行方式的message.delta使用该显示投影；固定回放文本保持原样，message.completed仍使用BFF核验后的最终文字和报告。原始事件账本不改写，partial摘要不能创建草稿或确认冻结。
- 3项专项覆盖逐字符前缀、转义/Unicode、嵌套summary及非法输入；完整TS202项通过（contracts42/BFF160）。新增浏览器用例在受控结构化SSE下检查摘要渐进显示、协议字段不可见、最终草稿事件之前无确认入口，以及最终核验文本替换；完整66项浏览器通过（47.6秒）。该浏览器用例使用控制流，不冒充新增真实模型调用。
- scripts/check_saved_report_stream.mts实际读取上一轮pi真实模型run f35c4cda-4aa8-4602-944f-773bd5610391的689个已保存流片段，产生212次渐进摘要更新；各前缀保持单调，最终文字与可信report.summary完全相等。没有再次推理或修改数据库；证据infra/verification/report-stream-observation.json。
- 本轮继续保留真实行情、Hermes完整门槛、远端CI/Review和staging未完成状态。候选镜像清单仍属于此前构建，不能当作已包含本轮显示修复。

- 最终全工作区typecheck/build、Ruff与文档检查通过，正常Web构建恢复默认代理。本轮仅显示层及测试脚本变化，没有重复Python金融套件或恢复演练。

## 2026-10-30 Hermes 未授权工具调用验收增量

固定 Hermes 镜像在无外网、无真实凭证的合成模型环境中完成 8 次 Gateway Run、2 Profile 并发验证：terminal/read_file/write_file/delegate_task 共 32 次调用收到明确拒绝结果，文件副作用标记不存在。见 `infra/verification/hermes-forbidden-tools-observation.json`。初始 3 次运行因探针对拒绝文本尾部空格的严格比较失败，已核对终态并保留记录；修正后独立合成批次通过。该证据仅覆盖空工具目录下全非法批次，不证明金融工具混合批次、OS 文件沙箱或全部 Hermes Gate；任务仍 IN_PROGRESS，真实行情/LIVE 与发布部署仍待验收。
