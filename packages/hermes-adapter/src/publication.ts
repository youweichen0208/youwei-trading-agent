import {
  readFile,
  lstat,
  mkdir,
  open,
  rename,
  rm,
  chown,
  readdir,
} from "node:fs/promises";
import { resolve, join } from "node:path";
import { randomUUID } from "node:crypto";
import { loadRegistry } from "./client.js";
import { loadDeployments } from "./lifecycle.js";
async function privateJson(path: string, containerReadable = false) {
  const stat = await lstat(path);
  if (
    !stat.isFile() ||
    stat.isSymbolicLink() ||
    (!containerReadable && stat.mode & 0o077) ||
    stat.size > 4194304
  )
    throw new Error("private_publication_file_required");
  return JSON.parse(await readFile(path, "utf8"));
}
function publicationOwner() {
  const value = process.env.HERMES_PUBLICATION_UID;
  if (value === undefined) return undefined;
  if (
    !/^\d+$/.test(value) ||
    !/^\d+$/.test(process.env.HERMES_PUBLICATION_GID ?? "")
  )
    throw new Error("invalid_publication_owner");
  return {
    uid: Number(value),
    gid: Number(process.env.HERMES_PUBLICATION_GID),
  };
}
async function atomic(path: string, value: unknown) {
  const temp = `${path}.${randomUUID()}.tmp`;
  try {
    const file = await open(temp, "wx", 0o600);
    try {
      await file.writeFile(JSON.stringify(value));
      await file.sync();
    } finally {
      await file.close();
    }
    const owner = publicationOwner();
    if (owner) await chown(temp, owner.uid, owner.gid);
    await rename(temp, path);
  } finally {
    await rm(temp, { force: true });
  }
}
/** Additive publication. Caller serializes publishers using the database reservation.
 * A version is fully validated before the single current.json pointer is switched.
 */
export async function publishDeployment(directory: string, outputRoot: string) {
  directory = resolve(directory);
  outputRoot = resolve(outputRoot);
  await mkdir(outputRoot, { recursive: true, mode: 0o700 });
  const stat = await lstat(outputRoot);
  if (!stat.isDirectory() || stat.isSymbolicLink() || stat.mode & 0o077)
    throw new Error("private_publication_root_required");
  let previous: {
    directories: string[];
    credentials: Record<string, string>;
    entries: any[];
  } = { directories: [], credentials: {}, entries: [] };
  try {
    const pointer = await privateJson(join(outputRoot, "current.json"));
    previous = await privateJson(pointer.snapshot);
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error;
  }
  const credentials = { ...previous.credentials };
  const manifest = await privateJson(join(directory, "preparation.json"));
  const entries = await privateJson(join(directory, "profiles.verified.json"));
  if (
    !Array.isArray(entries) ||
    entries.length !== 3 ||
    entries.some(
      (e) => !manifest.profiles.some((p: any) => p.id === e.profileId),
    )
  )
    throw new Error("verified_profiles_required");
  for (const entry of entries) {
    const secret = await privateJson(
      join(directory, "profiles", entry.profileId, "credentials.json"),
      true,
    );
    if (
      credentials[entry.tokenEnv] &&
      credentials[entry.tokenEnv] !== secret.apiKey
    )
      throw new Error("existing_credential_replacement_refused");
    credentials[entry.tokenEnv] = secret.apiKey;
    entry.evidenceFile = resolve(directory, entry.evidenceFile);
    const old = previous.entries.find((e) => e.profileId === entry.profileId);
    if (old && JSON.stringify(old) !== JSON.stringify(entry))
      throw new Error("existing_profile_replacement_refused");
  }
  const combined = [
    ...previous.entries,
    ...entries.filter(
      (e) => !previous.entries.some((old) => old.profileId === e.profileId),
    ),
  ];
  const directories = [...new Set([...previous.directories, directory])];
  const version = join(outputRoot, randomUUID());
  await mkdir(version, { mode: 0o700 });
  const registry = join(version, "registry.json"),
    keys = join(version, "credentials.json"),
    snapshot = join(version, "snapshot.json");
  const runtimeEntries = [];
  for (const entry of combined) {
    const evidenceFile = join(version, `${entry.profileId}.evidence.json`);
    await atomic(
      evidenceFile,
      JSON.parse(await readFile(entry.evidenceFile, "utf8")),
    );
    runtimeEntries.push({ ...entry, evidenceFile });
  }
  await atomic(registry, runtimeEntries);
  await atomic(keys, credentials);
  const resolver = await loadRegistry(registry, {
    ...process.env,
    ...credentials,
  });
  await loadDeployments(registry, directories, resolver);
  await atomic(snapshot, { entries: combined, credentials, directories });
  const owner = publicationOwner();
  if (owner) {
    for (const name of await readdir(version))
      await chown(join(version, name), owner.uid, owner.gid);
    await chown(version, owner.uid, owner.gid);
    await chown(outputRoot, owner.uid, owner.gid);
  }
  await atomic(join(outputRoot, "current.json"), {
    registry,
    credentials: keys,
    directories,
    snapshot,
  });
  const root = await open(outputRoot, "r");
  try {
    await root.sync();
  } finally {
    await root.close();
  }
}
export async function readPublication(
  path: string,
): Promise<{ registry: string; credentials: string; directories: string[] }> {
  const value = await privateJson(path);
  if (
    typeof value.registry !== "string" ||
    typeof value.credentials !== "string" ||
    !Array.isArray(value.directories) ||
    value.directories.some((p: unknown) => typeof p !== "string")
  )
    throw new Error("invalid_publication_pointer");
  return value;
}
