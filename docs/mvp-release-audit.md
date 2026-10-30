# MVP 发布缺口核对

核对时间：2026-10-30。范围为架构任务书 §1.2、§6.2、§15.1。当前为**未取得发布资格**；本表不以测试数量、已启动任务或行数计算完成百分比。下列“本机证据”只证明其运行范围，不能自动替代供应商、部署或完整 DoD。

上一 goal 状态：**blocked**。当时接入条件连续三轮未补齐，检查仍只有开发 REPLAY 配置、无供应商/staging 环境变量、无 `.git`。用户随后设置新 goal“请最快完成mvp落地”，恢复后的三轮核对仍无接入信息，现再次为 blocked；本机可访问服务已恢复并保留运行，但供应商及凭证本地路径、staging SSH 别名/域名、远端仓库地址仍需提供。重新启动 goal 不缩减原 MVP 范围，也不声明剩余实现或验收已经完成。

| §15.1 门槛 | 本机已有证据 | 尚需证明 / 下一动作 |
| --- | --- | --- |
| 1. Hermes 锁版本、未知执行不重发 | 锁定镜像，真实 pi 工具链，持久化 admission/游标，空白名单及混合批次拒绝非法工具 | 六类能力总门槛仍 UNVERIFIED；逐项对齐正式部署，禁止用本地功能成功解除门槛 |
| 2. 私网、鉴权、CSRF、密钥隔离 | 封装 BFF/Alpha/工具无公开端口，浏览器同源，独立 Gateway token，配置错误拒绝 | 实际 staging 网络、HTTPS、权限和安全审查 |
| 3. NVDA、日线、单 Profile、5 日 | 标准配置工厂固定 alpha-orchestrator，浏览器 NVDA 五交易日流程 | 目标部署的全部公开入口范围复核及依赖验收 |
| 4. 流式、取消、重连、历史、不重发 | 真实模型历史、796 条事件重放；取消/未知执行持久化测试 | 新标准配置运行受主机休眠干扰，未观察到首轮超时前实时完成；部署异常矩阵及正常时延 |
| 5. 真实行情、指标及口径 | Massive 候选适配器，数据拒绝/指标/日历参考测试 | 供应商选择和授权、两年实际样本、收盘口径/公司行动/日历资格；**Alpha 的 LIVE 快照加载仍明确返回 DATA_UNAVAILABLE，真实加载代码尚未接通** |
| 6. 真实工具驱动 Canvas | pi 真实模型、两个 Alpha 回执、报告核验，Canvas 与草稿流程 | LIVE 数据与目标部署下相同证据链 |
| 7. 完整预测协议及可追溯 | Decimal、会话、草稿确认、固定快照、真实 PG 与 REPLAY 冻结 | 真实行情/交易所资格、真实代码身份、LIVE 时间窗口验证 |
| 8. 不可变、幂等、同草稿并发 | 47 个不可变保护核对、真实 PG 并发与恢复、浏览器确认 | 部署运行角色与迁移 owner 分离、目标库验收；本地通过不代表远端权限已配置 |
| 9. 结算异常及版本保留 | 领域/PG 的到期、等待、拆股、不可评分、重试、修订测试 | 真实数据接入 Jobs、实际调度及来源证据；停牌/退市不能由缺数据推断 |
| 10. 历史/报告/审计/无伪概率 | 封装浏览器结算、历史、来源、版本；报告与不可评分测试 | LIVE 记录和部署范围验证，真实数据下统计与审计 |
| 11. 数据模式隔离与 fixture | REPLAY 标识、真实模型不等于真实行情、模拟结算单列 | LIVE 接入后继续验证不能降级为 fixture，并核对部署入口 |
| 12. CI/E2E/安全/staging/回滚/备份 | 204 项 TS、此前 66 项浏览器与 843 项 Python、真实 PG 备份恢复、封装流程 | 远端仓库/CI/Review、staging HTTPS、部署回滚、调度备份和监控 |
| 13. 可复现、延迟、成本、积压 | 固定镜像/源码哈希，备份重算、请求配额、成本算术与 Jobs 控制 | Git SHA 当前仍需真实来源；成本样例 UNCONFIGURED，需实际预算/费用；正常时延及部署队列/陈旧数据监测 |

## 本轮实际检查到的外部条件

- 项目 `.env` 只有开发/REPLAY、数据库与 Alpha 配置项；检查仅输出字段名，未输出密钥。没有环境变量名称表明已配置 Massive/Polygon/Alpaca 或 staging 接入。
- `~/.ssh/config` 存在，但当前别名没有明确指向本项目 staging。未把名称含 prod 的主机当作 staging，也未登录或修改远端服务。
- 当前目录没有 `.git`。已连接 GitHub 按 `youwei-trading-agent` 搜索返回空；这不证明其他名称的仓库不存在。
- 已请求用户提供：选定供应商及凭证本地路径、staging SSH 别名/域名、远端仓库地址。无需在聊天提交密钥。供应商许可与实际预算不能由代理自行假定。

这些外部条件尚未齐全，但也不能把剩余工作全部归为“只差配置”：LIVE 快照/结算来源接线、供应商资格验证和正式环境 Hermes 门槛仍有实际工作。得到接入信息后按真实数据→LIVE 研究/冻结→Jobs→部署验收推进；真正五交易日前瞻结算需等待目标交易日，不能用模拟时钟替代。

### LIVE 接线的代码依赖复核

`massive.Acquisition`、`AcquisitionStore._manifest()` 和数据湖 `Manifest` 均限定为 UNVERIFIED；迁移 `009_provider_acquisitions.sql` 同样只接受该值，且事实不可更新。因此不能通过改配置、更新旧事实或把原始采集当成合格快照来启用 LIVE。`api.py:load_market_snapshot()` 的真实分支仍拒绝服务，`jobs.py` 的真实分支仍选择 `UnavailableProvider`。下一项实质工作依赖已选供应商的真实响应和资格证据：建立可追溯的合格快照来源，再同时接研究与结算。当前没有供应商选择/凭证路径回复，不凭空声明资格、添加虚构授权或重复生成另一套 fixture。

## 证据入口

- [标准入口真实模型与封装浏览器](../infra/verification/hermes-configured-browser-observation.json)：1 请求、2 回执、1 预测/1 模拟结算；包含主机合盖休眠记录，不作为正常时延样本。
- [当前候选镜像与源码哈希](../infra/verification/mvp-candidate-observation.json)：本机候选，未取得发布资格。
- [Hermes 锁文件](../infra/hermes/source-lock.json)与[运行证据](../infra/hermes/runtime-observation.json)：总门槛仍 UNVERIFIED。
- [原全量 Python 与恢复证据](../infra/verification/archive/pre-current-ui-candidate/mvp-candidate-observation.json)：843 项、49 表、47 保护、46 预测及 20 结算重算；后续 Python/SQL 未改，TS 配置改动另有验证。
- [成本核对](../infra/verification/cost-budget-observation.json)：实际预算和费用未配置，算术测试不能替代账单证据。
- [行情候选研究与资格要求](market-provider-research.md)：公开文档研究，不是授权或真实采集通过。
