import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { DomainError } from "./tasks.js";

function quantProjectRoot() {
  let directory = resolve(process.env.QUANT_PROJECT_ROOT ?? process.cwd());
  for (let depth = 0; depth < 6; depth++) {
    if (existsSync(join(directory, "services/quant/alpha_quant/bridge.py")))
      return directory;
    const parent = dirname(directory);
    if (parent === directory || process.env.QUANT_PROJECT_ROOT) break;
    directory = parent;
  }
  throw new DomainError("quant_unavailable", 503);
}

// This bridge is private server infrastructure. Public tools never accept its raw input.
export class QuantRunner {
  async run(
    input: Record<string, unknown>,
    signal?: AbortSignal,
  ): Promise<any> {
    if (signal?.aborted) throw new DomainError("operation_cancelled");
    const bytes = JSON.stringify(input);
    if (Buffer.byteLength(bytes) > 4 * 1024 * 1024)
      throw new DomainError("quant_input_too_large", 400);
    const root = quantProjectRoot();
    const executable =
      process.env.QUANT_PYTHON ?? join(root, ".venv/bin/python");
    return new Promise((resolve, reject) => {
      const child = spawn(executable, ["-m", "alpha_quant.bridge"], {
        cwd: root,
        stdio: ["pipe", "pipe", "pipe"],
        // Do not pass provider/API/database credentials into the calculator.
        env: {
          PYTHONPATH: join(root, "services/quant"),
          PYTHONIOENCODING: "utf-8",
          LANG: "C.UTF-8",
          NODE_ENV: process.env.NODE_ENV ?? "production",
        },
      });
      const chunks: Buffer[] = [];
      let length = 0;
      let failure: Error | undefined;
      let killTimer: NodeJS.Timeout | undefined;
      const stop = (error: Error) => {
        failure ??= error;
        child.kill("SIGTERM");
        killTimer ??= setTimeout(() => child.kill("SIGKILL"), 500);
      };
      const abort = () => stop(new DomainError("operation_cancelled"));
      const timeout = setTimeout(
        () => stop(new DomainError("quant_timeout", 503)),
        15_000,
      );
      signal?.addEventListener("abort", abort, { once: true });
      if (signal?.aborted) abort();
      child.stdout.on("data", (chunk: Buffer) => {
        length += chunk.length;
        if (length > 4 * 1024 * 1024)
          stop(new DomainError("quant_output_too_large", 503));
        else chunks.push(chunk);
      });
      child.stderr.resume(); // Never include Python tracebacks or paths in tool responses.
      child.stdin.on("error", () => {
        failure ??= new DomainError("quant_input_failed", 503);
      });
      child.on("error", () => {
        failure ??= new DomainError("quant_unavailable", 503);
      });
      child.on("close", (code) => {
        clearTimeout(timeout);
        if (killTimer) clearTimeout(killTimer);
        signal?.removeEventListener("abort", abort);
        if (failure) return reject(failure);
        if (code !== 0) return reject(new DomainError("quant_failed", 503));
        try {
          const envelope = JSON.parse(Buffer.concat(chunks).toString("utf8"));
          if (envelope.ok !== true)
            throw new DomainError(
              typeof envelope.error === "string"
                ? envelope.error
                : "quant_failed",
              422,
            );
          resolve(envelope.result);
        } catch (error) {
          reject(
            error instanceof DomainError
              ? error
              : new DomainError("quant_invalid_output", 503),
          );
        }
      });
      child.stdin.end(bytes);
    });
  }
}
