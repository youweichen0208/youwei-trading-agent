import { ToolCapabilities } from "../../packages/server/src/tool-context.js";
import { after, before, test } from "node:test";
import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { createServer } from "node:http";
import { spawn } from "node:child_process";
import { setTimeout as delay } from "node:timers/promises";
import type { AddressInfo } from "node:net";
import { Pool } from "pg";
import { migrate } from "../../packages/server/src/migrate.js";
import { TaskStore } from "../../packages/server/src/tasks.js";
import { TaskWorker } from "../../packages/server/src/worker.js";
import {
  HermesClient,
  type Runtime,
  type RunRequest,
  type Run,
} from "../../packages/hermes-adapter/src/client.js";

const url = process.env.TEST_DATABASE_URL;
if (!url || !new URL(url).pathname.endsWith("_test"))
  throw new Error("Dedicated TEST_DATABASE_URL ending in _test required");
const schema = `test_${randomUUID().replaceAll("-", "")}`;
const admin = new Pool({ connectionString: url });
const pool = new Pool({
  connectionString: url,
  options: `-c search_path=${schema}`,
});
const store = new TaskStore(pool);
before(async () => {
  await admin.query(`CREATE SCHEMA ${schema}`);
  await migrate(pool);
});
after(async () => {
  await pool.end();
  await admin.query(`DROP SCHEMA ${schema} CASCADE`);
  await admin.end();
});
class SimulatedRuntime implements Runtime {
  identity = "test-runtime";
  retentionMs = 86_400_000;
  calls: string[] = [];
  stops = 0;
  loseResponse = false;
  runs = new Map<string, Run>();
  async create(request: RunRequest, key: string) {
    this.calls.push(key);
    if (!this.runs.has(key))
      this.runs.set(key, {
        run_id: `run_${key}`,
        session_id: request.session_id,
        status: "running",
      });
    if (this.loseResponse) {
      this.loseResponse = false;
      throw new Error("connection_lost_after_commit");
    }
    return this.runs.get(key)!.run_id;
  }
  async get(id: string, session: string) {
    const run = [...this.runs.values()].find((r) => r.run_id === id)!;
    assert.equal(run.session_id, session);
    return { ...run };
  }
  async stop(id: string) {
    this.stops++;
    const run = [...this.runs.values()].find((r) => r.run_id === id)!;
    run.status = "stopping";
  }
  complete(status: Run["status"] = "completed") {
    for (const run of this.runs.values()) {
      run.status = status;
      run.output = "Durable final answer";
    }
  }
}
async function setup(configured = true) {
  const owner = randomUUID();
  await pool.query(
    "INSERT INTO users(id,email,password_hash) VALUES($1,$2,'test')",
    [owner, `${owner}@test.invalid`],
  );
  const profileIds: string[] = [];
  for (const role of ["research", "prediction", "portfolio"]) {
    const id = randomUUID();
    profileIds.push(id);
    await pool.query(
      "INSERT INTO profile_instances(id,owner_id,role) VALUES($1,$2,$3)",
      [id, owner, role],
    );
  }
  const thread = await store.createThread(owner);
  const task = await store.submit(owner, thread.id, randomUUID(), {
    input: "research",
  });
  const runtime = new SimulatedRuntime();
  const resolver = (id: string) =>
    configured && profileIds.includes(id) ? runtime : undefined;
  const worker = new TaskWorker(store, resolver);
  const step = async (w = worker) => {
    await pool.query(
      "UPDATE tasks SET next_attempt_at=next_attempt_at-interval '1 day' WHERE owner_id=$1",
      [owner],
    );
    return w.once(owner);
  };
  return { owner, thread, task, runtime, worker, step, resolver };
}

test("unconfigured profiles fail before submission rather than fabricate a response", async () => {
  const a = await setup(false);
  await a.step();
  const t = await store.get(a.owner, a.task.id);
  assert.equal(t.status, "failed");
  assert.equal((t.output as { reason: string }).reason, "profile_unavailable");
  assert.equal(await store.execution(a.owner, t.id), undefined);
});

