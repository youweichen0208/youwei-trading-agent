import { randomBytes, createHash } from "node:crypto";
import {
  mkdir,
  mkdtemp,
  readFile,
  rename,
  rm,
  writeFile,
  lstat,
  chmod,
} from "node:fs/promises";
import { isAbsolute, join, resolve } from "node:path";
import type { Pool } from "pg";
import { realpath } from "node:fs/promises";
import {
  withPreparationLock,
  reservePreparation,
} from "./preparation-ledger.js";
import { z } from "zod";

const roles = ["research", "prediction", "portfolio"] as const;
const model = z
  .object({
    name: z.string().min(1).max(200),
    baseUrl: z.url().refine((value) => {
      const url = new URL(value);
      return (
        ["https:", "http:"].includes(url.protocol) &&
        !url.username &&
        !url.password &&
        !url.search &&
        !url.hash
      );
    }),
    apiMode: z.enum(["chat_completions", "responses", "anthropic_messages"]),
    authMode: z.enum(["api_key", "bearer"]).optional(),
    apiKey: z
      .string()
      .min(16)
      .max(4096)
      .regex(/^[^\s\x00-\x1f]+$/),
  })
  .strict()
  .refine(
    (value) =>
      value.authMode === undefined || value.apiMode === "anthropic_messages",
    {
      message: "authMode is only supported for anthropic_messages",
    },
  );
export const provisionSettings = z
  .object({
    // The custom image ID/digest, not the upstream base image digest.
    image: z.string().regex(/^sha256:[a-f0-9]{64}$/),
    gatewayOrigin: z.url().refine((value) => {
      const url = new URL(value);
      return (
        ["https:", "http:"].includes(url.protocol) &&
        !url.username &&
        !url.password &&
        ["", "/"].includes(url.pathname) &&
        !url.search &&
        !url.hash &&
        !["localhost", "127.0.0.1", "[::1]"].includes(url.hostname)
      );
    }),
    firstPort: z.number().int().min(1024).max(65533),
    models: z
      .object({ research: model, prediction: model, portfolio: model })
      .strict(),
  })
  .strict();

export const json = (value: unknown) => JSON.stringify(value, null, 2) + "\n";
export const digest = (value: string) =>
  createHash("sha256").update(value).digest("hex");

export async function ownerProfiles(
  pool: Pick<Pool, "query">,
  ownerId: string,
): Promise<PreparedProfile[]> {
  z.uuid().parse(ownerId);
  const profiles = (
    await pool.query(
      "SELECT id,role FROM profile_instances WHERE context_id=$1 ORDER BY role",
      [ownerId],
    )
  ).rows as PreparedProfile[];
  if (
    profiles.length !== 3 ||
    new Set(profiles.map((p) => p.role)).size !== 3 ||
    profiles.some((p) => !roles.includes(p.role))
  )
    throw new Error("owner_profiles_not_found");
  for (const profile of profiles) z.uuid().parse(profile.id);
  return profiles;
}
export async function contextOwner(
  pool: Pick<Pool, "query">,
  contextId: string,
): Promise<string> {
  const row = (
    await pool.query("SELECT owner_id FROM execution_contexts WHERE id=$1", [
      contextId,
    ])
  ).rows[0];
  if (!row) throw new Error("execution_context_missing");
  return row.owner_id;
}
export async function privatePreparationRoot(outputRoot: string) {
  if (!isAbsolute(outputRoot))
    throw new Error("absolute_private_output_required");
  await mkdir(outputRoot, { recursive: true, mode: 0o700 });
  const stat = await lstat(outputRoot);
  if (!stat.isDirectory() || stat.isSymbolicLink() || stat.mode & 0o077)
    throw new Error("private_output_permissions_required");
  return realpath(outputRoot);
}
export async function writePreparation(
  staging: string,
  files: Map<string, PreparationFile>,
) {
  for (const [path, file] of files) {
    await mkdir(resolve(staging, path, ".."), { recursive: true, mode: 0o700 });
    await writeFile(join(staging, path), file.content, {
      mode: file.mode,
      flag: "wx",
    });
    // The staging tree is private and every file was exclusively created.
    // A service umask of 0077 must not strip the read-only container mount's
    // read bits: Hermes runs as a different UID after its privilege drop.
    await chmod(join(staging, path), file.mode);
  }
}
export function preparationPaths(directory: string) {
  return {
    directory,
    composeFile: join(directory, "compose.json"),
    registryFile: join(directory, "profiles.unverified.json"),
  };
}
/** Prepare only; do not start containers or mark profiles ready without evidence. */
export async function prepareProfiles(
  pool: Pool,
  ownerId: string,
  input: unknown,
  outputRoot: string,
) {
  ownerId = z.uuid().parse(ownerId).toLowerCase();
  const settings = provisionSettings.parse(input);
  const profiles = await ownerProfiles(pool, ownerId);
  const dataOwner = await contextOwner(pool, ownerId);
  const root = await privatePreparationRoot(outputRoot);
  try {
    await lstat(join(root, ".auto-preparer.json"));
    throw new Error("automatic_preparation_root");
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error;
  }
  const result = await withPreparationLock(pool, ownerId, async (c) => {
    const directory = join(root, ownerId);
    try {
      await lstat(directory);
      throw new Error("profile_directory_exists");
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error;
    }
    // Reserve before publishing files. Failed/crashed manual preparation keeps
    // its binding and ports; automatic preparation must not regenerate its keys.
    await reservePreparation(
      c,
      ownerId,
      digest(json({ settings, root })),
      settings.firstPort,
    );
    const staging = await mkdtemp(join(root, ".alpha-prepare-"));
    try {
      const keys = Object.fromEntries(
        profiles.map((p) => [p.id, randomBytes(32).toString("hex")]),
      );
      await writePreparation(
        staging,
        renderPreparation(profiles, dataOwner, settings, keys, ownerId),
      );
      await rename(staging, directory);
      return preparationPaths(directory);
    } finally {
      await rm(staging, { recursive: true, force: true });
    }
  });
  if (!result) throw new Error("preparation_busy");
  return result;
}

