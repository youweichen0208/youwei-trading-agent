import { before, after, test } from "node:test";
import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { readFile, mkdtemp, rm } from "node:fs/promises";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { AutoProfilePreparer } from "../../packages/hermes-adapter/src/auto-preparation.js";
import { Pool } from "pg";
import {
  provisionDatabaseAccess,
  databaseCredentials,
  services,
  type Service,
} from "../../packages/server/src/database-access.js";
import { migrate } from "../../packages/server/src/migrate.js";
import { TaskStore, type Task } from "../../packages/server/src/tasks.js";
import { AuthStore } from "../../packages/server/src/auth.js";
import { BusinessStore } from "../../packages/server/src/business.js";
import { DataService } from "../../packages/server/src/data-service.js";
import { createToolHandler } from "../../packages/server/src/tool-gateway.js";
import { ToolCapabilities } from "../../packages/server/src/tool-context.js";
import { maintain } from "../../packages/server/src/maintenance.js";
import { ProfileSupervisor } from "../../packages/server/src/profile-supervisor.js";
import { RuntimeRecovery } from "../../packages/server/src/runtime-recovery.js";
import { ResearchStore } from "../../packages/server/src/research-store.js";
import { ResearchAPI } from "../../packages/server/src/research-api.js";
import { ForecastStore } from "../../packages/server/src/forecast-store.js";
import { NotificationStore } from "../../packages/server/src/notifications.js";
import { TaskWorker } from "../../packages/server/src/worker.js";
import type { Runtime } from "../../packages/hermes-adapter/src/client.js";
import type {
  ContainerState,
  ProfileDriver,
} from "../../packages/server/src/profile-supervisor.js";

const url = process.env.TEST_DATABASE_URL;
if (!url || !new URL(url).pathname.endsWith("_test"))
  throw new Error("Dedicated TEST_DATABASE_URL ending in _test required");
const suffix = randomUUID().replaceAll("-", ""),
  schema = `test_${suffix}`;
const admin = new Pool({ connectionString: url }),
  owner = new Pool({
    connectionString: url,
    options: `-c search_path=${schema}`,
  });
const credentials = databaseCredentials(`qa_${suffix.slice(0, 20)}`);
const pools = {} as Record<Service, Pool>;
let provisioned = false;
before(async () => {
  await admin.query(`CREATE SCHEMA ${schema}`);
  await migrate(owner);
  await provisionDatabaseAccess(owner, credentials);
  provisioned = true;
  for (const service of services) {
    const connection = new URL(url!);
    connection.username = credentials[service].name;
    connection.password = credentials[service].password;
    connection.searchParams.delete("options");
    pools[service] = new Pool({ connectionString: connection.toString() });
    assert.equal(
      (await pools[service].query("SELECT current_user")).rows[0].current_user,
      credentials[service].name,
    );
  }
});
after(async () => {
  await Promise.all(Object.values(pools).map((pool) => pool.end()));
  await owner.end();
  await admin.query(`DROP SCHEMA ${schema} CASCADE`);
  if (provisioned)
    for (const service of services) {
      await admin.query(`DROP OWNED BY ${credentials[service].name}`);
      await admin.query(`DROP ROLE ${credentials[service].name}`);
    }
  await admin.end();
});
const denied = (error: unknown) =>
  (error as { code?: string }).code === "42501";

