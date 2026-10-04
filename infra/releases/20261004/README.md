# 2026-10-04 三仓库发布清单

发布操作与最终验收见 [部署记录](../../../docs/ops/three-repo-vm-rollout-20261004.md)。本目录保存无秘密的源身份、镜像锁、部署清单及验收证据。

- `chat.lock.json`：WebUI v0.11.4、个人 Hermes、既有 LiteLLM/PostgreSQL。
- `production.lock.json`：仅 Core API 换新镜像；Worker、Runner、数据库和运行时保持既有版本，各自绑定镜像。
- `core-api-build-inputs.json`：当前未提交平台源码的精确文件 hash 和归档身份，不声称镜像等同于旧 Git HEAD。
- `preflight-evidence.json`：切换前实际验证，包括无网络旧数据迁移与 mock 跨服务验收。
- `*.manifest.json`：对应锁和渲染 Compose 的 SHA256；不是 ResearchRelease 批准。

原始渲染 Compose 含凭证，仅存目标机 `/root/three-repo-rollout-20261004/{production,chat}.resolved.json`（0600），不上传。证据路径相对于本目录。完整校验在目标机运行：

```bash
python3 /root/three-repo-rollout-20261004/validate_release.py
```

该脚本沿用既有 `ops.s09b_deploy.compose_env` 合并 production.env、config.env 和目标机仓库的供应商凭证；不能仅传 production.env 假定配置齐全。两组均须输出 `VALID deployment`。本目录可独立做 catalog 检查；deployment 还需要受控的实际渲染配置。
