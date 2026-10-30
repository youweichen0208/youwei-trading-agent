import pg from "pg";
import type { PoolClient } from "pg";

export function database(url: string) {
  const pool = new pg.Pool({ connectionString: url, max: 16, connectionTimeoutMillis: 5000, idleTimeoutMillis: 30000 });
  pool.on("error", () => console.error(JSON.stringify({ event: "database.connection_error" })));
  return pool;
}

export async function transaction<T>(pool: pg.Pool, work: (connection: PoolClient) => Promise<T>): Promise<T> {
  const connection = await pool.connect();
  try {
    await connection.query("BEGIN");
    const value = await work(connection);
    await connection.query("COMMIT");
    return value;
  } catch (error) {
    await connection.query("ROLLBACK");
    throw error;
  } finally { connection.release(); }
}

export async function lock(connection: PoolClient, key: string) {
  await connection.query("SELECT pg_advisory_xact_lock(hashtextextended($1,0))", [key]);
}