test("restricted services complete scheduled research, freeze context, publish and evaluate forecasts", async () => {
  const user = await new AuthStore(pools.web).register(
    `${randomUUID()}@forecast-access.test`,
    "test-password-long",
  );
  const store = new TaskStore(pools.worker),
    workerId = randomUUID();
  await new ResearchStore(pools.web).updateCoverage(user.id, {
    symbol: "NVDA",
    action: "watch",
  });
  const root = await store.submitSystem(
    user.id,
    "scheduled-research",
    { input: "Permission-scoped background research" },
    { priority: 600 },
  );
  assert.equal(
    (
      await store.submitSystem(
        user.id,
        "scheduled-research",
        { input: "Permission-scoped background research" },
        { priority: 600 },
      )
    ).id,
    root.id,
  );
  const task = (await store.claim(user.id, workerId))!.task;
  const researchProfile = (
    await pools.worker.query(
      "SELECT id FROM profile_instances WHERE owner_id=$1 AND role='research'",
      [user.id],
    )
  ).rows[0].id;
  await store.prepareExecution(
    user.id,
    task.id,
    workerId,
    task.generation,
    { input: "research" },
    researchProfile,
    randomUUID(),
  );
  await store.beginSubmission(
    user.id,
    task.id,
    workerId,
    task.generation,
    "test-runtime",
  );
  const research = new ResearchStore(pools.tools, {
    async collect() {
      return [
        {
          source: "web",
          status: "available",
          failures: [],
          documents: [
            {
              source: "web",
              sourceKey: "fixture-permission",
              observedAt: new Date(Date.now() - 1000).toISOString(),
              publishedAt: new Date(Date.now() - 2000).toISOString(),
              payload: { text: "Synthetic source for permission validation" },
            },
          ],
        },
      ];
    },
  });
  const evidence = await research.collect(
    { ownerId: user.id },
    { symbol: "NVDA", sources: ["web"] },
    "permission-collect",
  );
  const context = await research.context(user.id, "NVDA");
  assert.deepEqual(context.evidenceRefs, evidence.evidenceRefs);
  await store.spawn(
    user.id,
    task.id,
    workerId,
    task.generation,
    "prediction",
    "prediction",
    { researchContextRef: context.contextRef },
  );
  const child = (await store.claim(user.id, workerId))!.task;
  const profile = (
    await pools.worker.query(
      "SELECT id FROM profile_instances WHERE owner_id=$1 AND role='prediction'",
      [user.id],
    )
  ).rows[0].id;
  await store.prepareExecution(
    user.id,
    child.id,
    workerId,
    child.generation,
    { input: "forecast" },
    profile,
    randomUUID(),
  );
  await store.beginSubmission(
    user.id,
    child.id,
    workerId,
    child.generation,
    "test-runtime",
  );
  const published = await new ForecastStore(pools.tools).publish(
    { ownerId: user.id, taskId: child.id, profileId: profile },
    {
      symbol: "NVDA",
      horizon: 5,
      meanReturn: "0.05",
      lowerReturn: "-0.1",
      upperReturn: "0.2",
      upProbability: "0.65",
      researchContextRef: context.contextRef,
      evidenceRefs: context.evidenceRefs,
      datasetRefs: [],
      eventKey: "permission-fixture",
      thesis: "Synthetic test only",
    },
    "permission-forecast",
  );
  const forecast = await new ForecastStore(pools.web).detail(
    user.id,
    published.forecastId,
  );
  const evaluated = await new ForecastStore(pools.worker).evaluateDue(
    {
      source: "fixture",
      version: "v1",
      feed: "fixture",
      async bars(q) {
        return [
          {
            session: q.start,
            open: "100",
            high: "101",
            low: "99",
            close: "100",
            volume: "1",
          },
          {
            session: q.end,
            open: "105",
            high: "107",
            low: "104",
            close: q.symbol === "SPY" ? "102" : "106",
            volume: "1",
          },
        ];
      },
    },
    new Date(new Date(forecast.evaluate_after).getTime() + 1000),
  );
  assert.ok(
    evaluated.evaluated.some(
      (item) => item.forecastId === published.forecastId,
    ),
    JSON.stringify(evaluated),
  );
  const notification = await new NotificationStore(pools.worker).enqueue(
    user.id,
    "evaluation",
    published.forecastId,
    { title: "Test evaluation", text: "Synthetic settled forecast" },
  );
  await new NotificationStore(pools.web).markRead(user.id, notification.id);
  const hub = await new ResearchAPI(pools.web).hub(user.id);
  assert.equal(hub.summary.evaluated, 1);
  assert.equal(hub.notifications[0].read_at !== null, true);
  assert.deepEqual(await new BusinessStore(pools.web).threads(user.id), []);
});

