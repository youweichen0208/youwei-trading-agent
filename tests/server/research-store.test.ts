import { before, after, test } from "node:test";
import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { Pool } from "pg";
import { migrate } from "../../packages/server/src/migrate.js";
import { BusinessStore } from "../../packages/server/src/business.js";
import { ResearchStore } from "../../packages/server/src/research-store.js";
import {
  MarketScanner,
  type ScanProvider,
} from "../../packages/server/src/market-scanner.js";
import type { SourceResult } from "../../packages/server/src/research-sources.js";
import { QuantRunner } from "../../packages/server/src/quant-runner.js";

const url = process.env.TEST_DATABASE_URL;
if (!url || !new URL(url).pathname.endsWith("_test"))
  throw new Error("Dedicated TEST_DATABASE_URL ending in _test required");
const schema = `test_${randomUUID().replaceAll("-", "")}`;
const admin = new Pool({ connectionString: url }),
  pool = new Pool({
    connectionString: url,
    options: `-c search_path=${schema}`,
  });
before(async () => {
  await admin.query(`CREATE SCHEMA ${schema}`);
  await migrate(pool);
});
after(async () => {
  await pool.end();
  await admin.query(`DROP SCHEMA ${schema} CASCADE`);
  await admin.end();
});
async function owner() {
  const id = randomUUID();
  await pool.query(
    "INSERT INTO users(id,email,password_hash) VALUES($1,$2,'test')",
    [id, `${id}@test.invalid`],
  );
  return id;
}
function fixture(
  observedAt = "2025-01-03T18:00:00Z",
  text = "First evidence",
): SourceResult[] {
  return [
    {
      source: "web",
      status: "available",
      failures: [],
      documents: [
        {
          source: "web",
          sourceKey: "https://example.com/ir",
          publishedAt: "2025-01-02T18:00:00Z",
          observedAt,
          payload: { text, url: "https://example.com/ir" },
        },
      ],
    },
  ];
}
test("collections are idempotent and evidence is immutable, owner-scoped and filtered by when actually observed", async () => {
  const a = await owner(),
    b = await owner();
  let calls = 0,
    documents = fixture();
  const store = new ResearchStore(pool, {
    async collect() {
      calls++;
      return documents;
    },
  });
  const first = await store.collect(
    { ownerId: a },
    { symbol: "NVDA", sources: ["web"] },
    "collection-1",
  );
  const repeated = await store.collect(
    { ownerId: a },
    { symbol: "NVDA", sources: ["web"] },
    "collection-1",
  );
  assert.equal(first.collectionRef, repeated.collectionRef);
  assert.equal(calls, 1);
  await assert.rejects(
    store.collect(
      { ownerId: a },
      { symbol: "AMD", sources: ["web"] },
      "collection-1",
    ),
    /operation_key_conflict/,
  );
  const second = await store.collect(
    { ownerId: a },
    { symbol: "NVDA", sources: ["web"] },
    "collection-2",
  );
  assert.deepEqual(second.evidenceRefs, first.evidenceRefs);
  assert.equal(second.fingerprint, first.fingerprint);
  const beforeObserved = await store.context(a, "NVDA", "2025-01-03T17:00:00Z");
  assert.deepEqual(beforeObserved.evidenceRefs, []);
  const known = await store.context(a, "NVDA", "2025-01-03T19:00:00Z");
  assert.deepEqual(known.evidenceRefs, first.evidenceRefs);
  assert.match(known.evidence[0]!.eventKey, /^[a-f0-9]{64}$/);
  await assert.rejects(store.loadContext(b, known.contextRef), /not_found/);
  await assert.rejects(
    pool.query("UPDATE research_evidence SET payload='{}' WHERE id=$1", [
      first.evidenceRefs[0],
    ]),
    /immutable_fact/,
  );
  await assert.rejects(
    pool.query("UPDATE research_contexts SET payload='{}' WHERE id=$1", [
      known.contextRef,
    ]),
    /immutable_fact/,
  );
  documents = fixture("2025-01-04T18:00:00Z", "Revised evidence");
  const revision = await store.collect(
    { ownerId: a },
    { symbol: "NVDA", sources: ["web"] },
    "collection-3",
  );
  assert.notDeepEqual(revision.evidenceRefs, first.evidenceRefs);
  const restored = await store.loadContext(a, known.contextRef);
  assert.equal(restored.evidence[0].payload.text, "First evidence");
  const now = await store.context(a, "NVDA", "2025-01-05T18:00:00Z");
  assert.equal(now.evidence.length, 1);
  assert.equal(now.evidence[0]!.eventKey, known.evidence[0]!.eventKey);
  assert.equal(now.evidence[0].payload.text, "Revised evidence");
  assert.equal((await store.stock(b, "NVDA")).counts.evidence, 0);
});

