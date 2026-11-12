import { before, after, test } from "node:test";
import assert from "node:assert/strict";
import { randomBytes, randomUUID } from "node:crypto";
import { Pool } from "pg";
import { migrate } from "../../packages/server/src/migrate.js";
import { AuthStore } from "../../packages/server/src/auth.js";
import { TaskStore } from "../../packages/server/src/tasks.js";
import { RuntimeRecovery } from "../../packages/server/src/runtime-recovery.js";
import { TaskWorker } from "../../packages/server/src/worker.js";
import {
  ProfileSupervisor,
  type ManagedProfile,
  type ProfileDriver,
  type ContainerState,
} from "../../packages/server/src/profile-supervisor.js";
import type { Runtime } from "../../packages/hermes-adapter/src/client.js";

const url = process.env.TEST_DATABASE_URL;
if (!url || !new URL(url).pathname.endsWith("_test"))
  throw new Error("Dedicated test database required");
const schema = `test_${randomUUID().replaceAll("-", "")}`;
const admin = new Pool({ connectionString: url });
const pool = new Pool({
  connectionString: url,
  options: `-c search_path=${schema}`,
});
const tasks = new TaskStore(pool);
before(async () => {
  await admin.query(`CREATE SCHEMA ${schema}`);
  await migrate(pool);
});
after(async () => {
  await pool.end();
  await admin.query(`DROP SCHEMA ${schema} CASCADE`);
  await admin.end();
});
function deferred() {
  let resolve!: () => void;
  const promise = new Promise<void>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}
class Engine implements ProfileDriver {
  state: ContainerState | undefined;
  starts = 0;
  stops = 0;
  onStart = async () => {};
  onStop = async () => {};
  onInspect = async () => {};
  async inspect() {
    await this.onInspect();
    return this.state ? { ...this.state } : undefined;
  }
  async healthy() {
    return this.state?.status === "running";
  }
  async start(_p: ManagedProfile, id?: string) {
    this.starts++;
    await this.onStart();
    this.state = {
      id: id ?? randomBytes(32).toString("hex"),
      status: "running",
      startedAt: new Date().toISOString(),
    };
  }
  async stop() {
    this.stops++;
    await this.onStop();
    this.state!.status = "stopped";
  }
}
async function setup() {
  const owner = (
    await new AuthStore(pool).register(
      `${randomUUID()}@test.invalid`,
      "long-test-password",
    )
  ).id;
  const profileId = (
    await pool.query(
      "SELECT id FROM profile_instances WHERE owner_id=$1 AND role='research'",
      [owner],
    )
  ).rows[0].id;
  const profile: ManagedProfile = {
    ownerId: owner,
    profileId,
    role: "research",
    deploymentHash: "fixture-deployment",
  };
  const engine = new Engine();
  const supervisor = new ProfileSupervisor(pool, engine, 1000);
  await supervisor.tick(profile); // enroll without starting an idle Profile
  const thread = await tasks.createThread(owner);
  let creates = 0;
  const requests: string[] = [];
  const runtime: Runtime = {
    identity: "fixture-runtime",
    retentionMs: 60_000,
    async create(request) {
      creates++;
      requests.push(request.session_id);
      return `run-${creates}`;
    },
    async get(run_id, session_id) {
      return { run_id, session_id, status: "completed", output: "done" };
    },
    async stop() {},
  };
  const worker = new TaskWorker(tasks, () => runtime);
  const submit = () =>
    tasks.submit(owner, thread.id, randomUUID(), { input: "fixture" });
  const step = async () => {
    await pool.query(
      "UPDATE tasks SET next_attempt_at=now() WHERE owner_id=$1",
      [owner],
    );
    return worker.once(owner);
  };
  const state = async () =>
    (
      await pool.query("SELECT * FROM profile_instances WHERE id=$1", [
        profileId,
      ])
    ).rows[0];
  const idle = () =>
    pool.query(
      "UPDATE profile_instances SET last_used_at=now()-interval '1 minute' WHERE id=$1",
      [profileId],
    );
  return {
    owner,
    profile,
    engine,
    supervisor,
    submit,
    step,
    state,
    idle,
    runtime,
    requests,
    creates: () => creates,
  };
}

