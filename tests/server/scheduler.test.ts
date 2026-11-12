import { after, before, test } from "node:test";
import assert from "node:assert/strict";
import { randomUUID, createHash } from "node:crypto";
import { Pool } from "pg";
import { migrate } from "../../packages/server/src/migrate.js";
import { TaskStore } from "../../packages/server/src/tasks.js";
import { ResearchScheduler } from "../../packages/server/src/research-scheduler.js";
import { ExperienceStore } from "../../packages/server/src/experience-store.js";

const url = process.env.TEST_DATABASE_URL;
if (!url || !new URL(url).pathname.endsWith("_test"))
  throw Error("Dedicated test database required");
const schema = `test_${randomUUID().replaceAll("-", "")}`;
const admin = new Pool({ connectionString: url }),
  pool = new Pool({
    connectionString: url,
    options: `-c search_path=${schema}`,
  });
const store = new TaskStore(pool),
  worker = randomUUID();
before(async () => {
  await admin.query(`CREATE SCHEMA ${schema}`);
  await migrate(pool);
});
after(async () => {
  await pool.end();
  await admin.query(`DROP SCHEMA ${schema} CASCADE`);
  await admin.end();
});
async function user() {
  const owner = randomUUID();
  await pool.query(
    "INSERT INTO users(id,email,password_hash) VALUES($1,$2,$3)",
    [owner, `${owner}@test.invalid`, "fixture"],
  );
  return owner;
}
function scheduler() {
  return new ResearchScheduler(pool, {
    research: {
      syncCoverage: async () => {},
      listCoverage: async () => [],
      collect: async () => ({ evidenceRefs: [] }),
      context: async () => ({ contextRef: randomUUID() }),
    },
    scanner: { scan: async () => ({ selected: [] }) },
    forecasts: { evaluateDue: async () => ({}) },
    calendar: {
      run: async () => ({
        session: "2026-11-12",
        marketOpen: "2026-11-12T14:30:00Z",
        marketClose: "2026-11-12T21:00:00Z",
        latestCompletedSession: "2026-11-11",
        temporaryExpirySession: "2026-12-10",
      }),
    },
  });
}
async function finish(owner: string) {
  const taken = (await store.claim(owner, worker))!.task;
  await store.finish(
    owner,
    taken.id,
    worker,
    taken.generation,
    "completed",
    {},
  );
  return taken;
}
async function savedForecast(
  owner: string,
  taskId: string,
  horizon = 5,
  supersedes?: string,
) {
  const id = randomUUID(),
    result = randomUUID(),
    payload = {
      meanReturn: supersedes ? "-0.03" : "0.05",
      upProbability: supersedes ? "0.3" : "0.65",
      lowerReturn: "-0.1",
      upperReturn: "0.1",
      thesis: supersedes
        ? "New official guidance invalidates prior thesis"
        : "Original thesis",
    };
  await pool.query(
    "INSERT INTO results(id,owner_id,task_id,kind,payload,lineage) VALUES($1,$2,$3,'forecast',$4,'{}')",
    [result, owner, taskId, JSON.stringify(payload)],
  );
  await pool.query(
    `INSERT INTO forecasts(id,owner_id,task_id,result_id,operation_key,request_hash,symbol,horizon,event_key,research_context_id,published_at,start_session,target_session,start_open_at,target_close_at,evaluate_after,payload,supersedes_id)
    VALUES($1,$2,$3,$4,$1::uuid::text,'fixture','NVDA',$5,$1::uuid::text,$6,now(),'2090-01-01','2090-02-01','2090-01-01T14:30:00Z','2090-02-01T21:00:00Z','2090-02-01T21:20:00Z',$7,$8)`,
    [
      id,
      owner,
      taskId,
      result,
      horizon,
      randomUUID(),
      JSON.stringify(payload),
      supersedes ?? null,
    ],
  );
  return id;
}