test("source failure and future timestamps do not become successful research evidence", async () => {
  const user = await owner();
  const missing = new ResearchStore(pool, {
    async collect() {
      return [
        {
          source: "x",
          status: "unavailable",
          documents: [],
          failures: ["missing_configuration"],
        },
      ];
    },
  });
  const failed = await missing.collect(
    { ownerId: user },
    { symbol: "NVDA", sources: ["x"] },
    "missing",
  );
  assert.equal(failed.status, "unavailable");
  assert.deepEqual(failed.evidenceRefs, []);
  const future = new ResearchStore(pool, {
    async collect() {
      return fixture("2099-01-01T00:00:00Z");
    },
  });
  const invalid = await future.collect(
    { ownerId: user },
    { symbol: "NVDA", sources: ["web"] },
    "future",
  );
  assert.equal(invalid.status, "unavailable");
  assert.equal(invalid.evidenceRefs.length, 0);
  assert.ok(
    invalid.failures.some(
      (item: any) => item.reason === "invalid_evidence_time",
    ),
  );
});

test("frozen context includes only personal price datasets already known at its cutoff", async () => {
  const user = await owner(),
    store = new ResearchStore(pool);
  async function dataset(
    kind: "personal" | "group",
    created: string,
    marketAsOf: string,
  ) {
    const thread = randomUUID(),
      task = randomUUID(),
      operation = randomUUID(),
      id = randomUUID();
    await pool.query("INSERT INTO threads(id,owner_id) VALUES($1,$2)", [
      thread,
      user,
    ]);
    if (kind === "group") {
      const context = randomUUID();
      await pool.query(
        "INSERT INTO execution_contexts(id,owner_id,kind,app_id,chat_id) VALUES($1,$2,'group','test',$3)",
        [context, user, context],
      );
      await pool.query(
        "UPDATE thread_contexts SET context_id=$2 WHERE thread_id=$1",
        [thread, context],
      );
    }
    const workspace = randomUUID();
    await pool.query(
      "INSERT INTO workspaces(id,owner_id,thread_id) VALUES($1,$2,$3)",
      [workspace, user, thread],
    );
    await pool.query(
      "INSERT INTO tasks(id,owner_id,thread_id,root_id,role,request_key,request_hash,input,workspace_revision,workspace_id) VALUES($1,$2,$3,$1,'research',$4,'test','{}',0,$5)",
      [task, user, thread, task, workspace],
    );
    await pool.query(
      "INSERT INTO tool_operations(id,owner_id,task_id,operation_key,kind,request_hash,request,status) VALUES($1,$2,$3,$4,'market','test','{}','completed')",
      [operation, user, task, operation],
    );
    await pool.query(
      "INSERT INTO datasets(id,owner_id,metadata,payload,content_hash,operation_id,created_at) VALUES($1,$2,$3,'{}','test',$4,$5)",
      [
        id,
        user,
        JSON.stringify({
          kind: "ohlcv",
          symbol: "NVDA",
          adjustment: "split",
          feed: "fixture",
          asOf: marketAsOf,
        }),
        operation,
        created,
      ],
    );
    return id;
  }
  const allowed = await dataset(
    "personal",
    "2025-01-02T22:00:00Z",
    "2025-01-02T21:00:00Z",
  );
  await dataset("group", "2025-01-03T22:00:00Z", "2025-01-03T21:00:00Z");
  await dataset("personal", "2025-01-04T22:00:00Z", "2025-01-04T21:00:00Z");
  await dataset("personal", "2025-01-03T22:00:00Z", "2025-01-04T21:00:00Z");
  const context = await store.context(user, "NVDA", "2025-01-03T23:00:00Z");
  assert.deepEqual(context.datasetRefs, [allowed]);
});

