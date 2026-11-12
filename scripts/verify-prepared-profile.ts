import { publishDeployment } from "../packages/hermes-adapter/src/publication.js";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { readFile, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { z } from "zod";
import { database } from "../packages/server/src/db.js";
import {
  reserveAcceptance,
  finishAcceptance,
} from "../packages/server/src/acceptance-capacity.js";
const directory = process.argv[2];
if (!directory) throw new Error("prepared_directory_required");
const manifest = JSON.parse(
  await readFile(resolve(directory, "preparation.json"), "utf8"),
);
const contextId = z.uuid().parse(manifest.contextId ?? manifest.ownerId);
const pool = database();
try {
  const reservation = await reserveAcceptance(pool, contextId);
  if (!reservation) {
    console.info("acceptance_waiting_for_capacity");
    process.exitCode = 2;
  } else {
    let confirmed = false;
    try {
      await promisify(execFile)(
        process.env.ACCEPTANCE_PYTHON ?? "python3",
        ["infra/deployment/check-profiles.py", resolve(directory)],
        { timeout: 3600000, maxBuffer: 1048576 },
      );
      confirmed = true;
      if (process.env.HERMES_PUBLICATION_ROOT)
        await publishDeployment(directory, process.env.HERMES_PUBLICATION_ROOT);
      console.info("acceptance_verified");
    } catch (error) {
      const failure = error as {
        stdout?: string;
        stderr?: string;
        code?: string | number;
      };
      await writeFile(
        resolve(directory, "acceptance-process-failure.json"),
        JSON.stringify({
          code: failure.code,
          stdout: failure.stdout,
          stderr: failure.stderr,
        }),
        { mode: 0o600 },
      );
      console.error("acceptance_failed_requires_reconciliation");
      process.exitCode = 1;
    } finally {
      await finishAcceptance(pool, reservation, confirmed);
    }
  }
} finally {
  await pool.end();
}
