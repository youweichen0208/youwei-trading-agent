import { after, before, test } from "node:test";
import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { Pool } from "pg";
import { migrate } from "../../packages/server/src/migrate.js";
import { DomainError, TaskStore } from "../../packages/server/src/tasks.js";

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
const worker = randomUUID();
before(async () => {
  await admin.query(`CREATE SCHEMA ${schema}`);
  await migrate(pool);
});
after(async () => {
  await pool.end();
  await admin.query(`DROP SCHEMA ${schema} CASCADE`);
  await admin.end();
});
async function setup() {
  const owner = randomUUID();
  await pool.query(
    "INSERT INTO users(id,email,password_hash) VALUES($1,$2,$3)",
    [owner, `${owner}@test.invalid`, "not-a-real-password"],
  );
  const thread = await store.createThread(owner);
  return { owner, thread };
}
async function running() {
  const { owner, thread } = await setup();
  const task = await store.submit(owner, thread.id, randomUUID(), {
    input: "Analyze NVDA",
  });
  const claim = (await store.claim(owner, worker))!;
  assert.equal(claim.task.id, task.id);
  return { owner, thread, task: claim.task };
}
function error(code: string) {
  return (e: unknown) => e instanceof DomainError && e.code === code;
}

test("migration can be run twice without changing schema", async () => {
  await migrate(pool);
});

test("concurrent identical submissions persist exactly one task and user message", async () => {
  const { owner, thread } = await setup(),
    key = randomUUID();
  const tasks = await Promise.all(
    Array.from({ length: 10 }, () =>
      store.submit(owner, thread.id, key, { input: "NVDA" }),
    ),
  );
  assert.equal(new Set(tasks.map((t) => t.id)).size, 1);
  const count = (
    await pool.query("SELECT count(*) FROM messages WHERE task_id=$1", [
      tasks[0]!.id,
    ])
  ).rows[0];
  assert.equal(count.count, "1");
  await assert.rejects(
    store.submit(owner, thread.id, key, { input: "AAPL" }),
    error("idempotency_conflict"),
  );
});

test("resource ownership applies to reads, submits, cancellation, and events", async () => {
  const a = await running(),
    b = await setup();
  for (const action of [
    () => store.get(b.owner, a.task.id),
    () => store.stop(b.owner, a.task.id),
    () => store.events(b.owner, a.task.id),
    () => store.submit(b.owner, a.thread.id, "other", { input: "attack" }),
  ])
    await assert.rejects(action(), error("not_found"));
});

test("concurrent workers cannot both claim the same profile", async () => {
  const { owner, thread } = await setup();
  await store.submit(owner, thread.id, "first", { input: "one" });
  await store.submit(owner, thread.id, "second", { input: "two" });
  const claims = await Promise.all(
    Array.from({ length: 8 }, () => store.claim(owner, randomUUID())),
  );
  assert.equal(claims.filter(Boolean).length, 1);
});

test("expired lease recovery fences old worker and retains execution identity", async () => {
  const { owner, task } = await running();
  const execution = await store.prepareExecution(
    owner,
    task.id,
    worker,
    task.generation,
    { input: "one" },
    "profile",
    "session",
  );
  await pool.query(
    "UPDATE tasks SET lease_until=now()-interval '1 second' WHERE id=$1",
    [task.id],
  );
  const nextWorker = randomUUID();
  const recovered = (await store.claim(owner, nextWorker))!;
  assert.equal(recovered.recovery, true);
  assert.equal(recovered.task.status, "unknown");
  assert.equal(recovered.task.generation, task.generation + 1);
  await assert.rejects(
    store.finish(owner, task.id, worker, task.generation, "completed", {
      text: "late",
    }),
    error("stale_lease"),
  );
  const same = await store.prepareExecution(
    owner,
    task.id,
    nextWorker,
    recovered.task.generation,
    { input: "one" },
    "profile",
    "session",
  );
  assert.equal(same.idempotency_key, execution.idempotency_key);
  await assert.rejects(
    store.prepareExecution(
      owner,
      task.id,
      nextWorker,
      recovered.task.generation,
      { input: "changed" },
      "profile",
      "session",
    ),
    error("execution_identity_conflict"),
  );
});