test("Worker waits before submission; demand starts once and idle stop preserves container/session identity", async () => {
  const a = await setup();
  assert.equal(a.engine.starts, 0);
  const first = await a.submit();
  await a.step();
  assert.equal(a.creates(), 0);
  assert.equal(
    (await tasks.execution(a.owner, first.id))!.submission_started_at,
    null,
  );
  await a.supervisor.tick(a.profile);
  assert.equal((await a.state()).state, "ready");
  const container = a.engine.state!.id;
  await a.step();
  await a.step();
  assert.equal((await tasks.get(a.owner, first.id)).status, "completed");
  await a.idle();
  await a.supervisor.tick(a.profile);
  assert.equal((await a.state()).state, "stopped");
  assert.equal(a.engine.stops, 1);
  await a.submit();
  await a.supervisor.tick(a.profile);
  await a.step();
  assert.equal(a.engine.state!.id, container);
  assert.equal(a.engine.starts, 2);
  assert.equal(a.creates(), 2);
  assert.equal(a.requests[0], a.requests[1]);
});

test("exited container with an unresolved submitted execution never automatically restarts", async () => {
  const a = await setup();
  const root = await a.submit();
  await a.supervisor.tick(a.profile);
  await a.step();
  a.engine.state!.status = "stopped";
  await a.supervisor.tick(a.profile);
  assert.equal((await a.state()).state, "unknown");
  assert.equal(
    (await a.state()).lifecycle_error,
    "runtime_exited_with_active_execution",
  );
  await a.submit();
  await a.supervisor.tick(a.profile);
  assert.equal(a.engine.starts, 1);
  assert.equal(
    (await tasks.execution(a.owner, root.id))!.upstream_run_id,
    "run-1",
  );
});

test("lost create plus container exit stays unknown without retrying submission", async () => {
  const a = await setup();
  const root = await a.submit();
  await a.supervisor.tick(a.profile);
  const create = a.runtime.create.bind(a.runtime);
  a.runtime.create = async (...args) => {
    await create(...args);
    throw new Error("lost response");
  };
  await a.step();
  a.engine.state!.status = "stopped";
  await a.supervisor.tick(a.profile);
  await a.step();
  assert.equal((await tasks.get(a.owner, root.id)).status, "unknown");
  assert.equal(
    (await tasks.execution(a.owner, root.id))!.upstream_run_id,
    null,
  );
  assert.equal(a.creates(), 1);
  assert.equal(a.engine.starts, 1);
});

test("two supervisors serialize starts using a database session lock", async () => {
  const a = await setup();
  await a.submit();
  const entered = deferred(),
    release = deferred();
  a.engine.onStart = async () => {
    entered.resolve();
    await release.promise;
  };
  const pending = a.supervisor.tick(a.profile);
  await entered.promise;
  await new ProfileSupervisor(pool, a.engine, 1000).tick(a.profile);
  assert.equal(a.engine.starts, 1);
  release.resolve();
  await pending;
  assert.equal((await a.state()).state, "ready");
});

test("new demand arriving during idle stop cannot submit until stop and restart finish", async () => {
  const a = await setup();
  await a.submit();
  await a.supervisor.tick(a.profile);
  await a.step();
  await a.step();
  await a.idle();
  const entered = deferred(),
    release = deferred();
  a.engine.onStop = async () => {
    entered.resolve();
    await release.promise;
  };
  const stopping = a.supervisor.tick(a.profile);
  await entered.promise;
  const next = await a.submit();
  await a.step();
  assert.equal(a.creates(), 1);
  assert.equal(
    (await tasks.execution(a.owner, next.id))!.submission_started_at,
    null,
  );
  release.resolve();
  await stopping;
  await a.supervisor.tick(a.profile);
  await a.step();
  assert.equal(a.creates(), 2);
});

