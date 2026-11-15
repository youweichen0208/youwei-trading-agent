import { randomBytes, randomUUID } from "node:crypto";
import {
  link,
  lstat,
  mkdir,
  mkdtemp,
  open,
  readFile,
  readdir,
  rename,
  rm,
} from "node:fs/promises";
import { dirname, join } from "node:path";
import type { Pool } from "pg";
import { z } from "zod";
import {
  digest,
  json,
  ownerProfiles,
  contextOwner,
  preparationPaths,
  privatePreparationRoot,
  provisionSettings,
  readProvisionSettings,
  renderPreparation,
  writePreparation,
  type PreparationFile,
} from "./provision.js";
import {
  reservePreparation,
  withPreparationLock,
  type PreparationJob,
} from "./preparation-ledger.js";

/** Stable identity of a dedicated automatic root: path, inode and marker id. */
export async function preparationRootIdentity(root: string) {
  const marker = z
    .object({ id: z.uuid() })
    .strict()
    .parse(await readProvisionSettings(join(root, ".auto-preparer.json")));
  const stat = await lstat(root);
  return digest(json({ root, dev: stat.dev, ino: stat.ino, id: marker.id }));
}
/** The whole automatic policy, so a rotated model set needs a new generation. */
export function preparationSettingsHash(
  settings: z.infer<typeof provisionSettings>,
  lastPort: number,
  rootIdentity: string,
) {
  return digest(json({ settings, lastPort, rootIdentity }));
}

async function syncDirectory(path: string) {
  const file = await open(path, "r");
  try {
    await file.sync();
  } finally {
    await file.close();
  }
}
/** Publish a private file without ever replacing a previously published seed. */
async function publishPrivate(path: string, content: string) {
  const temp = `${path}.${randomUUID()}.tmp`;
  try {
    const file = await open(temp, "wx", 0o600);
    try {
      await file.writeFile(content);
      await file.sync();
    } finally {
      await file.close();
    }
    try {
      await link(temp, path);
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== "EEXIST") throw error;
    }
    await syncDirectory(dirname(path));
  } finally {
    await rm(temp, { force: true });
  }
}

async function validateFiles(
  directory: string,
  files: Map<string, PreparationFile>,
) {
  // Only an unpublished/just-published preparation is adoptable. Extra files,
  // symlinks, changed permissions or runtime activity require explicit review.
  const expectedDirs = new Set<string>([""]);
  for (const path of files.keys()) {
    let parent = dirname(path);
    while (parent !== ".") {
      expectedDirs.add(parent);
      parent = dirname(parent);
    }
  }
  const visit = async (relative: string) => {
    const path = join(directory, relative),
      stat = await lstat(path);
    if (stat.isSymbolicLink()) throw new Error("prepared_files_changed");
    if (stat.isDirectory() && expectedDirs.has(relative)) {
      if ((stat.mode & 0o777) !== 0o700)
        throw new Error("prepared_files_changed");
      for (const entry of await readdir(path))
        await visit(join(relative, entry));
    } else {
      const expected = files.get(relative);
      if (
        !expected ||
        !stat.isFile() ||
        (stat.mode & 0o777) !== expected.mode ||
        stat.size !== Buffer.byteLength(expected.content) ||
        (await readFile(path, "utf8")) !== expected.content
      )
        throw new Error("prepared_files_changed");
    }
  };
  await visit("");
  // Traversal also rejects unexpected files; explicit reads reject missing ones.
  for (const [relative, expected] of files)
    if (
      (await readFile(join(directory, relative), "utf8")) !== expected.content
    )
      throw new Error("prepared_files_changed");
}

const seedSchema = z
  .object({
    version: z.literal(1),
    requestId: z.uuid(),
    ownerId: z.uuid(),
    settingsHash: z.string(),
    firstPort: z.number().int(),
    profiles: z
      .array(
        z
          .object({
            id: z.uuid(),
            role: z.enum(["research", "prediction", "portfolio"]),
          })
          .strict(),
      )
      .length(3),
    keys: z.record(z.uuid(), z.string().regex(/^[a-f0-9]{64}$/)),
  })
  .strict();

export class AutoProfilePreparer {
  private constructor(
    readonly pool: Pool,
    private readonly settings: z.infer<typeof provisionSettings>,
    readonly root: string,
    readonly lastPort: number,
    private readonly rootIdentity: string,
    private readonly settingsHash: string,
  ) {}