test("service logins complete a three-role research with real PostgreSQL and Quant", async () => {
  const web = new TaskStore(pools.web),
    worker = new TaskStore(pools.worker),
    business = new BusinessStore(pools.web);
  const auth = new AuthStore(pools.web),
    email = `${randomUUID()}@access.test`,
    password = "test-password-long";
  const user = await auth.register(email, password),
    login = await auth.login(email, password);
  assert.equal((await auth.authenticate(login.token)).id, user.id);
  const holdings = await business.saveHoldings(user.id, {
    asOfSession: "2025-08-01",
    positions: [{ symbol: "NVDA", quantity: "2" }],
    cashUsd: "100",
  });
  await business.watchlist(user.id, "NVDA");
  const thread = await web.createThread(user.id);
  await web.submit(user.id, thread.id, randomUUID(), {
    input: "test role permissions",
    portfolioSnapshotRef: holdings.snapshotId,
  });
  const workerId = randomUUID(),
    task = (await worker.claim(user.id, workerId))!.task;
  const capabilities = new ToolCapabilities(pools.worker, "ab".repeat(32));
  async function capability(t: Task) {
    const context = await worker.executionContext(
      user.id,
      t.id,
      workerId,
      t.generation,
    );
    await worker.prepareExecution(
      user.id,
      t.id,
      workerId,
      t.generation,
      { input: "fixture" },
      context.profileId,
      context.sessionId,
    );
    const token = await capabilities.issue(
      user.id,
      t.id,
      workerId,
      t.generation,
    );
    await worker.beginSubmission(
      user.id,
      t.id,
      workerId,
      t.generation,
      "fixture-runtime",
    );
    await worker.bindUpstream(
      user.id,
      t.id,
      workerId,
      t.generation,
      `run-${t.id}`,
    );
    await worker.archiveEvents(
      user.id,
      t.id,
      workerId,
      t.generation,
      `run-${t.id}`,
      {
        events: [
          {
            sequence: 0,
            kind: "message.delta",
            text: "Role-tested partial answer",
          },
        ],
        gap: false,
        closed: true,
        limited: false,
      },
    );
    assert.equal(
      (await worker.get(user.id, t.id)).stream_text,
      t.parent_id ? "" : "Role-tested partial answer",
    );
    return token;
  }
  const token = await capability(task);
  const rows: Record<string, string>[] = JSON.parse(
    await readFile(
      new URL("../fixtures/synthetic-market.json", import.meta.url),
      "utf8",
    ),
  );
  const handler = createToolHandler(
    pools.tools,
    new DataService(pools.tools, {
      source: "synthetic-test-only",
      version: "fixture-v1",
      feed: "fixture",
      async bars(q) {
        return rows.filter(
          (row) => row.session! >= q.start && row.session! <= q.end,
        );
      },
    }),
  );
  async function call(token: string, tool: string, args: unknown) {
    const response = await handler(
      new Request("http://tools/call", {
        method: "POST",
        headers: {
          Authorization: `Bearer ${token}`,
          "Content-Type": "application/json",
          "X-Alpha-Operation-Id": randomUUID(),
        },
        body: JSON.stringify({ tool, arguments: args }),
      }),
    );
    const result = await response.json();
    assert.equal(response.status, 200, JSON.stringify(result));
    return result;
  }
  async function market(adjustment: string) {
    const op = await call(token, "market_history", {
      symbol: "NVDA",
      start: "2025-01-02",
      end: "2025-08-01",
      adjustment,
    });
    assert.equal(op.status, "completed", JSON.stringify(op));
    return op.result.datasetRef;
  }
  const split = await market("split"),
    raw = await market("raw");
  const calculated = await call(token, "quant_calculate", {
    kind: "indicators",
    datasetRef: split,
  });
  assert.equal(calculated.status, "completed", JSON.stringify(calculated));
  await call(token, "canvas_upsert", {
    component: "stock_chart",
    title: "Test chart",
    datasetRef: split,
  });
  await call(token, "canvas_upsert", {
    component: "indicator_panel",
    title: "Test indicators",
    resultRef: calculated.result.resultRef,
  });
  const summary = await call(token, "save_research_summary", {
    text: "Test-only interpretation",
    datasetRefs: [split],
    resultRefs: [calculated.result.resultRef],
  });
  await call(token, "canvas_upsert", {
    component: "research_summary",
    title: "Test summary",
    resultRef: summary.resultRef,
  });
  for (const role of ["prediction", "portfolio"] as const) {
    const child = await call(token, "submit_specialist", {
      role,
      requestKey: role,
      question: role,
      datasetRefs: [role === "portfolio" ? raw : split],
      ...(role === "portfolio"
        ? { portfolioSnapshotRef: holdings.snapshotId }
        : {}),
    });
    const leased = (await worker.claim(user.id, workerId))!.task;
    assert.equal(leased.id, child.childTaskId);
    const childToken = await capability(leased);
    const output = await call(
      childToken,
      "quant_calculate",
      role === "portfolio"
        ? {
            kind: role,
            portfolioSnapshotRef: holdings.snapshotId,
            datasetRefs: [raw],
          }
        : { kind: role, datasetRef: split },
    );
    assert.equal(output.status, "completed", JSON.stringify(output));
    await call(childToken, "canvas_upsert", {
      component:
        role === "portfolio" ? "portfolio_summary" : "prediction_result",
      title: role,
      resultRef: output.result.resultRef,
    });
    await worker.recordReceipt(
      user.id,
      leased.id,
      workerId,
      leased.generation,
      { status: "completed", output: "fixture" },
    );
    await worker.operations(user.id, leased.id);
    await worker.finish(
      user.id,
      leased.id,
      workerId,
      leased.generation,
      "completed",
      { resultRefs: [output.result.resultRef] },
    );
    assert.equal(
      (await call(token, "get_specialist_result", { childTaskId: leased.id }))
        .status,
      "completed",
    );
  }
  const workspace = await call(token, "get_workspace", {});
  assert.equal(workspace.snapshot.artifacts.length, 5);
  await business.saveLayout(
    user.id,
    workspace.id,
    workspace.version,
    workspace.snapshot.artifacts
      .map((a: { artifactId: string }) => a.artifactId)
      .reverse(),
  );
  const operations = await worker.operations(user.id, task.id);
  assert.ok(operations.every((op) => op.status === "completed"));
  await worker.recordReceipt(user.id, task.id, workerId, task.generation, {
    status: "completed",
    output: "fixture",
  });
  await worker.finish(
    user.id,
    task.id,
    workerId,
    task.generation,
    "completed",
    { text: "Permission-scoped workflow complete" },
  );
  assert.equal((await web.snapshot(user.id, task.id)).task.status, "completed");
  assert.ok(
    (await web.audit(user.id, task.id)).items.some((row) => row.receipt_id),
  );
  assert.equal((await maintain(pools.worker)).skipped, false);
  const profile = (
    await pools.worker.query(
      "SELECT id FROM profile_instances WHERE owner_id=$1 AND role='research'",
      [user.id],
    )
  ).rows[0];
  await new ProfileSupervisor(pools.worker, {
    async inspect() {
      return undefined;
    },
    async healthy() {
      return false;
    },
    async start() {
      throw new Error("idle profile must not start");
    },
    async stop() {
      throw new Error("absent profile must not stop");
    },
  }).tick({
    ownerId: user.id,
    profileId: profile.id,
    role: "research",
    deploymentHash: "role-test-deployment",
  });
  assert.equal(
    (
      await pools.worker.query(
        "SELECT state FROM profile_instances WHERE id=$1",
        [profile.id],
      )
    ).rows[0].state,
    "stopped",
  );
  const managed = {
    ownerId: user.id,
    profileId: profile.id,
    role: "research" as const,
    deploymentHash: "role-test-deployment",
  };
  const container = "a".repeat(64);
  await pools.worker.query(
    "UPDATE profile_instances SET state='unknown',lifecycle_error='container_control_unconfirmed',container_id=$2 WHERE id=$1",
    [profile.id, container],
  );
  const supervisor = new ProfileSupervisor(pools.worker, {
    async inspect() {
      return { id: container, status: "stopped" };
    },
    async healthy() {
      return false;
    },
    async start() {
      throw new Error("reconciliation must not start");
    },
    async stop() {
      throw new Error("reconciliation must not stop");
    },
  });
  const recovery = (await supervisor.reconcileStopped(managed, container))!;
  const evidence = (await supervisor.inspectRecovery(managed))!;
  assert.equal(evidence.audit[0].sequence, recovery.auditSequence);
  assert.equal(evidence.audit[0].database_actor, credentials.worker.name);
  assert.equal(
    evidence.audit[0].previous_error,
    "container_control_unconfirmed",
  );
  await assert.rejects(
    ToolCapabilities.authenticate(pools.tools, token),
    /unauthorized/,
  );
  await auth.logout(login.token);
});

