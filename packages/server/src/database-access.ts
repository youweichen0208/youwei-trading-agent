import { randomBytes } from "node:crypto";
import type { Pool } from "pg";
import { transaction } from "./db.js";
import { grantResearchAccess, researchTables } from "./research-access.js";

export const services = ["web", "worker", "tools"] as const;
export type Service = (typeof services)[number];
export type DatabaseCredentials = Record<
  Service,
  { name: string; password: string }
>;
const identifier = (value: string) => `"${value.replaceAll('"', '""')}"`;
const literal = (value: string) => `'${value.replaceAll("'", "''")}'`;
export function databaseCredentials(prefix: string): DatabaseCredentials {
  if (!/^[a-z][a-z0-9_]{2,35}$/.test(prefix) || prefix.startsWith("pg_"))
    throw new Error("invalid_database_role_prefix");
  return {
    web: { name: `${prefix}_web`, password: randomBytes(32).toString("hex") },
    worker: {
      name: `${prefix}_worker`,
      password: randomBytes(32).toString("hex"),
    },
    tools: {
      name: `${prefix}_tools`,
      password: randomBytes(32).toString("hex"),
    },
  };
}
const tables = [
  ...researchTables,
  "acceptance_capacity",
  "feishu_oauth_states",
  "execution_contexts",
  "thread_contexts",
  "profile_contexts",
  "feishu_identities",
  "feishu_login_challenges",
  "feishu_chats",
  "feishu_inbox",
  "feishu_outbox",
  "migrations",
  "users",
  "threads",
  "workspaces",
  "tasks",
  "executions",
  "messages",
  "task_events",
  "datasets",
  "results",
  "login_sessions",
  "profile_instances",
  "profile_sessions",
  "auth_attempts",
  "portfolios",
  "portfolio_snapshots",
  "watchlist",
  "tool_operations",
  "tool_calls",
  "task_artifacts",
  "tool_exit_receipts",
  "execution_audit",
  "profile_lifecycle_audit",
  "runtime_recoveries",
  "profile_preparation_jobs",
  "profile_preparation_policy",
  "profile_preparation_policy_versions",
  "profile_api_ports",
];