test("uncertain stop and replacement container are latched for operator reconciliation", async () => {
  const a = await setup();
  await a.submit();
  await a.supervisor.tick(a.profile);
  await a.step();
  await a.step();
  await a.idle();
  a.engine.onStop = async () => {
    throw new Error("lost stop acknowledgement");
  };
  await a.supervisor.tick(a.profile);
  await a.submit();
  await a.supervisor.tick(a.profile);
  assert.equal(
    (await a.state()).lifecycle_error,
    "container_control_unconfirmed",
  );
  assert.equal(a.engine.starts, 1);
  assert.equal(a.engine.stops, 1);
  const b = await setup();
  await b.submit();
  await b.supervisor.tick(b.profile);
  b.engine.state!.id = randomUUID();
  await b.supervisor.tick(b.profile);
  assert.equal((await b.state()).lifecycle_error, "container_identity_changed");
  assert.equal(b.engine.starts, 1);
});

test("supervisor restart never treats a persisted in-flight stop as ready", async () => {
  const a = await setup();
  await a.submit();
  await a.supervisor.tick(a.profile);
  await pool.query(
    "UPDATE profile_instances SET state='stopping' WHERE id=$1",
    [a.profile.profileId],
  );
  await new ProfileSupervisor(pool, a.engine, 1000).tick(a.profile);
  assert.equal((await a.state()).state, "unknown");
  await a.step();
  assert.equal(a.creates(), 0);
});

async function uncertainStop() {
  const a = await setup();
  await a.submit();
  await a.supervisor.tick(a.profile);
  await a.step();
  await a.step();
  await a.idle();
  a.engine.onStop = async () => {
    a.engine.state!.status = "stopped";
    throw new Error("stop succeeded but acknowledgement lost");
  };
  await a.supervisor.tick(a.profile);
  assert.equal(
    (await a.state()).lifecycle_error,
    "container_control_unconfirmed",
  );
  return a;
}

test("operator inspects without mutation and reconciles the original stopped container with immutable audit", async () => {
  const a = await uncertainStop();
  const next = await a.submit();
  const id = a.engine.state!.id;
  const before = await a.state();
  const report = (await a.supervisor.inspectRecovery(a.profile))!;
  assert.equal(report.pending, 1);
  assert.equal(report.submitted, 0);
  assert.deepEqual(report.observed, a.engine.state);
  assert.deepEqual(await a.state(), before);
  const result = (await a.supervisor.reconcileStopped(a.profile, id))!;
  assert.equal((await a.state()).state, "stopped");
  assert.equal((await a.state()).lifecycle_error, null);
  assert.equal(a.engine.starts, 1);
  assert.equal(a.engine.stops, 1);
  assert.equal((await tasks.get(a.owner, next.id)).status, "queued");
  const receipt = (
    await pool.query(
      "SELECT * FROM profile_lifecycle_audit WHERE sequence=$1",
      [result.auditSequence],
    )
  ).rows[0];
  assert.equal(receipt.previous_state, "unknown");
  assert.equal(receipt.previous_error, "container_control_unconfirmed");
  assert.equal(receipt.state, "stopped");
  assert.equal(receipt.container_id, id);
  assert.equal(receipt.owner_id, a.owner);
  assert.ok(receipt.database_actor);
  assert.deepEqual(await a.supervisor.reconcileStopped(a.profile, id), result);
  for (const sql of [
    "UPDATE profile_lifecycle_audit SET state='ready' WHERE sequence=$1",
    "DELETE FROM profile_lifecycle_audit WHERE sequence=$1",
  ])
    await assert.rejects(
      pool.query(sql, [result.auditSequence]),
      /immutable_execution_evidence/,
    );
  await assert.rejects(
    pool.query("TRUNCATE profile_lifecycle_audit"),
    /immutable_execution_evidence/,
  );
  await a.supervisor.tick(a.profile);
  await a.step();
  assert.equal(a.engine.starts, 2);
  assert.equal(a.engine.state!.id, id);
  assert.equal(a.creates(), 2);
});