export type PreparedProfile = { id: string; role: (typeof roles)[number] };
export type PreparationFile = { content: string; mode: number };
export function renderPreparation(
  profiles: PreparedProfile[],
  ownerId: string,
  settings: z.infer<typeof provisionSettings>,
  keys: Record<string, string>,
  contextId = ownerId,
): Map<string, PreparationFile> {
  const files = new Map<string, PreparationFile>();
  const put = (path: string, content: string, options: { mode: number }) => {
    files.set(path, { content, mode: options.mode });
  };
  const services: Record<string, unknown> = {},
    networks: Record<string, unknown> = {};
  const secrets: Record<string, unknown> = {};
  const registry = [],
    credentials = [],
    fingerprints: Record<string, string> = {};
  for (const role of roles) {
    const profile = profiles.find((p) => p.role === role)!;
    z.uuid().parse(profile.id);
    const identity = { version: 1, ownerId, profileId: profile.id, role };
    const service = `profile-${profile.id}`;
    const port = settings.firstPort + roles.indexOf(role);
    const apiKey = keys[profile.id]!;
    if (!/^[a-f0-9]{64}$/.test(apiKey))
      throw new Error("invalid_preparation_key");
    const tokenEnv = `HERMES_PROFILE_${profile.id.replaceAll("-", "").toUpperCase()}`;
    const { apiKey: modelApiKey, ...modelConfig } = settings.models[role];
    const relative = join("profiles", profile.id);
    const profileDir = relative;
    const spec = {
      ...identity,
      model: modelConfig,
      gatewayOrigin: settings.gatewayOrigin,
    };
    put(join(profileDir, "home", ".alpha-profile.json"), json(identity), {
      mode: 0o600,
    });
    // Parent directories are 0700; these two files are mounted read-only into
    // this service only. 0444 lets the container's unprivileged UID read them.
    put(join(profileDir, "runtime.json"), json(spec), {
      mode: 0o444,
    });
    put(join(profileDir, "credentials.json"), json({ apiKey, modelApiKey }), {
      mode: 0o444,
    });
    fingerprints[profile.id] = digest(json(spec));
    networks[service] = { driver: "bridge" };
    secrets[service] = { file: `./${relative}/credentials.json` };
    services[service] = {
      image: settings.image,
      container_name: `alpha-${profile.id}`,
      labels: {
        "lab.alpha.owner": ownerId,
        "lab.alpha.profile": profile.id,
        "lab.alpha.role": role,
      },
      restart: "no",
      command: ["/opt/hermes/.venv/bin/python", "-m", "alpha_hermes.bootstrap"],
      environment: {
        ALPHA_PROFILE_SPEC: "/run/alpha/runtime.json",
        ALPHA_PROFILE_SECRET: "/run/secrets/profile_credentials",
      },
      volumes: [
        { type: "bind", source: `./${relative}/home`, target: "/opt/data" },
        {
          type: "bind",
          source: `./${relative}/runtime.json`,
          target: "/run/alpha/runtime.json",
          read_only: true,
        },
      ],
      secrets: [{ source: service, target: "profile_credentials" }],
      ports: [
        {
          target: 8642,
          published: String(port),
          host_ip: "127.0.0.1",
          protocol: "tcp",
        },
      ],
      networks: [service],
      extra_hosts: ["host.docker.internal:host-gateway"],
      security_opt: ["no-new-privileges:true"],
      cap_drop: ["ALL"],
      // Upstream s6 boot initializes/chowns only this Profile's volume, then
      // runs Hermes as UID 10000. No Docker socket or host PID/network mode.
      cap_add: ["CHOWN", "DAC_OVERRIDE", "FOWNER", "SETGID", "SETUID", "KILL"],
      pids_limit: 256,
      mem_limit: "2g",
      cpus: 1,
      stop_grace_period: "60s",
      logging: {
        driver: "json-file",
        options: { "max-size": "10m", "max-file": "3" },
      },
    };
    registry.push({
      profileId: profile.id,
      origin: `http://127.0.0.1:${port}`,
      tokenEnv,
      imageDigest: settings.image,
      evidenceFile: `evidence/${profile.id}.json`,
    });
    credentials.push(`${tokenEnv}=${apiKey}`);
  }
  put(
    join("compose.json"),
    json({
      name: `alpha-${contextId}`,
      services,
      networks,
      secrets,
    }),
    { mode: 0o600 },
  );
  put(join("profiles.unverified.json"), json(registry), {
    mode: 0o600,
  });
  put(join("worker.env"), credentials.join("\n") + "\n", { mode: 0o600 });
  put(
    join("preparation.json"),
    json({
      ownerId,
      ...(contextId !== ownerId ? { contextId } : {}),
      profiles,
      fingerprints,
      image: settings.image,
      status: "prepared_unverified",
    }),
    { mode: 0o600 },
  );
  return files;
}

export async function readProvisionSettings(path: string): Promise<unknown> {
  const stat = await lstat(path);
  if (
    !stat.isFile() ||
    stat.isSymbolicLink() ||
    stat.mode & 0o077 ||
    stat.size > 65536
  )
    throw new Error("private_settings_file_required");
  return JSON.parse(await readFile(path, "utf8"));
}
