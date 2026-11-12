import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { resolve } from "node:path";
import { setTimeout } from "node:timers/promises";
import pg from "pg";
import { AutoProfilePreparer } from "../packages/hermes-adapter/src/auto-preparation.js";
import { readProvisionSettings } from "../packages/hermes-adapter/src/provision.js";

const [settingsPath, outputPath, lastPort, mode, ownerId, ...extra] =
  process.argv.slice(2);
if (
  !settingsPath ||
  !outputPath ||
  !lastPort ||
  !/^\d+$/.test(lastPort) ||
  !process.env.DATABASE_URL ||
  extra.length ||
  (mode !== undefined && mode !== "--once" && mode !== "--retry") ||
  (mode === "--retry" ? !ownerId : ownerId !== undefined)
) {
  console.error(
    "usage: DATABASE_URL=... pnpm hermes:prepare-auto PRIVATE_SETTINGS PRIVATE_OUTPUT_ROOT LAST_PORT [--once | --retry OWNER_UUID]",
  );
  process.exitCode = 1;
} else {
  const pool = new pg.Pool({ connectionString: process.env.DATABASE_URL });
  const stop = new AbortController();
  process.once("SIGINT", () => stop.abort());
  process.once("SIGTERM", () => stop.abort());
  try {
    const preparer = await AutoProfilePreparer.open(
      pool,
      await readProvisionSettings(settingsPath),
      resolve(outputPath),
      Number(lastPort),
    );
    if (mode === "--retry") {
      const result = await preparer.prepare(ownerId!, true);
      console.log(JSON.stringify(result));
      if (result.status === "blocked" || result.status === "busy")
        process.exitCode = 1;
    } else {
      do {
        const results = await preparer.once();
        if (results.length) console.log(JSON.stringify(results));
        if (
          process.env.HERMES_AUTO_VERIFY === "1" &&
          process.env.HERMES_PUBLICATION_ROOT
        ) {
          const pending = (
            await pool.query(`SELECT j.owner_id FROM profile_preparation_jobs j WHERE j.status='prepared'
            AND NOT EXISTS(SELECT 1 FROM profile_instances p WHERE p.context_id=j.owner_id AND p.lifecycle_managed)
            ORDER BY j.created_at LIMIT 1`)
          ).rows[0];
          if (pending && !stop.signal.aborted) {
            try {
              await promisify(execFile)(
                process.execPath,
                [
                  "--import",
                  "tsx",
                  "scripts/verify-prepared-profile.ts",
                  resolve(outputPath, pending.owner_id),
                ],
                { timeout: 3700000, maxBuffer: 1048576 },
              );
              console.info("prepared_profile_verified_and_published");
            } catch {
              console.info(
                "profile_acceptance_waiting_or_requires_reconciliation",
              );
            }
          }
        }
        if (mode === "--once") {
          if (results.some((r) => r.status === "blocked")) process.exitCode = 1;
          break;
        }
        await setTimeout(5000, undefined, { signal: stop.signal }).catch(
          (error) => {
            if (!stop.signal.aborted) throw error;
          },
        );
      } while (!stop.signal.aborted);
    }
  } catch {
    // Input validation and filesystem errors can include secrets or private paths.
    console.error(
      "Automatic Profile preparation failed. Check private policy, database access and the bound output root.",
    );
    process.exitCode = 1;
  } finally {
    await pool.end();
  }
}
