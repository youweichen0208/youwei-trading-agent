# 实施计划与交付状态

更新：2026-10-05。领域语义见 [CONTEXT](../CONTEXT.md)，设计见 [架构](ARCHITECTURE.md)，代码职责见 [仓库边界](REPOSITORY.md)。历史计划和逐次完成证据集中于 [历史实施记录](archive/implementation-through-20261004.md)，其中旧 Pipe、旧版本、预算方案及当时的待办不再作为当前操作指令。

## 当前状态与后续节点

- 四仓源码职责已拆分；根据 2026-10-05 金融工具部署记录，聊天链路为 youwei-webui v0.11.4 → 官方 Hermes v2026.9.24 个人助手 → LiteLLM / trading_core / Core / EODHD MCP。具体提交、镜像及数据路径以 [上游登记](UPSTREAMS.md) 指向的已部署组合为准。
- Phase 1A 已按所有者批准登记，首个 cutoff 为 2026-10-10 06:00 ET。正式预测仍遵循批准 release、既有代码例外与封存守卫；个人助手重构不启用 Phase 1B。
- 源码、测试、候选、目标机与生产结果按各次完成记录分别验收；S12p 已部署，不据此宣称真实付费模型或正式前向评估通过。后续候选也不能继承历史部署状态。

## 任务索引

以下状态区分工程实现与正式业务验收。原任务验收细则和逐次命令保留在历史实施记录对应 Sxx。

| 任务 | 当前职责与交付状态 | 后续约束 |
| --- | --- | --- |
| S00 | 目标、时间、批次、评分协议已登记 | 已绑定 hash 的语义修订创建新版本 |
| S01 | 运行时、供应商、目标机与 gVisor 可行性已有记录 | 新环境或上游升级重新验证 |
| S02 | 持久作业、租约、身份、幂等、事件已实现；预算账本已按决定退出 | 保持事务与 fencing；迁移链保留 |
| S03 | 独立 Runner、受限计算、产物校验已实现 | 权限和网络按执行角色隔离 |
| S04 | PIT 日线、版本、授权与冻结快照已实现 | 正式输入截止时间由 Controller 管理 |
| S05 | Ledger、Outcome 修订、评分与归档已实现 | 历史封存不可重写 |
| S06 | Phase 1A 已批准登记；批次预注册已核查 | 等待首个 cutoff 与真实标签，不提前宣称预测效果 |
| S07 | Hermes 研究适配、受控调用、用量和模型归因已有验证 | 正式 Phase 1B 仍需发布流程；最小归因补丁保留 |
| S08 | Hermes 实验实例、Runner 工具面、持久回执、Controller 接纳及隔离 mock 已实现 | Pi 暂缓；生成代码继续经受控 Runner |
| S09 | 平台及聊天部署、备份、恢复和运维演练已有记录；聊天国内入口性能切片已部署 | 热加载目标、独立故障域备份等未完成能力不能从同机演练推导 |
| S10 | Dashboard 只读报告、案例和评估视图已实现 | 正式评估等待成熟标签；研究 Memory/审批扩展另验收 |
| S11 | 受控进化与后续扩展 | 按登记、比较、人工批准推进，不由个人记忆自动启用 |
| S12 | 探索研究接口、Dashboard 与个人助手原生入口已实现；独立金融包及免费工具已部署 | 免费金融与 Marketplace 工具已上线；Extended 限制保留 |

## S12 子任务与证据

| 子任务 | 结果与追溯 |
| --- | --- |
| S12a/b | Core 探索研究提交、查询、取消与版本报告，Dashboard 只读展示；验收见历史记录 |
| S12c | 旧 Research Pipe 已退出主链路，本轮删除实现与专属测试；[历史说明](archive/openwebui-pipe.md) |
| S12d | 研究网络、Runner、归因及发布例外；[准备与授权记录](ops/s12-d2-enablement-prep.md)保留原时点含义 |
| S12e/f/g | 个人助手原生接入、三仓职责与助手源码迁移；历史完成记录保留，当前操作见[助手手册](ops/hermes-personal-assistant.md) |
| S12h | [三仓部署与旧数据兼容](ops/three-repo-vm-rollout-20261004.md) |
| S12i | [个人助手官方 Release 切换](ops/hermes-release-20260924-rollout.md) |
| S12j | [EODHD MCP 部署与套餐验收](ops/eodhd-mcp-rollout-20261004.md) |
| S12k | 三仓重构与冗余清理：验证结果、保留兼容项和未验证项见[重构记录](ops/three-repo-refactor-20261004.md) |

