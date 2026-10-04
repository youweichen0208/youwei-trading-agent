# LLM Gateway 选型核实

核实日期：2026-09-27（同日按评审修正）。上游已定：火山引擎（Volcengine）API 网关，Anthropic Messages 兼容协议，模型 GLM 5.3 / GLM 5.3 Flash / DeepSeek V4 Flash/Pro / Qwen 3.8 Flash。方法：PyPI、LiteLLM 官方文档（virtual keys / spend tracking / users / model access group budgets）、Hermes 官方 FAQ、本机 pi 生产配置、sg-prod 实测。端到端实测记录见 [s01-target-verification](s01-target-verification.md)。

## 已确认

| 项目 | 事实 | 来源 |
| --- | --- | --- |
| Pi 协议能力 | models.json 的 `api` 同时支持 `openai-completions` 与 `anthropic-messages`——本机用 Anthropic 只是当前配置，非能力限制 | [models 文档](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/models.md) |
| Hermes 协议能力 | 官方支持 Anthropic 直连（`AIAgent(model="anthropic/claude-opus-4.7")`、`hermes auth add anthropic`）**及** OpenAI 兼容 custom endpoint（`provider: custom` + `base_url`）——两个 agent 都是双协议 | [FAQ](https://hermes-agent.nousresearch.com/docs/reference/faq) |
| LiteLLM 版本 | 1.102.1（PyPI 最新），Python `>=3.10,<3.15` | [PyPI](https://pypi.org/project/litellm/) |
| **请求级预算预留** | **Budget reservation 默认启用**：按请求体 + 模型定价预估最大成本，在供应商处理前预留该金额；预留将超预算时在发往上游前拒绝；响应计价后结算差额 | [users 文档](https://docs.litellm.ai/docs/proxy/users) |
| 预算体系 | per-key/user/team `max_budget` + `budget_duration` 周期重置；**Model Access Group Budgets**：同组模型共享预算池，多个 key 从同一池扣减（run→共享预算映射的候选工具） | [virtual keys](https://docs.litellm.ai/docs/proxy/virtual_keys) / [model access group budgets](https://docs.litellm.ai/docs/proxy/model_access_group_budgets) |
| 速率 vs 并发 | `tpm_limit` / `rpm_limit` 是**速率**限制；`max_parallel_requests` 是 per-key **在途并发**上限——两个独立参数，分别验收；另有 ITPM/OTPM（输入/输出 token 速率分开限制） | 同上 + [cost tracking](https://docs.litellm.ai/docs/proxy/cost_tracking) |
| 用量记录 | per-request spend tracking 入 Postgres（key/user/team 维度，`/key/info` 等查询） | [spend tracking](https://docs.litellm.ai/docs/proxy/cost_tracking) |
| Key 管理 | 需要一个 Postgres（`DATABASE_URL`）+ master key（Proxy Admin）；官方 Docker 镜像 | [virtual keys](https://docs.litellm.ai/docs/proxy/virtual_keys) |
| 火山网关认证 | 不认纯 `x-api-key`，LiteLLM 需 `extra_headers: Authorization: Bearer`（sg-prod 实测证实） | [实测记录](s01-target-verification.md) |

## 接线方案（候选，非"必须双协议"）

两个 agent 都说双协议，接线是选型题，按同一验收矩阵比较后定：

| 方案 | Hermes | Pi | 已验证部分 |
| --- | --- | --- | --- |
| A：统一 OpenAI 兼容 | custom provider → LiteLLM `/v1` | `openai-completions` → LiteLLM `/v1` | chat / 工具调用 / 流式（含 thinking blocks）实测通过 |
| B：统一 Anthropic | anthropic provider → LiteLLM Anthropic 端点 | `anthropic-messages` → LiteLLM Anthropic 端点 | 待测；注意区分 LiteLLM **统一 `/v1/messages`** 与 **`/anthropic` passthrough**（后者实测认证未通） |
| C：双入口 | 各用原生协议，LiteLLM 两侧都暴露 | 同左 | passthrough 一侧待修 |

验收矩阵（每方案同表验证）：工具调用流式、辅助调用（Hermes delegation / Pi 子代理）、用量记录、取消传播。

## 职责分层（Controller / Gateway）

- **Controller**：run 的业务账本、任务生命周期、预算归集与回收、审计入口。
- **Gateway**：每次上游请求的预算准入、预留与结算——由 LiteLLM budget reservation 承担，**不是**只检查累计消费。

需补齐的配置与验收（S02 网关实现时）：

1. run → key / 共享预算映射（Model Access Group Budgets 为候选；决定每 run 独立 key 还是共享池）
2. **实际模型价格**：GLM / DeepSeek / Qwen 不在 LiteLLM 默认成本表，需自定义 cost map 并与火山实际计费对账
3. 输出上限：`max_tokens` 配置 + OTPM 输出 token 限制
4. 重试计费：重试即新请求，每次单独入 spend logs——确认预留/结算覆盖重试路径
5. **计数器故障时拒绝请求**（fail-closed）：Postgres 不可用时 LiteLLM 放行还是拒绝，需实测并写入验收
6. 并发与速率分别验收：`max_parallel_requests`（在途数）与 tpm/rpm 各自的生效证明

## 待实测（2026-10-02 更新：研究链路相关项已验证）

1. 方案 B/C 的 anthropic 端点接线（含 `/anthropic` passthrough 认证修复或改用统一 `/v1/messages`）——**已放弃**：研究链路与聊天均选定方案 A（OpenAI 兼容 `/v1`），B/C 不再是候选
2. 客户端取消 → LiteLLM → 火山上游的取消传播——**已验证成立**（中断后 5.5s 内终止上游；spend 记 completion=0）
3. 自定义 cost map + 火山计费对账——**搁置**（预算/计费维度已按 2026-09-30 所有者决策删除；spend 仅记 token）
4. fail-closed 行为（DB 故障时）——**已实测：FAIL-OPEN**（已缓存 key 在计数库不可达时照常准入并转发上游；限额为 DB 支撑，故障期间推定不可执行）
5. Hermes / Pi 实际指向网关的端到端（按选定方案）——**已验证**（Hermes agent-runtime → LiteLLM → 火山，方案 A，含工具调用流式与用量逐 token 对账；Pi 暂缓）

全部证据与缺陷修复记录：[s07-real-gateway-verification](../ops/s07-real-gateway-verification.md)。