test("specialists can run while root awaits, deduplicate and prohibit recursion", async () => {
  const { owner, task } = await running();
  const child = await store.spawn(
    owner,
    task.id,
    worker,
    task.generation,
    "prediction",
    "dispatch1",
    { input: "forecast" },
  );
  assert.equal(
    (
      await store.spawn(
        owner,
        task.id,
        worker,
        task.generation,
        "prediction",
        "dispatch1",
        { input: "forecast" },
      )
    ).id,
    child.id,
  );
  const claimed = (await store.claim(owner, worker))!.task;
  assert.equal(claimed.id, child.id);
  await assert.rejects(
    store.spawn(
      owner,
      child.id,
      worker,
      claimed.generation,
      "portfolio",
      "recursive",
      { input: "x" },
    ),
    error("dispatch_not_allowed"),
  );
  await assert.rejects(
    store.finish(owner, task.id, worker, task.generation, "completed", {
      text: "too soon",
    }),
    error("children_still_running"),
  );
  await store.finish(owner, child.id, worker, claimed.generation, "completed", {
    resultRef: "future-reference",
  });
  await store.finish(owner, task.id, worker, task.generation, "completed", {
    text: "complete",
  });
  const events = await store.events(owner, task.id);
  assert.deepEqual(
    events.map((e) => e.sequence),
    Array.from({ length: events.length }, (_, i) => i + 1),
  );
  assert.equal(events.at(-1).event, "run.completed");
});

test("cancel root propagates, waits for children and rejects late success", async () => {
  const { owner, task } = await running();
  const child = await store.spawn(
    owner,
    task.id,
    worker,
    task.generation,
    "prediction",
    "dispatch",
    { input: "forecast" },
  );
  const runningChild = (await store.claim(owner, worker))!.task;
  assert.equal((await store.stop(owner, task.id)).status, "stopping");
  assert.equal((await store.get(owner, child.id)).status, "stopping");
  await assert.rejects(
    store.spawn(owner, task.id, worker, task.generation, "portfolio", "new", {
      input: "x",
    }),
    error("dispatch_not_allowed"),
  );
  await assert.rejects(
    store.finish(owner, task.id, worker, task.generation, "cancelled", {}),
    error("children_still_running"),
  );
  await store.finish(
    owner,
    child.id,
    worker,
    runningChild.generation,
    "cancelled",
    {},
  );
  await assert.rejects(
    store.finish(owner, task.id, worker, task.generation, "completed", {
      text: "late",
    }),
    error("cancellation_pending"),
  );
  await store.finish(owner, task.id, worker, task.generation, "cancelled", {});
  assert.equal((await store.get(owner, task.id)).status, "cancelled");
});

test("failed required child cannot produce successful parent", async () => {
  const { owner, task } = await running();
  const child = await store.spawn(
    owner,
    task.id,
    worker,
    task.generation,
    "portfolio",
    "dispatch",
    { input: "value" },
  );
  const c = (await store.claim(owner, worker))!.task;
  await store.finish(owner, child.id, worker, c.generation, "failed", {
    reason: "provider_unavailable",
  });
  await assert.rejects(
    store.finish(owner, task.id, worker, task.generation, "completed", {}),
    error("required_child_failed"),
  );
  await store.finish(owner, task.id, worker, task.generation, "failed", {
    text: "partial only",
  });
});

test("completed tasks do not change on stop and final message/event commit together", async () => {
  const { owner, task } = await running();
  await store.finish(owner, task.id, worker, task.generation, "completed", {
    text: "final",
  });
  assert.equal((await store.stop(owner, task.id)).status, "completed");
  const msg = (
    await pool.query(
      "SELECT content FROM messages WHERE task_id=$1 AND role='assistant'",
      [task.id],
    )
  ).rows;
  assert.deepEqual(msg, [{ content: "final" }]);
  const events = await store.events(owner, task.id);
  assert.equal(events.at(-1).data.output.text, "final");
});

test("data facts reject accidental update or deletion", async () => {
  const { owner } = await setup(),
    id = randomUUID();
  await pool.query(
    "INSERT INTO datasets(id,owner_id,metadata,payload,content_hash) VALUES($1,$2,$3,$4,$5)",
    [id, owner, "{}", "[]", "hash"],
  );
  await assert.rejects(
    pool.query("UPDATE datasets SET payload=$2 WHERE id=$1", [id, "[1]"]),
    /immutable_fact/,
  );
  await assert.rejects(
    pool.query("DELETE FROM datasets WHERE id=$1", [id]),
    /immutable_fact/,
  );
});

test("persistent snapshot restores final messages after event retention expires", async () => {
  const { owner, task } = await running();
  await store.finish(owner, task.id, worker, task.generation, "completed", {
    text: "durable answer",
  });
  const before = await store.snapshot(owner, task.id);
  await pool.query("DELETE FROM task_events WHERE task_id=$1", [task.id]);
  const restored = await store.snapshot(owner, task.id);
  // Incremental progress expires with its events; durable state must not.
  const { progress: _before, ...durable } = before;
  const { progress: _after, ...after } = restored;
  assert.deepEqual(after, durable);
  assert.equal(restored.messages.at(-1).content, "durable answer");
  assert.ok(restored.lastSequence > 0);
  const other = await setup();
  await assert.rejects(
    store.snapshot(other.owner, task.id),
    error("not_found"),
  );
});

