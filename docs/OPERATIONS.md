# 运维入口

平台统一管理部署组合与验证证据；各能力仓维护自己的源码、测试和构建。执行操作前核对目标机、当前镜像、配置与持久卷，不从文档示例推断实时状态。

| 场景 | 操作依据 |
| --- | --- |
| 固定上游、候选和发布检查 | [上游管理](UPSTREAMS.md) |
| 个人助手、WebUI 与工作台 | [个人助手手册](ops/hermes-personal-assistant.md) |
| WebUI 加载性能、入口缓存与恢复 | [入口切片验收](ops/webui-performance-20261005.md)、[热加载验收](ops/webui-warm-loading-20261005.md) |
| 免费金融包上线与恢复证据 | [金融部署记录](ops/trading-core-20261005.md) |
| 目标机资源与历史环境 | [目标机记录](research/s01-target-verification.md) |
| gVisor 与受限执行 | [沙箱验证](research/gvisor-quant-stack.md) |
| 数据库恢复机制 | [PITR 演练](ops/backup-pitr-drill.md) |
| 当前交付与未完成项 | [实施计划](IMPLEMENTATION_PLAN.md) |

升级前准备固定源码、wheel hash、依赖锁、registry digest 和兼容证据，备份当前配置及数据，并在隔离副本验证恢复。金融版本切换只更新消费该包的助手与同镜像技能服务，按对应发布记录保留网络、密钥和数据卷。

秘密只在受控配置中注入；SEC 联系标识使用服务端 `TRADING_SEC_USER_AGENT`，文档不记录实际邮箱。源码提交、catalog 检查、候选镜像与 mock 通过均不等于生产已切换。

失败恢复切换前镜像和匹配配置；涉及 WebUI 数据迁移时按副本恢复流程处理，不覆盖正式 Ledger。现有同机备份和本地 PITR 演练不证明独立故障域恢复或外部锚定完成。
