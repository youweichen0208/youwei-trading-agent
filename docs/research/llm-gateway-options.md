# LLM Gateway 选型核实

核实日期：2026-09-27。上游已定：火山引擎（Volcengine）API 网关，Anthropic Messages 兼容协议，模型 GLM 5.3 / GLM 5.3 Flash / DeepSeek V4 Flash/Pro / Qwen 3.8 Flash。方法：PyPI、LiteLLM 官方文档、Hermes 官方 FAQ、本机 pi 生产配置。未做端到端实测。

## 已确认

| 项目 | 事实 | 来源 |
| --- | --- | --- |
| 协议分叉 | **Pi 走 anthropic-messages**（本机 models.json 生产实证）；**Hermes custom provider 走 OpenAI 兼容**（`provider: custom` + `base_url`）——网关必须同时暴露两种前端协议 | 本机配置 + [Hermes FAQ](https://hermes-agent.nousresearch.com/docs/reference/faq) |
| LiteLLM 版本 | 1.102.1（PyPI 最新），Python `>=3.10,<3.15` | [PyPI](https://pypi.org/project/litellm/) |
| 虚拟键 | per-key `max_budget`、`tpm_limit`、`rpm_limit`、`models` 白名单 | [virtual keys 文档](https://docs.litellm.ai/docs/proxy/virtual_keys) |
| 花费追踪 | 按 key / user / team 查询（`/key/info` 等）；按模型成本表计算 | 同上 |
| Key 管理 | 需要一个 Postgres（`DATABASE_URL`）+ master key（Proxy Admin） | 同上 |
| 部署 | 官方 Docker 镜像 | 同上 |

## 需求满足度对比（架构 §2 llm-gateway 单元 / §11 预算）

| 需求 | LiteLLM Proxy | 自建薄代理 |
| --- | --- | --- |
| 预算预留/结算 | `max_budget` 是累计阈值，**没有 run 级原子预留语义**——预留必须由 Controller 应用层实现，网关负责执行层扣减与记账 | 全部自研 |
| 审计 | 花费与调用记录入库（Postgres） | 自研 |
| 取消 | 客户端断开后的上游取消传播**待实测** | 自研 |
| 并发限额 | per-key tpm / rpm | 自研 |
| 多协议前端 | OpenAI 兼容端点 + anthropic passthrough（细节待验证） | 自研 |

## 建议

MVP 采用 **LiteLLM Proxy**（Docker 部署，Postgres 复用 SG 主库）。职责划分：Controller 在任务创建时按 run 预算签发短期虚拟键（模型白名单 + 预算 + 速率），任务结束回收并结算；网关执行限额与记账。这与架构 §11 "run 级原子费用预留" 不冲突——预留是应用层记账行为，网关是强制执行点。两套前端协议（OpenAI 兼容给 Hermes、anthropic 端点给 Pi）由 LiteLLM 统一承接，上游统一指向火山网关。

## 待实测

1. LiteLLM 接火山 anthropic 兼容上游（custom provider base_url 配置）与**流式工具调用透传**
2. 客户端取消 → LiteLLM → 火山上游的取消传播
3. Hermes custom provider 指向 LiteLLM OpenAI 端点端到端
4. Pi models.json 指向 LiteLLM anthropic 端点端到端
5. 火山网关侧已有的配额/用量/并发能力（控制台确认，避免重复建设）
