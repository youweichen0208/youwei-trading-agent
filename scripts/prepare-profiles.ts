import { resolve } from "node:path";
import pg from "pg";
import {
  prepareProfiles,
  readProvisionSettings,
} from "../packages/hermes-adapter/src/provision.js";

const [ownerId, settingsPath, outputPath] = process.argv.slice(2);
if (!ownerId || !settingsPath || !outputPath || !process.env.DATABASE_URL)
  throw new Error(
    "usage: DATABASE_URL=... pnpm hermes:prepare OWNER_UUID PRIVATE_SETTINGS PRIVATE_OUTPUT_DIR",
  );
const pool = new pg.Pool({ connectionString: process.env.DATABASE_URL });
try {
  const prepared = await prepareProfiles(
    pool,
    ownerId,
    await readProvisionSettings(settingsPath),
    resolve(outputPath),
  );
  console.log(JSON.stringify(prepared));
} catch {
  // Schema errors can echo a credential's value; no input/error dump at this boundary.
  console.error(
    "Profile preparation failed. Check private settings, owner and output directory; existing profiles are never overwritten.",
  );
  process.exitCode = 1;
} finally {
  await pool.end();
}
