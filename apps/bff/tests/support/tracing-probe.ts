// Invoked only by scripts/check_tracing.py with ephemeral loopback endpoints.
import assert from "node:assert/strict";
import { createServer } from "node:http";
import express from "express";
import { AlphaClient } from "../../src/alpha.js";
import { readConfig } from "../../src/config.js";
import { observeRequests } from "../../src/observability.js";
import { startTracing } from "../../src/tracing.js";

const config = readConfig({YTA_ENV: "test", YTA_DATABASE_URL: "postgresql://unused/tracing_test",
  YTA_ALPHA_TOKEN: "trace-probe-service-token-not-live-12345", YTA_ALPHA_URL: process.argv[2],
  YTA_OTLP_TRACES_URL: process.argv[3]});
for (const value of [config.YTA_ALPHA_URL, config.YTA_OTLP_TRACES_URL!]) {
  assert.equal(new URL(value).hostname, "127.0.0.1");
}
const shutdown = startTracing(config);
const client = new AlphaClient(config);
const app = express();
app.use(observeRequests(() => {}));
app.get("/api/yta/v1/health", async (_req, res) => {
  res.json(await client.request("/internal/v1/health"));
});
const server = createServer(app);
try {
  await new Promise<void>(resolve => server.listen(0, "127.0.0.1", resolve));
  const address = server.address(); assert(address && typeof address !== "string");
  const started = performance.now();
  const response = await fetch(`http://127.0.0.1:${address.port}/api/yta/v1/health`, {
    headers: {traceparent: "00-" + "a".repeat(32) + "-" + "b".repeat(16) + "-01",
      baggage: "private-probe-marker", Cookie: "private-probe-marker"}});
  assert.equal(response.status, 200); await response.text();
  assert(performance.now() - started < 3000, "Telemetry must not delay the response");
  console.log("business-response-ok");
} finally {
  server.closeAllConnections();
  await new Promise<void>(resolve => server.close(() => resolve()));
  await shutdown();
}
