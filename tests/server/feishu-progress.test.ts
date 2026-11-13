import { test } from "node:test";
import assert from "node:assert/strict";
import { feishuProgress } from "../../packages/server/src/feishu-progress.js";
test("slow tasks get a stable state receipt, never a premature or false completion", () => {
  const created = new Date("2026-11-13T00:00:00Z"),
    now = created.getTime();
  assert.equal(feishuProgress("queued", created, now + 29999), null);
  assert.equal(
    feishuProgress("queued", created, now + 30000)?.phase,
    "progress_queued",
  );
  assert.deepEqual(
    feishuProgress("running", created, now + 60000),
    feishuProgress("running", created, now + 600000),
  );
  for (const state of [
    "completed",
    "failed",
    "cancelled",
    "unknown",
    "interrupted",
  ])
    assert.equal(feishuProgress(state, created, now + 60000), null);
});
