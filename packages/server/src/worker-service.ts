import { setTimeout } from "node:timers/promises";
import { maintain } from "./maintenance.js";
import type { TaskWorker } from "./worker.js";

/** The production service loop; the caller owns process signals and the pool. */
export async function runWorkerService(
  worker: TaskWorker,
  signal: AbortSignal,
  reload?: () => Promise<void>,
  background?: (signal: AbortSignal) => Promise<unknown>,
) {
  const pool = worker.store.pool;
  let nextMaintenance = 0;
  let nextReload = 0;
  let nextBackground = 0;
  let backgroundRun: Promise<void> | undefined;
  console.info("Task Worker started", { workerId: worker.id });
  while (!signal.aborted) {
    if (background && !backgroundRun && Date.now() >= nextBackground) {
      nextBackground = Date.now() + 5000;
      // Source collection/settlement must not block interactive queue polling.
      backgroundRun = background(signal)
        .then(() => undefined)
        .catch(() => {
          console.error("Research scheduling failed; will retry");
        })
        .finally(() => {
          backgroundRun = undefined;
        });
    }
    if (reload && Date.now() >= nextReload) {
      nextReload = Date.now() + 5000;
      await reload();
    }
    if (Date.now() >= nextMaintenance) {
      nextMaintenance = Date.now() + 60_000;
      try {
        const result = await maintain(pool);
        if (
          !result.skipped &&
          (result.events ||
            result.sessions ||
            result.attempts ||
            result.capabilities)
        )
          console.info("Expired transient records cleaned", result);
      } catch {
        console.error("Maintenance failed; task processing will continue");
      }
    }
    if (signal.aborted) break;
    try {
      const owners = (
        await pool.query<{ owner_id: string }>(`SELECT t.owner_id FROM tasks t
        WHERE status IN ('queued','running','stopping','unknown') AND next_attempt_at<=now()
        AND (lease_until IS NULL OR lease_until<now())
        AND (status<>'queued' OR (
          NOT EXISTS(SELECT 1 FROM tasks a WHERE a.owner_id=t.owner_id AND a.role=t.role
            AND a.status IN ('running','stopping','unknown'))
          AND (parent_id IS NOT NULL OR NOT EXISTS(SELECT 1 FROM tasks a
            WHERE a.owner_id=t.owner_id AND a.parent_id IS NULL AND a.thread_id=t.thread_id AND a.ordinal<t.ordinal
            AND a.status NOT IN ('completed','failed','cancelled','interrupted')))))
        GROUP BY t.owner_id ORDER BY min(CASE WHEN status IN ('unknown','stopping') OR (status='running' AND lease_until IS NOT NULL) THEN 0
          WHEN status='queued' AND parent_id IS NULL THEN 2 ELSE 1 END),max(priority) DESC,min(next_attempt_at) LIMIT 120`)
      ).rows;
      for (
        let offset = 0;
        offset < owners.length && !signal.aborted;
        offset += 8
      ) {
        const batch = await Promise.allSettled(
          owners
            .slice(offset, offset + 8)
            .map((row) => worker.once(row.owner_id)),
        );
        if (batch.some((result) => result.status === "rejected"))
          console.error("Worker storage step failed; will retry");
      }
    } catch {
      console.error("Worker scan failed; will retry");
    }
    if (!signal.aborted) await setTimeout(500);
  }
  await backgroundRun;
}
