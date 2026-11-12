import type { Pool, PoolClient } from "pg";

export const preparationRoles = [
  "research",
  "prediction",
  "portfolio",
] as const;
export type PreparationJob = {
  owner_id: string;
  request_id: string;
  status: "queued" | "preparing" | "prepared" | "blocked" | "manual";
  first_port: number | null;
  settings_hash: string | null;
  error_code: string | null;
};

/** Session lock survives transaction commits, and is released by process death. */
export async function withPreparationLock<T>(
  pool: Pool,
  ownerId: string,
  work: (client: PoolClient) => Promise<T>,
): Promise<T | undefined> {
  const c = await pool.connect();
  let locked = false;
  let discard = false;
  try {
    locked = (
      await c.query(
        "SELECT pg_try_advisory_lock(hashtext('profile_preparation_jobs'::regclass::oid::text),hashtext($1)) AS locked",
        [ownerId],
      )
    ).rows[0].locked;
    return locked ? await work(c) : undefined;
  } finally {
    if (locked) {
      try {
        const result = await c.query(
          "SELECT pg_advisory_unlock(hashtext('profile_preparation_jobs'::regclass::oid::text),hashtext($1)) AS unlocked",
          [ownerId],
        );
        discard = !result.rows[0].unlocked;
      } catch {
        discard = true;
      }
    }
    c.release(discard);
  }
}

/** Reserve individual ports atomically across automatic and manual preparation. */
export async function reservePreparation(
  c: PoolClient,
  ownerId: string,
  settingsHash: string,
  firstPort: number,
  lastPort?: number,
): Promise<PreparationJob> {
  await c.query("BEGIN");
  try {
    await c.query(
      "SELECT pg_advisory_xact_lock(hashtext('profile_api_ports'::regclass::oid::text),hashtext('allocate'))",
    );
    // Only explicit manual preparation enrolls users predating the job migration.
    if (lastPort === undefined)
      await c.query(
        "INSERT INTO profile_preparation_jobs(owner_id) VALUES($1) ON CONFLICT DO NOTHING",
        [ownerId],
      );
    const job = (
      await c.query<PreparationJob>(
        "SELECT * FROM profile_preparation_jobs WHERE owner_id=$1 FOR UPDATE",
        [ownerId],
      )
    ).rows[0];
    if (!job) throw new Error("preparation_job_missing");
    if (job.first_port !== null) {
      if (
        job.settings_hash !== settingsHash ||
        (lastPort === undefined &&
          (job.status !== "manual" || job.first_port !== firstPort)) ||
        (lastPort !== undefined && job.status === "manual")
      )
        throw new Error("preparation_binding_conflict");
      const ports = (
        await c.query<{ port: number; role: string }>(
          "SELECT port,role FROM profile_api_ports WHERE owner_id=$1 ORDER BY port",
          [ownerId],
        )
      ).rows;
      if (
        ports.length !== 3 ||
        ports.some(
          (p, i) =>
            p.port !== job.first_port! + i || p.role !== preparationRoles[i],
        )
      )
        throw new Error("preparation_ports_mismatch");
    } else {
      if (lastPort === undefined && job.status !== "queued")
        throw new Error("preparation_binding_conflict");
      const port =
        lastPort === undefined
          ? firstPort
          : (
              await c.query<{ port: number }>(
                `SELECT candidate AS port FROM generate_series($1::integer,$2::integer-2,3) candidate
         WHERE NOT EXISTS (SELECT 1 FROM profile_api_ports WHERE port BETWEEN candidate AND candidate+2)
         ORDER BY candidate LIMIT 1`,
                [firstPort, lastPort],
              )
            ).rows[0]?.port;
      if (port === undefined) throw new Error("port_pool_exhausted");
      for (const [i, role] of preparationRoles.entries())
        await c.query(
          "INSERT INTO profile_api_ports(port,owner_id,role) VALUES($1,$2,$3)",
          [port + i, ownerId, role],
        );
      job.first_port = port;
      job.settings_hash = settingsHash;
    }
    if (job.status !== "prepared" && job.status !== "manual") {
      job.status = lastPort === undefined ? "manual" : "preparing";
      await c.query(
        "UPDATE profile_preparation_jobs SET status=$2,first_port=$3,settings_hash=$4,error_code=NULL WHERE owner_id=$1",
        [ownerId, job.status, job.first_port, job.settings_hash],
      );
    }
    await c.query("COMMIT");
    return job;
  } catch (error) {
    await c.query("ROLLBACK");
    throw error;
  }
}
