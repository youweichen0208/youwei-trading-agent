import { mkdir, writeFile, access } from "node:fs/promises";
import { randomBytes, randomUUID } from "node:crypto";
import { fileURLToPath } from "node:url";
import { readConfig } from "../src/config.js";
import { database } from "../src/database.js";
import { hashPassword } from "../src/password.js";

const config = readConfig();
if (config.YTA_ENV !== "development" || config.YTA_DATA_MODE !== "REPLAY") throw new Error("Bootstrap is restricted to development / REPLAY");
const pool = database(config.YTA_DATABASE_URL);
const email = "youwei@local.yta";
const output = new URL("../../../.local/login.txt", import.meta.url);
try {
  const existing = await pool.query("SELECT c.id FROM yta.login_credentials c JOIN yta.users u ON u.id=c.owner_id WHERE u.email=$1", [email]);
  if (existing.rowCount) {
    await access(output);
    console.log("Local login already exists; credentials remain in .local/login.txt");
  } else {
    const owner = randomUUID();
    const password = randomBytes(24).toString("base64url");
    await pool.query("INSERT INTO yta.users(id,email) VALUES ($1,$2) ON CONFLICT DO NOTHING", [owner, email]);
    await pool.query("INSERT INTO yta.login_credentials(owner_id,password_hash) SELECT id,$2 FROM yta.users WHERE email=$1", [email, await hashPassword(password)]);
    await mkdir(new URL("../../../.local/", import.meta.url), { recursive: true, mode: 0o700 });
    await writeFile(output, `Local REPLAY login only\nEmail: ${email}\nPassword: ${password}\n`, { mode: 0o600, flag: "wx" });
    console.log(`Created local login; read credentials from ${fileURLToPath(output)} (not printed)`);
  }
} finally { await pool.end(); }
