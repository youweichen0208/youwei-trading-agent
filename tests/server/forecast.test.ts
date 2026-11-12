import { before, after, test } from "node:test";
import assert from "node:assert/strict";
import { randomUUID, createHash } from "node:crypto";
import { Pool } from "pg";
import { migrate } from "../../packages/server/src/migrate.js";
import { TaskStore } from "../../packages/server/src/tasks.js";
import {
  ForecastStore,
  forecastInput,
} from "../../packages/server/src/forecast-store.js";
import { ExperienceStore } from "../../packages/server/src/experience-store.js";
import { ResearchStore } from "../../packages/server/src/research-store.js";
import type { MarketProvider } from "../../packages/market/src/alpaca.js";
const url = process.env.TEST_DATABASE_URL;
if (!url || !new URL(url).pathname.endsWith("_test"))
  throw new Error("Dedicated TEST_DATABASE_URL ending in _test required");
const schema = `test_${randomUUID().replaceAll("-", "")}`,
  admin = new Pool({ connectionString: url }),
  pool = new Pool({
    connectionString: url,
    options: `-c search_path=${schema}`,
  });
const tasks = new TaskStore(pool),
  clock = () => new Date("2025-07-03T18:00:00Z"),
  forecasts = new ForecastStore(pool, undefined, clock);
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
    profile = randomUUID(),
    predictionProfile = randomUUID(),
    worker = randomUUID();
  await pool.query(
    "INSERT INTO users(id,email,password_hash) VALUES($1,$2,'test')",
    [owner, `${owner}@test.invalid`],
  );
  await pool.query(
    "INSERT INTO profile_instances(id,owner_id,role) VALUES($1,$2,'research')",
    [profile, owner],
  );
  await pool.query(
    "INSERT INTO profile_instances(id,owner_id,role) VALUES($1,$2,'prediction')",
    [predictionProfile, owner],
  );
  const thread = await tasks.createThread(owner);
  await tasks.submit(owner, thread.id, randomUUID(), { input: "forecast" });
  const task = (await tasks.claim(owner, worker))!.task;
  await tasks.prepareExecution(
    owner,
    task.id,
    worker,
    task.generation,
    { input: "forecast" },
    profile,
    randomUUID(),
  );
  await tasks.beginSubmission(
    owner,
    task.id,
    worker,
    task.generation,
    "test-runtime",
  );
  await tasks.spawn(
    owner,
    task.id,
    worker,
    task.generation,
    "prediction",
    "forecast-child",
    { input: "predict" },
  );
  const child = (await tasks.claim(owner, worker))!.task;
  await tasks.prepareExecution(
    owner,
    child.id,
    worker,
    child.generation,
    { input: "predict" },
    predictionProfile,
    randomUUID(),
  );
  await tasks.beginSubmission(
    owner,
    child.id,
    worker,
    child.generation,
    "test-runtime",
  );
  const evidence = randomUUID(),
    context = randomUUID();
  await pool.query(
    "INSERT INTO research_evidence(id,owner_id,symbol,source,source_key,published_at,observed_at,content_hash,payload) VALUES($1,$2,'NVDA','fixture',$1::uuid::text,'2025-07-03T12:00:00Z','2025-07-03T13:00:00Z','test','{}')",
    [evidence, owner],
  );
  await pool.query(
    "INSERT INTO research_contexts(id,owner_id,symbol,as_of,payload,created_at) VALUES($1,$2,'NVDA','2025-07-03T17:00:00Z',$3,'2025-07-03T17:00:00Z')",
    [context, owner, JSON.stringify({ evidenceRefs: [evidence] })],
  );
  const input = {
    symbol: "NVDA",
    horizon: 5,
    meanReturn: "0.2",
    lowerReturn: "0",
    upperReturn: "0.3",
    upProbability: "0.7",
    researchContextRef: context,
    evidenceRefs: [evidence],
    datasetRefs: [],
    eventKey: "earnings-q2",
    thesis: "Test forward thesis",
  };
  return {
    owner,
    ctx: { ownerId: owner, taskId: child.id, profileId: predictionProfile },
    researchCtx: { ownerId: owner, taskId: task.id, profileId: profile },
    input,
  };
}
const provider: MarketProvider = {
  source: "fixture",
  feed: "fixture",
  version: "v1",
  async bars(q) {
    return [
      {
        session: q.start,
        open: "100",
        high: "110",
        low: "90",
        close: "105",
        volume: "1",
      },
      {
        session: q.end,
        open: "110",
        high: "130",
        low: "100",
        close: q.symbol === "SPY" ? "110" : "120",
        volume: "1",
      },
    ];
  },
};
test("publishing is idempotent, immutable, prospective and isolated", async () => {
  const a = await setup(),
    first = await forecasts.publish(a.ctx, a.input, "same");
  assert.equal(
    (await forecasts.publish(a.ctx, a.input, "same")).forecastId,
    first.forecastId,
  );
  await assert.rejects(
    forecasts.publish(a.ctx, { ...a.input, thesis: "changed" }, "same"),
    /operation_key_conflict/,
  );
  const item = await forecasts.detail(a.owner, first.forecastId);
  assert.equal(item.start_session, "2025-07-07");
  assert.equal(item.target_session, "2025-07-11");
  assert.equal(item.payload.subjective, true);
  assert.equal(item.payload.intervalCoverage, "0.8");
  await assert.rejects(
    pool.query("UPDATE forecasts SET symbol='AAPL' WHERE id=$1", [
      first.forecastId,
    ]),
    /immutable_fact/,
  );
  await assert.rejects(
    forecasts.detail(randomUUID(), first.forecastId),
    /not_found/,
  );
  const b = await setup();
  await assert.rejects(
    forecasts.publish(b.ctx, a.input, "foreign"),
    /research_context_not_found/,
  );
  assert.equal(
    forecastInput.safeParse({ ...a.input, startPrice: "100" }).success,
    false,
  );
  assert.equal(
    forecastInput.safeParse({ ...a.input, upProbability: "1.1" }).success,
    false,
  );
});
test("missing prices remain pending and resuming settles once from same snapshot", async () => {
  const a = await setup(),
    saved = await forecasts.publish(a.ctx, a.input, "resolve");
  const before = await forecasts.evaluateDue(
    provider,
    new Date("2025-07-11T20:19:59Z"),
  );
  assert.equal(before.evaluated.length, 0);
  assert.ok(
    (await forecasts.evaluateDue(undefined, new Date("2025-07-11T20:20:00Z")))
      .pending > 0,
  );
  assert.equal(
    (await forecasts.detail(a.owner, saved.forecastId)).pending_reason,
    "market_provider_unavailable",
  );
  const missing = {
    ...provider,
    async bars() {
      return [];
    },
  };
  assert.ok(
    (await forecasts.evaluateDue(missing, new Date("2025-07-11T20:20:00Z")))
      .pending > 0,
  );
  const success = await forecasts.evaluateDue(
    provider,
    new Date("2025-07-11T20:20:00Z"),
  );
  assert.ok(success.evaluated.some((e) => e.forecastId === saved.forecastId));
  const item = await forecasts.detail(a.owner, saved.forecastId);
  assert.equal(item.evaluation.payload.actualReturn, "0.2");
  assert.equal(item.evaluation.payload.excessReturn, "0.1");
  assert.equal(item.evaluation.payload.metrics.absoluteError, "0.0");
  assert.equal(
    (await forecasts.evaluateDue(provider, new Date("2025-07-12T00:00:00Z")))
      .evaluated.length,
    0,
  );
  const revision = await forecasts.publish(
    a.ctx,
    {
      ...a.input,
      supersedesForecastId: saved.forecastId,
      thesis: "Revised view",
    },
    "revision",
  );
  assert.notEqual(revision.forecastId, saved.forecastId);
  assert.equal(
    (await forecasts.detail(a.owner, saved.forecastId)).payload.thesis,
    a.input.thesis,
  );
});
test("future observations cannot enter published frozen evidence", async () => {
  const a = await setup(),
    future = randomUUID(),
    context = randomUUID();
  await pool.query(
    "INSERT INTO research_evidence(id,owner_id,symbol,source,source_key,observed_at,content_hash,payload) VALUES($1,$2,'NVDA','fixture',$1::uuid::text,'2025-07-04T00:00:00Z','future','{}')",
    [future, a.owner],
  );
  await pool.query(
    "INSERT INTO research_contexts(id,owner_id,symbol,as_of,payload,created_at) VALUES($1,$2,'NVDA','2025-07-03T17:00:00Z',$3,'2025-07-03T17:00:00Z')",
    [context, a.owner, JSON.stringify({ evidenceRefs: [future] })],
  );
  await assert.rejects(
    forecasts.publish(
      a.ctx,
      { ...a.input, researchContextRef: context, evidenceRefs: [future] },
      "future",
    ),
    /future_evidence/,
  );
});
test("reviews require resolved outcomes and make candidate lessons, never immediate adopted rules", async () => {
  const a = await setup(),
    f = await forecasts.publish(a.ctx, a.input, "reviewed"),
    experience = new ExperienceStore(pool);
  const request = {
    forecastId: f.forecastId,
    text: "Evidence supported the demand thesis; magnitude still needs validation.",
    lesson: {
      title: "Demand upgrade",
      statement: "Demand upgrades may lead returns",
      scope: "general",
      horizon: 5,
      eventType: "guidance",
      applicability: "Use around guidance updates",
      invalidation: "Do not apply without quantified demand",
    },
  };
  await assert.rejects(
    experience.recordReview(a.researchCtx, request, "review"),
    /resolved_forecast_required/,
  );
  await forecasts.evaluateDue(provider, new Date("2025-07-12T00:00:00Z"));
  const review = await experience.recordReview(
    a.researchCtx,
    request,
    "review",
  );
  assert.equal(
    (await experience.recordReview(a.researchCtx, request, "review")).reviewId,
    review.reviewId,
  );
  assert.equal((await experience.list(a.owner))[0].status, "shadow");
  const snapshot = await new ResearchStore(pool).context(a.owner, "NVDA");
  assert.equal(snapshot.lessons.length, 0);
  const offered = snapshot.trialCandidates.find(
    (l: any) => l.version_id === review.lessonVersionRef,
  );
  assert.ok(offered);
  assert.equal("statement" in offered, false);
  assert.equal("title" in offered, false);
  assert.deepEqual(await experience.assessBatches(a.owner), []);
});

