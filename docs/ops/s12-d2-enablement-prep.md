# S12 D2 启用准备：模型定稿、完整归因链、例外登记与暂存镜像（待首批封存后滚动）

日期：2026-10-03（同日按所有者评审修订）。前置：[s12d-deployment-execution](s12d-deployment-execution.md)（D1 部署执行）。

## 所有者决策（2026-10-03）

| 项 | 决定 |
| --- | --- |
| 探索研究默认模型 | `glm-5.3`（依据 S07n 实测：工程兼容性证据，非研究质量结论） |
| 自动跨模型回退 | 首版关闭，失败如实报告 |
| 模型侧启用验收 | 按 S07o 契约复验量化输入、quant_relation、引用及完整报告产出 |
| 版本留痕 | 每次保存模型路由、**实际返回标识**、**prompt 与完整执行配置**及其 hash |
| 适用范围 | 仅探索性研究默认配置；**不构成正式预测 Campaign 的模型批准** |
| 最小 Hermes 补丁 | **保留**（评审确认符合仓库政策） |
| release 差异 | **①(a)：接受差异，登记为限定版本兼容性例外**（见下）；r4 差异**首批封存（2026-10-10）前**完成登记 |
| 滚动时机 | **②(b)：首批封存确认后**；期间完成修复、构建与隔离验收 |

后续与 Flash 候选的比较须预登记 Trial（引用正确性/完整率/耗时）。

## 评审修复（2026-10-03，三处 P2）

1. **r3→r4 镜像纳入部署组件校验（Standards）**：upstreams 的 hermes 组件 `enabled=true`，以**环境绑定**（`sandbox-runner` 服务的 `YOUWEI_RUNNER_AGENT_RUNTIME_IMAGE`）纳入 deployment 校验——当前 VALID 即覆盖该镜像。同时登记补丁 hash（`d713a32d…`）与移除条件，并明确警示：`git apply --check` 只证明补丁可应用，**不证明上游语义未变**——任何 Hermes revision 升级须对补丁区域做语义复核。
2. **prompt 与完整执行配置留痕（Spec 1）**：契约新增 `ResearchInvocationAttribution`——`brief_sha256`（确定性研究简报即 prompt 的 hash）、`execution_config`（解析后的**非敏感**运行参数：model/provider/迭代预算/输出上限/网关端点；**绝不含网关凭证**）+ `execution_config_sha256`（构造时校验）。报告 versions 块完整记录。
3. **实际返回标识独立观测范围（Spec 2）**：`model_returned` 从 usage dict 移入 attribution，配 `model_returned_scope = "last_completed_provider_response"`（字面量，非自由文本）；不与 usage 的 `complete` 混淆（usage 可能整回合累计，该标识仅为最后一次完成的供应商响应）。如实声明：该标识是**网关/供应商返回值的观测**，不能独立证明底层模型身份。**S08 多步编排路径尚未保存归因**——列为未覆盖范围（探索循环的实验往返不产生 attribution 记录，后续需要时单独补）。

契约变更为可选字段：旧 agent-runtime 输出仍可解析；但**旧 Runner 会拒绝新容器的 attribution 字段**（extra=forbid）——Runner + agent-runtime + Core 三镜像在 S12 滚动时**一起**升级（生产 Runner `c3c0a0c5` idle 保留至滚动）。

## release 差异：限定版本兼容性例外（①(a)）

批准 release `bdb8bbe0…`（campaign `phase1a-pilot-2026q4` 绑定）的 code_files 五项中，仅 `youwei_core/ledger/pipeline.py` 与部署代码不同：

| 版本 | pipeline.py sha256 | 内容 |
| --- | --- | --- |
| release（2026-10-02 登记） | `fb2ab111…` | S07m 状态 |
| **r4（当前生产）** | `79f38ade…` | + S08c `f43531e`（Controller 实验编排接线）——**r4 先部署、后发现差异**，批准时间不倒填，如实记录 |
| r6（暂存） | 见下 | + S07o `cbf5259`（量化预测入研究输入） |

两级 diff 逐行核验：**全部位于 Phase 1B/探索路径**；Phase 1A 的 `_phase1a_llm_adjusted`、baseline/quant 计算与封存调用零改动（`_phase1a_llm_adjusted` 两版字节一致，sha256 `82de38a9…`）。其余四项 code_files（quant/logistic.py、quant/dataset.py、model_registry.py、uv.lock）r4/r6 均未变；运行时无 code_files 校验。

**登记载体**：新迁移 `c2d3e4f5a6b7` 的 `release_code_exceptions` 表（append-only，每行绑定 release + 部署镜像 digest + campaign；记录实际 code_files hash、差异摘要、验证依据、approver 与 basis；`created_at` 为登记时服务器时钟）。**适用范围逐行显式**：未来部署不自动继承——再变代码须新 release 或新例外行。可执行守卫（`tests/test_phase1a_code_identity.py`）：① Phase 1A llm provider 字节恒等（hash 常量锚定 release 状态，改动即红）；② Phase 1A campaign 即使 worker 带研究接线也**零调用**研究链路（llm_adjusted 封 unavailable/not_enabled 而非错误路径）。

