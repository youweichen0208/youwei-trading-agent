import { test, type TestContext } from "node:test";
import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import {
  lstat,
  mkdtemp,
  readFile,
  readdir,
  realpath,
  rm,
  writeFile,
} from "node:fs/promises";
import { join, relative, sep } from "node:path";
import { tmpdir } from "node:os";
import { Pool } from "pg";
import { migrate } from "../../packages/server/src/migrate.js";
import { AuthStore } from "../../packages/server/src/auth.js";
import { TaskStore } from "../../packages/server/src/tasks.js";
import { ExecutionContextStore } from "../../packages/server/src/execution-context.js";
import {
  AutoProfilePreparer,
  preparationRootIdentity,
  preparationSettingsHash,
} from "../../packages/hermes-adapter/src/auto-preparation.js";
import { digest } from "../../packages/hermes-adapter/src/provision.js";
import {
  restoreOwnerPreparation,
  rotateAllOwners,
  rotateOwnerPreparation,
} from "../../packages/hermes-adapter/src/rotation.js";

const url = process.env.TEST_DATABASE_URL;
if (!url || !new URL(url).pathname.endsWith("_test"))
  throw new Error("Dedicated TEST_DATABASE_URL ending in _test required");
const settings = (firstPort = 31000) => {
  const model = (role: string) => ({
    name: "test-only-model",
    baseUrl: "https://model.invalid/v1",
    apiMode: "chat_completions" as const,
    apiKey: `test-only-credential-${role}`,
  });
  return {
    image: `sha256:${"a".repeat(64)}`,
    gatewayOrigin: "http://host.docker.internal:3410",
    firstPort,
    models: {
      research: model("research"),
      prediction: model("prediction"),
      portfolio: model("portfolio"),
    },
  };
};
const rotatedSettings = () => {
  const next = settings();
  for (const role of ["research", "prediction", "portfolio"] as const)
    next.models[role]!.name = "deepseek-v4.1-flash";
  return next;
};
async function setup(t: TestContext) {
  const schema = `test_${randomUUID().replaceAll("-", "")}`;
  const admin = new Pool({ connectionString: url });
  await admin.query(`CREATE SCHEMA ${schema}`);
  const connection = new URL(url!);
  connection.searchParams.set("options", `-c search_path=${schema}`);
  const pool = new Pool({ connectionString: connection.toString() });
  const root = await mkdtemp(join(tmpdir(), "alpha-rotate-"));
  t.after(async () => {
    await pool.end();
    await admin.query(`DROP SCHEMA ${schema} CASCADE`);
    await admin.end();
    await rm(root, { recursive: true, force: true });
  });
  await migrate(pool);
  const user = () =>
    new AuthStore(pool).register(
      `${randomUUID()}@test.invalid`,
      "a-long-test-password",
    );
  const output = join(root, "prepared");
  const preparer = await AutoProfilePreparer.open(
    pool,
    settings(),
    output,
    lastPort,
  );
  return {
    pool,
    root,
    output,
    preparer,
    user,
    backupRoot: join(root, "backups"),
  };
}
const lastPort = 31020;

