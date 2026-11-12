import { before, after, test } from "node:test";
import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { readFile } from "node:fs/promises";
import { Pool } from "pg";
import { migrate } from "../../packages/server/src/migrate.js";
import {
  TaskStore,
  DomainError,
  type Role,
} from "../../packages/server/src/tasks.js";
import { BusinessStore } from "../../packages/server/src/business.js";
import {
  DataService,
  type ToolContext,
} from "../../packages/server/src/data-service.js";
import { QuantRunner } from "../../packages/server/src/quant-runner.js";
import { TaskWorker } from "../../packages/server/src/worker.js";
import {
  canonical as toolCanonical,
  sha256,
} from "../../packages/server/src/tool-context.js";
import {
  AlpacaProvider,
  parseFinancialJSON,
  type MarketProvider,
  type MarketQuery,
} from "../../packages/market/src/alpaca.js";

const url = process.env.TEST_DATABASE_URL;
if (!url || !new URL(url).pathname.endsWith("_test"))
  throw new Error("Dedicated TEST_DATABASE_URL ending in _test required");
const schema = `test_${randomUUID().replaceAll("-", "")}`;
const admin = new Pool({ connectionString: url }),
  pool = new Pool({
    connectionString: url,
    options: `-c search_path=${schema}`,
  });
const store = new TaskStore(pool),
  business = new BusinessStore(pool);
const rows: Record<string, string>[] = JSON.parse(
  await readFile(
    new URL("../fixtures/synthetic-market.json", import.meta.url),
    "utf8",
  ),
);
const provider: MarketProvider = {
  source: "synthetic-test-only",
  version: "fixture-v1",
  feed: "fixture",
  async bars(q: MarketQuery) {
    return rows.filter((r) => r.session! >= q.start && r.session! <= q.end);
  },
};
const service = new DataService(pool, provider);
before(async () => {
  await admin.query(`CREATE SCHEMA ${schema}`);
  await migrate(pool);
});
after(async () => {
  await pool.end();
  await admin.query(`DROP SCHEMA ${schema} CASCADE`);
  await admin.end();
});
async function setup() {
  const owner = randomUUID(),
    worker = randomUUID();
  await pool.query(
    "INSERT INTO users(id,email,password_hash) VALUES($1,$2,'test')",
    [owner, `${owner}@test.invalid`],
  );
  const profiles = new Map<Role, string>();
  for (const role of ["research", "prediction", "portfolio"] as const) {
    const id = randomUUID();
    profiles.set(role, id);
    await pool.query(
      "INSERT INTO profile_instances(id,owner_id,role) VALUES($1,$2,$3)",
      [id, owner, role],
    );
  }
  const thread = await store.createThread(owner);
  await store.submit(owner, thread.id, randomUUID(), { input: "research" });
  const root = (await store.claim(owner, worker))!.task;
  async function context(role: Role = "research") {
    let task = root;
    if (role !== "research") {
      await store.spawn(owner, root.id, worker, root.generation, role, role, {
        input: role,
      });
      task = (await store.claim(owner, worker))!.task;
    }
    const ctx = {
      ownerId: owner,
      taskId: task.id,
      profileId: profiles.get(role)!,
    };
    await store.prepareExecution(
      owner,
      task.id,
      worker,
      task.generation,
      { input: role },
      ctx.profileId,
      randomUUID(),
    );
    await store.beginSubmission(
      owner,
      task.id,
      worker,
      task.generation,
      "test-runtime",
    );
    return { ctx, task };
  }
  return { owner, worker, root, context };
}
const market = {
  symbol: "NVDA",
  start: "2025-01-02",
  end: "2025-08-01",
  adjustment: "split",
} as const;
async function dataset(ctx: ToolContext, input: unknown = market) {
  const result = await service.market(ctx, randomUUID(), input);
  assert.equal(result.status, "completed", result.error_code ?? "");
  return result.result!.datasetRef as string;
}

