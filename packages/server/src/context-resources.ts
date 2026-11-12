import type { Pool, PoolClient } from "pg";
import { DomainError } from "./tasks.js";
/** Facts remain immutable; their producing task supplies the context provenance. */
export async function assertContextResources(
  db: Pick<Pool | PoolClient, "query">,
  owner: string,
  thread: string,
  kind: "datasets" | "results",
  ids: readonly string[],
) {
  if (!ids.length) return;
  const source =
    kind === "datasets"
      ? "JOIN tool_operations op ON op.id=r.operation_id JOIN tasks source ON source.id=op.task_id"
      : "JOIN tasks source ON source.id=r.task_id";
  const count = (
    await db.query(
      `SELECT r.id FROM ${kind} r ${source}
    JOIN thread_contexts sc ON sc.thread_id=source.thread_id AND sc.owner_id=r.owner_id
    JOIN thread_contexts tc ON tc.context_id=sc.context_id AND tc.owner_id=sc.owner_id
    WHERE r.owner_id=$1 AND tc.thread_id=$2 AND r.id=ANY($3::uuid[])`,
      [owner, thread, ids],
    )
  ).rowCount;
  if (count !== new Set(ids).size) throw new DomainError("not_found", 404);
}
