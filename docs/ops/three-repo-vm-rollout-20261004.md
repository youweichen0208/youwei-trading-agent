# 三仓库 VM 替换完成（2026-10-04）

用户授权把三个仓库部署到现有 VM 替换旧服务。已按授权在 sg-prod 完成替换；用户明确选择遵循 youwei-webui 最新版本（v0.11.4）。公网入口 https://trading.youwei-agent.com 已验证健康、版本、HTML 和编译后前端资源均返回 200。

后续个人助手已按用户选择切至官方 v2026.9.24 / Python 3.13；下面保留三仓库首次切换记录。当前助手版本与回滚见 [后续 Release 切换](hermes-release-20260924-rollout.md)。

## 现状与版本决策

- 目标：sg-prod，4 vCPU / 7.8 GB，根盘剩余约 107 GB；已有 Core r7、Runner s12g、研究 Hermes r4、官方 Open WebUI v0.6.36、Dashboard r4。
- youwei-webui 当前 main 的 package version 为 0.11.4，SHA `8bd8b4fac`；与既有已验收 0.6.36 不是同一基线。用户已选择当前最新版本。远端 main 已重新 fetch，仍为此 SHA；VM 上原样构建与 4 GB 前端堆均实测 JavaScript heap out of memory（exit 134）。`youwei` 分支随后仅调整构建堆为 8 GB 并增加独立镜像 CI，提交 `0a8aa48ef454a9e1c73df1a3d73e980e332521dc` 已推送；[GitHub Actions](https://github.com/youweichen0208/youwei-webui/actions/runs/37162411974) 构建发布成功，运行功能仍为 main 的 v0.11.4。
- 平台当前源码变化为只读日线 API；实际只替换 Core API，保持负责正式批次的 Worker/Runner/研究镜像。没有批准新 release 或扩大已登记代码例外。

## 已完成构建与发布

| 组件 | 来源 | amd64 registry digest |
| --- | --- | --- |
| trading-assistant | commit `3ff91cf2f78a001c8454497d75ea6e0cfa3f7374` | `ghcr.io/youweichen0208/trading-assistant@sha256:8be71b9ac8df8019c3e6a2dac5321b8fba384c99e2baaf76e848284a622b9f10` |
| youwei-webui | commit `0a8aa48ef454a9e1c73df1a3d73e980e332521dc`，v0.11.4 | `ghcr.io/youweichen0208/youwei-webui@sha256:1ae57fd42c216bfb165f0888ed2b0f42b1e23a9a6c22dc6edc6b6c78a2c9ec3b` |
| Core API | 当前工作区的显式源码归档，尚未提交；见下面 hash | `ghcr.io/youweichen0208/youwei-core@sha256:678077eee1284099176d16172275455e206af8fc561ab84d00f075ba171de58e` |

Core 使用现有固定 Python 3.13 基础镜像 `sha256:bb2988715db2cf7ace7b53f38f3cffbef7c7046a656bee66245eb0ed386e2e81`、固定 uv `sha256:440fd6477af86a2f1b38080c539f1672cd22acb1b1a47e321dba5158ab08864d`。助手使用自身固定 Dockerfile/上游锁，无升级。

首次 Core 归档带入 macOS AppleDouble 元数据文件，镜像 `sha256:949bf1fdbe217283ba9d8dfd2d24c033db7732d564bf8bf1732c0d88c4cba03a` 已发布但**不采用、不部署**。改用 Python tarfile GNU 格式过滤元数据后重建为上表候选；未通过浮动标签覆盖来掩盖首次结果。

Core 干净源码归档 SHA256：`49d4144e8b25f5db79a264b59778d97f0722b1100238cdff6f6f3d1aa987c7a1`。目标机路径 `/root/three-repo-rollout-20261004/youwei-core-assistant-clean.tar`，源码 `/root/three-repo-rollout-20261004/core-clean/`；构建和推送日志同目录保留。没有把 .env、个人数据或凭证打入源码归档。

## 实际验证与备份

- 切换前备份 `/opt/youwei/backups/chat/daily/chat-backup-20261003-230937`，秘密另存既有 secrets 备份树。只读核对 WebUI 为一个 admin、3 条聊天、旧 Pipe `youwei_research_pipe` 活跃。
- VM 上 `chat_backup_verify.sh` → **ALL PASS**：LiteLLM 100 条日志/6 条 token 恢复、WebUI 3 条聊天/25 张表、SQLite integrity、附件/向量目录、部署配置、秘密归档权限。
- 首次恢复及本地测试暴露 PostgreSQL 临时初始化服务被 socket 就绪检查误判：恢复紧接着 createdb 失败，本地 Alembic 连接失败。改为 `pg_isready -h 127.0.0.1` 等最终 TCP 服务；修复后相关测试 **18 passed**，完整 `uv run --frozen pytest -q` **704 passed，无 skip，199.58s**。fixture 与备份恢复脚本均修正。
- VM 隔离运行 `ops/verify_assistant_webui.py --assistant-image <上表助手digest>` → **PASS**：真实固定 WebUI + 助手 amd64 镜像 + mock 模型，发现/鉴权接线、完整历史追问/SSE、来源知识、重启、镜像内无网络恢复、旧聊天保留、注册关闭、普通模型发现失败时后台分流。临时容器/卷/网络由脚本清理。
- 新 Core 镜像注册 `/v1/data/daily-bars` 的无网络构造检查通过。最终干净镜像在 VM 临时候选实例连接现有数据，只执行读取：未认证 401、租户认证 200，SPY 2026-09-28 至 10-02 返回 5 根日线。包内 Python 文件与线上 r7 比较：共有文件仅 app.py 改变，新增 data/assistant.py；旧镜像额外的无引用 ops/config.py 打包残留不在候选中。临时候选实例已清理。

## 正式切换与最终验证

- 2026-10-04 01:48–01:52 UTC（北京时间 09:48–09:52）完成切换。Core API、个人 Hermes、WebUI 均健康；实际 Compose 与通过检查的候选字节一致。`infra/releases/20261004/` 保存三仓库源身份、两个完整组件锁、manifest、切换前和切换后证据；目标机两组 `validate_upstreams.py --mode deployment` 均 **VALID**。含凭证的渲染 Compose 只保留在 VM 0600 文件中。
- VM `ops/verify_assistant_webui.py --assistant-image <固定digest> --webui-image <v0.11.4固定digest>` **PASS**：新 WebUI 真实镜像、原生助手、mock 模型；发现、鉴权、追问、SSE、知识、重启、无网络镜像恢复、普通模型发现故障下后台分流、关闭注册、旧聊天保留。
- VM `ops/verify_webui_upgrade.py --archive <切换前备份>/openwebui-data.tar.gz --webui-image <固定digest>` **PASS**：无网络迁移、3 条聊天 JSON 字节 hash 一致、所有者和密码 hash 不变、私有模型 grants、关闭注册、重启。首次校验脚本对配置数值调用 json.loads 失败；修正为保留 SQLite 原生数值后完整重跑通过，不改迁移内容。
- 线上只读验收 **PASS**：Core 未认证日线 401、助手独立 tenant key 查询 SPY 返回 5 根日线；Hermes 未认证发现 401、认证发现正确；WebUI 认证模型发现含 Hermes/glm-5.3，旧 Pipe 不再活跃，3 条旧聊天逐条与原卷一致、注册关闭；专用 LiteLLM key 可发现允许模型；WebUI `/api/version` 为 0.11.4。
- Worker、Runner、两套 PostgreSQL、LiteLLM 的 container ID 和 StartedAt 与切换前完全一致。只更换 Core API，未重建这些服务。未变更正式 ResearchRelease、未调用正式评估。
- 备份脚本及同目录 `assistant_image.py` 已安装到实际 cron 路径 `/opt/youwei/chat/`。切换前最终备份：`chat-backup-20261004-014218`；切换后完整备份：`/opt/youwei/backups/chat/daily/chat-backup-20261004-015244`。`chat_backup_verify.sh` **ALL PASS**：LiteLLM 100 条日志/7 条 token，WebUI 3 条聊天/44 张表、SQLite integrity、附件和向量目录、Compose；助手 5 个数据库的镜像隔离恢复及归档 hash，秘密另存且权限 0600。生产新 profile 目前没有知识笔记；知识保存/读取/恢复已在隔离 mock 场景验证。
- 最终本地完整回归 `uv run --frozen pytest -q`：**705 passed，无 skip，202.49s**。本次实际全量执行发生在切换前；之后仅迁移验收脚本读取兼容及部署记录更新，脚本已在 VM 实际运行。主 catalog、助手 catalog、两组发布 catalog、部署清单、shell 语法、Python 编译及差异空白再次检查。

## 数据保留与回滚

新 WebUI 卷 `youwei-chat_openwebui_data_v0114_20261004` 来源于旧 WebUI 停服后的完整复制，容器内路径仍为 `/app/backend/data`；原卷 `youwei-chat_openwebui_data` 未升级、未删除。原签名密钥复用，账号和密码保留。回滚 Compose 在 `/root/three-repo-rollout-20261004/rollback/`。失败时部署驱动会恢复旧服务定义和原卷；本次未触发回滚。

如果以后需要回滚：先备份切换后的新增聊天/知识，停止新 WebUI 和助手；恢复 rollback/chat.compose.json 与 rollback/production.compose.json；沿用既有 `ops.s09b_deploy.compose_env('production')` 合并全部生产配置，仅 `up -d --no-deps core-api`，聊天栈仅 `up -d --no-deps openwebui`，再检查健康。旧 WebUI 自动挂回未升级原卷；不可让旧版本直接打开 v0.11.4 的数据库。保留新卷，回滚旧卷不会自动带回切换后的新聊天。不要删除任何卷。

## 未验证项与交付边界

未调用真实付费模型、生产研究提交或正式前向评估，未执行 ResearchRelease 批准。聊天链路为真实镜像配 mock 模型验证，线上只执行鉴权、发现和已有数据读取；公网资源可达不等于人工浏览器交互已验收。完整真实证券研究和引用质量仍待业务验收。备份仍在同一 VM，不宣称独立故障域备份已完成。

平台镜像来自明确文件清单及 hash 的当前工作区归档，平台原有未提交改动全部保留，未声称已提交或推送；助手源码已推送 3ff91cf，WebUI 构建变更已推送 youwei 分支 0a8aa48。服务运行镜像全部以 digest 固定。

## 最新版适配与切换设计

- v0.11.4 的配置表为逐 key/value 行，模型权限迁入 access_grant；平台接线脚本兼容两个已验证 schema。新增用例先观察旧脚本报 no such column: id，再实现，保留单所有者、关闭注册、模型私有权限、普通后台模型、旧聊天及无关配置/权限。相关 21 项测试通过。
- 新版将在最终停服副本的新命名卷升级，原 `youwei-chat_openwebui_data` 保留原格式；回滚切回旧 Compose/旧卷，无需对升级后数据库执行不确定的降级。容器内路径保持 `/app/backend/data`。
- 已准备助手独立 Core tenant key、glm-5.3 专用 LiteLLM key（并发1）与 gateway key；仅写入目标机受控 secrets.env（0600），没有输出密钥值。已保留 WebUI 原签名密钥以维持现有会话兼容。

WebUI CI 实际解析基础镜像：Node22-alpine3.20 `sha256:2289fb1fba0f4633b08ec47b94a89c7e20b829fc5679f9b7b298eaa2f1ed8b7e`；Python3.11-slim-bookworm `sha256:2333bd330d12de02514770b3585cad313644316047cdee24a7acfdece6de6efb`；uv0.12.10 `sha256:2bb3ebca0a796a155094a27773d290c4b074572e6107f171d88d086682fd2500`。最终发布以完整镜像 digest 固定，原上游 Dockerfile 的标签输入尚未改写为 digest。

## 部署后版本显示复核（2026-10-04）

用户报告页面显示 `Open WebUI ‧ v0.6.36`。只读复核：公网 `/api/version`、`/api/config`、VM 8090 的版本均为 0.11.4，运行镜像 digest 与本次清单一致；公网首页的 Last-Modified 为本次构建时间。使用临时新浏览器上下文和短期所有者认证进入实际页面，再打开“设置 → 关于”，自动断言 `v0.11.4` 存在、`v0.6.36` 不存在，结果 PASS。没有复现用户旧标识，旧标签页保留前端仅为待用户强制刷新验证的推断；本次未改生产配置、未清除用户浏览器数据。该检查补充版本显示的浏览器验收，不代表真实模型聊天或所有浏览器操作已验收。
