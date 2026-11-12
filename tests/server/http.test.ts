import { before, after, test } from "node:test";
import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { Pool } from "pg";
import { migrate } from "../../packages/server/src/migrate.js";
import { createHandler } from "../../packages/server/src/http.js";
import { TaskStore } from "../../packages/server/src/tasks.js";
const url = process.env.TEST_DATABASE_URL;
if (!url || !new URL(url).pathname.endsWith("_test"))
  throw new Error("TEST_DATABASE_URL ending in _test required");
const schema = `test_${randomUUID().replaceAll("-", "")}`;
const admin = new Pool({ connectionString: url }),
  pool = new Pool({
    connectionString: url,
    options: `-c search_path=${schema}`,
  });
const origin = "http://localhost:3400",
  handle = createHandler(pool, origin);
const request = (
  path: string,
  method = "GET",
  body?: unknown,
  cookie?: string,
  headers?: Record<string, string>,
) =>
  handle(
    new Request(origin + "/api/" + path, {
      method,
      headers: {
        origin,
        "content-type": "application/json",
        ...(cookie ? { cookie } : {}),
        ...headers,
      },
      ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
    }),
  );
before(async () => {
  await admin.query(`CREATE SCHEMA ${schema}`);
  await migrate(pool);
});
after(async () => {
  await pool.end();
  await admin.query(`DROP SCHEMA ${schema} CASCADE`);
  await admin.end();
});
async function account() {
  const res = await request("auth/register", "POST", {
    email: `${randomUUID()}@test.example`,
    password: "test password long enough",
  });
  assert.equal(res.status, 201);
  return res.headers.get("set-cookie")!.split(";")[0]!;
}

test("BFF authenticates cookies and rejects foreign origins and owner injection", async () => {
  assert.equal((await request("threads")).status, 401);
  const cookie = await account();
  const csrf = await request("threads", "POST", undefined, cookie, {
    origin: "https://evil.example",
  });
  assert.equal(csrf.status, 403);
  const thread = await (
    await request("threads", "POST", undefined, cookie)
  ).json();
  const bad = await request(
    "research",
    "POST",
    { threadId: thread.id, input: "NVDA", ownerId: randomUUID() },
    cookie,
    { "idempotency-key": randomUUID() },
  );
  assert.equal(bad.status, 400);
  const submitted = await request(
    "research",
    "POST",
    { threadId: thread.id, input: "NVDA" },
    cookie,
    { "idempotency-key": randomUUID() },
  );
  assert.equal(submitted.status, 202);
  const task = await submitted.json(),
    other = await account();
  assert.equal(
    (await request(`tasks/${task.taskId}`, "GET", undefined, other)).status,
    404,
  );
  assert.equal(
    (await request(`tasks/${task.taskId}/stop`, "POST", undefined, other))
      .status,
    404,
  );
  assert.equal(
    (await request(`tasks/${task.taskId}/stop`, "POST", undefined, cookie))
      .status,
    200,
  );
  const sse = await request(
    `tasks/${task.taskId}/events`,
    "GET",
    undefined,
    cookie,
  );
  const data = await sse.text();
  assert.match(data, /state.snapshot/);
  assert.match(data, /cancelled/);
});

test("SSE disconnect leaves work queued; retained cursors replay and expired cursors restore snapshots", async () => {
  const cookie = await account();
  const thread = await (
    await request("threads", "POST", undefined, cookie)
  ).json();
  const task = await (
    await request(
      "research",
      "POST",
      { threadId: thread.id, input: "NVDA" },
      cookie,
      { "idempotency-key": randomUUID() },
    )
  ).json();
  const path = `tasks/${task.taskId}`;
  const stream = await request(`${path}/events`, "GET", undefined, cookie);
  const reader = stream.body!.getReader();
  const first = new TextDecoder().decode((await reader.read()).value);
  const cursor = Number(/^id: (\d+)/m.exec(first)![1]);
  assert.match(first, /state.snapshot/);
  await reader.cancel();
  reader.releaseLock();
  const pending = await (await request(path, "GET", undefined, cookie)).json();
  assert.equal(pending.task.status, "queued");
  await request(`${path}/stop`, "POST", undefined, cookie);
  const replay = await (
    await request(`${path}/events`, "GET", undefined, cookie, {
      "last-event-id": String(cursor),
    })
  ).text();
  assert.match(replay, /run.cancelled/);
  assert.doesNotMatch(replay, /state.snapshot/);
  await pool.query("DELETE FROM task_events WHERE task_id=$1", [task.taskId]);
  const recovered = await (
    await request(`${path}/events`, "GET", undefined, cookie, {
      "last-event-id": String(cursor),
    })
  ).text();
  assert.match(recovered, /state.snapshot/);
  assert.match(recovered, /cancelled/);
  assert.equal(
    (
      await request(`${path}/events`, "GET", undefined, cookie, {
        "last-event-id": "999999",
      })
    ).status,
    400,
  );
});