test("market facts and calculations are immutable, lossless, scoped and reproducible by reference", async () => {
  const a = await setup(),
    { ctx } = await a.context();
  const ref = await dataset(ctx);
  const fact = await business.resource(a.owner, "datasets", ref);
  assert.equal(fact.metadata.kind, "ohlcv");
  assert.equal(fact.metadata.source, "synthetic-test-only");
  assert.equal(fact.metadata.pointInTimeVerified, false);
  assert.ok(fact.metadata.sessionRange.start < market.start);
  const result = await service.calculate(ctx, "indicators", {
    kind: "indicators",
    datasetRef: ref,
  });
  assert.equal(result.status, "completed", result.error_code ?? "");
  const saved = await business.resource(
    a.owner,
    "results",
    result.result!.resultRef as string,
  );
  assert.equal(saved.kind, "indicators");
  assert.equal(saved.payload.rows[0].session, "2025-01-02");
  assert.notEqual(saved.payload.rows[0].ma20, null);
  assert.equal(saved.lineage.inputs[0].contentHash, fact.content_hash);
  assert.equal(saved.lineage.decimalPrecision, 34);
  await assert.rejects(
    pool.query("UPDATE results SET payload='{}' WHERE id=$1", [saved.id]),
    /immutable_fact/,
  );
  const { ctx: prediction } = await a.context("prediction");
  const predicted = await service.calculate(prediction, "prediction", {
    kind: "prediction",
    datasetRef: ref,
  });
  assert.equal(predicted.status, "completed", predicted.error_code ?? "");
  const p = await business.resource(
    a.owner,
    "results",
    predicted.result!.resultRef as string,
  );
  assert.equal(p.payload.points.length, 60);
  assert.equal(p.payload.evaluation, "retrospective");
  assert.equal(p.payload.label, "探索性统计基线");
});

test("tool schema rejects model prices and identity overrides; cross-user references cannot be used", async () => {
  const a = await setup(),
    b = await setup(),
    { ctx } = await a.context(),
    { ctx: other } = await b.context();
  const ref = await dataset(ctx);
  await assert.rejects(
    service.calculate(ctx, "fake", {
      kind: "indicators",
      datasetRef: ref,
      prices: [1, 2],
      ownerId: b.owner,
    }),
  );
  const result = await service.calculate(other, "foreign", {
    kind: "indicators",
    datasetRef: ref,
  });
  assert.equal(result.status, "failed");
  assert.equal(result.error_code, "not_found");
  await assert.rejects(
    service.calculate(ctx, "wrong-role", {
      kind: "prediction",
      datasetRef: ref,
    }),
    /tool_not_allowed/,
  );
  await assert.rejects(
    service.market({ ...ctx, profileId: other.profileId }, "forged", market),
    /not_found/,
  );
  const operation = (
    await pool.query("SELECT operation_id FROM datasets WHERE id=$1", [ref])
  ).rows[0];
  await assert.rejects(service.get(other, operation.operation_id), /not_found/);
});

test("stable operation keys deduplicate provider calls and refuse changed parameters", async () => {
  const a = await setup(),
    { ctx } = await a.context();
  let calls = 0;
  const counted = new DataService(pool, {
    ...provider,
    async bars(q, s) {
      calls++;
      return provider.bars(q, s);
    },
  });
  const results = await Promise.all([
    counted.market(ctx, "same", market),
    counted.market(ctx, "same", market),
  ]);
  assert.equal(results[0]!.id, results[1]!.id);
  assert.equal(calls, 1);
  const again = await counted.market(ctx, "same", market);
  assert.equal(again.status, "completed");
  assert.equal(calls, 1);
  await assert.rejects(
    counted.market(ctx, "same", { ...market, symbol: "AAPL" }),
    /operation_key_conflict/,
  );
});

test("manual portfolio uses raw same-day quotes; missing price produces partial value rather than zero", async () => {
  const a = await setup(),
    { ctx } = await a.context();
  const holdings = await business.saveHoldings(a.owner, {
    asOfSession: "2025-08-01",
    positions: [
      { symbol: "NVDA", quantity: "0.1" },
      { symbol: "AAPL", quantity: "1" },
    ],
    cashUsd: "0.2",
  });
  const ref = await dataset(ctx, { ...market, adjustment: "raw" });
  const { ctx: portfolio } = await a.context("portfolio");
  const result = await service.calculate(portfolio, "value", {
    kind: "portfolio",
    portfolioSnapshotRef: holdings.snapshotId,
    datasetRefs: [ref],
  });
  assert.equal(result.status, "completed", result.error_code ?? "");
  const saved = await business.resource(
    a.owner,
    "results",
    result.result!.resultRef as string,
  );
  assert.equal(saved.payload.status, "partial");
  assert.equal(saved.payload.total, null);
  assert.equal(saved.payload.positions[0].weight, null);
  assert.deepEqual(saved.payload.missing, [
    { symbol: "AAPL", reason: "missing_price" },
  ]);
  const sameDay = await business.saveHoldings(a.owner, {
    asOfSession: "2025-08-01",
    positions: [{ symbol: "NVDA", quantity: "0.1" }],
    cashUsd: "0.2",
  });
  const complete = await service.calculate(portfolio, "complete", {
    kind: "portfolio",
    portfolioSnapshotRef: sameDay.snapshotId,
    datasetRefs: [ref],
  });
  const value = await business.resource(
    a.owner,
    "results",
    complete.result!.resultRef as string,
  );
  const expected = (BigInt(rows.at(-1)!.close!) + 2n).toString();
  assert.equal(
    value.payload.total,
    `${expected.slice(0, -1)}.${expected.slice(-1)}`,
  );
});

