import { database } from "../packages/server/src/db.js";
import { migrate } from "../packages/server/src/migrate.js";
const url = process.env.DATABASE_URL;
if (!url || !new URL(url).pathname.endsWith("_test"))
  throw new Error("isolated_test_database_required");
const pool = database();
async function evidence() {
  const result: Record<string, unknown> = {};
  for (const table of [
    "users",
    "threads",
    "tasks",
    "datasets",
    "results",
    "messages",
    "profile_instances",
    "profile_sessions",
    "portfolios",
    "portfolio_snapshots",
  ])
    result[table] = (
      await pool.query(`SELECT count(*)::int AS count FROM ${table}`)
    ).rows[0].count;
  for (const table of [
    "datasets",
    "results",
    "messages",
    "profile_sessions",
    "portfolio_snapshots",
  ])
    result[`${table}_digest`] = (
      await pool.query(
        `SELECT md5(coalesce(string_agg(value,',' ORDER BY value),'')) AS hash FROM (SELECT row_to_json(t)::text AS value FROM ${table} t) rows`,
      )
    ).rows[0].hash;
  result.profiles = (
    await pool.query(
      "SELECT id,owner_id,role FROM profile_instances ORDER BY id",
    )
  ).rows;
  return JSON.stringify(result);
}
try {
  const before = await evidence();
  await migrate(pool);
  if (before !== (await evidence())) throw new Error("historical_data_changed");
  const missing = (
    await pool.query(
      `SELECT count(*)::int AS n FROM users u LEFT JOIN execution_contexts c ON c.id=u.id AND c.owner_id=u.id AND c.kind='personal' WHERE c.id IS NULL`,
    )
  ).rows[0].n;
  if (missing) throw new Error("personal_context_backfill_missing");
  await migrate(pool);
  console.info("migration_preserved_historical_facts_profiles_and_accounts");
} finally {
  await pool.end();
}
