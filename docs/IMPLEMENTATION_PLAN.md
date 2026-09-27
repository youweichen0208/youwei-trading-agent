# v0.3 修复与实施计划

日期：2026-09-27\
设计依据：[ARCHITECTURE.md](ARCHITECTURE.md)\
问题来源：[v0.2 评审](ARCHITECTURE_REVIEW_v0.2.md)\
状态：S00 协议已确认定稿（2026-09-27）；S01–S11 工程任务均待实施、待验收。

## 1. 执行规则

每项任务记录负责人、实现引用、验证命令/报告与遗留问题，满足验收条件后再标为完成。文档编写完成不算工程完成。

依赖按以下顺序推进：

```text
S00 目标与评估协议
  → S01 技术验证与环境决定
  → S02 持久任务、权限与预算
  → S03 沙箱执行与产物链路
  → S04 PIT 数据与冻结快照
  → S05 Ledger、Outcome 与评分
  → S06 baseline/quant 前向批次
  → S07 Hermes 研究与三组预测
  → S08 Pi 探索任务
  → S09 国内入口与完整 MVP 验收
  → S10 评估界面、Memory 与审批
  → S11 前向候选验证与人工发布
```

S03 与 S04 在 S02 的身份、任务和对象契约确定后可并行；S09 的界面可提前开发，正式联调依赖持久事件与研究结果契约。S08 与 S09 可并行。无论如何拆工，后续功能不能绕过前置的权限、预算、截止时间或提交检查。

## 2. 任务清单

### S00 — 固定目标、时间和评估协议（Phase 0）

- [x] 记录使用范围、登记日 S&P 500 PIT 总体、GICS Sector 分层固定种子抽样20证券、SPY、D20 主目标与每周频率。
- [x] 固定 TargetSpec 版本：算术超额总收益、D1/D20/D60、正常交易时段、日历、分红、并购、停牌/退市及缺失处理（Q5 固定目标窗口已确认）。
- [x] 定义周六06:00 ET cutoff、封存检查/提交确认、入场前15分钟 deadline、entry/exit 及服务器时钟要求。
- [x] 固定 campaign/batch/case/source/release 的层级关系、enabled_sources、缺失与回退政策、主指标和评估时点；来源未启用不计为执行失败。
- [x] 定义最小 research release、人类所有者批准、trial 追加约定与 Git/外部锚定的保护范围；具体批准记录待实际 release 生成。

登记文件：[S00 协议索引](protocols/README.md)、[目标协议](protocols/target-spec.v1.md)、[时间协议](protocols/time-protocol.v1.md)、[批次政策](protocols/campaign-policy.v1.md)、[已知答案](protocols/known-answer-cases.v1.md)、[机器可读登记](protocols/s00-registration.v1.json)、[Trial Registry](trials/registry.md)。Git 仓库已初始化。

剩余决策：无。Q5 固定目标窗口、Q7 先描述统计后连续周批次区块自举、Q9 Campaign/Batch 两层结构均已确认（2026-09-27）。实际名单/数据/日历/模型引用在 S01/S04/S06 取得，不虚构 hash 或提前批准。

交付：版本化协议、字段契约和已知答案用例；具体默认值须记入配置，不由 Agent 在运行中决定。

验收：跨开盘完成、节假日/提前收盘、拆分分红、停牌退市、LLM 未启用/超时等案例均有明确处理；模型版本与证券集合尚未确定时保持待登记，不生成正式 campaign。

### S01 — 核实运行时与部署可行性（Phase 0；依赖 S00）

- [x] 固定 Hermes、Pi、Python/Node、镜像与依赖版本；依据运行时核实验证实际接口（Hermes `7fa45eb349a1` + uv sync --frozen 可复现；Pi 0.87.1 + node:22 + RPC 冒烟通过；见 [实测记录](research/s01-target-verification.md)）。
- [ ] Hermes 独立任务实例、工具白名单、关闭研究链路内置记忆/后台学习/会话检索（隔离键名已从官方文档确认，运行时验证待 S07 接入）。
- [x] Pi 工具和 RPC 管理命令白名单依据；资源自动加载、技能及扩展固定为可信部署内容（命令全集与 flags 已固定于 [runtime-version-pinning](research/runtime-version-pinning.md)；wrapper 实现属 S08）。
- [x] 在目标 Linux ECS 上执行真实 quant 依赖，验证 gVisor、Parquet、资源与网络限制（runsc 开销噪声级、mmap 正常、锁定配置生效、网络阻断；见 [实测记录](research/s01-target-verification.md)）。
- [ ] 试用数据源（Tiingo），验证原始版本、源时间、退市/公司行为、许可与模型使用范围；待账号 token（能力核实见 [tiingo-capabilities](research/tiingo-capabilities.md)）。
- [ ] 验证 LLM Gateway 的工具调用、辅助请求、取消、用量记录和并发限额（工具调用/流式/网关链路已通，火山需 Bearer 头；取消/用量/限额待 S02；见 [实测记录](research/s01-target-verification.md)）。
- [ ] 用隔离测试库验证备份工具的本地 WAL 归档与恢复（OSS 已排除出 MVP 范围）。