test("restricted Worker performs native recovery while Web reads only owner-scoped recovery audit", async () => {
  const user = await new AuthStore(pools.web).register(
    `${randomUUID()}@recovery.test`,
    "test-password-long",
  );
  const profile = {
    ownerId: user.id,
    role: "research" as const,
    deploymentHash: "permission-recovery",
    profileId: (
      await pools.worker.query(
        "SELECT id FROM profile_instances WHERE owner_id=$1 AND role='research'",
        [user.id],
      )
    ).rows[0].id,
  };
  const state: ContainerState = {
    id: "d".repeat(64),
    status: "running",
    startedAt: "2026-11-07T01:00:00.123456789Z",
  };
  const driver: ProfileDriver = {
    async inspect() {
      return { ...state };
    },
    async healthy() {
      return state.status === "running";
    },
    async stop() {
      state.status = "stopped";
    },
    async start() {
      state.status = "running";
      state.startedAt = "2026-11-07T02:00:00.987654321Z";
    },
  };
  await new ProfileSupervisor(pools.worker, driver).tick(profile);
  const web = new TaskStore(pools.web),
    store = new TaskStore(pools.worker);
  const thread = await web.createThread(user.id);
  const task = await web.submit(user.id, thread.id, randomUUID(), {
    input: "Test unknown recovery",
  });
  let status: "unknown" | "cancelled" = "unknown";
  const admission = randomUUID().replaceAll("-", "");
  const runtime: Runtime = {
    identity: "permission-test-runtime",
    retentionMs: 60000,
    async create() {
      return "permission-test-run";
    },
    async get(run_id, session_id) {
      return {
        run_id,
        session_id,
        status,
        admission_id: admission,
        execution_generation: 2,
      };
    },
    async stop() {},
    async resolveUnknown() {
      status = "cancelled";
    },
  };
  const worker = new TaskWorker(store, () => runtime);
  await worker.once(user.id);
  await worker.once(user.id);
  assert.equal((await web.get(user.id, task.id)).status, "unknown");
  const recovery = (await new RuntimeRecovery(
    pools.worker,
    driver,
  ).interruptUnknown(profile, task.id, runtime))!;
  assert.equal(recovery.phase, "resolved");
  const entries = (await web.audit(user.id, task.id)).items.filter(
    (e) => e.recovery_id === recovery.id,
  );
  assert.equal(entries.length, 6);
  assert.ok(
    (
      await owner.query(
        "SELECT database_actor FROM execution_audit WHERE recovery_id=$1",
        [recovery.id],
      )
    ).rows.every((e) => e.database_actor === credentials.worker.name),
  );
  await pools.worker.query(
    "UPDATE tasks SET next_attempt_at=now() WHERE id=$1",
    [task.id],
  );
  await worker.once(user.id);
  assert.equal((await web.get(user.id, task.id)).status, "interrupted");
  await assert.rejects(web.audit(randomUUID(), task.id), /not_found/);
});

