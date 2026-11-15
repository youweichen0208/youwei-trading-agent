# Hermes 模型代际轮换设计（2026-11-15）

## 意图

把生产 Hermes 的所有模型从 `glm-5.3-flash` 切到 `deepseek-v4.1-flash`，并让生产配置只保留这一个模型。用户已选择「正式轮换能力」而非一次性改文件：轮换必须可审计、可重复、可回滚，不能绕过 `profile_preparation_policy` 的不可变语义。

已实测：同一 volce 网关、现有 key，`model=deepseek-v4.1-flash` 返回 200（实际模型 `deepseek-v4-1-flash-261017`），thinking 与 `output_config.effort=low` 均可用；新闻翻译风格的 JSON 输出可被现有解析器接受。

## 现状与约束

- 模型在准备时固化进每个 Profile 的 `runtime.json`，其摘要写入 `preparation.json.fingerprints`；supervisor 启动前校验摘要，容器 bootstrap 每次启动从 `runtime.json` 重写 `home/config.yaml`。
- `profile_preparation_policy` 是单行、不可变表；`profile_preparation_jobs` 一旦绑定 `settings_hash/first_port` 不可改，`prepared` 后整行不可变；`profile_api_ports` 不可变。
- 因此只改 `profile-settings.json` 会使 `AutoProfilePreparer.open` 抛 `preparation_policy_changed`，新用户准备会失败；直接改 `runtime.json` 会被生命周期摘要校验拒绝。
- 现有 9 个 Profile（3 个 owner 的 research/prediction/portfolio）、2 个用户、0 个活动任务；`profiles/` 中还有 `multiuser-smoke` 测试目录。
- 生产服务：`alpha-lab-preparer`、`alpha-lab-supervisor`、`alpha-lab-worker`、`alpha-lab-web`；Profile 容器按需启动。

## 目标 / 非目标

**目标**
- 新增可追加的 preparation 策略代际；轮换后自动准备对新用户使用新模型。
- 既有 9 个 Profile 原地切换模型，保留 home 记忆、profile ID、API 端口与密钥。
- 所有写入可回滚：旧 artifacts、种子、settings 文件、新闻翻译配置均有备份。
- 生产不再有生效的 glm 配置：profile 三角色、bootstrap 模型、新闻翻译全部为 deepseek。
- 验证覆盖真实 deepseek 的 9 个 Profile 契约检查与新闻翻译调用。

**非目标**
- 不改变模型网关、镜像、Database 最小权限模型（除新增表的授权）。
- 不改变 Profile 生命周期/端口分配/密钥轮换语义。
- 不删除远端网关上的其他模型；只清理本部署的配置。
- 不保留 glm 作为 fallback（`fallback_model` 继续为 null）。

**反目标**
- 通过禁用不可变触发器改 `profile_preparation_policy` 或既有 job 行。
- 用新目录重建 home 导致记忆/会话上下文丢失。
- 让部分 owner 已切换、部分未切换而策略文件已生效。

## 方案

### 已选：版本化策略 + 原地 artifacts 轮换

新增迁移 `035_profile_policy_versions.sql`：

```sql
CREATE TABLE profile_preparation_policy_versions (
  generation integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  root_identity text NOT NULL,
  settings_hash text NOT NULL CHECK(settings_hash ~ '^[a-f0-9]{64}$'),
  first_port integer NOT NULL CHECK(first_port BETWEEN 1024 AND 65533),
  last_port integer NOT NULL CHECK(last_port BETWEEN first_port+2 AND 65535),
  created_at timestamptz NOT NULL DEFAULT now()
);
INSERT INTO profile_preparation_policy_versions(root_identity,settings_hash,first_port,last_port)
  SELECT root_identity,settings_hash,first_port,last_port FROM profile_preparation_policy;
```

- 只允许 `INSERT`；`UPDATE/DELETE/TRUNCATE` 触发器拒绝（复用 `reject_execution_evidence_mutation`）。
- 旧 `profile_preparation_policy` 原样保留，作为历史证据，不再作为读取源；迁移 035 在已有部署种子化一行，fresh DB 由 `AutoProfilePreparer.open` 首次插入。
- Worker 获得 `SELECT, INSERT`；Web/Tools 无权限，沿用现有隔离。新表加入 `database-access.ts`/`upgrade-access.ts` 的对象与授权清单。

`AutoProfilePreparer.open`：
- 读取 `ORDER BY generation DESC LIMIT 1` 的最新代际；计算 `digest(json({settings,lastPort,rootIdentity}))` 并比较，失配仍抛 `preparation_policy_changed`。
- 表为空时插入第一代（同时写旧表第一代，保持回退兼容），并发冲突后重读。
- 其余 prepare/reserve 逻辑不变；新用户使用最新代际。

轮换命令 `scripts/rotate-model.ts`（新增 `pnpm hermes:rotate-model`）：

```
DATABASE_URL=OWNER_URL pnpm hermes:rotate-model \
  NEW_SETTINGS PRIVATE_ROOT LAST_PORT BACKUP_ROOT [--owner UUID ...] [--apply]
```

