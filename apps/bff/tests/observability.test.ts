import { NodeTracerProvider } from "@opentelemetry/sdk-trace-node";
import { InMemorySpanExporter, SimpleSpanProcessor } from "@opentelemetry/sdk-trace-base";
import { context, trace, SpanStatusCode } from "@opentelemetry/api";
import { randomUUID } from "node:crypto";
import { createServer, type Server } from "node:http";
import { setTimeout as delay } from "node:timers/promises";
import express from "express";
import { afterAll, beforeAll, expect, it } from "vitest";
import { AlphaClient } from "../src/alpha.js";
import { readConfig } from "../src/config.js";
import { observeRequests, safeRoute, safeUpstreamId, type Observation } from "../src/observability.js";

const exporter = new InMemorySpanExporter();
const provider = new NodeTracerProvider({spanProcessors: [new SimpleSpanProcessor(exporter)]});
const incoming: Record<string, string | string[] | undefined>[] = [];
const entries: Observation[] = [];
let server: Server, upstream: Server, origin: string;
const privateText = "private-cookie-password-prompt-token";
const upstreamIds: string[] = [];
async function listen(server: Server) {
  await new Promise<void>(resolve => server.listen(0, "127.0.0.1", resolve));
  const address = server.address();
  if (!address || typeof address === "string") throw new Error("Missing address");
  return "http://127.0.0.1:" + address.port;
}
beforeAll(async () => {
  provider.register();
  upstream = createServer((req, res) => {
    incoming.push(req.headers);
    const id = "req_" + randomUUID(); upstreamIds.push(id);
    setTimeout(() => {res.setHeader("X-Request-ID", id); res.end('{}');}, 10);
  });
  const alphaOrigin = await listen(upstream);
  const client = new AlphaClient(readConfig({YTA_DATABASE_URL: "postgresql://localhost/yta_test",
    YTA_ALPHA_TOKEN: privateText + "-long-service-token", YTA_ALPHA_URL: alphaOrigin}));
  const app = express();
  app.use(observeRequests(entry => entries.push(entry)));
  app.use(express.json());
  app.post("/api/yta/v1/predictions/:id", async (req, res) => {
    await client.request("/internal/v1/predictions/" + req.params.id + "?token=" + privateText,
      randomUUID(), req.body, privateText);
    res.json({id: res.locals.requestId});
  });
  app.get("/api/yta/v1/runs/:id/events", (req, res) => {
    res.setHeader("Content-Type", "text/event-stream"); res.write(": connected\n\n");
  });
  app.get("/api/yta/v1/me", (req, res) => res.status(401).json({error: "unauthorized"}));
  server = createServer(app); origin = await listen(server);
});
afterAll(async () => {
  await provider.shutdown(); trace.disable(); context.disable();
  for (const item of [server, upstream]) {
    item.closeAllConnections(); await new Promise<void>(resolve => item.close(() => resolve()));
  }
});

