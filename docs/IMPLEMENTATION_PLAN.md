# v0.3 修复与实施计划

日期：2026-09-27；结构优化更新：2026-09-28\
设计依据：[ARCHITECTURE.md](ARCHITECTURE.md)\
问题来源：[v0.2 评审](ARCHITECTURE_REVIEW_v0.2.md)\
状态：S00 协议已确认定稿（2026-09-27）；S01 主体完成（Tiingo 字段级验证已实测；EOD 实盘观测随 S04 首次采集、生产套餐条款确认待购买时；网关取消/用量/限额待验收）；S02 已完成（隔离开发环境验收，2026-09-27）；S03 第一/二纵切片完成（沙箱执行、独立 Runner 与 HTTP 产物链路，2026-09-28；生产部署仍待验收）；S04 第一至三纵切片完成（PIT 数据基础、交易日历与冻结快照，2026-09-27；训练 manifest 登记，2026-09-28；独立退市真相源、派生特征与多源仍待补源决策）；S05 已完成四纵切片（封存核心、Outcome 回填、评估报告、归档与监控，2026-09-27）加 S05e 证据追溯收紧（2026-09-28）；S06 第一/二纵切片完成（前向预测管线，2026-09-27；供应商更正自动触发、月度汇总与报告门控，2026-09-28；正式 campaign 启动项仍阻断）；S03 剩余部署项与 S06 正式启动及 S07–S11 待实施、待验收。

## 1. 执行规则

每项任务记录负责人、实现引用、验证命令/报告与遗留问题，满足验收条件后再标为完成。文档编写完成不算工程完成。

2026-09-28 的结构优化按 [仓库边界](REPOSITORY.md) 与 [上游管理](UPSTREAMS.md) 落地：继续单业务仓库；Core 保留原包与迁移链；quant 抽成纯计算包；共享 contracts 与独立 Runner 各有包边界，Runner 自有依赖锁。外部 Agent/UI/检索接入仍按 S07–S11 推进。本轮结果见下方 S03b 与结构优化记录，历史纵切片中的“进程内 Runner”描述保留为当时状态。

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

- [x] 固定 Hermes、Pi、Python/Node、镜像与依赖版本；依据运行时核实验证实际接口（Hermes main `7fa45eb` + Python 3.14 + `uv sync --frozen` 可复现；Pi 0.87.1 + node:22 + RPC 冒烟通过；见 [实测记录](research/s01-target-verification.md)）。
- [ ] Hermes 独立任务实例、工具白名单、关闭研究链路内置记忆/后台学习/会话检索（隔离键名已从官方文档确认，运行时验证待 S07 接入）。
- [x] Pi 工具和 RPC 管理命令白名单依据；资源自动加载、技能及扩展固定为可信部署内容（命令全集与 flags 已固定于 [runtime-version-pinning](research/runtime-version-pinning.md)；wrapper 实现属 S08）。
- [x] 在目标 SG Linux 主机上执行真实 quant 依赖，验证 gVisor、Parquet、资源与网络限制（runsc 开销噪声级、mmap 正常、锁定配置生效、网络阻断；见 [实测记录](research/s01-target-verification.md)）。
- [x] 试用数据源（Tiingo），验证原始版本、源时间、退市/公司行为、许可与模型使用范围（2026-09-27 实测：字段全集、拆分精确、分红复权 ~1e-5 偏差→自算总收益维持、退市三形态含 SGEN 幽灵行/SIVB 缺失、SPY 正常；见 [tiingo-token-verification](research/tiingo-token-verification.md)。遗留：EOD 周五晚实盘观测随 S04 首次采集闭环；生产套餐 ToS 留存/LLM 条款于购买时浏览器确认——S04 正式快照前阻断项）。
- [ ] 验证 LLM Gateway 的工具调用、辅助请求、取消、用量记录和并发限额（工具调用/流式/网关链路已通，火山需 Bearer 头；取消/用量/限额待 S02；见 [实测记录](research/s01-target-verification.md)）。
- [x] 用隔离测试库验证备份工具的本地 WAL 归档与恢复（OSS 已排除出 MVP 范围）（[演练脚本](../ops/backup/pitr_drill.sh)、[记录](ops/backup-pitr-drill.md)：归档+基准备份+PITR 停点验证通过；容量级指标待目标机 S09 重测）。

交付：固定版本清单、技术验证记录、供应商与授权决定、恢复样例及未解决问题。

验收：所有会改变架构的开放项均有证据或明确收缩方案。只有文档支持、没有目标环境验证的项目不能标记为通过。

### S02 — 建立持久任务、权限和预算（Phase 1A；依赖 S01）

- [x] Core API / Worker 共用模块化代码库，分开运行凭证和资源限额。
- [x] PG run/job/attempt/step/event/outbox 表，短事务领取、租约、心跳、递增 attempt_token（events 表兼作 outbox；步骤留痕以 attempt + events 承载，见 db/meta.py 设计说明）。
- [x] 在每次有副作用的提交处校验 fencing token；服务重启后从已完成步骤恢复。
- [x] Idempotency-Key 绑定租户及 payload hash；同键不同输入拒绝。
- [x] 身份来自鉴权；按 job 签发能力令牌，限制快照、工具、租户和有效期（令牌机制与 scope 已落地；Runner 的快照内容/hash 范围绑定已于 S03b 接入；Hermes 范围接线待 S07）。
- [x] run 级原子费用预留、结算、最大尝试数、墙钟期限、取消传播和结构化日志（墙钟期限由 claim 路径 reaper 执行，超期 run 取消、在造 attempt 结果被 fence）。
- [x] 从第一版设置备份、任务积压、磁盘、WAL、预算与错误告警（备份/PITR 演练、/v1/ops/status 队列积压/待对账预算/未发布事件/未收割过期租约/超期 run/WAL 归档延迟告警；磁盘水位为宿主层监控，随 S09 部署验收落实）。

交付：可独立运行的确定性执行控制与任务查询接口。

验收：杀进程、重复投递、租约过期、旧 Worker 返回、预算同时申请、提交响应丢失均不产生重复业务提交。外部模型调用重试费用完整归集，不宣称外部调用恰好一次。

### S03 — 打通受限计算与产物链路（Phase 1A；依赖 S02）