test(
  "logout closes an existing SSE subscription and denies reconnect",
  { timeout: 4000 },
  async () => {
    const cookie = await account();
    const thread = await (
      await request("threads", "POST", undefined, cookie)
    ).json();
    const task = await (
      await request(
        "research",
        "POST",
        { threadId: thread.id, input: "NVDA" },
        cookie,
        { "idempotency-key": randomUUID() },
      )
    ).json();
    const path = `tasks/${task.taskId}/events`;
    const stream = await request(path, "GET", undefined, cookie);
    const reader = stream.body!.getReader();
    await reader.read();
    assert.equal(
      (await request("auth/logout", "POST", undefined, cookie)).status,
      200,
    );
    let data = "";
    for (;;) {
      const chunk = await reader.read();
      if (chunk.done) break;
      data += new TextDecoder().decode(chunk.value);
    }
    reader.releaseLock();
    assert.match(data, /connection.error/);
    assert.equal((await request(path, "GET", undefined, cookie)).status, 401);
  },
);

test("portfolio versions are owner-scoped and references cannot be borrowed", async () => {
  const cookie = await account(),
    other = await account();
  const body = {
    asOfSession: "2025-07-03",
    positions: [{ symbol: "NVDA", quantity: "2.5" }],
    cashUsd: "100",
  };
  const saved = await (
    await request("portfolios", "POST", body, cookie)
  ).json();
  const next = await (
    await request(
      `portfolios/${saved.portfolioId}/snapshots`,
      "POST",
      { ...body, cashUsd: "200" },
      cookie,
    )
  ).json();
  assert.notEqual(next.snapshotId, saved.snapshotId);
  const old = await (
    await request(
      `portfolios/${saved.portfolioId}?version=${saved.snapshotId}`,
      "GET",
      undefined,
      cookie,
    )
  ).json();
  assert.equal(old.cash_usd, "100");
  assert.equal(
    (await request(`portfolios/${saved.portfolioId}`, "GET", undefined, other))
      .status,
    404,
  );
  const thread = await (
    await request("threads", "POST", undefined, other)
  ).json();
  const attempt = await request(
    "research",
    "POST",
    {
      threadId: thread.id,
      input: "value",
      portfolioSnapshotRef: saved.snapshotId,
    },
    other,
    { "idempotency-key": randomUUID() },
  );
  assert.equal(attempt.status, 404);
});

test("workspace uses version checks and cannot inject new artifacts through layout API", async () => {
  const cookie = await account(),
    thread = await (await request("threads", "POST", undefined, cookie)).json();
  assert.equal(
    (
      await request(
        `workspaces/${thread.workspaceId}`,
        "PUT",
        { expectedVersion: 0, order: [] },
        cookie,
      )
    ).status,
    200,
  );
  assert.equal(
    (
      await request(
        `workspaces/${thread.workspaceId}`,
        "PUT",
        { expectedVersion: 99, order: [] },
        cookie,
      )
    ).status,
    409,
  );
  assert.equal(
    (
      await request(
        `workspaces/${thread.workspaceId}`,
        "PUT",
        { expectedVersion: 0, order: [randomUUID()] },
        cookie,
      )
    ).status,
    400,
  );
});

