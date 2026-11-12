import { TaskWorker } from "../../packages/server/src/worker.js";
import { spawn } from "node:child_process";
import { createServer } from "node:net";
import { setTimeout as delay } from "node:timers/promises";
import { before, after, test } from "node:test";
import assert from "node:assert/strict";
import { randomBytes, randomUUID } from "node:crypto";
import { readFile } from "node:fs/promises";
import { Pool } from "pg";
import { migrate } from "../../packages/server/src/migrate.js";
import {
  TaskStore,
  type Task,
  type Role,
} from "../../packages/server/src/tasks.js";
import { AuthStore } from "../../packages/server/src/auth.js";
import { BusinessStore } from "../../packages/server/src/business.js";
import { DataService } from "../../packages/server/src/data-service.js";
import { ToolCapabilities } from "../../packages/server/src/tool-context.js";
import { createToolHandler } from "../../packages/server/src/tool-gateway.js";

const url = process.env.TEST_DATABASE_URL;
if (!url || !new URL(url).pathname.endsWith("_test"))
  throw new Error("Dedicated TEST_DATABASE_URL ending in _test required");
const schema = `test_${randomUUID().replaceAll("-", "")}`,
  admin = new Pool({ connectionString: url });
const pool = new Pool({
    connectionString: url,
    options: `-c search_path=${schema}`,
  }),
  store = new TaskStore(pool),
  business = new BusinessStore(pool),
  worker = randomUUID();
const capabilities = new ToolCapabilities(
  pool,
  randomBytes(32).toString("hex"),
);
const rows: any[] = JSON.parse(
  await readFile(
    new URL("../fixtures/synthetic-market.json", import.meta.url),
    "utf8",
  ),
);
const data = new DataService(pool, {
  source: "synthetic-test-only",
  version: "fixture-v1",
  feed: "fixture",
  async bars(q) {
    return rows.filter((r) => r.session >= q.start && r.session <= q.end);
  },
});
const handler = createToolHandler(pool, data);
before(async () => {
  await admin.query(`CREATE SCHEMA ${schema}`);
  await migrate(pool);
});
after(async () => {
  await pool.end();
  await admin.query(`DROP SCHEMA ${schema} CASCADE`);
  await admin.end();
});
async function token(task: Task) {
  const profile = (
    await pool.query(
      "SELECT id FROM profile_instances WHERE owner_id=$1 AND role=$2",
      [task.owner_id, task.role],
    )
  ).rows[0].id;
  await store.prepareExecution(
    task.owner_id,
    task.id,
    worker,
    task.generation,
    { input: task.role },
    profile,
    randomUUID(),
  );
  const value = await capabilities.issue(
    task.owner_id,
    task.id,
    worker,
    task.generation,
  );
  await store.beginSubmission(
    task.owner_id,
    task.id,
    worker,
    task.generation,
    "test-runtime",
  );
  return value;
}
async function setup() {
  const user = await new AuthStore(pool).register(
    `${randomUUID()}@test.invalid`,
    "test-password-long",
  );
  const owner = user.id;
  const holdings = await business.saveHoldings(owner, {
    asOfSession: "2025-08-01",
    positions: [{ symbol: "NVDA", quantity: "2" }],
    cashUsd: "100",
  });
  const thread = await store.createThread(owner);
  await store.submit(owner, thread.id, randomUUID(), {
    input: "research",
    portfolioSnapshotRef: holdings.snapshotId,
  });
  const task = (await store.claim(owner, worker))!.task;
  return { owner, holdings, thread, task, token: await token(task) };
}
async function call(
  token: string,
  tool: string,
  args: unknown,
  key = randomUUID(),
  expected = 200,
) {
  const response = await handler(
    new Request("http://tools/call", {
      method: "POST",
      headers: {
        Authorization: `Bearer ${token}`,
        "Content-Type": "application/json",
        "X-Alpha-Operation-Id": key,
      },
      body: JSON.stringify({ tool, arguments: args }),
    }),
  );
  const body = await response.json();
  assert.equal(response.status, expected, JSON.stringify(body));
  return body;
}
async function market(token: string, adjustment = "split") {
  const op = await call(token, "market_history", {
    symbol: "NVDA",
    start: "2025-01-02",
    end: "2025-08-01",
    adjustment,
  });
  assert.equal(op.status, "completed", JSON.stringify(op));
  return op.result.datasetRef as string;
}