test("reconciliation refuses unresolved runs including a lost create and never changes task evidence", async () => {
  const a = await setup();
  const root = await a.submit();
  await a.supervisor.tick(a.profile);
  const create = a.runtime.create.bind(a.runtime);
  a.runtime.create = async (...args) => {
    await create(...args);
    throw new Error("lost response");
  };
  await a.step();
  a.engine.state!.status = "stopped";
  await a.supervisor.tick(a.profile);
  const before = await tasks.execution(a.owner, root.id);
  await assert.rejects(
    a.supervisor.reconcileStopped(a.profile, a.engine.state!.id),
    /profile_execution_unresolved/,
  );
  assert.deepEqual(await tasks.execution(a.owner, root.id), before);
  assert.equal((await a.state()).state, "unknown");
  assert.equal(a.creates(), 1);
  assert.equal(a.engine.starts, 1);
});

test("reconciliation rejects wrong deployment, owner, container and unconfirmed exit", async () => {
  const a = await uncertainStop();
  const id = a.engine.state!.id;
  const before = await a.state();
  for (const profile of [
    { ...a.profile, ownerId: randomUUID() },
    { ...a.profile, deploymentHash: "different-deployment" },
    { ...a.profile, role: "prediction" as const },
  ])
    await assert.rejects(
      a.supervisor.reconcileStopped(profile, id),
      /profile_owner_missing|profile_deployment_mismatch/,
    );
  await assert.rejects(
    a.supervisor.reconcileStopped(a.profile, "e".repeat(64)),
    /container_identity_changed/,
  );
  a.engine.state = { id: "e".repeat(64), status: "stopped" };
  await assert.rejects(
    a.supervisor.reconcileStopped(a.profile, id),
    /container_identity_changed/,
  );
  a.engine.state = { id, status: "running" };
  await assert.rejects(
    a.supervisor.reconcileStopped(a.profile, id),
    /container_exit_unconfirmed/,
  );
  a.engine.state = undefined;
  await assert.rejects(
    a.supervisor.reconcileStopped(a.profile, id),
    /container_identity_changed/,
  );
  assert.deepEqual(await a.state(), before);
});

test("operator reconciliation excludes supervisor control but allows new queued work without early submission", async () => {
  const a = await uncertainStop();
  const entered = deferred(),
    release = deferred();
  a.engine.onInspect = async () => {
    entered.resolve();
    await release.promise;
  };
  const recovery = a.supervisor.reconcileStopped(a.profile, a.engine.state!.id);
  try {
    await entered.promise;
    await assert.rejects(
      new ProfileSupervisor(pool, a.engine).inspectRecovery(a.profile),
      /profile_control_busy/,
    );
    await new ProfileSupervisor(pool, a.engine).tick(a.profile);
    const next = await a.submit();
    await a.step();
    assert.equal(
      (await tasks.execution(a.owner, next.id))!.submission_started_at,
      null,
    );
    assert.equal(a.creates(), 1);
    assert.equal(a.engine.starts, 1);
  } finally {
    release.resolve();
  }
  await recovery;
  await a.supervisor.tick(a.profile);
  await a.step();
  assert.equal(a.creates(), 2);
});

test("unresolved tool/call evidence blocks profile recovery even when task status is terminal", async () => {
  const a = await uncertainStop();
  const task = (
    await pool.query("SELECT id FROM tasks WHERE owner_id=$1 LIMIT 1", [
      a.owner,
    ])
  ).rows[0].id;
  const operation = randomUUID();
  await pool.query(
    `INSERT INTO tool_operations(id,owner_id,task_id,operation_key,kind,request_hash,request,status)
    VALUES($1,$2,$3,'orphan-operation','market','fixture','{}','unknown')`,
    [operation, a.owner, task],
  );
  await assert.rejects(
    a.supervisor.reconcileStopped(a.profile, a.engine.state!.id),
    /profile_execution_unresolved/,
  );
  await pool.query("UPDATE tool_operations SET status='failed' WHERE id=$1", [
    operation,
  ]);
  await pool.query(
    `INSERT INTO tool_calls(id,owner_id,task_id,operation_key,tool,request_hash)
    VALUES($1,$2,$3,'orphan-call','market_history','fixture')`,
    [randomUUID(), a.owner, task],
  );
  await assert.rejects(
    a.supervisor.reconcileStopped(a.profile, a.engine.state!.id),
    /profile_execution_unresolved/,
  );
  assert.equal((await a.state()).state, "unknown");
});