async function nextPredictionRun(owner: string) {
  await pool.query(
    "UPDATE tasks SET status='completed' WHERE owner_id=$1 AND status='running'",
    [owner],
  );
  const profiles = (
    await pool.query(
      "SELECT id,role FROM profile_instances WHERE owner_id=$1",
      [owner],
    )
  ).rows;
  const research = profiles.find((p) => p.role === "research").id,
    prediction = profiles.find((p) => p.role === "prediction").id,
    worker = randomUUID();
  const thread = await tasks.createThread(owner);
  await tasks.submit(owner, thread.id, randomUUID(), {
    input: "isolated trial",
  });
  const root = (await tasks.claim(owner, worker))!.task;
  await tasks.prepareExecution(
    owner,
    root.id,
    worker,
    root.generation,
    { input: "trial" },
    research,
    randomUUID(),
  );
  await tasks.beginSubmission(
    owner,
    root.id,
    worker,
    root.generation,
    "test-runtime",
  );
  await tasks.spawn(
    owner,
    root.id,
    worker,
    root.generation,
    "prediction",
    randomUUID(),
    { input: "trial arm" },
  );
  const child = (await tasks.claim(owner, worker))!.task;
  await tasks.prepareExecution(
    owner,
    child.id,
    worker,
    child.generation,
    { input: "trial arm" },
    prediction,
    randomUUID(),
  );
  await tasks.beginSubmission(
    owner,
    child.id,
    worker,
    child.generation,
    "test-runtime",
  );
  return {
    threadId: thread.id,
    ctx: { ownerId: owner, taskId: child.id, profileId: prediction },
  };
}
test("shadow arms are isolated, frozen before future open, immutable and evaluated as pairs", async () => {
  const a = await setup(),
    saved = await forecasts.publish(a.ctx, a.input, "trial-source");
  await forecasts.evaluateDue(provider, new Date("2025-07-12T00:00:00Z"));
  const experience = new ExperienceStore(pool);
  const review = await experience.recordReview(
    a.researchCtx,
    {
      forecastId: saved.forecastId,
      text: "A candidate mechanism to test",
      lesson: {
        title: "Candidate",
        statement: "Candidate mechanism",
        scope: "general",
        horizon: 5,
        eventType: "guidance",
        applicability: "Guidance changes",
        invalidation: "No guidance change",
      },
    },
    "trial-review",
  );
  assert.ok(review.lessonVersionRef);
  const context = randomUUID(),
    now = new Date();
  const eventSource1 = randomUUID(),
    eventSource2 = randomUUID();
  for (const sourceKey of [eventSource1, eventSource2])
    await pool.query(
      "INSERT INTO research_evidence(id,owner_id,symbol,source,source_key,published_at,observed_at,content_hash,payload) VALUES($1,$2,'NVDA','fixture',$1::uuid::text,$3,$3,'trial','{}')",
      [sourceKey, a.owner, now],
    );
  const eventKey1 = createHash("sha256")
      .update(`fixture:${eventSource1}`)
      .digest("hex"),
    eventKey2 = createHash("sha256")
      .update(`fixture:${eventSource2}`)
      .digest("hex");
  await pool.query(
    "INSERT INTO research_contexts(id,owner_id,symbol,as_of,payload) VALUES($1,$2,'NVDA',$3,$4)",
    [
      context,
      a.owner,
      now,
      JSON.stringify({
        symbol: "NVDA",
        asOf: now.toISOString(),
        evidenceRefs: [eventSource1, eventSource2],
        evidence: [{ fact: "same evidence" }],
        forecasts: [{ secret: "candidate mechanism leaked via old forecast" }],
        lessons: [],
      }),
    ],
  );
  const assignment = await experience.createTrialAssignment(
    a.owner,
    { lessonVersionRef: review.lessonVersionRef!, symbol: "NVDA", horizon: 5 },
    { researchContextRef: context, eventKey: eventKey1 },
  );
  const controlThread = (
    await pool.query("SELECT thread_id FROM tasks WHERE id=$1", [a.ctx.taskId])
  ).rows[0].thread_id;
  await experience.bindTrialArm(
    a.owner,
    assignment.trialId,
    "control",
    controlThread,
  );
  const control = await experience.getTrialContext(a.ctx);
  assert.equal(control.arm, "control");
  assert.equal(control.context.lessons.length, 0);
  assert.equal(control.context.forecasts, undefined);
  const distribution = {
    meanReturn: "0.1",
    lowerReturn: "0",
    upperReturn: "0.3",
    upProbability: "0.6",
  };
  await assert.rejects(
    experience.publishTrialArm(
      a.ctx,
      { trialId: assignment.trialId, arm: "candidate", distribution },
      "wrong",
    ),
    /trial_not_assigned/,
  );
  await experience.publishTrialArm(
    a.ctx,
    { trialId: assignment.trialId, arm: "control", distribution },
    "control",
  );
  await assert.rejects(
    experience.publishTrialArm(
      a.ctx,
      {
        trialId: assignment.trialId,
        arm: "control",
        distribution: { ...distribution, upProbability: "0.9" },
      },
      "rewrite",
    ),
    /trial_arm_immutable/,
  );
  const run = await nextPredictionRun(a.owner);
  await experience.bindTrialArm(
    a.owner,
    assignment.trialId,
    "candidate",
    run.threadId,
  );
  const candidate = await experience.getTrialContext(run.ctx);
  assert.equal(candidate.context.lessons.length, 1);
  assert.deepEqual(candidate.context.evidence, control.context.evidence);
  assert.notEqual(candidate.researchContextRef, control.researchContextRef);
  await experience.publishTrialArm(
    run.ctx,
    {
      trialId: assignment.trialId,
      arm: "candidate",
      distribution: {
        ...distribution,
        meanReturn: "0.2",
        upProbability: "0.8",
      },
    },
    "candidate",
  );
  const trial = (
    await pool.query("SELECT * FROM experience_trials WHERE id=$1", [
      assignment.trialId,
    ])
  ).rows[0];
  const evaluated = await experience.evaluateTrials(
    provider,
    new Date(trial.evaluate_after),
  );
  assert.ok(evaluated.evaluated.includes(assignment.trialId));
  const outcome = (
    await pool.query(
      "SELECT payload FROM experience_trial_evaluations WHERE trial_id=$1",
      [assignment.trialId],
    )
  ).rows[0].payload;
  assert.equal(outcome.control.brier, "0.16");
  assert.equal(outcome.candidate.brier, "0.04");
  assert.deepEqual(await experience.assessBatches(a.owner), []);
  const second = await experience.createTrialAssignment(
    a.owner,
    { lessonVersionRef: review.lessonVersionRef!, symbol: "NVDA", horizon: 5 },
    { researchContextRef: context, eventKey: eventKey2 },
  );
  await experience.evaluateTrials(provider, new Date(trial.evaluate_after));
  assert.equal(
    (
      await pool.query(
        "SELECT reason FROM experience_trial_checks WHERE trial_id=$1",
        [second.trialId],
      )
    ).rows[0].reason,
    "expired_missing_arm",
  );
  const lateRun = await nextPredictionRun(a.owner);
  await experience.bindTrialArm(
    a.owner,
    second.trialId,
    "control",
    lateRun.threadId,
  );
  const late = new ExperienceStore(
    pool,
    undefined,
    () => new Date(trial.evaluate_after),
  );
  await assert.rejects(
    late.publishTrialArm(
      lateRun.ctx,
      { trialId: second.trialId, arm: "control", distribution },
      "late",
    ),
    /trial_expired/,
  );
  // Seed independent completed event fixtures to exercise the real SQL grouping and batch policy.
  async function sample(index: number, bad = false) {
    const id = randomUUID(),
      ticker = ["AAPL", "AMD", "MSFT", "TSLA", "NVDA"][index % 5]!;
    await pool.query(
      `INSERT INTO experience_trials(id,owner_id,lesson_version_id,research_context_id,symbol,horizon,event_key,operation_key,request_hash,published_at,start_session,target_session,evaluate_after,payload)
      SELECT $1,owner_id,lesson_version_id,research_context_id,$2,horizon,$3,$1::uuid::text,request_hash,$4,start_session,target_session,evaluate_after,payload FROM experience_trials WHERE id=$5`,
      [
        id,
        ticker,
        `independent-event-${index}`,
        new Date(),
        assignment.trialId,
      ],
    );
    const value = bad
      ? {
          ...outcome,
          candidate: {
            ...outcome.candidate,
            brier: "0.5",
            absoluteError: "0.2",
            intervalScore: "0.6",
          },
        }
      : outcome;
    await pool.query(
      "INSERT INTO experience_trial_evaluations(trial_id,payload) VALUES($1,$2)",
      [id, JSON.stringify(value)],
    );
  }
  for (let i = 0; i < 28; i++) await sample(i);
  assert.deepEqual(await experience.assessBatches(a.owner), []);
  await sample(28);
  const adopted = await experience.assessBatches(a.owner);
  assert.equal(adopted.length, 1);
  assert.equal(adopted[0]!.status, "active");
  assert.deepEqual(await experience.assessBatches(a.owner), []);
  await sample(29, true);
  assert.deepEqual(await experience.assessBatches(a.owner), []);
  for (let i = 30; i < 59; i++) await sample(i, true);
  const paused = await experience.assessBatches(a.owner);
  assert.equal(paused.length, 1);
  assert.equal(paused[0]!.status, "shadow");
});