test("runtime roles cannot rewrite audit or facts, create schema objects, or access another service's secrets", async () => {
  for (const service of services) {
    const pool = pools[service];
    for (const sql of [
      "UPDATE users SET password_hash='changed'",
      "DELETE FROM tasks",
      "UPDATE datasets SET payload='{}'",
      "DELETE FROM results",
      "TRUNCATE task_events",
      "DELETE FROM execution_audit",
      "DELETE FROM runtime_recoveries",
      "TRUNCATE runtime_recoveries",
      "UPDATE runtime_recoveries SET container_id='forged'",
      "INSERT INTO profile_lifecycle_audit(owner_id,profile_id,state,managed,database_actor) SELECT owner_id,id,'ready',true,'forged' FROM profile_instances LIMIT 1",
      "DELETE FROM profile_lifecycle_audit",
      "UPDATE profile_lifecycle_audit SET state='ready'",
      "TRUNCATE profile_lifecycle_audit",
      "UPDATE execution_audit SET status='completed'",
      "INSERT INTO execution_audit(owner_id,task_id,event,status,generation) SELECT owner_id,id,'forged','completed',1 FROM tasks LIMIT 1",
      "UPDATE tool_exit_receipts SET status='completed'",
      "DELETE FROM tool_exit_receipts",
      `CREATE TABLE ${schema}.forbidden(id int)`,
      "SELECT * FROM migrations",
      "ALTER TABLE tasks DISABLE TRIGGER ALL",
    ])
      await assert.rejects(pool.query(sql), denied, `${service}: ${sql}`);
    const flags = (
      await owner.query(
        "SELECT rolsuper,rolcreatedb,rolcreaterole,rolreplication,rolbypassrls FROM pg_roles WHERE rolname=$1",
        [credentials[service].name],
      )
    ).rows[0];
    assert.ok(Object.values(flags).every((value) => value === false));
  }
  for (const service of ["worker", "tools"] as const) {
    await assert.rejects(
      pools[service].query("SELECT password_hash FROM users"),
      denied,
    );
    await assert.rejects(
      pools[service].query("SELECT * FROM login_sessions"),
      denied,
    );
    await assert.rejects(
      pools[service].query("DELETE FROM login_sessions"),
      denied,
    );
    await assert.rejects(
      pools[service].query(
        "INSERT INTO portfolio_snapshots(id,owner_id,portfolio_id,as_of_session,positions,cash_usd) SELECT id,owner_id,portfolio_id,as_of_session,positions,cash_usd FROM portfolio_snapshots",
      ),
      denied,
    );
  }
  await assert.rejects(pools.web.query("SELECT * FROM executions"), denied);
  await assert.rejects(
    pools.tools.query("SELECT tool_token_ciphertext FROM executions"),
    denied,
  );
  await assert.rejects(
    pools.tools.query("UPDATE tasks SET status='completed'"),
    denied,
  );
  await assert.rejects(
    pools.web.query("SELECT maintain_transient_records()"),
    denied,
  );
  await assert.rejects(
    pools.tools.query("SELECT maintain_transient_records()"),
    denied,
  );
  await assert.rejects(
    provisionDatabaseAccess(owner, credentials),
    /database_role_already_exists/,
  );
});