- [ ] Runner 固定镜像 digest、命令模板、只读根目录、网络和资源限额。（S03b 生产模式在启动时拒绝非 digest 镜像或非 runsc；开发模式需显式选择。目标机 gVisor/资源验收仍待 S09）
- [x] Worker 授权冻结快照 → 签名 HTTP 请求 → 独立 Runner 固定 spool → hash 校验 → 沙箱只读挂载。（MVP 小型 JSON 字节；大规模流式与二进制输入另行交付）
- [ ] 标准 quant 使用已发布镜像入口，不启动 Pi Agent。（进展：sandbox.execute 从固定镜像执行作业脚本，不启动任何 Agent；Pi 接入归 S08）
- [x] Runner 经 HTTP 返回校验产物 → Worker 核对 job/attempt/hash → fencing 与事件同事务入库。（请求绑定 tenant/冻结内容/租约；跨租户作业快照拒绝。OSS 已排除 MVP）
- [ ] 限制路径、文件类型/大小/数量；拒绝穿越、symlink/hardlink 和不安全反序列化；复杂解析在受限环境。（已落地：tar 流内存校验——白名单扩展、单文件/总量/数量上限、拒绝 symlink/hardlink/特殊文件/穿越/非 UTF-8，产物不落宿主磁盘）
- [x] 超时/取消终止计算，清理容器和临时文件，保留受控日志。（S03b：HTTP 取消、租约到期停止、清理完成前占用并发槽、日志轮转及有限回执缓存；强杀后的遗留容器在 Runner 下次启动回收，目标机恢复时限仍需 S09）

交付：可信 Runner 与统一批处理执行 Interface。

验收：沙箱无法读取密钥、其他作业目录或连接网络；恶意产物不会在宿主/主站执行；OOM、磁盘满、日志暴涨不拖垮 PG。尚未有生产数据时先用固定合成快照验收。

### S04 — 建立 PIT 数据与冻结证据（Phase 1A；依赖 S02，可与 S03 并行）

- [ ] 证券永久 ID、标识历史、交易日历、公司行为、退市、市场/财务源版本。（进展：永久 ID + 标识历史 + 退市标志 + **交易日历（规则生成、实测验证）** 已落地；独立退市真相源待后续）
- [ ] 明确 source_available_at、ingested_time、usable_at、时间精度和质量等级。（进展：三时间戳 + 证据依据 + zero_volume 质量标记已落地）
- [ ] Data Service 统一采集、源限速、授权标签、缓存与原始响应；collector 不重复实现供应商逻辑。（进展：Tiingo 采集器 + 限速 + 授权标签 + 原始响应存储已落地；多源扩展待后续）
- [ ] 前向查询与 historical_source 重建分别标识；正式查询必须携带 Controller 时间与权限上下文。（进展：as_of + mode 双模式查询已落地；权限上下文待接入能力令牌）
- [ ] Snapshot 固定查询、证券、源版本、原始对象、schema、文件 hash 与代码版本。（进展：daily_bars 快照冻结/读取/审计验证已落地，见 S04b 进度；日线快照 Runner 接入见 S03b，特征快照及 Hermes 接入待后续）
- [ ] 派生特征保留依赖；不能用当前 consensus/IV/重述值替代缺失历史值。（待实施）
- [ ] 训练 manifest 固定预处理、成熟标签、拟合窗口、校准与模型产物。（已落地：training_manifests 只追加登记 + 结构校验（五项固定事实显式声明，none 需明说）+ 载具 manifest 已知答案（artifact hash 锁定 quant 模块字节）+ campaign 注册验证 ref/hash/特征集/模型版本一致性，见 S04c 进度；正式模型的 manifest 待模型选定后登记）

交付：可冻结、可授权读取、可恢复的证据快照。

验收：回补不能改变旧快照；重述前后按版本正确查询；不能跨租户读取私有快照；任何模型输入均能追溯到截止时间内的数据或冻结规则。

### S05 — 实现 Ledger、结果更正与最小评分（Phase 1A；依赖 S02–S04）

- [ ] 创建 campaign/case，原子封存一个 case/release 的三种 source 结果及引用。（进展：release/批准、campaign 登记、batch/case 规划与封存核心已落地，见 S05a 进度；正式查询 API 与 job 驱动接线归 S06）
- [ ] 唯一约束、source 状态/空值、值域、输入 manifest 与 attempt 校验。（进展：唯一约束、值域、空值纪律与 attempt fencing 已落地；produced 来源的 evidence 快照强制引用与封存边界 PIT 纪律已随 S05e 落地）
- [ ] 获取 case/chain 锁后重新检查实时钟与 deadline；持久提交确认与及时性判定追加留痕。（已落地：链头锁后 clock_timestamp 重检窗口，锁等待跨 deadline 拒绝；确认事件追加，未确认保守 uncertain）
- [ ] 应用 UPDATE/DELETE/TRUNCATE 禁止；提交权限、链头锁、规范化行内容与链序号固定。（进展：DB 触发器 + 逐 campaign 哈希链 + 链校验器已落地；提交权限的 API 接线归 S06/S07，生产角色 revocation 随 S09 部署）
- [ ] Outcome 按 case 回填，以 revision/supersedes 追加更正，禁止分叉和覆盖。（已落地：见 S05b 进度；退市/并购对价等公司行为终值仍限价格序列内的拆分分红，复杂事件走 unresolved+依据路径）
- [ ] 最小 Evaluation 固定 case、commit、结果版本及评分代码；按预测类型计算 Brier、MSE/RMSE 等。（已落地：见 S05c 进度；月度汇总与区块自举区间明确不在此片，前者归 S06 调度、后者待参数登记）
- [ ] 本地 Ledger 归档与链校验、对象引用恢复及迟到/未确认监控；独立控制的链头锚定与恢复副本仍需 S09。（已落地：OSS 已排除 MVP → 本地内容寻址归档导出（JSONL + manifest 含链头 hash，供链外锚定）；verify_archive 免库自证（文件 hash + 从归档行重算提交链 + outcome 链 + 报告 hash）；verify_against_db 对象引用恢复校验（FK 防删 + 快照/raw 对象完整性）与链分歧报告；归档目录追加式永不改写；迟到/未确认监控入 ops（见 S05d 进度））

