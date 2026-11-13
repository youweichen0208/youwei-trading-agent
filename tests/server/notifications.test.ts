import { after, before, test } from "node:test";
import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { Pool } from "pg";
import { migrate } from "../../packages/server/src/migrate.js";
import {
  NotificationStore,
  deliverNextFeishu,
  splitFeishuText,
} from "../../packages/server/src/notifications.js";
const url = process.env.TEST_DATABASE_URL;
if (!url || !new URL(url).pathname.endsWith("_test"))
  throw Error("Dedicated test database required");
const schema = `test_${randomUUID().replaceAll("-", "")}`;
const admin = new Pool({ connectionString: url }),
  pool = new Pool({
    connectionString: url,
    options: `-c search_path=${schema}`,
  });
const owner = randomUUID(),
  store = new NotificationStore(pool),
  app = "test-app";
before(async () => {
  await admin.query(`CREATE SCHEMA ${schema}`);
  await migrate(pool);
  await pool.query(
    "INSERT INTO users(id,email,password_hash) VALUES($1,$2,$3)",
    [owner, `${owner}@test.invalid`, "fixture"],
  );
  await pool.query(
    "INSERT INTO feishu_identities(app_id,open_id,owner_id) VALUES($1,$2,$3)",
    [app, "test-recipient", owner],
  );
});
after(async () => {
  await pool.end();
  await admin.query(`DROP SCHEMA ${schema} CASCADE`);
  await admin.end();
});

test("proactive delivery does not fabricate inbox; ambiguous retry keeps the same UUID", async () => {
  const first = await store.enqueue(owner, "forecast_changed", "forecast-1", {
    title: "NVDA 判断更新",
    text: "新旧判断对比",
    href: "/lab",
  });
  const repeated = await store.enqueue(
    owner,
    "forecast_changed",
    "forecast-1",
    { title: "different", text: "must not overwrite" },
  );
  assert.equal(first.id, repeated.id);
  assert.equal(repeated.payload.title, "NVDA 判断更新");
  assert.equal(await store.prepareFeishu(app, "https://test.invalid"), 1);
  assert.equal(await store.prepareFeishu(app, "https://test.invalid"), 0);
  assert.equal(
    (await pool.query("SELECT count(*)::int n FROM feishu_inbox")).rows[0].n,
    0,
  );
  const attempts: any[] = [];
  let fail = true;
  const client = {
    im: {
      message: {
        reply: async () => {
          throw Error("unexpected reply");
        },
        create: async (args: any) => {
          attempts.push(args);
          if (fail) {
            fail = false;
            throw Error("response lost");
          }
          return { code: 0, data: { message_id: "delivered" } };
        },
      },
    },
  };
  const now = new Date(Date.now() + 1000);
  await assert.rejects(
    deliverNextFeishu(pool, client, app, now),
    /response lost/,
  );
  assert.equal(
    (await deliverNextFeishu(pool, client, app, new Date(+now + 31000))).status,
    "sent",
  );
  assert.equal(attempts.length, 2);
  assert.equal(attempts[0].data.uuid, attempts[1].data.uuid);
  assert.equal(attempts[0].params.receive_id_type, "open_id");
  assert.equal(attempts[0].data.receive_id, "test-recipient");
  assert.equal(
    (
      await pool.query(
        "SELECT message_id FROM feishu_outbox WHERE notification_id=$1",
        [first.id],
      )
    ).rows[0].message_id,
    "delivered",
  );
});

test("notification ownership and disabled delivery are enforced", async () => {
  const item = await store.enqueue(owner, "daily_digest", "digest-2", {
    title: "日报",
    text: "研究完成",
  });
  await assert.rejects(store.markRead(randomUUID(), item.id), /not_found/);
  assert.ok((await store.markRead(owner, item.id)).read_at);
  assert.equal(await store.prepareFeishu(app, "https://test.invalid"), 1);
  await pool.query(
    "INSERT INTO research_settings(owner_id,payload) VALUES($1,'{\"feishuNotifications\":false}')",
    [owner],
  );
  assert.equal(await store.prepareFeishu(app, "https://test.invalid"), 0);
  const unexpected = async () => {
    throw Error("disabled notifications must not be sent");
  };
  assert.equal(
    (
      await deliverNextFeishu(
        pool,
        { im: { message: { reply: unexpected, create: unexpected } } },
        app,
        new Date(Date.now() + 1000),
      )
    ).status,
    "idle",
  );
});

test("UTF-8 notification parts preserve complete characters and fit transport budget", () => {
  const original = "股票🧪".repeat(8000),
    parts = splitFeishuText(original);
  assert.ok(parts.length > 1);
  assert.equal(parts.join(""), original);
  assert.ok(
    parts.every((text) => Buffer.byteLength(JSON.stringify({ text })) <= 12000),
  );
});

test("finished research never delivers stale queued or running receipts", async () => {
  const chat = randomUUID(),
    key = randomUUID();
  await pool.query("INSERT INTO feishu_chats(key,owner_id) VALUES($1,$2)", [
    chat,
    owner,
  ]);
  await pool.query(
    "INSERT INTO feishu_inbox(key,chat_key,message_id,chat_id,input,processed,finished) VALUES($1,$2,$1,$2,'fixture',true,true)",
    [key, chat],
  );
  for (const phase of [
    "progress_queued",
    "progress_running",
    "progress_stopping",
  ])
    await pool.query(
      "INSERT INTO feishu_outbox(id,inbox_key,phase,part,text,next_attempt) VALUES($1,$2,$3,0,'stale',now()-interval '1 day')",
      [randomUUID(), key, phase],
    );
  const sent: unknown[] = [];
  const send = async (args: unknown) => {
    sent.push(args);
    return { code: 0, data: { message_id: "fixture" } };
  };
  const client = { im: { message: { create: send, reply: send } } };
  for (let i = 0; i < 3; i++) await deliverNextFeishu(pool, client, app);
  assert.equal(sent.length, 0);
});
