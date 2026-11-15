import { test, type TestContext } from "node:test";
import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { spawn } from "node:child_process";
import { once } from "node:events";
import {
  mkdtemp,
  readFile,
  rm,
  writeFile,
  lstat,
  chmod,
  symlink,
  readdir,
} from "node:fs/promises";
import { join, resolve } from "node:path";
import { tmpdir } from "node:os";
import { setTimeout } from "node:timers/promises";
import { Pool } from "pg";
import { migrate } from "../../packages/server/src/migrate.js";
import { AuthStore } from "../../packages/server/src/auth.js";
import {
  AutoProfilePreparer,
  preparationRootIdentity,
  preparationSettingsHash,
} from "../../packages/hermes-adapter/src/auto-preparation.js";
import {
  prepareProfiles,
  provisionSettings,
} from "../../packages/hermes-adapter/src/provision.js";

const url = process.env.TEST_DATABASE_URL;
if (!url || !new URL(url).pathname.endsWith("_test"))
  throw new Error("Dedicated TEST_DATABASE_URL ending in _test required");
const settings = (firstPort = 31000) => ({
  image: `sha256:${"a".repeat(64)}`,
  gatewayOrigin: "http://host.docker.internal:3410",
  firstPort,
  models: Object.fromEntries(
    ["research", "prediction", "portfolio"].map((role) => [
      role,
      {
        name: "test-only-model",
        baseUrl: "https://model.invalid/v1",
        apiMode: "chat_completions",
        apiKey: `test-only-credential-${role}`,
      },
    ]),
  ),
});
async function setup(t: TestContext, lastPort = 31020) {
  const schema = `test_${randomUUID().replaceAll("-", "")}`;
  const admin = new Pool({ connectionString: url });
  await admin.query(`CREATE SCHEMA ${schema}`);
  const connection = new URL(url!);
  connection.searchParams.set("options", `-c search_path=${schema}`);
  const pool = new Pool({ connectionString: connection.toString() });
  const root = await mkdtemp(join(tmpdir(), "alpha-auto-"));
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
  return { schema, admin, pool, root, output, preparer, user, connection };
}
async function until(check: () => Promise<boolean>) {
  const deadline = Date.now() + 15000;
  while (!(await check())) {
    if (Date.now() > deadline) throw new Error("condition_timeout");
    await setTimeout(30);
  }
}

test("concurrent preparers allocate disjoint triples and stable independent secrets without making profiles ready", async (t) => {
  const { pool, output, preparer, user } = await setup(t);
  const users = await Promise.all([user(), user(), user()]);
  const peer = await AutoProfilePreparer.open(pool, settings(), output, 31020);
  const results = await Promise.all(
    users.flatMap((u) => [preparer.prepare(u.id), peer.prepare(u.id)]),
  );
  assert.equal(results.filter((r) => r.status === "prepared").length, 3);
  assert.equal(results.filter((r) => r.status === "busy").length, 3);
  const ports = (
    await pool.query(
      "SELECT port,owner_id FROM profile_api_ports ORDER BY port",
    )
  ).rows;
  assert.deepEqual(
    ports.map((p) => p.port),
    Array.from({ length: 9 }, (_, i) => 31000 + i),
  );
  const keys = new Set<string>();
  for (const u of users) {
    const env = await readFile(join(output, u.id, "worker.env"), "utf8");
    for (const line of env.trim().split("\n")) keys.add(line.split("=")[1]!);
    assert.equal(
      (await preparer.prepare(u.id.toUpperCase())).status,
      "prepared",
    );
    assert.equal(await readFile(join(output, u.id, "worker.env"), "utf8"), env);
    assert.equal((await lstat(join(output, u.id))).mode & 0o777, 0o700);
    assert.equal(
      (await lstat(join(output, u.id, "worker.env"))).mode & 0o777,
      0o600,
    );
    await assert.rejects(lstat(join(output, u.id, "evidence")), {
      code: "ENOENT",
    });
    const manifest = JSON.parse(
      await readFile(join(output, u.id, "preparation.json"), "utf8"),
    );
    const job = (
      await pool.query(
        "SELECT * FROM profile_preparation_jobs WHERE owner_id=$1",
        [u.id],
      )
    ).rows[0];
    assert.equal(manifest.requestId, job.request_id);
    assert.equal(manifest.status, "prepared_unverified");
  }
  assert.equal(keys.size, 9);
  assert.ok(
    (await pool.query("SELECT state FROM profile_instances")).rows.every(
      (p) => p.state === "unprovisioned",
    ),
  );
  assert.deepEqual(await preparer.once(), []);
});

