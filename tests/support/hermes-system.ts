// Opt-in process test peer. Never imported by production or the default suite.
// Only model decisions and market bars are fixtures; task/data/tool code is real.
import assert from "node:assert/strict";
import { randomBytes, randomUUID } from "node:crypto";
import { readFile } from "node:fs/promises";
import { createServer } from "node:http";
import { createInterface } from "node:readline";
import { Readable } from "node:stream";
import { setTimeout as delay } from "node:timers/promises";
import { Pool } from "pg";
import { AuthStore } from "../../packages/server/src/auth.js";
import { BusinessStore } from "../../packages/server/src/business.js";
import { DataService } from "../../packages/server/src/data-service.js";
import { QuantRunner } from "../../packages/server/src/quant-runner.js";
import { migrate } from "../../packages/server/src/migrate.js";
import { TaskStore } from "../../packages/server/src/tasks.js";
import { createToolHandler } from "../../packages/server/src/tool-gateway.js";
import { workerHarness, bounded, deferred } from "./hermes-worker-harness.js";
import { prepareProfiles } from "../../packages/hermes-adapter/src/provision.js";

const url = process.env.TEST_DATABASE_URL;
if (!url || !new URL(url).pathname.endsWith("_test"))
  throw new Error("Dedicated TEST_DATABASE_URL ending in _test required");
const schema = `test_${randomUUID().replaceAll("-", "")}`;
const admin = new Pool({ connectionString: url });
const pool = new Pool({
  connectionString: url,
  options: `-c search_path=${schema}`,
});
const input = createInterface({ input: process.stdin });
const lines = input[Symbol.asyncIterator]();
let stopping = false;
process.once("SIGTERM", () => {
  stopping = true;
  input.close();
});
const store = new TaskStore(pool);
const business = new BusinessStore(pool);
const observed: { status: number; tool: string }[] = [];
const bars = JSON.parse(
  await readFile(
    new URL("../fixtures/synthetic-market.json", import.meta.url),
    "utf8",
  ),
);
const yearBars = JSON.parse(
  await readFile(
    new URL("../fixtures/synthetic-market-year.json", import.meta.url),
    "utf8",
  ),
);
let marketCalls = 0;
let blockAt: "provider" | "validated" | undefined;
let providerEntered = deferred<void>(),
  providerRelease = deferred<void>();
class DelayedValidation extends QuantRunner {
  override async run(input: Record<string, unknown>, signal?: AbortSignal) {
    const result = await super.run(input, signal);
    if (blockAt === "validated" && input.operation === "validate_market") {
      providerEntered.resolve();
      await providerRelease.promise;
    }
    return result;
  }
}
let harness: Awaited<ReturnType<typeof workerHarness>> | undefined;
const handler = createToolHandler(
  pool,
  new DataService(
    pool,
    {
      source: "synthetic-test-only",
      version: "fixture-v1",
      feed: "fixture",
      async bars(q) {
        marketCalls++;
        if (blockAt === "provider") {
          providerEntered.resolve();
          // Controlled provider deliberately finishes after cancellation, testing
          // that an abort/stop acknowledgement cannot invent an exit receipt.
          await providerRelease.promise;
        }
        return (q.start < bars[0].session ? yearBars : bars).filter(
          (r: { session: string }) =>
            r.session >= q.start && r.session <= q.end,
        );
      },
    },
    new DelayedValidation(),
  ),
);
const server = createServer(async (req, res) => {
  try {
    const headers = new Headers();
    for (const [key, value] of Object.entries(req.headers))
      if (value !== undefined)
        headers.set(key, Array.isArray(value) ? value.join(",") : value);
    const request = new Request(`http://tools${req.url}`, {
      method: req.method,
      headers,
      ...(req.method === "POST"
        ? { body: Readable.toWeb(req), duplex: "half" }
        : {}),
    } as RequestInit);
    const body = req.method === "POST" ? await request.clone().json() : {};
    const response = await handler(request);
    observed.push({ status: response.status, tool: body.tool ?? "list" });
    res.writeHead(response.status, Object.fromEntries(response.headers));
    res.end(Buffer.from(await response.arrayBuffer()));
  } catch {
    res.writeHead(503).end();
  }
});