it("correlates concurrent requests with Alpha IDs without logging private inputs", async () => {
  entries.length = 0; exporter.reset(); incoming.length = 0;
  const responses = await Promise.all(Array.from({length: 4}, async () => {
    const response = await fetch(origin + "/api/yta/v1/predictions/" + randomUUID() + "?cursor=" + privateText,
      {method: "POST", headers: {"Content-Type": "application/json", Cookie: privateText,
        Authorization: privateText, "X-Request-ID": privateText,
        traceparent: "00-" + "a".repeat(32) + "-" + "b".repeat(16) + "-01", baggage: privateText, tracestate: privateText}, body: JSON.stringify({prompt: privateText})});
    expect((await response.json()).id).toBe(response.headers.get("x-request-id"));
    return response.headers.get("x-request-id");
  }));
  await delay(20);
  expect(new Set(responses).size).toBe(4);
  for (const requestId of responses) {
    const pair = entries.filter(entry => entry.request_id === requestId);
    expect(pair).toHaveLength(2);
    expect(pair.find(entry => entry.event === "bff.request")).toMatchObject({
      route: "/api/yta/v1/predictions/:id", status_code: 200, completed: true});
    const alpha = pair.find(entry => entry.event === "bff.alpha_request")!;
    expect(alpha).toMatchObject({route: "/internal/v1/predictions/:id", outcome: "success"});
    expect(upstreamIds).toContain(alpha.upstream_request_id);
    expect(pair.every(entry => entry.duration_ms >= 0)).toBe(true);
  }
  const spans = exporter.getFinishedSpans();
  expect(spans).toHaveLength(8);
  for (const requestId of responses) {
    const root = spans.find(span => span.attributes["yta.request_id"] === requestId)!;
    const child = spans.find(span => span.parentSpanContext?.spanId === root.spanContext().spanId)!;
    expect(root.parentSpanContext).toBeUndefined();
    expect(root.spanContext().traceId).not.toBe("a".repeat(32));
    expect(child.spanContext().traceId).toBe(root.spanContext().traceId);
    expect(incoming.some(headers => headers.traceparent === `00-${child.spanContext().traceId}-${child.spanContext().spanId}-01`)).toBe(true);
    expect(entries.filter(entry => entry.request_id === requestId).every(entry => entry.trace_id === root.spanContext().traceId)).toBe(true);
  }
  expect(incoming.every(headers => !headers.baggage && !headers.tracestate)).toBe(true);
  expect(JSON.stringify(spans.map(span => ({name: span.name, attributes: span.attributes, events: span.events})))).not.toContain(privateText);
  expect(JSON.stringify(entries)).not.toContain(privateText);
});

it("records authentication errors and unmatched paths without echoing path text", async () => {
  entries.length = 0;
  await (await fetch(origin + "/api/yta/v1/me")).text();
  await (await fetch(origin + "/" + privateText)).text();
  await delay(20);
  expect(entries).toHaveLength(2);
  expect(entries[0]).toMatchObject({route: "/api/yta/v1/me", status_code: 401, completed: true});
  expect(entries[1]).toMatchObject({route: "UNMATCHED", status_code: 404});
  expect(JSON.stringify(entries)).not.toContain(privateText);
});

it("records an aborted SSE once without reporting a completed response", async () => {
  entries.length = 0;
  const controller = new AbortController();
  const response = await fetch(origin + "/api/yta/v1/runs/" + randomUUID() + "/events",
    {signal: controller.signal});
  const reading = response.text().catch(() => undefined);
  controller.abort(); await reading;
  await delay(30);
  expect(entries).toHaveLength(1);
  expect(entries[0]).toMatchObject({route: "/api/yta/v1/runs/:id/events", completed: false});
  expect(exporter.getFinishedSpans().find(span => span.attributes["yta.request_id"] === entries[0]!.request_id)?.status.code).toBe(SpanStatusCode.ERROR);
});

it("rejects arbitrary upstream IDs and unsafe endpoint labels", () => {
  for (const id of [privateText, "req_" + privateText, "req_" + "a".repeat(1000)]) expect(safeUpstreamId(id)).toBeNull();
  expect(safeUpstreamId("req_" + "a".repeat(32))).toBe("req_" + "a".repeat(32));
  expect(safeRoute("/internal/v1/predictions/" + privateText)).toBe("UNMATCHED");
  expect(safeRoute("/internal/v1/stocks/NVDA/indicators?secret=" + privateText)).toBe("/internal/v1/stocks/NVDA/indicators");
});

it("logging sink failure cannot change a successful response", async () => {
  const app = express(); app.use(observeRequests(() => {throw new Error(privateText);}));
  app.get("/api/yta/v1/health", (req, res) => res.json({status: "ok"}));
  const isolated = createServer(app); const address = await listen(isolated);
  try { expect(await (await fetch(address + "/api/yta/v1/health")).json()).toEqual({status: "ok"}); }
  finally { isolated.closeAllConnections(); await new Promise<void>(resolve => isolated.close(() => resolve())); }
});