test("interactive roots overtake queued background, retain thread order, and system work never changes user Canvas", async () => {
  const owner = await user(),
    thread = await store.createThread(owner);
  await pool.query("UPDATE workspaces SET snapshot=$2 WHERE thread_id=$1", [
    thread.id,
    JSON.stringify({ artifacts: [{ title: "keep" }] }),
  ]);
  const bg = await store.submitSystem(
    owner,
    "background",
    { input: "background NVDA" },
    { priority: 200 },
  );
  assert.equal(
    (
      await pool.query(
        "SELECT count(*)::int n FROM messages WHERE task_id=$1",
        [bg.id],
      )
    ).rows[0].n,
    0,
  );
  assert.deepEqual(
    (
      await pool.query("SELECT snapshot FROM workspaces WHERE thread_id=$1", [
        thread.id,
      ])
    ).rows[0].snapshot,
    { artifacts: [{ title: "keep" }] },
  );
  const first = await store.submit(owner, thread.id, "user-1", {
      input: "first",
    }),
    second = await store.submit(owner, thread.id, "user-2", {
      input: "second",
    });
  const task = (await store.claim(owner, worker))!.task;
  assert.equal(task.id, first.id);
  assert.ok(task.deadline.getTime() - Date.now() > 590000);
  assert.equal(await store.take(owner, randomUUID()), null);
  await store.finish(owner, task.id, worker, task.generation, "completed", {});
  assert.equal((await finish(owner)).id, second.id);
  const last = (await store.claim(owner, worker))!.task;
  assert.equal(last.id, bg.id);
  assert.ok(last.deadline.getTime() - Date.now() < 121000);
  await store.finish(owner, last.id, worker, last.generation, "completed", {
    text: "background result",
  });
  assert.equal(
    (
      await pool.query(
        "SELECT count(*)::int n FROM messages WHERE task_id=$1",
        [bg.id],
      )
    ).rows[0].n,
    0,
  );
});

test("system admission is idempotent and does not consume the ten user slots", async () => {
  const owner = await user();
  const bg = await store.submitSystem(
    owner,
    "repeat",
    { input: "scan" },
    { priority: 100 },
  );
  assert.equal(
    (
      await store.submitSystem(
        owner,
        "repeat",
        { input: "scan" },
        { priority: 100 },
      )
    ).id,
    bg.id,
  );
  await assert.rejects(
    store.submitSystem(
      owner,
      "repeat",
      { input: "changed" },
      { priority: 100 },
    ),
    /idempotency_conflict/,
  );
  const thread = await store.createThread(owner);
  for (let i = 0; i < 10; i++)
    await store.submit(owner, thread.id, `user-${i}`, { input: `q ${i}` });
  await assert.rejects(
    store.submit(owner, thread.id, "overflow", { input: "11" }),
    /queue_full/,
  );
  await assert.rejects(
    store.submitSystem(
      owner,
      "foreign-thread",
      { input: "background" },
      { threadId: thread.id, priority: 100 },
    ),
    /background_thread_required/,
  );
});

test("daily background budget counts distinct stocks, new candidate quota does not block maintenance", async () => {
  const owner = await user(),
    s = scheduler(),
    session = "2026-11-11";
  const opts = { dailyResearchLimit: 2, dailyCandidateLimit: 1 };
  assert.ok(
    await s.enqueueJob(
      owner,
      "AAA",
      "candidate",
      session,
      "new-a",
      { selection: { symbol: "AAA" } },
      opts,
    ),
  );
  assert.ok(
    await s.enqueueJob(owner, "AAA", "candidate", session, "check-a", {}, opts),
  );
  assert.equal(
    await s.enqueueJob(
      owner,
      "BBB",
      "candidate",
      session,
      "new-b",
      { selection: { symbol: "BBB" } },
      opts,
    ),
    null,
  );
  assert.ok(
    await s.enqueueJob(owner, "BBB", "holding", session, "holding-b", {}, opts),
  );
  assert.equal(
    (
      await s.enqueueJob(
        owner,
        "CCC",
        "holding",
        session,
        "holding-c",
        {},
        opts,
      )
    ).status,
    "deferred",
  );
  assert.ok(
    await s.enqueueJob(owner, "CCC", "review", session, "review-c", {}, opts),
  );
});

