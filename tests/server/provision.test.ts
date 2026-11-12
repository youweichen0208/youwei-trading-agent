import { before, after, test } from "node:test";
import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import {
  mkdtemp,
  readFile,
  rm,
  stat,
  writeFile,
  chmod,
  symlink,
  mkdir,
} from "node:fs/promises";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { Pool } from "pg";
import { AuthStore } from "../../packages/server/src/auth.js";
import { migrate } from "../../packages/server/src/migrate.js";
import {
  prepareProfiles,
  readProvisionSettings,
} from "../../packages/hermes-adapter/src/provision.js";
import {
  loadDeployments,
  DockerProfileDriver,
} from "../../packages/hermes-adapter/src/lifecycle.js";

const url = process.env.TEST_DATABASE_URL;
if (!url || !new URL(url).pathname.endsWith("_test"))
  throw new Error("Dedicated TEST_DATABASE_URL ending in _test required");
const schema = `test_${randomUUID().replaceAll("-", "")}`;
const admin = new Pool({ connectionString: url });
const pool = new Pool({
  connectionString: url,
  options: `-c search_path=${schema}`,
});
let root: string;
before(async () => {
  await admin.query(`CREATE SCHEMA ${schema}`);
  await migrate(pool);
  root = await mkdtemp(join(tmpdir(), "alpha-profiles-"));
});
after(async () => {
  await rm(root, { recursive: true, force: true });
  await pool.end();
  await admin.query(`DROP SCHEMA ${schema} CASCADE`);
  await admin.end();
});
const settings = (port = 18642) => ({
  image: `sha256:${"a".repeat(64)}`,
  gatewayOrigin: "http://host.docker.internal:3410",
  firstPort: port,
  models: Object.fromEntries(
    ["research", "prediction", "portfolio"].map((role) => [
      role,
      {
        name: "test-only-model",
        baseUrl: "https://model.invalid/v1",
        apiMode: "chat_completions",
        apiKey: `test-only-credential-${role}`,
      },
    ]),
  ),
});
async function user() {
  return new AuthStore(pool).register(
    `${randomUUID()}@test.invalid`,
    "a-long-test-password",
  );
}

test("strict host umask preserves private directories and container-readable mounts", async () => {
  const owner = await user();
  const previous = process.umask(0o077);
  try {
    const prepared = await prepareProfiles(
      pool,
      owner.id,
      settings(18810),
      root,
    );
    assert.equal((await stat(prepared.directory)).mode & 0o777, 0o700);
    const entries = JSON.parse(await readFile(prepared.registryFile, "utf8"));
    for (const entry of entries) {
      const directory = join(prepared.directory, "profiles", entry.profileId);
      assert.equal((await stat(directory)).mode & 0o777, 0o700);
      for (const file of ["runtime.json", "credentials.json"])
        assert.equal((await stat(join(directory, file))).mode & 0o777, 0o444);
      assert.equal(
        (await stat(join(directory, "home", ".alpha-profile.json"))).mode &
          0o777,
        0o600,
      );
    }
  } finally {
    process.umask(previous);
  }
});

