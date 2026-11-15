# Hermes 模型代际轮换 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把生产 Hermes 的 9 个 Profile、bootstrap 模型和新闻翻译全部切到 `deepseek-v4.1-flash`，提供可审计、可重复、可回滚的模型代际轮换能力。

**Architecture:** 新增只追加的 `profile_preparation_policy_versions` 表；`AutoProfilePreparer` 读取最新代际。轮换命令对每个 owner 原地重写 `runtime.json`、`preparation.json` 指纹和 seed 摘要，保留 home 记忆/ID/端口/密钥，全部成功后写入新 policy 并替换 settings；失败按备份恢复。

**Tech Stack:** TypeScript、PostgreSQL、node:test、tsx、现有 Hermes preparation/lifecycle 模块。

**Spec:** `docs/superpowers/specs/2026-11-15-model-rotation-design.md`

## Global Constraints

- 不修改/删除既有 `profile_preparation_policy`、`profile_preparation_jobs`、`profile_api_ports` 行；不启用/禁用不可变触发器。
- `runtime.json` 模式 0444，`preparation.json`/seed 0600，备份目录 0700。
- 轮换期间不得有 `queued/running/stopping/unknown` 任务或运行中的 Profile 容器。
- 生产生效配置只保留 `deepseek-v4.1-flash`；glm 只允许存在于私有回滚备份。
- 不输出模型密钥；命令输出仅 owner/hash/backup 路径。
- 新表只允许 INSERT；`UPDATE/DELETE/TRUNCATE` 必须被拒绝。Worker 仅 `SELECT, INSERT`，Web/Tools 无权限。
- 验证使用真实 `deepseek-v4.1-flash`；禁止用替身模型宣称通过。

## Review Focus

- 轮换写入窗口内新任务启动容器，读到新旧混合的 runtime/preparation：必须由「无活动任务 + 无运行容器」前置检查拒绝。
- 进程在 artifacts 写完后、policy/设置写入前崩溃：重跑必须补齐 policy 与设置，不得重复备份或重复生成密钥。
- 备份恢复后 `preparation.json` 指纹与 `runtime.json` 不一致：恢复步骤必须重算并校验，不能只拷文件。
- 新用户准备在旧 policy 行仍存在时读到错误代际：`open` 必须选择最新 `generation`，fresh DB 必须能初始化第一代。
- 轮换后 seed 的 `settingsHash` 未更新：后续 `--retry` 恢复会报 `preparation_seed_changed`。
- 新闻翻译切模型后返回 thinking 块/非 JSON：现有解析器必须仍只取 text 块并校验中文/ID。

---

### Task 1: 策略代际表与最新代际读取

**Files:**
- Create: `infra/migrations/035_profile_policy_versions.sql`
- Modify: `packages/hermes-adapter/src/auto-preparation.ts:124-201`
- Modify: `packages/server/src/database-access.ts:60-70,240-250`
- Modify: `packages/server/src/upgrade-access.ts:26-40`
- Test: `tests/server/auto-preparation.test.ts:180-220`
- Test: `tests/server/database-access.test.ts:840-900`

**Interfaces:**
- Produces: `preparationRootIdentity(root: string): Promise<string>`
- Produces: `preparationSettingsHash(settings: ProvisionSettings, lastPort: number, rootIdentity: string): string`
- Produces: `AutoProfilePreparer.open` 继续返回 `AutoProfilePreparer`，内部读取最新代际。

- [ ] **Step 1: 写迁移**

`infra/migrations/035_profile_policy_versions.sql`：

```sql
-- Append-only policy generations; the legacy singleton remains evidence.
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
CREATE TRIGGER immutable_preparation_version BEFORE UPDATE OR DELETE ON profile_preparation_policy_versions
 FOR EACH ROW EXECUTE FUNCTION reject_execution_evidence_mutation();
CREATE TRIGGER immutable_preparation_version_truncate BEFORE TRUNCATE ON profile_preparation_policy_versions
 FOR EACH STATEMENT EXECUTE FUNCTION reject_execution_evidence_mutation();
```