test("deferred stock work survives new evidence and is admitted next session within budget", async () => {
  const owner = await user(),
    s = scheduler(),
    opts = { dailyResearchLimit: 1 };
  const first = await s.enqueueJob(
    owner,
    "AAA",
    "holding",
    "2026-11-11",
    "first",
    {},
    opts,
  );
  await pool.query("UPDATE research_jobs SET status='completed' WHERE id=$1", [
    first.id,
  ]);
  const waiting = await s.enqueueJob(
    owner,
    "BBB",
    "watchlist",
    "2026-11-11",
    "event-1",
    { researchContextRef: "old" },
    opts,
  );
  assert.equal(waiting.status, "deferred");
  const refreshed = await s.enqueueJob(
    owner,
    "BBB",
    "watchlist",
    "2026-11-12",
    "event-2",
    { researchContextRef: "new" },
    opts,
  );
  assert.equal(refreshed.id, waiting.id);
  assert.equal(refreshed.checkpoint.researchContextRef, "new");
  await s.promoteDeferred(owner, "2026-11-11", opts);
  assert.equal(
    (
      await pool.query("SELECT status FROM research_jobs WHERE id=$1", [
        waiting.id,
      ])
    ).rows[0].status,
    "deferred",
  );
  await s.promoteDeferred(owner, "2026-11-12", opts);
  const promoted = (
    await pool.query(
      "SELECT status,session::text FROM research_jobs WHERE id=$1",
      [waiting.id],
    )
  ).rows[0];
  assert.deepEqual(promoted, { status: "pending", session: "2026-11-12" });
  assert.equal(
    (
      await s.enqueueJob(
        owner,
        "CCC",
        "candidate",
        "2026-11-12",
        "third",
        {},
        opts,
      )
    ).status,
    "deferred",
  );
});

test("phase handoff preserves persisted references and waits for interactive work", async () => {
  const owner = await user(),
    s = scheduler();
  const job = await s.enqueueJob(
    owner,
    "NVDA",
    "holding",
    "2026-11-11",
    "phase-test",
  );
  await s.advanceJobs();
  const current = (
    await pool.query("SELECT * FROM research_jobs WHERE id=$1", [job.id])
  ).rows[0];
  const task = (await store.claim(owner, worker))!.task;
  assert.equal(task.id, current.active_task_id);
  const resultId = randomUUID(),
    ctx = randomUUID();
  const currentDataset = randomUUID(),
    staleDataset = randomUUID(),
    rawDataset = randomUUID(),
    otherStock = randomUUID();
  for (const [id, symbol, adjustment] of [
    [currentDataset, "NVDA", "split"],
    [staleDataset, "NVDA", "split"],
    [rawDataset, "NVDA", "raw"],
    [otherStock, "AAPL", "split"],
  ])
    await pool.query(
      "INSERT INTO datasets(id,owner_id,metadata,payload,content_hash) VALUES($1,$2,$3,'[]',$1::uuid::text)",
      [id, owner, JSON.stringify({ symbol, adjustment })],
    );
  await pool.query(
    "INSERT INTO research_contexts(id,owner_id,symbol,as_of,payload) VALUES($1,$2,'NVDA',now(),$3)",
    [
      ctx,
      owner,
      JSON.stringify({ datasetRefs: [currentDataset, rawDataset, otherStock] }),
    ],
  );
  await pool.query("UPDATE research_jobs SET checkpoint=$2 WHERE id=$1", [
    job.id,
    JSON.stringify({ datasetRefs: [staleDataset] }),
  ]);
  await pool.query(
    "INSERT INTO results(id,owner_id,task_id,kind,payload,lineage) VALUES($1,$2,$3,'research_summary',$4,$5)",
    [
      resultId,
      owner,
      task.id,
      JSON.stringify({ text: "evidence gathered" }),
      JSON.stringify({ researchContextRef: ctx }),
    ],
  );
  await store.finish(owner, task.id, worker, task.generation, "completed", {
    resultRefs: ["fake-model-reference"],
  });
  const thread = await store.createThread(owner);
  const userTask = await store.submit(owner, thread.id, "question", {
    input: "urgent question",
  });
  await s.advanceJobs();
  const pending = (
    await pool.query("SELECT * FROM research_jobs WHERE id=$1", [job.id])
  ).rows[0];
  assert.equal(pending.stage, "analyze");
  assert.equal(pending.active_task_id, null);
  assert.deepEqual(pending.checkpoint.resultRefs, [resultId]);
  assert.equal(pending.checkpoint.researchContextRef, ctx);
  assert.deepEqual(pending.checkpoint.datasetRefs, [currentDataset]);
  assert.deepEqual(pending.checkpoint.historicalDatasetRefs, [staleDataset]);
  assert.equal((await finish(owner)).id, userTask.id);
  await s.advanceJobs();
  const next = (
    await pool.query("SELECT * FROM research_jobs WHERE id=$1", [job.id])
  ).rows[0];
  const nextTask = await store.get(owner, next.active_task_id);
  assert.equal(nextTask.input.researchPhase, "analyze");
  assert.equal(nextTask.input.researchContextRef, ctx);
});

