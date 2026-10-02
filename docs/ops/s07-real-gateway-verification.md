# S07 真实网关研究链路验证（agent-runtime → LiteLLM → 火山）

日期：2026-10-02。环境：sg-prod（Docker 29.5.2）。范围：[llm-gateway-options 待测清单](../research/llm-gateway-options.md)中与方案 A（OpenAI 兼容）相关的研究链路项 + S07m 遗留（真实 LiteLLM 接线、受限网络 DNS）。**不含**：Phase 1B 数据转发授权（E2E 用合成证据，无真实研究数据）、研究角色/模型选择（本验证是管线验证，模型选择走 Trial + release 批准）、S08 工具往返契约、方案 B/C（Anthropic 端点，已非候选）。

## 拓扑（临时接线，Phase 1B 前需声明式化）

```
youwei-research (docker network create --internal, 172.27.0.0/16)
  └─ 研究容器（agent-runtime, digest 镜像, Ed25519 授权）
       └─ http://litellm:4000/v1  （Docker DNS 别名）
            └─ youwei-chat-litellm-1（同时接入 youwei-chat_chat 与 youwei-research）
                 └─ 火山 Anthropic 兼容端点（Bearer）
```

- `docker network connect --alias litellm youwei-research youwei-chat-litellm-1` 是**非持久**接线（litellm 容器重建即失效）；正式形态（compose 声明式共享网络）归 Phase 1B 部署决策。当前保持接通以便复现。
- 网络探针（`services/sandbox-runner/smoke/research_net_probe.py`）：DNS 解析 `litellm` → 172.27.0.2 ✓；研究 key 在受限网络内可见 5 模型 ✓；外网 8.8.8.8/1.1.1.1 与外部 DNS 全阻断 ✓；chat 栈（postgres 172.26.0.2:5432、openwebui 172.26.0.4:8080）从研究网络不可达 ✓（S07m 遗留的 DNS/IPv6 项关闭）。
- 验证用虚拟 key（管理面签发，值只存 `/root/youwei-research-verify/keys.env` 0600）：`research-e2e`（max_parallel_requests=2，5 模型）、`research-conc`（max_parallel_requests=1）、`research-rate`（rpm_limit=2）、`research-tpm`（tpm_limit=6000）。组合限额 key `research-limits` 验证后删除（见 TPM 发现）。

## 验证矩阵结果

| 项 | 结果 | 证据 |
| --- | --- | --- |
| 工具调用流式（网关级） | **PASS** | glm-5.3 流式 tool_call 增量重建正确（`snapshot_manifest` + `{}`），finish=tool_calls；tool result 回传后模型准确复述 snapshot_id（往返完成） |
| E2E 研究回合（链路级） | **PASS** | Runner → 研究容器（`sha256:bca0a5b9…`）→ LiteLLM → 火山，合成证据 10 bar；glm-5.3 返回合法提案 `source_status=unavailable`（理由：合成证据无 benchmark 数据——正确判断）+ 2 引用 + 4 条合规 ResearchWarning（含一条 PIT 异常观察：as_of 时刻含当日 bar）；usage `session_delta complete=True` |
| 用量记录 | **PASS（逐 token 一致）** | 网关 4 请求 prompt 合计 19,703 / completion 合计 8,071，与 session_delta `prompt_tokens=19703`/`completion_tokens=8071` 完全一致；api_calls=4 与网关请求数一致 |
| 辅助调用观测 | **已观测** | 真实模型每回合多 1 个**无 tools、无 messages** 的请求（mock 下不出现）；计入 session 计数器与 spend logs。修正 S07j「aux 不进 session 计数器」的离线结论。 |
| 取消传播（客户端中断） | **成立** | 中断后请求 dur=5462ms（同类完整生成 40-80s）→ LiteLLM 在数秒内终止上游流；spend 记录 `completion=0`（中断流用量不入账，供应商侧是否计费不可见） |
| 取消传播（Runner 超时击杀） | **修复后成立** | 见缺陷 F4。15s 超时通常落在 Hermes 初始化期（首轮 API 调用约在启动 25s 后），无网关请求产生；容器 socket 随 rm -f 关闭，网关侧行为同客户端中断 |
| 并发限额 max_parallel_requests=1 | **PASS** | 长请求流式中（2050 chunk）重叠请求即拒：429 `Limit type: max_parallel_requests. Current limit: 1, Remaining: 0` |
| 速率限额 rpm_limit=2 | **PASS** | 3 连发：200、200、429 `Limit type: requests. Current limit: 2` |
| 令牌限额 tpm_limit=6000 | **PASS** | 大 prompt 请求 1 通过（余 1703），请求 2 拒：429 `Limit type: tokens` |
| TPM 准入语义（额外发现） | — | TPM 在**准入前按预估用量**检查：projected > remaining 即拒（组合 key 上长请求因 `Remaining: 1000` 被拒，即使 prior spend 为 0）。限额叠加时互相干扰，专用 key 分别验收。 |
| fail-closed（计数库不可达） | **FAIL-OPEN** | 同 digest 并行临时栈（`ops/gw_failclosed_drill.sh`，不碰生产聊天栈）：DB 停止后虚拟 key 请求两次均通过准入并转发上游（401 来自火山 dummy key）。适用于已验证/缓存 key；未缓存 key 无法测（建 key 需 DB）。**含义**：DB 故障时网关可用性优先于计数完整性，spend 将出现缺口；rpm/tpm/并发限额为 DB 支撑，DB 故障期间推定不可执行（未单独实测）。 |
| 模型列表（研究 key） | PASS | 受限网络内 5 模型可见（200） |
| Ed25519 / mock 回归（新镜像） | PASS | container_smoke 5/5（错 tenant/缺 scope/坏签名/过期全拒）；research_e2e mock END_TO_END OK（api_calls=1） |

