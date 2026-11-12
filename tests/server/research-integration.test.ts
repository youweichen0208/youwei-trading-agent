import { before, after, test } from "node:test";
import assert from "node:assert/strict";
import { randomUUID, randomBytes } from "node:crypto";
import { readFile } from "node:fs/promises";
import { Pool } from "pg";
import { migrate } from "../../packages/server/src/migrate.js";
import { AuthStore } from "../../packages/server/src/auth.js";
import { TaskStore, type Task } from "../../packages/server/src/tasks.js";
import { ToolCapabilities } from "../../packages/server/src/tool-context.js";
import { ToolGateway } from "../../packages/server/src/tool-gateway.js";
import { DataService } from "../../packages/server/src/data-service.js";
import { ForecastStore } from "../../packages/server/src/forecast-store.js";
import { createHandler } from "../../packages/server/src/http.js";
import { artifactCards } from "../../packages/server/src/feishu-cards.js";

const url = process.env.TEST_DATABASE_URL;
if (!url || !new URL(url).pathname.endsWith("_test"))
  throw new Error("Dedicated TEST_DATABASE_URL ending in _test required");
const schema = `test_${randomUUID().replaceAll("-", "")}`;
const admin = new Pool({ connectionString: url });
const pool = new Pool({
  connectionString: url,
  options: `-c search_path=${schema}`,
});
const tasks = new TaskStore(pool);
const worker = randomUUID();
const capabilities = new ToolCapabilities(
  pool,
  randomBytes(32).toString("hex"),
);
const rows: any[] = JSON.parse(
  await readFile(
    new URL("../fixtures/synthetic-market.json", import.meta.url),
    "utf8",
  ),
);
const gateway = new ToolGateway(
  pool,
  new DataService(pool, {
    source: "synthetic-test-only",
    version: "fixture-v1",
    feed: "fixture",
    async bars(query) {
      return rows.filter(
        (r) => r.session >= query.start && r.session <= query.end,
      );
    },
  }),
);
const origin = "http://localhost:3400";
const handle = createHandler(pool, origin);
before(async () => {
  await admin.query(`CREATE SCHEMA ${schema}`);
  await migrate(pool);
});
after(async () => {
  await pool.end();
  await admin.query(`DROP SCHEMA ${schema} CASCADE`);
  await admin.end();
});
async function bind(task: Task) {
  const profile = (
    await pool.query(
      "SELECT id FROM profile_instances WHERE owner_id=$1 AND role=$2",
      [task.owner_id, task.role],
    )
  ).rows[0].id;
  await tasks.prepareExecution(
    task.owner_id,
    task.id,
    worker,
    task.generation,
    { input: task.role },
    profile,
    randomUUID(),
  );
  const token = await capabilities.issue(
    task.owner_id,
    task.id,
    worker,
    task.generation,
  );
  await tasks.beginSubmission(
    task.owner_id,
    task.id,
    worker,
    task.generation,
    "fixture-runtime",
  );
  return ToolCapabilities.authenticate(pool, token);
}
async function account() {
  const user = await new AuthStore(pool).register(
    `${randomUUID()}@research.test`,
    "research-test-password",
  );
  const session = await new AuthStore(pool).login(
    user.email!,
    "research-test-password",
  );
  return { owner: user.id, cookie: `alpha_session=${session.token}` };
}
async function request(
  cookie: string,
  path: string,
  method = "GET",
  body?: unknown,
) {
  return handle(
    new Request(`${origin}/api/${path}`, {
      method,
      headers: { origin, cookie, "Content-Type": "application/json" },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    }),
  );
}

