import { randomUUID } from "node:crypto";
import assert from "node:assert/strict";
import { database } from "../packages/server/src/db.js";
import { ExecutionContextStore } from "../packages/server/src/execution-context.js";
import { TaskStore } from "../packages/server/src/tasks.js";
import { assertContextResources } from "../packages/server/src/context-resources.js";
if (!new URL(process.env.DATABASE_URL!).pathname.endsWith("_test"))
  throw new Error("isolated_test_database_required");
const pool = database();
try {
  const dataset = (
    await pool.query(
      `SELECT d.id,d.owner_id,t.thread_id FROM datasets d JOIN tool_operations op ON op.id=d.operation_id JOIN tasks t ON t.id=op.task_id LIMIT 1`,
    )
  ).rows[0];
  if (!dataset) throw new Error("historical_dataset_required");
  const tasks = new TaskStore(pool),
    contexts = new ExecutionContextStore(pool);
  const personal = await tasks.createThread(dataset.owner_id);
  await assertContextResources(
    pool,
    dataset.owner_id,
    personal.id,
    "datasets",
    [dataset.id],
  );
  const a = await contexts.group(
    dataset.owner_id,
    "cli_isolation",
    `oc_${randomUUID()}`,
  );
  const b = await contexts.group(
    dataset.owner_id,
    "cli_isolation",
    `oc_${randomUUID()}`,
  );
  const threadA = await tasks.createThread(dataset.owner_id, a.id);
  const threadB = await tasks.createThread(dataset.owner_id, b.id);
  for (const thread of [threadA, threadB])
    await assert.rejects(
      assertContextResources(pool, dataset.owner_id, thread.id, "datasets", [
        dataset.id,
      ]),
      /not_found/,
    );
  const result = (
    await pool.query("SELECT id FROM results WHERE owner_id=$1 LIMIT 1", [
      dataset.owner_id,
    ])
  ).rows[0];
  if (!result) throw new Error("historical_result_required");
  await assert.rejects(
    assertContextResources(pool, dataset.owner_id, threadA.id, "results", [
      result.id,
    ]),
    /not_found/,
  );
  await assert.rejects(
    contexts.thread(randomUUID(), dataset.thread_id),
    /not_found/,
  );
  const profiles = (
    await pool.query(
      "SELECT id,context_id FROM profile_instances WHERE context_id=ANY($1::uuid[])",
      [[a.id, b.id]],
    )
  ).rows;
  assert.equal(profiles.length, 6);
  assert.equal(new Set(profiles.map((p) => p.id)).size, 6);
  console.info("historical_private_references_denied_in_two_group_contexts");
} finally {
  await pool.end();
}