// Explicit deployment operation, never called by a running application. Refuse
// existing roles and mixed-use schemas; never change another app's role or ACL.
export async function provisionDatabaseAccess(
  pool: Pool,
  credentials: DatabaseCredentials,
) {
  if (
    Object.keys(credentials).sort().join() !== [...services].sort().join() ||
    services.some((service) => {
      const entry = credentials[service];
      return (
        !entry ||
        !/^[a-z][a-z0-9_]{2,50}$/.test(entry.name) ||
        entry.name.startsWith("pg_") ||
        !/^[a-f0-9]{64}$/.test(entry.password)
      );
    }) ||
    new Set(services.map((service) => credentials[service].name)).size !== 3
  )
    throw new Error("invalid_database_credentials");
  return transaction(pool, async (c) => {
    const context = (
      await c.query<{ schema: string; database: string }>(
        "SELECT current_schema() AS schema,current_database() AS database",
      )
    ).rows[0]!;
    if (!context.schema || context.schema.startsWith("pg_"))
      throw new Error("application_schema_required");
    await c.query(
      "SELECT pg_advisory_xact_lock(hashtext(current_database()),hashtext($1))",
      [`${context.schema}:alpha_access`],
    );
    const relations = (
      await c.query<{ relname: string; owned: boolean }>(
        `SELECT relname,relowner=(SELECT oid FROM pg_roles WHERE rolname=current_user) AS owned
       FROM pg_class WHERE relnamespace=$1::regnamespace AND relkind IN ('r','p','v','m','f')`,
        [context.schema],
      )
    ).rows;
    if (
      relations.length !== tables.length ||
      relations.some((row) => !row.owned || !tables.includes(row.relname))
    )
      throw new Error("dedicated_owned_application_schema_required");
    const functions = (
      await c.query<{ proname: string; owned: boolean }>(
        `SELECT proname,proowner=(SELECT oid FROM pg_roles WHERE rolname=current_user) AS owned
       FROM pg_proc WHERE pronamespace=$1::regnamespace`,
        [context.schema],
      )
    ).rows;
    const allowedFunctions = [
      "default_profile_context",
      "create_personal_context",
      "bind_personal_context",
      "reject_fact_mutation",
      "reject_execution_evidence_mutation",
      "audit_task_transition",
      "audit_tool_transition",
      "audit_profile_transition",
      "guard_runtime_recovery",
      "guard_profile_preparation",
      "audit_runtime_recovery",
      "lock_task_owner",
      "maintain_transient_records",
    ];
    const sequences = (
      await c.query<{ relname: string }>(
        "SELECT relname FROM pg_class WHERE relnamespace=$1::regnamespace AND relkind='S'",
        [context.schema],
      )
    ).rows;
    if (
      functions.length !== allowedFunctions.length ||
      functions.some(
        (fn) => !fn.owned || !allowedFunctions.includes(fn.proname),
      ) ||
      sequences.length !== 5 ||
      sequences.some(
        (sequence) =>
          ![
            "tasks_ordinal_seq",
            "portfolio_snapshots_ordinal_seq",
            "execution_audit_sequence_seq",
            "profile_lifecycle_audit_sequence_seq",
            "profile_preparation_policy_versions_generation_seq",
          ].includes(sequence.relname),
      )
    )
      throw new Error("dedicated_owned_application_schema_required");
    const schema = identifier(context.schema),
      db = identifier(context.database);
    const ready = await c.query(
      `SELECT 1 FROM ${schema}.migrations WHERE name='017_profile_preparation.sql'`,
    );
    if (!ready.rowCount) throw new Error("database_migration_required");
    const existing = await c.query(
      "SELECT 1 FROM pg_roles WHERE rolname=ANY($1::text[])",
      [services.map((service) => credentials[service].name)],
    );
    if (existing.rowCount) throw new Error("database_role_already_exists");
    // PUBLIC cannot provide an accidental route around the per-service grants.
    await c.query(`REVOKE ALL ON SCHEMA ${schema} FROM PUBLIC`);
    await c.query(`REVOKE ALL ON ALL TABLES IN SCHEMA ${schema} FROM PUBLIC`);
    await c.query(
      `REVOKE ALL ON ALL SEQUENCES IN SCHEMA ${schema} FROM PUBLIC`,
    );
    await c.query(
      `REVOKE ALL ON ALL FUNCTIONS IN SCHEMA ${schema} FROM PUBLIC`,
    );
    for (const service of services) {
      const role = identifier(credentials[service].name);
      await c.query(
        `CREATE ROLE ${role} LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS CONNECTION LIMIT 16 PASSWORD ${literal(credentials[service].password)}`,
      );
      await c.query(`GRANT CONNECT ON DATABASE ${db} TO ${role}`);
      await c.query(`GRANT USAGE ON SCHEMA ${schema} TO ${role}`);
      await c.query(
        `ALTER ROLE ${role} IN DATABASE ${db} SET search_path=pg_catalog,${schema},pg_temp`,
      );
      await c.query(
        `ALTER ROLE ${role} SET idle_in_transaction_session_timeout='60s'`,
      );
      await c.query(
        `GRANT EXECUTE ON FUNCTION ${schema}.lock_task_owner(uuid) TO ${role}`,
      );
    }
    const grant = async (service: Service, rights: string, table: string) => {
      await c.query(
        `GRANT ${rights} ON TABLE ${schema}.${identifier(table)} TO ${identifier(credentials[service].name)}`,
      );
    };
    for (const table of [
      "users",
      "login_sessions",
      "auth_attempts",
      "threads",
      "workspaces",
      "tasks",
      "messages",
      "profile_instances",
      "portfolios",
      "portfolio_snapshots",
      "watchlist",
    ])
      await grant("web", "SELECT, INSERT", table);
    for (const table of [
      "task_events",
      "task_artifacts",
      "datasets",
      "results",
      "execution_audit",
    ])
      await grant("web", "SELECT", table);
    for (const service of ["web", "worker"] as const) {
      for (const table of [
        "execution_contexts",
        "thread_contexts",
        "profile_contexts",
      ])
        await grant(service, "SELECT, INSERT", table);
    }
    for (const table of [
      "execution_contexts",
      "thread_contexts",
      "profile_contexts",
    ])
      await grant("tools", "SELECT", table);
    await grant("web", "SELECT, INSERT, UPDATE", "feishu_oauth_states");
    await grant("web", "SELECT, INSERT", "feishu_identities");
    await grant("web", "UPDATE(context_id)", "thread_contexts");
    await grant("worker", "UPDATE(context_id)", "thread_contexts");
    await grant("web", "SELECT, INSERT, UPDATE", "feishu_login_challenges");
    await grant("web", "INSERT", "task_events");
    await grant("web", "INSERT(owner_id)", "profile_preparation_jobs");
    for (const table of [
      "profile_preparation_jobs",
      "profile_preparation_policy",
      "profile_preparation_policy_versions",
      "profile_api_ports",
    ])
      await grant("worker", "SELECT, INSERT", table);
    await grant(
      "worker",
      "UPDATE(status,first_port,settings_hash,error_code)",
      "profile_preparation_jobs",
    );
    await grant("web", "DELETE", "login_sessions");
    await grant("web", "UPDATE(count,reset_at)", "auth_attempts");
    await grant("web", "UPDATE(title)", "threads");
    await grant("web", "UPDATE(version,revision,snapshot)", "workspaces");
    await grant(
      "web",
      "UPDATE(status,cancel_requested_at,cancel_reason,sequence,artifact_order)",
      "tasks",
    );
    await grant(
      "web",
      "SELECT(task_id,status), UPDATE(status)",
      "tool_operations",
    );

    for (const table of [
      "tasks",
      "profile_instances",
      "profile_sessions",
      "executions",
      "tool_operations",
      "tool_calls",
      "profile_lifecycle_audit",
    ])
      await grant("worker", "SELECT", table);
    for (const table of [
      "profile_sessions",
      "executions",
      "messages",
      "task_events",
    ])
      await grant("worker", "INSERT", table);
    await grant(
      "worker",
      "UPDATE(state,lifecycle_managed,deployment_hash,container_id,container_started_at,last_used_at,lifecycle_error)",
      "profile_instances",
    );
    await grant(
      "worker",
      "UPDATE(deadline,status,generation,lease_owner,lease_until,error_code,output,cancel_requested_at,cancel_reason,failure_code,next_attempt_at,sequence,stream_text)",
      "tasks",
    );
    await grant(
      "worker",
      "UPDATE(upstream_run_id,runtime_identity,runtime_container_id,runtime_container_started_at,runtime_deployment_hash,submission_started_at,terminal_receipt,tool_token_hash,tool_token_ciphertext,event_cursor,event_gap,event_closed)",
      "executions",
    );
    await grant("worker", "SELECT, INSERT", "runtime_recoveries");
    await grant(
      "worker",
      "UPDATE(phase,stopped_container_started_at,restarted_container_started_at,native_receipt)",
      "runtime_recoveries",
    );
    await grant(
      "worker",
      "SELECT, INSERT, UPDATE, DELETE",
      "acceptance_capacity",
    );
    await grant("worker", "UPDATE(status,error_code)", "tool_operations");
    await grant("worker", "UPDATE(response)", "tool_calls");
    await c.query(
      `GRANT EXECUTE ON FUNCTION ${schema}.maintain_transient_records() TO ${identifier(credentials.worker.name)}`,
    );

    for (const table of [
      "tasks",
      "tool_operations",
      "tool_calls",
      "datasets",
      "results",
      "task_artifacts",
    ])
      await grant("tools", "SELECT, INSERT", table);
    for (const table of [
      "workspaces",
      "profile_instances",
      "portfolio_snapshots",
    ])
      await grant("tools", "SELECT", table);
    for (const table of ["task_events", "tool_exit_receipts"])
      await grant("tools", "INSERT", table);
    await grant(
      "tools",
      "SELECT(task_id,profile_id,submission_started_at,tool_token_hash)",
      "executions",
    );
    await grant("tools", "UPDATE(sequence,artifact_order)", "tasks");
    await grant(
      "tools",
      "UPDATE(status,result,error_code,finished_at)",
      "tool_operations",
    );
    await grant("tools", "UPDATE(response)", "tool_calls");
    await grant("tools", "UPDATE(version,snapshot)", "workspaces");
    await grant("tools", "UPDATE(body,removed)", "task_artifacts");
    for (const [service, names] of [
      ["web", ["tasks_ordinal_seq", "portfolio_snapshots_ordinal_seq"]],
      ["tools", ["tasks_ordinal_seq"]],
    ] as const)
      for (const name of names)
        await c.query(
          `GRANT USAGE ON SEQUENCE ${schema}.${identifier(name)} TO ${identifier(credentials[service].name)}`,
        );
    await grantResearchAccess(
      c,
      context.schema,
      Object.fromEntries(
        services.map((service) => [service, credentials[service].name]),
      ) as Record<Service, string>,
    );
    return context;
  });
}
