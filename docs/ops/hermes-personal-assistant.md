# 个人助手：平台接线、兼容验证与恢复

2026-10-04。助手原生配置、插件、构建和备份格式由 [trading-assistant](https://github.com/youweichen0208/trading-assistant) 维护。本平台只消费固定助手提交与镜像，保留 WebUI 切换、Core 接口、编排、跨服务兼容及整体备份调度。源码拆分不代表镜像发布或生产切换。

`youwei-webui → trading-assistant（Hermes gateway）→ LiteLLM / EODHD MCP / Core HTTP / 个人知识`

历史兼容基线：官方 Open WebUI v0.6.36 + Hermes `7fa45eb349a1a6f1eebc010b3fef0a9d996f386a`。用户已授权部署 youwei-webui 最新 v0.11.4；本次具体提交、数据迁移和目标机结果见 [三仓库部署记录](three-repo-vm-rollout-20261004.md)。个人 profile/知识与研究实验实例及正式 Ledger 分离；保持单所有者，基础五工具之外按明确允许列表接入 EODHD 七项查询。

当前个人助手已按用户选择切换官方 **v2026.9.24 / v0.21.5**，独立 Python **3.13.16**；源码与上游依赖锁零修改。版本身份、DDGS 可选依赖、旧数据兼容及回滚见 [Release 切换记录](hermes-release-20260924-rollout.md)。研究/实验运行时保持原版本。

随后按用户授权上线 EODHD 直接查询，实际套餐权限、镜像身份、验证与回滚见 [EODHD 部署记录](eodhd-mcp-rollout-20261004.md)。

## 部署与升级

资产：[助手构建仓库](https://github.com/youweichen0208/trading-assistant)、[Compose overlay](../../infra/compose/chat-assistant.json)、[WebUI 配置脚本](../../integrations/openwebui/configure_assistant.py)。现有 `chat.json` 的服务/卷保持可单独使用；显式合并 overlay 才引入助手。目标机必须使用自己的部署副本按字段合并，不能覆盖其路径适配。

1. 在 trading-assistant 独立构建并发布目标 amd64 镜像，取得真实 registry digest；本机 arm64 冒烟镜像不等于目标机验收。发布前将候选组件及当前聊天栈依赖合入部署登记、生成清单，运行 UPSTREAMS deployment 检查。2026-10-04 首次发布已经完成，当前启用聊天组合见 `infra/releases/20261004-eodhd-mcp/`；以后升级仍按此流程验证。
2. 建立专用 Core tenant key（当前所有者租户）和 LiteLLM 虚拟 key（alias `hermes-personal`、models `["glm-5.3"]`、max_parallel_requests=1）。后者只存在助手服务，不用 chat key/master key 代替。随机生成 `YOUWEI_ASSISTANT_API_KEY`（至少 32 随机字节）。写入目标受控 secrets.env，0600；Compose 需要 `YOUWEI_ASSISTANT_IMAGE`、`YOUWEI_ASSISTANT_API_KEY`、`YOUWEI_ASSISTANT_LLM_KEY`、`YOUWEI_ASSISTANT_CORE_KEY`。
3. 发布含日线接口的 Core API 镜像；本片无数据库迁移，不改 Worker 预测路径。先核对当前 release 绑定文件与部署例外记录；不得从本地测试通过推导发布获批。
4. 备份 WebUI 库/附件和部署配置。启动 overlay 的 `hermes-assistant`，确认 health、鉴权、基础五工具和 Core 可达。启用 EODHD 时在私密 secrets.env 注入 `EODHD_API_KEY`，仅映射给助手，另验收七项 MCP 工具发现与查询。助手不发布宿主端口，只经内部服务地址访问。
5. **停止 Open WebUI** 后，先保留原卷，将停服一致副本复制到新卷，再在挂载新卷的一次性固定镜像中运行 `python /workspace/integrations/openwebui/configure_assistant.py`（仓库只读挂载）。环境为 `YOUWEI_WEBUI_OWNER_ID`、`YOUWEI_ASSISTANT_API_KEY`、已有 `YOUWEI_CHAT_KEY`、确切 `YOUWEI_OLD_PIPE_ID`，默认数据库 `/app/backend/data/webui.db`。脚本兼容 v0.6.36 blob/access_control 与 v0.11.4 per-key/access_grant 两种实际 schema，必须先在数据副本执行真实上游迁移。脚本要求库中恰有这个 admin 所有者，在单事务内修改持久配置、私有模型 ACL、默认模型、普通任务模型及静态五模型清单（防止模型发现故障时后台请求回退到 Hermes）、关闭注册、停用旧 Pipe。保留原 Pipe 代码/配置及所有聊天，失败则事务回滚。不要删除卷或仅修改 env 来假定覆盖已有持久配置。
6. 启动 WebUI；检查默认入口、连续追问、知识闭环、标题/标签模型、停止/异常显示。生产不允许添加第二个管理员共享此 profile；新增用户前重新设计身份与记忆隔离。
7. 备份 cron 必须使用包含助手服务的合并部署 compose（仍是变量模板，不写展开的秘密）。安装新版聊天备份脚本及同目录 `ops/backup/assistant_image.py`；恢复不依赖助手源码。备份脚本由 compose 判断助手为必备产物，缺失/备份失败进入既有失败监控。

回滚：恢复切换前 WebUI 数据库和配置、停用助手服务，保留助手两个卷供调查。Core 新增只读接口无迁移回退需求；不要恢复正式 Ledger 数据或删除历史聊天来回滚聊天入口。

## 备份与镜像内隔离恢复

EODHD 凭证通过 secrets.env 随现有秘密备份独立保存，profile 配置仅保存变量引用。个人 MCP 直连不改变正式研究数据入口；套餐权限不足必须反馈，不能自动采购。未设置 EODHD key 或 MCP 不可用时仍可启动基础助手，以支持无网络恢复。新增接线只更新助手镜像和环境，不需要重新配置 WebUI 或迁移聊天/知识卷。

`chat_backup.sh` 在运行容器调用 `/opt/youwei-assistant/backup.py`，保存 `hermes-data.tar.gz` 以及 `hermes-image.json`（image ID、RepoDigests、归档 SHA256）。元数据只查询镜像身份，不保存容器环境或密钥。服务名、持久卷路径与备份归档内部格式不变。应保留对应镜像或其可拉取的 registry digest；本地 image ID 无法从 registry 拉取。

`chat_backup_verify.sh` 调用同目录 `assistant_image.py` 校验归档与镜像身份，再以 `--network none --read-only --cap-drop ALL` 启动一次性容器，只读挂载备份文件，恢复到 tmpfs，不挂生产数据或助手源码。归档存在或 Compose 声明助手时必须验证，失败使整个验证失败。

```bash
# 含镜像身份的新备份
ops/backup/chat_backup_verify.sh /path/to/chat-backup-TIMESTAMP
# 老备份缺少镜像元数据时，必须由操作者明确提供已验证的镜像
YOUWEI_VERIFY_ASSISTANT_IMAGE='registry/assistant@sha256:<verified-digest>' \
  ops/backup/chat_backup_verify.sh /path/to/old-backup
# 单独验证助手副本（也用于跨服务验收）
python3 ops/backup/assistant_image.py verify \
  --archive /path/to/hermes-data.tar.gz --metadata /path/to/hermes-image.json
```

禁止自动选 latest。指定镜像不匹配已记录 image ID、归档 hash 不符或恢复工具失败均拒绝继续。元数据/hash 用于一致性核对，不构成签名或独立故障域证明。SQLite 各库一致，不承诺数据库、memory、notes 跨存储原子快照；恢复 tmpfs 上限 512 MB，较大备份须先评估并调整验证资源。沿用 7 日 + 4 周保留、秘密独立归档。

实际恢复需先停止助手，将隔离验证通过的 profile/knowledge 副本放回原卷并修正 UID 10001，使用匹配镜像重新安装配置/插件。WebUI 的凭证与历史由其备份恢复；回滚聊天入口不恢复正式 Ledger。生产恢复另按授权操作。

## 本地跨服务验证

```bash
uv run --frozen python ops/verify_assistant_webui.py --assistant-image trading-assistant:refactor --webui-image youwei-webui:refactor
uv run --frozen pytest -q tests/contracts/test_webui_configuration.py tests/contracts/test_assistant_image_backup.py tests/test_daily_bars_api.py
python3 infra/validate_upstreams.py --mode catalog --lock infra/chat/assistant-upstreams.lock.json
```

助手镜像由独立仓库提前构建；脚本将显式传入镜像解析为本地 immutable image ID，并使用独立 `ops/assistant_mock_model.py`。真实固定 WebUI + mock 模型验收发现、追问、SSE、知识、重启、旧聊天保留、后台普通模型分流与镜像内隔离恢复，不调用真实付费模型。原生发现/工具限制/修订删除等由助手仓库验收。详细结果见实施计划 S12g/S12h。目标机部署和生产备份隔离恢复已通过；浏览器人工操作、真实付费模型与完整证券研究仍未验收。

## 候选配置准备（不部署）

旧 `chat.json` 是历史启动模板，不可覆盖现有部署副本。以下工具从实际四服务 Compose 生成新文件，只替换助手和 WebUI 镜像，保留所有环境引用、卷、路径和网络；输出权限 0600，拒绝覆盖已有文件。

```bash
python3 ops/render_chat_release.py --base /private/current-compose.json \
  --assistant-image 'registry/assistant@sha256:<digest>' \
  --webui-image 'registry/webui@sha256:<digest>' \
  --output /private/candidate-compose.json
python3 ops/render_chat_release.py --check /private/candidate-compose.json
```

生成后在受控目录检查差异、渲染 Compose 并按 UPSTREAMS 生成候选锁与清单。配置可能含秘密，不提交展开文件。`deploy_chat.sh` 的 up/webui 阶段只接受现有四服务、固定 digest 的部署副本，不再复制旧模板；它是实际运行操作，本轮未执行。

跨服务 CI 由 `Chat compatibility` 工作流执行：`infra/chat/verification-sources.json` 固定助手和 WebUI 的完整候选提交；各自独立 checkout/构建，在临时卷里运行相同验收命令，不发布镜像、不读取生产凭证。该文件是测试组合，不替代已部署锁及 ResearchRelease。

## Agent 工作台候选接线

WebUI 新增工作台通过原生 gateway 的会话、运行、审批与 cron HTTP 接口访问个人助手。需设置 `YOUWEI_WORKBENCH_OWNER_ID` 为现有 WebUI 用户 ID，并指定 `YOUWEI_WEBUI_WORKBENCH_IMAGE` 为验证后的新 WebUI 镜像；助手镜像需包含 `workbench.py`。

在现有 `chat.json`、`chat-assistant.json` 之后叠加 `infra/compose/chat-workbench.json`，它为 WebUI 设置服务端凭证与只读技能服务 URL，另起同镜像技能服务并只读挂载 `hermes_profile`。不挂载 WebUI 数据库到助手，不改变旧聊天存储，不开放技能写入或宿主执行。完整参数与行为见 [WebUI 工作台文档](https://github.com/youweichen0208/youwei-webui/blob/develop/docs/hermes/README.md)。

这是候选配置，当前部署锁和 VM 未切换；部署前仍按固定镜像与兼容验收流程验证，不能直接把浮动分支当作生产镜像。

## 2026-10-05 Marketplace 指数工具

当前允许列表为九项 MCP + 五项基础工具：原有七项加 `mp_indices_list` / `mp_index_components`。用 `GSPC.INDX` 查询当前与历史成分，先查指数列表，不自动批量下载。独立 Marketplace 配额与通用 API 权限分开；旧有基本面/财报日历仍受套餐限制。最新源码、镜像及部署验收见 [Marketplace 记录](eodhd-marketplace-rollout-20261005.md)；Extended 十九项候选未启用。