test("manual reservations exclude overlapping automatic triples; exhaustion never recycles another owner's ports", async (t) => {
  const { pool, root, preparer, user } = await setup(t, 31008);
  const manual = await user();
  await prepareProfiles(pool, manual.id, settings(31001), join(root, "manual"));
  const a = await user(),
    b = await user();
  assert.equal((await preparer.prepare(manual.id)).status, "manual");
  assert.equal((await preparer.prepare(a.id)).status, "prepared");
  const result = await preparer.prepare(b.id);
  assert.equal(result.status, "blocked");
  assert.equal(
    "errorCode" in result && result.errorCode,
    "port_pool_exhausted",
  );
  assert.deepEqual(
    (
      await pool.query(
        "SELECT port FROM profile_api_ports WHERE owner_id=$1 ORDER BY port",
        [a.id],
      )
    ).rows.map((p) => p.port),
    [31006, 31007, 31008],
  );
  assert.equal(
    (
      await pool.query(
        "SELECT count(*) FROM profile_api_ports WHERE owner_id=$1",
        [b.id],
      )
    ).rows[0].count,
    "0",
  );
  assert.deepEqual(await preparer.once(), []);
  const conflict = await user();
  await assert.rejects(
    prepareProfiles(pool, conflict.id, settings(31002), join(root, "manual")),
    { code: "23505" },
  );
  assert.equal(
    (
      await pool.query(
        "SELECT status FROM profile_preparation_jobs WHERE owner_id=$1",
        [conflict.id],
      )
    ).rows[0].status,
    "queued",
  );
});

test("immutable policy rejects changed roots, model credentials and port pools", async (t) => {
  const { pool, root, output, preparer, user } = await setup(t);
  const u = await user();
  await preparer.prepare(u.id);
  const original = await readFile(join(output, u.id, "worker.env"), "utf8");
  await assert.rejects(
    AutoProfilePreparer.open(pool, settings(), join(root, "other"), 31020),
    /preparation_policy_changed/,
  );
  await assert.rejects(
    AutoProfilePreparer.open(pool, settings(), output, 31023),
    /preparation_policy_changed/,
  );
  const changed = settings();
  changed.models.research!.apiKey = "changed-test-credential";
  await assert.rejects(
    AutoProfilePreparer.open(pool, changed, output, 31020),
    /preparation_policy_changed/,
  );
  await assert.rejects(
    prepareProfiles(pool, (await user()).id, settings(32000), output),
    /automatic_preparation_root/,
  );
  assert.equal(
    await readFile(join(output, u.id, "worker.env"), "utf8"),
    original,
  );
  await assert.rejects(
    pool.query("UPDATE profile_preparation_policy SET last_port=32000"),
  );
  await assert.rejects(pool.query("DELETE FROM profile_api_ports"));
  await assert.rejects(
    pool.query(
      "UPDATE profile_preparation_jobs SET first_port=32000 WHERE owner_id=$1",
      [u.id],
    ),
    /immutable_preparation_binding/,
  );
});

test("policy versions seed the legacy row and select the latest generation", async (t) => {
  const { pool, output, preparer, user } = await setup(t);
  const u = await user();
  await preparer.prepare(u.id);
  assert.deepEqual(
    (
      await pool.query(
        "SELECT settings_hash FROM profile_preparation_policy_versions ORDER BY generation",
      )
    ).rows.map((r) => r.settings_hash),
    [
      (
        await pool.query(
          "SELECT settings_hash FROM profile_preparation_policy",
        )
      ).rows[0].settings_hash,
    ],
  );
  const next = provisionSettings.parse(settings());
  next.models.research.name = "next-model";
  next.models.prediction.name = "next-model";
  next.models.portfolio.name = "next-model";
  const rootIdentity = await preparationRootIdentity(output);
  const nextHash = preparationSettingsHash(next, 31020, rootIdentity);
  await pool.query(
    "INSERT INTO profile_preparation_policy_versions(root_identity,settings_hash,first_port,last_port) VALUES($1,$2,$3,$4)",
    [rootIdentity, nextHash, 31000, 31020],
  );
  const reopened = await AutoProfilePreparer.open(pool, next, output, 31020);
  assert.equal(reopened.root, output);
  await assert.rejects(
    AutoProfilePreparer.open(pool, settings(), output, 31020),
    /preparation_policy_changed/,
  );
  await assert.rejects(
    pool.query("UPDATE profile_preparation_policy_versions SET last_port=32000"),
  );
  await assert.rejects(
    pool.query("DELETE FROM profile_preparation_policy_versions"),
  );
  await assert.rejects(
    pool.query("TRUNCATE profile_preparation_policy_versions"),
  );
});