test("submission captures the observed container incarnation once and restart replay cannot overwrite it", async () => {
  const a = await setup();
  const root = await a.submit();
  await a.supervisor.tick(a.profile);
  const originalStart = "2026-11-07T01:02:03.123456789Z";
  a.engine.state!.startedAt = originalStart;
  await a.supervisor.tick(a.profile);
  const create = a.runtime.create.bind(a.runtime);
  a.runtime.create = async (...args) => {
    await create(...args);
    throw new Error("lost response");
  };
  await a.step();
  const original = (await tasks.execution(a.owner, root.id))!;
  assert.equal(original.runtime_container_id, a.engine.state!.id);
  assert.equal(original.runtime_container_started_at, originalStart);
  assert.equal(original.runtime_deployment_hash, a.profile.deploymentHash);
  // Observing a later incarnation is not permission to fabricate an exit. It
  // updates the live Profile/audit only, never the original execution evidence.
  a.engine.state!.startedAt = "2026-11-07T02:03:04.987654321Z";
  await a.supervisor.tick(a.profile);
  await a.step();
  assert.equal(
    (await tasks.execution(a.owner, root.id))!.runtime_container_started_at,
    originalStart,
  );
  assert.equal((await tasks.get(a.owner, root.id)).status, "unknown");
  const report = (await a.supervisor.inspectRecovery(a.profile))!;
  assert.equal(report.container_started_at, a.engine.state!.startedAt);
  assert.equal(report.audit[0].previous_container_started_at, originalStart);
  assert.equal(report.audit[0].container_started_at, a.engine.state!.startedAt);
});

test("a ready managed Profile without a start observation cannot submit", async () => {
  const a = await setup();
  const root = await a.submit();
  await a.supervisor.tick(a.profile);
  await pool.query(
    "UPDATE profile_instances SET container_started_at=NULL WHERE id=$1",
    [a.profile.profileId],
  );
  await a.step();
  assert.equal(a.creates(), 0);
  assert.equal(
    (await tasks.execution(a.owner, root.id))!.submission_started_at,
    null,
  );
});

async function unknownRun() {
  const a = await setup();
  const root = await a.submit();
  const admission = randomUUID().replaceAll("-", "");
  let nativeStatus: "unknown" | "cancelled" = "unknown";
  let resolves = 0;
  a.runtime.get = async (run_id, session_id) => ({
    run_id,
    session_id,
    status: nativeStatus,
    admission_id: admission,
    execution_generation: 3,
  });
  a.runtime.resolveUnknown = async (_run, id, generation) => {
    assert.equal(id, admission);
    assert.equal(generation, 3);
    resolves++;
    nativeStatus = "cancelled";
  };
  await a.supervisor.tick(a.profile);
  await a.step();
  await a.step();
  assert.equal((await tasks.get(a.owner, root.id)).status, "unknown");
  const recovery = () =>
    new RuntimeRecovery(pool, a.engine).interruptUnknown(
      a.profile,
      root.id,
      a.runtime,
    );
  const record = async () =>
    (
      await pool.query("SELECT * FROM runtime_recoveries WHERE task_id=$1", [
        root.id,
      ])
    ).rows[0];
  return { ...a, root, admission, recovery, record, resolves: () => resolves };
}

