# 数据库运行账户

Web、Worker 和私有工具网关分别使用独立 PostgreSQL 登录账户。迁移与授权使用拥有本项目表和函数的部署账户；运行进程不使用该账户。这个边界限制服务职责与破坏性操作，用户之间的行级归属仍由 BFF/工具网关验证，不宣称已经实现数据库 RLS。

## 权限范围

| 账户 | 主要能力 | 明确不授予 |
|---|---|---|
| Web | 登录注册、研究提交/停止、持仓版本、自选股、布局、历史/审计读取 | 执行凭证读取、事实/回执写入、审计写入、迁移与 DDL |
| Worker | 租约、执行身份与能力令牌、终态归档、工具回执核对、调用固定清理函数 | 用户密码/登录会话读取、持仓写入、任意删除、事实写入、审计写入、DDL |
| Tools | 能力令牌摘要认证、工具操作/退出回执、不可变数据/结果、专项派发、Canvas | 登录信息、能力令牌密文、持仓修改、任务状态/租约修改、删除、审计写入、DDL |

三个账户均为非超级用户、无建库/建角色/复制/BYPASSRLS 权限、无角色成员关系，连接上限为 16。部分表只授予指定列的更新/读取权限。账户的默认 search_path 固定为 pg_catalog、应用 schema、pg_temp。

`lock_task_owner(uuid)` 只锁定现有用户行并返回是否存在；避免为了 SELECT FOR UPDATE 给各服务授予 users 更新权限。审计触发器以表所有者身份追加记录，运行账户不能直接 INSERT/UPDATE/DELETE 审计。`maintain_transient_records()` 只执行固定的 7 天事件保留、过期登录/限流清理与终态令牌清理策略，不接受表名、期限或批量大小参数；只有 Worker 可调用。上述 SECURITY DEFINER 函数固定搜索路径，不能被调用方的临时表或 search_path 重定向。

## 一次性准备

先用部署账户完成迁移 017，再运行授权命令。目标 schema 必须只包含本项目已知表、序列和函数，且表/函数由当前部署账户拥有；混用 schema 会拒绝处理。命令仅修改目标 schema 的 PUBLIC 权限并创建指定前缀的新账户，不改动其他 schema/已有账户。

```sh
# DATABASE_URL 在此处是部署账户连接，不能是运行账户。
pnpm db:migrate
pnpm db:access alpha_runtime /绝对路径/私有目录/database-access
```

命令要求新的输出目录和未使用的角色名前缀，拒绝覆盖、重新授权已有角色或隐式轮换密码。目录权限 0700，web.env、worker.env、tools.env 及 state.json 为 0600；每个服务有独立随机密码。文件在数据库授权前保存，避免提交成功但确认响应丢失时丢失凭证；state.json 只有明确成功后才标记 provisioned。若命令失败，保留文件并检查数据库实际状态，不要假定失败意味着事务未提交，也不要用新密码盲目重试已有角色。

该命令撤销目标 schema、表、序列、函数对 PUBLIC 的权限。部署账户仍拥有其对象。后续新增迁移需要维护显式权限清单；不会自动把新增表或函数权限授予运行账户。首次安装器拒绝复用已有角色，因此后续权限升级应由部署账户以单独、可审核的迁移执行。

## 启动服务

在各自终端加载对应文件，不要把部署账户 URL 或其他服务的文件一并注入进程：

```sh
# Web
set -a
. /绝对路径/私有目录/database-access/web.env
set +a
APP_ORIGIN=http://localhost:3400 pnpm dev
```

Worker 终端加载 worker.env 后运行 `pnpm worker`，工具网关终端加载 tools.env 后运行 `pnpm tools`。Hermes 注册表、TOOL_CAPABILITY_KEY、行情凭证仍按各服务职责单独配置；本步骤不提供这些外部配置，也不创建已验证的 Hermes 注册表。生产 Web 仍要求 HTTPS APP_ORIGIN。

仓库本地 `.local/database-access` 已在独立开发数据库 alpha_test 中创建 alpha_runtime_web、alpha_runtime_worker、alpha_runtime_tools；该目录被忽略，不应提交。Compose 的 alpha 超级用户仍仅用于本地迁移、角色准备及隔离测试。生产环境应使用独立数据库/集群、受控部署账户与私有网络；这次本地验证不代表已完成生产部署。

## 验证范围

集成测试使用三个真正的 PostgreSQL 登录连接，完成注册、持仓、研究提交、执行身份、工具数据/计算、三角色分派、五类 Canvas、布局、归档、审计读取和保留清理。行情为测试夹具，Quant 是真实 Python 进程，Hermes 调度受控，不冒充模型/供应商联调。

拒绝用例包括事实/审计/回执篡改、删除任务、TRUNCATE、读取其他服务凭证、工具服务修改任务状态、创建表和禁用触发器。另验证受控函数在临时同名表与伪造搜索路径下仍访问真实应用对象。每次测试创建随机 schema/角色，结束后只删除自身资源。


## 从迁移 011 升级

迁移 012 增加执行事件游标/缺口/关闭标记和任务文本快照。新安装器已包含这些列的权限。已有运行账户不重新创建、不轮换密码；由部署账户先执行 `pnpm db:migrate`，再只对原 Worker 账户增加以下列权限（这里使用本地 schema 和角色名，其他部署应替换为其实际名称）：

```sql
GRANT UPDATE(event_cursor,event_gap,event_closed) ON public.executions TO alpha_runtime_worker;
GRANT UPDATE(stream_text) ON public.tasks TO alpha_runtime_worker;
```

Web 原有 tasks 的 SELECT 权限可读取文本快照；Tools 不获得这些字段的更新权限。上述两项授权已应用到本地独立 alpha_test，不修改其他数据库或服务账户。


