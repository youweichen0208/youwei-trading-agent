import { execFile } from "node:child_process";
import { randomUUID } from "node:crypto";
import { chmod, mkdir, open, readFile, rename, rm } from "node:fs/promises";
import { dirname, join, resolve } from "node:path";
import { promisify } from "node:util";
import pg from "pg";
import { z } from "zod";
import {
  privatePreparationRoot,
  provisionSettings,
  readProvisionSettings,
} from "../packages/hermes-adapter/src/provision.js";
import { rotateAllOwners } from "../packages/hermes-adapter/src/rotation.js";

const run = promisify(execFile);
const services = ["alpha-lab-supervisor", "alpha-lab-worker"];

/** execFile/fs/systemctl reject with a system error carrying `code`. */
function errorCode(error: unknown): unknown {
  if (typeof error !== "object" || error === null || !("code" in error))
    return undefined;
  return error.code;
}

async function syncDirectory(path: string) {
  const file = await open(path, "r");
  try {
    await file.sync();
  } finally {
    await file.close();
  }
}
async function writePrivate(path: string, content: string) {
  const file = await open(path, "wx", 0o600);
  try {
    await file.writeFile(content);
    await file.sync();
  } finally {
    await file.close();
  }
  await syncDirectory(dirname(path));
}
/** Keep every attempt's pre-rotation settings; an identical rerun is a no-op. */
async function backupOnce(path: string, content: string) {
  try {
    await writePrivate(path, content);
  } catch (error) {
    if (errorCode(error) !== "EEXIST") throw error;
    if ((await readFile(path, "utf8")) !== content)
      throw new Error("rotation_backup_exists");
  }
}
/** Replace the policy file in place: same-directory temp file, fsync, rename. */
async function replaceSettings(path: string, content: string) {
  const temp = `${path}.${randomUUID()}.tmp`;
  try {
    await writePrivate(temp, content);
    await chmod(temp, 0o600);
    await rename(temp, path);
    await syncDirectory(dirname(path));
  } finally {
    await rm(temp, { force: true });
  }
}
async function runningProfiles() {
  try {
    return (
      await run("docker", ["ps", "-q", "--filter", "label=lab.alpha.profile"], {
        timeout: 10000,
      })
    ).stdout.trim();
  } catch {
    throw new Error("profile_containers_unverifiable");
  }
}
/** `systemctl is-active` exits non-zero for every inactive/unknown state. */
async function serviceActive(service: string) {
  try {
    await run("systemctl", ["is-active", "--quiet", service]);
    return true;
  } catch (error) {
    if (typeof errorCode(error) === "number") return false;
    throw new Error("rotation_services_unverifiable");
  }
}

const [
  settingsPath,
  nextSettingsPath,
  rootPath,
  lastPortArgument,
  backupRootArgument,
  ...flags
] = process.argv.slice(2);
const owners: string[] = [];
let apply = false;
let invalid = false;
for (let index = 0; index < flags.length; index += 1) {
  const flag = flags[index]!;
  const owner = flags[index + 1];
  if (flag === "--apply" && !apply) apply = true;
  else if (flag === "--owner" && owner && z.uuid().safeParse(owner).success) {
    owners.push(owner);
    index += 1;
  } else invalid = true;
}
if (
  !settingsPath ||
  !nextSettingsPath ||
  !rootPath ||
  !lastPortArgument ||
  !/^\d+$/.test(lastPortArgument) ||
  !backupRootArgument ||
  !process.env.DATABASE_URL ||
  invalid
) {
  console.error(
    "usage: DATABASE_URL=... pnpm hermes:rotate-model CURRENT_SETTINGS NEW_SETTINGS PRIVATE_ROOT LAST_PORT BACKUP_ROOT [--owner UUID ...] [--apply]",
  );
  process.exitCode = 1;
} else {
  const pool = new pg.Pool({ connectionString: process.env.DATABASE_URL });
  try {
    const current = provisionSettings.parse(
      await readProvisionSettings(settingsPath),
    );
    const next = provisionSettings.parse(
      await readProvisionSettings(nextSettingsPath),
    );
    // Only the model policy may move; ports, image and gateway stay bound to
    // the running deployment.
    if (
      current.image !== next.image ||
      current.gatewayOrigin !== next.gatewayOrigin ||
      current.firstPort !== next.firstPort
    )
      throw new Error("settings_incompatible");
    const lastPort = Number(lastPortArgument);
    const policy = await (async () => {
      try {
        return (
          await pool.query(
            "SELECT first_port,last_port FROM profile_preparation_policy_versions ORDER BY generation DESC LIMIT 1",
          )
        ).rows[0];
      } catch (error) {
        if (errorCode(error) === "42P01")
          throw new Error("preparation_policy_missing");
        throw error;
      }
    })();
    if (
      !policy ||
      policy.first_port !== current.firstPort ||
      policy.last_port !== lastPort
    )
      throw new Error("preparation_policy_changed");
    // A running supervisor would re-seal the old deployment hash, and a live
    // profile container still reads the specs being rewritten. A dry run is
    // read-only, so it reports these blockers instead of refusing.
    const containers = await runningProfiles().catch((error: Error) => {
      if (apply) throw error;
      console.error("dry-run: profile container state is unavailable");
      return "";
    });
    const active: string[] = [];
    for (const service of services) {
      const running = await serviceActive(service).catch((error: Error) => {
        if (apply) throw error;
        return false;
      });
      if (running) active.push(service);
    }
    if (apply && containers) throw new Error("profile_container_running");
    if (apply && active.length) throw new Error("rotation_services_running");
    if (!apply) {
      if (containers)
        console.error(
          "dry-run: apply would refuse while profile containers are running",
        );
      if (active.length)
        console.error(
          `dry-run: apply would refuse while ${active.join(", ")} are active`,
        );
    }
    const targets = owners.length
      ? owners
      : (
          await pool.query<{ owner_id: string }>(
            "SELECT owner_id FROM profile_preparation_jobs WHERE status='prepared' ORDER BY owner_id",
          )
        ).rows.map((row) => row.owner_id);
    const backupRoot = resolve(backupRootArgument);
    const plan = await rotateAllOwners(
      pool,
      targets,
      current,
      next,
      resolve(rootPath),
      lastPort,
      backupRoot,
      apply,
    );
    if (apply) {
      const nextContent = await readFile(nextSettingsPath, "utf8");
      const previousContent = await readFile(settingsPath, "utf8");
      // A rerun after artifacts but before settings must still publish them.
      if (previousContent !== nextContent) {
        const stamp = new Date().toISOString().replaceAll(":", "-");
        const directory = join(
          await privatePreparationRoot(backupRoot),
          stamp,
          "provider",
        );
        await mkdir(directory, { recursive: true, mode: 0o700 });
        await backupOnce(
          join(directory, "profile-settings.json"),
          previousContent,
        );
        await replaceSettings(settingsPath, nextContent);
        if ((await readFile(settingsPath, "utf8")) !== nextContent)
          throw new Error("settings_write_failed");
      }
    }
    console.log(
      JSON.stringify({
        owners: plan.owners,
        fromHash: plan.fromHash,
        toHash: plan.toHash,
        changed: plan.changed,
        backupRoot,
      }),
    );
  } catch (error) {
    // Error codes are actionable; raw messages can contain secrets or paths.
    const message = error instanceof Error ? error.message : "";
    if (/^[a-z][a-z0-9_]*$/.test(message))
      console.error(`rotation failed: ${message}`);
    else
      console.error(
        "Model rotation failed. Check private policy, database access and the backup root.",
      );
    process.exitCode = 1;
  } finally {
    await pool.end();
  }
}