test("rotation rewrites runtime specs, fingerprints and seed without touching memory", async (t) => {
  const { pool, output, preparer, user, backupRoot } = await setup(t);
  const owner = await user();
  assert.equal((await preparer.prepare(owner.id)).status, "prepared");
  const manifestPath = join(output, owner.id, "preparation.json");
  const originalManifest = JSON.parse(await readFile(manifestPath, "utf8"));
  const researchId = originalManifest.profiles.find(
    (p: { role: string }) => p.role === "research",
  ).id as string;
  const profileDirectory = join(output, owner.id, "profiles", researchId);
  const original = await readFile(
    join(profileDirectory, "runtime.json"),
    "utf8",
  );
  const originalSeed = await readFile(
    join(output, ".auto-seeds", `${originalManifest.requestId}.json`),
    "utf8",
  );
  // Memory is the container's /opt/data volume: rotation must never rewrite it.
  await writeFile(
    join(profileDirectory, "home", "memory.json"),
    '{"kept":true}\n',
    {
      mode: 0o600,
    },
  );
  const identity = await readFile(
    join(profileDirectory, "home", ".alpha-profile.json"),
    "utf8",
  );
  const next = rotatedSettings();
  const toHash = preparationSettingsHash(
    next,
    lastPort,
    await preparationRootIdentity(await realpath(output)),
  );
  const result = await rotateOwnerPreparation(
    pool,
    owner.id,
    next,
    output,
    lastPort,
    backupRoot,
  );
  assert.equal(result.changed, true);
  assert.equal(result.ownerId, owner.id);
  assert.equal(result.fromHash, originalManifest.settingsHash);
  assert.notEqual(result.fromHash, result.toHash);
  assert.equal(result.toHash, toHash);
  const manifestRaw = await readFile(manifestPath, "utf8");
  const manifest = JSON.parse(manifestRaw);
  assert.equal(manifest.settingsHash, result.toHash);
  assert.equal(manifest.requestId, originalManifest.requestId);
  for (const profile of manifest.profiles) {
    const runtime = await readFile(
      join(output, owner.id, "profiles", profile.id, "runtime.json"),
      "utf8",
    );
    assert.equal(JSON.parse(runtime).model.name, "deepseek-v4.1-flash");
    assert.equal(manifest.fingerprints[profile.id], digest(runtime));
    assert.equal(
      (
        await lstat(
          join(output, owner.id, "profiles", profile.id, "runtime.json"),
        )
      ).mode & 0o777,
      0o444,
    );
  }
  assert.notEqual(
    await readFile(join(profileDirectory, "runtime.json"), "utf8"),
    original,
  );
  // Memory and the mounted identity file are byte-identical after rotation.
  assert.equal(
    await readFile(join(profileDirectory, "home", "memory.json"), "utf8"),
    '{"kept":true}\n',
  );
  assert.equal(
    await readFile(
      join(profileDirectory, "home", ".alpha-profile.json"),
      "utf8",
    ),
    identity,
  );
  // Fingerprints of the untouched profiles change too.
  assert.deepEqual(
    Object.keys(manifest.fingerprints).sort(),
    originalManifest.profiles.map((p: { id: string }) => p.id).sort(),
  );
  const seed = JSON.parse(
    await readFile(
      join(output, ".auto-seeds", `${manifest.requestId}.json`),
      "utf8",
    ),
  );
  assert.equal(seed.settingsHash, result.toHash);
  assert.equal(seed.requestId, originalManifest.requestId);
  // Backup is private, complete and holds the pre-rotation bytes.
  const backupDirectory = result.backupDirectory!;
  assert.ok(backupDirectory);
  assert.equal(relative(backupRoot, backupDirectory).split(sep).length, 2);
  assert.equal((await lstat(backupDirectory)).mode & 0o777, 0o700);
  assert.equal((await lstat(backupRoot)).mode & 0o777, 0o700);
  assert.equal(
    await readFile(join(backupDirectory, `runtime-${researchId}.json`), "utf8"),
    original,
  );
  assert.equal(
    await readFile(join(backupDirectory, "preparation.json"), "utf8"),
    JSON.stringify(originalManifest, null, 2) + "\n",
  );
  assert.equal(
    await readFile(join(backupDirectory, "seed.json"), "utf8"),
    originalSeed,
  );
  // Rotation keeps each profile's gateway key: only the model policy moves.
  for (const profile of manifest.profiles) {
    const live = JSON.parse(
      await readFile(
        join(output, owner.id, "profiles", profile.id, "credentials.json"),
        "utf8",
      ),
    );
    const backup = JSON.parse(
      await readFile(
        join(backupDirectory, `credentials-${profile.id}.json`),
        "utf8",
      ),
    );
    assert.equal(live.apiKey, backup.apiKey);
  }
  for (const entry of await readdir(backupDirectory))
    assert.equal(
      (await lstat(join(backupDirectory, entry))).mode & 0o777,
      0o600,
      `${entry} must be private`,
    );
  assert.deepEqual(
    (await readdir(backupDirectory)).sort(),
    [
      "preparation.json",
      "seed.json",
      ...originalManifest.profiles.flatMap((profile: { id: string }) => [
        `runtime-${profile.id}.json`,
        `credentials-${profile.id}.json`,
      ]),
    ].sort(),
  );
  // A second rotation with the same settings is a no-op with no new backup.
  const backupsBefore = (await readdir(backupRoot)).length;
  const again = await rotateOwnerPreparation(
    pool,
    owner.id,
    next,
    output,
    lastPort,
    backupRoot,
  );
  assert.deepEqual(again, {
    ownerId: owner.id,
    fromHash: toHash,
    toHash,
    changed: false,
    backupDirectory: null,
  });
  assert.equal((await readdir(backupRoot)).length, backupsBefore);
  assert.equal(await readFile(manifestPath, "utf8"), manifestRaw);
});

