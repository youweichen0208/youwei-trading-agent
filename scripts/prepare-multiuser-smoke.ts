import { readFile, writeFile } from "node:fs/promises";
import { randomUUID } from "node:crypto";
import { database } from "../packages/server/src/db.js";
import { AuthStore } from "../packages/server/src/auth.js";
import { ExecutionContextStore } from "../packages/server/src/execution-context.js";
import {
  prepareProfiles,
  readProvisionSettings,
} from "../packages/hermes-adapter/src/provision.js";
const [settingsFile, outputRoot, recordFile] = process.argv.slice(2);
if (
  !settingsFile ||
  !outputRoot ||
  !recordFile ||
  !new URL(process.env.DATABASE_URL!).pathname.endsWith("_test")
)
  throw new Error("isolated_smoke_arguments_required");
const pool = database();
try {
  try {
    await readFile(recordFile);
    throw new Error("smoke_already_prepared");
  } catch (e) {
    if ((e as NodeJS.ErrnoException).code !== "ENOENT") throw e;
  }
  const owner = await new AuthStore(pool).register(
    `${randomUUID()}@smoke.invalid`,
    randomUUID(),
  );
  const context = await new ExecutionContextStore(pool).group(
    owner.id,
    "cli_smoke",
    "oc_smoke",
  );
  const settings = (await readProvisionSettings(settingsFile)) as Record<
    string,
    unknown
  >;
  const prepared = await prepareProfiles(
    pool,
    context.id,
    { ...settings, firstPort: 21000 },
    outputRoot,
  );
  await writeFile(
    recordFile,
    JSON.stringify({ ownerId: owner.id, contextId: context.id, ...prepared }),
    { flag: "wx", mode: 0o600 },
  );
  console.info("isolated_group_smoke_prepared");
} finally {
  await pool.end();
}
