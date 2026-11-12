import { test } from "node:test";
import assert from "node:assert/strict";
import {
  AlpacaScanProvider,
  rankCandidates,
} from "../../packages/server/src/market-scanner.js";

test("market ranking is deterministic, takes eight ranked plus two exploration, and handles missing announcements", () => {
  const rows = Array.from({ length: 20 }, (_, i) => ({
    symbol: `S${i}`,
    relativeReturn: i / 100,
    volumeRatio: i + 1,
    announcementCount: null,
  }));
  const ranked = rankCandidates(rows, "2025-01-02", ["S19"]);
  assert.equal(ranked[0]!.symbol, "S18");
  assert.equal(
    ranked.slice(0, 8).filter((row) => row.reason === "ranked").length,
    8,
  );
  assert.equal(
    ranked.slice(8, 10).filter((row) => row.reason === "exploration").length,
    2,
  );
  assert.equal(new Set(ranked.map((row) => row.symbol)).size, 19);
  assert.deepEqual(
    ranked,
    rankCandidates([...rows].reverse(), "2025-01-02", ["S19"]),
  );
  assert.ok(
    ranked.every(
      (row) =>
        row.announcementCount === null && row.score >= 0 && row.score <= 1,
    ),
  );
});

test("tied factors have equal scores and event percentile is equally weighted when available", () => {
  const rows = [
    {
      symbol: "AAA",
      relativeReturn: 0.05,
      volumeRatio: 2,
      announcementCount: 0,
    },
    {
      symbol: "BBB",
      relativeReturn: -0.05,
      volumeRatio: 2,
      announcementCount: 1,
    },
  ];
  const ranked = rankCandidates(rows, "seed");
  assert.equal(ranked[0]!.symbol, "BBB");
  assert.equal(ranked[0]!.score, 2 / 3);
  assert.equal(ranked[1]!.score, 1 / 3);
});

test("Alpaca scanner collects all bar pages and preserves missing symbols instead of substituting zero", async () => {
  let calls = 0;
  const provider = new AlpacaScanProvider(
    {
      ALPACA_DATA_KEY: "test",
      ALPACA_DATA_SECRET: "test",
      ALPACA_DATA_FEED: "iex",
    },
    async (input) => {
      const url = new URL(String(input));
      assert.equal(url.searchParams.get("feed"), "iex");
      calls++;
      return new Response(
        JSON.stringify(
          calls === 1
            ? {
                bars: { AAA: [{ t: "2025-01-02T05:00:00Z", c: 100, v: 50 }] },
                next_page_token: "next",
              }
            : {
                bars: { AAA: [{ t: "2025-01-03T05:00:00Z", c: 105, v: 70 }] },
                next_page_token: null,
              },
        ),
      );
    },
  );
  const bars = await provider.bars(["AAA", "BBB"], "2025-01-02", "2025-01-03");
  assert.equal(calls, 2);
  assert.equal(bars.AAA!.length, 2);
  assert.equal(bars.AAA![0]!.session, "2025-01-02");
  assert.equal(bars.BBB, undefined);
});

test("empty or unavailable universe is not made into a synthetic stock list", async () => {
  const missing = new AlpacaScanProvider({}, async () => {
    throw new Error("unexpected network");
  });
  await assert.rejects(missing.assets(), /scanner_configuration_incomplete/);
  const empty = new AlpacaScanProvider(
    {
      ALPACA_DATA_KEY: "test",
      ALPACA_DATA_SECRET: "test",
      ALPACA_DATA_FEED: "iex",
    },
    async () => new Response("[]"),
  );
  assert.deepEqual(await empty.assets(), []);
});
