# 个人助手官方 Release 发布记录

当前聊天栈：官方 Hermes v2026.9.24 / Python 3.13.16，助手源码 f594ec5，WebUI 保持 v0.11.4。完整验证、构建身份与回滚见[部署记录](../../../docs/ops/hermes-release-20260924-rollout.md)。平台生产服务仍使用前一 `../20261004/production.lock.json`。

`chat.lock.json` / `chat.manifest.json` 绑定本次部署；`preflight-evidence.json` 是候选验收，`live-evidence.json` 是实际切换后检查。`initial-deployment-evidence.json` 保留未变更聊天组件的前次验收证据。文件中的证据路径相对于本目录。

含秘密的渲染 Compose 只在 VM `/root/hermes-release-20260924-20261004/chat.resolved.json`（0600）；不上传。实际部署配置与该文件已比对相同，deployment 校验通过。两个较早的构建失败和首次因检查名单误纳临时容器而自动回滚如实记录在部署文档。
