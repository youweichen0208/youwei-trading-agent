# 实施计划与交付状态

更新：2026-10-04。领域语义见 [CONTEXT](../CONTEXT.md)，设计见 [架构](ARCHITECTURE.md)，代码职责见 [仓库边界](REPOSITORY.md)。历史计划和逐次完成证据集中于 [历史实施记录](archive/implementation-through-20261004.md)，其中旧 Pipe、旧版本、预算方案及当时的待办不再作为当前操作指令。

## 当前状态与后续节点

- 三仓源码职责已拆分；根据 2026-10-04 部署记录，聊天链路为 youwei-webui v0.11.4 → 官方 Hermes v2026.9.24 个人助手 → LiteLLM / Core / EODHD MCP。具体提交、镜像及数据路径以 [上游登记](UPSTREAMS.md) 指向的已部署组合为准。
- Phase 1A 已按所有者批准登记，首个 cutoff 为 2026-10-10 06:00 ET。正式预测仍遵循批准 release、既有代码例外与封存守卫；个人助手重构不启用 Phase 1B。
- 当前重构只交付源码、测试与候选构建，不部署 VM，不执行真实付费模型或正式前向评估。历史部署通过不等于本轮候选已上线。

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
| S09 | 平台及聊天部署、备份、恢复和运维演练已有记录 | 国内入口、独立故障域备份等未完成能力不能从同机演练推导 |
| S10 | Dashboard 只读报告、案例和评估视图已实现 | 正式评估等待成熟标签；研究 Memory/审批扩展另验收 |
| S11 | 受控进化与后续扩展 | 按登记、比较、人工批准推进，不由个人记忆自动启用 |
| S12 | 探索研究接口、Dashboard 与个人助手原生入口已实现；三仓拆分与 EODHD 接入已部署 | 当前重构候选尚未部署；套餐权限限制保留 |

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
