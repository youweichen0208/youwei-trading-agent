# 聊天栈部署记录（LiteLLM 网关 + Open WebUI）

日期：2026-10-02。环境：sg-prod（DigitalOcean，Docker 29.5.2）。范围：所有者个人 LLM 聊天入口（`youwei-chat` compose 项目）+ S07 agent-runtime 镜像发布。**不含**研究管线的 LLM 接入（Phase 1B 数据转发授权未取得，S07 真实网关验证矩阵另批）。

## 决策（所有者 2026-10-02）

- LLM 上游复用本地 pi agent 的火山引擎 key（Anthropic 兼容端点，北京区 API 网关；key 与 Bearer 同值）。
- 子域名 `trading.youwei-agent.com`；公网入口走国内阿里云 ECS（cn-shanghai，`8.159.158.155`）中转——**备案问题待所有者确认**（未备案则非标准端口或退回 SG 直连）。
- 聊天不设预算上限（独立虚拟 key，`max_parallel_requests=8` 防失控）。
- 范围：S07 Hermes 收尾 + 聊天入口；Phase 1B 启用与 S08 不在本轮。

## 部署内容（`/opt/youwei/chat/`，compose 项目 `youwei-chat`）

| 服务 | 镜像（digest 固定） | 说明 |
| --- | --- | --- |
| postgres | `postgres:16-alpine@sha256:721873c3…`（复用生产同 digest） | LiteLLM 专用库（密钥/spend logs），独立卷，无发布端口 |
| litellm | `ghcr.io/berriai/litellm@sha256:f8043697479513b9ae6f3abac8e62d61b908cfd31541cc3c3c994e16a28d784e`（v1.102.1，与登记候选版本一致） | 5 模型（glm-5.3 / glm-5.3-flash / deepseek-v4-flash / deepseek-v4-pro / qwen3.8-flash），Anthropic 兼容火山端点 + Bearer 头（S01 已验证模式），`127.0.0.1:4000` |
| openwebui | `ghcr.io/open-webui/open-webui@sha256:0b73f17a1e63c024ec6bb80a2c0d4a6d79dfd798a5b608531c52e340e6ef168f`（v0.6.36） | `OPENAI_API_BASE_URL` 指向 LiteLLM `/v1`（chat 虚拟 key），`127.0.0.1:8090`，自带认证，数据持久卷 |

- 仓库资产：`infra/compose/chat.json`（无秘密，`${VAR}` 插值）、`infra/chat/litellm-config.yaml.template`（`__VOLC_KEY__` 占位）、`ops/deploy_chat.sh`（secrets/up/key/webui/status 幂等分相）。
- 凭证：`/opt/youwei/chat/secrets.env`（0700 目录 / 0600 文件）：`VOLC_API_KEY`（经 stdin 迁移自本地 pi 配置）、`LITELLM_MASTER_KEY`、`LITELLM_PG_PASSWORD`、`YOUWEI_CHAT_KEY`；`config.yaml` 渲染后 0600。
- 资源限额：litellm 1G/1CPU、openwebui 768M/0.5CPU、postgres 512M/0.5CPU；日志轮转 10m×3；`restart: unless-stopped`。

## 验证（2026-10-02 实测）

- 模型列表（chat key）：5 个模型全部可见。
- 真实调用：`glm-5.3` 正文正常（finish=stop）；`deepseek-v4-flash` 流式 168 个 SSE chunk；usage 入 `LiteLLM_SpendLogs`（含请求计数）。
- Open WebUI：`/health` 200、容器 healthy；首启自动下载 RAG 依赖（约 25s）；**注册当前开放**（首个注册者成为管理员）——所有者创建账户后应将 `ENABLE_SIGNUP` 改回 `False` 重新部署。
- 虚拟 key 隔离：chat key 仅见 5 模型；master key 仅存于 secrets（管理面）。

## agent-runtime 镜像发布（S07 收尾项）

- `youwei/agent-runtime:dev`（S07m 本地构建）已推送 `ghcr.io/youweichen0208/youwei-agent-runtime:phase1a-s07`；**registry digest `sha256:998f060eb0f7fadaa1712e9540370a4dc1e79995b56f1a41a9c1f7b1e892f153`**（与本地 manifest digest 一致），按 digest 拉取验证通过。upstreams 登记 `deployment.image` 保持 null：镜像已可拉取，但是否/何时进入研究部署归 Phase 1B 决策（当前 Runner 未在生产 compose 部署）。

## 已知限制与待办

- **公网入口未接**：等所有者回答备案问题；方案 A（ECS 443，需备案）/ B（ECS 8443 非标端口）/ C（SG 直连 443）。DNS `trading.youwei-agent.com` A 记录由所有者添加。
- 成本记录：GLM/DeepSeek/Qwen 不在 LiteLLM 默认成本表，当前 spend 只记 token 不记金额；自定义 cost map + 火山计费对账为待办（llm-gateway-options 遗留项）。
- 未测（llm-gateway-options 待测清单）：取消传播、fail-closed（计数器故障行为）、并发/速率限额生效证明——随 S07 研究链路真实网关验证批补齐。
- LiteLLM 管理面（`/key/*`）仅绑 `127.0.0.1`，经 SSH 访问；研究链路接入时再决定 agent-runtime 网络如何到达网关。
- 备份：聊天栈为可重建基础设施（openwebui 数据卷含账户/聊天记录，未纳入 pgbackrest；重要时可加 dump cron）。
