import { createHash } from "node:crypto";
import { readFile, lstat } from "node:fs/promises";
import { resolve, dirname } from "node:path";
import { z } from "zod";
import { readRunEvents, type EventBatch } from "./events.js";

export const runSchema = z.object({
  run_id: z.string().min(1).max(255),
  session_id: z.string(),
  status: z.enum([
    "started",
    "queued",
    "running",
    "stopping",
    "unknown",
    "waiting_for_approval",
    "completed",
    "failed",
    "cancelled",
    "interrupted",
  ]),
  output: z.string().max(500_000).nullish(),
  pending_controls: z.array(z.unknown()).optional(),
  admission_id: z.string().min(1).max(255).optional(),
  execution_generation: z.number().int().nonnegative().optional(),
});
export type Run = z.infer<typeof runSchema>;
export interface RunRequest {
  input: string;
  session_id: string;
  instructions: string;
}
export interface Runtime {
  identity: string;
  retentionMs: number;
  create(
    request: RunRequest,
    key: string,
    capability?: string,
  ): Promise<string>;
  get(runId: string, sessionId: string): Promise<Run>;
  stop(runId: string): Promise<void>;
  events?(runId: string, after: number): Promise<EventBatch>;
  healthy?(): Promise<boolean>;
  resolveUnknown?(
    runId: string,
    admissionId: string,
    generation: number,
  ): Promise<void>;
}
export class UpstreamError extends Error {
  constructor(public code: string) {
    super(code);
  }
}

export class HermesClient implements Runtime {
  readonly identity: string;
  constructor(
    readonly origin: string,
    private token: string,
    imageDigest: string,
    readonly retentionMs: number,
    private transport: typeof fetch = fetch,
    capabilityFingerprint?: string,
  ) {
    // The ledger scope includes credentials. Rotation cannot silently replay an
    // old key under a new scope, even when the endpoint has stayed the same.
    this.identity = createHash("sha256")
      .update(
        JSON.stringify([
          origin,
          token,
          imageDigest,
          capabilityFingerprint ?? null,
        ]),
      )
      .digest("hex");
  }
  private async request(
    path: string,
    method: string,
    body?: unknown,
    key?: string,
    capability?: string,
  ): Promise<unknown> {
    const response = await this.transport(`${this.origin}${path}`, {
      method,
      redirect: "error",
      signal: AbortSignal.timeout(5000),
      headers: {
        Authorization: `Bearer ${this.token}`,
        "Content-Type": "application/json",
        ...(key ? { "Idempotency-Key": key } : {}),
        ...(capability ? { "X-Alpha-Tool-Capability": capability } : {}),
      },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    });
    if (!response.ok) {
      await response.body?.cancel();
      // Upstream bodies can contain credentials, tool inputs or internal paths.
      throw new UpstreamError(
        response.status === 404
          ? "upstream_not_found"
          : `upstream_http_${response.status}`,
      );
    }
    const reader = response.body?.getReader();
    if (!reader) throw new UpstreamError("upstream_invalid_response");
    const chunks: Uint8Array[] = [];
    let bytes = 0;
    try {
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        bytes += value.length;
        if (bytes > 1_048_576)
          throw new UpstreamError("upstream_response_too_large");
        chunks.push(value);
      }
      return JSON.parse(Buffer.concat(chunks).toString("utf8"));
    } finally {
      await reader.cancel();
      reader.releaseLock();
    }
  }
  async create(request: RunRequest, key: string, capability?: string) {
    return z
      .object({ run_id: z.string().min(1).max(255) })
      .parse(await this.request("/v1/runs", "POST", request, key, capability))
      .run_id;
  }
  async get(runId: string, sessionId: string) {
    const run = runSchema.parse(
      await this.request(`/v1/runs/${encodeURIComponent(runId)}`, "GET"),
    );
    if (run.run_id !== runId || run.session_id !== sessionId)
      throw new UpstreamError("upstream_identity_mismatch");
    return run;
  }
  async stop(runId: string) {
    await this.request(
      `/v1/runs/${encodeURIComponent(runId)}/stop`,
      "POST",
      {},
    );
  }
  async resolveUnknown(runId: string, admissionId: string, generation: number) {
    await this.request(
      `/v1/runs/${encodeURIComponent(runId)}/resolve-unknown`,
      "POST",
      {
        admission_id: z.string().min(1).max(255).parse(admissionId),
        execution_generation: z.number().int().nonnegative().parse(generation),
      },
    );
  }
  events(runId: string, after: number) {
    return readRunEvents(this.transport, this.origin, this.token, runId, after);
  }
  async healthy() {
    try {
      await this.request("/health", "GET");
      return true;
    } catch {
      return false;
    }
  }
}