test("personal research tools freeze evidence, publish both horizons, render cards and expose real outcomes", async () => {
  const a = await account();
  const thread = await tasks.createThread(a.owner);
  await tasks.submit(a.owner, thread.id, randomUUID(), {
    input: "NVDA complete research",
  });
  const root = (await tasks.claim(a.owner, worker))!.task;
  const rootCtx = await bind(root);
  const evidence = randomUUID();
  await pool.query(
    "INSERT INTO research_evidence(id,owner_id,symbol,source,source_key,published_at,observed_at,content_hash,payload) VALUES($1,$2,'NVDA','sec','fixture-filing',now()-interval '2 minutes',now()-interval '1 minute','fixture',$3)",
    [
      evidence,
      a.owner,
      JSON.stringify({
        title: "Synthetic filing",
        url: "https://example.test/filing",
        text: "Synthetic test evidence only",
      }),
    ],
  );
  const history: any = await gateway.invoke(
    rootCtx,
    "market_history",
    {
      symbol: "NVDA",
      start: "2025-01-02",
      end: "2025-08-01",
      adjustment: "split",
    },
    randomUUID(),
  );
  const datasetRef = history.result.datasetRef;
  const context: any = await gateway.invoke(
    rootCtx,
    "read_research_context",
    { symbol: "NVDA" },
    randomUUID(),
  );
  assert.ok(context.datasetRefs.includes(datasetRef));
  assert.ok(context.evidenceRefs.includes(evidence));
  await gateway.invoke(
    rootCtx,
    "submit_specialist",
    {
      role: "prediction",
      requestKey: "forecast",
      question: "Two forward horizons",
      datasetRefs: [datasetRef],
      researchContextRef: context.contextRef,
    },
    randomUUID(),
  );
  const child = (await tasks.claim(a.owner, worker))!.task;
  assert.equal(child.input.researchContextRef, context.contextRef);
  const ctx = await bind(child);
  const primaryContext: any = await gateway.invoke(
    ctx,
    "read_research_context",
    { symbol: "NVDA", contextRef: context.contextRef },
    randomUUID(),
  );
  assert.equal(Object.hasOwn(primaryContext, "trialCandidates"), false);
  const page: any = await gateway.invoke(
    ctx,
    "read_research_context",
    { contextRef: context.contextRef, evidenceOffset: 0 },
    randomUUID(),
  );
  assert.equal(page.contextRef, context.contextRef);
  assert.equal(page.symbol, "NVDA");
  const published: any[] = [];
  for (const horizon of [5, 20]) {
    const input = {
      symbol: "NVDA",
      horizon,
      meanReturn: "0.05",
      lowerReturn: "-0.05",
      upperReturn: "0.15",
      upProbability: "0.65",
      researchContextRef: context.contextRef,
      evidenceRefs: [evidence],
      datasetRefs: [datasetRef],
      eventKey: "fixture-filing",
      thesis: "Synthetic evidence supports a test thesis",
    };
    const key = randomUUID();
    const result: any = await gateway.invoke(
      ctx,
      "publish_forecast",
      input,
      key,
    );
    assert.deepEqual(
      await gateway.invoke(ctx, "publish_forecast", input, key),
      result,
    );
    published.push(result);
    await gateway.invoke(
      ctx,
      "canvas_upsert",
      {
        component: "forecast_result",
        title: `NVDA ${horizon}日`,
        resultRef: result.resultRef,
      },
      randomUUID(),
    );
    const saved = (
      await pool.query("SELECT * FROM results WHERE id=$1", [result.resultRef])
    ).rows[0];
    const cards = artifactCards(
      {
        component: "forecast_result",
        title: "NVDA",
        resultRef: result.resultRef,
      },
      saved,
      origin,
    );
    assert.match(JSON.stringify(cards), /80%/);
    assert.match(JSON.stringify(cards), /预期收益：5(?:\.0+)?%/);
  }
  assert.equal(
    (
      await pool.query("SELECT count(*) FROM forecasts WHERE owner_id=$1", [
        a.owner,
      ])
    ).rows[0].count,
    "2",
  );
  const summary: any = await gateway.invoke(
    rootCtx,
    "save_research_summary",
    {
      text: "Both forward forecasts saved",
      datasetRefs: [],
      resultRefs: published.map((p) => p.resultRef),
      researchContextRef: context.contextRef,
    },
    randomUUID(),
  );
  assert.ok(summary.resultRef);
  const hubResponse = await request(a.cookie, "research-hub");
  assert.equal(
    hubResponse.status,
    200,
    JSON.stringify(await hubResponse.clone().json()),
  );
  const hub = await hubResponse.json();
  assert.equal(hub.summary.open, 2);
  assert.equal(hub.coverage[0].kind, "temporary");
  assert.equal(hub.forecasts[0].status, "open");
  const forecast = await new ForecastStore(pool).detail(
    a.owner,
    published[0].forecastId,
  );
  const settled = await new ForecastStore(pool).evaluateDue(
    {
      source: "synthetic-test-only",
      version: "fixture-v1",
      feed: "fixture",
      async bars(q) {
        return [
          {
            session: q.start,
            open: "100",
            high: "101",
            low: "99",
            close: "100",
            volume: "10",
          },
          {
            session: q.end,
            open: "104",
            high: "107",
            low: "103",
            close: q.symbol === "SPY" ? "102" : "106",
            volume: "10",
          },
        ];
      },
    },
    new Date(new Date(forecast.evaluate_after).getTime() + 1000),
  );
  assert.equal(settled.evaluated.length, 1);
  const detail = await (
    await request(a.cookie, `forecasts/${published[0].forecastId}`)
  ).json();
  assert.equal(detail.evaluation.payload.actualReturn, "0.06");
  assert.equal(detail.evidence[0].payload.title, "Synthetic filing");
  const after = await (await request(a.cookie, "research-hub")).json();
  assert.equal(after.summary.byHorizon[5].samples, 1);
  assert.equal(after.summary.byHorizon[20], undefined);
  const b = await account();
  assert.equal(
    (await request(b.cookie, `forecasts/${published[0].forecastId}`)).status,
    404,
  );
});

