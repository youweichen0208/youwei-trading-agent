import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { Pool } from "pg";
import { MarketScanner } from "../../packages/server/src/market-scanner.js";
import { ForecastStore } from "../../packages/server/src/forecast-store.js";
import { AlpacaProvider } from "../../packages/market/src/alpaca.js";
const [schema, owner] = process.argv.slice(2);
assert.match(schema ?? "", /^test_[a-f0-9]{32}$/);
assert.match(owner ?? "", /^[a-f0-9-]{36}$/);
const url = process.env.TEST_DATABASE_URL!;
assert.ok(new URL(url).pathname.endsWith("_test"));
const market = JSON.parse(
  await readFile(process.env.ALPHA_MARKET_FILE!, "utf8"),
);
process.env.ALPACA_DATA_KEY = market.apiKey;
process.env.ALPACA_DATA_SECRET = market.apiSecret;
process.env.ALPACA_DATA_FEED = market.feed;
const pool = new Pool({
  connectionString: url,
  options: `-c search_path=${schema}`,
});
try {
  const forecasts = new ForecastStore(pool);
  const before = await forecasts.list(owner!);
  assert.equal(before.length, 2);
  const settlement = await forecasts.evaluateDue(
    new AlpacaProvider(market.apiKey, market.apiSecret, market.feed),
  );
  assert.equal(settlement.evaluated.length, 0);
  assert.equal(
    (await pool.query("SELECT count(*)::int n FROM forecast_evaluations"))
      .rows[0].n,
    0,
  );
  const scan = await new MarketScanner(pool).scan(owner!);
  console.log(
    JSON.stringify({
      checkedAt: new Date().toISOString(),
      prematureSettlementPrevented: true,
      settlement,
      scan,
    }),
  );
} finally {
  await pool.end();
}
