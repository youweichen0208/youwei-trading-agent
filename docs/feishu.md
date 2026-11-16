# 飞书研究入口

运行 `pnpm feishu`。配置保存在服务器私有文件，不能提交凭证。

必需环境变量：`FEISHU_APP_ID`、`FEISHU_APP_SECRET`、`APP_ORIGIN`。
正常模式还需要 `DATABASE_URL`、`FEISHU_TENANT_KEY`、`FEISHU_OWNER_OPEN_ID`、`FEISHU_OWNER_ID`，以及逗号分隔的 `FEISHU_GROUP_CHAT_IDS`。
绑定工作台账户为 youweichen0208@gmail.com；通过数据库查找其用户 UUID。

创建企业自建应用、启用机器人，开通 `im:message.p2p_msg:readonly`、`im:message.group_at_msg:readonly`、`im:message:send_as_bot`。
先以 `FEISHU_DISCOVER=1` 启动，配置长连接并订阅 `im.message.receive_v1`，发布版本。
私聊机器人并在指定群 @ 机器人，登记模式只打印身份标识，不执行研究。
将本人和群标识写入私有配置，停止登记进程，去掉 FEISHU_DISCOVER 后再启动。

部署模板为 infra/deployment/feishu.service。先应用迁移 018，建立独立 alpha-feishu 系统账户与数据库角色。
该数据库角色需要 schema USAGE、lock_task_owner(uuid) EXECUTE、tasks_ordinal_seq USAGE；
threads/workspaces/tasks/messages/task_events 的 SELECT、INSERT；threads.title、workspaces.version/revision/snapshot、tasks.status/cancel_requested_at/cancel_reason/sequence 的 UPDATE；
tool_operations 的 SELECT(task_id,status)、UPDATE(status)；三个 feishu 表的 SELECT、INSERT、UPDATE。
不给 users、登录会话、模型凭证或执行能力令牌读取权限。

将代码安装在独立 release，feishu-current 指向该 release，配置文件由 root 持有且权限 0600。
未完成凭证与身份配置前不要启用服务。回滚仅停止 alpha-lab-feishu，保留消息记录。

命令：/new 新会话；/status 当前任务；/stop 停止；/help 帮助。
私聊和每个群分别维护会话，但复用同一账户的 Hermes Profile，不能视为独立账户之间的记忆隔离。
回复失败每 30 秒重试，首次发送 30 分钟后仍不确定则标记 uncertain，人工核对原消息后处理；不自动重跑研究。

## 原生图表与数据卡片（2026-11-12）

迁移 019 为发件队列增加 card JSON。研究完成后先发送文字，再按该任务的 task_artifacts 历史版本生成原生卡片；不读取可能已被后续任务替换的当前画布。飞书运行角色增加 task_artifacts、datasets、results 的只读权限，所有数据查询校验 owner_id。

行情卡包含收盘价折线图、成交量柱状图和近期 OHLCV 表；指标卡包含 MA20、RSI14 图与数据表。每 100 个数据日分一张卡，表格列出本段最后 10 条。预测与模拟持仓保留探索性/模拟和缺失数据标识。图表仅为展示转换数值，表格保留保存的十进制字符串；缺失值不转为零。没有画布数据的对话仍只发送文字。

卡片通过原有持久化发件队列投递，网络重试使用原 UUID；明确的 230099 卡片拒绝会降级为工作台链接，其他失败沿用投递重试与待核对流程。不会因卡片发送失败重新运行研究。

正式飞书版本：`/opt/hermes-alpha-lab/releases/20261112Tfeishucards1`，`feishu-current` 指向此目录。此前版本记录在服务器私有 `feishu-before-cards.json`。回滚切回旧符号链接并仅重启 alpha-lab-feishu；迁移 019 为兼容性新增列，保留即可。

验证：类型检查通过；隔离 PostgreSQL 中 26 项测试通过（包含历史任务绑定、跨用户拒绝、事务回滚和原有 HTTP/任务回归）；真实历史数据原生卡片获飞书 API 成功响应并发送到本人私聊。尚未人工确认各版本手机客户端实际渲染效果。

图表可读性调整：关闭常驻数值标签与折线圆点；短日期水平显示并自动避让；成交量图以万股显示，数据表保持股；保留原始数据与缺失断点。使用 VChart 1.12.3 在 375/1000px 渲染检查，修正坐标轴格式模板产生的错误刻度；飞书原预览消息通过 PATCH 更新成功。

## 多人接入实施状态（2026-11-12）

当前仍是单人入口，不能宣称对外多人可用。020 迁移新增按 app_id + open_id 唯一的身份映射，以及可选邮箱、密码和显示名称；FeishuIdentityStore 提供事务化准入（默认 10 人）和显式原账号绑定。准入关闭时已有映射仍可解析。映射服务尚未接入机器人或 Web 路由，不接受浏览器自行提供身份。绑定必须先验证原账号登录及飞书身份，不按姓名或邮箱合并。

待完成：按用户与群隔离的执行上下文及工具引用检查、渠道最小数据库权限、自动准备与动态注册表加载、根任务容量预约、群研究只读网页展示、开关与真实部署验收。在上述隔离落实前保持现有机器人发送者限制。部署迁移前备份数据库；没有执行生产迁移或飞书后台发布。

新增并发准入、名额、关闭准入及旧账号绑定集成测试，需要 TEST_DATABASE_URL 指向名称以 _test 结尾的独立 PostgreSQL 数据库。

### 实施进展补充

已实现身份映射、OAuth state/PKCE 服务端流程及网页入口。2026-11-16 起移除五分钟私聊确认登录和私聊绑定：登录页只保留「企业内部飞书授权登录」，机器人对 `/login` 仅回复停用提示。相关开关默认关闭：`FEISHU_OAUTH_ENABLED`、`FEISHU_MULTIUSER_ENABLED`、`FEISHU_ADMISSION_ENABLED`，值为 `1` 才开启；`FEISHU_TRIAL_LIMIT` 默认 10；`FEISHU_LOGIN_ENABLED` 已不再使用。多人模式当前只处理私聊，群运行环境尚未贯通。OAuth 登录要求应用开通 `contact:user.base:readonly` 并配置回调 `https://<origin>/api/auth/feishu/oauth-callback`。开启前仍须完成权限与真实外部用户验收。

Worker 每五秒重载有效注册表，可用 `HERMES_REGISTRY_CREDENTIALS` 指定 0600 JSON 凭据映射文件；该文件不允许覆盖 TOOL_CAPABILITY_KEY。Supervisor 可用 `HERMES_DEPLOYMENT_DIRECTORIES` 指定私有 JSON 目录数组，加载新增部署时保留原部署身份。生产 Worker 默认启用 `RESEARCH_SINGLE_ROOT=1`。旧运行身份仅在当前进程中保留，重启后缺失旧凭据的任务仍需恢复处理，不能宣称凭据轮换后的重启恢复已完成。

自动准备的 prepared 状态仅表示文件准备完成。机器人须等待三个受管 Profile 登记后才提交研究。真实契约验收、独立测试工具端口、统一验收容量预约及原子发布链仍待完成；现有 check-profiles.py 占用 3410，不可在生产 Tools 运行时直接执行。当前代码尚未生产部署。
