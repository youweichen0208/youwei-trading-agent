import { test } from "node:test";
import assert from "node:assert/strict";
import { failureText } from "../../packages/server/src/failure-text.js";

test("terminal task text names the actual blocker", () => {
  assert.match(failureText("failed", "required_child_failed"), /专项分析任务/);
  assert.match(failureText("failed", "required_tool_failed"), /工具调用失败/);
  assert.match(failureText("failed", "final_output_missing"), /没有返回结论/);
  assert.match(failureText("cancelled", "deadline_exceeded"), /执行时限/);
  assert.match(failureText("interrupted", "interrupted"), /中断/);
  assert.equal(failureText("cancelled", "user_requested"), "任务已停止。");
  assert.equal(
    failureText("failed", "unmapped_reason"),
    "任务未完整完成，请查看任务状态。",
  );
});