## 发现并修复的缺陷（全部 TDD：先红后绿）

| # | 缺陷 | 修复 |
| --- | --- | --- |
| F1 | 研究简报缺响应格式契约：真实模型以散文回答（mock 冒烟从未暴露）→ `parse_proposal` 失败 | `adapter.build_research_brief` 增加必填 JSON 格式段（字段清单、warnings 对象形状、unavailable 不得带值的值域纪律、不得自报 model） |
| F2 | 提案 `model` 归因信模型自报（外部文本不可信输入） | `parse_proposal` 增加 `model_attribution`，`run_research` 从 `config.model/provider` 注入并丢弃自报 |
| F3 | 无显式输出上限：glm-5.3 长思考把 JSON 写到一半撞 API 默认 max_tokens（finish_reason=length） | `ResearchConfig.max_output_tokens=16384` 传入 `AIAgent(max_tokens=…)` |
| F4 | **Runner 超时只杀 docker 客户端进程，容器继续运行并持续调用网关**（击杀后泄漏容器又跑 1 分多钟、完成 3 次 API 调用） | `run_research_container` 超时/取消/客户端故障路径按容器名 `docker rm -f`（干净退出 0 不额外调用）；新增 `tests/pure/test_research_container.py` 4 个假 docker 回归测试；SG 真实 Docker 复验 `CLEANUP VERIFIED` |
| F5 | Runner 失败诊断被 Hermes banner 淹没（500 字符 stderr 尾部） | `_decode_result` 优先取容器 stdout 的结构化错误 + 2000 字符 stderr 尾部 |
| F6 | `research_e2e.py` 硬编码 S07m 旧子网 IP（172.19.0.2；网络重建后为 172.27.0.0/16）导致 mock 冒烟挂起 | 网关 URL/镜像改环境变量驱动 |

工具面安全（Hermes Tool Search 桥）：隔离 agent 实际暴露 `tool_search/tool_describe/tool_call` 三个元工具（插件工具被渐进披露折叠），模型经桥调用平台工具。`bridge_scope_probe.py`（容器内、真实 agent 机制）实证：桥内请求 `terminal` 被拒（"not a deferred one"）、**直接伪装** `terminal` 工具调用被会话校验拒绝（"Tool 'terminal' does not exist"）、经桥及直接调用 `snapshot_manifest` 均正常分发且 contextvar 授权链完整。toolset 隔离对两条逃逸路径成立（源码佐证：`tool_executor._unwrap_tool_search_call` 显式强制会话 toolset 范围）。

## 镜像与代码状态

- 验证镜像：`youwei/agent-runtime@sha256:bca0a5b9…`（SG 本地 manifest digest，含 F1-F3 修复；由 `infra/build-agent-runtime.sh` 从当前工作树构建）。**GHCR 仍为旧 digest `998f060e…`（phase1a-s07），修复镜像重发布待凭证**。
- 新增可复现资产：`services/sandbox-runner/smoke/research_real_gateway.py`（链路级 E2E 驱动，含超时击杀模式）、`gateway_matrix.py`（网关级矩阵）、`research_net_probe.py`（受限网络探针）、`services/agent-runtime/smoke/bridge_scope_probe.py`（工具面越权探针）、`ops/gw_failclosed_drill.sh`（fail-closed 并行栈演练）。
- 测试：agent-runtime 3.14 → **53 passed**（新增 4：简报格式契约、归因覆盖 ×2、max_tokens 传递）；本机 pure+contracts → **130 passed**（新增 4：Runner 容器生命周期回归）。

## 费用

research-e2e key 全程 40 请求 / prompt 179,074 / completion 72,711 tokens（含多轮失败迭代与缓存命中重复计数）；限额 key 合计 <2,100 completion tokens。GLM 无自定义 cost map（预算维度已删除，2026-09-30 所有者决策），spend 仅记 token。

## 剩余限制

- 修复镜像未发布 GHCR（无 registry 引用，`upstreams.lock` 的 `deployment.image` 保持 null）；发布后应同步更新登记。
- 研究链路到网关的接线是临时 `docker network connect`，litellm 容器重建即失效；Phase 1B 部署时需声明式化（compose 共享网络或等价物）。
- fail-closed 结论限定「已缓存 key」；DB 故障期间限额执行行为未实测；若需 fail-closed 语义需评估 LiteLLM 配置或外加防护。
- 辅助请求（无 tools/messages 的首个请求）的身份与触发条件未完全钉定（仅真实模型出现、计数与 spend 均覆盖）；Phase 1B 接入时观测其 token 占比。
- 模型选择（glm-5.3）是管线验证用车，不构成研究角色/prompt 定稿；正式选择走 Trial 登记 + release 批准。
- S08 工具往返契约（quant_run 等四工具）不在本批；真实研究证据转发待 Phase 1B 数据授权。
- 单回合延迟 84-224s（3-5 次 API 调用）：Phase 1B 批量 60 case 串行不可行，需并发设计（研究 key 并发限额、批内并行度）——届时按实测重估。