const binding = z
  .object({
    profileId: z.uuid(),
    origin: z.url(),
    tokenEnv: z.string().regex(/^[A-Z][A-Z0-9_]*$/),
    imageDigest: z.string().regex(/^sha256:[a-f0-9]{64}$/),
    evidenceFile: z.string().min(1),
  })
  .strict();
const evidence = z
  .object({
    imageDigest: z.string(),
    origin: z.string(),
    profileId: z.uuid(),
    // Filled only by a real deployment contract check; no bundled passing report.
    checkedAt: z.iso.datetime(),
    retentionMs: z.number().int().min(60_000).max(86_400_000),
    checks: z
      .object({
        profileIsolation: z.literal(true),
        durableIdempotency: z.literal(true),
        restartReplay: z.literal(true),
        sessionIdentity: z.literal(true),
        stopWaitsForExit: z.literal(true),
        terminalOutput: z.literal(true),
        toolIdentityInjection: z.literal(true),
      })
      .strict(),
  })
  .strict();
export type RuntimeResolver = (
  profileId: string,
  identity?: string | null,
) => Runtime | undefined;
export async function loadRegistry(
  path?: string,
  environment: NodeJS.ProcessEnv = process.env,
): Promise<RuntimeResolver> {
  if (!path) return () => undefined;
  const capabilityKey = environment.TOOL_CAPABILITY_KEY;
  if (!capabilityKey || !/^[a-f0-9]{64}$/.test(capabilityKey))
    throw new Error("tool_capability_key_required");
  const entries = z
    .array(binding)
    .parse(JSON.parse(await readFile(path, "utf8")));
  const runtimes = new Map<string, Runtime>(),
    origins = new Set<string>();
  for (const entry of entries) {
    const url = new URL(entry.origin);
    if (
      !["http:", "https:"].includes(url.protocol) ||
      url.username ||
      url.password ||
      url.pathname !== "/" ||
      url.search ||
      url.hash
    )
      throw new Error("invalid_runtime_origin");
    if (origins.has(url.origin) || runtimes.has(entry.profileId))
      throw new Error("duplicate_profile_runtime");
    const report = evidence.parse(
      JSON.parse(
        await readFile(resolve(dirname(path), entry.evidenceFile), "utf8"),
      ),
    );
    if (
      report.imageDigest !== entry.imageDigest ||
      report.origin !== url.origin ||
      report.profileId !== entry.profileId
    )
      throw new Error("runtime_evidence_mismatch");
    const token = environment[entry.tokenEnv];
    if (!token || /[\r\n]/.test(token))
      throw new Error("runtime_credential_missing");
    runtimes.set(
      entry.profileId,
      new HermesClient(
        url.origin,
        token,
        entry.imageDigest,
        report.retentionMs,
        fetch,
        createHash("sha256").update(capabilityKey).digest("hex"),
      ),
    );
    origins.add(url.origin);
  }
  return (id) => runtimes.get(id);
}

/** Publish a complete validated snapshot; keep old identities available for active executions. */
export class ReloadingRegistry {
  private current: RuntimeResolver = () => undefined;
  private history = new Map<string, Runtime>();
  readonly resolve: RuntimeResolver = (profileId, identity) => {
    const current = this.current(profileId);
    if (!identity || current?.identity === identity) return current;
    return this.history.get(`${profileId}:${identity}`);
  };
  constructor(
    readonly path?: string,
    readonly credentialsFile?: string,
  ) {}
  async reload() {
    const environment = { ...process.env };
    if (this.credentialsFile) {
      const stat = await lstat(this.credentialsFile);
      if (
        !stat.isFile() ||
        stat.isSymbolicLink() ||
        stat.mode & 0o077 ||
        stat.size > 1048576
      )
        throw new Error("private_registry_credentials_required");
      const credentials = z
        .record(
          z.string().regex(/^[A-Z][A-Z0-9_]*$/),
          z
            .string()
            .min(1)
            .regex(/^[^\r\n]+$/),
        )
        .parse(JSON.parse(await readFile(this.credentialsFile, "utf8")));
      // A registry update cannot rotate the service-wide capability encryption key.
      if ("TOOL_CAPABILITY_KEY" in credentials)
        throw new Error("reserved_registry_credential");
      Object.assign(environment, credentials);
    }
    const before = this.path ? await readFile(this.path, "utf8") : "[]";
    const next = await loadRegistry(this.path, environment);
    if (this.path && before !== (await readFile(this.path, "utf8")))
      throw new Error("registry_changed_during_reload");
    const entries: { profileId: string }[] = this.path
      ? JSON.parse(await readFile(this.path, "utf8"))
      : [];
    // Read back consistency: a concurrent publication must not produce a mixed snapshot.
    for (const entry of entries)
      if (!next(entry.profileId))
        throw new Error("registry_changed_during_reload");
    for (const entry of entries) {
      const runtime = next(entry.profileId)!;
      this.history.set(`${entry.profileId}:${runtime.identity}`, runtime);
    }
    this.current = next;
  }
}