交付：固定版本清单、技术验证记录、供应商与授权决定、恢复样例及未解决问题。

验收：所有会改变架构的开放项均有证据或明确收缩方案。只有文档支持、没有目标环境验证的项目不能标记为通过。

### S02 — 建立持久任务、权限和预算（Phase 1A；依赖 S01）

- [ ] Core API / Worker 共用模块化代码库，分开运行凭证和资源限额。
- [ ] PG run/job/attempt/step/event/outbox 表，短事务领取、租约、心跳、递增 attempt_token。
- [ ] 在每次有副作用的提交处校验 fencing token；服务重启后从已完成步骤恢复。
- [ ] Idempotency-Key 绑定租户及 payload hash；同键不同输入拒绝。
- [ ] 身份来自鉴权；按 job 签发能力令牌，限制快照、工具、租户和有效期。
- [ ] run 级原子费用预留、结算、最大尝试数、墙钟期限、取消传播和结构化日志。
- [ ] 从第一版设置备份、任务积压、磁盘、WAL、预算与错误告警。

交付：可独立运行的确定性执行控制与任务查询接口。

验收：杀进程、重复投递、租约过期、旧 Worker 返回、预算同时申请、提交响应丢失均不产生重复业务提交。外部模型调用重试费用完整归集，不宣称外部调用恰好一次。

### S03 — 打通受限计算与产物链路（Phase 1A；依赖 S02）

- [ ] Runner 固定镜像 digest、命令模板、只读根目录、网络和资源限额。
- [ ] Data Service 内部快照流 → Runner 固定 spool → hash 校验 → 沙箱只读挂载。
- [ ] 标准 quant 使用已发布镜像入口，不启动 Pi Agent。
- [ ] Runner → Core API 受限上传 → OSS → 不可变 artifact manifest；每段校验 job/tenant 权限。
- [ ] 限制路径、文件类型/大小/数量；拒绝穿越、symlink/hardlink 和不安全反序列化；复杂解析在受限环境。
- [ ] 超时/取消终止计算，清理容器和临时文件，保留受控日志。

交付：可信 Runner 与统一批处理执行 Interface。

验收：沙箱无法读取密钥、其他作业目录或连接网络；恶意产物不会在宿主/主站执行；OOM、磁盘满、日志暴涨不拖垮 PG。尚未有生产数据时先用固定合成快照验收。

### S04 — 建立 PIT 数据与冻结证据（Phase 1A；依赖 S02，可与 S03 并行）

- [ ] 证券永久 ID、标识历史、交易日历、公司行为、退市、市场/财务源版本。
- [ ] 明确 source_available_at、ingested_time、usable_at、时间精度和质量等级。
- [ ] Data Service 统一采集、源限速、授权标签、缓存与原始响应；collector 不重复实现供应商逻辑。
- [ ] 前向查询与 historical_source 重建分别标识；正式查询必须携带 Controller 时间与权限上下文。
- [ ] Snapshot 固定查询、证券、源版本、原始对象、schema、文件 hash 与代码版本。
- [ ] 派生特征保留依赖；不能用当前 consensus/IV/重述值替代缺失历史值。
- [ ] 训练 manifest 固定预处理、成熟标签、拟合窗口、校准与模型产物。

交付：可冻结、可授权读取、可恢复的证据快照。

验收：回补不能改变旧快照；重述前后按版本正确查询；不能跨租户读取私有快照；任何模型输入均能追溯到截止时间内的数据或冻结规则。

### S05 — 实现 Ledger、结果更正与最小评分（Phase 1A；依赖 S02–S04）

- [ ] 创建 campaign/case，原子封存一个 case/release 的三种 source 结果及引用。
- [ ] 唯一约束、source 状态/空值、值域、输入 manifest 与 attempt 校验。
- [ ] 获取 case/chain 锁后重新检查实时钟与 deadline；持久提交确认与及时性判定追加留痕。
- [ ] 应用 UPDATE/DELETE/TRUNCATE 禁止；提交权限、链头锁、规范化行内容与链序号固定。
- [ ] Outcome 按 case 回填，以 revision/supersedes 追加更正，禁止分叉和覆盖。
- [ ] 最小 Evaluation 固定 case、commit、结果版本及评分代码；按预测类型计算 Brier、MSE/RMSE 等。
- [ ] 独立 Ledger 归档桶、保留策略、链校验、对象引用恢复与迟到/未确认监控。

