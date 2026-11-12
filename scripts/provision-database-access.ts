import { mkdir, writeFile } from "node:fs/promises";
import { isAbsolute, join } from "node:path";
import { database } from "../packages/server/src/db.js";
import {
  databaseCredentials,
  provisionDatabaseAccess,
  services,
} from "../packages/server/src/database-access.js";

const [prefix, directory, ...extra] = process.argv.slice(2);
if (
  !prefix ||
  !directory ||
  !isAbsolute(directory) ||
  extra.length ||
  !process.env.DATABASE_URL
)
  throw new Error(
    "usage: DATABASE_URL=OWNER_URL pnpm db:access ROLE_PREFIX ABSOLUTE_NEW_PRIVATE_DIRECTORY",
  );
const credentials = databaseCredentials(prefix),
  pool = database();
try {
  // Preserve generated credentials even if COMMIT's acknowledgement is lost.
  // Never overwrite a directory or retry with new credentials for existing roles.
  await mkdir(directory, { mode: 0o700 });
  for (const service of services) {
    const url = new URL(process.env.DATABASE_URL);
    url.username = credentials[service].name;
    url.password = credentials[service].password;
    url.searchParams.delete("options");
    const connection = url.toString().replaceAll("'", "%27");
    await writeFile(
      join(directory, `${service}.env`),
      `DATABASE_URL='${connection}'\n`,
      { mode: 0o600, flag: "wx" },
    );
  }
  const state = join(directory, "state.json");
  await writeFile(
    state,
    JSON.stringify({
      status: "prepared_unverified",
      roles: services.map((service) => credentials[service].name),
    }) + "\n",
    { mode: 0o600, flag: "wx" },
  );
  const context = await provisionDatabaseAccess(pool, credentials);
  await writeFile(
    state,
    JSON.stringify({
      status: "provisioned",
      ...context,
      roles: services.map((service) => credentials[service].name),
    }) + "\n",
    { mode: 0o600 },
  );
  console.info("Database access provisioned; private connection files saved", {
    directory,
  });
} catch {
  console.error(
    "Database access preparation failed. Existing roles/directories are never overwritten. Preserve any private files for reconciliation; do not assume a failed acknowledgement means the database transaction did not commit.",
  );
  process.exitCode = 1;
} finally {
  await pool.end();
}
