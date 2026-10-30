import { randomUUID, randomBytes } from "node:crypto";
import { database } from "../../apps/bff/src/database";
import { hashPassword } from "../../apps/bff/src/password";

export default async function setup() {
  const url = process.env.YTA_TEST_DATABASE_URL ?? "postgresql://yta_app:local-yta-app@127.0.0.1:54329/yta_test";
  if (!new URL(url).pathname.endsWith("_test")) throw new Error("E2E requires a dedicated _test database");
  const pool = database(url); const id = randomUUID();
  process.env.YTA_E2E_EMAIL = `browser-${id}@local.yta`;
  process.env.YTA_E2E_PASSWORD = randomBytes(24).toString("base64url");
  try {
    await pool.query("INSERT INTO yta.users(id,email) VALUES($1,$2)", [id, process.env.YTA_E2E_EMAIL]);
    await pool.query("INSERT INTO yta.login_credentials(owner_id,password_hash) VALUES($1,$2)", [id, await hashPassword(process.env.YTA_E2E_PASSWORD)]);
  } finally { await pool.end(); }
}
