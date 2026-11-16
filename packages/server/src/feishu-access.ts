import type { Pool } from "pg";
import { transaction } from "./db.js";
/** Explicit deployment migration for the existing dedicated bot role.
 * Never grant SELECT on users, sessions, OAuth state, or browser challenge secrets.
 */
export async function grantFeishuIdentityAccess(pool: Pool, role: string) {
  if (!/^[a-z][a-z0-9_]{2,50}$/.test(role) || role.startsWith("pg_"))
    throw new Error("invalid_feishu_database_role");
  await transaction(pool, async (c) => {
    const schema = (await c.query("SELECT current_schema() AS name")).rows[0]
      .name;
    const prefix = `"${String(schema).replaceAll('"', '""')}".`;
    const grant = async (rights: string, table: string) =>
      c.query(`GRANT ${rights} ON TABLE ${prefix}"${table}" TO "${role}"`);
    await grant("SELECT(context_id,status)", "acceptance_capacity");
    await grant("INSERT(id,display_name)", "users");
    await grant("UPDATE(source)", "threads");
    await grant("SELECT, INSERT", "feishu_identities");
    await grant("SELECT, INSERT, UPDATE", "execution_contexts");
    await grant("SELECT, INSERT, UPDATE(context_id)", "thread_contexts");
    await grant("SELECT, INSERT", "profile_contexts");
    await grant("SELECT, INSERT", "profile_instances");
    await grant("SELECT, INSERT(owner_id)", "profile_preparation_jobs");
    await grant("SELECT, INSERT, UPDATE", "auth_attempts");
  });
}