test("restore reverts a rotation to the exact backed-up artifacts", async (t) => {
  const { pool, output, preparer, user, backupRoot } = await setup(t);
  const owner = await user();
  assert.equal((await preparer.prepare(owner.id)).status, "prepared");
  const manifestPath = join(output, owner.id, "preparation.json");
  const originalManifest = JSON.parse(await readFile(manifestPath, "utf8"));
  const runtimes = new Map<string, string>();
  for (const profile of originalManifest.profiles)
    runtimes.set(
      profile.id,
      await readFile(
        join(output, owner.id, "profiles", profile.id, "runtime.json"),
        "utf8",
      ),
    );
  const rotated = await rotateOwnerPreparation(
    pool,
    owner.id,
    rotatedSettings(),
    output,
    lastPort,
    backupRoot,
  );
  assert.equal(rotated.changed, true);
  await restoreOwnerPreparation(output, owner.id, rotated.backupDirectory!);
  const restored = JSON.parse(await readFile(manifestPath, "utf8"));
  assert.deepEqual(restored, originalManifest);
  for (const profile of restored.profiles) {
    const runtime = await readFile(
      join(output, owner.id, "profiles", profile.id, "runtime.json"),
      "utf8",
    );
    assert.equal(runtime, runtimes.get(profile.id));
    assert.equal(restored.fingerprints[profile.id], digest(runtime));
    assert.equal(JSON.parse(runtime).model.name, "test-only-model");
    assert.equal(
      (
        await lstat(
          join(output, owner.id, "profiles", profile.id, "runtime.json"),
        )
      ).mode & 0o777,
      0o444,
    );
  }
  const seed = JSON.parse(
    await readFile(
      join(output, ".auto-seeds", `${restored.requestId}.json`),
      "utf8",
    ),
  );
  assert.equal(seed.settingsHash, originalManifest.settingsHash);
  // A rotation is possible again after the restore, proving the restore is exact.
  assert.equal(
    (
      await rotateOwnerPreparation(
        pool,
        owner.id,
        rotatedSettings(),
        output,
        lastPort,
        backupRoot,
      )
    ).changed,
    true,
  );
});

test("restore rejects missing or corrupt backups", async (t) => {
  const { pool, output, preparer, user, backupRoot, root } = await setup(t);
  const owner = await user();
  assert.equal((await preparer.prepare(owner.id)).status, "prepared");
  await assert.rejects(
    restoreOwnerPreparation(output, owner.id, join(root, "absent")),
    /rotation_backup_invalid/,
  );
  const rotated = await rotateOwnerPreparation(
    pool,
    owner.id,
    rotatedSettings(),
    output,
    lastPort,
    backupRoot,
  );
  const backupDirectory = rotated.backupDirectory!;
  const runtime = (await readdir(backupDirectory)).find((f) =>
    f.startsWith("runtime-"),
  )!;
  await writeFile(join(backupDirectory, runtime), '{"version":1}\n');
  await assert.rejects(
    restoreOwnerPreparation(output, owner.id, backupDirectory),
    /rotation_backup_invalid/,
  );
});

test("rotation refuses an owner with an active task", async (t) => {
  const { pool, output, preparer, user, backupRoot } = await setup(t);
  const owner = await user();
  assert.equal((await preparer.prepare(owner.id)).status, "prepared");
  const manifestPath = join(output, owner.id, "preparation.json");
  const before = await readFile(manifestPath, "utf8");
  const tasks = new TaskStore(pool);
  const thread = await tasks.createThread(owner.id);
  await tasks.submit(owner.id, thread.id, randomUUID(), { input: "research" });
  await assert.rejects(
    rotateOwnerPreparation(
      pool,
      owner.id,
      rotatedSettings(),
      output,
      lastPort,
      backupRoot,
    ),
    /profile_busy/,
  );
  assert.equal(await readFile(manifestPath, "utf8"), before);
  await assert.rejects(readdir(backupRoot), /ENOENT/);
});