交付：可独立运行的预测封存、到期结果任务和可复算报告。

验收：三组不能部分提交；锁等待跨 deadline 被拒绝；响应丢失保守标记 uncertain；更正产生新报告且旧报告不变；恢复后输入与指标 hash 可核对。合成数据验收不得标记为真实前向成绩。

### S06 — 开始 baseline/quant 前向运行（Phase 1A；依赖 S05）

- [ ] 发布固定 baseline 与简单量化模型，登记训练与校准版本。
- [ ] 人工批准初始 release，按已登记规则固定20证券 panel，事前登记每批60个 case（D1/D20/D60）。
- [ ] llm_adjusted 保留 unavailable/not_enabled，不填充伪造 LLM 结果。
- [ ] Scheduler 按周创建新批次，按交易日检查到期 Outcome。
- [ ] 最小只读查询显示计划数、完成数、缺失、迟到、数据质量和评分适用范围。

交付：实际预测记录、定时任务及首批回填结果。

验收：从真实运行时刻开始积累；正式结果只使用已成熟标签。首批标签未到期时可标记“预测管线运行中”，完整闭环验收需等待真实回填。

### S07 — 接入 Hermes 与三组预测（Phase 1B；依赖 S06 的预测管线验收）

首次真实批次成功封存、任务与数据检查通过后即可开展本项，不要求等待全部标签成熟；Phase 1A 的完整结果闭环仍需实际回填验收。

- [ ] 将 Controller 的冻结计划/证据转换为 Hermes 输入；每个 run 独立上下文。
- [ ] 先使用一个研究综合角色及固定反证步骤；按实际收益再扩展并行角色。
- [ ] 研究引用可定位原文，warnings、缺失、量化依据、实际模型和成本完整输出。
- [ ] Hermes 返回 Proposal；Controller 校验并封存，Agent 无权写 Ledger 或改变 Lesson。
- [ ] prompt 回归验证结构、引用、权限与成本，记录新的 release。
- [ ] 从新 campaign 启用 llm_adjusted；按预登记政策记录失败/回退，不回补历史 LLM 预测。

交付：完整研究报告与同 case 的三组前向输出。

验收：故障后可恢复；同批三组共享目标和证据版本；生成截止后到达的结果不能进入准时评估。固定输入可复查，不要求外部 LLM 重跑逐字一致。

### S08 — 接入一个 Pi 探索任务（Phase 1B；依赖 S03、S07）

- [ ] 通过受控 RPC wrapper 运行 Pi，所有模型调用归到父 run 预算。
- [ ] 工具仅访问授权快照、受控 job 文件系统与 Sandbox Runner。
- [ ] 固定 Job/Artifact 契约，覆盖取消、partial、timeout、warnings、代码与环境引用。
- [ ] 选择一个 quant 库未覆盖的探索问题，生成代码后在沙箱执行。
- [ ] 实验候选登记 trial；禁止把生成代码热加载为 Extension 或生产 quant 库。

交付：从 Hermes 提议到 Pi 实验、沙箱结果、研究引用的完整样例。

验收：本地 shell、RPC 管理命令、加载扩展、跨作业文件请求均无法突破允许范围；标准 quant 任务继续直接执行库函数。

### S09 — 完成国内入口与 MVP 运维验收（Phase 1B；依赖 S02、S07，可与 S08 并行）

- [ ] CN PostgreSQL 保存身份、成员关系、tasks、submission outbox 与 event cursor。
- [ ] 提交使用 mTLS/服务鉴权/租户幂等键；SG 是执行状态唯一权威。
- [ ] CN 拉取持久事件，在本地事务更新镜像与 cursor；浏览器通过 SSE 收进度。
- [ ] cursor 缺口/过期、跨境断线、请求超时能重新对账。
- [ ] Web 展示数据截止、来源、三组状态、数据质量、成本及授权允许的产物。
- [ ] 多用户开放前完成 RLS、对象下载、cache/session 越权测试。
- [ ] 真实数据恢复演练、资源压测与故障注入，记录实测 RPO/RTO。

交付：两台 ECS 上的可操作 MVP 与部署/恢复运行记录。

验收：断网不丢提交、不重复研究；无授权用户不能读取结果；预算耗尽和数据缺失可解释；MVP 完成不能代替“预测有效”的统计结论。

### S10 — 完善评估、Memory 与审批（Phase 2；依赖 S07，评估需成熟标签）

- [ ] Dashboard 展示预登记全集、可评分子集、paired loss、缺失、回退和成本。
- [ ] 使用适合依赖结构的方法估计不确定性；有限样本不作稳定有效宣称。
- [ ] Memory 内容、状态事件、实际上下文快照分别存储，历史召回使用当时状态。
- [ ] postmortem 与候选 Lesson 留在探索区，正式上下文只包含批准的规则。
- [ ] 完整实验登记、审批界面和 release 审计；不是此阶段才开始留审批记录。
- [ ] Lesson 首次生效须人工批准并纳入新 release；涉及 LLM 行为须前向验证。
- [ ] Lesson 紧急撤销时暂停相关未完成 campaign，以新 release 创建新批次；保留原批次和既有快照，不静默改写上下文。