test("upstream identity can only be bound after preparation and never replaced", async () => {
  const { owner, task } = await running();
  await assert.rejects(
    store.bindUpstream(owner, task.id, worker, task.generation, "run1"),
    error("execution_not_prepared"),
  );
  await store.prepareExecution(
    owner,
    task.id,
    worker,
    task.generation,
    { input: "one" },
    "profile",
    "session",
  );
  await store.bindUpstream(owner, task.id, worker, task.generation, "run1");
  await store.bindUpstream(owner, task.id, worker, task.generation, "run1");
  await assert.rejects(
    store.bindUpstream(owner, task.id, worker, task.generation, "run2"),
    error("upstream_identity_conflict"),
  );
});

test("queue admission is bounded per owner under concurrency and preserves idempotent replay", async () => {
  const a = await setup(),
    b = await setup();
  const attempts = await Promise.allSettled(
    Array.from({ length: 20 }, (_, i) =>
      store.submit(a.owner, a.thread.id, `quota-${i}`, {
        // Research wording so the admission cap applies; consults bypass it.
        // The trailing number is parsed below to rebuild the original key.
        input: `research NVDA ${i}`,
      }),
    ),
  );
  const admitted = attempts.flatMap((item) =>
    item.status === "fulfilled" ? [item.value] : [],
  );
  const rejected = attempts.filter((item) => item.status === "rejected");
  assert.equal(admitted.length, 10);
  assert.equal(rejected.length, 10);
  assert.ok(
    rejected.every(
      (item) =>
        item.reason instanceof DomainError &&
        item.reason.code === "queue_full" &&
        item.reason.httpStatus === 429,
    ),
  );
  const root = (await store.claim(a.owner, worker))!.task;
  await store.spawn(
    a.owner,
    root.id,
    worker,
    root.generation,
    "prediction",
    "child-at-capacity",
    { input: "specialist" },
  );
  await store.defer(
    a.owner,
    root.id,
    worker,
    root.generation,
    "unknown",
    "upstream_execution_unknown",
    0,
  );
  const before = await store.snapshot(a.owner, root.id);
  await assert.rejects(
    store.submit(a.owner, a.thread.id, "extra", { input: "分析 AAPL 近期走势" }),
    error("queue_full"),
  );
  assert.deepEqual(await store.snapshot(a.owner, root.id), before);
  const first = admitted[0]!;
  // Rebuild the original key from the trailing index in "research NVDA <i>".
  const firstKey = `quota-${String(first.input.input).split(" ")[2]}`;
  assert.equal(
    (await store.submit(a.owner, a.thread.id, firstKey, first.input)).id,
    first.id,
  );
  await assert.rejects(
    store.submit(a.owner, a.thread.id, firstKey, { input: "分析 MSFT 近期走势" }),
    error("idempotency_conflict"),
  );
  assert.equal(
    (
      await store.submit(b.owner, b.thread.id, "other-owner", {
        input: "分析 NVDA 近期走势",
      })
    ).status,
    "queued",
  );
  const queued = admitted.find((task) => task.id !== root.id)!;
  await store.stop(a.owner, queued.id);
  assert.equal(
    (
      await store.submit(a.owner, a.thread.id, "after-cancel", {
        input: "new research",
      })
    ).status,
    "queued",
  );
  assert.equal((await store.get(a.owner, root.id)).status, "unknown");
});

test("group tasks select only their own Profile sessions", async () => {
  const { ExecutionContextStore } =
    await import("../../packages/server/src/execution-context.js");
  const { owner, thread } = await setup();
  const contexts = new ExecutionContextStore(pool);
  const group = await contexts.group(owner, "cli_test", "oc_group");
  const same = await contexts.group(owner, "cli_test", "oc_group");
  assert.equal(group.id, same.id);
  const otherGroup = await contexts.group(owner, "cli_test", "oc_other");
  assert.notEqual(group.id, otherGroup.id);
  await pool.query(
    "INSERT INTO profile_instances(id,owner_id,role) VALUES($1,$2,'research')",
    [randomUUID(), owner],
  );
  await pool.query(
    "UPDATE thread_contexts SET context_id=$1 WHERE thread_id=$2",
    [group.id, thread.id],
  );
  assert.equal((await contexts.thread(owner, thread.id)).kind, "group");
  await assert.rejects(
    contexts.thread(randomUUID(), thread.id),
    error("not_found"),
  );
  await store.submit(owner, thread.id, randomUUID(), {
    input: "分析 NVDA 近期走势",
  });
  const claim = (await store.claim(owner, worker))!;
  const execution = await store.executionContext(
    owner,
    claim.task.id,
    worker,
    claim.task.generation,
  );
  const profile = (
    await pool.query("SELECT context_id FROM profile_instances WHERE id=$1", [
      execution.profileId,
    ])
  ).rows[0];
  assert.equal(profile.context_id, group.id);
  assert.equal(
    (
      await pool.query(
        "SELECT count(*)::int AS n FROM profile_sessions WHERE thread_id=$1",
        [thread.id],
      )
    ).rows[0].n,
    1,
  );
});

