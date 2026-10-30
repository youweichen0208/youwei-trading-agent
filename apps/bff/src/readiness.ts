import type { Pool } from "pg";
import { z } from "zod";
import type { AlphaClient } from "./alpha.js";
import type { Config } from "./config.js";

const tables = ["users", "login_attempts", "login_account_attempts", "login_credentials", "login_sessions", "login_revocations", "research_sessions", "research_runs", "agent_events", "research_messages", "run_cancellations"];
const sequences = ["login_attempts_id_seq", "login_account_attempts_id_seq", "login_credentials_id_seq", "research_messages_id_seq"];

export async function databaseStatus(pool: Pool): Promise<string> {
  let connection;
  let timer: NodeJS.Timeout | undefined;
  let released = false;
  try {
    connection = await pool.connect();
    const client = connection;
    timer = setTimeout(() => { released = true; client.release(true); }, 3000);
    timer.unref();
    await connection.query("BEGIN READ ONLY");
    await connection.query("SET LOCAL statement_timeout = '2s'");
    await connection.query("SET LOCAL lock_timeout = '1s'");
    const names = tables.map(name => `yta.${name}`);
    const result = await connection.query(
      "SELECT name,to_regclass(name) IS NOT NULL AS present," +
      "has_table_privilege(current_user,to_regclass(name),'SELECT') " +
      "AND has_table_privilege(current_user,to_regclass(name),'INSERT') " +
      "AND NOT has_table_privilege(current_user,to_regclass(name),'UPDATE') " +
      "AND NOT has_table_privilege(current_user,to_regclass(name),'DELETE') " +
      "AND NOT has_table_privilege(current_user,to_regclass(name),'TRUNCATE') AS safe " +
      "FROM unnest($1::text[]) name", [names]);
    if (result.rows.some(row => !row.present)) return "schema_missing";
    if (result.rows.some(row => row.safe !== true)) return "permissions_invalid";
    const sequence = await connection.query(
      "SELECT to_regclass(name) IS NOT NULL AS present," +
      "has_sequence_privilege(current_user,to_regclass(name),'USAGE') " +
      "AND has_sequence_privilege(current_user,to_regclass(name),'SELECT') AS allowed " +
      "FROM unnest($1::text[]) name", [sequences.map(name => `yta.${name}`)]);
    if (sequence.rows.some(row => !row.present)) return "schema_missing";
    if (sequence.rows.some(row => row.allowed !== true)) return "permissions_invalid";
    const immutableNames = names.filter(name => name !== "yta.users");
    const guards = await connection.query("SELECT count(*)::integer AS count FROM pg_trigger WHERE tgrelid=ANY(SELECT to_regclass(name) FROM unnest($1::text[]) name) AND tgname='immutable_fact' AND tgenabled='O'", [immutableNames]);
    if (guards.rows[0]?.count !== immutableNames.length) return "immutable_guards_missing";
    return "ready";
  } catch { return "unavailable"; }
  finally {
    clearTimeout(timer);
    // A probe owns its read-only connection; destroying it also bounds stalled socket reads.
    if (connection && !released) connection.release(true);
  }
}

export async function readiness(pool: Pool, alpha: AlphaClient, config: Config) {
  const checkAlpha = async () => {
    try {
      const body = await alpha.request("/internal/v1/ready", undefined, undefined, undefined, AbortSignal.timeout(3000));
      const parsed = z.object({status: z.literal("ready"), service: z.literal("alpha-engine"), data_mode: z.literal(config.YTA_DATA_MODE), checks: z.object({database: z.literal("ready"), market: z.literal("replay")})}).safeParse(body);
      return parsed.success ? "ready" : "not_ready";
    } catch { return "not_ready"; }
  };
  const [database, upstream] = await Promise.all([databaseStatus(pool), checkAlpha()]);
  const research = config.YTA_DATA_MODE === "REPLAY" ? "replay" : "unverified";
  const admission = config.YTA_WRITE_ADMISSION;
  return {status: admission === "OPEN" && database === "ready" && upstream === "ready" && research === "replay" ? "ready" : "not_ready", service: "yta-bff", data_mode: config.YTA_DATA_MODE, checks: {database, alpha: upstream, research, admission}};
}