for (const cancel of [false, true])
  test(`uncertain admission waits without side effects; cancel=${cancel}`, async () => {
    const a = await setup();
    await store.defer(
      a.owner,
      a.task.id,
      worker,
      a.task.generation,
      "unknown",
      "lost_create_response",
      0,
    );
    const pending = call(
      a.token,
      "market_history",
      {
        symbol: "NVDA",
        start: "2025-01-02",
        end: "2025-08-01",
        adjustment: "split",
      },
      randomUUID(),
      cancel ? 409 : 200,
    );
    await delay(150);
    assert.equal(
      (
        await pool.query("SELECT count(*) FROM tool_calls WHERE task_id=$1", [
          a.task.id,
        ])
      ).rows[0].count,
      "0",
    );
    assert.equal(
      (
        await pool.query(
          "SELECT count(*) FROM tool_operations WHERE task_id=$1",
          [a.task.id],
        )
      ).rows[0].count,
      "0",
    );
    if (cancel) await store.stop(a.owner, a.task.id);
    else {
      const leased = (await store.take(a.owner, worker))!;
      await store.defer(
        a.owner,
        a.task.id,
        worker,
        leased.generation,
        "running",
        null,
        0,
      );
    }
    const result = await pending;
    assert.equal(
      cancel ? result.error : result.status,
      cancel ? "tool_not_admissible" : "completed",
    );
  });

test("execution capability is encrypted at rest, stable on retry and cannot select another owner", async () => {
  const a = await setup(),
    b = await setup();
  assert.equal(
    await capabilities.issue(a.owner, a.task.id, worker, a.task.generation),
    a.token,
  );
  const stored = (
    await pool.query(
      "SELECT tool_token_hash,tool_token_ciphertext FROM executions WHERE task_id=$1",
      [a.task.id],
    )
  ).rows[0];
  assert.ok(!JSON.stringify(stored).includes(a.token));
  const ctx = await ToolCapabilities.authenticate(pool, a.token);
  assert.equal(ctx.ownerId, a.owner);
  assert.equal(ctx.taskId, a.task.id);
  await assert.rejects(
    ToolCapabilities.authenticate(pool, randomBytes(32).toString("hex")),
    /unauthorized/,
  );
  await assert.rejects(
    capabilities.issue(a.owner, a.task.id, randomUUID(), a.task.generation),
    /stale_lease/,
  );
  const response = await handler(
    new Request("http://tools/call", {
      method: "POST",
      headers: {
        Authorization: `Bearer ${a.token}`,
        "Content-Type": "application/json",
        "X-Alpha-Operation-Id": randomUUID(),
      },
      body: JSON.stringify({
        tool: "get_workspace",
        arguments: {},
        ownerId: b.owner,
      }),
    }),
  );
  assert.equal(response.status, 400);
  const origin = await handler(
    new Request("http://tools/tools", {
      headers: {
        Authorization: `Bearer ${a.token}`,
        Origin: "http://localhost",
      },
    }),
  );
  assert.equal(origin.status, 403);
});