交付：可审计评估、复盘与受控上下文发布。

验收：后来 validated/retired/superseded 的状态不会改变过去召回；候选 Lesson 正文无法借背景材料进入正式预测；每份评估明确分母和实际使用的版本。

### S11 — 受控进化与扩展（Phase 3；依赖 S10）

- [ ] 预登记 challenger 的 release、目标、主检验、样本、查看次数和停止规则。
- [ ] 纯量化候选进行带成本、purge/embargo 的历史验证；LLM 相关候选等待未来 shadow cohort。
- [ ] Evaluator 独立权限，Evolution 不读取最终样本或修改阈值。
- [ ] 使用过的最终评估窗口不重新充当未见样本；继续积累新的未来窗口。
- [ ] 人工批准 promote，保存依据并支持回滚到已批准 release。
- [ ] 根据实测瓶颈增加计算节点；之后再考虑复杂模型、Canvas、Portfolio 或检索派生层。

交付：可审计的候选比较、批准和回滚流程。

验收：修改 prompt/model/memory/工具/降级政策均产生新版本；未批准候选不能影响生产预测；历史模拟与正式前向结果清晰区分。

## 3. 评审问题到实施任务的映射

以下全部已在设计文档合并，工程状态均为待实施/待验收。

| 评审编号 | 修复主题 | 实施任务 |
| --- | --- | --- |
| P0-1 | 信息截止与预测封存/确认时间 | S00、S05 |
| P0-2 | 来源时间、系统时间与快照 | S04 |
| P0-3 | Memory 状态穿越与候选 Lesson 注入 | S07 默认关闭；S10 受控开启 |
| P0-4 | 全量 cohort、失败与三组配对 | S00、S05–S07、S10 |
| P0-5 | Outcome 追加更正与报告固定版本 | S05 |
| P0-6 | 持久执行、租约、幂等、恢复 | S02、S05 |
| P1-1 | 预测统计量与评分匹配 | S00、S05 |
| P1-2 | 最终样本隔离、指标检验与前向验证 | S00、S10–S11 |
| P1-3 | 标准 quant 绕过 Pi | S03、S06、S08 |
| P1-4 | 沙箱与产物安全 | S01、S03、S08 |
| P1-5 | 国内持久库、采集归属、出口与通信 | S02、S04、S09 |
| P1-6 | research release 覆盖全部行为变更 | S00、S06–S07、S10–S11 |
| P1-7 | 服务端授权与租户隔离 | S02–S04、S09 |
| P1-8 | 归档信任范围与可复现承诺 | S04–S05、S09 |

## 4. 完成记录

### S00 完成记录（2026-09-27）

```text
任务：S00 固定目标、时间和评估协议
负责人：项目所有者（Q1–Q9 决策与确认）；Agent（协议编写与核对，无批准权）
状态：已完成（协议登记层面；工程实现与部署验收归属 S01+）
实现引用：docs/protocols/（target-spec.v1、time-protocol.v1、campaign-policy.v1、known-answer-cases.v1、s00-registration.v1.json）；CONTEXT.md；ARCHITECTURE §5–6 的 Campaign/Batch 同步
固定配置/版本：target-spec-v1、time-protocol-v1、campaign-policy-v1、sampling-json-v1、seed=20260927、N=20、horizon={D1,D20,D60} 主 D20、SPY、周六 06:00 ET cutoff、Phase 1A sources={baseline, quant_model} 无回退
验证命令或报告：known-answer-cases.v1 K01–K11；K01 经独立实现复算，配额、选中名单与 hash 一致
验收时间与结果：2026-09-27 协议定稿。跨开盘、节假日/提前收盘、拆分分红、停牌退市、LLM 未启用/超时均有明确处理；模型版本与证券集合保持待登记，formal_campaign_allowed=false
剩余限制：PIT 总体/GICS 快照、真实名单与 hash、日历/tzdb、SPY 永久 ID、模型与 training manifest、预算/权限/恢复验收、具体 release hash 的人工批准均为 S01+ 阻断项
```

执行时为每个 Sxx 增补以下记录；没有证据的任务保持未完成。

```text
任务：
负责人：
状态：待实施 / 进行中 / 待验收 / 已完成
实现引用：
固定配置/版本：
验证命令或报告：
验收时间与结果：
剩余限制：
```

开发排期只管理交付和验收；20D/60D 标签成熟及有效样本积累按真实交易日计算，不能用回放或合成数据替代。
