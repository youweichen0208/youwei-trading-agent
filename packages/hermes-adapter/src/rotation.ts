import { randomUUID } from "node:crypto";
import {
  chmod,
  lstat,
  mkdir,
  open,
  readFile,
  rename,
  rm,
} from "node:fs/promises";
import { dirname, join } from "node:path";
import type { Pool } from "pg";
import { z } from "zod";
import {
  contextOwner,
  digest,
  json,
  ownerProfiles,
  privatePreparationRoot,
  provisionSettings,
  renderPreparation,
} from "./provision.js";
import {
  preparationRootIdentity,
  preparationSettingsHash,
} from "./auto-preparation.js";
import type { PreparationJob } from "./preparation-ledger.js";

export type RotatedOwner = {
  ownerId: string;
  fromHash: string;
  toHash: string;
  changed: boolean;
  backupDirectory: string | null;
};

const hash = z.string().regex(/^[a-f0-9]{64}$/);
const profileSchema = z.object({
  id: z.uuid(),
  role: z.enum(["research", "prediction", "portfolio"]),
});
const manifestSchema = z.object({
  ownerId: z.uuid(),
  contextId: z.uuid().optional(),
  requestId: z.uuid(),
  settingsHash: hash,
  fingerprints: z.record(z.uuid(), hash),
  profiles: z.array(profileSchema).length(3),
});
const credentialsSchema = z.object({ apiKey: hash });
const seedSchema = z.object({
  ownerId: z.uuid(),
  requestId: z.uuid(),
  settingsHash: hash,
  firstPort: z.number().int(),
});