test("unknown tasks block a job; completed review without saved review is not success", async () => {
  const owner = await user(),
    s = scheduler(),
    job = await s.enqueueJob(
      owner,
      "NVDA",
      "review",
      "2026-11-11",
      "review-missing",
      { forecastId: randomUUID() },
    );
  await s.advanceJobs();
  const task = (await store.claim(owner, worker))!.task;
  await store.defer(
    owner,
    task.id,
    worker,
    task.generation,
    "unknown",
    "lost_response",
    0,
  );
  await s.advanceJobs();
  assert.equal(
    (await pool.query("SELECT status FROM research_jobs WHERE id=$1", [job.id]))
      .rows[0].status,
    "blocked",
  );
  const recovered = (await store.take(owner, worker))!;
  await store.finish(
    owner,
    task.id,
    worker,
    recovered.generation,
    "completed",
    {},
  );
  await s.advanceJobs();
  const row = (
    await pool.query("SELECT * FROM research_jobs WHERE id=$1", [job.id])
  ).rows[0];
  assert.equal(row.status, "failed");
  assert.equal(row.error_code, "review_output_missing");
});

test("a retained pair completes analysis without creating a publish stage", async () => {
  const owner = await user(),
    s = scheduler(),
    job = await s.enqueueJob(owner, "NVDA", "holding", "2026-11-11", "retain");
  await pool.query("UPDATE research_jobs SET stage='analyze' WHERE id=$1", [
    job.id,
  ]);
  await s.advanceJobs();
  const task = (await store.claim(owner, worker))!.task;
  const refs = [
    await savedForecast(owner, task.id, 5),
    await savedForecast(owner, task.id, 20),
  ];
  await pool.query(
    "INSERT INTO results(id,owner_id,task_id,kind,payload,lineage) VALUES($1,$2,$3,'research_summary',$4,'{}')",
    [
      randomUUID(),
      owner,
      task.id,
      JSON.stringify({
        text: "No material change",
        forecastAction: "retain",
        forecastIds: refs,
      }),
    ],
  );
  await store.finish(owner, task.id, worker, task.generation, "completed", {});
  await s.advanceJobs();
  const complete = (
    await pool.query("SELECT * FROM research_jobs WHERE id=$1", [job.id])
  ).rows[0];
  assert.equal(complete.status, "completed");
  assert.equal(complete.stage, "analyze");
  assert.equal(
    (
      await pool.query(
        "SELECT count(*)::int n FROM tasks WHERE research_job_id=$1",
        [job.id],
      )
    ).rows[0].n,
    1,
  );
});