test("three role tool flow produces all five saved Canvas components and final references", async () => {
  const a = await setup(),
    ref = await market(a.token),
    raw = await market(a.token, "raw");
  const lostReceipt = (
    await pool.query(
      "SELECT id FROM tool_calls WHERE task_id=$1 AND tool='market_history' ORDER BY created_at LIMIT 1",
      [a.task.id],
    )
  ).rows[0];
  await pool.query("UPDATE tool_calls SET response=NULL WHERE id=$1", [
    lostReceipt.id,
  ]);
  await store.operations(a.owner, a.task.id);
  assert.ok(
    (
      await pool.query("SELECT response FROM tool_calls WHERE id=$1", [
        lostReceipt.id,
      ])
    ).rows[0].response.result.datasetRef,
  );
  const indicators = await call(a.token, "quant_calculate", {
    kind: "indicators",
    datasetRef: ref,
  });
  await call(a.token, "canvas_upsert", {
    component: "stock_chart",
    title: "NVDA 日线",
    datasetRef: ref,
    props: { showVolume: true },
  });
  await call(a.token, "canvas_upsert", {
    component: "indicator_panel",
    title: "MA20 与 RSI14",
    resultRef: indicators.result.resultRef,
  });
  const summary = await call(a.token, "save_research_summary", {
    text: "This is test interpretation based on the saved inputs.",
    datasetRefs: [ref],
    resultRefs: [indicators.result.resultRef],
  });
  await call(a.token, "canvas_upsert", {
    component: "research_summary",
    title: "研究解释",
    resultRef: summary.resultRef,
  });
  for (const role of ["prediction", "portfolio"] as Role[]) {
    const request = {
      role,
      requestKey: role,
      question: role,
      datasetRefs: [role === "portfolio" ? raw : ref],
      ...(role === "portfolio"
        ? { portfolioSnapshotRef: a.holdings.snapshotId }
        : {}),
    };
    const dispatched = await call(a.token, "submit_specialist", request);
    const child = (await store.claim(a.owner, worker))!.task;
    assert.equal(child.id, dispatched.childTaskId);
    const childToken = await token(child);
    const listed = await handler(
      new Request("http://tools/tools", {
        headers: { Authorization: `Bearer ${childToken}` },
      }),
    );
    assert.ok(
      !(await listed.json()).tools.some(
        (t: any) => t.name === "submit_specialist",
      ),
    );
    const computed = await call(
      childToken,
      "quant_calculate",
      role === "portfolio"
        ? {
            kind: role,
            portfolioSnapshotRef: a.holdings.snapshotId,
            datasetRefs: [raw],
          }
        : { kind: role, datasetRef: ref },
    );
    assert.equal(computed.status, "completed", JSON.stringify(computed));
    await call(childToken, "canvas_upsert", {
      component:
        role === "portfolio" ? "portfolio_summary" : "prediction_result",
      title: role,
      resultRef: computed.result.resultRef,
    });
    await call(childToken, "submit_specialist", request, randomUUID(), 403);
    await store.finish(
      a.owner,
      child.id,
      worker,
      child.generation,
      "completed",
      { resultRefs: [computed.result.resultRef] },
    );
    assert.equal(
      (await call(a.token, "get_specialist_result", { childTaskId: child.id }))
        .status,
      "completed",
    );
  }
  const workspace = await call(a.token, "get_workspace", {});
  assert.equal(workspace.snapshot.artifacts.length, 5);
  const reversed = workspace.snapshot.artifacts
    .map((artifact: any) => artifact.artifactId)
    .reverse();
  const layout = await business.saveLayout(
    a.owner,
    a.task.workspace_id,
    workspace.version,
    reversed,
  );
  assert.equal(layout.version, workspace.version + 1);
  assert.equal(
    (
      await business.saveLayout(
        a.owner,
        a.task.workspace_id,
        layout.version,
        reversed,
      )
    ).version,
    layout.version,
  );
  await assert.rejects(
    business.saveLayout(
      a.owner,
      a.task.workspace_id,
      workspace.version,
      reversed,
    ),
    /version_conflict/,
  );
  assert.deepEqual(
    (await store.snapshot(a.owner, a.task.id)).artifacts.map(
      (artifact: any) => artifact.artifactId,
    ),
    reversed,
  );
  const events = await store.events(a.owner, a.task.id);
  assert.equal(events.filter((e) => e.event === "canvas.upsert").length, 5);
  await store.bindUpstream(
    a.owner,
    a.task.id,
    worker,
    a.task.generation,
    "final-run",
  );
  await store.defer(
    a.owner,
    a.task.id,
    worker,
    a.task.generation,
    "running",
    null,
    0,
  );
  const finisher = new TaskWorker(store, () => ({
    identity: "test-runtime",
    retentionMs: 86_400_000,
    async create() {
      throw new Error("must not re-submit");
    },
    async get(run_id, session_id) {
      return {
        run_id,
        session_id,
        status: "completed" as const,
        output: "five components complete",
      };
    },
    async stop() {},
  }));
  await finisher.once(a.owner);
  assert.equal((await store.get(a.owner, a.task.id)).status, "completed");
  assert.equal(
    ((await store.get(a.owner, a.task.id)).output as { resultRefs: string[] })
      .resultRefs.length,
    4,
  );
  const snap = await store.snapshot(a.owner, a.task.id);
  assert.equal(snap.artifacts.length, 5);
  await pool.query("DELETE FROM task_events WHERE task_id=$1", [a.task.id]);
  assert.deepEqual(
    (await store.snapshot(a.owner, a.task.id)).artifacts,
    snap.artifacts,
  );
});