test("single-root admission serializes different owners and unknown retains capacity", async () => {
  // Other tests intentionally leave tasks active; isolate this deployment policy fixture.
  const isolated = `capacity_${randomUUID().replaceAll("-", "")}`;
  await admin.query(`CREATE SCHEMA ${isolated}`);
  const scoped = new Pool({
    connectionString: url,
    options: `-c search_path=${isolated}`,
  });
  const previous = process.env.RESEARCH_SINGLE_ROOT;
  try {
    await migrate(scoped);
    const tasks = new TaskStore(scoped);
    const owners = [randomUUID(), randomUUID()];
    for (const owner of owners) {
      await scoped.query("INSERT INTO users(id) VALUES($1)", [owner]);
      const thread = await tasks.createThread(owner);
      await tasks.submit(owner, thread.id, randomUUID(), {
        input: "capacity test",
      });
    }
    process.env.RESEARCH_SINGLE_ROOT = "1";
    const claims = await Promise.all(
      owners.map((owner) => tasks.take(owner, worker)),
    );
    assert.equal(claims.filter(Boolean).length, 1);
    const active = claims.find(Boolean)!;
    const waiting = owners.find((owner) => owner !== active.owner_id)!;
    await tasks.defer(
      active.owner_id,
      active.id,
      worker,
      active.generation,
      "unknown",
      "test",
      0,
    );
    assert.equal(await tasks.take(waiting, worker), null);
    await scoped.query("UPDATE tasks SET status='completed' WHERE id=$1", [
      active.id,
    ]);
    await scoped.query(
      "UPDATE tasks SET deadline=now()-interval '1 hour' WHERE owner_id=$1",
      [waiting],
    );
    const next = await tasks.take(waiting, worker);
    assert.ok(next);
    assert.ok(next.deadline.getTime() > Date.now() + 590000);
  } finally {
    if (previous === undefined) delete process.env.RESEARCH_SINGLE_ROOT;
    else process.env.RESEARCH_SINGLE_ROOT = previous;
    await scoped.end();
    await admin.query(`DROP SCHEMA ${isolated} CASCADE`);
  }
});

test("acceptance reservation survives unknown outcome and prevents research admission", async () => {
  const { reserveAcceptance, finishAcceptance } =
    await import("../../packages/server/src/acceptance-capacity.js");
  const isolated = `acceptance_${randomUUID().replaceAll("-", "")}`;
  await admin.query(`CREATE SCHEMA ${isolated}`);
  const scoped = new Pool({
    connectionString: url,
    options: `-c search_path=${isolated}`,
  });
  const previous = process.env.RESEARCH_SINGLE_ROOT;
  try {
    await migrate(scoped);
    const owner = randomUUID();
    await scoped.query("INSERT INTO users(id) VALUES($1)", [owner]);
    const store = new TaskStore(scoped);
    const thread = await store.createThread(owner);
    await store.submit(owner, thread.id, randomUUID(), {
      input: "waiting for acceptance",
    });
    process.env.RESEARCH_SINGLE_ROOT = "1";
    const claims = await Promise.all([
      reserveAcceptance(scoped, owner),
      reserveAcceptance(scoped, owner),
    ]);
    assert.equal(claims.filter(Boolean).length, 1);
    const id = claims.find(Boolean)!;
    assert.equal(await store.take(owner, worker), null);
    await finishAcceptance(scoped, id, false);
    assert.equal(
      (await scoped.query("SELECT status FROM acceptance_capacity")).rows[0]
        .status,
      "unknown",
    );
    assert.equal(await reserveAcceptance(scoped, owner), undefined);
    assert.equal(await store.take(owner, worker), null);
    await finishAcceptance(scoped, id, true);
    assert.ok(await store.take(owner, worker));
    assert.equal(await reserveAcceptance(scoped, owner), undefined);
  } finally {
    if (previous === undefined) delete process.env.RESEARCH_SINGLE_ROOT;
    else process.env.RESEARCH_SINGLE_ROOT = previous;
    await scoped.end();
    await admin.query(`DROP SCHEMA ${isolated} CASCADE`);
  }
});
