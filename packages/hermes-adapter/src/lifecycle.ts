import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { createHash } from "node:crypto";
import { lstat, readFile, realpath } from "node:fs/promises";
import { resolve, join } from "node:path";
import { z } from "zod";
import { HermesClient, loadRegistry, type RuntimeResolver, type Runtime } from "./client.js";
import type {
  ManagedProfile,
  ProfileDriver,
  ContainerState,
} from "../../server/src/profile-supervisor.js";

const digest = (value: string) =>
  createHash("sha256").update(value).digest("hex");
const same = (a: unknown, b: unknown) =>
  JSON.stringify(a) === JSON.stringify(b);
export interface Deployment extends ManagedProfile {
  composeFile: string;
  service: string;
  container: string;
  image: string;
  origin: string;
  home: string;
  homeIdentity: string;
  spec: string;
  secret: string;
  network: string;
  runtime: Runtime;
  files: Record<string, string>;
}
async function regularFile(path: string) {
  const stat = await lstat(path);
  if (!stat.isFile() || stat.isSymbolicLink() || stat.size > 1_048_576)
    throw new Error("invalid_deployment_file");
  return readFile(path, "utf8");
}

/** Only operator-supplied prepared directories and verified registry entries. */
export async function loadDeployments(
  registryFile: string,
  directories: string[],
  resolver?: RuntimeResolver,
): Promise<Deployment[]> {
  const runtimes = resolver ?? (await loadRegistry(registryFile));
  const entries = JSON.parse(await regularFile(resolve(registryFile)));
  const deployments: Deployment[] = [];
  for (const input of directories) {
    const stat = await lstat(resolve(input));
    const directory = await realpath(resolve(input));
    if (!stat.isDirectory() || stat.isSymbolicLink() || stat.mode & 0o077)
      throw new Error("private_deployment_directory_required");
    const preparation = JSON.parse(
      await regularFile(join(directory, "preparation.json")),
    );
    const ownerId = z.uuid().parse(preparation.ownerId);
    const contextId = z.uuid().parse(preparation.contextId ?? ownerId);
    const composeFile = join(directory, "compose.json");
    const composeRaw = await regularFile(composeFile),
      compose = JSON.parse(composeRaw);
    if (
      compose.name !== `alpha-${contextId}` ||
      Object.keys(compose.services).length !== 3 ||
      preparation.profiles.length !== 3 ||
      Object.keys(compose).some(
        (key) => !["name", "services", "networks", "secrets"].includes(key),
      ) ||
      !same(
        Object.keys(compose.services).sort(),
        Object.keys(compose.networks).sort(),
      ) ||
      !same(
        Object.keys(compose.services).sort(),
        Object.keys(compose.secrets).sort(),
      )
    )
      throw new Error("invalid_deployment_compose");
    for (const prepared of preparation.profiles) {
      const profileId = z.uuid().parse(prepared.id);
      const role = z
        .enum(["research", "prediction", "portfolio"])
        .parse(prepared.role);
      const entry = entries.find(
        (e: { profileId: string }) => e.profileId === profileId,
      );
      const runtime = runtimes(profileId);
      if (
        !entry ||
        !runtime?.healthy ||
        entry.imageDigest !== preparation.image
      )
        throw new Error("verified_profile_registry_required");
      const service = `profile-${profileId}`,
        container = `alpha-${profileId}`;
      const value = compose.services[service];
      const base = join(directory, "profiles", profileId);
      const home = join(base, "home"),
        spec = join(base, "runtime.json"),
        secret = join(base, "credentials.json");
      if ((await realpath(base)) !== base || (await realpath(home)) !== home)
        throw new Error("deployment_symlink_refused");
      const homeStat = await lstat(home, { bigint: true });
      if (!homeStat.isDirectory()) throw new Error("invalid_profile_home");
      const homeIdentity = `${homeStat.dev}:${homeStat.ino}`;
      const expectedIdentity = { version: 1, ownerId, profileId, role };
      const identityRaw = await regularFile(join(home, ".alpha-profile.json"));
      const specRaw = await regularFile(spec),
        secretRaw = await regularFile(secret);
      const identity = JSON.parse(identityRaw),
        runtimeSpec = JSON.parse(specRaw);
      const expectedRuntime = new HermesClient(
        entry.origin,
        JSON.parse(secretRaw).apiKey,
        entry.imageDigest,
        runtime.retentionMs,
        fetch,
        process.env.TOOL_CAPABILITY_KEY
          ? digest(process.env.TOOL_CAPABILITY_KEY)
          : undefined,
      );
      if (runtime.identity !== expectedRuntime.identity)
        throw new Error("profile_credential_mismatch");
      if (
        !same(identity, expectedIdentity) ||
        preparation.fingerprints[profileId] !== digest(specRaw) ||
        !same(
          Object.fromEntries(
            Object.keys(expectedIdentity).map((key) => [key, runtimeSpec[key]]),
          ),
          expectedIdentity,
        )
      )
        throw new Error("profile_identity_mismatch");
      const relative = `./profiles/${profileId}`;
      const port = Number(new URL(entry.origin).port);
      const allowed = [
        "image",
        "container_name",
        "labels",
        "restart",
        "command",
        "environment",
        "volumes",
        "secrets",
        "ports",
        "networks",
        "extra_hosts",
        "security_opt",
        "cap_drop",
        "cap_add",
        "pids_limit",
        "mem_limit",
        "cpus",
        "stop_grace_period",
        "logging",
      ];
      if (
        !value ||
        Object.keys(value).some((key) => !allowed.includes(key)) ||
        value.image !== entry.imageDigest ||
        value.container_name !== container ||
        value.restart !== "no" ||
        !same(value.command, [
          "/opt/hermes/.venv/bin/python",
          "-m",
          "alpha_hermes.bootstrap",
        ]) ||
        !same(value.labels, {
          "lab.alpha.owner": ownerId,
          "lab.alpha.profile": profileId,
          "lab.alpha.role": role,
        }) ||
        !same(value.environment, {
          ALPHA_PROFILE_SPEC: "/run/alpha/runtime.json",
          ALPHA_PROFILE_SECRET: "/run/secrets/profile_credentials",
        }) ||
        !same(value.volumes, [
          { type: "bind", source: `${relative}/home`, target: "/opt/data" },
          {
            type: "bind",
            source: `${relative}/runtime.json`,
            target: "/run/alpha/runtime.json",
            read_only: true,
          },
        ]) ||
        !same(value.secrets, [
          { source: service, target: "profile_credentials" },
        ]) ||
        !same(compose.secrets[service], {
          file: `${relative}/credentials.json`,
        }) ||
        !same(value.ports, [
          {
            target: 8642,
            published: String(port),
            host_ip: "127.0.0.1",
            protocol: "tcp",
          },
        ]) ||
        new URL(entry.origin).hostname !== "127.0.0.1" ||
        new URL(entry.origin).protocol !== "http:" ||
        !same(value.networks, [service]) ||
        !same(compose.networks[service], { driver: "bridge" }) ||
        !same(value.security_opt, ["no-new-privileges:true"]) ||
        !same(value.cap_drop, ["ALL"]) ||
        !same(value.cap_add, [
          "CHOWN",
          "DAC_OVERRIDE",
          "FOWNER",
          "SETGID",
          "SETUID",
          "KILL",
        ]) ||
        value.pids_limit !== 256 ||
        value.mem_limit !== "2g" ||
        value.cpus !== 1 ||
        value.stop_grace_period !== "60s" ||
        !same(value.extra_hosts, ["host.docker.internal:host-gateway"]) ||
        !same(value.logging, {
          driver: "json-file",
          options: { "max-size": "10m", "max-file": "3" },
        })
      )
        throw new Error("deployment_compose_mismatch");
      const files = {
        [composeFile]: digest(composeRaw),
        [spec]: digest(specRaw),
        [secret]: digest(secretRaw),
        [join(home, ".alpha-profile.json")]: digest(identityRaw),
      };
      deployments.push({
        ownerId,
        profileId,
        role,
        composeFile,
        service,
        container,
        image: entry.imageDigest,
        origin: entry.origin,
        home,
        homeIdentity,
        spec,
        secret,
        network: `${compose.name}_${service}`,
        runtime,
        files,
        deploymentHash: digest(JSON.stringify({ files, homeIdentity })),
      });
    }
  }
  if (new Set(deployments.map((p) => p.profileId)).size !== deployments.length)
    throw new Error("duplicate_managed_profile");
  return deployments;
}