test("Canvas mutations deduplicate, validate references and archive old revisions", async () => {
  const a = await setup(),
    ref = await market(a.token),
    key = randomUUID();
  const request = {
    component: "stock_chart",
    title: "NVDA daily",
    datasetRef: ref,
  };
  const first = await call(a.token, "canvas_upsert", request, key),
    before = await call(a.token, "get_workspace", {});
  assert.deepEqual(await call(a.token, "canvas_upsert", request, key), first);
  assert.equal(
    (await call(a.token, "get_workspace", {})).version,
    before.version,
  );
  const same = await call(a.token, "canvas_upsert", {
    ...request,
    artifactId: first.artifactId,
  });
  assert.equal(same.changed, false);
  await call(
    a.token,
    "canvas_patch",
    { artifactId: first.artifactId, datasetRef: randomUUID() },
    randomUUID(),
    400,
  );
  await call(
    a.token,
    "canvas_upsert",
    { ...request, component: "backtest_result" },
    randomUUID(),
    400,
  );
  await call(
    a.token,
    "canvas_upsert",
    { ...request, title: "<script>alert(1)</script>" },
    randomUUID(),
    400,
  );
  await call(
    a.token,
    "canvas_upsert",
    { ...request, ownerId: a.owner, prices: [100] },
    randomUUID(),
    400,
  );
  await call(a.token, "canvas_patch", {
    artifactId: first.artifactId,
    title: "New title",
    props: { color: "#112233" },
  });
  const newTask = await store.submit(a.owner, a.thread.id, "new-revision", {
    input: "one year",
  });
  const current = await business.resource(
    a.owner,
    "workspaces",
    a.task.workspace_id,
  );
  assert.ok(newTask.workspace_revision > a.task.workspace_revision);
  assert.equal(current.snapshot.artifacts.length, 0);
  const archived = await call(a.token, "canvas_patch", {
    artifactId: first.artifactId,
    title: "Archived title",
  });
  assert.equal(archived.archived, true);
  const after = await business.resource(
    a.owner,
    "workspaces",
    a.task.workspace_id,
  );
  assert.equal(after.version, current.version);
  assert.equal(after.snapshot.artifacts.length, 0);
  assert.equal(
    (await store.snapshot(a.owner, a.task.id)).artifacts[0].title,
    "Archived title",
  );
  const removed = await call(a.token, "canvas_remove", {
    artifactId: first.artifactId,
  });
  assert.equal(removed.archived, true);
  assert.equal((await store.snapshot(a.owner, a.task.id)).artifacts.length, 0);
  assert.equal(
    (await business.resource(a.owner, "workspaces", a.task.workspace_id))
      .version,
    current.version,
  );
});