test("published-file drift blocks recovery without rewriting credentials; explicit retry adopts only exact originals", async (t) => {
  const { pool, output, preparer, user } = await setup(t);
  const u = await user();
  await pool.query(`CREATE FUNCTION fail_prepared() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN IF NEW.status='prepared' THEN RAISE EXCEPTION 'test_commit_failure'; END IF; RETURN NEW; END $$;
    CREATE TRIGGER fail_prepared BEFORE UPDATE ON profile_preparation_jobs FOR EACH ROW EXECUTE FUNCTION fail_prepared()`);
  assert.equal((await preparer.prepare(u.id)).status, "blocked");
  const file = join(output, u.id, "worker.env"),
    original = await readFile(file, "utf8");
  await pool.query("DROP TRIGGER fail_prepared ON profile_preparation_jobs");
  const seedName = (await readdir(join(output, ".auto-seeds")))[0]!;
  const seedPath = join(output, ".auto-seeds", seedName),
    seedContent = await readFile(seedPath, "utf8");
  await rm(seedPath);
  assert.equal((await preparer.prepare(u.id, true)).status, "blocked");
  await assert.rejects(lstat(seedPath), { code: "ENOENT" });
  assert.equal(await readFile(file, "utf8"), original);
  await writeFile(seedPath, seedContent, { mode: 0o600 });

  await writeFile(file, "changed\n");
  assert.equal((await preparer.prepare(u.id, true)).status, "blocked");
  assert.equal(await readFile(file, "utf8"), "changed\n");
  await writeFile(file, original);
  await chmod(file, 0o644);
  assert.equal((await preparer.prepare(u.id, true)).status, "blocked");
  await chmod(file, 0o600);
  assert.equal((await preparer.prepare(u.id, true)).status, "prepared");
  assert.equal(await readFile(file, "utf8"), original);
  assert.equal(
    (
      await pool.query(
        "SELECT count(*) FROM profile_api_ports WHERE owner_id=$1",
        [u.id],
      )
    ).rows[0].count,
    "3",
  );
});

test("a symlink output and a changed root marker fail closed", async (t) => {
  const { root, output, preparer, user } = await setup(t);
  const u = await user();
  const foreign = join(root, "foreign");
  await writeFile(foreign, "keep me", { mode: 0o600 });
  await symlink(foreign, join(output, u.id));
  assert.equal((await preparer.prepare(u.id)).status, "blocked");
  assert.equal(await readFile(foreign, "utf8"), "keep me");
  const b = await user();
  await writeFile(
    join(output, ".auto-preparer.json"),
    JSON.stringify({ id: randomUUID() }),
  );
  assert.equal((await preparer.prepare(b.id)).status, "blocked");
  await assert.rejects(lstat(join(output, b.id)), { code: "ENOENT" });
});

