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

- **公网入口（2026-10-02 上线）**：备案经运行证据确认有效（`market.youwei-agent.com` 一直从该 cn-shanghai ECS 标准 443 服务）。DNS（Cloudflare）两条 **灰云** 记录由所有者添加（初次误开橙云已纠正）：`trading A 8.159.158.155`、`sg-chat A 168.144.39.34`。两端 certbot 发证成功（均有效至 2026-12-31，systemd timer 自动续期在位）。验证：`https://trading.youwei-agent.com/health` → 200（公网路径全链：浏览器 → ECS TLS → SG TLS → Open WebUI）；未认证 websocket 被应用拒绝属预期（应用侧行为，非代理问题）；SG 侧 vhost 对非 ECS 来源 403（allowlist 生效）。配置：`infra/chat/nginx-ecs-trading.conf`、`infra/chat/nginx-sg-chat.conf`（allow/deny 在 location 级，避免挡 ACME 验证；代理 127.0.0.1:8090）。
- **入口性能优化（2026-10-03）**：所有者反馈页面 loading 久——分段计时定位：每请求重付 CN→SG TLS 握手（~1.2s/次，上海↔新加坡 RTT ~300ms），页面 HTML 单独就要 6s，SPA 静态资源逐个回源叠加。修复三项：①ECS nginx `upstream sg_chat_backend` keepalive 池（16 连接，握手一次摊平；变量 proxy_pass 不支持 keepalive，改 upstream 块+启动时域名解析）；②`/_app/immutable/`（内容 hash，30d）与 `/static/`（10m，custom.css 可变）本地 proxy_cache（`/var/cache/nginx/youwei-chat`，200MB）——静态资源从上海本地响应；③`Connection` 头改 map 映射（websocket 升级正常、普通请求保持连接复用）。实测：HTML 6.0s→**0.5s**（公网 0.5s）、静态资源回源 1.4s→缓存命中 **0.12s**、连续 API ttfb 2.6s→**0.4s**；登录限流与 certbot TLS 结构保留。
- 成本记录：GLM/DeepSeek/Qwen 不在 LiteLLM 默认成本表，当前 spend 只记 token 不记金额；自定义 cost map + 火山计费对账维持待办（仅为观测项：预算维度已按 2026-09-30 所有者决策删除）。
- ~~未测（llm-gateway-options 待测清单）：取消传播、fail-closed、并发/速率限额生效证明~~ **已验证（2026-10-02）**：取消传播成立（客户端中断后 5.5s 内终止上游流，spend 记 completion=0）；fail-closed 实测为 **FAIL-OPEN**（已缓存 key 在计数库不可达时照常准入并转发）；max_parallel_requests / rpm / tpm 分别生效证明齐备（TPM 为准入前预估用量检查）。详见 [s07-real-gateway-verification](s07-real-gateway-verification.md)。
- LiteLLM 管理面（`/key/*`）仅绑 `127.0.0.1`，经 SSH 访问；研究链路网络接线已落地（临时）：litellm 容器以别名 `litellm` 接入 `youwei-research` internal 网络（`docker network connect`，容器重建即失效，Phase 1B 需声明式化）；验证用研究虚拟 key（e2e/conc/rate/tpm 四枚）存 sg-prod `/root/youwei-research-verify/keys.env`（0600）。
- 备份（2026-10-02 上线，所有者确认规格）：每日 1 次（cron `40 3 * * * UTC`，`/opt/youwei/chat/chat_backup.sh`，repo 源 `ops/backup/chat_backup.sh`）——LiteLLM PG `pg_dump -Fc` + Open WebUI 一致性 SQLite 副本（容器内 Python `sqlite3.backup()`，镜像无 sqlite3 CLI）+ `uploads/`/`vector_db/`（排除 `cache/`）+ 部署时 `compose.json`；密钥（`secrets.env` + 渲染后 `config.yaml`，含 VOLC key）单独存 `/opt/youwei/backups/chat-secrets/`（0700/0600），不进常规备份目录；保留 7 日备 + 4 周备（周日复制到 `weekly/`）。失败入现有监控：`ops_status_poll.sh` 监控 heartbeat（`/opt/youwei/chat/backup-state/`），产生 `chat_backup_missing`/`chat_backup_stale`（>26h）`/chat_backup_failed` 告警，走现有告警集合投递（webhook 配置后自动生效）。隔离恢复验证通过（`ops/backup/chat_backup_verify.sh`：一次性容器 pg_restore——SpendLogs 56 行/VerificationToken 5 行；webui.db `integrity_check` + 25 表 + chat 行；uploads/vector_db 在归档；secrets 0600）；告警三路径（failed/stale/RESOLVED）与保留轮换（10→7、weekly 复制）均已实测。**异地备份维持暂缓：备份与栈同机（sg-prod），整机故障会同时丢失栈与备份，如实记录同机风险**。
- 遗留：`market.youwei-agent.com` DNS 仍指向 ECS 且 502（旧原型已拆）；是否清理归所有者。
