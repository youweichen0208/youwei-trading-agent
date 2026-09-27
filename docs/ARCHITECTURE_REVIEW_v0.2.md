# v0.2 架构评审与优化建议

日期：2026-09-26\
评审范围：用户提供的设计文本、Hermes/Pi 官方文档及相关基础设施资料。工作目录尚无应用实现，本次未做代码审查、性能测试或 ECS 部署验证。

评审修复已合并至 [v0.3 架构](ARCHITECTURE.md)，工程实施与验收待完成。各问题对应的任务见[修复与实施计划](IMPLEMENTATION_PLAN.md)。本文件保留 v0.2 的问题背景，不作为当前运行规范。框架证据和待验证项见 [Hermes/Pi 运行时核实](research/hermes-pi-runtime-verification.md)。

## 1. 判断

v0.2 的事实/经验分离、LLM 前向评估、基线、PIT、只追加 Ledger、快照和人工发布值得保留。但其“可信评估”和“安全执行”仍有数个定义缺口，按照现稿直接实现，会得到能够生成报告却未必能证明效果的系统。

最高优先级是把数据时间、预测提交、样本完整性、记忆生效与结果更正转成代码可检查的不变量。同时压缩服务和 Agent 循环数量。

P0 表示正式评估前必须修复；P1 表示 MVP 部署或相关能力启用前必须明确。

## 2. 高影响问题

### P0-1：AS_OF_TIME 不等于预测封存时间（§11、§13.1）

反例：09:29 开始研究，09:39 完成，仍以 09:30 开盘作为入场价。即使输入声称截至 09:29，预测也没有在入场前留下不可修改的记录。

修复：拆分 decision_cutoff、sealed_at、prediction_deadline、entry_at、exit_at，满足 cutoff <= sealed_at <= deadline < entry_at。入场与期限在 campaign 前固定；迟到保留失败记录，不能悄悄顺延或补写。

### P0-2：available_time 混合了两种历史（§11.1）

反例：2026 年回补 2024 年财报，并赋予 2024 年 available_time，之后重查“2024 年系统输入”便会出现当时尚未获取的记录。发布时间也不证明数据内容就是当时那个版本。

修复：区分 source_available_at 与系统实际 usable_at，保存源版本及接收记录。历史量化重建与真实前向运行分别标识；旧预测只引用旧快照，禁止通过现查数据冒充复现。consensus、forward PE、IV、新闻修订与模型训练标签也必须遵守 PIT。

### P0-3：Memory 的状态和正文都可绕过验证（§12.3–12.6）

两条独立漏洞：

- 9 月 1 日创建 hypothesis、9 月 20 日改为 validated；按 created_at 查询 9 月 10 日会读到后来批准的状态。superseded_by 同样可能改变过去的可见性。
- postmortem 可召回，正文却含 candidate_lesson；即使独立 Lesson 仍为 hypothesis，建议也已进入正式预测上下文。

修复：内容版本与状态事件分别追加，按 as_of 重建当时状态。候选规则和原始复盘进入实验区；正式预测只使用批准的上下文。人工批准从第一次使 Lesson 生效时实施，不推迟到 Phase 2。

### P0-4：固定股票池仍有缺失选择偏差（§13.3–13.4）

高波动或复杂事件更容易使 Agent 失败。若只评估成功记录，剩余样本依然偏置。“无偏样本”不应由定时股票池直接推导。

修复：任务运行前创建 cohort/campaign manifest 与每个 forecast case；失败、迟到、降级都保留。按同证券、同 horizon、同窗口的 case 配对三组预测，报告全量 coverage 和实际降级政策表现。三个 horizon 对应九条预测，run_id 单独不足以配对。

### P0-5：Outcome 的主键不支持追加更正（§13.5）

prediction_id PRIMARY KEY 限制每条预测只能一条 outcome，与“回填出错追加更正”冲突。之后补 postmortem_ref 也要求 UPDATE。