- [ ] **Step 2: 失败测试：最新代际与旧表种子**

在 `tests/server/auto-preparation.test.ts` 的 `immutable policy...` 测试后追加：

```ts
test("policy versions seed the legacy row and select the latest generation", async (t) => {
  const { pool, output, preparer, user } = await setup(t);
  const u = await user();
  await preparer.prepare(u.id);
  assert.deepEqual(
    (await pool.query("SELECT settings_hash FROM profile_preparation_policy_versions ORDER BY generation")).rows.map((r) => r.settings_hash),
    [(await pool.query("SELECT settings_hash FROM profile_preparation_policy")).rows[0].settings_hash],
  );
  const next = settings();
  next.models.research!.name = "next-model";
  next.models.prediction!.name = "next-model";
  next.models.portfolio!.name = "next-model";
  const rootIdentity = await preparationRootIdentity(output);
  const nextHash = preparationSettingsHash(next, 31020, rootIdentity);
  await pool.query(
    "INSERT INTO profile_preparation_policy_versions(root_identity,settings_hash,first_port,last_port) VALUES($1,$2,$3,$4)",
    [rootIdentity, nextHash, 31000, 31020],
  );
  const reopened = await AutoProfilePreparer.open(pool, next, output, 31020);
  assert.equal(reopened.root, output);
  await assert.rejects(AutoProfilePreparer.open(pool, settings(), output, 31020), /preparation_policy_changed/);
  await assert.rejects(pool.query("UPDATE profile_preparation_policy_versions SET last_port=32000"));
  await assert.rejects(pool.query("DELETE FROM profile_preparation_policy_versions"));
  await assert.rejects(pool.query("TRUNCATE profile_preparation_policy_versions"));
});
```

- [ ] **Step 3: 验证失败**

Run: `TEST_DATABASE_URL=... pnpm exec tsx --test tests/server/auto-preparation.test.ts`
Expected: FAIL，`preparationRootIdentity is not defined` / 表不存在。

- [ ] **Step 4: 实现导出与最新代际读取**

在 `auto-preparation.ts` 增加：

```ts
export async function preparationRootIdentity(root: string) {
  const marker = z.object({ id: z.uuid() }).strict().parse(
    await readProvisionSettings(join(root, ".auto-preparer.json")),
  );
  const stat = await lstat(root);
  return digest(json({ root, dev: stat.dev, ino: stat.ino, id: marker.id }));
}
export function preparationSettingsHash(
  settings: z.infer<typeof provisionSettings>,
  lastPort: number,
  rootIdentity: string,
) {
  return digest(json({ settings, lastPort, rootIdentity }));
}
```

`open` 中删除直接插入旧表的逻辑，改为：

```ts
const rootIdentity = await preparationRootIdentity(root);
const settingsHash = preparationSettingsHash(settings, lastPort, rootIdentity);
let policy = (
  await pool.query(
    "SELECT * FROM profile_preparation_policy_versions ORDER BY generation DESC LIMIT 1",
  )
).rows[0];
if (!policy) {
  const legacy = (
    await pool.query("SELECT * FROM profile_preparation_policy WHERE singleton=true")
  ).rows[0];
  if (legacy && (
    legacy.root_identity !== rootIdentity ||
    legacy.settings_hash !== settingsHash ||
    legacy.first_port !== settings.firstPort ||
    legacy.last_port !== lastPort
  ))
    throw new Error("preparation_policy_changed");
  if (!legacy)
    await pool.query(
      "INSERT INTO profile_preparation_policy(root_identity,settings_hash,first_port,last_port) VALUES($1,$2,$3,$4) ON CONFLICT DO NOTHING",
      [rootIdentity, settingsHash, settings.firstPort, lastPort],
    );
  await pool.query(
    "INSERT INTO profile_preparation_policy_versions(root_identity,settings_hash,first_port,last_port) VALUES($1,$2,$3,$4)",
    [rootIdentity, settingsHash, settings.firstPort, lastPort],
  );
  policy = (
    await pool.query(
      "SELECT * FROM profile_preparation_policy_versions ORDER BY generation DESC LIMIT 1",
    )
  ).rows[0];
}
if (
  !policy ||
  policy.root_identity !== rootIdentity ||
  policy.settings_hash !== settingsHash ||
  policy.first_port !== settings.firstPort ||
  policy.last_port !== lastPort
)
  throw new Error("preparation_policy_changed");
```

