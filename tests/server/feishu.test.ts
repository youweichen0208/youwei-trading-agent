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

test("greetings finish without research admission and duplicate receipt is idempotent", async () => {
  const { acceptQuickReply, quickReplyText } =
    await import("../../packages/server/src/feishu-quick-reply.js");
  const key = randomUUID(),
    chat = randomUUID();
  await pool.query("INSERT INTO feishu_chats(key,owner_id) VALUES($1,$2)", [
    chat,
    owner,
  ]);
  await pool.query(
    "INSERT INTO feishu_inbox(key,chat_key,message_id,chat_id,input) VALUES($1,$2,$1,$2,'hello')",
    [key, chat],
  );
  let replies = 0;
  const enqueue = async () => {
    replies++;
  };
  for (let i = 0; i < 2; i++)
    await transaction(pool, (c) => acceptQuickReply(c, key, "hello", enqueue));
  const row = (
    await pool.query(
      "SELECT processed,finished,task_id FROM feishu_inbox WHERE key=$1",
      [key],
    )
  ).rows[0];
  assert.deepEqual(row, { processed: true, finished: true, task_id: null });
  assert.equal(replies, 1);
  assert.ok(quickReplyText("你是谁？"));
  assert.equal(quickReplyText("你好，分析一下 AAPL"), null);
  assert.equal(quickReplyText("查看 AAPL 最新预测"), null);
});

test("saved research bypasses models and cannot cross personal/group boundaries", async () => {
  const { acceptQuickReply, savedResearchSymbol } =
    await import("../../packages/server/src/feishu-quick-reply.js");
  const { ExecutionContextStore } =
    await import("../../packages/server/src/execution-context.js");
  const group = await new ExecutionContextStore(pool).group(
    owner,
    "test-app",
    "test-group",
  );
  const store = new TaskStore(pool),
    thread = await store.createThread(owner);
  const task = await store.submit(owner, thread.id, randomUUID(), {
    input: "fixture",
    symbol: "AAPL",
  });
  await pool.query(
    "INSERT INTO results(id,owner_id,task_id,kind,payload,lineage) VALUES($1,$2,$3,'research_summary',$4,'{}')",
    [
      randomUUID(),
      owner,
      task.id,
      { symbol: "AAPL", text: "PRIVATE_SENTINEL" },
    ],
  );
  for (const horizon of [5, 20]) {
    const result = randomUUID();
    await pool.query(
      "INSERT INTO results(id,owner_id,task_id,kind,payload,lineage) VALUES($1,$2,$3,'forecast','{}','{}')",
      [result, owner, task.id],
    );
    await pool.query(
      `INSERT INTO forecasts(id,owner_id,task_id,result_id,operation_key,request_hash,symbol,horizon,event_key,research_context_id,published_at,start_session,target_session,start_open_at,target_close_at,evaluate_after,payload)
      VALUES($1,$2,$3,$4,$5,'fixture','AAPL',$6,'fixture',$7,now(),'2026-01-02','2026-02-02','2026-01-02T14:30:00Z','2026-02-02T21:00:00Z','2026-02-02T21:20:00Z',$8)`,
      [
        randomUUID(),
        owner,
        task.id,
        result,
        `forecast-${horizon}`,
        horizon,
        randomUUID(),
        {
          meanReturn: "0.05",
          upProbability: "0.60",
          thesis: "FORECAST_SENTINEL",
        },
      ],
    );
  }
  const before = (await pool.query("SELECT count(*)::int AS n FROM tasks"))
    .rows[0].n;
  for (const context of [owner, group.id]) {
    const key = randomUUID(),
      chat = randomUUID();
    await pool.query(
      "INSERT INTO feishu_chats(key,owner_id,context_id) VALUES($1,$2,$3)",
      [chat, owner, context],
    );
    await pool.query(
      "INSERT INTO feishu_inbox(key,chat_key,message_id,chat_id,input) VALUES($1,$2,$1,$2,'查看 AAPL 研究')",
      [key, chat],
    );
    let reply = "";
    await transaction(pool, (c) =>
      acceptQuickReply(c, key, "查看 AAPL 研究", async (_c, _k, _p, text) => {
        reply = text;
      }),
    );
    assert.equal(reply.includes("PRIVATE_SENTINEL"), context === owner);
    assert.equal(reply.includes("FORECAST_SENTINEL"), context === owner);
    if (context === owner) {
      assert.ok(
        reply.includes("5 交易日预测") && reply.includes("20 交易日预测"),
      );
      assert.ok(reply.includes("5.0%") && reply.includes("60.0%"));
      assert.ok(reply.includes("期限已结束"));
    } else assert.ok(reply.includes("还没有"));
  }
  assert.equal(
    (await pool.query("SELECT count(*)::int AS n FROM tasks")).rows[0].n,
    before,
  );
  assert.equal(savedResearchSymbol("重新分析 AAPL"), null);
  assert.equal(savedResearchSymbol("查看 AAPL 最新预测"), "AAPL");
});
