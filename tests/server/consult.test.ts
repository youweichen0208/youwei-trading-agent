import { after, test } from "node:test";
import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { Pool } from "pg";
import { migrate } from "../../packages/server/src/migrate.js";
import { AuthStore } from "../../packages/server/src/auth.js";
import { TaskStore } from "../../packages/server/src/tasks.js";
import { createHandler } from "../../packages/server/src/http.js";

const url = process.env.TEST_DATABASE_URL;
if (!url || !new URL(url).pathname.endsWith("_test"))
  throw new Error("Dedicated TEST_DATABASE_URL ending in _test required");
const admin = new Pool({ connectionString: url });
const origin = "http://localhost:3400";

const schemas: string[] = [];
/** Each test gets its own schema so migrations and drops cannot collide. */
async function isolated() {
  const schema = `consult_${randomUUID().replaceAll("-", "")}`;
  await admin.query(`CREATE SCHEMA ${schema}`);
  schemas.push(schema);
  const pool = new Pool({ connectionString: url, options: `-c search_path=${schema}` });
  await migrate(pool);
  return pool;
}
after(async () => {
  for (const schema of schemas) await admin.query(`DROP SCHEMA ${schema} CASCADE`);
  await admin.end();
});

test("a question about saved knowledge becomes a consult, not research", async (t) => {
  const pool = await isolated();
  const handle = createHandler(pool, origin);

  const auth = new AuthStore(pool);
  const user = await auth.register(`${randomUUID()}@consult.test`, "consult-test-password");
  const { token } = await auth.login(user.email!, "consult-test-password");
  const cookie = `alpha_session=${token}`;
  const post = async (path: string, body: unknown) =>
    handle(
      new Request(`${origin}/api/${path}`, {
        method: "POST",
        headers: {
          origin,
          cookie,
          "Content-Type": "application/json",
          "Idempotency-Key": randomUUID(),
        },
        body: JSON.stringify(body),
      }),
    );

  const threadResponse = await handle(
    new Request(`${origin}/api/threads`, { method: "POST", headers: { origin, cookie } }),
  );
  const thread = await threadResponse.json();
  assert.equal(threadResponse.status, 201, JSON.stringify(thread));

  const accepted = await post(
    "research",
    { threadId: thread.id, input: "可以帮我看一下我当前的知识沉淀内容吗" },
  );
  const acceptedBody = await accepted.json();
  assert.equal(accepted.status, 202, JSON.stringify(acceptedBody));
  const { taskId } = acceptedBody;

  const row = (await pool.query("SELECT role, origin FROM tasks WHERE id=$1", [taskId])).rows[0];
  assert.equal(row.role, "consult", "a product question must not run the research pipeline");
  assert.equal(row.origin, "interactive");

  // Still a normal user message that the thread renders.
  const message = (
    await pool.query("SELECT content FROM messages WHERE task_id=$1 AND role='user'", [taskId])
  ).rows[0];
  assert.match(message.content, /知识沉淀/);

  // It must not consume one of the ten interactive research slots: fill the
  // research queue, then confirm a consult still gets in while a research
  // submission is rejected.
  const store: TaskStore = new TaskStore(pool);
  for (let i = 0; i < 10; i += 1)
    await store.submit(user.id!, thread.id, randomUUID(), {
      input: "分析 NVDA 近期走势",
    });
  await assert.rejects(
    () => store.submit(user.id!, thread.id, randomUUID(), { input: "分析 AAPL 近期走势" }),
    /queue_full/,
  );
  const extra = await store.createThread(user.id!);
  const consult = await store.submit(user.id!, extra.id, randomUUID(), {
    input: "帮我查一下我的研究记录",
  });
  assert.equal(consult.role, "consult", "a consult must bypass the research cap");
});

test("a stock question stays a research task", async (t) => {
  const pool = await isolated();
  const auth = new AuthStore(pool);
  const user = await auth.register(`${randomUUID()}@research2.test`, "consult-test-password");
  const store = new TaskStore(pool);
  const thread = await store.createThread(user.id!);
  const task = await store.submit(user.id!, thread.id, randomUUID(), {
    input: "查一下特斯拉最近的走势",
  });
  assert.equal(task.role, "research");
});