交付：可独立运行的预测封存、到期结果任务和可复算报告。

验收：三组不能部分提交；锁等待跨 deadline 被拒绝；响应丢失保守标记 uncertain；更正产生新报告且旧报告不变；恢复后输入与指标 hash 可核对。合成数据验收不得标记为真实前向成绩。

### S06 — 开始 baseline/quant 前向运行（Phase 1A；依赖 S05）

- [ ] 发布固定 baseline 与简单量化模型，登记训练与校准版本。（进展：管线载具 baseline-constant-v0 / quant-momentum-v0 已随 S06a 落地并全量披露；正式模型选择须 Trial 登记 + release 人工批准，待阻断项解除）
- [ ] 人工批准初始 release，按已登记规则固定20证券 panel，事前登记每批60个 case。（阻断：S&P 500 PIT 总体/GICS 快照未取得、生产套餐 ToS 未确认、人工批准未发生；工程路径已就绪）
- [ ] llm_adjusted 保留 unavailable/not_enabled，不填充伪造 LLM 结果。（管线已固定封存该位置，S06a 验收）
- [ ] Scheduler 按周创建新批次，按交易日检查到期 Outcome。（已落地：见 S06a/S06b 进度；已 resolved Outcome 的供应商更正自动触发已于 S06b 落地）
- [ ] 最小只读查询显示计划数、完成数、缺失、迟到、数据质量和评分适用范围。（已落地：campaign_status 服务 + 租户隔离 API，见 S06a 进度）

交付：实际预测记录、定时任务及首批回填结果。

验收：从真实运行时刻开始积累；正式结果只使用已成熟标签。首批标签未到期时可标记“预测管线运行中”，完整闭环验收需等待真实回填。

### S07 — 接入 Hermes 与三组预测（Phase 1B；依赖 S06 的预测管线验收）

首次真实批次成功封存、任务与数据检查通过后即可开展本项，不要求等待全部标签成熟；Phase 1A 的完整结果闭环仍需实际回填验收。

- [ ] 将 Controller 的冻结计划/证据转换为 Hermes 输入；每个 run 独立上下文。
- [ ] 在 `services/agent-runtime/` 建立独立 Hermes Python 3.14 环境，固定上游与依赖；实现 FrozenEvidence → ResearchProposal 契约及权限、取消、升级兼容性测试。
- [ ] 先使用一个研究综合角色及固定反证步骤；按实际收益再扩展并行角色。
- [ ] 研究引用可定位原文，warnings、缺失、量化依据、实际模型和成本完整输出。
- [ ] Hermes 返回 Proposal；Controller 校验并封存，Agent 无权写 Ledger 或改变 Lesson。
- [ ] prompt 回归验证结构、引用、权限与成本，记录新的 release。
- [ ] 从新 campaign 启用 llm_adjusted；按预登记政策记录失败/回退，不回补历史 LLM 预测。

交付：完整研究报告与同 case 的三组前向输出。

验收：故障后可恢复；同批三组共享目标和证据版本；生成截止后到达的结果不能进入准时评估。固定输入可复查，不要求外部 LLM 重跑逐字一致。

### S08 — 接入一个 Pi 探索任务（Phase 1B；依赖 S03、S07）

- [ ] 通过受控 RPC wrapper 运行 Pi，所有模型调用归到父 run 预算。
- [ ] 在实际接入时创建 Pi Node 包与 `integrations/pi/` 适配/扩展；固定包版本和锁，以 RPC 契约测试验收升级。
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
- [ ] 评估 Open WebUI 适配或复用归档 Next.js；界面只消费 Core 接口，固定上游版本并验证身份、任务与结果契约。
- [ ] 多用户开放前完成 RLS、对象下载、cache/session 越权测试。
- [ ] 真实数据恢复演练、资源压测与故障注入，记录实测 RPO/RTO。
- [ ] 使用独立构建镜像、完整 digest 与目标机验收报告生成部署清单；运行 `infra/validate_upstreams.py --mode deployment` 并验证升级/回滚兼容性。

交付：国内入口与 SG Core 主机上的可操作 MVP、部署与恢复运行记录；SG 当前基线为 DigitalOcean 4 vCPU / 7.8 GB。

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

以下全部已在设计文档合并；工程状态查对应任务清单与完成记录，设计修复不代表全部部署验收已经完成。

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

### S02a 进度（2026-09-27）

- 状态：**S02a 已完成**（隔离开发环境验收；S02b 未开始）。
- 交付：schema/迁移（tenants/runs/jobs/attempts/events/budget_entries）、幂等 run 提交 API（租户绑定 Idempotency-Key + payload hash）、claim/租约/心跳/fencing（attempt_no 为 fencing token）、租约过期 reaper（requeue/取消/终态语义）、取消与完成竞争规则（迟到完成保留但不 apply）、预算 reserve/settle/release + 待对账查询（并发预留原子限额、结算幂等、未知费用阻塞预算、迟到 attempt 费用照记）、事务 outbox（events 表同事务追加 + publisher）、worker 循环入口。
- 验收：23 个测试覆盖七个场景（重复投递、崩溃恢复、租约过期、旧 Worker 迟到提交、取消/完成竞争、预算并发与重复结算、响应丢失重试）；TDD 过程修复三个真实缺陷（FastAPI 装饰器状态码覆盖幂等重放、取消未及运行中 job、过期租约迟到提交未被 fence）。
- 待办：S02b（身份/能力令牌、取消传播、Gateway 验收、结构化日志、备份演练、告警）。

### S02b 进度（2026-09-27）

- 已完成：Bearer API Key 认证（sha256 存储、撤销、admin bootstrap 键管理租户/键；X-Tenant-Id 通道已移除）、按 job 签发 HMAC 能力令牌（绑定 job/attempt/租户/范围，有效期≤租约）、取消传播（watchdog 心跳同时轮询 job 状态，取消时终止 handler、attempt 落 cancelled）、LLM Gateway 客户端（预留→调用→结算；fail-closed：账本不可达不发送；传输/网关错误释放、超时保留待对账；占位价待对账）、结构化 JSON 日志。
- S02b 收尾（同日完成）：墙钟期限执行（claim 路径 reaper：超期 run 及其未终态 job 取消、run.deadline_exceeded 事件、在造 attempt 补交结果被 fence）；ops 健康检查与告警端点（/healthz 无鉴权 liveness；/v1/ops/status 管理键鉴权，报告队列积压、待对账预算、未发布事件、未收割过期租约、超期 run、WAL 归档状态及阈值告警）；备份恢复演练（本地 WAL 归档 + PITR）。
- 验收：59 个测试（新增：墙钟期限 3、ops 8）；PITR 演练一次通过（备份后提交恢复后在库、目标时间后提交不在库、恢复库过 alembic head 与应用读路径）。