test("operator interrupts only the original unknown run after verified container exit and audits every phase", async () => {
  const a = await unknownRun();
  const before = await tasks.execution(a.owner, a.root.id);
  const result = (await a.recovery())!;
  assert.equal(result.phase, "resolved");
  assert.equal(a.engine.stops, 1);
  assert.equal(a.engine.starts, 2);
  assert.equal(a.resolves(), 1);
  assert.equal(a.creates(), 1);
  assert.equal((await tasks.get(a.owner, a.root.id)).status, "unknown");
  assert.deepEqual(
    await tasks.execution(a.owner, a.root.id),
    before,
    "operator does not write task exit receipts",
  );
  const audit = await tasks.audit(a.owner, a.root.id);
  const events = audit.items.filter((e) => e.recovery_id === result.id);
  assert.deepEqual(
    events.map((e) => e.event),
    [
      "recovery.stopping",
      "recovery.stopped",
      "recovery.starting",
      "recovery.started",
      "recovery.resolving",
      "recovery.resolved",
    ],
  );
  assert.ok(
    (
      await pool.query(
        "SELECT database_actor FROM execution_audit WHERE recovery_id=$1",
        [result.id],
      )
    ).rows.every((e) => e.database_actor),
  );
  assert.deepEqual(await a.recovery(), result);
  assert.equal(a.engine.stops, 1);
  await a.step();
  assert.equal((await tasks.get(a.owner, a.root.id)).status, "interrupted");
  assert.equal((await a.state()).lifecycle_error, null);
  await assert.rejects(
    pool.query("UPDATE runtime_recoveries SET phase='stopping' WHERE id=$1", [
      result.id,
    ]),
    /immutable_recovery_identity_or_phase/,
  );
  await assert.rejects(
    pool.query(
      "UPDATE runtime_recoveries SET container_id='changed' WHERE id=$1",
      [result.id],
    ),
    /immutable_recovery_identity_or_phase/,
  );
  await assert.rejects(
    pool.query("DELETE FROM runtime_recoveries WHERE id=$1", [result.id]),
    /immutable_execution_evidence/,
  );
});

test("unknown external tool work refuses native recovery before any container action", async () => {
  const a = await unknownRun();
  await pool.query(
    `INSERT INTO tool_operations(id,owner_id,task_id,operation_key,kind,request_hash,request,status)
    VALUES($1,$2,$3,'pending','market','fixture','{}','unknown')`,
    [randomUUID(), a.owner, a.root.id],
  );
  await assert.rejects(a.recovery(), /profile_execution_unresolved/);
  assert.equal(await a.record(), undefined);
  assert.equal(a.engine.stops, 0);
  assert.equal(a.resolves(), 0);
});

test("native recovery refuses foreign/deployment/runtime identities and a live non-unknown turn", async () => {
  const a = await unknownRun();
  await assert.rejects(
    new RuntimeRecovery(pool, a.engine).interruptUnknown(
      { ...a.profile, ownerId: randomUUID() },
      a.root.id,
      a.runtime,
    ),
    /profile_owner_missing|recovery_binding_mismatch/,
  );
  await assert.rejects(
    new RuntimeRecovery(pool, a.engine).interruptUnknown(
      { ...a.profile, deploymentHash: "changed" },
      a.root.id,
      a.runtime,
    ),
    /recovery_binding_mismatch/,
  );
  a.runtime.identity = "changed";
  await assert.rejects(a.recovery(), /recovery_binding_mismatch/);
  a.runtime.identity = "fixture-runtime";
  const get = a.runtime.get;
  a.runtime.get = async (...args) => ({
    ...(await get(...args)),
    status: "running",
  });
  await assert.rejects(a.recovery(), /native_run_not_unknown/);
  assert.equal(a.engine.stops, 0);
  assert.equal(await a.record(), undefined);
});

