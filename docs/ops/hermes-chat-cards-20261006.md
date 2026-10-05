# Hermes 普通聊天金融卡片：候选验收

日期：2026-10-06（上海时区）。状态：**已按用户授权部署 SG**，保留此前候选验证记录。

WebUI [PR #5](https://github.com/youweichen0208/youwei-webui/pull/5)，功能源码 `7a868661e41563f1bae33ddbfdcdd061a66cc5a5`，基于已合并 `9f038b659`。Hermes 固定 `f97608f178d1ffeca59860195ab7da295f7c8e5f`，金融包与助手生产镜像不变。[候选构建](https://github.com/youweichen0208/youwei-webui/actions/runs/37344666708) 的身份另记于本次 release 目录，候选阶段未修改生产；本轮发布使用 release 目录的 `chat.lock.json` 与 `chat.manifest.json`。

## 行为与权限

指定 Hermes 连接使用 Responses API，按所有者 ID 鉴权，完整分支历史由 WebUI 提交；不转发客户端工具定义、运行覆盖参数或 previous_response_id。工具消息转换为仅展示类型，避免 WebUI 再执行。完整增量结果随消息 output 保存，不被最终摘要覆盖，刷新不重查供应商。

展示日线图/表、指标数值、SEC 财务表及通用工具过程，保留缺失值、来源、申报日期和非正式 PIT 标识。图表按视口加载，首页无金融请求。没有新业务表、上游补丁、独立页面、研究任务轮询或知识管理按钮；停止助手不取消 Core 任务。

## 实际验证

- WebUI Python 桥接/聊天 26 项、金融组件 8 项、API/startup 11 项、侧栏 14 项通过。新增行为先失败后修复，覆盖截短、乱序、断流及中间说明/最终回答共存。
- Node 22 前端构建通过。全仓 svelte-check 仍为基线 **7001 errors / 198 warnings / 344 files**，新增图表警告已修正，不能称全仓类型检查通过。
- 固定 Hermes 原生 gateway + mock 模型/工具：三类金融结果的流式、非流式、完整结果序列化通过。fixture 初次未关闭上游 tool_search，未产生预期调用；对齐助手配置后重新通过，失败轮次保留为验证背景。
- 本机 Docker amd64 WebUI，以现行固定镜像作依赖基线、挂载本次代码/构建：真实聊天 API、保存历史、普通模型、原始 Responses 绕过阻断通过。这是挂载联调，不等于最终候选镜像恢复验收。
- 实际发布镜像：`linux/amd64`、源码标签与 [候选身份](../../infra/releases/20261006-hermes-chat-cards/candidate.json) 一致。`ops/verify_webui_upgrade.py` 对合成备份执行无网络恢复/重启，2 条聊天 JSON、账户与密码保持一致，私有授权与关闭注册检查通过。另用未挂载源码的候选容器执行 `verify_hermes_chat.py --webui-url`，三类工具流式/非流式、持久历史、普通连接与原始 Responses 阻断均通过。
- 浏览器：同一聊天三类卡片、数据表、价格口径切换、SEC 链接、刷新、390px 手机/暗色通过，无水平溢出。慢流在运行中停止后，消息 done=true、已收到内容 incomplete 保留，输入框可编辑。极早停止时观察到短暂恢复运行，任务登记/停止竞态另行核实，本次未改该机制。
- 平台配置、WebUI 初始化、聊天发布和助手备份四个契约文件共 20 项通过；开关覆盖两种配置表格式，保留其他连接、后台模型和历史。

CI：WebUI 的 bridge、native-chat、frontend、image 全部通过；平台完整 Core 测试及 research-runtime 通过（[运行记录](https://github.com/youweichen0208/youwei-trading-agent/actions/runs/37345364162)）。旧固定组合 compatibility 首轮在金融返回 JSON 断言失败（[记录](https://github.com/youweichen0208/youwei-trading-agent/actions/runs/37345364094)），已请求重跑。该流程的 WebUI 固定为 `09137c785a5a78fc2ada6878e5de05cfbe2454cf`，并非本次候选；固定来源、脚本与 mock 本次均未改，尚不能确定失败根因。

原生验证：WebUI `ops/verify_hermes_chat.py <固定 checkout>`，可加 `--webui-url http://127.0.0.1:<一次性容器端口>` 做 HTTP 联调。只使用临时 HOME、合成凭证和数据，不应指向生产。

## 首页性能回归

同一浏览器、localhost HTTP/1.1，每实例保留登录会话，冷/热各 5 次，终点为输入框可见且可编辑。原始体积、请求数、排队近似值、API 耗时和协议见 [样本](../../infra/releases/20261006-hermes-chat-cards/local-performance.json)。

| 环境 | 冷中位数 / 最大值 | 热中位数 / 最大值 |
| --- | --- | --- |
| 现行固定镜像，本地独立基线 | 1.026 / 1.042 秒 | 0.309 / 0.361 秒 |
| 本地挂载本次代码及构建 | 0.954 / 0.960 秒 | 0.217 / 0.248 秒 |

未观察到本地首页回退。临时账户、历史、模型配置和资产压缩来源不完全相同，只作冒烟回归，**不代表公网提速或 ≤15/≤5 秒目标通过**。此次未改 Nginx、未运行生产性能会话。

## 生产切换与回滚

生产发布另行执行：记录旧镜像/配置，备份 WebUI，验证候选恢复副本，再停止 WebUI 执行离线开关：

```bash
python integrations/openwebui/configure_hermes_chat.py \
  --database <已备份的-WebUI-db> --owner-id <已有所有者-ID> --enabled true
```

脚本仅修改已登记 Hermes 连接的 hermes_chat 与 hermes_owner_id，不重跑首次配置。随后只更新 WebUI，助手、金融包、Core 不切换。异常时停 WebUI，用 `--enabled false` 撤销开关并恢复旧固定镜像与匹配配置，不覆盖研究数据库。

候选阶段未验证：正式 VM 切换、生产备份恢复、公网冷/热复测、真实付费模型选工具质量、正式前向评估。后续 VM 切换与生产备份恢复结果见下节。没有真实付费模型调用或正式研究数据变更。

## SG 生产发布（2026-10-06）

用户授权将已合并功能部署到 VM。发布前核对 WebUI develop `87a3f58da21a21802441811a6f8cee5a47629a7d` 与候选源码 `7a868661e41563f1bae33ddbfdcdd061a66cc5a5` 的文件差异为空；助手与金融包相对已部署版本仅文档变化，平台此次合并不改 Core/Worker/Runner 功能代码。只切换 WebUI，不为文档变化重启其余服务。

固定 amd64 镜像 `sha256:203a484b5dfa6237a98536b2fbeb650f910fa7635bdb34a771cf785237b8e345`，启用所有者限定 `hermes_chat`。目标机 `/root/hermes-cards-20261006/` 保存私密备份、渲染配置、切换日志和 rollback 副本；源码与部署脚本不含凭证。新的锁/清单与线上实际渲染 Compose 的 deployment 校验通过。

实际验证：切换前标准聊天/Hermes/配置备份恢复 ALL PASS；真实备份在候选镜像中无网络启动/重启通过，7 条聊天 JSON、账户和密码保持一致。切换后 WebUI healthy，版本、登录、聊天与工作台 API 均 200；未认证工作台 401，原始 Responses 绕过 403；新开关/所有者匹配。旧聊天及账户保留，另外 15 个容器 ID/StartedAt/镜像未变。部署后标准备份隔离恢复 ALL PASS。

公网浏览器保留原登录，确认新版本、h2、首页输入框和旧聊天可编辑，工作台正常渲染。切换启动期间一次导航返回 502；健康就绪后重新导航恢复正常。本轮没有重新测量冷/热五次矩阵，也没有发送真实付费模型消息；卡片/mock 流式行为沿用同源码实际候选的前述验证。没有正式研究数据变更或正式前向评估。

回滚：停止 WebUI，对当前数据库执行 `configure_hermes_chat.py --enabled false`，恢复 `/root/hermes-cards-20261006/rollback/compose.json`，以现有 secrets 文件执行 `docker compose -p youwei-chat --project-directory /opt/youwei/chat -f /opt/youwei/chat/compose.json --env-file /opt/youwei/chat/secrets.env up -d --no-deps openwebui`。旧镜像为 `sha256:8aa9d39f1a5fc1517c9e93e03d3069e44ca041765c074e337810cfb5b5cd168d`；保留当前聊天卷，不覆盖研究库。本次没有触发回滚，不能称实际回滚演练。