test("same-event expired forecasts renew as independent periods while active periods require revisions", async () => {
  const a = await setup(),
    first = await forecasts.publish(a.ctx, a.input, "original-period");
  await assert.rejects(
    forecasts.publish(
      a.ctx,
      { ...a.input, thesis: "changed before expiry" },
      "unmarked-revision",
    ),
    /forecast_revision_required/,
  );
  const afterExpiry = new ForecastStore(
    pool,
    undefined,
    () => new Date("2025-07-12T18:00:00Z"),
  );
  const renewed = await afterExpiry.publish(
    a.ctx,
    { ...a.input, thesis: "Same event, new future horizon" },
    "renewed-period",
  );
  assert.notEqual(renewed.forecastId, first.forecastId);
  const item = await afterExpiry.detail(a.owner, renewed.forecastId);
  assert.equal(item.supersedes_id, null);
  assert.equal(item.start_session, "2025-07-14");
  assert.equal(item.target_session, "2025-07-18");
  assert.equal(
    (
      await afterExpiry.publish(
        a.ctx,
        { ...a.input, thesis: "Same event, new future horizon" },
        "repeat-period",
      )
    ).forecastId,
    renewed.forecastId,
  );
  await assert.rejects(
    afterExpiry.publish(
      a.ctx,
      { ...a.input, thesis: "Unmarked same-period change" },
      "duplicate-period",
    ),
    /forecast_revision_required/,
  );
  assert.equal(
    (
      await pool.query(
        "SELECT count(*)::integer AS n FROM forecasts WHERE owner_id=$1 AND supersedes_id IS NULL",
        [a.owner],
      )
    ).rows[0].n,
    2,
  );
});
