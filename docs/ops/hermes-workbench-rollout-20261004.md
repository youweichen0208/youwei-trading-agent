# Hermes 工作台上线（2026-10-04）

按用户要求把 sg-prod 上的 Web 替换为当前 youwei-webui develop（含 Hermes 工作台），并完成与 Hermes 助手的对接。Core、Worker、Runner、研究/实验运行时、LiteLLM、PostgreSQL 均未变更。

## 版本与构建身份

| 组件 | 固定值 |
| --- | --- |
| WebUI 源码 | youwei-webui develop `a94b7bbbbb81d59749a627958f2df1c2b5c7691c`（= 0.11.4 archive + d0f0472 工作台 + 构建堆/CI 恢复） |
| WebUI 镜像 | `ghcr.io/youweichen0208/youwei-webui@sha256:171e0e83a7b03b29d1d3552dc637b1e71526085278a3717dc92989ce4cbd630f` |
| 助手源码 | trading-assistant develop `df63763f86cf86d3e76343f5b416b0b9ff14cb65`，已推送 |
| 助手镜像 | `ghcr.io/youweichen0208/trading-assistant@sha256:5686c66c00568511ea645c8402cfad6b37ae4019aceb12674ba27eb999618171`（tag `workbench-20261004`） |
| Hermes 上游 | 官方 v2026.9.24 / `f97608f178d1ffeca59860195ab7da295f7c8e5f`，无补丁 |

WebUI 镜像由 GitHub Actions [Youwei image 工作流](https://github.com/youweichen0208/youwei-webui/actions/runs/37211620457)从 develop 源码构建发布（develop 重置后恢复了 8 GB 前端构建堆与源绑定镜像 CI，两个提交已推送）。助手镜像在 sg-prod 用 `git archive` 干净源码包（sha256 `2290053f…`，无 macOS 元数据）按既有 Dockerfile 构建 amd64 并推送。

## 工作台接线

- `openwebui` 新增环境变量：`HERMES_WORKBENCH_URL=http://hermes-assistant:8642`、`HERMES_WORKBENCH_KEY=${YOUWEI_ASSISTANT_API_KEY}`（与助手 `API_SERVER_KEY` 一致）、`HERMES_WORKBENCH_OWNER_ID=229e8589-71b1-4d11-94bb-0fc37abe11bf`（现有唯一 admin，非邮箱）、`HERMES_WORKBENCH_SKILLS_URL=http://hermes-workbench-skills:8643`。密钥只存在于 VM 0600 secrets.env，不进浏览器。
- 新增 `hermes-workbench-skills` 服务：同一助手镜像，入口 `python /opt/youwei-assistant/workbench.py`，只读挂载生产 `hermes_profile` 卷，`--read-only` + `cap_drop ALL` + `no-new-privileges`，仅 chat 内网，无主机端口。与平台 `infra/compose/chat-workbench.json` 叠加设计一致。
- `hermes-assistant` 同步换至新助手镜像（新增技能读取器 + 已本地验证的工具策略/引导重构；环境、卷、网络不变）。
- 前端入口 `/agent`、`/agent/skills`、`/agent/cron`、`/agent/gateway`；后端桥接 `/api/v1/hermes`（owner 专属、方法/路径允许列表、幂等键、SSE 透传）。设计边界见 youwei-webui `docs/hermes/README.md`。

## 实际验证

| 层次 | 结果 |
| --- | --- |
| youwei-webui CI（工作台路径） | bridge pytest + vitest + vite build 通过（run 37210665665） |
| youwei-webui 镜像 CI | 构建并按源码 SHA 发布（run 37211620457，6m20s） |
| trading-assistant CI | df63763 verify 通过（run 37210602303） |
| 隔离技能读取（VM） | 新镜像 + 生产 profile 只读 + `--network none --read-only`：未认证 401，认证目录 200 共 53 个技能；测试容器已清理 |
| 跨服务 mock（VM） | `ops/verify_assistant_webui.py` 双新镜像：cutover/发现/对话/历史追问/SSE/知识/重启/备份恢复/后台分流/关闭注册/历史保留 **PASS** |
| 切换 | 仅重建 openwebui、hermes-assistant，新建 hermes-workbench-skills；litellm/postgres 容器 ID 与 StartedAt 不变 |
| 线上验收 | 三容器健康；`/api/version` 0.11.4；四个 `/agent` 路由 200；未认证桥接 401；短期 owner JWT（10 分钟）下 config `{"enabled":true}`、capabilities/gateway 健康全 ok、skills 53 个 + 正文、既有会话列表、jobs 空；模型发现含 Hermes 美股助手；旧 5 条聊天与单用户保留；公网 `https://trading.youwei-agent.com` 200 |
| 备份 | 切换前 `chat-backup-20261004-151819`、切换后 `chat-backup-20261004-152218`，`chat_backup_verify.sh` **ALL PASS**（含助手 5 库镜像隔离恢复） |

WebUI 数据卷沿用 `youwei-chat_openwebui_data_v0114_20261004`，同为 0.11.4 schema，无迁移。会话/任务仍由 Hermes 独占存储，WebUI 数据库不复制。

## 回滚

回滚材料在 VM `/root/workbench-20261004/rollback/compose.json`（旧 WebUI digest `1ae57fd42c21…`、旧助手 digest `618f820ef393…`、无 skills 服务）。先备份切换后新增数据，再恢复该文件并执行：

```bash
docker compose -p youwei-chat --project-directory /opt/youwei/chat \
  -f /opt/youwei/chat/compose.json --env-file /opt/youwei/chat/secrets.env \
  up -d --remove-orphans
```

## 未验证项与边界

- 未用真实付费模型跑工作台对话、审批、cron 自然语言创建的端到端业务验收（桥接链路已用短期 owner JWT 验证到 Hermes 原生接口；mock 链路已全通）。浏览器人工操作待用户确认。
- 技能写入/自我改进审批、平台配置写入、网关启停等在设计上未开放，页面如实提示。
- 同机备份仍不构成独立故障域。