## 4. 完成记录

2026-10-04 以前的 S00–S12 完成记录已整体移入[历史实施记录](archive/implementation-through-20261004.md#4-完成记录)，保留失败、弃用方案、批准与部署经过。供应商证据、协议、Trial、Release 清单和数据库迁移仍留在原位置；归档不改变其授权或 hash。

新任务按纵向切片记录实现、实际验证与剩余依赖；不在工作规则文档复制进度或测试数量。

## 历史深链接

<a id="s06e-分类方案与正式登记复核2026-09-28"></a>
S06e 分类与登记复核的原始验收见[历史记录](archive/implementation-through-20261004.md#s06e-分类方案与正式登记复核2026-09-28)。保留此锚点供已固定协议引用。

<a id="s08--接入一个-pi-探索任务phase-1b依赖-s03s07"></a>
旧 Pi 探索任务深链接保留；当前决策是 Hermes 研究/实验实例，Pi 暂缓。原任务及决策经过见[历史记录](archive/implementation-through-20261004.md)。

## S12l — 默认分支统一为 develop（2026-10-04）

所有者要求三仓以 develop 为默认协作分支，并将本轮重构 PR 合入 develop。平台现有 develop 为本轮分支祖先；助手 develop 从原 main 建立，WebUI develop 从原 youwei 定制基线建立。按所有者补充要求，确认 main 无独有提交后删除三仓远端及本地 main 分支；提交历史保留在 develop，旧 youwei 分支保留。不重写提交，不操作 VM；WebUI 自有镜像工作流同步为 develop。

平台 PR 的 GitGuardian 对旧提交报 7 处告警，已逐项核对：4 处是一次性 PITR/pgBackRest 演练容器的固定测试密码，2 处为 Compose 必填环境变量引用，1 处是换行解析测试使用的不可解析 PEM 字符串（实际密码学加载拒绝）。这些不是可用生产凭证；未关闭扫描、添加忽略规则或改写历史，扫描告警与功能 CI 结果分别报告。

## S12m — Hermes Agent 工作台（2026-10-04）

按所有者提供的集成设计在 youwei-webui 0.11.4 新增 `/agent`、技能库、定时任务、消息网关四页。使用现有 WebUI 登录与指定 owner 校验，后端按方法/路径/字段允许列表代理固定 Hermes 原生 HTTP；密钥仅存服务器。会话、运行、审批、幂等、停止与 cron 权威仍在 Hermes。Calendar 展示最近/下次运行投影，不引入第二个调度器。

trading-assistant 增 `integrations/hermes/workbench.py` 只读技能服务，处理固定 `v2026.9.24` 原生 `/v1/skills` 的实测 500（调用 `_find_all_skills` 时传入不支持的 `include_editorial` 参数）；复用上游读取器并关闭技能预处理，无上游源码补丁。平台仅新增可选 `infra/compose/chat-workbench.json`，仍须新镜像与既有 WebUI owner ID，不修改已部署 release 清单。

实际隔离验证：助手 `uv run --frozen pytest -q` 25 通过；WebUI Python 3.12 代理测试 20 通过；Vitest SSE/Calendar 4 通过；Node 22 Vite production build 与助手 Docker build 通过。WebUI `ops/verify_hermes_workbench.py` 使用固定 Hermes、临时 HOME 和 mock 模型，验证发现、会话、运行幂等、SSE、持久历史、cron 创建/暂停/恢复/删除及只读技能正文；未调用真实付费模型。浏览器验证基于 mock HTTP，与原生服务验收分别记录。

完整 svelte-check 在原始 archive 基线和修改后均报告 7001 errors / 198 warnings（344 files），新增工作台文件无类型错误；不能报告完整类型检查通过。平台 catalog 校验和三份 Compose 合并渲染通过（仅占位值，没有启动服务）。Core/Runner 业务代码未改，未重跑整个平台数据库测试集。

未开放项：技能写入/自我改进审批、终端与运行环境切换、平台凭证/配对/启停控制；助手现有工具允许列表保持不变，界面明确说明。未发布工作台生产镜像、未做 VM 切换、未运行正式前向评估。WebUI 原生旧聊天保留；源码此前清空后重新导入 0.11.4，不能用旧已部署镜像身份代表本次候选。

## S12n — EODHD Extended 十九工具候选（2026-10-05，生产切换阻断）

按所有者计划在 trading-assistant 实现十九工具允许列表、日期/年份/数量与短时 WebSocket 限制；保留基础五工具、服务端鉴权与脱敏，移除基本面/财报日历。官方远端十九项 schema 已核对并用于原生 mock。独立源码和 amd64 候选镜像已发布；完整身份见[候选验收](ops/eodhd-extended-20261005.md)。

实际验证：助手 TDD 后完整 pytest 50 passed；固定 Hermes 原生 mock 与无网络镜像内十九工具循环、鉴权、越界/凭证拒绝、脱敏、超时/断连、流式/历史、备份恢复通过。平台三个相关契约文件 21 passed，catalog 与三份 Compose 合并占位渲染通过。VM 候选隔离容器原生发现全部 24 工具、真实 AAPL 报价字段校验通过，无真实模型调用。

真实账户十九项验收：15 项有效；盘中历史、技术指标、筛选返回 403；BTC-USD 实时采集连接断开。两项国债接口忽略 limit，但年份验证通过，保留此限制。按计划暂停生产切换，未静默删除失败项；候选锁 disabled/smoke_only，生产镜像/配置/卷保持原值。切换前配置与数据备份、生产副本隔离恢复、切换后工作台鉴权和真实查询验收尚未执行，须账户缺口解除后执行；本轮未跑 Core 全量数据库测试、付费模型选工具质量或正式前向评估。VM 上新助手与现有 WebUI 的隔离 mock 全部通过（工具/历史/流式/备份恢复/后台分流）；详见候选验收记录。

## S12o — Marketplace 指数工具接入与部署（2026-10-05）

用户确认购买 Indices Historical Constituents Data API 并授权部署。trading-assistant 保留生产七项 MCP，新增 `mp_indices_list`、`mp_index_components`，共九项 MCP + 五项基础工具。单指数 JSON/凭证覆盖拒绝和脱敏通过原生执行边界；官方 schema 与 hash 登记。旧 Token 轮换后实测 401，助手秘密配置更新为用户提供的新 Token，Core 凭证不变。

助手 TDD 后完整测试 60 passed；固定 checkout/本地镜像原生九工具循环通过。平台相关契约 21 passed。sg-prod 只替换助手及 skills；原生发现 14 工具，110 项指数及 GSPC 当前/历史成分实测通过；工作台未认证 401、owner 接口全 200。最终 amd64 原生/mock、WebUI 跨服务 mock、切换前后备份恢复及部署清单校验全部通过，其余 12 个既有容器身份/启动时间不变。候选和上线实测、发布身份与局限以[本次部署记录](ops/eodhd-marketplace-rollout-20261005.md)及 release preflight/postflight evidence 为准。未调用真实付费模型、未变更正式研究数据链路或评估协议，未重复 Core 全量数据库测试；此前 S12n 的 Extended 权限/连接阻断仍保留，不因本次两工具接入宣称已解决。

## S12p — 独立 trading_core 与免费金融工具（2026-10-05，已部署）

新增私有 `youweichen0208/trading_core`，默认 develop；Python 3.13 独立包提供 Yahoo/yfinance 日线、调整收盘价基础指标、SEC Company Facts 财报。保留平台 Core、quant、协议与已批准研究版本。助手注册 `trading_price_history`、`trading_indicators`、`trading_financials`，日线/指标/财报默认使用免费源；EODHD 指数与既有工具继续保留。

固定金融源码构建 wheel，重复构建 hash 一致；助手固定补充依赖，安装前后确认官方 Hermes 已有包版本不变。独立安装无 Hermes/Core 依赖。TDD 先观察缺失模块/API失败，再通过指标、价格、财报及工具边界测试：金融包 `uv run --frozen pytest -q` 23 passed，助手同命令 67 passed；平台三个相关契约文件21 passed，catalog通过。

sg-prod 已构建推送 amd64 镜像，隔离原生金融执行、完整原生mock、WebUI跨服务mock及候选备份恢复均通过；AAPL/MSFT/SPY真实日线均23行，六项日线/指标查询通过。首轮因缺少SEC联系信息保持候选。所有者随后提供联系邮箱，AAPL/MSFT财报实查通过，32个非缺失值与7份申报inline XBRL的期间/单位/值逐项匹配。刷新备份并通过候选恢复及deployment校验后，仅切换助手与skills两服务；健康、17工具发现、真实免费查询、EODHD报价/指数及工作台鉴权通过，其余12个容器身份/启动时间不变，未触发回滚。完整源码/wheel/镜像身份、跨服务验证、备份恢复和限制见 [本次验收记录](ops/trading-core-20261005.md) 与 [生产证据](../infra/releases/20261005-trading-core/contact-postflight.json)。未执行真实付费模型或正式前向评估。

## S12q — 四仓 Markdown 维护入口统一（2026-10-05）

四仓统一 README、AGENTS、CONTEXT 与 docs 下的索引、架构、开发、运维、状态入口。trading_core、trading-assistant 和 youwei-webui 补齐缺失文档；平台沿用现有领域与架构文档，补充开发、运维和状态导航。四仓职责集中在仓库边界，生产身份继续引用上游登记与发布证据；保留 WebUI 上游 README 正文和各次历史验收。同步修正当前三仓表述、基础/MCP 工具数量、原生金融依赖安装步骤和工作台已部署状态。

实际验证：检查36个新增或修改 Markdown 的本地及跨仓相对目标、统一文档入口和代码块闭合；四仓 `git diff --check` 通过。命令、工具允许列表、环境变量及 CI 入口与当前源码核对。原始证据、协议、依赖锁、运行代码及生产配置未改，平台已有 `.playwright-mcp/` 保留。纯文档任务未重跑业务测试、完整镜像、目标机、真实供应商或正式前向评估；本次没有生产操作。

## S09 — WebUI 入口与侧栏性能切片（2026-10-05）

上海 Nginx 1.18 启用 HTTP/2，成功哈希资源一年 immutable 缓存，保留 SG keepalive、压缩变体和长连接。WebUI 独立性能 PR #3 使聊天页仅请求工作台启用状态，详情按路由加载、去重、取消并防止过期回写。固定源码 b8fc503f3 构建 amd64 镜像，完成备份/候选恢复后仅切换 WebUI，其余 15 个容器身份及启动时间不变；未触发回滚。

实际验证：侧栏 Vitest 14、既有 API Vitest 4、Python 工作台 20、平台相关契约 21 通过；前端与完整镜像构建、目标机 Nginx 隔离测试、代理缓存/gzip 变体、跨服务 mock、浏览器流式/停止与备份恢复通过。全仓类型检查保留既有 7001 errors / 198 warnings。三阶段同会话冷/热各5次：修改前 31.03/10.30 秒，仅入口 10.72/6.72 秒，最终 10.57/6.74 秒（中位数）。冷目标达到，热 ≤5 秒未达到；剩余串行配置/身份/用户设置/模型列表链已定位。异常和首次新版冷样本保留，未证明侧栏独立时间收益或两分钟现象已解决。

实现、命令、源码/digest、最大值、原始资源/API 耗时、失败背景、共享站点既有 502、回滚与限制见 [验收记录](ops/webui-performance-20261005.md) 和 infra/releases/20261005-webui-performance。没有调用真实付费模型、修改正式研究数据或执行正式前向评估；未重跑 Core 全量数据库测试。