test("lost stop acknowledgement resumes from stopped evidence without retrying stop or releasing the gate", async () => {
  const a = await unknownRun();
  a.engine.onStop = async () => {
    a.engine.state!.status = "stopped";
    throw new Error("lost stop ack");
  };
  await assert.rejects(a.recovery(), /lost stop ack/);
  assert.equal((await a.record()).phase, "stopping");
  await a.supervisor.tick(a.profile);
  assert.equal((await a.state()).lifecycle_error, "run_reconciliation_pending");
  assert.equal(a.engine.starts, 1);
  await assert.rejects(
    a.supervisor.reconcileStopped(a.profile, a.engine.state!.id),
    /run_reconciliation_pending/,
  );
  assert.equal((await a.recovery())!.phase, "resolved");
  assert.equal(a.engine.stops, 1);
  assert.equal(a.resolves(), 1);
});

test("lost start acknowledgement resumes the same incarnation without another start", async () => {
  const a = await unknownRun();
  a.engine.onStart = async () => {
    a.engine.state!.status = "running";
    a.engine.state!.startedAt = "2026-11-07T12:34:56.123456789Z";
    throw new Error("lost start ack");
  };
  await assert.rejects(a.recovery(), /lost start ack/);
  assert.equal((await a.record()).phase, "starting");
  assert.equal((await a.recovery())!.phase, "resolved");
  assert.equal(a.engine.starts, 2);
  assert.equal(a.engine.stops, 1);
});

test("lost native resolution response rechecks terminal state and Worker can settle before operator resumes", async () => {
  const a = await unknownRun();
  const resolve = a.runtime.resolveUnknown!;
  a.runtime.resolveUnknown = async (...args) => {
    await resolve(...args);
    throw new Error("lost native ack");
  };
  await assert.rejects(a.recovery(), /lost native ack/);
  assert.equal((await a.record()).phase, "resolving");
  await a.step();
  assert.equal((await tasks.get(a.owner, a.root.id)).status, "interrupted");
  const next = await a.submit();
  await a.step();
  assert.equal(
    (await tasks.execution(a.owner, next.id))!.submission_started_at,
    null,
  );
  assert.equal(a.creates(), 1);
  assert.equal((await a.recovery())!.phase, "resolved");
  assert.equal(a.resolves(), 1);
  await a.step();
  assert.equal(a.creates(), 2);
});

test("recovery excludes another operator and supervisor while new work remains queued", async () => {
  const a = await unknownRun();
  const entered = deferred(),
    release = deferred();
  a.engine.onStop = async () => {
    entered.resolve();
    await release.promise;
  };
  const recovery = a.recovery();
  try {
    await entered.promise;
    await assert.rejects(a.recovery(), /profile_control_busy/);
    await a.supervisor.tick(a.profile);
    const next = await a.submit();
    await a.step();
    assert.equal((await tasks.get(a.owner, next.id)).status, "queued");
    assert.equal(a.creates(), 1);
    assert.equal(a.resolves(), 0);
  } finally {
    release.resolve();
  }
  assert.equal((await recovery)!.phase, "resolved");
});

test("a changed native admission after restart stays paused and never resolves another turn", async () => {
  const a = await unknownRun();
  const get = a.runtime.get;
  a.runtime.get = async (...args) => ({
    ...(await get(...args)),
    admission_id: a.engine.starts > 1 ? randomUUID() : a.admission,
  });
  await assert.rejects(a.recovery(), /recovery_native_identity_changed/);
  assert.equal((await a.record()).phase, "started");
  assert.equal((await a.state()).lifecycle_error, "run_reconciliation_pending");
  assert.equal(a.resolves(), 0);
  assert.equal((await tasks.get(a.owner, a.root.id)).status, "unknown");
});

test("single-root supervisor leaves queued Profiles stopped", async () => {
  const fixture = await setup();
  const previous = process.env.RESEARCH_SINGLE_ROOT;
  try {
    process.env.RESEARCH_SINGLE_ROOT = "1";
    await fixture.submit();
    await fixture.supervisor.tick(fixture.profile);
    assert.equal(fixture.engine.starts, 0);
  } finally {
    if (previous === undefined) delete process.env.RESEARCH_SINGLE_ROOT;
    else process.env.RESEARCH_SINGLE_ROOT = previous;
  }
});
