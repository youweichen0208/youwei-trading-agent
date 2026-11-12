import { test } from "node:test";
import assert from "node:assert/strict";
import { artifactCards } from "../../packages/server/src/feishu-cards.js";
const origin = "https://trading.example.com";
const stock = { component: "stock_chart", title: "TEST 日线" };
test("charts preserve gaps, zero volume and exact table decimals", () => {
  const [card] = artifactCards(
    stock,
    {
      metadata: {
        source: "fixture",
        displayRange: { start: "2026-01-02", end: "2026-01-05" },
      },
      payload: [
        { session: "2026-01-01", close: "999" },
        { session: "2026-01-02", close: "123.4567890123456789", volume: "0" },
        { session: "2026-01-03", close: null, volume: null },
        { session: "2026-01-05", close: "124", volume: "2" },
      ],
    },
    origin,
  );
  const charts = card!.body.elements.filter((e) => e.tag === "chart");
  assert.deepEqual(
    charts[0].chart_spec.data.values.map((v: any) => v.segment),
    ["0", "1"],
  );
  assert.equal(charts[1].chart_spec.data.values[0].value, 0);
  const rows = card!.body.elements.find((e) => e.tag === "table").rows;
  assert.equal(rows[0].close, "123.4567890123456789");
  assert.equal(rows[1].close, "缺失");
  assert.equal(rows.length, 3);
});
test("long periods split without dropping observations and keep payload bounded", () => {
  const rows = Array.from({ length: 241 }, (_, i) => ({
    session: `2026-${String(i).padStart(4, "0")}`,
    close: "123",
    volume: "1234",
  }));
  const cards = artifactCards(stock, { payload: rows }, origin);
  assert.equal(cards.length, 3);
  assert.equal(
    cards.reduce(
      (n, c) =>
        n +
        c.body.elements.find((e) => e.tag === "chart").chart_spec.data.values
          .length,
      0,
    ),
    241,
  );
  for (const c of cards)
    assert.ok(Buffer.byteLength(JSON.stringify(c)) < 28000);
});
test("no graph is invented for unavailable or non-research responses", () => {
  assert.deepEqual(
    artifactCards(
      { component: "research_summary" },
      { payload: { text: "hello" } },
      origin,
    ),
    [],
  );
  const [c] = artifactCards(
    stock,
    { payload: [{ session: "2026-01-01", close: null, volume: null }] },
    origin,
  );
  assert.equal(c!.body.elements.filter((e) => e.tag === "chart").length, 0);
});
test("prediction and incomplete simulated holdings retain labels", () => {
  const [p] = artifactCards(
    { title: "预测", component: "prediction_result" },
    { payload: { status: "unavailable" } },
    origin,
  );
  assert.match(JSON.stringify(p), /探索性/);
  assert.match(JSON.stringify(p), /样本不足/);
  const [h] = artifactCards(
    { title: "组合", component: "portfolio_summary" },
    {
      payload: {
        total: null,
        missing: ["TEST"],
        positions: [{ symbol: "TEST", value: null, weight: null }],
      },
    },
    origin,
  );
  assert.match(JSON.stringify(h), /模拟/);
  assert.match(JSON.stringify(h), /缺少有效价格/);
  assert.doesNotMatch(JSON.stringify(h), /0\.000%/);
});