  static async open(
    pool: Pool,
    input: unknown,
    outputRoot: string,
    lastPort: number,
  ) {
    const settings = provisionSettings.parse(input);
    z.number()
      .int()
      .min(settings.firstPort + 2)
      .max(65535)
      .parse(lastPort);
    const root = await privatePreparationRoot(outputRoot);
    // A dedicated automatic root cannot absorb prior manually prepared files.
    const markerPath = join(root, ".auto-preparer.json");
    try {
      await lstat(markerPath);
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error;
      const entries = await readdir(root);
      if (
        !entries.includes(".auto-preparer.json") &&
        entries.some((e) => !/^\.auto-preparer\.json\.[a-f0-9-]+\.tmp$/.test(e))
      )
        throw new Error("empty_automatic_root_required");
      await publishPrivate(markerPath, json({ id: randomUUID() }));
    }
    const rootIdentity = await preparationRootIdentity(root);
    const settingsHash = preparationSettingsHash(
      settings,
      lastPort,
      rootIdentity,
    );
    // The newest generation is authoritative; the legacy singleton is only
    // evidence for the first generation and must agree with it.
    let policy = (
      await pool.query(
        "SELECT * FROM profile_preparation_policy_versions ORDER BY generation DESC LIMIT 1",
      )
    ).rows[0];
    if (!policy) {
      const legacy = (
        await pool.query(
          "SELECT * FROM profile_preparation_policy WHERE singleton=true",
        )
      ).rows[0];
      if (
        legacy &&
        (legacy.root_identity !== rootIdentity ||
          legacy.settings_hash !== settingsHash ||
          legacy.first_port !== settings.firstPort ||
          legacy.last_port !== lastPort)
      )
        throw new Error("preparation_policy_changed");
      if (!legacy)
        await pool.query(
          "INSERT INTO profile_preparation_policy(root_identity,settings_hash,first_port,last_port) VALUES($1,$2,$3,$4) ON CONFLICT DO NOTHING",
          [rootIdentity, settingsHash, settings.firstPort, lastPort],
        );
      await pool.query(
        "INSERT INTO profile_preparation_policy_versions(root_identity,settings_hash,first_port,last_port) VALUES($1,$2,$3,$4)",
        [rootIdentity, settingsHash, settings.firstPort, lastPort],
      );
      policy = (
        await pool.query(
          "SELECT * FROM profile_preparation_policy_versions ORDER BY generation DESC LIMIT 1",
        )
      ).rows[0];
    }
    if (
      !policy ||
      policy.root_identity !== rootIdentity ||
      policy.settings_hash !== settingsHash ||
      policy.first_port !== settings.firstPort ||
      policy.last_port !== lastPort
    )
      throw new Error("preparation_policy_changed");
    return new AutoProfilePreparer(
      pool,
      settings,
      root,
      lastPort,
      rootIdentity,
      settingsHash,
    );
  }

  private async validateRoot() {
    await privatePreparationRoot(this.root);
    if ((await preparationRootIdentity(this.root)) !== this.rootIdentity)
      throw new Error("preparation_root_changed");
  }

  /** Bounded scan; a blocked job needs an explicit owner-specific retry. */
  async once() {
    const jobs = (
      await this.pool.query<{ owner_id: string }>(
        "SELECT owner_id FROM profile_preparation_jobs WHERE status IN ('queued','preparing') ORDER BY created_at LIMIT 100",
      )
    ).rows;
    const results = [];
    for (const job of jobs) results.push(await this.prepare(job.owner_id));
    return results;
  }