test("event cursor, partial snapshot and app events commit atomically and fence stale collectors", async () => {
  const a = await setup();
  await a.step();
  const t = (await store.take(a.owner, a.worker.id))!;
  const e = (await store.execution(a.owner, t.id))!;
  const append = (
    events: { sequence: number; kind: string; text?: string; tool?: string }[],
    generation = t.generation,
  ) =>
    store.archiveEvents(
      a.owner,
      t.id,
      a.worker.id,
      generation,
      e.upstream_run_id!,
      {
        events,
        gap: false,
        closed: false,
        limited: false,
      },
    );
  await append([{ sequence: 0, kind: "message.delta", text: "Saved prefix" }]);
  const first = await store.snapshot(a.owner, t.id);
  assert.equal(first.task.stream_text, "Saved prefix");
  await append([
    { sequence: 0, kind: "message.delta", text: "must not duplicate" },
  ]);
  assert.equal(
    (await store.snapshot(a.owner, t.id)).lastSequence,
    first.lastSequence,
  );
  await assert.rejects(
    append(
      [{ sequence: 1, kind: "message.delta", text: "old" }],
      t.generation - 1,
    ),
    /stale_lease/,
  );
  await assert.rejects(
    append([
      { sequence: 1, kind: "message.delta", text: "must roll back" },
      { sequence: -1, kind: "message.delta", text: "invalid" },
    ]),
    /invalid_event_cursor/,
  );
  assert.equal((await store.execution(a.owner, t.id))!.event_cursor, "0");
  assert.equal(
    (await store.snapshot(a.owner, t.id)).task.stream_text,
    "Saved prefix",
  );
  await append([
    { sequence: 4, kind: "message.delta", text: "missing middle" },
    { sequence: 5, kind: "tool.completed", tool: "market_history" },
  ]);
  assert.equal((await store.execution(a.owner, t.id))!.event_gap, true);
  assert.equal(
    (await store.snapshot(a.owner, t.id)).task.stream_text,
    "Saved prefix",
  );
  await pool.query("DELETE FROM task_events WHERE task_id=$1", [t.id]);
  assert.equal(
    (await store.snapshot(a.owner, t.id)).task.stream_text,
    "Saved prefix",
  );
  await store.defer(
    a.owner,
    t.id,
    a.worker.id,
    t.generation,
    "running",
    null,
    0,
  );
  a.runtime.complete();
  await a.step();
  const final = await store.snapshot(a.owner, t.id);
  assert.equal(final.task.status, "completed");
  assert.equal(final.messages.at(-1).content, "Durable final answer");
  assert.equal(final.messages.at(-1).id, first.task.stream_message_id);
});

test("terminal receipt survives event backlog draining and event loss does not rerun work", async () => {
  const a = await setup();
  await a.step();
  a.runtime.complete();
  const runtime: Runtime = a.runtime;
  const cursors: number[] = [];
  runtime.events = async (_id, after) => {
    cursors.push(after);
    if (after === -1)
      return {
        events: Array.from({ length: 256 }, (_, sequence) => ({
          sequence,
          kind: "run.running",
        })),
        gap: false,
        closed: false,
        limited: true,
      };
    throw new Error("upstream event history gone after restart");
  };
  await a.step();
  assert.equal(
    (await store.execution(a.owner, a.task.id))!.terminal_receipt!.status,
    "completed",
  );
  assert.equal((await store.get(a.owner, a.task.id)).status, "running");
  a.runtime.get = async () => {
    throw new Error("terminal status retention expired");
  };
  await a.step(new TaskWorker(store, a.resolver));
  assert.equal((await store.get(a.owner, a.task.id)).status, "completed");
  assert.deepEqual(cursors, [-1, 255]);
  assert.equal(a.runtime.calls.length, 1);
  assert.equal((await store.execution(a.owner, a.task.id))!.event_gap, true);
});

test("lost create response is recovered using the same durable identity, then archived without browser", async () => {
  const a = await setup();
  a.runtime.loseResponse = true;
  await a.step();
  assert.equal((await store.get(a.owner, a.task.id)).status, "unknown");
  const restarted = new TaskWorker(store, a.resolver);
  await a.step(restarted);
  assert.equal(a.runtime.calls.length, 2);
  assert.equal(new Set(a.runtime.calls).size, 1);
  assert.equal(a.runtime.runs.size, 1);
  a.runtime.complete();
  await a.step(restarted);
  assert.equal((await store.get(a.owner, a.task.id)).status, "completed");
  const snapshot = await store.snapshot(a.owner, a.task.id);
  assert.equal(snapshot.messages.at(-1).content, "Durable final answer");
  await pool.query("DELETE FROM task_events WHERE task_id=$1", [a.task.id]);
  assert.deepEqual(await store.snapshot(a.owner, a.task.id), snapshot);
});

