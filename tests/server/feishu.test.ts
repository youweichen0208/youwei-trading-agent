import { before, after, test } from "node:test";
import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { Pool } from "pg";
import { migrate } from "../../packages/server/src/migrate.js";
import { transaction } from "../../packages/server/src/db.js";
import { TaskStore } from "../../packages/server/src/tasks.js";
const url = process.env.TEST_DATABASE_URL;
if (!url || !new URL(url).pathname.endsWith("_test"))
  throw Error("test database required");
const schema = `test_${randomUUID().replaceAll("-", "")}`;
const admin = new Pool({ connectionString: url });
const pool = new Pool({
  connectionString: url,
  options: `-c search_path=${schema}`,
});
const owner = randomUUID();
before(async () => {
  await admin.query(`CREATE SCHEMA ${schema}`);
  await migrate(pool);
  await pool.query(
    "INSERT INTO users(id,email,password_hash) VALUES($1,$2,$3)",
    [owner, `${owner}@test.invalid`, "fixture"],
  );
});
after(async () => {
  await pool.end();
  await admin.query(`DROP SCHEMA ${schema} CASCADE`);
  await admin.end();
});
test("channel task admission participates in outer rollback", async () => {
  await assert.rejects(
    transaction(pool, async (c) => {
      const store = new TaskStore(pool, c);
      const thread = await store.createThread(owner);
      await store.submit(owner, thread.id, "feishu:rollback", {
        input: "test",
      });
      throw Error("simulate crash");
    }),
  );
  assert.equal(
    (await pool.query("SELECT count(*)::int AS n FROM tasks")).rows[0].n,
    0,
  );
  assert.equal(
    (await pool.query("SELECT count(*)::int AS n FROM threads")).rows[0].n,
    0,
  );
});
test("channel retry preserves a single task and message; stop works in transaction", async () => {
  await transaction(pool, async (c) => {
    const store = new TaskStore(pool, c);
    const thread = await store.createThread(owner);
    const first = await store.submit(owner, thread.id, "feishu:duplicate", {
      input: "test",
    });
    const second = await store.submit(owner, thread.id, "feishu:duplicate", {
      input: "test",
    });
    assert.equal(first.id, second.id);
    await store.stop(owner, first.id);
  });
  assert.equal(
    (await pool.query("SELECT count(*)::int AS n FROM messages")).rows[0].n,
    1,
  );
  assert.equal(
    (await pool.query("SELECT status FROM tasks")).rows[0].status,
    "cancelled",
  );
});

test("cards load task-bound historical artifacts and reject another owner", async () => {
  const { loadTaskCards } =
    await import("../../packages/server/src/feishu-cards.js");
  const store = new TaskStore(pool),
    thread = await store.createThread(owner);
  const task = await store.submit(owner, thread.id, "cards:history", {
    input: "fixture",
  });
  const dataset = randomUUID();
  await pool.query(
    "INSERT INTO datasets(id,owner_id,metadata,payload,content_hash) VALUES($1,$2,$3,$4,$5)",
    [
      dataset,
      owner,
      JSON.stringify({ kind: "ohlcv", source: "synthetic fixture" }),
      JSON.stringify([{ session: "2026-01-02", close: "100", volume: "0" }]),
      "fixture",
    ],
  );
  await pool.query(
    "INSERT INTO task_artifacts(id,owner_id,root_id,source_task_id,body) VALUES($1,$2,$3,$3,$4)",
    [
      randomUUID(),
      owner,
      task.id,
      JSON.stringify({
        component: "stock_chart",
        title: "Historical fixture",
        datasetRef: dataset,
      }),
    ],
  );
  await store.submit(owner, thread.id, "cards:newer", {
    input: "new revision",
  });
  const c = await pool.connect();
  try {
    assert.equal(
      (await loadTaskCards(c, owner, task.id, "https://example.com")).length,
      1,
    );
    assert.equal(
      (await loadTaskCards(c, randomUUID(), task.id, "https://example.com"))
        .length,
      0,
    );
  } finally {
    c.release();
  }
});