- [ ] **Step 5: 授权**

`database-access.ts` 的 `tables` 数组加 `"profile_preparation_policy_versions"`；在 worker 的 `profile_preparation_jobs/policy/profile_api_ports` 数组加新表（`SELECT, INSERT`）。`upgrade-access.ts` 同样给 worker `SELECT, INSERT`。`tests/server/database-access.test.ts` 的拒绝表数组加入新表，并断言 worker 可 `INSERT`、不可 `UPDATE/DELETE/TRUNCATE`。

- [ ] **Step 6: 验证通过**

Run: `TEST_DATABASE_URL=... pnpm exec tsx --test tests/server/auto-preparation.test.ts tests/server/database-access.test.ts`
Expected: 全部 PASS。

- [ ] **Step 7: Commit**

```bash
d = await commit.draft()
await d.commit("feat: version preparation policy for model rotation")
```

---

### Task 2: 单 owner 原地轮换模块

**Files:**
- Create: `packages/hermes-adapter/src/rotation.ts`
- Test: `tests/server/model-rotation.test.ts`

**Interfaces:**
- Consumes: `preparationRootIdentity`, `preparationSettingsHash`, `ownerProfiles`, `contextOwner`, `renderPreparation`, `readProvisionSettings`。
- Produces:

```ts
export type RotatedOwner = {
  ownerId: string;
  fromHash: string;
  toHash: string;
  changed: boolean;
  backupDirectory: string | null;
};
export async function rotateOwnerPreparation(
  pool: Pool,
  ownerId: string,
  settings: z.infer<typeof provisionSettings>,
  root: string,
  lastPort: number,
  backupRoot: string,
): Promise<RotatedOwner>;
export async function restoreOwnerPreparation(
  root: string,
  ownerId: string,
  backupDirectory: string,
): Promise<void>;
```

- [ ] **Step 1: 写失败测试**

新建 `tests/server/model-rotation.test.ts`，复用 `auto-preparation.test.ts` 的 setup 结构（schema + migrate + temp root + `AutoProfilePreparer.open(settings(), output, 31020)` + 注册用户 + `prepare`）。核心用例：

```ts
test("rotation rewrites runtime specs, fingerprints and seed without touching memory", async (t) => {
  const originalManifest = JSON.parse(await readFile(join(output, user.id, "preparation.json"), "utf8"));
  const researchId = originalManifest.profiles.find((p: { role: string }) => p.role === "research").id;
  const original = await readFile(join(output, user.id, "profiles", researchId, "runtime.json"), "utf8");
  const backupRoot = join(root, "backups");
  const next = settings();
  for (const role of ["research", "prediction", "portfolio"] as const)
    next.models[role]!.name = "deepseek-v4.1-flash";
  const result = await rotateOwnerPreparation(
    pool, user.id, next, output, 31020, backupRoot,
  );
  assert.equal(result.changed, true);
  assert.notEqual(result.fromHash, result.toHash);
  const manifest = JSON.parse(await readFile(join(output, user.id, "preparation.json"), "utf8"));
  assert.equal(manifest.settingsHash, result.toHash);
  for (const profile of manifest.profiles) {
    const runtime = await readFile(join(output, user.id, "profiles", profile.id, "runtime.json"), "utf8");
    assert.equal(JSON.parse(runtime).model.name, "deepseek-v4.1-flash");
    assert.equal(manifest.fingerprints[profile.id], digest(runtime));
  }
  assert.equal(await readFile(join(result.backupDirectory!, "runtime-<profileId>.json"), "utf8"), original);
  const seed = JSON.parse(await readFile(join(output, ".auto-seeds", `${manifest.requestId}.json`), "utf8"));
  assert.equal(seed.settingsHash, result.toHash);
  assert.deepEqual((await rotateOwnerPreparation(pool, user.id, next, output, 31020, backupRoot)).changed, false);
});
```