test("rotation keeps group-context bindings and ignores other contexts' tasks", async (t) => {
  const { pool, output, preparer, user, backupRoot } = await setup(t);
  const owner = await user();
  assert.equal((await preparer.prepare(owner.id)).status, "prepared");
  const group = await new ExecutionContextStore(pool).group(
    owner.id,
    "cli_rotation",
    "chat_rotation",
  );
  assert.equal((await preparer.prepare(group.id)).status, "prepared");
  const personalBefore = await readFile(
    join(output, owner.id, "preparation.json"),
    "utf8",
  );
  const original = JSON.parse(
    await readFile(join(output, group.id, "preparation.json"), "utf8"),
  );
  assert.equal(original.contextId, group.id);
  assert.equal(original.ownerId, owner.id);
  // Work in another context must not block rotating this context's profiles.
  const tasks = new TaskStore(pool);
  const thread = await tasks.createThread(owner.id);
  await tasks.submit(owner.id, thread.id, randomUUID(), { input: "research" });
  const result = await rotateOwnerPreparation(
    pool,
    group.id,
    rotatedSettings(),
    output,
    lastPort,
    backupRoot,
  );
  assert.equal(result.changed, true);
  const manifest = JSON.parse(
    await readFile(join(output, group.id, "preparation.json"), "utf8"),
  );
  assert.equal(manifest.contextId, group.id);
  assert.equal(manifest.ownerId, owner.id);
  assert.equal(manifest.requestId, original.requestId);
  assert.equal(manifest.settingsHash, result.toHash);
  for (const profile of manifest.profiles) {
    const runtime = await readFile(
      join(output, group.id, "profiles", profile.id, "runtime.json"),
      "utf8",
    );
    assert.equal(JSON.parse(runtime).model.name, "deepseek-v4.1-flash");
    assert.equal(JSON.parse(runtime).ownerId, owner.id);
    assert.equal(manifest.fingerprints[profile.id], digest(runtime));
  }
  assert.equal(
    await readFile(join(output, owner.id, "preparation.json"), "utf8"),
    personalBefore,
  );
  // A task running in this context does block the rotation.
  const rotatedManifest = await readFile(
    join(output, group.id, "preparation.json"),
    "utf8",
  );
  const groupThread = await tasks.createThread(owner.id, group.id);
  await tasks.submit(owner.id, groupThread.id, randomUUID(), {
    input: "research",
  });
  await assert.rejects(
    rotateOwnerPreparation(
      pool,
      group.id,
      rotatedSettings(),
      output,
      lastPort,
      backupRoot,
    ),
    /profile_busy/,
  );
  assert.equal(
    await readFile(join(output, group.id, "preparation.json"), "utf8"),
    rotatedManifest,
  );
});

test("rotation omits an absent seed from the backup", async (t) => {
  const { pool, output, preparer, user, backupRoot } = await setup(t);
  const owner = await user();
  assert.equal((await preparer.prepare(owner.id)).status, "prepared");
  const manifest = JSON.parse(
    await readFile(join(output, owner.id, "preparation.json"), "utf8"),
  );
  await rm(join(output, ".auto-seeds", `${manifest.requestId}.json`));
  const result = await rotateOwnerPreparation(
    pool,
    owner.id,
    rotatedSettings(),
    output,
    lastPort,
    backupRoot,
  );
  assert.equal(result.changed, true);
  assert.ok(
    !(await readdir(result.backupDirectory!)).includes("seed.json"),
    "seed backup must be omitted when the seed is absent",
  );
});

test("rotation requires a prepared owner job", async (t) => {
  const { pool, output, user, backupRoot } = await setup(t);
  const owner = await user();
  await assert.rejects(
    rotateOwnerPreparation(
      pool,
      owner.id,
      rotatedSettings(),
      output,
      lastPort,
      backupRoot,
    ),
    /preparation_job_not_ready/,
  );
  await assert.rejects(
    rotateOwnerPreparation(
      pool,
      randomUUID(),
      rotatedSettings(),
      output,
      lastPort,
      backupRoot,
    ),
    /preparation_job_not_ready/,
  );
});

test("rotation refuses a manual preparation", async (t) => {
  const { pool, output, user, backupRoot } = await setup(t);
  const owner = await user();
  // Manual preparations have no automatic marker, manifest generation or seed.
  await pool.query(
    "UPDATE profile_preparation_jobs SET status='manual',first_port=32000,settings_hash=$2 WHERE owner_id=$1",
    [owner.id, "c".repeat(64)],
  );
  await assert.rejects(
    rotateOwnerPreparation(
      pool,
      owner.id,
      rotatedSettings(),
      output,
      lastPort,
      backupRoot,
    ),
    /preparation_job_not_ready/,
  );
});