export class DockerProfileDriver implements ProfileDriver {
  private bindings: Map<string, Deployment>;
  constructor(
    deployments: Deployment[],
    private command = async (args: string[], timeout: number) =>
      (
        await promisify(execFile)("docker", args, {
          timeout,
          maxBuffer: 2 * 1024 * 1024,
        })
      ).stdout,
  ) {
    this.bindings = new Map(deployments.map((p) => [p.profileId, p]));
  }
  private async deployment(profile: ManagedProfile) {
    const p = this.bindings.get(profile.profileId);
    if (!p || p.deploymentHash !== profile.deploymentHash)
      throw new Error("unknown_managed_profile");
    for (const [path, hash] of Object.entries(p.files))
      if (digest(await regularFile(path)) !== hash)
        throw new Error("deployment_changed");
    if ((await realpath(p.home)) !== p.home)
      throw new Error("deployment_changed");
    const home = await lstat(p.home, { bigint: true });
    if (`${home.dev}:${home.ino}` !== p.homeIdentity)
      throw new Error("profile_home_replaced");
    return p;
  }
  private async docker(args: string[], timeout = 15_000) {
    try {
      return await this.command(args, timeout);
    } catch {
      throw new Error("docker_operation_unconfirmed");
    }
  }
  async absent(profile: ManagedProfile, containerId: string): Promise<boolean> {
    const p = await this.deployment(profile);
    const byId = (
      await this.docker([
        "ps",
        "-a",
        "--no-trunc",
        "--filter",
        `id=${containerId}`,
      ])
    ).trim();
    if (byId) return false;
    const byName = (
      await this.docker([
        "ps",
        "-a",
        "--no-trunc",
        "--filter",
        `name=^/${p.container}$`,
      ])
    ).trim();
    return !byName;
  }
  async inspect(profile: ManagedProfile): Promise<ContainerState | undefined> {
    const p = await this.deployment(profile);
    const ids = (
      await this.docker([
        "ps",
        "-aq",
        "--no-trunc",
        "--filter",
        `name=^/${p.container}$`,
      ])
    ).trim();
    if (!ids) return undefined;
    if (!/^[a-f0-9]{64}$/.test(ids))
      throw new Error("ambiguous_profile_container");
    const [info] = JSON.parse(await this.docker(["container", "inspect", ids]));
    const [image] = JSON.parse(
      await this.docker(["image", "inspect", p.image]),
    );
    const labels = info.Config.Labels ?? {},
      host = info.HostConfig;
    const mounts = new Map(
      info.Mounts.map(
        (m: { Destination: string; Source: string; RW: boolean }) => [
          m.Destination,
          m,
        ],
      ),
    );
    const mount = (target: string, source: string, writable: boolean) => {
      const m = mounts.get(target) as
        { Source: string; RW: boolean } | undefined;
      return m?.Source === source && m?.RW === writable;
    };
    if (
      info.Id !== ids ||
      info.Image !== p.image ||
      info.Name !== `/${p.container}` ||
      labels["lab.alpha.owner"] !== p.ownerId ||
      labels["lab.alpha.profile"] !== p.profileId ||
      labels["lab.alpha.role"] !== p.role ||
      image.Id !== p.image ||
      !same(info.Config.Entrypoint, image.Config.Entrypoint) ||
      info.Config.User !== image.Config.User ||
      !same(info.Config.Cmd, [
        "/opt/hermes/.venv/bin/python",
        "-m",
        "alpha_hermes.bootstrap",
      ]) ||
      host.Privileged ||
      host.PidMode === "host" ||
      host.IpcMode === "host" ||
      host.UsernsMode === "host" ||
      host.NetworkMode === "host" ||
      (host.Devices?.length ?? 0) !== 0 ||
      (host.DeviceRequests?.length ?? 0) !== 0 ||
      host.RestartPolicy.Name !== "no" ||
      !host.SecurityOpt?.includes("no-new-privileges:true") ||
      !host.CapDrop?.includes("ALL") ||
      !same(
        (host.CapAdd ?? [])
          .map((name: string) => name.replace(/^CAP_/, ""))
          .sort(),
        ["CHOWN", "DAC_OVERRIDE", "FOWNER", "KILL", "SETGID", "SETUID"],
      ) ||
      host.Memory !== 2 * 1024 ** 3 ||
      host.NanoCpus !== 1_000_000_000 ||
      host.PidsLimit !== 256 ||
      !same(host.PortBindings, {
        "8642/tcp": [{ HostIp: "127.0.0.1", HostPort: new URL(p.origin).port }],
      }) ||
      !same(Object.keys(info.NetworkSettings.Networks), [p.network]) ||
      mounts.size !== 3 ||
      !mount("/opt/data", p.home, true) ||
      !mount("/run/alpha/runtime.json", p.spec, false) ||
      !mount("/run/secrets/profile_credentials", p.secret, false)
    )
      throw new Error("container_isolation_mismatch");
    const status =
      info.State.Running && !info.State.Paused && !info.State.Restarting
        ? "running"
        : ["created", "exited"].includes(info.State.Status) &&
            info.State.Pid === 0
          ? "stopped"
          : "other";
    const startedAt =
      typeof info.State.StartedAt === "string" &&
      /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?Z$/.test(
        info.State.StartedAt,
      ) &&
      Number.isFinite(Date.parse(info.State.StartedAt)) &&
      !info.State.StartedAt.startsWith("0001-")
        ? info.State.StartedAt
        : undefined;
    if (status === "running" && !startedAt)
      throw new Error("container_start_identity_missing");
    return { id: ids, status, ...(startedAt ? { startedAt } : {}) };
  }
  async start(profile: ManagedProfile, existingId?: string) {
    const p = await this.deployment(profile);
    const found = await this.inspect(profile);
    if (found?.id !== existingId) throw new Error("container_identity_changed");
    if (existingId)
      await this.docker(["container", "start", existingId], 70_000);
    else
      await this.docker(
        [
          "compose",
          "-f",
          p.composeFile,
          "up",
          "-d",
          "--no-deps",
          "--no-recreate",
          "--no-build",
          "--pull",
          "never",
          p.service,
        ],
        70_000,
      );
  }
  async stop(profile: ManagedProfile, containerId: string) {
    if ((await this.inspect(profile))?.id !== containerId)
      throw new Error("container_identity_changed");
    await this.docker(
      ["container", "stop", "--timeout", "60", containerId],
      70_000,
    );
  }
  async healthy(profile: ManagedProfile) {
    const p = await this.deployment(profile);
    return p.runtime.healthy!();
  }
}