test("explicit applicable lesson proposals create two isolated bound trial sessions only once", async () => {
  const owner = await user(),
    thread = await store.createThread(owner),
    task = await store.submit(owner, thread.id, "proposal", {
      input: "analyse",
    });
  await finish(owner);
  const original = await savedForecast(owner, task.id),
    review = randomUUID(),
    reviewResult = randomUUID(),
    lesson = randomUUID(),
    version = randomUUID(),
    evidence = randomUUID(),
    context = randomUUID();
  await pool.query(
    "INSERT INTO results(id,owner_id,task_id,kind,payload,lineage) VALUES($1,$2,$3,'review','{}','{}')",
    [reviewResult, owner, task.id],
  );
  await pool.query(
    "INSERT INTO forecast_reviews(id,forecast_id,owner_id,task_id,result_id,operation_key,request_hash,payload) VALUES($1,$2,$3,$4,$5,'review','fixture','{}')",
    [review, original, owner, task.id, reviewResult],
  );
  await pool.query(
    "INSERT INTO experience_lessons(id,owner_id,title,scope,status) VALUES($1,$2,'guidance test','company','shadow')",
    [lesson, owner],
  );
  await pool.query(
    "INSERT INTO experience_versions(id,owner_id,lesson_id,review_id,horizon,symbol,event_type,statement,applicability,invalidation,created_at) VALUES($1,$2,$3,$4,5,'NVDA','guidance','rule','guidance event','counterexample',now()-interval '2 hours')",
    [version, owner, lesson, review],
  );
  await pool.query(
    "UPDATE experience_lessons SET current_version_id=$2 WHERE id=$1",
    [lesson, version],
  );
  const key = createHash("sha256").update("sec:filing-new").digest("hex");
  const now = new Date(),
    published = new Date(+now - 3600000),
    observed = new Date(+now - 1800000);
  await pool.query(
    "INSERT INTO research_evidence(id,owner_id,symbol,source,source_key,published_at,observed_at,content_hash,payload) VALUES($1,$2,'NVDA','sec','filing-new',$3,$4,'fixture','{}')",
    [evidence, owner, published, observed],
  );
  await pool.query(
    "INSERT INTO research_contexts(id,owner_id,symbol,as_of,payload) VALUES($1,$2,'NVDA',$3,$4)",
    [
      context,
      owner,
      now,
      JSON.stringify({
        symbol: "NVDA",
        evidenceRefs: [evidence],
        evidence: [
          {
            id: evidence,
            eventKey: key,
            source: "sec",
            source_key: "filing-new",
            published_at: published,
          },
        ],
        lessons: [],
      }),
    ],
  );
  await pool.query(
    "INSERT INTO results(id,owner_id,task_id,kind,payload,lineage) VALUES($1,$2,$3,'research_summary',$4,$5)",
    [
      randomUUID(),
      owner,
      task.id,
      JSON.stringify({
        text: "Applicable event",
        trialCandidates: [{ lessonVersionRef: version, eventKey: key }],
      }),
      JSON.stringify({ researchContextRef: context }),
    ],
  );
  // Exercise the same scheduling entry point used by the live worker, with actual assignment persistence.
  await pool.query(
    "INSERT INTO research_settings(owner_id,payload) SELECT id,'{\"enabled\":false}'::jsonb FROM users WHERE id<>$1 ON CONFLICT(owner_id) DO UPDATE SET payload=excluded.payload",
    [owner],
  );
  const experience = new ExperienceStore(pool);
  const deps = {
    research: {
      syncCoverage: async () => {},
      listCoverage: async () => [],
      collect: async () => ({}),
      context: async () => ({}),
    },
    scanner: { scan: async () => ({ selected: [] }) },
    forecasts: { evaluateDue: async () => ({}) },
    experience,
    calendar: {
      run: async () => ({
        session: null,
        marketOpen: null,
        marketClose: null,
        latestCompletedSession: "2026-11-11",
        temporaryExpirySession: "2026-12-10",
      }),
    },
  };
  await new ResearchScheduler(pool, deps).tick(new Date());
  await new ResearchScheduler(pool, deps).tick(new Date());
  const jobs = (
    await pool.query(
      "SELECT * FROM research_jobs WHERE owner_id=$1 AND tier='trial'",
      [owner],
    )
  ).rows;
  assert.equal(jobs.length, 2);
  assert.equal(new Set(jobs.map((j: any) => j.thread_id)).size, 2);
  assert.equal(new Set(jobs.map((j: any) => j.checkpoint.trialArm)).size, 2);
  assert.equal(
    (
      await pool.query(
        "SELECT count(*)::int n FROM experience_trials WHERE owner_id=$1",
        [owner],
      )
    ).rows[0].n,
    1,
  );
  assert.equal(
    (
      await pool.query(
        "SELECT count(*)::int n FROM experience_trial_arm_bindings WHERE owner_id=$1",
        [owner],
      )
    ).rows[0].n,
    2,
  );
  assert.equal(
    (
      await pool.query(
        "SELECT count(*)::int n FROM messages WHERE owner_id=$1",
        [owner],
      )
    ).rows[0].n,
    1,
  );
});

