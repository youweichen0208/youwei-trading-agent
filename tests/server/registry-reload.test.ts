import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, writeFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { randomUUID } from "node:crypto";
import { ReloadingRegistry } from "../../packages/hermes-adapter/src/client.js";
test("registry atomically rotates credentials and retains old execution identity", async () => {
  const root = await mkdtemp(join(tmpdir(), "alpha-registry-"));
  const previous = process.env.TOOL_CAPABILITY_KEY;
  process.env.TOOL_CAPABILITY_KEY = "a".repeat(64);
  try {
    const entry = {
      profileId: randomUUID(),
      origin: "http://127.0.0.1:18720",
      tokenEnv: "TEST_PROFILE_KEY",
      imageDigest: `sha256:${"b".repeat(64)}`,
      evidenceFile: "evidence.json",
    };
    await writeFile(
      join(root, "evidence.json"),
      JSON.stringify({
        profileId: entry.profileId,
        origin: entry.origin,
        imageDigest: entry.imageDigest,
        checkedAt: new Date().toISOString(),
        retentionMs: 60000,
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
    );
    await writeFile(join(root, "registry.json"), JSON.stringify([entry]));
    const credentials = join(root, "credentials.json");
    await writeFile(
      credentials,
      JSON.stringify({ TEST_PROFILE_KEY: "first" }),
      { mode: 0o600 },
    );
    const registry = new ReloadingRegistry(
      join(root, "registry.json"),
      credentials,
    );
    await registry.reload();
    const first = registry.resolve(entry.profileId)!;
    await writeFile(
      credentials,
      JSON.stringify({ TEST_PROFILE_KEY: "second" }),
    );
    await registry.reload();
    assert.notEqual(
      registry.resolve(entry.profileId)!.identity,
      first.identity,
    );
    assert.equal(registry.resolve(entry.profileId, first.identity), first);
    const current = registry.resolve(entry.profileId);
    await writeFile(join(root, "registry.json"), "broken");
    await assert.rejects(registry.reload());
    assert.equal(registry.resolve(entry.profileId), current);
  } finally {
    if (previous === undefined) delete process.env.TOOL_CAPABILITY_KEY;
    else process.env.TOOL_CAPABILITY_KEY = previous;
    await rm(root, { recursive: true, force: true });
  }
});
