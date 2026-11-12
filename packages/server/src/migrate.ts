import type { Pool } from "pg";
import { createHash } from "node:crypto";
import { readFile, readdir } from "node:fs/promises";
import { transaction } from "./db.js";
export async function migrate(pool: Pool) {
  await transaction(pool, async (c) => {
    await c.query("SELECT pg_advisory_xact_lock(hashtext('alpha_migrations'))");
    await c.query(
      "CREATE TABLE IF NOT EXISTS migrations(name text PRIMARY KEY, checksum text NOT NULL)",
    );
    const dir = new URL("../../../infra/migrations/", import.meta.url);
    for (const name of (await readdir(dir))
      .filter((n) => n.endsWith(".sql"))
      .sort()) {
      const sql = await readFile(new URL(name, dir), "utf8");
      const checksum = createHash("sha256").update(sql).digest("hex");
      const existing = (
        await c.query("SELECT checksum FROM migrations WHERE name=$1", [name])
      ).rows[0];
      if (existing) {
        if (existing.checksum !== checksum)
          throw new Error("migration_changed");
        continue;
      }
      await c.query(sql);
      await c.query("INSERT INTO migrations VALUES($1,$2)", [name, checksum]);
    }
  });
}