加一个活动任务拒绝用例：`new TaskStore(pool).createThread(user.id)` + `submit` 后 `assert.rejects(rotateOwnerPreparation(...), /profile_busy/)`。

- [ ] **Step 2: 验证失败**

Run: `TEST_DATABASE_URL=... pnpm exec tsx --test tests/server/model-rotation.test.ts`
Expected: FAIL，模块不存在。

- [ ] **Step 3: 实现 `rotateOwnerPreparation`**

实现顺序：
1. `ownerId = z.uuid().parse(ownerId)`；`root = await privatePreparationRoot(root)`。
2. 查询 `profile_preparation_jobs`：必须 `status = 'prepared'` 且 `first_port` 非空，否则 `preparation_job_not_ready`（manual 准备没有 marker/settingsHash/seed，不支持轮换）。
3. 查询活动任务：`SELECT 1 FROM tasks WHERE owner_id=$1 AND status IN ('queued','running','stopping','unknown') LIMIT 1`；有则 `throw new Error("profile_busy")`。
4. 读 `preparation.json`（`readProvisionSettings`），校验 `ownerId`、`profiles` 长度 3、`requestId`、`settingsHash`；读三个 `runtime.json`，`digest(raw) === manifest.fingerprints[id]`，否则 `prepared_files_changed`。
5. 计算 `rootIdentity`、`toHash`；若 `manifest.settingsHash === toHash` 且所有 runtime model 已匹配新 settings，返回 `changed:false, backupDirectory:null`。
6. 从各 `credentials.json` 读 `apiKey`（64 hex）；`contextOwner(pool, manifest.contextId ?? ownerId)`；`renderPreparation(profiles, dataOwner, {...settings, firstPort: job.first_port}, keys, ownerId)`。
7. 备份到 `join(backupRoot, new Date().toISOString().replaceAll(":", "-"), ownerId)`（0700）：`runtime-<profileId>.json`、`credentials-<profileId>.json`、`preparation.json`、有 seed 时 `seed.json`；原内容与权限 0600。
8. 原子写（同目录临时文件 + fsync + rename，模式 0444/0600）：每个 `profiles/<id>/runtime.json`、`credentials.json`、`preparation.json`（合并 `settingsHash: toHash`）、seed（合并 `settingsHash: toHash`）。
9. 重新读取并断言：runtime digest 等于新 `fingerprints[id]`，manifest `settingsHash === toHash`，seed hash 一致；返回 `backupDirectory`。
10. `restoreOwnerPreparation(root, ownerId, backupDirectory)`：从备份目录把 `runtime-<id>.json`、`credentials-<id>.json`、`preparation.json`、`seed.json` 原子写回原路径（0444/0600），重新校验 runtime digest 等于恢复后 manifest 指纹；备份缺失或校验失败抛 `rotation_backup_invalid`。

- [ ] **Step 4: 验证通过**

Run: `TEST_DATABASE_URL=... pnpm exec tsx --test tests/server/model-rotation.test.ts`
Expected: PASS（含幂等与 busy 用例）。

- [ ] **Step 5: Commit**

```bash
d = await commit.draft()
await d.commit("feat: rotate prepared profile models in place")
```

---

### Task 3: 轮换命令与全量事务

