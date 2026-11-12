import { Pool, type PoolClient } from "pg";

export function database(url = process.env.DATABASE_URL) {
  if (!url) throw new Error("DATABASE_URL required");
  return new Pool({ connectionString: url, max: 12 });
}
export async function lockOwner(
  c: PoolClient,
  owner: string,
): Promise<boolean> {
  return (
    await c.query<{ locked: boolean }>(
      "SELECT lock_task_owner($1::uuid) AS locked",
      [owner],
    )
  ).rows[0]!.locked;
}
export async function transaction<T>(
  pool: Pool,
  fn: (c: PoolClient) => Promise<T>,
): Promise<T> {
  const c = await pool.connect();
  try {
    await c.query("BEGIN");
    const result = await fn(c);
    await c.query("COMMIT");
    return result;
  } catch (e) {
    await c.query("ROLLBACK");
    throw e;
  } finally {
    c.release();
  }
}