### S02 完成记录（2026-09-27）

```text
任务：S02 建立持久任务、权限和预算（S02a + S02b + 收尾）
负责人：项目所有者（验收）；Agent（实现与测试）
状态：已完成（隔离开发环境验收；生产部署与真实恢复演练归 S09）
实现引用：youwei_core/{api,auth,budget,jobs,llm,ops,worker}；migrations 086db0070190 + 0d8471d9eb62；tests/；ops/backup/pitr_drill.sh
固定配置/版本：Settings（YOUWEI_* 前缀）：lease_ttl 30s、心跳 10s、告警阈值（队列积压 300s/未发布事件 60s/待对账 3600s/WAL 归档 1800s）；fencing token = attempt_no；生产归档配置要求见 docs/ops/backup-pitr-drill.md
验证命令或报告：`.venv/bin/pytest -q` → 59 通过；`./ops/backup/pitr_drill.sh` → PASS（实测见 docs/ops/backup-pitr-drill.md）
验收时间与结果：2026-09-27。杀进程、重复投递、租约过期、旧 Worker 迟到提交、取消/完成竞争、预算并发与重复结算、提交响应丢失均不产生重复业务提交；墙钟超期 run 不再执行且在造结果被 fence；PITR 停点正确（备份后提交在库、目标后提交不在库）
剩余限制：火山侧网关取消/用量/并发限额验收与真实定价对账待接入（S07 前完成）；能力令牌的快照粒度 scope 待 S04 快照 ID；磁盘水位、告警外发通道、生产环境实测 RPO/RTO 随 S09 部署验收；日志采集管道随部署建立
```

执行时为每个 Sxx 增补以下记录；没有证据的任务保持未完成。

### S04a 进度（2026-09-27）

- 状态：**S04 第一纵切片完成**（隔离开发环境验收；切片范围见下，S04 整体未完成）。
- 交付：证券主数据（永久 ID + 标识有效期，as-of 解析含 ticker 复用/歧义处理）、Tiingo EOD 采集器（限速客户端 + 严格解析 + 原始响应不可变存储 + 内容级去重）、价格观测表（版本 = raw_object_id；volume=0 幽灵行标记 zero_volume）、PIT 查询（as_of + mode 双模式：forward 拒绝未来时间、historical_source 显式标记）、`data.tiingo_daily` job handler 接入 S02 worker（重试/租约/围栏语义自动继承）；migration `a1c2e3f4b5d6`。
- 验收：26 个新测试覆盖：回补不改变旧 as-of 结果（S04 核心验收项）、更正产生新版本且 PIT 正确选版、无源时间时保守处理（usable_at 管辖、依据入档）、SGEN 幽灵行标记、同内容去重、限速间隔、worker handler 端到端。
- 实测：真 token 冒烟（一次性容器 + 迁移 + AAPL/SPY 真实采集 + PIT 查询 + 去重），AAPL 周五收盘 341.07 与早前直接探测一致。
- 待办（S04 剩余）：独立退市真相源、派生特征依赖、训练 manifest、多源接入与正式查询权限上下文；生产套餐 ToS 确认后才能作正式快照源。

### S04b 进度（2026-09-27）

- 状态：**S04 第二纵切片完成**（隔离开发环境验收 + 真实数据校验）。
- 交付：NYSE 交易日历（规则引擎 nyse-rules-v1：固定/浮动假日含周末顺延与元旦例外、Juneteenth 2022+、特别闭市表、提前收盘；版本化构建 append-only + 内容 hash，日历行可重建；封存 case 经 manifest 的 version+hash 保留原计划日历）、会话时刻（zoneinfo America/New_York DST 感知，09:30/16:00/13:00 ET→UTC）、批次时刻解析（time-protocol §1：周六 06:00 ET cutoff→下一常规交易日开盘入场、开盘前 15 分钟 deadline、周一休市顺延；非法 cutoff 拒绝）、D1/D20/D60 horizon 偏移（入场日计 D1）、时钟偏差检测（Settings 阈值）、日历 vs 观测行情交叉校验（休市日报错、交易日缺 bar 报告）；冻结快照（content-addressed：PIT 查询物化 + 完整 manifest——查询/mode/证券/raw 对象版本/schema/行数/内容 hash/供应商版本与授权标签/代码版本；缺失显式记录 rows=0 不伪造；同 query 同 as_of 重复冻结返回同一快照；读取验证内容 hash，审计验证器检查 raw 对象完整性）；migration `b7d8e9f0a1c2`。
- 验收：27 个新测试：假日规则对照已知交易所事实（2020/2021/2022/2025/2026 具体日期）、DST 边界（EDT 13:30 UTC vs EST 14:30 UTC）、D20/D60 手算对照、cutoff 校验、快照不可变（更正后重冻结旧 as_of 字节级一致）、篡改检测。
- **实测（日历）**：规则日历 2020-2027 构建后，与真实 SPY 2024-01-01..2026-09-25 全部 686 个交易日交叉验证——**双向零差异**（无休市日 bar、无缺 bar 交易日；含 2025-01-09 卡特哀悼日、三年 Good Friday、2025-12-24/2026-11-27 提前收盘）；批次解析：2026-09-26 cutoff→入场 09-28 09:30 EDT、D20=10-23、D60=12-21。
- 待办：tzdb 包固定与 NTP 阈值目标机实测（S09）；快照与能力令牌 scope 接线归 S03/S07。

### S05a 进度（2026-09-27）