test("rotation clears the recorded deployment seal so profiles can restart", async (t) => {
  const { pool, output, preparer, user, backupRoot } = await setup(t);
  const owner = await user();
  assert.equal((await preparer.prepare(owner.id)).status, "prepared");
  const seal = (hash: string) =>
    pool.query(
      "UPDATE profile_instances SET deployment_hash=$2 WHERE owner_id=$1",
      [owner.id, hash],
    );
  const seals = async () =>
    (
      await pool.query<{ deployment_hash: string | null }>(
        "SELECT deployment_hash FROM profile_instances WHERE owner_id=$1 ORDER BY role",
        [owner.id],
      )
    ).rows.map((row) => row.deployment_hash);
  await seal("a".repeat(64));
  assert.deepEqual(await seals(), [
    "a".repeat(64),
    "a".repeat(64),
    "a".repeat(64),
  ]);
  const result = await rotateOwnerPreparation(
    pool,
    owner.id,
    rotatedSettings(),
    output,
    lastPort,
    backupRoot,
  );
  assert.equal(result.changed, true);
  assert.deepEqual(await seals(), [null, null, null]);
  // A re-sealed generation is cleared even when the rerun changes nothing.
  await seal("b".repeat(64));
  const again = await rotateOwnerPreparation(
    pool,
    owner.id,
    rotatedSettings(),
    output,
    lastPort,
    backupRoot,
  );
  assert.equal(again.changed, false);
  assert.deepEqual(await seals(), [null, null, null]);
});

test("rotation plans every owner, then switches all of them before the policy", async (t) => {
  const { pool, output, preparer, user, backupRoot } = await setup(t);
  const first = await user();
  const second = await user();
  assert.equal((await preparer.prepare(first.id)).status, "prepared");
  assert.equal((await preparer.prepare(second.id)).status, "prepared");
  const manifest = (owner: string) =>
    readFile(join(output, owner, "preparation.json"), "utf8");
  const originals = [await manifest(first.id), await manifest(second.id)];
  const generations = async () =>
    (
      await pool.query<{ settings_hash: string }>(
        "SELECT settings_hash FROM profile_preparation_policy_versions ORDER BY generation",
      )
    ).rows;
  assert.equal((await generations()).length, 1);
  const plan = await rotateAllOwners(
    pool,
    [first.id, second.id],
    settings(),
    rotatedSettings(),
    output,
    lastPort,
    backupRoot,
    false,
  );
  assert.deepEqual(plan.owners, [first.id, second.id]);
  assert.notEqual(plan.fromHash, plan.toHash);
  assert.equal(plan.changed, true);
  // A dry run must not rewrite artifacts, create backups or append a policy.
  assert.equal(await manifest(first.id), originals[0]);
  assert.equal(await manifest(second.id), originals[1]);
  assert.equal((await generations()).length, 1);
  await assert.rejects(readdir(backupRoot), /ENOENT/);
  const applied = await rotateAllOwners(
    pool,
    [first.id, second.id],
    settings(),
    rotatedSettings(),
    output,
    lastPort,
    backupRoot,
    true,
  );
  assert.equal(applied.changed, true);
  assert.equal(applied.toHash, plan.toHash);
  for (const owner of [first.id, second.id]) {
    const rotated = JSON.parse(await manifest(owner));
    assert.equal(rotated.settingsHash, applied.toHash);
    for (const profile of rotated.profiles) {
      const runtime = await readFile(
        join(output, owner, "profiles", profile.id, "runtime.json"),
        "utf8",
      );
      assert.equal(JSON.parse(runtime).model.name, "deepseek-v4.1-flash");
      assert.equal(rotated.fingerprints[profile.id], digest(runtime));
    }
  }
  // The policy is appended once, after every owner has switched.
  const history = await generations();
  assert.equal(history.length, 2);
  assert.equal(history.at(-1)!.settings_hash, applied.toHash);
  // A repeated plan sees the settled generation and schedules no work.
  const settled = await rotateAllOwners(
    pool,
    [first.id, second.id],
    settings(),
    rotatedSettings(),
    output,
    lastPort,
    backupRoot,
    false,
  );
  assert.equal(settled.changed, false);
  assert.equal(settled.fromHash, plan.fromHash);
  assert.equal(settled.toHash, applied.toHash);
});