test("stop acknowledgement does not release profile until upstream terminal evidence", async () => {
  const a = await setup();
  await a.step();
  await store.stop(a.owner, a.task.id);
  await a.step();
  assert.equal(a.runtime.stops, 1);
  assert.equal((await store.get(a.owner, a.task.id)).status, "stopping");
  const next = await store.submit(a.owner, a.thread.id, "next", {
    input: "next",
  });
  await a.step();
  assert.equal((await store.get(a.owner, next.id)).status, "queued");
  a.runtime.complete("cancelled");
  await a.step();
  assert.equal((await store.get(a.owner, a.task.id)).status, "cancelled");
  await a.step();
  assert.equal((await store.get(a.owner, next.id)).status, "running");
});

test("expired retry window and changed runtime identity cannot create a replacement run", async () => {
  const a = await setup();
  a.runtime.loseResponse = true;
  await a.step();
  await pool.query(
    "UPDATE executions SET submission_started_at=now()-interval '25 hours' WHERE task_id=$1",
    [a.task.id],
  );
  await a.step();
  assert.equal(a.runtime.calls.length, 1);
  assert.equal((await store.get(a.owner, a.task.id)).status, "unknown");
  a.runtime.identity = "different-credential-or-image";
  await a.step();
  assert.equal(a.runtime.calls.length, 1);
});

test("stopped prepared-but-unsent execution never sends its first request", async () => {
  const a = await setup();
  const w = randomUUID();
  const t = (await store.take(a.owner, w))!;
  const ctx = await store.executionContext(a.owner, t.id, w, t.generation);
  await store.prepareExecution(
    a.owner,
    t.id,
    w,
    t.generation,
    { input: "x", session_id: ctx.sessionId, instructions: "test" },
    ctx.profileId,
    ctx.sessionId,
  );
  await store.defer(a.owner, t.id, w, t.generation, "running", null, 0);
  await store.stop(a.owner, t.id);
  await a.step();
  assert.equal(a.runtime.calls.length, 0);
  assert.equal((await store.get(a.owner, t.id)).status, "cancelled");
});

test("crash after durable submission marker replays same key and fences previous worker", async () => {
  const a = await setup();
  const previous = randomUUID();
  const t = (await store.take(a.owner, previous))!;
  const ctx = await store.executionContext(
    a.owner,
    t.id,
    previous,
    t.generation,
  );
  const request = {
    input: "x",
    session_id: ctx.sessionId,
    instructions: "test",
  };
  const execution = await store.prepareExecution(
    a.owner,
    t.id,
    previous,
    t.generation,
    request,
    ctx.profileId,
    ctx.sessionId,
  );
  await store.beginSubmission(
    a.owner,
    t.id,
    previous,
    t.generation,
    a.runtime.identity,
  );
  await a.runtime.create(request, execution.idempotency_key);
  await pool.query(
    "UPDATE tasks SET lease_until=now()-interval '1 second' WHERE id=$1",
    [t.id],
  );
  await a.step();
  assert.equal(new Set(a.runtime.calls).size, 1);
  assert.equal(a.runtime.runs.size, 1);
  await assert.rejects(
    store.bindUpstream(a.owner, t.id, previous, t.generation, "other"),
    /stale_lease/,
  );
});

test("unknown upstream and missing final output cannot be reported as success", async () => {
  const a = await setup();
  await a.step();
  a.runtime.complete("unknown");
  await a.step();
  assert.equal((await store.get(a.owner, a.task.id)).status, "unknown");
  assert.equal(a.runtime.calls.length, 1);
  a.runtime.complete();
  a.runtime.runs.values().next().value!.output = "";
  await a.step();
  assert.equal((await store.get(a.owner, a.task.id)).status, "interrupted");
});

test("terminal receipt survives waiting for specialists and upstream status expiry", async () => {
  const a = await setup();
  await a.step();
  const lease = (await store.take(a.owner, a.worker.id))!;
  const child = await store.spawn(
    a.owner,
    a.task.id,
    a.worker.id,
    lease.generation,
    "prediction",
    "specialist",
    { input: "predict" },
  );
  await store.defer(
    a.owner,
    lease.id,
    a.worker.id,
    lease.generation,
    "running",
    null,
    0,
  );
  await pool.query(
    "UPDATE tasks SET next_attempt_at=now()-interval '2 days' WHERE id=$1",
    [a.task.id],
  );
  a.runtime.complete();
  await a.step(); // Root terminal receipt, waits for child.
  assert.ok((await store.execution(a.owner, a.task.id))!.terminal_receipt);
  a.runtime.runs.clear();
  // Dispatch child in its own profile and then complete it.
  await a.step();
  a.runtime.complete();
  for (let i = 0; i < 4; i++) await a.step();
  assert.equal((await store.get(a.owner, child.id)).status, "completed");
  assert.equal((await store.get(a.owner, a.task.id)).status, "completed");
});