async function syncDirectory(path: string) {
  const file = await open(path, "r");
  try {
    await file.sync();
  } finally {
    await file.close();
  }
}
/** Read a published artifact; any deviation from a regular file is the error. */
async function readArtifact(path: string, code: string) {
  try {
    if (!(await lstat(path)).isFile()) throw new Error(code);
    return await readFile(path, "utf8");
  } catch {
    throw new Error(code);
  }
}
/** Read a 0600 artifact without following symlinks or widening permissions. */
async function readPrivate(path: string, code: string) {
  try {
    const stat = await lstat(path);
    if (!stat.isFile() || stat.mode & 0o077 || stat.size > 65536)
      throw new Error(code);
    return await readFile(path, "utf8");
  } catch {
    throw new Error(code);
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
/** Replace an artifact in place: same-directory temp file, fsync, then rename. */
async function publishAtomic(path: string, content: string, mode: number) {
  const temp = `${path}.${randomUUID()}.tmp`;
  try {
    await writePrivate(temp, content);
    await chmod(temp, mode);
    await rename(temp, path);
    await syncDirectory(dirname(path));
  } finally {
    await rm(temp, { force: true });
  }
}

/**
 * Rewrite one owner's prepared artifacts for a new model generation. Memory
 * (`home/`) and the published compose/registry documents are never touched:
 * ports, keys and container topology stay bound to the preparation generation.
 */
export async function rotateOwnerPreparation(
  pool: Pool,
  ownerId: string,
  settings: z.infer<typeof provisionSettings>,
  root: string,
  lastPort: number,
  backupRoot: string,
): Promise<RotatedOwner> {
  const owner = z.uuid().parse(ownerId).toLowerCase();
  const next = provisionSettings.parse(settings);
  z.number()
    .int()
    .min(next.firstPort + 2)
    .max(65535)
    .parse(lastPort);
  const rootPath = await privatePreparationRoot(root);
  const directory = join(rootPath, owner);
  const manifestPath = join(directory, "preparation.json");
  const job = (
    await pool.query<PreparationJob>(
      "SELECT * FROM profile_preparation_jobs WHERE owner_id=$1",
      [owner],
    )
  ).rows[0];
  if (
    !job ||
    (job.status !== "prepared" && job.status !== "manual") ||
    job.first_port === null
  )
    throw new Error("preparation_job_not_ready");
  const firstPort = job.first_port;
  // A running task still reads the current specs; rotation waits for it.
  const busy = await pool.query(
    `SELECT 1 FROM tasks t JOIN thread_contexts tc ON tc.thread_id=t.thread_id AND tc.owner_id=t.owner_id
     WHERE tc.context_id=$1 AND t.status IN ('queued','running','stopping','unknown') LIMIT 1`,
    [owner],
  );
  if (busy.rowCount) throw new Error("profile_busy");
  const dataOwner = await contextOwner(pool, owner);
  const profiles = await ownerProfiles(pool, owner);
  const manifestRaw = await readPrivate(
    manifestPath,
    "preparation_manifest_changed",
  );
  let manifest: z.infer<typeof manifestSchema>;
  try {
    manifest = manifestSchema.parse(JSON.parse(manifestRaw));
  } catch {
    throw new Error("preparation_manifest_changed");
  }
  if (
    (manifest.contextId ?? manifest.ownerId) !== owner ||
    manifest.ownerId !== dataOwner ||
    manifest.requestId !== job.request_id ||
    Object.keys(manifest.fingerprints).length !== 3 ||
    json([...profiles].sort((a, b) => a.role.localeCompare(b.role))) !==
      json([...manifest.profiles].sort((a, b) => a.role.localeCompare(b.role)))
  )
    throw new Error("preparation_manifest_changed");
  const current = new Map<
    string,
    { runtime: string; credentials: string; apiKey: string }
  >();
  for (const profile of manifest.profiles) {
    const profileDirectory = join(directory, "profiles", profile.id);
    const runtime = await readArtifact(
      join(profileDirectory, "runtime.json"),
      "prepared_files_changed",
    );
    // A rotated directory must be exactly the published generation; drift is
    // reviewed by an operator instead of being silently overwritten.
    if (digest(runtime) !== manifest.fingerprints[profile.id])
      throw new Error("prepared_files_changed");
    const credentials = await readArtifact(
      join(profileDirectory, "credentials.json"),
      "prepared_files_changed",
    );
    let apiKey: string;
    try {
      apiKey = credentialsSchema.parse(JSON.parse(credentials)).apiKey;
    } catch {
      throw new Error("prepared_files_changed");
    }
    current.set(profile.id, { runtime, credentials, apiKey });
  }
  const seedPath = join(rootPath, ".auto-seeds", `${manifest.requestId}.json`);
  let seedRaw: string | null = null;
  try {
    await lstat(seedPath);
    seedRaw = await readPrivate(seedPath, "preparation_seed_changed");
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error;
  }
  if (seedRaw !== null) {
    let seed: z.infer<typeof seedSchema>;
    try {
      seed = seedSchema.parse(JSON.parse(seedRaw));
    } catch {
      throw new Error("preparation_seed_changed");
    }
    if (
      seed.ownerId !== owner ||
      seed.requestId !== manifest.requestId ||
      seed.firstPort !== firstPort
    )
      throw new Error("preparation_seed_changed");
  }
  const toHash = preparationSettingsHash(
    next,
    lastPort,
    await preparationRootIdentity(rootPath),
  );
  // Group contexts keep their allocated port triple and rendered context
  // binding; only the model policy comes from the new generation.
  const files = renderPreparation(
    profiles,
    dataOwner,
    { ...next, firstPort },
    Object.fromEntries(
      manifest.profiles.map((profile) => [
        profile.id,
        current.get(profile.id)!.apiKey,
      ]),
    ),
    owner,
  );
  const renderedManifest = files.get("preparation.json")!;
  const manifestContent = json({
    ...JSON.parse(renderedManifest.content),
    requestId: manifest.requestId,
    settingsHash: toHash,
  });
  const seedContent =
    seedRaw === null
      ? null
      : json({ ...JSON.parse(seedRaw), settingsHash: toHash });
  const desired = manifest.profiles.map((profile) => ({
    id: profile.id,
    runtime: files.get(join("profiles", profile.id, "runtime.json"))!,
    credentials: files.get(join("profiles", profile.id, "credentials.json"))!,
  }));
  if (
    manifest.settingsHash === toHash &&
    manifestContent === manifestRaw &&
    seedContent === seedRaw &&
    desired.every(
      (file) =>
        file.runtime.content === current.get(file.id)!.runtime &&
        file.credentials.content === current.get(file.id)!.credentials,
    )
  )
    return {
      ownerId: owner,
      fromHash: manifest.settingsHash,
      toHash,
      changed: false,
      backupDirectory: null,
    };
  // Backup first: a rewrite is only attempted once the exact originals exist.
  const backupDirectory = join(
    await privatePreparationRoot(backupRoot),
    new Date().toISOString().replaceAll(":", "-"),
    owner,
  );
  await mkdir(backupDirectory, { recursive: true, mode: 0o700 });
  for (const profile of manifest.profiles) {
    await writePrivate(
      join(backupDirectory, `runtime-${profile.id}.json`),
      current.get(profile.id)!.runtime,
    );
    await writePrivate(
      join(backupDirectory, `credentials-${profile.id}.json`),
      current.get(profile.id)!.credentials,
    );
  }
  await writePrivate(join(backupDirectory, "preparation.json"), manifestRaw);
  if (seedRaw !== null)
    await writePrivate(join(backupDirectory, "seed.json"), seedRaw);
  for (const file of desired) {
    const profileDirectory = join(directory, "profiles", file.id);
    await publishAtomic(
      join(profileDirectory, "runtime.json"),
      file.runtime.content,
      file.runtime.mode,
    );
    await publishAtomic(
      join(profileDirectory, "credentials.json"),
      file.credentials.content,
      file.credentials.mode,
    );
  }
  await publishAtomic(manifestPath, manifestContent, renderedManifest.mode);
  if (seedContent !== null) await publishAtomic(seedPath, seedContent, 0o600);
  // Read back what is on disk; a partial rewrite must never look successful.
  const verifiedRaw = await readPrivate(
    manifestPath,
    "rotation_verification_failed",
  );
  let verified: z.infer<typeof manifestSchema>;
  try {
    verified = manifestSchema.parse(JSON.parse(verifiedRaw));
  } catch {
    throw new Error("rotation_verification_failed");
  }
  if (
    verifiedRaw !== manifestContent ||
    verified.settingsHash !== toHash ||
    verified.requestId !== manifest.requestId
  )
    throw new Error("rotation_verification_failed");
  for (const file of desired) {
    const profileDirectory = join(directory, "profiles", file.id);
    const runtime = await readFile(
      join(profileDirectory, "runtime.json"),
      "utf8",
    );
    if (
      runtime !== file.runtime.content ||
      verified.fingerprints[file.id] !== digest(runtime) ||
      (await readFile(join(profileDirectory, "credentials.json"), "utf8")) !==
        file.credentials.content
    )
      throw new Error("rotation_verification_failed");
  }
  if (
    seedContent !== null &&
    (await readFile(seedPath, "utf8")) !== seedContent
  )
    throw new Error("rotation_verification_failed");
  return {
    ownerId: owner,
    fromHash: manifest.settingsHash,
    toHash,
    changed: true,
    backupDirectory,
  };
}

/** Revert a rotation to the exact artifacts recorded in its backup directory. */
export async function restoreOwnerPreparation(
  root: string,
  ownerId: string,
  backupDirectory: string,
): Promise<void> {
  const owner = z.uuid().parse(ownerId).toLowerCase();
  const rootPath = await privatePreparationRoot(root);
  const directory = join(rootPath, owner);
  const load = async (name: string) => {
    try {
      const stat = await lstat(join(backupDirectory, name));
      if (!stat.isFile() || stat.mode & 0o077 || stat.size > 65536)
        throw new Error("rotation_backup_invalid");
      return await readFile(join(backupDirectory, name), "utf8");
    } catch {
      throw new Error("rotation_backup_invalid");
    }
  };
  const manifestRaw = await load("preparation.json");
  let manifest: z.infer<typeof manifestSchema>;
  try {
    manifest = manifestSchema.parse(JSON.parse(manifestRaw));
  } catch {
    throw new Error("rotation_backup_invalid");
  }
  if ((manifest.contextId ?? manifest.ownerId) !== owner)
    throw new Error("rotation_backup_invalid");
  const files = new Map<string, { runtime: string; credentials: string }>();
  for (const profile of manifest.profiles) {
    try {
      if (!(await lstat(join(directory, "profiles", profile.id))).isDirectory())
        throw new Error("rotation_backup_invalid");
    } catch {
      throw new Error("rotation_backup_invalid");
    }
    const runtime = await load(`runtime-${profile.id}.json`);
    // The backup must describe itself: fingerprints bind the recorded specs.
    if (digest(runtime) !== manifest.fingerprints[profile.id])
      throw new Error("rotation_backup_invalid");
    files.set(profile.id, {
      runtime,
      credentials: await load(`credentials-${profile.id}.json`),
    });
  }
  let seed: string | null = null;
  try {
    await lstat(join(backupDirectory, "seed.json"));
    seed = await load("seed.json");
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error;
  }
  if (seed !== null) {
    let parsed: z.infer<typeof seedSchema>;
    try {
      parsed = seedSchema.parse(JSON.parse(seed));
    } catch {
      throw new Error("rotation_backup_invalid");
    }
    if (
      parsed.ownerId !== owner ||
      parsed.requestId !== manifest.requestId ||
      parsed.settingsHash !== manifest.settingsHash
    )
      throw new Error("rotation_backup_invalid");
  }
  for (const profile of manifest.profiles) {
    const file = files.get(profile.id)!;
    const profileDirectory = join(directory, "profiles", profile.id);
    await publishAtomic(
      join(profileDirectory, "runtime.json"),
      file.runtime,
      0o444,
    );
    await publishAtomic(
      join(profileDirectory, "credentials.json"),
      file.credentials,
      0o444,
    );
  }
  await publishAtomic(join(directory, "preparation.json"), manifestRaw, 0o600);
  if (seed !== null)
    await publishAtomic(
      join(rootPath, ".auto-seeds", `${manifest.requestId}.json`),
      seed,
      0o600,
    );
  // Verify the restored generation against its own recorded fingerprints.
  if (
    (await readFile(join(directory, "preparation.json"), "utf8")) !==
    manifestRaw
  )
    throw new Error("rotation_backup_invalid");
  for (const profile of manifest.profiles) {
    const runtime = await readFile(
      join(directory, "profiles", profile.id, "runtime.json"),
      "utf8",
    );
    if (
      runtime !== files.get(profile.id)!.runtime ||
      digest(runtime) !== manifest.fingerprints[profile.id]
    )
      throw new Error("rotation_backup_invalid");
  }
}