**验证依据**（入登记记录）：上述守卫测试 + 全量 678 passed + 生产只读复核（部署中 r4 + 冻结输入：**60/60 quant 预测可用**、批次 1 窗口 56/61 已覆盖、余 5 个交易日由滚动采集在 cutoff 前落地——2026-10-03 07:31Z 复跑）+ 双源 Campaign 不调 LLM 的接线条件核验（`"llm_adjusted" in campaign.enabled_sources` 才构造 fetcher）。

**登记执行**：r4 例外（digest `54df4db5…`）于首批封存（2026-10-10）**前**写入生产库；r6 例外（digest 见下）在滚动时登记（其 code_files 与 r4 的差异仅 S07o，同样被守卫覆盖）。

## 交付镜像（sg-prod 构建、GHCR 发布、digest 拉取验证；**均未滚动**）

| 镜像 | tag | registry digest | 内容 |
| --- | --- | --- | --- |
| youwei-core | `phase1a-r6` | `sha256:119b2b6cd7df3ef91ddb4b3de827ae30dec114d1a82dbd4b1a28e6c866fb6986` | r5 之后：例外登记表迁移、完整归因链（attribution 解码/入报告）、Phase 1A 守卫；容器内 sanity（表 meta/契约/报告参数/API import）通过。基底 `python:3.13-slim@sha256:bb298871…` + `uv@sha256:440fd647…` 显式固定。r5（`10a4ecf9…`）作废不用 |
| youwei-runner | `phase1a-s12e` | `sha256:814be9c23ad84c459db7ae53661d4d58f355a71daf70fdda0dc7eac8de0f9dee` | 契约含 attribution 字段（否则拒绝新 agent-runtime 输出）；镜像内契约解析验证通过；基底三重固定（python/uv/docker:29-cli@`b1805116…`） |
| youwei-agent-runtime | `phase1a-s07-r4` | `sha256:8d210f9105c0a85aa89bc362ea87e1eddf93d278abd6190fd55e33f93ff3c6ab` | Hermes 补丁 + `build_execution_attribution`（prompt hash/执行配置/model_returned+scope）；镜像内补丁与归因模型断言 + 容器冒烟 5/5 通过。r3（`97d8b9c1…`）作废不用 |

`infra/compose/production.json` 已暂存上述三 digest（生产仍运行 r4 + `c3c0a0c5` Runner + 旧 env）。

## 滚动安全（②(b) 期间完成隔离验收，滚动按下列程序）

**前置兼容性**（滚动前验证）：
- 迁移纯新增（`a1b2c3d4e5f6` 实验表、`b2c3d4e5f6a7` 探索表、`c2d3e4f5a6b7` 例外表——CREATE TABLE + 触发器，无既有表 ALTER）；旧镜像（r4）代码不含对新表的任何引用（发布前在 r4 镜像内 grep 佐证）→ 迁移先行对生产库安全（r4 无感知）。
- 例外表迁移 + r4 例外登记在首批封存前执行（一次性容器，`youwei_migrate` 角色）。

**滚动步骤**（首批封存确认后）：部署副本字段级更新（core ×2 / runner image / agent-runtime env）→ `docker compose config` 校验 → `alembic upgrade head` → worker 研究接线 env（`YOUWEI_RUNNER_URL`/`YOUWEI_RESEARCH_MODEL=glm-5.3`/`YOUWEI_EXPERIMENT_EXPLORATION_ENABLED=true`）→ `up -d`（api/worker/runner 重建）→ 验证（healthz、ops 无告警、`POST /v1/research` 未认证 401、批次调度不受扰、r6 例外登记）→ Open WebUI 入口四步（含 openwebui 重建接 edge 网络）→ 八项端到端验收 + 模型侧复验（报告 versions 块含 `research_model_configured=glm-5.3` 与 attribution 的 `brief_sha256`/`execution_config`/`model_returned`+scope）。

**回滚程序**（顺序强制）：
1. **先停入口**：Open WebUI 管理面板禁用研究函数（不再接收新探索任务）；
2. **排空在途**：等待或取消所有 pending/running 的 `research.exploratory` 任务——**旧 Worker 对未知 kind 会 `fail_attempt`**（`worker/loop.py` 的 no-handler 路径），禁止带着在途探索任务降级 Worker；
3. 清空 worker 研究接线 env → 降级镜像（core→r4、runner→`c3c0a0c5`、env→`bca0a5b9`）；
4. **数据库不 downgrade**：新表已含研究/例外数据，`alembic downgrade` 会 DROP 含数据表——回滚仅回镜像，schema 前向保留（旧镜像对新表无感知，兼容）。

## 验证索引

- 本机全量：`pytest -q` → **678 passed, 1 skipped**（真实 PG 容器 + 完整迁移链；本轮新增：契约 attribution 2、Phase 1A 守卫 2、例外表 1，重构 research_client/exploratory/agent-runtime 归因断言）
- agent-runtime（3.14）：**87 passed**；容器冒烟 5/5；镜像内补丁/归因/构建器断言通过
- 三镜像 push + digest 拉取验证（sg-prod，GHCR 凭证在位）；r6 容器内 sanity 通过
- 生产只读复核（r4 + 冻结输入）：60/60 quant 预测可用、批次 1 窗口 56/61 覆盖（2026-10-03 07:31Z）
- 所有者本轮只读复核：配置与研究客户端测试 10 passed、git diff --check、catalog 校验
