// Opt-in PG + real Hermes recovery peer. The Python parent controls only its
// own gateway process group; this is not a Docker isolation certificate.
import assert from "node:assert/strict";
import { randomBytes, randomUUID } from "node:crypto";
import { createInterface } from "node:readline";
import { Pool } from "pg";
import { HermesClient } from "../../packages/hermes-adapter/src/client.js";
import { AuthStore } from "../../packages/server/src/auth.js";
import { migrate } from "../../packages/server/src/migrate.js";
import {
  ProfileSupervisor,
  type ProfileDriver,
} from "../../packages/server/src/profile-supervisor.js";
import { RuntimeRecovery } from "../../packages/server/src/runtime-recovery.js";
import { TaskStore } from "../../packages/server/src/tasks.js";
import { TaskWorker } from "../../packages/server/src/worker.js";
import { ToolCapabilities } from "../../packages/server/src/tool-context.js";

const url = process.env.TEST_DATABASE_URL;
if (!url || !new URL(url).pathname.endsWith("_test"))
  throw new Error("Dedicated test database required");
const schema = `test_${randomUUID().replaceAll("-", "")}`;
const admin = new Pool({ connectionString: url }),
  pool = new Pool({
    connectionString: url,
    options: `-c search_path=${schema}`,
  });
const input = createInterface({ input: process.stdin }),
  lines = input[Symbol.asyncIterator]();
process.once("SIGTERM", () => input.close());
async function receive() {
  const line = await lines.next();
  if (line.done) throw new Error("Process controller disconnected");
  return JSON.parse(line.value);
}
async function control(command: string) {
  console.log(JSON.stringify({ command }));
  const response = await receive();
  assert.equal(response.command, command);
  return response.value;
}
let created = false;
try {
  await admin.query(`CREATE SCHEMA ${schema}`);
  created = true;
  await migrate(pool);
  const owner = (
    await new AuthStore(pool).register(
      `${randomUUID()}@recovery.test`,
      "test-password-long",
    )
  ).id;
  const profileId = (
    await pool.query(
      "SELECT id FROM profile_instances WHERE owner_id=$1 AND role='research'",
      [owner],
    )
  ).rows[0].id;
  console.log(JSON.stringify({ owner, profileId }));
  const binding = await receive();
  assert.equal(new URL(binding.origin).hostname, "127.0.0.1");
  const runtime = new HermesClient(
    binding.origin,
    binding.token,
    "test-source-process",
    3600000,
  );
  const profile = {
    ownerId: owner,
    profileId,
    role: "research" as const,
    deploymentHash: "test-source-process",
  };
  const driver: ProfileDriver = {
    async inspect() {
      return control("inspect");
    },
    async start() {
      await control("start");
    },
    async stop() {
      await control("stop");
    },
    async healthy() {
      return control("healthy");
    },
  };
  await new ProfileSupervisor(pool, driver).tick(profile);
  const store = new TaskStore(pool),
    thread = await store.createThread(owner);
  const task = await store.submit(owner, thread.id, randomUUID(), {
    input: "Use get_workspace once and report the result.",
  });
  const worker = new TaskWorker(
    store,
    () => runtime,
    new ToolCapabilities(pool, randomBytes(32).toString("hex")),
  );
  await worker.once(owner);
  const original = (await store.execution(owner, task.id))!;
  assert.ok(original.upstream_run_id);
  await control("crash-and-restart");
  await worker.once(owner);
  assert.equal((await store.get(owner, task.id)).status, "unknown");
  const recovery = (await new RuntimeRecovery(pool, driver).interruptUnknown(
    profile,
    task.id,
    runtime,
  ))!;
  assert.equal(recovery.phase, "resolved");
  assert.equal(recovery.native_receipt!.status, "cancelled");
  assert.equal((await store.execution(owner, task.id))!.terminal_receipt, null);
  assert.equal((await store.get(owner, task.id)).status, "unknown");
  await pool.query("UPDATE tasks SET next_attempt_at=now() WHERE id=$1", [
    task.id,
  ]);
  await worker.once(owner);
  assert.equal((await store.get(owner, task.id)).status, "interrupted");
  const evidence = (
    await pool.query("SELECT * FROM executions WHERE task_id=$1", [task.id])
  ).rows[0];
  assert.equal(evidence.upstream_run_id, original.upstream_run_id);
  assert.equal(evidence.idempotency_key, original.idempotency_key);
  assert.equal(evidence.terminal_receipt.status, "cancelled");
  assert.equal(evidence.tool_token_hash, null);
  assert.equal(
    evidence.event_gap,
    true,
    "lost native stream is recorded, never fabricated",
  );
  const audit = (await store.audit(owner, task.id)).items.filter(
    (e) => e.recovery_id === recovery.id,
  );
  assert.equal(audit.length, 6);
  assert.equal((await pool.query("SELECT 1 FROM tool_operations")).rowCount, 0);
  assert.equal(
    (await pool.query("SELECT 1 FROM runtime_recoveries")).rowCount,
    1,
  );
  assert.equal(
    (await new RuntimeRecovery(pool, driver).interruptUnknown(
      profile,
      task.id,
      runtime,
    ))!.id,
    recovery.id,
  );
  console.log(
    JSON.stringify({
      passed: true,
      phase: recovery.phase,
      taskStatus: "interrupted",
      auditSteps: audit.length,
    }),
  );
} finally {
  input.close();
  await pool.end();
  if (created) await admin.query(`DROP SCHEMA ${schema} CASCADE`);
  await admin.end();
}