## 从迁移 012 升级

迁移 013 为 profile_instances 增加监管标记、部署指纹、容器 ID、最后使用时间和错误代码。监管进程复用受限 Worker 数据库账户；Web 和 Tools 不获得生命周期列的更新权限。先迁移，再由部署账户向既有 Worker 角色授予下面的列权限（替换为实际 schema/角色名）：

```sql
GRANT UPDATE(state,lifecycle_managed,deployment_hash,container_id,last_used_at,lifecycle_error)
ON public.profile_instances TO alpha_runtime_worker;
```

本地 alpha_test 已完成这项升级。新安装器已包含权限。监管进程是独立的宿主机服务，Docker 权限不交给 Web、Tools 或 Hermes；它不使用迁移账户。已用真实受限 Worker 登录验证监管状态更新与 owner 锁函数调用。


## 从迁移 013 升级

迁移 014 增加不可变 `profile_lifecycle_audit` 与固定搜索路径的审计触发器。Profile 注册、状态、错误与容器/部署绑定变化自动写入审计；最后使用时间刷新不写重复记录。先执行迁移，再给监管进程使用的 Worker 账户增加只读权限：

```sql
GRANT SELECT ON public.profile_lifecycle_audit TO alpha_runtime_worker;
```

运行账户不获得该表的 INSERT/UPDATE/DELETE/TRUNCATE 或序列权限，触发器以部署账户权限追加，记录实际数据库登录 `session_user`。Web 与 Tools 不读取该运维审计。新安装器已包含此表、函数、序列的识别及显式只读授权；恢复命令仍使用受限 Worker 账户。该登录表示服务身份，不表示具体操作人员身份。

本地独立 alpha_test 已应用迁移 014 和上述只读授权；未修改其他数据库或服务账户。


## 从迁移 014 升级

迁移 015 保存监管进程观察到的 Docker `StartedAt`（保留纳秒精度的原字符串），并在首次提交事务内绑定容器 ID、启动时间和部署指纹。后续重试不覆盖原观察；旧任务不回填，静态非托管运行保持空值。先升级监管进程并完成迁移，再运行新 Worker；托管 Profile 缺少启动观察时不会首次提交。

```sql
GRANT UPDATE(container_started_at) ON public.profile_instances TO alpha_runtime_worker;
GRANT UPDATE(runtime_container_id,runtime_container_started_at,runtime_deployment_hash)
ON public.executions TO alpha_runtime_worker;
```

生命周期审计增加启动时间变化，新安装器包含上述列权限。本地独立 alpha_test 已应用迁移及显式授权。观察到启动时间改变不单独构成某个 run 的退出证明；容器重启也不授权补造任务/工具回执。


## 从迁移 015 升级

迁移 016 增加 `runtime_recoveries`，记录原执行身份、核对阶段、容器停止/启动观察和匹配的原生终态摘要。阶段只能向前，原身份/已保存观察不能改写，记录不可删除。阶段变化通过固定搜索路径的特权触发器追加到不可变 `execution_audit`；新增 recovery_id 与 database_actor 不回填历史身份。

```sql
GRANT SELECT, INSERT ON public.runtime_recoveries TO alpha_runtime_worker;
GRANT UPDATE(phase,stopped_container_started_at,restarted_container_started_at,native_receipt)
ON public.runtime_recoveries TO alpha_runtime_worker;
```

本地独立 alpha_test 已完成迁移和以上授权，新安装器同步维护对象清单。Worker/监管命令不获得任意改写恢复身份、删除记录或直接写审计的权限。Web 继续通过任务 owner 校验读取带 recovery_id 的审计，不读取运维恢复表；Tools 无新增权限。受限 Worker 登录已完成一次完整核对与 Worker 正常归档验证。


## 从迁移 016 升级

迁移 017 增加注册后的配置准备任务、单宿主机准备策略与持久化 API 端口预留。新注册与准备任务同事务提交；不为老用户补建任务，以免覆盖已有手动部署。数据库只保存绑定摘要和状态，模型凭证与运行 API 密钥保存在宿主机私有文件。先执行迁移，再由部署账户添加下面的授权（替换为实际 schema 和角色）：

```sql
GRANT INSERT(owner_id) ON public.profile_preparation_jobs TO alpha_runtime_web;
GRANT SELECT, INSERT ON public.profile_preparation_jobs, public.profile_preparation_policy,
  public.profile_api_ports TO alpha_runtime_worker;
GRANT UPDATE(status,first_port,settings_hash,error_code)
  ON public.profile_preparation_jobs TO alpha_runtime_worker;
```

Web 只能按默认状态入队，不能指定端口/策略/状态或读取准备表；Tools 无新增权限。Worker 不获得这些记录的删除/截断权限，不能改写 request_id；已绑定端口与配置摘要由触发器保护，策略和端口预留不可修改或删除。自动准备命令使用 Worker 账户与私有策略文件，不需要 Docker 控制权。准备成功不改变 Profile 的就绪状态，也不生成验收证据。新安装器同步维护表/函数清单与权限。

## 从迁移 035 升级（模型代际）

迁移 035 增加只追加的 `profile_preparation_policy_versions`，旧 `profile_preparation_policy` 原样保留并回填为第 1 代。轮换命令与自动准备只读取最新代际；新表禁止 UPDATE/DELETE/TRUNCATE。

```sql
GRANT SELECT, INSERT ON public.profile_preparation_policy_versions TO alpha_runtime_worker;
```

Web/Tools 无权限。生产已执行 `scripts/upgrade-multiuser-access.ts` 应用该授权。

上述 017 迁移及最小增量授权已应用本地独立 alpha_test。测试中的真实受限 Worker 已完成自动准备，Web/Tools 越权读写与 Worker 身份修改/删除/截断拒绝均通过。
