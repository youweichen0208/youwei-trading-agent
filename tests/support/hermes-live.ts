// Explicit live-provider acceptance only. No synthetic model or market fallback.
import assert from "node:assert/strict";
import { randomBytes, randomUUID } from "node:crypto";
import { lstat, readFile, writeFile } from "node:fs/promises";
import { createServer } from "node:http";
import { join } from "node:path";
import { createInterface } from "node:readline";
import { Readable } from "node:stream";
import { setTimeout as delay } from "node:timers/promises";
import { Pool } from "pg";
import { AlpacaProvider } from "../../packages/market/src/alpaca.js";
import { AuthStore } from "../../packages/server/src/auth.js";
import { BusinessStore } from "../../packages/server/src/business.js";
import { DataService } from "../../packages/server/src/data-service.js";
import { migrate } from "../../packages/server/src/migrate.js";
import { TaskStore } from "../../packages/server/src/tasks.js";
import { createToolHandler } from "../../packages/server/src/tool-gateway.js";
import { prepareProfiles } from "../../packages/hermes-adapter/src/provision.js";
import { workerHarness, bounded } from "./hermes-worker-harness.js";

const url = process.env.TEST_DATABASE_URL;
if (!url || !new URL(url).pathname.endsWith("_test"))
  throw new Error("Dedicated test DB required");
const input = createInterface({ input: process.stdin });
const lines = input[Symbol.asyncIterator]();
const configuration = JSON.parse((await lines.next()).value!);
async function privateJSON(path: string) {
  const file = await lstat(path);
  assert.ok(
    file.isFile() && !file.isSymbolicLink() && !(file.mode & 0o077),
    "Private configuration required",
  );
  return JSON.parse(await readFile(path, "utf8"));
}
const model = await privateJSON(configuration.modelFile);
const market = await privateJSON(configuration.marketFile);
assert.equal(market.provider, "alpaca");
assert.ok(["iex", "sip"].includes(market.feed));
const provider = new AlpacaProvider(
  market.apiKey,
  market.apiSecret,
  market.feed,
);
const schema = `test_${randomUUID().replaceAll("-", "")}`;
const admin = new Pool({ connectionString: url });
const pool = new Pool({
  connectionString: url,
  options: `-c search_path=${schema}`,
});
const store = new TaskStore(pool),
  business = new BusinessStore(pool);
const observed: { tool: string; status: number }[] = [];
const handler = createToolHandler(pool, new DataService(pool, provider));
const server = createServer(async (req, res) => {
  try {
    const headers = new Headers();
    for (const [key, value] of Object.entries(req.headers))
      if (value !== undefined)
        headers.set(key, Array.isArray(value) ? value.join(",") : value);
    const init: RequestInit & { duplex?: "half" } = {
      method: req.method,
      headers,
    };
    if (req.method === "POST")
      Object.assign(init, { body: Readable.toWeb(req), duplex: "half" });
    const request = new Request(`http://tools${req.url}`, init);
    const body = req.method === "POST" ? await request.clone().json() : {};
    const response = await handler(request);
    observed.push({ tool: body.tool ?? "list", status: response.status });
    console.error(JSON.stringify({ tool: body.tool, status: response.status }));
    res.writeHead(response.status, Object.fromEntries(response.headers));
    res.end(Buffer.from(await response.arrayBuffer()));
  } catch {
    res.writeHead(503).end();
  }
});
let created = false,
  stopping = false;
