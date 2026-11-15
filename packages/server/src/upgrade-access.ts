import type { Pool } from "pg";
import { transaction } from "./db.js";
import { grantFeishuIdentityAccess } from "./feishu-access.js";
import { grantResearchAccess } from "./research-access.js";
/** Apply additive grants after migrations to already-provisioned runtime roles. */
export async function upgradeRuntimeAccess(
  pool: Pool,
  roles: { web: string; worker: string; tools: string; feishu: string },
) {
  for (const name of Object.values(roles))
    if (!/^[a-z][a-z0-9_]{2,50}$/.test(name) || name.startsWith("pg_"))
      throw new Error("invalid_runtime_role");
  if (new Set(Object.values(roles)).size !== 4)
    throw new Error("distinct_runtime_roles_required");
  await transaction(pool, async (c) => {
    const schema = (await c.query("SELECT current_schema() AS name")).rows[0]
      .name;
    const prefix = `"${String(schema).replaceAll('"', '""')}".`;
    await grantResearchAccess(c, schema, roles);
    const grant = async (
      service: keyof typeof roles,
      rights: string,
      table: string,
    ) =>
      c.query(`GRANT ${rights} ON ${prefix}"${table}" TO "${roles[service]}"`);
    for (const service of ["web", "worker"] as const) {
      for (const table of [
        "execution_contexts",
        "thread_contexts",
        "profile_contexts",
      ])
        await grant(service, "SELECT, INSERT", table);
      await grant(service, "UPDATE(context_id)", "thread_contexts");
    }
    for (const table of [
      "execution_contexts",
      "thread_contexts",
      "profile_contexts",
    ])
      await grant("tools", "SELECT", table);
    await grant("web", "SELECT, INSERT", "feishu_identities");
    for (const table of ["feishu_login_challenges", "feishu_oauth_states"])
      await grant("web", "SELECT, INSERT, UPDATE", table);
    await grant(
      "worker",
      "SELECT, INSERT",
      "profile_preparation_policy_versions",
    );
    await grant("worker", "UPDATE(deadline)", "tasks");
    await grant(
      "worker",
      "SELECT, INSERT, UPDATE, DELETE",
      "acceptance_capacity",
    );
  });
  await grantFeishuIdentityAccess(pool, roles.feishu);
}
