import { ResearchScheduler } from "../../packages/server/src/research-scheduler.js";
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
import { QuantRunner } from "../../packages/server/src/quant-runner.js";
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
const ticker = configuration.symbol ?? "NVDA";
assert.match(ticker, /^[A-Z]{1,5}$/);
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
process.env.ALPACA_DATA_KEY = market.apiKey;
process.env.ALPACA_DATA_SECRET = market.apiSecret;
process.env.ALPACA_DATA_FEED = market.feed;
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
  const email = `${randomUUID()}@test.invalid`;
  const owner = (
    await new AuthStore(pool).register(email, "test-password-long")
  ).id;
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
  const scheduler = new ResearchScheduler(pool, {
    research: {syncCoverage: async()=>{}, listCoverage: async()=>[], collect: async()=>({}), context: async()=>({})},
    scanner: {scan: async()=>({selected:[]})}, forecasts: {evaluateDue: async()=>({})},
  });
  const schedule = await new QuantRunner().run({operation:"research_schedule",asOf:new Date().toISOString()});
  const job = await scheduler.enqueueJob(owner,ticker,"temporary",schedule.latestCompletedSession,randomUUID());
  const researchStarted = Date.now();
  const worker = harness.start();
  let current: any;
  const deadline = Date.now()+1400000;
  while(Date.now()<deadline) {
    await scheduler.advanceJobs();
    current=(await pool.query("SELECT * FROM research_jobs WHERE id=$1",[job.id])).rows[0];
    if (["completed","failed","blocked"].includes(current.status)) break;
    await delay(1000);
  }
  const tasks=(await pool.query("SELECT id,role,status,failure_code,input,stream_text FROM tasks WHERE owner_id=$1 ORDER BY created_at",[owner])).rows;
  const forecasts=(await pool.query("SELECT * FROM forecasts WHERE owner_id=$1 ORDER BY horizon",[owner])).rows;
  const datasets=(await pool.query("SELECT id,metadata FROM datasets WHERE owner_id=$1",[owner])).rows;
  const evidence=(await pool.query("SELECT source,count(*)::int FROM research_evidence WHERE owner_id=$1 GROUP BY source",[owner])).rows;
  const report={researchDurationMs:Date.now()-researchStarted,passed:current.status==="completed" && forecasts.length===2,owner,email,schema,job:current,tasks,forecasts,datasets,evidence,tools:observed,syntheticMarket:false,syntheticModel:false};
  await writeFile(join(configuration.evidenceRoot,"research-result.json"),JSON.stringify(report,null,2),{mode:0o600});
  assert.equal(current.status,"completed");
  assert.deepEqual(forecasts.map(f=>f.horizon),[5,20]);
  assert.ok(evidence.length);
  assert.ok(tasks.every(t=>t.status==="completed"));
  const linked = (await pool.query("SELECT f.id FROM forecasts f JOIN research_contexts x ON x.id=f.research_context_id AND x.owner_id=f.owner_id WHERE f.owner_id=$1 AND jsonb_array_length(x.payload->'evidence')>0 AND jsonb_array_length(x.payload->'datasetRefs')>0",[owner])).rows;
  assert.equal(linked.length,2);
  const artifacts=(await pool.query("SELECT a.* FROM task_artifacts a JOIN tasks t ON t.id=a.source_task_id WHERE t.owner_id=$1",[owner])).rows;
  assert.equal(artifacts.length,2);
  assert.equal((await pool.query("SELECT count(*)::int AS n FROM feishu_outbox")).rows[0].n,0);
  assert.ok(datasets.every(d=>d.metadata.source==="alpaca"));
  worker.child.kill("SIGTERM");
  await bounded(worker.exit,15000,"shutdown");
  console.log(JSON.stringify({passed:true,owner,schema,forecasts:forecasts.map(f=>({id:f.id,horizon:f.horizon})),nativeRuns:4,components:[],syntheticMarket:false,syntheticModel:false}));
  // Keep the gateway/database available until Python has stopped its containers.
  await lines.next();
} finally {
  await harness?.close();
  input.close();
  server.closeAllConnections();
  await new Promise<void>((resolve) => server.close(() => resolve()));
  await pool.end();
  // Preserve this dedicated test schema for browser and scheduler acceptance.
  if (created)
    await writeFile(
      join(configuration.evidenceRoot, "database-scope.json"),
      JSON.stringify({ schema }),
      { mode: 0o600 },
    );
  await admin.end();
}
