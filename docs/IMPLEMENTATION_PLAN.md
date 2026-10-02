# v0.3 修复与实施计划

日期：2026-09-27；结构优化更新：2026-09-28\
设计依据：[ARCHITECTURE.md](ARCHITECTURE.md)\
问题来源：[v0.2 评审](ARCHITECTURE_REVIEW_v0.2.md)\
状态：**Phase 1A 前向实验已批准并注册，运行中**——所有者 2026-10-02 批准（package r1）；campaign `phase1a-pilot-2026q4`（`a63f8494-4730-4cc7-bf2d-0b29e895aa3c`）active，12 批 cutoff 已入库，首个 cutoff **2026-10-10 06:00 ET**（批次于 10-03 06:00 ET 后由调度器预注册）——S00–S06 完成（Phase 1A 批次运行中）；S07 收尾（真实网关研究链路验证完成，聊天栈上线；Phase 1B 未启用）；S09a/S09b 完成；S09c 实质验收完成（磁盘治理、重启/回滚 drill、备份接入生产、告警轮询、整机重启演练 RTO≈3min；剩余 webhook URL 与负载复测按所有者决策 2026-10-02 搁置）。**下一步：S08c Controller 接线代码与测试完成（见 S08c 进度；隔离 mock 验收待执行）→ 隔离环境 mock 验收（网络探针 + HTTP 越权实测）；聊天栈备份已确认（每日，7 日备+4 周备，含一次隔离恢复验证）待实施；批次 1 预注册核查 2026-10-03 18:00（北京时间）后执行，cutoff 2026-10-10 06:00 ET。** 阶段详情见 [§4 完成记录](#4-完成记录)；S08（受控量化探索）已决策为 Hermes 唯一框架、Pi 暂缓。

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
  → S08 受控量化探索闭环（Hermes）
  → S09 国内入口与完整 MVP 验收
  → S10 评估界面、Memory 与审批
  → S11 前向候选验证与人工发布
```

S03 与 S04 在 S02 的身份、任务和对象契约确定后可并行；S09 的界面可提前开发，正式联调依赖持久事件与研究结果契约。S08 与 S09 可并行。无论如何拆工，后续功能不能绕过前置的权限、预算、截止时间或提交检查。

## 当前进度速览与下一步（2026-10-02 更新）

| 阶段 | 状态 | 关键事实 |
| --- | --- | --- |
| S00–S05 | 完成 | 协议定稿；持久任务/权限；沙箱+产物链路；PIT/快照；Ledger/Outcome/评分/归档 |
| S06 | 已批准、campaign 运行中 | 真实 panel/日历/SPY 冻结；Logistic/Ridge 候选 + Trial（D20 未显示增量）；release `bdb8bbe0…` 获所有者批准（2026-10-02 package r1）；campaign `phase1a-pilot-2026q4` active、12 批 cutoff 入库、首批 cutoff `2026-10-10`（批次 1 于 10-03 06:00 ET 后预注册） |
| S07 | 纵切片完成、Phase 1B 未启用 | Hermes 研究契约/进程边界/平台工具/无头镜像；Ed25519 跨容器接线（S07m）；真实网关研究链路验证完成（S07n：工具调用/取消/限额/fail-closed 全实测，发现修复 6 缺陷含 Runner 容器泄漏）；聊天栈上线；预算维度已删除 |
| S08 | 设计已确认 + Runner 工具面 + Controller 接线落地 | 往返契约设计经所有者确认（D1=A/D2=A+五项最小要求）；`experiment-v1` 契约 + Runner 持久回执存储与工具/控制端点已落地（S08b）；S08c Controller 接线完成：Core 登记/接纳表 + 编排器 + 实验实例派发（Runner `/v1/experiment-invocations` + agent-runtime `experiment-once` + youwei-experiment 工具集）+ 研究重入 + 取消传播，全量测试通过；隔离 mock 验收（网络探针 + HTTP 越权实测）待执行 |
| S09a | 完成 | 采集调度 collect_tick（16 测试）+ 生产 Compose（去 Runner）+ Core 镜像发布 GHCR + deployment 校验 VALID |
| **S09b** | 完成 | 运行接线闭环：隔离验收（合成 12/12 + 真实 45/45）、生产库准备（tenant `f497c122…`/计划 hash 固定）、GHCR 镜像发布（r2 digest `53631663…`，含 token 日志缺陷修复）、生产 API/Worker/采集上线（105 观测、21 任务 succeeded、deployment 校验 VALID） |
| **S09c** | 基本完成（两项搁置） | 磁盘 82%→14%、docker 级重启恢复、r2→r1→r2 回滚 drill、备份接入生产（pgBackRest 同机：WAL 归档 + 每日全量 + wal_archive 监控生效）、告警轮询上线（多格式 webhook，URL 待填）、整机重启演练 RTO≈3min；所有者决策：异地备份与负载复测搁置 |
| S10–S11 | 未开始 | 评估界面/Memory/审批；候选验证与发布 |

**当前运行状态（批准链已走完）：** release `bdb8bbe0…` 已登记批准（approver `human-owner`、scope `0b899b00…`，执行记录见 [§4 完成记录](#4-完成记录)）；campaign `phase1a-pilot-2026q4`（`a63f8494-…`）active，计划 hash `d403c8e5…` 服务端重算校验通过，12 批 planned_cutoffs 入库。批次 1（cutoff 2026-10-10 06:00 ET）由调度器于 2026-10-03 06:00 ET 后自动预注册；特征历史已回补（86 交易日 × 21 对象）且 60/60 可评分。tenant（`f497c122…`）与生产环境（core r2 `53631663…`、postgres `8d69232c…`）已固定，ops status 无告警。

**下一步动作（按序）：** ① S08 隔离环境 mock 验收（网络探针 + HTTP 越权实测；Controller 接线已完成，见 S08c 进度）；② 聊天栈备份（所有者已确认 2026-10-02：每日 1 次保留 7 日备+4 周备；LiteLLM PG 备份 + Open WebUI 库/附件/配置（SQLite 需一致性备份）；密钥单独受控；失败入现有监控；一次隔离恢复验证；异地备份维持暂缓并如实记录同机风险）；③ 批次 1 预注册核查（2026-10-03 18:00 北京时间后，只读四项：正确 campaign 1 批次/60 case/窗口正确/无重复）并在 2026-10-10 06:00 ET cutoff 窗口确认预测封存与及时确认。

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
- [x] Pi 工具和 RPC 管理命令白名单依据；资源自动加载、技能及扩展固定为可信部署内容（命令全集与 flags 已固定于 [runtime-version-pinning](research/runtime-version-pinning.md)；Pi 暂缓接入，wrapper 不设于当前 MVP）。
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
- [ ] 标准 quant 使用已发布镜像入口，不启动 Agent 模型循环。（进展：sandbox.execute 从固定镜像执行作业脚本，不启动任何 Agent；受控探索闭环归 S08，Pi 暂缓接入）
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

- [ ] 发布固定 baseline 与简单量化模型，登记训练与校准版本。（进展：管线载具 baseline-constant-v0 / quant-momentum-v0 已随 S06a 落地并全量披露；Logistic/Ridge候选实现及真实Trial已完成（S06h），主D20未显示增量；候选 release 已重生成 `release-logistic-ridge-candidate-20261002-v1`（hash `bdb8bbe0...`，S06i 因 pipeline.py/uv.lock 变更重注册）；正式 release 已获所有者批准（2026-10-02，package r1）并随 campaign 注册生效）
- [ ] 人工批准初始 release，按已登记规则固定20证券 panel，事前登记每批60个 case。（进展：所有者 2026-10-02 批准 package r1；campaign `phase1a-pilot-2026q4`（`a63f8494-…`）已注册 active——`campaign_plan_sha256`=`d403c8e5…`、`scope_manifest_sha256`=`0b899b00…`（生产 tenant `f497c122…`）；eodhd_sector 的 20 证券 panel、完整恢复包、日历和 SPY 已实际冻结（S06h）；12 批 planned_cutoffs 已入库，批次 1（60 case）于 2026-10-03 06:00 ET 后由调度器自动预注册——首批落地后本项关闭）
- [ ] llm_adjusted 保留 unavailable/not_enabled，不填充伪造 LLM 结果。（管线已固定封存该位置，S06a 验收）
- [ ] Scheduler 按周创建新批次，按交易日检查到期 Outcome。（已落地：见 S06a/S06b 进度；已 resolved Outcome 的供应商更正自动触发已于 S06b 落地）
- [ ] 最小只读查询显示计划数、完成数、缺失、迟到、数据质量和评分适用范围。（已落地：campaign_status 服务 + 租户隔离 API，见 S06a 进度）

交付：实际预测记录、定时任务及首批回填结果。

验收：从真实运行时刻开始积累；正式结果只使用已成熟标签。首批标签未到期时可标记“预测管线运行中”，完整闭环验收需等待真实回填。

### S07 — 接入 Hermes 与三组预测（Phase 1B；依赖 S06 的预测管线验收）

首次真实批次成功封存、任务与数据检查通过后即可开展本项，不要求等待全部标签成熟；Phase 1A 的完整结果闭环仍需实际回填验收。

该条件约束正式 Phase 1B 运行；契约、Controller 接线、候选研究角色/prompt/反证步骤及离线测试可提前准备。用于选择 prompt/模型的比较在 Trial 中事前登记，实际用于正式研究须绑定具体 release 并由人类批准；不把“不得自行批准”解释为“不得编写候选”。真实网关、费用和权限未验收时，测试替身的结果只记工程验证。

- [ ] 将 Controller 的冻结计划/证据转换为 Hermes 输入；每个 run 独立上下文。
- [ ] 在 `services/agent-runtime/` 建立独立 Hermes Python 3.14 环境，固定上游与依赖；实现 FrozenEvidence → ResearchProposal 契约及权限、取消、升级兼容性测试。
- [ ] 先使用一个研究综合角色及固定反证步骤；按实际收益再扩展并行角色。
- [ ] 研究引用可定位原文，warnings、缺失、量化依据、实际模型和成本完整输出。（S07e 已实现冻结快照行解析及 Runtime 预检；新闻/SEC 原文定位、Controller 接收与真实成本仍待后续）
- [ ] Hermes 返回 Proposal；Controller 校验并封存，Agent 无权写 Ledger 或改变 Lesson。
- [ ] prompt 回归验证结构、引用、权限与成本，记录新的 release。
- [ ] 从新 campaign 启用 llm_adjusted；按预登记政策记录失败/回退，不回补历史 LLM 预测。

交付：完整研究报告与同 case 的三组前向输出。

验收：故障后可恢复；同批三组共享目标和证据版本；生成截止后到达的结果不能进入准时评估。固定输入可复查，不要求外部 LLM 重跑逐字一致。

### S08 — 受控量化探索闭环（Hermes）（Phase 1B；依赖 S03、S07）

> 2026-10-01 决策：MVP 暂缓接入 Pi，Hermes 作为唯一 Agent 框架；研究角色与实验角色由独立 Hermes 实例实现。依据 [hermes-only-runtime-assessment](research/hermes-only-runtime-assessment.md)。Pi 保留为可替换的实验 Agent 适配器候选，版本锁不变，仅在 Hermes 暴露具体缺口时经预登记比较后再评估接入。

- [ ] **设计「研究请求 → Controller 授权 → 执行 → 产物引用」的往返契约**：Hermes 研究实例经 Controller 受控接口申请计算、查询状态、读取产物；提交、状态查询、产物读取分别授权；标准 quant 不经 Agent 模型循环，artifact_read 为读取操作；不预先承诺必须实现 quant_run/sandbox_submit/sandbox_status/artifact_read 这四个名称，按一个完整探索用例决定工具接口（S07 收敛决定）。
- [ ] 库未覆盖时，Controller 启动独立 Hermes 实验实例，输入冻结快照说明与明确问题；生成代码作为不可信作业输入，交 Sandbox Runner 执行。研究/实验实例不共享可自动演化的记忆。
- [ ] 固定 Job/Artifact 契约，覆盖取消、partial、timeout、warnings、代码与环境引用；提交、状态查询、产物读取分别授权。
- [ ] 实验输出由研究实例消费，再返回 Proposal；Controller 继续掌握任务状态、租约、截止时间与正式封存。
- [ ] 选择一个 quant 库未覆盖的探索问题，生成代码后在沙箱执行，完成从 Hermes 提议到沙箱结果、研究引用的完整样例。
- [ ] 实验候选登记 trial；禁止把生成代码热加载为 Extension 或生产 quant 库。

交付：从 Hermes 研究提议到实验实例、沙箱执行、结果回收、研究引用的完整样例。

### S08a 进度（2026-10-02，设计定稿 + 契约首切片）

- 状态：**往返契约设计完成（两个决策点待所有者确认）+ `experiment-v1` wire 契约与授权扩展落地**。设计文档：[s08-exploration-loop-design](research/s08-exploration-loop-design.md)——完整探索用例（滚动波动率比/分位数）驱动接口；编排 = 研究→实验（独立实例、无共享记忆）→沙箱→引用→研究重入；不变式与切片计划在文档内。
- 待所有者确认（改变隔离/权限语义）：**D1 工具通路**（建议：批准出口扩为 {网关, Runner 工具端点}，与研究网关接线同模式；备选 Unix socket 侧信道保持仅网关出口）；**D2 计算执行归属**（建议：Runner 按实验键直接执行 + Controller 统一登记 append-only 实验记录；备选每次计算走完整 Core job）。
- 交付（不依赖 D1/D2 的部分）：
  - `contracts/src/youwei_contracts/experiment.py`（新）：`ExperimentRequest`（研究实例的提问，有界声明式，无代码）、`ExperimentComputationRequest`（代码/argv/SBX_* env/期望扩展/超时，**无快照字段——Runner 注入**，边界对齐 sandbox-v1）、`ArtifactManifest`（无内容的产物身份）、`ExperimentComputationStatus`（running/succeeded/failed/cancelled/timeout + partial 语义：终态失败且已有产物才可标 partial）、`ExperimentResult`（findings/warnings/computations，逐项 code_sha256 一致性、去重、终态约束）、`experiment_artifact_locator`/`parse_experiment_locator`（`experiment:<uuid>/artifacts/<path>` 引用形态）。
  - `research_capability.py`：新增 `AUD_RUNNER_TOOLS` audience 与 `experiment:submit/status/read` 三 scope（提交/状态/读取分别授权）；旧 audience/令牌行为不变（回归测试覆盖）。
  - `sandbox.py`：argv/env 规则提取为共享 `check_argv_env`（sandbox-v1 与 experiment-v1 同源），新增 `ARTIFACT_EXTENSIONS` 常量。
- 验证：本机 `tests/pure`+`tests/contracts`+`tests/known_answers` → **209 passed**（新增 `test_experiment.py` 22 项：locator 往返/拒绝、请求边界（代码/env/argv/扩展/超时/禁额外字段）、partial 一致性（终态才可 partial、partial 必有产物）、结果 code hash 一致/去重/禁 running 条目、runner-tools audience 往返 + 旧 audience 回归）；agent-runtime 3.14 → **53 passed** 无回归（contracts editable 安装直接生效）。
- 剩余限制：Runner 工具端点、Controller 编排（实验派发/记录/研究重入）、端到端样例与 Trial 登记归后续切片，待 D1/D2 确认后按设计文档 §6 顺序推进。

### S08b 进度（2026-10-02，所有者确认 D1/D2 + Runner 工具面与持久回执）

- **所有者确认（2026-10-02）**：D1=A（实验容器=网关+专用实验工具入口；研究容器维持现状；沙箱无网络无密钥无令牌；网络探针 + HTTP 越权测试共同验证，实验令牌不能调 Runner 控制接口）；D2=A+五项最小要求（①派发前登记绑定+限额（次数/并发/累计时长/产物大小）②持久幂等（Runner 重启后仍成立，内存字典不足）③取消传播（父任务取消/租约失效/attempt 更替→拒新增+清理容器；最终提交再验 fencing）④可信执行证据（回执核验代码/镜像/快照/产物 hash；失败超时中断留痕）⑤产物引用唯一（locator 含 computation ID））。聊天栈备份（每日，7 日备+4 周备，SQLite 一致性备份，密钥受控，失败入监控，一次隔离恢复验证，异地维持暂缓）与批次 1 预注册核查（2026-10-03 18:00 北京时间后，只读：正确 campaign 1 批次/60 case/窗口正确/无重复）同期确认。
- 契约补齐（`experiment.py`）：`ExperimentAuthorization`（绑定 tenant/run/job/attempt(含 attempt_id)/case + evidence hash + 执行配置 + `ExperimentLimits`（次数/并发/累计时长/产物大小）+ **快照（Runner 注入源）**）；`ExperimentComputationReceipt`（控制面证据：request/code/image/snapshot hash + 终态 + 产物清单 + 时长，partial 一致性同 status）；locator 改为 `experiment:<id>/computations/<computation_id>/artifacts/<path>`（跨计算同名文件不冲突）；`SCOPE_EXPERIMENT_ADMIN` 控制面 scope。
- Runner 侧交付：
  - `youwei_runner/experiment_store.py`（新）：持久存储——authorizations/receipts JSONL 追加（逐行 fsync，加载时同键末行胜出）、产物内容寻址文件（写入前验 size+hash、读取时重验、路径穿越拒绝、同路径异内容拒绝）；**重启语义：加载时 running 回执改写为 failed(interrupted) 并落盘，同 id+payload 幂等重放返回中断回执，绝不静默重执行**；登记幂等（同内容 no-op/异内容 409）。
  - `app.py` 实验面：控制面 `POST/DELETE /v1/experiment-authorizations`（aud=runner-exec + experiment:admin；登记绑定校验；终止=拒新增+取消在运行任务+**兜底回执更新**（任务未启动即被取消的窗口））与 `GET .../receipts`（证据回执）；工具面 `POST /v1/experiment-computations`（aud=runner-tools + experiment:submit；限额四项 + 全局并发 + 幂等/冲突）、`GET .../{ids}`（status）、`GET .../artifacts/{path}`（read，回执内路径才可读，内容 hash 重验）；执行任务复用沙箱执行器（合成 SandboxRequest，**快照由 Runner 从授权注入**），exit≠0 带产物标 partial，超时/取消/异常均落回执；`settings.experiment_store_dir`（空=入口 503）。
  - timeout 回执无产物（现行沙箱执行器超时路径不回收产物 tar）——timeout-partial 留作执行器后续增强，不阻塞本片。
- 验证：`tests/pure/test_experiment_store.py` 11 项（登记幂等/终止持久/回执重启存活/中断改写二次加载一致/末行胜出/产物往返+篡改检出/路径穿越拒绝/同路径异内容拒绝）；`tests/contracts/test_experiment_runner_http.py` 9 项（假执行器全链：登记→提交→status→read→回执证据；幂等重放/异 payload 409；限额：次数/并发/时长预算；终止：在运行任务 cancelled + 新提交 403；**重启存活：新 app 同目录重载，中断回执幂等重放**；越权矩阵：工具令牌×/v1/executions 403、工具令牌×/v1/research-invocations 403、控制令牌×工具端点 403、scope 缺失 403、跨实验绑定 403、未登记 404；未配置 503）。本机全量 **230 passed**；agent-runtime **53 passed** 无回归。
- 剩余：Controller 接线（S08c：派发前 Core 登记 + Runner 授权登记调用、实验实例派发（youwei-experiment toolset + 工具令牌签发/续期）、回执核验+fencing 再校验接纳、研究重入编排、父任务取消/租约失效/attempt 更替→terminate）；隔离环境 mock 验收（含网络探针扩展与 HTTP 越权实测）；真实快照接线后端到端样例与 Trial 登记；聊天栈备份与批次 1 核查按所有者指示另行推进。

验收：本地 shell、RPC 管理命令、加载扩展、跨作业文件请求均无法突破允许范围；标准 quant 任务继续直接执行库函数；Hermes 内置 `execute_code` 不得替代 gVisor Sandbox Runner——研究/实验实例维持明确工具允许列表，生成代码一律经 Controller 授权进入 Runner。

### S08c 进度（2026-10-03，Controller 接线：登记/接纳 + 实验实例派发 + 研究重入）

- 状态：**Controller 接线代码与隔离开发测试完成**（按所有者确认的推进顺序，设计修订与 Runner 工具端点之后的第一步实现；**隔离环境 mock 验收——网络探针 + HTTP 越权实测——待执行**，真实快照接线后端到端样例与 Trial 登记归后续）。
- 契约补齐（`youwei_contracts`）：
  - `research_capability.py`：新 audience `runtime-experiment`（Controller → 实验容器授权）与新 scope `experiment:run`（派发，aud=runner-exec；容器授权同 scope，镜像 research:run 双用法）+ `experiment:run_status`（轮询派发状态）。
  - `agent_runtime.py`：`ResearchInvocationResult` 携带 proposal **XOR** `experiment_request`（互斥校验）；`ResearchInvocationRequest` 增 `experiments`（重入回合携带已接纳实验结果，上限 4）。
  - `experiment.py`：实验实例派发 wire（`ExperimentInvocationRequest/Result/Status/Envelope` + digest）与重入上下文（`ExperimentContext/ExperimentContextComputation` + `experiment_context_from_result`——只携带身份/hash/manifest，不携带代码体，ledger 持有代码）。
  - `research.py`：`validate_proposal_references` 接受可选 `experiments`——kind="code" 引用解析到所携实验的 artifact manifest（无实验时仍拒绝）；新增 `resolve_experiment_reference`。
- Core 侧交付：
  - 迁移 `a1b2c3d4e5f6`（upgrade/downgrade/upgrade 实测，6 触发器在位）：`experiment_records`（派发前登记：绑定 tenant/run/job/attempt/case + 证据 hash + 问题 + 限额 + 快照引用；append-only）与 `experiment_outcomes`（接纳：result + 回执 + 镜像 + 快照 hash + fencing 尝试；append-only）。同步 meta 与测试清表清单。
  - `youwei_core/ledger/experiment_records.py`：`register_experiment`（幂等/冲突、fencing（当前运行尝试+存活租约+deadline 检查）、逐 case 上限 K=2、跨租户拒绝、快照存在性）、`accept_experiment`（纯逻辑回执↔结果核验：逐计算 code hash/终态/artifact manifest 全等、快照 hash 一致、单一镜像、无 running 残留；fencing 写边界再校验且必须是登记尝试；幂等/冲突）、`verify_experiment_evidence`/`resolve_experiment_reference`/`resolve_experiment_artifact`（locator → 已接纳回执内的 manifest）。
  - `youwei_core/ledger/experiment_client.py`：控制面客户端（登记/终止/回执，逐次活跃租约重签）+ `sign_experiment_dispatch_tokens`（runner-exec 派发 / runtime-experiment 容器 / runner-tools 工具三令牌；工具令牌 exp = 派发 + 限额时长 + 300s 裕量，作为 terminate 之后的纵深）+ `run_experiment_instance`（提交→轮询至终态）。
  - `youwei_core/ledger/experiment_orchestrator.py`：探索循环编排——研究回合返回实验请求时：Core 登记 → Runner 授权登记 → 实例派发 → 回执取回 → 接纳核验 → `ExperimentContext` 重入下一回合；逐 case 上限自然终止（CapReached → unavailable）；任何失败/取消（含 CancelledError）后 best-effort Runner terminate（拒新增+清容器）再传播；`active_lease_expiry` 从 attempts 表重读存活租约（心跳保活，修复 claim-time 快照在多回合长 case 上的陈旧问题）；最终 proposal 的 kind="code" 引用对已接纳集合再校验。
  - `pipeline.py`/`worker/loop.py`/`config.py`：`RunnerResearchConfig` 增 `experiment_client`+`experiment_limits`（同生同灭），`experiment_exploration_enabled` + 四项限额模板设置；Phase 1B 且接线存在时 fetcher 走编排器；顺手修复既有潜伏缺陷——pipeline 引用 `signing_key` 但类字段名为 `key`（Phase 1B 未启用故未触发），并清理 research_client 中重复的类定义。
- Runner 侧交付：`experiment_instance.py`（镜像 research.py 纪律：固定镜像、非 root 只读、cap-drop、资源限额、超时按容器名强杀、字节上限、入口 `experiment-once`；**网络 = `youwei-experiment`（网关 + 本 Runner 工具端点，D1=A；研究容器维持 youwei-research 仅网关）**；stdin wire 携带完整 request（绑定字段供容器验签）+ Runner 注入的网关与工具端点，快照绝不入 wire）；`app.py` 新增 `/v1/experiment-invocations`（submit：aud=runner-exec + experiment:run + 绑定校验 + 登记前置 + 幂等/409 + 容量；status：experiment:run_status；terminate 传播：终止实验时同时取消运行中的实例派发任务）。
- agent-runtime 侧交付：`experiment_tools.py`（`youwei-experiment` 工具集：sandbox_submit/sandbox_status/artifact_read 为 HTTP 工具（同步 httpx），computation_id 由工具生成、experiment_invocation_id 取自上下文（模型不可遗）；令牌逐调用重验（audience/scope/绑定）；HTTP 错误作为工具输出返回模型（可自适应），本地授权失败抛错；**ComputationJournal 记录实际提交的代码与末次观察状态——最终 `ExperimentResult.computations` 由日志组装而非模型自报**，running 条目不入结果（仅回执侧体现））；`experiment.py`（确定性实验简报：问题 + 快照 manifest（内容绝不给实例）+ 沙箱契约 + 输出格式；findings 解析（JSON/围栏容忍）；`run_experiment` 隔离构造 + 回合后从日志组装结果；wire codec：runtime-experiment 授权验签（绑定全量比对）+ 编码）；`main.py` 新增 `experiment-once` 入口（stdout 纪律同 research-once：banner 转 stderr、stdin/stdout 字节上限）；httpx 入依赖（0.28.x）。
- 验证：
  - 本机（3.13）：`tests/pure + tests/contracts + tests/known_answers` → **288 passed**（新增：experiment_evidence 19、experiment_client 8、experiment_orchestrator 6、experiment_invocation 契约 11、experiment_invocation_http 14、experiment_runner_http 回归 9）。
  - **SG 真实 PostgreSQL 全量：`.venv/bin/pytest -q` → 585 passed（含 test_experiment_records 20 项 DB 级 + test_experiment_orchestration 2 项 DB 级）**；迁移 `a1b2c3d4e5f6` 在一次性 PG 容器 upgrade/downgrade/upgrade 实测，6 触发器在位。
  - agent-runtime（3.14 自有环境）→ **81 passed**（新增 test_experiment_runtime 25 + test_main 增 3）；Runner 独立锁 `uv sync --no-dev` 通过、独立导入通过。
  - 修复过程中全量收集暴露既有问题：5 个测试文件 `from conftest import make_campaign_plan` 与 `tests/pure/conftest.py` 模块名冲突（违反“不直接导入 conftest”规则），全仓收集时 16 个文件报错——已将帮助函数移入 `tests/campaign_plan_helper.py` 并重指向（此为既有缺陷，与 S08 无关但阻断全量验证）。
- 剩余限制：隔离环境 mock 验收（`youwei-experiment` 网络探针：实验容器仅网关+Runner 工具端点、研究容器不可达 Runner；HTTP 越权实测：实验令牌 × 控制端点/研究入口、控制令牌 × 工具/派发端点）待执行；agent-runtime 镜像需重建发布（`experiment-once` 入口 + httpx 依赖）；Runner 生产部署需 `experiment_store_dir`/`experiment_tool_base_url`/`youwei-experiment` 网络接入配置（生产模式已强制校验）；实验实例结果 stdout 上限 1MB——超限如实失败（代码体大时可能触达，探索用例通常远小）；`ExperimentInvocationRequest.config` 的 model 由编排器从 research 配置传入，实验实例与研究生成的模型选择末级统一待 Phase 1B 启用时定稿。

### S09 — 完成国内入口与 MVP 运维验收（Phase 1B；依赖 S02、S07，可与 S08 并行）

- [ ] CN PostgreSQL 保存身份、成员关系、tasks、submission outbox 与 event cursor。
- [ ] 提交使用 mTLS/服务鉴权/租户幂等键；SG 是执行状态唯一权威。
- [ ] CN 拉取持久事件，在本地事务更新镜像与 cursor；浏览器通过 SSE 收进度。
- [ ] cursor 缺口/过期、跨境断线、请求超时能重新对账。
- [ ] Web 展示数据截止、来源、三组状态、数据质量、成本及授权允许的产物。
- [ ] 评估 Open WebUI 适配或复用归档 Next.js；界面只消费 Core 接口，固定上游版本并验证身份、任务与结果契约。
- [ ] 多用户开放前完成 RLS、对象下载、cache/session 越权测试。
- [ ] 真实数据恢复演练、资源压测与故障注入，记录实测 RPO/RTO。
- [ ] 使用独立构建镜像、完整 digest 与目标机验收报告生成部署清单；运行 `infra/validate_upstreams.py --mode deployment` 并验证升级/回滚兼容性。（进展：S09a 已生成 `infra/compose/production.json` + `infra/deployment-manifest.json`，Core 镜像发布 GHCR digest `sha256:7292c853...`、postgres digest `sha256:721873c3...`，`--mode deployment` → VALID；升级/回滚兼容性验证待 S09b/S09c；详见 S09a 进度）

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
| P1-3 | 标准 quant 绕过 Agent 模型循环 | S03、S06、S08 |
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
- 待办（S06 剩余，2026-09-28复核）：采集/抽样已随 S06d 跑通，分类方案 (a) 的候选 v2 与正式登记收尾见 S06e；仍需完整映射/源证据恢复及登记校验、生产数据许可确认、正式模型与 manifest 固定、实际 release 人工批准、预算配置。模型/特征比较按 Trial 规则登记；月度汇总、更正触发及报告门控已随 S06b 落地。

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

### S06c 进度（2026-09-28）

- 状态：**确定性抽样器的工程实现完成**（不代表正式 campaign 登记所需工程检查全部完成；后续复核见 S06e）。
- 交付：`quant/sampling.py`——S00 登记算法 sampling-json-v1（sector-stratified-hash-v1，seed=20260927，N=20）的首个仓库内实现：总体规范化（仅 security_id + sector_code、按 security_id 升序、重复拒绝、缺 PIT sector code 停止登记不静默删除）；配额（每层 1 名 + (N_s-1)/(M-K) 先取整再余数降序、同余按 sector code 升序；M=K=20 零分母分支）；层内按 SHA256(compact_JSON([sampler_version, seed, frame_hash, sector_code, security_id])) 升序取座、同 hash 按 security_id；compact JSON 严格按登记规则（数组序固定、对象键字典序、无空格、UTF-8、无 BOM/换行）；选中名单升序 + selected_list_sha256；draw manifest（算法事实，源版本/映射由数据层补充）。
- 验收：8 个新测试（**K01 已知答案全量复现**——配额 S01–S09×2/S10/S11×1、末两层选中 SEC-10-2/SEC-11-1、记录 hash 37421b62… 一次复算一致；行序不变性；compact JSON 规则；异余数分配手算对照 9/6/3/1/1；M=K=20 每层一名；M<20/缺分类/重复 id/K>20 四组拒绝；规范化只留协议字段；确定性 + manifest 形状）。纯计算无 PG 依赖。`uv run --frozen pytest -q` → 248 通过。
- 待办：成分快照到手后的接线——EODHD 帧构建（ticker→永久 ID 映射 + sector 归属）→ select_sample → panel_manifest 登记；GICS 协议修订决策仍待用户选择。

### S06d 进度（2026-09-28）

- 状态：**成分采集与 panel 抽样的开发链路完成**（真实数据冒烟通过；完整冻结登记包、映射恢复与登记闸门仍需 S06e 收尾，正式启动另需许可/预算/实际 release 批准）。
- 交付：**EODHD 成分采集器**（`data/eodhd.py`）——Marketplace 端点 `/api/mp/unicornbay/spglobal/comp/GSPC.INDX`（非标准 `/api/fundamentals/`，已实测确认）；严格解析（General/Components/HistoricalTickerComponents；缺 Sector 拒收）；不可变 raw_object 存储（内容 hash 去重、source_available_at/basis 证据模型同 Tiingo）；数据源登记（slug `eodhd`）。**panel 构建**（`data/panel.py`）——ticker→永久 security_id 映射（已在主数据则解析复用、新成员则 create_security）；跨源 ticker 归一化（EODHD `BRK-B` → Tiingo 约定 `BRK.B`）；帧规范化（仅 security_id+sector_code、升序、frame_hash）；`draw_panel` 调登记抽样器；`build_panel_manifest`（抽样器事实 + frame_as_of/index/源版本/raw_object 引用/ticker 映射，stratification_level 由调用方显式固定——GICS 命名决策待定）。
- 验收：10 个新测试（解析归一化与四组拒收、不可变存储去重、缺源时间 basis、caller_evidence basis（锁定 NOT NULL 回归）、HTTP 错误、ticker 归一化、帧映射/建证券/幂等、缺 sector 停抽、draw+manifest 形状）。**真实 token 端到端冒烟**（一次性容器）：采集 503 成分 + 822 历史成员 → 帧 11 板块（frame_sha256 `8e495fc7…`）→ seed 20260927 抽出 20 只（覆盖全部 11 板块，selected_list_sha256 `59ed17c5…`）。`uv run --frozen pytest -q` → 258 通过。
- 待办更新：推荐 `eodhd_sector` 的候选协议已于 S06e 起草；更改标签本身不足以正式登记。先完成 S06e 的源证据、映射恢复与登记校验，再绑定真实 panel/release；历史成员映射及分类、完整退市真相源仍未验收。

### S06e 分类方案与正式登记复核（2026-09-28）

- 选择建议：**方案 (a)**，首版按 EODHD 实际返回的 Sector 分层，名称 `eodhd_sector`。它能提供本项目所需的供应商板块覆盖；不要求先采购官方 GICS，也不宣称11个板块与 GICS 的规则或证券归属等价。未来改用官方分类时创建新协议/panel/campaign/release，不改写历史。
- 交付：候选 [campaign-policy.v2](protocols/campaign-policy.v2.md)及 [s00-registration.v2.json](protocols/s00-registration.v2.json)；同步领域术语、架构、协议索引和 EODHD 实测记录中的过强结论。v1及其 hash 保留。N=20、seed、配额算法、SPY、horizon、来源及评分规则保持原登记；候选文件不是 release 批准。
- 已确认工程缺口：`data/panel.py` 新证券使用随机 UUID、标识有效期硬填1990；同库幂等测试不证明新库恢复同一名单。`build_panel_manifest` 未核对 raw/frame/sample/time，`register_campaign` 仅校验其为字典。现阶段不能写“剩余全部是人工项”。

正式登记前的工程收尾：

- [x] **证券映射及恢复**：按有证据的时点建立标识有效期（valid_from = frame_as_of，依据 observed_in_sp500_constituents_at_frame_as_of，不再硬填 1990）；冻结完整 security_id 映射与规范化 frame（panel_registrations 表，append-only、内容寻址）。清洁环境恢复原映射后复算，同一 frame/名单 hash 一致；重建随机 UUID 后重抽不是恢复路径（恢复用冻结 UUID 幂等重建，并检测 ticker 归属冲突）。
- [x] **源与时间证据**：raw_object 保留原始内容/hash、observed_at/usable_at、来源缺失依据（S06d 采集器）；panel_registrations 冻结 frame_as_of/observed_at/usable_at/basis；登记闸门核对 frame_as_of 不得早于 usable_at（错时间拒绝）。分类快照以 Components[].Sector 原值冻结，不做 GICS 重命名/翻译/合并。
- [x] **正式登记闸门**：`validate_panel_registration` 从源 raw_object 重解析→冻结映射→重建 frame→重抽样，逐项核对源内容 hash、映射 hash、frame hash、配额、选中名单 hash、时间一致性、协议 hash 与调用方 panel_security_ids；register_campaign 在 panel_manifest 声明 panel_registration_id 时强制校验（错源/错时间/错 frame/错名单/错标签均拒绝）。

S06e 工程收尾完成记录（2026-09-28）：11 个新测试（标识有效期=frame_as_of 且零回溯、冻结→清洁恢复复算同 frame/名单 hash、恢复幂等、ticker 归属冲突检测、闸门正向、错源/错时间/错 frame/错名单/错标签五类拒绝、register_campaign 闸门三路径、append-only）。migration `d6e7f8a9b0c1`（upgrade/downgrade/upgrade、2 触发器）。`uv run --frozen pytest -q` → 269 通过。真实数据正式登记仍需：实际 panel 的完整冻结 + release hash 人工批准（本轮未执行）。

许可和批准准备：

- Phase 1A LLM 未启用，建议金额上限0并验证禁止模型调用；数据/主机预算另列。Phase 1B 才启用非零 LLM 预算。
- Phase 1A 确认数据源的计算、留存/备份及终止订阅处理权限；发送数据给 LLM 的授权在 Phase 1B 首次转发前取得，不把这一未来能力误列为 Phase 1A 的调用失败。
- 正式模型、training manifest、日历/tzdb、实际 panel 与政策引用准备完整后，生成具体 release hash 交项目所有者批准；Agent 不填写批准人、时间或放行标志。

本轮验证：抽样已知答案 **8 passed**；本轮未改 v1 文件字节，v2 的全部协议引用 hash 已核对。另发现 `0401fd4` 曾更新时间协议状态行却未同步 v1 登记 hash；时间规则未变，v2 已记录可恢复的原提交及前后 hash，不将历史引用问题报为通过。检查文档链接与差异格式；未运行真实供应商请求、未写正式数据库、未登记 panel 或启动 campaign。后续工程实现与测试通过后再逐项勾选。

### S06f 候选包准备（2026-10-01）

- 状态：**分类方案定稿 + 候选包准备完成**；工程侧推进到可审阅/批准，正式 campaign 未启动、无 release 批准。
- 交付：
  - 分类方案定稿：`campaign-policy.v2.md` 状态由「候选修订」改为「分类方案已确定（方案 (a) eodhd_sector），实际 release 待批准」；`s00-registration.v2.json` 的 `status` 改为 `classification_decided_release_pending_approval`、新增 `classification_decision`/`classification_decided_date`，并同步 `protocol_files` 里 campaign-policy.v2.md 的新 hash `245fbde1...`；README 同步。v1 与其 hash 保留，formal_campaign_allowed 保持 false，批准字段保持空。
  - 可执行准备入口：`ops/prepare_s06_campaign.py`——读 `EODHD_API_KEY`/`YOUWEI_DATABASE_URL` 与 `s00-registration.v2.json`（单一事实来源），串联 ingest→frame→draw→freeze→validate，输出候选包 JSON（registration_id、frame/mapping/selected hash、校验报告、remaining_missing_items）；不填批准字段、不注册 campaign、不绑定 release。
  - 模型候选说明：`docs/trials/model-candidates.md`——明确 baseline-constant-v0/quant-momentum-v0 是工程载具（动量直接映射概率、无拟合/校准/标签成熟），列出 baseline/quant 正式化候选方向与固定步骤（Trial→设计→历史验证→manifest→release 批准），不替所有者选模型。
  - 审阅材料：`docs/ops/s06-campaign-candidate-package.md`——汇总「已确定/待冻结/缺失项」三部分，明确 8 项阻断项与谁解除。
  - 测试：`tests/test_s06_prepare.py` 4 个纯逻辑测试（协议读取状态、hash 与文件一致、缺失项报告含/不含校验脏标记）。
- 验证：`tests/test_s06_prepare.py` 4 passed；`infra/validate_upstreams.py --mode catalog` VALID；v2 JSON 有效且 4 个协议文件 hash 与登记一致。
- 剩余限制：未在真实库跑准备入口（需 EODHD 许可确认后执行）；Tiingo 生产套餐 ToS、EODHD 限额/留存权限、正式模型选择、真实 Trial 登记、实际 panel 冻结、release 批准均待项目所有者逐项解除。

### S06g 模型规格 + Trial 预登记 + 许可拆分 + 引用固定（2026-10-01）

- 状态：**模型方向确定并 Trial 预登记、许可按阶段拆分、时间记录修正、tzdb 固定**；仍无 release 批准、正式 campaign 未启动。
- 交付：
  - 模型规格：`docs/trials/model-candidates.md` 更新——baseline 定为常量 `p=0.5`/`expected=0`；quant 定为 L2 正则化 Logistic Regression（少量价格特征，预测 D20 跑赢 SPY 的二分类标签），训练窗口/特征/预处理/正则化/标签成熟全部满足 PIT，期望收益单独建模（不从胜率反推）；`quant-momentum-v0` 保留为工程载具。
  - Trial 预登记：`docs/trials/registry.md` 登记 `trial-001-quant-lr-vs-baseline` 的 `registered` 事件（假设、主指标 paired Brier、D20 主/D1/D60 探索、purge/embargo、待固定参数标注 to_be_fixed_before_started、结果留空），event_hash `cb05a9c7...`；`s00-registration.v2.json` 的 `trial_count` 改为 1。
  - 许可拆分：`docs/ops/data-license-checklist.md`——按阶段拆 Phase 1A（内部计算/留存/备份/退订/限额）与 Phase 1B（LLM 转发）；含 Tiingo/EODHD 两家的套餐现状、待核实条款、证据位置、待询问供应商的草稿；明确「LLM 转发未取得不阻断 Phase 1A」。
  - 时间记录修正：`ops/prepare_s06_campaign.py` 的 `observed_at=None` 改为 `observed_at=ingest.usable_at`（系统实际采集时刻，与供应商生效时间 source_effective_at 区分）；`_remaining_missing` 移除「source_effective_at_unknown」阻断项，改入 `source_time_record`（事实记录、非阻断）；候选包文档 §3 拆「阻断项（6 项）」与「事实记录（非阻断）」。
  - tzdb 固定：`pyproject.toml` 加 `tzdata>=2026.4,<2027`，`uv.lock` 解析为 tzdata==2026.4（IANA 2026d）；`s00-registration.v2.json` 的 `tzdb_version` 填 `2026d`、新增 `calendar_rules_version=nyse-rules-v1` 与 `tzdata_package=tzdata==2026.4`。
  - 测试：`tests/test_s06_prepare.py` 更新（source_effective_at 从阻断项改为非阻断断言）。
- 验证：`tests/test_s06_prepare.py` 4 passed；`tests/test_data_calendar.py` 20 passed（tzdata 加入无回归）；`tzdb_version()` 返回 `2026d`；v2 JSON 有效；uv.lock revision 仍为 3（无漂移）。
- 剩余限制：日历 build 版本（年份范围 + 内容 hash）与 SPY 永久 ID 仍待冻结/采集时固定（技术方案已定，无需再决策）；tzdata 的目标机 NTP/时区验证归 S09；正式模型实现、Trial `started`/结果事件、实际 panel 冻结、release 批准均待后续。

### S07a 进度（2026-09-28）

- 状态：**S07 第一纵切片完成（环境搭建与 API 签名钉定）**；Hermes 研究规划/契约/权限接线仍待后续切片，正式启用依赖 S06 首次真实批次封存验收。
- 交付：`services/agent-runtime/` 独立包骨架（`youwei-agent-runtime`，Python 3.14，无 Core/Ledger/DB/供应商密钥依赖）；SG（sg-prod 168.144.39.34）上重建固定 Hermes 环境——`~/s01-verify/hermes314/`（S01 测试 clone，HEAD=`7fa45eb...`）原 `.venv` 软链接指向已删除的 `/usr/local/bin/python3.14`，用 uv 重装 Python 3.14.7 后 `uv sync --frozen --python 3.14`（Hermes 自己的 `uv.lock`）重建成功；`hermes --version` → `Hermes Agent v0.21.5+3129.g7fa45eb (2026.9.24)`，Python 3.14.7，venv 142MB。
- **关键架构结论**：Hermes **不可 pip 安装**（无 wheel/sdist；官方仅支持源码/editable、Docker、Nix，`uv sync` 触发 “Building wheels or sdists for hermes-agent is not supported”）。因此 `youwei-agent-runtime` 不把 `hermes-agent` 列为 pip 依赖，而是作为独立 checkout + 独立 venv 的外部运行时；适配层在运行时经 `PYTHONPATH` 指向其 `agent/` 包（实测 `import agent` 成功，无 `hermes_agent` 顶层包）或以子进程调用。已在 `pyproject.toml` 以注释明确此边界。
- **API 签名钉定（解决 runtime-version-pinning 待实测 1）**：`AIAgent.__init__`（`run_agent.py`）显式含 `enabled_toolsets`、`disabled_toolsets`、`skip_memory`、`skip_context_files`、`skip_background_review`、`provider`、`base_url`、`api_key`、`api_mode`、`model`、`platform`、`session_id`、`gateway_session_key`、`iteration_budget`、`run_budget_seconds` 等；`chat(message, stream_callback=None) -> str`（`agent/turn_facade.py` TurnFacadeMixin），内部 `run_conversation(...)["final_response"]`；构造经 `agent.agent_init.init_agent` 转发。
- 验证：SG 上 `hermes --version` 输出正确；`import agent` 成功；`.venv/bin/python --version` = 3.14.7；HEAD=`7fa45eb...`。
- 剩余限制：`services/agent-runtime` 骨架尚未在 SG 建立独立 venv（当前仅确认 Hermes 环境）；FrozenEvidence→ResearchProposal 契约、记忆隔离键运行时验证、研究工具白名单、Controller 校验封存、取消/权限/升级兼容性测试、独立 Docker 镜像构建与 digest 固定均归 S07 后续切片。本机 GitHub 网络不通，后续 Hermes 相关构建一律在 SG 执行。

### S07b 进度（2026-09-28）

- 状态：**S07 第二纵切片完成（FrozenEvidence→ResearchProposal 契约）**；Hermes Adapter 实现、Controller 接线、隔离验证仍待后续切片。
- 交付：`contracts/src/youwei_contracts/research.py`（`research-v1` 契约，无 DB/上游 SDK 依赖）——`FrozenEvidence`（`CasePlan` + `EvidenceSnapshot` + `target_policy_sha256` + `batch_manifest`；`EvidenceSnapshot` 强制 `content_sha256 == sha256(canonical_json(content))` 且 `mode` 仅 `forward`）；`ResearchProposal`（仅 `llm_adjusted` 位置，`source_status ∈ {produced, unavailable}`，produced 强制两值+attributable model，unavailable 强制 reason 且禁值，`p_outperform ∈ [0,1]`，`references`/`warnings`/`missing`/`quantitative_basis`/`model.cost_estimate`）+ `proposal_digest`。值域纪律与 `sealing.py SourcePrediction` 对齐（`SOURCE_STATUSES=(produced, fallback, unavailable)`，Hermes 不提出 fallback——那是 Controller 的 `phase1b-llm-from-quant` 降级政策）。
- 验收：`tests/contracts/test_research.py` 7 个新测试（hash 校验/篡改拒绝/extra 拒绝/produced 双值+model/unavailable reason+禁值/值域/digest 稳定）全部通过；`tests/contracts/` 全量 27 passed（原 20 + 新增 7）；`from youwei_contracts.research import ...` 在 Core 环境（3.13）与 agent-runtime 环境（3.14）均可导入。
- 剩余限制：Hermes Adapter（`AIAgent` 封装 + 记忆隔离键运行时验证 + 研究工具白名单）、Controller 端 `proposal → SourcePrediction` 封存接线、取消/权限/升级兼容性测试、独立 Docker 镜像构建与 digest 固定均归 S07 后续切片；`llm_adjusted` 从 `unavailable/not_enabled` 转为 `produced` 需 Phase 1B 启用（依赖 S06 首次真实批次封存验收 + 数据源 LLM 转发授权）。

### S07c 进度（2026-09-28）

- 状态：**S07 第三纵切片完成（adapter 确定性层 + 隔离键运行时验证）**；真实 `AIAgent` 研究调用、平台工具注册、Controller 接线仍待后续切片。
- 交付：`services/agent-runtime/src/youwei_agent_runtime/adapter.py`（无 Hermes 导入、仅依赖 `youwei-contracts` 的纯逻辑层）——`ISOLATION_KWARGS`（`skip_memory=True`/`skip_context_files=True`/`skip_background_review=True`/`enabled_toolsets=[]` 空白名单）、`RESEARCH_TOOLS`（平台研究工具白名单：snapshot_manifest/quant_run/sandbox_submit/sandbox_status/artifact_read，非 Hermes 内置工具集）、`build_research_brief`（FrozenEvidence→确定性研究简报，仅含冻结事实，禁止伪造）、`proposal_from_payload`（proposal 值域纪律边界）。`agent-runtime` 加 `youwei-contracts` 路径依赖 + `pytest` dev 组 + 自有 `[tool.pytest.ini_options]`。
- **隔离键运行时验证（解决 runtime-version-pinning 记忆隔离键待办）**：sg-prod 上以固定 commit `7fa45eb` 构造 `AIAgent(skip_memory=True, enabled_toolsets=[], skip_context_files=True, skip_background_review=True)`（不调用 LLM），实测 `_memory_store=None`、`_memory_enabled=False`、`_user_profile_enabled=False`、`valid_tool_names=[]`、`skip_context_files=True`、`skip_background_review=True`；构造日志明示 "No tools selected / No tools loaded"。确认 `enabled_toolsets=[]`（空列表非 None）是研究角色正确隔离面。
- 验收：`services/agent-runtime/tests/test_adapter.py` 6 个测试（隔离配置固化、brief 确定性、brief 仅冻结事实、零 bar 不伪造、proposal 构造/值域拒绝）全部通过，`uv run --frozen pytest -q`（agent-runtime 自有 3.14 环境）→ 6 passed。
- 剩余限制：真实 `AIAgent` 研究调用需 LLM 网关 + 预算（Phase 1B 启用，非零预算）；平台研究工具（snapshot_manifest 等）的注册与服务端授权尚未实现（需确认 Hermes 自定义工具注册机制 `ctx.register_tool`）；Controller 端 `proposal → SourcePrediction` 封存接线、取消/权限/升级兼容性测试、独立 Docker 镜像构建与 digest 固定均归 S07 后续切片。

### S07d 进度（2026-09-28）

- 状态：**S07 第四纵切片完成（零成本端到端冒烟）**；真实网关调用、平台工具注册、Controller 接线仍待后续切片。
- 交付：`services/agent-runtime/src/youwei_agent_runtime/runtime.py`（Hermes 运行时桥）——`ResearchConfig`（`base_url`/`api_key`/`model`/`provider=custom`/`max_iterations`/`run_budget_seconds`）、`make_agent`（构造隔离 `AIAgent`，应用 `ISOLATION_KWARGS`）、`parse_proposal`（解析模型文本响应为合法 `ResearchProposal`，容忍 markdown fence/嵌入 JSON）、`run_research`（`asyncio.to_thread` 包装同步 `chat()`，端到端 seam）；`services/agent-runtime/smoke/mock_gateway.py` + `smoke_e2e.py`（可复现零成本冒烟资产，路径经环境变量 `HERMES_CHECKOUT`/`CONTRACTS_SRC`/`AGENT_RUNTIME_SRC` 配置）。
- **关键实测发现**：Hermes `chat()` 默认走 SSE 流式（实际 HTTP 请求含 `stream=true` + `stream_options`，与 request dump 里被剥离的 body 不同）；mock 必须返回 `text/event-stream` 而非普通 JSON，否则报 "empty response stream" 重试后失败。
- 验收：`services/agent-runtime/tests/test_runtime.py` 5 个测试（parse_proposal 纯逻辑：普通 JSON/markdown fence/嵌入对象/值域拒绝/非 JSON 拒绝）通过，`uv run --frozen pytest -q`（3.14）→ 11 passed（6 adapter + 5 runtime）；sg-prod 上零成本端到端冒烟全链路跑通——隔离键生效（`memory store: None`、`valid tools: []`）→ 简报确定性生成 → `chat()` 流式调用 mock 1 次完成 → 解析出 `source_status=produced`/`p_outperform=0.6`/`model`/`references` 的合法 `ResearchProposal`，输出 `SMOKE OK`。
- 剩余限制：真实 LLM 调用需网关 + 非零预算（Phase 1B）；平台研究工具注册与服务端授权未实现（`ctx.register_tool` 机制待确认）；Controller 端 `proposal → SourcePrediction` 封存接线、取消/权限/升级兼容性测试、独立 Docker 镜像构建与 digest 固定均归 S07 后续切片。

### S07e 进度（2026-09-28）

- 状态：**冻结证据行引用解析与 Runtime 消费完成本地验收**。检查确认 Core 尚无 Proposal 接收入口，因此本片接入已有 `run_research` 返回路径；Controller 和 Ledger 接线保持待办。
- 契约实现：`resolve_reference(evidence, reference) -> dict` 使用严格的 `snapshot:<snapshot_id>/rows/<index>`，按冻结 content 原始零基位置解析；拒绝自由文本、旧 `row-0` 占位符、外部 URL、错快照、越界、负数/前导零/非 ASCII 索引及非 evidence 类型；解析前重验内容 hash，返回深拷贝。`evidence_row_locator` 为调用方生成同一格式，`validate_proposal_references` 额外检查 run/case 并解析引用列表。
- 实际消费：简报原来只有行情摘要，本片补充完整冻结行及对应定位符，保留完整 batch 快照与原始行序；`run_research` 在返回前调用引用校验。`parse_proposal` 保持 wire/值域解码职责，直接调用它不代表引用已经验证。冒烟 mock 从实际请求复制定位符，冒烟脚本增加引用验证，未重跑 SG Hermes 冒烟。
- 边界：输入依赖可信调用方已经授权的 FrozenEvidence，不新增数据库、租户授权或隐含的证券过滤；引用存在不证明论断成立，也未改变最少引用数量或论断覆盖政策。memory/code/model 类型因无对应授权解析器，在本校验入口拒绝。未来 Controller 必须独立重验绑定证据，继续执行租约、截止、预算及 release 检查。
- 依赖修复：共享 contracts 原声明只支持3.13，与已建立的3.14 Agent Runtime 矛盾；现支持 `>=3.13,<3.15`，Core/Runner 仍3.13，Agent Runtime 仍3.14。三个环境重新 lock/sync 成功，依赖版本与锁文件字节不变，实际安装元数据及导入已核对。
- 验证：按 TDD 观察到解析函数缺失、错快照/负索引误通过、返回值修改冻结输入、Runtime 返回未校验引用等失败，再逐项实现。Core：`uv run --frozen pytest -q --tb=short --maxfail=3` → **301 passed in 85.23s**；Agent Runtime 自有3.14环境：`uv run --frozen pytest -q` → **16 passed**；指定该包 pytest 配置并隔离 Core fixture 后，3.14研究契约 → **32 passed**。测试只替换外部 Hermes SDK，简报/解析/引用校验使用真实本地实现，无真实模型费用。
- 下一步：Controller 受控接收 Proposal、冻结证据授权与 `SourcePrediction` 封存可继续做离线工程验证；候选角色/prompt/反证步骤可以准备，比较前登记 Trial，正式生效仍需具体 release 的人类批准。真实网关调用、预算与取消计费、工具授权及目标机部署单独验收。

### S07f 进度（2026-09-28）

- 状态：**Controller 离线接收与封存映射完成本地验收**。纯逻辑、无 DB/网关/真实模型；实际 `seal_commit` 接线（fencing/window/attempt）与 Phase 1B campaign 注册仍待后续。
- 交付：`youwei_core/ledger/controller.py`——`proposal_to_llm_adjusted(proposal, evidence_snapshot_id) -> SourcePrediction`（produced/unavailable 两种状态映射；`evidence_snapshot_id` 由 Controller 从自身冻结快照提供，不信 proposal 的引用；produced 记录 `model.model_version` 归因）；`apply_phase1b_fallback(proposal, quant_prediction, ...) -> ProposalReception`（campaign-policy §3 Phase 1B 回退：LLM unavailable 且 quant produced → 复制 quant 输出标 `fallback`，保留 LLM 失败 reason + `fallback_from`/`fallback_reason`；quant 也无有效输出 → 保持 unavailable；produced 直接通过；`phase1b-llm-from-quant` 外的政策拒绝）。新增 `PHASE1B_FALLBACK_POLICY` 常量。
- 架构定位：映射放 Core（掌握封存/fencing/预算/快照身份），agent-runtime（独立 3.14 包）不 import Core；proposal 跨进程边界接收后在此映射，再走既有 `seal_commit` 的 fencing/window/evidence 路径。
- 验收：`tests/pure/test_controller.py` 8 个测试（produced 映射、unavailable 映射、produced 无 model 契约层拒绝、produced 不 fallback、LLM unavailable→quant fallback、quant 无效→保持 unavailable、未知 fallback 政策拒绝、政策常量核对）全部通过；`tests/pure/` 为免 PG 目录（自有 conftest 覆盖根 PG fixture）。`uv run --frozen pytest tests/pure/ -q` → 8 passed；contracts 52 passed 无回归。
- 剩余限制：`register_campaign` 与 `sealing._validate_sources` 仍只认 Phase 1A（Phase 1B 政策实现属后续切片，需新 Campaign + release 人工批准才启用）；Controller 实际接收 proposal 的进程边界（job/HTTP/子进程）、真实证据快照授权、`proposal → SealRequest` 三源封存的端到端接线、取消/权限/升级兼容性测试均待后续；候选 prompt/角色登记与批准归人。

### S07g 进度（2026-09-28）

- 状态：**Phase 1B 封存政策与 pipeline proposal seam 完成本地+SG 验收**。纯逻辑离线测试通过，且 DB 级端到端测试已在 sg-prod（Docker + 真实 PG + Alembic）验证通过。
- 交付：
  - `youwei_core/ledger/service.py`：新增 `PHASE1B_ENABLED_SOURCES=("baseline","quant_model","llm_adjusted")` 与 `PHASE1B_FALLBACK_POLICY="phase1b-llm-from-quant"`；`register_campaign` 从「仅 Phase 1A」改为「Phase 1A 或 Phase 1B」（Phase 1B 要求 fallback_policy 匹配 `phase1b-llm-from-quant`，否则拒绝）。
  - `youwei_core/ledger/sealing.py`：`_validate_sources` 的 enabled 校验改为接受 Phase 1A 或 Phase 1B（其余 source 集合拒绝）；fallback 分支改用 `PHASE1B_FALLBACK_POLICY` 常量（去魔法字符串）；Phase 1A 下 `llm_adjusted` 仍固定 unavailable/not_enabled（不变）。
  - `youwei_core/ledger/controller.py`：`PHASE1B_FALLBACK_POLICY` 改为从 `service` 导入（去重复定义）。
  - `youwei_core/ledger/pipeline.py`：`run_batch_predictions` 加 `llm_adjusted_provider` 可选参数（默认 `_phase1a_llm_adjusted` 保持 unavailable/not_enabled）；新增 `make_phase1b_llm_adjusted_provider(fetch_proposal)`（proposal 获取经进程边界由调用方提供，None/异常映射为 unavailable，其余走 `apply_phase1b_fallback`）。
  - `tests/test_ledger_campaign.py`：原「拒绝 Phase 1B sources」断言改为「Phase 1B sources + Phase 1A fallback 不匹配被拒绝」。
  - `tests/test_ledger_phase1b.py`（新）：Phase 1B DB 级端到端测试。
- 验收：
  - 纯逻辑（本机/SG 无 DB）：`tests/pure/test_phase1b.py` 9 个测试全通过；`tests/pure/` 全量 17 passed、contracts 52 passed 无回归；所有 ledger 模块 import 正常。
  - **DB 级（sg-prod，Docker 29.5.2 + postgres:16-alpine + 真实 Alembic）**：`tests/test_ledger_phase1b.py` 3 个测试全通过（Phase 1B campaign 注册含 fallback_policy、llm_adjusted produced 三源封存、llm_adjusted fallback 复制 quant 封存）；`test_ledger_campaign.py` 12 passed（含更新后的 Phase 1B 断言）、`test_ledger_seal.py` 17 passed（Phase 1A 封存无回归）、`test_ledger_pipeline.py` 13 passed（无回归）。
- 剩余限制：Controller 实际接收 proposal 的进程边界（job/HTTP/子进程）、真实证据快照授权、取消/权限/升级兼容性测试仍待后续；Phase 1B 正式启用仍需新 Campaign + 人工批准 release + 数据源 LLM 转发授权 + 非零预算。

### S07h 进度（2026-09-28）

- 状态：**Controller 到 agent-runtime 的进程边界接线完成本地+SG 验收**。Controller 现在会为 Phase 1B 批次把冻结证据经子进程送往 agent-runtime，取回 ResearchProposal 并封存 produced 的 llm_adjusted；纯逻辑与 DB 级端到端测试均通过。
- 交付：
  - `youwei_core/ledger/evidence.py`：`build_frozen_evidence` / `build_case_plan`——把 Controller 已冻结的快照（`read_snapshot` 结果）+ 计划 case 行 + 批次 manifest 组装成 `research-v1` 的 `FrozenEvidence`；非 forward 快照拒收；contracts 的 `EvidenceSnapshot` 在构造时重验内容 hash。
  - `youwei_core/ledger/agent_client.py`：Controller 侧子进程客户端——`encode_request`/`decode_result`（单行 JSON stdin/stdout 编解码，结果重新 `ResearchProposal.model_validate`）、`run_agent_research`（注入 process_factory 的 async seam，超时 kill 子进程）、`build_process_factory`（绑定 command/env）。
  - `youwei_core/ledger/pipeline.py`：`AgentRuntimeConfig` dataclass（research_config/process_factory/timeout）；`make_phase1b_llm_fetcher`（绑定 run_id/tenant_id/snapshot/batch_manifest/capability_token，返回 `fetch_proposal(case,bars)`，并在取回后独立重验 proposal 的 run_id/case_id 绑定）；`run_batch_predictions` 在 campaign 启用 `llm_adjusted` 且提供了 agent_runtime 时经该 fetcher 构造 provider，否则仍封存 unavailable（不伪造 LLM 值）。
  - `youwei_core/worker/loop.py` + `youwei_core/config.py`：`_build_agent_runtime(settings)` 组装 `AgentRuntimeConfig`（`agent_runtime_enabled`/`agent_runtime_command`/`agent_runtime_pythonpath`/`agent_runtime_timeout_seconds` + `agent_llm_*`），传给 `make_batch_predict_handler`；能力令牌沿用 worker loop 在 claim 时已签的 per-job 令牌（scope 含 `llm_call`）。
  - `services/agent-runtime/src/youwei_agent_runtime/invoke.py` + `main.py`：agent-runtime 侧子进程入口 `research-once`——stdin 读一行 JSON（capability_token + FrozenEvidence + config），`verify_capability`（scope `llm_call` + tenant 绑定）后调 `run_research`，stdout 单行输出 `{ok, proposal}` 或 `{ok:false, error}`；能力密钥经环境变量传入，不进命令行/请求体。
- 验证：
  - 纯逻辑（本机 3.13）：`tests/pure/test_evidence_agent_client.py` 7 个测试通过（FrozenEvidence 组装、非 forward 拒收、篡改拒收、编解码往返、假子进程驱动 fetcher 取回 proposal、子进程失败抛错）；`tests/pure/` 全量 24 passed、`tests/contracts/` 52 passed 无回归。
  - agent-runtime（本机 3.14）：`services/agent-runtime/tests/test_invoke.py` 6 个测试通过（合法能力取回 proposal、跨租户拒收、缺 scope 拒收、坏签名拒收、非 JSON 拒收、结果/错误编解码）；`services/agent-runtime` 全量 22 passed。
  - **DB 级（sg-prod，Docker 29.5.2 + postgres:16-alpine + 真实 Alembic）**：`tests/test_ledger_phase1b.py` 4 个测试通过（新增 `test_phase1b_pipeline_seals_produced_llm_via_agent_runtime`——Phase 1B campaign 经 `run_batch_predictions(agent_runtime=...)` 用假子进程 fetcher 取回 produced proposal 并三源封存，llm_adjusted 落 produced）；`test_ledger_pipeline.py`/`test_ledger_seal.py`/`test_ledger_campaign.py` 共 42 passed 无回归。
- 剩余限制：agent-runtime 子进程的真实 Hermes `AIAgent` 调用仍需 LLM 网关 + 非零预算（Phase 1B 启用）；预算账本对 Hermes 内部网关调用的成本归集仍是后续切片（本片只取回 proposal，不记账）；平台研究工具（snapshot_manifest/quant_run/...）注册与服务端授权未实现；取消/权限/升级兼容性测试、独立 Docker 镜像构建与 digest 固定归后续；sg-prod 的 Hermes venv 缺 pydantic（youwei-contracts 依赖），正式 agent-runtime 部署环境随 S09 镜像切片补齐。Phase 1B 正式启用仍依赖新 Campaign + 人工批准 release + 数据源 LLM 转发授权。

### S07i 进度（2026-09-28）

- 状态：**平台研究工具（snapshot_manifest）纯逻辑层 + runtime 接入完成本地验收**。首个平台工具 `snapshot_manifest` 的 schema/handler/授权模型落地，run_research 在回合内把 Controller 能力令牌塞进 contextvar 供工具 re-verify；真实 Hermes `tools.registry` 直连注册路径已在 sg-prod 源码核实（注册 API 钉定），SG 运行时注册留待 S09 镜像切片。
- 交付：
  - `services/agent-runtime/src/youwei_agent_runtime/tools.py`（新，纯逻辑、无 Hermes 导入）——`RESEARCH_TOOLSET="youwei-research"`、`TOOL_REQUIRED_SCOPE="llm_call"`、`ToolContext`（evidence + capability_token/secret）、`set/reset/current_tool_context` contextvar、`require_scope`（对已验令牌 re-verify 签名/过期/scope，防御纵深）、`snapshot_manifest_schema`/`snapshot_manifest_handler`（报告冻结证据 manifest：snapshot_id/kind/as_of/mode/content_sha256/row_count/schema/coverage/sources，重验 content hash，不读活数据）、`RESEARCH_TOOL_DEFINITIONS` + `register_research_tools(ctx)`（PluginContext 路径）。
  - `services/agent-runtime/src/youwei_agent_runtime/runtime.py`：`_register_research_tools()`（直连 `from tools.registry import registry` + `registry.register(...)`，deferred import 保纯逻辑面仍可导入）；`run_research` 增加可选 `capability_token`/`capability_secret`，回合内 `set_tool_context` 并在 finally 复位。
  - `services/agent-runtime/src/youwei_agent_runtime/invoke.py`：`honor_request` 把已验的 capability token/secret 原样转发给 `run_research`（工具 handler 可据此 re-verify）。
  - `services/agent-runtime/src/youwei_agent_runtime/main.py`：`research-once` 在 honor 前进程级调用一次 `_register_research_tools()`。
  - `services/agent-runtime/src/youwei_agent_runtime/adapter.py`：`ISOLATION_KWARGS` 的 `enabled_toolsets` 由 `[]` 改为 `[RESEARCH_TOOLSET]`（仅平台工具集，仍关全部 Hermes 内置工具集）；注释同步。
- 验证：
  - agent-runtime（本机 3.14）：`tests/test_tools.py` 12 个测试（context 缺失拒绝、set/reset、scope 有效/缺失/坏签名/过期、handler 报 manifest、row_count 回退 len(content)、篡改拒收、缺 scope 拒收、schema 形状、definitions 注册表）；`tests/test_invoke.py` 增 capability 转发断言；`tests/test_runtime.py` 增回合内 context 可读 + 回合后清理；`services/agent-runtime` 全量 **37 passed**。
  - **sg-prod 只读核实**：`tools/registry.py` 第 1015 行 `registry = ToolRegistry()`、第 666 行 `register(name, toolset, schema, handler, check_fn=None, requires_env=None, is_async=False, description="", emoji="", max_result_size_chars=None, dynamic_schema_overrides=None, override=False, scope=None)`、第 843 行 `get_definitions`（`{...schema, "name": entry.name}`）、第 888 行 `dispatch`（sync `handler(args, **kwargs)`，str 返回合法）；`from tools.registry import registry`（非 `from tools import registry`）确认。详见 runtime-version-pinning.md「平台工具直连注册路径核实」。
  - **SG 实测（真实 Hermes，零成本）**：Hermes venv 已补齐 pydantic 2.13.4，同步 S07i 源码后——`_register_research_tools()` 注册生效（`registry.get_entry("snapshot_manifest")` toolset=`youwei-research`，构造日志 `Enabled toolset 'youwei-research': snapshot_manifest`、`Final tool selection (1 tools): snapshot_manifest`）；handler 授权正反两面通过（无 context 拒绝、合法 `llm_call` scope 返回 manifest、`snapshot_read` scope 拒绝）；`smoke_e2e.py` 全链路 `SMOKE OK`（mock 提取真实 locator、proposal 解析 + 引用校验通过、`resolved evidence rows: 1`）。详见 runtime-version-pinning.md「SG 实测」。
  - **Tool Search 渐进披露（记录）**：Hermes `tools.tool_search` 默认把 plugin 工具折叠到 `tool_call`/`tool_describe`/`tool_search` 桥后，故 `agent.valid_tool_names` 显示桥工具而非 `snapshot_manifest`；不影响注册与 handler 授权（dispatch 路径不变），是否关闭 tool_search 让工具直接暴露归真实 LLM 调用切片评估。
- 剩余限制：`quant_run`/`sandbox_submit`/`sandbox_status`/`artifact_read` 四个平台工具未实现（adapter 的 `RESEARCH_TOOLS` 白名单已列名，后续切片逐个落地，每个 handler 同样按 run 能力令牌授权）；预算账本对 Hermes 内部网关调用成本归集、取消/权限/升级兼容性测试、独立 Docker 镜像构建与 digest 固定归后续；Phase 1B 正式启用依赖新 Campaign + 人工批准 release + 数据源 LLM 转发授权。

### S07i 补充（工具范围收敛，2026-09-30）

- 状态：**收敛平台工具声明与实现的一致**。此前 `adapter.RESEARCH_TOOLS` 常量列了 5 个工具名，但实际注册只来自 `tools.RESEARCH_TOOL_DEFINITIONS`（当时仅 `snapshot_manifest`），且该常量仅被测试引用——这是声明与实现不一致，不能描述为 Hermes 已获得五项能力。
- 调整：删除 `adapter.RESEARCH_TOOLS` 常量及其测试断言；工具的唯一事实来源收敛为 `tools.RESEARCH_TOOL_DEFINITIONS`（当前 `["snapshot_manifest"]`）。`test_tools.py::test_research_tool_definitions_registry` 已断言实际注册集合 == `["snapshot_manifest"]`（非测常量）；未授权调用拒绝已由 `test_current_tool_context_raises_without_run`/`test_require_scope_rejects_*`/`test_snapshot_manifest_handler_requires_scope` 覆盖。沙箱执行类能力（quant_run/sandbox_submit/sandbox_status/artifact_read）非 Pi 专属、也非不可安全实现，但需「研究请求 → Controller 授权 → 执行 → 产物引用」的往返契约，归 S08 按一个完整探索用例决定接口（提交、状态查询、产物读取分别授权），不预先承诺这四个名称。
- 验证：agent-runtime（本机 3.14）全量 **42 passed** 无回归。
- 剩余限制：收敛工具列表本身不代表整个 S07 已完成；S07 剩余验收（真实网关调用、取消计费语义、独立 Docker 镜像构建与 digest 固定）与 S08 沙箱往返契约继续推进。Phase 1B 正式启用依赖新 Campaign + 人工批准 release + 数据源 LLM 转发授权。

### S07j 进度（2026-09-28）

- 状态：**agent-runtime 弹性测试经真实 Controller 路径完成（C 切片：取消/权限/兼容性/故障恢复）**。测试驱动真实 worker loop → run_batch_predictions → fetcher → 子进程链路，仅在外部 Hermes/网关边界用假子进程替身；真实上游是否停止计算/计费另行验收。
- 交付：
  - `tests/test_agent_runtime_resilience.py`（新，DB 级，sg-prod Docker + 真实 Alembic）：5 个测试——执行中取消 kill 子进程 + attempt 落 cancelled、子进程超时/非零退出均封存 llm unavailable（batch 不崩溃）、重复响应 already_sealed 幂等（每 case 仅一个 commit）、lease 过期重新入队并恢复封存。
  - `tests/pure/test_evidence_agent_client.py`：补 4 个纯逻辑测试——fetcher 层错 run/错 case 绑定拒绝（`run_id/case_id does not match`）、decode_result 对不兼容 proposal 形状（缺 produced 值）报错、非 JSON 报错。
  - `services/agent-runtime/tests/test_invoke.py`：补过期令牌拒绝（`capability rejected`）。
  - `youwei_core/worker/loop.py`：修复模块级 `AgentRuntimeConfig` 未导入导致 `import youwei_core.worker.loop` 抛 `NameError`（`_build_agent_runtime` 返回注解在加载时求值）；模块级 import，`main()` 去重复局部 import。
- 关键发现：SG 部署目录的 `agent_client.py` 仍是 S07h 之前版本（缺 `4fc65ee` 的 finally 取消 reap，只有 `except TimeoutError` kill），取消测试在 SG 先暴露 `hanging.killed=False`，同步本机 `agent_client.py` 后通过——确认「测试经真实路径」能捕获部署同步遗漏。
- 验证：
  - 本机（3.13）`tests/pure/` 全量 31 passed（27 + 新增 4）；`services/agent-runtime` 全量 38 passed（37 + 过期令牌 1）。
  - sg-prod DB 级：`tests/test_agent_runtime_resilience.py` 5 passed；`test_worker_loop.py` 7 passed、`test_ledger_phase1b.py`+`test_ledger_pipeline.py` 17 passed 无回归。
- 剩余限制：真实 Hermes/网关的取消计费语义（上游是否停止计算/停止计费）另行验收；预算归集（A 切片）与剩余平台工具（B 切片）后续；Phase 1B 正式启用依赖新 Campaign + 人工批准 release + 数据源 LLM 转发授权。

### S07k 进度（2026-09-29）

- 状态：**预算归集 A 切片——用量观测与跨进程透传完成（SG 只读核实 + 本地离线验收）**。真实 Hermes checkout 的 usage 属性已核实，session 累计差值作为回合用量主观测源、`_last_turn_usage` 作兼容回退；wire 协议已携带带完整性与来源标注的 usage 报告；DB 归集（预算 settle）为下一片，成本换算与预留/结算时点语义已确定（见下）。
- **SG 只读核实结论（固定 SHA `7fa45eb`，`~/s01-verify/hermes314`）**：
  - `agent._last_turn_usage` **真实存在**（`agent/turn_usage.py:191`，`agent._last_turn_usage = dict(usage_dict)`，注释 "Stash canonical usage for on_turn_complete(); keep the latest call's."），但语义是**最后一次 API 调用的 usage**，不是回合累计；回合开始 `conversation_loop.py:1573` 重置为 `None`，回合结束 `turn_finalizer.py` 传给 `on_turn_complete` 钩子。
  - **session 累计计数器是回合用量的权威观测源**：`agent/turn_usage.py:174-181` 每次 API 调用后把 `record_response_usage` 累加到 `session_prompt_tokens`/`session_completion_tokens`/`session_total_tokens`/`session_input_tokens`/`session_output_tokens`/`session_cache_read_tokens`/`session_cache_write_tokens`/`session_reasoning_tokens` + `session_api_calls`（`agent/agent_init.py:2298-2303` 初始化全 0）。`session_api_calls` 对**每个完成的 provider attempt 都计数**（含工具循环的辅助调用），但「包含全部重试/压缩/辅助调用」这一覆盖范围**仍需按固定版本核实，不自动假设**。
  - `chat()` 只返回字符串（`turn_facade.py`：`run_conversation(...)["final_response"]`），返回 dict 无 usage 字段，故 usage 只能从 agent 属性读。
  - `CanonicalUsage`（`agent/usage_pricing.py`）：`input_tokens`/`output_tokens`/`cache_read_tokens`/`cache_write_tokens`/`reasoning_tokens`，`prompt_tokens = input + cache_read + cache_write`（**字段重叠，不可全部相加计费**）。
- **用量观测语义（已确定）**：
  - session_* 累计计数器为回合用量主观测源，取**回合前后差值**（before 值检查、不假定恒 0）；`_last_turn_usage` 仅作兼容回退，必须标记 `last_call_fallback` + `complete=False`（只覆盖最后一次调用）。
  - token 数与 api_calls 用**可空非负整数**：未知填 `null`，确认为零才填 `0`。保留各字段原始语义，prompt/input、completion/output 可能重叠，缓存与 reasoning 可能是子集，**不全部相加计费**。`api_calls` 只表达已核实计数范围，不能直接当作供应商收费请求次数。
  - `complete=true` 需覆盖范围已核实且本回合无丢失；超时/断流/取消/覆盖不明或 counter 倒退（回合中 reset）均标记不完整并附 `incomplete_reasons`。
- **成本换算语义（已确定，下一片实现）**：版本化、已核实的 cost map，记录模型、费率版本及计费类别（含缓存 token 等差异）；费率或用量不完整时保留为待对账，占位价不能写成已确认的 actual_micros，未知费用不能按 0 处理。最终费用核对以 Gateway／供应商记录为依据。
- **预留与结算时点（已确定，下一片实现）**：预留由 Controller 在可能产生费用的调用开始前执行；Hermes 内部多次调用还需 Gateway 执行请求级限额避免总预算被绕过。结算在用量及可用价格确定后按调用或 attempt 幂等结算，不等待 batch 封存；预测失败/封存失败/取消时已发生费用仍归集，费用未知则保留预留等待对账。
- 交付：
  - `services/agent-runtime/src/youwei_agent_runtime/runtime.py`：新增 `UsageReport`（`source`/`scope`/`complete`/`incomplete_reasons` + 8 token 字段 + `api_calls`，`to_dict()` 供 wire 序列化）、`_SessionUsageSnapshot`、`_snapshot_session_usage`、`_delta_usage`（取差 + counter 倒退检测）、`_from_last_call`（last_call_fallback，含 provider 命名容错 `cache_read_input_tokens`/`input_tokens` 等）；`ResearchTurn` 的 `usage` 由裸 dict 改为 `UsageReport`；`run_research` 回合前/后各快照一次 session 计数器取差，无 session 计数器时回退 last-call，无任何信号时 `unavailable`。
  - `services/agent-runtime/src/youwei_agent_runtime/invoke.py`：`encode_result(turn)` 携带 `usage` 报告（`{ok, proposal, usage}`），`honor_request` 传 `turn` 而非 `turn.proposal`。
  - `youwei_core/ledger/agent_client.py`：新增 `AgentResearchResult`（`proposal` + `usage`）；`decode_result` 解析 usage，旧 proposal-only runtime 给合成 `unavailable` 报告（`usage_not_reported`，未知非零）；`run_agent_research` 返回 `AgentResearchResult`。
  - `youwei_core/ledger/pipeline.py`：`make_phase1b_llm_fetcher` 加可选 `usage_sink` 回调，取回后把 `result.usage` 原样传给 sink（预算层归集用）；无 sink 时丢弃 usage 但仍返回 proposal（封存路径无需预算账本）。
  - 测试：`services/agent-runtime/tests/test_runtime.py` 重写 usage 观测测试（session_delta 完整字段/complete、last_call 回退、无信号 unavailable、counter 倒退 incomplete），`test_invoke.py` 补 usage 透传断言；`tests/pure/test_evidence_agent_client.py` 补 wire usage 往返、proposal-only 默认 unavailable、usage_sink 透传、无 sink 丢弃。
- 验证：
  - agent-runtime（本机 3.14）：`services/agent-runtime` 全量 **42 passed**（40 + 新增 2）。
  - 本机（3.13）`tests/pure` + `tests/contracts` 全量 **86 passed** 无回归（DB 级测试需 Docker，本机不可用未跑；sg-prod 归后续）。
- 剩余限制：session 计数器覆盖范围已核实为「主回合 provider 调用」（`turn_response_check.py` → `record_response_usage`）；auxiliary 调用（压缩/标题/vision/web_extract/session_search）的 usage 走独立的 `agent/aux_accounting.record_aux_usage` → `session_db.record_auxiliary_usage`，**不进入 session_* 计数器**，且隔离配置 `_session_db=None` 时会被静默丢弃——研究角色是否实际触发 aux 调用需在真实网关调用时观测。真实费率缺失只阻断真实金额验收，不阻断结算逻辑离线实现。Phase 1B 正式启用依赖新 Campaign + 人工批准 release + 数据源 LLM 转发授权。

### S07k 预算结算进度（2026-09-29，cost map + Controller 接线）

- 状态：**版本化 cost map 与 Controller 预留/结算接线完成（离线实现 + 纯逻辑验收）**；Gateway 请求级限额（第三片）与 DB 级验证留后续。占位价结算 book 为 `estimated_micros` 而非已确认 `settled_micros`，未知费用保留预留待对账、不按 0 处理。
- **cost map（第 1 步）**：
  - `youwei_core/llm/pricing.py` 重写——`CostMap`（`version` + `currency=USD` + `models`）、`RateCard`（5 个计费类别 `input`/`output`/`cache_read`/`cache_write`/`reasoning`，费率 `Decimal` micros/token，`status ∈ {placeholder, reconciled}`）、`CostResult`（`amount_micros` + `status ∈ {confirmed, estimated, unknown}` + `breakdown`）、`price_usage`（5 类别独立计价，占位卡→estimated、reconciled→confirmed）、`estimate_max_cost_for`（ceil 保守预留）；币种 USD、金额精度整数 micros、舍入 half-up（结算）/ceil（预留）；`prompt_tokens`/`completion_tokens` 是派生字段（prompt=input+cache_read+cache_write），不叠加计费。legacy `actual_cost`/`estimate_max_cost`/`MODEL_PRICES` 保留为薄封装（占位价，不得 book confirmed）。`DEFAULT_COST_MAP`（`cost-map-v1-placeholder`）承载现有占位价。
- **Controller 接线（第 2 步）**：
  - `youwei_core/budget/service.py`：`settle` 加 `confirmed: bool = True`——`confirmed=True` book `settled_micros`（entry_type `settle`），`confirmed=False` book `estimated_micros`（entry_type `settle_estimate`，idem_key `:estimated`）；两者不同幂等键，estimate 不与后续 confirmed 撞键；`pending_reconciliation` 把 `settle_estimate` 也视为已结算（关闭 reserve）。
  - `youwei_core/db/meta.py` + `migrations/versions/e7f8a9b0c1d2_s07k_estimated_cost_column.py`：`runs.estimated_micros`（BigInteger 非负）与 `settled_micros` 分开；`get_run_view` 暴露 `estimated_micros`。
  - `youwei_core/llm/client.py`：`GatewayClient` 加 `cost_map` 注入；reserve 用 `estimate_max_cost_for`、settle 用 `price_usage` 的 `status` 驱动 `confirmed`（占位价→estimated）。
  - `youwei_core/ledger/pipeline.py`：新增 `TurnBudgetWiring`（`engine`/`attempt_id`/`turn_reserve_micros`/`cost_map`）；`make_phase1b_llm_fetcher` 加 `budget` 参数——turn 前 `reserve` 配置上限、turn 后 `_settle_turn_budget`（仅 `source=session_delta` 且 `complete=True` 才计价结算，占位价→estimated；不完整/未知/`last_call_fallback` 保留 reserve 待对账，不按 0）；`AgentRuntimeConfig` 加 `turn_reserve_micros`；`run_batch_predictions` 在 `turn_reserve_micros > 0` 时构建 `TurnBudgetWiring`。
  - `youwei_core/config.py` + `worker/loop.py`：`agent_turn_reserve_micros`（默认 0=不启用）经 `_build_agent_runtime` 传入。
- 验证（纯逻辑 + DB 级）：`tests/pure/test_cost_map.py` 12 passed（5 类别独立计价、prompt/completion 不叠加、placeholder→estimated vs reconciled→confirmed、非整数费率精确 + half-up/ceil 舍入、unknown model 拒绝）；`tests/pure/test_turn_budget.py` 6 passed（complete placeholder→estimated、reconciled→confirmed、incomplete/unavailable/非 dict/last_call 均保留 reserve）；本机 `tests/pure`+`tests/contracts` 全量 **104 passed** 无回归。**sg-prod DB 级（Docker + postgres:16-alpine + 真实 Alembic）**：`tests/test_llm_client.py`+`test_budget.py` 15 passed（含 placeholder→estimated、reconciled→confirmed 两路径）；`test_ledger_phase1b.py`+`test_ledger_pipeline.py` 17 passed、`test_agent_runtime_resilience.py`+`test_worker_loop.py` 12 passed、`test_run_submission.py`+`test_worker.py` 15 passed 无回归；新增 `tests/test_turn_budget_db.py` 3 passed（fetcher 端到端：完整 usage+占位价→estimated_micros、不完整/unavailable→reserve 保留待对账）；迁移 `e7f8a9b0c1d2` upgrade/downgrade/upgrade 在临时 PG 实测通过。
- 剩余限制：**第 3 片 Gateway 请求级限额未实现**（Hermes 内部多次调用/重试/辅助请求的请求级预留与结算，防止绕过 run 总预算——依赖 LiteLLM budget reservation，见 llm-gateway-options.md）；`turn_reserve_micros` 的保守上限值需部署时按真实网关定价配置，当前为占位价估算；`estimated_micros → settled_micros` 的对账转换（`adjust`）未实现（对账后人工/自动把 estimate 转为 confirmed）；真实费率对账后替换 `DEFAULT_COST_MAP` 为 reconciled 版本。

### 预算/计费删除（2026-09-30，项目所有者决定）

- 状态：**预算与计费维度整体删除**。项目所有者决定「计费这块不重要」，保留 run/job/attempt 任务执行机制（租约、幂等、fencing、取消），仅删除金额（预算/预留/结算/成本 map）这一维度。
- 删除范围：
  - 模块：`youwei_core/budget/`（reserve/settle/release/pending_reconciliation）与 `youwei_core/llm/`（GatewayClient + pricing，均为无业务调用方的死代码，实际 LLM 调用由 Hermes 在 agent-runtime 内直连网关完成）。
  - schema：`budget_entries` 表整表删除；`runs` 表删除 `total_budget_micros`/`reserved_micros`/`settled_micros`/`estimated_micros` 四列及对应 check 约束。迁移 `f8a9b0c1d2e3`（upgrade/downgrade/upgrade 实测通过）。
  - 接线：`RunSubmission.total_budget_micros` 字段、`get_run_view` 预算字段、`pipeline.py` 的 `TurnBudgetWiring`/`_settle_turn_budget`、`ops` 的 pending_reconciliation 告警、`config` 的 `alert_pending_reconciliation_age_seconds`/`agent_turn_reserve_micros` 均删除。agent-runtime 的 usage 报告仍透传（`usage_sink`）但仅作观测，不再接预算结算。
  - 测试：删除 `test_budget.py`/`test_llm_client.py`/`test_turn_budget_db.py`/`pure/test_cost_map.py`/`pure/test_turn_budget.py`，清理其余测试的预算断言。
- 验证：
  - 本机（3.13）`tests/pure`+`tests/contracts` **86 passed**（删 18 个预算/成本测试后无回归）；全部 Core 模块 import 通过。
  - sg-prod DB 级（真实 PG + Alembic）：`test_run_submission`+`test_worker`+`test_worker_loop`+`test_ops` 30 passed；`test_ledger_seal`+`test_ledger_pipeline`+`test_ledger_phase1b`+`test_agent_runtime_resilience` 39 passed；`test_auth`+`test_capability`+`test_ledger_*`（campaign/archive/outcomes/evaluation/monthly/training）+`test_logging` 70 passed；`test_sandbox`+`test_data_*`（calendar/pit/snapshots/securities）51 passed。迁移 `f8a9b0c1d2e3` upgrade/downgrade/upgrade 在临时 PG 实测通过。
  - 唯一失败：`test_sandbox.py::test_timeout_kills_and_removes_container` 因硬编码 `/usr/local/bin/docker`（SG 实际在 `/usr/bin/docker`）而 `FileNotFoundError`——**既有环境问题，与本次删除无关**。
- 剩余限制：run 执行无金额上限保护；若未来需要恢复成本控制，可回滚 `f8a9b0c1d2e3` 迁移（已保留 downgrade）。S02 预算相关的历史记录保留为当时状态，不代表当前实现。

### S07l 进度（2026-10-01，agent-runtime 无头镜像构建与 digest 固定）

- 状态：**独立镜像构建与容器内无费用冒烟完成**；部署引用保持未就绪（尚无镜像仓库，digest 为本地 manifest，未 push）。镜像验收与 Controller 跨容器接线分开记录，构建成功不代表整个 S07 完成。
- 交付：
  - `infra/images/agent-runtime.Dockerfile`：精简无头镜像（非 Hermes 官方产品镜像——不用 pm/s6/Chromium/TUI）。固定基础镜像 `python:3.14-slim@sha256:51dafde8...` 与 `ghcr.io/astral-sh/uv:0.11.16@sha256:440fd647...`；Hermes 以 `git init` + `git fetch --depth 1 origin <SHA>` + `checkout FETCH_HEAD` 获取并校验 commit；`uv sync --frozen --no-dev --python /usr/local/bin/python` 装核心依赖到 `/opt/hermes/.venv`；`youwei-contracts` + `youwei-agent-runtime` 用 `uv pip install --no-deps` 装入同一 venv（避免第二锁升级 pydantic 破坏 Hermes 精确 pin）；非 root（UID 10001）；`PYTHONPATH=/opt/hermes`；ENTRYPOINT `youwei-agent-runtime`。
  - `infra/build-agent-runtime.sh`：本地打包最小 build context（rsync 排除 .venv/__pycache__）→ 传 SG → `docker build` → 报 manifest digest。
  - `services/agent-runtime/smoke/container_smoke.py`：容器内驱动真实 `research-once` 入口 + 本地 mock 网关，验证合法/错 tenant/缺 scope/坏签名/过期五场景。
  - `services/agent-runtime/src/youwei_agent_runtime/main.py`：**修复 stdout 污染**——Hermes `AIAgent.__init__` 用裸 `print()` 把启动 banner/API 进度/会话日志写 stdout，破坏 `research-once` 单行 JSON 契约；`_research_once` 现在整个 turn 期间把 `sys.stdout` 重定向到 `sys.stderr`，仅写最终结果/错误前恢复。
  - `services/agent-runtime/tests/test_main.py`：3 个纯逻辑测试钉定 stdout 纪律（结果/错误是唯一 stdout、banner 走 stderr、回合后 stdout 恢复）。
  - `docs/ops/agent-runtime-image-build.md`：验收记录（digest、基础镜像、依赖策略、冒烟矩阵、剩余限制）。
  - `infra/upstreams.lock.yaml`：hermes notes 记录 manifest digest 与验收文档；`deployment.image` 保持 null（未就绪）。
- 固定信息：镜像 `youwei-agent-runtime:dev` manifest digest `sha256:31b22113b2deee2476c94ddf290aa58f9b76c647742a60e5fd5f64cffa3dd02d`（单平台、无 provenance）；OCI tar 153MB（SG `/root/agent-runtime-image/`，内容 hash `fc72bbd5...`）；Python 3.14.7、pydantic 2.13.4（Hermes 锁）。
- 验证：
  - 容器内冒烟 **5/5 通过**：合法令牌→produced proposal（p=0.6、1 引用、usage=session_delta 完整）；错 tenant/缺 scope/坏签名/过期四类拒绝均返回 `ok:false` 且错误片段正确。`run_agent.AIAgent` import、`snapshot_manifest` 工具注册（toolset=`youwei-research`）、隔离键（memory store None）均验证。
  - 本机 agent-runtime（3.14）`pytest -q` → **45 passed**（42 + 新增 3）；`main import OK`（纯逻辑面无 Hermes 依赖）。
  - `infra/validate_upstreams.py --mode catalog` → VALID。
- 剩余限制：manifest digest 为本地 digest 非 registry 引用，尚无镜像仓库（部署引用未就绪）；真实 LLM 网关调用、取消计费语义、数据源 LLM 转发授权、Phase 1B 正式启用（新 Campaign + 人工 release 批准）仍待后续；未验证 Controller 跨容器接线、生产资源压测、gVisor 运行。

### S07m 进度（2026-10-01，研究运行时跨容器接线：Ed25519 授权 + Runner 受控执行）

- 状态：**S07m-1（研究授权契约）、S07m-2（跨容器执行）、S07m-3（SG 镜像/受限网络/贯通）均完成**。研究链路采用非对称授权，既有 sandbox-v1 HMAC 链路保持不变。S07m-3 在 SG 实测：容器内 Ed25519 冒烟 5/5、受限网络（internal network 仅网关出口，外网/宿主机/docker socket 均不可达）、真实 Controller→Runner→研究容器→mock 网关端到端贯通（`END_TO_END OK`，produced p=0.6 + session_delta usage）。
- 决策（项目所有者 2026-10-01）：契约形态为新增 `agent-runtime-v1`（保留 research-once stdin/stdout）；本片只做单次研究调用，S08 探索工具往返单独实现；研究容器仅允许访问批准的网关出口，sandbox-v1 继续无网络；凭证改造限定新研究链路（Ed25519），不迁移旧 HMAC。
- 交付：
  - `contracts/src/youwei_contracts/research_capability.py`（新）：Ed25519 研究令牌（`ywr_` 前缀）——`generate_research_keypair`/`sign_research_token`/`verify_research_token`/`public_key_thumbprint`；audience（`runner-exec`/`runtime-research`）、scope（`research:run`/`research:status`/`research:cancel`）；kid 只查询部署受信公钥，未知 kid/alg/版本/audience 一律拒绝（无 HMAC 回退）；绑定 tenant/run/job/attempt/case/证据 hash/执行配置版本/有效期/nbf；续租是全新签名（内层令牌不随外层租约续期延长）。`cryptography>=44,<51` 作为 `youwei-contracts[signing]` 可选依赖（deferred import，纯 DTO 消费者无需安装）。
  - `contracts/src/youwei_contracts/agent_runtime.py`（新）：`agent-runtime-v1` 契约——`ResearchInvocationRequest`（`invocation_id` 键解决逐 Case 冲突；绑定 tenant/run/job/attempt/case/证据/执行配置版本；证据 hash 构造时校验）、`ResearchRuntimeConfig`（只含 model/iterations，base_url/api_key 由 Runner 注入）、`ResearchInvocationResult`（含实际镜像 digest）、`ResearchInvocationStatus`、`ResearchInvocationEnvelope`（稳定内容 request 与 runtime_token 分离，续签不改变请求 digest）。
  - `services/sandbox-runner/src/youwei_runner/research.py`（新）：Runner 研究执行入口——`run_research_container`（`docker run -i` 转发 stdin/stdout，非 root 只读、cap-drop ALL、CPU/mem/PID 限制、仅网关出口网络、超时 kill+清理、stdout/stderr 字节上限、结果超限失败不截断）、`build_research_config`、`verify_research_grant`、`execute_research_request`（注入网关 endpoint/key，调用方不可覆盖）。
  - `services/sandbox-runner/src/youwei_runner/app.py`：新增 `/v1/research-invocations` 三端点（submit/status/cancel），`invocation_id` 键 + Ed25519 授权（`aud=runner-exec`）+ 幂等（同 invocation 同内容重放、异内容 409）+ 绑定校验；`settings.py` 加研究镜像/网关/公钥/执行配置版本/资源上限，生产模式强制 digest + 网关配置非空（缺网络规则拒绝启动）。
  - `youwei_core/ledger/research_client.py`（新）：Controller 侧研究链路——`ResearchSigningKey`/`sign_invocation_tokens`（签发 runner-exec + runtime-research 两令牌）、`build_research_request`/`evidence_sha256`、`ResearchRunnerClient`（HTTP 提交/轮询）、`run_research_via_runner`（提交→轮询→结果，每次令牌使用前经 expiry_provider 重签，续租=新签名）、`make_runner_research_fetcher`（`fetch_proposal(case,bars)` 一 Case 一 invocation）。
  - `youwei_core/ledger/pipeline.py`：`run_batch_predictions` 新增 `runner_research` 参数，Phase 1B 优先走 Runner 受控链路（每 Case 独立 invocation_id），否则回退旧本地 subprocess 或 unavailable；`make_batch_predict_handler` 透传。
  - `youwei_core/config.py` + `worker/loop.py`：`research_signing_private_key`/`research_signing_kid`/`research_exec_config_version`/`research_model`；`_build_runner_research` 组装 `RunnerResearchConfig`；worker 启动/关闭管理研究 client。
  - `services/agent-runtime`：`invoke.py`/`tools.py`/`runtime.py`/`main.py` 由 HMAC secret 改为 Ed25519 公钥验签（`YOUWEI_RESEARCH_PUBLIC_KEYS` env 读 `{kid: pem}`，验签 `aud=runtime-research` + `research:run` scope + tenant + case 绑定）；`main.py` 加 stdin/stdout 字节上限（超限失败不截断）。
- 验证（本机，无 Docker/DB）：`uv run --frozen pytest tests/pure tests/contracts tests/known_answers -q` → **183 passed**（含新增 research_capability 13、agent_runtime 6、research_runner_http 7、research_client 5）；agent-runtime 3.14 自有环境 `pytest -q` → **48 passed**（invoke 10、tools 12、runtime、main 含 stdin 上限）；`git diff --check` 通过。cryptography 统一到 50.x（Core/Runner/agent-runtime 三环境 50.0.2，镜像内 Hermes 锁 50.0.1），Ed25519 签发/验签在 3.13 与 3.14 均实测通过。
- SG 验证（S07m-3）：容器内 Ed25519 冒烟 5/5（合法 grant produced / 错 tenant / 缺 scope `research:run` / 坏签名 / 过期）；受限网络 `youwei-research`（`docker network create --internal`）实测——mock 网关可达、外网 8.8.8.8/宿主机公网 IP/docker socket 网关均 `Network is unreachable`；真实 `execute_research_request`→`run_research_container`（`docker run -i` + Ed25519 验签 + 网关注入 + 公钥注入）端到端贯通 `END_TO_END OK`。验收文档 [agent-runtime-cross-container.md](ops/agent-runtime-cross-container.md)；镜像 digest `sha256:998f060eb0f7fadaa1712e9540370a4dc1e79995b56f1a41a9c1f7b1e892f153`（tag `youwei/agent-runtime:dev`）。
- 剩余限制：manifest digest 为本地 digest 非 registry 引用，尚无镜像仓库（`deployment.image` 保持 null）；受限网络的 DNS/IPv6 通路未显式测试（internal network 无外网，DNS 解析需 S09 按网关实际地址固定）；真实 LLM 网关（litellm）调用、取消计费语义、数据源 LLM 转发授权、Phase 1B 正式启用（新 Campaign + 人工 release 批准）仍待后续；DB 级测试（迁移 + 现有测试全量）仍待 SG 完整跑（本片只跑纯逻辑 + 端到端冒烟）；expiry_provider 当前用 claim 时 lease，DB 级 active-lease 重查随 S07m 后续 DB 测试验证。

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


### S06h 真实冻结、候选模型与 Trial（2026-10-01）

- 状态：**本地候选工程与真实历史比较完成；未批准release、未启动正式Campaign、未部署SG生产**。
- 真实数据：EODHD 503成员按既定规则固定20证券；Tiingo 20证券+SPY采集55,189条日线，失败0。原始数据只保留在gitignore私有目录。
- 恢复：`data/panel_bundle.py`完整源/映射/登记导出导入，篡改/冲突拒绝、同事务回滚；真实新库恢复后完整包和名单hash一致、二次恢复幂等。候选库另完成pg_dump→新库恢复，日历/行情/模型manifest/release行数与内容hash一致；这是本地候选恢复，不代替独立故障域备份。
- 固定引用：NYSE规则v2补7月3日交易日提前收盘；固定2016–2028 build及hash，实际加载tzdata 2026.4/IANA 2026d字节；SPY永久ID固定。[实际引用](ops/s06-campaign-freeze-20261001.md)
- 模型：`quant/logistic.py`逐horizon L2 Logistic+独立Ridge，训练标准化固定，JSON产物无pickle；`quant/dataset.py`统一连续交易日特征和目标收益；`quant/trial.py`固定切分、成熟过滤、65session embargo、成对评分及完整计划分母。
- 实际Trial：事前追加参数后只跑一次，保存started/completed及代码/数据hash；D20测试Brier 0.253012 vs baseline 0.250000，成对差+0.003012。无调参重跑，无显著性结论。当前panel历史回顾缺乏完整历史PIT/公司行为证据，不能算正式能力证明。[结果](trials/trial-001-results.md)
- 接线：`ledger/model_registry.py`按release/horizon/hash取冻结模型，拒绝错hash/未知模型/未来训练/错日历，缺特征unavailable。`pipeline.py`新模型用120天冻结股票/SPY证据；旧release保持载具、90天和旧pipeline版本。
- 运维入口：`ops/collect_s06_trial_data.py`冻结行情/引用；`ops/run_s06_trial.py`校验预登记hash与panel并拒绝重复Trial；`ops/build_s06_release.py`校验全部输入/结果/引用后幂等登记候选，不批准、不创建Campaign。
- 候选：`release-logistic-ridge-candidate-20261001-v1`，hash `06618a0c769cceb75968106897b4f3638cd6ff61de0fe3565e73fa3c1f6eceb5`；批准记录0、Campaign0。[候选材料](ops/s06-campaign-candidate-package.md)（注：该 hash 已于 S06i 之后因 pipeline.py/uv.lock 变更失效；已于 2026-10-02 换新日期重新生成候选 `release-logistic-ridge-candidate-20261002-v1`，hash `bdb8bbe0...`，见下方 S06i 剩余项）
- 测试修复：发现旧纯测试手动asyncio.run清空pytest session loop，引发后续异步测试连锁失败。9个测试改async/await；最小三测试复现从1失败变为3通过，不改生产循环。
- 验证：关键接口按TDD实际观察红→绿；新/相关量化、日历、panel恢复、模型接线测试通过。最终 `uv run --frozen pytest -q --tb=short` → **396 passed，无skip**；`python3 infra/validate_upstreams.py --mode catalog` → VALID；`git diff --check`通过；v2四份协议hash与登记一致。冻结模型JSON重新加载后，六组验证/测试预测共7,800条及评分逐值复现。完整候选库恢复验证行情55,189行、日历3,391行、504证券、1个training manifest/1个release，批准/Campaign/预测均为0。
- 剩余：人工评估该候选是否值得前向实验及实际release批准；生产权限/部署/独立恢复仍按S09验收。不将本地运行或历史回顾升级为正式前向结果。

### S06i 前向实验登记与调度边界（2026-10-01）

- 状态：**三个调度/批准约束落地**（本地实现+纯逻辑验收）；DB级集成测试待SG验证；实际release批准仍由人完成。
- 背景：为受控前向实验（12周观察窗口、模型冻结）补齐注册与调度边界，区分「停止新增批次」与「继续结果随访」。
- 交付：
  - `youwei_core/ledger/plan.py`（新）：`expand_planned_cutoffs`按America/New_York本地日期+7天展开周六06:00 ET cutoff（跨DST不漂移）；`prepare_campaign_plan`生成 `planned_cutoffs_sha256`（周次清单）与 `campaign_plan_sha256`（完整实验范围：tenant/key/release hash/panel/target/benchmark/sources/fallback/主指标/时间协议/周次清单）；`CampaignPlanScope` Pydantic 严格校验批准 scope。
  - 迁移 `f9a0b1c2d3e4`：campaigns 加 `planned_cutoffs`/`planned_cutoffs_sha256`/`campaign_plan_sha256`；release_approvals 加 `scope_manifest`/`scope_sha256` 且唯一键从(release,approver)改为(release,approver,scope_sha256)（同一人可对同一release的不同计划分别批准）；新增 append-only `campaign_control_events` 表。
  - `service.py`：`register_campaign` 必填 planned_cutoffs 并校验 hash，且要求批准 scope_manifest 绑定本 campaign_plan_sha256 + tenant + key + release hash（legacy 字符串 scope 不授予新实验授权）；`approve_release` 加 scope_manifest；新增 `stop_campaign_new_batches`（追加事件、幂等）与 `campaign_is_stopped`；`plan_batch` 拒绝清单外 cutoff。
  - `scheduler.py`：批次规划只遍历冻结清单（第13批不存在）、跳过已停止 campaign；预测运行排除已停止；结果回填/报告/更正不受停止影响。
  - 测试：`tests/pure/test_campaign_plan.py`（DST展开、双hash敏感性、scope校验，7项）；`tests/test_s06i_campaign_plan.py`（DB级：范围外拒绝、错plan scope拒绝、停止幂等、停止后跳过规划，4项）；同步 test_ledger_campaign/phase1b/training/panel_registration 的 fixture 走「prepare→批准(scope)→register」顺序。
- 验证：`uv run --frozen pytest -q tests/pure tests/known_answers tests/contracts` → **152 passed**；各DB测试模块 `--collect-only` 无import错误。
- 剩余：DB级测试（迁移+新测试+现有测试全量）需Docker，本机不可用，待SG验证；SG生产部署/权限/恢复验收归S09；实际release批准（候选hash `06618a0c...` + 实验计划hash + 批准范围）由项目所有者一次性审阅后完成。

### S06i 范围绑定修复 + SG DB 验收（2026-10-01，补记）

- 状态：**register_campaign 范围绑定缺口已修复，全量 DB 测试在 SG 真实 PostgreSQL 验收通过**。
- 缺口（项目所有者审阅发现）：S06i 的 `register_campaign` 只重算了 `planned_cutoffs_sha256`，未根据实际登记参数重算完整 `campaign_plan_sha256`——只把调用方提供的 hash 与批准记录比较，调用方可保留已批准 hash 而篡改实际计划参数。
- 修复：
  - `plan.py`：提取 `campaign_plan_sha256_from(...)` 纯函数（从实际参数重算完整计划 hash），`prepare_campaign_plan` 复用它（保持字节一致）。
  - `service.py`：`register_campaign` 服务端重算 `campaign_plan_sha256`（用实际 tenant/key/release hash/phase/panel/target/sources/fallback/主指标/时间协议/cutoffs），与调用方提供的值比较，不匹配拒绝；`phase` 从 `enabled_sources` 推导（1a/1b）并核对批准 scope 的 phase；security 存在性检查移到重算之前（先报参数错误、后报 hash 不匹配）。
  - `plan_batch` 检查顺序修正：`resolve_batch_times`（非周六报 CalendarError）移到 frozen plan 检查之前，避免「非周六 cutoff」被误报为「outside frozen plan」。
  - 测试：新增 3 个反例测试（改 primary_metric/target_specs/enabled_sources 后携带原 hash 拒绝）；修复 S06i 遗留的 frozen plan 时间错位——`make_campaign_plan` 默认 first_cutoff 从硬编码 2026-01-03 改为动态 `next_weekly_cutoff(now)`，各测试按需显式传 first_cutoff/batch_count（monthly/archive 用过去锚点，backfill 测试用 now-3/4 周锚点）。
- 验证（SG 真实 PostgreSQL，Docker + postgres:16-alpine + Alembic）：`test_s06i_campaign_plan` 7 passed、`test_ledger_campaign` 12 passed、`test_ledger_seal`/`phase1b`/`pipeline`/`training`/`data_panel_registration` 全通过、`test_ledger_archive`/`monthly`/`evaluation`/`outcomes`/`model_registry` 全通过、`test_worker`/`worker_loop`/`ops`/`auth`/`capability` 69 passed、`test_data_*`/`panel_bundle`/`run_submission`/`logging`/`s06_prepare` 78 passed、`test_data_eodhd`/`tiingo`/`sandbox`/`agent_runtime_resilience` 38 passed。本机纯逻辑 `tests/pure tests/contracts tests/known_answers` → 183 passed 无回归。
- 剩余：`06618a0c...` 候选 release 已不能代表当前工作区——S06i 之后 `pipeline.py`（S06i 修复 + S07m runner_research）与 `uv.lock`（S07m 加 cryptography）两个 `code_files` 的 hash 变更，其余冻结输入（historical-input、trial 全 artifact、panel、spec、protocol）全部未变（本机逐一核验匹配）。决策（项目所有者）：换新日期重新生成新候选（方案 A），`manifest_id` 保持 `tm-logistic-ridge-candidate-20261001-v1` 不变（manifest 内容未变、不含 code_files，重跑幂等 created=False）；`ops/build_s06_release.py` 已参数化 `--release-date YYYYMMDD` 生成 `release-logistic-ridge-candidate-{date}-v1`。**SG 重跑已完成（2026-10-02，UTC 10-01）**：在 sg-prod 用 `candidate-database.dump` 恢复出一次性候选库容器 `youwei-s06-candidate-20261001`（`127.0.0.1:56962`，恢复验证 release/training_manifest/securities/prices/calendar_days 与旧 restore report 逐项一致），再以 `.venv/bin/python ops/build_s06_release.py --candidate-dir .local/s06-candidate-20261001 --release-date 20261002` 注册新候选。权威结果：新 `release_id`=`release-logistic-ridge-candidate-20261002-v1`、`release_content_sha256`=`bdb8bbe034551d7bcc413965925dc9d7c6f52fa31d4344b8b0c199672c5b4994`（`training_manifest_sha256` 不变 `febd1080...`）；旧 release `...-20261001-v1`=`06618a0c...` 保留在库中为历史，`research_releases` 共 2 行、批准 0、Campaign 0。三个产物文件与候选库 dump 已回写本机 `.local/s06-candidate-20261001/`（旧产物/旧 dump 备份于 `.pre-20261002/`）。Phase 1A 必需的 S09 部署/权限/恢复验收仍待做；完整 release hash + Campaign 计划 hash + 使用范围的正式人工批准归项目所有者。

### S06j 候选前向计划（12 批，2026-10-02 拟定）

- 状态：**候选 12 批 Phase 1A 前向实验的冻结输入已确定（项目所有者 2026-10-02）；最终 `campaign_plan_sha256`/`scope_manifest` 待正式 tenant UUID 与 S09 验收后生成**。不启动、不批准；批准包交项目所有者一次性确认后才可入库。
- 冻结输入：
  - `first_cutoff`：`2026-10-10T06:00:00-04:00`（America/New_York，即 `2026-10-10T10:00:00Z`）；`batch_count`=12，覆盖 2026-10-10 至 2026-12-26。
  - `campaign_key`：`phase1a-pilot-2026q4`。
  - `release_content_sha256`：`bdb8bbe034551d7bcc413965925dc9d7c6f52fa31d4344b8b0c199672c5b4994`（`release-logistic-ridge-candidate-20261002-v1`）。
  - `phase`：`1a`；`enabled_sources`=`baseline`,`quant_model`；`fallback_policy`=`phase1a-none`；`primary_metric`=`paired_brier_quant_minus_baseline`。
  - `panel`：`s00-registration.v2.json` `selected_security_ids`（20 只）；`benchmark`=SPY `ee161699-08b6-4250-986a-b39a393a68df`。
  - `target_specs`：D1/D20/D60（`excess-tr-d1-v1`/`excess-tr-d20-v1`/`excess-tr-d60-v1`）；`time_protocol_ref`=`time-protocol-v1`、`time_protocol_sha256`=`82d6b3473d8ce609f138e476d157c7032c3818753cd50c4b404ac568952cf8fe`。
- 12 批 cutoff（`expand_planned_cutoffs` 按 America/New_York 本地 +7 天展开，跨 DST 不漂移）：批 1–4 `10:00:00Z`（EDT），批 5–12 `11:00:00Z`（EST，11-07 DST 切换起）——2026-10-10/10-17/10-24/10-31（10:00Z）、11-07/11-14/11-21/11-28/12-05/12-12/12-19/12-26（11:00Z）。
- `tenant_id`：待正式库部署后查询 slug `youwei-internal-research` 是否已存在（`create_tenant` 仅按 id 幂等、不按 slug，须先查询）；已存在则复用 UUID，不存在则经管理员入口创建一次，保存/登记/备份返回 UUID 后再生成最终计划 hash。不用测试租户 UUID、不重复生成。
- 剩余：S09 部署/权限/恢复/运行验收完成后，在正式库创建/复用 tenant → 生成最终 `campaign_plan_sha256` + `scope_manifest` → 组装批准包（release hash + 计划 hash + 使用范围 + 验收证据）交项目所有者一次性确认。正式批准前若未就绪，用后续日期重新生成计划；批准登记后不自动顺延、不删除漏跑周次。

### S09a 进度（2026-10-02，部署准备：采集调度 + 生产 Compose + 镜像发布）

- 状态：**S09a 部署准备完成（Phase 1A 最小部署单元 + 镜像发布 + deployment 校验通过）**。决策（项目所有者 2026-10-02）：Phase 1A 暂不需要 Runner（Logistic/Ridge 在 Core 内执行；`runner_url` 空时 Worker 不建 Runner 客户端；Scheduler 已含在 Worker）；四项选择——GHCR 私有镜像仓库 + SG 容器化 PostgreSQL + 生产 Docker Compose + localhost API/SSH 隧道；备份用 pgBackRest（目标待落实）。
- 采集调度（补工程缺口：`data.tiingo_daily` handler 原本无调度）：
  - `youwei_core/data/collect.py`（新）：`resolve_collection_targets`（指定 release manifest 读 `panel_registration_id` + `references.benchmark_security_id`，读该 panel 的 `selected` + benchmark 去重得 21 对象，**禁止取最新 panel**，缺 ticker/歧义硬报错不换股）；`observation_slot`（America/New_York 17:30 首采 + 每 30 分钟补采至次日 05:30 ET，05:30–17:30 无 slot）；`missing_trading_days`（窗口内交易日对比实际 `price_observations` 行，`zero_volume` 也算有数据——HTTP 200/非空响应/created=False 都不等于齐全）；`collect_tick`（滚动最近 5 交易日，幂等键=租户+collect-v1+tiingo+security_id+日期范围+slot，同 slot 幂等、下一 slot 重查）。
  - `config.py` 加 `collect_release_id`/`collect_tenant_id`/`collect_interval_seconds`；`worker/loop.py` 的 `scheduler_fn` 组合 `scheduler_tick` + `collect_tick`（采集不依赖 campaign stop，stop_new_batches 后仍服务 D20/D60 随访）。
  - 测试：`tests/test_data_collect.py` 16 项（9 纯 slot/DST + 7 DB：named-release-not-latest、benchmark 去重、缺 release/ticker 报错、提交幂等、完整窗口跳过、缺数据下一 slot 重查）。SG PostgreSQL 真实迁移 16 passed。
- 生产 Compose 与镜像：
  - `infra/compose/production.json`（新）：postgres + core-api + core-worker，**去 Runner**（无 `YOUWEI_RUNNER_URL`）；API 绑 `127.0.0.1:8000`（SSH 隧道）；worker 挂 `egress` 网络访问 Tiingo；`read_only`+`tmpfs`+`cap_drop ALL`+`no-new-privileges`+资源限额（cpu/mem/pids）+`json-file` 日志轮转 10m×3+`restart: unless-stopped`；采集经 `YOUWEI_COLLECT_RELEASE_ID`/`_TENANT_ID` 启用。
  - `infra/postgres/init/01-roles.sh`（新）：迁移账号 `youwei_migrate`（DDL，跑 Alembic）与应用账号 `youwei_app`（DML，API/Worker 连接）分离。
  - Core 镜像：`infra/images/core.Dockerfile` 构建 `ghcr.io/youweichen0208/youwei-core`，推送 GHCR，digest `sha256:7292c8538e70750ebbd3c278ebd8572563b342ca5593981c34fc8659b9bd0e3c`；postgres:16-alpine digest `sha256:721873c3...`。验收报告 [s09a-core-image-release.md](ops/s09a-core-image-release.md)。
  - `infra/upstreams.lock.yaml`：新增 `core`（local 0.1.0）+ `postgres`（image）组件并置 `enabled:true` + `verification:passed`（evidence 指向验收报告）；Hermes/Pi/litellm/Open WebUI/OpenViking 保持 disabled/null。
  - `infra/deployment-manifest.json`（新）：`schema_version=1` + `lock_sha256` + `compose_sha256`（渲染后 Compose，占位环境变量不入秘密）+ `components` 精确匹配 enabled 组件。
- 验证：`python3 infra/validate_upstreams.py --mode catalog` → VALID；`--mode deployment --compose <渲染后> --manifest infra/deployment-manifest.json` → **VALID**（sg-prod）；`docker compose config --quiet` 通过；`git diff --check` 通过。
- 剩余限制：deployment 校验只证明文件与镜像引用一致，不替代生产运行验收（S09b：PG/API/Worker/采集/租户登记闭环）与运维验收（S09c：远端恢复、重启恢复、资源测量、告警、RPO/RTO）；Core 镜像未在生产跑通（未起 Compose 实际运行）；pgBackRest 备份目标未落实（优先复用已授权独立目标，否则 DO Spaces；同机卷/WebDAV 不构成独立故障域）；真实 GHCR 凭证已撤销（镜像按 digest 拉取仍需 read 权限 token，部署时重新配置）。

### S09b 进度（2026-10-02，隔离验收 + 生产库准备）

- 状态：**S09b 运行接线完成（2026-10-02）**：隔离验收（两轮）+ 生产库准备 + GHCR 镜像发布 + 生产 API/Worker/采集上线；期间发现并修复 token 入日志缺陷（r2 重发布）。批准与 Campaign 未发生（release_approvals 0、campaigns 0）。
- 修复 S09a 部署缺陷（5 处，比原报告多 1 处 internal 网络）：
  1. `production.json` 挂载路径 `./postgres/init` → `../postgres/init`（相对 compose 文件位置解析不到脚本）。
  2. `01-roles.sh` 的 `:'password'` 在 `DO $$…$$` 块内不插值 → `\gexec` + `format('%L', :'var')`；真实 PG 验证角色创建/密码登录/DB owner。
  3. 迁移连接 `127.0.0.1:5432`（PG 未映射宿主端口）→ 一次性 Core 容器连 internal `core` 网络 `postgres:5432`，用 `youwei_migrate` 跑 `alembic upgrade head`（实际执行：33 表、`f9a0b1c2d3e4`）。
  4. `youwei_core/api/main.py` 监听 `127.0.0.1` → `0.0.0.0`；宿主经映射端口 `127.0.0.1:8001` 访问 `/healthz` 返回 200。
  5. **（新发现）** core-api 仅在 `internal:true` 的 `core` 网络，容器端口无法发布到宿主 → 加非 internal `edge` 网络（对齐 development 冒烟经验，见 runner-compose-smoke.md）。
- 镜像：本地重新构建 Core 镜像 tag `phase1a-s09b`，image ID/索引 digest `sha256:55678d52...`、平台 manifest `sha256:0c1f2b09...`、config `sha256:f65d29fb...`。**未 push GHCR（凭证失效，push/pull 均 denied），无 registry digest**；新镜像正式发布待 GHCR 凭证恢复。
- 部署目录 `/opt/youwei/`（production + acceptance 两环境，独立 project/卷/网络/凭证/端口）；凭证目录 `/opt/youwei/secrets/`（0700），每环境独立 0600 凭证（PG 三密码 + admin key + capability secret，各 32 字节 hex，首次生成后续复用）。部署驱动 `ops/s09b_deploy.py`。
- 隔离验收（见 [s09b-acceptance.md](ops/s09b-acceptance.md)）：
  - 第一轮合成数据（mock Tiingo，`ops/s09b_mock_tiingo.py`）：**12/12**——权限（401/404/跨租户）、任务提交→worker→采集→入库、幂等重放、失败处理。
  - 第二轮真实 Tiingo（`ops/s09b_real_collect.py`）：**45/45**——SPY + 20 panel 成员最近 5 交易日，字段完整、幂等去重。验收库最终 securities 506、price_observations 110、calendar_days 3391。
- 生产库准备（见 [s09b-production-db-prep.md](ops/s09b-production-db-prep.md)）：
  - 独立生产 PostgreSQL + 真实 Alembic（本地新镜像）；`ops/s09b_import_candidate.py`（纯导入，逐项校验 + 幂等登记）导入冻结 panel/SPY/日历/training manifest/release。
  - release `release-logistic-ridge-candidate-20261002-v1` content sha256 `bdb8bbe0...` 与 S06i 记录一致；导入前校验 code_files(5)/protocol_refs(4)/panel_bundle/training_manifest hash 全部匹配；幂等重跑 created=False。
  - 正式 tenant `youwei-internal-research` = **`f497c122-45b6-497b-bb99-9c42401c3e5f`**（按 slug 幂等创建/复用）。
  - **campaign_plan_sha256** = `d403c8e553e32e554cc4b587e7f08772f253aced70d2896062a5984aab7b1d29`；**planned_cutoffs_sha256** = `59f7aa3e1303c75da6d1795f5353628ec8dc9e54b78fcd48edbce12a3b290788`；**scope_manifest_sha256** = `0b899b003c923efca10dc7de63f4921c1e87ad5223f610caa4ee602689a11805`。12 批 cutoff 与 S06j 逐项一致。
  - 保持 release_approvals 0、campaigns 0、formal_campaign_allowed=false。
- 关键注意项：`primary_metric` 正式值 **`paired_brier_quant_minus_baseline`**（S06j + v2 文件）；`register_campaign` 默认参数是 `d20_paired_brier_delta`（仅测试占位），正式注册必须显式传前者否则计划 hash 不匹配。
- 仓库同步（收尾时发现并修复）：`tests/contracts/test_upstreams.py` 的仓库 catalog 断言过期——S09a 启用 core/postgres 组件后，裸 `--mode deployment` 的报错从 "at least one enabled" 变为 "requires --compose and --manifest"（S09a 提交时未重跑该组测试）。已更新断言，意图不变：仓库 catalog 单独不构成经验证的部署。本地无 PG 测试组（pure/contracts/known_answers）183 通过；`--mode catalog` VALID。
- 收尾（GHCR 凭证恢复后，2026-10-02；见 [s09b-production-launch.md](ops/s09b-production-launch.md)）：
  - 凭证经 stdin 登录（不落命令行/日志/仓库）；推送 `phase1a-s09b` → registry digest `55678d52...`，按 digest 拉取验证通过；部署文件更新后 SG 渲染 deployment 校验 VALID。
  - 首次启动验证：healthz 200；采集窗口内 21 对象 × 最近 5 交易日真实采集（105 行、21 任务全部 succeeded）。
  - **缺陷与修复**：httpx INFO 日志打印完整 URL，token 作为 query 参数明文入 worker 日志；改为 `Authorization: Token` 头（真实调用验证 200/403，[tiingo-token-verification §8](research/tiingo-token-verification.md)），SG 真实 PG 31 测试通过，重建 r2 镜像 `phase1a-s09b2`（registry digest `53631663...`）重新发布并重部署；旧容器（含 token 日志）随重建删除，新日志 0 处 token，`raw_objects` 与数据库本就不含 token。
  - 最终状态：production.json / upstreams.lock.yaml / deployment-manifest 固定 r2 digest；SG deployment + catalog 双 VALID；`ops/s09b_deploy.py` LOCAL_CORE_TAG → `phase1a-s09b2`；生产三服务健康（restarts=0）。
- 剩余限制：S09c 运维验收（远端恢复、重启恢复、资源测量、告警、RPO/RTO、pgBackRest 目标落实）；acceptance 环境留存/拆除随 S09c 决定；下一交易日 ET 17:30 首采为头认证采集的自然确认点；组装批准包交项目所有者一次性确认。

### S09c 进度（2026-10-02 起，运维验收进行中）

- 状态：**S09c 第四批完成**（磁盘治理、重启恢复、升级/回滚 drill、采集配置持久化修复、pgBackRest 镜像+演练、告警轮询、生产备份接入、**droplet 整机重启演练**）；所有者决策（2026-10-02）：备份异地目标暂不考虑；剩余：告警 webhook URL、负载下资源复测。详见 [s09c-ops-acceptance.md](ops/s09c-ops-acceptance.md)。分段记录：
  - 磁盘治理：根分区 82% → **14%**（回收 108.9G 构建缓存 + 73 个未用匿名卷 ~4.2G；Docker 29 镜像/缓存存于系统 containerd `/var/lib/containerd` namespace moby，du docker 目录不反映占用）。
  - 重启恢复（docker 级）：stop → start 三服务按依赖门控恢复，数据完好（105 行/21 任务），采集配置保留；worker stop 超宽限被 SIGKILL（exit 137）——租约机制按崩溃安全覆盖；整机重启演练待所有者确认。
  - 升级/回滚兼容性：r2→r1→r2 drill 通过（两版对当前生产库均健康，同 alembic head 无 schema 差异）；仓库部署文件保持 r2。
  - 缺陷修复：`up -d` 重建会静默禁用采集（env 未持久）——`s09b_deploy.py` 增加按环境 `config.env`（setdefault 合并，shell 导出可覆盖），SG 已写 `/opt/youwei/production/config.env`；force-recreate 不带导出验证采集配置在位。
  - 资源基线（静默期）：worker 130MiB/768M、api 59MiB/384M、pg 36MiB/1G、内存 available 6.3G；负载下复测待做。
  - pgBackRest 镜像（第二批）：`infra/images/postgres-pgbackrest.Dockerfile`——pinned alpine 基底（16.15，musl，同基底避免 collation 迁移）+ 源码编译 pgBackRest 2.59.2；已推 GHCR digest `sha256:8d69232c...`（拉取验证通过）；未接入生产（lock 待接入时更新）。
  - pgBackRest 演练（第二批）：[ops/backup/pgbackrest_drill.sh](../ops/backup/pgbackrest_drill.sh) posix repo 全链通过——stanza/check/全量备份（30s，31MB→4MB）/PITR（A+B 在 C 不在，9s promote）/全量丢失恢复（A+B+C 全在）/alembic head；S3 repo 仅差配置，待目标决策。
  - 告警轮询（第二批）：`ops/ops_status_poll.sh` 上线 SG cron（每 5 分钟）——alert 集变化投递一次（fail closed 含 api_unreachable），本地告警日志+心跳；两态转换验证；webhook 通道待配置 `alert-channel.env`。
  - 生产备份接入（第三批）：postgres 换自定义镜像（同基底免 dump/restore，api 全程未断、数据完好）；`archive_mode=on`+`archive_timeout=60`+posix repo；stanza/check/首个全量备份 7s（32.9MB→4.3MB）；`/v1/ops/status` `wal_archive` 生产真实生效（enabled=true，4 archived/0 failed）；cron 每日 11:15 UTC 全量（保留 7）+周日 check；worker `init:true` 修 PID1 信号；观察：空闲期 XLogArchiveTimeout 不切空段（零浪费），活跃期 RPO ≤ ~1 分钟；upstreams.lock postgres 组件更新（source=local、evidence=冻结的 s09c-postgres-image-release.md）+ deployment 校验双 VALID。
  - 整机重启演练（第四批）：reboot → SSH 恢复 2 分 10 秒 → 40 秒内 9 容器全部自愈（含 webdav）→ nginx/docker/cron active、数据完好、采集配置保留、归档器恢复；**实测 RTO ≈ 3 分钟**无人工干预。
  - 告警 webhook 多格式（第四批）：`generic|slack|discord|feishu|wecom|telegram` 六格式就绪，只差 URL。
- 所有者决策（2026-10-02）：① 备份异地目标暂不考虑（同机副本保持）；② 告警通道推进中（待 URL）；③ 整机重启演练已完成。剩余：告警 webhook URL、采集高峰/首批 Campaign 负载下资源复测。

### Phase 1A 批准包（2026-10-02，r1 修订待批）

- 初版组装（`docs/ops/phase1a-campaign-approval-20261002.md`）：release `bdb8bbe0…` + 计划 `d403c8e5…` + scope_manifest `0b899b00…`，全部断言对照生产库与 make_plan 重算核验；所有者审阅后**暂不批准**，指出三项问题（审阅意见不构成批准记录）。
- r1 修订（同日，hash 集不变）：
  1. **历史回补（原阻断项）**：`ops/backfill_history.py` 经标准 ingest 路径回补 2026-06-01 → 2026-10-01 共 86 个已完成交易日 × 21 对象（零缺口对照冻结日历；`ingested_at`/`usable_at` 为实际时间，均早于首个 cutoff）；生产库 price_observations 105 → 1911（版本化重叠按 PIT 读取取最新）。
  2. **可评分性验证**：`ops/verify_prediction_coverage.py`（只读）按批次同路径（build_release_predictor + daily_bars_asof + predict_case）验证——模拟 cutoff 2026-10-01：**60/60 quant 预测可用**；批次 1 窗口（61 session，2026-07-16 → 10-09）已覆盖 55，余 6 个（10-02 → 10-09）由滚动采集在 cutoff 前落地。
  3. **随访时间线修正**：末批入场 2026-12-28、D60 出场 2027-03-24 收盘、宽限期至 2027-04-01 收盘（含 2027-03-26 耶稣受难日休市，按冻结日历重算）；采集与随访覆盖至此之后。
  4. `approve_release` 示例补必填 `scope` 审计文本参数。
- 状态：待所有者对 r1 的明确批准；批准后按包内 §6 机制执行（approve_release → register_campaign 显式 primary_metric）。

### Phase 1A 批准执行与 Campaign 注册（2026-10-02，所有者批准后）

- **所有者批准**："批准"（2026-10-02，对 package r1 的 hash 集）。`ops/approve_phase1a_campaign.py` 执行（一次性容器，三项 hash 钉死校验通过后写入）：
  - `release_approvals`：approver `human-owner`、release content `bdb8bbe0…`、**scope_sha256 `0b899b00…`**（结构化 scope 绑定 tenant/campaign_key/release/plan hash）、basis "phase1a-pilot-2026q4 one-time approval 2026-10-02 (package r1)"。
  - `campaigns`：`phase1a-pilot-2026q4` = `a63f8494-4730-4cc7-bf2d-0b29e895aa3c`，active，primary_metric `paired_brier_quant_minus_baseline`（显式传入），12 批 planned_cutoffs 入库；服务端从实际参数重算 plan hash `d403c8e5…` 校验通过。
- 调度：`next_weekly_cutoff` 语义为"严格晚于现在的首个周六 06:00 ET"——批次 1（cutoff 2026-10-10）将于 2026-10-03 06:00 ET 后预注册，2026-10-10 06:00 ET 窗口内冻结输入、生成并原子封存预测。特征历史已回补且 60/60 可用（见批准包 §4.1）；cutoff 前余 6 个交易日由滚动采集覆盖。
- 状态：生产库 release_approvals=1、campaigns=1、forecast_batches=0（待调度器预注册）；ops status ok 无告警。批准包文档按定稿冻结不作修改，执行记录以本节为准。
- 后续：批次运行与随访（采集/结果随访覆盖至 2027-04-01 之后）；D1/D20/D60 标签成熟后评分；月度汇总按协议追加。

### 聊天栈部署与 S07 收尾（2026-10-02，LiteLLM + Open WebUI + agent-runtime 发布）

- 所有者决策（2026-10-02）：火山 key 复用本地 pi 配置；子域 `trading.youwei-agent.com`；公网入口拟走国内阿里云 ECS（cn-shanghai）中转（**备案问题待确认**：未备案则非标端口或 SG 直连）；聊天不设预算上限；本轮范围 = S07 收尾 + 聊天入口，Phase 1B/S08 不动。
- **youwei-chat 栈**（sg-prod `/opt/youwei/chat/`，[chat-stack-deployment.md](ops/chat-stack-deployment.md)）：postgres（LiteLLM 专用库，复用 pinned digest）+ LiteLLM v1.102.1（`ghcr.io/berriai/litellm@sha256:f8043697…`，火山 Anthropic 兼容端点 + Bearer，5 模型，spend logs，`127.0.0.1:4000`）+ Open WebUI v0.6.36（`@sha256:0b73f17a…`，指向网关，`127.0.0.1:8090`，注册暂开放待所有者建号后关闭）。仓库资产：`infra/compose/chat.json`、`infra/chat/litellm-config.yaml.template`、`ops/deploy_chat.sh`（幂等分相部署）。
- 验证：5 模型列表、真实 glm-5.3 正文调用（finish=stop）、deepseek-v4-flash 流式 168 SSE chunk、spend logs 入库、chat 虚拟 key 隔离、Open WebUI /health 200。
- **agent-runtime 镜像发布**：`ghcr.io/youweichen0208/youwei-agent-runtime:phase1a-s07`，registry digest `sha256:998f060e…`（与 S07m 本地 digest 一致），digest 拉取验证通过；upstreams hermes notes 更新（deployment.image 仍 null，归 Phase 1B 决策）。
- upstreams：litellm 与 openwebui 组件升级为 integrated + passed（digest + 证据 = chat-stack-deployment.md），enabled 保持 false（部署校验器只覆盖研究生产 compose；启用待 S07 研究链路接线决策）。catalog + deployment 双 VALID。
- 公网入口上线（2026-10-02）：`https://trading.youwei-agent.com`（Cloudflare 灰云 DNS：`trading A 8.159.158.155`、`sg-chat A 168.144.39.34`；两端 certbot 证书有效至 2026-12-31、systemd 续期在位；公网 `/health` 200 全链验证；SG vhost 对非 ECS 来源 403，allow/deny 置 location 级避免挡 ACME）。所有者建号完成、`ENABLE_SIGNUP=False`（signup 403 验证）。
- 剩余：聊天栈备份策略（当前可重建 + openwebui 数据卷未纳入 pgbackrest，重要时可加 dump cron）。agent-runtime 修复镜像已重发布 GHCR（`phase1a-s07-r2`，digest `bca0a5b9…`，2026-10-02）。

### S07n 真实网关研究链路验证（2026-10-02，agent-runtime → LiteLLM → 火山）

- 状态：**验证矩阵完成，全部实测**；发现并修复 6 项缺陷（其中 Runner 超时容器泄漏为关键项）。详见 [s07-real-gateway-verification](ops/s07-real-gateway-verification.md)（含全部证据与复现脚本）。Phase 1B 数据转发授权未取得——E2E 用合成证据，不含真实研究数据；模型选择为管线验证用车，不构成研究角色定稿。
- 拓扑（临时）：`youwei-research` internal 网络（172.27.0.0/16）+ litellm 以别名 `litellm` 接入（`docker network connect`，容器重建即失效，Phase 1B 需声明式化）；网络探针验证 DNS 解析、网关可达、外网/外部 DNS 阻断、chat 栈隔离（S07m 遗留 DNS 项关闭）。
- 矩阵结果：工具调用流式 PASS（增量重建+往返）；E2E PASS（Runner → 容器（`bca0a5b9…`）→ LiteLLM → 火山，glm-5.3 返回合法 unavailable 提案——正确识别合成证据无 benchmark 数据）；用量逐 token 一致（网关 4 请求 prompt/completion 合计 = session_delta 19703/8071）；辅助调用已观测（真实模型每回合多 1 个无 tools/messages 请求，计入 session 计数器——修正 S07j 离线结论）；取消传播成立（客户端中断后 5.5s 终止上游，completion=0；Runner 击杀同机制）；max_parallel_requests/rpm/tpm 分别 PASS（额外发现：TPM 准入前按预估用量检查）；**fail-closed 实测为 FAIL-OPEN**（已缓存 key 在计数库不可达时照常准入；限额为 DB 支撑，故障期间推定不可执行）。
- 工具面安全：Hermes Tool Search 桥（tool_search/describe/call 元工具）实证——桥内请求 `terminal` 被拒、直接伪装工具名被会话校验拒、平台工具经桥/直接调用均正常分发且 contextvar 授权链完整。
- 缺陷修复（全部 TDD 先红后绿）：①简报缺响应格式契约（真实模型散文回答 → parse 失败；补 JSON 格式段含 warnings 对象形状与值域纪律）；②提案 model 归因信模型自报（改为运行时配置注入）；③无输出上限（glm-5.3 撞 API 默认 max_tokens 截断；`max_output_tokens=16384`）；④**Runner 超时只杀 docker 客户端、容器泄漏并继续调网关**（改为按容器名 `docker rm -f`，假 docker 回归测试 4 项 + SG 真实 Docker 复验）；⑤失败诊断被 banner 淹没（优先取 stdout 结构化错误）；⑥smoke 硬编码旧子网 IP。
- 新增资产：`research_real_gateway.py`（链路级 E2E 驱动，含超时击杀模式）、`gateway_matrix.py`（网关级矩阵）、`research_net_probe.py`（网络探针）、`bridge_scope_probe.py`（工具面越权探针）、`ops/gw_failclosed_drill.sh`（并行栈 fail-closed 演练，不碰生产聊天栈）；验证用研究 key 四枚（e2e/conc/rate/tpm，存 sg-prod 0600 文件）。
- 验证：agent-runtime 3.14 → 53 passed（新 4）；本机 pure+contracts → 130 passed（新 4）；新镜像对 S07m 既有路径零回归（container_smoke 5/5、mock END_TO_END OK）；catalog + deployment 双 VALID（lock/manifest hash 级联）。
- 剩余限制：接线为临时性（litellm 接入 youwei-research 为 `docker network connect`，容器重建即失效，Phase 1B 需声明式化）；fail-closed 语义如需收紧需另评估；单回合延迟 84-224s，Phase 1B 批量 60 case 需并发设计；S08 工具往返契约与真实证据转发仍待后续。修复镜像已重发布 GHCR（`phase1a-s07-r2`，digest `bca0a5b9…`）。
