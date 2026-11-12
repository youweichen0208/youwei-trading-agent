import { readPublication } from "../packages/hermes-adapter/src/publication.js";
import { lstat, readFile } from "node:fs/promises";
import { ReloadingRegistry } from "../packages/hermes-adapter/src/client.js";
import { setTimeout as delay } from "node:timers/promises";
import { database } from "../packages/server/src/db.js";
import { ProfileSupervisor } from "../packages/server/src/profile-supervisor.js";
import {
  loadDeployments,
  DockerProfileDriver,
} from "../packages/hermes-adapter/src/lifecycle.js";

const [registryFile, ...directories] = process.argv.slice(2);
if (!registryFile || !directories.length)
  throw new Error(
    "usage: pnpm hermes:supervise VERIFIED_REGISTRY PREPARED_DIRECTORY...",
  );
process.env.RESEARCH_SINGLE_ROOT ??= "1";
const pool = database();
let closing = false;
process.once("SIGTERM", () => {
  closing = true;
});
process.once("SIGINT", () => {
  closing = true;
});
try {
  async function snapshot() {
    if (process.env.HERMES_PUBLICATION_POINTER) {
      const publication = await readPublication(
        process.env.HERMES_PUBLICATION_POINTER,
      );
      const registry = new ReloadingRegistry(
        publication.registry,
        publication.credentials,
      );
      await registry.reload();
      return loadDeployments(
        publication.registry,
        publication.directories,
        registry.resolve,
      );
    }
    let inputs = directories;
    const manifest = process.env.HERMES_DEPLOYMENT_DIRECTORIES;
    if (manifest) {
      const stat = await lstat(manifest);
      if (
        !stat.isFile() ||
        stat.isSymbolicLink() ||
        stat.mode & 0o077 ||
        stat.size > 1048576
      )
        throw new Error("private_deployment_manifest_required");
      const value: unknown = JSON.parse(await readFile(manifest, "utf8"));
      if (
        !Array.isArray(value) ||
        !value.length ||
        value.some((v) => typeof v !== "string")
      )
        throw new Error("invalid_deployment_manifest");
      inputs = value;
    }
    const registry = new ReloadingRegistry(
      registryFile,
      process.env.HERMES_REGISTRY_CREDENTIALS,
    );
    await registry.reload();
    return loadDeployments(registryFile!, inputs, registry.resolve);
  }
  let profiles = await snapshot();
  let supervisor = new ProfileSupervisor(
    pool,
    new DockerProfileDriver(profiles),
  );
  let nextReload = Date.now() + 5000;
  console.info("Profile supervisor started", { profiles: profiles.length });
  while (!closing) {
    if (Date.now() >= nextReload) {
      nextReload = Date.now() + 5000;
      try {
        const candidate = await snapshot();
        // Never silently drop supervision of existing containers or replace their sealed identity.
        if (
          profiles.some(
            (old) =>
              !candidate.some(
                (p) =>
                  p.profileId === old.profileId &&
                  p.deploymentHash === old.deploymentHash,
              ),
          )
        )
          throw new Error("active_deployment_replacement_refused");
        profiles = candidate;
        supervisor = new ProfileSupervisor(
          pool,
          new DockerProfileDriver(profiles),
        );
      } catch {
        console.error(
          "Supervisor update rejected; retaining last valid deployments",
        );
      }
    }
    for (let offset = 0; offset < profiles.length && !closing; offset += 4) {
      const batch = await Promise.allSettled(
        profiles
          .slice(offset, offset + 4)
          .map((profile) => supervisor.tick(profile)),
      );
      if (batch.some((result) => result.status === "rejected"))
        console.error("Profile check unavailable; supervision will retry");
    }
    if (!closing) await delay(1000);
  }
} catch {
  console.error(
    "Profile supervision refused. Check private prepared configuration, verified registry and database permissions.",
  );
  process.exitCode = 1;
} finally {
  await pool.end();
}