**Files:**
- Create: `scripts/rotate-model.ts`
- Modify: `packages/hermes-adapter/src/rotation.ts`（导出 `rotateAllOwners` 与 `RotationPlan`）
- Modify: `package.json`（`"hermes:rotate-model": "tsx scripts/rotate-model.ts"`）
- Test: `tests/server/model-rotation.test.ts`（追加全量轮换用例，直接测导出的 `rotateAllOwners`）

**Interfaces:**
- Consumes: `rotateOwnerPreparation`。
- Produces:

```ts
export type RotationPlan = {
  owners: string[];
  fromHash: string;
  toHash: string;
  changed: boolean;
};
export async function rotateAllOwners(
  pool: Pool,
  ownerIds: string[],
  currentSettings: z.infer<typeof provisionSettings>,
  settings: z.infer<typeof provisionSettings>,
  root: string,
  lastPort: number,
  backupRoot: string,
  apply: boolean,
): Promise<RotationPlan>;
```

- [ ] **Step 1: 写全量测试**

在 `tests/server/model-rotation.test.ts` 追加：准备两个用户；`rotateAllOwners(pool, [a.id,b.id], settings(), next, output, 31020, backupRoot, false)` 返回 `changed:true` 且两个 owner 的文件未变（dry-run）；`apply:true` 后两个 owner 都切换；数据库最新 policy 行的 `settings_hash` 等于返回的 `toHash`；再次 dry-run 返回 `changed:false`。失败回滚用例：先轮换 owner A 成功，再让 owner B 抛错（传入未准备的 owner），断言 A 已通过 `restoreOwnerPreparation` 恢复原 runtime 指纹且 policy 未新增。

- [ ] **Step 2: 验证失败**

Run: `TEST_DATABASE_URL=... pnpm exec tsx --test tests/server/model-rotation.test.ts`
Expected: FAIL，`rotateAllOwners` 不存在。

- [ ] **Step 3: 实现 `rotateAllOwners`**

1. 校验 input `ownerIds` 非空且去重；用 `preparationRootIdentity` 计算 `rootIdentity`，分别用 `currentSettings`/`settings` 计算 `fromHash`/`toHash`；要求 `fromHash !== toHash`。
2. 读取最新 policy 行，允许其 `settings_hash` 等于 `fromHash` 或 `toHash`（崩溃后重跑），其他值拒绝 `preparation_policy_changed`。同时允许每个 owner 的 `preparation.json.settingsHash` 为两者之一。
3. dry-run：仅查询每个 owner 的 job/active task/文件指纹，返回 `{owners, fromHash, toHash, changed}`；不写文件、不写 policy。
4. apply：循环调用 `rotateOwnerPreparation`（已切换的 owner 返回 `changed:false`）；任一失败，按已产生的 `backupDirectory` 逆序调用 `restoreOwnerPreparation`，再抛错。
5. 全部 owner 成功后，在事务中向 `profile_preparation_policy_versions` 追加新行；若最新行已是 `toHash` 则跳过：
```sql
INSERT INTO profile_preparation_policy_versions(root_identity,settings_hash,first_port,last_port)
SELECT $1,$2,$3,$4
WHERE (SELECT settings_hash FROM profile_preparation_policy_versions ORDER BY generation DESC LIMIT 1) IS DISTINCT FROM $2
```
6. 返回 `{owners, fromHash, toHash, changed}`；不写 settings 文件（由 CLI 负责）。

- [ ] **Step 4: 实现 CLI**