test("cancel tracks tool exit and prevents successful task finalization while a tool is active", async () => {
  const a = await setup(),
    { ctx } = await a.context();
  let entered!: () => void;
  const started = new Promise<void>((resolve) => {
    entered = resolve;
  });
  const slow = new DataService(pool, {
    ...provider,
    async bars(_q, signal) {
      entered();
      return new Promise((_, reject) => {
        const stop = () => reject(new DomainError("operation_cancelled"));
        signal.addEventListener("abort", stop, { once: true });
        if (signal.aborted) stop();
      });
    },
  });
  const work = slow.market(ctx, "slow", market);
  await started;
  await assert.rejects(
    store.finish(a.owner, a.root.id, a.worker, a.root.generation, "completed", {
      text: "too early",
    }),
    /tools_still_running/,
  );
  await store.stop(a.owner, a.root.id);
  const result = await work;
  assert.equal(result.status, "cancelled");
  await store.finish(
    a.owner,
    a.root.id,
    a.worker,
    a.root.generation,
    "cancelled",
    {},
  );
});

test("missing provider and failed required calculation are explicit and block false task success", async () => {
  const a = await setup(),
    { ctx } = await a.context();
  const result = await new DataService(pool).market(
    ctx,
    "unconfigured",
    market,
  );
  assert.equal(result.status, "failed");
  assert.equal(result.error_code, "market_provider_unavailable");
  await assert.rejects(
    store.finish(a.owner, a.root.id, a.worker, a.root.generation, "completed", {
      text: "fake",
    }),
    /required_tool_failed/,
  );
});

test("lost tool executor becomes unknown without re-running its operation", async () => {
  const a = await setup(),
    { ctx } = await a.context();
  const id = randomUUID();
  // A lost executor has no exit receipt. Do not manufacture this state by
  // reverting a completed operation, which already has durable exit evidence.
  await pool.query(
    `INSERT INTO tool_operations(id,owner_id,task_id,operation_key,kind,request_hash,request,status,started_at)
     VALUES($1,$2,$3,'lost','market',$4,$5,'running',now()-interval '1 minute')`,
    [
      id,
      a.owner,
      ctx.taskId,
      sha256(toolCanonical({ kind: "market", request: market })),
      JSON.stringify(market),
    ],
  );
  assert.equal(
    (await store.operations(a.owner, a.root.id))[0]!.status,
    "unknown",
  );
  assert.equal((await service.market(ctx, "lost", market)).status, "unknown");
  await assert.rejects(
    service.market(ctx, "replacement", market),
    /tool_execution_unknown/,
  );
  assert.equal(
    (
      await pool.query(
        "SELECT 1 FROM tool_exit_receipts WHERE operation_id=$1",
        [id],
      )
    ).rowCount,
    0,
  );
});

function deferred() {
  let resolve!: () => void;
  const promise = new Promise<void>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}
test("late valid facts after cancellation are partial and commit with exit evidence", async () => {
  const a = await setup(),
    { ctx } = await a.context();
  const entered = deferred(),
    exit = deferred();
  class DelayedReturn extends QuantRunner {
    override async run(input: Record<string, unknown>, signal?: AbortSignal) {
      const result = await super.run(input, signal);
      if (input.operation === "validate_market") {
        entered.resolve();
        await exit.promise;
      }
      return result;
    }
  }
  const late = new DataService(pool, provider, new DelayedReturn());
  const work = late.market(ctx, "late-result", market);
  try {
    await entered.promise;
    await store.stop(a.owner, ctx.taskId);
    await pool.query(
      "UPDATE tool_operations SET started_at=now()-interval '1 minute' WHERE task_id=$1",
      [ctx.taskId],
    );
    assert.equal(
      (await store.operations(a.owner, ctx.taskId))[0]!.status,
      "unknown",
    );
    exit.resolve();
    const result = await work;
    assert.equal(result.status, "cancelled");
    assert.equal(result.result?.partial, true);
    const receipt = (
      await pool.query(
        "SELECT * FROM tool_exit_receipts WHERE operation_id=$1",
        [result.id],
      )
    ).rows[0];
    assert.deepEqual(receipt.result, result.result);
    const dataset = await business.resource(
      a.owner,
      "datasets",
      result.result!.datasetRef as string,
    );
    assert.equal(dataset.operation_id, result.id);
    await assert.rejects(
      store.finish(
        a.owner,
        ctx.taskId,
        a.worker,
        a.root.generation,
        "completed",
        { text: "too late" },
      ),
      /cancellation_pending/,
    );
  } finally {
    exit.resolve();
    await work;
  }
});