test("coverage follows latest real holdings and explicit watchlist; temporary stocks expire by trading sessions", async () => {
  const user = await owner(),
    business = new BusinessStore(pool),
    store = new ResearchStore(pool);
  const portfolio = await business.saveHoldings(user, {
    asOfSession: "2025-01-02",
    cashUsd: "0",
    positions: [{ symbol: "NVDA", quantity: "2" }],
  });
  await business.watchlist(user, "AMD");
  const coverage = await store.listCoverage(user);
  assert.equal(coverage[0]!.symbol, "NVDA");
  assert.equal(coverage[0]!.tier, "holding");
  assert.equal(coverage[1]!.tier, "watchlist");
  await store.updateCoverage(user, { symbol: "NVDA", action: "remove" });
  assert.equal(
    (await store.listCoverage(user)).find((row) => row.symbol === "NVDA")!
      .paused,
    true,
  );
  assert.equal((await business.portfolios(user))[0].positions[0].quantity, "2");
  await store.updateCoverage(user, { symbol: "AMD", action: "remove" });
  assert.deepEqual(await business.watchlist(user), []);
  await store.touchTemporary(user, "TSLA");
  const temp = (await store.listCoverage(user)).find(
    (row) => row.symbol === "TSLA",
  )!;
  const schedule = await new QuantRunner().run({
    operation: "research_schedule",
    asOf: new Date().toISOString(),
  });
  assert.equal(temp.expires_session, schedule.temporaryExpirySession);
  await pool.query(
    "UPDATE research_coverage SET paused=true,expires_session='2025-01-02' WHERE owner_id=$1 AND symbol='TSLA'",
    [user],
  );
  const resumed = await store.updateCoverage(user, {
    symbol: "TSLA",
    action: "resume",
  });
  assert.equal(
    resumed.find((row) => row.symbol === "TSLA")!.expires_session,
    schedule.temporaryExpirySession,
  );
  await business.saveHoldings(
    user,
    { asOfSession: "2025-01-03", cashUsd: "10", positions: [] },
    portfolio.portfolioId,
  );
  assert.equal(
    (await store.listCoverage(user)).find((row) => row.symbol === "NVDA")!.tier,
    "candidate",
  );
  assert.equal((await store.settings(user)).dailyResearchLimit, 30);
  assert.equal(
    (await store.settings(user, { dailyResearchLimit: 15 })).dailyResearchLimit,
    15,
  );
  await assert.rejects(
    store.settings(user, { dailyResearchLimit: 5 }),
    /candidate_limit_exceeds_research_limit/,
  );
});

test("scanner persists observed universe, missing coverage and deterministic daily candidates", async () => {
  const user = await owner();
  const quant = new QuantRunner();
  const schedule = await quant.run({
    operation: "research_schedule",
    asOf: new Date().toISOString(),
  });
  const session = schedule.latestCompletedSession;
  let calls = 0;
  const provider: ScanProvider = {
    feed: "fixture-only",
    async assets() {
      return [
        { symbol: "AAA", name: "AAA", exchange: "NASDAQ" },
        { symbol: "MISSING", name: "Missing", exchange: "NYSE" },
      ];
    },
    async bars(symbols) {
      calls++;
      return Object.fromEntries(
        symbols
          .filter((ticker) => ticker !== "MISSING")
          .map((ticker) => [
            ticker,
            schedule.recentCompletedSessions.map((date: string, i: number) => ({
              session: date,
              close: 100 + (ticker === "AAA" ? i : 0),
              volume: 100 + i,
            })),
          ]),
      );
    },
    async announcements() {
      return { counts: { AAA: 2 }, complete: true };
    },
  };
  const scanner = new MarketScanner(pool, provider);
  const failed = randomUUID();
  await pool.query(
    "INSERT INTO market_scan_runs(id,owner_id,session,payload,created_at) VALUES($1,$2,$3,$4,now()-interval '6 minutes')",
    [
      failed,
      user,
      session,
      JSON.stringify({
        session,
        status: "unavailable",
        selected: [],
        failures: ["source_http_503"],
      }),
    ],
  );
  const first = await scanner.scan(user, session),
    second = await scanner.scan(user, session);
  assert.notEqual(first.scanRef, failed);
  assert.equal(first.scanRef, second.scanRef);
  assert.equal(calls, 1);
  assert.equal(first.status, "partial");
  assert.ok(first.coverage.missing.includes("MISSING"));
  assert.equal(first.selected[0].symbol, "AAA");
  assert.equal(
    (
      await pool.query(
        "SELECT count(*)::int AS n FROM market_scan_runs WHERE owner_id=$1",
        [user],
      )
    ).rows[0].n,
    2,
  );
  await assert.rejects(
    pool.query("UPDATE market_scan_runs SET payload='{}' WHERE id=$1", [
      first.scanRef,
    ]),
    /immutable_fact/,
  );
});