test("coverage controls persist without deleting predictions; background threads stay out of chat", async () => {
  const a = await account();
  assert.equal(
    (
      await request(a.cookie, "coverage", "PUT", {
        symbol: "MSFT",
        action: "watch",
      })
    ).status,
    200,
  );
  const pause = await (
    await request(a.cookie, "coverage", "PUT", {
      symbol: "MSFT",
      action: "pause",
    })
  ).json();
  assert.equal(pause[0].status, "paused");
  const resume = await (
    await request(a.cookie, "coverage", "PUT", {
      symbol: "MSFT",
      action: "resume",
    })
  ).json();
  assert.equal(resume[0].status, "active");
  assert.equal(
    (
      await request(a.cookie, "agent-settings", "PUT", {
        dailyResearchLimit: 1,
        dailyCandidateLimit: 2,
      })
    ).status,
    400,
  );
  const settings = await (
    await request(a.cookie, "agent-settings", "PUT", { enabled: false })
  ).json();
  assert.equal(settings.enabled, false);
  const background = await tasks.submitSystem(
    a.owner,
    "background-test",
    { input: "synthetic background" },
    { priority: 200 },
  );
  assert.ok(background.id);
  const chats = await (await request(a.cookie, "threads")).json();
  assert.equal(chats.length, 0);
  const removed = await (
    await request(a.cookie, "coverage", "PUT", {
      symbol: "MSFT",
      action: "remove",
    })
  ).json();
  assert.equal(removed.length, 0);
});

async function predictionForRecovery() {
  const a = await account();
  const thread = await tasks.createThread(a.owner);
  await tasks.submit(a.owner, thread.id, randomUUID(), {
    input: "Receipt recovery fixture",
  });
  const root = (await tasks.claim(a.owner, worker))!.task;
  const rootCtx = await bind(root);
  const context: any = await gateway.invoke(
    rootCtx,
    "read_research_context",
    { symbol: "NVDA" },
    randomUUID(),
  );
  await gateway.invoke(
    rootCtx,
    "submit_specialist",
    {
      role: "prediction",
      requestKey: "recovery-child",
      question: "Publish one forecast",
      datasetRefs: [],
      researchContextRef: context.contextRef,
    },
    randomUUID(),
  );
  const ctx = await bind((await tasks.claim(a.owner, worker))!.task);
  const input = {
    symbol: "NVDA",
    horizon: 5,
    meanReturn: "0.05",
    lowerReturn: "-0.05",
    upperReturn: "0.15",
    upProbability: "0.65",
    researchContextRef: context.contextRef,
    evidenceRefs: [],
    datasetRefs: [],
    eventKey: "receipt-recovery-event",
    thesis: "Synthetic recovery fixture only",
  };
  return { ...a, ctx, input };
}

