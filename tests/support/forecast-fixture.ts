// Browser-only synthetic fixtures; never imported by production code.
import { randomUUID } from "node:crypto";
import { Pool } from "pg";
import { TaskStore } from "../../packages/server/src/tasks.js";
import { ResearchStore } from "../../packages/server/src/research-store.js";
import { ForecastStore } from "../../packages/server/src/forecast-store.js";
import { NotificationStore } from "../../packages/server/src/notifications.js";

export async function seedForecasts(email: string) {
  const url = process.env.TEST_DATABASE_URL;
  if (!url || !new URL(url).pathname.endsWith("_test"))
    throw new Error("Dedicated TEST_DATABASE_URL ending in _test required");
  const pool = new Pool({
    connectionString: url,
    ...(process.env.TEST_DATABASE_SCHEMA
      ? { options: `-c search_path=${process.env.TEST_DATABASE_SCHEMA}` }
      : {}),
  });
  try {
    const owner = (
      await pool.query("SELECT id FROM users WHERE email=$1", [email])
    ).rows[0].id;
    const tasks = new TaskStore(pool),
      worker = randomUUID();
    const thread = await tasks.createThread(owner);
    await tasks.submit(owner, thread.id, randomUUID(), {
      input: "Synthetic forecast browser fixture",
    });
    const root = (await tasks.claim(owner, worker))!.task;
    const researchProfile = (
      await pool.query(
        "SELECT id FROM profile_instances WHERE owner_id=$1 AND role='research'",
        [owner],
      )
    ).rows[0].id;
    await tasks.prepareExecution(
      owner,
      root.id,
      worker,
      root.generation,
      { input: "fixture" },
      researchProfile,
      randomUUID(),
    );
    await tasks.beginSubmission(
      owner,
      root.id,
      worker,
      root.generation,
      "fixture-runtime",
    );
    await tasks.spawn(
      owner,
      root.id,
      worker,
      root.generation,
      "prediction",
      "fixture-prediction",
      {},
    );
    const child = (await tasks.claim(owner, worker))!.task;
    const profile = (
      await pool.query(
        "SELECT id FROM profile_instances WHERE owner_id=$1 AND role='prediction'",
        [owner],
      )
    ).rows[0].id;
    await tasks.prepareExecution(
      owner,
      child.id,
      worker,
      child.generation,
      { input: "fixture" },
      profile,
      randomUUID(),
    );
    await tasks.beginSubmission(
      owner,
      child.id,
      worker,
      child.generation,
      "fixture-runtime",
    );
    const research = new ResearchStore(pool, {
      async collect() {
        return [
          {
            source: "web",
            status: "available",
            failures: [],
            documents: [
              {
                source: "web",
                sourceKey: "browser-fixture",
                publishedAt: new Date(Date.now() - 2000).toISOString(),
                observedAt: new Date(Date.now() - 1000).toISOString(),
                payload: {
                  title: "测试公司公告",
                  text: "仅用于浏览器验收的合成证据",
                  url: "https://example.test/filing",
                },
              },
            ],
          },
        ];
      },
    });
    await research.collect(
      { ownerId: owner },
      { symbol: "NVDA", sources: ["web"] },
      "browser-evidence",
    );
    const context = await research.context(owner, "NVDA");
    const forecasts = new ForecastStore(pool);
    let first: any;
    for (const horizon of [5, 20]) {
      const result = await forecasts.publish(
        { ownerId: owner, taskId: child.id, profileId: profile },
        {
          symbol: "NVDA",
          horizon,
          meanReturn: "0.05",
          lowerReturn: "-0.05",
          upperReturn: "0.15",
          upProbability: "0.65",
          researchContextRef: context.contextRef,
          evidenceRefs: context.evidenceRefs,
          datasetRefs: [],
          eventKey: "browser-fixture",
          thesis: "测试判断：合成证据支持温和上涨。",
        },
        `browser-${horizon}`,
      );
      if (horizon === 5)
        first = await forecasts.detail(owner, result.forecastId);
    }
    await forecasts.evaluateDue(
      {
        source: "synthetic-test-only",
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
              open: "104",
              high: "107",
              low: "103",
              close: q.symbol === "SPY" ? "102" : "106",
              volume: "1",
            },
          ];
        },
      },
      new Date(new Date(first.evaluate_after).getTime() + 1000),
    );
    await new NotificationStore(pool).enqueue(
      owner,
      "evaluation",
      "browser-evaluation",
      { title: "NVDA 测试预测已验证", text: "实际收益6%，仅用于测试。" },
    );
  } finally {
    await pool.end();
  }
}
