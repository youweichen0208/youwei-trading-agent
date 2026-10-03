# S12 D2 启用准备：模型定稿、实际返回标识留痕、镜像 r5/r3（暂存待部署）

日期：2026-10-03。前置：[s12d-deployment-execution](s12d-deployment-execution.md)（D1 部署执行）。

## 所有者决策（D2，2026-10-03）

| 项 | 决定 |
| --- | --- |
| 探索研究默认模型 | `glm-5.3`（依据 S07n 实测：已走通工具调用/结构化提案/受控网关链路——工程兼容性证据，非研究质量结论） |
| 自动跨模型回退 | 首版关闭，失败如实报告 |
| 模型侧启用验收 | 按当前 S07o 契约复验量化输入、quant_relation、引用及完整报告产出 |
| 版本留痕 | 每次保存模型路由、**实际返回标识**、prompt 与配置版本 |
| 适用范围 | 仅探索性研究默认配置；**不构成正式预测 Campaign 的模型批准** |

后续若单回合延迟（实测 84–224s）成为实际问题，再预登记与 Flash 候选的同题比较（引用正确性/完整率/耗时），按结果决定是否替换。

## 实际返回标识的实现（最小 fork 记录）

Hermes（`7fa45eb`）无稳定表面暴露响应的 model id：`chat()` 只返回字符串、回合结果 dict 与历史消息均不含 model、钩子只收 usage（sg-prod 固定 checkout 逐点核实）。按仓库上游政策（适配层无法满足时的最小 fork），落地为 **2 行补丁**：

- `infra/images/hermes-last-turn-model.patch`：`agent/turn_usage.py` 的 `record_response_usage` 在每次完成的 provider 调用后 `agent._last_turn_model = getattr(response, "model", None)`（与 `_last_turn_usage` 同生命周期；适配层每回合新建 agent，值不会跨回合泄漏）。补丁先 `git apply --check` 再应用——上游升级破坏补丁时构建失败而非静默丢失。
- agent-runtime：`UsageReport.model_returned`（未打补丁的 checkout 或供应商省略 → None，绝不伪造），经**既有 usage dict** 携带回 Core——wire 契约零变更，已部署的 Runner 镜像（`c3c0a0c5…`）无需重建。
- Core：`make_runner_research_fetcher` 增可选 `attribution_sink`（model_returned / image_digest / usage / exec_config_version）；探索报告 versions 块记录 `research_model_configured` + `research_attribution`，与配置注入的提案归因并列。

## 交付镜像（sg-prod 构建、GHCR 发布、digest 拉取验证）

| 镜像 | tag | registry digest | 内容 |
| --- | --- | --- | --- |
| youwei-agent-runtime | `phase1a-s07-r3` | `sha256:97d8b9c1d12d2cdca86eab47007d4a14277474b9cb2c5ff72eb34a9462571f86` | Hermes 补丁 + `model_returned`；镜像内补丁存在性验证 + 容器冒烟 5/5（合法 grant/错租户/缺 scope/坏签名/过期）无回归 |
| youwei-core | `phase1a-r5` | `sha256:10a4ecf9cc592d74a347ac8d6a39dd953a70c69198179c5c4998146ba36ae8af` | r4 之后全部：S07o 量化入研究输入、S12a 探索研究后端（含迁移 `b2c3d4e5f6a7`）、S12b 查询 API、S08c 迁移 `a1b2c3d4e5f6`、本文的 attribution 与私钥 `\n` 修复；构建基底 `python:3.13-slim@sha256:bb298871…`（=r4 实际基底，tag 已漂移故显式固定）+ `uv:0.11.16@sha256:440fd647…`；容器内 sanity（config 反转义/attribution 参数/API import）通过 |

`infra/compose/production.json` 已暂存：core-api/core-worker → r5、sandbox-runner env `YOUWEI_RUNNER_AGENT_RUNTIME_IMAGE` → r3（**生产未滚动**——r4/bca0a5b9 仍在跑）。

## 与批准 release 的代码差异（S07o 约束，待所有者决定）

批准 release `bdb8bbe0…`（campaign `phase1a-pilot-2026q4` 绑定）的 `code_files` 五项中，r5 相比仅 `youwei_core/ledger/pipeline.py` 变化：

| 版本 | pipeline.py sha256 | 内容 |
| --- | --- | --- |
| release（2026-10-02 登记） | `fb2ab111…` | S07m 状态 |
| **r4（当前生产）** | `79f38ade…` | + S08c `f43531e`（Controller 实验编排接线）——**r4 已与 release 不同**（r4 构建于该提交后，未单独走差异决策） |
| r5（暂存） | `08d6705f…` | + S07o `cbf5259`（量化预测入研究输入） |

两级 diff 逐行核验：**全部落在 Phase 1B/探索路径**（`make_phase1b_llm_adjusted_provider`/`make_phase1b_llm_fetcher` 签名与 quant 传递、`run_batch_predictions` 的 runner_research 分支含实验编排条件）；Phase 1A 的 `_phase1a_llm_adjusted`、baseline/quant 计算、证据冻结与封存调用零改动。`code_files` 其余四项（quant/logistic.py、quant/dataset.py、model_registry.py、uv.lock）r4/r5 均未变。运行时无 code_files 校验（登记时记录；生产 r4 运行正常佐证）。

**待所有者决策**：r5 滚动前（a）明确接受上述差异（记录在案），或（b）登记新 release 并批准。另需决定**时机**：批次 1 cutoff（2026-10-10 06:00 ET）前滚动 vs 封存确认后滚动。

## r5 滚动步骤（获批准后执行）

1. 部署副本字段级更新（core digest ×2 + agent-runtime env）；`docker compose config` 校验。
2. `alembic upgrade head`（生产库 `f9a0b1c2d3e4` → head，两条**纯新增**迁移：S08c experiment_records/outcomes + S12a exploratory 两表及触发器；一次性容器、youwei_migrate 角色）。
3. worker 研究接线 env（runbook §2.4）：`YOUWEI_RUNNER_URL=http://sandbox-runner:8091`、`YOUWEI_RESEARCH_MODEL=glm-5.3`、`YOUWEI_EXPERIMENT_EXPLORATION_ENABLED=true` 写入 production.env。
4. `docker compose up -d`（core-api/core-worker 重建至 r5；sandbox-runner 因 env 变更重建）。
5. 验证：healthz、ops status 无告警、`POST /v1/research` 未认证 401（新 API 在位）、worker 日志无错、批次 1 相关调度不受扰。
6. Open WebUI 入口（runbook §6 四步 + openwebui 重建接 edge 网络）。
7. 八项端到端验收（runbook §7）+ 所有者的模型侧验收（量化输入/quant_relation/引用/完整报告产出——报告 versions 块含 `research_model_configured=glm-5.3` 与 `research_attribution.model_returned`）。

## 验证索引

- 本机全量：`pytest -q` → **673 passed, 1 skipped**（真实 PG 容器 + 迁移链；新增：config 反转义 3、fetcher attribution 2、agent-runtime model_returned 2、exploratory runner 路径 attribution 1）
- agent-runtime（3.14）：**87 passed**；容器冒烟 5/5；镜像内补丁与字段存在性断言通过
- 两镜像 push + digest 拉取验证（sg-prod，GHCR 凭证在位）
