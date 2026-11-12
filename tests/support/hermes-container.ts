// Opt-in Docker test preparation only. No passing production registry is written.
import assert from "node:assert/strict";
import { createHash, randomBytes, randomUUID } from "node:crypto";
import { lstat, readFile } from "node:fs/promises";
import { join } from "node:path";
import { createInterface } from "node:readline";
import { Pool } from "pg";
import { AuthStore } from "../../packages/server/src/auth.js";
import { migrate } from "../../packages/server/src/migrate.js";
import { prepareProfiles } from "../../packages/hermes-adapter/src/provision.js";
import {
  DockerProfileDriver,
  type Deployment,
} from "../../packages/hermes-adapter/src/lifecycle.js";
import { HermesClient } from "../../packages/hermes-adapter/src/client.js";
import { ProfileSupervisor } from "../../packages/server/src/profile-supervisor.js";
import { TaskStore } from "../../packages/server/src/tasks.js";
import { TaskWorker } from "../../packages/server/src/worker.js";
import { ToolCapabilities } from "../../packages/server/src/tool-context.js";
import { RuntimeRecovery } from "../../packages/server/src/runtime-recovery.js";
const url = process.env.TEST_DATABASE_URL;
if (!url || !new URL(url).pathname.endsWith("_test"))
  throw new Error("Dedicated test database required");
