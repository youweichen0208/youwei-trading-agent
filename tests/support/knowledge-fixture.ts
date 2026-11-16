// Browser-only synthetic fixtures; never imported by production code.
import { randomUUID } from "node:crypto";
import { Pool } from "pg";
import { seedForecasts } from "./forecast-fixture.js";

export async function seedKnowledge(email: string) {
  const url = process.env.TEST_DATABASE_URL;
  if (!url || !new URL(url).pathname.endsWith("_test"))
    throw new Error("Dedicated TEST_DATABASE_URL ending in _test required");
  await seedForecasts(email);
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
    const forecast = (
      await pool.query(
        "SELECT id,task_id FROM forecasts WHERE owner_id=$1 ORDER BY published_at DESC LIMIT 1",
        [owner],
      )
    ).rows[0];
    const reviewResult = randomUUID();
    await pool.query(
      "INSERT INTO results(id,owner_id,task_id,kind,payload,lineage) VALUES($1,$2,$3,'review',$4,$5)",
      [
        reviewResult,
        owner,
        forecast.task_id,
        JSON.stringify({ verdict: "fixture" }),
        JSON.stringify({ forecastRef: forecast.id }),
      ],
    );
    const reviewId = randomUUID();
    await pool.query(
      "INSERT INTO forecast_reviews(id,forecast_id,owner_id,task_id,result_id,operation_key,request_hash,payload) VALUES($1,$2,$3,$4,$5,$6,$7,$8)",
      [
        reviewId,
        forecast.id,
        owner,
        forecast.task_id,
        reviewResult,
        randomUUID(),
        randomUUID(),
        JSON.stringify({ verdict: "fixture" }),
      ],
    );
    const lessonId = randomUUID(),
      versionId = randomUUID();
    await pool.query(
      "INSERT INTO experience_lessons(id,owner_id,title,scope,status) VALUES($1,$2,$3,'company','shadow')",
      [lessonId, owner, "指引兑现后的短期动量"],
    );
    await pool.query(
      `INSERT INTO experience_versions(id,owner_id,lesson_id,review_id,horizon,symbol,event_type,statement,applicability,invalidation)
       VALUES($1,$2,$3,$4,5,'NVDA','guidance',$5,$6,$7)`,
      [
        versionId,
        owner,
        lessonId,
        reviewId,
        "当公司上调指引且股价尚未反映时，未来 5 个交易日倾向上涨。",
        "指引上调且公告后首个交易日成交量高于 20 日均量。",
        "指引上调后 5 日内出现反向公告或成交量持续萎缩。",
      ],
    );
    await pool.query(
      "UPDATE experience_lessons SET current_version_id=$2 WHERE id=$1",
      [lessonId, versionId],
    );
    const contextId = randomUUID();
    await pool.query(
      "INSERT INTO research_contexts(id,owner_id,symbol,as_of,payload) VALUES($1,$2,'NVDA',$3,'{}')",
      [contextId, owner, new Date(Date.now() - 3600_000).toISOString()],
    );
    await pool.query(
      "INSERT INTO research_contexts(id,owner_id,symbol,as_of,payload) VALUES($1,$2,'AMD',$3,'{}')",
      [
        randomUUID(),
        owner,
        new Date(Date.now() - 7200_000).toISOString(),
      ],
    );
    await pool.query(
      "INSERT INTO results(id,owner_id,task_id,kind,payload,lineage) VALUES($1,$2,$3,'research_summary',$4,$5)",
      [
        randomUUID(),
        owner,
        forecast.task_id,
        JSON.stringify({
          text: [
            "合成研究摘要：证据支持温和上涨，需关注指引变化。",
            "",
            "一、研究背景",
            "1. 指引上调且成交量放大。",
            "2. 同业估值仍在区间下沿。",
            "",
            "二、结论",
            "证据支持温和上涨，需关注指引变化。",
          ].join("\n"),
        }),
        JSON.stringify({ researchContextRef: contextId }),
      ],
    );
    await pool.query(
      "INSERT INTO results(id,owner_id,task_id,kind,payload,lineage) VALUES($1,$2,$3,'research_summary',$4,'{}')",
      [
        randomUUID(),
        owner,
        forecast.task_id,
        JSON.stringify({ text: "无研究上下文的摘要，不应出现在知识库。" }),
      ],
    );
  } finally {
    await pool.end();
  }
}
