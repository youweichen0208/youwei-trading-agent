import { test } from "node:test";
import assert from "node:assert/strict";
import {
  criticalTools,
  fatalToolFailure,
} from "../../packages/server/src/tool-policy.js";

const at = (second: number) => new Date(Date.UTC(2026, 10, 14, 12, 0, second));

test("coverage tool failures are not fatal", () => {
  assert.equal(
    fatalToolFailure([
      { tool: "collect_evidence", status: "failed", at: at(1) },
      { tool: "read_research_context", status: "failed", at: at(2) },
      { tool: "read_dataset", status: "failed", at: at(3) },
    ]),
    null,
  );
});

test("an unrecovered critical tool failure is fatal", () => {
  assert.deepEqual(
    fatalToolFailure([
      {
        tool: "market_history",
        status: "failed",
        error_code: "missing_data",
        at: at(1),
      },
    ]),
    { tool: "market_history", errorCode: "missing_data" },
  );
  assert.ok(criticalTools.has("publish_forecast"));
});

test("a later successful call to the same tool recovers the failure", () => {
  assert.equal(
    fatalToolFailure([
      { tool: "market_history", status: "failed", at: at(1) },
      { tool: "market_history", status: "completed", at: at(2) },
    ]),
    null,
  );
});

test("a success before the failure does not recover it", () => {
  assert.deepEqual(
    fatalToolFailure([
      { tool: "quant_calculate", status: "completed", at: at(1) },
      {
        tool: "quant_calculate",
        status: "failed",
        error_code: "quant_failed",
        at: at(2),
      },
    ]),
    { tool: "quant_calculate", errorCode: "quant_failed" },
  );
});

test("cancelled operations are consequences, not causes", () => {
  assert.equal(
    fatalToolFailure([
      { tool: "market_history", status: "cancelled", at: at(1) },
    ]),
    null,
  );
});
