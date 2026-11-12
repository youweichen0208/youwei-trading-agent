import { createServer } from "node:http";
import { Readable } from "node:stream";
import { database } from "../../packages/server/src/db.js";
import { DataService } from "../../packages/server/src/data-service.js";
import { createToolHandler } from "../../packages/server/src/tool-gateway.js";
import { configuredMarketProvider } from "../../packages/market/src/alpaca.js";

const pool = database(),
  handler = createToolHandler(
    pool,
    new DataService(pool, configuredMarketProvider()),
  );
const host = process.env.TOOL_GATEWAY_HOST ?? "127.0.0.1",
  port = Number(process.env.TOOL_GATEWAY_PORT ?? "3410");
if (!Number.isInteger(port) || port < 1 || port > 65535)
  throw new Error("invalid_tool_gateway_port");
let active = 0,
  closing = false;
const server = createServer(async (req, res) => {
  if (closing || active >= 32) {
    res.writeHead(503);
    res.end();
    return;
  }
  active++;
  try {
    const method = req.method ?? "GET",
      headers = new Headers();
    for (const [key, value] of Object.entries(req.headers))
      if (value !== undefined)
        headers.set(key, Array.isArray(value) ? value.join(",") : value);
    const init: RequestInit & { duplex?: "half" } = { method, headers };
    if (!["GET", "HEAD"].includes(method)) {
      init.body = Readable.toWeb(req) as ReadableStream<Uint8Array>;
      init.duplex = "half";
    }
    const response = await handler(
      new Request(`http://tool-gateway${req.url ?? "/"}`, init),
    );
    res.writeHead(response.status, Object.fromEntries(response.headers));
    res.end(Buffer.from(await response.arrayBuffer()));
  } catch {
    res.writeHead(503);
    res.end('{"error":"tool_dependency_unavailable"}');
  } finally {
    active--;
  }
});
server.requestTimeout = 10_000;
server.headersTimeout = 5000;
server.keepAliveTimeout = 5000;
await new Promise<void>((resolve) => server.listen(port, host, resolve));
console.info("Private tool gateway started", { host, port });
const close = () => {
  if (closing) return;
  closing = true;
  server.close(() => {
    void pool.end();
  });
  server.closeIdleConnections();
};
process.once("SIGTERM", close);
process.once("SIGINT", close);