test("supervisor validates sealed prepared files and refuses foreign or weakened container identity", async () => {
  const owner = await user();
  const prepared = await prepareProfiles(pool, owner.id, settings(18720), root);
  const entries = JSON.parse(await readFile(prepared.registryFile, "utf8"));
  const previous = new Map<string, string | undefined>();
  const env = (name: string, value: string) => {
    previous.set(name, process.env[name]);
    process.env[name] = value;
  };
  env("TOOL_CAPABILITY_KEY", "a".repeat(64));
  try {
    await mkdir(join(prepared.directory, "evidence"), { mode: 0o700 });
    for (const entry of entries) {
      const credentials = JSON.parse(
        await readFile(
          join(
            prepared.directory,
            "profiles",
            entry.profileId,
            "credentials.json",
          ),
          "utf8",
        ),
      );
      env(entry.tokenEnv, credentials.apiKey);
      // Test-only contract fixtures, private temporary directory removed by after().
      await writeFile(
        join(prepared.directory, entry.evidenceFile),
        JSON.stringify({
          imageDigest: entry.imageDigest,
          origin: entry.origin,
          profileId: entry.profileId,
          checkedAt: new Date().toISOString(),
          retentionMs: 60_000,
          checks: {
            profileIsolation: true,
            durableIdempotency: true,
            restartReplay: true,
            sessionIdentity: true,
            stopWaitsForExit: true,
            terminalOutput: true,
            toolIdentityInjection: true,
          },
        }),
        { mode: 0o600 },
      );
    }
    const deployments = await loadDeployments(prepared.registryFile, [
      prepared.directory,
    ]);
    assert.equal(deployments.length, 3);
    const { publishDeployment, readPublication } =
      await import("../../packages/hermes-adapter/src/publication.js");
    await writeFile(
      join(prepared.directory, "profiles.verified.json"),
      JSON.stringify(entries),
      { mode: 0o600 },
    );
    const publicationRoot = join(root, "published");
    for (const entry of entries) delete process.env[entry.tokenEnv];
    await publishDeployment(prepared.directory, publicationRoot);
    for (const entry of entries) {
      const secret = JSON.parse(
        await readFile(
          join(
            prepared.directory,
            "profiles",
            entry.profileId,
            "credentials.json",
          ),
          "utf8",
        ),
      );
      process.env[entry.tokenEnv] = secret.apiKey;
    }
    const pointer = await readPublication(
      join(publicationRoot, "current.json"),
    );
    assert.deepEqual(pointer.directories, [prepared.directory]);
    const originalPointer = await readFile(
      join(publicationRoot, "current.json"),
      "utf8",
    );
    await writeFile(join(prepared.directory, "profiles.verified.json"), "[]");
    await assert.rejects(
      publishDeployment(prepared.directory, publicationRoot),
    );
    assert.equal(
      await readFile(join(publicationRoot, "current.json"), "utf8"),
      originalPointer,
    );
    const p = deployments[0]!;
    const id = "b".repeat(64);
    let present = true;
    const changes: string[][] = [];
    const info: any = {
      Id: id,
      Image: p.image,
      Name: `/${p.container}`,
      Config: {
        User: "",
        Entrypoint: ["/init"],
        Cmd: ["/opt/hermes/.venv/bin/python", "-m", "alpha_hermes.bootstrap"],
        Labels: {
          "lab.alpha.owner": p.ownerId,
          "lab.alpha.profile": p.profileId,
          "lab.alpha.role": p.role,
        },
      },
      HostConfig: {
        RestartPolicy: { Name: "no" },
        SecurityOpt: ["no-new-privileges:true"],
        CapDrop: ["ALL"],
        CapAdd: ["CHOWN", "DAC_OVERRIDE", "FOWNER", "SETGID", "SETUID", "KILL"],
        Memory: 2 * 1024 ** 3,
        NanoCpus: 1e9,
        PidsLimit: 256,
        PortBindings: {
          "8642/tcp": [
            { HostIp: "127.0.0.1", HostPort: new URL(p.origin).port },
          ],
        },
      },
      Mounts: [
        { Destination: "/opt/data", Source: p.home, RW: true },
        { Destination: "/run/alpha/runtime.json", Source: p.spec, RW: false },
        {
          Destination: "/run/secrets/profile_credentials",
          Source: p.secret,
          RW: false,
        },
      ],
      NetworkSettings: { Networks: { [p.network]: {} } },
      State: {
        Running: true,
        Status: "running",
        Pid: 42,
        StartedAt: "2026-11-07T01:02:03.123456789Z",
      },
    };
    const driver = new DockerProfileDriver(deployments, async (args) => {
      if (args[0] === "ps") return present ? id : "";
      if (args[0] === "image")
        return JSON.stringify([
          { Id: p.image, Config: { User: "", Entrypoint: ["/init"] } },
        ]);
      if (args[1] === "inspect") return JSON.stringify([info]);
      changes.push(args);
      return "";
    });
    assert.deepEqual(await driver.inspect(p), {
      id,
      status: "running",
      startedAt: info.State.StartedAt,
    });
    const startedAt = info.State.StartedAt;
    info.State.StartedAt = "0001-01-01T00:00:00Z";
    await assert.rejects(driver.inspect(p), /container_start_identity_missing/);
    info.State.StartedAt = startedAt;
    info.HostConfig.CapAdd.push("SYS_ADMIN");
    await assert.rejects(driver.start(p, id), /isolation_mismatch/);
    info.HostConfig.CapAdd.pop();
    info.Config.Labels["lab.alpha.owner"] = randomUUID();
    await assert.rejects(driver.stop(p, id), /isolation_mismatch/);
    info.Config.Labels["lab.alpha.owner"] = p.ownerId;
    info.Mounts.push({
      Destination: "/var/run/docker.sock",
      Source: "/var/run/docker.sock",
      RW: true,
    });
    await assert.rejects(driver.inspect(p), /isolation_mismatch/);
    info.Mounts.pop();
    assert.equal(changes.length, 0);
    await driver.stop(p, id);
    assert.deepEqual(changes.pop(), [
      "container",
      "stop",
      "--timeout",
      "60",
      id,
    ]);
    present = false;
    await driver.start(p);
    assert.deepEqual(changes.pop(), [
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
    ]);
    const original = await readFile(prepared.composeFile, "utf8");
    await writeFile(prepared.composeFile, original + "\n");
    await assert.rejects(driver.start(p), /deployment_changed/);
    const unsafe = JSON.parse(original);
    unsafe.services[p.service].restart = "unless-stopped";
    await writeFile(prepared.composeFile, JSON.stringify(unsafe));
    await assert.rejects(
      loadDeployments(prepared.registryFile, [prepared.directory]),
      /compose_mismatch/,
    );
    assert.equal(changes.length, 0);
  } finally {
    for (const [name, value] of previous) {
      if (value === undefined) delete process.env[name];
      else process.env[name] = value;
    }
  }
});