test("failed final commit leaves no fact or exit receipt and cannot rerun the operation", async () => {
  const a = await setup(),
    { ctx } = await a.context();
  await pool.query(`CREATE FUNCTION simulate_tool_commit_failure() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN IF NEW.operation_key='commit-failure' AND NEW.status='completed' THEN
      RAISE EXCEPTION 'simulated_commit_failure'; END IF; RETURN NEW; END $$;
    CREATE TRIGGER simulate_tool_commit_failure BEFORE UPDATE ON tool_operations
      FOR EACH ROW EXECUTE FUNCTION simulate_tool_commit_failure()`);
  try {
    await assert.rejects(
      service.market(ctx, "commit-failure", market),
      /simulated_commit_failure/,
    );
    const operation = (
      await pool.query("SELECT * FROM tool_operations WHERE task_id=$1", [
        ctx.taskId,
      ])
    ).rows[0];
    assert.equal(operation.status, "running");
    assert.equal(
      (
        await pool.query(
          "SELECT 1 FROM tool_exit_receipts WHERE operation_id=$1",
          [operation.id],
        )
      ).rowCount,
      0,
    );
    assert.equal(
      (
        await pool.query("SELECT 1 FROM datasets WHERE operation_id=$1", [
          operation.id,
        ])
      ).rowCount,
      0,
    );
    assert.equal(
      (await store.audit(a.owner, ctx.taskId)).items.filter(
        (row) => row.event === "tool.transition",
      ).length,
      0,
    );
    await pool.query(
      "UPDATE tool_operations SET started_at=now()-interval '1 minute' WHERE id=$1",
      [operation.id],
    );
    await store.operations(a.owner, ctx.taskId);
    assert.equal(
      (await service.market(ctx, "commit-failure", market)).status,
      "unknown",
    );
    await assert.rejects(
      service.market(ctx, "replacement", market),
      /tool_execution_unknown/,
    );
  } finally {
    await pool.query(
      "DROP TRIGGER simulate_tool_commit_failure ON tool_operations; DROP FUNCTION simulate_tool_commit_failure()",
    );
  }
});