let harness: Awaited<ReturnType<typeof workerHarness>> | undefined;
process.once("SIGTERM", () => {
  stopping = true;
  input.close();
});
try {
  await admin.query(`CREATE SCHEMA ${schema}`);
  created = true;
  await migrate(pool);
  const owner = (
    await new AuthStore(pool).register(
      `${randomUUID()}@test.invalid`,
      "test-password-long",
    )
  ).id;
  const holdings = await business.saveHoldings(owner, {
    asOfSession: "2025-08-01",
    positions: [{ symbol: "NVDA", quantity: "2" }],
    cashUsd: "100",
  });
  const profiles = (
    await pool.query(
      "SELECT id,role FROM profile_instances WHERE owner_id=$1",
      [owner],
    )
  ).rows;
  await new Promise<void>((resolve) =>
    server.listen(0, process.env.ALPHA_TEST_BIND_HOST ?? "127.0.0.1", resolve),
  );
  const address = server.address();
  assert.ok(address && typeof address !== "string");
  const prepared = await prepareProfiles(
    pool,
    owner,
    {
      image: configuration.image,
      firstPort: configuration.firstPort,
      gatewayOrigin: `http://host.docker.internal:${address.port}`,
      models: { research: model, prediction: model, portfolio: model },
    },
    configuration.outputRoot,
  );
  console.log(JSON.stringify({ prepared, profiles }));
  const message = await lines.next();
  assert.equal(message.done, false);
  const bindings = JSON.parse(message.value!);
  harness = await workerHarness(
    bindings,
    profiles.find((p) => p.role === "research").id,
    schema,
    randomBytes(32).toString("hex"),
    { dropFirstResponse: false },
  );
  const thread = await store.createThread(owner);
  const root = await store.submit(owner, thread.id, randomUUID(), {
    input:
      "Analyze NVDA from 2025-01-02 to 2025-08-01. Include the price chart and MA20/RSI14, " +
      "ask the prediction specialist for the fixed statistical forecast and historical evaluation, " +
      "and ask the portfolio specialist to value my selected simulated portfolio as of its date. " +
      "Publish all five corresponding Canvas components and summarize the saved evidence in Chinese. " +
      "Label the IEX-only market coverage. Do not execute trades.",
    portfolioSnapshotRef: holdings.snapshotId,
  });
  const worker = harness.start();
  const deadline = Date.now() + 600_000;
  while (!stopping && Date.now() < deadline) {
    assert.equal(worker.child.exitCode, null, "Worker exited unexpectedly");
    assert.equal(
      worker.child.signalCode,
      null,
      "Worker terminated unexpectedly",
    );
    const current = await store.get(owner, root.id);
    if (
      ["completed", "failed", "cancelled", "interrupted"].includes(
        current.status,
      )
    )
      break;
    await delay(500);
  }
  const snapshot = await store.snapshot(owner, root.id);
  const children = await store.children(owner, root.id);
  // Private diagnostic evidence contains only task facts and references, never credentials.
  await writeFile(
    join(configuration.evidenceRoot, "research-result.json"),
    JSON.stringify(
      {
        status: snapshot.task.status,
        failureCode: snapshot.task.failure_code,
        output: snapshot.task.stream_text,
        artifacts: snapshot.artifacts,
        children: children.map((t) => ({
          role: t.role,
          status: t.status,
          failureCode: t.failure_code,
        })),
        tools: observed,
      },
      null,
      2,
    ) + "\n",
    { mode: 0o600 },
  );
  assert.equal(
    snapshot.task.status,
    "completed",
    "Live research did not complete",
  );
  assert.ok(
    snapshot.task.stream_text.trim().length > 20,
    "Final research text missing",
  );
  assert.deepEqual(children.map((t) => t.role).sort(), [
    "portfolio",
    "prediction",
  ]);
  assert.ok(
    children.every((t) => t.status === "completed"),
    "Specialist failed",
  );
  const components = [
    ...new Set(snapshot.artifacts.map((a: any) => a.component)),
  ].sort();
  assert.deepEqual(
    components,
    [
      "stock_chart",
      "indicator_panel",
      "research_summary",
      "prediction_result",
      "portfolio_summary",
    ].sort(),
  );
  const datasets = (
    await pool.query(
      "SELECT metadata,payload FROM datasets WHERE owner_id=$1",
      [owner],
    )
  ).rows;
  assert.ok(datasets.length >= 2);
  assert.ok(
    datasets.every(
      (d) => d.metadata.source === "alpaca" && d.metadata.feed === market.feed,
    ),
    "Market provenance mismatch",
  );
  const calculations = (
    await pool.query(
      "SELECT kind,payload,lineage FROM results WHERE owner_id=$1",
      [owner],
    )
  ).rows;
  for (const kind of [
    "indicators",
    "prediction",
    "portfolio",
    "research_summary",
  ])
    assert.ok(
      calculations.some((r) => r.kind === kind),
      `Missing ${kind} result`,
    );
  const prediction = calculations.find((r) => r.kind === "prediction")!;
  assert.equal(prediction.payload.evaluation, "retrospective");
  assert.equal(prediction.payload.points.length, 60);
  const portfolioArtifact = snapshot.artifacts.find(
    (artifact: any) => artifact.component === "portfolio_summary",
  );
  assert.ok(portfolioArtifact?.resultRef, "Portfolio Canvas reference missing");
  const portfolio = await business.resource(
    owner,
    "results",
    portfolioArtifact.resultRef,
  );
  assert.deepEqual(
    portfolio.payload.missing,
    [],
    "Available raw NVDA quote was not valued",
  );
  assert.equal(
    typeof portfolio.payload.total,
    "string",
    "Complete portfolio total missing",
  );
  const executions = (
    await pool.query(
      "SELECT profile_id,session_id,event_closed,terminal_receipt,tool_token_hash FROM executions",
    )
  ).rows;
  assert.equal(new Set(executions.map((e) => e.profile_id)).size, 3);
  assert.equal(new Set(executions.map((e) => e.session_id)).size, 3);
  assert.ok(
    executions.every(
      (e) =>
        e.event_closed &&
        e.terminal_receipt?.status === "completed" &&
        e.tool_token_hash === null,
    ),
  );
  const stranger = (
    await new AuthStore(pool).register(
      `${randomUUID()}@test.invalid`,
      "test-password-long",
    )
  ).id;
  await assert.rejects(store.snapshot(stranger, root.id), /not_found/);
  for (const artifact of snapshot.artifacts) {
    if (artifact.datasetRef)
      await assert.rejects(
        business.resource(stranger, "datasets", artifact.datasetRef),
        /not_found/,
      );
    if (artifact.resultRef)
      await assert.rejects(
        business.resource(stranger, "results", artifact.resultRef),
        /not_found/,
      );
  }
  assert.equal(new Set(harness.creates.map((c) => c.runId)).size, 3);
  const report = {
    passed: true,
    checkedAt: new Date().toISOString(),
    image: configuration.image,
    model: model.name,
    market: { source: provider.source, feed: provider.feed },
    roles: ["research", "prediction", "portfolio"],
    components,
    datasets: datasets.length,
    calculations: calculations.length,
    toolRequests: observed.length,
    nativeRuns: executions.length,
    crossUserDenied: true,
    syntheticMarket: false,
    syntheticModel: false,
    scope:
      "isolated Docker acceptance; production registry and public deployment remain separate",
  };
  await writeFile(
    join(configuration.evidenceRoot, "live-research-evidence.json"),
    JSON.stringify(report, null, 2) + "\n",
    { mode: 0o600 },
  );
  worker.child.kill("SIGTERM");
  assert.deepEqual(
    await bounded(worker.exit, 15000, "Worker shutdown timeout"),
    { code: 0, signal: null },
  );
  console.log(JSON.stringify(report));
  // Keep the gateway/database available until Python has stopped its containers.
  await lines.next();
} finally {
  await harness?.close();
  input.close();
  server.closeAllConnections();
  await new Promise<void>((resolve) => server.close(() => resolve()));
  await pool.end();
  if (created) await admin.query(`DROP SCHEMA ${schema} CASCADE`);
  await admin.end();
}