test("profile preparation separates two owners' homes, credentials, networks and API bindings", async () => {
  const apiKeys = new Set(),
    homes = new Set(),
    networkNames = new Set();
  for (const firstPort of [18642, 18652]) {
    const owner = await user();
    const prepared = await prepareProfiles(
      pool,
      owner.id,
      settings(firstPort),
      root,
    );
    const compose = JSON.parse(await readFile(prepared.composeFile, "utf8"));
    assert.equal(Object.keys(compose.services).length, 3);
    assert.equal(compose.name, `alpha-${owner.id}`);
    assert.equal((await stat(prepared.directory)).mode & 0o777, 0o700);
    const registry = JSON.parse(await readFile(prepared.registryFile, "utf8"));
    for (const service of Object.values(compose.services) as any[]) {
      const profileId = service.labels["lab.alpha.profile"];
      assert.equal(service.labels["lab.alpha.owner"], owner.id);
      assert.deepEqual(service.cap_drop, ["ALL"]);
      assert.equal(service.ports[0].host_ip, "127.0.0.1");
      assert.equal(service.networks.length, 1);
      assert.equal(service.volumes.length, 2);
      assert.ok(
        service.volumes.every((v: any) => !v.source.includes("docker.sock")),
      );
      const path = join(prepared.directory, "profiles", profileId);
      const credentials = JSON.parse(
        await readFile(join(path, "credentials.json"), "utf8"),
      );
      const identity = JSON.parse(
        await readFile(join(path, "home", ".alpha-profile.json"), "utf8"),
      );
      assert.equal(identity.ownerId, owner.id);
      assert.equal(identity.profileId, profileId);
      assert.match(credentials.apiKey, /^[a-f0-9]{64}$/);
      apiKeys.add(credentials.apiKey);
      homes.add(path);
      networkNames.add(service.networks[0]);
      const transport = registry.find((r: any) => r.profileId === profileId);
      assert.equal(transport.imageDigest, settings().image);
      assert.equal(
        transport.origin,
        `http://127.0.0.1:${service.ports[0].published}`,
      );
      assert.equal(JSON.stringify(compose).includes(credentials.apiKey), false);
      assert.equal(
        JSON.stringify(compose).includes(credentials.modelApiKey),
        false,
      );
    }
    const states = (
      await pool.query(
        "SELECT state FROM profile_instances WHERE owner_id=$1",
        [owner.id],
      )
    ).rows;
    assert.ok(states.every((row) => row.state === "unprovisioned"));
  }
  assert.equal(apiKeys.size, 6);
  assert.equal(homes.size, 6);
  assert.equal(networkNames.size, 6);
});