test("saved forecast revisions enqueue one durable comparison notification", async () => {
  const owner = await user(),
    thread = await store.createThread(owner),
    task = await store.submit(owner, thread.id, "forecast", {
      input: "forecast",
    });
  await finish(owner);
  const original = await savedForecast(owner, task.id),
    revision = await savedForecast(owner, task.id, 5, original);
  await scheduler().tick(new Date());
  await scheduler().tick(new Date());
  const notices = (
    await pool.query(
      "SELECT * FROM notifications WHERE owner_id=$1 AND kind='forecast_changed'",
      [owner],
    )
  ).rows;
  assert.equal(notices.length, 1);
  assert.equal(notices[0].payload.forecastId, revision);
  assert.match(notices[0].payload.text, /5.0% → -3.0%/);
  assert.match(notices[0].payload.text, /原预测仍保留/);
});

test("schedule retries and worker restarts deduplicate scan and trading-day digest", async () => {
  const owner = await user();
  let scans = 0,
    settles = 0;
  const deps = {
    research: {
      syncCoverage: async () => {},
      listCoverage: async () => [],
      collect: async () => ({}),
      context: async () => ({}),
    },
    scanner: {
      scan: async () => {
        scans++;
        return { selected: [] };
      },
    },
    forecasts: {
      evaluateDue: async () => {
        settles++;
      },
    },
    calendar: {
      run: async () => ({
        session: null,
        marketOpen: null,
        marketClose: null,
        latestCompletedSession: "2026-11-13",
        temporaryExpirySession: "2026-12-11",
      }),
    },
  };
  // Other tests leave owners with jobs; disable them to focus on this scheduler scope.
  await pool.query(
    "INSERT INTO research_settings(owner_id,payload) SELECT id,'{\"enabled\":false}'::jsonb FROM users WHERE id<>$1 ON CONFLICT(owner_id) DO UPDATE SET payload=excluded.payload",
    [owner],
  );
  await new ResearchScheduler(pool, deps).tick(
    new Date("2026-11-14T01:00:00Z"),
  );
  await new ResearchScheduler(pool, deps).tick(
    new Date("2026-11-14T01:01:00Z"),
  );
  await new ResearchScheduler(pool, deps).tick(
    new Date("2026-11-15T01:01:00Z"),
  );
  assert.equal(scans, 1);
  assert.equal(settles, 3);
  assert.equal(
    (
      await pool.query(
        "SELECT count(*)::int n FROM notifications WHERE owner_id=$1 AND kind='daily_digest'",
        [owner],
      )
    ).rows[0].n,
    1,
  );
});