修复：OutcomeRevision 使用独立 ID、revision、supersedes、计算版本与更正原因。真实结果按 case 保存，三个 source 共享；复盘独立引用结果。Brier/MAE 等由评估层计算，报告锁定结果版本，修正后另出新报告。

### P0-6：Agent 调度没有持久执行语义（§6、§8、§17）

PG 写任务成功、Redis 投递失败时如何恢复？执行成功但 ACK 丢失怎么办？旧 Worker 超时后回来，是否仍能写预测？仅有 job_id 幂等没有回答这些问题。

修复：Hermes 保持研究 Supervisor；确定性 Workflow Controller 管理租约、步骤、预算、期限、幂等及最终入账。MVP 用 PG 持久任务和事务 outbox；保留 Redis 时也不能绕过 outbox。PG 官方支持使用 SKIP LOCKED 处理队列消费者，但它不是完整工作流引擎。[PG 文档](https://www.postgresql.org/docs/16/sql-select.html)

### P1-1：MAE 与“期望收益”的预测目标不匹配（§13.7）

expected_excess_return 表示条件均值，主要采用 MAE 会奖励条件中位数。均值应主要用 MSE/RMSE；q50 可用 MAE，分位数用 pinball loss，区间覆盖率需结合区间宽度或适当评分。该原则来自预测评分的原始研究。[Gneiting，Making and Evaluating Point Forecasts](https://arxiv.org/abs/0912.0902)

### P1-2：Lock-box 与 DSR 不能作为通用验证闸门（§15）

使用后的历史数据不能靠重新划区恢复独立性。LLM 还可能从训练数据、新闻、记忆或生成策略中接触历史结果，限制某个数据库权限不足以解决。

修复：量化策略做历史验证；prompt、Lesson、LLM 调整等冻结为候选版本并等待未来 cohort。预登记主指标、查看次数和停止规则。DSR 面向 Sharpe 选择问题，不能证明 Brier 或任意 Lesson 有效。[DSR 原始论文](https://doi.org/10.2139/ssrn.2460551)

### P1-3：标准分析不需要每次经过 Pi Agent（§7.4）

已经固化为 quant.events 的 event study，再让 Pi 理解并选择调用，会增加延迟、成本和非确定性。

修复：标准任务直接调用版本化量化入口，并在受限批处理环境运行；只有探索代码任务才启动 Pi。Extension 仍可复用相同业务能力，不承载第二份数据逻辑。

### P1-4：沙箱保障与产物处理说得过满（§16）

无密钥、无网络只降低部分风险。恶意代码仍可把输入数据写入输出，生成带路径穿越的归档、同源可执行 HTML 或消耗资源。持 socket 的 Runner 仍是宿主高权限主体。

修复：Runner 固定模板、资源限额、网络和路径；输出按不可信输入校验、隔离展示。Pi Extension 本身是可信部署代码，不能把模型生成的扩展加载到宿主。gVisor 仍依赖运行配置和宿主资源策略。[gVisor 安全模型](https://gvisor.dev/docs/architecture_guide/security/)

### P1-5：网络与存储职责没有闭合（§5、§9、§16、§21）

- 国内承诺保存身份和 tasks，却只有 Redis，没有持久身份/镜像库；
- collector 可以访问外网，但数据源调用与密钥又被规定仅属于 Data Service；
- pi-worker 上传 OSS、备份归档、SG 回调国内，都需要原出口矩阵没有指定的主体；
- agent-gateway 加入普通 edge 网络，不能再声称只有列出的三个 egress 服务能出网；
- internal network 内部成员仍可互通，不是租户隔离或细粒度访问控制。

修复：CN 增加小型 PG；collector 并入 Data Service 模块；明确供应商、OSS、模型和备份各自出口。MVP 以 CN 拉取持久事件取代反向回调，减少跨境协议面。[Docker 网络说明](https://docs.docker.com/reference/compose-file/networks/)

### P1-6：真正的变更单位大于 strategy_version（§15、§18）

prompt、LLM 路由、特征、召回规则、validated Lesson 集合、超时降级都能改变预测。只审批策略代码不足以阻止隐式进化。

修复：引入 research_release manifest，组合所有行为版本；每个 campaign 绑定一个 release。改动须回归、登记和按影响前向验证，批准人与报告一起留痕。

### P1-7：多租户与授权不能由模型参数保证（§12、§19）

scope、tenant_id 和 snapshot_id 是请求参数，不是权限证明。等到 Phase 3 才做隔离可能早于真实第二租户进入。

修复：从登录主体派生租户权限，发放限 job 的能力令牌；在实际多用户/多租户开放前落实数据库和产物隔离。RLS 应用账号不得具有 owner/superuser/BYPASSRLS 等绕过能力。[PG RLS 文档](https://www.postgresql.org/docs/16/ddl-rowsecurity.html)

### P1-8：不可篡改与可复现需要限定承诺（§13、§18）

DB owner 仍可重写数据或关闭防护；单纯自存 hash 也可整体重算。每日 WORM 锚定之前存在信任窗口。LLM 即使给相同输入也不保证相同输出。

修复：明确威胁模型、链序号/锁/规范化方式、外部锚定频率及独立归档桶；承诺复现输入和量化结果，保存实际 LLM 输出。OSS BucketWorm 作用于整桶，不应假定能只对目录开启。[OSS 官方说明](https://www.alibabacloud.com/help/en/oss/user-guide/oss-retention-policies)

## 3. 方案取舍

| 调整 | 收益 | 代价/限制 |
| --- | --- | --- |
| PG 持久任务替代 MVP Redis 队列 | 减少跨存储双写，任务与结果易于原子提交 | 需要正确实现租约、重试、清理；不能只写一个轮询循环 |
| CN 主动拉取事件 | 单向跨境请求，降低回调运维复杂度 | 引入可配置的轮询延迟 |
| 标准 quant 跳过 Pi | 成本低、可测试、输出稳定 | 新分析需先扩充 quant 库，探索才走生成代码 |
| 六个 SG 应用部署单元 | 减少服务数量，保留权限与资源隔离 | 单机仍是故障域，运维需实测恢复 |
| 冻结 release 和记忆集 | 防止隐式变化污染评估 | 学习不能即时自动影响正式预测 |
| MVP 一个 horizon、10–20 个证券 | 更早跑通前向留痕和结果回填 | 只能验证管线，统计结论需更长时间与足够样本 |

上表保存最初的评审取舍。2026-09-27 的 S00 登记已将规模定为20证券、D1/D20/D60且D20为主；实际配置以 [S00 协议](protocols/README.md)为准。

不建议立即增加 Temporal、Kafka、Kubernetes、向量数据库或更多 Agent。先通过故障恢复、预测截止时间、样本完整性和收益已知答案验收，再按观测到的需求替换实现。

## 4. 先实施的工作

本节顺序已与 v0.3 同步，具体任务和验收以[实施计划](IMPLEMENTATION_PLAN.md)为准。

1. 固定 TargetSpec 与 campaign 协议，验证数据源和运行时，写出收益及截止时间的已知答案。
2. 建立持久任务、权限、预算、幂等、故障恢复与受限执行基础。
3. 跑通 Snapshot → baseline/quant → Ledger → Outcome，开始积累真实前向数据。
4. 接入 Hermes 与受控 Pi 探索任务；新的三组对照批次按 case 封存，prompt 回归从首次发布就记录。
5. 完成两地入口与完整 MVP 验收，再扩展评估界面、Lesson 和候选晋级。

v0.3 保留原本的长期方向，但把第一阶段的价值收敛为：一次研究有可靠证据、一次预测在入场前封存、一次评估能解释所有计划样本的去向。