test("preparation refuses overwrite, unknown owner, identity injection and concurrent duplicate creation", async () => {
  const owner = await user();
  const attempts = await Promise.allSettled([
    prepareProfiles(pool, owner.id, settings(19000), root),
    prepareProfiles(pool, owner.id, settings(19000), root),
  ]);
  assert.equal(attempts.filter((a) => a.status === "fulfilled").length, 1);
  const original = await readFile(join(root, owner.id, "worker.env"), "utf8");
  await assert.rejects(
    prepareProfiles(pool, owner.id, settings(19000), root),
    /profile_directory_exists/,
  );
  assert.equal(
    await readFile(join(root, owner.id, "worker.env"), "utf8"),
    original,
  );
  await assert.rejects(
    prepareProfiles(pool, randomUUID(), settings(19000), root),
    /owner_profiles_not_found/,
  );
  await assert.rejects(
    prepareProfiles(
      pool,
      owner.id,
      { ...settings(19000), ownerId: randomUUID() },
      root,
    ),
    /Unrecognized key/,
  );
});

test("credential-bearing provisioning inputs must be private regular files", async () => {
  const file = join(root, "settings.json");
  await writeFile(file, JSON.stringify(settings()), { mode: 0o600 });
  assert.deepEqual(await readProvisionSettings(file), settings());
  await chmod(file, 0o644);
  await assert.rejects(
    readProvisionSettings(file),
    /private_settings_file_required/,
  );
  await chmod(file, 0o600);
  const link = join(root, "settings-link.json");
  await symlink(file, link);
  await assert.rejects(
    readProvisionSettings(link),
    /private_settings_file_required/,
  );
});

test("Anthropic profiles preserve protocol and auth selection without exposing model credentials", async () => {
  const configured = settings(19300);
  for (const model of Object.values(configured.models)) {
    Object.assign(model, { apiMode: "anthropic_messages", authMode: "bearer" });
  }
  const owner = await user();
  const prepared = await prepareProfiles(pool, owner.id, configured, root);
  const registry = JSON.parse(await readFile(prepared.registryFile, "utf8"));
  for (const profile of registry) {
    const directory = join(prepared.directory, "profiles", profile.profileId);
    const runtime = JSON.parse(
      await readFile(join(directory, "runtime.json"), "utf8"),
    );
    assert.equal(runtime.model.apiMode, "anthropic_messages");
    assert.equal(runtime.model.authMode, "bearer");
    assert.equal(runtime.model.apiKey, undefined);
    const credentials = JSON.parse(
      await readFile(join(directory, "credentials.json"), "utf8"),
    );
    assert.equal(
      credentials.modelApiKey,
      configured.models[runtime.role]!.apiKey,
    );
  }
  const invalid = settings(19310);
  Object.assign(invalid.models.research!, { authMode: "bearer" });
  await assert.rejects(
    prepareProfiles(pool, (await user()).id, invalid, root),
    /authMode/,
  );
});

test("group preparation separates context directory and Compose identity from data owner", async () => {
  const { ExecutionContextStore } =
    await import("../../packages/server/src/execution-context.js");
  const owner = await user();
  const contexts = new ExecutionContextStore(pool);
  const a = await contexts.group(owner.id, "cli_test", "oc_a");
  const b = await contexts.group(owner.id, "cli_test", "oc_b");
  const first = await prepareProfiles(pool, a.id, settings(29100), root);
  const second = await prepareProfiles(pool, b.id, settings(29110), root);
  assert.notEqual(first.directory, second.directory);
  const manifest = JSON.parse(
    await readFile(join(first.directory, "preparation.json"), "utf8"),
  );
  assert.equal(manifest.ownerId, owner.id);
  assert.equal(manifest.contextId, a.id);
  const compose = JSON.parse(await readFile(first.composeFile, "utf8"));
  assert.equal(compose.name, `alpha-${a.id}`);
  for (const profile of manifest.profiles) {
    const identity = JSON.parse(
      await readFile(
        join(
          first.directory,
          "profiles",
          profile.id,
          "home",
          ".alpha-profile.json",
        ),
        "utf8",
      ),
    );
    assert.equal(identity.ownerId, owner.id);
    assert.equal(
      (
        await pool.query(
          "SELECT context_id FROM profile_instances WHERE id=$1",
          [profile.id],
        )
      ).rows[0].context_id,
      a.id,
    );
  }
});
