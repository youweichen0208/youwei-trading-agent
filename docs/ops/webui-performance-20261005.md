# WebUI 打开与刷新性能优化（2026-10-05）

已部署上海入口与 WebUI 两项改动。冷加载目标达到；热加载 ≤5 秒目标未达到。未复现完整两分钟，不能宣称该现象已解决。此次没有真实付费模型调用、数据库迁移或正式前向评估。

## 改动与发布身份

- 上海 `aliyun-akshare` 保持 Nginx 1.18，使用 `listen 443 ssl http2`。仅 `/_app/immutable/` 的 200/206/304 响应增加 `public, max-age=31536000, immutable`；隐藏该 location 的上游 Cache-Control/Expires，避免冲突。保留代理缓存、Vary、压缩、SG keepalive、WebSocket/SSE 配置，`/static/` 策略不变。依据 [listen 官方说明](https://nginx.org/en/docs/http/ngx_http_core_module.html#listen) 和 [响应头模块说明](https://nginx.org/en/docs/http/ngx_http_headers_module.html)。实现：[入口配置](../../infra/chat/nginx-ecs-trading.conf)。
- WebUI `src/lib/components/agent/SidebarNav.svelte`（父组件导入名 AgentSidebarNav）仅在聊天页请求 `config`。进入 `/agent` 或子路径后加载 sessions/skills/jobs，同次访问去重，离开/销毁取消，过期结果不回写，失败不轮询；返回聊天清空详情与数量。API 封装增加可选第五参数 `AbortSignal`，服务端接口、鉴权和模型配置不变。
- WebUI [PR #3](https://github.com/youweichen0208/youwei-webui/pull/3)，源码 `b8fc503f3c725518d25cedeb4387b34434de45be`；从该提交的 git archive、原 Dockerfile 构建 `linux/amd64`，BUILD_HASH 与 OCI revision 一致。已推送、registry 检查并按 digest 拉取：`ghcr.io/youweichen0208/youwei-webui@sha256:52783da9334e73bc33c483873143fd9be4053861b6216dbf9959053305272e6f`。
- 原镜像 `ghcr.io/youweichen0208/youwei-webui@sha256:171e0e83a7b03b29d1d3552dc637b1e71526085278a3717dc92989ce4cbd630f`，源码 `a94b7bbbbb81d59749a627958f2df1c2b5c7691c`。两者同为 v0.11.4，后端与迁移源码无差异。

## 三阶段实测

同一 Chrome 154、网络、已有登录会话，未启用网络/CPU 模拟。终点由每次导航开始前安装的 observer 记录：`#chat-input` 可见且 `isContentEditable`。上线后另实际插入测试文字并撤销，确认编辑成功、原草稿恢复，未发送消息。冷加载采用 DevTools `reload(ignoreCache=true)`，热加载采用普通 reload；这是强制刷新口径，并非删除整个浏览器配置。代理缓存未人为清空。脚本：[chat_loading_timing.js](../../ops/chat_loading_timing.js)。

每阶段冷/热各 5 次；连续快速样本之间尽量保持至少 25 秒，避免既有 auth 限流。最初边构建边测的数据因 SG 内存/换页负载单独留在 `browser-before-build-concurrent.json.gz`，不进入下表；构建和隔离容器结束、确认无换页活动后重测基线。

| 阶段 | 冷中位数 / 最大 | 热中位数 / 最大 | 实际协议 |
| --- | --- | --- | --- |
| 修改前 | 31.03 / 44.14 秒 | 10.30 / 12.15 秒 | HTTP/1.1 |
| 仅入口更新 | 10.72 / 13.78 秒 | 6.72 / 23.59 秒 | h2 |
| 入口 + 前端 | 10.57 / 23.77 秒 | 6.74 / 7.92 秒 | h2 |

新版 23.77 秒是首次加载新哈希资源，保留在最终 5 次冷样本内；其余为 10.57、8.12、9.96、10.96 秒。入口阶段热加载 23.59 秒异常样本同样保留，其多个 API 在发出请求前等待约 14 秒，不能仅凭此确定网络根因。无样本因慢而删除；所有观测到的 API 均成功。

| 指标（每组中位数） | 修改前冷 / 热 | 仅入口冷 / 热 | 最终冷 / 热 |
| --- | --- | --- | --- |
| 资源传输字节（不含 HTML） | 2,786,800 / 16,209 | 2,787,904 / 19,222 | 2,020,380 / 15,990 |
| 单次页面最大预请求等待 | 21.993 / 1.743 秒 | 1.059 / 0.509 秒 | 1.013 / 0.0036 秒 |

预请求等待使用 Resource Timing 的 `requestStart - fetchStart - DNS - connect` 估计，不能冒充精确 CDP Queueing；资源统计仅包含输入框就绪时已完成的资源，资源复用、压缩变体和时机影响字节数，不能将字节差都归因于代码缩减。原始每次资源/API 耗时、协议和体积见 [测量汇总](../../infra/releases/20261005-webui-performance/performance-summary.json) 与同目录三份 `browser-{before,edge,frontend}.json.gz`。

主要收益来自入口。侧栏三类请求确实消除，但仅入口与最终中位数近似，不能宣称已证明侧栏的独立加载时间收益。最终冷加载较基线降低约 66%，热加载降低约 35%。

## 未达到热加载目标的定位

最终热样本的资源排队已很小，但五个启动调用仍串行。中位数样本在导航后的时间如下：

| 请求 | 开始 → 完成 |
| --- | --- |
| `/api/config` | 1.537 → 2.399 秒 |
| `/api/v1/auths/` | 2.409 → 3.699 秒 |
| `/api/config` 再次刷新 | 3.700 → 4.569 秒 |
| `/api/v1/users/user/settings` | 4.616 → 5.628 秒 |
| `/api/models` | 5.630 → 6.683 秒 |
| 输入框可编辑 | 6.740 秒 |

源码对应 WebUI `src/routes/+layout.svelte` 的配置、session 校验、配置刷新，以及 `src/routes/(app)/+layout.svelte` 的 `setUserSettings` 回调后 `setModels`。模型列表使用用户设置中的 directConnections，配置刷新处于身份初始化流程中，不能未经语义验证就删请求或提前复用匿名配置。后续需对这条初始化链单独设计并行/去重及登录失效、配置变化、连接设置的回归验证；本次保留该行为，未扩大修改登录权限或模型配置。

## 验证分层

| 层次 | 实际验证与结果 |
| --- | --- |
| 本地前端 | 先复现 7 项组件失败；修复后 `npx vitest run --config vitest.sidebar.config.ts` 14 passed；原有 `npx vitest run src/lib/apis/hermes/index.test.ts` 4 passed |
| 本地 Python / 构建 | 工作台 Python 3.12 `PYTHONPATH=backend .venv-workbench/bin/python -m pytest -q backend/tests/hermes` 20 passed；Node 22 `npm ci --ignore-scripts --legacy-peer-deps --no-audit`、`NODE_OPTIONS=--max-old-space-size=8192 npx vite build` 通过 |
| 类型检查 | `npm run check` 仍为原有 7001 errors / 198 warnings / 344 files，本次文件无诊断错误；全仓类型检查未通过 |
| 平台 | `uv run --frozen pytest -q tests/contracts/test_webui_configuration.py tests/contracts/test_assistant_image_backup.py tests/contracts/test_upstreams.py` 21 passed；主 catalog、新聊天 catalog 与候选/live Compose deployment 校验通过 |
| CI / 完整镜像 | WebUI PR 的 bridge、frontend、image 均成功；SG 固定源码完整 Docker build（含 Pyodide）及 digest push/pull 通过 |
| 入口隔离 | `python3 ops/verify_chat_edge.py --config <candidate>` 在目标机 Nginx 1.18 上用临时端口/cache 测 200/206/304、302/404/500、代理 HIT、gzip/identity 通过；完整 staged/live `nginx -t` 通过 |
| 备份 / 候选 | 标准备份恢复 ALL PASS；最新切换前及切换后备份再次恢复 ALL PASS；候选无网络启动/重启保留 6 条聊天 JSON、账户、密码，见 candidate-restore.log |
| 跨服务 | 固定新 WebUI + 当前助手 + mock 模型：发现、聊天、完整历史、SSE、知识、重启、备份、后台分流及关闭注册全部通过；无生产密钥和数据卷 |
| 浏览器隔离 | 合成账号登录、旧 fixture 聊天打开、聊天页仅 config、进入工作台才加载、子路由不重复、返回清空、直接打开、mock 流式显示与停止通过；[细节](../../infra/releases/20261005-webui-performance/browser-mock.json) |
| 生产 | 只替换 WebUI；healthy，版本/登录/聊天/工作台接口 200，未认证工作台 401；其余 15 个容器 ID/StartedAt 不变。真实浏览器旧聊天打开及可编辑、工作台/技能/返回聊天正常，新 HTML 加载新哈希入口且保留登录 |

上海公网确认 h2、成功哈希资源一年缓存、代理 HIT；gzip 与 identity 分别 MISS 后 HIT，解压内容 hash 相同。HTML、版本、API 和 404 不获得长期缓存，`/static/` 保持原策略。已有 WebSocket 在 reload 前后保持 OPEN 并继续收到帧，测试后关闭。共享 `dash` 仍为预期未认证 401；`market` 切换前已是 502，切换后仍为 502，未纳入此次修复。Nginx 1.18 共享 IPv4 TLS listener 上其他站点也协商 h2；未修改其配置文件。公网真实付费 SSE 未调用，SSE 在隔离 mock 中验证，生产 buffering/timeouts 未改。

## 恢复与限制

上海备份 `/root/webui-perf-20261005/trading.before.conf`。需要恢复时先核对站点无后续变更，恢复 `/etc/nginx/sites-available/trading`，执行 `nginx -t`，成功后 `systemctl reload nginx`。本次未触发回滚，没有为演练故意中断入口。

SG 工作目录 `/root/webui-perf-20261005/`；`rollback/compose.json`、`rollback/secrets.env` 为切换前配置，备份在 `backups/` 的受控目录。仅 WebUI image 字段改变，秘密配置不变。自动切换脚本 [cutover.py](../../infra/releases/20261005-webui-performance/cutover.py) 在健康/后验失败时恢复旧 Compose，仅 `up -d --no-deps openwebui`。同版本无迁移，保留现有卷及新聊天；不覆盖正式研究数据。此次没有触发生产回滚，不能将其称为实际回滚演练通过。

实际部署清单为 [chat.lock.json](../../infra/releases/20261005-webui-performance/chat.lock.json) / [chat.manifest.json](../../infra/releases/20261005-webui-performance/chat.manifest.json)，渲染配置仅在 SG `production-rendered-private.json`（0600），与验收候选逐字节一致。没有重跑 Core 全量数据库测试，没有真实付费模型质量或正式前向评估。热加载目标与偶发长等待仍需后续处理。