test("lost forecast commit acknowledgement leaves the tool receipt recoverable with the original operation key", async (t) => {
  const { DomainError } = await import("../../packages/server/src/tasks.js");
  const original = ForecastStore.prototype.publish;
  for (const failure of [
    new Error("simulated_lost_commit_acknowledgement"),
    new DomainError("storage_connection_lost", 503),
    new DomainError("tool_execution_unknown"),
  ]) {
    const a = await predictionForRecovery();
    const key = randomUUID();
    let lost = false;
    const injected = t.mock.method(
      ForecastStore.prototype,
      "publish",
      async function (
        this: ForecastStore,
        ...args: Parameters<ForecastStore["publish"]>
      ) {
        const receipt = await original.apply(this, args);
        if (!lost) {
          lost = true;
          // The immutable business result has committed; simulate losing its acknowledgement.
          throw failure;
        }
        return receipt;
      },
    );
    try {
      await assert.rejects(
        gateway.invoke(a.ctx, "publish_forecast", a.input, key),
        (error) => error === failure,
      );
      const first = (
        await pool.query(
          "SELECT c.id,c.response,f.id AS forecast_id,f.result_id FROM tool_calls c JOIN forecasts f ON f.task_id=c.task_id AND f.operation_key=c.id::text WHERE c.task_id=$1 AND c.operation_key=$2",
          [a.ctx.taskId, key],
        )
      ).rows[0];
      assert.ok(
        first.forecast_id,
        "business commit must exist before recovery",
      );
      assert.equal(
        first.response,
        null,
        "unknown commit outcome must not become a cached failure",
      );
      await pool.query("UPDATE tasks SET status='unknown' WHERE id=$1", [
        a.ctx.taskId,
      ]);
      const recovered: any = await gateway.invoke(
        a.ctx,
        "publish_forecast",
        a.input,
        key,
      );
      assert.equal(recovered.forecastId, first.forecast_id);
      assert.equal(recovered.resultRef, first.result_id);
      assert.deepEqual(
        await gateway.invoke(a.ctx, "publish_forecast", a.input, key),
        recovered,
      );
      assert.equal(
        (
          await pool.query(
            "SELECT count(*)::integer AS n FROM forecasts WHERE owner_id=$1",
            [a.owner],
          )
        ).rows[0].n,
        1,
      );
      assert.equal(
        (
          await pool.query("SELECT response FROM tool_calls WHERE id=$1", [
            first.id,
          ])
        ).rows[0].response.forecastId,
        first.forecast_id,
      );
    } finally {
      injected.mock.restore();
    }
  }
});

test("a definite forecast reference rejection remains a cached terminal tool result", async () => {
  const a = await predictionForRecovery();
  const key = randomUUID(),
    input = { ...a.input, researchContextRef: randomUUID() };
  await assert.rejects(
    gateway.invoke(a.ctx, "publish_forecast", input, key),
    /research_context_not_found/,
  );
  assert.deepEqual(
    await gateway.invoke(a.ctx, "publish_forecast", input, key),
    { error: "research_context_not_found" },
  );
  assert.equal(
    (
      await pool.query(
        "SELECT count(*)::integer AS n FROM forecasts WHERE owner_id=$1",
        [a.owner],
      )
    ).rows[0].n,
    0,
  );
});

test("confirmed local quant exit failures remain terminal rather than stranding a tool as unknown", async (t) => {
  const { DomainError } = await import("../../packages/server/src/tasks.js");
  const a = await predictionForRecovery(),
    key = randomUUID();
  t.mock.method(ForecastStore.prototype, "publish", async () => {
    throw new DomainError("quant_timeout", 503);
  });
  await assert.rejects(
    gateway.invoke(a.ctx, "publish_forecast", a.input, key),
    /quant_timeout/,
  );
  assert.deepEqual(
    await gateway.invoke(a.ctx, "publish_forecast", a.input, key),
    { error: "quant_timeout" },
  );
  assert.equal(
    (
      await pool.query(
        "SELECT count(*)::integer AS n FROM forecasts WHERE owner_id=$1",
        [a.owner],
      )
    ).rows[0].n,
    0,
  );
});

test("wrong Canvas component is correctable but unresolved publication cannot finish successfully", async () => {
  for (const correct of [false, true]) {
    const a = await predictionForRecovery();
    const published: any = await gateway.invoke(
      a.ctx,
      "publish_forecast",
      a.input,
      randomUUID(),
    );
    const rejected: any = await gateway.invoke(
      a.ctx,
      "canvas_upsert",
      {
        component: "prediction_result",
        title: "Wrong component",
        resultRef: published.resultRef,
      },
      randomUUID(),
    );
    assert.equal(rejected.status, "needs_correction");
    assert.equal(rejected.component, "forecast_result");
    assert.ok(
      (await tasks.operations(a.owner, a.ctx.taskId)).every(
        (o) => o.status === "completed",
      ),
    );
    if (correct)
      await gateway.invoke(
        a.ctx,
        "canvas_upsert",
        {
          component: rejected.component,
          title: "Corrected component",
          resultRef: published.resultRef,
        },
        randomUUID(),
      );
    const task = await tasks.get(a.owner, a.ctx.taskId);
    await tasks.recordReceipt(a.owner, task.id, worker, task.generation, {
      status: "completed",
      output: "Finished",
    });
    const operations = await tasks.operations(a.owner, task.id);
    assert.equal(
      operations.some((o) => o.status === "failed"),
      !correct,
    );
    if (correct)
      await tasks.finish(
        a.owner,
        task.id,
        worker,
        task.generation,
        "completed",
        {},
      );
    else
      await assert.rejects(
        tasks.finish(
          a.owner,
          task.id,
          worker,
          task.generation,
          "completed",
          {},
        ),
        /required_tool_failed/,
      );
  }
});
