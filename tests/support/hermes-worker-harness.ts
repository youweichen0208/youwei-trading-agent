// Local-only fault-injection transport for the opt-in real Hermes process test.
import assert from "node:assert/strict";
import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import { createHash } from "node:crypto";
import { createServer } from "node:http";
import { setTimeout as delay } from "node:timers/promises";

export function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}
export async function bounded<T>(
  value: Promise<T>,
  ms: number,
  message: string,
) {
  const abort = new AbortController();
  try {
    return await Promise.race([
      value,
      delay(ms, undefined, { signal: abort.signal, ref: false }).then(() => {
        throw new Error(message);
      }),
    ]);
  } finally {
    abort.abort();
  }
}
type WorkerProcess = {
  child: ChildProcessWithoutNullStreams;
  exit: Promise<{ code: number | null; signal: NodeJS.Signals | null }>;
  output: () => string;
};
type Binding = {
  profileId: string;
  origin: string;
  token: string;
  image?: string;
};
export async function workerHarness(
  bindings: Binding[],
  researchId: string,
  schema: string,
  key: string,
  options: { dropFirstResponse?: boolean } = {},
) {
  const accepted = deferred<string>();
  const creates: {
    profileId: string;
    runId: string;
    key: string;
    capabilityHash: string;
    request: string;
  }[] = [];
  const stops: string[] = [];
  const errors: string[] = [];
  let held = false;
  const servers: ReturnType<typeof createServer>[] = [];
  const workers: WorkerProcess[] = [];
  const targets: Binding[] = [];
  try {
    for (const binding of bindings) {
      assert.equal(new URL(binding.origin).hostname, "127.0.0.1");
      const server = createServer(async (req, res) => {
        const closing = new AbortController();
        res.once("close", () => closing.abort());
        try {
          const parts: Buffer[] = [];
          for await (const part of req) parts.push(part);
          const body = Buffer.concat(parts).toString("utf8");
          const headers = new Headers();
          for (const name of [
            "authorization",
            "content-type",
            "idempotency-key",
            "x-alpha-tool-capability",
            "last-event-id",
          ])
            if (typeof req.headers[name] === "string")
              headers.set(name, req.headers[name]);
          const response = await fetch(binding.origin + req.url, {
            method: req.method,
            headers,
            signal: closing.signal,
            redirect: "error",
            ...(req.method === "POST" ? { body } : {}),
          });
          const isCreate = req.method === "POST" && req.url === "/v1/runs";
          const isStop = req.method === "POST" && req.url?.endsWith("/stop");
          if (isCreate || isStop) {
            const text = await response.text();
            assert.ok(response.ok, `native operation HTTP ${response.status}`);
            if (isCreate) {
              const run = JSON.parse(text);
              creates.push({
                profileId: binding.profileId,
                runId: run.run_id,
                key: headers.get("idempotency-key")!,
                request: body,
                capabilityHash: createHash("sha256")
                  .update(headers.get("x-alpha-tool-capability")!)
                  .digest("hex"),
              });
              if (binding.profileId === researchId && !held) {
                held = true;
                accepted.resolve(run.run_id);
                // Deliberately leave the response open. The parent kills the
                // Worker while it is awaiting this already-accepted create.
                if (options.dropFirstResponse !== false) return;
              }
            } else stops.push(req.url!);
            res.writeHead(response.status, {
              "Content-Type": "application/json",
            });
            res.end(text);
            return;
          }
          res.writeHead(response.status, {
            "Content-Type":
              response.headers.get("content-type") ?? "application/json",
          });
          if (response.body)
            for await (const part of response.body) {
              if (res.destroyed) break;
              res.write(part);
            }
          res.end();
        } catch {
          if (!closing.signal.aborted) {
            errors.push("proxy_operation_failed");
            res.destroy();
          }
        }
      });
      servers.push(server);
      await new Promise<void>((resolve) =>
        server.listen(0, "127.0.0.1", resolve),
      );
      const address = server.address();
      assert.ok(address && typeof address !== "string");
      targets.push({ ...binding, origin: `http://127.0.0.1:${address.port}` });
    }
  } catch (error) {
    await close();
    throw error;
  }
  function start(): WorkerProcess {
    const child = spawn(
      process.execPath,
      ["--import", "tsx", "tests/support/hermes-worker-process.ts"],
      {
        env: {
          PATH: process.env.PATH,
          HOME: process.env.HOME,
          TEST_DATABASE_URL: process.env.TEST_DATABASE_URL,
        },
        stdio: ["pipe", "pipe", "pipe"],
      },
    );
    let output = "";
    for (const stream of [child.stdout, child.stderr])
      stream.on("data", (chunk) => {
        output = (output + chunk).slice(-16000);
      });
    const exit = new Promise<{
      code: number | null;
      signal: NodeJS.Signals | null;
    }>((resolve, reject) => {
      child.once("error", reject);
      child.once("exit", (code, signal) => resolve({ code, signal }));
    });
    child.stdin.end(JSON.stringify({ schema, key, bindings: targets }) + "\n");
    const worker = { child, exit, output: () => output };
    workers.push(worker);
    return worker;
  }
  async function close() {
    for (const worker of workers) {
      if (worker.child.exitCode === null && worker.child.signalCode === null)
        worker.child.kill("SIGTERM");
      try {
        await bounded(worker.exit, 12000, "worker cleanup timeout");
      } catch {
        worker.child.kill("SIGKILL");
        await worker.exit;
      }
    }
    for (const server of servers) {
      server.closeAllConnections();
      await new Promise<void>((resolve) => server.close(() => resolve()));
    }
  }
  return { start, close, accepted: accepted.promise, creates, stops, errors };
}