test("audit is owner-scoped, immutable, paginated and retained independently of SSE", async () => {
  const cookie = await account(),
    other = await account();
  const thread = await (
    await request("threads", "POST", undefined, cookie)
  ).json();
  const task = await (
    await request(
      "research",
      "POST",
      {
        threadId: thread.id,
        input: "private prompt must not appear in audit",
      },
      cookie,
      { "idempotency-key": randomUUID() },
    )
  ).json();
  const owner = (
    await pool.query("SELECT owner_id FROM tasks WHERE id=$1", [task.taskId])
  ).rows[0].owner_id;
  const store = new TaskStore(pool),
    worker = randomUUID();
  const root = (await store.claim(owner, worker))!.task;
  const child = await store.spawn(
    owner,
    root.id,
    worker,
    root.generation,
    "prediction",
    "specialist",
    { input: "private child input" },
  );
  await request(`tasks/${root.id}/stop`, "POST", undefined, cookie);
  const endpoint = `tasks/${root.id}/audit`;
  assert.equal((await request(endpoint)).status, 401);
  assert.equal((await request(endpoint, "GET", undefined, other)).status, 404);
  assert.equal(
    (await request(`tasks/${child.id}/audit`, "GET", undefined, other)).status,
    404,
  );
  const response = await request(endpoint, "GET", undefined, cookie);
  assert.equal(response.headers.get("cache-control"), "no-store");
  const before = await response.json();
  assert.ok(
    before.items.some((item: { task_id: string }) => item.task_id === child.id),
  );
  assert.ok(
    before.items.some(
      (item: { event: string; status: string }) =>
        item.event === "task.transition" && item.status === "stopping",
    ),
  );
  assert.doesNotMatch(
    JSON.stringify(before),
    /private prompt|private child|password|tool_token|request_hash/,
  );
  const childHistory = await (
    await request(`tasks/${child.id}/audit`, "GET", undefined, cookie)
  ).json();
  assert.ok(childHistory.items.length > 0);
  assert.ok(
    childHistory.items.every(
      (item: { task_id: string }) => item.task_id === child.id,
    ),
  );
  await pool.query("DELETE FROM task_events WHERE task_id=$1", [root.id]);
  assert.deepEqual(
    await (await request(endpoint, "GET", undefined, cookie)).json(),
    before,
  );
  await assert.rejects(
    pool.query(
      "UPDATE execution_audit SET status='completed' WHERE task_id=$1",
      [root.id],
    ),
    /immutable_execution_evidence/,
  );
  await assert.rejects(
    pool.query("DELETE FROM execution_audit WHERE task_id=$1", [root.id]),
    /immutable_execution_evidence/,
  );
  await assert.rejects(
    pool.query("TRUNCATE execution_audit"),
    /immutable_execution_evidence/,
  );
  await assert.rejects(
    pool.query("TRUNCATE tool_exit_receipts CASCADE"),
    /immutable_execution_evidence/,
  );
  for (const cursor of ["-1", "1.5", "9223372036854775808", "abc", "01", ""]) {
    assert.equal(
      (await request(`${endpoint}?after=${cursor}`, "GET", undefined, cookie))
        .status,
      400,
    );
  }
  // Synthetic audit volume exercises paging without issuing 210 executions.
  await pool.query(
    `INSERT INTO execution_audit(owner_id,task_id,event,status,generation)
    SELECT $1,$2,'task.transition','stopping',1 FROM generate_series(1,210)`,
    [owner, root.id],
  );
  const first = await (
    await request(endpoint, "GET", undefined, cookie)
  ).json();
  const second = await (
    await request(
      `${endpoint}?after=${first.nextCursor}`,
      "GET",
      undefined,
      cookie,
    )
  ).json();
  assert.equal(first.items.length, 200);
  assert.equal(second.items.length, before.items.length + 10);
  const all = [...first.items, ...second.items];
  assert.equal(new Set(all.map((item) => item.sequence)).size, all.length);
  assert.ok(
    all.every(
      (item, index) =>
        typeof item.sequence === "string" &&
        (index === 0 ||
          BigInt(item.sequence) > BigInt(all[index - 1].sequence)),
    ),
  );
  const end = await (
    await request(
      `${endpoint}?after=${second.nextCursor}`,
      "GET",
      undefined,
      cookie,
    )
  ).json();
  assert.deepEqual(end, { items: [], nextCursor: second.nextCursor });
});

test("group history is owner-only and web cannot continue or stop it", async () => {
  const { ExecutionContextStore } =
    await import("../../packages/server/src/execution-context.js");
  const cookie = await account(),
    other = await account();
  const owner = (
    await (await request("auth/session", "GET", undefined, cookie)).json()
  ).id;
  const context = await new ExecutionContextStore(pool).group(
    owner,
    "cli_test",
    "oc_http",
  );
  const tasks = new TaskStore(pool);
  const thread = await tasks.createThread(owner, context.id);
  const task = await tasks.submit(owner, thread.id, randomUUID(), {
    input: "group question",
  });
  assert.equal(
    (
      await (
        await request(`threads/${thread.id}`, "GET", undefined, cookie)
      ).json()
    ).source,
    "group",
  );
  assert.equal(
    (await request(`threads/${thread.id}`, "GET", undefined, other)).status,
    404,
  );
  assert.equal(
    (
      await request(
        "research",
        "POST",
        { threadId: thread.id, input: "continue" },
        cookie,
        { "idempotency-key": randomUUID() },
      )
    ).status,
    403,
  );
  assert.equal(
    (await request(`tasks/${task.id}/stop`, "POST", {}, cookie)).status,
    403,
  );
});