test("HTTP adapter sends scoped auth/key, checks identity and refuses redirects", async () => {
  let lastKey: string | undefined;
  let mode = "normal";
  const admissionId = randomUUID().replaceAll("-", "");
  let resolutions = 0;
  const server = createServer(async (req, res) => {
    assert.equal(req.headers.authorization, "Bearer test-token");
    if (mode === "redirect") {
      res.writeHead(302, { Location: "http://127.0.0.1:1/steal" });
      res.end();
      return;
    }
    res.setHeader("Content-Type", "application/json");
    if (req.method === "POST" && req.url === "/v1/runs") {
      lastKey = req.headers["idempotency-key"] as string;
      let body = "";
      for await (const c of req) body += c;
      assert.equal(JSON.parse(body).session_id, "session");
      res.end(JSON.stringify({ run_id: "run-1", status: "started" }));
    } else if (
      req.method === "POST" &&
      req.url === "/v1/runs/run-1/resolve-unknown"
    ) {
      let body = "";
      for await (const chunk of req) body += chunk;
      assert.deepEqual(JSON.parse(body), {
        admission_id: admissionId,
        execution_generation: 3,
      });
      assert.equal(req.headers["idempotency-key"], undefined);
      resolutions++;
      res.end(JSON.stringify({ acknowledged: true }));
    } else
      res.end(
        JSON.stringify({
          run_id: "run-1",
          session_id: mode === "wrong-session" ? "other" : "session",
          status: "completed",
          output: "answer",
          admission_id: admissionId,
          execution_generation: 3,
        }),
      );
  });
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  try {
    const client = new HermesClient(
      `http://127.0.0.1:${(server.address() as AddressInfo).port}`,
      "test-token",
      "test-image",
      86_400_000,
    );
    assert.equal(
      await client.create(
        { input: "test", session_id: "session", instructions: "test" },
        "stable-key",
      ),
      "run-1",
    );
    assert.equal(lastKey, "stable-key");
    assert.equal((await client.get("run-1", "session")).output, "answer");
    assert.equal(
      (await client.get("run-1", "session")).admission_id,
      admissionId,
    );
    await client.resolveUnknown("run-1", admissionId, 3);
    assert.equal(resolutions, 1);
    await assert.rejects(client.resolveUnknown("run-1", admissionId, -1));
    assert.equal(resolutions, 1);
    mode = "wrong-session";
    await assert.rejects(
      client.get("run-1", "session"),
      /upstream_identity_mismatch/,
    );
    mode = "redirect";
    await assert.rejects(client.get("run-1", "session"));
  } finally {
    await new Promise<void>((resolve, reject) =>
      server.close((e) => (e ? reject(e) : resolve())),
    );
  }
});

test("known run disappearing never triggers another create", async () => {
  const a = await setup();
  await a.step();
  a.runtime.runs.clear();
  await a.step();
  await a.step();
  assert.equal(a.runtime.calls.length, 1);
  assert.equal((await store.get(a.owner, a.task.id)).status, "unknown");
});

test("cancel after lost create response resolves original identity and waits for exit", async () => {
  const a = await setup();
  a.runtime.loseResponse = true;
  await a.step();
  await store.stop(a.owner, a.task.id);
  await a.step();
  await a.step();
  assert.equal(new Set(a.runtime.calls).size, 1);
  assert.equal(a.runtime.stops, 1);
  assert.equal((await store.get(a.owner, a.task.id)).status, "stopping");
  a.runtime.complete("cancelled");
  await a.step();
  assert.equal((await store.get(a.owner, a.task.id)).status, "cancelled");
});

