import { before, after, test } from "node:test";
import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { Pool } from "pg";
import { maintain } from "../../packages/server/src/maintenance.js";
import { migrate } from "../../packages/server/src/migrate.js";
import { TaskStore } from "../../packages/server/src/tasks.js";

const url = process.env.TEST_DATABASE_URL;
if (!url || !new URL(url).pathname.endsWith("_test"))
  throw new Error("Dedicated TEST_DATABASE_URL ending in _test required");
const schema = `test_${randomUUID().replaceAll("-", "")}`;
const admin = new Pool({ connectionString: url }),
  pool = new Pool({
    connectionString: url,
    options: `-c search_path=${schema}`,
  }),
  tasks = new TaskStore(pool);
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
    "INSERT INTO users(id,email,password_hash) VALUES($1,$2,'test')",
    [owner, `${owner}@test.invalid`],
  );
  const thread = await tasks.createThread(owner);
  const task = await tasks.submit(owner, thread.id, randomUUID(), {
    input: "retained research",
  });
  return { owner, thread, task };
}

test("maintenance deletes bounded expired batches without changing durable research or active auth", async () => {
  const a = await setup();
  await tasks.stop(a.owner, a.task.id);
  // More than one batch, including the previously created task events.
  await pool.query(
    "UPDATE task_events SET created_at=now()-interval '8 days' WHERE task_id=$1",
    [a.task.id],
  );
  await pool.query(
    `INSERT INTO task_events(id,owner_id,task_id,sequence,event,data,created_at)
    SELECT gen_random_uuid(),$1,$2,n,'run.progress','{}',now()-interval '8 days'
    FROM generate_series(3,1005) n`,
    [a.owner, a.task.id],
  );
  await pool.query(
    `INSERT INTO task_events(id,owner_id,task_id,sequence,event,data)
    VALUES($1,$2,$3,1006,'run.cancelled','{}')`,
    [randomUUID(), a.owner, a.task.id],
  );
  await pool.query("UPDATE tasks SET sequence=1006 WHERE id=$1", [a.task.id]);
  await pool.query(
    `INSERT INTO login_sessions(token_hash,owner_id,expires_at)
    SELECT 'expired-'||n,$1,now()-interval '1 second' FROM generate_series(1,1005) n`,
    [a.owner],
  );
  await pool.query(
    `INSERT INTO login_sessions(token_hash,owner_id,expires_at) VALUES('valid-session',$1,now()+interval '1 day')`,
    [a.owner],
  );
  await pool.query(`INSERT INTO auth_attempts(bucket,count,reset_at)
    SELECT 'expired-'||n,7,now()-interval '1 second' FROM generate_series(1,1005) n`);
  await pool.query(
    "INSERT INTO auth_attempts(bucket,count,reset_at) VALUES('live-bucket',7,now()+interval '1 hour')",
  );
  const datasetId = randomUUID(),
    resultId = randomUUID();
  await pool.query(
    "INSERT INTO datasets(id,owner_id,metadata,payload,content_hash) VALUES($1,$2,'{}','{}','test')",
    [datasetId, a.owner],
  );
  await pool.query(
    "INSERT INTO results(id,owner_id,task_id,kind,payload,lineage) VALUES($1,$2,$3,'research','{}','{}')",
    [resultId, a.owner, a.task.id],
  );
  const snapshot = await tasks.snapshot(a.owner, a.task.id),
    audit = await tasks.audit(a.owner, a.task.id);
  const first = await maintain(pool);
  assert.deepEqual(first, {
    skipped: false,
    events: 1000,
    sessions: 1000,
    attempts: 1000,
    capabilities: 0,
  });
  assert.deepEqual(await tasks.snapshot(a.owner, a.task.id), snapshot);
  assert.deepEqual(await tasks.audit(a.owner, a.task.id), audit);
  assert.deepEqual(await maintain(pool), {
    skipped: false,
    events: 5,
    sessions: 5,
    attempts: 5,
    capabilities: 0,
  });
  assert.deepEqual(await maintain(pool), {
    skipped: false,
    events: 0,
    sessions: 0,
    attempts: 0,
    capabilities: 0,
  });
  assert.equal((await tasks.events(a.owner, a.task.id, 0)).length, 1);
  assert.equal(
    (await pool.query("SELECT * FROM login_sessions")).rows[0].token_hash,
    "valid-session",
  );
  assert.equal(
    (await pool.query("SELECT * FROM auth_attempts")).rows[0].count,
    7,
  );
  assert.equal(
    (await pool.query("SELECT id FROM datasets WHERE id=$1", [datasetId]))
      .rowCount,
    1,
  );
  assert.equal(
    (await pool.query("SELECT id FROM results WHERE id=$1", [resultId]))
      .rowCount,
    1,
  );
});

test("maintenance skips live locks and a competing cleaner; never retires unknown identities", async () => {
  const a = await setup(),
    b = await setup();
  await tasks.stop(a.owner, a.task.id);
  const worker = randomUUID(),
    active = (await tasks.claim(b.owner, worker))!.task;
  await tasks.prepareExecution(
    b.owner,
    active.id,
    worker,
    active.generation,
    { input: "original" },
    "profile",
    "session",
  );
  await tasks.beginSubmission(
    b.owner,
    active.id,
    worker,
    active.generation,
    "original-runtime",
  );
  await tasks.defer(
    b.owner,
    active.id,
    worker,
    active.generation,
    "unknown",
    "upstream_execution_unknown",
    0,
  );
  // Simulate terminal capabilities saved by an older version of the application.
  await pool.query(
    `INSERT INTO executions(task_id,idempotency_key,request,profile_id,session_id,tool_token_hash,tool_token_ciphertext)
    VALUES($1,$2,'{}','retired-profile','retired-session','retired-hash','retired-cipher')`,
    [a.task.id, randomUUID()],
  );
  await pool.query(
    "UPDATE executions SET tool_token_hash='active-hash',tool_token_ciphertext='active-cipher' WHERE task_id=$1",
    [active.id],
  );
  const original = (
    await pool.query("SELECT * FROM executions WHERE task_id=$1", [active.id])
  ).rows[0];
  await pool.query(
    "UPDATE task_events SET created_at=now()-interval '8 days' WHERE task_id=$1",
    [a.task.id],
  );
  const blocker = await pool.connect();
  try {
    await blocker.query("BEGIN");
    await blocker.query(
      "SELECT pg_advisory_xact_lock(hashtext(current_database()),hashtext(current_schema() || ':alpha_maintenance'))",
    );
    assert.deepEqual(await maintain(pool), { skipped: true });
    await blocker.query("ROLLBACK");
    await blocker.query("BEGIN");
    await blocker.query(
      "SELECT id FROM task_events WHERE task_id=$1 FOR UPDATE",
      [a.task.id],
    );
    const result = await maintain(pool);
    assert.deepEqual(result, {
      skipped: false,
      events: 0,
      sessions: 0,
      attempts: 0,
      capabilities: 1,
    });
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
    assert.deepEqual(
      (
        await pool.query("SELECT * FROM executions WHERE task_id=$1", [
          active.id,
        ])
      ).rows[0],
      original,
    );
    assert.equal((await tasks.get(b.owner, active.id)).status, "unknown");
    await blocker.query("ROLLBACK");
    const cleaned = await maintain(pool);
    assert.equal(cleaned.skipped, false);
    if (!cleaned.skipped) assert.equal(cleaned.events, 2);
  } finally {
    await blocker.query("ROLLBACK");
    blocker.release();
  }
});