`scripts/rotate-model.ts` 参数：
```
DATABASE_URL=OWNER_URL pnpm hermes:rotate-model \
  CURRENT_SETTINGS NEW_SETTINGS PRIVATE_ROOT LAST_PORT BACKUP_ROOT \
  [--owner UUID ...] [--apply]
```
- 读两份 settings；校验 `image/gatewayOrigin/firstPort` 相同、`lastPort` 与 policy 一致。
- 若 `docker ps -q --filter label=lab.alpha.profile` 非空，拒绝 `profile_container_running`。
- 若 `systemctl is-active --quiet alpha-lab-supervisor` 或 `alpha-lab-worker` 为 active，拒绝 `rotation_services_running`（运行中的 supervisor 会用旧部署 hash 重新封印，抵消解封）。
- 查询 owner：显式参数或 `SELECT owner_id FROM profile_preparation_jobs WHERE status = 'prepared' ORDER BY owner_id`。
- 调用 `rotateAllOwners`；`--apply` 成功后原子替换 `CURRENT_SETTINGS` 内容为 `NEW_SETTINGS`（0600，旧文件备份到 `BACKUP_ROOT/<timestamp>/provider/profile-settings.json`）。
- 输出 `{owners, fromHash, toHash, changed, backupRoot}`；dry-run 只打印计划。
- 崩溃恢复：settings 写入后即使 policy 追加失败也不回滚 settings；重跑同一命令会看到 artifacts/settings 已是目标 hash，跳过已切换项并补齐 policy。只有 artifacts 阶段失败才按 `restoreOwnerPreparation` 回滚。

- [ ] **Step 5: 验证**

Run: `pnpm typecheck`
Run: `TEST_DATABASE_URL=... pnpm exec tsx --test tests/server/model-rotation.test.ts`
Expected: PASS。

- [ ] **Step 6: Commit**

```bash
d = await commit.draft()
await d.commit("feat: add model rotation command")
```

---

### Task 4: 生产轮换与验证

**Files:**
- Modify: `/opt/hermes-alpha-lab/private/profile-settings.json`（生产）
- Modify: `/opt/hermes-alpha-lab/private/provider/claude-code-model.json`（生产）
- Modify: `/opt/hermes-alpha-lab/private/provider/news-translation.json`（生产）
- Modify: `alpha-lab-preparer.service` 的 release 工作目录
- Evidence: `docs/implementation-status.md`

**Interfaces:**
- Consumes: `pnpm hermes:rotate-model`、`scripts/verify-prepared-profile.ts`。

- [ ] **Step 1: 备份、部署新代码与预检**

把 Task 1-3 的文件同步到 `/opt/hermes-alpha-lab/releases/20261115-feishu-ui`，在该目录运行 `pnpm typecheck`。生产 preparer 当前指向旧 release，必须增加 drop-in 指向新 release：

```bash
install -d -m 755 /etc/systemd/system/alpha-lab-preparer.service.d
printf '[Service]\nWorkingDirectory=/opt/hermes-alpha-lab/releases/20261115-feishu-ui\n' \
  > /etc/systemd/system/alpha-lab-preparer.service.d/release.conf
systemctl daemon-reload
```

以 owner 身份应用迁移与新增授权：

```bash
cd /opt/hermes-alpha-lab/releases/20261115-feishu-ui
set -a; . /opt/hermes-alpha-lab/private/database-owner.env; set +a
pnpm db:migrate
pnpm exec tsx scripts/upgrade-multiuser-access.ts alpha_live_web alpha_live_worker alpha_live_tools alpha_live_feishu
```

备份与预检：

```bash
TS=$(date -u +%Y%m%dT%H%M%SZ)
install -d -m 700 /opt/hermes-alpha-lab/private/rotation-backups/$TS
cp -a /opt/hermes-alpha-lab/private/profile-settings.json /opt/hermes-alpha-lab/private/provider/*.json /opt/hermes-alpha-lab/private/rotation-backups/$TS/
systemctl stop alpha-lab-worker alpha-lab-supervisor
docker ps --filter label=lab.alpha.profile --format '{{.Names}}'
```
Expected: 迁移成功；新表存在且 policy 行数等于旧表的 1 行；无容器输出；活动任务为 0。

- [ ] **Step 2: 生成 deepseek settings 并 dry-run**