test("foreign references and role mismatches fail; old execution tokens never migrate to new tasks", async () => {
  const a = await setup(),
    b = await setup(),
    ref = await market(a.token);
  await call(b.token, "read_dataset", { datasetRef: ref }, randomUUID(), 404);
  await call(
    b.token,
    "canvas_upsert",
    { component: "stock_chart", title: "foreign", datasetRef: ref },
    randomUUID(),
    404,
  );
  await store.finish(
    a.owner,
    a.task.id,
    worker,
    a.task.generation,
    "completed",
    { text: "done" },
  );
  const next = await store.submit(a.owner, a.thread.id, "next", {
    input: "next",
  });
  const leased = (await store.claim(a.owner, worker))!.task;
  assert.equal(leased.id, next.id);
  const nextToken = await token(leased);
  assert.notEqual(nextToken, a.token);
  await call(
    a.token,
    "canvas_upsert",
    { component: "stock_chart", title: "late", datasetRef: ref },
    randomUUID(),
    401,
  );
  await assert.rejects(
    ToolCapabilities.authenticate(pool, a.token),
    /unauthorized/,
  );
  const retired = (
    await pool.query(
      "SELECT tool_token_hash,tool_token_ciphertext FROM executions WHERE task_id=$1",
      [a.task.id],
    )
  ).rows[0];
  assert.deepEqual(retired, {
    tool_token_hash: null,
    tool_token_ciphertext: null,
  });
  assert.equal(
    (await call(nextToken, "get_workspace", {})).snapshot.artifacts.length,
    0,
  );
});

test("private gateway runs as a standalone process and authenticates real HTTP calls", async () => {
  const a = await setup();
  const portProbe = createServer();
  await new Promise<void>((resolve) =>
    portProbe.listen(0, "127.0.0.1", resolve),
  );
  const port = (portProbe.address() as { port: number }).port;
  await new Promise<void>((resolve) => portProbe.close(() => resolve()));
  const connection = new URL(url!);
  connection.searchParams.set("options", `-c search_path=${schema}`);
  const env: NodeJS.ProcessEnv = {
    ...process.env,
    DATABASE_URL: connection.toString(),
    TOOL_GATEWAY_HOST: "127.0.0.1",
    TOOL_GATEWAY_PORT: String(port),
  };
  delete env.MARKET_PROVIDER;
  const child = spawn(
    process.execPath,
    ["--import", "tsx", "apps/tools/main.ts"],
    { env, stdio: ["ignore", "pipe", "pipe"] },
  );
  let output = "";
  child.stdout.on("data", (chunk) => {
    output += chunk;
  });
  child.stderr.on("data", (chunk) => {
    output += chunk;
  });
  const exited = new Promise<number | null>((resolve, reject) => {
    child.once("exit", resolve);
    child.once("error", reject);
  });
  try {
    const until = Date.now() + 10_000;
    while (
      !output.includes("Private tool gateway started") &&
      Date.now() < until
    )
      await delay(50);
    assert.match(output, /Private tool gateway started/);
    const listed = await fetch(`http://127.0.0.1:${port}/tools`, {
      headers: { Authorization: `Bearer ${a.token}` },
    });
    assert.equal(listed.status, 200);
    assert.ok(
      (await listed.json()).tools.some(
        (t: any) => t.name === "submit_specialist",
      ),
    );
    const denied = await fetch(`http://127.0.0.1:${port}/tools`);
    assert.equal(denied.status, 401);
    await denied.arrayBuffer();
    const result = await fetch(`http://127.0.0.1:${port}/call`, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${a.token}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ tool: "get_workspace", arguments: {} }),
    });
    assert.equal(result.status, 200);
    assert.equal((await result.json()).id, a.task.workspace_id);
    child.kill("SIGTERM");
    assert.equal(
      await Promise.race([
        exited,
        delay(10_000, undefined, { ref: false }).then(() => {
          throw new Error("gateway did not exit");
        }),
      ]),
      0,
    );
  } finally {
    if (child.exitCode === null) child.kill("SIGKILL");
    await exited;
  }
});
