import { after, before, test } from "node:test";
import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { Pool } from "pg";
import { migrate } from "../../packages/server/src/migrate.js";
import { createHandler } from "../../packages/server/src/http.js";
import { TaskStore } from "../../packages/server/src/tasks.js";
import { TaskWorker } from "../../packages/server/src/worker.js";
import { PersonalNoteStore } from "../../packages/server/src/personal-notes.js";
import { ExecutionContextStore } from "../../packages/server/src/execution-context.js";
import type {
  Run,
  RunRequest,
  Runtime,
} from "../../packages/hermes-adapter/src/client.js";

const url = process.env.TEST_DATABASE_URL;
if (!url || !new URL(url).pathname.endsWith("_test"))
  throw new Error("Dedicated TEST_DATABASE_URL ending in _test required");
const schema = `test_${randomUUID().replaceAll("-", "")}`;
const admin = new Pool({ connectionString: url }),
  pool = new Pool({
    connectionString: url,
    options: `-c search_path=${schema}`,
  });
const store = new TaskStore(pool),
  notes = new PersonalNoteStore(pool);
const origin = "http://localhost:3400",
  handle = createHandler(pool, origin);
const request = (
  path: string,
  method = "GET",
  body?: unknown,
  cookie?: string,
) =>
  handle(
    new Request(origin + "/api/" + path, {
      method,
      headers: {
        origin,
        "content-type": "application/json",
        ...(cookie ? { cookie } : {}),
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
const list = async (cookie: string) =>
  (await (await request("personal-notes", "GET", undefined, cookie)).json()) as any[];

test("one account reads, edits and deletes only its own notes", async () => {
  const a = await account(),
    b = await account();
  const created = await request(
    "personal-notes",
    "POST",
    { title: "我的风格", body: "偏好现金流稳定的成长股，回避高杠杆。" },
    a,
  );
  assert.equal(created.status, 200);
  const note = (await created.json()) as any;
  assert.equal(note.title, "我的风格");
  assert.equal(note.pinned, false);
  assert.equal(note.useInAgent, true);
  assert.equal(note.symbol, null);
  assert.deepEqual(note.tags, []);
  assert.equal((await list(a)).length, 1);
  assert.deepEqual(await list(b), []);
  assert.equal(
    (
      await request(
        `personal-notes/${note.id}`,
        "PUT",
        { title: "被篡改" },
        b,
      )
    ).status,
    404,
  );
  assert.equal(
    (await request(`personal-notes/${note.id}`, "DELETE", undefined, b)).status,
    404,
  );
  const kept = await list(a);
  assert.equal(kept.length, 1);
  assert.equal(kept[0].title, "我的风格");
});

test("formatted bodies keep a plain-text mirror for the agent", async () => {
  const cookie = await account();
  const created = (await (
    await request(
      "personal-notes",
      "POST",
      {
        title: "带格式的笔记",
        body: "偏好现金流稳定的成长股",
        bodyHtml: "<p><strong>偏好现金流稳定的成长股</strong></p>",
      },
      cookie,
    )
  ).json()) as any;
  assert.equal(
    created.bodyHtml,
    "<p><strong>偏好现金流稳定的成长股</strong></p>",
  );
  assert.equal(created.body, "偏好现金流稳定的成长股");
  const cleared = (await (
    await request(
      `personal-notes/${created.id}`,
      "PUT",
      { bodyHtml: null },
      cookie,
    )
  ).json()) as any;
  assert.equal(cleared.bodyHtml, null);
  assert.equal(cleared.body, "偏好现金流稳定的成长股");
  assert.equal(
    (
      await request(
        "personal-notes",
        "POST",
        { title: "过长", body: "正文", bodyHtml: "a".repeat(60001) },
        cookie,
      )
    ).status,
    400,
  );
});

test("invalid note input is rejected before storage", async () => {
  const cookie = await account();
  const invalid = [
    { title: "   ", body: "正文" },
    { title: "标题", body: "a".repeat(20001) },
    { title: "标题", body: "正文", tags: Array.from({ length: 11 }, () => "标签") },
    { title: "标题", body: "正文", symbol: "nvda" },
    { title: "标题", body: "正文", ownerId: randomUUID() },
  ];
  for (const body of invalid)
    assert.equal(
      (await request("personal-notes", "POST", body, cookie)).status,
      400,
    );
  const note = (
    await (
      await request("personal-notes", "POST", { title: "标题", body: "正文" }, cookie)
    ).json()
  ) as any;
  assert.equal(
    (await request(`personal-notes/${note.id}`, "PUT", {}, cookie)).status,
    400,
  );
  assert.deepEqual(await list(cookie), await list(cookie));
  assert.equal((await list(cookie)).length, 1);
});

test("pinned notes lead the list, symbol clears, delete removes", async () => {
  const cookie = await account();
  const create = async (body: Record<string, unknown>) =>
    (await (
      await request("personal-notes", "POST", body, cookie)
    ).json()) as any;
  const first = await create({
    title: "估值纪律",
    body: "买入前写下失效条件。",
    symbol: "NVDA",
    tags: ["估值", "估值", "风险"],
  });
  assert.deepEqual(first.tags, ["估值", "风险"]);
  await create({ title: "复盘模板", body: "先看方向，再看幅度与时间。" });
  const pinned = await create({
    title: "仓位上限",
    body: "单票不超过两成。",
    pinned: true,
  });
  const ordered = await list(cookie);
  assert.equal(ordered.length, 3);
  assert.equal(ordered[0].id, pinned.id);
  const cleared = (await (
    await request(`personal-notes/${first.id}`, "PUT", { symbol: null }, cookie)
  ).json()) as any;
  assert.equal(cleared.symbol, null);
  assert.equal(cleared.title, "估值纪律");
  assert.equal(
    (await request(`personal-notes/${pinned.id}`, "DELETE", undefined, cookie))
      .status,
    200,
  );
  const remaining = await list(cookie);
  assert.equal(remaining.length, 2);
  assert.ok(!remaining.some((row) => row.id === pinned.id));
});

class RecordingRuntime implements Runtime {
  identity = "test-runtime";
  retentionMs = 86_400_000;
  requests: RunRequest[] = [];
  runs = new Map<string, Run>();
  async create(request: RunRequest, key: string) {
    this.requests.push(request);
    if (!this.runs.has(key))
      this.runs.set(key, {
        run_id: `run_${key}`,
        session_id: request.session_id,
        status: "running",
      });
    return this.runs.get(key)!.run_id;
  }
  async get(id: string) {
    return { ...[...this.runs.values()].find((run) => run.run_id === id)! };
  }
  async stop() {}
}

async function accountWithProfiles(notesStore?: PersonalNoteStore) {
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
  const runtime = new RecordingRuntime();
  const worker = new TaskWorker(
    store,
    (id) => (profileIds.includes(id) ? runtime : undefined),
    undefined,
    notesStore,
  );
  const submit = async (threadId: string, input: Record<string, unknown>) => {
    const task = await store.submit(owner, threadId, randomUUID(), input);
    await pool.query(
      "UPDATE tasks SET next_attempt_at=next_attempt_at-interval '1 day' WHERE id=$1",
      [task.id],
    );
    assert.equal(await worker.once(owner), true);
    return task;
  };
  return { owner, runtime, submit };
}

test("a personal thread freezes the notebook into the submitted instructions", async () => {
  const { owner, runtime, submit } = await accountWithProfiles();
  const body = `偏好低估值分红股 ${randomUUID()}`;
  await notes.create(owner, { title: "我的风格", body, symbol: "NVDA" });
  const muted = `不应注入 ${randomUUID()}`;
  await notes.create(owner, {
    title: "已关闭",
    body: muted,
    useInAgent: false,
  });
  const thread = await store.createThread(owner);
  await submit(thread.id, { input: "研究 NVDA" });
  const instructions = runtime.requests[0]!.instructions;
  assert.match(instructions, /PERSONAL NOTEBOOK/);
  assert.match(instructions, /### 我的风格/);
  assert.match(instructions, new RegExp(body));
  assert.ok(instructions.includes("股票：NVDA"));
  assert.ok(!instructions.includes(muted));
});

test("shadow trials and group conversations never receive the notebook", async () => {
  const trial = await accountWithProfiles();
  const secret = `个人心得 ${randomUUID()}`;
  await notes.create(trial.owner, { title: "试验", body: secret });
  const trialThread = await store.createThread(trial.owner);
  await trial.submit(trialThread.id, {
    input: "研究 NVDA 影子试验",
    trialId: randomUUID(),
  });
  const trialInstructions = trial.runtime.requests[0]!.instructions;
  assert.match(trialInstructions, /isolated shadow experiment arm/);
  assert.ok(!trialInstructions.includes(secret));
  assert.ok(!trialInstructions.includes("PERSONAL NOTEBOOK"));

  const group = await accountWithProfiles();
  const groupSecret = `群聊不可见 ${randomUUID()}`;
  await notes.create(group.owner, { title: "群聊", body: groupSecret });
  const context = await new ExecutionContextStore(pool).group(
    group.owner,
    "cli_test",
    `oc_${randomUUID().replaceAll("-", "")}`,
  );
  const groupThread = await store.createThread(group.owner, context.id);
  const groupTask = await store.submit(group.owner, groupThread.id, randomUUID(), {
    input: "研究 NVDA 群聊问题",
  });
  await pool.query(
    "UPDATE tasks SET next_attempt_at=next_attempt_at-interval '1 day' WHERE id=$1",
    [groupTask.id],
  );
  // Group profiles are provisioned per context; this worker accepts any Profile.
  assert.equal(
    await new TaskWorker(store, () => group.runtime).once(group.owner),
    true,
  );
  const groupInstructions = group.runtime.requests[0]!.instructions;
  assert.ok(!groupInstructions.includes(groupSecret));
  assert.ok(!groupInstructions.includes("PERSONAL NOTEBOOK"));
});

test("a long note is truncated to its prompt budget", async () => {
  const { owner, runtime, submit } = await accountWithProfiles();
  const body = "长期复盘要点".repeat(700).slice(0, 3000);
  assert.equal(body.length, 3000);
  await notes.create(owner, { title: "长笔记", body });
  const thread = await store.createThread(owner);
  await submit(thread.id, { input: "研究 NVDA" });
  const instructions = runtime.requests[0]!.instructions;
  assert.ok(instructions.includes(body.slice(0, 2000) + "…"));
  assert.ok(!instructions.includes(body.slice(0, 2001)));
});

test("an unreadable notebook never blocks the research task", async () => {
  const unavailable = {
    forAgent: async () => {
      throw new Error("storage_unavailable");
    },
  } as unknown as PersonalNoteStore;
  const { owner, runtime, submit } = await accountWithProfiles(unavailable);
  const thread = await store.createThread(owner);
  await submit(thread.id, { input: "研究 NVDA" });
  const instructions = runtime.requests[0]!.instructions;
  assert.match(instructions, /research coordinator/);
  assert.ok(!instructions.includes("PERSONAL NOTEBOOK"));
});