test("rotation restores every rotated owner when a later owner fails", async (t) => {
  const { pool, output, preparer, user, backupRoot } = await setup(t);
  const ready = await user();
  // Registration alone leaves the owner without a prepared job.
  const unready = await user();
  assert.equal((await preparer.prepare(ready.id)).status, "prepared");
  const manifestPath = join(output, ready.id, "preparation.json");
  const original = JSON.parse(await readFile(manifestPath, "utf8"));
  const runtimes = new Map<string, string>();
  for (const profile of original.profiles)
    runtimes.set(
      profile.id,
      await readFile(
        join(output, ready.id, "profiles", profile.id, "runtime.json"),
        "utf8",
      ),
    );
  const policy = async () =>
    (
      await pool.query<{ count: number }>(
        "SELECT count(*)::int AS count FROM profile_preparation_policy_versions",
      )
    ).rows[0]!.count;
  const before = await policy();
  await assert.rejects(
    rotateAllOwners(
      pool,
      [ready.id, unready.id],
      settings(),
      rotatedSettings(),
      output,
      lastPort,
      backupRoot,
      true,
    ),
    /preparation_job_not_ready/,
  );
  // The owner that did rotate is back on its exact original generation.
  assert.deepEqual(JSON.parse(await readFile(manifestPath, "utf8")), original);
  for (const profile of original.profiles) {
    const runtime = await readFile(
      join(output, ready.id, "profiles", profile.id, "runtime.json"),
      "utf8",
    );
    assert.equal(runtime, runtimes.get(profile.id));
    assert.equal(original.fingerprints[profile.id], digest(runtime));
    assert.equal(JSON.parse(runtime).model.name, "test-only-model");
  }
  assert.equal(await policy(), before);
});

test("rotation accepts an owner prepared under an older generation than the policy", async (t) => {
  const { pool, output, preparer, user, backupRoot } = await setup(t);
  const owner = await user();
  assert.equal((await preparer.prepare(owner.id)).status, "prepared");
  const manifestPath = join(output, owner.id, "preparation.json");
  const original = JSON.parse(await readFile(manifestPath, "utf8"));
  const job = (
    await pool.query<{ settings_hash: string }>(
      "SELECT settings_hash FROM profile_preparation_jobs WHERE owner_id=$1",
      [owner.id],
    )
  ).rows[0]!;
  // A newer policy generation appears without re-preparing the owner: exactly
  // the production skew where existing artifacts keep an older hash.
  const policySettings = settings();
  for (const role of ["research", "prediction", "portfolio"] as const)
    policySettings.models[role]!.name = "policy-only-model";
  const rootIdentity = await preparationRootIdentity(await realpath(output));
  const policyHash = preparationSettingsHash(
    policySettings,
    lastPort,
    rootIdentity,
  );
  assert.notEqual(policyHash, original.settingsHash);
  await pool.query(
    "INSERT INTO profile_preparation_policy_versions(root_identity,settings_hash,first_port,last_port) VALUES($1,$2,$3,$4)",
    [rootIdentity, policyHash, policySettings.firstPort, lastPort],
  );
  // The owner still carries the generation it was prepared under.
  assert.equal(job.settings_hash, original.settingsHash);
  const plan = await rotateAllOwners(
    pool,
    [owner.id],
    policySettings,
    rotatedSettings(),
    output,
    lastPort,
    backupRoot,
    false,
  );
  assert.equal(plan.fromHash, policyHash);
  assert.equal(plan.changed, true);
  const applied = await rotateAllOwners(
    pool,
    [owner.id],
    policySettings,
    rotatedSettings(),
    output,
    lastPort,
    backupRoot,
    true,
  );
  assert.equal(applied.changed, true);
  const rotated = JSON.parse(await readFile(manifestPath, "utf8"));
  assert.equal(rotated.settingsHash, applied.toHash);
  for (const profile of rotated.profiles) {
    const runtime = await readFile(
      join(output, owner.id, "profiles", profile.id, "runtime.json"),
      "utf8",
    );
    assert.equal(JSON.parse(runtime).model.name, "deepseek-v4.1-flash");
    assert.equal(rotated.fingerprints[profile.id], digest(runtime));
  }
  // The target generation is appended after the policy the plan started from.
  const history = (
    await pool.query<{ settings_hash: string }>(
      "SELECT settings_hash FROM profile_preparation_policy_versions ORDER BY generation",
    )
  ).rows.map((row) => row.settings_hash);
  assert.deepEqual(history, [
    original.settingsHash,
    policyHash,
    applied.toHash,
  ]);
});
