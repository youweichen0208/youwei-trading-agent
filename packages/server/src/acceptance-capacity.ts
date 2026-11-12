import { randomUUID } from "node:crypto";
import type { Pool } from "pg";
import { transaction } from "./db.js";
export async function reserveAcceptance(pool: Pool, contextId: string) {
  return transaction(pool, async (c) => {
    await c.query(
      "SELECT pg_advisory_xact_lock(hashtext('research-root-capacity'))",
    );
    if (
      (
        await c.query(
          `SELECT 1 FROM tasks WHERE status IN ('running','stopping','unknown') LIMIT 1`,
        )
      ).rowCount ||
      (await c.query("SELECT 1 FROM acceptance_capacity")).rowCount ||
      (
        await c.query(
          "SELECT 1 FROM profile_instances WHERE lifecycle_managed AND state IN ('starting','ready','stopping','unknown') LIMIT 1",
        )
      ).rowCount
    )
      return undefined;
    const id = randomUUID();
    await c.query(
      "INSERT INTO acceptance_capacity(context_id,reservation_id,status) VALUES($1,$2,'running')",
      [contextId, id],
    );
    return id;
  });
}
/** Caller must have positive evidence that acceptance processes and containers exited. */
export async function finishAcceptance(
  pool: Pool,
  reservation: string,
  exitConfirmed: boolean,
) {
  if (exitConfirmed)
    await pool.query(
      "DELETE FROM acceptance_capacity WHERE reservation_id=$1",
      [reservation],
    );
  else
    await pool.query(
      "UPDATE acceptance_capacity SET status='unknown' WHERE reservation_id=$1",
      [reservation],
    );
}