test("standalone worker processes durable queue and exits on SIGTERM", async () => {
  const a = await setup(false);
  const expired = (
    await pool.query(
      "UPDATE task_events SET created_at=now()-interval '8 days' WHERE task_id=$1 RETURNING id",
      [a.task.id],
    )
  ).rows[0].id;
  const connection = new URL(url!);
  connection.searchParams.set("options", `-c search_path=${schema}`);
  const processEnv: NodeJS.ProcessEnv = {
    ...process.env,
    DATABASE_URL: connection.toString(),
  };
  delete processEnv.HERMES_PROFILE_REGISTRY;
  const child = spawn(
    process.execPath,
    ["--import", "tsx", "apps/worker/main.ts"],
    { env: { ...processEnv, RESEARCH_SINGLE_ROOT: "0" }, stdio: ["ignore", "pipe", "pipe"] },
  );
  let output = "";
  child.stdout.on("data", (chunk) => {
    output += chunk;
  });
  child.stderr.on("data", (chunk) => {
    output += chunk;
  });
  const exited = new Promise<number | null>((resolve, reject) => {
    child.once("error", reject);
    child.once("exit", resolve);
  });
  try {
    const until = Date.now() + 10_000;
    while (
      (await store.get(a.owner, a.task.id)).status !== "failed" &&
      Date.now() < until
    )
      await delay(50);
    assert.equal(
      (await store.get(a.owner, a.task.id)).status,
      "failed",
      output,
    );
    assert.match(output, /Task Worker started/);
    assert.equal(
      (await pool.query("SELECT 1 FROM task_events WHERE id=$1", [expired]))
        .rowCount,
      0,
    );
    assert.match(output, /Expired transient records cleaned/);
    assert.ok((await store.snapshot(a.owner, a.task.id)).messages.length >= 2);
    child.kill("SIGTERM");
    assert.equal(
      await Promise.race([
        exited,
        delay(10_000, undefined, { ref: false }).then(() => {
          throw new Error("worker did not exit");
        }),
      ]),
      0,
    );
  } finally {
    if (child.exitCode === null) child.kill("SIGKILL");
    await exited;
  }
});

test("required tool failure stops live upstream before finalizing as failed", async () => {
  const a = await setup();
  await a.step();
  await pool.query(
    `INSERT INTO tool_operations(id,owner_id,task_id,operation_key,kind,request_hash,request,status,error_code)
    VALUES($1,$2,$3,'failed-tool','quant','test','{}','failed','missing_data')`,
    [randomUUID(), a.owner, a.task.id],
  );
  await a.step();
  assert.equal(a.runtime.stops, 1);
  assert.equal((await store.get(a.owner, a.task.id)).status, "stopping");
  a.runtime.complete("cancelled");
  await a.step();
  const task = await store.get(a.owner, a.task.id);
  assert.equal(task.status, "failed");
  assert.equal(
    (task.output as { reason: string }).reason,
    "required_tool_failed",
  );
});

test("unsupported controls stop safely and retain failure reason instead of auto-approving", async () => {
  const a = await setup();
  await a.step();
  a.runtime.complete("waiting_for_approval");
  await a.step();
  assert.equal(a.runtime.stops, 1);
  assert.equal(
    (await store.get(a.owner, a.task.id)).failure_code,
    "unsupported_interaction",
  );
  a.runtime.complete("cancelled");
  await a.step();
  assert.equal((await store.get(a.owner, a.task.id)).status, "failed");
});

test("deadline expiry waits for exit and preserves deadline reason", async () => {
  const a = await setup();
  await a.step();
  await pool.query(
    "UPDATE tasks SET deadline=now()-interval '1 second' WHERE id=$1",
    [a.task.id],
  );
  await a.step();
  assert.equal(a.runtime.stops, 1);
  assert.equal((await store.get(a.owner, a.task.id)).status, "stopping");
  a.runtime.complete();
  await a.step();
  const task = await store.get(a.owner, a.task.id);
  assert.equal(task.status, "cancelled");
  assert.equal((task.output as { reason: string }).reason, "deadline_exceeded");
});

test("Worker binds stable capabilities to execution transport before any tool can run", async () => {
  const a = await setup(),
    caps = new ToolCapabilities(pool, "ab".repeat(32));
  const tokens: string[] = [];
  const original = a.runtime.create.bind(a.runtime);
  a.runtime.create = async (request, key, capability?: string) => {
    assert.ok(capability);
    tokens.push(capability);
    assert.ok(!JSON.stringify(request).includes(capability));
    const ctx = await ToolCapabilities.authenticate(pool, capability);
    assert.equal(ctx.taskId, a.task.id);
    assert.equal(ctx.ownerId, a.owner);
    return original(request, key);
  };
  a.runtime.loseResponse = true;
  await a.step(new TaskWorker(store, a.resolver, caps));
  await a.step(new TaskWorker(store, a.resolver, caps));
  assert.equal(tokens.length, 2);
  assert.equal(tokens[0], tokens[1]);
  assert.equal(a.runtime.runs.size, 1);
});