流程（默认 dry-run，打印计划；`--apply` 才写）：
1. 校验新 settings：`image/gatewayOrigin/firstPort` 与当前一致，仅模型字段变化；解析 root identity 与新旧 settings hash。
2. 查询目标 owner 的 `profile_preparation_jobs`，要求 `prepared` 且无 `queued/running/stopping/unknown` 任务；无 owner 参数时选择所有已准备 owner。
3. 对每个 owner 取 `withPreparationLock`，读取并校验 `preparation.json` 指纹与当前 `runtime.json` 一致、`profiles`/端口一致。
4. 备份到 `BACKUP_ROOT/<timestamp>/<owner>/`：`runtime.json`、`credentials.json`、`preparation.json`、`.auto-seeds/<requestId>.json`，权限 0600/0700。
5. 用新 settings 重新渲染每个角色的 `runtime.json`（`{version,ownerId,profileId,role,model,gatewayOrigin}`，model 不含 apiKey），必要时更新 `credentials.json.modelApiKey`；原子写（同目录临时文件、fsync、`rename`、0444/0600）。
6. 更新 `preparation.json`：保留 `ownerId/profiles/image/requestId/status`，写入新 `fingerprints` 与 `settingsHash`；同步更新 seed 的 `settingsHash`（keys/requestId 不变）。`compose.json`、`worker.env`、端口不动。
7. 全部 owner 成功后：插入新 policy 版本行 → 原子写 `private/profile-settings.json` → 重启 `alpha-lab-preparer`（先部署新 release 的 preparer 代码）。任何 owner 失败：按备份恢复已写 owner，策略文件与 policy 均不动，退出非零。
8. 输出每个 owner 的 `fromHash → toHash`、备份路径；不输出密钥。

模型配置清理（同一轮换）：
- `private/provider/claude-code-model.json` 与 `private/provider/news-translation.json` 改为 deepseek（baseUrl/apiMode/authMode/apiKey 不变，仅 `name`）。
- `profile-settings.json` 三角色全部 deepseek；`fallback_model` 由 bootstrap 固定为 null。
- 旧文件备份到 `BACKUP_ROOT/<timestamp>/provider/`；历史 glm 配置仅存在于备份，确认后可由用户决定删除。

### 未选方案

- **一次性禁用触发器改 policy + 改 artifacts**：快，但破坏不可变审计语义，回滚只能靠备份；用户已选择版本化，不采用。
- **新目录/新 home 重建**：能整目录原子切换，但需要复制 home 记忆，且旧目录长期并存易漂移；原地改 `runtime.json` 已足以切换模型，记忆零迁移。
- **新 schema/全新部署**：干净但需迁移用户/线程/研究事实，风险和工期都不成比例。

## 轮换与回滚

- 轮换前：`systemctl stop alpha-lab-worker alpha-lab-supervisor`（web 可继续排队；0 活动任务），避免新任务在 artifacts 写入窗口启动容器。
- 轮换写入：更新三个 Profile 的 `runtime.json`/指纹后，同时清空 `profile_instances.deployment_hash`，让下一次 supervisor tick 重新封印新代际；否则 `ProfileSupervisor` 会以 `profile_deployment_mismatch` 永久拒绝启动。
- 轮换后验证：对每个 owner 运行现有 `scripts/verify-prepared-profile.ts <ownerDir>`（真实 deepseek + 合成工具网关），成功后 `profiles.verified.json` 由检查器重写；再启动 supervisor/worker。
- 回滚：同一命令指向旧 settings 备份即可生成新一代 policy 并恢复 artifacts；若 artifacts 已写但未插 policy，直接从备份恢复文件。旧代际 policy 行与备份不删除，作为审计证据。

## 验收

- 迁移：旧 singleton 行被种子化为第 1 代；新表 UPDATE/DELETE/TRUNCATE 被拒绝；Worker 可 INSERT/SELECT，Web/Tools 拒绝。
- `AutoProfilePreparer.open`：最新代际匹配时成功；追加新代际后新 settings 成功；旧 settings 失配仍抛 `preparation_policy_changed`。
- 轮换模块：fixture owner 从 A 模型切到 B，`runtime.json` 模型/指纹、`preparation.json.settingsHash`、seed hash 全部一致；重复执行 no-op；有活动任务时拒绝；备份可完整恢复。
- 生产：9 个 Profile 的 `verify-prepared-profile` 全绿；`deepseek-v4.1-flash` 请求出现在证据；`profile-settings.json`、bootstrap、新闻翻译配置无 glm；`pnpm typecheck`、相关测试、`pnpm build` 通过。
- 新闻翻译：生产调用 `translatedNews` 返回中文且 id 完整（或等价真实网关 smoke）。

## 风险与未决

- 轮换命令需要停止 worker/supervisor，期间新研究排队或失败（预计数分钟；验证阶段更长）。用户需接受维护窗口。
- Profile 验证会产生真实模型费用与一次完整契约检查（9 个 Profile）。
- `preparation.json` 是准备证据；原地更新会改变证据内容。备份 + 新 policy 代际 + 验收输出构成新的审计链，旧证据保留在备份中。
- 需要在设计批准后确认维护窗口，并检查是否有用户正在使用。
