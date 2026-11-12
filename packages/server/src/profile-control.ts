import type { Pool, PoolClient } from "pg";
import { lockOwner } from "./db.js";

export async function profileTransaction<T>(
  c: PoolClient,
  owner: string,
  fn: () => Promise<T>,
) {
  await c.query("BEGIN");
  try {
    if (!(await lockOwner(c, owner))) throw new Error("profile_owner_missing");
    const result = await fn();
    await c.query("COMMIT");
    return result;
  } catch (error) {
    await c.query("ROLLBACK");
    throw error;
  }
}

export async function withProfileControl<T>(
  pool: Pool,
  profileId: string,
  required: boolean,
  operation: (c: PoolClient) => Promise<T>,
): Promise<T | undefined> {
  const c = await pool.connect();
  let locked = false;
  try {
    locked = (
      await c.query(
        "SELECT pg_try_advisory_lock(hashtext('profile_instances'::regclass::oid::text),hashtext($1)) AS locked",
        [`alpha_profile:${profileId}`],
      )
    ).rows[0].locked;
    if (!locked) {
      if (required) throw new Error("profile_control_busy");
      return;
    }
    return await operation(c);
  } finally {
    let discard = false;
    if (locked)
      await c
        .query(
          "SELECT pg_advisory_unlock(hashtext('profile_instances'::regclass::oid::text),hashtext($1))",
          [`alpha_profile:${profileId}`],
        )
        .catch(() => {
          discard = true;
        });
    c.release(discard);
  }
}