test("actual preparer SIGKILL after publication resumes in a new process with the original files, keys and ports", async (t) => {
  const { pool, schema, root, output, user, connection } = await setup(t);
  const u = await user();
  const policy = join(root, "policy.json");
  await writeFile(policy, JSON.stringify(settings()), { mode: 0o600 });
  const blocker = await pool.connect();
  await blocker.query("SELECT pg_advisory_lock(hashtext($1),9281)", [schema]);
  let held = true;
  await pool.query(`CREATE FUNCTION hold_prepared() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN IF NEW.status='prepared' THEN PERFORM pg_advisory_xact_lock(hashtext(TG_TABLE_SCHEMA),9281); END IF; RETURN NEW; END $$;
    CREATE TRIGGER hold_prepared BEFORE UPDATE ON profile_preparation_jobs FOR EACH ROW EXECUTE FUNCTION hold_prepared()`);
  const appName = `prep-${randomUUID()}`;
  connection.searchParams.set("application_name", appName);
  const children: ReturnType<typeof spawn>[] = [];
  const run = () => {
    const child = spawn(
      process.execPath,
      [
        "--import",
        "tsx",
        resolve("scripts/prepare-profiles-auto.ts"),
        policy,
        output,
        "31020",
        "--once",
      ],
      {
        detached: true,
        stdio: ["ignore", "pipe", "pipe"],
        env: { PATH: process.env.PATH, DATABASE_URL: connection.toString() },
      },
    );
    children.push(child);
    let stdout = "",
      stderr = "";
    child.stdout!.on("data", (data) => {
      stdout += data;
    });
    child.stderr!.on("data", (data) => {
      stderr += data;
    });
    return {
      child,
      exit: once(child, "close"),
      logs: () => ({ stdout, stderr }),
    };
  };
  try {
    const first = run();
    await until(async () =>
      Boolean(
        (
          await pool.query(
            "SELECT 1 FROM pg_stat_activity WHERE application_name=$1 AND wait_event='advisory'",
            [appName],
          )
        ).rowCount,
      ),
    );
    const file = join(output, u.id, "worker.env"),
      original = await readFile(file, "utf8");
    const ports = (
      await pool.query(
        "SELECT * FROM profile_api_ports WHERE owner_id=$1 ORDER BY port",
        [u.id],
      )
    ).rows;
    const seedNames = await readdir(join(output, ".auto-seeds"));
    const seed = await readFile(
      join(output, ".auto-seeds", seedNames[0]!),
      "utf8",
    );
    assert.equal(
      (
        await pool.query(
          "SELECT status FROM profile_preparation_jobs WHERE owner_id=$1",
          [u.id],
        )
      ).rows[0].status,
      "preparing",
    );
    process.kill(-first.child.pid!, "SIGKILL");
    const [, signal] = await first.exit;
    assert.equal(signal, "SIGKILL");
    // Killing a client does not prove its in-flight SQL rolled back. Terminate
    // only this test child's blocked DB backend before releasing the barrier,
    // then verify preparing is durable so the new process must do recovery.
    await pool.query(
      "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE application_name=$1",
      [appName],
    );
    await until(
      async () =>
        !(
          await pool.query(
            "SELECT 1 FROM pg_stat_activity WHERE application_name=$1",
            [appName],
          )
        ).rowCount,
    );
    assert.equal(
      (
        await pool.query(
          "SELECT status FROM profile_preparation_jobs WHERE owner_id=$1",
          [u.id],
        )
      ).rows[0].status,
      "preparing",
    );

    await blocker.query("SELECT pg_advisory_unlock(hashtext($1),9281)", [
      schema,
    ]);
    held = false;
    const second = run();
    assert.notEqual(second.child.pid, first.child.pid);
    const [code] = await second.exit;
    assert.equal(code, 0, second.logs().stderr);
    assert.match(second.logs().stdout, /"status":"prepared"/);
    assert.equal(await readFile(file, "utf8"), original);
    assert.equal(
      await readFile(join(output, ".auto-seeds", seedNames[0]!), "utf8"),
      seed,
    );
    assert.deepEqual(
      (
        await pool.query(
          "SELECT * FROM profile_api_ports WHERE owner_id=$1 ORDER BY port",
          [u.id],
        )
      ).rows,
      ports,
    );
    assert.equal(
      (
        await pool.query(
          "SELECT status FROM profile_preparation_jobs WHERE owner_id=$1",
          [u.id],
        )
      ).rows[0].status,
      "prepared",
    );
    assert.ok(
      (
        await pool.query(
          "SELECT state FROM profile_instances WHERE owner_id=$1",
          [u.id],
        )
      ).rows.every((p) => p.state === "unprovisioned"),
    );
  } finally {
    for (const child of children)
      if (child.exitCode === null && child.signalCode === null) {
        const exited = once(child, "exit");
        process.kill(-child.pid!, "SIGKILL");
        await exited;
      }
    if (held)
      await blocker.query("SELECT pg_advisory_unlock(hashtext($1),9281)", [
        schema,
      ]);
    blocker.release();
  }
});