let created = false;
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
  console.log(
    JSON.stringify({
      owner,
      profiles,
      toolOrigin: `http://127.0.0.1:${address.port}`,
    }),
  );
  let message = await lines.next();
  if (message.done) throw new Error("Runtime configuration not received");
  let configuration = JSON.parse(message.value);
  if (configuration.action === "prepare-containers") {
    const modelOrigin = new URL(configuration.modelOrigin);
    assert.equal(
      modelOrigin.hostname,
      process.env.ALPHA_TEST_BIND_HOST ?? "127.0.0.1",
    );
    modelOrigin.hostname = "host.docker.internal";
    const prepared = await prepareProfiles(
      pool,
      owner,
      {
        image: configuration.image,
        firstPort: configuration.firstPort,
        gatewayOrigin: `http://host.docker.internal:${address.port}`,
        models: Object.fromEntries(
          ["research", "prediction", "portfolio"].map((role) => [
            role,
            {
              name: "alpha-fixture",
              baseUrl: `${modelOrigin.origin}/${role}/v1`,
              apiMode: "chat_completions",
              apiKey: "test-only-model-key-not-a-provider-secret",
            },
          ]),
        ),
      },
      configuration.outputRoot,
    );
    console.log(JSON.stringify({ prepared }));
    message = await lines.next();
    if (message.done)
      throw new Error("Container runtime bindings not received");
    configuration = JSON.parse(message.value);
  }
  const bindings: {
    profileId: string;
    origin: string;
    token: string;
    image?: string;
  }[] = configuration;
  assert.equal(bindings.length, 3);
  harness = await workerHarness(
    bindings,
    profiles.find((p) => p.role === "research").id,
    schema,
    randomBytes(32).toString("hex"),
  );
  const thread = await store.createThread(owner);
  const root = await store.submit(owner, thread.id, randomUUID(), {
    input:
      "Analyze NVDA from 2025-01-02 to 2025-08-01, predict, and value the selected simulated portfolio.",
    portfolioSnapshotRef: holdings.snapshotId,
  });
  const first = harness.start();
  const acceptedRun = await bounded(
    harness.accepted,
    30000,
    "first create was not accepted",
  );
  const crashed = await store.get(owner, root.id);
  const prepared = (await store.execution(owner, root.id))!;
  assert.ok(prepared.submission_started_at);
  assert.equal(prepared.upstream_run_id, null);
  assert.ok(crashed.lease_until && crashed.lease_until.getTime() > Date.now());
  assert.ok(first.child.kill("SIGKILL"));
  assert.equal(
    (await bounded(first.exit, 5000, "Worker did not exit after SIGKILL"))
      .signal,
    "SIGKILL",
  );
  const replacement = harness.start();
  assert.notEqual(first.child.pid, replacement.child.pid);
  await delay(1000);
  assert.equal(
    harness.creates.filter((c) => c.runId === acceptedRun).length,
    1,
    "replacement cannot replay before live lease expiry",
  );
  // Keep the real 30-second lease: no test UPDATE shortens its deadline.
  const until = Date.now() + 180_000;
  while (!stopping && Date.now() < until) {
    assert.equal(replacement.child.exitCode, null, replacement.output());
    assert.equal(replacement.child.signalCode, null, replacement.output());
    const current = await store.get(owner, root.id);
    if (
      ["completed", "failed", "cancelled", "interrupted"].includes(
        current.status,
      )
    )
      break;
    await delay(50);
  }
  const result = await store.snapshot(owner, root.id);
  assert.equal(result.task.status, "completed", JSON.stringify(result));
  assert.equal(
    result.task.stream_text.trim(),
    "Three roles and five components completed.",
  );
  assert.ok(result.task.generation > crashed.generation);
  const creates = harness.creates.length;
  const runIds = new Set(harness.creates.map((c) => c.runId));
  assert.equal(creates, 4, "root replay plus one create per specialist");
  assert.equal(runIds.size, 3, "replay must retain the same native run");
  const rootCreates = harness.creates.filter((c) => c.runId === acceptedRun);
  assert.equal(rootCreates.length, 2);
  assert.deepEqual(
    rootCreates[0],
    rootCreates[1],
    "replay preserves request, session, key and capability",
  );
  assert.equal(rootCreates[0]!.key, prepared.idempotency_key);
  assert.equal(marketCalls, 2, "no duplicate market side effects");
  const children = await store.children(owner, root.id);
  assert.deepEqual(children.map((t) => t.role).sort(), [
    "portfolio",
    "prediction",
  ]);
  assert.ok(children.every((t) => t.status === "completed"));
  assert.ok(children.every((t) => t.stream_text === ""));
  assert.deepEqual(
    result.artifacts.map((a: any) => a.component).sort(),
    [
      "stock_chart",
      "indicator_panel",
      "research_summary",
      "prediction_result",
      "portfolio_summary",
    ].sort(),
  );
  const executions = (await pool.query("SELECT * FROM executions")).rows;
  assert.equal(executions.length, 3);
  assert.equal(new Set(executions.map((e) => e.session_id)).size, 3);
  assert.equal(new Set(executions.map((e) => e.profile_id)).size, 3);
  assert.ok(
    executions.every((e) => Number(e.event_cursor) >= 0 && e.event_closed),
  );
  assert.ok(executions.every((e) => !e.event_gap));
  const archivedEvents = (
    await pool.query("SELECT event,data FROM task_events WHERE task_id=$1", [
      root.id,
    ])
  ).rows;
  assert.ok(archivedEvents.some((e) => e.data.stage === "tool.started"));
  assert.ok(archivedEvents.some((e) => e.data.stage === "tool.completed"));
  assert.ok(archivedEvents.some((e) => e.event === "assistant.delta"));
  assert.ok(!JSON.stringify(archivedEvents).includes('"preview"'));
  assert.ok(
    executions.every(
      (e) =>
        e.terminal_receipt?.status === "completed" &&
        e.tool_token_hash === null &&
        e.tool_token_ciphertext === null,
    ),
  );
  const operations = (await pool.query("SELECT status FROM tool_operations"))
    .rows;
  assert.equal(operations.length, 5); // two datasets plus indicators/prediction/portfolio
  assert.ok(operations.every((op) => op.status === "completed"));
  assert.ok(
    observed.length >= 17 && observed.every((call) => call.status === 200),
    JSON.stringify(observed),
  );
  const stranger = (
    await new AuthStore(pool).register(
      `${randomUUID()}@test.invalid`,
      "test-password-long",
    )
  ).id;
  await assert.rejects(store.snapshot(stranger, root.id), /not_found/);
  for (const artifact of result.artifacts) {
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
  const audit = await store.audit(owner, root.id);
  assert.ok(audit.items.length > 0);
  console.log(JSON.stringify({ action: "restart-research" }));
  const restartLine = await bounded(
    lines.next(),
    45000,
    "research restart acknowledgement missing",
  );
  assert.equal(restartLine.done, false);
  const restart = JSON.parse(restartLine.value!);
  assert.equal(restart.restarted, true);
  assert.notEqual(restart.previousPid, restart.pid);
  // Follow-up is intentionally underspecified: the real native session must
  // retain the confirmed NVDA question and answer, without sending them again.
  const followup = await store.submit(owner, thread.id, randomUUID(), {
    input: "把它改成一年",
  });
  const followupUntil = Date.now() + 60000;
  while (Date.now() < followupUntil) {
    if (
      ["completed", "failed", "cancelled", "interrupted"].includes(
        (await store.get(owner, followup.id)).status,
      )
    )
      break;
    await delay(50);
  }
  const followupResult = await store.snapshot(owner, followup.id);
  assert.equal(
    followupResult.task.status,
    "completed",
    JSON.stringify(followupResult),
  );
  assert.equal(
    followupResult.task.stream_text.trim(),
    "NVDA one-year follow-up completed.",
  );
  const followupExecution = (await store.execution(owner, followup.id))!;
  assert.equal(followupExecution.session_id, prepared.session_id);
  assert.equal(followupExecution.profile_id, prepared.profile_id);
  assert.notEqual(followupExecution.upstream_run_id, acceptedRun);
  assert.equal(
    JSON.parse((followupExecution.request as { input: string }).input).input,
    "把它改成一年",
  );
  assert.deepEqual(
    followupResult.artifacts.map((a: any) => a.component).sort(),
    ["indicator_panel", "stock_chart"],
  );
  const yearChart = followupResult.artifacts.find(
    (a: any) => a.component === "stock_chart",
  )!;
  const yearDataset = await business.resource(
    owner,
    "datasets",
    yearChart.datasetRef,
  );
  assert.equal(yearDataset.metadata.symbol, "NVDA");
  assert.equal(yearDataset.metadata.displayRange.start, "2024-08-01");
  assert.equal(yearDataset.metadata.displayRange.end, "2025-08-01");
  assert.equal(marketCalls, 3);
  assert.equal(harness.creates.length, 5);
  // The new revision must not destroy the previous research artifacts.
  assert.deepEqual(
    (await store.snapshot(owner, root.id)).artifacts,
    result.artifacts,
  );
  await assert.rejects(
    business.resource(stranger, "datasets", yearChart.datasetRef),
    /not_found/,
  );
  // Exercise both unvalidated provider data and already validated late facts.
  for (const stage of ["provider", "validated"] as const) {
    blockAt = stage;
    providerEntered = deferred<void>();
    providerRelease = deferred<void>();
    // A separate thread avoids reusing previous model history for cancellation.
    const cancelThread = await store.createThread(owner);
    const cancelled = await store.submit(owner, cancelThread.id, randomUUID(), {
      input:
        "Analyze NVDA from 2025-01-02 to 2025-08-01, then predict and value the selected portfolio.",
      portfolioSnapshotRef: holdings.snapshotId,
    });
    await bounded(
      providerEntered.promise,
      30000,
      "cancellation provider was not entered",
    );
    const bindUntil = Date.now() + 5000;
    while (
      !(await store.execution(owner, cancelled.id))?.upstream_run_id &&
      Date.now() < bindUntil
    )
      await delay(25);
    const cancelExecution = (await store.execution(owner, cancelled.id))!;
    assert.ok(cancelExecution.upstream_run_id);
    const following = await store.submit(owner, cancelThread.id, randomUUID(), {
      input: "Queued after cancellation",
    });
    await store.stop(owner, cancelled.id);
    const stopUntil = Date.now() + 20000;
    while (
      !harness.stops.some((path) =>
        path.includes(cancelExecution.upstream_run_id!),
      ) &&
      Date.now() < stopUntil
    )
      await delay(50);
    assert.ok(
      harness.stops.some((path) =>
        path.includes(cancelExecution.upstream_run_id!),
      ),
      "native stop acknowledgement observed",
    );
    await delay(1000);
    assert.ok(
      ["stopping", "unknown"].includes(
        (await store.get(owner, cancelled.id)).status,
      ),
    );
    assert.equal((await store.get(owner, following.id)).status, "queued");
    assert.equal(await store.execution(owner, following.id), undefined);
    const pendingTools = (
      await pool.query(
        "SELECT id,status FROM tool_operations WHERE task_id=$1",
        [cancelled.id],
      )
    ).rows;
    assert.equal(pendingTools.length, 1);
    assert.ok(
      ["running", "stopping", "unknown"].includes(pendingTools[0].status),
    );
    assert.equal(
      (
        await pool.query("SELECT 1 FROM tool_exit_receipts WHERE task_id=$1", [
          cancelled.id,
        ])
      ).rowCount,
      0,
    );
    // Remove only this test's queued request before releasing the original tool.
    await store.stop(owner, following.id);
    providerRelease.resolve();
    const cancelUntil = Date.now() + 45000;
    while (
      (await store.get(owner, cancelled.id)).status !== "cancelled" &&
      Date.now() < cancelUntil
    )
      await delay(50);
    assert.equal((await store.get(owner, cancelled.id)).status, "cancelled");
    const settled = (await store.execution(owner, cancelled.id))!;
    assert.ok(settled.terminal_receipt);
    assert.equal(
      (
        await pool.query(
          "SELECT tool_token_hash FROM executions WHERE task_id=$1",
          [cancelled.id],
        )
      ).rows[0].tool_token_hash,
      null,
    );
    const exitReceipts = (
      await pool.query("SELECT * FROM tool_exit_receipts WHERE task_id=$1", [
        cancelled.id,
      ])
    ).rows;
    assert.equal(exitReceipts.length, 1);
    const partial = (
      await pool.query("SELECT * FROM datasets WHERE operation_id=$1", [
        pendingTools[0].id,
      ])
    ).rows;
    if (stage === "validated") {
      assert.equal(partial.length, 1);
      assert.equal(exitReceipts[0].result.partial, true);
      assert.equal(exitReceipts[0].result.datasetRef, partial[0].id);
    } else {
      assert.equal(
        partial.length,
        0,
        "unvalidated cancelled data must not become a fact",
      );
      assert.equal(exitReceipts[0].result, null);
    }
    assert.equal(exitReceipts[0].status, "cancelled");
  }
  assert.equal(
    harness.creates.length,
    7,
    "queued-after-cancel tasks never submitted",
  );
  assert.equal(marketCalls, 5);
  assert.deepEqual(harness.errors, []);
  replacement.child.kill("SIGTERM");
  assert.deepEqual(
    await bounded(
      replacement.exit,
      12000,
      "replacement Worker shutdown timeout",
    ),
    { code: 0, signal: null },
  );
  console.log(
    JSON.stringify({
      passed: true,
      workerCrashRecovered: true,
      cancellationWaitedForTool: true,
      followupPreservedSession: true,
      followupAfterHermesRestart: true,
      researchCreates: creates,
      totalCreates: harness.creates.length,
      cancellationCases: 2,
      runs: runIds.size,
      marketCalls,
      artifacts: result.artifacts.length,
      children: children.length,
      toolRequests: observed.length,
    }),
  );
} finally {
  providerRelease.resolve();
  await harness?.close();
  input.close();
  server.closeAllConnections();
  await new Promise<void>((resolve) => server.close(() => resolve()));
  await pool.end();
  if (created) await admin.query(`DROP SCHEMA ${schema} CASCADE`);
  await admin.end();
}
