import { gzipSync } from "node:zlib";
import { request as httpRequest } from "node:http";
import { afterAll, beforeAll, expect, it, vi } from "vitest";
import { errorSchema } from "@yta/contracts";
import { createBff } from "../src/app.js";
import { readConfig } from "../src/config.js";
import type { Observation } from "../src/observability.js";

let server: Awaited<ReturnType<typeof createBff>>, origin: string;
const entries: Observation[] = [];
const privateText = "synthetic-private-password-do-not-echo";
beforeAll(async () => {
  server = await createBff({startRunner: false, log: entry => entries.push(entry), config: readConfig({
    YTA_ENV: "test", YTA_DATABASE_URL: "postgresql://localhost:1/body_boundary_test",
    YTA_ALPHA_TOKEN: "body-test-only-service-token-not-live",
  })});
  vi.spyOn(server.pool, "query").mockRejectedValue(new Error("Body rejection must precede DB access"));
  await server.app.listen(0, "127.0.0.1");
  origin = await server.app.getUrl();
});
afterAll(async () => { server.app.getHttpServer().closeAllConnections(); await server.close(); });

async function post(body: string | Buffer, extra: Record<string, string> = {}) {
  return fetch(origin + "/api/yta/v1/auth/login", {method: "POST", body,
    headers: {"Content-Type": "application/json", Origin: "http://localhost:3000", ...extra}});
}
async function rejected(response: Response, status: number, code: string) {
  expect(response.status).toBe(status);
  expect(response.headers.get("content-type")).toContain("application/json");
  expect(response.headers.get("set-cookie")).toBeNull();
  const text = await response.text();
  expect(text).not.toContain(privateText);
  const error = errorSchema.parse(JSON.parse(text)).error;
  expect(error).toMatchObject({code, retryable: false, request_id: response.headers.get("x-request-id")});
  expect(server.pool.query).not.toHaveBeenCalled();
  expect(JSON.stringify(entries)).not.toContain(privateText);
}

it("rejects malformed and primitive JSON without reflecting submitted credentials", async () => {
  for (const body of [`{"password":"${privateText}",`, JSON.stringify(privateText)]) {
    await rejected(await post(body), 400, "MALFORMED_JSON");
  }
});

it("enforces the 32 KiB decoded byte limit, accepting exactly the parser boundary", async () => {
  const exact = JSON.stringify({password: "x".repeat(32768 - 15)});
  expect(Buffer.byteLength(exact)).toBe(32768);
  // Parsing succeeds; the login schema rejects the missing email before DB access.
  await rejected(await post(exact), 422, "INVALID_REQUEST");
  await rejected(await post(exact + " "), 413, "REQUEST_TOO_LARGE");
  await rejected(await post(JSON.stringify({password: "界".repeat(12000)})), 413, "REQUEST_TOO_LARGE");
});

it("bounds compressed bodies after decompression", async () => {
  await rejected(await post(gzipSync("{}"), {"Content-Encoding": "gzip"}), 422, "INVALID_REQUEST");
  const compressed = gzipSync(JSON.stringify({password: privateText.repeat(2000)}));
  expect(compressed.length).toBeLessThan(32768);
  await rejected(await post(compressed, {"Content-Encoding": "gzip"}), 413, "REQUEST_TOO_LARGE");
});

it("rejects oversized chunked requests without a Content-Length header", async () => {
  const result = await new Promise<{status: number; body: string; requestId: string | string[] | undefined}>((resolve, reject) => {
    const request = httpRequest(origin + "/api/yta/v1/auth/login", {method: "POST",
      headers: {"Content-Type": "application/json", Origin: "http://localhost:3000"}}, response => {
      const chunks: Buffer[] = [];
      response.on("data", chunk => chunks.push(chunk));
      response.on("error", reject);
      response.on("end", () => resolve({status: response.statusCode!, body: Buffer.concat(chunks).toString(), requestId: response.headers["x-request-id"]}));
    });
    request.on("error", reject);
    request.setTimeout(3000, () => request.destroy(new Error("Test request timed out")));
    request.write('{"password":"');
    for (let i = 0; i < 5; i++) request.write("x".repeat(8192));
    request.end('"}');
  });
  expect(result.status).toBe(413);
  expect(errorSchema.parse(JSON.parse(result.body)).error).toMatchObject({code: "REQUEST_TOO_LARGE", retryable: false, request_id: result.requestId});
  expect(server.pool.query).not.toHaveBeenCalled();
});

it("returns explicit unsupported encoding and charset errors", async () => {
  await rejected(await post("{}", {"Content-Encoding": "unsupported-" + privateText}), 415, "UNSUPPORTED_ENCODING");
  await rejected(await post("{}", {"Content-Type": "application/json; charset=iso-8859-1"}), 415, "UNSUPPORTED_CHARSET");
});

it("rejects corrupt compressed input and remains available for subsequent requests", async () => {
  await rejected(await post(privateText, {"Content-Encoding": "gzip"}), 400, "INVALID_BODY");
  const response = await fetch(origin + "/api/yta/v1/health");
  expect(response.status).toBe(200);
  expect(await response.json()).toMatchObject({status: "ok"});
});
