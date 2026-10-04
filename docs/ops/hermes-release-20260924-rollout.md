# 个人助手切换官方 Hermes v2026.9.24（2026-10-04）

已按用户选择及 Python 环境变更授权，在 sg-prod 将个人助手切换至官方 **Hermes Agent v0.21.5 / v2026.9.24**。WebUI 仍为 v0.11.4，继续直接请求助手 Gateway。Core、Worker、Runner、研究/实验运行时及正式 ResearchRelease 保持原状。

## 版本与构建身份

| 项目 | 固定值 |
| --- | --- |
| 官方 Release | https://github.com/NousResearch/hermes-agent/releases/tag/v2026.9.24 |
| 官方完整提交 | `f97608f178d1ffeca59860195ab7da295f7c8e5f` |
| trading-assistant 提交 | `f594ec5765d3d71c68b6e69d58e416a84aece018`，已推送 main |
| 已部署镜像 | `ghcr.io/youweichen0208/trading-assistant@sha256:b99e7edb7da1ffc42d7ef7bb2a47a06bb44b1332d2db083568a187cbe514aec1` |
| Python | 3.13.16；`python:3.13-slim@sha256:bb2988715db2cf7ace7b53f38f3cffbef7c7046a656bee66245eb0ed386e2e81` |
| uv | 0.11.16；`sha256:440fd6477af86a2f1b38080c539f1672cd22acb1b1a47e321dba5158ab08864d` |
| 上游补丁 | 无；pyproject.toml 与 uv.lock 同官方 tag 字节一致 |

这次是从 2026-09-27 main 固定提交回到 2026-09-24 正式 Release，不称为代码时间线上的升级。该 Release 要求 Python `>=3.11,<3.14`；用户明确同意个人助手改用独立 Python 3.13。仓库指引同步区分个人助手 3.13 与研究/实验 3.14。

上游依赖通过原始 `uv sync --frozen --no-dev --extra messaging` 安装。这个 Release 内置 DDGS provider，但未定义后来加入的 `ddgs` extra；助手独立的 `infra/ddgs-requirements.txt` 固定 DDGS 9.16.0、primp 2.0.0、lxml 6.1.2、click 8.4.2 及包 hash，与之前已验证版本一致，click 与 Release 锁一致。通过 `uv --no-config pip install --require-hashes --no-deps` 安装这组可选依赖，运行时禁止 lazy installs。未重写上游锁、未放宽上游 Python 约束。

镜像标签 `io.youwei.hermes.release`、`io.youwei.hermes.revision` 和 `/opt/youwei-assistant/upstreams.lock.json` 保存身份。镜像内官方 pyproject SHA256 为 `6f969b9fdff95e7269ec808361e3ae8be5357036e6257e53a3306878cf8c41dd`，uv.lock 为 `5b3798f326209475abca8ef7cbf7c9406f12e687c28c0b540dfe597466f48590`，与官方 checkout 比对一致。

## 实际验证

- 助手独立 Python 3.13 开发环境：12 项测试通过；依赖一致性检查 85 packages compatible。本机为 3.13.14，目标镜像/CI 为 3.13.16。
- 官方完整 SHA 的原生验收：发现、401、429 并发拒绝、断流恢复、错误输入、五工具循环、追问与 SSE、memory、知识修订/删除、跨聊天读取、备份恢复及重启 **PASS**。使用 mock 模型，不访问生产凭证。
- [GitHub CI](https://github.com/youweichen0208/trading-assistant/actions/runs/37171045115)：独立安装、12 项测试、原生验收、amd64 镜像构建全部通过。
- VM `ops/verify_assistant_webui.py --assistant-image <上述镜像> --webui-image <现用v0.11.4镜像>`：完整 mock 对话、鉴权、发现、历史、知识、重启、镜像恢复、发现失败时后台普通模型分流、关闭注册 **PASS**。
- VM `ops/verify_assistant_release.py --archive <备份>/hermes-data.tar.gz --metadata <备份>/hermes-image.json --candidate-image <上述镜像>`：先按原记录镜像校验备份身份，再用候选镜像恢复到私有临时目录；全程 `--network none`、无生产卷和凭证，启动/重启均 **PASS**。保留 2 个会话、12 条消息的行内容 hash，5 个数据库完整；生产当时没有独立 memory/knowledge 文件，知识闭环由上述 mock 场景覆盖。
- 平台相关备份身份、WebUI 配置测试 **8 passed**；未修改 Core 业务代码、数据库 schema 或共享契约，未重复运行平台全量。此前三仓库部署的 705 项全量结果属于 S12h。
- 线上 `hermes --version` 明确输出 `Hermes Agent v0.21.5 (2026.9.24)`、Python 3.13.16；健康、未认证 401、认证发现、WebUI 认证模型发现 **PASS**。WebUI 当前 5 条聊天；容器与数据卷未更换。全部其他常驻服务的 container ID / StartedAt 与切换前一致。
- 当前聊天栈完整锁与 manifest 位于 `infra/releases/20261004-hermes-v20260924/`，`VALID deployment`；实际部署的渲染 Compose 与验证文件字节相同。含凭证的渲染配置仅存 VM 0600 文件，不上传。
- 切换后备份 `/opt/youwei/backups/chat/daily/chat-backup-20261004-023723`，隔离恢复 **ALL PASS**：WebUI 5 条聊天/44 张表，LiteLLM 115 条日志/7 条 token，助手 5 个数据库与固定镜像身份、归档 hash，秘密独立保存且权限 0600。

## 部署过程与回滚

首次镜像构建遗漏新增文件的 Docker context 放行，修复 `.dockerignore`；随后可选依赖安装继承上游 uv override 范围约束，与 require-hashes 冲突，改为该安装步骤 `--no-config`。修复仅在助手构建层，上游未改；两次失败保留在 CI 历史。

首次切换后助手已健康，但检查名单误包含正在清理的隔离测试容器，自动回滚到原镜像和原卷，确认健康。名单修正为实际常驻服务后第二次切换成功。原副本与首次切换副本均为 2 个会话/12 条消息，没有丢失数据。没有再次重启其他服务。

切换时先停个人助手，再完整复制并逐文件 hash 核对；最终使用：

- profile：`youwei-chat_hermes_profile_v20260924_20261004_r2`
- knowledge：`youwei-chat_hermes_knowledge_v20260924_20261004_r2`

容器内路径、环境变量、服务名、网络和备份格式均保持。原卷 `youwei-chat_hermes_profile` / `youwei-chat_hermes_knowledge` 和首次副本保留。回滚 Compose 为 `/root/hermes-release-20260924-20261004/rollback/compose.json`，原助手镜像 digest 为 `8be71b9ac8df8019c3e6a2dac5321b8fba384c99e2baaf76e848284a622b9f10`。

回滚前先备份当前新增数据；停止助手，将上述原 Compose 恢复到 `/opt/youwei/chat/compose.json`，用既有 secrets.env、项目名 youwei-chat 执行 `up -d --no-deps hermes-assistant`，确认健康。原 Compose 指向原卷，无需对新卷做逆向迁移；保留新卷用于处理切换后的新会话。WebUI 和平台无需一起回滚。

本轮自动验收没有发起真实付费模型调用、生产研究提交或正式前向评估；已有用户流量与验收流量分别对待。未验证完整真实证券研究质量；同机备份仍不是独立故障域备份。平台原有未提交改动保留，助手发布提交已推送。
