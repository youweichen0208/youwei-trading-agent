# Hermes 工作台发布记录（2026-10-04）

当前聊天栈：WebUI v0.11.4 + owner-scoped Hermes 工作台（`/agent` 四入口 + `/api/v1/hermes` 桥接），助手源码 df63763（含只读技能服务），Hermes 上游不变 v2026.9.24。完整验证、构建身份与回滚见[部署记录](../../../docs/ops/hermes-workbench-rollout-20261004.md)。

- `chat.lock.json` / `chat.manifest.json` 绑定本次部署：openwebui 换新镜像并接线四个 `HERMES_WORKBENCH_*` 环境变量；hermes-assistant 换新镜像；新增 hermes-workbench-skills 只读技能服务（同一助手镜像，`python /opt/youwei-assistant/workbench.py`，8643）。
- `preflight-evidence.json`：CI 镜像构建、VM 构建身份、隔离技能读取、跨服务 mock 验收、切换前备份。
- `live-evidence.json`：实际切换后检查（健康、桥接端点、旧聊天、公网入口、部署后备份）。

含凭证的渲染 Compose 只在 VM `/root/workbench-20261004/candidate.rendered.json`（0600）；不上传。实际部署的 `/opt/youwei/chat/compose.json` 与评审候选字节一致（sha256 `ea905c45…`）。
