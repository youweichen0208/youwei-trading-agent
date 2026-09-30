# 项目文档

当前架构版本：v0.3，S00 参数于2026-09-27登记。使用范围已确认自用或受控内部研究，不自动交易、不向公众提供服务或对外提供投资建议。目标为中国大陆入口与新加坡核心；现有 SG 主机为 DigitalOcean 4 vCPU / 7.8 GB。

Core 已有持久任务、PIT 快照、Ledger、结果回填与预测管线的开发纵向切片；独立 Runner 已通过本地跨进程测试和 Compose 联调。外部 Agent、国内入口及目标机生产运维仍待各自接入与验收，完成状态查实施计划。旧原型已归档移除；OSS 已退出 MVP，本地归档与恢复演练不代表独立防篡改或生产恢复目标已经实现。

## 阅读顺序

| 顺序 | 文档 | 用途 |
| --- | --- | --- |
| 1 | [技术架构 v0.3](ARCHITECTURE.md) | 当前设计依据：职责、预测协议、数据、Memory、执行、安全与部署 |
| 1a | [仓库边界与接入方式](REPOSITORY.md) | 修改模块、依赖或部署边界时读取：单业务仓库、纯量化、共享契约、Runner 及上游组件的职责 |
| 1b | [上游版本与部署校验](UPSTREAMS.md) | 上游登记、独立依赖锁、开发 Compose、镜像与清单验证、升级及回滚 |
| 2 | [修复与实施计划](IMPLEMENTATION_PLAN.md) | 按 S00–S11 推进的任务、依赖、交付物和验收标准 |
| 2a | [S00 协议登记](protocols/README.md) | 已确认的目标/时间/批次政策、已知答案、参数/hash 与 S01+ 阻断项 |
| 2b | [Trial Registry](trials/registry.md) | 真实模型试验的追加登记载体，目前无试验 |
| 3 | [v0.2 评审记录](ARCHITECTURE_REVIEW_v0.2.md) | 保存问题背景、反例与取舍，问题编号关联实施计划 |
| 4 | [Hermes / Pi 运行时核实](research/hermes-pi-runtime-verification.md) | 官方能力、限制、来源及目标环境待验证项 |
| 4a | [S01 研究文档](research/) | 运行时版本固定、Tiingo 能力、gVisor 量化栈、LLM Gateway 选型 |
| 4b | [运维记录](ops/) | 备份/PITR、[结构验证](ops/structure-verification.md)、[Runner Compose 联调](ops/runner-compose-smoke.md)与生产部署要求 |

## 文档使用规则

- 当前设计以 ARCHITECTURE.md 为准；目标、时间与评估协议以 docs/protocols/ 登记版本为准；实施顺序、实际验证和完成状态以 IMPLEMENTATION_PLAN.md 为准。
- 评审记录保留原问题语境，不作为另一份运行规范。
- 运行时资料说明核实时的官方能力；固定上游版本、安装冒烟、业务接入和生产部署是不同状态，分别保留证据。
- “设计已合并”与“工程已验收”分别记录。首次正式 campaign 前落实最小 release、人工批准、trial 与时间协议。
- 修改目标、数据、prompt、模型、工具、记忆或降级规则时，同步更新受影响的设计与发布版本。

领域术语见 [CONTEXT.md](../CONTEXT.md)。供应商选择、实际20证券集合、模型版本及恢复能力需要后续实际记录，不能根据文档占位值视为已经确定；批准人已指定为项目所有者本人，实际 release 仍需绑定具体 hash 批准。
