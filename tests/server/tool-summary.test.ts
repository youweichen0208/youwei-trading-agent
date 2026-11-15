import { test } from "node:test";
import assert from "node:assert/strict";
import { toolDetail } from "../../packages/server/src/tool-summary.js";

test("tool detail summarizes whitelisted call arguments", () => {
  assert.equal(
    toolDetail(
      "tool.started",
      JSON.stringify({
        symbol: "NVDA",
        start: "2025-01-01",
        end: "2025-06-30",
        adjustment: "split",
      }),
    ),
    "NVDA · 2025-01-01→2025-06-30 · 复权价格",
  );
  assert.equal(
    toolDetail(
      "tool.started",
      JSON.stringify({
        symbol: "NVDA",
        sources: ["sec", "web", "x", "macro"],
        start: "2026-11-01",
        end: "2026-11-14",
      }),
    ),
    "NVDA · 2026-11-01→2026-11-14 · 来源 sec/web/x",
  );
  assert.equal(
    toolDetail("tool.started", JSON.stringify({ kind: "indicators" })),
    "技术指标",
  );
});

test("tool detail summarizes whitelisted result counts and duration", () => {
  assert.equal(
    toolDetail(
      "tool.completed",
      JSON.stringify({
        status: "partial",
        evidenceRefs: ["a", "b"],
        sources: [{ source: "sec" }, { source: "web" }],
        failures: ["x"],
      }),
      3.24,
    ),
    "部分覆盖 · 证据 2 条 · 1 个来源失败 · 2 个来源 · 耗时 3.2 秒",
  );
  assert.equal(
    toolDetail(
      "tool.completed",
      JSON.stringify({ counts: { evidence: 24, forecasts: 2, lessons: 5 } }),
      12.6,
    ),
    "证据 24 条 · 预测 2 条 · 经验 5 条 · 耗时 13 秒",
  );
});

test("tool detail never forwards free text, URLs or malformed previews", () => {
  assert.equal(toolDetail("tool.started", "plain preview text"), undefined);
  assert.equal(
    toolDetail("tool.started", JSON.stringify(["a", "b"])),
    undefined,
  );
  assert.equal(toolDetail("tool.started", undefined), undefined);
  assert.equal(
    toolDetail(
      "tool.started",
      JSON.stringify({
        symbol: "NVDA",
        query: "用户私密问题",
        urls: ["https://example.com/secret"],
      }),
    ),
    "NVDA",
  );
  assert.equal(toolDetail("tool.completed", undefined, 0), undefined);
  assert.equal(toolDetail("tool.completed", undefined, 0.05), undefined);
  assert.match(toolDetail("tool.completed", undefined, 0.4) ?? "", /耗时 0\.4 秒/);
});
