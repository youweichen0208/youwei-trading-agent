import type { PoolClient } from "pg";

export const researchTables = [
  "forecasts",
  "forecast_evaluations",
  "forecast_checks",
  "forecast_reviews",
  "experience_lessons",
  "experience_versions",
  "experience_trials",
  "experience_trial_evaluations",
  "experience_batches",
  "experience_trial_arm_bindings",
  "experience_trial_arms",
  "experience_trial_checks",
  "research_evidence",
  "research_contexts",
  "research_collection_runs",
  "research_coverage",
  "research_settings",
  "market_scan_runs",
  "research_jobs",
  "research_schedule_runs",
  "research_checks",
  "notifications",
] as const;

/** Additive permissions for both initial provisioning and an existing deployment. */
export async function grantResearchAccess(
  c: PoolClient,
  schema: string,
  roles: { web: string; worker: string; tools: string; feishu?: string },
) {
  const quote = (name: string) => `"${name.replaceAll('"', '""')}"`;
  const grant = async (
    service: keyof typeof roles,
    rights: string,
    table: string,
  ) => {
    const role = roles[service];
    if (role)
      await c.query(
        `GRANT ${rights} ON TABLE ${quote(schema)}.${quote(table)} TO ${quote(role)}`,
      );
  };
  for (const service of ["web", "worker", "tools"] as const)
    for (const table of researchTables) await grant(service, "SELECT", table);
  for (const table of ["research_coverage", "research_settings"])
    await grant("web", "INSERT, UPDATE, DELETE", table);
  await grant("web", "DELETE", "watchlist");
  await grant("web", "UPDATE(read_at)", "notifications");
  for (const table of [
    "portfolios",
    "portfolio_snapshots",
    "watchlist",
    "datasets",
    "results",
    "threads",
    "workspaces",
    "task_artifacts",
  ])
    await grant("worker", "SELECT", table);
  for (const table of [
    "threads",
    "workspaces",
    "tasks",
    "results",
    "datasets",
    ...researchTables,
  ])
    await grant("worker", "INSERT", table);
  for (const table of [
    "forecast_checks",
    "experience_lessons",
    "experience_trial_checks",
    "research_coverage",
    "research_settings",
    "research_collection_runs",
    "research_jobs",
    "research_schedule_runs",
    "research_checks",
    "notifications",
  ])
    await grant("worker", "UPDATE", table);
  await grant("worker", "DELETE", "forecast_checks");
  await grant("worker", "UPDATE(source,title)", "threads");
  await grant("worker", "UPDATE(version,revision,snapshot)", "workspaces");
  await c.query(
    `GRANT USAGE ON SEQUENCE ${quote(schema)}.tasks_ordinal_seq TO ${quote(roles.worker)}`,
  );
  for (const table of ["watchlist", "portfolios"])
    await grant("tools", "SELECT", table);
  await grant("tools", "SELECT(request,runtime_identity)", "executions");
  for (const table of [
    "forecasts",
    "forecast_reviews",
    "experience_lessons",
    "experience_versions",
    "experience_trial_arms",
    "research_evidence",
    "research_contexts",
    "research_collection_runs",
    "research_coverage",
  ])
    await grant("tools", "INSERT", table);
  for (const table of [
    "experience_lessons",
    "research_collection_runs",
    "research_coverage",
  ])
    await grant("tools", "UPDATE", table);
  if (roles.feishu) {
    await grant("feishu", "SELECT, UPDATE(read_at)", "notifications");
    await grant("feishu", "SELECT", "research_settings");
    await grant("feishu", "SELECT, INSERT, UPDATE", "feishu_outbox");
  }
}