for (const cancel of [false, true]) {
  test(`original executor exit resolves unknown without rerun; cancel=${cancel}`, async () => {
    const a = await setup(),
      { ctx } = await a.context();
    const entered = deferred(),
      aborted = deferred(),
      exit = deferred();
    let executions = 0;
    const slow = new DataService(pool, {
      ...provider,
      async bars(_query, signal) {
        executions++;
        signal.addEventListener("abort", () => aborted.resolve(), {
          once: true,
        });
        if (signal.aborted) aborted.resolve();
        entered.resolve();
        // Aborting is not exiting. Hold this executor until explicitly released.
        await exit.promise;
        throw new DomainError("operation_cancelled");
      },
    });
    const work = slow.market(ctx, "original", market);
    try {
      await entered.promise;
      await pool.query(
        "UPDATE tool_operations SET started_at=now()-interval '1 minute' WHERE task_id=$1",
        [ctx.taskId],
      );
      const [unknown] = await store.operations(a.owner, ctx.taskId);
      assert.equal(unknown!.status, "unknown");
      await aborted.promise;
      assert.equal(
        (await slow.market(ctx, "original", market)).status,
        "unknown",
      );
      await assert.rejects(
        slow.market(ctx, "new-key", market),
        /tool_execution_unknown/,
      );
      if (cancel) await store.stop(a.owner, ctx.taskId);
      const execution = (await store.execution(a.owner, ctx.taskId))!;
      await store.bindUpstream(
        a.owner,
        ctx.taskId,
        a.worker,
        a.root.generation,
        "original-run",
      );
      await store.recordReceipt(
        a.owner,
        ctx.taskId,
        a.worker,
        a.root.generation,
        {
          run_id: "original-run",
          session_id: execution.session_id,
          status: "completed",
          output: "Upstream is finished but the tool has not exited.",
        },
      );
      await store.defer(
        a.owner,
        ctx.taskId,
        a.worker,
        a.root.generation,
        "unknown",
        "waiting_for_tools",
        0,
      );
      const next = await store.submit(a.owner, a.root.thread_id, randomUUID(), {
        input: "next",
      });
      const worker = new TaskWorker(store, () => {
        throw new Error("must not create or poll replacement");
      });
      await worker.once(a.owner);
      assert.equal((await store.get(a.owner, ctx.taskId)).status, "unknown");
      assert.equal((await store.get(a.owner, next.id)).status, "queued");
      assert.equal(
        (
          await pool.query(
            "SELECT 1 FROM tool_exit_receipts WHERE task_id=$1",
            [ctx.taskId],
          )
        ).rowCount,
        0,
      );
      exit.resolve();
      const result = await work;
      assert.equal(result.status, cancel ? "cancelled" : "failed");
      assert.equal(executions, 1);
      const replay = await slow.market(ctx, "original", market);
      assert.equal(replay.id, result.id);
      assert.equal(replay.status, result.status);
      assert.deepEqual(replay.result, result.result);
      assert.equal(executions, 1);
      const receipt = (
        await pool.query(
          "SELECT * FROM tool_exit_receipts WHERE operation_id=$1",
          [result.id],
        )
      ).rows[0];
      assert.equal(receipt.status, result.status);
      const audit = await store.audit(a.owner, ctx.taskId);
      const recovered = audit.items.find(
        (row) =>
          row.operation_id === result.id &&
          row.previous_status === "unknown" &&
          row.status === result.status,
      );
      assert.equal(recovered?.receipt_id, result.id);
      await pool.query("UPDATE tasks SET next_attempt_at=now() WHERE id=$1", [
        ctx.taskId,
      ]);
      await worker.once(a.owner);
      assert.equal(
        (await store.get(a.owner, ctx.taskId)).status,
        cancel ? "cancelled" : "failed",
      );
      assert.equal((await store.take(a.owner, randomUUID()))!.id, next.id);
      await assert.rejects(
        pool.query(
          "UPDATE tool_exit_receipts SET status='completed' WHERE operation_id=$1",
          [result.id],
        ),
        /immutable_execution_evidence/,
      );
      await assert.rejects(
        pool.query("DELETE FROM tool_exit_receipts WHERE operation_id=$1", [
          result.id,
        ]),
        /immutable_execution_evidence/,
      );
    } finally {
      exit.resolve();
      await work;
    }
  });
}

test("Alpaca adapter preserves decimal lexemes, paginates and records explicit feed/adjustment", async () => {
  assert.deepEqual(
    parseFinancialJSON(
      '{"price":123.123456789123456789,"volume":9007199254740993}',
    ),
    { price: "123.123456789123456789", volume: "9007199254740993" },
  );
  const requests: URL[] = [];
  const transport: typeof fetch = async (input, init) => {
    const u = new URL(input.toString());
    requests.push(u);
    assert.equal(new Headers(init?.headers).get("APCA-API-KEY-ID"), "test-key");
    assert.equal(init?.redirect, "error");
    return new Response(
      `{"bars":{"NVDA":[{"t":"2025-01-0${requests.length + 1}T05:00:00Z","o":123.123456789123456789,"h":124,"l":123,"c":123.5,"v":9007199254740993}]},"next_page_token":${requests.length === 1 ? '"next"' : "null"}}`,
    );
  };
  const adapter = new AlpacaProvider(
    "test-key",
    "test-secret",
    "iex",
    transport,
  );
  const bars = (await adapter.bars(
    {
      symbol: "NVDA",
      start: "2025-01-02",
      end: "2025-01-03",
      adjustment: "split",
    },
    new AbortController().signal,
  )) as Record<string, string>[];
  assert.equal(bars[0]!.open, "123.123456789123456789");
  assert.equal(bars[0]!.volume, "9007199254740993");
  assert.equal(bars[0]!.session, "2025-01-02");
  assert.equal(requests.length, 2);
  assert.equal(requests[1]!.searchParams.get("page_token"), "next");
  assert.equal(requests[0]!.searchParams.get("feed"), "iex");
  assert.equal(requests[0]!.searchParams.get("adjustment"), "split");
  const denied = new AlpacaProvider(
    "test-key",
    "test-secret",
    "iex",
    async () => new Response("private error details", { status: 403 }),
  );
  await assert.rejects(
    denied.bars({ ...market }, new AbortController().signal),
    /market_http_403/,
  );
});

test("Quant subprocess rejects pre-cancelled work and does not return raw errors", async () => {
  const controller = new AbortController();
  controller.abort();
  await assert.rejects(
    new QuantRunner().run({ operation: "indicators" }, controller.signal),
    /operation_cancelled/,
  );
  await assert.rejects(
    new QuantRunner().run({ operation: "unknown" }),
    /unsupported_operation/,
  );
});
