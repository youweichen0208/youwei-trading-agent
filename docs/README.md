# 项目文档

四仓共同组成个人助手与美股研究平台：WebUI 管登录和聊天，trading-assistant 管个人 Hermes、插件与知识，trading_core 管独立金融查询与计算，平台管 Core、Runner、研究、评估与部署登记。个人知识不自动成为正式研究输入。

| 入口 | 用途 |
| --- | --- |
| [项目入口](../README.md) / [Agent 指引](../AGENTS.md) / [领域术语](../CONTEXT.md) | 定位、修改规则与共同语言 |
| [开发](DEVELOPMENT.md) | 独立环境、测试与本地运行 |
| [运维](OPERATIONS.md) | 发布、备份和恢复入口 |
| [状态](STATUS.md) | 当前证据及未完成项 |
| [架构](ARCHITECTURE.md) | 当前职责、调用链、数据与权限 |
| [仓库边界](REPOSITORY.md) | 模块、依赖环境及测试入口 |
| [实施计划](IMPLEMENTATION_PLAN.md) | 当前状态、Sxx 索引、后续节点 |
| [上游管理](UPSTREAMS.md) | 固定版本、候选与已部署组合、升级验证 |
| [个人助手运维](ops/hermes-personal-assistant.md) | WebUI 接线、跨服务验收、备份恢复 |
| [登记协议](protocols/README.md) | 正式目标、截止时间、批次及评分语义 |
| [Trial Registry](trials/registry.md) | 真实试验与选择过程 |
| [历史归档](archive/README.md) | 旧架构、替代方案和逐次完成记录 |

事实与设计要求分别记录；测试、候选构建、目标机验收和生产部署分别报告。Hash 绑定的协议和证据保持原位置与内容，后续改变遵循其版本流程。领域术语见 [CONTEXT](../CONTEXT.md)。