const input = createInterface({ input: process.stdin });
const lines = input[Symbol.asyncIterator]();
const configuration = JSON.parse((await lines.next()).value!);
let providerModel: unknown;
if (configuration.modelFile) {
  const file = await lstat(configuration.modelFile);
  assert.ok(file.isFile() && !file.isSymbolicLink() && !(file.mode & 0o077));
  providerModel = JSON.parse(await readFile(configuration.modelFile, "utf8"));
}
const schema = `test_${randomUUID().replaceAll("-", "")}`;
const admin = new Pool({ connectionString: url });
const pool = new Pool({
  connectionString: url,
  options: `-c search_path=${schema}`,
});
let created = false;
process.once("SIGTERM", () => input.close());
try {
  await admin.query(`CREATE SCHEMA ${schema}`);
  created = true;
  await migrate(pool);
  const prepared = [];
  for (const firstPort of configuration.ports) {
    const owner = await new AuthStore(pool).register(
      `${randomUUID()}@test.invalid`,
      "test-password-long",
    );
    const settings = {
      image: configuration.image,
      firstPort,
      gatewayOrigin: configuration.peerOrigin,
      models: Object.fromEntries(
        ["research", "prediction", "portfolio"].map((role) => [
          role,
          providerModel ?? {
            name: "alpha-fixture",
            baseUrl: configuration.peerOrigin + "/v1",
            apiMode: "chat_completions",
            apiKey: "test-only-model-key-not-a-provider-secret",
          },
        ]),
      ),
    };
    prepared.push({
      ownerId: owner.id,
      ...(await prepareProfiles(
        pool,
        owner.id,
        settings,
        configuration.outputRoot,
      )),
    });
  }
  console.log(JSON.stringify({ prepared }));
  // Build test bindings from the actual prepared private files. This fixture
  // exercises the production driver and controller, not loadRegistry's separate
  // provider-evidence gate; it never writes a passing registry/report.
  const digest = (value: string) =>
    createHash("sha256").update(value).digest("hex");
  const deployments: Deployment[] = [];
  for (const owner of prepared) {
    const composeRaw = await readFile(owner.composeFile, "utf8");
    const compose = JSON.parse(composeRaw);
    const preparation = JSON.parse(
      await readFile(join(owner.directory, "preparation.json"), "utf8"),
    );
    for (const profile of preparation.profiles) {
      const base = join(owner.directory, "profiles", profile.id);
      const home = join(base, "home"),
        spec = join(base, "runtime.json"),
        secret = join(base, "credentials.json");
      const homeStat = await lstat(home, { bigint: true });
      const homeIdentity = `${homeStat.dev}:${homeStat.ino}`;
      const secretRaw = await readFile(secret, "utf8");
      const service = `profile-${profile.id}`;
      const origin = `http://127.0.0.1:${compose.services[service].ports[0].published}`;
      const files = {
        [owner.composeFile]: digest(composeRaw),
        [spec]: digest(await readFile(spec, "utf8")),
        [secret]: digest(secretRaw),
        [join(home, ".alpha-profile.json")]: digest(
          await readFile(join(home, ".alpha-profile.json"), "utf8"),
        ),
      };
      deployments.push({
        ownerId: owner.ownerId,
        profileId: profile.id,
        role: profile.role,
        composeFile: owner.composeFile,
        service,
        container: compose.services[service].container_name,
        image: configuration.image,
        origin,
        home,
        homeIdentity,
        spec,
        secret,
        network: `${compose.name}_${service}`,
        files,
        deploymentHash: digest(JSON.stringify({ files, homeIdentity })),
        runtime: new HermesClient(
          origin,
          JSON.parse(secretRaw).apiKey,
          configuration.image,
          60_000,
        ),
      });
    }
  }
  const driver = new DockerProfileDriver(deployments);
  const supervisor = new ProfileSupervisor(pool, driver, 1000);
  for await (const line of { [Symbol.asyncIterator]: () => lines }) {
    const request = JSON.parse(line);
    if (request.action === "crash-recovery") {
      const p = deployments.find((d) => d.profileId === request.profileId)!;
      assert.ok(p);
      const tasks = new TaskStore(pool);
      const thread = await tasks.createThread(p.ownerId);
      const task = await tasks.submit(p.ownerId, thread.id, randomUUID(), {
        input: "Hold the model response for the container crash fixture.",
      });
      await supervisor.tick(p);
      const worker = new TaskWorker(
        tasks,
        (id) => (id === p.profileId ? p.runtime : undefined),
        new ToolCapabilities(pool, randomBytes(32).toString("hex")),
      );
      await worker.once(p.ownerId);
      const original = (await tasks.execution(p.ownerId, task.id))!;
      assert.ok(original.upstream_run_id);
      console.log(JSON.stringify({ crash: "admitted" }));
      const signal = await lines.next();
      assert.equal(signal.done, false);
      assert.equal(JSON.parse(signal.value!).crashed, true);
      const stopped = (await driver.inspect(p))!;
      assert.equal(stopped.status, "stopped");
      // Outstanding execution must prevent the normal supervisor from starting
      // even the original container. A transport failure is not an exit receipt.
      await supervisor.tick(p);
      await supervisor.tick(p);
      assert.equal((await driver.inspect(p))?.status, "stopped");
      assert.equal(
        (
          await pool.query("SELECT state FROM profile_instances WHERE id=$1", [
            p.profileId,
          ])
        ).rows[0].state,
        "unknown",
      );
      await worker.once(p.ownerId);
      assert.equal((await tasks.get(p.ownerId, task.id)).status, "unknown");
      const recovery = new RuntimeRecovery(pool, driver);
      await assert.rejects(
        recovery.interruptUnknown(p, task.id, p.runtime),
        /recovery_runtime_unavailable/,
      );
      // Explicit operator restoration of the original API is a prerequisite
      // of today's recovery interface, NOT an automatic supervisor transition.
      await driver.start(p, stopped.id);
      const deadline = Date.now() + 45_000;
      while (!(await driver.healthy(p).catch(() => false))) {
        assert.ok(Date.now() < deadline, "Restored API did not become ready");
        await new Promise((resolve) => setTimeout(resolve, 250));
      }
      assert.equal(
        (await p.runtime.get(original.upstream_run_id!, original.session_id))
          .status,
        "unknown",
      );
      const resolved = (await recovery.interruptUnknown(
        p,
        task.id,
        p.runtime,
      ))!;
      assert.equal(resolved.phase, "resolved");
      assert.equal(resolved.native_receipt!.status, "cancelled");
      assert.equal((await tasks.get(p.ownerId, task.id)).status, "unknown");
      assert.equal(
        (await tasks.execution(p.ownerId, task.id))!.terminal_receipt,
        null,
      );
      await pool.query("UPDATE tasks SET next_attempt_at=now() WHERE id=$1", [
        task.id,
      ]);
      await worker.once(p.ownerId);
      assert.equal((await tasks.get(p.ownerId, task.id)).status, "interrupted");
      const evidence = (
        await pool.query("SELECT * FROM executions WHERE task_id=$1", [task.id])
      ).rows[0];
      assert.equal(evidence.upstream_run_id, original.upstream_run_id);
      assert.equal(evidence.idempotency_key, original.idempotency_key);
      assert.equal(evidence.runtime_container_id, stopped.id);
      assert.equal(evidence.terminal_receipt.status, "cancelled");
      assert.equal(evidence.tool_token_hash, null);
      assert.equal(evidence.event_gap, true);
      const audit = (await tasks.audit(p.ownerId, task.id)).items.filter(
        (entry) => entry.recovery_id === resolved.id,
      );
      assert.equal(audit.length, 6);
      assert.equal(
        (
          await pool.query("SELECT 1 FROM tool_operations WHERE task_id=$1", [
            task.id,
          ])
        ).rowCount,
        0,
      );
      assert.equal(
        (await recovery.interruptUnknown(p, task.id, p.runtime))!.id,
        resolved.id,
      );
      console.log(
        JSON.stringify({
          crashRecovery: "passed",
          taskStatus: "interrupted",
          auditSteps: audit.length,
        }),
      );
      continue;
    }
    assert.equal(request.action, "lifecycle");
    for (const p of deployments) {
      assert.equal((await driver.inspect(p))?.status, "running");
      assert.equal(await driver.healthy(p), true);
    }
    const p = deployments.find((d) => d.profileId === request.profileId)!;
    assert.ok(p);
    const before = (await driver.inspect(p))!;
    const state = async () =>
      (
        await pool.query(
          "SELECT state,container_id,container_started_at,lifecycle_error FROM profile_instances WHERE id=$1",
          [p.profileId],
        )
      ).rows[0];
    // Accelerated test idle threshold; no production timeout is changed.
    await new Promise((resolve) => setTimeout(resolve, 1100));
    await supervisor.tick(p);
    assert.equal(
      (await state()).state,
      "stopped",
      JSON.stringify(await state()),
    );
    assert.equal((await driver.inspect(p))?.status, "stopped");
    const tasks = new TaskStore(pool);
    const thread = await tasks.createThread(p.ownerId);
    const task = await tasks.submit(p.ownerId, thread.id, randomUUID(), {
      input: "lifecycle demand fixture",
    });
    const deadline = Date.now() + 45_000;
    while (Date.now() < deadline) {
      await supervisor.tick(p);
      if ((await state()).state === "ready") break;
      await new Promise((resolve) => setTimeout(resolve, 250));
    }
    const ready = await state();
    assert.equal(ready.state, "ready", JSON.stringify(ready));
    assert.equal(ready.container_id, before.id);
    assert.notEqual(ready.container_started_at, before.startedAt);
    assert.equal(ready.lifecycle_error, null);
    // This queued fixture only creates demand; it is never submitted to a run.
    await tasks.stop(p.ownerId, task.id);
    for (const other of deployments.filter((d) => d.profileId !== p.profileId))
      assert.equal((await driver.inspect(other))?.status, "running");
    console.log(
      JSON.stringify({ lifecycle: "passed", containerId: ready.container_id }),
    );
  }
} finally {
  input.close();
  await pool.end();
  if (created) await admin.query(`DROP SCHEMA ${schema} CASCADE`);
  await admin.end();
}