test("privileged functions cannot be redirected by temporary tables or caller search paths", async () => {
  const client = await pools.worker.connect();
  const original = (await owner.query("SELECT id FROM users LIMIT 1")).rows[0]
    .id;
  try {
    await client.query("BEGIN");
    await client.query("CREATE TEMP TABLE users(id uuid)");
    await client.query(
      "CREATE TEMP TABLE task_events(id uuid,created_at timestamptz)",
    );
    await client.query("CREATE TEMP TABLE execution_audit(sequence bigint)");
    await client.query(
      "CREATE TEMP TABLE profile_lifecycle_audit(sequence bigint)",
    );
    await client.query("SET LOCAL search_path=pg_temp,pg_catalog");
    await client.query(
      `UPDATE ${schema}.profile_instances SET state='unknown' WHERE owner_id=$1`,
      [original],
    );
    assert.equal(
      (
        await client.query(
          "SELECT count(*)::int AS count FROM pg_temp.profile_lifecycle_audit",
        )
      ).rows[0].count,
      0,
    );
    assert.ok(
      (
        await client.query(
          `SELECT count(*)::int AS count FROM ${schema}.profile_lifecycle_audit WHERE owner_id=$1 AND state='unknown'`,
          [original],
        )
      ).rows[0].count >= 3,
    );
    assert.equal(
      (
        await client.query(`SELECT ${schema}.lock_task_owner($1) AS locked`, [
          original,
        ])
      ).rows[0].locked,
      true,
    );
    assert.equal(
      (
        await client.query(`SELECT ${schema}.lock_task_owner($1) AS locked`, [
          randomUUID(),
        ])
      ).rows[0].locked,
      false,
    );
    assert.equal(
      (
        await client.query(
          `SELECT ${schema}.maintain_transient_records() AS result`,
        )
      ).rows[0].result.skipped,
      false,
    );
    await client.query("ROLLBACK");
  } finally {
    await client.query("ROLLBACK");
    client.release();
  }
  await owner.query("CREATE TABLE unrelated_private_table(id integer)");
  try {
    await assert.rejects(
      provisionDatabaseAccess(
        owner,
        databaseCredentials(
          `qa_${randomUUID().replaceAll("-", "").slice(0, 20)}`,
        ),
      ),
      /dedicated_owned_application_schema_required/,
    );
  } finally {
    await owner.query("DROP TABLE unrelated_private_table");
  }
});

