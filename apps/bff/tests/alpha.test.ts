import { createServer, type Server, type ServerResponse } from "node:http";
import { gzipSync } from "node:zlib";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { AlphaClient } from "../src/alpha.js";
import { readConfig } from "../src/config.js";

const secret = "test-only-alpha-transport-service-token-12345";
describe("Alpha transport response boundaries", () => {
  let server: Server;
  let client: AlphaClient;
  let route: (response: ServerResponse) => void;
  let calls = 0;
  beforeAll(async () => {
    server = createServer((req, res) => {
      calls++;
      expect(req.headers.authorization).toBe("Bearer " + secret);
      route(res);
    });
    await new Promise<void>(resolve => server.listen(0, "127.0.0.1", resolve));
    const address = server.address();
    if (!address || typeof address === "string") throw new Error("Missing test address");
    client = new AlphaClient(readConfig({YTA_ENV: "test", YTA_DATABASE_URL: "postgresql://localhost/yta_test", YTA_ALPHA_TOKEN: secret, YTA_ALPHA_URL: "http://127.0.0.1:" + address.port}));
  });
  afterAll(async () => {
    server.closeAllConnections();
    await new Promise<void>(resolve => server.close(() => resolve()));
  });

  it("accepts the exact byte limit and preserves UTF-8 split across chunks", async () => {
    route = res => {
      const bytes = Buffer.from(JSON.stringify("中" + "a".repeat(3_999_995)));
      expect(bytes.byteLength).toBe(4_000_000);
      res.write(bytes.subarray(0, 2));
      setTimeout(() => res.end(bytes.subarray(2)), 5);
    };
    const result = await client.request("/boundary");
    expect((result as string).startsWith("中")).toBe(true);
    expect((result as string).length).toBe(3_999_996);
  });

  it("rejects a chunked oversized body before the upstream finishes", async () => {
    let closed!: () => void;
    const disconnected = new Promise<void>(resolve => { closed = resolve; });
    route = res => {
      res.on("close", closed);
      res.writeHead(200, {"Content-Type": "application/json"});
      res.write('"' + "中".repeat(1_400_000)); // character count is below limit; bytes exceed it
      // Deliberately never end: the client must cancel immediately on size overflow.
    };
    const before = calls;
    await expect(client.request("/oversized")).rejects.toMatchObject({code: "UPSTREAM_INVALID", status: 502});
    await disconnected;
    expect(calls).toBe(before + 1);
  });

  it("bounds decompressed bytes rather than trusting Content-Length", async () => {
    route = res => {
      const compressed = gzipSync(JSON.stringify("x".repeat(4_000_000)));
      res.writeHead(200, {"Content-Encoding": "gzip", "Content-Length": compressed.length});
      res.end(compressed);
    };
    await expect(client.request("/compressed")).rejects.toMatchObject({code: "UPSTREAM_INVALID", status: 502});
  });

  it("honors cancellation after response headers without retrying", async () => {
    const controller = new AbortController();
    const reason = new Error("caller canceled");
    route = res => { res.writeHead(200); res.write('{"pending":'); setTimeout(() => controller.abort(reason), 10); };
    const before = calls;
    await expect(client.request("/stalled", undefined, {}, "original-key", controller.signal)).rejects.toBe(reason);
    expect(calls).toBe(before + 1);
  });

  it("enforces the default deadline while the response body is stalled", async () => {
    route = res => { res.writeHead(200); res.write('{"pending":'); };
    const before = calls;
    const started = Date.now();
    await expect(client.request("/deadline")).rejects.toMatchObject({code: "ALPHA_UNAVAILABLE", status: 503, retryable: true});
    expect(Date.now() - started).toBeGreaterThanOrEqual(14_000);
    expect(Date.now() - started).toBeLessThan(20_000);
    expect(calls).toBe(before + 1);
  }, 22_000);

  it("normalizes a truncated body without leaking diagnostics", async () => {
    route = res => { res.writeHead(200, {"Content-Length": 1000}); res.write(secret); setTimeout(() => res.destroy(), 5); };
    await expect(client.request("/truncated")).rejects.toMatchObject({code: "ALPHA_UNAVAILABLE", status: 503, retryable: true});
  });

  it("rejects malformed JSON and redirects without forwarding credentials", async () => {
    route = res => res.end(secret);
    await expect(client.request("/malformed")).rejects.toMatchObject({code: "UPSTREAM_INVALID", status: 502});
    const before = calls;
    route = res => { res.writeHead(302, {Location: "/credential-target"}); res.end(); };
    await expect(client.request("/redirect")).rejects.toMatchObject({code: "ALPHA_UNAVAILABLE", status: 503});
    expect(calls).toBe(before + 1);
  });
});
