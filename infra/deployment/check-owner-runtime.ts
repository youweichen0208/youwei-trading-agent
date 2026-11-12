/** Read-only diagnostic: every Profile for an owner must resolve in the live registry. */
import { strict as assert } from "node:assert";
import pg from "pg";
import { loadRegistry } from "../../packages/hermes-adapter/src/client.js";
const owner = process.argv[2];
assert.match(owner ?? "", /^[a-f0-9-]{36}$/);
const pool = new pg.Pool({ connectionString: process.env.DATABASE_URL });
try {
  const resolve = await loadRegistry(process.env.HERMES_PROFILE_REGISTRY);
  const { rows } = await pool.query(
    "SELECT id,role,state,lifecycle_managed FROM profile_instances WHERE owner_id=$1 ORDER BY role",
    [owner],
  );
  assert.equal(rows.length, 3);
  const status = rows.map((row) => ({
    role: row.role,
    state: row.state,
    runtimeConnected: !!resolve(row.id),
    lifecycleManaged: row.lifecycle_managed,
  }));
  console.log(JSON.stringify(status));
  assert.ok(
    status.every((row) => row.runtimeConnected && row.lifecycleManaged),
    "profile_unavailable: owner Profiles missing from verified runtime registry",
  );
} finally {
  await pool.end();
}