  async prepare(ownerId: string, retry = false) {
    ownerId = z.uuid().parse(ownerId).toLowerCase();
    const result = await withPreparationLock(this.pool, ownerId, async (c) => {
      const existing = (
        await c.query<PreparationJob>(
          "SELECT * FROM profile_preparation_jobs WHERE owner_id=$1",
          [ownerId],
        )
      ).rows[0];
      if (!existing) throw new Error("preparation_job_missing");
      if (
        existing.status === "prepared" ||
        existing.status === "manual" ||
        (existing.status === "blocked" && !retry)
      )
        return { ownerId, status: existing.status };
      try {
        await this.validateRoot();
        const profiles = await ownerProfiles(c, ownerId);
        const job = await reservePreparation(
          c,
          ownerId,
          this.settingsHash,
          this.settings.firstPort,
          this.lastPort,
        );
        const seedDir = join(this.root, ".auto-seeds");
        await mkdir(seedDir, { recursive: true, mode: 0o700 });
        await privatePreparationRoot(seedDir);
        const directory = join(this.root, ownerId);
        let published = true;
        try {
          await lstat(directory);
        } catch (error) {
          if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error;
          published = false;
        }
        const seedPath = join(seedDir, `${job.request_id}.json`);
        let seedExists = true;
        try {
          await lstat(seedPath);
        } catch (error) {
          if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error;
          seedExists = false;
        }
        if (!seedExists && published)
          throw new Error("preparation_seed_missing");
        if (!seedExists) {
          // Published once. Process death before/after this step never changes
          // keys that could already have appeared in a prepared directory.
          await publishPrivate(
            seedPath,
            json({
              version: 1,
              requestId: job.request_id,
              ownerId,
              settingsHash: this.settingsHash,
              firstPort: job.first_port,
              profiles,
              keys: Object.fromEntries(
                profiles.map((p) => [p.id, randomBytes(32).toString("hex")]),
              ),
            }),
          );
        }
        const seed = seedSchema.parse(await readProvisionSettings(seedPath));
        if (
          seed.ownerId !== ownerId ||
          seed.requestId !== job.request_id ||
          seed.settingsHash !== this.settingsHash ||
          seed.firstPort !== job.first_port ||
          json(seed.profiles) !== json(profiles) ||
          Object.keys(seed.keys).length !== 3 ||
          new Set(Object.values(seed.keys)).size !== 3 ||
          profiles.some((p) => !seed.keys[p.id])
        )
          throw new Error("preparation_seed_changed");
        const files = renderPreparation(
          profiles,
          await contextOwner(c, ownerId),
          { ...this.settings, firstPort: seed.firstPort },
          seed.keys,
          ownerId,
        );
        const manifest = files.get("preparation.json")!;
        manifest.content = json({
          ...JSON.parse(manifest.content),
          requestId: job.request_id,
          settingsHash: this.settingsHash,
        });
        if (!published) {
          const staging = await mkdtemp(
            join(this.root, `.auto-${job.request_id}-`),
          );
          try {
            await writePreparation(staging, files);
            // Flush content and directories before the atomic directory rename.
            for (const relative of files.keys()) {
              const file = await open(join(staging, relative), "r");
              try {
                await file.sync();
              } finally {
                await file.close();
              }
            }
            const dirs = new Set([...files.keys()].map((p) => dirname(p)));
            for (const dir of dirs) {
              let current = dir;
              while (current !== ".") {
                await syncDirectory(join(staging, current));
                current = dirname(current);
              }
            }
            await syncDirectory(staging);
            await this.validateRoot();
            await rename(staging, directory);
            await syncDirectory(this.root);
          } finally {
            await rm(staging, { recursive: true, force: true });
          }
        }
        await validateFiles(directory, files);
        await c.query(
          "UPDATE profile_preparation_jobs SET status='prepared',error_code=NULL WHERE owner_id=$1 AND status='preparing'",
          [ownerId],
        );
        return { ownerId, status: "prepared", ...preparationPaths(directory) };
      } catch (error) {
        const code =
          error instanceof Error && error.message === "port_pool_exhausted"
            ? "port_pool_exhausted"
            : "preparation_failed";
        // An uncertain commit must not downgrade an already prepared job.
        const marked = await c.query(
          "UPDATE profile_preparation_jobs SET status='blocked',error_code=$2 WHERE owner_id=$1 AND status IN ('queued','preparing','blocked') RETURNING status",
          [ownerId, code],
        );
        if (!marked.rowCount) {
          const actual = (
            await c.query<PreparationJob>(
              "SELECT * FROM profile_preparation_jobs WHERE owner_id=$1",
              [ownerId],
            )
          ).rows[0];
          if (actual?.status === "prepared" || actual?.status === "manual")
            return { ownerId, status: actual.status };
          throw new Error("preparation_state_unknown");
        }
        return { ownerId, status: "blocked", errorCode: code };
      }
    });
    return result ?? { ownerId, status: "busy" };
  }
}
