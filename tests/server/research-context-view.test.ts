import { test } from "node:test";
import assert from "node:assert/strict";
import { researchContextView } from "../../packages/server/src/research-context-view.js";
test("model evidence pages retain frozen references without spilling full news bodies", () => {
  const evidence = Array.from({ length: 50 }, (_, i) => ({
    id: `e-${i}`,
    source: "market_news",
    eventKey: `event-${i}`,
    payload: {
      title: "Company update",
      text: "x".repeat(5000),
      url: "https://example.test/news",
    },
  }));
  const snapshot = {
    contextRef: "fixed-context",
    symbol: "NVDA",
    evidence,
    evidenceRefs: evidence.map((e) => e.id),
    datasetRefs: ["fixed-dataset"],
    lessons: [],
  };
  const original = JSON.stringify(snapshot);
  const ids: string[] = [];
  for (let offset: number | null = 0; offset !== null;) {
    const view = researchContextView(snapshot, offset);
    assert.ok(JSON.stringify(view).length < 10000);
    assert.equal(view.contextRef, snapshot.contextRef);
    assert.deepEqual(view.datasetRefs, ["fixed-dataset"]);
    assert.deepEqual(view.evidenceRefs, snapshot.evidenceRefs);
    ids.push(...view.evidence.map((e: any) => e.id));
    offset = view.evidencePage.nextOffset;
  }
  assert.deepEqual(ids, snapshot.evidenceRefs);
  assert.equal(JSON.stringify(snapshot), original);
});

test("short evidence uses at most three bounded pages for fifty items", () => {
  const evidence = Array.from({ length: 50 }, (_, i) => ({
    id: `e-${i}`,
    payload: {
      title: "Company update",
      summary: "Short verified news summary.",
    },
  }));
  const seen: string[] = [];
  let offset: number | null = 0,
    pages = 0;
  while (offset !== null) {
    const view = researchContextView(
      { contextRef: "frozen", evidence },
      offset,
    );
    assert.ok(JSON.stringify(view).length < 10000);
    seen.push(...view.evidence.map((e: any) => e.id));
    offset = view.evidencePage.nextOffset;
    pages++;
  }
  assert.deepEqual(
    seen,
    evidence.map((e) => e.id),
  );
  assert.ok(pages <= 3, `Expected <=3 model reads, got ${pages}`);
});