- 状态：**S05 第一纵切片完成**（隔离开发环境验收；切片范围见下，S05 整体未完成）。
- 交付：**登记层**——research release 不可变登记（规范化 manifest 内容 hash，排除自身与批准记录）+ 人类批准记录（campaign 注册强制校验批准 hash 匹配；Agent 只记录不批准）、campaign 事前登记（Phase 1A 强制 enabled_sources={baseline, quant_model} + 无回退；panel/benchmark 存在性校验；同键同内容幂等）、批次规划（周六 06:00 ET cutoff 经版本化日历解析 deadline/entry/exit，case 实例窗口封存；过去 cutoff 必须显式 backfilled_plan 补记漏周，保留在分母）、漏跑事实追加事件。**封存核心**——逐 campaign 哈希链（链头行事务内 FOR UPDATE 锁、单调序号、规范化行内容含 predictions、prev_hash 链接、verify_chain 从存储行重算全部内容 hash）；seal 短事务：链锁后 clock_timestamp() 重检窗口（早干 cutoff 拒绝、跨 deadline 拒绝不回填、锁等待跨 deadline 同样拒绝）+ attempt fencing（attempt_no/状态/租约/租户）+ 三位置原子插入（Phase 1A llm_adjusted 固定 unavailable/not_enabled；enabled 来源失败可 unavailable+reason；值域 0≤p≤1、有限数值、unavailable 无值、fallback 拒绝）；及时性：第二短事务追加 durable_confirmation（自身 clock_timestamp 判定 on_time/late，部分唯一索引保证至多一条），封存与确认之间崩溃 → 未确认，过 deadline 保守 uncertain，重放补确认但不升级已判定；同 (case, release) 幂等重放返回原 commit、不同业务内容冲突。**不可变性**：8 张 ledger 表 BEFORE UPDATE/DELETE/TRUNCATE 触发器（youwei.ledger_mutation 会话变量为运维/测试逃生阀）；migration `c9d0e1f2a3b4`。
- 验收：24 个新测试：release 幂等/冲突/批准强制、campaign 校验与幂等、2026-09-26 已知答案批次窗口（D20=10-23、D60=12-21、DST）、过去 cutoff 补记、触发器拒绝 UPDATE/DELETE/TRUNCATE、封存原子性与事件、窗口违规、**锁等待跨 deadline 拒绝**（双连接实测）、stale attempt 与跨租户 fencing、幂等重放与冲突、确认崩溃窗口→uncertain→重放补 late 不升级、值域反例 8 组、时钟偏差停机、链链接与篡改检测。迁移实测：upgrade/downgrade/upgrade、16 触发器、部分唯一索引。
- 待办（S05 剩余）：最小评估报告、Ledger 归档与迟到/未确认监控；evidence 快照强制引用与 seal 的 API/worker 接线归 S06；生产环境应用角色 revocation 随 S09 部署（当前触发器对 owner 同样生效，但 owner 可绕过，与“不声称绝对防篡改”一致）。

### S05b 进度（2026-09-27）

- 状态：**S05 第二纵切片完成**（隔离开发环境验收；切片范围见下，S05 整体未完成）。
- 交付：outcome_revisions 只追加版本链（unique(case_id, revision) + supersedes 恒指当前头 + case 行锁串行化，禁分叉禁覆盖；DB 级约束：resolved ⟺ 三收益非空、revision 1 ⟺ 无父；表纳入 append-only 触发器）；收益计算 resolver `total-return-v1`（入场开盘→出场收盘，入场日计 D1；除息日收盘再投资——入场日除息无权、出场日除息仍计入；拆分先于分红调 units；与基准同口径；全部量化到 1e-10）；解析流程：DB 时钟 PIT forward 视图 → 完整性检查（窗口内每个交易日两证券均有 quality-ok 有效 bar，zero_volume 幽灵行不算有效价）→ 冻结快照并从快照内容计算（存储数字可从引用快照重算）→ 追加 revision；宽限期内缺失 → pending 不写；过期仍缺 → unresolved 附缺失清单与依据；unscorable 为证据驱动的市场事实（reason+evidence 必填，resolve 不得静默改写）；迟到数据/供应商更正 → 新 revision 追加（correction_reason，默认 data_revision），旧版本不变；幂等：同状态+同快照内容+同值 → 返回原头；verify_outcome_chains 结构化链校验（编号连续 + supersedes 线性）。migration `d0e1f2a3b4c5`。另修复：decimal_str 规范化（Decimal 零的 str 不稳定，会破坏内容 hash 重算），sealing/outcomes 统一使用。
- 验收：15 个新测试：分红总收益手算对照（0.21/0.01/0.20）、D1 同日入出、除息边界（入场日不计/出场日计入）、拆分、pending 不写、宽限期后 unresolved 附缺失、zero_volume 无效价、迟到数据补齐→resolved rev2、供应商更正→新值 rev2 旧版不变、幂等重解析、未成熟拒绝、unscorable 证据必填且粘滞、DB 约束反例、append-only 触发器、链篡改检测（自引用 supersedes 被验证器发现；置 NULL 路径被 DB 约束直接拒绝）。迁移实测：upgrade/downgrade/upgrade、2 触发器。
- 待办（S05 剩余）：最小 Evaluation（固定 case/commit/outcome 版本与评分代码，Brier/MSE）、Ledger 归档与迟到/未确认监控；退市/并购复杂对价终值仍走 unresolved+依据路径（依赖 S04 独立退市真相源）；调度器驱动归 S06。

### S05c 进度（2026-09-27）