test("restricted Worker prepares registration jobs while Web cannot read policy or change preparation bindings", async () => {
  const user = await new AuthStore(pools.web).register(
    `${randomUUID()}@access.test`,
    "test-password-long",
  );
  const root = await mkdtemp(join(tmpdir(), "alpha-access-preparation-"));
  try {
    const preparer = await AutoProfilePreparer.open(
      pools.worker,
      {
        image: `sha256:${"a".repeat(64)}`,
        gatewayOrigin: "http://host.docker.internal:3410",
        firstPort: 34000,
        models: Object.fromEntries(
          ["research", "prediction", "portfolio"].map((role) => [
            role,
            {
              name: "test-only-model",
              baseUrl: "https://model.invalid/v1",
              apiMode: "chat_completions",
              apiKey: `test-only-credential-${role}`,
            },
          ]),
        ),
      },
      root,
      34020,
    );
    assert.equal((await preparer.prepare(user.id)).status, "prepared");
    for (const table of [
      "profile_preparation_jobs",
      "profile_preparation_policy",
      "profile_preparation_policy_versions",
      "profile_api_ports",
    ]) {
      await assert.rejects(pools.web.query(`SELECT * FROM ${table}`), denied);
      await assert.rejects(pools.tools.query(`SELECT * FROM ${table}`), denied);
      await assert.rejects(pools.worker.query(`DELETE FROM ${table}`), denied);
      await assert.rejects(pools.worker.query(`TRUNCATE ${table}`), denied);
    }
    for (const [rights, expected] of [
      ["SELECT", true],
      ["INSERT", true],
      ["UPDATE", false],
      ["DELETE", false],
      ["TRUNCATE", false],
    ] as const)
      assert.equal(
        (
          await owner.query(
            "SELECT has_table_privilege($1,$2,$3) AS granted",
            [
              credentials.worker.name,
              `${schema}.profile_preparation_policy_versions`,
              rights,
            ],
          )
        ).rows[0].granted,
        expected,
        `worker ${rights}`,
      );
    await assert.rejects(
      pools.web.query(
        "INSERT INTO profile_preparation_jobs(owner_id,status) VALUES($1,'blocked')",
        [user.id],
      ),
      denied,
    );
    await assert.rejects(
      pools.web.query(
        "UPDATE profile_preparation_jobs SET status='prepared' WHERE owner_id=$1",
        [user.id],
      ),
      denied,
    );
    await assert.rejects(
      pools.worker.query(
        "UPDATE profile_preparation_jobs SET request_id=$1 WHERE owner_id=$2",
        [randomUUID(), user.id],
      ),
      denied,
    );
    await assert.rejects(
      pools.worker.query(
        "UPDATE profile_preparation_jobs SET first_port=34500 WHERE owner_id=$1",
        [user.id],
      ),
      /immutable_preparation_binding/,
    );
    await assert.rejects(
      pools.worker.query(
        "UPDATE profile_preparation_policy SET last_port=34500",
      ),
      denied,
    );
    await assert.rejects(
      pools.worker.query(
        "UPDATE profile_api_ports SET port=34500 WHERE owner_id=$1",
        [user.id],
      ),
      denied,
    );
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("restricted worker scheduler persists scans, checkpoints, deferred work and due reviews", async () => {
  const { ResearchScheduler } =
    await import("../../packages/server/src/research-scheduler.js");
  const { MarketScanner } =
    await import("../../packages/server/src/market-scanner.js");
  const { QuantRunner } =
    await import("../../packages/server/src/quant-runner.js");
  const { ExperienceStore } =
    await import("../../packages/server/src/experience-store.js");
  const user = await new AuthStore(pools.web).register(
    `${randomUUID()}@scheduler-access.test`,
    "test-password-long",
  );
  const worker = new TaskStore(pools.worker),
    workerId = randomUUID();
  const quant = new QuantRunner(),
    now = new Date();
  const schedule = await quant.run({
    operation: "research_schedule",
    asOf: now.toISOString(),
  });
  await pools.worker.query(
    "INSERT INTO research_settings(owner_id,payload) SELECT owner_id,'{\"enabled\":false}'::jsonb FROM execution_contexts WHERE kind='personal' AND owner_id<>$1 ON CONFLICT(owner_id) DO UPDATE SET payload=excluded.payload",
    [user.id],
  );
  const webResearch = new ResearchStore(pools.web);
  await webResearch.updateCoverage(user.id, {
    symbol: "NVDA",
    action: "watch",
  });
  await webResearch.settings(user.id, {
    dailyResearchLimit: 1,
    dailyCandidateLimit: 1,
    digestHour: 0,
  });
  const research = new ResearchStore(pools.worker, {
    async collect() {
      return [
        {
          source: "web",
          status: "available",
          failures: [],
          documents: [
            {
              source: "web",
              sourceKey: "scheduler-permission",
              observedAt: new Date(+now - 1000).toISOString(),
              publishedAt: new Date(+now - 2000).toISOString(),
              payload: { text: "Synthetic scheduled evidence" },
            },
          ],
        },
      ];
    },
  });
  const scanner = new MarketScanner(
    pools.worker,
    {
      feed: "fixture",
      async assets() {
        return [
          { symbol: "AAPL", name: "Synthetic equity", exchange: "NASDAQ" },
        ];
      },
      async bars(symbols) {
        return Object.fromEntries(
          symbols.map((symbol) => [
            symbol,
            schedule.recentCompletedSessions.map(
              (session: string, index: number) => ({
                session,
                close: symbol === "AAPL" ? 100 + index : 100,
                volume: 1000,
              }),
            ),
          ]),
        );
      },
      async announcements() {
        return { counts: { AAPL: 1 }, complete: true };
      },
    },
    quant,
  );
  const scheduler = new ResearchScheduler(pools.worker, {
    research,
    scanner,
    forecasts: new ForecastStore(pools.worker),
    experience: new ExperienceStore(pools.worker),
    market: {
      source: "fixture",
      version: "v1",
      feed: "fixture",
      async bars(q) {
        return [
          {
            session: q.start,
            open: "100",
            high: "101",
            low: "99",
            close: "100",
            volume: "1",
          },
        ];
      },
    },
    calendar: {
      async run() {
        return schedule;
      },
    },
  });
  await scheduler.tick(now);
  const runs = (
    await pools.worker.query(
      "SELECT kind,status,error_code FROM research_schedule_runs WHERE owner_id=$1",
      [user.id],
    )
  ).rows;
  assert.ok(
    runs.some((run) => run.kind === "close" && run.status === "completed"),
    JSON.stringify(runs),
  );
  assert.equal(
    (
      await pools.worker.query(
        "SELECT count(*)::int n FROM market_scan_runs WHERE owner_id=$1",
        [user.id],
      )
    ).rows[0].n,
    1,
  );
  assert.equal(
    (
      await pools.worker.query(
        "SELECT count(*)::int n FROM research_checks WHERE owner_id=$1",
        [user.id],
      )
    ).rows[0].n,
    1,
  );
  const jobs = (
    await pools.worker.query("SELECT * FROM research_jobs WHERE owner_id=$1", [
      user.id,
    ])
  ).rows;
  const stockJob = jobs.find((job) => job.symbol === "NVDA"),
    candidate = jobs.find((job) => job.symbol === "AAPL");
  assert.equal(stockJob.status, "running");
  assert.equal(candidate.status, "deferred");
  const task = (await worker.claim(user.id, workerId))!.task;
  assert.equal(task.id, stockJob.active_task_id);
  const context = await research.context(user.id, "NVDA"),
    resultId = randomUUID();
  await pools.worker.query(
    "INSERT INTO results(id,owner_id,task_id,kind,payload,lineage) VALUES($1,$2,$3,'research_summary','{\"text\":\"Collected\"}',$4)",
    [
      resultId,
      user.id,
      task.id,
      JSON.stringify({ researchContextRef: context.contextRef }),
    ],
  );
  await worker.finish(
    user.id,
    task.id,
    workerId,
    task.generation,
    "completed",
    {},
  );
  await scheduler.advanceJobs(now);
  const checkpoint = (
    await pools.worker.query("SELECT * FROM research_jobs WHERE id=$1", [
      stockJob.id,
    ])
  ).rows[0];
  assert.equal(checkpoint.stage, "analyze");
  assert.equal(checkpoint.thread_id, stockJob.thread_id);
  assert.ok(checkpoint.checkpoint.resultRefs.includes(resultId));
  assert.equal(checkpoint.checkpoint.researchContextRef, context.contextRef);
  const forecast = randomUUID(),
    forecastResult = randomUUID(),
    evaluationResult = randomUUID();
  for (const id of [forecastResult, evaluationResult])
    await pools.worker.query(
      "INSERT INTO results(id,owner_id,task_id,kind,payload,lineage) VALUES($1,$2,$3,'forecast','{}','{}')",
      [id, user.id, task.id],
    );
  await pools.worker.query(
    "INSERT INTO forecasts(id,owner_id,task_id,result_id,operation_key,request_hash,symbol,horizon,event_key,research_context_id,published_at,start_session,target_session,start_open_at,target_close_at,evaluate_after,payload) VALUES($1,$2,$3,$4,'scheduled-permission','fixture','NVDA',5,'fixture',$5,now(),'2025-01-02','2025-01-08','2025-01-02T14:30:00Z','2025-01-08T21:00:00Z','2025-01-08T21:20:00Z','{}')",
    [forecast, user.id, task.id, forecastResult, context.contextRef],
  );
  await pools.worker.query(
    "INSERT INTO forecast_evaluations(id,forecast_id,owner_id,result_id,payload) VALUES($1,$2,$3,$4,'{}')",
    [randomUUID(), forecast, user.id, evaluationResult],
  );
  await scheduler.tick(new Date(+now + 60000));
  const review = (
    await pools.worker.query(
      "SELECT * FROM research_jobs WHERE owner_id=$1 AND tier='review'",
      [user.id],
    )
  ).rows[0];
  assert.equal(review.checkpoint.forecastId, forecast);
  assert.equal(review.checkpoint.evaluationRef, evaluationResult);
  const nextSession = new Date(
    Date.parse(schedule.latestCompletedSession) + 86400000,
  )
    .toISOString()
    .slice(0, 10);
  await scheduler.promoteDeferred(
    user.id,
    nextSession,
    { dailyResearchLimit: 1 },
    new Date(+now + 86400000),
  );
  assert.equal(
    (
      await pools.worker.query("SELECT status FROM research_jobs WHERE id=$1", [
        candidate.id,
      ])
    ).rows[0].status,
    "pending",
  );
  assert.ok(
    (await new NotificationStore(pools.web).list(user.id)).some(
      (notice) => notice.kind === "daily_digest",
    ),
  );
});

test("web role can soft-delete its own threads", async () => {
  const auth = new AuthStore(pools.web);
  const user = await auth.register(
    `${randomUUID()}@access.test`,
    "test-password-long",
  );
  const tasks = new TaskStore(pools.web);
  const thread = await tasks.createThread(user.id);
  const business = new BusinessStore(pools.web);
  assert.deepEqual(await business.deleteThread(user.id, thread.id), {
    id: thread.id,
    deleted: true,
  });
  assert.deepEqual(await business.threads(user.id), []);
  await assert.rejects(business.thread(user.id, thread.id), /not_found/);
});