复制 `profile-settings.json` 到 `private/provider/profile-settings.deepseek.json`，把三角色 `name` 改为 `deepseek-v4.1-flash`（baseUrl/apiMode/authMode/apiKey 不变，0600）。执行：

```bash
pnpm hermes:rotate-model \
  /opt/hermes-alpha-lab/private/profile-settings.json \
  /opt/hermes-alpha-lab/private/provider/profile-settings.deepseek.json \
  /opt/hermes-alpha-lab/private/profiles 19999 \
  /opt/hermes-alpha-lab/private/rotation-backups/$TS
```
Expected: 输出 3 个 owner、两个 hash、`changed: true`，无文件改动。

- [ ] **Step 3: 应用轮换**

同命令加 `--apply`。Expected: `backupRoot` 打印；`preparation.json.settingsHash` 等于 `toHash`；三个 runtime `model.name` 为 `deepseek-v4.1-flash`。

- [ ] **Step 4: 切换辅助模型并重启 preparer**

把 `private/provider/claude-code-model.json` 与 `news-translation.json` 的 `name` 改为 `deepseek-v4.1-flash`（0600，旧文件已在 Step 1 备份）；确认 `profile-settings.json` 已是 deepseek；重启 `alpha-lab-preparer`，日志无 `preparation_policy_changed`。

- [ ] **Step 5: 9 个 Profile 真实验证**

在 release 目录加载 worker DB 连接后对每个 owner 目录执行（checker 会自行起停 3 个容器）：

```bash
cd /opt/hermes-alpha-lab/releases/20261115-feishu-ui
set -a; . /opt/hermes-alpha-lab/private/database-services/worker.env; set +a
pnpm exec tsx scripts/verify-prepared-profile.ts /opt/hermes-alpha-lab/private/profiles/<owner>
```
Expected: `acceptance_verified`；`profiles.verified.json` 重写；证据中模型请求为 deepseek。

失败回滚（不要只拷备份，否则 policy/settings 仍指向新模型）：把 Step 1 的旧 `profile-settings.json` 作为 `NEW_SETTINGS`、当前 deepseek 文件作为 `CURRENT_SETTINGS`，运行同一命令 `--apply`；然后从备份恢复 `claude-code-model.json`/`news-translation.json`，重启 preparer，重跑旧模型验证，再启动 supervisor/worker。

- [ ] **Step 6: 恢复服务与新闻翻译 smoke**

`systemctl start alpha-lab-supervisor alpha-lab-worker`；确认 `/` 200、`/api/auth/session` 401、无新 `BFF request failed`。用现有 `translatedNews` 路径请求一次真实新闻（或等价网关请求），断言返回中文 title/summary 且 id 数量一致。

- [ ] **Step 7: 更新文档并提交**

`docs/implementation-status.md` 追加本轮迁移号、policy 代际、BUILD_ID、9 个 Profile 验证结果、回滚命令；提交。

---

### Task 5: 清理与文档收尾

- [ ] 删除临时 `profile-settings.deepseek.json`（已复制到 settings）。
- [ ] 在 `docs/database-access.md` 记录新表与授权。
- [ ] 在 `docs/implementation-status.md` 写明 glm 仅存在于 `rotation-backups/<TS>`，以及删除备份的前置条件。
- [ ] 运行 `pnpm typecheck`、相关测试、`pnpm build`，记录结果。

## Self-Review

- Spec coverage：policy 代际、既有 9 Profile 原地轮换、新用户准备、备份/回滚、新闻翻译、bootstrap、验证与文档均有任务。
- 类型一致性：`preparationRootIdentity`/`preparationSettingsHash` 在 Task 1 定义，Task 2/3 使用；`rotateOwnerPreparation` 返回 `RotatedOwner`，`rotateAllOwners` 消费它。
- 未覆盖：远端网关其他模型无法删除（设计明确非目标）；生产验证需要维护窗口与真实模型费用。