test("candidate expiry uses trading sessions and expired coverage stops while settlement continues", async () => {
  const owner = await user();
  await pool.query(
    "INSERT INTO research_settings(owner_id,payload) SELECT id,'{\"enabled\":false}'::jsonb FROM users WHERE id<>$1 ON CONFLICT(owner_id) DO UPDATE SET payload=excluded.payload",
    [owner],
  );
  await pool.query(
    "INSERT INTO research_coverage(owner_id,symbol,tier,expires_session) VALUES($1,'TEMP','temporary','2026-12-10')",
    [owner],
  );
  let latestSession = "2026-11-11",
    settlements = 0;
  const s = new ResearchScheduler(pool, {
    research: {
      syncCoverage: async () => {},
      listCoverage: async () => [],
      collect: async () => ({}),
      context: async () => ({ contextRef: randomUUID() }),
    },
    scanner: {
      scan: async () => ({
        selected: latestSession === "2026-11-11" ? [{ symbol: "NEW" }] : [],
      }),
    },
    forecasts: {
      evaluateDue: async () => {
        settlements++;
      },
    },
    calendar: {
      run: async () => ({
        session: null,
        marketOpen: null,
        marketClose: null,
        latestCompletedSession: latestSession,
        temporaryExpirySession: "2026-12-10",
      }),
    },
  });
  await s.tick(new Date("2026-11-12T01:00:00Z"));
  assert.equal(
    (
      await pool.query(
        "SELECT expires_session::text FROM research_coverage WHERE owner_id=$1 AND symbol='NEW'",
        [owner],
      )
    ).rows[0].expires_session,
    "2026-12-10",
  );
  latestSession = "2026-12-10";
  await s.tick(new Date("2026-12-11T01:00:00Z"));
  const coverage = (
    await pool.query(
      "SELECT symbol,paused FROM research_coverage WHERE owner_id=$1 ORDER BY symbol",
      [owner],
    )
  ).rows;
  assert.deepEqual(coverage, [
    { symbol: "NEW", paused: true },
    { symbol: "TEMP", paused: true },
  ]);
  assert.equal(settlements, 2);
});

test("a failed admission never advances the evidence watermark and retries preserve one job", async () => {
  const owner = await user(),
    s = scheduler();
  s.dependencies.research.listCoverage = async () => [
    { symbol: "NVDA", tier: "holding" },
  ];
  s.dependencies.research.collect = async () => ({
    fingerprint: "changed",
    evidenceRefs: [],
  });
  const original = s.enqueueJob.bind(s);
  s.enqueueJob = async () => {
    throw Error("lost database connection");
  };
  await assert.rejects(
    (s as any).checkCoverage(owner, "2026-11-11", {}, new Date(), false),
    /lost database/,
  );
  assert.equal(
    (
      await pool.query(
        "SELECT count(*)::int n FROM research_checks WHERE owner_id=$1",
        [owner],
      )
    ).rows[0].n,
    0,
  );
  s.enqueueJob = original;
  await (s as any).checkCoverage(owner, "2026-11-11", {}, new Date(), false);
  await (s as any).checkCoverage(owner, "2026-11-11", {}, new Date(), false);
  assert.equal(
    (
      await pool.query(
        "SELECT count(*)::int n FROM research_jobs WHERE owner_id=$1",
        [owner],
      )
    ).rows[0].n,
    1,
  );
  assert.equal(
    (
      await pool.query(
        "SELECT count(*)::int n FROM research_checks WHERE owner_id=$1",
        [owner],
      )
    ).rows[0].n,
    1,
  );
});