- 状态：**S05 第三纵切片完成**（隔离开发环境验收；S05 剩余归档/监控项见下）。
- 交付：evaluation_reports 只追加版本链（unique(batch, horizon, version) + supersedes 恒指当前版本 + 首版无父 DB 约束；表纳入 append-only 触发器）；批双 horizon 报告生成器：门槛（该 horizon 全部 case 过计划 exit 且均有 outcome 头，否则 NotReady 列明 pending——数据等待由 resolver 宽限政策决定，报告不无限等待）；内容固定引用（case/commit/outcome revision 清单、release、scoring code version `scoring-v1`、内容 hash，无时间戳→同头重生成幂等）；主指标 d_i=(p_quant−y)²−(p_baseline−y)²干可配对子集（准时确认 + 双源 produced + resolved）半均值，配对均值 NA 不填 0；y=1(excess>0)、恰好 0 记 false；逐源 Brier/MSE/RMSE（各自可评分集，分母单独披露）；coverage 全量披露（planned/with_commit/on_time/pairable/outcome 状态分布/source 状态分布，逐 case exclusion 理由：no_commit/commit_late/commit_uncertain/*_not_produced/outcome_unresolved/outcome_unscorable）；更正→新报告版本追加，旧报告保留原引用与原指标；v1 仅描述统计，区间方法明确不启用。migration `e1f2a3b4c5d6`。
- 验收：6 个新测试：六路径全覆盖场景（配对/仅 baseline/无 commit/迟到确认/unresolved/unscorable，手算 d=-0.07、Brier 0.23125/0.09、MSE/RMSE）、无配对→NA、零超额记 false（d 手算 -0.07）、NotReady 双门槛（未成熟/缺 outcome 头）与未知批次、幂等重生成+更正追加 v2（旧版引用不变）、append-only 触发器。迁移实测：upgrade/downgrade/upgrade、2 触发器。
- 待办（S05 剩余）：Ledger 归档（OSS 已排除 MVP；本地归档导出+链外锚定+对象引用恢复）、迟到/未确认 commit 与 pending outcome 的监控接入 ops 告警；月度报告与调度器归 S06；Phase 1B fallback 位置的评估区分待 S07。

### S06a 进度（2026-09-27）

- 状态：**S06 第一纵切片完成**（隔离开发环境验收；正式 campaign 启动项仍阻断，见待办）。
- 交付：**预测管线**——`research.batch_predict` job handler：批次级单一证据快照（全 panel+基准，cutoff 处 PIT forward 冻结，同一 manifest 共享）→ 模型注册表（baseline-constant-v0 常量 / quant-momentum-v0 二十日动量载具，历史不足 unavailable+insufficient_history，明确非正式模型）→ 逐 case 原子封存（Phase 1A llm 固定 unavailable/not_enabled；attempt fencing；逐 case 失败报告不静默丢弃；job 重试幂等）。**调度器 tick**——事前登记：每个活跃 campaign 始终预登记即将到来的周六 cutoff；漏周显式 backfill+batch.missed 事件保留分母；窗口内批次幂等提交恰好一个预测 run（idempotency key=batch）；到期 Outcome：exit 已过且无头或 unresolved 的 case 重解析（resolved 不自动重跑、unscorable 粘滞）；报告：完备 (batch, horizon) 幂等重生成；tick 全步骤幂等可重入，异常逐项记录不中断。**状态查询**——campaign_status 服务 + GET /v1/campaigns/{id}/status（租户隔离，不泄露存在性；计划/commit/准时/迟到/未确认/无 commit/outcome 状态/报告版本）；worker 接线（handler 注册 + scheduler 循环，Settings.scheduler_interval_seconds=60s，异常不杀 worker）。
- 验收：8 个新测试：tick 预登记即将 cutoff 且幂等、漏两周补记（backfill+miss 事件+未来周正常）、窗口内恰好一个预测 run、handler 全 case 封存（共享单一证据快照、18 预测位置、flat 行情动量 0、重试 already_sealed）、无历史 quant unavailable、逐 case 失败不炸 job、tick 解析到期 Outcome 并生成 D20 报告（D1/D60 未成熟不生成）、状态视图全链路 + API 租户隔离（200/404/401）。
- 待办（S06 剩余）：正式 campaign 启动项——S&P 500 PIT 总体/GICS 快照与真实 20 证券名单（依赖成分源）、生产套餐 ToS 确认、正式模型 Trial 登记 + release 人工批准、预算配置；月度汇总、更正自动触发与报告 tick 规模化门控已随 S06b 落地。

### S03a 进度（2026-09-27）

- 状态：**S03 第一纵切片完成**（隔离开发环境验收；部署形态与 runsc 实测见待办）。
- 交付：**沙箱 Runner**（`youwei_core/sandbox/`）——隔离契约全部由 Runner 强制，调用方仅提供不可信文本（script/argv/env）+ snapshot_id，不能指定镜像/挂载/网络/宿主路径：固定镜像（仅 Runner 配置，部署时 digest 固定）、非 root（65534）、只读根、cap-drop ALL、no-new-privileges、CPU/memory/PID 限额、`--network none`、容器仅获显式 per-job 环境变量；输入（物化快照 + 脚本）只读挂载。**输出链路**：产物写入 size-capped tmpfs（磁盘满风险被 tmpfs 硬性约束，宿主无可写路径）；容器 detached 运行，可信 wrapper 写完成标记，`docker exec tar` 流式导出后**内存内 tar 校验**（白名单扩展、单文件/总量/数量上限、拒绝 symlink/hardlink/特殊成员/绝对路径/穿越/非 UTF-8）——产物从不落宿主磁盘；实测发现 `docker cp` 不可见 tmpfs 内容，故改用 tar 流。**生命周期**：墙钟超时杀死并清理容器、OOM 检测（exit 137 / State.OOMKilled）、日志截断（64KB）；脚本自身非零退出是结果而非基础设施故障。**产物存储**：`artifacts` 表（append-only 触发器）+ attempt fencing 受限存储路径（迟到/跨租 Worker 产物拒收）+ 租户隔离读取；spool 物化双重 hash 校验（读取时 + 写入后，篡改检测）。**接线**：`sandbox.execute` handler 注册入 worker（Settings：sandbox_image/runtime/memory/timeout）；migration `f2a3b4c5d6e7`。
- 验收：11 个新测试（实测容器）：隔离契约（网络阻断/只读根/非 root/宿主环境变量不泄露）、快照物化→脚本读取→产物入库全链路（hash/attempt 绑定/事件）、脚本失败是结果、argv shell-quoting 安全、symlink 拒绝、扩展/大小/UTF-8 三类反例、超时杀死且容器无残留、OOM 检测、spool 篡改检测、存储 fencing、append-only 触发器。迁移实测：upgrade/downgrade/upgrade、2 触发器。
- 待办（S03 剩余）：Runner 独立部署形态（当前 in-process 于 worker）与 Data Service 流式接口（当前直读 DB 快照）；目标机 runsc 运行时实测（S01 已验环境，需接线验证）；Parquet 等二进制产物类型扩展；定量镜像入口与 quant 库发布（S06 正式模型时）；磁盘水位监控随 S09。

### S05d 进度（2026-09-27）

- 状态：**S05 第四纵切片完成，S05 四项任务全部落地**（隔离开发环境验收；正式前向运行归 S06）。
- 交付：**Ledger 归档**（`youwei_core/ledger/archive.py`）——`export_campaign_archive`：campaign 完整 ledger 记录（commits/predictions/commit_events/outcome_revisions/evaluation_reports）导出为确定性 JSONL（规范排序 + 规范化序列化）+ manifest（逐文件内容 hash、行数、导出时链头 seq/hash、release 引用），时间戳子目录每次导出新建（追加式约定，永不改写）；manifest 链头 hash 为**可提交外部锚定的值**（当前仅存于本地，文件和 hash 均可被同一管理员重写；尚未实现独立锚定）；`verify_archive` **免数据库自证**：文件 hash、从归档行重算完整提交链（内容 hash 重算——篡改者同时修正文件 hash 仍会被内容重算识破）、outcome 修订链、报告内容 hash、manifest 链头一致；`verify_against_db`：对象引用恢复校验（引用快照存在且内容/raw 对象完整——DB 层 FK 已防删除被引快照，内容篡改由校验识破）+ 活库与归档链分歧报告（导出后新封存为预期 note）。**监控**：ops_snapshot 新增 ledger 节——`unconfirmed_past_deadline`（确认丢失，任意发生即告警 `ledger_unconfirmed_past_deadline`）、`late_confirmations`（合法记录态，仅披露）、`unheaded_overdue_outcomes`（exit 后 14 天无 outcome 头 → 调度器卡死告警 `ledger_outcomes_not_resolved`；14 天裕量安全覆盖假日拉伸的 5 交易日宽限期）。
- 验收：6 个新测试：导出→免库自证（链头/行数/双导出独立目录）、**双重篡改检测**（文件 hash 与内容 hash 重算两级）、链分歧 note + FK 防删 + 内容篡改识破、未知 campaign 拒绝、ops 干净态零告警 + 未确认过期告警 + 重放补确认后转为 late 披露、exit 过期无头告警。
- 待办：链外锚定操作流程（人工将 manifest 链头 hash 记录到 Git/外部媒介，随首个正式 campaign 启动）；归档自动化调度（当前手动导出，随 S09 部署周期化）；归档恢复演练（从归档重建可读视图，S09 恢复演练范围）。

### S03b 与仓库结构优化（2026-09-28）

- 状态：**独立 Runner 与 HTTP 产物链路已完成开发环境验收**；单业务仓库、quant/contracts 边界、镜像与上游管理已落地。S03 的目标机 gVisor、已发布 quant 镜像和生产容量验收仍未完成。
- 执行边界：Runner 从 `youwei_core/sandbox/runner.py` 提取到独立 `services/sandbox-runner/`，有独立依赖锁；Core 仅保留授权、HTTP 客户端和 fenced 入库。共享 `youwei-contracts` 绑定 tenant/job/attempt、请求内容 hash 与租约，传递冻结快照及受校验的文本产物。Runner 无 Core/数据库/供应商依赖；仅 Runner 镜像持有 Docker CLI，Core 不安装 Runner 运行依赖。
- 故障与资源处理：HTTP 重复提交幂等、异内容冲突、租约到期停止、取消传播、跨租户快照拒绝、产物 hash/归属校验；清理完成前保留并发槽，限制请求/产物/缓存体积。修复 Worker 心跳数据库故障与进程退出的状态处理，保留租约恢复路径，避免任务卡在无法回收的状态。
- 计算边界：baseline/quant 工程模型提取到 `quant/models.py`，不改变公式、窗口或版本；新增已知答案测试。收益解析仍在 Ledger，此次不改变目标协议、试验结果或 release 批准状态。
- 上游与部署：新增 `infra/upstreams.lock.yaml`、catalog/deployment 校验器、Core/Runner 独立 Dockerfile 与本地开发 Compose。发布检查绑定精确镜像、渲染配置、验收报告及清单 hash；所有未接入上游保持 disabled。Hermes/Pi/Open WebUI/OpenViking 的正式接入和契约测试继续归 S07–S11。
- 验证：`uv run --frozen pytest -q --tb=short --maxfail=3` → **216 passed**；Runner 独立锁安装通过；两个实际镜像构建及无网络依赖隔离检查通过。真实开发 Compose 的 API → Worker → HTTP Runner → 沙箱 → 产物/事件入库通过，临时资源已清理；修复了 API 只接 internal 网络时本机映射端口不可达的问题。命令、环境与范围见 [结构验证记录](ops/structure-verification.md)与 [Compose 联调记录](ops/runner-compose-smoke.md)。
- 文档：同步 `AGENTS.md`、`CONTEXT.md`、架构、实施计划与文档索引；新增仓库边界和上游管理说明；统一 SG 4 vCPU / 7.8 GB、原型已归档、OSS 不在 MVP 的现状。
- 剩余限制：本地 Docker 默认运行时的成功不替代目标 Linux/runsc 验收；生产镜像发布、资源压测、强杀后的恢复时限、备份故障域与告警仍归 S09。当前仅支持有界 JSON 输入与文本产物；二进制/流式传输和标准 quant 镜像入口待后续。正式 campaign 的数据授权、总体/GICS、模型 manifest 与人工 release 批准仍保持原阻断状态。

### S06b 进度（2026-09-28）

- 状态：**S06 第二纵切片完成**（隔离开发环境验收；正式 campaign 启动项仍阻断，见 S06a 待办）。
- 交付：**供应商更正自动触发**——调度器扫描已 resolved 且 exit 已过的 case：候选 SQL（窗口内两证券存在 ingested/usable 晚于 head.recorded_at 且现已可用的观测）加精确证据比对（head 冻结快照的 (security, date, raw_object_id) 集合 vs 当前 PIT 选择），仅在证据确实变化时重解析并追加更正 revision（correction_reason 默认 data_revision）；无变化不重冻结不产生快照垃圾；unscorable 粘滞；窗口外新数据不触发。**月度汇总报告**（`ledger/monthly.py`）——`monthly_summary_reports` 只追加版本链（unique(campaign, month, version) + supersedes 恒指头 + 首版无父 + append-only 触发器）：按批次 cutoff 月份归属，批次等权聚合已登记批次 D20 点估计（手算验收 0 与 0.11 → 0.055）；无点估计批次（未成熟/无可配对 case/漏跑补记）NA 不稀释均值；披露证券数、计划批次数、可评分批次数、成熟标签数与逐批 D20 报告引用、case/commit/outcome head 引用、成熟/未成熟；调度默认固定为次月首个常规交易日 06:00 ET（假日顺延与 DST 已知答案验证：2026-09 → 10-01 10:00Z、2026-12 → 2027-01-04 11:00Z）；月末前 NotReady；后续成熟/更正追加新版本且旧版本引用不变；同状态幂等重生成。**tick 规模化门控**——`batch_report_input_state`/`monthly_report_input_state` 可变缓存表（显式非 ledger、无触发器）记录上次生成时的输入 digest（cases/heads/commits/confirmations/成熟向量 + 评分版本盐）：输入未变且报告存在则跳过重生成，历史批次不再每 tick 全量重哈希；NotReady 尝试同样记录 digest，解除阻塞的输入变化（head 出现、exit 到期）在后续 tick 重新触发。**归档**——月报表纳入 campaign 归档导出与免库自证（内容 hash 重算校验）。migration `a3b4c5d6e7f8`。
- 验收：13 个新测试（pipeline 5：更正触发/无新数据不动且不重冻结/unscorable 粘滞/窗口外忽略/digest 门控一次重生成后复跳过；monthly 8：批次等权手算聚合、NA 不稀释、NotReady 与无批次月拒绝、due_at 假日+DST 已知答案、幂等+append-only、更正追加版本且旧版引用不变、tick 到期生成+门控复用）；归档导出与自证含月报。迁移实测：upgrade/downgrade/upgrade、月报表 2 触发器、状态表零触发器。`uv run --frozen pytest -q` → 229 通过。
- 待办：正式 campaign 启动项不变（见 S06a）；Phase 1B fallback 位置的评估区分待 S07；区块自举参数登记待样本条件满足；月报查询 API 随 S09/S10 界面工作接入。

### S05e 进度（2026-09-28）

- 状态：**S05 追加切片完成**（隔离开发环境验收；填补 S05 遗留的 produced 证据强制引用，服务 S04 验收线“任何模型输入均能追溯到截止时间内的数据或冻结规则”）。
- 背景：S06a 的 batch_predict 虽将证据快照记入 commit 的 input_manifest，但逐 prediction 的 evidence_snapshot_id 全为 NULL（原测试断言集合为 {None} 空转通过）——模型输入的逐源追溯实际缺失。
- 交付：**pipeline 逐源证据引用**——quant 位置携带批次共享证据快照 id（unavailable 也携带：快照记录使其无法运行的数据缺失事实）；常量基线不消费证据、如实保持 NULL。**封存边界证据纪律**（围栏/窗口检查之后、写入之前；幂等重放不重复校验）：produced 的 quant_model/llm_adjusted 必须引用冻结证据（拒绝不可追溯输入）；引用的快照必须存在、mode=forward（historical_source 是重建非证据）且 as_of <= 该 case 截止时间（截止后冻结的证据不得入账）。**DB 级约束**——predictions 表 CHECK：produced 消耗型来源必须携带 evidence_snapshot_id（直接 INSERT 同样受限）。migration `b4c5d6e7f8a9`（含既有数据预检查查询；当前无生产数据，开发库迁移前需确认预检查为 0）。
- 验收：5 个新测试（证据引用落地与基线诚实 NULL、无证据 produced 拒绝、截止后冻结证据拒绝、historical_source 证据拒绝、DB 约束反例+正例）；修复原空转断言（逐源真实断言）；归档对象引用恢复校验从 2 扩至 4（quant 证据快照纳入）；重放幂等保持（fixture 证据按 case 缓存，与真实管线同一快照语义一致）。迁移实测 upgrade/downgrade/upgrade、约束在位。`uv run --frozen pytest -q` → 234 通过。
- 待办：Phase 1B llm_adjusted 启用时同一纪律自动生效（校验已覆盖）；fallback 位置的证据语义（引用 quant prediction）随 S07 设计。

### S04c 进度（2026-09-28）

- 状态：**S04 第三纵切片完成**（隔离开发环境验收；独立退市真相源与多源扩展仍待补源采购决策）。
- 交付：**训练 manifest 登记**（`ledger/training.py` + `training_manifests` 表，append-only 触发器同 research_releases）——manifest 结构校验：至少一个特征集（逐特征 name/kind/definition/missing_policy，缺失政策显式）与一个模型（role/model_version/artifact{kind, ref, 64-hex sha} + 五项固定事实 preprocessing/fitting_window/calibration/label_maturation/feature_set，none 需明说不可默认）；模型 feature_set 必须解析到已声明特征集；登记幂等（同 id 同内容返原行）同 id 异内容冲突；内容 hash 自排斥且与键序无关。**载具 manifest 已知答案**（`vehicle_training_manifest`）：baseline-constant-v0 / quant-momentum-v0 的 artifact hash 锁定 quant/models.py 实际字节（改代码不改登记可被检测），特征集 momentum-20d-v0 含显式缺失政策（不足 21 根 bar → unavailable/insufficient_history，不伪造）。**campaign 注册接线**：release manifest 的 training_manifest_ref + sha256 必须解析到已登记内容，feature_set_version 必须为该 manifest 声明、baseline_version/quant_model_version 必须与其模型一致；无引用的 release 照旧注册（向后兼容）。migration `c5d6e7f8a9b0`。
- 验收：6 个新测试（登记幂等/冲突、结构校验 11 组反例全部拒收且零落库、内容 hash 自排斥+键序无关、载具已知答案含 artifact hash 对照实际文件、append-only 触发器、campaign 接线 6 路径：有效/未知 ref/hash 不匹配/特征集未声明/模型版本不一致/缺 hash 字段）。迁移实测 upgrade/downgrade/upgrade、2 触发器在位。`uv run --frozen pytest -q` → 240 通过。
- 待办：正式模型选定后登记其 manifest（Trial 登记与人工 release 批准归人）；载具 manifest 的正式登记（tm-vehicles-v0）随首个正式 release 起执行；派生特征（consensus/IV 等外部特征依赖）待补源后随特征集登记扩展。

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
