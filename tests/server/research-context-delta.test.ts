import { test } from "node:test";
import assert from "node:assert/strict";
import { contextDeltaView } from "../../packages/server/src/research-context-delta.js";

const evidence = (id: string) => ({
  id,
  source: "sec",
  eventKey: id,
  published_at: "2026-11-01",
  observed_at: "2026-11-02",
  payload: {
    title: `title ${id}`,
    url: `https://example.com/${id}`,
    summary: `summary ${id}`,
  },
});

const snapshot = (ref: string, ids: string[]) => ({
  contextRef: ref,
  symbol: "NVDA",
  evidence: ids.map(evidence),
  counts: { evidence: ids.length },
});

const view = {
  contextRef: "new",
  symbol: "NVDA",
  counts: { evidence: 3 },
  evidence: [{ id: "a" }],
  evidencePage: { offset: 0, total: 3, nextOffset: 2, instruction: "full" },
};

test("delta returns only new evidence and explains omitted unchanged items", () => {
  const result = contextDeltaView(
    snapshot("new", ["a", "b", "c"]),
    view,
    snapshot("old", ["a"]),
  );
  assert.deepEqual(
    result.evidence.map((item: any) => item.id),
    ["b", "c"],
  );
  assert.equal(result.evidencePage.total, 2);
  assert.equal(result.evidencePage.nextOffset, null);
  assert.match(result.evidencePage.instruction, /old/);
  assert.deepEqual(result.delta, {
    previousContextRef: "old",
    newEvidence: 2,
    unchangedEvidence: 1,
    totalEvidence: 3,
  });
  assert.equal(result.counts.evidence, 3);
});

test("delta with no new evidence returns a short unchanged receipt", () => {
  const result = contextDeltaView(
    snapshot("new", ["a"]),
    view,
    snapshot("old", ["a"]),
  );
  assert.deepEqual(result.evidence, []);
  assert.equal(result.evidencePage.total, 0);
  assert.equal(result.delta.newEvidence, 0);
  assert.match(result.evidencePage.instruction, /No new evidence/);
});

test("delta caps very large additions and marks truncation", () => {
  const ids = Array.from({ length: 25 }, (_, index) => `e${index}`);
  const result = contextDeltaView(
    snapshot("new", ids),
    view,
    snapshot("old", []),
  );
  assert.equal(result.evidence.length, 20);
  assert.equal(result.evidencePage.total, 25);
  assert.equal(result.delta.truncated, true);
});
