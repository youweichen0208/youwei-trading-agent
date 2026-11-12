import type { Pool } from "pg";
import { transaction } from "./db.js";

type MaintenanceResult =
  | { skipped: true }
  | {
      skipped: false;
      events: number;
      sessions: number;
      attempts: number;
      capabilities: number;
    };

// The fixed SQL policy owns deletion privileges; Worker can only invoke it.
export async function maintain(pool: Pool): Promise<MaintenanceResult> {
  return transaction(pool, async (c) => {
    await c.query("SET LOCAL statement_timeout='3s'");
    await c.query("SET LOCAL lock_timeout='250ms'");
    return (
      await c.query<{ result: MaintenanceResult }>(
        "SELECT maintain_transient_records() AS result",
      )
    ).rows[0]!.result;
  });
}
